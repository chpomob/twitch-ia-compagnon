"""The ``watch`` module: sessions, cadence, caps, validator and ``watch.state``
facts (phase 3 R6, plan step P16; AC29, AC30 validator half, AC31 cadence,
cap and ``max_active`` halves).

The module runs on a :func:`~conftest.runtime_context` whose
:class:`~conftest.ManualClock` is both its clock and (through ``sleep``) its
scheduler's sleeper; ticks are collected through the ``_on_tick`` seam, the
internal emit hook plan step P17 wires to the triggers. Chat commands are
published as schema-2 events of the fixture platform ``fake``. Time moves
only by :meth:`ManualClock.advance`, one step at a time; no positive-duration
sleep.
"""

from __future__ import annotations

import asyncio
import dataclasses
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from conftest import (
    FakeCaptureSource,
    FakeResponse,
    HeldSession,
    ManualClock,
    RecordingScheduler,
    ScriptedModel,
    events_of,
    final,
    png_bytes,
    runtime_context,
    settle,
    tool_call,
    wait_until,
)
from core.actions import AuthorizationPolicy, AuthorizationRule
from core.admission import AdmissionScheduler
from core.attachments import AttachmentStore
from core.context import ChatContext
from core.contracts import (
    TRACE_ACTION_STARTED,
    TRACE_BRAIN_ADMISSION_ACCEPTED,
    TRACE_INPUT_TRIGGER_ACCEPTED,
    TRACE_INPUT_TRIGGER_REJECTED,
    WILDCARD,
    Destination,
    SessionKey,
)
from core.lifecycle import PhaseCoordinator
from core.loader import ModuleLoader
from core.runtime import RuntimeContext
from core.triggers import TriggerDecision, TriggerRegistry
from modules import watch
from modules.watch import (
    COUNT_ADMITTED,
    COUNT_EMITTED,
    COUNT_NOT_ADMITTED,
    COUNT_SKIPPED_CAP,
    COUNT_SKIPPED_IN_FLIGHT,
    FACT_WATCH_STATE,
    TICK_AUTHOR,
    WatchModule,
    WatchModuleError,
    WatchSettings,
    activate,
    tick_event,
    validate_settings,
)
from test_brain import merge_settings


REPOSITORY = Path(__file__).resolve().parents[1]
START = 1000.0
PLATFORM = "fake"
CHANNEL = "chan-a"
OTHER_CHANNEL = "chan-b"
ROLES_PROVENANCE = "fake.badges"


class Watched:
    """One watch module on a manual clock, with the ticks it handed over."""

    def __init__(self, runtime: RuntimeContext, clock: ManualClock) -> None:
        self.runtime = runtime
        self.clock = clock
        self.ticks: list[tuple[str, str, float]] = []
        self.handle: WatchModule | None = None
        self._messages = 0

    def on_tick(self, platform: str, channel_id: str, instant: float) -> None:
        self.ticks.append((platform, channel_id, instant))

    def stamps(self, channel_id: str = CHANNEL) -> list[float]:
        return [instant for _platform, channel, instant in self.ticks if channel == channel_id]

    async def run(self, seconds: float, step: float = 1.0) -> None:
        """Advance the clock by *seconds*, *step* at a time, letting the loop run."""

        await settle()
        elapsed = 0.0
        while elapsed < seconds:
            self.clock.advance(step)
            elapsed += step
            await settle()

    async def say(
        self,
        text: str,
        *,
        roles: list[str] | None = None,
        provenance: str | None = ROLES_PROVENANCE,
        channel: str = CHANNEL,
        kind: str | None = None,
    ) -> None:
        self._messages += 1
        author: dict[str, Any] = {"id": f"viewer-{self._messages}", "display_name": "Someone"}
        if roles is not None:
            author["roles"] = list(roles)
            if provenance is not None:
                author["roles_provenance"] = provenance
        payload: dict[str, Any] = {
            "platform": PLATFORM,
            "channel_id": channel,
            "author": author,
            "message_id": f"message-{self._messages}",
            "text": text,
        }
        if kind is not None:
            payload["kind"] = kind
        await self.runtime.bus.publish(
            "channel.chat.message", payload, {"source": PLATFORM, "schema_version": 2}
        )
        await settle()

    def facts(self) -> list[tuple[str, str, str]]:
        return [
            (event["payload"]["channel_id"], event["payload"]["state"], event["payload"]["reason"])
            for event in events_of(self.runtime.bus, FACT_WATCH_STATE)
        ]


async def watched(*, started: bool = True, **settings: Any) -> Watched:
    clock = ManualClock(START)
    runtime = runtime_context(clock=clock)
    harness = Watched(runtime, clock)
    configured: dict[str, Any] = {
        "channels": [f"{PLATFORM}/{CHANNEL}", f"{PLATFORM}/{OTHER_CHANNEL}"],
        "_sleeper": clock.sleep,
        "_on_tick": harness.on_tick,
        **settings,
    }
    harness.handle = await activate(runtime.for_module("watch"), configured, {})
    await harness.handle.prepare()
    if started:
        await harness.handle.start_inputs()
        await settle()
    return harness


def max_in_any_hour(stamps: list[float]) -> int:
    """The most stamps inside one window ``(t − 3600, t]``, sliding over them."""

    return max(
        (sum(1 for other in stamps if stamp - 3600 < other <= stamp) for stamp in stamps),
        default=0,
    )


# -- the manifest -------------------------------------------------------------- #


def test_the_manifest_declares_an_input_with_the_event_kind_trigger_and_no_action() -> None:
    """R6: role ``input``, consumes chat messages, declares ``event_kind`` with
    default policy ``event_kind: [watch_tick]``, no action and no credential."""

    manifest = yaml.safe_load((REPOSITORY / "modules" / "watch" / "module.yaml").read_text())
    assert manifest["name"] == "watch"
    assert manifest["manifest_version"] == 2
    assert manifest["lifecycle"] == {"roles": ["input"]}
    assert manifest["produces"] == []
    assert manifest["consumes"] == ["channel.chat.message"]
    assert "actions" not in manifest and "credentials" not in manifest
    triggers = manifest["triggers"]
    assert [entry["name"] for entry in triggers["types"]] == ["event_kind"]
    assert triggers["default_policy"] == {
        "combination": "all_of",
        "rules": [{"type": "event_kind", "parameters": {"kinds": ["watch_tick"]}}],
    }
    assert callable(getattr(watch, manifest["settings_validator"]))


@pytest.mark.asyncio
async def test_the_loader_registers_the_default_policy_and_activates_the_handle(
    tmp_path: Path,
) -> None:
    """Discovery accepts the manifest: its trigger declaration is registered
    under ``watch`` and the handle carries the ``start_inputs`` an input needs."""

    root = tmp_path / "modules"
    shutil.copytree(
        REPOSITORY / "modules" / "watch",
        root / "watch",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    clock = ManualClock(START)
    registry = TriggerRegistry()
    runtime = runtime_context(clock=clock, trigger_registry=registry)
    loader = ModuleLoader(runtime.bus, root, context=runtime, environ={})
    activations = await loader.activate_enabled(
        {
            "enabled_modules": ["watch"],
            "modules": {"watch": {"channels": [f"{PLATFORM}/{CHANNEL}"], "limits": {}}},
        }
    )
    assert [activation.name for activation in activations] == ["watch"]
    spec = registry.spec("watch")
    assert spec is not None and spec.declares("event_kind")
    assert tuple(spec.default_policy.rules[0].parameters["kinds"]) == ("watch_tick",)
    await activations[0].handle.close()


# -- AC30: the validator ----------------------------------------------------------- #


def test_ac30_the_validator_refuses_each_bad_field_naming_it() -> None:
    """AC30 (validator half): ``interval_seconds: 14``, ``max_ticks_per_hour:
    241``, ``max_ticks_per_hour: 100`` with ``interval_seconds: 60`` (above
    3600 // 60 = 60), ``max_active_seconds: 59`` and 5 channels are each
    refused with one diagnostic naming the field. The ``--check-config``
    exit 2 is plan step P22's."""

    base = {"channels": [f"{PLATFORM}/{CHANNEL}"]}
    assert validate_settings(base) == []
    defaults = WatchSettings.from_mapping(base)
    assert (
        defaults.interval_seconds,
        defaults.max_ticks_per_hour,
        defaults.max_active_seconds,
        defaults.activation,
        defaults.start_command,
        defaults.stop_command,
        defaults.command_audience,
    ) == (60, 30, 3600, "command", "!watch", "!unwatch", "broadcaster")
    assert len(defaults.prompt_text) <= 500

    refused = [
        ({"interval_seconds": 14}, "interval_seconds"),
        ({"max_ticks_per_hour": 241}, "max_ticks_per_hour"),
        ({"max_ticks_per_hour": 100, "interval_seconds": 60}, "max_ticks_per_hour"),
        ({"max_active_seconds": 59}, "max_active_seconds"),
        ({"channels": [f"{PLATFORM}/c{index}" for index in range(5)]}, "channels"),
    ]
    for changes, field_name in refused:
        diagnostics = validate_settings({**base, **changes})
        assert len(diagnostics) == 1, (changes, diagnostics)
        assert f"'{field_name}'" in diagnostics[0], (changes, diagnostics)

    # The bounds themselves are accepted, and the cross-check reads the defaults.
    assert validate_settings({**base, "interval_seconds": 15, "max_ticks_per_hour": 240}) == []
    assert validate_settings({**base, "max_ticks_per_hour": 60}) == []
    assert validate_settings({**base, "max_ticks_per_hour": 61}) != []
    assert validate_settings({**base, "interval_seconds": 3600, "max_ticks_per_hour": 1}) == []
    assert validate_settings({**base, "interval_seconds": 3600}) != []
    assert validate_settings({**base, "max_active_seconds": 14400}) == []
    assert validate_settings({**base, "max_active_seconds": 14401}) != []
    assert validate_settings({"channels": [f"{PLATFORM}/c{index}" for index in range(4)]}) == []

    for bad in (
        {},
        {"channels": []},
        {"channels": ["no-separator"]},
        {"channels": [f"{PLATFORM}/{CHANNEL}", f"{PLATFORM}/{CHANNEL}"]},
        {**base, "activation": "always"},
        {**base, "command_audience": "everyone"},
        {**base, "command_audience": []},
        {**base, "command_audience": {}},
        {**base, "activation": []},
        {**base, "start_command": "!unwatch"},
        {**base, "stop_command": "two words"},
        {**base, "prompt_text": "x" * 501},
        {**base, "interval_seconds": True},
        {**base, "surprise": 1},
        {**base, "_sleeper": 3},
    ):
        assert validate_settings(bad) != [], bad
    assert validate_settings({**base, "prompt_text": "x" * 500}) == []


@pytest.mark.asyncio
async def test_refused_settings_fail_activation_without_echoing_a_value() -> None:
    runtime = runtime_context(clock=ManualClock(START))
    with pytest.raises(WatchModuleError) as raised:
        await activate(
            runtime.for_module("watch"),
            {"channels": ["fake/secret-channel"], "interval_seconds": 14},
            {},
        )
    assert "secret-channel" not in str(raised.value)
    assert "14" not in str(raised.value)


# -- AC29: sessions ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_ac29_without_activation_no_tick_is_emitted() -> None:
    """AC29: ``activation`` unset gives 0 ticks over 600 s and no fact."""

    h = await watched()
    await h.run(600)
    assert h.ticks == []
    assert h.facts() == []
    assert not h.handle.is_active(PLATFORM, CHANNEL)
    await h.handle.close()


@pytest.mark.asyncio
async def test_ac29_the_broadcasters_start_command_ticks_every_interval() -> None:
    """AC29: after ``!watch`` from the trusted broadcaster, 10 ticks over 600 s
    at 60 s, 60 s apart, on that channel only; ``watch.state active command``."""

    h = await watched()
    await h.say("!watch", roles=["broadcaster"])
    started = h.clock.now
    assert h.handle.is_active(PLATFORM, CHANNEL)
    assert h.facts() == [(CHANNEL, "active", "command")]
    await h.run(600)
    assert h.stamps() == [started + 60.0 * k for k in range(1, 11)]
    assert h.stamps(OTHER_CHANNEL) == []
    # No trigger engine and no scheduler in this harness: every tick is
    # emitted to the observer and none is admitted (plan step P17).
    assert h.handle.counts(PLATFORM, CHANNEL) == {
        COUNT_EMITTED: 10,
        COUNT_SKIPPED_CAP: 0,
        COUNT_SKIPPED_IN_FLIGHT: 0,
        COUNT_ADMITTED: 0,
        COUNT_NOT_ADMITTED: 10,
    }
    await h.handle.close()


@pytest.mark.asyncio
async def test_ac29_a_viewers_start_command_starts_nothing() -> None:
    """AC29: ``!watch`` from a viewer (no trusted role) starts nothing; nor do
    unattested roles, a moderator under the default audience, a notice, a
    command with extra text, or a channel that is not configured."""

    h = await watched()
    await h.say("!watch")
    await h.say("!watch", roles=["subscriber"])
    await h.say("!watch", roles=["broadcaster"], provenance=None)
    await h.say("!watch", roles=["moderator"])
    await h.say("!watch", roles=["broadcaster"], kind="raid")
    await h.say("!watch now", roles=["broadcaster"])
    await h.say(" !watch", roles=["broadcaster"])
    await h.say("!watch", roles=["broadcaster"], channel="chan-unwatched")
    await h.run(600)
    assert h.ticks == []
    assert h.facts() == []
    await h.handle.close()


@pytest.mark.asyncio
async def test_the_moderators_audience_accepts_a_trusted_moderator() -> None:
    h = await watched(command_audience="moderators")
    await h.say("!watch", roles=["vip"])
    assert h.facts() == []
    await h.say("!watch", roles=["moderator"])
    assert h.facts() == [(CHANNEL, "active", "command")]
    await h.run(120)
    assert len(h.stamps()) == 2
    await h.say("!unwatch", roles=["moderator"])
    assert h.facts()[-1] == (CHANNEL, "inactive", "command")
    await h.handle.close()


@pytest.mark.asyncio
async def test_ac29_the_stop_command_stops_the_ticks() -> None:
    """AC29: ``!unwatch`` from the audience gives 0 further ticks and
    ``watch.state inactive command``; a viewer's ``!unwatch`` stops nothing."""

    h = await watched()
    await h.say("!watch", roles=["broadcaster"])
    await h.run(180)
    assert len(h.stamps()) == 3
    await h.say("!unwatch")
    assert h.handle.is_active(PLATFORM, CHANNEL)
    await h.say("!unwatch", roles=["broadcaster"])
    assert not h.handle.is_active(PLATFORM, CHANNEL)
    before = list(h.ticks)
    await h.run(600)
    assert h.ticks == before
    assert h.facts() == [(CHANNEL, "active", "command"), (CHANNEL, "inactive", "command")]
    # A repeated command changes nothing and publishes nothing.
    await h.say("!unwatch", roles=["broadcaster"])
    assert len(h.facts()) == 2
    await h.handle.close()


@pytest.mark.asyncio
async def test_ac29_startup_activation_ticks_without_a_command() -> None:
    """AC29: ``activation: startup`` ticks without a command, on every channel,
    with ``watch.state active startup``; ``stop_inputs`` ends them
    (``inactive shutdown``) with 0 ticks after it."""

    h = await watched(activation="startup")
    assert h.facts() == [(CHANNEL, "active", "startup"), (OTHER_CHANNEL, "active", "startup")]
    await h.run(300)
    assert h.stamps() == [START + 60.0 * k for k in range(1, 6)]
    assert h.stamps(OTHER_CHANNEL) == h.stamps()
    await h.handle.stop_inputs()
    before = list(h.ticks)
    await h.run(300)
    assert h.ticks == before
    assert h.facts()[2:] == [
        (CHANNEL, "inactive", "shutdown"),
        (OTHER_CHANNEL, "inactive", "shutdown"),
    ]
    # After stop_inputs a start command is ignored.
    await h.say("!watch", roles=["broadcaster"])
    await h.run(120)
    assert h.ticks == before
    await h.handle.close()


@pytest.mark.asyncio
async def test_no_command_is_read_before_start_inputs() -> None:
    h = await watched(started=False)
    await h.say("!watch", roles=["broadcaster"])
    await h.run(120)
    assert h.ticks == [] and h.facts() == []
    await h.handle.start_inputs()
    await h.say("!watch", roles=["broadcaster"])
    await h.run(60)
    assert len(h.ticks) == 1
    await h.handle.close()


# -- AC31: the cap and max_active ---------------------------------------------------- #


@pytest.mark.asyncio
async def test_ac31_the_hourly_cap_holds_in_every_sliding_window() -> None:
    """AC31 (cap half): with ``interval_seconds: 15`` and ``max_ticks_per_hour:
    10``, at most 10 ticks in any 3600 s window ``(t − 3600, t]``, checked by
    sliding over the tick stamps; a tick over the cap is skipped and counted.

    The boundary: the first tick at ``t0 + 15`` leaves the window exactly at
    ``t0 + 3615``, so the tick due then is emitted, and the one due at
    ``t0 + 3600`` (the first still in the window) is skipped."""

    h = await watched(interval_seconds=15, max_ticks_per_hour=10, max_active_seconds=14400)
    await h.say("!watch", roles=["broadcaster"])
    t0 = h.clock.now
    await h.run(2 * 3600 + 60, step=15.0)
    stamps = h.stamps()
    assert max_in_any_hour(stamps) == 10
    assert stamps[:10] == [t0 + 15.0 * k for k in range(1, 11)]
    assert t0 + 3600.0 not in stamps
    assert stamps[10] == t0 + 3615.0
    counts = h.handle.counts(PLATFORM, CHANNEL)
    due = int((h.clock.now - t0) // 15)
    assert counts[COUNT_EMITTED] == len(stamps)
    assert counts[COUNT_EMITTED] + counts[COUNT_SKIPPED_CAP] == due
    assert counts[COUNT_SKIPPED_CAP] > 0
    assert len(h.handle.window(PLATFORM, CHANNEL)) <= 10
    await h.handle.close()


@pytest.mark.asyncio
async def test_ac31_the_hour_is_kept_across_sessions() -> None:
    """Stopping and starting again does not reset the channel's sliding hour."""

    h = await watched(interval_seconds=60, max_ticks_per_hour=3)
    await h.say("!watch", roles=["broadcaster"])
    await h.run(180)
    assert len(h.stamps()) == 3
    await h.say("!unwatch", roles=["broadcaster"])
    await h.say("!watch", roles=["broadcaster"])
    await h.run(600)
    assert len(h.stamps()) == 3
    assert h.handle.counts(PLATFORM, CHANNEL)[COUNT_SKIPPED_CAP] == 10
    await h.handle.close()


@pytest.mark.asyncio
async def test_ac31_a_session_ends_at_max_active() -> None:
    """AC31 (``max_active`` half): a session started at t0 with
    ``max_active_seconds: 600`` emits no tick after t0 + 600 and publishes
    ``watch.state inactive max_active``. The session lives in ``[t0, t0 +
    600)``: the end wins the tie with the tick due at t0 + 600."""

    h = await watched(max_active_seconds=600)
    await h.say("!watch", roles=["broadcaster"])
    t0 = h.clock.now
    await h.run(1200)
    stamps = h.stamps()
    assert stamps == [t0 + 60.0 * k for k in range(1, 10)]
    assert all(stamp <= t0 + 600 for stamp in stamps)
    assert h.facts() == [(CHANNEL, "active", "command"), (CHANNEL, "inactive", "max_active")]
    assert not h.handle.is_active(PLATFORM, CHANNEL)
    # A new command starts a new session.
    await h.say("!watch", roles=["broadcaster"])
    await h.run(60)
    assert len(h.stamps()) == 10
    await h.handle.close()


@pytest.mark.asyncio
async def test_a_cancelled_state_publication_leaves_the_session_scheduled() -> None:
    """A start whose ``watch.state`` publication is cancelled still owns a
    scheduler: the session ticks and ends at ``max_active``."""

    h = await watched(max_active_seconds=120)
    supervision = h.handle._supervision

    class Cancelling:
        async def emit(self, name: str, fact: Any) -> None:
            raise asyncio.CancelledError

    h.handle._supervision = Cancelling()
    with pytest.raises(asyncio.CancelledError):
        await h.handle._start(h.handle._channels[(PLATFORM, CHANNEL)], "command")
    h.handle._supervision = supervision
    t0 = h.clock.now
    assert h.handle.is_active(PLATFORM, CHANNEL)
    await h.run(180)
    assert h.stamps() == [t0 + 60.0]
    assert not h.handle.is_active(PLATFORM, CHANNEL)
    assert h.facts() == [(CHANNEL, "inactive", "max_active")]
    await h.handle.close()


@pytest.mark.asyncio
async def test_max_active_ends_a_session_between_two_ticks() -> None:
    """An end that falls between two due ticks is not delayed to the next one."""

    h = await watched(interval_seconds=3600, max_ticks_per_hour=1, max_active_seconds=90)
    await h.say("!watch", roles=["broadcaster"])
    t0 = h.clock.now
    await h.run(89)
    assert h.handle.is_active(PLATFORM, CHANNEL)
    await h.run(1)
    assert h.clock.now == t0 + 90
    assert not h.handle.is_active(PLATFORM, CHANNEL)
    assert h.facts()[-1] == (CHANNEL, "inactive", "max_active")
    assert h.ticks == []
    await h.handle.close()


@pytest.mark.asyncio
async def test_close_ends_active_sessions_and_leaves_no_task() -> None:
    h = await watched(activation="startup")
    await h.run(60)
    await h.handle.close()
    before = list(h.ticks)
    await h.run(300)
    assert h.ticks == before
    assert [fact[1:] for fact in h.facts()[2:]] == [("inactive", "shutdown")] * 2
    await h.say("!watch", roles=["broadcaster"])
    assert len(h.facts()) == 4


# --------------------------------------------------------------------------- #
# P17 — ticks through triggers and admission, the in-flight rule, the
# ``brain.watch`` principal end to end, and shutdown (AC31-in-flight, AC32, AC33)
# --------------------------------------------------------------------------- #

WATCH_PRINCIPAL = "brain.watch"
BRAIN_PRINCIPAL = "brain"
CHAT_WRITE = "chat.write"
SCREEN_CAPTURE = "screen.capture"
SCOPE_OF = {CHAT_WRITE: "chat", SCREEN_CAPTURE: "capture"}
COMPANION = "Companion"
VIEWER = "viewer-4"
FAKE_MODULE = "fakeplatform"
FAKE_CHANNEL = "fake-channel-1"
CAPTURE_SOURCE = "screen"
INTERVAL = 15
FIXTURE_MODULES = REPOSITORY / "tests" / "fixtures" / "modules"
VERTICAL_COPIES = ("chat_context", "users", "capture", "watch", "brain")
CHAT_LIMITS = {"max_messages": 16, "max_bytes": 8192, "max_age_seconds": 600.0, "max_channels": 8}
USERS_SETTINGS = {"max_channels": 8, "max_users_per_channel": 100, "max_age_seconds": 600.0}
STORE_LIMITS = {
    "max_object_bytes": 4 * 1024 * 1024,
    "max_objects": 16,
    "max_total_bytes": 16 * 1024 * 1024,
    "max_bytes_per_run": 8 * 1024 * 1024,
    "ttl_seconds": 30.0,
}
ADMISSION = {
    "session_queue_capacity": 4,
    "global_pending_capacity": 64,
    "max_sessions": 32,
    "workers": 4,
    "wait_seconds": 30,
    "total_run_seconds": 120,
}
WATCH_SESSION = SessionKey(platform=PLATFORM, channel_id=FAKE_CHANNEL, viewer_id=TICK_AUTHOR)


def grant(action: str, *principals: str) -> AuthorizationRule:
    return AuthorizationRule(
        rule_id=f"{action}-{'-'.join(principals)}",
        action_name=action,
        destination=Destination(WILDCARD, WILDCARD, SCOPE_OF[action]),
        principals=principals,
        granted_permissions=(action,),
    )


@pytest.fixture(scope="module")
def vertical_modules(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Copies of the shipped packages the scenario enables, plus the fake platform."""

    directory = tmp_path_factory.mktemp("modules")
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
    for name in VERTICAL_COPIES:
        shutil.copytree(REPOSITORY / "modules" / name, directory / name, ignore=ignore)
    shutil.copytree(FIXTURE_MODULES / FAKE_MODULE, directory / FAKE_MODULE, ignore=ignore)
    return directory


class Vertical:
    """The fake platform, the transcript, the users directory, the capture
    module, ``watch`` and the brain, discovered and activated by the real
    loader on one runtime whose shared admission scheduler runs the brain —
    so the platform and ``watch`` both admit directly, as twitch does."""

    def __init__(
        self,
        context: RuntimeContext,
        clock: ManualClock,
        scheduler: AdmissionScheduler,
        session: Any,
        source: FakeCaptureSource,
        handles: dict[str, Any],
        *,
        shared: bool = True,
    ) -> None:
        self.context = context
        self.clock = clock
        self.scheduler = scheduler
        self.shared = shared
        self.session = session
        self.source = source
        self.handles = handles
        self.in_flight_samples: list[int] = []

    @property
    def watch(self) -> WatchModule:
        return self.handles["watch"]

    async def run(self, seconds: float, step: float = 1.0) -> None:
        """Advance the clock *step* at a time, sampling the watch runs in flight."""

        elapsed = 0.0
        while elapsed < seconds:
            self.clock.advance(step)
            elapsed += step
            await settle()
            self.in_flight_samples.append(self.watch_runs_in_flight())

    def watch_runs_in_flight(self) -> int:
        """Admitted watch runs without a terminal record, read from the traces."""

        admitted = {
            event["payload"]["run_id"]
            for event in events_of(self.context.bus, TRACE_BRAIN_ADMISSION_ACCEPTED)
            if event["payload"]["session"] == WATCH_SESSION.serialize()
        }
        return len(admitted - set(self.scheduler.run_records()))

    def records(self, session: SessionKey | None = None) -> list[Any]:
        return [
            record
            for record in self.scheduler.run_records().values()
            if session is None or record.session == session.serialize()
        ]

    async def completed(self, count: int) -> None:
        await wait_until(lambda: len(self.scheduler.run_records()) >= count)

    async def ask(self, message_id: str) -> None:
        published = await self.handles[FAKE_MODULE].inject(
            {
                "channel_id": FAKE_CHANNEL,
                "author": {"id": VIEWER, "display_name": "Viewer"},
                "message_id": message_id,
                "text": f"{COMPANION}, what is happening?",
            }
        )
        assert published is not None

    def tools(self, index: int) -> list[str]:
        body = self.session.post_calls[index]["json"]
        return [tool["function"]["name"] for tool in body.get("tools", [])]

    def calls(self, run_id: str) -> list[dict[str, Any]]:
        """``action.started`` of every executor call *run_id* made, in order."""

        return [
            event["payload"]
            for event in events_of(self.context.bus, TRACE_ACTION_STARTED)
            if event["payload"]["run_id"] == run_id
        ]

    def entries(self) -> tuple[Any, Any]:
        """The chat transcript's channels and the users directory's channels."""

        return self.context.chat.live_channels(), self.handles["users"].directory.channels()

    async def close(self) -> None:
        for name in ("watch", FAKE_MODULE):
            await self.handles[name].stop_inputs()
        if self.shared:
            await self.scheduler.aclose()
        for handle in reversed(list(self.handles.values())):
            await handle.close()


async def vertical(
    modules_directory: Path,
    session: Any,
    rules: list[AuthorizationRule],
    *,
    shared: bool = True,
) -> Vertical:
    """The scenario on a shared scheduler, or — ``shared=False`` — wired as
    ``core.main`` wires it: no scheduler on the context, the brain owning
    and publishing its own, ``watch`` resolving it from the services."""

    clock = ManualClock(START)
    store = AttachmentStore(clock=clock, **STORE_LIMITS)
    base = runtime_context(
        clock=clock,
        authorization=AuthorizationPolicy(rules),
        attachments=store,
        trigger_registry=TriggerRegistry(companion_name=COMPANION),
        chat=ChatContext(clock=clock, **CHAT_LIMITS),
    )
    handles: dict[str, Any] = {}

    async def run_body(run: Any) -> Any:
        return await handles["brain"].run(run)

    scheduler = AdmissionScheduler(
        run_body,
        **ADMISSION,
        clock=clock,
        sleeper=clock.sleep,
        supervision=base.supervision,
        run_module="brain",
        run_cleanup=store.release,
    )
    context = dataclasses.replace(base, scheduler=scheduler) if shared else base
    source = FakeCaptureSource(data=png_bytes(2, 2), width=2, height=2)
    brain_settings = merge_settings(None)
    brain_settings.update({"_session_factory": lambda: session, "_sleeper": clock.sleep})
    modules: dict[str, dict[str, Any]] = {
        FAKE_MODULE: {
            "channel_ids": [FAKE_CHANNEL],
            "companion_name": COMPANION,
            "transport": SimpleNamespace(outcomes=[], sends=[]),
        },
        "chat_context": {"limits": {"chat_context": dict(CHAT_LIMITS)}},
        "users": dict(USERS_SETTINGS),
        "capture": {
            "sources": {CAPTURE_SOURCE: {"kind": "file", "path": "/nonexistent/never-read.png"}},
            "default_source": CAPTURE_SOURCE,
            "_source_factory": lambda spec: source,
        },
        "watch": {
            "channels": [f"{PLATFORM}/{FAKE_CHANNEL}"],
            "interval_seconds": INTERVAL,
            "activation": "startup",
            "_sleeper": clock.sleep,
        },
        "brain": brain_settings,
    }
    loader = ModuleLoader(context.bus, modules_directory, context=context, environ={})
    activations = await loader.activate_enabled(
        {"enabled_modules": list(modules), "modules": modules}
    )
    handles.update({activation.name: activation.handle for activation in activations})
    if not shared:
        scheduler = handles["brain"].scheduler
    harness = Vertical(context, clock, scheduler, session, source, handles, shared=shared)
    try:
        if shared:
            await scheduler.start()
        for handle in handles.values():
            await handle.prepare()
        for name in (FAKE_MODULE, "watch"):
            await handles[name].start_inputs()
        await settle()
    except BaseException:
        await harness.close()
        raise
    return harness


# -- the tick event, the decision and the admission ---------------------------- #


def test_the_tick_is_a_normalised_watch_tick_of_the_reserved_author() -> None:
    """R6: kind ``watch_tick``, text ``prompt_text``, author ``system:watch``,
    source ``watch``, schema version 2, a per-channel sequence message id."""

    event = tick_event(PLATFORM, CHANNEL, 3, "Look.")
    assert event["metadata"] == {"source": "watch", "schema_version": 2}
    assert event["payload"] == {
        "platform": PLATFORM,
        "channel_id": CHANNEL,
        "author": {"id": "system:watch", "display_name": "watch"},
        "message_id": "watch-3",
        "text": "Look.",
        "kind": "watch_tick",
    }
    assert event["type"] != "channel.chat.message"


@pytest.mark.asyncio
async def test_an_accepted_tick_is_decided_traced_and_admitted_never_published() -> None:
    """R6: each tick is evaluated with the module's own default policy, the
    decision is traced, and the accepted tick is admitted directly under the
    session ``(platform, channel, system:watch)``; nothing is published as
    ``channel.chat.message`` and message ids are monotonic per channel."""

    clock = ManualClock(START)
    registry = TriggerRegistry()
    recording = RecordingScheduler()
    runtime = runtime_context(clock=clock, trigger_registry=registry, scheduler=recording)
    root = REPOSITORY / "modules"
    loader = ModuleLoader(runtime.bus, root, context=runtime, environ={})
    (activation,) = await loader.activate_enabled(
        {
            "enabled_modules": ["watch"],
            "modules": {
                "watch": {
                    "channels": [f"{PLATFORM}/{CHANNEL}", f"{PLATFORM}/{OTHER_CHANNEL}"],
                    "activation": "startup",
                    "prompt_text": "What is on screen?",
                    "_sleeper": clock.sleep,
                }
            },
        }
    )
    handle = activation.handle
    await handle.prepare()
    await handle.start_inputs()
    await settle()
    for _ in range(180):
        clock.advance(1)
        await settle()
    await handle.close()

    assert len(recording.admissions) == 6
    by_channel: dict[str, list[str]] = {}
    for session_key, work in recording.admissions:
        assert session_key.viewer_id == TICK_AUTHOR
        assert work.kind == "watch.tick"
        payload = work.payload["payload"]
        assert payload["kind"] == "watch_tick"
        assert payload["text"] == "What is on screen?"
        assert payload["author"]["id"] == TICK_AUTHOR
        assert work.source_event_id == payload["message_id"]
        by_channel.setdefault(session_key.channel_id, []).append(payload["message_id"])
    assert by_channel == {
        CHANNEL: ["watch-1", "watch-2", "watch-3"],
        OTHER_CHANNEL: ["watch-1", "watch-2", "watch-3"],
    }
    accepted = events_of(runtime.bus, TRACE_INPUT_TRIGGER_ACCEPTED)
    assert [event["payload"]["input"] for event in accepted] == ["watch"] * 6
    assert events_of(runtime.bus, "channel.chat.message") == []
    assert handle.counts(PLATFORM, CHANNEL)[COUNT_ADMITTED] == 3


@pytest.mark.asyncio
async def test_a_rejected_tick_is_traced_and_never_admitted() -> None:
    """A refused decision leaves 0 admissions, one traced rejection per tick
    and the tick counted as not admitted."""

    class Refusing:
        def __init__(self) -> None:
            self.events: list[Any] = []

        def evaluate(self, event: Any, **_: Any) -> TriggerDecision:
            self.events.append(event)
            payload = event["payload"]
            return TriggerDecision(
                accepted=False,
                reason="rejected:test",
                policy_version=None,
                source_event_id=payload["message_id"],
                input_name=event["metadata"]["source"],
                platform=payload["platform"],
                channel_id=payload["channel_id"],
            )

    clock = ManualClock(START)
    engine = Refusing()
    recording = RecordingScheduler()
    runtime = runtime_context(clock=clock, triggers=engine, scheduler=recording)
    h = Watched(runtime, clock)
    h.handle = await activate(
        runtime.for_module("watch"),
        {"channels": [f"{PLATFORM}/{CHANNEL}"], "activation": "startup", "_sleeper": clock.sleep},
        {},
    )
    await h.handle.prepare()
    await h.handle.start_inputs()
    await h.run(120)
    await h.handle.close()
    assert len(engine.events) == 2
    assert recording.admissions == []
    assert len(events_of(runtime.bus, TRACE_INPUT_TRIGGER_REJECTED)) == 2
    assert h.handle.counts(PLATFORM, CHANNEL)[COUNT_NOT_ADMITTED] == 2


# -- AC31: the in-flight rule ----------------------------------------------------- #


@pytest.mark.asyncio
async def test_ac31_a_tick_due_while_the_watch_run_is_running_is_skipped(
    vertical_modules: Path,
) -> None:
    """AC31 (in-flight half): a held scripted model keeps the first watch run
    running; the next due tick is skipped (skip counter + 1), no second watch
    run is admitted, and at every step at most one watch run is in flight.
    Once the run ended, the next due tick is admitted again."""

    session = HeldSession(FakeResponse(200, final("First look.")), FakeResponse(200, final("Second look.")))
    h = await vertical(
        vertical_modules, session, [grant(CHAT_WRITE, BRAIN_PRINCIPAL, WATCH_PRINCIPAL)]
    )
    try:
        await h.run(INTERVAL)
        await wait_until(session.entered.is_set)
        assert h.watch.in_flight(PLATFORM, FAKE_CHANNEL) == 1
        assert h.watch_runs_in_flight() == 1

        await h.run(INTERVAL)
        counts = h.watch.counts(PLATFORM, FAKE_CHANNEL)
        assert counts[COUNT_SKIPPED_IN_FLIGHT] == 1
        assert counts[COUNT_ADMITTED] == 1
        assert counts[COUNT_EMITTED] == 1
        assert h.records(WATCH_SESSION) == []
        assert len(session.post_calls) == 1

        session.release.set()
        await h.completed(1)
        (first,) = h.records(WATCH_SESSION)
        assert first.status == "success"
        assert h.watch.in_flight(PLATFORM, FAKE_CHANNEL) == 0

        await h.run(INTERVAL)
        await h.completed(2)
        counts = h.watch.counts(PLATFORM, FAKE_CHANNEL)
        assert (counts[COUNT_ADMITTED], counts[COUNT_SKIPPED_IN_FLIGHT]) == (2, 1)
        assert max(h.in_flight_samples) == 1
    finally:
        session.release.set()
        await h.close()


class EvictingScheduler(RecordingScheduler):
    """Keeps no terminal record at all, as if each were evicted at once, and
    reports another session's run executing throughout; the watch session's
    own run is executing only while ``running`` names it."""

    def __init__(self) -> None:
        super().__init__()
        self.running: str | None = None

    active_runs = 3

    def run_record(self, run_id: str) -> Any:
        return None

    def queue_depth(self, session_key: Any) -> int:
        return 0

    def active_run(self, session_key: Any) -> str | None:
        return self.running if session_key == WATCH_SESSION_OF_CHANNEL else "other-run"


WATCH_SESSION_OF_CHANNEL = SessionKey(platform=PLATFORM, channel_id=CHANNEL, viewer_id=TICK_AUTHOR)


@pytest.mark.asyncio
async def test_an_evicted_record_ends_the_run_despite_other_sessions_running() -> None:
    """The in-flight rule is per session: with the terminal record evicted,
    the watch run counts as ended once its own session holds nothing queued
    and runs nothing, however busy other sessions keep the scheduler."""

    clock = ManualClock(START)
    evicting = EvictingScheduler()
    runtime = runtime_context(clock=clock, trigger_registry=TriggerRegistry(), scheduler=evicting)
    loader = ModuleLoader(runtime.bus, REPOSITORY / "modules", context=runtime, environ={})
    (activation,) = await loader.activate_enabled(
        {
            "enabled_modules": ["watch"],
            "modules": {
                "watch": {
                    "channels": [f"{PLATFORM}/{CHANNEL}"],
                    "activation": "startup",
                    "_sleeper": clock.sleep,
                }
            },
        }
    )
    handle = activation.handle
    await handle.prepare()
    await handle.start_inputs()
    try:
        for _ in range(180):
            clock.advance(1)
            await settle()
        counts = handle.counts(PLATFORM, CHANNEL)
        assert (counts[COUNT_ADMITTED], counts[COUNT_SKIPPED_IN_FLIGHT]) == (3, 0)

        # While the watch session's own run executes, the next tick is held.
        evicting.running = f"run-{len(evicting.admissions)}"
        for _ in range(60):
            clock.advance(1)
            await settle()
        counts = handle.counts(PLATFORM, CHANNEL)
        assert (counts[COUNT_ADMITTED], counts[COUNT_SKIPPED_IN_FLIGHT]) == (3, 1)
    finally:
        await handle.close()


@pytest.mark.asyncio
async def test_watch_admits_into_the_brain_scheduler_when_the_context_has_none(
    vertical_modules: Path,
) -> None:
    """Wired as ``core.main`` wires it — no scheduler on the context — the
    brain owns its scheduler and publishes it; ``watch`` resolves it and the
    accepted tick runs under ``brain.watch`` rather than being dropped."""

    session = ScriptedModel(final("Quiet stream."))
    h = await vertical(
        vertical_modules,
        session,
        [grant(CHAT_WRITE, BRAIN_PRINCIPAL, WATCH_PRINCIPAL)],
        shared=False,
    )
    try:
        assert h.context.scheduler is None
        assert h.handles["brain"].owns_scheduler
        await h.run(INTERVAL)
        await h.completed(1)
        (watch_run,) = h.records(WATCH_SESSION)
        assert watch_run.status == "success"
        assert {call["principal"] for call in h.calls(watch_run.run_id)} == {WATCH_PRINCIPAL}
        counts = h.watch.counts(PLATFORM, FAKE_CHANNEL)
        assert (counts[COUNT_ADMITTED], counts[COUNT_NOT_ADMITTED]) == (1, 0)
    finally:
        await h.close()


# -- AC32: the brain.watch principal end to end ----------------------------------- #


@pytest.mark.asyncio
async def test_ac32_a_tick_feeds_nothing_and_a_brain_only_rule_offers_it_no_capture(
    vertical_modules: Path,
) -> None:
    """AC32: a tick yields 0 chat-context and 0 users-directory entries; with
    ``screen.capture`` granted to ``brain`` only, the watch run is offered no
    capture and makes 0 capture calls (the capture source is never read),
    while a chat run of the same channel is offered it and captures."""

    session = ScriptedModel(
        final("Quiet stream."),
        tool_call(SCREEN_CAPTURE, {}),
        final("Here is the stream."),
    )
    h = await vertical(
        vertical_modules,
        session,
        [
            grant(CHAT_WRITE, BRAIN_PRINCIPAL, WATCH_PRINCIPAL),
            grant(SCREEN_CAPTURE, BRAIN_PRINCIPAL),
        ],
    )
    try:
        await h.run(INTERVAL)
        await h.completed(1)
        (watch_run,) = h.records(WATCH_SESSION)
        assert watch_run.status == "success"
        assert h.entries() == ((), ())
        assert h.tools(0) == []
        assert h.source.calls == []
        assert [call["action"] for call in h.calls(watch_run.run_id)] == [CHAT_WRITE]
        assert {call["principal"] for call in h.calls(watch_run.run_id)} == {WATCH_PRINCIPAL}
        assert events_of(h.context.bus, "channel.chat.message") == []

        await h.ask("chat-1")
        await h.completed(2)
        (chat_run,) = h.records(SessionKey(platform=PLATFORM, channel_id=FAKE_CHANNEL, viewer_id=VIEWER))
        assert h.tools(1) == [SCREEN_CAPTURE]
        assert len(h.source.calls) == 1
        assert [(call["action"], call["principal"]) for call in h.calls(chat_run.run_id)] == [
            (SCREEN_CAPTURE, BRAIN_PRINCIPAL),
            (CHAT_WRITE, BRAIN_PRINCIPAL),
        ]
    finally:
        await h.close()


@pytest.mark.asyncio
async def test_ac32_a_rule_naming_brain_watch_captures_through_the_chat_runs_provider(
    vertical_modules: Path,
) -> None:
    """AC32: with ``screen.capture`` granted to ``brain.watch`` (and to
    ``brain``), the watch run is offered the capture and invokes the provider
    once under ``brain.watch``; a chat run captures through the very same
    provider under ``brain``. The tick still feeds nothing."""

    session = ScriptedModel(
        tool_call(SCREEN_CAPTURE, {}),
        final("Nice view."),
        tool_call(SCREEN_CAPTURE, {}),
        final("Here is the stream."),
    )
    h = await vertical(
        vertical_modules,
        session,
        [
            grant(CHAT_WRITE, BRAIN_PRINCIPAL, WATCH_PRINCIPAL),
            grant(SCREEN_CAPTURE, BRAIN_PRINCIPAL, WATCH_PRINCIPAL),
        ],
    )
    try:
        await h.run(INTERVAL)
        await h.completed(1)
        (watch_run,) = h.records(WATCH_SESSION)
        assert watch_run.status == "success"
        assert h.tools(0) == [SCREEN_CAPTURE]
        assert len(h.source.calls) == 1
        watch_calls = h.calls(watch_run.run_id)
        assert [(call["action"], call["principal"]) for call in watch_calls] == [
            (SCREEN_CAPTURE, WATCH_PRINCIPAL),
            (CHAT_WRITE, WATCH_PRINCIPAL),
        ]
        assert h.entries() == ((), ())

        await h.ask("chat-1")
        await h.completed(2)
        (chat_run,) = h.records(SessionKey(platform=PLATFORM, channel_id=FAKE_CHANNEL, viewer_id=VIEWER))
        chat_calls = h.calls(chat_run.run_id)
        assert chat_calls[0]["action"] == SCREEN_CAPTURE
        assert chat_calls[0]["principal"] == BRAIN_PRINCIPAL
        assert len(h.source.calls) == 2
        (binding,) = h.context.actions.bindings(SCREEN_CAPTURE)
        assert binding.module == "capture"
        assert watch_calls[0]["provider"] == chat_calls[0]["provider"] == binding.provider_name
    finally:
        await h.close()


# -- AC33: shutdown during an active session --------------------------------------- #


@pytest.mark.asyncio
async def test_ac33_shutdown_stops_the_ticks_within_the_global_deadline() -> None:
    """AC33: the coordinator's shutdown during an active session stops the
    ticks at ``stop_inputs`` (0 ticks, 0 decisions and 0 admissions after
    it), publishes ``watch.state inactive`` reason ``shutdown`` and completes
    within the global shutdown deadline on the manual clock."""

    clock = ManualClock(START)
    recording = RecordingScheduler()
    runtime = runtime_context(clock=clock, trigger_registry=TriggerRegistry(), scheduler=recording)
    ticks: list[float] = []
    loader = ModuleLoader(runtime.bus, REPOSITORY / "modules", context=runtime, environ={})
    activations = await loader.activate_enabled(
        {
            "enabled_modules": ["watch"],
            "modules": {
                "watch": {
                    "channels": [f"{PLATFORM}/{CHANNEL}"],
                    "activation": "startup",
                    "_sleeper": clock.sleep,
                    "_on_tick": lambda _platform, _channel, instant: ticks.append(instant),
                }
            },
        }
    )
    deadline = 10.0
    coordinator = PhaseCoordinator(
        activations,
        tasks=runtime.tasks,
        clock=clock,
        sleeper=clock.sleep,
        shutdown_deadline_seconds=deadline,
    )
    await coordinator.start()
    handle = activations[0].handle
    for _ in range(150):
        clock.advance(1)
        await settle()
    assert handle.is_active(PLATFORM, CHANNEL)
    assert len(ticks) == len(recording.admissions) == 2

    stopping_at = clock()
    stop = asyncio.ensure_future(coordinator.stop())
    await wait_until(stop.done)
    report = stop.result()
    assert clock() - stopping_at <= deadline
    assert not any("watch" in failure for failure in report.failures)
    assert not handle.is_active(PLATFORM, CHANNEL)

    decisions = len(events_of(runtime.bus, TRACE_INPUT_TRIGGER_ACCEPTED))
    for _ in range(600):
        clock.advance(1)
        await settle(2)
    assert len(ticks) == len(recording.admissions) == 2
    assert len(events_of(runtime.bus, TRACE_INPUT_TRIGGER_ACCEPTED)) == decisions
    facts = [
        (event["payload"]["state"], event["payload"]["reason"])
        for event in events_of(runtime.bus, FACT_WATCH_STATE)
    ]
    assert facts == [("active", "startup"), ("inactive", "shutdown")]
    assert runtime.tasks.active == 0
