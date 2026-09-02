"""Asynchronous, ordered event publication with wildcard subscriptions."""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from functools import lru_cache, partial
from typing import Any

Event = dict[str, Any]
Handler = Callable[[Event], Any]


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
class _Subscription:
    pattern: tuple[str, ...]
    handler: Handler
    order: int
    registration: int


class EventBus:
    """Publish events through a deterministic chain of matching handlers."""

    def __init__(self) -> None:
        self._subscriptions: list[_Subscription] = []
        self._events: list[Event] = []
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

        self._events.append(deepcopy(event))
        return event

    def list_events(self) -> list[Event]:
        """Return finalized records for all successful or stopped publications."""

        return deepcopy(self._events)


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
