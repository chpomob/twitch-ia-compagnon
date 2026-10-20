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
viewer-memory directory (:func:`memory_directory`), and the Kick webhook
edge: a fixed, test-only RSA key pair (:data:`KICK_TEST_KEY`) and a
:class:`SignedWebhookSender` that signs deliveries with it and posts them
through an in-process ``aiohttp`` test client, and the YouTube edge (P20): a
:class:`ScriptedTokenEndpoint` issuing, refusing or losing access tokens with
a request log, and a :class:`ScriptedLiveChatAPI` answering the broadcast
lookup, list pages carrying ``pollingIntervalMillis`` and the insert, delete
and ban requests, with a per-endpoint request count. A suite that mocked the
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

Phase 8 (P1) adds a recorded random source (:class:`RecordedRandom`) and the
autouse guard ``_ac33_isolation``: under it, creating a socket (the event
loop's own wakeup pair excepted) or starting a process raises, for every test
of the suite but the nodes of the shrinking ``AC33_PENDING`` ledger and an
operator-opted-in real-instance trial.

This file is a single point of failure for every suite that imports it; a
change here must be reviewed against each consumer, not just the suite that
motivated it (P24).
"""

from __future__ import annotations

import asyncio
import base64
import errno
import hashlib
import json
import os
import random
import socket
import struct
import subprocess
import sys
import time
import zlib
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import IntEnum
from pathlib import Path
from types import SimpleNamespace
from typing import Any, NamedTuple

import pytest

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

    def next_deadline(self) -> float | None:
        """The earliest instant a parked sleeper wakes at; ``None`` when none is."""

        return min((deadline for deadline, _ in self._waiters), default=None)

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


async def wait_until(predicate: Any, turns: int = 2000, *, seconds: float | None = None) -> None:
    """Yield bare turns until *predicate* holds, at most *turns* of them.

    A turn count bounds loop-only work, but not work handed to a worker
    thread (``asyncio.to_thread``): the thread runs in wall time, so a loaded
    machine can outlast any turn count. *seconds*, when given, is a wall-time
    floor for such waits: the wait fails only once the turns are spent and
    that much real time has passed. It never sleeps, and no virtual clock moves.
    """

    deadline = None if seconds is None else time.monotonic() + seconds
    spent = 0
    while True:
        if predicate():
            return
        spent += 1
        if spent >= turns and (deadline is None or time.monotonic() >= deadline):
            break
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
# The Kick webhook edge (phase 3 P18): a signed delivery, standard library only
# --------------------------------------------------------------------------- #


class KickTestKey(NamedTuple):
    """An RSA key pair for the Kick webhook suite: **TEST-ONLY, NEVER A SECRET**.

    Generated once for this repository's tests and published with them on
    purpose; it signs nothing but in-process test deliveries. ``public_pem``
    is what a module is configured with (a SubjectPublicKeyInfo PEM),
    ``private_exponent`` is what :class:`SignedWebhookSender` signs with.
    """

    public_pem: str
    modulus: int
    public_exponent: int
    private_exponent: int

    @property
    def size_bytes(self) -> int:
        return (self.modulus.bit_length() + 7) // 8


#: TEST-ONLY RSA-2048 key pair (see :class:`KickTestKey`).
KICK_TEST_KEY = KickTestKey(
    public_pem=(
        "-----BEGIN PUBLIC KEY-----\n"
        "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAqWl5WKQUnFyDRgT7Fkkh\n"
        "5+nhXOlq6g7ZeDt2w9kDYFg1+zJtEDRIQHe7F/Xeap9VOQrRcasIX+7g1AxXvVbJ\n"
        "ZwzoTQ9N/igYZgrsoPKQz+tbLCJe1PvDPn6X8aTiqon8sgRKOSzdZ2Zhb9b7gN/W\n"
        "wzM2fofbezjgsV4bA6gRhbJ1tF/wTA00LMyUvWO2P7m6KVmg77eyOVS/EL4gUhqR\n"
        "ZPHBZ2Q7zZbW6tFagKnL1u0S35I/bQKqvHQDTj6V34vhS+cd02kAVATwAFbnKyEV\n"
        "/HO6ToMOziucvFlTG3dXhk5ZpB6eTvCVWURHjn9S0y60UkayljSYviIF73KjfnD9\n"
        "8QIDAQAB\n"
        "-----END PUBLIC KEY-----\n"
    ),
    modulus=int(
        "a9697958a4149c5c834604fb164921e7e9e15ce96aea0ed9783b76c3d9036058"
        "35fb326d1034484077bb17f5de6a9f55390ad171ab085feee0d40c57bd56c967"
        "0ce84d0f4dfe2818660aeca0f290cfeb5b2c225ed4fbc33e7e97f1a4e2aa89fc"
        "b2044a392cdd6766616fd6fb80dfd6c333367e87db7b38e0b15e1b03a81185b2"
        "75b45ff04c0d342ccc94bd63b63fb9ba2959a0efb7b23954bf10be20521a9164"
        "f1c167643bcd96d6ead15a80a9cbd6ed12df923f6d02aabc74034e3e95df8be1"
        "4be71dd369005404f00056e72b2115fc73ba4e830ece2b9cbc59531b7757864e"
        "59a41e9e4ef0955944478e7f52d32eb45246b2963498be2205ef72a37e70fdf1",
        16,
    ),
    public_exponent=65537,
    private_exponent=int(
        "5bc2ffb15d9eb45affd7eb56bd697b6e0ca6bf16c78c63e2b357322b3edeaf4e"
        "d85e699fdd891421a738b5efbd3b6f764fe16634f57921cd580643713ae34950"
        "0213c9b9a27b29e89d4d0982dc20481ea951ac844544e12a76938295d7189c89"
        "3d3b49a0d286523d884575e2e0995dd0796ae8fa598dd2ef11e3712a77dc95d7"
        "83b7da99b8ec82ef4308f387467797533cdff053f2fc9bd6098545785da56183"
        "71bff4e242c1e4c3a3c4e9add148c8f4af1103193c41fda30ee514bc6ff9021e"
        "dbfa5af355cb7dbf9ff7ded5bb4087f4f7010c5ea7d5c7828cfc31870f0da3d6"
        "b22190977eb3a8ed8c6e7e068123f621ac70c4b050e2aa0f2afaeaddb224a69",
        16,
    ),
)

#: The DER DigestInfo prefix of SHA-256 (RFC 8017 §9.2), restated here so the
#: sender signs without importing the module under test.
SHA256_DIGEST_INFO_PREFIX = bytes.fromhex("3031300d060960864801650304020105000420")


class SignedWebhookSender:
    """Signs and posts Kick webhook deliveries as the platform would.

    The signature is RSASSA-PKCS1-v1_5/SHA-256 over ``message id + "." +
    timestamp + "." + raw body``, computed as ``pow(m, d, n)`` with *key*;
    the timestamp is RFC 3339 UTC read from *clock* (epoch seconds, so a
    :class:`ManualClock` built on an epoch drives it). :meth:`post` sends
    through an in-process ``aiohttp`` test client (``TestClient``) — nothing
    leaves the process. Every header and the body can be overridden, so a
    suite forges a bad signature, a stale timestamp or a duplicate id on
    purpose.
    """

    def __init__(self, key: KickTestKey, clock: Callable[[], float]) -> None:
        self.key = key
        self.clock = clock
        self._sequence = 0

    def next_message_id(self) -> str:
        self._sequence += 1
        return f"kick-delivery-{self._sequence}"

    def timestamp(self, at: float | None = None) -> str:
        instant = self.clock() if at is None else at
        moment = datetime.fromtimestamp(instant, tz=timezone.utc)
        return moment.strftime("%Y-%m-%dT%H:%M:%SZ")

    def encode(self, message: bytes, digest_info_prefix: bytes = SHA256_DIGEST_INFO_PREFIX) -> bytes:
        """The EMSA-PKCS1-v1_5 encoding of SHA-256(*message*); a suite forges
        a wrong DigestInfo by passing another *digest_info_prefix*."""

        digest_info = digest_info_prefix + hashlib.sha256(message).digest()
        size = self.key.size_bytes
        return b"\x00\x01" + b"\xff" * (size - len(digest_info) - 3) + b"\x00" + digest_info

    def sign_encoded(self, encoded: bytes) -> str:
        """``pow(m, d, n)`` of an encoded message, base64."""

        value = pow(int.from_bytes(encoded, "big"), self.key.private_exponent, self.key.modulus)
        return base64.b64encode(value.to_bytes(self.key.size_bytes, "big")).decode("ascii")

    def sign(self, message_id: str, timestamp: str, body: bytes) -> str:
        content = message_id.encode("utf-8") + b"." + timestamp.encode("utf-8") + b"." + body
        return self.sign_encoded(self.encode(content))

    def headers(
        self,
        event_type: str,
        body: bytes,
        *,
        message_id: str | None = None,
        timestamp: str | None = None,
        signature: str | None = None,
    ) -> dict[str, str]:
        """The delivery headers: signed over *body* unless *signature* is given."""

        delivery_id = message_id if message_id is not None else self.next_message_id()
        stamp = timestamp if timestamp is not None else self.timestamp()
        return {
            "Content-Type": "application/json",
            "Kick-Event-Message-Id": delivery_id,
            "Kick-Event-Subscription-Id": "subscription-test",
            "Kick-Event-Message-Timestamp": stamp,
            "Kick-Event-Type": event_type,
            "Kick-Event-Version": "1",
            "Kick-Event-Signature": (
                signature if signature is not None else self.sign(delivery_id, stamp, body)
            ),
        }

    @staticmethod
    def body(payload: Any) -> bytes:
        if isinstance(payload, (bytes, bytearray)):
            return bytes(payload)
        return json.dumps(payload, separators=(",", ":")).encode("utf-8")

    async def post(
        self,
        client: Any,
        event_type: str,
        payload: Any,
        *,
        path: str = "/",
        message_id: str | None = None,
        timestamp: str | None = None,
        signature: str | None = None,
    ) -> int:
        """Post one delivery through *client* (an ``aiohttp`` ``TestClient``,
        or a client session given a full URL as *path*); return the status."""

        body = self.body(payload)
        headers = self.headers(
            event_type,
            body,
            message_id=message_id,
            timestamp=timestamp,
            signature=signature,
        )
        async with client.post(path, data=body, headers=headers) as response:
            await response.read()
            return response.status


# --------------------------------------------------------------------------- #
# The YouTube edge (phase 3 P20): a token endpoint and a live-chat API
# --------------------------------------------------------------------------- #


TOKEN_ISSUED = "issue"
TOKEN_REFUSED = "refuse"
TOKEN_LOST = "lost"
TOKEN_SERVER_ERROR = "server_error"


class ScriptedTokenEndpoint:
    """The OAuth token endpoint a refresh token is exchanged at.

    ``script`` answers the exchanges in order, then every later one with a
    fresh token of ``default_lifetime`` seconds. An entry is a lifetime in
    seconds (an ``int``: a token ``access-<n>`` is issued), or
    :data:`TOKEN_REFUSED` (400 ``invalid_grant``), :data:`TOKEN_LOST` (the
    answer never arrives) or :data:`TOKEN_SERVER_ERROR` (a 503). ``requests``
    logs each exchange with its instant on *clock* and its grant type — never
    the client secret; ``issued`` logs each token issued with its expiry.
    """

    def __init__(
        self,
        clock: Callable[[], float],
        *script: Any,
        default_lifetime: int = 3600,
    ) -> None:
        self.clock = clock
        self.script = list(script)
        self.default_lifetime = default_lifetime
        self.requests: list[dict[str, Any]] = []
        self.issued: list[dict[str, Any]] = []

    @property
    def refreshes(self) -> int:
        return len(self.requests)

    def answer(self, data: Any) -> Any:
        form = dict(data) if isinstance(data, Mapping) else {}
        self.requests.append(
            {
                "at": self.clock(),
                "grant_type": form.get("grant_type"),
                "refresh_token": form.get("refresh_token"),
                "client_id": form.get("client_id"),
                "has_client_secret": bool(form.get("client_secret")),
            }
        )
        entry = self.script.pop(0) if self.script else self.default_lifetime
        if entry == TOKEN_REFUSED:
            return FakeResponse(
                400, {"error": "invalid_grant", "error_description": "Token has been revoked."}
            )
        if entry == TOKEN_LOST:
            return ConnectionError("token endpoint unreachable")
        if entry == TOKEN_SERVER_ERROR:
            return FakeResponse(503, {"error": "backend_error"})
        token = f"access-{len(self.issued) + 1}"
        self.issued.append({"token": token, "at": self.clock(), "expires_at": self.clock() + entry})
        return FakeResponse(
            200,
            {"access_token": token, "expires_in": entry, "token_type": "Bearer", "scope": "youtube"},
        )

    def valid_at(self, token: str, instant: float) -> bool:
        """Whether *token* was issued here and had not expired at *instant*."""

        return any(
            entry["token"] == token and entry["at"] <= instant < entry["expires_at"]
            for entry in self.issued
        )


LIVE_CHAT_ENDPOINTS = ("broadcast_lookup", "list", "insert", "delete", "ban")


class ScriptedLiveChatAPI:
    """The live-chat API and, through *token_endpoint*, the token exchange:
    the injected session of the youtube module.

    Requests are routed by URL: ``…/token`` to *token_endpoint*,
    ``…/liveBroadcasts`` to the broadcast lookup, ``…/liveChat/messages`` to
    list (GET), insert (POST) or delete (DELETE), ``…/liveChat/bans`` to ban.
    ``broadcasts`` maps a channel id to the ``liveChatId`` of its active
    broadcast (absent: no active broadcast). A list answers the next of
    ``pages`` (a page body, a :class:`FakeResponse` or an exception), then a
    default empty page carrying ``polling_interval_ms``. Insert, delete and
    ban answer the next of their scripted answers, then a 200/204 success.
    ``counts`` is the per-endpoint request count; ``requests`` logs every API
    request with its endpoint, instant on *clock*, bearer token and params.
    """

    def __init__(
        self,
        clock: Callable[[], float],
        *,
        token_endpoint: ScriptedTokenEndpoint | None = None,
        broadcasts: Mapping[str, str] | None = None,
        polling_interval_ms: int = 5000,
        pages: Sequence[Any] = (),
        lookups: Sequence[Any] = (),
        inserts: Sequence[Any] = (),
        deletes: Sequence[Any] = (),
        bans: Sequence[Any] = (),
    ) -> None:
        self.clock = clock
        self.token_endpoint = token_endpoint
        self.broadcasts = dict(broadcasts or {})
        self.polling_interval_ms = polling_interval_ms
        self.pages = list(pages)
        self.lookups = list(lookups)
        self.answers: dict[str, list[Any]] = {
            "insert": list(inserts),
            "delete": list(deletes),
            "ban": list(bans),
        }
        self.counts: dict[str, int] = {name: 0 for name in LIVE_CHAT_ENDPOINTS}
        self.requests: list[dict[str, Any]] = []
        self.close_calls = 0
        self._page_sequence = 0

    def times(self, endpoint: str) -> list[float]:
        return [request["at"] for request in self.requests if request["endpoint"] == endpoint]

    async def get(self, url: str, **kwargs: Any) -> Any:
        if url.endswith("/liveBroadcasts"):
            return _raise_or_return(self._lookup(kwargs))
        if url.endswith("/liveChat/messages"):
            return _raise_or_return(self._list(kwargs))
        raise AssertionError(f"unexpected GET {url}")

    async def post(self, url: str, **kwargs: Any) -> Any:
        if url.endswith("/token"):
            assert self.token_endpoint is not None, "no token endpoint scripted"
            return _raise_or_return(self.token_endpoint.answer(kwargs.get("data")))
        if url.endswith("/liveChat/messages"):
            return _raise_or_return(self._write("insert", kwargs, 200))
        if url.endswith("/liveChat/bans"):
            return _raise_or_return(self._write("ban", kwargs, 200))
        raise AssertionError(f"unexpected POST {url}")

    async def delete(self, url: str, **kwargs: Any) -> Any:
        if url.endswith("/liveChat/messages"):
            return _raise_or_return(self._write("delete", kwargs, 204))
        raise AssertionError(f"unexpected DELETE {url}")

    async def close(self) -> None:
        self.close_calls += 1

    def _record(self, endpoint: str, kwargs: Mapping[str, Any]) -> None:
        self.counts[endpoint] += 1
        headers = kwargs.get("headers") or {}
        authorization = headers.get("Authorization", "")
        self.requests.append(
            {
                "endpoint": endpoint,
                "at": self.clock(),
                "token": authorization.removeprefix("Bearer "),
                "params": dict(kwargs.get("params") or {}),
                "json": kwargs.get("json"),
            }
        )

    def _lookup(self, kwargs: Mapping[str, Any]) -> Any:
        self._record("broadcast_lookup", kwargs)
        if self.lookups:
            return _as_answer(self.lookups.pop(0))
        items = [
            {
                "id": f"broadcast-{channel_id}",
                "snippet": {"channelId": channel_id, "liveChatId": live_chat_id},
            }
            for channel_id, live_chat_id in self.broadcasts.items()
        ]
        return FakeResponse(200, {"kind": "youtube#liveBroadcastListResponse", "items": items})

    def _list(self, kwargs: Mapping[str, Any]) -> Any:
        self._record("list", kwargs)
        if self.pages:
            return _as_answer(self.pages.pop(0))
        self._page_sequence += 1
        return FakeResponse(
            200,
            {
                "kind": "youtube#liveChatMessageListResponse",
                "items": [],
                "nextPageToken": f"page-{self._page_sequence}",
                "pollingIntervalMillis": self.polling_interval_ms,
            },
        )

    def _write(self, endpoint: str, kwargs: Mapping[str, Any], status: int) -> Any:
        self._record(endpoint, kwargs)
        scripted = self.answers[endpoint]
        if scripted:
            return _as_answer(scripted.pop(0))
        return FakeResponse(status, {"id": f"{endpoint}-{self.counts[endpoint]}"} if status == 200 else None)


def _as_answer(entry: Any) -> Any:
    """A scripted entry as an answer: a response or exception passes through,
    a body becomes a 200 :class:`FakeResponse`."""

    if isinstance(entry, (FakeResponse, BaseException)):
        return entry
    return FakeResponse(200, entry)


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

    # The test-only Kick key pair is a pair: d inverts e modulo n.
    key = KICK_TEST_KEY
    assert key.modulus.bit_length() == 2048 and key.size_bytes == 256
    assert pow(pow(0x1234567, key.private_exponent, key.modulus), key.public_exponent, key.modulus) == 0x1234567
    sender = SignedWebhookSender(key, ManualClock(1_700_000_000.0))
    assert sender.timestamp() == "2023-11-14T22:13:20Z"
    assert base64.b64decode(sender.sign("id", "t", b"{}")).__len__() == 256

    # The YouTube edge answers by URL and counts per endpoint.
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_self_check_youtube_edge())
    finally:
        loop.close()


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


async def _self_check_youtube_edge() -> None:
    clock = ManualClock(50.0)
    tokens = ScriptedTokenEndpoint(clock, 3600, TOKEN_REFUSED)
    api = ScriptedLiveChatAPI(
        clock, token_endpoint=tokens, broadcasts={"UC1": "chat-1"}, polling_interval_ms=2000
    )
    issued = await api.post("https://token.example/token", data={"grant_type": "refresh_token"})
    assert issued.status == 200 and issued.body["access_token"] == "access-1"
    assert tokens.valid_at("access-1", 3649.0) and not tokens.valid_at("access-1", 3650.0)
    refused = await api.post("https://token.example/token", data={})
    assert refused.status == 400 and tokens.refreshes == 2
    lookup = await api.get("https://api.example/liveBroadcasts", headers={"Authorization": "Bearer t"})
    assert lookup.body["items"][0]["snippet"]["liveChatId"] == "chat-1"
    page = await api.get("https://api.example/liveChat/messages", params={"liveChatId": "chat-1"})
    assert page.body["pollingIntervalMillis"] == 2000 and page.body["nextPageToken"] == "page-1"
    assert (await api.post("https://api.example/liveChat/messages", json={})).status == 200
    assert (await api.delete("https://api.example/liveChat/messages", params={"id": "m"})).status == 204
    assert (await api.post("https://api.example/liveChat/bans", json={})).status == 200
    assert api.counts == {"broadcast_lookup": 1, "list": 1, "insert": 1, "delete": 1, "ban": 1}
    assert api.requests[0]["token"] == "t" and api.times("list") == [50.0]


_self_check()


# --------------------------------------------------------------------------- #
# A recorded random source (phase 8, P1)
# --------------------------------------------------------------------------- #


class RecordedRandom:
    """A random source that serves a recorded list of values, in order.

    ``ordinal`` is the index of the next value served, so after *n* draws it
    is *n*; :meth:`seed` starts the same (or a new) list over, which is how a
    replay is made reproducible by construction. Running past the end of the
    list is a test error, never a silent wrap.
    """

    def __init__(self, values: Sequence[float]) -> None:
        self._values: tuple[float, ...] = ()
        self.ordinal = 0
        self.seed(values)

    def seed(self, values: Sequence[float] | None = None) -> None:
        if values is not None:
            recorded = tuple(float(value) for value in values)
            if not recorded or not all(0.0 <= value < 1.0 for value in recorded):
                raise ValueError("RecordedRandom needs at least one value in [0, 1)")
            self._values = recorded
        self.ordinal = 0

    @property
    def values(self) -> tuple[float, ...]:
        return self._values

    def random(self) -> float:
        if self.ordinal >= len(self._values):
            raise AssertionError(
                f"RecordedRandom exhausted after {len(self._values)} values"
            )
        value = self._values[self.ordinal]
        self.ordinal += 1
        return value


# --------------------------------------------------------------------------- #
# AC33: no test opens a socket or starts a process (phase 8, P1, D8)
# --------------------------------------------------------------------------- #

#: The one socket pair a test may create: the event loop's own wakeup pair,
#: told by its caller's module and qualified name, never by a line number.
_SELF_PIPE_MODULE = "asyncio.selector_events"
_SELF_PIPE_QUALNAME = "BaseSelectorEventLoop._make_self_pipe"
_SELF_PIPE_NAME = "_make_self_pipe"

#: The ``socket`` module functions that create (or resolve for) a socket.
_SOCKET_FUNCTIONS = ("create_connection", "create_server", "fromfd", "getaddrinfo")
#: The ``os`` functions that start or replace a process.
_PROCESS_FUNCTIONS = (
    "system",
    "popen",
    "posix_spawn",
    "posix_spawnp",
    "fork",
    "forkpty",
    "execl",
    "execle",
    "execlp",
    "execlpe",
    "execv",
    "execve",
    "execvp",
    "execvpe",
)

#: The opt-in trial gates: per file, the gate function the file skips with
#: and, per trial node, the module constant that gate is called with. The
#: phase-5 file's gate is ``opted_in(question)`` over its own ``NODES``.
_TRIAL_GATES: dict[str, tuple[str, dict[str, str]]] = {
    "test_phase2_trials.py": (
        "required",
        {
            "test_trial_speech_synthesis": "SPEECH_VARIABLE",
            "test_trial_transcription": "TRANSCRIPTION_VARIABLE",
            "test_trial_playback_command": "PLAYER_VARIABLE",
            "test_trial_capture_command": "RECORDER_VARIABLE",
            "test_trial_scene_provider": "SCENE_VARIABLE",
            "test_trial_platform_polls": "POLL_VARIABLE",
        },
    ),
    "test_phase3_trials.py": (
        "gate",
        {
            "test_trial_platform_clips": "CLIPS_TRIAL",
            "test_trial_platform_moderation": "MODERATION_TRIAL",
            "test_trial_community_notices": "NOTICES_TRIAL",
            "test_trial_kick": "KICK_TRIAL",
            "test_trial_youtube": "YOUTUBE_TRIAL",
            "test_trial_screen_watch": "WATCH_TRIAL",
        },
    ),
}
_PHASE5_TRIALS = "test_phase5_trials.py"

#: The transitional ledger (D8): the pre-existing nodes the probe at
#: ``7782e5f`` found opening a socket or starting a process, by node id with
#: the parameter suffix stripped. It may only shrink: P15a–P15e remove the
#: nodes they convert and P16 asserts it is empty. Nothing is ever added.
AC33_PENDING: frozenset[str] = frozenset(
    {
        "tests/test_agentic_loop.py::test_ac31_the_core_and_the_neutral_modules_name_no_platform",
        "tests/test_audio_input.py::test_the_real_transport_against_a_closed_port_degrades_the_transcription",
        "tests/test_audio_input.py::test_the_shipped_runner_is_not_held_by_a_fast_recorder_after_its_segment",
        "tests/test_audio_input.py::test_the_shipped_runner_kills_a_child_left_by_an_exited_wrapper",
        "tests/test_audio_input.py::test_the_shipped_runner_kills_a_silent_real_recorder_at_the_deadline",
        "tests/test_audio_input.py::test_the_shipped_runner_records_a_real_process",
        "tests/test_audio_output.py::test_the_subprocess_runner_feeds_a_real_player_its_stdin",
        "tests/test_automation_bridge.py::test_ac15_phase6_a_permanently_raising_trace_keeps_the_backoff",
        "tests/test_automation_bridge.py::test_ac15_phase6_a_raising_reconcile_trace_leaves_the_owner_alive",
        "tests/test_automation_bridge.py::test_ac16_no_run_observation_is_a_success",
        "tests/test_automation_bridge.py::test_ac16_row_acknowledged_is_execution_unconfirmed",
        "tests/test_automation_bridge.py::test_ac16_row_bridge_not_ready",
        "tests/test_automation_bridge.py::test_ac16_row_cancelled_before_the_frame_left",
        "tests/test_automation_bridge.py::test_ac16_row_instance_action_ambiguous",
        "tests/test_automation_bridge.py::test_ac16_row_instance_action_disabled",
        "tests/test_automation_bridge.py::test_ac16_row_instance_action_missing",
        "tests/test_automation_bridge.py::test_ac16_row_instance_error_answer",
        "tests/test_automation_bridge.py::test_ac16_row_invalid_arguments",
        "tests/test_automation_bridge.py::test_ac16_row_no_acknowledgement_before_the_deadline",
        "tests/test_automation_bridge.py::test_ac16_row_not_allowlisted",
        "tests/test_automation_bridge.py::test_ac17_a_late_acknowledgement_is_lost_and_never_re_invoked",
        "tests/test_automation_bridge.py::test_ac17_an_answer_landing_while_the_timer_is_cleaned_up_is_lost",
        "tests/test_automation_bridge.py::test_ac18_a_group_entry_reaches_its_actions_only",
        "tests/test_automation_bridge.py::test_ac18_an_empty_or_absent_allowlist_refuses_everything",
        "tests/test_automation_bridge.py::test_ac18_only_allowlisted_names_are_reachable",
        "tests/test_automation_bridge.py::test_ac19_the_entry_schema_decides_the_arguments",
        "tests/test_automation_bridge.py::test_ac20_reconciliation_names_the_unmatched_entries_once",
        "tests/test_automation_bridge.py::test_ac21_a_principal_without_a_rule_is_refused_before_the_provider",
        "tests/test_automation_bridge.py::test_ac24_the_challenge_answer_and_readiness_only_after_the_inventory",
        "tests/test_automation_bridge.py::test_ac25_a_failed_start_retries_and_a_success_reports_ready",
        "tests/test_automation_bridge.py::test_ac25_a_lost_connection_degrades_at_once_and_reconnects_after_the_minimum",
        "tests/test_automation_bridge.py::test_ac25_the_backoff_doubles_from_the_minimum_and_is_capped_at_the_maximum",
        "tests/test_automation_bridge.py::test_ac26_a_full_queue_drops_and_counts_and_never_stalls_the_connection",
        "tests/test_automation_bridge.py::test_ac27_owning_only_cues_subscribes_to_the_custom_category_only",
        "tests/test_automation_bridge.py::test_ac27_owning_twitch_chat_adds_only_twitch_chat_and_notices",
        "tests/test_automation_bridge.py::test_ac28_a_run_in_flight_at_shutdown_is_acknowledgement_lost",
        "tests/test_automation_bridge.py::test_ac28_cancelling_the_owner_task_propagates_and_releases_the_connection",
        "tests/test_automation_bridge.py::test_ac28_shutdown_closes_within_budget_and_fails_a_pending_request",
        "tests/test_automation_bridge.py::test_ac29_a_configured_policy_under_the_bridge_decides_its_cues",
        "tests/test_automation_bridge.py::test_ac29_a_valid_cue_is_one_normalised_event_the_default_policy_accepts",
        "tests/test_automation_bridge.py::test_ac30_a_cue_without_user_has_one_stable_author_unlike_the_companion",
        "tests/test_automation_bridge.py::test_ac30_a_malformed_or_oversized_envelope_is_dropped_and_counted",
        "tests/test_automation_bridge.py::test_ac32_instance_roles_satisfy_an_audience_only_when_trusted",
        "tests/test_automation_bridge.py::test_ac33_a_mapped_chat_event_authored_by_the_companion_is_refused",
        "tests/test_automation_bridge.py::test_ac33_engine_half_an_event_authored_by_the_companion_is_refused",
        "tests/test_automation_bridge.py::test_ac33_the_refusal_comes_from_the_identities_the_bridge_hands_over",
        "tests/test_automation_bridge.py::test_ac34_another_channel_is_refused_unsupported_destination",
        "tests/test_automation_bridge.py::test_ac34_a_page_holds_at_most_limit_and_the_cursor_pages_the_same_list",
        "tests/test_automation_bridge.py::test_ac34_the_owned_channel_reads_one_page_of_active_viewers",
        "tests/test_automation_bridge.py::test_ac37_the_status_guard_runs_unmarked_and_the_trial_file_keeps_eight_nodes",
        "tests/test_automation_bridge.py::test_ac3_an_unreachable_instance_leaves_a_ready_companion_and_a_degraded_bridge",
        "tests/test_automation_bridge.py::test_ac43_owning_chat_each_fixture_gives_one_event_and_unmapped_is_dropped",
        "tests/test_automation_bridge.py::test_ac43_owning_only_twitch_the_kick_and_youtube_fixtures_give_nothing",
        "tests/test_automation_bridge.py::test_ac45_every_matching_group_entry_must_accept",
        "tests/test_automation_bridge.py::test_ac45_the_action_entry_beats_the_group_entry",
        "tests/test_automation_bridge.py::test_ac4_each_failure_mode_degrades_with_its_reason_and_leaks_nothing",
        "tests/test_automation_bridge.py::test_a_connection_loss_fails_every_pending_request",
        "tests/test_automation_bridge.py::test_a_cursor_from_another_destination_or_not_minted_here_is_refused",
        "tests/test_automation_bridge.py::test_a_malformed_viewer_answer_is_a_provider_failure",
        "tests/test_automation_bridge.py::test_an_instance_without_a_challenge_is_not_sent_an_authentication",
        "tests/test_automation_bridge.py::test_a_platform_the_bridge_does_not_own_is_never_bound_to_it",
        "tests/test_automation_bridge.py::test_a_withheld_viewer_list_ends_timeout_never_external_unknown",
        "tests/test_automation_bridge.py::test_d8_a_connection_closed_before_the_segment_is_provider_not_ready",
        "tests/test_automation_bridge.py::test_d8_an_interruption_in_the_drain_after_the_write_is_acknowledgement_lost",
        "tests/test_automation_bridge.py::test_d8_a_pre_write_failure_sends_nothing_and_withdraws_readiness",
        "tests/test_automation_bridge.py::test_d8_behaviour_the_frame_reaches_the_transport_in_the_marking_segment",
        "tests/test_automation_bridge.py::test_d8_cancelling_after_the_segment_is_acknowledgement_lost",
        "tests/test_automation_bridge.py::test_d8_negative_control_the_behavioural_check_catches_f2s_mutation",
        "tests/test_automation_bridge.py::test_d8_the_executor_deadline_before_the_frame_left_is_a_timeout",
        "tests/test_automation_bridge.py::test_fake_instance_answers_one_hello_and_records_one_frame",
        "tests/test_automation_bridge.py::test_fake_instance_authentication_switches",
        "tests/test_automation_bridge.py::test_fake_instance_holds_withholds_and_rejects_acknowledgements",
        "tests/test_automation_bridge.py::test_fake_instance_malformed_hello_and_failed_inventory",
        "tests/test_automation_bridge.py::test_fake_instance_pushes_events_echoes_subscriptions_and_closes",
        "tests/test_automation_bridge.py::test_fake_instance_stop_ends_the_connection",
        "tests/test_automation_bridge.py::test_r1_a_withheld_inventory_degrades_within_the_startup_bound",
        "tests/test_automation_bridge.py::test_r5_reconciliation_runs_again_at_every_connection",
        "tests/test_automation_bridge.py::test_r6_a_refused_subscription_leaves_the_bridge_not_ready",
        "tests/test_automation_bridge.py::test_r7_a_cue_for_a_platform_not_owned_is_dropped_by_the_bridge",
        "tests/test_automation_bridge.py::test_r7_an_accepted_cue_is_admitted_to_the_shared_scheduler",
        "tests/test_automation_bridge.py::test_r7_a_replayed_cue_id_is_neither_republished_nor_redecided",
        "tests/test_automation_bridge.py::test_r7b_a_replayed_event_without_an_id_is_not_published_twice",
        "tests/test_automation_bridge.py::test_r7b_mapped_chat_feeds_the_shared_transcript_once",
        "tests/test_automation_bridge.py::test_the_loader_activates_the_bridge_and_a_run_is_refused_not_ready",
        "tests/test_automation_bridge.py::test_without_owning_viewers_the_read_is_declared_and_never_bound",
        "tests/test_capture.py::test_command_exiting_non_zero_is_capture_failed_and_oversize_output_is_refused",
        "tests/test_capture.py::test_command_source_runs_a_python_one_liner_writing_a_png_to_stdout",
        "tests/test_capture.py::test_module_names_no_platform",
        "tests/test_capture.py::test_run_command_reads_bounded_stdout_and_kills_a_flooding_child",
        "tests/test_chat_context_module.py::test_module_names_no_platform",
        "tests/test_clips.py::test_ac12_with_only_kick_required_unset_starts_with_the_action_unbound",
        "tests/test_config_ui.py::test_ac30_a_child_exiting_2_is_refused_with_its_status_and_diagnostic",
        "tests/test_config_ui.py::test_ac30_a_child_writing_nothing_within_the_window_is_unknown",
        "tests/test_config_ui.py::test_ac30_a_ready_record_of_the_child_with_the_on_disk_digest_is_accepted",
        "tests/test_config_ui.py::test_ac30_the_launch_argv_runs_without_a_shell",
        "tests/test_config_ui.py::test_ac31_a_child_ignoring_terminate_is_killed_after_the_bounded_wait",
        "tests/test_config_ui.py::test_ac31_apply_never_signals_a_foreign_record_pid",
        "tests/test_config_ui.py::test_ac32_after_a_successful_apply_every_page_shows_in_sync",
        "tests/test_config_ui.py::test_ac35_no_secret_value_reaches_any_response_log_report_or_record",
        "tests/test_config_ui.py::test_ac39_ac40_a_child_writing_only_an_unaccepted_record_is_unknown",
        "tests/test_config_ui.py::test_d9_a_chatty_child_is_accepted_within_the_window",
        "tests/test_config_ui.py::test_d9_a_refused_report_carries_the_last_line_within_2_kib",
        "tests/test_config_ui.py::test_d9_draining_continues_after_acceptance",
        "tests/test_config_ui.py::test_d9_the_refused_tail_is_redacted_and_value_free",
        "tests/test_config_ui.py::test_the_child_inherits_the_environment_plus_the_status_variable",
        "tests/test_hygiene.py::test_n8_the_generated_overlay_and_status_files_are_ignored",
        "tests/test_input_ownership.py::test_ac14_the_real_users_and_bridge_split_the_viewer_read",
        "tests/test_input_ownership.py::test_ac2_the_shipped_profiles_have_zero_changed_lines_since_the_phase_base",
        "tests/test_input_ownership.py::test_ac44_the_integration_scenarios_count_zero_ownership_rejections",
        "tests/test_input_ownership.py::test_ac5_a_twitch_event_is_dropped_while_the_owning_bridge_is_down",
        "tests/test_installation_doc.py::test_the_guides_profile_is_accepted_by_the_real_entry_point",
        "tests/test_installation_doc.py::test_the_readme_config_ui_command_runs",
        "tests/test_integration.py::test_ac27_example_config_drives_one_correlated_run_and_clean_shutdown",
        "tests/test_integration.py::test_r8_refused_send_is_sanitized_leaves_no_send_fact_and_next_send_succeeds",
        "tests/test_kick.py::test_ac24_a_replay_across_the_whole_tolerance_span_is_deduplicated",
        "tests/test_kick.py::test_ac32_an_author_id_with_a_colon_is_refused_and_counted",
        "tests/test_kick.py::test_ac34_a_65536_byte_body_is_still_accepted",
        "tests/test_kick.py::test_ac34_a_valid_delivery_is_published_once_and_admitted_per_policy",
        "tests/test_kick.py::test_ac34_each_refusal_is_answered_4xx_counted_and_publishes_nothing",
        "tests/test_kick.py::test_ac34_the_listener_runs_on_the_configured_host_and_port",
        "tests/test_kick.py::test_ac34_the_timestamp_bound_is_inclusive_at_300_seconds_either_way",
        "tests/test_kick.py::test_ac35_500_characters_send_one_request_and_501_send_none",
        "tests/test_kick.py::test_ac35_a_429_blocks_sends_until_the_announced_retry_instant",
        "tests/test_kick.py::test_ac35_a_90_second_timeout_is_sent_as_2_minutes",
        "tests/test_kick.py::test_ac35_each_badge_satisfies_its_audience_rule",
        "tests/test_kick.py::test_ac35_each_notice_type_maps_to_its_kind_and_is_admitted_by_kind",
        "tests/test_kick.py::test_ac35_the_channel_account_is_the_broadcaster_by_identity",
        "tests/test_kick.py::test_ac35_the_service_refuses_locally_with_no_request",
        "tests/test_kick.py::test_a_channel_outside_the_configured_ones_is_refused_with_no_request",
        "tests/test_kick.py::test_a_delivery_before_start_inputs_is_not_accepted",
        "tests/test_kick.py::test_a_delivery_for_an_unconfigured_channel_is_ignored_and_counted",
        "tests/test_kick.py::test_a_lost_or_5xx_send_is_external_unknown_and_never_retried",
        "tests/test_kick.py::test_an_anonymous_gift_is_published_but_never_admitted",
        "tests/test_kick.py::test_an_unlisted_notice_and_an_unmapped_type_are_ignored_and_counted",
        "tests/test_kick.py::test_an_unreadable_429_blocks_for_the_bounded_default_and_is_traced",
        "tests/test_kick.py::test_chat_owned_elsewhere_publishes_no_delivery_and_keeps_chat_write",
        "tests/test_kick.py::test_chat_write_is_bound_on_kick_chat_and_ready_after_prepare",
        "tests/test_kick.py::test_each_ban_answer_is_classified_after_one_request",
        "tests/test_kick.py::test_each_other_answer_is_classified_after_one_request",
        "tests/test_kick.py::test_owning_by_default_a_valid_delivery_is_still_published",
        "tests/test_kick.py::test_the_companion_own_messages_are_dropped_before_anything",
        "tests/test_kick.py::test_the_dedup_window_is_bounded_by_its_settings",
        "tests/test_kick.py::test_the_largest_timeout_is_10080_minutes_and_is_sent",
        "tests/test_kick.py::test_the_moderation_service_is_timeout_only_and_no_clip_or_poll_is_published",
        "tests/test_kick.py::test_the_public_key_is_fetched_once_at_prepare_when_not_configured",
        "tests/test_main.py::test_the_accepted_limits_bound_the_real_scheduler_memory_and_audit_queue",
        "tests/test_phase5_docs_status.py::test_ac36_the_socket_count_sees_a_local_and_a_network_socket",
        "tests/test_phase5_docs_status.py::test_ac36_the_trial_file_skips_its_eight_nodes_opening_no_socket",
        "tests/test_phase5_docs_status.py::test_ac37_no_document_calls_the_bridge_delivered",
        "tests/test_phase5_docs_status.py::test_ac37_the_exclusion_covers_only_the_campaign_papers",
        "tests/test_profiles.py::test_ac31_the_full_pc_profile_degrades_three_modules_and_still_chats",
        "tests/test_profiles.py::test_ac43_with_an_unreachable_speech_endpoint_audio_speak_is_named_not_ready_too",
        "tests/test_profiles.py::test_installed_distribution_discovers_the_shipped_manifests_and_answers_help",
        "tests/test_proxy_process.py::test_ac39_two_process_topology_over_loopback",
        "tests/test_proxy.py::test_ac31_the_proxy_and_the_agent_link_name_no_platform",
        "tests/test_proxy.py::test_the_real_listener_cuts_an_oversized_binary_frame_before_the_module_sees_it",
        "tests/test_shutdown.py::test_cli_deadline_covers_cancellation_resistant_task",
        "tests/test_shutdown.py::test_cli_process_exits_with_synchronous_audit_writer",
        "tests/test_stream_control.py::test_the_default_factory_frames_the_protocol_over_a_real_loopback_socket",
        "tests/test_users.py::test_module_names_no_platform",
        "tests/test_viewer_memory.py::test_ac19_cli_the_module_entry_runs_without_a_network_client",
        "tests/test_web.py::test_ac24_the_shipped_profiles_have_zero_changed_lines_since_the_phase_base",
        "tests/test_web.py::test_the_socket_guard_refuses_and_records_every_non_loopback_surface",
        "tests/test_youtube.py::test_ac39_three_platforms_on_one_runtime",
    }
)


def ac33_node(nodeid: str) -> str:
    """*nodeid* without its parameter suffix: the ledger's key."""

    return nodeid.split("[", 1)[0]


def trial_opted_in(file_name: str, module: Any, test_name: str) -> bool:
    """Whether an operator opted the trial node in, by the file's own gate."""

    if file_name == _PHASE5_TRIALS:
        question = module.NODES.get(test_name)
        return question is not None and bool(module.opted_in(question))
    gate_name, constants = _TRIAL_GATES.get(file_name, ("", {}))
    constant = constants.get(test_name)
    if constant is None:
        return False
    try:
        getattr(module, gate_name)(getattr(module, constant))
    except pytest.skip.Exception:
        return False
    return True


def _is_self_pipe(frame: Any) -> bool:
    code = frame.f_code
    if frame.f_globals.get("__name__") != _SELF_PIPE_MODULE:
        return False
    qualname = getattr(code, "co_qualname", None)
    if qualname is None:  # pragma: no cover - 3.10 has no co_qualname
        return code.co_name == _SELF_PIPE_NAME
    return qualname == _SELF_PIPE_QUALNAME


#: The real entry points, captured at import, before any test patched one.
_REAL_ENTRY_POINTS: dict[tuple[Any, str], Any] = {
    (socket.socket, "__init__"): socket.socket.__init__,
    (socket, "socketpair"): socket.socketpair,
    **{(socket, name): getattr(socket, name) for name in _SOCKET_FUNCTIONS},
    (subprocess.Popen, "__init__"): subprocess.Popen.__init__,
    **{(os, name): getattr(os, name) for name in _PROCESS_FUNCTIONS if hasattr(os, name)},
}


@pytest.fixture(autouse=True)
def _ac33_isolation(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> Any:
    """AC33 (R9, D8): creating a socket or starting a process raises.

    Every test runs under it except a ledger node (until its conversion)
    and a real-instance trial an operator opted in. ``monkeypatch`` undoes
    every patch after the test. An entry point an enclosing module-wide
    fixture already replaced with its own refusal (``tests/test_config_ui.py``
    asserts its patch is the one installed) is left to that fixture.
    """

    nodeid = request.node.nodeid
    file_name = Path(str(request.node.path)).name
    if ac33_node(nodeid) in AC33_PENDING or trial_opted_in(
        file_name, request.module, getattr(request.node, "originalname", request.node.name)
    ):
        yield
        return

    def install(owner: Any, name: str, replacement: Any) -> None:
        if getattr(owner, name) is _REAL_ENTRY_POINTS[(owner, name)]:
            monkeypatch.setattr(owner, name, replacement)

    def no_socket(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError(f"AC33: {nodeid} created a socket")

    def no_process(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError(f"AC33: {nodeid} started a process")

    real_init = _REAL_ENTRY_POINTS[(socket.socket, "__init__")]
    real_socketpair = _REAL_ENTRY_POINTS[(socket, "socketpair")]

    def guarded_init(
        self: socket.socket, family: Any = -1, type: Any = -1, proto: Any = -1, fileno: Any = None
    ) -> None:
        if fileno is None:
            no_socket()
        real_init(self, family, type, proto, fileno)

    def guarded_socketpair(*args: Any, **kwargs: Any) -> Any:
        if not _is_self_pipe(sys._getframe(1)):
            no_socket()
        return real_socketpair(*args, **kwargs)

    install(socket.socket, "__init__", guarded_init)
    install(socket, "socketpair", guarded_socketpair)
    for name in _SOCKET_FUNCTIONS:
        install(socket, name, no_socket)
    install(subprocess.Popen, "__init__", no_process)
    for name in _PROCESS_FUNCTIONS:
        if hasattr(os, name):
            install(os, name, no_process)
    yield
