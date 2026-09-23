"""``moderation.request`` through the ``moderation`` module (phase 3 R5; AC24,
AC25, AC26, AC27 without Kick, AC28).

Every call goes through the real executor under a real authorization policy
on a :class:`~conftest.ManualClock`. The moderation service is a
:class:`~conftest.ScriptedModerationService` published by the fixture
platform (``fake``) through its ``moderation_service`` seam, or published
under ``(moderation, twitch)`` beside twitch-shaped chat events, or the twitch
module's own service over a scripted HTTP session. The target messages reach
the module the way a platform delivers them: fed to the chat context, then
published as ``channel.chat.message``. No positive-duration sleep.

Every case also checks the no-silent-outcome rule of R5 (AC28): a shared
fixture asserts, for every harness a case built, that the number of
``moderation.decision`` facts equals the requests, approvals, rejections and
expiries the case drove.

The Kick-named case of AC27 (``delete_message`` on kick →
``platform_unsupported``) runs with the kick module's own service (plan step
P19), which offers ``timeout`` only.
"""

from __future__ import annotations

import asyncio
import dataclasses
import itertools
import json
import re
from collections import deque
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from conftest import (
    KICK_TEST_KEY,
    FakeResponse,
    ManualClock,
    ScriptedModel,
    ScriptedModerationService,
    events_of,
    final,
    helix_message_deleted,
    runtime_context,
    settle,
    tool_call,
    trace_texts,
    wait_until,
)
from core.actions import ActionExecutor, AuthorizationPolicy, AuthorizationRule
from core.bus import EventBus
from core.context import ChatContext, ChatEntry
from core.contracts import TRACE_BRAIN_RUN_COMPLETED, ActionCall, ActionObservation, Destination
from core.loader import ModuleLoader
from core.runtime import RuntimeContext
from core.triggers import TriggerRegistry
from fixtures.modules import fakeplatform
from modules.brain import BRAIN_ERROR_RUN_LIMIT, PRINCIPAL
from modules.moderation import (
    FACT_MODERATION_DECISION,
    MANIFEST_PATH,
    MODERATION_ACTION,
    STRICT_RULES,
    ModerationModule,
    ModerationModuleError,
    activate as activate_moderation,
    validate_settings,
)
from modules.kick import MODERATION_BANS_URL as KICK_BANS_URL, activate as activate_kick
from modules.twitch import HELIX_MODERATION_CHAT_URL, activate as activate_twitch
from test_brain import VALID_SETTINGS, copy_settings


REPOSITORY = Path(__file__).resolve().parents[1]
SHIPPED_MODULES = REPOSITORY / "modules"
FIXTURE_MODULES = REPOSITORY / "tests" / "fixtures" / "modules"
MODULE_SOURCE = SHIPPED_MODULES / "moderation" / "__init__.py"
START = 1000.0
CHANNEL = "chan-a"
OTHER_CHANNEL = "chan-b"
COMPANION = "companion"
COMPANION_ID = "Companion-Bot"
ROLES_PROVENANCE = "fake.badges"
FAKE_SETTINGS = {"channel_ids": [CHANNEL, OTHER_CHANNEL], "companion_name": COMPANION}
TWITCH_CHANNEL = "broadcaster-42"
TWITCH_SETTINGS = {
    "client_id": "configured-client",
    "client_secret": "never-show-client-secret",
    "access_token": "never-show-access-token",
    "broadcaster_id": TWITCH_CHANNEL,
    "bot_user_id": "bot-24",
    "companion_name": "Companion",
}
TWITCH_CREDENTIALS = ("never-show-client-secret", "never-show-access-token")
ACT_BOTH = {"mode": "act", "act": {"operations": ["delete_message", "timeout"]}}


@pytest.fixture(autouse=True)
def clean_fakeplatform():
    fakeplatform.reset()
    yield
    fakeplatform.reset()


#: Every harness the running case built, for :func:`no_silent_outcome`.
_HARNESSES: list["ModerationHarness"] = []


@pytest.fixture(autouse=True)
def no_silent_outcome():
    """AC28: over every AC24–AC28 case, the ``moderation.decision`` facts
    number the requests, approvals, rejections and expiries — the shared
    counter each harness keeps, checked once the case is over."""

    _HARNESSES.clear()
    yield
    harnesses = list(_HARNESSES)
    _HARNESSES.clear()
    for harness in harnesses:
        harness.assert_one_fact_per_request()


class CompanionModerationService(ScriptedModerationService):
    """A scripted service stating the companion's own author identity."""

    companion_id = COMPANION_ID


class BlockingModerationService(ScriptedModerationService):
    """Records each request, then holds its answer until ``release`` is set."""

    def __init__(self, **options: Any) -> None:
        super().__init__(**options)
        self.release = asyncio.Event()

    async def apply(self, operation: str, **arguments: Any) -> Any:
        answer = await super().apply(operation, **arguments)
        await self.release.wait()
        return answer


class StubbornModerationService(ScriptedModerationService):
    """Records each request, then holds its answer through any cancellation
    until ``release`` is set."""

    def __init__(self, **options: Any) -> None:
        super().__init__(**options)
        self.release = asyncio.Event()

    async def apply(self, operation: str, **arguments: Any) -> Any:
        answer = await super().apply(operation, **arguments)
        while not self.release.is_set():
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                continue
        return answer


def moderation_rule() -> AuthorizationRule:
    return AuthorizationRule(
        rule_id="grant-moderation",
        action_name=MODERATION_ACTION,
        granted_permissions=("moderation.request",),
    )


class ModerationHarness:
    """A platform publishing a scripted moderation service, the moderation
    module on the same runtime, and the executor calls a test drives.

    ``platform`` ``fake`` is the fixture platform module (its ``inject`` feeds
    the chat context and publishes); ``twitch`` publishes the service under
    ``(moderation, twitch)`` and feeds and publishes twitch-shaped events."""

    def __init__(
        self,
        runtime: RuntimeContext,
        handle: ModerationModule,
        service: Any,
        clock: ManualClock,
        platform: str,
        edge: Any,
    ) -> None:
        self.runtime = runtime
        self.handle = handle
        self.service = service
        self.clock = clock
        self.platform = platform
        self.edge = edge
        self.calls = 0
        # The decisions outside a call a case drove, each owing one fact.
        self.approvals = 0
        self.rejections = 0
        self.expiries = 0
        self._commands = 0

    async def say(
        self,
        message_id: str,
        author: str,
        *,
        roles: list[str] | None = None,
        provenance: str | None = ROLES_PROVENANCE,
        channel: str = CHANNEL,
        text: str = "buy followers at spam.example",
    ) -> None:
        """One chat message of *author*, delivered as the platform delivers it."""

        author_entry: dict[str, Any] = {"id": author, "display_name": author.title()}
        if roles is not None:
            author_entry["roles"] = list(roles)
            if provenance is not None:
                author_entry["roles_provenance"] = provenance
        if self.platform == "fake":
            published = await self.edge.inject(
                {"channel_id": channel, "author": author_entry, "message_id": message_id, "text": text}
            )
            assert published is not None
            return
        self.runtime.chat.append(
            self.platform, channel, ChatEntry(author_id=author, message_id=message_id, text=text)
        )
        await self.runtime.bus.publish(
            "channel.chat.message",
            {
                "platform": self.platform,
                "channel_id": channel,
                "author": author_entry,
                "message_id": message_id,
                "text": text,
            },
            {"source": self.platform, "schema_version": 2},
        )

    def call(
        self,
        message_id: str,
        *,
        operation: str = "delete_message",
        reason: str = "spam link",
        duration: int | None = None,
        channel: str = CHANNEL,
        platform: str | None = None,
        deadline: float = 100_000.0,
    ) -> ActionCall:
        self.calls += 1
        arguments: dict[str, Any] = {
            "operation": operation,
            "message_id": message_id,
            "reason": reason,
        }
        if duration is not None:
            arguments["duration_seconds"] = duration
        return ActionCall(
            action_name=MODERATION_ACTION,
            action_version=1,
            arguments=arguments,
            conversation_id="conversation-1",
            run_id=f"run-{self.calls}",
            call_id=f"moderation-call-{self.calls}",
            source_event_id="source-1",
            destination=Destination(platform or self.platform, channel, "moderation"),
            principal="brain",
            deadline=deadline,
            message_id="source-1",
        )

    async def request(self, message_id: str, **options: Any) -> ActionObservation:
        observation = await self.runtime.executor.invoke(self.call(message_id, **options))
        await settle()
        return observation

    def start(self, message_id: str, **options: Any) -> "asyncio.Future[ActionObservation]":
        return asyncio.ensure_future(self.runtime.executor.invoke(self.call(message_id, **options)))

    async def command(
        self,
        text: str,
        author: str,
        *,
        roles: list[str] | None = None,
        channel: str = CHANNEL,
        counts: str | None = None,
        provenance: str | None = ROLES_PROVENANCE,
    ) -> None:
        """One chat command; *counts* names the decision it owes a fact for
        (``approval`` or ``rejection``), ``None`` when it must do nothing."""

        self._commands += 1
        if counts == "approval":
            self.approvals += 1
        elif counts == "rejection":
            self.rejections += 1
        else:
            assert counts is None, counts
        await self.say(
            f"command-{self._commands}",
            author,
            roles=roles,
            provenance=provenance,
            channel=channel,
            text=text,
        )
        await settle()

    def facts(self) -> list[dict[str, Any]]:
        return [event["payload"] for event in events_of(self.runtime.bus, FACT_MODERATION_DECISION)]

    def assert_one_fact_per_request(self) -> None:
        """R5, AC28: no silent outcome — one fact per request that reached the
        module, per approval, per rejection and per expiry."""

        assert len(self.facts()) == self.calls + self.approvals + self.rejections + self.expiries

    async def close(self) -> None:
        await self.handle.close()


async def moderation_harness(
    service: Any = None,
    *,
    platform: str = "fake",
    grant: bool = True,
    chat_max_messages: int = 16,
    executor_on_clock: bool = False,
    **settings: Any,
) -> ModerationHarness:
    clock = ManualClock(START)
    if service is None:
        service = CompanionModerationService(clock=clock)
    chat = ChatContext(
        max_messages=chat_max_messages, max_bytes=65536, max_age_seconds=86400.0, clock=clock
    )
    runtime = runtime_context(clock=clock, chat=chat)
    if executor_on_clock:
        # The same executor, its timeout race on the manual clock.
        runtime = dataclasses.replace(
            runtime,
            executor=ActionExecutor(
                runtime.actions,
                runtime.actions._authorization,
                supervision=runtime.supervision,
                clock=clock,
                sleeper=clock.sleep,
            ),
        )
    edge: Any = None
    if platform == "fake":
        edge = await fakeplatform.activate(
            runtime.for_module("fakeplatform"),
            {**FAKE_SETTINGS, "services": ["moderation"], "moderation_service": service},
            {},
        )
        await edge.prepare()
    elif service is not False:
        runtime.for_module(platform).services.publish("moderation", platform, service)
    ids = (f"p-{number}" for number in itertools.count(1))
    handle = await activate_moderation(
        runtime.for_module("moderation"),
        {"_sleeper": clock.sleep, "_id_source": lambda: next(ids), **settings},
        {},
    )
    await handle.prepare()
    if grant:
        runtime.actions._authorization.grant(moderation_rule())
    harness = ModerationHarness(runtime, handle, service, clock, platform, edge)
    _HARNESSES.append(harness)
    return harness


def assert_refused(observation: ActionObservation, code: str) -> None:
    assert observation.status == "refused", observation
    assert observation.result is None
    assert observation.error is not None and observation.error["code"] == code, observation.error


# --------------------------------------------------------------------------- #
# The manifest and the settings
# --------------------------------------------------------------------------- #


def test_manifest_declares_the_one_model_proposable_write() -> None:
    """R5: version 1, write, ``model_proposable``, permission
    ``moderation.request``, destinations ``*/*/moderation``, no delivery,
    idempotency ``none``; the arguments; the module consumes chat messages."""

    manifest = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert manifest["consumes"] == ["channel.chat.message"]
    assert manifest["produces"] == []
    assert "credentials" not in manifest and "triggers" not in manifest
    (action,) = manifest["actions"]
    assert action["name"] == MODERATION_ACTION and action["version"] == 1
    assert action["nature"] == "write" and action["model_proposable"] is True
    assert action["required_permissions"] == ["moderation.request"]
    assert action["supported_destinations"] == [
        {"platform": "*", "channel_id": "*", "scope": "moderation"}
    ]
    assert "delivery" not in action and action["idempotency"] == "none"
    arguments = action["argument_schema"]
    assert set(arguments["properties"]) == {
        "operation",
        "message_id",
        "reason",
        "duration_seconds",
    }
    assert arguments["properties"]["operation"]["enum"] == ["delete_message", "timeout"]
    assert arguments["properties"]["duration_seconds"]["type"] == "integer"
    assert arguments["additionalProperties"] is False


@pytest.mark.asyncio
async def test_the_loader_discovers_the_spec_the_module_binds() -> None:
    """AC25 (discovery): the loader declares the spec with
    ``model_proposable`` and the module binds the same spec at prepare."""

    h = await moderation_harness()
    try:
        spec = h.runtime.actions.registered_ready()[MODERATION_ACTION]
        assert spec.model_proposable is True and spec.nature == "write"
        assert spec.delivery is None
        (binding,) = h.runtime.actions.bindings(MODERATION_ACTION)
        assert binding.destination == Destination("*", "*", "moderation")
        assert h.handle.bound_platforms == ("fake",)
    finally:
        await h.close()


def test_the_validator_accepts_the_defaults_and_refuses_each_bad_field() -> None:
    """The settings hook: one value-free diagnostic per offending field."""

    assert validate_settings({}) == []
    assert validate_settings(
        {
            "mode": "act",
            "channels": {"twitch/c2": {"mode": "alert"}},
            "act": {"operations": ["delete_message", "timeout"]},
            "max_timeout_seconds": 3600,
            "max_actions_per_window": 20,
            "window_seconds": 60,
            "per_target_cooldown_seconds": 0,
            "propose": {
                "max_pending": 256,
                "proposal_ttl_seconds": 30,
                "auto_apply": ["delete_message"],
                "approve_command": "!yes",
                "reject_command": "!no",
            },
            "limits": {"chat_context": {"max_messages": 50}},
        }
    ) == []
    cases = {
        "mode": {"mode": "silent"},
        "channels": {"channels": {"no-slash": {"mode": "act"}}},
        "channels.<channel>": {"channels": {"twitch/c2": {"mode": "loud"}}},
        "act.operations": {"act": {"operations": ["delete_message", "delete_message"]}},
        "max_timeout_seconds": {"max_timeout_seconds": 3601},
        "max_actions_per_window": {"max_actions_per_window": 21},
        "window_seconds": {"window_seconds": 59},
        "per_target_cooldown_seconds": {"per_target_cooldown_seconds": -1},
        "propose.max_pending": {"propose": {"max_pending": 257}},
        "propose.proposal_ttl_seconds": {"propose": {"proposal_ttl_seconds": 29}},
        "propose.auto_apply": {"propose": {"auto_apply": ["purge"]}},
        "propose.approve_command": {"propose": {"approve_command": "!mod ok"}},
        "propose.reject_command": {"propose": {"reject_command": "!modok"}},
        "unknown": {"unknown": 1},
    }
    for field_name, settings in cases.items():
        diagnostics = validate_settings(settings)
        assert len(diagnostics) == 1, (field_name, diagnostics)
        assert f"field {field_name!r}" in diagnostics[0], diagnostics
        assert diagnostics[0].startswith("module 'moderation'")
    # A boolean is not an integer, and no configured value is echoed back.
    (diagnostic,) = validate_settings({"max_timeout_seconds": True})
    assert "True" not in diagnostic


@pytest.mark.asyncio
async def test_refused_settings_fail_activation_without_echoing_a_value() -> None:
    runtime = runtime_context(clock=ManualClock(START))
    with pytest.raises(ModerationModuleError) as raised:
        await activate_moderation(runtime.for_module("moderation"), {"mode": "hush-7f3"}, {})
    assert "hush-7f3" not in str(raised.value)


# --------------------------------------------------------------------------- #
# AC24 — the default mode alerts
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_ac24_the_default_mode_alerts_with_no_platform_request_and_one_fact() -> None:
    """AC24: with no ``mode``, a granted ``delete_message`` ends ``success``
    disposition ``alerted``, the service receives 0 requests, and 1
    ``moderation.decision`` fact is published — carrying the mode, the
    disposition, the operation, the platform, the channel, the message, the
    target author and the reason."""

    h = await moderation_harness()
    try:
        await h.say("m-1", "viewer-7")
        observation = await h.request("m-1", reason="spam link")
        assert observation.status == "success", observation
        assert observation.result == {
            "disposition": "alerted",
            "operation": "delete_message",
            "message_id": "m-1",
        }
        assert h.service.requests == []
        (fact,) = h.facts()
        assert fact["mode"] == "alert" and fact["status"] == "success"
        assert fact["disposition"] == "alerted" and fact["code"] is None
        assert (fact["operation"], fact["platform"], fact["channel_id"]) == (
            "delete_message",
            "fake",
            CHANNEL,
        )
        assert (fact["message_id"], fact["target_author_id"], fact["reason"]) == (
            "m-1",
            "viewer-7",
            "spam link",
        )
        (completed,) = [
            event["payload"] for event in events_of(h.runtime.bus, "action.completed")
        ]
        assert completed["status"] == "success" and completed["disposition"] == "alerted"
        h.assert_one_fact_per_request()
        # Alert records even what act would refuse: 0 requests either way.
        assert (await h.request("unknown-id", operation="timeout", duration=9999)).status == (
            "success"
        )
        assert h.service.requests == []
        h.assert_one_fact_per_request()
    finally:
        await h.close()


@pytest.mark.asyncio
async def test_a_call_the_executor_refuses_never_reaches_the_module() -> None:
    """Default deny: without a rule the call is ``refused not_authorized``,
    the module is not entered — 0 requests, 0 facts."""

    h = await moderation_harness(grant=False, **ACT_BOTH)
    try:
        await h.say("m-1", "viewer-7")
        assert_refused(await h.request("m-1"), "not_authorized")
        assert h.service.requests == [] and h.facts() == []
        # The executor's refusal never reached the module: it owes no fact.
        h.calls -= 1
    finally:
        await h.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"operation": "timeout"}, "required with timeout"),
        ({"duration": 60}, "only valid with timeout"),
        ({"reason": "x" * 201}, "at most 200"),
        ({"message_id": " "}, "message_id"),
    ],
    ids=["timeout-without-duration", "delete-with-duration", "reason-201", "blank-message"],
)
async def test_arguments_the_schema_cannot_check_are_refused_before_any_rule(
    arguments: dict[str, Any], message: str
) -> None:
    """R5 arguments: ``duration_seconds`` only with ``timeout`` and required
    with it, ``reason`` at most 200 characters — ``error invalid_arguments``,
    0 requests, 1 fact (the fact's reason cut to 200)."""

    h = await moderation_harness(**ACT_BOTH)
    try:
        await h.say("m-1", "viewer-7")
        options = {"message_id": "m-1", **arguments}
        observation = await h.request(options.pop("message_id"), **options)
        assert observation.status == "error", observation
        assert observation.error["code"] == "invalid_arguments"
        assert message in observation.error["message"]
        assert h.service.requests == []
        (fact,) = h.facts()
        assert fact["status"] == "error" and fact["code"] == "invalid_arguments"
        assert fact["reason"] is None or len(fact["reason"]) <= 200
    finally:
        await h.close()


# --------------------------------------------------------------------------- #
# AC25 — offered only with a rule, once per run (the real module)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_ac25_the_real_module_is_offered_only_with_a_rule_and_once_per_run() -> None:
    """AC25 on the shipped module: loaded with the fixture platform and the
    shipped brain, ``moderation.request`` is offered 0 times while no rule
    grants it; once granted it is offered, the first model call runs
    (``alerted``, 1 fact, 0 platform requests) and a second call of it in
    the same run is ``refused run_limit`` without reaching the module;
    the granted ``chat.write`` is never offered."""

    clock = ManualClock(START)
    policy = AuthorizationPolicy(
        [
            AuthorizationRule(
                rule_id="brain-chat.write",
                action_name="chat.write",
                principals=(PRINCIPAL,),
                granted_permissions=("chat.write",),
            )
        ]
    )
    context = runtime_context(
        clock=clock,
        trigger_registry=TriggerRegistry(companion_name=COMPANION),
        chat=ChatContext(max_messages=16, max_bytes=8192, max_age_seconds=600.0, clock=clock),
        authorization=policy,
    )
    service = ScriptedModerationService(clock=clock)
    diagnostics: list[str] = []
    fixtures = ModuleLoader(context.bus, FIXTURE_MODULES, context=context, environ={})
    (platform,) = await fixtures.activate_enabled(
        {
            "enabled_modules": ["fakeplatform"],
            "modules": {
                "fakeplatform": {
                    "channel_ids": [CHANNEL],
                    "companion_name": COMPANION,
                    "transport": SimpleNamespace(outcomes=[], sends=[]),
                    "services": ["moderation"],
                    "moderation_service": service,
                    "diagnostic_reporter": diagnostics.append,
                }
            },
        }
    )
    arguments = {"operation": "delete_message", "message_id": "m-2", "reason": "spam link"}
    session = ScriptedModel(
        final("Nothing to do."),
        tool_call(MODERATION_ACTION, arguments),
        tool_call(MODERATION_ACTION, {**arguments, "reason": "again"}),
        final("Done."),
    )
    brain_settings = copy_settings(VALID_SETTINGS)
    brain_settings.update(
        {
            "_session_factory": lambda: session,
            "_sleeper": clock.sleep,
            "diagnostic_reporter": diagnostics.append,
        }
    )
    shipped = ModuleLoader(context.bus, SHIPPED_MODULES, context=context, environ={})
    activations = await shipped.activate_enabled(
        {
            "enabled_modules": ["moderation", "brain"],
            "modules": {"moderation": {"_sleeper": clock.sleep}, "brain": brain_settings},
        }
    )
    handles = {activation.name: activation.handle for activation in activations}
    brain = handles["brain"]
    for handle in (platform.handle, handles["moderation"], brain):
        await handle.prepare()

    def tools(index: int) -> list[str]:
        body = session.requests()[index]
        return [tool["function"]["name"] for tool in body.get("tools", [])]

    async def ask(message_id: str) -> None:
        before = len(events_of(context.bus, TRACE_BRAIN_RUN_COMPLETED))
        published = await platform.handle.inject(
            {
                "channel_id": CHANNEL,
                "author": {"id": "viewer-7", "display_name": "Viewer"},
                "message_id": message_id,
                "text": f"{COMPANION}, buy followers at spam.example",
            }
        )
        assert published is not None
        await wait_until(
            lambda: len(events_of(context.bus, TRACE_BRAIN_RUN_COMPLETED)) > before
        )
        await settle()

    try:
        await ask("m-1")
        assert tools(0) == []

        context.actions._authorization.grant(
            AuthorizationRule(
                rule_id="brain-moderation",
                action_name=MODERATION_ACTION,
                principals=(PRINCIPAL,),
                granted_permissions=("moderation.request",),
            )
        )
        await ask("m-2")
        assert [tools(index) for index in (1, 2, 3)] == [[MODERATION_ACTION]] * 3
        assert all("chat.write" not in tools(index) for index in range(4))
        tool_results = [
            json.loads(message["content"].split("\n", 1)[0])
            for message in session.requests()[3]["messages"]
            if message["role"] == "tool"
        ]
        first, second = tool_results
        assert first["status"] == "success" and first["result"]["disposition"] == "alerted"
        assert (second["status"], second["error"]["code"]) == ("refused", BRAIN_ERROR_RUN_LIMIT)
        started = [
            event["payload"]
            for event in events_of(context.bus, "action.started")
            if event["payload"]["action"] == MODERATION_ACTION
        ]
        assert len(started) == 1 and started[0]["principal"] == PRINCIPAL
        assert service.requests == []
        (fact,) = [event["payload"] for event in events_of(context.bus, FACT_MODERATION_DECISION)]
        assert (fact["disposition"], fact["target_author_id"]) == ("alerted", "viewer-7")
    finally:
        await brain.close()
        await handles["moderation"].close()
        await platform.handle.close()


# --------------------------------------------------------------------------- #
# AC27 — each strict rule refuses with its code and 0 requests
# --------------------------------------------------------------------------- #

PLATFORMS = ["fake", "twitch"]


@pytest.mark.asyncio
@pytest.mark.parametrize("platform", PLATFORMS)
async def test_ac27_a_timeout_outside_the_allowed_operations_is_operation_not_allowed(
    platform: str,
) -> None:
    """AC27: ``operations`` defaulting to ``[delete_message]``, a timeout →
    ``operation_not_allowed``, 0 requests; the delete of the same message is
    applied."""

    h = await moderation_harness(platform=platform, mode="act")
    try:
        await h.say("m-1", "viewer-7")
        assert_refused(await h.request("m-1", operation="timeout", duration=60), "operation_not_allowed")
        assert h.service.requests == []
        assert (await h.request("m-1")).result["disposition"] == "applied"
        assert len(h.service.requests) == 1
        assert [fact["code"] for fact in h.facts()] == ["operation_not_allowed", None]
        h.assert_one_fact_per_request()
    finally:
        await h.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("platform", PLATFORMS)
async def test_ac27_an_unknown_message_is_target_unknown(platform: str) -> None:
    """AC27: an unknown message id → ``target_unknown``, 0 requests."""

    h = await moderation_harness(platform=platform, mode="act")
    try:
        await h.say("m-1", "viewer-7")
        assert_refused(await h.request("never-seen"), "target_unknown")
        assert_refused(await h.request("m-1", channel=OTHER_CHANNEL), "target_unknown")
        assert h.service.requests == []
        (first, second) = h.facts()
        assert first["code"] == "target_unknown" and first["target_author_id"] is None
        h.assert_one_fact_per_request()
    finally:
        await h.close()


@pytest.mark.asyncio
async def test_a_message_older_than_the_index_but_still_in_the_context_is_target_unknown() -> None:
    """Plan decision 7 and its risk: the index is capped by
    ``limits.chat_context.max_messages``; a message it evicted but the chat
    context still retains is ``target_unknown`` (the conservative choice);
    one evicted from the chat context but still indexed is too."""

    h = await moderation_harness(
        mode="act", limits={"chat_context": {"max_messages": 2}}, chat_max_messages=16
    )
    try:
        for index in range(1, 4):
            await h.say(f"m-{index}", f"viewer-{index}")
        assert h.handle.indexed("fake", CHANNEL) == ("m-2", "m-3")
        assert_refused(await h.request("m-1"), "target_unknown")
        assert h.service.requests == []
    finally:
        await h.close()

    h = await moderation_harness(mode="act", chat_max_messages=1)
    try:
        await h.say("m-1", "viewer-1")
        await h.say("m-2", "viewer-2")
        assert "m-1" in h.handle.indexed("fake", CHANNEL)
        assert_refused(await h.request("m-1"), "target_unknown")
        assert h.service.requests == []
        assert (await h.request("m-2")).result["disposition"] == "applied"
    finally:
        await h.close()


@pytest.mark.asyncio
async def test_a_notice_is_never_indexed_as_a_target() -> None:
    """Plan decision 2: only ``kind == message`` events are indexed."""

    h = await moderation_harness(platform="twitch", mode="act")
    try:
        h.runtime.chat.append("twitch", CHANNEL, ChatEntry("viewer-7", "n-1", "gifted 5 subs"))
        await h.runtime.bus.publish(
            "channel.chat.message",
            {
                "platform": "twitch",
                "channel_id": CHANNEL,
                "author": {"id": "viewer-7"},
                "message_id": "n-1",
                "text": "gifted 5 subs",
                "kind": "community_sub_gift",
            },
            {"source": "twitch", "schema_version": 2},
        )
        assert h.handle.indexed("twitch", CHANNEL) == ()
        assert_refused(await h.request("n-1"), "target_unknown")
        assert h.service.requests == []
    finally:
        await h.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("platform", PLATFORMS)
@pytest.mark.parametrize(
    ("author", "roles"),
    [
        ("mod-1", ["moderator"]),
        ("vip-1", ["vip", "subscription"]),
        ("owner-1", ["broadcaster"]),
        ("companion-bot", []),
    ],
    ids=["moderator", "vip", "broadcaster", "companion"],
)
async def test_ac27_a_protected_author_is_target_protected(
    platform: str, author: str, roles: list[str]
) -> None:
    """AC27: a message by a trusted moderator, VIP, the broadcaster or the
    companion (compared case-insensitively with the identity the platform's
    service states) → ``target_protected``, 0 requests — for a delete and a
    timeout alike."""

    h = await moderation_harness(platform=platform, **ACT_BOTH)
    try:
        await h.say("m-1", author, roles=roles)
        assert_refused(await h.request("m-1"), "target_protected")
        assert_refused(await h.request("m-1", operation="timeout", duration=60), "target_protected")
        assert h.service.requests == []
        assert [fact["target_author_id"] for fact in h.facts()] == [author, author]
        h.assert_one_fact_per_request()
    finally:
        await h.close()


@pytest.mark.asyncio
async def test_roles_without_an_attestation_protect_nobody() -> None:
    """Only attested roles are trusted: ``moderator`` claimed with no
    ``roles_provenance`` does not protect its author (the fixture platform
    refuses such a message outright, so it arrives twitch-shaped here)."""

    h = await moderation_harness(platform="twitch", mode="act")
    try:
        await h.say("m-1", "viewer-7", roles=["moderator"], provenance=None)
        assert (await h.request("m-1")).result["disposition"] == "applied"
        assert len(h.service.requests) == 1
    finally:
        await h.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("platform", PLATFORMS)
async def test_ac27_a_duration_above_the_maximum_is_duration_out_of_range(platform: str) -> None:
    """AC27: ``duration_seconds`` 301 with ``max_timeout_seconds: 300`` →
    ``duration_out_of_range``, and so is 0 — 0 requests; 300 is sent as is."""

    h = await moderation_harness(platform=platform, max_timeout_seconds=300, **ACT_BOTH)
    try:
        await h.say("m-1", "viewer-7")
        await h.say("m-2", "viewer-8")
        assert_refused(await h.request("m-1", operation="timeout", duration=301), "duration_out_of_range")
        assert_refused(await h.request("m-1", operation="timeout", duration=0), "duration_out_of_range")
        assert h.service.requests == []
        assert (await h.request("m-2", operation="timeout", duration=300)).status == "success"
        (sent,) = h.service.requests
        assert (sent["operation"], sent["duration_seconds"], sent["target_author_id"]) == (
            "timeout",
            300,
            "viewer-8",
        )
        h.assert_one_fact_per_request()
    finally:
        await h.close()


@pytest.mark.asyncio
async def test_the_duration_is_checked_after_the_platform_rounding() -> None:
    """R5: ``duration_out_of_range`` is judged after the service's
    ``round_duration`` hook — on a whole-minute platform 241 s is sent as
    300 s, while 250 s with ``max_timeout_seconds: 299`` rounds to 300 and
    is refused although 250 is within the bound."""

    service = CompanionModerationService(duration_unit_seconds=60)
    h = await moderation_harness(service, max_timeout_seconds=299, **ACT_BOTH)
    try:
        await h.say("m-1", "viewer-7")
        assert_refused(await h.request("m-1", operation="timeout", duration=250), "duration_out_of_range")
        assert service.requests == []
    finally:
        await h.close()

    service = CompanionModerationService(duration_unit_seconds=60)
    h = await moderation_harness(service, max_timeout_seconds=300, **ACT_BOTH)
    try:
        await h.say("m-1", "viewer-7")
        assert (await h.request("m-1", operation="timeout", duration=241)).status == "success"
        assert [request["duration_seconds"] for request in service.requests] == [300]
    finally:
        await h.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("platform", PLATFORMS)
async def test_ac27_the_fourth_application_inside_the_window_is_rate_limited(platform: str) -> None:
    """AC27: with ``max_actions_per_window: 3`` (window 600 s), the 4th
    application within 600 s → ``rate_limited`` with 0 requests; at 600 s
    after the first the slot is free again."""

    h = await moderation_harness(platform=platform, mode="act", max_actions_per_window=3)
    try:
        for index in range(1, 6):
            await h.say(f"m-{index}", f"viewer-{index}")
        for index in range(1, 4):
            assert (await h.request(f"m-{index}")).result["disposition"] == "applied"
            h.clock.advance(100.0)
        assert_refused(await h.request("m-4"), "rate_limited")
        assert len(h.service.requests) == 3
        h.clock.advance(299.0)  # 599 s after the first request
        assert_refused(await h.request("m-4"), "rate_limited")
        h.clock.advance(1.0)
        assert (await h.request("m-4")).result["disposition"] == "applied"
        assert len(h.service.requests) == 4
        h.assert_one_fact_per_request()
    finally:
        await h.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("platform", PLATFORMS)
async def test_ac27_a_second_action_on_the_same_author_is_target_cooldown(platform: str) -> None:
    """AC27: a second action on the same author (another message of theirs)
    within 600 s → ``target_cooldown``, 0 requests; another author is not
    cooled down; at 600 s the author may be acted on again."""

    h = await moderation_harness(platform=platform, **ACT_BOTH)
    try:
        await h.say("m-1", "viewer-7")
        await h.say("m-2", "viewer-7")
        await h.say("m-3", "viewer-8")
        assert (await h.request("m-1")).status == "success"
        h.clock.advance(599.0)
        assert_refused(await h.request("m-2", operation="timeout", duration=60), "target_cooldown")
        assert len(h.service.requests) == 1
        assert (await h.request("m-3")).status == "success"
        h.clock.advance(1.0)
        assert (await h.request("m-2")).status == "success"
        assert [request["message_id"] for request in h.service.requests] == ["m-1", "m-3", "m-2"]
        h.assert_one_fact_per_request()
    finally:
        await h.close()


@pytest.mark.asyncio
async def test_ac27_an_operation_the_platform_does_not_offer_is_platform_unsupported() -> None:
    """AC27 (the Kick case runs in P19): a service offering ``timeout`` only
    refuses a delete ``platform_unsupported`` with 0 requests; a platform
    with no moderation service refuses every application the same way."""

    service = CompanionModerationService(operations={"timeout"})
    h = await moderation_harness(service, **ACT_BOTH)
    try:
        await h.say("m-1", "viewer-7")
        assert_refused(await h.request("m-1"), "platform_unsupported")
        assert service.requests == []
        assert (await h.request("m-1", operation="timeout", duration=60)).status == "success"
    finally:
        await h.close()

    h = await moderation_harness(False, platform="unserved", **ACT_BOTH)
    try:
        await h.say("m-1", "viewer-7")
        assert h.handle.bound_platforms == ()
        assert_refused(await h.request("m-1"), "platform_unsupported")
        # A refusal reserves nothing: no rate window is kept for the channel.
        assert h.handle._windows == {}
        h.assert_one_fact_per_request()
    finally:
        await h.close()


@pytest.mark.asyncio
async def test_the_rules_are_checked_in_the_specification_order() -> None:
    """R5: a request breaking every rule is refused by the first one; each
    rule, once satisfied, yields to the next — the codes come out in
    :data:`STRICT_RULES` order, with 0 requests throughout."""

    service = CompanionModerationService(operations=set())
    h = await moderation_harness(
        service, mode="act", max_actions_per_window=1, max_timeout_seconds=60
    )
    try:
        await h.say("seed", "viewer-0")
        await h.say("m-mod", "mod-1", roles=["moderator"])
        await h.say("m-1", "viewer-7")
        await h.say("m-2", "viewer-7")
        codes: list[str] = []

        async def refusal(message_id: str, **options: Any) -> None:
            observation = await h.request(message_id, **options)
            assert observation.status == "refused", observation
            codes.append(observation.error["code"])

        await refusal("nowhere", operation="timeout", duration=999)
        h.handle._settings = dataclasses.replace(
            h.handle.settings, act_operations=frozenset({"delete_message", "timeout"})
        )
        await refusal("nowhere", operation="timeout", duration=999)
        await refusal("m-mod", operation="timeout", duration=999)
        await refusal("m-1", operation="timeout", duration=999)
        # Fill the channel's window and the author's cooldown by hand.
        h.handle._windows[("fake", CHANNEL)] = deque([START])
        await refusal("m-1", operation="timeout", duration=60)
        h.handle._windows[("fake", CHANNEL)].clear()
        h.handle._target_sent[("fake", CHANNEL, "viewer-7")] = START
        await refusal("m-2", operation="timeout", duration=60)
        h.handle._target_sent.clear()
        await refusal("m-2", operation="timeout", duration=60)
        assert codes == list(STRICT_RULES)
        assert service.requests == []
        h.assert_one_fact_per_request()
    finally:
        await h.close()


# --------------------------------------------------------------------------- #
# AC28 — one request, its outcome, never a second
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
@pytest.mark.parametrize("platform", PLATFORMS)
async def test_ac28_an_allowed_delete_sends_exactly_one_request_and_is_applied(platform: str) -> None:
    """AC28: an allowed delete sends exactly 1 request (the message, its
    author, the reason) and ends ``applied``; its fact says so."""

    h = await moderation_harness(platform=platform, mode="act")
    try:
        await h.say("m-1", "viewer-7")
        observation = await h.request("m-1", reason="spam link")
        assert observation.status == "success", observation
        assert observation.result["disposition"] == "applied"
        (sent,) = h.service.requests
        assert sent == {
            "operation": "delete_message",
            "channel_id": CHANNEL,
            "message_id": "m-1",
            "target_author_id": "viewer-7",
            "duration_seconds": None,
            "reason": "spam link",
            "at": START,
        }
        (fact,) = h.facts()
        assert (fact["mode"], fact["status"], fact["disposition"]) == ("act", "success", "applied")
        assert h.handle.window(platform, CHANNEL) == (START,)
    finally:
        await h.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scripted", "status", "code"),
    [
        ("rejected", "error", "platform_rejected"),
        ("rate", "error", "rate_limited_platform"),
        ("lost", "external_unknown", "external_effect_unknown"),
        ("server_error", "external_unknown", "external_effect_unknown"),
        (ConnectionResetError("lost"), "external_unknown", "external_effect_unknown"),
    ],
    ids=["rejected", "rate", "lost", "server-error", "raised"],
)
async def test_ac28_each_platform_outcome_maps_and_is_never_retried(
    scripted: Any, status: str, code: str
) -> None:
    """AC28: a platform refusal → ``error platform_rejected``; a rate refusal
    → ``error rate_limited_platform``; a lost response or a server error →
    ``external_unknown`` — each with 1 request and 0 second request; the
    reservation stands (the window counts the request), and the fact carries
    the executor's terminal status and code."""

    service = CompanionModerationService(outcomes=[scripted])
    h = await moderation_harness(service, mode="act")
    try:
        await h.say("m-1", "viewer-7")
        observation = await h.request("m-1")
        assert observation.status == status, observation
        assert observation.error["code"] == code
        await settle()
        assert len(service.requests) == 1
        assert len(h.handle.window("fake", CHANNEL)) == 1
        (fact,) = h.facts()
        assert (fact["status"], fact["code"], fact["disposition"]) == (status, code, None)
        # The same message again is the author's cooldown: still no second request.
        assert_refused(await h.request("m-1"), "target_cooldown")
        assert len(service.requests) == 1
        h.assert_one_fact_per_request()
    finally:
        await h.close()


@pytest.mark.asyncio
async def test_ac28_the_deadline_after_sending_is_external_unknown_with_one_fact() -> None:
    """AC28: the call deadline passing while the request is pending ends the
    call ``external_unknown`` (it was declared emitted before the request
    left); 1 request, and the one fact states the executor's outcome."""

    service = BlockingModerationService()
    h = await moderation_harness(service, mode="act", executor_on_clock=True)
    try:
        await h.say("m-1", "viewer-7")
        pending = h.start("m-1")
        await wait_until(lambda: len(service.requests) == 1)
        h.clock.advance(10.0)
        observation = await pending
        await settle()
        assert observation.status == "external_unknown", observation
        assert len(service.requests) == 1
        (fact,) = h.facts()
        assert fact["status"] == "external_unknown" and fact["target_author_id"] == "viewer-7"
        service.release.set()
        await settle()
        assert len(service.requests) == 1 and len(h.facts()) == 1
    finally:
        service.release.set()
        await h.close()


@pytest.mark.asyncio
async def test_ac28_the_twitch_service_deletes_once_and_is_applied() -> None:
    """AC28 on the twitch module's own service: an allowed delete is one
    ``DELETE helix/moderation/chat`` for the message, ``applied``; nothing in
    the traces names a credential."""

    clock = ManualClock(START)
    chat = ChatContext(max_messages=16, max_bytes=8192, max_age_seconds=600.0, clock=clock)
    runtime = runtime_context(clock=clock, chat=chat)

    class HelixModerationSession:
        def __init__(self) -> None:
            self.requests: list[dict[str, Any]] = []

        async def delete(self, url: str, **kwargs: Any) -> Any:
            assert url == HELIX_MODERATION_CHAT_URL, url
            self.requests.append({"method": "DELETE", **kwargs})
            return helix_message_deleted()

        async def close(self) -> None:
            return None

    session = HelixModerationSession()
    diagnostics: list[str] = []

    async def no_delay(_: float) -> None:
        await asyncio.sleep(0)

    twitch = await activate_twitch(
        runtime.for_module("twitch"),
        {
            **TWITCH_SETTINGS,
            "_session_factory": lambda: session,
            "_retry_delay": no_delay,
            "diagnostic_reporter": diagnostics.append,
        },
        {},
    )
    handle = await activate_moderation(
        runtime.for_module("moderation"), {"mode": "act", "_sleeper": clock.sleep}, {}
    )
    h = ModerationHarness(runtime, handle, None, clock, "twitch", None)
    try:
        await handle.prepare()
        assert handle.bound_platforms == ("twitch",)
        runtime.actions._authorization.grant(moderation_rule())
        await h.say("tw-1", "viewer-7", channel=TWITCH_CHANNEL)
        observation = await h.request("tw-1", channel=TWITCH_CHANNEL)
        assert observation.status == "success", observation
        assert observation.result["disposition"] == "applied"
        (sent,) = session.requests
        assert sent["params"] == {
            "broadcaster_id": TWITCH_CHANNEL,
            "moderator_id": "bot-24",
            "message_id": "tw-1",
        }
        (fact,) = h.facts()
        assert fact["disposition"] == "applied" and fact["platform"] == "twitch"
        texts = trace_texts(runtime.bus) + diagnostics
        for secret in TWITCH_CREDENTIALS:
            assert sum(secret in text for text in texts) == 0, secret
    finally:
        await handle.close()
        await twitch.close()


KICK_CHANNEL = "4242"
KICK_SETTINGS = {
    "client_secret": "never-show-kick-secret",
    "access_token": "never-show-kick-token",
    "listener": {"host": "127.0.0.1", "port": 0},
    "public_key": KICK_TEST_KEY.public_pem,
    "channels": [KICK_CHANNEL],
    "companion_name": "Companion",
}


class KickBansSession:
    """The kick module's HTTP session, scripted for the bans endpoint."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    async def post(self, url: str, **kwargs: Any) -> Any:
        assert url == KICK_BANS_URL, url
        self.requests.append(kwargs)
        return FakeResponse(200, {"data": {}, "message": "OK"})

    async def close(self) -> None:
        return None


async def kick_harness(**settings: Any) -> tuple[ModerationHarness, Any, KickBansSession]:
    """The moderation module beside the kick module's own service."""

    clock = ManualClock(START)
    chat = ChatContext(max_messages=16, max_bytes=8192, max_age_seconds=600.0, clock=clock)
    runtime = runtime_context(clock=clock, chat=chat)
    session = KickBansSession()
    kick = await activate_kick(
        runtime.for_module("kick"),
        {**KICK_SETTINGS, "_session_factory": lambda: session, "_wall_clock": clock},
        {},
    )
    handle = await activate_moderation(
        runtime.for_module("moderation"), {"_sleeper": clock.sleep, **settings}, {}
    )
    await handle.prepare()
    runtime.actions._authorization.grant(moderation_rule())
    harness = ModerationHarness(runtime, handle, kick.moderation_service, clock, "kick", None)
    _HARNESSES.append(harness)
    return harness, kick, session


@pytest.mark.asyncio
async def test_ac27_delete_message_on_kick_is_platform_unsupported_with_no_request() -> None:
    """AC27 (the Kick case): in mode ``act``, the kick service offers
    ``timeout`` only, so a ``delete_message`` on kick is ``refused
    platform_unsupported`` with 0 platform requests."""

    h, kick, session = await kick_harness(**ACT_BOTH)
    try:
        assert h.handle.bound_platforms == ("kick",)
        await h.say("k-1", "viewer-7", channel=KICK_CHANNEL)
        assert_refused(await h.request("k-1", channel=KICK_CHANNEL), "platform_unsupported")
        assert session.requests == []
        (fact,) = h.facts()
        assert fact["platform"] == "kick"
    finally:
        await h.close()
        await kick.close()


@pytest.mark.asyncio
async def test_a_90_second_kick_timeout_is_applied_as_2_minutes() -> None:
    """AC35 through the moderation module: the kick service rounds 90 s up to
    120 s for the bound check and sends ``duration: 2`` (minutes), once."""

    h, kick, session = await kick_harness(**ACT_BOTH)
    try:
        await h.say("k-1", "viewer-7", channel=KICK_CHANNEL)
        observation = await h.request(
            "k-1", channel=KICK_CHANNEL, operation="timeout", duration=90
        )
        assert observation.status == "success", observation
        assert observation.result["disposition"] == "applied"
        (sent,) = session.requests
        assert sent["json"]["duration"] == 2
        assert sent["json"]["user_id"] == "viewer-7"
    finally:
        await h.close()
        await kick.close()


# --------------------------------------------------------------------------- #
# Concurrency — the per-channel lock and the reservation before the send
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_five_concurrent_requests_send_three_and_refuse_two_rate_limited() -> None:
    """F-P2: with a service whose request blocks, ``max_actions_per_window:
    3`` and 5 distinct targets, 5 requests launched together give exactly 3
    service requests and 2 ``rate_limited`` refusals — checked before any
    request is answered."""

    service = BlockingModerationService()
    h = await moderation_harness(service, mode="act", max_actions_per_window=3)
    try:
        for index in range(1, 6):
            await h.say(f"m-{index}", f"viewer-{index}")
        gathered = asyncio.ensure_future(
            asyncio.gather(*(h.start(f"m-{index}") for index in range(1, 6)))
        )
        await wait_until(lambda: len(service.requests) == 3)
        await settle()
        assert len(service.requests) == 3
        refused = [fact for fact in h.facts() if fact["status"] == "refused"]
        assert [fact["code"] for fact in refused] == ["rate_limited", "rate_limited"]
        assert not gathered.done()

        service.release.set()
        observations = await gathered
        await settle()
        statuses = sorted(
            observation.result["disposition"] if observation.status == "success"
            else observation.error["code"]
            for observation in observations
        )
        assert statuses == ["applied", "applied", "applied", "rate_limited", "rate_limited"]
        assert len(service.requests) == 3
        h.assert_one_fact_per_request()
    finally:
        service.release.set()
        await h.close()


@pytest.mark.asyncio
async def test_two_concurrent_requests_on_one_author_send_one() -> None:
    """F-P2: two requests on two messages of the same author, launched
    together, give 1 request and 1 ``target_cooldown`` refusal — the
    cooldown key is the author, reserved before the send."""

    service = BlockingModerationService()
    h = await moderation_harness(service, mode="act")
    try:
        await h.say("m-1", "viewer-7")
        await h.say("m-2", "viewer-7")
        both = asyncio.ensure_future(asyncio.gather(h.start("m-1"), h.start("m-2")))
        await wait_until(lambda: len(h.facts()) == 1)
        await settle()
        assert len(service.requests) == 1
        (refusal,) = h.facts()
        assert refusal["code"] == "target_cooldown" and refusal["target_author_id"] == "viewer-7"
        service.release.set()
        first, second = await both
        await settle()
        assert first.result["disposition"] == "applied"
        assert_refused(second, "target_cooldown")
        assert len(service.requests) == 1
        h.assert_one_fact_per_request()
    finally:
        service.release.set()
        await h.close()


@pytest.mark.asyncio
async def test_two_concurrent_requests_on_two_channels_both_send() -> None:
    """F-P2: the lock and the counters are per channel — with
    ``max_actions_per_window: 1``, requests on two channels launched
    together both send while the first is still pending."""

    service = BlockingModerationService()
    h = await moderation_harness(service, mode="act", max_actions_per_window=1)
    try:
        await h.say("m-1", "viewer-7")
        await h.say("m-2", "viewer-7", channel=OTHER_CHANNEL)
        both = asyncio.ensure_future(
            asyncio.gather(h.start("m-1"), h.start("m-2", channel=OTHER_CHANNEL))
        )
        await wait_until(lambda: len(service.requests) == 2)
        assert {request["channel_id"] for request in service.requests} == {CHANNEL, OTHER_CHANNEL}
        service.release.set()
        observations = await both
        await settle()
        assert [observation.result["disposition"] for observation in observations] == [
            "applied",
            "applied",
        ]
        h.assert_one_fact_per_request()
    finally:
        service.release.set()
        await h.close()


# --------------------------------------------------------------------------- #
# AC26 — mode propose: proposals, commands, expiry, eviction, auto_apply
# --------------------------------------------------------------------------- #

BROADCASTER = "streamer-1"
MODERATOR = "mod-1"
PROPOSE = {"mode": "propose"}


async def proposed(h: ModerationHarness, message_id: str, **options: Any) -> str:
    """One request in mode ``propose``: its proposal id, after 0 requests."""

    before = len(h.service.requests)
    observation = await h.request(message_id, **options)
    assert observation.status == "success", observation
    assert observation.result["disposition"] == "proposed"
    assert len(h.service.requests) == before
    return observation.result["proposal_id"]


@pytest.mark.asyncio
async def test_ac26_a_request_in_mode_propose_yields_a_proposal_id_and_no_request() -> None:
    """AC26: a request in mode ``propose`` ends ``success`` disposition
    ``proposed`` with a proposal id from the injected id source, sends 0
    platform requests, and its one fact carries the id.

    Supersedes P14's interim ``refused mode_unavailable`` for mode
    ``propose`` (R5, AC26: plan step P15 adds the proposal table)."""

    h = await moderation_harness(**PROPOSE)
    try:
        await h.say("m-1", "viewer-7")
        observation = await h.request("m-1")
        assert observation.status == "success", observation
        assert observation.result == {
            "disposition": "proposed",
            "operation": "delete_message",
            "message_id": "m-1",
            "proposal_id": "p-1",
        }
        assert h.service.requests == []
        assert h.handle.pending("fake", CHANNEL) == ("p-1",)
        (fact,) = h.facts()
        assert (fact["mode"], fact["status"], fact["disposition"]) == (
            "propose",
            "success",
            "proposed",
        )
        assert fact["proposal_id"] == "p-1" and fact["target_author_id"] == "viewer-7"
    finally:
        await h.close()


@pytest.mark.asyncio
async def test_ac26_the_broadcaster_approves_with_one_request_and_applied() -> None:
    """AC26: ``!modok <id>`` from the trusted broadcaster applies the
    proposal — 1 platform request, one fact ``applied`` naming the approver;
    the proposal leaves the table, so a second approval does nothing."""

    h = await moderation_harness(**PROPOSE)
    try:
        await h.say("m-1", "viewer-7")
        proposal_id = await proposed(h, "m-1", reason="spam link")
        await h.command(
            f"!modok {proposal_id}", BROADCASTER, roles=["broadcaster"], counts="approval"
        )
        (sent,) = h.service.requests
        assert (sent["operation"], sent["message_id"], sent["target_author_id"]) == (
            "delete_message",
            "m-1",
            "viewer-7",
        )
        assert sent["reason"] == "spam link"
        approval = h.facts()[-1]
        assert (approval["decision"], approval["status"], approval["disposition"]) == (
            "approved",
            "success",
            "applied",
        )
        assert approval["proposal_id"] == proposal_id and approval["decided_by"] == BROADCASTER
        assert approval["call_id"] == h.facts()[0]["call_id"]
        assert h.handle.pending("fake", CHANNEL) == ()
        assert h.handle.window("fake", CHANNEL) == (START,)
        await h.command(f"!modok {proposal_id}", BROADCASTER, roles=["broadcaster"])
        assert len(h.service.requests) == 1
        assert h.handle.ignored_commands["unknown_id"] == 1
    finally:
        await h.close()


@pytest.mark.asyncio
async def test_ac26_an_approval_without_a_trusted_role_applies_nothing() -> None:
    """AC26: the same command from a viewer (no trusted role), from a VIP, or
    naming the id from another channel applies nothing — 0 requests, no fact, counted — and the proposal still
    waits for the broadcaster."""

    h = await moderation_harness(**PROPOSE)
    try:
        await h.say("m-1", "viewer-7")
        proposal_id = await proposed(h, "m-1")
        await h.command(f"!modok {proposal_id}", "viewer-8")
        await h.command(f"!modok {proposal_id}", "vip-1", roles=["vip"])
        assert h.handle.ignored_commands["untrusted"] == 2
        # The channel is part of the lookup key.
        await h.command(
            f"!modok {proposal_id}", BROADCASTER, roles=["broadcaster"], channel=OTHER_CHANNEL
        )
        await h.command(f"!modno {proposal_id}", MODERATOR, roles=["moderator"], channel=OTHER_CHANNEL)
        assert h.handle.ignored_commands["unknown_id"] == 2
        # Neither a command word alone nor a longer line is a command.
        await h.command("!modok", BROADCASTER, roles=["broadcaster"])
        await h.command(f"!modok {proposal_id} now", BROADCASTER, roles=["broadcaster"])
        assert h.service.requests == [] and len(h.facts()) == 1
        assert h.handle.pending("fake", CHANNEL) == (proposal_id,)
        await h.command(
            f"!modok {proposal_id}", MODERATOR, roles=["moderator"], counts="approval"
        )
        assert len(h.service.requests) == 1
        assert h.facts()[-1]["disposition"] == "applied"
    finally:
        await h.close()


@pytest.mark.asyncio
async def test_roles_no_platform_attested_decide_nothing() -> None:
    """AC26: a broadcaster role without ``author.roles_provenance`` is not
    trusted — the approval is counted and applies nothing."""

    h = await moderation_harness(platform="twitch", **PROPOSE)
    try:
        await h.say("m-1", "viewer-7")
        proposal_id = await proposed(h, "m-1")
        await h.command(f"!modok {proposal_id}", "viewer-9", roles=["broadcaster"], provenance=None)
        assert h.handle.ignored_commands["untrusted"] == 1
        assert h.service.requests == [] and len(h.facts()) == 1
        assert h.handle.pending("twitch", CHANNEL) == (proposal_id,)
    finally:
        await h.close()


@pytest.mark.asyncio
async def test_ac26_a_moderator_rejection_discards_the_proposal() -> None:
    """AC26: ``!modno <id>`` from a trusted moderator discards the proposal —
    one ``rejected`` fact, 0 requests; a later approval finds nothing."""

    h = await moderation_harness(**PROPOSE)
    try:
        await h.say("m-1", "viewer-7")
        proposal_id = await proposed(h, "m-1")
        await h.command(f"!modno {proposal_id}", MODERATOR, roles=["moderator"], counts="rejection")
        rejection = h.facts()[-1]
        assert (rejection["decision"], rejection["status"], rejection["disposition"]) == (
            "rejected",
            "success",
            "rejected",
        )
        assert rejection["decided_by"] == MODERATOR and rejection["code"] is None
        assert h.handle.pending("fake", CHANNEL) == ()
        await h.command(f"!modok {proposal_id}", BROADCASTER, roles=["broadcaster"])
        assert h.service.requests == []
        assert h.handle.ignored_commands["unknown_id"] == 1
    finally:
        await h.close()


@pytest.mark.asyncio
async def test_ac26_an_unapproved_proposal_expires_after_300_seconds() -> None:
    """AC26: on the injected clock an unapproved proposal still waits at
    299 s and expires at 300 s with exactly 1 ``expired`` fact, published by
    the supervised sweep; an approval after it applies nothing."""

    h = await moderation_harness(**PROPOSE)
    try:
        await h.say("m-1", "viewer-7")
        proposal_id = await proposed(h, "m-1")
        h.clock.advance(299.0)
        await settle()
        assert len(h.facts()) == 1 and h.handle.pending("fake", CHANNEL) == (proposal_id,)
        h.clock.advance(1.0)
        await settle()
        h.expiries += 1
        expired = [fact for fact in h.facts() if fact["disposition"] == "expired"]
        assert len(expired) == 1
        assert (expired[0]["decision"], expired[0]["status"]) == ("expired", "success")
        assert expired[0]["proposal_id"] == proposal_id
        assert h.handle.pending("fake", CHANNEL) == ()
        await h.command(f"!modok {proposal_id}", BROADCASTER, roles=["broadcaster"])
        h.clock.advance(3600.0)
        await settle()
        assert h.service.requests == [] and len(expired) == 1
    finally:
        await h.close()


@pytest.mark.asyncio
async def test_ac26_the_ttl_is_configurable_and_the_sweep_follows_new_proposals() -> None:
    """``proposal_ttl_seconds``: each proposal expires on its own instant,
    also when it was stored while the sweep was idle."""

    h = await moderation_harness(mode="propose", propose={"proposal_ttl_seconds": 30})
    try:
        await h.say("m-1", "viewer-7")
        await h.say("m-2", "viewer-8")
        first = await proposed(h, "m-1")
        h.clock.advance(20.0)
        second = await proposed(h, "m-2")
        h.clock.advance(10.0)
        await settle()
        h.expiries += 1
        assert h.handle.pending("fake", CHANNEL) == (second,)
        h.clock.advance(20.0)
        await settle()
        h.expiries += 1
        assert [fact["proposal_id"] for fact in h.facts() if fact["disposition"] == "expired"] == [
            first,
            second,
        ]
        # The idle sweep wakes for a proposal stored later.
        await h.say("m-3", "viewer-9")
        third = await proposed(h, "m-3")
        h.clock.advance(30.0)
        await settle()
        h.expiries += 1
        assert h.facts()[-1]["proposal_id"] == third
    finally:
        await h.close()


@pytest.mark.asyncio
async def test_ac26_with_max_pending_2_a_third_proposal_evicts_the_closest_to_expiry() -> None:
    """AC26: with ``max_pending: 2`` the third proposal evicts the one
    closest to expiry, which publishes its ``expired`` fact."""

    h = await moderation_harness(mode="propose", propose={"max_pending": 2})
    try:
        for index in range(1, 4):
            await h.say(f"m-{index}", f"viewer-{index}")
        first = await proposed(h, "m-1")
        h.clock.advance(10.0)
        second = await proposed(h, "m-2", channel=CHANNEL)
        h.clock.advance(10.0)
        third = await proposed(h, "m-3")
        h.expiries += 1
        assert h.handle.pending("fake", CHANNEL) == (second, third)
        (evicted,) = [fact for fact in h.facts() if fact["disposition"] == "expired"]
        assert evicted["proposal_id"] == first and evicted["message_id"] == "m-1"
        await h.command(f"!modok {first}", BROADCASTER, roles=["broadcaster"])
        assert h.service.requests == []
    finally:
        await h.close()


@pytest.mark.asyncio
async def test_ac26_auto_apply_applies_a_delete_at_once_under_the_strict_rules() -> None:
    """AC26: ``auto_apply: [delete_message]`` applies a delete at once — 1
    request, ``applied``, nothing stored — under the strict rules (a
    protected author is still ``target_protected`` with 0 requests); an
    operation outside ``auto_apply`` is still proposed."""

    h = await moderation_harness(
        mode="propose",
        act={"operations": ["delete_message", "timeout"]},
        propose={"auto_apply": ["delete_message"]},
    )
    try:
        await h.say("m-1", "viewer-7")
        await h.say("m-2", "mod-2", roles=["moderator"])
        await h.say("m-3", "viewer-9")
        observation = await h.request("m-1")
        assert observation.status == "success" and observation.result["disposition"] == "applied"
        assert len(h.service.requests) == 1
        assert h.handle.pending("fake", CHANNEL) == ()
        assert_refused(await h.request("m-2"), "target_protected")
        assert len(h.service.requests) == 1
        assert await proposed(h, "m-3", operation="timeout", duration=60) == "p-1"
        assert [fact["mode"] for fact in h.facts()] == ["propose"] * 3
    finally:
        await h.close()


@pytest.mark.asyncio
async def test_approval_checks_the_strict_rules_on_the_state_at_approval() -> None:
    """Plan P15 risk: the rules read the state at approval — a message that
    left the chat context since the proposal is ``target_unknown``, and an
    operation outside ``act.operations`` is ``operation_not_allowed``; each
    approval sends 0 requests and publishes its one fact."""

    h = await moderation_harness(mode="propose", chat_max_messages=2)
    try:
        await h.say("m-1", "viewer-7")
        stale = await proposed(h, "m-1")
        await h.say("m-2", "viewer-8")
        timeout = await proposed(h, "m-2", operation="timeout", duration=60)
        await h.say("m-3", "viewer-9")
        await h.command(f"!modok {stale}", BROADCASTER, roles=["broadcaster"], counts="approval")
        await h.command(f"!modok {timeout}", BROADCASTER, roles=["broadcaster"], counts="approval")
        refusals = [fact for fact in h.facts() if fact.get("decision") == "approved"]
        assert [(fact["status"], fact["code"]) for fact in refusals] == [
            ("refused", "target_unknown"),
            ("refused", "operation_not_allowed"),
        ]
        assert h.service.requests == []
    finally:
        await h.close()


@pytest.mark.asyncio
async def test_approval_outcomes_map_like_act() -> None:
    """An approval's request maps as ``act`` does (``platform_rejected``)."""

    service = CompanionModerationService(outcomes=["rejected"])
    h = await moderation_harness(service, **PROPOSE)
    try:
        await h.say("m-1", "viewer-7")
        proposal_id = await proposed(h, "m-1")
        await h.command(f"!modok {proposal_id}", BROADCASTER, roles=["broadcaster"], counts="approval")
        approval = h.facts()[-1]
        assert (approval["status"], approval["code"]) == ("error", "platform_rejected")
        assert len(service.requests) == 1
    finally:
        await h.close()


@pytest.mark.asyncio
async def test_an_approval_whose_request_hangs_is_bounded_and_external_unknown() -> None:
    """An approval is bounded by the action's timeout on the sleeper: a
    request still pending then ends ``external_unknown`` with its one fact."""

    service = BlockingModerationService()
    h = await moderation_harness(service, **PROPOSE)
    try:
        await h.say("m-1", "viewer-7")
        proposal_id = await proposed(h, "m-1")
        await h.command(f"!modok {proposal_id}", BROADCASTER, roles=["broadcaster"], counts="approval")
        assert len(service.requests) == 1 and len(h.facts()) == 1
        h.clock.advance(10.0)
        await settle()
        approval = h.facts()[-1]
        assert (approval["status"], approval["code"]) == (
            "external_unknown",
            "external_effect_unknown",
        )
    finally:
        service.release.set()
        await h.close()


@pytest.mark.asyncio
async def test_an_approval_whose_service_holds_its_cancellation_is_still_bounded() -> None:
    """The approval's timeout bounds its fact: a service that holds on to its
    cancellation does not delay the ``external_unknown`` fact, nor ``close``."""

    service = StubbornModerationService()
    h = await moderation_harness(service, **PROPOSE)
    try:
        await h.say("m-1", "viewer-7")
        proposal_id = await proposed(h, "m-1")
        await h.command(f"!modok {proposal_id}", BROADCASTER, roles=["broadcaster"], counts="approval")
        assert len(service.requests) == 1
        h.clock.advance(10.0)
        await settle()
        approval = h.facts()[-1]
        assert (approval["decision"], approval["status"], approval["code"]) == (
            "approved",
            "external_unknown",
            "external_effect_unknown",
        )
        await asyncio.wait_for(h.close(), 1.0)
        h.assert_one_fact_per_request()
    finally:
        service.release.set()
        await settle()


@pytest.mark.asyncio
async def test_close_during_an_approval_in_flight_still_publishes_its_one_fact() -> None:
    """A cleanup cancellation of an approval whose request left publishes its
    one decision, ``external_unknown``, even when the service holds on."""

    service = StubbornModerationService()
    h = await moderation_harness(service, **PROPOSE)
    try:
        await h.say("m-1", "viewer-7")
        proposal_id = await proposed(h, "m-1")
        await h.command(f"!modok {proposal_id}", BROADCASTER, roles=["broadcaster"], counts="approval")
        assert len(service.requests) == 1
        await asyncio.wait_for(h.close(), 1.0)
        await settle()
        approval = h.facts()[-1]
        assert (approval["decision"], approval["status"]) == ("approved", "external_unknown")
        h.assert_one_fact_per_request()
        assert h.handle.pending("fake", CHANNEL) == ()
    finally:
        service.release.set()
        await settle()


@pytest.mark.asyncio
async def test_an_unusable_or_repeated_id_falls_back_to_a_fresh_one() -> None:
    """Ids stay unique per channel whatever the injected source returns."""

    h = await moderation_harness(mode="propose", _id_source=lambda: "same")
    try:
        await h.say("m-1", "viewer-7")
        await h.say("m-2", "viewer-8")
        first = await proposed(h, "m-1")
        second = await proposed(h, "m-2")
        assert first == "same" and second != first
        assert h.handle.pending("fake", CHANNEL) == (first, second)
    finally:
        await h.close()


@pytest.mark.asyncio
async def test_approval_and_auto_apply_launched_together_send_one_request() -> None:
    """AC26 concurrency (F-P2): with the blocking service and
    ``max_actions_per_window: 1``, an approval of proposal A and an
    ``auto_apply`` request launched together give exactly 1 request and 1
    ``rate_limited`` fact — both go through the one locked
    check-and-reserve."""

    service = BlockingModerationService()
    h = await moderation_harness(
        service,
        mode="propose",
        max_actions_per_window=1,
        act={"operations": ["delete_message", "timeout"]},
        propose={"auto_apply": ["delete_message"]},
    )
    try:
        await h.say("m-1", "viewer-7")
        await h.say("m-2", "viewer-8")
        proposal_a = await proposed(h, "m-1", operation="timeout", duration=60)
        h.approvals += 1
        auto = h.start("m-2")
        approve = asyncio.ensure_future(
            h.say("command-a", BROADCASTER, roles=["broadcaster"], text=f"!modok {proposal_a}")
        )
        await wait_until(lambda: len(h.facts()) == 2)
        await settle()
        assert len(service.requests) == 1
        (refusal,) = [fact for fact in h.facts() if fact["status"] == "refused"]
        assert refusal["code"] == "rate_limited"
        service.release.set()
        await approve
        await auto
        await settle()
        assert len(service.requests) == 1
        outcomes = sorted(
            fact["disposition"] or fact["code"] for fact in h.facts()[1:]
        )
        assert outcomes == ["applied", "rate_limited"]
    finally:
        service.release.set()
        await h.close()


@pytest.mark.asyncio
async def test_two_approvals_of_one_proposal_launched_together_send_one_request() -> None:
    """AC26 concurrency (F-P2): two ``!modok <id>`` for the same proposal
    launched together give 1 request — the proposal leaves the table under
    the channel lock before the application, so the second approval finds
    an unknown id."""

    service = BlockingModerationService()
    h = await moderation_harness(service, **PROPOSE)
    try:
        await h.say("m-1", "viewer-7")
        proposal_id = await proposed(h, "m-1")
        h.approvals += 1
        both = asyncio.ensure_future(
            asyncio.gather(
                h.say("command-a", BROADCASTER, roles=["broadcaster"], text=f"!modok {proposal_id}"),
                h.say("command-b", MODERATOR, roles=["moderator"], text=f"!modok {proposal_id}"),
            )
        )
        await both
        await wait_until(lambda: len(service.requests) == 1)
        await settle()
        assert len(service.requests) == 1
        assert h.handle.ignored_commands["unknown_id"] == 1
        service.release.set()
        await settle()
        assert len(service.requests) == 1
        assert h.facts()[-1]["disposition"] == "applied"
    finally:
        service.release.set()
        await h.close()


@pytest.mark.asyncio
async def test_the_drain_stops_the_sweep_and_waits_for_an_approval_in_flight() -> None:
    """Phase lifecycle: the drain stops the expiry sweep and ignores new
    commands, and gives an approval in flight the drain deadline."""

    service = BlockingModerationService()
    h = await moderation_harness(service, **PROPOSE)
    try:
        await h.say("m-1", "viewer-7")
        await h.say("m-2", "viewer-8")
        first = await proposed(h, "m-1")
        second = await proposed(h, "m-2")
        await h.command(f"!modok {first}", BROADCASTER, roles=["broadcaster"], counts="approval")
        drain = asyncio.ensure_future(h.handle.drain(5.0))
        await settle()
        assert not drain.done()
        await h.command(f"!modok {second}", BROADCASTER, roles=["broadcaster"])
        assert len(service.requests) == 1
        service.release.set()
        await drain
        await settle()
        assert h.facts()[-1]["disposition"] == "applied"
        h.clock.advance(3600.0)
        await settle()
        assert h.handle.pending("fake", CHANNEL) == (second,)
    finally:
        service.release.set()
        await h.close()


# --------------------------------------------------------------------------- #
# AC28 — the per-channel override
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_ac28_the_override_twitch_c2_act_leaves_twitch_c1_in_alert() -> None:
    """AC28: with the per-channel override ``twitch/c2: act`` and no
    ``mode``, a delete on ``twitch/c1`` is ``alerted`` with 0 requests and
    one on ``twitch/c2`` is ``applied`` with 1 request — the mode is
    resolved per request."""

    h = await moderation_harness(platform="twitch", channels={"twitch/c2": {"mode": "act"}})
    try:
        await h.say("m-1", "viewer-7", channel="c1")
        await h.say("m-2", "viewer-8", channel="c2")
        alerted = await h.request("m-1", channel="c1")
        assert alerted.result["disposition"] == "alerted"
        assert h.service.requests == []
        applied = await h.request("m-2", channel="c2")
        assert applied.result["disposition"] == "applied"
        (sent,) = h.service.requests
        assert sent["channel_id"] == "c2"
        assert [(fact["channel_id"], fact["mode"]) for fact in h.facts()] == [
            ("c1", "alert"),
            ("c2", "act"),
        ]
        # c1 stays in alert after c2 acted.
        await h.say("m-3", "viewer-9", channel="c1")
        assert (await h.request("m-3", channel="c1")).result["disposition"] == "alerted"
        assert len(h.service.requests) == 1
    finally:
        await h.close()


@pytest.mark.asyncio
async def test_a_propose_override_proposes_on_its_channel_only() -> None:
    """An override to ``propose`` on one channel proposes there, and its
    approval command is read in that channel only."""

    h = await moderation_harness(mode="act", channels={f"fake/{OTHER_CHANNEL}": {"mode": "propose"}})
    try:
        await h.say("m-1", "viewer-7")
        await h.say("m-2", "viewer-8", channel=OTHER_CHANNEL)
        assert (await h.request("m-1")).result["disposition"] == "applied"
        proposal_id = await proposed(h, "m-2", channel=OTHER_CHANNEL)
        assert h.handle.pending("fake", OTHER_CHANNEL) == (proposal_id,)
        await h.command(f"!modok {proposal_id}", BROADCASTER, roles=["broadcaster"])
        assert len(h.service.requests) == 1
        await h.command(
            f"!modok {proposal_id}",
            BROADCASTER,
            roles=["broadcaster"],
            channel=OTHER_CHANNEL,
            counts="approval",
        )
        assert len(h.service.requests) == 2
        assert h.facts()[-1]["disposition"] == "applied"
    finally:
        await h.close()


# --------------------------------------------------------------------------- #
# Shutdown, the audit and the absence of a permanent removal
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_a_request_during_the_drain_is_cancelled_with_its_fact() -> None:
    """Phase lifecycle: after ``drain`` began a request ends ``cancelled``
    with 0 requests and its fact; a request in flight is waited for up to
    the drain deadline."""

    service = BlockingModerationService()
    h = await moderation_harness(service, mode="act")
    try:
        await h.say("m-1", "viewer-7")
        await h.say("m-2", "viewer-8")
        pending = h.start("m-1")
        await wait_until(lambda: len(service.requests) == 1)
        drain = asyncio.ensure_future(h.handle.drain(5.0))
        await settle()
        observation = await h.request("m-2")
        assert observation.status == "cancelled", observation
        assert len(service.requests) == 1
        assert not drain.done()
        service.release.set()
        await drain
        assert (await pending).result["disposition"] == "applied"
        await settle()
        assert [fact["status"] for fact in h.facts()] == ["cancelled", "success"]
        h.assert_one_fact_per_request()
    finally:
        service.release.set()
        await h.close()


@pytest.mark.asyncio
async def test_the_audit_subscription_covers_the_decision_fact() -> None:
    """R5: the audit records ``moderation.decision`` through its existing
    subscription — its manifest consumes ``**``, which the bus routes the
    new type to."""

    audit = yaml.safe_load((SHIPPED_MODULES / "audit" / "module.yaml").read_text("utf-8"))
    (pattern,) = audit["consumes"]
    bus = EventBus()
    seen: list[str] = []

    async def record(event: Any) -> None:
        seen.append(event["type"])

    bus.subscribe(pattern, record)
    await bus.publish(FACT_MODERATION_DECISION, {"mode": "alert"}, {})
    assert seen == [FACT_MODERATION_DECISION]


def test_no_operation_removes_a_viewer_permanently() -> None:
    """R5: permanent bans are never available — the module and its manifest
    name no such operation, the only operations are a deletion and a
    timeout, and a timeout's duration is bounded to 1..3600 s."""

    for path in (MODULE_SOURCE, MANIFEST_PATH):
        assert re.findall(r"\bban\b|permaban|unban|/bans", path.read_text("utf-8"), re.I) == []
    manifest = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))
    (action,) = manifest["actions"]
    assert action["argument_schema"]["properties"]["operation"]["enum"] == [
        "delete_message",
        "timeout",
    ]
    bounds = manifest["settings_schema"]["properties"]["max_timeout_seconds"]
    assert (bounds["minimum"], bounds["maximum"]) == (1, 3600)
