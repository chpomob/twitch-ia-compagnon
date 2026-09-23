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
import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml

from conftest import ManualClock, events_of, runtime_context, settle
from core.loader import ModuleLoader
from core.runtime import RuntimeContext
from core.triggers import TriggerRegistry
from modules import watch
from modules.watch import (
    COUNT_EMITTED,
    COUNT_SKIPPED_CAP,
    FACT_WATCH_STATE,
    WatchModule,
    WatchModuleError,
    WatchSettings,
    activate,
    validate_settings,
)


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
    assert h.handle.counts(PLATFORM, CHANNEL) == {COUNT_EMITTED: 10, COUNT_SKIPPED_CAP: 0}
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
