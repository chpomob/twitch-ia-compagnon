"""``modules/audio_output``: ``audio.speak`` and ``audio.play`` (R1, R9-packaging).

AC1 — a scripted 1.5 s WAV (48 000 data bytes) and a recording player that
exits 0: ``audio.speak {text: "hello"}`` ends ``success`` with ``playback:
"completed"`` and ``duration_ms == played_ms == 1500``, exactly one synthesis
request carrying ``model``, ``voice``, ``input == "hello"`` and
``response_format == "wav"``, and exactly one player start that received the
synthesis body byte for byte — all 48 044 bytes, header first (finding P2);
``audio.play {sound: "chime"}`` on a 0.5 s clip plays its 16 044 bytes with
``duration_ms == 500`` and no synthesis request; a body with a ``LIST``
chunk before ``data`` is written whole and ``parse_wav`` reports its larger
``data_offset``. AC2 — every failure of the taxonomy with its effect count;
no observation of either action in this module is ever ``external_unknown``.
The manifest declares both actions with their delivery mappings,
permissions, destinations and credential, and no setting that could shorten
a deadline (AC41).

Every call goes through the real executor built by ``runtime_context`` under
an applicable rule; the synthesis transport, the player runner and the
sleeper are injected, and time moves only on the :class:`ManualClock`.
"""

from __future__ import annotations

import asyncio
import re
import struct
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from core.actions import ERROR_INVALID_ARGUMENTS, ERROR_TIMED_OUT, AuthorizationRule
from core.contracts import ActionCall, ActionObservation, Destination
from core.lifecycle import PhaseCoordinator
from core.loader import ModuleLoader
from core.runtime import RUNTIME_API, ModuleContext, RuntimeContext
from conftest import (
    HELD,
    FakeBytesResponse,
    FakePlayer,
    ManualClock,
    RecordingPlayerRunner,
    ScriptedSpeechTransport,
    runtime_context,
    trace_texts,
    wait_until,
    wav_bytes,
)

from modules.audio_output import (
    CAUSE_SYNTHESIS,
    DEFAULT_MAX_AUDIO_BYTES,
    DEFAULT_MAX_TEXT_CHARS,
    ERROR_AUDIO_TOO_LARGE,
    ERROR_CLIP_UNREADABLE,
    ERROR_INVALID_AUDIO,
    ERROR_OUTPUT_NOT_ALLOWED,
    ERROR_PLAYBACK_FAILED,
    ERROR_SPEECH_TOO_LONG,
    ERROR_TTS_FAILED,
    ERROR_TTS_UNAVAILABLE,
    ERROR_VOICE_NOT_ALLOWED,
    MANIFEST_PATH,
    MODULE_NAME,
    PLAY_ACTION,
    PROVIDER_NAME,
    READ_CHUNK_BYTES,
    SPEAK_ACTION,
    WRITE_CHUNK_BYTES,
    AudioOutputModule,
    AudioOutputModuleError,
    InvalidAudio,
    WavInfo,
    activate,
    parse_wav,
    validate_settings,
)


ROOT = Path(__file__).parents[1]
MODULE_DIR = ROOT / "modules" / "audio_output"

PRINCIPAL = "brain"
RUN_ID = "run-1"
AUDIO = Destination("twitch", "channel-1", "audio")
FAKE_AUDIO = Destination("fake", "channel-9", "audio")

ENDPOINT = "http://speech.invalid/v1/audio/speech"
API_KEY = "speech-key-4f1b9c2e7d"
MODEL = "speech-model-under-test"
PLAYER_ARGV = ["player-under-test", "--from-stdin"]
HEADSET_ARGV = ["headset-player-under-test", "-"]

#: Every observation of this module's actions, for the module-wide AC2
#: assertion that none is ``external_unknown``.
OBSERVED: list[ActionObservation] = []


# --------------------------------------------------------------------------- #
# Harness
# --------------------------------------------------------------------------- #


def wav_with_list_chunk(seconds: float, list_payload: bytes = b"INFOISFT\x05\x00\x00\x00test\x00\x00") -> bytes:
    """A canonical WAV with a ``LIST`` chunk inserted between ``fmt `` and ``data``."""

    canonical = wav_bytes(seconds)
    list_chunk = b"LIST" + struct.pack("<I", len(list_payload)) + list_payload
    if len(list_payload) & 1:
        list_chunk += b"\x00"
    body = canonical[:36] + list_chunk + canonical[36:]
    return body[:4] + struct.pack("<I", len(body) - 8) + body[8:]


def module_settings(
    transport: ScriptedSpeechTransport,
    runner: RecordingPlayerRunner,
    clock: ManualClock,
    *,
    clips: dict[str, Path] | None = None,
    synthesis: dict[str, Any] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "synthesis": {
            "endpoint": ENDPOINT,
            "model": MODEL,
            "api_key": API_KEY,
            "probe": False,
            **(synthesis or {}),
        },
        "voices": {"allowed": ["narrator", "bright"], "default": "narrator"},
        "outputs": {
            "speakers": {"player": {"argv": list(PLAYER_ARGV)}},
            "headset": {"player": {"argv": list(HEADSET_ARGV)}},
        },
        "default_output": "speakers",
        "clips": {name: {"path": str(path)} for name, path in (clips or {}).items()},
        "_synthesis_transport": transport,
        "_player_runner": runner,
        "_sleeper": clock.sleep,
        **extra,
    }


class Harness:
    """One prepared module on a real executor, under rules granting both actions."""

    def __init__(
        self,
        context: ModuleContext,
        handle: AudioOutputModule,
        transport: ScriptedSpeechTransport,
        runner: RecordingPlayerRunner,
        clock: ManualClock,
    ) -> None:
        self.context = context
        self.handle = handle
        self.transport = transport
        self.runner = runner
        self.clock = clock
        self._calls = 0

    @property
    def runtime(self) -> RuntimeContext:
        return self.context._runtime

    def call(self, action: str, arguments: dict[str, Any], destination: Destination = AUDIO) -> ActionCall:
        self._calls += 1
        return ActionCall(
            action_name=action,
            action_version=1,
            arguments=arguments,
            conversation_id="conversation-1",
            run_id=RUN_ID,
            call_id=f"call-{self._calls}",
            source_event_id="source-1",
            destination=destination,
            principal=PRINCIPAL,
            deadline=10_000.0,
            message_id="source-1",
        )

    async def invoke(
        self, action: str, arguments: dict[str, Any], destination: Destination = AUDIO
    ) -> ActionObservation:
        observation = await self.runtime.executor.invoke(self.call(action, arguments, destination))
        OBSERVED.append(observation)
        return observation

    async def speak(self, **arguments: Any) -> ActionObservation:
        return await self.invoke(SPEAK_ACTION, arguments)

    async def play(self, **arguments: Any) -> ActionObservation:
        return await self.invoke(PLAY_ACTION, arguments)


async def harness(
    *answers: Any,
    players: tuple[FakePlayer, ...] = (),
    clips: dict[str, Path] | None = None,
    synthesis: dict[str, Any] | None = None,
    grant: bool = True,
    **extra: Any,
) -> Harness:
    clock = ManualClock(1000.0)
    transport = ScriptedSpeechTransport(*answers)
    runner = RecordingPlayerRunner(*players, clock=clock)
    context = runtime_context(clock=clock).for_module(MODULE_NAME)
    handle = await activate(
        context,
        module_settings(transport, runner, clock, clips=clips, synthesis=synthesis, **extra),
        {},
    )
    await handle.prepare()
    if grant:
        policy = context._runtime.actions._authorization
        for action in (SPEAK_ACTION, PLAY_ACTION):
            policy.grant(
                AuthorizationRule(
                    rule_id=f"grant-{action}",
                    action_name=action,
                    granted_permissions=(action,),
                )
            )
    return Harness(context, handle, transport, runner, clock)


def assert_failure(observation: ActionObservation, status: str, code: str) -> dict[str, Any]:
    assert observation.status == status, observation
    assert observation.result is None
    assert observation.error is not None
    assert observation.error["code"] == code, observation.error
    return dict(observation.error)


# --------------------------------------------------------------------------- #
# AC1: synthesis then complete-WAV playback; clips
# --------------------------------------------------------------------------- #


async def test_speak_synthesises_once_and_plays_the_whole_wav_to_completion() -> None:
    """AC1: one request ``{model, voice, input: "hello", response_format:
    "wav"}`` with the bearer header, one player start that received the
    synthesis body byte for byte (48 044 bytes, ``RIFF`` first), ``success``
    with ``playback: "completed"`` and ``duration_ms == played_ms == 1500``."""

    body = wav_bytes(1.5)
    assert len(body) == 48_044
    h = await harness(body)
    try:
        observation = await h.speak(text="hello")

        assert observation.status == "success", observation.error
        assert observation.result == {
            "output": "speakers",
            "voice": "narrator",
            "duration_ms": 1500,
            "played_ms": 1500,
            "playback": "completed",
        }
        (request,) = h.transport.requests
        assert request["url"] == ENDPOINT
        assert request["json"] == {
            "model": MODEL,
            "voice": "narrator",
            "input": "hello",
            "response_format": "wav",
        }
        assert request["headers"]["Authorization"] == f"Bearer {API_KEY}"
        assert h.runner.starts == [PLAYER_ARGV]
        assert h.runner.received == body
        assert len(h.runner.received) == 48_044
        assert h.runner.received[:4] == b"RIFF"
        assert h.runner.players[0].stdin_closed
        assert h.runner.stops == [] and h.runner.kills == []
        assert observation.provenance["provider"] == PROVIDER_NAME
        assert observation.provenance["output"] == "speakers"
    finally:
        await h.handle.close()


async def test_speak_honours_an_allowed_voice_and_output_and_omits_an_empty_key() -> None:
    """R1: an allowed ``voice`` and a configured ``output`` are used as
    named; without an ``api_key`` no Authorization header is sent. A body
    larger than one write is fed in bounded writes, still every byte."""

    body = wav_bytes(3.0)
    assert len(body) > 2 * WRITE_CHUNK_BYTES
    h = await harness(body, synthesis={"api_key": ""})
    try:
        observation = await h.speak(text="bonjour", voice="bright", output="headset")

        assert observation.status == "success", observation.error
        assert observation.result["voice"] == "bright"
        assert observation.result["output"] == "headset"
        assert observation.result["duration_ms"] == observation.result["played_ms"] == 3000
        (request,) = h.transport.requests
        assert request["json"]["voice"] == "bright"
        assert "Authorization" not in request["headers"]
        assert h.runner.starts == [HEADSET_ARGV]
        assert h.runner.received == body
    finally:
        await h.handle.close()


async def test_play_feeds_the_whole_clip_and_sends_no_synthesis_request(tmp_path: Path) -> None:
    """AC1: ``audio.play {sound: "chime"}`` on a 0.5 s clip ends ``success``
    with ``duration_ms == played_ms == 500``; the player received the clip's
    16 044 bytes; 0 synthesis requests."""

    clip = wav_bytes(0.5)
    assert len(clip) == 16_044
    path = tmp_path / "chime.wav"
    path.write_bytes(clip)
    h = await harness(clips={"chime": path})
    try:
        observation = await h.play(sound="chime")

        assert observation.status == "success", observation.error
        assert observation.result == {
            "output": "speakers",
            "sound": "chime",
            "duration_ms": 500,
            "played_ms": 500,
            "playback": "completed",
        }
        assert h.transport.requests == []
        assert h.runner.starts == [PLAYER_ARGV]
        assert h.runner.received == clip
    finally:
        await h.handle.close()


async def test_a_list_chunk_before_data_is_written_whole_and_offsets_the_data() -> None:
    """Finding P2: a body with a ``LIST`` chunk between ``fmt `` and ``data``
    is played as it is — every byte, the extra chunk included — and
    ``parse_wav`` reports the larger ``data_offset``, so the duration and the
    estimate count the data chunk only."""

    body = wav_with_list_chunk(1.5)
    info = parse_wav(body)
    assert info.data_offset > 44
    assert info.data_offset == len(body) - 48_000
    assert info.data_bytes == 48_000
    assert info.duration_ms == 1500

    h = await harness(body)
    try:
        observation = await h.speak(text="hello")

        assert observation.status == "success", observation.error
        assert observation.result["duration_ms"] == observation.result["played_ms"] == 1500
        assert h.runner.received == body
    finally:
        await h.handle.close()


# --------------------------------------------------------------------------- #
# parse_wav
# --------------------------------------------------------------------------- #


def test_parse_wav_reads_the_canonical_header_and_computes_the_duration() -> None:
    """``duration_ms = data_bytes × 1000 // (rate × channels × bytes/sample)``;
    ``data_offset`` 44 for the canonical header."""

    assert parse_wav(wav_bytes(1.5)) == WavInfo(16000, 1, 16, 44, 48_000)
    assert parse_wav(wav_bytes(1.5)).duration_ms == 1500
    stereo = parse_wav(wav_bytes(0.5, sample_rate=48000, channels=2, bits=16))
    assert (stereo.sample_rate, stereo.channels, stereo.bits) == (48000, 2, 16)
    assert stereo.duration_ms == 500
    eight_bit = parse_wav(wav_bytes(1.0, sample_rate=8000, channels=1, bits=8))
    assert eight_bit.duration_ms == 1000


def test_the_accepted_bytes_estimate_counts_data_bytes_only() -> None:
    """P2/P9 groundwork: header bytes never count as played; the estimate
    rounds down and never exceeds the duration."""

    info = parse_wav(wav_bytes(10.0))
    assert info.played_ms(0) == 0
    assert info.played_ms(20) == 0
    assert info.played_ms(44) == 0
    assert info.played_ms(44 + 128_000) == 4000
    assert info.played_ms(44 + 128_031) == 4000
    assert info.played_ms(44 + 320_000) == 10_000
    assert info.played_ms(10**9) == info.duration_ms == 10_000


def _with_format(body: bytes, audio_format: int) -> bytes:
    return body[:20] + struct.pack("<H", audio_format) + body[22:]


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"not a wav!",
        b"RIFX" + wav_bytes(0.1)[4:],
        wav_bytes(0.1)[:8] + b"AVI " + wav_bytes(0.1)[12:],
        _with_format(wav_bytes(0.1), 3),  # IEEE float, not PCM
        wav_bytes(0.0),  # an empty data chunk
        wav_bytes(0.1)[:-10],  # a data size larger than the body
        wav_bytes(0.1)[:36],  # no data chunk
        wav_bytes(0.1)[:12] + wav_bytes(0.1)[36:],  # data before any fmt
    ],
    ids=[
        "empty",
        "ten-foreign-bytes",
        "not-riff",
        "not-wave",
        "not-pcm",
        "empty-data",
        "data-larger-than-body",
        "no-data",
        "no-fmt",
    ],
)
def test_parse_wav_refuses_what_is_not_a_pcm_segment_with_data(body: bytes) -> None:
    with pytest.raises(InvalidAudio):
        parse_wav(body)


def test_parse_wav_accepts_the_extensible_pcm_form() -> None:
    canonical = wav_bytes(0.5)
    fmt = struct.pack("<HHIIHH", 0xFFFE, 1, 16000, 32000, 2, 16) + struct.pack(
        "<HHI", 22, 16, 4
    ) + struct.pack("<H", 1) + b"\x00\x00\x00\x00\x10\x00\x80\x00\x00\xaa\x00\x38\x9b\x71"
    body = b"RIFF" + b"\x00" * 4 + b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + canonical[36:]
    body = body[:4] + struct.pack("<I", len(body) - 8) + body[8:]
    info = parse_wav(body)
    assert info.duration_ms == 500
    assert info.data_offset == 12 + 8 + len(fmt) + 8


# --------------------------------------------------------------------------- #
# AC2: the failure taxonomy with its effect counts
# --------------------------------------------------------------------------- #


async def test_a_transport_exception_is_tts_unavailable_with_no_player_start() -> None:
    h = await harness(ConnectionError("speech service unreachable"))
    try:
        observation = await h.speak(text="hello")

        assert_failure(observation, "error", ERROR_TTS_UNAVAILABLE)
        assert len(h.transport.requests) == 1
        assert h.runner.starts == []
    finally:
        await h.handle.close()


async def test_a_503_is_tts_failed_naming_the_code_and_never_the_body() -> None:
    """AC2: the status code is in the message; nothing of the body is, nor
    in any trace."""

    secret_body = b"upstream overloaded: queue token zq8-private-detail"
    h = await harness(FakeBytesResponse(503, secret_body))
    try:
        observation = await h.speak(text="hello")

        error = assert_failure(observation, "error", ERROR_TTS_FAILED)
        assert "503" in error["message"]
        rendered = repr(observation)
        for fragment in ("overloaded", "zq8-private-detail", "queue token"):
            assert fragment not in rendered
            assert all(fragment not in text for text in trace_texts(h.runtime.bus))
        assert h.runner.starts == []
    finally:
        await h.handle.close()


async def test_no_answer_within_the_timeout_is_a_synthesis_timeout_on_the_clock() -> None:
    """AC2: the request is bounded on the injected sleeper — still running at
    ``timeout_seconds − ε``, ``timeout`` with cause ``synthesis`` once the
    clock reaches it — the module's own observation, with 0 player starts."""

    h = await harness(HELD, synthesis={"timeout_seconds": 10})
    try:
        task = asyncio.ensure_future(h.speak(text="hello"))
        await wait_until(lambda: h.transport.held == 1)
        h.clock.advance(9.99)
        for _ in range(50):
            await asyncio.sleep(0)
        assert not task.done()
        h.clock.advance(0.01)
        observation = await task

        error = assert_failure(observation, "timeout", ERROR_TIMED_OUT)
        assert error["cause"] == CAUSE_SYNTHESIS
        assert "synthesis" in error["message"]
        assert "with emission" not in error["message"]
        assert h.transport.held == 0
        assert h.runner.starts == []
    finally:
        await h.handle.close()


async def test_ten_non_wav_bytes_are_invalid_audio_with_no_player_start() -> None:
    h = await harness(b"0123456789")
    try:
        assert_failure(await h.speak(text="hello"), "error", ERROR_INVALID_AUDIO)
        assert h.runner.starts == []
    finally:
        await h.handle.close()


async def test_an_empty_body_is_invalid_audio() -> None:
    h = await harness(FakeBytesResponse(200, b""))
    try:
        assert_failure(await h.speak(text="hello"), "error", ERROR_INVALID_AUDIO)
        assert h.runner.starts == []
    finally:
        await h.handle.close()


async def test_a_25_second_segment_is_speech_too_long_with_no_player_start() -> None:
    h = await harness(wav_bytes(25.0), max_speech_seconds=20)
    try:
        error = assert_failure(await h.speak(text="hello"), "error", ERROR_SPEECH_TOO_LONG)
        assert error["duration_ms"] == 25_000
        assert h.runner.starts == []
    finally:
        await h.handle.close()


async def test_a_body_one_byte_over_max_audio_bytes_is_audio_too_large() -> None:
    """AC2: ``max_audio_bytes + 1`` is refused with 0 player starts; a body of
    exactly ``max_audio_bytes`` plays."""

    body = wav_bytes(1.5)
    h = await harness(body, body, max_audio_bytes=len(body) - 1)
    try:
        assert_failure(await h.speak(text="hello"), "error", ERROR_AUDIO_TOO_LARGE)
        assert h.runner.starts == []
    finally:
        await h.handle.close()

    exact = await harness(body, max_audio_bytes=len(body))
    try:
        assert (await exact.speak(text="hello")).status == "success"
    finally:
        await exact.handle.close()


async def test_an_endless_answer_is_audio_too_large_after_a_bounded_read() -> None:
    """AC2: the 2xx body is read in chunks and the read stops once
    ``max_audio_bytes`` is passed — an answer that never ends is refused
    with ``audio_too_large`` having held at most one chunk over the bound."""

    class EndlessStream:
        def __init__(self) -> None:
            self.reads = 0
            self.served = 0

        async def read(self, n: int = -1) -> bytes:
            self.reads += 1
            size = READ_CHUNK_BYTES if n < 0 else n
            self.served += size
            return b"\0" * size

    response = FakeBytesResponse(200, b"")
    stream = EndlessStream()
    response.content = stream  # type: ignore[assignment]
    limit = 3 * READ_CHUNK_BYTES + 10
    h = await harness(response, max_audio_bytes=limit)
    try:
        assert_failure(await h.speak(text="hello"), "error", ERROR_AUDIO_TOO_LARGE)
        assert stream.served == limit + 1
        assert stream.reads == 4
        assert response.release_calls == 1
        assert h.runner.starts == []
    finally:
        await h.handle.close()


async def test_a_body_is_read_off_the_stream_in_bounded_chunks() -> None:
    """AC2: a body larger than one read chunk is assembled whole from the
    response's stream and plays."""

    body = wav_bytes(3.0)
    assert len(body) > READ_CHUNK_BYTES
    response = FakeBytesResponse(200, body)
    h = await harness(response)
    try:
        assert (await h.speak(text="hello")).status == "success"
        assert h.runner.received == body
        assert response.content_reads >= 2
    finally:
        await h.handle.close()


async def test_a_pipe_broken_while_closing_is_playback_failed_despite_exit_0() -> None:
    """AC3: every write was accepted into the pipe and the player exited 0,
    but closing its input broke the pipe — the buffered tail never reached
    it — so the playback is ``playback_failed``, never completed."""

    h = await harness(
        wav_bytes(1.5), players=(FakePlayer(close_error=BrokenPipeError(32, "broken")),)
    )
    try:
        error = assert_failure(await h.speak(text="hello"), "error", ERROR_PLAYBACK_FAILED)
        assert "whole segment" in error["message"]
        assert error["duration_ms"] == 1500
        assert len(h.runner.starts) == 1
    finally:
        await h.handle.close()


async def test_a_player_exiting_1_is_playback_failed_with_the_accepted_estimate() -> None:
    """AC2/AC3: a player that accepted everything and exited 1 →
    ``playback_failed`` carrying ``played_ms`` = the whole duration."""

    h = await harness(wav_bytes(1.5), players=(FakePlayer(exit_code=1),))
    try:
        error = assert_failure(await h.speak(text="hello"), "error", ERROR_PLAYBACK_FAILED)
        assert error["played_ms"] == 1500
        assert error["duration_ms"] == 1500
        assert len(h.runner.starts) == 1
        assert h.runner.received == wav_bytes(1.5)
    finally:
        await h.handle.close()


async def test_an_unstartable_player_is_playback_failed_with_nothing_played() -> None:
    h = await harness(wav_bytes(1.5), players=(FakePlayer(startable=False),))
    try:
        error = assert_failure(await h.speak(text="hello"), "error", ERROR_PLAYBACK_FAILED)
        assert error["played_ms"] == 0
        assert h.runner.starts == []
        assert h.runner.failed_starts == [PLAYER_ARGV]
        assert all("player-under-test" not in text for text in trace_texts(h.runtime.bus))
    finally:
        await h.handle.close()


async def test_a_player_that_stops_reading_mid_chunk_is_measured_at_the_byte() -> None:
    """Risk (P8): the accepted count is read after every write, so a player
    that takes 16 031 data bytes of the first 32 KiB chunk and then exits 0
    is ``playback_failed`` — not every byte was accepted — with ``played_ms
    == 500`` (16 031 ÷ 32 000, rounded down), never a whole-chunk figure."""

    player = FakePlayer(accept_limit=16_031)
    h = await harness(wav_bytes(1.5), players=(player,))
    try:
        task = asyncio.ensure_future(h.speak(text="hello"))
        await wait_until(lambda: player.accepted == 44 + 16_031)
        player.release()
        error = assert_failure(await task, "error", ERROR_PLAYBACK_FAILED)
        assert error["played_ms"] == 500
    finally:
        await h.handle.close()


async def test_a_player_that_took_only_header_bytes_played_nothing() -> None:
    player = FakePlayer(accept_total=20)
    h = await harness(wav_bytes(1.5), players=(player,))
    try:
        task = asyncio.ensure_future(h.speak(text="hello"))
        await wait_until(lambda: player.accepted == 20)
        player.release()
        error = assert_failure(await task, "error", ERROR_PLAYBACK_FAILED)
        assert error["played_ms"] == 0
    finally:
        await h.handle.close()


async def test_an_unknown_sound_is_invalid_arguments_with_no_effect(tmp_path: Path) -> None:
    path = tmp_path / "chime.wav"
    path.write_bytes(wav_bytes(0.5))
    h = await harness(clips={"chime": path})
    try:
        assert_failure(await h.play(sound="gong"), "error", ERROR_INVALID_ARGUMENTS)
        assert h.runner.starts == []
        assert h.transport.requests == []
    finally:
        await h.handle.close()


async def test_a_clip_removed_after_prepare_is_clip_unreadable(tmp_path: Path) -> None:
    path = tmp_path / "chime.wav"
    path.write_bytes(wav_bytes(0.5))
    h = await harness(clips={"chime": path})
    try:
        path.unlink()
        error = assert_failure(await h.play(sound="chime"), "error", ERROR_CLIP_UNREADABLE)
        assert str(tmp_path) not in error["message"]
        assert all(str(tmp_path) not in text for text in trace_texts(h.runtime.bus))
        assert h.runner.starts == []
    finally:
        await h.handle.close()


async def test_a_clip_is_parsed_and_bounded_like_a_synthesis_body(tmp_path: Path) -> None:
    """R1: the clip's bytes are held to the same bounds — foreign bytes are
    ``invalid_audio``, an over-long clip ``speech_too_long``, an over-heavy
    one ``audio_too_large`` — with 0 player starts."""

    foreign = tmp_path / "foreign.wav"
    foreign.write_bytes(b"0123456789")
    long_clip = tmp_path / "long.wav"
    long_clip.write_bytes(wav_bytes(3.0))
    h = await harness(clips={"foreign": foreign, "long": long_clip}, max_audio_bytes=40_000)
    try:
        assert_failure(await h.play(sound="foreign"), "error", ERROR_INVALID_AUDIO)
        assert_failure(await h.play(sound="long"), "error", ERROR_AUDIO_TOO_LARGE)
        assert h.runner.starts == []
    finally:
        await h.handle.close()

    bounded = await harness(clips={"long": long_clip}, max_speech_seconds=2)
    try:
        assert_failure(await bounded.play(sound="long"), "error", ERROR_SPEECH_TOO_LONG)
        assert bounded.runner.starts == []
    finally:
        await bounded.handle.close()


async def test_a_voice_outside_the_allowlist_is_refused_with_no_request() -> None:
    h = await harness(wav_bytes(1.5))
    try:
        assert_failure(await h.speak(text="hello", voice="other"), "refused", ERROR_VOICE_NOT_ALLOWED)
        assert h.transport.requests == []
        assert h.runner.starts == []
    finally:
        await h.handle.close()


async def test_an_output_outside_the_configuration_is_refused_with_no_request(tmp_path: Path) -> None:
    path = tmp_path / "chime.wav"
    path.write_bytes(wav_bytes(0.5))
    h = await harness(wav_bytes(1.5), clips={"chime": path})
    try:
        assert_failure(await h.speak(text="hello", output="other"), "refused", ERROR_OUTPUT_NOT_ALLOWED)
        assert_failure(await h.play(sound="chime", output="other"), "refused", ERROR_OUTPUT_NOT_ALLOWED)
        assert h.transport.requests == []
        assert h.runner.starts == []
    finally:
        await h.handle.close()


async def test_a_text_outside_one_to_max_text_chars_is_invalid_arguments() -> None:
    """R1: ``text`` of 1..``max_text_chars`` characters — checked first,
    before the voice and the output, with no request."""

    h = await harness(wav_bytes(1.5), max_text_chars=5)
    try:
        assert_failure(await h.speak(text=""), "error", ERROR_INVALID_ARGUMENTS)
        assert_failure(await h.speak(text="sixsix"), "error", ERROR_INVALID_ARGUMENTS)
        assert_failure(
            await h.speak(text="sixsix", voice="other", output="other"),
            "error",
            ERROR_INVALID_ARGUMENTS,
        )
        assert_failure(
            await h.speak(text="five!", voice="other", output="other"),
            "refused",
            ERROR_VOICE_NOT_ALLOWED,
        )
        assert h.transport.requests == []
        assert (await h.speak(text="five!")).status == "success"
        assert len(h.transport.requests) == 1
    finally:
        await h.handle.close()
    assert DEFAULT_MAX_TEXT_CHARS == 400


async def test_an_empty_endpoint_sends_no_request_and_is_tts_unavailable() -> None:
    """Arbiter decision 22/09: no endpoint is assumed — an empty one never
    produces a request to some implicit address."""

    h = await harness(wav_bytes(1.5), synthesis={"endpoint": ""})
    try:
        assert_failure(await h.speak(text="hello"), "error", ERROR_TTS_UNAVAILABLE)
        assert h.transport.requests == []
        assert h.transport.factory_calls == 0
    finally:
        await h.handle.close()


async def test_without_a_rule_neither_action_reaches_the_provider(tmp_path: Path) -> None:
    """Default-deny: without an applicable rule the call is refused before the
    provider — no request, no player start."""

    path = tmp_path / "chime.wav"
    path.write_bytes(wav_bytes(0.5))
    h = await harness(wav_bytes(1.5), clips={"chime": path}, grant=False)
    try:
        assert (await h.speak(text="hello")).status == "refused"
        assert (await h.play(sound="chime")).status == "refused"
        assert h.runtime.executor.provider_invocations == 0
        assert h.transport.requests == []
        assert h.runner.starts == []
    finally:
        await h.handle.close()


async def test_the_api_key_appears_in_no_observation_or_trace(tmp_path: Path) -> None:
    path = tmp_path / "chime.wav"
    path.write_bytes(wav_bytes(0.5))
    h = await harness(wav_bytes(1.5), FakeBytesResponse(500, API_KEY.encode()), clips={"chime": path})
    try:
        observations = [await h.speak(text="hello"), await h.speak(text="hello"), await h.play(sound="chime")]
        for observation in observations:
            assert API_KEY not in repr(observation)
            assert ENDPOINT not in repr(observation)
        assert all(API_KEY not in text for text in trace_texts(h.runtime.bus))
    finally:
        await h.handle.close()


async def test_close_withdraws_readiness_and_closes_the_transport() -> None:
    h = await harness(wav_bytes(1.5))
    assert {SPEAK_ACTION, PLAY_ACTION} <= set(h.runtime.actions.registered_ready())
    assert (await h.speak(text="hello")).status == "success"
    await h.handle.close()
    assert SPEAK_ACTION not in h.runtime.actions.registered_ready()
    assert PLAY_ACTION not in h.runtime.actions.registered_ready()
    assert h.transport.close_calls == 1
    observation = await h.speak(text="hello")
    assert observation.status == "refused"
    assert observation.error["code"] == "provider_not_ready"


# --------------------------------------------------------------------------- #
# Manifest, settings hook, activation, packaging
# --------------------------------------------------------------------------- #


def _schema_keys(schema: Any) -> list[str]:
    keys: list[str] = []
    if isinstance(schema, dict):
        for name, sub in (schema.get("properties") or {}).items():
            keys.append(name)
            keys.extend(_schema_keys(sub))
        keys.extend(_schema_keys(schema.get("items")))
    return keys


def test_manifest_declares_both_write_actions_with_their_delivery_mappings() -> None:
    """R1/R2/R9: manifest v2 with no role and no event, the credential
    ``synthesis.api_key``, the declared hook, and exactly ``audio.speak``
    (delivery ``text``) and ``audio.play`` (effect-only delivery) — writes
    on ``*/*/audio`` under their own permissions — and a settings schema
    declaring no lead, margin or early-stop key (AC41)."""

    manifest = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert MANIFEST_PATH == MODULE_DIR / "module.yaml"
    assert manifest["name"] == MODULE_NAME == "audio_output"
    assert manifest["manifest_version"] == 2
    assert manifest["runtime_api"] == RUNTIME_API == 2
    assert manifest["produces"] == []
    assert manifest["consumes"] == []
    assert manifest["middleware"] is False
    assert manifest["lifecycle"] == {"roles": []}
    assert manifest["credentials"] == ["synthesis.api_key"]
    assert manifest["settings_validator"] == "validate_settings"
    assert not any(key in manifest for key in ("rules", "authorization", "grants"))

    schema = manifest["settings_schema"]
    assert set(schema["properties"]) == {
        "synthesis", "voices", "outputs", "default_output", "clips",
        "max_text_chars", "max_speech_seconds", "max_audio_bytes",
        "stop_grace_seconds", "max_waiters", "required", "limits",
    }
    assert set(schema["properties"]["synthesis"]["properties"]) == {
        "endpoint", "model", "api_key", "timeout_seconds", "probe", "probe_text",
    }
    assert schema["properties"]["synthesis"]["properties"]["api_key"]["type"] == "string"
    assert set(schema["properties"]["voices"]["properties"]) == {"allowed", "default"}
    keys = _schema_keys(schema)
    assert keys
    assert not [key for key in keys if re.search(r"lead|margin|early_stop", key)]

    speak, play = manifest["actions"]
    assert speak["name"] == SPEAK_ACTION and play["name"] == PLAY_ACTION
    for action, permission in ((speak, "audio.speak"), (play, "audio.play")):
        assert action["version"] == 1
        assert action["nature"] == "write"
        assert action["required_permissions"] == [permission]
        assert action["supported_destinations"] == [
            {"platform": "*", "channel_id": "*", "scope": "audio"}
        ]
        assert action["idempotency"] == "none"
        assert action["argument_schema"]["additionalProperties"] is False
        assert action["result_schema"]["additionalProperties"] is False
        assert "estimate" in action["description"]
        assert "played_ms" in action["description"]
    assert speak["delivery"] == {"text_argument": "text"}
    assert play["delivery"] == {"text_argument": "none"}
    assert speak["timeout_seconds"] == 40
    assert play["timeout_seconds"] == 30
    assert set(speak["argument_schema"]["properties"]) == {"text", "voice", "output"}
    assert speak["argument_schema"]["required"] == ["text"]
    assert set(play["argument_schema"]["properties"]) == {"sound", "output"}
    assert play["argument_schema"]["required"] == ["sound"]
    assert set(speak["result_schema"]["required"]) == {
        "output", "voice", "duration_ms", "played_ms", "playback",
    }
    assert set(play["result_schema"]["required"]) == {
        "output", "sound", "duration_ms", "played_ms", "playback",
    }


def _good_settings() -> dict[str, Any]:
    return {
        "synthesis": {
            "endpoint": ENDPOINT,
            "model": MODEL,
            "api_key": API_KEY,
            "timeout_seconds": 10,
            "probe": True,
            "probe_text": "ready",
        },
        "voices": {"allowed": ["narrator", "bright"], "default": "narrator"},
        "outputs": {"speakers": {"player": {"argv": list(PLAYER_ARGV)}}},
        "default_output": "speakers",
        "clips": {"chime": {"path": "/var/sounds/chime.wav"}},
        "max_text_chars": 400,
        "max_speech_seconds": 20,
        "max_audio_bytes": 5_242_880,
        "stop_grace_seconds": 1,
        "max_waiters": 1,
        "required": False,
        "limits": {"attachments": {}},
    }


def _without(mapping: dict[str, Any], *path: str) -> dict[str, Any]:
    node = mapping
    for key in path[:-1]:
        node = node[key]
    del node[path[-1]]
    return mapping


def _with(mapping: dict[str, Any], value: Any, *path: str) -> dict[str, Any]:
    node = mapping
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    return mapping


@pytest.mark.parametrize(
    ("mutate", "field"),
    [
        (lambda s: _without(s, "synthesis"), "synthesis"),
        (lambda s: _with(s, "x", "synthesis"), "synthesis"),
        (lambda s: _without(s, "synthesis", "endpoint"), "synthesis.endpoint"),
        (lambda s: _with(s, 5, "synthesis", "model"), "synthesis.model"),
        (lambda s: _with(s, 7, "synthesis", "api_key"), "synthesis.api_key"),
        (lambda s: _with(s, 0, "synthesis", "timeout_seconds"), "synthesis.timeout_seconds"),
        (lambda s: _with(s, "yes", "synthesis", "probe"), "synthesis.probe"),
        (lambda s: _with(s, " ", "synthesis", "probe_text"), "synthesis.probe_text"),
        (lambda s: _with(s, 1, "synthesis", "lead_seconds"), "synthesis.lead_seconds"),
        (lambda s: _without(s, "voices"), "voices"),
        (lambda s: _with(s, [], "voices", "allowed"), "voices.allowed"),
        (lambda s: _with(s, "deep-secret-voice", "voices", "default"), "voices.default"),
        (lambda s: _without(s, "outputs"), "outputs"),
        (lambda s: _with(s, {}, "outputs"), "outputs"),
        (lambda s: _with(s, [], "outputs", "speakers", "player", "argv"), "outputs.speakers.player.argv"),
        (lambda s: _with(s, "x", "outputs", "speakers", "player"), "outputs.speakers.player"),
        (lambda s: _without(s, "default_output"), "default_output"),
        (lambda s: _with(s, "elsewhere", "default_output"), "default_output"),
        (lambda s: _with(s, {"path": ""}, "clips", "chime"), "clips.chime.path"),
        (lambda s: _with(s, [], "clips"), "clips"),
        (lambda s: _with(s, 0, "max_text_chars"), "max_text_chars"),
        (lambda s: _with(s, -1, "max_speech_seconds"), "max_speech_seconds"),
        (lambda s: _with(s, 0, "max_audio_bytes"), "max_audio_bytes"),
        (lambda s: _with(s, -1, "stop_grace_seconds"), "stop_grace_seconds"),
        (lambda s: _with(s, -1, "max_waiters"), "max_waiters"),
        (lambda s: _with(s, "no", "required"), "required"),
        (lambda s: _with(s, 1.0, "deadline_margin"), "deadline_margin"),
        (lambda s: _with(s, "not callable", "_player_runner"), "_player_runner"),
    ],
)
def test_settings_hook_names_each_offending_field_without_its_value(mutate: Any, field: str) -> None:
    """R7: one value-free diagnostic per field; ``voices.default`` must be
    allowed, ``default_output`` configured, every argv non-empty; a lead or
    margin key is refused like any undeclared one (AC41)."""

    assert validate_settings(_good_settings()) == []
    diagnostics = validate_settings(mutate(_good_settings()))
    assert diagnostics, field
    assert any(f"field {field!r}" in item for item in diagnostics), diagnostics
    assert all(item.startswith("module 'audio_output': ") for item in diagnostics)
    for value in (ENDPOINT, API_KEY, MODEL, "player-under-test", "/var/sounds", "deep-secret-voice", "elsewhere"):
        assert all(value not in item for item in diagnostics), diagnostics


def test_settings_hook_accepts_empty_endpoint_and_model_and_the_defaults() -> None:
    """No endpoint and no model are assumed: both may be empty; every bound
    has a default."""

    minimal = {
        "synthesis": {"endpoint": "", "model": ""},
        "voices": {"allowed": ["narrator"], "default": "narrator"},
        "outputs": {"speakers": {"player": {"argv": ["player"]}}},
        "default_output": "speakers",
    }
    assert validate_settings(minimal) == []
    assert validate_settings("nope") == ["module 'audio_output': field 'settings': must be a mapping"]


async def test_activation_refuses_refused_settings_without_values() -> None:
    clock = ManualClock()
    context = runtime_context(clock=clock).for_module(MODULE_NAME)
    settings = _good_settings()
    settings["default_output"] = "elsewhere"
    with pytest.raises(AudioOutputModuleError) as refused:
        await activate(context, settings, {})
    assert "elsewhere" not in str(refused.value)
    assert API_KEY not in str(refused.value)


async def test_activation_defaults_to_the_subprocess_runner_and_opens_no_session() -> None:
    """The seams default to the shipped runner, a lazily created ``aiohttp``
    session and ``asyncio.sleep``: activation and ``prepare`` open nothing."""

    import modules.audio_output as audio_output

    created: list[Any] = []
    context = runtime_context().for_module(MODULE_NAME)
    original = audio_output.SESSION_FACTORY
    audio_output.SESSION_FACTORY = lambda: created.append(1)  # type: ignore[assignment]
    try:
        handle = await activate(context, _good_settings(), {})
        await handle.prepare()
        assert isinstance(handle._runner, audio_output.SubprocessPlayerRunner)
        assert handle._sleeper is asyncio.sleep
        assert created == []
        await handle.close()
    finally:
        audio_output.SESSION_FACTORY = original


async def test_the_subprocess_runner_feeds_a_real_player_its_stdin() -> None:
    """Decision 2: the shipped runner starts the configured command, writes
    the bytes to its stdin, closes it and reaps the exit status."""

    from modules.audio_output import SubprocessPlayerRunner

    runner = SubprocessPlayerRunner()
    body = wav_bytes(0.25)
    script = (
        "import sys; data = sys.stdin.buffer.read(); "
        f"sys.exit(0 if len(data) == {len(body)} and data[:4] == b'RIFF' else 3)"
    )
    process = await runner.start([sys.executable, "-c", script])
    accepted = 0
    for offset in range(0, len(body), WRITE_CHUNK_BYTES):
        accepted += await runner.write(process, body[offset : offset + WRITE_CHUNK_BYTES])
    await runner.close_stdin(process)
    assert accepted == len(body)
    assert await runner.wait(process) == 0


async def test_the_loader_discovers_the_manifest_and_prepare_binds_the_declared_specs() -> None:
    """R7/R9: the real loader validates the manifest and runs the hook with
    the reserved ``limits`` block; ``prepare`` binds both actions to the very
    specs discovered; a granted call succeeds through the context's executor
    on a second platform; the declared credential is redacted."""

    clock = ManualClock()
    context = runtime_context(clock=clock)
    transport = ScriptedSpeechTransport(wav_bytes(1.5))
    runner = RecordingPlayerRunner(clock=clock)
    loader = ModuleLoader(context.bus, ROOT / "modules", context=context, environ={})
    activations = await loader.activate_enabled(
        {
            "enabled_modules": [MODULE_NAME],
            "modules": {
                MODULE_NAME: {
                    **module_settings(transport, runner, clock),
                    "limits": {"attachments": {}},
                }
            },
        }
    )
    (activation,) = activations
    assert activation.manifest_version == 2
    assert activation.roles == frozenset()
    for action in (SPEAK_ACTION, PLAY_ACTION):
        assert action in context.actions.discovered()
        assert context.actions.bindings(action) == ()

    diagnostics: list[str] = []
    coordinator = PhaseCoordinator(activations, tasks=context.tasks, reporter=diagnostics.append)
    assert (await coordinator.start()).status == 0
    for action in (SPEAK_ACTION, PLAY_ACTION):
        (binding,) = context.actions.bindings(action)
        assert binding.spec == context.actions.discovered()[action]
        assert action in context.actions.registered_ready()

    context.actions._authorization.grant(
        AuthorizationRule(
            rule_id="grant-speak", action_name=SPEAK_ACTION, granted_permissions=("audio.speak",)
        )
    )
    call = ActionCall(
        action_name=SPEAK_ACTION,
        action_version=1,
        arguments={"text": "hello"},
        conversation_id="conversation-1",
        run_id=RUN_ID,
        call_id="call-loader",
        source_event_id="source-1",
        destination=FAKE_AUDIO,
        principal=PRINCIPAL,
        deadline=10_000.0,
        message_id="source-1",
    )
    observation = await context.executor.invoke(call)
    OBSERVED.append(observation)
    assert observation.status == "success", observation.error
    assert runner.received == wav_bytes(1.5)
    assert loader.redacted_credentials == 1

    assert (await coordinator.stop()).status == 0
    assert diagnostics == []
    assert SPEAK_ACTION not in context.actions.registered_ready()
    assert all(API_KEY not in text for text in trace_texts(context.bus))


def test_pyproject_ships_the_manifest() -> None:
    """R9/AC33 (this module's line): the package-data guard of
    ``tests/test_main.py`` requires it as soon as the directory exists."""

    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '"modules.audio_output" = ["module.yaml"]' in text


def test_module_names_no_platform() -> None:
    for path in (MODULE_DIR / "__init__.py", MANIFEST_PATH):
        assert "twitch" not in path.read_text(encoding="utf-8").lower()


# --------------------------------------------------------------------------- #
# Module-wide (AC2): never external_unknown — keep this test last
# --------------------------------------------------------------------------- #


def test_zz_no_observation_of_either_action_was_external_unknown() -> None:
    """AC2: across every call this module made, playback being local, no
    observation is ``external_unknown``."""

    assert len(OBSERVED) >= 30, len(OBSERVED)
    assert all(observation.status != "external_unknown" for observation in OBSERVED)
    assert DEFAULT_MAX_AUDIO_BYTES == 5_242_880
