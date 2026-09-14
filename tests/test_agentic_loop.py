"""The multi-turn agentic loop (R1, R2, R4; AC1–AC7, AC11, AC12, AC24, AC25, AC46, AC47).

An admitted run iterates model turns: each turn offers the model a bounded
transcript and the *authorized* read actions of the run's destination, read
afresh per action and per declared scope; the model answers with one
proposal or a final response; a proposal becomes one executor call whose
observation — text and image parts alike — returns to the model; the final
response is delivered through the configured terminal step. Delivery is
never offered as a tool, a non-read proposal is refused by the runtime
itself, and every image the run leased is released when it ends, on every
exit path.

The harness runs the **shipped** ``modules/brain/`` on the versioned runtime
the shared fixture builds — the real executor, the real scheduler the
engine builds for itself, the real attachment store on the injected clock —
with the four capabilities bound in the test as read doubles and a fake
send edge, under the names and scopes the real modules declare (``chat``
for ``chat.read``/``users.read``, ``capture`` for ``screen.capture``); the
real modules join in a later step. Nothing here sleeps: the clock is
injected and every wait is a bounded number of bare loop turns.
"""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import pytest

from core.actions import (
    ERROR_NOT_AUTHORIZED,
    ActionRegistry,
    AuthorizationPolicy,
    AuthorizationRule,
)
from core.attachments import AttachmentStore
from core.bus import EventBus
from core.contracts import (
    BRAIN_ERROR_ATTACHMENT_EXPIRED,
    BRAIN_ERROR_MALFORMED_ARGUMENTS,
    BRAIN_ERROR_NOT_A_READ_ACTION,
    BRAIN_ERROR_OBSERVATION_TOO_LARGE,
    BRAIN_ERROR_UNKNOWN_ACTION,
    RUN_FAILURE_CAPABILITY_MISSING,
    RUN_FAILURE_UNSUPPORTED_RESPONSE_SHAPE,
    TRACE_ACTION_COMPLETED,
    TRACE_ACTION_STARTED,
    TRACE_BRAIN_RUN_COMPLETED,
    TRACE_BRAIN_RUN_STARTED,
    WILDCARD,
    ActionObservation,
    ActionSpec,
    Destination,
    SessionKey,
)
from core.runtime import RuntimeContext
from conftest import (
    FAIL_AFTER_EMISSION,
    FakeResponse,
    HeldSession,
    ManualClock,
    ScriptedModel,
    events_of,
    final,
    png_bytes,
    runtime_context as build_context,
    settle,
    text_and_tool_call,
    tool_call,
    tool_calls,
    wait_until,
)
from modules.brain import (
    DELIVERY_NOT_ATTEMPTED,
    FALLBACK_NONE,
    MODULE_NAME,
    PRINCIPAL,
    TRACE_OBSERVATION,
    activate,
)
from modules import audit
from test_brain import VALID_SETTINGS, chat_payload, merge_settings


PLATFORM = "twitch"
CHANNEL = "channel-1"
VIEWER = "viewer-4"
INPUT_EVENT = "channel.chat.message"

# The four capabilities of R5, under the names and runtime scopes the real
# modules declare; the brain holds none of these names (AC56).
CHAT_READ = "chat.read"
USERS_READ = "users.read"
SCREEN_CAPTURE = "screen.capture"
CHAT_WRITE = "chat.write"
CHAT_SCOPE = "chat"
CAPTURE_SCOPE = "capture"
READ_ACTIONS = (CHAT_READ, USERS_READ, SCREEN_CAPTURE)
SCOPE_OF = {
    CHAT_READ: CHAT_SCOPE,
    USERS_READ: CHAT_SCOPE,
    SCREEN_CAPTURE: CAPTURE_SCOPE,
    CHAT_WRITE: CHAT_SCOPE,
}
ALL_GRANTS = (CHAT_READ, USERS_READ, SCREEN_CAPTURE, CHAT_WRITE)

READER_MODULE = "reader"
CAPTURER_MODULE = "capturer"
SENDER_MODULE = "sender"

FINAL_TEXT = "Here is what I saw."
IMAGE_WIDTH = 2
IMAGE_HEIGHT = 2
PNG = png_bytes(IMAGE_WIDTH, IMAGE_HEIGHT)
STORE_LIMITS = {
    "max_object_bytes": 4 * 1024 * 1024,
    "max_objects": 16,
    "max_total_bytes": 16 * 1024 * 1024,
    "max_bytes_per_run": 8 * 1024 * 1024,
    "ttl_seconds": 30.0,
}
MAX_OBSERVATION_BYTES = VALID_SETTINGS["budget"]["max_observation_bytes"]
assert MAX_OBSERVATION_BYTES == 5_242_880


def _read_spec(name: str, *, scope: str, arguments: Mapping[str, Any]) -> ActionSpec:
    return ActionSpec(
        name=name,
        version=1,
        description=f"Read through {name}.",
        argument_schema={
            "type": "object",
            "properties": dict(arguments),
            "required": [],
            "additionalProperties": False,
        },
        result_schema={"type": "object"},
        nature="read",
        required_permissions=(name,),
        supported_destinations=(Destination(WILDCARD, WILDCARD, scope),),
        timeout_seconds=10.0,
        idempotency="none",
    )


CHAT_READ_SPEC = _read_spec(
    CHAT_READ, scope=CHAT_SCOPE, arguments={"limit": {"type": "integer", "minimum": 1, "maximum": 50}}
)
USERS_READ_SPEC = _read_spec(
    USERS_READ, scope=CHAT_SCOPE, arguments={"limit": {"type": "integer", "minimum": 1, "maximum": 100}}
)
SCREEN_CAPTURE_SPEC = _read_spec(
    SCREEN_CAPTURE, scope=CAPTURE_SCOPE, arguments={"source": {"type": "string"}}
)
CHAT_WRITE_SPEC = ActionSpec(
    name=CHAT_WRITE,
    version=1,
    description="Send one chat message to the channel.",
    argument_schema={
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
        "additionalProperties": False,
    },
    result_schema={"type": "object", "properties": {"message_id": {"type": "string"}}},
    nature="write",
    required_permissions=(CHAT_WRITE,),
    supported_destinations=(Destination(WILDCARD, WILDCARD, CHAT_SCOPE),),
    timeout_seconds=10.0,
    idempotency="key",
    delivery={"text_argument": "text"},
)

CHAT_MESSAGES = [
    {"author_id": "viewer-1", "message_id": "m-1", "text": "first observed line", "observed_at": 990.0},
    {"author_id": "viewer-2", "message_id": "m-2", "text": "second observed line", "observed_at": 995.0},
]


def grant(action: str, *, scope: str | None = None, rule_id: str | None = None) -> AuthorizationRule:
    """One explicit grant of *action* to the brain's principal on *scope* (R5)."""

    return AuthorizationRule(
        rule_id=rule_id or f"brain-{action}",
        action_name=action,
        destination=Destination(WILDCARD, WILDCARD, scope or SCOPE_OF[action]),
        principals=(PRINCIPAL,),
        granted_permissions=(action,),
    )


# --------------------------------------------------------------------------- #
# The read doubles behind the executor, and the fake send edge
# --------------------------------------------------------------------------- #


class ScriptedReadProvider:
    """A read provider answering each call with the next scripted result.

    A result is an :class:`~core.contracts.ActionObservation`, a callable
    building one from the invocation, or an exception to raise; with nothing
    scripted the provider answers a success carrying the chat lines as text.
    ``hold`` (a future the test resolves) keeps the next call pending.
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self.results: list[Any] = []
        self.calls: list[Any] = []
        self.hold: asyncio.Future[Any] | None = None
        self.entered = asyncio.Event()

    async def invoke(self, invocation: Any) -> ActionObservation:
        self.calls.append(invocation.call)
        invocation.mark_not_emitted()
        self.entered.set()
        if self.hold is not None:
            await self.hold
        if not self.results:
            return chat_read_observation(self.name)
        result = self.results.pop(0)
        if callable(result):
            result = result(invocation)
        if isinstance(result, BaseException):
            raise result
        return result


def chat_read_observation(provider: str, messages: Sequence[Mapping[str, Any]] = CHAT_MESSAGES) -> ActionObservation:
    rendered = "\n".join(f"{line['author_id']}: {line['text']}" for line in messages)
    return ActionObservation(
        status="success",
        provenance={"provider": provider},
        result={"messages": [dict(line) for line in messages], "observed_at": 1000.0},
        parts=[{"type": "text", "text": rendered}],
    )


class CaptureProvider:
    """A ``screen.capture`` provider leasing one image into the run's store.

    The bytes are leased under the calling run's ``run_id`` — or under
    ``lease_run`` when a test wants a foreign lease — **before** the
    optional ``hold``, so a run cancelled or timed out mid-capture holds a
    lease its end must release. ``text`` adds a text part beside the image
    and ``declared_size`` lets a test misstate the stored size.
    """

    name = "fake-capture"

    def __init__(self, store: AttachmentStore, clock: ManualClock, data: bytes = PNG) -> None:
        self.store = store
        self.clock = clock
        self.data = data
        self.calls: list[Any] = []
        self.refs: list[Any] = []
        self.hold: asyncio.Future[Any] | None = None
        self.entered = asyncio.Event()
        self.lease_run: str | None = None
        self.text: str | None = None
        self.declared_size: int | None = None

    async def invoke(self, invocation: Any) -> ActionObservation:
        call = invocation.call
        self.calls.append(call)
        invocation.mark_not_emitted()
        ref = self.store.put(self.lease_run or call.run_id, self.data, content_type="image/png")
        self.refs.append(ref)
        self.entered.set()
        if self.hold is not None:
            await self.hold
        captured_at = self.clock()
        parts: list[dict[str, Any]] = []
        if self.text is not None:
            parts.append({"type": "text", "text": self.text})
        parts.append(
            {
                "type": "image_ref",
                "attachment_id": ref.attachment_id,
                "content_type": "image/png",
                "size": self.declared_size if self.declared_size is not None else ref.size,
                "width": IMAGE_WIDTH,
                "height": IMAGE_HEIGHT,
                "captured_at": captured_at,
                "provider_id": self.name,
            }
        )
        return ActionObservation(
            status="success",
            provenance={"provider": self.name},
            result={
                "source": "primary",
                "content_type": "image/png",
                "width": IMAGE_WIDTH,
                "height": IMAGE_HEIGHT,
                "size": len(self.data),
                "captured_at": captured_at,
            },
            parts=parts,
        )


class SendProvider:
    """The ``chat.write`` edge: records what left, scriptable, holdable."""

    name = "fake-send"

    def __init__(self) -> None:
        self.sends: list[dict[str, Any]] = []
        self.outcomes: list[str] = []
        self.hold: asyncio.Future[Any] | None = None
        self.entered = asyncio.Event()
        self.invocations = 0

    async def invoke(self, invocation: Any) -> ActionObservation:
        self.invocations += 1
        invocation.mark_not_emitted()
        self.entered.set()
        if self.hold is not None:
            await self.hold
        outcome = self.outcomes.pop(0) if self.outcomes else "sent"
        invocation.mark_emitted()
        if outcome == FAIL_AFTER_EMISSION:
            raise RuntimeError("no confirmation")
        call = invocation.call
        self.sends.append(
            {
                "text": call.arguments["text"],
                "call_id": call.call_id,
                "run_id": call.run_id,
                "principal": call.principal,
                "destination": str(call.destination),
            }
        )
        return ActionObservation(
            status="success",
            provenance={"provider": self.name},
            result={"message_id": f"sent-{len(self.sends)}"},
        )


class SnapshotModel(ScriptedModel):
    """A scripted model that snapshots the edges at every scenario request.

    ``on_request(body)`` is called before each request is answered, so a
    test can record how many sends had left or how many objects the store
    held at the instant the model was asked — what the model *could* have
    seen, not what the run ended with.
    """

    def __init__(self, *bodies: Any) -> None:
        super().__init__(*bodies)
        self.on_request: Callable[[dict[str, Any]], None] | None = None

    async def _answer(self, url: str, kwargs: dict[str, Any]) -> Any:
        if self.on_request is not None:
            self.on_request(kwargs["json"])
        return await super()._answer(url, kwargs)


# --------------------------------------------------------------------------- #
# The harness
# --------------------------------------------------------------------------- #


@dataclass
class LoopHarness:
    """The activated engine, its store and every edge a scenario reads."""

    handle: Any
    context: RuntimeContext
    clock: ManualClock
    store: AttachmentStore
    session: ScriptedModel
    reader: ScriptedReadProvider
    users: ScriptedReadProvider
    capture: CaptureProvider
    sender: SendProvider
    policy: AuthorizationPolicy
    diagnostics: list[str]
    tasks_before: int
    audit_lines: list[str] = field(default_factory=list)
    audit_handle: Any = None

    @property
    def bus(self) -> EventBus:
        return self.context.bus

    @property
    def executor(self) -> Any:
        return self.context.executor

    async def send(self, text: str = "What is happening on stream?", *, message_id: str = "message-7") -> None:
        await self.bus.publish(
            INPUT_EVENT,
            chat_payload(text, message_id=message_id, viewer_id=VIEWER, channel_id=CHANNEL, platform=PLATFORM),
            {"source": "test", "schema_version": 2},
        )

    async def completed(self, count: int = 1) -> Any:
        """Wait, in bare loop turns, for *count* terminal records; the last one."""

        await wait_until(lambda: len(self.handle.scheduler.run_records()) >= count)
        await wait_until(lambda: len(events_of(self.bus, TRACE_BRAIN_RUN_COMPLETED)) >= count)
        return list(self.handle.scheduler.run_records().values())[-1]

    async def ask(self, text: str = "What is happening on stream?", *, message_id: str = "message-7") -> Any:
        before = len(self.handle.scheduler.run_records())
        await self.send(text, message_id=message_id)
        return await self.completed(before + 1)

    def requests(self) -> list[dict[str, Any]]:
        return self.session.requests()

    def tools(self, index: int) -> list[str]:
        return [tool["function"]["name"] for tool in self.requests()[index].get("tools", [])]

    def traces(self, event_type: str) -> list[dict[str, Any]]:
        return events_of(self.bus, event_type)

    def run_completed(self) -> dict[str, Any]:
        return self.traces(TRACE_BRAIN_RUN_COMPLETED)[-1]["payload"]

    def executor_calls(self) -> list[tuple[str, str]]:
        """Every executor call as ``(action, call_id)``, in invocation order."""

        return [
            (event["payload"]["action"], event["payload"]["call_id"])
            for event in self.traces(TRACE_ACTION_STARTED)
        ]

    def observations(self) -> list[dict[str, Any]]:
        return [event["payload"] for event in self.traces(TRACE_OBSERVATION)]

    def memory(self) -> list[tuple[str, str]]:
        key = SessionKey(platform=PLATFORM, channel_id=CHANNEL, viewer_id=VIEWER)
        return [(exchange.user, exchange.assistant) for exchange in self.handle.memory.recall(key)]

    async def close(self) -> None:
        await self.handle.close()
        if self.audit_handle is not None:
            await self.audit_handle.flush(1.0)
            await self.audit_handle.close()


async def activate_loop(
    *bodies: Any,
    grants: Sequence[str | AuthorizationRule] = ALL_GRANTS,
    settings_overrides: dict[str, Any] | None = None,
    session: ScriptedModel | None = None,
    store_limits: Mapping[str, Any] | None = None,
    with_audit: bool = False,
) -> LoopHarness:
    """Bind the four capabilities, activate and prepare the shipped brain.

    *grants* are action names (granted on the scope the real module declares
    for that action) or explicit rules; the store and the executor share
    one :class:`AttachmentStore` on the injected clock through the shared
    fixture; the brain builds and owns its real scheduler.
    """

    clock = ManualClock()
    store = AttachmentStore(clock=clock, **(dict(store_limits) if store_limits else STORE_LIMITS))
    policy = AuthorizationPolicy(
        [rule if isinstance(rule, AuthorizationRule) else grant(rule) for rule in grants]
    )
    registry = ActionRegistry(authorization=policy)
    reader = ScriptedReadProvider("fake-chat-read")
    users = ScriptedReadProvider("fake-users-read")
    capture = CaptureProvider(store, clock)
    sender = SendProvider()
    registry.register(CHAT_READ_SPEC, reader, module=READER_MODULE)
    registry.register(USERS_READ_SPEC, users, module=READER_MODULE)
    registry.register(SCREEN_CAPTURE_SPEC, capture, module=CAPTURER_MODULE)
    registry.register(CHAT_WRITE_SPEC, sender, module=SENDER_MODULE)
    for module in (READER_MODULE, CAPTURER_MODULE, SENDER_MODULE):
        registry.mark_ready(module)
    context = build_context(clock=clock, authorization=policy, actions=registry, attachments=store)

    audit_lines: list[str] = []
    audit_handle = None
    if with_audit:
        audit_handle = await audit.activate(
            context.for_module(audit.MODULE_NAME),
            {
                "output": "stdout",
                "queue": {"max_records": 1000, "max_bytes": 4 * 1024 * 1024},
                "_writer": audit_lines.append,
            },
            {},
        )
        await audit_handle.prepare()

    target_session = session if session is not None else ScriptedModel(*bodies)
    diagnostics: list[str] = []
    settings = merge_settings(settings_overrides)
    settings.update(
        {
            "_session_factory": lambda: target_session,
            "_sleeper": clock.sleep,
            "diagnostic_reporter": diagnostics.append,
        }
    )
    tasks_before = context.tasks.active
    handle = await activate(context.for_module(MODULE_NAME), settings, {})
    await handle.prepare()
    return LoopHarness(
        handle=handle,
        context=context,
        clock=clock,
        store=store,
        session=target_session,
        reader=reader,
        users=users,
        capture=capture,
        sender=sender,
        policy=policy,
        diagnostics=diagnostics,
        tasks_before=tasks_before,
        audit_lines=audit_lines,
        audit_handle=audit_handle,
    )


def image_parts(body: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Every ``image_url`` part of a request body, over all its messages."""

    parts: list[dict[str, Any]] = []
    for message in body["messages"]:
        content = message.get("content")
        if isinstance(content, list):
            parts.extend(part for part in content if part.get("type") == "image_url")
    return parts


def tool_messages(body: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [message for message in body["messages"] if message.get("role") == "tool"]


def observation_envelope(message: Mapping[str, Any]) -> dict[str, Any]:
    """The JSON envelope a tool message carries: status, error code, result."""

    content = message["content"]
    text = content if isinstance(content, str) else content[0]["text"]
    return json.loads(text.split("\n", 1)[0])


def message_text(message: Mapping[str, Any]) -> str:
    content = message["content"]
    if isinstance(content, str):
        return content
    return "\n".join(part.get("text", "") for part in content if part.get("type") == "text")


AC1_SCRIPT = (
    tool_call(CHAT_READ, {"limit": 5}),
    tool_call(SCREEN_CAPTURE, {}),
    final(FINAL_TEXT),
)


# --------------------------------------------------------------------------- #
# AC1, AC2, AC6, AC12, AC49: the reference scenario
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_ac1_one_message_drives_three_executor_calls_and_one_send_in_order() -> None:
    """AC1 (R1): with grants for the four actions and a model answering
    [``chat.read`` proposal, ``screen.capture`` proposal, final], one
    admitted message yields exactly 3 executor calls ``call-1``, ``call-2``,
    ``call-3`` in that order, exactly 1 send — the final text, made as
    ``call-3`` — and 0 sends before it; ``brain.run.completed`` carries
    ``status: success``, ``turns: 3``, ``action_calls: 3`` and the AC49
    ``deliveries`` entry; every trace of the run shares one ``run_id`` and
    one ``conversation_id``."""

    session = SnapshotModel(*AC1_SCRIPT)
    harness = await activate_loop(session=session)
    sends_at_request: list[int] = []
    session.on_request = lambda body: sends_at_request.append(len(harness.sender.sends))
    try:
        record = await harness.ask()
        run_id = record.run_id

        assert record.status == "success"
        assert harness.executor_calls() == [
            (CHAT_READ, f"{run_id}/call-1"),
            (SCREEN_CAPTURE, f"{run_id}/call-2"),
            (CHAT_WRITE, f"{run_id}/call-3"),
        ]
        assert len(harness.reader.calls) == 1 and len(harness.capture.calls) == 1
        assert harness.reader.calls[0].call_id == f"{run_id}/call-1"
        assert harness.reader.calls[0].principal == PRINCIPAL
        assert str(harness.reader.calls[0].destination) == f"{PLATFORM}/{CHANNEL}/{CHAT_SCOPE}"
        assert str(harness.capture.calls[0].destination) == f"{PLATFORM}/{CHANNEL}/{CAPTURE_SCOPE}"
        assert [send["text"] for send in harness.sender.sends] == [FINAL_TEXT]
        assert harness.sender.sends[0]["call_id"] == f"{run_id}/call-3"
        assert sends_at_request == [0, 0, 0]
        assert len(harness.requests()) == 3

        completed = harness.run_completed()
        assert completed["status"] == "success"
        assert completed["turns"] == 3
        assert completed["action_calls"] == 3
        assert completed["model_calls"] == 3
        assert completed["sends"] == 1
        assert completed["delivery"] == "success"
        assert completed["fallback"] == FALLBACK_NONE
        assert completed["deliveries"] == [
            {"action": CHAT_WRITE, "call_id": f"{run_id}/call-3", "text": True, "status": "success"}
        ]
        assert completed["tokens_estimated"] is True and completed["tokens"] > 0

        # Every trace of the run — the scheduler's lifecycle pair, the
        # executor's per-call pair, the loop's observations — shares the
        # run's identity and its conversation (the admission trace carries
        # the run id before a conversation exists).
        correlated = [
            event for event in harness.bus.list_events() if "run_id" in event["payload"]
        ]
        assert {event["payload"]["run_id"] for event in correlated} == {run_id}
        conversations = {
            event["payload"]["conversation_id"]
            for event in correlated
            if "conversation_id" in event["payload"]
        }
        assert len(conversations) == 1
        assert {event["type"] for event in correlated if "conversation_id" in event["payload"]} >= {
            TRACE_BRAIN_RUN_STARTED,
            TRACE_BRAIN_RUN_COMPLETED,
            TRACE_ACTION_STARTED,
            TRACE_ACTION_COMPLETED,
            TRACE_OBSERVATION,
        }
        assert harness.diagnostics == []
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_ac2_observations_return_to_the_model_as_text_then_as_one_image() -> None:
    """AC2 (R1): the second request carries the ``chat.read`` observation's
    messages as text in a ``tool`` message answering the proposal; the third
    carries exactly one image input derived from the capture — the stored
    PNG, encoded at request time — and the store holds 0 objects for the
    run after ``brain.run.completed``."""

    harness = await activate_loop(*AC1_SCRIPT)
    try:
        record = await harness.ask()
        run_id = record.run_id
        first, second, third = harness.requests()

        assert tool_messages(first) == [] and image_parts(first) == []
        (result,) = tool_messages(second)
        assert observation_envelope(result)["status"] == "success"
        for line in CHAT_MESSAGES:
            assert line["text"] in message_text(result)
        assert image_parts(second) == []
        # The assistant turn that earned the observation precedes it.
        assistant = second["messages"][-2]
        assert assistant["role"] == "assistant"
        assert assistant["tool_calls"][0]["function"]["name"] == CHAT_READ
        assert assistant["tool_calls"][0]["id"] == result["tool_call_id"] == f"{run_id}/call-1"

        assert len(tool_messages(third)) == 2
        (image,) = image_parts(third)
        assert image["image_url"]["url"] == (
            f"data:image/png;base64,{base64.b64encode(PNG).decode('ascii')}"
        )
        # A ``tool`` message carries text only (Chat Completions schema): the
        # capture's tool result is a string, and the image follows it in a
        # ``user`` message that names the call it answers.
        capture_result = tool_messages(third)[1]
        assert isinstance(capture_result["content"], str)
        assert observation_envelope(capture_result)["status"] == "success"
        image_message = third["messages"][third["messages"].index(capture_result) + 1]
        assert image_message["role"] == "user"
        assert capture_result["tool_call_id"] in image_message["content"][0]["text"]
        assert image_message["content"][1] is image
        for message in third["messages"]:
            if message["role"] == "tool":
                assert isinstance(message["content"], str)
        assert harness.store.usage(run_id).objects == 0
        assert harness.store.object_count == 0
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_ac6_every_request_offers_exactly_the_authorized_read_actions_per_scope() -> None:
    """AC6 (R1): every request of AC1 lists as tools exactly the authorized
    read actions — ``chat.read``, ``users.read`` on scope ``chat``,
    ``screen.capture`` on scope ``capture`` — never ``chat.write``; the
    system instructions carry no ``[send:`` tag and no tool name. The
    offer is per action and per declared scope: dropping the ``capture``
    rule removes ``screen.capture`` alone, and a ``screen.capture`` rule on
    scope ``chat`` offers nothing, since its binding is ``*/*/capture``."""

    harness = await activate_loop(*AC1_SCRIPT)
    try:
        await harness.ask()
        assert len(harness.requests()) == 3
        for index in range(3):
            assert harness.tools(index) == [CHAT_READ, SCREEN_CAPTURE, USERS_READ]
            system = harness.requests()[index]["messages"][0]
            assert system["role"] == "system"
            assert "[send:" not in system["content"]
            for name in (*READ_ACTIONS, CHAT_WRITE):
                assert name not in system["content"]
    finally:
        await harness.close()

    without_capture = await activate_loop(final("ok"), grants=(CHAT_READ, USERS_READ, CHAT_WRITE))
    try:
        await without_capture.ask()
        assert without_capture.tools(0) == [CHAT_READ, USERS_READ]
    finally:
        await without_capture.close()

    misplaced = await activate_loop(
        final("ok"),
        grants=(CHAT_READ, USERS_READ, CHAT_WRITE, grant(SCREEN_CAPTURE, scope=CHAT_SCOPE)),
    )
    try:
        await misplaced.ask()
        assert misplaced.tools(0) == [CHAT_READ, USERS_READ]
    finally:
        await misplaced.close()


@pytest.mark.asyncio
async def test_ac12_image_bytes_live_in_the_request_body_and_nowhere_else() -> None:
    """AC12 (R2): the image bytes appear in exactly one place — the model
    request body. No bus event, no supervision trace and no audit record of
    the run carries a base64 payload or a filesystem path; the observation
    trace carries the ``attachment_id``, ``size``, ``width`` and ``height``
    of the image and no more."""

    harness = await activate_loop(*AC1_SCRIPT, with_audit=True)
    try:
        record = await harness.ask()
        await settle(50)
        encoded = base64.b64encode(PNG).decode("ascii")
        (third,) = [body for body in harness.requests() if image_parts(body)]
        assert encoded in json.dumps(third)

        rendered_events = json.dumps(harness.bus.list_events(), default=str)
        assert encoded not in rendered_events
        assert "base64" not in rendered_events
        assert "data:image" not in rendered_events
        assert "/tmp" not in rendered_events and ".png" not in rendered_events
        assert harness.audit_lines, "the audit stage recorded the run"
        rendered_audit = "\n".join(harness.audit_lines)
        assert encoded not in rendered_audit and "base64" not in rendered_audit

        capture_trace = [
            payload for payload in harness.observations() if payload["action"] == SCREEN_CAPTURE
        ]
        (payload,) = capture_trace
        (ref,) = harness.capture.refs
        assert payload["run_id"] == record.run_id
        assert payload["call_id"] == f"{record.run_id}/call-2"
        assert payload["parts"] == [
            {
                "type": "image_ref",
                "attachment_id": ref.attachment_id,
                "content_type": "image/png",
                "size": len(PNG),
                "width": IMAGE_WIDTH,
                "height": IMAGE_HEIGHT,
            }
        ]
        assert payload["observation_bytes"] == len(PNG)
    finally:
        await harness.close()


# --------------------------------------------------------------------------- #
# AC3, AC4, AC5, AC46: classification, refusals, synthetic observations
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "shape"),
    [
        (tool_calls([(CHAT_READ, {"limit": 1}), (USERS_READ, {"limit": 1})]), "multiple_tool_calls"),
        (text_and_tool_call("Let me look.", CHAT_READ, {"limit": 1}), "tool_call_with_text"),
    ],
)
async def test_ac3_an_unsupported_response_shape_ends_the_run_with_no_action(
    body: dict[str, Any], shape: str
) -> None:
    """AC3 (R1, decision 2): two tool calls, or a tool call with text, end
    the run ``error`` with ``failure: unsupported_response_shape`` and 0
    executor calls — no partial effect, nothing delivered."""

    harness = await activate_loop(body, final("never"))
    try:
        record = await harness.ask()
        assert record.status == "error"
        assert record.delivery == DELIVERY_NOT_ATTEMPTED
        assert harness.executor_calls() == []
        assert harness.executor.provider_invocations == 0
        assert harness.sender.sends == []
        assert len(harness.requests()) == 1
        completed = harness.run_completed()
        assert completed["failure"] == RUN_FAILURE_UNSUPPORTED_RESPONSE_SHAPE
        assert completed["shape"] == shape
        assert completed["turns"] == 1 and completed["action_calls"] == 0
        assert completed["deliveries"] == []
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_ac4_a_read_proposal_without_a_rule_is_refused_by_the_executor_and_the_run_goes_on() -> None:
    """AC4 (R1): a ``screen.capture`` proposal with no applicable rule is
    passed to the executor — default deny is the executor's — and returns a
    ``refused`` observation to the model, provider invoked 0 times; with a
    final response next the run ends ``success`` with ``turns: 2``."""

    harness = await activate_loop(
        tool_call(SCREEN_CAPTURE, {}), final(FINAL_TEXT), grants=(CHAT_READ, USERS_READ, CHAT_WRITE)
    )
    try:
        record = await harness.ask()
        run_id = record.run_id
        assert record.status == "success"
        assert harness.capture.calls == []
        assert harness.executor_calls() == [
            (SCREEN_CAPTURE, f"{run_id}/call-1"),
            (CHAT_WRITE, f"{run_id}/call-2"),
        ]
        refused = harness.executor.outcome(f"{run_id}/call-1")
        assert refused.status == "refused" and refused.error["code"] == ERROR_NOT_AUTHORIZED
        (result,) = tool_messages(harness.requests()[1])
        assert observation_envelope(result) == {"status": "refused", "error": {"code": ERROR_NOT_AUTHORIZED}}
        completed = harness.run_completed()
        assert (completed["turns"], completed["action_calls"]) == (2, 2)
        assert harness.sender.sends[0]["text"] == FINAL_TEXT
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_ac5_non_object_arguments_yield_a_synthetic_malformed_arguments_error() -> None:
    """AC5 (R1): a proposal whose arguments are the string ``"oops"`` yields
    one transcript observation with ``error.code == "malformed_arguments"``,
    0 executor calls, and counts as 1 turn."""

    harness = await activate_loop(tool_call(CHAT_READ, "oops"), final(FINAL_TEXT))
    try:
        record = await harness.ask()
        assert record.status == "success"
        (result,) = tool_messages(harness.requests()[1])
        assert observation_envelope(result) == {
            "status": "error",
            "error": {"code": BRAIN_ERROR_MALFORMED_ARGUMENTS},
        }
        assistant = harness.requests()[1]["messages"][-2]
        assert assistant["tool_calls"][0]["function"]["arguments"] == "oops"
        assert harness.executor_calls() == [(CHAT_WRITE, f"{record.run_id}/call-1")]
        assert harness.reader.calls == []
        completed = harness.run_completed()
        assert (completed["turns"], completed["action_calls"]) == (2, 1)
        synthetic = harness.observations()
        assert synthetic[0]["call_id"] is None
        assert synthetic[0]["error_code"] == BRAIN_ERROR_MALFORMED_ARGUMENTS
        assert synthetic[0]["turn"] == 1
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_ac46_a_write_proposal_is_refused_by_the_runtime_itself_whatever_the_grants() -> None:
    """AC46 (R1, decision 1): with a ``chat.write`` grant for the brain, a
    model proposing ``chat.write`` on turn 1 yields one transcript
    observation ``refused`` / ``not_a_read_action`` with 0 executor calls
    for it; the final response of turn 2 is then delivered as ``call-1`` —
    exactly 1 send — and the record reads ``success``, ``turns: 2``,
    ``action_calls: 1``. A proposal naming ``nope.action`` yields
    ``refused`` / ``unknown_action`` with 0 executor calls."""

    harness = await activate_loop(tool_call(CHAT_WRITE, {"text": "hi"}), final(FINAL_TEXT))
    try:
        record = await harness.ask()
        run_id = record.run_id
        assert record.status == "success"
        (result,) = tool_messages(harness.requests()[1])
        assert observation_envelope(result) == {
            "status": "refused",
            "error": {"code": BRAIN_ERROR_NOT_A_READ_ACTION},
        }
        assert harness.executor_calls() == [(CHAT_WRITE, f"{run_id}/call-1")]
        assert [send["text"] for send in harness.sender.sends] == [FINAL_TEXT]
        assert harness.sender.sends[0]["call_id"] == f"{run_id}/call-1"
        assert harness.sender.invocations == 1
        completed = harness.run_completed()
        assert completed["status"] == "success"
        assert (completed["turns"], completed["action_calls"]) == (2, 1)
        assert harness.observations()[0]["error_code"] == BRAIN_ERROR_NOT_A_READ_ACTION
    finally:
        await harness.close()

    unknown = await activate_loop(tool_call("nope.action", {}), final(FINAL_TEXT))
    try:
        record = await unknown.ask()
        assert record.status == "success"
        (result,) = tool_messages(unknown.requests()[1])
        assert observation_envelope(result) == {
            "status": "refused",
            "error": {"code": BRAIN_ERROR_UNKNOWN_ACTION},
        }
        assert unknown.executor_calls() == [(CHAT_WRITE, f"{record.run_id}/call-1")]
        assert unknown.executor.provider_invocations == 1  # the delivery only
    finally:
        await unknown.close()


# --------------------------------------------------------------------------- #
# AC7: memory holds the final text, and only a confirmed one
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_ac7_only_the_confirmed_final_text_reaches_conversation_memory() -> None:
    """AC7 (R1): only the final response text is written to memory — no
    observation, no proposal — and only for a ``success`` delivery; a
    ``FAIL_AFTER_EMISSION`` delivery leaves memory unchanged and the record
    reads ``delivery == "external_unknown"``."""

    harness = await activate_loop(*AC1_SCRIPT)
    try:
        await harness.ask()
        ((user, assistant),) = harness.memory()
        assert assistant == FINAL_TEXT
        assert "What is happening on stream?" in user
        for line in CHAT_MESSAGES:
            assert line["text"] not in user and line["text"] not in assistant
    finally:
        await harness.close()

    uncertain = await activate_loop(*AC1_SCRIPT)
    uncertain.sender.outcomes.append(FAIL_AFTER_EMISSION)
    try:
        record = await uncertain.ask()
        assert record.delivery == "external_unknown"
        assert uncertain.run_completed()["delivery"] == "external_unknown"
        assert uncertain.memory() == []
        assert uncertain.sender.sends == []
    finally:
        await uncertain.close()


# --------------------------------------------------------------------------- #
# AC11, AC24, AC47: images and the run's capabilities and leases
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_ac11_an_image_observation_without_verified_vision_ends_the_run() -> None:
    """AC11 (R2): with ``capabilities.required: [structured_output]`` only,
    an ``image_ref`` observation ends the run ``error`` with ``failure:
    capability_missing``, ``capability: vision``, 0 further model requests,
    and the attachment released — the image is never dropped silently."""

    harness = await activate_loop(
        tool_call(SCREEN_CAPTURE, {}),
        final("never"),
        settings_overrides={"capabilities": {"required": ["structured_output"]}},
    )
    try:
        assert harness.handle.verified_capabilities == frozenset({"structured_output"})
        record = await harness.ask()
        assert record.status == "error"
        assert record.delivery == DELIVERY_NOT_ATTEMPTED
        assert len(harness.requests()) == 1
        assert len(harness.capture.calls) == 1
        completed = harness.run_completed()
        assert completed["failure"] == RUN_FAILURE_CAPABILITY_MISSING
        assert completed["capability"] == "vision"
        assert (completed["turns"], completed["action_calls"]) == (1, 1)
        assert harness.store.usage(record.run_id).objects == 0
        assert harness.store.object_count == 0
        assert harness.sender.sends == []
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_ac24_an_image_whose_lease_expired_before_its_model_turn_ends_the_run() -> None:
    """AC24 (R4): an ``image_ref`` leased at ``t`` and used by a model turn at
    ``t + ttl_seconds + 1`` — the clock advanced once the observation was
    adopted, before the request is built — ends the run ``error`` with
    ``failure: attachment_expired`` and 0 model requests carrying the image."""

    harness = await activate_loop(tool_call(SCREEN_CAPTURE, {}), final("never"))

    def expire(event: Mapping[str, Any]) -> None:
        if event["payload"].get("action") == SCREEN_CAPTURE:
            harness.clock.advance(STORE_LIMITS["ttl_seconds"] + 1.0)

    harness.bus.subscribe(TRACE_OBSERVATION, expire)
    try:
        record = await harness.ask()
        assert record.status == "error"
        assert len(harness.requests()) == 1
        assert image_parts(harness.requests()[0]) == []
        completed = harness.run_completed()
        assert completed["failure"] == BRAIN_ERROR_ATTACHMENT_EXPIRED
        assert harness.store.usage(record.run_id).objects == 0
        assert harness.sender.sends == []
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_ac47_the_observation_byte_budget_is_enforced_by_the_brain_with_the_images_released() -> None:
    """AC47 (R4), model side: with ``max_observation_bytes: 5242880`` an
    observation carrying one text part of 3 145 728 bytes and one image of
    ``size`` 3 145 728 — 6 291 456 in total, each alone under the bound —
    becomes ``error observation_too_large`` although the executor's own
    bound is ``None``; the store holds 0 objects for the run before the
    next model request, which carries 0 image parts. The same two parts
    with the image at 2 097 152 bytes (5 242 880 in total) are accepted and
    the next request carries exactly 1 image part."""

    text = "t" * 3_145_728
    tokens = {"budget": {"max_tokens": 4_000_000}}

    session = SnapshotModel(tool_call(SCREEN_CAPTURE, {}), final(FINAL_TEXT))
    over = await activate_loop(session=session, settings_overrides=tokens)
    objects_at_request: list[int] = []
    session.on_request = lambda body: objects_at_request.append(over.store.object_count)
    over.capture.data = b"\x89" * 3_145_728
    over.capture.text = text
    try:
        record = await over.ask()
        assert record.status == "success"
        assert objects_at_request == [0, 0]
        assert image_parts(over.requests()[1]) == []
        (result,) = tool_messages(over.requests()[1])
        assert observation_envelope(result) == {
            "status": "error",
            "error": {"code": BRAIN_ERROR_OBSERVATION_TOO_LARGE},
        }
        assert text[:64] not in message_text(result)
        assert over.executor.outcome(f"{record.run_id}/call-1").status == "success"
        assert over.observations()[0]["error_code"] == BRAIN_ERROR_OBSERVATION_TOO_LARGE
        assert over.observations()[0]["parts"] == []
    finally:
        await over.close()

    session = SnapshotModel(tool_call(SCREEN_CAPTURE, {}), final(FINAL_TEXT))
    exact = await activate_loop(session=session, settings_overrides=tokens)
    objects_at_request = []
    session.on_request = lambda body: objects_at_request.append(exact.store.object_count)
    exact.capture.data = b"\x89" * 2_097_152
    exact.capture.text = text
    try:
        record = await exact.ask()
        assert record.status == "success"
        assert objects_at_request == [0, 1]
        assert len(image_parts(exact.requests()[1])) == 1
        assert exact.observations()[0]["observation_bytes"] == 5_242_880
        assert exact.store.usage(record.run_id).objects == 0
    finally:
        await exact.close()


# --------------------------------------------------------------------------- #
# AC25: every terminal path releases the run's images
# --------------------------------------------------------------------------- #


async def _run_with_image_then(harness: LoopHarness, *, hold: str) -> str:
    """Drive one message; return the run id once the edge *hold* names is held."""

    await harness.send()
    if hold == "capture":
        await asyncio.wait_for(harness.capture.entered.wait(), 1)
    else:
        await asyncio.wait_for(harness.sender.entered.wait(), 1)
    await settle()
    (started,) = harness.traces(TRACE_BRAIN_RUN_STARTED)
    return started["payload"]["run_id"]


@pytest.mark.asyncio
async def test_ac25_success_error_and_timeout_release_the_images_and_the_tasks() -> None:
    """AC25 (R4): success, error and timeout — for each, the store reports 0
    objects for the run once ``brain.run.completed`` is published, and the
    supervised task count is back to its pre-run value."""

    success = await activate_loop(*AC1_SCRIPT)
    try:
        record = await success.ask()
        assert record.status == "success"
        assert success.store.usage(record.run_id).objects == 0
        assert success.store.object_count == 0
        assert success.context.tasks.active == success.tasks_before
    finally:
        await success.close()

    error = await activate_loop(
        tool_call(SCREEN_CAPTURE, {}),
        tool_calls([(CHAT_READ, {"limit": 1}), (USERS_READ, {"limit": 1})]),
    )
    try:
        record = await error.ask()
        assert record.status == "error"
        assert len(error.capture.refs) == 1
        assert error.store.usage(record.run_id).objects == 0
        assert error.store.object_count == 0
        assert error.context.tasks.active == error.tasks_before
    finally:
        await error.close()

    held = HeldSession(
        FakeResponse(200, tool_call(SCREEN_CAPTURE, {})), FakeResponse(200, final("late"))
    )
    timeout = await activate_loop(session=held)  # type: ignore[arg-type]
    try:
        await timeout.send()
        await asyncio.wait_for(held.entered.wait(), 1)
        held.entered.clear()
        held.release.set()
        await wait_until(lambda: len(timeout.capture.refs) == 1)
        held.release.clear()
        await asyncio.wait_for(held.entered.wait(), 1)
        assert timeout.store.object_count == 1
        timeout.clock.advance(31.0)
        record = await timeout.completed()
        assert record.status == "timeout"
        assert timeout.store.usage(record.run_id).objects == 0
        assert timeout.store.object_count == 0
        assert timeout.context.tasks.active == timeout.tasks_before
        held.release.set()
    finally:
        await timeout.close()


@pytest.mark.asyncio
async def test_ac25_cancellation_during_a_capture_a_model_call_or_a_delivery_releases_the_images() -> None:
    """AC25 (R4): cancellation during a capture, during the model call and
    during delivery — the shutdown ends the run ``cancelled`` with its one
    ``brain.run.completed``, the store reports 0 objects for the run once
    it is published, the supervised task count is back to baseline, and
    the cancellation propagated through the loop rather than being
    swallowed by it."""

    capture = await activate_loop(tool_call(SCREEN_CAPTURE, {}), final("never"))
    capture.capture.hold = asyncio.get_running_loop().create_future()
    run_id = await _run_with_image_then(capture, hold="capture")
    assert capture.store.usage(run_id).objects == 1
    await capture.close()
    record = capture.handle.scheduler.run_records()[run_id]
    assert record.status == "cancelled"
    assert capture.run_completed()["status"] == "cancelled"
    assert capture.store.usage(run_id).objects == 0 and capture.store.object_count == 0
    assert capture.context.tasks.active == capture.tasks_before
    assert capture.executor.outcome(f"{run_id}/call-1").status == "cancelled"

    held = HeldSession(
        FakeResponse(200, tool_call(SCREEN_CAPTURE, {})), FakeResponse(200, final("never"))
    )
    model = await activate_loop(session=held)  # type: ignore[arg-type]
    await model.send()
    await asyncio.wait_for(held.entered.wait(), 1)
    held.entered.clear()
    held.release.set()
    await wait_until(lambda: len(model.capture.refs) == 1)
    held.release.clear()
    await asyncio.wait_for(held.entered.wait(), 1)
    (started,) = model.traces(TRACE_BRAIN_RUN_STARTED)
    run_id = started["payload"]["run_id"]
    assert model.store.usage(run_id).objects == 1
    await model.close()
    assert model.handle.scheduler.run_records()[run_id].status == "cancelled"
    assert model.run_completed()["status"] == "cancelled"
    assert model.store.usage(run_id).objects == 0 and model.store.object_count == 0
    assert model.context.tasks.active == model.tasks_before

    delivery = await activate_loop(tool_call(SCREEN_CAPTURE, {}), final(FINAL_TEXT))
    delivery.sender.hold = asyncio.get_running_loop().create_future()
    run_id = await _run_with_image_then(delivery, hold="delivery")
    assert delivery.store.usage(run_id).objects == 1
    await delivery.close()
    assert delivery.handle.scheduler.run_records()[run_id].status == "cancelled"
    assert delivery.run_completed()["status"] == "cancelled"
    assert delivery.store.usage(run_id).objects == 0 and delivery.store.object_count == 0
    assert delivery.context.tasks.active == delivery.tasks_before
    assert delivery.sender.sends == []
    assert delivery.memory() == []
