"""``viewer_memory`` — one bounded memory file per viewer (phase 3 R3).

**The store.** :class:`MemoryStore` keeps one UTF-8 JSON file per
``(platform, channel_id, viewer_id)`` in the configured ``directory``. The
file name is :func:`memory_file_name`: the hexadecimal SHA-256 of the key
serialized as a compact JSON array (collision-free: the array's quoting
separates the three fields) plus ``.json``, so no identifier appears in a
name. A file holds ``format: 1``, the three key fields, ``display_name`` (at
most :data:`DISPLAY_NAME_MAX_CHARS` characters, cut on write), ``first_seen``,
``last_seen`` and ``last_used`` (UTC, the fixed format
:data:`TIMESTAMP_FORMAT`, so the strings compare as the instants do),
``use_count``, ``interactions`` and ``notes`` (each ``at``, ``viewer_text``
≤ 200 characters, optional ``reply_text`` ≤ 200, ``delivery``).

**Bounds.** A write drops the file's oldest notes until it holds at most
``max_notes`` notes and at most ``max_file_bytes`` bytes. If a lone newest
note still does not fit, the file keeps 0 notes; if the metadata alone does
not fit, ``display_name`` is shortened by whole characters from its end, and a
key too long to fit even then is refused (:class:`MemoryStoreError`) — the
file bound is absolute. Before a write that would make the file count exceed
``max_files`` or the total exceed ``max_total_bytes``, the candidates — every
memory file except the one being written — are deleted in ascending
(``last_used``, ``use_count``, ``first_seen``, file name) order until both
bounds hold after the write (``evicted``). A file whose deletion fails stays
indexed and counted; if the deletions that succeed cannot make both bounds
hold, the write is refused (:class:`MemoryStoreError`, carrying the
evictions it did make). The order is read from an
in-memory index of each file's metadata, rebuilt at prepare, so an eviction
sorts ``n`` entries without re-reading a file.

**Atomic write.** A temporary file in the same directory (a name no memory
file can have), ``fsync``, then ``os.replace``: a reader sees the old file or
the new one, never a part. A failure before the replace removes the
temporary file and leaves the old file intact.

**Retention.** A record whose ``last_seen`` is older than ``retention_days``
(on the injected wall clock) is never returned by :meth:`MemoryStore.read`,
and is deleted at prepare and at every sweep (``expired``).

**Prepare scan** (before readiness). Only names matching
:data:`MEMORY_FILE_PATTERN` are read; anything else is neither read, counted
nor deleted. A memory-named file that is oversized, unreadable, malformed or
stored under a name that is not its key's is deleted (``corrupt``); expired
files are deleted; then, if the retained files still exceed a bound, the
eviction runs with no target. From ``start_inputs`` (the module declares the
``input`` role for this one source) the sweep runs every
``sweep_interval_seconds`` as a supervised task, until ``stop_inputs`` — so a
shutdown never waits on it at the drain.

**Erasure.** A ``channel.chat.message`` of kind ``message`` whose text is
exactly ``forget_command`` (non-empty) and whose author is trusted — an
attested ``author.id`` outside the reserved ``:`` namespace — deletes that
author's file for the event's platform and channel (``erased``). The module
consumes the event itself, so this happens whatever the trigger decides.

**Facts.** Every eviction pass, expiry pass, corrupt pass and erasure
publishes one ``memory.removed`` fact carrying ``reason`` and ``count`` only —
no viewer identifier, no file name.

**Seams, for the tests.** ``_sleeper`` (the sweep's sleep, ``asyncio.sleep``
by default) and ``_wall_clock`` (epoch seconds, ``time.time`` by default) are
read from *settings* at ``activate`` and never from a configuration file.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import os
import re
import tempfile
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.contracts import EVENT_KIND_DEFAULT


MODULE_NAME = "viewer_memory"

#: The fact every removal pass publishes.
FACT_MEMORY_REMOVED = "memory.removed"
REASON_EVICTED = "evicted"
REASON_EXPIRED = "expired"
REASON_CORRUPT = "corrupt"
REASON_ERASED = "erased"

MEMORY_FORMAT = 1
MEMORY_FILE_PATTERN = re.compile(r"^[0-9a-f]{64}\.json$")
#: The pinned UTC format: fixed width, so the strings order as the instants.
TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"
_TIMESTAMP_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$")

DISPLAY_NAME_MAX_CHARS = 64
NOTE_TEXT_MAX_CHARS = 200
DELIVERY_VALUES = ("confirmed", "unconfirmed", "none")

DEFAULT_RETENTION_DAYS = 30
RETENTION_DAYS_BOUNDS = (1, 365)
DEFAULT_MAX_FILES = 1000
MAX_FILES_BOUNDS = (1, 100000)
DEFAULT_MAX_FILE_BYTES = 4096
MAX_FILE_BYTES_BOUNDS = (512, 65536)
DEFAULT_MAX_TOTAL_BYTES = 16777216
MAX_TOTAL_BYTES_CEILING = 1073741824
DEFAULT_MAX_NOTES = 10
MAX_NOTES_BOUNDS = (1, 100)
DEFAULT_SWEEP_INTERVAL_SECONDS = 3600
SWEEP_INTERVAL_BOUNDS = (60, 86400)
DEFAULT_FORGET_COMMAND = "!forgetme"

_SECONDS_PER_DAY = 86400
_TEMPORARY_PREFIX = ".viewer-memory-"
_TEMPORARY_SUFFIX = ".tmp"
_CHAT_EVENT = "channel.chat.message"
#: No platform author identifier contains it: reserved identities do (R6).
_RESERVED_SEPARATOR = ":"

_RECORD_KEYS = frozenset(
    {
        "format",
        "platform",
        "channel_id",
        "viewer_id",
        "display_name",
        "first_seen",
        "last_seen",
        "last_used",
        "use_count",
        "interactions",
        "notes",
    }
)
_NOTE_KEYS = frozenset({"at", "viewer_text", "reply_text", "delivery"})

# Setting names — referenced by name, never by value, in diagnostics.
_ACCEPTED_LIMITS_SETTING = "limits"
_SETTING_DIRECTORY = "directory"
_SETTING_RETENTION_DAYS = "retention_days"
_SETTING_MAX_FILES = "max_files"
_SETTING_MAX_FILE_BYTES = "max_file_bytes"
_SETTING_MAX_TOTAL_BYTES = "max_total_bytes"
_SETTING_MAX_NOTES = "max_notes"
_SETTING_SWEEP_INTERVAL = "sweep_interval_seconds"
_SETTING_FORGET_COMMAND = "forget_command"
_SETTINGS = frozenset(
    {
        _SETTING_DIRECTORY,
        _SETTING_RETENTION_DAYS,
        _SETTING_MAX_FILES,
        _SETTING_MAX_FILE_BYTES,
        _SETTING_MAX_TOTAL_BYTES,
        _SETTING_MAX_NOTES,
        _SETTING_SWEEP_INTERVAL,
        _SETTING_FORGET_COMMAND,
    }
)
_INTEGER_SETTINGS = (
    (_SETTING_RETENTION_DAYS, RETENTION_DAYS_BOUNDS),
    (_SETTING_MAX_FILES, MAX_FILES_BOUNDS),
    (_SETTING_MAX_FILE_BYTES, MAX_FILE_BYTES_BOUNDS),
    (_SETTING_MAX_TOTAL_BYTES, (MAX_FILE_BYTES_BOUNDS[0], MAX_TOTAL_BYTES_CEILING)),
    (_SETTING_MAX_NOTES, MAX_NOTES_BOUNDS),
    (_SETTING_SWEEP_INTERVAL, SWEEP_INTERVAL_BOUNDS),
)

_SEAM_SLEEPER = "_sleeper"
_SEAM_WALL_CLOCK = "_wall_clock"
_SEAMS = frozenset({_SEAM_SLEEPER, _SEAM_WALL_CLOCK})

Sleeper = Callable[[float], Awaitable[Any]]
WallClock = Callable[[], float]
MemoryKey = tuple[str, str, str]


class ViewerMemoryError(RuntimeError):
    """A setup failure whose message contains no configured value."""


class MemoryStoreError(RuntimeError):
    """A store operation that could not be carried out; value-free.

    ``removed`` holds the deletions the operation made before it gave up
    (``{reason: count}``), so their facts are still published.
    """

    def __init__(self, message: str, *, removed: Mapping[str, int] | None = None) -> None:
        super().__init__(message)
        self.removed: dict[str, int] = dict(removed or {})


# --------------------------------------------------------------------------- #
# Settings validation hook
# --------------------------------------------------------------------------- #


def validate_settings(settings: Any) -> list[str]:
    """Check this module's settings; return one diagnostic per offending field.

    This is the hook ``settings_validator`` in the manifest names. Each
    diagnostic names the module and the field and nothing else; an empty list
    means the settings are accepted. ``directory`` is a non-empty string;
    every bound is an integer in its range; ``max_total_bytes`` is at least
    the effective ``max_file_bytes`` (the cross-field bound);
    ``forget_command`` is a string without surrounding whitespace, empty to
    disable it. The reserved ``limits`` block is accepted and not inspected;
    any other key is refused by name.
    """

    if not isinstance(settings, Mapping):
        return [_setting_diagnostic("settings", "must be a mapping")]
    diagnostics: list[str] = []
    for field_name in settings:
        if (
            field_name not in _SETTINGS
            and field_name != _ACCEPTED_LIMITS_SETTING
            and field_name not in _SEAMS
        ):
            diagnostics.append(
                _setting_diagnostic(str(field_name), "is not a setting this module declares")
            )
    for seam in sorted(_SEAMS):
        if seam in settings and not callable(settings[seam]):
            diagnostics.append(_setting_diagnostic(seam, "must be callable"))
    directory = settings.get(_SETTING_DIRECTORY)
    if not isinstance(directory, str) or not directory.strip():
        diagnostics.append(_setting_diagnostic(_SETTING_DIRECTORY, "must be a non-empty path"))
    bounded_ok: dict[str, bool] = {}
    for field_name, (low, high) in _INTEGER_SETTINGS:
        ok = field_name not in settings or _is_int_within(settings[field_name], low, high)
        bounded_ok[field_name] = ok
        if not ok:
            diagnostics.append(
                _setting_diagnostic(field_name, f"must be an integer from {low} to {high}")
            )
    if bounded_ok[_SETTING_MAX_FILE_BYTES] and bounded_ok[_SETTING_MAX_TOTAL_BYTES]:
        file_bytes = settings.get(_SETTING_MAX_FILE_BYTES, DEFAULT_MAX_FILE_BYTES)
        total_bytes = settings.get(_SETTING_MAX_TOTAL_BYTES, DEFAULT_MAX_TOTAL_BYTES)
        if total_bytes < file_bytes:
            diagnostics.append(
                _setting_diagnostic(
                    _SETTING_MAX_TOTAL_BYTES, f"must be at least {_SETTING_MAX_FILE_BYTES}"
                )
            )
    if _SETTING_FORGET_COMMAND in settings:
        command = settings[_SETTING_FORGET_COMMAND]
        if not isinstance(command, str) or command != command.strip():
            diagnostics.append(
                _setting_diagnostic(
                    _SETTING_FORGET_COMMAND,
                    "must be a string without surrounding whitespace",
                )
            )
    return diagnostics


def _setting_diagnostic(field_name: str, reason: str) -> str:
    return f"module {MODULE_NAME!r}: field {field_name!r}: {reason}"


def _is_int_within(value: Any, low: int, high: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high


@dataclass(frozen=True, slots=True)
class MemorySettings:
    """The accepted settings, parsed once."""

    directory: Path
    retention_days: int = DEFAULT_RETENTION_DAYS
    max_files: int = DEFAULT_MAX_FILES
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES
    max_notes: int = DEFAULT_MAX_NOTES
    sweep_interval_seconds: int = DEFAULT_SWEEP_INTERVAL_SECONDS
    forget_command: str = DEFAULT_FORGET_COMMAND

    @classmethod
    def from_mapping(cls, settings: Mapping[str, Any]) -> "MemorySettings":
        diagnostics = validate_settings(settings)
        if diagnostics:
            raise ViewerMemoryError(
                f"{MODULE_NAME} configuration: settings were refused "
                f"({len(diagnostics)} diagnostics)"
            )
        return cls(
            directory=Path(settings[_SETTING_DIRECTORY]),
            retention_days=int(settings.get(_SETTING_RETENTION_DAYS, DEFAULT_RETENTION_DAYS)),
            max_files=int(settings.get(_SETTING_MAX_FILES, DEFAULT_MAX_FILES)),
            max_file_bytes=int(settings.get(_SETTING_MAX_FILE_BYTES, DEFAULT_MAX_FILE_BYTES)),
            max_total_bytes=int(settings.get(_SETTING_MAX_TOTAL_BYTES, DEFAULT_MAX_TOTAL_BYTES)),
            max_notes=int(settings.get(_SETTING_MAX_NOTES, DEFAULT_MAX_NOTES)),
            sweep_interval_seconds=int(
                settings.get(_SETTING_SWEEP_INTERVAL, DEFAULT_SWEEP_INTERVAL_SECONDS)
            ),
            forget_command=str(settings.get(_SETTING_FORGET_COMMAND, DEFAULT_FORGET_COMMAND)),
        )

    @property
    def retention_seconds(self) -> float:
        return float(self.retention_days * _SECONDS_PER_DAY)


# --------------------------------------------------------------------------- #
# Names, timestamps, serialization
# --------------------------------------------------------------------------- #


def memory_file_name(platform: str, channel_id: str, viewer_id: str) -> str:
    """The file name of a key: ``sha256(<compact JSON array>) + ".json"``."""

    serialized = json.dumps(
        [platform, channel_id, viewer_id], ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest() + ".json"


def format_timestamp(epoch_seconds: float) -> str:
    """*epoch_seconds* in the pinned UTC format :data:`TIMESTAMP_FORMAT`."""

    return datetime.fromtimestamp(float(epoch_seconds), tz=timezone.utc).strftime(
        TIMESTAMP_FORMAT
    )


def parse_timestamp(value: Any) -> float:
    """The epoch seconds of a pinned-format timestamp; ``ValueError`` otherwise."""

    if not isinstance(value, str) or not _TIMESTAMP_PATTERN.match(value):
        raise ValueError("timestamp")
    return datetime.strptime(value, TIMESTAMP_FORMAT).replace(tzinfo=timezone.utc).timestamp()


def _serialize(record: Mapping[str, Any]) -> bytes:
    return json.dumps(record, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _is_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value)


def _is_count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _valid_note(note: Any) -> bool:
    if not isinstance(note, Mapping) or not set(note) <= _NOTE_KEYS:
        return False
    try:
        parse_timestamp(note.get("at"))
    except ValueError:
        return False
    viewer_text = note.get("viewer_text")
    if not isinstance(viewer_text, str) or len(viewer_text) > NOTE_TEXT_MAX_CHARS:
        return False
    if "reply_text" in note:
        reply_text = note["reply_text"]
        if not isinstance(reply_text, str) or len(reply_text) > NOTE_TEXT_MAX_CHARS:
            return False
    return note.get("delivery") in DELIVERY_VALUES


def _parse_record(raw: bytes, name: str) -> dict[str, Any]:
    """The record *raw* holds, stored under *name*; ``ValueError`` when malformed."""

    try:
        record = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        # Nesting deep enough to exhaust the parser's recursion is malformed.
        raise ValueError("json") from None
    if not isinstance(record, dict) or set(record) != _RECORD_KEYS:
        raise ValueError("shape")
    if record["format"] != MEMORY_FORMAT or isinstance(record["format"], bool):
        raise ValueError("format")
    key = (record["platform"], record["channel_id"], record["viewer_id"])
    if not all(_is_text(part) for part in key) or memory_file_name(*key) != name:
        raise ValueError("key")
    display_name = record["display_name"]
    if not isinstance(display_name, str) or len(display_name) > DISPLAY_NAME_MAX_CHARS:
        raise ValueError("display_name")
    for field_name in ("first_seen", "last_seen", "last_used"):
        parse_timestamp(record[field_name])
    if not _is_count(record["use_count"]) or not _is_count(record["interactions"]):
        raise ValueError("counts")
    notes = record["notes"]
    if not isinstance(notes, list) or not all(_valid_note(note) for note in notes):
        raise ValueError("notes")
    return record


@dataclass(frozen=True, slots=True)
class _IndexEntry:
    """What eviction and the sweep need of one file, without reading it."""

    last_used: str
    use_count: int
    first_seen: str
    last_seen_epoch: float
    size: int

    @classmethod
    def of(cls, record: Mapping[str, Any], size: int) -> "_IndexEntry":
        return cls(
            last_used=record["last_used"],
            use_count=record["use_count"],
            first_seen=record["first_seen"],
            last_seen_epoch=parse_timestamp(record["last_seen"]),
            size=size,
        )


# --------------------------------------------------------------------------- #
# The store
# --------------------------------------------------------------------------- #


class MemoryStore:
    """The directory of memory files and its in-memory metadata index.

    Every operation is synchronous and returns the removals it made as
    ``{reason: count}`` (reasons with a count of 0 left out); the module turns
    each into one ``memory.removed`` fact. Nothing here awaits, so on one
    event loop no two operations interleave.
    """

    def __init__(self, settings: MemorySettings, *, wall_clock: WallClock = time.time) -> None:
        self._settings = settings
        self._directory = settings.directory
        self._wall_clock = wall_clock
        # ``file name → metadata`` of every memory file; rebuilt by :meth:`scan`.
        self._index: dict[str, _IndexEntry] = {}
        self._total_bytes = 0

    @property
    def settings(self) -> MemorySettings:
        return self._settings

    @property
    def directory(self) -> Path:
        return self._directory

    def stats(self) -> tuple[int, int]:
        """``(file count, total bytes)`` of the indexed memory files."""

        return len(self._index), self._total_bytes

    def indexed(self) -> tuple[str, ...]:
        """The indexed file names, sorted."""

        return tuple(sorted(self._index))

    def eviction_order(self) -> list[str]:
        """Every indexed name in eviction order (first is deleted first)."""

        return sorted(self._index, key=self._eviction_key)

    def _eviction_key(self, name: str) -> tuple[str, int, str, str]:
        entry = self._index[name]
        return (entry.last_used, entry.use_count, entry.first_seen, name)

    def path_of(self, platform: str, channel_id: str, viewer_id: str) -> Path:
        return self._directory / memory_file_name(platform, channel_id, viewer_id)

    # -- prepare scan and sweep --------------------------------------------- #

    def scan(self) -> dict[str, int]:
        """Rebuild the index; delete corrupt, then expired files; then evict.

        Only memory-named entries are looked at. The directory is created
        when absent.
        """

        self._directory.mkdir(parents=True, exist_ok=True)
        self._index.clear()
        self._total_bytes = 0
        removed = {REASON_CORRUPT: 0, REASON_EXPIRED: 0, REASON_EVICTED: 0}
        horizon = self._horizon()
        with os.scandir(self._directory) as entries:
            candidates = [entry for entry in entries if MEMORY_FILE_PATTERN.match(entry.name)]
        for entry in sorted(candidates, key=lambda item: item.name):
            if entry.is_dir(follow_symlinks=False):
                continue
            record, size = self._read_file(entry.name, symlink=entry.is_symlink())
            if record is None:
                removed[REASON_CORRUPT] += self._unlink(entry.name)
                continue
            index_entry = _IndexEntry.of(record, size)
            if index_entry.last_seen_epoch < horizon:
                removed[REASON_EXPIRED] += self._unlink(entry.name)
                continue
            self._index[entry.name] = index_entry
            self._total_bytes += size
        removed[REASON_EVICTED] = self._evict(target=None, new_size=0)
        return {reason: count for reason, count in removed.items() if count}

    def sweep(self) -> dict[str, int]:
        """Delete every indexed record past retention."""

        horizon = self._horizon()
        expired = [name for name, entry in self._index.items() if entry.last_seen_epoch < horizon]
        count = sum(self._delete(name) for name in sorted(expired))
        return {REASON_EXPIRED: count} if count else {}

    def _horizon(self) -> float:
        return float(self._wall_clock()) - self._settings.retention_seconds

    def _read_file(self, name: str, *, symlink: bool = False) -> tuple[dict[str, Any] | None, int]:
        """The valid record stored under *name* and its size, or ``(None, 0)``."""

        if symlink:
            return None, 0
        limit = self._settings.max_file_bytes
        try:
            with open(self._directory / name, "rb") as handle:
                raw = handle.read(limit + 1)
        except OSError:
            return None, 0
        if len(raw) > limit:
            return None, 0
        try:
            return _parse_record(raw, name), len(raw)
        except (ValueError, KeyError, TypeError):
            return None, 0

    # -- reads ---------------------------------------------------------------- #

    def read(self, platform: str, channel_id: str, viewer_id: str) -> dict[str, Any] | None:
        """The viewer's record, or ``None`` when absent, unreadable or expired.

        A read is not a use: it changes nothing on disk.
        """

        name = memory_file_name(platform, channel_id, viewer_id)
        if name not in self._index:
            return None
        record, _ = self._read_file(name)
        if record is None:
            return None
        if parse_timestamp(record["last_seen"]) < self._horizon():
            return None
        return record

    # -- writes --------------------------------------------------------------- #

    def record(
        self,
        platform: str,
        channel_id: str,
        viewer_id: str,
        *,
        display_name: str | None = None,
        note: Mapping[str, Any] | None = None,
    ) -> tuple[dict[str, Any], dict[str, int]]:
        """Create or update the viewer's file: one interaction, one use.

        ``interactions`` and ``use_count`` + 1, ``last_seen`` and
        ``last_used`` now, ``display_name`` replaced when given (cut to 64
        characters), *note* (``viewer_text``, optional ``reply_text``,
        ``delivery``) appended with ``at`` now. Returns the stored record and
        the removals the write needed.
        """

        key = (platform, channel_id, viewer_id)
        if not all(_is_text(part) for part in key):
            raise MemoryStoreError(f"{MODULE_NAME} record: the key is invalid")
        name = memory_file_name(*key)
        now = format_timestamp(self._wall_clock())
        existing = self.read(*key)
        if existing is None:
            record: dict[str, Any] = {
                "format": MEMORY_FORMAT,
                "platform": platform,
                "channel_id": channel_id,
                "viewer_id": viewer_id,
                "display_name": "",
                "first_seen": now,
                "last_seen": now,
                "last_used": now,
                "use_count": 0,
                "interactions": 0,
                "notes": [],
            }
        else:
            record = dict(existing)
            record["notes"] = list(existing["notes"])
        if display_name is not None:
            if not isinstance(display_name, str):
                raise MemoryStoreError(f"{MODULE_NAME} record: display_name is invalid")
            record["display_name"] = display_name[:DISPLAY_NAME_MAX_CHARS]
        record["last_seen"] = now
        record["last_used"] = now
        record["use_count"] += 1
        record["interactions"] += 1
        if note is not None:
            record["notes"].append(self._note(note, now))
        raw = self._fit(record)
        removed = self._write(name, record, raw)
        return record, removed

    def forget(self, platform: str, channel_id: str, viewer_id: str) -> int:
        """Delete the viewer's file; the number of files deleted (0 or 1)."""

        return self._delete(memory_file_name(platform, channel_id, viewer_id))

    def _note(self, note: Mapping[str, Any], at: str) -> dict[str, Any]:
        viewer_text = note.get("viewer_text")
        reply_text = note.get("reply_text")
        delivery = note.get("delivery")
        if (
            not isinstance(viewer_text, str)
            or len(viewer_text) > NOTE_TEXT_MAX_CHARS
            or (reply_text is not None
                and (not isinstance(reply_text, str) or len(reply_text) > NOTE_TEXT_MAX_CHARS))
            or delivery not in DELIVERY_VALUES
            or not set(note) <= _NOTE_KEYS - {"at"}
        ):
            raise MemoryStoreError(f"{MODULE_NAME} record: the note is invalid")
        stored: dict[str, Any] = {"at": at, "viewer_text": viewer_text}
        if reply_text is not None:
            stored["reply_text"] = reply_text
        stored["delivery"] = delivery
        return stored

    def _fit(self, record: dict[str, Any]) -> bytes:
        """Drop oldest notes, then shorten the name, until the file fits."""

        limit = self._settings.max_file_bytes
        notes: list[Any] = record["notes"]
        if len(notes) > self._settings.max_notes:
            del notes[: len(notes) - self._settings.max_notes]
        raw = _serialize(record)
        while len(raw) > limit and notes:
            # Oldest first; a lone newest note that does not fit goes too.
            del notes[0]
            raw = _serialize(record)
        while len(raw) > limit and record["display_name"]:
            record["display_name"] = record["display_name"][:-1]
            raw = _serialize(record)
        if len(raw) > limit:
            raise MemoryStoreError(f"{MODULE_NAME} record: the key does not fit max_file_bytes")
        return raw

    def _write(self, name: str, record: Mapping[str, Any], raw: bytes) -> dict[str, int]:
        evicted = self._evict(target=name, new_size=len(raw))
        self._atomic_write(name, raw)
        previous = self._index.pop(name, None)
        if previous is not None:
            self._total_bytes -= previous.size
        self._index[name] = _IndexEntry.of(record, len(raw))
        self._total_bytes += len(raw)
        return {REASON_EVICTED: evicted} if evicted else {}

    def _evict(self, *, target: str | None, new_size: int) -> int:
        """Delete candidates in eviction order until both bounds hold after the write."""

        settings = self._settings
        previous = self._index.get(target) if target is not None else None
        added_file = 1 if target is not None and previous is None else 0
        size_delta = new_size - (previous.size if previous is not None else 0)

        def over() -> bool:
            return (
                len(self._index) + added_file > settings.max_files
                or self._total_bytes + size_delta > settings.max_total_bytes
            )

        if not over():
            return 0
        count = 0
        for name in [name for name in self.eviction_order() if name != target]:
            if not over():
                break
            # A file that could not be removed stays indexed: it is still on
            # disk, counts toward both bounds and is retried by later passes.
            count += self._delete(name)
        if target is not None and over():
            raise MemoryStoreError(
                f"{MODULE_NAME} record: the bounds cannot be met",
                removed={REASON_EVICTED: count} if count else {},
            )
        return count

    def _atomic_write(self, name: str, raw: bytes) -> None:
        """Temporary file in the same directory, ``fsync``, ``os.replace``."""

        descriptor, temporary = tempfile.mkstemp(
            prefix=_TEMPORARY_PREFIX, suffix=_TEMPORARY_SUFFIX, dir=self._directory
        )
        try:
            try:
                view = memoryview(raw)
                while view:
                    written = os.write(descriptor, view)
                    view = view[written:]
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            os.replace(temporary, self._directory / name)
        except BaseException:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise
        self._sync_directory()

    def _sync_directory(self) -> None:
        """Persist the rename where the platform allows opening a directory."""

        try:
            descriptor = os.open(self._directory, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(descriptor)
        except OSError:
            pass
        finally:
            os.close(descriptor)

    def _delete(self, name: str) -> int:
        """Remove *name* from disk and index; 1 when a file was deleted."""

        deleted = self._unlink(name)
        if deleted or not (self._directory / name).exists():
            self._forget_index(name)
        return deleted

    def _forget_index(self, name: str) -> None:
        entry = self._index.pop(name, None)
        if entry is not None:
            self._total_bytes -= entry.size

    def _unlink(self, name: str) -> int:
        try:
            os.unlink(self._directory / name)
        except FileNotFoundError:
            return 0
        except OSError:
            return 0
        return 1


# --------------------------------------------------------------------------- #
# The handle
# --------------------------------------------------------------------------- #


class ViewerMemoryModule:
    """The v2 handle: scan at ``prepare``, sweep, erase on the chat command."""

    def __init__(
        self,
        context: Any,
        settings: MemorySettings,
        *,
        sleeper: Sleeper,
        wall_clock: WallClock,
    ) -> None:
        self._bus = context.bus
        self._supervision = getattr(context, "supervision", None)
        self._tasks = getattr(context, "tasks", None)
        self._settings = settings
        self._sleeper = sleeper
        self._store = MemoryStore(settings, wall_clock=wall_clock)
        self._sweep_task: Any = None
        self._prepared = False
        self._stopping = False
        self._closed = False
        # Value-free diagnostics of what could not be done (a failed deletion
        # pass, a lost fact); bounded to the newest few.
        self._diagnostics: list[str] = []

    @property
    def settings(self) -> MemorySettings:
        return self._settings

    @property
    def store(self) -> MemoryStore:
        return self._store

    @property
    def diagnostics(self) -> tuple[str, ...]:
        return tuple(self._diagnostics)

    # -- lifecycle hooks ---------------------------------------------------- #

    async def prepare(self) -> None:
        """Scan the directory, route the erasure command, start the sweep."""

        if self._prepared or self._closed:
            return
        try:
            removed = self._store.scan()
        except OSError:
            raise ViewerMemoryError(
                f"{MODULE_NAME} prepare: the memory directory could not be scanned"
            ) from None
        await self._publish_removed(removed)
        self._bus.subscribe(_CHAT_EVENT, self.handle_chat_message)
        self._prepared = True

    async def start_inputs(self) -> None:
        """After the readiness barrier: start the sweep, a supervised task."""

        if not self._prepared or self._closed or self._stopping or self._sweep_task is not None:
            return
        self._sweep_task = self._tasks.spawn(self._sweep_loop(), name=f"{MODULE_NAME}-sweep")

    async def stop_inputs(self) -> None:
        """Stop the sweep before the drain; the erasure command still works."""

        self._stopping = True
        await self._stop_sweep()

    async def close(self) -> None:
        """Stop the sweep; later chat messages are ignored."""

        if self._closed:
            return
        self._closed = True
        self._stopping = True
        await self._stop_sweep()

    async def _stop_sweep(self) -> None:
        task = self._sweep_task
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    # -- the sweep ------------------------------------------------------------ #

    async def _sweep_loop(self) -> None:
        interval = float(self._settings.sweep_interval_seconds)
        while not self._stopping:
            await self._sleeper(interval)
            if self._stopping:
                return
            await self.sweep()

    async def sweep(self) -> dict[str, int]:
        """One retention pass; publishes its ``expired`` fact."""

        try:
            removed = self._store.sweep()
        except OSError:
            self._diagnose("sweep: failed")
            return {}
        await self._publish_removed(removed)
        return removed

    # -- the store, with its facts ------------------------------------------ #

    def read(self, platform: str, channel_id: str, viewer_id: str) -> dict[str, Any] | None:
        return self._store.read(platform, channel_id, viewer_id)

    async def record(
        self,
        platform: str,
        channel_id: str,
        viewer_id: str,
        *,
        display_name: str | None = None,
        note: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """:meth:`MemoryStore.record`, then one fact for its eviction pass."""

        try:
            record, removed = self._store.record(
                platform, channel_id, viewer_id, display_name=display_name, note=note
            )
        except MemoryStoreError as error:
            await self._publish_removed(error.removed)
            raise
        await self._publish_removed(removed)
        return record

    async def forget(self, platform: str, channel_id: str, viewer_id: str) -> int:
        """Erase one viewer's file; one ``erased`` fact when a file went."""

        count = self._store.forget(platform, channel_id, viewer_id)
        await self._publish_removed({REASON_ERASED: count} if count else {})
        return count

    # -- the erasure command -------------------------------------------------- #

    async def handle_chat_message(self, event: Mapping[str, Any]) -> None:
        """Erase the author's file on the exact ``forget_command`` text.

        Never raises and never returns a replacement: this consumer sits in
        the publisher's chain and must not change or fail it.
        """

        if self._closed or not self._prepared:
            return
        command = self._settings.forget_command
        if not command:
            return
        key = _forget_key(event, command)
        if key is None:
            return
        try:
            await self.forget(*key)
        except Exception:  # noqa: BLE001 - a consumer must not fail the publisher
            self._diagnose("erasure: failed")

    # -- facts ---------------------------------------------------------------- #

    async def _publish_removed(self, removed: Mapping[str, int]) -> None:
        emit = getattr(self._supervision, "emit", None)
        if not callable(emit):
            return
        for reason, count in removed.items():
            if not count:
                continue
            try:
                outcome = emit(FACT_MEMORY_REMOVED, {"reason": reason, "count": count})
                if inspect.isawaitable(outcome):
                    await outcome
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - a lost fact must not undo a removal
                self._diagnose(f"fact {reason}: not published")

    def _diagnose(self, text: str) -> None:
        self._diagnostics.append(f"{MODULE_NAME} {text}")
        del self._diagnostics[:-16]


def _forget_key(event: Any, command: str) -> MemoryKey | None:
    """The key a chat event erases, or ``None`` when it erases nothing.

    Only an ordinary message (kind ``message``) whose text is exactly
    *command*, from an attested ``author.id`` outside the reserved ``:``
    namespace, on a named platform and channel.
    """

    if not isinstance(event, Mapping):
        return None
    payload = event.get("payload")
    if not isinstance(payload, Mapping):
        return None
    kind = payload.get("kind", EVENT_KIND_DEFAULT)
    if kind is not None and kind != EVENT_KIND_DEFAULT:
        return None
    if payload.get("text") != command:
        return None
    platform = payload.get("platform")
    channel_id = payload.get("channel_id")
    author = payload.get("author")
    viewer_id = author.get("id") if isinstance(author, Mapping) else None
    if not all(_is_text(part) for part in (platform, channel_id, viewer_id)):
        return None
    if _RESERVED_SEPARATOR in viewer_id:
        return None
    return platform, channel_id, viewer_id


# --------------------------------------------------------------------------- #
# Activation
# --------------------------------------------------------------------------- #


async def activate(context: Any, settings: Mapping[str, Any], catalog: Any) -> ViewerMemoryModule:
    """Validate the settings and build the handle; nothing is opened here."""

    del catalog  # Nothing is declared from the catalog.
    bus = getattr(context, "bus", None)
    tasks = getattr(context, "tasks", None)
    if not callable(getattr(bus, "subscribe", None)) or not callable(
        getattr(tasks, "spawn", None)
    ):
        raise ViewerMemoryError(f"{MODULE_NAME} activation: runtime context is invalid")
    if not isinstance(settings, Mapping):
        raise ViewerMemoryError(f"{MODULE_NAME} configuration: settings must be a mapping")
    parsed = MemorySettings.from_mapping(settings)
    return ViewerMemoryModule(
        context,
        parsed,
        sleeper=settings.get(_SEAM_SLEEPER, asyncio.sleep),
        wall_clock=settings.get(_SEAM_WALL_CLOCK, time.time),
    )


__all__ = [
    "DEFAULT_FORGET_COMMAND",
    "DEFAULT_MAX_FILE_BYTES",
    "DEFAULT_MAX_FILES",
    "DEFAULT_MAX_NOTES",
    "DEFAULT_MAX_TOTAL_BYTES",
    "DEFAULT_RETENTION_DAYS",
    "DEFAULT_SWEEP_INTERVAL_SECONDS",
    "DELIVERY_VALUES",
    "DISPLAY_NAME_MAX_CHARS",
    "FACT_MEMORY_REMOVED",
    "MEMORY_FILE_PATTERN",
    "MEMORY_FORMAT",
    "MODULE_NAME",
    "NOTE_TEXT_MAX_CHARS",
    "REASON_CORRUPT",
    "REASON_ERASED",
    "REASON_EVICTED",
    "REASON_EXPIRED",
    "TIMESTAMP_FORMAT",
    "MemorySettings",
    "MemoryStore",
    "MemoryStoreError",
    "ViewerMemoryError",
    "ViewerMemoryModule",
    "activate",
    "format_timestamp",
    "memory_file_name",
    "parse_timestamp",
    "validate_settings",
]
