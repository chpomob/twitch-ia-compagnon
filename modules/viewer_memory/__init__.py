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

**Actions** (R4). ``memory.recall`` (read) and ``memory.record`` (write, not
model-proposable, no delivery) are bound at ``prepare`` over the declared
``*/*/memory`` destinations. Their viewer is the run's session viewer: the
call's ``conversation_id`` — the brain passes ``SessionKey.serialize()`` — is
parsed back into ``(platform, channel_id, viewer_id)`` by
:func:`parse_conversation_id`. An unparsable id, a viewer in the reserved
``:`` namespace (``system:watch``) or a key naming another platform or
channel than the destination ends ``error no_viewer``. No argument names a
viewer: ``memory.recall`` takes none and ``memory.record`` only the exchange
data, both with ``additionalProperties: false``, so the executor refuses an
identity argument before the provider runs.

A recall of an absent or expired record is ``{known: false}``. Otherwise it
is ``known``, ``display_name``, ``first_seen``, ``last_seen``,
``interactions``, the notes newest first and ``truncated``, fitted by
:func:`fit_recall` to ``max_recall_bytes`` as the UTF-8 bytes of the exact
serialization returned as the observation's text part — the size the
executor's ``observation_size`` counts. Notes are left out oldest first,
then ``display_name`` is shortened by whole characters from its end;
``truncated`` is true when anything was left out. A recall that returns a
record is a use (``use_count`` + 1, ``last_used`` now). A record creates or
updates the file through :meth:`MemoryStore.record`, with its bounds and
eviction.

**Facts.** Every eviction pass, expiry pass, corrupt pass and erasure
publishes one ``memory.removed`` fact carrying ``reason`` and ``count`` only —
no viewer identifier, no file name. A deletion the prepare scan could not
make is reported file by file in ``memory.deletion_failed`` facts: batches of
at most :data:`FAILURE_BATCH_SIZE` records (hashed file name, reason, bytes,
``remained: true``) with ``batch``, ``batches`` and ``failed``, so every
failed file is named whatever their number while no trace, diagnostic or
error message grows with it. ``module.degraded`` carries the count and the
first batch; a diagnostic and the error message name at most a few files and
count the rest.

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
from collections.abc import Awaitable, Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, NamedTuple

import yaml

from core.contracts import (
    EVENT_KIND_DEFAULT,
    PART_TYPE_TEXT,
    ActionObservation,
    ActionSpec,
    ContractError,
    Destination,
    SessionKey,
)


MODULE_NAME = "viewer_memory"
MANIFEST_PATH = Path(__file__).with_name("module.yaml")

#: The two actions this module provides (R4), and the provider serving both.
RECALL_ACTION = "memory.recall"
RECORD_ACTION = "memory.record"
PROVIDER_NAME = "viewer_memory"
MEMORY_SCOPE = "memory"

#: The session has no platform viewer (a watch run), or none on this channel.
ERROR_NO_VIEWER = "no_viewer"
ERROR_INVALID_ARGUMENTS = "invalid_arguments"
ERROR_STORE_FAILED = "store_failed"
_ERROR_PROVIDER_CLOSED = "provider_closed"

#: The fact every removal pass publishes.
FACT_MEMORY_REMOVED = "memory.removed"
REASON_EVICTED = "evicted"
REASON_EXPIRED = "expired"
REASON_CORRUPT = "corrupt"
REASON_ERASED = "erased"
#: The facts that list, batch by batch, the files a scan failed to delete.
FACT_MEMORY_DELETION_FAILED = "memory.deletion_failed"
#: Failure records per ``memory.deletion_failed`` batch, per
#: ``module.degraded`` and per diagnostic pass: a record is at most ~130
#: trace bytes, so a batch stays well inside the default trace budget.
FAILURE_BATCH_SIZE = 6
#: At most this many oversized files are named in the scan's error message;
#: the others are counted.
EXCEEDED_FILES_NAMED = 2
#: What any retained diagnostic and any bounds-refusal message is kept
#: within, whatever the number of files: each is built from a bounded number
#: of bounded parts, never joined over every file and then cut.
DIAGNOSTIC_MAX_CHARS = 1024

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
DEFAULT_MAX_RECALL_BYTES = 1024
MAX_RECALL_BYTES_BOUNDS = (512, 8192)

_SECONDS_PER_DAY = 86400
_TEMPORARY_PREFIX = ".viewer-memory-"
_TEMPORARY_SUFFIX = ".tmp"
#: The outcomes of one file deletion.
_DELETED = "deleted"
_ABSENT = "absent"
_FAILED = "failed"
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
_SETTING_MAX_RECALL_BYTES = "max_recall_bytes"
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
        _SETTING_MAX_RECALL_BYTES,
    }
)
_INTEGER_SETTINGS = (
    (_SETTING_RETENTION_DAYS, RETENTION_DAYS_BOUNDS),
    (_SETTING_MAX_FILES, MAX_FILES_BOUNDS),
    (_SETTING_MAX_FILE_BYTES, MAX_FILE_BYTES_BOUNDS),
    (_SETTING_MAX_TOTAL_BYTES, (MAX_FILE_BYTES_BOUNDS[0], MAX_TOTAL_BYTES_CEILING)),
    (_SETTING_MAX_NOTES, MAX_NOTES_BOUNDS),
    (_SETTING_SWEEP_INTERVAL, SWEEP_INTERVAL_BOUNDS),
    (_SETTING_MAX_RECALL_BYTES, MAX_RECALL_BYTES_BOUNDS),
)

_SEAM_SLEEPER = "_sleeper"
_SEAM_WALL_CLOCK = "_wall_clock"
_SEAMS = frozenset({_SEAM_SLEEPER, _SEAM_WALL_CLOCK})

Sleeper = Callable[[float], Awaitable[Any]]
WallClock = Callable[[], float]
MemoryKey = tuple[str, str, str]


class ViewerMemoryError(RuntimeError):
    """A setup failure whose message contains no configured value."""


#: The value-free reasons ``module.degraded`` and the diagnostics carry when a
#: deletion failed (R3): the bounds could not be restored at ``prepare`` —
#: startup refused — or a file stays on disk within them.
REASON_BOUNDS_NOT_MET = "the memory bounds cannot be met: a deletion failed"
REASON_DELETION_FAILED = "a memory file could not be deleted"
#: The reason a :class:`DeletionFailure` names for a file over
#: ``max_file_bytes`` (read as corrupt). Never a ``memory.removed`` reason: a
#: removed oversized file is counted as ``corrupt``.
REASON_OVERSIZED = "oversized"


class DeletionFailure(NamedTuple):
    """A memory file the prepare scan tried and failed to delete, and which
    is still on disk (counted toward the bounds). *name* is the hashed file
    name, so it names the file without a configured value."""

    name: str
    reason: str
    size: int

    def describe(self) -> str:
        return f"{self.name} ({self.reason}, {self.size} bytes) could not be deleted and remained"

    def record(self) -> dict[str, Any]:
        """The trace record: full file name, reason, bytes, and that it remained."""

        return {"file": self.name, "reason": self.reason, "bytes": self.size, "remained": True}


def failure_batch_count(failures: Sequence[DeletionFailure]) -> int:
    """How many :data:`FAILURE_BATCH_SIZE` batches *failures* make."""

    return -(-len(failures) // FAILURE_BATCH_SIZE)


def failure_batches(
    failures: Sequence[DeletionFailure],
) -> Iterator[list[dict[str, Any]]]:
    """*failures* as trace records, in batches of :data:`FAILURE_BATCH_SIZE`.

    Lazy: each batch's records are built only when it is reached, so at most
    one batch is live whatever the number of failures.
    """

    for start in range(0, len(failures), FAILURE_BATCH_SIZE):
        yield [failure.record() for failure in failures[start:start + FAILURE_BATCH_SIZE]]


class MemoryStoreError(RuntimeError):
    """A store operation that could not be carried out; value-free.

    ``removed`` holds the deletions the operation made before it gave up
    (``{reason: count}``), so their facts are still published.
    """

    def __init__(
        self,
        message: str,
        *,
        removed: Mapping[str, int] | None = None,
        failures: Sequence[DeletionFailure] = (),
    ) -> None:
        super().__init__(message)
        self.removed: dict[str, int] = dict(removed or {})
        #: The files the operation failed to delete and which remained.
        self.failures: tuple[DeletionFailure, ...] = tuple(failures)


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
    max_recall_bytes: int = DEFAULT_MAX_RECALL_BYTES

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
            max_recall_bytes=int(
                settings.get(_SETTING_MAX_RECALL_BYTES, DEFAULT_MAX_RECALL_BYTES)
            ),
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
        # ``file name → (reason, bytes)`` of every corrupt or expired memory
        # file whose deletion failed: still on disk, so it counts toward both
        # bounds, and every later eviction pass or sweep retries it.
        self._stranded: dict[str, tuple[str, int]] = {}
        # Deletions that failed since the store was built (value-free).
        self._failed_deletions = 0
        # ``file name → reason`` of every deletion the last prepare scan tried
        # and failed, the eviction pass included; see :meth:`scan_failures`.
        self._scan_attempts: dict[str, str] = {}

    @property
    def settings(self) -> MemorySettings:
        return self._settings

    @property
    def directory(self) -> Path:
        return self._directory

    def stats(self) -> tuple[int, int]:
        """``(file count, total bytes)`` of the memory files on disk: the
        indexed ones and those whose deletion failed."""

        stranded_bytes = sum(size for _reason, size in self._stranded.values())
        return len(self._index) + len(self._stranded), self._total_bytes + stranded_bytes

    @property
    def failed_deletions(self) -> int:
        """How many deletions failed since the store was built."""

        return self._failed_deletions

    def stranded(self) -> tuple[str, ...]:
        """The corrupt or expired file names whose deletion failed, sorted."""

        return tuple(sorted(self._stranded))

    def scan_failures(self) -> tuple[DeletionFailure, ...]:
        """The files the last :meth:`scan` failed to delete and which are
        still on disk, sorted by name — whether or not other deletions then
        restored the bounds. A failure a later retry of the same scan undid
        is not listed: that removal is a fact instead."""

        limit = self._settings.max_file_bytes
        failures = []
        for name in sorted(self._scan_attempts):
            if name in self._stranded:
                size = self._stranded[name][1]
                reason = REASON_OVERSIZED if size > limit else self._scan_attempts[name]
            elif name in self._index:
                size = self._index[name].size
                reason = self._scan_attempts[name]
            else:
                continue
            failures.append(DeletionFailure(name, reason, size))
        return tuple(failures)

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
        when absent. A corrupt or expired file whose deletion fails stays
        counted (:meth:`stranded`); every failed deletion is listed by
        :meth:`scan_failures`. When a bound still does not hold after the
        eviction pass — the file count or total bytes, or a file over
        ``max_file_bytes`` that could not be deleted — :class:`MemoryStoreError`
        is raised naming what is exceeded, carrying the removals made and the
        failures: the store is never used above its bounds.
        """

        self._directory.mkdir(parents=True, exist_ok=True)
        self._index.clear()
        self._total_bytes = 0
        self._stranded.clear()
        self._scan_attempts.clear()
        removed = {REASON_CORRUPT: 0, REASON_EXPIRED: 0, REASON_EVICTED: 0}
        horizon = self._horizon()
        with os.scandir(self._directory) as entries:
            candidates = [entry for entry in entries if MEMORY_FILE_PATTERN.match(entry.name)]
        for entry in sorted(candidates, key=lambda item: item.name):
            if entry.is_dir(follow_symlinks=False):
                continue
            record, size = self._read_file(entry.name, symlink=entry.is_symlink())
            if record is None:
                self._discard(entry, REASON_CORRUPT, removed)
                continue
            index_entry = _IndexEntry.of(record, size)
            if index_entry.last_seen_epoch < horizon:
                self._discard(entry, REASON_EXPIRED, removed)
                continue
            self._index[entry.name] = index_entry
            self._total_bytes += size
        exceeded: list[str] = []
        try:
            evicted = self._evict(target=None, new_size=0)
        except MemoryStoreError as error:
            evicted = error.removed
            files, total = self.stats()
            if files > self._settings.max_files:
                exceeded.append(f"{files} files held, over max_files {self._settings.max_files}")
            if total > self._settings.max_total_bytes:
                exceeded.append(
                    f"{total} bytes held, over max_total_bytes {self._settings.max_total_bytes}"
                )
        removed = _merge_removed(removed, evicted)
        failures = self.scan_failures()
        # Bounded before it is built: a few oversized files named, the rest
        # counted — every one of them is in ``failures``.
        limit = self._settings.max_file_bytes
        # A counter and at most EXCEEDED_FILES_NAMED names: no list that
        # grows with the number of oversized files.
        oversized = 0
        for failure in failures:
            if failure.reason != REASON_OVERSIZED:
                continue
            oversized += 1
            if oversized <= EXCEEDED_FILES_NAMED:
                exceeded.append(
                    f"{failure.name} is {failure.size} bytes, over max_file_bytes {limit}"
                )
        if oversized > EXCEEDED_FILES_NAMED:
            exceeded.append(
                f"{oversized - EXCEEDED_FILES_NAMED} more files over max_file_bytes "
                f"{limit} could not be deleted and remained"
            )
        if exceeded:
            raise MemoryStoreError(
                f"{MODULE_NAME} scan: the bounds cannot be met: " + "; ".join(exceeded),
                removed=removed,
                failures=failures,
            )
        return removed

    def _discard(self, entry: Any, reason: str, removed: dict[str, int]) -> None:
        """Delete a corrupt or expired scanned file; keep it counted on failure."""

        outcome = self._unlink(entry.name)
        if outcome == _DELETED:
            removed[reason] += 1
        elif outcome == _FAILED:
            try:
                size = entry.stat(follow_symlinks=False).st_size
            except OSError:
                size = 0
            self._stranded[entry.name] = (reason, int(size))
            self._scan_attempts[entry.name] = reason

    def sweep(self) -> dict[str, int]:
        """Retry the stranded files, then delete every indexed record past
        retention. A record whose deletion fails stays indexed (and
        unreadable, being expired) and is retried by the next sweep."""

        removed = self._retry_stranded()
        horizon = self._horizon()
        expired = [name for name, entry in self._index.items() if entry.last_seen_epoch < horizon]
        count = sum(self._delete(name) for name in sorted(expired))
        return _merge_removed(removed, {REASON_EXPIRED: count})

    def _retry_stranded(self) -> dict[str, int]:
        """Try once more to delete every stranded file; ``{reason: count}``."""

        removed: dict[str, int] = {}
        for name in sorted(self._stranded):
            reason = self._stranded[name][0]
            outcome = self._unlink(name)
            if outcome == _FAILED:
                continue
            del self._stranded[name]
            if outcome == _DELETED:
                removed[reason] = removed.get(reason, 0) + 1
        return removed

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

    def use(self, platform: str, channel_id: str, viewer_id: str) -> dict[str, int]:
        """Count one use of the viewer's retained record: ``use_count`` + 1,
        ``last_used`` now. Nothing else changes; an absent or expired record
        is left alone. Returns the removals the write needed."""

        existing = self.read(platform, channel_id, viewer_id)
        if existing is None:
            return {}
        record = dict(existing)
        record["notes"] = list(existing["notes"])
        record["use_count"] += 1
        record["last_used"] = format_timestamp(self._wall_clock())
        raw = self._fit(record)
        return self._write(memory_file_name(platform, channel_id, viewer_id), record, raw)

    def forget(self, platform: str, channel_id: str, viewer_id: str) -> int:
        """Delete the viewer's file; the number of files deleted (0 or 1).

        A file that is on disk but could not be deleted raises
        :class:`MemoryStoreError`: a failed erasure is never reported as an
        absent file.
        """

        name = memory_file_name(platform, channel_id, viewer_id)
        outcome = self._unlink(name)
        if outcome == _FAILED:
            raise MemoryStoreError(f"{MODULE_NAME} forget: the memory file could not be deleted")
        self._forget_index(name)
        self._stranded.pop(name, None)
        return 1 if outcome == _DELETED else 0

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
        removed = self._evict(target=name, new_size=len(raw))
        self._atomic_write(name, raw)
        # The replacement overwrote any stranded file of the same name: it is
        # now valid, so no later retry may delete it as corrupt or expired.
        self._stranded.pop(name, None)
        previous = self._index.pop(name, None)
        if previous is not None:
            self._total_bytes -= previous.size
        self._index[name] = _IndexEntry.of(record, len(raw))
        self._total_bytes += len(raw)
        return removed

    def _evict(self, *, target: str | None, new_size: int) -> dict[str, int]:
        """Delete until both bounds hold after the write: the stranded files
        first (retried), then candidates in eviction order.

        Returns ``{reason: count}``; the prepare scan's failed deletions are
        kept for :meth:`scan_failures`. When the bounds still do not hold — a
        deletion failed — raises :class:`MemoryStoreError` carrying the
        removals made, for a write (*target*) and for the prepare scan
        (*target* ``None``) alike.
        """

        settings = self._settings

        def replaced_size() -> int | None:
            """Bytes of the file the write replaces, indexed or stranded;
            looked up each time, as a retry may delete a stranded target."""

            if target in self._index:
                return self._index[target].size
            if target in self._stranded:
                return self._stranded[target][1]
            return None

        def over() -> bool:
            files, total = self.stats()
            if target is not None:
                previous = replaced_size()
                if previous is None:
                    files += 1
                    total += new_size
                else:
                    total += new_size - previous
            return files > settings.max_files or total > settings.max_total_bytes

        if not over():
            return {}
        removed = self._retry_stranded()
        count = 0
        for name in [name for name in self.eviction_order() if name != target]:
            if not over():
                break
            # A file that could not be removed stays indexed: it is still on
            # disk, counts toward both bounds and is retried by later passes.
            count += self._delete(name)
            if target is None and name in self._index:
                # The prepare scan's failure is reported even when a later
                # candidate restores the bounds.
                self._scan_attempts[name] = REASON_EVICTED
        removed = _merge_removed(removed, {REASON_EVICTED: count})
        if over():
            operation = "record" if target is not None else "scan"
            raise MemoryStoreError(
                f"{MODULE_NAME} {operation}: the bounds cannot be met", removed=removed
            )
        return removed

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
        """Remove *name* from disk and index; 1 when a file was deleted.

        A file that could not be removed stays indexed and counted.
        """

        outcome = self._unlink(name)
        if outcome == _FAILED:
            return 0
        self._forget_index(name)
        return 1 if outcome == _DELETED else 0

    def _forget_index(self, name: str) -> None:
        entry = self._index.pop(name, None)
        if entry is not None:
            self._total_bytes -= entry.size

    def _unlink(self, name: str) -> str:
        """Remove *name* from disk: :data:`_DELETED`, :data:`_ABSENT` when it
        was already gone, or :data:`_FAILED` (counted) when it stays."""

        try:
            os.unlink(self._directory / name)
        except FileNotFoundError:
            return _ABSENT
        except OSError:
            self._failed_deletions += 1
            return _FAILED
        return _DELETED


def _merge_removed(*parts: Mapping[str, int]) -> dict[str, int]:
    """The sum of ``{reason: count}`` mappings, reasons with 0 left out."""

    merged: dict[str, int] = {}
    for part in parts:
        for reason, count in part.items():
            if count:
                merged[reason] = merged.get(reason, 0) + count
    return merged


# --------------------------------------------------------------------------- #
# The handle
# --------------------------------------------------------------------------- #


class _MemoryActionProvider:
    """The provider bound to both memory actions: hands each call to the module."""

    __slots__ = ("_module",)

    name = PROVIDER_NAME

    def __init__(self, module: "ViewerMemoryModule") -> None:
        self._module = module

    async def invoke(self, invocation: Any) -> ActionObservation:
        return await self._module._invoke(invocation)


class ViewerMemoryModule:
    """The v2 handle: scan and bind at ``prepare``, sweep, recall and record,
    erase on the chat command."""

    def __init__(
        self,
        context: Any,
        settings: MemorySettings,
        *,
        sleeper: Sleeper,
        wall_clock: WallClock,
    ) -> None:
        self._bus = context.bus
        self._actions = context.actions
        self._provider = _MemoryActionProvider(self)
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
        except MemoryStoreError as error:
            # A deletion failed and a bound does not hold: the removals made
            # are still facts, and the reason — with each file that remained
            # and the bound exceeded — goes on the module's own health trace
            # first, since the coordinator's failure names the module only.
            # No action is bound and readiness is never advertised.
            await self._publish_removed(error.removed)
            self._diagnose_failures(error.failures)
            # Bounded by construction (:meth:`MemoryStore.scan`): at most
            # EXCEEDED_FILES_NAMED files named; every failed file is in the
            # ``memory.deletion_failed`` batches instead.
            detail = str(error).partition(": the bounds cannot be met: ")[2]
            self._diagnose(f"{REASON_BOUNDS_NOT_MET}: {detail}")
            await self._report_degraded(REASON_BOUNDS_NOT_MET, error.failures, exceeded=detail)
            await self._publish_failures(REASON_BOUNDS_NOT_MET, error.failures)
            raise ViewerMemoryError(
                f"{MODULE_NAME} prepare: {REASON_BOUNDS_NOT_MET}: {detail}"
            ) from None
        except OSError:
            raise ViewerMemoryError(
                f"{MODULE_NAME} prepare: the memory directory could not be scanned"
            ) from None
        await self._publish_removed(removed)
        failures = self._store.scan_failures()
        if failures:
            # Within the bounds — other files were removed instead, or the
            # file fits them — but a deletion failed and the file stays on
            # disk: counted, retried by later passes, and said file by file.
            self._diagnose_failures(failures)
            await self._report_degraded(REASON_DELETION_FAILED, failures)
            await self._publish_failures(REASON_DELETION_FAILED, failures)
        try:
            for action_name in (RECALL_ACTION, RECORD_ACTION):
                self._actions.register(
                    _declared_spec(action_name), self._provider, provider_name=PROVIDER_NAME
                )
        except ViewerMemoryError:
            raise
        except Exception:
            raise ViewerMemoryError(f"{MODULE_NAME} prepare: action binding failed") from None
        self._bus.subscribe(_CHAT_EVENT, self.handle_chat_message)
        self._prepared = True
        self._actions.mark_ready()

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
        if self._prepared:
            self._actions.mark_not_ready()
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

        failed = self._store.failed_deletions
        try:
            removed = self._store.sweep()
        except OSError:
            self._diagnose("sweep: failed")
            return {}
        await self._publish_removed(removed)
        if self._store.failed_deletions != failed:
            self._diagnose(REASON_DELETION_FAILED)
            await self._report_degraded(REASON_DELETION_FAILED)
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
        """Erase one viewer's file; one ``erased`` fact when a file went.

        A file that could not be deleted is reported on ``module.degraded``
        and raises :class:`MemoryStoreError`: never taken for an absent file.
        """

        try:
            count = self._store.forget(platform, channel_id, viewer_id)
        except MemoryStoreError:
            await self._report_degraded(REASON_DELETION_FAILED)
            raise
        await self._publish_removed({REASON_ERASED: count} if count else {})
        return count

    # -- the actions (R4) ----------------------------------------------------- #

    async def _invoke(self, invocation: Any) -> ActionObservation:
        """Serve one ``memory.recall`` or ``memory.record`` call.

        The executor has already checked the arguments against the declared
        schema, so no identity argument reaches here.
        """

        call = invocation.call
        destination = call.destination
        action_name = call.action_name
        provenance = {
            "provider": PROVIDER_NAME,
            "platform": destination.platform,
            "channel_id": destination.channel_id,
            "route": action_name,
        }
        if self._closed or not self._prepared:
            return _failure(provenance, _ERROR_PROVIDER_CLOSED, "the memory store is closed")
        key = parse_conversation_id(call.conversation_id)
        if key is None or key[:2] != (destination.platform, destination.channel_id):
            return _failure(
                provenance, ERROR_NO_VIEWER, "the session has no platform viewer on this channel"
            )
        if action_name == RECALL_ACTION:
            return await self._recall(key, provenance)
        return await self._record(key, call.arguments, provenance)

    async def _recall(self, key: MemoryKey, provenance: Mapping[str, Any]) -> ActionObservation:
        try:
            stored = self._store.read(*key)
        except OSError:
            return _failure(provenance, ERROR_STORE_FAILED, "the memory could not be read")
        if stored is None:
            result: dict[str, Any] = {"known": False}
            text = _render(result)
        else:
            result, text = fit_recall(stored, self._settings.max_recall_bytes)
            # A recall that returns a record is a use; a failed use loses the
            # count, never the answer.
            try:
                removed = self._store.use(*key)
            except MemoryStoreError as error:
                removed = error.removed
                self._diagnose("use: not recorded")
            except OSError:
                removed = {}
                self._diagnose("use: not recorded")
            await self._publish_removed(removed)
        return ActionObservation(
            status="success",
            provenance=provenance,
            result=result,
            parts=({"type": PART_TYPE_TEXT, "text": text},),
        )

    async def _record(
        self, key: MemoryKey, arguments: Mapping[str, Any], provenance: Mapping[str, Any]
    ) -> ActionObservation:
        # The schema subset has no length keyword: the text bounds are here.
        for field_name in ("viewer_text", "reply_text"):
            value = arguments.get(field_name)
            if value is not None and len(value) > NOTE_TEXT_MAX_CHARS:
                return _failure(
                    provenance,
                    ERROR_INVALID_ARGUMENTS,
                    f"{field_name} must be at most {NOTE_TEXT_MAX_CHARS} characters",
                )
        note = {
            name: arguments[name]
            for name in ("viewer_text", "reply_text", "delivery")
            if name in arguments
        }
        try:
            record = await self.record(
                *key, display_name=arguments.get("display_name"), note=note
            )
        except (MemoryStoreError, OSError):
            return _failure(provenance, ERROR_STORE_FAILED, "the memory could not be written")
        return ActionObservation(
            status="success",
            provenance=provenance,
            result={"interactions": record["interactions"], "notes": len(record["notes"])},
        )

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

    async def _report_degraded(
        self, reason: str, failures: Sequence[DeletionFailure] = (), **details: Any
    ) -> None:
        degraded = getattr(self._supervision, "degraded", None)
        if not callable(degraded):
            return
        if failures:
            # The count and the first batch only: module health reports one
            # fact per reason, so the complete list is the batches'.
            details["failed_deletions"] = len(failures)
            details["failure_batches"] = failure_batch_count(failures)
            details["failures"] = next(failure_batches(failures[:FAILURE_BATCH_SIZE]))
        try:
            outcome = degraded(
                reason=reason, capabilities=[RECALL_ACTION, RECORD_ACTION], **details
            )
            if inspect.isawaitable(outcome):
                await outcome
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a health report must not fail the caller
            self._diagnose("degraded: not published")

    async def _publish_failures(
        self, reason: str, failures: Sequence[DeletionFailure]
    ) -> None:
        """Every failed file, in ``memory.deletion_failed`` batches.

        Facts, not health states: module health does not repeat a reason it
        already reported, so each batch is its own fact. A batch that could
        not be published is counted in one diagnostic.
        """

        emit = getattr(self._supervision, "emit", None)
        if not callable(emit) or not failures:
            return
        # Counted arithmetically and emitted lazily: one batch of records is
        # live at a time, however many files failed.
        batches = failure_batch_count(failures)
        lost = 0
        for number, records in enumerate(failure_batches(failures), start=1):
            payload = {
                "reason": reason,
                "batch": number,
                "batches": batches,
                "failed": len(failures),
                "failures": records,
            }
            del records
            try:
                outcome = emit(FACT_MEMORY_DELETION_FAILED, payload)
                if inspect.isawaitable(outcome):
                    await outcome
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - a lost report must not fail the caller
                lost += 1
            finally:
                del payload
        if lost:
            self._diagnose(
                f"fact {FACT_MEMORY_DELETION_FAILED}: {lost} of {batches} batches "
                "not published"
            )

    def _diagnose_failures(self, failures: Sequence[DeletionFailure]) -> None:
        """One diagnostic per file of the first batch, then one counting the
        rest: a bounded number of bounded lines, whatever the count."""

        for failure in failures[:FAILURE_BATCH_SIZE]:
            self._diagnose(f"{REASON_DELETION_FAILED}: {failure.describe()}")
        if len(failures) > FAILURE_BATCH_SIZE:
            batches = failure_batch_count(failures)
            self._diagnose(
                f"{REASON_DELETION_FAILED}: {len(failures) - FAILURE_BATCH_SIZE} more files "
                f"could not be deleted and remained; all {len(failures)} are named in "
                f"{batches} {FACT_MEMORY_DELETION_FAILED} facts"
            )

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
# The session viewer and the recall observation
# --------------------------------------------------------------------------- #


def parse_conversation_id(conversation_id: Any) -> MemoryKey | None:
    """The platform viewer of a ``SessionKey.serialize()`` string, or ``None``.

    The inverse of the length-prefixed ``<n>:<platform>|<n>:<channel>|<n>:
    <viewer>`` form; only the canonical serialization of a valid key is
    accepted. ``None`` too for a viewer in the reserved ``:`` namespace
    (``system:watch``): no platform author identifier contains it.
    """

    if not isinstance(conversation_id, str):
        return None
    parts: list[str] = []
    position = 0
    while position < len(conversation_id) and len(parts) < 3:
        separator = conversation_id.find(":", position)
        digits = conversation_id[position:separator] if separator >= 0 else ""
        if not digits.isascii() or not digits.isdigit():
            return None
        start = separator + 1
        end = start + int(digits)
        if end > len(conversation_id):
            return None
        parts.append(conversation_id[start:end])
        position = end + 1
    if len(parts) != 3:
        return None
    try:
        key = SessionKey(*parts)
    except ContractError:
        return None
    if key.serialize() != conversation_id:
        return None
    if _RESERVED_SEPARATOR in key.viewer_id:
        return None
    return key.platform, key.channel_id, key.viewer_id


def _render(result: Mapping[str, Any]) -> str:
    """The observation text of a recall: the compact JSON of its result."""

    return json.dumps(result, ensure_ascii=False, separators=(",", ":"))


def fit_recall(record: Mapping[str, Any], limit: int) -> tuple[dict[str, Any], str]:
    """The recall result of *record* within *limit* UTF-8 bytes, and its text.

    The metadata is always kept; the notes, newest first, are left out
    oldest first, then ``display_name`` is shortened by whole characters
    from its end. ``truncated`` says whether anything was left out. The
    size is that of the returned text, exactly what the observation carries.
    """

    notes = [dict(note) for note in reversed(record["notes"])]
    result: dict[str, Any] = {
        "known": True,
        "display_name": record["display_name"],
        "first_seen": record["first_seen"],
        "last_seen": record["last_seen"],
        "interactions": record["interactions"],
        "notes": notes,
        "truncated": False,
    }

    def size() -> int:
        return len(_render(result).encode("utf-8"))

    if size() > limit:
        result["truncated"] = True
        while notes and size() > limit:
            notes.pop()
        name = result["display_name"]
        if size() > limit and name:
            # Shorten by whole characters: the longest prefix that fits.
            low, high = 0, len(name)
            while low < high:
                middle = (low + high + 1) // 2
                result["display_name"] = name[:middle]
                if size() <= limit:
                    low = middle
                else:
                    high = middle - 1
            result["display_name"] = name[:low]
    return result, _render(result)


def _failure(provenance: Mapping[str, Any], code: str, message: str) -> ActionObservation:
    return ActionObservation(
        status="error",
        provenance=provenance,
        error={"code": code, "message": message, "retryable": False},
    )


def _declared_spec(action_name: str) -> ActionSpec:
    """Build *action_name*'s contract from the colocated manifest.

    The spec registered at ``prepare`` must equal the one the loader declared
    at discovery, field for field: reading the same file keeps the two from
    drifting, and the registry refuses a redeclaration that differs.
    """

    try:
        manifest = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))
        entry = next(
            item
            for item in manifest["actions"]
            if isinstance(item, Mapping) and item.get("name") == action_name
        )
        return ActionSpec(
            name=entry["name"],
            version=entry["version"],
            description=entry["description"],
            argument_schema=entry["argument_schema"],
            result_schema=entry["result_schema"],
            nature=entry["nature"],
            required_permissions=tuple(entry.get("required_permissions", ())),
            supported_destinations=tuple(
                Destination(
                    platform=item.get("platform"),
                    channel_id=item.get("channel_id"),
                    scope=item.get("scope"),
                )
                for item in entry["supported_destinations"]
            ),
            timeout_seconds=entry["timeout_seconds"],
            idempotency=entry["idempotency"],
            delivery=entry.get("delivery"),
            model_proposable=entry.get("model_proposable", False),
        )
    except Exception:
        raise ViewerMemoryError(
            f"{MODULE_NAME} prepare: manifest declaration of {action_name!r} is invalid"
        ) from None


# --------------------------------------------------------------------------- #
# Activation
# --------------------------------------------------------------------------- #


async def activate(context: Any, settings: Mapping[str, Any], catalog: Any) -> ViewerMemoryModule:
    """Validate the settings and build the handle; nothing is opened here."""

    del catalog  # Nothing is declared from the catalog.
    bus = getattr(context, "bus", None)
    tasks = getattr(context, "tasks", None)
    actions = getattr(context, "actions", None)
    if (
        not callable(getattr(bus, "subscribe", None))
        or not callable(getattr(tasks, "spawn", None))
        or not all(
            callable(getattr(actions, method, None))
            for method in ("register", "mark_ready", "mark_not_ready")
        )
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
    "DEFAULT_MAX_RECALL_BYTES",
    "DEFAULT_MAX_FILE_BYTES",
    "DEFAULT_MAX_FILES",
    "DEFAULT_MAX_NOTES",
    "DEFAULT_MAX_TOTAL_BYTES",
    "DEFAULT_RETENTION_DAYS",
    "DEFAULT_SWEEP_INTERVAL_SECONDS",
    "DELIVERY_VALUES",
    "DIAGNOSTIC_MAX_CHARS",
    "DISPLAY_NAME_MAX_CHARS",
    "ERROR_NO_VIEWER",
    "EXCEEDED_FILES_NAMED",
    "FACT_MEMORY_DELETION_FAILED",
    "FACT_MEMORY_REMOVED",
    "FAILURE_BATCH_SIZE",
    "MEMORY_FILE_PATTERN",
    "MANIFEST_PATH",
    "MEMORY_FORMAT",
    "MODULE_NAME",
    "NOTE_TEXT_MAX_CHARS",
    "PROVIDER_NAME",
    "RECALL_ACTION",
    "RECORD_ACTION",
    "REASON_BOUNDS_NOT_MET",
    "REASON_CORRUPT",
    "REASON_DELETION_FAILED",
    "REASON_ERASED",
    "REASON_EVICTED",
    "REASON_EXPIRED",
    "REASON_OVERSIZED",
    "TIMESTAMP_FORMAT",
    "DeletionFailure",
    "MemorySettings",
    "MemoryStore",
    "MemoryStoreError",
    "ViewerMemoryError",
    "ViewerMemoryModule",
    "activate",
    "fit_recall",
    "failure_batch_count",
    "failure_batches",
    "format_timestamp",
    "memory_file_name",
    "parse_conversation_id",
    "parse_timestamp",
    "validate_settings",
]
