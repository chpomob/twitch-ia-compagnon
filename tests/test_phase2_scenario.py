"""The phase 2 end-to-end scenario (R1, R3, R4, R5, R10; AC35).

One viewer message → the scripted model proposes ``audio.capture {seconds:
3}`` → the observation carries the ``audio_ref`` and the transcription the
scripted speech-to-text transport answered → the model answers with a final
text → the configured delivery list ``[chat.write, audio.speak]`` writes the
answer to the chat and speaks it. The scenario runs on the shipped ``twitch``
module (scripted ``_session_factory``) and on the ``fakeplatform`` fixture,
each twice:

- *local* — ``audio_input`` and ``audio_output`` on the brain's own runtime
  context, next to the platform's ``chat.write`` (the three local providers);
- *proxy* — ``audio_input`` and ``audio_output`` behind ``agent_link`` on an
  agent runtime context, paired to the shipped ``proxy`` on the brain's
  context over ``MemoryWebSocketPair``; both contexts share one
  :class:`ManualClock`, so the agent store and the brain store age together.

Every module is loaded by the real :class:`~core.loader.ModuleLoader` and
driven by the real :class:`~core.lifecycle.PhaseCoordinator`; the brain is
the shipped one, handed nothing but its ``delivery`` group and its required
``capabilities`` — it names no audio module, no transport and no platform.

The shutdown cases start the coordinator's shutdown while the capture
records, while the answer plays and while a scene change (the ``websocket``
scene provider against the in-process peer of P15) waits for its
confirmation: each ends within the global shutdown deadline on the injected
clock, the modules' ``drain()`` hooks stopping their devices so each record
comes back through the provider path (decision 1).

Nothing sleeps: time moves only on the clock, and every wait is a bounded
number of loop turns.
"""

from __future__ import annotations

import ast
import asyncio
import copy
import io
import json
import re
import sys
import tokenize
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from core.actions import AuthorizationPolicy, AuthorizationRule
from core.attachments import AttachmentStore
from core.context import ChatContext
from core.contracts import TRACE_BRAIN_RUN_COMPLETED, WILDCARD, Destination
from core.lifecycle import PhaseCoordinator
from core.loader import ModuleLoader
from core.runtime import RuntimeContext
from core.triggers import TriggerRegistry
from conftest import (
    FakeAudioSource,
    FakePlayer,
    ManualClock,
    MemoryWebSocketPair,
    RecordingPlayerRunner,
    ScriptedModel,
    ScriptedSpeechTransport,
    ScriptedTranscriptionTransport,
    WSMsgType,
    carries_audio_part,
    events_of,
    final,
    runtime_context as build_context,
    settle,
    tool_call,
    wait_until,
    wav_bytes,
)
from modules.brain import MODULE_NAME as BRAIN, PRINCIPAL
from modules.stream_control import (
    CAUSE_CONFIRMATION_LOST,
    REQUEST_SET_SCENE,
)
from test_brain import VALID_SETTINGS, copy_settings
from test_integration import (
    FakeTwitchSession,
    FakeWebSocket,
    notification,
    sent_response,
    welcome,
)
from test_proxy import FakeServer
from test_stream_control import ScenePeer


ROOT = Path(__file__).parents[1]
SHIPPED_MODULES = ROOT / "modules"
FIXTURE_MODULES = ROOT / "tests" / "fixtures" / "modules"
BRAIN_SOURCE = SHIPPED_MODULES / "brain" / "__init__.py"

# The action names live here and in the manifests — never in the brain.
CHAT_WRITE = "chat.write"
AUDIO_CAPTURE = "audio.capture"
AUDIO_SPEAK = "audio.speak"
SCENE_SET = "stream.scene.set"

AUDIO_INPUT = "audio_input"
AUDIO_OUTPUT = "audio_output"
STREAM_CONTROL = "stream_control"
PROXY = "proxy"
AGENT_LINK = "agent_link"
TWITCH = "twitch"
FAKEPLATFORM = "fakeplatform"

#: The permission each action's manifest requires.
PERMISSIONS = {
    CHAT_WRITE: "chat.write",
    AUDIO_CAPTURE: "audio.capture",
    AUDIO_SPEAK: "audio.speak",
    SCENE_SET: "stream.scene",
}

COMPANION = "companion"
VIEWER = "viewer-7"
FAKE_CHANNEL = "chan-a"
BROADCASTER = "broadcaster-42"
BOT_USER = "bot-24"

ANSWER = "I heard you say hello, welcome to the stream!"
TRANSCRIPT = "hello companion, can you hear me"
TALKING = "Talking"

RECORDER_ARGV = [sys.executable, "recorder-under-test", "--wav"]
PLAYER_ARGV = [sys.executable, "player-under-test", "-"]
STT_ENDPOINT = "http://stt.invalid:8443/v1/audio/transcriptions"
STT_MODEL = "stt-model-under-test"
SPEECH_ENDPOINT = "http://speech.invalid/v1/audio/speech"
SPEECH_MODEL = "speech-model-under-test"
SCENE_URL = "ws://127.0.0.1:4455"
SCENE_PASSWORD = "scene-password-3f81c2"

#: The recorder hands out 5 s; ``seconds: 3`` stores the canonical 96 044 bytes.
SOURCE_SEGMENT = wav_bytes(5.0)
SEGMENT_BYTES = 96_044
SPEECH_BODY = wav_bytes(1.5)
#: 10 s of speech: 320 000 data bytes at 32 000 B/s.
LONG_SPEECH = wav_bytes(10.0)

PROXY_TOKEN = "pairing-secret-token"
AGENT_ID = "agent-pc-1"
BRAIN_URL = "ws://127.0.0.1:8765/agent"
STORE_LIMITS: dict[str, Any] = {
    "max_object_bytes": 1_048_576,
    "max_objects": 8,
    "max_total_bytes": 4_194_304,
    "max_bytes_per_run": 2_097_152,
    "ttl_seconds": 300.0,
}

#: The global shutdown deadline and the per-hook bound of the shutdown cases.
SHUTDOWN_DEADLINE = 20.0
HOOK_TIMEOUT = 2.0

SPEAK_DELIVERY = {
    "mode": "fixed",
    "actions": [
        {"action": CHAT_WRITE, "text_argument": "text"},
        {"action": AUDIO_SPEAK, "text_argument": "text"},
    ],
}
SCENE_DELIVERY = {
    "mode": "fixed",
    "actions": [
        {"action": CHAT_WRITE, "text_argument": "text"},
        {"action": SCENE_SET, "text_argument": "none", "arguments": {"scene": TALKING}},
    ],
}

SCENARIO = (tool_call(AUDIO_CAPTURE, {"seconds": 3}), final(ANSWER))


def grant(action: str) -> AuthorizationRule:
    """One explicit grant of *action* to the brain's principal (default-deny)."""

    return AuthorizationRule(
        rule_id=f"brain-{action}",
        action_name=action,
        principals=(PRINCIPAL,),
        granted_permissions=(PERMISSIONS[action],),
    )


def agent_grant(action: str) -> AuthorizationRule:
    """The agent profile's rule for *action*: scope ``audio``, principal ``brain``."""

    return AuthorizationRule(
        rule_id=f"agent-{action}",
        action_name=action,
        destination=Destination(WILDCARD, WILDCARD, "audio"),
        principals=(PRINCIPAL,),
        granted_permissions=(PERMISSIONS[action],),
    )


# --------------------------------------------------------------------------- #
# The seams of the two audio modules
# --------------------------------------------------------------------------- #


@dataclass
class AudioSeams:
    """The injected edges of ``audio_input`` and ``audio_output``."""

    clock: ManualClock
    source: FakeAudioSource
    stt: ScriptedTranscriptionTransport
    speech: ScriptedSpeechTransport
    runner: RecordingPlayerRunner

    @classmethod
    def build(
        cls,
        clock: ManualClock,
        *,
        source: FakeAudioSource | None = None,
        speech: bytes = SPEECH_BODY,
        players: tuple[FakePlayer, ...] = (),
    ) -> "AudioSeams":
        return cls(
            clock=clock,
            source=source if source is not None else FakeAudioSource(SOURCE_SEGMENT),
            # The first answer is the prepare-time probe's.
            stt=ScriptedTranscriptionTransport({"text": ""}, {"text": TRANSCRIPT}),
            speech=ScriptedSpeechTransport(speech),
            runner=RecordingPlayerRunner(*players, clock=clock),
        )

    def settings(self) -> dict[str, dict[str, Any]]:
        return {
            AUDIO_INPUT: {
                "sources": {"mic": {"kind": "command", "argv": list(RECORDER_ARGV)}},
                "default_source": "mic",
                "transcription": {
                    "enabled": True,
                    "endpoint": STT_ENDPOINT,
                    "model": STT_MODEL,
                    "api_key": "",
                },
                "_source_factory": lambda spec: self.source,
                "_transcription_transport": self.stt,
                "_sleeper": self.clock.sleep,
            },
            AUDIO_OUTPUT: {
                "synthesis": {
                    "endpoint": SPEECH_ENDPOINT,
                    "model": SPEECH_MODEL,
                    "api_key": "",
                    "probe": False,
                },
                "voices": {"allowed": ["narrator"], "default": "narrator"},
                "outputs": {"speakers": {"player": {"argv": list(PLAYER_ARGV)}}},
                "default_output": "speakers",
                "stop_grace_seconds": 1,
                "_synthesis_transport": self.speech,
                "_player_runner": self.runner,
                "_sleeper": self.clock.sleep,
            },
        }

    def transcription_requests(self) -> list[dict[str, Any]]:
        """The call-time requests: the first one is the prepare-time probe."""

        return self.stt.requests[1:]


# --------------------------------------------------------------------------- #
# The two platforms
# --------------------------------------------------------------------------- #


class TwitchEdge:
    """The shipped ``twitch`` module over a scripted platform session."""

    name = TWITCH
    platform = "twitch"
    channel = BROADCASTER

    def __init__(self) -> None:
        self.websocket = FakeWebSocket(welcome())
        self.session = FakeTwitchSession(
            client_id="configured-client",
            bot_user_id=BOT_USER,
            websocket=self.websocket,
            sends=[sent_response("sent-1"), sent_response("sent-2")],
        )
        self._messages = 0

    def settings(self, diagnostics: list[str]) -> dict[str, Any]:
        async def no_delay(_: float) -> None:
            await asyncio.sleep(0)

        return {
            "client_id": "configured-client",
            "client_secret": "configured-client-secret",
            "access_token": "configured-access-token",
            "broadcaster_id": BROADCASTER,
            "bot_user_id": BOT_USER,
            "companion_name": COMPANION,
            "_session_factory": lambda: self.session,
            "_retry_delay": no_delay,
            "diagnostic_reporter": diagnostics.append,
        }

    async def say(self, text: str) -> None:
        self._messages += 1
        self.websocket.feed(
            notification(BROADCASTER, f"m-{self._messages}", text, viewer_id=VIEWER)
        )

    def sends(self) -> list[str]:
        return [call["json"]["message"] for call in self.session.helix_calls]

    async def close(self) -> None:
        return None


class FakePlatformEdge:
    """The ``fakeplatform`` fixture, loaded by the real loader like a shipped module."""

    name = FAKEPLATFORM
    platform = "fake"
    channel = FAKE_CHANNEL

    def __init__(self) -> None:
        self.transport = SimpleNamespace(outcomes=[], sends=[])
        self.handle: Any = None
        self._messages = 0

    def settings(self, diagnostics: list[str]) -> dict[str, Any]:
        return {
            "channel_ids": [FAKE_CHANNEL],
            "companion_name": COMPANION,
            "transport": self.transport,
            "diagnostic_reporter": diagnostics.append,
        }

    async def say(self, text: str) -> None:
        self._messages += 1
        published = await self.handle.inject(
            {
                "channel_id": FAKE_CHANNEL,
                "author": {"id": VIEWER, "display_name": "Viewer"},
                "message_id": f"m-{self._messages}",
                "text": text,
            }
        )
        assert published is not None

    def sends(self) -> list[str]:
        return [send["text"] for send in self.handle.sends]

    async def close(self) -> None:
        return None


PLATFORMS = {TWITCH: TwitchEdge, FAKEPLATFORM: FakePlatformEdge}


# --------------------------------------------------------------------------- #
# The brain side, and the agent side of the proxy case
# --------------------------------------------------------------------------- #


@dataclass
class BrainSide:
    """The brain's runtime: platform, the enabled phase 2 modules, the brain."""

    edge: Any
    context: RuntimeContext
    clock: ManualClock
    store: AttachmentStore
    session: ScriptedModel
    coordinator: PhaseCoordinator
    handles: dict[str, Any]
    diagnostics: list[str]
    audio: AudioSeams | None = None
    peer: ScenePeer | None = None
    stopped: bool = False

    @property
    def brain(self) -> Any:
        return self.handles[BRAIN]

    @property
    def bus(self) -> Any:
        return self.context.bus

    @property
    def executor(self) -> Any:
        return self.context.executor

    async def ask(self) -> None:
        """One viewer message addressed to the companion, as the platform delivers it."""

        await self.edge.say(f"{COMPANION}, can you hear me?")

    async def run(self) -> Any:
        """Drive one message to its terminal record; that record."""

        await self.ask()
        await wait_until(lambda: len(self.brain.scheduler.run_records()) == 1)
        await wait_until(lambda: len(events_of(self.bus, TRACE_BRAIN_RUN_COMPLETED)) == 1)
        await settle()
        return list(self.brain.scheduler.run_records().values())[-1]

    def completed(self) -> dict[str, Any]:
        (event,) = events_of(self.bus, TRACE_BRAIN_RUN_COMPLETED)
        return event["payload"]

    async def stop(self) -> Any:
        self.stopped = True
        return await self.coordinator.stop()

    async def close(self) -> None:
        if not self.stopped:
            await self.stop()
        await self.edge.close()
        if self.peer is not None:
            await self.peer.stop()


async def start_brain_side(
    platform: str,
    *,
    clock: ManualClock,
    bodies: tuple[Any, ...] = SCENARIO,
    enabled: tuple[str, ...] = (AUDIO_INPUT, AUDIO_OUTPUT),
    delivery: dict[str, Any] = SPEAK_DELIVERY,
    audio: AudioSeams | None = None,
    peer: ScenePeer | None = None,
) -> BrainSide:
    """Load the platform, *enabled* and the brain on one runtime; start them.

    The shipped brain gets the settings of the example profiles with only
    ``delivery`` and the required capabilities (``audio`` verified by its
    prepare-time probe, R4) changed. The coordinator runs the whole startup
    — prepare in activation order, the readiness barrier, ``start_inputs`` —
    and, for the shutdown cases, the one global shutdown deadline.
    """

    diagnostics: list[str] = []
    store = AttachmentStore(clock=clock, **STORE_LIMITS)
    actions = [CHAT_WRITE, AUDIO_CAPTURE, AUDIO_SPEAK, SCENE_SET]
    context = build_context(
        clock=clock,
        trigger_registry=TriggerRegistry(companion_name=COMPANION),
        chat=ChatContext(max_messages=16, max_bytes=8192, max_age_seconds=600.0, clock=clock),
        authorization=AuthorizationPolicy([grant(action) for action in actions]),
        attachments=store,
    )
    edge = PLATFORMS[platform]()
    session = ScriptedModel(*bodies)
    brain_settings = copy_settings(VALID_SETTINGS)
    brain_settings["delivery"] = copy.deepcopy(delivery)
    brain_settings["capabilities"] = {"required": ["structured_output", "audio"]}
    brain_settings.update(
        {
            "_session_factory": lambda: session,
            "_sleeper": clock.sleep,
            "diagnostic_reporter": diagnostics.append,
        }
    )
    available: dict[str, dict[str, Any]] = {}
    if audio is not None:
        available.update(audio.settings())
    if peer is not None:
        available[STREAM_CONTROL] = {
            "scenes": {
                "provider": {"kind": "websocket", "url": SCENE_URL, "password": SCENE_PASSWORD},
                "allowed": [TALKING],
            },
            "polls": {"enabled": False},
            "_websocket_factory": peer.factory,
            "_sleeper": clock.sleep,
            "_wall_clock": clock,
        }
    available[PROXY] = {
        "listen": {"host": "127.0.0.1", "port": 8765},
        "pairing_token": PROXY_TOKEN,
        "actions": [AUDIO_CAPTURE, AUDIO_SPEAK],
        # No socket is bound: the agent reaches the proxy over the in-memory pair.
        "_server_factory": lambda handler, **kwargs: FakeServer(handler=handler, **kwargs),
        "_sleeper": clock.sleep,
        "_wall_clock": lambda: 1_700_000_000.0,
        "diagnostic_reporter": diagnostics.append,
        "limits": {"attachments": dict(STORE_LIMITS)},
    }

    activations: list[Any] = []
    if platform == FAKEPLATFORM:
        fixtures = ModuleLoader(context.bus, FIXTURE_MODULES, context=context, environ={})
        activations += await fixtures.activate_enabled(
            {"enabled_modules": [FAKEPLATFORM], "modules": {FAKEPLATFORM: edge.settings(diagnostics)}}
        )
        shipped_names = [*enabled, BRAIN]
        modules = {name: available[name] for name in enabled}
    else:
        shipped_names = [TWITCH, *enabled, BRAIN]
        modules = {TWITCH: edge.settings(diagnostics), **{name: available[name] for name in enabled}}
    modules[BRAIN] = brain_settings
    shipped = ModuleLoader(context.bus, SHIPPED_MODULES, context=context, environ={})
    activations += await shipped.activate_enabled(
        {"enabled_modules": shipped_names, "modules": modules}
    )
    handles = {activation.name: activation.handle for activation in activations}
    if platform == FAKEPLATFORM:
        edge.handle = handles[FAKEPLATFORM]
    coordinator = PhaseCoordinator(
        activations,
        tasks=context.tasks,
        clock=clock,
        sleeper=clock.sleep,
        reporter=diagnostics.append,
        observer=context.health.observe_phase,
        shutdown_deadline_seconds=SHUTDOWN_DEADLINE,
        hook_timeout_seconds=HOOK_TIMEOUT,
    )
    side = BrainSide(
        edge=edge,
        context=context,
        clock=clock,
        store=store,
        session=session,
        coordinator=coordinator,
        handles=handles,
        diagnostics=diagnostics,
        audio=audio,
        peer=peer,
    )
    report = await coordinator.start()
    assert report.status == 0, (report, diagnostics)
    assert "audio" in side.brain.verified_capabilities
    return side


@dataclass
class AgentSide:
    """The agent profile's shape: ``audio_input``, ``audio_output`` and
    ``agent_link`` on the agent's own runtime, sharing the brain's clock."""

    context: RuntimeContext
    store: AttachmentStore
    coordinator: PhaseCoordinator
    handles: dict[str, Any]
    diagnostics: list[str]
    dials: asyncio.Queue[MemoryWebSocketPair] = field(default_factory=asyncio.Queue)

    @property
    def link(self) -> Any:
        return self.handles[AGENT_LINK]


async def start_agent_side(clock: ManualClock, audio: AudioSeams) -> AgentSide:
    diagnostics: list[str] = []
    store = AttachmentStore(clock=clock, **STORE_LIMITS)
    context = build_context(
        clock=clock,
        attachments=store,
        authorization=AuthorizationPolicy(
            [agent_grant(AUDIO_CAPTURE), agent_grant(AUDIO_SPEAK)]
        ),
    )
    dials: asyncio.Queue[MemoryWebSocketPair] = asyncio.Queue()

    async def connector(url: str, **_kwargs: Any) -> Any:
        assert url == BRAIN_URL
        return (await dials.get()).client

    modules = {
        **audio.settings(),
        AGENT_LINK: {
            "brain_url": BRAIN_URL,
            "pairing_token": PROXY_TOKEN,
            "agent_id": AGENT_ID,
            "actions": [AUDIO_CAPTURE, AUDIO_SPEAK],
            "_connector": connector,
            "_sleeper": clock.sleep,
            "diagnostic_reporter": diagnostics.append,
            "limits": {"attachments": dict(STORE_LIMITS)},
        },
    }
    loader = ModuleLoader(context.bus, SHIPPED_MODULES, context=context, environ={})
    activations = await loader.activate_enabled(
        {"enabled_modules": list(modules), "modules": modules}
    )
    handles = {activation.name: activation.handle for activation in activations}
    assert list(handles) == [AUDIO_INPUT, AUDIO_OUTPUT, AGENT_LINK]
    coordinator = PhaseCoordinator(
        activations, tasks=context.tasks, clock=clock, sleeper=clock.sleep,
        reporter=diagnostics.append,
    )
    report = await coordinator.start()
    assert report.status == 0, (report, diagnostics)
    return AgentSide(context, store, coordinator, handles, diagnostics, dials)


async def pair(brain: BrainSide, agent: AgentSide) -> tuple[MemoryWebSocketPair, asyncio.Task[Any]]:
    """Pair the agent's link to the brain's proxy over one in-memory pair."""

    proxy = brain.handles[PROXY]
    wire = MemoryWebSocketPair()
    handler = asyncio.create_task(proxy.connection_handler(wire.server))
    agent.dials.put_nowait(wire)
    await wait_until(lambda: agent.link.paired and proxy.paired)
    await settle()
    ready = brain.context.actions.registered_ready()
    assert AUDIO_CAPTURE in ready and AUDIO_SPEAK in ready
    return wire, handler


# --------------------------------------------------------------------------- #
# Reading one run: the contracts both transports must share
# --------------------------------------------------------------------------- #


def observation_contract(observation: Any) -> dict[str, Any]:
    """What a transport must preserve of one observation: keys, status, codes."""

    error = observation.error
    return {
        "status": observation.status,
        "code": None if error is None else error.get("code"),
        "result": None if observation.result is None else sorted(observation.result),
        "parts": [sorted(part) for part in observation.parts],
        "part_types": [part["type"] for part in observation.parts],
        "transcription": [
            sorted(part["transcription"]) for part in observation.parts if "transcription" in part
        ],
    }


@dataclass
class ScenarioRun:
    """One run of the AC35 scenario, and what AC35 compares across runs."""

    record: Any
    completed: dict[str, Any]
    capture: Any
    speak: Any
    chat: Any
    brain_attachment: str
    sends: list[str]

    def contracts(self) -> dict[str, Any]:
        return {
            "capture": observation_contract(self.capture),
            "speak": observation_contract(self.speak),
            "chat": {"status": self.chat.status, "code": self.chat.error and self.chat.error.get("code")},
            "deliveries": [
                (entry["action"], entry["text"], entry["status"])
                for entry in self.completed["deliveries"]
            ],
            "transcription_status": self.capture.result["transcription_status"],
            "playback": (self.speak.result["playback"], self.speak.result["played_ms"]),
        }


async def run_scenario(brain: BrainSide) -> ScenarioRun:
    """Drive one message through AC35's script and check its local facts."""

    record = await brain.run()
    run = record.run_id
    completed = brain.completed()
    capture = brain.executor.outcome(f"{run}/call-1")
    chat = brain.executor.outcome(f"{run}/call-2")
    speak = brain.executor.outcome(f"{run}/call-3")

    # The record: two model turns, three action calls, both entries delivered.
    assert record.status == "success", (record, completed, brain.diagnostics)
    assert (completed["turns"], completed["action_calls"]) == (2, 3)
    assert [(entry["action"], entry["text"], entry["status"]) for entry in completed["deliveries"]] == [
        (CHAT_WRITE, True, "success"),
        (AUDIO_SPEAK, True, "success"),
    ]
    assert completed["audio_omitted"] == 0

    # The capture: one audio_ref leased in the brain's store, its transcription.
    assert capture.status == "success", capture.error
    assert capture.result["transcription_status"] == "ok"
    (part,) = capture.parts
    assert part["type"] == "audio_ref"
    assert part["size"] == SEGMENT_BYTES and part["duration_ms"] == 3000
    assert part["transcription"]["text"] == TRANSCRIPT
    assert part["transcription"]["truncated"] is False

    # The model heard it: the second request carries the audio and the text.
    first, second = brain.session.requests()
    assert [tool["function"]["name"] for tool in first["tools"]] == [AUDIO_CAPTURE]
    assert not carries_audio_part(first)
    assert carries_audio_part(second)
    assert TRANSCRIPT in json.dumps(second)

    # The delivery: one chat send, one completed playback of the final text.
    sends = brain.edge.sends()
    assert sends == [ANSWER]
    assert chat.status == "success"
    assert speak.status == "success", speak.error
    assert speak.result["playback"] == "completed"
    assert speak.result["duration_ms"] == speak.result["played_ms"] == 1500

    # The attachment is released with the run.
    assert brain.store.lookup(part["attachment_id"]) is None
    assert brain.store.object_count == 0
    return ScenarioRun(record, completed, capture, speak, chat, part["attachment_id"], sends)


def assert_the_audio_edges_served_it(audio: AudioSeams) -> None:
    """The scripted STT saw the stored segment once; the synthesis spoke the answer once."""

    assert audio.source.reads == [3]
    (stt,) = audio.transcription_requests()
    assert len(stt["file"]) == SEGMENT_BYTES
    (request,) = audio.speech.requests
    assert request["json"]["input"] == ANSWER
    assert audio.runner.starts == [PLAYER_ARGV]
    assert audio.runner.received == SPEECH_BODY
    assert audio.runner.stops == [] and audio.runner.kills == []


# --------------------------------------------------------------------------- #
# AC35 — local providers, then across the proxy, on both platforms
# --------------------------------------------------------------------------- #


async def local_scenario(platform: str) -> ScenarioRun:
    clock = ManualClock()
    audio = AudioSeams.build(clock)
    brain = await start_brain_side(platform, clock=clock, audio=audio)
    try:
        bindings = brain.context.actions
        assert [b.module for b in bindings.bindings(AUDIO_CAPTURE)] == [AUDIO_INPUT]
        assert [b.module for b in bindings.bindings(AUDIO_SPEAK)] == [AUDIO_OUTPUT]
        result = await run_scenario(brain)
        assert_the_audio_edges_served_it(audio)
        assert result.capture.provenance["provider"] != PROXY
        return result
    finally:
        await brain.close()


async def proxy_scenario(platform: str) -> ScenarioRun:
    clock = ManualClock()
    audio = AudioSeams.build(clock)
    brain = await start_brain_side(platform, clock=clock, enabled=(PROXY,))
    agent = await start_agent_side(clock, audio)
    handler: asyncio.Task[Any] | None = None
    try:
        bindings = brain.context.actions
        assert [b.module for b in bindings.bindings(AUDIO_CAPTURE)] == [PROXY]
        assert [b.module for b in bindings.bindings(AUDIO_SPEAK)] == [PROXY]
        wire, handler = await pair(brain, agent)

        result = await run_scenario(brain)
        assert_the_audio_edges_served_it(audio)
        assert result.capture.provenance["remote"]["provider"] == AUDIO_INPUT

        # The segment crossed once by reference, under the agent's own id.
        headers = [
            json.loads(message.data)
            for message in wire.client.sent
            if message.type == WSMsgType.TEXT and json.loads(message.data)["type"] == "attachment"
        ]
        (header,) = headers
        assert header["content_type"] == "audio/wav" and header["size"] == SEGMENT_BYTES
        binaries = [m.data for m in wire.client.sent if m.type == WSMsgType.BINARY]
        assert [len(data) for data in binaries] == [SEGMENT_BYTES]
        # Released on both stores: the agent's copy after its transfer, the
        # brain's with the run.
        assert agent.store.lookup(header["attachment_id"]) is None
        assert brain.store.lookup(result.brain_attachment) is None
        assert agent.store.object_count == 0 and brain.store.object_count == 0

        # The agent-side calls: one capture and one speech, nothing else.
        outcomes = agent.context.executor.outcomes()
        assert sorted(o.status for o in outcomes.values()) == ["success", "success"]
        return result
    finally:
        await agent.coordinator.stop()
        await brain.close()
        if handler is not None:
            if not handler.done():
                handler.cancel()
            await asyncio.gather(handler, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("platform", [TWITCH, FAKEPLATFORM])
async def test_ac35_the_scenario_has_one_contract_locally_and_across_the_proxy(platform: str) -> None:
    """AC35 (R1, R3, R4, R5): the scripted model proposes ``audio.capture
    {seconds: 3}``, sees the ``audio_ref`` and its transcription, answers;
    the delivery ``[chat.write, audio.speak]`` runs. Locally and across the
    proxy the record shows ``turns == 2``, ``action_calls == 3`` (capture,
    chat.write, audio.speak), 1 chat send, 1 completed playback whose
    synthesis ``input`` is the final text, the attachment released after the
    run (``lookup`` is ``None`` on both stores in the proxy case), and the
    two transports produce the same observation and delivery contracts —
    the same keys, statuses and codes."""

    local = await local_scenario(platform)
    remote = await proxy_scenario(platform)

    assert remote.contracts() == local.contracts()
    assert local.sends == remote.sends == [ANSWER]


@pytest.mark.asyncio
async def test_ac35_both_platforms_serve_one_contract() -> None:
    """AC35, platform neutrality: the twitch module and the fixture platform
    yield the same capture, speech and delivery contracts — the brain holds
    no platform branch either."""

    twitch = await local_scenario(TWITCH)
    fake = await local_scenario(FAKEPLATFORM)

    assert twitch.contracts() == fake.contracts()


# --------------------------------------------------------------------------- #
# AC35 — the brain holds no capture, audio or transport branch
# --------------------------------------------------------------------------- #

#: Decision 5's sites, by function or module-level name: the R4 adapter
#: encoding, the capability probe, and the ``audio_omitted`` /
#: ``capability_missing`` record.
ADAPTER_ENCODING = {
    "_ModelAdapter._encode_part",
    "_TurnEntry",
    "_TurnEntry.messages",
    "_observation_audio",
    "_part_summaries",
    "_estimate_message_tokens",
    "_audio_token_estimate",
    "_AUDIO_MS_PER_TOKEN",
    "_AUDIO_MESSAGE_TEXT",
    "_IMAGE_MESSAGE_TEXT",
    "_PART_AUDIO_REF",
    "_ATTACHMENT_PART_TYPES",
    "_PART_INPUT_AUDIO",
    "_AUDIO_FORMAT_WAV",
}
PROBE = {
    "_ModelAdapter.probe",
    "_ModelAdapter._probe_reason",
    "_names_audio_input",
    "_PROBE_KIND_AUDIO",
    "_AUDIO_REJECTED_STATUSES",
    "KNOWN_CAPABILITIES",
    "CAPABILITY_AUDIO",
    "PROBE_REASON_AUDIO_REJECTED",
    "__all__",
}
OMISSION_RECORD = {"BrainModule._propose", "_RunState", "_RunState.correlation"}
ALLOWED_SITES = ADAPTER_ENCODING | PROBE | OMISSION_RECORD
UNTOUCHED = ("BrainModule._resolve_delivery", "BrainModule._deliver_all", "BrainModule._loop")
FORBIDDEN_WORDS = re.compile(r"audio|capture|proxy", re.IGNORECASE)


def brain_code_hits() -> list[tuple[int, str, str]]:
    """``(line, owner, token)`` for every code token of the brain module
    naming ``audio``, ``capture`` or ``proxy`` — comments and docstrings
    excluded, the owner being the innermost function or class, the
    module-level name assigned, or the imported name."""

    source = BRAIN_SOURCE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    docstrings: set[tuple[int, int]] = set()
    spans: list[tuple[int, int, str]] = []
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list):
            for statement in body:
                if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant):
                    if isinstance(statement.value.value, str):
                        docstrings.add((statement.lineno, statement.col_offset))

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = prefix + child.name
                spans.append((child.lineno, child.end_lineno or child.lineno, name))
                walk(child, name + ".")
            elif prefix == "" and isinstance(child, (ast.Assign, ast.AnnAssign)):
                target = child.targets[0] if isinstance(child, ast.Assign) else child.target
                if isinstance(target, ast.Name):
                    spans.append((child.lineno, child.end_lineno or child.lineno, target.id))
            elif prefix == "" and isinstance(child, ast.ImportFrom):
                for alias in child.names:
                    spans.append((child.lineno, child.end_lineno or child.lineno, f"import:{alias.name}"))
            else:
                walk(child, prefix)

    walk(tree, "")
    hits: list[tuple[int, str, str]] = []
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type not in (tokenize.NAME, tokenize.STRING):
            continue
        if token.start in docstrings or not FORBIDDEN_WORDS.search(token.string):
            continue
        line = token.start[0]
        owners = sorted((end - start, name) for start, end, name in spans if start <= line <= end)
        owner = owners[0][1] if owners else "<module>"
        if owner.startswith("import:"):
            # One ``from ... import (...)`` spans every imported name: the
            # owner is the name the token is.
            owner = token.string
        hits.append((line, owner, token.string))
    return hits


def test_ac35_the_brain_names_audio_capture_or_proxy_only_at_decision_5_sites() -> None:
    """AC35 grep: in ``modules/brain/__init__.py`` the words ``audio``,
    ``capture`` and ``proxy`` appear in code only in the R4 adapter encoding,
    the capability probe and the ``audio_omitted``/``capability_missing``
    record (decision 5) — never in the delivery resolution, the delivery
    step or the loop, and ``proxy`` nowhere."""

    hits = brain_code_hits()
    owners = {owner for _line, owner, _token in hits}
    outside = [hit for hit in hits if hit[1] not in ALLOWED_SITES]
    assert outside == [], outside
    assert not [hit for hit in hits if "proxy" in hit[2].lower()]
    assert not [hit for hit in hits if hit[1].startswith(UNTOUCHED)]
    # The allowlist is not vacuous: each of the three kinds of site is found.
    assert owners & ADAPTER_ENCODING
    assert owners & PROBE
    assert owners & OMISSION_RECORD
    source = BRAIN_SOURCE.read_text(encoding="utf-8")
    for name in UNTOUCHED:
        assert f"def {name.split('.')[-1]}(" in source, name


# --------------------------------------------------------------------------- #
# AC35 — shutdown during the capture, the playback and a scene change
# --------------------------------------------------------------------------- #


async def stop_within_the_deadline(brain: BrainSide) -> tuple[Any, float]:
    """Start the coordinator's shutdown and drive the clock until it ends.

    The clock moves in 0.5 s steps — past the brain's drain bound, each
    module's stop grace and drain deadline — and never past the global
    shutdown deadline: a shutdown still running there fails the test.
    """

    requested_at = brain.clock.now
    stop = asyncio.ensure_future(brain.stop())
    await settle()
    while not stop.done():
        assert brain.clock.now < requested_at + SHUTDOWN_DEADLINE, "shutdown overran its deadline"
        brain.clock.advance(0.5)
        await settle()
    report = stop.result()
    assert brain.clock.now <= requested_at + SHUTDOWN_DEADLINE
    # The brain's drain waits for its run for its whole allowance and ties
    # with the coordinator's hook bound: the only failure a shutdown may
    # report is a drain phase; every call still has its own outcome.
    assert all("phase 'drain'" in failure for failure in report.failures), report.failures
    return report, requested_at


def outcome_of(brain: BrainSide, action: str) -> Any:
    """The terminal observation of the run's one call to *action*."""

    (call_id,) = [
        event["payload"]["call_id"]
        for event in events_of(brain.bus, "action.started")
        if event["payload"]["action"] == action
    ]
    return brain.executor.outcome(call_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("platform", [TWITCH, FAKEPLATFORM])
async def test_ac35_a_shutdown_during_the_capture_ends_it_cancelled_with_no_attachment(
    platform: str,
) -> None:
    """AC35 (R3, decision 1): the recorder has emitted nothing when the
    shutdown starts; ``audio_input``'s ``drain()`` kills it and the capture
    comes back through the provider path ``cancelled`` — 0 attachments, 0
    transcription requests — and the shutdown ends within its deadline."""

    clock = ManualClock()
    audio = AudioSeams.build(clock, source=FakeAudioSource(gated=True))
    brain = await start_brain_side(platform, clock=clock, audio=audio)
    try:
        await brain.ask()
        await wait_until(audio.source.entered.is_set)
        await settle()

        await stop_within_the_deadline(brain)

        capture = outcome_of(brain, AUDIO_CAPTURE)
        assert capture.status == "cancelled", capture
        assert capture.error["code"] == "cancelled"
        # The module's own record, not the executor's generic one.
        assert "timed out" not in capture.error["message"]
        assert capture.parts == ()
        assert audio.source.kills == 1
        assert audio.transcription_requests() == []
        assert brain.store.object_count == 0
        assert len(brain.brain.scheduler.run_records()) == 1
    finally:
        await brain.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("platform", [TWITCH, FAKEPLATFORM])
async def test_ac35_a_shutdown_during_the_playback_ends_it_cancelled_with_its_played_ms(
    platform: str,
) -> None:
    """AC35 (R1, decision 1): the answer's 10 s of speech are playing — the
    player accepted the header and 128 000 data bytes, 4 s — when the
    shutdown starts; ``audio_output``'s ``drain()`` stops the player once and
    the speech comes back ``cancelled`` with ``played_ms == 4000``; the chat
    entry kept its ``success``; the shutdown ends within its deadline."""

    clock = ManualClock()
    player = FakePlayer(accept_limit=128_000)
    audio = AudioSeams.build(clock, speech=LONG_SPEECH, players=(player,))
    brain = await start_brain_side(platform, clock=clock, audio=audio)
    try:
        await brain.ask()
        await wait_until(lambda: player.accepted == 44 + 128_000)
        await settle()

        await stop_within_the_deadline(brain)

        speak = outcome_of(brain, AUDIO_SPEAK)
        assert speak.status == "cancelled", speak
        assert speak.error["code"] == "cancelled"
        assert speak.error["played_ms"] == 4000
        assert speak.error["duration_ms"] == 10_000
        assert "timed out with emission" not in speak.error["message"]
        assert audio.runner.stops == [1]
        (request,) = audio.speech.requests
        assert request["json"]["input"] == ANSWER
        assert outcome_of(brain, AUDIO_CAPTURE).status == "success"
        assert outcome_of(brain, CHAT_WRITE).status == "success"
        assert brain.edge.sends() == [ANSWER]
        assert brain.store.object_count == 0
    finally:
        await brain.close()


class HeldScenePeer(ScenePeer):
    """The P15 peer whose set command is applied and whose answer a test sends."""

    def pending_set(self) -> dict[str, Any]:
        (request,) = self.requested(REQUEST_SET_SCENE)
        return request

    async def answer_set(self) -> None:
        request = self.pending_set()
        self.current = request["requestData"]["sceneName"]
        response = {
            "requestType": REQUEST_SET_SCENE,
            "requestId": request["requestId"],
            "requestStatus": {"result": True, "code": 100},
        }
        self.responses.append(response)
        await self.pairs[-1].server.send_str(json.dumps({"op": 7, "d": response}))


@pytest.mark.asyncio
@pytest.mark.parametrize("confirmation", ["in time", "too late"])
async def test_ac35_a_shutdown_during_a_scene_change_resolves_it_per_ac23(confirmation: str) -> None:
    """AC35 → AC23 (R6, decision 1): the delivery ``[chat.write,
    stream.scene.set {scene: Talking}]`` has sent its set command over the
    ``websocket`` provider to the in-process peer, whose answer is pending,
    when the shutdown starts. ``stream_control``'s ``drain()`` gives the sent
    command the drain deadline: answered in time, the read-back confirms it
    (``success``); not answered, it ends ``external_unknown`` (cause
    ``confirmation_lost``) with no read after it. One set command either
    way, and the shutdown ends within its global deadline."""

    clock = ManualClock()
    peer = HeldScenePeer(answers={REQUEST_SET_SCENE: ["silent"]})
    brain = await start_brain_side(
        FAKEPLATFORM,
        clock=clock,
        bodies=(final(ANSWER),),
        enabled=(STREAM_CONTROL,),
        delivery=SCENE_DELIVERY,
        peer=peer,
    )
    stream = brain.handles[STREAM_CONTROL]
    try:
        await brain.ask()
        await wait_until(lambda: len(peer.requested(REQUEST_SET_SCENE)) == 1)
        await settle()
        requests_before = len(peer.requests)

        requested_at = clock.now
        stop = asyncio.ensure_future(brain.stop())
        await settle()
        answered = False
        while not stop.done():
            assert clock.now < requested_at + SHUTDOWN_DEADLINE, "shutdown overran its deadline"
            if confirmation == "in time" and stream._draining and not answered:
                # The drain has begun: the pending answer arrives within it.
                answered = True
                await peer.answer_set()
                await settle()
                continue
            clock.advance(0.5)
            await settle()
        report = stop.result()
        assert clock.now <= requested_at + SHUTDOWN_DEADLINE
        assert all("phase 'drain'" in failure for failure in report.failures), report.failures

        scene = outcome_of(brain, SCENE_SET)
        after = peer.request_types()[requests_before:]
        if confirmation == "in time":
            assert scene.status == "success", scene
            assert scene.result["scene"] == TALKING
            assert after == ["GetCurrentProgramScene"]
        else:
            assert scene.status == "external_unknown", scene
            assert scene.error["cause"] == CAUSE_CONFIRMATION_LOST
            assert after == []
        assert len(peer.requested(REQUEST_SET_SCENE)) == 1
        assert outcome_of(brain, CHAT_WRITE).status == "success"
        assert brain.edge.sends() == [ANSWER]
    finally:
        await brain.close()
