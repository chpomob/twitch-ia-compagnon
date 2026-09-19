"""Generic phase lifecycle coordination and supervised task ownership (R4).

The application does not order its modules by name. It drives one fixed phase
sequence and asks each module, through its **declared manifest role** and the
**hooks its handle exposes**, what it does in each phase:

===========================  =========================================
Phase                        Guarantee
===========================  =========================================
``validate_settings``        Every enabled module's own settings are
                             accepted before any of them opens a
                             transport.
``prepare``                  Consumers, handlers, resources and
                             supervised tasks are registered. No input
                             is produced yet.
``readiness_barrier``        Not a hook. Preparation succeeded for every
                             module; only now does the gate open and only
                             now may an input be admitted (AC14).
``start_inputs``             Producers open their sources.
---------------------------  -----------------------------------------
``stop_inputs``              Producers cut new sources. Outputs, stores
                             and executors stay usable.
``drain``                    Accepted work finishes inside the drain
                             budget or is explicitly cancelled, with a
                             grace period so a cancelled run can still
                             publish its terminal outcome (AC15).
``close`` (compatibility)    A v1 activation, which has no phase but
                             ``close()``, stops its inputs, drains and
                             releases all at once here: after every
                             versioned producer stopped and drained, and
                             before any versioned resource it may still
                             publish into closes.
``close``                    Ordinary transports and stores close.
                             Failures are collected, never swallowed,
                             and never skip the remaining modules.
``flush`` + ``close``        Observation services finish their traces
                             after the last close, then close. This role
                             is *declared*, never inferred from a name.
===========================  =========================================

A module with no role in a phase supplies nothing and the phase is a no-op for
it. Every hook is invoked at most once per coordinator, so the sequence is
idempotent, and every hook is bounded by a per-hook timeout that is itself
capped by one finite global startup deadline and one finite global shutdown
deadline — local timeouts can never sum past the global budget.

:class:`SupervisedTasks` owns every detached task. Nothing calls
``asyncio.create_task`` and forgets it: a spawned task is registered under an
owner, its completion is always observed, its failure is collected as a
sanitised diagnostic naming that owner, and shutdown drains the owned set under
the drain deadline so no admitted run is orphaned.

The bounded close, the explicit cancellation with a retrieval callback for late
exceptions, the per-module failure collection and the last-resort process
watchdog are **ported** from ``core/main.py`` rather than rewritten; the entry
point delegates to this module in a later step.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import math
import os
import threading
import time
from collections import deque
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any

__all__ = [
    "DEFAULT_CANCEL_GRACE_SECONDS",
    "DEFAULT_DRAIN_DEADLINE_SECONDS",
    "DEFAULT_HOOK_TIMEOUT_SECONDS",
    "DEFAULT_SHUTDOWN_DEADLINE_SECONDS",
    "DEFAULT_STARTUP_DEADLINE_SECONDS",
    "HOOK_NAMES",
    "MANIFEST_LIFECYCLE_KEY",
    "MANIFEST_ROLES_KEY",
    "PHASES",
    "PHASE_CLOSE",
    "PHASE_CLOSE_OBSERVATION",
    "PHASE_DRAIN",
    "PHASE_FLUSH",
    "PHASE_PREPARE",
    "PHASE_READINESS_BARRIER",
    "PHASE_START_INPUTS",
    "PHASE_STOP_INPUTS",
    "PHASE_VALIDATE_SETTINGS",
    "ROLES",
    "ROLE_INPUT",
    "ROLE_OBSERVATION",
    "BarrierViolation",
    "DrainReport",
    "LifecycleError",
    "LifecycleReport",
    "PhaseCoordinator",
    "ProcessWatchdog",
    "ReadinessBarrier",
    "SupervisedTasks",
    "arm_shutdown_watchdog",
    "close_modules",
    "install_shutdown_watchdog",
    "module_roles",
]

Clock = Callable[[], float]
Sleeper = Callable[[float], Awaitable[None]]
Reporter = Callable[[str], None]
PhaseObserver = Callable[..., Any]
"""``observer(phase, module_name, *, failure=None)`` — see :class:`PhaseCoordinator`."""


# --------------------------------------------------------------------------- #
# Vocabulary
# --------------------------------------------------------------------------- #

PHASE_VALIDATE_SETTINGS = "validate_settings"
PHASE_PREPARE = "prepare"
PHASE_READINESS_BARRIER = "readiness_barrier"
PHASE_START_INPUTS = "start_inputs"
PHASE_STOP_INPUTS = "stop_inputs"
PHASE_DRAIN = "drain"
PHASE_CLOSE = "close"
PHASE_FLUSH = "flush"
PHASE_CLOSE_OBSERVATION = "close_observation"

#: The fixed sequence, in order. ``readiness_barrier`` has no hook: it is the
#: gate between preparation and the first produced input.
PHASES: tuple[str, ...] = (
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

#: The handle attribute each phase looks for. Two phases share ``close``: an
#: observation service closes in its own phase, after ordinary resources.
HOOK_NAMES: Mapping[str, str] = {
    PHASE_VALIDATE_SETTINGS: "validate_settings",
    PHASE_PREPARE: "prepare",
    PHASE_START_INPUTS: "start_inputs",
    PHASE_STOP_INPUTS: "stop_inputs",
    PHASE_DRAIN: "drain",
    PHASE_CLOSE: "close",
    PHASE_FLUSH: "flush",
    PHASE_CLOSE_OBSERVATION: "close",
}

#: Phases whose hook receives the remaining budget in seconds.
_DEADLINE_PHASES = frozenset({PHASE_DRAIN, PHASE_FLUSH})

ROLE_INPUT = "input"
ROLE_OBSERVATION = "observation"
ROLES = frozenset({ROLE_INPUT, ROLE_OBSERVATION})

MANIFEST_LIFECYCLE_KEY = "lifecycle"
MANIFEST_ROLES_KEY = "roles"

# Ported from ``core/main.py``: each hook gets a grace period, the process
# watchdog covers cancellation-resistant tasks and a blocked default-executor
# writer that ``asyncio.run`` would otherwise join forever.
DEFAULT_STARTUP_DEADLINE_SECONDS = 120.0
DEFAULT_SHUTDOWN_DEADLINE_SECONDS = 120.0
DEFAULT_HOOK_TIMEOUT_SECONDS = 60.0
DEFAULT_DRAIN_DEADLINE_SECONDS = 60.0
DEFAULT_CANCEL_GRACE_SECONDS = 0.1

#: Diagnostics carry module names and phase names, never module output. This
#: cap is the last guard against a pathological manifest name.
_MAX_DIAGNOSTIC_NAME = 120
#: Refusals recorded by the barrier are bounded; the count is unbounded.
_MAX_RETAINED_REFUSALS = 32


class LifecycleError(RuntimeError):
    """A lifecycle defect, always naming the module it belongs to."""


class BarrierViolation(LifecycleError):
    """Raised when input is produced or admitted before the barrier (AC14)."""

    def __init__(self, module: str, reason: str) -> None:
        self.module = module
        self.reason = reason
        super().__init__(f"module {_name(module)!r}: {reason}")


class _StartupAborted(Exception):
    """Internal: startup stopped, the collected failures carry the reason."""


# --------------------------------------------------------------------------- #
# Reports
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class LifecycleReport:
    """The outcome of a phase sequence.

    ``status`` is process-style: 0 only when nothing failed. ``diagnostics``
    holds every message worth reporting, ``failures`` the subset that made the
    status non-zero — a run cancelled at an exhausted drain deadline is
    reported without being an error, because the deadline is the policy.
    """

    status: int
    diagnostics: tuple[str, ...] = ()
    failures: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DrainReport:
    """The outcome of draining the supervised set."""

    #: Tasks that finished on their own inside the budget.
    completed: int = 0
    #: Diagnostics for tasks cancelled once the drain deadline was exhausted.
    cancelled: tuple[str, ...] = ()
    #: Diagnostics for tasks that raised, or that resisted cancellation.
    failures: tuple[str, ...] = ()


# --------------------------------------------------------------------------- #
# Readiness barrier
# --------------------------------------------------------------------------- #


class ReadinessBarrier:
    """The gate between preparation and the first produced input (R4, AC14).

    Two guards, both naming the module in their diagnostic:

    ``guard_admission`` is what ingestion calls. Before the barrier completes
    it refuses, so 0 inputs reach admission while any module is still
    preparing — however slow that preparation is.

    ``guard_producer`` is what a producer calls when it opens its source. A
    producer that starts before the barrier is refused; this is also the
    refusal a v1 producer meets when it cannot honour the barrier at all.
    """

    __slots__ = ("_completed", "_event", "_refusals", "_refused")

    def __init__(self) -> None:
        self._completed = False
        self._event = asyncio.Event()
        self._refused = 0
        self._refusals: deque[str] = deque(maxlen=_MAX_RETAINED_REFUSALS)

    @property
    def completed(self) -> bool:
        """Whether preparation succeeded for every module."""

        return self._completed

    @property
    def refused(self) -> int:
        """How many admissions and producer starts the gate has refused."""

        return self._refused

    @property
    def refusals(self) -> tuple[str, ...]:
        """The most recent refusal diagnostics, bounded and sanitised."""

        return tuple(self._refusals)

    def complete(self) -> None:
        """Open the gate. Idempotent, so a repeated phase changes nothing."""

        self._completed = True
        self._event.set()

    async def wait(self) -> None:
        """Block until the gate opens."""

        await self._event.wait()

    def admits(self) -> bool:
        """Whether input may be admitted right now, without raising."""

        return self._completed

    def guard_admission(self, module: str) -> None:
        """Refuse an input offered before the barrier completes (AC14)."""

        if self._completed:
            return
        self._refuse(module, "input refused before the readiness barrier")

    def guard_producer(self, module: str) -> None:
        """Refuse a producer that starts before the barrier completes (AC14)."""

        if self._completed:
            return
        self._refuse(module, "producer started before the readiness barrier")

    def _refuse(self, module: str, reason: str) -> None:
        violation = BarrierViolation(module, reason)
        self._refused += 1
        self._refusals.append(str(violation))
        raise violation


# --------------------------------------------------------------------------- #
# Supervised tasks
# --------------------------------------------------------------------------- #


class SupervisedTasks:
    """Owner of every detached task, so none is orphaned or unobserved (R4).

    ``spawn`` is the only way a run becomes detached. The registry keeps the
    task, observes its completion — a failure is collected as a sanitised
    diagnostic naming its owner, never left as an unretrieved exception — and
    releases it when it ends.

    ``drain`` gives the owned set the drain budget to finish. When that budget
    is exhausted the remaining tasks are cancelled and then given a **grace
    period**: a run holding an in-flight write needs that window to let its
    executor classify the call and publish its terminal outcome, so a
    deadline-cancelled run still reports a terminal status instead of
    vanishing (AC15, and the reason it never contradicts AC33). Only a task
    that raises, or one that survives its cancellation grace, is a failure.
    """

    __slots__ = (
        "_abandoned",
        "_clock",
        "_closed",
        "_failures",
        "_idle",
        "_sleep",
        "_tasks",
    )

    def __init__(
        self,
        *,
        clock: Clock = time.monotonic,
        sleeper: Sleeper | None = None,
    ) -> None:
        if not callable(clock):
            raise LifecycleError("supervised tasks: field 'clock': must be callable")
        if sleeper is not None and not callable(sleeper):
            raise LifecycleError("supervised tasks: field 'sleeper': must be callable")
        self._clock = clock
        self._sleep: Sleeper = sleeper if sleeper is not None else asyncio.sleep
        self._tasks: dict[asyncio.Task[Any], tuple[str, str]] = {}
        # Tasks that resisted their cancellation, at the drain deadline or at
        # an owner's own bound. They stay referenced here until they end: a
        # pending task nothing references is destroyed by the collector,
        # unobserved, before the loop's teardown reaches it.
        self._abandoned: list[asyncio.Task[Any]] = []
        self._failures: list[str] = []
        self._closed = False
        self._idle = asyncio.Event()
        self._idle.set()

    @property
    def active(self) -> int:
        """How many owned tasks have not completed yet."""

        return len(self._tasks)

    @property
    def closed(self) -> bool:
        """Whether the registry refuses new work."""

        return self._closed

    @property
    def failures(self) -> tuple[str, ...]:
        """Sanitised diagnostics for every observed task failure."""

        return tuple(self._failures)

    @property
    def abandoned(self) -> tuple[asyncio.Task[Any], ...]:
        """The tasks that resisted cancellation and have still not ended.

        Each one is already a recorded failure; the registry holds them so
        their eventual end is observed by the loop's teardown, not the
        collector.
        """

        self._abandoned = [task for task in self._abandoned if not task.done()]
        return tuple(self._abandoned)

    def owners(self) -> tuple[str, ...]:
        """The owners of the tasks still running, in registration order."""

        return tuple(owner for _, owner in self._tasks.values())

    def spawn(
        self,
        coro: Awaitable[Any],
        *,
        name: str,
        owner: str,
    ) -> asyncio.Task[Any]:
        """Register *coro* as a detached task owned by *owner*.

        Refused once the registry is closed, so nothing detaches after the
        drain has finished and could outlive the process teardown.
        """

        if not inspect.isawaitable(coro):
            _discard(coro)
            raise LifecycleError(
                f"module {_name(owner)!r}: field 'spawn': must be awaitable"
            )
        task_name = _require_text(name, owner, "spawn.name", coro)
        task_owner = _require_text(owner, owner, "spawn.owner", coro)
        if self._closed:
            _discard(coro)
            raise LifecycleError(
                f"module {task_owner!r}: task {task_name!r} refused: "
                "the supervised registry is closed"
            )

        task = asyncio.ensure_future(coro)
        self._tasks[task] = (task_name, task_owner)
        self._idle.clear()
        task.add_done_callback(self._observe)
        return task

    def abandon(
        self,
        task: "asyncio.Future[Any]",
        *,
        name: str,
        owner: str,
    ) -> str | None:
        """Take over a task its *owner* cancelled and could not stop (R4).

        For work an owner detached outside :meth:`spawn` — a publication a
        confirmed send handed to the loop, say — bounded by the owner's own
        budget and still running once that budget and its cancellation grace
        are spent. Handing it here transfers what is left of the ownership:
        the task is cancelled if it was not, stays referenced until it ends
        and has its late outcome consumed. The failure is recorded exactly as
        a drained task that resists cancellation is (AC15), so a shutdown
        that had to leave the task behind reports non-zero instead of
        reading the owner's normal return as success.

        Accepted after :meth:`aclose`, since nothing new detaches — the task
        already had. A task already done is consumed and is no failure.
        Returns the diagnostic recorded, or ``None``.
        """

        if not isinstance(task, asyncio.Future):
            raise LifecycleError(
                f"module {_name(owner)!r}: field 'abandon': must be a task"
            )
        task_name = _require_text(name, owner, "abandon.name", None)
        task_owner = _require_text(owner, owner, "abandon.owner", None)
        if task.done():
            _consume_result(task)
            return None
        if not _cancellation_requested(task):
            # One request is enough: a second ``CancelledError`` thrown into a
            # task that is already unwinding — cleaning up slowly in a
            # ``finally`` — would interrupt the very cleanup it was given.
            task.cancel()
        task.add_done_callback(_consume_result)
        self._abandoned.append(task)
        failure = (
            f"module {task_owner!r}: task {task_name!r} did not stop when cancelled"
        )
        self._failures.append(failure)
        return failure

    async def wait_idle(self) -> None:
        """Block until no owned task remains."""

        await self._idle.wait()

    async def drain(
        self, deadline_seconds: float, *, deadline_at: float | None = None
    ) -> DrainReport:
        """Finish owned tasks inside *deadline_seconds*, then cancel the rest.

        Returns what happened; the caller decides what makes the process
        status non-zero. *deadline_at* is the absolute point on the clock
        that the cancellation grace after the budget may not pass — the one
        global shutdown deadline when the coordinator drains under it (R4);
        without it the grace stands on its own.
        """

        budget = _finite(deadline_seconds, "drain deadline", allow_zero=True)
        completed = 0
        cancelled: list[str] = []
        failures: list[str] = []

        # One absolute deadline over the *changing* owned set: spawning stays
        # allowed while draining, so a child accepted by a task that then
        # finishes inherits whatever is left of the budget instead of being
        # cancelled the instant its parent returns. It is the budget's own
        # end, kept apart from the optional global *deadline_at*: the budget
        # is spent by the time the grace starts, so capping the grace by it
        # would grant none at all.
        budget_at = self._clock() + budget
        counted: set[asyncio.Task[Any]] = set()
        first = True
        while True:
            if any(task.done() for task in self._tasks):
                # A finished task leaves the owned set through its completion
                # callback. Give that callback its turn before judging the set,
                # so a task that has already returned is never miscounted as
                # still running — nor cancelled at the deadline.
                await asyncio.sleep(0)
            for task in list(self._tasks):
                if task.done() and task not in counted:
                    counted.add(task)
                    completed += 1
            waiting = {task for task in self._tasks if not task.done()}
            if not waiting:
                break
            remaining = budget_at - self._clock()
            if remaining <= 0 and not first:
                break
            first = False
            finished = await _wait_bounded(
                waiting, max(remaining, 0.0), self._sleep
            )
            for task in finished:
                if task not in counted:
                    counted.add(task)
                    completed += 1
            if waiting - finished:
                # The budget ran out with owned work still pending.
                break

        # Whatever is left has spent the whole budget. Cancel explicitly, then
        # allow the grace window for a terminal outcome to be published.
        for task, (task_name, task_owner) in list(self._tasks.items()):
            if task.done():
                continue
            task.cancel()
            cancelled.append(
                f"module {task_owner!r}: task {task_name!r} was cancelled "
                "at the drain deadline"
            )

        if self._tasks:
            waiting = set(self._tasks)
            finished = await _wait_bounded(
                waiting,
                _remaining(DEFAULT_CANCEL_GRACE_SECONDS, self._clock, deadline_at),
                self._sleep,
            )
            for task, (task_name, task_owner) in list(self._tasks.items()):
                if task.done() or task in finished:
                    continue
                failures.append(
                    f"module {task_owner!r}: task {task_name!r} did not stop "
                    "when cancelled"
                )
                # Retrieve even a late exception, without waiting for it, and
                # keep the reference so the task is not collected unobserved.
                task.add_done_callback(_consume_result)
                self._abandoned.append(task)
                self._tasks.pop(task, None)
            if not self._tasks:
                self._idle.set()

        return DrainReport(
            completed=completed,
            cancelled=tuple(cancelled),
            failures=tuple(failures),
        )

    async def aclose(
        self,
        deadline_seconds: float | None = None,
        *,
        deadline_at: float | None = None,
    ) -> DrainReport:
        """Drain, then refuse further work. Idempotent."""

        budget = (
            DEFAULT_DRAIN_DEADLINE_SECONDS
            if deadline_seconds is None
            else deadline_seconds
        )
        try:
            report = await self.drain(budget, deadline_at=deadline_at)
        finally:
            # Even a cancelled close refuses further work: the caller asked
            # for the registry to stop accepting, and a half-drained registry
            # that still spawns would outlive the teardown that cancelled it.
            self._closed = True
        return report

    def _observe(self, task: asyncio.Task[Any]) -> None:
        entry = self._tasks.pop(task, None)
        if not self._tasks:
            self._idle.set()
        if entry is None:
            return
        task_name, task_owner = entry
        if task.cancelled():
            return
        exception = task.exception()
        if exception is None:
            return
        # Sanitised: the owner, the task label and the fact of the failure.
        # No exception text reaches a diagnostic.
        self._failures.append(
            f"module {task_owner!r}: task {task_name!r} failed"
        )


# --------------------------------------------------------------------------- #
# Process watchdog (ported from core/main.py)
# --------------------------------------------------------------------------- #


class ProcessWatchdog:
    """The CLI's last-resort deadline, ported unchanged in behaviour.

    Cancelling a coroutine does not prove a native capture or a thread writer
    stopped. Once armed, the timer bypasses interpreter thread joins and
    stream flushing with a non-zero exit; pending records may be lost. An
    embedded caller owns its own process policy and simply never arms one.
    """

    __slots__ = ("_cancelled", "_started", "_status", "_timer")

    def __init__(
        self,
        seconds: float = DEFAULT_SHUTDOWN_DEADLINE_SECONDS,
        *,
        status: int = 1,
        exit_process: Callable[[int], Any] = os._exit,
    ) -> None:
        budget = _finite(seconds, "watchdog seconds")
        if not callable(exit_process):
            raise LifecycleError("watchdog: field 'exit_process': must be callable")
        if not isinstance(status, int) or isinstance(status, bool):
            raise LifecycleError("watchdog: field 'status': must be an integer")
        self._status = status
        self._started = False
        self._cancelled = False
        self._timer = threading.Timer(budget, exit_process, args=(status,))
        self._timer.daemon = True

    @property
    def status(self) -> int:
        """The process status the deadline exits with when it fires."""

        return self._status

    @property
    def armed(self) -> bool:
        """Whether the deadline is currently running."""

        return self._started and not self._cancelled

    def arm(self) -> None:
        """Start the deadline. Repeated calls neither restart nor revive it.

        A timer runs once: re-arming an armed deadline would reset it, and
        re-arming a cancelled one would resurrect a deadline the owner
        deliberately dropped.
        """

        if self._started or self._cancelled:
            return
        self._started = True
        self._timer.start()

    def cancel(self) -> None:
        """Stop the deadline."""

        self._cancelled = True
        self._timer.cancel()


_shutdown_watchdog: ContextVar[ProcessWatchdog | None] = ContextVar(
    "shutdown_watchdog", default=None
)


def install_shutdown_watchdog(watchdog: ProcessWatchdog | None) -> Token[Any]:
    """Make *watchdog* the one :func:`arm_shutdown_watchdog` arms."""

    return _shutdown_watchdog.set(watchdog)


def reset_shutdown_watchdog(token: Token[Any]) -> None:
    """Restore the watchdog installed before *token* was taken."""

    _shutdown_watchdog.reset(token)


def arm_shutdown_watchdog() -> None:
    """Arm the installed watchdog, if there is one. Ported from ``core.main``."""

    watchdog = _shutdown_watchdog.get()
    if watchdog is not None:
        watchdog.arm()


# --------------------------------------------------------------------------- #
# Roles
# --------------------------------------------------------------------------- #


def module_roles(module: Any) -> frozenset[str]:
    """The declared lifecycle roles of *module*, never inferred from its name.

    A role comes from the activation's own ``roles`` field when the loader
    supplies one, and otherwise from ``lifecycle.roles`` in the declared
    manifest. An undeclared role name is a defect naming the module and the
    field, not a silently ignored entry.
    """

    name = _module_name(module)
    declared = getattr(module, "roles", None)
    if declared is None:
        manifest = getattr(module, "manifest", None)
        section = (
            manifest.get(MANIFEST_LIFECYCLE_KEY)
            if isinstance(manifest, Mapping)
            else None
        )
        declared = (
            section.get(MANIFEST_ROLES_KEY) if isinstance(section, Mapping) else None
        )
    if declared is None:
        return frozenset()
    if isinstance(declared, str) or not isinstance(declared, (Sequence, frozenset, set)):
        raise LifecycleError(
            f"module {name!r}: field 'lifecycle.roles': must be a list of roles"
        )
    roles: set[str] = set()
    for role in declared:
        if not isinstance(role, str) or role not in ROLES:
            known = ", ".join(sorted(ROLES))
            raise LifecycleError(
                f"module {name!r}: field 'lifecycle.roles': "
                f"unknown role; declared roles are: {known}"
            )
        roles.add(role)
    return frozenset(roles)


# --------------------------------------------------------------------------- #
# Bounded close (ported from core/main.py::_close_activations)
# --------------------------------------------------------------------------- #


async def close_modules(
    modules: Sequence[Any],
    *,
    timeout_seconds: float = DEFAULT_HOOK_TIMEOUT_SECONDS,
    cancel_grace_seconds: float = DEFAULT_CANCEL_GRACE_SECONDS,
    sleeper: Sleeper | None = None,
    clock: Clock = time.monotonic,
    deadline_at: float | None = None,
) -> list[str]:
    """Close *modules* in reverse order, bounded, collecting every failure.

    This is ``core/main.py::_close_activations`` moved here, with one
    deliberate change required by R4: the name map that sorted the platform
    module first and ``audit`` last is gone. Ordering now comes from the
    declared observation role — observation services close after ordinary
    resources — and otherwise from reverse activation order.

    A close that overruns its budget is cancelled explicitly, given a short
    cancellation window — capped, like the budget, by what remains of
    *deadline_at* — and left with a callback that retrieves even a late
    exception so nothing is reported as never-retrieved.
    """

    arm_shutdown_watchdog()
    budget = _finite(timeout_seconds, "close timeout", allow_zero=True)
    grace = _finite(cancel_grace_seconds, "cancel grace", allow_zero=True)
    wait = sleeper if sleeper is not None else asyncio.sleep
    failures: list[str] = []
    for module in _close_order(modules):
        close = _close_callable(module)
        if close is None:
            continue
        remaining = _remaining(budget, clock, deadline_at)
        task = asyncio.ensure_future(close())
        try:
            finished = await _wait_bounded({task}, remaining, wait)
            if task not in finished:
                task.cancel()
                # The grace is capped by the same deadline as the close (R4).
                await _wait_bounded(
                    {task}, _remaining(grace, clock, deadline_at), wait
                )
                # Retrieve even late exceptions without waiting indefinitely.
                task.add_done_callback(_consume_result)
                raise TimeoutError
            if task.cancelled():
                raise RuntimeError("close was cancelled")
            task.result()
        except asyncio.CancelledError:
            task.cancel()
            task.add_done_callback(_consume_result)
            raise
        except Exception:
            failures.append(f"module {_module_name(module)!r}: shutdown failed")
    return failures


def _close_order(modules: Sequence[Any]) -> list[Any]:
    ordered = list(reversed(list(modules)))
    return sorted(
        ordered,
        key=lambda module: 1 if ROLE_OBSERVATION in module_roles(module) else 0,
    )


def _close_callable(module: Any) -> Callable[[], Awaitable[Any]] | None:
    """The idempotent close of *module*, or ``None`` when it has no role here."""

    handle = getattr(module, "handle", module)
    if not _is_async_callable(getattr(handle, "close", None)):
        return None
    owned = getattr(module, "close", None)
    if module is not handle and _is_async_callable(owned):
        # The activation's own close is the idempotent one; prefer it.
        return owned
    return getattr(handle, "close")


# --------------------------------------------------------------------------- #
# Phase coordinator
# --------------------------------------------------------------------------- #


class PhaseCoordinator:
    """Drive the fixed phase sequence over declared module roles (R4).

    The coordinator never reads a module name to decide what to do with it. It
    reads the declared role, looks for the phase's hook on the returned handle,
    and treats a missing hook as the no-op of a module with no role in that
    phase.

    Every hook runs under ``min(hook_timeout, remaining global budget)``, so
    however many modules there are, startup cannot exceed one finite startup
    deadline and shutdown cannot exceed one finite shutdown deadline.

    *compatibility* holds the activations that took the loader's v1 route.
    They have no startup phase here — a v1 module opens its transport at
    activation — and exactly one shutdown hook, ``close()``, which stops
    their inputs, drains what they accepted and releases their resources all
    at once. The coordinator runs it as its own step of the shutdown
    sequence, between the drain phase and the close phase, so that in a
    mixed pipeline every direction is safe: a versioned producer has stopped
    and drained before a v1 sink closes, and a v1 producer has closed before
    the versioned consumers and observation services it publishes into close
    or flush. The step shares the one global shutdown deadline, whether
    shutdown was requested or is the unwinding of a failed startup.

    *observer* is told of every phase transition the coordinator completes,
    as ``observer(phase, module_name, failure=None)`` after a hook returned —
    a no-op hook included, since the phase is then complete for that module
    — and as ``observer(phase, module_name, failure=<diagnostic>)`` after one
    failed, timed out or was cancelled. It is how the runtime's health owner
    turns the sequence into ``module.ready``, ``module.degraded`` and
    ``module.stopped`` (R8) without the coordinator knowing a trace type or a
    module name. The call is bounded by the same allowance as the hook it
    follows, and nothing it raises reaches the sequence: a health report
    that fails is noted as a diagnostic and the next module is still driven.
    """

    __slots__ = (
        "_abandoned",
        "_barrier",
        "_cancel_grace_seconds",
        "_clock",
        "_compatibility",
        "_current",
        "_diagnostics",
        "_drain_deadline_seconds",
        "_failures",
        "_hook_timeout_seconds",
        "_invoked",
        "_modules",
        "_observer",
        "_ready",
        "_reported_task_failures",
        "_reporter",
        "_shutdown_deadline_seconds",
        "_shutdown_cancelled",
        "_shutdown_task",
        "_sleep",
        "_started",
        "_stopped",
        "_startup_deadline_seconds",
        "_tasks",
    )

    def __init__(
        self,
        modules: Iterable[Any],
        *,
        compatibility: Iterable[Any] = (),
        startup_deadline_seconds: float = DEFAULT_STARTUP_DEADLINE_SECONDS,
        shutdown_deadline_seconds: float = DEFAULT_SHUTDOWN_DEADLINE_SECONDS,
        hook_timeout_seconds: float = DEFAULT_HOOK_TIMEOUT_SECONDS,
        drain_deadline_seconds: float = DEFAULT_DRAIN_DEADLINE_SECONDS,
        cancel_grace_seconds: float = DEFAULT_CANCEL_GRACE_SECONDS,
        clock: Clock = time.monotonic,
        sleeper: Sleeper | None = None,
        tasks: SupervisedTasks | None = None,
        barrier: ReadinessBarrier | None = None,
        reporter: Reporter | None = None,
        observer: PhaseObserver | None = None,
    ) -> None:
        if not callable(clock):
            raise LifecycleError("lifecycle: field 'clock': must be callable")
        if sleeper is not None and not callable(sleeper):
            raise LifecycleError("lifecycle: field 'sleeper': must be callable")
        if reporter is not None and not callable(reporter):
            raise LifecycleError("lifecycle: field 'reporter': must be callable")
        if observer is not None and not callable(observer):
            raise LifecycleError("lifecycle: field 'observer': must be callable")

        self._modules = list(modules)
        for module in self._modules:
            # Refuse an undeclared role before any transport is opened.
            module_roles(module)
        # A v1 activation declares no role: it is routed by its contract, not
        # by anything read from it, so nothing is validated here.
        self._compatibility = list(compatibility)

        self._startup_deadline_seconds = _finite(
            startup_deadline_seconds, "startup_deadline_seconds"
        )
        self._shutdown_deadline_seconds = _finite(
            shutdown_deadline_seconds, "shutdown_deadline_seconds"
        )
        self._hook_timeout_seconds = _finite(
            hook_timeout_seconds, "hook_timeout_seconds"
        )
        self._drain_deadline_seconds = _finite(
            drain_deadline_seconds, "drain_deadline_seconds", allow_zero=True
        )
        self._cancel_grace_seconds = _finite(
            cancel_grace_seconds, "cancel_grace_seconds", allow_zero=True
        )

        self._clock = clock
        self._sleep: Sleeper = sleeper if sleeper is not None else asyncio.sleep
        self._reporter = reporter
        self._observer = observer
        self._barrier = barrier if barrier is not None else ReadinessBarrier()
        self._tasks = (
            tasks
            if tasks is not None
            else SupervisedTasks(clock=clock, sleeper=self._sleep)
        )
        self._invoked: set[tuple[str, str]] = set()
        # Hook tasks given up on — overrunning, or left behind by a cancelled
        # caller — stay referenced here: a pending task nothing references is
        # destroyed by the collector, with a warning on stderr, before the
        # watchdog or the loop's own teardown reaches it.
        self._abandoned: list[asyncio.Task[Any]] = []
        self._diagnostics: list[str] = []
        self._failures: list[str] = []
        self._current: tuple[str, str] | None = None
        self._reported_task_failures = 0
        self._started = False
        self._stopped = False
        self._shutdown_task: asyncio.Task[Any] | None = None
        self._shutdown_cancelled = False
        self._ready = False

    # -- read-only surface --------------------------------------------------- #

    @property
    def barrier(self) -> ReadinessBarrier:
        """The gate ingestion and producers consult (AC14)."""

        return self._barrier

    @property
    def tasks(self) -> SupervisedTasks:
        """The registry that owns every detached run."""

        return self._tasks

    @property
    def ready(self) -> bool:
        """Whether startup completed through the last producer."""

        return self._ready

    @property
    def abandoned(self) -> tuple[asyncio.Task[Any], ...]:
        """The hook tasks given up on that have still not ended.

        Each one is already a recorded failure; the coordinator keeps the
        reference so a hook that resisted its cancellation past the global
        deadline stays owned — never waited for again — until it ends.
        """

        self._abandoned = [task for task in self._abandoned if not task.done()]
        return tuple(self._abandoned)

    @property
    def report(self) -> LifecycleReport:
        """The outcome so far: non-zero as soon as anything failed."""

        return LifecycleReport(
            status=1 if self._failures else 0,
            diagnostics=tuple(self._diagnostics),
            failures=tuple(self._failures),
        )

    def collect_task_failures(self) -> tuple[str, ...]:
        """Report every supervised-task failure not reported yet.

        Called at the end of shutdown, and callable at any time by a
        long-running owner that wants failures surfaced as they happen rather
        than at teardown.
        """

        observed = self._tasks.failures
        fresh = observed[self._reported_task_failures :]
        self._reported_task_failures = len(observed)
        for failure in fresh:
            self._fail(failure)
        return fresh

    def producers(self) -> tuple[Any, ...]:
        """Modules declaring the input role, in activation order."""

        return tuple(
            module for module in self._modules if ROLE_INPUT in module_roles(module)
        )

    def observers(self) -> tuple[Any, ...]:
        """Modules declaring the observation role, in activation order."""

        return tuple(
            module
            for module in self._modules
            if ROLE_OBSERVATION in module_roles(module)
        )

    def compatibility(self) -> tuple[Any, ...]:
        """The v1 activations closed between drain and close, in activation order."""

        return tuple(self._compatibility)

    # -- phases -------------------------------------------------------------- #

    async def start(self, *, deadline_at: float | None = None) -> LifecycleReport:
        """Validate, prepare, open the barrier, then start producers.

        Idempotent: a second call reports the first outcome and invokes no
        hook again. On any failure the modules already prepared are shut down
        through the ordinary shutdown sequence before the report is returned,
        so a partial startup never leaks a transport.

        *deadline_at* is the absolute point on the injected clock by which
        startup must be done. It is the one global startup deadline, and the
        caller that already spent part of it — on loading and activation —
        hands the same point here so validation, activation and the phases
        share one budget instead of each getting a fresh one (R4). Without
        it the coordinator takes its own ``startup_deadline_seconds`` from
        now.
        """

        if self._started:
            return self.report
        self._started = True
        if deadline_at is None:
            deadline_at = self._clock() + self._startup_deadline_seconds

        try:
            if not self._validate_hooks():
                # A malformed hook is a defect of the module, not of a run.
                # Refuse it before anything is prepared, so no transport is
                # opened only to be torn down again.
                raise _StartupAborted
            for phase in (PHASE_VALIDATE_SETTINGS, PHASE_PREPARE):
                for module in self._modules:
                    if not await self._run_hook(
                        module, phase, deadline_at=deadline_at, budget="startup"
                    ):
                        raise _StartupAborted
            for module in self.producers():
                if not self._has_hook(module, PHASE_START_INPUTS):
                    # A producer with no way to wait for the barrier cannot be
                    # started safely: it would open its source before the gate.
                    # This is the refusal a v1 producer meets, and it happens
                    # before the gate opens, not after.
                    self._fail(
                        f"module {_module_name(module)!r}: "
                        "cannot honour the readiness barrier"
                    )
                    raise _StartupAborted
            self._barrier.complete()
            for module in self.producers():
                if not await self._run_hook(
                    module,
                    PHASE_START_INPUTS,
                    deadline_at=deadline_at,
                    budget="startup",
                ):
                    raise _StartupAborted
        except _StartupAborted:
            await self._shutdown()
            return self.report
        except asyncio.CancelledError:
            self._note_cancellation()
            await self._shutdown()
            raise

        self._ready = True
        return self.report

    async def stop(self, *, deadline_at: float | None = None) -> LifecycleReport:
        """Stop producers, drain, close resources, then flush observation.

        Idempotent, and safe after a failed :meth:`start` — every hook already
        invoked is skipped.

        *deadline_at* is the absolute point on the injected clock by which the
        whole cleanup must be done: the one global shutdown deadline, when the
        caller shares it with another cleanup owner — the loader's late handle
        closes — so the two never take a budget each (R4). Without it a
        sequence begun here takes the shutdown budget from now; a sequence
        already under way keeps the deadline it began with.
        """

        await self._shutdown(deadline_at=deadline_at)
        return self.report

    async def _shutdown(self, *, deadline_at: float | None = None) -> None:
        """Run the shutdown half of the sequence exactly once.

        Reached both from :meth:`stop` and from a failed or cancelled
        :meth:`start`; a caller that was itself cancelled still gets the
        complete cleanup before the cancellation propagates, which is the
        guarantee the entry point made before this logic moved here. The
        phases therefore run in their own task, shielded from the caller's
        cancellation, and every entry awaits that same task.

        Started is not finished: ``_stopped`` only records that the sequence
        began. Cleanup counts as done when the phase task *completed*, so a
        task cancelled part-way — the loop tearing every task down, not merely
        the caller being cancelled — leaves the phases it never reached still
        owed, and the next entry resumes them. Every hook already invoked
        stays invoked, so a resumed sequence repeats none of them and the
        resumption terminates.
        """

        task = self._shutdown_task
        if task is not None and task.done():
            # Retrieve any late failure before deciding what to do next.
            _consume_result(task)
            if not task.cancelled():
                return
            # Cancelled part-way: cleanup is unfinished, not complete. Report
            # it even when no caller was awaiting to see the cancellation.
            self._note_shutdown_cancelled()
            self._shutdown_task = task = None
        if task is None:
            self._stopped = True
            self._shutdown_cancelled = False
            arm_shutdown_watchdog()
            if deadline_at is None:
                deadline_at = self._clock() + self._shutdown_deadline_seconds
            task = asyncio.ensure_future(self._shutdown_phases(deadline_at))
            self._shutdown_task = task
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            # Cancelling the *caller* must not abandon cleanup half-done: the
            # remaining phases keep running and are awaited before the
            # cancellation propagates.
            if not task.done():
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
            if task.cancelled():
                self._note_shutdown_cancelled()
                # The phases it never reached are still owed, so the next
                # entry resumes them instead of reporting a clean stop.
                self._shutdown_task = None
            raise

    def _note_shutdown_cancelled(self) -> None:
        """Record an abandoned shutdown once, however it is observed."""

        if self._shutdown_cancelled:
            return
        self._shutdown_cancelled = True
        self._fail("lifecycle: shutdown was cancelled before it completed")

    async def _shutdown_phases(self, deadline_at: float) -> None:
        for module in reversed(self.producers()):
            await self._run_hook(
                module, PHASE_STOP_INPUTS, deadline_at=deadline_at, budget="shutdown"
            )

        await self._drain(deadline_at)

        # Every versioned transport, store and observation service is still
        # open: whatever a v1 module drains here still has somewhere to go.
        await self._close_compatibility(deadline_at)

        observers = set(map(id, self.observers()))
        ordinary = [
            module for module in self._modules if id(module) not in observers
        ]
        await self._close_phase(ordinary, PHASE_CLOSE, deadline_at)

        for module in self.observers():
            await self._run_hook(
                module, PHASE_FLUSH, deadline_at=deadline_at, budget="shutdown"
            )
        await self._close_phase(
            list(self.observers()), PHASE_CLOSE_OBSERVATION, deadline_at
        )

        self.collect_task_failures()

    async def _drain(self, deadline_at: float) -> None:
        """Finish accepted work: owned runs first, then module drain hooks.

        Owned runs go first on purpose. Every transport, executor and store is
        still open at this point, so a run cancelled at the deadline can still
        have its in-flight call classified and its terminal outcome published
        before the close phase takes those resources away.
        """

        drain = await self._tasks.drain(
            self._drain_budget(deadline_at), deadline_at=deadline_at
        )
        for message in drain.cancelled:
            self._note(message)
        for message in drain.failures:
            self._fail(message)

        for module in reversed(self._modules):
            await self._run_hook(
                module, PHASE_DRAIN, deadline_at=deadline_at, budget="shutdown"
            )

        # The drain phase is over: nothing may detach any more. A task spawned
        # by a drain or close hook would otherwise outlive the transports about
        # to close, with no later drain left to observe it.
        closing = await self._tasks.aclose(
            self._drain_budget(deadline_at), deadline_at=deadline_at
        )
        for message in closing.cancelled:
            self._note(message)
        for message in closing.failures:
            self._fail(message)

    async def _close_compatibility(self, deadline_at: float) -> None:
        """Close the v1 activations one at a time, in activation order.

        A v1 configuration lists its pipeline from source to sink, and a v1
        close is the module's whole shutdown, so closing the source first
        lets the work it accepted reach the sinks before those close — the
        guarantee the name-ordered entry point gave, given here without a
        name. Each close gets the hook grace period capped by what remains
        of the global shutdown deadline; one that overruns is cancelled and
        the next module is still closed. A close already run — a resumed
        sequence — is not run again.
        """

        for module in self._compatibility:
            name = _module_name(module)
            key = (name, PHASE_CLOSE)
            if key in self._invoked:
                continue
            self._invoked.add(key)
            failures = await close_modules(
                [module],
                timeout_seconds=self._hook_timeout_seconds,
                cancel_grace_seconds=self._cancel_grace_seconds,
                sleeper=self._sleep,
                clock=self._clock,
                deadline_at=deadline_at,
            )
            for failure in failures:
                self._fail(failure)
            # A v1 close is the module's whole shutdown: stopped, or degraded
            # by the first failure its close met.
            await self._observe(
                PHASE_CLOSE,
                name,
                failures[0] if failures else None,
                deadline_at=deadline_at,
            )

    def _drain_budget(self, deadline_at: float) -> float:
        """The drain allowance left inside the global shutdown deadline."""

        return max(
            min(
                self._drain_deadline_seconds,
                _remaining_to(self._clock, deadline_at),
            ),
            0.0,
        )

    async def _close_phase(
        self, modules: Sequence[Any], phase: str, deadline_at: float
    ) -> None:
        for module in _close_order(modules):
            await self._run_hook(
                module, phase, deadline_at=deadline_at, budget="shutdown"
            )

    # -- one hook ------------------------------------------------------------ #

    async def _run_hook(
        self,
        module: Any,
        phase: str,
        *,
        deadline_at: float,
        budget: str,
    ) -> bool:
        """Run one bounded, idempotent hook. ``False`` when it failed."""

        name = _module_name(module)
        key = (name, phase)
        if key in self._invoked:
            return True
        self._invoked.add(key)

        failure = await self._invoke_hook(
            module, name, phase, deadline_at=deadline_at, budget=budget
        )
        if failure is not None:
            self._fail(failure)
        # The transition is complete for this module either way; the health
        # owner is told which way, under the hook's own allowance (R8).
        await self._observe(phase, name, failure, deadline_at=deadline_at)
        return failure is None

    async def _invoke_hook(
        self,
        module: Any,
        name: str,
        phase: str,
        *,
        deadline_at: float,
        budget: str,
    ) -> str | None:
        """Invoke one hook. Returns the failure diagnostic, or ``None``."""

        try:
            hook = self._resolve_hook(module, phase)
        except LifecycleError as defect:
            # A malformed hook is reported like any other phase failure, so a
            # startup unwinds through the ordinary shutdown and a shutdown
            # still reaches every remaining module.
            return str(defect)
        if hook is None:
            # A module with no role in this phase supplies a no-op.
            return None

        remaining = _remaining_to(self._clock, deadline_at)
        allowance = min(self._hook_timeout_seconds, remaining)
        if allowance <= 0:
            return (
                f"module {name!r}: phase {phase!r} exceeded the global "
                f"{budget} deadline"
            )

        self._current = (name, phase)
        arguments = (allowance,) if phase in _DEADLINE_PHASES else ()
        try:
            # An incompatible signature raises here, before there is a task.
            task = asyncio.ensure_future(hook(*arguments))
        except asyncio.CancelledError:
            raise
        except Exception:
            return f"module {name!r}: phase {phase!r} failed"
        try:
            finished = await _wait_bounded({task}, allowance, self._sleep)
            if task not in finished:
                task.cancel()
                # The grace to observe the cancellation is capped by the same
                # global deadline as the hook itself (R4): a hook cut at
                # that deadline gets one scheduling turn, never a fresh
                # window past the one absolute point the caller waits on.
                await _wait_bounded(
                    {task},
                    _remaining(self._cancel_grace_seconds, self._clock, deadline_at),
                    self._sleep,
                )
                self._abandon(task)
                return f"module {name!r}: phase {phase!r} timed out"
            if task.cancelled():
                return f"module {name!r}: phase {phase!r} was cancelled"
            task.result()
        except asyncio.CancelledError:
            task.cancel()
            self._abandon(task)
            raise
        except Exception:
            return f"module {name!r}: phase {phase!r} failed"
        return None

    def _abandon(self, task: "asyncio.Task[Any]") -> None:
        """Give up on *task*: its late outcome is consumed, never awaited."""

        task.add_done_callback(_consume_result)
        if not task.done():
            self._abandoned.append(task)

    async def _observe(
        self,
        phase: str,
        name: str,
        failure: str | None,
        *,
        deadline_at: float,
    ) -> None:
        """Tell the observer of one completed transition, bounded, never raising.

        The observer publishes on the bus, so it is given the same allowance a
        hook gets inside the global deadline and no more; one that overruns
        is cancelled and noted. A failure inside it is a diagnostic, never a
        lifecycle failure: a health fact that could not be reported must not
        stop the module that comes next from being driven.
        """

        if self._observer is None:
            return
        allowance = min(
            self._hook_timeout_seconds, _remaining_to(self._clock, deadline_at)
        )
        try:
            outcome = self._observer(phase, name, failure=failure)
            if not inspect.isawaitable(outcome):
                return
            task = asyncio.ensure_future(outcome)
        except asyncio.CancelledError:
            raise
        except Exception:
            self._note(f"module {name!r}: phase {phase!r}: health report failed")
            return
        try:
            finished = await _wait_bounded({task}, max(allowance, 0.0), self._sleep)
            if task not in finished:
                task.cancel()
                self._abandon(task)
                self._note(f"module {name!r}: phase {phase!r}: health report timed out")
                return
            if task.cancelled():
                self._note(f"module {name!r}: phase {phase!r}: health report failed")
                return
            task.result()
        except asyncio.CancelledError:
            task.cancel()
            self._abandon(task)
            raise
        except Exception:
            self._note(f"module {name!r}: phase {phase!r}: health report failed")

    def _resolve_hook(
        self, module: Any, phase: str
    ) -> Callable[..., Awaitable[Any]] | None:
        if phase in (PHASE_CLOSE, PHASE_CLOSE_OBSERVATION):
            return _close_callable(module)
        handle = getattr(module, "handle", module)
        hook = getattr(handle, HOOK_NAMES[phase], None)
        if hook is None:
            return None
        if not _is_async_callable(hook):
            raise LifecycleError(
                f"module {_module_name(module)!r}: field "
                f"{HOOK_NAMES[phase]!r}: async lifecycle hook is required"
            )
        return hook

    def _has_hook(self, module: Any, phase: str) -> bool:
        try:
            return self._resolve_hook(module, phase) is not None
        except LifecycleError:
            # Present but malformed: :meth:`_validate_hooks` already named it.
            return True

    def _validate_hooks(self) -> bool:
        """Resolve every phase hook of every module. ``False`` on any defect.

        Runs before the first hook is invoked, so a module exposing a
        synchronous hook is refused while nothing has been prepared yet.
        """

        sound = True
        for module in self._modules:
            for phase in HOOK_NAMES:
                try:
                    self._resolve_hook(module, phase)
                except LifecycleError as defect:
                    self._fail(str(defect))
                    sound = False
        return sound

    # -- diagnostics --------------------------------------------------------- #

    def _note_cancellation(self) -> None:
        if self._current is None:
            self._fail("lifecycle: startup was cancelled")
            return
        name, phase = self._current
        self._fail(f"module {name!r}: phase {phase!r} was cancelled")

    def _note(self, message: str) -> None:
        self._diagnostics.append(message)
        if self._reporter is not None:
            self._reporter(message)

    def _fail(self, message: str) -> None:
        self._failures.append(message)
        self._note(message)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


async def _wait_bounded(
    tasks: set[asyncio.Task[Any]],
    seconds: float,
    sleeper: Sleeper,
) -> set[asyncio.Task[Any]]:
    """Wait at most *seconds* for *tasks*; return those that finished.

    The budget is spent through the injected sleeper, never through
    ``asyncio.wait``'s own timer, so a test can drive every deadline from an
    injected timeline instead of real time.
    """

    pending = {task for task in tasks if not task.done()}
    finished = {task for task in tasks if task.done()}
    if not pending:
        return finished
    if seconds <= 0:
        # Still give the tasks one scheduling turn: a hook that completes
        # immediately must not be reported as overrunning a zero budget.
        await asyncio.sleep(0)
        return {task for task in tasks if task.done()}

    timer = asyncio.ensure_future(sleeper(seconds))
    try:
        while True:
            pending = {task for task in tasks if not task.done()}
            if not pending or timer.done():
                break
            await asyncio.wait(
                pending | {timer}, return_when=asyncio.FIRST_COMPLETED
            )
    finally:
        timer.cancel()
        timer.add_done_callback(_consume_result)
    return {task for task in tasks if task.done()}


def _cancellation_requested(task: "asyncio.Future[Any]") -> bool:
    """Whether *task* already has a cancellation request pending.

    A task records its requests (``Task.cancelling``, 3.11+); a bare future
    does not, and is asked again — cancelling a future twice is harmless.
    """

    cancelling = getattr(task, "cancelling", None)
    if not callable(cancelling):
        return False
    return cancelling() > 0


def _consume_result(task: asyncio.Task[Any]) -> None:
    if not task.cancelled():
        task.exception()


def _discard(coro: Any) -> None:
    """Close a coroutine that will never be scheduled."""

    close = getattr(coro, "close", None)
    if callable(close):
        close()


def _remaining(
    budget: float, clock: Clock, deadline_at: float | None
) -> float:
    if deadline_at is None:
        return budget
    return max(min(budget, deadline_at - clock()), 0.0)


def _remaining_to(clock: Clock, deadline_at: float) -> float:
    return deadline_at - clock()


def _is_async_callable(value: Any) -> bool:
    return callable(value) and (
        inspect.iscoroutinefunction(value)
        or inspect.iscoroutinefunction(getattr(value, "__call__", None))
    )


def _module_name(module: Any) -> str:
    return _name(getattr(module, "name", None))


def _name(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        return "<unknown>"
    return value[:_MAX_DIAGNOSTIC_NAME]


def _require_text(value: Any, owner: Any, field: str, coro: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        _discard(coro)
        raise LifecycleError(
            f"module {_name(owner)!r}: field {field!r}: must be a non-empty string"
        )
    return value[:_MAX_DIAGNOSTIC_NAME]


def _finite(value: Any, field: str, *, allow_zero: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LifecycleError(f"lifecycle: field {field!r}: must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise LifecycleError(f"lifecycle: field {field!r}: must be finite")
    if number < 0 or (number == 0 and not allow_zero):
        raise LifecycleError(f"lifecycle: field {field!r}: must be positive")
    return number
