"""``moderation.request`` through the ``moderation`` module (phase 3 R5; AC24,
AC25, AC27 without Kick, AC28 core cases).

Every call goes through the real executor under a real authorization policy
on a :class:`~conftest.ManualClock`. The moderation service is a
:class:`~conftest.ScriptedModerationService` published by the fixture
platform (``fake``) through its ``moderation_service`` seam, or published
under ``(moderation, twitch)`` beside twitch-shaped chat events, or the twitch
module's own service over a scripted HTTP session. The target messages reach
the module the way a platform delivers them: fed to the chat context, then
published as ``channel.chat.message``. No positive-duration sleep.

Every case also checks the no-silent-outcome rule of R5: the module publishes
exactly one ``moderation.decision`` fact per request that reaches it.

The Kick-named case of AC27 (``delete_message`` on kick →
``platform_unsupported``) runs with the kick module (plan step P19); the
propose mode and the per-channel override of AC28 are plan step P15's.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import re
from collections import deque
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from conftest import (
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

    def facts(self) -> list[dict[str, Any]]:
        return [event["payload"] for event in events_of(self.runtime.bus, FACT_MODERATION_DECISION)]

    def assert_one_fact_per_request(self) -> None:
        """R5, AC28: no silent outcome — one fact per request that reached the module."""

        assert len(self.facts()) == self.calls

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
    handle = await activate_moderation(
        runtime.for_module("moderation"), {"_sleeper": clock.sleep, **settings}, {}
    )
    await handle.prepare()
    if grant:
        runtime.actions._authorization.grant(moderation_rule())
    return ModerationHarness(runtime, handle, service, clock, platform, edge)


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
    finally:
        await h.close()


@pytest.mark.asyncio
async def test_propose_is_refused_until_its_table_exists_with_no_request() -> None:
    """Plan P14/P15: mode ``propose`` is declared now; until P15 adds its
    proposal table a request in it sends 0 requests and still publishes its
    fact. A per-channel override is resolved per request."""

    h = await moderation_harness(mode="propose", channels={f"fake/{OTHER_CHANNEL}": {"mode": "act"}})
    try:
        await h.say("m-1", "viewer-7")
        await h.say("m-2", "viewer-8", channel=OTHER_CHANNEL)
        assert_refused(await h.request("m-1"), "mode_unavailable")
        assert h.service.requests == []
        applied = await h.request("m-2", channel=OTHER_CHANNEL)
        assert applied.result["disposition"] == "applied"
        assert len(h.service.requests) == 1
        assert [fact["mode"] for fact in h.facts()] == ["propose", "act"]
        h.assert_one_fact_per_request()
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
