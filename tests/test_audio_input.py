"""``modules/audio_input``: ``audio.capture`` (R3, R4, R10 module half, R9-packaging).

AC8 — an injected source producing 5 s of valid WAV (160 044 bytes) and
``seconds: 3``: ``success``, one ``audio_ref`` of 96 044 bytes / 3 000 ms /
16 kHz / mono, an attachment of exactly those bytes leased to the call's run,
``captured_at`` from the clock, ``transcription_status == "disabled"``, a
stored segment whose header announces its 96 000-byte data chunk. AC9 — the
format taxonomy (8 kHz, stereo, 8-bit, non-WAV → ``invalid_result``, nothing
stored), ``seconds`` above ``max_seconds`` refused before any spawn, a segment
above ``max_bytes`` refused with nothing stored, a 30 s segment stored whole.
AC10/AC39 (module half) — the admission reserve refuses before any spawn; a
recorder that emitted nothing is killed at the call deadline itself — never
at ``t = 98.0`` or ``99.99`` — and the module's ``capture_timed_out`` record,
stamped at or after the deadline, is adopted by the real executor; a
cancellation or a drain kills it and ends ``cancelled``; the run's release and
the store's TTL leave nothing behind. AC12 — nothing is spawned outside a
call (checked across the whole module by the last test), no task at
``prepare``, no producer role, default-deny. AC41 — no lead or margin; the
only deadline subtractions are the admission reserve and the transcription
window.

P11 — the optional transcription. AC11: one multipart request carrying the
stored WAV and ``model`` (``language`` when set), ``ok`` with a dated
transcription cut to ``max_chars``, the four failure kinds each a ``success``
without ``transcription``, ``skipped:deadline`` with nothing sent when the
window is exhausted. AC42: the window edge ``expiry − 1 s`` abandons a held
request and the record, stamped at the module's decision instant before the
deadline, is adopted as ``success`` — at the edge, on a delayed wake with a
late answer, with the executor's adoption parked past the deadline; a drain
and a cancellation after storing end the same way. AC29 (``audio_input``): a
failed transcription probe degrades the transcription only, value-free;
``required: true`` fails ``prepare`` naming ``transcription.endpoint`` with
the transport closed.

Every call goes through the real executor built by ``runtime_context`` over a
shared :class:`AttachmentStore` on the :class:`ManualClock`; the sources, the
subprocess runner and the sleeper are injected, and time moves only on the
clock.
"""

from __future__ import annotations

import asyncio
import dataclasses
import os
import re
import select
import struct
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from core.actions import ERROR_INVALID_ARGUMENTS, ERROR_TIMED_OUT, ActionExecutor, AuthorizationRule
from core.attachments import AttachmentExpired, AttachmentStore
from core.contracts import COUNTER_ACTION_TIMEOUTS, ActionCall, ActionObservation, Destination
from core.lifecycle import PhaseCoordinator
from core.loader import ModuleLoader
from core.runtime import ModuleContext, RuntimeContext
from conftest import (
    HELD,
    FakeAudioSource,
    FakeBytesResponse,
    ManualClock,
    RecordingSubprocessRunner,
    ScriptedTranscriptionTransport,
    events_of,
    runtime_context,
    settle,
    silent_wav,
    trace_texts,
    wait_until,
    wav_bytes,
)

from modules.audio_input import (
    CAPTURE_ACTION,
    DEFAULT_MAX_BYTES,
    ERROR_ATTACHMENT_REFUSED,
    ERROR_CAPTURE_FAILED,
    ERROR_CAPTURE_TIMED_OUT,
    ERROR_CAPTURE_TOO_LONG,
    ERROR_INVALID_RESULT,
    MANIFEST_PATH,
    MODULE_NAME,
    PROVIDER_ID,
    PROVIDER_NAME,
    TRANSCRIPTION_DEGRADED_REASON,
    TRANSCRIPTION_PROVIDER_ID,
    TRANSCRIPTION_RESERVE_SECONDS,
    AudioInputModule,
    AudioInputModuleError,
    InvalidSegment,
    activate,
    cut_segment,
    probe_segment,
    spawn_recorder,
    validate_settings,
)


ROOT = Path(__file__).parents[1]
MODULE_DIR = ROOT / "modules" / "audio_input"

PRINCIPAL = "brain"
RUN_ID = "run-1"
AUDIO = Destination("twitch", "channel-1", "audio")
FAKE_AUDIO = Destination("fake", "channel-9", "audio")

#: ``argv[0]`` must resolve to an executable at ``prepare``, so the scripted
#: recorders name the running interpreter; the runner is injected and nothing
#: is spawned from these.
RECORDER_ARGV = [sys.executable, "recorder-under-test", "--wav"]
MISSING_RECORDER = "/nonexistent/recorder-missing"

STORE_LIMITS: dict[str, Any] = {
    "max_object_bytes": 2 * 1024 * 1024,
    "max_objects": 16,
    "max_total_bytes": 8 * 1024 * 1024,
    "max_bytes_per_run": 4 * 1024 * 1024,
    "ttl_seconds": 300.0,
}

DEADLINE = 100.0

#: Spawns a runner saw while no ``audio.capture`` call was in flight, across
#: the whole module (AC12); checked by the last test.
SPAWNS_OUTSIDE_CALLS: list[list[str]] = []
SPAWNS_TOTAL: list[list[str]] = []


# --------------------------------------------------------------------------- #
# Harness
# --------------------------------------------------------------------------- #


class CallTrackingRunner(RecordingSubprocessRunner):
    """The shared recording runner, noting each spawn made outside a call."""

    def __init__(self, *outputs: bytes, gated: bool = False, startable: bool = True) -> None:
        super().__init__(*outputs, gated=gated, startable=startable)
        self.in_call = 0

    async def spawn(self, argv: Any, **kwargs: Any) -> Any:
        SPAWNS_TOTAL.append(list(argv))
        if self.in_call == 0:
            SPAWNS_OUTSIDE_CALLS.append(list(argv))
        return await super().spawn(argv, **kwargs)


def fake_settings(source: FakeAudioSource, clock: ManualClock, **extra: Any) -> dict[str, Any]:
    """A command source whose object is the shared fake: nothing is spawned."""

    return {
        "sources": {"mic": {"kind": "command", "argv": list(RECORDER_ARGV)}},
        "default_source": "mic",
        "_source_factory": lambda spec: source,
        "_sleeper": clock.sleep,
        **extra,
    }


def command_settings(runner: RecordingSubprocessRunner, clock: ManualClock, **extra: Any) -> dict[str, Any]:
    """The shipped command source over the recording runner."""

    return {
        "sources": {"mic": {"kind": "command", "argv": list(RECORDER_ARGV)}},
        "default_source": "mic",
        "_subprocess_runner": runner,
        "_sleeper": clock.sleep,
        **extra,
    }


class Harness:
    """One prepared module on a real executor over a shared store."""

    def __init__(
        self,
        context: ModuleContext,
        handle: AudioInputModule,
        clock: ManualClock,
        store: AttachmentStore,
        runner: CallTrackingRunner | None,
    ) -> None:
        self.context = context
        self.handle = handle
        self.clock = clock
        self.store = store
        self.runner = runner
        self.deadline = 10_000.0
        self.invocations: list[Any] = []
        self.provider_tasks: list[asyncio.Task[Any]] = []
        self._calls = 0
        original = handle._invoke_capture

        async def recorded(invocation: Any) -> Any:
            self.invocations.append(invocation)
            task = asyncio.current_task()
            assert task is not None
            self.provider_tasks.append(task)
            if runner is not None:
                runner.in_call += 1
            try:
                return await original(invocation)
            finally:
                if runner is not None:
                    runner.in_call -= 1

        handle._invoke_capture = recorded  # type: ignore[method-assign]

    @property
    def runtime(self) -> RuntimeContext:
        return self.context._runtime

    def call(self, arguments: dict[str, Any], destination: Destination = AUDIO) -> ActionCall:
        self._calls += 1
        return ActionCall(
            action_name=CAPTURE_ACTION,
            action_version=1,
            arguments=arguments,
            conversation_id="conversation-1",
            run_id=RUN_ID,
            call_id=f"call-{self._calls}",
            source_event_id="source-1",
            destination=destination,
            principal=PRINCIPAL,
            deadline=self.deadline,
            message_id="source-1",
        )

    async def capture(self, destination: Destination = AUDIO, **arguments: Any) -> ActionObservation:
        return await self.runtime.executor.invoke(self.call(arguments, destination))

    def start(self, **arguments: Any) -> "asyncio.Task[ActionObservation]":
        return asyncio.ensure_future(self.capture(**arguments))

    @property
    def spawns(self) -> int:
        return 0 if self.runner is None else self.runner.spawn_count


async def harness(
    source: FakeAudioSource | None = None,
    *,
    runner: CallTrackingRunner | None = None,
    settings: dict[str, Any] | Any = None,
    grant: bool = True,
    start: float = 1000.0,
    store_limits: dict[str, Any] | None = None,
    executor_sleeper: Any = None,
    prepare: bool = True,
    **extra: Any,
) -> Harness:
    clock = ManualClock(start)
    store = AttachmentStore(clock=clock, **(store_limits or STORE_LIMITS))
    runtime = runtime_context(clock=clock, attachments=store)
    if executor_sleeper is not None:
        # The same real executor, its timer on a scripted sleeper, so a test
        # decides when the executor notices the deadline (R10).
        runtime = dataclasses.replace(
            runtime,
            executor=ActionExecutor(
                runtime.actions,
                runtime.actions._authorization,
                supervision=runtime.supervision,
                counters=runtime.executor._counters,
                clock=clock,
                attachments=store,
                sleeper=executor_sleeper,
            ),
        )
    context = runtime.for_module(MODULE_NAME)
    if callable(settings):
        settings = settings(clock)
    if settings is None:
        if source is not None:
            settings = fake_settings(source, clock, **extra)
        else:
            if runner is None:
                runner = CallTrackingRunner()
            settings = command_settings(runner, clock, **extra)
    handle = await activate(context, settings, {})
    if prepare:
        await handle.prepare()
    if grant:
        context._runtime.actions._authorization.grant(
            AuthorizationRule(
                rule_id="grant-audio-capture",
                action_name=CAPTURE_ACTION,
                granted_permissions=("audio.capture",),
            )
        )
    return Harness(context, handle, clock, store, runner)


class ParkedSleeper:
    """The executor's timer, parked until a test fires it (R10 adoption)."""

    def __init__(self) -> None:
        self.delays: list[float] = []
        self._waiters: list[asyncio.Future[None]] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)
        waiter: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._waiters.append(waiter)
        await waiter

    def fire(self) -> None:
        for waiter in self._waiters:
            if not waiter.done():
                waiter.set_result(None)


def advance_to(clock: ManualClock, instant: float) -> None:
    clock.advance(instant - clock.now)
    assert clock.now == instant


async def finished(task: "asyncio.Future[Any]") -> Any:
    """Await *task*, failing (never hanging) if a regression leaves it parked."""

    return await asyncio.wait_for(task, 5.0)


async def spin() -> None:
    await settle(60)


def assert_failure(observation: ActionObservation, status: str, code: str) -> dict[str, Any]:
    assert observation.status == status, observation
    assert observation.result is None
    assert observation.parts == ()
    assert observation.error is not None
    assert observation.error["code"] == code, observation.error
    return dict(observation.error)


def assert_nothing_stored(h: Harness) -> None:
    assert h.store.object_count == 0
    assert h.store.total_bytes == 0


def assert_adopted_once(h: Harness, status: str) -> None:
    outcomes = h.runtime.executor.outcomes()
    assert list(outcomes) == [f"call-{h._calls}"]
    completed = events_of(h.runtime.bus, "action.completed")
    assert [event["payload"]["status"] for event in completed] == [status]


def data_chunk_size(segment: bytes) -> int:
    assert segment[36:40] == b"data"
    return struct.unpack("<I", segment[40:44])[0]


def streaming_wav(seconds: float) -> bytes:
    """A recorder's stdout: a ``LIST`` chunk before ``data`` and the streaming
    placeholder ``0xFFFFFFFF`` for both sizes, which a pipe cannot know."""

    canonical = wav_bytes(seconds)
    listing = b"LIST" + struct.pack("<I", 4) + b"INFO"
    body = canonical[:36] + listing + b"data" + struct.pack("<I", 0xFFFFFFFF) + canonical[44:]
    return body[:4] + struct.pack("<I", 0xFFFFFFFF) + body[8:]


# --------------------------------------------------------------------------- #
# AC8: a bounded segment into the run-leased store
# --------------------------------------------------------------------------- #


async def test_a_5_second_source_cut_to_3_seconds_is_stored_and_referenced() -> None:
    """AC8: 160 044 bytes in, ``seconds: 3`` → ``success``, one ``audio_ref``
    of 96 044 bytes, an attachment of exactly those bytes leased to the run,
    ``captured_at`` from the clock, ``transcription_status: "disabled"``; the
    stored bytes are ``RIFF``/``WAVE`` with a 96 000-byte data chunk."""

    five_seconds = wav_bytes(5.0)
    assert len(five_seconds) == 160_044
    source = FakeAudioSource(five_seconds)
    h = await harness(source, start=1234.5)

    observation = await h.capture(seconds=3)

    assert observation.status == "success", observation.error
    assert observation.result == {
        "source": "mic",
        "content_type": "audio/wav",
        "size": 96_044,
        "duration_ms": 3000,
        "sample_rate_hz": 16000,
        "channels": 1,
        "captured_at": 1234.5,
        "transcription_status": "disabled",
    }
    (part,) = observation.parts
    assert part["type"] == "audio_ref"
    assert part["size"] == 96_044
    assert part["duration_ms"] == 3000
    assert part["sample_rate_hz"] == 16000
    assert part["channels"] == 1
    assert part["content_type"] == "audio/wav"
    assert part["captured_at"] == 1234.5
    assert part["provider_id"] == PROVIDER_ID == "audio-input"
    assert "transcription" not in part

    ref = h.store.lookup(part["attachment_id"])
    assert ref is not None
    assert ref.run_id == RUN_ID
    assert ref.size == 96_044
    assert ref.content_type == "audio/wav"
    assert h.store.object_count == 1
    stored = h.store.get(ref)
    assert len(stored) == 96_044
    assert stored[:4] == b"RIFF" and stored[8:12] == b"WAVE"
    assert struct.unpack("<I", stored[4:8])[0] == 96_036
    assert data_chunk_size(stored) == 96_000
    assert stored[:44] == wav_bytes(3.0)[:44]
    assert stored[44:] == five_seconds[44 : 44 + 96_000]
    assert source.reads == [3]
    assert source.kills == 0
    assert observation.provenance["provider"] == PROVIDER_NAME
    assert observation.provenance["source"] == "mic"


async def test_the_shipped_command_source_reads_the_recorder_incrementally() -> None:
    """AC8 through the shipped command source: one spawn of the configured
    argv at the call, stdout read until the header and 96 000 data bytes are
    held, the recorder then terminated — never killed — and the same segment
    stored."""

    runner = CallTrackingRunner(wav_bytes(5.0))
    h = await harness(runner=runner)
    assert runner.spawns == []

    observation = await h.capture(seconds=3)

    assert observation.status == "success", observation.error
    assert observation.result["size"] == 96_044
    assert runner.spawns == [RECORDER_ARGV]
    assert runner.terminates == [1]
    assert runner.kills == []
    (process,) = runner.processes
    # Only what the segment needs was read: 2 chunks of 64 KiB, not 160 044.
    assert len(process._pending) == 160_044 - 2 * 65_536
    ref = h.store.lookup(observation.parts[0]["attachment_id"])
    assert data_chunk_size(h.store.get(ref)) == 96_000


async def test_a_streaming_header_is_rewritten_to_the_canonical_one() -> None:
    """Risk (header consistency): a recorder's placeholder sizes and a ``LIST``
    chunk before ``data`` are replaced by the canonical 44-byte header whose
    ``RIFF`` and ``data`` sizes match the bytes kept."""

    h = await harness(runner=CallTrackingRunner(streaming_wav(2.0)))

    observation = await h.capture(seconds=1)

    assert observation.status == "success", observation.error
    ref = h.store.lookup(observation.parts[0]["attachment_id"])
    stored = h.store.get(ref)
    assert stored == wav_bytes(1.0)
    assert observation.result["duration_ms"] == 1000


async def test_a_shorter_recording_is_stored_with_its_own_duration() -> None:
    h = await harness(FakeAudioSource(wav_bytes(1.5)))

    observation = await h.capture(seconds=3)

    assert observation.status == "success", observation.error
    assert observation.result["size"] == 48_044
    assert observation.result["duration_ms"] == 1500


async def test_a_file_source_is_re_read_at_each_call(tmp_path: Path) -> None:
    """R3: a ``file`` source is re-read at each call and cut to ``seconds``;
    a file removed after ``prepare`` is ``capture_failed`` naming no path."""

    path = tmp_path / "loop.wav"
    path.write_bytes(wav_bytes(5.0))
    h = await harness(
        settings=lambda clock: {
            "sources": {"loop": {"kind": "file", "path": str(path)}},
            "default_source": "loop",
            "_sleeper": clock.sleep,
        }
    )
    assert h.handle.usable_sources == {"loop"}

    first = await h.capture(seconds=3)
    assert first.status == "success", first.error
    assert first.result["size"] == 96_044

    path.write_bytes(wav_bytes(2.0))
    second = await h.capture(seconds=3)
    assert second.status == "success", second.error
    assert second.result["size"] == 64_044

    path.unlink()
    third = await h.capture(seconds=3)
    error = assert_failure(third, "error", ERROR_CAPTURE_FAILED)
    assert str(tmp_path) not in error["message"]
    assert h.store.object_count == 2


# --------------------------------------------------------------------------- #
# AC9: the segment convention and the bounds
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(wav_bytes(1.0, sample_rate=8000), id="8000-hz"),
        pytest.param(wav_bytes(1.0, channels=2), id="stereo"),
        pytest.param(wav_bytes(1.0, bits=8), id="8-bit"),
        pytest.param(b"0123456789abcdefghij", id="20-non-wav-bytes"),
    ],
)
async def test_a_segment_outside_the_convention_is_invalid_result_with_nothing_stored(
    body: bytes,
) -> None:
    """AC9: 8 000 Hz, stereo, 8-bit or 20 non-WAV bytes → ``error
    invalid_result`` with 0 attachments stored."""

    if body.startswith(b"0123"):
        assert len(body) == 20
    h = await harness(FakeAudioSource(body))

    observation = await h.capture(seconds=1)

    assert_failure(observation, "error", ERROR_INVALID_RESULT)
    assert_nothing_stored(h)


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(b"", id="empty"),
        pytest.param(wav_bytes(0.0), id="no-audio"),
        pytest.param(wav_bytes(1.0)[:36], id="no-data-chunk"),
    ],
)
def test_cut_segment_refuses_a_segment_without_audio(body: bytes) -> None:
    with pytest.raises(InvalidSegment):
        cut_segment(body, 3)


async def test_seconds_above_max_seconds_is_invalid_arguments_before_any_spawn() -> None:
    """AC9: with the default ``max_seconds: 30``, ``seconds: 40`` is ``error
    invalid_arguments`` with 0 processes spawned."""

    h = await harness()

    observation = await h.capture(seconds=40)

    assert_failure(observation, "error", ERROR_INVALID_ARGUMENTS)
    assert h.spawns == 0
    assert_nothing_stored(h)


@pytest.mark.parametrize(
    "arguments",
    [
        pytest.param({"seconds": 0}, id="zero"),
        pytest.param({"seconds": "3"}, id="string"),
        pytest.param({"seconds": 2.5}, id="float"),
        pytest.param({"seconds": True}, id="bool"),
        pytest.param({}, id="missing"),
        pytest.param({"seconds": 3, "source": "elsewhere"}, id="unknown-source"),
    ],
)
async def test_malformed_arguments_are_invalid_arguments_before_any_spawn(
    arguments: dict[str, Any],
) -> None:
    h = await harness()

    observation = await h.capture(**arguments)

    assert_failure(observation, "error", ERROR_INVALID_ARGUMENTS)
    assert h.spawns == 0
    assert_nothing_stored(h)


async def test_a_40_second_segment_over_max_bytes_is_attachment_refused() -> None:
    """AC9: ``max_seconds: 40``, ``max_bytes: 1_048_576`` and a valid 40 s
    source (1 280 044 bytes) → ``error attachment_refused``, 0 bytes stored."""

    forty = wav_bytes(40.0)
    assert len(forty) == 1_280_044
    h = await harness(FakeAudioSource(forty), max_seconds=40, max_bytes=1_048_576)

    observation = await h.capture(seconds=40)

    assert_failure(observation, "error", ERROR_ATTACHMENT_REFUSED)
    assert_nothing_stored(h)


async def test_a_30_second_segment_is_stored_whole_under_the_defaults() -> None:
    """AC9: with the defaults and ``seconds: 30`` the 960 044-byte segment is
    stored and ``size == 960_044``."""

    assert DEFAULT_MAX_BYTES == 1_048_576
    h = await harness(FakeAudioSource(wav_bytes(31.0)))

    observation = await h.capture(seconds=30)

    assert observation.status == "success", observation.error
    assert observation.result["size"] == 960_044
    assert observation.result["duration_ms"] == 30_000
    assert h.store.total_bytes == 960_044


async def test_a_refusal_by_the_store_itself_is_attachment_refused() -> None:
    """Risk: ``attachments.put`` refusing the segment on its own bound maps to
    ``attachment_refused`` too, with nothing stored."""

    h = await harness(
        FakeAudioSource(wav_bytes(5.0)),
        store_limits={**STORE_LIMITS, "max_object_bytes": 50_000},
    )

    observation = await h.capture(seconds=3)

    error = assert_failure(observation, "error", ERROR_ATTACHMENT_REFUSED)
    assert "max_object_bytes" in error["message"]
    assert_nothing_stored(h)


async def test_an_endless_recorder_is_read_within_a_bounded_buffer() -> None:
    """Risk: a recorder that streams without a readable header is read in
    chunks and stopped once the buffer reaches ``max_bytes`` plus header
    room — never held whole in memory — and nothing is stored."""

    endless = b"\x00" * (8 * 1024 * 1024)
    runner = CallTrackingRunner(endless)
    h = await harness(runner=runner, max_bytes=100_000)

    observation = await h.capture(seconds=3)

    assert_failure(observation, "error", ERROR_INVALID_RESULT)
    (process,) = runner.processes
    read = len(endless) - len(process._pending)
    assert read <= 100_000 + 4096 + 65_536
    assert runner.terminates == [1]
    assert_nothing_stored(h)


async def test_an_unstartable_recorder_is_capture_failed() -> None:
    h = await harness(runner=CallTrackingRunner(startable=False))

    observation = await h.capture(seconds=3)

    assert_failure(observation, "error", ERROR_CAPTURE_FAILED)
    assert_nothing_stored(h)


# --------------------------------------------------------------------------- #
# AC10 / AC39 module half: admission reserve, kill at the deadline itself
# --------------------------------------------------------------------------- #


async def test_seconds_beyond_the_admission_reserve_is_capture_too_long_before_any_spawn() -> None:
    """AC10: with 4 s left before the call deadline and ``grace_seconds: 2``,
    ``seconds: 3`` is ``error capture_too_long`` with 0 processes spawned;
    ``seconds: 2`` fits the reserve and is recorded."""

    h = await harness(runner=CallTrackingRunner(wav_bytes(5.0)), start=96.0, grace_seconds=2)
    h.deadline = DEADLINE

    observation = await h.capture(seconds=3)

    assert_failure(observation, "error", ERROR_CAPTURE_TOO_LONG)
    assert h.spawns == 0
    assert_nothing_stored(h)

    fits = await h.capture(seconds=2)
    assert fits.status == "success", fits.error
    assert h.spawns == 1


async def test_the_admission_reserve_reads_the_spec_timeout_too() -> None:
    """``expiry = min(call.deadline, now + timeout_seconds)``: with a far call
    deadline the 45 s spec budget bounds the request — 43 s fits a 2 s
    reserve and 44 s does not."""

    h = await harness(FakeAudioSource(wav_bytes(1.0)), max_seconds=60)

    assert_failure(await h.capture(seconds=44), "error", ERROR_CAPTURE_TOO_LONG)
    assert (await h.capture(seconds=43)).status == "success"


@pytest.mark.parametrize("path", ["watcher", "watcher-late-100.5", "executor-timer"])
async def test_a_silent_recorder_is_killed_at_the_deadline_and_the_record_adopted(path: str) -> None:
    """AC10/AC39 (module half): a gated recorder emitting nothing, call
    deadline ``t = 100.0``, ``grace_seconds: 2``: 0 kills at ``t = 98.0`` and
    at ``t = 99.99`` — the reserve is not applied to the kill — exactly 1
    kill at the deadline, the module's record stamped ``>= 100.0`` and
    adopted: ``error`` with ``code == "capture_timed_out"`` (never
    ``timed_out``), 0 attachments, one ``action.completed`` carrying
    ``status: error``, one record for the call. Three ways the deadline is
    noticed: the module's watcher with the executor's timer parked, the
    watcher woken late, and the executor's timer first (the
    ``CancelledError`` path)."""

    executor_timer = ParkedSleeper()
    source = FakeAudioSource(gated=True)
    h = await harness(source, start=90.0, grace_seconds=2, executor_sleeper=executor_timer)
    h.deadline = DEADLINE

    task = h.start(seconds=3)
    await wait_until(source.entered.is_set)
    await spin()
    assert executor_timer.delays == [10.0]

    advance_to(h.clock, 98.0)
    await spin()
    assert source.kills == 0
    assert not task.done()

    advance_to(h.clock, 99.99)
    await spin()
    assert source.kills == 0
    assert not task.done()

    if path == "watcher":
        advance_to(h.clock, 100.0)
        stamp = 100.0
    elif path == "watcher-late-100.5":
        advance_to(h.clock, 100.5)
        stamp = 100.5
    else:
        h.clock.now = 100.0
        executor_timer.fire()
        stamp = 100.0
    observation = await finished(task)
    await spin()

    assert source.kills == 1
    (invocation,) = h.invocations
    assert invocation.provider_completed_at == stamp
    assert invocation.provider_completed_at >= DEADLINE
    error = assert_failure(observation, "error", ERROR_CAPTURE_TIMED_OUT)
    assert error["code"] != ERROR_TIMED_OUT
    assert "timed out with emission" not in error["message"]
    assert_adopted_once(h, "error")
    assert_nothing_stored(h)


async def test_a_command_recorder_is_killed_once_at_the_deadline() -> None:
    """AC39 on the shipped command source: a recorder that emits nothing and
    ignores nothing is killed exactly once, by the watcher at ``expiry``."""

    runner = CallTrackingRunner(gated=True)
    executor_timer = ParkedSleeper()
    h = await harness(runner=runner, start=90.0, executor_sleeper=executor_timer)
    h.deadline = DEADLINE

    # 8 s: the recording's own duration timer (t = 98) terminates it first,
    # so the recorder is made to ignore termination.
    task = h.start(seconds=8)
    await wait_until(lambda: runner.processes)
    (process,) = runner.processes
    process.terminate = lambda: runner.terminates.append(process.pid)  # type: ignore[method-assign]
    await spin()

    advance_to(h.clock, 98.0)
    await spin()
    assert runner.terminates == [1]
    assert runner.kills == []
    advance_to(h.clock, 99.99)
    await spin()
    assert runner.kills == []
    assert not task.done()

    advance_to(h.clock, 100.0)
    observation = await finished(task)
    await spin()

    assert runner.kills == [1]
    assert process.returncode is not None
    assert_failure(observation, "error", ERROR_CAPTURE_TIMED_OUT)
    assert_adopted_once(h, "error")
    assert_nothing_stored(h)


@pytest.mark.parametrize("kind", ["fake-source", "command"])
async def test_a_cancellation_mid_capture_kills_and_ends_cancelled(kind: str) -> None:
    """AC10: a ``CancelledError`` reaching the provider at ``t = 50.0`` (its
    deadline at ``100.0``) is caught: 1 kill, and the returned record —
    adopted by the executor — is ``cancelled`` with 0 attachments."""

    source = FakeAudioSource(gated=True) if kind == "fake-source" else None
    runner = None if source is not None else CallTrackingRunner(gated=True)
    h = await harness(source, runner=runner, start=48.0)
    h.deadline = DEADLINE

    task = h.start(seconds=3)
    await wait_until(lambda: bool(h.provider_tasks))
    await spin()
    advance_to(h.clock, 50.0)
    await spin()
    assert not task.done()
    h.provider_tasks[0].cancel()
    observation = await finished(task)
    await spin()

    assert_failure(observation, "cancelled", "cancelled")
    assert_adopted_once(h, "cancelled")
    if source is not None:
        assert source.kills == 1
    else:
        assert runner is not None
        assert runner.kills == [1]
    assert_nothing_stored(h)


async def test_a_drain_started_at_the_deadline_kills_and_ends_cancelled() -> None:
    """AC10/decision 1: a drain started at ``t = 100.0`` (before the watcher
    is woken) kills the recorder — the first trigger fixes ``cancelled`` —
    and the record, stamped at the deadline, is adopted; a call arriving
    during the shutdown ends ``cancelled`` before any read."""

    source = FakeAudioSource(gated=True)
    h = await harness(source, start=90.0)
    h.deadline = DEADLINE

    task = h.start(seconds=3)
    await wait_until(source.entered.is_set)
    advance_to(h.clock, 99.99)
    await spin()
    h.clock.now = 100.0
    await finished(asyncio.ensure_future(h.handle.drain(5.0)))
    observation = await finished(task)
    await spin()

    assert_failure(observation, "cancelled", "cancelled")
    assert h.invocations[0].provider_completed_at == 100.0
    assert_adopted_once(h, "cancelled")
    assert source.kills == 1
    assert_nothing_stored(h)

    h.clock.advance(1.0)  # the parked watcher wakes to nothing
    await spin()
    assert source.kills == 1
    h.deadline = 10_000.0
    later = await h.capture(seconds=1)
    assert_failure(later, "cancelled", "cancelled")
    assert source.reads == [3]
    await h.handle.close()


async def test_close_kills_a_running_recorder_and_withdraws_readiness() -> None:
    source = FakeAudioSource(gated=True)
    h = await harness(source)
    assert CAPTURE_ACTION in h.runtime.actions.registered_ready()

    task = h.start(seconds=3)
    await wait_until(source.entered.is_set)
    await h.handle.close()
    observation = await finished(task)

    assert_failure(observation, "cancelled", "cancelled")
    assert source.kills == 1
    assert CAPTURE_ACTION not in h.runtime.actions.registered_ready()


async def test_the_runs_release_and_the_ttl_leave_no_attachment_behind() -> None:
    """AC10: after the run's terminal record (``store.release(run_id)``)
    every attachment of the run is gone; a lease that outlived a crashed run
    is reaped by the TTL on the injected clock."""

    h = await harness(FakeAudioSource(wav_bytes(5.0)))
    first = await h.capture(seconds=3)
    second = await h.capture(seconds=2)
    ids = [first.parts[0]["attachment_id"], second.parts[0]["attachment_id"]]
    assert all(h.store.lookup(attachment_id) is not None for attachment_id in ids)

    freed = h.store.release(RUN_ID)
    assert freed.objects == 2
    assert all(h.store.lookup(attachment_id) is None for attachment_id in ids)
    assert_nothing_stored(h)

    # A crashed run never releases: the TTL reaps its lease on the clock.
    crashed = await h.capture(seconds=3)
    ref = h.store.lookup(crashed.parts[0]["attachment_id"])
    assert ref is not None
    h.clock.advance(STORE_LIMITS["ttl_seconds"] + 1.0)
    with pytest.raises(AttachmentExpired):
        h.store.get(ref)
    assert h.store.object_count == 0
    assert h.store.lookup(ref.attachment_id) is None


# --------------------------------------------------------------------------- #
# AC12: on demand only
# --------------------------------------------------------------------------- #


async def test_without_a_rule_the_capture_is_refused_before_the_provider() -> None:
    """AC12: default-deny, reads included: ``refused`` with 0 spawns, 0
    attachments and the provider never entered."""

    h = await harness(grant=False)

    observation = await h.capture(seconds=3)

    assert observation.status == "refused", observation
    assert h.invocations == []
    assert h.spawns == 0
    assert_nothing_stored(h)


async def test_prepare_starts_no_task_and_spawns_nothing() -> None:
    """AC12: 0 supervised tasks and 0 spawns after activation and
    ``prepare``; the handle offers no input hook to start; a spawn happens
    only inside a call, and the call leaves no task behind."""

    runner = CallTrackingRunner(wav_bytes(2.0))
    h = await harness(runner=runner, prepare=False)
    assert h.context.tasks.active == 0
    await h.handle.prepare()
    assert h.context.tasks.active == 0
    assert runner.spawn_count == 0
    assert not hasattr(h.handle, "start_inputs")

    observation = await h.capture(seconds=1)
    assert observation.status == "success", observation.error
    assert runner.spawn_count == 1
    assert h.context.tasks.active == 0


def test_manifest_declares_an_on_demand_read_with_no_role() -> None:
    """AC12/R3: ``produces: []``, ``consumes: []``, no middleware, no
    lifecycle role; the one ``read`` action on ``*/*/audio`` with its
    permission, no delivery capability, 45 s, ``idempotency: none``; the
    settings and the credential R3 names, and no lead/margin key (AC41)."""

    manifest = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert manifest["name"] == MODULE_NAME
    assert manifest["manifest_version"] == 2
    assert manifest["produces"] == []
    assert manifest["consumes"] == []
    assert manifest["middleware"] is False
    assert manifest["lifecycle"] == {"roles": []}
    assert manifest["credentials"] == ["transcription.api_key"]
    assert manifest["settings_validator"] == "validate_settings"
    properties = manifest["settings_schema"]["properties"]
    assert set(properties) == {
        "sources",
        "default_source",
        "max_seconds",
        "max_bytes",
        "grace_seconds",
        "transcription",
        "required",
        "limits",
    }
    assert set(properties["transcription"]["properties"]) == {
        "enabled",
        "endpoint",
        "model",
        "api_key",
        "language",
        "timeout_seconds",
        "max_chars",
    }
    (action,) = manifest["actions"]
    assert action["name"] == CAPTURE_ACTION
    assert action["nature"] == "read"
    assert action["required_permissions"] == ["audio.capture"]
    assert action["supported_destinations"] == [
        {"platform": "*", "channel_id": "*", "scope": "audio"}
    ]
    assert "delivery" not in action
    assert action["timeout_seconds"] == 45
    assert action["idempotency"] == "none"
    assert set(action["argument_schema"]["properties"]) == {"seconds", "source"}
    assert action["argument_schema"]["properties"]["seconds"]["type"] == "integer"
    assert action["argument_schema"]["required"] == ["seconds"]
    assert set(action["result_schema"]["required"]) == {
        "source",
        "content_type",
        "size",
        "duration_ms",
        "sample_rate_hz",
        "channels",
        "captured_at",
        "transcription_status",
    }


def test_pyproject_ships_the_manifest() -> None:
    """R9 (this module's line): the package-data guard of ``tests/test_main.py``
    requires it as soon as the directory exists."""

    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '"modules.audio_input" = ["module.yaml"]' in text


def test_module_names_no_platform() -> None:
    for path in (MODULE_DIR / "__init__.py", MANIFEST_PATH):
        assert "twitch" not in path.read_text(encoding="utf-8").lower()


# --------------------------------------------------------------------------- #
# Readiness at prepare and the required policy
# --------------------------------------------------------------------------- #


def degraded_payloads(h: Harness) -> list[dict[str, Any]]:
    return [event["payload"] for event in events_of(h.runtime.bus, "module.degraded")]


async def test_an_unusable_source_beside_a_usable_one_is_reported_and_the_action_bound() -> None:
    h = await harness(
        settings=lambda clock: {
            "sources": {
                "mic": {"kind": "command", "argv": list(RECORDER_ARGV)},
                "mix": {"kind": "command", "argv": [MISSING_RECORDER, "--secret-arg"]},
            },
            "default_source": "mic",
            "_subprocess_runner": CallTrackingRunner(),
            "_sleeper": clock.sleep,
        }
    )

    assert h.handle.usable_sources == {"mic"}
    assert CAPTURE_ACTION in h.runtime.actions.registered_ready()
    (payload,) = degraded_payloads(h)
    assert "mix" in payload["reason"] and "'mic'" not in payload["reason"]
    # The action stays bound: no capability is degraded.
    assert payload.get("capabilities", []) == []
    for text in trace_texts(h.runtime.bus):
        assert MISSING_RECORDER not in text and "--secret-arg" not in text


async def test_no_usable_source_leaves_the_action_unbound_and_degraded(tmp_path: Path) -> None:
    runner = CallTrackingRunner()
    h = await harness(
        settings=lambda clock: {
            "sources": {
                "mix": {"kind": "command", "argv": [MISSING_RECORDER]},
                "loop": {"kind": "file", "path": str(tmp_path / "absent.wav")},
            },
            "default_source": "mix",
            "_subprocess_runner": runner,
            "_sleeper": clock.sleep,
        },
        runner=runner,
    )

    assert h.handle.usable_sources == frozenset()
    assert CAPTURE_ACTION in h.runtime.actions.discovered()
    assert h.runtime.actions.bindings(CAPTURE_ACTION) == ()
    (payload,) = degraded_payloads(h)
    assert payload["capabilities"] == [CAPTURE_ACTION]
    assert str(tmp_path) not in payload["reason"]

    observation = await h.capture(seconds=1)
    assert observation.status in {"refused", "error"}
    assert h.invocations == []
    assert runner.spawn_count == 0


async def test_required_with_an_unusable_source_fails_prepare_naming_the_field() -> None:
    with pytest.raises(AudioInputModuleError) as raised:
        await harness(
            settings=lambda clock: {
                "sources": {
                    "mic": {"kind": "command", "argv": list(RECORDER_ARGV)},
                    "mix": {"kind": "command", "argv": [MISSING_RECORDER]},
                },
                "default_source": "mic",
                "required": True,
                "_subprocess_runner": CallTrackingRunner(),
                "_sleeper": clock.sleep,
            }
        )
    message = str(raised.value)
    assert "audio_input" in message and "sources.mix" in message
    assert MISSING_RECORDER not in message


# --------------------------------------------------------------------------- #
# Settings and activation
# --------------------------------------------------------------------------- #


def _valid() -> dict[str, Any]:
    return {
        "sources": {
            "mic": {"kind": "command", "argv": ["/usr/bin/recorder-secret", "-f", "S16_LE"]},
            "loop": {"kind": "file", "path": "/home/someone/secret-loop.wav"},
        },
        "default_source": "mic",
        "max_seconds": 30,
        "max_bytes": 1_048_576,
        "grace_seconds": 2,
        "transcription": {
            "enabled": False,
            "endpoint": "",
            "model": "",
            "api_key": "stt-key-secret",
            "language": "fr",
            "timeout_seconds": 10,
            "max_chars": 2000,
        },
        "required": False,
        "limits": {"attachments": {}},
    }


def test_settings_hook_accepts_the_declared_settings_and_the_minimum() -> None:
    assert validate_settings(_valid()) == []
    assert (
        validate_settings(
            {"sources": {"mic": {"kind": "file", "path": "x.wav"}}, "default_source": "mic"}
        )
        == []
    )


@pytest.mark.parametrize(
    ("mutate", "field"),
    [
        (lambda s: s.pop("sources"), "sources"),
        (lambda s: s.update(sources={}), "sources"),
        (lambda s: s["sources"].update(mic={"kind": "stream"}), "sources.mic.kind"),
        (lambda s: s["sources"]["mic"].update(argv=[]), "sources.mic.argv"),
        (lambda s: s["sources"]["mic"].update(command_timeout_seconds=5), "sources.mic.command_timeout_seconds"),
        (lambda s: s["sources"]["loop"].update(path=""), "sources.loop.path"),
        (lambda s: s.update(default_source="elsewhere"), "default_source"),
        (lambda s: s.update(max_seconds=0), "max_seconds"),
        (lambda s: s.update(max_bytes=True), "max_bytes"),
        (lambda s: s.update(grace_seconds=-1), "grace_seconds"),
        (lambda s: s.update(required="yes"), "required"),
        (lambda s: s["transcription"].update(enabled="no"), "transcription.enabled"),
        (lambda s: s["transcription"].update(timeout_seconds=0), "transcription.timeout_seconds"),
        (lambda s: s["transcription"].update(max_chars=0), "transcription.max_chars"),
        (lambda s: s["transcription"].update(api_key=5), "transcription.api_key"),
        (lambda s: s["transcription"].update(lead=1), "transcription.lead"),
        (lambda s: s.update(deadline_margin=1), "deadline_margin"),
        (lambda s: s.update(_sleeper="not callable"), "_sleeper"),
    ],
)
def test_settings_hook_names_each_offending_field_without_its_value(mutate: Any, field: str) -> None:
    settings = _valid()
    mutate(settings)

    diagnostics = validate_settings(settings)

    assert any(f"'{field}'" in diagnostic for diagnostic in diagnostics), diagnostics
    for diagnostic in diagnostics:
        assert diagnostic.startswith(f"module {MODULE_NAME!r}")
        for secret in ("recorder-secret", "secret-loop", "stt-key-secret"):
            assert secret not in diagnostic


async def test_activation_refuses_a_context_without_actions_store_or_clock() -> None:
    """``activate`` refuses a context lacking the actions facade, the
    attachment store or the clock, with one value-free diagnostic."""

    context = runtime_context(
        clock=ManualClock(), attachments=AttachmentStore(clock=ManualClock(), **STORE_LIMITS)
    ).for_module(MODULE_NAME)

    class _Missing:
        def __init__(self, **surfaces: Any) -> None:
            self.__dict__.update(surfaces)

    for broken in (
        _Missing(attachments=context.attachments, clock=context.clock),
        _Missing(actions=context.actions, clock=context.clock),
        _Missing(actions=context.actions, attachments=object(), clock=context.clock),
        _Missing(actions=context.actions, attachments=context.attachments, clock=None),
    ):
        with pytest.raises(AudioInputModuleError, match="runtime context is invalid"):
            await activate(broken, _valid(), {})
    with pytest.raises(AudioInputModuleError, match="settings were refused") as raised:
        await activate(context, {**_valid(), "max_seconds": 0}, {})
    assert "recorder-secret" not in str(raised.value)


async def test_the_loader_discovers_the_manifest_and_prepare_binds_the_declared_spec() -> None:
    """R7/R9: the real loader validates the manifest and runs the hook with
    the reserved ``limits`` block; ``prepare`` binds the very spec
    discovered; a granted call succeeds on a second platform; the declared
    credential is redacted; ``stop`` withdraws readiness."""

    clock = ManualClock()
    store = AttachmentStore(clock=clock, **STORE_LIMITS)
    context = runtime_context(clock=clock, attachments=store)
    runner = CallTrackingRunner(wav_bytes(2.0))
    loader = ModuleLoader(context.bus, ROOT / "modules", context=context, environ={})
    settings = _valid()
    settings["sources"] = {"mic": {"kind": "command", "argv": list(RECORDER_ARGV)}}
    activations = await loader.activate_enabled(
        {
            "enabled_modules": [MODULE_NAME],
            "modules": {
                MODULE_NAME: {**settings, "_subprocess_runner": runner, "_sleeper": clock.sleep}
            },
        }
    )
    (activation,) = activations
    assert activation.manifest_version == 2
    assert activation.roles == frozenset()
    assert CAPTURE_ACTION in context.actions.discovered()
    assert context.actions.bindings(CAPTURE_ACTION) == ()

    diagnostics: list[str] = []
    coordinator = PhaseCoordinator(activations, tasks=context.tasks, reporter=diagnostics.append)
    assert (await coordinator.start()).status == 0
    (binding,) = context.actions.bindings(CAPTURE_ACTION)
    assert binding.spec == context.actions.discovered()[CAPTURE_ACTION]
    assert CAPTURE_ACTION in context.actions.registered_ready()
    assert runner.spawn_count == 0

    context.actions._authorization.grant(
        AuthorizationRule(
            rule_id="grant-capture",
            action_name=CAPTURE_ACTION,
            granted_permissions=("audio.capture",),
        )
    )
    runner.in_call += 1  # a direct executor call: the spawn is inside it
    try:
        observation = await context.executor.invoke(
            ActionCall(
                action_name=CAPTURE_ACTION,
                action_version=1,
                arguments={"seconds": 1},
                conversation_id="conversation-1",
                run_id=RUN_ID,
                call_id="call-loader",
                source_event_id="source-1",
                destination=FAKE_AUDIO,
                principal=PRINCIPAL,
                deadline=10_000.0,
                message_id="source-1",
            )
        )
    finally:
        runner.in_call -= 1
    assert observation.status == "success", observation.error
    assert observation.result["size"] == 32_044
    assert loader.redacted_credentials == 1

    assert (await coordinator.stop()).status == 0
    assert diagnostics == []
    assert CAPTURE_ACTION not in context.actions.registered_ready()
    assert all("stt-key-secret" not in text for text in trace_texts(context.bus))


# --------------------------------------------------------------------------- #
# The shipped runner against real processes
# --------------------------------------------------------------------------- #

#: Writes a 5 s 16 kHz mono 16-bit WAV to stdout, then exits.
WAV_WRITER = (
    "import struct, sys; n = 5 * 32000; "
    "sys.stdout.buffer.write(b'RIFF' + struct.pack('<I', 36 + n) + b'WAVEfmt ' "
    "+ struct.pack('<IHHIIHH', 16, 1, 1, 16000, 32000, 2, 16) + b'data' "
    "+ struct.pack('<I', n) + bytes(n))"
)
#: Emits nothing and never exits on its own: only a signal ends it.
SILENT_RECORDER = "import select; select.select([], [], [])"


def real_runner(processes: list[Any], spawned: asyncio.Event) -> Any:
    async def runner(*argv: str, **kwargs: Any) -> Any:
        process = await spawn_recorder(*argv, **kwargs)
        processes.append(process)
        spawned.set()
        return process

    return runner


def real_settings(argv: list[str], runner: Any) -> Any:
    return lambda clock: {
        "sources": {"mic": {"kind": "command", "argv": argv}},
        "default_source": "mic",
        "_subprocess_runner": runner,
        "_sleeper": clock.sleep,
        "grace_seconds": 0,
    }


async def test_the_shipped_runner_records_a_real_process() -> None:
    """The default runner spawns the configured program; its stdout is read
    until the segment is held, the recorder stopped and reaped."""

    processes: list[Any] = []
    spawned = asyncio.Event()
    h = await harness(
        settings=real_settings(
            [sys.executable, "-c", WAV_WRITER], real_runner(processes, spawned)
        )
    )

    observation = await finished(asyncio.ensure_future(h.capture(seconds=3)))

    assert observation.status == "success", observation.error
    assert observation.result["size"] == 96_044
    (process,) = processes
    assert process.returncode is not None


async def test_the_shipped_runner_kills_a_silent_real_recorder_at_the_deadline() -> None:
    """AC39 against a real process: a recorder that emits nothing is killed
    (its whole process group) when the clock reaches the deadline, the call
    ends ``capture_timed_out`` with nothing stored and the process is reaped."""

    processes: list[Any] = []
    spawned = asyncio.Event()
    h = await harness(
        settings=real_settings(
            [sys.executable, "-c", SILENT_RECORDER], real_runner(processes, spawned)
        ),
        start=90.0,
    )
    h.deadline = DEADLINE

    task = h.start(seconds=10)
    await asyncio.wait_for(spawned.wait(), 5.0)
    (process,) = processes
    advance_to(h.clock, 99.99)
    await spin()
    assert process.returncode is None
    assert not task.done()

    advance_to(h.clock, DEADLINE)
    observation = await finished(task)
    await finished(asyncio.ensure_future(process.wait()))

    assert process.returncode is not None and process.returncode < 0
    assert_failure(observation, "error", ERROR_CAPTURE_TIMED_OUT)
    assert_nothing_stored(h)


#: Streams a WAV header and then audio without end, ignoring SIGTERM: only a
#: closed stdout (or a kill) stops it.
ENDLESS_STUBBORN_RECORDER = (
    "import signal, struct, sys; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
    "out = sys.stdout.buffer; n = 0x7FFFFFF0; "
    "out.write(b'RIFF' + struct.pack('<I', 36 + n) + b'WAVEfmt ' "
    "+ struct.pack('<IHHIIHH', 16, 1, 1, 16000, 32000, 2, 16) + b'data' "
    "+ struct.pack('<I', n))\n"
    "while True: out.write(bytes(65536))"
)
#: A silent recorder that holds the FIFO ``argv[1]`` open for writing and
#: announces itself with one byte: the FIFO reads EOF once it has ended.
HELD_CHILD = (
    "import select, sys; alive = open(sys.argv[1], 'wb', buffering=0); "
    "alive.write(b'r'); select.select([], [], [])"
)
#: Starts :data:`HELD_CHILD` in its own process group and exits, leaving the
#: child holding stdout.
LEAVING_WRAPPER = (
    "import subprocess, sys; "
    f"subprocess.Popen([sys.executable, '-c', {HELD_CHILD!r}, sys.argv[1]])"
)


async def _readable(fd: int) -> None:
    """Return once *fd* is readable, failing (never hanging) after 5 s."""

    loop = asyncio.get_running_loop()
    ready = loop.create_future()
    loop.add_reader(fd, lambda: ready.done() or ready.set_result(None))
    try:
        await asyncio.wait_for(ready, 5.0)
    finally:
        loop.remove_reader(fd)


async def test_the_shipped_runner_is_not_held_by_a_fast_recorder_after_its_segment() -> None:
    """A recorder streaming faster than it is read — and ignoring SIGTERM —
    does not park the call once the segment is held: its stdout is closed
    before it is awaited, so the capture succeeds without the deadline."""

    processes: list[Any] = []
    spawned = asyncio.Event()
    h = await harness(
        settings=real_settings(
            [sys.executable, "-c", ENDLESS_STUBBORN_RECORDER], real_runner(processes, spawned)
        )
    )

    observation = await finished(asyncio.ensure_future(h.capture(seconds=1)))

    assert observation.status == "success", observation.error
    assert observation.result["size"] == 32_044
    (process,) = processes
    assert process.returncode is not None


async def test_the_shipped_runner_kills_a_child_left_by_an_exited_wrapper(tmp_path: Path) -> None:
    """A wrapper that exits while its recording child still holds stdout is
    reaped first; the kill at the deadline still reaches the child through
    the process group."""

    alive = tmp_path / "alive"
    os.mkfifo(alive)
    fd = os.open(alive, os.O_RDONLY | os.O_NONBLOCK)
    try:
        processes: list[Any] = []
        spawned = asyncio.Event()
        h = await harness(
            settings=real_settings(
                [sys.executable, "-c", LEAVING_WRAPPER, str(alive)],
                real_runner(processes, spawned),
            ),
            start=90.0,
        )
        h.deadline = DEADLINE

        task = h.start(seconds=10)
        await asyncio.wait_for(spawned.wait(), 5.0)
        (process,) = processes
        await _readable(fd)
        assert os.read(fd, 1) == b"r"
        # ``wait()`` would park until the child closes stdout: the exit is
        # observed on ``returncode``.
        await wait_until(lambda: process.returncode is not None, turns=200_000)
        assert process.returncode == 0
        assert select.select([fd], [], [], 0)[0] == []  # the child still runs
        assert not task.done()

        advance_to(h.clock, DEADLINE)
        observation = await finished(task)

        assert_failure(observation, "error", ERROR_CAPTURE_TIMED_OUT)
        assert_nothing_stored(h)
        await _readable(fd)
        assert os.read(fd, 1) == b""  # the child ended with the group
    finally:
        os.close(fd)


# --------------------------------------------------------------------------- #
# P11: the optional transcription (AC11, AC42, AC29)
# --------------------------------------------------------------------------- #

STT_ENDPOINT = "http://stt.invalid:8443/v1/audio/transcriptions"
STT_KEY = "stt-key-7e2d9a41c6"
STT_MODEL = "stt-model-under-test"
#: What the prepare-time probe is answered with; every scripted transport
#: below answers it first.
PROBE_OK: dict[str, Any] = {"text": ""}


class LateAnswerTransport(ScriptedTranscriptionTransport):
    """A transport whose :data:`HELD` answers a test hands over when it chooses.

    Unlike :meth:`release`, :meth:`deliver` still hands the answer over after
    the module abandoned the request — the answer arrives at the transport and
    nobody is left to take it, as a real endpoint answering late would.
    """

    def __init__(self, *answers: Any) -> None:
        super().__init__(*answers)
        self.gates: list[asyncio.Future[Any]] = []

    async def post(self, url: str, **kwargs: Any) -> Any:
        if not (self.answers and self.answers[0] is HELD):
            return await super().post(url, **kwargs)
        self.answers.pop(0)
        self.requests.append(self._record(url, kwargs))
        gate: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self.gates.append(gate)
        return self._normalise(await asyncio.shield(gate))

    @property
    def waiting(self) -> int:
        return sum(1 for gate in self.gates if not gate.done())

    def deliver(self, answer: Any) -> None:
        for gate in self.gates:
            if not gate.done():
                gate.set_result(answer)
                return
        raise AssertionError("no held request to answer")


def stt_settings(**extra: Any) -> dict[str, Any]:
    return {
        "enabled": True,
        "endpoint": STT_ENDPOINT,
        "model": STT_MODEL,
        "api_key": STT_KEY,
        **extra,
    }


async def stt_harness(
    transport: ScriptedTranscriptionTransport,
    source: FakeAudioSource | None = None,
    *,
    stt: dict[str, Any] | None = None,
    **kwargs: Any,
) -> Harness:
    return await harness(
        source if source is not None else FakeAudioSource(wav_bytes(5.0)),
        transcription=stt_settings(**(stt or {})),
        _transcription_transport=transport,
        **kwargs,
    )


def call_requests(transport: ScriptedTranscriptionTransport) -> list[dict[str, Any]]:
    """The requests of the calls: the first request is the prepare-time probe."""

    probe, *rest = transport.requests
    assert probe["file"] == probe_segment()
    return rest


def stored_bytes(h: Harness, part: dict[str, Any]) -> bytes:
    ref = h.store.lookup(part["attachment_id"])
    assert ref is not None
    return h.store.get(ref)


def assert_captured(
    h: Harness, observation: ActionObservation, transcription_status: str
) -> dict[str, Any]:
    """``success`` with its ``audio_ref`` leased to the run and the given status."""

    assert observation.status == "success", observation.error
    assert observation.error is None
    assert observation.result is not None
    assert observation.result["transcription_status"] == transcription_status
    (part,) = observation.parts
    assert part["type"] == "audio_ref"
    assert part["size"] == 96_044
    ref = h.store.lookup(part["attachment_id"])
    assert ref is not None and ref.run_id == RUN_ID and ref.size == 96_044
    return dict(part)


def timeouts_counted(h: Harness) -> int:
    return h.runtime.supervision.snapshot().get(COUNTER_ACTION_TIMEOUTS, 0)


def test_the_probe_segment_is_generated_silence_of_8044_bytes() -> None:
    """R3: the transcription probe's 0.25 s silent segment is generated — 8 044
    bytes, the canonical 16 kHz mono 16-bit header over zeros."""

    segment = probe_segment()
    assert len(segment) == 8_044
    assert segment == silent_wav(0.25)
    assert cut_segment(segment, 1).duration_ms == 250


@pytest.mark.parametrize("language", [None, "fr"])
async def test_a_transcription_answer_is_dated_and_carried_by_the_audio_ref(
    language: str | None,
) -> None:
    """AC11: a scripted transport answering ``{"text": "bonjour à tous"}`` →
    ``transcription == {text, transcribed_at, provider_id, truncated:
    false}``, ``transcription_status == "ok"``, exactly 1 request carrying the
    stored 96 044-byte WAV as a file, ``model``, ``language`` when set and the
    bearer header; the key appears in no trace."""

    transport = ScriptedTranscriptionTransport(PROBE_OK, {"text": "bonjour à tous"})
    stt = {"language": language} if language is not None else {}
    h = await stt_harness(transport, stt=stt, start=1234.5)

    observation = await h.capture(seconds=3)

    part = assert_captured(h, observation, "ok")
    assert part["transcription"] == {
        "text": "bonjour à tous",
        "transcribed_at": 1234.5,
        "provider_id": TRANSCRIPTION_PROVIDER_ID,
        "truncated": False,
    }
    (request,) = call_requests(transport)
    assert request["url"] == STT_ENDPOINT
    assert len(request["file"]) == 96_044
    assert request["file"] == stored_bytes(h, part)
    assert request["filename"]
    assert request["model"] == STT_MODEL
    assert request["language"] == language
    assert request["headers"]["Authorization"] == f"Bearer {STT_KEY}"
    assert_adopted_once(h, "success")
    for text in trace_texts(h.runtime.bus):
        assert STT_KEY not in text
    await h.handle.close()
    assert transport.close_calls == 1


async def test_a_long_transcription_is_cut_to_max_chars() -> None:
    """AC11: a 2 500-character answer with ``max_chars: 2000`` → 2 000
    characters, ``truncated: true``."""

    transport = ScriptedTranscriptionTransport(PROBE_OK, {"text": "é" * 2500})
    h = await stt_harness(transport, stt={"max_chars": 2000})

    part = assert_captured(h, await h.capture(seconds=3), "ok")

    assert part["transcription"]["text"] == "é" * 2000
    assert part["transcription"]["truncated"] is True


@pytest.mark.parametrize(
    ("answer", "status"),
    [
        (OSError("connection refused by 10.0.0.7:8443"), "failed:stt_unavailable"),
        (500, "failed:stt_failed"),
        (HELD, "failed:stt_timed_out"),
        ({"segments": []}, "failed:invalid_transcription"),
        ({"text": 42}, "failed:invalid_transcription"),
        (FakeBytesResponse(200, b"not json at all"), "failed:invalid_transcription"),
        (FakeBytesResponse(200, ConnectionResetError("reset mid-body")), "failed:stt_unavailable"),
    ],
    ids=[
        "transport-exception",
        "http-500",
        "no-answer",
        "no-text",
        "text-not-string",
        "not-json",
        "body-lost-after-200",
    ],
)
async def test_a_failed_transcription_leaves_the_capture_a_success(answer: Any, status: str) -> None:
    """AC11: a transport exception, an HTTP 500, no answer within
    ``timeout_seconds`` on the clock (not earlier), a 2xx body lost
    mid-read (a transport failure) and a 2xx body without a string ``text``
    each yield ``success`` with the ``audio_ref``,
    ``transcription`` absent and their own status; exactly 1 request."""

    transport = ScriptedTranscriptionTransport(PROBE_OK, answer)
    source = FakeAudioSource(wav_bytes(5.0))
    h = await stt_harness(transport, source)

    task = h.start(seconds=3)
    if answer is HELD:
        await wait_until(lambda: transport.held == 1)
        h.clock.advance(9.99)
        await spin()
        assert not task.done()
        h.clock.advance(0.01)
    observation = await finished(task)

    part = assert_captured(h, observation, status)
    assert "transcription" not in part
    assert len(call_requests(transport)) == 1
    assert source.kills == 0
    assert h.store.object_count == 1
    assert_adopted_once(h, "success")
    if answer is HELD:
        assert transport.held == 0  # the request was abandoned
        assert h.invocations[0].provider_completed_at == 1010.0


@pytest.mark.parametrize("stored_at", [99.5, 99.0], ids=["0.5-s-left", "exactly-1-s-left"])
async def test_an_exhausted_window_sends_nothing_and_is_skipped(stored_at: float) -> None:
    """AC11/AC42: a segment stored with 0.5 s — or exactly 1 s — left before
    the call deadline has no transcription window: 0 requests,
    ``skipped:deadline``, still ``success`` with the ``audio_ref``."""

    transport = ScriptedTranscriptionTransport(PROBE_OK)
    source = FakeAudioSource(wav_bytes(5.0), gated=True)
    h = await stt_harness(transport, source, start=90.0)
    h.deadline = DEADLINE

    task = h.start(seconds=3)
    await wait_until(source.entered.is_set)
    advance_to(h.clock, stored_at)
    source.release()
    observation = await finished(task)

    part = assert_captured(h, observation, "skipped:deadline")
    assert "transcription" not in part
    assert call_requests(transport) == []
    assert h.invocations[0].provider_completed_at == stored_at
    assert_adopted_once(h, "success")


async def boundary_harness(
    *, executor_sleeper: Any = None
) -> tuple[Harness, LateAnswerTransport, FakeAudioSource, "asyncio.Task[ActionObservation]"]:
    """AC42 setup: call deadline ``t = 100.0``, ``timeout_seconds: 10``, the
    segment stored at ``t = 95.0``, the one request held by the transport."""

    transport = LateAnswerTransport(PROBE_OK, HELD)
    source = FakeAudioSource(wav_bytes(5.0), gated=True)
    h = await stt_harness(
        transport,
        source,
        stt={"timeout_seconds": 10},
        start=90.0,
        executor_sleeper=executor_sleeper,
    )
    h.deadline = DEADLINE
    task = h.start(seconds=3)
    await wait_until(source.entered.is_set)
    advance_to(h.clock, 95.0)
    source.release()
    await wait_until(lambda: transport.waiting == 1)
    await spin()
    assert len(call_requests(transport)) == 1
    return h, transport, source, task


def assert_window_closed(
    h: Harness, observation: ActionObservation, source: FakeAudioSource, stamp: float
) -> None:
    """AC42's record: ``success`` with the ``audio_ref``, no transcription,
    ``failed:stt_timed_out``, stamped *stamp* ``< expires_at`` and adopted by
    the in-time rule — one record, no timeout counted, nothing killed, the
    attachment still leased to the run."""

    part = assert_captured(h, observation, "failed:stt_timed_out")
    assert "transcription" not in part
    (invocation,) = h.invocations
    assert invocation.provider_completed_at == stamp
    assert invocation.provider_completed_at < DEADLINE
    assert_adopted_once(h, "success")
    assert timeouts_counted(h) == 0
    assert source.kills == 0
    assert h.store.object_count == 1


async def test_the_window_edge_abandons_the_request_and_keeps_the_capture() -> None:
    """AC42 (a): at ``t = 98.99`` the call still runs with exactly 1 request
    and 0 records; at ``t = 99.0`` (``expiry − 1 s``) the request is abandoned
    and the record, stamped ``== 99.0``, is the capture's ``success`` with
    ``failed:stt_timed_out``; the answer released at ``t = 99.5`` changes
    nothing."""

    h, transport, source, task = await boundary_harness()

    advance_to(h.clock, 98.99)
    await spin()
    assert not task.done()
    assert len(call_requests(transport)) == 1
    assert len(h.runtime.executor.outcomes()) == 0
    assert events_of(h.runtime.bus, "action.completed") == []

    advance_to(h.clock, DEADLINE - TRANSCRIPTION_RESERVE_SECONDS)
    assert h.clock.now == 99.0
    observation = await finished(task)
    await spin()
    assert_window_closed(h, observation, source, 99.0)

    advance_to(h.clock, 99.5)
    transport.deliver({"text": "trop tard"})
    await spin()
    assert_window_closed(h, observation, source, 99.0)
    assert len(call_requests(transport)) == 1


@pytest.mark.parametrize("order", ["bound-then-answer", "answer-then-bound", "answer-only"])
async def test_a_delayed_wake_does_not_adopt_a_late_text(order: str) -> None:
    """AC42 (b): the clock goes from ``t = 98.0`` to ``99.6`` in one step, so
    the bound parked at ``99.0`` wakes the module with the clock already at
    ``99.6``, and the answer is released at ``99.6`` too — in either order,
    or with the answer alone waking the module, its text in hand: the clock
    decides, not the winning event. The record is the capture's ``success``
    with ``failed:stt_timed_out``, stamped ``== 99.6 < 100.0``."""

    h, transport, source, task = await boundary_harness()
    advance_to(h.clock, 98.0)
    await spin()
    assert not task.done()

    if order == "bound-then-answer":
        advance_to(h.clock, 99.6)
        transport.deliver({"text": "trop tard"})
    elif order == "answer-then-bound":
        h.clock.now = 99.6
        transport.deliver({"text": "trop tard"})
        h.clock.advance(0.0)
    else:
        h.clock.now = 99.6
        transport.deliver({"text": "trop tard"})
    observation = await finished(task)
    await spin()

    assert_window_closed(h, observation, source, 99.6)


async def test_a_delayed_wake_adopted_late_by_the_executor_is_still_a_success() -> None:
    """AC42 (b), adoption parked: the record stamped at ``t = 99.6`` is adopted
    as ``success`` even though the executor only resumes at ``t = 100.5`` —
    the phase 1 in-time rule, never R10's late-interruption path."""

    executor_timer = ParkedSleeper()
    h, transport, source, task = await boundary_harness(executor_sleeper=executor_timer)
    advance_to(h.clock, 98.0)
    await spin()

    advance_to(h.clock, 99.6)
    transport.deliver({"text": "trop tard"})
    (invocation,) = h.invocations
    for _ in range(200):
        if invocation.provider_completed_at is not None:
            break
        await asyncio.sleep(0)
    assert invocation.provider_completed_at == 99.6
    assert not task.done()  # adoption has not happened yet
    h.clock.now = 100.5

    observation = await finished(task)
    await spin()
    assert_window_closed(h, observation, source, 99.6)
    assert executor_timer.delays == [10.0]


@pytest.mark.parametrize("answered", [True, False], ids=["answered-at-97", "held-at-98"])
async def test_a_drain_lets_the_transcription_finish_within_its_deadline(answered: bool) -> None:
    """AC42/decision 1: a drain started at ``t = 96.0`` with ``deadline_seconds:
    2`` cancels nothing and kills nothing: an answer at ``t = 97.0`` is
    adopted (``ok``); a request still held at ``t = 98.0`` is abandoned when
    the drain deadline passes (``failed:stt_timed_out``), the capture a
    ``success`` either way and the drain done within its deadline."""

    h, transport, source, task = await boundary_harness()
    advance_to(h.clock, 96.0)
    drain = asyncio.ensure_future(h.handle.drain(2.0))
    await spin()
    assert not drain.done()
    assert not task.done()

    if answered:
        advance_to(h.clock, 97.0)
        transport.deliver({"text": "bonjour"})
    else:
        advance_to(h.clock, 97.99)
        await spin()
        assert not drain.done() and not task.done()
        advance_to(h.clock, 98.0)
    observation = await finished(task)
    await finished(drain)
    assert h.clock.now <= 98.0

    if answered:
        part = assert_captured(h, observation, "ok")
        assert part["transcription"]["text"] == "bonjour"
        assert part["transcription"]["transcribed_at"] == 97.0
    else:
        part = assert_captured(h, observation, "failed:stt_timed_out")
        assert "transcription" not in part
    assert_adopted_once(h, "success")
    assert source.kills == 0
    assert h.store.object_count == 1
    await h.handle.close()


@pytest.mark.parametrize("phase", ["transcribing", "recording"])
async def test_a_cancellation_after_storing_returns_the_capture(phase: str) -> None:
    """Round 2 P1: the provider coroutine, invoked directly and cancelled at
    ``t = 96.0`` after its segment was stored, catches the cancellation,
    abandons the request and returns ``success`` with the ``audio_ref`` and
    ``failed:stt_timed_out`` — 0 kills, 1 attachment. The same cancellation
    while still recording ends ``cancelled`` with 0 attachments and 1 kill
    (P10)."""

    transport = LateAnswerTransport(PROBE_OK, HELD)
    source = FakeAudioSource(wav_bytes(5.0), gated=True)
    h = await stt_harness(transport, source, start=90.0)
    h.deadline = DEADLINE
    invocation = SimpleNamespace(
        call=h.call({"seconds": 3}),
        spec=h.runtime.actions.discovered()[CAPTURE_ACTION],
        mark_not_emitted=lambda: None,
    )
    task = asyncio.ensure_future(h.handle._provider.invoke(invocation))
    await wait_until(source.entered.is_set)
    advance_to(h.clock, 95.0)
    if phase == "transcribing":
        source.release()
        await wait_until(lambda: transport.waiting == 1)
    await spin()
    advance_to(h.clock, 96.0)
    await spin()
    assert not task.done()

    task.cancel()
    observation = await finished(task)

    if phase == "transcribing":
        part = assert_captured(h, observation, "failed:stt_timed_out")
        assert "transcription" not in part
        assert source.kills == 0
        assert h.store.object_count == 1
    else:
        assert_failure(observation, "cancelled", "cancelled")
        assert source.kills == 1
        assert_nothing_stored(h)
        assert call_requests(transport) == []


async def test_the_recorder_is_still_killed_at_the_deadline_itself_with_transcription() -> None:
    """AC39/AC41: the transcription window never moves the recorder's kill —
    0 kills at ``t = 99.0`` for a recorder still running with a ``t = 100.0``
    deadline, 1 kill at ``100.0``, ``capture_timed_out``, no request."""

    transport = ScriptedTranscriptionTransport(PROBE_OK)
    source = FakeAudioSource(gated=True)
    h = await stt_harness(transport, source, start=90.0, executor_sleeper=ParkedSleeper())
    h.deadline = DEADLINE

    task = h.start(seconds=3)
    await wait_until(source.entered.is_set)
    advance_to(h.clock, 99.0)
    await spin()
    assert source.kills == 0 and not task.done()
    advance_to(h.clock, 99.99)
    await spin()
    assert source.kills == 0 and not task.done()

    advance_to(h.clock, 100.0)
    observation = await finished(task)
    await spin()

    assert source.kills == 1
    assert_failure(observation, "error", ERROR_CAPTURE_TIMED_OUT)
    assert_nothing_stored(h)
    assert call_requests(transport) == []


def assert_value_free(reason: str) -> None:
    for fragment in (STT_ENDPOINT, "stt.invalid", "8443", "http", "127.0.0.1", ":1", "/v1", STT_KEY):
        assert fragment not in reason, fragment


@pytest.mark.parametrize("failure", ["raising", "http-503", "held"])
async def test_a_failed_transcription_probe_degrades_the_transcription_only(failure: str) -> None:
    """AC29 (``audio_input``, decision 11): a probe that raises, answers a
    non-2xx status or does not answer within ``timeout_seconds`` on the clock
    → exactly 1 ``module.degraded`` with the value-free reason ``transcription
    unavailable`` and no capability; ``audio.capture`` stays bound and ready;
    every later capture is ``success`` with ``transcription_status:
    "unavailable"`` and sends 0 requests; the transport is released."""

    probe_answer = {
        "raising": ConnectionRefusedError(f"cannot reach {STT_ENDPOINT}"),
        "http-503": 503,
        "held": HELD,
    }[failure]
    transport = ScriptedTranscriptionTransport(probe_answer)
    h = await stt_harness(transport, prepare=False)
    preparing = asyncio.ensure_future(h.handle.prepare())
    if failure == "held":
        await wait_until(lambda: transport.held == 1)
        h.clock.advance(9.99)
        await spin()
        assert not preparing.done()
        h.clock.advance(0.01)
    await finished(preparing)

    (payload,) = degraded_payloads(h)
    assert payload["reason"] == TRANSCRIPTION_DEGRADED_REASON
    assert payload.get("capabilities", []) == []
    assert_value_free(payload["reason"])
    for text in trace_texts(h.runtime.bus):
        assert STT_ENDPOINT not in text and STT_KEY not in text
    assert CAPTURE_ACTION in h.runtime.actions.registered_ready()
    assert h.handle.transcription_available is False
    assert transport.close_calls == 1
    assert h.context.tasks.active == 0

    observation = await h.capture(seconds=3)
    part = assert_captured(h, observation, "unavailable")
    assert "transcription" not in part
    assert len(transport.requests) == 1  # the probe alone
    assert transport.held == 0


async def test_the_real_transport_against_a_closed_port_degrades_the_transcription() -> None:
    """AC29 with the shipped factory: an endpoint on the closed loopback port
    ``127.0.0.1:1`` fails the probe — bounded by ``timeout_seconds`` through
    the sleeper seam — and degrades the transcription alone, value-free."""

    source = FakeAudioSource(wav_bytes(5.0))
    h = await harness(
        source,
        prepare=False,
        transcription=stt_settings(endpoint="http://127.0.0.1:1/v1/audio/transcriptions"),
    )
    await finished(asyncio.ensure_future(h.handle.prepare()))

    (payload,) = degraded_payloads(h)
    assert payload["reason"] == TRANSCRIPTION_DEGRADED_REASON
    assert payload.get("capabilities", []) == []
    assert_value_free(payload["reason"])
    assert CAPTURE_ACTION in h.runtime.actions.registered_ready()
    assert h.handle._session is None  # the transport was released

    observation = await h.capture(seconds=3)
    assert_captured(h, observation, "unavailable")
    await h.handle.close()


async def test_required_with_a_failed_transcription_probe_fails_prepare() -> None:
    """AC29/R8: with ``required: true`` a failed transcription probe fails
    ``prepare`` naming ``audio_input`` and ``transcription.endpoint`` — no
    value — with 0 transports left open."""

    transport = ScriptedTranscriptionTransport(OSError(f"refused by {STT_ENDPOINT}"))
    with pytest.raises(AudioInputModuleError) as raised:
        await stt_harness(transport, required=True)

    message = str(raised.value)
    assert "audio_input" in message and "transcription.endpoint" in message
    assert STT_ENDPOINT not in message and STT_KEY not in message
    assert transport.factory_calls == 1
    assert transport.close_calls == 1


async def test_a_disabled_transcription_opens_no_transport() -> None:
    """R3: ``transcription.enabled: false`` sends no probe and no request and
    reports ``disabled``."""

    transport = ScriptedTranscriptionTransport()
    h = await harness(
        FakeAudioSource(wav_bytes(5.0)),
        transcription=stt_settings(enabled=False),
        _transcription_transport=transport,
    )

    assert_captured(h, await h.capture(seconds=3), "disabled")
    assert transport.requests == [] and transport.factory_calls == 0


def test_the_only_deadline_subtractions_are_the_two_ac41_authorizes() -> None:
    """AC41 (amended, round 3 V4): in the module, the call deadline ``expiry``
    loses a quantity in exactly two places — the ``capture_too_long``
    admission reserve and the transcription window's 1-s
    ``transcription_reserve`` — besides the watcher's remaining time, which
    subtracts only the clock."""

    text = (MODULE_DIR / "__init__.py").read_text(encoding="utf-8")
    code = [line.split("#", 1)[0].strip() for line in text.splitlines()]
    sites = [line for line in code if re.search(r"\bexpiry\s*-", line)]
    assert sites == [
        "if seconds > (expiry - self._clock()) - settings.grace_seconds:",
        "watcher = asyncio.ensure_future(self._sleeper(max(0.0, expiry - self._clock())))",
        "transcription_edge = expiry - transcription_reserve",
    ]
    assert TRANSCRIPTION_RESERVE_SECONDS == 1.0
    assert "transcription_reserve = TRANSCRIPTION_RESERVE_SECONDS" in code


# --------------------------------------------------------------------------- #
# AC41: no lead, no margin, no early stop
# --------------------------------------------------------------------------- #


def test_no_lead_or_margin_exists_in_the_module_or_its_manifest() -> None:
    """AC41: the module and its manifest name no adoption lead, deadline
    margin, early stop or deadline lead — the recorder is killed at
    ``expiry`` and ``grace_seconds`` is the admission reserve only."""

    pattern = re.compile(
        r"adoption_lead|lead_seconds|deadline_margin|early_stop|deadline_lead", re.IGNORECASE
    )
    files = sorted(path for path in MODULE_DIR.rglob("*") if path.suffix in {".py", ".yaml"})
    assert MODULE_DIR / "__init__.py" in files and MANIFEST_PATH in files
    for path in files:
        assert pattern.findall(path.read_text(encoding="utf-8")) == [], path


# --------------------------------------------------------------------------- #
# Module-wide (AC12): nothing spawned outside a call — keep this test last
# --------------------------------------------------------------------------- #


def test_zz_no_recorder_was_spawned_outside_an_audio_capture_call() -> None:
    """AC12: across every test of this module, the subprocess runner recorded
    0 spawns outside ``audio.capture`` calls — and the suite did spawn."""

    assert SPAWNS_OUTSIDE_CALLS == []
    assert len(SPAWNS_TOTAL) >= 8, len(SPAWNS_TOTAL)
