"""Bounded per-channel chat transcript context (R3, R6).

The chat context records what was said in a channel. It is keyed by
``(platform, channel_id)`` only — never by :class:`~core.contracts.SessionKey`
— because a transcript belongs to the channel, not to a viewer.

**This is transcript context and it must never be used as conversation
memory.** Conversation memory is the model-facing history of a viewer's
exchanges with the companion: it lives in the brain, is keyed by
:class:`~core.contracts.SessionKey` and is therefore never shared across
platforms, channels or viewers (R3). Feeding this transcript into a model
request as if it were memory would hand one viewer's exchanges to another
viewer's run inside the same channel. The two stores answer two different
questions — "what was said in this channel" versus "what this viewer and the
companion said to each other" — and neither may stand in for the other.

:meth:`ChatContext.append` is synchronous and total: ingestion calls it after
normalisation and *before* trigger evaluation, so a message is recorded whether
its trigger rejects it, admission refuses it, or its run ends in ``stale_drop``
(AC11). It never awaits, so no ingestion path can be tempted to skip it.

Retention is bounded per key on the three axes named by the specification —
``max_messages``, ``max_bytes`` and ``max_age_seconds`` — and the number of
live keys is itself capped by ``max_channels``, so a stream carrying one new
channel per event cannot grow the store without limit. Saturation on any axis
evicts the oldest entries of that key first, and the key cap evicts the least
recently appended channel as a whole. Every bound is required and validated as
a finite positive number at construction, raising a
:class:`~core.contracts.ContractError` naming the offending setting so that
``core.main`` can surface it as a startup diagnostic (R6).

The module performs no I/O, uses no asyncio and reads time only through the
injected clock.
"""

from __future__ import annotations

import math
import time
from collections import OrderedDict, deque
from collections.abc import Callable
from dataclasses import dataclass, field
from itertools import islice
from typing import Any

from .contracts import ContractError, SessionKey

__all__ = [
    "DEFAULT_MAX_CHANNELS",
    "ChatContext",
    "ChatEntry",
    "ChatRecord",
]

Clock = Callable[[], float]

DEFAULT_MAX_CHANNELS = 256
"""Live ``(platform, channel_id)`` keys retained when no cap is configured.

The three per-key bounds are required because the specification names them;
this fourth one guards the key space itself, so it carries a finite default
rather than forcing every caller to know it exists.
"""


@dataclass(frozen=True, slots=True)
class ChatEntry:
    """One normalised chat message offered to the context.

    ``author_id`` and ``message_id`` come from normalisation and must identify
    the message. ``text`` may be empty: a message carrying only an attachment
    is still part of the transcript, and refusing it would make :meth:`append`
    conditional on content it does not own.
    """

    author_id: str
    message_id: str
    text: str

    def __post_init__(self) -> None:
        _require_text(self.author_id, "ChatEntry.author_id")
        _require_text(self.message_id, "ChatEntry.message_id")
        if not isinstance(self.text, str):
            raise ContractError(
                "ChatEntry.text", f"must be a string, got {type(self.text).__name__}"
            )


@dataclass(frozen=True, slots=True)
class ChatRecord:
    """A retained entry, dated by the context clock at ingestion.

    Reads return these, so a caller always sees when a message was recorded
    rather than having to infer it from position.
    """

    timestamp: float
    author_id: str
    message_id: str
    text: str


@dataclass(slots=True)
class _Retained:
    """One record with the byte weight measured once, at append."""

    record: ChatRecord
    size: int


@dataclass(slots=True)
class _ChannelState:
    """The transcript of a single ``(platform, channel_id)`` key."""

    entries: deque[_Retained] = field(default_factory=deque)
    size: int = 0


class ChatContext:
    """Bounded, per-channel transcript of ingested chat messages.

    Never a conversation memory: see the module docstring.
    """

    def __init__(
        self,
        *,
        max_messages: int,
        max_bytes: int,
        max_age_seconds: float,
        max_channels: int = DEFAULT_MAX_CHANNELS,
        clock: Clock = time.monotonic,
    ) -> None:
        """Build a context whose retention is bounded on every axis.

        All three per-key bounds are required and validated here as finite
        positive numbers; an absent (``None``), non-numeric or non-finite value
        raises a :class:`~core.contracts.ContractError` naming the setting
        (R6). ``max_channels`` caps the number of live keys with the same
        validation. ``clock`` is injected so that age eviction is testable and
        never reads the wall clock behind the caller's back.
        """

        self._max_messages = _validate_count_limit(max_messages, "max_messages")
        self._max_bytes = _validate_count_limit(max_bytes, "max_bytes")
        self._max_age_seconds = _validate_duration_limit(
            max_age_seconds, "max_age_seconds"
        )
        self._max_channels = _validate_count_limit(max_channels, "max_channels")
        if not callable(clock):
            raise ContractError("clock", "must be callable")
        self._clock = clock

        # Ordered least-recently-appended first, so the key cap evicts the
        # channel that has been silent longest.
        self._channels: OrderedDict[tuple[str, str], _ChannelState] = OrderedDict()

    def append(self, platform: str, channel_id: str, entry: ChatEntry) -> ChatRecord:
        """Record *entry* in the transcript of ``(platform, channel_id)``.

        Synchronous and total: ingestion calls this after normalisation and
        before trigger evaluation, so the message is recorded whatever the
        pipeline decides afterwards — trigger rejection, admission rejection or
        ``stale_drop`` (AC11). The entry is dated with the injected clock and
        the dated record is returned.

        Appending then evicts the oldest entries of this key until the count,
        byte and age bounds all hold, and evicts whole least-recently-appended
        channels until the key cap holds. Saturation therefore drops history,
        never the message being ingested — unless that message alone exceeds
        ``max_bytes``, in which case the bound wins and the key is left empty:
        the store is bounded before it is complete.
        """

        key = self._key(platform, channel_id)
        if not isinstance(entry, ChatEntry):
            raise ContractError(
                "ChatContext.append.entry",
                f"must be a ChatEntry, got {type(entry).__name__}",
            )

        record = ChatRecord(
            timestamp=self._clock(),
            author_id=entry.author_id,
            message_id=entry.message_id,
            text=entry.text,
        )

        state = self._channels.get(key)
        if state is None:
            state = _ChannelState()
            self._channels[key] = state
        else:
            self._channels.move_to_end(key)

        retained = _Retained(record=record, size=_record_bytes(record))
        state.entries.append(retained)
        state.size += retained.size

        self._evict(key, state)
        self._evict_channels()
        return record

    def read(
        self, platform: str, channel_id: str, *, limit: int
    ) -> tuple[ChatRecord, ...]:
        """Return at most *limit* dated records for one channel, oldest first.

        The most recent *limit* entries are returned in ingestion order, each
        carrying the timestamp it was recorded with. A key that was never
        appended to — any other ``(platform, channel_id)`` — returns an empty
        tuple: transcripts never cross channels.

        Age eviction is re-applied here, so a read alone can never return an
        entry that has outlived ``max_age_seconds``. Reading does not count as
        activity for the key cap, which tracks the least recently *appended*
        channel.
        """

        key = self._key(platform, channel_id)
        limit = _validate_read_limit(limit)

        state = self._channels.get(key)
        if state is None:
            return ()

        self._evict(key, state)
        entries = state.entries
        if not entries or limit == 0:
            return ()

        if limit >= len(entries):
            selected: Any = entries
        else:
            selected = islice(entries, len(entries) - limit, None)
        return tuple(item.record for item in selected)

    def live_channels(self) -> tuple[tuple[str, str], ...]:
        """Return the retained keys, least recently appended first."""

        return tuple(self._channels)

    def _key(self, platform: Any, channel_id: Any) -> tuple[str, str]:
        """Validate and return the ``(platform, channel_id)`` key.

        A :class:`~core.contracts.SessionKey` is refused by name rather than
        silently stringified: it identifies conversation memory in the brain,
        and keying a transcript by it would both fragment the channel history
        and invite the store to be mistaken for memory (R3).
        """

        for value, field_name in (
            (platform, "ChatContext.platform"),
            (channel_id, "ChatContext.channel_id"),
        ):
            if isinstance(value, SessionKey):
                raise ContractError(
                    field_name,
                    "must be a plain identifier; a SessionKey keys conversation "
                    "memory in the brain, never the chat context",
                )
        return (
            _require_text(platform, "ChatContext.platform"),
            _require_text(channel_id, "ChatContext.channel_id"),
        )

    def _evict(self, key: tuple[str, str], state: _ChannelState) -> None:
        """Drop this key's oldest entries until count, byte and age all hold."""

        horizon = self._clock() - self._max_age_seconds
        entries = state.entries
        while entries and (
            len(entries) > self._max_messages
            or state.size > self._max_bytes
            or entries[0].record.timestamp < horizon
        ):
            state.size -= entries.popleft().size

        if not entries:
            # An emptied key holds nothing to read; keeping it would let a
            # burst of dead channels occupy the key cap against live ones.
            self._channels.pop(key, None)

    def _evict_channels(self) -> None:
        """Drop whole channels, least recently appended first, until capped."""

        channels = self._channels
        while len(channels) > self._max_channels:
            channels.popitem(last=False)


def _require_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise ContractError(
            field_name, f"must be a string, got {type(value).__name__}"
        )
    if not value.strip():
        raise ContractError(field_name, "must not be empty")
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


def _validate_read_limit(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ContractError(
            "ChatContext.read.limit", f"must be an integer, got {type(value).__name__}"
        )
    if value < 0:
        raise ContractError(
            "ChatContext.read.limit", f"must not be negative, got {value!r}"
        )
    return value


def _record_bytes(record: ChatRecord) -> int:
    """Return the UTF-8 weight of the strings this record keeps retained."""

    return (
        len(record.author_id.encode("utf-8", "surrogatepass"))
        + len(record.message_id.encode("utf-8", "surrogatepass"))
        + len(record.text.encode("utf-8", "surrogatepass"))
    )
