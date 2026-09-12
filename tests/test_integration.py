from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

import core.main as application
from modules.twitch import (
    EVENTSUB_SUBSCRIPTIONS_URL,
    EVENTSUB_URL,
    HELIX_CHAT_URL,
    TOKEN_VALIDATION_URL,
)


ROOT = Path(__file__).parents[1]
EXAMPLE_CONFIG = ROOT / "config.yaml.example"


class FakeResponse:
    def __init__(self, status: int, body: Any) -> None:
        self.status = status
        self._body = body
        self.release_calls = 0

    async def json(self) -> Any:
        return self._body

    def release(self) -> None:
        self.release_calls += 1


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
    def __init__(
        self,
        environ: Mapping[str, str],
        websocket: FakeWebSocket,
        send_responses: list[FakeResponse],
    ) -> None:
        self._environ = environ
        self._websocket = websocket
        self._send_responses = list(send_responses)
        self.get_calls: list[dict[str, Any]] = []
        self.post_calls: list[dict[str, Any]] = []
        self.ws_calls: list[str] = []
        self.close_calls = 0
        self.helix_called = asyncio.Event()

    async def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.get_calls.append({"url": url, **kwargs})
        return FakeResponse(
            200,
            {
                "client_id": self._environ["TWITCH_CLIENT_ID"],
                "user_id": self._environ["TWITCH_BOT_USER_ID"],
            },
        )

    async def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.post_calls.append({"url": url, **kwargs})
        if url == EVENTSUB_SUBSCRIPTIONS_URL:
            return FakeResponse(202, {"data": [{"id": "subscription-created"}]})
        if url == HELIX_CHAT_URL and self._send_responses:
            response = self._send_responses.pop(0)
            self.helix_called.set()
            return response
        raise AssertionError("unexpected Twitch POST operation")

    async def ws_connect(self, url: str) -> FakeWebSocket:
        self.ws_calls.append(url)
        return self._websocket

    async def close(self) -> None:
        self.close_calls += 1


class FakeBrainSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self._responses = list(responses)
        self.post_calls: list[dict[str, Any]] = []
        self.close_calls = 0

    async def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.post_calls.append({"url": url, **kwargs})
        if not self._responses:
            raise AssertionError("unexpected model request")
        return self._responses.pop(0)

    async def close(self) -> None:
        self.close_calls += 1


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


def _welcome() -> dict[str, Any]:
    return {
        "metadata": {"message_type": "session_welcome"},
        "payload": {"session": {"id": "integration-session"}},
    }


def _notification(
    environ: Mapping[str, str], message_id: str, text: str
) -> dict[str, Any]:
    return {
        "metadata": {
            "message_type": "notification",
            "subscription_type": "channel.chat.message",
        },
        "payload": {
            "subscription": {"type": "channel.chat.message"},
            "event": {
                "broadcaster_user_id": environ["TWITCH_BROADCASTER_ID"],
                "chatter_user_id": "integration-viewer",
                "chatter_user_name": "Integration Viewer",
                "message_id": message_id,
                "message": {"text": text},
            },
        },
    }


def _completion(text: str) -> dict[str, Any]:
    return {"choices": [{"message": {"content": text}}]}


def _inject_boundaries(
    monkeypatch: pytest.MonkeyPatch,
    *,
    environ: Mapping[str, str],
    twitch_session: FakeTwitchSession,
    brain_session: FakeBrainSession,
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


PIPELINE_TRACES = (
    "channel.chat.message",
    "input.trigger.accepted",
    "brain.admission.accepted",
    "brain.run.started",
    "action.started",
    "channel.chat.sent",
    "action.completed",
    "brain.run.completed",
)
"""One accepted message through the real pipeline, in order (R8, AC27)."""

def _audit_until(audit_lines: list[str], event_type: str, count: int) -> tuple[Callable[[str], Any], asyncio.Event]:
    """An audit writer that signals once *count* records of *event_type* landed."""

    complete = asyncio.Event()

    async def write_audit(line: str) -> None:
        audit_lines.append(line)
        if sum(json.loads(entry)["type"] == event_type for entry in audit_lines) >= count:
            complete.set()

    return write_audit, complete


def _pipeline(records: list[dict[str, Any]], source_event_id: str) -> list[dict[str, Any]]:
    """The pipeline traces of one source event, in audit order.

    Correlated the way AC27 reads them: by the source event identifier up to
    admission, and by the run id the admission trace names from then on —
    the executor's traces carry the run id and the call id, not the source.
    """

    run_ids = {
        record["payload"]["run_id"]
        for record in records
        if record["type"] == "brain.admission.accepted"
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


def _credentials(environ: Mapping[str, str]) -> list[str]:
    return [
        environ["TWITCH_CLIENT_SECRET"],
        environ["TWITCH_ACCESS_TOKEN"],
        environ["OPENAI_API_KEY"],
        environ["OPENAI_ENDPOINT"],
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("self_echo", [False, True])
async def test_example_config_drives_full_chat_pipeline_and_clean_shutdown(
    monkeypatch: pytest.MonkeyPatch,
    self_echo: bool,
) -> None:
    """Replaces the synchronous 4-record pipeline test (allowlisted; R1, R2, R8).

    The message carries the configured keyword, so the channel's selected
    trigger policy accepts it (R1); the brain admits it and the run executes
    detached from the ingestion (R2); the reply is delivered through the
    executor onto the platform send service, and the traces of the run —
    admission, start, action start and completion, the confirmed send,
    completion — are all audited with one run id (R8, AC27). The compatibility
    ``channel.chat.send`` route carries nothing. A self echo is dropped before
    any of it (AC5).
    """

    environ = _environment()
    websocket = FakeWebSocket(_welcome())
    twitch_session = FakeTwitchSession(
        environ,
        websocket,
        [FakeResponse(200, {"data": [{"message_id": "sent", "is_sent": True}]})],
    )
    brain_session = FakeBrainSession([FakeResponse(200, _completion("Hello there"))])
    audit_lines: list[str] = []
    diagnostics: list[str] = []
    resolved_configs: list[dict[str, Any]] = []
    write_audit, audit_complete = _audit_until(audit_lines, "brain.run.completed", 1)

    _inject_boundaries(
        monkeypatch,
        environ=environ,
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
            own_message = _notification(
                environ, "bot-echo", "!ask Companion, hello there"
            )
            own_message["payload"]["event"]["chatter_user_id"] = environ[
                "TWITCH_BOT_USER_ID"
            ]
            websocket.feed(own_message)
            websocket.feed(own_message)
        websocket.feed(
            _notification(environ, "incoming-one", "!ask Companion, hello")
        )
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
    helix_calls = [
        call for call in twitch_session.post_calls if call["url"] == HELIX_CHAT_URL
    ]
    assert len(subscription_calls) == 1
    assert subscription_calls[0]["json"]["condition"] == {
        "broadcaster_user_id": environ["TWITCH_BROADCASTER_ID"],
        "user_id": environ["TWITCH_BOT_USER_ID"],
    }
    assert len(helix_calls) == 1
    assert helix_calls[0]["json"] == {
        "broadcaster_id": environ["TWITCH_BROADCASTER_ID"],
        "sender_id": environ["TWITCH_BOT_USER_ID"],
        "message": "Hello there",
    }
    assert helix_calls[0]["headers"]["Authorization"] == (
        f"Bearer {environ['TWITCH_ACCESS_TOKEN']}"
    )
    assert helix_calls[0]["headers"]["Client-Id"] == environ["TWITCH_CLIENT_ID"]

    audit_records = [json.loads(line) for line in audit_lines]
    audit_types = [record["type"] for record in audit_records]
    assert audit_types.count("channel.chat.send") == 0
    assert audit_types.count("channel.chat.message") == 1
    assert audit_types.count("input.trigger.rejected") == 0
    pipeline = _pipeline(audit_records, "incoming-one")
    assert [record["type"] for record in pipeline] == list(PIPELINE_TRACES)
    incoming, trigger, *run_traces = pipeline
    assert incoming["payload"]["platform"] == "twitch"
    assert incoming["payload"]["author"]["id"] == "integration-viewer"
    assert "run_id" not in trigger["payload"]
    run_ids = {record["payload"]["run_id"] for record in run_traces}
    assert len(run_ids) == 1
    admission = run_traces[0]
    assert (trigger["payload"]["platform"], trigger["payload"]["channel_id"]) == (
        admission["payload"]["platform"], admission["payload"]["channel_id"],
    )
    assert trigger["payload"]["source_event_id"] == admission["payload"]["source_event_id"]
    sent = next(record for record in pipeline if record["type"] == "channel.chat.sent")
    assert sent["payload"]["message_id"] == "sent"
    assert sent["payload"]["channel_id"] == environ["TWITCH_BROADCASTER_ID"]
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
async def test_failed_helix_publication_is_sanitized_and_next_one_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A refused platform send is sanitised and the next send succeeds.

    Replaces the fixed 7-record, 2-synchronous-model-call variant
    (allowlisted; R1, R2, R8). Both messages carry the configured keyword, so
    the 2 model calls are the 2 accepted triggers' own (R1); each run is
    admitted and executes detached (R2); the refused delivery is the
    executor's explicit observation — the run reports ``delivery: error``
    and 0 sends, and the confirmed send of the second run is the only send
    fact (R5, R8).
    """

    environ = _environment()
    websocket = FakeWebSocket(_welcome())
    leaked_body = {"message": "|".join(environ.values())}
    twitch_session = FakeTwitchSession(
        environ,
        websocket,
        [
            FakeResponse(503, leaked_body),
            FakeResponse(
                200,
                {"data": [{"message_id": "recovered", "is_sent": True}]},
            ),
        ],
    )
    brain_session = FakeBrainSession(
        [
            FakeResponse(200, _completion("First")),
            FakeResponse(200, _completion("Second")),
        ]
    )
    audit_lines: list[str] = []
    diagnostics: list[str] = []
    write_audit, audit_complete = _audit_until(audit_lines, "brain.run.completed", 2)

    _inject_boundaries(
        monkeypatch,
        environ=environ,
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
            _notification(
                environ, "failed-publication", "!ask Companion, first request"
            )
        )
        await asyncio.wait_for(twitch_session.helix_called.wait(), timeout=1)
        websocket.feed(
            _notification(
                environ, "later-publication", "!ask Companion, second request"
            )
        )
        await asyncio.wait_for(audit_complete.wait(), timeout=1)
    finally:
        stop.set()
    assert await asyncio.wait_for(task, timeout=1) == 0

    helix_calls = [
        call for call in twitch_session.post_calls if call["url"] == HELIX_CHAT_URL
    ]
    assert [call["json"]["message"] for call in helix_calls] == ["First", "Second"]
    assert diagnostics == ["twitch chat send: rejected (status 503)"]
    rendered_diagnostics = "\n".join(diagnostics)
    assert all(value not in rendered_diagnostics for value in environ.values())
    assert all(value not in "\n".join(audit_lines) for value in _credentials(environ))
    assert len(brain_session.post_calls) == 2
    audit_records = [json.loads(line) for line in audit_lines]
    audit_types = [record["type"] for record in audit_records]
    assert audit_types.count("channel.chat.message") == 2
    assert audit_types.count("channel.chat.send") == 0
    assert audit_types.count("input.trigger.accepted") == 2
    assert audit_types.count("input.trigger.rejected") == 0
    assert audit_types.count("brain.run.started") == 2
    assert audit_types.count("brain.run.completed") == 2
    # Only the confirmed send is a send fact; the rejected one leaves none.
    assert audit_types.count("channel.chat.sent") == 1
    failed = _pipeline(audit_records, "failed-publication")
    assert [record["type"] for record in failed] == [
        trace for trace in PIPELINE_TRACES if trace != "channel.chat.sent"
    ]
    assert failed[-1]["payload"]["delivery"] == "error"
    assert failed[-1]["payload"]["sends"] == 0
    recovered = _pipeline(audit_records, "later-publication")
    assert [record["type"] for record in recovered] == list(PIPELINE_TRACES)
    assert recovered[-1]["payload"]["delivery"] == "success"
    assert recovered[-1]["payload"]["sends"] == 1
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
