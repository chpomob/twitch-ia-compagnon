"""Action binding, default-deny authorization and terminal outcomes (R5, R8).

Nothing here sleeps, opens a transport or reads a real clock: the executor's
clock and its timeout sleeper are both injected, so a timeout is provoked by
letting the injected sleeper return rather than by waiting for one.

The last section carries the P1 contract assertions the plan attaches to this
step: a length-prefixed :class:`~core.contracts.SessionKey` serialisation that
adversarial separators cannot collide, and the terminal-status and nature
constraints that keep an unknown status out of an observation (R3, R5).
"""

import asyncio

import pytest

from core.actions import (
    COUNTER_ACTION_TIMEOUTS,
    COUNTER_LOST_TRACES,
    EMISSION_EMITTED,
    ERROR_EXTERNAL_UNKNOWN,
    ERROR_INVALID_ARGUMENTS,
    ERROR_INVALID_RESULT,
    ERROR_NOT_AUTHORIZED,
    REASON_NO_RULE,
    ActionExecutor,
    ActionRegistry,
    AmbiguousBindingError,
    AuthorizationPolicy,
    AuthorizationRule,
)
from core.contracts import (
    TERMINAL_STATUSES,
    TRACE_ACTION_COMPLETED,
    TRACE_ACTION_STARTED,
    WILDCARD,
    ActionCall,
    ActionObservation,
    ActionSpec,
    ContractError,
    Counters,
    Destination,
    SessionKey,
)

PLATFORM = "twitch"
CHANNEL_A = "chan-a"
CHANNEL_B = "chan-b"
OPERATOR = "operator:companion"
MODULE = "chat-module"

CHAT_SCOPE = Destination(PLATFORM, WILDCARD, "chat")

READ_SPEC = ActionSpec(
    name="chat.read_recent",
    version=1,
    description="Return the recent lines of one channel.",
    argument_schema={
        "type": "object",
        "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 50}},
        "required": ["limit"],
        "additionalProperties": False,
    },
    result_schema={
        "type": "object",
        "properties": {"lines": {"type": "array", "items": {"type": "string"}}},
        "required": ["lines"],
    },
    nature="read",
    required_permissions=("chat.read",),
    supported_destinations=(CHAT_SCOPE,),
    timeout_seconds=5.0,
    idempotency="natural",
)

WRITE_SPEC = ActionSpec(
    name="chat.send",
    version=1,
    description="Send one message to one channel.",
    argument_schema={
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
        "additionalProperties": False,
    },
    result_schema={
        "type": "object",
        "properties": {"message_id": {"type": "string"}},
        "required": ["message_id"],
    },
    nature="write",
    required_permissions=("chat.write",),
    supported_destinations=(CHAT_SCOPE,),
    timeout_seconds=5.0,
    idempotency="key",
)


# --------------------------------------------------------------------------- #
# Doubles: no transport, no real clock, no sleep
# --------------------------------------------------------------------------- #


class FakeBus:
    """Records published traces and can be made to fail on chosen types."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []
        self.failing: set[str] = set()
        self.on_publish = None

    async def publish(self, event_type: str, payload) -> None:
        if self.on_publish is not None:
            self.on_publish(event_type, payload)
        if event_type in self.failing:
            raise RuntimeError(f"publication of {event_type!r} failed")
        self.events.append((event_type, dict(payload)))

    def of_type(self, event_type: str) -> list[dict]:
        return [payload for name, payload in self.events if name == event_type]


class FakeSupervision:
    """A faithful stand-in for P10's facade, no more and no less.

    ``record_and_emit`` calls *record* first and swallows-and-counts a failed
    publication, which is the only behaviour of P10 this step depends on.
    """

    def __init__(self, bus: FakeBus, counters: Counters) -> None:
        self.bus = bus
        self.counters = counters

    async def emit(self, event_type: str, payload) -> None:
        await self.bus.publish(event_type, payload)

    async def record_and_emit(self, record, event_type: str, payload) -> None:
        record()
        try:
            await self.bus.publish(event_type, payload)
        except Exception:
            self.counters.increment(COUNTER_LOST_TRACES)


class ManualClock:
    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, delta: float) -> None:
        self.now += delta


async def never_sleep(_delay: float) -> None:
    """A timeout that never fires, so the provider always wins the race."""

    await asyncio.Event().wait()


async def immediate_sleep(_delay: float) -> None:
    """A timeout that fires at once, so the deadline always wins the race."""

    return None


async def hang(_invocation) -> ActionObservation:
    await asyncio.Event().wait()
    raise AssertionError("unreachable")  # pragma: no cover


def read_success(_invocation) -> ActionObservation:
    return ActionObservation(
        status="success",
        provenance={"transport": "fake"},
        result={"lines": ["hello"]},
    )


def write_success(invocation) -> ActionObservation:
    invocation.mark_emitted()
    return ActionObservation(
        status="success",
        provenance={"transport": "fake"},
        result={"message_id": "m-1"},
    )


class Provider:
    """Counts what it was actually asked to do."""

    def __init__(self, name: str, behaviour=read_success) -> None:
        self.name = name
        self.behaviour = behaviour
        self.calls: list = []

    async def invoke(self, invocation):
        self.calls.append(invocation)
        outcome = self.behaviour(invocation)
        if asyncio.iscoroutine(outcome) or isinstance(outcome, asyncio.Future):
            return await outcome
        return outcome


class Harness:
    """A registry, a policy and an executor wired onto the doubles."""

    def __init__(self, *, sleeper=never_sleep, cancel_grace: float = 0.05) -> None:
        self.bus = FakeBus()
        self.counters = Counters()
        self.supervision = FakeSupervision(self.bus, self.counters)
        self.policy = AuthorizationPolicy()
        self.registry = ActionRegistry(authorization=self.policy)
        self.clock = ManualClock()
        self.executor = ActionExecutor(
            self.registry,
            self.policy,
            supervision=self.supervision,
            counters=self.counters,
            clock=self.clock,
            sleeper=sleeper,
            cancel_grace_seconds=cancel_grace,
        )

    def declare(self, *specs: ActionSpec, module: str = MODULE) -> None:
        for spec in specs:
            self.registry.declare(spec, module=module)

    def bind(self, spec: ActionSpec, provider: Provider, *, destinations=None, module=MODULE):
        return self.registry.bind(
            spec.name, provider, module=module, destinations=destinations
        )

    def ready(self, module: str = MODULE) -> None:
        self.registry.mark_ready(module)

    def allow(self, spec: ActionSpec, *, rule_id: str = "rule") -> AuthorizationRule:
        rule = AuthorizationRule(
            rule_id=rule_id,
            action_name=spec.name,
            destination=CHAT_SCOPE,
            principals=(OPERATOR,),
            natures=(spec.nature,),
            granted_permissions=spec.required_permissions,
        )
        self.policy.grant(rule)
        return rule


_CALL_SEQUENCE = iter(range(1, 10_000))


def make_call(
    spec: ActionSpec,
    *,
    channel: str = CHANNEL_A,
    arguments=None,
    run_id: str = "run-1",
    call_id: str | None = None,
    principal: str = OPERATOR,
    deadline: float = 1000.0,
    version: int | None = None,
) -> ActionCall:
    if arguments is None:
        arguments = {"limit": 5} if spec.nature == "read" else {"text": "bonjour"}
    return ActionCall(
        action_name=spec.name,
        action_version=spec.version if version is None else version,
        arguments=arguments,
        conversation_id="conv-1",
        run_id=run_id,
        call_id=call_id or f"call-{next(_CALL_SEQUENCE)}",
        source_event_id="evt-1",
        destination=Destination(PLATFORM, channel, "chat"),
        principal=principal,
        deadline=deadline,
    )


# --------------------------------------------------------------------------- #
# AC16 — exactly one provider per action and destination scope
# --------------------------------------------------------------------------- #


def test_overlapping_wildcard_and_concrete_bindings_fail_preparation():
    """AC16: the ambiguity is a preparation failure, not a first-call surprise."""

    harness = Harness()
    harness.declare(WRITE_SPEC)
    alpha = Provider("alpha-sender", write_success)
    beta = Provider("beta-sender", write_success)

    harness.bind(WRITE_SPEC, alpha, destinations=CHAT_SCOPE)
    with pytest.raises(AmbiguousBindingError) as raised:
        harness.bind(
            WRITE_SPEC, beta, destinations=Destination(PLATFORM, CHANNEL_A, "chat")
        )

    message = str(raised.value)
    assert WRITE_SPEC.name in message
    assert "alpha-sender" in message
    assert "beta-sender" in message
    # The refused binding left the registry untouched and reached no provider.
    assert [b.provider_name for b in harness.registry.bindings(WRITE_SPEC.name)] == [
        "alpha-sender"
    ]
    assert alpha.calls == [] and beta.calls == []
    assert harness.executor.provider_invocations == 0


def test_overlapping_scope_hierarchy_fails_preparation():
    """AC16: ``chat`` already covers ``chat.reply``; that is one scope, not two."""

    spec = ActionSpec(
        name="chat.reply",
        version=1,
        description="Reply in a thread.",
        argument_schema={"type": "object"},
        result_schema={"type": "object"},
        nature="write",
        required_permissions=(),
        supported_destinations=(CHAT_SCOPE,),
        timeout_seconds=1.0,
        idempotency="key",
    )
    harness = Harness()
    harness.declare(spec)
    broad = Provider("broad-sender", write_success)
    narrow = Provider("narrow-sender", write_success)

    harness.bind(spec, broad, destinations=Destination(PLATFORM, CHANNEL_A, "chat"))
    with pytest.raises(AmbiguousBindingError) as raised:
        harness.bind(
            spec, narrow, destinations=Destination(PLATFORM, CHANNEL_A, "chat.reply")
        )
    assert "broad-sender" in str(raised.value)
    assert "narrow-sender" in str(raised.value)
    assert len(harness.registry.bindings(spec.name)) == 1


def test_a_provider_claiming_two_overlapping_destinations_at_once_registers_nothing():
    """AC16: binding is atomic, so a rejected bind leaves 0 partial bindings."""

    harness = Harness()
    harness.declare(WRITE_SPEC)
    provider = Provider("greedy-sender", write_success)

    with pytest.raises(AmbiguousBindingError):
        harness.bind(
            WRITE_SPEC,
            provider,
            destinations=[
                Destination(PLATFORM, CHANNEL_A, "chat"),
                Destination(PLATFORM, WILDCARD, "chat"),
            ],
        )
    assert harness.registry.bindings(WRITE_SPEC.name) == ()


async def test_disjoint_bindings_each_receive_exactly_their_own_calls():
    """AC16: two disjoint destinations both register and never cross over."""

    harness = Harness()
    harness.declare(WRITE_SPEC)
    alpha = Provider("alpha-sender", write_success)
    beta = Provider("beta-sender", write_success)
    harness.bind(WRITE_SPEC, alpha, destinations=Destination(PLATFORM, CHANNEL_A, "chat"))
    harness.bind(WRITE_SPEC, beta, destinations=Destination(PLATFORM, CHANNEL_B, "chat"))
    harness.ready()
    harness.allow(WRITE_SPEC)

    to_a = await harness.executor.invoke(make_call(WRITE_SPEC, channel=CHANNEL_A))
    to_b = await harness.executor.invoke(make_call(WRITE_SPEC, channel=CHANNEL_B))

    assert to_a.status == "success" and to_b.status == "success"
    assert len(alpha.calls) == 1 and len(beta.calls) == 1
    assert alpha.calls[0].call.destination.channel_id == CHANNEL_A
    assert beta.calls[0].call.destination.channel_id == CHANNEL_B
    assert harness.executor.provider_invocations == 2


def test_the_three_registry_views_are_distinct():
    """R5: only the authorized view may ever be offered to the model."""

    harness = Harness()
    harness.declare(READ_SPEC, WRITE_SPEC)
    assert set(harness.registry.discovered()) == {READ_SPEC.name, WRITE_SPEC.name}
    assert harness.registry.registered_ready() == {}
    assert harness.registry.authorized(principal=OPERATOR) == {}

    harness.bind(READ_SPEC, Provider("reader"))
    # Bound but the module has not passed the readiness barrier yet.
    assert harness.registry.registered_ready() == {}

    harness.ready()
    assert set(harness.registry.registered_ready()) == {READ_SPEC.name}
    # Ready is still not authorized: default-deny has no ambient grant.
    assert harness.registry.authorized(principal=OPERATOR) == {}

    harness.allow(READ_SPEC)
    assert set(harness.registry.authorized(principal=OPERATOR)) == {READ_SPEC.name}
    assert harness.registry.authorized(principal="someone-else") == {}


# --------------------------------------------------------------------------- #
# AC17 — default-deny, re-checked at every call
# --------------------------------------------------------------------------- #


async def test_zero_rules_refuse_a_read_and_a_write_with_zero_invocations():
    """AC17: default-deny applies to reads exactly as it does to writes."""

    harness = Harness()
    harness.declare(READ_SPEC, WRITE_SPEC)
    reader = Provider("reader", read_success)
    sender = Provider("sender", write_success)
    harness.bind(READ_SPEC, reader)
    harness.bind(WRITE_SPEC, sender)
    harness.ready()

    read = await harness.executor.invoke(make_call(READ_SPEC))
    write = await harness.executor.invoke(make_call(WRITE_SPEC))

    assert read.status == "refused" and write.status == "refused"
    assert read.error["code"] == ERROR_NOT_AUTHORIZED
    assert read.provenance["authorization_reason"] == REASON_NO_RULE
    assert reader.calls == [] and sender.calls == []
    assert harness.executor.provider_invocations == 0


async def test_a_read_rule_authorizes_the_read_only():
    """AC17: an explicit grant is scoped to what it names, and to nothing else."""

    harness = Harness()
    harness.declare(READ_SPEC, WRITE_SPEC)
    reader = Provider("reader", read_success)
    sender = Provider("sender", write_success)
    harness.bind(READ_SPEC, reader)
    harness.bind(WRITE_SPEC, sender)
    harness.ready()
    harness.allow(READ_SPEC, rule_id="read-only")

    read = await harness.executor.invoke(make_call(READ_SPEC))
    write = await harness.executor.invoke(make_call(WRITE_SPEC))

    assert read.status == "success"
    assert write.status == "refused"
    assert len(reader.calls) == 1
    assert sender.calls == []


async def test_revoking_between_two_calls_of_one_run_refuses_the_second():
    """AC17: authorization is re-checked per call, never granted for a run."""

    harness = Harness()
    harness.declare(READ_SPEC)
    reader = Provider("reader", read_success)
    harness.bind(READ_SPEC, reader)
    harness.ready()
    rule = harness.allow(READ_SPEC, rule_id="revocable")

    first = await harness.executor.invoke(make_call(READ_SPEC, run_id="run-42"))
    assert harness.policy.revoke(rule.rule_id) is True
    second = await harness.executor.invoke(make_call(READ_SPEC, run_id="run-42"))

    assert first.status == "success"
    assert second.status == "refused"
    assert len(reader.calls) == 1


async def test_a_rule_missing_a_required_permission_refuses():
    """R5: a rule that matches but grants too little is still a refusal."""

    harness = Harness()
    harness.declare(READ_SPEC)
    reader = Provider("reader", read_success)
    harness.bind(READ_SPEC, reader)
    harness.ready()
    harness.policy.grant(
        AuthorizationRule(
            rule_id="incomplete",
            action_name=READ_SPEC.name,
            destination=CHAT_SCOPE,
            principals=(OPERATOR,),
            natures=("read",),
            granted_permissions=(),
        )
    )

    observation = await harness.executor.invoke(make_call(READ_SPEC))
    assert observation.status == "refused"
    assert reader.calls == []


# --------------------------------------------------------------------------- #
# AC18 — the six terminal statuses, one dedicated scenario each
# --------------------------------------------------------------------------- #


async def _terminal(spec, behaviour, *, sleeper=never_sleep, arguments=None, grant=True):
    harness = Harness(sleeper=sleeper)
    harness.declare(spec)
    provider = Provider("sender", behaviour)
    harness.bind(spec, provider)
    harness.ready()
    if grant:
        harness.allow(spec)
    observation = await harness.executor.invoke(make_call(spec, arguments=arguments))
    return harness, provider, observation


async def test_confirmed_send_yields_success():
    """AC18/1: a confirmed external effect with a schema-valid result."""

    harness, provider, observation = await _terminal(WRITE_SPEC, write_success)
    assert observation.status == "success"
    assert observation.result["message_id"] == "m-1"
    assert observation.error is None
    assert observation.provenance["provider"] == "sender"
    assert observation.provenance["transport"] == "fake"
    assert len(provider.calls) == 1


async def test_missing_rule_yields_refused():
    """AC18/2: default-deny is a terminal status, not an exception."""

    harness, provider, observation = await _terminal(WRITE_SPEC, write_success, grant=False)
    assert observation.status == "refused"
    assert provider.calls == []


async def test_arguments_failing_the_schema_yield_error_with_zero_invocations():
    """AC18/3: argument validation happens before any provider is reached."""

    harness, provider, observation = await _terminal(
        READ_SPEC, read_success, arguments={"limit": 0}
    )
    assert observation.status == "error"
    assert observation.error["code"] == ERROR_INVALID_ARGUMENTS
    assert provider.calls == []
    assert harness.executor.provider_invocations == 0


async def test_expired_read_yields_timeout_and_counts_it():
    """AC18/4: a read has no external effect, so an expiry is a certain timeout."""

    harness, provider, observation = await _terminal(
        READ_SPEC, hang, sleeper=immediate_sleep
    )
    assert observation.status == "timeout"
    assert len(provider.calls) == 1
    assert harness.counters.get(COUNTER_ACTION_TIMEOUTS) == 1


async def test_deadline_already_expired_never_reaches_the_provider():
    """R2: a call whose deadline has passed cannot have emitted anything."""

    harness = Harness()
    harness.declare(READ_SPEC)
    provider = Provider("reader", read_success)
    harness.bind(READ_SPEC, provider)
    harness.ready()
    harness.allow(READ_SPEC)
    harness.clock.now = 100.0

    observation = await harness.executor.invoke(make_call(READ_SPEC, deadline=50.0))
    assert observation.status == "timeout"
    assert provider.calls == []
    assert harness.counters.get(COUNTER_ACTION_TIMEOUTS) == 1


async def test_a_confirmation_landing_after_the_deadline_is_never_a_success():
    """R2/AC33: a late response must not become success, even when the race
    itself cannot tell — both the provider and the timer already ready."""

    timers: list[asyncio.Future] = []

    async def parked_sleep(_delay: float) -> None:
        """A timer the test fires itself, so both futures can be made ready."""

        waiter = asyncio.get_running_loop().create_future()
        timers.append(waiter)
        await waiter

    release = asyncio.Event()

    async def emitted_then_confirms(invocation) -> ActionObservation:
        invocation.mark_emitted()
        await release.wait()
        return ActionObservation(
            status="success",
            provenance={"transport": "fake"},
            result={"message_id": "m-1"},
        )

    harness = Harness(sleeper=parked_sleep)
    harness.declare(WRITE_SPEC)
    provider = Provider("sender", emitted_then_confirms)
    harness.bind(WRITE_SPEC, provider)
    harness.ready()
    harness.allow(WRITE_SPEC)

    call = make_call(WRITE_SPEC, call_id="late-1", deadline=1000.0)
    task = asyncio.ensure_future(harness.executor.invoke(call))
    for _ in range(20):
        await asyncio.sleep(0)
    assert len(timers) == 1  # the executor is parked on provider and timer

    # The confirmation is released and the timer fires, all before the parked
    # executor resumes: both futures are done, and the clock is past the
    # deadline, so the race cannot be ordered by completion alone.
    release.set()
    timers[0].set_result(None)
    harness.clock.now = 1000.0

    observation = await task
    assert observation.status == "external_unknown"
    assert observation.result is None
    assert observation.provenance["emission"] == EMISSION_EMITTED
    assert (observation.error or {}).get("code") == ERROR_EXTERNAL_UNKNOWN
    assert len(provider.calls) == 1
    statuses = [value.status for value in harness.executor.outcomes().values()]
    assert statuses == ["external_unknown"]
    assert statuses.count("success") == 0
    assert harness.counters.get(COUNTER_ACTION_TIMEOUTS) == 0


async def test_a_confirmation_landing_in_time_survives_a_late_adoption():
    """R5 / design §3.3: confirmed success is judged by when the confirmation
    *arrived*, not by when the executor got around to adopting it.

    The provider confirms at t=9 against a deadline of t=10; the executor's
    task is resumed only at t=11. That is a timely confirmation the loop
    delivered late, not a missing one — turning it into ``external_unknown``
    would report an uncertain delivery for a send the transport confirmed.
    """

    timers: list[asyncio.Future] = []

    async def parked_sleep(_delay: float) -> None:
        waiter = asyncio.get_running_loop().create_future()
        timers.append(waiter)
        await waiter

    release = asyncio.Event()

    async def emitted_then_confirms(invocation) -> ActionObservation:
        invocation.mark_emitted()
        await release.wait()
        return ActionObservation(
            status="success",
            provenance={"transport": "fake"},
            result={"message_id": "m-timely"},
        )

    harness = Harness(sleeper=parked_sleep)
    harness.declare(WRITE_SPEC)
    provider = Provider("sender", emitted_then_confirms)
    harness.bind(WRITE_SPEC, provider)
    harness.ready()
    harness.allow(WRITE_SPEC)

    # Entered at t=6 with a 5 s spec timeout, the call deadline (t=10) is the
    # limit that applies.
    harness.clock.now = 6.0
    call = make_call(WRITE_SPEC, call_id="timely-1", deadline=10.0)
    task = asyncio.ensure_future(harness.executor.invoke(call))
    for _ in range(20):
        await asyncio.sleep(0)
    assert len(timers) == 1

    # The confirmation arrives at t=9 — the provider's own task runs and is
    # stamped before the executor is resumed at all.
    harness.clock.now = 9.0
    release.set()
    (invocation,) = provider.calls
    for _ in range(20):
        if invocation.provider_completed_at is not None:
            break
        await asyncio.sleep(0)
    assert invocation.provider_completed_at == 9.0
    assert not task.done()  # adoption has not happened yet

    # The executor only resumes at t=11, past the deadline.
    harness.clock.now = 11.0

    observation = await task
    assert observation.status == "success"
    assert observation.result == {"message_id": "m-timely"}
    assert observation.provenance["emission"] == EMISSION_EMITTED
    statuses = [value.status for value in harness.executor.outcomes().values()]
    assert statuses == ["success"]
    assert harness.counters.get(COUNTER_ACTION_TIMEOUTS) == 0
    assert [p["status"] for p in harness.bus.of_type(TRACE_ACTION_COMPLETED)] == [
        "success"
    ]


async def test_a_confirmation_after_the_spec_timeout_is_never_a_success():
    """R2/AC33: the limit that applies is the shorter of the spec's timeout and
    the call deadline — a confirmation past the spec timeout is late even while
    the call deadline is still far in the future."""

    timers: list[asyncio.Future] = []

    async def parked_sleep(_delay: float) -> None:
        waiter = asyncio.get_running_loop().create_future()
        timers.append(waiter)
        await waiter

    release = asyncio.Event()

    async def emitted_then_confirms(invocation) -> ActionObservation:
        invocation.mark_emitted()
        await release.wait()
        return ActionObservation(
            status="success",
            provenance={"transport": "fake"},
            result={"message_id": "m-late"},
        )

    harness = Harness(sleeper=parked_sleep)
    harness.declare(WRITE_SPEC)  # timeout_seconds=5.0
    provider = Provider("sender", emitted_then_confirms)
    harness.bind(WRITE_SPEC, provider)
    harness.ready()
    harness.allow(WRITE_SPEC)

    call = make_call(WRITE_SPEC, call_id="late-spec-1", deadline=1000.0)
    task = asyncio.ensure_future(harness.executor.invoke(call))
    for _ in range(20):
        await asyncio.sleep(0)
    assert len(timers) == 1

    # Past the spec's 5 s timeout, nowhere near the 1000 s deadline; both the
    # confirmation and the timer are ready when the executor resumes.
    release.set()
    timers[0].set_result(None)
    harness.clock.now = 6.0

    observation = await task
    assert observation.status == "external_unknown"
    assert observation.result is None
    assert observation.provenance["emission"] == EMISSION_EMITTED
    assert (observation.error or {}).get("code") == ERROR_EXTERNAL_UNKNOWN
    statuses = [value.status for value in harness.executor.outcomes().values()]
    assert statuses == ["external_unknown"]
    assert statuses.count("success") == 0
    assert harness.counters.get(COUNTER_ACTION_TIMEOUTS) == 0


async def test_cancelled_call_with_no_emitted_effect_yields_cancelled():
    """AC18/5: cancellation below the executor is an outcome, not a propagation."""

    def cancelled(invocation):
        invocation.mark_not_emitted()
        raise asyncio.CancelledError

    harness, provider, observation = await _terminal(WRITE_SPEC, cancelled)
    assert observation.status == "cancelled"
    assert len(provider.calls) == 1
    assert harness.counters.get(COUNTER_ACTION_TIMEOUTS) == 0


async def test_lost_response_after_emission_yields_external_unknown():
    """AC18/6: an effect that may be on the wire is never reported as timed out."""

    async def emitted_then_lost(invocation):
        invocation.mark_emitted()
        await asyncio.Event().wait()

    harness, provider, observation = await _terminal(
        WRITE_SPEC, emitted_then_lost, sleeper=immediate_sleep
    )
    assert observation.status == "external_unknown"
    assert observation.provenance["emission"] == EMISSION_EMITTED
    assert len(provider.calls) == 1
    # An uncertain effect is not a timeout, and is never retried.
    assert harness.counters.get(COUNTER_ACTION_TIMEOUTS) == 0
    assert harness.executor.provider_invocations == 1


async def test_a_silent_write_defaults_to_external_unknown_on_timeout():
    """R5 risk: absent an explicit signal, a write's timeout stays uncertain."""

    harness, provider, observation = await _terminal(
        WRITE_SPEC, hang, sleeper=immediate_sleep
    )
    assert observation.status == "external_unknown"
    assert harness.counters.get(COUNTER_ACTION_TIMEOUTS) == 0


async def test_the_six_terminal_statuses_are_produced_exactly():
    """AC18: six dedicated scenarios, six distinct statuses, no seventh."""

    async def emitted_then_lost(invocation):
        invocation.mark_emitted()
        await asyncio.Event().wait()

    def cancelled(invocation):
        invocation.mark_not_emitted()
        raise asyncio.CancelledError

    produced = set()
    for spec, behaviour, sleeper, arguments, grant in (
        (WRITE_SPEC, write_success, never_sleep, None, True),
        (WRITE_SPEC, write_success, never_sleep, None, False),
        (READ_SPEC, read_success, never_sleep, {"limit": 0}, True),
        (READ_SPEC, hang, immediate_sleep, None, True),
        (WRITE_SPEC, cancelled, never_sleep, None, True),
        (WRITE_SPEC, emitted_then_lost, immediate_sleep, None, True),
    ):
        _harness, _provider, observation = await _terminal(
            spec, behaviour, sleeper=sleeper, arguments=arguments, grant=grant
        )
        produced.add(observation.status)

    assert produced == set(TERMINAL_STATUSES)


async def test_result_violating_the_result_schema_yields_error_and_zero_successes():
    """AC18: a success is only reported once its result has been validated."""

    def malformed(invocation):
        invocation.mark_emitted()
        return ActionObservation(
            status="success",
            provenance={"transport": "fake"},
            result={"message_id": 17},
        )

    harness, provider, observation = await _terminal(WRITE_SPEC, malformed)

    assert observation.status == "error"
    assert observation.error["code"] == ERROR_INVALID_RESULT
    assert observation.result is None
    assert len(provider.calls) == 1
    recorded = [o.status for o in harness.executor.outcomes().values()]
    assert recorded.count("success") == 0
    completed = harness.bus.of_type(TRACE_ACTION_COMPLETED)
    assert [payload["status"] for payload in completed] == ["error"]


async def test_a_provider_failure_that_never_emitted_is_an_error():
    """R5: an ordinary provider fault stays an ``error``, not an uncertainty."""

    def boom(invocation):
        invocation.mark_not_emitted()
        raise RuntimeError("transport refused the frame")

    harness, provider, observation = await _terminal(WRITE_SPEC, boom)
    assert observation.status == "error"
    assert "transport refused the frame" in observation.error["message"]


async def test_an_unready_module_is_an_error_with_zero_invocations():
    """R5: the readiness barrier gates invocation, not only the view."""

    harness = Harness()
    harness.declare(READ_SPEC)
    provider = Provider("reader", read_success)
    harness.bind(READ_SPEC, provider)
    harness.allow(READ_SPEC)  # authorized, but the module never became ready

    observation = await harness.executor.invoke(make_call(READ_SPEC))
    assert observation.status == "error"
    assert observation.error["code"] == "provider_not_ready"
    assert provider.calls == []


# --------------------------------------------------------------------------- #
# R2/R5 — the interruption paths cannot be outwaited or talked around
# --------------------------------------------------------------------------- #


async def test_time_spent_before_invocation_is_taken_out_of_the_budget():
    """R2: the budget is measured at the provider's door, not at ``invoke``'s.

    Publishing ``action.started`` and validating the arguments take time. If the
    budget were still measured from the entry instant, a deadline consumed by
    those steps would leave the provider a positive budget and a licence to emit
    after expiry.
    """

    harness = Harness()
    harness.declare(WRITE_SPEC)
    provider = Provider("sender", write_success)
    harness.bind(WRITE_SPEC, provider)
    harness.ready()
    harness.allow(WRITE_SPEC)

    def burn_the_deadline(event_type, _payload):
        if event_type == TRACE_ACTION_STARTED:
            harness.clock.now = 20.0

    harness.bus.on_publish = burn_the_deadline

    observation = await harness.executor.invoke(make_call(WRITE_SPEC, deadline=10.0))

    assert observation.status == "timeout"
    assert provider.calls == []
    assert harness.executor.provider_invocations == 0
    assert observation.provenance["emission"] == "not_emitted"


async def test_a_provider_that_swallows_its_cancellation_still_terminates():
    """R8: cancellation is a request, so the wait on it has to be bounded.

    A provider that catches ``CancelledError`` and keeps waiting must not be
    able to hold ``invoke`` open for ever — that is the one outcome R8 forbids:
    a call with no terminal observation at all.
    """

    entered = asyncio.Event()
    swallowed = asyncio.Event()
    release = asyncio.Event()

    async def stubborn(invocation):
        invocation.mark_emitted()
        entered.set()
        while True:
            try:
                await release.wait()
            except asyncio.CancelledError:
                # Refuses to die for as long as the test keeps it held.
                swallowed.set()
                if release.is_set():
                    raise
                continue
            return ActionObservation(
                status="success", provenance={}, result={"message_id": "late"}
            )

    harness = Harness(sleeper=immediate_sleep, cancel_grace=0.01)
    harness.declare(WRITE_SPEC)
    provider = Provider("sender", stubborn)
    harness.bind(WRITE_SPEC, provider)
    harness.ready()
    harness.allow(WRITE_SPEC)

    call = make_call(WRITE_SPEC)
    observation = await asyncio.wait_for(harness.executor.invoke(call), timeout=5.0)

    assert entered.is_set()
    assert swallowed.is_set()  # the grace really did expire on a live task
    assert observation.status == "external_unknown"
    assert harness.executor.outcome(call.call_id) is observation
    assert [p["status"] for p in harness.bus.of_type(TRACE_ACTION_COMPLETED)] == [
        "external_unknown"
    ]

    # Let the abandoned task finish so the loop is not left holding it. Its late
    # success is consumed and discarded: the call already terminated.
    release.set()
    for _ in range(4):
        await asyncio.sleep(0)
    assert harness.executor.outcome(call.call_id).status == "external_unknown"


async def test_a_returned_timeout_after_an_emission_is_still_external_unknown():
    """R5: the emission rule applies to the provider's own report as well.

    A provider may catch its transport's timeout and return a well-formed
    ``timeout`` observation. Having already signalled an emission, that report
    is exactly as uncertain as a timeout the executor detects, and adopting it
    unchanged would slip a possibly-sent write past the uncertainty barrier.
    """

    def emitted_then_gave_up(invocation):
        invocation.mark_emitted()
        return ActionObservation(
            status="timeout",
            provenance={"transport": "fake"},
            error={"code": "timed_out", "message": "no ack", "retryable": True},
        )

    harness, provider, observation = await _terminal(WRITE_SPEC, emitted_then_gave_up)

    assert observation.status == "external_unknown"
    assert observation.provenance["emission"] == EMISSION_EMITTED
    assert harness.counters.get(COUNTER_ACTION_TIMEOUTS) == 0
    assert len(provider.calls) == 1


async def test_a_silent_write_returning_cancelled_is_still_external_unknown():
    """R5: an unqualified write is uncertain however the interruption arrived."""

    def gave_up_quietly(_invocation):
        return ActionObservation(
            status="cancelled",
            provenance={"transport": "fake"},
            error={"code": "cancelled", "message": "aborted", "retryable": False},
        )

    _harness, _provider, observation = await _terminal(WRITE_SPEC, gave_up_quietly)
    assert observation.status == "external_unknown"


async def test_a_returned_timeout_on_a_read_is_adopted_as_it_stands():
    """R5: a read has no effect to duplicate, so its own report is kept."""

    def timed_out(_invocation):
        return ActionObservation(
            status="timeout",
            provenance={"transport": "fake"},
            error={"code": "timed_out", "message": "no ack", "retryable": True},
        )

    harness, _provider, observation = await _terminal(READ_SPEC, timed_out)
    assert observation.status == "timeout"
    assert observation.provenance["transport"] == "fake"
    assert harness.counters.get(COUNTER_ACTION_TIMEOUTS) == 1


# --------------------------------------------------------------------------- #
# R8 — the terminal state is recorded before its trace is published
# --------------------------------------------------------------------------- #


async def test_failed_completed_publication_leaves_the_observation_recorded():
    """R8: a lost trace never cancels, repeats or downgrades an external effect."""

    harness = Harness()
    harness.declare(WRITE_SPEC)
    provider = Provider("sender", write_success)
    harness.bind(WRITE_SPEC, provider)
    harness.ready()
    harness.allow(WRITE_SPEC)
    harness.bus.failing.add(TRACE_ACTION_COMPLETED)

    call = make_call(WRITE_SPEC)
    seen: list[tuple[str, object]] = []
    harness.bus.on_publish = lambda event_type, _payload: seen.append(
        (event_type, harness.executor.outcome(call.call_id))
    )

    observation = await harness.executor.invoke(call)

    # The publication failed, yet invoke still returns the terminal outcome...
    assert observation.status == "success"
    assert harness.executor.outcome(call.call_id) is observation
    # ...which was already durable when the publication was attempted.
    assert dict(seen)[TRACE_ACTION_STARTED] is None
    assert dict(seen)[TRACE_ACTION_COMPLETED] is observation
    # The effect happened once, was not retried, and the loss is visible.
    assert len(provider.calls) == 1
    assert harness.executor.provider_invocations == 1
    assert harness.counters.get(COUNTER_LOST_TRACES) == 1
    assert harness.bus.of_type(TRACE_ACTION_COMPLETED) == []


async def test_replaying_a_recorded_call_id_reaches_zero_providers():
    """R8: a caller retrying after a lost trace reads the recorded outcome."""

    harness = Harness()
    harness.declare(WRITE_SPEC)
    provider = Provider("sender", write_success)
    harness.bind(WRITE_SPEC, provider)
    harness.ready()
    harness.allow(WRITE_SPEC)

    call = make_call(WRITE_SPEC)
    first = await harness.executor.invoke(call)
    second = await harness.executor.invoke(call)

    assert second is first
    assert len(provider.calls) == 1
    assert len(harness.bus.of_type(TRACE_ACTION_COMPLETED)) == 1


async def test_every_invocation_emits_started_and_completed_with_the_correlation():
    """R8: action, provider, call_id, terminal status and duration, every time."""

    harness = Harness()
    harness.declare(WRITE_SPEC)
    provider = Provider("sender", write_success)
    harness.bind(WRITE_SPEC, provider)
    harness.ready()
    harness.allow(WRITE_SPEC)
    harness.clock.now = 10.0

    call = make_call(WRITE_SPEC)

    original_invoke = provider.invoke

    async def ticking(invocation):
        harness.clock.advance(0.5)
        return await original_invoke(invocation)

    provider.invoke = ticking
    await harness.executor.invoke(call)

    started = harness.bus.of_type(TRACE_ACTION_STARTED)
    completed = harness.bus.of_type(TRACE_ACTION_COMPLETED)
    assert len(started) == 1 and len(completed) == 1
    assert started[0]["action"] == WRITE_SPEC.name
    assert started[0]["provider"] == "sender"
    assert started[0]["call_id"] == call.call_id
    assert completed[0]["action"] == WRITE_SPEC.name
    assert completed[0]["provider"] == "sender"
    assert completed[0]["call_id"] == call.call_id
    assert completed[0]["run_id"] == call.run_id
    assert completed[0]["status"] == "success"
    assert completed[0]["duration_seconds"] == pytest.approx(0.5)
    # A trace of an action never carries the prompt or the reasoning.
    assert set(completed[0]) & {"prompt", "reasoning"} == set()


async def test_a_refused_call_still_emits_its_started_and_completed_pair():
    """R8: correlation must not depend on the outcome being a success."""

    harness = Harness()
    harness.declare(READ_SPEC)
    harness.bind(READ_SPEC, Provider("reader"))
    harness.ready()

    await harness.executor.invoke(make_call(READ_SPEC))
    assert len(harness.bus.of_type(TRACE_ACTION_STARTED)) == 1
    assert harness.bus.of_type(TRACE_ACTION_COMPLETED)[0]["status"] == "refused"


async def test_outer_cancellation_records_the_outcome_before_it_propagates():
    """R8: a run cancelled from above never loses a possibly-emitted effect."""

    harness = Harness()
    harness.declare(WRITE_SPEC)
    entered = asyncio.Event()

    async def emitted_then_hang(invocation):
        invocation.mark_emitted()
        entered.set()
        await asyncio.Event().wait()

    provider = Provider("sender", emitted_then_hang)
    harness.bind(WRITE_SPEC, provider)
    harness.ready()
    harness.allow(WRITE_SPEC)

    call = make_call(WRITE_SPEC)
    task = asyncio.ensure_future(harness.executor.invoke(call))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    recorded = harness.executor.outcome(call.call_id)
    assert recorded is not None
    assert recorded.status == "external_unknown"
    assert len(provider.calls) == 1


async def test_a_completion_hook_runs_after_the_recorded_outcome_and_its_trace():
    """R8/AC27 (P24 F8): a fact a provider defers through the invocation's
    completion hook follows the executor's own ``action.completed``.

    The hook runs once the observation is recorded and its trace published —
    it reads both — and exactly once; a hook that raises is the provider's own
    and neither reaches the returned outcome nor stops the hooks after it."""

    harness = Harness()
    harness.declare(WRITE_SPEC)
    seen: list[tuple[object, int]] = []
    order: list[str] = []

    def confirmed_then_defers(invocation):
        invocation.mark_emitted()

        def explode() -> None:
            order.append("explode")
            raise RuntimeError("the provider's own")

        def fact() -> None:
            order.append("fact")
            seen.append(
                (
                    harness.executor.outcome(invocation.call.call_id),
                    len(harness.bus.of_type(TRACE_ACTION_COMPLETED)),
                )
            )

        invocation.after_completion(explode)
        invocation.after_completion(fact)
        order.append("returned")
        return ActionObservation(
            status="success",
            provenance={"transport": "fake"},
            result={"message_id": "m-1"},
        )

    provider = Provider("sender", confirmed_then_defers)
    harness.bind(WRITE_SPEC, provider)
    harness.ready()
    harness.allow(WRITE_SPEC)

    call = make_call(WRITE_SPEC)
    observation = await harness.executor.invoke(call)

    assert observation.status == "success"
    assert order == ["returned", "explode", "fact"]
    (recorded, completed_traces) = seen[0]
    assert recorded is observation
    assert completed_traces == 1
    assert len(seen) == 1
    (invocation,) = provider.calls
    # Registered once the call is terminal, a hook runs at once, exactly once.
    invocation.after_completion(lambda: order.append("late"))
    assert order == ["returned", "explode", "fact", "late"]
    with pytest.raises(ContractError):
        invocation.after_completion("not callable")


async def test_a_completion_hook_still_runs_when_the_call_is_cancelled_from_above():
    """R8/AC27: an outer cancellation publishes the terminal trace and then
    runs the hooks — a fact confirmed before the cancellation is never lost."""

    harness = Harness()
    harness.declare(WRITE_SPEC)
    entered = asyncio.Event()
    seen: list[tuple[str | None, int]] = []

    async def confirmed_then_hang(invocation):
        invocation.mark_emitted()
        invocation.after_completion(
            lambda: seen.append(
                (
                    getattr(harness.executor.outcome(invocation.call.call_id), "status", None),
                    len(harness.bus.of_type(TRACE_ACTION_COMPLETED)),
                )
            )
        )
        entered.set()
        await asyncio.Event().wait()

    provider = Provider("sender", confirmed_then_hang)
    harness.bind(WRITE_SPEC, provider)
    harness.ready()
    harness.allow(WRITE_SPEC)

    call = make_call(WRITE_SPEC)
    task = asyncio.ensure_future(harness.executor.invoke(call))
    await entered.wait()
    assert seen == []
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert seen == [("external_unknown", 1)]


# --------------------------------------------------------------------------- #
# P1 contract validation (R3, R5)
# --------------------------------------------------------------------------- #


def test_distinct_session_triples_never_serialise_equally():
    """R3: length prefixes make separators in the components harmless."""

    triples = [
        ("twitch", "a", "bc"),
        ("twitch", "ab", "c"),
        ("twitch", "a|b", "c"),
        ("twitch", "a", "|b|c"),
        ("twitch", "1:a", "b"),
        ("twitch", "1", ":ab"),
        ("twitch|a", "b", "c"),
        ("twitch", "b", "c"),
    ]
    rendered = {SessionKey(*triple).serialize() for triple in triples}
    assert len(rendered) == len(triples)


def test_session_key_refuses_an_empty_component():
    """R3: an empty component would make two different sessions look alike."""

    for triple in (("", "c", "v"), ("p", "", "v"), ("p", "c", "  ")):
        with pytest.raises(ContractError) as raised:
            SessionKey(*triple)
        assert "SessionKey." in str(raised.value)


def test_an_observation_outside_the_six_terminal_statuses_is_refused():
    """R5: the terminal set is closed, so no status can mean 'probably fine'."""

    with pytest.raises(ContractError) as raised:
        ActionObservation(status="delivered", provenance={"provider": "p"})
    assert "ActionObservation.status" in str(raised.value)
    assert set(TERMINAL_STATUSES) == {
        "success",
        "refused",
        "error",
        "timeout",
        "cancelled",
        "external_unknown",
    }


def test_an_action_spec_refuses_a_nature_outside_read_and_write():
    """R5: nature drives the conservative timeout default; it cannot be free text."""

    with pytest.raises(ContractError) as raised:
        ActionSpec(
            name="chat.sudo",
            version=1,
            description="Neither a read nor a write.",
            argument_schema={"type": "object"},
            result_schema={"type": "object"},
            nature="admin",
            required_permissions=(),
            supported_destinations=(CHAT_SCOPE,),
            timeout_seconds=1.0,
            idempotency="none",
        )
    assert "ActionSpec.nature" in str(raised.value)
