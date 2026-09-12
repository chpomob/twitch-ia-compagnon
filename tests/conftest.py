"""The one shared runtime-context fixture (P19).

Every suite that activates a module on the versioned runtime builds the
context through :func:`runtime_context` here rather than assembling its own
(``test_twitch``, ``test_brain``, ``test_audit`` today; the retention,
lifecycle and integration suites next). The builder wires the **real**
:class:`~core.actions.ActionExecutor`, the real
:class:`~core.admission.AdmissionScheduler` the brain engine builds onto it
and the real :class:`~core.runtime.Supervision`; only the edges the process
cannot own are fakes — the model transport (:class:`FakeSession`), the send
edge behind ``chat.write`` (:class:`FakeTransport` behind
:class:`FakeSendProvider`) and time itself (:class:`ManualClock`). A suite
that mocked the executor would hide AC19's executor-confirmed delivery, so
the executor is never mocked here.

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
from types import SimpleNamespace
from typing import Any

from core.actions import (
    ERROR_TIMED_OUT,
    ActionExecutor,
    ActionRegistry,
    AuthorizationPolicy,
)
from core.bus import EventBus
from core.contracts import ActionObservation, Counters, SessionKey
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


class FakeSession:
    """The model transport: one prepared result per request, in order."""

    def __init__(self, *results: Any) -> None:
        self.results = list(results)
        self.post_calls: list[dict[str, Any]] = []
        self.close_calls = 0

    async def post(self, url: str, **kwargs: Any) -> Any:
        self.post_calls.append({"url": url, **kwargs})
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result

    async def close(self) -> None:
        self.close_calls += 1


class HeldSession(FakeSession):
    """A model transport that holds every request until released."""

    def __init__(self, *results: Any) -> None:
        super().__init__(*results)
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def post(self, url: str, **kwargs: Any) -> Any:
        self.post_calls.append({"url": url, **kwargs})
        result = self.results.pop(0)
        self.entered.set()
        await self.release.wait()
        if isinstance(result, BaseException):
            raise result
        return result


def completion(content: str, usage: dict[str, int] | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"choices": [{"message": {"content": content}}]}
    if usage is not None:
        body["usage"] = usage
    return body


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
    onto the same context.
    """

    target_bus = bus if bus is not None else EventBus()
    target_clock = clock if clock is not None else ManualClock()
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
        )
    return RuntimeContext(
        bus=target_bus,
        actions=registry,
        supervision=supervision,
        tasks=SupervisedTasks(),
        executor=ActionExecutor(
            registry, policy, supervision=supervision, counters=counters, clock=target_clock
        ),
        triggers=engine,
        chat=chat,
        scheduler=scheduler,
        clock=target_clock,
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
