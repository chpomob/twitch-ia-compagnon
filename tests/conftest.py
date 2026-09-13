"""The one shared runtime-context fixture (P19).

Every suite that activates a module on the versioned runtime builds the
context through :func:`runtime_context` here rather than assembling its own
(``test_twitch``, ``test_brain``, ``test_audit`` today; the retention,
lifecycle and integration suites next). The builder wires the **real**
:class:`~core.actions.ActionExecutor`, the real
:class:`~core.admission.AdmissionScheduler` the brain engine builds onto it
and the real :class:`~core.runtime.Supervision`; only the edges the process
cannot own are fakes — the model transport (:class:`FakeSession`, with the
Chat Completions body builders :func:`tool_call`, :func:`tool_calls`,
:func:`text_and_tool_call`, :func:`final`/:func:`completion` and the
:class:`ScriptedModel` shorthand), the send edge behind ``chat.write``
(:class:`FakeTransport` behind :class:`FakeSendProvider`), the screen
behind ``screen.capture`` (:class:`FakeCaptureSource`, :func:`png_bytes`),
the wire between brain and agent (:class:`MemoryWebSocketPair`), time itself
(:class:`ManualClock`) and randomness (``rng``). A suite that mocked the
executor would hide AC19's executor-confirmed delivery, so the executor is
never mocked here.

The model transport tells a **probe** request (R2: ``tool_choice`` forcing
:data:`core.contracts.PROBE_TOOL`) from a scenario request by its body, never
by call order: probes are answered from ``probe_results`` and recorded in
``probe_calls``, scenario requests consume ``results`` and land in
``post_calls``, so a per-run request count keeps its meaning whether or not
the module under test probes at ``prepare``, and a suite that never prepares
the brain sees no probe at all. The harness imports nothing from
``modules.brain``: the probe tool name is the shared contract constant.

A module's own declarations — its trigger registration, its action bindings
and grants — stay in its suite; this module owns only the assembly every
context shares: bus, counters, supervision, tasks, executor and clock, with
one counter registry shared by all of them so a snapshot is readable
regardless of which component incremented (R8, AC28).

This file is a single point of failure for every suite that imports it; a
change here must be reviewed against each consumer, not just the suite that
motivated it (P24).
"""

from __future__ import annotations

import asyncio
import json
import random
import struct
import zlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import IntEnum
from types import SimpleNamespace
from typing import Any

from core.actions import (
    ERROR_TIMED_OUT,
    ActionExecutor,
    ActionRegistry,
    AuthorizationPolicy,
)
from core.attachments import AttachmentStore
from core.bus import EventBus
from core.contracts import PROBE_TOOL, ActionObservation, Counters, SessionKey
from core.lifecycle import SupervisedTasks
from core.runtime import RuntimeContext, Supervision
from core.triggers import TriggerEngine


# --------------------------------------------------------------------------- #
# The fake clock: time moves only when a test says so
# --------------------------------------------------------------------------- #


class ManualClock:
    """A monotonic clock nobody waits on: time only moves when a test says so.

    ``sleep`` is the sleeper injected into a scheduler the engine owns; it
    parks a future until :meth:`advance` brings the clock past it, so no
    deadline ever fires because a test waited.
    """

    def __init__(self, now: float = 1000.0) -> None:
        self.now = now
        self._waiters: list[tuple[float, asyncio.Future[None]]] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, delay: float) -> None:
        if delay <= 0:
            await asyncio.sleep(0)
            return
        waiter: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        entry = (self.now + delay, waiter)
        self._waiters.append(entry)
        try:
            await waiter
        except asyncio.CancelledError:
            self._waiters = [item for item in self._waiters if item is not entry]
            raise

    def advance(self, delta: float) -> None:
        self.now += delta
        due = [item for item in self._waiters if item[0] <= self.now]
        self._waiters = [item for item in self._waiters if item[0] > self.now]
        for _deadline, waiter in due:
            if not waiter.done():
                waiter.set_result(None)


# --------------------------------------------------------------------------- #
# The fake model edge
# --------------------------------------------------------------------------- #


class FakeResponse:
    def __init__(self, status: int, body: Any) -> None:
        self.status = status
        self.body = body
        self.release_calls = 0

    async def json(self) -> Any:
        if isinstance(self.body, Exception):
            raise self.body
        return self.body

    def release(self) -> None:
        self.release_calls += 1


def is_probe_request(body: Any) -> bool:
    """Whether a request body is a capability probe (R2).

    A probe forces the runtime's probe tool through ``tool_choice``; that is
    the only thing that makes it one — not its position in the call
    sequence, so a suite that never prepares the brain is unaffected.
    """

    if not isinstance(body, Mapping):
        return False
    choice = body.get("tool_choice")
    if not isinstance(choice, Mapping):
        return False
    function = choice.get("function")
    return isinstance(function, Mapping) and function.get("name") == PROBE_TOOL


def carries_image_part(body: Any) -> bool:
    """Whether a request body carries at least one image part (R2, AC8)."""

    if not isinstance(body, Mapping):
        return False
    for message in body.get("messages", ()):
        content = message.get("content") if isinstance(message, Mapping) else None
        if not isinstance(content, list):
            continue
        for part in content:
            if isinstance(part, Mapping) and part.get("type") == "image_url":
                return True
    return False


def _raise_or_return(result: Any) -> Any:
    if isinstance(result, BaseException):
        raise result
    return result


class FakeSession:
    """The model transport: one prepared result per scenario request, in order.

    ``results`` answer scenario requests and are consumed one per request;
    ``probe_results`` answer probe requests (see :func:`is_probe_request`) and
    never touch ``results``. Without an explicit ``probe_results`` — or once
    the given ones are used up — every probe, the image probe included, is
    answered with one valid forced tool call, so a module that probes at
    ``prepare`` becomes ready without the suite scripting the probes.
    """

    def __init__(self, *results: Any, probe_results: Sequence[Any] | None = None) -> None:
        self.results = list(results)
        self.probe_results = list(probe_results) if probe_results is not None else []
        self.post_calls: list[dict[str, Any]] = []
        self.probe_calls: list[dict[str, Any]] = []
        self.close_calls = 0

    async def post(self, url: str, **kwargs: Any) -> Any:
        if is_probe_request(kwargs.get("json")):
            self.probe_calls.append({"url": url, **kwargs})
            return _raise_or_return(self._next_probe_result())
        return await self._answer(url, kwargs)

    async def _answer(self, url: str, kwargs: dict[str, Any]) -> Any:
        self.post_calls.append({"url": url, **kwargs})
        return _raise_or_return(self.results.pop(0))

    def _next_probe_result(self) -> Any:
        if self.probe_results:
            return self.probe_results.pop(0)
        return FakeResponse(200, tool_call(PROBE_TOOL, {"ok": True}))

    async def close(self) -> None:
        self.close_calls += 1


class HeldSession(FakeSession):
    """A model transport that holds every scenario request until released.

    Probes are answered at once: they belong to ``prepare``, and holding them
    would hold the readiness barrier, not the run a test wants to observe.
    """

    def __init__(self, *results: Any, probe_results: Sequence[Any] | None = None) -> None:
        super().__init__(*results, probe_results=probe_results)
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def _answer(self, url: str, kwargs: dict[str, Any]) -> Any:
        self.post_calls.append({"url": url, **kwargs})
        result = self.results.pop(0)
        self.entered.set()
        await self.release.wait()
        return _raise_or_return(result)


class ScriptedModel(FakeSession):
    """A model transport scripted with response bodies rather than responses.

    Each body becomes a 2xx :class:`FakeResponse`; a :class:`FakeResponse`
    or an exception passes through unchanged so a scenario can mix a
    non-2xx status or a transport failure into the script.
    """

    def __init__(self, *bodies: Any, probe_results: Sequence[Any] | None = None) -> None:
        super().__init__(
            *(
                body
                if isinstance(body, (FakeResponse, BaseException))
                else FakeResponse(200, body)
                for body in bodies
            ),
            probe_results=probe_results,
        )

    def requests(self) -> list[dict[str, Any]]:
        """The scenario request bodies, in order; probe bodies excluded."""

        return [call["json"] for call in self.post_calls]


# Chat Completions bodies the transport answers with (decision 2).


def _with_usage(body: dict[str, Any], usage: dict[str, int] | None) -> dict[str, Any]:
    if usage is not None:
        body["usage"] = usage
    return body


def _tool_call_entry(name: str, arguments: Any, index: int) -> dict[str, Any]:
    # A ``str`` is sent as given so a scenario can script arguments that do
    # not decode (``malformed_arguments``); anything else is JSON-encoded as
    # the backend would.
    encoded = arguments if isinstance(arguments, str) else json.dumps(arguments)
    return {
        "id": f"call-{index}",
        "type": "function",
        "function": {"name": name, "arguments": encoded},
    }


def tool_calls(
    calls: Sequence[tuple[str, Any] | Mapping[str, Any]],
    *,
    content: str | None = None,
    usage: dict[str, int] | None = None,
) -> dict[str, Any]:
    """A response carrying the given tool calls — ``(name, arguments)`` pairs
    or ``{"name", "arguments"}`` mappings — and, optionally, text."""

    entries = []
    for index, call in enumerate(calls, start=1):
        if isinstance(call, Mapping):
            entries.append(_tool_call_entry(call["name"], call["arguments"], index))
        else:
            name, arguments = call
            entries.append(_tool_call_entry(name, arguments, index))
    message: dict[str, Any] = {"role": "assistant", "content": content, "tool_calls": entries}
    return _with_usage(
        {"choices": [{"message": message, "finish_reason": "tool_calls"}]}, usage
    )


def tool_call(name: str, arguments: Any, *, usage: dict[str, int] | None = None) -> dict[str, Any]:
    """A response carrying exactly one tool call (a proposal, decision 2)."""

    return tool_calls([(name, arguments)], usage=usage)


def text_and_tool_call(
    text: str, name: str, arguments: Any, *, usage: dict[str, int] | None = None
) -> dict[str, Any]:
    """A response mixing text and a tool call (unsupported, decision 2)."""

    return tool_calls([(name, arguments)], content=text, usage=usage)


def final(text: str, usage: dict[str, int] | None = None) -> dict[str, Any]:
    """A final answer: text and no tool call (decision 2)."""

    return _with_usage({"choices": [{"message": {"content": text}}]}, usage)


completion = final


# --------------------------------------------------------------------------- #
# The fake send edge behind the real executor (AC19)
# --------------------------------------------------------------------------- #


# Outcomes the fake send edge can be told to produce for one delivery.
SENT = "sent"
FAIL_BEFORE_EMISSION = "fail_before_emission"
FAIL_AFTER_EMISSION = "fail_after_emission"
TIMEOUT_BEFORE_EMISSION = "timeout_before_emission"


class FakeTransport:
    """The send edge behind the real executor: what actually left (AC19)."""

    def __init__(self, *outcomes: str) -> None:
        self.outcomes = list(outcomes)
        self.sends: list[dict[str, Any]] = []


class FakeSendProvider:
    """A ``chat.write`` provider bound in the real registry over the fake edge."""

    name = "fake-send"

    def __init__(self, transport: FakeTransport) -> None:
        self._transport = transport

    async def invoke(self, invocation: Any) -> ActionObservation:
        invocation.mark_not_emitted()
        call = invocation.call
        outcome = self._transport.outcomes.pop(0) if self._transport.outcomes else SENT
        provenance = {"provider": self.name}
        if outcome == FAIL_BEFORE_EMISSION:
            raise RuntimeError("transport unavailable")
        if outcome == TIMEOUT_BEFORE_EMISSION:
            return ActionObservation(
                status="timeout",
                provenance=provenance,
                error={"code": ERROR_TIMED_OUT, "message": "", "retryable": False},
            )
        invocation.mark_emitted()
        if outcome == FAIL_AFTER_EMISSION:
            raise RuntimeError("no confirmation")
        self._transport.sends.append(
            {
                "text": call.arguments["text"],
                "destination": call.destination,
                "principal": call.principal,
                "run_id": call.run_id,
                "call_id": call.call_id,
                "conversation_id": call.conversation_id,
                "source_event_id": call.source_event_id,
                "message_id": call.message_id,
            }
        )
        return ActionObservation(
            status="success",
            provenance=provenance,
            result={"message_id": f"sent-{len(self._transport.sends)}"},
        )


# --------------------------------------------------------------------------- #
# The fake screen behind ``screen.capture`` (R5)
# --------------------------------------------------------------------------- #


def png_bytes(width: int, height: int) -> bytes:
    """A minimal valid PNG of ``width`` × ``height`` opaque black RGB pixels."""

    if width <= 0 or height <= 0:
        raise ValueError("png_bytes: width and height must be positive")

    def chunk(kind: bytes, payload: bytes) -> bytes:
        crc = zlib.crc32(kind + payload) & 0xFFFFFFFF
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", crc)

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    rows = (b"\x00" + b"\x00" * (3 * width)) * height
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


class FakeCaptureSource:
    """The screen behind a capture provider: hands out one prepared image.

    ``read`` returns ``data`` and records every call in ``calls``; a test sets
    ``fail_with`` to make the next read raise, or ``hold`` (a future the test
    resolves) to keep the read pending until it says so — the provider's
    timeout is exercised on the injected clock, never by waiting.
    ``content_type``, ``width`` and ``height`` describe the bytes so a test
    asserts the sniffed result against what it handed in.
    """

    def __init__(
        self,
        data: bytes | None = None,
        content_type: str = "image/png",
        width: int = 1,
        height: int = 1,
    ) -> None:
        self.data = data if data is not None else png_bytes(width, height)
        self.content_type = content_type
        self.width = width
        self.height = height
        self.fail_with: BaseException | None = None
        self.hold: asyncio.Future[Any] | None = None
        self.calls: list[dict[str, Any]] = []
        self.entered = asyncio.Event()

    async def read(self, *args: Any, **kwargs: Any) -> bytes:
        self.calls.append({"args": args, "kwargs": kwargs})
        self.entered.set()
        if self.hold is not None:
            await self.hold
        if self.fail_with is not None:
            raise self.fail_with
        return self.data

    __call__ = read

    def describe(self) -> dict[str, Any]:
        return {
            "content_type": self.content_type,
            "width": self.width,
            "height": self.height,
            "size": len(self.data),
        }


# --------------------------------------------------------------------------- #
# The in-memory wire between brain and agent (R6)
# --------------------------------------------------------------------------- #


class WSMsgType(IntEnum):
    """The message types an ``aiohttp`` WebSocket hands out, same values.

    ``IntEnum`` compares by value, so code that checks a received message
    against ``aiohttp.WSMsgType.TEXT`` sees the same answer under the pair.
    """

    TEXT = 1
    BINARY = 2
    CLOSE = 8
    PING = 9
    PONG = 10
    CLOSING = 256
    CLOSED = 257
    ERROR = 258


# The code a real transport reports when a connection ended without a close
# handshake (RFC 6455 §7.4.1); what ``drop`` leaves on both ends.
WS_ABNORMAL_CLOSURE = 1006

_WS_TERMINAL = frozenset({WSMsgType.CLOSE, WSMsgType.CLOSING, WSMsgType.CLOSED, WSMsgType.ERROR})


@dataclass(frozen=True)
class WSMessage:
    type: WSMsgType
    data: Any
    extra: Any = None

    def json(self) -> Any:
        return json.loads(self.data)


class MemoryWebSocket:
    """One end of :class:`MemoryWebSocketPair`.

    The minimal surface both ``aiohttp.web.WebSocketResponse`` and
    ``aiohttp.ClientWebSocketResponse`` expose — ``send_str``, ``send_bytes``,
    ``send_json``, ``receive`` (and the typed ``receive_*``), ``close``,
    ``closed``, ``close_code``, async iteration — so the proxy and agent code
    runs unchanged over it. Frames arrive in the order they were sent; a
    ``close`` reaches the peer as a ``CLOSE`` message carrying the code,
    after which the peer's ``close_code`` is that code and further receives
    return ``CLOSED``. ``sent`` keeps every frame this end wrote.
    """

    def __init__(self) -> None:
        self._inbox: asyncio.Queue[WSMessage] = asyncio.Queue()
        self._peer: MemoryWebSocket | None = None
        self.closed = False
        self.close_code: int | None = None
        self.sent: list[WSMessage] = []

    def _attach(self, peer: MemoryWebSocket) -> None:
        self._peer = peer

    def _deliver(self, message: WSMessage) -> None:
        if self._peer is None:
            raise RuntimeError("memory websocket: end is not paired")
        if self.closed or self._peer.closed:
            raise ConnectionResetError("Cannot write to closing transport")
        self.sent.append(message)
        self._peer._inbox.put_nowait(message)

    async def send_str(self, data: str) -> None:
        if not isinstance(data, str):
            raise TypeError(f"data argument must be str ({type(data)!r})")
        self._deliver(WSMessage(WSMsgType.TEXT, data))

    async def send_bytes(self, data: bytes) -> None:
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise TypeError(f"data argument must be byte-ish ({type(data)!r})")
        self._deliver(WSMessage(WSMsgType.BINARY, bytes(data)))

    async def send_json(self, data: Any) -> None:
        await self.send_str(json.dumps(data))

    async def receive(self, timeout: float | None = None) -> WSMessage:
        # ``timeout`` is accepted for signature parity only: a test drives
        # deadlines on the injected clock, never on a transport wait.
        if self.closed and self._inbox.empty():
            return WSMessage(WSMsgType.CLOSED, None)
        message = await self._inbox.get()
        if message.type is WSMsgType.CLOSE and not self.closed:
            self.closed = True
            self.close_code = message.data
        return message

    async def receive_str(self, timeout: float | None = None) -> str:
        message = await self.receive(timeout)
        if message.type is not WSMsgType.TEXT:
            raise TypeError(f"Received message {message.type}:{message.data!r} is not str")
        return message.data

    async def receive_bytes(self, timeout: float | None = None) -> bytes:
        message = await self.receive(timeout)
        if message.type is not WSMsgType.BINARY:
            raise TypeError(f"Received message {message.type}:{message.data!r} is not bytes")
        return message.data

    async def receive_json(self, timeout: float | None = None) -> Any:
        return json.loads(await self.receive_str(timeout))

    async def close(self, code: int = 1000, message: bytes = b"") -> bool:
        if self.closed:
            return False
        self.closed = True
        self.close_code = code
        if self._peer is not None and not self._peer.closed:
            self._peer._inbox.put_nowait(WSMessage(WSMsgType.CLOSE, code, message))
        # Wake a local receive() already parked on the inbox, as aiohttp's
        # close does, so a receive loop on this end terminates on shutdown.
        self._inbox.put_nowait(WSMessage(WSMsgType.CLOSED, None))
        return True

    def exception(self) -> BaseException | None:
        return None

    def __aiter__(self) -> MemoryWebSocket:
        return self

    async def __anext__(self) -> WSMessage:
        message = await self.receive()
        if message.type in _WS_TERMINAL:
            raise StopAsyncIteration
        return message


class MemoryWebSocketPair:
    """Two paired in-memory WebSocket ends: ``server`` (the brain's
    ``WebSocketResponse``) and ``client`` (the agent's
    ``ClientWebSocketResponse``). ``drop`` ends both abruptly, as a lost
    connection would: no close handshake, ``close_code`` 1006 on both ends,
    frames already queued still readable, then ``CLOSED``.
    """

    def __init__(self) -> None:
        self.server = MemoryWebSocket()
        self.client = MemoryWebSocket()
        self.server._attach(self.client)
        self.client._attach(self.server)

    @property
    def ends(self) -> tuple[MemoryWebSocket, MemoryWebSocket]:
        return self.server, self.client

    def drop(self) -> None:
        for end in self.ends:
            if not end.closed:
                end.closed = True
                end.close_code = WS_ABNORMAL_CLOSURE
                end._inbox.put_nowait(WSMessage(WSMsgType.CLOSED, None))


# --------------------------------------------------------------------------- #
# Recording doubles the suites read instead of the run engine
# --------------------------------------------------------------------------- #


class RecordingScheduler:
    """Records what ingestion admits; runs nothing (the run engine is P16)."""

    def __init__(self) -> None:
        self.admissions: list[tuple[SessionKey, Any]] = []

    def admit(self, session_key: SessionKey, work: Any) -> Any:
        self.admissions.append((session_key, work))
        return SimpleNamespace(accepted=True, run_id=f"run-{len(self.admissions)}")


# --------------------------------------------------------------------------- #
# The one shared runtime-context fixture
# --------------------------------------------------------------------------- #


def runtime_context(
    bus: EventBus | None = None,
    *,
    clock: ManualClock | None = None,
    scheduler: Any = None,
    triggers: Any = None,
    trigger_registry: Any = None,
    dedup_max_entries: int = 8,
    dedup_ttl_seconds: float = 60.0,
    chat: Any = None,
    authorization: AuthorizationPolicy | None = None,
    actions: ActionRegistry | None = None,
    attachments: AttachmentStore | None = None,
    rng: Any = None,
) -> RuntimeContext:
    """The versioned runtime every suite builds identically (P19).

    Real executor, real supervision, real supervised tasks, one counter
    registry shared by both so a snapshot reads every component's counts.
    ``trigger_registry`` is wrapped in the real trigger engine sharing the
    context's clock and counters, with the dedup window the caller bounds;
    a pre-built engine (or a fake recording decisions) can be passed as
    ``triggers`` instead. ``actions`` replaces the empty default registry so
    a suite can bind its providers before activation; ``authorization``
    feeds the executor's default-deny check (R5). ``scheduler`` is the
    shared admission scheduler — when ``None``, a module that owns one
    (the brain engine) builds the real :class:`AdmissionScheduler` itself
    onto the same context. ``attachments`` is the one store both the modules
    lease from (through the context) and the executor validates every
    ``image_ref`` part against (R4): a suite exercising images passes a
    store built on the same ``clock``, and the default ``None`` keeps a
    suite without images exactly as before. ``rng`` is the one random
    source every component draws from (trigger probability rules, proxy
    session ids): a seeded ``random.Random(0)`` by default, so a run replays
    identically without a suite seeding anything.
    """

    target_bus = bus if bus is not None else EventBus()
    target_clock = clock if clock is not None else ManualClock()
    target_rng = rng if rng is not None else random.Random(0)
    counters = Counters()
    policy = authorization if authorization is not None else AuthorizationPolicy()
    registry = actions if actions is not None else ActionRegistry(authorization=policy)
    supervision = Supervision(target_bus, counters=counters)
    engine = triggers
    if trigger_registry is not None:
        engine = TriggerEngine(
            trigger_registry,
            dedup_max_entries=dedup_max_entries,
            dedup_ttl_seconds=dedup_ttl_seconds,
            clock=target_clock,
            counters=counters,
            rng=target_rng,
        )
    return RuntimeContext(
        bus=target_bus,
        actions=registry,
        supervision=supervision,
        tasks=SupervisedTasks(),
        executor=ActionExecutor(
            registry,
            policy,
            supervision=supervision,
            counters=counters,
            clock=target_clock,
            attachments=attachments,
        ),
        triggers=engine,
        chat=chat,
        attachments=attachments,
        scheduler=scheduler,
        clock=target_clock,
        rng=target_rng,
    )


# --------------------------------------------------------------------------- #
# Shared loop helpers: bounded waits, never sleeps
# --------------------------------------------------------------------------- #


async def settle(turns: int = 20) -> None:
    """Give the loop *turns* bare reschedules. Zero delay, so no time passes."""

    for _ in range(turns):
        await asyncio.sleep(0)


async def wait_until(predicate: Any, turns: int = 2000) -> None:
    for _ in range(turns):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition did not become true within the turn budget")


def events_of(bus: EventBus, event_type: str) -> list[dict[str, Any]]:
    return [event for event in bus.list_events() if event["type"] == event_type]
