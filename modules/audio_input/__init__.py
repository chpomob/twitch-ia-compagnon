"""The ``audio.capture`` action over configured sources (R3, R4).

The companion listens through this module: one call, one bounded segment,
returned as a reference into the run-leased attachment store — never as bytes
in an event and never as a path on this machine. Which microphone or mix the
segment holds is the business of the configured *source*: a ``command``
source is a program started at the call with a fixed ``argv`` whose stdout is
the WAV it records, stopped once the requested duration is recorded (or its
``seconds`` elapsed on the injected sleeper), its stdout read incrementally
and bounded; a ``file`` source is a WAV the module re-reads at each call.
Nothing is recorded in the background: a capture is on demand, requested by
the brain, and refused without an applicable rule with the provider never
entered — no file is read and no process is spawned (AC12).

**The segment convention** (decision 5). The module accepts only RIFF/WAVE,
PCM (format 1), 16-bit, 16 000 Hz, mono — anything else, a non-WAV body or
one without audio, is ``error invalid_result`` with 0 bytes stored (AC9). The
data chunk is cut to ``seconds × 32 000`` bytes and the segment rewritten
with the canonical 44-byte header whose ``RIFF`` and ``data`` sizes match the
bytes kept, so the executor's size check and every reader of the header
agree. A segment heavier than ``max_bytes`` (default 1 048 576), or one the
store refuses on any of its own axes, is ``error attachment_refused`` with 0
bytes stored. The stored segment is leased to the call's ``run_id`` with
content type ``audio/wav``; the result names the source, the size, the
duration, the format, the instant of acquisition on the runtime clock and
``transcription_status``, and the observation carries exactly one
``audio_ref`` part with ``provider_id: "audio-input"``.

**Arguments first, and the admission reserve.** ``seconds`` must be an
integer in 1..``max_seconds`` and ``source`` a configured source, else ``error
invalid_arguments``. At entry the call computes ``expiry = min(call.deadline,
clock() + spec.timeout_seconds)`` — the executor's own arithmetic — and a
``seconds`` above ``(expiry − now) − grace_seconds`` is ``error
capture_too_long``: all three before any process is spawned. ``grace_seconds``
is this admission reserve and nothing else (AC41).

**The call deadline kills the recorder, nothing earlier** (R10, decision 1).
While a source records, a watcher parked on the injected sleeper until
``expiry`` itself kills it and the call returns at once ``error
capture_timed_out`` with 0 bytes stored. A ``CancelledError`` reaching the
call while recording is caught: the recorder is killed (once) and the record
is returned normally — ``error capture_timed_out`` when the clock has reached
``expiry``, ``cancelled`` otherwise. ``drain`` kills running recorders, their
calls ending ``cancelled``. One kill per capture: the first trigger fixes the
status. The executor adopts such a record even when it is stamped at or after
the deadline (R10).

**The optional transcription** (R3; decision 1, round 2 P1, round 3 V1
and V4). With ``transcription.enabled``, once ``attachments.put`` has
returned the capture is complete: the segment is leased to the run, the
observation is a ``success`` carrying its ``audio_ref`` whatever becomes of
the transcription, and the module never releases the segment itself. It then
reads the clock once and computes three named quantities —
``transcription_reserve`` (1 s, R3's ``skipped:deadline`` constant),
``transcription_edge = expiry − transcription_reserve`` and
``transcription_window = transcription_edge − now`` — the second of the two
deadline subtractions AC41 authorizes, and bounding the transcription request
only (the recorder is killed at ``expiry`` itself). A window ``≤ 0``
transcribes nothing (``skipped:deadline``). Otherwise one multipart request —
the WAV streamed from memory, ``model``, ``language`` when set, a bearer
header when ``api_key`` is set — is raced against a bound of
``min(timeout_seconds, transcription_window)`` on the sleeper. The order is
fixed: *attempt*, then *decide* at the wake, whatever woke the call (the
answer, the bound, ``drain`` or a ``CancelledError``), on the clock read at
that instant — an answer in hand before ``transcription_edge`` gives its
status, anything else abandons the request as ``failed:stt_timed_out`` — then
*author* the ``success`` record and return it with no further await, so the
executor's completion stamp is the decision instant, before ``expiry``. A 2xx
JSON ``{text}`` becomes ``audio_ref.transcription = {text (cut to
max_chars), transcribed_at, provider_id: "audio-input-stt", truncated}`` and
``ok``; a transport failure is ``failed:stt_unavailable``, a non-2xx answer
``failed:stt_failed``, a 2xx answer without a string ``text``
``failed:invalid_transcription``. ``prepare`` sends one probe — a generated
0.25 s silent segment, bounded by ``timeout_seconds``; when it fails, the
capture stays bound, one ``module.degraded`` reports ``transcription
unavailable`` with no capability, and every capture reports ``unavailable``
with no request (decision 11).

**Seams, for the tests.** ``_source_factory`` builds the source object of
each configured source (by default :class:`FileSource` and
:class:`CommandSource`; a harness hands in its own), ``_subprocess_runner``
replaces ``asyncio.create_subprocess_exec`` for the command sources,
``_transcription_transport`` the HTTP session of the optional transcription
and ``_sleeper`` the sleep every bound is raced against, so a deadline is
exercised on an injected clock without a sleeping child. They are read from
*settings* and never from a configuration file.

Lifecycle: the manifest declares no role, so the handle takes part in
``prepare``, ``drain`` and ``close``. ``prepare`` probes each source — a
``command`` must name an executable, a ``file`` must be readable — binds
``audio.capture`` when at least one is usable and reports ``module.degraded``
for the others (value-free, naming the source); with none usable the action is
left unbound, and ``required: true`` turns an unusable source into a failed
``prepare`` naming the field; with transcription enabled it also sends the
transcription probe, and ``required: true`` turns its failure into a failed
``prepare`` naming ``transcription.endpoint``. ``drain`` lets a call in its
transcription phase finish within the drain deadline and abandons its request
when that deadline passes first; ``close`` releases the transport.
"""

from __future__ import annotations

import asyncio
import inspect
import io
import json
import math
import os
import shutil
import signal
import struct
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

try:  # Keep the module importable for transport-injected contract tests.
    import aiohttp
except ModuleNotFoundError:  # pragma: no cover - production installs dependencies
    aiohttp = None  # type: ignore[assignment]

from core.actions import ERROR_CANCELLED, ERROR_INVALID_ARGUMENTS
from core.attachments import AttachmentRefused
from core.contracts import (
    BRAIN_ERROR_ATTACHMENT_REFUSED,
    BRAIN_ERROR_INVALID_RESULT,
    PART_TYPE_AUDIO_REF,
    ActionObservation,
    ActionSpec,
    Destination,
)


MODULE_NAME = "audio_input"

CAPTURE_ACTION = "audio.capture"
PROVIDER_NAME = "audio_input"
"""Provider name in bindings and traces."""

PROVIDER_ID = "audio-input"
"""The ``provider_id`` of every ``audio_ref`` part."""

MANIFEST_PATH = Path(__file__).with_name("module.yaml")

SOURCE_KIND_FILE = "file"
SOURCE_KIND_COMMAND = "command"
SOURCE_KINDS = frozenset({SOURCE_KIND_FILE, SOURCE_KIND_COMMAND})

CONTENT_TYPE_WAV = "audio/wav"
SAMPLE_RATE_HZ = 16_000
CHANNELS = 1
BITS_PER_SAMPLE = 16
BYTES_PER_SECOND = SAMPLE_RATE_HZ * CHANNELS * BITS_PER_SAMPLE // 8
"""32 000: the data bytes of one second of the segment convention."""

WAV_HEADER_BYTES = 44
"""The canonical header every stored segment carries."""

DEFAULT_MAX_SECONDS = 30
DEFAULT_MAX_BYTES = 1_048_576
DEFAULT_GRACE_SECONDS = 2.0
DEFAULT_TRANSCRIPTION_TIMEOUT_SECONDS = 10.0
DEFAULT_TRANSCRIPTION_MAX_CHARS = 2000

TRANSCRIPTION_PROVIDER_ID = "audio-input-stt"
"""The ``provider_id`` of an ``audio_ref`` transcription."""

TRANSCRIPTION_DISABLED = "disabled"
TRANSCRIPTION_OK = "ok"
TRANSCRIPTION_UNAVAILABLE = "unavailable"
"""The prepare-time probe failed: no capture sends a transcription request."""
TRANSCRIPTION_SKIPPED_DEADLINE = "skipped:deadline"
TRANSCRIPTION_STT_UNAVAILABLE = "failed:stt_unavailable"
TRANSCRIPTION_STT_FAILED = "failed:stt_failed"
TRANSCRIPTION_STT_TIMED_OUT = "failed:stt_timed_out"
TRANSCRIPTION_INVALID = "failed:invalid_transcription"

TRANSCRIPTION_RESERVE_SECONDS = 1.0
"""R3's ``skipped:deadline`` constant: the ``transcription_reserve``.

The second of the two deadline subtractions AC41 authorizes, and the only
literal of the transcription window: a request is not started, and a running
one is abandoned, once less than this remains before ``expiry``, so the
capture's ``success`` record is stamped before the call expires. It bounds the
transcription request only; the recorder is killed at ``expiry`` itself.
"""

TRANSCRIPTION_DEGRADED_REASON = "transcription unavailable"
"""The value-free ``module.degraded`` reason of a failed transcription probe."""

PROBE_SEGMENT_SECONDS = 0.25
"""The generated silent segment the transcription probe sends (8 044 bytes)."""

ERROR_CAPTURE_TOO_LONG = "capture_too_long"
"""``seconds`` exceeds the time left before the deadline minus ``grace_seconds``."""

ERROR_CAPTURE_TIMED_OUT = "capture_timed_out"
"""The call deadline reached a recording: killed, 0 bytes stored."""

ERROR_CAPTURE_FAILED = "capture_failed"
"""A source could not be read at all: file unreadable, recorder failed to start."""

ERROR_ATTACHMENT_REFUSED = BRAIN_ERROR_ATTACHMENT_REFUSED
ERROR_INVALID_RESULT = BRAIN_ERROR_INVALID_RESULT
_ERROR_PROVIDER_CLOSED = "provider_closed"

#: The reserved setting the entry point hands its accepted ``limits`` block
#: over in (``core.main.LIMITS_KEY``). Accepted, never read.
_ACCEPTED_LIMITS_SETTING = "limits"

#: Settings this module reads; every other non-seam key is refused by name.
_SETTING_SOURCES = "sources"
_SETTING_DEFAULT_SOURCE = "default_source"
_SETTING_MAX_SECONDS = "max_seconds"
_SETTING_MAX_BYTES = "max_bytes"
_SETTING_GRACE_SECONDS = "grace_seconds"
_SETTING_TRANSCRIPTION = "transcription"
_SETTING_REQUIRED = "required"
_SETTINGS = frozenset(
    {
        _SETTING_SOURCES,
        _SETTING_DEFAULT_SOURCE,
        _SETTING_MAX_SECONDS,
        _SETTING_MAX_BYTES,
        _SETTING_GRACE_SECONDS,
        _SETTING_TRANSCRIPTION,
        _SETTING_REQUIRED,
    }
)
_TRANSCRIPTION_TEXT_FIELDS = ("endpoint", "model", "api_key", "language")
_TRANSCRIPTION_FIELDS = frozenset(
    {"enabled", "timeout_seconds", "max_chars", *_TRANSCRIPTION_TEXT_FIELDS}
)

#: The injection seams: read from *settings*, never from a configuration file.
_SEAM_SOURCE_FACTORY = "_source_factory"
_SEAM_SUBPROCESS_RUNNER = "_subprocess_runner"
_SEAM_TRANSCRIPTION_TRANSPORT = "_transcription_transport"
_SEAM_SLEEPER = "_sleeper"
_SEAMS = frozenset(
    {_SEAM_SOURCE_FACTORY, _SEAM_SUBPROCESS_RUNNER, _SEAM_TRANSCRIPTION_TRANSPORT, _SEAM_SLEEPER}
)

#: The multipart field names and the file name the transcription request uses.
_FIELD_FILE = "file"
_FIELD_MODEL = "model"
_FIELD_LANGUAGE = "language"
_UPLOAD_FILENAME = "segment.wav"
#: How much of a transcription answer is read at a time, and the room its
#: JSON may take beyond ``max_chars`` characters (each at most 12 bytes when
#: escaped as a surrogate pair): an answer past the bound is not a
#: transcription the module will hold in memory.
_ANSWER_CHUNK = 65_536
_ANSWER_ALLOWANCE_BYTES = 65_536
_ANSWER_BYTES_PER_CHAR = 12

#: How much a command source reads from the recorder's stdout at a time.
_READ_CHUNK = 65_536
#: Room left for header chunks beyond ``max_bytes`` when a source's read is
#: bounded: a segment is cut to the canonical header, so what a source may
#: hold past ``max_bytes`` is header, never audio the store would keep.
_HEADER_ALLOWANCE_BYTES = 4096

_WAVE_FORMAT_PCM = 1
#: Declared ``data`` sizes a streaming recorder writes when it cannot know
#: the length up front: the chunk runs to the end of what was read.
_STREAMING_DATA_SIZES = frozenset({0, 0xFFFFFFFF})


class AudioInputModuleError(RuntimeError):
    """A setup failure whose message contains no configured value."""


class CaptureError(Exception):
    """A source could not deliver a segment; the message names no path."""


class InvalidSegment(ValueError):
    """The bytes are not a segment of the convention; value-free message."""


# --------------------------------------------------------------------------- #
# Settings validation hook (R7)
# --------------------------------------------------------------------------- #


def validate_settings(settings: Any) -> list[str]:
    """Check this module's settings; return one diagnostic per offending field.

    This is the hook ``settings_validator`` in the manifest names. Each
    diagnostic names the module and the field and nothing else — no path, no
    argv, no endpoint, no key is echoed; an empty list means the settings are
    accepted.

    ``sources`` must be a non-empty mapping of names to ``{kind: command,
    argv[]}`` (``argv`` a non-empty list of strings) or ``{kind: file,
    path}``; ``default_source`` must name one of them; ``max_seconds`` and
    ``max_bytes`` positive integers, ``grace_seconds`` a finite non-negative
    number; ``transcription`` a mapping of its declared fields; ``required``
    a boolean. The reserved ``limits`` block is accepted and not inspected; a
    seam must be callable (a transport is a factory); any other key is
    refused by name.
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

    sources = settings.get(_SETTING_SOURCES)
    names: set[str] = set()
    if sources is None:
        diagnostics.append(_setting_diagnostic(_SETTING_SOURCES, "is required"))
    elif not isinstance(sources, Mapping) or not sources:
        diagnostics.append(
            _setting_diagnostic(_SETTING_SOURCES, "must be a non-empty mapping of sources")
        )
    else:
        for name, entry in sources.items():
            if not isinstance(name, str) or not name.strip():
                diagnostics.append(
                    _setting_diagnostic(_SETTING_SOURCES, "must use non-empty source names")
                )
                continue
            names.add(name)
            diagnostics.extend(_source_diagnostics(f"{_SETTING_SOURCES}.{name}", entry))

    default = settings.get(_SETTING_DEFAULT_SOURCE)
    if default is None:
        diagnostics.append(_setting_diagnostic(_SETTING_DEFAULT_SOURCE, "is required"))
    elif not _is_text(default):
        diagnostics.append(
            _setting_diagnostic(_SETTING_DEFAULT_SOURCE, "must be a non-empty string")
        )
    elif names and default not in names:
        diagnostics.append(
            _setting_diagnostic(_SETTING_DEFAULT_SOURCE, "must name a configured source")
        )

    for field_name in (_SETTING_MAX_SECONDS, _SETTING_MAX_BYTES):
        if field_name in settings and not _is_positive_int(settings[field_name]):
            diagnostics.append(_setting_diagnostic(field_name, "must be a positive integer"))
    if _SETTING_GRACE_SECONDS in settings and not _is_non_negative_number(
        settings[_SETTING_GRACE_SECONDS]
    ):
        diagnostics.append(
            _setting_diagnostic(_SETTING_GRACE_SECONDS, "must be a finite non-negative number")
        )
    if _SETTING_TRANSCRIPTION in settings:
        diagnostics.extend(_transcription_diagnostics(settings[_SETTING_TRANSCRIPTION]))
    if _SETTING_REQUIRED in settings and not isinstance(settings[_SETTING_REQUIRED], bool):
        diagnostics.append(_setting_diagnostic(_SETTING_REQUIRED, "must be a boolean"))
    return diagnostics


def _source_diagnostics(field: str, entry: Any) -> list[str]:
    """The diagnostics of one ``sources.<name>`` entry, value-free."""

    if not isinstance(entry, Mapping):
        return [_setting_diagnostic(field, "must be a mapping")]
    kind = entry.get("kind")
    # ``isinstance`` first: an unhashable ``kind`` must be reported as a
    # diagnostic, not raise from the membership test.
    if not isinstance(kind, str) or kind not in SOURCE_KINDS:
        return [_setting_diagnostic(f"{field}.kind", "must be 'command' or 'file'")]
    diagnostics: list[str] = []
    if kind == SOURCE_KIND_FILE:
        allowed = {"kind", "path"}
        if not _is_text(entry.get("path")):
            diagnostics.append(_setting_diagnostic(f"{field}.path", "must be a non-empty string"))
    else:
        allowed = {"kind", "argv"}
        argv = entry.get("argv")
        if (
            not isinstance(argv, (list, tuple))
            or not argv
            or not all(_is_text(item) for item in argv)
        ):
            diagnostics.append(
                _setting_diagnostic(f"{field}.argv", "must be a non-empty list of strings")
            )
    for key in entry:
        if key not in allowed:
            diagnostics.append(
                _setting_diagnostic(f"{field}.{key}", "is not a field of this source kind")
            )
    return diagnostics


def _transcription_diagnostics(transcription: Any) -> list[str]:
    field = _SETTING_TRANSCRIPTION
    if not isinstance(transcription, Mapping):
        return [_setting_diagnostic(field, "must be a mapping")]
    diagnostics: list[str] = []
    for key in transcription:
        if key not in _TRANSCRIPTION_FIELDS:
            diagnostics.append(
                _setting_diagnostic(f"{field}.{key}", "is not a field of the transcription")
            )
    if "enabled" in transcription and not isinstance(transcription["enabled"], bool):
        diagnostics.append(_setting_diagnostic(f"{field}.enabled", "must be a boolean"))
    for key in _TRANSCRIPTION_TEXT_FIELDS:
        if key in transcription and not isinstance(transcription[key], str):
            diagnostics.append(_setting_diagnostic(f"{field}.{key}", "must be a string"))
    if "timeout_seconds" in transcription and not _is_positive_number(
        transcription["timeout_seconds"]
    ):
        diagnostics.append(
            _setting_diagnostic(f"{field}.timeout_seconds", "must be a finite positive number")
        )
    if "max_chars" in transcription and not _is_positive_int(transcription["max_chars"]):
        diagnostics.append(_setting_diagnostic(f"{field}.max_chars", "must be a positive integer"))
    return diagnostics


def _setting_diagnostic(field_name: str, reason: str) -> str:
    return f"module {MODULE_NAME!r}: field {field_name!r}: {reason}"


def _is_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def _is_finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _is_positive_number(value: Any) -> bool:
    return _is_finite_number(value) and value > 0


def _is_non_negative_number(value: Any) -> bool:
    return _is_finite_number(value) and value >= 0


@dataclass(frozen=True, slots=True)
class SourceSpec:
    """One configured source, as the settings declared it.

    ``argv`` is set for a ``command`` source, ``path`` for a ``file`` source.
    The spec is what the source factory receives; it is never rendered into a
    diagnostic, a trace or an observation.
    """

    name: str
    kind: str
    path: str | None = None
    argv: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, name: str, entry: Mapping[str, Any]) -> "SourceSpec":
        if entry["kind"] == SOURCE_KIND_FILE:
            return cls(name=name, kind=SOURCE_KIND_FILE, path=str(entry["path"]))
        return cls(
            name=name,
            kind=SOURCE_KIND_COMMAND,
            argv=tuple(str(item) for item in entry["argv"]),
        )


@dataclass(frozen=True, slots=True)
class _Transcription:
    """The transcription settings, parsed."""

    enabled: bool = False
    endpoint: str = ""
    model: str = ""
    api_key: str = ""
    language: str = ""
    timeout_seconds: float = DEFAULT_TRANSCRIPTION_TIMEOUT_SECONDS
    max_chars: int = DEFAULT_TRANSCRIPTION_MAX_CHARS

    @classmethod
    def from_mapping(cls, entry: Mapping[str, Any]) -> "_Transcription":
        return cls(
            enabled=bool(entry.get("enabled", False)),
            endpoint=str(entry.get("endpoint", "")),
            model=str(entry.get("model", "")),
            api_key=str(entry.get("api_key", "")),
            language=str(entry.get("language", "")),
            timeout_seconds=float(
                entry.get("timeout_seconds", DEFAULT_TRANSCRIPTION_TIMEOUT_SECONDS)
            ),
            max_chars=int(entry.get("max_chars", DEFAULT_TRANSCRIPTION_MAX_CHARS)),
        )


@dataclass(frozen=True, slots=True)
class _Settings:
    """The accepted settings, parsed once."""

    sources: Mapping[str, SourceSpec]
    default_source: str
    max_seconds: int
    max_bytes: int
    grace_seconds: float
    transcription: _Transcription
    required: bool

    @classmethod
    def from_mapping(cls, settings: Mapping[str, Any]) -> "_Settings":
        return cls(
            sources={
                name: SourceSpec.from_mapping(name, entry)
                for name, entry in settings[_SETTING_SOURCES].items()
            },
            default_source=str(settings[_SETTING_DEFAULT_SOURCE]),
            max_seconds=int(settings.get(_SETTING_MAX_SECONDS, DEFAULT_MAX_SECONDS)),
            max_bytes=int(settings.get(_SETTING_MAX_BYTES, DEFAULT_MAX_BYTES)),
            grace_seconds=float(settings.get(_SETTING_GRACE_SECONDS, DEFAULT_GRACE_SECONDS)),
            transcription=_Transcription.from_mapping(settings.get(_SETTING_TRANSCRIPTION, {})),
            required=bool(settings.get(_SETTING_REQUIRED, False)),
        )


# --------------------------------------------------------------------------- #
# The segment convention (decision 5)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Segment:
    """A segment cut to the convention: the bytes to store and what they hold."""

    data: bytes
    data_bytes: int

    @property
    def size(self) -> int:
        return len(self.data)

    @property
    def duration_ms(self) -> int:
        return self.data_bytes * 1000 // BYTES_PER_SECOND


def wav_data_layout(data: bytes) -> tuple[int, int] | None:
    """``(data_offset, declared_size)`` of the ``data`` chunk, or ``None``.

    ``None`` while *data* does not (yet) hold a RIFF/WAVE header up to the
    payload of its ``data`` chunk: a command source reads this to know when
    it holds enough audio.
    """

    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        return None
    position = 12
    while position + 8 <= len(data):
        kind = data[position : position + 4]
        (size,) = struct.unpack("<I", data[position + 4 : position + 8])
        if kind == b"data":
            return position + 8, size
        position += 8 + size + (size & 1)
    return None


def cut_segment(data: bytes, seconds: int) -> Segment:
    """Validate *data* against the convention and cut it to *seconds*.

    The chunks are walked in order (a pad byte follows an odd-sized chunk);
    the ``fmt `` chunk must precede ``data`` and declare PCM (format 1),
    16 000 Hz, one channel, 16 bits. The ``data`` payload runs for its
    declared size, or to the end of what was read when that size is a
    streaming placeholder or larger than the bytes held; it is cut to
    ``seconds × 32 000`` bytes (a whole number of frames) and the segment
    rewritten with the canonical 44-byte header whose ``RIFF`` and ``data``
    sizes match the bytes kept. Anything else — not RIFF/WAVE, another
    format, rate, channel count or width, no audio to keep — raises
    :class:`InvalidSegment`.
    """

    data = bytes(data)
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise InvalidSegment("not a RIFF/WAVE body")
    position = 12
    fmt_seen = False
    while position + 8 <= len(data):
        kind = data[position : position + 4]
        (size,) = struct.unpack("<I", data[position + 4 : position + 8])
        payload = position + 8
        if kind == b"fmt ":
            _check_fmt(data[payload : payload + size], size)
            fmt_seen = True
        elif kind == b"data":
            if not fmt_seen:
                raise InvalidSegment("data chunk precedes the fmt chunk")
            available = len(data) - payload
            held = available if size in _STREAMING_DATA_SIZES or size > available else size
            kept = min(held, int(seconds) * BYTES_PER_SECOND)
            kept -= kept % (CHANNELS * BITS_PER_SAMPLE // 8)
            if kept * 1000 // BYTES_PER_SECOND < 1:
                raise InvalidSegment("no audio in the data chunk")
            audio = data[payload : payload + kept]
            return Segment(data=_canonical_header(kept) + audio, data_bytes=kept)
        position = payload + size + (size & 1)
    raise InvalidSegment("no data chunk")


def _check_fmt(chunk: bytes, size: int) -> None:
    if size < 16 or len(chunk) < 16:
        raise InvalidSegment("fmt chunk too short")
    audio_format, channels, rate, _byte_rate, _align, bits = struct.unpack("<HHIIHH", chunk[:16])
    if audio_format != _WAVE_FORMAT_PCM:
        raise InvalidSegment("not PCM")
    if rate != SAMPLE_RATE_HZ:
        raise InvalidSegment("sample rate is not 16000 Hz")
    if channels != CHANNELS:
        raise InvalidSegment("not mono")
    if bits != BITS_PER_SAMPLE:
        raise InvalidSegment("not 16-bit")


def _canonical_header(data_bytes: int) -> bytes:
    block_align = CHANNELS * BITS_PER_SAMPLE // 8
    return (
        b"RIFF"
        + struct.pack("<I", 36 + data_bytes)
        + b"WAVE"
        + b"fmt "
        + struct.pack(
            "<IHHIIHH",
            16,
            _WAVE_FORMAT_PCM,
            CHANNELS,
            SAMPLE_RATE_HZ,
            BYTES_PER_SECOND,
            block_align,
            BITS_PER_SAMPLE,
        )
        + b"data"
        + struct.pack("<I", data_bytes)
    )


def probe_segment() -> bytes:
    """The transcription probe's segment: 0.25 s of silence, 8 044 bytes.

    Generated in memory at each probe, never read from disk.
    """

    data_bytes = int(PROBE_SEGMENT_SECONDS * BYTES_PER_SECOND)
    return _canonical_header(data_bytes) + bytes(data_bytes)


# --------------------------------------------------------------------------- #
# Sources (R3)
# --------------------------------------------------------------------------- #

#: The runner seam, shaped like ``asyncio.create_subprocess_exec``:
#: ``runner(*argv, **kwargs)`` awaits to a process exposing ``stdout.read(n)``,
#: ``terminate()``, ``kill()``, ``await wait()`` and ``returncode``; an
#: optional ``close_stdout()`` releases the pipe once reading has stopped.
SubprocessRunner = Callable[..., Awaitable[Any]]
Sleeper = Callable[[float], Awaitable[Any]]


class FileSource:
    """A ``file`` source: the WAV is re-read afresh at each call.

    The read is bounded to what the store could keep plus header room; the
    module cuts it to the requested seconds. Nothing runs, so :meth:`kill`
    has nothing to stop.
    """

    __slots__ = ("_limit", "_path", "name")

    def __init__(self, spec: SourceSpec, *, max_bytes: int) -> None:
        if spec.kind != SOURCE_KIND_FILE or spec.path is None:
            raise AudioInputModuleError(f"{MODULE_NAME}: source {spec.name!r} is not a file source")
        self.name = spec.name
        self._path = spec.path
        self._limit = int(max_bytes) + _HEADER_ALLOWANCE_BYTES

    def probe(self) -> str | None:
        """Why the source is unavailable now, or ``None`` when it is readable."""

        if not os.path.isfile(self._path) or not os.access(self._path, os.R_OK):
            return "is not a readable file"
        return None

    async def read(self, seconds: int) -> bytes:
        del seconds  # The module cuts the segment; the read is bounded in bytes.
        path = self._path
        limit = self._limit

        def _read() -> bytes:
            with open(path, "rb") as handle:
                return handle.read(limit)

        try:
            return await asyncio.to_thread(_read)
        except OSError:
            raise CaptureError("file could not be read") from None

    def kill(self) -> None:
        """Nothing records: a file read has no process to stop."""


class CommandSource:
    """A ``command`` source: ``argv`` started at the call, stdout read incrementally.

    Each call gets its own :class:`CommandRecording` through :meth:`begin`,
    so a kill reaches the recorder of that call and no other.
    """

    __slots__ = ("_argv", "_limit", "_runner", "_sleeper", "name")

    def __init__(
        self, spec: SourceSpec, *, runner: SubprocessRunner, sleeper: Sleeper, max_bytes: int
    ) -> None:
        if spec.kind != SOURCE_KIND_COMMAND or not spec.argv:
            raise AudioInputModuleError(
                f"{MODULE_NAME}: source {spec.name!r} is not a command source"
            )
        self.name = spec.name
        self._argv = spec.argv
        self._runner = runner
        self._sleeper = sleeper
        self._limit = int(max_bytes) + _HEADER_ALLOWANCE_BYTES

    def probe(self) -> str | None:
        """Why the program cannot run, or ``None`` when it resolves to an executable."""

        if shutil.which(self._argv[0]) is None:
            return "does not resolve to an executable"
        return None

    def begin(self) -> "CommandRecording":
        return CommandRecording(
            self._argv, runner=self._runner, sleeper=self._sleeper, limit=self._limit
        )


class CommandRecording:
    """One recorder run: started by :meth:`read`, stopped by the duration or :meth:`kill`.

    :meth:`read` spawns the recorder, reads its stdout in chunks and stops
    reading once the header and ``seconds × 32 000`` data bytes are held, or
    the buffer reached its byte bound — the recorder is then terminated —
    and terminates it when ``seconds`` elapsed on the sleeper, taking what it
    flushed until its stdout closes. Memory never holds more than the bound,
    however long a recorder streams. :meth:`kill` is synchronous and
    idempotent: one kill per recording, whatever triggers it.
    """

    __slots__ = ("_argv", "_killed", "_limit", "_process", "_runner", "_sleeper")

    def __init__(
        self, argv: Sequence[str], *, runner: SubprocessRunner, sleeper: Sleeper, limit: int
    ) -> None:
        self._argv = tuple(argv)
        self._runner = runner
        self._sleeper = sleeper
        self._limit = int(limit)
        self._process: Any | None = None
        self._killed = False

    async def read(self, seconds: int) -> bytes:
        if self._killed:
            return b""
        try:
            process = await self._runner(
                *self._argv,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                start_new_session=True,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            raise CaptureError("recorder could not be started") from None
        self._process = process
        if self._killed:
            # Killed while it was being started: it never records.
            self._kill_process()
            return b""

        needed = int(seconds) * BYTES_PER_SECOND
        buffer = bytearray()
        timer = asyncio.ensure_future(self._sleeper(float(seconds)))
        reading: asyncio.Future[Any] | None = None
        terminated = False
        try:
            while True:
                reading = asyncio.ensure_future(process.stdout.read(_READ_CHUNK))
                if not terminated:
                    done, _pending = await asyncio.wait(
                        {reading, timer}, return_when=asyncio.FIRST_COMPLETED
                    )
                    if reading not in done:
                        # The requested duration elapsed: stop the recorder and
                        # take what it flushes until its stdout closes.
                        self._terminate()
                        terminated = True
                chunk = await reading
                reading = None
                if not chunk:
                    break
                buffer += chunk
                if len(buffer) >= self._limit or _holds(buffer, needed):
                    break
            if not terminated:
                self._terminate()
            # Reading has stopped: unread output must not keep the pipe (and
            # so the process's reaping) pending until the call deadline.
            close_stdout = getattr(process, "close_stdout", None)
            if callable(close_stdout):
                close_stdout()
            await process.wait()
        except BaseException:
            if reading is not None and not reading.done():
                reading.cancel()
            self._kill_process()
            raise
        finally:
            if not timer.done():
                timer.cancel()
        return bytes(buffer[: self._limit])

    def kill(self) -> None:
        """Kill the recorder of this call now; a second call is a no-op."""

        if self._killed:
            return
        self._killed = True
        if self._process is not None:
            self._kill_now(self._process)

    def _kill_process(self) -> None:
        if self._process is None:
            return
        if not self._killed:
            self._killed = True
            self._kill_now(self._process)

    @staticmethod
    def _kill_now(process: Any) -> None:
        # No ``returncode`` guard: the recorder's leader may have exited while
        # a child it started still records in the same process group.
        try:
            process.kill()
        except ProcessLookupError:
            pass

    def _terminate(self) -> None:
        process = self._process
        if process is None:
            return
        try:
            process.terminate()
        except ProcessLookupError:
            pass


def _holds(buffer: bytearray, needed: int) -> bool:
    """Whether *buffer* holds a header and at least *needed* data bytes."""

    layout = wav_data_layout(bytes(buffer[:_HEADER_ALLOWANCE_BYTES]))
    if layout is None:
        return False
    offset, _declared = layout
    return len(buffer) - offset >= needed


class RecorderProcess:
    """The shipped recorder process: signals reach its whole process group.

    The recorder is started in its own session (``start_new_session``), so a
    configured wrapper script that launches the real recording tool does not
    leave that tool running past a kill. A kill also closes the stdout pipe,
    so a pending read ends when the process is gone. Signals go to the
    group even after the leader exited: a child may still be recording.
    """

    __slots__ = ("_process",)

    def __init__(self, process: asyncio.subprocess.Process) -> None:
        self._process = process

    @property
    def pid(self) -> int:
        return self._process.pid

    @property
    def returncode(self) -> int | None:
        return self._process.returncode

    @property
    def stdout(self) -> Any:
        return self._process.stdout

    def terminate(self) -> None:
        _signal_group(self._process, signal.SIGTERM)

    def kill(self) -> None:
        _signal_group(self._process, signal.SIGKILL)
        _close_stdout(self._process)

    def close_stdout(self) -> None:
        """Stop reading: the pipe closes, so :meth:`wait` needs no further read."""

        _close_stdout(self._process)

    async def wait(self) -> int:
        return await self._process.wait()


async def spawn_recorder(*argv: str, **kwargs: Any) -> RecorderProcess:
    """The default runner: ``asyncio.create_subprocess_exec`` behind :class:`RecorderProcess`."""

    return RecorderProcess(await asyncio.create_subprocess_exec(*argv, **kwargs))


def _signal_group(process: Any, signum: int) -> None:
    killpg = getattr(os, "killpg", None)
    if killpg is not None:
        try:
            killpg(process.pid, signum)
            return
        except OSError:
            pass
    if process.returncode is None:
        try:
            process.send_signal(signum)
        except ProcessLookupError:
            pass


def _close_stdout(process: Any) -> None:
    # asyncio exposes no public handle on a subprocess's pipe transports: the
    # subprocess transport is reached through ``Process._transport``, whose
    # ``get_pipe_transport`` is public.
    transport = getattr(process, "_transport", None)
    get_pipe_transport = getattr(transport, "get_pipe_transport", None)
    pipe = get_pipe_transport(1) if callable(get_pipe_transport) else None
    if pipe is not None:
        pipe.close()


def _default_session_factory() -> Any:
    if aiohttp is None:
        raise RuntimeError("aiohttp is not installed")
    return aiohttp.ClientSession()


SESSION_FACTORY: Callable[[], Any] = _default_session_factory
"""The shipped transcription transport: an ``aiohttp`` session, made on first use."""


def default_source_factory(
    spec: SourceSpec, *, runner: SubprocessRunner, sleeper: Sleeper, max_bytes: int
) -> FileSource | CommandSource:
    """Build the shipped source object for *spec*."""

    if spec.kind == SOURCE_KIND_FILE:
        return FileSource(spec, max_bytes=max_bytes)
    return CommandSource(spec, runner=runner, sleeper=sleeper, max_bytes=max_bytes)


# --------------------------------------------------------------------------- #
# The handle
# --------------------------------------------------------------------------- #


class _CaptureProvider:
    """The ``audio.capture`` provider the executor invokes (R3)."""

    __slots__ = ("_module",)

    name = PROVIDER_NAME

    def __init__(self, module: "AudioInputModule") -> None:
        self._module = module

    async def invoke(self, invocation: Any) -> ActionObservation:
        return await self._module._invoke_capture(invocation)


class _Capture:
    """One recording in flight: its recorder, and the status its kill fixed."""

    __slots__ = ("interrupt", "recording", "status")

    def __init__(self, recording: Any) -> None:
        self.recording = recording
        self.status: str | None = None
        self.interrupt: asyncio.Future[None] = asyncio.get_running_loop().create_future()


class _CallFailure(Exception):
    def __init__(self, status: str, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class _Answer:
    """What one transcription request came back with: its status, its text on ``ok``."""

    status: str
    text: str | None = None


class _Transcribing:
    """One call in its transcription phase: what ``drain`` waits for and interrupts."""

    __slots__ = ("finished", "interrupt")

    def __init__(self) -> None:
        loop = asyncio.get_running_loop()
        self.interrupt: asyncio.Future[None] = loop.create_future()
        self.finished: asyncio.Future[None] = loop.create_future()


@dataclass(frozen=True, slots=True)
class _Stored:
    """A capture complete in the store: what its ``success`` record is authored from."""

    wav: bytes
    result: Mapping[str, Any]
    part: Mapping[str, Any]
    provenance: Mapping[str, Any]

    def observation(
        self, transcription_status: str, transcription: Mapping[str, Any] | None
    ) -> ActionObservation:
        result = {**self.result, "transcription_status": transcription_status}
        part = dict(self.part)
        if transcription is not None:
            part["transcription"] = dict(transcription)
        return ActionObservation(
            status="success", provenance=dict(self.provenance), result=result, parts=(part,)
        )


class AudioInputModule:
    """The v2 handle: probe and bind at ``prepare``, record on demand."""

    def __init__(
        self,
        context: Any,
        settings: _Settings,
        sources: Mapping[str, Any],
        *,
        sleeper: Sleeper,
        transport_factory: Callable[[], Any],
    ) -> None:
        self._actions = context.actions
        self._attachments = context.attachments
        self._clock: Callable[[], float] = context.clock
        self._supervision = getattr(context, "supervision", None)
        self._settings = settings
        self._sources = dict(sources)
        self._sleeper = sleeper
        # The transcription transport: made on first use, released by close.
        self._transport_factory = transport_factory
        self._session: Any | None = None
        self._transcription_available = settings.transcription.enabled
        self._provider = _CaptureProvider(self)
        self._captures: set[_Capture] = set()
        self._transcribing: set[_Transcribing] = set()
        self._usable: frozenset[str] = frozenset()
        self._prepared = False
        self._draining = False
        self._closed = False

    @property
    def settings(self) -> _Settings:
        return self._settings

    @property
    def sources(self) -> Mapping[str, Any]:
        """The source objects by name, for inspection."""

        return dict(self._sources)

    @property
    def usable_sources(self) -> frozenset[str]:
        """The sources whose probe succeeded at ``prepare``."""

        return self._usable

    @property
    def transcription_available(self) -> bool:
        """Whether captures send a transcription request (enabled, probe not failed)."""

        return self._transcription_available

    # -- lifecycle hooks ---------------------------------------------------- #

    async def prepare(self) -> None:
        """Probe every source, bind ``audio.capture`` when one is usable.

        A ``command`` source must name an executable resolved on ``PATH`` or
        by its own path, a ``file`` source must be a readable file; a source
        without a ``probe`` is taken as usable. With ``required: true`` the
        first unusable source fails ``prepare`` naming the field. Otherwise
        the unusable sources are reported once through ``module.degraded``
        with a value-free reason naming them; ``audio.capture`` is bound and the module marked
        ready when at least one source is usable, and left declared but
        unbound — reported as the degraded capability — when none is.

        With transcription enabled and a usable source, one probe request
        carrying :func:`probe_segment` is sent, bounded by
        ``timeout_seconds`` on the sleeper, and must answer a transcription.
        When it does not, the transport is released, ``required: true``
        fails ``prepare`` naming ``transcription.endpoint``, and otherwise
        the transcription alone is degraded: ``audio.capture`` stays bound,
        one ``module.degraded`` carries the value-free reason ``transcription
        unavailable`` and no capability (decision 11), and every capture
        reports ``unavailable`` without a request. Nothing is spawned and no
        task is started.
        """

        if self._prepared or self._closed:
            return
        settings = self._settings
        unusable: list[str] = []
        for name in settings.sources:
            if _probe(self._sources[name]) is not None:
                if settings.required:
                    raise AudioInputModuleError(
                        f"{MODULE_NAME} prepare: field '{_SETTING_SOURCES}.{name}' unavailable"
                    )
                unusable.append(name)
        usable = frozenset(name for name in settings.sources if name not in unusable)
        transcription_failed = False
        if settings.transcription.enabled and usable:
            transcription_failed = not await self._probe_transcription()
            if transcription_failed:
                self._transcription_available = False
                await self._close_session()
                if settings.required:
                    raise AudioInputModuleError(
                        f"{MODULE_NAME} prepare: field "
                        f"'{_SETTING_TRANSCRIPTION}.endpoint' unavailable"
                    )
        self._usable = usable
        spec = _declared_capture_spec()
        try:
            if self._usable:
                self._actions.register(spec, self._provider, provider_name=PROVIDER_NAME)
            else:
                self._actions.declare(spec)
        except Exception:
            raise AudioInputModuleError(f"{MODULE_NAME} prepare: action binding failed") from None
        self._prepared = True
        if not self._usable:
            await self._report_degraded(
                f"{MODULE_NAME}: no audio source is usable", [CAPTURE_ACTION]
            )
            return
        if unusable:
            names = ", ".join(repr(name) for name in unusable)
            await self._report_degraded(f"{MODULE_NAME}: unavailable sources {names}", [])
        if transcription_failed:
            await self._report_degraded(TRANSCRIPTION_DEGRADED_REASON, [])
        self._actions.mark_ready()

    async def drain(self, deadline_seconds: float) -> None:
        """Kill every running recorder; let the transcriptions end within the deadline.

        A recorder's call ends ``cancelled`` with 0 bytes stored; a call
        arriving afterwards ends ``cancelled`` before any process is spawned.
        A call already in its transcription phase has stored its capture: it
        is not cancelled and nothing is killed — its request may still answer
        within *deadline_seconds* on the clock, and is abandoned
        (``failed:stt_timed_out``, the capture still ``success``) when the
        drain deadline passes first.
        """

        self._draining = True
        self._kill_all("cancelled")
        await self._join_transcriptions(max(float(deadline_seconds), 0.0))

    async def close(self) -> None:
        """Withdraw readiness, kill whatever still records, release the transport.

        A transcription still pending is abandoned (its capture ends
        ``success``, ``failed:stt_timed_out``) before the transport closes.
        """

        if self._closed:
            return
        self._closed = True
        self._draining = True
        if self._prepared and self._usable:
            self._actions.mark_not_ready()
        self._kill_all("cancelled")
        self._interrupt_transcriptions()
        await self._close_session()

    async def _join_transcriptions(self, budget: float) -> None:
        """Wait for the transcribing calls up to *budget* on the sleeper, then interrupt them."""

        def pending() -> set[asyncio.Future[None]]:
            return {item.finished for item in self._transcribing if not item.finished.done()}

        waiting = pending()
        if not waiting:
            return
        if budget <= 0:
            self._interrupt_transcriptions()
            return
        timer = asyncio.ensure_future(self._sleeper(budget))
        try:
            while waiting and not timer.done():
                await asyncio.wait(waiting | {timer}, return_when=asyncio.FIRST_COMPLETED)
                waiting = pending()
        finally:
            _abandon(timer)
            self._interrupt_transcriptions()

    def _interrupt_transcriptions(self) -> None:
        for item in tuple(self._transcribing):
            if not item.interrupt.done():
                item.interrupt.set_result(None)

    async def _close_session(self) -> None:
        session, self._session = self._session, None
        close = getattr(session, "close", None)
        if callable(close):
            try:
                outcome = close()
                if inspect.isawaitable(outcome):
                    await outcome
            except Exception:  # noqa: BLE001 - a transport that fails to close is gone anyway
                pass

    def _kill_all(self, status: str) -> None:
        for capture in tuple(self._captures):
            self._kill(capture, status)

    def _kill(self, capture: _Capture, status: str) -> None:
        """Kill *capture*'s recorder once; the first trigger fixes its status."""

        if capture.status is not None:
            return
        capture.status = status
        if not capture.interrupt.done():
            capture.interrupt.set_result(None)
        kill = getattr(capture.recording, "kill", None)
        if callable(kill):
            try:
                kill()
            except Exception:  # noqa: BLE001 - the call's record stands regardless
                pass

    async def _report_degraded(self, reason: str, capabilities: Sequence[str]) -> None:
        degraded = getattr(self._supervision, "degraded", None)
        if not callable(degraded):
            return
        try:
            outcome = degraded(reason=reason, capabilities=list(capabilities))
            if inspect.isawaitable(outcome):
                await outcome
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a health report must not fail prepare
            return

    # -- the capture (R3, R4, R10) ------------------------------------------- #

    async def _invoke_capture(self, invocation: Any) -> ActionObservation:
        """Serve one ``audio.capture`` call for the executor."""

        # A capture records a sound; nothing reaches the outside world, so an
        # interruption is a certain ``timeout``/``cancelled``.
        invocation.mark_not_emitted()
        call = invocation.call
        expiry = min(float(call.deadline), self._clock() + float(invocation.spec.timeout_seconds))
        destination = call.destination
        provenance: dict[str, Any] = {
            "provider": PROVIDER_NAME,
            "platform": destination.platform,
            "channel_id": destination.channel_id,
            "route": CAPTURE_ACTION,
        }
        try:
            name, seconds = self._admit(call.arguments, expiry)
            provenance["source"] = name
            data = await self._record(self._sources[name], seconds, expiry)
            captured_at = float(self._clock())
            stored = self._store(call.run_id, name, data, seconds, captured_at, provenance)
        except _CallFailure as failure:
            return ActionObservation(
                status=failure.status,
                provenance=provenance,
                error={"code": failure.code, "message": failure.message, "retryable": False},
            )
        # The capture is complete: from here the record is a ``success`` with
        # its ``audio_ref`` whatever happens to the transcription (R3), and it
        # is authored and returned with no await after the decision, so the
        # executor's completion stamp is the decision instant.
        status, transcription = await self._transcribe(stored.wav, expiry)
        return stored.observation(status, transcription)

    def _admit(self, arguments: Mapping[str, Any], expiry: float) -> tuple[str, int]:
        """The source and seconds of the call, or the refusal — before any spawn."""

        settings = self._settings
        if self._closed:
            raise _CallFailure("error", _ERROR_PROVIDER_CLOSED, f"{MODULE_NAME}: closed")
        if self._draining:
            raise _CallFailure("cancelled", ERROR_CANCELLED, f"{MODULE_NAME}: shutting down")
        seconds = arguments.get("seconds")
        if (
            not isinstance(seconds, int)
            or isinstance(seconds, bool)
            or not 1 <= seconds <= settings.max_seconds
        ):
            raise _CallFailure(
                "error",
                ERROR_INVALID_ARGUMENTS,
                f"seconds must be an integer from 1 to {settings.max_seconds}",
            )
        requested = arguments.get("source")
        name = settings.default_source if requested is None else requested
        if not isinstance(name, str) or name not in self._sources:
            raise _CallFailure(
                "error", ERROR_INVALID_ARGUMENTS, "source is not one this module configures"
            )
        # The admission reserve (AC41's first authorized subtraction): evaluated
        # once, here, before any process is spawned; the kill stays at expiry.
        if seconds > (expiry - self._clock()) - settings.grace_seconds:
            raise _CallFailure(
                "error",
                ERROR_CAPTURE_TOO_LONG,
                f"{seconds} s cannot be recorded before the call deadline "
                f"with a {settings.grace_seconds:g} s reserve",
            )
        return name, seconds

    async def _record(self, source: Any, seconds: int, expiry: float) -> bytes:
        """Record *seconds* from *source*, killed at *expiry*, on drain or on cancellation.

        The read runs beside a watcher parked on the sleeper until *expiry*
        itself. Whichever settles the call first wins: the read (its bytes),
        the watcher (kill, ``capture_timed_out``), ``drain`` (kill,
        ``cancelled``) or a ``CancelledError`` — caught, the recorder killed,
        ``capture_timed_out`` at or after *expiry* and ``cancelled`` before,
        the record returned rather than re-raised so the executor adopts it
        (R10). An interrupted call abandons the read and the watcher without
        awaiting anything and returns at once.
        """

        begin = getattr(source, "begin", None)
        recording = begin() if callable(begin) else source
        capture = _Capture(recording)
        self._captures.add(capture)
        reader = asyncio.ensure_future(recording.read(seconds))
        watcher = asyncio.ensure_future(self._sleeper(max(0.0, expiry - self._clock())))
        try:
            try:
                await asyncio.wait(
                    {reader, watcher, capture.interrupt}, return_when=asyncio.FIRST_COMPLETED
                )
            except asyncio.CancelledError:
                self._kill(capture, "timeout" if self._clock() >= expiry else "cancelled")
            else:
                if capture.status is None and not reader.done():
                    self._kill(capture, "timeout")
        finally:
            self._captures.discard(capture)
            _abandon(watcher)
        if capture.status is not None:
            _abandon(reader)
            if capture.status == "timeout":
                raise _CallFailure(
                    "error",
                    ERROR_CAPTURE_TIMED_OUT,
                    "recorder killed at the call deadline; nothing was stored",
                )
            raise _CallFailure("cancelled", ERROR_CANCELLED, "recorder killed on cancellation")
        if reader.cancelled():
            raise _CallFailure("error", ERROR_CAPTURE_FAILED, "source was interrupted")
        failure = reader.exception()
        if failure is not None:
            raise _CallFailure("error", ERROR_CAPTURE_FAILED, "source could not be read")
        if self._clock() >= expiry:
            # Finished, but observed at or after the deadline: not a capture
            # within the call — the deadline's record, nothing stored.
            raise _CallFailure(
                "error",
                ERROR_CAPTURE_TIMED_OUT,
                "recording ended at the call deadline; nothing was stored",
            )
        data = reader.result()
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise _CallFailure("error", ERROR_INVALID_RESULT, "source produced no bytes")
        return bytes(data)

    def _store(
        self,
        run_id: str,
        name: str,
        data: bytes,
        seconds: int,
        captured_at: float,
        provenance: Mapping[str, Any],
    ) -> _Stored:
        try:
            segment = cut_segment(data, seconds)
        except InvalidSegment as invalid:
            raise _CallFailure(
                "error",
                ERROR_INVALID_RESULT,
                f"source {name!r} produced no 16 kHz mono 16-bit PCM WAV ({invalid})",
            ) from None
        if segment.size > self._settings.max_bytes:
            raise _CallFailure(
                "error", ERROR_ATTACHMENT_REFUSED, f"source {name!r}: segment exceeds max_bytes"
            )
        try:
            ref = self._attachments.put(run_id, segment.data, content_type=CONTENT_TYPE_WAV)
        except AttachmentRefused as refused:
            raise _CallFailure(
                "error",
                ERROR_ATTACHMENT_REFUSED,
                f"attachment store refused the segment ({refused.limit})",
            ) from None

        result = {
            "source": name,
            "content_type": CONTENT_TYPE_WAV,
            "size": ref.size,
            "duration_ms": segment.duration_ms,
            "sample_rate_hz": SAMPLE_RATE_HZ,
            "channels": CHANNELS,
            "captured_at": captured_at,
            "transcription_status": TRANSCRIPTION_DISABLED,
        }
        part: dict[str, Any] = {
            "type": PART_TYPE_AUDIO_REF,
            "attachment_id": ref.attachment_id,
            "content_type": CONTENT_TYPE_WAV,
            "size": ref.size,
            "duration_ms": segment.duration_ms,
            "sample_rate_hz": SAMPLE_RATE_HZ,
            "channels": CHANNELS,
            "captured_at": captured_at,
            "provider_id": PROVIDER_ID,
        }
        return _Stored(wav=segment.data, result=result, part=part, provenance=dict(provenance))

    # -- the optional transcription (R3; decision 1) --------------------------- #

    async def _transcribe(
        self, wav: bytes, expiry: float
    ) -> tuple[str, Mapping[str, Any] | None]:
        """The transcription status of a stored capture, and its transcription on ``ok``.

        Never raises and never re-raises a cancellation: the capture it
        follows is complete. The clock is read once for the window —
        ``transcription_reserve`` is R3's 1 s, the second deadline
        subtraction AC41 authorizes, bounding this request only — and an
        exhausted window sends nothing. Otherwise: *attempt* one request
        against the bound ``min(timeout_seconds, transcription_window)``;
        *decide* at the wake, whatever woke the call, on the clock read then
        — an answer in hand before ``transcription_edge`` gives its status,
        anything else abandons the request (``failed:stt_timed_out``, a late
        text not adopted); the caller then authors the record with no
        further await. The last wake this schedules is the edge, never
        anything at or after ``expiry``.
        """

        settings = self._settings.transcription
        if not settings.enabled:
            return TRANSCRIPTION_DISABLED, None
        if not self._transcription_available:
            return TRANSCRIPTION_UNAVAILABLE, None
        now = self._clock()
        transcription_reserve = TRANSCRIPTION_RESERVE_SECONDS
        transcription_edge = expiry - transcription_reserve
        transcription_window = transcription_edge - now
        if transcription_window <= 0:
            return TRANSCRIPTION_SKIPPED_DEADLINE, None

        transcribing = _Transcribing()
        self._transcribing.add(transcribing)
        request: asyncio.Future[_Answer] | None = None
        bound: asyncio.Future[Any] | None = None
        answer: _Answer | None = None
        try:
            # Attempt.
            try:
                session = await self._session_or_none()
                if session is None:
                    answer = _Answer(TRANSCRIPTION_STT_UNAVAILABLE)
                else:
                    request = asyncio.ensure_future(self._request(session, wav))
                    bound = asyncio.ensure_future(
                        self._sleeper(min(settings.timeout_seconds, transcription_window))
                    )
                    await asyncio.wait(
                        {request, bound, transcribing.interrupt},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
            except asyncio.CancelledError:
                # Cancelled after storing: the request is abandoned, not
                # awaited, and the capture's record is returned normally.
                pass
            # Decide, on the clock read at the wake.
            decided_at = float(self._clock())
            if request is not None and request.done() and not request.cancelled():
                failure = request.exception()
                answer = _Answer(TRANSCRIPTION_STT_UNAVAILABLE) if failure else request.result()
            if answer is None or decided_at >= transcription_edge:
                return TRANSCRIPTION_STT_TIMED_OUT, None
            if answer.status != TRANSCRIPTION_OK or answer.text is None:
                return answer.status, None
            text = answer.text
            return TRANSCRIPTION_OK, {
                "text": text[: settings.max_chars],
                "transcribed_at": decided_at,
                "provider_id": TRANSCRIPTION_PROVIDER_ID,
                "truncated": len(text) > settings.max_chars,
            }
        finally:
            self._transcribing.discard(transcribing)
            if not transcribing.finished.done():
                transcribing.finished.set_result(None)
            if bound is not None:
                _abandon(bound)
            if request is not None:
                _abandon(request)

    async def _probe_transcription(self) -> bool:
        """One probe request with :func:`probe_segment`, bounded by ``timeout_seconds``."""

        settings = self._settings.transcription
        session = await self._session_or_none()
        if session is None:
            return False
        request = asyncio.ensure_future(self._request(session, probe_segment()))
        timer = asyncio.ensure_future(self._sleeper(settings.timeout_seconds))
        try:
            await asyncio.wait({request, timer}, return_when=asyncio.FIRST_COMPLETED)
        except BaseException:
            _abandon(request)
            raise
        finally:
            _abandon(timer)
        if not request.done():
            # The bound won: the probe failed, its request is abandoned.
            _abandon(request)
            return False
        if request.cancelled() or request.exception() is not None:
            return False
        return request.result().status == TRANSCRIPTION_OK

    async def _session_or_none(self) -> Any | None:
        """The transport, made on first use; ``None`` when it cannot be made."""

        if not self._settings.transcription.endpoint:
            return None
        if self._session is None:
            try:
                created = self._transport_factory()
                if inspect.isawaitable(created):
                    created = await created
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - reported as the transport being unavailable
                return None
            self._session = created
        return self._session

    async def _request(self, session: Any, wav: bytes) -> _Answer:
        """POST *wav* as a multipart body; the answer's status and text.

        The WAV is streamed from memory — the transport never sees a path.
        A transport failure — before the answer or while its body is read —
        is ``failed:stt_unavailable``, a non-2xx answer
        ``failed:stt_failed`` (its body never read), a 2xx answer that is not
        JSON with a string ``text`` — or larger than the answer bound —
        ``failed:invalid_transcription``.
        """

        settings = self._settings.transcription
        headers: dict[str, str] = {}
        if settings.api_key:
            headers["Authorization"] = f"Bearer {settings.api_key}"
        response: Any | None = None
        try:
            try:
                response = await session.post(
                    settings.endpoint, data=self._form(wav), headers=headers
                )
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - any transport failure is the same outcome
                return _Answer(TRANSCRIPTION_STT_UNAVAILABLE)
            status = getattr(response, "status", None)
            if not isinstance(status, int) or isinstance(status, bool) or not 200 <= status < 300:
                return _Answer(TRANSCRIPTION_STT_FAILED)
            try:
                body = await self._read_answer(response)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - a body lost mid-answer is a transport failure
                return _Answer(TRANSCRIPTION_STT_UNAVAILABLE)
            try:
                decoded = json.loads(body.decode("utf-8")) if body is not None else None
            except Exception:  # noqa: BLE001 - a body that does not decode is not a transcription
                return _Answer(TRANSCRIPTION_INVALID)
            text = decoded.get("text") if isinstance(decoded, Mapping) else None
            if not isinstance(text, str):
                return _Answer(TRANSCRIPTION_INVALID)
            return _Answer(TRANSCRIPTION_OK, text)
        finally:
            if response is not None:
                await _release(response)

    def _form(self, wav: bytes) -> Any:
        """The multipart body: the WAV file, ``model``, and ``language`` when set."""

        settings = self._settings.transcription
        if aiohttp is None:  # pragma: no cover - production installs dependencies
            fields: dict[str, Any] = {_FIELD_FILE: io.BytesIO(wav), _FIELD_MODEL: settings.model}
            if settings.language:
                fields[_FIELD_LANGUAGE] = settings.language
            return fields
        form = aiohttp.FormData()
        form.add_field(
            _FIELD_FILE, io.BytesIO(wav), filename=_UPLOAD_FILENAME, content_type=CONTENT_TYPE_WAV
        )
        form.add_field(_FIELD_MODEL, settings.model)
        if settings.language:
            form.add_field(_FIELD_LANGUAGE, settings.language)
        return form

    async def _read_answer(self, response: Any) -> bytes | None:
        """The 2xx body, or ``None`` when it exceeds the answer bound.

        Read in chunks off the response's stream so an oversized (or
        endless) answer never holds more than one chunk past the bound; a
        response with no stream is read whole.
        """

        limit = (
            self._settings.transcription.max_chars * _ANSWER_BYTES_PER_CHAR
            + _ANSWER_ALLOWANCE_BYTES
        )
        stream = getattr(response, "content", None)
        read = getattr(stream, "read", None)
        if not callable(read):
            body = await response.read()
            body = bytes(body)
            return body if len(body) <= limit else None
        parts: list[bytes] = []
        total = 0
        while True:
            chunk = await read(_ANSWER_CHUNK)
            if not chunk:
                break
            parts.append(bytes(chunk))
            total += len(chunk)
            if total > limit:
                return None
        return b"".join(parts)


def _probe(source: Any) -> str | None:
    """Why *source* is unavailable, or ``None``; a source without ``probe`` is usable."""

    probe = getattr(source, "probe", None)
    if not callable(probe):
        return None
    try:
        reason = probe()
    except Exception:  # noqa: BLE001 - a probe that fails is an unusable source
        return "could not be checked"
    return reason if isinstance(reason, str) and reason else None


def _abandon(task: "asyncio.Future[Any]") -> None:
    """Cancel *task* without waiting for it; its outcome is consumed when it ends."""

    if not task.done():
        task.cancel()
    task.add_done_callback(_consume)


def _consume(task: "asyncio.Future[Any]") -> None:
    if not task.cancelled():
        task.exception()


async def _release(response: Any) -> None:
    release = getattr(response, "release", None)
    if not callable(release):
        return
    try:
        outcome = release()
        if inspect.isawaitable(outcome):
            await outcome
    except Exception:  # noqa: BLE001 - a response that fails to release is gone anyway
        pass


# --------------------------------------------------------------------------- #
# Activation (R7)
# --------------------------------------------------------------------------- #


async def activate(
    context: Any,
    settings: Mapping[str, Any],
    catalog: Mapping[str, Mapping[str, Any]],
) -> AudioInputModule:
    """Build the handle from the scoped runtime context (R7).

    *context* is the module-scoped view of the versioned runtime: the action
    facade the provider is registered on at ``prepare``, the attachment store
    every segment is leased into, the clock every instant is read from and
    the supervision surface an unusable source is reported on. Settings are
    checked through the same hook the loader ran. The seams
    ``_source_factory``, ``_subprocess_runner``, ``_transcription_transport``
    and ``_sleeper`` are read from *settings* and default to the shipped
    sources, :func:`spawn_recorder`, an ``aiohttp`` session factory (the
    session is made on first use, never here) and ``asyncio.sleep``.
    Nothing is spawned and no transport is opened here.
    """

    del catalog  # Capabilities are declared by the colocated manifest.
    _require_runtime_surfaces(context)
    if not isinstance(settings, Mapping):
        raise AudioInputModuleError(f"{MODULE_NAME} configuration: settings must be a mapping")
    diagnostics = validate_settings(settings)
    if diagnostics:
        raise AudioInputModuleError(
            f"{MODULE_NAME} configuration: settings were refused "
            f"({len(diagnostics)} diagnostics)"
        )
    parsed = _Settings.from_mapping(settings)
    runner = settings.get(_SEAM_SUBPROCESS_RUNNER, spawn_recorder)
    sleeper = settings.get(_SEAM_SLEEPER, asyncio.sleep)
    factory = settings.get(_SEAM_SOURCE_FACTORY)
    sources: dict[str, Any] = {}
    for name, spec in parsed.sources.items():
        try:
            if factory is None:
                built = default_source_factory(
                    spec, runner=runner, sleeper=sleeper, max_bytes=parsed.max_bytes
                )
            else:
                built = factory(spec)
            if inspect.isawaitable(built):
                built = await built
        except AudioInputModuleError:
            raise
        except Exception:
            raise AudioInputModuleError(
                f"{MODULE_NAME} configuration: source {name!r} could not be built"
            ) from None
        if not callable(getattr(built, "read", None)) and not callable(
            getattr(built, "begin", None)
        ):
            raise AudioInputModuleError(
                f"{MODULE_NAME} configuration: source {name!r} has no read method"
            )
        sources[name] = built
    return AudioInputModule(
        context,
        parsed,
        sources,
        sleeper=sleeper,
        transport_factory=settings.get(_SEAM_TRANSCRIPTION_TRANSPORT, SESSION_FACTORY),
    )


def _require_runtime_surfaces(context: Any) -> None:
    """Refuse a context lacking the actions facade, a store or a clock.

    Duck-typed so a harness may inject fakes, while a mis-wired runtime is
    refused at activation with one diagnostic rather than at the first
    capture. Without a store no ``audio_ref`` can be leased.
    """

    actions = getattr(context, "actions", None)
    attachments = getattr(context, "attachments", None)
    clock = getattr(context, "clock", None)
    if (
        actions is None
        or not all(
            callable(getattr(actions, method, None))
            for method in ("register", "declare", "mark_ready", "mark_not_ready")
        )
        or attachments is None
        or not callable(getattr(attachments, "put", None))
        or not callable(clock)
    ):
        raise AudioInputModuleError(f"{MODULE_NAME} activation: runtime context is invalid")


def _declared_capture_spec() -> ActionSpec:
    """Build the ``audio.capture`` contract from the colocated manifest.

    The spec registered at ``prepare`` must equal the one the loader declared
    at discovery, field for field: reading the same file keeps the two from
    drifting, and the registry refuses a redeclaration that differs.
    """

    try:
        manifest = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))
        entry = next(
            item
            for item in manifest["actions"]
            if isinstance(item, Mapping) and item.get("name") == CAPTURE_ACTION
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
        )
    except Exception:
        raise AudioInputModuleError(
            f"{MODULE_NAME} prepare: manifest declaration of {CAPTURE_ACTION!r} is invalid"
        ) from None


__all__ = [
    "BYTES_PER_SECOND",
    "CAPTURE_ACTION",
    "CHANNELS",
    "CONTENT_TYPE_WAV",
    "DEFAULT_GRACE_SECONDS",
    "DEFAULT_MAX_BYTES",
    "DEFAULT_MAX_SECONDS",
    "ERROR_ATTACHMENT_REFUSED",
    "ERROR_CAPTURE_FAILED",
    "ERROR_CAPTURE_TIMED_OUT",
    "ERROR_CAPTURE_TOO_LONG",
    "ERROR_INVALID_RESULT",
    "MANIFEST_PATH",
    "MODULE_NAME",
    "PROVIDER_ID",
    "PROVIDER_NAME",
    "SAMPLE_RATE_HZ",
    "SOURCE_KINDS",
    "SOURCE_KIND_COMMAND",
    "SOURCE_KIND_FILE",
    "PROBE_SEGMENT_SECONDS",
    "SESSION_FACTORY",
    "TRANSCRIPTION_DEGRADED_REASON",
    "TRANSCRIPTION_DISABLED",
    "TRANSCRIPTION_INVALID",
    "TRANSCRIPTION_OK",
    "TRANSCRIPTION_PROVIDER_ID",
    "TRANSCRIPTION_RESERVE_SECONDS",
    "TRANSCRIPTION_SKIPPED_DEADLINE",
    "TRANSCRIPTION_STT_FAILED",
    "TRANSCRIPTION_STT_TIMED_OUT",
    "TRANSCRIPTION_STT_UNAVAILABLE",
    "TRANSCRIPTION_UNAVAILABLE",
    "WAV_HEADER_BYTES",
    "AudioInputModule",
    "AudioInputModuleError",
    "CaptureError",
    "CommandRecording",
    "CommandSource",
    "FileSource",
    "InvalidSegment",
    "RecorderProcess",
    "Segment",
    "SourceSpec",
    "activate",
    "cut_segment",
    "default_source_factory",
    "probe_segment",
    "spawn_recorder",
    "validate_settings",
    "wav_data_layout",
]
