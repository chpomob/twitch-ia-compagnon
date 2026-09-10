"""Bounded admission scheduler owning the run terminal state (R2, R8).

Ingestion validates a normalised event, copies it and hands it here; the
handler then returns. :meth:`AdmissionScheduler.admit` is **synchronous and
non-blocking**: it validates, takes an independent deep copy of the payload so
a later mutation of the bus event cannot reach the queued work, enqueues and
returns an :class:`AdmissionResult`. It never awaits the model, never awaits a
worker and never awaits a trace publication, so a second message and a
keepalive frame can be ingested while a first run is still held inside a
pending model call (R2, AC6).

Everything the scheduler holds is bounded and every bound is required, finite
and validated at construction, raising a :class:`~core.contracts.ContractError`
that names the offending setting so ``core.main`` can stop startup on it (R2):
one FIFO queue per session capped at ``session_queue_capacity``, a global
``global_pending_capacity`` over every queue, a cap of ``max_sessions`` live
sessions and a pool of exactly ``workers`` workers. Saturation is a **refusal**:
a full session queue or a reached global cap rejects the work, traces the
saturation reason with the depth of the queue that saturated, counts it and
promises no replay (AC7).

Two deadlines, both measured on the injected monotonic clock and both fixed at
admission. ``total_run_seconds`` is the single configured total run duration:
``total_deadline`` is the admission instant plus that duration and bounds the
**whole** run, model call and delivery included. ``wait_seconds`` gives the
queue wait deadline, clamped at admission so it can never exceed the total
deadline — otherwise a ``stale_drop`` could outlive the run budget it belongs
to.

A wait deadline reached before a worker picks the work up ends it in terminal
status ``timeout`` with reason ``stale_drop``, traced with its waited duration
and counted, having produced 0 model calls and 0 sends (AC8). Expiry is
detected by a reaper rather than at dequeue, because with every worker busy the
work would otherwise sit in the queue unexamined and outlive its own deadline.

The total deadline reached **after** the run started stops the run at its next
boundary rather than starting further work: the scheduler marks the
:class:`RunContext` expired, lets the body reach that boundary and only then
abandons it, ending the run ``timeout`` with reason ``run_deadline``, traced
with its total duration and counted (AC33). The boundary matters: an action
call carries the run's total deadline, so the executor (P7) is the one that
classifies a call still in flight — ``timeout`` when nothing was emitted and
``external_unknown`` when the request may already be on the wire, never
``success``. Cancelling the worker outright instead would report a false
``cancelled`` on a write that was already emitted, and the scheduler never
re-admits, replays or re-invokes such a call, so the request is emitted exactly
once and never retried.

The scheduler is the **single emission owner** of the two run lifecycle events.
It emits ``brain.run.started`` when a worker picks the work up and
``brain.run.completed`` exactly once per admitted work on every exit path —
normal completion, ``stale_drop``, ``run_deadline``, cancellation and an
exception escaping the run body — so AC27's "exactly 1 of each" holds without
any cross-component coordination. The run body publishes neither: it returns a
:class:`RunOutcome` that the scheduler merges into the traced payload, and a
body that reaches for either event through its :class:`RunContext` is refused
with a :class:`RunLifecycleEmissionError` naming the module that tried.

Being the owner of the run terminal state, the scheduler implements R8's
record-before-publish ordering: the terminal state and both durations are
written into the :class:`RunRecord` **before** ``brain.run.completed`` is handed
to ``Supervision.record_and_emit`` (P10). A publication that fails therefore
leaves the recorded state intact and re-runs, retries and cancels nothing.

At most one run per session is active at a time, distinct sessions progress
concurrently up to the worker limit, and a session whose queue is empty and
which has no active run is evicted — queue and lock together, and only in that
state, so a drain can never race an eviction into a lost or doubly-executed
item (AC9).

Shutdown finishes the accounting rather than dropping it. :meth:`aclose` stops
the pool, then drains every still-queued item to terminal ``cancelled`` with a
:class:`RunRecord` and its one ``brain.run.completed``, because a closed
scheduler cannot be restarted to finish them and an admitted item that simply
vanishes is the one hole AC27 does not tolerate. Outstanding trace publications
are capped like every other retention surface (``max_pending_traces``): a
supervision that stalls loses the traces it cannot keep up with and counts
them, so a flood of rejections can never grow that set without limit.

The module opens no transport and reads time only through the injected clock;
every wait is driven by the injected sleeper, so a deadline can be provoked
without sleeping.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import math
import secrets
import time
from collections import OrderedDict, deque
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Protocol

from .contracts import (
    COUNTER_ADMISSION_REJECTIONS,
    COUNTER_LOST_TRACES,
    COUNTER_RUN_DEADLINE_EXPIRIES,
    COUNTER_STALE_DROP,
    TERMINAL_STATUSES,
    TRACE_BRAIN_ADMISSION_ACCEPTED,
    TRACE_BRAIN_ADMISSION_REJECTED,
    TRACE_BRAIN_RUN_COMPLETED,
    TRACE_BRAIN_RUN_STARTED,
    ContractError,
    Counters,
    SessionKey,
)

__all__ = [
    "DEFAULT_BOUNDARY_YIELDS",
    "DEFAULT_MAX_PENDING_TRACES",
    "DEFAULT_MAX_RECORDS",
    "DEFAULT_RUN_MODULE",
    "REASON_ADMITTED",
    "REASON_CANCELLED",
    "REASON_COMPLETED",
    "REASON_GLOBAL_PENDING_CAP",
    "REASON_RUN_DEADLINE",
    "REASON_RUN_FAILED",
    "REASON_SESSION_CAP",
    "REASON_SESSION_QUEUE_FULL",
    "REASON_STALE_DROP",
    "RUN_LIFECYCLE_TRACES",
    "AdmissionResult",
    "AdmissionScheduler",
    "RunContext",
    "RunDeadlineExceeded",
    "RunLifecycleEmissionError",
    "RunOutcome",
    "RunRecord",
    "Supervision",
    "Work",
]

Clock = Callable[[], float]
Sleeper = Callable[[float], Awaitable[None]]
IdFactory = Callable[[], str]

RUN_ID_BYTES = 8
"""Entropy of a generated ``run_id``; it correlates every trace of one run."""

DEFAULT_RUN_MODULE = "brain"
"""Module credited with the run body, named in the lifecycle-emission refusal."""

DEFAULT_MAX_RECORDS = 1024
"""How many terminal :class:`RunRecord` values stay addressable by ``run_id``.

The record store is bounded like every other retention surface (R6): oldest
first. It is generous relative to the number of runs a supervision read spans.
"""

DEFAULT_MAX_PENDING_TRACES = 256
"""How many detached trace publications may be outstanding at once (R2, R6).

``admit`` never awaits a trace, so every accepted *and* every rejected message
hands one more publication to the loop. Under a supervision that stalls, that
set is the one surface a saturation refusal does not already bound, so it is
bounded here: beyond the cap the trace is refused and counted lost, exactly as
a queue beyond its capacity refuses the work.
"""

DEFAULT_BOUNDARY_YIELDS = 64
"""Event-loop turns granted to a run body to reach its next boundary.

An expired run is asked to stop, not cut off: these turns let the executor
finish classifying a call already in flight and let the body return its
partial outcome. They consume no time on any clock — each is a bare reschedule
— so the bound is on cooperation, not on duration. A body that ignores every
one of them is abandoned, which is what the bound is for.
"""

# --------------------------------------------------------------------------- #
# Admission and run reasons
# --------------------------------------------------------------------------- #

REASON_ADMITTED = "admitted"
"""The work entered its session queue."""

REASON_SESSION_QUEUE_FULL = "session_queue_full"
"""This session already holds ``session_queue_capacity`` pending items."""

REASON_GLOBAL_PENDING_CAP = "global_pending_cap"
"""Every session together already holds ``global_pending_capacity`` items."""

REASON_SESSION_CAP = "session_cap"
"""``max_sessions`` sessions are live and none of them is idle enough to evict."""

REASON_COMPLETED = "completed"
"""The run body returned its own outcome inside both deadlines."""

REASON_STALE_DROP = "stale_drop"
"""The wait deadline was reached before any worker picked the work up."""

REASON_RUN_DEADLINE = "run_deadline"
"""The total deadline was reached after the run had started."""

REASON_CANCELLED = "cancelled"
"""The scheduler itself was stopped while this run was in flight."""

REASON_RUN_FAILED = "run_failed"
"""An exception escaped the run body."""

RUN_LIFECYCLE_TRACES = frozenset({TRACE_BRAIN_RUN_STARTED, TRACE_BRAIN_RUN_COMPLETED})
"""The two traces only the scheduler may publish (R8, AC27)."""


# --------------------------------------------------------------------------- #
# Explicit failures
# --------------------------------------------------------------------------- #


class RunLifecycleEmissionError(RuntimeError):
    """Raised when a run body reaches for a run lifecycle trace.

    The scheduler is the single emission owner of ``brain.run.started`` and
    ``brain.run.completed``; a second publisher is exactly how AC27's "exactly
    1 of each" stops holding, so the attempt is refused rather than
    de-duplicated, and the diagnostic names the module that tried.
    """

    def __init__(self, module: str, event_type: str) -> None:
        self.module = module
        self.event_type = event_type
        super().__init__(
            f"module {module!r} may not publish {event_type!r}: the admission "
            "scheduler is the single emission owner of the run lifecycle traces"
        )


class RunDeadlineExceeded(RuntimeError):
    """Raised by :meth:`RunContext.checkpoint` once the total deadline passed.

    A run body that prefers an exception to a boundary test may raise this at
    its own boundaries; the scheduler treats it as the deadline it is, not as
    a run failure.
    """

    def __init__(self, run_id: str, total_deadline: float, now: float) -> None:
        self.run_id = run_id
        self.total_deadline = total_deadline
        self.now = now
        super().__init__(
            f"run {run_id!r} passed its total deadline "
            f"({now!r} is at or past {total_deadline!r})"
        )


class SchedulerClosed(RuntimeError):
    """Raised when work is offered to a scheduler that has been closed."""


# --------------------------------------------------------------------------- #
# Supervision, declared structurally (R8)
# --------------------------------------------------------------------------- #


class Supervision(Protocol):
    """The subset of P10's facade the scheduler depends on.

    Declared structurally so this module does not import the runtime context
    and close an import cycle. Either method may be synchronous or awaitable.
    """

    def emit(self, event_type: str, payload: Mapping[str, Any]) -> Any:
        ...  # pragma: no cover - protocol declaration

    def record_and_emit(
        self,
        record: Callable[[], Any],
        event_type: str,
        payload: Mapping[str, Any],
    ) -> Any:
        ...  # pragma: no cover - protocol declaration


class _NullSupervision:
    """Stand-in used when no supervision is injected.

    It honours the ordering contract by calling *record* first, so a scheduler
    built without supervision behaves like one built with it.
    """

    def emit(self, event_type: str, payload: Mapping[str, Any]) -> None:
        return None

    def record_and_emit(
        self,
        record: Callable[[], Any],
        event_type: str,
        payload: Mapping[str, Any],
    ) -> None:
        record()
        return None


# --------------------------------------------------------------------------- #
# What ingestion hands over
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Work:
    """One unit of admitted work, independent of the event it came from.

    ``payload`` is deep-frozen at construction — nested mappings become
    read-only and nested sequences become tuples — so the bus event ingestion
    read from may be mutated, reused or recycled afterwards without any of that
    reaching the queued copy (R2, AC6).
    """

    payload: Mapping[str, Any]
    source_event_id: str
    kind: str = "chat.message"
    conversation_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload", _frozen_copy(self.payload, "Work.payload"))
        _require_text(self.source_event_id, "Work.source_event_id")
        _require_text(self.kind, "Work.kind")
        if self.conversation_id is not None:
            _require_text(self.conversation_id, "Work.conversation_id")


@dataclass(frozen=True, slots=True)
class AdmissionResult:
    """What :meth:`AdmissionScheduler.admit` answers, synchronously.

    ``replay_promised`` is always ``False``: a rejected message is dropped and
    said to be dropped. Promising a replay the scheduler has no queue slot to
    keep would be the unbounded backlog the caps exist to prevent (AC7).
    """

    accepted: bool
    reason: str
    session: str
    queue_depth: int
    pending: int
    run_id: str | None = None
    replay_promised: bool = False


@dataclass(frozen=True, slots=True)
class RunOutcome:
    """What a run body returns to the scheduler; it publishes nothing itself.

    ``status`` is a terminal state from
    :data:`~core.contracts.TERMINAL_STATUSES` and ``delivery`` is the delivery
    outcome, carried **separately** so a run that reached its end without
    getting a message out is never read as a send (R8).
    """

    status: str = "success"
    delivery: str | None = None
    model_calls: int = 0
    sends: int = 0
    correlation: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status not in TERMINAL_STATUSES:
            allowed = ", ".join(sorted(TERMINAL_STATUSES))
            raise ContractError(
                "RunOutcome.status", f"must be one of {allowed}, got {self.status!r}"
            )
        if self.delivery is not None:
            _require_text(self.delivery, "RunOutcome.delivery")
        for name in ("model_calls", "sends"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise ContractError(
                    f"RunOutcome.{name}", f"must be an integer, got {type(value).__name__}"
                )
            if value < 0:
                raise ContractError(f"RunOutcome.{name}", f"must not be negative, got {value!r}")
        object.__setattr__(
            self, "correlation", _frozen_copy(self.correlation, "RunOutcome.correlation")
        )


@dataclass(slots=True)
class RunRecord:
    """The scheduler's own memory of one run, written before its trace.

    Read back through :meth:`AdmissionScheduler.run_record`. It is the value a
    failed publication leaves behind, which is the whole point of writing it
    first (R8).
    """

    run_id: str
    session: str
    status: str
    reason: str
    waited_seconds: float
    total_seconds: float
    model_calls: int = 0
    sends: int = 0
    delivery: str | None = None
    started: bool = False
    admitted_at: float = 0.0
    completed_at: float = 0.0


# --------------------------------------------------------------------------- #
# The handle a run body is given
# --------------------------------------------------------------------------- #


class RunContext:
    """One run in flight: its identity, its budget and its boundary.

    The body reads :attr:`expired` (or calls :meth:`checkpoint`) at each of its
    own boundaries and returns instead of starting further work. It reads
    :attr:`total_deadline` when it builds an action call, so the executor
    inherits the run's budget and is the component that classifies a call still
    in flight when the budget runs out (AC33).
    """

    __slots__ = (
        "_clock",
        "_expired",
        "_module",
        "_supervision",
        "conversation_id",
        "model_calls",
        "run_id",
        "sends",
        "session_key",
        "started_at",
        "total_deadline",
        "waited_seconds",
        "work",
    )

    def __init__(
        self,
        *,
        run_id: str,
        session_key: SessionKey,
        work: Work,
        conversation_id: str,
        total_deadline: float,
        started_at: float,
        waited_seconds: float,
        clock: Clock,
        supervision: Any,
        module: str,
    ) -> None:
        self.run_id = run_id
        self.session_key = session_key
        self.work = work
        self.conversation_id = conversation_id
        self.total_deadline = total_deadline
        self.started_at = started_at
        self.waited_seconds = waited_seconds
        self.model_calls = 0
        self.sends = 0
        self._clock = clock
        self._supervision = supervision
        self._module = module
        self._expired = False

    # -- the budget --------------------------------------------------------- #

    @property
    def now(self) -> float:
        return self._clock()

    @property
    def remaining(self) -> float:
        """Seconds left before the total deadline; never negative."""

        return max(0.0, self.total_deadline - self._clock())

    @property
    def expired(self) -> bool:
        """Whether the total deadline has been reached.

        True once the scheduler has signalled expiry *or* the clock has passed
        the deadline, so a body that polls between two boundaries sees it even
        before the scheduler's timer has been scheduled back in.
        """

        return self._expired or self._clock() >= self.total_deadline

    def expire(self) -> None:
        """Signal the boundary. Called by the scheduler, never by the body."""

        self._expired = True

    def checkpoint(self) -> None:
        """Raise :class:`RunDeadlineExceeded` when the budget is spent."""

        if self.expired:
            raise RunDeadlineExceeded(self.run_id, self.total_deadline, self._clock())

    # -- what the run did --------------------------------------------------- #

    def note_model_call(self, count: int = 1) -> int:
        """Record that the body reached the model *count* more times."""

        self.model_calls += _positive_count(count, "RunContext.note_model_call")
        return self.model_calls

    def note_send(self, count: int = 1) -> int:
        """Record *count* more confirmed outbound sends."""

        self.sends += _positive_count(count, "RunContext.note_send")
        return self.sends

    # -- traces the body may publish ---------------------------------------- #

    def emit(self, event_type: str, payload: Mapping[str, Any]) -> Any:
        """Publish a trace, refusing the two the scheduler owns (R8, AC27)."""

        _require_text(event_type, "RunContext.emit.event_type")
        if event_type in RUN_LIFECYCLE_TRACES:
            raise RunLifecycleEmissionError(self._module, event_type)
        return self._supervision.emit(event_type, payload)

    def record_and_emit(
        self,
        record: Callable[[], Any],
        event_type: str,
        payload: Mapping[str, Any],
    ) -> Any:
        """Record-then-publish for a trace the body itself owns."""

        _require_text(event_type, "RunContext.record_and_emit.event_type")
        if event_type in RUN_LIFECYCLE_TRACES:
            raise RunLifecycleEmissionError(self._module, event_type)
        return self._supervision.record_and_emit(record, event_type, payload)


# --------------------------------------------------------------------------- #
# Queued items
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class _Item:
    """One admitted work waiting for, or holding, a worker."""

    run_id: str
    session_key: SessionKey
    session: str
    work: Work
    conversation_id: str
    admitted_at: float
    wait_deadline: float
    total_deadline: float
    trace: "asyncio.Future[Any] | None" = None


# --------------------------------------------------------------------------- #
# The scheduler
# --------------------------------------------------------------------------- #


class AdmissionScheduler:
    """Bounded FIFO admission, one active run per session, owned terminals."""

    __slots__ = (
        "_active",
        "_boundary_yields",
        "_change",
        "_clock",
        "_closed",
        "_counters",
        "_global_pending_capacity",
        "_id_factory",
        "_max_pending_traces",
        "_max_records",
        "_max_sessions",
        "_pending",
        "_queues",
        "_reaper",
        "_ready",
        "_records",
        "_run_body",
        "_run_module",
        "_running",
        "_session_queue_capacity",
        "_sleep",
        "_supervision",
        "_total_run_seconds",
        "_traces",
        "_wait_seconds",
        "_workers",
        "_worker_count",
    )

    def __init__(
        self,
        run_body: Callable[[RunContext], Awaitable[Any]],
        *,
        session_queue_capacity: int | None = None,
        global_pending_capacity: int | None = None,
        max_sessions: int | None = None,
        workers: int | None = None,
        total_run_seconds: float | None = None,
        wait_seconds: float | None = None,
        clock: Clock = time.monotonic,
        sleeper: Sleeper | None = None,
        supervision: Supervision | None = None,
        counters: Counters | None = None,
        id_factory: IdFactory | None = None,
        run_module: str = DEFAULT_RUN_MODULE,
        max_records: int = DEFAULT_MAX_RECORDS,
        max_pending_traces: int = DEFAULT_MAX_PENDING_TRACES,
        boundary_yields: int = DEFAULT_BOUNDARY_YIELDS,
    ) -> None:
        if not callable(run_body):
            raise ContractError("run_body", "must be callable")
        if not callable(clock):
            raise ContractError("clock", "must be callable")
        if sleeper is not None and not callable(sleeper):
            raise ContractError("sleeper", "must be callable")
        if id_factory is not None and not callable(id_factory):
            raise ContractError("id_factory", "must be callable")

        self._session_queue_capacity = _validate_count_limit(
            session_queue_capacity, "session_queue_capacity"
        )
        self._global_pending_capacity = _validate_count_limit(
            global_pending_capacity, "global_pending_capacity"
        )
        self._max_sessions = _validate_count_limit(max_sessions, "max_sessions")
        self._worker_count = _validate_count_limit(workers, "workers")
        self._total_run_seconds = _validate_duration_limit(
            total_run_seconds, "total_run_seconds"
        )
        self._wait_seconds = _validate_duration_limit(wait_seconds, "wait_seconds")
        self._max_records = _validate_count_limit(max_records, "max_records")
        self._max_pending_traces = _validate_count_limit(
            max_pending_traces, "max_pending_traces"
        )
        self._boundary_yields = _validate_count_limit(boundary_yields, "boundary_yields")
        self._run_module = _require_text(run_module, "run_module")

        self._run_body = run_body
        self._clock = clock
        self._sleep: Sleeper = sleeper if sleeper is not None else asyncio.sleep
        self._supervision: Any = (
            supervision if supervision is not None else _NullSupervision()
        )
        self._counters = counters
        self._id_factory: IdFactory = (
            id_factory if id_factory is not None else _default_run_id
        )

        self._queues: "OrderedDict[str, deque[_Item]]" = OrderedDict()
        self._active: dict[str, str] = {}
        self._ready: deque[str] = deque()
        self._pending = 0
        self._records: "OrderedDict[str, RunRecord]" = OrderedDict()
        self._traces: set["asyncio.Future[Any]"] = set()
        self._workers: list["asyncio.Task[None]"] = []
        self._reaper: "asyncio.Task[None] | None" = None
        self._change = asyncio.Event()
        self._running = False
        self._closed = False

    # -- read-only surface --------------------------------------------------- #

    @property
    def pending(self) -> int:
        """How many admitted items are queued and not yet picked up."""

        return self._pending

    @property
    def live_sessions(self) -> int:
        """How many session queues and locks are currently retained (AC9)."""

        return len(self._queues)

    @property
    def active_runs(self) -> int:
        """How many runs are executing right now, at most ``workers`` (AC9)."""

        return len(self._active)

    def queue_depth(self, session_key: SessionKey | str) -> int:
        """Depth of one session's queue, 0 for a session with no queue."""

        session = _session_name(session_key)
        queue = self._queues.get(session)
        return len(queue) if queue is not None else 0

    def run_record(self, run_id: str) -> RunRecord | None:
        """The recorded terminal state of *run_id*, if still retained."""

        _require_text(run_id, "run_id")
        return self._records.get(run_id)

    def run_records(self) -> Mapping[str, RunRecord]:
        """Every retained terminal record, oldest first."""

        return MappingProxyType(dict(self._records))

    # -- admission ----------------------------------------------------------- #

    def admit(self, session_key: SessionKey, work: Work) -> AdmissionResult:
        """Validate, copy, enqueue and return — synchronously (R2, AC6).

        Nothing here awaits: not the model, not a worker, not the trace. The
        admission trace is handed to the loop as a detached publication and the
        worker that picks the work up waits for it before ``brain.run.started``,
        so AC27's ordering holds without the handler blocking on it.
        """

        if not isinstance(session_key, SessionKey):
            raise ContractError("admit.session_key", "must be a SessionKey")
        if not isinstance(work, Work):
            raise ContractError("admit.work", "must be a Work")
        if self._closed:
            raise SchedulerClosed("the admission scheduler has been closed")

        session = session_key.serialize()
        queue = self._queues.get(session)
        depth = len(queue) if queue is not None else 0

        if queue is not None and depth >= self._session_queue_capacity:
            return self._reject(
                session_key, work, REASON_SESSION_QUEUE_FULL, depth=depth
            )
        if self._pending >= self._global_pending_capacity:
            return self._reject(
                session_key, work, REASON_GLOBAL_PENDING_CAP, depth=self._pending
            )
        if queue is None:
            self._evict_idle_sessions()
            if len(self._queues) >= self._max_sessions:
                return self._reject(
                    session_key, work, REASON_SESSION_CAP, depth=len(self._queues)
                )
            queue = deque()
            self._queues[session] = queue

        admitted_at = self._clock()
        total_deadline = admitted_at + self._total_run_seconds
        # Clamped, never merely configured: a wait deadline past the total
        # deadline would let a ``stale_drop`` outlive the run budget (R2).
        wait_deadline = min(admitted_at + self._wait_seconds, total_deadline)

        item = _Item(
            run_id=self._new_run_id(),
            session_key=session_key,
            session=session,
            work=work,
            conversation_id=work.conversation_id or session,
            admitted_at=admitted_at,
            wait_deadline=wait_deadline,
            total_deadline=total_deadline,
        )
        queue.append(item)
        self._queues.move_to_end(session)
        self._pending += 1
        if session not in self._active and session not in self._ready:
            self._ready.append(session)
        self._change.set()

        item.trace = self._spawn_trace(
            TRACE_BRAIN_ADMISSION_ACCEPTED,
            self._admission_payload(
                item.session_key,
                item.work,
                REASON_ADMITTED,
                depth=len(queue),
                run_id=item.run_id,
            ),
        )
        return AdmissionResult(
            accepted=True,
            reason=REASON_ADMITTED,
            session=session,
            queue_depth=len(queue),
            pending=self._pending,
            run_id=item.run_id,
        )

    def _reject(
        self, session_key: SessionKey, work: Work, reason: str, *, depth: int
    ) -> AdmissionResult:
        """Refuse the work, count it, trace it, and promise nothing (AC7)."""

        self._count(COUNTER_ADMISSION_REJECTIONS)
        self._spawn_trace(
            TRACE_BRAIN_ADMISSION_REJECTED,
            self._admission_payload(session_key, work, reason, depth=depth, run_id=None),
        )
        return AdmissionResult(
            accepted=False,
            reason=reason,
            session=session_key.serialize(),
            queue_depth=depth,
            pending=self._pending,
            run_id=None,
        )

    def _admission_payload(
        self,
        session_key: SessionKey,
        work: Work,
        reason: str,
        *,
        depth: int,
        run_id: str | None,
    ) -> dict[str, Any]:
        """The correlation AC27 reads: the source event and the channel pair."""

        payload: dict[str, Any] = {
            "session": session_key.serialize(),
            "platform": session_key.platform,
            "channel_id": session_key.channel_id,
            "viewer_id": session_key.viewer_id,
            "source_event_id": work.source_event_id,
            "kind": work.kind,
            "reason": reason,
            "queue_depth": depth,
            "pending": self._pending,
            "replay_promised": False,
        }
        if run_id is not None:
            payload["run_id"] = run_id
        return payload

    # -- lifecycle ----------------------------------------------------------- #

    async def start(self) -> None:
        """Start the worker pool and the wait-deadline reaper. Idempotent."""

        if self._closed:
            raise SchedulerClosed("the admission scheduler has been closed")
        if self._running:
            return
        self._running = True
        self._workers = [
            asyncio.ensure_future(self._worker(index))
            for index in range(self._worker_count)
        ]
        self._reaper = asyncio.ensure_future(self._reap())

    async def aclose(self) -> None:
        """Stop the pool, then finish the accounting of everything admitted.

        Runs still in flight end ``cancelled``, traced once. So does every item
        still waiting in a queue: a closed scheduler cannot be restarted to
        execute them, so leaving them behind would drop admitted work with
        neither a :class:`RunRecord` nor a ``brain.run.completed``, which is the
        one hole AC27's "exactly 1 of each per admitted work" does not tolerate.
        """

        self._closed = True
        if self._running:
            self._running = False
            self._change.set()
            tasks = [
                task for task in (*self._workers, self._reaper) if task is not None
            ]
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.wait(tasks)
                for task in tasks:
                    if not task.cancelled():
                        task.exception()
            self._workers = []
            self._reaper = None
        await self._drain_pending()
        await self._drain_traces()

    async def _drain_pending(self) -> None:
        """End every still-queued item ``cancelled``, recorded and traced once.

        The workers are already stopped and ``admit`` already refuses, so
        nothing can enter a queue while this drains and no item can be taken
        from under it.
        """

        while self._queues:
            _session, queue = self._queues.popitem(last=False)
            while queue:
                item = queue.popleft()
                self._pending -= 1
                await self._complete_cancelled(item)
        self._ready.clear()
        self._pending = 0

    # -- the worker pool ------------------------------------------------------ #

    async def _worker(self, _index: int) -> None:
        while self._running:
            # Cleared *before* looking, never after: an admission that lands
            # between the look and the wait must leave the flag set, or the
            # wakeup it raised is lost and its work waits for the next one.
            self._change.clear()
            item = self._take()
            if item is None:
                await self._change.wait()
                continue
            try:
                await self._run(item)
            except asyncio.CancelledError:
                raise
            except Exception as failure:
                # The pool outlives its runs. Anything unexpected escaping
                # ``_run`` ends *this item*, never the worker carrying it: a
                # worker that exited here would leave a ``workers=1`` scheduler
                # marked running while nothing admitted ever executes again.
                # The work still gets a terminal state, unless it already has a
                # truer one, in which case ``_record`` refuses this one.
                with contextlib.suppress(Exception):
                    await self._complete_unhandled(item, failure)
            finally:
                self._release(item.session)

    def _take(self) -> _Item | None:
        """Pop the oldest item of the oldest ready session, and lock it.

        Only a session with no active run is ever in ``_ready``, so popping and
        locking here is what gives AC9's "at most one active run per session".
        """

        while self._ready:
            session = self._ready.popleft()
            queue = self._queues.get(session)
            if not queue:
                self._maybe_evict(session)
                continue
            item = queue.popleft()
            self._pending -= 1
            self._active[session] = item.run_id
            return item
        return None

    def _release(self, session: str) -> None:
        """Unlock *session*, re-queue it when it still holds work, else evict."""

        self._active.pop(session, None)
        queue = self._queues.get(session)
        if queue:
            if session not in self._ready:
                self._ready.append(session)
            self._change.set()
            return
        self._maybe_evict(session)

    def _maybe_evict(self, session: str) -> None:
        """Drop a session's queue and lock, and only when it is truly idle.

        Empty queue and no active run: evicting anything else would race a
        drain into a lost or doubly-executed item (AC9).
        """

        if self._queues.get(session):
            return
        if session in self._active:
            return
        self._queues.pop(session, None)

    def _evict_idle_sessions(self) -> None:
        for session in list(self._queues):
            self._maybe_evict(session)

    # -- one run -------------------------------------------------------------- #

    async def _run(self, item: _Item) -> None:
        """Take one admitted item to exactly one ``brain.run.completed``."""

        now = self._clock()
        if now >= item.wait_deadline:
            await self._complete_stale(item, now)
            return

        # Both of these await supervision, and the run's budget bounds them
        # exactly as it bounds the body: a hook that blocks here would hold the
        # worker past the total deadline where the reaper — which only sees
        # queued items — can no longer reach this one.
        await self._bounded_by(self._settled(item.trace), item.total_deadline)
        started_at = self._clock()
        waited = max(0.0, started_at - item.admitted_at)
        context = RunContext(
            run_id=item.run_id,
            session_key=item.session_key,
            work=item.work,
            conversation_id=item.conversation_id,
            total_deadline=item.total_deadline,
            started_at=started_at,
            waited_seconds=waited,
            clock=self._clock,
            supervision=self._supervision,
            module=self._run_module,
        )
        published = await self._bounded_by(
            self._emit(
                TRACE_BRAIN_RUN_STARTED,
                {
                    **self._correlation(item),
                    "waited_seconds": waited,
                    "total_deadline_seconds": self._total_run_seconds,
                },
            ),
            item.total_deadline,
        )
        if not published:
            # Abandoned at the deadline: a lost trace, like any other trace
            # that could not be published, never a held worker (R8).
            self._count(COUNTER_LOST_TRACES)

        if self._clock() >= item.total_deadline:
            # The budget was spent before the body could be entered. Ending it
            # here *is* the boundary: starting work that is already out of time
            # is exactly what AC33 says not to do.
            context.expire()
            self._count(COUNTER_RUN_DEADLINE_EXPIRIES)
            await self._complete(item, context, "timeout", REASON_RUN_DEADLINE, None)
            return

        body = asyncio.ensure_future(_resolved(self._run_body(context)))
        remaining = item.total_deadline - self._clock()
        timer = asyncio.ensure_future(self._sleep(max(0.0, remaining)))
        try:
            await asyncio.wait({body, timer}, return_when=asyncio.FIRST_COMPLETED)
        except asyncio.CancelledError:
            # Shutdown, not expiry. The boundary is still offered, and the
            # terminal state is still written — ``_publish_completion`` records
            # before it awaits, so even a second cancellation cannot lose it.
            _detach(timer)
            with contextlib.suppress(asyncio.CancelledError):
                await self._abandon(context, body)
            _detach(body)
            with contextlib.suppress(asyncio.CancelledError):
                await self._complete(item, context, "cancelled", REASON_CANCELLED, None)
            raise
        finally:
            _detach(timer)

        if not body.done() or context.expired:
            # The budget is spent: stop at the next boundary instead of
            # starting further work, and let whatever is in flight be
            # classified by its own owner rather than cut off here (AC33).
            outcome = await self._abandon(context, body)
            self._count(COUNTER_RUN_DEADLINE_EXPIRIES)
            await self._complete(item, context, "timeout", REASON_RUN_DEADLINE, outcome)
            return

        if body.cancelled():
            await self._complete(item, context, "cancelled", REASON_CANCELLED, None)
            return
        error = body.exception()
        if error is not None:
            if isinstance(error, RunDeadlineExceeded):
                self._count(COUNTER_RUN_DEADLINE_EXPIRIES)
                await self._complete(
                    item, context, "timeout", REASON_RUN_DEADLINE, None
                )
                return
            await self._complete(
                item,
                context,
                "error",
                REASON_RUN_FAILED,
                None,
                error=f"{type(error).__name__}: {error}",
            )
            return

        try:
            outcome = _coerce_outcome(body.result())
        except ContractError as invalid:
            # An unsupported return value is the body's failure, not the
            # worker's: it ends this run ``error`` here rather than escaping
            # into the pool, where it would take the worker down with it.
            await self._complete(
                item,
                context,
                "error",
                REASON_RUN_FAILED,
                None,
                error=f"{type(invalid).__name__}: {invalid}",
            )
            return
        await self._complete(item, context, outcome.status, REASON_COMPLETED, outcome)

    async def _abandon(
        self, context: RunContext, body: "asyncio.Future[Any]"
    ) -> RunOutcome | None:
        """Signal the boundary, wait for it, and only then let the body go.

        The wait is a bounded number of bare event-loop turns: it costs no time
        on any clock and it is what lets the executor finish classifying a call
        already in flight — the difference between an honest
        ``external_unknown`` and a false ``cancelled`` on a request that is
        already on the wire (AC33). A body that never reaches a boundary is
        cancelled, which is the bound's purpose.
        """

        context.expire()
        for _ in range(self._boundary_yields):
            if body.done():
                break
            await asyncio.sleep(0)
        if not body.done():
            body.cancel()
            # The cancellation gets the same bounded courtesy the boundary got,
            # and no more: a body that catches ``CancelledError`` and keeps
            # awaiting would otherwise hold this worker and its session for
            # good, keep the timeout record from ever being written and hang
            # ``aclose`` behind it. Past the bound it is let go, still
            # cancelled, and the run is terminated without it.
            for _ in range(self._boundary_yields):
                if body.done():
                    break
                await asyncio.sleep(0)
            if not body.done():
                _detach(body)
                return None
        if body.cancelled():
            return None
        error = body.exception()
        if error is not None:
            return None
        try:
            return _coerce_outcome(body.result())
        except ContractError:
            # An expired run that also returned nonsense is still an expired
            # run: it carries no outcome rather than failing the worker.
            return None

    async def _complete_stale(self, item: _Item, now: float) -> None:
        """End an item whose wait deadline passed before any worker took it."""

        await self._settled(item.trace)
        self._count(COUNTER_STALE_DROP)
        waited = max(0.0, now - item.admitted_at)
        record = RunRecord(
            run_id=item.run_id,
            session=item.session,
            status="timeout",
            reason=REASON_STALE_DROP,
            waited_seconds=waited,
            total_seconds=waited,
            model_calls=0,
            sends=0,
            delivery=None,
            started=False,
            admitted_at=item.admitted_at,
            completed_at=now,
        )
        await self._publish_completion(item, record, {})

    async def _complete_cancelled(self, item: _Item) -> None:
        """End a still-queued item at shutdown: it never started, and says so."""

        await self._settled(item.trace)
        now = self._clock()
        waited = max(0.0, now - item.admitted_at)
        record = RunRecord(
            run_id=item.run_id,
            session=item.session,
            status="cancelled",
            reason=REASON_CANCELLED,
            waited_seconds=waited,
            total_seconds=waited,
            model_calls=0,
            sends=0,
            delivery=None,
            started=False,
            admitted_at=item.admitted_at,
            completed_at=now,
        )
        await self._publish_completion(item, record, {})

    async def _complete_unhandled(self, item: _Item, failure: BaseException) -> None:
        """Last-resort terminal state for a run whose own handling failed.

        Reached only from the worker's guard: it keeps the work accounted for
        when a defect escaped :meth:`_run`. A run that already recorded its
        terminal state makes :meth:`_record` refuse this one, so the earlier,
        truer state stands and no second trace is published.
        """

        now = self._clock()
        waited = max(0.0, now - item.admitted_at)
        record = RunRecord(
            run_id=item.run_id,
            session=item.session,
            status="error",
            reason=REASON_RUN_FAILED,
            waited_seconds=waited,
            total_seconds=waited,
            model_calls=0,
            sends=0,
            delivery=None,
            started=True,
            admitted_at=item.admitted_at,
            completed_at=now,
        )
        await self._publish_completion(
            item, record, {"error": f"{type(failure).__name__}: {failure}"}
        )

    async def _complete(
        self,
        item: _Item,
        context: RunContext,
        status: str,
        reason: str,
        outcome: RunOutcome | None,
        *,
        error: str | None = None,
    ) -> None:
        """Write the terminal state, then publish it — in that order (R8)."""

        completed_at = self._clock()
        record = RunRecord(
            run_id=item.run_id,
            session=item.session,
            status=status,
            reason=reason,
            waited_seconds=context.waited_seconds,
            total_seconds=max(0.0, completed_at - item.admitted_at),
            model_calls=max(context.model_calls, outcome.model_calls if outcome else 0),
            sends=max(context.sends, outcome.sends if outcome else 0),
            delivery=outcome.delivery if outcome is not None else None,
            started=True,
            admitted_at=item.admitted_at,
            completed_at=completed_at,
        )
        extra: dict[str, Any] = {}
        if outcome is not None:
            extra.update(
                {
                    key: value
                    for key, value in outcome.correlation.items()
                    if key not in _RESERVED_TRACE_FIELDS
                }
            )
        if error is not None:
            extra["error"] = error
        await self._publish_completion(item, record, extra)

    async def _publish_completion(
        self, item: _Item, record: RunRecord, extra: Mapping[str, Any]
    ) -> None:
        """The single ``brain.run.completed`` of this work, recorded first."""

        self._record(record)
        payload = {
            **self._correlation(item),
            "status": record.status,
            "reason": record.reason,
            "waited_seconds": record.waited_seconds,
            "total_seconds": record.total_seconds,
            "delivery": record.delivery,
            "model_calls": record.model_calls,
            "sends": record.sends,
            "started": record.started,
            **extra,
        }
        await self._record_and_emit(
            lambda: self._record(record), TRACE_BRAIN_RUN_COMPLETED, payload
        )

    def _correlation(self, item: _Item) -> dict[str, Any]:
        return {
            "run_id": item.run_id,
            "session": item.session,
            "platform": item.session_key.platform,
            "channel_id": item.session_key.channel_id,
            "viewer_id": item.session_key.viewer_id,
            "conversation_id": item.conversation_id,
            "source_event_id": item.work.source_event_id,
            "kind": item.work.kind,
        }

    def _record(self, record: RunRecord) -> None:
        """Write the terminal state. Idempotent, and never a downgrade.

        ``record_and_emit`` calls this again as its ordering primitive, so the
        second write must be a no-op; a *different* terminal state for a
        recorded ``run_id`` is a programming error and is refused, because
        overwriting one silently is exactly the downgrade R8 forbids.
        """

        stored = self._records.get(record.run_id)
        if stored is not None:
            if stored != record:
                raise ContractError(
                    "run_record",
                    f"run {record.run_id!r} already terminated as {stored.status!r}/"
                    f"{stored.reason!r}; refusing to overwrite it with "
                    f"{record.status!r}/{record.reason!r}",
                )
            return
        self._records[record.run_id] = record
        while len(self._records) > self._max_records:
            self._records.popitem(last=False)

    # -- the wait-deadline reaper --------------------------------------------- #

    async def _reap(self) -> None:
        """Drop queued items whose wait deadline passed, even with 0 free workers.

        Checking only at dequeue would leave an item behind a held worker
        unexamined for as long as the worker is held, so the drop that AC8
        requires would never happen while it matters most.
        """

        while self._running:
            self._change.clear()
            deadline = self._earliest_wait_deadline()
            if deadline is None:
                await self._change.wait()
                continue
            delay = deadline - self._clock()
            if delay > 0:
                await self._sleep_or_change(delay)
            stale = self._expired_items()
            for item in stale:
                await self._complete_stale(item, self._clock())
            if not stale:
                # Woken by an admission, or by a deadline a worker got to
                # first: yield rather than spin, so a reaper that finds
                # nothing to do can never starve the loop it shares.
                await asyncio.sleep(0)

    def _earliest_wait_deadline(self) -> float | None:
        earliest: float | None = None
        for queue in self._queues.values():
            for item in queue:
                if earliest is None or item.wait_deadline < earliest:
                    earliest = item.wait_deadline
        return earliest

    def _expired_items(self) -> list[_Item]:
        """Remove and return every queued item past its wait deadline.

        Removal is synchronous and complete before the first trace is awaited,
        so a worker can never pick up an item this sweep has already claimed.
        """

        now = self._clock()
        stale: list[_Item] = []
        for session, queue in list(self._queues.items()):
            keep: deque[_Item] = deque()
            for item in queue:
                if now >= item.wait_deadline:
                    stale.append(item)
                    self._pending -= 1
                else:
                    keep.append(item)
            if len(keep) != len(queue):
                self._queues[session] = keep
                if not keep:
                    self._maybe_evict(session)
        return stale

    async def _sleep_or_change(self, delay: float) -> None:
        """Sleep *delay* on the injected sleeper, or wake early on a change."""

        timer = asyncio.ensure_future(self._sleep(delay))
        waiter = asyncio.ensure_future(self._change.wait())
        try:
            await asyncio.wait({timer, waiter}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            _detach(timer)
            _detach(waiter)

    # -- traces ---------------------------------------------------------------- #

    def _count(self, name: str) -> None:
        if self._counters is not None:
            self._counters.increment(name)

    def _spawn_trace(
        self, event_type: str, payload: Mapping[str, Any]
    ) -> "asyncio.Future[Any] | None":
        """Hand a trace to the loop without awaiting it, so ``admit`` stays sync.

        Detached, but not unbounded: a rejection traces too, so a flood that
        every queue cap correctly refuses would still hand one publication per
        message to a supervision that may be publishing them slower than they
        arrive. Past ``max_pending_traces`` outstanding publications the trace
        is refused and counted lost — the same answer saturation gets
        everywhere else in this module, rather than memory growth behind caps
        that look like they hold (R2, R6).
        """

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return None
        if len(self._traces) >= self._max_pending_traces:
            self._count(COUNTER_LOST_TRACES)
            return None
        task = asyncio.ensure_future(self._emit(event_type, payload))
        self._traces.add(task)
        task.add_done_callback(self._traces.discard)
        return task

    async def _drain_traces(self) -> None:
        while self._traces:
            pending = set(self._traces)
            await asyncio.wait(pending)
            self._traces -= pending

    async def _bounded_by(self, awaitable: Any, deadline: float) -> bool:
        """Await *awaitable* until *deadline*; ``False`` when it did not finish.

        The wait runs on the injected sleeper like every other wait here, and
        what it bounds is supervision: a hook that never returns is abandoned
        at the deadline instead of holding the worker that is waiting on it.
        """

        remaining = deadline - self._clock()
        if remaining <= 0:
            _close(awaitable)
            return False
        task = asyncio.ensure_future(_resolved(awaitable))
        timer = asyncio.ensure_future(self._sleep(remaining))
        try:
            await asyncio.wait({task, timer}, return_when=asyncio.FIRST_COMPLETED)
        except asyncio.CancelledError:
            _detach(task)
            raise
        finally:
            _detach(timer)
        if not task.done():
            _detach(task)
            return False
        if not task.cancelled():
            task.exception()
        return True

    async def _settled(self, trace: "asyncio.Future[Any] | None") -> None:
        """Wait for this item's admission trace, so AC27's order holds."""

        if trace is None or trace.done():
            return
        await asyncio.wait({trace})

    async def _emit(self, event_type: str, payload: Mapping[str, Any]) -> None:
        try:
            await _resolved(self._supervision.emit(event_type, payload))
        except asyncio.CancelledError:
            raise
        except Exception:
            # A trace that cannot be published is a lost trace, never a reason
            # to abandon work that is otherwise proceeding (R8).
            self._count(COUNTER_LOST_TRACES)

    async def _record_and_emit(
        self,
        record: Callable[[], Any],
        event_type: str,
        payload: Mapping[str, Any],
    ) -> None:
        try:
            await _resolved(
                self._supervision.record_and_emit(record, event_type, payload)
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            # P10 swallows and counts the publication failure itself; this
            # catch covers a supervision that does not, so a failed trace can
            # never propagate back into the owner of the terminal state.
            self._count(COUNTER_LOST_TRACES)

    def _new_run_id(self) -> str:
        return _require_text(self._id_factory(), "id_factory")


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #

_RESERVED_TRACE_FIELDS = frozenset(
    {
        "run_id",
        "session",
        "platform",
        "channel_id",
        "viewer_id",
        "conversation_id",
        "source_event_id",
        "kind",
        "status",
        "reason",
        "waited_seconds",
        "total_seconds",
        "delivery",
        "model_calls",
        "sends",
        "started",
    }
)
"""Keys a run body's correlation mapping may not overwrite in the trace."""


def _default_run_id() -> str:
    return f"run-{secrets.token_hex(RUN_ID_BYTES)}"


async def _resolved(value: Any) -> Any:
    """Await *value* when it is awaitable, so a hook may be either."""

    if inspect.isawaitable(value):
        return await value
    return value


def _coerce_outcome(value: Any) -> RunOutcome:
    """Adopt what the run body returned, defaulting a bare ``None`` to success."""

    if isinstance(value, RunOutcome):
        return value
    if value is None:
        return RunOutcome()
    raise ContractError(
        "run_body", f"must return a RunOutcome or None, got {type(value).__name__}"
    )


def _close(awaitable: Any) -> None:
    """Close an awaitable nothing will ever await, so nothing warns about it."""

    if inspect.iscoroutine(awaitable):
        awaitable.close()


def _detach(task: "asyncio.Future[Any]") -> None:
    """Stop waiting on *task* without leaking whatever it ends up raising."""

    if task.done():
        if not task.cancelled():
            task.exception()
        return
    task.cancel()
    task.add_done_callback(lambda done: None if done.cancelled() else done.exception())


def _session_name(session_key: SessionKey | str) -> str:
    if isinstance(session_key, SessionKey):
        return session_key.serialize()
    return _require_text(session_key, "session_key")


def _frozen_copy(value: Any, field_name: str) -> Any:
    """Return a deep, immutable copy: no later mutation can reach it (R2)."""

    if isinstance(value, Mapping):
        return MappingProxyType(
            {
                _require_text(key, f"{field_name} key"): _frozen_copy(item, field_name)
                for key, item in value.items()
            }
        )
    if isinstance(value, (str, bytes, bytearray)):
        return bytes(value) if isinstance(value, bytearray) else value
    if isinstance(value, Sequence):
        return tuple(_frozen_copy(item, field_name) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_frozen_copy(item, field_name) for item in value)
    return value


def _require_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise ContractError(field_name, f"must be a string, got {type(value).__name__}")
    if not value.strip():
        raise ContractError(field_name, "must not be empty")
    return value


def _positive_count(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ContractError(field_name, f"must be an integer, got {type(value).__name__}")
    if value < 0:
        raise ContractError(field_name, f"must not be negative, got {value!r}")
    return value


def _validate_count_limit(value: Any, setting: str) -> int:
    if value is None:
        raise ContractError(setting, "must be configured with a finite positive value")
    if isinstance(value, bool) or not isinstance(value, int):
        raise ContractError(setting, f"must be an integer, got {type(value).__name__}")
    if value < 1:
        raise ContractError(setting, f"must be a positive integer, got {value!r}")
    return value


def _validate_duration_limit(value: Any, setting: str) -> float:
    if value is None:
        raise ContractError(setting, "must be configured with a finite positive value")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractError(setting, f"must be a number, got {type(value).__name__}")
    limit = float(value)
    if not math.isfinite(limit):
        raise ContractError(setting, f"must be finite, got {value!r}")
    if limit <= 0:
        raise ContractError(setting, f"must be strictly positive, got {value!r}")
    return limit
