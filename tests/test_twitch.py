from __future__ import annotations

import asyncio
import dataclasses
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from core.actions import AuthorizationPolicy, AuthorizationRule
from core.bus import EventBus
from core.context import ChatContext
from core.contracts import (
    EVENT_KINDS,
    TRACE_MODULE_DEGRADED,
    COUNTER_DEDUP_EVICTIONS,
    COUNTER_LOST_TRACES,
    COUNTER_TRIGGER_REJECTIONS,
    ActionCall,
    Destination,
    SessionKey,
    TriggerPolicy,
    TriggerRule,
    TriggerSpec,
    TriggerTypeDeclaration,
)
from core.lifecycle import PhaseCoordinator
from core.loader import ModuleLoader
from core.runtime import ModuleContext, RuntimeContext, ServiceRegistryError
from core.triggers import TriggerContext, TriggerRegistry
from conftest import (
    FakeResponse,
    ManualClock,
    RecordingScheduler,
    events_of,
    helix_clip_created,
    helix_clip_listed,
    helix_clips_empty,
    helix_message_deleted,
    helix_not_live,
    helix_refusal,
    helix_timeout_applied,
    runtime_context as build_context,
    trace_texts,
    wait_until,
)
from modules.twitch import (
    CHAT_WRITE_ACTION,
    CHAT_WRITE_PROVIDER,
    EVENTSUB_SUBSCRIPTIONS_URL,
    EVENTSUB_URL,
    HELIX_CHAT_URL,
    HELIX_CLIPS_URL,
    HELIX_MODERATION_BANS_URL,
    HELIX_MODERATION_CHAT_URL,
    MANIFEST_PATH,
    NOTICE_KINDS,
    PLATFORM,
    TOKEN_VALIDATION_URL,
    TwitchModule,
    TwitchModuleError,
    ClipLookupError,
    activate,
    validate_settings,
)


ROOT = Path(__file__).parents[1]

SETTINGS = {
    "client_id": "configured-client",
    "client_secret": "never-show-client-secret",
    "access_token": "never-show-access-token",
    "broadcaster_id": "broadcaster-42",
    "bot_user_id": "bot-24",
    "companion_name": "Companion",
}


class FakeWebSocket:
    def __init__(self, *frames: Any) -> None:
        self.frames: asyncio.Queue[Any] = asyncio.Queue()
        for frame in frames:
            self.frames.put_nowait(frame)
        self.close_calls = 0
        self.close_code: int | None = None

    async def receive(self) -> Any:
        return await self.frames.get()

    async def close(self) -> None:
        self.close_calls += 1

    def feed(self, frame: Any) -> None:
        self.frames.put_nowait(frame)


class FakeSession:
    def __init__(
        self,
        websockets: list[FakeWebSocket],
        *,
        validation: FakeResponse | None = None,
        subscriptions: list[FakeResponse] | None = None,
        sends: list[Any] | None = None,
    ) -> None:
        self.websockets = list(websockets)
        self.validation = validation or FakeResponse(
            200,
            {"client_id": SETTINGS["client_id"], "user_id": SETTINGS["bot_user_id"]},
        )
        self.subscriptions = subscriptions or [
            FakeResponse(202, {"data": [{"id": "subscription"}]})
        ]
        self.sends = list(sends or [])
        self.get_calls: list[dict[str, Any]] = []
        self.post_calls: list[dict[str, Any]] = []
        self.ws_calls: list[str] = []
        self.close_calls = 0

    async def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.get_calls.append({"url": url, **kwargs})
        return self.validation

    async def post(self, url: str, **kwargs: Any) -> FakeResponse:
        call = {"url": url, **kwargs}
        self.post_calls.append(call)
        if url == EVENTSUB_SUBSCRIPTIONS_URL:
            return self.subscriptions.pop(0)
        if url == HELIX_CHAT_URL:
            result = self.sends.pop(0)
            if isinstance(result, BaseException):
                raise result
            return result
        raise AssertionError(f"unexpected POST URL: {url}")

    async def ws_connect(self, url: str) -> FakeWebSocket:
        self.ws_calls.append(url)
        return self.websockets.pop(0)

    async def close(self) -> None:
        self.close_calls += 1


def welcome(session_id: str) -> dict[str, Any]:
    return {
        "metadata": {"message_type": "session_welcome"},
        "payload": {"session": {"id": session_id}},
    }


def notification(message_id: str, text: str = "hello") -> dict[str, Any]:
    return {
        "metadata": {
            "message_type": "notification",
            "subscription_type": "channel.chat.message",
        },
        "payload": {
            "subscription": {"type": "channel.chat.message"},
            "event": {
                "broadcaster_user_id": SETTINGS["broadcaster_id"],
                "chatter_user_id": "viewer-7",
                "chatter_user_name": "ViewerName",
                "message_id": message_id,
                "message": {"text": text},
            },
        },
    }


def close_frame(code: int) -> Any:
    return SimpleNamespace(type="close", data=code)


async def no_delay(_: float) -> None:
    await asyncio.sleep(0)


def manifest() -> dict[str, Any]:
    return yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))


def manifest_trigger_spec() -> TriggerSpec:
    """The trigger declaration of the real manifest, built by public contracts."""

    declared = manifest()["triggers"]
    default = declared["default_policy"]
    return TriggerSpec(
        types=tuple(
            TriggerTypeDeclaration(
                name=entry["name"], parameter_schema=entry["parameter_schema"]
            )
            for entry in declared["types"]
        ),
        combinations=tuple(declared["combinations"]),
        default_policy=TriggerPolicy(
            rules=tuple(
                TriggerRule(type=rule["type"], parameters=rule["parameters"])
                for rule in default["rules"]
            ),
            combination=default["combination"],
        ),
    )


def runtime_context(
    bus: EventBus | None = None,
    *,
    clock: Any = None,
    dedup_max_entries: int = 8,
    dedup_ttl_seconds: float = 60.0,
    scheduler: Any = None,
    declare_triggers: bool = True,
    authorization: AuthorizationPolicy | None = None,
    policy: TriggerPolicy | None = None,
) -> RuntimeContext:
    """The shared fixture (``conftest.runtime_context``) carrying this
    module's declarations: the manifest's trigger spec registered for the
    ``twitch`` input and the chat context bounds its ingestion feeds.

    ``declare_triggers`` registers the manifest's trigger declaration, as
    the loader would; a test that runs the real loader passes ``False`` so
    the loader can register it itself. The executor, supervision, counters
    and tasks are the shared assembly — no twitch-specific wiring here.
    ``policy`` is a configured trigger policy for the ``twitch`` input.
    """

    registry = TriggerRegistry(companion_name=SETTINGS["companion_name"])
    if declare_triggers:
        registry.register(
            "twitch", manifest_trigger_spec(), companion_name=SETTINGS["companion_name"]
        )
        if policy is not None:
            registry.configure("twitch", policy)
    target_clock = clock if clock is not None else ManualClock()
    return build_context(
        bus,
        clock=target_clock,
        scheduler=scheduler,
        authorization=authorization,
        trigger_registry=registry,
        dedup_max_entries=dedup_max_entries,
        dedup_ttl_seconds=dedup_ttl_seconds,
        chat=ChatContext(
            max_messages=16, max_bytes=8192, max_age_seconds=600.0, clock=target_clock
        ),
    )


def module_context(bus: EventBus | None = None, **options: Any) -> ModuleContext:
    return runtime_context(bus, **options).for_module("twitch")


def chat_events(bus: EventBus) -> list[dict[str, Any]]:
    return events_of(bus, "channel.chat.message")


async def start_module(
    settings: dict[str, Any],
    bus: EventBus | None = None,
    context: ModuleContext | None = None,
):
    """Activate and run the startup phases the coordinator would run (R4).

    A failing phase unwinds through ``close()`` exactly as the coordinator
    does before the failure propagates.
    """

    handle = await activate(context or module_context(bus), settings, {})
    try:
        await handle.prepare()
        await handle.start_inputs()
    except BaseException:
        await handle.close()
        raise
    return handle


async def activate_with(
    session: FakeSession,
    bus: EventBus | None = None,
    diagnostics: list[str] | None = None,
    context: ModuleContext | None = None,
):
    target_bus = context.bus if context is not None else (bus or EventBus())
    target_diagnostics = diagnostics if diagnostics is not None else []
    settings = {
        **SETTINGS,
        "_session_factory": lambda: session,
        "_retry_delay": no_delay,
        "diagnostic_reporter": target_diagnostics.append,
    }
    handle = await start_module(settings, target_bus, context)
    return handle, target_bus, target_diagnostics


def assert_sanitized(diagnostics: list[str]) -> None:
    rendered = "\n".join(diagnostics)
    assert SETTINGS["client_secret"] not in rendered
    assert SETTINGS["access_token"] not in rendered


def test_manifest_declares_twitch_source_and_sink() -> None:
    """R7/R1/R5: the manifest is v2 — the former 4-key equality is superseded.

    It declares the runtime contract it is built against, the ``input`` role,
    a settings schema and the hook this package implements, the trigger
    types with their parameter schemas, the combination operators, exactly
    one default policy that names the companion only by token, and the
    ``chat.write`` action contract carrying the delivery capability under
    ``text`` (R1 decision 1, AC58) and nothing else.

    Rewritten by phase 3 R1 (plan P7): the settings gain the optional
    ``notices`` (its ``kinds`` the six Twitch notice kinds, not required) and
    the trigger types gain ``event_kind`` with the built-in schema; the
    default policy, the action and the credentials are unchanged.
    """

    from core.runtime import RUNTIME_API

    manifest_path = ROOT / "modules" / "twitch" / "module.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))

    assert manifest["name"] == "twitch"
    assert manifest["manifest_version"] == 2
    assert manifest["runtime_api"] == RUNTIME_API
    assert manifest["produces"] == ["channel.chat.message"]
    assert manifest["consumes"] == ["channel.chat.send"]
    assert manifest["middleware"] is False
    assert manifest["lifecycle"] == {"roles": ["input"]}

    schema = manifest["settings_schema"]
    assert set(schema["required"]) == set(SETTINGS)
    assert set(schema["properties"]) == set(SETTINGS) | {"notices"}
    notices = schema["properties"]["notices"]
    assert notices["type"] == "object"
    assert set(notices["properties"]) == {"kinds"}
    assert notices["properties"]["kinds"]["items"]["enum"] == list(NOTICE_KINDS)
    assert notices["properties"]["kinds"]["items"]["enum"] == [
        "sub", "resub", "sub_gift", "community_sub_gift", "raid", "follow",
    ]
    assert manifest["settings_validator"] == "validate_settings"
    assert callable(validate_settings)
    assert manifest["credentials"] == ["client_id", "client_secret", "access_token"]

    triggers = manifest["triggers"]
    declared = {declaration["name"]: declaration for declaration in triggers["types"]}
    assert set(declared) == {"probability", "audience", "keyword", "event_kind"}
    for declaration in declared.values():
        assert declaration["parameter_schema"]["type"] == "object"
    assert declared["event_kind"]["parameter_schema"] == {
        "type": "object",
        "properties": {
            "kinds": {"type": "array", "items": {"type": "string", "enum": list(EVENT_KINDS)}}
        },
        "required": ["kinds"],
        "additionalProperties": False,
    }
    assert set(triggers["combinations"]) == {"all_of", "any_of", "none_of"}
    default_policy = triggers["default_policy"]
    assert default_policy == {
        "combination": "all_of",
        "rules": [
            {"type": "keyword", "parameters": {"keywords": ["${companion_name}"]}}
        ],
    }
    assert SETTINGS["companion_name"] not in manifest_path.read_text(encoding="utf-8")

    (action,) = manifest["actions"]
    assert action["name"] == "chat.write"
    assert action["version"] == 1
    assert action["nature"] == "write"
    assert action["required_permissions"] == ["chat.write"]
    assert action["supported_destinations"] == [
        {"platform": "twitch", "channel_id": "*", "scope": "chat"}
    ]
    assert action["argument_schema"]["required"] == ["text"]
    assert set(action["result_schema"]["required"]) == {"message_id", "destination"}
    assert action["timeout_seconds"] == 10
    assert action["idempotency"] == "none"
    assert action["delivery"] == {"text_argument": "text"}
    assert set(action) == {
        "name",
        "version",
        "description",
        "argument_schema",
        "result_schema",
        "nature",
        "required_permissions",
        "supported_destinations",
        "timeout_seconds",
        "idempotency",
        "delivery",
    }
    assert set(manifest) == {
        "name",
        "manifest_version",
        "runtime_api",
        "produces",
        "consumes",
        "middleware",
        "lifecycle",
        "settings_schema",
        "settings_validator",
        "credentials",
        "triggers",
        "actions",
    }


def test_settings_hook_names_module_and_field_without_values() -> None:
    """R7/AC24: one diagnostic per offending field; no configured value echoed."""

    assert validate_settings(SETTINGS) == []

    invalid = {**SETTINGS, "companion_name": "   "}
    del invalid["broadcaster_id"]
    diagnostics = validate_settings(invalid)

    assert diagnostics == [
        "module 'twitch': field 'broadcaster_id': must be a non-empty string",
        "module 'twitch': field 'companion_name': must be a non-empty string",
    ]
    assert_sanitized(diagnostics)
    assert validate_settings(["not", "a", "mapping"]) == [
        "module 'twitch': field 'settings': must be a mapping"
    ]


@pytest.mark.asyncio
async def test_loader_activates_the_v2_manifest_and_coordinator_drives_the_phases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R4/R7: the manifest's declarations and this package load together.

    The real loader resolves the declared hook, validates the settings, records
    the declarations and activates through the context; the coordinator then
    opens the source only after the readiness barrier and releases every
    transport on stop.
    """

    websocket = FakeWebSocket(welcome("session-1"))
    session = FakeSession([websocket])
    diagnostics: list[str] = []
    context = runtime_context(declare_triggers=False)
    loader = ModuleLoader(context.bus, ROOT / "modules", context=context, environ={})
    settings = {
        **SETTINGS,
        "_session_factory": lambda: session,
        "_retry_delay": no_delay,
        "diagnostic_reporter": diagnostics.append,
    }

    activations = await loader.activate_enabled(
        {"enabled_modules": ["twitch"], "modules": {"twitch": settings}}
    )

    (activation,) = activations
    assert activation.manifest_version == 2
    assert activation.roles == frozenset({"input"})
    assert type(activation.handle).__name__ == TwitchModule.__name__
    # Activation touched no transport: that is what the barrier protects.
    assert session.get_calls == []
    assert session.ws_calls == []
    assert CHAT_WRITE_ACTION in context.actions.discovered()
    assert context.actions.bindings(CHAT_WRITE_ACTION) == ()
    assert context.triggers.registry.spec("twitch") is not None

    coordinator = PhaseCoordinator(
        activations, tasks=context.tasks, reporter=diagnostics.append
    )
    assert (await coordinator.start()).status == 0
    assert coordinator.ready
    assert [call["url"] for call in session.get_calls] == [TOKEN_VALIDATION_URL]
    assert session.ws_calls == [EVENTSUB_URL]
    # Preparation bound the provider to the manifest's own declaration, over
    # the configured channel only, and marked the module ready (R5).
    (binding,) = context.actions.bindings(CHAT_WRITE_ACTION)
    assert binding.provider_name == CHAT_WRITE_PROVIDER
    assert binding.module == "twitch"
    assert binding.destination == Destination(PLATFORM, SETTINGS["broadcaster_id"], "chat")
    assert CHAT_WRITE_ACTION in context.actions.registered_ready()
    # The bound spec is the discovered one, field for field — the delivery
    # capability included (R1 decision 1): any drift between the module's
    # reading of the manifest and the loader's would have refused the binding.
    discovered = context.actions.discovered()[CHAT_WRITE_ACTION]
    assert binding.spec == discovered
    assert discovered.delivery == {"text_argument": "text"}
    assert discovered.delivery_text_argument == "text"

    websocket.feed(notification("loaded-1"))
    await wait_until(lambda: len(chat_events(context.bus)) == 1)
    assert chat_events(context.bus)[0]["payload"]["message_id"] == "loaded-1"
    # The loader registered the manifest's default policy for this input:
    # "hello" mentions no companion name, so the decision is a traced
    # rejection resolved from that declaration (R1).
    await wait_until(lambda: len(events_of(context.bus, "input.trigger.rejected")) == 1)
    (rejected,) = events_of(context.bus, "input.trigger.rejected")
    assert rejected["payload"]["input"] == "twitch"
    assert rejected["payload"]["source_event_id"] == "loaded-1"
    assert rejected["payload"]["reason"] == "rejected:keyword_no_match"

    report = await coordinator.stop()
    assert report.status == 0
    assert diagnostics == []
    assert websocket.close_calls == 1
    assert session.close_calls == 1
    assert context.tasks.active == 0
    assert CHAT_WRITE_ACTION not in context.actions.registered_ready()


@pytest.mark.asyncio
async def test_loader_refuses_settings_the_hook_rejects_before_activation() -> None:
    """R7/AC24: the declared hook refuses through the loader, value-free."""

    from core.loader import ModuleLoadError

    context = runtime_context()
    loader = ModuleLoader(context.bus, ROOT / "modules", context=context, environ={})
    invalid = {**SETTINGS, "companion_name": ""}
    activated: list[Any] = []
    invalid["_session_factory"] = lambda: activated.append("session")

    with pytest.raises(ModuleLoadError) as caught:
        await loader.activate_enabled(
            {"enabled_modules": ["twitch"], "modules": {"twitch": invalid}}
        )

    # The module's own diagnostic reaches the report, naming module and
    # field, with 0 configured values (AC24).
    assert caught.value.diagnostics == (
        "module 'twitch': field 'companion_name': must be a non-empty string",
    )
    assert_sanitized([str(caught.value)])
    assert SETTINGS["access_token"] not in str(caught.value)
    assert activated == []
    assert loader.activations == []


@pytest.mark.asyncio
async def test_phase_hooks_are_idempotent_and_safe_out_of_order() -> None:
    """R4: a second call of any phase is a no-op; close alone releases all."""

    websocket = FakeWebSocket(welcome("session-1"))
    session = FakeSession([websocket])
    handle, _, diagnostics = await activate_with(session)

    await handle.prepare()
    await handle.start_inputs()
    assert len(session.get_calls) == 1
    assert session.ws_calls == [EVENTSUB_URL]

    await handle.stop_inputs()
    await handle.stop_inputs()
    assert websocket.close_calls == 1
    assert session.close_calls == 0
    # The source is cut, but the send transport still serves a late reply.
    assert handle._receive_task.done()

    await handle.drain(0.5)
    await handle.close()
    await handle.close()
    assert session.close_calls == 1
    assert diagnostics == []

    unprepared = await activate(
        module_context(),
        {**SETTINGS, "_session_factory": lambda: FakeSession([])},
        {},
    )
    with pytest.raises(TwitchModuleError, match="requires prepare"):
        await unprepared.start_inputs()
    await unprepared.close()


@pytest.mark.asyncio
async def test_activation_refuses_a_context_without_the_runtime_surfaces() -> None:
    """AC26: the bus is read from the context, never received in its place."""

    with pytest.raises(TwitchModuleError, match="runtime context is invalid"):
        await activate(EventBus(), {**SETTINGS, "_session_factory": FakeSession}, {})


@pytest.mark.asyncio
async def test_notification_mapping_dedup_and_retry_reconnect() -> None:
    first = FakeWebSocket(
        welcome("session-1"),
        notification("message-1", "first"),
        notification("message-1", "duplicate"),
        close_frame(4005),
    )
    second = FakeWebSocket(
        welcome("session-2"),
        notification("message-1", "duplicate after reconnect"),
        notification("message-2", "second"),
    )
    session = FakeSession(
        [first, second],
        subscriptions=[
            FakeResponse(202, {"data": [{}]}),
            FakeResponse(202, {"data": [{}]}),
        ],
        sends=[
            FakeResponse(
                200,
                {"data": [{"message_id": "sent-1", "is_sent": True}]},
            ),
            FakeResponse(
                200,
                {"data": [{"message_id": "sent-2", "is_sent": True}]},
            ),
        ],
    )
    handle, bus, diagnostics = await activate_with(session)
    try:
        await wait_until(lambda: len(chat_events(bus)) == 2)

        events = chat_events(bus)
        # Normalised once, at the boundary, into the schema-version-2 shape
        # (R3). "first" names no companion, so the trigger refused it; the
        # bus copy is the normalised event and nothing more (R1).
        assert events[0] == {
            "type": "channel.chat.message",
            "payload": {
                "platform": "twitch",
                "channel_id": SETTINGS["broadcaster_id"],
                "author": {"id": "viewer-7", "display_name": "ViewerName"},
                "message_id": "message-1",
                "text": "first",
            },
            "metadata": {"source": "twitch", "schema_version": 2},
        }
        assert events[1]["payload"]["message_id"] == "message-2"
        assert session.ws_calls == [EVENTSUB_URL, EVENTSUB_URL]
        subscription_calls = [
            call
            for call in session.post_calls
            if call["url"] == EVENTSUB_SUBSCRIPTIONS_URL
        ]
        assert [
            call["json"]["transport"]["session_id"] for call in subscription_calls
        ] == ["session-1", "session-2"]
        assert subscription_calls[0]["json"]["condition"] == {
            "broadcaster_user_id": SETTINGS["broadcaster_id"],
            "user_id": SETTINGS["bot_user_id"],
        }
        assert session.get_calls == [
            {
                "url": TOKEN_VALIDATION_URL,
                "headers": {
                    "Authorization": f"OAuth {SETTINGS['access_token']}"
                },
            }
        ]

        await bus.publish("channel.chat.send", {"text": "after reconnect"}, {})
        send_calls = [
            call for call in session.post_calls if call["url"] == HELIX_CHAT_URL
        ]
        assert len(send_calls) == 1
        assert send_calls[0]["json"]["message"] == "after reconnect"
        assert diagnostics == []
    finally:
        await handle.close()

    await handle.close()
    assert session.close_calls == 1
    assert first.close_calls == 1
    assert second.close_calls == 1


@pytest.mark.asyncio
async def test_twitch_directed_handoff_preserves_subscription() -> None:
    reconnect_url = "wss://eventsub.wss.twitch.tv/reconnect/session"
    first = FakeWebSocket(
        welcome("session-1"),
        {
            "metadata": {"message_type": "session_reconnect"},
            "payload": {"session": {"reconnect_url": reconnect_url}},
        },
    )
    second = FakeWebSocket(welcome("session-2"), notification("message-2"))
    session = FakeSession([first, second])
    handle, bus, diagnostics = await activate_with(session)
    try:
        await wait_until(lambda: len(chat_events(bus)) == 1)
        subscription_calls = [
            call
            for call in session.post_calls
            if call["url"] == EVENTSUB_SUBSCRIPTIONS_URL
        ]
        assert session.ws_calls == [EVENTSUB_URL, reconnect_url]
        assert len(subscription_calls) == 1
        assert diagnostics == []
    finally:
        await handle.close()


@pytest.mark.asyncio
async def test_twitch_directed_handoff_drains_old_socket_during_overlap() -> None:
    reconnect_url = "wss://eventsub.wss.twitch.tv/reconnect/session"
    first = FakeWebSocket(
        welcome("session-1"),
        {
            "metadata": {"message_type": "session_reconnect"},
            "payload": {"session": {"reconnect_url": reconnect_url}},
        },
        notification("message-old", "from old socket"),
        close_frame(4004),
    )
    second = FakeWebSocket()
    session = FakeSession([first, second])
    handle, bus, diagnostics = await activate_with(session)
    try:
        await wait_until(lambda: len(chat_events(bus)) == 1)
        assert chat_events(bus)[0]["payload"]["message_id"] == "message-old"

        second.feed(welcome("session-2"))
        second.feed(notification("message-new", "from replacement socket"))
        await wait_until(lambda: len(chat_events(bus)) == 2)

        assert [
            event["payload"]["message_id"] for event in chat_events(bus)
        ] == ["message-old", "message-new"]
        assert first.close_calls == 1
        assert diagnostics == []
    finally:
        await handle.close()


@pytest.mark.asyncio
async def test_handoff_overlap_deduplicates_concurrent_notifications() -> None:
    reconnect_url = "wss://eventsub.wss.twitch.tv/reconnect/session"
    first = FakeWebSocket(
        welcome("session-1"),
        {
            "metadata": {"message_type": "session_reconnect"},
            "payload": {"session": {"reconnect_url": reconnect_url}},
        },
        notification("message-shared", "from old socket"),
        close_frame(4004),
    )
    second = FakeWebSocket(
        welcome("session-2"),
        notification("message-shared", "from replacement socket"),
    )
    publish_started = asyncio.Event()
    release_publish = asyncio.Event()

    async def block_publication(_: dict[str, Any]) -> None:
        publish_started.set()
        await release_publish.wait()

    bus = EventBus()
    bus.subscribe("channel.chat.message", block_publication)
    session = FakeSession([first, second])
    handle, _, diagnostics = await activate_with(session, bus=bus)
    try:
        await wait_until(publish_started.is_set)
        release_publish.set()
        await wait_until(lambda: len(chat_events(bus)) == 1)
        await asyncio.sleep(0)

        # The window records the decision before the publication awaits any
        # consumer, so the concurrent redelivery finds it and publishes,
        # feeds and decides nothing a second time (R6).
        assert len(chat_events(bus)) == 1
        assert len(events_of(bus, "input.trigger.rejected")) == 1
        assert diagnostics == []
    finally:
        await handle.close()


@pytest.mark.asyncio
async def test_close_during_handoff_closes_each_socket_once() -> None:
    reconnect_url = "wss://eventsub.wss.twitch.tv/reconnect/session"
    first = FakeWebSocket(
        welcome("session-1"),
        {
            "metadata": {"message_type": "session_reconnect"},
            "payload": {"session": {"reconnect_url": reconnect_url}},
        },
    )
    second = FakeWebSocket()
    session = FakeSession([first, second])
    handle, _, _ = await activate_with(session)
    await wait_until(lambda: len(session.ws_calls) == 2)

    await handle.close()

    assert first.close_calls == 1
    assert second.close_calls == 1
    assert session.close_calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("close_code", [4002, 4004, 4007])
async def test_recoverable_twitch_close_codes_create_fresh_session(
    close_code: int,
) -> None:
    first = FakeWebSocket(welcome("session-1"), close_frame(close_code))
    second = FakeWebSocket(welcome("session-2"), notification("message-2"))
    session = FakeSession(
        [first, second],
        subscriptions=[
            FakeResponse(202, {"data": [{}]}),
            FakeResponse(202, {"data": [{}]}),
        ],
    )
    handle, bus, diagnostics = await activate_with(session)
    try:
        await wait_until(lambda: len(chat_events(bus)) == 1)
        assert session.ws_calls == [EVENTSUB_URL, EVENTSUB_URL]
        assert diagnostics == []
    finally:
        await handle.close()


@pytest.mark.asyncio
async def test_immediate_reconnect_failures_escalate_bounded_backoff() -> None:
    delays: list[float] = []

    async def record_delay(delay: float) -> None:
        delays.append(delay)
        await asyncio.sleep(0)

    first = FakeWebSocket(welcome("session-1"), close_frame(4005))
    second = FakeWebSocket(welcome("session-2"), close_frame(4005))
    third = FakeWebSocket(welcome("session-3"), notification("message-3"))
    session = FakeSession(
        [first, second, third],
        subscriptions=[
            FakeResponse(202, {"data": [{}]}),
            FakeResponse(202, {"data": [{}]}),
            FakeResponse(202, {"data": [{}]}),
        ],
    )
    settings = {
        **SETTINGS,
        "_session_factory": lambda: session,
        "_retry_delay": record_delay,
        "diagnostic_reporter": lambda _: None,
    }
    handle = await start_module(settings)
    try:
        await wait_until(lambda: len(delays) == 2)
        assert delays == [0.25, 0.5]
    finally:
        await handle.close()


@pytest.mark.asyncio
async def test_non_retryable_disconnect_stops_and_closes_socket() -> None:
    websocket = FakeWebSocket(welcome("session-1"), close_frame(4001))
    session = FakeSession([websocket])
    handle, _, diagnostics = await activate_with(session)
    try:
        await wait_until(lambda: websocket.close_calls == 1)
        assert session.ws_calls == [EVENTSUB_URL]
        assert diagnostics == ["twitch websocket: non-retryable disconnect"]
    finally:
        await handle.close()

    assert websocket.close_calls == 1
    assert session.close_calls == 1


@pytest.mark.asyncio
async def test_revocation_reports_only_subscription_diagnostic() -> None:
    websocket = FakeWebSocket(
        welcome("session-1"),
        {"metadata": {"message_type": "revocation"}, "payload": {}},
    )
    session = FakeSession([websocket])
    handle, _, diagnostics = await activate_with(session)
    try:
        await wait_until(lambda: websocket.close_calls == 1)
        assert diagnostics == ["twitch subscription: revoked"]
    finally:
        await handle.close()


@pytest.mark.asyncio
async def test_close_cancels_a_pending_retry() -> None:
    retry_started = asyncio.Event()

    async def pending_retry(_: float) -> None:
        retry_started.set()
        await asyncio.Future()

    websocket = FakeWebSocket(welcome("session-1"), close_frame(4005))
    session = FakeSession([websocket])
    settings = {
        **SETTINGS,
        "_session_factory": lambda: session,
        "_retry_delay": pending_retry,
        "diagnostic_reporter": lambda _: None,
    }
    handle = await start_module(settings)
    await wait_until(retry_started.is_set)

    await asyncio.wait_for(handle.close(), timeout=1)

    assert session.ws_calls == [EVENTSUB_URL]
    assert websocket.close_calls == 1
    assert session.close_calls == 1


@pytest.mark.asyncio
async def test_authentication_rejection_is_sanitized_and_cleans_up() -> None:
    session = FakeSession(
        [], validation=FakeResponse(401, {"message": SETTINGS["access_token"]})
    )
    diagnostics: list[str] = []
    settings = {
        **SETTINGS,
        "_session_factory": lambda: session,
        "diagnostic_reporter": diagnostics.append,
    }

    with pytest.raises(TwitchModuleError, match="authentication rejected"):
        await start_module(settings)

    assert diagnostics == ["twitch authentication: rejected (status 401)"]
    assert_sanitized(diagnostics)
    assert session.close_calls == 1


@pytest.mark.asyncio
async def test_subscription_rejection_is_sanitized_and_has_no_false_event() -> None:
    websocket = FakeWebSocket(welcome("session-1"))
    session = FakeSession(
        [websocket],
        subscriptions=[
            FakeResponse(403, {"message": SETTINGS["client_secret"]})
        ],
    )
    diagnostics: list[str] = []
    bus = EventBus()
    settings = {
        **SETTINGS,
        "_session_factory": lambda: session,
        "diagnostic_reporter": diagnostics.append,
    }

    with pytest.raises(TwitchModuleError, match="subscription rejected"):
        await start_module(settings, bus)

    assert diagnostics == ["twitch subscription: rejected (status 403)"]
    assert bus.list_events() == []
    assert websocket.close_calls == 1
    assert session.close_calls == 1
    assert_sanitized(diagnostics)


@pytest.mark.asyncio
async def test_authentication_non_json_rejection_preserves_status() -> None:
    response = FakeResponse(502, ValueError("not json"))
    session = FakeSession([], validation=response)
    diagnostics: list[str] = []
    settings = {
        **SETTINGS,
        "_session_factory": lambda: session,
        "diagnostic_reporter": diagnostics.append,
    }

    with pytest.raises(TwitchModuleError, match="authentication rejected"):
        await start_module(settings)

    assert diagnostics == ["twitch authentication: rejected (status 502)"]
    assert response.release_calls == 1


@pytest.mark.asyncio
async def test_subscription_non_json_rejection_preserves_status() -> None:
    websocket = FakeWebSocket(welcome("session-1"))
    response = FakeResponse(503, ValueError("not json"))
    session = FakeSession([websocket], subscriptions=[response])
    diagnostics: list[str] = []
    settings = {
        **SETTINGS,
        "_session_factory": lambda: session,
        "diagnostic_reporter": diagnostics.append,
    }

    with pytest.raises(TwitchModuleError, match="subscription rejected"):
        await start_module(settings)

    assert diagnostics == ["twitch subscription: rejected (status 503)"]
    assert response.release_calls == 1


@pytest.mark.asyncio
async def test_malformed_notification_is_reported_without_publication() -> None:
    websocket = FakeWebSocket(
        welcome("session-1"),
        {
            "metadata": {"message_type": "notification"},
            "payload": {"event": {"message_id": SETTINGS["access_token"]}},
        },
    )
    session = FakeSession([websocket])
    handle, bus, diagnostics = await activate_with(session)
    try:
        await wait_until(lambda: bool(diagnostics))
        assert diagnostics == ["twitch notification: malformed data"]
        assert bus.list_events() == []
        assert_sanitized(diagnostics)
    finally:
        await handle.close()


def test_transport_urls_are_the_expected_twitch_operations() -> None:
    assert TOKEN_VALIDATION_URL == "https://id.twitch.tv/oauth2/validate"
    assert EVENTSUB_SUBSCRIPTIONS_URL == (
        "https://api.twitch.tv/helix/eventsub/subscriptions"
    )


def sent_response() -> FakeResponse:
    return FakeResponse(200, {"data": [{"message_id": "sent", "is_sent": True}]})

async def sent_traces_settled(handle: TwitchModule) -> None:
    """Wait for the ``channel.chat.sent`` publications a confirmed send handed
    to the loop: the send outcome never waits for them (R8)."""

    pending = tuple(handle._sent_traces)
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)



@pytest.mark.asyncio
async def test_chat_send_uses_configured_identity_and_preserves_event() -> None:
    class RecordingBus(EventBus):
        def __init__(self) -> None:
            super().__init__()
            self.patterns: list[str] = []

        def subscribe(self, pattern, handler, order=0):
            self.patterns.append(pattern)
            super().subscribe(pattern, handler, order)

    response = sent_response()
    session = FakeSession([FakeWebSocket(welcome("one"))], sends=[response])
    handle, bus, diagnostics = await activate_with(session, RecordingBus())
    payload = {"text": " Hello! ", "parent_message_id": "parent"}
    metadata = {"source": "test", "delivery_status": "failed"}
    try:
        result = await bus.publish("channel.chat.send", payload, metadata)
        assert bus.patterns == ["channel.chat.send"]
        assert session.post_calls[-1] == {
            "url": HELIX_CHAT_URL,
            "headers": {
                "Authorization": f"Bearer {SETTINGS['access_token']}",
                "Client-Id": SETTINGS["client_id"],
                "Content-Type": "application/json",
            },
            "json": {
                "broadcaster_id": SETTINGS["broadcaster_id"],
                "sender_id": SETTINGS["bot_user_id"],
                "message": " Hello! ",
                "reply_parent_message_id": "parent",
            },
        }
        assert result["payload"] == payload
        assert result["metadata"] == {
            "source": "test",
            "delivery_status": "sent",
            "delivery_outcome": "success",
        }
        assert metadata == {"source": "test", "delivery_status": "failed"}
        assert response.release_calls == 1
        assert diagnostics == []
        # The confirmed send is one traced fact, published after the record
        # was written and correlated to the compatibility route (R8); the
        # route reported ``sent`` without waiting for it.
        await sent_traces_settled(handle)
        (sent,) = events_of(bus, "channel.chat.sent")
        assert sent["payload"] == {
            "platform": "twitch",
            "channel_id": SETTINGS["broadcaster_id"],
            "message_id": "sent",
            "provider": CHAT_WRITE_PROVIDER,
            "route": "channel.chat.send",
        }
        assert handle.send_record.snapshot() == {
            "emitted": 1,
            "confirmed": 1,
            "failed": 0,
            "unknown": 0,
            "last_message_id": "sent",
        }
    finally:
        await handle.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {}, {"text": None}, {"text": 42}, {"text": ""}, {"text": " \n\t"},
    {"text": "hello", "parent_message_id": None},
    {"text": "hello", "parent_message_id": " "},
])
async def test_invalid_send_is_a_failed_attempt_without_request(
    payload: dict[str, Any],
) -> None:
    session = FakeSession([FakeWebSocket(welcome("one"))], sends=[sent_response()])
    handle, bus, diagnostics = await activate_with(session)
    try:
        result = await bus.publish("channel.chat.send", payload, {})
        assert result["metadata"]["delivery_status"] == "failed"
        assert len(session.post_calls) == 1  # EventSub subscription only.
        result = await bus.publish("channel.chat.send", {"text": "next"}, {})
        assert result["metadata"]["delivery_status"] == "sent"
        assert diagnostics == ["twitch chat send: invalid input"]
    finally:
        await handle.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("response, diagnostic, outcome", [
    (FakeResponse(503, {"message": SETTINGS["access_token"]}), "rejected (status 503)", "error"),
    (FakeResponse(401, {"message": SETTINGS["client_secret"]}), "rejected (status 401)", "error"),
    (FakeResponse(202, {"data": [{"is_sent": True, "message_id": "id"}]}), "rejected (status 202)", "error"),
    (FakeResponse(200, None), "malformed response", "external_unknown"),
    (FakeResponse(200, {"data": []}), "malformed response", "external_unknown"),
    (FakeResponse(200, {"data": {}}), "malformed response", "external_unknown"),
    (FakeResponse(200, {"data": [None]}), "malformed response", "external_unknown"),
    (FakeResponse(200, {"data": [{}, {}]}), "malformed response", "external_unknown"),
    (FakeResponse(200, {"data": [{"message_id": "id"}]}), "malformed response", "external_unknown"),
    (FakeResponse(200, {"data": [{"is_sent": 1, "message_id": "id"}]}), "malformed response", "external_unknown"),
    (FakeResponse(200, {"data": [{"is_sent": False, "drop_reason": SETTINGS["access_token"]}]}), "rejected (status 200)", "error"),
    (FakeResponse(200, {"data": [{"is_sent": True}]}), "malformed response", "external_unknown"),
    (FakeResponse(200, {"data": [{"is_sent": True, "message_id": " "}]}), "malformed response", "external_unknown"),
    (FakeResponse(200, {"data": [{"is_sent": True, "message_id": 1}]}), "malformed response", "external_unknown"),
    (FakeResponse(200, ValueError(SETTINGS["access_token"])), "malformed response", "external_unknown"),
    (OSError(SETTINGS["access_token"]), "request failed", "external_unknown"),
    (asyncio.TimeoutError(SETTINGS["client_secret"]), "request timed out", "external_unknown"),
])
async def test_send_failure_continues_chain_and_next_send_succeeds(
    response: Any, diagnostic: str, outcome: str,
) -> None:
    """R5/R8: an unconfirmed send is ``error`` or ``external_unknown``, never
    ``success``, and emits no ``channel.chat.sent``; the next confirmed send
    emits exactly one. The former ``[first, second]`` history equality is
    superseded by R8's confirmed-send trace, retained on the same bus."""

    success = sent_response()
    session = FakeSession([FakeWebSocket(welcome("one"))], sends=[response, success])
    handle, bus, diagnostics = await activate_with(session)
    observed = []
    bus.subscribe("**", lambda event: observed.append(event), order=90)
    try:
        first = await bus.publish("channel.chat.send", {"text": "first"}, {})
        assert events_of(bus, "channel.chat.sent") == []
        second = await bus.publish("channel.chat.send", {"text": "next"}, {})
        assert first["metadata"]["delivery_status"] == "failed"
        assert first["metadata"]["delivery_outcome"] == outcome
        assert second["metadata"]["delivery_status"] == "sent"
        assert second["metadata"]["delivery_outcome"] == "success"
        # The route reported the confirmed send without waiting for its
        # trace, so the trace lands after the route's own record (R8).
        await sent_traces_settled(handle)
        (sent,) = events_of(bus, "channel.chat.sent")
        assert sent["payload"]["message_id"] == "sent"
        assert observed == bus.list_events() == [first, second, sent]
        assert handle.send_record.snapshot() == {
            "emitted": 2,
            "confirmed": 1,
            "failed": 1 if outcome == "error" else 0,
            "unknown": 1 if outcome == "external_unknown" else 0,
            "last_message_id": "sent",
        }
        assert len(session.post_calls) == 3
        assert diagnostics == [f"twitch chat send: {diagnostic}"]
        assert_sanitized(diagnostics)
        assert SETTINGS["access_token"] not in str(observed)
        assert SETTINGS["client_secret"] not in str(observed)
        if isinstance(response, FakeResponse):
            assert response.release_calls == 1
        assert success.release_calls == 1
    finally:
        await handle.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["timeout", "cancel", "close"])
async def test_pending_send_releases_response_on_timeout_cancellation_and_close(
    action: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import modules.twitch as twitch

    started = asyncio.Event()

    class PendingResponse(FakeResponse):
        async def json(self) -> Any:
            started.set()
            await asyncio.Event().wait()

    pending = PendingResponse(200, None)
    websocket = FakeWebSocket(welcome("one"))
    session = FakeSession([websocket], sends=[pending, sent_response()])
    handle, bus, diagnostics = await activate_with(session)
    if action == "timeout":
        monkeypatch.setattr(twitch, "_CHAT_SEND_TIMEOUT_SECONDS", 0.01)
    publication = asyncio.create_task(
        bus.publish("channel.chat.send", {"text": "first"}, {})
    )
    try:
        await asyncio.wait_for(started.wait(), timeout=1)
        if action == "timeout":
            result = await asyncio.wait_for(publication, timeout=1)
            assert result["metadata"]["delivery_status"] == "failed"
            assert diagnostics == ["twitch chat send: request timed out"]
        else:
            if action == "cancel":
                publication.cancel()
            else:
                await asyncio.wait_for(handle.close(), timeout=1)
            with pytest.raises(asyncio.CancelledError):
                await publication
            assert bus.list_events() == []
            assert diagnostics == []
        assert pending.release_calls == 1
        assert not handle._active_send_tasks
        next_result = await bus.publish("channel.chat.send", {"text": "next"}, {})
        assert next_result["metadata"]["delivery_status"] == (
            "failed" if action == "close" else "sent"
        )
        assert len(session.post_calls) == (2 if action == "close" else 3)
    finally:
        if not publication.done():
            publication.cancel()
            await asyncio.gather(publication, return_exceptions=True)
        await handle.close()
    assert session.close_calls == 1
    assert websocket.close_calls == 1


@pytest.mark.asyncio
async def test_self_notifications_are_not_published_or_deduplicated() -> None:
    session = FakeSession([FakeWebSocket(welcome("session-self"))])
    handle, bus, diagnostics = await activate_with(session)
    try:
        own_message = notification("self-message")
        own_message["payload"]["event"]["chatter_user_id"] = SETTINGS["bot_user_id"]
        await handle._publish_notification(own_message)
        await handle._publish_notification(own_message)
        assert bus.list_events() == []

        # Ignored echoes must not change the publication dedupe state.
        await handle._publish_notification(notification("self-message"))
        await handle._publish_notification(notification("self-message"))
        assert len(chat_events(bus)) == 1
        assert chat_events(bus)[0]["payload"]["author"]["id"] == "viewer-7"
        assert diagnostics == []
    finally:
        await handle.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "identity",
    [{}, {"bot_user_id": None}, {"bot_user_id": ""}, {"bot_user_id": "   "}],
)
async def test_notification_filter_tolerates_missing_identity(
    identity: dict[str, Any],
) -> None:
    # Activation requires a bot ID for EventSub; exercise reception defensively
    # with incomplete settings without weakening that transport contract.
    bus = EventBus()
    diagnostics: list[str] = []
    handle = TwitchModule(
        module_context(bus),
        SimpleNamespace(**identity),
        FakeSession([]),
        diagnostics.append,
        no_delay,
    )
    try:
        await handle._publish_notification(notification("viewer-message"))
        assert len(chat_events(bus)) == 1
        assert chat_events(bus)[0]["payload"]["author"]["id"] == "viewer-7"
        assert diagnostics == []
    finally:
        await handle.close()


# --------------------------------------------------------------------------- #
# Reception on the v2 runtime: normalisation, identity, dedup window, admission
# (R1, R3, R6 — AC12, AC22)
# --------------------------------------------------------------------------- #


async def _ingest(handle: TwitchModule, envelope: dict[str, Any]) -> None:
    """Feed one envelope through the reception task exactly as the socket does."""

    task = asyncio.create_task(handle._publish_notification(envelope))
    await task


@pytest.mark.asyncio
async def test_untrusted_identity_is_one_traced_rejection_and_valid_event_is_normalized() -> None:
    """AC12 (R3): no trusted viewer identity → 0 admissions, 0 publications and
    exactly 1 traced rejection; a valid notification → exactly 1 normalised
    event with ``schema_version`` 2 and the five non-empty payload fields,
    fed to the chat context, decided, and admitted under its session key."""

    scheduler = RecordingScheduler()
    context = module_context(scheduler=scheduler)
    bus = context.bus
    session = FakeSession([FakeWebSocket(welcome("one"))])
    handle, _, diagnostics = await activate_with(session, context=context)
    try:
        anonymous = notification("anonymous-1", "Companion, who am I?")
        del anonymous["payload"]["event"]["chatter_user_id"]
        await _ingest(handle, anonymous)

        assert scheduler.admissions == []
        assert chat_events(bus) == []
        assert events_of(bus, "input.trigger.accepted") == []
        (rejected,) = events_of(bus, "input.trigger.rejected")
        assert rejected["payload"] == {
            "source_event_id": "anonymous-1",
            "input": "twitch",
            "platform": "twitch",
            "channel_id": SETTINGS["broadcaster_id"],
            "policy_version": None,
            "reason": "unauthenticated",
        }
        assert "run_id" not in rejected["payload"]
        assert context.supervision.snapshot()[COUNTER_TRIGGER_REJECTIONS] == 1
        assert context.chat.read("twitch", SETTINGS["broadcaster_id"], limit=10) == ()
        assert diagnostics == []

        await _ingest(handle, notification("valid-1", "Hello Companion"))

        (event,) = chat_events(bus)
        assert event["metadata"]["schema_version"] == 2
        assert event["metadata"]["source"] == "twitch"
        payload = event["payload"]
        for field_value in (
            payload["platform"],
            payload["channel_id"],
            payload["author"]["id"],
            payload["message_id"],
            payload["text"],
        ):
            assert isinstance(field_value, str) and field_value
        assert payload["platform"] == PLATFORM
        assert payload["channel_id"] == SETTINGS["broadcaster_id"]
        assert payload["author"]["id"] == "viewer-7"
        assert payload["message_id"] == "valid-1"
        assert payload["text"] == "Hello Companion"

        (accepted,) = events_of(bus, "input.trigger.accepted")
        assert accepted["payload"]["source_event_id"] == "valid-1"
        assert accepted["payload"]["platform"] == "twitch"
        assert accepted["payload"]["channel_id"] == SETTINGS["broadcaster_id"]
        assert accepted["payload"]["reason"] == "accepted:keyword_match"
        assert isinstance(accepted["payload"]["policy_version"], str)
        assert "run_id" not in accepted["payload"]
        # Traced before it was admitted, and admitted exactly once (AC27).
        assert [e["type"] for e in bus.list_events()] == [
            "input.trigger.rejected",
            "channel.chat.message",
            "input.trigger.accepted",
        ]
        ((session_key, work),) = scheduler.admissions
        assert session_key == SessionKey("twitch", SETTINGS["broadcaster_id"], "viewer-7")
        assert work.source_event_id == "valid-1"
        assert work.kind == "chat.message"
        assert work.payload["payload"]["message_id"] == "valid-1"
        assert work.payload["metadata"]["schema_version"] == 2

        (record,) = context.chat.read("twitch", SETTINGS["broadcaster_id"], limit=10)
        assert (record.author_id, record.message_id, record.text) == (
            "viewer-7", "valid-1", "Hello Companion",
        )
        assert diagnostics == []
    finally:
        await handle.close()


@pytest.mark.asyncio
async def test_rejected_trigger_is_fed_traced_and_never_admitted() -> None:
    """R1/R3: a trigger rejection still feeds the chat context and records the
    input, creates 0 admissions and is exactly 1 traced decision."""

    scheduler = RecordingScheduler()
    context = module_context(scheduler=scheduler)
    session = FakeSession([FakeWebSocket(welcome("one"))])
    handle, bus, diagnostics = await activate_with(session, context=context)
    try:
        await _ingest(handle, notification("plain-1", "nothing addressed to anyone"))
        await _ingest(handle, notification("plain-2", "still nothing"))

        assert scheduler.admissions == []
        assert len(chat_events(bus)) == 2
        assert len(events_of(bus, "input.trigger.rejected")) == 2
        assert events_of(bus, "input.trigger.accepted") == []
        records = context.chat.read("twitch", SETTINGS["broadcaster_id"], limit=10)
        assert [record.message_id for record in records] == ["plain-1", "plain-2"]
        assert context.supervision.snapshot()[COUNTER_TRIGGER_REJECTIONS] == 2
        assert diagnostics == []
    finally:
        await handle.close()


@pytest.mark.asyncio
async def test_accepted_and_rejected_events_share_one_normalised_shape_on_the_bus() -> None:
    """R1/R3/AC4: the bus carries every trusted input in exactly one shape.

    Normalisation happens once, at the boundary: an accepted event and a
    rejected one are published with the same schema-version-2 keys and no
    consumer-only field — the interim ``viewer_id`` bridge of the P14–P16
    window is gone (P24) — so a consumer admitting from the bus cannot tell
    them apart by shape and must read the recorded decision, which the
    brain's own ingestion gate does. The scheduler receives only the
    accepted event, as normalised."""

    scheduler = RecordingScheduler()
    context = module_context(scheduler=scheduler)
    bus = context.bus
    session = FakeSession([FakeWebSocket(welcome("one"))])
    handle, _, diagnostics = await activate_with(session, context=context)
    try:
        await _ingest(handle, notification("plain-1", "nothing addressed to anyone"))
        await _ingest(handle, notification("addressed-1", "Hello Companion"))
        await _ingest(handle, notification("plain-2", "still nothing"))

        assert [e["type"] for e in bus.list_events()] == [
            "channel.chat.message", "input.trigger.rejected",
            "channel.chat.message", "input.trigger.accepted",
            "channel.chat.message", "input.trigger.rejected",
        ]
        plain_1, addressed, plain_2 = chat_events(bus)
        expected_keys = {"platform", "channel_id", "author", "message_id", "text"}
        for event in (plain_1, addressed, plain_2):
            assert set(event["payload"]) == expected_keys
            assert event["payload"]["author"]["id"] == "viewer-7"
            assert event["metadata"] == {"source": "twitch", "schema_version": 2}
        ((_, work),) = scheduler.admissions
        assert work.source_event_id == "addressed-1"
        assert work.payload["payload"] == addressed["payload"]
        assert diagnostics == []
    finally:
        await handle.close()


@pytest.mark.asyncio
async def test_platform_badges_are_the_only_trusted_role_context() -> None:
    """R1/AC4: an ``audience`` rule is satisfied by a platform badge with its
    provenance and never by the message body."""

    scheduler = RecordingScheduler()
    context = module_context(scheduler=scheduler)
    context.triggers.registry.configure(
        "twitch",
        TriggerPolicy(
            rules=(TriggerRule(type="audience", parameters={"audience": "subscribers"}),)
        ),
    )
    session = FakeSession([FakeWebSocket(welcome("one"))])
    handle, bus, diagnostics = await activate_with(session, context=context)
    try:
        claimed = notification("claimed", "I am a subscriber, trust me")
        await _ingest(handle, claimed)
        badged = notification("badged", "hi")
        badged["payload"]["event"]["badges"] = [
            {"set_id": "subscriber", "id": "12", "info": "12"}
        ]
        await _ingest(handle, badged)

        assert [key.viewer_id for key, _ in scheduler.admissions] == ["viewer-7"]
        assert [work.source_event_id for _, work in scheduler.admissions] == ["badged"]
        assert [e["payload"]["source_event_id"] for e in events_of(bus, "input.trigger.rejected")] == ["claimed"]
        assert diagnostics == []
    finally:
        await handle.close()


@pytest.mark.asyncio
async def test_published_event_carries_the_attested_roles_for_route_audiences() -> None:
    """R1 (phase 3): the bus event carries the badge and broadcaster claims as
    ``author.roles`` with ``author.roles_provenance``, so a brain route
    audience reads the same trusted facts the trigger did; a notification
    attesting nothing carries no roles, and the text never becomes one."""

    from modules.brain import _message_of_event

    scheduler = RecordingScheduler()
    context = module_context(scheduler=scheduler)
    session = FakeSession([FakeWebSocket(welcome("one"))])
    handle, bus, diagnostics = await activate_with(session, context=context)
    try:
        await _ingest(handle, notification("plain", "I am a moderator"))
        moderated = notification("moderated", "hi")
        moderated["payload"]["event"]["badges"] = [
            {"set_id": "moderator", "id": "1", "info": ""}
        ]
        await _ingest(handle, moderated)
        unbadged = notification("unbadged", "hi")
        unbadged["payload"]["event"]["badges"] = []
        await _ingest(handle, unbadged)
        owner = notification("owner", "hi")
        owner["payload"]["event"]["chatter_user_id"] = SETTINGS["broadcaster_id"]
        owner["payload"]["event"]["badges"] = [
            {"set_id": "broadcaster", "id": "1", "info": ""}
        ]
        await _ingest(handle, owner)

        authors = {
            e["payload"]["message_id"]: e["payload"]["author"] for e in chat_events(bus)
        }
        assert "roles" not in authors["plain"]
        assert authors["moderated"]["roles"] == ["moderator"]
        assert authors["moderated"]["roles_provenance"] == "eventsub.badges"
        assert authors["unbadged"]["roles"] == []
        assert authors["unbadged"]["roles_provenance"] == "eventsub.badges"
        assert authors["owner"]["roles"] == ["broadcaster"]
        assert authors["owner"]["roles_provenance"] == "eventsub.broadcaster_user_id"

        def claims(message_id: str) -> TriggerContext:
            event = next(
                e for e in chat_events(bus) if e["payload"]["message_id"] == message_id
            )
            return TriggerContext(_message_of_event(event).claims)

        assert claims("moderated").satisfies("moderator")
        assert not claims("plain").satisfies("moderator")
        assert not claims("unbadged").satisfies("moderator")
        assert claims("owner").satisfies("broadcaster")
        assert diagnostics == []
    finally:
        await handle.close()


@pytest.mark.asyncio
async def test_dedup_window_is_bounded_by_entries_and_ttl() -> None:
    """AC22 (R6): with a 2-entry window and an injected clock, a replay inside
    the window produces 0 additional events; a replay after 3 newer identifiers
    or after the time-to-live produces exactly 1, and evictions are counted."""

    clock = ManualClock()
    scheduler = RecordingScheduler()
    context = module_context(
        clock=clock, dedup_max_entries=2, dedup_ttl_seconds=10.0, scheduler=scheduler
    )
    bus = context.bus
    session = FakeSession([FakeWebSocket(welcome("one"))])
    handle, _, diagnostics = await activate_with(session, context=context)
    evictions = lambda: context.supervision.snapshot()[COUNTER_DEDUP_EVICTIONS]  # noqa: E731
    try:
        await _ingest(handle, notification("a", "Hello Companion"))
        before = len(bus.list_events())
        assert len(chat_events(bus)) == 1
        assert len(scheduler.admissions) == 1

        # Inside the window: the recorded decision is reused and nothing —
        # no event, no trace, no feed, no admission — is produced again.
        await _ingest(handle, notification("a", "Hello Companion"))
        assert len(bus.list_events()) == before
        assert len(scheduler.admissions) == 1
        assert len(context.chat.read("twitch", SETTINGS["broadcaster_id"], limit=10)) == 1
        assert context.triggers.window_size == 1

        # 3 newer identifiers push "a" out of the 2-entry window, oldest first.
        for identifier in ("b", "c", "d"):
            await _ingest(handle, notification(identifier, "Hello Companion"))
        assert context.triggers.window_size == 2
        assert evictions() == 2
        assert len(chat_events(bus)) == 4

        await _ingest(handle, notification("a", "Hello Companion"))
        assert len(chat_events(bus)) == 5
        assert [e["payload"]["message_id"] for e in chat_events(bus)][-1] == "a"
        assert len(scheduler.admissions) == 5
        assert evictions() == 3

        # Past the time-to-live the entry reads as absent and is reprocessed.
        await _ingest(handle, notification("e", "Hello Companion"))
        assert len(chat_events(bus)) == 6
        clock.advance(11.0)
        await _ingest(handle, notification("e", "Hello Companion"))
        assert len(chat_events(bus)) == 7
        assert [e["payload"]["message_id"] for e in chat_events(bus)][-2:] == ["e", "e"]
        assert len(scheduler.admissions) == 7
        assert evictions() >= 4
        assert diagnostics == []
    finally:
        await handle.close()


@pytest.mark.asyncio
async def test_ingestion_returns_without_awaiting_run_work() -> None:
    """R2: the ingestion path admits and returns; nothing in it awaits a run.
    With the scheduler recording only, the publish lock is free again as soon
    as the notification is decided, so a second notification is not blocked."""

    scheduler = RecordingScheduler()
    context = module_context(scheduler=scheduler)
    session = FakeSession([FakeWebSocket(welcome("one"))])
    handle, bus, diagnostics = await activate_with(session, context=context)
    try:
        first = asyncio.create_task(
            handle._publish_notification(notification("one", "Hello Companion"))
        )
        second = asyncio.create_task(
            handle._publish_notification(notification("two", "Hello Companion"))
        )
        await asyncio.wait_for(asyncio.gather(first, second), timeout=1)
        assert not handle._publish_lock.locked()
        assert [work.source_event_id for _, work in scheduler.admissions] == ["one", "two"]
        assert len(chat_events(bus)) == 2
        assert diagnostics == []
    finally:
        await handle.close()


# --------------------------------------------------------------------------- #
# Delivery: the chat.write provider and the confirmed-send trace (R5, R8)
# --------------------------------------------------------------------------- #


def grant_chat_write(context: ModuleContext | RuntimeContext) -> None:
    runtime = context._runtime if isinstance(context, ModuleContext) else context
    runtime.actions._authorization.grant(  # the policy the fixture built
        AuthorizationRule(
            rule_id="grant-chat-write",
            action_name=CHAT_WRITE_ACTION,
            granted_permissions=("chat.write",),
        )
    )


def chat_write_call(call_id: str = "call-1", **overrides: Any) -> ActionCall:
    fields = {
        "action_name": CHAT_WRITE_ACTION,
        "action_version": 1,
        "arguments": {"text": "Hello there", "parent_message_id": "parent-1"},
        "conversation_id": "conversation-1",
        "run_id": "run-1",
        "call_id": call_id,
        "source_event_id": "source-1",
        "destination": Destination("twitch", SETTINGS["broadcaster_id"], "chat"),
        "principal": "brain",
        "deadline": 10_000.0,
        "message_id": "source-1",
    }
    fields.update(overrides)
    return ActionCall(**fields)


@pytest.mark.asyncio
async def test_chat_write_provider_reports_success_only_on_confirmation() -> None:
    """R5/R8: through the executor, a confirmed send is ``success`` carrying
    the platform identifier and exactly 1 ``channel.chat.sent`` correlated to
    the run; a request that left with no readable answer is ``external_unknown``
    with 0 traces; a platform refusal is ``error`` with 0 traces."""

    context = module_context()
    grant_chat_write(context)
    session = FakeSession(
        [FakeWebSocket(welcome("one"))],
        sends=[
            sent_response(),
            FakeResponse(200, {"data": [{"is_sent": True}]}),
            FakeResponse(503, {"message": SETTINGS["access_token"]}),
        ],
    )
    handle, bus, diagnostics = await activate_with(session, context=context)
    executor = context.executor
    try:
        confirmed = await executor.invoke(chat_write_call("call-1"))
        assert confirmed.status == "success"
        assert confirmed.result == {
            "message_id": "sent",
            "destination": {"platform": "twitch", "channel_id": SETTINGS["broadcaster_id"]},
        }
        assert confirmed.provenance["provider"] == CHAT_WRITE_PROVIDER
        assert confirmed.provenance["emission"] == "emitted"
        assert session.post_calls[-1]["json"] == {
            "broadcaster_id": SETTINGS["broadcaster_id"],
            "sender_id": SETTINGS["bot_user_id"],
            "message": "Hello there",
            "reply_parent_message_id": "parent-1",
        }
        # The outcome never waited for the trace (R8); the trace was handed
        # to the loop through the invocation's completion hook and lands
        # after the executor's own ``action.completed`` (AC27).
        assert handle.send_record.confirmed == 1
        await sent_traces_settled(handle)
        (sent,) = events_of(bus, "channel.chat.sent")
        assert sent["payload"] == {
            "platform": "twitch",
            "channel_id": SETTINGS["broadcaster_id"],
            "message_id": "sent",
            "provider": CHAT_WRITE_PROVIDER,
            "route": "chat.write",
            "run_id": "run-1",
            "call_id": "call-1",
            "conversation_id": "conversation-1",
            "source_event_id": "source-1",
            "source_message_id": "source-1",
        }
        # AC27's order: action.started, action.completed, then the send fact,
        # which exists only once the transport confirmed.
        types = [e["type"] for e in bus.list_events()]
        assert types.count("action.completed") == 1
        assert (
            types.index("action.started")
            < types.index("action.completed")
            < types.index("channel.chat.sent")
        )

        unknown = await executor.invoke(chat_write_call("call-2"))
        assert unknown.status == "external_unknown"
        assert unknown.error["code"] == "malformed_response"
        assert unknown.provenance["emission"] == "emitted"

        refused = await executor.invoke(chat_write_call("call-3"))
        assert refused.status == "error"
        assert refused.error["code"] == "platform_rejected"
        assert refused.error["retryable"] is False

        assert len(events_of(bus, "channel.chat.sent")) == 1
        assert executor.provider_invocations == 3
        assert handle.send_record.snapshot() == {
            "emitted": 3,
            "confirmed": 1,
            "failed": 1,
            "unknown": 1,
            "last_message_id": "sent",
        }
        assert diagnostics == [
            "twitch chat send: malformed response",
            "twitch chat send: rejected (status 503)",
        ]
        assert_sanitized(diagnostics)
        assert SETTINGS["access_token"] not in str(bus.list_events())
    finally:
        await handle.close()


@pytest.mark.asyncio
async def test_chat_write_refuses_without_a_rule_blank_text_and_other_channels() -> None:
    """R5: default-deny with 0 rules and 0 provider invocations; what the
    schema cannot say is refused by the provider before any emission."""

    context = module_context()
    session = FakeSession([FakeWebSocket(welcome("one"))], sends=[sent_response()])
    handle, bus, diagnostics = await activate_with(session, context=context)
    executor = context.executor
    try:
        refused = await executor.invoke(chat_write_call("call-0"))
        assert refused.status == "refused"
        assert executor.provider_invocations == 0

        grant_chat_write(context)
        blank = await executor.invoke(
            chat_write_call("call-blank", arguments={"text": "   "})
        )
        assert blank.status == "error"
        assert blank.error["code"] == "invalid_arguments"
        assert blank.provenance["emission"] == "not_emitted"

        elsewhere = await executor.invoke(
            chat_write_call(
                "call-elsewhere",
                destination=Destination("twitch", "another-channel", "chat"),
            )
        )
        assert elsewhere.status == "error"
        assert elsewhere.error["code"] == "no_provider"

        assert [c["url"] for c in session.post_calls] == [EVENTSUB_SUBSCRIPTIONS_URL]
        assert events_of(bus, "channel.chat.sent") == []
        assert handle.send_record.emitted == 0
        assert diagnostics == ["twitch chat send: invalid input"]
    finally:
        await handle.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["chat.write", "channel.chat.send"])
async def test_lost_sent_trace_neither_retries_nor_repeats_the_send(route: str) -> None:
    """R8/AC28: with ``channel.chat.sent`` publication always raising, the send
    record still shows exactly 1 confirmed send, the transport is invoked
    exactly 1 time, the loss is counted, and the route still reports success."""

    runtime = runtime_context()
    context = runtime.for_module("twitch")
    grant_chat_write(context)
    bus = context.bus

    def explode(event: dict[str, Any]) -> None:
        raise RuntimeError(SETTINGS["access_token"])

    bus.subscribe("channel.chat.sent", explode)
    session = FakeSession([FakeWebSocket(welcome("one"))], sends=[sent_response()])
    handle, _, diagnostics = await activate_with(session, context=context)
    try:
        if route == "chat.write":
            observation = await context.executor.invoke(chat_write_call("call-1"))
            assert observation.status == "success"
            assert observation.result["message_id"] == "sent"
        else:
            result = await bus.publish("channel.chat.send", {"text": "Hello there"}, {})
            assert result["metadata"]["delivery_status"] == "sent"
            assert result["metadata"]["delivery_outcome"] == "success"

        helix_calls = [c for c in session.post_calls if c["url"] == HELIX_CHAT_URL]
        assert len(helix_calls) == 1
        # Recorded before the outcome was returned, whatever the trace does.
        assert handle.send_record.confirmed == 1
        assert handle.send_record.emitted == 1
        await sent_traces_settled(handle)
        assert events_of(bus, "channel.chat.sent") == []
        assert context.supervision.snapshot()[COUNTER_LOST_TRACES] == 1
        (loss,) = runtime.supervision.losses
        assert loss.startswith("channel.chat.sent: PublicationError")
        assert diagnostics == []
    finally:
        await handle.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["chat.write", "channel.chat.send"])
async def test_confirmed_outcome_never_waits_for_the_sent_trace(route: str) -> None:
    """R5/R8: with a ``channel.chat.sent`` subscriber held, a confirmed send
    still reports ``success`` at once on either route — the executor's
    budget is not spent on the trace, so the confirmed send is never turned
    into ``external_unknown``, and the compatibility caller is not held. The
    record is already written when the outcome is returned; ``close`` then
    waits for the trace, which lands exactly once."""

    context = module_context()
    grant_chat_write(context)
    bus = context.bus
    release = asyncio.Event()
    held: list[dict[str, Any]] = []

    async def slow_subscriber(event: dict[str, Any]) -> None:
        held.append(event)
        await release.wait()

    bus.subscribe("channel.chat.sent", slow_subscriber)
    session = FakeSession([FakeWebSocket(welcome("one"))], sends=[sent_response()])
    handle, _, diagnostics = await activate_with(session, context=context)
    try:
        if route == "chat.write":
            observation = await asyncio.wait_for(
                context.executor.invoke(chat_write_call("call-1")), timeout=1
            )
            assert observation.status == "success"
            assert observation.result["message_id"] == "sent"
        else:
            result = await asyncio.wait_for(
                bus.publish("channel.chat.send", {"text": "Hello there"}, {}),
                timeout=1,
            )
            assert result["metadata"]["delivery_status"] == "sent"
            assert result["metadata"]["delivery_outcome"] == "success"

        # The subscriber still holds the trace; the outcome did not wait.
        await wait_until(lambda: len(held) == 1)
        assert not release.is_set()
        assert handle.send_record.confirmed == 1
        assert handle.send_record.last_message_id == "sent"
        assert events_of(bus, "channel.chat.sent") == []
        if route == "chat.write":
            assert len(events_of(bus, "action.completed")) == 1

        closing = asyncio.create_task(handle.close())
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert not closing.done()
        release.set()
        await asyncio.wait_for(closing, timeout=1)
        (sent,) = events_of(bus, "channel.chat.sent")
        assert sent["payload"]["message_id"] == "sent"
        assert sent["payload"]["route"] == route
        assert not handle._sent_traces
        helix_calls = [c for c in session.post_calls if c["url"] == HELIX_CHAT_URL]
        assert len(helix_calls) == 1
        assert diagnostics == []
    finally:
        release.set()
        await handle.close()


@pytest.mark.asyncio
async def test_pending_sent_traces_are_capped_and_close_is_bounded_under_a_held_subscriber() -> None:
    """R6/R8 (P24 F4): with the ``channel.chat.sent`` subscriber held and a
    cap of 2 pending traces, 5 confirmed sends leave exactly 2 traces pending
    and drop 3 — counted in the send record and as ``lost_traces``, outside
    the held path — while all 5 sends stay confirmed and recorded and the
    transport was used exactly 5 times. ``close`` then finishes inside its
    own budget with the subscriber still held, cancelling and counting the 2
    traces it could not publish, and the set is empty afterwards."""

    context = module_context()
    grant_chat_write(context)
    bus = context.bus
    release = asyncio.Event()
    held: list[dict[str, Any]] = []

    async def slow_subscriber(event: dict[str, Any]) -> None:
        held.append(event)
        await release.wait()

    bus.subscribe("channel.chat.sent", slow_subscriber)
    session = FakeSession(
        [FakeWebSocket(welcome("one"))], sends=[sent_response() for _ in range(5)]
    )
    diagnostics: list[str] = []
    settings = {
        **SETTINGS,
        "_session_factory": lambda: session,
        "_retry_delay": no_delay,
        "diagnostic_reporter": diagnostics.append,
        "_max_pending_sent_traces": 2,
        "_sent_trace_close_seconds": 0.05,
    }
    handle = await start_module(settings, bus, context)
    try:
        for index in range(5):
            observation = await asyncio.wait_for(
                context.executor.invoke(chat_write_call(f"call-{index}")), timeout=1
            )
            assert observation.status == "success"
            assert observation.result["message_id"] == "sent"

        await wait_until(lambda: len(held) == 2)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        # The set is bounded by the cap, not by how many sends were confirmed.
        assert len(handle._sent_traces) == 2
        assert len(held) == 2
        assert not release.is_set()
        # Every send is confirmed and recorded, independently of its trace.
        assert handle.send_record.confirmed == 5
        assert handle.send_record.emitted == 5
        assert handle.send_record.last_message_id == "sent"
        helix_calls = [c for c in session.post_calls if c["url"] == HELIX_CHAT_URL]
        assert len(helix_calls) == 5
        # The 3 dropped traces are counted outside the saturated path.
        assert handle.send_record.dropped_traces == 3
        assert context.supervision.snapshot()[COUNTER_LOST_TRACES] == 3
        assert diagnostics == [
            "twitch sent trace: dropped (pending traces saturated)"
        ] * 3
        assert events_of(bus, "channel.chat.sent") == []
        assert len(events_of(bus, "action.completed")) == 5

        # Bounded shutdown: the subscriber is still held, and close returns.
        await asyncio.wait_for(handle.close(), timeout=1)
        assert not release.is_set()
        assert not handle._sent_traces
        assert handle.send_record.confirmed == 5
        assert handle.send_record.dropped_traces == 5
        assert context.supervision.snapshot()[COUNTER_LOST_TRACES] == 5
        assert diagnostics[-1] == "twitch sent trace: dropped (close budget exhausted)"
        assert len(diagnostics) == 4
        assert events_of(bus, "channel.chat.sent") == []
        assert session.close_calls == 1
        assert_sanitized(diagnostics)
    finally:
        release.set()
        await handle.close()


@pytest.mark.asyncio
async def test_close_stays_bounded_when_a_sent_trace_subscriber_swallows_cancellation() -> None:
    """R6 (P24 F4 review A1): a ``channel.chat.sent`` subscriber that catches
    ``CancelledError`` and keeps waiting cannot hold ``close`` through the
    cancellation settlement either. The trace is cancelled and counted at the
    first budget, abandoned at the second, the set is empty, the send
    transport is closed, and ``close`` returns well inside twice the budget."""

    context = module_context()
    grant_chat_write(context)
    bus = context.bus
    release = asyncio.Event()
    cancelled = asyncio.Event()

    async def stubborn_subscriber(event: dict[str, Any]) -> None:
        try:
            await release.wait()
        except asyncio.CancelledError:
            cancelled.set()
            # Swallows the cancellation and keeps holding.
            await release.wait()

    bus.subscribe("channel.chat.sent", stubborn_subscriber)
    session = FakeSession([FakeWebSocket(welcome("one"))], sends=[sent_response()])
    diagnostics: list[str] = []
    settings = {
        **SETTINGS,
        "_session_factory": lambda: session,
        "_retry_delay": no_delay,
        "diagnostic_reporter": diagnostics.append,
        "_sent_trace_close_seconds": 0.05,
    }
    handle = await start_module(settings, bus, context)
    try:
        observation = await asyncio.wait_for(
            context.executor.invoke(chat_write_call("call-0")), timeout=1
        )
        assert observation.status == "success"
        await wait_until(lambda: len(handle._sent_traces) == 1)
        (trace,) = tuple(handle._sent_traces)

        loop = asyncio.get_running_loop()
        started = loop.time()
        await asyncio.wait_for(handle.close(), timeout=1)
        elapsed = loop.time() - started
        assert elapsed < 0.5
        assert cancelled.is_set()
        assert not release.is_set()
        # Cancelled and counted at the first budget, abandoned at the second.
        assert not trace.done()
        assert not handle._sent_traces
        assert handle.send_record.confirmed == 1
        assert handle.send_record.dropped_traces == 1
        assert context.supervision.snapshot()[COUNTER_LOST_TRACES] == 1
        assert diagnostics == [
            "twitch sent trace: dropped (close budget exhausted)",
            "twitch sent trace: cancellation abandoned (1 held past the close budget)",
        ]
        # The transport is closed regardless of the held subscriber.
        assert session.close_calls == 1
        assert_sanitized(diagnostics)
        # Abandoned is not forgotten (R4/AC15): the supervised registry now
        # owns the still-running trace, and the unfinished cancellation is a
        # recorded failure, not a normal return.
        registry = context._runtime.tasks
        assert registry.abandoned == (trace,)
        assert registry.failures == (
            "module 'twitch': task 'sent trace' did not stop when cancelled",
        )
        assert handle._abandoned_sent_traces == []
    finally:
        release.set()
        await handle.close()
    await asyncio.gather(trace, return_exceptions=True)
    assert registry.abandoned == ()


@pytest.mark.asyncio
async def test_coordinator_reports_nonzero_when_a_sent_trace_resists_cancellation_at_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R4/AC15 (P24 N3): through the real loader, coordinator and shared
    supervised registry, a ``channel.chat.sent`` subscriber that swallows
    its cancellation cannot turn the shutdown into a clean one. The module's
    close budget is shorter than the phase budget, so ``close`` returns in
    time and the transport is released — but the still-running publication
    is owned by the registry, not dropped on the loop, and the lifecycle
    report is non-zero and names the task that did not stop. The confirmed
    send stays confirmed and counted."""

    monkeypatch.setenv("TWITCH_CLIENT_SECRET", "secret-value")
    monkeypatch.setenv("TWITCH_ACCESS_TOKEN", "token-value")
    websocket = FakeWebSocket(welcome("session-1"))
    session = FakeSession([websocket], sends=[sent_response()])
    diagnostics: list[str] = []
    context = runtime_context(declare_triggers=False)
    grant_chat_write(context)
    release = asyncio.Event()
    cancelled = asyncio.Event()

    async def stubborn_subscriber(event: dict[str, Any]) -> None:
        try:
            await release.wait()
        except asyncio.CancelledError:
            cancelled.set()
            await release.wait()

    context.bus.subscribe("channel.chat.sent", stubborn_subscriber)
    loader = ModuleLoader(context.bus, ROOT / "modules", context=context, environ={})
    settings = {
        **SETTINGS,
        "_session_factory": lambda: session,
        "_retry_delay": no_delay,
        "diagnostic_reporter": diagnostics.append,
        "_sent_trace_close_seconds": 0.05,
    }
    activations = await loader.activate_enabled(
        {"enabled_modules": ["twitch"], "modules": {"twitch": settings}}
    )
    (activation,) = activations
    handle = activation.handle
    coordinator = PhaseCoordinator(
        activations,
        tasks=context.tasks,
        reporter=diagnostics.append,
        hook_timeout_seconds=1.0,
        shutdown_deadline_seconds=2.0,
        drain_deadline_seconds=0.1,
    )
    assert (await coordinator.start()).status == 0
    assert coordinator.ready

    observation = await asyncio.wait_for(
        context.executor.invoke(chat_write_call("call-0")), timeout=1
    )
    assert observation.status == "success"
    assert observation.result["message_id"] == "sent"
    await wait_until(lambda: len(handle._sent_traces) == 1)
    (trace,) = tuple(handle._sent_traces)
    try:
        loop = asyncio.get_running_loop()
        started = loop.time()
        report = await asyncio.wait_for(coordinator.stop(), timeout=1)
        elapsed = loop.time() - started
        assert elapsed < 0.5
        assert cancelled.is_set()
        assert not release.is_set()

        # Non-zero, naming the task that did not stop (AC15).
        assert report.status == 1
        assert report.failures == (
            "module 'twitch': task 'sent trace' did not stop when cancelled",
        )
        assert (
            "twitch sent trace: cancellation abandoned (1 held past the close budget)"
            in diagnostics
        )
        assert_sanitized(diagnostics)

        # Ownership retained: the trace still runs and the registry holds it.
        assert not trace.done()
        assert context.tasks.abandoned == (trace,)
        assert not handle._sent_traces
        assert handle._abandoned_sent_traces == []

        # The transport was still closed inside the deadline, and the send
        # is confirmed and counted regardless of its trace.
        assert websocket.close_calls == 1
        assert session.close_calls == 1
        assert handle.send_record.confirmed == 1
        assert handle.send_record.dropped_traces == 1
        assert context.supervision.snapshot()[COUNTER_LOST_TRACES] == 1
        assert events_of(context.bus, "channel.chat.sent") == []
        assert CHAT_WRITE_ACTION not in context.actions.registered_ready()
    finally:
        release.set()
        await asyncio.gather(trace, return_exceptions=True)
    assert context.tasks.abandoned == ()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("_max_pending_sent_traces", 0),
        ("_max_pending_sent_traces", 2.5),
        ("_max_pending_sent_traces", True),
        ("_sent_trace_close_seconds", float("inf")),
        ("_sent_trace_close_seconds", -1.0),
        ("_sent_trace_close_seconds", "5"),
    ],
)
async def test_sent_trace_bounds_are_validated_finite_at_activation(
    field_name: str, value: Any
) -> None:
    """R6: a non-finite or non-positive bound on the detached sent traces is
    refused at activation, naming module and field and never the value, with
    0 sessions created."""

    created: list[Any] = []

    def factory() -> FakeSession:
        session = FakeSession([FakeWebSocket(welcome("one"))])
        created.append(session)
        return session

    settings = {**SETTINGS, "_session_factory": factory, field_name: value}
    with pytest.raises(TwitchModuleError) as caught:
        await activate(module_context(), settings, {})
    assert f"module 'twitch': field {field_name!r}" in str(caught.value)
    assert repr(value) not in str(caught.value)
    assert created == []


@pytest.mark.asyncio
async def test_provider_binding_is_the_manifest_contract_and_refuses_ambiguity() -> None:
    """R5/AC16: preparation binds the manifest's ``chat.write`` declaration;
    a second provider already bound over the same channel fails preparation
    with a diagnostic naming the action and both providers, and 0 requests."""

    context = module_context()
    declared = manifest()["actions"][0]
    assert declared["name"] == CHAT_WRITE_ACTION

    class Rival:
        name = "rival"

        async def invoke(self, invocation: Any) -> Any:
            raise AssertionError("never invoked")

    session = FakeSession([FakeWebSocket(welcome("one"))])
    handle = await activate(
        context,
        {**SETTINGS, "_session_factory": lambda: session, "diagnostic_reporter": print},
        {},
    )
    assert context.actions.discovered() == {}
    await handle.prepare()
    await handle.close()
    spec = context.actions.discovered()[CHAT_WRITE_ACTION]
    assert spec.version == declared["version"]
    assert spec.nature == "write"
    assert spec.required_permissions == tuple(declared["required_permissions"])

    diagnostics: list[str] = []
    rival_context = module_context()
    rival_context.actions.register(
        spec, Rival(), destinations=Destination("twitch", "*", "chat")
    )
    session = FakeSession([FakeWebSocket(welcome("one"))])
    ambiguous = await activate(
        rival_context,
        {
            **SETTINGS,
            "_session_factory": lambda: session,
            "diagnostic_reporter": diagnostics.append,
        },
        {},
    )
    try:
        with pytest.raises(TwitchModuleError, match="action binding failed"):
            await ambiguous.prepare()
        assert session.get_calls == []
        assert session.post_calls == []
        (diagnostic,) = diagnostics
        assert CHAT_WRITE_ACTION in diagnostic
        assert "rival" in diagnostic and CHAT_WRITE_PROVIDER in diagnostic
        assert_sanitized(diagnostics)
    finally:
        await ambiguous.close()


# --------------------------------------------------------------------------- #
# Community notices, opt-in subscriptions and the ``:`` identity refusal
# (phase 3 R1, R6 — AC5, AC32 twitch half)
# --------------------------------------------------------------------------- #

# EventSub envelopes as the public reference documents them
# (``channel.chat.notification`` v1 and ``channel.follow`` v2); the field
# names are the ones the mapping reads.


def chat_notice(
    message_id: str,
    notice_type: str,
    *,
    chatter_id: str = "viewer-7",
    anonymous: bool = False,
    system_message: str = "a community notice",
    **blocks: Any,
) -> dict[str, Any]:
    event: dict[str, Any] = {
        "broadcaster_user_id": SETTINGS["broadcaster_id"],
        "broadcaster_user_login": "streamer",
        "broadcaster_user_name": "Streamer",
        "chatter_user_id": chatter_id,
        "chatter_user_login": "chatter",
        "chatter_user_name": "Chatter",
        "chatter_is_anonymous": anonymous,
        "color": "",
        "badges": [],
        "system_message": system_message,
        "message_id": message_id,
        "message": {"text": "", "fragments": []},
        "notice_type": notice_type,
    }
    for name in (
        "sub",
        "resub",
        "sub_gift",
        "community_sub_gift",
        "gift_paid_upgrade",
        "prime_paid_upgrade",
        "pay_it_forward",
        "raid",
        "unraid",
        "announcement",
        "bits_badge_tier",
        "charity_donation",
    ):
        event[name] = blocks.get(name)
    return {
        "metadata": {
            "message_id": f"envelope-{message_id}",
            "message_type": "notification",
            "message_timestamp": "2026-09-23T12:00:00.000000000Z",
            "subscription_type": "channel.chat.notification",
            "subscription_version": "1",
        },
        "payload": {
            "subscription": {"type": "channel.chat.notification", "version": "1"},
            "event": event,
        },
    }


def raid_notice(message_id: str, raider_id: str = "42") -> dict[str, Any]:
    return chat_notice(
        message_id,
        "raid",
        chatter_id=raider_id,
        system_message="Raider is raiding with a party of 3.",
        raid={
            "user_id": raider_id,
            "user_name": "Raider",
            "user_login": "raider",
            "viewer_count": 3,
            "profile_image_url": "https://example.invalid/raider.png",
        },
    )


def anonymous_community_gift(message_id: str) -> dict[str, Any]:
    return chat_notice(
        message_id,
        "community_sub_gift",
        chatter_id="274598607",
        anonymous=True,
        system_message="An anonymous user is gifting 5 Tier 1 Subs to the community!",
        community_sub_gift={
            "id": "community-gift-1",
            "total": 5,
            "sub_tier": "1000",
            "cumulative_total": None,
        },
    )


def follow_notice(message_id: str, follower_id: str = "55") -> dict[str, Any]:
    return {
        "metadata": {
            "message_id": message_id,
            "message_type": "notification",
            "message_timestamp": "2026-09-23T12:00:00.000000000Z",
            "subscription_type": "channel.follow",
            "subscription_version": "2",
        },
        "payload": {
            "subscription": {"type": "channel.follow", "version": "2"},
            "event": {
                "user_id": follower_id,
                "user_login": "follower",
                "user_name": "Follower",
                "broadcaster_user_id": SETTINGS["broadcaster_id"],
                "broadcaster_user_login": "streamer",
                "broadcaster_user_name": "Streamer",
                "followed_at": "2026-09-23T12:00:00.000000000Z",
            },
        },
    }


def event_kind_policy(*kinds: str) -> TriggerPolicy:
    return TriggerPolicy(
        rules=(TriggerRule(type="event_kind", parameters={"kinds": list(kinds)}),),
        combination="all_of",
    )


async def activate_notices(
    session: FakeSession,
    context: ModuleContext,
    kinds: list[str] | None,
) -> tuple[TwitchModule, list[str]]:
    diagnostics: list[str] = []
    settings: dict[str, Any] = {
        **SETTINGS,
        "_session_factory": lambda: session,
        "_retry_delay": no_delay,
        "diagnostic_reporter": diagnostics.append,
    }
    if kinds is not None:
        settings["notices"] = {"kinds": kinds}
    return await start_module(settings, context.bus, context), diagnostics


def subscription_requests(session: FakeSession) -> list[dict[str, Any]]:
    return [
        call["json"] for call in session.post_calls if call["url"] == EVENTSUB_SUBSCRIPTIONS_URL
    ]


def accepted_subscription() -> FakeResponse:
    return FakeResponse(202, {"data": [{"id": "subscription"}]})


@pytest.mark.asyncio
async def test_notice_kinds_select_the_subscriptions() -> None:
    """AC5 (R1): with ``notices.kinds`` unset exactly the phase 2 subscription
    (chat messages); with ``[sub, raid, follow]`` 3 — chat messages, chat
    notifications and follows (version 2, the bot account as the moderator
    reading followers)."""

    unset = FakeSession([FakeWebSocket(welcome("one"))])
    handle, _ = await activate_notices(unset, module_context(), None)
    await handle.close()
    (only,) = subscription_requests(unset)
    assert only == {
        "type": "channel.chat.message",
        "version": "1",
        "condition": {
            "broadcaster_user_id": SETTINGS["broadcaster_id"],
            "user_id": SETTINGS["bot_user_id"],
        },
        "transport": {"method": "websocket", "session_id": "one"},
    }

    listed = FakeSession(
        [FakeWebSocket(welcome("two"))],
        subscriptions=[accepted_subscription() for _ in range(3)],
    )
    handle, diagnostics = await activate_notices(
        listed, module_context(), ["sub", "raid", "follow"]
    )
    await handle.close()
    requests = subscription_requests(listed)
    assert len(requests) == 3
    assert [(r["type"], r["version"]) for r in requests] == [
        ("channel.chat.message", "1"),
        ("channel.chat.notification", "1"),
        ("channel.follow", "2"),
    ]
    assert requests[1]["condition"] == requests[0]["condition"]
    assert requests[2]["condition"] == {
        "broadcaster_user_id": SETTINGS["broadcaster_id"],
        "moderator_user_id": SETTINGS["bot_user_id"],
    }
    assert all(r["transport"]["session_id"] == "two" for r in requests)
    assert diagnostics == []

    # One kind of each subscription selects exactly that subscription.
    follow_only = FakeSession(
        [FakeWebSocket(welcome("three"))],
        subscriptions=[accepted_subscription() for _ in range(2)],
    )
    handle, _ = await activate_notices(follow_only, module_context(), ["follow"])
    await handle.close()
    assert [r["type"] for r in subscription_requests(follow_only)] == [
        "channel.chat.message",
        "channel.follow",
    ]


@pytest.mark.asyncio
async def test_a_raid_is_one_raid_event_fed_and_admitted_for_the_raider() -> None:
    """AC5 (R1): a raid from viewer 42 → one event of kind ``raid`` with author
    ``42`` (the raider of the event's raid block) and the system text, one
    chat-context entry and, under ``event_kind [raid]``, 1 admitted run in
    session ``(twitch, <channel>, 42)``."""

    scheduler = RecordingScheduler()
    context = module_context(scheduler=scheduler, policy=event_kind_policy("raid"))
    session = FakeSession(
        [FakeWebSocket(welcome("one"))],
        subscriptions=[accepted_subscription() for _ in range(2)],
    )
    handle, diagnostics = await activate_notices(session, context, ["raid"])
    try:
        await _ingest(handle, raid_notice("raid-1", "42"))

        (event,) = chat_events(context.bus)
        payload = event["payload"]
        assert payload["kind"] == "raid"
        assert payload["author"]["id"] == "42"
        assert payload["author"]["display_name"] == "Raider"
        assert payload["text"] == "Raider is raiding with a party of 3."
        assert payload["message_id"] == "raid-1"
        assert event["metadata"] == {"source": "twitch", "schema_version": 2}

        (record,) = context.chat.read("twitch", SETTINGS["broadcaster_id"], limit=10)
        assert (record.author_id, record.message_id) == ("42", "raid-1")

        (accepted,) = events_of(context.bus, "input.trigger.accepted")
        assert accepted["payload"]["reason"] == "accepted:event_kind_raid"
        ((session_key, work),) = scheduler.admissions
        assert session_key == SessionKey("twitch", SETTINGS["broadcaster_id"], "42")
        assert work.payload["payload"]["kind"] == "raid"

        # A chat message does not match the raid-only policy.
        await _ingest(handle, notification("plain-1", "Hello Companion"))
        assert len(scheduler.admissions) == 1
        assert handle.counts == {"invalid": 0, "notices_ignored": 0}
        assert diagnostics == []
    finally:
        await handle.close()


@pytest.mark.asyncio
async def test_the_raid_author_is_read_from_the_raid_block() -> None:
    """R1: the raid's author is the raider the raid block names, whatever the
    chatter fields say."""

    scheduler = RecordingScheduler()
    context = module_context(scheduler=scheduler, policy=event_kind_policy("raid"))
    session = FakeSession(
        [FakeWebSocket(welcome("one"))],
        subscriptions=[accepted_subscription() for _ in range(2)],
    )
    handle, _ = await activate_notices(session, context, ["raid"])
    try:
        envelope = raid_notice("raid-2", "42")
        del envelope["payload"]["event"]["chatter_user_id"]
        await _ingest(handle, envelope)
        ((session_key, _work),) = scheduler.admissions
        assert session_key.viewer_id == "42"
    finally:
        await handle.close()


@pytest.mark.asyncio
async def test_other_and_unlisted_notice_types_are_ignored_and_counted() -> None:
    """AC5 (R1): an ``announcement`` yields 0 events and ``notices_ignored`` + 1;
    a mapped kind that ``notices.kinds`` does not list is ignored the same way."""

    scheduler = RecordingScheduler()
    context = module_context(scheduler=scheduler, policy=event_kind_policy("raid"))
    session = FakeSession(
        [FakeWebSocket(welcome("one"))],
        subscriptions=[accepted_subscription() for _ in range(2)],
    )
    handle, diagnostics = await activate_notices(session, context, ["raid"])
    try:
        await _ingest(
            handle,
            chat_notice(
                "announce-1",
                "announcement",
                system_message="",
                announcement={"color": "PRIMARY"},
            ),
        )
        assert chat_events(context.bus) == []
        assert handle.counts["notices_ignored"] == 1

        await _ingest(
            handle,
            chat_notice("sub-1", "sub", sub={"sub_tier": "1000", "is_prime": False}),
        )
        assert chat_events(context.bus) == []
        assert handle.counts == {"invalid": 0, "notices_ignored": 2}
        assert context.chat.read("twitch", SETTINGS["broadcaster_id"], limit=10) == ()
        assert scheduler.admissions == []
        assert events_of(context.bus, "input.trigger.accepted") == []
        assert events_of(context.bus, "input.trigger.rejected") == []
        assert diagnostics == []
    finally:
        await handle.close()


@pytest.mark.asyncio
async def test_an_anonymous_community_gift_is_fed_and_never_admitted() -> None:
    """AC5 (R1, plan decision 3): an anonymous community gift yields 1
    chat-context entry under ``system:anonymous`` and 0 admissions, even under
    a policy selecting its kind; a redelivery feeds nothing more."""

    scheduler = RecordingScheduler()
    context = module_context(
        scheduler=scheduler, policy=event_kind_policy("community_sub_gift")
    )
    session = FakeSession(
        [FakeWebSocket(welcome("one"))],
        subscriptions=[accepted_subscription() for _ in range(2)],
    )
    handle, diagnostics = await activate_notices(session, context, ["community_sub_gift"])
    try:
        await _ingest(handle, anonymous_community_gift("gift-1"))
        await _ingest(handle, anonymous_community_gift("gift-1"))

        (record,) = context.chat.read("twitch", SETTINGS["broadcaster_id"], limit=10)
        assert record.author_id == "system:anonymous"
        assert record.text.startswith("An anonymous user is gifting")
        (event,) = chat_events(context.bus)
        assert event["payload"]["kind"] == "community_sub_gift"
        assert event["payload"]["author"] == {"id": "system:anonymous"}
        assert scheduler.admissions == []
        assert events_of(context.bus, "input.trigger.accepted") == []
        assert events_of(context.bus, "input.trigger.rejected") == []
        assert handle.counts == {"invalid": 0, "notices_ignored": 0}
        assert diagnostics == []
    finally:
        await handle.close()


@pytest.mark.asyncio
async def test_a_follow_is_a_follow_notice_of_the_follower() -> None:
    """R1: a ``channel.follow`` event is a ``follow`` notice authored by the
    follower, identified by the EventSub envelope, deduplicated on it."""

    scheduler = RecordingScheduler()
    context = module_context(scheduler=scheduler, policy=event_kind_policy("follow"))
    session = FakeSession(
        [FakeWebSocket(welcome("one"))],
        subscriptions=[accepted_subscription() for _ in range(2)],
    )
    handle, diagnostics = await activate_notices(session, context, ["follow"])
    try:
        await _ingest(handle, follow_notice("follow-envelope-1", "55"))
        await _ingest(handle, follow_notice("follow-envelope-1", "55"))

        (event,) = chat_events(context.bus)
        assert event["payload"]["kind"] == "follow"
        assert event["payload"]["author"]["id"] == "55"
        assert event["payload"]["message_id"] == "follow-envelope-1"
        ((session_key, _work),) = scheduler.admissions
        assert session_key == SessionKey("twitch", SETTINGS["broadcaster_id"], "55")
        assert diagnostics == []
    finally:
        await handle.close()


@pytest.mark.asyncio
async def test_a_rejected_follow_subscription_degrades_follow_and_chat_continues() -> None:
    """AC5 (R1): a rejected follow subscription (non-2xx) yields 1
    ``module.degraded`` naming ``follow`` and its reason; startup completes,
    and a subsequent chat message is still admitted."""

    scheduler = RecordingScheduler()
    context = module_context(scheduler=scheduler)
    websocket = FakeWebSocket(welcome("one"))
    session = FakeSession(
        [websocket],
        subscriptions=[
            accepted_subscription(),
            FakeResponse(403, {"message": "subscription missing proper authorization"}),
        ],
    )
    handle, diagnostics = await activate_notices(session, context, ["follow"])
    try:
        (degraded,) = events_of(context.bus, TRACE_MODULE_DEGRADED)
        assert degraded["payload"]["module"] == "twitch"
        assert degraded["payload"]["capabilities"] == ["follow"]
        assert degraded["payload"]["reason"] == "follow subscription rejected (status 403)"
        assert diagnostics == ["twitch follow subscription: rejected (status 403)"]

        websocket.feed(notification("after-1", "Hello Companion"))
        await wait_until(lambda: len(scheduler.admissions) == 1)
        ((session_key, _work),) = scheduler.admissions
        assert session_key == SessionKey("twitch", SETTINGS["broadcaster_id"], "viewer-7")
        assert len(events_of(context.bus, TRACE_MODULE_DEGRADED)) == 1
    finally:
        await handle.close()


@pytest.mark.asyncio
async def test_a_revoked_notice_subscription_degrades_its_kinds_and_chat_continues() -> None:
    """R1: a revoked follow subscription degrades ``follow`` only; the socket
    keeps delivering chat, while a revoked chat subscription still stops it."""

    scheduler = RecordingScheduler()
    context = module_context(scheduler=scheduler)
    websocket = FakeWebSocket(welcome("one"))
    session = FakeSession(
        [websocket],
        subscriptions=[accepted_subscription() for _ in range(2)],
    )
    handle, _ = await activate_notices(session, context, ["follow"])
    try:
        websocket.feed(
            {
                "metadata": {"message_type": "revocation"},
                "payload": {"subscription": {"type": "channel.follow", "status": "authorization_revoked"}},
            }
        )
        websocket.feed(notification("after-1", "Hello Companion"))
        await wait_until(lambda: len(scheduler.admissions) == 1)
        (degraded,) = events_of(context.bus, TRACE_MODULE_DEGRADED)
        assert degraded["payload"]["capabilities"] == ["follow"]
        assert degraded["payload"]["reason"] == "follow subscription revoked"
        assert websocket.close_calls == 0
    finally:
        await handle.close()


@pytest.mark.asyncio
async def test_each_revoked_notice_subscription_reports_its_own_degradation() -> None:
    """R1: with both notice subscriptions listed, revoking one then the other
    yields 2 ``module.degraded`` events with distinct reasons, so the second
    loss is not deduplicated into the first."""

    scheduler = RecordingScheduler()
    context = module_context(scheduler=scheduler)
    websocket = FakeWebSocket(welcome("one"))
    session = FakeSession(
        [websocket],
        subscriptions=[accepted_subscription() for _ in range(3)],
    )
    handle, _ = await activate_notices(session, context, ["raid", "follow"])
    try:
        for subscription_type in ("channel.follow", "channel.chat.notification"):
            websocket.feed(
                {
                    "metadata": {"message_type": "revocation"},
                    "payload": {
                        "subscription": {
                            "type": subscription_type,
                            "status": "authorization_revoked",
                        }
                    },
                }
            )
        websocket.feed(notification("after-1", "Hello Companion"))
        await wait_until(lambda: len(scheduler.admissions) == 1)
        follow, chat_notification = events_of(context.bus, TRACE_MODULE_DEGRADED)
        assert follow["payload"]["capabilities"] == ["follow"]
        assert follow["payload"]["reason"] == "follow subscription revoked"
        assert chat_notification["payload"]["capabilities"] == ["raid"]
        assert chat_notification["payload"]["reason"] == (
            "chat notification subscription revoked"
        )
        assert websocket.close_calls == 0
    finally:
        await handle.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("reserved", ["x:y", "system:anonymous", "system:watch"])
async def test_an_author_identifier_containing_a_colon_is_refused_and_counted(
    reserved: str,
) -> None:
    """AC32 twitch half (R6): an author id containing ``:`` yields 0
    admissions and the invalid counter + 1 — no feed, no publication, no
    decision — for a chat message and for a notice alike."""

    scheduler = RecordingScheduler()
    context = module_context(scheduler=scheduler)
    session = FakeSession(
        [FakeWebSocket(welcome("one"))],
        subscriptions=[accepted_subscription() for _ in range(2)],
    )
    handle, _ = await activate_notices(session, context, ["raid"])
    try:
        message = notification("colon-1", "Hello Companion")
        message["payload"]["event"]["chatter_user_id"] = reserved
        await _ingest(handle, message)

        assert scheduler.admissions == []
        assert handle.counts["invalid"] == 1
        assert chat_events(context.bus) == []
        assert context.chat.read("twitch", SETTINGS["broadcaster_id"], limit=10) == ()
        assert events_of(context.bus, "input.trigger.accepted") == []
        assert events_of(context.bus, "input.trigger.rejected") == []

        await _ingest(handle, raid_notice("colon-raid", reserved))
        assert scheduler.admissions == []
        assert handle.counts == {"invalid": 2, "notices_ignored": 0}

        # A valid author on the same input is still admitted.
        await _ingest(handle, notification("valid-1", "Hello Companion"))
        assert len(scheduler.admissions) == 1
    finally:
        await handle.close()


@pytest.mark.parametrize(
    ("notices", "field"),
    [
        ("raid", "notices"),
        ({"kinds": ["raid"], "extra": True}, "notices"),
        ({"kinds": "raid"}, "notices.kinds"),
        ({"kinds": ["announcement"]}, "notices.kinds"),
        ({"kinds": ["tip"]}, "notices.kinds"),
        ({"kinds": ["raid", "raid"]}, "notices.kinds"),
    ],
)
def test_settings_hook_refuses_an_invalid_notices_setting(notices: Any, field: str) -> None:
    """R1: ``notices.kinds`` lists Twitch notice kinds only, each once; the
    diagnostic names the field and echoes no value."""

    (diagnostic,) = validate_settings({**SETTINGS, "notices": notices})
    assert f"field {field!r}" in diagnostic
    assert validate_settings({**SETTINGS, "notices": {"kinds": list(NOTICE_KINDS)}}) == []
    assert validate_settings({**SETTINGS, "notices": {}}) == []



# --------------------------------------------------------------------------- #
# The clip and moderation services (R2, R5; P8)
# --------------------------------------------------------------------------- #


BODY_SECRET = "platform-body-must-not-leak"


class HelixServiceSession:
    """The module's HTTP session scripted for the clip and moderation
    endpoints: every request is recorded as ``(method, url, kwargs)`` and
    answered by the next scripted :class:`FakeResponse` (or raised)."""

    def __init__(self, *answers: Any) -> None:
        self.answers = list(answers)
        self.requests: list[dict[str, Any]] = []
        self.close_calls = 0

    def _answer(self, method: str, url: str, kwargs: dict[str, Any]) -> Any:
        assert url in (HELIX_CLIPS_URL, HELIX_MODERATION_CHAT_URL, HELIX_MODERATION_BANS_URL), url
        self.requests.append({"method": method, "url": url, **kwargs})
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer

    async def get(self, url: str, **kwargs: Any) -> Any:
        return self._answer("GET", url, kwargs)

    async def post(self, url: str, **kwargs: Any) -> Any:
        return self._answer("POST", url, kwargs)

    async def delete(self, url: str, **kwargs: Any) -> Any:
        return self._answer("DELETE", url, kwargs)

    async def close(self) -> None:
        self.close_calls += 1


async def activate_services(
    session: HelixServiceSession, context: ModuleContext | None = None
) -> tuple[TwitchModule, ModuleContext, list[str]]:
    scoped = context if context is not None else module_context()
    diagnostics: list[str] = []
    handle = await activate(
        scoped,
        {
            **SETTINGS,
            "_session_factory": lambda: session,
            "_retry_delay": no_delay,
            "diagnostic_reporter": diagnostics.append,
        },
        {},
    )
    return handle, scoped, diagnostics


def assert_credential_free(bus: EventBus, diagnostics: list[str]) -> None:
    """No configured credential nor platform body in any trace or diagnostic."""

    texts = trace_texts(bus) + diagnostics
    for value in (SETTINGS["access_token"], SETTINGS["client_secret"], BODY_SECRET):
        assert sum(value in text for text in texts) == 0, value


def lost_connection() -> Exception:
    return ConnectionResetError("connection reset after the request left")


@pytest.mark.asyncio
async def test_activation_publishes_clip_and_moderation_next_to_poll() -> None:
    """R2, R5, AC12 (twitch service): a context with a registry gets ``(clip,
    twitch)`` and ``(moderation, twitch)``; activation sends nothing."""

    runtime = runtime_context()
    registry = runtime.services
    session = HelixServiceSession()
    handle, _, _ = await activate_services(session, runtime.for_module("twitch"))
    try:
        assert dict(registry.entries()) == {
            ("poll", "twitch"): "twitch",
            ("clip", "twitch"): "twitch",
            ("moderation", "twitch"): "twitch",
        }
        assert registry.resolve("clip", "twitch") is handle.clip_service
        moderation = registry.resolve("moderation", "twitch")
        assert moderation is handle.moderation_service
        assert moderation.operations == frozenset({"delete_message", "timeout"})
        assert moderation.round_duration(61) == 61
        assert session.requests == []
    finally:
        await handle.close()
    assert session.close_calls == 1


@pytest.mark.asyncio
async def test_activation_on_a_default_context_publishes_nothing_and_succeeds() -> None:
    """R2, R5: a context without a registry activates exactly as before."""

    runtime = dataclasses.replace(runtime_context(), services=None)
    scoped = runtime.for_module("twitch")
    assert scoped.services.available is False
    session = HelixServiceSession()
    handle, _, _ = await activate_services(session, scoped)
    try:
        assert isinstance(handle, TwitchModule)
        assert session.requests == []
    finally:
        await handle.close()


@pytest.mark.asyncio
async def test_a_duplicate_clip_publication_fails_activation_and_releases_the_session() -> None:
    runtime = runtime_context()
    holder = object()
    runtime.services.publish("clip", "twitch", holder, module="rival")
    session = HelixServiceSession()
    with pytest.raises(ServiceRegistryError):
        await activate_services(session, runtime.for_module("twitch"))
    assert runtime.services.resolve("clip", "twitch") is holder
    assert session.close_calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("answer", "outcome"),
    [
        (helix_not_live(), "offline"),
        (helix_not_live(status=400), "offline"),
        (helix_refusal(404, BODY_SECRET), "offline"),
        (helix_refusal(401, BODY_SECRET), "rejected"),
        (helix_refusal(403, BODY_SECRET), "rejected"),
        (helix_refusal(400, BODY_SECRET), "rejected"),
        (helix_refusal(429, BODY_SECRET), "rate"),
        (helix_refusal(500, BODY_SECRET), "uncertain"),
        (helix_refusal(503, BODY_SECRET), "uncertain"),
        (lost_connection(), "uncertain"),
        (asyncio.TimeoutError(), "uncertain"),
        (FakeResponse(202, ValueError(BODY_SECRET)), "uncertain"),
        (FakeResponse(202, {"data": []}), "uncertain"),
    ],
    ids=[
        "404-not-live", "400-not-live", "404", "401", "403", "400", "429", "500",
        "503", "lost", "timeout", "unreadable-202", "empty-202",
    ],
)
async def test_each_clip_create_answer_maps_to_its_outcome_with_one_request(
    answer: Any, outcome: str
) -> None:
    """R2 (AC12 twitch service): one ``POST helix/clips``, never retried,
    classified into the taxonomy; nothing of the credential or the body leaks."""

    session = HelixServiceSession(answer)
    handle, scoped, diagnostics = await activate_services(session)
    try:
        result = await handle.clip_service.create("broadcaster-42")

        assert result.outcome == outcome
        assert result.clip_id is None and result.edit_url is None
        assert len(session.requests) == 1
        request = session.requests[0]
        assert (request["method"], request["url"]) == ("POST", HELIX_CLIPS_URL)
        assert request["params"] == {"broadcaster_id": "broadcaster-42"}
        assert_credential_free(scoped.bus, diagnostics)
    finally:
        await handle.close()


@pytest.mark.asyncio
async def test_an_accepted_clip_carries_its_id_and_edit_url_and_lookup_finds_it() -> None:
    """R2: 202 → ``accepted(id, edit_url)``; ``lookup`` is one ``GET
    helix/clips?id=`` each, ``None`` until the clip is listed."""

    session = HelixServiceSession(
        helix_clip_created("AwkwardClip", "https://clips.twitch.example/AwkwardClip/edit"),
        helix_clips_empty(),
        helix_clip_listed("AwkwardClip", "https://clips.twitch.example/AwkwardClip"),
    )
    handle, scoped, diagnostics = await activate_services(session)
    try:
        service = handle.clip_service
        created = await service.create("broadcaster-42")
        assert (created.outcome, created.clip_id, created.edit_url) == (
            "accepted",
            "AwkwardClip",
            "https://clips.twitch.example/AwkwardClip/edit",
        )
        assert len(session.requests) == 1
        assert session.requests[0]["headers"]["Authorization"] == (
            f"Bearer {SETTINGS['access_token']}"
        )
        assert session.requests[0]["headers"]["Client-Id"] == SETTINGS["client_id"]

        assert await service.lookup("AwkwardClip") is None
        assert await service.lookup("AwkwardClip") == "https://clips.twitch.example/AwkwardClip"
        lookups = session.requests[1:]
        assert [(r["method"], r["url"], r["params"]) for r in lookups] == [
            ("GET", HELIX_CLIPS_URL, {"id": "AwkwardClip"}),
        ] * 2
        assert_credential_free(scoped.bus, diagnostics)
    finally:
        await handle.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "answer",
    [helix_refusal(500, BODY_SECRET), helix_refusal(401, BODY_SECRET), lost_connection()],
    ids=["500", "401", "lost"],
)
async def test_a_failed_lookup_raises_once_without_the_body(answer: Any) -> None:
    session = HelixServiceSession(answer)
    handle, scoped, diagnostics = await activate_services(session)
    try:
        with pytest.raises(ClipLookupError) as failed:
            await handle.clip_service.lookup("AwkwardClip")
        assert BODY_SECRET not in str(failed.value)
        assert len(session.requests) == 1
        assert_credential_free(scoped.bus, diagnostics)
    finally:
        await handle.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("answer", "outcome"),
    [
        (helix_message_deleted(), "ok"),
        (helix_refusal(400, BODY_SECRET), "rejected"),
        (helix_refusal(401, BODY_SECRET), "rejected"),
        (helix_refusal(403, BODY_SECRET), "rejected"),
        (helix_refusal(404, BODY_SECRET), "rejected"),
        (helix_refusal(429, BODY_SECRET), "rate"),
        (helix_refusal(500, BODY_SECRET), "uncertain"),
        (lost_connection(), "uncertain"),
    ],
    ids=["204", "400", "401", "403", "404", "429", "500", "lost"],
)
async def test_each_delete_answer_maps_to_its_outcome_with_one_request(
    answer: Any, outcome: str
) -> None:
    """R5, AC28 (platform half): one ``DELETE helix/moderation/chat`` for the
    message, moderated by the configured bot identity, never retried."""

    session = HelixServiceSession(answer)
    handle, scoped, diagnostics = await activate_services(session)
    try:
        result = await handle.moderation_service.apply(
            "delete_message",
            channel_id="broadcaster-42",
            message_id="msg-9",
            target_author_id="viewer-7",
        )

        assert result.outcome == outcome
        assert len(session.requests) == 1
        request = session.requests[0]
        assert (request["method"], request["url"]) == ("DELETE", HELIX_MODERATION_CHAT_URL)
        assert request["params"] == {
            "broadcaster_id": "broadcaster-42",
            "moderator_id": SETTINGS["bot_user_id"],
            "message_id": "msg-9",
        }
        assert request["headers"]["Authorization"] == f"Bearer {SETTINGS['access_token']}"
        assert_credential_free(scoped.bus, diagnostics)
    finally:
        await handle.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("answer", "outcome"),
    [
        (helix_timeout_applied("viewer-7", 61), "ok"),
        (helix_refusal(400, BODY_SECRET), "rejected"),
        (helix_refusal(403, BODY_SECRET), "rejected"),
        (helix_refusal(429, BODY_SECRET), "rate"),
        (helix_refusal(502, BODY_SECRET), "uncertain"),
        (lost_connection(), "uncertain"),
    ],
    ids=["200", "400", "403", "429", "502", "lost"],
)
async def test_each_timeout_answer_maps_to_its_outcome_with_one_request(
    answer: Any, outcome: str
) -> None:
    """R5, AC28 (platform half): one ``POST helix/moderation/bans`` carrying
    the duration in seconds, unrounded."""

    session = HelixServiceSession(answer)
    handle, scoped, diagnostics = await activate_services(session)
    try:
        result = await handle.moderation_service.apply(
            "timeout",
            channel_id="broadcaster-42",
            message_id="msg-9",
            target_author_id="viewer-7",
            duration_seconds=61,
            reason="spam",
        )

        assert result.outcome == outcome
        assert len(session.requests) == 1
        request = session.requests[0]
        assert (request["method"], request["url"]) == ("POST", HELIX_MODERATION_BANS_URL)
        assert request["params"] == {
            "broadcaster_id": "broadcaster-42",
            "moderator_id": SETTINGS["bot_user_id"],
        }
        assert request["json"] == {
            "data": {"user_id": "viewer-7", "duration": 61, "reason": "spam"}
        }
        assert_credential_free(scoped.bus, diagnostics)
    finally:
        await handle.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation", "arguments"),
    [
        ("timeout", {"target_author_id": "viewer-7"}),
        ("timeout", {"target_author_id": "viewer-7", "duration_seconds": None}),
        ("timeout", {"target_author_id": "viewer-7", "duration_seconds": 0}),
        ("timeout", {"target_author_id": "viewer-7", "duration_seconds": -5}),
        ("timeout", {"target_author_id": "viewer-7", "duration_seconds": 1.5}),
        ("timeout", {"target_author_id": "viewer-7", "duration_seconds": True}),
        ("timeout", {"duration_seconds": 60}),
        ("delete_message", {}),
        ("ban", {"target_author_id": "viewer-7"}),
    ],
    ids=[
        "no-duration", "none-duration", "zero-duration", "negative-duration",
        "fractional-duration", "bool-duration", "no-target", "no-message", "ban",
    ],
)
async def test_a_request_without_its_arguments_is_refused_with_zero_requests(
    operation: str, arguments: dict[str, Any]
) -> None:
    """R5: a timeout without a positive whole duration never reaches the ban
    endpoint (no permanent ban); nothing incomplete is sent."""

    session = HelixServiceSession()
    handle, scoped, diagnostics = await activate_services(session)
    try:
        result = await handle.moderation_service.apply(
            operation, channel_id="broadcaster-42", **arguments
        )
        assert result.outcome == "rejected"
        assert session.requests == []
        assert_credential_free(scoped.bus, diagnostics)
    finally:
        await handle.close()


@pytest.mark.asyncio
async def test_a_closed_handle_sends_no_clip_or_moderation_request() -> None:
    session = HelixServiceSession()
    handle, _, _ = await activate_services(session)
    await handle.close()

    assert (await handle.clip_service.create("broadcaster-42")).outcome == "uncertain"
    moderation = await handle.moderation_service.apply(
        "delete_message", channel_id="broadcaster-42", message_id="msg-9"
    )
    assert moderation.outcome == "uncertain"
    assert session.requests == []
