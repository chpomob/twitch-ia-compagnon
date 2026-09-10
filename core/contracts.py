"""Versioned, dependency-free identity and action contracts.

Every layer of the runtime imports this module and this module imports nothing
from the project, so it can carry the vocabulary shared by triggers, admission,
the action registry and the executor without creating an import cycle.

Contracts validate their own fields at construction and raise :class:`ContractError`
naming the offending field. The module performs no I/O and uses no asyncio.

Beyond the identity and action contracts it also carries the declarative trigger
vocabulary (R1) and the two supervision primitives every layer needs (R8): the
:class:`Counters` registry and :func:`sanitize_trace`. They live here rather than
in a module of their own because ``core/triggers.py``, ``core/admission.py``,
``core/actions.py`` and ``core/runtime.py`` all need them while ``core/runtime.py``
already imports the contracts, so hosting them there would close an import cycle.
Nothing else belongs here: this module stays pure data plus those two utilities.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

CONTRACT_VERSION = 1
"""Version of the contracts carried by :class:`ActionCall`."""

SUPPORTED_CONTRACT_VERSIONS = frozenset({CONTRACT_VERSION})

TERMINAL_STATUSES = frozenset(
    {"success", "refused", "error", "timeout", "cancelled", "external_unknown"}
)
"""The only statuses an :class:`ActionObservation` may carry (R5)."""

ACTION_NATURES = frozenset({"read", "write"})
"""An action either observes (``read``) or engages an effect (``write``)."""

IDEMPOTENCY_POLICIES = frozenset({"none", "key", "natural"})
"""How a provider behaves when the same call is replayed.

``none``
    Replaying may duplicate the effect; no blind retry is allowed.
``key``
    The provider deduplicates on the call identity while its entry is kept.
``natural``
    Replaying engages no additional effect.
"""

WILDCARD = "*"
"""The only wildcard a :class:`Destination` component may hold."""

COMBINATION_OPERATORS = frozenset({"all_of", "any_of", "none_of"})
"""The combination operators a :class:`TriggerSpec` may declare (R1).

An operator is never implicit: a module declares the ones it supports and a
policy may only use a declared one.
"""

POLICY_VERSION_LENGTH = 16
"""Hexadecimal characters kept from the policy content hash (R1)."""

# --------------------------------------------------------------------------- #
# Trace vocabulary (R8)
# --------------------------------------------------------------------------- #

TRACE_MODULE_READY = "module.ready"
TRACE_MODULE_DEGRADED = "module.degraded"
TRACE_MODULE_STOPPED = "module.stopped"
TRACE_INPUT_TRIGGER_ACCEPTED = "input.trigger.accepted"
TRACE_INPUT_TRIGGER_REJECTED = "input.trigger.rejected"
TRACE_BRAIN_ADMISSION_ACCEPTED = "brain.admission.accepted"
TRACE_BRAIN_ADMISSION_REJECTED = "brain.admission.rejected"
TRACE_BRAIN_RUN_STARTED = "brain.run.started"
TRACE_BRAIN_RUN_COMPLETED = "brain.run.completed"
TRACE_ACTION_STARTED = "action.started"
TRACE_ACTION_COMPLETED = "action.completed"
TRACE_CHANNEL_CHAT_SENT = "channel.chat.sent"

TRACE_EVENT_TYPES = (
    TRACE_MODULE_READY,
    TRACE_MODULE_DEGRADED,
    TRACE_MODULE_STOPPED,
    TRACE_INPUT_TRIGGER_ACCEPTED,
    TRACE_INPUT_TRIGGER_REJECTED,
    TRACE_BRAIN_ADMISSION_ACCEPTED,
    TRACE_BRAIN_ADMISSION_REJECTED,
    TRACE_BRAIN_RUN_STARTED,
    TRACE_BRAIN_RUN_COMPLETED,
    TRACE_ACTION_STARTED,
    TRACE_ACTION_COMPLETED,
    TRACE_CHANNEL_CHAT_SENT,
)
"""The whole supervision vocabulary, in publication order (R8)."""

INTERNAL_TRACE_TYPES = frozenset(TRACE_EVENT_TYPES)
"""Trace event types that must never be trigger-evaluated (R8, AC5).

The runtime publishes its own supervision facts on the same bus as platform
events. Feeding one back into trigger evaluation would let the runtime answer
itself, so the trigger engine excludes this set as a system invariant, before
any rule and with no rule able to re-enable it.
"""

TERMINAL_TRACE_TYPES = frozenset(
    {
        TRACE_ACTION_COMPLETED,
        TRACE_BRAIN_RUN_COMPLETED,
        TRACE_CHANNEL_CHAT_SENT,
        TRACE_MODULE_STOPPED,
    }
)
"""Traces whose terminal state must be recorded by its owner before publication.

R8 orders the two writes: the owner records the terminal state, then the trace
is published. ``Supervision`` routes exactly these four through
``record_and_emit`` and refuses them on ``emit`` so the ordering cannot be
bypassed by accident.
"""

# --------------------------------------------------------------------------- #
# Counters and trace sanitisation (R8)
# --------------------------------------------------------------------------- #

COUNTER_TRIGGER_REJECTIONS = "trigger_rejections"
COUNTER_ADMISSION_REJECTIONS = "admission_rejections"
COUNTER_STALE_DROP = "stale_drop"
COUNTER_RUN_DEADLINE_EXPIRIES = "run_deadline_expiries"
COUNTER_ACTION_TIMEOUTS = "action_timeouts"
COUNTER_AUDIT_RECORD_LOSSES = "audit_record_losses"
COUNTER_DEDUP_EVICTIONS = "dedup_evictions"

REQUIRED_COUNTERS = (
    COUNTER_TRIGGER_REJECTIONS,
    COUNTER_ADMISSION_REJECTIONS,
    COUNTER_STALE_DROP,
    COUNTER_RUN_DEADLINE_EXPIRIES,
    COUNTER_ACTION_TIMEOUTS,
    COUNTER_AUDIT_RECORD_LOSSES,
    COUNTER_DEDUP_EVICTIONS,
)
"""The seven counters supervision must expose (R8, AC28)."""

COUNTER_LOST_TRACES = "lost_traces"
"""Diagnostic count of terminal traces that could not be published (R8).

Deliberately kept out of :data:`REQUIRED_COUNTERS`: it measures the runtime's
own observability rather than the pipeline, and ``record_and_emit`` increments
it when the publication of an already-recorded terminal state fails, so the
loss is visible instead of silent. It is still readable from a snapshot.
"""

DIAGNOSTIC_COUNTERS = (COUNTER_LOST_TRACES,)
"""Counters exposed by a snapshot but outside the required set."""

RESERVED_TRACE_KEYS = frozenset({"prompt", "reasoning"})
"""Payload keys a trace may never carry, at any depth (R8, AC29)."""

REDACTION_PLACEHOLDER = "[REDACTED]"

TRUNCATION_KEY = "trace_truncated"
"""Marker key set on a trace payload that had to be shortened."""

TRACE_MAX_BYTES = 2048
"""Default byte budget of one sanitised trace payload."""

TRACE_MIN_MAX_BYTES = 64
"""Smallest budget that can still hold the truncation marker."""

__all__ = [
    "ACTION_NATURES",
    "COMBINATION_OPERATORS",
    "CONTRACT_VERSION",
    "COUNTER_ACTION_TIMEOUTS",
    "COUNTER_ADMISSION_REJECTIONS",
    "COUNTER_AUDIT_RECORD_LOSSES",
    "COUNTER_DEDUP_EVICTIONS",
    "COUNTER_LOST_TRACES",
    "COUNTER_RUN_DEADLINE_EXPIRIES",
    "COUNTER_STALE_DROP",
    "COUNTER_TRIGGER_REJECTIONS",
    "DIAGNOSTIC_COUNTERS",
    "IDEMPOTENCY_POLICIES",
    "INTERNAL_TRACE_TYPES",
    "POLICY_VERSION_LENGTH",
    "REDACTION_PLACEHOLDER",
    "REQUIRED_COUNTERS",
    "RESERVED_TRACE_KEYS",
    "SUPPORTED_CONTRACT_VERSIONS",
    "TERMINAL_STATUSES",
    "TERMINAL_TRACE_TYPES",
    "TRACE_ACTION_COMPLETED",
    "TRACE_ACTION_STARTED",
    "TRACE_BRAIN_ADMISSION_ACCEPTED",
    "TRACE_BRAIN_ADMISSION_REJECTED",
    "TRACE_BRAIN_RUN_COMPLETED",
    "TRACE_BRAIN_RUN_STARTED",
    "TRACE_CHANNEL_CHAT_SENT",
    "TRACE_EVENT_TYPES",
    "TRACE_INPUT_TRIGGER_ACCEPTED",
    "TRACE_INPUT_TRIGGER_REJECTED",
    "TRACE_MAX_BYTES",
    "TRACE_MIN_MAX_BYTES",
    "TRACE_MODULE_DEGRADED",
    "TRACE_MODULE_READY",
    "TRACE_MODULE_STOPPED",
    "TRUNCATION_KEY",
    "WILDCARD",
    "ActionCall",
    "ActionObservation",
    "ActionSpec",
    "ContractError",
    "Counters",
    "Destination",
    "SchemaError",
    "SessionKey",
    "TriggerPolicy",
    "TriggerRule",
    "TriggerSpec",
    "TriggerTypeDeclaration",
    "sanitize_trace",
    "trace_size",
    "validate_against_schema",
    "validate_schema",
]


class ContractError(ValueError):
    """Raised when a contract field or a validated value is unacceptable.

    ``field`` names the offending field as a dotted path so the diagnostic
    shape is identical for identity contracts, action contracts and schema
    validation.
    """

    def __init__(self, field: str, reason: str) -> None:
        self.field = field
        self.reason = reason
        super().__init__(f"{field}: {reason}")


class SchemaError(ContractError):
    """Raised when a schema definition itself is malformed."""


# --------------------------------------------------------------------------- #
# Field helpers
# --------------------------------------------------------------------------- #


def _require_text(value: Any, field: str) -> str:
    """Return *value* when it is a non-blank string, else raise."""

    if not isinstance(value, str):
        raise ContractError(field, f"must be a string, got {type(value).__name__}")
    if not value.strip():
        raise ContractError(field, "must not be empty")
    return value


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _require_finite_number(value: Any, field: str) -> float:
    if not _is_number(value):
        raise ContractError(field, f"must be a number, got {type(value).__name__}")
    number = float(value)
    if not math.isfinite(number):
        raise ContractError(field, "must be a finite number")
    return number


def _require_mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ContractError(field, f"must be a mapping, got {type(value).__name__}")
    for key in value:
        if not isinstance(key, str):
            raise ContractError(field, f"must use string keys, got {type(key).__name__}")
    return value


def _freeze(value: Any, field: str) -> Any:
    """Deep-copy *value* into an immutable structure with string mapping keys."""

    if isinstance(value, Mapping):
        _require_mapping(value, field)
        return MappingProxyType(
            {key: _freeze(item, f"{field}.{key}") for key, item in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item, f"{field}[{index}]") for index, item in enumerate(value))
    if isinstance(value, (set, frozenset)):
        raise ContractError(field, "must not contain a set; use a list")
    return value


def _frozen_mapping(value: Any, field: str) -> Mapping[str, Any]:
    _require_mapping(value, field)
    return _freeze(value, field)


# --------------------------------------------------------------------------- #
# Minimal JSON-schema subset
# --------------------------------------------------------------------------- #

_CONSTRAINT_KEYWORDS = frozenset(
    {
        "type",
        "enum",
        "minimum",
        "maximum",
        "required",
        "properties",
        "items",
        "additionalProperties",
    }
)

# Annotations carry no constraint; they are accepted and ignored so manifests
# can document their schemas. Every other keyword is an explicit error rather
# than a silent no-op, so an unsupported constraint can never look enforced.
_ANNOTATION_KEYWORDS = frozenset({"title", "description"})

_KNOWN_KEYWORDS = _CONSTRAINT_KEYWORDS | _ANNOTATION_KEYWORDS

_TYPE_NAMES = frozenset(
    {"object", "array", "string", "integer", "number", "boolean", "null"}
)


def validate_schema(schema: Any, *, label: str) -> None:
    """Raise :class:`SchemaError` unless *schema* uses the supported subset.

    The subset is ``type``, ``enum``, ``minimum``, ``maximum``, ``required``,
    ``properties``, ``items`` and ``additionalProperties``.
    """

    if not isinstance(schema, Mapping):
        raise SchemaError(label, f"must be a mapping, got {type(schema).__name__}")
    for key in schema:
        if not isinstance(key, str):
            raise SchemaError(label, f"keyword must be a string, got {type(key).__name__}")
        if key not in _KNOWN_KEYWORDS:
            supported = ", ".join(sorted(_KNOWN_KEYWORDS))
            raise SchemaError(
                f"{label}.{key}", f"unsupported schema keyword; supported: {supported}"
            )

    if "type" in schema:
        declared = schema["type"]
        if not isinstance(declared, str):
            raise SchemaError(
                f"{label}.type", "must be a single type name, got a non-string"
            )
        if declared not in _TYPE_NAMES:
            names = ", ".join(sorted(_TYPE_NAMES))
            raise SchemaError(f"{label}.type", f"unknown type {declared!r}; known: {names}")

    if "enum" in schema:
        members = schema["enum"]
        if not isinstance(members, (list, tuple)) or not members:
            raise SchemaError(f"{label}.enum", "must be a non-empty list of values")

    for bound in ("minimum", "maximum"):
        if bound in schema and not _is_number(schema[bound]):
            raise SchemaError(f"{label}.{bound}", "must be a number")
    if "minimum" in schema and "maximum" in schema:
        if float(schema["minimum"]) > float(schema["maximum"]):
            raise SchemaError(f"{label}.minimum", "must not exceed maximum")

    if "required" in schema:
        required = schema["required"]
        if not isinstance(required, (list, tuple)):
            raise SchemaError(f"{label}.required", "must be a list of property names")
        seen: set[str] = set()
        for name in required:
            if not isinstance(name, str) or not name:
                raise SchemaError(
                    f"{label}.required", "must list non-empty property names"
                )
            if name in seen:
                raise SchemaError(f"{label}.required", f"lists {name!r} twice")
            seen.add(name)

    if "properties" in schema:
        properties = schema["properties"]
        if not isinstance(properties, Mapping):
            raise SchemaError(f"{label}.properties", "must be a mapping of subschemas")
        for name, subschema in properties.items():
            if not isinstance(name, str) or not name:
                raise SchemaError(
                    f"{label}.properties", "must use non-empty property names"
                )
            validate_schema(subschema, label=f"{label}.properties.{name}")

    if "items" in schema:
        validate_schema(schema["items"], label=f"{label}.items")

    if "additionalProperties" in schema and not isinstance(
        schema["additionalProperties"], bool
    ):
        raise SchemaError(f"{label}.additionalProperties", "must be a boolean")


def validate_against_schema(value: Any, schema: Any, *, label: str) -> None:
    """Raise :class:`ContractError` unless *value* satisfies *schema*.

    *label* prefixes every diagnostic so arguments, results and trigger
    parameters share one diagnostic shape. The schema itself is checked first,
    so an unsupported keyword is reported instead of being silently ignored.
    Absent ``additionalProperties: false``, undeclared object keys are accepted,
    as in JSON Schema.
    """

    validate_schema(schema, label=label)
    _apply_schema(value, schema, label=label)


def _apply_schema(value: Any, schema: Mapping[str, Any], *, label: str) -> None:
    declared = schema.get("type")
    if declared is not None:
        _check_type(value, declared, label)

    if "enum" in schema:
        members = schema["enum"]
        if not any(_enum_matches(value, member) for member in members):
            allowed = ", ".join(repr(member) for member in members)
            raise ContractError(label, f"must be one of {allowed}, got {value!r}")

    for bound, ok, wording in (
        ("minimum", lambda v, b: v >= b, "must be >="),
        ("maximum", lambda v, b: v <= b, "must be <="),
    ):
        if bound in schema:
            if not _is_number(value):
                raise ContractError(
                    label, f"must be a number to satisfy {bound}, got {type(value).__name__}"
                )
            # Compared as declared: coercing both sides to float would let an
            # integer beyond 2**53 pass a bound it actually exceeds, because
            # both would collapse onto the same float. Python compares int and
            # float exactly, so the mixed comparison stays precise.
            if not ok(value, schema[bound]):
                raise ContractError(label, f"{wording} {schema[bound]!r}, got {value!r}")

    object_keywords = {"required", "properties", "additionalProperties"} & set(schema)
    if object_keywords:
        if not isinstance(value, Mapping):
            raise ContractError(
                label, f"must be a mapping, got {type(value).__name__}"
            )
        _apply_object_schema(value, schema, label=label)

    if "items" in schema:
        if not _is_array(value):
            raise ContractError(label, f"must be a list, got {type(value).__name__}")
        for index, item in enumerate(value):
            _apply_schema(item, schema["items"], label=f"{label}[{index}]")


def _apply_object_schema(
    value: Mapping[Any, Any], schema: Mapping[str, Any], *, label: str
) -> None:
    for key in value:
        if not isinstance(key, str):
            raise ContractError(label, f"must use string keys, got {type(key).__name__}")

    for name in schema.get("required", ()):
        if name not in value:
            raise ContractError(f"{label}.{name}", "is required and missing")

    properties: Mapping[str, Any] = schema.get("properties", {})
    for name, subschema in properties.items():
        if name in value:
            _apply_schema(value[name], subschema, label=f"{label}.{name}")

    if schema.get("additionalProperties") is False:
        for name in value:
            if name not in properties:
                raise ContractError(f"{label}.{name}", "is not an allowed property")


def _is_array(value: Any) -> bool:
    return isinstance(value, (list, tuple))


def _check_type(value: Any, declared: str, label: str) -> None:
    if declared == "object":
        ok = isinstance(value, Mapping)
    elif declared == "array":
        ok = _is_array(value)
    elif declared == "string":
        ok = isinstance(value, str)
    elif declared == "integer":
        ok = isinstance(value, int) and not isinstance(value, bool)
    elif declared == "number":
        ok = _is_number(value)
    elif declared == "boolean":
        ok = isinstance(value, bool)
    else:  # "null"
        ok = value is None
    if not ok:
        raise ContractError(
            label, f"must be of type {declared!r}, got {type(value).__name__}"
        )


def _enum_matches(value: Any, member: Any) -> bool:
    """Compare two JSON values structurally, ignoring container packaging.

    Specs freeze their schemas, so an enumeration member arrives as a tuple or
    a :class:`MappingProxyType` while the candidate value is a plain list or
    dict. Matching must see through that packaging yet stay strict about JSON
    semantics, so ``True`` never matches ``1``, at the top level or nested.
    """

    if isinstance(value, bool) != isinstance(member, bool):
        return False

    if isinstance(value, Mapping) or isinstance(member, Mapping):
        if not (isinstance(value, Mapping) and isinstance(member, Mapping)):
            return False
        if len(value) != len(member):
            return False
        return all(
            key in member and _enum_matches(item, member[key])
            for key, item in value.items()
        )

    if _is_array(value) or _is_array(member):
        if not (_is_array(value) and _is_array(member)):
            return False
        if len(value) != len(member):
            return False
        return all(_enum_matches(a, b) for a, b in zip(value, member))

    return value == member


# --------------------------------------------------------------------------- #
# Identity
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class SessionKey:
    """Identity of a run and of its conversation memory (R3).

    Two platforms or two channels never share a memory, even for the same
    ``viewer_id``. The serialisation is length-prefixed so no two distinct
    triples can collide, whatever separator characters the components contain.
    """

    platform: str
    channel_id: str
    viewer_id: str

    def __post_init__(self) -> None:
        _require_text(self.platform, "SessionKey.platform")
        _require_text(self.channel_id, "SessionKey.channel_id")
        _require_text(self.viewer_id, "SessionKey.viewer_id")

    def serialize(self) -> str:
        """Return the unambiguous string form of this key."""

        platform, channel_id, viewer_id = self.platform, self.channel_id, self.viewer_id
        return (
            f"{len(platform)}:{platform}"
            f"|{len(channel_id)}:{channel_id}"
            f"|{len(viewer_id)}:{viewer_id}"
        )

    def __str__(self) -> str:
        return self.serialize()


# --------------------------------------------------------------------------- #
# Destinations
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Destination:
    """Where an action acts, or the scope a provider is bound to.

    Each component is either a concrete value or :data:`WILDCARD`. ``scope`` is
    additionally hierarchical: ``chat`` contains ``chat.reply``. Containment and
    overlap are exact, so an ambiguous binding can be detected at preparation
    rather than at the first call.
    """

    platform: str
    channel_id: str
    scope: str

    def __post_init__(self) -> None:
        _validate_component(self.platform, "Destination.platform")
        _validate_component(self.channel_id, "Destination.channel_id")
        _validate_scope(self.scope, "Destination.scope")

    @property
    def is_concrete(self) -> bool:
        """Whether this destination names one target rather than a scope."""

        return WILDCARD not in (self.platform, self.channel_id, self.scope)

    def contains(self, other: "Destination") -> bool:
        """Whether every target *other* designates is also designated by self."""

        if not isinstance(other, Destination):
            raise ContractError("Destination.contains", "expects a Destination")
        return (
            _component_contains(self.platform, other.platform)
            and _component_contains(self.channel_id, other.channel_id)
            and _scope_contains(self.scope, other.scope)
        )

    def overlaps(self, other: "Destination") -> bool:
        """Whether some target is designated by both destinations."""

        if not isinstance(other, Destination):
            raise ContractError("Destination.overlaps", "expects a Destination")
        return (
            _component_overlaps(self.platform, other.platform)
            and _component_overlaps(self.channel_id, other.channel_id)
            and (_scope_contains(self.scope, other.scope) or _scope_contains(other.scope, self.scope))
        )

    def __contains__(self, other: "Destination") -> bool:
        return self.contains(other)

    def __str__(self) -> str:
        return f"{self.platform}/{self.channel_id}/{self.scope}"


def _validate_component(value: Any, field: str) -> None:
    _require_text(value, field)
    if WILDCARD in value and value != WILDCARD:
        raise ContractError(field, f"must be {WILDCARD!r} or contain no wildcard")


def _validate_scope(value: Any, field: str) -> None:
    _validate_component(value, field)
    if value == WILDCARD:
        return
    for segment in value.split("."):
        if not segment.strip():
            raise ContractError(field, "must not contain an empty segment")


def _component_contains(outer: str, inner: str) -> bool:
    return outer == WILDCARD or outer == inner


def _component_overlaps(left: str, right: str) -> bool:
    return _component_contains(left, right) or _component_contains(right, left)


def _scope_contains(outer: str, inner: str) -> bool:
    if outer == WILDCARD or outer == inner:
        return True
    if inner == WILDCARD:
        return False
    return inner.startswith(f"{outer}.")


# --------------------------------------------------------------------------- #
# Actions
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class ActionSpec:
    """What an action is, independently of who provides it (R5)."""

    name: str
    version: int
    description: str
    argument_schema: Mapping[str, Any]
    result_schema: Mapping[str, Any]
    nature: str
    required_permissions: tuple[str, ...]
    supported_destinations: tuple[Destination, ...]
    timeout_seconds: float
    idempotency: str

    def __post_init__(self) -> None:
        _validate_action_name(self.name, "ActionSpec.name")
        _validate_version(self.version, "ActionSpec.version")
        _require_text(self.description, "ActionSpec.description")

        for field in ("argument_schema", "result_schema"):
            schema = getattr(self, field)
            validate_schema(schema, label=f"ActionSpec.{field}")
            object.__setattr__(self, field, _frozen_mapping(schema, f"ActionSpec.{field}"))

        if self.nature not in ACTION_NATURES:
            allowed = ", ".join(sorted(ACTION_NATURES))
            raise ContractError(
                "ActionSpec.nature", f"must be one of {allowed}, got {self.nature!r}"
            )

        object.__setattr__(
            self,
            "required_permissions",
            _unique_texts(
                self.required_permissions, "ActionSpec.required_permissions"
            ),
        )

        destinations = self.supported_destinations
        if isinstance(destinations, Destination) or not isinstance(destinations, Sequence):
            raise ContractError(
                "ActionSpec.supported_destinations", "must be a sequence of Destination"
            )
        destinations = tuple(destinations)
        if not destinations:
            raise ContractError(
                "ActionSpec.supported_destinations", "must declare at least one destination"
            )
        seen: list[Destination] = []
        for index, destination in enumerate(destinations):
            field = f"ActionSpec.supported_destinations[{index}]"
            if not isinstance(destination, Destination):
                raise ContractError(field, "must be a Destination")
            if destination in seen:
                raise ContractError(field, f"duplicates {destination}")
            seen.append(destination)
        object.__setattr__(self, "supported_destinations", destinations)

        timeout = _require_finite_number(self.timeout_seconds, "ActionSpec.timeout_seconds")
        if timeout <= 0:
            raise ContractError("ActionSpec.timeout_seconds", "must be strictly positive")
        object.__setattr__(self, "timeout_seconds", timeout)

        if self.idempotency not in IDEMPOTENCY_POLICIES:
            allowed = ", ".join(sorted(IDEMPOTENCY_POLICIES))
            raise ContractError(
                "ActionSpec.idempotency",
                f"must be one of {allowed}, got {self.idempotency!r}",
            )

    def supports(self, destination: Destination) -> bool:
        """Whether *destination* falls inside a declared supported destination."""

        if not isinstance(destination, Destination):
            raise ContractError("ActionSpec.supports", "expects a Destination")
        return any(declared.contains(destination) for declared in self.supported_destinations)

    def validate_arguments(self, arguments: Any) -> None:
        """Raise :class:`ContractError` unless *arguments* match the schema."""

        validate_against_schema(arguments, self.argument_schema, label="arguments")

    def validate_result(self, result: Any) -> None:
        """Raise :class:`ContractError` unless *result* matches the schema."""

        validate_against_schema(result, self.result_schema, label="result")


def _validate_dotted_name(value: Any, field: str, *, minimum_segments: int) -> None:
    """Validate a stable identifier made of dot-separated alphanumeric segments."""

    _require_text(value, field)
    segments = value.split(".")
    if len(segments) < minimum_segments:
        raise ContractError(
            field, f"must have at least {minimum_segments} dotted segments, got {value!r}"
        )
    for segment in segments:
        if not segment or not segment[0].isalpha():
            raise ContractError(field, f"has an invalid segment in {value!r}")
        if not all(char.isalnum() or char == "_" for char in segment):
            raise ContractError(field, f"has an invalid segment in {value!r}")


def _validate_action_name(value: Any, field: str) -> None:
    _validate_dotted_name(value, field, minimum_segments=2)


def _validate_version(value: Any, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ContractError(field, f"must be an integer, got {type(value).__name__}")
    if value < 1:
        raise ContractError(field, "must be strictly positive")
    return value


def _unique_texts(values: Any, field: str) -> tuple[str, ...]:
    if isinstance(values, str) or not isinstance(values, Sequence):
        raise ContractError(field, "must be a sequence of strings")
    result: list[str] = []
    for index, value in enumerate(values):
        item = _require_text(value, f"{field}[{index}]")
        if item in result:
            raise ContractError(f"{field}[{index}]", f"duplicates {item!r}")
        result.append(item)
    return tuple(result)


@dataclass(frozen=True, slots=True)
class ActionCall:
    """One correlated request to act, carrying its own trust context (R5).

    ``arguments`` are checked for shape here; the executor validates them
    against the spec's ``argument_schema`` before invoking any provider. They
    are stored as a deep, immutable copy — nested mappings become read-only and
    nested lists become tuples — so a validated call cannot be altered
    afterwards by whoever built it.
    """

    action_name: str
    action_version: int
    arguments: Mapping[str, Any]
    conversation_id: str
    run_id: str
    call_id: str
    source_event_id: str
    destination: Destination
    principal: str
    deadline: float
    message_id: str | None = None
    contract_version: int = CONTRACT_VERSION

    def __post_init__(self) -> None:
        _validate_action_name(self.action_name, "ActionCall.action_name")
        _validate_version(self.action_version, "ActionCall.action_version")
        object.__setattr__(
            self, "arguments", _frozen_mapping(self.arguments, "ActionCall.arguments")
        )
        for field in ("conversation_id", "run_id", "call_id", "source_event_id", "principal"):
            _require_text(getattr(self, field), f"ActionCall.{field}")

        if not isinstance(self.destination, Destination):
            raise ContractError("ActionCall.destination", "must be a Destination")
        if not self.destination.is_concrete:
            raise ContractError(
                "ActionCall.destination",
                f"must name a concrete target, got {self.destination}",
            )

        object.__setattr__(
            self, "deadline", _require_finite_number(self.deadline, "ActionCall.deadline")
        )

        if self.message_id is not None:
            _require_text(self.message_id, "ActionCall.message_id")

        if self.contract_version not in SUPPORTED_CONTRACT_VERSIONS:
            supported = ", ".join(str(v) for v in sorted(SUPPORTED_CONTRACT_VERSIONS))
            raise ContractError(
                "ActionCall.contract_version",
                f"must be one of {supported}, got {self.contract_version!r}",
            )


@dataclass(frozen=True, slots=True)
class ActionObservation:
    """The terminal outcome of an action, including for a write (R5).

    ``status`` is constrained to :data:`TERMINAL_STATUSES`. A result is only
    carried by a ``success``; every other status carries a normalised error so
    an absent status can never be read as a delivery.
    """

    status: str
    provenance: Mapping[str, Any]
    result: Mapping[str, Any] | None = None
    error: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.status not in TERMINAL_STATUSES:
            allowed = ", ".join(sorted(TERMINAL_STATUSES))
            raise ContractError(
                "ActionObservation.status",
                f"must be one of {allowed}, got {self.status!r}",
            )

        provenance = _frozen_mapping(self.provenance, "ActionObservation.provenance")
        if not provenance:
            raise ContractError("ActionObservation.provenance", "must not be empty")
        object.__setattr__(self, "provenance", provenance)

        if self.result is not None:
            if self.status != "success":
                raise ContractError(
                    "ActionObservation.result",
                    f"is only carried by a success, got status {self.status!r}",
                )
            object.__setattr__(
                self, "result", _frozen_mapping(self.result, "ActionObservation.result")
            )

        if self.status == "success":
            if self.error is not None:
                raise ContractError(
                    "ActionObservation.error", "must be absent on a success"
                )
        else:
            if self.error is None:
                raise ContractError(
                    "ActionObservation.error",
                    f"is required when status is {self.status!r}",
                )
            object.__setattr__(
                self, "error", _normalised_error(self.error, "ActionObservation.error")
            )

    @property
    def succeeded(self) -> bool:
        return self.status == "success"


def _normalised_error(value: Any, field: str) -> Mapping[str, Any]:
    error = _frozen_mapping(value, field)
    _require_text(error.get("code"), f"{field}.code")
    if not isinstance(error.get("message"), str):
        raise ContractError(f"{field}.message", "must be a string")
    if "retryable" in error and not isinstance(error["retryable"], bool):
        raise ContractError(f"{field}.retryable", "must be a boolean")
    return error


# --------------------------------------------------------------------------- #
# Canonical rendering
# --------------------------------------------------------------------------- #


def _canonical_text(value: Any) -> str:
    """Render *value* as one unambiguous string.

    Every scalar carries a type tag and every string a length prefix, so no two
    distinct values share a rendering: ``True`` never renders like ``1`` and
    ``{"a": "b|c"}`` never renders like ``{"a|b": "c"}``. Mapping keys are
    sorted, because a mapping has no order; sequence order is preserved,
    because a trigger policy's evaluation order is explicit and meaningful.

    It backs both the policy content hash (R1) and :func:`trace_size` (R8).
    """

    if value is None:
        return "n"
    if isinstance(value, bool):
        return "b1" if value else "b0"
    if isinstance(value, int):
        return f"i{value}"
    if isinstance(value, float):
        return f"f{value!r}"
    if isinstance(value, str):
        return f"s{len(value)}:{value}"
    if isinstance(value, Mapping):
        parts = []
        for key in sorted(value):
            parts.append(f"{len(key)}:{key}={_canonical_text(value[key])};")
        return "m" + "".join(parts)
    if isinstance(value, (list, tuple)):
        return "a" + "".join(f"{_canonical_text(item)};" for item in value)
    return _canonical_text(str(value))


def trace_size(payload: Any) -> int:
    """Return the bounded byte size :func:`sanitize_trace` measures against.

    It is the UTF-8 length of the canonical rendering, so it counts keys,
    string content and structural overhead rather than the exact bytes any one
    serialiser would emit. It is deterministic and monotone in the content,
    which is what a budget needs.
    """

    return len(_canonical_text(payload).encode("utf-8"))


# --------------------------------------------------------------------------- #
# Triggers (R1)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class TriggerTypeDeclaration:
    """One trigger type a module supports, with the schema of its parameters.

    A module declares these in its manifest; configuration may only select a
    declared type and may only pass parameters this schema accepts, both
    checked at startup before any transport is opened (R1, AC2).
    """

    name: str
    parameter_schema: Mapping[str, Any]

    def __post_init__(self) -> None:
        _validate_dotted_name(
            self.name, "TriggerTypeDeclaration.name", minimum_segments=1
        )
        label = "TriggerTypeDeclaration.parameter_schema"
        validate_schema(self.parameter_schema, label=label)
        object.__setattr__(
            self, "parameter_schema", _frozen_mapping(self.parameter_schema, label)
        )

    def validate_parameters(self, parameters: Any, *, label: str = "parameters") -> None:
        """Raise :class:`ContractError` unless *parameters* match the schema."""

        validate_against_schema(parameters, self.parameter_schema, label=label)


@dataclass(frozen=True, slots=True)
class TriggerRule:
    """One parameterised rule inside a :class:`TriggerPolicy`."""

    type: str
    parameters: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_dotted_name(self.type, "TriggerRule.type", minimum_segments=1)
        object.__setattr__(
            self, "parameters", _frozen_mapping(self.parameters, "TriggerRule.parameters")
        )


@dataclass(frozen=True, slots=True)
class TriggerPolicy:
    """What configuration selects for one input and channel (R1).

    ``version`` is a content hash of the rules and their combination, not a
    caller-chosen label: two identical policies always carry the same version
    and any change to a rule changes it, so the version in an
    ``input.trigger.*`` trace identifies exactly what decided (R8). It may be
    omitted, and a value supplied — when a recorded policy is rebuilt — must be
    the hash of the content it is attached to.
    """

    rules: tuple[TriggerRule, ...]
    combination: str = "all_of"
    version: str | None = None

    def __post_init__(self) -> None:
        rules = self.rules
        if isinstance(rules, (str, TriggerRule)) or not isinstance(rules, Sequence):
            raise ContractError("TriggerPolicy.rules", "must be a sequence of TriggerRule")
        rules = tuple(rules)
        if not rules:
            raise ContractError("TriggerPolicy.rules", "must declare at least one rule")
        for index, rule in enumerate(rules):
            if not isinstance(rule, TriggerRule):
                raise ContractError(f"TriggerPolicy.rules[{index}]", "must be a TriggerRule")
        object.__setattr__(self, "rules", rules)

        if not isinstance(self.combination, str):
            raise ContractError(
                "TriggerPolicy.combination",
                f"must be a string, got {type(self.combination).__name__}",
            )
        if self.combination not in COMBINATION_OPERATORS:
            allowed = ", ".join(sorted(COMBINATION_OPERATORS))
            raise ContractError(
                "TriggerPolicy.combination",
                f"must be one of {allowed}, got {self.combination!r}",
            )

        computed = self._content_version()
        if self.version is None:
            object.__setattr__(self, "version", computed)
        else:
            _require_text(self.version, "TriggerPolicy.version")
            if self.version != computed:
                raise ContractError(
                    "TriggerPolicy.version",
                    f"must be the content hash {computed!r} or omitted, "
                    f"got {self.version!r}",
                )

    def _content_version(self) -> str:
        content = _canonical_text(
            {
                "combination": self.combination,
                "rules": [
                    {"type": rule.type, "parameters": rule.parameters}
                    for rule in self.rules
                ],
            }
        )
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        return digest[:POLICY_VERSION_LENGTH]


@dataclass(frozen=True, slots=True)
class TriggerSpec:
    """What an input module declares about its own triggering (R1).

    The types it supports with their parameter schemas, the combination
    operators it supports, and exactly one default policy for its input type.
    A module may declare nothing, in which case it has no default policy and no
    policy can be configured for it at all (AC2).
    """

    types: tuple[TriggerTypeDeclaration, ...]
    combinations: tuple[str, ...] = ()
    default_policy: TriggerPolicy | None = None

    def __post_init__(self) -> None:
        declarations = self.types
        if isinstance(declarations, TriggerTypeDeclaration) or not isinstance(
            declarations, Sequence
        ):
            raise ContractError(
                "TriggerSpec.types", "must be a sequence of TriggerTypeDeclaration"
            )
        declarations = tuple(declarations)
        seen: set[str] = set()
        for index, declaration in enumerate(declarations):
            location = f"TriggerSpec.types[{index}]"
            if not isinstance(declaration, TriggerTypeDeclaration):
                raise ContractError(location, "must be a TriggerTypeDeclaration")
            if declaration.name in seen:
                raise ContractError(location, f"duplicates type {declaration.name!r}")
            seen.add(declaration.name)
        object.__setattr__(self, "types", declarations)

        combinations = _unique_texts(self.combinations, "TriggerSpec.combinations")
        for index, operator in enumerate(combinations):
            if operator not in COMBINATION_OPERATORS:
                allowed = ", ".join(sorted(COMBINATION_OPERATORS))
                raise ContractError(
                    f"TriggerSpec.combinations[{index}]",
                    f"must be one of {allowed}, got {operator!r}",
                )
        object.__setattr__(self, "combinations", combinations)

        if not declarations:
            if combinations:
                raise ContractError(
                    "TriggerSpec.combinations",
                    "must be empty when no trigger type is declared",
                )
            if self.default_policy is not None:
                raise ContractError(
                    "TriggerSpec.default_policy",
                    "must be absent when no trigger type is declared",
                )
            return

        if self.default_policy is None:
            raise ContractError(
                "TriggerSpec.default_policy",
                "must declare exactly one default policy for the input type",
            )
        if not isinstance(self.default_policy, TriggerPolicy):
            raise ContractError("TriggerSpec.default_policy", "must be a TriggerPolicy")
        self.validate_policy(self.default_policy, label="TriggerSpec.default_policy")

    def declares(self, type_name: str) -> bool:
        """Whether *type_name* is one of the declared trigger types."""

        return any(declaration.name == type_name for declaration in self.types)

    def declaration(self, type_name: str) -> TriggerTypeDeclaration | None:
        """Return the declaration of *type_name*, or ``None``."""

        for declaration in self.types:
            if declaration.name == type_name:
                return declaration
        return None

    def validate_policy(self, policy: Any, *, label: str = "policy") -> None:
        """Raise :class:`ContractError` unless this module can honour *policy*.

        *label* prefixes every diagnostic so the caller can name the input the
        policy belongs to, as R1 requires. Rejected: a type this module does
        not declare, a parameter failing the declared schema, a combination
        operator this module does not declare, and any policy at all when the
        module declares no trigger type.
        """

        if not isinstance(policy, TriggerPolicy):
            raise ContractError(label, "must be a TriggerPolicy")

        if not self.types:
            raise ContractError(
                label, "declares no trigger type, so no policy can be configured"
            )

        if policy.combination not in self.combinations:
            declared = ", ".join(self.combinations) or "none"
            raise ContractError(
                f"{label}.combination",
                f"is not declared by the module; declared: {declared}",
            )

        for index, rule in enumerate(policy.rules):
            location = f"{label}.rules[{index}]"
            declaration = self.declaration(rule.type)
            if declaration is None:
                declared = ", ".join(sorted(d.name for d in self.types))
                raise ContractError(
                    f"{location}.type",
                    f"is not declared by the module; declared: {declared}",
                )
            declaration.validate_parameters(
                rule.parameters, label=f"{location}.parameters"
            )


# --------------------------------------------------------------------------- #
# Counters (R8, AC28)
# --------------------------------------------------------------------------- #


class Counters:
    """A closed registry of named counts exposed by supervision (R8).

    The seven counters R8 requires are pre-declared at construction, so a
    snapshot lists them all and is readable even before anything happened
    (AC28). The diagnostic :data:`COUNTER_LOST_TRACES` is pre-declared too and
    is reported alongside them without joining the required set.

    Incrementing an undeclared name raises rather than creating it, so a typo
    surfaces instead of quietly producing a counter nobody reads. A layer that
    needs its own count declares it explicitly with :meth:`declare`.
    """

    __slots__ = ("_values",)

    def __init__(self, names: Sequence[str] = ()) -> None:
        self._values: dict[str, int] = {
            name: 0 for name in (*REQUIRED_COUNTERS, *DIAGNOSTIC_COUNTERS)
        }
        for name in _unique_texts(names, "Counters.names"):
            self._values.setdefault(name, 0)

    def declare(self, name: str) -> None:
        """Register *name* at zero, keeping any count it already carries."""

        _require_text(name, "Counters.declare")
        self._values.setdefault(name, 0)

    def increment(self, name: str, amount: int = 1) -> int:
        """Add *amount* to the declared counter *name* and return its value."""

        _require_text(name, "Counters.increment")
        if name not in self._values:
            known = ", ".join(sorted(self._values))
            raise ContractError(
                "Counters.increment", f"unknown counter {name!r}; declared: {known}"
            )
        if not isinstance(amount, int) or isinstance(amount, bool):
            raise ContractError(
                "Counters.increment.amount",
                f"must be an integer, got {type(amount).__name__}",
            )
        if amount < 0:
            raise ContractError("Counters.increment.amount", "must not be negative")
        self._values[name] += amount
        return self._values[name]

    def get(self, name: str) -> int:
        """Return the value of the declared counter *name*."""

        _require_text(name, "Counters.get")
        if name not in self._values:
            known = ", ".join(sorted(self._values))
            raise ContractError(
                "Counters.get", f"unknown counter {name!r}; declared: {known}"
            )
        return self._values[name]

    def snapshot(self) -> Mapping[str, int]:
        """Return an immutable, name-ordered view of every declared count."""

        return MappingProxyType({name: self._values[name] for name in sorted(self._values)})

    def __contains__(self, name: object) -> bool:
        return name in self._values


# --------------------------------------------------------------------------- #
# Trace sanitisation (R8, AC29)
# --------------------------------------------------------------------------- #


def sanitize_trace(
    payload: Mapping[str, Any],
    *,
    secrets: Sequence[str] = (),
    max_bytes: int = TRACE_MAX_BYTES,
) -> dict[str, Any]:
    """Return a bounded, credential-free copy of a trace payload (R8, AC29).

    Three guarantees, in this order:

    ``prompt`` and ``reasoning`` are refused outright, at any depth, with a
    :class:`ContractError` naming the path. A trace carries decisions and
    correlation identifiers; a full prompt or a model's reasoning is not
    bounded content and has no place in one, so this is a rejection rather than
    a redaction.

    Every configured secret is redacted wherever it appears verbatim, in a
    string value or a key, whole or embedded. **The guarantee is exactly
    that: verbatim-substring redaction over the configured values.** A
    credential that reached the payload re-encoded — percent-escaped inside a
    URL, base64'd inside a header dump, split across two fields — is not
    matched by this pass and never will be. The real protection is upstream:
    traces carry summaries and identifiers, never raw transport payloads, so a
    credential has no path into one. This pass is the second line.

    The result is then fitted to *max_bytes*, measured by :func:`trace_size`,
    by capping every string value to a common byte length and, only if keys
    alone still exceed the budget, dropping the heaviest top-level entries.
    Anything shortened sets :data:`TRUNCATION_KEY` to ``True``, whose cost is
    always reserved from the budget.

    The returned structure is plain ``dict``, ``list`` and scalars, so it
    survives JSON serialisation by the audit writer unchanged.
    """

    values = _validated_secrets(secrets)
    budget = _validated_budget(max_bytes) - _TRUNCATION_MARKER_WEIGHT

    if not isinstance(payload, Mapping):
        raise ContractError("payload", f"must be a mapping, got {type(payload).__name__}")
    cleaned = _clean(payload, values, "payload")

    fitted, truncated = _fit(cleaned, budget)
    if truncated:
        fitted[TRUNCATION_KEY] = True
    return fitted


def _validated_secrets(secrets: Any) -> tuple[str, ...]:
    """Return the non-blank secret values, longest first.

    Longest first so that a secret containing another is redacted as a whole
    instead of leaving the remainder of the longer value in the trace. Blank
    entries are skipped: an unset credential in configuration is an empty
    string, and redacting it would replace every position of every string.
    """

    if isinstance(secrets, str) or not isinstance(secrets, Sequence):
        raise ContractError("secrets", "must be a sequence of strings")
    values: list[str] = []
    for index, secret in enumerate(secrets):
        if not isinstance(secret, str):
            raise ContractError(
                f"secrets[{index}]", f"must be a string, got {type(secret).__name__}"
            )
        if secret and secret not in values:
            values.append(secret)
    return tuple(sorted(values, key=len, reverse=True))


def _validated_budget(max_bytes: Any) -> int:
    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool):
        raise ContractError(
            "max_bytes", f"must be an integer, got {type(max_bytes).__name__}"
        )
    if max_bytes < TRACE_MIN_MAX_BYTES:
        raise ContractError("max_bytes", f"must be at least {TRACE_MIN_MAX_BYTES}")
    return max_bytes


def _redact(text: str, secrets: Sequence[str]) -> str:
    for secret in secrets:
        if secret in text:
            text = text.replace(secret, REDACTION_PLACEHOLDER)
    return text


def _clean(value: Any, secrets: Sequence[str], path: str) -> Any:
    """Copy *value* into plain JSON-ready data, redacted and reserved-key free."""

    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ContractError(
                    path, f"must use string keys, got {type(key).__name__}"
                )
            # The key is redacted before it is used anywhere, diagnostics
            # included: a path built from the raw key would carry a configured
            # credential out of the trace through the exception message.
            clean_key = _redact(key, secrets)
            if key in RESERVED_TRACE_KEYS:
                raise ContractError(
                    f"{path}.{clean_key}",
                    f"is a reserved key; a trace carries no {clean_key} content",
                )
            if clean_key in result:
                raise ContractError(
                    f"{path}.{clean_key}", "collides with another key once redacted"
                )
            result[clean_key] = _clean(item, secrets, f"{path}.{clean_key}")
        return result
    if isinstance(value, str):
        return _redact(value, secrets)
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, (list, tuple)):
        return [
            _clean(item, secrets, f"{path}[{index}]") for index, item in enumerate(value)
        ]
    # Anything else is rendered first, then redacted, so no object can carry a
    # secret into a trace through its repr.
    return _redact(str(value), secrets)


def _fit(cleaned: dict[str, Any], budget: int) -> tuple[dict[str, Any], bool]:
    """Return *cleaned* shortened to *budget* bytes and whether it was shortened."""

    if trace_size(cleaned) <= budget:
        return cleaned, False

    # trace_size is monotone in the string cap, so the largest acceptable cap
    # is found by bisection rather than by shrinking one string at a time.
    low, high = 0, _longest_string(cleaned)
    best: dict[str, Any] | None = None
    while low <= high:
        middle = (low + high) // 2
        candidate = _cap_strings(cleaned, middle)
        if trace_size(candidate) <= budget:
            best = candidate
            low = middle + 1
        else:
            high = middle - 1
    if best is not None:
        return best, True

    # Keys alone exceed the budget: drop the heaviest top-level entries, by key
    # for a stable order, until what is left fits. An empty mapping always does.
    result = _cap_strings(cleaned, 0)
    while result and trace_size(result) > budget:
        victim = max(result, key=lambda key: (trace_size({key: result[key]}), key))
        del result[victim]
    return result, True


def _longest_string(value: Any) -> int:
    if isinstance(value, str):
        return len(value.encode("utf-8"))
    if isinstance(value, Mapping):
        return max((_longest_string(item) for item in value.values()), default=0)
    if isinstance(value, list):
        return max((_longest_string(item) for item in value), default=0)
    return 0


def _cap_strings(value: Any, cap: int) -> Any:
    """Copy *value* with every string value cut to *cap* UTF-8 bytes."""

    if isinstance(value, str):
        data = value.encode("utf-8")
        if len(data) <= cap:
            return value
        return data[:cap].decode("utf-8", "ignore")
    if isinstance(value, Mapping):
        return {key: _cap_strings(item, cap) for key, item in value.items()}
    if isinstance(value, list):
        return [_cap_strings(item, cap) for item in value]
    return value


_TRUNCATION_MARKER_WEIGHT = trace_size({TRUNCATION_KEY: True}) - trace_size({})
"""Budget always reserved so the truncation marker itself never overflows."""
