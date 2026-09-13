"""Action binding, default-deny authorization and terminal outcomes (R5, R8).

Nothing here sleeps, opens a transport or reads a real clock: the executor's
clock and its timeout sleeper are both injected, so a timeout is provoked by
letting the injected sleeper return rather than by waiting for one.

The last section carries the P1 contract assertions the plan attaches to this
step: a length-prefixed :class:`~core.contracts.SessionKey` serialisation that
adversarial separators cannot collide, and the terminal-status and nature
constraints that keep an unknown status out of an observation (R3, R5).

Phase 1 (R4) adds the executor-side checks on observation ``parts``: an
``image_ref`` must be leased to the call's run, unexpired on the injected
clock and of the stored size, the observation must fit the executor's byte
bound, and a rejected observation releases every image it named (AC22, AC47);
and the not-ready provider outcome becomes ``refused`` (R6, AC35).
"""

import asyncio
from dataclasses import replace

import pytest

from core.actions import (
    COUNTER_ACTION_TIMEOUTS,
    COUNTER_LOST_TRACES,
    EMISSION_EMITTED,
    ERROR_EXTERNAL_UNKNOWN,
    ERROR_INVALID_ARGUMENTS,
    ERROR_INVALID_RESULT,
    ERROR_NOT_AUTHORIZED,
    ERROR_OBSERVATION_TOO_LARGE,
    ERROR_PROVIDER_NOT_READY,
    ERROR_UNKNOWN_ACTION,
    REASON_NO_RULE,
    ActionExecutor,
    ActionRegistry,
    AmbiguousBindingError,
    AuthorizationPolicy,
    AuthorizationRule,
)
from core.attachments import AttachmentRef, AttachmentStore
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

    def __init__(
        self,
        *,
        sleeper=never_sleep,
        cancel_grace: float = 0.05,
        clock: ManualClock | None = None,
        attachments: AttachmentStore | None = None,
        max_observation_bytes: int | None = None,
    ) -> None:
        self.bus = FakeBus()
        self.counters = Counters()
        self.supervision = FakeSupervision(self.bus, self.counters)
        self.policy = AuthorizationPolicy()
        self.registry = ActionRegistry(authorization=self.policy)
        self.clock = clock if clock is not None else ManualClock()
        self.attachments = attachments
        self.executor = ActionExecutor(
            self.registry,
            self.policy,
            supervision=self.supervision,
            counters=self.counters,
            clock=self.clock,
            sleeper=sleeper,
            cancel_grace_seconds=cancel_grace,
            attachments=attachments,
            max_observation_bytes=max_observation_bytes,
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


async def test_an_unready_module_is_refused_with_zero_invocations():
    """R5/R6: the readiness barrier gates invocation, not only the view.

    Replaces the phase-0 assertion that a not-ready provider was an ``error``:
    phase 1 (plan step P3, R6/AC35) classifies it as ``refused`` with the
    same ``provider_not_ready`` code, so an action whose provider disconnected
    mid-run reads to the model as a capability that declined, never as a
    broken action. The guarantees kept: 0 provider invocations, the code.
    """

    harness = Harness()
    harness.declare(READ_SPEC)
    provider = Provider("reader", read_success)
    harness.bind(READ_SPEC, provider)
    harness.allow(READ_SPEC)  # authorized, but the module never became ready

    observation = await harness.executor.invoke(make_call(READ_SPEC))
    assert observation.status == "refused"
    assert observation.error["code"] == ERROR_PROVIDER_NOT_READY == "provider_not_ready"
    assert observation.result is None
    assert observation.parts == ()
    assert provider.calls == []
    assert harness.executor.provider_invocations == 0
    completed = harness.bus.of_type(TRACE_ACTION_COMPLETED)
    assert [(p["status"], p["error_code"]) for p in completed] == [
        ("refused", "provider_not_ready")
    ]


async def test_a_module_losing_readiness_mid_run_is_refused_from_then_on():
    """R6/AC35: a provider that disconnects between two calls of one run.

    The first call reaches the provider; after ``mark_not_ready`` the second
    is refused with ``provider_not_ready`` and adds no invocation, and the
    outcome is a refusal — not an error — so the run continues with it.
    """

    harness = Harness()
    harness.declare(READ_SPEC)
    provider = Provider("reader", read_success)
    harness.bind(READ_SPEC, provider)
    harness.ready()
    harness.allow(READ_SPEC)

    first = await harness.executor.invoke(make_call(READ_SPEC, run_id="run-9"))
    assert first.status == "success"
    harness.registry.mark_not_ready(MODULE)

    second = await harness.executor.invoke(make_call(READ_SPEC, run_id="run-9"))
    assert second.status == "refused"
    assert second.error["code"] == ERROR_PROVIDER_NOT_READY
    assert len(provider.calls) == 1
    assert harness.executor.provider_invocations == 1


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
# R4 — observation parts: image leases, stored size, byte bound (AC22, AC47)
# --------------------------------------------------------------------------- #

MIB = 1024 * 1024
MAX_OBSERVATION_BYTES = 5 * MIB
"""AC47's bound: 5 242 880 bytes."""

_ATTACHMENT_LIMITS = {
    "max_object_bytes": 8 * MIB,
    "max_objects": 16,
    "max_total_bytes": 32 * MIB,
    "max_bytes_per_run": 16 * MIB,
    "ttl_seconds": 60.0,
}


def _store(clock: ManualClock, **overrides) -> AttachmentStore:
    """A generous store on the executor's own clock, so expiry is judged once."""

    return AttachmentStore(clock=clock, **{**_ATTACHMENT_LIMITS, **overrides})


def _image_harness(**overrides) -> tuple[Harness, AttachmentStore]:
    """A ready, authorized reader whose observations may carry images."""

    clock = ManualClock()
    store = AttachmentStore(clock=clock, **{**_ATTACHMENT_LIMITS, **overrides})
    # One injected clock for the store and the executor: expiry is judged once.
    harness = Harness(
        clock=clock, attachments=store, max_observation_bytes=MAX_OBSERVATION_BYTES
    )
    harness.declare(READ_SPEC)
    harness.provider = Provider("capture")
    harness.bind(READ_SPEC, harness.provider)
    harness.ready()
    harness.allow(READ_SPEC)
    return harness, store


def _image_ref(ref: AttachmentRef, **overrides) -> dict:
    part = {
        "type": "image_ref",
        "attachment_id": ref.attachment_id,
        "content_type": ref.content_type,
        "size": ref.size,
        "width": 1920,
        "height": 1080,
        "captured_at": ref.created_at,
        "provider_id": "capture",
    }
    part.update(overrides)
    return part


def _text(size: int) -> dict:
    return {"type": "text", "text": "a" * size}


def _observing(parts, *, status: str = "success"):
    """A read provider behaviour returning the given parts on *status*."""

    def behaviour(_invocation) -> ActionObservation:
        if status == "success":
            return ActionObservation(
                status="success",
                provenance={"transport": "fake"},
                result={"lines": []},
                parts=parts,
            )
        return ActionObservation(
            status=status,
            provenance={"transport": "fake"},
            error={"code": "upstream", "message": "the source failed", "retryable": False},
            parts=parts,
        )

    return behaviour


async def _observe(harness: Harness, parts, *, status: str = "success", run_id: str = "run-1"):
    """Have the harness's bound provider answer the next call with *parts*."""

    provider = harness.provider
    provider.behaviour = _observing(parts, status=status)
    observation = await harness.executor.invoke(make_call(READ_SPEC, run_id=run_id))
    return provider, observation


def _assert_rejected(observation: ActionObservation, code: str) -> None:
    """The invariant: a rejected observation adopts nothing of the provider's."""

    assert observation.status == "error"
    assert observation.error["code"] == code
    assert observation.result is None
    assert observation.parts == ()
    assert observation.provenance.get("transport") is None
    assert observation.provenance["provider_entered"] is True


async def test_a_valid_image_ref_leased_to_the_run_is_adopted_with_parts_intact():
    """AC22 (positive): a lease held by this run, unexpired, of the stored size."""

    harness, store = _image_harness()
    ref = store.put("run-1", b"\x89PNG" + b"\x00" * 60, content_type="image/png")
    parts = [_text(12), _image_ref(ref)]

    provider, observation = await _observe(harness, parts)

    assert observation.status == "success"
    assert observation.result["lines"] == ()
    assert observation.parts == tuple(parts)
    assert observation.parts[1]["attachment_id"] == ref.attachment_id
    assert observation.provenance["transport"] == "fake"
    assert len(provider.calls) == 1
    # Adopting neither releases nor touches the lease: it is the run's until
    # its terminal record releases it.
    assert store.usage("run-1").objects == 1
    assert store.lookup(ref.attachment_id) == ref


def _leased_to_another_run(store: AttachmentStore, clock: ManualClock) -> dict:
    ref = store.put("run-2", b"\x00" * 64, content_type="image/png")
    return _image_ref(ref)


def _expired_on_the_clock(store: AttachmentStore, clock: ManualClock) -> dict:
    ref = store.put("run-1", b"\x00" * 64, content_type="image/png")
    clock.advance(_ATTACHMENT_LIMITS["ttl_seconds"])  # exactly at the deadline: expired
    return _image_ref(ref)


def _size_off_by_one(store: AttachmentStore, clock: ManualClock) -> dict:
    ref = store.put("run-1", b"\x00" * 64, content_type="image/png")
    return _image_ref(ref, size=ref.size + 1)


def _size_under_by_one(store: AttachmentStore, clock: ManualClock) -> dict:
    ref = store.put("run-1", b"\x00" * 64, content_type="image/png")
    return _image_ref(ref, size=ref.size - 1)


def _never_issued(store: AttachmentStore, clock: ManualClock) -> dict:
    # Nothing is stored: the observation names an identifier this store never
    # handed out, and the discard of an unknown identifier is a no-op.
    ref = AttachmentRef("0" * 32, "run-1", "image/png", 64, clock(), clock() + 60.0)
    return _image_ref(ref)


@pytest.mark.parametrize(
    "make_part",
    [
        _leased_to_another_run,
        _expired_on_the_clock,
        _size_off_by_one,
        _size_under_by_one,
        _never_issued,
    ],
    ids=["another_run", "expired", "size_plus_one", "size_minus_one", "never_issued"],
)
async def test_a_bad_image_lease_is_invalid_result_and_releases_the_object(make_part):
    """AC22, AC47 tail: wrong run, expired, or a size 1 byte off the stored one.

    The observation becomes ``error invalid_result``, nothing of the provider's
    result or parts is adopted, and every object the observation named is
    released at once: the store holds 0 objects for either run before any
    model request could carry the image.
    """

    harness, store = _image_harness()
    part = make_part(store, harness.clock)

    provider, observation = await _observe(harness, [_text(3), part])

    _assert_rejected(observation, ERROR_INVALID_RESULT)
    assert "parts[1]" in observation.error["message"]
    assert len(provider.calls) == 1
    assert store.object_count == 0
    assert store.usage("run-1").objects == 0
    assert store.usage("run-2").objects == 0
    assert store.total_bytes == 0
    # Idempotent with the run-end release: nothing is freed twice.
    assert store.release("run-1").objects == 0
    assert store.release("run-2").objects == 0
    completed = harness.bus.of_type(TRACE_ACTION_COMPLETED)
    assert [(p["status"], p["error_code"]) for p in completed] == [("error", "invalid_result")]


async def test_a_lease_of_the_run_that_expired_is_judged_on_the_executor_clock():
    """AC22: one second before the deadline is a lease; at the deadline it is not."""

    harness, store = _image_harness()
    ref = store.put("run-1", b"\x00" * 64, content_type="image/png")
    harness.clock.advance(_ATTACHMENT_LIMITS["ttl_seconds"] - 1.0)

    _provider, observation = await _observe(harness, [_image_ref(ref)])
    assert observation.status == "success"
    assert len(observation.parts) == 1

    harness.clock.advance(1.0)
    _provider, late = await _observe(harness, [_image_ref(ref)])
    _assert_rejected(late, ERROR_INVALID_RESULT)
    assert "expired" in late.error["message"]
    assert store.object_count == 0


async def test_one_bad_lease_rejects_the_whole_observation_and_releases_every_image():
    """R4 invariant: no partially adopted observation.

    Two images, the first a valid lease of this run, the second leased to
    another run: the observation is rejected whole and **both** objects are
    released — the valid one is not kept leased behind an error.
    """

    harness, store = _image_harness()
    mine = store.put("run-1", b"\x00" * 64, content_type="image/png")
    theirs = store.put("run-2", b"\x00" * 64, content_type="image/jpeg")

    _provider, observation = await _observe(harness, [_image_ref(mine), _image_ref(theirs)])

    _assert_rejected(observation, ERROR_INVALID_RESULT)
    assert store.lookup(mine.attachment_id) is None
    assert store.lookup(theirs.attachment_id) is None
    assert store.object_count == 0


@pytest.mark.parametrize("status", ["error", "timeout", "cancelled", "refused"])
async def test_every_terminal_status_has_its_image_leases_validated(status):
    """R4: the parts are validated on every terminal observation, not only a success."""

    harness, store = _image_harness()
    foreign = store.put("run-2", b"\x00" * 64, content_type="image/png")

    _provider, observation = await _observe(harness, [_image_ref(foreign)], status=status)

    _assert_rejected(observation, ERROR_INVALID_RESULT)
    assert store.object_count == 0


async def test_a_non_success_observation_with_a_valid_image_is_adopted_as_it_stands():
    """R4: a provider's own ``error`` may carry an image the model will see."""

    harness, store = _image_harness()
    ref = store.put("run-1", b"\x00" * 64, content_type="image/png")

    _provider, observation = await _observe(harness, [_image_ref(ref)], status="error")

    assert observation.status == "error"
    assert observation.error["code"] == "upstream"
    assert len(observation.parts) == 1
    assert store.usage("run-1").objects == 1


async def test_an_observation_over_the_bound_is_too_large_and_releases_its_images():
    """AC47 head: 3 145 728 text bytes + a 3 145 728-byte image, each alone
    under 5 242 880, are 6 291 456 together: ``error observation_too_large``,
    0 objects for the run afterwards, nothing of the provider's adopted.
    """

    harness, store = _image_harness()
    ref = store.put("run-1", b"\x00" * (3 * MIB), content_type="image/jpeg")
    assert ref.size == 3_145_728

    provider, observation = await _observe(harness, [_text(3_145_728), _image_ref(ref)])

    _assert_rejected(observation, ERROR_OBSERVATION_TOO_LARGE)
    assert observation.error["code"] == "observation_too_large"
    assert "6291456" in observation.error["message"]
    assert len(provider.calls) == 1
    assert store.usage("run-1").objects == 0
    assert store.total_bytes == 0
    assert store.release("run-1").objects == 0


async def test_an_observation_exactly_at_the_bound_is_adopted_with_its_image():
    """AC47 middle: 3 145 728 + 2 097 152 = 5 242 880 is accepted, image kept."""

    harness, store = _image_harness()
    ref = store.put("run-1", b"\x00" * (2 * MIB), content_type="image/jpeg")
    assert ref.size == 2_097_152

    _provider, observation = await _observe(harness, [_text(3_145_728), _image_ref(ref)])

    assert observation.status == "success"
    assert len(observation.parts) == 2
    assert observation.parts[1]["attachment_id"] == ref.attachment_id
    assert store.usage("run-1").objects == 1


async def test_a_single_text_part_over_the_bound_is_observation_too_large():
    """R4: the per-part text bound is the same rule as the observation bound,
    so it carries the same code — one code for one situation."""

    harness, store = _image_harness()
    ref = store.put("run-1", b"\x00" * 64, content_type="image/png")

    _provider, observation = await _observe(
        harness, [_text(MAX_OBSERVATION_BYTES + 1), _image_ref(ref)]
    )

    _assert_rejected(observation, ERROR_OBSERVATION_TOO_LARGE)
    assert "parts[0].text" in observation.error["message"]
    assert store.object_count == 0


async def test_without_a_bound_only_the_leases_are_checked():
    """``max_observation_bytes=None`` sets no size bound (the brain's is P12)."""

    clock = ManualClock()
    store = _store(clock)
    harness = Harness(clock=clock, attachments=store)
    harness.declare(READ_SPEC)
    harness.provider = Provider("capture")
    harness.bind(READ_SPEC, harness.provider)
    harness.ready()
    harness.allow(READ_SPEC)
    ref = store.put("run-1", b"\x00" * (3 * MIB), content_type="image/jpeg")

    _provider, observation = await _observe(harness, [_text(3 * MIB), _image_ref(ref)])
    assert observation.status == "success"
    assert len(observation.parts) == 2

    foreign = store.put("run-2", b"\x00" * 64, content_type="image/png")
    _provider, rejected = await _observe(harness, [_image_ref(foreign)])
    _assert_rejected(rejected, ERROR_INVALID_RESULT)
    assert store.lookup(foreign.attachment_id) is None


async def test_an_image_ref_without_a_store_cannot_be_validated_and_is_invalid_result():
    """R4: no store, no lease to check — an image is never adopted on trust."""

    harness = Harness()
    harness.declare(READ_SPEC)
    harness.provider = Provider("capture")
    harness.bind(READ_SPEC, harness.provider)
    harness.ready()
    harness.allow(READ_SPEC)
    orphan = {
        "type": "image_ref",
        "attachment_id": "a" * 32,
        "content_type": "image/png",
        "size": 64,
        "width": 8,
        "height": 8,
        "captured_at": 0.0,
        "provider_id": "capture",
    }

    _provider, observation = await _observe(harness, [orphan])
    _assert_rejected(observation, ERROR_INVALID_RESULT)
    assert "no attachment store" in observation.error["message"]

    # Text-only parts need no store and are adopted as they stand.
    _provider, texty = await _observe(harness, [_text(5)])
    assert texty.status == "success"
    assert texty.parts == (_text(5),)


async def test_a_result_failing_its_schema_releases_the_images_it_carried():
    """R4/AC18: a rejected success adopts nothing — parts included."""

    harness, store = _image_harness()
    ref = store.put("run-1", b"\x00" * 64, content_type="image/png")

    def malformed(_invocation) -> ActionObservation:
        return ActionObservation(
            status="success",
            provenance={"transport": "fake"},
            result={"lines": "not-a-list"},
            parts=[_image_ref(ref)],
        )

    harness.provider.behaviour = malformed
    observation = await harness.executor.invoke(make_call(READ_SPEC))

    _assert_rejected(observation, ERROR_INVALID_RESULT)
    assert store.object_count == 0


def _writer_with_images() -> tuple[Harness, AttachmentStore]:
    """A ready, authorized writer whose observations may carry images."""

    clock = ManualClock()
    store = _store(clock)
    harness = Harness(
        clock=clock, attachments=store, max_observation_bytes=MAX_OBSERVATION_BYTES
    )
    harness.declare(WRITE_SPEC)
    harness.provider = Provider("sender")
    harness.bind(WRITE_SPEC, harness.provider)
    harness.ready()
    harness.allow(WRITE_SPEC)
    return harness, store


def _interrupted(status: str, parts, *, emit: bool):
    def behaviour(invocation) -> ActionObservation:
        if emit:
            invocation.mark_emitted()
        return ActionObservation(
            status=status,
            provenance={"transport": "fake"},
            error={"code": "timed_out", "message": "no ack", "retryable": True},
            parts=parts,
        )

    return behaviour


@pytest.mark.parametrize("status", ["timeout", "cancelled"])
async def test_rejected_parts_do_not_erase_the_uncertainty_of_an_emitted_write(status):
    """R4 × R5: discarding invalid parts must not lose an emitted write.

    The provider signalled an emission, then returned ``timeout`` (or
    ``cancelled``) carrying an image leased to another run. The parts are
    rejected and the foreign object released, but the terminal status stays
    ``external_unknown`` — as it would have with sound parts — never
    ``error``, which a caller would read as licence to send again.
    """

    harness, store = _writer_with_images()
    foreign = store.put("run-2", b"\x00" * 64, content_type="image/png")
    harness.provider.behaviour = _interrupted(status, [_image_ref(foreign)], emit=True)

    observation = await harness.executor.invoke(make_call(WRITE_SPEC))

    assert observation.status == "external_unknown"
    assert observation.error["code"] == ERROR_EXTERNAL_UNKNOWN
    assert observation.provenance["emission"] == EMISSION_EMITTED
    assert observation.parts == ()
    assert observation.provenance.get("transport") is None
    # The rejection is still on record, inside the message.
    assert ERROR_INVALID_RESULT in observation.error["message"]
    assert "run-2" in observation.error["message"]
    assert store.lookup(foreign.attachment_id) is None
    assert harness.counters.get(COUNTER_ACTION_TIMEOUTS) == 0
    completed = harness.bus.of_type(TRACE_ACTION_COMPLETED)
    assert [(p["status"], p["error_code"]) for p in completed] == [
        ("external_unknown", ERROR_EXTERNAL_UNKNOWN)
    ]


async def test_a_silent_write_interrupted_with_bad_parts_is_still_external_unknown():
    """R5: emission unknown on a write is uncertain whatever the parts say."""

    harness, store = _writer_with_images()
    foreign = store.put("run-2", b"\x00" * 64, content_type="image/png")
    harness.provider.behaviour = _interrupted("timeout", [_image_ref(foreign)], emit=False)

    observation = await harness.executor.invoke(make_call(WRITE_SPEC))

    assert observation.status == "external_unknown"
    assert observation.error["code"] == ERROR_EXTERNAL_UNKNOWN
    assert observation.parts == ()
    assert store.lookup(foreign.attachment_id) is None


async def test_a_read_interrupted_with_bad_parts_is_a_certain_error():
    """R5: nothing to duplicate on a read, so the rejection's own code stands."""

    harness, store = _image_harness()
    foreign = store.put("run-2", b"\x00" * 64, content_type="image/png")
    harness.provider.behaviour = _interrupted("timeout", [_image_ref(foreign)], emit=False)

    observation = await harness.executor.invoke(make_call(READ_SPEC))

    _assert_rejected(observation, ERROR_INVALID_RESULT)
    assert store.lookup(foreign.attachment_id) is None
    assert harness.counters.get(COUNTER_ACTION_TIMEOUTS) == 0


async def test_synthetic_observations_carry_no_parts():
    """R4: what the executor synthesises itself never carries parts."""

    harness, store = _image_harness()
    ref = store.put("run-1", b"\x00" * 64, content_type="image/png")

    # Not authorized at another principal, unknown action, bad arguments:
    # three synthetic paths, none reaches the provider.
    provider = harness.provider
    provider.behaviour = _observing([_image_ref(ref)])
    unauthorized = await harness.executor.invoke(make_call(READ_SPEC, principal="viewer:x"))
    unknown = await harness.executor.invoke(make_call(replace(READ_SPEC, name="chat.missing")))
    malformed = await harness.executor.invoke(make_call(READ_SPEC, arguments={"limit": 0}))

    assert unauthorized.status == "refused" and unauthorized.parts == ()
    assert unknown.error["code"] == ERROR_UNKNOWN_ACTION and unknown.parts == ()
    assert malformed.error["code"] == ERROR_INVALID_ARGUMENTS and malformed.parts == ()
    assert provider.calls == []

    # The executor-detected interruptions and the uncertain effect as well.
    for spec, behaviour, sleeper in (
        (READ_SPEC, hang, immediate_sleep),
        (WRITE_SPEC, write_success, never_sleep),
    ):
        _h, _p, observation = await _terminal(spec, behaviour, sleeper=sleeper)
        assert observation.parts == ()
    _h, _p, refused = await _terminal(WRITE_SPEC, write_success, grant=False)
    assert refused.status == "refused" and refused.parts == ()


@pytest.mark.parametrize("bound", [0, -1, True, 1.5, "5"])
def test_the_observation_bound_must_be_a_positive_integer_or_none(bound):
    harness = Harness()
    with pytest.raises(ContractError) as refused:
        ActionExecutor(harness.registry, harness.policy, max_observation_bytes=bound)
    assert refused.value.field == "ActionExecutor.max_observation_bytes"

    assert ActionExecutor(harness.registry, harness.policy, max_observation_bytes=None)
    assert ActionExecutor(harness.registry, harness.policy, max_observation_bytes=1)


def test_the_attachment_store_must_be_a_store_or_none():
    harness = Harness()
    with pytest.raises(ContractError) as refused:
        ActionExecutor(harness.registry, harness.policy, attachments=object())
    assert refused.value.field == "ActionExecutor.attachments"


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
