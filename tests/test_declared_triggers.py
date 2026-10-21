"""Declared trigger types (phase 8).

P2 holds AC15 and the route half of AC16 (R4): the four kinds appended to
the vocabulary, and a brain route naming one of them.

P3 holds the construction-level half of AC2 and AC8's pattern length (R1):
the shapes of a declared trigger type and their structural validation, each
refusal naming its sub-path and echoing no configured value.

P4 holds AC1, AC2 and the load half of AC3 (R1): the loader parses the new
manifest keys, and refuses a malformed declared type at discovery — before any
transport is opened — with a diagnostic at ``triggers.<input>.types[i]`` and
the sub-path, echoing no configured value.

P5 holds the policy half of AC1 and AC3, and AC6 (R1, R2 scoping): the
registry accepts a well-formed declared type, validates a declared rule's
parameters against the declared schema and the kernel's checks, and refuses
an override naming a declared type for an input that did not declare it.

P6 holds AC4, AC5, AC7, AC8, AC35 and AC36 (R2): the engine evaluates a
declared rule's frozen ``match`` tree over the normalised payload, the
``matches`` wildcard is segment-based with a counted, linear step bound, a
declared probability parameter draws exactly once per evaluation, and each
such draw is recorded in the decision's ``draws``. AC10 is the unmodified
``tests/test_triggers.py``.

P7 holds AC9, AC11, AC12, AC13, AC14 and AC34 (R3, R2 admission): a hooked
type's hook is supplied only by its declaring module, through its own context,
awaited by ``evaluate_async`` at most once per eligible evaluation under its
bound on the engine's clock, and any failed outcome vetoes the decision before
the combination; an accepted declared decision still meets admission and the
authorization view unchanged.

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
import gc
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

import yaml

import core.main as application
from core.actions import ActionRegistry, AuthorizationPolicy
from core.admission import REASON_GLOBAL_PENDING_CAP, AdmissionScheduler, Work
from core.loader import ModuleLoader, ModuleLoadError
from core.runtime import REASON_INPUT_NOT_OWNED
from core.contracts import (
    CHAT_FAMILY_KINDS,
    EVENT_KINDS,
    GENERIC_TRIGGER_TYPE_NAMES,
    PLATFORM_NOTICE_KINDS,
    TRACE_BRAIN_ADMISSION_ACCEPTED,
    TRACE_BRAIN_ADMISSION_REJECTED,
    TRACE_BRAIN_RUN_COMPLETED,
    TRACE_INPUT_TRIGGER_ACCEPTED,
    TRACE_INPUT_TRIGGER_REJECTED,
    ContractError,
    TriggerPolicy,
    TriggerRule,
    TriggerSpec,
    TriggerTypeDeclaration,
    WILDCARD,
    ActionSpec,
    Destination,
    SessionKey,
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
from core.triggers import (
    BUILTIN_TRIGGER_TYPE_NAMES,
    BUILTIN_TRIGGER_TYPES,
    COUNT_WILDCARD_STEPS,
    DRAW_ENTRY_KEYS,
    REASON_DECISION_PENDING,
    TriggerDecision,
    TriggerEngine,
    TriggerRegistry,
    _wildcard_match,
    decision_trace_payload,
)
from modules.twitch import EVENTSUB_SUBSCRIPTIONS_URL
from test_examples import ROOT, DeviceEdges, _activate_example, _sample_environ
from test_presence_pack import PresenceTwitchSession, presence_environ
from test_integration import FakeWebSocket, welcome
from test_stream_control import ScenePeer
from test_twitch import chat_notice, follow_notice
from test_loader import runtime_context

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


# --------------------------------------------------------------------------- #
# P3 (R1): declared-type contract shapes; AC2 structural rows, AC8 length
# --------------------------------------------------------------------------- #

BADGE_SCHEMA = {
    "type": "object",
    "properties": {"tiers": {"type": "array", "items": {"type": "string"}}},
    "required": ["tiers"],
    "additionalProperties": False,
}
BADGE_MATCH = {"in": [{"field": "badge.tier"}, {"param": "tiers"}]}

INTERVAL_SCHEMA = {
    "type": "object",
    "properties": {
        "every_minutes": {"type": "integer", "minimum": 1, "maximum": 1440},
        "probability": {"type": "number", "minimum": 0, "maximum": 1},
        "window": {
            "type": "object",
            "properties": {
                "start_minute": {"type": "integer", "minimum": 0, "maximum": 1439},
                "end_minute": {"type": "integer", "minimum": 0, "maximum": 1439},
            },
            "required": ["start_minute", "end_minute"],
        },
    },
    "required": ["every_minutes", "probability"],
}


def _interval_match(end_path: str = "window.end_minute") -> dict[str, Any]:
    return {
        "all": [
            {"gte": [{"field": "timer.minute_of_day"}, {"param": "window.start_minute"}]},
            {"lte": [{"field": "timer.minute_of_day"}, {"param": end_path}]},
        ]
    }


def _badge(**overrides: Any) -> TriggerTypeDeclaration:
    arguments: dict[str, Any] = {
        "name": "badge_tier",
        "parameter_schema": BADGE_SCHEMA,
        "kinds": ("message",),
        "match": BADGE_MATCH,
    }
    arguments.update(overrides)
    return TriggerTypeDeclaration(**arguments)


def _badge_spec(
    declaration: TriggerTypeDeclaration, fields: Mapping[str, str] | None = None
) -> TriggerSpec:
    return TriggerSpec(
        types=(declaration,),
        combinations=("all_of",),
        default_policy=TriggerPolicy(
            rules=(TriggerRule(declaration.name, {"tiers": ["gold"]}),)
        ),
        fields={"badge.tier": "string"} if fields is None else fields,
    )


def _refused(build: Any, field: str, *configured: str) -> ContractError:
    """Build, expect a refusal at exactly *field*, and check that no
    *configured* string reaches the diagnostic."""

    with pytest.raises(ContractError) as caught:
        build()
    assert caught.value.field == field, caught.value
    for value in configured:
        assert value not in str(caught.value), (value, str(caught.value))
    return caught.value


def _nested_not(depth: int) -> dict[str, Any]:
    tree: dict[str, Any] = {"equals": [{"field": "text"}, "deep_sentinel"]}
    for _ in range(depth - 1):
        tree = {"not": tree}
    return tree


def test_p3_the_ac1_badge_tier_declaration_constructs_and_its_spec_validates() -> None:
    """AC1 (R1), construction level: the ``badge_tier`` declaration with a
    ``kinds``, a schema with required ``tiers`` and an ``in`` rule over the
    declared field ``badge.tier`` constructs, and its policy validates."""

    declaration = _badge()
    assert declaration.declared
    assert declaration.kinds == ("message",)
    spec = _badge_spec(declaration)
    spec.validate_policy(
        TriggerPolicy(rules=(TriggerRule("badge_tier", {"tiers": ["gold"]}),))
    )
    assert dict(spec.fields) == {"badge.tier": "string"}


def test_p3_generic_declarations_construct_unchanged_and_are_not_declared() -> None:
    """R1: the four built-in types keep their shape and none counts as
    declared; the contract's generic names are exactly the engine's."""

    assert set(BUILTIN_TRIGGER_TYPE_NAMES) == GENERIC_TRIGGER_TYPE_NAMES
    for declaration in BUILTIN_TRIGGER_TYPES:
        assert not declaration.declared
        assert declaration.kinds == ()
        assert declaration.match is None
        assert declaration.probability_parameter is None
        assert declaration.hook is False
        assert declaration.hook_timeout_seconds is None
    plain = TriggerTypeDeclaration(name="keyword", parameter_schema={"type": "object"})
    assert not plain.declared


def test_p3_a_hooked_declaration_counts_as_declared() -> None:
    """R1, R3: ``hook: true`` with a bound in (0, 10] is a declared type."""

    hooked = _badge(match=None, hook=True, hook_timeout_seconds=0.5)
    assert hooked.declared and hooked.match is None
    assert _badge(match=None, hook=True, hook_timeout_seconds=10).declared


@pytest.mark.parametrize(
    ("overrides", "field", "configured"),
    [
        pytest.param({"name": "keyword"}, "name", (), id="generic-name"),
        pytest.param({"name": "b" * 33}, "name", ("b" * 33,), id="33-characters"),
        pytest.param({"name": "Badge-Tier"}, "name", ("Badge-Tier",), id="pattern"),
        pytest.param({"kinds": ()}, "kinds", (), id="empty-kinds"),
        pytest.param({"kinds": ("banana",)}, "kinds[0]", ("banana",), id="unknown-kind"),
        pytest.param(
            {"hook": True, "hook_timeout_seconds": 1}, "hook", (), id="match-and-hook"
        ),
        pytest.param(
            {
                "match": {
                    "all": [
                        {"equals": [{"field": "text"}, "hello_sentinel"]},
                        {"regex": [{"field": "text"}, "re_sentinel"]},
                    ]
                }
            },
            "match.all[1]",
            ("regex", "re_sentinel", "hello_sentinel"),
            id="operator-regex",
        ),
        pytest.param(
            {"match": {"in": [{"field": "badge.tier"}, {"param": "ranks_sentinel"}]}},
            "match.in[1].param",
            ("ranks_sentinel",),
            id="unknown-param",
        ),
        pytest.param({"match": _nested_not(9)}, "match" + ".not" * 8, ("deep_sentinel",), id="depth-9"),
    ],
)
def test_p3_ac2_structural_rows_are_refused_by_sub_path(
    overrides: dict[str, Any], field: str, configured: tuple[str, ...]
) -> None:
    """AC2 (R1), construction level: eight of the nine fixture rows are
    refused by the declaration itself, the error naming the sub-path and
    echoing no configured string (the ninth, a ``field`` path the input does
    not emit, is the spec's — see the next test)."""

    _refused(lambda: _badge(**overrides), f"TriggerTypeDeclaration.{field}", *configured)


def test_p3_ac2_a_field_absent_from_canonical_and_declared_fields_is_refused() -> None:
    """AC2 (R1), A4: ``secret.token`` is neither canonical nor declared in
    ``fields``, so the spec refuses it at the operand's sub-path; the same
    tree over a declared field is accepted."""

    tree = {
        "all": [
            {"equals": [{"field": "text"}, "hello"]},
            {"in": [{"field": "secret.token"}, {"param": "tiers"}]},
        ]
    }
    declaration = _badge(match=tree)
    _refused(
        lambda: _badge_spec(declaration),
        "TriggerSpec.types[0].match.all[1].in[0].field",
        "secret.token",
    )
    assert _badge_spec(declaration, {"secret.token": "string"}).declares("badge_tier")
    # A canonical field needs no declaration.
    _badge_spec(_badge(match={"equals": [{"field": "author.display_name"}, "x"]}), {})


def test_p3_ac2_depth_8_constructs_and_depth_9_is_refused() -> None:
    """AC2 (R1): the depth bound is 8, inclusive."""

    assert _badge(match=_nested_not(8)).declared
    _refused(lambda: _badge(match=_nested_not(9)), "TriggerTypeDeclaration.match" + ".not" * 8)


def test_p3_a_literal_list_holds_at_most_64_scalars() -> None:
    """R1: 64 literal scalars construct, 65 are refused at the operand."""

    items = [f"tier_{index}" for index in range(65)]
    assert _badge(match={"in": [{"field": "badge.tier"}, items[:64]]}).declared
    _refused(
        lambda: _badge(match={"in": [{"field": "badge.tier"}, items]}),
        "TriggerTypeDeclaration.match.in[1]",
        "tier_64",
        "tier_0",
    )


def test_p3_a_tree_holds_at_most_64_nodes() -> None:
    """R1: ``any`` over 63 comparisons is 64 nodes and constructs; over 64
    it is 65 and is refused, at the tree, before visiting the children."""

    def tree(width: int) -> dict[str, Any]:
        return {"any": [{"equals": [{"field": "text"}, "node_sentinel"]}] * width}

    assert _badge(match=tree(63)).declared
    _refused(lambda: _badge(match=tree(64)), "TriggerTypeDeclaration.match", "node_sentinel")
    # A tree far past the bound is refused the same way, without walking it.
    _refused(lambda: _badge(match=tree(1_000_000)), "TriggerTypeDeclaration.match")


def test_p3_a_self_referencing_tree_is_refused_by_its_bounds() -> None:
    """R1 risk: a cyclic tree (a YAML anchor loop) ends the bounded walk."""

    children: list[Any] = []
    cyclic = {"all": children}
    children.append(cyclic)
    with pytest.raises(ContractError) as caught:
        _badge(match=cyclic)
    assert caught.value.field.startswith("TriggerTypeDeclaration.match")


def test_p3_ac8_a_matches_pattern_holds_at_most_128_characters() -> None:
    """AC8 (R2, A2), load half: a 128-character pattern constructs, a
    141-character one is refused at the pattern without echoing it."""

    assert _badge(match={"matches": [{"field": "text"}, "*" * 128]}).declared
    pattern = "hi*bot?" * 20 + "x"
    assert len(pattern) == 141
    _refused(
        lambda: _badge(match={"matches": [{"field": "text"}, pattern]}),
        "TriggerTypeDeclaration.match.matches[1]",
        pattern,
        "hi*bot?",
    )


def test_p3_ac30_a_param_path_resolves_through_object_properties_only() -> None:
    """AC30 (R8)'s load refusal at construction level: the ``interval``
    shape over ``window.start_minute``/``window.end_minute`` constructs, and a
    ``{param: window.nope}`` operand is refused naming its path."""

    interval = TriggerTypeDeclaration(
        name="interval",
        parameter_schema=INTERVAL_SCHEMA,
        kinds=("timer",),
        match=_interval_match(),
        probability_parameter="probability",
    )
    assert interval.declared and interval.probability_parameter == "probability"
    TriggerSpec(
        types=(interval,),
        combinations=("all_of",),
        default_policy=TriggerPolicy(
            rules=(TriggerRule("interval", {"every_minutes": 30, "probability": 0.25}),)
        ),
        fields={"timer.minute_of_day": "integer"},
    )
    _refused(
        lambda: TriggerTypeDeclaration(
            name="interval",
            parameter_schema=INTERVAL_SCHEMA,
            kinds=("timer",),
            match=_interval_match("window.nope"),
        ),
        "TriggerTypeDeclaration.match.all[1].lte[1].param",
        "nope",
    )
    # A path through a non-object property does not resolve either.
    _refused(
        lambda: TriggerTypeDeclaration(
            name="interval",
            parameter_schema=INTERVAL_SCHEMA,
            kinds=("timer",),
            match={"gte": [{"field": "timer.minute_of_day"}, {"param": "probability.x"}]},
        ),
        "TriggerTypeDeclaration.match.gte[1].param",
    )


def test_p3_a_non_required_parameter_is_refused_under_not() -> None:
    """R1: a parameter under a non-required object may not appear under
    ``not``; a required one may, and the same optional one outside ``not``
    is accepted."""

    def build(tree: dict[str, Any]) -> TriggerTypeDeclaration:
        return TriggerTypeDeclaration(
            name="interval", parameter_schema=INTERVAL_SCHEMA, kinds=("timer",), match=tree
        )

    optional = {"gte": [{"field": "timer.minute_of_day"}, {"param": "window.start_minute"}]}
    required = {"gte": [{"field": "timer.minute_of_day"}, {"param": "every_minutes"}]}
    assert build(optional).declared
    assert build({"not": required}).declared
    _refused(
        lambda: build({"any": [required, {"not": optional}]}),
        "TriggerTypeDeclaration.match.any[1].not.gte[1].param",
        "start_minute",
    )


@pytest.mark.parametrize(
    "schema",
    [
        pytest.param({**INTERVAL_SCHEMA, "required": ["every_minutes"]}, id="not-required"),
        pytest.param(
            {
                **INTERVAL_SCHEMA,
                "properties": {
                    **INTERVAL_SCHEMA["properties"],
                    "probability": {"type": "number", "minimum": 0, "maximum": 2},
                },
            },
            id="unbounded",
        ),
    ],
)
def test_p3_the_probability_parameter_is_required_and_bounded(schema: dict[str, Any]) -> None:
    """AC3 (R1, A3), construction level: a probability parameter the schema
    does not list as required, or whose number is not bounded within [0, 1],
    is refused at ``probability_parameter`` without echoing its name."""

    _refused(
        lambda: TriggerTypeDeclaration(
            name="interval",
            parameter_schema=schema,
            kinds=("timer",),
            match=_interval_match(),
            probability_parameter="probability",
        ),
        "TriggerTypeDeclaration.probability_parameter",
        "'probability'",
    )


@pytest.mark.parametrize("timeout", [0, 10.5, -1, float("nan"), True, None])
def test_p3_ac12_a_hook_bound_outside_0_to_10_is_refused(timeout: Any) -> None:
    """AC12 (R3), construction level: ``hook_timeout_seconds`` must be a
    number in (0, 10] for a hooked type; 0 and 10.5 are refused."""

    _refused(
        lambda: _badge(match=None, hook=True, hook_timeout_seconds=timeout),
        "TriggerTypeDeclaration.hook_timeout_seconds",
    )


def test_p3_a_hook_bound_without_a_hook_is_refused() -> None:
    """R3: the bound is declared iff ``hook`` is true."""

    _refused(
        lambda: _badge(hook_timeout_seconds=1), "TriggerTypeDeclaration.hook_timeout_seconds"
    )


def test_p3_ac3_neither_match_nor_hook_is_refused_at_name() -> None:
    """AC3 (R1), construction level: kinds without a match or a hook."""

    _refused(lambda: _badge(match=None), "TriggerTypeDeclaration.name")


def test_p3_a_generic_declaration_cannot_carry_declared_only_keys() -> None:
    """R1 risk: a generic name with extra declared keys is refused, so it can
    never reach a declared evaluator."""

    _refused(
        lambda: TriggerTypeDeclaration(
            name="probability",
            parameter_schema={"type": "object"},
            probability_parameter="probability",
        ),
        "TriggerTypeDeclaration.probability_parameter",
    )
    _refused(lambda: _badge(name="probability"), "TriggerTypeDeclaration.name")


def test_p3_the_match_tree_and_fields_are_frozen() -> None:
    """R1: the match tree and the field catalog cannot change after load."""

    tree = {"in": [{"field": "badge.tier"}, ["gold"]]}
    declaration = _badge(match=tree)
    tree["in"][1].append("silver")
    assert declaration.match["in"][1] == ("gold",)
    with pytest.raises(TypeError):
        declaration.match["all"] = []  # type: ignore[index]
    fields = {"badge.tier": "string"}
    spec = _badge_spec(declaration, fields)
    fields["badge.tier"] = "integer"
    assert spec.fields["badge.tier"] == "string"
    with pytest.raises(TypeError):
        spec.fields["x"] = "string"  # type: ignore[index]


@pytest.mark.parametrize(
    ("fields", "configured"),
    [
        pytest.param({"badge.tier": "object"}, "badge.tier", id="non-scalar-type"),
        pytest.param({"badge..tier": "string"}, "badge..tier", id="empty-segment"),
        pytest.param({"text": "integer"}, "text", id="canonical-redeclared"),
        pytest.param({"secret.token": {"x"}}, "secret.token", id="set-value"),
        pytest.param({"secret.token": {"k": "v"}}, "secret.token", id="mapping-value"),
        pytest.param({"secret.token": ["string"]}, "secret.token", id="list-value"),
    ],
)
def test_p3_a_field_catalog_entry_is_a_dotted_path_and_a_scalar_type(
    fields: dict[str, str], configured: str
) -> None:
    """A4: each ``fields`` entry is refused by its position, not its path."""

    error = _refused(lambda: _badge_spec(_badge(), fields), "TriggerSpec.fields[0]")
    assert repr(configured) not in str(error)


def test_p3_a_cyclic_field_catalog_value_is_refused_by_position() -> None:
    """A4: a self-containing value is refused, not recursed into."""

    cyclic: list[object] = []
    cyclic.append(cyclic)
    error = _refused(
        lambda: _badge_spec(_badge(), {"secret.token": cyclic}), "TriggerSpec.fields[0]"
    )
    assert "secret.token" not in str(error)


@pytest.mark.parametrize(
    ("tree", "field"),
    [
        pytest.param({"in": [{"field": "badge.tier"}, "gold"]}, "match.in[1]", id="in-scalar"),
        pytest.param({"equals": [{"field": "text"}, ["a"]]}, "match.equals[1]", id="list"),
        pytest.param({"equals": [{"field": "text"}]}, "match.equals", id="arity"),
        pytest.param({"all": []}, "match.all", id="empty-all"),
        pytest.param({"equals": [{"value": "x"}, "y"]}, "match.equals[0]", id="operand"),
        pytest.param({"equals": [{"field": "text"}, None]}, "match.equals[1]", id="null"),
        pytest.param({"matches": [{"field": "text"}, 3]}, "match.matches[1]", id="pattern"),
        pytest.param({"equals": [{"field": "text"}, "x"], "any": []}, "match", id="two-ops"),
    ],
)
def test_p3_malformed_nodes_and_operands_are_refused_by_sub_path(
    tree: dict[str, Any], field: str
) -> None:
    """R1: the closed node set and its operand shapes."""

    _refused(lambda: _badge(match=tree), f"TriggerTypeDeclaration.{field}")


# --------------------------------------------------------------------------- #
# P4 (R1): the loader parses and diagnoses the new manifest keys; AC1, AC2, AC3
# --------------------------------------------------------------------------- #

DECLARED_FIXTURES = Path(__file__).parent / "fixtures" / "modules" / "declared"
INPUT = "badge"

#: A module that opens one transport the moment it is activated, so a refusal
#: at load is seen to open none.
COUNTING_SOURCE = """
class Handle:
    def __init__(self, transport):
        self.transport = transport

    async def start_inputs(self):
        return None

    async def close(self):
        self.transport.close()


async def activate(context, settings, catalog):
    transport = settings["transport"]
    transport.open()
    return Handle(transport)
"""


class CountingTransport:
    """The transport fake: counts every open and every close."""

    def __init__(self) -> None:
        self.opened = 0
        self.closed = 0

    def open(self) -> None:
        self.opened += 1

    def close(self) -> None:
        self.closed += 1


def _fixture_manifest() -> dict[str, Any]:
    with (DECLARED_FIXTURES / INPUT / "module.yaml").open(encoding="utf-8") as stream:
        return yaml.safe_load(stream)


def _with_type(**overrides: Any) -> dict[str, Any]:
    """The fixture manifest with its one declared type overridden; a ``None``
    override removes the key."""

    manifest = _fixture_manifest()
    declaration = manifest["triggers"]["types"][0]
    for key, value in overrides.items():
        if value is None:
            declaration.pop(key, None)
        else:
            declaration[key] = value
    return manifest


def _write_module(root: Path, manifest: Mapping[str, Any]) -> Path:
    directory = root / INPUT
    directory.mkdir()
    (directory / "module.yaml").write_text(yaml.safe_dump(dict(manifest)), encoding="utf-8")
    (directory / "__init__.py").write_text(COUNTING_SOURCE, encoding="utf-8")
    return directory


async def _load(root: Path, transport: CountingTransport) -> list[Any]:
    context = runtime_context()
    loader = ModuleLoader(context.bus, root, context=context, environ={})
    return await loader.activate_enabled(
        {"enabled_modules": [INPUT], "modules": {INPUT: {"transport": transport}}}
    )


async def _refused_at_load(
    tmp_path: Path, manifest: Mapping[str, Any], field: str, *configured: str
) -> str:
    """Load *manifest* enabled, expect a refusal at exactly *field* with 0
    transports opened, and check no *configured* string reaches it."""

    _write_module(tmp_path, manifest)
    transport = CountingTransport()
    with pytest.raises(ModuleLoadError) as caught:
        await _load(tmp_path, transport)
    diagnostic = str(caught.value)
    assert transport.opened == 0
    assert f"module {INPUT!r}: field {field!r}: " in diagnostic, diagnostic
    for value in configured:
        assert value not in diagnostic, (value, diagnostic)
    return diagnostic


async def test_p4_ac1_the_badge_tier_manifest_loads_with_its_declared_field() -> None:
    """AC1 (R1), load level: the fixture manifest declaring ``badge_tier`` and
    ``triggers.fields: {badge.tier: string}`` is parsed at discovery into the
    declared type, and the policy ``badge_tier{tiers: ["gold"]}`` validates
    against what was parsed."""

    context = runtime_context()
    loader = ModuleLoader(context.bus, DECLARED_FIXTURES, context=context, environ={})

    assert await loader.activate_enabled({"enabled_modules": [], "modules": {}}) == []

    assert set(loader.discovered) == {INPUT}
    spec = loader.discovered[INPUT].declaration.triggers
    assert spec is not None
    assert dict(spec.fields) == {"badge.tier": "string"}
    (declaration,) = spec.types
    assert declaration.name == "badge_tier" and declaration.declared
    assert declaration.kinds == ("message",)
    assert declaration.match == {"in": ({"field": "badge.tier"}, {"param": "tiers"})}
    assert declaration.hook is False and declaration.probability_parameter is None
    assert spec.default_policy.rules[0].type == "badge_tier"
    spec.validate_policy(
        TriggerPolicy(rules=(TriggerRule("badge_tier", {"tiers": ["gold"]}),))
    )


async def test_p4_the_counting_transport_counts_an_accepted_load(tmp_path: Path) -> None:
    """The control of AC2's count: the same module with a generic trigger
    type loads and opens exactly one transport, so the 0 of a refusal is the
    loader's doing, not a fake that never counts."""

    manifest = _fixture_manifest()
    manifest["triggers"] = {
        "types": [
            {
                "name": "keyword",
                "parameter_schema": {
                    "type": "object",
                    "properties": {"keywords": {"type": "array", "items": {"type": "string"}}},
                    "required": ["keywords"],
                },
            }
        ],
        "combinations": ["all_of"],
        "default_policy": {
            "rules": [{"type": "keyword", "parameters": {"keywords": ["hello"]}}]
        },
    }
    _write_module(tmp_path, manifest)
    transport = CountingTransport()

    activations = await _load(tmp_path, transport)

    assert [activation.name for activation in activations] == [INPUT]
    assert transport.opened == 1
    for activation in activations:
        await activation.close()


_DECLARED_AT = f"triggers.{INPUT}.types[0]"


@pytest.mark.parametrize(
    ("overrides", "sub_path", "configured"),
    [
        pytest.param({"name": "keyword"}, "name", (), id="generic-name"),
        pytest.param({"name": "q" * 33}, "name", ("q" * 33,), id="33-characters"),
        pytest.param({"kinds": []}, "kinds", (), id="empty-kinds"),
        pytest.param({"kinds": ["banana"]}, "kinds[0]", ("banana",), id="unknown-kind"),
        pytest.param(
            {"hook": True, "hook_timeout_seconds": 1}, "hook", (), id="match-and-hook"
        ),
        pytest.param(
            {
                "match": {
                    "all": [
                        {"equals": [{"field": "text"}, "hello_sentinel"]},
                        {"regex": [{"field": "text"}, "re_sentinel"]},
                    ]
                }
            },
            "match.all[1]",
            ("regex", "re_sentinel", "hello_sentinel"),
            id="operator-regex",
        ),
        pytest.param(
            {
                "match": {
                    "all": [
                        {"equals": [{"field": "text"}, "hello_sentinel"]},
                        {"in": [{"field": "secret.token"}, {"param": "tiers"}]},
                    ]
                }
            },
            "match.all[1].in[0].field",
            ("secret.token", "hello_sentinel"),
            id="undeclared-field",
        ),
        pytest.param(
            {"match": {"in": [{"field": "badge.tier"}, {"param": "ranks_sentinel"}]}},
            "match.in[1].param",
            ("ranks_sentinel",),
            id="unknown-param",
        ),
        pytest.param(
            {"match": _nested_not(9)}, "match" + ".not" * 8, ("deep_sentinel",), id="depth-9"
        ),
    ],
)
async def test_p4_ac2_each_fixture_declaration_is_refused_at_load(
    tmp_path: Path,
    overrides: dict[str, Any],
    sub_path: str,
    configured: tuple[str, ...],
) -> None:
    """AC2 (R1): each of the nine fixture declarations is refused at load,
    the module enabled, with 0 transports opened and a diagnostic naming
    ``triggers.<input>.types[i]`` and the offending sub-path — the field the
    input does not emit included, which only the spec can see — and echoing
    no configured string."""

    await _refused_at_load(
        tmp_path, _with_type(**overrides), f"{_DECLARED_AT}.{sub_path}", *configured
    )


async def test_p4_ac2_the_diagnostic_names_the_declaring_position(tmp_path: Path) -> None:
    """AC2 (R1): the index is the declared type's own position in
    ``triggers.types``, after a generic type the same input declares."""

    manifest = _with_type(kinds=["banana"])
    manifest["triggers"]["types"].insert(
        0,
        {
            "name": "probability",
            "parameter_schema": {
                "type": "object",
                "properties": {"probability": {"type": "number", "minimum": 0, "maximum": 1}},
                "required": ["probability"],
            },
        },
    )
    await _refused_at_load(tmp_path, manifest, f"triggers.{INPUT}.types[1].kinds[0]", "banana")


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"match": None}, id="kinds-without-match"),
        pytest.param({"match": None, "hook": False}, id="hook-false"),
        pytest.param({"match": None, "kinds": None, "hook": False}, id="hook-false-alone"),
    ],
)
async def test_p4_ac3_neither_match_nor_hook_is_refused_at_name(
    tmp_path: Path, overrides: dict[str, Any]
) -> None:
    """AC3 (R1), load half: a declared type with neither ``match`` nor a hook
    is refused at ``triggers.<input>.types[0].name``, echoing no name."""

    await _refused_at_load(tmp_path, _with_type(**overrides), f"{_DECLARED_AT}.name", "badge_tier")


async def test_p4_a_bare_non_generic_name_is_still_refused_at_its_name(tmp_path: Path) -> None:
    """R1: a non-generic name carrying no declared key at all is a generic
    declaration to the manifest parser, so a disabled module declaring one is
    still discovered; enabled, it is refused before any transport opens by
    the registry's existing refusal, at ``triggers.<input>.types[0].name``."""

    manifest = _with_type(match=None, kinds=None)
    _write_module(tmp_path, manifest)
    context = runtime_context()
    loader = ModuleLoader(context.bus, tmp_path, context=context, environ={})
    await loader.activate_enabled({"enabled_modules": [], "modules": {}})
    (declaration,) = loader.discovered[INPUT].declaration.triggers.types
    assert not declaration.declared

    transport = CountingTransport()
    with pytest.raises(ModuleLoadError) as caught:
        await _load(tmp_path, transport)
    assert transport.opened == 0
    assert f"{_DECLARED_AT}.name: is not a trigger type" in str(caught.value)


async def test_p4_ac3_a_non_required_probability_parameter_is_refused(tmp_path: Path) -> None:
    """AC3 (R1), load half: a ``probability_parameter`` the schema does not
    list as required is refused at its own key, echoing no configured name."""

    schema = {
        "type": "object",
        "properties": {
            "tiers": {"type": "array", "items": {"type": "string"}},
            "chance_sentinel": {"type": "number", "minimum": 0, "maximum": 1},
        },
        "required": ["tiers"],
    }
    await _refused_at_load(
        tmp_path,
        _with_type(parameter_schema=schema, probability_parameter="chance_sentinel"),
        f"{_DECLARED_AT}.probability_parameter",
        "chance_sentinel",
    )


async def test_p4_a_required_probability_parameter_loads(tmp_path: Path) -> None:
    """R1: the same declaration with the parameter required is accepted at
    discovery and keeps its probability parameter."""

    schema = {
        "type": "object",
        "properties": {
            "tiers": {"type": "array", "items": {"type": "string"}},
            "chance": {"type": "number", "minimum": 0, "maximum": 1},
        },
        "required": ["tiers", "chance"],
    }
    manifest = _with_type(parameter_schema=schema, probability_parameter="chance")
    manifest["triggers"]["default_policy"]["rules"][0]["parameters"]["chance"] = 0.5
    _write_module(tmp_path, manifest)
    context = runtime_context()
    loader = ModuleLoader(context.bus, tmp_path, context=context, environ={})

    await loader.activate_enabled({"enabled_modules": [], "modules": {}})

    (declaration,) = loader.discovered[INPUT].declaration.triggers.types
    assert declaration.probability_parameter == "chance"


async def test_p4_a_hooked_declaration_loads_with_its_bound(tmp_path: Path) -> None:
    """R1, R3: ``hook: true`` with ``hook_timeout_seconds`` parses from a
    manifest; the bound outside (0, 10] is refused at its key."""

    _write_module(tmp_path, _with_type(match=None, hook=True, hook_timeout_seconds=0.5))
    context = runtime_context()
    loader = ModuleLoader(context.bus, tmp_path, context=context, environ={})
    await loader.activate_enabled({"enabled_modules": [], "modules": {}})
    (declaration,) = loader.discovered[INPUT].declaration.triggers.types
    assert declaration.hook is True and declaration.hook_timeout_seconds == 0.5

    refused = tmp_path / "refused"
    refused.mkdir()
    await _refused_at_load(
        refused,
        _with_type(match=None, hook=True, hook_timeout_seconds=10.5),
        f"{_DECLARED_AT}.hook_timeout_seconds",
    )


@pytest.mark.parametrize(
    ("schema", "configured"),
    [
        pytest.param({"type": "secret_sentinel"}, ("secret_sentinel",), id="unknown-type"),
        pytest.param(
            {
                "type": "object",
                "properties": {"tiers_sentinel": {"type": "string"}},
                "required": ["tiers_sentinel", "tiers_sentinel"],
            },
            ("tiers_sentinel",),
            id="duplicated-required",
        ),
        pytest.param(
            {
                "type": "object",
                "properties": {
                    "tiers": {
                        "type": "string",
                        "enum": ["gold_sentinel"],
                        "default": "lead_sentinel",
                    }
                },
            },
            ("tiers", "gold_sentinel", "lead_sentinel"),
            id="default-outside-enum",
        ),
    ],
)
async def test_p4_a_malformed_parameter_schema_echoes_no_value(
    tmp_path: Path, schema: dict[str, Any], configured: tuple[str, ...]
) -> None:
    """R1: a declared type whose parameter schema is malformed is refused at
    ``triggers.<input>.types[i].parameter_schema``; the schema diagnostic,
    which can quote a declared type, name or default, reaches it as a fixed
    reason, echoing no configured value."""

    diagnostic = await _refused_at_load(
        tmp_path,
        _with_type(parameter_schema=schema),
        f"{_DECLARED_AT}.parameter_schema",
        *configured,
    )
    assert "is not a valid parameter schema" in diagnostic


async def test_p4_a_generic_type_keeps_its_existing_diagnostic(tmp_path: Path) -> None:
    """R1 risk: a generic declaration's refusal keeps the pre-existing
    ``triggers.types[i]`` wording, with no input segment."""

    manifest = _fixture_manifest()
    manifest["triggers"]["types"] = [
        {"name": "keyword", "parameter_schema": {"type": "banana"}}
    ]
    diagnostic = await _refused_at_load(tmp_path, manifest, "triggers.types[0]")
    assert f"triggers.{INPUT}.types" not in diagnostic


async def test_p4_a_malformed_field_catalog_is_refused_at_load(tmp_path: Path) -> None:
    """R1, A4: ``triggers.fields`` is parsed into the spec, so an entry that
    redeclares a canonical field or names no scalar type is refused at load
    by position, echoing neither its path nor its type."""

    manifest = _fixture_manifest()
    manifest["triggers"]["fields"] = {"badge.tier": "string", "rank.sentinel": "colour_sentinel"}
    diagnostic = await _refused_at_load(
        tmp_path, manifest, "triggers", "rank.sentinel", "colour_sentinel"
    )
    assert "TriggerSpec.fields[1]" in diagnostic


# --------------------------------------------------------------------------- #
# P5 (R1, R2 scoping): the registry accepts declared types; AC1, AC3, AC6
# --------------------------------------------------------------------------- #

#: A second fixture input, declaring only the generic ``keyword`` type.
SECOND_INPUT = "second"


def _gold() -> TriggerPolicy:
    return TriggerPolicy(rules=(TriggerRule("badge_tier", {"tiers": ["gold"]}),))


def _badge_registry(declaration: TriggerTypeDeclaration | None = None) -> TriggerRegistry:
    registry = TriggerRegistry(companion_name="companion")
    registry.register(INPUT, _badge_spec(_badge() if declaration is None else declaration))
    return registry


def _second_spec(*types: TriggerTypeDeclaration) -> TriggerSpec:
    keyword = next(d for d in BUILTIN_TRIGGER_TYPES if d.name == "keyword")
    return TriggerSpec(
        types=(keyword, *types),
        combinations=("all_of",),
        default_policy=TriggerPolicy(
            rules=(TriggerRule("keyword", {"keywords": ["hello"]}),)
        ),
    )


def _second_message(message_id: str, text: str = "hello") -> dict[str, Any]:
    return {
        "type": "channel.chat.message",
        "payload": {
            "platform": "twitch",
            "kind": "message",
            "channel_id": "channel-1",
            "author": {"id": "viewer-1", "display_name": "Viewer"},
            "message_id": message_id,
            "text": text,
            "badge": {"tier": "gold"},
        },
        "metadata": {"schema_version": 2, "source": SECOND_INPUT},
    }


def _registry_refused(build: Any, field: str, *configured: str) -> ContractError:
    """Like :func:`_refused`, but the field is checked as the diagnostic's
    prefix the way the loader quotes it."""

    error = _refused(build, field, *configured)
    assert str(error).startswith(f"{field}: "), str(error)
    return error


def test_p5_ac1_the_registry_accepts_the_badge_tier_declaration() -> None:
    """AC1 (R1), policy half: the declared ``badge_tier`` type registers — the
    "is not a trigger type this runtime can evaluate" refusal no longer
    applies to it — with its default policy, and the configured policy
    ``badge_tier{tiers: ["gold"]}`` validates and is selected."""

    registry = _badge_registry()

    assert registry.inputs() == (INPUT,)
    registry.validate_policy(INPUT, _gold())
    registry.configure(INPUT, _gold())
    assert registry.resolve(INPUT) == _gold()


async def test_p5_ac1_the_badge_fixture_registers_when_enabled(tmp_path: Path) -> None:
    """AC1 (R1): the fixture manifest, enabled, passes registration and opens
    its one transport; the engine's registry then holds the declared type and
    validates the configured ``badge_tier{tiers: ["gold"]}``."""

    _write_module(tmp_path, _fixture_manifest())
    transport = CountingTransport()
    context = runtime_context()
    loader = ModuleLoader(context.bus, tmp_path, context=context, environ={})

    activations = await loader.activate_enabled(
        {"enabled_modules": [INPUT], "modules": {INPUT: {"transport": transport}}}
    )

    assert [activation.name for activation in activations] == [INPUT]
    assert transport.opened == 1
    registry = context.triggers.registry
    (declaration,) = registry.spec(INPUT).types
    assert declaration.name == "badge_tier" and declaration.declared
    registry.validate_policy(INPUT, _gold())
    for activation in activations:
        await activation.close()


def test_p5_a_non_generic_undeclared_name_keeps_the_existing_refusal() -> None:
    """R1: a non-generic name that carries no match rule and no hook is not a
    declared type, and the registry refuses it as before."""

    bare = TriggerTypeDeclaration(name="badge_tier", parameter_schema=BADGE_SCHEMA)
    assert not bare.declared
    spec = TriggerSpec(types=(bare,), combinations=("all_of",), default_policy=_gold())

    error = _registry_refused(
        lambda: TriggerRegistry().register(INPUT, spec), f"triggers.{INPUT}.types[0].name"
    )
    assert "is not a trigger type this runtime can evaluate" in str(error)


@pytest.mark.parametrize("policy", [None, {}], ids=["none", "mapping"])
def test_p5_a_policy_that_is_not_a_trigger_policy_keeps_its_contract_error(
    policy: Any,
) -> None:
    """A *policy* that is not a :class:`TriggerPolicy` is refused with the
    spec's field-specific ContractError — the value-stripping step never
    reads ``rules`` from it."""

    registry = _badge_registry()

    with pytest.raises(ContractError) as caught:
        registry.validate_policy(INPUT, policy)
    assert caught.value.field == f"triggers.{INPUT}.policy"
    assert "must be a TriggerPolicy" in caught.value.reason


@pytest.mark.parametrize(
    "schema",
    [
        pytest.param(BADGE_SCHEMA, id="closed-schema"),
        pytest.param(
            {key: value for key, value in BADGE_SCHEMA.items() if key != "additionalProperties"},
            id="open-schema",
        ),
    ],
)
def test_p5_ac3_a_parameter_outside_the_declared_schema_is_refused(
    schema: dict[str, Any],
) -> None:
    """AC3 (R1), policy half: a configured parameter outside the declared
    schema is refused naming
    ``triggers.<input>.policy.rules[0].parameters.<key>``, echoing no
    configured value — whether or not the schema itself closes its
    properties, since a declared type only narrows."""

    registry = _badge_registry(_badge(parameter_schema=schema))
    policy = TriggerPolicy(
        rules=(
            TriggerRule(
                "badge_tier", {"tiers": ["gold_sentinel"], "rank": "value_sentinel"}
            ),
        )
    )

    _registry_refused(
        lambda: registry.validate_policy(INPUT, policy),
        f"triggers.{INPUT}.policy.rules[0].parameters.rank",
        "gold_sentinel",
        "value_sentinel",
    )
    assert registry.resolve(INPUT) == _badge_spec(_badge()).default_policy


def test_p5_ac3_a_parameter_of_the_wrong_type_echoes_no_value() -> None:
    """AC3 (R1): a declared parameter failing its declared schema is refused
    at its own path, echoing no configured value."""

    registry = _badge_registry()
    policy = TriggerPolicy(rules=(TriggerRule("badge_tier", {"tiers": "gold_sentinel"}),))

    _registry_refused(
        lambda: registry.configure(INPUT, policy),
        f"triggers.{INPUT}.policy.rules[0].parameters.tiers",
        "gold_sentinel",
    )


def test_p5_a_value_outside_a_declared_enumeration_echoes_no_value() -> None:
    """AC3 (R1): the schema validator quotes an offending value; for a
    declared rule the diagnostic keeps its path and wording, not the value."""

    schema = {
        "type": "object",
        "properties": {
            "tiers": {"type": "array", "items": {"type": "string", "enum": ["gold", "silver"]}}
        },
        "required": ["tiers"],
    }
    registry = _badge_registry(_badge(parameter_schema=schema))
    policy = TriggerPolicy(
        rules=(TriggerRule("badge_tier", {"tiers": ["gold", "bronze_sentinel"]}),)
    )

    error = _registry_refused(
        lambda: registry.validate_policy(INPUT, policy),
        f"triggers.{INPUT}.policy.rules[0].parameters.tiers[1]",
        "bronze_sentinel",
    )
    assert "must be one of" in error.reason


def test_p5_a_nested_parameter_outside_its_object_is_refused() -> None:
    """R1: the kernel's key check descends into object parameters, so a key
    the declared object does not list is refused at its full path."""

    declaration = TriggerTypeDeclaration(
        name="interval",
        parameter_schema=INTERVAL_SCHEMA,
        kinds=("timer",),
        match=_interval_match(),
        probability_parameter="probability",
    )
    window = {"start_minute": 600, "end_minute": 700}
    accepted = {"every_minutes": 5, "probability": 0.25, "window": window}
    spec = TriggerSpec(
        types=(declaration,),
        combinations=("all_of",),
        default_policy=TriggerPolicy(rules=(TriggerRule("interval", accepted),)),
        fields={"timer.minute_of_day": "integer"},
    )
    registry = TriggerRegistry()
    registry.register(INPUT, spec)
    registry.validate_policy(INPUT, TriggerPolicy(rules=(TriggerRule("interval", accepted),)))

    nested = {**accepted, "window": {**window, "lunch_sentinel": 4242}}
    _registry_refused(
        lambda: registry.validate_policy(
            INPUT, TriggerPolicy(rules=(TriggerRule("interval", nested),))
        ),
        f"triggers.{INPUT}.policy.rules[0].parameters.window.lunch_sentinel",
        "4242",
    )


@pytest.mark.parametrize("probability", [1.5, -0.25, True])
def test_p5_a_probability_parameter_outside_0_to_1_is_refused(probability: Any) -> None:
    """R1: the probability parameter's configured value is a number in
    [0, 1], refused at its path otherwise, echoing no value."""

    schema = {
        "type": "object",
        "properties": {
            "tiers": {"type": "array", "items": {"type": "string"}},
            "chance": {"type": "number", "minimum": 0, "maximum": 1},
        },
        "required": ["tiers", "chance"],
    }
    half = TriggerPolicy(
        rules=(TriggerRule("badge_tier", {"tiers": ["gold"], "chance": 0.5}),)
    )
    registry = TriggerRegistry()
    registry.register(
        INPUT,
        TriggerSpec(
            types=(_badge(parameter_schema=schema, probability_parameter="chance"),),
            combinations=("all_of",),
            default_policy=half,
            fields={"badge.tier": "string"},
        ),
    )
    registry.validate_policy(INPUT, half)
    policy = TriggerPolicy(
        rules=(TriggerRule("badge_tier", {"tiers": ["gold"], "chance": probability}),)
    )

    _registry_refused(
        lambda: registry.validate_policy(INPUT, policy),
        f"triggers.{INPUT}.policy.rules[0].parameters.chance",
        str(probability),
    )


def test_p5_a_configured_declared_policy_replaces_the_default_without_merge() -> None:
    """R1 risk: policy resolution is untouched — a configured declared policy
    replaces the module default entirely, nothing merged into it."""

    registry = _badge_registry()
    silver = TriggerPolicy(rules=(TriggerRule("badge_tier", {"tiers": ["silver"]}),))

    assert registry.resolve(INPUT) == _badge_spec(_badge()).default_policy
    registry.configure(INPUT, silver)

    resolved = registry.resolve(INPUT)
    assert resolved == silver
    assert [dict(rule.parameters) for rule in resolved.rules] == [{"tiers": ("silver",)}]


@pytest.mark.parametrize(
    "second_types",
    [
        pytest.param((), id="generic-only"),
        pytest.param(
            (_badge(name="rank_tier", match={"in": [{"field": "badge.tier"}, {"param": "tiers"}]}),),
            id="own-declared-type",
        ),
    ],
)
def test_p5_ac6_an_override_naming_another_inputs_type_is_refused(
    second_types: tuple[TriggerTypeDeclaration, ...],
) -> None:
    """AC6 (R2): a per-call override naming ``badge_tier`` on an event whose
    ``metadata.source`` is a second input that does not declare it raises a
    ContractError naming that input, before any rule runs: the declaring
    input's rule is evaluated 0 times, no value is drawn and nothing is
    recorded."""

    registry = _badge_registry()
    registry.register(
        SECOND_INPUT,
        TriggerSpec(
            types=_second_spec().types + second_types,
            combinations=("all_of",),
            default_policy=_second_spec().default_policy,
            fields={"badge.tier": "string"} if second_types else {},
        ),
    )
    rng = RecordedRandom((0.5,))
    engine = TriggerEngine(
        registry, dedup_max_entries=8, dedup_ttl_seconds=60.0, clock=ManualClock(), rng=rng
    )
    control = engine.evaluate(_second_message("m0"))
    assert control.accepted and control.input_name == SECOND_INPUT
    evaluations, window = engine.rule_evaluations, engine.window_size

    with pytest.raises(ContractError) as caught:
        engine.evaluate(_second_message("m1"), policy=_gold())

    assert caught.value.field == f"triggers.{SECOND_INPUT}.policy.rules[0].type"
    assert f"triggers.{INPUT}" not in str(caught.value)
    assert engine.rule_evaluations - evaluations == 0
    assert engine.window_size == window
    assert rng.ordinal == 0


def test_p5_the_registry_keeps_a_hook_slot_per_hooked_type() -> None:
    """R1, R3 groundwork: per input, the registry keeps one slot for each
    hooked type it declared, unfilled until a hook is supplied; a match type
    has none, and the table is read-only."""

    hooked = _badge(name="wake", match=None, hook=True, hook_timeout_seconds=0.5)
    spec = TriggerSpec(
        types=(_badge(), hooked),
        combinations=("all_of",),
        default_policy=_gold(),
        fields={"badge.tier": "string"},
    )
    registry = TriggerRegistry()
    registry.register(INPUT, spec)
    registry.register(SECOND_INPUT, _second_spec())

    assert dict(registry.hook_slots(INPUT)) == {"wake": None}
    assert dict(registry.hook_slots(SECOND_INPUT)) == {}
    assert dict(registry.hook_slots("nobody")) == {}
    with pytest.raises(TypeError):
        registry.hook_slots(INPUT)["wake"] = object()  # type: ignore[index]


# --------------------------------------------------------------------------- #
# P6 (R2): declared-rule evaluation, the wildcard matcher, probability draws
# and ``draws``; AC4, AC5, AC7, AC8, AC35, AC36 (AC10: test_triggers.py runs
# unmodified)
# --------------------------------------------------------------------------- #

#: The schema of a declared probability type: ``p`` is the probability, and
#: ``label`` a second parameter no ``draws`` entry may ever carry.
LUCKY_SCHEMA = {
    "type": "object",
    "properties": {
        "p": {"type": "number", "minimum": 0, "maximum": 1},
        "label": {"type": "string"},
    },
    "required": ["p"],
    "additionalProperties": False,
}
LUCKY_MATCH = {"equals": [{"field": "badge.tier"}, "gold"]}
COMPANION = "companion-bot"


def _event(
    message_id: str,
    *,
    tier: Any = "gold",
    kind: str = "message",
    text: str = "hello",
    author: str = "viewer-1",
    source: str = INPUT,
    **extra: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "platform": "twitch",
        "kind": kind,
        "channel_id": "channel-1",
        "author": {"id": author, "display_name": "Viewer"},
        "message_id": message_id,
        "text": text,
    }
    badge = dict(extra.pop("badge", {}))
    if tier is not None:
        badge["tier"] = tier
    if badge:
        payload["badge"] = badge
    payload.update(extra)
    return {
        "type": "channel.chat.message",
        "payload": payload,
        "metadata": {"schema_version": 2, "source": source},
    }


def _declared_engine(
    *types: TriggerTypeDeclaration,
    default: TriggerPolicy | None = None,
    fields: Mapping[str, str] | None = None,
    values: tuple[float, ...] = (0.5,) * 8,
    generic: tuple[str, ...] = (),
) -> tuple[TriggerEngine, RecordedRandom, ManualClock]:
    if default is None:
        # A spec declaring types must carry a default; a generic one serves.
        generic = (*generic, "keyword")
        default = _policy("keyword", keywords=["hello"])
    builtins = tuple(d for d in BUILTIN_TRIGGER_TYPES if d.name in generic)
    spec = TriggerSpec(
        types=builtins + types,
        combinations=("all_of", "any_of", "none_of"),
        default_policy=default,
        fields={"badge.tier": "string"} if fields is None else fields,
    )
    registry = TriggerRegistry(companion_name="companion")
    registry.register(INPUT, spec)
    rng = RecordedRandom(values)
    clock = ManualClock()
    engine = TriggerEngine(
        registry,
        dedup_max_entries=512,
        dedup_ttl_seconds=600.0,
        companion_identity=COMPANION,
        clock=clock,
        rng=rng,
    )
    return engine, rng, clock


def _lucky(**overrides: Any) -> TriggerTypeDeclaration:
    arguments: dict[str, Any] = {
        "name": "lucky",
        "parameter_schema": LUCKY_SCHEMA,
        "kinds": ("message",),
        "match": LUCKY_MATCH,
        "probability_parameter": "p",
    }
    arguments.update(overrides)
    return TriggerTypeDeclaration(**arguments)


def _policy(type_name: str, **parameters: Any) -> TriggerPolicy:
    return TriggerPolicy(rules=(TriggerRule(type_name, parameters),))


def test_p6_ac4_badge_tier_decides_by_the_payload_field() -> None:
    """AC4 (R2): with the AC1 type and policy, ``gold`` matches; ``silver``,
    an absent tier and the integer ``7`` (a JSON-type mismatch) do not; a
    ``cue`` decides ``kind_mismatch``; with ``["gold", "silver"]``, ``silver``
    matches and ``Gold`` does not (strings compare case-sensitively)."""

    engine, rng, _ = _declared_engine(_badge(), default=_gold())

    reasons = [
        engine.evaluate(_event("m1", tier="gold")).reason,
        engine.evaluate(_event("m2", tier="silver")).reason,
        engine.evaluate(_event("m3", tier=None)).reason,
        engine.evaluate(_event("m4", tier=7)).reason,
        engine.evaluate(_event("m5", tier="gold", kind="cue")).reason,
    ]
    assert reasons == [
        "accepted:badge_tier_match",
        "rejected:badge_tier_no_match",
        "rejected:badge_tier_no_match",
        "rejected:badge_tier_no_match",
        "rejected:badge_tier_kind_mismatch",
    ]

    engine.registry.configure(INPUT, _policy("badge_tier", tiers=["gold", "silver"]))
    assert engine.evaluate(_event("m6", tier="silver")).reason == "accepted:badge_tier_match"
    assert engine.evaluate(_event("m7", tier="Gold")).reason == "rejected:badge_tier_no_match"
    # A type without a probability parameter draws nothing and records nothing.
    assert rng.ordinal == 0
    assert engine.evaluate(_event("m8")).draws == ()


def test_p6_ac5_invariants_run_before_any_declared_rule() -> None:
    """AC5 (R2): a self-authored event, an internal trace and a
    non-schema-2 event are rejected by their existing invariant reasons with
    0 rule evaluations and 0 draws; a redelivery inside the dedup window
    returns the recorded decision with 0 evaluations and 0 draws."""

    engine, rng, _ = _declared_engine(_lucky(), default=_policy("lucky", p=0.9))

    self_authored = _event("m1", author=COMPANION.upper())
    trace = dict(_event("m2"), type=TRACE_INPUT_TRIGGER_ACCEPTED)
    legacy = _event("m3")
    legacy["metadata"] = {"schema_version": 1, "source": INPUT}

    reasons = [engine.evaluate(event).reason for event in (self_authored, trace, legacy)]
    assert reasons == [
        "self_authored",
        "internal_trace",
        "not_normalized:metadata.schema_version",
    ]
    assert engine.rule_evaluations == 0
    assert rng.ordinal == 0

    first = engine.evaluate(_event("m4"))
    evaluations, served = engine.rule_evaluations, rng.ordinal
    assert (evaluations, served) == (1, 1)
    again = engine.evaluate(_event("m4"))
    assert again is first
    assert engine.rule_evaluations - evaluations == 0
    assert rng.ordinal - served == 0


def test_p6_ac7_one_draw_per_evaluation_and_a_reproducible_replay() -> None:
    """AC7 (R2): 100 events — 50 matching the tree, 50 not — consume exactly
    100 draws; the same events replayed from the same source yield the
    identical 100 decisions; a matching event whose draw is at or above the
    probability decides ``probability_miss``."""

    values = tuple((index * 37 % 100) / 100 for index in range(100))
    events = [
        _event(f"m{index}", tier="gold" if index % 2 == 0 else "silver")
        for index in range(100)
    ]

    def run() -> tuple[list[TriggerDecision], RecordedRandom]:
        engine, rng, _ = _declared_engine(
            _lucky(), default=_policy("lucky", p=0.5), values=values
        )
        return [engine.evaluate(event) for event in events], rng

    decisions, rng = run()
    assert rng.ordinal == 100
    assert [len(decision.draws) for decision in decisions] == [1] * 100
    assert [decision.draws[0]["ordinal"] for decision in decisions] == list(range(100))

    replayed, replay_rng = run()
    assert replay_rng.ordinal == 100
    assert replayed == decisions

    by_reason = {decision.reason for decision in decisions}
    assert by_reason == {
        "accepted:lucky_match",
        "rejected:lucky_no_match",
        "rejected:lucky_probability_miss",
    }
    for index, decision in enumerate(decisions):
        draw = values[index]
        if index % 2:
            expected = "rejected:lucky_no_match"
        elif draw < 0.5:
            expected = "accepted:lucky_match"
        else:
            expected = "rejected:lucky_probability_miss"
        assert decision.reason == expected, (index, draw, decision.reason)


def test_p6_a_kind_mismatch_still_takes_its_one_draw() -> None:
    """R2: the number of draws depends on the policy alone, so a declared
    probability rule draws once even for an event its ``kinds`` exclude."""

    engine, rng, _ = _declared_engine(_lucky(), default=_policy("lucky", p=0.5))
    decision = engine.evaluate(_event("m1", kind="cue"))
    assert decision.reason == "rejected:lucky_kind_mismatch"
    assert rng.ordinal == 1
    assert [entry["ordinal"] for entry in decision.draws] == [0]


def test_p6_ac8_matches_is_a_case_insensitive_wildcard() -> None:
    """AC8 (R2): ``hi*bot?`` matches ``Hi there BOTs`` and not ``hi bot``; a
    pattern of 128 ``*`` over a 10 000-character text decides on the injected
    clock, synchronously, with no timeout path involved."""

    greeting = TriggerTypeDeclaration(
        name="greeting",
        parameter_schema={"type": "object", "properties": {}},
        kinds=("message",),
        match={"matches": [{"field": "text"}, "hi*bot?"]},
    )
    stars = TriggerTypeDeclaration(
        name="stars",
        parameter_schema={"type": "object", "properties": {}},
        kinds=("message",),
        match={"matches": [{"field": "text"}, "*" * 128]},
    )
    engine, rng, clock = _declared_engine(greeting, stars, default=_policy("greeting"))

    assert engine.evaluate(_event("m1", text="Hi there BOTs")).reason == "accepted:greeting_match"
    assert engine.evaluate(_event("m2", text="hi bot")).reason == "rejected:greeting_no_match"

    started = clock.now
    decision = engine.evaluate(_event("m3", text="x" * 10_000), policy=_policy("stars"))
    assert decision.reason == "accepted:stars_match"
    assert clock.now == started
    assert rng.ordinal == 0


def test_p6_ac35_draws_record_the_five_scalars_of_each_draw() -> None:
    """AC35 (R2): under ``t{p: 0.3}`` and the values ``0.9, 0.1, 0.42``, three
    tree-matching events decide miss, match, miss, and the third ``draws`` is
    the literal entry; a replay from the re-seeded source yields equal
    ``draws``; a redelivery of the third returns the same ``draws`` with 0
    draws taken; a generic-only policy (``probability`` included) has
    ``draws == ()``; no entry carries a payload field, the event text or a
    parameter other than ``p``."""

    values = (0.9, 0.1, 0.42)
    secret_text = "secret text of the viewer"
    policy = _policy("t", p=0.3, label="other-parameter-value")
    events = [
        _event(f"m{index}", text=secret_text, badge={"note": "payload-value"})
        for index in range(3)
    ]

    def run(rng_values: tuple[float, ...]) -> tuple[TriggerEngine, RecordedRandom, list[TriggerDecision]]:
        engine, rng, _ = _declared_engine(
            _lucky(name="t"), default=policy, values=rng_values,
            fields={"badge.tier": "string", "badge.note": "string"},
            generic=("probability", "keyword"),
        )
        return engine, rng, [engine.evaluate(event) for event in events]

    engine, rng, decisions = run(values)
    assert [decision.reason for decision in decisions] == [
        "rejected:t_probability_miss",
        "accepted:t_match",
        "rejected:t_probability_miss",
    ]
    assert decisions[2].draws == (
        {"rule": 0, "parameter": "p", "probability": 0.3, "draw": 0.42, "ordinal": 2},
    )

    rng.seed()
    _, replay_rng, replayed = run(rng.values)
    assert replay_rng.values == values
    assert [d.draws for d in replayed] == [d.draws for d in decisions]

    served = rng.ordinal
    again = engine.evaluate(events[2])
    assert again.draws == decisions[2].draws
    assert rng.ordinal == served

    generic = TriggerPolicy(
        rules=(
            TriggerRule("probability", {"probability": 0.5}),
            TriggerRule("keyword", {"keywords": ["secret"]}),
        ),
        combination="any_of",
    )
    generic_decision = engine.evaluate(_event("g1"), policy=generic)
    assert generic_decision.draws == ()
    assert set(decision_trace_payload(generic_decision)) == {
        "source_event_id", "input", "platform", "channel_id", "policy_version", "reason",
    }

    forbidden = (secret_text, "payload-value", "other-parameter-value", "label", "gold")
    for decision in decisions:
        for entry in decision.draws:
            assert tuple(entry) == DRAW_ENTRY_KEYS
            assert entry["parameter"] == "p"
            for value in entry.values():
                assert not any(text in str(value) for text in forbidden), entry
    trace = decision_trace_payload(decisions[2])
    assert trace["draws"] == [
        {"rule": 0, "parameter": "p", "probability": 0.3, "draw": 0.42, "ordinal": 2}
    ]
    assert json.dumps(trace)  # five scalars, serialisable as a trace payload


def test_p6_draws_follow_policy_order_and_an_override_counts_from_zero() -> None:
    """R2: one entry per evaluated declared probability rule, in policy order,
    with ``rule`` its index; a generic ``probability`` draw is counted by the
    source (so ordinals reproduce the draw) yet recorded in no entry; an
    ``rng`` override counts from its own zero."""

    engine, rng, _ = _declared_engine(
        _lucky(), _lucky(name="second_lucky"), generic=("probability",),
        values=(0.1, 0.2, 0.3, 0.4),
    )
    policy = TriggerPolicy(
        rules=(
            TriggerRule("lucky", {"p": 0.9}),
            TriggerRule("probability", {"probability": 0.9}),
            TriggerRule("second_lucky", {"p": 0.25}),
        )
    )
    decision = engine.evaluate(_event("m1"), policy=policy)
    assert decision.draws == (
        {"rule": 0, "parameter": "p", "probability": 0.9, "draw": 0.1, "ordinal": 0},
        {"rule": 2, "parameter": "p", "probability": 0.25, "draw": 0.3, "ordinal": 2},
    )
    assert decision.reason == "rejected:second_lucky_probability_miss"

    override = RecordedRandom((0.7, 0.6))
    first = engine.evaluate(_event("m2"), policy=_policy("lucky", p=0.5), rng=override)
    second = engine.evaluate(_event("m3"), policy=_policy("lucky", p=0.5), rng=override)
    assert [first.draws[0]["ordinal"], second.draws[0]["ordinal"]] == [0, 1]
    assert rng.ordinal == 3


def test_p6_each_override_source_keeps_its_own_count() -> None:
    """R2: an override's ordinals count the values that object has served,
    so alternating between two overrides — or passing one on a call an
    invariant refuses — never restarts the other's count, and passing the
    injected source itself shares the engine's count."""

    engine, rng, _ = _declared_engine(_lucky(), values=(0.1, 0.2))
    policy = _policy("lucky", p=0.5)
    a = RecordedRandom((0.7, 0.6, 0.5))
    b = RecordedRandom((0.4, 0.3))
    ordinals = [
        engine.evaluate(_event(message_id), policy=policy, rng=source).draws[0]["ordinal"]
        for message_id, source in (("m1", a), ("m2", b), ("m3", a))
    ]
    assert ordinals == [0, 0, 1]
    refused = engine.evaluate(_event("m4", author=COMPANION), policy=policy, rng=b)
    assert refused.reason == "self_authored"
    assert engine.evaluate(_event("m5"), policy=policy, rng=a).draws[0]["ordinal"] == 2
    assert engine.evaluate(_event("m6"), policy=policy, rng=b).draws[0]["ordinal"] == 1
    assert [a.ordinal, b.ordinal] == [3, 2]

    first = engine.evaluate(_event("m7"), policy=policy)
    shared = engine.evaluate(_event("m8"), policy=policy, rng=rng)
    assert [first.draws[0]["ordinal"], shared.draws[0]["ordinal"]] == [0, 1]


def test_p6_comparisons_follow_the_json_type_rules() -> None:
    """R2: a boolean is never a number for ``gte``/``lte``/``equals``/``in``;
    an absent ``param`` operand makes its comparison true; an absent
    ``field`` makes it false; ``in`` is false for a non-scalar left value."""

    level = TriggerTypeDeclaration(
        name="level",
        parameter_schema={
            "type": "object",
            "properties": {
                "minimum": {"type": "integer"},
                "maximum": {"type": "integer"},
                "flags": {"type": "array", "items": {"type": "integer"}},
            },
            "required": ["minimum"],
        },
        kinds=("message",),
        match={
            "all": [
                {"gte": [{"field": "badge.level"}, {"param": "minimum"}]},
                {"lte": [{"field": "badge.level"}, {"param": "maximum"}]},
                {"in": [{"field": "badge.level"}, {"param": "flags"}]},
            ]
        },
    )
    flag = TriggerTypeDeclaration(
        name="flag",
        parameter_schema={"type": "object", "properties": {}},
        kinds=("message",),
        match={"any": [{"equals": [{"field": "badge.on"}, 1]}, {"in": [{"field": "badge.on"}, [1, 0]]}]},
    )
    engine, _, _ = _declared_engine(
        level, flag, fields={"badge.level": "integer", "badge.on": "boolean"}
    )
    minimum_only = _policy("level", minimum=3)

    def reason(message_id: str, policy: TriggerPolicy, **badge: Any) -> str:
        event = _event(message_id, tier=None, badge=badge)
        return engine.evaluate(event, policy=policy).reason

    # ``maximum`` and ``flags`` are unset: their comparisons narrow nothing.
    assert reason("a", minimum_only, level=5) == "accepted:level_match"
    assert reason("b", minimum_only, level=2) == "rejected:level_no_match"
    assert reason("c", minimum_only, level=True) == "rejected:level_no_match"
    assert reason("d", minimum_only, level="5") == "rejected:level_no_match"
    assert reason("e", minimum_only) == "rejected:level_no_match"
    assert reason("f", minimum_only, level=[5]) == "rejected:level_no_match"
    bounded = _policy("level", minimum=3, maximum=4, flags=[4, 9])
    assert reason("g", bounded, level=4) == "accepted:level_match"
    assert reason("h", bounded, level=5) == "rejected:level_no_match"
    assert reason("i", _policy("level", minimum=0, flags=[1]), level=True) == (
        "rejected:level_no_match"
    )
    assert reason("j", _policy("flag"), on=True) == "rejected:flag_no_match"
    assert reason("k", _policy("flag"), on=1) == "accepted:flag_match"


def test_p6_not_and_nested_logic_evaluate_iteratively() -> None:
    """R2: ``not``, ``all`` and ``any`` combine at depth, the eight-deep
    tree the contract allows included."""

    deep = TriggerTypeDeclaration(
        name="deep",
        parameter_schema={"type": "object", "properties": {}},
        kinds=("message",),
        match=_nested_not(8),
    )
    mixed = TriggerTypeDeclaration(
        name="mixed",
        parameter_schema={"type": "object", "properties": {}},
        kinds=("message",),
        match={
            "any": [
                {"all": [{"equals": [{"field": "text"}, "a"]}, {"not": {"equals": [{"field": "kind"}, "cue"]}}]},
                {"matches": [{"field": "author.id"}, "VIP-*"]},
            ]
        },
    )
    engine, _, _ = _declared_engine(deep, mixed, fields={})

    # Eight levels: seven ``not`` around one ``equals`` — an odd count negates.
    assert engine.evaluate(_event("d1", text="deep_sentinel"), policy=_policy("deep")).reason == (
        "rejected:deep_no_match"
    )
    assert engine.evaluate(_event("d2", text="other"), policy=_policy("deep")).reason == (
        "accepted:deep_match"
    )
    assert engine.evaluate(_event("x1", text="a"), policy=_policy("mixed")).accepted
    assert not engine.evaluate(_event("x2", text="b"), policy=_policy("mixed")).accepted
    assert engine.evaluate(_event("x3", text="b", author="vip-7"), policy=_policy("mixed")).accepted


@pytest.mark.parametrize(
    ("pattern", "text", "expected"),
    [
        pytest.param("", "", True, id="empty-pattern-empty-text"),
        pytest.param("", "a", False, id="empty-pattern-text"),
        pytest.param("*", "", True, id="star-empty-text"),
        pytest.param("*", "anything at all", True, id="star-only"),
        pytest.param("***", "x", True, id="stars-only"),
        pytest.param("?abc", "xabc", True, id="question-first"),
        pytest.param("abc?", "abcx", True, id="question-last"),
        pytest.param("?", "", False, id="question-needs-a-character"),
        pytest.param("a?", "a", False, id="question-at-end-needs-a-character"),
        pytest.param("a*b*c", "aXXbYYc", True, id="middle-segment"),
        pytest.param("a*b", "ab", True, id="star-empty-run"),
        pytest.param("ab*ba", "aba", False, id="prefix-and-suffix-may-not-overlap"),
        pytest.param("*b?b*", "abcbbxb", True, id="question-inside-a-middle-segment"),
        pytest.param("HELLO", "hello", True, id="case-insensitive"),
        pytest.param("hello", "hello!", False, id="anchored-without-star"),
        pytest.param("STRAßE", "straße", True, id="sharp-s-folds-per-character"),
        pytest.param("strasse", "straße", False, id="sharp-s-never-expands-the-text"),
        pytest.param("?", "ß", True, id="question-takes-one-sharp-s"),
        pytest.param("İ", "İ", True, id="dotted-capital-i-equals-itself"),
        pytest.param("i", "İ", False, id="dotted-capital-i-is-not-i"),
        pytest.param("*i*", "İİİ", False, id="dotted-capital-i-under-stars"),
    ],
)
def test_p6_the_wildcard_matcher_table(pattern: str, text: str, expected: bool) -> None:
    """A2, D5: the matcher's verdicts, each within the
    ``(len(text) + 1) × (len(pattern) + 1)`` step bound — case folding is
    per character, so it never changes the length the bound is taken over."""

    matched, steps = _wildcard_match(pattern, text)
    assert matched is expected
    assert 0 <= steps <= (len(text) + 1) * (len(pattern) + 1)


def test_p6_ac36_the_matcher_is_linear_in_the_text() -> None:
    """AC36 (R2): the 128-character pattern of 63 ``*a`` pairs and ``*b``,
    over 5 000 and 10 000 ``a``, decides no match within
    ``(len(text) + 1) × 129`` steps, steps(10 000) ≤ 2 × steps(5 000) + 129,
    and the engine's ``counts["wildcard_steps"]`` grows by exactly the
    matcher's reported steps."""

    pattern = "*a" * 63 + "*b"
    assert len(pattern) == 128
    steps: dict[int, int] = {}
    for size in (5_000, 10_000):
        matched, steps[size] = _wildcard_match(pattern, "a" * size)
        assert matched is False
        assert steps[size] <= (size + 1) * 129
    assert steps[10_000] <= 2 * steps[5_000] + 129

    # A middle segment that scans the whole text and fails late at every
    # position: the bound and the linear growth hold where the work is real,
    # not only where the suffix fails at once.
    hard = "*abac*"
    hard_steps: dict[int, int] = {}
    for size in (2_500, 5_000):
        matched, hard_steps[size] = _wildcard_match(hard, "ab" * size)
        assert matched is False
        assert 2 * size < hard_steps[size] <= (2 * size + 1) * (len(hard) + 1)
    assert hard_steps[5_000] <= 2 * hard_steps[2_500] + len(hard) + 1

    spread = "*a" * 63 + "*"
    matched, spread_steps = _wildcard_match(spread, "a" * 10_000)
    assert matched is True and spread_steps == 63

    rule = TriggerTypeDeclaration(
        name="pattern",
        parameter_schema={"type": "object", "properties": {}},
        kinds=("message",),
        match={"matches": [{"field": "text"}, pattern]},
    )
    engine, _, _ = _declared_engine(rule, fields={})
    for size in (5_000, 10_000):
        before = engine.counts[COUNT_WILDCARD_STEPS]
        decision = engine.evaluate(_event(f"w{size}", text="a" * size), policy=_policy("pattern"))
        assert decision.reason == "rejected:pattern_no_match"
        assert engine.counts[COUNT_WILDCARD_STEPS] - before == steps[size]
    # A text the pattern matches: every middle segment is located.
    before = engine.counts[COUNT_WILDCARD_STEPS]
    mixed_text = "ab" * 5_000
    decision = engine.evaluate(_event("w-mixed", text=mixed_text), policy=_policy("pattern"))
    assert decision.reason == "accepted:pattern_match"
    assert engine.counts[COUNT_WILDCARD_STEPS] - before == _wildcard_match(pattern, mixed_text)[1]
    with pytest.raises(TypeError):
        engine.counts[COUNT_WILDCARD_STEPS] = 0  # type: ignore[index]


def test_p6_draws_shape_is_validated_and_value_free() -> None:
    """R2: a ``draws`` entry holds exactly the five keys, each a scalar; a
    refusal names the entry and echoes no value; entries are read-only."""

    base = {
        "accepted": True,
        "reason": "accepted:t_match",
        "policy_version": None,
        "source_event_id": "m1",
    }
    entry = {"rule": 0, "parameter": "p", "probability": 0.3, "draw": 0.1, "ordinal": 0}
    decision = TriggerDecision(**base, draws=[entry])
    assert decision.draws == (entry,)
    with pytest.raises(TypeError):
        decision.draws[0]["draw"] = 0.9  # type: ignore[index]
    assert TriggerDecision(**base).draws == ()

    with_text = dict(entry, text="viewer secret")
    _refused(lambda: TriggerDecision(**base, draws=(with_text,)), "TriggerDecision.draws[0]", "viewer secret")
    missing = {key: entry[key] for key in DRAW_ENTRY_KEYS[:4]}
    _refused(lambda: TriggerDecision(**base, draws=(missing,)), "TriggerDecision.draws[0]")
    nested = dict(entry, parameter={"secret": "viewer secret"})
    _refused(
        lambda: TriggerDecision(**base, draws=(nested,)),
        "TriggerDecision.draws[0].parameter",
        "viewer secret",
    )
    _refused(lambda: TriggerDecision(**base, draws=entry), "TriggerDecision.draws")  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# P7 (R3, R2 admission): hooked types — async evaluation, the bounded hook,
# the hard veto and supply through the module's own context; AC9, AC11, AC12,
# AC13, AC14, AC34
# --------------------------------------------------------------------------- #

#: The hooked fixture type's bound, in seconds of the engine's clock.
HOOK_BOUND = 0.5
WAKE_SCHEMA = {
    "type": "object",
    "properties": {"phrase": {"type": "string"}},
    "additionalProperties": False,
}


def _wake(**overrides: Any) -> TriggerTypeDeclaration:
    arguments: dict[str, Any] = {
        "name": "wake",
        "parameter_schema": WAKE_SCHEMA,
        "kinds": ("message",),
        "hook": True,
        "hook_timeout_seconds": HOOK_BOUND,
    }
    arguments.update(overrides)
    return TriggerTypeDeclaration(**arguments)


def _wake_policy(combination: str = "all_of", *rules: TriggerRule) -> TriggerPolicy:
    if not rules:
        rules = (TriggerRule("wake", {"phrase": "hey"}),)
    return TriggerPolicy(rules=rules, combination=combination)


def _wake_engine(
    default: TriggerPolicy | None = None, **overrides: Any
) -> tuple[TriggerEngine, RecordedRandom, ManualClock]:
    return _declared_engine(
        _wake(**overrides),
        default=_wake_policy() if default is None else default,
        generic=("keyword",),
    )


class Hook:
    """A hook that records each invocation and answers as told.

    ``answer`` is returned; an exception instance is raised instead; the
    sentinel :data:`NEVER` parks the hook forever (until cancelled).
    """

    def __init__(self, answer: Any = True) -> None:
        self.answer = answer
        self.calls: list[tuple[Any, Any]] = []
        self.cancelled = 0

        async def hook(event: Any, parameters: Any) -> Any:
            self.calls.append((event, parameters))
            if self.answer is NEVER:
                try:
                    await asyncio.get_running_loop().create_future()
                except asyncio.CancelledError:
                    self.cancelled += 1
                    raise
            if isinstance(self.answer, BaseException):
                raise self.answer
            return self.answer

        self.hook = hook


NEVER = object()


class _Ownership:
    """An ownership table naming one owner for every pair, or none."""

    def __init__(self, owner: str | None = None) -> None:
        self._owner = owner

    def owner(self, family: str, platform: str) -> str | None:
        return self._owner

    def is_set_none(self, family: str, platform: str) -> bool:
        return False

    def rows(self) -> tuple[tuple[str, str], ...]:
        return ()

    def excluded(self, family: str) -> frozenset[str]:
        return frozenset()


def _supply(engine: TriggerEngine, module: str, type_name: str, hook: Any) -> None:
    """Supply *module*'s hook the way a module does: through its own context."""

    runtime_context(triggers=engine).for_module(module).supply_trigger_hook(
        type_name, hook
    )


async def test_p7_ac11_each_hook_answer_decides_and_is_retained() -> None:
    """AC11 (R3): ``True``, ``False``, ``1``, ``"yes"``, ``None`` and a
    raise decide ``accepted:wake_match``, ``rejected:wake_no_match`` and four
    ``rejected:wake_hook_error``; each is counted as a trigger decision and
    retained in the dedup window."""

    engine, _, _ = _wake_engine()
    hook = Hook()
    _supply(engine, INPUT, "wake", hook.hook)
    answers = [True, False, 1, "yes", None, RuntimeError("viewer secret")]
    decisions = []
    for index, answer in enumerate(answers):
        hook.answer = answer
        decisions.append(await engine.evaluate_async(_event(f"h{index}")))

    assert [decision.reason for decision in decisions] == [
        "accepted:wake_match",
        "rejected:wake_no_match",
        *["rejected:wake_hook_error"] * 4,
    ]
    assert len(hook.calls) == 6
    # Counted: every rejection is one trigger rejection, the acceptance none.
    assert engine.counters.get("trigger_rejections") == 5
    assert engine.window_size == 6
    for index, decision in enumerate(decisions):
        assert engine.recorded(
            platform="twitch", channel_id="channel-1", source_event_id=f"h{index}"
        ) is decision
    assert "viewer secret" not in repr(decisions)


async def test_p7_the_hook_reads_a_deep_read_only_view() -> None:
    """R3: the hook receives a read-only, deep-frozen view of the event and of
    the rule's parameters; writing through it fails and is a hook error."""

    engine, _, _ = _wake_engine()
    seen: list[tuple[Any, Any]] = []

    async def hook(event: Any, parameters: Any) -> bool:
        seen.append((event, parameters))
        event["payload"]["badge"]["tier"] = "forged"
        return True

    _supply(engine, INPUT, "wake", hook)
    event = _event("v1", tags=["a", {"b": 1}])
    decision = await engine.evaluate_async(event)
    assert decision.reason == "rejected:wake_hook_error"
    (view, parameters), = seen
    assert isinstance(view, types.MappingProxyType)
    assert isinstance(view["payload"]["badge"], types.MappingProxyType)
    assert isinstance(view["payload"]["tags"][1], types.MappingProxyType)
    assert view["payload"]["tags"][0] == "a"
    assert dict(parameters) == {"phrase": "hey"}
    with pytest.raises(TypeError):
        parameters["phrase"] = "x"  # type: ignore[index]
    assert event["payload"]["badge"]["tier"] == "gold"


async def test_p7_ac12_a_hook_that_never_returns_times_out_on_the_bound() -> None:
    """AC12 (R3): with a 0.5 s bound, a never-returning hook decides
    ``rejected:wake_hook_timeout`` once the engine's ManualClock has advanced
    exactly 0.5 s (no epsilon), the hook is cancelled, and a following event of
    the same input is evaluated normally."""

    engine, _, clock = _wake_engine()
    hook = Hook(NEVER)
    _supply(engine, INPUT, "wake", hook.hook)

    pending = asyncio.ensure_future(engine.evaluate_async(_event("t1")))
    await settle()
    assert not pending.done()
    assert len(hook.calls) == 1
    clock.advance(HOOK_BOUND - 0.25)
    await settle()
    assert not pending.done()
    clock.advance(0.25)
    await settle()
    assert pending.done()
    decision = pending.result()
    assert decision.reason == "rejected:wake_hook_timeout"
    assert hook.cancelled == 1
    assert engine.recorded(
        platform="twitch", channel_id="channel-1", source_event_id="t1"
    ) is decision

    hook.answer = True
    following = await engine.evaluate_async(_event("t2"))
    assert following.reason == "accepted:wake_match"
    assert len(hook.calls) == 2


async def test_p7_a_timed_out_hook_answering_late_is_ignored_and_retrieved() -> None:
    """R3: a hook that swallows its cancellation and raises later changes no
    decision, and its exception is consumed (no "never retrieved")."""

    engine, _, clock = _wake_engine()
    loop = asyncio.get_running_loop()
    reported: list[Mapping[str, Any]] = []
    previous = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: reported.append(context))
    release = loop.create_future()

    async def stubborn(event: Any, parameters: Any) -> bool:
        while True:
            try:
                await asyncio.shield(release)
                break
            except asyncio.CancelledError:
                continue
        raise RuntimeError("late")

    _supply(engine, INPUT, "wake", stubborn)
    pending = asyncio.ensure_future(engine.evaluate_async(_event("late1")))
    await settle()
    clock.advance(HOOK_BOUND)
    await settle()
    decision = pending.result()
    assert decision.reason == "rejected:wake_hook_timeout"
    release.set_result(None)
    await settle()
    assert engine.recorded(
        platform="twitch", channel_id="channel-1", source_event_id="late1"
    ) is decision
    del pending
    gc.collect()
    loop.set_exception_handler(previous)
    assert [context.get("message") for context in reported] == []


async def test_p7_ac12_a_bound_of_0_or_10_5_is_refused_at_load(tmp_path: Path) -> None:
    """AC12 (R3), load half (reuses P3/P4): ``hook_timeout_seconds`` of 0 and
    10.5 are refused at construction and at load, naming the field."""

    for timeout in (0, 10.5):
        _refused(
            lambda: _wake(hook_timeout_seconds=timeout),
            "TriggerTypeDeclaration.hook_timeout_seconds",
        )
        root = tmp_path / str(timeout).replace(".", "_")
        root.mkdir()
        await _refused_at_load(
            root,
            _with_type(match=None, hook=True, hook_timeout_seconds=timeout),
            f"{_DECLARED_AT}.hook_timeout_seconds",
        )


async def test_p7_ac13_the_hook_runs_only_for_an_eligible_event() -> None:
    """AC13 (R3): 0 invocations for an invariant-rejected event, a dedup
    redelivery, an event of a kind outside ``kinds`` and an event of another
    input; 1 for an eligible event."""

    engine, _, _ = _wake_engine()
    engine.registry.register(SECOND_INPUT, _second_spec())
    hook = Hook(True)
    _supply(engine, INPUT, "wake", hook.hook)

    eligible = await engine.evaluate_async(_event("e1"))
    assert eligible.reason == "accepted:wake_match"
    assert len(hook.calls) == 1

    self_authored = await engine.evaluate_async(_event("e2", author=COMPANION))
    redelivery = await engine.evaluate_async(_event("e1"))
    other_kind = await engine.evaluate_async(_event("e3", kind="cue"))
    other_input = await engine.evaluate_async(_second_message("e4"))
    assert self_authored.reason == "self_authored"
    assert redelivery is eligible
    assert other_kind.reason == "rejected:wake_kind_mismatch"
    assert other_input.reason == "accepted:keyword_match"
    assert len(hook.calls) == 1
    # The other input cannot name the type at all (R2 scoping).
    with pytest.raises(ContractError):
        await engine.evaluate_async(_second_message("e5"), policy=_wake_policy())
    assert len(hook.calls) == 1


async def test_p7_ac14_only_the_declaring_module_supplies_its_hook(
    tmp_path: Path,
) -> None:
    """AC14 (R3, D4): a second module supplying the first module's hooked
    type — through its own context or the engine — is refused, value-free, and
    the type then decides ``rejected:wake_hook_unavailable``; with no hook at
    all the decision is the same. A non-coroutine hook is refused too."""

    engine, _, _ = _wake_engine()
    engine.registry.register(SECOND_INPUT, _second_spec())
    context = runtime_context(triggers=engine)
    intruder = Hook(True)

    second = context.for_module(SECOND_INPUT)
    with pytest.raises(ContractError) as caught:
        second.supply_trigger_hook("wake", intruder.hook)
    assert caught.value.field == f"triggers.{SECOND_INPUT}.hook"
    # The shared engine and registry offer no public supply naming a module:
    # a module reaching them through its context cannot fill another's slot.
    assert not hasattr(engine, "supply_hook")
    assert not hasattr(engine.registry, "supply_hook")
    owned = context.for_module(SECOND_INPUT, ownership=_Ownership())
    with pytest.raises(ContractError):
        owned.triggers.supply_hook("wake", intruder.hook)
    assert dict(engine.registry.hook_slots(INPUT)) == {"wake": None}

    refused_decision = await engine.evaluate_async(_event("u1"))
    assert refused_decision.reason == "rejected:wake_hook_unavailable"
    assert intruder.calls == []

    fresh, _, _ = _wake_engine()
    assert (await fresh.evaluate_async(_event("u1"))).reason == (
        "rejected:wake_hook_unavailable"
    )

    own = context.for_module(INPUT)

    def not_a_coroutine(event: Any, parameters: Any) -> bool:
        return True

    with pytest.raises(ContractError) as caught:
        own.supply_trigger_hook("wake", not_a_coroutine)
    assert caught.value.field == f"triggers.{INPUT}.hook"
    with pytest.raises(ContractError) as caught:
        own.supply_trigger_hook("not-declared-sentinel", intruder.hook)
    assert "not-declared-sentinel" not in str(caught.value)

    supplied = Hook(True)
    own.supply_trigger_hook("wake", supplied.hook)
    assert (await engine.evaluate_async(_event("u2"))).reason == "accepted:wake_match"
    assert len(supplied.calls) == 1


async def test_p7_ac34_a_failed_hook_vetoes_whatever_the_combination() -> None:
    """AC34 (R3): under ``none_of[wake]`` a raising hook, a never-returning
    one and no hook decide ``hook_error``, ``hook_timeout`` and
    ``hook_unavailable``; under ``any_of[keyword{hi}, wake]`` an event with
    text ``hi`` and a raising hook decides ``hook_error``, and so does the same
    policy as the module's default. 0 events are admitted."""

    scheduler = AdmissionScheduler(
        _never_run,
        session_queue_capacity=8,
        global_pending_capacity=8,
        max_sessions=8,
        workers=1,
        total_run_seconds=10.0,
        wait_seconds=10.0,
        clock=ManualClock(),
    )
    decisions: list[TriggerDecision] = []

    none_of = _wake_policy("none_of")
    engine, _, clock = _wake_engine()
    decisions.append(await engine.evaluate_async(_event("n0"), policy=none_of))
    hook = Hook(RuntimeError("boom"))
    _supply(engine, INPUT, "wake", hook.hook)
    decisions.append(await engine.evaluate_async(_event("n1"), policy=none_of))
    hook.answer = NEVER
    pending = asyncio.ensure_future(engine.evaluate_async(_event("n2"), policy=none_of))
    await settle()
    clock.advance(HOOK_BOUND)
    await settle()
    decisions.append(pending.result())
    assert [decision.reason for decision in decisions] == [
        "rejected:wake_hook_unavailable",
        "rejected:wake_hook_error",
        "rejected:wake_hook_timeout",
    ]
    # Sanity: the same policy with a ``False`` answer is accepted, so the
    # rejections above are the veto, not the combination.
    hook.answer = False
    assert (await engine.evaluate_async(_event("n3"), policy=none_of)).accepted

    any_of = _wake_policy(
        "any_of",
        TriggerRule("keyword", {"keywords": ["hi"]}),
        TriggerRule("wake", {"phrase": "hey"}),
    )
    hook.answer = RuntimeError("boom")
    configured = await engine.evaluate_async(_event("a1", text="hi"), policy=any_of)
    assert configured.reason == "rejected:wake_hook_error"
    decisions.append(configured)

    default_engine, _, _ = _wake_engine(default=any_of)
    _supply(default_engine, INPUT, "wake", hook.hook)
    by_default = await default_engine.evaluate_async(_event("a1", text="hi"))
    assert by_default.reason == "rejected:wake_hook_error"
    decisions.append(by_default)

    admitted = [
        scheduler.admit(SessionKey("twitch", "channel-1", "viewer-1"), Work({}, d.source_event_id))
        for d in decisions
        if d.accepted
    ]
    assert admitted == []
    assert scheduler.pending == 0
    await scheduler.aclose()


async def _never_run(context: Any) -> Any:  # pragma: no cover - never admitted
    raise AssertionError("no run is admitted")


def test_p7_d1_synchronous_evaluate_never_calls_the_hook() -> None:
    """D1 (R3): the synchronous ``evaluate`` on a hooked policy decides
    ``rejected:wake_hook_unavailable`` without calling the supplied hook, and
    the decision is recorded; a kind outside ``kinds`` still decides
    ``kind_mismatch``."""

    engine, _, _ = _wake_engine()
    hook = Hook(True)
    _supply(engine, INPUT, "wake", hook.hook)
    decision = engine.evaluate(_event("s1"))
    assert decision.reason == "rejected:wake_hook_unavailable"
    assert engine.window_size == 1
    assert engine.evaluate(_event("s2", kind="cue")).reason == (
        "rejected:wake_kind_mismatch"
    )
    assert hook.calls == []


async def test_p7_a_hooked_probability_type_draws_once_and_can_miss() -> None:
    """R2, R3: a hooked type with a probability parameter draws once per
    evaluation, before the hook is awaited; a ``True`` answer whose draw
    missed decides ``probability_miss``."""

    declaration = _wake(
        parameter_schema=LUCKY_SCHEMA, probability_parameter="p"
    )
    engine, rng, _ = _declared_engine(
        declaration, default=_policy("wake", p=0.5), values=(0.9, 0.1), generic=("keyword",)
    )
    hook = Hook(True)
    _supply(engine, INPUT, "wake", hook.hook)
    missed = await engine.evaluate_async(_event("p1"))
    hit = await engine.evaluate_async(_event("p2"))
    assert [missed.reason, hit.reason] == [
        "rejected:wake_probability_miss",
        "accepted:wake_match",
    ]
    assert rng.ordinal == 2
    assert [entry["draw"] for entry in (*missed.draws, *hit.draws)] == [0.9, 0.1]


async def test_p7_the_owned_facade_guards_evaluate_async() -> None:
    """R3: ``_OwnedTriggers.evaluate_async`` applies the ownership guard of
    ``evaluate`` — a non-owned input is refused unread, its hook never called —
    and forwards an owned event to the engine."""

    engine, _, _ = _wake_engine()
    hook = Hook(True)
    _supply(engine, INPUT, "wake", hook.hook)
    context = runtime_context(triggers=engine)

    refused = context.for_module(INPUT, ownership=_Ownership("someone-else"), declared=True)
    decision = await refused.triggers.evaluate_async(_event("o1"))
    assert decision.reason == REASON_INPUT_NOT_OWNED
    assert hook.calls == []

    owned = context.for_module(INPUT, ownership=_Ownership(INPUT), declared=True)
    assert (await owned.triggers.evaluate_async(_event("o2"))).reason == "accepted:wake_match"
    assert len(hook.calls) == 1


class GatedHook:
    """A hook parked, per message id, until the test opens its gate."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.gates: dict[str, asyncio.Future[bool]] = {}

    async def hook(self, event: Any, parameters: Any) -> bool:
        message_id = event["payload"]["message_id"]
        self.calls.append(message_id)
        gate = asyncio.get_running_loop().create_future()
        self.gates[message_id] = gate
        return await gate

    def open(self, message_id: str, answer: bool = True) -> None:
        self.gates.pop(message_id).set_result(answer)


async def _settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


def test_p7_the_owned_facade_hands_out_no_kernel_supply() -> None:
    """R3, D4: neither the scoped facade nor the shared registry it exposes
    offers a supply naming a module, and the facade forwards no private
    engine member, so the context-bound supply is the only module path."""

    engine, _, _ = _wake_engine()
    engine.registry.register(SECOND_INPUT, _second_spec())
    owned = runtime_context(triggers=engine).for_module(
        SECOND_INPUT, ownership=_Ownership(SECOND_INPUT), declared=True
    )
    with pytest.raises(AttributeError):
        owned.triggers._supply_hook
    with pytest.raises(AttributeError):
        owned.triggers._registry
    assert not hasattr(owned.triggers.registry, "supply_hook")
    assert dict(engine.registry.hook_slots(INPUT)) == {"wake": None}


async def test_p7_a_concurrent_redelivery_waits_for_the_one_decision() -> None:
    """R2, R3: a redelivery arriving while the first evaluation awaits its
    hook reuses that evaluation's decision — the hook is called once and the
    rule draws once — and a synchronous evaluation meanwhile refuses with
    ``decision_pending``, drawing nothing and recording nothing."""

    declaration = _wake(parameter_schema=LUCKY_SCHEMA, probability_parameter="p")
    engine, rng, _ = _declared_engine(
        declaration, default=_policy("wake", p=0.5), values=(0.1, 0.9), generic=("keyword",)
    )
    hook = GatedHook()
    _supply(engine, INPUT, "wake", hook.hook)

    first = asyncio.ensure_future(engine.evaluate_async(_event("c1")))
    await _settle()
    second = asyncio.ensure_future(engine.evaluate_async(_event("c1")))
    await _settle()
    pending = engine.evaluate(_event("c1"))
    assert pending.reason == REASON_DECISION_PENDING
    assert not pending.accepted
    assert engine.window_size == 0
    assert hook.calls == ["c1"]
    assert rng.ordinal == 1

    hook.open("c1")
    decided, again = await asyncio.gather(first, second)
    assert decided is again
    assert decided.reason == "accepted:wake_match"
    assert hook.calls == ["c1"]
    assert rng.ordinal == 1
    assert engine.window_size == 1
    assert engine.counters.get("trigger_rejections") == 0


async def test_p7_a_cancelled_evaluation_releases_its_waiter() -> None:
    """R3: cancelling the evaluation a redelivery waits on lets the waiter
    evaluate afresh rather than inherit the cancellation."""

    engine, _, _ = _wake_engine()
    hook = GatedHook()
    _supply(engine, INPUT, "wake", hook.hook)

    first = asyncio.ensure_future(engine.evaluate_async(_event("k1")))
    await _settle()
    second = asyncio.ensure_future(engine.evaluate_async(_event("k1")))
    await _settle()
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    await _settle()
    assert hook.calls == ["k1", "k1"]
    hook.open("k1", False)
    assert (await second).reason == "rejected:wake_no_match"


async def test_p7_an_answer_released_with_the_timer_after_the_bound_is_late() -> None:
    """R3: a hook that answers after ``hook_timeout_seconds`` on the engine's
    clock times out even when one clock step releases the hook and the timer
    together, so both are done when the evaluation resumes."""

    engine, _, clock = _wake_engine()

    async def slow(event: Any, parameters: Any) -> bool:
        await clock.sleep(HOOK_BOUND * 2)
        return True

    _supply(engine, INPUT, "wake", slow)
    evaluation = asyncio.ensure_future(engine.evaluate_async(_event("t1")))
    await _settle()
    clock.advance(HOOK_BOUND * 2)
    assert (await evaluation).reason == "rejected:wake_hook_timeout"

    async def prompt(event: Any, parameters: Any) -> bool:
        await clock.sleep(HOOK_BOUND)
        return True

    _supply(engine, INPUT, "wake", prompt)
    evaluation = asyncio.ensure_future(engine.evaluate_async(_event("t2")))
    await _settle()
    clock.advance(HOOK_BOUND)
    assert (await evaluation).reason == "accepted:wake_match"


async def test_p7_out_of_order_hook_answers_still_expire_in_time() -> None:
    """R6: a decision dated before one recorded ahead of it — its hook
    answered later — is still evicted at its own time-to-live, and an expired
    decision is never recalled."""

    base, rng, clock = _wake_engine()
    engine = TriggerEngine(
        base.registry,
        dedup_max_entries=512,
        dedup_ttl_seconds=1.0,
        companion_identity=COMPANION,
        clock=clock,
        rng=rng,
    )
    hook = GatedHook()
    _supply(engine, INPUT, "wake", hook.hook)

    early = asyncio.ensure_future(engine.evaluate_async(_event("a")))
    await _settle()
    clock.advance(0.1)
    late = asyncio.ensure_future(engine.evaluate_async(_event("b")))
    await _settle()
    hook.open("b")
    await late
    clock.advance(0.1)
    hook.open("a")
    await early
    assert engine.window_size == 2

    clock.advance(0.85)  # 1.05 after "a" began, 0.95 after "b"
    redelivered = asyncio.ensure_future(engine.evaluate_async(_event("a")))
    await _settle()
    assert hook.calls == ["a", "b", "a"]
    hook.open("a", False)
    assert (await redelivered).reason == "rejected:wake_no_match"
    assert engine.recorded(
        platform="twitch", channel_id="channel-1", source_event_id="b"
    ) is not None


def test_p7_ac9_an_accepted_declared_decision_grants_nothing() -> None:
    """AC9 (R2): an accepted declared-type decision followed by a full
    scheduler leaves the event not admitted with the scheduler's own refusal;
    with a granting-rule-free authorization configuration the run is offered
    0 writes, read from the existing authorization view."""

    engine, _, _ = _declared_engine(_badge(), default=_gold())
    decision = engine.evaluate(_event("g1"))
    assert decision.reason == "accepted:badge_tier_match"

    scheduler = AdmissionScheduler(
        _never_run,
        session_queue_capacity=1,
        global_pending_capacity=1,
        max_sessions=8,
        workers=1,
        total_run_seconds=10.0,
        wait_seconds=10.0,
        clock=ManualClock(),
    )
    filler = scheduler.admit(SessionKey("twitch", "channel-1", "viewer-0"), Work({}, "filler"))
    assert filler.accepted
    result = scheduler.admit(
        SessionKey("twitch", "channel-1", "viewer-1"), Work({}, decision.source_event_id)
    )
    assert not result.accepted
    assert result.reason == REASON_GLOBAL_PENDING_CAP

    write = ActionSpec(
        name="chat.send",
        version=1,
        description="Send one message to one channel.",
        argument_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        },
        result_schema={"type": "object"},
        nature="write",
        required_permissions=("chat.write",),
        supported_destinations=(Destination("twitch", WILDCARD, "chat"),),
        timeout_seconds=1.0,
        idempotency="key",
    )

    class Provider:
        async def invoke(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover
            raise AssertionError("never called")

    provider = Provider()

    actions = ActionRegistry(authorization=AuthorizationPolicy())
    actions.declare(write, module="writer")
    actions.bind(write.name, provider, module="writer", provider_name="writer")
    actions.mark_ready("writer")
    assert tuple(actions.registered_ready()) == ("chat.send",)
    offered = actions.authorized(principal="operator:companion")
    assert [s for s in offered.values() if s.nature == "write"] == []
    assert len(offered) == 0
