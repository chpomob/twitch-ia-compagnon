"""Action registry, executor and default-deny authorization (R5, R8).

Three objects carry the whole of R5's action surface.

:class:`ActionRegistry` holds what modules declare and what they bind. It binds
**exactly one** provider per ``(action name, destination scope)``: a second
provider whose destination overlaps an existing binding fails *preparation*
with an :class:`AmbiguousBindingError` naming the action and both competing
providers, and no provider is invoked to discover it (AC16). Overlap is decided
by :meth:`~core.contracts.Destination.overlaps`, so a wildcard scope colliding
with a concrete one, or ``chat`` colliding with ``chat.reply``, is caught while
binding rather than at the first call. There is no broadcast to several sinks
and no automatic fallback after an uncertain effect: resolution returns one
binding or none.

The registry exposes three **distinct** views, deliberately not collapsible into
one another: :meth:`~ActionRegistry.discovered` is what manifests declared,
:meth:`~ActionRegistry.registered_ready` is what has a bound provider whose
module passed the readiness barrier, and :meth:`~ActionRegistry.authorized` is
what the applicable rules permit a given principal at a given destination. Only
the authorized view is ever offered to the model (R5).

:class:`AuthorizationPolicy` is **default-deny**. It holds explicit rules and
answers one question — may this principal run this action at this destination?
— with no ambient grant, no per-run cache and no read/write asymmetry: with 0
rules a read is refused exactly like a write (AC17). Because the executor asks
the policy on every call and keeps nothing, revoking a rule between two calls of
one run refuses the second.

:class:`ActionExecutor` runs, in this order: authorization, argument validation,
provider invocation under the spec's timeout, result validation. Anything that
fails before the provider is reached performs 0 provider invocations, and a
result failing the result schema is reported as ``error`` — never as a success
with an unchecked payload (AC18). A bound provider whose module is not ready —
never became ready, or lost its readiness mid-run when a remote agent
disconnected — is ``refused`` with ``provider_not_ready`` and 0 invocations,
so the model reads it as a refusal to act rather than a broken action (R6,
AC35).

Result validation covers the observation's typed ``parts`` (phase 1, R4) on
**every** terminal observation a provider returns, whatever its status: the
parts must have the contract's shape, every ``image_ref`` must name an
attachment the injected :class:`~core.attachments.AttachmentStore` holds
leased to the call's ``run_id``, unexpired on the executor clock at
validation and of the stored size, and the observation's size — text bytes
plus image sizes, see :func:`~core.contracts.observation_size` — must not
exceed the executor's ``max_observation_bytes``. A lease failure is
``error invalid_result``, an oversized observation ``error
observation_too_large``; in both cases nothing of the provider's observation
is adopted and every ``image_ref`` it named is discarded from the store at
once, so a rejected observation leaves no partially adopted parts and no
bytes leased until the run ends (AC22, AC47). The size rule is one rule with
two enforcement points: the brain enforces its own ``budget.max_observation_bytes``
per run, the executor's bound is a runtime-wide guard a caller may set and
``core.main`` leaves unset. Observations the executor synthesises itself carry
no parts.

Two properties are structural rather than conventional.

*An uncertain effect is never reported as a certainty.* A provider signals
emission explicitly on the :class:`ActionInvocation` handle it is given. When a
call times out or is cancelled, an emitted effect yields ``external_unknown``, an
explicitly un-emitted one yields ``timeout``/``cancelled``, and **an absent
signal on a ``write`` yields ``external_unknown``** — reporting a timed-out send
as ``timeout`` when the request may already be on the wire is the false negative
that invites a duplicate send, so the conservative side is the default (R2, R5,
AC33).

*The terminal state is recorded before its trace is published.* The executor is
the owner of the action terminal state, so R8's record-before-publish ordering
lives here: the completed :class:`~core.contracts.ActionObservation` is written
into the per-call outcome store — the value :meth:`ActionExecutor.invoke`
returns and the value the run reads — **before** ``action.completed`` is handed
to supervision. A publication that fails therefore leaves the observation
recorded and readable, is counted as a lost trace, and never re-invokes the
provider, retries the call or downgrades the recorded status. Re-invoking a
``call_id`` that already has a recorded outcome returns that outcome and reaches
0 providers, so even a caller that retries after a failed trace cannot produce a
second external effect.

This module opens no transport and reads time only through an injected clock;
the timeout race is driven by an injected sleeper, so a timeout can be provoked
without sleeping. Cancelling a provider is bounded by
:data:`DEFAULT_CANCEL_GRACE_SECONDS`, because a provider that declines to stop
must not be able to leave a call with no terminal observation at all.
"""

from __future__ import annotations

import asyncio
import inspect
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Any, Protocol

from .attachments import AttachmentStore
from .contracts import (
    ACTION_NATURES,
    BRAIN_ERROR_OBSERVATION_TOO_LARGE,
    COUNTER_ACTION_TIMEOUTS,
    COUNTER_LOST_TRACES,
    PART_TYPE_IMAGE_REF,
    TRACE_ACTION_COMPLETED,
    TRACE_ACTION_STARTED,
    WILDCARD,
    ActionCall,
    ActionObservation,
    ActionSpec,
    ContractError,
    Counters,
    Destination,
    observation_size,
    validate_parts,
)

__all__ = [
    "ANY_PRINCIPAL",
    "DEFAULT_CANCEL_GRACE_SECONDS",
    "DEFAULT_MAX_OUTCOMES",
    "EMISSION_EMITTED",
    "EMISSION_NOT_EMITTED",
    "EMISSION_UNKNOWN",
    "ERROR_INVALID_ARGUMENTS",
    "ERROR_INVALID_OBSERVATION",
    "ERROR_INVALID_RESULT",
    "ERROR_NOT_AUTHORIZED",
    "ERROR_NO_PROVIDER",
    "ERROR_OBSERVATION_TOO_LARGE",
    "ERROR_PROVIDER_FAILED",
    "ERROR_PROVIDER_NOT_READY",
    "ERROR_UNKNOWN_ACTION",
    "ERROR_UNSUPPORTED_DESTINATION",
    "ERROR_VERSION_MISMATCH",
    "REASON_NO_RULE",
    "REASON_MISSING_PERMISSION",
    "REASON_RULE_MATCHED",
    "ActionBinding",
    "ActionDeclarationError",
    "ActionExecutor",
    "ActionInvocation",
    "ActionProvider",
    "ActionRegistry",
    "ActionRegistryError",
    "AmbiguousBindingError",
    "AuthorizationDecision",
    "AuthorizationPolicy",
    "AuthorizationRule",
    "Supervision",
    "UnknownActionError",
]

Clock = Callable[[], float]
Sleeper = Callable[[float], Awaitable[None]]

ANY_PRINCIPAL = WILDCARD
"""Principal token a rule uses to apply to every caller."""

ANY_ACTION = WILDCARD
"""Action token a rule uses to apply to every action name."""

DEFAULT_MAX_OUTCOMES = 1024
"""How many terminal observations the executor keeps addressable by ``call_id``.

The store is bounded like every other retention surface in the runtime (R6):
oldest first. A ``call_id`` evicted from it is no longer replay-protected, which
is why the bound is generous relative to the number of calls one run makes.
"""

DEFAULT_CANCEL_GRACE_SECONDS = 1.0
"""How long the executor waits for a cancelled provider task to actually end.

Cancellation is a request, not a guarantee: a provider that catches
``CancelledError`` and keeps waiting would otherwise hold ``invoke`` open for
ever and the call would reach *no* terminal observation at all — the one
outcome R8 does not permit. Past this grace the task is abandoned (still
cancelled, its eventual exception consumed) and the outcome is recorded anyway.
"""

# --------------------------------------------------------------------------- #
# Emission signalling
# --------------------------------------------------------------------------- #

EMISSION_UNKNOWN = "unknown"
"""The provider said nothing about whether the external effect left."""

EMISSION_NOT_EMITTED = "not_emitted"
"""The provider stated that nothing reached the outside world."""

EMISSION_EMITTED = "emitted"
"""The provider stated that the effect may already have been emitted."""

EMISSION_STATES = frozenset({EMISSION_UNKNOWN, EMISSION_NOT_EMITTED, EMISSION_EMITTED})

# --------------------------------------------------------------------------- #
# Normalised error codes
# --------------------------------------------------------------------------- #

ERROR_UNKNOWN_ACTION = "unknown_action"
ERROR_VERSION_MISMATCH = "action_version_mismatch"
ERROR_UNSUPPORTED_DESTINATION = "unsupported_destination"
ERROR_NOT_AUTHORIZED = "not_authorized"
ERROR_INVALID_ARGUMENTS = "invalid_arguments"
ERROR_INVALID_RESULT = "invalid_result"
ERROR_INVALID_OBSERVATION = "invalid_observation"
ERROR_OBSERVATION_TOO_LARGE = BRAIN_ERROR_OBSERVATION_TOO_LARGE
"""An observation whose parts exceed the executor's byte bound (R4, AC47).

The same literal the brain uses for its own per-run budget: one rule, two
enforcement points, one code in the traces.
"""
ERROR_NO_PROVIDER = "no_provider"
ERROR_PROVIDER_NOT_READY = "provider_not_ready"
ERROR_PROVIDER_FAILED = "provider_failed"
ERROR_TIMED_OUT = "timed_out"
ERROR_CANCELLED = "cancelled"
ERROR_EXTERNAL_UNKNOWN = "external_effect_unknown"

REASON_NO_RULE = "no_applicable_rule"
REASON_MISSING_PERMISSION = "missing_permission"
REASON_RULE_MATCHED = "rule_matched"


# --------------------------------------------------------------------------- #
# Preparation failures
# --------------------------------------------------------------------------- #


class ActionRegistryError(RuntimeError):
    """Raised while preparing the action surface, before any call is made."""


class ActionDeclarationError(ActionRegistryError):
    """Two modules declared incompatible specs for one action name."""


class AmbiguousBindingError(ActionRegistryError):
    """Two providers claim overlapping destinations for one action (AC16).

    The diagnostic names the action and both competing providers, because the
    operator's fix is to change one of the two bindings and the message is the
    only place that says which two they are.
    """

    def __init__(
        self,
        action_name: str,
        existing_provider: str,
        existing_destination: Destination,
        new_provider: str,
        new_destination: Destination,
    ) -> None:
        self.action_name = action_name
        self.existing_provider = existing_provider
        self.new_provider = new_provider
        super().__init__(
            f"Ambiguous binding for action {action_name!r}: provider "
            f"{existing_provider!r} already covers destination "
            f"{existing_destination}, which overlaps destination "
            f"{new_destination} claimed by provider {new_provider!r}. "
            f"Exactly one provider may be bound per action and destination scope."
        )


class UnknownActionError(ActionRegistryError):
    """A provider was bound to, or a view asked about, an undeclared action."""


# --------------------------------------------------------------------------- #
# Protocols
# --------------------------------------------------------------------------- #


class ActionProvider(Protocol):
    """One async invocation returning a terminal observation (R5).

    ``name`` identifies the provider in binding diagnostics and in traces, so it
    must be stable and human-readable: it is what AC16's ambiguity message
    prints.

    :meth:`invoke` receives the :class:`ActionInvocation` handle rather than the
    bare call, because signalling emission is part of the contract: a provider
    that puts a request on the wire calls :meth:`ActionInvocation.mark_emitted`
    *before* awaiting the response, so a timeout after that point is reported as
    ``external_unknown`` and never as ``timeout``.
    """

    name: str

    async def invoke(self, invocation: "ActionInvocation") -> ActionObservation:
        ...  # pragma: no cover - protocol declaration


class Supervision(Protocol):
    """The subset of P10's supervision facade the executor depends on (R8).

    Declared structurally so this module does not import the runtime context and
    close an import cycle. Either method may be synchronous or awaitable.

    ``record_and_emit`` must call *record* before publishing, and must not let a
    publication failure reach the caller; the executor does not rely on that
    alone, since it writes the outcome itself before handing the trace over.
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

    It still honours the ordering contract by calling *record* first, so an
    executor built without supervision behaves like one built with it.
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
# The handle a provider is given
# --------------------------------------------------------------------------- #


class ActionInvocation:
    """One call in flight, plus the provider's explicit emission signal.

    The signal is monotone in the unsafe direction only: once a provider has
    declared that the effect may have left, it cannot take that back, because
    the whole point is to stop a later layer from concluding "nothing happened"
    from a missing response.

    The handle also carries the provider's *completion hooks*: work the
    provider wants run once this call is terminal — its observation recorded
    and ``action.completed`` offered to supervision — and not before. A
    provider whose effect is confirmed inside ``invoke`` publishes the fact of
    that effect through one, so the audit reads the executor's completion
    first and the provider's fact after it, the order AC27 fixes.
    """

    __slots__ = (
        "call",
        "spec",
        "provider_name",
        "_emission",
        "_hooks",
        "_completed",
        "_provider_completed_at",
    )

    def __init__(self, call: ActionCall, spec: ActionSpec, provider_name: str) -> None:
        self.call = call
        self.spec = spec
        self.provider_name = provider_name
        self._emission = EMISSION_UNKNOWN
        self._hooks: list[Callable[[], Any]] = []
        self._completed = False
        self._provider_completed_at: float | None = None

    @property
    def provider_completed_at(self) -> float | None:
        """When the provider's ``invoke`` ended, on the executor's clock.

        Stamped by the executor on the provider's own task, the instant the
        coroutine returns, raises or is cancelled — before any other task can
        run. It is the time the confirmation (or failure) *arrived*, which is
        the time the deadline is judged against; the instant the executor gets
        around to adopting it is not, since the loop may resume it late.
        ``None`` while the provider is still running.
        """

        return self._provider_completed_at

    @property
    def emission(self) -> str:
        """One of :data:`EMISSION_UNKNOWN`, ``NOT_EMITTED``, ``EMITTED``."""

        return self._emission

    @property
    def emitted(self) -> bool:
        return self._emission == EMISSION_EMITTED

    def mark_emitted(self) -> None:
        """Declare that the external effect may already have been emitted."""

        self._emission = EMISSION_EMITTED

    def mark_not_emitted(self) -> None:
        """Declare that nothing has reached the outside world yet."""

        if self._emission == EMISSION_EMITTED:
            raise ContractError(
                "ActionInvocation.mark_not_emitted",
                "cannot unsay an emission that was already declared",
            )
        self._emission = EMISSION_NOT_EMITTED

    def after_completion(self, hook: Callable[[], Any]) -> None:
        """Run *hook* once this call is terminal, never before (R8, AC27).

        The executor calls it after the terminal observation is recorded and
        its ``action.completed`` trace has been offered to supervision — on
        every exit, a cancellation included — so a fact the provider defers
        here is published after the executor's own completion. A hook
        registered once the call is already terminal runs at once; each hook
        runs exactly once. A hook is synchronous and hands any waiting to the
        loop: it runs on the executor's path, and it must not spend the
        caller's time. Whatever it raises is its own and never reaches the
        recorded terminal state.
        """

        if not callable(hook):
            raise ContractError(
                "ActionInvocation.after_completion", "expects a callable hook"
            )
        if self._completed:
            _run_hook(hook)
            return
        self._hooks.append(hook)

    def _complete(self) -> None:
        """Mark the call terminal and run the hooks registered so far."""

        self._completed = True
        hooks, self._hooks = self._hooks, []
        for hook in hooks:
            _run_hook(hook)


def _run_hook(hook: Callable[[], Any]) -> None:
    try:
        hook()
    except Exception:
        # The hook is the provider's own work past the terminal state: its
        # failure is the provider's to diagnose and cannot unwind an outcome
        # that is already recorded.
        return


# --------------------------------------------------------------------------- #
# Authorization (default-deny)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class AuthorizationRule:
    """One explicit grant. There is no implicit one (R5, AC17).

    A rule applies when the action name, the nature, the principal and the
    destination all match; it *permits* only when it also grants every
    permission the spec requires, so narrowing a spec's ``required_permissions``
    is what widens the grant, never the absence of a check.
    """

    rule_id: str
    action_name: str = ANY_ACTION
    destination: Destination = Destination(WILDCARD, WILDCARD, WILDCARD)
    principals: tuple[str, ...] = (ANY_PRINCIPAL,)
    natures: tuple[str, ...] = tuple(sorted(ACTION_NATURES))
    granted_permissions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_text(self.rule_id, "AuthorizationRule.rule_id")
        _require_text(self.action_name, "AuthorizationRule.action_name")
        if not isinstance(self.destination, Destination):
            raise ContractError("AuthorizationRule.destination", "must be a Destination")
        object.__setattr__(
            self,
            "principals",
            _texts(self.principals, "AuthorizationRule.principals", minimum=1),
        )
        natures = _texts(self.natures, "AuthorizationRule.natures", minimum=1)
        for nature in natures:
            if nature not in ACTION_NATURES:
                allowed = ", ".join(sorted(ACTION_NATURES))
                raise ContractError(
                    "AuthorizationRule.natures",
                    f"must be one of {allowed}, got {nature!r}",
                )
        object.__setattr__(self, "natures", natures)
        object.__setattr__(
            self,
            "granted_permissions",
            _texts(self.granted_permissions, "AuthorizationRule.granted_permissions"),
        )

    def applies_to(self, spec: ActionSpec, principal: str, destination: Destination) -> bool:
        """Whether this rule has anything to say about that call."""

        if self.action_name not in (ANY_ACTION, spec.name):
            return False
        if spec.nature not in self.natures:
            return False
        if ANY_PRINCIPAL not in self.principals and principal not in self.principals:
            return False
        return self.destination.contains(destination)

    def grants(self, spec: ActionSpec) -> bool:
        """Whether this rule carries every permission *spec* requires."""

        granted = set(self.granted_permissions)
        return all(permission in granted for permission in spec.required_permissions)


@dataclass(frozen=True, slots=True)
class AuthorizationDecision:
    """Why a call was permitted or refused, for the trace and the observation."""

    allowed: bool
    reason: str
    rule_id: str | None = None


class AuthorizationPolicy:
    """A mutable set of explicit rules, evaluated fresh on every call (R5).

    Default-deny is structural: :meth:`permits` starts from "no", and a rule set
    that says nothing about a call cannot make it "yes". The policy caches
    nothing per run and holds no per-principal grant, so revoking a rule takes
    effect on the very next call.
    """

    __slots__ = ("_rules",)

    def __init__(self, rules: Sequence[AuthorizationRule] = ()) -> None:
        self._rules: "OrderedDict[str, AuthorizationRule]" = OrderedDict()
        for rule in rules:
            self.grant(rule)

    def grant(self, rule: AuthorizationRule) -> None:
        """Add *rule*, replacing any rule already carrying its ``rule_id``."""

        if not isinstance(rule, AuthorizationRule):
            raise ContractError("AuthorizationPolicy.grant", "expects an AuthorizationRule")
        self._rules[rule.rule_id] = rule

    def revoke(self, rule_id: str) -> bool:
        """Drop the rule carrying *rule_id*; return whether one was dropped."""

        _require_text(rule_id, "AuthorizationPolicy.revoke")
        return self._rules.pop(rule_id, None) is not None

    def clear(self) -> None:
        self._rules.clear()

    @property
    def rules(self) -> tuple[AuthorizationRule, ...]:
        return tuple(self._rules.values())

    def permits(
        self, spec: ActionSpec, *, principal: str, destination: Destination
    ) -> AuthorizationDecision:
        """Answer, from the current rules alone, whether this call may run."""

        if not isinstance(spec, ActionSpec):
            raise ContractError("AuthorizationPolicy.permits", "expects an ActionSpec")
        _require_text(principal, "AuthorizationPolicy.permits.principal")
        if not isinstance(destination, Destination):
            raise ContractError(
                "AuthorizationPolicy.permits.destination", "must be a Destination"
            )

        applicable = [
            rule
            for rule in self._rules.values()
            if rule.applies_to(spec, principal, destination)
        ]
        if not applicable:
            return AuthorizationDecision(False, REASON_NO_RULE)
        for rule in applicable:
            if rule.grants(spec):
                return AuthorizationDecision(True, REASON_RULE_MATCHED, rule.rule_id)
        return AuthorizationDecision(False, REASON_MISSING_PERMISSION, applicable[0].rule_id)


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class ActionBinding:
    """One provider bound to one action over one destination scope."""

    spec: ActionSpec
    provider: Any
    provider_name: str
    destination: Destination
    module: str


class ActionRegistry:
    """What is declared, what is bound and ready, and what is authorized (R5)."""

    __slots__ = ("_authorization", "_bindings", "_modules", "_ready", "_specs")

    def __init__(self, *, authorization: AuthorizationPolicy | None = None) -> None:
        self._authorization = authorization
        self._specs: "OrderedDict[str, ActionSpec]" = OrderedDict()
        self._modules: dict[str, str] = {}
        self._bindings: "OrderedDict[str, list[ActionBinding]]" = OrderedDict()
        self._ready: set[str] = set()

    # -- declaration ------------------------------------------------------- #

    def declare(self, spec: ActionSpec, *, module: str) -> None:
        """Record *spec* as discovered, as a manifest declares it."""

        if not isinstance(spec, ActionSpec):
            raise ContractError("ActionRegistry.declare", "expects an ActionSpec")
        _require_text(module, "ActionRegistry.declare.module")
        existing = self._specs.get(spec.name)
        if existing is not None and existing != spec:
            raise ActionDeclarationError(
                f"Action {spec.name!r} is already declared by module "
                f"{self._modules[spec.name]!r} with a different specification; "
                f"module {module!r} cannot redeclare it."
            )
        self._specs[spec.name] = spec
        self._modules[spec.name] = module
        self._bindings.setdefault(spec.name, [])

    # -- binding ----------------------------------------------------------- #

    def bind(
        self,
        action_name: str,
        provider: Any,
        *,
        module: str,
        destinations: Destination | Sequence[Destination] | None = None,
        provider_name: str | None = None,
    ) -> tuple[ActionBinding, ...]:
        """Bind *provider* to *action_name* over one or more destination scopes.

        Preparation is atomic: every destination is validated against the spec
        and against every existing binding **before** anything is recorded, so a
        binding that fails leaves the registry exactly as it was and invokes 0
        providers (AC16).
        """

        _require_text(action_name, "ActionRegistry.bind.action_name")
        _require_text(module, "ActionRegistry.bind.module")
        spec = self._specs.get(action_name)
        if spec is None:
            known = ", ".join(sorted(self._specs)) or "none"
            raise UnknownActionError(
                f"Cannot bind a provider to undeclared action {action_name!r}; "
                f"declared actions: {known}."
            )

        name = provider_name if provider_name is not None else getattr(provider, "name", None)
        _require_text(name, "ActionRegistry.bind.provider_name")
        if not callable(getattr(provider, "invoke", None)):
            raise ContractError(
                "ActionRegistry.bind.provider", "must expose an invoke() coroutine"
            )

        targets = _destination_sequence(destinations, spec)
        existing = self._bindings.setdefault(action_name, [])

        candidates: list[ActionBinding] = []
        for destination in targets:
            if not spec.supports(destination):
                declared = ", ".join(str(d) for d in spec.supported_destinations)
                raise ActionRegistryError(
                    f"Provider {name!r} cannot be bound to action {action_name!r} at "
                    f"destination {destination}: the specification supports {declared}."
                )
            for bound in (*existing, *candidates):
                if bound.destination.overlaps(destination):
                    raise AmbiguousBindingError(
                        action_name,
                        bound.provider_name,
                        bound.destination,
                        name,
                        destination,
                    )
            candidates.append(
                ActionBinding(
                    spec=spec,
                    provider=provider,
                    provider_name=name,
                    destination=destination,
                    module=module,
                )
            )

        existing.extend(candidates)
        return tuple(candidates)

    # -- registration ------------------------------------------------------- #

    def register(
        self,
        spec: ActionSpec,
        provider: Any,
        *,
        module: str,
        destinations: Destination | Sequence[Destination] | None = None,
        provider_name: str | None = None,
    ) -> tuple[ActionBinding, ...]:
        """Declare *spec* and bind *provider* to it, all or nothing.

        The registration a v2 activation performs: the manifest declaration and
        the live handler arrive together. :meth:`declare` and :meth:`bind` are
        each atomic on their own, but the pair is not — an invalid provider or
        an unsupported destination would otherwise leave the declaration
        committed, so ``discovered()`` would name an action nothing can serve
        and a corrected specification could no longer be declared under that
        name. The declaration is therefore restored to exactly what it was
        before the call whenever the binding is refused (AC16).
        """

        if not isinstance(spec, ActionSpec):
            raise ContractError("ActionRegistry.register", "expects an ActionSpec")
        declared = spec.name in self._specs
        previous_spec = self._specs.get(spec.name)
        previous_module = self._modules.get(spec.name)

        self.declare(spec, module=module)
        try:
            return self.bind(
                spec.name,
                provider,
                module=module,
                destinations=destinations,
                provider_name=provider_name,
            )
        except BaseException:
            self._rollback_declaration(
                spec.name, declared, previous_spec, previous_module
            )
            raise

    def _rollback_declaration(
        self,
        action_name: str,
        declared: bool,
        previous_spec: ActionSpec | None,
        previous_module: str | None,
    ) -> None:
        """Undo the declaration half of a registration whose binding failed.

        A name that was not declared before the call is removed outright,
        including the empty binding list :meth:`declare` seeded. A name that
        was already declared keeps its owner and its specification: the
        redeclaration is reverted, never the original.
        """

        if declared:
            if previous_spec is not None:
                self._specs[action_name] = previous_spec
            if previous_module is not None:
                self._modules[action_name] = previous_module
            return
        self._specs.pop(action_name, None)
        self._modules.pop(action_name, None)
        if not self._bindings.get(action_name):
            self._bindings.pop(action_name, None)

    # -- readiness barrier -------------------------------------------------- #

    def mark_ready(self, module: str) -> None:
        """Record that *module* passed the readiness barrier."""

        _require_text(module, "ActionRegistry.mark_ready")
        self._ready.add(module)

    def mark_not_ready(self, module: str) -> None:
        """Withdraw *module* from the ready set (degraded or stopped)."""

        _require_text(module, "ActionRegistry.mark_not_ready")
        self._ready.discard(module)

    def is_ready(self, module: str) -> bool:
        return module in self._ready

    # -- views -------------------------------------------------------------- #

    def discovered(self) -> Mapping[str, ActionSpec]:
        """Every action a manifest declared, bound or not."""

        return MappingProxyType(dict(self._specs))

    def registered_ready(self) -> Mapping[str, ActionSpec]:
        """Declared actions with a bound provider whose module is ready."""

        return MappingProxyType(
            {
                name: spec
                for name, spec in self._specs.items()
                if any(
                    binding.module in self._ready
                    for binding in self._bindings.get(name, ())
                )
            }
        )

    def authorized(
        self,
        *,
        principal: str,
        destination: Destination | None = None,
        authorization: AuthorizationPolicy | None = None,
    ) -> Mapping[str, ActionSpec]:
        """The only view offered to the model (R5).

        With no policy at all the view is empty, which is default-deny expressed
        as a surface rather than as a check: an unconfigured runtime offers the
        model nothing rather than everything.
        """

        _require_text(principal, "ActionRegistry.authorized.principal")
        policy = authorization if authorization is not None else self._authorization
        if policy is None:
            return MappingProxyType({})
        if destination is not None and not isinstance(destination, Destination):
            raise ContractError(
                "ActionRegistry.authorized.destination", "must be a Destination"
            )

        allowed: dict[str, ActionSpec] = {}
        for name, spec in self.registered_ready().items():
            for binding in self._bindings.get(name, ()):
                if binding.module not in self._ready:
                    continue
                target = destination if destination is not None else binding.destination
                if destination is not None and not binding.destination.contains(destination):
                    continue
                if policy.permits(spec, principal=principal, destination=target).allowed:
                    allowed[name] = spec
                    break
        return MappingProxyType(allowed)

    def bindings(self, action_name: str | None = None) -> tuple[ActionBinding, ...]:
        """Every recorded binding, or those of one action."""

        if action_name is None:
            return tuple(
                binding for bindings in self._bindings.values() for binding in bindings
            )
        return tuple(self._bindings.get(action_name, ()))

    def resolve(self, action_name: str, destination: Destination) -> ActionBinding | None:
        """The single binding covering *destination*, or ``None``.

        Preparation guarantees at most one; the defensive check here turns a
        registry corrupted by some later change into a loud failure rather than
        into a silent broadcast.
        """

        _require_text(action_name, "ActionRegistry.resolve.action_name")
        if not isinstance(destination, Destination):
            raise ContractError("ActionRegistry.resolve.destination", "must be a Destination")
        matches = [
            binding
            for binding in self._bindings.get(action_name, ())
            if binding.destination.contains(destination)
        ]
        if not matches:
            return None
        if len(matches) > 1:
            raise AmbiguousBindingError(
                action_name,
                matches[0].provider_name,
                matches[0].destination,
                matches[1].provider_name,
                matches[1].destination,
            )
        return matches[0]


def _destination_sequence(
    destinations: Destination | Sequence[Destination] | None, spec: ActionSpec
) -> tuple[Destination, ...]:
    if destinations is None:
        return spec.supported_destinations
    if isinstance(destinations, Destination):
        return (destinations,)
    if isinstance(destinations, str) or not isinstance(destinations, Sequence):
        raise ContractError(
            "ActionRegistry.bind.destinations", "must be a Destination or a sequence of them"
        )
    targets = tuple(destinations)
    if not targets:
        raise ContractError(
            "ActionRegistry.bind.destinations", "must name at least one destination"
        )
    for index, destination in enumerate(targets):
        if not isinstance(destination, Destination):
            raise ContractError(
                f"ActionRegistry.bind.destinations[{index}]", "must be a Destination"
            )
    return targets


# --------------------------------------------------------------------------- #
# Executor
# --------------------------------------------------------------------------- #


class ActionExecutor:
    """Authorize, validate, invoke under a timeout, validate again (R5, R8).

    The executor owns the action terminal state: every call ends in exactly one
    :class:`~core.contracts.ActionObservation`, written into the outcome store
    before ``action.completed`` is handed to supervision, and readable
    afterwards through :meth:`outcome` whatever the trace did.
    """

    __slots__ = (
        "_attachments",
        "_authorization",
        "_cancel_grace",
        "_clock",
        "_counters",
        "_max_observation_bytes",
        "_max_outcomes",
        "_outcomes",
        "_provider_invocations",
        "_registry",
        "_sleep",
        "_supervision",
    )

    def __init__(
        self,
        registry: ActionRegistry,
        authorization: AuthorizationPolicy,
        *,
        supervision: Supervision | None = None,
        counters: Counters | None = None,
        clock: Clock = time.monotonic,
        sleeper: Sleeper | None = None,
        max_outcomes: int = DEFAULT_MAX_OUTCOMES,
        cancel_grace_seconds: float = DEFAULT_CANCEL_GRACE_SECONDS,
        attachments: AttachmentStore | None = None,
        max_observation_bytes: int | None = None,
    ) -> None:
        """Wire the executor onto its registry, policy and optional store.

        ``attachments`` is the store every ``image_ref`` part is checked
        against; without one, an observation naming an image cannot be
        validated and is ``invalid_result``. ``max_observation_bytes`` bounds
        the size of every observation's parts (R4); ``None`` sets no bound
        here, leaving the brain's own ``budget.max_observation_bytes`` as the
        single enforcement point in production.
        """

        if not isinstance(registry, ActionRegistry):
            raise ContractError("ActionExecutor.registry", "must be an ActionRegistry")
        if not isinstance(authorization, AuthorizationPolicy):
            raise ContractError(
                "ActionExecutor.authorization", "must be an AuthorizationPolicy"
            )
        if not callable(clock):
            raise ContractError("ActionExecutor.clock", "must be callable")
        if not isinstance(max_outcomes, int) or isinstance(max_outcomes, bool):
            raise ContractError("ActionExecutor.max_outcomes", "must be an integer")
        if max_outcomes < 1:
            raise ContractError("ActionExecutor.max_outcomes", "must be strictly positive")
        if isinstance(cancel_grace_seconds, bool) or not isinstance(
            cancel_grace_seconds, (int, float)
        ):
            raise ContractError("ActionExecutor.cancel_grace_seconds", "must be a number")
        if cancel_grace_seconds < 0:
            raise ContractError(
                "ActionExecutor.cancel_grace_seconds", "must not be negative"
            )
        if attachments is not None and not isinstance(attachments, AttachmentStore):
            raise ContractError(
                "ActionExecutor.attachments", "must be an AttachmentStore or None"
            )
        if max_observation_bytes is not None:
            if isinstance(max_observation_bytes, bool) or not isinstance(
                max_observation_bytes, int
            ):
                raise ContractError(
                    "ActionExecutor.max_observation_bytes", "must be an integer or None"
                )
            if max_observation_bytes < 1:
                raise ContractError(
                    "ActionExecutor.max_observation_bytes", "must be strictly positive"
                )

        self._registry = registry
        self._authorization = authorization
        self._supervision: Any = supervision if supervision is not None else _NullSupervision()
        self._counters = counters
        self._clock = clock
        self._sleep: Sleeper = sleeper if sleeper is not None else asyncio.sleep
        self._max_outcomes = max_outcomes
        self._cancel_grace = float(cancel_grace_seconds)
        self._attachments = attachments
        self._max_observation_bytes = max_observation_bytes
        self._outcomes: "OrderedDict[str, ActionObservation]" = OrderedDict()
        self._provider_invocations = 0

    # -- outcome store ------------------------------------------------------ #

    @property
    def provider_invocations(self) -> int:
        """How many times a provider was actually entered, for auditing."""

        return self._provider_invocations

    def outcome(self, call_id: str) -> ActionObservation | None:
        """The recorded terminal observation of *call_id*, if still retained."""

        _require_text(call_id, "ActionExecutor.outcome")
        return self._outcomes.get(call_id)

    def outcomes(self) -> Mapping[str, ActionObservation]:
        """Every retained terminal observation, oldest first."""

        return MappingProxyType(dict(self._outcomes))

    def _record(self, call_id: str, observation: ActionObservation) -> None:
        """Write the terminal state. Idempotent, and never a downgrade.

        ``record_and_emit`` calls this again as its ordering primitive, so the
        second write must be a no-op rather than a conflict; a *different*
        observation for a recorded ``call_id`` is a programming error and is
        refused, because silently overwriting a terminal state is exactly the
        downgrade R8 forbids.
        """

        stored = self._outcomes.get(call_id)
        if stored is not None:
            if stored != observation:
                raise ContractError(
                    "ActionExecutor._record",
                    f"call {call_id!r} already terminated as {stored.status!r}; "
                    f"refusing to overwrite it with {observation.status!r}",
                )
            return
        self._outcomes[call_id] = observation
        while len(self._outcomes) > self._max_outcomes:
            self._outcomes.popitem(last=False)

    # -- traces ------------------------------------------------------------- #

    def _count(self, name: str) -> None:
        if self._counters is not None:
            self._counters.increment(name)

    async def _emit(self, event_type: str, payload: Mapping[str, Any]) -> None:
        try:
            await _resolved(self._supervision.emit(event_type, payload))
        except Exception:
            # A trace that cannot be published is a lost trace, never a reason
            # to abandon an action that is otherwise proceeding (R8).
            self._count(COUNTER_LOST_TRACES)

    async def _record_and_emit(
        self,
        record: Callable[[], Any],
        event_type: str,
        payload: Mapping[str, Any],
    ) -> None:
        try:
            await _resolved(self._supervision.record_and_emit(record, event_type, payload))
        except Exception:
            # P10 swallows and counts the publication failure itself; this
            # catch covers a supervision that does not, so that a failed trace
            # still cannot propagate back into the owner of the terminal state.
            self._count(COUNTER_LOST_TRACES)

    # -- the call ----------------------------------------------------------- #

    async def invoke(self, call: ActionCall) -> ActionObservation:
        """Run one call to its single terminal observation."""

        if not isinstance(call, ActionCall):
            raise ContractError("ActionExecutor.invoke", "expects an ActionCall")

        recorded = self._outcomes.get(call.call_id)
        if recorded is not None:
            # A caller retrying a call that already terminated — after a lost
            # trace, say — reads the recorded outcome and reaches 0 providers.
            return recorded

        started_at = self._clock()
        spec = self._registry.discovered().get(call.action_name)

        binding: ActionBinding | None = None
        # A failure to reach a provider, resolved here but reported only after
        # authorization and argument validation have had their say: status,
        # code and message.
        pending: tuple[str, str, str] | None = None
        if spec is not None and spec.supports(call.destination):
            try:
                binding = self._registry.resolve(call.action_name, call.destination)
            except AmbiguousBindingError as exc:
                pending = ("error", ERROR_NO_PROVIDER, str(exc))
            else:
                if binding is None:
                    pending = (
                        "error",
                        ERROR_NO_PROVIDER,
                        f"no provider is bound to {call.action_name!r} at {call.destination}",
                    )
                elif not self._registry.is_ready(binding.module):
                    # A bound provider that is not ready — never became ready,
                    # or a remote agent that disconnected mid-run — reads as a
                    # refusal to act, not as a broken action: the model is told
                    # the capability declined, the provider is never entered
                    # (R6, AC35).
                    pending = (
                        "refused",
                        ERROR_PROVIDER_NOT_READY,
                        f"module {binding.module!r} is not ready to provide "
                        f"{call.action_name!r}",
                    )

        await self._emit(
            TRACE_ACTION_STARTED,
            {
                "action": call.action_name,
                "action_version": call.action_version,
                "provider": binding.provider_name if binding is not None else None,
                "call_id": call.call_id,
                "run_id": call.run_id,
                "conversation_id": call.conversation_id,
                "destination": str(call.destination),
                "nature": spec.nature if spec is not None else None,
                "principal": call.principal,
            },
        )

        if spec is None:
            return await self._terminate(
                call, binding, started_at, "error",
                code=ERROR_UNKNOWN_ACTION,
                message=f"action {call.action_name!r} is not declared",
            )
        if call.action_version != spec.version:
            return await self._terminate(
                call, binding, started_at, "error",
                code=ERROR_VERSION_MISMATCH,
                message=(
                    f"call asks for version {call.action_version} of "
                    f"{call.action_name!r}, which is declared at version {spec.version}"
                ),
            )
        if not spec.supports(call.destination):
            return await self._terminate(
                call, binding, started_at, "error",
                code=ERROR_UNSUPPORTED_DESTINATION,
                message=(
                    f"{call.action_name!r} does not support destination {call.destination}"
                ),
            )

        # 1. Authorization, default-deny, re-evaluated on every single call.
        decision = self._authorization.permits(
            spec, principal=call.principal, destination=call.destination
        )
        if not decision.allowed:
            return await self._terminate(
                call, binding, started_at, "refused",
                code=ERROR_NOT_AUTHORIZED,
                message=(
                    f"principal {call.principal!r} is not authorized to run "
                    f"{call.action_name!r} at {call.destination} ({decision.reason})"
                ),
                extra={"authorization_reason": decision.reason},
            )

        # 2. Arguments, before any provider is reached.
        try:
            spec.validate_arguments(call.arguments)
        except ContractError as exc:
            return await self._terminate(
                call, binding, started_at, "error",
                code=ERROR_INVALID_ARGUMENTS,
                message=str(exc),
            )

        if pending is not None:
            status, code, message = pending
            return await self._terminate(
                call, binding, started_at, status, code=code, message=message
            )
        assert binding is not None  # implied by pending being None

        # 3. Provider invocation under the spec's timeout.
        # The clock is read again here, not reused from *started_at*: publishing
        # ``action.started`` and validating the arguments can themselves consume
        # what was left of the deadline, and a budget measured from the entry
        # instant would then let the provider run — and emit — past expiry.
        entered_at = self._clock()
        expires_at = self._expiry(spec, call, entered_at)
        if expires_at <= entered_at:
            # Expired before the provider was entered: nothing can have been
            # emitted, so this is a certain timeout.
            return await self._terminate(
                call, binding, started_at, "timeout",
                code=ERROR_TIMED_OUT,
                message=f"deadline for {call.action_name!r} expired before invocation",
                emission=EMISSION_NOT_EMITTED,
            )
        return await self._run_provider(
            call, spec, binding, started_at, expires_at - entered_at, expires_at
        )

    def _expiry(self, spec: ActionSpec, call: ActionCall, now: float) -> float:
        """The instant this call expires: the earlier of the spec's timeout,
        counted from the provider's door, and the call's own deadline.

        Kept as an absolute instant on the executor's clock, so a provider's
        completion is judged against the limit that actually applied — the
        spec timeout when it is the shorter one, not only the call deadline.
        """

        return min(float(call.deadline), now + float(spec.timeout_seconds))

    async def _cancel(self, task: "asyncio.Future[Any]") -> bool:
        """Cancel *task* and wait at most the grace for it to actually end.

        Cancellation is a request. A provider that swallows ``CancelledError``
        and goes on waiting must not be able to keep ``invoke`` from reaching a
        terminal observation, so the wait is bounded: past the grace the task is
        abandoned — cancelled, and its eventual outcome consumed by a callback —
        and the caller records the outcome regardless. Returns whether the task
        did stop within the grace, for callers that want to say so.
        """

        if task.done():
            _consume(task)
            return True
        task.cancel()
        try:
            done, _pending = await asyncio.wait({task}, timeout=self._cancel_grace)
        except asyncio.CancelledError:
            _detach(task)
            raise
        if task in done:
            _consume(task)
            return True
        _detach(task)
        return False

    async def _run_provider(
        self,
        call: ActionCall,
        spec: ActionSpec,
        binding: ActionBinding,
        started_at: float,
        budget: float,
        expires_at: float,
    ) -> ActionObservation:
        invocation = ActionInvocation(call, spec, binding.provider_name)
        try:
            return await self._run_invocation(
                call, spec, binding, started_at, budget, expires_at, invocation
            )
        finally:
            # Every exit below has recorded the terminal observation and
            # offered ``action.completed``; only now may the provider's
            # deferred facts follow it (AC27).
            invocation._complete()

    async def _run_invocation(
        self,
        call: ActionCall,
        spec: ActionSpec,
        binding: ActionBinding,
        started_at: float,
        budget: float,
        expires_at: float,
        invocation: ActionInvocation,
    ) -> ActionObservation:
        self._provider_invocations += 1
        provider_task = asyncio.ensure_future(self._observe(binding, invocation))
        timer_task = asyncio.ensure_future(self._sleep(budget))

        try:
            done, _pending = await asyncio.wait(
                {provider_task, timer_task}, return_when=asyncio.FIRST_COMPLETED
            )
        except asyncio.CancelledError:
            # Cancelled from the outside: record the terminal state and publish
            # it before letting the cancellation continue, so the run never
            # loses the fact that this call may have emitted something.
            await self._cancel(provider_task)
            await self._cancel(timer_task)
            await self._terminate_uncertain(
                call, binding, started_at, invocation.emission, spec, certain="cancelled"
            )
            raise

        if provider_task in done:
            await self._cancel(timer_task)
            completed_at = invocation.provider_completed_at
            if completed_at is None or completed_at >= expires_at:
                # The result *arrived* after the call expired — the spec's
                # timeout or the call deadline, whichever applied — including
                # the case where both futures were already ready when the wait
                # returned, so the race itself cannot order a late confirmation
                # first. The instant judged is the provider's own completion
                # stamp, not the clock now: a confirmation that landed in time
                # stays a confirmation however late the executor is resumed to
                # adopt it (R5, design §3.3). Whatever a late provider says,
                # even a confirmed success, the call was unconfirmed at expiry
                # (R2/AC33): the result is not adopted, and the interruption is
                # latched through the single emission rule instead.
                _consume(provider_task)
                return await self._terminate_uncertain(
                    call, binding, started_at, invocation.emission, spec, certain="timeout"
                )
            if provider_task.cancelled():
                # The provider was cancelled from below; the executor's own task
                # is untouched, so this is an outcome, not a propagation.
                return await self._terminate_uncertain(
                    call, binding, started_at, invocation.emission, spec, certain="cancelled"
                )
            error = provider_task.exception()
            if error is not None:
                if invocation.emitted:
                    return await self._terminate(
                        call, binding, started_at, "external_unknown",
                        code=ERROR_EXTERNAL_UNKNOWN,
                        message=(
                            f"provider {binding.provider_name!r} failed after signalling "
                            f"emission: {type(error).__name__}: {error}"
                        ),
                        emission=invocation.emission,
                        invoked=True,
                    )
                return await self._terminate(
                    call, binding, started_at, "error",
                    code=ERROR_PROVIDER_FAILED,
                    message=f"{type(error).__name__}: {error}",
                    emission=invocation.emission,
                    invoked=True,
                )
            return await self._validate_observation(
                call, spec, binding, started_at, invocation, provider_task.result()
            )

        # The timer won: cancel the provider and resolve what the effect may be.
        await self._cancel(provider_task)
        return await self._terminate_uncertain(
            call, binding, started_at, invocation.emission, spec, certain="timeout"
        )

    async def _observe(self, binding: ActionBinding, invocation: ActionInvocation) -> Any:
        """Run the provider and stamp the instant its ``invoke`` ended.

        The stamp is taken on the provider's task, in the same step as the
        return, the raise or the cancellation — so it records when the
        confirmation arrived, whatever the loop does to the executor's task
        afterwards.
        """

        try:
            return await binding.provider.invoke(invocation)
        finally:
            invocation._provider_completed_at = self._clock()

    async def _validate_observation(
        self,
        call: ActionCall,
        spec: ActionSpec,
        binding: ActionBinding,
        started_at: float,
        invocation: ActionInvocation,
        observation: Any,
    ) -> ActionObservation:
        """Adopt the provider's observation, or replace it whole.

        The parts are validated first, on every status (R4): a provider's
        ``error`` may carry a text part the model will read, and an
        ``image_ref`` on any status is a lease the store must hold for this
        run. Then a success's result is checked against the spec's schema,
        and an interruption the provider reported itself against the
        emission rule. Whatever fails, nothing of the provider's observation
        survives into the terminal one — no partially adopted parts — and
        every ``image_ref`` it named is discarded from the store at once.
        A rejection replaces the observation, never the emission rule: see
        :meth:`_reject`.
        """

        if not isinstance(observation, ActionObservation):
            return await self._terminate(
                call, binding, started_at, "error",
                code=ERROR_INVALID_OBSERVATION,
                message=(
                    f"provider {binding.provider_name!r} returned "
                    f"{type(observation).__name__}, not an ActionObservation"
                ),
                emission=invocation.emission,
                invoked=True,
            )

        # 4a. Parts: shape, text bound, image leases, observation size.
        rejection = self._reject_parts(call, observation)
        if rejection is not None:
            code, message = rejection
            return await self._reject(
                call, spec, binding, started_at, invocation, observation,
                code=code, message=message,
            )

        if observation.status == "success":
            # 4b. Result validation, before anything is reported as a success.
            try:
                spec.validate_result(observation.result)
            except ContractError as exc:
                return await self._reject(
                    call, spec, binding, started_at, invocation, observation,
                    code=ERROR_INVALID_RESULT, message=str(exc),
                )
        elif observation.status in ("timeout", "cancelled"):
            # An interruption the provider caught itself — its own transport
            # timeout, say — is subject to exactly the same emission rule as one
            # the executor detects. A provider that signalled an emission and
            # then returned a plain ``timeout`` would otherwise slip a
            # possibly-sent write past the uncertainty barrier.
            status, code = _uncertain_status(invocation.emission, spec, observation.status)
            if status != observation.status:
                self._discard_images(observation.parts)
                return await self._terminate(
                    call, binding, started_at, status,
                    code=code,
                    message=(
                        f"provider {binding.provider_name!r} reported "
                        f"{observation.status!r} with emission "
                        f"{invocation.emission!r}; reported as {status!r}"
                    ),
                    emission=invocation.emission,
                    invoked=True,
                )

        provenance = self._provenance(
            call, binding, self._duration(started_at), invocation.emission, invoked=True
        )
        provenance.update(dict(observation.provenance))
        final = replace(observation, provenance=provenance)
        return await self._publish(call, final, started_at)

    async def _reject(
        self,
        call: ActionCall,
        spec: ActionSpec,
        binding: ActionBinding,
        started_at: float,
        invocation: ActionInvocation,
        observation: ActionObservation,
        *,
        code: str,
        message: str,
    ) -> ActionObservation:
        """Replace a provider observation the executor cannot adopt.

        The images it named are discarded and none of its parts survive.
        Its *status*, though, is not simply ``error``: a rejection says the
        observation is unusable, not that the effect did not happen. An
        interruption the provider reported (``timeout``, ``cancelled``) is
        first resolved by the emission rule, exactly as it would be with
        sound parts (4c); when that rule says ``external_unknown`` — an
        emitted write, or a write with emission unknown — the terminal
        status is ``external_unknown``, so a caller never reads an invalid
        observation as licence to retry a write that may already have gone
        out. Every other rejection is ``error`` with the rejection's own
        code (a schema-invalid ``success`` stays ``invalid_result``, AC18);
        the rejection is kept in the message either way.
        """

        self._discard_images(observation.parts)
        status = "error"
        if observation.status in ("timeout", "cancelled"):
            status, _ = _uncertain_status(invocation.emission, spec, observation.status)
        if status == "external_unknown":
            return await self._terminate(
                call, binding, started_at, "external_unknown",
                code=ERROR_EXTERNAL_UNKNOWN,
                message=(
                    f"provider {binding.provider_name!r} reported "
                    f"{observation.status!r} with emission {invocation.emission!r}, "
                    f"and its observation was rejected ({code}: {message}); "
                    "reported as 'external_unknown'"
                ),
                emission=invocation.emission,
                invoked=True,
            )
        return await self._terminate(
            call, binding, started_at, "error",
            code=code,
            message=message,
            emission=invocation.emission,
            invoked=True,
        )

    def _reject_parts(
        self, call: ActionCall, observation: ActionObservation
    ) -> tuple[str, str] | None:
        """Why the observation's parts cannot be adopted, or ``None`` (R4).

        Checked in this order, the first failure deciding the code:

        1. shape — :func:`~core.contracts.validate_parts` again, as a
           provider's observation is not trusted to have kept the contract
           it was constructed under — ``invalid_result``;
        2. the per-part text bound, ``observation_too_large``: a single text
           part above ``max_observation_bytes`` is the same oversize the
           whole-observation rule below reports, one code for one rule;
        3. every ``image_ref`` must be an attachment the store holds, leased
           to this call's ``run_id``, unexpired on the executor clock **now**
           and of the recorded stored size — ``invalid_result`` (AC22, AC47);
           without a store, no image can be validated at all;
        4. the observation's size — text bytes plus image sizes — must not
           exceed ``max_observation_bytes`` — ``observation_too_large``
           (AC47); ``None`` sets no bound.

        An observation carrying no parts has nothing to reject.
        """

        parts = observation.parts
        if not parts:
            return None
        bound = self._max_observation_bytes

        try:
            validate_parts(parts)
        except ContractError as exc:
            return ERROR_INVALID_RESULT, str(exc)
        if bound is not None:
            try:
                validate_parts(parts, max_text_bytes=bound)
            except ContractError as exc:
                return ERROR_OBSERVATION_TOO_LARGE, str(exc)

        now = self._clock()
        for index, part in enumerate(parts):
            if part["type"] != PART_TYPE_IMAGE_REF:
                continue
            label = f"ActionObservation.parts[{index}]"
            attachment_id = part["attachment_id"]
            if self._attachments is None:
                return (
                    ERROR_INVALID_RESULT,
                    f"{label} names attachment {attachment_id!r}, but the executor "
                    "has no attachment store to validate it against",
                )
            ref = self._attachments.lookup(attachment_id)
            if ref is None:
                return (
                    ERROR_INVALID_RESULT,
                    f"{label} names attachment {attachment_id!r}, which the store "
                    "does not hold",
                )
            if ref.run_id != call.run_id:
                return (
                    ERROR_INVALID_RESULT,
                    f"{label} names attachment {attachment_id!r}, which is leased to "
                    f"run {ref.run_id!r}, not to this call's run {call.run_id!r}",
                )
            if now >= ref.expires_at:
                return (
                    ERROR_INVALID_RESULT,
                    f"{label} names attachment {attachment_id!r}, whose lease expired "
                    f"at {ref.expires_at!r} (clock at {now!r})",
                )
            if part["size"] != ref.size:
                return (
                    ERROR_INVALID_RESULT,
                    f"{label}.size is {part['size']}, but the store holds "
                    f"{ref.size} bytes for attachment {attachment_id!r}",
                )

        if bound is not None:
            size = observation_size(parts)
            if size > bound:
                return (
                    ERROR_OBSERVATION_TOO_LARGE,
                    f"observation is {size} bytes of parts, above the bound of {bound}",
                )
        return None

    def _discard_images(self, parts: Sequence[Mapping[str, Any]]) -> None:
        """Drop from the store every attachment the rejected *parts* name.

        A rejected observation adopts nothing, so an image it named would
        otherwise stay leased until the run's terminal record releases it.
        Discarding is idempotent with that release — the store drops an
        object exactly once — and an identifier the store does not hold is
        a no-op, so a lease that was already unknown costs nothing here. The
        identifiers are unguessable, so any one a provider names is one the
        store handed out: dropping it prevents a leak, never reaches bytes
        the provider was not given.
        """

        if self._attachments is None:
            return
        for part in parts:
            if isinstance(part, Mapping) and part.get("type") == PART_TYPE_IMAGE_REF:
                attachment_id = part.get("attachment_id")
                if isinstance(attachment_id, str) and attachment_id.strip():
                    self._attachments.discard(attachment_id)

    async def _terminate_uncertain(
        self,
        call: ActionCall,
        binding: ActionBinding | None,
        started_at: float,
        emission: str,
        spec: ActionSpec,
        *,
        certain: str,
    ) -> ActionObservation:
        """Record an interruption the executor itself detected.

        The status comes from :func:`_uncertain_status`, the single place the
        emission rule lives, so an interruption the *provider* reports is
        resolved exactly the same way.
        """

        status, code = _uncertain_status(emission, spec, certain)
        verb = "timed out" if certain == "timeout" else "was cancelled"
        return await self._terminate(
            call, binding, started_at, status,
            code=code,
            message=(
                f"{call.action_name!r} {verb} with emission {emission!r}; "
                f"reported as {status!r}"
            ),
            emission=emission,
            invoked=True,
        )

    async def _terminate(
        self,
        call: ActionCall,
        binding: ActionBinding | None,
        started_at: float,
        status: str,
        *,
        code: str,
        message: str,
        emission: str = EMISSION_UNKNOWN,
        invoked: bool = False,
        extra: Mapping[str, Any] | None = None,
    ) -> ActionObservation:
        provenance = self._provenance(
            call, binding, self._duration(started_at), emission, invoked=invoked
        )
        if extra:
            provenance.update(dict(extra))
        observation = ActionObservation(
            status=status,
            provenance=provenance,
            error={"code": code, "message": message, "retryable": False},
        )
        return await self._publish(call, observation, started_at)

    async def _publish(
        self, call: ActionCall, observation: ActionObservation, started_at: float
    ) -> ActionObservation:
        """Count, record, then trace — in that order (R8)."""

        if observation.status == "timeout":
            self._count(COUNTER_ACTION_TIMEOUTS)

        # The terminal state is durable here, before the trace exists at all.
        self._record(call.call_id, observation)

        duration = observation.provenance.get("duration_seconds", self._duration(started_at))
        await self._record_and_emit(
            lambda: self._record(call.call_id, observation),
            TRACE_ACTION_COMPLETED,
            {
                "action": call.action_name,
                "action_version": call.action_version,
                "provider": observation.provenance.get("provider"),
                "call_id": call.call_id,
                "run_id": call.run_id,
                "conversation_id": call.conversation_id,
                "destination": str(call.destination),
                "status": observation.status,
                "duration_seconds": duration,
                "error_code": (observation.error or {}).get("code"),
            },
        )
        # The value returned is the value recorded, whatever the trace did.
        return self._outcomes.get(call.call_id, observation)

    def _duration(self, started_at: float) -> float:
        return max(0.0, self._clock() - started_at)

    def _provenance(
        self,
        call: ActionCall,
        binding: ActionBinding | None,
        duration: float,
        emission: str,
        *,
        invoked: bool,
    ) -> dict[str, Any]:
        return {
            "action": call.action_name,
            "action_version": call.action_version,
            "provider": binding.provider_name if binding is not None else None,
            "module": binding.module if binding is not None else None,
            "call_id": call.call_id,
            "run_id": call.run_id,
            "conversation_id": call.conversation_id,
            "destination": str(call.destination),
            "principal": call.principal,
            "duration_seconds": duration,
            "emission": emission,
            "provider_entered": invoked,
        }


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #


async def _resolved(value: Any) -> Any:
    """Await *value* when it is awaitable, so supervision may be either."""

    if inspect.isawaitable(value):
        return await value
    return value


def _uncertain_status(emission: str, spec: ActionSpec, certain: str) -> tuple[str, str]:
    """The status an interrupted call carries, given what the provider signalled.

    An explicit emission decides. When the provider said nothing, a ``read`` has
    no external effect to duplicate and keeps the certain status, while a
    ``write`` becomes ``external_unknown``: reporting a possibly-sent request as
    merely timed out is the false negative that invites a second send.
    """

    if emission == EMISSION_EMITTED:
        return "external_unknown", ERROR_EXTERNAL_UNKNOWN
    if emission == EMISSION_NOT_EMITTED or spec.nature == "read":
        return certain, (ERROR_TIMED_OUT if certain == "timeout" else ERROR_CANCELLED)
    return "external_unknown", ERROR_EXTERNAL_UNKNOWN


def _consume(task: "asyncio.Future[Any]") -> None:
    """Read a finished task's outcome so it is never reported as unretrieved."""

    if not task.cancelled():
        task.exception()


def _detach(task: "asyncio.Future[Any]") -> None:
    """Stop waiting on *task* without leaking whatever it ends up raising.

    Used for a task the executor no longer needs an answer from: it is still
    cancelled, but its result is collected by a callback whenever it finally
    lands, rather than by an await that could never return.
    """

    if task.done():
        _consume(task)
        return
    task.cancel()
    task.add_done_callback(_consume)


def _require_text(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ContractError(field, f"must be a string, got {type(value).__name__}")
    if not value.strip():
        raise ContractError(field, "must not be empty")
    return value


def _texts(values: Any, field: str, *, minimum: int = 0) -> tuple[str, ...]:
    if isinstance(values, str) or not isinstance(values, Sequence):
        raise ContractError(field, "must be a sequence of strings")
    items = tuple(_require_text(value, f"{field}[{index}]") for index, value in enumerate(values))
    if len(items) < minimum:
        raise ContractError(field, f"must carry at least {minimum} entries")
    if len(set(items)) != len(items):
        raise ContractError(field, "must not repeat an entry")
    return items
