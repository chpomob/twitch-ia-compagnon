"""Trigger declaration, resolution and deterministic evaluation (R1, AC1-AC5).

Every test here is pure configuration and pure decision: no transport is
opened, no clock is read and no value is drawn that this file did not inject,
which is what lets the determinism and "before any transport" criteria be
asserted at all.

The last section carries the P2 unit assertions the plan attaches to this step:
``sanitize_trace`` redacts, truncates and refuses a reserved key, and
``Counters`` exposes the seven required counters before anything increments
them (R8, AC28, AC29).
"""

from dataclasses import fields

import pytest

from conftest import runtime_context

from core.contracts import (
    COUNTER_DEDUP_EVICTIONS,
    COUNTER_TRIGGER_REJECTIONS,
    INTERNAL_TRACE_TYPES,
    REQUIRED_COUNTERS,
    TRACE_ACTION_COMPLETED,
    TRUNCATION_KEY,
    ContractError,
    Counters,
    TriggerPolicy,
    TriggerRule,
    TriggerSpec,
    TriggerTypeDeclaration,
    sanitize_trace,
    trace_size,
)
from core.triggers import (
    BUILTIN_TRIGGER_TYPES,
    COMPANION_NAME_TOKEN,
    REASON_INTERNAL_TRACE,
    REASON_NOT_NORMALIZED,
    REASON_SELF_AUTHORED,
    REASON_UNAUTHENTICATED,
    TRIGGER_TYPE_AUDIENCE,
    TRIGGER_TYPE_KEYWORD,
    TRIGGER_TYPE_PROBABILITY,
    TriggerContext,
    TriggerDecision,
    TriggerEngine,
    TriggerRegistry,
    TrustedClaim,
    companion_mention_policy,
)

COMPANION_NAME = "Compagnon"
COMPANION_ID = "companion-bot"
CHAT_INPUT = "twitch"
EXPLICIT_CHANNEL = "channel-explicit"
DEFAULT_CHANNEL = "channel-default"


# --------------------------------------------------------------------------- #
# Doubles: nothing sleeps, nothing draws and nothing connects on its own
# --------------------------------------------------------------------------- #


class _Clock:
    """An injected clock: no test ever sleeps."""

    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class _Rng:
    """A recorded random source that counts every draw.

    Determinism is asserted against a recorded sequence rather than a seeded
    generator, so a replay is reproducible by construction and the number of
    values consumed is directly observable (AC3).
    """

    def __init__(self, values) -> None:
        self._values = tuple(values)
        self.draws = 0

    def random(self) -> float:
        value = self._values[self.draws % len(self._values)]
        self.draws += 1
        return value


class _Transport:
    """A transport that records whether startup ever reached it (AC2)."""

    def __init__(self) -> None:
        self.opened = 0

    def open(self) -> None:
        self.opened += 1


def _startup(registry: TriggerRegistry, configured, transport: _Transport) -> None:
    """Apply the configured policies, then open the transport.

    The order is the one R1 requires: policy validation is part of startup and
    precedes any network effect, so an invalid configuration must raise with
    ``transport.opened`` still at 0.
    """

    for input_name, channel_id, policy in configured:
        registry.configure(input_name, policy, channel_id=channel_id)
    transport.open()


# --------------------------------------------------------------------------- #
# Fixtures: what a module declares, and what configuration selects
# --------------------------------------------------------------------------- #


def _chat_spec(
    *,
    types=BUILTIN_TRIGGER_TYPES,
    combinations=("all_of", "any_of"),
    default_policy=None,
) -> TriggerSpec:
    """The chat input's manifest declaration (R1)."""

    if default_policy is None:
        default_policy = companion_mention_policy()
    return TriggerSpec(
        types=tuple(types), combinations=combinations, default_policy=default_policy
    )


def _silent_spec() -> TriggerSpec:
    """A module declaring zero trigger types, so no policy may name it (AC2)."""

    return TriggerSpec(types=())


def _keyword_policy(*keywords: str, combination: str = "all_of") -> TriggerPolicy:
    return TriggerPolicy(
        rules=(
            TriggerRule(type=TRIGGER_TYPE_KEYWORD, parameters={"keywords": list(keywords)}),
        ),
        combination=combination,
    )


def _probability_policy(probability: float) -> TriggerPolicy:
    return TriggerPolicy(
        rules=(
            TriggerRule(
                type=TRIGGER_TYPE_PROBABILITY, parameters={"probability": probability}
            ),
        )
    )


def _audience_policy(audience: str) -> TriggerPolicy:
    return TriggerPolicy(
        rules=(TriggerRule(type=TRIGGER_TYPE_AUDIENCE, parameters={"audience": audience}),)
    )


def _registry(**overrides) -> TriggerRegistry:
    registry = TriggerRegistry(companion_name=overrides.pop("companion_name", COMPANION_NAME))
    registry.register(CHAT_INPUT, overrides.pop("spec", _chat_spec()))
    return registry


def _engine(registry: TriggerRegistry | None = None, **overrides) -> TriggerEngine:
    settings = {
        "dedup_max_entries": 64,
        "dedup_ttl_seconds": 300.0,
        "companion_identity": COMPANION_ID,
        "clock": _Clock(),
        "rng": _Rng((0.5,)),
    }
    settings.update(overrides)
    return TriggerEngine(_registry() if registry is None else registry, **settings)


def _message(
    *,
    message_id: str,
    text: str = "hello",
    channel_id: str = DEFAULT_CHANNEL,
    author_id: str = "viewer-1",
    platform: str = "twitch",
    source: str = CHAT_INPUT,
) -> dict:
    """A normalised chat event, schema version 2 (R3)."""

    return {
        "type": "channel.chat.message",
        "payload": {
            "platform": platform,
            "channel_id": channel_id,
            "author": {"id": author_id, "display_name": "Viewer"},
            "message_id": message_id,
            "text": text,
        },
        "metadata": {"schema_version": 2, "source": source},
    }


# --------------------------------------------------------------------------- #
# AC1 — two channels, two policies, one engine; the default replaced, not merged
# --------------------------------------------------------------------------- #


def test_two_channels_apply_two_policies_in_one_engine() -> None:
    """AC1: 2 accepted and 2 rejected over 4 messages in one process.

    The explicitly configured channel applies its own rule; the channel with no
    explicit rule falls back to the module default, "the message text mentions
    the configured companion name".
    """

    registry = _registry()
    transport = _Transport()
    _startup(registry, [(CHAT_INPUT, EXPLICIT_CHANNEL, _keyword_policy("!ask"))], transport)
    assert transport.opened == 1
    engine = _engine(registry)

    decisions = [
        engine.evaluate(_message(message_id="m1", channel_id=EXPLICIT_CHANNEL, text="!ask something")),
        engine.evaluate(_message(message_id="m2", channel_id=EXPLICIT_CHANNEL, text="just chatting")),
        engine.evaluate(_message(message_id="m3", channel_id=DEFAULT_CHANNEL, text=f"hey {COMPANION_NAME} how are you")),
        engine.evaluate(_message(message_id="m4", channel_id=DEFAULT_CHANNEL, text="just chatting")),
    ]

    assert [decision.accepted for decision in decisions] == [True, False, True, False]
    assert sum(decision.accepted for decision in decisions) == 2
    assert sum(not decision.accepted for decision in decisions) == 2
    assert engine.counters.get(COUNTER_TRIGGER_REJECTIONS) == 2

    # The two channels were decided by two different policies, each naming the
    # version that decided it (R8).
    explicit_version = _keyword_policy("!ask").version
    default_version = companion_mention_policy().version
    assert explicit_version != default_version
    assert [decisions[0].policy_version, decisions[1].policy_version] == [
        explicit_version,
        explicit_version,
    ]
    assert [decisions[2].policy_version, decisions[3].policy_version] == [
        default_version,
        default_version,
    ]


def test_configured_policy_replaces_the_module_default_entirely() -> None:
    """AC1: the default is replaced, never merged into the configured policy.

    A mention of the companion name is exactly what the module default accepts.
    On the channel that configures its own rule it must decide nothing at all,
    which is only true if the default was dropped rather than combined (R1).
    """

    registry = _registry()
    registry.configure(CHAT_INPUT, _keyword_policy("!ask"), channel_id=EXPLICIT_CHANNEL)
    engine = _engine(registry)

    mention = engine.evaluate(
        _message(message_id="m1", channel_id=EXPLICIT_CHANNEL, text=f"hello {COMPANION_NAME}")
    )
    assert mention.accepted is False
    assert mention.policy_version == _keyword_policy("!ask").version


def test_resolution_prefers_channel_then_input_then_module_default() -> None:
    """AC1: exactly one policy decides, chosen most specific first."""

    registry = _registry()
    default = companion_mention_policy()
    assert registry.resolve(CHAT_INPUT, DEFAULT_CHANNEL) == default

    input_wide = _keyword_policy("!input")
    registry.configure(CHAT_INPUT, input_wide)
    assert registry.resolve(CHAT_INPUT, DEFAULT_CHANNEL) == input_wide

    channel_only = _keyword_policy("!channel")
    registry.configure(CHAT_INPUT, channel_only, channel_id=EXPLICIT_CHANNEL)
    assert registry.resolve(CHAT_INPUT, EXPLICIT_CHANNEL) == channel_only
    assert registry.resolve(CHAT_INPUT, DEFAULT_CHANNEL) == input_wide
    assert registry.resolve("unknown-input", DEFAULT_CHANNEL) is None


def test_module_default_takes_the_companion_name_from_configuration() -> None:
    """R1: the default policy carries a token; configuration carries the name."""

    registry = TriggerRegistry(companion_name="Ada")
    registry.register(CHAT_INPUT, _chat_spec())
    engine = _engine(registry)

    assert engine.evaluate(_message(message_id="m1", text="hello Ada")).accepted is True
    assert (
        engine.evaluate(_message(message_id="m2", text=f"hello {COMPANION_NAME}")).accepted
        is False
    )

    # Without a configured name the token cannot resolve, and that is a startup
    # diagnostic rather than a rule that silently never matches.
    nameless = TriggerRegistry()
    with pytest.raises(ContractError) as error:
        nameless.register(CHAT_INPUT, _chat_spec())
    assert COMPANION_NAME_TOKEN in str(error.value)


def test_input_registered_with_its_own_companion_name_resolves_the_token() -> None:
    """R1: the name is the declaring input's setting; the registry-wide one is a fallback.

    The application builds its registry without a name — the core's own
    configuration carries no module vocabulary — and the loader registers
    each input with the name found in that module's settings. The name given
    at registration decides both validation and evaluation for that input,
    and an input registered without one falls back to the registry-wide name.
    """

    nameless = TriggerRegistry()
    nameless.register(CHAT_INPUT, _chat_spec(), companion_name="Ada")
    assert nameless.companion_name is None
    assert nameless.companion_name_for(CHAT_INPUT) == "Ada"
    assert nameless.companion_name_for("other-input") is None
    engine = _engine(nameless)
    assert engine.evaluate(_message(message_id="m1", text="hello Ada")).accepted is True
    assert (
        engine.evaluate(_message(message_id="m2", text=f"hello {COMPANION_NAME}")).accepted
        is False
    )

    shared = TriggerRegistry(companion_name=COMPANION_NAME)
    shared.register(CHAT_INPUT, _chat_spec())
    assert shared.companion_name_for(CHAT_INPUT) == COMPANION_NAME

    with pytest.raises(ContractError) as error:
        TriggerRegistry().register(CHAT_INPUT, _chat_spec(), companion_name="   ")
    assert f"triggers.{CHAT_INPUT}.companion_name" in str(error.value)


# --------------------------------------------------------------------------- #
# AC2 — four invalid configurations, refused before any transport
# --------------------------------------------------------------------------- #


def test_undeclared_trigger_type_is_refused_before_any_transport() -> None:
    """AC2: a type the module does not declare, named with its input."""

    registry = TriggerRegistry(companion_name=COMPANION_NAME)
    registry.register(
        CHAT_INPUT,
        _chat_spec(
            types=[
                declaration
                for declaration in BUILTIN_TRIGGER_TYPES
                if declaration.name != TRIGGER_TYPE_AUDIENCE
            ]
        ),
    )
    transport = _Transport()

    with pytest.raises(ContractError) as error:
        _startup(registry, [(CHAT_INPUT, EXPLICIT_CHANNEL, _audience_policy("subscribers"))], transport)

    message = str(error.value)
    assert f"triggers.{CHAT_INPUT}" in message
    assert ".type" in message
    assert transport.opened == 0


def test_probability_parameter_outside_the_schema_is_refused() -> None:
    """AC2: a parameter failing the declared schema, named with its field."""

    registry = _registry()
    transport = _Transport()

    with pytest.raises(ContractError) as error:
        _startup(registry, [(CHAT_INPUT, EXPLICIT_CHANNEL, _probability_policy(1.5))], transport)

    message = str(error.value)
    assert f"triggers.{CHAT_INPUT}" in message
    assert "probability" in message
    assert transport.opened == 0


def test_undeclared_combination_operator_is_refused() -> None:
    """AC2: a combination the module does not declare."""

    registry = TriggerRegistry(companion_name=COMPANION_NAME)
    registry.register(CHAT_INPUT, _chat_spec(combinations=("all_of",)))
    transport = _Transport()

    with pytest.raises(ContractError) as error:
        _startup(
            registry,
            [(CHAT_INPUT, EXPLICIT_CHANNEL, _keyword_policy("!ask", combination="any_of"))],
            transport,
        )

    message = str(error.value)
    assert f"triggers.{CHAT_INPUT}" in message
    assert "combination" in message
    assert transport.opened == 0


def test_explicit_policy_on_a_module_declaring_no_trigger_type_is_refused() -> None:
    """AC2: a module declaring zero types accepts no policy at all."""

    registry = _registry()
    registry.register("metrics", _silent_spec())
    transport = _Transport()

    with pytest.raises(ContractError) as error:
        _startup(registry, [("metrics", None, _keyword_policy("!ask"))], transport)

    message = str(error.value)
    assert "triggers.metrics" in message
    assert "no trigger type" in message
    assert transport.opened == 0
    assert registry.resolve("metrics", DEFAULT_CHANNEL) is None


def test_a_module_cannot_declare_a_type_the_runtime_cannot_evaluate() -> None:
    """R1: a manifest that could only fail at ingestion fails at load."""

    registry = TriggerRegistry(companion_name=COMPANION_NAME)
    spec = TriggerSpec(
        types=(
            TriggerTypeDeclaration(name="astrology", parameter_schema={"type": "object"}),
        ),
        combinations=("all_of",),
        default_policy=TriggerPolicy(rules=(TriggerRule(type="astrology"),)),
    )

    with pytest.raises(ContractError) as error:
        registry.register(CHAT_INPUT, spec)
    assert f"triggers.{CHAT_INPUT}.types[0].name" in str(error.value)


def test_a_policy_naming_an_undeclared_input_is_refused() -> None:
    registry = _registry()
    with pytest.raises(ContractError) as error:
        registry.validate_policy("nowhere", _keyword_policy("!ask"))
    assert "triggers.nowhere" in str(error.value)


def test_a_per_call_policy_override_is_held_to_what_the_module_declared() -> None:
    """AC2: an override is not a way past the declaration it decides under.

    The canonical built-in schema would accept this probability rule; the chat
    module declares only ``keyword``, so the event's own input must refuse it —
    without consulting a rule or drawing a value.
    """

    keyword_only = _chat_spec(
        types=tuple(
            declaration
            for declaration in BUILTIN_TRIGGER_TYPES
            if declaration.name == TRIGGER_TYPE_KEYWORD
        ),
        combinations=("all_of",),
        default_policy=_keyword_policy("!ask"),
    )
    registry = _registry(spec=keyword_only)
    rng = _Rng((0.5,))
    engine = _engine(registry, rng=rng)

    with pytest.raises(ContractError) as error:
        engine.evaluate(_message(message_id="m1"), policy=_probability_policy(1.0))

    message = str(error.value)
    assert f"triggers.{CHAT_INPUT}.policy.rules[0].type" in message
    assert TRIGGER_TYPE_KEYWORD in message  # what the module does declare
    assert rng.draws == 0
    assert engine.rule_evaluations == 0
    assert engine.window_size == 0


def test_a_per_call_policy_cannot_decide_for_an_input_declaring_no_type() -> None:
    """AC2: a module declaring zero trigger types accepts no policy at all."""

    registry = _registry()
    registry.register("metrics", _silent_spec())
    engine = _engine(registry)

    with pytest.raises(ContractError) as error:
        engine.evaluate(
            _message(message_id="m1", source="metrics"), policy=_keyword_policy("!ask")
        )

    message = str(error.value)
    assert "triggers.metrics" in message
    assert "no trigger type" in message


# --------------------------------------------------------------------------- #
# AC3 — determinism over the sequence, and no redraw inside the dedup window
# --------------------------------------------------------------------------- #

_DRAWS = (
    0.04, 0.71, 0.35, 0.09, 0.88, 0.51, 0.02, 0.63, 0.44, 0.97,
    0.16, 0.08, 0.77, 0.29, 0.55, 0.01, 0.68, 0.39, 0.92, 0.23,
)
"""Twenty recorded draws; five are below the 0.1 threshold under test."""


def _twenty_events() -> list[dict]:
    return [_message(message_id=f"m{index}", text=f"message {index}") for index in range(20)]


def _replay(events) -> tuple[list[bool], _Rng, TriggerEngine]:
    registry = _registry()
    registry.configure(CHAT_INPUT, _probability_policy(0.1))
    rng = _Rng(_DRAWS)
    engine = _engine(registry, rng=rng)
    return [engine.evaluate(event).accepted for event in events], rng, engine


def test_probability_policy_replays_identically_over_the_same_sequence() -> None:
    """AC3: the same 20 events and the same random source decide the same way.

    Determinism is defined over the whole event sequence, not per channel: one
    engine draws every probability rule from one source, so the assertion is
    that replaying the sequence reproduces it exactly.
    """

    events = _twenty_events()
    first, first_rng, _ = _replay(events)
    second, second_rng, _ = _replay(events)

    assert first == second
    assert first_rng.draws == second_rng.draws == 20
    # Not vacuous: the sequence must actually contain both verdicts.
    assert first.count(True) == 5
    assert first.count(False) == 15


def test_runtime_context_builds_its_trigger_engine_on_the_injected_rng() -> None:
    """AC3 via the shared fixture: the engine ``runtime_context`` builds from a
    registry draws probability rules from the ``rng`` the suite injected, so
    a replay through the fixture is as deterministic as one built by hand."""

    events = _twenty_events()
    accepted = []
    for _ in range(2):
        registry = _registry()
        registry.configure(CHAT_INPUT, _probability_policy(0.1))
        rng = _Rng(_DRAWS)
        context = runtime_context(trigger_registry=registry, rng=rng)
        accepted.append([context.triggers.evaluate(event).accepted for event in events])
        assert rng.draws == 20

    assert accepted[0] == accepted[1]
    assert accepted[0].count(True) == 5


def test_redelivery_inside_the_window_redraws_nothing() -> None:
    """AC3: a redelivered source event reuses the recorded decision, 0 draws."""

    events = _twenty_events()
    accepted, rng, engine = _replay(events)
    draws_after_first_pass = rng.draws
    evaluations = engine.rule_evaluations

    replayed = engine.evaluate(events[5])

    assert rng.draws == draws_after_first_pass
    assert engine.rule_evaluations == evaluations
    assert replayed.accepted == accepted[5]
    assert replayed is engine.recorded(
        platform="twitch", channel_id=DEFAULT_CHANNEL, source_event_id="m5"
    )
    # A redelivery is one decision, not two: the rejection counter is untouched.
    assert engine.counters.get(COUNTER_TRIGGER_REJECTIONS) == accepted.count(False)


def test_the_decision_window_is_bounded_by_entries_and_ttl() -> None:
    """R6, AC22: oldest-first eviction, counted, and a re-decision afterwards."""

    registry = _registry()
    registry.configure(CHAT_INPUT, _probability_policy(0.1))
    rng = _Rng(_DRAWS)
    clock = _Clock()
    engine = _engine(registry, rng=rng, clock=clock, dedup_max_entries=2, dedup_ttl_seconds=10.0)

    for index in range(3):
        engine.evaluate(_message(message_id=f"m{index}"))
    assert engine.window_size == 2
    assert engine.counters.get(COUNTER_DEDUP_EVICTIONS) == 1

    draws = rng.draws
    engine.evaluate(_message(message_id="m0"))  # evicted: decided again
    assert rng.draws == draws + 1
    engine.evaluate(_message(message_id="m0"))  # retained again: no redraw
    assert rng.draws == draws + 1

    clock.now += 11.0
    evictions = engine.counters.get(COUNTER_DEDUP_EVICTIONS)
    engine.evaluate(_message(message_id="m0"))
    assert rng.draws == draws + 2
    assert engine.counters.get(COUNTER_DEDUP_EVICTIONS) > evictions


def test_a_replay_clock_dates_and_expires_the_window_in_one_domain() -> None:
    """AC3: what a replay records through its own clock reads back the same.

    The engine's clock and the replay's sit far apart, and the TTL is shorter
    than the distance between them: a decision taken through the replay clock
    must not read as absent merely because the engine's clock is elsewhere.
    """

    registry = _registry()
    registry.configure(CHAT_INPUT, _keyword_policy("hello"))
    engine = _engine(registry, clock=_Clock(1000.0), dedup_ttl_seconds=10.0)
    replay = _Clock(0.0)
    event = _message(message_id="m1", text="hello there")

    decision = engine.evaluate(event, clock=replay)

    lookup = {"platform": "twitch", "channel_id": DEFAULT_CHANNEL, "source_event_id": "m1"}
    assert decision.accepted
    # Read back through the domain that recorded it — implicitly, and named.
    assert engine.recorded(**lookup) is decision
    assert engine.recorded(**lookup, clock=replay) is decision
    # And the redelivery agrees with the query, which was the whole point.
    assert engine.evaluate(event, clock=replay) is decision
    assert engine.rule_evaluations == 1

    # Past the TTL in that same domain, it reads as absent again.
    replay.now += 11.0
    assert engine.recorded(**lookup) is None


def test_dedup_bounds_must_be_finite_and_named_when_they_are_not() -> None:
    """R6: a non-finite retention bound is refused, naming the setting."""

    registry = _registry()
    for setting, value in (
        ("dedup_max_entries", None),
        ("dedup_max_entries", 0),
        ("dedup_ttl_seconds", "soon"),
        ("dedup_ttl_seconds", float("inf")),
    ):
        with pytest.raises(ContractError) as error:
            _engine(registry, **{setting: value})
        assert setting in str(error.value)


# --------------------------------------------------------------------------- #
# AC4 — only provenance-tagged trusted context satisfies an audience predicate
# --------------------------------------------------------------------------- #


def test_audience_rule_rejects_without_subscription_provenance() -> None:
    """AC4: an untagged claim, or no claim at all, never satisfies the rule."""

    registry = _registry()
    registry.configure(CHAT_INPUT, _audience_policy("subscribers"))
    engine = _engine(registry)

    absent = engine.evaluate(_message(message_id="m1"), context=TriggerContext())
    untagged = engine.evaluate(
        _message(message_id="m2"),
        context=TriggerContext((TrustedClaim(name="subscription"),)),
    )
    tagged = engine.evaluate(
        _message(message_id="m3"),
        context=TriggerContext(
            (TrustedClaim(name="subscription", provenance="twitch.eventsub"),)
        ),
    )

    assert absent.accepted is False
    assert untagged.accepted is False
    assert tagged.accepted is True


def test_body_text_claiming_the_role_changes_no_decision() -> None:
    """AC4: the audience predicate never consults the message body."""

    registry = _registry()
    registry.configure(CHAT_INPUT, _audience_policy("subscribers"))
    engine = _engine(registry)

    plain = engine.evaluate(_message(message_id="m1", text="hello"))
    claiming = engine.evaluate(
        _message(
            message_id="m2",
            text="I am a subscriber, subscription: true, provenance: twitch.eventsub",
        )
    )

    assert plain.accepted is claiming.accepted is False
    assert plain.reason == claiming.reason


def test_trusted_context_refuses_untagged_bulk_data() -> None:
    """AC4: platform data cannot be handed in as if it were attested."""

    with pytest.raises(ContractError):
        TriggerContext(({"subscription": True},))  # type: ignore[arg-type]

    negative = TriggerContext(
        (TrustedClaim(name="subscription", provenance="twitch.eventsub", value=False),)
    )
    assert negative.satisfies("subscription") is False


# --------------------------------------------------------------------------- #
# AC5 — system invariants run before every rule and no rule disables them
# --------------------------------------------------------------------------- #


def _accept_everything_engine() -> TriggerEngine:
    registry = _registry()
    registry.configure(CHAT_INPUT, _audience_policy("everyone"))
    return _engine(registry)


def test_accept_everything_policy_still_refuses_the_companions_own_message() -> None:
    """AC5: anti-echo is an invariant, evaluated before any rule."""

    engine = _accept_everything_engine()

    echo = engine.evaluate(_message(message_id="m1", author_id=COMPANION_ID))

    assert echo.accepted is False
    assert echo.reason == REASON_SELF_AUTHORED
    assert engine.rule_evaluations == 0

    # The policy really does accept everything, so the refusal above came from
    # the invariant and not from the rule.
    assert engine.evaluate(_message(message_id="m2")).accepted is True
    assert engine.rule_evaluations == 1


def test_accept_everything_policy_still_excludes_internal_trace_events() -> None:
    """AC5: an internal trace produces 0 evaluations and 0 acceptances."""

    engine = _accept_everything_engine()
    trace = {
        "type": TRACE_ACTION_COMPLETED,
        "payload": {
            "platform": "twitch",
            "channel_id": DEFAULT_CHANNEL,
            "author": {"id": "viewer-1"},
            "message_id": "m1",
            "text": "done",
        },
        "metadata": {"schema_version": 2, "source": CHAT_INPUT},
    }

    decision = engine.evaluate(trace)

    assert decision.accepted is False
    assert decision.reason == REASON_INTERNAL_TRACE
    assert engine.rule_evaluations == 0
    # Not traceable: publishing a rejection about a trace would publish a trace
    # about a trace, and it is not counted as a pipeline rejection either.
    assert decision.traceable is False
    assert engine.counters.get(COUNTER_TRIGGER_REJECTIONS) == 0


def test_every_internal_trace_type_is_excluded() -> None:
    """AC5: the exclusion covers the whole supervision vocabulary (R8)."""

    engine = _accept_everything_engine()
    for index, event_type in enumerate(sorted(INTERNAL_TRACE_TYPES)):
        event = _message(message_id=f"m{index}")
        event["type"] = event_type
        assert engine.evaluate(event).reason == REASON_INTERNAL_TRACE
    assert engine.rule_evaluations == 0


def test_an_unauthenticated_or_unnormalised_event_is_refused_by_invariant() -> None:
    """AC5: payload validation and authenticity precede every rule."""

    engine = _accept_everything_engine()

    anonymous = _message(message_id="m1")
    anonymous["payload"] = dict(anonymous["payload"])
    anonymous["payload"].pop("author")
    unnormalised = _message(message_id="m2")
    unnormalised["metadata"] = {"source": CHAT_INPUT}

    assert engine.evaluate(anonymous).reason == REASON_UNAUTHENTICATED
    assert engine.evaluate(unnormalised).reason.startswith(REASON_NOT_NORMALIZED)
    assert engine.rule_evaluations == 0
    assert engine.counters.get(COUNTER_TRIGGER_REJECTIONS) == 2


def test_a_decision_never_carries_a_run_id() -> None:
    """R8, AC27: the decision is taken before admission, so no run exists yet."""

    names = {field.name for field in fields(TriggerDecision)}
    assert "run_id" not in names
    assert {"accepted", "reason", "policy_version", "source_event_id"} <= names

    decision = _accept_everything_engine().evaluate(_message(message_id="m1"))
    assert not hasattr(decision, "run_id")
    assert decision.source_event_id == "m1"
    assert decision.platform == "twitch"
    assert decision.channel_id == DEFAULT_CHANNEL


def test_an_input_with_no_applicable_policy_rejects_rather_than_accepts() -> None:
    """R1: absent configuration is never an implicit acceptance."""

    registry = TriggerRegistry(companion_name=COMPANION_NAME)
    registry.register("metrics", _silent_spec())
    engine = _engine(registry)

    decision = engine.evaluate(_message(message_id="m1", source="metrics"))

    assert decision.accepted is False
    assert decision.policy_version is None


# --------------------------------------------------------------------------- #
# P2 units: the sanitiser and the counters this engine reports through (R8)
# --------------------------------------------------------------------------- #


def test_sanitize_trace_redacts_a_configured_credential_verbatim_and_nested() -> None:
    """AC29: a configured value never survives, at the top level or below."""

    secret = "oauth:s3cr3t-value"
    payload = {
        "input": CHAT_INPUT,
        "authorization": f"Bearer {secret}",
        "nested": {"list": [{"token": secret}, "clean"]},
    }

    sanitized = sanitize_trace(payload, secrets=(secret,))

    assert secret not in repr(sanitized)
    assert sanitized["nested"]["list"][1] == "clean"
    assert sanitized["input"] == CHAT_INPUT


def test_sanitize_trace_truncates_at_the_byte_budget() -> None:
    """R8: trace content is bounded, and says when it was shortened."""

    sanitized = sanitize_trace({"reason": "x" * 5000}, max_bytes=128)

    assert trace_size(sanitized) <= 128
    assert sanitized[TRUNCATION_KEY] is True
    assert len(sanitized["reason"]) < 5000


def test_sanitize_trace_refuses_a_prompt_key() -> None:
    """R8, AC29: no full prompt and no model reasoning reaches a trace."""

    for reserved in ("prompt", "reasoning"):
        with pytest.raises(ContractError) as error:
            sanitize_trace({"run_id": "r1", reserved: "..."})
        assert reserved in str(error.value)


def test_counters_expose_the_seven_required_names_before_any_increment() -> None:
    """AC28: supervision is readable before anything has happened."""

    snapshot = Counters().snapshot()

    assert set(REQUIRED_COUNTERS) <= set(snapshot)
    assert len(REQUIRED_COUNTERS) == 7
    assert all(snapshot[name] == 0 for name in REQUIRED_COUNTERS)
