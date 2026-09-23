"""The one shared runtime-context fixture (P19).

Every suite that activates a module on the versioned runtime builds the
context through :func:`runtime_context` here rather than assembling its own
(``test_twitch``, ``test_brain``, ``test_audit`` today; the retention,
lifecycle and integration suites next). The builder wires the **real**
:class:`~core.actions.ActionExecutor`, the real
:class:`~core.admission.AdmissionScheduler` the brain engine builds onto it
and the real :class:`~core.runtime.Supervision`; only the edges the process
cannot own are fakes — the model transport (:class:`FakeSession`, with the
Chat Completions body builders :func:`tool_call`, :func:`tool_calls`,
:func:`text_and_tool_call`, :func:`final`/:func:`completion` and the
:class:`ScriptedModel` shorthand), the send edge behind ``chat.write``
(:class:`FakeTransport` behind :class:`FakeSendProvider`), the screen
behind ``screen.capture`` (:class:`FakeCaptureSource`, :func:`png_bytes`),
the wire between brain and agent (:class:`MemoryWebSocketPair`), time itself
(:class:`ManualClock`) and randomness (``rng``). Phase 2 adds the audio and
stream edges (P5): generated WAV segments (:func:`wav_bytes`,
:func:`silent_wav`), the speech and transcription transports
(:class:`ScriptedSpeechTransport`, :class:`ScriptedTranscriptionTransport`),
the player behind ``audio_output`` (:class:`RecordingPlayerRunner`,
:class:`FakePlayer`), the recorder behind ``audio_input``
(:class:`FakeAudioSource`, :class:`RecordingSubprocessRunner`), the scene
provider (:class:`ScriptedSceneProvider`) and a platform poll service
(:class:`ScriptedPollService`) — every one driven by the injected clock,
none sleeping. The context carries a :class:`~core.runtime.ServiceRegistry`
by default so a publishing module is exercised on the harness (R7). Phase 3
adds the platform ``clip`` and ``moderation`` services
(:class:`ScriptedClipService`, :class:`ScriptedModerationService`), handed
to the fixture platform through its ``clip_service``/``moderation_service``
seams, the Helix answers of the twitch clip and moderation endpoints
(:func:`helix_clip_created`, :func:`helix_clip_listed`,
:func:`helix_clips_empty`, :func:`helix_refusal`, :func:`helix_not_live`,
:func:`helix_message_deleted`, :func:`helix_timeout_applied`), and a
viewer-memory directory (:func:`memory_directory`). A suite that mocked the
executor would hide AC19's executor-confirmed delivery, so the executor is
never mocked here.

The model transport tells a **probe** request (R2: ``tool_choice`` forcing
:data:`core.contracts.PROBE_TOOL`) from a scenario request by its body, never
by call order: probes are answered from ``probe_results`` and recorded in
``probe_calls``, scenario requests consume ``results`` and land in
``post_calls``, so a per-run request count keeps its meaning whether or not
the module under test probes at ``prepare``, and a suite that never prepares
the brain sees no probe at all. The harness imports nothing from
``modules.brain``: the probe tool name is the shared contract constant.

A module's own declarations — its trigger registration, its action bindings
and grants — stay in its suite; this module owns only the assembly every
context shares: bus, counters, supervision, tasks, executor and clock, with
one counter registry shared by all of them so a snapshot is readable
regardless of which component incremented (R8, AC28).

This file is a single point of failure for every suite that imports it; a
change here must be reviewed against each consumer, not just the suite that
motivated it (P24).
"""

from __future__ import annotations

import asyncio
import errno
import json
import random
import struct
import zlib
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path
from types import SimpleNamespace
from typing import Any, NamedTuple

from core.actions import (
    ERROR_TIMED_OUT,
    ActionExecutor,
    ActionRegistry,
    AuthorizationPolicy,
)
from core.attachments import AttachmentStore
from core.bus import EventBus
from core.contracts import PROBE_TOOL, ActionObservation, Counters, SessionKey
from core.lifecycle import SupervisedTasks
from core.runtime import RuntimeContext, ServiceRegistry, Supervision
from core.triggers import TriggerEngine


# --------------------------------------------------------------------------- #
# The fake clock: time moves only when a test says so
# --------------------------------------------------------------------------- #


class ManualClock:
    """A monotonic clock nobody waits on: time only moves when a test says so.

    ``sleep`` is the sleeper injected into a scheduler the engine owns; it
    parks a future until :meth:`advance` brings the clock past it, so no
    deadline ever fires because a test waited.
    """

    def __init__(self, now: float = 1000.0) -> None:
        self.now = now
        self._waiters: list[tuple[float, asyncio.Future[None]]] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, delay: float) -> None:
        if delay <= 0:
            await asyncio.sleep(0)
            return
        waiter: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        entry = (self.now + delay, waiter)
        self._waiters.append(entry)
        try:
            await waiter
        except asyncio.CancelledError:
            self._waiters = [item for item in self._waiters if item is not entry]
            raise

    def advance(self, delta: float) -> None:
        self.now += delta
        due = [item for item in self._waiters if item[0] <= self.now]
        self._waiters = [item for item in self._waiters if item[0] > self.now]
        for _deadline, waiter in due:
            if not waiter.done():
                waiter.set_result(None)


# --------------------------------------------------------------------------- #
# The fake model edge
# --------------------------------------------------------------------------- #


class FakeResponse:
    def __init__(self, status: int, body: Any) -> None:
        self.status = status
        self.body = body
        self.release_calls = 0

    async def json(self) -> Any:
        if isinstance(self.body, Exception):
            raise self.body
        return self.body

    def release(self) -> None:
        self.release_calls += 1


def is_probe_request(body: Any) -> bool:
    """Whether a request body is a capability probe (R2).

    A probe forces the runtime's probe tool through ``tool_choice``; that is
    the only thing that makes it one — not its position in the call
    sequence, so a suite that never prepares the brain is unaffected.
    """

    if not isinstance(body, Mapping):
        return False
    choice = body.get("tool_choice")
    if not isinstance(choice, Mapping):
        return False
    function = choice.get("function")
    return isinstance(function, Mapping) and function.get("name") == PROBE_TOOL


def carries_image_part(body: Any) -> bool:
    """Whether a request body carries at least one image part (R2, AC8)."""

    if not isinstance(body, Mapping):
        return False
    for message in body.get("messages", ()):
        content = message.get("content") if isinstance(message, Mapping) else None
        if not isinstance(content, list):
            continue
        for part in content:
            if isinstance(part, Mapping) and part.get("type") == "image_url":
                return True
    return False


def carries_audio_part(body: Any) -> bool:
    """Whether a request body carries at least one ``input_audio`` part (R4)."""

    if not isinstance(body, Mapping):
        return False
    for message in body.get("messages", ()):
        content = message.get("content") if isinstance(message, Mapping) else None
        if not isinstance(content, list):
            continue
        for part in content:
            if isinstance(part, Mapping) and part.get("type") == "input_audio":
                return True
    return False


PROBE_KIND_TEXT = "text"
PROBE_KIND_IMAGE = "image"
PROBE_KIND_AUDIO = "audio"


def probe_kind(body: Any) -> str:
    """Which capability a request body exercises: ``audio`` when it carries an
    ``input_audio`` part, ``image`` when it carries an image part, else
    ``text`` (R2, R4, AC15)."""

    if carries_audio_part(body):
        return PROBE_KIND_AUDIO
    if carries_image_part(body):
        return PROBE_KIND_IMAGE
    return PROBE_KIND_TEXT


def _raise_or_return(result: Any) -> Any:
    if isinstance(result, BaseException):
        raise result
    return result


class FakeSession:
    """The model transport: one prepared result per scenario request, in order.

    ``results`` answer scenario requests and are consumed one per request;
    ``probe_results`` answer probe requests (see :func:`is_probe_request`) and
    never touch ``results``. Without an explicit ``probe_results`` — or once
    the given ones are used up — every probe, the image and audio probes
    included (:func:`probe_kind` tells them apart), is answered with one valid
    forced tool call, so a module that probes at ``prepare`` becomes ready
    without the suite scripting the probes, and a probe never lands in
    ``post_calls`` (AC15).
    """

    def __init__(self, *results: Any, probe_results: Sequence[Any] | None = None) -> None:
        self.results = list(results)
        self.probe_results = list(probe_results) if probe_results is not None else []
        self.post_calls: list[dict[str, Any]] = []
        self.probe_calls: list[dict[str, Any]] = []
        self.close_calls = 0

    async def post(self, url: str, **kwargs: Any) -> Any:
        if is_probe_request(kwargs.get("json")):
            self.probe_calls.append({"url": url, **kwargs})
            return _raise_or_return(self._next_probe_result())
        return await self._answer(url, kwargs)

    async def _answer(self, url: str, kwargs: dict[str, Any]) -> Any:
        self.post_calls.append({"url": url, **kwargs})
        return _raise_or_return(self.results.pop(0))

    def _next_probe_result(self) -> Any:
        if self.probe_results:
            return self.probe_results.pop(0)
        return FakeResponse(200, tool_call(PROBE_TOOL, {"ok": True}))

    async def close(self) -> None:
        self.close_calls += 1


class HeldSession(FakeSession):
    """A model transport that holds every scenario request until released.

    Probes are answered at once: they belong to ``prepare``, and holding them
    would hold the readiness barrier, not the run a test wants to observe.
    """

    def __init__(self, *results: Any, probe_results: Sequence[Any] | None = None) -> None:
        super().__init__(*results, probe_results=probe_results)
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def _answer(self, url: str, kwargs: dict[str, Any]) -> Any:
        self.post_calls.append({"url": url, **kwargs})
        result = self.results.pop(0)
        self.entered.set()
        await self.release.wait()
        return _raise_or_return(result)


class ScriptedModel(FakeSession):
    """A model transport scripted with response bodies rather than responses.

    Each body becomes a 2xx :class:`FakeResponse`; a :class:`FakeResponse`
    or an exception passes through unchanged so a scenario can mix a
    non-2xx status or a transport failure into the script.
    """

    def __init__(self, *bodies: Any, probe_results: Sequence[Any] | None = None) -> None:
        super().__init__(
            *(
                body
                if isinstance(body, (FakeResponse, BaseException))
                else FakeResponse(200, body)
                for body in bodies
            ),
            probe_results=probe_results,
        )

    def requests(self) -> list[dict[str, Any]]:
        """The scenario request bodies, in order; probe bodies excluded."""

        return [call["json"] for call in self.post_calls]


# Chat Completions bodies the transport answers with (decision 2).


def _with_usage(body: dict[str, Any], usage: dict[str, int] | None) -> dict[str, Any]:
    if usage is not None:
        body["usage"] = usage
    return body


def _tool_call_entry(name: str, arguments: Any, index: int) -> dict[str, Any]:
    # A ``str`` is sent as given so a scenario can script arguments that do
    # not decode (``malformed_arguments``); anything else is JSON-encoded as
    # the backend would.
    encoded = arguments if isinstance(arguments, str) else json.dumps(arguments)
    return {
        "id": f"call-{index}",
        "type": "function",
        "function": {"name": name, "arguments": encoded},
    }


def tool_calls(
    calls: Sequence[tuple[str, Any] | Mapping[str, Any]],
    *,
    content: str | None = None,
    usage: dict[str, int] | None = None,
) -> dict[str, Any]:
    """A response carrying the given tool calls — ``(name, arguments)`` pairs
    or ``{"name", "arguments"}`` mappings — and, optionally, text."""

    entries = []
    for index, call in enumerate(calls, start=1):
        if isinstance(call, Mapping):
            entries.append(_tool_call_entry(call["name"], call["arguments"], index))
        else:
            name, arguments = call
            entries.append(_tool_call_entry(name, arguments, index))
    message: dict[str, Any] = {"role": "assistant", "content": content, "tool_calls": entries}
    return _with_usage(
        {"choices": [{"message": message, "finish_reason": "tool_calls"}]}, usage
    )


def tool_call(name: str, arguments: Any, *, usage: dict[str, int] | None = None) -> dict[str, Any]:
    """A response carrying exactly one tool call (a proposal, decision 2)."""

    return tool_calls([(name, arguments)], usage=usage)


def text_and_tool_call(
    text: str, name: str, arguments: Any, *, usage: dict[str, int] | None = None
) -> dict[str, Any]:
    """A response mixing text and a tool call (unsupported, decision 2)."""

    return tool_calls([(name, arguments)], content=text, usage=usage)


def final(text: str, usage: dict[str, int] | None = None) -> dict[str, Any]:
    """A final answer: text and no tool call (decision 2)."""

    return _with_usage({"choices": [{"message": {"content": text}}]}, usage)


completion = final


# --------------------------------------------------------------------------- #
# The fake send edge behind the real executor (AC19)
# --------------------------------------------------------------------------- #


# Outcomes the fake send edge can be told to produce for one delivery.
SENT = "sent"
FAIL_BEFORE_EMISSION = "fail_before_emission"
FAIL_AFTER_EMISSION = "fail_after_emission"
TIMEOUT_BEFORE_EMISSION = "timeout_before_emission"


class FakeTransport:
    """The send edge behind the real executor: what actually left (AC19)."""

    def __init__(self, *outcomes: str) -> None:
        self.outcomes = list(outcomes)
        self.sends: list[dict[str, Any]] = []


class FakeSendProvider:
    """A ``chat.write`` provider bound in the real registry over the fake edge."""

    name = "fake-send"

    def __init__(self, transport: FakeTransport) -> None:
        self._transport = transport

    async def invoke(self, invocation: Any) -> ActionObservation:
        invocation.mark_not_emitted()
        call = invocation.call
        outcome = self._transport.outcomes.pop(0) if self._transport.outcomes else SENT
        provenance = {"provider": self.name}
        if outcome == FAIL_BEFORE_EMISSION:
            raise RuntimeError("transport unavailable")
        if outcome == TIMEOUT_BEFORE_EMISSION:
            return ActionObservation(
                status="timeout",
                provenance=provenance,
                error={"code": ERROR_TIMED_OUT, "message": "", "retryable": False},
            )
        invocation.mark_emitted()
        if outcome == FAIL_AFTER_EMISSION:
            raise RuntimeError("no confirmation")
        self._transport.sends.append(
            {
                "text": call.arguments["text"],
                "destination": call.destination,
                "principal": call.principal,
                "run_id": call.run_id,
                "call_id": call.call_id,
                "conversation_id": call.conversation_id,
                "source_event_id": call.source_event_id,
                "message_id": call.message_id,
            }
        )
        return ActionObservation(
            status="success",
            provenance=provenance,
            result={"message_id": f"sent-{len(self._transport.sends)}"},
        )


# --------------------------------------------------------------------------- #
# The fake screen behind ``screen.capture`` (R5)
# --------------------------------------------------------------------------- #


def png_bytes(width: int, height: int) -> bytes:
    """A minimal valid PNG of ``width`` × ``height`` opaque black RGB pixels."""

    if width <= 0 or height <= 0:
        raise ValueError("png_bytes: width and height must be positive")

    def chunk(kind: bytes, payload: bytes) -> bytes:
        crc = zlib.crc32(kind + payload) & 0xFFFFFFFF
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", crc)

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    rows = (b"\x00" + b"\x00" * (3 * width)) * height
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


class FakeCaptureSource:
    """The screen behind a capture provider: hands out one prepared image.

    ``read`` returns ``data`` and records every call in ``calls``; a test sets
    ``fail_with`` to make the next read raise, or ``hold`` (a future the test
    resolves) to keep the read pending until it says so — the provider's
    timeout is exercised on the injected clock, never by waiting.
    ``content_type``, ``width`` and ``height`` describe the bytes so a test
    asserts the sniffed result against what it handed in.
    """

    def __init__(
        self,
        data: bytes | None = None,
        content_type: str = "image/png",
        width: int = 1,
        height: int = 1,
    ) -> None:
        self.data = data if data is not None else png_bytes(width, height)
        self.content_type = content_type
        self.width = width
        self.height = height
        self.fail_with: BaseException | None = None
        self.hold: asyncio.Future[Any] | None = None
        self.calls: list[dict[str, Any]] = []
        self.entered = asyncio.Event()

    async def read(self, *args: Any, **kwargs: Any) -> bytes:
        self.calls.append({"args": args, "kwargs": kwargs})
        self.entered.set()
        if self.hold is not None:
            await self.hold
        if self.fail_with is not None:
            raise self.fail_with
        return self.data

    __call__ = read

    def describe(self) -> dict[str, Any]:
        return {
            "content_type": self.content_type,
            "width": self.width,
            "height": self.height,
            "size": len(self.data),
        }


# --------------------------------------------------------------------------- #
# Generated WAV segments (R1, R3, R4)
# --------------------------------------------------------------------------- #


def wav_bytes(
    seconds: float,
    *,
    sample_rate: int = 16000,
    channels: int = 1,
    bits: int = 16,
    fill: int = 0,
) -> bytes:
    """A PCM RIFF/WAVE segment with the canonical 44-byte header.

    The data chunk holds ``round(seconds × sample_rate)`` frames of
    ``channels × bits / 8`` bytes, every byte equal to ``fill`` — so the
    default is silence and ``wav_bytes(1.5)`` carries 48 000 data bytes.
    """

    if bits <= 0 or bits % 8:
        raise ValueError("wav_bytes: bits must be a positive multiple of 8")
    if sample_rate <= 0 or channels <= 0 or seconds < 0:
        raise ValueError("wav_bytes: sample_rate and channels positive, seconds >= 0")
    block_align = channels * bits // 8
    frames = int(round(seconds * sample_rate))
    data = bytes([fill & 0xFF]) * (frames * block_align)
    fmt = struct.pack(
        "<HHIIHH", 1, channels, sample_rate, sample_rate * block_align, block_align, bits
    )
    return (
        b"RIFF"
        + struct.pack("<I", 36 + len(data))
        + b"WAVE"
        + b"fmt "
        + struct.pack("<I", len(fmt))
        + fmt
        + b"data"
        + struct.pack("<I", len(data))
        + data
    )


def silent_wav(seconds: float = 0.25) -> bytes:
    """A 16 kHz mono 16-bit all-zero segment: ``silent_wav()`` is the 8 044
    bytes the brain's audio probe and the transcription probe send."""

    return wav_bytes(seconds)


def _wav_data_layout(data: bytes) -> tuple[int, int] | None:
    """``(data_offset, declared_size)`` of the ``data`` chunk, or ``None``
    while *data* does not yet hold a RIFF/WAVE header up to that chunk."""

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


def wav_data_size(data: bytes) -> int:
    """How many bytes of the ``data`` chunk *data* actually holds."""

    layout = _wav_data_layout(bytes(data))
    if layout is None:
        raise ValueError("wav_data_size: not a RIFF/WAVE segment with a data chunk")
    offset, declared = layout
    return max(0, min(declared, len(data) - offset))


# --------------------------------------------------------------------------- #
# Scripted HTTP transports: speech synthesis and transcription (R1, R3)
# --------------------------------------------------------------------------- #


class _Held:
    """The answer that never comes on its own."""

    def __repr__(self) -> str:
        return "HELD"


HELD: Any = _Held()
"""A scripted answer that parks the request until the caller gives up.

Nothing in the transport ever times it out: the module's own bound, parked on
the injected sleeper, fires when the test advances the clock past the
timeout, and cancels the request. ``release(answer)`` hands a late answer to
the oldest held request, for the cases where a test lets it arrive.
"""


class _FakeBodyStream:
    """``FakeBytesResponse.content``: the body served by ``read(n)``, then ``b""``."""

    def __init__(self, response: "FakeBytesResponse") -> None:
        self._response = response

    async def read(self, n: int = -1) -> bytes:
        response = self._response
        body = await response.read()
        start = response._offset
        end = len(body) if n < 0 else min(len(body), start + n)
        response._offset = end
        response.content_reads += 1
        return body[start:end]


class FakeBytesResponse:
    """An HTTP answer read as bytes (``read``, or in chunks off ``content``) or as JSON (``json``).

    ``body`` is bytes, a text, a JSON-able value, or an exception every read
    raises (a body lost mid-answer). ``content`` is the body's stream, as on
    an ``aiohttp`` response; ``content_reads`` counts the chunks it served.
    """

    def __init__(self, status: int, body: Any = b"") -> None:
        self.status = status
        self.body = body
        self.release_calls = 0
        self.content_reads = 0
        self._offset = 0
        self.content = _FakeBodyStream(self)

    async def read(self) -> bytes:
        body = _raise_or_return(self.body)
        if isinstance(body, (bytes, bytearray, memoryview)):
            return bytes(body)
        if isinstance(body, str):
            return body.encode("utf-8")
        return json.dumps(body).encode("utf-8")

    async def text(self) -> str:
        return (await self.read()).decode("utf-8", errors="replace")

    async def json(self, **_kwargs: Any) -> Any:
        body = _raise_or_return(self.body)
        if isinstance(body, (bytes, bytearray, memoryview, str)):
            return json.loads(bytes(body) if not isinstance(body, str) else body)
        return body

    def release(self) -> None:
        self.release_calls += 1

    async def __aenter__(self) -> FakeBytesResponse:
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        self.release()


class _ScriptedHTTPTransport:
    """One scripted answer per ``post``, in order; every request recorded.

    The object is its own session factory (calling it returns itself and
    counts ``factory_calls``), so it fits a ``_…_transport`` seam that expects
    either a session or a factory. ``close`` is counted in ``close_calls``.
    """

    def __init__(self, *answers: Any, default: Any = None) -> None:
        self.answers = list(answers)
        self.default = default
        self.requests: list[dict[str, Any]] = []
        self.factory_calls = 0
        self.close_calls = 0
        self._held: list[asyncio.Future[Any]] = []

    def __call__(self, *_args: Any, **_kwargs: Any) -> Any:
        self.factory_calls += 1
        return self

    @property
    def held(self) -> int:
        """How many requests are parked on a :data:`HELD` answer right now."""

        return sum(1 for waiter in self._held if not waiter.done())

    def release(self, answer: Any) -> None:
        """Hand *answer* to the oldest request still parked on :data:`HELD`."""

        for waiter in self._held:
            if not waiter.done():
                waiter.set_result(answer)
                return
        raise AssertionError("no held request to release")

    async def post(self, url: str, **kwargs: Any) -> Any:
        self.requests.append(self._record(url, kwargs))
        answer = self.answers.pop(0) if self.answers else self._default_answer()
        if answer is HELD:
            waiter: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
            self._held.append(waiter)
            try:
                answer = await waiter
            finally:
                self._held = [item for item in self._held if item is not waiter]
        return self._normalise(_raise_or_return(answer))

    async def close(self) -> None:
        self.close_calls += 1

    def _record(self, url: str, kwargs: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def _default_answer(self) -> Any:
        raise NotImplementedError

    def _normalise(self, answer: Any) -> Any:
        raise NotImplementedError


class ScriptedSpeechTransport(_ScriptedHTTPTransport):
    """The speech-synthesis endpoint behind ``audio_output`` (R1).

    Each ``post`` records ``{url, headers, json}`` in ``requests`` and answers
    with the next script entry: a :class:`FakeBytesResponse`, raw bytes (a
    2xx answer carrying them), an ``int`` (that status, empty body), an
    exception (raised: the transport failed), or :data:`HELD`. Past the
    script every request is answered 200 with :func:`silent_wav` (or with
    ``default`` when given), so a prepare-time probe needs no scripting.
    """

    def _record(self, url: str, kwargs: dict[str, Any]) -> dict[str, Any]:
        return {
            "url": url,
            "headers": dict(kwargs.get("headers") or {}),
            "json": kwargs.get("json"),
        }

    def _default_answer(self) -> Any:
        return self.default if self.default is not None else silent_wav()

    def _normalise(self, answer: Any) -> Any:
        if isinstance(answer, (FakeBytesResponse, FakeResponse)):
            return answer
        if isinstance(answer, bool):
            raise TypeError("ScriptedSpeechTransport: a bool is not an answer")
        if isinstance(answer, int):
            return FakeBytesResponse(answer, b"")
        return FakeBytesResponse(200, answer)

    def bodies(self) -> list[Any]:
        """The JSON bodies of every request, in order."""

        return [request["json"] for request in self.requests]


def _form_value(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value)
    getvalue = getattr(value, "getvalue", None)
    if callable(getvalue):
        return bytes(getvalue())
    return value


def _form_fields(data: Any) -> list[tuple[str, Any, str | None]]:
    """``(name, value, filename)`` for each field of a multipart body.

    Reads a plain mapping, or the field list of an ``aiohttp.FormData``
    without importing it (the harness imports ``core`` only).
    """

    if isinstance(data, Mapping):
        return [(str(name), _form_value(value), None) for name, value in data.items()]
    fields = getattr(data, "_fields", None)
    if not isinstance(fields, list):
        return []
    found = []
    for entry in fields:
        type_options, _headers, value = entry
        name = type_options.get("name")
        filename = type_options.get("filename")
        found.append((str(name), _form_value(value), filename))
    return found


class ScriptedTranscriptionTransport(_ScriptedHTTPTransport):
    """The transcription endpoint behind ``audio_input`` (R3).

    Each ``post`` records the multipart fields: ``file`` (the WAV bytes of the
    field named ``file``, else of the first bytes-valued field),
    ``filename``, ``model`` and ``language`` (``None`` when absent), plus
    ``url``, ``headers`` and every field by name under ``fields``. Answers: a
    mapping (a 2xx JSON body), an ``int`` (that status), a
    :class:`FakeBytesResponse`, an exception, or :data:`HELD`. Past the script
    every request is answered 200 ``{"text": ""}`` (or ``default``).
    """

    def _record(self, url: str, kwargs: dict[str, Any]) -> dict[str, Any]:
        fields = _form_fields(kwargs.get("data"))
        named = {name: value for name, value, _filename in fields}
        file_entry = next((entry for entry in fields if entry[0] == "file"), None)
        if file_entry is None:
            file_entry = next(
                (entry for entry in fields if isinstance(entry[1], bytes)), None
            )
        return {
            "url": url,
            "headers": dict(kwargs.get("headers") or {}),
            "fields": named,
            "file": file_entry[1] if file_entry is not None else None,
            "filename": file_entry[2] if file_entry is not None else None,
            "model": named.get("model"),
            "language": named.get("language"),
        }

    def _default_answer(self) -> Any:
        return self.default if self.default is not None else {"text": ""}

    def _normalise(self, answer: Any) -> Any:
        if isinstance(answer, (FakeBytesResponse, FakeResponse)):
            return answer
        if isinstance(answer, bool):
            raise TypeError("ScriptedTranscriptionTransport: a bool is not an answer")
        if isinstance(answer, int):
            return FakeBytesResponse(answer, {"error": {"code": answer}})
        return FakeBytesResponse(200, answer)


# --------------------------------------------------------------------------- #
# The player behind ``audio_output`` (R1, decision 2, findings P2 and P5)
# --------------------------------------------------------------------------- #


class PlayerEvent(NamedTuple):
    """One entry of :attr:`RecordingPlayerRunner.events`.

    ``kind`` is ``start``, ``stop``, ``kill`` or ``exit``; ``player`` the
    1-based start number; ``output`` the output name the argv belongs to
    (from the runner's ``outputs`` map), else ``argv[0]``.
    """

    kind: str
    player: int
    output: str


PLAYER_TERMINATED = -15
PLAYER_KILLED = -9


class FakePlayer:
    """One scripted player process; :class:`RecordingPlayerRunner` starts it.

    ``accept_limit`` counts **data** bytes: the player accepts every byte up
    to the ``data`` chunk's payload (44 for the header :func:`wav_bytes`
    writes) plus ``accept_limit`` payload bytes, then its standard input
    blocks — ``FakePlayer(accept_limit=128_000)`` fed a 10 s segment holds
    128 044 bytes in ``received``. ``None`` accepts everything.
    ``accept_total`` (a harness extension) caps the total instead, header
    included, for a player that stops inside the header. A write that
    fills the limit returns the count it accepted from that chunk; the next
    write parks until the player exits, then raises ``BrokenPipeError``.

    The player exits with ``exit_code`` when its standard input is closed
    with everything accepted (unless ``holds_exit``: it keeps playing until
    :meth:`release`), or when :meth:`release` lets a blocked player go. A
    stop terminates it at once (exit ``-15``) unless ``ignores_terminate``,
    in which case only the kill after the grace ends it (exit ``-9``).
    ``startable=False`` makes :meth:`RecordingPlayerRunner.start` fail.
    ``close_error`` is raised by the close (after the exit it causes): a
    pipe that broke while its buffered tail was flushing.
    """

    def __init__(
        self,
        accept_limit: int | None = None,
        exit_code: int = 0,
        startable: bool = True,
        ignores_terminate: bool = False,
        *,
        accept_total: int | None = None,
        holds_exit: bool = False,
        close_error: BaseException | None = None,
    ) -> None:
        self.accept_limit = accept_limit
        self.close_error = close_error
        self.exit_code = exit_code
        self.startable = startable
        self.ignores_terminate = ignores_terminate
        self.accept_total = accept_total
        self.holds_exit = holds_exit
        self.received = bytearray()
        self.argv: tuple[str, ...] = ()
        self.number = 0
        self.output = ""
        self.returncode: int | None = None
        self.stdin_closed = False
        self._data_offset: int | None = None
        self._runner: RecordingPlayerRunner | None = None
        self._exited: asyncio.Future[None] | None = None

    @property
    def alive(self) -> bool:
        return self.returncode is None

    @property
    def accepted(self) -> int:
        return len(self.received)

    def _limit(self, prospective: bytes) -> int | None:
        limits = []
        if self.accept_total is not None:
            limits.append(self.accept_total)
        if self.accept_limit is not None:
            if self._data_offset is None:
                layout = _wav_data_layout(prospective)
                if layout is not None:
                    self._data_offset = layout[0]
            if self._data_offset is not None:
                limits.append(self._data_offset + self.accept_limit)
        return min(limits) if limits else None

    def offer(self, chunk: bytes) -> int:
        """Accept what the limit allows from *chunk*; the count accepted.

        The synchronous half of a write, with no blocking: a full pipe
        accepts 0.
        """

        if not self.alive:
            raise BrokenPipeError(errno.EPIPE, "player exited")
        chunk = bytes(chunk)
        limit = self._limit(bytes(self.received[:65536]) + chunk[:65536])
        room = len(chunk) if limit is None else max(0, limit - len(self.received))
        accepted = min(len(chunk), room)
        self.received += chunk[:accepted]
        return accepted

    async def write(self, chunk: bytes) -> int:
        accepted = self.offer(chunk)
        if accepted == 0 and chunk:
            await self._wait_exit()
            raise BrokenPipeError(errno.EPIPE, "player exited")
        return accepted

    def close_stdin(self) -> None:
        self.stdin_closed = True
        if self.alive and not self.holds_exit:
            self._exit(self.exit_code)
        if self.close_error is not None:
            raise self.close_error

    def release(self) -> None:
        """Let a blocked or still-playing player exit with ``exit_code``."""

        if self.alive:
            self._exit(self.exit_code)

    async def wait(self) -> int:
        await self._wait_exit()
        assert self.returncode is not None
        return self.returncode

    def _future(self) -> asyncio.Future[None]:
        if self._exited is None:
            self._exited = asyncio.get_running_loop().create_future()
            if not self.alive:
                self._exited.set_result(None)
        return self._exited

    async def _wait_exit(self) -> None:
        if self.alive:
            await asyncio.shield(self._future())

    def _exit(self, code: int) -> None:
        self.returncode = code
        if self._exited is not None and not self._exited.done():
            self._exited.set_result(None)
        if self._runner is not None:
            self._runner._event("exit", self)


class RecordingPlayerRunner:
    """The player runner seam of decision 2, recording every step.

    ``start(argv)`` hands out the next scripted :class:`FakePlayer` (a
    default one past the script) and records ``argv`` in ``starts``; an
    unstartable player is recorded in ``failed_starts`` and raises
    ``FileNotFoundError``. ``write``, ``close_stdin`` and ``wait`` delegate
    to the player. ``stop(process, grace_seconds)`` records the stop, then
    terminates the player; one that ignores termination is killed after
    ``grace_seconds`` on the injected sleeper (``clock.sleep`` or
    ``sleeper``) — recorded in ``kills`` — and only then can ``wait``
    return; ``kill(process)`` kills it at once, also recorded in ``kills``.
    ``events`` is the ordered ``start``/``stop``/``kill``/``exit``
    log the ownership assertions read (finding P5). ``outputs`` optionally
    maps an output name to its argv so events carry the output name.
    """

    def __init__(
        self,
        *players: FakePlayer,
        clock: ManualClock | None = None,
        sleeper: Any = None,
        outputs: Mapping[str, Sequence[str]] | None = None,
    ) -> None:
        self.script = list(players)
        self.players: list[FakePlayer] = []
        self.starts: list[list[str]] = []
        self.failed_starts: list[list[str]] = []
        self.stops: list[int] = []
        self.kills: list[int] = []
        self.events: list[PlayerEvent] = []
        self._sleeper = sleeper if sleeper is not None else (clock.sleep if clock else None)
        self._outputs = {tuple(argv): name for name, argv in (outputs or {}).items()}

    async def start(self, argv: Sequence[str]) -> FakePlayer:
        player = self.script.pop(0) if self.script else FakePlayer()
        if not player.startable:
            self.failed_starts.append(list(argv))
            raise FileNotFoundError(errno.ENOENT, "player cannot be started")
        player.argv = tuple(argv)
        player.number = len(self.players) + 1
        player.output = self._outputs.get(player.argv, argv[0] if argv else "")
        player._runner = self
        self.players.append(player)
        self.starts.append(list(argv))
        self._event("start", player)
        return player

    async def write(self, process: FakePlayer, chunk: bytes) -> int:
        return await process.write(chunk)

    async def close_stdin(self, process: FakePlayer) -> None:
        process.close_stdin()

    async def wait(self, process: FakePlayer) -> int:
        return await process.wait()

    async def stop(self, process: FakePlayer, grace_seconds: float) -> None:
        self.stops.append(process.number)
        self._event("stop", process)
        if not process.alive:
            return
        if not process.ignores_terminate:
            process._exit(PLAYER_TERMINATED)
            return
        if self._sleeper is None:
            raise AssertionError("RecordingPlayerRunner: a stop grace needs a clock or sleeper")
        await self._sleeper(grace_seconds)
        if process.alive:
            self.kills.append(process.number)
            self._event("kill", process)
            process._exit(PLAYER_KILLED)

    def kill(self, process: FakePlayer) -> None:
        """Kill *process* at once, without awaiting anything (recorded in ``kills``)."""

        if process.alive:
            self.kills.append(process.number)
            self._event("kill", process)
            process._exit(PLAYER_KILLED)

    def release(self) -> None:
        """Let every player still running exit with its ``exit_code``."""

        for player in self.players:
            player.release()

    @property
    def received(self) -> bytes:
        """What the most recently started player accepted."""

        return bytes(self.players[-1].received) if self.players else b""

    def kinds(self) -> list[tuple[str, int]]:
        """``events`` as ``(kind, player)`` pairs, the shape order checks read."""

        return [(event.kind, event.player) for event in self.events]

    def _event(self, kind: str, player: FakePlayer) -> None:
        self.events.append(PlayerEvent(kind, player.number, player.output))


# --------------------------------------------------------------------------- #
# The recorder behind ``audio_input`` (R3)
# --------------------------------------------------------------------------- #


class FakeAudioSource:
    """An injectable audio source: ``read(seconds)`` hands out WAV bytes.

    ``data`` is the scripted segment (``wav_bytes(seconds)`` when ``None``);
    every read is recorded in ``reads``. ``fail_with`` makes reads raise. A
    ``gated`` source emits nothing until :meth:`release`; :meth:`kill`
    (counted in ``kills``) ends a pending read with no bytes, as a killed
    recorder would.
    """

    def __init__(
        self,
        data: bytes | None = None,
        *,
        gated: bool = False,
        fail_with: BaseException | None = None,
    ) -> None:
        self.data = data
        self.gated = gated
        self.fail_with = fail_with
        self.reads: list[float] = []
        self.kills = 0
        self.released = False
        self.killed = False
        self.entered = asyncio.Event()
        self._gate: asyncio.Future[None] | None = None

    async def read(self, seconds: float) -> bytes:
        self.reads.append(seconds)
        self.entered.set()
        if self.fail_with is not None:
            raise self.fail_with
        if self.gated and not (self.released or self.killed):
            if self._gate is None or self._gate.done():
                self._gate = asyncio.get_running_loop().create_future()
            await asyncio.shield(self._gate)
        if self.killed:
            return b""
        return self.data if self.data is not None else wav_bytes(seconds)

    __call__ = read

    def release(self) -> None:
        self.released = True
        self._open_gate()

    def kill(self) -> None:
        self.kills += 1
        self.killed = True
        self._open_gate()

    def _open_gate(self) -> None:
        if self._gate is not None and not self._gate.done():
            self._gate.set_result(None)


class _FakeStdout:
    def __init__(self, process: FakeRecorderProcess) -> None:
        self._process = process

    async def read(self, n: int = -1) -> bytes:
        return await self._process._read(n)


class FakeRecorderProcess:
    """A recorder process shaped like ``asyncio.subprocess.Process``:
    ``stdout.read(n)``, ``terminate()``, ``kill()``, ``await wait()``,
    ``returncode``, ``pid``."""

    def __init__(self, runner: RecordingSubprocessRunner, number: int, output: bytes, gated: bool) -> None:
        self._runner = runner
        self.pid = number
        self.argv: tuple[str, ...] = ()
        self._pending = bytes(output)
        self._gated = gated
        self.returncode: int | None = None
        self.stdout = _FakeStdout(self)
        self._wake: asyncio.Future[None] | None = None

    async def _read(self, n: int) -> bytes:
        while self._gated and self.returncode is None:
            if self._wake is None or self._wake.done():
                self._wake = asyncio.get_running_loop().create_future()
            await asyncio.shield(self._wake)
        if self._gated:
            # Ended before it was released: it never emitted anything.
            return b""
        if not self._pending:
            if self.returncode is None:
                self._finish(0)
            return b""
        size = len(self._pending) if n is None or n < 0 else n
        chunk, self._pending = self._pending[:size], self._pending[size:]
        return chunk

    def release(self) -> None:
        """Let a gated recorder emit its output."""

        self._gated = False
        self._poke()

    def terminate(self) -> None:
        if self.returncode is None:
            self._runner.terminates.append(self.pid)
            self._finish(PLAYER_TERMINATED)

    def kill(self) -> None:
        self._runner.kills.append(self.pid)
        if self.returncode is None:
            self._finish(PLAYER_KILLED)

    async def wait(self) -> int:
        while self.returncode is None:
            if self._wake is None or self._wake.done():
                self._wake = asyncio.get_running_loop().create_future()
            await asyncio.shield(self._wake)
        return self.returncode

    def _finish(self, code: int) -> None:
        self.returncode = code
        self._poke()

    def _poke(self) -> None:
        if self._wake is not None and not self._wake.done():
            self._wake.set_result(None)


class RecordingSubprocessRunner:
    """The subprocess seam behind ``audio_input``'s command sources.

    ``spawn(argv)`` (or calling the runner as ``create_subprocess_exec``
    would be, ``runner(*argv, **kwargs)``) records ``argv`` in ``spawns`` and
    returns a :class:`FakeRecorderProcess` whose stdout yields the next
    scripted output (``wav_bytes(1.0)`` past the script). ``gated`` recorders
    emit nothing until their ``release()`` or a kill; kills and terminations
    are recorded by process number in ``kills``/``terminates``.
    ``startable=False`` makes every spawn raise ``FileNotFoundError``.
    """

    def __init__(self, *outputs: bytes, gated: bool = False, startable: bool = True) -> None:
        self.outputs = list(outputs)
        self.gated = gated
        self.startable = startable
        self.spawns: list[list[str]] = []
        self.kills: list[int] = []
        self.terminates: list[int] = []
        self.processes: list[FakeRecorderProcess] = []

    @property
    def spawn_count(self) -> int:
        return len(self.spawns)

    @property
    def kill_count(self) -> int:
        return len(self.kills)

    async def spawn(self, argv: Sequence[str], **_kwargs: Any) -> FakeRecorderProcess:
        if not self.startable:
            raise FileNotFoundError(errno.ENOENT, "recorder cannot be started")
        output = self.outputs.pop(0) if self.outputs else wav_bytes(1.0)
        process = FakeRecorderProcess(self, len(self.processes) + 1, output, self.gated)
        process.argv = tuple(argv)
        self.processes.append(process)
        self.spawns.append(list(argv))
        return process

    async def __call__(self, *argv: str, **kwargs: Any) -> FakeRecorderProcess:
        return await self.spawn(list(argv), **kwargs)

    def release(self) -> None:
        for process in self.processes:
            process.release()


# --------------------------------------------------------------------------- #
# The scene provider behind ``stream.scene.set`` (R6)
# --------------------------------------------------------------------------- #


SCENE_OK = "ok"
SCENE_SWALLOW = "swallow"
SCENE_RAISE = "raise"
SCENE_UNKNOWN = "unknown"
SCENE_UNKNOWN_CODE = 600


class ScriptedSceneProvider:
    """A scene provider (``connected``, ``list_scenes``, ``current_scene``,
    ``set_scene``, ``close``) driven by a script.

    ``set_scene(name)`` records ``name`` in ``sets`` and answers the next
    entry of ``set_answers`` (``ok`` past the script), with the request/
    response shape of the websocket provider:

    - ``ok`` — the scene becomes ``current`` (recorded in ``applied``);
      answers ``{"result": True}``; a name outside ``scenes`` answers as
      ``unknown`` instead;
    - ``swallow`` — the scene is applied but the answer is lost: raises
      ``TimeoutError``;
    - ``raise`` — the connection fails before the scene is applied: raises
      ``ConnectionError``;
    - ``unknown`` — ``{"result": False, "code": 600}``, nothing applied;
    - an ``int`` — ``{"result": False, "code": <int>}``, nothing applied.

    ``current_scene()`` records its answer in ``reads`` and answers the next
    ``read_answers`` entry (a scene name, or ``raise``/an exception to fail),
    else ``current``. While disconnected every call raises
    ``ConnectionError`` and is not recorded; ``list_scenes`` calls are
    counted in ``lists``.
    """

    def __init__(
        self,
        scenes: Sequence[str] = ("Talking", "Gaming"),
        current: str = "Gaming",
        *,
        set_answers: Sequence[Any] = (),
        read_answers: Sequence[Any] = (),
        connected: bool = True,
    ) -> None:
        self.scenes = list(scenes)
        self.current = current
        self.set_answers = list(set_answers)
        self.read_answers = list(read_answers)
        self._connected = connected
        self.sets: list[str] = []
        self.applied: list[str] = []
        self.reads: list[Any] = []
        self.lists = 0
        self.close_calls = 0

    @property
    def connected(self) -> bool:
        return self._connected

    def disconnect(self) -> None:
        self._connected = False

    def reconnect(self) -> None:
        self._connected = True

    def _require_connected(self) -> None:
        if not self._connected:
            raise ConnectionError("scene provider disconnected")

    async def list_scenes(self) -> list[str]:
        self._require_connected()
        self.lists += 1
        return list(self.scenes)

    async def current_scene(self) -> str:
        self._require_connected()
        answer = self.read_answers.pop(0) if self.read_answers else self.current
        self.reads.append(answer)
        if answer == SCENE_RAISE:
            raise ConnectionError("scene read failed")
        return _raise_or_return(answer)

    async def set_scene(self, name: str) -> dict[str, Any]:
        self._require_connected()
        self.sets.append(name)
        answer = self.set_answers.pop(0) if self.set_answers else SCENE_OK
        if answer == SCENE_RAISE:
            raise ConnectionError("scene provider disconnected")
        if answer == SCENE_UNKNOWN or (
            answer in (SCENE_OK, SCENE_SWALLOW) and name not in self.scenes
        ):
            return {"result": False, "code": SCENE_UNKNOWN_CODE}
        if isinstance(answer, int) and not isinstance(answer, bool):
            return {"result": False, "code": answer}
        if answer not in (SCENE_OK, SCENE_SWALLOW):
            _raise_or_return(answer)
            raise AssertionError(f"ScriptedSceneProvider: unknown set answer {answer!r}")
        self.current = name
        self.applied.append(name)
        if answer == SCENE_SWALLOW:
            raise TimeoutError("scene set answer lost")
        return {"result": True}

    async def close(self) -> None:
        self.close_calls += 1
        self._connected = False


# --------------------------------------------------------------------------- #
# A platform poll service (R7)
# --------------------------------------------------------------------------- #


class PollServiceError(Exception):
    """Base of the scripted poll service's failures."""


class PollTransportError(PollServiceError):
    """The transport failed; ``sent`` says whether the request had left."""

    def __init__(self, sent: bool) -> None:
        super().__init__("poll request lost" if sent else "poll request not sent")
        self.sent = sent


class PollStatusError(PollServiceError):
    """A non-2xx answer: ``status`` and a sanitised ``message``, never a body."""

    def __init__(self, status: int, message: str = "") -> None:
        super().__init__(message or f"poll request failed with status {status}")
        self.status = status
        self.message = message or f"status {status}"


class PollMalformedAnswer(PollServiceError):
    """A 2xx answer whose body is not a poll."""

    sent = True


class ScriptedPollService:
    """A poll service (``create``, ``get``) scripted per channel.

    ``create(channel_id, question, options, duration_seconds)`` records the
    call in ``creates`` and applies the next outcome for the channel
    (:meth:`script_create`, else the service-wide ``create`` outcomes, else
    ``ok``):

    - ``ok`` — a poll is added to ``polls[channel_id]`` and returned;
    - ``lost`` — the poll is added but the answer is lost:
      ``PollTransportError(sent=True)``;
    - ``status:<code>`` — nothing added, ``PollStatusError(code)``;
    - ``transport`` — nothing sent: ``PollTransportError(sent=False)``;
    - ``malformed`` — the poll is added, ``PollMalformedAnswer``;
    - ``held`` — nothing added, the call parks until cancelled (or
      :meth:`release_held`);
    - an exception — raised.

    ``get(channel_id)`` records the channel in ``gets`` and answers the next
    outcome: ``list`` (the channel's ``polls``, the default), ``raise``
    (``PollTransportError(sent=True)``), an explicit sequence (returned
    as given) or an exception. Poll times come from ``clock`` (0.0 without
    one). The failure classes are the harness's own; a consumer tells them
    apart by their ``sent``/``status`` attributes, never by importing them.
    """

    def __init__(
        self,
        *,
        clock: Any = None,
        create: Sequence[Any] = (),
        get: Sequence[Any] = (),
    ) -> None:
        self._clock = clock
        self._create_default = list(create)
        self._get_default = list(get)
        self._create_script: dict[str, list[Any]] = {}
        self._get_script: dict[str, list[Any]] = {}
        self.polls: dict[str, list[dict[str, Any]]] = {}
        self.creates: list[dict[str, Any]] = []
        self.gets: list[str] = []
        self._held: list[asyncio.Future[Any]] = []
        self._next_id = 0

    def script_create(self, channel_id: str, *outcomes: Any) -> None:
        self._create_script.setdefault(channel_id, []).extend(outcomes)

    def script_get(self, channel_id: str, *outcomes: Any) -> None:
        self._get_script.setdefault(channel_id, []).extend(outcomes)

    def add_poll(
        self,
        channel_id: str,
        question: str,
        options: Sequence[str],
        *,
        started_at: float | None = None,
        duration_seconds: int = 60,
        state: str = "active",
    ) -> dict[str, Any]:
        """Put a poll in the channel's table directly (a poll made elsewhere)."""

        self._next_id += 1
        started = started_at if started_at is not None else self._now()
        poll = {
            "poll_id": f"poll-{self._next_id}",
            "question": question,
            "options": list(options),
            "started_at": started,
            "ends_at": started + duration_seconds,
            "state": state,
        }
        self.polls.setdefault(channel_id, []).append(poll)
        return dict(poll)

    def release_held(self, answer: Any = None) -> None:
        for waiter in self._held:
            if not waiter.done():
                waiter.set_result(answer)
                return
        raise AssertionError("no held create to release")

    def _now(self) -> float:
        return float(self._clock()) if self._clock is not None else 0.0

    @staticmethod
    def _next(script: dict[str, list[Any]], default: list[Any], channel_id: str, fallback: str) -> Any:
        queue = script.get(channel_id)
        if queue:
            return queue.pop(0)
        if default:
            return default.pop(0)
        return fallback

    async def create(
        self, channel_id: str, question: str, options: Sequence[str], duration_seconds: int
    ) -> dict[str, Any]:
        self.creates.append(
            {
                "channel_id": channel_id,
                "question": question,
                "options": list(options),
                "duration_seconds": duration_seconds,
            }
        )
        outcome = self._next(self._create_script, self._create_default, channel_id, "ok")
        if isinstance(outcome, BaseException):
            raise outcome
        if outcome == "transport":
            raise PollTransportError(sent=False)
        if isinstance(outcome, str) and outcome.startswith("status:"):
            status = int(outcome.split(":", 1)[1])
            raise PollStatusError(status)
        if outcome == "held":
            waiter: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
            self._held.append(waiter)
            try:
                return _raise_or_return(await waiter)
            finally:
                self._held = [item for item in self._held if item is not waiter]
        if outcome not in ("ok", "lost", "malformed"):
            raise AssertionError(f"ScriptedPollService: unknown create outcome {outcome!r}")
        poll = self.add_poll(
            channel_id, question, options, duration_seconds=duration_seconds
        )
        if outcome == "lost":
            raise PollTransportError(sent=True)
        if outcome == "malformed":
            raise PollMalformedAnswer("poll answer malformed")
        return poll

    async def get(self, channel_id: str) -> list[dict[str, Any]]:
        self.gets.append(channel_id)
        outcome = self._next(self._get_script, self._get_default, channel_id, "list")
        if isinstance(outcome, BaseException):
            raise outcome
        if outcome == "raise":
            raise PollTransportError(sent=True)
        if outcome == "list":
            return [
                {key: poll[key] for key in ("poll_id", "question", "options", "started_at", "state")}
                for poll in self.polls.get(channel_id, [])
            ]
        if isinstance(outcome, Sequence) and not isinstance(outcome, str):
            return [dict(entry) for entry in outcome]
        raise AssertionError(f"ScriptedPollService: unknown get outcome {outcome!r}")


# --------------------------------------------------------------------------- #
# Platform clip and moderation services (phase 3: R2, R5)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class ClipCreateResult:
    """A classified create answer, the taxonomy every platform's clip service
    returns: ``accepted`` (with ``clip_id``), ``offline``, ``rejected``,
    ``rate`` or ``uncertain``. ``scripted`` is the outcome that produced it."""

    outcome: str
    clip_id: str | None = None
    edit_url: str | None = None
    scripted: str = ""


@dataclass(frozen=True, slots=True)
class ModerationResult:
    """A classified moderation answer: ``ok``, ``rejected``, ``rate`` or
    ``uncertain``. ``scripted`` is the outcome that produced it."""

    outcome: str
    scripted: str = ""


class _InFlight:
    """Counts overlapping requests: ``overlap`` is set once two are in flight."""

    def __init__(self) -> None:
        self.in_flight = 0
        self.max_in_flight = 0

    @property
    def overlap(self) -> bool:
        return self.max_in_flight > 1

    def __enter__(self) -> None:
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)

    def __exit__(self, *exc: Any) -> None:
        self.in_flight -= 1


_CLIP_CREATE_CLASSES = {
    "offline": "offline",
    "auth": "rejected",
    "rate": "rate",
    "lost": "uncertain",
    "server_error": "uncertain",
}


class ScriptedClipService:
    """A platform ``clip`` service (``create``, ``lookup``) driven by a script.

    ``create(channel_id)`` pops the next create outcome (else
    ``accepted clip-<n>``) and returns a :class:`ClipCreateResult`:

    - ``accepted <id>`` — ``accepted`` with that ``clip_id``;
    - ``offline`` → ``offline``; ``auth`` → ``rejected``; ``rate`` →
      ``rate``; ``lost`` and ``server_error`` → ``uncertain`` (the request
      left, its answer is not trustworthy);
    - ``hold`` — the request has left and parks until cancelled or
      :meth:`release_held` hands it the outcome to apply;
    - an exception — raised.

    ``lookup(clip_id)`` pops the next lookup outcome (else
    ``lookup_default``): ``found <url>`` answers that URL, ``found`` alone a
    URL derived from the id, ``empty`` answers ``None``; an exception is
    raised.

    Every request lands in ``requests`` as ``{"op", "at", ...}`` stamped by
    ``clock`` (0.0 without one); ``creates`` counts the create requests, and
    ``in_flight``/``max_in_flight``/``overlap`` record whether two requests
    were ever outstanding at once.
    """

    def __init__(
        self,
        *,
        clock: Any = None,
        create: Sequence[Any] = (),
        lookup: Sequence[Any] = (),
        lookup_default: str = "found",
    ) -> None:
        self._clock = clock
        self._create_script = list(create)
        self._lookup_script = list(lookup)
        self._lookup_default = lookup_default
        self._flight = _InFlight()
        self._held: list[asyncio.Future[Any]] = []
        self.requests: list[dict[str, Any]] = []

    def script_create(self, *outcomes: Any) -> "ScriptedClipService":
        self._create_script.extend(outcomes)
        return self

    def script_lookup(self, *outcomes: Any) -> "ScriptedClipService":
        self._lookup_script.extend(outcomes)
        return self

    @property
    def creates(self) -> int:
        return sum(1 for request in self.requests if request["op"] == "create")

    @property
    def lookups(self) -> int:
        return sum(1 for request in self.requests if request["op"] == "lookup")

    @property
    def in_flight(self) -> int:
        return self._flight.in_flight

    @property
    def max_in_flight(self) -> int:
        return self._flight.max_in_flight

    @property
    def overlap(self) -> bool:
        return self._flight.overlap

    def release_held(self, outcome: Any = "accepted") -> None:
        for waiter in self._held:
            if not waiter.done():
                waiter.set_result(outcome)
                return
        raise AssertionError("ScriptedClipService: no held create to release")

    def _now(self) -> float:
        return float(self._clock()) if self._clock is not None else 0.0

    async def create(self, channel_id: str) -> ClipCreateResult:
        with self._flight:
            self.requests.append({"op": "create", "channel_id": channel_id, "at": self._now()})
            number = self.creates
            outcome = self._create_script.pop(0) if self._create_script else "accepted"
            if outcome == "hold":
                waiter: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
                self._held.append(waiter)
                try:
                    outcome = await waiter
                finally:
                    self._held = [item for item in self._held if item is not waiter]
            return self._classify_create(outcome, number)

    @staticmethod
    def _classify_create(outcome: Any, number: int) -> ClipCreateResult:
        if isinstance(outcome, BaseException):
            raise outcome
        if not isinstance(outcome, str):
            raise AssertionError(f"ScriptedClipService: unknown create outcome {outcome!r}")
        word, _, argument = outcome.partition(" ")
        if word == "accepted":
            clip_id = argument.strip() or f"clip-{number}"
            return ClipCreateResult(
                "accepted", clip_id, f"https://clips.example/{clip_id}/edit", outcome
            )
        if word in _CLIP_CREATE_CLASSES and not argument:
            return ClipCreateResult(_CLIP_CREATE_CLASSES[word], scripted=outcome)
        raise AssertionError(f"ScriptedClipService: unknown create outcome {outcome!r}")

    async def lookup(self, clip_id: str) -> str | None:
        with self._flight:
            self.requests.append({"op": "lookup", "clip_id": clip_id, "at": self._now()})
            outcome = self._lookup_script.pop(0) if self._lookup_script else self._lookup_default
            if isinstance(outcome, BaseException):
                raise outcome
            word, _, argument = str(outcome).partition(" ")
            if word == "found":
                return argument.strip() or f"https://clips.example/{clip_id}"
            if word == "empty" and not argument:
                return None
            raise AssertionError(f"ScriptedClipService: unknown lookup outcome {outcome!r}")


_MODERATION_CLASSES = {
    "ok": "ok",
    "rejected": "rejected",
    "rate": "rate",
    "lost": "uncertain",
    "server_error": "uncertain",
}

DEFAULT_MODERATION_OPERATIONS = frozenset({"delete_message", "timeout"})


class ScriptedModerationService:
    """A platform ``moderation`` service driven by a script.

    ``operations`` is what the platform offers (default ``{delete_message,
    timeout}``). ``round_duration(seconds)`` is the platform's unit rounding
    hook: up to a multiple of ``duration_unit_seconds`` (1, no rounding, by
    default; 60 for a whole-minute platform). ``apply(operation, *,
    channel_id, ...)`` records the request in ``requests`` stamped by
    ``clock`` and returns a :class:`ModerationResult` from the next scripted
    outcome (else ``ok``): ``ok``, ``rejected``, ``rate``, or ``lost`` and
    ``server_error`` → ``uncertain``; an exception is raised. An operation
    outside ``operations`` is a caller bug (the module must refuse it
    ``platform_unsupported`` with 0 requests): it is recorded and raises.
    """

    def __init__(
        self,
        *,
        clock: Any = None,
        operations: Any = DEFAULT_MODERATION_OPERATIONS,
        outcomes: Sequence[Any] = (),
        duration_unit_seconds: int = 1,
    ) -> None:
        self._clock = clock
        self.operations = frozenset(operations)
        self._script = list(outcomes)
        self._unit = int(duration_unit_seconds)
        self.requests: list[dict[str, Any]] = []

    def script(self, *outcomes: Any) -> "ScriptedModerationService":
        self._script.extend(outcomes)
        return self

    def round_duration(self, seconds: int) -> int:
        return -(-int(seconds) // self._unit) * self._unit

    async def apply(
        self,
        operation: str,
        *,
        channel_id: str,
        message_id: str | None = None,
        target_author_id: str | None = None,
        duration_seconds: int | None = None,
        reason: str | None = None,
    ) -> ModerationResult:
        self.requests.append(
            {
                "operation": operation,
                "channel_id": channel_id,
                "message_id": message_id,
                "target_author_id": target_author_id,
                "duration_seconds": duration_seconds,
                "reason": reason,
                "at": float(self._clock()) if self._clock is not None else 0.0,
            }
        )
        if operation not in self.operations:
            raise AssertionError(
                f"ScriptedModerationService: operation {operation!r} is not offered"
            )
        outcome = self._script.pop(0) if self._script else "ok"
        if isinstance(outcome, BaseException):
            raise outcome
        if outcome not in _MODERATION_CLASSES:
            raise AssertionError(f"ScriptedModerationService: unknown outcome {outcome!r}")
        return ModerationResult(_MODERATION_CLASSES[outcome], outcome)


# --------------------------------------------------------------------------- #
# Helix answers for the twitch clip and moderation endpoints (phase 3: R2, R5)
# --------------------------------------------------------------------------- #
#
# :class:`FakeResponse` bodies shaped like the platform's answers, for a suite
# scripting the twitch module's HTTP session. A refusal carries its ``message``
# in the body only — a module must never echo it.


def helix_clip_created(
    clip_id: str = "clip-1", edit_url: str | None = None, *, status: int = 202
) -> FakeResponse:
    """``POST helix/clips`` accepted: the new clip's id and edit URL."""

    return FakeResponse(
        status,
        {
            "data": [
                {
                    "id": clip_id,
                    "edit_url": edit_url or f"https://clips.twitch.example/{clip_id}/edit",
                }
            ]
        },
    )


def helix_clip_listed(clip_id: str = "clip-1", url: str | None = None) -> FakeResponse:
    """``GET helix/clips?id=`` finding the clip."""

    return FakeResponse(
        200,
        {
            "data": [
                {
                    "id": clip_id,
                    "url": url or f"https://clips.twitch.example/{clip_id}",
                    "created_at": "2026-09-23T12:00:00Z",
                }
            ],
            "pagination": {},
        },
    )


def helix_clips_empty() -> FakeResponse:
    """``GET helix/clips?id=`` before the clip is listed."""

    return FakeResponse(200, {"data": [], "pagination": {}})


def helix_refusal(status: int, message: str = "refused") -> FakeResponse:
    """A Helix error answer: ``{error, status, message}``."""

    return FakeResponse(status, {"error": "Error", "status": status, "message": message})


def helix_not_live(status: int = 404) -> FakeResponse:
    """The platform's refusal to clip a channel that is not live."""

    return helix_refusal(status, "Clipping is not possible for an offline channel: not live")


def helix_message_deleted() -> FakeResponse:
    """``DELETE helix/moderation/chat`` done: 204, no body."""

    return FakeResponse(204, ValueError("no content"))


def helix_timeout_applied(user_id: str = "viewer-7", duration: int = 60) -> FakeResponse:
    """``POST helix/moderation/bans`` with a duration done: the timeout's end."""

    return FakeResponse(
        200,
        {
            "data": [
                {
                    "broadcaster_id": "broadcaster-42",
                    "moderator_id": "bot-24",
                    "user_id": user_id,
                    "created_at": "2026-09-23T12:00:00Z",
                    "end_time": f"2026-09-23T12:{duration // 60:02d}:{duration % 60:02d}Z",
                }
            ]
        },
    )


# --------------------------------------------------------------------------- #
# A viewer-memory directory (phase 3: R3)
# --------------------------------------------------------------------------- #


def memory_directory(tmp_path: Path, name: str = "memory") -> tuple[Path, Callable[[], list[str]]]:
    """A fresh, empty directory under *tmp_path* and a lister of its ``*.json``.

    The lister returns the sorted names (not paths) of the directory's
    immediate ``*.json`` files, read anew at every call.
    """

    directory = tmp_path / name
    directory.mkdir(parents=True, exist_ok=False)

    def json_names() -> list[str]:
        return sorted(entry.name for entry in directory.glob("*.json") if entry.is_file())

    return directory, json_names


# --------------------------------------------------------------------------- #
# The in-memory wire between brain and agent (R6)
# --------------------------------------------------------------------------- #


class WSMsgType(IntEnum):
    """The message types an ``aiohttp`` WebSocket hands out, same values.

    ``IntEnum`` compares by value, so code that checks a received message
    against ``aiohttp.WSMsgType.TEXT`` sees the same answer under the pair.
    """

    TEXT = 1
    BINARY = 2
    CLOSE = 8
    PING = 9
    PONG = 10
    CLOSING = 256
    CLOSED = 257
    ERROR = 258


# The code a real transport reports when a connection ended without a close
# handshake (RFC 6455 §7.4.1); what ``drop`` leaves on both ends.
WS_ABNORMAL_CLOSURE = 1006

_WS_TERMINAL = frozenset({WSMsgType.CLOSE, WSMsgType.CLOSING, WSMsgType.CLOSED, WSMsgType.ERROR})


@dataclass(frozen=True)
class WSMessage:
    type: WSMsgType
    data: Any
    extra: Any = None

    def json(self) -> Any:
        return json.loads(self.data)


class MemoryWebSocket:
    """One end of :class:`MemoryWebSocketPair`.

    The minimal surface both ``aiohttp.web.WebSocketResponse`` and
    ``aiohttp.ClientWebSocketResponse`` expose — ``send_str``, ``send_bytes``,
    ``send_json``, ``receive`` (and the typed ``receive_*``), ``close``,
    ``closed``, ``close_code``, async iteration — so the proxy and agent code
    runs unchanged over it. Frames arrive in the order they were sent; a
    ``close`` reaches the peer as a ``CLOSE`` message carrying the code,
    after which the peer's ``close_code`` is that code and further receives
    return ``CLOSED``. ``sent`` keeps every frame this end wrote.
    """

    def __init__(self) -> None:
        self._inbox: asyncio.Queue[WSMessage] = asyncio.Queue()
        self._peer: MemoryWebSocket | None = None
        self.closed = False
        self.close_code: int | None = None
        self.sent: list[WSMessage] = []

    def _attach(self, peer: MemoryWebSocket) -> None:
        self._peer = peer

    def _deliver(self, message: WSMessage) -> None:
        if self._peer is None:
            raise RuntimeError("memory websocket: end is not paired")
        if self.closed or self._peer.closed:
            raise ConnectionResetError("Cannot write to closing transport")
        self.sent.append(message)
        self._peer._inbox.put_nowait(message)

    async def send_str(self, data: str) -> None:
        if not isinstance(data, str):
            raise TypeError(f"data argument must be str ({type(data)!r})")
        self._deliver(WSMessage(WSMsgType.TEXT, data))

    async def send_bytes(self, data: bytes) -> None:
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise TypeError(f"data argument must be byte-ish ({type(data)!r})")
        self._deliver(WSMessage(WSMsgType.BINARY, bytes(data)))

    async def send_json(self, data: Any) -> None:
        await self.send_str(json.dumps(data))

    async def receive(self, timeout: float | None = None) -> WSMessage:
        # ``timeout`` is accepted for signature parity only: a test drives
        # deadlines on the injected clock, never on a transport wait.
        if self.closed and self._inbox.empty():
            return WSMessage(WSMsgType.CLOSED, None)
        message = await self._inbox.get()
        if message.type is WSMsgType.CLOSE and not self.closed:
            self.closed = True
            self.close_code = message.data
        return message

    async def receive_str(self, timeout: float | None = None) -> str:
        message = await self.receive(timeout)
        if message.type is not WSMsgType.TEXT:
            raise TypeError(f"Received message {message.type}:{message.data!r} is not str")
        return message.data

    async def receive_bytes(self, timeout: float | None = None) -> bytes:
        message = await self.receive(timeout)
        if message.type is not WSMsgType.BINARY:
            raise TypeError(f"Received message {message.type}:{message.data!r} is not bytes")
        return message.data

    async def receive_json(self, timeout: float | None = None) -> Any:
        return json.loads(await self.receive_str(timeout))

    async def close(self, code: int = 1000, message: bytes = b"") -> bool:
        if self.closed:
            return False
        self.closed = True
        self.close_code = code
        if self._peer is not None and not self._peer.closed:
            self._peer._inbox.put_nowait(WSMessage(WSMsgType.CLOSE, code, message))
        # Wake a local receive() already parked on the inbox, as aiohttp's
        # close does, so a receive loop on this end terminates on shutdown.
        self._inbox.put_nowait(WSMessage(WSMsgType.CLOSED, None))
        return True

    def exception(self) -> BaseException | None:
        return None

    def __aiter__(self) -> MemoryWebSocket:
        return self

    async def __anext__(self) -> WSMessage:
        message = await self.receive()
        if message.type in _WS_TERMINAL:
            raise StopAsyncIteration
        return message


class MemoryWebSocketPair:
    """Two paired in-memory WebSocket ends: ``server`` (the brain's
    ``WebSocketResponse``) and ``client`` (the agent's
    ``ClientWebSocketResponse``). ``drop`` ends both abruptly, as a lost
    connection would: no close handshake, ``close_code`` 1006 on both ends,
    frames already queued still readable, then ``CLOSED``.
    """

    def __init__(self) -> None:
        self.server = MemoryWebSocket()
        self.client = MemoryWebSocket()
        self.server._attach(self.client)
        self.client._attach(self.server)

    @property
    def ends(self) -> tuple[MemoryWebSocket, MemoryWebSocket]:
        return self.server, self.client

    def drop(self) -> None:
        for end in self.ends:
            if not end.closed:
                end.closed = True
                end.close_code = WS_ABNORMAL_CLOSURE
                end._inbox.put_nowait(WSMessage(WSMsgType.CLOSED, None))


# --------------------------------------------------------------------------- #
# Recording doubles the suites read instead of the run engine
# --------------------------------------------------------------------------- #


class RecordingScheduler:
    """Records what ingestion admits; runs nothing (the run engine is P16)."""

    def __init__(self) -> None:
        self.admissions: list[tuple[SessionKey, Any]] = []

    def admit(self, session_key: SessionKey, work: Any) -> Any:
        self.admissions.append((session_key, work))
        return SimpleNamespace(accepted=True, run_id=f"run-{len(self.admissions)}")


# --------------------------------------------------------------------------- #
# The one shared runtime-context fixture
# --------------------------------------------------------------------------- #


def runtime_context(
    bus: EventBus | None = None,
    *,
    clock: ManualClock | None = None,
    scheduler: Any = None,
    triggers: Any = None,
    trigger_registry: Any = None,
    dedup_max_entries: int = 8,
    dedup_ttl_seconds: float = 60.0,
    chat: Any = None,
    authorization: AuthorizationPolicy | None = None,
    actions: ActionRegistry | None = None,
    attachments: AttachmentStore | None = None,
    rng: Any = None,
    services: Any = None,
) -> RuntimeContext:
    """The versioned runtime every suite builds identically (P19).

    Real executor, real supervision, real supervised tasks, one counter
    registry shared by both so a snapshot reads every component's counts.
    ``trigger_registry`` is wrapped in the real trigger engine sharing the
    context's clock and counters, with the dedup window the caller bounds;
    a pre-built engine (or a fake recording decisions) can be passed as
    ``triggers`` instead. ``actions`` replaces the empty default registry so
    a suite can bind its providers before activation; ``authorization``
    feeds the executor's default-deny check (R5). ``scheduler`` is the
    shared admission scheduler — when ``None``, a module that owns one
    (the brain engine) builds the real :class:`AdmissionScheduler` itself
    onto the same context. ``attachments`` is the one store both the modules
    lease from (through the context) and the executor validates every
    ``image_ref`` part against (R4): a suite exercising images passes a
    store built on the same ``clock``, and the default ``None`` keeps a
    suite without images exactly as before. ``rng`` is the one random
    source every component draws from (trigger probability rules, proxy
    session ids): a seeded ``random.Random(0)`` by default, so a run replays
    identically without a suite seeding anything. ``services`` is the service
    registry modules publish into and resolve from (R7): a fresh
    :class:`~core.runtime.ServiceRegistry` by default, so a publishing module
    and its consumer meet on the harness exactly as on the assembled runtime;
    a suite needing a context *without* a registry replaces the field with
    ``dataclasses.replace(context, services=None)``.
    """

    target_bus = bus if bus is not None else EventBus()
    target_clock = clock if clock is not None else ManualClock()
    target_rng = rng if rng is not None else random.Random(0)
    target_services = services if services is not None else ServiceRegistry()
    counters = Counters()
    policy = authorization if authorization is not None else AuthorizationPolicy()
    registry = actions if actions is not None else ActionRegistry(authorization=policy)
    supervision = Supervision(target_bus, counters=counters)
    engine = triggers
    if trigger_registry is not None:
        engine = TriggerEngine(
            trigger_registry,
            dedup_max_entries=dedup_max_entries,
            dedup_ttl_seconds=dedup_ttl_seconds,
            clock=target_clock,
            counters=counters,
            rng=target_rng,
        )
    return RuntimeContext(
        bus=target_bus,
        actions=registry,
        supervision=supervision,
        tasks=SupervisedTasks(),
        executor=ActionExecutor(
            registry,
            policy,
            supervision=supervision,
            counters=counters,
            clock=target_clock,
            attachments=attachments,
        ),
        triggers=engine,
        chat=chat,
        attachments=attachments,
        scheduler=scheduler,
        clock=target_clock,
        rng=target_rng,
        services=target_services,
    )


# --------------------------------------------------------------------------- #
# Shared loop helpers: bounded waits, never sleeps
# --------------------------------------------------------------------------- #


async def settle(turns: int = 20) -> None:
    """Give the loop *turns* bare reschedules. Zero delay, so no time passes."""

    for _ in range(turns):
        await asyncio.sleep(0)


async def wait_until(predicate: Any, turns: int = 2000) -> None:
    for _ in range(turns):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition did not become true within the turn budget")


def events_of(bus: EventBus, event_type: str) -> list[dict[str, Any]]:
    return [event for event in bus.list_events() if event["type"] == event_type]


def trace_texts(bus: EventBus) -> list[str]:
    """Every retained event on *bus* flattened to one JSON text, in order.

    What a value-free assertion searches: a secret, a payload or a path must
    be a substring of none of them.
    """

    return [
        json.dumps(event, sort_keys=True, ensure_ascii=False, default=str)
        for event in bus.list_events()
    ]


# --------------------------------------------------------------------------- #
# Self-checks: every suite imports this file, so a broken double fails the run
# --------------------------------------------------------------------------- #


def _self_check() -> None:
    silent = silent_wav(0.25)
    assert len(silent) == 8044, len(silent)
    assert silent[:4] == b"RIFF" and silent[8:12] == b"WAVE"
    assert _wav_data_layout(silent) == (44, 8000)
    assert not any(silent[44:]), "silent_wav samples are not all zero"
    assert wav_data_size(wav_bytes(1.5)) == 48_000
    assert wav_data_size(wav_bytes(10.0)) == 320_000
    assert wav_data_size(wav_bytes(1.0, sample_rate=8000, channels=2, bits=8)) == 16_000

    audio_part = {"type": "input_audio", "input_audio": {"data": "", "format": "wav"}}
    image_part = {"type": "image_url", "image_url": {"url": "data:image/png;base64,"}}
    with_audio = {"messages": [{"role": "user", "content": [audio_part]}]}
    with_image = {"messages": [{"role": "user", "content": [image_part]}]}
    assert carries_audio_part(with_audio)
    assert not carries_audio_part(with_image)
    assert not carries_audio_part({"messages": [{"role": "user", "content": "text"}]})
    assert probe_kind(with_audio) == PROBE_KIND_AUDIO
    assert probe_kind(with_image) == PROBE_KIND_IMAGE
    assert probe_kind({"messages": []}) == PROBE_KIND_TEXT

    segment = wav_bytes(10.0)
    player = FakePlayer(accept_limit=128_000)
    accepted = 0
    for start in range(0, len(segment), 32_768):
        taken = player.offer(segment[start : start + 32_768])
        accepted += taken
        if taken < len(segment[start : start + 32_768]):
            break
    assert accepted == 128_044 and len(player.received) == 128_044, accepted
    assert player.offer(segment[accepted:]) == 0
    everything = FakePlayer()
    assert everything.offer(segment) == len(segment)
    header_only = FakePlayer(accept_total=20)
    assert header_only.offer(segment) == 20

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_self_check_phase3_services())
    finally:
        loop.close()
    with tempfile.TemporaryDirectory() as scratch:
        directory, json_names = memory_directory(Path(scratch))
        assert directory.is_dir() and json_names() == []
        (directory / "b.json").write_text("{}", encoding="utf-8")
        (directory / "a.json").write_text("{}", encoding="utf-8")
        (directory / "notes.txt").write_text("", encoding="utf-8")
        assert json_names() == ["a.json", "b.json"]


async def _self_check_phase3_services() -> None:
    clock = ManualClock(10.0)
    clips = ScriptedClipService(
        clock=clock,
        create=["accepted c-1", "offline", "auth", "rate", "lost", "server_error"],
        lookup=["empty", "found https://clips.example/x"],
    )
    first = await clips.create("chan")
    assert (first.outcome, first.clip_id) == ("accepted", "c-1")
    assert [(await clips.create("chan")).outcome for _ in range(5)] == [
        "offline", "rejected", "rate", "uncertain", "uncertain",
    ]
    clock.advance(1.0)
    assert await clips.lookup("c-1") is None
    assert await clips.lookup("c-1") == "https://clips.example/x"
    assert await clips.lookup("c-1") == "https://clips.example/c-1"
    assert clips.creates == 6 and clips.lookups == 3
    assert [request["at"] for request in clips.requests] == [10.0] * 6 + [11.0] * 3
    assert not clips.overlap and clips.in_flight == 0

    clips.script_create("hold", "hold")
    held = [asyncio.ensure_future(clips.create("chan")) for _ in range(2)]
    for _ in range(5):
        await asyncio.sleep(0)
    assert clips.in_flight == 2 and clips.overlap
    clips.release_held("accepted c-9")
    clips.release_held("rate")
    assert (await held[0]).clip_id == "c-9" and (await held[1]).outcome == "rate"
    assert clips.in_flight == 0

    moderation = ScriptedModerationService(
        clock=clock, outcomes=["rejected", "rate", "lost", "server_error"]
    )
    assert moderation.operations == frozenset({"delete_message", "timeout"})
    assert moderation.round_duration(61) == 61
    assert ScriptedModerationService(duration_unit_seconds=60).round_duration(61) == 120
    outcomes = [
        (await moderation.apply("delete_message", channel_id="chan", message_id="m")).outcome
        for _ in range(5)
    ]
    assert outcomes == ["rejected", "rate", "uncertain", "uncertain", "ok"]
    narrow = ScriptedModerationService(operations={"timeout"})
    try:
        await narrow.apply("delete_message", channel_id="chan", message_id="m")
    except AssertionError:
        pass
    else:
        raise AssertionError("an unoffered operation must not pass silently")
    assert len(moderation.requests) == 5 and moderation.requests[0]["at"] == 11.0


_self_check()
