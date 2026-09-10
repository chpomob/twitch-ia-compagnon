"""Asynchronous, ordered event publication with wildcard subscriptions."""

from __future__ import annotations

import inspect
import json
import math
import sys
import time
from collections import deque
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from functools import lru_cache, partial
from typing import Any

Event = dict[str, Any]
Handler = Callable[[Event], Any]
Clock = Callable[[], float]

#: Retention defaults applied when a caller configures no explicit bound. They
#: are finite by construction; ``core.main`` overrides them from configuration
#: and a non-finite or absent configured value is rejected here (R6).
DEFAULT_HISTORY_MAX_EVENTS = 1000
DEFAULT_HISTORY_MAX_BYTES = 8 * 1024 * 1024
DEFAULT_HISTORY_MAX_AGE_SECONDS = 3600.0


@lru_cache(maxsize=4096)
def matches_event_pattern(pattern: str, event_type: str) -> bool:
    """Return whether a concrete event type matches a subscription pattern."""

    return _matches(_validate_pattern(pattern), _validate_event_type(event_type))


class PublicationError(RuntimeError):
    """Raised when a subscriber fails while handling a publication."""

    def __init__(
        self,
        event_type: str,
        handler: Handler,
        handler_identity: str,
        cause: Exception,
    ) -> None:
        self.event_type = event_type
        self.handler = handler
        self.handler_identity = handler_identity
        self.cause = cause
        super().__init__(
            f"Publication of {event_type!r} failed in handler "
            f"{handler_identity!r}: {cause}"
        )


@dataclass(frozen=True, slots=True)
class _HistoryRecord:
    """One retained publication with the retention keys measured at append."""

    event: Event
    timestamp: float
    size: int


@dataclass(frozen=True, slots=True)
class _Subscription:
    pattern: tuple[str, ...]
    handler: Handler
    order: int
    registration: int


class EventBus:
    """Publish events through a deterministic chain of matching handlers.

    The publication history is bounded; see :meth:`__init__` for the bounds and
    :meth:`_retain` for the eviction point.
    """

    def __init__(
        self,
        *,
        history_max_events: int = DEFAULT_HISTORY_MAX_EVENTS,
        history_max_bytes: int = DEFAULT_HISTORY_MAX_BYTES,
        history_max_age_seconds: float = DEFAULT_HISTORY_MAX_AGE_SECONDS,
        clock: Clock = time.monotonic,
    ) -> None:
        """Build a bus whose publication history is bounded on three axes.

        The publication history is bounded by event count, total bytes and age;
        saturation evicts the oldest records rather than refusing or blocking a
        publication (R6). Every bound must be a finite, positive number: an
        absent (``None``) or non-finite value raises here, naming the setting,
        so that ``core.main`` can surface it as a startup diagnostic.
        """

        self._history_max_events = _validate_count_limit(
            history_max_events, "history_max_events"
        )
        self._history_max_bytes = _validate_count_limit(
            history_max_bytes, "history_max_bytes"
        )
        self._history_max_age_seconds = _validate_duration_limit(
            history_max_age_seconds, "history_max_age_seconds"
        )
        if not callable(clock):
            raise TypeError("clock must be callable")
        self._clock = clock

        self._subscriptions: list[_Subscription] = []
        self._history: deque[_HistoryRecord] = deque()
        self._history_bytes = 0
        self._next_registration = 0

    def subscribe(self, pattern: str, handler: Handler, order: int = 0) -> None:
        """Register *handler* for *pattern* at the requested chain order.

        ``*`` consumes one event-type segment and ``**`` consumes zero or more.
        Equal-order subscriptions retain their registration order.
        """

        segments = _validate_pattern(pattern)
        if not callable(handler):
            raise TypeError("handler must be callable")
        if not isinstance(order, int) or isinstance(order, bool):
            raise TypeError("order must be an integer")

        subscription = _Subscription(
            pattern=segments,
            handler=handler,
            order=order,
            registration=self._next_registration,
        )
        self._next_registration += 1
        self._subscriptions.append(subscription)

    async def publish(
        self,
        event_type: str,
        payload: Mapping[str, Any],
        metadata: Mapping[str, Any],
    ) -> Event:
        """Publish an event and return the finalized event record.

        The matching chain is selected from the original event type. A mapping
        returned by a handler is a complete replacement; the literal ``False``
        stops the chain and records the event without invoking later handlers.
        Failed publications are not added to history.

        Retention is applied only once the chain has returned, so a saturated
        history evicts older records instead of failing this publication.
        """

        event_segments = _validate_event_type(event_type)
        _validate_event_fields(payload, metadata)
        event: Event = {
            "type": event_type,
            "payload": payload,
            "metadata": metadata,
        }

        chain = sorted(
            (
                subscription
                for subscription in self._subscriptions
                if _matches(subscription.pattern, event_segments)
            ),
            key=lambda subscription: (
                subscription.order,
                subscription.registration,
            ),
        )

        for subscription in chain:
            try:
                result = subscription.handler(event)
                if inspect.isawaitable(result):
                    result = await result

                if result is False:
                    break
                if isinstance(result, Mapping):
                    event = _validated_replacement(result)
            except Exception as exc:
                raise PublicationError(
                    event_type=event_type,
                    handler=subscription.handler,
                    handler_identity=_handler_identity(subscription.handler),
                    cause=exc,
                ) from exc

        self._retain(event)
        return event

    def list_events(self) -> list[Event]:
        """Return the retained records, oldest first, in publication order.

        Age eviction is re-applied here so that a record which became stale
        since the last publication is never returned.
        """

        self._evict()
        return [deepcopy(record.event) for record in self._history]

    def _retain(self, event: Event) -> None:
        """Append *event* to the history, then evict until every bound holds.

        Called strictly after the handler chain has run, so a saturated history
        can never fail a publication (AC20). The byte size is measured once,
        here, and cached on the record rather than recomputed on every
        eviction pass.
        """

        retained = deepcopy(event)
        record = _HistoryRecord(
            event=retained,
            timestamp=self._clock(),
            size=_measure_event_bytes(retained),
        )
        self._history.append(record)
        self._history_bytes += record.size
        self._evict()

    def _evict(self) -> None:
        """Drop the oldest records until count, byte and age bounds all hold."""

        horizon = self._clock() - self._history_max_age_seconds
        history = self._history
        while history and (
            len(history) > self._history_max_events
            or self._history_bytes > self._history_max_bytes
            or history[0].timestamp < horizon
        ):
            self._history_bytes -= history.popleft().size


def _split_dotted(value: str, *, label: str) -> tuple[str, ...]:
    if not isinstance(value, str):
        raise TypeError(f"{label} must be a string")
    if not value:
        raise ValueError(f"{label} must not be empty")

    segments = tuple(value.split("."))
    if any(not segment for segment in segments):
        raise ValueError(f"{label} must contain no empty segments")
    return segments


def _validate_event_type(event_type: str) -> tuple[str, ...]:
    segments = _split_dotted(event_type, label="event type")
    if any("*" in segment for segment in segments):
        raise ValueError("event type must not contain wildcard segments")
    return segments


def _validate_pattern(pattern: str) -> tuple[str, ...]:
    segments = _split_dotted(pattern, label="pattern")
    if any("*" in segment and segment not in {"*", "**"} for segment in segments):
        raise ValueError("wildcards must occupy a complete pattern segment")
    return segments


def _validate_event_fields(
    payload: Any,
    metadata: Any,
) -> None:
    if not isinstance(payload, Mapping):
        raise TypeError("event payload must be a mapping")
    if not isinstance(metadata, Mapping):
        raise TypeError("event metadata must be a mapping")


def _validate_count_limit(value: Any, setting: str) -> int:
    if value is None:
        raise ValueError(f"{setting} must be configured with a finite positive value")
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{setting} must be an integer")
    if value < 1:
        raise ValueError(f"{setting} must be a positive integer, got {value!r}")
    return value


def _validate_duration_limit(value: Any, setting: str) -> float:
    if value is None:
        raise ValueError(f"{setting} must be configured with a finite positive value")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{setting} must be a number")
    limit = float(value)
    if not math.isfinite(limit):
        raise ValueError(f"{setting} must be finite, got {value!r}")
    if limit <= 0:
        raise ValueError(f"{setting} must be strictly positive, got {value!r}")
    return limit


def _slot_names(cls: type) -> tuple[str, ...]:
    """Return every ``__slots__`` name declared along *cls*'s resolution order."""

    names: list[str] = []
    for base in cls.__mro__:
        slots = base.__dict__.get("__slots__")
        if slots is None:
            continue
        if isinstance(slots, str):
            names.append(slots)
        else:
            names.extend(slots)
    return tuple(names)


def _retained_size(value: Any, seen: set[int]) -> int:
    """Return the memory retained by *value*'s object graph, counting once.

    Traversal covers mappings, sequences, sets and the attributes an ordinary
    object holds in ``__dict__`` or in slots, so a buffer reachable only
    through an attribute is charged for what it retains. *seen* carries object
    identity across calls, which both terminates cycles and prevents charging a
    shared object twice within the same event.
    """

    total = 0
    stack: list[Any] = [value]
    while stack:
        current = stack.pop()
        marker = id(current)
        if marker in seen:
            continue
        seen.add(marker)

        try:
            total += sys.getsizeof(current)
        except TypeError:  # pragma: no cover - objects without a reportable size
            continue

        if isinstance(current, memoryview):
            # getsizeof reports the view, not the buffer it keeps alive.
            total += current.nbytes
            continue
        if isinstance(current, (str, bytes, bytearray)):
            continue
        if isinstance(current, Mapping):
            stack.extend(current.keys())
            stack.extend(current.values())
            continue
        if isinstance(current, (list, tuple, set, frozenset, deque)):
            stack.extend(current)
            continue

        attributes = getattr(current, "__dict__", None)
        if isinstance(attributes, Mapping):
            stack.extend(attributes.values())
        for slot in _slot_names(type(current)):
            try:
                stack.append(getattr(current, slot))
            except AttributeError:
                continue
    return total


def _measure_event_bytes(event: Event) -> int:
    """Return the retained byte weight of *event*, measured once at append.

    JSON-representable structure is charged its serialized size. A value JSON
    cannot represent is charged what its retained object graph actually
    occupies instead of the length of its ``repr``: the history keeps a deep
    copy of that value, so a payload hiding a large buffer behind an ordinary
    object must count against ``history_max_bytes`` rather than slip past it as
    a short ``<... object at 0x...>`` string.
    """

    seen: set[int] = set()
    opaque_bytes = 0

    def measure_opaque(value: Any) -> str:
        nonlocal opaque_bytes
        opaque_bytes += _retained_size(value, seen)
        # A terminal placeholder: the weight is accounted just above, so the
        # serialized text must not charge for the value a second time.
        return ""

    try:
        serialized = json.dumps(event, default=measure_opaque, ensure_ascii=False)
    except (TypeError, ValueError, RecursionError):
        # Circular or otherwise unserializable structure: fall back to a full
        # traversal, which still measures the contents that stay retained.
        return opaque_bytes + _retained_size(event, seen)
    return opaque_bytes + len(serialized.encode("utf-8", "surrogatepass"))


def _validated_replacement(replacement: Mapping[str, Any]) -> Event:
    required = {"type", "payload", "metadata"}
    missing = required.difference(replacement)
    if missing:
        fields = ", ".join(sorted(missing))
        raise ValueError(f"event replacement is missing required field(s): {fields}")

    _validate_event_type(replacement["type"])
    _validate_event_fields(replacement["payload"], replacement["metadata"])
    return dict(replacement)


def _handler_identity(handler: Handler) -> str:
    if isinstance(handler, partial):
        target = _handler_identity(handler.func)
        return f"functools.partial({target})@0x{id(handler):x}"

    module = getattr(handler, "__module__", handler.__class__.__module__)
    name = getattr(handler, "__qualname__", None)
    if name is not None:
        return f"{module}.{name}"

    name = getattr(handler, "__name__", None)
    if name is not None:
        return f"{module}.{name}"

    handler_type = handler.__class__
    return (
        f"{handler_type.__module__}.{handler_type.__qualname__}"
        f"@0x{id(handler):x}"
    )


@lru_cache(maxsize=4096)
def _matches(pattern: tuple[str, ...], event: tuple[str, ...]) -> bool:
    reachable = {0}
    for event_segment in event:
        while reachable:
            expanded = reachable | {
                index + 1
                for index in reachable
                if index < len(pattern) and pattern[index] == "**"
            }
            if expanded == reachable:
                break
            reachable = expanded

        reachable = {
            index + 1
            for index in reachable
            if index < len(pattern)
            and pattern[index] in {"*", "**", event_segment}
        } | {
            index
            for index in reachable
            if index < len(pattern) and pattern[index] == "**"
        }

    while reachable:
        expanded = reachable | {
            index + 1
            for index in reachable
            if index < len(pattern) and pattern[index] == "**"
        }
        if expanded == reachable:
            return len(pattern) in reachable
        reachable = expanded

    return False
