"""Declarative trigger registry and deterministic trigger evaluation (R1).

The runtime asks one question at ingestion, after normalisation and after the
chat context has been fed, and before admission, budgets and any model call:
*does this event deserve a run?* This module answers it and nothing else. It
opens no transport, awaits nothing and reads time and randomness only through
injected callables.

Two objects carry the answer.

:class:`TriggerRegistry` holds what modules *declare* and what configuration
*selects*. An input module registers the :class:`~core.contracts.TriggerSpec`
from its manifest — the trigger types it supports with the parameter schema of
each, the combination operators it supports, and exactly one default policy for
its input type. Configuration then selects a policy per input and per channel.
:meth:`TriggerRegistry.validate_policy` runs at startup, before any transport is
opened, and every diagnostic it raises names the input and the offending field
(AC2). **A configured policy replaces the module default entirely**: resolution
returns exactly one policy object and never merges two, so a channel rule can
neither inherit nor weaken what the module declared (R1, AC1).

:class:`TriggerEngine` decides. :meth:`TriggerEngine.evaluate` is deterministic
for the same normalised event, trusted context, configuration, clock and random
source (AC3). Determinism is defined **over the whole event sequence, not per
channel**: one engine draws every probability rule from one random source, so
replaying the same events in the same order reproduces the same decisions,
while replaying a subset, or the same channel's events interleaved differently,
legitimately does not.

Three guarantees are structural rather than conventional.

*Invariants run before rules and no rule can disable them.* Authenticity and
payload validation, anti-echo of the companion's own author identity, and the
exclusion of :data:`~core.contracts.INTERNAL_TRACE_TYPES` are applied in a
separate pass that a policy is never consulted for, so an accept-everything
policy — or an ``audience`` rule that also whitelisted the companion — cannot
let the runtime answer itself (R1, AC5). An internal trace is excluded before
anything else and its decision is not traceable: publishing a rejection about
``action.completed`` would publish a trace about a trace, forever.

*Trusted context alone satisfies a role or subscription predicate.* The
``audience`` evaluator takes the context and never the message text — a narrow
signature, not a convention — and a :class:`TrustedClaim` with no provenance is
retained but never satisfies a predicate, so body text claiming a role changes
no decision (R1, AC4).

*The dedup window is consulted before any draw.* A redelivery of a source event
already decided inside the window returns the recorded decision, having drawn
0 additional values from the random source (AC3). The window is bounded by
entry count and time-to-live, evicts oldest first and counts every eviction, so
the deduplication guarantee covers the retained window only: a repetition
arriving after eviction is decided again (R6, AC22). One clock dates the window
and the same clock reads its age — the injected one, or the override a replay
passes — so a decision can never be recorded in one time domain and expired in
another.

A per-call policy override is held to the same declaration a configured policy
is: it is validated against what the event's own input module declared before
any rule runs, so overriding resolution cannot smuggle in an undeclared trigger
type or widen a schema the module narrowed (R1, AC2).
"""

from __future__ import annotations

import math
import random
import time
import weakref
from collections import OrderedDict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from .contracts import (
    COUNTER_DEDUP_EVICTIONS,
    COUNTER_TRIGGER_REJECTIONS,
    EVENT_KINDS,
    INTERNAL_TRACE_TYPES,
    ContractError,
    Counters,
    TriggerPolicy,
    TriggerRule,
    TriggerSpec,
    TriggerTypeDeclaration,
    validate_against_schema,
    validate_event_kind,
)

__all__ = [
    "AUDIENCE_CLAIMS",
    "BUILTIN_TRIGGER_TYPES",
    "BUILTIN_TRIGGER_TYPE_NAMES",
    "COMPANION_NAME_SETTING",
    "COMPANION_NAME_TOKEN",
    "NORMALIZED_SCHEMA_VERSION",
    "REASON_INTERNAL_TRACE",
    "REASON_INVALID_PAYLOAD",
    "REASON_MAX_LENGTH",
    "REASON_NOT_NORMALIZED",
    "REASON_NO_POLICY",
    "REASON_SELF_AUTHORED",
    "REASON_UNAUTHENTICATED",
    "TRIGGER_TYPE_AUDIENCE",
    "TRIGGER_TYPE_EVENT_KIND",
    "TRIGGER_TYPE_KEYWORD",
    "TRIGGER_TYPE_PROBABILITY",
    "TriggerContext",
    "TriggerDecision",
    "TriggerEngine",
    "TriggerRegistry",
    "TrustedClaim",
    "companion_mention_policy",
    "decision_trace_payload",
]

Clock = Callable[[], float]
RandomSource = Any
"""Anything exposing ``random() -> float`` in ``[0, 1)``.

:class:`random.Random` satisfies it, and so does a test double that hands out a
recorded sequence, which is how AC3 replays a run without sleeping.
"""

NORMALIZED_SCHEMA_VERSION = 2
"""``metadata.schema_version`` of an event the engine will evaluate (R3).

An event that carries anything else was not normalised at the boundary, so its
fields have no agreed meaning and the engine refuses to read them.
"""

TRIGGER_TYPE_PROBABILITY = "probability"
TRIGGER_TYPE_AUDIENCE = "audience"
TRIGGER_TYPE_KEYWORD = "keyword"
TRIGGER_TYPE_EVENT_KIND = "event_kind"

COMPANION_NAME_SETTING = "companion_name"
"""The input module setting :data:`COMPANION_NAME_TOKEN` resolves from.

The name is business configuration of the input that declares the policy, so
it lives in that module's own settings — the loader hands it to the registry
when it records the module's declaration — and never in the core's own
configuration, which stays free of any module's vocabulary (R1, R7).
"""

COMPANION_NAME_TOKEN = "${companion_name}"
"""Placeholder a ``keyword`` rule uses for the configured companion name.

R1 makes the chat input's default policy "the message text mentions the
configured companion name", *with that name coming from configuration*. A
manifest therefore cannot spell the name out: it declares this token, and the
registry resolves it from the name configured for that input, or from its
own ``companion_name`` when the input carries none. A policy using the token
when no name is configured fails validation at startup, naming the input and
the field, rather than silently never matching.
"""

AUDIENCE_CLAIMS: Mapping[str, str | None] = {
    "everyone": None,
    "subscribers": "subscription",
    "moderators": "moderator",
    "vips": "vip",
    "broadcaster": "broadcaster",
}
"""Audience name mapped to the trusted claim that satisfies it.

``everyone`` maps to ``None``: it needs no claim at all. Every other audience
needs a provenance-tagged claim of that name in the trusted context; nothing
else — and in particular nothing in the message body — can satisfy it (AC4).
"""

REASON_MAX_LENGTH = 120
"""Byte-ish cap on a decision reason; a trace carries a token, not a story."""

REASON_INTERNAL_TRACE = "internal_trace"
REASON_INVALID_PAYLOAD = "invalid_payload"
REASON_NOT_NORMALIZED = "not_normalized"
REASON_UNAUTHENTICATED = "unauthenticated"
REASON_SELF_AUTHORED = "self_authored"
REASON_NO_POLICY = "no_policy"

DRAW_ENTRY_KEYS = ("rule", "parameter", "probability", "draw", "ordinal")
"""The closed key set of one :attr:`TriggerDecision.draws` entry (R2).

Five scalars that, with the recorded random source, reproduce one draw of a
declared probability rule. The list is its redaction: no payload field, no
event text and no other parameter is ever part of an entry.
"""

COUNT_WILDCARD_STEPS = "wildcard_steps"
"""The engine count of pattern/text comparisons ``matches`` has taken (A2)."""

_ACCEPTED_PREFIX = "accepted"
_REJECTED_PREFIX = "rejected"
_NO_RULE_MATCHED = "no_rule_matched"


# --------------------------------------------------------------------------- #
# Built-in trigger types (R1)
# --------------------------------------------------------------------------- #

BUILTIN_TRIGGER_TYPES: tuple[TriggerTypeDeclaration, ...] = (
    TriggerTypeDeclaration(
        name=TRIGGER_TYPE_PROBABILITY,
        parameter_schema={
            "type": "object",
            "properties": {
                "probability": {"type": "number", "minimum": 0, "maximum": 1}
            },
            "required": ["probability"],
            "additionalProperties": False,
        },
    ),
    TriggerTypeDeclaration(
        name=TRIGGER_TYPE_AUDIENCE,
        parameter_schema={
            "type": "object",
            "properties": {
                "audience": {"type": "string", "enum": sorted(AUDIENCE_CLAIMS)}
            },
            "required": ["audience"],
            "additionalProperties": False,
        },
    ),
    TriggerTypeDeclaration(
        name=TRIGGER_TYPE_KEYWORD,
        parameter_schema={
            "type": "object",
            "properties": {"keywords": {"type": "array", "items": {"type": "string"}}},
            "required": ["keywords"],
            "additionalProperties": False,
        },
    ),
    TriggerTypeDeclaration(
        name=TRIGGER_TYPE_EVENT_KIND,
        parameter_schema={
            "type": "object",
            "properties": {
                "kinds": {
                    "type": "array",
                    "items": {"type": "string", "enum": list(EVENT_KINDS)},
                }
            },
            "required": ["kinds"],
            "additionalProperties": False,
        },
    ),
)
"""The four types this engine can execute, with their canonical schemas.

The ``event_kind`` schema also carries ``minItems: 1`` and
``uniqueItems: true`` on ``kinds`` (R1). The schema subset of
:func:`~core.contracts.validate_schema` has neither keyword, so both are
enforced by :func:`_validate_builtin_rule`, which every configured and default
policy passes through at registration and again before evaluation.

A module declares the subset it supports in its manifest and may narrow a
schema — a stricter ``maximum`` on a probability, a shorter audience
enumeration — but it may not widen one: a configured rule is checked against
the module's declared schema *and* against the canonical schema here, so a
manifest cannot declare parameters the evaluator would not understand.
"""

_BUILTIN_BY_NAME: Mapping[str, TriggerTypeDeclaration] = {
    declaration.name: declaration for declaration in BUILTIN_TRIGGER_TYPES
}

BUILTIN_TRIGGER_TYPE_NAMES: tuple[str, ...] = tuple(sorted(_BUILTIN_BY_NAME))


def companion_mention_policy() -> TriggerPolicy:
    """Return the per-type default R1 requires of the chat input.

    "The message text mentions the configured companion name", expressed with
    :data:`COMPANION_NAME_TOKEN` so the name itself stays in configuration.
    """

    return TriggerPolicy(
        rules=(
            TriggerRule(
                type=TRIGGER_TYPE_KEYWORD,
                parameters={"keywords": [COMPANION_NAME_TOKEN]},
            ),
        ),
        combination="all_of",
    )


# --------------------------------------------------------------------------- #
# Trusted context (R1, AC4)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class TrustedClaim:
    """One fact about the author, and where that fact came from.

    ``provenance`` names what attested the claim — the platform notification
    field, the API call, the moderation list. It is deliberately optional so an
    untagged claim can be *carried* and still never satisfy a predicate:
    absence of information may not stand in for information (R1, AC4).
    """

    name: str
    provenance: str | None = None
    value: Any = True

    def __post_init__(self) -> None:
        _require_text(self.name, "TrustedClaim.name")
        if self.provenance is not None and not isinstance(self.provenance, str):
            raise ContractError(
                "TrustedClaim.provenance",
                f"must be a string or None, got {type(self.provenance).__name__}",
            )

    @property
    def is_trusted(self) -> bool:
        """Whether this claim may satisfy a predicate.

        It must be provenance-tagged and must not be a negative assertion: a
        claim recorded as ``False`` or ``None`` says the author does *not* hold
        it, and reading its mere presence as satisfaction would invert it.
        """

        if not isinstance(self.provenance, str) or not self.provenance.strip():
            return False
        return self.value is not False and self.value is not None


@dataclass(frozen=True, slots=True)
class TriggerContext:
    """The trusted context an input module offers with an event.

    Only :class:`TrustedClaim` instances are accepted: a bare mapping of roles
    is refused by name, so untagged platform data cannot be mistaken for
    attested facts on the way in.
    """

    claims: tuple[TrustedClaim, ...] = ()

    def __post_init__(self) -> None:
        claims = self.claims
        if isinstance(claims, (str, TrustedClaim)) or not isinstance(claims, Sequence):
            raise ContractError(
                "TriggerContext.claims", "must be a sequence of TrustedClaim"
            )
        claims = tuple(claims)
        for index, claim in enumerate(claims):
            if not isinstance(claim, TrustedClaim):
                raise ContractError(
                    f"TriggerContext.claims[{index}]",
                    "must be a TrustedClaim; an untagged value carries no provenance "
                    "and can never satisfy a predicate",
                )
        object.__setattr__(self, "claims", claims)

    def claim(self, name: str) -> TrustedClaim | None:
        """Return the claim called *name*, tagged or not, or ``None``."""

        for claim in self.claims:
            if claim.name == name:
                return claim
        return None

    def satisfies(self, name: str) -> bool:
        """Whether a provenance-tagged claim called *name* is present."""

        claim = self.claim(name)
        return claim is not None and claim.is_trusted


_EMPTY_CONTEXT = TriggerContext()


# --------------------------------------------------------------------------- #
# Decision (R1, R8)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class TriggerDecision:
    """What the engine decided about one source event.

    It carries ``accepted``, the bounded ``reason``, the ``policy_version`` that
    decided and the ``source_event_id`` it decided about, plus the correlation
    pair ``(platform, channel_id)`` and the input name. **It carries no
    ``run_id`` and must never grow one** (R8, AC27): a trigger decision is taken
    before admission, so at the moment it exists no run exists to name, and a
    field here would invite the ingestion path to invent one.

    ``draws`` holds one entry per evaluated declared rule with a
    ``probability_parameter``, in policy order, each exactly the five scalars
    of :data:`DRAW_ENTRY_KEYS` (R2). Every generic-only policy — the generic
    ``probability`` type included — leaves it ``()``.
    """

    accepted: bool
    reason: str
    policy_version: str | None
    source_event_id: str | None
    input_name: str | None = None
    platform: str | None = None
    channel_id: str | None = None
    draws: tuple[Mapping[str, Any], ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.accepted, bool):
            raise ContractError(
                "TriggerDecision.accepted",
                f"must be a boolean, got {type(self.accepted).__name__}",
            )
        _require_text(self.reason, "TriggerDecision.reason")
        if len(self.reason) > REASON_MAX_LENGTH:
            raise ContractError(
                "TriggerDecision.reason",
                f"must be at most {REASON_MAX_LENGTH} characters",
            )
        for value, field_name in (
            (self.policy_version, "TriggerDecision.policy_version"),
            (self.source_event_id, "TriggerDecision.source_event_id"),
            (self.input_name, "TriggerDecision.input_name"),
            (self.platform, "TriggerDecision.platform"),
            (self.channel_id, "TriggerDecision.channel_id"),
        ):
            if value is not None and not isinstance(value, str):
                raise ContractError(
                    field_name, f"must be a string or None, got {type(value).__name__}"
                )
        object.__setattr__(self, "draws", _validated_draws(self.draws))

    @property
    def traceable(self) -> bool:
        """Whether an ``input.trigger.*`` fact may be published for this.

        False for an internal trace event only. The runtime publishes its own
        supervision facts on the same bus it ingests from; announcing that a
        trace was not trigger-evaluated would itself be a publication, which
        would be evaluated, announced, and so on without end.
        """

        return self.reason != REASON_INTERNAL_TRACE


# --------------------------------------------------------------------------- #
# Registry (R1, AC1, AC2)
# --------------------------------------------------------------------------- #


class TriggerRegistry:
    """What modules declare and what configuration selects, per input.

    Fed at load time from module manifests, queried at startup by
    :meth:`validate_policy` before any transport is opened, and read at
    ingestion by :class:`TriggerEngine`.
    """

    __slots__ = (
        "_companion_name",
        "_names",
        "_specs",
        "_inputs",
        "_channels",
        "_hooks",
    )

    def __init__(self, *, companion_name: str | None = None) -> None:
        """Build a registry resolving :data:`COMPANION_NAME_TOKEN` to *companion_name*.

        The name is configuration, not a module constant, so it lives here
        rather than in any manifest (R1). This one is the registry-wide
        fallback; an input registered with its own configured name uses that
        name instead.
        """

        if companion_name is not None:
            _require_text(companion_name, COMPANION_NAME_SETTING)
        self._companion_name = companion_name
        self._names: dict[str, str] = {}
        self._specs: dict[str, TriggerSpec] = {}
        self._inputs: dict[str, TriggerPolicy] = {}
        self._channels: dict[tuple[str, str], TriggerPolicy] = {}
        # Per input, one slot per hooked type it declared; a slot holds the
        # hook its own module supplied, ``None`` until one is.
        self._hooks: dict[str, dict[str, Any]] = {}

    @property
    def companion_name(self) -> str | None:
        """The registry-wide companion name, or ``None`` when unset."""

        return self._companion_name

    def companion_name_for(self, input_name: str) -> str | None:
        """The name :data:`COMPANION_NAME_TOKEN` resolves to for *input_name*.

        The name the input was registered with wins over the registry-wide
        one; ``None`` means no name is configured anywhere for this input.
        """

        return self._names.get(input_name, self._companion_name)

    def register(
        self,
        input_name: str,
        spec: TriggerSpec,
        *,
        companion_name: str | None = None,
    ) -> None:
        """Record the :class:`~core.contracts.TriggerSpec` *input_name* declares.

        Every declared type must be one this engine can execute — a generic
        type, or a type the module declares with its own match rule or hook
        (R1) — and every generic schema must be one the matching evaluator
        understands, both checked here so a manifest that could only fail at
        ingestion fails at load instead. The module's own default policy is
        validated the same way as a configured one. *companion_name* is the name configured in this
        input's own settings; it resolves the token for this input only.
        """

        name = _require_text(input_name, "input_name")
        if not isinstance(spec, TriggerSpec):
            raise ContractError(
                f"triggers.{name}", f"must be a TriggerSpec, got {type(spec).__name__}"
            )
        if name in self._specs:
            raise ContractError(f"triggers.{name}", "is already declared by a module")
        if companion_name is not None:
            _require_text(companion_name, f"triggers.{name}.{COMPANION_NAME_SETTING}")

        for index, declaration in enumerate(spec.types):
            if declaration.declared:
                # Its shape was checked when it was constructed (R1); a
                # generic name never carries the declared keys.
                continue
            if declaration.name not in _BUILTIN_BY_NAME:
                supported = ", ".join(BUILTIN_TRIGGER_TYPE_NAMES)
                raise ContractError(
                    f"triggers.{name}.types[{index}].name",
                    f"is not a trigger type this runtime can evaluate "
                    f"({declaration.name!r}); supported: {supported}",
                )

        if spec.default_policy is not None:
            self._validate_executable(
                spec.default_policy,
                spec=spec,
                label=f"triggers.{name}.default_policy",
                companion_name=(
                    self._companion_name if companion_name is None else companion_name
                ),
            )

        self._specs[name] = spec
        self._hooks[name] = {
            declaration.name: None for declaration in spec.types if declaration.hook
        }
        if companion_name is not None:
            self._names[name] = companion_name

    def hook_slots(self, input_name: str) -> Mapping[str, Any]:
        """Return *input_name*'s hook slot table, read-only.

        One slot per hooked type the input declared, holding the hook its own
        module supplied or ``None``; empty for an unregistered input.
        """

        return MappingProxyType(self._hooks.get(input_name, {}))

    def spec(self, input_name: str) -> TriggerSpec | None:
        """Return the spec declared for *input_name*, or ``None``."""

        return self._specs.get(input_name)

    def inputs(self) -> tuple[str, ...]:
        """Return the declared input names, in declaration order."""

        return tuple(self._specs)

    def validate_policy(self, input_name: str, policy: Any) -> None:
        """Raise unless *input_name* can honour *policy* (R1, AC2).

        Called at startup, before any transport is opened. Every diagnostic
        names the input and the offending field. Rejected: a type the module
        does not declare, a parameter failing the declared schema, a
        combination operator the module does not declare, and any policy at all
        on a module declaring zero trigger types.
        """

        name = _require_text(input_name, "input_name")
        label = f"triggers.{name}.policy"
        spec = self._specs.get(name)
        if spec is None:
            declared = ", ".join(self._specs) or "none"
            raise ContractError(
                label,
                f"names an input no module declares; declared inputs: {declared}",
            )
        try:
            spec.validate_policy(policy, label=label)
        except ContractError as error:
            raise _value_free(error, policy, spec=spec, label=label) from None
        self._validate_executable(
            policy, spec=spec, label=label, companion_name=self.companion_name_for(name)
        )

    def configure(
        self, input_name: str, policy: TriggerPolicy, *, channel_id: str | None = None
    ) -> None:
        """Select *policy* for one input, or for one channel of that input.

        The policy is validated first, so an invalid configuration never
        reaches resolution. A configured policy **replaces** the module default
        entirely; nothing here merges rules (R1, AC1).
        """

        name = _require_text(input_name, "input_name")
        self.validate_policy(name, policy)
        if channel_id is None:
            self._inputs[name] = policy
            return
        channel = _require_text(channel_id, f"triggers.{name}.channel_id")
        self._channels[(name, channel)] = policy

    def resolve(
        self, input_name: str, channel_id: str | None = None
    ) -> TriggerPolicy | None:
        """Return the one policy that decides for this input and channel.

        Most specific first — channel, then input, then the module default —
        and exactly one of them is returned. Two policies are never combined,
        so an explicit rule cannot be silently widened by the default it
        replaced (R1, AC1). ``None`` means no policy applies at all, which the
        engine turns into a rejection rather than an acceptance.
        """

        if channel_id is not None:
            configured = self._channels.get((input_name, channel_id))
            if configured is not None:
                return configured
        configured = self._inputs.get(input_name)
        if configured is not None:
            return configured
        spec = self._specs.get(input_name)
        return None if spec is None else spec.default_policy

    @staticmethod
    def _validate_executable(
        policy: TriggerPolicy,
        *,
        spec: TriggerSpec,
        label: str,
        companion_name: str | None,
    ) -> None:
        """Check every rule against what the kernel can evaluate for it.

        A rule naming a type *spec* declares is held to the kernel's checks of
        a declared type; any other rule to the canonical schema of its
        built-in type, exactly as before.
        """

        for index, rule in enumerate(policy.rules):
            location = f"{label}.rules[{index}]"
            declaration = spec.declaration(rule.type)
            if declaration is not None and declaration.declared:
                _validate_declared_rule(rule, declaration, label=location)
                continue
            _validate_builtin_rule(
                rule,
                companion_name=companion_name,
                label=location,
            )


# --------------------------------------------------------------------------- #
# Engine (R1, R6, AC3, AC5, AC22)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class _Normalized:
    """The fields the engine is allowed to read, once invariants have passed."""

    input_name: str
    platform: str
    channel_id: str
    author_id: str
    message_id: str
    text: str
    kind: str
    payload: Mapping[str, Any]

    def field(self, path: str) -> tuple[bool, Any]:
        """Return ``(present, value)`` for a dotted ``field`` path (R2).

        Read-only: the walk only reads mappings. ``kind`` reads the
        normalised kind, so an absent ``payload.kind`` reads as ``message``
        exactly as every other evaluator sees it.
        """

        if path == "kind":
            return True, self.kind
        node: Any = self.payload
        for segment in path.split("."):
            if not isinstance(node, Mapping) or segment not in node:
                return False, None
            node = node[segment]
        return True, node


class _Source:
    """The engine's random source, counting the values it has served.

    ``served`` is the 0-based ordinal of the next value, which is what a
    :attr:`TriggerDecision.draws` entry records so the recorded source
    reproduces the draw (R2).
    """

    __slots__ = ("_rng", "served")

    def __init__(self, rng: RandomSource | weakref.ref[RandomSource]) -> None:
        # Either the source itself, or a weak reference to it when the engine
        # must not keep an override alive (it is called while the caller still
        # holds the object, so the reference is live whenever it is drawn on).
        self._rng = rng
        self.served = 0

    @property
    def rng(self) -> RandomSource | None:
        if isinstance(self._rng, weakref.ref):
            return self._rng()
        return self._rng

    def random(self) -> float:
        rng = self.rng
        assert rng is not None, "a weakly held source is drawn on only while alive"
        draw = rng.random()
        self.served += 1
        return draw


@dataclass(frozen=True, slots=True)
class _Outcome:
    """One rule's verdict and the short token explaining it."""

    matched: bool
    reason: str


@dataclass(frozen=True, slots=True)
class _Recorded:
    """A decision kept for the dedup window.

    Dated by the clock of the call that recorded it, which is the clock every
    later reading of its age is taken from.
    """

    decision: TriggerDecision
    timestamp: float


class _InvariantRejection(Exception):
    """A system invariant refused the event; no rule will be consulted."""

    def __init__(
        self,
        reason: str,
        *,
        input_name: str | None = None,
        platform: str | None = None,
        channel_id: str | None = None,
        source_event_id: str | None = None,
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.input_name = input_name
        self.platform = platform
        self.channel_id = channel_id
        self.source_event_id = source_event_id


class TriggerEngine:
    """Decide, deterministically and once, whether an event deserves a run."""

    __slots__ = (
        "_registry",
        "_companion_identities",
        "_clock",
        "_rng",
        "_counters",
        "_dedup_max_entries",
        "_dedup_ttl_seconds",
        "_window",
        "_window_clock",
        "_rule_evaluations",
        "_source",
        "_override_sources",
        "_counts",
    )

    def __init__(
        self,
        registry: TriggerRegistry,
        *,
        dedup_max_entries: int,
        dedup_ttl_seconds: float,
        companion_identity: str | Sequence[str] | None = None,
        clock: Clock = time.monotonic,
        rng: RandomSource | None = None,
        counters: Counters | None = None,
    ) -> None:
        """Build an engine over *registry* with a bounded decision window.

        ``dedup_max_entries`` and ``dedup_ttl_seconds`` are required and
        validated as finite positive values, naming the setting when they are
        not, so ``core.main`` can refuse the configuration at startup (R6).
        ``companion_identity`` is the author identity — or identities — the
        anti-echo invariant refuses; ``clock`` and ``rng`` are injected so a
        test never sleeps and a replay never depends on wall time.
        """

        if not isinstance(registry, TriggerRegistry):
            raise ContractError(
                "registry", f"must be a TriggerRegistry, got {type(registry).__name__}"
            )
        self._registry = registry
        self._companion_identities = _companion_identities(companion_identity)
        self._dedup_max_entries = _validate_count_limit(
            dedup_max_entries, "dedup_max_entries"
        )
        self._dedup_ttl_seconds = _validate_duration_limit(
            dedup_ttl_seconds, "dedup_ttl_seconds"
        )
        self._clock = _validated_clock(clock, "clock")
        self._rng = _validated_rng(random.Random() if rng is None else rng, "rng")
        if counters is not None and not isinstance(counters, Counters):
            raise ContractError(
                "counters", f"must be a Counters, got {type(counters).__name__}"
            )
        self._counters = Counters() if counters is None else counters

        # Insertion-ordered, so the oldest entry is the front and eviction is
        # oldest-first on both axes.
        self._window: OrderedDict[tuple[str, str, str], _Recorded] = OrderedDict()
        # The clock the retained timestamps are dated by. It is the injected
        # clock until a call overrides it, and every reading of the window's
        # age — eviction and :meth:`recorded` alike — is taken from it, so a
        # replay cannot record in one time domain and expire in another.
        self._window_clock: Clock = self._clock
        self._rule_evaluations = 0
        # Values served by the injected source, and by every override source a
        # call has passed, each counted from its own zero (R2). Overrides are
        # keyed by identity and kept for as long as the object lives, so
        # alternating between two overrides never restarts either count.
        self._source = _Source(self._rng)
        self._override_sources: dict[int, _Source] = {}
        self._counts: dict[str, int] = {COUNT_WILDCARD_STEPS: 0}

    @property
    def registry(self) -> TriggerRegistry:
        return self._registry

    @property
    def counters(self) -> Counters:
        return self._counters

    @property
    def rule_evaluations(self) -> int:
        """How many individual rules this engine has evaluated.

        AC5 asserts this stays at 0 for the companion's own message and for an
        internal trace, whatever the policy says.
        """

        return self._rule_evaluations

    @property
    def counts(self) -> Mapping[str, int]:
        """The engine's own counts, read-only: ``wildcard_steps`` (A2, AC36)."""

        return MappingProxyType(self._counts)

    @property
    def window_size(self) -> int:
        """Decisions currently retained in the dedup window."""

        return len(self._window)

    def add_companion_identities(self, identities: Iterable[str]) -> None:
        """Add *identities* to those the anti-echo invariant refuses.

        A module that learns the companion's own account after construction —
        an input it relays rather than one the engine was built for — calls
        this before any evaluation. Each identity is validated and case-folded
        like the constructor's; the set only grows, so adding an identity
        already present changes nothing. Every identity is validated before
        any is added: a refused call leaves the set as it was. A bare string
        is refused rather than read as a sequence of one-character identities.
        """

        if isinstance(identities, str) or not isinstance(identities, Iterable):
            raise ContractError(
                "companion_identities",
                f"must be an iterable of strings, got {type(identities).__name__}",
            )
        added = frozenset(
            _require_text(identity, f"companion_identities[{index}]").casefold()
            for index, identity in enumerate(identities)
        )
        self._companion_identities = self._companion_identities | added

    def evaluate(
        self,
        event: Mapping[str, Any],
        *,
        context: TriggerContext | None = None,
        policy: TriggerPolicy | None = None,
        clock: Clock | None = None,
        rng: RandomSource | None = None,
    ) -> TriggerDecision:
        """Return the decision for *event*, deterministic for equal inputs (AC3).

        The order is fixed and not negotiable by configuration:

        1. system invariants — internal trace exclusion, payload validation and
           authenticity, then anti-echo of the companion's own identity;
        2. the dedup window, consulted **before** any rule, so a redelivery
           inside the window reuses the recorded decision and draws nothing;
        3. the one resolved policy, every rule of it evaluated.

        *policy* overrides resolution for this call, and *clock* and *rng*
        override the injected ones, so a caller can replay a recorded sequence
        without rebuilding the engine. An override is not a way around AC2: it
        is validated against what the event's own input module declared,
        exactly as a configured policy is, and raises
        :class:`~core.contracts.ContractError` naming the input and the
        offending field when the module could not honour it. Overriding *clock*
        re-bases the dedup window on that clock, and :meth:`recorded` then
        reads expiry from the same clock, so the two never disagree about what
        the window still holds. Rules are evaluated without short-circuiting:
        the number of draws taken from the random source depends on the policy
        alone, never on the content of the event.
        """

        clock = self._clock if clock is None else _validated_clock(clock, "clock")
        source = self._source_for(
            None if rng is None else _validated_rng(rng, "rng")
        )
        if context is None:
            context = _EMPTY_CONTEXT
        elif not isinstance(context, TriggerContext):
            raise ContractError(
                "context", f"must be a TriggerContext, got {type(context).__name__}"
            )

        try:
            normalized = _normalize(event)
        except _InvariantRejection as rejection:
            return self._reject_by_invariant(rejection)

        if self._is_companion(normalized.author_id):
            return self._reject_by_invariant(
                _InvariantRejection(
                    REASON_SELF_AUTHORED,
                    input_name=normalized.input_name,
                    platform=normalized.platform,
                    channel_id=normalized.channel_id,
                    source_event_id=normalized.message_id,
                )
            )

        if policy is not None:
            # An override decides for a real input, so it is held to exactly
            # what that input's module declared (R1, AC2). Rule evaluation
            # below checks only the canonical built-in schemas, so without
            # this a per-call policy could use a type the module never
            # declared, exceed the narrower parameter bounds of its declared
            # schema, combine with an operator the module does not support, or
            # decide at all for an input declaring zero trigger types. Checked
            # before the dedup window so an unhonourable override is refused
            # whatever the window happens to hold.
            self._registry.validate_policy(normalized.input_name, policy)

        # This call ages and dates the window, so the window's time domain is
        # this call's clock from here on: eviction below and every later
        # reading of expiry, :meth:`recorded` included, come from the same
        # source and cannot disagree.
        self._window_clock = clock
        now = clock()
        key = (normalized.platform, normalized.channel_id, normalized.message_id)
        recorded = self._recall(key, now)
        if recorded is not None:
            # Strictly before evaluation: the recorded decision is reproduced
            # exactly and the random source is not touched (AC3).
            return recorded

        resolved = (
            policy
            if policy is not None
            else self._registry.resolve(normalized.input_name, normalized.channel_id)
        )
        if resolved is None:
            decision = TriggerDecision(
                accepted=False,
                reason=REASON_NO_POLICY,
                policy_version=None,
                source_event_id=normalized.message_id,
                input_name=normalized.input_name,
                platform=normalized.platform,
                channel_id=normalized.channel_id,
            )
        else:
            if not isinstance(resolved, TriggerPolicy):
                raise ContractError(
                    "policy", f"must be a TriggerPolicy, got {type(resolved).__name__}"
                )
            accepted, token, draws = self._apply(
                resolved, normalized=normalized, context=context, source=source
            )
            prefix = _ACCEPTED_PREFIX if accepted else _REJECTED_PREFIX
            decision = TriggerDecision(
                accepted=accepted,
                reason=_bounded_reason(f"{prefix}:{token}"),
                policy_version=resolved.version,
                source_event_id=normalized.message_id,
                input_name=normalized.input_name,
                platform=normalized.platform,
                channel_id=normalized.channel_id,
                draws=draws,
            )

        self._record(key, decision, now)
        self._count(decision)
        return decision

    def recorded(
        self,
        *,
        platform: str,
        channel_id: str,
        source_event_id: str,
        clock: Clock | None = None,
    ) -> TriggerDecision | None:
        """Return the decision retained for one source event, or ``None``.

        Read-only: an entry past its time-to-live reads as absent here but is
        evicted, and counted, only by an evaluation.

        Expiry is read from the clock the window is currently dated by — the
        injected one until a call to :meth:`evaluate` overrode it, that
        override afterwards — so a decision just taken through a replay clock
        never reads as absent here merely because the engine's own clock sits
        elsewhere. *clock* names the domain explicitly instead, for a caller
        holding several.
        """

        key = (
            _require_text(platform, "platform"),
            _require_text(channel_id, "channel_id"),
            _require_text(source_event_id, "source_event_id"),
        )
        entry = self._window.get(key)
        if entry is None:
            return None
        reader = (
            self._window_clock if clock is None else _validated_clock(clock, "clock")
        )
        if entry.timestamp < reader() - self._dedup_ttl_seconds:
            return None
        return entry.decision

    def _source_for(self, rng: RandomSource | None) -> _Source:
        """Return the counting source a call draws from.

        The injected source keeps one count for the engine's life; an override
        counts from its own zero, and keeps counting whenever a later call
        passes the same object, whatever other sources were used in between
        (R2). Passing the injected source itself shares its count.

        An override's count is dropped when the object is garbage-collected,
        so a fresh object reusing its ``id`` starts from zero; an object that
        cannot be weakly referenced is retained for the engine's life instead,
        since forgetting it would let its ``id`` be reused under a stale count.
        """

        if rng is None or rng is self._rng:
            return self._source
        key = id(rng)
        source = self._override_sources.get(key)
        if source is not None and source.rng is rng:
            return source
        try:
            # The entry must not keep the object alive, or it would never be
            # collected and its entry never dropped.
            source = _Source(weakref.ref(rng))
        except TypeError:
            source = _Source(rng)
        else:
            weakref.finalize(rng, self._override_sources.pop, key, None)
        self._override_sources[key] = source
        return source

    def _apply(
        self,
        policy: TriggerPolicy,
        *,
        normalized: _Normalized,
        context: TriggerContext,
        source: _Source,
    ) -> tuple[bool, str, tuple[Mapping[str, Any], ...]]:
        """Evaluate every rule of *policy* and combine the outcomes.

        Also returns the ``draws`` entries of its declared probability rules,
        in policy order.
        """

        spec = self._registry.spec(normalized.input_name)
        companion_name = self._registry.companion_name_for(normalized.input_name)
        outcomes: list[_Outcome] = []
        draws: list[Mapping[str, Any]] = []
        for index, rule in enumerate(policy.rules):
            label = f"policy.rules[{index}]"
            self._rule_evaluations += 1
            declaration = None if spec is None else spec.declaration(rule.type)
            outcome, steps, draw = _evaluate_rule(
                rule,
                declaration=declaration,
                normalized=normalized,
                context=context,
                source=source,
                companion_name=companion_name,
                index=index,
                label=label,
            )
            self._counts[COUNT_WILDCARD_STEPS] += steps
            if draw is not None:
                draws.append(draw)
            outcomes.append(outcome)
        accepted, token = _combine(policy.combination, outcomes)
        return accepted, token, tuple(draws)

    def _is_companion(self, author_id: str) -> bool:
        """Whether *author_id* is the companion's own identity (anti-echo).

        Compared case-insensitively: a platform identity may reach the runtime
        as a numeric id in one payload and as a login in another, and an echo
        that differs only by case is still an echo.
        """

        return author_id.casefold() in self._companion_identities

    def _reject_by_invariant(self, rejection: _InvariantRejection) -> TriggerDecision:
        """Turn an invariant failure into a decision, consulting no rule.

        Not recorded in the dedup window: an invariant is deterministic and
        costs no draw, so replaying the event re-derives the same answer, and
        recording it would let a flood of malformed events evict the decisions
        the window exists for.
        """

        decision = TriggerDecision(
            accepted=False,
            reason=_bounded_reason(rejection.reason),
            policy_version=None,
            source_event_id=rejection.source_event_id,
            input_name=rejection.input_name,
            platform=rejection.platform,
            channel_id=rejection.channel_id,
        )
        self._count(decision)
        return decision

    def _count(self, decision: TriggerDecision) -> None:
        """Count a rejection once, and only one a trace may report."""

        if not decision.accepted and decision.traceable:
            self._counters.increment(COUNTER_TRIGGER_REJECTIONS)

    def _recall(
        self, key: tuple[str, str, str], now: float
    ) -> TriggerDecision | None:
        """Return the retained decision for *key*, evicting what has expired."""

        self._evict_expired(now)
        entry = self._window.get(key)
        return None if entry is None else entry.decision

    def _record(
        self, key: tuple[str, str, str], decision: TriggerDecision, now: float
    ) -> None:
        """Retain *decision* and evict until the window bounds hold."""

        self._window[key] = _Recorded(decision=decision, timestamp=now)
        self._window.move_to_end(key)
        self._evict_expired(now)
        while len(self._window) > self._dedup_max_entries:
            self._window.popitem(last=False)
            self._counters.increment(COUNTER_DEDUP_EVICTIONS)

    def _evict_expired(self, now: float) -> None:
        """Drop entries older than the time-to-live, oldest first."""

        horizon = now - self._dedup_ttl_seconds
        window = self._window
        while window:
            key = next(iter(window))
            if window[key].timestamp >= horizon:
                return
            del window[key]
            self._counters.increment(COUNTER_DEDUP_EVICTIONS)


# --------------------------------------------------------------------------- #
# System invariants (R1, AC5)
# --------------------------------------------------------------------------- #


def _normalize(event: Any) -> _Normalized:
    """Return the readable fields of a normalised event, or raise.

    Applied before any rule and reachable by no configuration: an internal
    trace is excluded first, then the envelope must be a schema-version-2
    normalisation carrying a trusted author identity. Anything else is refused
    with a reason naming what was wrong, never evaluated on the assumption that
    a missing field means "no".
    """

    if not isinstance(event, Mapping):
        raise _InvariantRejection(f"{REASON_INVALID_PAYLOAD}:event")

    event_type = event.get("type")
    if not isinstance(event_type, str) or not event_type.strip():
        raise _InvariantRejection(f"{REASON_INVALID_PAYLOAD}:type")
    if event_type in INTERNAL_TRACE_TYPES:
        raise _InvariantRejection(REASON_INTERNAL_TRACE)

    metadata = event.get("metadata")
    payload = event.get("payload")
    if not isinstance(metadata, Mapping):
        raise _InvariantRejection(f"{REASON_INVALID_PAYLOAD}:metadata")
    if not isinstance(payload, Mapping):
        raise _InvariantRejection(f"{REASON_INVALID_PAYLOAD}:payload")

    schema_version = metadata.get("schema_version")
    if (
        isinstance(schema_version, bool)
        or not isinstance(schema_version, int)
        or schema_version != NORMALIZED_SCHEMA_VERSION
    ):
        raise _InvariantRejection(f"{REASON_NOT_NORMALIZED}:metadata.schema_version")

    input_name = _optional_text(metadata.get("source"))
    if input_name is None:
        raise _InvariantRejection(f"{REASON_INVALID_PAYLOAD}:metadata.source")

    # Checked one at a time so each rejection carries every identifier that was
    # already established, and so the fields below are known non-empty.
    platform = _optional_text(payload.get("platform"))
    if platform is None:
        raise _InvariantRejection(
            f"{REASON_INVALID_PAYLOAD}:payload.platform", input_name=input_name
        )
    channel_id = _optional_text(payload.get("channel_id"))
    if channel_id is None:
        raise _InvariantRejection(
            f"{REASON_INVALID_PAYLOAD}:payload.channel_id",
            input_name=input_name,
            platform=platform,
        )
    message_id = _optional_text(payload.get("message_id"))
    if message_id is None:
        raise _InvariantRejection(
            f"{REASON_INVALID_PAYLOAD}:payload.message_id",
            input_name=input_name,
            platform=platform,
            channel_id=channel_id,
        )

    author = payload.get("author")
    author_id = (
        _optional_text(author.get("id")) if isinstance(author, Mapping) else None
    )
    if author_id is None:
        # Authenticity, not shape: without a trusted author identity there is
        # nobody to attribute the message to and no session to key a run by.
        raise _InvariantRejection(
            REASON_UNAUTHENTICATED,
            input_name=input_name,
            platform=platform,
            channel_id=channel_id,
            source_event_id=message_id,
        )

    text = payload.get("text")
    if not isinstance(text, str):
        raise _InvariantRejection(
            f"{REASON_INVALID_PAYLOAD}:payload.text",
            input_name=input_name,
            platform=platform,
            channel_id=channel_id,
            source_event_id=message_id,
        )

    # An absent kind is an ordinary message (every phase-2 event); anything
    # outside the closed vocabulary is refused, never read as "no kind".
    try:
        kind = validate_event_kind(payload.get("kind"), "payload.kind")
    except ContractError:
        raise _InvariantRejection(
            f"{REASON_INVALID_PAYLOAD}:payload.kind",
            input_name=input_name,
            platform=platform,
            channel_id=channel_id,
            source_event_id=message_id,
        ) from None

    return _Normalized(
        input_name=input_name,
        platform=platform,
        channel_id=channel_id,
        author_id=author_id,
        message_id=message_id,
        text=text,
        kind=kind,
        payload=MappingProxyType(payload),
    )


# --------------------------------------------------------------------------- #
# Rule evaluation (R1, AC3, AC4)
# --------------------------------------------------------------------------- #


def _evaluate_rule(
    rule: TriggerRule,
    *,
    declaration: TriggerTypeDeclaration | None,
    normalized: _Normalized,
    context: TriggerContext,
    source: _Source,
    companion_name: str | None,
    index: int,
    label: str,
) -> tuple[_Outcome, int, Mapping[str, Any] | None]:
    """Dispatch one rule to its evaluator.

    Returns the outcome, the wildcard steps it took and its ``draws`` entry,
    if any. A rule naming a type its input declared goes to
    :func:`_evaluate_declared`; any other to its built-in evaluator.

    Dispatch is explicit rather than table-driven so each evaluator can take
    exactly what it is allowed to read: ``_evaluate_audience`` is never handed
    the message text, which is what makes "body text never satisfies a role
    predicate" a property of the signature instead of a promise (AC4), and
    ``_evaluate_event_kind`` is handed neither the text nor the random source,
    so it can neither read the body nor shift a draw count.
    """

    if declaration is not None and declaration.declared:
        _validate_declared_rule(rule, declaration, label=label)
        return _evaluate_declared(
            declaration, rule.parameters, normalized, source, index, label=label
        )
    _validate_builtin_rule(rule, companion_name=companion_name, label=label)
    parameters = rule.parameters
    if rule.type == TRIGGER_TYPE_PROBABILITY:
        return _evaluate_probability(parameters, source), 0, None
    if rule.type == TRIGGER_TYPE_AUDIENCE:
        return _evaluate_audience(parameters, context), 0, None
    if rule.type == TRIGGER_TYPE_KEYWORD:
        outcome = _evaluate_keyword(
            parameters, normalized.text, companion_name, label=label
        )
        return outcome, 0, None
    if rule.type == TRIGGER_TYPE_EVENT_KIND:
        return _evaluate_event_kind(parameters, normalized.kind), 0, None
    # Unreachable while every built-in name has a branch above; a built-in
    # added without one must fail loudly rather than decide by default.
    raise ContractError(
        f"{label}.type", f"is declared but has no evaluator ({rule.type!r})"
    )


def _evaluate_declared(
    declaration: TriggerTypeDeclaration,
    parameters: Mapping[str, Any],
    normalized: _Normalized,
    source: _Source,
    index: int,
    *,
    label: str,
) -> tuple[_Outcome, int, Mapping[str, Any] | None]:
    """Evaluate a declared type's rule over the normalised event (R2).

    Runs no module code and opens no transport: the frozen ``match`` tree is
    read over the payload and the configured parameters, nothing else. A
    kind outside ``kinds`` does not match. When the type declares a
    ``probability_parameter``, exactly one value is drawn per evaluation —
    after the tree, before the combination, whatever the tree or the kind
    decided — so the number of draws depends on the policy alone; the rule
    then matches only when the tree matched and the draw is below the
    configured probability.
    """

    name = declaration.name
    if declaration.match is None:
        # A hooked type is evaluated only where its hook can be bounded.
        raise ContractError(
            f"{label}.type", "is a hooked trigger type this path cannot evaluate"
        )
    kind_matches = normalized.kind in declaration.kinds
    matched, steps = (
        _evaluate_tree(declaration.match, parameters, normalized)
        if kind_matches
        else (False, 0)
    )

    entry: Mapping[str, Any] | None = None
    missed = False
    parameter = declaration.probability_parameter
    if parameter is not None:
        probability = parameters[parameter]
        ordinal = source.served
        draw = _checked_draw(source)
        entry = {
            "rule": index,
            "parameter": parameter,
            "probability": probability,
            "draw": draw,
            "ordinal": ordinal,
        }
        missed = not draw < probability

    if not kind_matches:
        return _Outcome(False, f"{name}_kind_mismatch"), steps, entry
    if not matched:
        return _Outcome(False, f"{name}_no_match"), steps, entry
    if missed:
        return _Outcome(False, f"{name}_probability_miss"), steps, entry
    return _Outcome(True, f"{name}_match"), steps, entry


def _evaluate_tree(
    tree: Mapping[str, Any], parameters: Mapping[str, Any], normalized: _Normalized
) -> tuple[bool, int]:
    """Return whether *tree* holds, and the wildcard steps it took (R2).

    Iterative: logical nodes are frames on an explicit stack, so the tree's
    depth — bounded at construction — never becomes recursion. ``all`` and
    ``any`` stop at their first deciding operand; nothing here draws.
    """

    steps = 0
    # Each frame is [operator, operands, index of the operand being evaluated].
    stack: list[list[Any]] = []
    node = tree
    while True:
        ((operator, operands),) = node.items()
        if operator == "not":
            stack.append([operator, None, 0])
            node = operands
            continue
        if operator in ("all", "any"):
            stack.append([operator, operands, 0])
            node = operands[0]
            continue
        value, taken = _compare(operator, operands, parameters, normalized)
        steps += taken
        while True:
            if not stack:
                return value, steps
            frame = stack[-1]
            frame_operator, frame_operands, position = frame
            if frame_operator == "not":
                stack.pop()
                value = not value
                continue
            decided = value if frame_operator == "any" else not value
            if decided or position + 1 == len(frame_operands):
                stack.pop()
                continue
            frame[2] = position + 1
            node = frame_operands[position + 1]
            break


_ABSENT_FIELD = object()
_ABSENT_PARAM = object()


def _compare(
    operator: str,
    operands: Sequence[Any],
    parameters: Mapping[str, Any],
    normalized: _Normalized,
) -> tuple[bool, int]:
    """Decide one comparison node, returning its wildcard steps (R2).

    A ``param`` operand absent from the configured parameters makes the
    comparison true — an unset optional parameter narrows nothing; otherwise
    an absent ``field`` makes it false. ``equals``, ``gte`` and ``lte`` need
    two scalars of the same JSON type — a boolean is never a number — and
    ``gte``/``lte`` order numbers, or strings by code point; ``equals``
    compares strings case-sensitively. ``in`` needs a scalar left value and a
    list holding an equal element of the same JSON type. ``matches`` needs a
    string and a string pattern, compared by :func:`_wildcard_match`.
    """

    left, right = (_operand(operand, parameters, normalized) for operand in operands)
    if left is _ABSENT_PARAM or right is _ABSENT_PARAM:
        return True, 0
    if left is _ABSENT_FIELD or right is _ABSENT_FIELD:
        return False, 0
    left_type = _json_type(left)
    if left_type is None:
        return False, 0
    if operator == "in":
        if isinstance(right, (str, Mapping)) or not isinstance(right, Sequence):
            return False, 0
        return (
            any(_json_type(item) == left_type and item == left for item in right),
            0,
        )
    if left_type != _json_type(right):
        return False, 0
    if operator == "equals":
        return left == right, 0
    if operator == "matches":
        if left_type != "string":
            return False, 0
        return _wildcard_match(right, left)
    if left_type == "boolean":
        return False, 0
    if operator == "gte":
        return left >= right, 0
    return left <= right, 0


def _operand(
    operand: Any, parameters: Mapping[str, Any], normalized: _Normalized
) -> Any:
    """Resolve one operand to its value, or to an absence sentinel."""

    if isinstance(operand, Mapping):
        ((reference, path),) = operand.items()
        if reference == "field":
            present, value = normalized.field(path)
            return value if present else _ABSENT_FIELD
        node: Any = parameters
        for segment in path.split("."):
            if not isinstance(node, Mapping) or segment not in node:
                return _ABSENT_PARAM
            node = node[segment]
        return node
    return operand


def _json_type(value: Any) -> str | None:
    """The JSON scalar type of *value*, or ``None`` for a non-scalar."""

    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    return None


def _wildcard_match(pattern: str, text: str) -> tuple[bool, int]:
    """Match *text* against a case-insensitive wildcard *pattern* (A2, D5).

    ``*`` is any run of characters and ``?`` exactly one. Segment-based and
    non-recursive: the pattern is split on ``*``; the anchored prefix and
    suffix segments are checked first, then each middle segment is located
    leftmost, in order, after the previous one — leftmost placement is
    optimal for ``*``-separated segments, ``?`` included, so nothing is ever
    backtracked. Returns ``(matched, steps)``, a step being one comparison of
    a pattern position with a text position. Characters are compared one by
    one, each single character case-folded, so folding never changes the
    text length and the steps stay within
    ``(len(text) + 1) × (len(pattern) + 1)``, growing linearly in the text.
    """

    steps = 0
    segments = pattern.split("*")
    length = len(text)
    if len(segments) == 1:
        if len(pattern) != length:
            return False, 0
        for position, wanted in enumerate(pattern):
            steps += 1
            if not _same_character(wanted, text[position]):
                return False, steps
        return True, steps

    prefix, suffix = segments[0], segments[-1]
    if len(prefix) + len(suffix) > length:
        return False, 0
    for position, wanted in enumerate(prefix):
        steps += 1
        if not _same_character(wanted, text[position]):
            return False, steps
    end = length - len(suffix)
    for position, wanted in enumerate(suffix):
        steps += 1
        if not _same_character(wanted, text[end + position]):
            return False, steps

    cursor = len(prefix)
    for segment in segments[1:-1]:
        size = len(segment)
        if not size:
            continue
        start = cursor
        while True:
            if start + size > end:
                return False, steps
            for offset in range(size):
                steps += 1
                if not _same_character(segment[offset], text[start + offset]):
                    break
            else:
                break
            start += 1
        cursor = start + size
    return True, steps


def _same_character(wanted: str, seen: str) -> bool:
    """Whether one pattern character accepts one text character."""

    return wanted == "?" or wanted == seen or wanted.casefold() == seen.casefold()


def _checked_draw(source: _Source) -> float:
    """Draw one value and check it is a float in ``[0, 1)``."""

    draw = source.random()
    if not isinstance(draw, float) or not 0.0 <= draw < 1.0:
        raise ContractError(
            "rng.random", f"must return a float in [0, 1), got {draw!r}"
        )
    return draw


def _evaluate_probability(parameters: Mapping[str, Any], rng: _Source) -> _Outcome:
    """Draw once from the injected random source and compare to the threshold.

    Exactly one draw per evaluation, taken whatever the threshold is, so the
    number of values consumed depends only on the policy — which is what makes
    a replay of the same sequence reproduce the same decisions (AC3). Its draw
    is counted by the source but recorded in no ``draws`` entry (R2).
    """

    threshold = float(parameters["probability"])
    draw = _checked_draw(rng)
    matched = draw < threshold
    return _Outcome(matched, "probability_hit" if matched else "probability_miss")


def _evaluate_audience(
    parameters: Mapping[str, Any], context: TriggerContext
) -> _Outcome:
    """Consult the trusted context, and only the trusted context (AC4).

    This function receives no message text and no raw payload: a role or
    subscription claim is satisfied by a provenance-tagged claim or by nothing
    at all. A viewer writing "I am a subscriber" changes no decision, and an
    untagged claim assembled from an unverified source changes none either.
    """

    audience = parameters["audience"]
    claim = AUDIENCE_CLAIMS[audience]
    if claim is None:
        return _Outcome(True, "audience_everyone")
    if context.satisfies(claim):
        return _Outcome(True, f"audience_{audience}")
    return _Outcome(False, f"audience_{audience}_untrusted")


def _evaluate_keyword(
    parameters: Mapping[str, Any],
    text: str,
    companion_name: str | None,
    *,
    label: str,
) -> _Outcome:
    """Match the normalised text against the configured keywords.

    Case-insensitive substring matching over the normalised text only. The
    companion-name token is resolved from configuration here, so the default
    policy carries no name of its own.

    An event with empty text — a community notice carries none — never
    matches, whatever the keywords, the companion-name default included (R1).
    """

    keywords = _resolved_keywords(parameters, companion_name, label=f"{label}.parameters")
    if not text:
        return _Outcome(False, "keyword_no_text")
    lowered = text.casefold()
    for keyword in keywords:
        if keyword.casefold() in lowered:
            return _Outcome(True, "keyword_match")
    return _Outcome(False, "keyword_no_match")


def _evaluate_event_kind(parameters: Mapping[str, Any], kind: str) -> _Outcome:
    """Test the normalised event's kind for membership in ``kinds`` (R1, R6).

    Pure and draw-free: it receives neither the text nor the random source.
    """

    if kind in parameters["kinds"]:
        return _Outcome(True, f"event_kind_{kind}")
    return _Outcome(False, "event_kind_no_match")


def _combine(combination: str, outcomes: Sequence[_Outcome]) -> tuple[bool, str]:
    """Combine rule outcomes under the policy's declared operator."""

    if combination == "all_of":
        for outcome in outcomes:
            if not outcome.matched:
                return False, outcome.reason
        return True, outcomes[-1].reason
    if combination == "any_of":
        for outcome in outcomes:
            if outcome.matched:
                return True, outcome.reason
        return False, _NO_RULE_MATCHED
    # "none_of": the policy accepts exactly what no rule matched.
    for outcome in outcomes:
        if outcome.matched:
            return False, outcome.reason
    return True, _NO_RULE_MATCHED


def _validate_builtin_rule(
    rule: TriggerRule, *, companion_name: str | None, label: str
) -> None:
    """Check *rule* against the canonical schema of its built-in type.

    Applied at startup by the registry and again before a rule is evaluated, so
    a policy built outside :meth:`TriggerRegistry.validate_policy` — passed
    straight to :meth:`TriggerEngine.evaluate` — can never run with parameters
    nobody checked.
    """

    declaration = _BUILTIN_BY_NAME.get(rule.type)
    if declaration is None:
        supported = ", ".join(BUILTIN_TRIGGER_TYPE_NAMES)
        raise ContractError(
            f"{label}.type",
            f"is not a trigger type this runtime can evaluate ({rule.type!r}); "
            f"supported: {supported}",
        )
    declaration.validate_parameters(rule.parameters, label=f"{label}.parameters")
    if rule.type == TRIGGER_TYPE_KEYWORD:
        _resolved_keywords(
            rule.parameters, companion_name, label=f"{label}.parameters"
        )
    if rule.type == TRIGGER_TYPE_EVENT_KIND:
        kinds = rule.parameters["kinds"]
        if not kinds:
            raise ContractError(
                f"{label}.parameters.kinds", "must list at least one event kind"
            )
        seen: set[str] = set()
        for index, kind in enumerate(kinds):
            if kind in seen:
                raise ContractError(
                    f"{label}.parameters.kinds[{index}]", f"repeats {kind!r}"
                )
            seen.add(kind)


def _value_free(
    error: ContractError, policy: Any, *, spec: TriggerSpec, label: str
) -> ContractError:
    """Return *error*, stripped of the configured value when it is a declared
    rule's parameter diagnostic (R1).

    The schema validator quotes the offending value after ``, got``; a
    declared rule's diagnostic names the parameter path and keeps the
    wording, never the value. Any other diagnostic — including the one for a
    *policy* that is not a :class:`TriggerPolicy` at all — is returned
    unchanged.
    """

    if not isinstance(policy, TriggerPolicy):
        return error
    for index, rule in enumerate(policy.rules):
        location = f"{label}.rules[{index}].parameters"
        inside = error.field == location or error.field.startswith(
            (f"{location}.", f"{location}[")
        )
        if not inside:
            continue
        declaration = spec.declaration(rule.type)
        if declaration is None or not declaration.declared:
            return error
        return ContractError(error.field, error.reason.split(", got ", 1)[0])
    return error


def _validate_declared_rule(
    rule: TriggerRule, declaration: TriggerTypeDeclaration, *, label: str
) -> None:
    """Check *rule* against the kernel's checks of its declared type (R1).

    The declared parameter schema itself was already applied. On top of it a
    declared rule only narrows: the schema is read closed, so every configured
    key of an object declaring ``properties`` must be one of them, at every
    depth, unless the schema explicitly opens it — and the probability
    parameter, when the type has one, is a number in [0, 1]. Every diagnostic
    names the parameter path and echoes no configured value.
    """

    location = f"{label}.parameters"
    validate_against_schema(
        rule.parameters, declaration.parameter_schema, label=location, closed=True
    )
    parameter = declaration.probability_parameter
    if parameter is not None:
        value = rule.parameters.get(parameter)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not 0 <= value <= 1
        ):
            raise ContractError(
                f"{location}.{parameter}", "must be a probability in [0, 1]"
            )


def _resolved_keywords(
    parameters: Mapping[str, Any], companion_name: str | None, *, label: str
) -> tuple[str, ...]:
    """Return the keywords to match, with the companion token resolved."""

    raw = parameters["keywords"]
    if not raw:
        raise ContractError(f"{label}.keywords", "must list at least one keyword")
    resolved: list[str] = []
    for index, keyword in enumerate(raw):
        location = f"{label}.keywords[{index}]"
        if keyword == COMPANION_NAME_TOKEN:
            if companion_name is None:
                raise ContractError(
                    location,
                    f"uses {COMPANION_NAME_TOKEN} but no companion name is configured",
                )
            resolved.append(companion_name)
            continue
        if not keyword.strip():
            raise ContractError(location, "must not be empty")
        resolved.append(keyword)
    return tuple(resolved)


# --------------------------------------------------------------------------- #
# Draws and traces (R2, D6)
# --------------------------------------------------------------------------- #


def decision_trace_payload(decision: TriggerDecision) -> dict[str, Any]:
    """Return the ``input.trigger.*`` trace payload of *decision* (D6).

    The six keys every input's trace carries, plus ``draws`` — a list of the
    five-scalar entries — only when the decision has any, so a generic-only
    decision's trace is byte-identical to the one an input built before.
    """

    payload: dict[str, Any] = {
        "source_event_id": decision.source_event_id,
        "input": decision.input_name,
        "platform": decision.platform,
        "channel_id": decision.channel_id,
        "policy_version": decision.policy_version,
        "reason": decision.reason,
    }
    if decision.draws:
        payload["draws"] = [dict(entry) for entry in decision.draws]
    return payload


def _validated_draws(value: Any) -> tuple[Mapping[str, Any], ...]:
    """Return *value* as a tuple of read-only five-scalar entries, or raise.

    Each entry holds exactly :data:`DRAW_ENTRY_KEYS`, each a string, a number
    or a boolean. No diagnostic echoes a value.
    """

    label = "TriggerDecision.draws"
    if isinstance(value, (str, bytes, Mapping)) or not isinstance(value, Sequence):
        raise ContractError(label, "must be a sequence of draw entries")
    entries: list[Mapping[str, Any]] = []
    expected = frozenset(DRAW_ENTRY_KEYS)
    for index, entry in enumerate(value):
        location = f"{label}[{index}]"
        if not isinstance(entry, Mapping):
            raise ContractError(location, "must be a mapping")
        if len(entry) != len(DRAW_ENTRY_KEYS) or frozenset(entry) != expected:
            raise ContractError(
                location, f"must hold exactly the keys {', '.join(DRAW_ENTRY_KEYS)}"
            )
        for key in DRAW_ENTRY_KEYS:
            if _json_type(entry[key]) is None:
                raise ContractError(f"{location}.{key}", "must be a scalar")
        entries.append(MappingProxyType({key: entry[key] for key in DRAW_ENTRY_KEYS}))
    return tuple(entries)


# --------------------------------------------------------------------------- #
# Field helpers
# --------------------------------------------------------------------------- #


def _require_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise ContractError(field_name, f"must be a string, got {type(value).__name__}")
    if not value.strip():
        raise ContractError(field_name, "must not be empty")
    return value


def _optional_text(value: Any) -> str | None:
    """Return *value* when it is a non-blank string, else ``None``."""

    if isinstance(value, str) and value.strip():
        return value
    return None


def _bounded_reason(reason: str) -> str:
    """Return *reason* cut to :data:`REASON_MAX_LENGTH` characters."""

    if len(reason) <= REASON_MAX_LENGTH:
        return reason
    return reason[:REASON_MAX_LENGTH]


def _companion_identities(value: Any) -> frozenset[str]:
    """Return the case-folded identities the anti-echo invariant refuses."""

    if value is None:
        return frozenset()
    if isinstance(value, str):
        return frozenset({_require_text(value, "companion_identity").casefold()})
    if not isinstance(value, Sequence):
        raise ContractError(
            "companion_identity",
            f"must be a string or a sequence of strings, got {type(value).__name__}",
        )
    identities = set()
    for index, identity in enumerate(value):
        identities.add(
            _require_text(identity, f"companion_identity[{index}]").casefold()
        )
    return frozenset(identities)


def _validated_clock(clock: Any, field_name: str) -> Clock:
    if not callable(clock):
        raise ContractError(field_name, "must be callable")
    return clock


def _validated_rng(rng: Any, field_name: str) -> RandomSource:
    draw = getattr(rng, "random", None)
    if not callable(draw):
        raise ContractError(field_name, "must expose a callable random()")
    return rng


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
