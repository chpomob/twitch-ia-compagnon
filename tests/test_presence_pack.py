"""The shipped presence profile end to end (phase 3 R1, R8; AC6).

``presence.yaml.example`` is loaded as ``core.main`` loads it and activated
through the same path as the phase 2 profiles (``_activate_example`` of
``tests/test_examples.py``), then started by the coordinator ``core.main``
builds. Only the edges are fakes: the platform session (EventSub socket,
token validation, subscriptions, chat sends and the clip endpoints), the
model transport (a :class:`ScriptedModel`), the screen, the player, the scene
provider (the in-process :class:`ScenePeer`) and every module's sleeper,
which waits on a :class:`ManualClock` nobody advances — so no tick, sweep or
retry fires because a test waited.

The scenario drives, one at a time, the observations AC6 lists: a raid, a
viewer's ``!missed``, the broadcaster's ``!brb``, a moderator's ``!clip``, a
viewer's ``!brb``, the second visit of a viewer and the broadcaster's
``!watch``. The profile supplies its own environment (the plan's risk
mitigation): the sample values of ``tests/test_examples.py`` would not name a
watch channel of the ``<platform>/<channel_id>`` shape.
"""

from __future__ import annotations

import copy
import itertools
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

import core.main as application
from core.contracts import (
    TRACE_ACTION_COMPLETED,
    TRACE_ACTION_STARTED,
    TRACE_BRAIN_RUN_COMPLETED,
    TRACE_MODULE_DEGRADED,
)
from conftest import (
    FakeCaptureSource,
    FakeResponse,
    ManualClock,
    RecordingPlayerRunner,
    ScriptedModel,
    events_of,
    final,
    helix_clip_created,
    helix_clip_listed,
    settle,
    tool_call,
    wait_until,
    wav_bytes,
)
from modules.twitch import (
    EVENTSUB_SUBSCRIPTIONS_URL,
    HELIX_CHAT_URL,
    HELIX_CLIPS_URL,
)
from modules.watch import FACT_WATCH_STATE
from test_examples import ENV_REFERENCE, ROOT, _activate_example, _read_yaml
from test_hygiene import SECRET_SETTING
from test_integration import FakeTwitchSession, FakeWebSocket, welcome
from test_stream_control import ScenePeer
from test_twitch import raid_notice


PRESENCE_PROFILE = ROOT / "presence.yaml.example"
#: The three phase 2 profiles, which phase 3 does not touch (AC42).
PHASE2_PROFILES = ("config.yaml.example", "config.server.yaml.example", "agent.yaml.example")

BROADCASTER = "broadcaster-1"
MODERATOR = "moderator-7"
VIEWER = "viewer-3"
RAIDER = "42"
COMPANION = "Companion"
SCENES = ("Starting", "Main", "Break", "Ending")

#: The enabled set: the plan's ten modules plus ``capture``, which serves the
#: ``screen.capture`` the watch runs are granted.
ENABLED = [
    "twitch",
    "chat_context",
    "users",
    "capture",
    "audio_output",
    "stream_control",
    "clips",
    "viewer_memory",
    "moderation",
    "watch",
    "brain",
]
#: The routes R1 (c) lists, in the profile's order, with their safe audience
#: or their notice-only kinds, and the effect each one's own list carries.
NOTICE_KINDS = ["sub", "resub", "sub_gift", "community_sub_gift", "raid", "follow"]
ROUTES = {
    "thanks": {"kinds": NOTICE_KINDS, "command": None, "audience": None},
    "scene-soon": {"kinds": ["message"], "command": "!soon", "audience": "broadcaster"},
    "scene-brb": {"kinds": ["message"], "command": "!brb", "audience": "broadcaster"},
    "scene-end": {"kinds": ["message"], "command": "!end", "audience": "broadcaster"},
    "clip": {"kinds": ["message"], "command": "!clip", "audience": "moderators"},
    "missed": {"kinds": ["message"], "command": "!missed", "audience": None},
    "translate": {"kinds": ["message"], "command": "!translate", "audience": None},
    "watch": {"kinds": ["watch_tick"], "command": None, "audience": None},
    "mention": {"kinds": ["message"], "command": None, "audience": None},
}
ROUTE_EFFECTS = {
    "thanks": [("chat.write", "text", None), ("audio.play", "none", {"sound": "chime"})],
    "scene-soon": [("stream.scene.set", "none", {"scene": "Starting"}), ("chat.write", "text", None)],
    "scene-brb": [("stream.scene.set", "none", {"scene": "Break"}), ("chat.write", "text", None)],
    "scene-end": [("stream.scene.set", "none", {"scene": "Ending"}), ("chat.write", "text", None)],
    "clip": [("stream.clip.create", "none", None), ("chat.write", "text", None)],
}
#: Every action the presence pack uses, by principal (R1, R6): the grants.
GRANTS = {
    "brain": {
        "chat.read",
        "users.read",
        "chat.write",
        "audio.speak",
        "audio.play",
        "stream.scene.set",
        "stream.clip.create",
        "memory.recall",
        "memory.record",
        "moderation.request",
    },
    "brain.watch": {"chat.write", "screen.capture"},
}
#: The credentials of the enabled modules, by dotted setting path.
CREDENTIALS = (
    "twitch.client_id",
    "twitch.client_secret",
    "twitch.access_token",
    "brain.api_key",
    "stream_control.scenes.provider.password",
)


def _setting(settings: Mapping[str, Any], dotted: str) -> Any:
    value: Any = settings
    for part in dotted.split("."):
        value = value.get(part) if isinstance(value, Mapping) else None
    return value


def presence_environ(directory: Path) -> dict[str, str]:
    """The profile's own environment: one value per ``${NAME}`` it references,
    the chime a real WAV file, the memory directory under *directory*."""

    chime = directory / "chime.wav"
    chime.write_bytes(wav_bytes(0.2))
    return {
        "TWITCH_CLIENT_ID": "presence-client-id",
        "TWITCH_CLIENT_SECRET": "presence-client-secret",
        "TWITCH_ACCESS_TOKEN": "presence-access-token",
        "TWITCH_BROADCASTER_ID": BROADCASTER,
        "TWITCH_BOT_USER_ID": "bot-9",
        "AUDIO_PLAYER": sys.executable,
        "AUDIO_CHIME_PATH": str(chime),
        "SCENE_WEBSOCKET_URL": "wss://scenes.example:4455",
        "SCENE_WEBSOCKET_PASSWORD": "presence-scene-password",
        "VIEWER_MEMORY_DIRECTORY": str(directory / "memory"),
        "WATCH_CHANNEL": f"twitch/{BROADCASTER}",
        "MODEL_ENDPOINT": "https://model.invalid/v1",
        "MODEL_NAME": "presence-model",
        "MODEL_API_KEY": "presence-model-key",
    }


def _referenced(value: Any) -> set[str]:
    if isinstance(value, str):
        match = ENV_REFERENCE.fullmatch(value)
        return {match.group(1)} if match else set()
    if isinstance(value, Mapping):
        return set().union(*(_referenced(k) | _referenced(v) for k, v in value.items()), set())
    if isinstance(value, list):
        return set().union(*(_referenced(item) for item in value), set())
    return set()


# --------------------------------------------------------------------------- #
# The profile as shipped
# --------------------------------------------------------------------------- #


def test_the_profile_holds_no_literal_credential(tmp_path: Path) -> None:
    """AC6 (R1, R8): the hygiene ``SECRET_SETTING`` regex finds nothing in
    the file, every credential the enabled manifests declare is a ``${…}``
    reference, every ``secrets`` entry too, and the environment above
    resolves exactly the references the file makes."""

    text = PRESENCE_PROFILE.read_text(encoding="utf-8")
    assert [match.group(0) for match in SECRET_SETTING.finditer(text)] == []

    config = _read_yaml(PRESENCE_PROFILE)
    manifests = {name: _read_yaml(ROOT / "modules" / name / "module.yaml") for name in ENABLED}
    declared = {
        f"{name}.{setting}"
        for name, manifest in manifests.items()
        for setting in manifest.get("credentials", ())
    }
    # The speech key is left out entirely (no provider assumed): a declared
    # credential is either absent or a reference, never a literal.
    assert declared == set(CREDENTIALS) | {"audio_output.synthesis.api_key"}
    assert _setting(config["modules"], "audio_output.synthesis.api_key") is None
    for path in CREDENTIALS:
        value = _setting(config["modules"], path)
        assert isinstance(value, str) and ENV_REFERENCE.fullmatch(value), path
    assert config["secrets"] and all(ENV_REFERENCE.fullmatch(entry) for entry in config["secrets"])
    assert _referenced(config) == set(presence_environ(tmp_path))


def test_the_profile_enables_the_pack_with_its_routes_and_explicit_grants() -> None:
    """R1 (c): twitch with every notice kind, moderation in mode ``alert``,
    watch commanded by the broadcaster, a persona, the nine routes of the
    pack in order — each effect behind a privileged audience or notice-only
    kinds — and one explicit grant per action each principal uses."""

    config = _read_yaml(PRESENCE_PROFILE)
    modules = config["modules"]

    assert config["enabled_modules"] == ENABLED
    assert set(modules) == set(ENABLED)
    assert modules["twitch"]["notices"] == {"kinds": NOTICE_KINDS}
    assert modules["moderation"]["mode"] == "alert"
    assert modules["watch"]["activation"] == "command"
    assert modules["watch"]["command_audience"] == "broadcaster"
    assert modules["watch"]["start_command"] == "!watch"

    brain = modules["brain"]
    assert isinstance(brain["persona"], str) and 0 < len(brain["persona"]) <= 2000
    assert brain["delivery"] == {
        "mode": "fixed",
        "actions": [{"action": "chat.write", "text_argument": "text"}],
    }
    routes = brain["routes"]
    assert [route["name"] for route in routes] == list(ROUTES)
    for route in routes:
        match = route["match"]
        expected = ROUTES[route["name"]]
        assert (match["kinds"], match.get("command"), match.get("audience")) == (
            expected["kinds"],
            expected["command"],
            expected["audience"],
        ), route["name"]
        assert route["instructions"].strip(), route["name"]
        effects = ROUTE_EFFECTS.get(route["name"])
        if effects is None:
            assert "delivery" not in route, route["name"]
            continue
        assert route["delivery"]["mode"] == "fixed"
        assert [
            (entry["action"], entry["text_argument"], entry.get("arguments"))
            for entry in route["delivery"]["actions"]
        ] == effects
        # Route safety (R1): an effect is reachable only by a privileged
        # audience or by platform notices.
        assert match.get("audience") in ("broadcaster", "moderators") or (
            set(match["kinds"]) <= set(NOTICE_KINDS)
        )
    scenes = {
        entry["arguments"]["scene"]
        for route in routes
        for entry in route.get("delivery", {}).get("actions", ())
        if entry["action"] == "stream.scene.set"
    }
    assert scenes <= set(modules["stream_control"]["scenes"]["allowed"]) == set(SCENES)

    granted: dict[str, set[str]] = {}
    for rule in config["actions"]:
        assert rule["destination"]["platform"] == "twitch"
        assert rule["destination"]["channel_id"] == "${TWITCH_BROADCASTER_ID}"
        for principal in rule["principals"]:
            granted.setdefault(principal, set()).add(rule["action_name"])
    assert granted == GRANTS
    # The watch runs are granted the capture; the chat runs are not.
    assert "screen.capture" not in granted["brain"]


def test_the_budget_fits_the_effects_the_routes_deliver() -> None:
    """R1, R3: ``action_seconds`` allows a clip (and a scene change) its
    whole manifest timeout — the clip's 15 s confirmation window included —
    so a clip confirmed late in the window is not cut by the brain's call
    deadline; the delivery reserve holds every route's list, each entry at
    ``min(timeout, action_seconds)``; and a run that waited its whole queue
    time still starts outside the reserve."""

    config = _read_yaml(PRESENCE_PROFILE)
    brain = config["modules"]["brain"]
    budget = brain["budget"]
    timeouts = {
        action["name"]: action["timeout_seconds"]
        for name in ENABLED
        for action in _read_yaml(ROOT / "modules" / name / "module.yaml").get("actions", ())
    }

    assert budget["action_seconds"] >= timeouts["stream.clip.create"]
    assert budget["action_seconds"] >= timeouts["stream.scene.set"]
    for route in [{"name": "default", "delivery": brain["delivery"]}, *brain["routes"]]:
        entries = route.get("delivery", {}).get("actions", ())
        spent = sum(min(timeouts[entry["action"]], budget["action_seconds"]) for entry in entries)
        assert spent <= budget["delivery_reserve_seconds"], route["name"]
    admission = brain["admission"]
    assert admission == config["limits"]["admission"]
    assert admission["total_run_seconds"] - admission["wait_seconds"] > budget["delivery_reserve_seconds"]


def test_the_phase_2_profiles_are_not_the_presence_profile() -> None:
    """AC42 (R8): the pack ships in its own file; no phase 2 profile enables
    a phase 3 module or configures a persona or routes."""

    phase3 = {"clips", "viewer_memory", "moderation", "watch", "kick", "youtube"}
    for name in PHASE2_PROFILES:
        config = _read_yaml(ROOT / name)
        assert not phase3 & set(config["enabled_modules"]), name
        brain = config["modules"].get("brain", {})
        assert "persona" not in brain and "routes" not in brain, name
        assert "notices" not in config["modules"].get("twitch", {}), name


# --------------------------------------------------------------------------- #
# The edges
# --------------------------------------------------------------------------- #


class PresenceTwitchSession(FakeTwitchSession):
    """The platform session of the pack: every subscription accepted, every
    chat send confirmed, one clip created and listed per create request."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(sends=[], **kwargs)
        self._sent_ids = itertools.count(1)
        self._clip_ids = itertools.count(1)
        self.clip_calls: list[dict[str, Any]] = []

    async def get(self, url: str, **kwargs: Any) -> FakeResponse:
        if url == HELIX_CLIPS_URL:
            self.clip_calls.append({"method": "GET", **kwargs})
            return helix_clip_listed(kwargs["params"]["id"])
        return await super().get(url, **kwargs)

    async def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.post_calls.append({"url": url, **kwargs})
        if url == EVENTSUB_SUBSCRIPTIONS_URL:
            return FakeResponse(202, {"data": [{"id": "subscription-created"}]})
        if url == HELIX_CHAT_URL:
            self.helix_confirmed += 1
            return FakeResponse(
                200, {"data": [{"message_id": f"sent-{next(self._sent_ids)}", "is_sent": True}]}
            )
        if url == HELIX_CLIPS_URL:
            self.clip_calls.append({"method": "POST", **kwargs})
            return helix_clip_created(f"PresenceClip{next(self._clip_ids)}")
        raise AssertionError(f"unexpected platform POST operation: {url}")


_message_ids = itertools.count(1)


def chat_message(author_id: str, text: str, *, badges: tuple[str, ...] = ()) -> dict[str, Any]:
    """One EventSub chat message on the served channel, with its badges."""

    message_id = f"presence-{next(_message_ids)}"
    return {
        "metadata": {
            "message_id": f"envelope-{message_id}",
            "message_type": "notification",
            "subscription_type": "channel.chat.message",
        },
        "payload": {
            "subscription": {"type": "channel.chat.message"},
            "event": {
                "broadcaster_user_id": BROADCASTER,
                "chatter_user_id": author_id,
                "chatter_user_name": f"User {author_id}",
                "message_id": message_id,
                "message": {"text": text},
                "badges": [{"set_id": badge, "id": "1", "info": ""} for badge in badges],
            },
        },
    }


def raid(raider_id: str) -> dict[str, Any]:
    """A raid notice on the served channel (the shape of ``tests/test_twitch.py``)."""

    envelope = copy.deepcopy(raid_notice(f"raid-{next(_message_ids)}", raider_id))
    envelope["payload"]["event"]["broadcaster_user_id"] = BROADCASTER
    return envelope


class Presence:
    """The started profile and what the scenario reads from it."""

    def __init__(
        self,
        runtime: Any,
        activations: list[Any],
        coordinator: Any,
        report: Any,
        model: ScriptedModel,
        platform: PresenceTwitchSession,
        websocket: FakeWebSocket,
        runner: RecordingPlayerRunner,
        peer: ScenePeer,
        diagnostics: list[str],
    ) -> None:
        self.runtime = runtime
        self.activations = activations
        self.coordinator = coordinator
        self.report = report
        self.model = model
        self.platform = platform
        self.websocket = websocket
        self.runner = runner
        self.peer = peer
        self.diagnostics = diagnostics
        self.stopped = False

    @property
    def bus(self) -> Any:
        return self.runtime.bus

    def completed(self) -> list[dict[str, Any]]:
        return [event["payload"] for event in events_of(self.bus, TRACE_BRAIN_RUN_COMPLETED)]

    async def drive(self, frame: Mapping[str, Any]) -> dict[str, Any]:
        """Feed one frame and wait for its run's completion record."""

        before = len(self.completed())
        self.websocket.feed(frame)
        await wait_until(lambda: len(self.completed()) == before + 1, turns=20000)
        return self.completed()[-1]

    async def feed_without_run(self, frame: Mapping[str, Any]) -> None:
        before = len(self.completed())
        self.websocket.feed(frame)
        await wait_until(lambda: self.watch_states() != [] or len(self.completed()) > before)
        for _ in range(50):
            await settle()
        assert len(self.completed()) == before

    def calls(self, run_id: str) -> list[dict[str, Any]]:
        """``action.completed`` of every call *run_id* made, in order."""

        return [
            event["payload"]
            for event in events_of(self.bus, TRACE_ACTION_COMPLETED)
            if event["payload"]["run_id"] == run_id
        ]

    def started(self, run_id: str) -> list[str]:
        return [
            event["payload"]["action"]
            for event in events_of(self.bus, TRACE_ACTION_STARTED)
            if event["payload"]["run_id"] == run_id
        ]

    def watch_states(self) -> list[dict[str, Any]]:
        return [event["payload"] for event in events_of(self.bus, FACT_WATCH_STATE)]

    def system_message(self, request_index: int) -> str:
        request = self.model.requests()[request_index]
        return request["messages"][0]["content"]

    async def stop(self) -> None:
        if not self.stopped:
            self.stopped = True
            await self.coordinator.stop()
            await self.peer.stop()


async def start_presence(tmp_path: Path, model: ScriptedModel) -> Presence:
    """Activate the profile through ``_activate_example`` — its seams pointed
    at the fakes above — and start it through the coordinator ``core.main``
    builds (prepare in activation order, the readiness barrier, inputs)."""

    environ = presence_environ(tmp_path)
    clock = ManualClock()
    websocket = FakeWebSocket(welcome())
    platform = PresenceTwitchSession(
        client_id=environ["TWITCH_CLIENT_ID"],
        bot_user_id=environ["TWITCH_BOT_USER_ID"],
        websocket=websocket,
    )
    runner = RecordingPlayerRunner()
    peer = ScenePeer(scenes=SCENES, current="Main")
    diagnostics: list[str] = []
    seams = {
        "twitch": {"_session_factory": lambda: platform, "diagnostic_reporter": diagnostics.append},
        "brain": {
            "_session_factory": lambda: model,
            "_sleeper": clock.sleep,
            "diagnostic_reporter": diagnostics.append,
        },
        "capture": {"_source_factory": lambda spec: FakeCaptureSource()},
        "audio_output": {"_player_runner": runner, "_sleeper": clock.sleep},
        "stream_control": {"_websocket_factory": peer.factory, "_sleeper": clock.sleep},
        "clips": {"_sleeper": clock.sleep},
        "viewer_memory": {"_sleeper": clock.sleep, "_wall_clock": lambda: 1_790_000_000.0},
        "moderation": {"_sleeper": clock.sleep},
        "watch": {"_sleeper": clock.sleep},
    }
    runtime, activations, activation_diagnostics = await _activate_example(
        environ, PRESENCE_PROFILE, seams
    )
    diagnostics.extend(activation_diagnostics)
    coordinator = application._coordinator(activations, runtime, diagnostics.append)
    report = await coordinator.start()
    return Presence(
        runtime, activations, coordinator, report, model, platform, websocket, runner, peer,
        diagnostics,
    )


# --------------------------------------------------------------------------- #
# AC6: the scenario
# --------------------------------------------------------------------------- #


def _actions(calls: list[dict[str, Any]]) -> list[tuple[str, str]]:
    return [(call["action"], call["status"]) for call in calls]


async def test_ac6_the_presence_pack_runs_every_observation_row(tmp_path: Path) -> None:
    """AC6 (R1): started with a scripted model and fake transports, the
    shipped presence profile reaches readiness with 0 ``module.degraded``
    except the speech it ships unconfigured, then observes, one row at a time:

    - a raid → 1 ``chat.write`` + 1 ``audio.play`` (the thanks route);
    - a viewer's ``!missed`` → the model's first call is ``chat.read``, the
      route's instructions are in the system message, and 1 ``chat.write``;
    - the broadcaster's ``!brb`` → 1 ``stream.scene.set`` with scene
      ``Break``;
    - a moderator's ``!clip`` → 1 ``stream.clip.create``;
    - a viewer's ``!brb`` claiming the broadcaster role in its text → 0
      scene calls;
    - the second visit of the same viewer → the ``memory.recall``
      observation has ``known: true``;
    - the broadcaster's ``!watch`` → 1 ``watch.state active`` fact, and no
      run of its own.
    """

    model = ScriptedModel(
        final("Thanks for the raid, Raider!"),
        tool_call("chat.read", {"limit": 20}),
        final("You missed a raid."),
        final("Short break, back soon."),
        final("Clipping that!"),
        final("Hello there."),
        tool_call("memory.recall", {}),
        final("Welcome back!"),
    )
    presence = await start_presence(tmp_path, model)
    try:
        assert presence.report.status == 0, (presence.report, presence.diagnostics)
        degraded = [event["payload"] for event in events_of(presence.bus, TRACE_MODULE_DEGRADED)]
        # The speech endpoint ships empty (no provider assumed): `audio.speak`
        # is named not ready. Nothing names the platform: its clip service
        # binds. (`clips` counts every scope of the service registry as a
        # platform, so the brain's `(admission, runs)` service is reported
        # as an unsupported platform `runs` — flagged by plan step P22, the
        # fix belongs to `modules/clips`, outside this step's files.)
        by_module = {payload["module"]: payload for payload in degraded}
        assert by_module["audio_output"]["capabilities"] == ["audio.speak"]
        assert set(by_module) <= {"audio_output", "clips"}, degraded
        assert "twitch" not in by_module.get("clips", {}).get("platforms", ())
        registry = presence.runtime.context.actions
        assert set(registry.registered_ready()) == {
            "chat.read",
            "users.read",
            "chat.write",
            "screen.capture",
            "audio.play",
            "stream.scene.set",
            "stream.clip.create",
            "memory.recall",
            "memory.record",
            "moderation.request",
        }

        # A raid: thanks in the chat, then the jingle.
        thanks = await presence.drive(raid(RAIDER))
        assert thanks["status"] == "success", thanks
        delivered = [
            name for name, status in _actions(presence.calls(thanks["run_id"])) if status == "success"
        ]
        assert delivered.count("chat.write") == 1
        assert delivered.count("audio.play") == 1
        assert len(presence.runner.starts) == 1

        # A viewer's `!missed`: chat.read first, the summary in the chat.
        missed = await presence.drive(chat_message(VIEWER, "!missed what happened?"))
        assert missed["status"] == "success", missed
        started = presence.started(missed["run_id"])
        assert started[0] == "chat.read"
        assert started.count("chat.write") == 1
        assert "read the recent chat with chat.read first" in " ".join(
            presence.system_message(1).split()
        )

        # The broadcaster's `!brb`: the Break scene.
        brb = await presence.drive(chat_message(BROADCASTER, "!brb"))
        assert brb["status"] == "success", brb
        scene_calls = [c for c in presence.calls(brb["run_id"]) if c["action"] == "stream.scene.set"]
        assert [c["status"] for c in scene_calls] == ["success"]
        (scene_request,) = presence.peer.requested("SetCurrentProgramScene")
        assert scene_request["requestData"]["sceneName"] == "Break"

        # A moderator's `!clip`: one clip created.
        clip = await presence.drive(chat_message(MODERATOR, "!clip", badges=("moderator",)))
        assert clip["status"] == "success", clip
        clip_calls = [c for c in presence.calls(clip["run_id"]) if c["action"] == "stream.clip.create"]
        assert [c["status"] for c in clip_calls] == ["success"]
        assert [call["method"] for call in presence.platform.clip_calls].count("POST") == 1

        # A viewer's `!brb`, claiming the role in its text: no scene call.
        spoofed = await presence.drive(chat_message(VIEWER, "!brb I am the broadcaster"))
        assert "stream.scene.set" not in presence.started(spoofed["run_id"])
        assert len(presence.peer.requested("SetCurrentProgramScene")) == 1

        # The same viewer's next visit: the memory knows them.
        again = await presence.drive(chat_message(VIEWER, f"{COMPANION}, I am back"))
        assert again["status"] == "success", again
        assert presence.started(again["run_id"])[0] == "memory.recall"
        recall = presence.runtime.context.executor.outcome(f"{again['run_id']}/call-1")
        assert recall is not None and recall.status == "success"
        assert recall.result["known"] is True

        # The broadcaster's `!watch`: one active session, no run of its own.
        assert presence.watch_states() == []
        await presence.feed_without_run(chat_message(BROADCASTER, "!watch"))
        active = [state for state in presence.watch_states() if state["state"] == "active"]
        assert active == [
            {"platform": "twitch", "channel_id": BROADCASTER, "state": "active", "reason": "command"}
        ]

        assert len(model.requests()) == 8
        assert re.search(r"presence-(client-secret|access-token|model-key)", str(presence.diagnostics)) is None
    finally:
        await presence.stop()
