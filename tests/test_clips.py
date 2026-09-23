"""``stream.clip.create`` through the ``clips`` module (phase 3 R2; AC8–AC12).

Every call goes through the real executor under a real authorization policy
on a :class:`~conftest.ManualClock`: the module's sleeper is the clock's, so
the lookup cadence, the confirmation window, the call deadline and the drain
deadline all move only when a test advances the clock. The clip service is
the scripted :class:`~conftest.ScriptedClipService` published by the fixture
platform (``fake``), or the twitch module's own service over a scripted HTTP
session (AC12). No positive-duration sleep.

The Kick-named half of AC12 runs with the kick module (plan step P19).
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml

from conftest import (
    ManualClock,
    ScriptedClipService,
    events_of,
    helix_clip_created,
    helix_clip_listed,
    runtime_context,
    settle,
    trace_texts,
    wait_until,
)
from core.actions import AuthorizationRule
from core.contracts import ActionCall, ActionObservation, Destination
from core.lifecycle import PhaseCoordinator
from core.loader import ModuleLoadError, ModuleLoader
from core.runtime import RuntimeContext
from core.triggers import TriggerRegistry
from fixtures.modules import fakeplatform
from modules.clips import (
    CAUSE_CONFIRMATION_LOST,
    CLIP_ACTION,
    MANIFEST_PATH,
    REASON_PLATFORM_UNSUPPORTED,
    ClipsModule,
    ClipsModuleError,
    activate as activate_clips,
    validate_settings,
)
from modules.twitch import HELIX_CLIPS_URL, activate as activate_twitch


REPOSITORY = Path(__file__).resolve().parents[1]
FIXTURE_MODULES = REPOSITORY / "tests" / "fixtures" / "modules"
START = 1000.0
CHANNEL = "chan-a"
FAKE_CLIP = Destination("fake", CHANNEL, "clip")
FAKE_SETTINGS = {"channel_ids": [CHANNEL], "companion_name": "companion"}

TWITCH_SETTINGS = {
    "client_id": "configured-client",
    "client_secret": "never-show-client-secret",
    "access_token": "never-show-access-token",
    "broadcaster_id": "broadcaster-42",
    "bot_user_id": "bot-24",
    "companion_name": "Companion",
}
TWITCH_CREDENTIALS = ("never-show-client-secret", "never-show-access-token")


@pytest.fixture(autouse=True)
def clean_fakeplatform():
    fakeplatform.reset()
    yield
    fakeplatform.reset()


def grant_clips(runtime: RuntimeContext) -> None:
    runtime.actions._authorization.grant(
        AuthorizationRule(
            rule_id="grant-clip", action_name=CLIP_ACTION, granted_permissions=("stream.clip",)
        )
    )


class ClipHarness:
    """The fixture platform publishing a scripted clip service, the clips
    module on the same runtime, and the executor calls a test drives."""

    def __init__(
        self, runtime: RuntimeContext, handle: ClipsModule, service: Any, clock: ManualClock
    ) -> None:
        self.runtime = runtime
        self.handle = handle
        self.service = service
        self.clock = clock
        self._calls = 0

    def call(
        self, destination: Destination = FAKE_CLIP, deadline: float = 100_000.0
    ) -> ActionCall:
        self._calls += 1
        return ActionCall(
            action_name=CLIP_ACTION,
            action_version=1,
            arguments={},
            conversation_id="conversation-1",
            run_id="run-1",
            call_id=f"clip-call-{self._calls}",
            source_event_id="source-1",
            destination=destination,
            principal="brain",
            deadline=deadline,
            message_id="source-1",
        )

    async def create(self, **options: Any) -> ActionObservation:
        return await self.runtime.executor.invoke(self.call(**options))

    def start(self, **options: Any) -> "asyncio.Future[ActionObservation]":
        return asyncio.ensure_future(self.create(**options))

    async def drive(self, future: "asyncio.Future[Any]", *, step: float = 1.0, limit: int = 40) -> Any:
        """Advance the clock by *step* until *future* is done."""

        for _ in range(limit):
            await settle()
            if future.done():
                return future.result()
            self.clock.advance(step)
        await settle()
        assert future.done(), "the call did not end within the step budget"
        return future.result()

    def completed(self) -> list[dict[str, Any]]:
        return [event["payload"] for event in events_of(self.runtime.bus, "action.completed")]


async def clip_harness(
    service: Any = None,
    *,
    grant: bool = True,
    services: tuple[str, ...] = ("clip",),
    **settings: Any,
) -> ClipHarness:
    clock = ManualClock(START)
    if service is None:
        service = ScriptedClipService(clock=clock)
    runtime = runtime_context(clock=clock)
    await fakeplatform.activate(
        runtime.for_module("fakeplatform"),
        {**FAKE_SETTINGS, "services": list(services), "clip_service": service},
        {},
    )
    handle = await activate_clips(
        runtime.for_module("clips"), {"_sleeper": clock.sleep, **settings}, {}
    )
    await handle.prepare()
    if grant:
        grant_clips(runtime)
    return ClipHarness(runtime, handle, service, clock)


def assert_failure(observation: ActionObservation, status: str, code: str) -> dict[str, Any]:
    assert observation.status == status, observation
    assert observation.result is None
    assert observation.error is not None
    assert observation.error["code"] == code, observation.error
    return dict(observation.error)


# -- AC8: an authorized call is confirmed after exactly one create ---------- #


@pytest.mark.asyncio
async def test_an_authorized_call_is_confirmed_after_exactly_one_create() -> None:
    """AC8: the service confirming at the first lookup → ``success`` with a
    non-empty ``clip_id``, ``url`` and ``confirmed_at``, after 1 create."""

    h = await clip_harness()
    try:
        observation = await h.create()
        assert observation.status == "success", observation
        result = observation.result
        assert result["clip_id"] and result["url"] and result["confirmed_at"]
        assert result["confirmed_at"] == START
        assert h.service.creates == 1
        assert [request["op"] for request in h.service.requests] == ["create", "lookup"]
        assert h.service.requests[1]["clip_id"] == result["clip_id"]
        [completed] = h.completed()
        assert completed["status"] == "success" and completed["error_code"] is None
        assert completed["clip_id"] == result["clip_id"]
    finally:
        await h.handle.close()


@pytest.mark.asyncio
async def test_without_a_rule_the_call_is_refused_with_no_request() -> None:
    """AC8: default-deny — no rule, ``refused`` and 0 requests."""

    h = await clip_harness(grant=False)
    try:
        observation = await h.create()
        assert_failure(observation, "refused", "not_authorized")
        assert h.service.requests == []
    finally:
        await h.handle.close()


# -- AC9: the per-channel cooldown ------------------------------------------ #


@pytest.mark.asyncio
async def test_a_call_29_s_after_the_create_is_refused_and_one_at_30_s_sends() -> None:
    """AC9: with ``min_interval_seconds: 30`` a call 29 s after the first
    create request is ``refused cooldown`` with 0 requests; at 30 s it sends."""

    h = await clip_harness(min_interval_seconds=30)
    try:
        assert (await h.create()).status == "success"
        assert h.service.creates == 1
        sent = len(h.service.requests)

        h.clock.advance(29.0)
        early = await h.create()
        assert_failure(early, "refused", "cooldown")
        assert len(h.service.requests) == sent

        h.clock.advance(1.0)
        assert (await h.create()).status == "success"
        assert h.service.creates == 2
        assert h.service.requests[-2]["at"] == START + 30.0
    finally:
        await h.handle.close()


@pytest.mark.asyncio
async def test_the_cooldown_is_per_channel_and_defaults_to_30_s() -> None:
    """R2: another channel is not cooled down; the default interval is 30 s."""

    h = await clip_harness()
    try:
        assert (await h.create()).status == "success"
        other = Destination("fake", "chan-b", "clip")
        assert (await h.create(destination=other)).status == "success"
        assert h.service.creates == 2
        h.clock.advance(29.5)
        assert_failure(await h.create(), "refused", "cooldown")
        assert h.service.creates == 2
    finally:
        await h.handle.close()


# -- AC10: the failure taxonomy, at most one create per call ---------------- #


@pytest.mark.asyncio
async def test_a_lookup_empty_until_the_window_closes_is_clip_not_created() -> None:
    """AC10: lookups empty until 15 s after acceptance → ``error
    clip_not_created`` with 1 create and 0 second create; the first lookup
    is at once, then one per second, none after acceptance + 15 s."""

    service = ScriptedClipService(lookup_default="empty")
    h = await clip_harness(service)
    service._clock = h.clock
    try:
        call = h.start()
        observation = await h.drive(call)
        assert_failure(observation, "error", "clip_not_created")
        assert service.creates == 1
        lookups = [request["at"] for request in service.requests if request["op"] == "lookup"]
        assert lookups == [START + second for second in range(16)]
        assert h.clock.now == START + 15.0
        # Nothing more is sent once the call ended.
        h.clock.advance(5.0)
        await settle()
        assert service.lookups == 16 and service.creates == 1
    finally:
        await h.handle.close()


class SlowLookupClipService(ScriptedClipService):
    """Lookups answered empty; the one issued at *slow_at* answers only
    *delay* seconds later on the clock."""

    def __init__(self, *, slow_at: float, delay: float) -> None:
        super().__init__(lookup_default="empty")
        self._slow_at = slow_at
        self._delay = delay

    async def lookup(self, clip_id: str) -> str | None:
        issued_at = self._now()
        answer = await super().lookup(clip_id)
        if issued_at == self._slow_at:
            self._clock.advance(self._delay)
        return answer


@pytest.mark.asyncio
async def test_a_lookup_answered_past_the_window_starts_no_further_lookup() -> None:
    """Decision 8: a lookup issued at acceptance + 14 s and answered empty at
    + 16 s ends the call ``clip_not_created``; no lookup starts after
    acceptance + 15 s."""

    service = SlowLookupClipService(slow_at=START + 14.0, delay=2.0)
    h = await clip_harness(service)
    service._clock = h.clock
    try:
        observation = await h.drive(h.start())
        assert_failure(observation, "error", "clip_not_created")
        lookups = [request["at"] for request in service.requests if request["op"] == "lookup"]
        assert lookups == [START + second for second in range(15)]
        assert service.creates == 1
        [completed] = h.completed()
        assert "clip_id" not in completed
    finally:
        await h.handle.close()


@pytest.mark.asyncio
async def test_a_clip_found_mid_window_is_a_success_confirmed_then() -> None:
    """R2, decision 8: a clip listed at the fourth lookup is confirmed at
    acceptance + 3 s."""

    service = ScriptedClipService(lookup=("empty", "empty", "empty", "found https://c/x"))
    h = await clip_harness(service)
    service._clock = h.clock
    try:
        observation = await h.drive(h.start())
        assert observation.status == "success", observation
        assert observation.result["url"] == "https://c/x"
        assert observation.result["confirmed_at"] == START + 3.0
        assert (service.creates, service.lookups) == (1, 4)
    finally:
        await h.handle.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scripted", "status", "code"),
    [
        ("offline", "error", "channel_offline"),
        ("auth", "error", "platform_rejected"),
        ("rate", "error", "rate_limited"),
        ("lost", "external_unknown", "external_effect_unknown"),
        ("server_error", "external_unknown", "external_effect_unknown"),
        (ConnectionResetError("reset after sending"), "external_unknown", "external_effect_unknown"),
    ],
    ids=["offline", "auth", "rate", "lost", "server_error", "raised"],
)
async def test_each_create_answer_maps_to_its_outcome_with_one_create(
    scripted: Any, status: str, code: str
) -> None:
    """AC10: offline → ``channel_offline``, auth → ``platform_rejected``,
    rate → ``rate_limited`` (no retry), a lost answer or a server error →
    ``external_unknown`` cause ``confirmation_lost``; 1 create, no lookup."""

    h = await clip_harness(ScriptedClipService(create=(scripted,)))
    try:
        observation = await h.create()
        error = assert_failure(observation, status, code)
        if status == "external_unknown":
            assert error["cause"] == CAUSE_CONFIRMATION_LOST
        assert h.service.creates == 1
        assert h.service.lookups == 0
        [completed] = h.completed()
        assert (completed["status"], completed["error_code"]) == (status, code)
    finally:
        await h.handle.close()


@pytest.mark.asyncio
async def test_a_lost_last_lookup_is_external_unknown_not_clip_not_created() -> None:
    """R2: the window closing on a lookup whose answer is lost cannot
    conclude that no clip exists: ``external_unknown``, 1 create."""

    service = ScriptedClipService(
        lookup=(*(["empty"] * 15), ConnectionResetError("lookup lost"))
    )
    h = await clip_harness(service)
    service._clock = h.clock
    try:
        observation = await h.drive(h.start())
        error = assert_failure(observation, "external_unknown", "external_effect_unknown")
        assert error["cause"] == CAUSE_CONFIRMATION_LOST
        assert (service.creates, service.lookups) == (1, 16)
    finally:
        await h.handle.close()


@pytest.mark.asyncio
async def test_the_call_deadline_at_12_s_while_the_window_is_open_is_external_unknown() -> None:
    """AC10: a call deadline 12 s after acceptance, the window still open →
    ``external_unknown``; no lookup at or after the deadline, 1 create."""

    service = ScriptedClipService(lookup_default="empty")
    h = await clip_harness(service)
    service._clock = h.clock
    try:
        observation = await h.drive(h.start(deadline=START + 12.0))
        assert_failure(observation, "external_unknown", "external_effect_unknown")
        assert h.clock.now == START + 12.0
        assert service.creates == 1
        lookups = [request["at"] for request in service.requests if request["op"] == "lookup"]
        assert lookups == [START + second for second in range(12)]
        # The channel's lock was released at the deadline: a later call runs.
        h.clock.advance(30.0)
        service.script_lookup("found")
        assert (await h.create()).status == "success"
    finally:
        await h.handle.close()


@pytest.mark.asyncio
async def test_a_deadline_before_any_request_is_timeout_with_no_request() -> None:
    """AC10: a call whose deadline has passed ends ``timeout`` with 0 requests."""

    h = await clip_harness()
    try:
        observation = await h.create(deadline=START)
        assert_failure(observation, "timeout", "timed_out")
        assert h.service.requests == []
    finally:
        await h.handle.close()


@pytest.mark.asyncio
async def test_a_deadline_reached_while_waiting_for_the_channel_is_timeout_with_no_request() -> None:
    """AC10: a call whose deadline passes while it waits behind the create in
    flight ends ``timeout`` and sends nothing; its place is given back."""

    service = ScriptedClipService(create=("hold",))
    h = await clip_harness(service, max_waiters=1)
    service._clock = h.clock
    try:
        first = h.start()
        await wait_until(lambda: service.in_flight == 1)
        waiting = h.start(deadline=START + 3.0)
        await settle()
        assert_failure(await h.create(), "refused", "resource_busy")
        observation = await h.drive(waiting)
        assert_failure(observation, "timeout", "timed_out")
        assert service.creates == 1
        # The waiter's place was released: one more may wait again.
        again = h.start()
        await settle()
        assert not again.done()
        service.release_held("accepted clip-held")
        assert (await h.drive(first)).status == "success"
        assert_failure(await h.drive(again), "refused", "cooldown")
        assert service.creates == 1 and not service.overlap
    finally:
        await h.handle.close()


# -- AC11: serialization and admission on one channel ----------------------- #


@pytest.mark.asyncio
async def test_five_concurrent_calls_send_one_create_and_a_sixth_is_busy() -> None:
    """AC11: ``max_waiters: 4``, ``min_interval_seconds: 5``, a clock that
    does not advance: 1 call sends, the 4 waiting each end ``refused
    cooldown`` with 0 requests once resumed, a sixth concurrent call is
    ``refused resource_busy``; exactly 1 create, never two requests at once."""

    service = ScriptedClipService(create=("hold",))
    h = await clip_harness(service, max_waiters=4, min_interval_seconds=5)
    service._clock = h.clock
    try:
        first = h.start()
        await wait_until(lambda: service.in_flight == 1)
        waiters = [h.start() for _ in range(4)]
        await settle()
        assert not any(call.done() for call in waiters)

        sixth = await h.create()
        assert_failure(sixth, "refused", "resource_busy")

        service.release_held("accepted clip-1")
        assert (await asyncio.wait_for(first, 5)).status == "success"
        for call in waiters:
            assert_failure(await asyncio.wait_for(call, 5), "refused", "cooldown")
        assert service.creates == 1
        assert [request["op"] for request in service.requests] == ["create", "lookup"]
        assert not service.overlap
        assert h.clock.now == START
    finally:
        await h.handle.close()


# -- Drain and shutdown ------------------------------------------------------ #


@pytest.mark.asyncio
async def test_drain_cancels_a_waiting_call_and_bounds_a_confirmation_in_progress() -> None:
    """R2, phase lifecycle: the drain ends a call still waiting ``cancelled``
    with 0 requests of its own; a confirmation in progress is given the
    drain deadline and ends ``external_unknown`` past it, with no lookup
    after it."""

    service = ScriptedClipService(lookup_default="empty")
    h = await clip_harness(service)
    service._clock = h.clock
    try:
        confirming = h.start()
        await wait_until(lambda: service.lookups == 1)
        waiting = h.start()
        await settle()

        drain = asyncio.ensure_future(h.handle.drain(4.0))
        await settle()
        assert_failure(await asyncio.wait_for(waiting, 5), "cancelled", "cancelled")
        assert not confirming.done()

        await h.drive(drain)
        observation = await asyncio.wait_for(confirming, 5)
        error = assert_failure(observation, "external_unknown", "external_effect_unknown")
        assert error["cause"] == CAUSE_CONFIRMATION_LOST
        assert h.clock.now == START + 4.0
        assert service.creates == 1
        assert service.requests[-1]["at"] <= START + 4.0
        # Once draining, a new call is refused before any request.
        assert_failure(await h.create(), "cancelled", "cancelled")
        assert service.creates == 1
    finally:
        await h.handle.close()


def fixture_modules(root: Path) -> Path:
    """A modules directory with copies of ``clips`` and the fixture platform."""

    ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
    shutil.copytree(REPOSITORY / "modules" / "clips", root / "clips", ignore=ignore)
    shutil.copytree(FIXTURE_MODULES / "fakeplatform", root / "fakeplatform", ignore=ignore)
    return root


async def loaded(
    tmp_path: Path,
    clock: ManualClock,
    *,
    fake: dict[str, Any],
    clips: dict[str, Any],
) -> tuple[RuntimeContext, list[Any]]:
    runtime = runtime_context(
        clock=clock, trigger_registry=TriggerRegistry(companion_name="companion")
    )
    loader = ModuleLoader(runtime.bus, fixture_modules(tmp_path / "modules"), context=runtime, environ={})
    modules = {
        "fakeplatform": {**FAKE_SETTINGS, **fake},
        "clips": {"_sleeper": clock.sleep, "limits": {}, **clips},
    }
    activations = await loader.activate_enabled(
        {"enabled_modules": ["fakeplatform", "clips"], "modules": modules}
    )
    return runtime, activations


def coordinator_for(runtime: RuntimeContext, activations: list[Any], clock: ManualClock) -> PhaseCoordinator:
    return PhaseCoordinator(
        activations,
        tasks=runtime.tasks,
        clock=clock,
        sleeper=clock.sleep,
        shutdown_deadline_seconds=10.0,
        hook_timeout_seconds=4.0,
    )


@pytest.mark.asyncio
async def test_a_shutdown_during_a_confirmation_ends_the_call_within_the_deadline(
    tmp_path: Path,
) -> None:
    """Phase lifecycle: through the loader and the coordinator, a shutdown
    requested while a clip is being confirmed ends the call
    ``external_unknown`` at the drain deadline, within the global shutdown
    deadline; the action leaves the ready view. The drain uses its whole
    allowance, which the coordinator reports as a drain failure: the
    outcome is asserted, not the status."""

    clock = ManualClock(START)
    service = ScriptedClipService(clock=clock, lookup_default="empty")
    runtime, activations = await loaded(
        tmp_path, clock, fake={"services": ["clip"], "clip_service": service}, clips={}
    )
    coordinator = coordinator_for(runtime, activations, clock)
    assert (await coordinator.start()).status == 0
    grant_clips(runtime)
    harness = ClipHarness(runtime, activations[1].handle, service, clock)
    call = harness.start()
    await wait_until(lambda: service.lookups == 1)

    stop = asyncio.ensure_future(coordinator.stop())
    requested_at = clock.now
    observation = await harness.drive(call)
    await harness.drive(stop)

    error = assert_failure(observation, "external_unknown", "external_effect_unknown")
    assert error["cause"] == CAUSE_CONFIRMATION_LOST
    assert clock.now <= requested_at + 10.0
    assert service.creates == 1
    assert CLIP_ACTION not in runtime.actions.registered_ready()


# -- AC12: readiness per platform, the `required` policy, the trace --------- #


class HelixClipSession:
    """The twitch module's HTTP session, scripted for the clip endpoint."""

    def __init__(self, *answers: Any) -> None:
        self.answers = list(answers)
        self.requests: list[dict[str, Any]] = []

    async def _answer(self, method: str, url: str, kwargs: dict[str, Any]) -> Any:
        assert url == HELIX_CLIPS_URL, url
        self.requests.append({"method": method, "url": url, **kwargs})
        return self.answers.pop(0)

    async def get(self, url: str, **kwargs: Any) -> Any:
        return await self._answer("GET", url, kwargs)

    async def post(self, url: str, **kwargs: Any) -> Any:
        return await self._answer("POST", url, kwargs)

    async def close(self) -> None:
        return None


async def _no_delay(_: float) -> None:
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_twitch_binds_its_clips_and_a_platform_without_a_service_is_unsupported() -> None:
    """AC12 (without Kick): twitch publishes a clip service and the fixture
    platform publishes only ``poll``: ``stream.clip.create`` is ready for
    ``twitch/*/clip``, ``fake`` is unbound with reason
    ``platform_unsupported``; a successful call's ``action.completed`` trace
    carries its status and 0 credential values, and the observation carries
    the ``clip_id``."""

    clock = ManualClock(START)
    runtime = runtime_context(clock=clock)
    session = HelixClipSession(
        helix_clip_created("AwkwardClip", "https://clips.twitch.example/AwkwardClip/edit"),
        helix_clip_listed("AwkwardClip", "https://clips.twitch.example/AwkwardClip"),
    )
    diagnostics: list[str] = []
    twitch = await activate_twitch(
        runtime.for_module("twitch"),
        {
            **TWITCH_SETTINGS,
            "_session_factory": lambda: session,
            "_retry_delay": _no_delay,
            "diagnostic_reporter": diagnostics.append,
        },
        {},
    )
    await fakeplatform.activate(
        runtime.for_module("fakeplatform"), {**FAKE_SETTINGS, "services": ["poll"]}, {}
    )
    handle = await activate_clips(runtime.for_module("clips"), {"_sleeper": clock.sleep}, {})
    try:
        await handle.prepare()
        assert handle.bound_platforms == ("twitch",)
        assert handle.unbound == {"fake": REASON_PLATFORM_UNSUPPORTED}
        [binding] = runtime.actions.bindings(CLIP_ACTION)
        assert binding.destination == Destination("twitch", "*", "clip")
        assert CLIP_ACTION in runtime.actions.registered_ready()
        [degraded] = [event["payload"] for event in events_of(runtime.bus, "module.degraded")]
        assert degraded["reason"] == REASON_PLATFORM_UNSUPPORTED
        assert degraded["capabilities"] == [CLIP_ACTION]
        assert degraded["platforms"] == ["fake"]

        grant_clips(runtime)
        h = ClipHarness(runtime, handle, None, clock)
        observation = await h.create(destination=Destination("twitch", "broadcaster-42", "clip"))
        assert observation.status == "success", observation
        assert observation.result["clip_id"] == "AwkwardClip"
        assert observation.result["url"] == "https://clips.twitch.example/AwkwardClip"
        assert [(r["method"], r["params"]) for r in session.requests] == [
            ("POST", {"broadcaster_id": "broadcaster-42"}),
            ("GET", {"id": "AwkwardClip"}),
        ]
        completed = [
            payload for payload in h.completed() if payload["destination"].startswith("twitch/")
        ]
        assert [payload["status"] for payload in completed] == ["success"]
        assert completed[0]["clip_id"] == "AwkwardClip"

        # The unsupported platform: no provider, 0 requests anywhere.
        refused = await h.create(destination=FAKE_CLIP)
        assert refused.status == "error" and refused.error["code"] == "no_provider"
        assert len(session.requests) == 2

        texts = trace_texts(runtime.bus) + diagnostics
        for secret in TWITCH_CREDENTIALS:
            assert sum(secret in text for text in texts) == 0, secret
    finally:
        await handle.close()
        await twitch.close()


@pytest.mark.asyncio
async def test_required_with_no_clip_service_fails_startup_naming_clips_and_the_reason(
    tmp_path: Path,
) -> None:
    """AC12: the fixture platform with ``services: [poll]`` as the only
    platform and ``required: true``: startup fails with a diagnostic naming
    ``clips`` and ``platform_unsupported``."""

    clock = ManualClock(START)
    runtime, activations = await loaded(
        tmp_path, clock, fake={"services": ["poll"]}, clips={"required": True}
    )
    report = await coordinator_for(runtime, activations, clock).start()
    assert report.status != 0
    # The coordinator's failure names the module (never an exception text);
    # the module's own health trace carries the reason.
    assert any("'clips'" in failure for failure in report.failures), report.failures
    reasons = [
        event["payload"]
        for event in events_of(runtime.bus, "module.degraded")
        if event["payload"].get("module") == "clips"
    ]
    assert [payload["reason"] for payload in reasons][0] == REASON_PLATFORM_UNSUPPORTED
    assert reasons[0]["platforms"] == ["fake"]
    assert CLIP_ACTION not in runtime.actions.registered_ready()


@pytest.mark.asyncio
async def test_required_unset_leaves_the_action_unbound_and_startup_completes(
    tmp_path: Path,
) -> None:
    """AC12: ``required`` unset (default false): startup completes with
    ``stream.clip.create`` declared, unbound on ``fake`` with
    ``platform_unsupported``; a call is refused with 0 requests."""

    clock = ManualClock(START)
    runtime, activations = await loaded(tmp_path, clock, fake={"services": ["poll"]}, clips={})
    coordinator = coordinator_for(runtime, activations, clock)
    report = await coordinator.start()
    try:
        assert report.status == 0, report
        handle = activations[1].handle
        assert handle.bound_platforms == ()
        assert handle.unbound == {"fake": REASON_PLATFORM_UNSUPPORTED}
        assert CLIP_ACTION in runtime.actions.discovered()
        assert CLIP_ACTION not in runtime.actions.registered_ready()
        assert runtime.actions.bindings(CLIP_ACTION) == ()
        grant_clips(runtime)
        observation = await ClipHarness(runtime, handle, None, clock).create()
        assert observation.status == "refused"
        assert observation.error["code"] == "provider_not_ready"
    finally:
        await coordinator.stop()


@pytest.mark.asyncio
async def test_a_required_value_that_is_not_a_boolean_is_rejected(tmp_path: Path) -> None:
    """AC12: ``required: "yes"`` is rejected by the validator — through the
    loader too — naming the field, never read as true."""

    assert validate_settings({"required": "yes"}) == [
        "module 'clips': field 'required': must be a boolean"
    ]
    with pytest.raises(ModuleLoadError) as refused:
        await loaded(tmp_path, ManualClock(START), fake={}, clips={"required": "yes"})
    assert "required" in str(refused.value)


@pytest.mark.asyncio
async def test_prepare_under_required_raises_before_anything_is_bound() -> None:
    """AC12: the ``required`` failure is raised by ``prepare`` itself."""

    clock = ManualClock(START)
    runtime = runtime_context(clock=clock)
    await fakeplatform.activate(
        runtime.for_module("fakeplatform"), {**FAKE_SETTINGS, "services": ["poll"]}, {}
    )
    handle = await activate_clips(
        runtime.for_module("clips"), {"_sleeper": clock.sleep, "required": True}, {}
    )
    with pytest.raises(ClipsModuleError) as failed:
        await handle.prepare()
    assert "clips" in str(failed.value) and REASON_PLATFORM_UNSUPPORTED in str(failed.value)
    assert runtime.actions.bindings(CLIP_ACTION) == ()
    await handle.close()


# -- Settings and manifest --------------------------------------------------- #


@pytest.mark.parametrize(
    ("settings", "field"),
    [
        ({"min_interval_seconds": 4}, "min_interval_seconds"),
        ({"min_interval_seconds": 3601}, "min_interval_seconds"),
        ({"min_interval_seconds": True}, "min_interval_seconds"),
        ({"min_interval_seconds": 30.5}, "min_interval_seconds"),
        ({"max_waiters": 0}, "max_waiters"),
        ({"max_waiters": 65}, "max_waiters"),
        ({"required": 1}, "required"),
        ({"required": "yes"}, "required"),
        ({"unknown": 1}, "unknown"),
        ({"_sleeper": 3}, "_sleeper"),
    ],
)
def test_validate_settings_names_each_refused_field(settings: dict[str, Any], field: str) -> None:
    diagnostics = validate_settings(settings)
    assert len(diagnostics) == 1
    assert f"field {field!r}" in diagnostics[0]


def test_validate_settings_accepts_the_bounds_and_the_defaults() -> None:
    assert validate_settings({}) == []
    assert validate_settings({"limits": {"anything": 1}}) == []
    for interval in (5, 3600):
        for waiters in (1, 64):
            for required in (True, False):
                assert validate_settings(
                    {
                        "min_interval_seconds": interval,
                        "max_waiters": waiters,
                        "required": required,
                    }
                ) == []
    assert validate_settings("not a mapping") == [
        "module 'clips': field 'settings': must be a mapping"
    ]


def test_the_manifest_declares_the_clip_action_and_no_grant() -> None:
    """R2: version 1, write, permission ``stream.clip``, destinations
    ``*/*/clip``, effect-only delivery, 20 s, idempotency none, no arguments,
    not model-proposable; the three settings; no credential and no grant."""

    manifest = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert manifest["name"] == "clips"
    assert manifest["manifest_version"] == 2
    assert set(manifest["settings_schema"]["properties"]) == {
        "min_interval_seconds",
        "max_waiters",
        "required",
        "limits",
    }
    assert "credentials" not in manifest
    assert "grants" not in manifest and "actions_grants" not in manifest
    [action] = manifest["actions"]
    assert action["name"] == CLIP_ACTION
    assert action["version"] == 1
    assert action["nature"] == "write"
    assert action["required_permissions"] == ["stream.clip"]
    assert action["supported_destinations"] == [
        {"platform": "*", "channel_id": "*", "scope": "clip"}
    ]
    assert action["delivery"] == {"text_argument": "none"}
    assert action["timeout_seconds"] == 20
    assert action["idempotency"] == "none"
    assert action["argument_schema"]["properties"] == {}
    assert action["argument_schema"]["additionalProperties"] is False
    assert "model_proposable" not in action
