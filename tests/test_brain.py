"""The brain run engine on the v2 runtime (R2, R3, R5, R6, R7).

The former suite drove the model synchronously from the publication chain,
fed v1 payloads carrying ``chatter_id`` only, read delivery from ``[send:...]``
tags republished on the bus and keyed memory by viewer alone. Every one of
those assertions is on the specification's allowlist; each is replaced below
by its admission-driven, ``SessionKey``-keyed, executor-confirmed equivalent,
with the superseding requirement named in the test's docstring.

The harness wires the **real** :class:`~core.actions.ActionExecutor`, the
real :class:`~core.admission.AdmissionScheduler` the engine builds for itself
and the real :class:`~core.runtime.Supervision`; only the model transport and
the send edge behind ``chat.write`` are fakes. Nothing here sleeps: the clock
is injected and every wait is a bounded number of bare loop turns.
"""

from __future__ import annotations

import asyncio
import copy
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from core.actions import (
    ERROR_EXTERNAL_UNKNOWN,
    ERROR_NOT_AUTHORIZED,
    ERROR_PROVIDER_FAILED,
    ERROR_TIMED_OUT,
    ActionExecutor,
    ActionRegistry,
    AuthorizationPolicy,
    AuthorizationRule,
)
from core.admission import REASON_CANCELLED, REASON_RUN_DEADLINE
from core.bus import EventBus
from core.contracts import (
    PROBE_TOOL,
    TRACE_ACTION_COMPLETED,
    TRACE_ACTION_STARTED,
    TRACE_BRAIN_ADMISSION_ACCEPTED,
    TRACE_BRAIN_RUN_COMPLETED,
    TRACE_BRAIN_RUN_STARTED,
    WILDCARD,
    ActionSpec,
    ActionObservation,
    Counters,
    Destination,
    SessionKey,
)
from core.lifecycle import PhaseCoordinator, SupervisedTasks
from core.loader import ModuleLoader
from core.runtime import RUNTIME_API, RuntimeContext, Supervision
from conftest import (
    FAIL_AFTER_EMISSION,
    FAIL_BEFORE_EMISSION,
    HeldSession,
    FakeResponse,
    FakeSendProvider,
    FakeSession,
    FakeTransport,
    ManualClock,
    MemoryWebSocketPair,
    SENT,
    TIMEOUT_BEFORE_EMISSION,
    RecordingScheduler,
    WSMsgType,
    completion,
    events_of,
    runtime_context as build_context,
    settle,
    tool_call,
    wait_until,
)
from modules.brain import _Settings as brain_settings
from modules.brain import (
    CHAT_SCOPE,
    DELIVERY_NOT_ATTEMPTED,
    FALLBACK_DELIVERY_PREFIX,
    FALLBACK_SENT,
    TRACE_DELIVERY_RESOLVED,
    KNOWN_CAPABILITIES,
    MODULE_NAME,
    PRINCIPAL,
    BrainModuleError,
    ConversationMemory,
    _estimate_tokens,
    _estimate_tool_tokens,
    activate,
    validate_settings,
)


ROOT = Path(__file__).parents[1]

SETTINGS = {
    "endpoint": "https://configured.invalid/chat/completions",
    "model": "configured-model",
    "api_key": "never-show-api-key",
}

# The limits the engine owns (R2, R6): required by its manifest's schema and
# checked finite and positive by its declared hook.
LIMITS = {
    "admission": {
        "session_queue_capacity": 4,
        "global_pending_capacity": 64,
        "max_sessions": 32,
        "workers": 4,
        "wait_seconds": 30,
        "total_run_seconds": 120,
    },
    "budget": {
        "model_turns": 5,
        "model_call_seconds": 30,
        "action_seconds": 10,
        "max_tokens": 8192,
        "max_observation_bytes": 5_242_880,
        "max_action_calls": 6,
        "max_repeated_actions": 2,
        "delivery_reserve_seconds": 10,
    },
    "conversation_memory": {
        "max_sessions": 64,
        "max_exchanges": 20,
        "max_bytes": 65_536,
        "max_age_seconds": 3600.5,
    },
}

# The loop's policy groups (R1, R2, R3), required by the manifest's schema and
# checked for shape by the hook: the generic fallback, the backend
# capabilities verified at prepare, and the configured delivery list — the
# single-entry list of both example brain profiles (decision 1).
POLICY = {
    "fallback": {"enabled": True, "text": "I could not answer in time."},
    "capabilities": {"required": ["structured_output", "vision"]},
    "delivery": {
        "mode": "fixed",
        "actions": [{"action": "chat.write", "text_argument": "text"}],
    },
}

VALID_SETTINGS = {**SETTINGS, **LIMITS, **POLICY}

PLATFORM = "twitch"
OTHER_PLATFORM = "other-platform"
CHANNEL = "channel-1"
OTHER_CHANNEL = "channel-2"
VIEWER = "viewer-4"
INPUT_EVENT = "channel.chat.message"
COMPATIBILITY_ROUTE = "channel.chat.send"
SENDER_MODULE = "sender"
PERMISSION = "chat.write"

# The delivery action of this harness (R1, decision 1): a name the brain
# holds nowhere — it comes from the ``delivery`` settings group, and the
# send edge below declares it with the delivery capability as an input
# module's manifest does. ``CHAT_READ`` is a read action the model may be
# offered as a tool and propose; the agentic loop executes the proposal
# through the executor (the loop's own guarantees live in
# ``tests/test_agentic_loop.py``).
CHAT_WRITE = "chat.write"
CHAT_READ = "chat.read"
READ_PERMISSION = "chat.read"

# Outcomes the fake send edge can be told to produce for one delivery.
SENT = "sent"
FAIL_BEFORE_EMISSION = "fail_before_emission"
FAIL_AFTER_EMISSION = "fail_after_emission"
TIMEOUT_BEFORE_EMISSION = "timeout_before_emission"

RUN_TRACES = (TRACE_BRAIN_RUN_STARTED, TRACE_BRAIN_RUN_COMPLETED)

CHAT_WRITE_SPEC = ActionSpec(
    name=CHAT_WRITE,
    version=1,
    description="Send one chat message to the channel.",
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
    required_permissions=(PERMISSION,),
    supported_destinations=(
        Destination(PLATFORM, WILDCARD, CHAT_SCOPE),
        Destination(OTHER_PLATFORM, WILDCARD, CHAT_SCOPE),
    ),
    timeout_seconds=10.0,
    idempotency="key",
    delivery={"text_argument": "text"},
)

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
    required_permissions=(READ_PERMISSION,),
    supported_destinations=(
        Destination(PLATFORM, WILDCARD, CHAT_SCOPE),
        Destination(OTHER_PLATFORM, WILDCARD, CHAT_SCOPE),
    ),
    timeout_seconds=5.0,
    idempotency="none",
)

BRAIN_GRANT = AuthorizationRule(
    rule_id="brain-chat-write",
    action_name=CHAT_WRITE,
    principals=(PRINCIPAL,),
    granted_permissions=(PERMISSION,),
)

READ_GRANT = AuthorizationRule(
    rule_id="brain-chat-read",
    action_name=CHAT_READ,
    principals=(PRINCIPAL,),
    granted_permissions=(READ_PERMISSION,),
)


class FakeReadProvider:
    """A ``chat.read`` provider answering every call with two chat lines."""

    name = "fake-read"

    def __init__(self) -> None:
        self.calls: list[Any] = []

    async def invoke(self, invocation: Any) -> ActionObservation:
        self.calls.append(invocation.call)
        invocation.mark_not_emitted()
        return ActionObservation(
            status="success",
            provenance={"provider": self.name},
            result={"messages": [{"text": "one"}, {"text": "two"}]},
            parts=[{"type": "text", "text": "one\ntwo"}],
        )


# --------------------------------------------------------------------------- #
# Doubles: the trigger window, the publisher-recording bus
# --------------------------------------------------------------------------- #


class FakeTriggerEngine:
    """A window of recorded decisions the engine consults; it evaluates nothing.

    The brain never evaluates a trigger — the input module does, before it
    publishes (R1) — so only ``recorded`` is ever reached from here.
    """

    def __init__(self) -> None:
        self.decisions: dict[tuple[str, str, str], Any] = {}

    def decide(
        self,
        message_id: str,
        *,
        accepted: bool,
        platform: str = PLATFORM,
        channel_id: str = CHANNEL,
    ) -> None:
        self.decisions[(platform, channel_id, message_id)] = SimpleNamespace(
            accepted=accepted
        )

    def evaluate(self, event: Any, **_: Any) -> Any:
        raise AssertionError("the brain evaluates no trigger")

    def recorded(
        self, *, platform: str, channel_id: str, source_event_id: str, clock: Any = None
    ) -> Any:
        return self.decisions.get((platform, channel_id, source_event_id))


class PublisherRecordingBus(EventBus):
    """A bus that records which component published each event.

    The publishing component is the first frame on the publishing call stack
    outside the bus and the supervision facade, so a trace handed to
    supervision by the scheduler is attributed to ``core.admission`` and one
    published by the engine would be attributed to ``modules.brain``.
    """

    def __init__(self) -> None:
        super().__init__()
        self.publishers: list[tuple[str, str]] = []

    async def publish(self, event_type: str, payload: Any, metadata: Any) -> Any:
        self.publishers.append((event_type, _publishing_component()))
        return await super().publish(event_type, payload, metadata)


def _publishing_component() -> str:
    frame = sys._getframe(1)
    while frame is not None:
        module = frame.f_globals.get("__name__", "")
        if module.startswith(("core.", "modules.")) and module not in (
            "core.bus",
            "core.runtime",
        ):
            return module
        frame = frame.f_back
    return "unknown"


# --------------------------------------------------------------------------- #
# The runtime the engine is activated on
# --------------------------------------------------------------------------- #


def runtime_context(
    bus: EventBus | None = None,
    *,
    clock: Any = None,
    transport: FakeTransport | None = None,
    grant: bool = True,
    read: bool = False,
    read_grant: bool | None = None,
    triggers: Any = None,
    scheduler: Any = None,
) -> RuntimeContext:
    """The shared fixture (``conftest.runtime_context``) carrying this
    engine's delivery declarations: a ``chat.write`` provider over the fake
    send edge, registered and ready under another module's name as the input
    module's would be, and the policy carrying the one explicit grant the
    engine's principal needs — or none, so a test can watch default deny
    refuse the delivery (R5). With ``read`` a ``chat.read`` action is
    registered beside it — granted on the same flag, or as ``read_grant``
    says when given — so a test can watch a read action reach the tools
    while the delivery action never does (R1). The engine builds its own
    real scheduler unless one is shared through the context.
    """

    rules = [BRAIN_GRANT] if grant else []
    if read and (grant if read_grant is None else read_grant):
        rules.append(READ_GRANT)
    policy = AuthorizationPolicy(rules)
    actions = ActionRegistry(authorization=policy)
    actions.register(
        CHAT_WRITE_SPEC,
        FakeSendProvider(transport if transport is not None else FakeTransport()),
        module=SENDER_MODULE,
    )
    if read:
        actions.register(CHAT_READ_SPEC, FakeReadProvider(), module=SENDER_MODULE)
    actions.mark_ready(SENDER_MODULE)
    return build_context(
        bus,
        clock=clock,
        scheduler=scheduler,
        triggers=triggers,
        authorization=policy,
        actions=actions,
    )


@dataclass
class Harness:
    """One activated, prepared engine and every edge a test reads."""

    handle: Any
    context: RuntimeContext
    session: FakeSession
    transport: FakeTransport
    diagnostics: list[str]
    clock: ManualClock
    compatibility_sends: list[dict[str, Any]] = field(default_factory=list)

    @property
    def bus(self) -> EventBus:
        return self.context.bus

    @property
    def executor(self) -> ActionExecutor:
        return self.context.executor

    async def send(
        self,
        text: str = "What is up?",
        *,
        message_id: str = "message-7",
        viewer_id: str = VIEWER,
        channel_id: str = CHANNEL,
        platform: str = PLATFORM,
    ) -> None:
        await self.bus.publish(
            INPUT_EVENT,
            chat_payload(
                text,
                message_id=message_id,
                viewer_id=viewer_id,
                channel_id=channel_id,
                platform=platform,
            ),
            {"source": "test", "schema_version": 2},
        )

    async def completed(self, count: int = 1) -> list[Any]:
        """Wait, in bare loop turns, until *count* runs have a terminal record."""

        await wait_until(lambda: len(self.handle.scheduler.run_records()) >= count)
        return self.records()

    def records(self) -> list[Any]:
        return list(self.handle.scheduler.run_records().values())

    def traces(self, event_type: str) -> list[dict[str, Any]]:
        return events_of(self.bus, event_type)

    def requests(self) -> list[dict[str, Any]]:
        """The scenario requests, in order; the prepare-time probes excluded.

        ``FakeSession`` tells a probe from a scenario request by its body
        (R2), so a per-run count here keeps its meaning whatever ``prepare``
        sent; the probes are read from :meth:`probes`.
        """

        return self.session.post_calls

    def probes(self) -> list[dict[str, Any]]:
        return self.session.probe_calls

    def prompt(self, index: int = -1) -> list[dict[str, str]]:
        return self.session.post_calls[index]["json"]["messages"]

    def tools(self, index: int = -1) -> list[str]:
        """The tool names a scenario request offered, ``[]`` when it offered none."""

        body = self.session.post_calls[index]["json"]
        return [tool["function"]["name"] for tool in body.get("tools", [])]

    async def close(self) -> None:
        await self.handle.close()


def chat_payload(
    text: str = "What is up?",
    *,
    message_id: str = "message-7",
    viewer_id: str = VIEWER,
    channel_id: str = CHANNEL,
    platform: str = PLATFORM,
) -> dict[str, Any]:
    """A schema-version-2 normalised chat message, as an input publishes it."""

    return {
        "platform": platform,
        "channel_id": channel_id,
        "author": {"id": viewer_id, "display_name": "Viewer"},
        "message_id": message_id,
        "text": text,
    }


async def activate_with(
    *results: Any,
    settings_overrides: dict[str, Any] | None = None,
    grant: bool = True,
    read: bool = False,
    read_grant: bool | None = None,
    transport: FakeTransport | None = None,
    session: FakeSession | None = None,
    triggers: Any = None,
    scheduler: Any = None,
    bus: EventBus | None = None,
    prepare: bool = True,
) -> Harness:
    """Activate the engine on a fresh runtime and run its ``prepare`` phase.

    ``prepare`` probes the backend (R2): the shared ``FakeSession`` answers
    each probe with one valid forced tool call unless the caller scripted
    ``probe_results``, and records it apart from the scenario requests.
    """

    target_session = session if session is not None else FakeSession(*results)
    target_transport = transport if transport is not None else FakeTransport()
    clock = ManualClock()
    context = runtime_context(
        bus,
        clock=clock,
        transport=target_transport,
        grant=grant,
        read=read,
        read_grant=read_grant,
        triggers=triggers,
        scheduler=scheduler,
    )
    diagnostics: list[str] = []
    settings = merge_settings(settings_overrides)
    settings.update(
        {
            "_session_factory": lambda: target_session,
            "_sleeper": clock.sleep,
            "diagnostic_reporter": diagnostics.append,
        }
    )
    handle = await activate(context.for_module(MODULE_NAME), settings, {})
    harness = Harness(handle, context, target_session, target_transport, diagnostics, clock)
    if prepare:
        await handle.prepare()
    return harness


def merge_settings(overrides: dict[str, Any] | None) -> dict[str, Any]:
    settings = copy_settings(VALID_SETTINGS)
    for key, value in (overrides or {}).items():
        if isinstance(value, dict) and isinstance(settings.get(key), dict):
            settings[key] = {**settings[key], **value}
        else:
            settings[key] = value
    return settings


def copy_settings(settings: dict[str, Any]) -> dict[str, Any]:
    """A copy a test may mutate at any depth without touching the constants."""

    return copy.deepcopy(settings)


def assert_sanitized(diagnostics: list[str]) -> None:
    rendered = "\n".join(diagnostics)
    assert SETTINGS["api_key"] not in rendered
    assert SETTINGS["endpoint"] not in rendered
    assert "What is up?" not in rendered


def rendered_history(prompt: list[dict[str, str]]) -> str:
    """The retained exchanges of a request: everything between system and user."""

    return "\n".join(message["content"] for message in prompt[1:-1])


# --------------------------------------------------------------------------- #
# Manifest and settings hook (R7)
# --------------------------------------------------------------------------- #


def test_manifest_declares_v2_shape_settings_hook_and_no_grant() -> None:
    """R7 supersedes the former whole-manifest equality.

    The manifest carries ``manifest_version`` and ``runtime_api``, keeps the
    routing keys, declares no lifecycle role, states its settings schema and
    names its settings-validation hook. It declares no trigger and no action:
    ``produces`` and ``consumes`` describe routing and authorize nothing.
    """

    manifest_path = ROOT / "modules" / "brain" / "module.yaml"
    manifest_text = manifest_path.read_text(encoding="utf-8")
    manifest = yaml.safe_load(manifest_text)

    assert manifest["name"] == MODULE_NAME == "brain"
    assert manifest["manifest_version"] == 2
    assert manifest["runtime_api"] == RUNTIME_API
    assert manifest["produces"] == ["channel.chat.send"]
    assert manifest["consumes"] == ["channel.chat.message"]
    assert manifest["middleware"] is False
    assert manifest["lifecycle"] == {"roles": []}
    assert "triggers" not in manifest
    assert "actions" not in manifest

    schema = manifest["settings_schema"]
    assert schema["type"] == "object"
    assert set(schema["required"]) == set(VALID_SETTINGS)
    assert set(schema["properties"]) == set(VALID_SETTINGS)
    for group, limits in LIMITS.items():
        group_schema = schema["properties"][group]
        assert group_schema["type"] == "object"
        assert set(group_schema["required"]) == set(limits)
        assert set(group_schema["properties"]) == set(limits)
        for name in limits:
            declared = group_schema["properties"][name]["type"]
            assert declared == ("number" if name.endswith("_seconds") else "integer")
    fallback_schema = schema["properties"]["fallback"]
    assert fallback_schema["type"] == "object"
    assert set(fallback_schema["required"]) == set(POLICY["fallback"]) == {"enabled", "text"}
    assert fallback_schema["properties"]["enabled"]["type"] == "boolean"
    assert fallback_schema["properties"]["text"]["type"] == "string"
    capabilities_schema = schema["properties"]["capabilities"]
    assert capabilities_schema["type"] == "object"
    assert capabilities_schema["required"] == ["required"]
    assert capabilities_schema["properties"]["required"]["type"] == "array"
    assert capabilities_schema["properties"]["required"]["items"]["type"] == "string"
    delivery_schema = schema["properties"]["delivery"]
    assert delivery_schema["type"] == "object"
    assert delivery_schema["required"] == ["mode"]
    assert set(delivery_schema["properties"]) == {"mode", "actions", "preference", "overrides"}
    assert set(delivery_schema["properties"]["mode"]["enum"]) == {"fixed", "modules"}
    entry_schema = delivery_schema["properties"]["actions"]["items"]
    assert entry_schema["required"] == ["action"]
    assert set(entry_schema["properties"]) == {"action", "text_argument", "arguments"}
    assert delivery_schema["properties"]["preference"]["items"]["type"] == "string"
    assert delivery_schema["properties"]["overrides"]["type"] == "object"
    assert manifest["settings_validator"] == "validate_settings"
    assert callable(validate_settings)
    for value in SETTINGS.values():
        assert value not in manifest_text


def test_settings_hook_names_module_and_field_without_values() -> None:
    """R7/AC24: one diagnostic per offending field; no configured value echoed."""

    assert validate_settings(VALID_SETTINGS) == []

    invalid = copy_settings(VALID_SETTINGS)
    invalid["endpoint"] = "configured.invalid/chat/completions"
    del invalid["conversation_memory"]["max_exchanges"]
    diagnostics = validate_settings(invalid)

    assert diagnostics == [
        "module 'brain': field 'endpoint': must be a well-formed http(s) URL",
        "module 'brain': field 'conversation_memory.max_exchanges': is required",
    ]
    assert len(diagnostics) == 2
    for diagnostic in diagnostics:
        assert "'brain'" in diagnostic
    rendered = "\n".join(diagnostics)
    assert rendered.count(SETTINGS["api_key"]) == 0
    assert rendered.count(invalid["endpoint"]) == 0
    assert validate_settings(["not", "a", "mapping"]) == [
        "module 'brain': field 'settings': must be a mapping"
    ]


@pytest.mark.parametrize(
    ("path", "value", "expected"),
    [
        (("endpoint",), "ftp://configured.invalid/x", "field 'endpoint': must be a well-formed http(s) URL"),
        (("endpoint",), "https://", "field 'endpoint': must be a well-formed http(s) URL"),
        (("endpoint",), "https://host.invalid/a b", "field 'endpoint': must be a well-formed http(s) URL"),
        (("endpoint",), "https://host.invalid:abc/chat/completions", "field 'endpoint': must be a well-formed http(s) URL"),
        (("endpoint",), "https://host.invalid:99999/chat/completions", "field 'endpoint': must be a well-formed http(s) URL"),
        (("endpoint",), None, "field 'endpoint': is required"),
        (("model",), "   ", "field 'model': must be a non-empty string"),
        (("model",), 3, "field 'model': must be a non-empty string"),
        (("api_key",), "", "field 'api_key': must be a non-empty string"),
        (("api_key",), "${MODEL_API_KEY}", "field 'api_key': is an unresolved environment reference"),
        (("admission",), None, "field 'admission': is required"),
        (("budget",), [5], "field 'budget': must be a mapping of limits"),
        (("admission", "workers"), 0, "field 'admission.workers': must be a positive integer"),
        (("admission", "workers"), 2.0, "field 'admission.workers': must be a positive integer"),
        (("admission", "workers"), True, "field 'admission.workers': must be a positive integer"),
        (("admission", "wait_seconds"), float("inf"), "field 'admission.wait_seconds': must be a finite positive number"),
        (("admission", "wait_seconds"), float("nan"), "field 'admission.wait_seconds': must be a finite positive number"),
        (("admission", "wait_seconds"), "30", "field 'admission.wait_seconds': must be a finite positive number"),
        (("budget", "model_call_seconds"), 0, "field 'budget.model_call_seconds': must be a finite positive number"),
        (("budget", "max_tokens"), -1, "field 'budget.max_tokens': must be a positive integer"),
        (("conversation_memory", "max_age_seconds"), -0.5, "field 'conversation_memory.max_age_seconds': must be a finite positive number"),
        (("conversation_memory", "max_exchange"), 3, "field 'conversation_memory.max_exchange': is not a limit this module owns"),
    ],
)
def test_settings_hook_refuses_each_unevaluable_field_with_one_diagnostic(
    path: tuple[str, ...], value: Any, expected: str
) -> None:
    """R2/R6/R7: an absent, non-numeric, infinite or non-positive limit is a diagnostic."""

    invalid = copy_settings(VALID_SETTINGS)
    target: Any = invalid
    for component in path[:-1]:
        target = target[component]
    if value is None:
        del target[path[-1]]
    else:
        target[path[-1]] = value

    diagnostics = validate_settings(invalid)

    assert f"module 'brain': {expected}" in diagnostics
    assert len(diagnostics) == 1
    assert_sanitized(diagnostics)


# The 10 budget/admission values of R3, with the wording each kind refuses.
ACTED_LIMITS = [
    ("budget", "model_turns", "must be a positive integer"),
    ("budget", "model_call_seconds", "must be a finite positive number"),
    ("budget", "action_seconds", "must be a finite positive number"),
    ("budget", "max_tokens", "must be a positive integer"),
    ("budget", "max_observation_bytes", "must be a positive integer"),
    ("budget", "max_action_calls", "must be a positive integer"),
    ("budget", "max_repeated_actions", "must be a positive integer"),
    ("budget", "delivery_reserve_seconds", "must be a finite positive number"),
    ("admission", "wait_seconds", "must be a finite positive number"),
    ("admission", "total_run_seconds", "must be a finite positive number"),
]
UNEVALUABLE = [
    pytest.param(None, id="absent"),
    pytest.param(0, id="zero"),
    pytest.param(-1, id="negative"),
    pytest.param(float("inf"), id="infinite"),
]


@pytest.mark.parametrize(("group", "field_name", "wording"), ACTED_LIMITS)
@pytest.mark.parametrize("value", UNEVALUABLE)
def test_ac20_each_acted_limit_is_refused_when_absent_zero_negative_or_infinite(
    group: str, field_name: str, wording: str, value: Any
) -> None:
    """R3/AC20: the 10 budget/admission values, one diagnostic naming the field."""

    assert validate_settings(VALID_SETTINGS) == []
    invalid = copy_settings(VALID_SETTINGS)
    if value is None:
        del invalid[group][field_name]
    else:
        invalid[group][field_name] = value

    diagnostics = validate_settings(invalid)

    reason = "is required" if value is None else wording
    assert diagnostics == [f"module 'brain': field '{group}.{field_name}': {reason}"]
    assert_sanitized(diagnostics)
    with pytest.raises(BrainModuleError) as refused:
        brain_settings.from_mapping(invalid)
    assert str(refused.value) == diagnostics[0]


def test_ac20_the_queued_wait_may_not_exceed_the_total_run_deadline() -> None:
    """R3/AC20: ``admission.wait_seconds`` is part of ``admission.total_run_seconds``."""

    settings = copy_settings(VALID_SETTINGS)
    settings["admission"]["wait_seconds"] = 61
    settings["admission"]["total_run_seconds"] = 60

    assert validate_settings(settings) == [
        "module 'brain': field 'admission.wait_seconds': "
        "must not exceed admission.total_run_seconds"
    ]

    equal = copy_settings(VALID_SETTINGS)
    equal["admission"]["wait_seconds"] = 60
    equal["admission"]["total_run_seconds"] = 60
    assert validate_settings(equal) == []

    # An unevaluable total is its own single diagnostic; the comparison never
    # adds a second one on the wait.
    infinite = copy_settings(VALID_SETTINGS)
    infinite["admission"]["total_run_seconds"] = float("inf")
    assert validate_settings(infinite) == [
        "module 'brain': field 'admission.total_run_seconds': must be a finite positive number"
    ]


@pytest.mark.parametrize(
    ("required", "expected"),
    [
        (["vision"], "must name 'structured_output'"),
        (
            ["structured_output", "audio"],
            "names a capability this module does not know (known: structured_output, vision)",
        ),
    ],
)
def test_ac10_capabilities_required_needs_structured_output_and_only_known_names(
    required: list[str], expected: str
) -> None:
    """R2/AC10: both refusals, one diagnostic naming ``capabilities.required``."""

    assert KNOWN_CAPABILITIES == frozenset({"structured_output", "vision"})
    invalid = copy_settings(VALID_SETTINGS)
    invalid["capabilities"]["required"] = required

    diagnostics = validate_settings(invalid)

    assert diagnostics == [f"module 'brain': field 'capabilities.required': {expected}"]
    assert_sanitized(diagnostics)

    accepted = copy_settings(VALID_SETTINGS)
    accepted["capabilities"]["required"] = ["structured_output"]
    assert validate_settings(accepted) == []
    assert brain_settings.from_mapping(accepted).capabilities == frozenset({"structured_output"})


@pytest.mark.parametrize(
    ("path", "value", "expected"),
    [
        (("capabilities",), None, "field 'capabilities': is required"),
        (("capabilities",), ["structured_output"], "field 'capabilities': must be a mapping"),
        (("capabilities", "required"), None, "field 'capabilities.required': is required"),
        (("capabilities", "required"), "structured_output", "field 'capabilities.required': must be a list of capability names"),
        (("capabilities", "required"), ["structured_output", 3], "field 'capabilities.required': must be a list of capability names"),
        (("capabilities", "optional"), [], "field 'capabilities.optional': is not a setting this module knows"),
        (("fallback",), None, "field 'fallback': is required"),
        (("fallback",), "I could not answer in time.", "field 'fallback': must be a mapping"),
        (("fallback", "enabled"), None, "field 'fallback.enabled': is required"),
        (("fallback", "enabled"), "yes", "field 'fallback.enabled': must be a boolean"),
        (("fallback", "text"), None, "field 'fallback.text': is required"),
        (("fallback", "text"), "   ", "field 'fallback.text': must be a non-empty string"),
        (("fallback", "text"), 7, "field 'fallback.text': must be a non-empty string"),
        (("fallback", "retries"), 1, "field 'fallback.retries': is not a setting this module knows"),
    ],
)
def test_settings_hook_refuses_each_malformed_fallback_or_capability_field(
    path: tuple[str, ...], value: Any, expected: str
) -> None:
    """R2/R3: the ``fallback`` and ``capabilities`` groups, one diagnostic per field."""

    invalid = copy_settings(VALID_SETTINGS)
    target: Any = invalid
    for component in path[:-1]:
        target = target[component]
    if value is None:
        del target[path[-1]]
    else:
        target[path[-1]] = value

    diagnostics = validate_settings(invalid)

    assert diagnostics == [f"module 'brain': {expected}"]
    assert_sanitized(diagnostics)


@pytest.mark.parametrize(
    ("delivery", "expected"),
    [
        pytest.param(
            {"mode": "teleport", "actions": [{"action": "chat.write"}]},
            "field 'delivery.mode': must be one of fixed, modules",
            id="ac53-unknown-mode",
        ),
        pytest.param(
            {"mode": "fixed", "actions": [{"text_argument": "text"}]},
            "field 'delivery.actions[0].action': is required",
            id="ac53-entry-without-action",
        ),
        pytest.param(
            {"actions": [{"action": "chat.write"}]},
            "field 'delivery.mode': is required",
            id="mode-absent",
        ),
        pytest.param(
            {"mode": "fixed"},
            "field 'delivery.actions': is required for mode 'fixed'",
            id="fixed-without-list",
        ),
        pytest.param(
            {"mode": "fixed", "actions": {"action": "chat.write"}},
            "field 'delivery.actions': must be a list of delivery entries",
            id="list-not-a-list",
        ),
        pytest.param(
            {"mode": "fixed", "actions": [{"action": "chat.write"}, "audio.say"]},
            "field 'delivery.actions[1]': must be a mapping",
            id="entry-not-a-mapping",
        ),
        pytest.param(
            {"mode": "fixed", "actions": [{"action": "  "}]},
            "field 'delivery.actions[0].action': must be a non-empty action name",
            id="entry-blank-action",
        ),
        pytest.param(
            {"mode": "fixed", "actions": [{"action": "chat.write", "text_argument": None}]},
            "field 'delivery.actions[0].text_argument': must be an argument name or 'none'",
            id="entry-null-text-argument",
        ),
        pytest.param(
            {"mode": "fixed", "actions": [{"action": "chat.write", "text_argument": 3}]},
            "field 'delivery.actions[0].text_argument': must be an argument name or 'none'",
            id="entry-numeric-text-argument",
        ),
        pytest.param(
            {"mode": "fixed", "actions": [{"action": "chat.write", "arguments": ["x"]}]},
            "field 'delivery.actions[0].arguments': must be a mapping of arguments",
            id="entry-arguments-not-a-mapping",
        ),
        pytest.param(
            {"mode": "fixed", "actions": [{"action": "chat.write", "text": "x"}]},
            "field 'delivery.actions[0].text': is not a setting this module knows",
            id="entry-unknown-key",
        ),
        pytest.param(
            {"mode": "modules", "preference": "audio.say"},
            "field 'delivery.preference': must be a list of action names",
            id="preference-not-a-list",
        ),
        pytest.param(
            {"mode": "modules", "preference": ["audio.say", 1]},
            "field 'delivery.preference': must be a list of action names",
            id="preference-with-a-non-name",
        ),
        pytest.param(
            {"mode": "modules", "retries": 2},
            "field 'delivery.retries': is not a setting this module knows",
            id="group-unknown-key",
        ),
        pytest.param(
            {"mode": "modules", "overrides": [{"mode": "fixed", "actions": []}]},
            "field 'delivery.overrides': must be a mapping keyed by destination",
            id="overrides-not-a-mapping",
        ),
        pytest.param(
            {"mode": "modules", "overrides": {"chan-b": {"mode": "fixed", "actions": []}}},
            """field 'delivery.overrides["chan-b"]': must be keyed '<platform>/<channel_id>'""",
            id="override-key-without-platform",
        ),
        pytest.param(
            {"mode": "modules", "overrides": {"fake/chan-b": ["chat.write"]}},
            """field 'delivery.overrides["fake/chan-b"]': must be a mapping""",
            id="override-not-a-mapping",
        ),
        pytest.param(
            {"mode": "modules", "overrides": {"fake/chan-b": {"mode": "teleport"}}},
            """field 'delivery.overrides["fake/chan-b"].mode': must be one of fixed, modules""",
            id="override-unknown-mode",
        ),
        pytest.param(
            {"mode": "modules", "overrides": {"fake/chan-b": {"mode": "fixed", "actions": [{}]}}},
            """field 'delivery.overrides["fake/chan-b"].actions[0].action': is required""",
            id="override-entry-without-action",
        ),
        pytest.param(
            {
                "mode": "modules",
                "overrides": {"fake/chan-b": {"mode": "fixed", "actions": [], "overrides": {}}},
            },
            """field 'delivery.overrides["fake/chan-b"].overrides': is not a setting this module knows""",
            id="override-nesting-overrides",
        ),
    ],
)
def test_ac53_settings_hook_refuses_each_malformed_delivery_field_with_one_diagnostic(
    delivery: dict[str, Any], expected: str
) -> None:
    """R1/AC53 (validation part): ``delivery.mode: teleport`` and a fixed entry
    lacking ``action`` are refused with one diagnostic naming the field; so is
    every other shape defect of the group, its entries and its overrides."""

    invalid = copy_settings(VALID_SETTINGS)
    invalid["delivery"] = delivery

    diagnostics = validate_settings(invalid)

    assert diagnostics == [f"module 'brain': {expected}"]
    assert_sanitized(diagnostics)


def test_settings_hook_leaves_catalog_questions_to_prepare() -> None:
    """R1/AC53: the shape is the hook's; existence, nature and text mapping are
    resolved against the discovered catalog at ``prepare``, so an empty fixed
    list, an unknown action name, a read action or an effect-only entry pass
    validation as shaped settings."""

    for delivery in (
        {"mode": "fixed", "actions": []},
        {"mode": "fixed", "actions": [{"action": "nope.action"}]},
        {"mode": "fixed", "actions": [{"action": "chat.read", "text_argument": "text"}]},
        {"mode": "fixed", "actions": [{"action": "stream.set_scene", "text_argument": "none"}]},
        {
            "mode": "fixed",
            "actions": [{"action": "chat.write", "text_argument": "text", "arguments": {"text": "x"}}],
        },
        {"mode": "modules"},
        {"mode": "modules", "preference": ["audio.say", "chat.write", "nope.action"]},
        {"mode": "modules", "actions": [{"action": "chat.write"}], "preference": []},
        {"mode": "fixed", "actions": [{"action": "chat.write"}], "overrides": {}},
    ):
        settings = copy_settings(VALID_SETTINGS)
        settings["delivery"] = delivery
        assert validate_settings(settings) == [], delivery


def test_settings_parse_the_loop_groups_into_typed_values() -> None:
    """R1/R2/R3: ``_Settings.from_mapping`` carries the budgets, the fallback,
    the verified-capability set and the delivery lists as typed values."""

    settings = copy_settings(VALID_SETTINGS)
    settings["delivery"] = {
        "mode": "fixed",
        "actions": [
            {"action": " chat.write ", "text_argument": "text"},
            {"action": "stream.set_scene", "text_argument": "none", "arguments": {"scene": "answering"}},
            {"action": "audio.say"},
        ],
        "preference": ["audio.say"],
        "overrides": {
            "fake/chan-b": {
                "mode": "modules",
                "preference": ["stream.set_scene", "chat.write"],
            },
        },
    }

    parsed = brain_settings.from_mapping(settings)

    assert (
        parsed.budget.model_turns,
        parsed.budget.model_call_seconds,
        parsed.budget.action_seconds,
        parsed.budget.max_tokens,
        parsed.budget.max_observation_bytes,
        parsed.budget.max_action_calls,
        parsed.budget.max_repeated_actions,
        parsed.budget.delivery_reserve_seconds,
        parsed.admission.wait_seconds,
        parsed.admission.total_run_seconds,
    ) == (5, 30, 10, 8192, 5_242_880, 6, 2, 10, 30, 120)
    assert parsed.fallback.enabled is True
    assert parsed.fallback.text == "I could not answer in time."
    assert parsed.capabilities == frozenset({"structured_output", "vision"})
    assert isinstance(parsed.capabilities, frozenset)

    default = parsed.delivery.default
    assert default.mode == "fixed"
    assert [(entry.action, entry.text_argument, dict(entry.arguments)) for entry in default.actions] == [
        ("chat.write", "text", {}),
        ("stream.set_scene", "none", {"scene": "answering"}),
        ("audio.say", None, {}),
    ]
    assert default.preference == ("audio.say",)
    assert set(parsed.delivery.overrides) == {"fake/chan-b"}
    override = parsed.delivery.overrides["fake/chan-b"]
    assert override.mode == "modules"
    assert override.actions == ()
    assert override.preference == ("stream.set_scene", "chat.write")
    assert parsed.delivery.for_destination("fake", "chan-b") is override
    assert parsed.delivery.for_destination("fake", "chan-a") is default
    assert parsed.delivery.for_destination(PLATFORM, CHANNEL) is default

    # The constant's own default list, as both example brain profiles carry it.
    example = brain_settings.from_mapping(VALID_SETTINGS).delivery
    assert example.overrides == {}
    assert [(entry.action, entry.text_argument) for entry in example.default.actions] == [
        ("chat.write", "text")
    ]


@pytest.mark.asyncio
async def test_loader_resolves_the_declared_hook_and_grants_nothing_from_produces() -> None:
    """R7: the declared hook name resolves to this package's callable, and
    ``produces: [channel.chat.send]`` puts 0 actions of the brain's in the
    registry's discovered, ready and authorized views — the one action there
    is the send edge's, registered by the harness under another module.
    """

    session = FakeSession()
    diagnostics: list[str] = []
    context = runtime_context()
    loader = ModuleLoader(context.bus, ROOT / "modules", context=context, environ={})
    settings = {
        **copy_settings(VALID_SETTINGS),
        "_session_factory": lambda: session,
        "diagnostic_reporter": diagnostics.append,
    }

    activations = await loader.activate_enabled(
        {"enabled_modules": ["brain"], "modules": {"brain": settings}}
    )

    (activation,) = activations
    assert activation.name == "brain"
    assert activation.manifest_version == 2
    assert activation.roles == frozenset()
    assert type(activation.handle).__name__ == "BrainModule"
    assert diagnostics == []
    assert set(context.actions.discovered()) == {CHAT_WRITE}
    assert set(context.actions.registered_ready()) == {CHAT_WRITE}
    assert dict(context.actions.authorized(principal="viewer")) == {}
    assert all(binding.module == SENDER_MODULE for binding in context.actions.bindings())

    coordinator = PhaseCoordinator(
        activations, tasks=context.tasks, reporter=diagnostics.append
    )
    assert (await coordinator.start()).status == 0
    assert (await coordinator.stop()).status == 0
    assert diagnostics == []
    assert session.close_calls == 1


@pytest.mark.asyncio
async def test_loader_refuses_settings_the_hook_rejects_before_activation() -> None:
    """R7/AC24: the declared hook refuses through the loader, value-free."""

    from core.loader import ModuleLoadError

    context = runtime_context()
    loader = ModuleLoader(context.bus, ROOT / "modules", context=context, environ={})
    # Well-formed for the schema, refused by the hook: the schema cannot see
    # that this endpoint has no scheme or that this limit is infinite.
    invalid = copy_settings(VALID_SETTINGS)
    invalid["endpoint"] = "configured.invalid/chat/completions"
    invalid["conversation_memory"]["max_age_seconds"] = float("inf")
    activated: list[str] = []
    invalid["_session_factory"] = lambda: activated.append("session")

    with pytest.raises(ModuleLoadError) as caught:
        await loader.activate_enabled(
            {"enabled_modules": ["brain"], "modules": {"brain": invalid}}
        )

    # The module's own diagnostics reach the report, naming module and
    # field, with 0 configured values (AC24).
    assert caught.value.diagnostics == (
        "module 'brain': field 'endpoint': must be a well-formed http(s) URL",
        "module 'brain': field 'conversation_memory.max_age_seconds': "
        "must be a finite positive number",
    )
    assert_sanitized([str(caught.value)])
    assert invalid["endpoint"] not in str(caught.value)
    assert invalid["api_key"] not in str(caught.value)
    assert activated == []
    assert loader.activations == []


def test_settings_hook_accepts_owned_limits_equal_to_the_accepted_block() -> None:
    """R6 (P24 F6): handed the accepted ``limits`` block, owned groups equal to
    it pass; without the block, or with a block lacking the group, the
    module's own settings are the only copy and pass on their own terms. The
    ``budget`` group is the module's alone and is never compared."""

    accepted = {
        "admission": dict(LIMITS["admission"]),
        "conversation_memory": dict(LIMITS["conversation_memory"]),
        "bus_history": {"max_events": 1, "max_bytes": 1, "max_age_seconds": 1},
    }

    assert validate_settings({**copy_settings(VALID_SETTINGS), "limits": accepted}) == []
    assert validate_settings(VALID_SETTINGS) == []
    assert validate_settings(
        {**copy_settings(VALID_SETTINGS), "limits": {"admission": dict(LIMITS["admission"])}}
    ) == []
    # Equal as numbers: an integer copy of a float second count is the same bound.
    same = copy_settings(VALID_SETTINGS)
    same["admission"]["wait_seconds"] = 30.0
    assert validate_settings({**same, "limits": accepted}) == []


@pytest.mark.parametrize(
    ("path", "value", "expected"),
    [
        (
            ("admission", "workers"),
            2,
            "field 'admission.workers': must equal limits.admission.workers",
        ),
        (
            ("admission", "session_queue_capacity"),
            1,
            "field 'admission.session_queue_capacity': "
            "must equal limits.admission.session_queue_capacity",
        ),
        (
            ("admission", "total_run_seconds"),
            60,
            "field 'admission.total_run_seconds': "
            "must equal limits.admission.total_run_seconds",
        ),
        (
            ("conversation_memory", "max_exchanges"),
            1,
            "field 'conversation_memory.max_exchanges': "
            "must equal limits.conversation_memory.max_exchanges",
        ),
        (
            ("conversation_memory", "max_age_seconds"),
            3600,
            "field 'conversation_memory.max_age_seconds': "
            "must equal limits.conversation_memory.max_age_seconds",
        ),
    ],
)
def test_settings_hook_refuses_an_owned_limit_that_differs_from_the_accepted_one(
    path: tuple[str, str], value: Any, expected: str
) -> None:
    """R6 (P24 F6): an owned limit that differs from the accepted block is
    refused by field, value-free, so the scheduler and the memory are never
    built on a mirror the entry point did not accept."""

    settings = copy_settings(VALID_SETTINGS)
    settings[path[0]][path[1]] = value
    settings["limits"] = {
        "admission": dict(LIMITS["admission"]),
        "conversation_memory": dict(LIMITS["conversation_memory"]),
    }

    diagnostics = validate_settings(settings)

    assert diagnostics == [f"module 'brain': {expected}"]
    assert str(value) not in diagnostics[0]
    assert str(LIMITS[path[0]][path[1]]) not in diagnostics[0]
    assert_sanitized(diagnostics)
    with pytest.raises(BrainModuleError) as caught:
        brain_settings.from_mapping(settings)
    assert str(caught.value) == diagnostics[0]


def test_settings_hook_reports_every_differing_owned_limit_and_a_malformed_block() -> None:
    """Every field that differs is its own diagnostic; a limit that is not
    evaluable is reported as such and not compared; a handed block that is
    not a mapping, or a group that is not, is named by field."""

    settings = copy_settings(VALID_SETTINGS)
    settings["admission"]["workers"] = 1
    settings["admission"]["wait_seconds"] = float("inf")
    settings["conversation_memory"]["max_bytes"] = 1
    settings["limits"] = {
        "admission": dict(LIMITS["admission"]),
        "conversation_memory": dict(LIMITS["conversation_memory"]),
    }

    assert validate_settings(settings) == [
        "module 'brain': field 'admission.workers': must equal limits.admission.workers",
        "module 'brain': field 'admission.wait_seconds': must be a finite positive number",
        "module 'brain': field 'conversation_memory.max_bytes': "
        "must equal limits.conversation_memory.max_bytes",
    ]
    assert validate_settings({**copy_settings(VALID_SETTINGS), "limits": [1]}) == [
        "module 'brain': field 'limits': must be a mapping of limit groups"
    ]
    assert validate_settings(
        {**copy_settings(VALID_SETTINGS), "limits": {"admission": 4}}
    ) == [
        "module 'brain': field 'limits.admission': must be a mapping of limits"
    ]


@pytest.mark.asyncio
async def test_loader_refuses_an_owned_limit_differing_from_the_accepted_block_before_activation() -> None:
    """R6 (P24 F6): through the real loader, a differing copy stops startup
    naming module and field with 0 sessions created and 0 activations."""

    from core.loader import ModuleLoadError

    context = runtime_context()
    loader = ModuleLoader(context.bus, ROOT / "modules", context=context, environ={})
    settings = copy_settings(VALID_SETTINGS)
    settings["limits"] = {
        "admission": {**LIMITS["admission"], "workers": LIMITS["admission"]["workers"] + 1},
        "conversation_memory": dict(LIMITS["conversation_memory"]),
    }
    activated: list[str] = []
    settings["_session_factory"] = lambda: activated.append("session")

    with pytest.raises(ModuleLoadError) as caught:
        await loader.activate_enabled(
            {"enabled_modules": ["brain"], "modules": {"brain": settings}}
        )

    assert caught.value.diagnostics == (
        "module 'brain': field 'admission.workers': must equal limits.admission.workers",
    )
    assert_sanitized([str(caught.value)])
    assert activated == []
    assert loader.activations == []


@pytest.mark.asyncio
async def test_activation_refuses_a_bare_bus_and_a_context_without_executor() -> None:
    """R7/AC26: the engine is reached through the versioned context only.

    The compatibility signature's bare bus is refused before any session is
    created, and so is a context whose executor is absent: delivery is one
    executor call and nothing else (R5), so there is no engine without it.
    """

    created: list[str] = []
    settings = {**copy_settings(VALID_SETTINGS), "_session_factory": lambda: created.append("session")}

    with pytest.raises(BrainModuleError, match="runtime context is invalid"):
        await activate(EventBus(), settings, {})
    without_executor = RuntimeContext(
        bus=EventBus(),
        actions=ActionRegistry(authorization=AuthorizationPolicy()),
        supervision=Supervision(EventBus(), counters=Counters()),
        tasks=SupervisedTasks(),
    )
    with pytest.raises(BrainModuleError, match="runtime context is invalid"):
        await activate(without_executor.for_module(MODULE_NAME), settings, {})
    assert created == []


# --------------------------------------------------------------------------- #
# The request (R2, R3, R5)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_admitted_message_drives_one_configured_request_with_viewer_context() -> None:
    """Replaces the synchronous request inspection (allowlisted; R1, R2, R3).

    The request is observed once the admitted run has completed, not when the
    publication returns; the viewer identity is the attested ``author.id``
    of the normalised event, never a ``chatter_id``; and the request offers
    the model the ``read`` actions of the registry's *authorized* view — the
    one read action the engine's principal holds a grant for — and nothing
    else (R5), as Chat Completions tool definitions carrying the spec's name,
    description and argument schema with ``tool_choice: auto`` (R2) rather
    than as prompt text. The delivery action is granted too and is never
    offered (R1, decision 1: the model never selects the delivery); which
    read actions reach the tools per declared scope (AC6) is asserted with
    the agentic loop, in ``tests/test_agentic_loop.py``.
    """

    harness = await activate_with(FakeResponse(200, completion("Hello")), read=True)
    try:
        await harness.send()
        (record,) = await harness.completed()

        assert record.status == "success"
        assert len(harness.requests()) == 1
        request = harness.requests()[0]
        assert request["url"] == SETTINGS["endpoint"]
        assert request["json"]["model"] == SETTINGS["model"]
        assert request["headers"]["Authorization"] == f"Bearer {SETTINGS['api_key']}"
        assert harness.tools(0) == [CHAT_READ]
        # Granted and bound, so in the authorized view — and still not offered.
        authorized = harness.context.actions.authorized(
            principal=PRINCIPAL, destination=Destination(PLATFORM, CHANNEL, CHAT_SCOPE)
        )
        assert set(authorized) == {CHAT_WRITE, CHAT_READ}
        (tool,) = request["json"]["tools"]
        assert tool["type"] == "function"
        assert tool["function"]["description"] == CHAT_READ_SPEC.description
        assert tool["function"]["parameters"] == {
            "type": "object",
            "properties": {"limit": {"type": "integer", "minimum": 1}},
            "required": [],
            "additionalProperties": False,
        }
        json.dumps(request["json"])  # the body is plain JSON, frozen mappings unwrapped
        assert request["json"]["tool_choice"] == "auto"
        messages = request["json"]["messages"]
        assert messages[0]["role"] == "system"
        system = messages[0]["content"]
        assert "[send:" not in system
        assert CHAT_WRITE not in system
        assert messages[-1]["role"] == "user"
        assert "What is up?" in messages[-1]["content"]
        assert VIEWER in messages[-1]["content"]
        assert "message-7" in messages[-1]["content"]
        assert harness.diagnostics == []
        assert harness.session.results == []
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_request_offers_no_action_without_a_grant_and_reads_the_view_afresh() -> None:
    """R5: the offered tools are the authorized read view, read for every turn.

    Replaces the allowlisted assertion that a ``chat.write`` grant reached
    the prompt: a ``chat.write`` grant changes deliverability, never the
    offered tools (R1/AC6). With no rule the model is offered nothing — the
    request carries no ``tools`` and no ``tool_choice`` (default deny as a
    surface); a read grant added between two runs reaches the next
    request's tools, so a policy change is never cached across runs, while
    the delivery grant added with it reaches no request (R1, decision 1).
    The view is re-read every *turn*, not every run: within one run, a
    ``chat.read`` grant appearing after turn 1 — whose proposal the
    executor refused, default deny — is in turn 2's tools. The per-scope
    view (AC6) is asserted with the loop, in ``tests/test_agentic_loop.py``.
    """

    harness = await activate_with(
        FakeResponse(200, completion("First")),
        FakeResponse(200, completion("Second")),
        grant=False,
        read=True,
    )
    try:
        await harness.send(message_id="one")
        await harness.completed(1)
        assert harness.tools(0) == []
        assert "tools" not in harness.requests()[0]["json"]
        assert "tool_choice" not in harness.requests()[0]["json"]
        assert CHAT_WRITE not in harness.prompt(0)[0]["content"]

        harness.executor._authorization.grant(BRAIN_GRANT)
        harness.executor._authorization.grant(READ_GRANT)
        await harness.send(message_id="two")
        await harness.completed(2)
        assert harness.tools(1) == [CHAT_READ]
        assert CHAT_WRITE not in harness.prompt(1)[0]["content"]
        assert harness.transport.sends[-1]["text"] == "Second"
    finally:
        await harness.close()

    per_turn = await activate_with(
        FakeResponse(200, tool_call(CHAT_READ, {"limit": 2})),
        FakeResponse(200, completion("Now I know")),
        read=True,
        read_grant=False,
    )

    def grant_once_refused(event: dict[str, Any]) -> None:
        # The grant appears once turn 1's call has terminated — after the
        # executor refused it — and before turn 2's offer is read.
        if event["payload"]["action"] == CHAT_READ:
            per_turn.executor._authorization.grant(READ_GRANT)

    per_turn.bus.subscribe(TRACE_ACTION_COMPLETED, grant_once_refused)
    try:
        await per_turn.send(message_id="three")
        (record,) = await per_turn.completed(1)
        assert record.status == "success"
        assert per_turn.tools(0) == []
        assert per_turn.tools(1) == [CHAT_READ]
        # Turn 1's proposal reached the executor and was refused there, the
        # provider uninvoked; the grant changed the next turn's offer only.
        refused = per_turn.executor.outcome(f"{record.run_id}/call-1")
        assert refused.status == "refused"
        assert refused.error["code"] == ERROR_NOT_AUTHORIZED
        assert per_turn.executor.provider_invocations == 1  # the delivery
        assert CHAT_WRITE not in per_turn.prompt(1)[0]["content"]
        assert per_turn.transport.sends[-1]["text"] == "Now I know"
        assert per_turn.transport.sends[-1]["call_id"] == f"{record.run_id}/call-2"
    finally:
        await per_turn.close()


@pytest.mark.asyncio
async def test_configured_request_strips_string_settings() -> None:
    """Replaces the synchronous variant (allowlisted; R1, R2, R3): run-driven.

    A padded model name and key are used stripped; a padded endpoint is no
    longer stripped but refused by the declared settings hook (R7), so the
    request is only ever sent to an endpoint exactly as configured.
    """

    padded = copy_settings(VALID_SETTINGS)
    padded["endpoint"] = f"  {SETTINGS['endpoint']}\n"
    assert validate_settings(padded) == [
        "module 'brain': field 'endpoint': must be a well-formed http(s) URL"
    ]

    harness = await activate_with(
        FakeResponse(200, completion("Hello")),
        settings_overrides={
            "model": f" {SETTINGS['model']} ",
            "api_key": f"{SETTINGS['api_key']}\n",
        },
    )
    try:
        await harness.send()
        await harness.completed()

        request = harness.requests()[0]
        assert request["url"] == SETTINGS["endpoint"]
        assert request["json"]["model"] == SETTINGS["model"]
        assert request["headers"]["Authorization"] == f"Bearer {SETTINGS['api_key']}"
        assert harness.diagnostics == []
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_ingestion_returns_before_the_model_answers_and_queues_the_next() -> None:
    """R2/AC6: validate, copy, admit, return — the handler never awaits the model.

    With the model held on a first message, publishing a second message of
    the same session completes at once; it is queued behind the held run,
    and both complete once the model is released.
    """

    session = HeldSession(
        FakeResponse(200, completion("One")), FakeResponse(200, completion("Two"))
    )
    harness = await activate_with(session=session)
    try:
        await harness.send(message_id="one")
        await asyncio.wait_for(session.entered.wait(), timeout=1)
        # The publication of a second message returns while the first is held.
        await asyncio.wait_for(harness.send(message_id="two"), timeout=1)
        assert len(session.post_calls) == 1
        assert harness.handle.scheduler.pending == 1
        assert harness.records() == []

        session.release.set()
        records = await harness.completed(2)
        assert [record.status for record in records] == ["success", "success"]
        assert len(session.post_calls) == 2
        assert [send["text"] for send in harness.transport.sends] == ["One", "Two"]
    finally:
        session.release.set()
        await harness.close()


@pytest.mark.asyncio
async def test_ingestion_refuses_events_without_trusted_identity_or_shape() -> None:
    """R3/AC12: no ``author.id``, no admission; a malformed event admits nothing."""

    harness = await activate_with(FakeResponse(200, completion("never")))
    try:
        untrusted = chat_payload(message_id="anonymous")
        untrusted["author"] = {"display_name": "Someone"}
        await harness.bus.publish(INPUT_EVENT, untrusted, {"source": "test"})
        await harness.bus.publish(
            INPUT_EVENT, {"text": "hello", "message_id": "v1", "chatter_id": "x"}, {}
        )
        await wait_until(lambda: len(harness.diagnostics) == 2)

        assert harness.diagnostics == [
            "brain input: no trusted viewer identity",
            "brain input: malformed chat message",
        ]
        assert harness.traces(TRACE_BRAIN_ADMISSION_ACCEPTED) == []
        assert harness.requests() == []
        assert harness.records() == []
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_only_an_accepted_recorded_trigger_decision_is_admitted() -> None:
    """R1/AC4: a rejected or missing decision admits nothing and calls no model."""

    triggers = FakeTriggerEngine()
    harness = await activate_with(FakeResponse(200, completion("Hello")), triggers=triggers)
    try:
        triggers.decide("rejected", accepted=False)
        await harness.send(message_id="rejected")
        await harness.send(message_id="undecided")
        await wait_until(lambda: len(harness.diagnostics) == 1)
        assert harness.diagnostics == ["brain input: no trigger decision recorded"]
        assert harness.traces(TRACE_BRAIN_ADMISSION_ACCEPTED) == []
        assert harness.requests() == []

        triggers.decide("accepted", accepted=True)
        await harness.send(message_id="accepted")
        (record,) = await harness.completed()
        assert record.status == "success"
        assert len(harness.requests()) == 1
        assert len(harness.transport.sends) == 1
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_a_scheduler_shared_through_the_context_is_fed_by_the_producer() -> None:
    """AC27: with a shared scheduler the bus copy is a fact, not a second admission."""

    scheduler = RecordingScheduler()
    harness = await activate_with(FakeResponse(200, completion("never")), scheduler=scheduler)
    try:
        assert harness.handle.owns_scheduler is False
        assert harness.handle.scheduler is scheduler
        await harness.send()
        await settle()
        assert scheduler.admissions == []
        assert harness.requests() == []
    finally:
        await harness.close()


# --------------------------------------------------------------------------- #
# Delivery (R5, AC19)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_reply_is_delivered_through_exactly_one_executor_call_and_one_send() -> None:
    """Replaces the tagged republication (allowlisted; R2, R5) — AC19.

    One admitted message: exactly 1 model call, exactly 1 executor call and
    exactly 1 send at the fake transport. The delivery is a ``chat.write``
    call carrying the engine's principal, the run's identity, the session's
    destination and the source message; the compatibility
    ``channel.chat.send`` route — subscribed here as another sink would be —
    carries 0 of it, so the two paths together produce exactly 1 send.
    """

    harness = await activate_with(FakeResponse(200, completion("Hello there")))
    harness.bus.subscribe(COMPATIBILITY_ROUTE, harness.compatibility_sends.append)
    try:
        await harness.send()
        (record,) = await harness.completed()

        assert len(harness.requests()) == 1
        assert harness.executor.provider_invocations == 1
        (send,) = harness.transport.sends
        assert send["text"] == "Hello there"
        assert send["principal"] == PRINCIPAL
        assert send["destination"] == Destination(PLATFORM, CHANNEL, CHAT_SCOPE)
        assert send["run_id"] == record.run_id
        assert send["call_id"] == f"{record.run_id}/call-1"
        assert send["source_event_id"] == "message-7"
        assert send["message_id"] == "message-7"
        assert harness.compatibility_sends == []
        assert harness.traces(COMPATIBILITY_ROUTE) == []

        assert record.status == "success"
        assert record.delivery == "success"
        assert record.model_calls == 1
        assert record.sends == 1
        (started,) = harness.traces(TRACE_ACTION_STARTED)
        (completed,) = harness.traces(TRACE_ACTION_COMPLETED)
        assert started["payload"]["run_id"] == completed["payload"]["run_id"] == record.run_id
        assert completed["payload"]["status"] == "success"
        assert harness.diagnostics == []
    finally:
        await harness.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content",
    [
        "[send:channel.chat.send]Hello",
        "[send:channel.chat.send]First\n[send:channel.chat.send]Second",
        "The literal [send:example is an incomplete routing tag.",
        "[send:unsupported.event]No",
        "plain untagged output",
    ],
)
async def test_reply_text_is_delivered_verbatim_with_no_tag_parsing(content: str) -> None:
    """Replaces the ``[send:...]`` section tests (allowlisted; R5).

    There is no tag encoding: the model's reply is one plain-text message
    delivered as written through the one executor call, whether or not it
    happens to contain what the former encoding would have parsed.
    """

    harness = await activate_with(FakeResponse(200, completion(content)))
    try:
        await harness.send()
        (record,) = await harness.completed()

        assert record.status == "success"
        assert [send["text"] for send in harness.transport.sends] == [content]
        assert harness.executor.provider_invocations == 1
        assert harness.traces(COMPATIBILITY_ROUTE) == []
        assert harness.diagnostics == []
    finally:
        await harness.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("result", "expected", "status"),
    [
        (FakeResponse(500, {"api_key": SETTINGS["api_key"]}), "non-success", "error"),
        (FakeResponse(200, {}), "malformed", "error"),
        (FakeResponse(200, completion("   ")), "unsupported shape", "error"),
        (asyncio.TimeoutError(SETTINGS["api_key"]), "timed out", "timeout"),
        (RuntimeError(SETTINGS["api_key"]), "transport failed", "error"),
    ],
)
async def test_model_failures_deliver_nothing_and_are_sanitized(
    result: Any, expected: str, status: str
) -> None:
    """Replaces the synchronous failure inspection (allowlisted; R2).

    The failure is the run's terminal state, read from its record and its
    one ``brain.run.completed``: delivery not attempted, 0 executor calls,
    0 sends, and one value-free diagnostic. A body that is no message at all
    is malformed data; a message carrying blank text and no tool call is
    "neither" under decision 2 — an unsupported shape, ``failure:
    unsupported_response_shape`` — which supersedes the former "malformed"
    reading of a blank reply (R2, decision 2).
    """

    harness = await activate_with(result)
    try:
        await harness.send()
        (record,) = await harness.completed()

        assert record.status == status
        assert record.delivery == DELIVERY_NOT_ATTEMPTED
        assert record.model_calls == 1
        assert record.sends == 0
        assert harness.executor.provider_invocations == 0
        assert harness.transport.sends == []
        assert len(harness.diagnostics) == 1
        assert expected in harness.diagnostics[0]
        assert_sanitized(harness.diagnostics)
        (completed,) = harness.traces(TRACE_BRAIN_RUN_COMPLETED)
        assert completed["payload"]["status"] == status
        assert completed["payload"]["delivery"] == DELIVERY_NOT_ATTEMPTED
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_token_budget_bounds_the_prompt_and_the_reply() -> None:
    """Design §3.4: ``budget.max_tokens`` bounds input and output together.

    A prompt that leaves the reply no room ends the run with 0 model calls;
    the request carries the room the prompt leaves as its output cap; and a
    reply whose reported usage puts the run over the bound is discarded.
    The failure is named as R3 names every spent budget — ``failure:
    budget_exhausted``, ``budget: max_tokens`` with the ``tokens`` and
    ``tokens_estimated`` the run counted (AC15) — which supersedes the
    phase-0 ``token_budget_exceeded`` failure name this test asserted.
    A spent token budget is a run ending without a delivered final
    response because of budget exhaustion, so the generic fallback follows
    (R3, AC13): ``fallback.text`` is the run's one send, ``fallback ==
    "sent"``, ``delivery == "fallback:success"`` and the ``status`` stays
    ``error`` — which supersedes the phase-0 ``not_attempted`` delivery and
    0 sends this test asserted before the fallback existed.
    """

    harness = await activate_with(
        FakeResponse(200, completion("Hello", usage={"total_tokens": 9000})),
        FakeResponse(200, completion("Hello")),
    )
    try:
        await harness.send(message_id="over")
        (over,) = await harness.completed(1)
        assert over.status == "error"
        assert over.delivery == FALLBACK_DELIVERY_PREFIX + "success"
        assert over.sends == 1
        (send,) = harness.transport.sends
        assert send["text"] == POLICY["fallback"]["text"]
        assert send["source_event_id"] == "over"
        (completed,) = harness.traces(TRACE_BRAIN_RUN_COMPLETED)
        assert completed["payload"]["status"] == "error"
        assert completed["payload"]["failure"] == "budget_exhausted"
        assert completed["payload"]["budget"] == "max_tokens"
        assert completed["payload"]["tokens"] == 9000
        assert completed["payload"]["tokens_estimated"] is False
        assert completed["payload"]["fallback"] == FALLBACK_SENT
        assert completed["payload"]["delivery"] == FALLBACK_DELIVERY_PREFIX + "success"
        request = harness.requests()[0]
        assert 0 < request["json"]["max_tokens"] < LIMITS["budget"]["max_tokens"]
        assert harness.diagnostics == ["brain model response: token budget exceeded"]
    finally:
        await harness.close()

    tiny = await activate_with(
        FakeResponse(200, completion("never")), settings_overrides={"budget": {"max_tokens": 8}}
    )
    try:
        await tiny.send()
        (record,) = await tiny.completed()
        assert record.status == "error"
        assert record.model_calls == 0
        assert record.sends == 1
        assert record.delivery == FALLBACK_DELIVERY_PREFIX + "success"
        assert [send["text"] for send in tiny.transport.sends] == [POLICY["fallback"]["text"]]
        assert tiny.requests() == []
        assert tiny.diagnostics == ["brain run: prompt exceeds the token budget"]
        completed = tiny.traces(TRACE_BRAIN_RUN_COMPLETED)[-1]["payload"]
        assert (completed["failure"], completed["budget"]) == ("budget_exhausted", "max_tokens")
        assert completed["tokens"] > 8 and completed["tokens_estimated"] is True
        assert completed["fallback"] == FALLBACK_SENT
    finally:
        await tiny.close()


@pytest.mark.asyncio
async def test_offered_tool_definitions_count_toward_the_prompt_token_budget() -> None:
    """Design §3.4: the tool definitions are input the backend reads, so they
    weigh on the prompt's estimate as the action listing did when it was
    system text. With nothing authorized, and so nothing offered, a budget
    composes and the request's output cap is what the text alone leaves;
    under the same budget, offering ``chat.read`` — name, description,
    schema — is what puts the prompt over: no request is sent. The delivery
    action, granted in both cases, weighs nothing: it is never offered (R1).
    """

    tool_tokens = _estimate_tool_tokens((CHAT_READ_SPEC,))
    assert tool_tokens > _estimate_tokens("")

    bare = await activate_with(
        FakeResponse(200, completion("fits")),
        settings_overrides={"budget": {"max_tokens": 200}},
    )
    try:
        await bare.send()
        await bare.completed()
        (request,) = bare.requests()
        body = request["json"]
        assert "tools" not in body
        text_tokens = sum(_estimate_tokens(message["content"]) for message in body["messages"])
        assert body["max_tokens"] == 200 - text_tokens
    finally:
        await bare.close()

    # Room for the text, none once the tool definition is counted with it.
    limit = text_tokens + tool_tokens // 2
    offered = await activate_with(
        FakeResponse(200, completion("never")),
        settings_overrides={"budget": {"max_tokens": limit}},
        read=True,
    )
    try:
        await offered.send()
        (record,) = await offered.completed()
        assert record.status == "error"
        assert record.model_calls == 0
        assert offered.requests() == []
        assert offered.diagnostics == ["brain run: prompt exceeds the token budget"]
    finally:
        await offered.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("outcome", "grant", "delivery", "code"),
    [
        (SENT, False, "refused", ERROR_NOT_AUTHORIZED),
        (FAIL_BEFORE_EMISSION, True, "error", ERROR_PROVIDER_FAILED),
        (FAIL_AFTER_EMISSION, True, "external_unknown", ERROR_EXTERNAL_UNKNOWN),
        (TIMEOUT_BEFORE_EMISSION, True, "timeout", ERROR_TIMED_OUT),
    ],
)
async def test_only_a_success_observation_writes_back_to_memory(
    outcome: str, grant: bool, delivery: str, code: str
) -> None:
    """Replaces the bus-outcome and ``delivery_status`` history tests (allowlisted; R5).

    The delivery outcome is the executor's explicit observation, never a bus
    publication result or a mutated field: a refused, failed, uncertain or
    timed-out delivery is reported as such in the run's record and trace,
    and writes nothing into memory, so the next request carries 0 assistant
    messages — the model is never told it said something it did not.
    """

    harness = await activate_with(
        FakeResponse(200, completion("Undelivered reply")),
        FakeResponse(200, completion("Next reply")),
        grant=grant,
        transport=FakeTransport(outcome),
    )
    try:
        await harness.send("First question", message_id="one")
        (first,) = await harness.completed(1)
        assert first.status == delivery
        assert first.delivery == delivery
        assert first.sends == 0
        assert harness.transport.sends == []
        (completed,) = harness.traces(TRACE_BRAIN_RUN_COMPLETED)
        assert completed["payload"]["delivery"] == delivery
        assert completed["payload"]["delivery_error"] == code
        assert harness.executor.provider_invocations == (0 if not grant else 1)

        await harness.send("Next question", message_id="two")
        await harness.completed(2)
        prior = harness.prompt(1)[1:-1]
        assert prior == []
        assert "Undelivered reply" not in "\n".join(m["content"] for m in harness.prompt(1))
        assert harness.diagnostics == []
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_run_lifecycle_traces_are_published_by_the_scheduler_only() -> None:
    """R8: one admitted message, exactly 1 ``brain.run.started`` and exactly 1
    ``brain.run.completed``, both published by the scheduler; the engine
    itself publishes 0 events of either type, and its only publication of
    its own is the one ``brain.delivery.resolved`` of ``prepare`` (R1),
    before any run.
    """

    bus = PublisherRecordingBus()
    harness = await activate_with(FakeResponse(200, completion("Hello")), bus=bus)
    try:
        await harness.send()
        (record,) = await harness.completed()
        await wait_until(lambda: len(harness.traces(TRACE_BRAIN_RUN_COMPLETED)) == 1)

        assert len(harness.traces(TRACE_BRAIN_RUN_STARTED)) == 1
        assert len(harness.traces(TRACE_BRAIN_RUN_COMPLETED)) == 1
        run_publishers = [
            publisher for event_type, publisher in bus.publishers if event_type in RUN_TRACES
        ]
        assert run_publishers == ["core.admission", "core.admission"]
        assert [
            event_type
            for event_type, publisher in bus.publishers
            if publisher.startswith("modules.brain")
        ] == [TRACE_DELIVERY_RESOLVED]
        assert bus.publishers[0][0] == TRACE_DELIVERY_RESOLVED
        (completed,) = harness.traces(TRACE_BRAIN_RUN_COMPLETED)
        assert completed["payload"]["run_id"] == record.run_id
        assert completed["payload"]["status"] == "success"
        assert completed["payload"]["delivery"] == "success"
        assert completed["payload"]["model_calls"] == 1
        assert completed["payload"]["sends"] == 1
        assert completed["payload"]["action_calls"] == 1
        assert completed["payload"]["deliveries"] == [
            {
                "action": CHAT_WRITE,
                "call_id": f"{record.run_id}/call-1",
                "text": True,
                "status": "success",
            }
        ]
    finally:
        await harness.close()


# --------------------------------------------------------------------------- #
# Concurrency and shutdown (R2)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_model_requests_for_different_sessions_overlap_within_the_worker_cap() -> None:
    """Replaces the unbounded overlap test (allowlisted; R2).

    Two sessions' model calls overlap on 2 workers while a third message of
    the first session waits behind its held run: overlap is bounded by the
    worker count and by one active run per session, never unbounded.
    """

    session = HeldSession(
        FakeResponse(200, completion("One")),
        FakeResponse(200, completion("Two")),
        FakeResponse(200, completion("Three")),
    )
    harness = await activate_with(session=session, settings_overrides={"admission": {"workers": 2}})
    try:
        await harness.send(message_id="one", viewer_id="viewer-one")
        await harness.send(message_id="two", viewer_id="viewer-two")
        await harness.send(message_id="three", viewer_id="viewer-one")
        await wait_until(lambda: len(session.post_calls) == 2)
        assert harness.handle.scheduler.active_runs == 2
        assert harness.handle.scheduler.pending == 1

        session.release.set()
        records = await harness.completed(3)
        assert [record.status for record in records] == ["success"] * 3
        assert len(session.post_calls) == 3
        assert harness.diagnostics == []
    finally:
        session.release.set()
        await harness.close()


@pytest.mark.asyncio
async def test_close_ends_a_held_run_cancelled_with_its_model_call_counted() -> None:
    """R2/R4: close cancels the run still in its model call; the run is
    recorded exactly once, ``cancelled``, with the call it already issued
    counted; the session is closed once however many times close is called.
    """

    session = HeldSession(FakeResponse(200, completion("never")))
    harness = await activate_with(session=session)
    await harness.send()
    await asyncio.wait_for(session.entered.wait(), timeout=1)

    await asyncio.gather(harness.close(), harness.close())

    (record,) = harness.records()
    assert record.status == "cancelled"
    assert record.reason == REASON_CANCELLED
    assert record.model_calls == 1
    assert record.sends == 0
    assert len(harness.traces(TRACE_BRAIN_RUN_COMPLETED)) == 1
    assert harness.transport.sends == []
    assert session.close_calls == 1


async def test_a_confirmation_released_after_the_deadline_never_becomes_memory() -> None:
    """P24 gate F1 / AC33: a send still unconfirmed at expiry stays so.

    Scheduler, real brain engine and real executor share one manual clock.
    The send is on the wire when the budget ends; the transport then confirms
    — successfully — only after the clock has passed the total deadline. The
    confirmation is released in the same synchronous block as the advance, so
    the executor's race finds both the provider and its timer already ready:
    the late success must not be adopted, nothing may reach memory, and the
    run still ends ``timeout``/``run_deadline`` with zero sends.
    """

    clock = ManualClock()
    bus = EventBus()
    counters = Counters()
    policy = AuthorizationPolicy([BRAIN_GRANT])
    actions = ActionRegistry(authorization=policy)
    release = asyncio.Event()
    requests: list[str] = []

    class LateConfirmingProvider:
        name = "late-sender"

        async def invoke(self, invocation: Any) -> ActionObservation:
            requests.append(invocation.call.arguments["text"])
            invocation.mark_emitted()
            await release.wait()
            return ActionObservation(
                status="success",
                provenance={"provider": self.name},
                result={"message_id": "late-1"},
            )

    actions.register(CHAT_WRITE_SPEC, LateConfirmingProvider(), module=SENDER_MODULE)
    actions.mark_ready(SENDER_MODULE)
    supervision = Supervision(bus, counters=counters)
    executor = ActionExecutor(
        actions,
        policy,
        supervision=supervision,
        counters=counters,
        clock=clock,
        sleeper=clock.sleep,
    )
    triggers = FakeTriggerEngine()
    triggers.decide("late-1", accepted=True)
    context = RuntimeContext(
        bus=bus,
        actions=actions,
        supervision=supervision,
        tasks=SupervisedTasks(),
        executor=executor,
        triggers=triggers,
        clock=clock,
    )
    diagnostics: list[str] = []
    # The queued wait may not exceed the total it is part of (R3, AC20), so
    # the short total takes a shorter wait with it; the run starts at once,
    # so the wait itself is never reached here.
    settings = merge_settings(
        {"admission": {"total_run_seconds": 10.0, "wait_seconds": 5.0}}
    )
    settings.update(
        {
            "_session_factory": lambda: FakeSession(
                FakeResponse(200, completion("On the wire"))
            ),
            "_sleeper": clock.sleep,
            "diagnostic_reporter": diagnostics.append,
        }
    )
    handle = await activate(context.for_module(MODULE_NAME), settings, {})
    try:
        await handle.prepare()
        await bus.publish(
            INPUT_EVENT,
            chat_payload("Hold the reply", message_id="late-1"),
            {"source": "test", "schema_version": 2},
        )
        await wait_until(lambda: requests == ["On the wire"])

        # Past the deadline BEFORE the response is released — and released in
        # the same synchronous block, so the resumed executor faces both
        # futures ready and cannot order the confirmation first by itself.
        release.set()
        clock.advance(11.0)
        await wait_until(lambda: len(handle.scheduler.run_records()) == 1)

        (record,) = handle.scheduler.run_records().values()
        assert (record.status, record.reason) == ("timeout", REASON_RUN_DEADLINE)
        assert record.sends == 0

        # Zero success observations for the expired send (R2/AC33).
        statuses = [value.status for value in executor.outcomes().values()]
        assert statuses == ["external_unknown"]
        assert statuses.count("success") == 0
        assert executor.provider_invocations == 1

        # The late reply never reaches memory, so the model is never told it
        # said something the run recorded as unconfirmed.
        assert handle.memory.sessions() == ()
        assert handle.memory.recall(SessionKey(PLATFORM, CHANNEL, VIEWER)) == ()

        # Emitted exactly once, never replayed.
        assert requests == ["On the wire"]
        (completed,) = events_of(bus, TRACE_BRAIN_RUN_COMPLETED)
        assert completed["payload"]["status"] == "timeout"
        assert completed["payload"]["reason"] == REASON_RUN_DEADLINE
        assert completed["payload"]["sends"] == 0
        assert diagnostics == []
    finally:
        await handle.close()


# --------------------------------------------------------------------------- #
# Conversation memory (R3, R6, AC10, AC30)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_memory_is_keyed_by_session_key_not_by_viewer() -> None:
    """Replaces the viewer-keyed history tests (allowlisted; R3) — AC10.

    The same ``viewer_id`` on 2 platforms and in 2 channels holds 4 distinct
    memories, each request carrying 0 exchanges of the other 3; 2 viewers in
    1 channel hold 2 distinct memories. Memory is confirmed text only: a
    request carries what the send edge confirmed and nothing else (R5).
    """

    sessions = [
        (PLATFORM, CHANNEL, VIEWER),
        (OTHER_PLATFORM, CHANNEL, VIEWER),
        (PLATFORM, OTHER_CHANNEL, VIEWER),
        (OTHER_PLATFORM, OTHER_CHANNEL, VIEWER),
        (PLATFORM, CHANNEL, "viewer-other"),
    ]
    responses = [
        FakeResponse(200, completion(f"reply-{index}-{turn}"))
        for turn in range(2)
        for index in range(len(sessions))
    ]
    harness = await activate_with(*responses)
    try:
        for turn in range(2):
            for index, (platform, channel_id, viewer_id) in enumerate(sessions):
                await harness.send(
                    f"question-{index}-{turn}",
                    message_id=f"m-{index}-{turn}",
                    viewer_id=viewer_id,
                    channel_id=channel_id,
                    platform=platform,
                )
                await harness.completed(turn * len(sessions) + index + 1)

        assert len(harness.handle.memory.sessions()) == 5
        for index, (platform, channel_id, viewer_id) in enumerate(sessions):
            history = rendered_history(harness.prompt(len(sessions) + index))
            assert f"question-{index}-0" in history
            assert f"reply-{index}-0" in history
            for other in range(len(sessions)):
                if other != index:
                    assert f"question-{other}-0" not in history
                    assert f"reply-{other}-0" not in history
            key = SessionKey(platform=platform, channel_id=channel_id, viewer_id=viewer_id)
            assert [
                (exchange.assistant) for exchange in harness.handle.memory.recall(key)
            ] == [f"reply-{index}-0", f"reply-{index}-1"]
        assert all(record.status == "success" for record in harness.records())
        assert harness.diagnostics == []
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_memory_bounds_evict_through_the_configured_limits() -> None:
    """AC30 through the engine: the configured session cap evicts the least
    recently used session, so the evicted viewer's next request carries 0
    earlier exchanges while a retained viewer's still carries its own.
    """

    harness = await activate_with(
        *(FakeResponse(200, completion(f"reply-{index}")) for index in range(5)),
        settings_overrides={"conversation_memory": {"max_sessions": 2}},
    )
    try:
        await harness.send("evicted viewer question", message_id="one", viewer_id="viewer-one")
        await harness.completed(1)
        await harness.send("second viewer question", message_id="two", viewer_id="viewer-two")
        await harness.completed(2)
        await harness.send("third viewer question", message_id="three", viewer_id="viewer-three")
        await harness.completed(3)
        assert len(harness.handle.memory.sessions()) == 2

        await harness.send("viewer one returns", message_id="four", viewer_id="viewer-one")
        await harness.completed(4)
        assert rendered_history(harness.prompt(3)) == ""
        # Viewer one's return evicted the next least recently used session
        # (viewer two); viewer three, used more recently, keeps its own.
        assert len(harness.handle.memory.sessions()) == 2
        await harness.send("viewer three returns", message_id="five", viewer_id="viewer-three")
        await harness.completed(5)
        assert "third viewer question" in rendered_history(harness.prompt(4))
        assert "reply-2" in rendered_history(harness.prompt(4))
        assert harness.diagnostics == []
    finally:
        await harness.close()


def memory_of(clock: ManualClock) -> ConversationMemory:
    return ConversationMemory(
        max_sessions=2, max_exchanges=3, max_bytes=200, max_age_seconds=10, clock=clock
    )


def key_of(viewer_id: str) -> SessionKey:
    return SessionKey(platform=PLATFORM, channel_id=CHANNEL, viewer_id=viewer_id)


def test_memory_session_cap_evicts_exactly_the_least_recently_used_session() -> None:
    """AC30: 2 sessions retained; a 3rd evicts exactly 1, the silent longest."""

    clock = ManualClock(0.0)
    memory = memory_of(clock)
    memory.remember(key_of("a"), "qa", "ra")
    memory.remember(key_of("b"), "qb", "rb")
    memory.recall(key_of("a"))  # ``a`` is now the most recently used.

    memory.remember(key_of("c"), "qc", "rc")

    assert len(memory.sessions()) == 2
    assert memory.recall(key_of("b")) == ()
    assert [exchange.assistant for exchange in memory.recall(key_of("a"))] == ["ra"]
    assert [exchange.assistant for exchange in memory.recall(key_of("c"))] == ["rc"]


def test_memory_exchange_cap_keeps_the_newest_three() -> None:
    """AC30: a 4th exchange leaves exactly 3, the oldest absent."""

    memory = memory_of(ManualClock(0.0))
    for index in range(4):
        memory.remember(key_of("a"), f"q{index}", f"r{index}")

    assert [exchange.assistant for exchange in memory.recall(key_of("a"))] == ["r1", "r2", "r3"]


def test_memory_byte_cap_keeps_at_most_200_bytes() -> None:
    """AC30: exchanges totalling more than 200 bytes leave fewer than 3
    retained and at most 200 bytes; one exchange over the bound alone is
    not retained at all — the bound wins over the memory.
    """

    memory = memory_of(ManualClock(0.0))
    for index in range(3):
        memory.remember(key_of("a"), "q" * 40, f"{index}" * 40)

    retained = memory.recall(key_of("a"))
    assert 0 < len(retained) < 3
    assert sum(exchange.size for exchange in retained) <= 200
    assert retained[-1].assistant == "2" * 40

    memory.remember(key_of("a"), "x" * 150, "y" * 100)
    assert memory.recall(key_of("a")) == ()


def test_memory_age_bound_drops_expired_exchanges_and_keeps_later_ones() -> None:
    """AC30: 11 time units later 0 exchanges remain; one added afterwards is kept."""

    clock = ManualClock(0.0)
    memory = memory_of(clock)
    memory.remember(key_of("a"), "old question", "old reply")
    memory.remember(key_of("a"), "older question", "older reply")

    clock.advance(11)
    assert memory.recall(key_of("a")) == ()
    memory.remember(key_of("a"), "new question", "new reply")
    assert [exchange.assistant for exchange in memory.recall(key_of("a"))] == ["new reply"]
    assert memory.sessions() == (key_of("a").serialize(),)


@pytest.mark.parametrize(
    ("limits", "field_name"),
    [
        ({"max_sessions": 0}, "max_sessions"),
        ({"max_exchanges": 2.5}, "max_exchanges"),
        ({"max_bytes": True}, "max_bytes"),
        ({"max_age_seconds": float("inf")}, "max_age_seconds"),
    ],
)
def test_memory_refuses_absent_or_non_finite_bounds(limits: dict[str, Any], field_name: str) -> None:
    """R6: every bound is required, finite and positive at construction."""

    settings = {"max_sessions": 2, "max_exchanges": 3, "max_bytes": 200, "max_age_seconds": 10, **limits}
    with pytest.raises(BrainModuleError, match=f"conversation_memory.{field_name}"):
        ConversationMemory(**settings)


# --------------------------------------------------------------------------- #
# The shared harness (P7): the doubles every brain suite reads
# --------------------------------------------------------------------------- #


def test_tool_call_builder_yields_one_call_with_json_encoded_arguments() -> None:
    """R2 support: ``tool_call`` is one Chat Completions tool call whose
    arguments travel JSON-encoded, as the backend sends them."""

    body = tool_call("chat.read", {"limit": 3, "channel_id": CHANNEL}, usage={"total_tokens": 7})

    (choice,) = body["choices"]
    (entry,) = choice["message"]["tool_calls"]
    assert entry["type"] == "function"
    assert entry["function"]["name"] == "chat.read"
    assert isinstance(entry["function"]["arguments"], str)
    assert json.loads(entry["function"]["arguments"]) == {"limit": 3, "channel_id": CHANNEL}
    assert body["usage"] == {"total_tokens": 7}
    assert completion("Hello") == {"choices": [{"message": {"content": "Hello"}}]}


@pytest.mark.asyncio
async def test_session_without_probe_traffic_records_no_probe_and_keeps_post_calls() -> None:
    """R2 support: probe detection keys on the request body, not on call
    order — a scenario request is never mistaken for a probe, so
    ``probe_calls`` stays empty and ``post_calls`` counts every scenario
    request; a forced call on the probe tool is answered from
    ``probe_results`` without consuming ``results``."""

    session = FakeSession(FakeResponse(200, completion("One")), FakeResponse(200, completion("Two")))

    first = await session.post("endpoint", json={"model": "m", "messages": [{"role": "user", "content": "hi"}]})
    assert (await first.json()) == completion("One")
    assert session.probe_calls == []
    assert len(session.post_calls) == 1
    assert len(session.results) == 1

    probe = await session.post(
        "endpoint",
        json={
            "model": "m",
            "messages": [{"role": "user", "content": "probe"}],
            "tools": [{"type": "function", "function": {"name": PROBE_TOOL, "parameters": {}}}],
            "tool_choice": {"type": "function", "function": {"name": PROBE_TOOL}},
        },
    )
    (entry,) = (await probe.json())["choices"][0]["message"]["tool_calls"]
    assert entry["function"]["name"] == PROBE_TOOL
    assert len(session.probe_calls) == 1
    assert len(session.post_calls) == 1
    assert len(session.results) == 1


@pytest.mark.asyncio
async def test_memory_websocket_pair_delivers_frames_in_order_and_propagates_close_code() -> None:
    """R6 support: frames cross the pair in the order they were sent, text
    and binary alike; a ``close(code)`` reaches the peer as a ``CLOSE``
    message carrying that code, after which the peer reports it as its own
    ``close_code`` and every further receive is ``CLOSED``."""

    pair = MemoryWebSocketPair()
    await pair.client.send_str('{"v": 1, "type": "hello", "id": "h1"}')
    await pair.client.send_bytes(b"\x89PNG")
    await pair.client.send_str("second")
    assert not pair.server.closed

    received = [await pair.server.receive() for _ in range(3)]
    assert [message.type for message in received] == [WSMsgType.TEXT, WSMsgType.BINARY, WSMsgType.TEXT]
    assert [message.data for message in received] == [
        '{"v": 1, "type": "hello", "id": "h1"}',
        b"\x89PNG",
        "second",
    ]

    assert await pair.server.close(code=4401) is True
    assert pair.server.closed and pair.server.close_code == 4401
    closing = await pair.client.receive()
    assert closing.type == WSMsgType.CLOSE and closing.data == 4401
    assert pair.client.closed and pair.client.close_code == 4401
    assert (await pair.client.receive()).type == WSMsgType.CLOSED
    with pytest.raises(ConnectionResetError):
        await pair.client.send_str("late")

    # Closing an end wakes its own receiver already parked on the inbox, so a
    # receive loop on the closing side terminates during shutdown.
    parked = MemoryWebSocketPair()
    waiting = asyncio.ensure_future(parked.server.receive())
    await settle()
    assert not waiting.done()
    assert await parked.server.close() is True
    assert (await asyncio.wait_for(waiting, 1)).type == WSMsgType.CLOSED
    assert (await parked.server.receive()).type == WSMsgType.CLOSED

    dropped = MemoryWebSocketPair()
    await dropped.server.send_str("in flight")
    dropped.drop()
    assert (dropped.server.close_code, dropped.client.close_code) == (1006, 1006)
    assert (await dropped.client.receive()).data == "in flight"
    assert (await dropped.client.receive()).type == WSMsgType.CLOSED
