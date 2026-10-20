"""Declared trigger types (phase 8).

P2 holds AC15 and the route half of AC16 (R4): the four kinds appended to
the vocabulary, and a brain route naming one of them.

P1 holds the phase-7 baseline half of AC16 (R4): each shipped ``*.example``
profile, loaded as ``core.main`` loads it and started by the coordinator it
builds, is driven on one fixed event sequence against the fakes of
``tests/conftest.py`` — chat messages, each phase-7 notice kind, a ``cue``
and a ``watch_tick`` — and what it decided is compared with the record taken
at ``7782e5f``, before any production change
(``tests/fixtures/phase8/profile_baseline.json``): every trigger decision as
``(input, reason, policy_version)``, every platform subscription the fake
platform saw and every admission result. The runtime's clock is a
:class:`ManualClock` and its random source a :class:`RecordedRandom`, so the
record holds no timestamp and no random identifier: only reasons, versions and
identifiers derived from the fixed sequence.

A ``tip`` and a ``cue`` have no producer in any shipped profile (no profile
enables an input that emits them), so they are published on the bus as a
normalised chat event, the way their producer would publish one; the record
then shows what the profile does with them.
"""

from __future__ import annotations

import asyncio
import json
import re
import ssl
import sys
import time
import types
from collections.abc import Mapping
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised on the 3.10 floor only
    import tomli as tomllib

import pytest

import core.main as application
from core.contracts import (
    CHAT_FAMILY_KINDS,
    EVENT_KINDS,
    PLATFORM_NOTICE_KINDS,
    TRACE_BRAIN_ADMISSION_ACCEPTED,
    TRACE_BRAIN_ADMISSION_REJECTED,
    TRACE_BRAIN_RUN_COMPLETED,
    TRACE_INPUT_TRIGGER_ACCEPTED,
    TRACE_INPUT_TRIGGER_REJECTED,
    input_family_of_event,
    validate_event_kind,
)
from conftest import (
    FakeCaptureSource,
    FakeResponse,
    FakeSession,
    ManualClock,
    RecordedRandom,
    events_of,
    final,
    settle,
)
from modules.twitch import EVENTSUB_SUBSCRIPTIONS_URL
from test_examples import ROOT, DeviceEdges, _activate_example, _sample_environ
from test_presence_pack import PresenceTwitchSession, presence_environ
from test_integration import FakeWebSocket, welcome
from test_stream_control import ScenePeer
from test_twitch import chat_notice, follow_notice

BASELINE = Path(__file__).parent / "fixtures" / "phase8" / "profile_baseline.json"
#: The shipped profiles, by file name: every one of them is replayed.
SHIPPED_PROFILES = (
    "agent.yaml.example",
    "config.server.yaml.example",
    "config.yaml.example",
    "presence.yaml.example",
)
PRESENCE = "presence.yaml.example"

VIEWER = "viewer-3"
#: The interval of the presence profile's watch: the clock moves this much
#: once, after the notices, so a session ``!watch`` started ticks once.
WATCH_INTERVAL_SECONDS = 60.0
#: Values enough for any draw a profile takes; a phase-7 profile takes none
#: from a decision, and the record keeps the count.
RECORDED_VALUES = (0.5,) * 64
#: The bound on one step's quiescence, in rounds of loop turns.
QUIESCENCE_ROUNDS = 400


class AnsweringModel(FakeSession):
    """The model edge: every scenario request is answered with one final text."""

    async def _answer(self, url: str, kwargs: dict[str, Any]) -> Any:
        self.post_calls.append({"url": url, **kwargs})
        return FakeResponse(200, final("Thanks!"))


class _TimeOfMain(types.ModuleType):
    """``core.main``'s view of :mod:`time`: ``monotonic`` is the manual clock."""

    def __init__(self, clock: ManualClock) -> None:
        super().__init__("time")
        self.monotonic = clock

    def __getattr__(self, name: str) -> Any:
        return getattr(time, name)


class _RandomOfMain(types.ModuleType):
    """``core.main``'s view of :mod:`random`: ``Random()`` is the recorded source."""

    def __init__(self, source: RecordedRandom) -> None:
        super().__init__("random")
        self.Random = lambda *args: source

    def __getattr__(self, name: str) -> Any:
        import random

        return getattr(random, name)


async def _refuse_dial(*args: Any, **kwargs: Any) -> Any:
    raise ConnectionRefusedError("the baseline dials nothing")


class _Listener:
    """What the proxy's listener seam returns: nothing listens."""

    async def stop(self) -> None:
        return None

    async def close(self) -> None:
        return None


def _no_certificate(self: Any, *args: Any, **kwargs: Any) -> None:
    return None


async def _listener(handler: Any, **kwargs: Any) -> _Listener:
    return _Listener()


# --------------------------------------------------------------------------- #
# The fixed sequence
# --------------------------------------------------------------------------- #


def _chat(channel: str, author: str, message_id: str, text: str, badges: tuple[str, ...] = ()) -> dict[str, Any]:
    return {
        "metadata": {
            "message_id": f"envelope-{message_id}",
            "message_type": "notification",
            "subscription_type": "channel.chat.message",
        },
        "payload": {
            "subscription": {"type": "channel.chat.message"},
            "event": {
                "broadcaster_user_id": channel,
                "chatter_user_id": author,
                "chatter_user_name": f"User {author}",
                "message_id": message_id,
                "message": {"text": text},
                "badges": [{"set_id": badge, "id": "1", "info": ""} for badge in badges],
            },
        },
    }


def _on_channel(envelope: dict[str, Any], channel: str) -> dict[str, Any]:
    envelope["payload"]["event"]["broadcaster_user_id"] = channel
    return envelope


def _platform_frames(channel: str) -> list[tuple[str, dict[str, Any]]]:
    """The chat messages and the six platform notices, as EventSub frames."""

    return [
        ("chat:plain", _chat(channel, VIEWER, "baseline-chat-1", "hello everyone")),
        ("chat:keyword", _chat(channel, VIEWER, "baseline-chat-2", "!ask Companion, hello")),
        ("chat:mention", _chat(channel, VIEWER, "baseline-chat-3", "Companion, how are you?")),
        ("chat:command", _chat(channel, VIEWER, "baseline-chat-4", "!missed what happened?")),
        (
            "chat:broadcaster_watch",
            _chat(channel, channel, "baseline-chat-5", "!watch", ("broadcaster",)),
        ),
        (
            "notice:sub",
            _on_channel(
                chat_notice("baseline-sub", "sub", sub={"sub_tier": "1000", "is_prime": False}),
                channel,
            ),
        ),
        (
            "notice:resub",
            _on_channel(
                chat_notice(
                    "baseline-resub",
                    "resub",
                    resub={"cumulative_months": 3, "sub_tier": "1000", "is_prime": False},
                ),
                channel,
            ),
        ),
        (
            "notice:sub_gift",
            _on_channel(
                chat_notice(
                    "baseline-sub-gift",
                    "sub_gift",
                    sub_gift={"sub_tier": "1000", "recipient_user_id": "viewer-8"},
                ),
                channel,
            ),
        ),
        (
            "notice:community_sub_gift",
            _on_channel(
                chat_notice(
                    "baseline-community-gift",
                    "community_sub_gift",
                    community_sub_gift={"id": "gift-1", "total": 5, "sub_tier": "1000"},
                ),
                channel,
            ),
        ),
        (
            "notice:raid",
            _on_channel(
                chat_notice(
                    "baseline-raid",
                    "raid",
                    chatter_id="42",
                    raid={"user_id": "42", "user_name": "Raider", "viewer_count": 3},
                ),
                channel,
            ),
        ),
        ("notice:follow", _on_channel(follow_notice("baseline-follow", "55"), channel)),
    ]


def _bus_event(channel: str, message_id: str, kind: str, text: str) -> dict[str, Any]:
    """A normalised chat event of *kind*, as an input producing it publishes one."""

    return {
        "platform": "twitch",
        "channel_id": channel,
        "author": {"id": VIEWER, "display_name": "Viewer"},
        "message_id": message_id,
        "text": text,
        "kind": kind,
    }


# --------------------------------------------------------------------------- #
# One profile, started and driven
# --------------------------------------------------------------------------- #


def _environ(profile: str, directory: Path) -> dict[str, str]:
    if profile == PRESENCE:
        return presence_environ(directory)
    return _sample_environ(ROOT / profile)


def _seams(
    clock: ManualClock,
    platform: PresenceTwitchSession,
    model: AnsweringModel,
    peer: ScenePeer,
) -> dict[str, dict[str, Any]]:
    edges = DeviceEdges(peer=peer, clock=clock)
    return {
        **edges.seams(),
        "twitch": {"_session_factory": lambda: platform},
        "brain": {"_session_factory": lambda: model, "_sleeper": clock.sleep},
        "audit": {"_writer": lambda line: None},
        "capture": {"_source_factory": lambda spec: FakeCaptureSource()},
        "proxy": {"_server_factory": _listener},
        "agent_link": {"_connector": _refuse_dial, "_sleeper": clock.sleep},
        "clips": {"_sleeper": clock.sleep},
        "viewer_memory": {"_sleeper": clock.sleep, "_wall_clock": lambda: 1_790_000_000.0},
        "moderation": {"_sleeper": clock.sleep},
        "watch": {"_sleeper": clock.sleep},
    }


def _snapshot(bus: Any) -> dict[str, list[Any]]:
    decisions = [
        [
            event["payload"]["source_event_id"],
            event["payload"]["input"],
            event["payload"]["reason"],
            event["payload"]["policy_version"],
        ]
        for event in bus.list_events()
        if event["type"] in (TRACE_INPUT_TRIGGER_ACCEPTED, TRACE_INPUT_TRIGGER_REJECTED)
    ]
    admissions = [
        [
            event["payload"]["source_event_id"],
            event["payload"]["kind"],
            event["payload"]["session"],
            event["payload"]["reason"],
        ]
        for event in bus.list_events()
        if event["type"] in (TRACE_BRAIN_ADMISSION_ACCEPTED, TRACE_BRAIN_ADMISSION_REJECTED)
    ]
    return {"decisions": decisions, "admissions": admissions}


async def _quiesce(bus: Any) -> None:
    """Turn the loop until every admitted run completed and nothing new happened."""

    previous = -1
    for _ in range(QUIESCENCE_ROUNDS):
        await settle(50)
        await asyncio.sleep(0)
        admitted = {
            event["payload"].get("run_id")
            for event in events_of(bus, TRACE_BRAIN_ADMISSION_ACCEPTED)
        }
        completed = {
            event["payload"].get("run_id") for event in events_of(bus, TRACE_BRAIN_RUN_COMPLETED)
        }
        size = len(bus.list_events())
        if admitted <= completed and size == previous:
            return
        previous = size
    raise AssertionError("the profile did not settle on the fixed sequence")


async def record_profile(profile: str, directory: Path) -> dict[str, Any]:
    """Start *profile* on the fakes, drive the fixed sequence, return its record."""

    clock = ManualClock()
    source = RecordedRandom(RECORDED_VALUES)
    environ = _environ(profile, directory)
    websocket = FakeWebSocket(welcome())
    platform = PresenceTwitchSession(
        client_id=environ.get("TWITCH_CLIENT_ID", "absent"),
        bot_user_id=environ.get("TWITCH_BOT_USER_ID", "absent"),
        websocket=websocket,
    )
    model = AnsweringModel()
    peer = ScenePeer(scenes=("Starting", "Main", "Break", "Ending"), current="Main")
    reported: list[str] = []

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(application, "time", _TimeOfMain(clock))
        patch.setattr(application, "random", _RandomOfMain(source))
        runtime, activations, _ = await _activate_example(
            environ, ROOT / profile, _seams(clock, platform, model, peer)
        )
    coordinator = application._coordinator(activations, runtime, reported.append)
    steps: list[dict[str, Any]] = []
    try:
        with pytest.MonkeyPatch.context() as patch:
            # The server profile's listener names a certificate by reference;
            # the dependency loading it is the fake, never the proxy.
            patch.setattr(ssl.SSLContext, "load_cert_chain", _no_certificate)
            report = await coordinator.start()
        bus = runtime.bus
        await _quiesce(bus)
        enabled = {activation.name for activation in activations}

        async def step(name: str, drive: Any) -> None:
            before = _snapshot(bus)
            await drive()
            await _quiesce(bus)
            after = _snapshot(bus)
            steps.append(
                {
                    "step": name,
                    "decisions": after["decisions"][len(before["decisions"]):],
                    "admissions": after["admissions"][len(before["admissions"]):],
                }
            )

        channel = environ.get("TWITCH_BROADCASTER_ID", "absent")
        for name, frame in _platform_frames(channel):
            if "twitch" in enabled:

                async def feed(frame: dict[str, Any] = frame) -> None:
                    websocket.feed(frame)

            else:

                async def feed() -> None:
                    return None

            await step(name, feed)
        for kind, message_id, text in (
            ("tip", "baseline-tip", "a tip"),
            ("cue", "baseline-cue", "a routed cue"),
        ):

            async def publish(kind: str = kind, message_id: str = message_id, text: str = text) -> None:
                await bus.publish(
                    "channel.chat.message",
                    _bus_event(channel, message_id, kind, text),
                    {"source": "twitch", "schema_version": 2},
                )

            await step(f"bus:{kind}", publish)

        async def tick() -> None:
            clock.advance(WATCH_INTERVAL_SECONDS)

        await step("clock:watch_tick", tick)
        subscriptions = sorted(
            (
                {
                    "type": call["json"]["type"],
                    "version": call["json"]["version"],
                    "condition": call["json"]["condition"],
                }
                for call in platform.post_calls
                if call["url"] == EVENTSUB_SUBSCRIPTIONS_URL
            ),
            key=lambda entry: json.dumps(entry, sort_keys=True),
        )
        return {
            "start_status": report.status,
            "enabled": sorted(enabled),
            "subscriptions": subscriptions,
            "steps": steps,
            "draws": source.ordinal,
        }
    finally:
        await coordinator.stop()
        await peer.stop()


async def record_shipped_profiles(directory: Path) -> dict[str, Any]:
    record: dict[str, Any] = {}
    for profile in SHIPPED_PROFILES:
        workspace = directory / profile.replace(".", "_")
        workspace.mkdir()
        record[profile] = await record_profile(profile, workspace)
    return json.loads(json.dumps(record, sort_keys=True))


def _differences(expected: Any, actual: Any, path: str = "") -> list[str]:
    if isinstance(expected, Mapping) and isinstance(actual, Mapping):
        found: list[str] = []
        for key in sorted(set(expected) | set(actual)):
            if key not in actual:
                found.append(f"{path}/{key}: missing")
            elif key not in expected:
                found.append(f"{path}/{key}: unexpected {actual[key]!r}")
            else:
                found.extend(_differences(expected[key], actual[key], f"{path}/{key}"))
        return found
    if isinstance(expected, list) and isinstance(actual, list) and len(expected) == len(actual):
        return [
            difference
            for index, (left, right) in enumerate(zip(expected, actual))
            for difference in _differences(left, right, f"{path}[{index}]")
        ]
    return [] if expected == actual else [f"{path}: expected {expected!r}, got {actual!r}"]


# --------------------------------------------------------------------------- #
# AC16 (R4): the phase-7 baseline
# --------------------------------------------------------------------------- #


async def test_ac16_shipped_profiles_decide_as_in_phase7(tmp_path: Path) -> None:
    """AC16 (R4): each shipped profile, replayed on the fixed sequence against
    the fakes, makes the decisions (reason and policy version), the platform
    subscriptions and the admissions the phase-7 tree made — 0 differences
    from the record taken at ``7782e5f``."""

    expected = json.loads(BASELINE.read_text(encoding="utf-8"))
    assert sorted(expected) == sorted(SHIPPED_PROFILES)
    actual = await record_shipped_profiles(tmp_path)
    assert _differences(expected, actual) == []
    # The record is not vacuous: the sequence was decided and admitted.
    assert any(step["decisions"] for step in expected[PRESENCE]["steps"])
    assert any(step["admissions"] for step in expected["config.yaml.example"]["steps"])


def test_ac16_runtime_dependencies_are_exactly_aiohttp_and_pyyaml() -> None:
    """AC16 (R4): the runtime dependency list is exactly ``aiohttp`` and ``PyYAML``."""

    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    names = sorted(
        re.match(r"[A-Za-z0-9][A-Za-z0-9._-]*", requirement.strip()).group(0)
        for requirement in project["dependencies"]
    )
    assert names == ["PyYAML", "aiohttp"]


# --------------------------------------------------------------------------- #
# AC15 (R4): the vocabulary grows by appending
# --------------------------------------------------------------------------- #

#: The phase-7 vocabulary, written out: the order the appended kinds must keep.
PHASE7_EVENT_KINDS = (
    "message",
    "sub",
    "resub",
    "sub_gift",
    "community_sub_gift",
    "raid",
    "follow",
    "tip",
    "watch_tick",
    "cue",
)
PHASE8_EVENT_KINDS = ("reward", "manual", "speech", "timer")


def test_ac15_the_four_kinds_are_appended_after_the_phase7_ten() -> None:
    """AC15 (R4): the first ten kinds are the phase-7 tuple, the last four
    are ``reward``, ``manual``, ``speech``, ``timer``, and the seven notice
    kinds are unchanged."""

    assert EVENT_KINDS[:10] == PHASE7_EVENT_KINDS
    assert EVENT_KINDS[10:] == PHASE8_EVENT_KINDS
    assert len(EVENT_KINDS) == 14
    assert len(PLATFORM_NOTICE_KINDS) == 7
    assert not set(PHASE8_EVENT_KINDS) & set(PLATFORM_NOTICE_KINDS)
    assert not set(PHASE8_EVENT_KINDS) & set(CHAT_FAMILY_KINDS)


@pytest.mark.parametrize("kind", PHASE8_EVENT_KINDS)
def test_ac15_an_appended_kind_belongs_to_no_input_family(kind: str) -> None:
    """AC15 (R4, A1): a ``channel.chat.message`` of each new kind belongs to
    no family, while the same event as a ``message`` is ``chat``."""

    payload = {"platform": "twitch", "kind": kind, "text": "hello"}
    event = {"type": "channel.chat.message", "payload": payload}
    assert validate_event_kind(kind, "payload.kind") == kind
    assert input_family_of_event(event) is None
    assert input_family_of_event({**event, "payload": {**payload, "kind": "message"}}) == (
        "chat",
        "twitch",
    )


# --------------------------------------------------------------------------- #
# AC16 (R4): the route half
# --------------------------------------------------------------------------- #


async def test_ac16_a_brain_route_matching_manual_validates_at_startup(tmp_path: Path) -> None:
    """AC16 (R4): the presence profile with a brain route
    ``match.kinds: [manual]`` passes the startup check — the manifest schema
    and the brain's settings hook both accept the new kind — and the same
    route naming an unknown kind is still refused by its field."""

    path = ROOT / PRESENCE
    environ = presence_environ(tmp_path)

    def overlay(kinds: list[str]) -> dict[str, Any]:
        route = {"name": "manual", "match": {"kinds": kinds}}
        return {"modules": {"brain": {"routes": [route]}}}

    accepted: list[str] = []
    status = await application.check_config(
        path,
        environ=environ,
        diagnostic_reporter=accepted.append,
        overlay_document=overlay(["manual"]),
    )
    assert (status, accepted) == (0, [])

    refused: list[str] = []
    status = await application.check_config(
        path,
        environ=environ,
        diagnostic_reporter=refused.append,
        overlay_document=overlay(["hug"]),
    )
    assert status == 2
    assert len(refused) == 1 and "routes[0].match.kinds" in refused[0], refused
