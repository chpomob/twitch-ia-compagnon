"""The versioned runtime context and the supervision surface (R7, R8).

Two responsibilities live here, and they belong together because the second is
carried by the first.

**One injection channel.** :class:`RuntimeContext` is the *only* way a module
reaches the runtime. It is a frozen, keyword-only container holding the bus,
the action registry and executor, the trigger engine, the chat context, the
attachment store, an injectable monotonic clock, an injectable random source,
the supervised task registry, the admission scheduler and the supervision
facade. Nothing is ever attached to the bus object to smuggle a collaborator
across: the bus keeps exactly the attributes its class defines, so comparing
its attribute set before and after activation shows 0 additions (AC26).

It carries :data:`RUNTIME_API` as a field so a manifest declaring
``runtime_api`` is compared against the very value the running context hands
out, and widening the container later is a visible version bump instead of a
silent compatibility break for every v2 module.

Because the container is shared, it is frozen and it hands out **scoped
facades** where a module legitimately needs write access:
:class:`ModuleActions` binds the module name into every registration and
:class:`ModuleTasks` binds it into every spawn, so a module can register its own
actions and own its own tasks without being able to reconfigure another
module's scheduler, withdraw another module's readiness or spawn work under
another module's name. :meth:`RuntimeContext.for_module` returns the
:class:`ModuleContext` an activation receives, and that view holds the shared
container privately: a module that could reach ``context.runtime.actions``
would hold the unscoped registry, and the scoping would be a convention rather
than a boundary.

**One ordering primitive.** :class:`Supervision` wraps the :class:`Counters`
registry and publishes sanitised traces. R8 orders the two writes that make a
terminal state observable: *the owner records the state, then the trace is
published*. :meth:`Supervision.record_and_emit` is that order expressed as a
single call — it invokes the owner-supplied ``record`` callable first and
publishes only once that call returned, so a trace never describes a state that
was never recorded.

The two failure directions are deliberately asymmetric:

``record`` raises
    The publication is abandoned and the failure propagates. There is no trace
    for a state that was never recorded, and the owner is the one that must
    know its own write failed.

the publication raises
    The failure is caught, :data:`COUNTER_LOST_TRACES` is incremented, a
    sanitised diagnostic is retained and **nothing propagates back into the
    owner**. A trace that cannot be published must never cancel, retry or
    repeat the external effect that produced the state (R8, AC28). This is a
    deliberate swallow and never a bare ``except: pass``: the counter, readable
    through :meth:`Supervision.snapshot`, is what makes the loss observable.

:meth:`Supervision.emit` stays the path for the non-terminal facts —
``module.ready``, ``module.degraded``, ``input.trigger.*``,
``brain.admission.*``, ``action.started`` and ``brain.run.started``. The four
terminal traces of :data:`TERMINAL_TRACE_TYPES` — ``action.completed``,
``brain.run.completed``, ``channel.chat.sent`` and ``module.stopped`` — are
refused by ``emit`` with a diagnostic naming the event, so the ordering cannot
be bypassed by accident.

:class:`ModuleHealth` is the owner of the module-level states and the place
``module.ready``, ``module.degraded`` and ``module.stopped`` are emitted from,
driven by the coordinator's phase transitions through
:meth:`ModuleHealth.observe_phase`. It records the state in its own map before
handing ``module.stopped`` to ``record_and_emit``, so the terminal module trace
obeys the same order as every other terminal trace.

This module performs no I/O of its own beyond publishing on the injected bus,
and it imports no transport.
"""

from __future__ import annotations

import asyncio
import inspect
import random
import time
from collections import deque
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from .contracts import (
    COUNTER_LOST_TRACES,
    TERMINAL_TRACE_TYPES,
    TRACE_MAX_BYTES,
    TRACE_MODULE_DEGRADED,
    TRACE_MODULE_READY,
    TRACE_MODULE_STOPPED,
    Counters,
    sanitize_trace,
)
from .lifecycle import (
    PHASE_CLOSE,
    PHASE_CLOSE_OBSERVATION,
    PHASE_PREPARE,
    PHASE_START_INPUTS,
    PHASES,
)

__all__ = [
    "MODULE_STATE_DEGRADED",
    "MODULE_STATE_READY",
    "MODULE_STATE_STOPPED",
    "MODULE_STATES",
    "RUNTIME_API",
    "SUPPORTED_RUNTIME_APIS",
    "ModuleActions",
    "ModuleContext",
    "ModuleHealth",
    "ModuleSupervision",
    "ModuleTasks",
    "RuntimeContext",
    "RuntimeContextError",
    "Supervision",
    "SupervisionError",
]

Clock = Callable[[], float]
RandomSource = Any
Recorder = Callable[[], Any]


# --------------------------------------------------------------------------- #
# Versioning
# --------------------------------------------------------------------------- #

RUNTIME_API = 2
"""The runtime contract version a v2 manifest declares and receives (R7).

A module declares ``runtime_api`` in its manifest and the loader compares that
declaration against this constant — the same value :attr:`RuntimeContext.runtime_api`
carries — so a module built against an older container is refused at load time
rather than discovering a missing collaborator at activation. Adding a field to
:class:`RuntimeContext` is a compatibility break for every v2 module and must
bump this number.
"""

SUPPORTED_RUNTIME_APIS = frozenset({RUNTIME_API})
"""Every runtime contract version this runtime can still activate."""


# --------------------------------------------------------------------------- #
# Diagnostics
# --------------------------------------------------------------------------- #


class RuntimeContextError(RuntimeError):
    """Raised when a runtime context is built from an unusable collaborator."""


class SupervisionError(RuntimeError):
    """Raised when supervision is used in a way that would break R8's order."""


# --------------------------------------------------------------------------- #
# Module health vocabulary
# --------------------------------------------------------------------------- #

MODULE_STATE_READY = "ready"
MODULE_STATE_DEGRADED = "degraded"
MODULE_STATE_STOPPED = "stopped"

MODULE_STATES = (MODULE_STATE_READY, MODULE_STATE_DEGRADED, MODULE_STATE_STOPPED)
"""The three supervised states a module can be reported in (R8)."""


# --------------------------------------------------------------------------- #
# Supervision (R8, AC28)
# --------------------------------------------------------------------------- #

DEFAULT_MAX_RETAINED_LOSSES = 32
"""How many sanitised lost-trace diagnostics are kept for inspection."""


class Supervision:
    """Sanitised trace publication, counters, and R8's ordering primitive.

    Structurally this is the facade ``core/actions.py`` and ``core/admission.py``
    declare as their ``Supervision`` protocol: ``emit`` and ``record_and_emit``,
    either of which may be awaited. Both are coroutine functions here, so the
    bus publication is awaited rather than detached — a trace that is only
    scheduled is a trace whose order against the owner's record is undefined.
    """

    __slots__ = ("_bus", "_counters", "_losses", "_max_bytes", "_secrets")

    def __init__(
        self,
        bus: Any,
        *,
        counters: Counters | None = None,
        secrets: Sequence[str] = (),
        max_bytes: int = TRACE_MAX_BYTES,
        max_retained_losses: int = DEFAULT_MAX_RETAINED_LOSSES,
    ) -> None:
        """Build supervision over *bus*.

        ``secrets`` are the configured credential values redacted from every
        trace and from every diagnostic this facade retains, so a publication
        failure carrying a payload fragment cannot leak one through the loss
        report. ``counters`` is shared with the layers that count their own
        rejections, so one snapshot reports the whole pipeline.
        """

        if not callable(getattr(bus, "publish", None)):
            raise RuntimeContextError(
                "supervision: field 'bus': must expose an awaitable publish()"
            )
        if counters is not None and not isinstance(counters, Counters):
            raise RuntimeContextError(
                "supervision: field 'counters': must be a Counters, got "
                f"{type(counters).__name__}"
            )
        if isinstance(secrets, str) or not isinstance(secrets, Sequence):
            raise RuntimeContextError(
                "supervision: field 'secrets': must be a sequence of strings"
            )
        if not isinstance(max_retained_losses, int) or isinstance(
            max_retained_losses, bool
        ):
            raise RuntimeContextError(
                "supervision: field 'max_retained_losses': must be an integer"
            )
        if max_retained_losses < 1:
            raise RuntimeContextError(
                "supervision: field 'max_retained_losses': must be positive"
            )

        self._bus = bus
        self._counters = Counters() if counters is None else counters
        self._secrets = tuple(str(secret) for secret in secrets)
        self._max_bytes = max_bytes
        self._losses: deque[str] = deque(maxlen=max_retained_losses)

    # -- read-only surface --------------------------------------------------- #

    @property
    def counters(self) -> Counters:
        """The shared counter registry every layer increments."""

        return self._counters

    @property
    def losses(self) -> tuple[str, ...]:
        """Sanitised diagnostics for the retained lost traces, oldest first.

        The count in :meth:`snapshot` is the guarantee; this is the detail that
        makes a loss diagnosable instead of merely countable.
        """

        return tuple(self._losses)

    def snapshot(self) -> Mapping[str, int]:
        """The seven counters R8 requires, plus the lost-trace diagnostic."""

        return self._counters.snapshot()

    def count(self, name: str, amount: int = 1) -> int:
        """Increment a declared counter and return its new value."""

        return self._counters.increment(name, amount)

    # -- publication --------------------------------------------------------- #

    def emit(
        self,
        event_type: str,
        payload: Mapping[str, Any],
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> Awaitable[None]:
        """Sanitise and publish a **non-terminal** trace.

        The four terminal traces are refused here by name: their state must be
        recorded by its owner before the trace exists, and
        :meth:`record_and_emit` is the only call that guarantees that order.
        The refusal is raised by the call itself rather than by the awaited
        coroutine, so a caller that publishes a terminal trace on the wrong
        method is told at the call site — an accidental bypass must not depend
        on whether the result was awaited.

        A publication failure propagates: the caller decides what a lost
        non-terminal trace means for the work in progress. The terminal path is
        the one that must never propagate.
        """

        _require_text(event_type, "supervision.emit.event_type")
        if event_type in TERMINAL_TRACE_TYPES:
            raise SupervisionError(
                f"supervision.emit refuses the terminal trace {event_type!r}: R8 "
                "orders the owner's record before its publication, so "
                f"record_and_emit(record, {event_type!r}, payload) is the only "
                "way to publish it"
            )
        return self._publish(event_type, payload, metadata)

    def record_and_emit(
        self,
        record: Recorder,
        event_type: str,
        payload: Mapping[str, Any],
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> Awaitable[None]:
        """Record a terminal state, then publish its trace — in that order (R8).

        *record* is the owner's own write of its terminal state. It returns —
        or, when it is awaitable, completes — only once that state is durable
        in the owner's structures, and only then is the trace sanitised and
        published.

        A failure raised by *record* aborts the publication and propagates:
        there is no trace for a state that was never recorded, and the owner
        must see that its own write failed.

        A failure raised by the publication is caught, counted as a lost trace
        and reported through :meth:`snapshot`. It does not propagate, so a
        trace that cannot be published never cancels, retries or repeats the
        effect that produced the state (AC28).

        The arguments are checked here and the ordered pair of writes is the
        returned coroutine, so a malformed call fails at the call site while
        *record* still runs no earlier than the publication it precedes.
        """

        if not callable(record):
            raise SupervisionError(
                "supervision.record_and_emit: field 'record': must be callable"
            )
        _require_text(event_type, "supervision.record_and_emit.event_type")
        return self._ordered(record, event_type, payload, metadata)

    async def _ordered(
        self,
        record: Recorder,
        event_type: str,
        payload: Mapping[str, Any],
        metadata: Mapping[str, Any] | None,
    ) -> None:
        """The two writes R8 orders, in order."""

        # First write: the owner's. Deliberately outside the try below — a
        # failure here must reach the owner, not be filed as a lost trace.
        outcome = record()
        if inspect.isawaitable(outcome):
            await outcome

        # Second write: the trace. From here on nothing may reach the owner.
        try:
            await self._publish(event_type, payload, metadata)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - deliberate, and never silent
            self._counters.increment(COUNTER_LOST_TRACES)
            self._losses.append(self._loss_diagnostic(event_type, exc))

    # -- internals ----------------------------------------------------------- #

    async def _publish(
        self,
        event_type: str,
        payload: Mapping[str, Any],
        metadata: Mapping[str, Any] | None,
    ) -> None:
        """Sanitise both mappings and publish them on the bus.

        Sanitisation is part of the publication, not of the recording: a
        payload carrying a reserved key is refused here, after the owner's
        state is already written, and is counted as a lost trace rather than
        being allowed to unwind a terminal state.
        """

        clean_payload = sanitize_trace(
            payload, secrets=self._secrets, max_bytes=self._max_bytes
        )
        clean_metadata = (
            {}
            if metadata is None
            else sanitize_trace(
                metadata, secrets=self._secrets, max_bytes=self._max_bytes
            )
        )
        await self._bus.publish(event_type, clean_payload, clean_metadata)

    def _loss_diagnostic(self, event_type: str, exc: Exception) -> str:
        """Describe a lost trace without carrying a credential out of it."""

        raw = f"{event_type}: {type(exc).__name__}: {exc}"
        try:
            cleaned = sanitize_trace(
                {"reason": raw}, secrets=self._secrets, max_bytes=self._max_bytes
            )
        except Exception:  # noqa: BLE001 - the diagnostic must never itself fail
            return f"{event_type}: {type(exc).__name__}"
        reason = cleaned.get("reason")
        return reason if isinstance(reason, str) else f"{event_type}: unreportable"


# --------------------------------------------------------------------------- #
# Module health: where module.ready / module.degraded / module.stopped come from
# --------------------------------------------------------------------------- #

_PHASE_STATES: Mapping[str, str] = MappingProxyType(
    {
        # Preparation succeeded: consumers, handlers and resources are in
        # place. A module with no producer role is ready at this point and an
        # input module re-reports nothing when its producer opens, because a
        # repeated state is not re-emitted.
        PHASE_PREPARE: MODULE_STATE_READY,
        PHASE_START_INPUTS: MODULE_STATE_READY,
        # Ordinary teardown. ``stop_inputs`` and ``drain`` are not terminal:
        # outputs, stores and executors stay usable through them, so the module
        # is reported stopped only once its close hook returned.
        PHASE_CLOSE: MODULE_STATE_STOPPED,
        PHASE_CLOSE_OBSERVATION: MODULE_STATE_STOPPED,
    }
)
"""Which phase transition reports which state. Absent phases report nothing."""

_SILENT_PHASES = frozenset(PHASES) - frozenset(_PHASE_STATES)
"""Phases whose success is not a supervised state change of its own.

``validate_settings`` reports nothing because no transport is open yet;
``readiness_barrier`` is not a hook; ``stop_inputs``, ``drain`` and ``flush``
leave the module usable, so none of them is a state change on its own. Derived
from :data:`PHASES` so a phase added to the sequence is either mapped to a
state or explicitly silent, never quietly unreported.
"""


class ModuleHealth:
    """Owner of every module's supervised state, and the source of ``module.*``.

    The coordinator drives the phase sequence; this class turns each transition
    into the provider-health facts R8 requires, and owns the state map those
    facts describe. ``module.stopped`` is terminal, so it is written here
    first and published second, through :meth:`Supervision.record_and_emit` —
    the same order every other terminal trace obeys.

    A fact is published only when it changes, so a module that prepared and
    then started its producer is reported ready once, and a close that runs
    twice reports one ``module.stopped``. The reason is part of what changes:
    a module degrading for a *new* reason is a new fact and is reported, while
    the same degradation repeated is not.
    """

    __slots__ = ("_clock", "_reported", "_states", "_supervision")

    def __init__(self, supervision: Supervision, *, clock: Clock = time.monotonic) -> None:
        if not callable(getattr(supervision, "record_and_emit", None)):
            raise RuntimeContextError(
                "module health: field 'supervision': must expose record_and_emit()"
            )
        if not callable(clock):
            raise RuntimeContextError("module health: field 'clock': must be callable")
        self._supervision = supervision
        self._clock = clock
        self._states: dict[str, str] = {}
        # What was last published for each module, reason included, so a new
        # degradation reason is reported and a repeated one is not.
        self._reported: dict[str, tuple[str, str | None]] = {}

    @property
    def states(self) -> Mapping[str, str]:
        """Each known module's last recorded state, in first-seen order."""

        return MappingProxyType(dict(self._states))

    def state(self, module: str) -> str | None:
        """The last recorded state of *module*, or ``None`` if never reported."""

        return self._states.get(module)

    async def ready(
        self,
        module: str,
        *,
        capabilities: Sequence[str] = (),
        **details: Any,
    ) -> bool:
        """Report *module* as ready. Returns whether a trace was published."""

        return await self._transition(
            module, MODULE_STATE_READY, capabilities=capabilities, **details
        )

    async def degraded(
        self,
        module: str,
        *,
        reason: str,
        capabilities: Sequence[str] = (),
        **details: Any,
    ) -> bool:
        """Report *module* as degraded, with a bounded, sanitised *reason*."""

        return await self._transition(
            module,
            MODULE_STATE_DEGRADED,
            capabilities=capabilities,
            reason=reason,
            **details,
        )

    async def stopped(
        self,
        module: str,
        *,
        reason: str | None = None,
        capabilities: Sequence[str] = (),
        **details: Any,
    ) -> bool:
        """Report *module* as stopped, recording the state before the trace."""

        extra = dict(details)
        if reason is not None:
            extra["reason"] = reason
        return await self._transition(
            module, MODULE_STATE_STOPPED, capabilities=capabilities, **extra
        )

    async def observe_phase(
        self,
        phase: str,
        module: str,
        *,
        failure: str | None = None,
        capabilities: Sequence[str] = (),
        **details: Any,
    ) -> bool:
        """Turn one coordinator phase transition into a ``module.*`` fact.

        A hook that failed reports ``module.degraded`` whatever the phase was,
        carrying the phase it failed in alongside the sanitised reason. A hook
        that succeeded reports the state :data:`_PHASE_STATES` maps the phase
        to, and nothing at all for a phase that is not a state change of its
        own. An unknown phase is refused rather than silently unreported.
        """

        _require_text(phase, "module health.observe_phase.phase")
        if phase not in _PHASE_STATES and phase not in _SILENT_PHASES:
            known = ", ".join(PHASES)
            raise RuntimeContextError(
                f"module health.observe_phase.phase: unknown phase {phase!r}; "
                f"the sequence is: {known}"
            )
        if failure is not None:
            return await self.degraded(
                module,
                reason=failure,
                capabilities=capabilities,
                phase=phase,
                **details,
            )
        state = _PHASE_STATES.get(phase)
        if state is None:
            return False
        return await self._transition(
            module, state, capabilities=capabilities, phase=phase, **details
        )

    async def _transition(
        self,
        module: str,
        state: str,
        *,
        capabilities: Sequence[str] = (),
        **details: Any,
    ) -> bool:
        _require_text(module, "module health.module")
        reason = details.get("reason")
        fact = (state, reason if isinstance(reason, str) else None)
        if self._reported.get(module) == fact:
            return False

        payload: dict[str, Any] = {
            "module": module,
            "state": state,
            "at": float(self._clock()),
        }
        if capabilities:
            payload["capabilities"] = [
                _require_text(name, "module health.capabilities")
                for name in capabilities
            ]
        payload.update(details)

        if state == MODULE_STATE_STOPPED:
            # Terminal: the state is written by its owner — this map — before
            # the trace exists, and a publication that fails leaves the state
            # written, counted as a lost trace (R8, AC28).
            await self._supervision.record_and_emit(
                lambda: self._record(module, state, fact),
                TRACE_MODULE_STOPPED,
                payload,
            )
            return True

        event_type = (
            TRACE_MODULE_READY if state == MODULE_STATE_READY else TRACE_MODULE_DEGRADED
        )
        self._record(module, state, fact)
        await self._supervision.emit(event_type, payload)
        return True

    def _record(self, module: str, state: str, fact: tuple[str, str | None]) -> None:
        """Write the module's state. The owner's write, and the only one."""

        self._states[module] = state
        self._reported[module] = fact


# --------------------------------------------------------------------------- #
# Per-module scoped facades
# --------------------------------------------------------------------------- #


class ModuleActions:
    """A module's own write access to the shared action registry.

    The module name is bound at construction and is never a parameter, so a
    module declares, binds and marks ready under its own name only. The shared
    registry itself is not reachable through this facade: reaching it would
    make the scoping advisory.
    """

    __slots__ = ("_module", "_registry")

    def __init__(self, registry: Any, module: str) -> None:
        self._registry = registry
        self._module = _require_text(module, "module actions.module")

    @property
    def module(self) -> str:
        return self._module

    def declare(self, spec: Any) -> None:
        """Record *spec* as declared by this module."""

        self._registry.declare(spec, module=self._module)

    def bind(
        self,
        action_name: str,
        provider: Any,
        *,
        destinations: Any = None,
        provider_name: str | None = None,
    ) -> tuple[Any, ...]:
        """Bind *provider* to an already declared action over its scopes."""

        return self._registry.bind(
            action_name,
            provider,
            module=self._module,
            destinations=destinations,
            provider_name=provider_name,
        )

    def register(
        self,
        spec: Any,
        provider: Any,
        *,
        destinations: Any = None,
        provider_name: str | None = None,
    ) -> tuple[Any, ...]:
        """Declare *spec* and bind *provider* to it in one call.

        The registration a v2 activation performs: the manifest declaration and
        the live handler arrive together, and a binding that fails leaves the
        registry exactly as it was. The all-or-nothing part is the registry's
        own :meth:`ActionRegistry.register`, not a declare-then-bind pair
        assembled here: only the registry can restore a declaration it already
        committed, so rolling back from this facade would be a guess about its
        internals.
        """

        return self._registry.register(
            spec,
            provider,
            module=self._module,
            destinations=destinations,
            provider_name=provider_name,
        )

    def mark_ready(self) -> None:
        """Record this module as past the readiness barrier."""

        self._registry.mark_ready(self._module)

    def mark_not_ready(self) -> None:
        """Withdraw this module from the ready set (degraded or stopped)."""

        self._registry.mark_not_ready(self._module)

    def is_ready(self) -> bool:
        return bool(self._registry.is_ready(self._module))

    def discovered(self) -> Mapping[str, Any]:
        """Every action any manifest declared, bound or not — a read view."""

        return self._registry.discovered()


class ModuleTasks:
    """A module's own access to the supervised task registry.

    Ownership is bound at construction, so nothing detaches under another
    module's name and every failure names the module that spawned it.
    """

    __slots__ = ("_module", "_tasks")

    def __init__(self, tasks: Any, module: str) -> None:
        self._tasks = tasks
        self._module = _require_text(module, "module tasks.module")

    @property
    def module(self) -> str:
        return self._module

    @property
    def active(self) -> int:
        """How many of this module's spawned tasks are still running."""

        return sum(1 for owner in self._tasks.owners() if owner == self._module)

    def spawn(self, coro: Awaitable[Any], *, name: str) -> Any:
        """Register *coro* as a detached task owned by this module."""

        return self._tasks.spawn(coro, name=name, owner=self._module)


class ModuleSupervision:
    """A module's supervision surface: the facade plus its own health calls.

    ``emit``, ``record_and_emit`` and ``snapshot`` are delegated unchanged, so
    the ordering guarantee and the terminal refusal are exactly the shared
    ones. ``ready``, ``degraded`` and ``stopped`` are the module's own state
    transitions, with its name already bound.
    """

    __slots__ = ("_health", "_module", "_supervision")

    def __init__(
        self, supervision: Supervision, health: ModuleHealth, module: str
    ) -> None:
        self._supervision = supervision
        self._health = health
        self._module = _require_text(module, "module supervision.module")

    @property
    def module(self) -> str:
        return self._module

    def emit(
        self,
        event_type: str,
        payload: Mapping[str, Any],
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> Awaitable[None]:
        return self._supervision.emit(event_type, payload, metadata=metadata)

    def record_and_emit(
        self,
        record: Recorder,
        event_type: str,
        payload: Mapping[str, Any],
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> Awaitable[None]:
        return self._supervision.record_and_emit(
            record, event_type, payload, metadata=metadata
        )

    def snapshot(self) -> Mapping[str, int]:
        return self._supervision.snapshot()

    def count(self, name: str, amount: int = 1) -> int:
        return self._supervision.count(name, amount)

    async def ready(self, **details: Any) -> bool:
        return await self._health.ready(self._module, **details)

    async def degraded(self, *, reason: str, **details: Any) -> bool:
        return await self._health.degraded(self._module, reason=reason, **details)

    async def stopped(self, *, reason: str | None = None, **details: Any) -> bool:
        return await self._health.stopped(self._module, reason=reason, **details)


# --------------------------------------------------------------------------- #
# The context itself
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True, kw_only=True)
class RuntimeContext:
    """The versioned container a v2 activation is handed (R7, AC26).

    Frozen on purpose: the container is shared by every module, and a mutable
    one would let a module reconfigure another module's scheduler, swap the
    clock under a running trigger window or replace supervision with a sink.
    Write access is granted through the scoped facades of
    :meth:`for_module`, never by reassigning a field here.

    Every collaborator except the bus, the action registry, supervision and the
    supervised task registry is optional, so a partial runtime — a test
    harness, a validation-only startup — is expressible without inventing a
    stub for a collaborator it never uses. What is present is checked for the
    surface it will actually be called through, which keeps an injected fake
    usable while still catching a mis-wired field by name.
    """

    bus: Any
    actions: Any
    supervision: Supervision
    tasks: Any
    executor: Any = None
    triggers: Any = None
    chat: Any = None
    attachments: Any = None
    scheduler: Any = None
    clock: Clock = time.monotonic
    rng: RandomSource = None
    health: ModuleHealth = None  # type: ignore[assignment]
    runtime_api: int = RUNTIME_API

    def __post_init__(self) -> None:
        if not isinstance(self.runtime_api, int) or isinstance(self.runtime_api, bool):
            raise RuntimeContextError(
                "runtime context: field 'runtime_api': must be an integer, got "
                f"{type(self.runtime_api).__name__}"
            )
        if self.runtime_api not in SUPPORTED_RUNTIME_APIS:
            supported = ", ".join(str(value) for value in sorted(SUPPORTED_RUNTIME_APIS))
            raise RuntimeContextError(
                f"runtime context: field 'runtime_api': {self.runtime_api} is not "
                f"supported; supported: {supported}"
            )

        _require_surface(self.bus, ("publish", "subscribe"), "bus")
        _require_surface(
            self.actions, ("declare", "bind", "register", "mark_ready"), "actions"
        )
        _require_surface(
            self.supervision, ("emit", "record_and_emit", "snapshot"), "supervision"
        )
        _require_surface(self.tasks, ("spawn",), "tasks")
        if self.executor is not None:
            _require_surface(self.executor, ("invoke",), "executor")
        if self.triggers is not None:
            _require_surface(self.triggers, ("evaluate",), "triggers")
        if self.chat is not None:
            _require_surface(self.chat, ("append",), "chat")
        if self.attachments is not None:
            _require_surface(self.attachments, ("put", "get"), "attachments")
        if self.scheduler is not None:
            _require_surface(self.scheduler, ("admit",), "scheduler")

        if not callable(self.clock):
            raise RuntimeContextError("runtime context: field 'clock': must be callable")

        # The random source is injected so a probability rule replays
        # identically; a default is built here rather than shared as a class
        # attribute, so two contexts never draw from one sequence.
        if self.rng is None:
            object.__setattr__(self, "rng", random.Random())
        elif not callable(getattr(self.rng, "random", None)):
            raise RuntimeContextError(
                "runtime context: field 'rng': must expose random()"
            )

        if self.health is None:
            object.__setattr__(
                self, "health", ModuleHealth(self.supervision, clock=self.clock)
            )
        else:
            _require_surface(self.health, ("ready", "degraded", "stopped"), "health")

    # -- scoped access -------------------------------------------------------- #

    def for_module(self, module: str) -> "ModuleContext":
        """Return the scoped view activation hands to *module*.

        The shared collaborators are passed through unchanged; the three
        surfaces a module writes to — its action registrations, its supervised
        tasks and its own health — arrive already bound to its name.
        """

        name = _require_text(module, "runtime context.for_module.module")
        return ModuleContext(
            module=name,
            _runtime=self,
            actions=ModuleActions(self.actions, name),
            tasks=ModuleTasks(self.tasks, name),
            supervision=ModuleSupervision(self.supervision, self.health, name),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class ModuleContext:
    """One module's view of the runtime: shared reads, scoped writes.

    A module never receives the unscoped :class:`RuntimeContext` collaborators
    it could use to act for another module. Everything else — the bus, the
    executor, the trigger engine, the stores, the clock and the random source —
    is the same object every module sees, which is what makes the context the
    single injection channel it claims to be (AC26).

    The container itself is held privately and read only through the properties
    below. Exposing it would make the scoping advisory rather than real:
    ``context.runtime.actions`` is the unscoped registry, so a module holding it
    could withdraw another module's readiness or spawn tasks under another
    module's name, and freezing the container prevents neither — a frozen field
    stops the collaborator being *replaced*, not being *used*. The properties
    republish exactly the collaborators that are shared by design; the three
    write surfaces arrive only through their scoped facades.
    """

    module: str
    _runtime: RuntimeContext
    actions: ModuleActions
    tasks: ModuleTasks
    supervision: ModuleSupervision

    @property
    def runtime_api(self) -> int:
        return self._runtime.runtime_api

    @property
    def bus(self) -> Any:
        return self._runtime.bus

    @property
    def executor(self) -> Any:
        return self._runtime.executor

    @property
    def triggers(self) -> Any:
        return self._runtime.triggers

    @property
    def chat(self) -> Any:
        return self._runtime.chat

    @property
    def attachments(self) -> Any:
        return self._runtime.attachments

    @property
    def scheduler(self) -> Any:
        return self._runtime.scheduler

    @property
    def clock(self) -> Clock:
        return self._runtime.clock

    @property
    def rng(self) -> RandomSource:
        return self._runtime.rng


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #


def _require_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise RuntimeContextError(
            f"{field_name}: must be a string, got {type(value).__name__}"
        )
    if not value.strip():
        raise RuntimeContextError(f"{field_name}: must not be empty")
    return value


def _require_surface(value: Any, methods: Sequence[str], field_name: str) -> Any:
    """Check *value* answers the calls this field will actually receive.

    Duck-typed rather than ``isinstance``-checked so a test can inject a fake
    bus or a recording supervision, while a mis-wired field still fails at
    construction with a diagnostic naming the field and the missing call
    instead of at the first publication.
    """

    if value is None:
        raise RuntimeContextError(f"runtime context: field {field_name!r}: is required")
    for method in methods:
        if not callable(getattr(value, method, None)):
            raise RuntimeContextError(
                f"runtime context: field {field_name!r}: must expose {method}()"
            )
    return value
