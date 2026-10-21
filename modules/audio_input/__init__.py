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
*author* the ``success`` record and return it with no further await, at or
before the edge. The completion stamp is not the module's: the executor's
``core/actions.py::_observe`` takes it after the provider returns, so the
real delay past the edge is the wake delay plus any execution or preemption
before that stamp. The reserve mitigates that risk, it does not prove the
outcome: a delay of the whole reserve stamps the record at or after
``expiry``, a late ``success`` the executor replaces (the README's declared
limit). A 2xx
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
``_transcription_transport`` the HTTP session of the optional transcription,
``_detector_session`` the HTTP session of an ``http`` wake-word detector
and ``_sleeper`` the sleep every bound is raced against, so a deadline is
exercised on an injected clock without a sleeping child. They are read from
*settings* and never from a configuration file.

**The voice input** (phase 8 R7). ``listen`` (off by default) and
``wake_word.detector`` configure it; they are checked only when
``listen.enabled`` is true, each refusal naming its key and echoing no value.
The declared ``wake_word`` type is decided by :func:`_wake_word_hook`, which
reads the candidate's ``payload.wake_word.detected`` and nothing else. A
listening module sends no transcription probe at ``prepare`` — the only
transcription request it makes is the one an accepted candidate's segment
needs. With listening off no detector and no source is ever started and the
module is the phase-7 one, its probe included.

**One iteration of listening** (R7, A5) — :meth:`AudioInputModule.listen_once`
— runs four steps in this order and never in another. (1) *Verdict*: the
configured detector is awaited once, with no deadline of the companion's —
a ``command`` detector (:class:`CommandDetector`) is started through the
``_subprocess_runner`` seam with its standard input empty, exit 0 with a
trimmed standard output of 1 to 64 characters is a detection, exit 1 no
verdict, anything else a failure; an ``http`` detector (:class:`HttpDetector`)
sends one request carrying no audio through the ``_detector_session`` seam,
with no request timeout, and only ``{"detected": true, "phrase": …}`` or
``{"detected": false}`` are verdicts. A failure is a fixed code, never a
stringified exception. (2) *Candidate*: a detection builds a schema-2
``speech`` candidate (:func:`speech_candidate`) carrying ``payload.wake_word``
and the phrase as its text, decided by ``context.triggers.evaluate_async``
and traced; it is never published. (3) *Acquisition*: only an accepted
candidate invokes ``listen.source`` once, with ``AUDIO_INPUT_PRE_ROLL_SECONDS``
and ``AUDIO_INPUT_MAX_SECONDS`` in its environment; a WAV whose header is
not honest about its data, lasts more than ``listen.max_seconds`` by that
header or weighs more than ``max_bytes`` is a counted source failure — never
trimmed or padded — and the segment is transcribed once through the
``transcription`` settings, held in a local only until then: nothing reaches
the attachment store, the disk or the bus. (4) *Event*: a non-empty
transcription is published once as a ``speech`` event with the candidate's
author, ``message_id``, destination and source and no ``wake_word``; it is
never submitted to the engine. Admission is exactly once, as for the
operator's press: a scheduler the context carries is admitted into directly;
otherwise the brain owns the scheduler and its bus consumer admits the
published copy under the candidate's recorded decision. Each settlement is
counted (:meth:`AudioInputModule.counts`).

**The listen loop** (R7 lifecycle, supervision, restart). ``start_inputs``
spawns the loop as one task of the module's supervised registry (the owner
``prepare`` resolved); its first act is one detector invocation, and one
iteration runs at a time, so at most one detector, one acquisition and one
transcription are in flight. Between iterations it waits on the injected
sleeper: 1 s after a productive settlement (a declined candidate or a
published event), which also resets the unproductive run ``n`` to 0, and
``min(retry_seconds × 2^(n−1), 300)`` after the n-th unproductive one in a
row (no verdict, a detector, source or transcription failure, an empty
transcription). ``stop_inputs`` stops it for good: the loop is cancelled, an
invocation in flight is terminated and is not a failure, an acquisition in
flight is killed with its partial output discarded, and no invocation starts
afterwards; ``prepare`` starts nothing, so a startup that never reaches
``start_inputs`` invokes nothing. *Health* has two states and starts
``healthy``: a detector failure while ``healthy`` degrades it with exactly one
``module.degraded`` of the value-free reason ``wake_word_detector_failed``,
the first verdict (a detection or an exit 1) while ``degraded`` restores it
with exactly one ``module.ready``, and nothing else reports — source and
transcription outcomes never change the state. A detector held pending is
never timed out by the companion.

Lifecycle: the manifest declares the ``input`` role, so ``start_inputs`` and
``stop_inputs`` are phases of the handle — no-ops while listening is off — beside
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
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

import yaml

try:  # Keep the module importable for transport-injected contract tests.
    import aiohttp
except ModuleNotFoundError:  # pragma: no cover - production installs dependencies
    aiohttp = None  # type: ignore[assignment]

from core.actions import ERROR_CANCELLED, ERROR_INVALID_ARGUMENTS
from core.admission import Work
from core.attachments import AttachmentRefused
from core.contracts import (
    BRAIN_ERROR_ATTACHMENT_REFUSED,
    BRAIN_ERROR_INVALID_RESULT,
    PART_TYPE_AUDIO_REF,
    TRACE_INPUT_TRIGGER_ACCEPTED,
    TRACE_INPUT_TRIGGER_REJECTED,
    ActionObservation,
    ActionSpec,
    Destination,
    SessionKey,
)
from core.triggers import NORMALIZED_SCHEMA_VERSION, decision_trace_payload


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
_SETTING_LISTEN = "listen"
_SETTING_WAKE_WORD = "wake_word"
_SETTINGS = frozenset(
    {
        _SETTING_SOURCES,
        _SETTING_DEFAULT_SOURCE,
        _SETTING_MAX_SECONDS,
        _SETTING_MAX_BYTES,
        _SETTING_GRACE_SECONDS,
        _SETTING_TRANSCRIPTION,
        _SETTING_REQUIRED,
        _SETTING_LISTEN,
        _SETTING_WAKE_WORD,
    }
)
_TRANSCRIPTION_TEXT_FIELDS = ("endpoint", "model", "api_key", "language")
_TRANSCRIPTION_FIELDS = frozenset(
    {"enabled", "timeout_seconds", "max_chars", *_TRANSCRIPTION_TEXT_FIELDS}
)

#: The voice input (phase 8 R7): the ``listen`` fields and their defaults.
_LISTEN_ENABLED = "enabled"
_LISTEN_SOURCE = "source"
_LISTEN_PRE_ROLL_SECONDS = "pre_roll_seconds"
_LISTEN_SPEAKER = "speaker"
_LISTEN_DESTINATION = "destination"
_LISTEN_RETRY_SECONDS = "retry_seconds"
_LISTEN_MAX_SECONDS = "max_seconds"
_LISTEN_FIELDS = frozenset(
    {
        _LISTEN_ENABLED,
        _LISTEN_SOURCE,
        _LISTEN_PRE_ROLL_SECONDS,
        _LISTEN_SPEAKER,
        _LISTEN_DESTINATION,
        _LISTEN_RETRY_SECONDS,
        _LISTEN_MAX_SECONDS,
    }
)
DEFAULT_LISTEN_PRE_ROLL_SECONDS = 2.0
DEFAULT_LISTEN_SPEAKER = "streamer"
DEFAULT_LISTEN_RETRY_SECONDS = 5.0
DEFAULT_LISTEN_MAX_SECONDS = 10
LISTEN_PRE_ROLL_MAX_SECONDS = 5
LISTEN_RETRY_MIN_SECONDS = 1
LISTEN_RETRY_MAX_SECONDS = 300
_DESTINATION_FIELDS = ("platform", "channel_id")

#: The wake-word detector: ``{kind: command, argv}`` or ``{kind: http,
#: endpoint, api_key?}``, configured, never embedded and never assumed.
_WAKE_WORD_DETECTOR = "detector"
DETECTOR_KIND_COMMAND = "command"
DETECTOR_KIND_HTTP = "http"
_DETECTOR_FIELDS = {
    DETECTOR_KIND_COMMAND: frozenset({"kind", "argv"}),
    DETECTOR_KIND_HTTP: frozenset({"kind", "endpoint", "api_key"}),
}

WAKE_WORD_TYPE = "wake_word"
"""The hooked trigger type this module declares and supplies the hook of (R3, R7)."""

_WAKE_WORD_PAYLOAD = "wake_word"
_WAKE_WORD_DETECTED = "detected"
_WAKE_WORD_PHRASE = "phrase"

#: The voice input's events: kind, bus type, reserved author and work kind.
SPEECH_KIND = "speech"
SPEECH_EVENT_TYPE = "channel.chat.message"
VOICE_AUTHOR_PREFIX = "voice:"
WORK_KIND = "audio_input.speech"
#: The service key the brain publishes its owned scheduler under.
ADMISSION_SERVICE_KIND = "admission"
ADMISSION_SERVICE_SCOPE = "runs"

#: The environment an acquisition's source is started with (R7 step 3).
ENV_PRE_ROLL_SECONDS = "AUDIO_INPUT_PRE_ROLL_SECONDS"
ENV_MAX_SECONDS = "AUDIO_INPUT_MAX_SECONDS"

PHRASE_MAX_CHARS = 64
"""The longest phrase a detector may report (R7)."""

#: How much of a command detector's stdout, and of an HTTP detector's
#: answer, is held: a phrase is at most 64 characters, so anything past
#: this is not a verdict.
_DETECTOR_OUTPUT_MAX_BYTES = 4096
_DETECTOR_READ_CHUNK = 4096

#: The fixed codes a detector failure carries; never an exception's text.
DETECTOR_UNSTARTABLE = "detector_unstartable"
DETECTOR_EXIT_STATUS = "detector_exit_status"
DETECTOR_INVALID_PHRASE = "detector_invalid_phrase"
DETECTOR_OUTPUT_TOO_LONG = "detector_output_too_long"
DETECTOR_UNREACHABLE = "detector_unreachable"
DETECTOR_HTTP_STATUS = "detector_http_status"
DETECTOR_INVALID_ANSWER = "detector_invalid_answer"

#: How one iteration of listening settled (:meth:`AudioInputModule.listen_once`).
SETTLED_STOPPED = "stopped"
SETTLED_NO_VERDICT = "no_verdict"
SETTLED_DETECTOR_FAILURE = "detector_failure"
SETTLED_DECLINED = "declined"
SETTLED_SOURCE_FAILURE = "source_failure"
SETTLED_TRANSCRIPTION_ERROR = "transcription_error"
SETTLED_EMPTY_TRANSCRIPTION = "empty_transcription"
SETTLED_PUBLISHED = "published"

#: The counts :meth:`AudioInputModule.counts` reports for listening.
COUNT_DETECTOR_FAILURES = "detector_failures"
COUNT_SOURCE_FAILURES = "source_failures"
COUNT_TRANSCRIPTION_ERRORS = "transcription_errors"
COUNT_EMPTY_TRANSCRIPTIONS = "empty_transcriptions"
COUNT_CANDIDATES = "candidates"
COUNT_PUBLISHED = "published"
_LISTEN_COUNTS = (
    COUNT_DETECTOR_FAILURES,
    COUNT_SOURCE_FAILURES,
    COUNT_TRANSCRIPTION_ERRORS,
    COUNT_EMPTY_TRANSCRIPTIONS,
    COUNT_CANDIDATES,
    COUNT_PUBLISHED,
)

#: The restart scheduler (R7): after a productive settlement — a declined
#: candidate or a published event — the next invocation waits 1 s and the
#: unproductive run restarts at 0; after the n-th unproductive one in a row it
#: waits ``min(retry_seconds × 2^(n−1), 300)``.
LISTEN_PRODUCTIVE_DELAY_SECONDS = 1.0
LISTEN_BACKOFF_MAX_SECONDS = 300.0
_PRODUCTIVE_SETTLEMENTS = frozenset({SETTLED_DECLINED, SETTLED_PUBLISHED})
# 2^16 × the 1 s minimum already passes the cap: the exponent stops growing there.
_BACKOFF_MAX_EXPONENT = 16

#: The voice input's two health states and the one value-free reason (R7).
LISTEN_HEALTHY = "healthy"
LISTEN_DEGRADED = "degraded"
WAKE_WORD_DETECTOR_FAILED = "wake_word_detector_failed"

#: The injection seams: read from *settings*, never from a configuration file.
_SEAM_SOURCE_FACTORY = "_source_factory"
_SEAM_SUBPROCESS_RUNNER = "_subprocess_runner"
_SEAM_TRANSCRIPTION_TRANSPORT = "_transcription_transport"
_SEAM_SLEEPER = "_sleeper"
_SEAM_DETECTOR_SESSION = "_detector_session"
_SEAMS = frozenset(
    {
        _SEAM_SOURCE_FACTORY,
        _SEAM_SUBPROCESS_RUNNER,
        _SEAM_TRANSCRIPTION_TRANSPORT,
        _SEAM_SLEEPER,
        _SEAM_DETECTOR_SESSION,
    }
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
    if _listening(settings):
        diagnostics.extend(_listen_diagnostics(settings))
    return diagnostics


def _listening(settings: Mapping[str, Any]) -> bool:
    """Whether *settings* enable the voice input (phase 8 R7)."""

    listen = settings.get(_SETTING_LISTEN)
    return isinstance(listen, Mapping) and listen.get(_LISTEN_ENABLED) is True


def _listen_diagnostics(settings: Mapping[str, Any]) -> list[str]:
    """The diagnostics of an enabled ``listen`` and its detector, value-free (R7).

    Only called with listening enabled: with ``listen`` absent or disabled
    nothing here is checked and the module is the phase-7 one.
    """

    listen = settings[_SETTING_LISTEN]
    field = _SETTING_LISTEN
    diagnostics: list[str] = []
    for key in listen:
        if key not in _LISTEN_FIELDS:
            diagnostics.append(
                _setting_diagnostic(f"{field}.{key}", "is not a field of the voice input")
            )

    source = listen.get(_LISTEN_SOURCE)
    sources = settings.get(_SETTING_SOURCES)
    entry = sources.get(source) if isinstance(sources, Mapping) and _is_text(source) else None
    if not isinstance(entry, Mapping) or entry.get("kind") != SOURCE_KIND_COMMAND:
        diagnostics.append(
            _setting_diagnostic(
                f"{field}.{_LISTEN_SOURCE}", "must name a configured 'command' source"
            )
        )

    module_max = settings.get(_SETTING_MAX_SECONDS, DEFAULT_MAX_SECONDS)
    max_seconds = listen.get(_LISTEN_MAX_SECONDS, DEFAULT_LISTEN_MAX_SECONDS)
    max_valid = _is_positive_int(max_seconds) and (
        not _is_positive_int(module_max) or max_seconds <= module_max
    )
    if not max_valid:
        diagnostics.append(
            _setting_diagnostic(
                f"{field}.{_LISTEN_MAX_SECONDS}",
                f"must be an integer from 1 to the module's {_SETTING_MAX_SECONDS!r}",
            )
        )
    pre_roll = listen.get(_LISTEN_PRE_ROLL_SECONDS, DEFAULT_LISTEN_PRE_ROLL_SECONDS)
    if not _is_non_negative_number(pre_roll) or pre_roll > LISTEN_PRE_ROLL_MAX_SECONDS:
        diagnostics.append(
            _setting_diagnostic(
                f"{field}.{_LISTEN_PRE_ROLL_SECONDS}",
                f"must be a number from 0 to {LISTEN_PRE_ROLL_MAX_SECONDS}",
            )
        )
    elif max_valid and not pre_roll < max_seconds:
        diagnostics.append(
            _setting_diagnostic(
                f"{field}.{_LISTEN_PRE_ROLL_SECONDS}",
                f"must be less than {field}.{_LISTEN_MAX_SECONDS}",
            )
        )
    if _LISTEN_SPEAKER in listen and not _is_text(listen[_LISTEN_SPEAKER]):
        diagnostics.append(
            _setting_diagnostic(f"{field}.{_LISTEN_SPEAKER}", "must be a non-empty string")
        )
    retry = listen.get(_LISTEN_RETRY_SECONDS, DEFAULT_LISTEN_RETRY_SECONDS)
    if not _is_finite_number(retry) or not (
        LISTEN_RETRY_MIN_SECONDS <= retry <= LISTEN_RETRY_MAX_SECONDS
    ):
        diagnostics.append(
            _setting_diagnostic(
                f"{field}.{_LISTEN_RETRY_SECONDS}",
                f"must be a number from {LISTEN_RETRY_MIN_SECONDS} "
                f"to {LISTEN_RETRY_MAX_SECONDS}",
            )
        )
    diagnostics.extend(_destination_diagnostics(listen.get(_LISTEN_DESTINATION)))
    diagnostics.extend(_detector_diagnostics(settings.get(_SETTING_WAKE_WORD)))
    return diagnostics


def _destination_diagnostics(destination: Any) -> list[str]:
    field = f"{_SETTING_LISTEN}.{_LISTEN_DESTINATION}"
    if not isinstance(destination, Mapping):
        return [_setting_diagnostic(field, "must be a mapping of platform and channel_id")]
    diagnostics = [
        _setting_diagnostic(f"{field}.{key}", "must be a non-empty string")
        for key in _DESTINATION_FIELDS
        if not _is_text(destination.get(key))
    ]
    for key in destination:
        if key not in _DESTINATION_FIELDS:
            diagnostics.append(
                _setting_diagnostic(f"{field}.{key}", "is not a field of the destination")
            )
    return diagnostics


def _detector_diagnostics(wake_word: Any) -> list[str]:
    """The diagnostics of ``wake_word.detector``, required while listening."""

    field = f"{_SETTING_WAKE_WORD}.{_WAKE_WORD_DETECTOR}"
    if wake_word is not None and not isinstance(wake_word, Mapping):
        return [_setting_diagnostic(_SETTING_WAKE_WORD, "must be a mapping")]
    diagnostics: list[str] = []
    for key in wake_word or {}:
        if key != _WAKE_WORD_DETECTOR:
            diagnostics.append(
                _setting_diagnostic(
                    f"{_SETTING_WAKE_WORD}.{key}", "is not a field of the wake word"
                )
            )
    detector = (wake_word or {}).get(_WAKE_WORD_DETECTOR)
    if detector is None:
        diagnostics.append(_setting_diagnostic(field, "is required while listening is enabled"))
        return diagnostics
    if not isinstance(detector, Mapping):
        diagnostics.append(_setting_diagnostic(field, "must be a mapping"))
        return diagnostics
    kind = detector.get("kind")
    if not isinstance(kind, str) or kind not in _DETECTOR_FIELDS:
        diagnostics.append(_setting_diagnostic(f"{field}.kind", "must be 'command' or 'http'"))
        return diagnostics
    if kind == DETECTOR_KIND_COMMAND:
        argv = detector.get("argv")
        if (
            not isinstance(argv, (list, tuple))
            or not argv
            or not all(_is_text(item) for item in argv)
        ):
            diagnostics.append(
                _setting_diagnostic(f"{field}.argv", "must be a non-empty list of strings")
            )
    else:
        if not _is_text(detector.get("endpoint")):
            diagnostics.append(
                _setting_diagnostic(f"{field}.endpoint", "must be a non-empty string")
            )
        if "api_key" in detector and not isinstance(detector["api_key"], str):
            diagnostics.append(_setting_diagnostic(f"{field}.api_key", "must be a string"))
    for key in detector:
        if key not in _DETECTOR_FIELDS[kind]:
            diagnostics.append(
                _setting_diagnostic(f"{field}.{key}", "is not a field of this detector kind")
            )
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
class DetectorSpec:
    """The configured wake-word detector; never rendered into a diagnostic."""

    kind: str
    argv: tuple[str, ...] = ()
    endpoint: str = ""
    api_key: str = ""

    @classmethod
    def from_mapping(cls, entry: Mapping[str, Any]) -> "DetectorSpec":
        if entry["kind"] == DETECTOR_KIND_COMMAND:
            return cls(
                kind=DETECTOR_KIND_COMMAND, argv=tuple(str(item) for item in entry["argv"])
            )
        return cls(
            kind=DETECTOR_KIND_HTTP,
            endpoint=str(entry["endpoint"]),
            api_key=str(entry.get("api_key", "")),
        )


@dataclass(frozen=True, slots=True)
class ListenSettings:
    """The enabled voice input, parsed (phase 8 R7)."""

    source: str
    pre_roll_seconds: float
    speaker: str
    #: ``{platform, channel_id}``: the channel the event's run answers on.
    destination: Mapping[str, str]
    retry_seconds: float
    max_seconds: int
    detector: DetectorSpec

    @classmethod
    def from_mapping(cls, settings: Mapping[str, Any]) -> "ListenSettings":
        listen = settings[_SETTING_LISTEN]
        destination = listen[_LISTEN_DESTINATION]
        return cls(
            source=str(listen[_LISTEN_SOURCE]),
            pre_roll_seconds=float(
                listen.get(_LISTEN_PRE_ROLL_SECONDS, DEFAULT_LISTEN_PRE_ROLL_SECONDS)
            ),
            speaker=str(listen.get(_LISTEN_SPEAKER, DEFAULT_LISTEN_SPEAKER)),
            destination=MappingProxyType(
                {key: str(destination[key]) for key in _DESTINATION_FIELDS}
            ),
            retry_seconds=float(listen.get(_LISTEN_RETRY_SECONDS, DEFAULT_LISTEN_RETRY_SECONDS)),
            max_seconds=int(listen.get(_LISTEN_MAX_SECONDS, DEFAULT_LISTEN_MAX_SECONDS)),
            detector=DetectorSpec.from_mapping(
                settings[_SETTING_WAKE_WORD][_WAKE_WORD_DETECTOR]
            ),
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
    #: ``None`` unless ``listen.enabled`` is true: the phase-7 module.
    listen: ListenSettings | None = None

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
            listen=ListenSettings.from_mapping(settings) if _listening(settings) else None,
        )


# --------------------------------------------------------------------------- #
# The wake-word hook (phase 8 R3, R7)
# --------------------------------------------------------------------------- #


async def _wake_word_hook(view: Mapping[str, Any], parameters: Mapping[str, Any]) -> bool:
    """Decide a candidate by the verdict it carries, and nothing else.

    ``True`` iff ``payload.wake_word.detected`` is ``True``, ``False`` iff it
    is ``False``; any other shape raises, which the engine records as
    ``wake_word_hook_error`` — never a silent pass. The rule's parameters
    are empty and not read, nor is any other field of the event.
    """

    del parameters
    detected = view["payload"][_WAKE_WORD_PAYLOAD][_WAKE_WORD_DETECTED]
    if detected is True:
        return True
    if detected is False:
        return False
    raise ValueError("payload.wake_word.detected is not a boolean")


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

    def begin(self, environment: Mapping[str, str] | None = None) -> "CommandRecording":
        """One recording; *environment* is added to the recorder's own (R7).

        A recording begun with an environment is the voice input's
        acquisition: it is read until the source exits on its own.
        """

        return CommandRecording(
            self._argv,
            runner=self._runner,
            sleeper=self._sleeper,
            limit=self._limit,
            environment=environment,
            until_exit=environment is not None,
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

    With an *environment* (the voice input's acquisition, R7) the recorder
    is started with it added to the process's own environment; without one
    the recorder inherits it unchanged, as every ``audio.capture`` does.
    :attr:`failed_exit` tells whether the recorder ended on its own with a
    status other than 0 — never the case for one this reader stopped.

    With *until_exit* (the acquisition) the source ends the segment itself:
    nothing stops it at ``seconds`` or at a data size — the caller's own
    deadline kills it — and :attr:`failed_exit` holds unless it exited with
    status 0 by itself; a source that reached the byte bound is terminated
    and failed too. R7 counts every outcome but a clean exit as a failure.
    """

    __slots__ = (
        "_argv",
        "_environment",
        "_killed",
        "_limit",
        "_process",
        "_runner",
        "_sleeper",
        "_until_exit",
        "failed_exit",
    )

    def __init__(
        self,
        argv: Sequence[str],
        *,
        runner: SubprocessRunner,
        sleeper: Sleeper,
        limit: int,
        environment: Mapping[str, str] | None = None,
        until_exit: bool = False,
    ) -> None:
        self._argv = tuple(argv)
        self._runner = runner
        self._sleeper = sleeper
        self._limit = int(limit)
        self._environment = None if environment is None else dict(environment)
        self._process: Any | None = None
        self._killed = False
        self._until_exit = bool(until_exit)
        self.failed_exit = False

    async def read(self, seconds: int) -> bytes:
        if self._killed:
            return b""
        options: dict[str, Any] = {}
        if self._environment is not None:
            options["env"] = {**os.environ, **self._environment}
        try:
            process = await self._runner(
                *self._argv,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                start_new_session=True,
                **options,
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
        if self._until_exit:
            return await self._read_until_exit(process)

        needed = int(seconds) * BYTES_PER_SECOND
        buffer = bytearray()
        timer = asyncio.ensure_future(self._sleeper(float(seconds)))
        reading: asyncio.Future[Any] | None = None
        terminated = False
        stopped = False
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
                    stopped = True
                    break
            if not terminated:
                self._terminate()
            # Reading has stopped: unread output must not keep the pipe (and
            # so the process's reaping) pending until the call deadline.
            close_stdout = getattr(process, "close_stdout", None)
            if callable(close_stdout):
                close_stdout()
            status = await process.wait()
            # Only an exit status the recorder chose: a signal may be the
            # terminate this reader sent once its stdout closed.
            self.failed_exit = (
                not (terminated or stopped)
                and isinstance(status, int)
                and not isinstance(status, bool)
                and status > 0
            )
        except BaseException:
            if reading is not None and not reading.done():
                reading.cancel()
            self._kill_process()
            raise
        finally:
            if not timer.done():
                timer.cancel()
        return bytes(buffer[: self._limit])

    async def _read_until_exit(self, process: Any) -> bytes:
        """Read until the source closes its stdout and exits; fail unless it exits 0."""

        buffer = bytearray()
        reading: asyncio.Future[Any] | None = None
        overflowed = False
        try:
            while True:
                reading = asyncio.ensure_future(process.stdout.read(_READ_CHUNK))
                chunk = await reading
                reading = None
                if not chunk:
                    break
                buffer += chunk
                if len(buffer) >= self._limit:
                    # Past the byte bound: the segment is refused whatever
                    # follows, so the source is stopped and failed.
                    overflowed = True
                    self._terminate()
                    break
            close_stdout = getattr(process, "close_stdout", None)
            if callable(close_stdout):
                close_stdout()
            status = await process.wait()
            self.failed_exit = overflowed or not (
                isinstance(status, int) and not isinstance(status, bool) and status == 0
            )
        except BaseException:
            if reading is not None and not reading.done():
                reading.cancel()
            self._kill_process()
            raise
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
# The voice input: detectors, candidate, segment (phase 8 R7)
# --------------------------------------------------------------------------- #


class DetectorFailure(Exception):
    """A detector invocation that settled without a verdict (R7).

    ``code`` is one of the fixed ``DETECTOR_*`` codes; no exception text,
    argv, endpoint or output is ever carried.
    """

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class Verdict:
    """A detection: the phrase the detector matched, and nothing else."""

    phrase: str


def _phrase_or_failure(phrase: Any) -> str:
    """The trimmed phrase of 1 to 64 characters, else a detector failure."""

    if not isinstance(phrase, str):
        raise DetectorFailure(DETECTOR_INVALID_PHRASE)
    trimmed = phrase.strip()
    if not trimmed or len(trimmed) > PHRASE_MAX_CHARS:
        raise DetectorFailure(DETECTOR_INVALID_PHRASE)
    return trimmed


class CommandDetector:
    """A ``command`` detector: ``argv`` started per invocation, given no audio.

    Its standard input is empty and the companion sets no deadline on it: the
    wait is the detector's own. Exit 0 with a trimmed standard output of 1 to
    64 characters is a detection, exit 1 no verdict; any other status (a
    signal included), a longer output or a start failure is a
    :class:`DetectorFailure`. :meth:`terminate` stops the invocation in
    flight, for the loop's stop.
    """

    __slots__ = ("_argv", "_process", "_runner")

    def __init__(self, argv: Sequence[str], *, runner: SubprocessRunner) -> None:
        self._argv = tuple(argv)
        self._runner = runner
        self._process: Any | None = None

    async def wait(self) -> Verdict | None:
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
            raise DetectorFailure(DETECTOR_UNSTARTABLE) from None
        self._process = process
        output = bytearray()
        try:
            while True:
                chunk = await process.stdout.read(_DETECTOR_READ_CHUNK)
                if not chunk:
                    break
                output += chunk
                if len(output) > _DETECTOR_OUTPUT_MAX_BYTES:
                    _kill_quietly(process)
                    await process.wait()
                    raise DetectorFailure(DETECTOR_OUTPUT_TOO_LONG)
            status = await process.wait()
        except (asyncio.CancelledError, DetectorFailure):
            _kill_quietly(process)
            raise
        except Exception:
            _kill_quietly(process)
            raise DetectorFailure(DETECTOR_EXIT_STATUS) from None
        finally:
            self._process = None
        if status == 1:
            return None
        if status != 0:
            raise DetectorFailure(DETECTOR_EXIT_STATUS)
        try:
            text = bytes(output).decode("utf-8")
        except UnicodeDecodeError:
            raise DetectorFailure(DETECTOR_INVALID_PHRASE) from None
        return Verdict(_phrase_or_failure(text))

    def terminate(self) -> None:
        """Kill the invocation in flight, if any."""

        if self._process is not None:
            _kill_quietly(self._process)


def _kill_quietly(process: Any) -> None:
    try:
        process.kill()
    except (ProcessLookupError, OSError):
        pass


class HttpDetector:
    """An ``http`` detector: one request per invocation, carrying no audio.

    The request is a ``POST`` with no body, a bearer header when ``api_key``
    is set, and no request timeout: the session the ``_detector_session``
    seam makes (by default an ``aiohttp`` session whose timeout is
    disabled) is made on first use and released by :meth:`close`. Only a
    200 answer whose JSON body is exactly ``{"detected": true, "phrase":
    <1 to 64 characters>}`` or ``{"detected": false}`` is a verdict;
    anything else — another status, a body that is not such JSON, a refused
    or lost connection — is a :class:`DetectorFailure`.
    """

    __slots__ = ("_api_key", "_endpoint", "_factory", "_session")

    def __init__(self, endpoint: str, api_key: str, *, session_factory: Callable[[], Any]) -> None:
        self._endpoint = endpoint
        self._api_key = api_key
        self._factory = session_factory
        self._session: Any | None = None

    async def wait(self) -> Verdict | None:
        session = await self._session_or_failure()
        headers: dict[str, str] = {}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        try:
            response = await session.post(self._endpoint, headers=headers)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - every transport failure is the same code
            raise DetectorFailure(DETECTOR_UNREACHABLE) from None
        try:
            status = getattr(response, "status", None)
            if status != 200 or isinstance(status, bool):
                raise DetectorFailure(DETECTOR_HTTP_STATUS)
            try:
                body = await _read_bounded(response, _DETECTOR_OUTPUT_MAX_BYTES)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - a body lost mid-answer
                raise DetectorFailure(DETECTOR_UNREACHABLE) from None
            try:
                decoded = json.loads(body.decode("utf-8")) if body is not None else None
            except Exception:  # noqa: BLE001 - not JSON is not a verdict
                raise DetectorFailure(DETECTOR_INVALID_ANSWER) from None
        finally:
            await _release(response)
        return _http_verdict(decoded)

    async def _session_or_failure(self) -> Any:
        if self._session is None:
            try:
                created = self._factory()
                if inspect.isawaitable(created):
                    created = await created
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - reported as the endpoint being unreachable
                raise DetectorFailure(DETECTOR_UNREACHABLE) from None
            self._session = created
        return self._session

    async def close(self) -> None:
        session, self._session = self._session, None
        await _close_quietly(session)


def _http_verdict(decoded: Any) -> Verdict | None:
    """The verdict of one of the two shapes, else a detector failure."""

    if isinstance(decoded, Mapping):
        detected = decoded.get(_WAKE_WORD_DETECTED)
        if detected is False and set(decoded) == {_WAKE_WORD_DETECTED}:
            return None
        if detected is True and set(decoded) == {_WAKE_WORD_DETECTED, _WAKE_WORD_PHRASE}:
            phrase = decoded[_WAKE_WORD_PHRASE]
            if isinstance(phrase, str) and len(phrase) <= PHRASE_MAX_CHARS:
                return Verdict(_phrase_or_failure(phrase))
            raise DetectorFailure(DETECTOR_INVALID_PHRASE)
    raise DetectorFailure(DETECTOR_INVALID_ANSWER)


def _default_detector_session_factory() -> Any:
    """An ``aiohttp`` session with no request timeout: the detector's wait is its own."""

    if aiohttp is None:
        raise RuntimeError("aiohttp is not installed")
    return aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None))


DETECTOR_SESSION_FACTORY: Callable[[], Any] = _default_detector_session_factory
"""The shipped ``http`` detector transport, made on first use."""


def listen_backoff_seconds(retry_seconds: float, unproductive: int) -> float:
    """The wait before the next detector invocation (R7's restart scheduler).

    *unproductive* is how many unproductive settlements ran in a row, the
    last one included; 0 means the last settlement was productive (1 s).
    """

    if unproductive <= 0:
        return LISTEN_PRODUCTIVE_DELAY_SECONDS
    exponent = min(unproductive - 1, _BACKOFF_MAX_EXPONENT)
    return min(float(retry_seconds) * 2.0**exponent, LISTEN_BACKOFF_MAX_SECONDS)


def build_detector(
    spec: DetectorSpec, *, runner: SubprocessRunner, session_factory: Callable[[], Any]
) -> CommandDetector | HttpDetector:
    """The detector *spec* configures."""

    if spec.kind == DETECTOR_KIND_COMMAND:
        return CommandDetector(spec.argv, runner=runner)
    return HttpDetector(spec.endpoint, spec.api_key, session_factory=session_factory)


def speech_candidate(listen: ListenSettings, phrase: str) -> dict[str, Any]:
    """The schema-2 ``speech`` candidate of a detection (R7 step 2).

    It carries the detector's verdict — ``payload.wake_word`` and the phrase
    as ``text``, never a transcription — the reserved author
    ``voice:<speaker>``, a fresh ``message_id`` and the configured
    destination. It is decided, never published.
    """

    author = f"{VOICE_AUTHOR_PREFIX}{listen.speaker}"
    return {
        "type": SPEECH_EVENT_TYPE,
        "payload": {
            "platform": listen.destination["platform"],
            "channel_id": listen.destination["channel_id"],
            "author": {"id": author, "display_name": listen.speaker},
            "message_id": f"{author}:{uuid.uuid4().hex}",
            "text": phrase,
            "kind": SPEECH_KIND,
            _WAKE_WORD_PAYLOAD: {_WAKE_WORD_DETECTED: True, _WAKE_WORD_PHRASE: phrase},
        },
        "metadata": {"source": MODULE_NAME, "schema_version": NORMALIZED_SCHEMA_VERSION},
    }


def speech_event(candidate: Mapping[str, Any], transcription: str) -> dict[str, Any]:
    """The published ``speech`` event of an accepted candidate (R7 step 4).

    The candidate's author, ``message_id``, destination and source; the
    transcription as ``text`` and no ``wake_word``.
    """

    payload = {
        key: value for key, value in candidate["payload"].items() if key != _WAKE_WORD_PAYLOAD
    }
    payload["author"] = dict(payload["author"])
    payload["text"] = transcription
    return {
        "type": candidate["type"],
        "payload": payload,
        "metadata": dict(candidate["metadata"]),
    }


def listen_segment_data_bytes(data: bytes) -> int:
    """The ``data`` size an acquired segment's header declares, checked.

    The segment must be of the convention (:func:`cut_segment`'s checks) and
    its header honest: a streaming placeholder or a size larger than the
    bytes held is refused, so the duration read from the header is the
    audio actually transcribed. Raises :class:`InvalidSegment`; nothing is
    trimmed or padded.
    """

    layout = wav_data_layout(data)
    if layout is None:
        raise InvalidSegment("not a RIFF/WAVE body with a data chunk")
    offset, declared = layout
    if declared in _STREAMING_DATA_SIZES or declared > len(data) - offset:
        raise InvalidSegment("the header does not state the data held")
    # The format checks only: the cut copy is discarded.
    cut_segment(data, max(1, -(-declared // BYTES_PER_SECOND)))
    return declared


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
        runner: SubprocessRunner = spawn_recorder,
        detector_session_factory: Callable[[], Any] = DETECTOR_SESSION_FACTORY,
    ) -> None:
        self._actions = context.actions
        self._bus = getattr(context, "bus", None)
        self._triggers = getattr(context, "triggers", None)
        self._scheduler = getattr(context, "scheduler", None)
        self._services = getattr(context, "services", None)
        self._attachments = context.attachments
        self._clock: Callable[[], float] = context.clock
        self._supervision = getattr(context, "supervision", None)
        self._tasks = getattr(context, "tasks", None)
        self._supply_trigger_hook = getattr(context, "supply_trigger_hook", None)
        self._settings = settings
        self._sources = dict(sources)
        # The voice input's source and its loop's task owner, resolved at
        # ``prepare`` when listening is enabled (R7); nothing is started there.
        self._listen_source: Any | None = None
        self._listen_owner: Any | None = None
        # The wake-word detector, built at ``prepare`` from its settings.
        self._runner = runner
        self._detector_session_factory = detector_session_factory
        self._detector: CommandDetector | HttpDetector | None = None
        self._listen_counts = {name: 0 for name in _LISTEN_COUNTS}
        # The listen loop (R7): one supervised task from ``start_inputs`` to
        # ``stop_inputs``, the acquisition it holds, and its two-state health.
        self._listen_task: Any | None = None
        self._listen_stopping = False
        self._listen_recording: Any | None = None
        self._listen_health = LISTEN_HEALTHY
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

    @property
    def listening(self) -> bool:
        """Whether the voice input is enabled (phase 8 R7)."""

        return self._settings.listen is not None

    @property
    def listen_source(self) -> Any | None:
        """The source a wake word acquires from, resolved at ``prepare``."""

        return self._listen_source

    @property
    def listen_owner(self) -> Any | None:
        """The supervised-task owner the listen loop runs under, from ``prepare``."""

        return self._listen_owner

    @property
    def detector(self) -> CommandDetector | HttpDetector | None:
        """The wake-word detector, built at ``prepare`` when listening."""

        return self._detector

    def counts(self) -> Mapping[str, int]:
        """How the voice input's iterations settled (R7)."""

        return dict(self._listen_counts)

    @property
    def listen_health(self) -> str:
        """The voice input's health: ``healthy`` or ``degraded`` (R7)."""

        return self._listen_health

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

        With listening enabled (phase 8 R7) it also resolves ``listen.source``,
        registers the module's own supervised-task registry as the listen
        loop's owner and supplies :func:`_wake_word_hook` for the declared
        ``wake_word`` type; it starts no process, reads no audio and emits no
        input. It also sends no transcription probe: the listening path
        transcribes nothing before an accepted decision, so the probe — the
        capture action's readiness check — runs only when the module is not
        listening, and a listening module's transcription is judged by each
        call it makes. With listening off none of this happens.
        """

        if self._prepared or self._closed:
            return
        settings = self._settings
        if settings.listen is not None:
            self._prepare_listening(settings.listen)
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
        if settings.transcription.enabled and usable and settings.listen is None:
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

    def _prepare_listening(self, listen: ListenSettings) -> None:
        """Resolve the voice input's source and owner, supply the hook (R7)."""

        if not callable(getattr(self._tasks, "spawn", None)):
            raise AudioInputModuleError(
                f"{MODULE_NAME} prepare: field '{_SETTING_LISTEN}': "
                "no supervised task registry owns the listen loop"
            )
        if not callable(self._supply_trigger_hook):
            raise AudioInputModuleError(
                f"{MODULE_NAME} prepare: field '{_SETTING_LISTEN}': "
                "no trigger engine decides the wake word"
            )
        try:
            self._supply_trigger_hook(WAKE_WORD_TYPE, _wake_word_hook)
        except Exception:
            raise AudioInputModuleError(
                f"{MODULE_NAME} prepare: field '{_SETTING_LISTEN}': "
                f"the {WAKE_WORD_TYPE!r} hook could not be supplied"
            ) from None
        self._listen_source = self._sources[listen.source]
        self._listen_owner = self._tasks
        self._detector = build_detector(
            listen.detector,
            runner=self._runner,
            session_factory=self._detector_session_factory,
        )

    async def start_inputs(self) -> None:
        """The input phase (R7): start the listen loop, whose first act is the detector.

        The loop runs as one task of the module's supervised registry,
        resolved at ``prepare``; a second call, a call after ``stop_inputs``
        or ``close``, or one with listening off (or never prepared) starts
        nothing.
        """

        owner = self._listen_owner
        if (
            owner is None
            or self._detector is None
            or self._listen_task is not None
            or self._listen_stopping
            or self._closed
        ):
            return
        self._listen_task = owner.spawn(self._listen_loop(), name=f"{MODULE_NAME}-listen")

    async def stop_inputs(self) -> None:
        """End the input phase: no detector invocation starts after this.

        The loop is cancelled; an invocation in flight is terminated (a
        ``command`` detector's process killed, an ``http`` detector's
        request and session closed) and is not a detector failure; an
        acquisition in flight is killed, its partial output discarded, and a
        segment not yet transcribed is released with the loop's frame. The
        state is resolved in ``finally``, so a stop cut short by the
        coordinator still leaves no process running.
        """

        await self._stop_listening()

    async def _stop_listening(self) -> None:
        self._listen_stopping = True
        task, self._listen_task = self._listen_task, None
        try:
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        finally:
            _kill_recording(self._listen_recording)
            self._listen_recording = None
            detector = self._detector
            terminate = getattr(detector, "terminate", None)
            if callable(terminate):
                terminate()
            close_detector = getattr(detector, "close", None)
            if callable(close_detector):
                await close_detector()

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
        try:
            await self._stop_listening()
        finally:
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
        await _close_quietly(session)

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

    async def _report_ready(self) -> None:
        ready = getattr(self._supervision, "ready", None)
        if not callable(ready):
            return
        try:
            outcome = ready()
            if inspect.isawaitable(outcome):
                await outcome
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a lost health report changes nothing listened
            return

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

    # -- the voice input: one iteration (R7) --------------------------------- #

    async def listen_once(self) -> str:
        """Run one iteration of listening in R7's order; return how it settled.

        (1) the detector is awaited once; (2) a detection's candidate is
        decided by the trigger engine and traced, never published; (3) only an
        accepted candidate acquires one segment from ``listen.source`` and
        transcribes it once; (4) a non-empty transcription is published as
        the ``speech`` event and admitted, never submitted to the engine.
        Returns one of the ``SETTLED_*`` values; with listening off, before
        ``prepare`` or once closed nothing is invoked (``stopped``).
        """

        listen = self._settings.listen
        detector = self._detector
        if listen is None or detector is None or self._closed or self._listen_stopping:
            return SETTLED_STOPPED
        counts = self._listen_counts
        # (1) Verdict. A detector ended by ``stop_inputs`` is not a failure.
        try:
            verdict = await detector.wait()
        except DetectorFailure:
            if self._listen_stopping or self._closed:
                return SETTLED_STOPPED
            counts[COUNT_DETECTOR_FAILURES] += 1
            await self._listen_health_after(failed=True)
            return SETTLED_DETECTOR_FAILURE
        if self._listen_stopping or self._closed:
            return SETTLED_STOPPED
        await self._listen_health_after(failed=False)
        if verdict is None:
            return SETTLED_NO_VERDICT
        # (2) Candidate: decided on the verdict alone.
        candidate = speech_candidate(listen, verdict.phrase)
        counts[COUNT_CANDIDATES] += 1
        decision = await self._decide(candidate)
        if decision is None:
            return SETTLED_DECLINED
        # (3) Acquisition, then one transcription; the segment is a local
        # released once transcribed, never stored or published.
        wav = await self._acquire(listen)
        if wav is None:
            counts[COUNT_SOURCE_FAILURES] += 1
            return SETTLED_SOURCE_FAILURE
        text = await self._transcribe_segment(wav)
        del wav
        if text is None:
            counts[COUNT_TRANSCRIPTION_ERRORS] += 1
            return SETTLED_TRANSCRIPTION_ERROR
        if not text.strip():
            counts[COUNT_EMPTY_TRANSCRIPTIONS] += 1
            return SETTLED_EMPTY_TRANSCRIPTION
        # (4) Event: published and admitted under the candidate's decision.
        await self._publish_speech(speech_event(candidate, text))
        return SETTLED_PUBLISHED

    async def _listen_loop(self) -> None:
        """Iterate until stopped, waiting on the sleeper between iterations (R7).

        After a productive settlement (a declined candidate, a published
        event) the wait is 1 s and the unproductive run is reset; after
        each unproductive one (no verdict, a detector, source or
        transcription failure, an empty transcription) it grows as
        :func:`listen_backoff_seconds`. One iteration at a time, so at most
        one detector, one acquisition and one transcription are in flight.
        """

        listen = self._settings.listen
        if listen is None:
            return
        unproductive = 0
        while not self._listen_stopping and not self._closed:
            settled = await self.listen_once()
            if settled == SETTLED_STOPPED or self._listen_stopping:
                return
            if settled in _PRODUCTIVE_SETTLEMENTS:
                unproductive = 0
            else:
                unproductive += 1
            await self._sleeper(listen_backoff_seconds(listen.retry_seconds, unproductive))

    async def _listen_health_after(self, *, failed: bool) -> None:
        """The two-state health after a settled detector invocation (R7).

        A failure while ``healthy`` degrades it with one value-free
        ``module.degraded``; a verdict while ``degraded`` restores it with one
        ``module.ready``. Anything else reports nothing; source and
        transcription outcomes never reach here.
        """

        if failed and self._listen_health == LISTEN_HEALTHY:
            self._listen_health = LISTEN_DEGRADED
            await self._report_degraded(WAKE_WORD_DETECTOR_FAILED, [])
        elif not failed and self._listen_health == LISTEN_DEGRADED:
            self._listen_health = LISTEN_HEALTHY
            await self._report_ready()

    async def _decide(self, candidate: Mapping[str, Any]) -> Any | None:
        """The engine's decision when it accepts *candidate*, else ``None``; traced, fail-closed."""

        engine = self._triggers
        if engine is None:
            return None
        try:
            decision = await engine.evaluate_async(candidate)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - an undecided candidate acquires nothing
            return None
        await self._trace_decision(decision)
        return decision if getattr(decision, "accepted", False) is True else None

    async def _trace_decision(self, decision: Any) -> None:
        emit = getattr(self._supervision, "emit", None)
        if not callable(emit) or not getattr(decision, "traceable", True):
            return
        event_type = (
            TRACE_INPUT_TRIGGER_ACCEPTED if decision.accepted else TRACE_INPUT_TRIGGER_REJECTED
        )
        try:
            outcome = emit(event_type, decision_trace_payload(decision))
            if inspect.isawaitable(outcome):
                await outcome
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a lost trace changes nothing decided
            return

    async def _acquire(self, listen: ListenSettings) -> bytes | None:
        """One segment from ``listen.source``, checked; ``None`` on a source failure.

        The source is invoked once with the pre-roll and the bound in its
        environment and read until it exits, which must be with status 0.
        The read is bounded by a hard deadline of ``listen.max_seconds`` plus
        ``grace_seconds`` on the sleeper: past it the source is killed and
        abandoned, however it answers a terminate, and the outcome is a
        source failure. The WAV it wrote must be of the convention with an
        honest header, last at most ``listen.max_seconds`` by that header
        and weigh at most ``max_bytes``; it is neither trimmed nor padded.
        """

        source = self._listen_source
        environment = {
            ENV_PRE_ROLL_SECONDS: f"{listen.pre_roll_seconds:g}",
            ENV_MAX_SECONDS: str(listen.max_seconds),
        }
        begin = getattr(source, "begin", None)
        recording = begin(environment) if callable(begin) else source
        # Held for ``stop_inputs``, which kills it however the loop unwinds.
        self._listen_recording = recording
        reader = asyncio.ensure_future(recording.read(listen.max_seconds))
        deadline = asyncio.ensure_future(
            self._sleeper(float(listen.max_seconds) + self._settings.grace_seconds)
        )
        try:
            await asyncio.wait({reader, deadline}, return_when=asyncio.FIRST_COMPLETED)
        except BaseException:
            _kill_recording(recording)
            _abandon(reader)
            raise
        finally:
            _abandon(deadline)
            self._listen_recording = None
        if not reader.done():
            # The deadline won: the source is killed and its read abandoned.
            _kill_recording(recording)
            _abandon(reader)
            return None
        if reader.cancelled() or reader.exception() is not None:
            return None
        data = reader.result()
        if getattr(recording, "failed_exit", False) is True:
            return None
        if not isinstance(data, (bytes, bytearray, memoryview)):
            return None
        data = bytes(data)
        if len(data) > self._settings.max_bytes:
            return None
        try:
            declared = listen_segment_data_bytes(data)
        except InvalidSegment:
            return None
        if declared > listen.max_seconds * BYTES_PER_SECOND:
            return None
        return data

    async def _transcribe_segment(self, wav: bytes) -> str | None:
        """The transcription of one acquired segment, or ``None`` on an error.

        One request through the ``transcription`` settings, bounded by its
        ``timeout_seconds`` on the sleeper; transcription disabled or found
        unavailable at ``prepare`` sends nothing and is an error.
        """

        settings = self._settings.transcription
        if not settings.enabled or not self._transcription_available:
            return None
        session = await self._session_or_none()
        if session is None:
            return None
        request = asyncio.ensure_future(self._request(session, wav))
        bound = asyncio.ensure_future(self._sleeper(settings.timeout_seconds))
        try:
            await asyncio.wait({request, bound}, return_when=asyncio.FIRST_COMPLETED)
        except BaseException:
            _abandon(request)
            raise
        finally:
            _abandon(bound)
        if not request.done():
            _abandon(request)
            return None
        if request.cancelled() or request.exception() is not None:
            return None
        answer = request.result()
        if answer.status != TRANSCRIPTION_OK or answer.text is None:
            return None
        return answer.text[: settings.max_chars]

    async def _publish_speech(self, event: Mapping[str, Any]) -> None:
        """Publish the ``speech`` event once; admit it into a shared scheduler.

        Never through the engine: the candidate's decision, recorded under
        the same ``message_id`` and destination, is the one it runs under. A
        scheduler the context carries is admitted into here. Without one the
        brain owns its scheduler and its bus consumer admits the published
        copy under the decision the engine still retains for it — so
        admitting here too would run it twice. Acquisition and transcription
        may outlast that retention (its time-to-live, or eviction by newer
        decisions): a decision the engine no longer holds is then honoured
        here, by admitting directly into the scheduler the brain publishes
        as ``(admission, runs)`` — watch-style — once the event is published,
        and the brain's consumer, finding no decision, refuses the published
        copy. The engine is only read (``recorded``), never asked again.
        """

        scheduler = self._scheduler
        if scheduler is None and not self._decision_retained(event):
            scheduler = self._published_scheduler()
        publish = getattr(self._bus, "publish", None)
        if callable(publish):
            try:
                await publish(event["type"], event["payload"], event["metadata"])
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - a lost publication is not counted published
                pass
            else:
                self._listen_counts[COUNT_PUBLISHED] += 1
        if scheduler is None:
            return
        payload = event["payload"]
        try:
            scheduler.admit(
                SessionKey(
                    platform=payload["platform"],
                    channel_id=payload["channel_id"],
                    viewer_id=payload["author"]["id"],
                ),
                Work(payload=event, source_event_id=payload["message_id"], kind=WORK_KIND),
            )
        except Exception:  # noqa: BLE001 - a refusal is the scheduler's own traced decision
            return

    def _decision_retained(self, event: Mapping[str, Any]) -> bool:
        """Whether the engine still retains the decision the brain's consumer will read.

        An unreadable lookup counts as retained: the consumer reads the same
        engine, so admitting here too could run the event twice.
        """

        recorded = getattr(self._triggers, "recorded", None)
        if not callable(recorded):
            return True
        payload = event["payload"]
        try:
            decision = recorded(
                platform=payload["platform"],
                channel_id=payload["channel_id"],
                source_event_id=payload["message_id"],
            )
        except Exception:  # noqa: BLE001 - see the docstring
            return True
        return decision is not None

    def _published_scheduler(self) -> Any | None:
        """The scheduler the brain publishes as ``(admission, runs)``, or ``None``."""

        resolve = getattr(self._services, "resolve", None)
        if not callable(resolve):
            return None
        try:
            found = resolve(ADMISSION_SERVICE_KIND, ADMISSION_SERVICE_SCOPE)
        except Exception:  # noqa: BLE001 - an unreadable registry admits nothing
            return None
        return found if callable(getattr(found, "admit", None)) else None

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
        # is authored and returned with no await after the decision. The
        # executor's completion stamp (``_observe``) follows the return; on a
        # real clock it may lag the decision by execution or preemption time,
        # which the transcription reserve mitigates but cannot rule out.
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
        """The 2xx body, or ``None`` when it exceeds the answer bound."""

        limit = (
            self._settings.transcription.max_chars * _ANSWER_BYTES_PER_CHAR
            + _ANSWER_ALLOWANCE_BYTES
        )
        return await _read_bounded(response, limit)


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


def _kill_recording(recording: Any) -> None:
    """Kill *recording* now when it can be killed; a failing kill changes nothing."""

    kill = getattr(recording, "kill", None)
    if not callable(kill):
        return
    try:
        kill()
    except Exception:  # noqa: BLE001 - the read is abandoned either way
        return


def _abandon(task: "asyncio.Future[Any]") -> None:
    """Cancel *task* without waiting for it; its outcome is consumed when it ends."""

    if not task.done():
        task.cancel()
    task.add_done_callback(_consume)


def _consume(task: "asyncio.Future[Any]") -> None:
    if not task.cancelled():
        task.exception()


async def _read_bounded(response: Any, limit: int) -> bytes | None:
    """The body of *response*, or ``None`` when it exceeds *limit* bytes.

    Read in chunks off the response's stream so an oversized (or endless)
    answer never holds more than one chunk past the bound; a response with
    no stream is read whole.
    """

    stream = getattr(response, "content", None)
    read = getattr(stream, "read", None)
    if not callable(read):
        body = bytes(await response.read())
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


async def _close_quietly(session: Any) -> None:
    close = getattr(session, "close", None)
    if callable(close):
        try:
            outcome = close()
            if inspect.isawaitable(outcome):
                await outcome
        except Exception:  # noqa: BLE001 - a transport that fails to close is gone anyway
            pass


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
        runner=runner,
        detector_session_factory=settings.get(_SEAM_DETECTOR_SESSION, DETECTOR_SESSION_FACTORY),
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
    "COUNT_CANDIDATES",
    "COUNT_DETECTOR_FAILURES",
    "COUNT_EMPTY_TRANSCRIPTIONS",
    "COUNT_PUBLISHED",
    "COUNT_SOURCE_FAILURES",
    "COUNT_TRANSCRIPTION_ERRORS",
    "DETECTOR_SESSION_FACTORY",
    "ENV_MAX_SECONDS",
    "ENV_PRE_ROLL_SECONDS",
    "PHRASE_MAX_CHARS",
    "SETTLED_DECLINED",
    "SETTLED_DETECTOR_FAILURE",
    "SETTLED_EMPTY_TRANSCRIPTION",
    "SETTLED_NO_VERDICT",
    "SETTLED_PUBLISHED",
    "SETTLED_SOURCE_FAILURE",
    "SETTLED_STOPPED",
    "SETTLED_TRANSCRIPTION_ERROR",
    "SPEECH_KIND",
    "WORK_KIND",
    "CONTENT_TYPE_WAV",
    "DEFAULT_LISTEN_MAX_SECONDS",
    "DEFAULT_LISTEN_PRE_ROLL_SECONDS",
    "DEFAULT_LISTEN_RETRY_SECONDS",
    "DEFAULT_LISTEN_SPEAKER",
    "DETECTOR_KIND_COMMAND",
    "DETECTOR_KIND_HTTP",
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
    "WAKE_WORD_TYPE",
    "WAV_HEADER_BYTES",
    "AudioInputModule",
    "AudioInputModuleError",
    "CaptureError",
    "CommandDetector",
    "CommandRecording",
    "CommandSource",
    "DetectorFailure",
    "DetectorSpec",
    "HttpDetector",
    "Verdict",
    "build_detector",
    "listen_segment_data_bytes",
    "speech_candidate",
    "speech_event",
    "FileSource",
    "InvalidSegment",
    "ListenSettings",
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
