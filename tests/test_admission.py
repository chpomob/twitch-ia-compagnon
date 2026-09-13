"""Bounded admission, owned run terminals and both deadlines (R2, R8).

Nothing here sleeps, opens a transport or reads a real clock. The scheduler's
monotonic clock and every wait it performs are injected, so a wait deadline or
a total run deadline is provoked by advancing :class:`ManualClock` and letting
the loop turn — :func:`settle` is a run of bare zero-delay reschedules, which
is a yield, not a sleep. :data:`REAL_TIME_BUDGET` guards that: a test that
started sleeping for real would blow it.

The AC33 pair is deliberately asymmetric. In the first scenario nothing has
left the process, so the run simply ends ``run_deadline`` with 0 sends. In the
second the send provider has already put its request on the wire when the
budget runs out, and the point of the test is that the scheduler does **not**
decide what happened to it: it stops the run at the next boundary and the
executor (P7) classifies the call as ``external_unknown``, with the request
emitted exactly once and never retried.

The last section is phase 1's R3 groundwork for AC19: the optional stale-drop
hook is called exactly once per stale item with the work, the session key and
the total budget left, bounded by that budget, and whatever it does the
``stale_drop`` record and its one trace stand.
"""

import asyncio
import time

import pytest

from core.actions import (
    ActionExecutor,
    ActionRegistry,
    AuthorizationPolicy,
    AuthorizationRule,
)
from core.attachments import AttachmentStore, AttachmentUnknown, RunUsage
from core.admission import (
    COUNTER_STALE_DROP_HOOK_FAILURES,
    REASON_ADMITTED,
    REASON_CANCELLED,
    REASON_COMPLETED,
    REASON_GLOBAL_PENDING_CAP,
    REASON_RUN_DEADLINE,
    REASON_RUN_FAILED,
    REASON_SESSION_CAP,
    REASON_SESSION_QUEUE_FULL,
    REASON_STALE_DROP,
    AdmissionScheduler,
    RunLifecycleEmissionError,
    RunOutcome,
    Work,
)
from core.contracts import (
    COUNTER_ADMISSION_REJECTIONS,
    COUNTER_LOST_TRACES,
    COUNTER_RUN_DEADLINE_EXPIRIES,
    COUNTER_STALE_DROP,
    TRACE_BRAIN_ADMISSION_ACCEPTED,
    TRACE_BRAIN_ADMISSION_REJECTED,
    TRACE_BRAIN_RUN_COMPLETED,
    TRACE_BRAIN_RUN_STARTED,
    WILDCARD,
    ActionCall,
    ActionObservation,
    ActionSpec,
    ContractError,
    Counters,
    Destination,
    SessionKey,
)

PLATFORM = "twitch"
CHANNEL = "chan-a"
OPERATOR = "operator:companion"
MODULE = "chat-module"

REAL_TIME_BUDGET = 2.0
"""Wall-clock seconds a whole test may take. Nothing here waits for time."""

SESSION_A = SessionKey(PLATFORM, CHANNEL, "viewer-a")
SESSION_B = SessionKey(PLATFORM, CHANNEL, "viewer-b")
SESSION_C = SessionKey(PLATFORM, CHANNEL, "viewer-c")
HOLDER = SessionKey(PLATFORM, CHANNEL, "viewer-holder")

CHAT_SCOPE = Destination(PLATFORM, WILDCARD, "chat")

SEND_SPEC = ActionSpec(
    name="chat.send",
    version=1,
    description="Send one message to one channel.",
    argument_schema={
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
        "additionalProperties": False,
    },
    result_schema={
        "type": "object",
        "properties": {"message_id": {"type": "string"}},
        "required": ["message_id"],
    },
    nature="write",
    required_permissions=("chat.write",),
    supported_destinations=(CHAT_SCOPE,),
    timeout_seconds=1000.0,
    idempotency="key",
)


# --------------------------------------------------------------------------- #
# Doubles: no transport, no real clock, no sleep
# --------------------------------------------------------------------------- #


class ManualClock:
    """A monotonic clock nobody waits on: time only moves when a test says so.

    ``sleep`` is the scheduler's injected sleeper. It parks a future at
    ``now + delay`` and :meth:`advance` resolves the ones that came due, so a
    deadline fires because the test moved the clock, never because it waited.
    """

    def __init__(self, now: float = 0.0) -> None:
        self.now = now
        self.requested: list[float] = []
        self._waiters: list[tuple[float, asyncio.Future]] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, delay: float) -> None:
        self.requested.append(delay)
        if delay <= 0:
            return
        waiter = asyncio.get_running_loop().create_future()
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


async def settle(turns: int = 200) -> None:
    """Give the loop *turns* bare reschedules. Zero delay, so no time passes."""

    for _ in range(turns):
        await asyncio.sleep(0)


class FakeBus:
    """Records published traces and can be made to fail on chosen types."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []
        self.failing: set[str] = set()
        self.on_publish = None

    async def publish(self, event_type: str, payload) -> None:
        if self.on_publish is not None:
            self.on_publish(event_type, payload)
        if event_type in self.failing:
            raise RuntimeError(f"publication of {event_type!r} failed")
        self.events.append((event_type, dict(payload)))

    def of_type(self, event_type: str) -> list[dict]:
        return [payload for name, payload in self.events if name == event_type]

    def for_run(self, event_type: str, run_id: str) -> list[dict]:
        return [
            payload
            for payload in self.of_type(event_type)
            if payload.get("run_id") == run_id
        ]

    def types(self) -> list[str]:
        return [name for name, _payload in self.events]


class FakeSupervision:
    """A stand-in for P10's facade, no more and no less.

    With ``honours_ordering`` set — P10's real behaviour — ``record_and_emit``
    calls *record* first and swallows-and-counts a failed publication. Cleared,
    it publishes first and records only on success, which is the *wrong* order
    on purpose: R8 makes the scheduler the owner of the run terminal state, so
    the state must survive a supervision that never records at all.
    """

    def __init__(self, bus: FakeBus, counters: Counters) -> None:
        self.bus = bus
        self.counters = counters
        self.order: list[str] = []
        self.honours_ordering = True

    async def emit(self, event_type: str, payload) -> None:
        await self.bus.publish(event_type, payload)

    async def record_and_emit(self, record, event_type: str, payload) -> None:
        if self.honours_ordering:
            record()
            self.order.append(f"record:{event_type}")
        self.order.append(f"publish:{event_type}")
        try:
            await self.bus.publish(event_type, payload)
        except Exception:
            self.counters.increment(COUNTER_LOST_TRACES)
            return
        if not self.honours_ordering:
            record()
            self.order.append(f"record:{event_type}")


class FakeModel:
    """Counts requests and holds every one of them until released.

    It is the stand-in for whatever the run body calls out to; the scheduler
    never touches it, which is precisely what AC6 asserts.
    """

    def __init__(self) -> None:
        self.calls = 0
        self.responses = 0
        self._gate = asyncio.Event()

    async def respond(self, _prompt: str) -> str:
        self.calls += 1
        await self._gate.wait()
        self.responses += 1
        return "an answer"

    def release(self) -> None:
        self._gate.set()


class RunSpy:
    """A run body that records what it was given and holds where told to.

    Bodies are per-session-key gated so a test can let one session's run finish
    while another is still inside its model call.
    """

    def __init__(self, model: FakeModel | None = None) -> None:
        self.model = model
        self.started: list[str] = []
        self.finished: list[str] = []
        self.contexts: list = []
        self.concurrent = 0
        self.peak = 0
        self.gates: dict[str, asyncio.Event] = {}
        self.hold_all = False

    def gate(self, viewer_id: str) -> asyncio.Event:
        return self.gates.setdefault(viewer_id, asyncio.Event())

    async def __call__(self, context) -> RunOutcome:
        viewer = context.session_key.viewer_id
        self.started.append(context.work.payload["text"])
        self.contexts.append(context)
        self.concurrent += 1
        self.peak = max(self.peak, self.concurrent)
        try:
            if self.model is not None:
                context.note_model_call()
                await self.model.respond(context.work.payload["text"])
            if self.hold_all:
                await asyncio.Event().wait()
            if viewer in self.gates:
                await self.gates[viewer].wait()
            self.finished.append(context.work.payload["text"])
            return RunOutcome(status="success", delivery="delivered", sends=1)
        finally:
            self.concurrent -= 1


class Harness:
    """A scheduler wired onto the doubles, plus the counters it increments."""

    def __init__(self, run_body, **settings) -> None:
        self.bus = FakeBus()
        self.counters = Counters()
        self.supervision = FakeSupervision(self.bus, self.counters)
        self.clock = ManualClock()
        self.ids = iter(f"run-{index}" for index in range(1, 10_000))
        options = {
            "session_queue_capacity": 8,
            "global_pending_capacity": 32,
            "max_sessions": 8,
            "workers": 1,
            "total_run_seconds": 1000.0,
            "wait_seconds": 1000.0,
        }
        options.update(settings)
        self.scheduler = AdmissionScheduler(
            run_body,
            clock=self.clock,
            sleeper=self.clock.sleep,
            supervision=self.supervision,
            counters=self.counters,
            id_factory=lambda: next(self.ids),
            **options,
        )

    async def __aenter__(self) -> "Harness":
        await self.scheduler.start()
        return self

    async def __aexit__(self, *_exc) -> None:
        await self.scheduler.aclose()


def message(text: str, *, event_id: str | None = None, kind: str = "chat.message"):
    return Work(
        payload={"text": text},
        source_event_id=event_id or f"evt-{text}",
        kind=kind,
    )


# --------------------------------------------------------------------------- #
# AC6 — the handler returns before the model responds
# --------------------------------------------------------------------------- #


async def test_ingestion_returns_before_the_model_responds_and_queues_the_rest():
    """AC6: a held model blocks the run, never the ingestion of the next frames."""

    wall_start = time.monotonic()
    model = FakeModel()
    body = RunSpy(model)
    async with Harness(body, workers=1) as harness:
        first = harness.scheduler.admit(SESSION_A, message("first"))
        await settle()

        # The first run is inside the model and stays there for the whole test.
        assert model.calls == 1
        assert model.responses == 0

        second = harness.scheduler.admit(SESSION_A, message("second"))
        keepalive = harness.scheduler.admit(
            SESSION_B, message("keepalive", kind="channel.keepalive")
        )

        # Both admissions answered synchronously, while the model still holds.
        assert (first.accepted, second.accepted, keepalive.accepted) == (True, True, True)
        assert first.run_id and second.run_id and keepalive.run_id
        assert model.responses == 0

        await settle()
        accepted = harness.bus.of_type(TRACE_BRAIN_ADMISSION_ACCEPTED)
        assert [payload["source_event_id"] for payload in accepted] == [
            "evt-first",
            "evt-second",
            "evt-keepalive",
        ]
        assert {payload["run_id"] for payload in accepted} == {
            first.run_id,
            second.run_id,
            keepalive.run_id,
        }
        # The second message of session A is queued behind its own session's
        # active run, not lost and not run concurrently with it (AC9).
        assert harness.scheduler.queue_depth(SESSION_A) == 1
        assert body.started == ["first"]

    # Not one wait for real time anywhere in the scenario.
    assert harness.clock.now == 0.0
    assert time.monotonic() - wall_start < REAL_TIME_BUDGET


async def test_admitted_payload_is_independent_of_a_later_mutation():
    """R2: the queued copy is taken at admission, not shared with the event."""

    body = RunSpy()
    payload = {"text": "hello", "tags": ["a"], "meta": {"nested": 1}}
    work = Work(payload=payload, source_event_id="evt-1")

    payload["text"] = "mutated"
    payload["tags"].append("b")
    payload["meta"]["nested"] = 99

    async with Harness(body, workers=1) as harness:
        harness.scheduler.admit(SESSION_A, work)
        await settle()

    assert body.contexts[0].work.payload["text"] == "hello"
    assert body.contexts[0].work.payload["tags"] == ("a",)
    assert body.contexts[0].work.payload["meta"]["nested"] == 1
    with pytest.raises(TypeError):
        body.contexts[0].work.payload["text"] = "again"


# --------------------------------------------------------------------------- #
# AC7 — saturation is a refusal, traced with its reason and its depth
# --------------------------------------------------------------------------- #


async def test_full_session_queue_and_global_cap_reject_with_reason_and_depth():
    """AC7: 3 accepted, 3 rejected, each naming what saturated and how deep."""

    model = FakeModel()
    body = RunSpy(model)
    async with Harness(
        body,
        workers=1,
        session_queue_capacity=2,
        global_pending_capacity=3,
    ) as harness:
        # Hold the only worker so that 0 of the 6 messages below is dequeued.
        harness.scheduler.admit(HOLDER, message("holder"))
        await settle()
        assert harness.scheduler.pending == 0
        assert harness.scheduler.active_runs == 1
        assert model.calls == 1

        results = [
            harness.scheduler.admit(SESSION_A, message("a1")),
            harness.scheduler.admit(SESSION_A, message("a2")),
            harness.scheduler.admit(SESSION_A, message("a3")),
            harness.scheduler.admit(SESSION_B, message("b1")),
            harness.scheduler.admit(SESSION_B, message("b2")),
            harness.scheduler.admit(SESSION_C, message("c1")),
        ]
        await settle()

        assert [result.accepted for result in results] == [
            True, True, False, True, False, False
        ]
        assert [result.reason for result in results] == [
            REASON_ADMITTED,
            REASON_ADMITTED,
            REASON_SESSION_QUEUE_FULL,
            REASON_ADMITTED,
            REASON_GLOBAL_PENDING_CAP,
            REASON_GLOBAL_PENDING_CAP,
        ]
        assert [result.queue_depth for result in results] == [1, 2, 2, 1, 3, 3]

        rejected = harness.bus.of_type(TRACE_BRAIN_ADMISSION_REJECTED)
        assert len(rejected) == 3
        assert [
            (payload["source_event_id"], payload["reason"], payload["queue_depth"])
            for payload in rejected
        ] == [
            ("evt-a3", REASON_SESSION_QUEUE_FULL, 2),
            ("evt-b2", REASON_GLOBAL_PENDING_CAP, 3),
            ("evt-c1", REASON_GLOBAL_PENDING_CAP, 3),
        ]
        # A rejection promises nothing and carries no run identity to correlate.
        assert all(payload["replay_promised"] is False for payload in rejected)
        assert all("run_id" not in payload for payload in rejected)
        assert all(result.replay_promised is False for result in results if not result.accepted)

        accepted = harness.bus.of_type(TRACE_BRAIN_ADMISSION_ACCEPTED)
        assert [payload["source_event_id"] for payload in accepted] == [
            "evt-holder", "evt-a1", "evt-a2", "evt-b1",
        ]

        assert harness.counters.get(COUNTER_ADMISSION_REJECTIONS) == 3
        # The 3 rejected messages reached the model 0 times: only the holder did.
        assert model.calls == 1
        assert body.started == ["holder"]


# --------------------------------------------------------------------------- #
# AC8 — the wait deadline drops stale work before it can start
# --------------------------------------------------------------------------- #


async def test_wait_deadline_expiry_drops_stale_work_with_no_model_and_no_send():
    """AC8: `stale_drop` at `timeout`, traced once with its waited duration."""

    model = FakeModel()
    body = RunSpy(model)
    async with Harness(
        body, workers=1, wait_seconds=5.0, total_run_seconds=1000.0
    ) as harness:
        harness.scheduler.admit(HOLDER, message("holder"))
        await settle()
        assert harness.scheduler.active_runs == 1

        queued = harness.scheduler.admit(SESSION_A, message("stale"))
        await settle()
        assert harness.scheduler.pending == 1

        harness.clock.advance(6.0)
        await settle()

        record = harness.scheduler.run_record(queued.run_id)
        assert record is not None
        assert (record.status, record.reason) == ("timeout", REASON_STALE_DROP)
        assert record.waited_seconds == pytest.approx(6.0)
        assert record.started is False

        completed = harness.bus.for_run(TRACE_BRAIN_RUN_COMPLETED, queued.run_id)
        assert len(completed) == 1
        assert completed[0]["waited_seconds"] == pytest.approx(6.0)
        assert completed[0]["status"] == "timeout"
        assert completed[0]["reason"] == REASON_STALE_DROP
        assert completed[0]["model_calls"] == 0
        assert completed[0]["sends"] == 0

        # It never started, so it never announced a start either.
        assert harness.bus.for_run(TRACE_BRAIN_RUN_STARTED, queued.run_id) == []
        assert harness.counters.get(COUNTER_STALE_DROP) == 1
        # Only the holder ever reached the model, and nothing was ever sent.
        assert model.calls == 1
        assert body.started == ["holder"]
        assert harness.scheduler.pending == 0


async def test_wait_deadline_is_clamped_to_the_total_deadline():
    """R2: a wait deadline may never outlive the run budget it belongs to."""

    body = RunSpy(FakeModel())
    async with Harness(
        body, workers=1, wait_seconds=50.0, total_run_seconds=10.0
    ) as harness:
        harness.scheduler.admit(HOLDER, message("holder"))
        await settle()

        queued = harness.scheduler.admit(SESSION_A, message("stale"))
        await settle()

        # 11 units is well short of the configured 50-unit wait, and past the
        # 10-unit total run duration the wait was clamped to.
        harness.clock.advance(11.0)
        await settle()

        record = harness.scheduler.run_record(queued.run_id)
        assert record is not None
        assert (record.status, record.reason) == ("timeout", REASON_STALE_DROP)
        assert record.waited_seconds == pytest.approx(11.0)


# --------------------------------------------------------------------------- #
# AC9 — one run per session, sessions concurrent up to the worker limit
# --------------------------------------------------------------------------- #


async def test_one_session_runs_in_fed_order_while_another_session_progresses():
    """AC9: session A is serialised in fed order; session B does not wait for it."""

    body = RunSpy()
    body.gate("viewer-a")
    body.gate("viewer-b")
    async with Harness(body, workers=2) as harness:
        harness.scheduler.admit(SESSION_A, message("a1"))
        harness.scheduler.admit(SESSION_A, message("a2"))
        harness.scheduler.admit(SESSION_B, message("b1"))
        await settle()

        # One run for session A, and session B's run started alongside it while
        # A's second message is still queued behind A's own active run.
        assert body.started == ["a1", "b1"]
        assert harness.scheduler.queue_depth(SESSION_A) == 1
        assert harness.scheduler.active_runs == 2

        body.gates["viewer-a"].set()
        await settle()
        # Session A drained in the order it was fed, and B's run — started long
        # before A's second message completed — is still running.
        assert body.started == ["a1", "b1", "a2"]
        assert body.finished == ["a1", "a2"]
        assert harness.scheduler.active_runs == 1

        body.gates["viewer-b"].set()
        await settle()
        assert body.started == ["a1", "b1", "a2"]
        assert set(body.finished) == {"a1", "a2", "b1"}

        # Idle queues and their locks are evicted once nothing is left (AC9).
        assert harness.scheduler.pending == 0
        assert harness.scheduler.active_runs == 0
        assert harness.scheduler.live_sessions == 0


async def test_three_simultaneous_sessions_never_exceed_the_worker_limit():
    """AC9: with 2 workers, 3 sessions fed at once run 2 at a time, never 3."""

    body = RunSpy()
    for viewer in ("viewer-a", "viewer-b", "viewer-c"):
        body.gate(viewer)
    async with Harness(body, workers=2) as harness:
        harness.scheduler.admit(SESSION_A, message("a1"))
        harness.scheduler.admit(SESSION_B, message("b1"))
        harness.scheduler.admit(SESSION_C, message("c1"))
        await settle()

        assert body.peak == 2
        assert harness.scheduler.active_runs == 2
        assert len(body.started) == 2

        for viewer in ("viewer-a", "viewer-b", "viewer-c"):
            body.gates[viewer].set()
            await settle()

        assert body.peak == 2
        assert sorted(body.finished) == ["a1", "b1", "c1"]
        assert harness.scheduler.live_sessions == 0


# --------------------------------------------------------------------------- #
# AC33 — the total deadline bounds the whole run
# --------------------------------------------------------------------------- #


async def test_total_deadline_expiry_inside_the_model_ends_the_run_deadline():
    """AC33, first scenario: held in a pending model, 0 sends, traced once."""

    model = FakeModel()
    body = RunSpy(model)
    async with Harness(body, workers=1, total_run_seconds=10.0) as harness:
        admitted = harness.scheduler.admit(SESSION_A, message("held"))
        await settle()
        assert model.calls == 1
        assert model.responses == 0

        harness.clock.advance(11.0)
        await settle()

        record = harness.scheduler.run_record(admitted.run_id)
        assert record is not None
        assert (record.status, record.reason) == ("timeout", REASON_RUN_DEADLINE)
        assert record.total_seconds >= 10.0
        assert record.sends == 0

        completed = harness.bus.for_run(TRACE_BRAIN_RUN_COMPLETED, admitted.run_id)
        assert len(completed) == 1
        assert completed[0]["status"] == "timeout"
        assert completed[0]["reason"] == REASON_RUN_DEADLINE
        assert completed[0]["total_seconds"] >= 10.0
        assert completed[0]["sends"] == 0

        assert len(harness.bus.for_run(TRACE_BRAIN_RUN_STARTED, admitted.run_id)) == 1
        assert harness.counters.get(COUNTER_RUN_DEADLINE_EXPIRIES) == 1
        # The model never answered, so nothing was ever delivered.
        assert model.responses == 0
        assert body.finished == []


async def test_total_deadline_after_emission_is_classified_external_unknown_once():
    """AC33, second scenario: the request is on the wire when the budget ends.

    The deadline expires after the send provider emitted its request and before
    the transport confirmed. The scheduler stops the run at the next boundary
    and lets the executor classify the call: exactly 1 ``external_unknown``, 0
    ``success``, the request emitted exactly 1 time and never retried, and the
    run still ends ``timeout`` with reason ``run_deadline``.
    """

    class Transport:
        """Records what actually left, and how many times."""

        def __init__(self) -> None:
            self.requests: list[str] = []

        def emit(self, text: str) -> None:
            self.requests.append(text)

    transport = Transport()

    class BlockingSendProvider:
        name = "chat-sender"

        def __init__(self) -> None:
            self.invocations = 0

        async def invoke(self, invocation):
            self.invocations += 1
            # On the wire first, declared emitted second, confirmation never.
            transport.emit(invocation.call.arguments["text"])
            invocation.mark_emitted()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")  # pragma: no cover

    provider = BlockingSendProvider()
    bus = FakeBus()
    counters = Counters()
    supervision = FakeSupervision(bus, counters)
    clock = ManualClock()

    policy = AuthorizationPolicy()
    registry = ActionRegistry(authorization=policy)
    registry.declare(SEND_SPEC, module=MODULE)
    registry.bind(SEND_SPEC.name, provider, module=MODULE)
    registry.mark_ready(MODULE)
    policy.grant(
        AuthorizationRule(
            rule_id="allow-send",
            action_name=SEND_SPEC.name,
            destination=CHAT_SCOPE,
            principals=(OPERATOR,),
            natures=("write",),
            granted_permissions=SEND_SPEC.required_permissions,
        )
    )
    executor = ActionExecutor(
        registry,
        policy,
        supervision=supervision,
        counters=counters,
        clock=clock,
        sleeper=clock.sleep,
        cancel_grace_seconds=0.0,
    )

    observations: list[ActionObservation] = []

    async def run_body(context):
        # The call inherits the run's budget, so the executor is the component
        # that resolves a call still in flight when the budget runs out.
        call = ActionCall(
            action_name=SEND_SPEC.name,
            action_version=SEND_SPEC.version,
            arguments={"text": "on the wire"},
            conversation_id=context.conversation_id,
            run_id=context.run_id,
            call_id=f"{context.run_id}-call-1",
            source_event_id=context.work.source_event_id,
            destination=Destination(PLATFORM, CHANNEL, "chat"),
            principal=OPERATOR,
            deadline=context.total_deadline,
        )
        observations.append(await executor.invoke(call))
        if context.expired:
            # The boundary: stop here rather than start further work.
            return RunOutcome(status="timeout", delivery="unconfirmed")
        return RunOutcome(status="success", delivery="delivered", sends=1)

    scheduler = AdmissionScheduler(
        run_body,
        session_queue_capacity=4,
        global_pending_capacity=8,
        max_sessions=4,
        workers=1,
        total_run_seconds=10.0,
        wait_seconds=10.0,
        clock=clock,
        sleeper=clock.sleep,
        supervision=supervision,
        counters=counters,
        id_factory=lambda: "run-emitted",
    )
    await scheduler.start()
    try:
        admitted = scheduler.admit(SESSION_A, message("send me"))
        await settle()
        assert transport.requests == ["on the wire"]

        clock.advance(11.0)
        await settle()

        # Exactly one classification, and it is the uncertain one.
        assert len(observations) == 1
        assert observations[0].status == "external_unknown"
        statuses = [value.status for value in executor.outcomes().values()]
        assert statuses == ["external_unknown"]
        assert statuses.count("success") == 0

        # Emitted once, never replayed, re-admitted or re-invoked.
        assert transport.requests == ["on the wire"]
        assert provider.invocations == 1
        assert executor.provider_invocations == 1
        assert scheduler.pending == 0
        assert scheduler.live_sessions == 0

        record = scheduler.run_record(admitted.run_id)
        assert record is not None
        assert (record.status, record.reason) == ("timeout", REASON_RUN_DEADLINE)
        assert record.sends == 0
        # The body reached its boundary and returned; it was not cut off, which
        # is what let the executor classify the call rather than the scheduler.
        assert record.delivery == "unconfirmed"
        completed = bus.for_run(TRACE_BRAIN_RUN_COMPLETED, admitted.run_id)
        assert len(completed) == 1
        assert completed[0]["reason"] == REASON_RUN_DEADLINE
    finally:
        await scheduler.aclose()


# --------------------------------------------------------------------------- #
# R8 — the terminal state is recorded before its trace is published
# --------------------------------------------------------------------------- #


async def test_failed_completion_publication_leaves_the_record_intact():
    """R8: a lost `brain.run.completed` re-runs, retries and cancels nothing."""

    body = RunSpy()
    seen_at_publish: list[object] = []
    async with Harness(body, workers=1) as harness:
        # Supervision that publishes first and never gets to record, so a
        # recorded terminal state can only be one the scheduler wrote itself.
        harness.supervision.honours_ordering = False
        harness.bus.failing.add(TRACE_BRAIN_RUN_COMPLETED)
        harness.bus.on_publish = lambda event_type, payload: (
            seen_at_publish.append(
                harness.scheduler.run_record(payload["run_id"])
            )
            if event_type == TRACE_BRAIN_RUN_COMPLETED
            else None
        )
        admitted = harness.scheduler.admit(SESSION_A, message("once"))
        await settle()

        assert harness.bus.for_run(TRACE_BRAIN_RUN_COMPLETED, admitted.run_id) == []

        record = harness.scheduler.run_record(admitted.run_id)
        assert record is not None
        assert (record.status, record.reason) == ("success", REASON_COMPLETED)
        assert record.delivery == "delivered"
        assert record.waited_seconds == pytest.approx(0.0)
        assert record.total_seconds == pytest.approx(0.0)
        assert record.started is True

        # The state was already readable when the publication was attempted,
        # and supervision never recorded anything of its own.
        assert seen_at_publish == [record]
        assert harness.supervision.order == [f"publish:{TRACE_BRAIN_RUN_COMPLETED}"]
        assert harness.counters.get(COUNTER_LOST_TRACES) == 1

        # Neither re-admitted nor re-executed.
        assert body.started == ["once"]
        assert body.finished == ["once"]
        assert harness.scheduler.pending == 0
        assert harness.scheduler.active_runs == 0
        assert harness.scheduler.live_sessions == 0

        await settle()
        assert body.started == ["once"]


# --------------------------------------------------------------------------- #
# R8 — the scheduler is the single emission owner of the lifecycle traces
# --------------------------------------------------------------------------- #


async def test_run_body_may_not_publish_the_run_lifecycle_traces():
    """R8, AC27: a second publisher is refused, and the refusal names it."""

    refusals: list[RunLifecycleEmissionError] = []

    async def run_body(context):
        for event_type in (TRACE_BRAIN_RUN_STARTED, TRACE_BRAIN_RUN_COMPLETED):
            with pytest.raises(RunLifecycleEmissionError) as info:
                context.emit(event_type, {"run_id": context.run_id})
            refusals.append(info.value)
            with pytest.raises(RunLifecycleEmissionError):
                context.record_and_emit(lambda: None, event_type, {})
        return RunOutcome()

    async with Harness(run_body, workers=1, run_module="brain") as harness:
        admitted = harness.scheduler.admit(SESSION_A, message("quiet"))
        await settle()

    assert [error.event_type for error in refusals] == [
        TRACE_BRAIN_RUN_STARTED,
        TRACE_BRAIN_RUN_COMPLETED,
    ]
    assert all("brain" in str(error) for error in refusals)
    assert all(error.module == "brain" for error in refusals)

    # Exactly one of each, all of it published by the scheduler (AC27).
    assert len(harness.bus.for_run(TRACE_BRAIN_RUN_STARTED, admitted.run_id)) == 1
    assert len(harness.bus.for_run(TRACE_BRAIN_RUN_COMPLETED, admitted.run_id)) == 1


async def test_an_exception_escaping_the_run_body_still_completes_exactly_once():
    """R8: every exit path ends in exactly 1 `brain.run.completed`."""

    async def run_body(_context):
        raise RuntimeError("the body gave up")

    async with Harness(run_body, workers=1) as harness:
        admitted = harness.scheduler.admit(SESSION_A, message("boom"))
        await settle()

    record = harness.scheduler.run_record(admitted.run_id)
    assert record is not None
    assert (record.status, record.reason) == ("error", REASON_RUN_FAILED)
    completed = harness.bus.for_run(TRACE_BRAIN_RUN_COMPLETED, admitted.run_id)
    assert len(completed) == 1
    assert "the body gave up" in completed[0]["error"]
    assert harness.scheduler.live_sessions == 0


async def test_accepted_events_carry_the_run_id_and_the_lifecycle_order():
    """R8, AC27: one identical `run_id` from admission onwards, in order."""

    async def run_body(_context):
        return RunOutcome(status="success", delivery="delivered", sends=1)

    async with Harness(run_body, workers=1) as harness:
        admitted = harness.scheduler.admit(SESSION_A, message("one"))
        await settle()

    assert harness.bus.types() == [
        TRACE_BRAIN_ADMISSION_ACCEPTED,
        TRACE_BRAIN_RUN_STARTED,
        TRACE_BRAIN_RUN_COMPLETED,
    ]
    assert {payload["run_id"] for _name, payload in harness.bus.events} == {
        admitted.run_id
    }
    accepted = harness.bus.of_type(TRACE_BRAIN_ADMISSION_ACCEPTED)[0]
    assert (accepted["platform"], accepted["channel_id"]) == (PLATFORM, CHANNEL)
    assert accepted["source_event_id"] == "evt-one"
    assert accepted["reason"] == REASON_ADMITTED
    completed = harness.bus.of_type(TRACE_BRAIN_RUN_COMPLETED)[0]
    assert completed["delivery"] == "delivered"
    assert completed["sends"] == 1


# --------------------------------------------------------------------------- #
# R2 — every bound is required, finite and validated at construction
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "setting",
    [
        "session_queue_capacity",
        "global_pending_capacity",
        "max_sessions",
        "workers",
        "total_run_seconds",
        "wait_seconds",
    ],
)
@pytest.mark.parametrize("value", [None, float("inf"), 0])
async def test_absent_or_non_finite_limits_raise_naming_the_setting(setting, value):
    """R2: P12 can stop startup because the diagnostic names the setting."""

    async def run_body(_context):
        return RunOutcome()

    options = {
        "session_queue_capacity": 4,
        "global_pending_capacity": 8,
        "max_sessions": 4,
        "workers": 1,
        "total_run_seconds": 10.0,
        "wait_seconds": 5.0,
    }
    options[setting] = value
    with pytest.raises(ContractError) as info:
        AdmissionScheduler(run_body, **options)
    assert setting in str(info.value)


async def test_a_full_session_cap_refuses_a_new_session_rather_than_evicting_a_live_one():
    """R2, AC9: eviction only ever considers an idle session, never a live one."""

    body = RunSpy()
    body.hold_all = True
    async with Harness(
        body, workers=1, max_sessions=1, session_queue_capacity=4
    ) as harness:
        first = harness.scheduler.admit(SESSION_A, message("a1"))
        await settle()
        assert first.accepted
        assert harness.scheduler.active_runs == 1

        second = harness.scheduler.admit(SESSION_B, message("b1"))
        assert second.accepted is False
        assert second.reason == REASON_SESSION_CAP
        assert harness.counters.get(COUNTER_ADMISSION_REJECTIONS) == 1
        # The live session kept its queue and its lock.
        assert harness.scheduler.live_sessions == 1
        assert harness.scheduler.active_runs == 1


# --------------------------------------------------------------------------- #
# R2, R8 — nothing admitted is dropped, and nothing unbounded is held
# --------------------------------------------------------------------------- #


async def test_closing_completes_the_work_still_waiting_in_a_queue():
    """R8, AC27: a closed scheduler cannot finish it, so it terminates it."""

    body = RunSpy()
    body.hold_all = True
    harness = Harness(body, workers=1)
    await harness.scheduler.start()

    held = harness.scheduler.admit(SESSION_A, message("held"))
    queued = harness.scheduler.admit(SESSION_A, message("queued"))
    await settle()
    assert harness.scheduler.pending == 1

    await harness.scheduler.aclose()

    # The queued item never started, and says so rather than vanishing.
    record = harness.scheduler.run_record(queued.run_id)
    assert record is not None
    assert (record.status, record.reason) == ("cancelled", REASON_CANCELLED)
    assert record.started is False
    assert (record.model_calls, record.sends) == (0, 0)
    assert len(harness.bus.for_run(TRACE_BRAIN_RUN_COMPLETED, queued.run_id)) == 1
    # It never started, so it never claimed a `brain.run.started` either.
    assert harness.bus.for_run(TRACE_BRAIN_RUN_STARTED, queued.run_id) == []
    # The run that was in flight is accounted for exactly once as well.
    assert len(harness.bus.for_run(TRACE_BRAIN_RUN_COMPLETED, held.run_id)) == 1
    assert harness.scheduler.pending == 0
    assert harness.scheduler.live_sessions == 0


# --------------------------------------------------------------------------- #
# F9 — no admitted item vanishes on a shutdown that lands mid-publication
# --------------------------------------------------------------------------- #


def _gate_publications(
    harness, *, blocked_types: set[str], blocked_sources: set[str] | None = None
) -> asyncio.Event:
    """Hold chosen publications on a gate the terminal trace then opens.

    The gate is released by the first ``brain.run.completed`` the scheduler
    publishes, so the blocked traces finish and ``aclose`` can drain them:
    the test blocks the *pre-terminal* publications, never the accounting.
    """

    gate = asyncio.Event()
    upstream = harness.bus.publish

    async def gated(event_type, payload):
        if event_type in blocked_types and (
            blocked_sources is None or payload.get("source_event_id") in blocked_sources
        ):
            await gate.wait()
        await upstream(event_type, payload)

    harness.bus.publish = gated

    def open_on_completion(event_type, _payload):
        if event_type == TRACE_BRAIN_RUN_COMPLETED:
            gate.set()

    harness.bus.on_publish = open_on_completion
    return gate


async def test_shutdown_inside_the_admission_trace_settlement_still_records_the_run():
    """F9: a cancelled worker may not leave its dequeued item unrecorded."""

    body = RunSpy()
    harness = Harness(body, workers=1)
    await harness.scheduler.start()
    _gate_publications(harness, blocked_types={TRACE_BRAIN_ADMISSION_ACCEPTED})

    admitted = harness.scheduler.admit(SESSION_A, message("victim"))
    await settle()
    # Dequeued (a session is locked on it) yet recorded nowhere: the exact
    # ownership window F9 names, parked inside the admission publication.
    assert harness.scheduler.active_runs == 1
    assert harness.scheduler.pending == 0
    assert harness.scheduler.run_record(admitted.run_id) is None
    assert body.started == []

    await harness.scheduler.aclose()

    record = harness.scheduler.run_record(admitted.run_id)
    assert record is not None
    assert (record.status, record.reason) == ("cancelled", REASON_CANCELLED)
    assert record.started is False
    assert (record.model_calls, record.sends) == (0, 0)
    assert len(harness.bus.for_run(TRACE_BRAIN_RUN_COMPLETED, admitted.run_id)) == 1
    # Never started, so it never claimed a `brain.run.started` either.
    assert harness.bus.for_run(TRACE_BRAIN_RUN_STARTED, admitted.run_id) == []
    # No orphaned work: nothing pending, no session held, no active run.
    assert harness.scheduler.pending == 0
    assert harness.scheduler.active_runs == 0
    assert harness.scheduler.live_sessions == 0


async def test_shutdown_inside_the_started_publication_still_records_the_run():
    """F9: the started trace is awaited before the cancellation handler."""

    body = RunSpy()
    harness = Harness(body, workers=1)
    await harness.scheduler.start()
    _gate_publications(harness, blocked_types={TRACE_BRAIN_RUN_STARTED})

    admitted = harness.scheduler.admit(SESSION_A, message("victim"))
    await settle()
    assert harness.scheduler.active_runs == 1
    assert harness.scheduler.run_record(admitted.run_id) is None
    assert body.started == []
    # The admission trace settled; the worker is inside the started one.
    assert len(harness.bus.for_run(TRACE_BRAIN_ADMISSION_ACCEPTED, admitted.run_id)) == 1

    await harness.scheduler.aclose()

    record = harness.scheduler.run_record(admitted.run_id)
    assert record is not None
    assert (record.status, record.reason) == ("cancelled", REASON_CANCELLED)
    assert record.started is False
    assert (record.model_calls, record.sends) == (0, 0)
    assert len(harness.bus.for_run(TRACE_BRAIN_RUN_COMPLETED, admitted.run_id)) == 1
    assert harness.bus.for_run(TRACE_BRAIN_RUN_STARTED, admitted.run_id) == []
    assert body.started == []
    assert harness.scheduler.pending == 0
    assert harness.scheduler.active_runs == 0
    assert harness.scheduler.live_sessions == 0


async def test_shutdown_inside_a_stale_batch_completion_still_records_the_run():
    """F9: the reaper owns its claimed stale items until they are recorded."""

    body = RunSpy()
    body.hold_all = True
    harness = Harness(body, workers=1, wait_seconds=5.0)
    await harness.scheduler.start()
    _gate_publications(
        harness,
        blocked_types={TRACE_BRAIN_ADMISSION_ACCEPTED},
        blocked_sources={"evt-victim"},
    )

    held = harness.scheduler.admit(HOLDER, message("holder"))
    await settle()
    assert body.started == ["holder"]

    victim = harness.scheduler.admit(SESSION_B, message("victim"))
    harness.clock.advance(6.0)
    await settle()
    # Removed from its queue by the reaper's batch, completion parked on the
    # blocked admission trace, and recorded nowhere yet.
    assert harness.scheduler.pending == 0
    assert harness.scheduler.run_record(victim.run_id) is None
    assert body.started == ["holder"]

    await harness.scheduler.aclose()

    # The stale item kept the terminal state it was claimed for, not a hole.
    record = harness.scheduler.run_record(victim.run_id)
    assert record is not None
    assert (record.status, record.reason) == ("timeout", REASON_STALE_DROP)
    assert record.started is False
    assert (record.model_calls, record.sends) == (0, 0)
    assert len(harness.bus.for_run(TRACE_BRAIN_RUN_COMPLETED, victim.run_id)) == 1
    assert harness.counters.get(COUNTER_STALE_DROP) == 1
    # The held run is accounted for exactly once too: one terminal record
    # per accepted item, and no orphaned work anywhere.
    held_record = harness.scheduler.run_record(held.run_id)
    assert held_record is not None
    assert (held_record.status, held_record.reason) == ("cancelled", REASON_CANCELLED)
    assert len(harness.bus.of_type(TRACE_BRAIN_RUN_COMPLETED)) == 2
    assert harness.scheduler.pending == 0
    assert harness.scheduler.active_runs == 0
    assert harness.scheduler.live_sessions == 0


async def test_a_cancelled_stale_batch_is_not_duplicated_when_the_record_cache_evicted():
    """F9 review: an evicted record must not read as an unfinished item.

    ``max_records=1``: the first stale item's completion is fully published,
    the second blocks on its publication *after* its record evicted the
    first. Cancelling the reaper then hands the whole batch back — the
    already-terminal items must be recognised on the items themselves, not
    finished a second time with a duplicated record and stale-drop count.
    """

    body = RunSpy()
    body.hold_all = True
    harness = Harness(body, workers=1, wait_seconds=5.0, max_records=1)
    await harness.scheduler.start()

    held = harness.scheduler.admit(HOLDER, message("holder"))
    await settle()
    assert body.started == ["holder"]

    first = harness.scheduler.admit(SESSION_A, message("first"))
    second = harness.scheduler.admit(SESSION_A, message("second"))
    harness.clock.advance(6.0)

    # Block only the second item's completion, and only once, so a buggy
    # re-publication would complete rather than park the reaper forever.
    gate = asyncio.Event()
    blocked = False
    upstream = harness.bus.publish

    async def gated(event_type, payload):
        nonlocal blocked
        if (
            event_type == TRACE_BRAIN_RUN_COMPLETED
            and payload["run_id"] == second.run_id
            and not blocked
        ):
            blocked = True
            await gate.wait()
        await upstream(event_type, payload)

    harness.bus.publish = gated
    await settle()

    # The reaper claimed both stale items: the first is recorded *and*
    # published, the second is recorded — evicting the first — and parked.
    assert harness.scheduler.pending == 0
    assert len(harness.bus.for_run(TRACE_BRAIN_RUN_COMPLETED, first.run_id)) == 1
    assert harness.scheduler.run_record(second.run_id) is not None
    assert harness.scheduler.run_record(first.run_id) is None

    reaper = harness.scheduler._reaper
    reaper.cancel()
    with pytest.raises(asyncio.CancelledError):
        await reaper

    # No duplicated terminal events: the first item was not finished a
    # second time after its eviction. The second's own publication was the
    # one cancelled — its record stands, its trace is the documented loss.
    assert len(harness.bus.for_run(TRACE_BRAIN_RUN_COMPLETED, first.run_id)) == 1
    assert harness.bus.for_run(TRACE_BRAIN_RUN_COMPLETED, second.run_id) == []
    assert harness.counters.get(COUNTER_STALE_DROP) == 2
    record = harness.scheduler.run_record(second.run_id)
    assert record is not None
    assert (record.status, record.reason) == ("timeout", REASON_STALE_DROP)

    await harness.scheduler.aclose()

    # The held run is accounted for exactly once as well: one terminal event
    # per *published* item, one record per accepted item, no orphaned work.
    assert len(harness.bus.for_run(TRACE_BRAIN_RUN_COMPLETED, held.run_id)) == 1
    assert len(harness.bus.of_type(TRACE_BRAIN_RUN_COMPLETED)) == 2
    assert harness.counters.get(COUNTER_STALE_DROP) == 2
    assert harness.scheduler.pending == 0
    assert harness.scheduler.active_runs == 0
    assert harness.scheduler.live_sessions == 0


async def test_shutdown_inside_deadline_cleanup_keeps_the_started_run_accounting():
    """F9 review: a run cancelled while being abandoned keeps its real counts.

    The total deadline expires with the body in flight and its activity
    recorded; shutdown then lands inside ``_abandon``'s bounded courtesy,
    outside the inner cancellation handler. The terminal record must keep
    the started state and those counts, not restart as an unstarted
    ``stale_drop`` with zeros.
    """

    abandoned = asyncio.Event()

    async def run_body(context):
        context.note_model_call(2)
        context.note_send(1)
        swallowed = False
        while True:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                if swallowed:
                    raise
                # First boundary cancellation: the worker is inside
                # ``_abandon`` now, waiting for this body to stop.
                swallowed = True
                abandoned.set()

    harness = Harness(run_body, workers=1, total_run_seconds=10.0, wait_seconds=5.0)
    await harness.scheduler.start()
    admitted = harness.scheduler.admit(SESSION_A, message("busy"))
    await settle()
    assert harness.scheduler.active_runs == 1

    harness.clock.advance(11.0)
    await abandoned.wait()
    await harness.scheduler.aclose()

    record = harness.scheduler.run_record(admitted.run_id)
    assert record is not None
    assert (record.status, record.reason) == ("cancelled", REASON_CANCELLED)
    assert record.started is True
    assert (record.model_calls, record.sends) == (2, 1)

    completed = harness.bus.for_run(TRACE_BRAIN_RUN_COMPLETED, admitted.run_id)
    assert len(completed) == 1
    assert completed[0]["status"] == "cancelled"
    assert completed[0]["started"] is True
    assert (completed[0]["model_calls"], completed[0]["sends"]) == (2, 1)

    # The run had started; it was never a stale drop, and nothing is orphaned.
    assert harness.counters.get(COUNTER_STALE_DROP) == 0
    assert harness.scheduler.pending == 0
    assert harness.scheduler.active_runs == 0
    assert harness.scheduler.live_sessions == 0


async def test_a_blocked_run_started_publication_cannot_outlive_the_total_deadline():
    """R2, AC33: the run budget bounds supervision exactly as it bounds the body."""

    body = RunSpy()
    async with Harness(body, workers=1, total_run_seconds=10.0) as harness:
        published = harness.supervision.emit

        async def blocking_emit(event_type, payload):
            if event_type == TRACE_BRAIN_RUN_STARTED:
                await asyncio.Event().wait()
            await published(event_type, payload)

        harness.supervision.emit = blocking_emit

        admitted = harness.scheduler.admit(SESSION_A, message("blocked"))
        await settle()
        # The worker is held inside the publication; the body was never entered
        # and the reaper cannot see an item that has already been dequeued.
        assert body.started == []
        assert harness.scheduler.active_runs == 1

        harness.clock.advance(11.0)
        await settle()

        record = harness.scheduler.run_record(admitted.run_id)
        assert record is not None
        assert (record.status, record.reason) == ("timeout", REASON_RUN_DEADLINE)
        # Out of budget before the body, so the body is never entered at all.
        assert body.started == []
        assert record.sends == 0
        assert len(harness.bus.for_run(TRACE_BRAIN_RUN_COMPLETED, admitted.run_id)) == 1
        # The abandoned publication is a lost trace, not a held worker.
        assert harness.counters.get(COUNTER_LOST_TRACES) >= 1
        assert harness.counters.get(COUNTER_RUN_DEADLINE_EXPIRIES) == 1

        harness.supervision.emit = published
        harness.scheduler.admit(SESSION_B, message("after"))
        await settle()
        assert body.finished == ["after"]
        assert harness.scheduler.active_runs == 0


async def test_outstanding_trace_publications_are_bounded_under_a_stalled_supervision():
    """R2, R6: every rejection traces, so the trace set is capped like the queues."""

    body = RunSpy()
    body.hold_all = True
    harness = Harness(
        body,
        workers=1,
        session_queue_capacity=1,
        global_pending_capacity=1,
        max_pending_traces=2,
    )
    await harness.scheduler.start()

    gate = asyncio.Event()
    published = harness.supervision.emit

    async def stalled_emit(event_type, payload):
        await gate.wait()
        await published(event_type, payload)

    harness.supervision.emit = stalled_emit

    harness.scheduler.admit(SESSION_A, message("first"))
    for index in range(20):
        rejected = harness.scheduler.admit(SESSION_A, message(f"drop-{index}"))
        assert rejected.accepted is False
        assert rejected.replay_promised is False
    await settle()

    # Every queue cap held, and so did the one surface behind them.
    assert harness.scheduler.pending <= 1
    assert len(harness.scheduler._traces) <= 2
    assert harness.counters.get(COUNTER_LOST_TRACES) >= 19

    gate.set()
    await settle()
    await harness.scheduler.aclose()


async def test_a_body_that_swallows_its_cancellation_does_not_hold_the_worker():
    """R2, AC33: the boundary bound bounds the cancellation that follows it too."""

    release = asyncio.Event()
    entered: list[str] = []
    swallowed: list[str] = []

    async def run_body(context):
        entered.append(context.run_id)
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                # Refuses to stop, which is what the bound exists for.
                swallowed.append(context.run_id)
        return RunOutcome()

    async with Harness(run_body, workers=1, total_run_seconds=10.0) as harness:
        try:
            stuck = harness.scheduler.admit(SESSION_A, message("stuck"))
            await settle()
            assert entered == [stuck.run_id]

            harness.clock.advance(11.0)
            await settle()

            assert swallowed.count(stuck.run_id) >= 1
            record = harness.scheduler.run_record(stuck.run_id)
            assert record is not None
            assert (record.status, record.reason) == ("timeout", REASON_RUN_DEADLINE)
            assert (
                len(harness.bus.for_run(TRACE_BRAIN_RUN_COMPLETED, stuck.run_id)) == 1
            )

            # The worker and the session were let go rather than held on it.
            assert harness.scheduler.active_runs == 0
            following = harness.scheduler.admit(SESSION_B, message("next"))
            await settle()
            assert entered == [stuck.run_id, following.run_id]
        finally:
            # Only the test can end a body that answers no cancellation; the
            # scheduler stopped waiting on it, which is the whole point.
            release.set()
            await settle()


async def test_an_invalid_run_outcome_ends_the_run_and_not_the_worker():
    """R8: an unsupported return value is the body's failure, never the pool's."""

    entered: list[str] = []

    async def run_body(context):
        entered.append(context.run_id)
        if len(entered) == 1:
            return {"status": "success"}
        return RunOutcome(status="success", delivery="delivered", sends=1)

    async with Harness(run_body, workers=1) as harness:
        invalid = harness.scheduler.admit(SESSION_A, message("nonsense"))
        await settle()

        record = harness.scheduler.run_record(invalid.run_id)
        assert record is not None
        assert (record.status, record.reason) == ("error", REASON_RUN_FAILED)
        completed = harness.bus.for_run(TRACE_BRAIN_RUN_COMPLETED, invalid.run_id)
        assert len(completed) == 1
        assert "dict" in completed[0]["error"]

        # The single worker is still there to take the next admitted item.
        following = harness.scheduler.admit(SESSION_B, message("sane"))
        await settle()
        good = harness.scheduler.run_record(following.run_id)
        assert good is not None
        assert (good.status, good.reason) == ("success", REASON_COMPLETED)
        assert entered == [invalid.run_id, following.run_id]


# --------------------------------------------------------------------------- #
# R6 — the run's end releases what the run leased (P24 F5)
# --------------------------------------------------------------------------- #


LEASE_LIMITS = {
    "max_object_bytes": 16,
    "max_objects": 64,
    "max_total_bytes": 1024,
    "max_bytes_per_run": 64,
    "ttl_seconds": 1_000_000.0,
}
"""Generous bounds and a time-to-live no test reaches: only the run's end frees."""


class LeasingBody:
    """A run body that leases attachments under its own run identity.

    What the body does after leasing is chosen per session: it finishes, it
    fails, or it holds inside its "model" until the test lets it go or the
    scheduler ends it — the four terminal paths F5 names.
    """

    def __init__(self, store: AttachmentStore) -> None:
        self.store = store
        self.gates: dict[str, asyncio.Event] = {}
        self.leased: dict[str, list] = {}

    async def __call__(self, context):
        run_id = context.run_id
        self.leased[run_id] = [
            self.store.put(run_id, b"x" * 16, content_type="image/png"),
            self.store.put(run_id, b"y" * 16, content_type="image/png"),
        ]
        text = context.work.payload["text"]
        if text == "fail":
            raise RuntimeError("the body gave up")
        if text == "hold":
            await self.gates.setdefault(run_id, asyncio.Event()).wait()
        return RunOutcome(status="success", delivery="delivered", sends=1)


def _leasing_harness(**settings) -> tuple[Harness, AttachmentStore, LeasingBody]:
    """A scheduler whose ``run_cleanup`` is the store's own ``release``."""

    bound: dict[str, Harness] = {}
    store = AttachmentStore(clock=lambda: bound["harness"].clock(), **LEASE_LIMITS)
    body = LeasingBody(store)
    harness = Harness(body, run_cleanup=store.release, **settings)
    bound["harness"] = harness
    return harness, store, body


def _assert_released(store: AttachmentStore, body: LeasingBody, run_id: str) -> None:
    assert store.usage(run_id) == RunUsage(objects=0, total_bytes=0)
    assert run_id not in store.live_runs()
    for ref in body.leased[run_id]:
        with pytest.raises(AttachmentUnknown):
            store.get(ref)


async def test_a_completed_or_failed_run_releases_exactly_its_own_leases():
    """R6/AC23/AC31 (P24 F5): a genuinely admitted run that leased 2 objects
    has 0 leases once its terminal record is written — on success and on a
    body failure alike — with nothing in the test calling ``release``. An
    unrelated run's leases, and a queued sibling's, survive untouched, and
    the freed run identity can lease its full quota again."""

    harness, store, body = _leasing_harness(workers=1)
    bystander = store.put("run-bystander", b"b" * 16, content_type="image/png")
    async with harness:
        done = harness.scheduler.admit(SESSION_A, message("done"))
        failed = harness.scheduler.admit(SESSION_B, message("fail"))
        await settle()

        for admitted, status, reason in (
            (done, "success", REASON_COMPLETED),
            (failed, "error", REASON_RUN_FAILED),
        ):
            record = harness.scheduler.run_record(admitted.run_id)
            assert record is not None
            assert (record.status, record.reason) == (status, reason)
            assert len(body.leased[admitted.run_id]) == 2
            _assert_released(store, body, admitted.run_id)
            completed = harness.bus.for_run(TRACE_BRAIN_RUN_COMPLETED, admitted.run_id)
            assert len(completed) == 1

        # Exactly the two runs' objects went; the bystander's did not.
        assert store.usage("run-bystander") == RunUsage(objects=1, total_bytes=16)
        assert store.get(bystander) == b"b" * 16
        assert store.object_count == 1
        assert store.total_bytes == 16
        assert store.live_runs() == ("run-bystander",)

        # The identity is clean: its full quota is available again.
        for _ in range(4):
            store.put(done.run_id, b"z" * 16, content_type="image/png")
        assert store.usage(done.run_id) == RunUsage(objects=4, total_bytes=64)
        store.release(done.run_id)

    # Not one wait for real time anywhere in the scenario.
    assert harness.clock.now == 0.0


async def test_a_run_ended_by_its_deadline_or_by_shutdown_releases_its_leases():
    """R6 (P24 F5): the deadline expiry of a run held inside its model frees
    its 2 leases at the terminal record, while a concurrent held run keeps
    its 2; closing the scheduler then cancels that run and frees its 2 as
    well, and a queued item that never started ends with 0 leases to free
    — every terminal path releases, and none touches another run."""

    harness, store, body = _leasing_harness(workers=2, total_run_seconds=10.0)
    async with harness:
        expiring = harness.scheduler.admit(SESSION_A, message("hold"))
        await settle()
        harness.clock.advance(5.0)
        surviving = harness.scheduler.admit(SESSION_B, message("hold"))
        queued = harness.scheduler.admit(SESSION_B, message("never"))
        await settle()
        assert harness.scheduler.active_runs == 2
        assert harness.scheduler.pending == 1
        assert store.usage(expiring.run_id) == RunUsage(objects=2, total_bytes=32)
        assert store.usage(surviving.run_id) == RunUsage(objects=2, total_bytes=32)
        assert store.object_count == 4

        # The first run's total deadline passes; the second's does not.
        harness.clock.advance(6.0)
        await settle()
        record = harness.scheduler.run_record(expiring.run_id)
        assert record is not None
        assert (record.status, record.reason) == ("timeout", REASON_RUN_DEADLINE)
        _assert_released(store, body, expiring.run_id)
        assert store.usage(surviving.run_id) == RunUsage(objects=2, total_bytes=32)
        assert store.object_count == 2
        assert store.live_runs() == (surviving.run_id,)

    # Shutdown ended the held run and the queued item; each is recorded once.
    cancelled = harness.scheduler.run_record(surviving.run_id)
    assert cancelled is not None
    assert cancelled.status == "cancelled"
    _assert_released(store, body, surviving.run_id)
    never = harness.scheduler.run_record(queued.run_id)
    assert never is not None
    assert (never.status, never.reason, never.started) == (
        "cancelled",
        REASON_CANCELLED,
        False,
    )
    assert queued.run_id not in body.leased
    assert store.usage(queued.run_id) == RunUsage(objects=0, total_bytes=0)
    assert store.object_count == 0
    assert store.total_bytes == 0
    assert store.live_runs() == ()
    for run_id in (expiring.run_id, surviving.run_id, queued.run_id):
        assert len(harness.bus.for_run(TRACE_BRAIN_RUN_COMPLETED, run_id)) == 1


async def test_a_stale_dropped_item_and_a_failing_cleanup_never_break_the_record():
    """R6/R8 (P24 F5): the cleanup runs for a stale-dropped item too — it is
    a terminal path, with nothing leased — and a cleanup that raises is
    contained: the record and its one trace still land, and the next run's
    cleanup is still called."""

    calls: list[str] = []

    def cleanup(run_id: str) -> None:
        calls.append(run_id)
        if len(calls) == 1:
            raise RuntimeError("store unavailable")

    body = RunSpy(FakeModel())
    async with Harness(
        body, workers=1, wait_seconds=5.0, run_cleanup=cleanup
    ) as harness:
        holder = harness.scheduler.admit(HOLDER, message("holder"))
        await settle()
        stale = harness.scheduler.admit(SESSION_A, message("stale"))
        await settle()
        harness.clock.advance(6.0)
        await settle()

        record = harness.scheduler.run_record(stale.run_id)
        assert record is not None
        assert (record.status, record.reason) == ("timeout", REASON_STALE_DROP)
        assert calls == [stale.run_id]
        assert len(harness.bus.for_run(TRACE_BRAIN_RUN_COMPLETED, stale.run_id)) == 1

        body.model.release()
        await settle()
        assert harness.scheduler.run_record(holder.run_id) is not None
        assert calls == [stale.run_id, holder.run_id]
    # Exactly once per run, and never again for a run already recorded.
    assert calls == [stale.run_id, holder.run_id]


def test_a_non_callable_run_cleanup_is_refused_by_name():
    with pytest.raises(ContractError) as caught:
        AdmissionScheduler(lambda _context: None, run_cleanup="release")  # type: ignore[arg-type]
    assert "run_cleanup" in str(caught.value)


# --------------------------------------------------------------------------- #
# R3 — the stale-drop hook (AC19 groundwork)
# --------------------------------------------------------------------------- #
#
# The four constructors above run unchanged: ``on_stale_drop`` is optional and
# the scheduler without it drops stale work exactly as before. With it, the
# scheduler hands over the work, the session key and the budget left — and
# nothing else: it never learns what a fallback is.


class StaleDropHook:
    """Records every call, can answer, raise or hold, sync or async."""

    def __init__(
        self,
        result=None,
        *,
        raises: BaseException | None = None,
        hold: bool = False,
        synchronous: bool = False,
    ) -> None:
        self.result = result
        self.raises = raises
        self.hold = hold
        self.synchronous = synchronous
        self.calls: list[tuple[Work, SessionKey, float]] = []
        self.completed_at_call: list[list[dict]] = []
        self.cancelled = 0
        self.bus: FakeBus | None = None

    def _observe(self, work, session_key, remaining) -> None:
        self.calls.append((work, session_key, remaining))
        if self.bus is not None:
            self.completed_at_call.append(self.bus.of_type(TRACE_BRAIN_RUN_COMPLETED))

    def _answer(self):
        if self.raises is not None:
            raise self.raises
        return self.result

    def __call__(self, work, session_key, remaining):
        self._observe(work, session_key, remaining)
        if self.synchronous:
            return self._answer()
        return self._settle()

    async def _settle(self):
        if self.hold:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled += 1
                raise
        return self._answer()


def _hooked_harness(hook: StaleDropHook, **settings) -> Harness:
    body = RunSpy(FakeModel())
    options = {"workers": 1, "wait_seconds": 5.0, "total_run_seconds": 60.0}
    options.update(settings)
    harness = Harness(body, on_stale_drop=hook, **options)
    hook.bus = harness.bus
    return harness


async def test_the_stale_drop_hook_gets_the_work_session_and_remaining_budget_once():
    """R3/AC19 groundwork: exactly one call per stale item with ``(work,
    session_key, remaining)``, the remaining budget being the total budget
    minus the wait already spent; the call precedes the item's single
    ``brain.run.completed``, and the mapping it returns rides in that trace
    and in the record."""

    hook = StaleDropHook(
        {
            "fallback": "sent",
            "delivery": "fallback:success",
            "deliveries": [{"name": "chat.send", "status": "success"}],
        }
    )
    async with _hooked_harness(hook) as harness:
        holder = harness.scheduler.admit(HOLDER, message("holder"))
        await settle()
        stale_a = message("stale-a")
        stale_b = message("stale-b")
        queued_a = harness.scheduler.admit(SESSION_A, stale_a)
        harness.clock.advance(2.0)
        queued_b = harness.scheduler.admit(SESSION_B, stale_b)
        await settle()
        assert hook.calls == []

        # 6 units after A was admitted, 4 after B: only A expired its wait.
        harness.clock.advance(4.0)
        await settle()
        assert len(hook.calls) == 1
        work, session_key, remaining = hook.calls[0]
        assert work is stale_a
        assert session_key == SESSION_A
        # 60 units of total budget, 6 of them already spent waiting.
        assert remaining == pytest.approx(54.0)
        # Hook before publication: the trace was not out when it ran.
        assert not any(
            payload["run_id"] == queued_a.run_id for payload in hook.completed_at_call[0]
        )

        record = harness.scheduler.run_record(queued_a.run_id)
        assert record is not None
        assert (record.status, record.reason) == ("timeout", REASON_STALE_DROP)
        assert record.started is False
        assert record.delivery == "fallback:success"
        assert record.correlation["fallback"] == "sent"
        assert record.correlation["deliveries"] == (
            {"name": "chat.send", "status": "success"},
        )
        completed = harness.bus.for_run(TRACE_BRAIN_RUN_COMPLETED, queued_a.run_id)
        assert len(completed) == 1
        assert completed[0]["status"] == "timeout"
        assert completed[0]["reason"] == REASON_STALE_DROP
        assert completed[0]["fallback"] == "sent"
        assert completed[0]["delivery"] == "fallback:success"
        assert completed[0]["deliveries"] == ({"name": "chat.send", "status": "success"},)
        assert completed[0]["model_calls"] == 0
        assert completed[0]["sends"] == 0

        # B expires in its turn: one more call, with its own budget.
        harness.clock.advance(2.0)
        await settle()
        assert len(hook.calls) == 2
        assert hook.calls[1][0] is stale_b
        assert hook.calls[1][1] == SESSION_B
        assert hook.calls[1][2] == pytest.approx(54.0)
        assert len(harness.bus.for_run(TRACE_BRAIN_RUN_COMPLETED, queued_b.run_id)) == 1

        # The run that did start never reaches the hook.
        harness.scheduler._run_body.model.release()
        await settle()
        assert harness.scheduler.run_record(holder.run_id).status == "success"
        assert len(hook.calls) == 2
        assert harness.counters.get(COUNTER_STALE_DROP) == 2
        assert harness.counters.get(COUNTER_STALE_DROP_HOOK_FAILURES) == 0
        assert harness.scheduler.stale_drop_hook_failures == 0


@pytest.mark.parametrize("synchronous", [False, True], ids=["async", "sync"])
async def test_a_raising_stale_drop_hook_leaves_the_record_and_trace_intact_and_counts_once(
    synchronous,
):
    """R3: an exception in the hook is counted and diagnosed, never raised —
    the ``stale_drop`` record and its one ``brain.run.completed`` stand, the
    reaper survives, and the next stale item is still dropped."""

    hook = StaleDropHook(raises=RuntimeError("fallback exploded"), synchronous=synchronous)
    async with _hooked_harness(hook) as harness:
        harness.scheduler.admit(HOLDER, message("holder"))
        await settle()
        queued = harness.scheduler.admit(SESSION_A, message("stale"))
        await settle()
        harness.clock.advance(6.0)
        await settle()

        assert len(hook.calls) == 1
        record = harness.scheduler.run_record(queued.run_id)
        assert record is not None
        assert (record.status, record.reason) == ("timeout", REASON_STALE_DROP)
        assert record.waited_seconds == pytest.approx(6.0)
        assert record.delivery is None
        assert record.correlation["stale_drop_hook"] == "failed"
        completed = harness.bus.for_run(TRACE_BRAIN_RUN_COMPLETED, queued.run_id)
        assert len(completed) == 1
        assert completed[0]["status"] == "timeout"
        assert completed[0]["reason"] == REASON_STALE_DROP
        assert completed[0]["stale_drop_hook"] == "failed"
        assert completed[0]["stale_drop_hook_error"] == "RuntimeError: fallback exploded"
        assert harness.counters.get(COUNTER_STALE_DROP) == 1
        assert harness.counters.get(COUNTER_STALE_DROP_HOOK_FAILURES) == 1
        assert harness.scheduler.stale_drop_hook_failures == 1

        # The reaper is still sweeping: a second stale item is dropped too.
        later = harness.scheduler.admit(SESSION_B, message("later"))
        await settle()
        harness.clock.advance(6.0)
        await settle()
        assert len(hook.calls) == 2
        assert harness.scheduler.run_record(later.run_id).reason == REASON_STALE_DROP
        assert harness.counters.get(COUNTER_STALE_DROP_HOOK_FAILURES) == 2
    assert harness.scheduler.stale_drop_hook_failures == 2


async def test_a_stale_drop_hook_overrunning_its_budget_is_abandoned_at_the_total_deadline():
    """R3: the hook runs inside the reaper and is bounded by the remaining
    total budget — one that never returns is cancelled at the total deadline,
    diagnosed and counted, the record is published, and the sweep goes on."""

    hook = StaleDropHook({"fallback": "sent"}, hold=True)
    async with _hooked_harness(hook, wait_seconds=5.0, total_run_seconds=10.0) as harness:
        harness.scheduler.admit(HOLDER, message("holder"))
        await settle()
        queued = harness.scheduler.admit(SESSION_A, message("stale"))
        await settle()
        harness.clock.advance(6.0)
        await settle()

        assert len(hook.calls) == 1
        assert hook.calls[0][2] == pytest.approx(4.0)
        # Held inside the hook: not yet recorded, not yet published.
        assert harness.scheduler.run_record(queued.run_id) is None
        assert harness.bus.for_run(TRACE_BRAIN_RUN_COMPLETED, queued.run_id) == []

        # The total deadline: the hook is let go, cancelled, and the record
        # is written and published without what it never answered.
        harness.clock.advance(4.0)
        await settle()
        assert hook.cancelled == 1
        record = harness.scheduler.run_record(queued.run_id)
        assert record is not None
        assert (record.status, record.reason) == ("timeout", REASON_STALE_DROP)
        assert record.correlation["stale_drop_hook"] == "abandoned"
        assert "fallback" not in record.correlation
        completed = harness.bus.for_run(TRACE_BRAIN_RUN_COMPLETED, queued.run_id)
        assert len(completed) == 1
        assert completed[0]["stale_drop_hook"] == "abandoned"
        assert "fallback" not in completed[0]
        assert harness.counters.get(COUNTER_STALE_DROP_HOOK_FAILURES) == 1

        # The sweep was stalled by nothing: the next stale item is reached.
        # (The first holder met its own total deadline at 10; a fresh one
        # keeps the single worker busy so ``later`` has to wait.)
        hook.hold = False
        harness.scheduler.admit(HOLDER, message("holder-2"))
        await settle()
        assert harness.scheduler.active_runs == 1
        later = harness.scheduler.admit(SESSION_B, message("later"))
        await settle()
        harness.clock.advance(6.0)
        await settle()
        assert len(hook.calls) == 2
        record = harness.scheduler.run_record(later.run_id)
        assert record.reason == REASON_STALE_DROP
        assert record.correlation["fallback"] == "sent"


class GatedStaleDropHook:
    """Answers at once, except for the sessions it holds until ``release``."""

    def __init__(self, *held: SessionKey) -> None:
        self.held = set(held)
        self.gate = asyncio.Event()
        self.calls: list[tuple[Work, SessionKey, float]] = []
        self.cancelled: list[SessionKey] = []

    def release(self) -> None:
        self.gate.set()

    async def __call__(self, work, session_key, remaining):
        self.calls.append((work, session_key, remaining))
        if session_key in self.held:
            try:
                await self.gate.wait()
            except asyncio.CancelledError:
                self.cancelled.append(session_key)
                raise
        return {"fallback": f"sent:{session_key.viewer_id}"}


async def test_stale_items_of_one_sweep_run_their_hooks_side_by_side():
    """R3 review: one sweep's hooks are bounded by their own budgets, not by
    each other's — a hook holding item A's whole budget neither delays item
    B's call nor spends B's budget, and B is recorded while A is still held."""

    hook = GatedStaleDropHook(SESSION_A)
    async with _hooked_harness(hook, wait_seconds=5.0, total_run_seconds=10.0) as harness:
        harness.scheduler.admit(HOLDER, message("holder"))
        await settle()
        queued_a = harness.scheduler.admit(SESSION_A, message("stale-a"))
        queued_b = harness.scheduler.admit(SESSION_B, message("stale-b"))
        await settle()

        # Both expire in the same sweep, with the same 5 units left.
        harness.clock.advance(6.0)
        await settle()
        assert [(key, remaining) for _work, key, remaining in hook.calls] == [
            (SESSION_A, pytest.approx(4.0)),
            (SESSION_B, pytest.approx(4.0)),
        ]
        # A is held; B is already through, with its answer.
        assert harness.scheduler.run_record(queued_a.run_id) is None
        record_b = harness.scheduler.run_record(queued_b.run_id)
        assert record_b is not None
        assert record_b.reason == REASON_STALE_DROP
        assert record_b.correlation["fallback"] == "sent:viewer-b"
        assert len(harness.bus.for_run(TRACE_BRAIN_RUN_COMPLETED, queued_b.run_id)) == 1

        # A is let go at its own total deadline, exactly as before.
        harness.clock.advance(4.0)
        await settle()
        assert hook.cancelled == [SESSION_A]
        record_a = harness.scheduler.run_record(queued_a.run_id)
        assert record_a is not None
        assert record_a.correlation["stale_drop_hook"] == "abandoned"
        assert len(harness.bus.for_run(TRACE_BRAIN_RUN_COMPLETED, queued_a.run_id)) == 1
        assert harness.counters.get(COUNTER_STALE_DROP) == 2
        assert harness.counters.get(COUNTER_STALE_DROP_HOOK_FAILURES) == 1


async def test_a_sweep_runs_at_most_stale_drop_concurrency_hooks_at_once():
    """R3 review: the fan-out is bounded — with two slots and two hooks held,
    the third item waits for a slot, and takes its budget from the instant
    its turn comes rather than from the sweep that claimed it."""

    hook = GatedStaleDropHook(SESSION_A, SESSION_B)
    async with _hooked_harness(
        hook, wait_seconds=5.0, total_run_seconds=10.0, stale_drop_concurrency=2
    ) as harness:
        harness.scheduler.admit(HOLDER, message("holder"))
        await settle()
        queued = [
            harness.scheduler.admit(session, message(f"stale-{session.viewer_id}"))
            for session in (SESSION_A, SESSION_B, SESSION_C)
        ]
        await settle()

        harness.clock.advance(6.0)
        await settle()
        assert [key for _work, key, _remaining in hook.calls] == [SESSION_A, SESSION_B]
        assert all(harness.scheduler.run_record(item.run_id) is None for item in queued)

        # Two units later both slots free up: C is called with what is left
        # of *its* budget at that instant, and everything is recorded once.
        harness.clock.advance(2.0)
        hook.release()
        await settle()
        assert [key for _work, key, _remaining in hook.calls] == [
            SESSION_A,
            SESSION_B,
            SESSION_C,
        ]
        assert hook.calls[2][2] == pytest.approx(2.0)
        assert hook.cancelled == []
        for item, session in zip(queued, (SESSION_A, SESSION_B, SESSION_C)):
            record = harness.scheduler.run_record(item.run_id)
            assert record is not None
            assert record.reason == REASON_STALE_DROP
            assert record.correlation["fallback"] == f"sent:{session.viewer_id}"
            assert len(harness.bus.for_run(TRACE_BRAIN_RUN_COMPLETED, item.run_id)) == 1
        assert harness.counters.get(COUNTER_STALE_DROP) == 3
        assert harness.counters.get(COUNTER_STALE_DROP_HOOK_FAILURES) == 0


@pytest.mark.parametrize("stale_drop_concurrency", [0, None, 1.5, True])
def test_a_stale_drop_concurrency_that_is_not_a_positive_count_is_refused_by_name(
    stale_drop_concurrency,
):
    with pytest.raises(ContractError) as caught:
        Harness(lambda _context: None, stale_drop_concurrency=stale_drop_concurrency)
    assert "stale_drop_concurrency" in str(caught.value)


async def test_shutdown_inside_a_held_stale_drop_hook_counts_the_drop_once():
    """F9 review: shutdown landing inside the hook finishes the item through
    the orphan path — one ``stale_drop`` record, one trace, and the drop
    counted there and only there, never once before the hook and once again."""

    hook = StaleDropHook({"fallback": "sent"}, hold=True)
    harness = _hooked_harness(hook, wait_seconds=5.0, total_run_seconds=60.0)
    await harness.scheduler.start()
    harness.scheduler.admit(HOLDER, message("holder"))
    await settle()
    queued = harness.scheduler.admit(SESSION_A, message("stale"))
    await settle()
    harness.clock.advance(6.0)
    await settle()
    assert len(hook.calls) == 1
    assert harness.scheduler.run_record(queued.run_id) is None

    await harness.scheduler.aclose()

    assert hook.cancelled == 1
    record = harness.scheduler.run_record(queued.run_id)
    assert record is not None
    assert (record.status, record.reason) == ("timeout", REASON_STALE_DROP)
    assert record.started is False
    assert "fallback" not in record.correlation
    assert len(harness.bus.for_run(TRACE_BRAIN_RUN_COMPLETED, queued.run_id)) == 1
    assert harness.counters.get(COUNTER_STALE_DROP) == 1
    assert harness.counters.get(COUNTER_STALE_DROP_HOOK_FAILURES) == 0
    assert harness.scheduler.stale_drop_hook_failures == 0


async def test_a_stale_drop_at_a_spent_total_budget_hands_the_hook_zero_seconds():
    """R2/R3: the wait is clamped to the total, so a drop can meet a budget of
    exactly 0; the hook is still called once, with ``0.0``, and a synchronous
    answer is merged — nothing can be awaited past the total deadline."""

    hook = StaleDropHook({"fallback": "skipped:deadline_exceeded"}, synchronous=True)
    async with _hooked_harness(hook, wait_seconds=10.0, total_run_seconds=10.0) as harness:
        harness.scheduler.admit(HOLDER, message("holder"))
        await settle()
        queued = harness.scheduler.admit(SESSION_A, message("stale"))
        await settle()
        harness.clock.advance(11.0)
        await settle()

        assert len(hook.calls) == 1
        assert hook.calls[0][2] == 0.0
        record = harness.scheduler.run_record(queued.run_id)
        assert record is not None
        assert record.reason == REASON_STALE_DROP
        assert record.correlation["fallback"] == "skipped:deadline_exceeded"
        assert harness.scheduler.stale_drop_hook_failures == 0


@pytest.mark.parametrize(
    "result",
    [None, "sent", {"status": "success", "run_id": "forged", "delivery": 7, "sends": 3}],
    ids=["none", "text", "reserved-fields"],
)
async def test_a_stale_drop_hook_result_that_is_not_a_correlation_changes_nothing(result):
    """R8: only a mapping is merged, and the scheduler's own trace fields
    (status, run_id, sends, …) can no more be overwritten by the hook than by
    a run body; a non-text ``delivery`` is ignored rather than recorded."""

    hook = StaleDropHook(result)
    async with _hooked_harness(hook) as harness:
        harness.scheduler.admit(HOLDER, message("holder"))
        await settle()
        queued = harness.scheduler.admit(SESSION_A, message("stale"))
        await settle()
        harness.clock.advance(6.0)
        await settle()

        assert len(hook.calls) == 1
        record = harness.scheduler.run_record(queued.run_id)
        assert record is not None
        assert (record.status, record.reason) == ("timeout", REASON_STALE_DROP)
        assert record.delivery is None
        assert dict(record.correlation) == {}
        completed = harness.bus.for_run(TRACE_BRAIN_RUN_COMPLETED, queued.run_id)
        assert len(completed) == 1
        assert completed[0]["status"] == "timeout"
        assert completed[0]["run_id"] == queued.run_id
        assert completed[0]["sends"] == 0
        assert completed[0]["delivery"] is None
        assert harness.scheduler.stale_drop_hook_failures == 0


async def test_without_a_hook_a_stale_drop_reports_nothing_extra():
    """The default: no hook, an empty correlation, and no hook counter declared
    on the registry — the phase-0 stale drop (AC8) is untouched."""

    body = RunSpy(FakeModel())
    async with Harness(body, workers=1, wait_seconds=5.0) as harness:
        harness.scheduler.admit(HOLDER, message("holder"))
        await settle()
        queued = harness.scheduler.admit(SESSION_A, message("stale"))
        await settle()
        harness.clock.advance(6.0)
        await settle()

        record = harness.scheduler.run_record(queued.run_id)
        assert record is not None
        assert record.reason == REASON_STALE_DROP
        assert dict(record.correlation) == {}
        assert COUNTER_STALE_DROP_HOOK_FAILURES not in harness.counters
        assert harness.scheduler.stale_drop_hook_failures == 0
        (completed,) = harness.bus.for_run(TRACE_BRAIN_RUN_COMPLETED, queued.run_id)
        assert "stale_drop_hook" not in completed


def test_a_non_callable_stale_drop_hook_is_refused_by_name():
    with pytest.raises(ContractError) as caught:
        AdmissionScheduler(lambda _context: None, on_stale_drop="fallback")  # type: ignore[arg-type]
    assert "on_stale_drop" in str(caught.value)
