"""The ``timer`` input: manifest, settings, the resolved-policy check, the
firing loop, evaluation and admission (phase 8 R8, plan step P11; AC29,
AC30, AC31).

The module is discovered and activated by the real loader over the shipped
``modules/`` directory, on a :func:`~conftest.runtime_context` whose
:class:`~conftest.ManualClock` is the core's clock and whose sleep is the
loops' ``_sleeper``. The host's local minute is the ``_local_minute`` seam,
so no test depends on the machine's timezone, and every built event reaches
the ``_on_fire`` observer before it is decided. Time moves only by
:meth:`ManualClock.advance`, one minute at a time; no positive-duration
sleep. Probability draws come from the context's random source — a
:class:`~conftest.RecordedRandom` where the draws are counted.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml

from conftest import (
    ManualClock,
    RecordedRandom,
    RecordingScheduler,
    events_of,
    runtime_context,
    settle,
)
from core.contracts import (
    EVENT_KINDS,
    TRACE_INPUT_TRIGGER_ACCEPTED,
    TRACE_INPUT_TRIGGER_REJECTED,
    TRACE_MODULE_DEGRADED,
)
from core.loader import ModuleLoader, ModuleLoadError
from core.triggers import TriggerRegistry
import modules.timer as timer
from modules.timer import (
    COUNT_ADMITTED,
    COUNT_FIRED,
    COUNT_NOT_ADMITTED,
    TimerModule,
    validate_settings,
)


REPOSITORY = Path(__file__).resolve().parents[1]
MODULES = REPOSITORY / "modules"
START = 1000.0
PLATFORM = "fake"
CHANNEL_A = "chan-a"
CHANNEL_B = "chan-b"
PROMPT_A = "Say something about the stream so far."
PROMPT_B = "Ask the chat a question."


def channel(channel_id: str, prompt_text: str, platform: str = PLATFORM) -> dict[str, Any]:
    return {
        "destination": {"platform": platform, "channel_id": channel_id},
        "prompt_text": prompt_text,
    }


def interval_policy(**parameters: Any) -> dict[str, Any]:
    return {"combination": "all_of", "rules": [{"type": "interval", "parameters": parameters}]}


def configured(policies: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """The ``triggers`` block selecting *policies* per channel."""

    return {"timer": {"channels": policies}}


class Timed:
    """One timer module activated by the real loader on a manual clock."""

    def __init__(self, runtime: Any, clock: ManualClock, scheduler: RecordingScheduler,
                 handle: TimerModule, minute: list[int]) -> None:
        self.runtime = runtime
        self.clock = clock
        self.scheduler = scheduler
        self.handle = handle
        self.minute = minute
        self.fired: list[dict[str, Any]] = []

    async def advance_minutes(self, minutes: int) -> None:
        """Move the clock one minute at a time, letting each loop run."""

        for _ in range(minutes):
            self.clock.advance(60)
            await settle()

    def admitted(self) -> list[dict[str, Any]]:
        return [work.payload for _session, work in self.scheduler.admissions]

    def decisions(self) -> list[dict[str, Any]]:
        return [
            event["payload"]
            for event_type in (TRACE_INPUT_TRIGGER_ACCEPTED, TRACE_INPUT_TRIGGER_REJECTED)
            for event in events_of(self.runtime.bus, event_type)
        ]


async def timed(
    *,
    channels: list[dict[str, Any]] | None = None,
    triggers: dict[str, Any] | None = None,
    rng: Any = None,
    minute: int = 720,
    prepared: bool = True,
    started: bool = True,
    shared: bool = True,
) -> Timed:
    """The timer, enabled alone, activated by the loader; prepared and
    started if asked.

    ``shared=False`` wires it as ``core.main`` does: no scheduler on the
    context, the brain's own published as the ``(admission, runs)`` service.
    """

    clock = ManualClock(START)
    recording = RecordingScheduler()
    runtime = runtime_context(
        clock=clock,
        trigger_registry=TriggerRegistry(),
        scheduler=recording if shared else None,
        rng=rng,
    )
    if not shared:
        runtime.services.publish("admission", "runs", recording, module="brain")
    current = [minute]
    holder: list[Timed] = []

    def on_fire(event: Any) -> None:
        holder[0].fired.append(event)

    settings = {
        "channels": channels
        if channels is not None
        else [channel(CHANNEL_A, PROMPT_A), channel(CHANNEL_B, PROMPT_B)],
        "_sleeper": clock.sleep,
        "_local_minute": lambda: current[0],
        "_on_fire": on_fire,
    }
    config: dict[str, Any] = {"enabled_modules": ["timer"], "modules": {"timer": settings}}
    if triggers is not None:
        config["triggers"] = triggers
    loader = ModuleLoader(runtime.bus, MODULES, context=runtime, environ={})
    (activation,) = await loader.activate_enabled(config)
    harness = Timed(runtime, clock, recording, activation.handle, current)
    holder.append(harness)
    if prepared:
        await harness.handle.prepare()
    if started:
        await harness.handle.start_inputs()
        await settle()
    return harness


# -- the manifest ---------------------------------------------------------------- #


def test_the_manifest_declares_an_input_with_its_interval_type() -> None:
    """R8: role ``input``, produces the chat event, the declared fields, the
    declared ``interval`` type (kinds ``[timer]``, its schema, its
    ``probability_parameter`` and its window rule) and the default policy
    ``interval{every_minutes: 30, probability: 0.25}``."""

    manifest = yaml.safe_load((MODULES / "timer" / "module.yaml").read_text())
    assert manifest["name"] == "timer"
    assert manifest["manifest_version"] == 2
    assert manifest["lifecycle"] == {"roles": ["input"]}
    assert manifest["produces"] == ["channel.chat.message"]
    assert "credentials" not in manifest and "actions" not in manifest
    assert callable(getattr(timer, manifest["settings_validator"]))
    properties = manifest["settings_schema"]["properties"]
    assert set(properties) == {"channels"}
    assert set(properties["channels"]["items"]["properties"]) == {"destination", "prompt_text"}
    triggers = manifest["triggers"]
    assert triggers["fields"] == {"timer.minute_of_day": "integer", "timer.sequence": "integer"}
    types = {entry["name"]: entry for entry in triggers["types"]}
    assert set(types) == {"event_kind", "interval"}
    interval = types["interval"]
    assert interval["kinds"] == ["timer"]
    assert "hook" not in interval
    assert interval["probability_parameter"] == "probability"
    schema = interval["parameter_schema"]
    assert schema["required"] == ["every_minutes", "probability"]
    every = schema["properties"]["every_minutes"]
    assert (every["type"], every["minimum"], every["maximum"]) == ("integer", 1, 1440)
    probability = schema["properties"]["probability"]
    assert (probability["type"], probability["minimum"], probability["maximum"]) == (
        "number", 0, 1,
    )
    window = schema["properties"]["window"]
    assert window["required"] == ["start_minute", "end_minute"]
    for bound in ("start_minute", "end_minute"):
        node = window["properties"][bound]
        assert (node["type"], node["minimum"], node["maximum"]) == ("integer", 0, 1439)
    assert interval["match"] == {
        "all": [
            {"gte": [{"field": "timer.minute_of_day"}, {"param": "window.start_minute"}]},
            {"lte": [{"field": "timer.minute_of_day"}, {"param": "window.end_minute"}]},
        ]
    }
    assert triggers["default_policy"] == {
        "combination": "all_of",
        "rules": [
            {"type": "interval", "parameters": {"every_minutes": 30, "probability": 0.25}}
        ],
    }
    assert "timer" in EVENT_KINDS


def test_the_settings_validator_names_each_refused_field() -> None:
    """Each channel field is checked, one value-free diagnostic naming it."""

    good = {"channels": [channel(CHANNEL_A, PROMPT_A)]}
    assert validate_settings(good) == []
    cases = {
        "channels": {"channels": []},
        "channels[0].prompt_text": {"channels": [channel(CHANNEL_A, "  ")]},
        "channels[0].destination.channel_id": {"channels": [channel("", PROMPT_A)]},
        "channels[1].destination": {
            "channels": [channel(CHANNEL_A, PROMPT_A), channel(CHANNEL_A, PROMPT_B)]
        },
        "every_minutes": {"channels": [channel(CHANNEL_A, PROMPT_A)], "every_minutes": 10},
    }
    for field_name, settings in cases.items():
        diagnostics = validate_settings(settings)
        assert len(diagnostics) == 1, (field_name, diagnostics)
        assert diagnostics[0].startswith(f"module 'timer': field '{field_name}':"), diagnostics
        assert PROMPT_A not in diagnostics[0]


# -- AC29: the firing ------------------------------------------------------------ #


async def test_ac29_two_channels_every_10_minutes_give_12_accepted_events_in_an_hour() -> None:
    """AC29: ``interval{every_minutes: 10, probability: 1}`` on 2 channels,
    the clock advanced 60 minutes after ``start_inputs``: exactly 12
    ``timer`` events, sequences 1–6 per channel, ``message_id``
    ``timer:<platform>:<channel_id>:<sequence>``, each channel's text, all
    accepted and admitted."""

    policy = interval_policy(every_minutes=10, probability=1)
    h = await timed(triggers=configured({CHANNEL_A: policy, CHANNEL_B: policy}))
    assert h.fired == []
    await h.advance_minutes(60)

    assert len(h.fired) == 12
    for channel_id, prompt in ((CHANNEL_A, PROMPT_A), (CHANNEL_B, PROMPT_B)):
        events = [e for e in h.fired if e["payload"]["channel_id"] == channel_id]
        assert [e["payload"]["timer"]["sequence"] for e in events] == [1, 2, 3, 4, 5, 6]
        assert [e["payload"]["message_id"] for e in events] == [
            f"timer:{PLATFORM}:{channel_id}:{sequence}" for sequence in range(1, 7)
        ]
        for event in events:
            payload = event["payload"]
            assert event["type"] == "channel.chat.message"
            assert event["metadata"] == {"source": "timer", "schema_version": 2}
            assert payload["text"] == prompt
            assert payload["kind"] == "timer"
            assert payload["platform"] == PLATFORM
            assert payload["author"]["id"] == f"timer:{channel_id}"
            assert payload["timer"]["minute_of_day"] == 720
    decisions = h.decisions()
    assert len(decisions) == 12
    assert {d["reason"] for d in decisions} == {"accepted:interval_match"}
    assert len(events_of(h.runtime.bus, TRACE_INPUT_TRIGGER_REJECTED)) == 0
    assert h.admitted() == h.fired
    sessions = {session.viewer_id for session, _work in h.scheduler.admissions}
    assert sessions == {f"timer:{CHANNEL_A}", f"timer:{CHANNEL_B}"}
    assert {work.kind for _session, work in h.scheduler.admissions} == {"timer.fire"}
    counts = h.handle.counts()
    assert counts[(PLATFORM, CHANNEL_A)] == {
        COUNT_FIRED: 6, COUNT_ADMITTED: 6, COUNT_NOT_ADMITTED: 0,
    }
    # The event is never published on the bus: admission is the whole route.
    assert events_of(h.runtime.bus, "channel.chat.message") == []
    await h.handle.stop_inputs()


async def test_the_schedule_runs_from_the_start_not_from_the_last_firing() -> None:
    """Firings fall at ``t0 + k × interval``: the clock reaching each due
    instant late (in one 25-minute step) considers one firing and the
    schedule resumes at the next due instant, never from the late one."""

    policy = interval_policy(every_minutes=10, probability=1)
    h = await timed(
        channels=[channel(CHANNEL_A, PROMPT_A)], triggers=configured({CHANNEL_A: policy})
    )
    instants: list[float] = []
    original = h.handle._on_fire

    def stamp(event: Any) -> None:
        instants.append(h.clock())
        original(event)

    h.handle._on_fire = stamp
    h.clock.advance(25 * 60)
    await settle()
    assert len(h.fired) == 1  # due 10 and 20 passed at once: one considered
    await h.advance_minutes(5)  # 30 is due: on schedule, not 25 + 10
    assert len(h.fired) == 2
    assert instants == [START + 25 * 60, START + 30 * 60]
    await h.advance_minutes(10)
    assert len(h.fired) == 3
    await h.handle.stop_inputs()


async def test_a_deadline_passed_while_firing_is_skipped_not_fired_at_once() -> None:
    """The next deadline is computed from the clock after the firing: a
    firing at 10 that lasts until 25 skips the elapsed 20 and resumes at
    30, rather than firing again for 20 the moment it returns."""

    policy = interval_policy(every_minutes=10, probability=1)
    h = await timed(
        channels=[channel(CHANNEL_A, PROMPT_A)], triggers=configured({CHANNEL_A: policy})
    )
    instants: list[float] = []
    original = h.handle._on_fire

    def slow(event: Any) -> None:
        instants.append(h.clock())
        original(event)
        if len(instants) == 1:
            h.clock.advance(15 * 60)  # the firing at 10 ends at 25

    h.handle._on_fire = slow
    await h.advance_minutes(10)
    assert len(h.fired) == 1  # 20 passed during the firing: skipped
    await h.advance_minutes(5)  # now at 30
    assert len(h.fired) == 2
    assert instants == [START + 10 * 60, START + 30 * 60]
    await h.handle.stop_inputs()


async def test_an_accepted_event_reaches_the_brain_owned_scheduler_through_the_service() -> None:
    """With no scheduler on the context — the runtime ``core.main`` builds —
    an accepted event is admitted once into the scheduler the brain
    publishes as ``(admission, runs)``."""

    policy = interval_policy(every_minutes=10, probability=1)
    h = await timed(
        channels=[channel(CHANNEL_A, PROMPT_A)],
        triggers=configured({CHANNEL_A: policy}),
        shared=False,
    )
    await h.advance_minutes(10)
    assert [e["payload"]["message_id"] for e in h.admitted()] == [
        f"timer:{PLATFORM}:{CHANNEL_A}:1"
    ]
    await h.handle.stop_inputs()


# -- AC30: the draws, the window, the declaration --------------------------------- #


async def test_ac30_100_events_consume_100_draws_and_accept_those_below_the_probability() -> None:
    """AC30: with ``probability: 0.25`` and a recorded random source, 100
    timer events consume exactly 100 draws, and the accepted count equals
    the number of draws below 0.25."""

    values = [((index * 37) % 100) / 100 for index in range(100)]
    rng = RecordedRandom(values)
    policy = interval_policy(every_minutes=1, probability=0.25)
    h = await timed(
        channels=[channel(CHANNEL_A, PROMPT_A)],
        triggers=configured({CHANNEL_A: policy}),
        rng=rng,
    )
    await h.advance_minutes(100)

    assert len(h.fired) == 100
    assert rng.ordinal == 100
    below = sum(1 for value in values if value < 0.25)
    assert below == 25
    accepted = events_of(h.runtime.bus, TRACE_INPUT_TRIGGER_ACCEPTED)
    rejected = events_of(h.runtime.bus, TRACE_INPUT_TRIGGER_REJECTED)
    assert len(accepted) == below
    assert len(rejected) == 100 - below
    assert {e["payload"]["reason"] for e in rejected} == {"rejected:interval_probability_miss"}
    assert len(h.admitted()) == below
    draws = [e["payload"]["draws"][0] for e in accepted + rejected]
    assert sorted(d["ordinal"] for d in draws) == list(range(100))
    assert all(d["parameter"] == "probability" for d in draws)
    await h.handle.stop_inputs()


@pytest.mark.parametrize(
    ("minute", "reason"),
    [
        (599, "rejected:interval_no_match"),
        (600, "accepted:interval_match"),
        (660, "accepted:interval_match"),
        (661, "rejected:interval_no_match"),
    ],
)
async def test_ac30_the_window_bounds_are_inclusive(minute: int, reason: str) -> None:
    """AC30: window ``{start_minute: 600, end_minute: 660}``, probability 1 —
    minutes 599, 600, 660 and 661 decide rejected, accepted, accepted,
    rejected."""

    policy = interval_policy(
        every_minutes=10, probability=1, window={"start_minute": 600, "end_minute": 660}
    )
    h = await timed(
        channels=[channel(CHANNEL_A, PROMPT_A)],
        triggers=configured({CHANNEL_A: policy}),
        minute=minute,
    )
    await h.advance_minutes(10)
    (decision,) = h.decisions()
    assert decision["reason"] == reason
    assert h.fired[0]["payload"]["timer"]["minute_of_day"] == minute
    assert len(h.admitted()) == (1 if reason.startswith("accepted") else 0)
    await h.handle.stop_inputs()


@pytest.mark.parametrize("minute", [0, 1439])
async def test_ac30_without_a_window_every_minute_of_the_day_is_accepted(minute: int) -> None:
    """AC30: without a window, minutes 0 and 1439 are accepted (probability 1)."""

    policy = interval_policy(every_minutes=10, probability=1)
    h = await timed(
        channels=[channel(CHANNEL_A, PROMPT_A)],
        triggers=configured({CHANNEL_A: policy}),
        minute=minute,
    )
    await h.advance_minutes(10)
    (decision,) = h.decisions()
    assert decision["reason"] == "accepted:interval_match"
    assert len(h.admitted()) == 1
    await h.handle.stop_inputs()


async def test_ac30_the_shipped_declaration_registers_with_no_hook() -> None:
    """AC30: the shipped ``interval`` declaration passes R1's load checks as
    a declared match rule with no hook: the registry holds it declared, with
    no hook slot, and the default policy resolves for an unconfigured
    channel."""

    h = await timed(channels=[channel(CHANNEL_A, PROMPT_A)], started=False)
    registry = h.runtime.triggers.registry
    spec = registry.spec("timer")
    assert spec is not None
    declaration = spec.declaration("interval")
    assert declaration is not None and declaration.declared
    assert declaration.hook is False and declaration.match is not None
    assert declaration.kinds == ("timer",)
    assert dict(registry.hook_slots("timer")) == {}
    policy = registry.resolve("timer", CHANNEL_A)
    assert [rule.type for rule in policy.rules] == ["interval"]
    assert dict(policy.rules[0].parameters) == {"every_minutes": 30, "probability": 0.25}
    await h.handle.close()


async def test_ac30_a_rule_naming_an_undeclared_parameter_is_refused_at_load(
    tmp_path: Path,
) -> None:
    """AC30: a fixture declaration whose rule references ``{param:
    window.nope}`` is refused at load, the diagnostic naming that operand's
    path; nothing is activated."""

    root = tmp_path / "modules"
    shutil.copytree(
        MODULES / "timer", root / "timer", ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
    )
    manifest_path = root / "timer" / "module.yaml"
    manifest = yaml.safe_load(manifest_path.read_text())
    (interval,) = [entry for entry in manifest["triggers"]["types"] if entry["name"] == "interval"]
    interval["match"]["all"][1]["lte"][1] = {"param": "window.nope"}
    manifest_path.write_text(yaml.safe_dump(manifest))
    runtime = runtime_context(clock=ManualClock(START), trigger_registry=TriggerRegistry())
    loader = ModuleLoader(runtime.bus, root, context=runtime, environ={})

    with pytest.raises(ModuleLoadError) as caught:
        await loader.activate_enabled(
            {
                "enabled_modules": ["timer"],
                "modules": {"timer": {"channels": [channel(CHANNEL_A, PROMPT_A)]}},
            }
        )

    (diagnostic,) = caught.value.diagnostics
    assert diagnostic.startswith(
        "module 'timer': field 'triggers.timer.types[1].match.all[1].lte[1].param':"
    ), diagnostic
    assert "does not resolve through the declared parameter schema" in diagnostic
    assert runtime.triggers.registry.spec("timer") is None


# -- AC31: the startup diagnostics and the span ------------------------------------ #


LOAD_REFUSALS = {
    "every_minutes-0": (
        {"every_minutes": 0, "probability": 0.5},
        "rules[0].parameters.every_minutes",
    ),
    "every_minutes-1441": (
        {"every_minutes": 1441, "probability": 0.5},
        "rules[0].parameters.every_minutes",
    ),
    "probability-1.5": (
        {"every_minutes": 10, "probability": 1.5},
        "rules[0].parameters.probability",
    ),
}


@pytest.mark.parametrize("variant", sorted(LOAD_REFUSALS))
async def test_ac31_an_out_of_range_parameter_is_a_load_diagnostic_naming_it(
    variant: str,
) -> None:
    """AC31: ``every_minutes`` 0 and 1441 and ``probability`` 1.5 are each
    refused at load by the declared schema, one diagnostic naming the input
    and the field; nothing is activated, nothing fires."""

    parameters, field_name = LOAD_REFUSALS[variant]
    clock = ManualClock(START)
    runtime = runtime_context(clock=clock, trigger_registry=TriggerRegistry())
    loader = ModuleLoader(runtime.bus, MODULES, context=runtime, environ={})
    fired: list[Any] = []
    settings = {
        "channels": [channel(CHANNEL_A, PROMPT_A)],
        "_sleeper": clock.sleep,
        "_on_fire": fired.append,
    }

    with pytest.raises(ModuleLoadError) as caught:
        await loader.activate_enabled(
            {
                "enabled_modules": ["timer"],
                "modules": {"timer": settings},
                "triggers": configured({CHANNEL_A: interval_policy(**parameters)}),
            }
        )

    (diagnostic,) = caught.value.diagnostics
    assert diagnostic.startswith(f"module 'timer': field 'triggers.timer.channels.{CHANNEL_A}':")
    assert f"triggers.timer.policy.{field_name}" in diagnostic
    clock.advance(3600)
    await settle()
    assert fired == []


PREPARE_REFUSALS = {
    "window-700-600": (
        interval_policy(
            every_minutes=10, probability=1, window={"start_minute": 700, "end_minute": 600}
        ),
        "triggers.timer.policy.rules[0].parameters.window",
    ),
    "no-interval-rule": (
        {"combination": "all_of", "rules": [{"type": "event_kind", "parameters": {"kinds": ["timer"]}}]},
        "triggers.timer.policy.rules",
    ),
    "two-interval-rules": (
        {
            "combination": "all_of",
            "rules": [
                {"type": "interval", "parameters": {"every_minutes": 10, "probability": 1}},
                {"type": "interval", "parameters": {"every_minutes": 20, "probability": 1}},
            ],
        },
        "triggers.timer.policy.rules",
    ),
}


@pytest.mark.parametrize("variant", sorted(PREPARE_REFUSALS))
async def test_ac31_a_policy_the_loop_cannot_honour_is_a_startup_diagnostic(
    variant: str,
) -> None:
    """AC31: a window with start 700 > end 600, and a configured channel
    whose resolved policy holds 0 or 2 ``interval`` rules, are each refused
    in ``prepare``: ``module.degraded`` names the input, the channel index
    and the field, no configured value, and ``prepare`` raises; no loop
    starts and nothing fires."""

    policy, field_name = PREPARE_REFUSALS[variant]
    h = await timed(
        channels=[channel(CHANNEL_A, PROMPT_A), channel(CHANNEL_B, PROMPT_B)],
        triggers=configured({CHANNEL_B: policy}),
        prepared=False,
        started=False,
    )

    # The loader imports the package under its own private name, so the
    # error is matched by its base class and its name.
    with pytest.raises(RuntimeError) as caught:
        await h.handle.prepare()
    assert type(caught.value).__name__ == "TimerModuleError"

    (degraded,) = events_of(h.runtime.bus, TRACE_MODULE_DEGRADED)
    assert degraded["payload"]["module"] == "timer"
    reason = degraded["payload"]["reason"]
    assert reason == str(caught.value)
    assert reason.startswith(f"timer prepare: channels[1]: field '{field_name}':"), reason
    for value in (CHANNEL_B, PROMPT_B):
        assert value not in reason
    await h.handle.start_inputs()
    assert h.handle.running == 0
    await h.advance_minutes(60)
    assert h.fired == [] and h.decisions() == []
    await h.handle.close()


async def test_ac31_nothing_fires_after_prepare_before_start_inputs() -> None:
    """AC31: 60 minutes on the clock after ``prepare`` and before
    ``start_inputs`` publish 0 events."""

    policy = interval_policy(every_minutes=10, probability=1)
    h = await timed(triggers=configured({CHANNEL_A: policy, CHANNEL_B: policy}), started=False)
    assert h.handle.running == 0
    await h.advance_minutes(60)
    assert h.fired == [] and h.decisions() == [] and h.admitted() == []
    await h.handle.start_inputs()
    assert h.handle.running == 2
    await h.advance_minutes(10)
    assert len(h.fired) == 2
    await h.handle.stop_inputs()


async def test_ac31_nothing_fires_after_stop_inputs() -> None:
    """AC31: 60 minutes on the clock after ``stop_inputs`` publish 0 events;
    the loops are cancelled."""

    policy = interval_policy(every_minutes=10, probability=1)
    h = await timed(triggers=configured({CHANNEL_A: policy, CHANNEL_B: policy}))
    await h.advance_minutes(10)
    assert len(h.fired) == 2
    await h.handle.stop_inputs()
    assert h.handle.running == 0
    await h.advance_minutes(60)
    assert len(h.fired) == 2
    assert len(h.decisions()) == 2 and len(h.admitted()) == 2
    await h.handle.start_inputs()  # a stopped input does not start again
    await h.advance_minutes(60)
    assert len(h.fired) == 2
    await h.handle.close()
