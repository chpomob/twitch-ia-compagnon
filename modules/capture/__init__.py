"""The ``screen.capture`` action over configured sources (R5, R4).

The brain observes the stream through this module: one call, one image,
returned as a reference into the run-leased attachment store — never as
bytes in an event and never as a path on this machine. Which screen, window
or region the image shows is the business of the configured *source*: a
``file`` source is a path the module re-reads at each call (a capture tool
that keeps its last frame there, say), a ``command`` source is a program the
module runs with a fixed ``argv`` and whose stdout is the PNG or JPEG it
produced, bounded by ``command_timeout_seconds``. Nothing is captured in the
background: a capture is action-driven, requested by the brain, and refused
without an applicable rule with the provider never entered — no file is
read and no process is spawned (AC30, design v2 §4.2).

**What the observation carries.** The bytes are sniffed for their content
type and dimensions — the PNG ``IHDR`` chunk, the JPEG ``SOF`` marker — and
stored through ``context.attachments.put`` under the call's ``run_id``, so
the executor can check the reference it is handed back is leased to the
right run and unexpired (R4). The result names the source, the content type,
the width, the height, the stored size and the instant of acquisition on the
runtime clock, and the observation carries exactly one ``image_ref`` part
with the seven fields R4 lists and ``provider_id: "capture"``. No local path
appears in the result, the part, the provenance or any error: a source is
always named by its configured *name*.

**Bounds and failures, each an explicit outcome.** A capture heavier than
``max_bytes`` (default 5 242 880), or one the store cannot afford on any of
its own axes, is ``error attachment_refused`` with 0 bytes retained (AC23).
A command that has not finished within its ``command_timeout_seconds`` is
killed and the call is ``error capture_timed_out`` with 0 bytes stored
(AC29). Bytes that are not a PNG or JPEG with a readable header — an empty
capture, a truncated header, another format — are ``error invalid_result``:
a shape the executor would have rejected anyway, reported here rather than
raised. A source that cannot be read at all (a file that vanished, a program
that exited non-zero or could not be started) is ``error capture_failed``.
A ``source`` argument that names no configured source is ``error
invalid_arguments``.

**Readiness.** At ``prepare`` every configured source is checked — a
``file`` must be a readable file, a ``command`` must name an executable
resolved on ``PATH`` or by its own path — and the first that is not leaves
the module *not ready*: it is reported ``module.degraded`` naming the source
and ``prepare`` raises, so the startup names the source instead of offering
an action that cannot serve (AC29). Otherwise the provider is registered and
the module marked ready.

**Seams, for the tests.** ``_source_factory`` builds the source object of
each configured source (``modules/capture`` builds :class:`FileSource` and
:class:`CommandSource`; a harness hands in its own, such as the shared
``FakeCaptureSource``), ``_subprocess_runner`` replaces the coroutine that
spawns the configured program, and ``_sleeper`` the sleep the command
timeout is raced against, so a timeout is exercised on an injected clock
without a sleeping child. A source without a ``probe`` method is taken as
available at ``prepare``. These are read from *settings* and never from a
configuration file.

Lifecycle: the manifest declares no role, so the handle takes part in
``prepare`` and in ``close``, which withdraws readiness (R4).
"""

from __future__ import annotations

import asyncio
import inspect
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

from core.attachments import AttachmentRefused
from core.contracts import (
    BRAIN_ERROR_ATTACHMENT_REFUSED,
    BRAIN_ERROR_INVALID_RESULT,
    PART_TYPE_IMAGE_REF,
    ActionObservation,
    ActionSpec,
    Destination,
)


MODULE_NAME = "capture"

SCREEN_CAPTURE_ACTION = "screen.capture"
SCREEN_CAPTURE_PROVIDER = "capture"
"""Provider name in bindings and traces, and the ``provider_id`` of every part."""

MANIFEST_PATH = Path(__file__).with_name("module.yaml")

SOURCE_KIND_FILE = "file"
SOURCE_KIND_COMMAND = "command"
SOURCE_KINDS = frozenset({SOURCE_KIND_FILE, SOURCE_KIND_COMMAND})

DEFAULT_MAX_BYTES = 5_242_880
"""The byte bound of one capture when the settings name none (R5)."""

DEFAULT_COMMAND_TIMEOUT_SECONDS = 5.0
"""How long a ``command`` source may run when it declares no bound (R5)."""

CONTENT_TYPE_PNG = "image/png"
CONTENT_TYPE_JPEG = "image/jpeg"

ERROR_CAPTURE_TIMED_OUT = "capture_timed_out"
"""A command source outran ``command_timeout_seconds``; killed, 0 bytes stored."""

ERROR_CAPTURE_FAILED = "capture_failed"
"""A source could not be read at all: file unreadable, program failed to run."""

ERROR_ATTACHMENT_REFUSED = BRAIN_ERROR_ATTACHMENT_REFUSED
ERROR_INVALID_RESULT = BRAIN_ERROR_INVALID_RESULT
_ERROR_INVALID_ARGUMENTS = "invalid_arguments"
_ERROR_PROVIDER_CLOSED = "provider_closed"

#: The reserved setting the entry point hands its accepted ``limits`` block
#: over in (``core.main.LIMITS_KEY``). Accepted, never read.
_ACCEPTED_LIMITS_SETTING = "limits"

#: Settings this module reads; every other non-seam key is refused by name.
_SETTING_SOURCES = "sources"
_SETTING_DEFAULT_SOURCE = "default_source"
_SETTING_MAX_BYTES = "max_bytes"
_SETTINGS = frozenset({_SETTING_SOURCES, _SETTING_DEFAULT_SOURCE, _SETTING_MAX_BYTES})

#: The injection seams: read from *settings*, never from a configuration file.
_SEAM_SOURCE_FACTORY = "_source_factory"
_SEAM_SUBPROCESS_RUNNER = "_subprocess_runner"
_SEAM_SLEEPER = "_sleeper"
_SEAMS = frozenset({_SEAM_SOURCE_FACTORY, _SEAM_SUBPROCESS_RUNNER, _SEAM_SLEEPER})

#: How much a runner reads from the child's stdout at a time.
_READ_CHUNK = 65_536

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_PNG_HEADER_LENGTH = 24  # signature, IHDR length, "IHDR", width, height
_JPEG_SIGNATURE = b"\xff\xd8"
#: The start-of-frame markers carrying the dimensions (baseline, extended,
#: progressive, lossless, and their differential and arithmetic variants).
_JPEG_SOF_MARKERS = frozenset(
    {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
)
_JPEG_STANDALONE_MARKERS = frozenset({0x01, 0xD8, *range(0xD0, 0xD8)})
_JPEG_END_MARKERS = frozenset({0xD9, 0xDA})  # end of image, start of scan


class CaptureModuleError(RuntimeError):
    """A setup failure whose message contains no configured value."""


class CaptureError(Exception):
    """A source could not deliver a capture; the message names no path."""


class CaptureTimedOut(CaptureError):
    """A command source outran its ``command_timeout_seconds`` and was killed."""


class CaptureTooLarge(CaptureError):
    """A source produced more than ``max_bytes``; nothing was kept."""


# --------------------------------------------------------------------------- #
# Settings validation hook (R7)
# --------------------------------------------------------------------------- #


def validate_settings(settings: Any) -> list[str]:
    """Check this module's settings; return one diagnostic per offending field.

    This is the hook ``settings_validator`` in the manifest names. The loader
    runs it for every enabled module before any of them is activated (R7).
    Each diagnostic names the module and the field and nothing else — no
    path, no argv, no value is echoed; an empty list means the settings are
    accepted.

    ``sources`` must be a non-empty mapping of names to ``{kind: file, path}``
    or ``{kind: command, argv[], command_timeout_seconds?}`` shapes, ``argv``
    non-empty and every timeout a finite positive number; ``default_source``
    must name one of them; ``max_bytes`` when given must be a positive
    integer. The reserved ``limits`` block is accepted and not inspected;
    a seam must be callable; any other key is refused by name.
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
    for seam in _SEAMS:
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
    elif not isinstance(default, str) or not default.strip():
        diagnostics.append(
            _setting_diagnostic(_SETTING_DEFAULT_SOURCE, "must be a non-empty string")
        )
    elif names and default not in names:
        diagnostics.append(
            _setting_diagnostic(_SETTING_DEFAULT_SOURCE, "must name a configured source")
        )

    if _SETTING_MAX_BYTES in settings and not _is_positive_int(settings[_SETTING_MAX_BYTES]):
        diagnostics.append(_setting_diagnostic(_SETTING_MAX_BYTES, "must be a positive integer"))
    return diagnostics


def _source_diagnostics(field: str, entry: Any) -> list[str]:
    """The diagnostics of one ``sources.<name>`` entry, value-free."""

    if not isinstance(entry, Mapping):
        return [_setting_diagnostic(field, "must be a mapping")]
    kind = entry.get("kind")
    # ``isinstance`` first: an unhashable ``kind`` (a list, a mapping) must
    # be reported as a diagnostic, not raise from the membership test.
    if not isinstance(kind, str) or kind not in SOURCE_KINDS:
        return [_setting_diagnostic(f"{field}.kind", "must be 'file' or 'command'")]
    diagnostics: list[str] = []
    if kind == SOURCE_KIND_FILE:
        allowed = {"kind", "path"}
        if not _is_text(entry.get("path")):
            diagnostics.append(_setting_diagnostic(f"{field}.path", "must be a non-empty string"))
    else:
        allowed = {"kind", "argv", "command_timeout_seconds"}
        argv = entry.get("argv")
        if (
            not isinstance(argv, (list, tuple))
            or not argv
            or not all(_is_text(item) for item in argv)
        ):
            diagnostics.append(
                _setting_diagnostic(f"{field}.argv", "must be a non-empty list of strings")
            )
        if "command_timeout_seconds" in entry and not _is_positive_number(
            entry["command_timeout_seconds"]
        ):
            diagnostics.append(
                _setting_diagnostic(
                    f"{field}.command_timeout_seconds", "must be a finite positive number"
                )
            )
    for key in entry:
        if key not in allowed:
            diagnostics.append(
                _setting_diagnostic(f"{field}.{key}", "is not a field of this source kind")
            )
    return diagnostics


def _setting_diagnostic(field_name: str, reason: str) -> str:
    return f"module {MODULE_NAME!r}: field {field_name!r}: {reason}"


def _is_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def _is_positive_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value > 0
    )


@dataclass(frozen=True, slots=True)
class SourceSpec:
    """One configured source, as the settings declared it.

    ``path`` is set for a ``file`` source, ``argv`` and
    ``command_timeout_seconds`` for a ``command`` source. The spec is what
    the source factory receives; it is never rendered into a diagnostic,
    a trace or an observation.
    """

    name: str
    kind: str
    path: str | None = None
    argv: tuple[str, ...] = ()
    command_timeout_seconds: float = DEFAULT_COMMAND_TIMEOUT_SECONDS

    @classmethod
    def from_mapping(cls, name: str, entry: Mapping[str, Any]) -> "SourceSpec":
        if entry["kind"] == SOURCE_KIND_FILE:
            return cls(name=name, kind=SOURCE_KIND_FILE, path=str(entry["path"]))
        return cls(
            name=name,
            kind=SOURCE_KIND_COMMAND,
            argv=tuple(str(item) for item in entry["argv"]),
            command_timeout_seconds=float(
                entry.get("command_timeout_seconds", DEFAULT_COMMAND_TIMEOUT_SECONDS)
            ),
        )


@dataclass(frozen=True, slots=True)
class _Settings:
    """The accepted settings, parsed once."""

    sources: Mapping[str, SourceSpec]
    default_source: str
    max_bytes: int

    @classmethod
    def from_mapping(cls, settings: Mapping[str, Any]) -> "_Settings":
        return cls(
            sources={
                name: SourceSpec.from_mapping(name, entry)
                for name, entry in settings[_SETTING_SOURCES].items()
            },
            default_source=str(settings[_SETTING_DEFAULT_SOURCE]),
            max_bytes=int(settings.get(_SETTING_MAX_BYTES, DEFAULT_MAX_BYTES)),
        )


# --------------------------------------------------------------------------- #
# Header sniffing (R4): content type and dimensions from the bytes
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class ImageHeader:
    """What the leading bytes of a capture say it is."""

    content_type: str
    width: int
    height: int


def sniff_image(data: bytes) -> ImageHeader | None:
    """Read the content type and dimensions from *data*, or ``None``.

    A PNG is recognised by its signature and its ``IHDR`` chunk (width and
    height, big-endian); a JPEG by its ``SOI`` marker followed by a walk of
    the marker segments up to the first start-of-frame, which carries the
    height then the width. Anything else — another format, an empty buffer,
    a header cut short, a zero dimension, a scan that starts before any
    frame header — is ``None``, never an exception: the provider reports it
    as an ``invalid_result`` error rather than failing.
    """

    if data.startswith(_PNG_SIGNATURE):
        return _sniff_png(data)
    if data.startswith(_JPEG_SIGNATURE):
        return _sniff_jpeg(data)
    return None


def _sniff_png(data: bytes) -> ImageHeader | None:
    if len(data) < _PNG_HEADER_LENGTH or data[12:16] != b"IHDR":
        return None
    width, height = struct.unpack(">II", data[16:24])
    if width <= 0 or height <= 0:
        return None
    return ImageHeader(CONTENT_TYPE_PNG, width, height)


def _sniff_jpeg(data: bytes) -> ImageHeader | None:
    offset = 2
    length = len(data)
    while offset + 2 <= length:
        if data[offset] != 0xFF:
            return None
        marker = data[offset + 1]
        if marker == 0xFF:
            # Fill bytes may precede a marker.
            offset += 1
            continue
        if marker in _JPEG_STANDALONE_MARKERS:
            offset += 2
            continue
        if marker in _JPEG_END_MARKERS:
            return None
        if offset + 4 > length:
            return None
        (segment,) = struct.unpack(">H", data[offset + 2 : offset + 4])
        if segment < 2:
            return None
        if marker in _JPEG_SOF_MARKERS:
            if offset + 9 > length or segment < 7:
                return None
            height, width = struct.unpack(">HH", data[offset + 5 : offset + 9])
            if width <= 0 or height <= 0:
                return None
            return ImageHeader(CONTENT_TYPE_JPEG, width, height)
        offset += 2 + segment
    return None


# --------------------------------------------------------------------------- #
# Sources (R5)
# --------------------------------------------------------------------------- #

#: The runner seam: ``runner(argv, limit=n)`` awaits to ``(returncode,
#: stdout)``, where stdout holds at most ``limit + 1`` bytes — a child that
#: writes more is killed, so a buffer above the limit is the whole signal.
SubprocessRunner = Callable[..., Awaitable[tuple[int | None, bytes]]]
Sleeper = Callable[[float], Awaitable[Any]]


class FileSource:
    """A ``file`` source: the path is re-read afresh at each call."""

    __slots__ = ("_path", "name")

    def __init__(self, spec: SourceSpec) -> None:
        if spec.kind != SOURCE_KIND_FILE or spec.path is None:
            raise CaptureModuleError(f"{MODULE_NAME}: source {spec.name!r} is not a file source")
        self.name = spec.name
        self._path = spec.path

    def probe(self) -> str | None:
        """Why the source is unavailable now, or ``None`` when it is readable."""

        if not os.path.isfile(self._path) or not os.access(self._path, os.R_OK):
            return "is not a readable file"
        return None

    async def read(self, *, max_bytes: int) -> bytes:
        """The file's bytes now, read off the loop; above *max_bytes* is refused."""

        path = self._path
        limit = int(max_bytes)

        def _read() -> bytes:
            with open(path, "rb") as handle:
                return handle.read(limit + 1)

        try:
            data = await asyncio.to_thread(_read)
        except OSError:
            raise CaptureError("file could not be read") from None
        if len(data) > limit:
            raise CaptureTooLarge("file exceeds max_bytes")
        return data


class CommandSource:
    """A ``command`` source: run ``argv``, take stdout, bounded in time and bytes.

    The run is raced against the injected *sleeper* for
    ``command_timeout_seconds``: when the timer wins the runner task is
    cancelled — which kills the child — and :class:`CaptureTimedOut` is
    raised with nothing kept. A cancellation from above (the executor's own
    timeout or a run cancellation) cancels both and propagates.
    """

    __slots__ = ("_argv", "_runner", "_sleeper", "_timeout", "name")

    def __init__(self, spec: SourceSpec, *, runner: SubprocessRunner, sleeper: Sleeper) -> None:
        if spec.kind != SOURCE_KIND_COMMAND or not spec.argv:
            raise CaptureModuleError(
                f"{MODULE_NAME}: source {spec.name!r} is not a command source"
            )
        self.name = spec.name
        self._argv = spec.argv
        self._timeout = float(spec.command_timeout_seconds)
        self._runner = runner
        self._sleeper = sleeper

    def probe(self) -> str | None:
        """Why the program cannot run, or ``None`` when it resolves to an executable."""

        # ``which`` checks a name on ``PATH`` and a path (absolute or with a
        # directory component) directly, for existence and execute permission.
        if shutil.which(self._argv[0]) is None:
            return "does not resolve to an executable"
        return None

    async def read(self, *, max_bytes: int) -> bytes:
        limit = int(max_bytes)
        runner_task = asyncio.ensure_future(self._runner(self._argv, limit=limit))
        timer_task = asyncio.ensure_future(self._sleeper(self._timeout))
        try:
            done, _pending = await asyncio.wait(
                {runner_task, timer_task}, return_when=asyncio.FIRST_COMPLETED
            )
        except asyncio.CancelledError:
            await _settle(runner_task)
            await _settle(timer_task)
            raise
        if runner_task not in done:
            # The timer won: the runner's cancellation kills the child.
            await _settle(runner_task)
            await _settle(timer_task)
            raise CaptureTimedOut("command exceeded command_timeout_seconds")
        await _settle(timer_task)
        if runner_task.cancelled():
            raise CaptureError("command was interrupted")
        failure = runner_task.exception()
        if failure is not None:
            raise CaptureError("command could not be run") from None
        returncode, data = runner_task.result()
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise CaptureError("command runner produced no bytes")
        data = bytes(data)
        if len(data) > limit:
            raise CaptureTooLarge("command output exceeds max_bytes")
        if returncode != 0:
            raise CaptureError("command exited with a non-zero status")
        return data


async def run_command(
    argv: Sequence[str],
    *,
    limit: int,
    spawn: Callable[..., Awaitable[asyncio.subprocess.Process]] = asyncio.create_subprocess_exec,
) -> tuple[int | None, bytes]:
    """The default runner: spawn *argv*, read stdout up to *limit* + 1 bytes.

    stdin is closed and stderr discarded, so nothing the program prints —
    a path, a diagnostic — ever reaches a trace. A child that writes past
    the limit is killed and its exit status returned as it is; the caller
    reads the oversize buffer as the refusal. A cancellation kills the child
    before propagating, so no process outlives the call that spawned it.

    The program is started in its own session, hence its own isolated
    process group (``start_new_session``), and a kill is delivered to the
    whole group by :func:`_kill`: a configured wrapper script that launches
    the real capture tool does not leave that tool running past a timeout —
    nor holding the inherited stdout open, which would keep the wait on the
    pipe from returning. *spawn* is the ``create_subprocess_exec`` seam, for
    tests that stand in a transport of their own.
    """

    process = await spawn(
        *argv,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        start_new_session=True,
    )
    assert process.stdout is not None  # stdout=PIPE above
    chunks = bytearray()
    try:
        while len(chunks) <= limit:
            chunk = await process.stdout.read(_READ_CHUNK)
            if not chunk:
                break
            chunks += chunk
        if len(chunks) > limit:
            await _kill(process)
        else:
            await process.wait()
    except BaseException:
        await _kill(process)
        raise
    return process.returncode, bytes(chunks)


async def _kill(process: asyncio.subprocess.Process) -> None:
    """Terminate *process* and every descendant in its process group, then reap it.

    The group is the session :func:`run_command` started for the process,
    so its id is the process's own pid. It is signalled whether or not the
    process itself has already exited: a wrapper script that returned while
    its background child kept running would otherwise leave that child
    behind. The stdout pipe is then closed *before* the wait, so the wait
    ends when the process is reaped — not when the last holder of the
    inherited write end lets go of it, which a descendant the signal could
    not reach (one that started a session of its own, say) might never do.
    That keeps a timed-out capture within ``command_timeout_seconds`` and a
    cancelled one prompt. On a platform without ``killpg`` (or when the
    group is already gone) the process alone is killed.
    """

    _terminate_group(process)
    _close_stdout(process)
    await process.wait()


def _terminate_group(process: asyncio.subprocess.Process) -> None:
    killpg = getattr(os, "killpg", None)
    if killpg is not None:
        try:
            killpg(process.pid, signal.SIGKILL)
            return
        except OSError:
            # The group is already gone, or the signal could not be
            # delivered to it as a whole: fall back to the process alone.
            pass
    if process.returncode is None:
        try:
            process.kill()
        except ProcessLookupError:
            pass


def _close_stdout(process: asyncio.subprocess.Process) -> None:
    # asyncio exposes no public handle on a subprocess's pipe transports:
    # the subprocess transport is reached through ``Process._transport``,
    # whose ``get_pipe_transport`` is public. Absent either (another loop
    # implementation), the wait that follows depends on the pipe closing.
    transport = getattr(process, "_transport", None)
    get_pipe_transport = getattr(transport, "get_pipe_transport", None)
    pipe = get_pipe_transport(1) if callable(get_pipe_transport) else None
    if pipe is not None:
        pipe.close()


async def _settle(task: "asyncio.Future[Any]") -> None:
    """Cancel *task* if still running and wait for it, consuming its outcome."""

    if not task.done():
        task.cancel()
    await asyncio.wait({task})
    if not task.cancelled():
        task.exception()


def default_source_factory(
    spec: SourceSpec, *, runner: SubprocessRunner, sleeper: Sleeper
) -> FileSource | CommandSource:
    """Build the shipped source object for *spec*."""

    if spec.kind == SOURCE_KIND_FILE:
        return FileSource(spec)
    return CommandSource(spec, runner=runner, sleeper=sleeper)


# --------------------------------------------------------------------------- #
# The handle
# --------------------------------------------------------------------------- #


class _ScreenCaptureProvider:
    """The ``screen.capture`` provider the executor invokes (R5)."""

    __slots__ = ("_module",)

    name = SCREEN_CAPTURE_PROVIDER

    def __init__(self, module: "CaptureModule") -> None:
        self._module = module

    async def invoke(self, invocation: Any) -> ActionObservation:
        return await self._module._invoke_screen_capture(invocation)


class CaptureModule:
    """The v2 handle: check the sources at ``prepare``, capture on demand."""

    def __init__(
        self,
        context: Any,
        settings: _Settings,
        sources: Mapping[str, Any],
    ) -> None:
        self._actions = context.actions
        self._attachments = context.attachments
        self._clock = context.clock
        self._supervision = getattr(context, "supervision", None)
        self._settings = settings
        self._sources = dict(sources)
        self._provider = _ScreenCaptureProvider(self)
        self._prepared = False
        self._closed = False

    @property
    def sources(self) -> Mapping[str, Any]:
        """The source objects by name, for inspection."""

        return dict(self._sources)

    @property
    def max_bytes(self) -> int:
        return self._settings.max_bytes

    # -- lifecycle hooks (R4) ----------------------------------------------- #

    async def prepare(self) -> None:
        """Check every source, bind the provider, mark ready — or degrade.

        The first source that is unavailable is reported ``module.degraded``
        naming it and ``prepare`` raises with the same value-free
        diagnostic: nothing is bound and the module is not marked ready, so
        the action is never offered by a module that cannot serve it (AC29).
        """

        if self._prepared or self._closed:
            return
        for name in self._settings.sources:
            reason = _probe(self._sources[name])
            if reason is not None:
                diagnostic = f"{MODULE_NAME} prepare: source {name!r} {reason}"
                await self._report_degraded(diagnostic)
                raise CaptureModuleError(diagnostic)
        spec = _declared_screen_capture_spec()
        try:
            self._actions.register(spec, self._provider, provider_name=SCREEN_CAPTURE_PROVIDER)
        except Exception:
            raise CaptureModuleError(f"{MODULE_NAME} prepare: action binding failed") from None
        self._actions.mark_ready()
        self._prepared = True

    async def close(self) -> None:
        """Withdraw readiness; a later call is refused before the provider."""

        if self._closed:
            return
        self._closed = True
        if self._prepared:
            self._actions.mark_not_ready()

    async def _report_degraded(self, reason: str) -> None:
        degraded = getattr(self._supervision, "degraded", None)
        if not callable(degraded):
            return
        try:
            outcome = degraded(reason=reason)
            if inspect.isawaitable(outcome):
                await outcome
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a health report must not mask the failure
            return

    # -- the capture (R5, R4, AC23, AC29) ----------------------------------- #

    async def _invoke_screen_capture(self, invocation: Any) -> ActionObservation:
        """Serve one ``screen.capture`` call for the executor."""

        # A capture reads a screen; nothing reaches the outside world, so an
        # interruption is a certain ``timeout``/``cancelled``.
        invocation.mark_not_emitted()
        call = invocation.call
        destination = call.destination
        provenance: dict[str, Any] = {
            "provider": SCREEN_CAPTURE_PROVIDER,
            "platform": destination.platform,
            "channel_id": destination.channel_id,
            "route": SCREEN_CAPTURE_ACTION,
        }

        def failure(code: str, message: str) -> ActionObservation:
            return ActionObservation(
                status="error",
                provenance=provenance,
                error={"code": code, "message": message, "retryable": False},
            )

        if self._closed:
            return failure(_ERROR_PROVIDER_CLOSED, f"{MODULE_NAME} capture: closed")

        requested = call.arguments.get("source")
        name = self._settings.default_source if requested is None else requested
        if not isinstance(name, str) or name not in self._sources:
            return failure(_ERROR_INVALID_ARGUMENTS, "source is not one this module configures")
        provenance["source"] = name
        source = self._sources[name]
        max_bytes = self._settings.max_bytes

        try:
            data = await source.read(max_bytes=max_bytes)
        except asyncio.CancelledError:
            raise
        except CaptureTimedOut:
            return failure(
                ERROR_CAPTURE_TIMED_OUT,
                f"source {name!r} did not complete within command_timeout_seconds",
            )
        except CaptureTooLarge:
            return failure(
                ERROR_ATTACHMENT_REFUSED, f"source {name!r} produced more than max_bytes"
            )
        except Exception:  # noqa: BLE001 - every source failure is one explicit outcome
            return failure(ERROR_CAPTURE_FAILED, f"source {name!r} could not be read")
        captured_at = float(self._clock())

        if not isinstance(data, (bytes, bytearray, memoryview)):
            return failure(ERROR_INVALID_RESULT, f"source {name!r} produced no bytes")
        data = bytes(data)
        if len(data) > max_bytes:
            return failure(
                ERROR_ATTACHMENT_REFUSED, f"source {name!r} produced more than max_bytes"
            )
        header = sniff_image(data)
        if header is None:
            return failure(
                ERROR_INVALID_RESULT,
                f"source {name!r} produced no PNG or JPEG with a readable header",
            )

        try:
            ref = self._attachments.put(call.run_id, data, content_type=header.content_type)
        except AttachmentRefused as refused:
            return failure(
                ERROR_ATTACHMENT_REFUSED,
                f"attachment store refused the capture ({refused.limit})",
            )

        result = {
            "source": name,
            "content_type": header.content_type,
            "width": header.width,
            "height": header.height,
            "size": ref.size,
            "captured_at": captured_at,
        }
        part = {
            "type": PART_TYPE_IMAGE_REF,
            "attachment_id": ref.attachment_id,
            "content_type": header.content_type,
            "size": ref.size,
            "width": header.width,
            "height": header.height,
            "captured_at": captured_at,
            "provider_id": SCREEN_CAPTURE_PROVIDER,
        }
        return ActionObservation(
            status="success", provenance=provenance, result=result, parts=(part,)
        )


def _probe(source: Any) -> str | None:
    """Why *source* is unavailable, or ``None``; a source without ``probe`` is available."""

    probe = getattr(source, "probe", None)
    if not callable(probe):
        return None
    try:
        reason = probe()
    except Exception:  # noqa: BLE001 - a probe that fails is an unavailable source
        return "could not be checked"
    return reason if isinstance(reason, str) and reason else None


# --------------------------------------------------------------------------- #
# Activation (R7)
# --------------------------------------------------------------------------- #


async def activate(
    context: Any,
    settings: Mapping[str, Any],
    catalog: Mapping[str, Mapping[str, Any]],
) -> CaptureModule:
    """Build the handle from the scoped runtime context (R7).

    *context* is the module-scoped view of the versioned runtime: the action
    facade the provider is registered on at ``prepare``, the attachment
    store every capture is leased into, the clock every instant is stamped
    with and the supervision surface a degraded source is reported on.
    Settings are checked through the same hook the loader ran, so a handle
    built outside the loader is refused on the same terms. The seams
    ``_source_factory``, ``_subprocess_runner`` and ``_sleeper`` are read
    from *settings* and default to the shipped sources, the real subprocess
    runner and ``asyncio.sleep``.
    """

    del catalog  # Capabilities are declared by the colocated manifest.
    _require_runtime_surfaces(context)
    if not isinstance(settings, Mapping):
        raise CaptureModuleError(f"{MODULE_NAME} configuration: settings must be a mapping")
    diagnostics = validate_settings(settings)
    if diagnostics:
        raise CaptureModuleError(
            f"{MODULE_NAME} configuration: settings were refused "
            f"({len(diagnostics)} diagnostics)"
        )
    parsed = _Settings.from_mapping(settings)
    runner = settings.get(_SEAM_SUBPROCESS_RUNNER, run_command)
    sleeper = settings.get(_SEAM_SLEEPER, asyncio.sleep)
    factory = settings.get(_SEAM_SOURCE_FACTORY)
    sources: dict[str, Any] = {}
    for name, spec in parsed.sources.items():
        try:
            if factory is None:
                built = default_source_factory(spec, runner=runner, sleeper=sleeper)
            else:
                built = factory(spec)
            if inspect.isawaitable(built):
                built = await built
        except CaptureModuleError:
            raise
        except Exception:
            raise CaptureModuleError(
                f"{MODULE_NAME} configuration: source {name!r} could not be built"
            ) from None
        if not callable(getattr(built, "read", None)):
            raise CaptureModuleError(
                f"{MODULE_NAME} configuration: source {name!r} has no read method"
            )
        sources[name] = built
    return CaptureModule(context, parsed, sources)


def _require_runtime_surfaces(context: Any) -> None:
    """Refuse a context lacking the actions facade, a store or a clock.

    Duck-typed so a harness may inject fakes, while a mis-wired runtime is
    refused at activation with one diagnostic rather than at the first
    capture. Without a store no ``image_ref`` can be leased, so a context
    carrying none is refused here.
    """

    actions = getattr(context, "actions", None)
    attachments = getattr(context, "attachments", None)
    clock = getattr(context, "clock", None)
    if (
        actions is None
        or not all(
            callable(getattr(actions, method, None))
            for method in ("register", "mark_ready", "mark_not_ready")
        )
        or attachments is None
        or not callable(getattr(attachments, "put", None))
        or not callable(clock)
    ):
        raise CaptureModuleError(f"{MODULE_NAME} activation: runtime context is invalid")


def _declared_screen_capture_spec() -> ActionSpec:
    """Build the ``screen.capture`` contract from the colocated manifest (R5).

    The spec registered at ``prepare`` must equal the one the loader declared
    at discovery, field for field: reading the same file keeps the two from
    drifting, and the registry refuses a redeclaration that differs.
    """

    try:
        manifest = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))
        entry = next(
            item
            for item in manifest["actions"]
            if isinstance(item, Mapping) and item.get("name") == SCREEN_CAPTURE_ACTION
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
        raise CaptureModuleError(
            f"{MODULE_NAME} prepare: manifest declaration of {SCREEN_CAPTURE_ACTION!r} is invalid"
        ) from None


__all__ = [
    "CONTENT_TYPE_JPEG",
    "CONTENT_TYPE_PNG",
    "DEFAULT_COMMAND_TIMEOUT_SECONDS",
    "DEFAULT_MAX_BYTES",
    "ERROR_ATTACHMENT_REFUSED",
    "ERROR_CAPTURE_FAILED",
    "ERROR_CAPTURE_TIMED_OUT",
    "ERROR_INVALID_RESULT",
    "MANIFEST_PATH",
    "MODULE_NAME",
    "SCREEN_CAPTURE_ACTION",
    "SCREEN_CAPTURE_PROVIDER",
    "SOURCE_KINDS",
    "SOURCE_KIND_COMMAND",
    "SOURCE_KIND_FILE",
    "CaptureError",
    "CaptureModule",
    "CaptureModuleError",
    "CaptureTimedOut",
    "CaptureTooLarge",
    "CommandSource",
    "FileSource",
    "ImageHeader",
    "SourceSpec",
    "activate",
    "default_source_factory",
    "run_command",
    "sniff_image",
    "validate_settings",
]
