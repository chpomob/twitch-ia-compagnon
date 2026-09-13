"""End-to-end supervision, correlation and sanitisation (R8; AC11, AC27–AC29).

Two harnesses drive the same three real modules — the chat input, the run
engine and the audit stage — through the real loader, coordinator, trigger
engine, admission scheduler, executor and supervision; only the two network
edges (the platform session and the model transport) and time are fakes.

*The example configuration* (``config.yaml.example``) is run through
:func:`core.main.run` exactly as the process would, with the audit output as
the only observation window. The two allowlisted integration tests of the v1
suite are rewritten here against the new pipeline, each named for the
acceptance criterion it now enforces and citing the test it replaces.

*The shared fixture* (``tests/conftest.py``, P19) builds the versioned runtime
context; the loader activates the three manifests on it and the
:class:`~core.lifecycle.PhaseCoordinator` drives every phase. Here the bus
history, the counters and every owner's own record are readable, which is
what the R8 assertions need: a terminal state is recorded by its owner
*before* its trace is published, so a publication the bus refuses leaves
the state readable, the transport invoked exactly once and the loss counted
(R8, AC28), while an owner whose own write raises publishes no trace at all.

The ``module.*`` health facts are owned by :class:`~core.runtime.ModuleHealth`,
the context's own, which the coordinator's phase transitions drive through
its observer — the same wiring ``core/main.py`` makes (P24) — so the harness
reports nothing by hand.

Nothing here sleeps: the clock is injected and every wait is a bounded number
of bare loop turns. A strict global order over a concurrent pipeline is
asserted only for *one run's* correlated traces — filtered by the source
event identifier up to admission and by ``run_id`` from then on — never over
the raw bus sequence.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

import core.main as application
from core.actions import ActionExecutor, AuthorizationPolicy, AuthorizationRule
from core.bus import EventBus
from core.context import ChatContext
from core.contracts import (
    COUNTER_ADMISSION_REJECTIONS,
    COUNTER_AUDIT_RECORD_LOSSES,
    COUNTER_LOST_TRACES,
    COUNTER_STALE_DROP,
    COUNTER_TRIGGER_REJECTIONS,
    REQUIRED_COUNTERS,
    TERMINAL_TRACE_TYPES,
    TRACE_ACTION_COMPLETED,
    TRACE_ACTION_STARTED,
    TRACE_BRAIN_ADMISSION_ACCEPTED,
    TRACE_BRAIN_ADMISSION_REJECTED,
    TRACE_BRAIN_RUN_COMPLETED,
    TRACE_BRAIN_RUN_STARTED,
    TRACE_CHANNEL_CHAT_SENT,
    TRACE_EVENT_TYPES,
    TRACE_INPUT_TRIGGER_ACCEPTED,
    TRACE_INPUT_TRIGGER_REJECTED,
    TRACE_MODULE_READY,
    TRACE_MODULE_STOPPED,
    Destination,
)
from core.lifecycle import PHASE_CLOSE, PhaseCoordinator
from core.loader import ModuleLoader
from core.runtime import (
    MODULE_STATE_READY,
    MODULE_STATE_STOPPED,
    ModuleHealth,
    RuntimeContext,
)
from core.triggers import TriggerRegistry
from conftest import (
    FakeResponse,
    FakeSession,
    HeldSession,
    ManualClock,
    completion,
    events_of,
    runtime_context as build_context,
    settle,
    wait_until,
)
from modules.twitch import (
    EVENTSUB_SUBSCRIPTIONS_URL,
    EVENTSUB_URL,
    HELIX_CHAT_URL,
    TOKEN_VALIDATION_URL,
)


ROOT = Path(__file__).parents[1]
EXAMPLE_CONFIG = ROOT / "config.yaml.example"
MODULES = ROOT / "modules"

PLATFORM = "twitch"
INPUT_EVENT = "channel.chat.message"
COMPATIBILITY_ROUTE = "channel.chat.send"

PIPELINE_TRACES = (
    INPUT_EVENT,
    TRACE_INPUT_TRIGGER_ACCEPTED,
    TRACE_BRAIN_ADMISSION_ACCEPTED,
    TRACE_BRAIN_RUN_STARTED,
    TRACE_ACTION_STARTED,
    TRACE_ACTION_COMPLETED,
    TRACE_CHANNEL_CHAT_SENT,
    TRACE_BRAIN_RUN_COMPLETED,
)
"""One accepted message through the real pipeline, as the audit sees it.

The order AC27 fixes: ``action.started`` with its matching
``action.completed``, then ``channel.chat.sent``, then ``brain.run.completed``.
The provider records the confirmed send the instant the platform confirms but
hands the fact to the loop only through the invocation's completion hook, so
the executor's own completion is published first and the fact after it.
"""


# --------------------------------------------------------------------------- #
# The platform edge: one socket, one HTTP session, no network
# --------------------------------------------------------------------------- #


class FakeWebSocket:
    def __init__(self, welcome: Mapping[str, Any]) -> None:
        self._frames: asyncio.Queue[Any] = asyncio.Queue()
        self._frames.put_nowait(welcome)
        self.close_calls = 0

    async def receive(self) -> Any:
        return await self._frames.get()

    async def close(self) -> None:
        self.close_calls += 1

    def feed(self, frame: Mapping[str, Any]) -> None:
        self._frames.put_nowait(frame)


class FakeTwitchSession:
    """The platform session: token validation, subscription and Helix sends.

    ``sent_facts_seen`` is read the instant a Helix response is handed back
    and its value recorded, so a test can prove that 0 send facts existed
    before the transport confirmed (AC27). ``helix_confirmed`` counts the
    responses actually returned to the module.
    """

    def __init__(
        self,
        *,
        client_id: str,
        bot_user_id: str,
        websocket: FakeWebSocket,
        sends: Iterable[Any],
        sent_facts_seen: Callable[[], int] = lambda: 0,
    ) -> None:
        self._client_id = client_id
        self._bot_user_id = bot_user_id
        self._websocket = websocket
        self._sends = list(sends)
        self._sent_facts_seen = sent_facts_seen
        self.get_calls: list[dict[str, Any]] = []
        self.post_calls: list[dict[str, Any]] = []
        self.ws_calls: list[str] = []
        self.close_calls = 0
        self.helix_confirmed = 0
        self.helix_called = asyncio.Event()
        self.sent_facts_at_confirmation: list[int] = []

    @property
    def helix_calls(self) -> list[dict[str, Any]]:
        return [call for call in self.post_calls if call["url"] == HELIX_CHAT_URL]

    async def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.get_calls.append({"url": url, **kwargs})
        return FakeResponse(
            200, {"client_id": self._client_id, "user_id": self._bot_user_id}
        )

    async def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.post_calls.append({"url": url, **kwargs})
        if url == EVENTSUB_SUBSCRIPTIONS_URL:
            return FakeResponse(202, {"data": [{"id": "subscription-created"}]})
        if url == HELIX_CHAT_URL and self._sends:
            response = self._sends.pop(0)
            if isinstance(response, BaseException):
                raise response
            self.sent_facts_at_confirmation.append(self._sent_facts_seen())
            self.helix_confirmed += 1
            self.helix_called.set()
            return response
        raise AssertionError("unexpected platform POST operation")

    async def ws_connect(self, url: str) -> FakeWebSocket:
        self.ws_calls.append(url)
        return self._websocket

    async def close(self) -> None:
        self.close_calls += 1


def welcome() -> dict[str, Any]:
    return {
        "metadata": {"message_type": "session_welcome"},
        "payload": {"session": {"id": "integration-session"}},
    }


def notification(
    channel_id: str,
    message_id: str,
    text: str,
    *,
    viewer_id: str = "integration-viewer",
) -> dict[str, Any]:
    return {
        "metadata": {
            "message_type": "notification",
            "subscription_type": INPUT_EVENT,
        },
        "payload": {
            "subscription": {"type": INPUT_EVENT},
            "event": {
                "broadcaster_user_id": channel_id,
                "chatter_user_id": viewer_id,
                "chatter_user_name": "Integration Viewer",
                "message_id": message_id,
                "message": {"text": text},
            },
        },
    }


def sent_response(message_id: str = "sent") -> FakeResponse:
    return FakeResponse(200, {"data": [{"message_id": message_id, "is_sent": True}]})


# --------------------------------------------------------------------------- #
# Reading traces: correlation, recursive rendering
# --------------------------------------------------------------------------- #


def traces_of(events: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The supervision traces among *events*, in their original order."""

    return [dict(event) for event in events if event["type"] in TRACE_EVENT_TYPES]


def count_keys(value: Any, key: str) -> int:
    """How many mappings anywhere inside *value* carry *key*."""

    if isinstance(value, Mapping):
        return (key in value) + sum(count_keys(item, key) for item in value.values())
    if isinstance(value, (list, tuple, set, frozenset)):
        return sum(count_keys(item, key) for item in value)
    return 0


def rendered(value: Any) -> list[str]:
    """Every string a structure renders to, recursively, plus its JSON form.

    A secret surviving in a nested mapping, a list element, a key or a
    non-string scalar is found the same way as one at the top level.
    """

    strings: list[str] = []

    def walk(item: Any) -> None:
        if isinstance(item, Mapping):
            for key, nested in item.items():
                strings.append(str(key))
                walk(nested)
        elif isinstance(item, (list, tuple, set, frozenset)):
            for nested in item:
                walk(nested)
        elif isinstance(item, str):
            strings.append(item)
        elif isinstance(item, (bytes, bytearray)):
            strings.append(bytes(item).decode("utf-8", "replace"))
        else:
            strings.append(repr(item))

    walk(value)
    strings.append(json.dumps(value, default=str, sort_keys=True))
    return strings


def occurrences(needle: str, strings: Iterable[str]) -> int:
    return sum(text.count(needle) for text in strings)


def assert_ac27_correlation(
    traces: list[dict[str, Any]],
    *,
    source_event_id: str,
    platform: str,
    channel_id: str,
) -> str:
    """Assert AC27 over one source event's correlated traces; return its run id.

    The traces are read the way AC27 correlates them: by the source event
    identifier up to admission, and by the ``run_id`` the admission trace
    names from then on. Order is asserted between the traces of *this* run
    only, never over the raw sequence of a concurrent pipeline.
    """

    def payload(trace: Mapping[str, Any]) -> Mapping[str, Any]:
        return trace["payload"]

    triggers = [
        trace
        for trace in traces
        if trace["type"] == TRACE_INPUT_TRIGGER_ACCEPTED
        and payload(trace).get("source_event_id") == source_event_id
    ]
    admissions = [
        trace
        for trace in traces
        if trace["type"] == TRACE_BRAIN_ADMISSION_ACCEPTED
        and payload(trace).get("source_event_id") == source_event_id
    ]
    assert len(triggers) == 1, "exactly 1 accepted trigger for the source event"
    assert len(admissions) == 1, "exactly 1 accepted admission for the source event"
    (trigger,), (admission,) = triggers, admissions

    # The trigger decision precedes any run: 0 ``run_id`` fields at any depth,
    # and correlation by source event and channel pair instead.
    assert count_keys(trigger, "run_id") == 0
    assert payload(trigger)["source_event_id"] == payload(admission)["source_event_id"]
    assert (payload(trigger)["platform"], payload(trigger)["channel_id"]) == (
        payload(admission)["platform"],
        payload(admission)["channel_id"],
    ) == (platform, channel_id)

    run_id = payload(admission)["run_id"]
    assert isinstance(run_id, str) and run_id
    run = [trace for trace in traces if payload(trace).get("run_id") == run_id]
    assert run[0] is admission
    assert {payload(trace)["run_id"] for trace in run} == {run_id}
    assert all(count_keys(trace, "run_id") == 1 for trace in run)

    kinds = [trace["type"] for trace in run]
    assert kinds.count(TRACE_BRAIN_ADMISSION_ACCEPTED) == 1
    assert kinds.count(TRACE_BRAIN_RUN_STARTED) == 1
    assert kinds.count(TRACE_ACTION_STARTED) >= 1
    assert kinds.count(TRACE_CHANNEL_CHAT_SENT) == 1
    assert kinds.count(TRACE_BRAIN_RUN_COMPLETED) == 1

    def positions(kind: str) -> list[int]:
        return [index for index, trace in enumerate(run) if trace["type"] == kind]

    (admitted,) = positions(TRACE_BRAIN_ADMISSION_ACCEPTED)
    (started,) = positions(TRACE_BRAIN_RUN_STARTED)
    (sent,) = positions(TRACE_CHANNEL_CHAT_SENT)
    (completed,) = positions(TRACE_BRAIN_RUN_COMPLETED)
    assert traces.index(trigger) < traces.index(admission)
    assert admitted < started < positions(TRACE_ACTION_STARTED)[0]
    assert completed == len(run) - 1, "the run's completion is its last trace"

    # Each action start has exactly 1 matching completion, after it and
    # before the run completes; the send fact names one of those calls and
    # follows that call's completion — AC27's order is start, completion,
    # sent, run completed — never sits between the start and the completion.
    started_calls = {
        payload(run[index])["call_id"]: index for index in positions(TRACE_ACTION_STARTED)
    }
    completed_calls = {
        payload(run[index])["call_id"]: index
        for index in positions(TRACE_ACTION_COMPLETED)
    }
    assert set(started_calls) == set(completed_calls)
    assert len(started_calls) == kinds.count(TRACE_ACTION_STARTED)
    assert len(completed_calls) == kinds.count(TRACE_ACTION_COMPLETED)
    for call_id, begun in started_calls.items():
        assert begun < completed_calls[call_id] < completed
    sent_call = payload(run[sent])["call_id"]
    assert sent_call in started_calls
    assert started_calls[sent_call] < completed_calls[sent_call] < sent < completed, (
        "the send fact follows its call's action.completed and precedes "
        "brain.run.completed"
    )
    return run_id


# --------------------------------------------------------------------------- #
# Harness 1: the example configuration through core.main.run
# --------------------------------------------------------------------------- #


def _environment() -> dict[str, str]:
    unique = uuid4().hex
    return {
        "TWITCH_CLIENT_ID": "-".join(("client", unique)),
        "TWITCH_CLIENT_SECRET": "-".join(("secret", unique)),
        "TWITCH_ACCESS_TOKEN": "-".join(("token", unique)),
        "TWITCH_BROADCASTER_ID": "-".join(("broadcaster", unique)),
        "TWITCH_BOT_USER_ID": "-".join(("bot", unique)),
        "OPENAI_ENDPOINT": "/".join(
            ("https:", "", unique + ".invalid", "chat", "completions")
        ),
        "OPENAI_MODEL": "-".join(("model", unique)),
        "OPENAI_API_KEY": "-".join(("key", unique)),
    }


def _credentials(environ: Mapping[str, str]) -> list[str]:
    return [
        environ["TWITCH_CLIENT_SECRET"],
        environ["TWITCH_ACCESS_TOKEN"],
        environ["OPENAI_API_KEY"],
        environ["OPENAI_ENDPOINT"],
    ]


def _inject_boundaries(
    monkeypatch: pytest.MonkeyPatch,
    *,
    twitch_session: FakeTwitchSession,
    brain_session: FakeSession,
    audit_writer: Callable[[str], Any],
    diagnostics: list[str],
    resolved_configs: list[dict[str, Any]],
) -> None:
    real_load_config = application.load_config

    def load_with_boundaries(
        path: str | Path, *, environ: Mapping[str, str] | None = None
    ) -> dict[str, Any]:
        config = real_load_config(path, environ=environ)
        resolved_configs.append(config)
        config["modules"]["twitch"].update(
            {
                "_session_factory": lambda: twitch_session,
                "diagnostic_reporter": diagnostics.append,
            }
        )
        config["modules"]["brain"].update(
            {
                "_session_factory": lambda: brain_session,
                "diagnostic_reporter": diagnostics.append,
            }
        )
        config["modules"]["audit"]["_writer"] = audit_writer
        return config

    monkeypatch.setattr(application, "load_config", load_with_boundaries)


async def _start_application(
    environ: Mapping[str, str],
    stop: asyncio.Event,
    ready: asyncio.Event,
    diagnostics: list[str],
) -> asyncio.Task[int]:
    def report_ready(message: str) -> None:
        assert message == "ready"
        ready.set()

    task = asyncio.create_task(
        application.run(
            EXAMPLE_CONFIG,
            stop,
            environ=environ,
            ready_reporter=report_ready,
            diagnostic_reporter=diagnostics.append,
        )
    )
    await asyncio.wait_for(ready.wait(), timeout=1)
    return task


def _audit_until(
    audit_lines: list[str], event_type: str, count: int
) -> tuple[Callable[[str], Any], asyncio.Event]:
    """An audit writer that signals once *count* records of *event_type* landed."""

    complete = asyncio.Event()

    async def write_audit(line: str) -> None:
        audit_lines.append(line)
        if sum(json.loads(entry)["type"] == event_type for entry in audit_lines) >= count:
            complete.set()

    return write_audit, complete


def _audit_records(audit_lines: list[str]) -> list[dict[str, Any]]:
    return [json.loads(line) for line in audit_lines]


def _sent_lines(audit_lines: list[str]) -> Callable[[], int]:
    return lambda: sum(
        record["type"] == TRACE_CHANNEL_CHAT_SENT for record in _audit_records(audit_lines)
    )


def _pipeline(records: list[dict[str, Any]], source_event_id: str) -> list[dict[str, Any]]:
    """The pipeline records of one source event, in audit order.

    Correlated the way AC27 reads them: by the source event identifier up to
    admission, and by the run id the admission trace names from then on —
    the executor's traces carry the run id and the call id, not the source.
    """

    run_ids = {
        record["payload"]["run_id"]
        for record in records
        if record["type"] == TRACE_BRAIN_ADMISSION_ACCEPTED
        and record["payload"].get("source_event_id") == source_event_id
    }
    return [
        record
        for record in records
        if record["type"] in PIPELINE_TRACES
        and (
            record["payload"].get("source_event_id") == source_event_id
            or record["payload"].get("message_id") == source_event_id
            or record["payload"].get("run_id") in run_ids
        )
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("self_echo", [False, True])
async def test_ac27_example_config_drives_one_correlated_run_and_clean_shutdown(
    monkeypatch: pytest.MonkeyPatch,
    self_echo: bool,
) -> None:
    """AC27 through the example configuration and ``core.main.run``.

    Replaces ``test_example_config_drives_full_chat_pipeline_and_clean_shutdown``
    (allowlisted; superseded by R1, R2 and R8): that test fed a message no
    trigger policy accepts and asserted exactly 2 audit records of a
    synchronous pipeline. Here the message carries the configured keyword,
    so the channel's selected policy accepts it (R1); the engine admits it
    and the run executes detached from the ingestion (R2); the reply is
    delivered through the executor onto the platform send service and the
    audit sees, in AC27's order and under one ``run_id``, the trigger
    acceptance (with 0 ``run_id`` fields), the admission, the start, the
    action pair, the confirmed send and the completion (R8). The send fact
    exists only once the transport confirmed. The compatibility
    ``channel.chat.send`` route carries nothing, and a self echo is dropped
    before any of it (AC5).
    """

    environ = _environment()
    websocket = FakeWebSocket(welcome())
    audit_lines: list[str] = []
    twitch_session = FakeTwitchSession(
        client_id=environ["TWITCH_CLIENT_ID"],
        bot_user_id=environ["TWITCH_BOT_USER_ID"],
        websocket=websocket,
        sends=[sent_response()],
        sent_facts_seen=_sent_lines(audit_lines),
    )
    brain_session = FakeSession(FakeResponse(200, completion("Hello there")))
    diagnostics: list[str] = []
    resolved_configs: list[dict[str, Any]] = []
    write_audit, audit_complete = _audit_until(audit_lines, TRACE_BRAIN_RUN_COMPLETED, 1)
    channel = environ["TWITCH_BROADCASTER_ID"]

    _inject_boundaries(
        monkeypatch,
        twitch_session=twitch_session,
        brain_session=brain_session,
        audit_writer=write_audit,
        diagnostics=diagnostics,
        resolved_configs=resolved_configs,
    )

    stop = asyncio.Event()
    ready = asyncio.Event()
    task = await _start_application(environ, stop, ready, diagnostics)
    try:
        if self_echo:
            own_message = notification(
                channel, "bot-echo", "!ask Companion, hello there",
                viewer_id=environ["TWITCH_BOT_USER_ID"],
            )
            websocket.feed(own_message)
            websocket.feed(own_message)
        websocket.feed(notification(channel, "incoming-one", "!ask Companion, hello"))
        await asyncio.wait_for(audit_complete.wait(), timeout=1)
    finally:
        stop.set()
    assert await asyncio.wait_for(task, timeout=1) == 0

    assert diagnostics == []
    assert len(brain_session.post_calls) == 1
    model_call = brain_session.post_calls[0]
    assert "source_message_id: incoming-one" in (
        model_call["json"]["messages"][-1]["content"]
    )
    assert model_call["url"] == environ["OPENAI_ENDPOINT"]
    assert model_call["json"]["model"] == environ["OPENAI_MODEL"]
    assert model_call["headers"]["Authorization"] == (
        f"Bearer {environ['OPENAI_API_KEY']}"
    )

    assert twitch_session.ws_calls == [EVENTSUB_URL]
    assert twitch_session.get_calls == [
        {
            "url": TOKEN_VALIDATION_URL,
            "headers": {"Authorization": f"OAuth {environ['TWITCH_ACCESS_TOKEN']}"},
        }
    ]
    subscription_calls = [
        call
        for call in twitch_session.post_calls
        if call["url"] == EVENTSUB_SUBSCRIPTIONS_URL
    ]
    assert len(subscription_calls) == 1
    assert subscription_calls[0]["json"]["condition"] == {
        "broadcaster_user_id": channel,
        "user_id": environ["TWITCH_BOT_USER_ID"],
    }
    helix_calls = twitch_session.helix_calls
    assert len(helix_calls) == 1
    assert helix_calls[0]["json"] == {
        "broadcaster_id": channel,
        "sender_id": environ["TWITCH_BOT_USER_ID"],
        "message": "Hello there",
    }
    assert helix_calls[0]["headers"]["Authorization"] == (
        f"Bearer {environ['TWITCH_ACCESS_TOKEN']}"
    )
    assert helix_calls[0]["headers"]["Client-Id"] == environ["TWITCH_CLIENT_ID"]
    # 0 send facts existed when the transport confirmed: the fact follows.
    assert twitch_session.sent_facts_at_confirmation == [0]

    audit_records = _audit_records(audit_lines)
    audit_types = [record["type"] for record in audit_records]
    assert audit_types.count(COMPATIBILITY_ROUTE) == 0
    assert audit_types.count(INPUT_EVENT) == 1
    assert audit_types.count(TRACE_INPUT_TRIGGER_REJECTED) == 0
    for kind in (
        TRACE_INPUT_TRIGGER_ACCEPTED,
        TRACE_BRAIN_ADMISSION_ACCEPTED,
        TRACE_BRAIN_RUN_STARTED,
        TRACE_CHANNEL_CHAT_SENT,
        TRACE_BRAIN_RUN_COMPLETED,
    ):
        assert audit_types.count(kind) == 1, kind
    pipeline = _pipeline(audit_records, "incoming-one")
    assert [record["type"] for record in pipeline] == list(PIPELINE_TRACES)
    incoming = pipeline[0]
    assert incoming["payload"]["platform"] == PLATFORM
    assert incoming["payload"]["author"]["id"] == "integration-viewer"
    run_id = assert_ac27_correlation(
        traces_of(audit_records),
        source_event_id="incoming-one",
        platform=PLATFORM,
        channel_id=channel,
    )
    sent = next(record for record in pipeline if record["type"] == TRACE_CHANNEL_CHAT_SENT)
    assert sent["payload"]["message_id"] == "sent"
    assert sent["payload"]["channel_id"] == channel
    assert sent["payload"]["run_id"] == run_id
    completed = pipeline[-1]
    assert completed["payload"]["status"] == "success"
    assert completed["payload"]["delivery"] == "success"
    assert completed["payload"]["model_calls"] == 1
    assert completed["payload"]["sends"] == 1

    assert len(resolved_configs) == 1
    resolved = resolved_configs[0]["modules"]
    assert resolved["brain"]["endpoint"] == environ["OPENAI_ENDPOINT"]
    assert resolved["brain"]["model"] == environ["OPENAI_MODEL"]
    assert resolved["brain"]["api_key"] == environ["OPENAI_API_KEY"]
    assert resolved["twitch"]["client_secret"] == environ["TWITCH_CLIENT_SECRET"]
    assert twitch_session.close_calls == 1
    assert brain_session.close_calls == 1
    assert websocket.close_calls == 1


@pytest.mark.asyncio
async def test_r8_refused_send_is_sanitized_leaves_no_send_fact_and_next_send_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A refused platform send is sanitised and the next send succeeds.

    Replaces ``test_failed_helix_publication_is_sanitized_and_next_one_succeeds``
    (allowlisted; superseded by R1, R2 and R8): that test asserted exactly 4
    audit records, a fixed diagnostic list and 2 synchronous model calls for
    messages no trigger policy accepts. Both messages here carry the
    configured keyword, so the 2 model calls are the 2 accepted triggers'
    own (R1); each run is admitted and executes detached (R2); the refused
    delivery is the executor's explicit observation — the run reports
    ``delivery: error`` and 0 sends — and ``channel.chat.sent`` is emitted
    only for the confirmed send of the second run (R5, R8). The platform's
    rejection body carries every configured value and 0 of them reach a
    diagnostic or an audit line.
    """

    environ = _environment()
    websocket = FakeWebSocket(welcome())
    leaked_body = {"message": "|".join(environ.values())}
    audit_lines: list[str] = []
    twitch_session = FakeTwitchSession(
        client_id=environ["TWITCH_CLIENT_ID"],
        bot_user_id=environ["TWITCH_BOT_USER_ID"],
        websocket=websocket,
        sends=[FakeResponse(503, leaked_body), sent_response("recovered")],
        sent_facts_seen=_sent_lines(audit_lines),
    )
    brain_session = FakeSession(
        FakeResponse(200, completion("First")),
        FakeResponse(200, completion("Second")),
    )
    diagnostics: list[str] = []
    write_audit, audit_complete = _audit_until(audit_lines, TRACE_BRAIN_RUN_COMPLETED, 2)
    channel = environ["TWITCH_BROADCASTER_ID"]

    _inject_boundaries(
        monkeypatch,
        twitch_session=twitch_session,
        brain_session=brain_session,
        audit_writer=write_audit,
        diagnostics=diagnostics,
        resolved_configs=[],
    )

    stop = asyncio.Event()
    ready = asyncio.Event()
    task = await _start_application(environ, stop, ready, diagnostics)
    try:
        websocket.feed(
            notification(channel, "failed-publication", "!ask Companion, first request")
        )
        await asyncio.wait_for(twitch_session.helix_called.wait(), timeout=1)
        websocket.feed(
            notification(channel, "later-publication", "!ask Companion, second request")
        )
        await asyncio.wait_for(audit_complete.wait(), timeout=1)
    finally:
        stop.set()
    assert await asyncio.wait_for(task, timeout=1) == 0

    helix_calls = twitch_session.helix_calls
    assert [call["json"]["message"] for call in helix_calls] == ["First", "Second"]
    assert diagnostics == ["twitch chat send: rejected (status 503)"]
    rendered_diagnostics = rendered(diagnostics)
    assert all(occurrences(value, rendered_diagnostics) == 0 for value in environ.values())
    rendered_audit = rendered(_audit_records(audit_lines))
    assert all(occurrences(value, rendered_audit) == 0 for value in _credentials(environ))
    assert len(brain_session.post_calls) == 2
    audit_records = _audit_records(audit_lines)
    audit_types = [record["type"] for record in audit_records]
    assert audit_types.count(INPUT_EVENT) == 2
    assert audit_types.count(COMPATIBILITY_ROUTE) == 0
    assert audit_types.count(TRACE_INPUT_TRIGGER_ACCEPTED) == 2
    assert audit_types.count(TRACE_INPUT_TRIGGER_REJECTED) == 0
    assert audit_types.count(TRACE_BRAIN_RUN_STARTED) == 2
    assert audit_types.count(TRACE_BRAIN_RUN_COMPLETED) == 2
    # Only the confirmed send is a send fact; the rejected one leaves none.
    assert audit_types.count(TRACE_CHANNEL_CHAT_SENT) == 1
    assert twitch_session.sent_facts_at_confirmation == [0, 0]
    failed = _pipeline(audit_records, "failed-publication")
    assert [record["type"] for record in failed] == [
        trace for trace in PIPELINE_TRACES if trace != TRACE_CHANNEL_CHAT_SENT
    ]
    assert failed[-1]["payload"]["delivery"] == "error"
    assert failed[-1]["payload"]["sends"] == 0
    recovered = _pipeline(audit_records, "later-publication")
    assert [record["type"] for record in recovered] == list(PIPELINE_TRACES)
    assert recovered[-1]["payload"]["delivery"] == "success"
    assert recovered[-1]["payload"]["sends"] == 1
    assert_ac27_correlation(
        traces_of(audit_records),
        source_event_id="later-publication",
        platform=PLATFORM,
        channel_id=channel,
    )
    # The second model request must not treat the rejected reply as delivered.
    assert all(
        message["role"] != "assistant"
        for message in brain_session.post_calls[1]["json"]["messages"]
    )


def test_configured_runtime_values_are_absent_from_python_sources() -> None:
    environ = _environment()
    config = application.load_config(EXAMPLE_CONFIG, environ=environ)
    modules = config["modules"]
    configured_values = {
        modules["brain"]["endpoint"],
        modules["brain"]["model"],
        modules["brain"]["api_key"],
        modules["twitch"]["client_id"],
        modules["twitch"]["client_secret"],
        modules["twitch"]["access_token"],
        modules["twitch"]["broadcaster_id"],
        modules["twitch"]["bot_user_id"],
    }
    python_sources = "\n".join(
        path.read_text(encoding="utf-8") for path in ROOT.rglob("*.py")
    )

    assert all(value not in python_sources for value in configured_values)


# --------------------------------------------------------------------------- #
# Harness 2: the three manifests on the shared runtime-context fixture
# --------------------------------------------------------------------------- #

COMPANION = "Companion"
BROADCASTER = "broadcaster-42"
OTHER_CHANNEL = "broadcaster-99"
BOT_USER = "bot-24"
VIEWER = "viewer-7"
MODULE_NAMES = ("twitch", "brain", "audit")

ADMISSION = {
    "session_queue_capacity": 4,
    "global_pending_capacity": 64,
    "max_sessions": 32,
    "workers": 4,
    "wait_seconds": 30,
    "total_run_seconds": 120,
}
BUDGET = {
    "model_turns": 5,
    "model_call_seconds": 30,
    "action_seconds": 10,
    "max_tokens": 8192,
    "max_observation_bytes": 5_242_880,
}
MEMORY = {
    "max_sessions": 64,
    "max_exchanges": 20,
    "max_bytes": 65_536,
    "max_age_seconds": 3600.5,
}


def configured_secrets() -> dict[str, str]:
    """Credential values unique to one test, so a leak is unmistakable."""

    unique = uuid4().hex
    return {
        "client_secret": f"client-secret-{unique}",
        "access_token": f"access-token-{unique}",
        "api_key": f"api-key-{unique}",
    }


@dataclass
class Pipeline:
    """The three activated modules and every edge a test reads."""

    context: RuntimeContext
    clock: ManualClock
    websocket: FakeWebSocket
    twitch_session: FakeTwitchSession
    model: FakeSession
    audit_lines: list[str]
    diagnostics: list[str]
    coordinator: PhaseCoordinator
    health: ModuleHealth
    handles: dict[str, Any]
    secrets: dict[str, str]
    suppressed: list[str] = field(default_factory=list)

    @property
    def bus(self) -> EventBus:
        return self.context.bus

    @property
    def twitch(self) -> Any:
        return self.handles["twitch"]

    @property
    def brain(self) -> Any:
        return self.handles["brain"]

    @property
    def audit(self) -> Any:
        return self.handles["audit"]

    def traces(self) -> list[dict[str, Any]]:
        return traces_of(self.bus.list_events())

    def of(self, event_type: str) -> list[dict[str, Any]]:
        return events_of(self.bus, event_type)

    def snapshot(self) -> Mapping[str, int]:
        return self.context.supervision.snapshot()

    def feed(
        self,
        message_id: str,
        text: str,
        *,
        viewer_id: str = VIEWER,
        channel_id: str = BROADCASTER,
    ) -> None:
        self.websocket.feed(notification(channel_id, message_id, text, viewer_id=viewer_id))

    async def completed(self, count: int = 1) -> None:
        """Wait, in bare loop turns, until *count* runs have a terminal record,
        then let the traces a confirmed send handed to the loop land."""

        await wait_until(lambda: len(self.brain.scheduler.run_records()) >= count)
        await settle()

    async def stop(self) -> Any:
        """Stop through the coordinator, whose close phases report each
        module stopped through the health owner: recorded, then published."""

        return await self.coordinator.stop()


def suppressing(kind: str, suppressed: list[str]) -> Callable[[dict[str, Any]], None]:
    """A subscriber that makes the bus raise on *kind*, counting each time."""

    def explode(event: dict[str, Any]) -> None:
        suppressed.append(event["type"])
        raise RuntimeError(f"bus refuses {kind}")

    return explode


async def start_pipeline(
    *,
    model: FakeSession,
    sends: Iterable[Any] = (),
    bus: EventBus | None = None,
    raise_on: str | None = None,
    admission: Mapping[str, Any] | None = None,
    audit_writer: Callable[[str], Any] | None = None,
) -> Pipeline:
    """Load, activate and start the three modules on the shared fixture.

    The context is ``conftest.runtime_context`` with the trigger registry the
    loader fills from the chat input's manifest, a bounded chat context and
    the one explicit delivery grant the example configuration ships. The
    real loader validates every manifest and setting, and the real
    coordinator drives the phases and, through its observer, the context's
    health owner (R8).
    """

    clock = ManualClock()
    target_bus = bus if bus is not None else EventBus()
    suppressed: list[str] = []
    if raise_on is not None:
        target_bus.subscribe(raise_on, suppressing(raise_on, suppressed), order=-100)
    secrets = configured_secrets()
    policy = AuthorizationPolicy(
        [
            AuthorizationRule(
                rule_id="brain-delivers-chat-replies",
                action_name="chat.write",
                destination=Destination(PLATFORM, BROADCASTER, "chat"),
                principals=("brain",),
                natures=("write",),
                granted_permissions=("chat.write",),
            )
        ]
    )
    context = build_context(
        target_bus,
        clock=clock,
        trigger_registry=TriggerRegistry(companion_name=COMPANION),
        chat=ChatContext(
            max_messages=16, max_bytes=8192, max_age_seconds=600.0, clock=clock
        ),
        authorization=policy,
    )

    websocket = FakeWebSocket(welcome())
    twitch_session = FakeTwitchSession(
        client_id="configured-client",
        bot_user_id=BOT_USER,
        websocket=websocket,
        sends=sends,
        sent_facts_seen=lambda: len(events_of(target_bus, TRACE_CHANNEL_CHAT_SENT)),
    )
    audit_lines: list[str] = []
    diagnostics: list[str] = []

    async def record_audit(line: str) -> None:
        audit_lines.append(line)

    async def no_delay(_: float) -> None:
        await asyncio.sleep(0)

    config = {
        "enabled_modules": list(MODULE_NAMES),
        "modules": {
            "twitch": {
                "client_id": "configured-client",
                "client_secret": secrets["client_secret"],
                "access_token": secrets["access_token"],
                "broadcaster_id": BROADCASTER,
                "bot_user_id": BOT_USER,
                "companion_name": COMPANION,
                "_session_factory": lambda: twitch_session,
                "_retry_delay": no_delay,
                "diagnostic_reporter": diagnostics.append,
            },
            "brain": {
                "endpoint": "https://configured.invalid/chat/completions",
                "model": "configured-model",
                "api_key": secrets["api_key"],
                "admission": {**ADMISSION, **(admission or {})},
                "budget": dict(BUDGET),
                "conversation_memory": dict(MEMORY),
                "_session_factory": lambda: model,
                "_sleeper": clock.sleep,
                "diagnostic_reporter": diagnostics.append,
            },
            "audit": {
                "output": "stdout",
                "queue": {"max_records": 1000, "max_bytes": 4 * 1024 * 1024},
                "_writer": audit_writer if audit_writer is not None else record_audit,
            },
        },
    }
    loader = ModuleLoader(target_bus, MODULES, context=context, environ={})
    activations = await loader.activate_enabled(config)
    handles = {activation.name: activation.handle for activation in activations}
    assert tuple(handles) == MODULE_NAMES
    health = context.health
    coordinator = PhaseCoordinator(
        activations,
        tasks=context.tasks,
        reporter=diagnostics.append,
        observer=health.observe_phase,
    )
    startup = await coordinator.start()
    assert startup.status == 0, diagnostics
    assert {name: health.state(name) for name in handles} == {
        name: MODULE_STATE_READY for name in handles
    }
    return Pipeline(
        context=context,
        clock=clock,
        websocket=websocket,
        twitch_session=twitch_session,
        model=model,
        audit_lines=audit_lines,
        diagnostics=diagnostics,
        coordinator=coordinator,
        health=health,
        handles=handles,
        secrets=secrets,
        suppressed=suppressed,
    )


# --------------------------------------------------------------------------- #
# AC27 — one accepted message, one correlated, ordered set of traces
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_ac27_one_accepted_message_produces_ordered_traces_under_one_run_id() -> None:
    """AC27 on the shared fixture, read from the bus history.

    Exactly 1 accepted trigger, 1 accepted admission, 1 run start, the
    action pair, 1 confirmed-send fact and 1 run completion, in that order
    among this run's traces; one identical ``run_id`` from the admission
    on, 0 ``run_id`` fields on the trigger trace, which is correlated by
    source event and channel pair instead; and the send fact is published
    only after the transport confirmed *and* after its owner recorded it.
    """

    pipeline = await start_pipeline(
        model=FakeSession(FakeResponse(200, completion("Hello there"))),
        sends=[sent_response("sent-1")],
    )
    at_send_fact: list[tuple[int, int]] = []

    def observe_send_fact(event: dict[str, Any]) -> None:
        at_send_fact.append(
            (pipeline.twitch_session.helix_confirmed, pipeline.twitch.send_record.confirmed)
        )

    pipeline.bus.subscribe(TRACE_CHANNEL_CHAT_SENT, observe_send_fact)
    try:
        pipeline.feed("incoming-one", f"{COMPANION}, hello")
        await pipeline.completed(1)
    finally:
        report = await pipeline.stop()
    assert report.status == 0
    assert pipeline.diagnostics == []

    traces = pipeline.traces()
    kinds = [trace["type"] for trace in traces]
    for kind, count in (
        (TRACE_INPUT_TRIGGER_ACCEPTED, 1),
        (TRACE_INPUT_TRIGGER_REJECTED, 0),
        (TRACE_BRAIN_ADMISSION_ACCEPTED, 1),
        (TRACE_BRAIN_ADMISSION_REJECTED, 0),
        (TRACE_BRAIN_RUN_STARTED, 1),
        (TRACE_ACTION_STARTED, 1),
        (TRACE_ACTION_COMPLETED, 1),
        (TRACE_CHANNEL_CHAT_SENT, 1),
        (TRACE_BRAIN_RUN_COMPLETED, 1),
    ):
        assert kinds.count(kind) == count, kind
    run_id = assert_ac27_correlation(
        traces, source_event_id="incoming-one", platform=PLATFORM, channel_id=BROADCASTER
    )
    (completed,) = pipeline.of(TRACE_BRAIN_RUN_COMPLETED)
    assert completed["payload"]["run_id"] == run_id
    assert completed["payload"]["status"] == "success"
    assert completed["payload"]["delivery"] == "success"
    assert completed["payload"]["model_calls"] == 1
    assert completed["payload"]["sends"] == 1
    (sent,) = pipeline.of(TRACE_CHANNEL_CHAT_SENT)
    assert sent["payload"]["message_id"] == "sent-1"
    assert sent["payload"]["channel_id"] == BROADCASTER

    # The send fact followed the transport's confirmation and the owner's
    # record: 0 facts existed at confirmation, and at publication the
    # transport had confirmed 1 send that the provider had recorded.
    assert pipeline.twitch_session.sent_facts_at_confirmation == [0]
    assert at_send_fact == [(1, 1)]
    assert len(pipeline.twitch_session.helix_calls) == 1
    assert len(pipeline.model.post_calls) == 1


# --------------------------------------------------------------------------- #
# AC28 — an audit failure never reaches the external effect
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_ac28_failing_audit_writer_neither_cancels_nor_repeats_the_send() -> None:
    """AC28: with every audit record failing, the send happens exactly once.

    The confirmed external send is neither cancelled nor repeated — 1 Helix
    request, 1 provider invocation, 1 confirmed record — the run reports
    its terminal state through its owner and its trace, every offered
    record is counted as an audit loss outside the queue, and the 7
    counters R8 names are readable from supervision.
    """

    async def failing_writer(line: str) -> None:
        raise OSError("audit sink unavailable")

    pipeline = await start_pipeline(
        model=FakeSession(FakeResponse(200, completion("Hello there"))),
        sends=[sent_response()],
        audit_writer=failing_writer,
    )
    try:
        pipeline.feed("incoming-one", f"{COMPANION}, hello")
        await pipeline.completed(1)
        await wait_until(lambda: pipeline.audit.pending == 0)
        # Everything published since the audit's catch-all was routed: the
        # readiness facts of the two modules prepared before it never
        # reached it.
        offered = len(
            [
                event
                for event in pipeline.bus.list_events()
                if not (
                    event["type"] == TRACE_MODULE_READY
                    and event["payload"]["module"] != "audit"
                )
            ]
        )
        losses_before_stop = pipeline.audit.losses
        counted_before_stop = pipeline.snapshot()[COUNTER_AUDIT_RECORD_LOSSES]
    finally:
        report = await pipeline.stop()
    assert report.status == 0

    # Exactly 1 confirmed send: not cancelled, not repeated.
    assert len(pipeline.twitch_session.helix_calls) == 1
    assert pipeline.context.executor.provider_invocations == 1
    assert pipeline.twitch.send_record.snapshot() == {
        "emitted": 1, "confirmed": 1, "failed": 0, "unknown": 0, "last_message_id": "sent",
    }
    (observation,) = pipeline.context.executor.outcomes().values()
    assert observation.status == "success"

    # The run reports its terminal state, from its owner and on the bus.
    (record,) = pipeline.brain.scheduler.run_records().values()
    assert (record.status, record.delivery, record.sends) == ("success", "success", 1)
    assert record.status != "cancelled"
    (completed,) = pipeline.of(TRACE_BRAIN_RUN_COMPLETED)
    assert completed["payload"]["run_id"] == record.run_id
    assert completed["payload"]["status"] == "success"
    assert_ac27_correlation(
        pipeline.traces(),
        source_event_id="incoming-one",
        platform=PLATFORM,
        channel_id=BROADCASTER,
    )

    # Every record offered to the failing writer is a counted loss, reported
    # outside the queue and 0 of them reached the sink.
    assert pipeline.audit_lines == []
    assert offered > 0
    assert losses_before_stop == counted_before_stop == offered

    snapshot = pipeline.snapshot()
    assert set(REQUIRED_COUNTERS) <= set(snapshot)
    assert len(REQUIRED_COUNTERS) == 7
    assert all(isinstance(snapshot[name], int) for name in REQUIRED_COUNTERS)
    assert {name: snapshot[name] for name in REQUIRED_COUNTERS if name != COUNTER_AUDIT_RECORD_LOSSES} == {
        COUNTER_TRIGGER_REJECTIONS: 0,
        COUNTER_ADMISSION_REJECTIONS: 0,
        COUNTER_STALE_DROP: 0,
        "run_deadline_expiries": 0,
        "action_timeouts": 0,
        "dedup_evictions": 0,
    }
    assert snapshot[COUNTER_LOST_TRACES] == 0
    assert pipeline.diagnostics == []


# --------------------------------------------------------------------------- #
# R8 — the owner records the terminal state before its trace is published
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", sorted(TERMINAL_TRACE_TYPES))
async def test_r8_terminal_state_is_recorded_before_its_trace_and_survives_a_lost_publication(
    terminal: str,
) -> None:
    """R8 end to end: the bus raises on *terminal*, the owner's state stands.

    With the bus made to raise on one terminal trace type, the owner's
    recorded state is still readable afterwards — the executor's
    observation, the scheduler's run record, the provider's confirmed send,
    the health owner's stopped state — the transport is invoked exactly 1
    time with 0 retries and 0 repeats, and the lost-trace count equals the
    number of publications the bus refused. The other three terminal traces
    are published as usual.
    """

    pipeline = await start_pipeline(
        model=FakeSession(FakeResponse(200, completion("Hello there"))),
        sends=[sent_response("sent-1")],
        raise_on=terminal,
    )
    try:
        pipeline.feed("incoming-one", f"{COMPANION}, hello")
        await pipeline.completed(1)
    finally:
        report = await pipeline.stop()
    assert report.status == 0
    assert pipeline.diagnostics == []

    (admission,) = pipeline.of(TRACE_BRAIN_ADMISSION_ACCEPTED)
    run_id = admission["payload"]["run_id"]
    call_id = f"{run_id}/call-1"

    # Every owner's own record is readable, whatever the bus did.
    observation = pipeline.context.executor.outcome(call_id)
    assert observation is not None
    assert observation.status == "success"
    assert observation.result["message_id"] == "sent-1"
    record = pipeline.brain.scheduler.run_record(run_id)
    assert record is not None
    assert (record.status, record.delivery, record.sends, record.model_calls) == (
        "success", "success", 1, 1,
    )
    assert pipeline.twitch.send_record.confirmed == 1
    assert pipeline.twitch.send_record.last_message_id == "sent-1"
    assert {name: pipeline.health.state(name) for name in MODULE_NAMES} == {
        name: MODULE_STATE_STOPPED for name in MODULE_NAMES
    }

    # The transport was reached exactly once: 0 retries, 0 repeats.
    assert len(pipeline.twitch_session.helix_calls) == 1
    assert pipeline.twitch.send_record.emitted == 1
    assert pipeline.context.executor.provider_invocations == 1
    assert len(pipeline.model.post_calls) == 1

    # The refused trace is absent; the other terminal traces are present.
    expected = {
        TRACE_ACTION_COMPLETED: 1,
        TRACE_BRAIN_RUN_COMPLETED: 1,
        TRACE_CHANNEL_CHAT_SENT: 1,
        TRACE_MODULE_STOPPED: len(MODULE_NAMES),
    }
    assert pipeline.of(terminal) == []
    for kind, count in expected.items():
        if kind != terminal:
            assert len(pipeline.of(kind)) == count, kind
    assert pipeline.suppressed == [terminal] * expected[terminal]
    assert pipeline.snapshot()[COUNTER_LOST_TRACES] == expected[terminal]
    assert len(pipeline.context.supervision.losses) == expected[terminal]
    assert all(
        loss.startswith(f"{terminal}: PublicationError")
        for loss in pipeline.context.supervision.losses
    )


@pytest.mark.asyncio
async def test_r8_owner_whose_state_write_raises_publishes_no_trace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R8, the converse: a failed state write means 0 traces for that state.

    The executor's own write of the action's terminal state is made to
    raise: 0 ``action.completed`` is published, nothing is recorded for the
    call, the failure reaches the owner's caller — the run reports the
    delivery as an error, never as a success — and the transport, reached
    before the write, is still invoked exactly 1 time. The health owner's
    write is made to raise the same way: 0 ``module.stopped`` and no
    recorded state. Neither failure is a lost trace.
    """

    pipeline = await start_pipeline(
        model=FakeSession(FakeResponse(200, completion("Hello there"))),
        sends=[sent_response("sent-1")],
    )
    executor = pipeline.context.executor

    def refuse_write(self: ActionExecutor, call_id: str, observation: Any) -> None:
        raise RuntimeError("outcome store unavailable")

    # The executor is slotted, so its write is replaced on the class for the
    # duration of this test; the monkeypatch restores it afterwards.
    monkeypatch.setattr(ActionExecutor, "_record", refuse_write)

    class RefusingHealth(ModuleHealth):
        def _record(self, module: str, state: str, fact: Any) -> None:
            raise RuntimeError("state map unavailable")

    refusing_health = RefusingHealth(pipeline.context.supervision, clock=pipeline.clock)
    try:
        pipeline.feed("incoming-one", f"{COMPANION}, hello")
        await pipeline.completed(1)
        with pytest.raises(RuntimeError):
            await refusing_health.observe_phase(PHASE_CLOSE, "twitch")
    finally:
        report = await pipeline.stop()
    assert report.status == 0

    (admission,) = pipeline.of(TRACE_BRAIN_ADMISSION_ACCEPTED)
    run_id = admission["payload"]["run_id"]
    assert pipeline.of(TRACE_ACTION_STARTED) != []
    assert pipeline.of(TRACE_ACTION_COMPLETED) == []
    assert executor.outcome(f"{run_id}/call-1") is None
    assert executor.outcomes() == {}
    record = pipeline.brain.scheduler.run_record(run_id)
    assert record is not None
    assert record.delivery != "success"
    assert record.sends == 0
    (completed,) = pipeline.of(TRACE_BRAIN_RUN_COMPLETED)
    assert completed["payload"]["delivery"] == record.delivery
    assert completed["payload"]["sends"] == 0
    # The transport was reached once before the write; nothing repeated it.
    assert len(pipeline.twitch_session.helix_calls) == 1
    assert pipeline.twitch.send_record.emitted == 1

    assert refusing_health.state("twitch") is None
    # Only the coordinator-driven health owner reported the 3 modules stopped.
    assert len(pipeline.of(TRACE_MODULE_STOPPED)) == len(MODULE_NAMES)
    assert pipeline.snapshot()[COUNTER_LOST_TRACES] == 0


# --------------------------------------------------------------------------- #
# AC29 — traces are correlated, and carry no credential and no prompt
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_ac29_traces_carry_correlation_but_no_credential_and_no_prompt_body() -> None:
    """AC29: rendered recursively, every trace is free of secrets and prompt.

    The scenario uses configured credentials on both edges and a full prompt
    on the model. Rendering every emitted trace — each string at any depth,
    every key, every scalar and the JSON form, on the bus and in the audit
    lines that leave the process — finds 0 occurrences of any credential
    value and 0 occurrences of any prompt message body, while the AC27
    correlation identifiers are still present.
    """

    pipeline = await start_pipeline(
        model=FakeSession(FakeResponse(200, completion("Hello there"))),
        sends=[sent_response("sent-1")],
    )
    try:
        pipeline.feed("incoming-one", f"{COMPANION}, what is the secret handshake?")
        await pipeline.completed(1)
        await wait_until(lambda: pipeline.audit.pending == 0)
    finally:
        report = await pipeline.stop()
    assert report.status == 0

    # The scenario did use the credentials and did send a full prompt.
    secrets = pipeline.secrets
    (validation,) = pipeline.twitch_session.get_calls
    assert validation["headers"]["Authorization"] == f"OAuth {secrets['access_token']}"
    (helix,) = pipeline.twitch_session.helix_calls
    assert helix["headers"]["Authorization"] == f"Bearer {secrets['access_token']}"
    (request,) = pipeline.model.post_calls
    assert request["headers"]["Authorization"] == f"Bearer {secrets['api_key']}"
    prompt_bodies = [message["content"] for message in request["json"]["messages"]]
    assert len(prompt_bodies) >= 2
    assert all(len(body) > 20 for body in prompt_bodies)
    assert any("secret handshake" in body for body in prompt_bodies)

    bus_traces = pipeline.traces()
    audit_traces = traces_of(_audit_records(pipeline.audit_lines))
    # The audit's catch-all is routed at its own preparation, after the two
    # other modules were reported ready, and the audit flushes after every
    # ordinary close and closes last: its lines carry every trace but those
    # 2 readiness facts and its own ``module.stopped``, which the coordinator
    # reports only once the observation close phase returned.
    def before_or_after_the_audit(trace: Mapping[str, Any]) -> bool:
        module = trace["payload"].get("module")
        return (trace["type"] == TRACE_MODULE_READY and module != "audit") or (
            trace["type"] == TRACE_MODULE_STOPPED and module == "audit"
        )

    assert [trace["type"] for trace in audit_traces] == [
        trace["type"] for trace in bus_traces if not before_or_after_the_audit(trace)
    ]
    assert len(audit_traces) > 0
    assert {trace["type"] for trace in bus_traces} >= {
        TRACE_MODULE_READY,
        TRACE_INPUT_TRIGGER_ACCEPTED,
        TRACE_BRAIN_ADMISSION_ACCEPTED,
        TRACE_BRAIN_RUN_STARTED,
        TRACE_ACTION_STARTED,
        TRACE_ACTION_COMPLETED,
        TRACE_CHANNEL_CHAT_SENT,
        TRACE_BRAIN_RUN_COMPLETED,
        TRACE_MODULE_STOPPED,
    }
    strings = rendered(bus_traces) + rendered(audit_traces)
    for name, value in secrets.items():
        assert occurrences(value, strings) == 0, name
    for body in prompt_bodies:
        assert occurrences(body, strings) == 0
    assert occurrences(json.dumps(request["json"]), strings) == 0
    assert count_keys(bus_traces, "prompt") == 0
    assert count_keys(bus_traces, "reasoning") == 0

    # The correlation identifiers AC27 requires are still there.
    run_id = assert_ac27_correlation(
        bus_traces, source_event_id="incoming-one", platform=PLATFORM, channel_id=BROADCASTER
    )
    assert_ac27_correlation(
        audit_traces, source_event_id="incoming-one", platform=PLATFORM, channel_id=BROADCASTER
    )
    assert occurrences(run_id, strings) > 0
    assert occurrences("incoming-one", strings) > 0
    assert occurrences(f"{run_id}/call-1", strings) > 0
    assert pipeline.diagnostics == []


# --------------------------------------------------------------------------- #
# AC11 — the chat context is fed whatever the pipeline decides afterwards
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_ac11_rejected_saturated_and_stale_messages_all_reach_the_channel_context() -> None:
    """AC11: 3 messages in 1 channel, 3 different fates, 1 dated transcript.

    With the only worker held by a run in another channel, the served
    channel receives a message its trigger rejects, a message admission
    refuses at the global pending cap and a message that is admitted and
    then dropped stale when the wait deadline passes. A bounded read of the
    chat context for that channel returns all 3 in ingestion order, each
    with the timestamp it was recorded at; the same read for the other
    channel returns 0 of them.
    """

    model = HeldSession(FakeResponse(200, completion("Held reply")))
    pipeline = await start_pipeline(
        model=model,
        admission={
            "workers": 1,
            "session_queue_capacity": 1,
            "global_pending_capacity": 1,
            "wait_seconds": 5,
            "total_run_seconds": 120,
        },
    )
    clock = pipeline.clock
    try:
        # Hold the only worker with a run in the other channel.
        pipeline.feed("hold-1", f"{COMPANION}, hold on", viewer_id="viewer-holder", channel_id=OTHER_CHANNEL)
        await wait_until(model.entered.is_set)

        clock.advance(1.0)
        pipeline.feed("m-1", "hello everyone", viewer_id="viewer-a")
        await wait_until(lambda: len(pipeline.of(TRACE_INPUT_TRIGGER_REJECTED)) == 1)

        clock.advance(1.0)
        pipeline.feed("m-2", f"{COMPANION}, second", viewer_id="viewer-a")
        await wait_until(lambda: len(pipeline.of(TRACE_BRAIN_ADMISSION_ACCEPTED)) == 2)

        clock.advance(1.0)
        pipeline.feed("m-3", f"{COMPANION}, third", viewer_id="viewer-b")
        await wait_until(lambda: len(pipeline.of(TRACE_BRAIN_ADMISSION_REJECTED)) == 1)

        # Past the 5-unit wait deadline: the queued message is stale.
        clock.advance(6.0)
        await wait_until(lambda: len(pipeline.of(TRACE_BRAIN_RUN_COMPLETED)) == 1)

        model.release.set()
        await pipeline.completed(2)
    finally:
        report = await pipeline.stop()
    assert report.status == 0

    (rejected_trigger,) = pipeline.of(TRACE_INPUT_TRIGGER_REJECTED)
    assert rejected_trigger["payload"]["source_event_id"] == "m-1"
    (rejected_admission,) = pipeline.of(TRACE_BRAIN_ADMISSION_REJECTED)
    assert rejected_admission["payload"]["source_event_id"] == "m-3"
    assert rejected_admission["payload"]["reason"] == "global_pending_cap"
    stale = next(
        trace for trace in pipeline.of(TRACE_BRAIN_RUN_COMPLETED)
        if trace["payload"]["source_event_id"] == "m-2"
    )
    assert (stale["payload"]["status"], stale["payload"]["reason"]) == ("timeout", "stale_drop")
    assert stale["payload"]["model_calls"] == 0
    snapshot = pipeline.snapshot()
    assert snapshot[COUNTER_TRIGGER_REJECTIONS] == 1
    assert snapshot[COUNTER_ADMISSION_REJECTIONS] == 1
    assert snapshot[COUNTER_STALE_DROP] == 1
    # Only the holder ever reached the model; nothing was sent anywhere.
    assert len(model.post_calls) == 1
    assert pipeline.twitch_session.helix_calls == []

    chat = pipeline.context.chat
    records = chat.read(PLATFORM, BROADCASTER, limit=10)
    assert [(record.message_id, record.author_id, record.text) for record in records] == [
        ("m-1", "viewer-a", "hello everyone"),
        ("m-2", "viewer-a", f"{COMPANION}, second"),
        ("m-3", "viewer-b", f"{COMPANION}, third"),
    ]
    timestamps = [record.timestamp for record in records]
    assert timestamps == [1001.0, 1002.0, 1003.0]
    assert timestamps == sorted(timestamps) and len(set(timestamps)) == 3

    elsewhere = chat.read(PLATFORM, OTHER_CHANNEL, limit=10)
    assert [record.message_id for record in elsewhere] == ["hold-1"]
    assert {record.message_id for record in elsewhere} & {"m-1", "m-2", "m-3"} == set()
