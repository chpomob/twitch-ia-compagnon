"""Versioned, dependency-free identity and action contracts.

Every layer of the runtime imports this module and this module imports nothing
from the project, so it can carry the vocabulary shared by triggers, admission,
the action registry and the executor without creating an import cycle.

Contracts validate their own fields at construction and raise :class:`ContractError`
naming the offending field. The module performs no I/O and uses no asyncio.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
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

__all__ = [
    "ACTION_NATURES",
    "CONTRACT_VERSION",
    "IDEMPOTENCY_POLICIES",
    "SUPPORTED_CONTRACT_VERSIONS",
    "TERMINAL_STATUSES",
    "WILDCARD",
    "ActionCall",
    "ActionObservation",
    "ActionSpec",
    "ContractError",
    "Destination",
    "SchemaError",
    "SessionKey",
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


def _validate_action_name(value: Any, field: str) -> None:
    _require_text(value, field)
    segments = value.split(".")
    if len(segments) < 2:
        raise ContractError(field, f"must be a dotted name, got {value!r}")
    for segment in segments:
        if not segment or not segment[0].isalpha():
            raise ContractError(field, f"has an invalid segment in {value!r}")
        if not all(char.isalnum() or char == "_" for char in segment):
            raise ContractError(field, f"has an invalid segment in {value!r}")


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
