"""``modules/capture``: ``screen.capture`` from ``file`` and ``command`` sources (R5, R4).

AC29 — a ``file`` source returns one ``image_ref`` part whose ``width``,
``height`` and ``size`` match the file (re-read at each call); a ``command``
source whose process outruns ``command_timeout_seconds`` is ``error
capture_timed_out`` with 0 bytes stored, the timeout judged on the injected
clock with the runner held on a future — no sleeping child; a source missing
at ``prepare`` leaves the module not ready and ``module.degraded`` names the
source. AC23 — with ``max_object_bytes: 1024`` a 2048-byte capture is
``error attachment_refused`` and the store retains 0 bytes for the run; the
module's own ``max_bytes`` refuses on the same code before the store is
reached. AC30 — without an applicable rule the call is ``refused
not_authorized`` with the provider entered 0 times and no source touched, on
two platforms. An unknown ``source`` argument is ``invalid_arguments``; no
observation, part, provenance or trace carries a filesystem path; a
``command`` source is exercised end to end with a Python one-liner writing a
PNG to stdout; truncated or foreign bytes are ``invalid_result`` errors
rather than exceptions.

Every capture goes through the real executor built by ``runtime_context``
with a real :class:`AttachmentStore` on the same clock — authorization,
argument schema, result schema, parts validation and the lease check are the
executor's, not mocked.
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import struct
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest
import yaml

from core.actions import (
    ERROR_INVALID_ARGUMENTS,
    ERROR_NOT_AUTHORIZED,
    AuthorizationRule,
)
from core.attachments import AttachmentStore
from core.contracts import (
    IMAGE_REF_FIELDS,
    TRACE_MODULE_DEGRADED,
    ActionCall,
    ActionObservation,
    Destination,
)
from core.lifecycle import PhaseCoordinator
from core.loader import ModuleLoader
from core.runtime import RUNTIME_API, ModuleContext, RuntimeContext
from conftest import (
    FakeCaptureSource,
    ManualClock,
    events_of,
    png_bytes,
    runtime_context,
    settle,
    wait_until,
)

from modules.capture import (
    CONTENT_TYPE_JPEG,
    CONTENT_TYPE_PNG,
    DEFAULT_COMMAND_TIMEOUT_SECONDS,
    DEFAULT_MAX_BYTES,
    ERROR_ATTACHMENT_REFUSED,
    ERROR_CAPTURE_FAILED,
    ERROR_CAPTURE_TIMED_OUT,
    ERROR_INVALID_RESULT,
    MANIFEST_PATH,
    MODULE_NAME,
    SCREEN_CAPTURE_ACTION,
    SCREEN_CAPTURE_PROVIDER,
    CaptureModule,
    CaptureModuleError,
    CaptureTimedOut,
    CommandSource,
    FileSource,
    ImageHeader,
    SourceSpec,
    activate,
    run_command,
    sniff_image,
    validate_settings,
)


ROOT = Path(__file__).parents[1]
MODULE_DIR = ROOT / "modules" / "capture"

PRINCIPAL = "brain"
RUN_ID = "run-1"

TWITCH_CAPTURE = Destination("twitch", "channel-1", "capture")
FAKE_CAPTURE = Destination("fake", "channel-9", "capture")

STORE_LIMITS: dict[str, Any] = {
    "max_object_bytes": 1024 * 1024,
    "max_objects": 8,
    "max_total_bytes": 4 * 1024 * 1024,
    "max_bytes_per_run": 2 * 1024 * 1024,
    "ttl_seconds": 30.0,
}

#: The accepted ``limits`` block as the entry point hands it to every enabled
#: module: accepted by this module's schema and hook, never read.
LIMITS: dict[str, Any] = {
    "attachments": dict(STORE_LIMITS),
}

#: The one-liner a ``command`` source runs in the end-to-end test: it writes
#: the PNG it is handed (as hex) to stdout and nothing else.
PNG_WRITER = "import sys; sys.stdout.buffer.write(bytes.fromhex(sys.argv[1]))"


# --------------------------------------------------------------------------- #
# Harness
# --------------------------------------------------------------------------- #


def jpeg_bytes(width: int, height: int) -> bytes:
    """A JPEG header — SOI, an APP0 segment, a baseline SOF0, EOI — enough
    to sniff; the dimensions sit in the frame header, height first."""

    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00" + b"\x01\x01\x00" + b"\x00\x01\x00\x01\x00\x00"
    sof0 = (
        b"\xff\xc0"
        + struct.pack(">H", 17)
        + b"\x08"
        + struct.pack(">HH", height, width)
        + b"\x03\x01\x22\x00\x02\x11\x01\x03\x11\x01"
    )
    return b"\xff\xd8" + app0 + sof0 + b"\xff\xd9"


def file_settings(path: Path, **extra: Any) -> dict[str, Any]:
    return {
        "sources": {"screen": {"kind": "file", "path": str(path)}},
        "default_source": "screen",
        **extra,
    }


def command_settings(argv: list[str], *, timeout: float | None = None, **extra: Any) -> dict[str, Any]:
    source: dict[str, Any] = {"kind": "command", "argv": list(argv)}
    if timeout is not None:
        source["command_timeout_seconds"] = timeout
    return {"sources": {"tool": source}, "default_source": "tool", **extra}


def fake_settings(source: FakeCaptureSource, **extra: Any) -> dict[str, Any]:
    """A file source whose object is the shared fake: the path is never read."""

    return {
        "sources": {"fake": {"kind": "file", "path": "/nonexistent/never-read.png"}},
        "default_source": "fake",
        "_source_factory": lambda spec: source,
        **extra,
    }


def module_context(
    clock: ManualClock | None = None, *, store_limits: dict[str, Any] | None = None
) -> ModuleContext:
    target = clock if clock is not None else ManualClock()
    store = AttachmentStore(clock=target, **(store_limits or STORE_LIMITS))
    return runtime_context(clock=target, attachments=store).for_module(MODULE_NAME)


def runtime_of(context: ModuleContext) -> RuntimeContext:
    return context._runtime


def store_of(context: ModuleContext) -> AttachmentStore:
    return runtime_of(context).attachments


def grant_capture(context: ModuleContext, destination: Destination | None = None) -> None:
    rule: dict[str, Any] = {
        "rule_id": "grant-screen-capture",
        "action_name": SCREEN_CAPTURE_ACTION,
        "granted_permissions": ("screen.capture",),
    }
    if destination is not None:
        rule["destination"] = destination
    runtime_of(context).actions._authorization.grant(AuthorizationRule(**rule))


async def prepared_module(context: ModuleContext, settings: dict[str, Any]) -> CaptureModule:
    handle = await activate(context, settings, {})
    await handle.prepare()
    return handle


def capture_call(
    destination: Destination,
    arguments: dict[str, Any] | None = None,
    call_id: str = "call-1",
    run_id: str = RUN_ID,
) -> ActionCall:
    return ActionCall(
        action_name=SCREEN_CAPTURE_ACTION,
        action_version=1,
        arguments=arguments if arguments is not None else {},
        conversation_id="conversation-1",
        run_id=run_id,
        call_id=call_id,
        source_event_id="source-1",
        destination=destination,
        principal=PRINCIPAL,
        deadline=10_000.0,
        message_id="source-1",
    )


async def capture(
    context: ModuleContext,
    destination: Destination = TWITCH_CAPTURE,
    arguments: dict[str, Any] | None = None,
    call_id: str = "call-1",
) -> ActionObservation:
    return await runtime_of(context).executor.invoke(
        capture_call(destination, arguments, call_id)
    )


def image_part(observation: ActionObservation) -> dict[str, Any]:
    assert observation.status == "success", observation.error
    assert observation.result is not None
    (part,) = observation.parts
    assert part["type"] == "image_ref"
    assert set(part) == {"type", *IMAGE_REF_FIELDS}
    return dict(part)


def assert_error(observation: ActionObservation, code: str) -> None:
    assert observation.status == "error", observation
    assert observation.error is not None
    assert observation.error["code"] == code, observation.error
    assert observation.result is None
    assert observation.parts == ()


def assert_nothing_retained(context: ModuleContext, run_id: str = RUN_ID) -> None:
    store = store_of(context)
    assert store.usage(run_id).total_bytes == 0
    assert store.usage(run_id).objects == 0
    assert store.object_count == 0


def held_runner(loop: asyncio.AbstractEventLoop) -> tuple[Any, "asyncio.Future[Any]", asyncio.Event, list[Any]]:
    """A runner seam parked on a future the test controls, recording its calls."""

    held: asyncio.Future[Any] = loop.create_future()
    entered = asyncio.Event()
    calls: list[Any] = []

    async def runner(argv: Any, *, limit: int) -> Any:
        calls.append((tuple(argv), limit))
        entered.set()
        return await held

    return runner, held, entered, calls


# --------------------------------------------------------------------------- #
# AC29: file source → image_ref matching the file
# --------------------------------------------------------------------------- #


async def test_file_source_returns_one_image_ref_matching_the_file(tmp_path: Path) -> None:
    """AC29: a ``file`` source yields ``success`` with exactly one
    ``image_ref`` whose ``width``, ``height`` and ``size`` are the file's,
    ``content_type`` sniffed from the bytes, ``captured_at`` the injected
    clock, ``provider_id: "capture"``, an attachment the store leases to
    the call's run at that size — and the result mapping states the same."""

    image = png_bytes(64, 48)
    path = tmp_path / "frame.png"
    path.write_bytes(image)
    clock = ManualClock(500.0)
    context = module_context(clock)
    handle = await prepared_module(context, file_settings(path))
    grant_capture(context)
    try:
        observation = await capture(context)

        part = image_part(observation)
        assert (part["width"], part["height"], part["size"]) == (64, 48, len(image))
        assert part["content_type"] == CONTENT_TYPE_PNG
        assert part["captured_at"] == 500.0
        assert part["provider_id"] == SCREEN_CAPTURE_PROVIDER == "capture"
        assert observation.result == {
            "source": "screen",
            "content_type": CONTENT_TYPE_PNG,
            "width": 64,
            "height": 48,
            "size": len(image),
            "captured_at": 500.0,
        }
        ref = store_of(context).lookup(part["attachment_id"])
        assert ref is not None
        assert (ref.run_id, ref.size, ref.content_type) == (RUN_ID, len(image), CONTENT_TYPE_PNG)
        assert store_of(context).get(ref) == image
        assert store_of(context).usage(RUN_ID).objects == 1
        assert observation.provenance["provider"] == SCREEN_CAPTURE_PROVIDER
        assert observation.provenance["source"] == "screen"
    finally:
        await handle.close()


async def test_file_source_is_re_read_at_each_call_and_a_jpeg_is_sniffed(tmp_path: Path) -> None:
    """R5: the file is read afresh — a second call after the file changed
    reports the new dimensions and size; a JPEG is recognised by its frame
    header, dimensions and content type included."""

    path = tmp_path / "frame.img"
    path.write_bytes(png_bytes(8, 4))
    context = module_context()
    handle = await prepared_module(context, file_settings(path))
    grant_capture(context)
    try:
        first = image_part(await capture(context, call_id="call-1"))
        jpeg = jpeg_bytes(320, 200)
        path.write_bytes(jpeg)
        second = image_part(await capture(context, call_id="call-2"))

        assert (first["width"], first["height"], first["content_type"]) == (8, 4, CONTENT_TYPE_PNG)
        assert (second["width"], second["height"], second["size"]) == (320, 200, len(jpeg))
        assert second["content_type"] == CONTENT_TYPE_JPEG
        assert first["attachment_id"] != second["attachment_id"]
        assert store_of(context).usage(RUN_ID).objects == 2
    finally:
        await handle.close()


async def test_no_observation_part_provenance_or_trace_carries_a_filesystem_path(
    tmp_path: Path,
) -> None:
    """R4/AC29: the file's path appears nowhere — not in the result, the
    part, the provenance, the error of a failed read, nor in any trace the
    executor published; the source is named by its configured name only."""

    path = tmp_path / "secret-location.png"
    path.write_bytes(png_bytes(2, 2))
    context = module_context()
    handle = await prepared_module(context, file_settings(path))
    grant_capture(context)
    try:
        success = await capture(context, call_id="call-1")
        path.unlink()
        failed = await capture(context, call_id="call-2")

        image_part(success)
        assert_error(failed, ERROR_CAPTURE_FAILED)
        for observation in (success, failed):
            rendered = repr(observation)
            assert str(tmp_path) not in rendered
            assert "secret-location" not in rendered
        for event in runtime_of(context).bus.list_events():
            rendered = repr(event)
            assert str(tmp_path) not in rendered
            assert "secret-location" not in rendered
        assert failed.error["message"] == "source 'screen' could not be read"
    finally:
        await handle.close()


# --------------------------------------------------------------------------- #
# AC29: command source, timeout on the injected clock, no sleeping child
# --------------------------------------------------------------------------- #


async def test_command_source_beyond_its_timeout_is_capture_timed_out_with_0_bytes_stored() -> None:
    """AC29: the runner seam is held on a future; advancing the injected
    clock past ``command_timeout_seconds`` fires the timer, the runner is
    cancelled (the kill), the call is ``error capture_timed_out``, the store
    retains 0 bytes and no child ever slept."""

    clock = ManualClock()
    context = module_context(clock)
    runner, held, entered, calls = held_runner(asyncio.get_running_loop())
    settings = command_settings(
        [sys.executable, "-c", "pass"],
        timeout=5.0,
        _subprocess_runner=runner,
        _sleeper=clock.sleep,
    )
    handle = await prepared_module(context, settings)
    grant_capture(context)
    try:
        pending = asyncio.ensure_future(capture(context))
        await entered.wait()
        await settle()
        assert not pending.done()
        assert calls == [((sys.executable, "-c", "pass"), DEFAULT_MAX_BYTES)]

        clock.advance(4.0)
        await settle()
        assert not pending.done()
        clock.advance(1.0)
        observation = await pending

        assert_error(observation, ERROR_CAPTURE_TIMED_OUT)
        assert held.cancelled()
        assert_nothing_retained(context)
        assert runtime_of(context).executor.provider_invocations == 1
        assert observation.error["message"] == (
            "source 'tool' did not complete within command_timeout_seconds"
        )
    finally:
        await handle.close()


async def test_command_timeout_defaults_to_5_seconds() -> None:
    """R5: a ``command`` source declaring no ``command_timeout_seconds`` is
    bounded at 5 — the timer fires at exactly that on the injected clock."""

    clock = ManualClock()
    context = module_context(clock)
    runner, held, entered, _calls = held_runner(asyncio.get_running_loop())
    settings = command_settings(
        [sys.executable, "-c", "pass"], _subprocess_runner=runner, _sleeper=clock.sleep
    )
    handle = await prepared_module(context, settings)
    grant_capture(context)
    try:
        assert DEFAULT_COMMAND_TIMEOUT_SECONDS == 5.0
        pending = asyncio.ensure_future(capture(context))
        await entered.wait()
        clock.advance(4.9)
        await settle()
        assert not pending.done()
        clock.advance(0.1)
        assert_error(await pending, ERROR_CAPTURE_TIMED_OUT)
        assert held.cancelled()
    finally:
        await handle.close()


async def test_cancelling_the_call_cancels_the_runner_and_stores_nothing() -> None:
    """R4/AC25: a cancellation from above while the command runs cancels the
    runner (the child is killed) and leaves the store empty."""

    clock = ManualClock()
    context = module_context(clock)
    runner, held, entered, _calls = held_runner(asyncio.get_running_loop())
    settings = command_settings(
        [sys.executable, "-c", "pass"], timeout=5.0, _subprocess_runner=runner, _sleeper=clock.sleep
    )
    handle = await prepared_module(context, settings)
    grant_capture(context)
    try:
        pending = asyncio.ensure_future(capture(context))
        await entered.wait()
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert held.cancelled()
        assert_nothing_retained(context)
    finally:
        await handle.close()


async def test_command_source_runs_a_python_one_liner_writing_a_png_to_stdout() -> None:
    """R5 end to end through the real runner: the configured ``argv`` runs
    the interpreter with a one-liner writing a PNG to stdout; the observation
    carries its dimensions and size and the store holds the very bytes."""

    image = png_bytes(12, 7)
    clock = ManualClock()
    context = module_context(clock)
    settings = command_settings([sys.executable, "-c", PNG_WRITER, image.hex()], timeout=30.0)
    handle = await prepared_module(context, settings)
    grant_capture(context)
    try:
        observation = await capture(context)

        part = image_part(observation)
        assert (part["width"], part["height"], part["size"]) == (12, 7, len(image))
        assert observation.result["source"] == "tool"
        ref = store_of(context).lookup(part["attachment_id"])
        assert ref is not None
        assert store_of(context).get(ref) == image
    finally:
        await handle.close()


async def test_command_exiting_non_zero_is_capture_failed_and_oversize_output_is_refused() -> None:
    """R5: a program that exits non-zero is ``error capture_failed``; one
    that writes more than ``max_bytes`` is killed and the call is
    ``error attachment_refused`` — both with 0 bytes retained and nothing
    the program printed in the error."""

    context = module_context()
    failing = command_settings(
        [sys.executable, "-c", "import sys; sys.stderr.write('/leaked/path'); sys.exit(3)"],
        timeout=30.0,
    )
    handle = await prepared_module(context, failing)
    grant_capture(context)
    try:
        observation = await capture(context)
        assert_error(observation, ERROR_CAPTURE_FAILED)
        assert "/leaked/path" not in repr(observation)
        assert_nothing_retained(context)
    finally:
        await handle.close()

    context = module_context()
    flooding = command_settings(
        [sys.executable, "-c", PNG_WRITER, (png_bytes(1, 1) + b"\x00" * 4096).hex()],
        timeout=30.0,
        max_bytes=1024,
    )
    handle = await prepared_module(context, flooding)
    grant_capture(context)
    try:
        observation = await capture(context)
        assert_error(observation, ERROR_ATTACHMENT_REFUSED)
        assert_nothing_retained(context)
    finally:
        await handle.close()


async def test_run_command_reads_bounded_stdout_and_kills_a_flooding_child() -> None:
    """The default runner: at most ``limit + 1`` bytes are read, a child that
    writes more is killed rather than drained, and a clean exit reports its
    status with the whole output."""

    status, output = await run_command(
        [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'x' * 10)"], limit=64
    )
    assert (status, output) == (0, b"x" * 10)

    status, output = await run_command(
        [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'y' * 1_000_000)"],
        limit=100,
    )
    assert 100 < len(output) < 1_000_000
    assert status is not None


# --------------------------------------------------------------------------- #
# P16F finding 1: a command's whole process group is terminated on timeout and
# on cancellation, and the child is reaped without waiting on an inherited pipe
# --------------------------------------------------------------------------- #


class FakeProcess:
    """A stand-in for ``asyncio.subprocess.Process`` with the wait semantics
    of asyncio on Python 3.12: ``wait`` returns at once when the exit is
    already known, otherwise only once the exit is known *and* stdout is
    disconnected — the stall a descendant that inherited the pipe causes.
    A kill (of the process, or of its group through ``os.killpg``) reports
    the exit a loop turn later, as the child watcher would; nothing ever
    closes the pipe but the module."""

    def __init__(self, loop: asyncio.AbstractEventLoop, *, pid: int, exited: bool = False) -> None:
        self.pid = pid
        self.returncode: int | None = 0 if exited else None
        self.stdout = asyncio.StreamReader()
        self.events: list[str] = []
        self.argv: tuple[str, ...] = ()
        self.options: dict[str, Any] = {}
        self._loop = loop
        self._pipe_closed = False
        self._finished: asyncio.Future[None] = loop.create_future()
        self._transport = _FakeSubprocessTransport(self)

    def kill(self) -> None:
        self.events.append("kill")
        self._loop.call_soon(self._exit, -9)

    def signal_group(self, sig: int) -> None:
        self.events.append(f"killpg:{sig}")
        self._loop.call_soon(self._exit, -sig)

    def close_pipe(self) -> None:
        self.events.append("close_pipe")
        self._pipe_closed = True
        self.stdout.feed_eof()
        self._try_finish()

    async def wait(self) -> int | None:
        self.events.append("wait")
        if self.returncode is None:
            await self._finished
        self.events.append("reaped")
        return self.returncode

    def _exit(self, code: int) -> None:
        if self.returncode is None:
            self.returncode = code
        self._try_finish()

    def _try_finish(self) -> None:
        if self.returncode is not None and self._pipe_closed and not self._finished.done():
            self._finished.set_result(None)


class _FakeSubprocessTransport:
    def __init__(self, process: FakeProcess) -> None:
        self._process = process

    def get_pipe_transport(self, fd: int) -> Any:
        return _FakePipeTransport(self._process) if fd == 1 else None


class _FakePipeTransport:
    def __init__(self, process: FakeProcess) -> None:
        self._process = process

    def close(self) -> None:
        self._process.close_pipe()


def fake_subprocesses(
    monkeypatch: pytest.MonkeyPatch, *, exited: bool = False, killpg_fails: bool = False
) -> tuple[Any, dict[int, FakeProcess]]:
    """A ``create_subprocess_exec`` seam returning :class:`FakeProcess`
    objects, with ``os.killpg`` routed to the fake group of the same id."""

    loop = asyncio.get_running_loop()
    processes: dict[int, FakeProcess] = {}

    async def spawn(*argv: str, **options: Any) -> FakeProcess:
        process = FakeProcess(loop, pid=4000 + len(processes), exited=exited)
        process.argv = argv
        process.options = options
        processes[process.pid] = process
        return process

    def killpg(pgid: int, sig: int) -> None:
        if killpg_fails or pgid not in processes:
            raise ProcessLookupError(pgid)
        processes[pgid].signal_group(sig)

    monkeypatch.setattr(os, "killpg", killpg, raising=False)
    return spawn, processes


def fake_command_source(spawn: Any, sleeper: Any, timeout: float = 2.0) -> CommandSource:
    return CommandSource(
        SourceSpec(
            "tool", "command", argv=("/opt/capture/wrapper.sh",), command_timeout_seconds=timeout
        ),
        runner=functools.partial(run_command, spawn=spawn),
        sleeper=sleeper,
    )


async def test_a_timeout_terminates_the_process_group_and_reaps_without_waiting_on_the_pipe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Finding 1, the timeout path on an injected clock and transport: the
    program is spawned in its own session (an isolated process group), the
    timer firing kills the *group* (``os.killpg`` with ``SIGKILL`` on the
    pid, never ``process.kill`` alone), the stdout pipe is closed *before*
    the wait — which would otherwise never return, since nothing but the
    module lets go of it — the child is reaped, and ``CaptureTimedOut``
    arrives on the same turn budget with no real time elapsing."""

    clock = ManualClock()
    spawn, processes = fake_subprocesses(monkeypatch)
    source = fake_command_source(spawn, clock.sleep, timeout=2.0)

    pending = asyncio.ensure_future(source.read(max_bytes=4096))
    try:
        await wait_until(lambda: processes)
        (process,) = processes.values()
        assert process.argv == ("/opt/capture/wrapper.sh",)
        assert process.options["start_new_session"] is True
        assert process.options["stdin"] is asyncio.subprocess.DEVNULL
        assert process.options["stderr"] is asyncio.subprocess.DEVNULL
        clock.advance(1.99)
        await settle()
        assert not pending.done()
        assert process.events == []

        clock.advance(0.01)
        await settle()
        assert pending.done()
        with pytest.raises(CaptureTimedOut):
            pending.result()
    finally:
        if not pending.done():
            pending.cancel()
            await asyncio.wait({pending})

    assert process.events == ["killpg:9", "close_pipe", "wait", "reaped"]
    assert process.returncode == -9


async def test_a_cancellation_terminates_the_process_group_and_reaps_without_waiting_on_the_pipe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Finding 1, the cancellation path: cancelling the read (the executor's
    own deadline or a run cancellation) kills the group, closes the pipe
    before the wait, reaps the child and only then propagates — the timer
    never fires."""

    clock = ManualClock()
    spawn, processes = fake_subprocesses(monkeypatch)
    source = fake_command_source(spawn, clock.sleep, timeout=2.0)

    pending = asyncio.ensure_future(source.read(max_bytes=4096))
    await wait_until(lambda: processes)
    (process,) = processes.values()
    await settle()
    assert not pending.done()

    pending.cancel()
    await settle()
    assert pending.cancelled()
    assert process.events == ["killpg:9", "close_pipe", "wait", "reaped"]
    assert process.returncode == -9
    assert clock._waiters == []


async def test_the_group_is_terminated_even_when_the_wrapper_itself_already_exited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Finding 1: a wrapper script that returned while its background child
    kept stdout open is a known exit with a pipe still held. The group is
    signalled all the same — the child is what is left running — the pipe
    closed, and the wrapper's own status kept."""

    clock = ManualClock()
    spawn, processes = fake_subprocesses(monkeypatch, exited=True)
    source = fake_command_source(spawn, clock.sleep, timeout=2.0)

    pending = asyncio.ensure_future(source.read(max_bytes=4096))
    await wait_until(lambda: processes)
    (process,) = processes.values()
    await settle()
    assert not pending.done()

    clock.advance(2.0)
    await settle()
    assert pending.done()
    with pytest.raises(CaptureTimedOut):
        pending.result()
    assert process.events == ["killpg:9", "close_pipe", "wait", "reaped"]
    assert process.returncode == 0


async def test_a_group_that_cannot_be_signalled_falls_back_to_killing_the_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ``os.killpg`` refuses the group the process alone is killed, the
    pipe still closed before the wait, and the outcome is the same."""

    clock = ManualClock()
    spawn, processes = fake_subprocesses(monkeypatch, killpg_fails=True)
    source = fake_command_source(spawn, clock.sleep, timeout=2.0)

    pending = asyncio.ensure_future(source.read(max_bytes=4096))
    await wait_until(lambda: processes)
    (process,) = processes.values()
    pending.cancel()
    await settle()
    assert pending.cancelled()
    assert process.events == ["kill", "close_pipe", "wait", "reaped"]


# The real thing, on Linux: a shell script launching a long-lived capture
# program that inherits stdout. The program reports its pid to the test over
# loopback, then idles; the script either waits on it or returns at once,
# leaving it behind in the group. Nothing in the test sleeps: the program's
# start is awaited on the socket, its end on a pidfd.

_CAPTURE_PROGRAM = """
import os, socket, sys, time
with socket.create_connection(("127.0.0.1", int(sys.argv[1]))) as link:
    link.sendall(str(os.getpid()).encode())
time.sleep(120)
"""

_PIDFD = sys.platform.startswith("linux") and hasattr(os, "pidfd_open")


async def wait_for_termination(pid: int) -> None:
    """Event-driven: a pidfd becomes readable when its process terminates."""

    try:
        pidfd = os.pidfd_open(pid)
    except ProcessLookupError:
        return  # already reaped
    loop = asyncio.get_running_loop()
    terminated: asyncio.Future[None] = loop.create_future()
    loop.add_reader(pidfd, lambda: terminated.done() or terminated.set_result(None))
    try:
        done, _pending = await asyncio.wait({terminated}, timeout=15.0)
        assert terminated in done, "the capture program is still running"
    finally:
        loop.remove_reader(pidfd)
        os.close(pidfd)


def _is_gone(pid: int) -> bool:
    """Whether *pid* no longer names a live process (a zombie counts as gone)."""

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    try:
        with open(f"/proc/{pid}/stat") as handle:
            return handle.read().rsplit(")", 1)[1].split()[0] == "Z"
    except OSError:
        return True


def zombie_children() -> list[int]:
    """Children of this process that exited and were never reaped."""

    zombies: list[int] = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/stat") as handle:
                state, ppid = handle.read().rsplit(")", 1)[1].split()[:2]
        except OSError:
            continue
        if state == "Z" and int(ppid) == os.getpid():
            zombies.append(int(entry))
    return zombies


async def shell_script_with_background_program(
    tmp_path: Path, ending: str
) -> tuple[Path, Any, "asyncio.Future[int]"]:
    """Write the script and the program; serve the program's pid report."""

    program = tmp_path / "program.py"
    program.write_text(_CAPTURE_PROGRAM)
    loop = asyncio.get_running_loop()
    reported: asyncio.Future[int] = loop.create_future()

    async def on_report(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        pid = int(await reader.read(32))
        writer.close()
        if not reported.done():
            reported.set_result(pid)

    server = await asyncio.start_server(on_report, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    script = tmp_path / "wrapper.sh"
    script.write_text(f"#!/bin/sh\n{sys.executable} {program} {port} &\n{ending}\n")
    script.chmod(0o755)
    return script, server, reported


@pytest.mark.skipif(not _PIDFD, reason="needs Linux process groups, /proc and pidfd")
@pytest.mark.parametrize("ending", ["wait", "exit 0"], ids=["wrapper-waits", "wrapper-returns"])
@pytest.mark.parametrize("interruption", ["timeout", "cancellation"])
async def test_a_shell_script_s_background_capture_program_is_terminated_and_reaped_promptly(
    tmp_path: Path, ending: str, interruption: str
) -> None:
    """Finding 1 end to end: the configured shell script starts the capture
    program in the background — it inherits stdout and never writes — and
    either waits on it or returns at once. On the timeout (the injected
    clock reaching ``command_timeout_seconds``) and on a cancellation, (a)
    the program is gone: the group was terminated; (b) the terminal outcome
    arrives at once, not when the program would have let go of the pipe;
    (c) no zombie of ours survives — the wrapper was reaped."""

    script, server, reported = await shell_script_with_background_program(tmp_path, ending)
    clock = ManualClock()
    source = CommandSource(
        SourceSpec("tool", "command", argv=(str(script),), command_timeout_seconds=2.0),
        runner=run_command,
        sleeper=clock.sleep,
    )
    program_pid: int | None = None
    pending = asyncio.ensure_future(source.read(max_bytes=4096))
    try:
        done, _ = await asyncio.wait(
            {reported, pending}, return_when=asyncio.FIRST_COMPLETED, timeout=15.0
        )
        assert reported in done, "the capture program never started"
        program_pid = reported.result()
        assert not pending.done()
        assert not _is_gone(program_pid)

        started = time.monotonic()
        if interruption == "timeout":
            clock.advance(2.0)
        else:
            pending.cancel()
        done, _ = await asyncio.wait({pending}, timeout=15.0)
        assert pending in done, "the outcome waited on the inherited pipe"
        assert time.monotonic() - started < 5.0
        if interruption == "timeout":
            with pytest.raises(CaptureTimedOut):
                pending.result()
        else:
            assert pending.cancelled()

        await wait_for_termination(program_pid)
        assert _is_gone(program_pid)
        assert zombie_children() == []
    finally:
        server.close()
        await server.wait_closed()
        if not pending.done():
            pending.cancel()
            await asyncio.wait({pending})
        if program_pid is not None and not _is_gone(program_pid):
            os.kill(program_pid, 9)


# A wrapper that starts a long-lived grandchild sharing its stdout, reports the
# grandchild's pid, then floods stdout past the limit so the runner kills it.
_WRAPPER_WITH_GRANDCHILD = """
import subprocess, sys, time
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
sys.stdout.write(f"{child.pid:>10}")
sys.stdout.flush()
sys.stdout.buffer.write(b"z" * 4096)
sys.stdout.flush()
time.sleep(120)
"""


@pytest.mark.skipif(not _PIDFD, reason="needs Linux process groups, /proc and pidfd")
async def test_run_command_kills_the_whole_process_group_of_a_wrapper_script() -> None:
    """A configured wrapper that launches the real capture program must not
    leave that program running past a kill — nor holding the inherited stdout
    open, which would keep the runner waiting on the pipe long after the
    wrapper itself is dead."""

    status, output = await asyncio.wait_for(
        run_command([sys.executable, "-c", _WRAPPER_WITH_GRANDCHILD], limit=100), timeout=15.0
    )
    assert status is not None
    grandchild = int(output[:10])
    try:
        await wait_for_termination(grandchild)
        assert _is_gone(grandchild)
        assert zombie_children() == []
    finally:
        if not _is_gone(grandchild):
            os.kill(grandchild, 9)


# --------------------------------------------------------------------------- #
# AC29: a source missing at prepare → not ready, module.degraded names it
# --------------------------------------------------------------------------- #


async def test_a_file_source_missing_at_prepare_leaves_the_module_not_ready_and_degraded_names_it(
    tmp_path: Path,
) -> None:
    """AC29: ``prepare`` raises, nothing is bound, the module is not ready and
    ``module.degraded`` names the source — not its path."""

    missing = tmp_path / "gone.png"
    context = module_context()
    handle = await activate(
        context,
        {
            "sources": {
                "present": {"kind": "file", "path": str(tmp_path / "ok.png")},
                "absent": {"kind": "file", "path": str(missing)},
            },
            "default_source": "present",
        },
        {},
    )
    (tmp_path / "ok.png").write_bytes(png_bytes(1, 1))
    registry = runtime_of(context).actions

    with pytest.raises(CaptureModuleError) as refused:
        await handle.prepare()

    assert "'absent'" in str(refused.value)
    assert str(tmp_path) not in str(refused.value)
    assert SCREEN_CAPTURE_ACTION not in registry.registered_ready()
    assert registry.bindings(SCREEN_CAPTURE_ACTION) == ()
    assert not context.actions.is_ready()
    (degraded,) = events_of(runtime_of(context).bus, TRACE_MODULE_DEGRADED)
    assert degraded["payload"]["module"] == MODULE_NAME
    assert degraded["payload"]["state"] == "degraded"
    assert "'absent'" in degraded["payload"]["reason"]
    assert str(tmp_path) not in repr(degraded)
    await handle.close()


async def test_a_command_source_whose_program_does_not_resolve_is_degraded_at_prepare() -> None:
    """AC29: an executable neither absolute nor on ``PATH`` leaves the module
    not ready with ``module.degraded`` naming the source; the interpreter,
    resolved by its absolute path, is accepted."""

    context = module_context()
    handle = await activate(
        context,
        {
            "sources": {
                "tool": {"kind": "command", "argv": ["no-such-capture-tool-9f2c", "--png"]},
            },
            "default_source": "tool",
        },
        {},
    )
    with pytest.raises(CaptureModuleError):
        await handle.prepare()
    (degraded,) = events_of(runtime_of(context).bus, TRACE_MODULE_DEGRADED)
    assert "'tool'" in degraded["payload"]["reason"]
    assert "no-such-capture-tool" not in repr(degraded)
    assert SCREEN_CAPTURE_ACTION not in runtime_of(context).actions.registered_ready()

    ready = module_context()
    accepted = await prepared_module(ready, command_settings([sys.executable, "-c", "pass"]))
    assert SCREEN_CAPTURE_ACTION in runtime_of(ready).actions.registered_ready()
    assert events_of(runtime_of(ready).bus, TRACE_MODULE_DEGRADED) == []
    await accepted.close()


def test_shipped_sources_probe_the_filesystem(tmp_path: Path) -> None:
    """R5: a ``FileSource`` is available when its path is a readable file, a
    ``CommandSource`` when ``argv[0]`` resolves on ``PATH`` or by its path."""

    present = tmp_path / "p.png"
    present.write_bytes(b"")
    assert FileSource(SourceSpec("a", "file", path=str(present))).probe() is None
    assert FileSource(SourceSpec("a", "file", path=str(tmp_path))).probe() is not None
    assert FileSource(SourceSpec("a", "file", path=str(tmp_path / "none"))).probe() is not None

    async def runner(argv: Any, *, limit: int) -> Any:
        return 0, b""

    def build(argv: list[str]) -> CommandSource:
        return CommandSource(
            SourceSpec("c", "command", argv=tuple(argv)), runner=runner, sleeper=asyncio.sleep
        )

    assert build([sys.executable]).probe() is None
    assert build(["no-such-capture-tool-9f2c"]).probe() is not None
    assert build([str(tmp_path / "not-executable")]).probe() is not None


# --------------------------------------------------------------------------- #
# AC23: the store's max_object_bytes and the module's max_bytes
# --------------------------------------------------------------------------- #


async def test_a_2048_byte_capture_against_max_object_bytes_1024_is_attachment_refused() -> None:
    """AC23: with ``attachments.max_object_bytes: 1024`` a capture of 2048
    bytes is ``error attachment_refused`` and the store retains 0 bytes for
    the run; the error observation is what the executor publishes."""

    image = png_bytes(1, 1)
    image += b"\x00" * (2048 - len(image))
    assert len(image) == 2048
    source = FakeCaptureSource(data=image)
    context = module_context(store_limits={**STORE_LIMITS, "max_object_bytes": 1024})
    handle = await prepared_module(context, fake_settings(source))
    grant_capture(context)
    try:
        observation = await capture(context)

        assert_error(observation, ERROR_ATTACHMENT_REFUSED)
        assert "max_object_bytes" in observation.error["message"]
        assert_nothing_retained(context)
        assert len(source.calls) == 1
        completed = events_of(runtime_of(context).bus, "action.completed")
        assert completed[-1]["payload"]["status"] == "error"
        assert completed[-1]["payload"]["error_code"] == ERROR_ATTACHMENT_REFUSED
    finally:
        await handle.close()


async def test_the_modules_own_max_bytes_refuses_before_the_store_is_reached() -> None:
    """R5/AC23: ``max_bytes: 1024`` refuses a 2048-byte capture with the same
    ``attachment_refused`` code, the store never asked; the default bound is
    5 242 880."""

    assert DEFAULT_MAX_BYTES == 5_242_880
    image = png_bytes(1, 1) + b"\x00" * 2000
    source = FakeCaptureSource(data=image)
    context = module_context()
    handle = await prepared_module(context, fake_settings(source, max_bytes=1024))
    grant_capture(context)
    try:
        assert handle.max_bytes == 1024
        observation = await capture(context)
        assert_error(observation, ERROR_ATTACHMENT_REFUSED)
        assert_nothing_retained(context)
        assert source.calls == [{"args": (), "kwargs": {"max_bytes": 1024}}]
    finally:
        await handle.close()


async def test_a_run_at_its_byte_bound_is_refused_and_keeps_what_it_held() -> None:
    """R4: the store's per-run bound is a refusal too — the second capture is
    ``attachment_refused`` and the first stays leased at its size."""

    image = png_bytes(4, 4)
    source = FakeCaptureSource(data=image)
    context = module_context(
        store_limits={**STORE_LIMITS, "max_bytes_per_run": len(image) + len(image) // 2}
    )
    handle = await prepared_module(context, fake_settings(source))
    grant_capture(context)
    try:
        first = await capture(context, call_id="call-1")
        second = await capture(context, call_id="call-2")
        image_part(first)
        assert_error(second, ERROR_ATTACHMENT_REFUSED)
        assert store_of(context).usage(RUN_ID).total_bytes == len(image)
    finally:
        await handle.close()


# --------------------------------------------------------------------------- #
# AC30: default-deny on both platforms
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("destination", [TWITCH_CAPTURE, FAKE_CAPTURE], ids=["twitch", "fake"])
async def test_capture_without_a_rule_is_refused_and_no_source_is_touched(
    destination: Destination,
) -> None:
    """AC30: no applicable rule → ``refused not_authorized``, provider invoked
    0 times, the source read 0 times and the store empty, on
    ``twitch/*/capture`` and on ``fake/*/capture`` alike."""

    source = FakeCaptureSource(width=3, height=3)
    context = module_context()
    handle = await prepared_module(context, fake_settings(source))
    try:
        assert SCREEN_CAPTURE_ACTION in runtime_of(context).actions.registered_ready()
        assert runtime_of(context).actions.authorized(principal=PRINCIPAL) == {}

        observation = await capture(context, destination)

        assert observation.status == "refused"
        assert observation.error is not None
        assert observation.error["code"] == ERROR_NOT_AUTHORIZED
        assert observation.result is None
        assert observation.parts == ()
        assert runtime_of(context).executor.provider_invocations == 0
        assert source.calls == []
        assert_nothing_retained(context)
    finally:
        await handle.close()


async def test_a_rule_scoped_to_one_platform_does_not_reach_the_other() -> None:
    """AC30/R5: a grant on ``fake/*/capture`` serves the fake destination and
    leaves the twitch one refused, the provider entered exactly once."""

    source = FakeCaptureSource(width=3, height=3)
    context = module_context()
    handle = await prepared_module(context, fake_settings(source))
    grant_capture(context, Destination("fake", "*", "capture"))
    try:
        allowed = await capture(context, FAKE_CAPTURE, call_id="call-fake")
        refused = await capture(context, TWITCH_CAPTURE, call_id="call-twitch")

        part = image_part(allowed)
        assert (part["width"], part["height"], part["size"]) == (3, 3, len(source.data))
        assert refused.status == "refused"
        assert refused.error["code"] == ERROR_NOT_AUTHORIZED
        assert runtime_of(context).executor.provider_invocations == 1
        assert len(source.calls) == 1
    finally:
        await handle.close()


# --------------------------------------------------------------------------- #
# Arguments and malformed captures
# --------------------------------------------------------------------------- #


async def test_an_unknown_source_argument_is_invalid_arguments_and_a_known_one_selects() -> None:
    """R5: ``source`` naming no configured source is ``error
    invalid_arguments`` with nothing read; naming a configured one captures
    from it and the result says which; omitted, the default serves; a
    non-string is refused by the executor's schema before the provider."""

    first = FakeCaptureSource(width=5, height=5)
    second = FakeCaptureSource(width=9, height=2)
    context = module_context()
    settings = {
        "sources": {
            "one": {"kind": "file", "path": "/never/read/1.png"},
            "two": {"kind": "file", "path": "/never/read/2.png"},
        },
        "default_source": "two",
        "_source_factory": lambda spec: {"one": first, "two": second}[spec.name],
    }
    handle = await prepared_module(context, settings)
    grant_capture(context)
    try:
        unknown = await capture(context, arguments={"source": "three"}, call_id="call-1")
        assert_error(unknown, ERROR_INVALID_ARGUMENTS)
        assert "three" not in unknown.error["message"]
        assert first.calls == [] and second.calls == []

        chosen = await capture(context, arguments={"source": "one"}, call_id="call-2")
        assert chosen.result["source"] == "one"
        assert (image_part(chosen)["width"], image_part(chosen)["height"]) == (5, 5)

        defaulted = await capture(context, call_id="call-3")
        assert defaulted.result["source"] == "two"
        assert (image_part(defaulted)["width"], image_part(defaulted)["height"]) == (9, 2)

        before = runtime_of(context).executor.provider_invocations
        typed = await capture(context, arguments={"source": 7}, call_id="call-4")
        assert_error(typed, ERROR_INVALID_ARGUMENTS)
        assert runtime_of(context).executor.provider_invocations == before
        extra = await capture(context, arguments={"region": "left"}, call_id="call-5")
        assert_error(extra, ERROR_INVALID_ARGUMENTS)
        assert runtime_of(context).executor.provider_invocations == before
    finally:
        await handle.close()


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"not an image at all",
        png_bytes(4, 4)[:20],
        b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x0dIHDR" + struct.pack(">II", 0, 4) + b"\x00" * 5,
        b"\xff\xd8\xff\xc0\x00\x11\x08\x00",
        b"\xff\xd8\xff\xda\x00\x02" + b"\x00" * 16,
        b"GIF89a" + b"\x00" * 32,
    ],
    ids=["empty", "text", "png-truncated", "png-zero-width", "jpeg-truncated-sof", "jpeg-scan-first", "gif"],
)
async def test_bytes_without_a_readable_png_or_jpeg_header_are_invalid_result(data: bytes) -> None:
    """P16 risk: a truncated or foreign capture is an ``invalid_result``
    error the provider reports, never an exception, with 0 bytes stored."""

    source = FakeCaptureSource(data=data)
    context = module_context()
    handle = await prepared_module(context, fake_settings(source))
    grant_capture(context)
    try:
        observation = await capture(context)
        assert_error(observation, ERROR_INVALID_RESULT)
        assert_nothing_retained(context)
    finally:
        await handle.close()


def test_sniff_image_reads_png_ihdr_and_jpeg_sof_and_refuses_the_rest() -> None:
    """R4: the PNG ``IHDR`` and the first JPEG start-of-frame — past APP,
    fill bytes and standalone markers, progressive included — give the
    content type and dimensions; anything else is ``None``."""

    assert sniff_image(png_bytes(640, 480)) == ImageHeader(CONTENT_TYPE_PNG, 640, 480)
    assert sniff_image(jpeg_bytes(1920, 1080)) == ImageHeader(CONTENT_TYPE_JPEG, 1920, 1080)
    progressive = jpeg_bytes(30, 20).replace(b"\xff\xc0", b"\xff\xc2")
    assert sniff_image(progressive) == ImageHeader(CONTENT_TYPE_JPEG, 30, 20)
    padded = b"\xff\xd8\xff\xff\xff\xd0" + jpeg_bytes(7, 5)[2:]
    assert sniff_image(padded) == ImageHeader(CONTENT_TYPE_JPEG, 7, 5)

    assert sniff_image(b"") is None
    assert sniff_image(png_bytes(4, 4)[:23]) is None
    assert sniff_image(b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x0dIDAT" + b"\x00" * 8) is None
    assert sniff_image(jpeg_bytes(1, 1)[:-14]) is None
    assert sniff_image(jpeg_bytes(0, 5)) is None
    assert sniff_image(b"\xff\xd8\x00\xc0" + b"\x00" * 16) is None
    assert sniff_image(b"\xff\xd8\xff\xe0\x00\x01") is None
    assert sniff_image(b"\xff\xd8\xff\xd9") is None


async def test_a_source_that_raises_is_capture_failed_and_a_non_bytes_result_is_invalid() -> None:
    """R5: a source failure is ``error capture_failed`` with no exception
    reaching the executor; a source handing back something other than bytes
    is ``invalid_result``; both retain 0 bytes."""

    failing = FakeCaptureSource(width=2, height=2)
    failing.fail_with = OSError("/leaked/path: permission denied")
    context = module_context()
    handle = await prepared_module(context, fake_settings(failing))
    grant_capture(context)
    try:
        observation = await capture(context)
        assert_error(observation, ERROR_CAPTURE_FAILED)
        assert "/leaked/path" not in repr(observation)
        assert_nothing_retained(context)
    finally:
        await handle.close()

    odd = FakeCaptureSource(width=2, height=2)
    odd.data = "not bytes"  # type: ignore[assignment]
    context = module_context()
    handle = await prepared_module(context, fake_settings(odd))
    grant_capture(context)
    try:
        assert_error(await capture(context), ERROR_INVALID_RESULT)
        assert_nothing_retained(context)
    finally:
        await handle.close()


# --------------------------------------------------------------------------- #
# Manifest (R7, R5)
# --------------------------------------------------------------------------- #


def test_manifest_is_v2_and_declares_screen_capture_without_granting_it() -> None:
    """R7/R5: manifest v2 with no role, no event, no credential, a settings
    schema requiring ``sources`` and ``default_source`` (plus ``max_bytes``
    and the reserved ``limits`` block), the declared hook, and exactly the
    ``screen.capture`` read action over ``*/*/capture`` with an optional
    ``source`` argument, the six-field result, a 10 s budget, no idempotency
    and no delivery capability — no grant lives in the file."""

    manifest = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert MANIFEST_PATH == MODULE_DIR / "module.yaml"

    assert manifest["name"] == MODULE_NAME == "capture"
    assert manifest["manifest_version"] == 2
    assert manifest["runtime_api"] == RUNTIME_API
    assert manifest["produces"] == []
    assert manifest["consumes"] == []
    assert manifest["middleware"] is False
    assert manifest["lifecycle"] == {"roles": []}
    assert manifest["credentials"] == []
    assert manifest["settings_validator"] == "validate_settings"

    schema = manifest["settings_schema"]
    assert schema["type"] == "object"
    assert set(schema["properties"]) == {"sources", "default_source", "max_bytes", "limits"}
    assert set(schema["required"]) == {"sources", "default_source"}
    assert schema["properties"]["sources"]["type"] == "object"
    assert schema["properties"]["default_source"]["type"] == "string"
    assert schema["properties"]["max_bytes"]["type"] == "integer"
    assert schema["properties"]["max_bytes"]["minimum"] == 1
    assert schema["properties"]["limits"]["type"] == "object"
    # The seams are settings-only: the schema does not close the object so
    # a harness can inject them, and the hook refuses any other key by name.
    assert "additionalProperties" not in schema

    (action,) = manifest["actions"]
    assert action["name"] == SCREEN_CAPTURE_ACTION == "screen.capture"
    assert action["version"] == 1
    assert action["nature"] == "read"
    assert action["required_permissions"] == ["screen.capture"]
    assert action["supported_destinations"] == [
        {"platform": "*", "channel_id": "*", "scope": "capture"}
    ]
    arguments = action["argument_schema"]
    assert set(arguments["properties"]) == {"source"}
    assert arguments["properties"]["source"]["type"] == "string"
    assert "required" not in arguments
    assert arguments["additionalProperties"] is False
    result = action["result_schema"]
    assert set(result["properties"]) == set(result["required"]) == {
        "source", "content_type", "width", "height", "size", "captured_at",
    }
    assert result["additionalProperties"] is False
    assert result["properties"]["content_type"]["enum"] == ["image/png", "image/jpeg"]
    for name in ("width", "height", "size"):
        assert result["properties"][name] == {
            "type": "integer", "minimum": 1,
            "description": result["properties"][name]["description"],
        }
    assert result["properties"]["captured_at"]["type"] == "number"
    assert action["timeout_seconds"] == 10
    assert action["idempotency"] == "none"
    assert "delivery" not in action
    assert set(action) == {
        "name",
        "version",
        "description",
        "argument_schema",
        "result_schema",
        "nature",
        "required_permissions",
        "supported_destinations",
        "timeout_seconds",
        "idempotency",
    }
    assert set(manifest) == {
        "name",
        "manifest_version",
        "runtime_api",
        "produces",
        "consumes",
        "middleware",
        "lifecycle",
        "settings_schema",
        "settings_validator",
        "credentials",
        "actions",
    }
    assert not any(key in manifest for key in ("rules", "authorization", "grants"))


def test_module_names_no_platform() -> None:
    """R5/AC31: ``grep twitch`` over the module directory is 0 lines — which
    platform a capture is observed for is the destination's, a runtime
    value, never a name in code."""

    completed = subprocess.run(
        ["grep", "-r", "-i", "-c", "twitch", str(MODULE_DIR / "__init__.py"), str(MANIFEST_PATH)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 1, completed.stdout
    assert all(line.endswith(":0") for line in completed.stdout.splitlines())
    for path in (MODULE_DIR / "__init__.py", MANIFEST_PATH):
        assert "twitch" not in path.read_text(encoding="utf-8").lower()


# --------------------------------------------------------------------------- #
# Settings hook and activation (R7)
# --------------------------------------------------------------------------- #


def test_settings_hook_checks_source_shapes_and_names_fields_without_values(tmp_path: Path) -> None:
    """R7: ``sources`` (non-empty, each ``file`` with a ``path`` or
    ``command`` with a non-empty ``argv`` and a finite positive timeout) and
    ``default_source`` naming one are required; ``max_bytes`` is a positive
    integer; ``limits`` is accepted; a seam must be callable; every other
    key is refused by name; one diagnostic per field, no value echoed."""

    good = {
        "sources": {
            "shot": {"kind": "file", "path": "/var/frames/latest.png"},
            "tool": {"kind": "command", "argv": ["grab", "--png"], "command_timeout_seconds": 2.5},
        },
        "default_source": "tool",
        "max_bytes": 2048,
        "limits": LIMITS,
        "_source_factory": lambda spec: None,
    }
    assert validate_settings(good) == []
    assert validate_settings({"sources": good["sources"], "default_source": "shot"}) == []

    assert validate_settings("settings") == ["module 'capture': field 'settings': must be a mapping"]

    def fields(settings: Any) -> dict[str, str]:
        out: dict[str, str] = {}
        for line in validate_settings(settings):
            prefix, _, reason = line.partition(": ")
            assert prefix == "module 'capture'"
            field, _, why = reason.partition(": ")
            out[field.removeprefix("field '").rstrip("'")] = why
        return out

    assert set(fields({})) == {"sources", "default_source"}
    assert fields({"sources": {}, "default_source": "x"})["sources"].startswith("must be a non-empty")
    assert set(fields({"sources": [], "default_source": "x"})) == {"sources"}
    diagnostics = fields(
        {
            "sources": {
                "a": "not a mapping",
                "b": {"kind": "socket"},
                "c": {"kind": "file"},
                "d": {"kind": "file", "path": "/x", "argv": ["y"]},
                "e": {"kind": "command", "argv": []},
                "f": {"kind": "command", "argv": ["ok"], "command_timeout_seconds": 0},
                "g": {"kind": "command", "argv": ["ok"], "command_timeout_seconds": float("inf")},
                "h": {"kind": "command", "argv": ["ok", 3]},
                "i": {"kind": []},
                "j": {"kind": {}, "path": "/x"},
                "k": {},
                "": {"kind": "file", "path": "/x"},
            },
            "default_source": "zzz",
            "max_bytes": 0,
            "extra": 1,
            "_sleeper": "not callable",
        }
    )
    assert set(diagnostics) == {
        "sources.a",
        "sources.b.kind",
        "sources.c.path",
        "sources.d.argv",
        "sources.e.argv",
        "sources.f.command_timeout_seconds",
        "sources.g.command_timeout_seconds",
        "sources.h.argv",
        "sources.i.kind",
        "sources.j.kind",
        "sources.k.kind",
        "sources",
        "default_source",
        "max_bytes",
        "extra",
        "_sleeper",
    }
    assert diagnostics["default_source"] == "must name a configured source"
    assert diagnostics["max_bytes"] == "must be a positive integer"
    assert diagnostics["_sleeper"] == "must be callable"
    assert fields({"sources": good["sources"], "default_source": ""})["default_source"]
    assert fields({"sources": good["sources"], "default_source": "shot", "max_bytes": True})["max_bytes"]
    assert fields({"sources": good["sources"], "default_source": "shot", "max_bytes": 7.5})["max_bytes"]
    for line in validate_settings({"sources": {"s": {"kind": "file", "path": "/secret/frame.png"}}, "default_source": "nope", "max_bytes": -5}):
        assert "/secret" not in line and "nope" not in line and "-5" not in line


@pytest.mark.parametrize("kind", [[], {}, 12], ids=["list", "mapping", "int"])
def test_a_non_string_source_kind_is_the_kind_diagnostic_not_a_type_error(kind: Any) -> None:
    """Finding 2: ``kind: []`` and ``kind: {}`` are unhashable, ``kind: 12``
    is not a string — each is reported as ``sources.<name>.kind`` exactly
    like an unknown string kind, the validator never raises, and the other
    offending fields of the same settings block are reported alongside."""

    settings = {
        "sources": {
            "cam": {"kind": kind, "path": "/frames/cam.png"},
            "tool": {"kind": "command", "argv": []},
        },
        "default_source": "nobody",
        "max_bytes": 0,
    }
    diagnostics = validate_settings(settings)

    kind_diagnostic = "module 'capture': field 'sources.cam.kind': must be 'file' or 'command'"
    assert diagnostics.count(kind_diagnostic) == 1
    unknown = dict(
        settings,
        sources={**settings["sources"], "cam": {"kind": "socket", "path": "/frames/cam.png"}},
    )
    assert diagnostics == validate_settings(unknown)
    assert (
        "module 'capture': field 'sources.tool.argv': must be a non-empty list of strings"
        in diagnostics
    )
    assert "module 'capture': field 'default_source': must name a configured source" in diagnostics
    assert "module 'capture': field 'max_bytes': must be a positive integer" in diagnostics
    assert not any("/frames" in line or "nobody" in line for line in diagnostics)


async def test_activation_refuses_bad_settings_and_a_runtime_without_a_store_or_clock(
    tmp_path: Path,
) -> None:
    """R7: a handle built outside the loader is refused on the hook's terms;
    a context lacking the actions facade, an attachment store or a clock is
    refused at activation; a source factory that fails or hands back an
    object without ``read`` is refused; nothing is declared."""

    path = tmp_path / "f.png"
    path.write_bytes(png_bytes(1, 1))
    context = module_context()
    with pytest.raises(CaptureModuleError):
        await activate(context, {}, {})
    with pytest.raises(CaptureModuleError):
        await activate(context, "settings", {})  # type: ignore[arg-type]
    with pytest.raises(CaptureModuleError):
        await activate(context, file_settings(path, max_bytes=0), {})
    with pytest.raises(CaptureModuleError):
        await activate(context, file_settings(path, _source_factory=lambda spec: object()), {})

    def exploding(spec: SourceSpec) -> Any:
        raise RuntimeError(str(path))

    with pytest.raises(CaptureModuleError) as refused:
        await activate(context, file_settings(path, _source_factory=exploding), {})
    assert str(tmp_path) not in str(refused.value)

    class _Missing:
        def __init__(self, **fields: Any) -> None:
            self.__dict__.update(fields)

    for broken in (
        _Missing(attachments=context.attachments, clock=context.clock),
        _Missing(actions=context.actions, clock=context.clock),
        _Missing(actions=context.actions, attachments=object(), clock=context.clock),
        _Missing(actions=context.actions, attachments=context.attachments, clock=None),
    ):
        with pytest.raises(CaptureModuleError):
            await activate(broken, file_settings(path), {})
    assert SCREEN_CAPTURE_ACTION not in runtime_of(context).actions.discovered()


async def test_activation_accepts_an_awaitable_source_factory_and_command_defaults() -> None:
    """R5: the factory may be a coroutine function; a ``command`` spec carries
    its argv and the default timeout; a ``file`` spec its path."""

    seen: list[SourceSpec] = []

    async def factory(spec: SourceSpec) -> FakeCaptureSource:
        seen.append(spec)
        return FakeCaptureSource(width=1, height=1)

    context = module_context()
    handle = await activate(
        context,
        {
            "sources": {
                "shot": {"kind": "file", "path": "/frames/latest.png"},
                "tool": {"kind": "command", "argv": ["grab", "--png"]},
                "slow": {"kind": "command", "argv": ["grab"], "command_timeout_seconds": 2},
            },
            "default_source": "shot",
            "_source_factory": factory,
        },
        {},
    )
    assert [spec.name for spec in seen] == ["shot", "tool", "slow"]
    assert seen[0] == SourceSpec("shot", "file", path="/frames/latest.png")
    assert seen[1] == SourceSpec("tool", "command", argv=("grab", "--png"))
    assert seen[1].command_timeout_seconds == DEFAULT_COMMAND_TIMEOUT_SECONDS == 5.0
    assert seen[2].command_timeout_seconds == 2.0
    assert set(handle.sources) == {"shot", "tool", "slow"}
    await handle.prepare()
    assert SCREEN_CAPTURE_ACTION in runtime_of(context).actions.registered_ready()
    await handle.close()


async def test_prepare_binds_the_declared_spec_and_close_withdraws(tmp_path: Path) -> None:
    """R5/R4: ``prepare`` registers the manifest's own contract over
    ``*/*/capture`` under the provider name and marks the module ready —
    once, idempotently. ``close`` withdraws readiness, so a later call is
    refused ``provider_not_ready`` with the provider entered 0 times more."""

    path = tmp_path / "frame.png"
    path.write_bytes(png_bytes(2, 3))
    context = module_context()
    handle = await activate(context, file_settings(path), {})
    registry = runtime_of(context).actions
    assert SCREEN_CAPTURE_ACTION not in registry.discovered()

    await handle.prepare()
    await handle.prepare()

    (binding,) = registry.bindings(SCREEN_CAPTURE_ACTION)
    assert binding.provider_name == SCREEN_CAPTURE_PROVIDER
    assert binding.module == MODULE_NAME
    assert binding.destination == Destination("*", "*", "capture")
    assert binding.spec == registry.discovered()[SCREEN_CAPTURE_ACTION]
    assert binding.spec.nature == "read"
    assert binding.spec.idempotency == "none"
    assert binding.spec.timeout_seconds == 10
    assert binding.spec.delivery is None
    assert SCREEN_CAPTURE_ACTION in registry.registered_ready()

    grant_capture(context)
    image_part(await capture(context))

    await handle.close()
    await handle.close()
    assert SCREEN_CAPTURE_ACTION not in registry.registered_ready()
    observation = await capture(context, call_id="call-closed")
    assert observation.status == "refused"
    assert observation.error["code"] == "provider_not_ready"
    assert runtime_of(context).executor.provider_invocations == 1


async def test_loader_activates_the_manifest_and_the_coordinator_drives_prepare(
    tmp_path: Path,
) -> None:
    """R4/R7: the real loader validates the manifest, runs the declared hook
    with the reserved ``limits`` block, declares the action at discovery
    and activates through the context; the coordinator's ``prepare`` binds
    the provider to the very spec the loader declared and a granted capture
    then leases the file's bytes into the context's store; ``stop``
    withdraws readiness. A profile without ``default_source`` is refused by
    the hook, naming the field."""

    path = tmp_path / "frame.png"
    image = png_bytes(16, 9)
    path.write_bytes(image)
    clock = ManualClock()
    store = AttachmentStore(clock=clock, **STORE_LIMITS)
    context = runtime_context(clock=clock, attachments=store)
    loader = ModuleLoader(context.bus, ROOT / "modules", context=context, environ={})
    diagnostics: list[str] = []

    activations = await loader.activate_enabled(
        {
            "enabled_modules": [MODULE_NAME],
            "modules": {MODULE_NAME: {**file_settings(path), "limits": LIMITS}},
        }
    )
    (activation,) = activations
    assert activation.manifest_version == 2
    assert activation.roles == frozenset()
    assert type(activation.handle).__name__ == CaptureModule.__name__
    assert SCREEN_CAPTURE_ACTION in context.actions.discovered()
    assert context.actions.bindings(SCREEN_CAPTURE_ACTION) == ()

    coordinator = PhaseCoordinator(activations, tasks=context.tasks, reporter=diagnostics.append)
    assert (await coordinator.start()).status == 0
    (binding,) = context.actions.bindings(SCREEN_CAPTURE_ACTION)
    assert binding.spec == context.actions.discovered()[SCREEN_CAPTURE_ACTION]
    assert SCREEN_CAPTURE_ACTION in context.actions.registered_ready()

    context.actions._authorization.grant(
        AuthorizationRule(
            rule_id="grant",
            action_name=SCREEN_CAPTURE_ACTION,
            granted_permissions=("screen.capture",),
        )
    )
    observation = await context.executor.invoke(capture_call(FAKE_CAPTURE))
    part = image_part(observation)
    assert (part["width"], part["height"], part["size"]) == (16, 9, len(image))
    assert store.usage(RUN_ID).objects == 1

    assert (await coordinator.stop()).status == 0
    assert diagnostics == []
    assert SCREEN_CAPTURE_ACTION not in context.actions.registered_ready()

    refusing = ModuleLoader(context.bus, ROOT / "modules", context=context, environ={})
    with pytest.raises(Exception) as refused:
        await refusing.activate_enabled(
            {
                "enabled_modules": [MODULE_NAME],
                "modules": {
                    MODULE_NAME: {
                        "sources": {"screen": {"kind": "file", "path": str(path)}},
                        "limits": LIMITS,
                    }
                },
            }
        )
    assert "default_source" in str(refused.value)
    assert "capture" in str(refused.value)
    assert str(tmp_path) not in str(refused.value)


async def test_the_coordinator_reports_a_missing_source_and_startup_does_not_offer_the_action(
    tmp_path: Path,
) -> None:
    """AC29 through the loader and the coordinator: a source missing at
    ``prepare`` fails startup with the module reported ``degraded`` naming
    the source, and the action is never in the ready set."""

    clock = ManualClock()
    store = AttachmentStore(clock=clock, **STORE_LIMITS)
    context = runtime_context(clock=clock, attachments=store)
    loader = ModuleLoader(context.bus, ROOT / "modules", context=context, environ={})
    activations = await loader.activate_enabled(
        {
            "enabled_modules": [MODULE_NAME],
            "modules": {MODULE_NAME: file_settings(tmp_path / "missing.png")},
        }
    )
    diagnostics: list[str] = []
    coordinator = PhaseCoordinator(activations, tasks=context.tasks, reporter=diagnostics.append)

    report = await coordinator.start()

    assert report.status != 0
    assert SCREEN_CAPTURE_ACTION not in context.actions.registered_ready()
    degraded = events_of(context.bus, TRACE_MODULE_DEGRADED)
    assert degraded
    assert any("'screen'" in event["payload"].get("reason", "") for event in degraded)
    assert all(str(tmp_path) not in repr(event) for event in context.bus.list_events())


def test_module_package_is_importable_by_its_shipped_name() -> None:
    """P6 packaging: the directory is a package the distribution can ship."""

    assert (MODULE_DIR / "__init__.py").is_file()
    assert sys.modules["modules.capture"].__name__ == "modules.capture"
    assert inspect.iscoroutinefunction(activate)
