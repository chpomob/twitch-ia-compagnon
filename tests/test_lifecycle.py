"""Phase lifecycle coordination and supervised task ownership (R4, AC13-AC15).

Nothing here waits on wall-clock time. :class:`Timeline` is the injected clock
and the injected sleeper: a deadline fires because the test advances the
timeline, never because a real duration elapsed. The one suite member that
runs the real entry point — the fourth fictional module of AC13 — waits on
loop turns, not on a duration.
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

import core.main as application
from conftest import wait_until
from core.lifecycle import (
    DEFAULT_SHUTDOWN_DEADLINE_SECONDS,
    PHASES,
    PHASE_CLOSE,
    PHASE_CLOSE_OBSERVATION,
    PHASE_DRAIN,
    PHASE_FLUSH,
    PHASE_PREPARE,
    PHASE_READINESS_BARRIER,
    PHASE_START_INPUTS,
    PHASE_STOP_INPUTS,
    PHASE_VALIDATE_SETTINGS,
    ROLE_INPUT,
    ROLE_OBSERVATION,
    BarrierViolation,
    LifecycleError,
    PhaseCoordinator,
    ProcessWatchdog,
    ReadinessBarrier,
    SupervisedTasks,
    arm_shutdown_watchdog,
    close_modules,
    install_shutdown_watchdog,
    module_roles,
    reset_shutdown_watchdog,
)
from core.loader import ModuleActivation
from test_main import _finite_limits


# --------------------------------------------------------------------------- #
# Injected time
# --------------------------------------------------------------------------- #


class Timeline:
    """An injected monotonic clock whose sleepers only fire when advanced."""

    def __init__(self) -> None:
        self.now = 0.0
        self.registered = 0
        self._waiters: list[list[Any]] = []
        self._released = False

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.registered += 1
        if self._released:
            # Every budget is spent the instant it is claimed, and the clock
            # moves by exactly that budget, so a global deadline still bites.
            self.now += seconds
            await asyncio.sleep(0)
            return
        entry = [self.now + seconds, asyncio.get_running_loop().create_future()]
        self._waiters.append(entry)
        try:
            await entry[1]
        finally:
            if entry in self._waiters:
                self._waiters.remove(entry)

    @property
    def pending(self) -> int:
        return len(self._waiters)

    def advance(self, seconds: float) -> None:
        self.now += seconds
        for deadline, future in list(self._waiters):
            if deadline <= self.now and not future.done():
                future.set_result(None)

    def release(self) -> None:
        """Expire every pending timer and every timer claimed from now on."""

        self._released = True
        furthest = max((deadline for deadline, _ in self._waiters), default=self.now)
        self.advance(max(furthest - self.now, 0.0))


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


def hook(events: list[str], label: str, body=None):
    """An async phase hook that records *label* when it runs."""

    async def run(*arguments: Any) -> None:
        events.append(label)
        if body is not None:
            await body(*arguments)

    return run


def module(name: str, *, roles: tuple[str, ...] = (), **hooks: Any) -> ModuleActivation:
    """An activation declaring *roles* in its manifest and exposing *hooks*."""

    return ModuleActivation(
        name=name,
        manifest={"name": name, "lifecycle": {"roles": list(roles)}},
        handle=SimpleNamespace(**hooks),
    )


class Transport:
    """A send transport that refuses to be used once closed."""

    def __init__(self, events: list[str] | None = None, label: str = "sink") -> None:
        self.closed = False
        self.sent: list[str] = []
        self._events = events
        self._label = label

    def send(self, text: str) -> None:
        if self.closed:
            raise RuntimeError("send transport already closed")
        self.sent.append(text)

    async def close(self) -> None:
        self.closed = True
        if self._events is not None:
            self._events.append(f"close:{self._label}")


def coordinator_for(modules, timeline: Timeline, **options) -> PhaseCoordinator:
    options.setdefault("hook_timeout_seconds", 10.0)
    options.setdefault("startup_deadline_seconds", 100.0)
    options.setdefault("shutdown_deadline_seconds", 100.0)
    options.setdefault("drain_deadline_seconds", 50.0)
    return PhaseCoordinator(
        modules, clock=timeline, sleeper=timeline.sleep, **options
    )


# --------------------------------------------------------------------------- #
# AC14 — the readiness barrier
# --------------------------------------------------------------------------- #


async def test_slow_preparation_admits_no_input_and_refuses_an_early_producer() -> None:
    """AC14: 0 inputs reach admission while any module is still preparing."""

    timeline = Timeline()
    preparing = asyncio.Event()
    release = asyncio.Event()
    events: list[str] = []
    admitted: list[int] = []
    refusals: list[str] = []

    async def slow(*_: Any) -> None:
        preparing.set()
        await release.wait()

    store = module("store", prepare=hook(events, "prepare:store", slow))
    feed = module(
        "feed",
        roles=(ROLE_INPUT,),
        prepare=hook(events, "prepare:feed"),
        start_inputs=hook(events, "start:feed"),
    )
    coordinator = coordinator_for([store, feed], timeline)

    start = asyncio.create_task(coordinator.start())
    await asyncio.wait_for(preparing.wait(), 1)

    for index in range(20):
        try:
            coordinator.barrier.guard_admission("feed")
        except BarrierViolation as violation:
            refusals.append(str(violation))
        else:  # pragma: no cover - the assertion below is the real guard
            admitted.append(index)

    assert admitted == []
    assert len(refusals) == 20
    assert refusals[0] == "module 'feed': input refused before the readiness barrier"

    with pytest.raises(BarrierViolation) as early:
        coordinator.barrier.guard_producer("feed")
    assert str(early.value) == (
        "module 'feed': producer started before the readiness barrier"
    )
    assert early.value.module == "feed"
    assert "start:feed" not in events
    assert coordinator.barrier.completed is False

    release.set()
    report = await asyncio.wait_for(start, 1)

    assert report.status == 0
    assert coordinator.barrier.completed is True
    assert coordinator.ready is True
    # The gate now admits, and the producer only started after it opened.
    coordinator.barrier.guard_admission("feed")
    coordinator.barrier.guard_producer("feed")
    assert coordinator.barrier.refused == 21
    assert events[-1] == "start:feed"


async def test_producer_that_cannot_honour_the_barrier_is_refused_by_name() -> None:
    """AC14/R4: a producer with no barrier-aware start is refused, not started."""

    timeline = Timeline()
    events: list[str] = []
    legacy = module("legacy", roles=(ROLE_INPUT,), close=hook(events, "close:legacy"))
    coordinator = coordinator_for([legacy], timeline)

    report = await asyncio.wait_for(coordinator.start(), 1)

    assert report.status == 1
    assert "module 'legacy': cannot honour the readiness barrier" in report.failures
    # The gate never opened, so nothing could be admitted behind the refusal.
    assert coordinator.barrier.completed is False
    assert coordinator.ready is False
    with pytest.raises(BarrierViolation):
        coordinator.barrier.guard_admission("legacy")
    assert events == ["close:legacy"]


async def test_barrier_wait_releases_only_once_preparation_succeeded() -> None:
    barrier = ReadinessBarrier()
    waiter = asyncio.create_task(barrier.wait())
    await asyncio.sleep(0)
    assert not waiter.done()
    assert barrier.admits() is False
    barrier.complete()
    barrier.complete()  # idempotent
    await asyncio.wait_for(waiter, 1)
    assert barrier.admits() is True
    assert barrier.refused == 0


# --------------------------------------------------------------------------- #
# AC15 — detached runs, transports and the drain deadline
# --------------------------------------------------------------------------- #


async def test_detached_run_keeps_send_transport_usable_until_terminal_outcome(
) -> None:
    """AC15: stop keeps outputs open until the detached run publishes."""

    timeline = Timeline()
    events: list[str] = []
    transport = Transport(events)
    stopped = asyncio.Event()
    entered = asyncio.Event()
    release = asyncio.Event()
    outcomes: list[str] = []

    async def stop_body(*_: Any) -> None:
        stopped.set()

    async def detached_run() -> None:
        entered.set()
        await release.wait()
        transport.send("reply")
        outcomes.append("success")
        events.append("terminal:run")

    feed = module(
        "feed",
        roles=(ROLE_INPUT,),
        start_inputs=hook(events, "start:feed"),
        stop_inputs=hook(events, "stop:feed", stop_body),
    )
    sink = module("sink", close=transport.close)
    # Named 'alpha' on purpose: first alphabetically and first in activation
    # order, yet it must flush and close last because its manifest declares it.
    ledger = module(
        "alpha",
        roles=(ROLE_OBSERVATION,),
        flush=hook(events, "flush:alpha"),
        close=hook(events, "close:alpha"),
    )
    coordinator = coordinator_for([ledger, sink, feed], timeline)

    assert (await asyncio.wait_for(coordinator.start(), 1)).status == 0
    coordinator.tasks.spawn(detached_run(), name="run-1", owner="brain")
    await asyncio.wait_for(entered.wait(), 1)

    stop = asyncio.create_task(coordinator.stop())
    await asyncio.wait_for(stopped.wait(), 1)

    # The producer is stopped while the run is still in flight, and every
    # output it may need is still open.
    assert transport.closed is False
    assert coordinator.tasks.active == 1
    assert coordinator.tasks.owners() == ("brain",)

    release.set()
    report = await asyncio.wait_for(stop, 1)

    assert report.status == 0
    assert report.failures == ()
    assert outcomes == ["success"]
    assert transport.sent == ["reply"]
    assert events == [
        "start:feed",
        "stop:feed",
        "terminal:run",
        "close:sink",
        "flush:alpha",
        "close:alpha",
    ]
    assert coordinator.tasks.active == 0


async def test_exhausted_drain_deadline_cancels_the_run_which_still_reports(
) -> None:
    """AC15: an exhausted drain deadline cancels, it does not orphan.

    The run is cancelled while its write is in flight, and the transports it
    needs to classify that write are still open — which is why this never
    contradicts AC33's ``external_unknown``.
    """

    timeline = Timeline()
    events: list[str] = []
    transport = Transport(events)
    entered = asyncio.Event()
    outcomes: list[str] = []

    async def detached_run() -> None:
        entered.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            # The executor can still classify the in-flight call here.
            assert transport.closed is False
            outcomes.append("external_unknown")
            events.append("terminal:run")
            raise

    feed = module(
        "feed",
        roles=(ROLE_INPUT,),
        start_inputs=hook(events, "start:feed"),
        stop_inputs=hook(events, "stop:feed"),
    )
    sink = module("sink", close=transport.close)
    coordinator = coordinator_for(
        [sink, feed], timeline, drain_deadline_seconds=0.0
    )

    assert (await asyncio.wait_for(coordinator.start(), 1)).status == 0
    coordinator.tasks.spawn(detached_run(), name="run-1", owner="brain")
    await asyncio.wait_for(entered.wait(), 1)

    report = await asyncio.wait_for(coordinator.stop(), 1)

    assert outcomes == ["external_unknown"]
    assert events == ["start:feed", "stop:feed", "terminal:run", "close:sink"]
    # Cancelling at the deadline is the policy, not a failure.
    assert report.failures == ()
    assert report.status == 0
    assert (
        "module 'brain': task 'run-1' was cancelled at the drain deadline"
        in report.diagnostics
    )
    assert coordinator.tasks.active == 0


# --------------------------------------------------------------------------- #
# AC15 — the four non-zero termination scenarios
# --------------------------------------------------------------------------- #


async def test_partial_startup_closes_prepared_modules_and_reports_nonzero() -> None:
    """AC15 scenario 1: partial startup."""

    timeline = Timeline()
    events: list[str] = []

    async def failing(*_: Any) -> None:
        raise RuntimeError("secret-token-must-not-leak")

    first = module(
        "store",
        prepare=hook(events, "prepare:store"),
        close=hook(events, "close:store"),
    )
    broken = module(
        "gate",
        prepare=hook(events, "prepare:gate", failing),
        close=hook(events, "close:gate"),
    )
    feed = module(
        "feed",
        roles=(ROLE_INPUT,),
        prepare=hook(events, "prepare:feed"),
        start_inputs=hook(events, "start:feed"),
        close=hook(events, "close:feed"),
    )
    coordinator = coordinator_for([first, broken, feed], timeline)

    report = await asyncio.wait_for(coordinator.start(), 1)

    assert report.status == 1
    assert report.failures == ("module 'gate': phase 'prepare' failed",)
    assert all("secret-token" not in message for message in report.diagnostics)
    # The producer never prepared or started, and the gate never opened.
    assert "prepare:feed" not in events
    assert "start:feed" not in events
    assert coordinator.barrier.completed is False
    # Every module obtained is still closed, none skipped.
    assert set(events) >= {"close:store", "close:gate", "close:feed"}


async def test_cancelled_activation_closes_partial_startup_and_reports_nonzero(
) -> None:
    """AC15 scenario 2: a ``CancelledError`` during activation."""

    timeline = Timeline()
    events: list[str] = []
    preparing = asyncio.Event()

    async def never(*_: Any) -> None:
        preparing.set()
        await asyncio.Future()

    first = module(
        "store",
        prepare=hook(events, "prepare:store"),
        close=hook(events, "close:store"),
    )
    slow = module(
        "slow",
        prepare=hook(events, "prepare:slow", never),
        close=hook(events, "close:slow"),
    )
    coordinator = coordinator_for([first, slow], timeline)

    start = asyncio.create_task(coordinator.start())
    await asyncio.wait_for(preparing.wait(), 1)
    start.cancel()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(start, 1)

    report = coordinator.report
    assert report.status == 1
    assert "module 'slow': phase 'prepare' was cancelled" in report.failures
    # Cleanup still ran to completion before the cancellation propagated.
    assert set(events) >= {"close:store", "close:slow"}
    assert coordinator.barrier.completed is False


async def test_cancellation_resistant_task_is_bounded_and_reported_nonzero() -> None:
    """AC15 scenario 3: a cancellation-resistant task."""

    timeline = Timeline()
    events: list[str] = []
    entered = asyncio.Event()
    let_go = asyncio.Event()

    async def resistant() -> None:
        entered.set()
        while True:
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                events.append("resisted")
                await let_go.wait()
                raise

    sink = module("sink", close=hook(events, "close:sink"))
    ledger = module(
        "alpha",
        roles=(ROLE_OBSERVATION,),
        flush=hook(events, "flush:alpha"),
        close=hook(events, "close:alpha"),
    )
    coordinator = coordinator_for(
        [ledger, sink], timeline, drain_deadline_seconds=0.0
    )

    assert (await asyncio.wait_for(coordinator.start(), 1)).status == 0
    task = coordinator.tasks.spawn(resistant(), name="run-1", owner="brain")
    await asyncio.wait_for(entered.wait(), 1)

    stop = asyncio.create_task(coordinator.stop())
    await asyncio.sleep(0)
    timeline.release()
    try:
        report = await asyncio.wait_for(stop, 1)

        assert report.status == 1
        assert (
            "module 'brain': task 'run-1' did not stop when cancelled"
            in report.failures
        )
        assert "resisted" in events
        # A resistant run does not stop the remaining modules from closing.
        assert events[-3:] == ["close:sink", "flush:alpha", "close:alpha"]
    finally:
        let_go.set()
        await asyncio.gather(task, return_exceptions=True)


async def test_blocked_writer_is_bounded_and_later_modules_still_close() -> None:
    """AC15 scenario 4: a blocked synchronous writer.

    Cancelling the coroutine cannot stop a thread that is already blocked, so
    the phase must be bounded, reported against its module, and must not skip
    the modules that close after it.
    """

    timeline = Timeline()
    events: list[str] = []
    writing = threading.Event()
    unblock = threading.Event()

    def blocking_write() -> None:
        writing.set()
        unblock.wait(5)

    async def blocked_close(*_: Any) -> None:
        events.append("close:writer")
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, blocking_write)

    writer = module("writer", close=blocked_close)
    ledger = module(
        "alpha",
        roles=(ROLE_OBSERVATION,),
        flush=hook(events, "flush:alpha"),
        close=hook(events, "close:alpha"),
    )
    coordinator = coordinator_for([ledger, writer], timeline)

    assert (await asyncio.wait_for(coordinator.start(), 1)).status == 0
    stop = asyncio.create_task(coordinator.stop())
    await asyncio.wait_for(asyncio.to_thread(writing.wait, 5), 5)
    timeline.release()
    try:
        report = await asyncio.wait_for(stop, 2)

        assert report.status == 1
        assert "module 'writer': phase 'close' timed out" in report.failures
        assert events[-2:] == ["flush:alpha", "close:alpha"]
    finally:
        unblock.set()


async def test_resistant_close_hook_is_bounded_and_audit_role_still_closes() -> None:
    """The bounded close ported from the entry point, without its name map."""

    timeline = Timeline()
    events: list[str] = []
    entered = asyncio.Event()
    let_go = asyncio.Event()

    async def stubborn(*_: Any) -> None:
        entered.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            await let_go.wait()
            raise RuntimeError("late failure")

    stuck = module("stuck", close=stubborn)
    ledger = module(
        "alpha", roles=(ROLE_OBSERVATION,), close=hook(events, "close:alpha")
    )
    coordinator = coordinator_for([ledger, stuck], timeline)

    assert (await asyncio.wait_for(coordinator.start(), 1)).status == 0
    stop = asyncio.create_task(coordinator.stop())
    await asyncio.wait_for(entered.wait(), 1)
    timeline.release()
    try:
        report = await asyncio.wait_for(stop, 1)

        assert report.status == 1
        assert "module 'stuck': phase 'close' timed out" in report.failures
        assert events == ["close:alpha"]
    finally:
        let_go.set()
        await asyncio.sleep(0)


async def test_synchronous_hook_is_refused_before_anything_is_prepared() -> None:
    """A malformed hook is a lifecycle failure, never an escaping exception."""

    timeline = Timeline()
    events: list[str] = []
    transport = Transport(events)
    sound = module(
        "sound",
        prepare=hook(events, "prepare:sound"),
        close=transport.close,
    )
    # A synchronous hook: the defect the coordinator must name, not raise.
    broken = module("broken", prepare=lambda: None)
    coordinator = coordinator_for([sound, broken], timeline)

    report = await asyncio.wait_for(coordinator.start(), 1)

    assert report.status == 1
    assert report.failures == (
        "module 'broken': field 'prepare': async lifecycle hook is required",
    )
    # Refused before preparation: no module was prepared, and the shutdown
    # still ran, so nothing was left half-open.
    assert "prepare:sound" not in events
    assert transport.closed is True


async def test_hook_with_an_incompatible_signature_fails_only_its_own_phase(
) -> None:
    """A ``TypeError`` at call time is one phase's failure, not a skipped rest."""

    timeline = Timeline()
    events: list[str] = []

    async def wrong_signature(first: Any, second: Any) -> None:  # pragma: no cover
        return None

    offender = module("offender", drain=wrong_signature)
    later = module("later", close=hook(events, "close:later"))
    coordinator = coordinator_for([later, offender], timeline)

    assert (await asyncio.wait_for(coordinator.start(), 1)).status == 0
    report = await asyncio.wait_for(coordinator.stop(), 1)

    assert report.status == 1
    assert report.failures == ("module 'offender': phase 'drain' failed",)
    # Every later module still closed.
    assert events == ["close:later"]


async def test_a_synchronous_shutdown_hook_never_skips_the_remaining_modules(
) -> None:
    """A defect found only at teardown is named, and the rest still closes."""

    timeline = Timeline()
    events: list[str] = []
    transport = Transport(events, label="later")
    broken = module("broken", drain=lambda: None)
    later = module("later", close=transport.close)
    # No start(): the pre-flight validation never ran, so the defect surfaces
    # while the shutdown phases are already under way.
    coordinator = coordinator_for([later, broken], timeline)

    report = await asyncio.wait_for(coordinator.stop(), 1)

    assert report.status == 1
    assert report.failures == (
        "module 'broken': field 'drain': async lifecycle hook is required",
    )
    assert transport.closed is True
    assert events == ["close:later"]


async def test_cancelling_stop_still_finishes_the_remaining_cleanup() -> None:
    """AC15: a cancelled caller never leaves a transport open behind it."""

    timeline = Timeline()
    events: list[str] = []
    transport = Transport(events)
    gate = asyncio.Event()
    stopping = asyncio.Event()

    async def slow_stop(*_: Any) -> None:
        events.append("stop:feed")
        stopping.set()
        await gate.wait()
        events.append("stopped:feed")

    feed = module(
        "feed",
        roles=(ROLE_INPUT,),
        start_inputs=hook(events, "start:feed"),
        stop_inputs=slow_stop,
    )
    sink = module("sink", close=transport.close)
    coordinator = coordinator_for([sink, feed], timeline)

    assert (await asyncio.wait_for(coordinator.start(), 1)).status == 0
    stop = asyncio.create_task(coordinator.stop())
    await asyncio.wait_for(stopping.wait(), 1)
    assert events == ["start:feed", "stop:feed"]
    assert transport.closed is False

    stop.cancel()

    async def release_soon() -> None:
        await asyncio.sleep(0)
        gate.set()

    releasing = asyncio.create_task(release_soon())
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(stop, 1)
    await releasing

    # The cancellation propagated only once the cleanup was complete.
    assert events == ["start:feed", "stop:feed", "stopped:feed", "close:sink"]
    assert transport.closed is True

    # And the shutdown is only reported as done because it really is.
    report = await asyncio.wait_for(coordinator.stop(), 1)
    assert report.status == 0


async def test_an_abandoned_shutdown_is_resumed_and_never_reported_as_clean() -> None:
    """A shutdown cancelled part-way owes the phases it never reached.

    The caller being cancelled is already covered above. Here the *phase task
    itself* is torn down, as a loop cancelling every task at exit does: the
    close never runs, so the next stop must finish it instead of returning the
    clean status of a cleanup that never happened.
    """

    timeline = Timeline()
    events: list[str] = []
    transport = Transport(events)
    stopping = asyncio.Event()

    async def slow_stop(*_: Any) -> None:
        events.append("stop:feed")
        stopping.set()
        await asyncio.Event().wait()

    feed = module(
        "feed",
        roles=(ROLE_INPUT,),
        start_inputs=hook(events, "start:feed"),
        stop_inputs=slow_stop,
    )
    sink = module("sink", close=transport.close)
    coordinator = coordinator_for([sink, feed], timeline)

    assert (await asyncio.wait_for(coordinator.start(), 1)).status == 0
    stop = asyncio.create_task(coordinator.stop())
    await asyncio.wait_for(stopping.wait(), 1)

    coordinator._shutdown_task.cancel()  # the loop tears the phases down
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(stop, 1)

    # Nothing closed, so the cleanup is owed, not done.
    assert transport.closed is False

    report = await asyncio.wait_for(coordinator.stop(), 1)

    assert transport.closed is True
    assert events == ["start:feed", "stop:feed", "close:sink"]
    # The interrupted hook is not run twice, and the abandonment is reported
    # exactly once instead of being hidden behind a clean status.
    assert report.status == 1
    assert report.failures == (
        "lifecycle: shutdown was cancelled before it completed",
    )


async def test_a_cancelled_registry_close_still_refuses_further_work() -> None:
    """A close cut short by cancellation must not leave the registry open."""

    timeline = Timeline()
    tasks = SupervisedTasks(clock=timeline, sleeper=timeline.sleep)

    async def forever() -> None:
        await asyncio.Event().wait()

    owned = tasks.spawn(forever(), name="run-1", owner="brain")
    closing = asyncio.create_task(tasks.aclose(50.0))
    await asyncio.sleep(0)
    closing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(closing, 1)

    assert tasks.closed is True
    owned.cancel()
    with pytest.raises(asyncio.CancelledError):
        await owned

    async def late() -> None:  # pragma: no cover - never scheduled
        return None

    with pytest.raises(LifecycleError) as refused:
        tasks.spawn(late(), name="run-late", owner="brain")
    assert "the supervised registry is closed" in str(refused.value)


async def test_shutdown_closes_the_supervised_registry_before_teardown() -> None:
    """Nothing may detach once the drain phase is over."""

    timeline = Timeline()
    coordinator = coordinator_for([module("solo")], timeline)

    assert (await asyncio.wait_for(coordinator.start(), 1)).status == 0
    assert coordinator.tasks.closed is False
    assert (await asyncio.wait_for(coordinator.stop(), 1)).status == 0
    assert coordinator.tasks.closed is True

    async def late() -> None:  # pragma: no cover - never scheduled
        return None

    with pytest.raises(LifecycleError) as refused:
        coordinator.tasks.spawn(late(), name="run-late", owner="solo")
    assert "the supervised registry is closed" in str(refused.value)


# --------------------------------------------------------------------------- #
# Roles, no-ops, idempotence and global deadlines
# --------------------------------------------------------------------------- #


def test_roles_come_from_the_manifest_never_from_the_name() -> None:
    assert module_roles(module("alpha", roles=(ROLE_OBSERVATION,))) == {
        ROLE_OBSERVATION
    }
    assert module_roles(module("alpha")) == frozenset()
    # A loader-supplied roles field wins over the manifest section.
    supplied = SimpleNamespace(name="feed", manifest={}, roles=[ROLE_INPUT], handle=None)
    assert module_roles(supplied) == {ROLE_INPUT}

    with pytest.raises(LifecycleError) as invalid:
        module_roles(module("feed", roles=("producer",)))
    assert "module 'feed': field 'lifecycle.roles'" in str(invalid.value)

    broken = ModuleActivation(
        name="feed", manifest={"lifecycle": {"roles": "input"}}, handle=None
    )
    with pytest.raises(LifecycleError):
        module_roles(broken)


async def test_module_with_no_role_in_a_phase_supplies_a_noop() -> None:
    timeline = Timeline()
    events: list[str] = []
    bare = module("bare")  # no hook at all, not even close
    only_close = module("store", close=hook(events, "close:store"))
    coordinator = coordinator_for([bare, only_close], timeline)

    assert (await asyncio.wait_for(coordinator.start(), 1)).status == 0
    report = await asyncio.wait_for(coordinator.stop(), 1)

    assert report.status == 0
    assert report.diagnostics == ()
    assert events == ["close:store"]
    assert coordinator.producers() == ()
    assert coordinator.observers() == ()


async def test_every_phase_hook_is_invoked_at_most_once() -> None:
    timeline = Timeline()
    events: list[str] = []
    feed = module(
        "feed",
        roles=(ROLE_INPUT,),
        validate_settings=hook(events, "validate"),
        prepare=hook(events, "prepare"),
        start_inputs=hook(events, "start"),
        stop_inputs=hook(events, "stop"),
        drain=hook(events, "drain"),
        close=hook(events, "close"),
    )
    ledger = module(
        "alpha",
        roles=(ROLE_OBSERVATION,),
        flush=hook(events, "flush"),
        close=hook(events, "close:alpha"),
    )
    coordinator = coordinator_for([feed, ledger], timeline)

    await asyncio.wait_for(coordinator.start(), 1)
    await asyncio.wait_for(coordinator.start(), 1)
    await asyncio.wait_for(coordinator.stop(), 1)
    report = await asyncio.wait_for(coordinator.stop(), 1)

    assert report.status == 0
    assert events == [
        "validate",
        "prepare",
        "start",
        "stop",
        "drain",
        "close",
        "flush",
        "close:alpha",
    ]
    # The declared sequence the coordinator drives, for the record.
    assert PHASES == (
        PHASE_VALIDATE_SETTINGS,
        PHASE_PREPARE,
        PHASE_READINESS_BARRIER,
        PHASE_START_INPUTS,
        PHASE_STOP_INPUTS,
        PHASE_DRAIN,
        PHASE_CLOSE,
        PHASE_FLUSH,
        PHASE_CLOSE_OBSERVATION,
    )


async def test_drain_and_flush_hooks_receive_the_remaining_budget() -> None:
    timeline = Timeline()
    budgets: list[float] = []

    async def record(seconds: float) -> None:
        budgets.append(seconds)

    store = module("store", drain=record)
    ledger = module("alpha", roles=(ROLE_OBSERVATION,), flush=record)
    coordinator = coordinator_for(
        [store, ledger], timeline, hook_timeout_seconds=7.0, drain_deadline_seconds=5.0
    )

    await asyncio.wait_for(coordinator.start(), 1)
    report = await asyncio.wait_for(coordinator.stop(), 1)

    assert report.status == 0
    assert budgets == [7.0, 7.0]
    assert all(value <= 7.0 for value in budgets)


async def test_one_global_shutdown_deadline_caps_the_sum_of_local_timeouts() -> None:
    """R4: local timeouts do not sum without a cap.

    Four modules each entitled to a 10-unit close, under a 15-unit global
    shutdown deadline. The first two consume the whole global budget between
    them — the second capped below its own timeout — and the last two are
    refused against the global deadline instead of being granted 10 more each.
    """

    timeline = Timeline()
    invoked: list[str] = []
    let_go = asyncio.Event()

    def blocking(label: str):
        async def run(*_: Any) -> None:
            invoked.append(label)
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                await let_go.wait()
                raise

        return run

    # Reverse activation order closes 'd' first.
    modules = [module(name, close=blocking(name)) for name in ("d", "c", "b", "a")]
    coordinator = coordinator_for(
        modules,
        timeline,
        hook_timeout_seconds=10.0,
        shutdown_deadline_seconds=15.0,
        cancel_grace_seconds=0.1,
    )

    assert (await asyncio.wait_for(coordinator.start(), 1)).status == 0
    stop = asyncio.create_task(coordinator.stop())
    await asyncio.sleep(0)
    timeline.release()
    try:
        report = await asyncio.wait_for(stop, 1)

        assert report.status == 1
        assert invoked == ["a", "b"]
        assert report.failures == (
            "module 'a': phase 'close' timed out",
            "module 'b': phase 'close' timed out",
            "module 'c': phase 'close' exceeded the global shutdown deadline",
            "module 'd': phase 'close' exceeded the global shutdown deadline",
        )
        # Four 10-unit local timeouts would have spent 40; the cap held at 15
        # plus the two cancellation grace windows actually used.
        assert timeline.now <= 15.0 + 2 * 0.1
    finally:
        let_go.set()
        await asyncio.sleep(0)


# --------------------------------------------------------------------------- #
# The compatibility step of a mixed pipeline
# --------------------------------------------------------------------------- #


def legacy(name: str, close: Any) -> ModuleActivation:
    """A v1 activation: no declared version, no role, only ``close()``."""

    return ModuleActivation(name=name, manifest={"name": name}, handle=SimpleNamespace(close=close))


async def test_compatibility_close_runs_after_drain_and_before_any_versioned_close(
) -> None:
    """A v1 producer's whole shutdown is its close; it still has sinks to reach.

    The v1 source publishes the work it accepted into a versioned sink and a
    versioned observation service *from its close*. Both must still be open:
    the close therefore runs after the versioned producers stopped and
    drained, before the ordinary close phase, and before the observation
    service flushes what it saw.
    """

    timeline = Timeline()
    events: list[str] = []
    sink = Transport(events, label="sink")
    observed: list[str] = []

    async def legacy_close() -> None:
        events.append("close:legacy")
        sink.send("late work")
        observed.append("legacy stopped")

    async def flush(*_: Any) -> None:
        events.append(f"flush:alpha[{','.join(observed)}]")

    versioned_feed = module(
        "feed",
        roles=(ROLE_INPUT,),
        start_inputs=hook(events, "start:feed"),
        stop_inputs=hook(events, "stop:feed"),
        drain=hook(events, "drain:feed"),
        close=hook(events, "close:feed"),
    )
    ledger = module(
        "alpha",
        roles=(ROLE_OBSERVATION,),
        flush=flush,
        close=hook(events, "close:alpha"),
    )
    coordinator = coordinator_for(
        [ledger, module("sink", close=sink.close), versioned_feed],
        timeline,
        compatibility=[legacy("legacy", legacy_close)],
    )

    assert [item.name for item in coordinator.compatibility()] == ["legacy"]
    assert (await asyncio.wait_for(coordinator.start(), 1)).status == 0
    report = await asyncio.wait_for(coordinator.stop(), 1)

    assert report.status == 0
    assert sink.sent == ["late work"]
    assert events == [
        "start:feed",
        "stop:feed",
        "drain:feed",
        "close:legacy",
        "close:feed",
        "close:sink",
        "flush:alpha[legacy stopped]",
        "close:alpha",
    ]


async def test_compatibility_closes_in_activation_order_collecting_failures() -> None:
    """A v1 pipeline is listed source to sink; one failure skips no close."""

    timeline = Timeline()
    events: list[str] = []

    async def broken() -> None:
        events.append("close:relay")
        raise RuntimeError("secret-token-must-not-leak")

    coordinator = coordinator_for(
        [module("engine", close=hook(events, "close:engine"))],
        timeline,
        compatibility=[
            legacy("source", hook(events, "close:source")),
            legacy("relay", broken),
            legacy("sink", hook(events, "close:sink")),
        ],
    )

    assert (await asyncio.wait_for(coordinator.start(), 1)).status == 0
    report = await asyncio.wait_for(coordinator.stop(), 1)
    again = await asyncio.wait_for(coordinator.stop(), 1)

    assert report.status == 1
    assert report.failures == ("module 'relay': shutdown failed",)
    assert all("secret-token" not in message for message in report.diagnostics)
    assert events == ["close:source", "close:relay", "close:sink", "close:engine"]
    # Idempotent like every other phase: nothing closes twice.
    assert again == report


async def test_failed_startup_closes_compatibility_under_the_same_deadline() -> None:
    """R4: a failed start unwinds both routes inside one shutdown budget.

    The versioned module fails to prepare. Its unwinding takes the one
    global shutdown deadline of 15 units; the v1 close blocks and is capped
    by its 10-unit hook timeout, then the versioned close is capped by the 5
    units left — not granted a fresh budget of its own.
    """

    timeline = Timeline()
    invoked: dict[str, float] = {}
    let_go = asyncio.Event()

    def blocking(label: str):
        async def run(*_: Any) -> None:
            invoked[label] = timeline.now
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                await let_go.wait()
                raise

        return run

    async def failing(*_: Any) -> None:
        raise RuntimeError("prepare failed")

    coordinator = coordinator_for(
        [module("gate", prepare=failing, close=blocking("close:gate"))],
        timeline,
        compatibility=[legacy("legacy", blocking("close:legacy"))],
        hook_timeout_seconds=10.0,
        shutdown_deadline_seconds=15.0,
        cancel_grace_seconds=0.1,
    )

    start = asyncio.create_task(coordinator.start())
    await asyncio.sleep(0)
    timeline.release()
    try:
        report = await asyncio.wait_for(start, 1)

        assert report.status == 1
        assert coordinator.ready is False
        assert list(invoked) == ["close:legacy", "close:gate"]
        assert report.failures == (
            "module 'gate': phase 'prepare' failed",
            "module 'legacy': shutdown failed",
            "module 'gate': phase 'close' timed out",
        )
        # The released timeline spends the prepare hook's own timer before the
        # unwinding begins, so the budget is measured from the first close.
        # Two 10-unit local timeouts would have spent 20; the one global
        # deadline held at 15 plus the two cancellation grace windows, and the
        # versioned close got only what the v1 close left, not 10 of its own.
        unwinding_began = invoked["close:legacy"]
        assert invoked["close:gate"] - unwinding_began == pytest.approx(10.1)
        assert timeline.now - invoked["close:gate"] < 10.0
        assert timeline.now <= unwinding_began + 15.0 + 2 * 0.1
    finally:
        let_go.set()
        await asyncio.sleep(0)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("startup_deadline_seconds", float("inf")),
        ("shutdown_deadline_seconds", 0.0),
        ("hook_timeout_seconds", -1.0),
        ("drain_deadline_seconds", float("nan")),
        ("cancel_grace_seconds", "10"),
    ],
)
def test_every_lifecycle_deadline_must_be_finite_and_named_when_it_is_not(
    field: str, value: Any
) -> None:
    with pytest.raises(LifecycleError) as rejected:
        PhaseCoordinator([], **{field: value})
    assert repr(field) in str(rejected.value)


# --------------------------------------------------------------------------- #
# Supervised task registry
# --------------------------------------------------------------------------- #


async def test_every_task_failure_is_observed_and_reported_sanitised() -> None:
    unhandled: list[dict[str, Any]] = []
    loop = asyncio.get_running_loop()
    previous = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: unhandled.append(context))
    tasks = SupervisedTasks()

    async def failing() -> None:
        raise RuntimeError("credential-shaped-detail")

    try:
        task = tasks.spawn(failing(), name="run-1", owner="brain")
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.sleep(0)

        assert tasks.failures == ("module 'brain': task 'run-1' failed",)
        assert "credential-shaped-detail" not in tasks.failures[0]
        assert tasks.active == 0
        # The exception was retrieved, so nothing is reported as never-retrieved.
        del task
        await asyncio.sleep(0)
        assert unhandled == []
    finally:
        loop.set_exception_handler(previous)


async def test_drain_waits_for_owned_work_then_refuses_new_spawns() -> None:
    tasks = SupervisedTasks()
    finished: list[str] = []
    release = asyncio.Event()

    async def work(label: str) -> None:
        await release.wait()
        finished.append(label)

    tasks.spawn(work("one"), name="run-1", owner="brain")
    tasks.spawn(work("two"), name="run-2", owner="brain")
    assert tasks.active == 2

    idle = asyncio.create_task(tasks.wait_idle())
    drain = asyncio.create_task(tasks.aclose(50.0))
    await asyncio.sleep(0)
    assert not drain.done()
    assert not idle.done()
    release.set()
    await asyncio.wait_for(idle, 1)
    report = await asyncio.wait_for(drain, 1)

    assert sorted(finished) == ["one", "two"]
    assert report.completed == 2
    assert report.cancelled == ()
    assert report.failures == ()
    assert tasks.closed is True

    async def late() -> None:  # pragma: no cover - never scheduled
        return None

    with pytest.raises(LifecycleError) as refused:
        tasks.spawn(late(), name="run-3", owner="brain")
    assert "module 'brain': task 'run-3' refused" in str(refused.value)


async def test_work_spawned_while_draining_gets_the_rest_of_the_budget() -> None:
    """A child accepted during the drain is not cancelled when its parent ends."""

    tasks = SupervisedTasks()
    order: list[str] = []
    parenting = asyncio.Event()
    released = asyncio.Event()

    async def child() -> None:
        order.append("child:start")
        await released.wait()
        order.append("child:done")

    async def parent() -> None:
        await parenting.wait()
        order.append("parent:done")
        tasks.spawn(child(), name="run-child", owner="brain")

    tasks.spawn(parent(), name="run-parent", owner="brain")
    drain = asyncio.create_task(tasks.drain(50.0))
    await asyncio.sleep(0)
    assert not drain.done()

    # The parent finishes, handing its remaining work to a child. The drain
    # budget is nowhere near spent, so the child must inherit what is left of
    # it instead of being cancelled the instant its parent returns.
    parenting.set()
    for _ in range(4):
        await asyncio.sleep(0)
    assert order == ["parent:done", "child:start"]
    assert not drain.done()
    assert tasks.owners() == ("brain",)

    released.set()
    report = await asyncio.wait_for(drain, 1)

    assert order == ["parent:done", "child:start", "child:done"]
    assert report.completed == 2
    assert report.cancelled == ()
    assert report.failures == ()


async def test_spawn_rejects_a_missing_owner_or_name_without_leaking_a_coroutine(
) -> None:
    tasks = SupervisedTasks()

    async def work() -> None:  # pragma: no cover - never scheduled
        return None

    with pytest.raises(LifecycleError) as bad_name:
        tasks.spawn(work(), name="", owner="brain")
    assert "'spawn.name'" in str(bad_name.value)

    with pytest.raises(LifecycleError) as bad_owner:
        tasks.spawn(work(), name="run-1", owner="")
    assert "'spawn.owner'" in str(bad_owner.value)

    with pytest.raises(LifecycleError):
        tasks.spawn(object(), name="run-1", owner="brain")  # type: ignore[arg-type]

    assert tasks.active == 0


async def test_coordinator_reports_a_task_failure_that_happened_while_running(
) -> None:
    timeline = Timeline()
    reported: list[str] = []
    coordinator = coordinator_for(
        [module("store")], timeline, reporter=reported.append
    )

    async def failing() -> None:
        raise RuntimeError("boom")

    await asyncio.wait_for(coordinator.start(), 1)
    task = coordinator.tasks.spawn(failing(), name="run-1", owner="brain")
    await asyncio.gather(task, return_exceptions=True)

    assert coordinator.collect_task_failures() == (
        "module 'brain': task 'run-1' failed",
    )
    # Collected once, never twice.
    assert coordinator.collect_task_failures() == ()
    report = await asyncio.wait_for(coordinator.stop(), 1)
    assert report.failures == ("module 'brain': task 'run-1' failed",)
    assert reported == ["module 'brain': task 'run-1' failed"]


# --------------------------------------------------------------------------- #
# Bounded close and the process watchdog
# --------------------------------------------------------------------------- #


async def test_close_modules_orders_by_declared_role_not_by_name() -> None:
    events: list[str] = []
    # In activation order: an observation service first, then two ordinary
    # modules. Reverse activation order plus the declared role decides.
    ledger = module("alpha", roles=(ROLE_OBSERVATION,), close=hook(events, "alpha"))
    first = module("omega", close=hook(events, "omega"))
    second = module("beta", close=hook(events, "beta"))

    failures = await close_modules([ledger, first, second])

    assert failures == []
    assert events == ["beta", "omega", "alpha"]


async def test_close_modules_collects_failures_without_skipping_others() -> None:
    events: list[str] = []

    async def failing() -> None:
        raise RuntimeError("close failed")

    broken = module("broken", close=failing)
    other = module("other", close=hook(events, "other"))

    failures = await close_modules([broken, other])

    assert failures == ["module 'broken': shutdown failed"]
    assert events == ["other"]


async def test_close_modules_stops_at_a_global_deadline_it_was_given() -> None:
    """The helper the entry point reuses honours one global shutdown budget."""

    timeline = Timeline()
    events: list[str] = []
    entered = asyncio.Event()
    let_go = asyncio.Event()

    async def stubborn() -> None:
        entered.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            await let_go.wait()
            raise

    stuck = module("stuck", close=stubborn)
    other = module("other", close=hook(events, "other"))

    pending = asyncio.create_task(
        close_modules(
            [other, stuck],
            timeout_seconds=10.0,
            cancel_grace_seconds=0.1,
            sleeper=timeline.sleep,
            clock=timeline,
            deadline_at=4.0,
        )
    )
    await asyncio.wait_for(entered.wait(), 1)
    timeline.release()
    try:
        failures = await asyncio.wait_for(pending, 1)

        assert failures == ["module 'stuck': shutdown failed"]
        assert events == ["other"]
        # Capped at the 4 units left of the global budget, not the local 10.
        assert timeline.now <= 4.0 + 0.1
    finally:
        let_go.set()
        await asyncio.sleep(0)


async def test_close_modules_arms_the_installed_process_watchdog() -> None:
    exits: list[int] = []
    watchdog = ProcessWatchdog(
        DEFAULT_SHUTDOWN_DEADLINE_SECONDS, exit_process=exits.append
    )
    token = install_shutdown_watchdog(watchdog)
    try:
        assert watchdog.armed is False
        await close_modules([module("store")])
        assert watchdog.armed is True
        arm_shutdown_watchdog()  # repeated arming does not restart the timer
        assert watchdog.armed is True
    finally:
        watchdog.cancel()
        reset_shutdown_watchdog(token)
    assert watchdog.armed is False
    assert exits == []


def test_process_watchdog_rejects_a_non_finite_deadline() -> None:
    with pytest.raises(LifecycleError):
        ProcessWatchdog(float("inf"))
    with pytest.raises(LifecycleError):
        ProcessWatchdog(1.0, exit_process="not callable")  # type: ignore[arg-type]


def test_arm_shutdown_watchdog_is_safe_with_no_watchdog_installed() -> None:
    token = install_shutdown_watchdog(None)
    try:
        arm_shutdown_watchdog()
    finally:
        reset_shutdown_watchdog(token)


# --------------------------------------------------------------------------- #
# AC13 — the fourth fictional module, through the real loader and entry point
# --------------------------------------------------------------------------- #


FOURTH_MODULE_NAME = "beacon"
"""A module the entry point has never heard of: it must need nothing from it."""

FOURTH_MODULE_INPUT = "beacon.pulse"
FOURTH_MODULE_ACTION = "beacon.note"
FOURTH_MODULE_PERMISSION = "note.write"

FOURTH_MODULE_MANIFEST: dict[str, Any] = {
    "name": FOURTH_MODULE_NAME,
    "manifest_version": 2,
    "runtime_api": 2,
    "produces": [FOURTH_MODULE_INPUT],
    "consumes": [FOURTH_MODULE_INPUT],
    "middleware": False,
    # The one thing the coordinator reads to decide what each phase asks of
    # this module (R4). Producing an input is the ``input`` role.
    "lifecycle": {"roles": [ROLE_INPUT]},
    "settings_schema": {
        "type": "object",
        "properties": {
            "lifecycle_log": {"type": "string"},
            "channel": {"type": "string"},
        },
        "required": ["lifecycle_log", "channel"],
    },
    "settings_validator": "validate_settings",
    # Declared, therefore discovered; authorized only by the rule the
    # configuration grants below (R5).
    "actions": [
        {
            "name": FOURTH_MODULE_ACTION,
            "version": 1,
            "description": "Keep one note about a pulse.",
            "argument_schema": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
                "additionalProperties": False,
            },
            "result_schema": {
                "type": "object",
                "properties": {"noted": {"type": "boolean"}},
                "required": ["noted"],
                "additionalProperties": False,
            },
            "nature": "write",
            "required_permissions": [FOURTH_MODULE_PERMISSION],
            "supported_destinations": [
                {"platform": FOURTH_MODULE_NAME, "channel_id": "*", "scope": "notes"}
            ],
            "timeout_seconds": 5,
            "idempotency": "none",
        }
    ],
}

FOURTH_MODULE_SOURCE = '''
"""A fictional versioned module: one input source, one action it consumes.

It exposes every phase hook a producer has and records each one. Its input
is a single pulse published from the source it opens in ``start_inputs``;
its own handler admits that pulse and detaches a run that consumes the
declared action through the runtime's executor, so both directions of the
pipeline pass through the versioned runtime the entry point assembled.
"""

import asyncio
from pathlib import Path

from core.contracts import ActionCall, ActionObservation, Destination

INPUT_EVENT = "beacon.pulse"
ACTION = "beacon.note"
PRINCIPAL = "beacon"
PROVIDER = "beacon-notebook"


def _record(settings, line):
    with Path(settings["lifecycle_log"]).open("a", encoding="utf-8") as stream:
        stream.write(line + "\\n")


def validate_settings(settings):
    _record(settings, "validate_settings")
    return []


class Notebook:
    """The provider behind the declared action: it keeps the note."""

    name = PROVIDER

    def __init__(self, settings):
        self._settings = settings

    async def invoke(self, invocation):
        invocation.mark_not_emitted()
        text = invocation.call.arguments["text"]
        invocation.mark_emitted()
        _record(self._settings, f"provide:{text}")
        return ActionObservation(
            status="success",
            provenance={"provider": self.name},
            result={"noted": True},
        )


class Handle:
    def __init__(self, context, settings):
        self._context = context
        self._settings = settings
        self._destination = Destination(
            platform=PRINCIPAL, channel_id=settings["channel"], scope="notes"
        )
        self._prepared = False
        self._source = None
        self._stopped = asyncio.Event()
        self._closed = False

    # -- startup ------------------------------------------------------------ #

    async def prepare(self):
        if self._prepared or self._closed:
            return
        _record(self._settings, "prepare")
        # Bind to the action the manifest declared and the loader recorded;
        # the consumer route is subscribed before any producer may publish.
        self._context.actions.bind(
            ACTION,
            Notebook(self._settings),
            destinations=self._destination,
            provider_name=PROVIDER,
        )
        self._context.bus.subscribe(INPUT_EVENT, self.handle_pulse)
        self._context.actions.mark_ready()
        self._prepared = True

    async def start_inputs(self):
        if self._source is not None or self._closed:
            return
        if not self._prepared:
            raise RuntimeError("start_inputs requires prepare")
        _record(self._settings, "start_inputs")
        self._source = self._context.tasks.spawn(self._pulse(), name="beacon-source")

    async def _pulse(self):
        _record(self._settings, "input:published")
        await self._context.bus.publish(
            INPUT_EVENT, {"text": "ping", "message_id": "pulse-1"}, {}
        )
        # A source stays open until the coordinator cuts it.
        await self._stopped.wait()

    # -- the input handler: admit, detach, return (R2) ------------------------ #

    async def handle_pulse(self, event):
        self._context.tasks.spawn(self._consume(event), name="beacon-run")

    async def _consume(self, event):
        call = ActionCall(
            action_name=ACTION,
            action_version=1,
            arguments={"text": event["payload"]["text"]},
            conversation_id="beacon:conversation",
            run_id="beacon-run-1",
            call_id="beacon-run-1/call-1",
            source_event_id=event["payload"]["message_id"],
            destination=self._destination,
            principal=PRINCIPAL,
            deadline=self._context.clock() + 5.0,
        )
        observation = await self._context.executor.invoke(call)
        _record(self._settings, f"action:{observation.status}")

    # -- shutdown ----------------------------------------------------------- #

    async def stop_inputs(self):
        _record(self._settings, "stop_inputs")
        self._stopped.set()
        if self._source is not None:
            await asyncio.gather(self._source, return_exceptions=True)

    async def drain(self, deadline_seconds):
        _record(self._settings, "drain")

    async def close(self):
        if self._closed:
            return
        self._closed = True
        _record(self._settings, "close")


async def activate(context, settings, catalog):
    _record(settings, "activate")
    return Handle(context, settings)
'''


def _write_fourth_module(root: Path) -> None:
    """The module lives under its own ``modules`` root the loader is pointed at."""

    directory = root / FOURTH_MODULE_NAME
    directory.mkdir(parents=True)
    (directory / "module.yaml").write_text(
        yaml.safe_dump(FOURTH_MODULE_MANIFEST), encoding="utf-8"
    )
    (directory / "__init__.py").write_text(FOURTH_MODULE_SOURCE, encoding="utf-8")


def _entry_point_source() -> str:
    return (
        Path(__file__).resolve().parents[1] / "core" / "main.py"
    ).read_text(encoding="utf-8")


def test_entry_point_names_no_module() -> None:
    """AC13: the literal search — ``twitch``, ``brain``, ``audit`` — finds 0."""

    source = _entry_point_source().lower()

    for literal in ("twitch", "brain", "audit"):
        assert source.count(literal) == 0, literal


async def test_fourth_module_starts_and_stops_through_every_phase_unnamed(
    tmp_path: Path,
) -> None:
    """AC13: a module the entry point never heard of runs every phase.

    The fourth fictional module both produces an input and consumes an
    action. It is discovered by the real loader from its own directory,
    activated on the versioned runtime the entry point assembled, started
    only after the readiness barrier, and stopped, drained and closed by the
    coordinator — with 0 occurrences of its name, or of any module name, in
    ``core/main.py``.
    """

    modules = tmp_path / "modules"
    _write_fourth_module(modules)
    lifecycle_log = tmp_path / "lifecycle.log"
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "modules_directory": "./modules",
                "enabled_modules": [FOURTH_MODULE_NAME],
                "modules": {
                    FOURTH_MODULE_NAME: {
                        "lifecycle_log": str(lifecycle_log),
                        "channel": "main",
                    }
                },
                "limits": _finite_limits(),
                # Declaring the action authorized nothing; this rule does (R5).
                "actions": [
                    {
                        "rule_id": "beacon-notes",
                        "action_name": FOURTH_MODULE_ACTION,
                        "principals": [FOURTH_MODULE_NAME],
                        "granted_permissions": [FOURTH_MODULE_PERMISSION],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    def log_lines() -> list[str]:
        if not lifecycle_log.exists():
            return []
        return lifecycle_log.read_text(encoding="utf-8").splitlines()

    stop = asyncio.Event()
    ready = asyncio.Event()
    readiness: list[str] = []
    diagnostics: list[str] = []

    def report_ready(message: str) -> None:
        readiness.append(message)
        ready.set()

    task = asyncio.create_task(
        application.run(
            config_path,
            stop,
            ready_reporter=report_ready,
            diagnostic_reporter=diagnostics.append,
        )
    )
    try:
        await asyncio.wait_for(ready.wait(), 1)
        assert readiness == ["ready"]
        assert not task.done()
        # Validation, activation and preparation all preceded the barrier;
        # the source opened only after it.
        assert log_lines()[:4] == [
            "validate_settings",
            "activate",
            "prepare",
            "start_inputs",
        ]
        # The pulse is produced, admitted, and its detached run consumes the
        # declared action through the real executor and the granted rule.
        await wait_until(lambda: "action:success" in log_lines())
        assert log_lines()[4:] == ["input:published", "provide:ping", "action:success"]
    finally:
        stop.set()
    assert await asyncio.wait_for(task, 1) == 0
    assert diagnostics == []
    assert log_lines() == [
        "validate_settings",
        "activate",
        "prepare",
        "start_inputs",
        "input:published",
        "provide:ping",
        "action:success",
        "stop_inputs",
        "drain",
        "close",
    ]

    # 0 module-name literals were needed for any of that: not the fourth
    # module's, and not the three the entry point used to order by name.
    source = _entry_point_source().lower()
    for literal in (FOURTH_MODULE_NAME, "twitch", "brain", "audit"):
        assert source.count(literal) == 0, literal
