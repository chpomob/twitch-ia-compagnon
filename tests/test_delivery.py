"""The delivery list contract (R1, decision 1; AC49–AC58).

Delivery is a configured, pluggable terminal step: the brain's ``delivery``
settings group names an ordered list of delivery actions — ``mode: fixed``
with explicit entries, or ``mode: modules`` derived from the enabled modules
whose declarations carry the delivery capability, in ``preference`` order —
resolved and validated at ``prepare`` against the discovered catalog, and
invoked once, in order, when the final answer exists: one executor call per
entry, the answer text passed only where the entry declares it, every entry
invoked whatever the outcome of the previous ones, one explicit outcome per
entry in the run's accounting. The model never selects a delivery action and
is never offered one. The generic fallback (R3, AC55) takes the same list:
``fallback.text`` in place of the answer, one call per entry, each entry
counted against ``max_action_calls`` and skipped by name when none is left.

Every scenario runs the **shipped** ``modules/brain/`` — no monkeypatch, no
subclass — on the versioned runtime the shared fixture builds, with the two
fixture modules of ``tests/fixtures/modules`` loaded by the real
:class:`~core.loader.ModuleLoader` under the same rules as a shipped module:
``fakeplatform`` (platform ``fake``, ``chat.write`` with the delivery
capability, the keyword trigger) and ``fakeeffects`` (``audio.say`` with the
text, ``stream.set_scene`` with no text, ``overlay.raw`` with no delivery
capability). The only brain setting a scenario changes relative to the
single-entry list of the example profiles (AC49) is ``delivery`` (AC56).
Nothing here sleeps: the clock is injected and every wait is a bounded
number of bare loop turns.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from core.actions import (
    ERROR_EXTERNAL_UNKNOWN,
    ERROR_INVALID_ARGUMENTS,
    ERROR_NOT_AUTHORIZED,
    ERROR_PROVIDER_FAILED,
    AuthorizationPolicy,
    AuthorizationRule,
)
from core.admission import REASON_CANCELLED
from core.context import ChatContext
from core.contracts import (
    TRACE_ACTION_STARTED,
    TRACE_BRAIN_RUN_COMPLETED,
    TRACE_MODULE_DEGRADED,
    TRACE_MODULE_READY,
    WILDCARD,
    ActionObservation,
    ActionSpec,
    Destination,
    SessionKey,
)
from core.loader import ModuleLoader
from core.runtime import RuntimeContext
from core.triggers import TriggerRegistry
from conftest import (
    ManualClock,
    ScriptedModel,
    events_of,
    final,
    runtime_context as build_context,
    tool_call,
    wait_until,
)
from core.contracts import RUN_FAILURE_BUDGET_EXHAUSTED
from modules.brain import (
    DELIVERY_NOT_ATTEMPTED,
    FALLBACK_SENT,
    FALLBACK_SKIPPED_BUDGET_EXHAUSTED,
    FALLBACK_SKIPPED_NOT_AUTHORIZED,
    MODULE_NAME,
    PRINCIPAL,
    TRACE_DELIVERY_RESOLVED,
    BrainModuleError,
)
from test_brain import VALID_SETTINGS, copy_settings


ROOT = Path(__file__).parents[1]
FIXTURE_MODULES = ROOT / "tests" / "fixtures" / "modules"
SHIPPED_MODULES = ROOT / "modules"
BRAIN_PACKAGE = SHIPPED_MODULES / "brain"

PLATFORM = "fake"
CHANNEL_A = "chan-a"
CHANNEL_B = "chan-b"
VIEWER = "viewer-1"
COMPANION = "companion"
READER_MODULE = "reader"

# The actions the scenarios configure. The names live here and in the
# fixture manifests — never in ``modules/brain/`` (AC56).
CHAT_WRITE = "chat.write"
CHAT_READ = "chat.read"
AUDIO_SAY = "audio.say"
STREAM_SET_SCENE = "stream.set_scene"
OVERLAY_RAW = "overlay.raw"
NOPE = "nope.action"
DELIVERY_CAPABLE = (CHAT_WRITE, AUDIO_SAY, STREAM_SET_SCENE)
"""The delivery-capable actions of the two fixtures, in catalog order
(``fakeplatform`` is enabled before ``fakeeffects``)."""

# The single-entry ``delivery`` group of both example brain profiles (AC49,
# AC58): the one every other scenario differs from in that group alone.
AC49_DELIVERY = {"mode": "fixed", "actions": [{"action": CHAT_WRITE, "text_argument": "text"}]}
assert VALID_SETTINGS["delivery"] == AC49_DELIVERY

# The AC50 list: the text to the chat, then an effect with no text.
AC50_DELIVERY = {
    "mode": "fixed",
    "actions": [
        {"action": CHAT_WRITE, "text_argument": "text"},
        {"action": STREAM_SET_SCENE, "arguments": {"scene": "answering"}},
    ],
}

T = "Here is my answer."
FALLBACK_TEXT = VALID_SETTINGS["fallback"]["text"]

# The AC13 run under the profile's own budgets (AC56 lets a scenario change
# ``delivery`` alone): ``model_turns`` reads are proposed, each with its own
# ``limit`` so the repeated-action budget is not what stops the run, and the
# ``model_turns + 1``-th request is never built. A proposal whose arguments
# are not a JSON object is answered by the runtime itself (``malformed_
# arguments``) and costs no executor call, which is how the two scripts
# leave a different number of action calls to the terminal step.
MODEL_TURNS = VALID_SETTINGS["budget"]["model_turns"]
MAX_ACTION_CALLS = VALID_SETTINGS["budget"]["max_action_calls"]
assert (MODEL_TURNS, MAX_ACTION_CALLS) == (5, 6)
AC13_ONE_CALL_LEFT = tuple(
    tool_call(CHAT_READ, {"limit": index + 1}) for index in range(MODEL_TURNS)
) + (final("never"),)
"""Every turn a read that is executed: ``MODEL_TURNS`` action calls spent,
exactly one left for the terminal step."""
AC13_TWO_CALLS_LEFT = tuple(
    tool_call(CHAT_READ, {"limit": index + 1}) for index in range(MODEL_TURNS - 1)
) + (tool_call(CHAT_READ, "[1, 2]"), final("never"))
"""The last proposal malformed and refused by the runtime: one action call
fewer spent, two left for the two entries of the AC50 list."""

FORBIDDEN_IN_BRAIN = (CHAT_WRITE, AUDIO_SAY, STREAM_SET_SCENE, "fakeeffects", "twitch")
"""The literals ``grep -rn`` over ``modules/brain/`` must not match (AC56)."""

# A read action in the catalog, declared and bound as a module's manifest
# would: the one tool the model may be offered, never reached by the
# single-turn body; and the one name AC53 needs a catalog ``read`` action for.
CHAT_READ_SPEC = ActionSpec(
    name=CHAT_READ,
    version=1,
    description="Read the last chat messages of the channel.",
    argument_schema={
        "type": "object",
        "properties": {"limit": {"type": "integer", "minimum": 1}},
        "required": [],
        "additionalProperties": False,
    },
    result_schema={"type": "object", "properties": {"messages": {"type": "array"}}},
    nature="read",
    required_permissions=(CHAT_READ,),
    supported_destinations=(Destination(PLATFORM, WILDCARD, "chat"),),
    timeout_seconds=5.0,
    idempotency="none",
)


class NeverInvokedReadProvider:
    name = "fake-read"

    async def invoke(self, invocation: Any) -> ActionObservation:
        raise AssertionError("the single-turn body executes no proposal")


class AnsweringReadProvider:
    """A ``chat.read`` provider confirming every call with one text part.

    Bound in place of :class:`NeverInvokedReadProvider` by the scenarios
    whose model proposes reads until a budget is spent (AC55): each call is
    recorded, and none of them is a delivery.
    """

    name = "fake-read"

    def __init__(self) -> None:
        self.calls: list[Any] = []

    async def invoke(self, invocation: Any) -> ActionObservation:
        self.calls.append(invocation.call)
        invocation.mark_not_emitted()
        return ActionObservation(
            status="success",
            provenance={"provider": self.name},
            result={"messages": []},
            parts=[{"type": "text", "text": "no messages"}],
        )


def grant_rule(action: str) -> AuthorizationRule:
    """One explicit grant of *action* to the brain's principal (R5)."""

    return AuthorizationRule(
        rule_id=f"brain-{action}",
        action_name=action,
        principals=(PRINCIPAL,),
        granted_permissions=(action,),
    )


DEFAULT_GRANTS = (CHAT_WRITE, CHAT_READ, AUDIO_SAY, STREAM_SET_SCENE)


# --------------------------------------------------------------------------- #
# The harness: two fixtures and the shipped brain, loaded by the real loader
# --------------------------------------------------------------------------- #


@dataclass
class DeliveryHarness:
    """The activated modules and every edge a scenario reads."""

    context: RuntimeContext
    clock: ManualClock
    brain: Any
    platform: Any | None
    effects: Any | None
    session: ScriptedModel
    transport: Any
    diagnostics: list[str]
    settings: dict[str, Any]
    handles: dict[str, Any] = field(default_factory=dict)

    @property
    def bus(self) -> Any:
        return self.context.bus

    @property
    def executor(self) -> Any:
        return self.context.executor

    async def ask(
        self,
        text: str = f"{COMPANION}, hello",
        *,
        channel_id: str = CHANNEL_A,
        message_id: str = "m-1",
        viewer_id: str = VIEWER,
    ) -> Any:
        """Drive one viewer message through the fake platform; the run's record."""

        assert self.platform is not None, "no platform enabled"
        before = len(self.brain.scheduler.run_records())
        published = await self.platform.inject(
            {
                "channel_id": channel_id,
                "author": {"id": viewer_id, "display_name": "Viewer"},
                "message_id": message_id,
                "text": text,
            }
        )
        assert published is not None
        await wait_until(lambda: len(self.brain.scheduler.run_records()) > before)
        await wait_until(
            lambda: len(events_of(self.bus, TRACE_BRAIN_RUN_COMPLETED)) > before
        )
        return list(self.brain.scheduler.run_records().values())[-1]

    def completed(self) -> dict[str, Any]:
        """The payload of the last ``brain.run.completed``."""

        return events_of(self.bus, TRACE_BRAIN_RUN_COMPLETED)[-1]["payload"]

    def deliveries(self) -> list[dict[str, Any]]:
        return [dict(entry) for entry in self.completed()["deliveries"]]

    def resolved(self) -> dict[str, Any]:
        """The one ``brain.delivery.resolved`` trace's payload."""

        (trace,) = events_of(self.bus, TRACE_DELIVERY_RESOLVED)
        return trace["payload"]

    def executor_calls(self) -> list[tuple[str, str]]:
        """Every executor call as ``(action, call_id)``, in invocation order."""

        return [
            (event["payload"]["action"], event["payload"]["call_id"])
            for event in events_of(self.bus, TRACE_ACTION_STARTED)
        ]

    def statuses(self) -> list[str]:
        return [observation.status for observation in self.executor.outcomes().values()]

    def effect_calls(self, action: str) -> list[dict[str, Any]]:
        assert self.effects is not None, "no effects enabled"
        return list(self.effects.calls[action])

    def tools(self) -> list[list[str]]:
        """The tool names of every scenario request, in request order."""

        return [
            [tool["function"]["name"] for tool in body.get("tools", [])]
            for body in self.session.requests()
        ]

    def memory(self, channel_id: str = CHANNEL_A, viewer_id: str = VIEWER) -> list[str]:
        key = SessionKey(platform=PLATFORM, channel_id=channel_id, viewer_id=viewer_id)
        return [exchange.assistant for exchange in self.brain.memory.recall(key)]

    def ready(self) -> bool:
        return bool(self.context.actions.is_ready(MODULE_NAME))

    async def close(self) -> None:
        await self.brain.close()
        for handle in self.handles.values():
            await handle.close()


async def activate_delivery(
    *bodies: Any,
    delivery: Mapping[str, Any] = AC49_DELIVERY,
    effects: bool = True,
    platform: bool = True,
    grants: Sequence[str] = DEFAULT_GRANTS,
    scripts: dict[str, list[str]] | None = None,
    outcomes: Sequence[str] = (),
    prepare: bool = True,
    reader: Any | None = None,
) -> DeliveryHarness:
    """Load the fixtures and the shipped brain on one runtime; prepare them.

    Declarations are recorded by the loader before any module prepares, so
    the brain resolves its lists against the whole enabled catalog whatever
    the ``prepare`` order; the fixtures are prepared first so their providers
    are bound and ready when the first run delivers. The brain's settings are
    the AC49 settings with only ``delivery`` replaced (AC56). ``scripts``
    scripts the effect providers, ``outcomes`` the fake chat transport;
    ``reader`` replaces the never-invoked ``chat.read`` provider for a
    scenario whose model proposes reads.
    """

    clock = ManualClock()
    policy = AuthorizationPolicy([grant_rule(action) for action in grants])
    context = build_context(
        clock=clock,
        trigger_registry=TriggerRegistry(companion_name=COMPANION),
        chat=ChatContext(max_messages=16, max_bytes=8192, max_age_seconds=600.0, clock=clock),
        authorization=policy,
    )
    context.actions.register(
        CHAT_READ_SPEC,
        reader if reader is not None else NeverInvokedReadProvider(),
        module=READER_MODULE,
    )
    context.actions.mark_ready(READER_MODULE)

    diagnostics: list[str] = []
    transport = SimpleNamespace(outcomes=list(outcomes), sends=[])
    enabled = (["fakeplatform"] if platform else []) + (["fakeeffects"] if effects else [])
    fixtures = ModuleLoader(context.bus, FIXTURE_MODULES, context=context, environ={})
    activations = await fixtures.activate_enabled(
        {
            "enabled_modules": enabled,
            "modules": {
                "fakeplatform": {
                    "channel_ids": [CHANNEL_A, CHANNEL_B],
                    "companion_name": COMPANION,
                    "transport": transport,
                    "diagnostic_reporter": diagnostics.append,
                },
                "fakeeffects": {"scripts": scripts if scripts is not None else {}},
            },
        }
    )
    handles = {activation.name: activation.handle for activation in activations}

    session = ScriptedModel(*bodies)
    settings = copy_settings(VALID_SETTINGS)
    settings["delivery"] = copy.deepcopy(dict(delivery))
    assert {key for key in settings if settings[key] != VALID_SETTINGS[key]} <= {"delivery"}
    settings.update(
        {
            "_session_factory": lambda: session,
            "_sleeper": clock.sleep,
            "diagnostic_reporter": diagnostics.append,
        }
    )
    brain_loader = ModuleLoader(context.bus, SHIPPED_MODULES, context=context, environ={})
    (brain_activation,) = await brain_loader.activate_enabled(
        {"enabled_modules": ["brain"], "modules": {"brain": settings}}
    )
    harness = DeliveryHarness(
        context=context,
        clock=clock,
        brain=brain_activation.handle,
        platform=handles.get("fakeplatform"),
        effects=handles.get("fakeeffects"),
        session=session,
        transport=transport,
        diagnostics=diagnostics,
        settings=settings,
        handles=handles,
    )
    if prepare:
        for handle in handles.values():
            await handle.prepare()
        await harness.brain.prepare()
    return harness


async def prepare_fails(harness: DeliveryHarness) -> str:
    """Prepare the fixtures, then the brain, which must refuse; its diagnostic.

    The loader imports the brain package under a private module name, so the
    refusal is the ``BrainModuleError`` of *that* import — read from the
    handle's own module, the way the loader suite reaches a fixture's.
    """

    for handle in harness.handles.values():
        await handle.prepare()
    brain_module = sys.modules[type(harness.brain).__module__]
    assert brain_module.BrainModuleError.__name__ == BrainModuleError.__name__
    with pytest.raises(brain_module.BrainModuleError) as raised:
        await harness.brain.prepare()
    return str(raised.value)


def diagnostic(path: str, reason: str) -> str:
    return f"module '{MODULE_NAME}': delivery: {path}: {reason}"


def delivery_entry(action: str, call_id: str, text: bool, status: str) -> dict[str, Any]:
    return {"action": action, "call_id": call_id, "text": text, "status": status}


def tree_digest(*roots: Path) -> dict[str, str]:
    """A content digest of every source file under *roots*; caches excluded."""

    digests: dict[str, str] = {}
    for root in roots:
        for path in sorted(root.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                digests[str(path.relative_to(ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return digests


# --------------------------------------------------------------------------- #
# AC49 — the single-entry list of the example profiles is today's behaviour
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "entry",
    [
        {"action": CHAT_WRITE, "text_argument": "text"},
        {"action": CHAT_WRITE},
    ],
    ids=["explicit-mapping", "inherited-mapping"],
)
async def test_ac49_single_chat_write_entry_delivers_exactly_as_before(entry: dict) -> None:
    """AC49: the example profiles' list — and the same entry without
    ``text_argument``, inheriting the declaration's mapping — yields 1 model
    call, 1 executor call ``<run_id>/call-1`` carrying the text, 1 send, 1
    memory write, ``delivery == "success"`` and ``deliveries`` of exactly
    one entry with ``text: true``."""

    harness = await activate_delivery(
        final(T), delivery={"mode": "fixed", "actions": [entry]}
    )
    try:
        assert harness.resolved() == {"default": [CHAT_WRITE], "overrides": {}, "ignored": []}
        record = await harness.ask()

        assert (record.status, record.delivery, record.model_calls, record.sends) == (
            "success", "success", 1, 1
        )
        assert harness.executor_calls() == [(CHAT_WRITE, f"{record.run_id}/call-1")]
        assert harness.executor.provider_invocations == 1
        (send,) = harness.platform.sends
        assert send["text"] == T
        assert send["call_id"] == f"{record.run_id}/call-1"
        assert send["destination"] == Destination(PLATFORM, CHANNEL_A, "chat")
        assert send["principal"] == PRINCIPAL
        assert harness.deliveries() == [
            delivery_entry(CHAT_WRITE, f"{record.run_id}/call-1", True, "success")
        ]
        completed = harness.completed()
        assert completed["delivery"] == "success"
        assert completed["action_calls"] == 1
        assert harness.memory() == [T]
        assert harness.tools() == [[CHAT_READ]]
        assert harness.diagnostics == []
    finally:
        await harness.close()


# --------------------------------------------------------------------------- #
# AC50, AC51 — fixed lists beyond one entry: text + effect, text + text
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
@pytest.mark.parametrize("reverse", [False, True], ids=["chat-then-scene", "scene-then-chat"])
async def test_ac50_text_entry_and_effect_entry_are_invoked_in_list_order(reverse: bool) -> None:
    """AC50: the chat entry receives ``{"text": T}`` and the scene entry
    exactly ``{"scene": "answering"}`` (T under no key); 0 executor calls
    before the final response, call ids in list order, 1 send, the scene
    provider invoked once, ``deliveries`` ``text`` ``[true, false]``, both
    ``success``, ``delivery == "success"``, ``action_calls: 2``, memory
    written once with T. The reverse order yields the reverse call ids."""

    actions = list(AC50_DELIVERY["actions"])
    if reverse:
        actions.reverse()
    harness = await activate_delivery(final(T), delivery={"mode": "fixed", "actions": actions})
    try:
        assert harness.executor_calls() == []
        record = await harness.ask()
        run = record.run_id

        expected_order = [STREAM_SET_SCENE, CHAT_WRITE] if reverse else [CHAT_WRITE, STREAM_SET_SCENE]
        assert harness.executor_calls() == [
            (expected_order[0], f"{run}/call-1"),
            (expected_order[1], f"{run}/call-2"),
        ]
        assert harness.executor.provider_invocations == 2
        assert len(harness.session.requests()) == 1
        (send,) = harness.platform.sends
        assert send["text"] == T
        (scene,) = harness.effect_calls(STREAM_SET_SCENE)
        assert scene["arguments"] == {"scene": "answering"}
        assert T not in str(scene["arguments"])
        assert scene["destination"] == Destination(PLATFORM, CHANNEL_A, "stream")
        assert scene["principal"] == PRINCIPAL
        assert scene["run_id"] == run
        assert harness.effect_calls(AUDIO_SAY) == []

        chat_call = f"{run}/call-{2 if reverse else 1}"
        scene_call = f"{run}/call-{1 if reverse else 2}"
        assert send["call_id"] == chat_call
        assert scene["call_id"] == scene_call
        expected = [
            delivery_entry(CHAT_WRITE, chat_call, True, "success"),
            delivery_entry(STREAM_SET_SCENE, scene_call, False, "success"),
        ]
        if reverse:
            expected.reverse()
        assert harness.deliveries() == expected
        completed = harness.completed()
        assert (completed["delivery"], completed["action_calls"], completed["sends"]) == (
            "success", 2, 1
        )
        assert (record.status, record.delivery, record.sends) == ("success", "success", 1)
        assert harness.memory() == [T]
        assert harness.diagnostics == []
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_ac51_two_text_entries_both_receive_the_text_and_neither_is_a_tool() -> None:
    """AC51: ``chat.write`` then ``audio.say``, both with ``text_argument:
    text`` — the chat send and the audio provider's ``text`` both equal T,
    ``deliveries`` reports ``text`` ``[true, true]``, and every model request
    lists as tools exactly the authorized read actions: neither delivery
    action is offered although both are granted (R1, decision 1)."""

    harness = await activate_delivery(
        final(T),
        delivery={
            "mode": "fixed",
            "actions": [
                {"action": CHAT_WRITE, "text_argument": "text"},
                {"action": AUDIO_SAY, "text_argument": "text"},
            ],
        },
    )
    try:
        record = await harness.ask()
        run = record.run_id

        (send,) = harness.platform.sends
        assert send["text"] == T
        (said,) = harness.effect_calls(AUDIO_SAY)
        assert said["arguments"] == {"text": T}
        assert said["destination"] == Destination(PLATFORM, CHANNEL_A, "audio")
        assert harness.deliveries() == [
            delivery_entry(CHAT_WRITE, f"{run}/call-1", True, "success"),
            delivery_entry(AUDIO_SAY, f"{run}/call-2", True, "success"),
        ]
        assert (record.status, record.delivery, record.sends) == ("success", "success", 2)
        assert harness.memory() == [T]

        assert harness.tools() == [[CHAT_READ]]
        for tools in harness.tools():
            assert CHAT_WRITE not in tools
            assert AUDIO_SAY not in tools
        authorized = harness.context.actions.authorized(
            principal=PRINCIPAL, destination=Destination(PLATFORM, CHANNEL_A, "chat")
        )
        assert CHAT_WRITE in authorized  # granted and bound, yet never offered
        for body in harness.session.requests():
            assert "[send:" not in body["messages"][0]["content"]
        assert harness.diagnostics == []
    finally:
        await harness.close()


# --------------------------------------------------------------------------- #
# AC52 — mode `modules`: derived from the enabled modules, preference first
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_ac52_modules_mode_derives_the_list_in_preference_then_catalog_order() -> None:
    """AC52: with ``preference: [audio.say, chat.write]`` the resolved trace
    lists ``[audio.say, chat.write, stream.set_scene]`` — ``overlay.raw``
    absent (no delivery capability), ``chat.read`` absent (a read action) —
    and a final response yields 3 executor calls in that order, T passed to
    the first two and to no argument of the third. A derived entry carries
    no constant, so the scene call has no ``scene``: the executor refuses
    its arguments (an explicit ``error``, the provider never entered) and
    the text entries' outcomes stand on their own."""

    harness = await activate_delivery(
        final(T), delivery={"mode": "modules", "preference": [AUDIO_SAY, CHAT_WRITE]}
    )
    try:
        assert harness.resolved() == {
            "default": [AUDIO_SAY, CHAT_WRITE, STREAM_SET_SCENE],
            "overrides": {},
            "ignored": [],
        }
        assert OVERLAY_RAW in harness.context.actions.discovered()
        record = await harness.ask()
        run = record.run_id

        assert harness.executor_calls() == [
            (AUDIO_SAY, f"{run}/call-1"),
            (CHAT_WRITE, f"{run}/call-2"),
            (STREAM_SET_SCENE, f"{run}/call-3"),
        ]
        (said,) = harness.effect_calls(AUDIO_SAY)
        assert said["arguments"] == {"text": T}
        (send,) = harness.platform.sends
        assert send["text"] == T
        scene_call = harness.executor.outcome(f"{run}/call-3")
        assert (scene_call.status, scene_call.error["code"]) == ("error", ERROR_INVALID_ARGUMENTS)
        assert harness.effect_calls(STREAM_SET_SCENE) == []
        assert harness.effect_calls(OVERLAY_RAW) == []
        assert harness.executor.provider_invocations == 2
        assert harness.deliveries() == [
            delivery_entry(AUDIO_SAY, f"{run}/call-1", True, "success"),
            delivery_entry(CHAT_WRITE, f"{run}/call-2", True, "success"),
            delivery_entry(STREAM_SET_SCENE, f"{run}/call-3", False, "error"),
        ]
        assert (record.status, record.delivery, record.sends) == ("error", "error", 2)
        assert harness.completed()["action_calls"] == 3
        assert harness.memory() == [T]
        assert harness.tools() == [[CHAT_READ]]
        assert harness.diagnostics == []
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_ac52_a_preference_reorders_and_an_unknown_preference_is_ignored() -> None:
    """AC52: ``preference: [stream.set_scene]`` puts the scene first and the
    other two after it in catalog order; ``[audio.say, chat.write,
    nope.action]`` leaves the list of the previous scenario unchanged and
    lists ``nope.action`` as ignored in the trace."""

    scene_first = await activate_delivery(
        final(T), delivery={"mode": "modules", "preference": [STREAM_SET_SCENE]}
    )
    try:
        assert scene_first.resolved()["default"] == [STREAM_SET_SCENE, CHAT_WRITE, AUDIO_SAY]
        assert scene_first.resolved()["ignored"] == []
        record = await scene_first.ask()
        assert [action for action, _call in scene_first.executor_calls()] == [
            STREAM_SET_SCENE, CHAT_WRITE, AUDIO_SAY
        ]
        assert [entry["status"] for entry in scene_first.deliveries()] == [
            "error", "success", "success"
        ]
        assert record.delivery == "error"
        assert scene_first.memory() == [T]
    finally:
        await scene_first.close()

    with_unknown = await activate_delivery(
        final(T),
        delivery={"mode": "modules", "preference": [AUDIO_SAY, CHAT_WRITE, NOPE]},
    )
    try:
        assert with_unknown.resolved() == {
            "default": [AUDIO_SAY, CHAT_WRITE, STREAM_SET_SCENE],
            "overrides": {},
            "ignored": [NOPE],
        }
        assert with_unknown.ready()
        assert with_unknown.diagnostics == []
    finally:
        await with_unknown.close()


@pytest.mark.asyncio
async def test_ac52_ac56_enabling_the_delivery_module_changes_the_list_and_no_file() -> None:
    """AC52/AC56: the same ``mode: modules`` settings resolve to
    ``[chat.write]`` with the fixture delivery module disabled — and the run
    matches AC49 — and to the 3-entry list with it enabled, with 0 files
    changed under ``core/`` and ``modules/`` between the two."""

    before = tree_digest(ROOT / "core", SHIPPED_MODULES)
    settings = {"mode": "modules", "preference": [AUDIO_SAY, CHAT_WRITE]}

    disabled = await activate_delivery(final(T), delivery=settings, effects=False)
    try:
        assert set(disabled.context.actions.discovered()) == {CHAT_WRITE, CHAT_READ}
        assert disabled.resolved() == {"default": [CHAT_WRITE], "overrides": {}, "ignored": [AUDIO_SAY]}
        record = await disabled.ask()
        assert disabled.executor_calls() == [(CHAT_WRITE, f"{record.run_id}/call-1")]
        assert disabled.deliveries() == [
            delivery_entry(CHAT_WRITE, f"{record.run_id}/call-1", True, "success")
        ]
        (send,) = disabled.platform.sends
        assert send["text"] == T
        assert (record.status, record.delivery, record.sends) == ("success", "success", 1)
        assert disabled.memory() == [T]
    finally:
        await disabled.close()

    enabled = await activate_delivery(final(T), delivery=settings, effects=True)
    try:
        assert enabled.resolved()["default"] == [AUDIO_SAY, CHAT_WRITE, STREAM_SET_SCENE]
        assert enabled.resolved()["ignored"] == []
    finally:
        await enabled.close()

    assert tree_digest(ROOT / "core", SHIPPED_MODULES) == before


# --------------------------------------------------------------------------- #
# AC53 — resolution failures stop `prepare` before the barrier
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("actions", "path", "reason"),
    [
        ([{"action": NOPE}], "delivery.actions[0]", "unknown_action"),
        ([{"action": CHAT_READ, "text_argument": "text"}], "delivery.actions[0]", "not_a_delivery"),
        ([{"action": OVERLAY_RAW, "text_argument": "text"}], "delivery.actions[0]", "not_a_delivery"),
        (
            [{"action": STREAM_SET_SCENE, "text_argument": "text"}],
            "delivery.actions[0]",
            "text_mapping_missing",
        ),
        ([{"action": CHAT_WRITE, "text_argument": "body"}], "delivery.actions[0]", "text_mapping_missing"),
        (
            [{"action": CHAT_WRITE, "text_argument": "text", "arguments": {"text": "x"}}],
            "delivery.actions[0]",
            "text_mapping_ambiguous",
        ),
        ([], "delivery", "empty"),
        # Beyond the seven of AC53: ``none`` against a declaration that
        # expects the text is a mapping that differs from the declared one.
        ([{"action": CHAT_WRITE, "text_argument": "none"}], "delivery.actions[0]", "text_mapping_ambiguous"),
    ],
    ids=[
        "unknown_action",
        "read_action",
        "no_delivery_capability",
        "text_to_an_effect",
        "argument_absent",
        "constant_under_text_argument",
        "empty",
        "none_against_a_text_declaration",
    ],
)
async def test_ac53_each_unresolvable_fixed_list_fails_prepare_before_the_barrier(
    actions: list, path: str, reason: str
) -> None:
    """AC53: each fixed list fails ``prepare`` with one diagnostic naming the
    entry (or the list) and the reason; 0 scenario requests, 0 probes, 0
    sends, the brain not ready and reported ``degraded`` with the same
    value-free diagnostic. The shape hook accepted every one of them: what
    fails is the catalog's answer, at ``prepare``."""

    harness = await activate_delivery(
        final(T), delivery={"mode": "fixed", "actions": actions}, prepare=False
    )
    try:
        message = await prepare_fails(harness)

        assert message == diagnostic(path, reason)
        assert harness.session.requests() == []
        assert harness.session.probe_calls == []
        assert harness.platform.sends == []
        assert harness.executor.provider_invocations == 0
        assert not harness.ready()
        assert events_of(harness.bus, TRACE_DELIVERY_RESOLVED) == []
        (degraded,) = events_of(harness.bus, TRACE_MODULE_DEGRADED)
        assert degraded["payload"]["module"] == MODULE_NAME
        assert degraded["payload"]["reason"] == message
        assert [
            event["payload"]["module"] for event in events_of(harness.bus, TRACE_MODULE_READY)
        ].count(MODULE_NAME) == 0
        assert harness.diagnostics == []
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_ac53_modules_mode_with_no_delivery_capable_action_is_empty() -> None:
    """AC53: ``mode: modules`` over a catalog whose only action is a read one
    fails ``prepare`` with reason ``empty`` naming ``delivery``; nothing is
    requested, nothing is sent, the brain is not ready."""

    harness = await activate_delivery(
        final(T),
        delivery={"mode": "modules", "preference": [CHAT_WRITE]},
        platform=False,
        effects=False,
        prepare=False,
    )
    try:
        assert set(harness.context.actions.discovered()) == {CHAT_READ}
        message = await prepare_fails(harness)

        assert message == diagnostic("delivery", "empty")
        assert harness.session.requests() == []
        assert harness.session.probe_calls == []
        assert harness.executor.provider_invocations == 0
        assert not harness.ready()
        (degraded,) = events_of(harness.bus, TRACE_MODULE_DEGRADED)
        assert degraded["payload"]["reason"] == message
    finally:
        await harness.close()


# --------------------------------------------------------------------------- #
# AC54 — one explicit outcome per entry, no false confirmed send
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_ac54_an_uncertain_chat_entry_is_external_unknown_and_never_memorised() -> None:
    """AC54: with the AC50 list and the chat transport scripted
    ``FAIL_AFTER_EMISSION``, ``deliveries[0].status == "external_unknown"``,
    ``deliveries[1].status == "success"`` with the scene provider invoked
    exactly once, ``delivery == "external_unknown"`` and memory unchanged
    (AC7): the scene entry ran whatever the chat entry's outcome."""

    harness = await activate_delivery(
        final(T), delivery=AC50_DELIVERY, outcomes=["FAIL_AFTER_EMISSION"]
    )
    try:
        record = await harness.ask()
        run = record.run_id

        assert [entry["status"] for entry in harness.deliveries()] == [
            "external_unknown", "success"
        ]
        assert harness.deliveries() == [
            delivery_entry(CHAT_WRITE, f"{run}/call-1", True, "external_unknown"),
            delivery_entry(STREAM_SET_SCENE, f"{run}/call-2", False, "success"),
        ]
        assert (record.status, record.delivery, record.sends) == (
            "external_unknown", "external_unknown", 0
        )
        completed = harness.completed()
        assert completed["delivery"] == "external_unknown"
        assert completed["delivery_error"] == ERROR_EXTERNAL_UNKNOWN
        assert completed["action_calls"] == 2
        assert harness.platform.sends == []
        assert len(harness.effect_calls(STREAM_SET_SCENE)) == 1
        assert harness.executor.provider_invocations == 2
        assert harness.memory() == []
        assert harness.diagnostics == []
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_ac54_a_raising_scene_provider_is_an_error_entry_after_a_confirmed_send() -> None:
    """AC54: with the chat send succeeding and the scene provider raising,
    ``deliveries`` statuses are ``["success", "error"]``, ``delivery ==
    "error"`` and memory is written once with T — the text was confirmed."""

    harness = await activate_delivery(
        final(T), delivery=AC50_DELIVERY, scripts={STREAM_SET_SCENE: ["raise"]}
    )
    try:
        record = await harness.ask()
        run = record.run_id

        assert [entry["status"] for entry in harness.deliveries()] == ["success", "error"]
        assert harness.deliveries()[1] == delivery_entry(
            STREAM_SET_SCENE, f"{run}/call-2", False, "error"
        )
        assert (record.status, record.delivery, record.sends) == ("error", "error", 1)
        completed = harness.completed()
        assert completed["delivery"] == "error"
        assert completed["delivery_error"] == ERROR_PROVIDER_FAILED
        (send,) = harness.platform.sends
        assert send["text"] == T
        assert len(harness.effect_calls(STREAM_SET_SCENE)) == 1
        assert harness.memory() == [T]
        assert harness.diagnostics == []
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_ac54_a_scene_entry_with_no_applicable_rule_is_refused_and_the_send_is_still_made() -> None:
    """AC54: with no rule granting ``stream.set_scene``, its entry is
    ``refused`` (default deny is the executor's), its provider is invoked 0
    times, and the chat send is still made; ``delivery == "refused"``."""

    harness = await activate_delivery(
        final(T), delivery=AC50_DELIVERY, grants=(CHAT_WRITE, CHAT_READ, AUDIO_SAY)
    )
    try:
        record = await harness.ask()
        run = record.run_id

        assert harness.deliveries() == [
            delivery_entry(CHAT_WRITE, f"{run}/call-1", True, "success"),
            delivery_entry(STREAM_SET_SCENE, f"{run}/call-2", False, "refused"),
        ]
        assert (record.status, record.delivery, record.sends) == ("refused", "refused", 1)
        assert harness.completed()["delivery_error"] == ERROR_NOT_AUTHORIZED
        (send,) = harness.platform.sends
        assert send["text"] == T
        assert harness.effect_calls(STREAM_SET_SCENE) == []
        assert harness.executor.provider_invocations == 1
        assert harness.memory() == [T]
        assert harness.diagnostics == []
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_a_refused_text_entry_leaves_memory_untouched_while_the_effect_still_runs() -> None:
    """R1/AC7: memory is written only when every entry that receives the
    text ended ``success`` — a refused chat entry with a successful scene
    entry writes nothing, and the scene entry still ran."""

    harness = await activate_delivery(
        final(T), delivery=AC50_DELIVERY, grants=(CHAT_READ, STREAM_SET_SCENE)
    )
    try:
        record = await harness.ask()

        assert [entry["status"] for entry in harness.deliveries()] == ["refused", "success"]
        assert (record.status, record.delivery, record.sends) == ("refused", "refused", 0)
        assert harness.platform.sends == []
        assert len(harness.effect_calls(STREAM_SET_SCENE)) == 1
        assert harness.memory() == []
    finally:
        await harness.close()


# --------------------------------------------------------------------------- #
# AC55 — the generic fallback takes the same list (R3)
# --------------------------------------------------------------------------- #


def assert_ac13_exhausted(harness: DeliveryHarness, record: Any, reads: int) -> dict[str, Any]:
    """The AC13 shape under the profile's budgets: ``model_turns`` requests,
    *reads* executed, the run ``error`` / ``budget_exhausted`` / ``model_turns``."""

    assert record.status == "error"
    assert len(harness.session.requests()) == MODEL_TURNS
    assert harness.tools() == [[CHAT_READ]] * MODEL_TURNS
    completed = harness.completed()
    assert completed["failure"] == RUN_FAILURE_BUDGET_EXHAUSTED
    assert completed["budget"] == "model_turns"
    assert completed["turns"] == MODEL_TURNS
    read_calls = [call for call in harness.executor_calls() if call[0] == CHAT_READ]
    assert read_calls == [(CHAT_READ, f"{record.run_id}/call-{n}") for n in range(1, reads + 1)]
    assert harness.memory() == []
    return completed


@pytest.mark.asyncio
async def test_ac55_the_fallback_goes_through_both_entries_of_the_list() -> None:
    """AC55 (R3): the AC13 run with the AC50 list delivers ``fallback.text``
    by exactly 1 chat send and invokes the scene provider exactly once with
    ``{"scene": "answering"}``; ``deliveries`` has 2 entries both
    ``success``, ``fallback == "sent"``, ``delivery == "fallback:success"``,
    the call ids continue the run's counter, and the run's status stays
    ``error``."""

    reader = AnsweringReadProvider()
    harness = await activate_delivery(*AC13_TWO_CALLS_LEFT, delivery=AC50_DELIVERY, reader=reader)
    try:
        record = await harness.ask()
        run = record.run_id
        reads = MODEL_TURNS - 1
        completed = assert_ac13_exhausted(harness, record, reads)
        assert len(reader.calls) == reads

        chat_call = f"{run}/call-{reads + 1}"
        scene_call = f"{run}/call-{reads + 2}"
        assert harness.deliveries() == [
            delivery_entry(CHAT_WRITE, chat_call, True, "success"),
            delivery_entry(STREAM_SET_SCENE, scene_call, False, "success"),
        ]
        assert completed["fallback"] == FALLBACK_SENT
        assert completed["delivery"] == "fallback:success"
        assert completed["action_calls"] == reads + 2
        assert (record.status, record.delivery, record.sends) == ("error", "fallback:success", 1)
        (send,) = harness.platform.sends
        assert send["text"] == FALLBACK_TEXT
        assert send["call_id"] == chat_call
        assert send["principal"] == PRINCIPAL
        assert send["destination"] == Destination(PLATFORM, CHANNEL_A, "chat")
        (scene,) = harness.effect_calls(STREAM_SET_SCENE)
        assert scene["arguments"] == {"scene": "answering"}
        assert FALLBACK_TEXT not in str(scene["arguments"])
        assert scene["call_id"] == scene_call
        assert scene["principal"] == PRINCIPAL
        assert harness.effect_calls(AUDIO_SAY) == []
        assert harness.executor.provider_invocations == reads + 2
        # The fallback is the configured text, never anything the model said.
        assert "never" not in [send["text"] for send in harness.platform.sends]
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_ac55_one_action_call_left_invokes_the_first_entry_and_skips_the_second_by_name() -> None:
    """AC55 (R3): with ``max_action_calls`` leaving exactly 1 call at the
    terminal step, ``deliveries`` statuses are ``["success",
    "skipped:budget_exhausted"]`` — the chat send made, the scene provider
    invoked 0 times, the skipped entry with no call id — and ``delivery ==
    "fallback:skipped:budget_exhausted"`` with ``fallback == "sent"``."""

    reader = AnsweringReadProvider()
    harness = await activate_delivery(*AC13_ONE_CALL_LEFT, delivery=AC50_DELIVERY, reader=reader)
    try:
        record = await harness.ask()
        run = record.run_id
        completed = assert_ac13_exhausted(harness, record, MODEL_TURNS)
        assert MAX_ACTION_CALLS - MODEL_TURNS == 1

        chat_call = f"{run}/call-{MODEL_TURNS + 1}"
        assert [entry["status"] for entry in harness.deliveries()] == [
            "success", FALLBACK_SKIPPED_BUDGET_EXHAUSTED
        ]
        assert harness.deliveries() == [
            delivery_entry(CHAT_WRITE, chat_call, True, "success"),
            delivery_entry(STREAM_SET_SCENE, None, False, FALLBACK_SKIPPED_BUDGET_EXHAUSTED),
        ]
        assert completed["fallback"] == FALLBACK_SENT
        assert completed["delivery"] == f"fallback:{FALLBACK_SKIPPED_BUDGET_EXHAUSTED}"
        assert "delivery_error" not in completed
        assert completed["action_calls"] == MAX_ACTION_CALLS
        assert record.delivery == f"fallback:{FALLBACK_SKIPPED_BUDGET_EXHAUSTED}"
        assert record.sends == 1
        (send,) = harness.platform.sends
        assert send["text"] == FALLBACK_TEXT
        assert send["call_id"] == chat_call
        assert harness.effect_calls(STREAM_SET_SCENE) == []
        assert harness.executor.provider_invocations == MAX_ACTION_CALLS
        assert [call for call in harness.executor_calls() if call[0] == STREAM_SET_SCENE] == []
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_ac55_no_applicable_rule_for_either_entry_costs_no_executor_call() -> None:
    """AC55 (R3): with no applicable rule for either entry, 0 sends, 0
    delivery provider invocations, 0 executor calls for the fallback and
    ``fallback == "skipped:not_authorized"`` — each entry evaluated on the
    authorized view of its own scope, the read calls untouched."""

    reader = AnsweringReadProvider()
    harness = await activate_delivery(
        *AC13_TWO_CALLS_LEFT,
        delivery=AC50_DELIVERY,
        grants=(CHAT_READ, AUDIO_SAY),
        reader=reader,
    )
    try:
        record = await harness.ask()
        reads = MODEL_TURNS - 1
        completed = assert_ac13_exhausted(harness, record, reads)

        assert completed["fallback"] == FALLBACK_SKIPPED_NOT_AUTHORIZED
        assert completed["deliveries"] == []
        assert completed["delivery"] == DELIVERY_NOT_ATTEMPTED
        assert completed["action_calls"] == reads
        assert (record.status, record.delivery, record.sends) == ("error", DELIVERY_NOT_ATTEMPTED, 0)
        assert harness.platform.sends == []
        assert harness.effect_calls(STREAM_SET_SCENE) == []
        assert harness.effect_calls(AUDIO_SAY) == []
        assert harness.executor.provider_invocations == reads
        assert len(harness.executor_calls()) == reads
        assert (
            f"brain fallback: {FALLBACK_SKIPPED_NOT_AUTHORIZED} after {RUN_FAILURE_BUDGET_EXHAUSTED}"
            in harness.diagnostics
        )
    finally:
        await harness.close()


# --------------------------------------------------------------------------- #
# AC57 — per-destination overrides, keyed by SessionKey platform/channel
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_ac57_an_override_selects_the_list_of_its_destination_only() -> None:
    """AC57: with an override for ``fake/chan-b`` naming only
    ``stream.set_scene`` over the AC49 default, a run on ``chan-b`` invokes
    only the scene (0 sends, ``deliveries`` of length 1, ``text: false``)
    and a run on ``chan-a`` sends exactly one chat message; both lists are
    in the resolved trace."""

    harness = await activate_delivery(
        final(T),
        final(T),
        delivery={
            **AC49_DELIVERY,
            "overrides": {
                f"{PLATFORM}/{CHANNEL_B}": {
                    "mode": "fixed",
                    "actions": [{"action": STREAM_SET_SCENE, "arguments": {"scene": "b"}}],
                }
            },
        },
    )
    try:
        assert harness.resolved() == {
            "default": [CHAT_WRITE],
            "overrides": {f"{PLATFORM}/{CHANNEL_B}": [STREAM_SET_SCENE]},
            "ignored": [],
        }

        on_b = await harness.ask(channel_id=CHANNEL_B, message_id="m-b")
        assert harness.executor_calls() == [(STREAM_SET_SCENE, f"{on_b.run_id}/call-1")]
        assert harness.deliveries() == [
            delivery_entry(STREAM_SET_SCENE, f"{on_b.run_id}/call-1", False, "success")
        ]
        (scene,) = harness.effect_calls(STREAM_SET_SCENE)
        assert scene["arguments"] == {"scene": "b"}
        assert scene["destination"] == Destination(PLATFORM, CHANNEL_B, "stream")
        assert harness.platform.sends == []
        assert (on_b.status, on_b.delivery, on_b.sends) == ("success", "success", 0)
        # No entry received the text: nothing was said, nothing is memorised.
        assert harness.memory(channel_id=CHANNEL_B) == []

        on_a = await harness.ask(channel_id=CHANNEL_A, message_id="m-a")
        assert harness.executor_calls()[-1] == (CHAT_WRITE, f"{on_a.run_id}/call-1")
        (send,) = harness.platform.sends
        assert send["text"] == T
        assert send["destination"] == Destination(PLATFORM, CHANNEL_A, "chat")
        assert harness.deliveries() == [
            delivery_entry(CHAT_WRITE, f"{on_a.run_id}/call-1", True, "success")
        ]
        assert (on_a.status, on_a.delivery, on_a.sends) == ("success", "success", 1)
        assert len(harness.effect_calls(STREAM_SET_SCENE)) == 1
        assert harness.memory(channel_id=CHANNEL_A) == [T]
        assert harness.diagnostics == []
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_ac57_an_override_naming_an_unknown_action_fails_prepare_naming_its_path() -> None:
    """AC57: an override whose entry names ``nope.action`` fails preparation
    with a diagnostic naming ``delivery.overrides["fake/chan-b"].actions[0]``
    and reason ``unknown_action``, the default list resolving fine."""

    harness = await activate_delivery(
        final(T),
        delivery={
            **AC49_DELIVERY,
            "overrides": {
                f"{PLATFORM}/{CHANNEL_B}": {"mode": "fixed", "actions": [{"action": NOPE}]}
            },
        },
        prepare=False,
    )
    try:
        message = await prepare_fails(harness)

        assert message == diagnostic(
            f'delivery.overrides["{PLATFORM}/{CHANNEL_B}"].actions[0]', "unknown_action"
        )
        assert not harness.ready()
        assert harness.session.requests() == []
        assert harness.platform.sends == []
        assert events_of(harness.bus, TRACE_DELIVERY_RESOLVED) == []
    finally:
        await harness.close()


# --------------------------------------------------------------------------- #
# AC56, AC58 — the brain names no delivery; the profiles' group resolves
# --------------------------------------------------------------------------- #


def test_ac56_the_brain_package_names_no_platform_and_no_delivery_action() -> None:
    """AC56: ``grep -rn`` over ``modules/brain/`` for ``chat.write``,
    ``audio.say``, ``stream.set_scene``, ``fakeeffects`` and ``twitch``
    matches 0 lines — the delivery targets are policy, never code."""

    matches: list[str] = []
    for path in sorted(BRAIN_PACKAGE.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if any(literal in line for literal in FORBIDDEN_IN_BRAIN):
                matches.append(f"{path.relative_to(ROOT)}:{number}:{line}")
    assert matches == []


@pytest.mark.asyncio
async def test_ac58_the_profiles_delivery_group_resolves_on_a_second_platform() -> None:
    """AC58/AC49: the ``delivery`` group both example brain profiles carry
    (``mode: fixed``, the single ``chat.write`` entry with ``text_argument:
    text``, no ``overrides``) resolves against the fake platform's manifest
    declaration, which carries ``delivery: {text_argument: text}`` exactly
    as the shipped twitch manifest does — the brain depends on neither."""

    twitch = yaml.safe_load((SHIPPED_MODULES / "twitch" / "module.yaml").read_text("utf-8"))
    fake = yaml.safe_load((FIXTURE_MODULES / "fakeplatform" / "module.yaml").read_text("utf-8"))
    for manifest in (twitch, fake):
        (chat_write,) = [action for action in manifest["actions"] if action["name"] == CHAT_WRITE]
        assert chat_write["delivery"] == {"text_argument": "text"}

    harness = await activate_delivery(final(T), delivery=AC49_DELIVERY, effects=False)
    try:
        spec = harness.context.actions.discovered()[CHAT_WRITE]
        assert spec.delivery == {"text_argument": "text"}
        assert spec.supported_destinations[0].platform == PLATFORM
        assert harness.resolved() == {"default": [CHAT_WRITE], "overrides": {}, "ignored": []}
        assert harness.ready()
    finally:
        await harness.close()


# --------------------------------------------------------------------------- #
# Review round 1 — scope per destination (A2), sends noted as confirmed (A1)
# --------------------------------------------------------------------------- #

MULTI_SAY = "multi.say"
HELD_SAY = "held.say"
OTHER_PLATFORM = "other"


def text_delivery_spec(name: str, *destinations: Destination) -> ActionSpec:
    """A text-taking delivery action declared over *destinations*, in order."""

    return ActionSpec(
        name=name,
        version=1,
        description=f"Deliver the text through {name}.",
        argument_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        },
        result_schema={"type": "object", "properties": {}},
        nature="write",
        required_permissions=(name,),
        supported_destinations=tuple(destinations),
        timeout_seconds=5.0,
        idempotency="none",
        delivery={"text_argument": "text"},
    )


class RecordingProvider:
    """A provider that confirms every call and records where it was addressed."""

    name = "recording"

    def __init__(self) -> None:
        self.destinations: list[Destination] = []

    async def invoke(self, invocation: Any) -> ActionObservation:
        invocation.mark_not_emitted()
        self.destinations.append(invocation.call.destination)
        invocation.mark_emitted()
        return ActionObservation(status="success", provenance={"provider": self.name}, result={})


class HeldProvider:
    """A provider that never answers: the call stays in flight until cancelled."""

    name = "held"

    def __init__(self) -> None:
        self.entered = asyncio.Event()

    async def invoke(self, invocation: Any) -> ActionObservation:
        invocation.mark_not_emitted()
        self.entered.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


async def activate_with_extra_action(
    body: Any, spec: ActionSpec, provider: Any, delivery: Mapping[str, Any]
) -> DeliveryHarness:
    """The delivery harness with one more delivery action bound and granted."""

    harness = await activate_delivery(
        body, delivery=delivery, grants=DEFAULT_GRANTS + (spec.name,), prepare=False
    )
    harness.context.actions.register(spec, provider, module="extra")
    harness.context.actions.mark_ready("extra")
    for handle in harness.handles.values():
        await handle.prepare()
    await harness.brain.prepare()
    return harness


@pytest.mark.asyncio
async def test_a_delivery_is_addressed_to_the_scope_declared_for_the_runs_platform() -> None:
    """Review A2: an action declared for ``other/*/chat`` first and
    ``fake/*/audio`` second is addressed as ``fake/<channel>/audio`` on the
    fake platform — the scope of the declaration covering the run's
    destination, not the first declaration's — so the executor accepts the
    call and the send is confirmed."""

    provider = RecordingProvider()
    spec = text_delivery_spec(
        MULTI_SAY,
        Destination(OTHER_PLATFORM, WILDCARD, "chat"),
        Destination(PLATFORM, WILDCARD, "audio"),
    )
    delivery = {"mode": "fixed", "actions": [{"action": MULTI_SAY, "text_argument": "text"}]}
    harness = await activate_with_extra_action(final(T), spec, provider, delivery)
    try:
        record = await harness.ask(channel_id=CHANNEL_B)

        assert provider.destinations == [Destination(PLATFORM, CHANNEL_B, "audio")]
        assert harness.deliveries() == [
            delivery_entry(MULTI_SAY, f"{record.run_id}/call-1", True, "success")
        ]
        assert (record.status, record.delivery, record.sends) == ("success", "success", 1)
        assert harness.memory(channel_id=CHANNEL_B) == [T]
        assert harness.diagnostics == []
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_a_wildcard_scope_declaration_is_addressed_as_chat() -> None:
    """Review A2: a declaration open to every scope (``*/*/*``) yields a
    concrete call on the reply's own ``chat`` scope, since a call must name
    one target."""

    provider = RecordingProvider()
    spec = text_delivery_spec(MULTI_SAY, Destination(WILDCARD, WILDCARD, WILDCARD))
    delivery = {"mode": "fixed", "actions": [{"action": MULTI_SAY, "text_argument": "text"}]}
    harness = await activate_with_extra_action(final(T), spec, provider, delivery)
    try:
        record = await harness.ask()

        assert provider.destinations == [Destination(PLATFORM, CHANNEL_A, "chat")]
        assert (record.status, record.sends) == ("success", 1)
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_a_send_confirmed_before_a_cancelled_entry_stays_in_the_record() -> None:
    """Review A1: with the chat entry confirmed and the next entry still in
    flight when the brain closes, the run ends ``cancelled`` — recorded once
    — with ``sends == 1``: each confirmed send is noted on the run as it is
    observed, not once the whole list has returned."""

    provider = HeldProvider()
    spec = text_delivery_spec(HELD_SAY, Destination(WILDCARD, WILDCARD, "chat"))
    delivery = {
        "mode": "fixed",
        "actions": [
            {"action": CHAT_WRITE, "text_argument": "text"},
            {"action": HELD_SAY, "text_argument": "text"},
        ],
    }
    harness = await activate_with_extra_action(final(T), spec, provider, delivery)
    try:
        published = await harness.platform.inject(
            {
                "channel_id": CHANNEL_A,
                "author": {"id": VIEWER, "display_name": "Viewer"},
                "message_id": "m-1",
                "text": f"{COMPANION}, hello",
            }
        )
        assert published is not None
        await asyncio.wait_for(provider.entered.wait(), timeout=1)
        assert [send["text"] for send in harness.platform.sends] == [T]

        await harness.brain.close()

        (record,) = harness.brain.scheduler.run_records().values()
        assert record.status == "cancelled"
        assert record.reason == REASON_CANCELLED
        assert record.sends == 1
        assert record.model_calls == 1
        assert harness.completed()["sends"] == 1
        assert len(events_of(harness.bus, TRACE_BRAIN_RUN_COMPLETED)) == 1
        assert harness.memory() == []
    finally:
        await harness.close()


# =========================================================================== #
# Phase 2 P17 — the real `audio_output` and `stream_control` modules in the
# delivery list (R2; AC6, AC7)
# =========================================================================== #
#
# The same fixture platform and the same shipped brain, with the shipped
# ``audio_output`` (scripted speech transport, recording player runner) and
# ``stream_control`` (scripted scene provider) activated by the real loader
# on the same context — or, for the dropped-proxy case, the shipped ``proxy``
# serving ``audio.speak`` for a scripted agent over ``MemoryWebSocketPair``.
# The brain is handed nothing but a ``delivery`` group: it names none of
# these modules, and the phase 1 resolution rules carry their declarations
# (``audio.speak`` with the text, ``audio.play`` and ``stream.scene.set``
# effect-only). ``modules`` mode follows the catalog order the loaders
# yield, so every scenario fixes ``enabled_modules`` explicitly. Nothing
# sleeps: the clock is injected and every wait is a bounded number of turns.

from core.actions import ERROR_NO_PROVIDER, ERROR_PROVIDER_NOT_READY
from core.attachments import AttachmentStore
from core.contracts import PROXY_ERROR_PROXY_DISCONNECTED
from conftest import (
    MemoryWebSocketPair,
    RecordingPlayerRunner,
    ScriptedSceneProvider,
    ScriptedSpeechTransport,
    wav_bytes,
)

AUDIO_SPEAK = "audio.speak"
AUDIO_PLAY = "audio.play"
SCENE_SET = "stream.scene.set"
POLL_CREATE = "stream.poll.create"
AUDIO_OUTPUT = "audio_output"
STREAM_CONTROL = "stream_control"
PROXY = "proxy"

#: The permission each granted action requires, as its manifest declares it.
PHASE2_PERMISSIONS = {
    CHAT_WRITE: CHAT_WRITE,
    CHAT_READ: CHAT_READ,
    AUDIO_SPEAK: "audio.speak",
    AUDIO_PLAY: "audio.play",
    SCENE_SET: "stream.scene",
}

TALKING = "Talking"
CHIME = "chime"
#: ``argv[0]`` must resolve to an executable at ``prepare``, so the player
#: names the running interpreter; the runner is injected, nothing is spawned.
PLAYER_ARGV = [sys.executable, "player-under-test", "-"]
SPEECH_ENDPOINT = "http://speech.invalid/v1/audio/speech"
SPEECH_MODEL = "speech-model-under-test"

SPEECH_BODY = wav_bytes(1.5)
CHIME_CLIP = wav_bytes(0.5)

AC7_PREFERENCE = [CHAT_WRITE, AUDIO_SPEAK]
AC7_RESOLVED = [CHAT_WRITE, AUDIO_SPEAK, AUDIO_PLAY, SCENE_SET]

PROXY_TOKEN = "pairing-secret-token"
PROXY_STORE_LIMITS: dict[str, Any] = {
    "max_object_bytes": 1_048_576,
    "max_objects": 4,
    "max_total_bytes": 4_194_304,
    "max_bytes_per_run": 2_097_152,
    "ttl_seconds": 300.0,
}


def phase2_rule(action: str) -> AuthorizationRule:
    """One explicit grant of *action*'s declared permission to the brain."""

    return AuthorizationRule(
        rule_id=f"brain-{action}",
        action_name=action,
        principals=(PRINCIPAL,),
        granted_permissions=(PHASE2_PERMISSIONS[action],),
    )


def speak_entry() -> dict[str, Any]:
    return {"action": AUDIO_SPEAK, "text_argument": "text"}


def chat_entry() -> dict[str, Any]:
    return {"action": CHAT_WRITE, "text_argument": "text"}


def chime_entry() -> dict[str, Any]:
    return {"action": AUDIO_PLAY, "text_argument": "none", "arguments": {"sound": CHIME}}


def talking_entry() -> dict[str, Any]:
    return {"action": SCENE_SET, "text_argument": "none", "arguments": {"scene": TALKING}}


@dataclass
class Phase2Harness(DeliveryHarness):
    """The delivery harness with the shipped phase 2 modules' seams."""

    speech: Any = None
    runner: Any = None
    scenes: Any = None
    proxy: Any = None

    def outcome(self, call_id: str) -> ActionObservation:
        return self.executor.outcome(call_id)


async def activate_phase2(
    *bodies: Any,
    delivery: Mapping[str, Any],
    clip_dir: Path,
    enabled: Sequence[str] = (AUDIO_OUTPUT, STREAM_CONTROL),
    speech_answers: Sequence[Any] = (),
    synthesis: Mapping[str, Any] | None = None,
    player_argv: Sequence[str] = PLAYER_ARGV,
    grants: Sequence[str] = tuple(PHASE2_PERMISSIONS),
) -> Phase2Harness:
    """The fixture platform, then *enabled* and the shipped brain, prepared.

    The fixture loader declares ``chat.write`` first; the shipped loader then
    declares the actions of *enabled* in that order, which is the catalog
    order ``mode: modules`` follows after the preferred names. Every module
    is prepared before the brain, so the brain resolves against the whole
    catalog and delivers to providers already bound (or already unbound).
    """

    clock = ManualClock()
    store = AttachmentStore(clock=clock, **PROXY_STORE_LIMITS) if PROXY in enabled else None
    context = build_context(
        clock=clock,
        trigger_registry=TriggerRegistry(companion_name=COMPANION),
        chat=ChatContext(max_messages=16, max_bytes=8192, max_age_seconds=600.0, clock=clock),
        authorization=AuthorizationPolicy([phase2_rule(action) for action in grants]),
        attachments=store,
    )
    context.actions.register(CHAT_READ_SPEC, NeverInvokedReadProvider(), module=READER_MODULE)
    context.actions.mark_ready(READER_MODULE)

    diagnostics: list[str] = []
    transport = SimpleNamespace(outcomes=[], sends=[])
    fixtures = ModuleLoader(context.bus, FIXTURE_MODULES, context=context, environ={})
    (platform,) = await fixtures.activate_enabled(
        {
            "enabled_modules": ["fakeplatform"],
            "modules": {
                "fakeplatform": {
                    "channel_ids": [CHANNEL_A, CHANNEL_B],
                    "companion_name": COMPANION,
                    "transport": transport,
                    "diagnostic_reporter": diagnostics.append,
                }
            },
        }
    )

    clip = clip_dir / "chime.wav"
    clip.write_bytes(CHIME_CLIP)
    speech = ScriptedSpeechTransport(*speech_answers)
    runner = RecordingPlayerRunner(clock=clock)
    scenes = ScriptedSceneProvider(scenes=(TALKING, "Gaming"), current="Gaming")
    session = ScriptedModel(*bodies)
    brain_settings = copy_settings(VALID_SETTINGS)
    brain_settings["delivery"] = copy.deepcopy(dict(delivery))
    brain_settings.update(
        {
            "_session_factory": lambda: session,
            "_sleeper": clock.sleep,
            "diagnostic_reporter": diagnostics.append,
        }
    )
    available: dict[str, dict[str, Any]] = {
        AUDIO_OUTPUT: {
            "synthesis": {
                "endpoint": SPEECH_ENDPOINT,
                "model": SPEECH_MODEL,
                "api_key": "",
                "probe": False,
                **(synthesis or {}),
            },
            "voices": {"allowed": ["narrator"], "default": "narrator"},
            "outputs": {"speakers": {"player": {"argv": list(player_argv)}}},
            "default_output": "speakers",
            "clips": {CHIME: {"path": str(clip)}},
            "_synthesis_transport": speech,
            "_player_runner": runner,
            "_sleeper": clock.sleep,
        },
        STREAM_CONTROL: {
            "scenes": {"provider": {"kind": "scripted"}, "allowed": [TALKING]},
            "polls": {"enabled": False},
            "_scene_provider": scenes,
            "_sleeper": clock.sleep,
            "_wall_clock": clock,
        },
        PROXY: {
            "listen": {"host": "127.0.0.1", "port": 8765},
            "pairing_token": PROXY_TOKEN,
            "actions": [AUDIO_SPEAK],
            "_sleeper": clock.sleep,
            "_wall_clock": lambda: 1_700_000_000.0,
            "diagnostic_reporter": diagnostics.append,
            "limits": {"attachments": dict(PROXY_STORE_LIMITS)},
        },
    }
    names = [*enabled, MODULE_NAME]
    shipped = ModuleLoader(context.bus, SHIPPED_MODULES, context=context, environ={})
    activations = await shipped.activate_enabled(
        {
            "enabled_modules": names,
            "modules": {
                name: (brain_settings if name == MODULE_NAME else available[name]) for name in names
            },
        }
    )
    handles = {activation.name: activation.handle for activation in activations}
    brain = handles.pop(MODULE_NAME)
    handles = {"fakeplatform": platform.handle, **handles}
    for handle in handles.values():
        await handle.prepare()
    await brain.prepare()
    return Phase2Harness(
        context=context,
        clock=clock,
        brain=brain,
        platform=platform.handle,
        effects=None,
        session=session,
        transport=transport,
        diagnostics=diagnostics,
        settings=brain_settings,
        handles=handles,
        speech=speech,
        runner=runner,
        scenes=scenes,
        proxy=handles.get(PROXY),
    )


# --------------------------------------------------------------------------- #
# AC6 — fixed lists over the real modules
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_ac6_chat_write_then_audio_speak_writes_and_speaks_the_one_final_answer(
    tmp_path: Path,
) -> None:
    """AC6 (R2): ``[{chat.write, text}, {audio.speak, text}]`` — one final
    answer, ``deliveries == [{chat.write, text: true, success}, {audio.speak,
    text: true, success}]``, exactly 1 chat send, exactly 1 synthesis request
    whose ``input`` is the final text, one player that received the whole
    synthesised WAV, memory written once with the answer."""

    harness = await activate_phase2(
        final(T),
        delivery={"mode": "fixed", "actions": [chat_entry(), speak_entry()]},
        clip_dir=tmp_path,
        speech_answers=(SPEECH_BODY,),
    )
    try:
        assert harness.resolved() == {
            "default": [CHAT_WRITE, AUDIO_SPEAK], "overrides": {}, "ignored": []
        }
        record = await harness.ask()
        run = record.run_id

        assert harness.deliveries() == [
            delivery_entry(CHAT_WRITE, f"{run}/call-1", True, "success"),
            delivery_entry(AUDIO_SPEAK, f"{run}/call-2", True, "success"),
        ]
        assert harness.executor_calls() == [
            (CHAT_WRITE, f"{run}/call-1"), (AUDIO_SPEAK, f"{run}/call-2")
        ]
        (send,) = harness.platform.sends
        assert send["text"] == T
        (request,) = harness.speech.requests
        assert request["json"]["input"] == T
        assert request["json"]["voice"] == "narrator"
        assert harness.runner.starts == [PLAYER_ARGV]
        assert harness.runner.received == SPEECH_BODY
        spoken = harness.outcome(f"{run}/call-2")
        assert spoken.result["playback"] == "completed"
        assert spoken.result["duration_ms"] == spoken.result["played_ms"] == 1500
        assert (record.status, record.delivery, record.sends) == ("success", "success", 2)
        completed = harness.completed()
        assert (completed["delivery"], completed["action_calls"]) == ("success", 2)
        assert len(harness.session.requests()) == 1
        assert harness.tools() == [[CHAT_READ]]
        assert harness.memory() == [T]
        assert harness.diagnostics == []
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_ac6_chat_write_then_audio_play_plays_the_clip_without_the_text(
    tmp_path: Path,
) -> None:
    """AC6 (R2): ``[{chat.write, text}, {audio.play, none, arguments: {sound:
    chime}}]`` — the play entry is ``text: false`` and its call carries
    exactly ``{sound: chime}``; the clip is played (1 player start, the
    clip's bytes), 0 synthesis requests, 1 chat send, both ``success``."""

    harness = await activate_phase2(
        final(T),
        delivery={"mode": "fixed", "actions": [chat_entry(), chime_entry()]},
        clip_dir=tmp_path,
    )
    try:
        record = await harness.ask()
        run = record.run_id

        assert harness.deliveries() == [
            delivery_entry(CHAT_WRITE, f"{run}/call-1", True, "success"),
            delivery_entry(AUDIO_PLAY, f"{run}/call-2", False, "success"),
        ]
        played = harness.outcome(f"{run}/call-2")
        assert played.result["sound"] == CHIME
        assert played.result["playback"] == "completed"
        assert played.result["duration_ms"] == 500
        assert harness.runner.starts == [PLAYER_ARGV]
        assert harness.runner.received == CHIME_CLIP
        assert T.encode("utf-8") not in harness.runner.received
        assert harness.speech.requests == []
        (send,) = harness.platform.sends
        assert send["text"] == T
        assert (record.status, record.delivery, record.sends) == ("success", "success", 1)
        assert harness.memory() == [T]
        assert harness.diagnostics == []
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_ac6_a_scene_only_list_sets_the_scene_once_and_succeeds(tmp_path: Path) -> None:
    """AC6 (R2): ``[{stream.scene.set, none, arguments: {scene: Talking}}]``
    with the scripted scene provider — exactly one set command, for
    ``Talking``; ``delivery == "success"``; no text left anywhere (0 sends,
    0 synthesis requests, 0 player starts) and, no entry having received the
    text, nothing memorised."""

    harness = await activate_phase2(
        final(T),
        delivery={"mode": "fixed", "actions": [talking_entry()]},
        clip_dir=tmp_path,
    )
    try:
        record = await harness.ask()
        run = record.run_id

        assert harness.scenes.sets == [TALKING]
        assert harness.scenes.current == TALKING
        assert harness.deliveries() == [
            delivery_entry(SCENE_SET, f"{run}/call-1", False, "success")
        ]
        switched = harness.outcome(f"{run}/call-1")
        assert (switched.result["scene"], switched.result["previous_scene"]) == (TALKING, "Gaming")
        assert harness.completed()["delivery"] == "success"
        assert (record.status, record.delivery, record.sends) == ("success", "success", 0)
        assert harness.platform.sends == []
        assert harness.speech.requests == []
        assert harness.runner.starts == []
        assert harness.memory() == []
        assert harness.diagnostics == []
    finally:
        await harness.close()


# --------------------------------------------------------------------------- #
# AC7 — mode `modules`, a module not ready, an uncertain speech
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("enabled", "expected"),
    [
        ((AUDIO_OUTPUT, STREAM_CONTROL), AC7_RESOLVED),
        ((STREAM_CONTROL, AUDIO_OUTPUT), [CHAT_WRITE, AUDIO_SPEAK, SCENE_SET, AUDIO_PLAY]),
    ],
    ids=["audio-then-scenes", "scenes-then-audio"],
)
async def test_ac7_modules_mode_resolves_preferred_first_then_catalog_order(
    tmp_path: Path, enabled: tuple[str, ...], expected: list[str]
) -> None:
    """AC7 (R2): with the platform, ``audio_output`` and ``stream_control``
    enabled and ``preference: [chat.write, audio.speak]``, one
    ``brain.delivery.resolved`` lists ``[chat.write, audio.speak, audio.play,
    stream.scene.set]`` — ``stream.poll.create`` (declared, no delivery
    capability) and ``chat.read`` absent. The remaining entries follow the
    catalog order: enabling ``stream_control`` first puts the scene before
    the clip. The run invokes every entry in that order, the text to the two
    text entries only; a derived effect entry carries no constant, so its
    call is refused by the executor's argument check (phase 1 rule, AC52)."""

    harness = await activate_phase2(
        final(T),
        delivery={"mode": "modules", "preference": list(AC7_PREFERENCE)},
        clip_dir=tmp_path,
        enabled=enabled,
        speech_answers=(SPEECH_BODY,),
    )
    try:
        assert harness.resolved() == {"default": expected, "overrides": {}, "ignored": []}
        assert POLL_CREATE in harness.context.actions.discovered()
        record = await harness.ask()
        run = record.run_id

        assert harness.executor_calls() == [
            (action, f"{run}/call-{index}") for index, action in enumerate(expected, start=1)
        ]
        statuses = {entry["action"]: (entry["text"], entry["status"]) for entry in harness.deliveries()}
        assert statuses == {
            CHAT_WRITE: (True, "success"),
            AUDIO_SPEAK: (True, "success"),
            AUDIO_PLAY: (False, "error"),
            SCENE_SET: (False, "error"),
        }
        (request,) = harness.speech.requests
        assert request["json"]["input"] == T
        assert harness.runner.starts == [PLAYER_ARGV]
        assert harness.scenes.sets == []
        (send,) = harness.platform.sends
        assert send["text"] == T
        assert harness.memory() == [T]
        assert len(events_of(harness.bus, TRACE_DELIVERY_RESOLVED)) == 1
    finally:
        await harness.close()


async def run_ac7_modules_list(harness: Phase2Harness) -> tuple[Any, dict[str, Any]]:
    """Run the AC7 list; the record and each entry's ``(status, code)``."""

    assert harness.resolved()["default"] == AC7_RESOLVED
    record = await harness.ask()
    outcomes: dict[str, Any] = {}
    for entry in harness.deliveries():
        observation = harness.outcome(entry["call_id"])
        code = None if observation.error is None else observation.error["code"]
        outcomes[entry["action"]] = (entry["status"], code)
    return record, outcomes


@pytest.mark.asyncio
async def test_ac7_audio_output_not_ready_refuses_speech_and_keeps_the_other_outcomes(
    tmp_path: Path,
) -> None:
    """AC7 (R2, R8): ``audio_output`` whose only player resolves to no
    executable is not ready (its probe sends nothing: there is no output to
    verify); the ``audio.speak`` entry is ``refused provider_not_ready`` and
    reaches no provider, every other entry keeps the outcome it has when the
    module is ready, and — a text entry not having succeeded — conversation
    memory is not written (phase 1 rule)."""

    ready = await activate_phase2(
        final(T),
        delivery={"mode": "modules", "preference": list(AC7_PREFERENCE)},
        clip_dir=tmp_path,
        speech_answers=(SPEECH_BODY,),
    )
    try:
        _record, baseline = await run_ac7_modules_list(ready)
        assert baseline[AUDIO_SPEAK] == ("success", None)
    finally:
        await ready.close()

    harness = await activate_phase2(
        final(T),
        delivery={"mode": "modules", "preference": list(AC7_PREFERENCE)},
        clip_dir=tmp_path,
        synthesis={"probe": True},
        player_argv=[str(tmp_path / "no-such-player"), "-"],
    )
    try:
        assert not harness.context.actions.is_ready(AUDIO_OUTPUT)
        assert AUDIO_SPEAK not in harness.context.actions.registered_ready()
        record, outcomes = await run_ac7_modules_list(harness)

        assert outcomes[AUDIO_SPEAK] == ("refused", ERROR_PROVIDER_NOT_READY)
        assert {action: outcomes[action] for action in outcomes if action != AUDIO_SPEAK} == {
            action: baseline[action] for action in baseline if action != AUDIO_SPEAK
        }
        assert [entry["action"] for entry in harness.deliveries()] == AC7_RESOLVED
        assert harness.speech.requests == []
        assert harness.runner.starts == []
        (send,) = harness.platform.sends
        assert send["text"] == T
        assert record.sends == 1
        assert record.delivery != "success"
        assert harness.memory() == []
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_ac7_a_failed_speech_probe_fails_the_speech_entry_and_writes_no_memory(
    tmp_path: Path,
) -> None:
    """AC7 (R2), the failed-probe case: the probe synthesis raising leaves
    ``audio.speak`` unbound while ``audio.play`` stays bound, so the module
    itself stays ready; the speech entry reaches no provider (no request
    past the probe, no player), the other entries keep their outcomes and
    memory is not written.

    The code is the executor's ``error no_provider``, not the ``refused
    provider_not_ready`` AC7 names for this cause: readiness is per module
    in the registry, and ``audio_output`` binds ``audio.play`` — the
    deviation P9 pinned in ``test_a_failing_probe_leaves_speak_unbound_and_
    play_bound`` and reported; it is asserted here as observed so the delivery
    list's behaviour on that path is fixed either way (see the P17 report)."""

    harness = await activate_phase2(
        final(T),
        delivery={"mode": "modules", "preference": list(AC7_PREFERENCE)},
        clip_dir=tmp_path,
        synthesis={"probe": True},
        speech_answers=(ConnectionError("speech endpoint unreachable"),),
    )
    try:
        assert harness.context.actions.is_ready(AUDIO_OUTPUT)
        assert AUDIO_SPEAK not in harness.context.actions.registered_ready()
        assert AUDIO_PLAY in harness.context.actions.registered_ready()
        assert len(harness.speech.requests) == 1  # the probe, never played
        record, outcomes = await run_ac7_modules_list(harness)

        assert outcomes == {
            CHAT_WRITE: ("success", None),
            AUDIO_SPEAK: ("error", ERROR_NO_PROVIDER),
            AUDIO_PLAY: ("error", ERROR_INVALID_ARGUMENTS),
            SCENE_SET: ("error", ERROR_INVALID_ARGUMENTS),
        }
        assert len(harness.speech.requests) == 1
        assert harness.runner.starts == []
        assert record.sends == 1
        assert harness.memory() == []
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_ac7_an_uncertain_speech_across_a_dropped_proxy_is_never_memorised(
    tmp_path: Path,
) -> None:
    """AC7 (R2, R5): ``[{chat.write, text}, {audio.speak, text}]`` with
    ``audio.speak`` served by the shipped ``proxy`` for a paired agent; the
    connection drops after the ``call`` frame carrying the final text, so the
    speech entry ends ``external_unknown`` (``proxy_disconnected``) while the
    chat entry keeps its ``success`` — and the answer is never memorised as
    a confirmed send."""

    from test_proxy import Agent as ProxyAgent, audio_speak_spec

    harness = await activate_phase2(
        final(T),
        delivery={"mode": "fixed", "actions": [chat_entry(), speak_entry()]},
        clip_dir=tmp_path,
        enabled=(PROXY,),
    )
    agent: Any = None
    asking: asyncio.Task[Any] | None = None
    try:
        assert harness.resolved()["default"] == [CHAT_WRITE, AUDIO_SPEAK]
        pair = MemoryWebSocketPair()
        handler = asyncio.create_task(harness.proxy.connection_handler(pair.server))
        agent = ProxyAgent(None, pair, handler)
        welcome = await agent.pair_up(token=PROXY_TOKEN, specs=(audio_speak_spec(),))
        assert welcome["actions"] == [AUDIO_SPEAK] and agent.mismatches == []

        asking = asyncio.create_task(harness.ask())
        # Bounded: a delivery that never emits the frame fails here, not hangs.
        call = await asyncio.wait_for(agent.call_frame(), timeout=5)
        assert call["action_name"] == AUDIO_SPEAK
        assert call["arguments"] == {"text": T}
        pair.drop()
        record = await asking
        run = record.run_id

        assert harness.deliveries() == [
            delivery_entry(CHAT_WRITE, f"{run}/call-1", True, "success"),
            delivery_entry(AUDIO_SPEAK, f"{run}/call-2", True, "external_unknown"),
        ]
        spoken = harness.outcome(f"{run}/call-2")
        assert spoken.error["code"] == PROXY_ERROR_PROXY_DISCONNECTED
        (send,) = harness.platform.sends
        assert send["text"] == T
        assert (record.status, record.delivery, record.sends) == (
            "external_unknown", "external_unknown", 1
        )
        assert harness.completed()["delivery_error"] == PROXY_ERROR_PROXY_DISCONNECTED
        assert harness.memory() == []
        await wait_until(handler.done)
    finally:
        if asking is not None and not asking.done():
            asking.cancel()
            await asyncio.gather(asking, return_exceptions=True)
        await harness.close()
        if agent is not None and not agent.handler.done():
            agent.handler.cancel()
            await asyncio.gather(agent.handler, return_exceptions=True)
