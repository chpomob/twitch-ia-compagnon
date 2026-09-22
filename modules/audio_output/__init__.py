"""``audio.speak`` and ``audio.play`` on a configured output (R1).

The companion speaks through this module: ``audio.speak`` synthesises a text
through the configured *synthesis* endpoint and plays the WAV it answers,
``audio.play`` plays a configured *clip*. Which device the sound reaches is
the business of the configured *output* — a player command started once per
playback and fed the segment on its standard input — never of the brain and
never of a platform.

**Playback completed is the success condition** (decision 4). A call ends
``success`` only when the player accepted every byte of the WAV — the header
first, then the data chunk, every byte the synthesis returned or the clip
holds (finding P2: a player reads the format from the header on its stdin) —
and then exited 0. The result carries ``duration_ms``, read from the WAV
header, and ``played_ms``, the *accepted-bytes estimate*: the duration of the
**data** bytes the player accepted, so the header never counts as played
audio. Bytes a pipe accepted may not yet have reached the device; the
manifest's action descriptions say so and no device clock is claimed.

**Failures, each an explicit outcome** (R1, AC2). Argument checks come first
and cost nothing: a text outside 1..``max_text_chars`` characters is ``error
invalid_arguments``, a voice outside ``voices.allowed`` is ``refused
voice_not_allowed``, an output outside ``outputs`` is ``refused
output_not_allowed`` — no request is sent and no player started. Then one
synthesis request, bounded by ``synthesis.timeout_seconds`` on the injected
sleeper: a transport failure is ``error tts_unavailable``, a non-2xx answer
``error tts_failed`` naming the status code and never the body, no answer in
time ``timeout`` with cause ``synthesis``. The body is then held to its
bounds: heavier than ``max_audio_bytes`` is ``error audio_too_large``, not a
PCM RIFF/WAVE with a non-empty data chunk ``error invalid_audio``, longer
than ``max_speech_seconds`` ``error speech_too_long``. A ``sound`` naming no
clip is ``error invalid_arguments``, a clip that cannot be read at call time
``error clip_unreadable``. A player that cannot be started, or that exits
non-zero or before accepting the whole segment, is ``error playback_failed``
carrying the accepted-bytes estimate. Playback is local, so both actions
declare nothing emitted at entry and never end ``external_unknown``. The
synthesis body, the endpoint, the key, the argv and the clip paths never
appear in an observation, an error or a trace.

**Seams, for the tests.** ``_synthesis_transport`` builds the HTTP session
(an ``aiohttp`` session by default, created on first use), ``_player_runner``
replaces the asyncio-subprocess runner of decision 2, ``_sleeper`` the sleep
the synthesis bound is raced against. They are read from *settings* and never
from a configuration file.

Lifecycle: the manifest declares no role, so the handle takes part in
``prepare`` — which binds both actions and marks the module ready — and in
``close``, which withdraws readiness and releases the transport.
"""

from __future__ import annotations

import asyncio
import inspect
import math
import os
import signal
import struct
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from core.actions import ERROR_INVALID_ARGUMENTS, ERROR_TIMED_OUT
from core.contracts import ActionObservation, ActionSpec, Destination

try:  # Keep the module importable for transport-injected contract tests.
    import aiohttp
except ModuleNotFoundError:  # pragma: no cover - production installs dependencies
    aiohttp = None  # type: ignore[assignment]


MODULE_NAME = "audio_output"

SPEAK_ACTION = "audio.speak"
PLAY_ACTION = "audio.play"
PROVIDER_NAME = "audio_output"
"""Provider name in bindings, traces and provenance."""

MANIFEST_PATH = Path(__file__).with_name("module.yaml")

DEFAULT_SYNTHESIS_TIMEOUT_SECONDS = 10.0
DEFAULT_PROBE_TEXT = "ready"
DEFAULT_MAX_TEXT_CHARS = 400
DEFAULT_MAX_SPEECH_SECONDS = 20.0
DEFAULT_MAX_AUDIO_BYTES = 5_242_880
DEFAULT_STOP_GRACE_SECONDS = 1.0
DEFAULT_MAX_WAITERS = 1

WRITE_CHUNK_BYTES = 32_768
"""The most bytes one write hands the player (decision 2)."""

READ_CHUNK_BYTES = 65_536
"""The most bytes one read takes off the synthesis response's stream."""

PLAYBACK_COMPLETED = "completed"
CAUSE_SYNTHESIS = "synthesis"

ERROR_VOICE_NOT_ALLOWED = "voice_not_allowed"
ERROR_OUTPUT_NOT_ALLOWED = "output_not_allowed"
ERROR_TTS_UNAVAILABLE = "tts_unavailable"
ERROR_TTS_FAILED = "tts_failed"
ERROR_INVALID_AUDIO = "invalid_audio"
ERROR_AUDIO_TOO_LARGE = "audio_too_large"
ERROR_SPEECH_TOO_LONG = "speech_too_long"
ERROR_CLIP_UNREADABLE = "clip_unreadable"
ERROR_PLAYBACK_FAILED = "playback_failed"
_ERROR_PROVIDER_CLOSED = "provider_closed"

#: The reserved setting the entry point hands its accepted ``limits`` block
#: over in (``core.main.LIMITS_KEY``). Accepted, never read.
_ACCEPTED_LIMITS_SETTING = "limits"

_SETTING_SYNTHESIS = "synthesis"
_SETTING_VOICES = "voices"
_SETTING_OUTPUTS = "outputs"
_SETTING_DEFAULT_OUTPUT = "default_output"
_SETTING_CLIPS = "clips"
_SETTING_MAX_TEXT_CHARS = "max_text_chars"
_SETTING_MAX_SPEECH_SECONDS = "max_speech_seconds"
_SETTING_MAX_AUDIO_BYTES = "max_audio_bytes"
_SETTING_STOP_GRACE_SECONDS = "stop_grace_seconds"
_SETTING_MAX_WAITERS = "max_waiters"
_SETTING_REQUIRED = "required"
_SETTINGS = frozenset(
    {
        _SETTING_SYNTHESIS,
        _SETTING_VOICES,
        _SETTING_OUTPUTS,
        _SETTING_DEFAULT_OUTPUT,
        _SETTING_CLIPS,
        _SETTING_MAX_TEXT_CHARS,
        _SETTING_MAX_SPEECH_SECONDS,
        _SETTING_MAX_AUDIO_BYTES,
        _SETTING_STOP_GRACE_SECONDS,
        _SETTING_MAX_WAITERS,
        _SETTING_REQUIRED,
    }
)
_SYNTHESIS_FIELDS = frozenset(
    {"endpoint", "model", "api_key", "timeout_seconds", "probe", "probe_text"}
)
_VOICES_FIELDS = frozenset({"allowed", "default"})

#: The injection seams: read from *settings*, never from a configuration file.
_SEAM_SYNTHESIS_TRANSPORT = "_synthesis_transport"
_SEAM_PLAYER_RUNNER = "_player_runner"
_SEAM_SLEEPER = "_sleeper"
_SEAMS = frozenset({_SEAM_SYNTHESIS_TRANSPORT, _SEAM_PLAYER_RUNNER, _SEAM_SLEEPER})
#: What a player runner must provide (decision 2); ``stop`` is used when present.
_RUNNER_METHODS = ("start", "write", "close_stdin", "wait")

_WAVE_FORMAT_PCM = 1
_WAVE_FORMAT_EXTENSIBLE = 0xFFFE


class AudioOutputModuleError(RuntimeError):
    """A setup failure whose message contains no configured value."""


class InvalidAudio(ValueError):
    """A body that is not a PCM RIFF/WAVE segment with a non-empty data chunk."""


class _CallFailure(Exception):
    """One explicit non-success outcome of a call, raised to its invoke."""

    def __init__(self, status: str, code: str, message: str, **details: Any) -> None:
        super().__init__(code)
        self.status = status
        self.code = code
        self.message = message
        self.details = details


# --------------------------------------------------------------------------- #
# Settings validation hook (R7)
# --------------------------------------------------------------------------- #


def validate_settings(settings: Any) -> list[str]:
    """Check this module's settings; return one diagnostic per offending field.

    This is the hook ``settings_validator`` in the manifest names. Each
    diagnostic names the module and the field and nothing else — no
    endpoint, key, argv, path or voice is echoed; an empty list means the
    settings are accepted.

    ``synthesis`` must carry string ``endpoint`` and ``model`` (either may be
    empty: no endpoint is assumed), an optional string ``api_key``, a finite
    positive ``timeout_seconds``, a boolean ``probe`` and a non-empty
    ``probe_text``; ``voices.allowed`` a non-empty list of names and
    ``voices.default`` one of them; ``outputs`` a non-empty mapping of
    ``{player: {argv: [...]}}`` with a non-empty argv each and
    ``default_output`` one of them; ``clips`` a mapping of ``{path}``. The
    bounds are positive (``max_waiters`` and ``stop_grace_seconds`` may be
    0), ``required`` a boolean. The reserved ``limits`` block is accepted and
    not inspected; the transport and sleeper seams must be callable, the
    player runner must provide the runner methods; any other key is refused
    by name.
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
    for seam in (_SEAM_SYNTHESIS_TRANSPORT, _SEAM_SLEEPER):
        if seam in settings and not callable(settings[seam]):
            diagnostics.append(_setting_diagnostic(seam, "must be callable"))
    if _SEAM_PLAYER_RUNNER in settings and not all(
        callable(getattr(settings[_SEAM_PLAYER_RUNNER], method, None))
        for method in _RUNNER_METHODS
    ):
        diagnostics.append(
            _setting_diagnostic(
                _SEAM_PLAYER_RUNNER, "must provide start, write, close_stdin and wait"
            )
        )

    diagnostics.extend(_synthesis_diagnostics(settings.get(_SETTING_SYNTHESIS)))
    diagnostics.extend(_voices_diagnostics(settings.get(_SETTING_VOICES)))

    outputs = settings.get(_SETTING_OUTPUTS)
    output_names: set[str] = set()
    if outputs is None:
        diagnostics.append(_setting_diagnostic(_SETTING_OUTPUTS, "is required"))
    elif not isinstance(outputs, Mapping) or not outputs:
        diagnostics.append(
            _setting_diagnostic(_SETTING_OUTPUTS, "must be a non-empty mapping of outputs")
        )
    else:
        for name, entry in outputs.items():
            if not _is_text(name):
                diagnostics.append(
                    _setting_diagnostic(_SETTING_OUTPUTS, "must use non-empty output names")
                )
                continue
            output_names.add(name)
            diagnostics.extend(_output_diagnostics(f"{_SETTING_OUTPUTS}.{name}", entry))

    default_output = settings.get(_SETTING_DEFAULT_OUTPUT)
    if default_output is None:
        diagnostics.append(_setting_diagnostic(_SETTING_DEFAULT_OUTPUT, "is required"))
    elif not _is_text(default_output):
        diagnostics.append(
            _setting_diagnostic(_SETTING_DEFAULT_OUTPUT, "must be a non-empty string")
        )
    elif output_names and default_output not in output_names:
        diagnostics.append(
            _setting_diagnostic(_SETTING_DEFAULT_OUTPUT, "must name a configured output")
        )

    clips = settings.get(_SETTING_CLIPS)
    if clips is not None:
        if not isinstance(clips, Mapping):
            diagnostics.append(_setting_diagnostic(_SETTING_CLIPS, "must be a mapping of clips"))
        else:
            for name, entry in clips.items():
                if not _is_text(name):
                    diagnostics.append(
                        _setting_diagnostic(_SETTING_CLIPS, "must use non-empty clip names")
                    )
                    continue
                diagnostics.extend(_clip_diagnostics(f"{_SETTING_CLIPS}.{name}", entry))

    for field_name in (_SETTING_MAX_TEXT_CHARS, _SETTING_MAX_AUDIO_BYTES):
        if field_name in settings and not _is_positive_int(settings[field_name]):
            diagnostics.append(_setting_diagnostic(field_name, "must be a positive integer"))
    if _SETTING_MAX_SPEECH_SECONDS in settings and not _is_positive_number(
        settings[_SETTING_MAX_SPEECH_SECONDS]
    ):
        diagnostics.append(
            _setting_diagnostic(_SETTING_MAX_SPEECH_SECONDS, "must be a finite positive number")
        )
    if _SETTING_STOP_GRACE_SECONDS in settings and not _is_non_negative_number(
        settings[_SETTING_STOP_GRACE_SECONDS]
    ):
        diagnostics.append(
            _setting_diagnostic(
                _SETTING_STOP_GRACE_SECONDS, "must be a finite non-negative number"
            )
        )
    if _SETTING_MAX_WAITERS in settings and not _is_non_negative_int(
        settings[_SETTING_MAX_WAITERS]
    ):
        diagnostics.append(
            _setting_diagnostic(_SETTING_MAX_WAITERS, "must be a non-negative integer")
        )
    if _SETTING_REQUIRED in settings and not isinstance(settings[_SETTING_REQUIRED], bool):
        diagnostics.append(_setting_diagnostic(_SETTING_REQUIRED, "must be a boolean"))
    return diagnostics


def _synthesis_diagnostics(synthesis: Any) -> list[str]:
    field = _SETTING_SYNTHESIS
    if synthesis is None:
        return [_setting_diagnostic(field, "is required")]
    if not isinstance(synthesis, Mapping):
        return [_setting_diagnostic(field, "must be a mapping")]
    diagnostics: list[str] = []
    for key in synthesis:
        if key not in _SYNTHESIS_FIELDS:
            diagnostics.append(
                _setting_diagnostic(f"{field}.{key}", "is not a field of the synthesis provider")
            )
    for key in ("endpoint", "model"):
        if key not in synthesis:
            diagnostics.append(_setting_diagnostic(f"{field}.{key}", "is required"))
        elif not isinstance(synthesis[key], str):
            diagnostics.append(_setting_diagnostic(f"{field}.{key}", "must be a string"))
    if "api_key" in synthesis and not isinstance(synthesis["api_key"], str):
        diagnostics.append(_setting_diagnostic(f"{field}.api_key", "must be a string"))
    if "timeout_seconds" in synthesis and not _is_positive_number(synthesis["timeout_seconds"]):
        diagnostics.append(
            _setting_diagnostic(f"{field}.timeout_seconds", "must be a finite positive number")
        )
    if "probe" in synthesis and not isinstance(synthesis["probe"], bool):
        diagnostics.append(_setting_diagnostic(f"{field}.probe", "must be a boolean"))
    if "probe_text" in synthesis and not _is_text(synthesis["probe_text"]):
        diagnostics.append(_setting_diagnostic(f"{field}.probe_text", "must be a non-empty string"))
    return diagnostics


def _voices_diagnostics(voices: Any) -> list[str]:
    field = _SETTING_VOICES
    if voices is None:
        return [_setting_diagnostic(field, "is required")]
    if not isinstance(voices, Mapping):
        return [_setting_diagnostic(field, "must be a mapping")]
    diagnostics: list[str] = []
    for key in voices:
        if key not in _VOICES_FIELDS:
            diagnostics.append(
                _setting_diagnostic(f"{field}.{key}", "is not a field of the voice allowlist")
            )
    allowed = voices.get("allowed")
    names: set[str] = set()
    if (
        not isinstance(allowed, (list, tuple))
        or not allowed
        or not all(_is_text(item) for item in allowed)
    ):
        diagnostics.append(
            _setting_diagnostic(f"{field}.allowed", "must be a non-empty list of voice names")
        )
    else:
        names = set(allowed)
    default = voices.get("default")
    if not _is_text(default):
        diagnostics.append(_setting_diagnostic(f"{field}.default", "must be a non-empty string"))
    elif names and default not in names:
        diagnostics.append(_setting_diagnostic(f"{field}.default", "must be an allowed voice"))
    return diagnostics


def _output_diagnostics(field: str, entry: Any) -> list[str]:
    if not isinstance(entry, Mapping):
        return [_setting_diagnostic(field, "must be a mapping")]
    diagnostics = [
        _setting_diagnostic(f"{field}.{key}", "is not a field of an output")
        for key in entry
        if key != "player"
    ]
    player = entry.get("player")
    if not isinstance(player, Mapping):
        diagnostics.append(_setting_diagnostic(f"{field}.player", "must be a mapping"))
        return diagnostics
    for key in player:
        if key != "argv":
            diagnostics.append(
                _setting_diagnostic(f"{field}.player.{key}", "is not a field of a player")
            )
    argv = player.get("argv")
    if not isinstance(argv, (list, tuple)) or not argv or not all(_is_text(item) for item in argv):
        diagnostics.append(
            _setting_diagnostic(f"{field}.player.argv", "must be a non-empty list of strings")
        )
    return diagnostics


def _clip_diagnostics(field: str, entry: Any) -> list[str]:
    if not isinstance(entry, Mapping):
        return [_setting_diagnostic(field, "must be a mapping")]
    diagnostics = [
        _setting_diagnostic(f"{field}.{key}", "is not a field of a clip")
        for key in entry
        if key != "path"
    ]
    if not _is_text(entry.get("path")):
        diagnostics.append(_setting_diagnostic(f"{field}.path", "must be a non-empty string"))
    return diagnostics


def _setting_diagnostic(field_name: str, reason: str) -> str:
    return f"module {MODULE_NAME!r}: field {field_name!r}: {reason}"


def _is_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def _is_non_negative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _is_finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _is_positive_number(value: Any) -> bool:
    return _is_finite_number(value) and value > 0


def _is_non_negative_number(value: Any) -> bool:
    return _is_finite_number(value) and value >= 0


@dataclass(frozen=True, slots=True)
class _Settings:
    """The accepted settings, parsed once."""

    endpoint: str
    model: str
    api_key: str
    synthesis_timeout_seconds: float
    probe: bool
    probe_text: str
    voices: tuple[str, ...]
    default_voice: str
    outputs: Mapping[str, tuple[str, ...]]
    default_output: str
    clips: Mapping[str, str]
    max_text_chars: int
    max_speech_seconds: float
    max_audio_bytes: int
    stop_grace_seconds: float
    max_waiters: int
    required: bool

    @classmethod
    def from_mapping(cls, settings: Mapping[str, Any]) -> "_Settings":
        synthesis = settings[_SETTING_SYNTHESIS]
        voices = settings[_SETTING_VOICES]
        return cls(
            endpoint=str(synthesis["endpoint"]),
            model=str(synthesis["model"]),
            api_key=str(synthesis.get("api_key", "")),
            synthesis_timeout_seconds=float(
                synthesis.get("timeout_seconds", DEFAULT_SYNTHESIS_TIMEOUT_SECONDS)
            ),
            probe=bool(synthesis.get("probe", True)),
            probe_text=str(synthesis.get("probe_text", DEFAULT_PROBE_TEXT)),
            voices=tuple(str(name) for name in voices["allowed"]),
            default_voice=str(voices["default"]),
            outputs={
                str(name): tuple(str(item) for item in entry["player"]["argv"])
                for name, entry in settings[_SETTING_OUTPUTS].items()
            },
            default_output=str(settings[_SETTING_DEFAULT_OUTPUT]),
            clips={
                str(name): str(entry["path"])
                for name, entry in (settings.get(_SETTING_CLIPS) or {}).items()
            },
            max_text_chars=int(settings.get(_SETTING_MAX_TEXT_CHARS, DEFAULT_MAX_TEXT_CHARS)),
            max_speech_seconds=float(
                settings.get(_SETTING_MAX_SPEECH_SECONDS, DEFAULT_MAX_SPEECH_SECONDS)
            ),
            max_audio_bytes=int(settings.get(_SETTING_MAX_AUDIO_BYTES, DEFAULT_MAX_AUDIO_BYTES)),
            stop_grace_seconds=float(
                settings.get(_SETTING_STOP_GRACE_SECONDS, DEFAULT_STOP_GRACE_SECONDS)
            ),
            max_waiters=int(settings.get(_SETTING_MAX_WAITERS, DEFAULT_MAX_WAITERS)),
            required=bool(settings.get(_SETTING_REQUIRED, False)),
        )


# --------------------------------------------------------------------------- #
# WAV reading (decision 3: private to this module)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class WavInfo:
    """What a RIFF/WAVE header says about its PCM data chunk.

    ``data_offset`` is the byte offset of the ``data`` chunk's payload — 44
    for the canonical header, more when another chunk (a ``LIST``, say)
    precedes it — and ``data_bytes`` the payload size the chunk declares.
    """

    sample_rate: int
    channels: int
    bits: int
    data_offset: int
    data_bytes: int

    @property
    def bytes_per_second(self) -> int:
        return self.sample_rate * self.channels * (self.bits // 8)

    @property
    def duration_ms(self) -> int:
        """``data_bytes × 1000 // (rate × channels × bytes_per_sample)``."""

        return self.data_bytes * 1000 // self.bytes_per_second

    def played_ms(self, accepted_total: int) -> int:
        """The accepted-bytes estimate for *accepted_total* bytes of the whole body.

        Only data bytes count: ``min(data_bytes, max(0, accepted_total −
        data_offset))``, converted with the header formula, rounded down to
        the millisecond — so a player that took only header bytes has played
        nothing, and the estimate never exceeds :attr:`duration_ms`.
        """

        accepted_data = min(self.data_bytes, max(0, int(accepted_total) - self.data_offset))
        return min(self.duration_ms, accepted_data * 1000 // self.bytes_per_second)


def parse_wav(data: bytes) -> WavInfo:
    """Read the PCM format and data chunk of *data*, or raise :class:`InvalidAudio`.

    The ``RIFF``/``WAVE`` chunks are walked in order (a pad byte follows an
    odd-sized chunk); the ``fmt `` chunk must precede ``data`` and declare
    PCM (plain, or the extensible form with the PCM sub-format) with a
    positive rate, channel count and a whole number of bytes per sample. The
    ``data`` chunk must declare a non-empty payload that the body actually
    holds: a declared size larger than the body is refused, never trusted.
    """

    data = bytes(data)
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise InvalidAudio("not a RIFF/WAVE body")
    position = 12
    fmt: tuple[int, int, int] | None = None
    while position + 8 <= len(data):
        kind = data[position : position + 4]
        (size,) = struct.unpack("<I", data[position + 4 : position + 8])
        payload = position + 8
        if kind == b"fmt ":
            fmt = _parse_fmt(data[payload : payload + size], size)
        elif kind == b"data":
            if fmt is None:
                raise InvalidAudio("data chunk precedes the fmt chunk")
            if size == 0:
                raise InvalidAudio("empty data chunk")
            if payload + size > len(data):
                raise InvalidAudio("data chunk larger than the body")
            rate, channels, bits = fmt
            return WavInfo(rate, channels, bits, payload, size)
        position = payload + size + (size & 1)
    raise InvalidAudio("no data chunk")


def _parse_fmt(chunk: bytes, size: int) -> tuple[int, int, int]:
    if size < 16 or len(chunk) < 16:
        raise InvalidAudio("fmt chunk too short")
    audio_format, channels, rate, _byte_rate, _align, bits = struct.unpack("<HHIIHH", chunk[:16])
    if audio_format == _WAVE_FORMAT_EXTENSIBLE:
        # cbSize, valid bits, channel mask, then the sub-format GUID whose
        # first two bytes carry the format code.
        if len(chunk) < 26:
            raise InvalidAudio("extensible fmt chunk too short")
        (audio_format,) = struct.unpack("<H", chunk[24:26])
    if audio_format != _WAVE_FORMAT_PCM:
        raise InvalidAudio("not PCM")
    if rate <= 0 or channels <= 0 or bits <= 0 or bits % 8:
        raise InvalidAudio("unusable PCM format")
    return rate, channels, bits


# --------------------------------------------------------------------------- #
# The player runner (decision 2)
# --------------------------------------------------------------------------- #

Sleeper = Callable[[float], Awaitable[Any]]


class SubprocessPlayerRunner:
    """The shipped player runner: one asyncio subprocess per playback.

    ``start(argv)`` spawns the player in its own session with stdin a pipe
    and stdout/stderr discarded, so nothing it prints reaches a trace;
    ``write(process, chunk)`` writes one bounded chunk and waits for the pipe
    to drain — a blocking pipe, so the count it returns is what the player
    accepted (or what sits in the pipe buffer); ``close_stdin`` closes the
    pipe and raises if it broke before the buffered tail was delivered, ``wait`` reaps the exit status. ``stop(process, grace_seconds)``
    terminates the player's process group now and kills it once the grace
    has passed on the injected sleeper, then reaps it.
    """

    def __init__(
        self,
        *,
        sleeper: Sleeper = asyncio.sleep,
        spawn: Callable[..., Awaitable[Any]] = asyncio.create_subprocess_exec,
    ) -> None:
        self._sleeper = sleeper
        self._spawn = spawn

    async def start(self, argv: Sequence[str]) -> Any:
        return await self._spawn(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
        )

    async def write(self, process: Any, chunk: bytes) -> int:
        stdin = process.stdin
        if stdin is None:
            raise BrokenPipeError("player has no standard input")
        stdin.write(bytes(chunk))
        await stdin.drain()
        return len(chunk)

    async def close_stdin(self, process: Any) -> None:
        stdin = process.stdin
        if stdin is None:
            return
        stdin.close()
        # A pipe broken while the buffered tail flushes is raised: the
        # player did not take every byte, so the playback is incomplete.
        await stdin.wait_closed()

    async def wait(self, process: Any) -> int:
        return await process.wait()

    async def stop(self, process: Any, grace_seconds: float) -> None:
        _signal_group(process, signal.SIGTERM)
        if process.returncode is None and grace_seconds > 0:
            await self._sleeper(grace_seconds)
        if process.returncode is None:
            _signal_group(process, signal.SIGKILL)
        await process.wait()


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


def _default_session_factory() -> Any:
    if aiohttp is None:
        raise RuntimeError("aiohttp is not installed")
    return aiohttp.ClientSession()


SESSION_FACTORY: Callable[[], Any] = _default_session_factory


# --------------------------------------------------------------------------- #
# The handle
# --------------------------------------------------------------------------- #


class _SpeakProvider:
    """The ``audio.speak`` provider the executor invokes (R1)."""

    __slots__ = ("_module",)

    name = PROVIDER_NAME

    def __init__(self, module: "AudioOutputModule") -> None:
        self._module = module

    async def invoke(self, invocation: Any) -> ActionObservation:
        return await self._module._invoke_speak(invocation)


class _PlayProvider:
    """The ``audio.play`` provider the executor invokes (R1)."""

    __slots__ = ("_module",)

    name = PROVIDER_NAME

    def __init__(self, module: "AudioOutputModule") -> None:
        self._module = module

    async def invoke(self, invocation: Any) -> ActionObservation:
        return await self._module._invoke_play(invocation)


class AudioOutputModule:
    """The v2 handle: bind both actions at ``prepare``, synthesise and play on demand."""

    def __init__(
        self,
        context: Any,
        settings: _Settings,
        *,
        transport_factory: Callable[[], Any],
        runner: Any,
        sleeper: Sleeper,
    ) -> None:
        self._actions = context.actions
        self._settings = settings
        self._transport_factory = transport_factory
        self._runner = runner
        self._sleeper = sleeper
        self._session: Any | None = None
        self._speak_provider = _SpeakProvider(self)
        self._play_provider = _PlayProvider(self)
        self._stops: set[asyncio.Future[Any]] = set()
        self._prepared = False
        self._closed = False

    @property
    def settings(self) -> _Settings:
        return self._settings

    # -- lifecycle hooks ---------------------------------------------------- #

    async def prepare(self) -> None:
        """Bind both actions and mark the module ready.

        No request is sent and no player is started here: the synthesis
        endpoint is taken as unverified (``probe: false`` semantics), so an
        unreachable one surfaces on the first call as ``tts_unavailable``.
        """

        if self._prepared or self._closed:
            return
        for name, provider in (
            (SPEAK_ACTION, self._speak_provider),
            (PLAY_ACTION, self._play_provider),
        ):
            spec = _declared_spec(name)
            try:
                self._actions.register(spec, provider, provider_name=PROVIDER_NAME)
            except Exception:
                raise AudioOutputModuleError(
                    f"{MODULE_NAME} prepare: action binding failed"
                ) from None
        self._actions.mark_ready()
        self._prepared = True

    async def close(self) -> None:
        """Withdraw readiness, join the stops in flight, release the transport."""

        if self._closed:
            return
        self._closed = True
        if self._prepared:
            self._actions.mark_not_ready()
        if self._stops:
            await asyncio.wait(set(self._stops))
        session, self._session = self._session, None
        close = getattr(session, "close", None)
        if callable(close):
            try:
                outcome = close()
                if inspect.isawaitable(outcome):
                    await outcome
            except Exception:  # noqa: BLE001 - a transport that fails to close is gone anyway
                pass

    # -- the two actions (R1, AC1, AC2) --------------------------------------- #

    async def _invoke_speak(self, invocation: Any) -> ActionObservation:
        """Serve one ``audio.speak`` call for the executor."""

        # Playback is local: nothing reaches the outside world, so an
        # interruption is a certain ``timeout``/``cancelled`` (decision 4).
        invocation.mark_not_emitted()
        provenance = _provenance(invocation.call, SPEAK_ACTION)
        try:
            if self._closed:
                raise _CallFailure("error", _ERROR_PROVIDER_CLOSED, f"{MODULE_NAME}: closed")
            arguments = invocation.call.arguments
            text = arguments.get("text")
            if not isinstance(text, str) or not 1 <= len(text) <= self._settings.max_text_chars:
                raise _CallFailure(
                    "error",
                    ERROR_INVALID_ARGUMENTS,
                    f"text must hold 1 to {self._settings.max_text_chars} characters",
                )
            voice = arguments.get("voice", self._settings.default_voice)
            if not isinstance(voice, str) or voice not in self._settings.voices:
                raise _CallFailure(
                    "refused", ERROR_VOICE_NOT_ALLOWED, "voice is not one this module allows"
                )
            output = self._output(arguments)
            provenance["output"] = output
            provenance["voice"] = voice
            body = await self._synthesise(text, voice)
            info = self._admit(body)
            played_ms = await self._play(output, body, info)
        except _CallFailure as failure:
            return _failure_observation(provenance, failure)
        return ActionObservation(
            status="success",
            provenance=provenance,
            result={
                "output": output,
                "voice": voice,
                "duration_ms": info.duration_ms,
                "played_ms": played_ms,
                "playback": PLAYBACK_COMPLETED,
            },
        )

    async def _invoke_play(self, invocation: Any) -> ActionObservation:
        """Serve one ``audio.play`` call for the executor."""

        invocation.mark_not_emitted()
        provenance = _provenance(invocation.call, PLAY_ACTION)
        try:
            if self._closed:
                raise _CallFailure("error", _ERROR_PROVIDER_CLOSED, f"{MODULE_NAME}: closed")
            arguments = invocation.call.arguments
            sound = arguments.get("sound")
            if not isinstance(sound, str) or sound not in self._settings.clips:
                raise _CallFailure(
                    "error", ERROR_INVALID_ARGUMENTS, "sound is not a clip this module configures"
                )
            output = self._output(arguments)
            provenance["output"] = output
            provenance["sound"] = sound
            body = await self._read_clip(sound)
            info = self._admit(body)
            played_ms = await self._play(output, body, info)
        except _CallFailure as failure:
            return _failure_observation(provenance, failure)
        return ActionObservation(
            status="success",
            provenance=provenance,
            result={
                "output": output,
                "sound": sound,
                "duration_ms": info.duration_ms,
                "played_ms": played_ms,
                "playback": PLAYBACK_COMPLETED,
            },
        )

    def _output(self, arguments: Mapping[str, Any]) -> str:
        output = arguments.get("output", self._settings.default_output)
        if not isinstance(output, str) or output not in self._settings.outputs:
            raise _CallFailure(
                "refused", ERROR_OUTPUT_NOT_ALLOWED, "output is not one this module configures"
            )
        return output

    # -- synthesis ------------------------------------------------------------ #

    async def _synthesise(self, text: str, voice: str) -> bytes:
        """One synthesis request, bounded by ``timeout_seconds`` on the sleeper."""

        settings = self._settings
        if not settings.endpoint:
            raise _CallFailure(
                "error", ERROR_TTS_UNAVAILABLE, "synthesis endpoint is not configured"
            )
        session = await self._session_or_fail()
        payload = {
            "model": settings.model,
            "voice": voice,
            "input": text,
            "response_format": "wav",
        }
        headers = {"Content-Type": "application/json"}
        if settings.api_key:
            headers["Authorization"] = f"Bearer {settings.api_key}"

        request = asyncio.ensure_future(self._request(session, payload, headers))
        timer = asyncio.ensure_future(self._sleeper(settings.synthesis_timeout_seconds))
        try:
            done, _pending = await asyncio.wait(
                {request, timer}, return_when=asyncio.FIRST_COMPLETED
            )
        except asyncio.CancelledError:
            await _settle(request)
            await _settle(timer)
            raise
        if request not in done:
            # The bound won: the request's cancellation releases its response.
            await _settle(request)
            await _settle(timer)
            raise _CallFailure(
                "timeout",
                ERROR_TIMED_OUT,
                "synthesis did not answer within timeout_seconds",
                cause=CAUSE_SYNTHESIS,
            )
        await _settle(timer)
        if request.cancelled():
            raise _CallFailure("error", ERROR_TTS_UNAVAILABLE, "synthesis request was interrupted")
        failure = request.exception()
        if isinstance(failure, _CallFailure):
            raise failure
        if failure is not None:
            raise _CallFailure(
                "error", ERROR_TTS_UNAVAILABLE, "synthesis endpoint could not be reached"
            )
        return request.result()

    async def _session_or_fail(self) -> Any:
        if self._session is None:
            try:
                created = self._transport_factory()
                if inspect.isawaitable(created):
                    created = await created
            except Exception:
                raise _CallFailure(
                    "error", ERROR_TTS_UNAVAILABLE, "synthesis transport could not be created"
                ) from None
            self._session = created
        return self._session

    async def _request(
        self, session: Any, payload: Mapping[str, Any], headers: Mapping[str, str]
    ) -> bytes:
        """POST *payload*; the 2xx body as bytes. The body of any other answer is never read."""

        response: Any | None = None
        try:
            response = await session.post(
                self._settings.endpoint, json=dict(payload), headers=dict(headers)
            )
            status = getattr(response, "status", None)
            if not isinstance(status, int) or isinstance(status, bool) or not 200 <= status < 300:
                shown = status if isinstance(status, int) and not isinstance(status, bool) else "?"
                raise _CallFailure("error", ERROR_TTS_FAILED, f"synthesis answered HTTP {shown}")
            return await self._read_bounded(response)
        finally:
            if response is not None:
                await _release(response)

    async def _read_bounded(self, response: Any) -> bytes:
        """The 2xx body, at most ``max_audio_bytes + 1`` bytes of it.

        The body is read in chunks off the response's stream and the read
        stops once the bound is passed, so an oversized (or endless) answer
        never holds more than one chunk over ``max_audio_bytes`` in memory;
        ``_admit`` then refuses it. A response with no stream is read whole.
        """

        limit = self._settings.max_audio_bytes + 1
        stream = getattr(response, "content", None)
        read = getattr(stream, "read", None)
        if not callable(read):
            body = await response.read()
            if not isinstance(body, (bytes, bytearray, memoryview)):
                raise _CallFailure("error", ERROR_INVALID_AUDIO, "synthesis answered no bytes")
            return bytes(body)
        parts: list[bytes] = []
        total = 0
        while total < limit:
            chunk = await read(min(READ_CHUNK_BYTES, limit - total))
            if not isinstance(chunk, (bytes, bytearray, memoryview)):
                raise _CallFailure("error", ERROR_INVALID_AUDIO, "synthesis answered no bytes")
            if not chunk:
                break
            parts.append(bytes(chunk))
            total += len(chunk)
        return b"".join(parts)

    # -- clips ---------------------------------------------------------------- #

    async def _read_clip(self, sound: str) -> bytes:
        """The clip's bytes now, read off the loop, at most ``max_audio_bytes + 1``."""

        path = self._settings.clips[sound]
        limit = self._settings.max_audio_bytes + 1

        def _read() -> bytes:
            with open(path, "rb") as handle:
                return handle.read(limit)

        try:
            return await asyncio.to_thread(_read)
        except OSError:
            raise _CallFailure(
                "error", ERROR_CLIP_UNREADABLE, f"clip {sound!r} could not be read"
            ) from None

    # -- bounds --------------------------------------------------------------- #

    def _admit(self, body: bytes) -> WavInfo:
        """Hold a body to ``max_audio_bytes``, the WAV shape and ``max_speech_seconds``."""

        settings = self._settings
        if len(body) > settings.max_audio_bytes:
            raise _CallFailure("error", ERROR_AUDIO_TOO_LARGE, "audio exceeds max_audio_bytes")
        try:
            info = parse_wav(body)
        except InvalidAudio:
            raise _CallFailure(
                "error", ERROR_INVALID_AUDIO, "audio is not a PCM WAV with audio data"
            ) from None
        if info.duration_ms > settings.max_speech_seconds * 1000:
            raise _CallFailure(
                "error",
                ERROR_SPEECH_TOO_LONG,
                "audio is longer than max_speech_seconds",
                duration_ms=info.duration_ms,
            )
        return info

    # -- playback (decision 2, finding P2) ------------------------------------ #

    async def _play(self, output: str, body: bytes, info: WavInfo) -> int:
        """Feed the whole WAV to one player on *output*; ``played_ms`` on completion.

        Every byte is written — the header first, then the data chunk — in
        writes of at most :data:`WRITE_CHUNK_BYTES`, and the running total is
        read from the runner after *every* write, so a player that stops
        reading mid-chunk is measured at the byte. Completion is every byte
        accepted, standard input closed cleanly and exit 0; anything else is ``playback_failed`` with the
        accepted-bytes estimate.
        """

        runner = self._runner
        try:
            process = await runner.start(list(self._settings.outputs[output]))
        except asyncio.CancelledError:
            raise
        except Exception:
            raise _CallFailure(
                "error",
                ERROR_PLAYBACK_FAILED,
                f"output {output!r}: player could not be started",
                played_ms=0,
                duration_ms=info.duration_ms,
            ) from None

        accepted_total = 0
        try:
            while accepted_total < len(body):
                chunk = body[accepted_total : accepted_total + WRITE_CHUNK_BYTES]
                try:
                    accepted = await runner.write(process, chunk)
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 - a closed pipe ends the writes
                    break
                count = _accepted_count(accepted, len(chunk))
                if count <= 0:
                    break
                accepted_total += count
            closed = True
            try:
                await runner.close_stdin(process)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - a failed close is an incomplete delivery
                closed = False
            try:
                returncode = await runner.wait(process)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - an unreadable exit is not a completion
                returncode = None
        except asyncio.CancelledError:
            # The call is going away: the player must not outlive it.
            self._stop_detached(process)
            raise

        played_ms = info.played_ms(accepted_total)
        if returncode == 0 and closed and accepted_total >= len(body):
            return played_ms
        if returncode != 0:
            reason = f"player exited with status {returncode}"
        elif not closed:
            reason = "player's input broke before the whole segment was delivered"
        else:
            reason = "player exited before accepting the whole segment"
        raise _CallFailure(
            "error",
            ERROR_PLAYBACK_FAILED,
            f"output {output!r}: {reason}",
            played_ms=played_ms,
            duration_ms=info.duration_ms,
        )

    def _stop_detached(self, process: Any) -> None:
        stop = getattr(self._runner, "stop", None)
        if not callable(stop):
            return
        task = asyncio.ensure_future(_quietly(stop(process, self._settings.stop_grace_seconds)))
        self._stops.add(task)
        task.add_done_callback(self._stops.discard)


def _accepted_count(value: Any, offered: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        return 0
    return max(0, min(int(value), offered))


def _provenance(call: Any, action: str) -> dict[str, Any]:
    destination = call.destination
    return {
        "provider": PROVIDER_NAME,
        "platform": destination.platform,
        "channel_id": destination.channel_id,
        "route": action,
    }


def _failure_observation(provenance: Mapping[str, Any], failure: _CallFailure) -> ActionObservation:
    error: dict[str, Any] = {
        "code": failure.code,
        "message": failure.message,
        "retryable": False,
    }
    error.update(failure.details)
    return ActionObservation(status=failure.status, provenance=provenance, error=error)


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


async def _quietly(awaitable: Awaitable[Any]) -> None:
    try:
        await awaitable
    except Exception:  # noqa: BLE001 - a failed stop leaves nothing to report here
        pass


async def _settle(task: "asyncio.Future[Any]") -> None:
    """Cancel *task* if still running and wait for it, consuming its outcome."""

    if not task.done():
        task.cancel()
    await asyncio.wait({task})
    if not task.cancelled():
        task.exception()


# --------------------------------------------------------------------------- #
# Activation (R7)
# --------------------------------------------------------------------------- #


async def activate(
    context: Any,
    settings: Mapping[str, Any],
    catalog: Mapping[str, Mapping[str, Any]],
) -> AudioOutputModule:
    """Build the handle from the scoped runtime context (R7).

    Settings are checked through the same hook the loader ran, so a handle
    built outside the loader is refused on the same terms. The seams
    ``_synthesis_transport``, ``_player_runner`` and ``_sleeper`` are read
    from *settings* and default to an ``aiohttp`` session factory (the
    session is created on first use, never here), the asyncio-subprocess
    :class:`SubprocessPlayerRunner` and ``asyncio.sleep``.
    """

    del catalog  # Capabilities are declared by the colocated manifest.
    actions = getattr(context, "actions", None)
    if actions is None or not all(
        callable(getattr(actions, method, None))
        for method in ("register", "mark_ready", "mark_not_ready")
    ):
        raise AudioOutputModuleError(f"{MODULE_NAME} activation: runtime context is invalid")
    if not isinstance(settings, Mapping):
        raise AudioOutputModuleError(f"{MODULE_NAME} configuration: settings must be a mapping")
    diagnostics = validate_settings(settings)
    if diagnostics:
        raise AudioOutputModuleError(
            f"{MODULE_NAME} configuration: settings were refused "
            f"({len(diagnostics)} diagnostics)"
        )
    parsed = _Settings.from_mapping(settings)
    sleeper = settings.get(_SEAM_SLEEPER, asyncio.sleep)
    runner = settings.get(_SEAM_PLAYER_RUNNER)
    if runner is None:
        runner = SubprocessPlayerRunner(sleeper=sleeper)
    for method in _RUNNER_METHODS:
        if not callable(getattr(runner, method, None)):
            raise AudioOutputModuleError(
                f"{MODULE_NAME} configuration: player runner lacks {method}()"
            )
    transport_factory = settings.get(_SEAM_SYNTHESIS_TRANSPORT, SESSION_FACTORY)
    return AudioOutputModule(
        context,
        parsed,
        transport_factory=transport_factory,
        runner=runner,
        sleeper=sleeper,
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
        )
    except Exception:
        raise AudioOutputModuleError(
            f"{MODULE_NAME} prepare: manifest declaration of {action_name!r} is invalid"
        ) from None


__all__ = [
    "CAUSE_SYNTHESIS",
    "DEFAULT_MAX_AUDIO_BYTES",
    "DEFAULT_MAX_SPEECH_SECONDS",
    "DEFAULT_MAX_TEXT_CHARS",
    "DEFAULT_MAX_WAITERS",
    "DEFAULT_PROBE_TEXT",
    "DEFAULT_STOP_GRACE_SECONDS",
    "DEFAULT_SYNTHESIS_TIMEOUT_SECONDS",
    "ERROR_AUDIO_TOO_LARGE",
    "ERROR_CLIP_UNREADABLE",
    "ERROR_INVALID_AUDIO",
    "ERROR_OUTPUT_NOT_ALLOWED",
    "ERROR_PLAYBACK_FAILED",
    "ERROR_SPEECH_TOO_LONG",
    "ERROR_TTS_FAILED",
    "ERROR_TTS_UNAVAILABLE",
    "ERROR_VOICE_NOT_ALLOWED",
    "MANIFEST_PATH",
    "MODULE_NAME",
    "PLAYBACK_COMPLETED",
    "PLAY_ACTION",
    "PROVIDER_NAME",
    "READ_CHUNK_BYTES",
    "SPEAK_ACTION",
    "WRITE_CHUNK_BYTES",
    "AudioOutputModule",
    "AudioOutputModuleError",
    "InvalidAudio",
    "SubprocessPlayerRunner",
    "WavInfo",
    "activate",
    "parse_wav",
    "validate_settings",
]
