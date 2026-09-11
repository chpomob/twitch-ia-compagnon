from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from core.actions import (
    ActionExecutor,
    ActionRegistry,
    AuthorizationPolicy,
    AuthorizationRule,
)
from core.bus import EventBus
from core.context import ChatContext
from core.contracts import (
    COUNTER_DEDUP_EVICTIONS,
    COUNTER_LOST_TRACES,
    COUNTER_TRIGGER_REJECTIONS,
    ActionCall,
    Counters,
    Destination,
    SessionKey,
    TriggerPolicy,
    TriggerRule,
    TriggerSpec,
    TriggerTypeDeclaration,
)
from core.lifecycle import PhaseCoordinator, SupervisedTasks
from core.loader import ModuleLoader
from core.runtime import ModuleContext, RuntimeContext, Supervision
from core.triggers import TriggerEngine, TriggerRegistry
from modules.twitch import (
    CHAT_WRITE_ACTION,
    CHAT_WRITE_PROVIDER,
    EVENTSUB_SUBSCRIPTIONS_URL,
    EVENTSUB_URL,
    HELIX_CHAT_URL,
    MANIFEST_PATH,
    PLATFORM,
    TOKEN_VALIDATION_URL,
    TwitchModule,
    TwitchModuleError,
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


class FakeResponse:
    def __init__(self, status: int, body: Any) -> None:
        self.status = status
        self.body = body
        self.release_calls = 0

    async def json(self) -> Any:
        if isinstance(self.body, Exception):
            raise self.body
        return self.body

    def release(self) -> None:
        self.release_calls += 1


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


async def wait_until(predicate: Any) -> None:
    for _ in range(200):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition did not become true")


class FakeClock:
    """An injected monotonic clock; tests advance it, nothing sleeps."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class RecordingScheduler:
    """Records what ingestion admits; runs nothing (the run engine is P16)."""

    def __init__(self) -> None:
        self.admissions: list[tuple[SessionKey, Any]] = []

    def admit(self, session_key: SessionKey, work: Any) -> Any:
        self.admissions.append((session_key, work))
        return SimpleNamespace(accepted=True, run_id=f"run-{len(self.admissions)}")


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
) -> RuntimeContext:
    """The versioned runtime over a real bus, registry, engine, context and tasks.

    ``declare_triggers`` registers the manifest's trigger declaration for the
    ``twitch`` input, as the loader would; a test that runs the real loader
    passes ``False`` so the loader can register it itself.
    """

    target_bus = bus or EventBus()
    target_clock = clock if clock is not None else FakeClock()
    counters = Counters()
    registry = TriggerRegistry(companion_name=SETTINGS["companion_name"])
    if declare_triggers:
        registry.register(
            "twitch", manifest_trigger_spec(), companion_name=SETTINGS["companion_name"]
        )
    policy = authorization if authorization is not None else AuthorizationPolicy()
    actions = ActionRegistry(authorization=policy)
    supervision = Supervision(target_bus, counters=counters)
    return RuntimeContext(
        bus=target_bus,
        actions=actions,
        supervision=supervision,
        tasks=SupervisedTasks(),
        executor=ActionExecutor(
            actions, policy, supervision=supervision, counters=counters, clock=target_clock
        ),
        triggers=TriggerEngine(
            registry,
            dedup_max_entries=dedup_max_entries,
            dedup_ttl_seconds=dedup_ttl_seconds,
            clock=target_clock,
            counters=counters,
        ),
        chat=ChatContext(
            max_messages=16, max_bytes=8192, max_age_seconds=600.0, clock=target_clock
        ),
        scheduler=scheduler,
        clock=target_clock,
    )


def module_context(bus: EventBus | None = None, **options: Any) -> ModuleContext:
    return runtime_context(bus, **options).for_module("twitch")


def events_of(bus: EventBus, event_type: str) -> list[dict[str, Any]]:
    return [event for event in bus.list_events() if event["type"] == event_type]


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
    a settings schema and the hook this package implements, the three trigger
    types with their parameter schemas, the combination operators, exactly
    one default policy that names the companion only by token, and the
    ``chat.write`` action contract.
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
    assert set(schema["properties"]) == set(SETTINGS)
    assert manifest["settings_validator"] == "validate_settings"
    assert callable(validate_settings)

    triggers = manifest["triggers"]
    declared = {declaration["name"]: declaration for declaration in triggers["types"]}
    assert set(declared) == {"probability", "audience", "keyword"}
    for declaration in declared.values():
        assert declaration["parameter_schema"]["type"] == "object"
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

    assert "'twitch'" in str(caught.value)
    assert "validate_settings" in str(caught.value)
    assert_sanitized([str(caught.value)])
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
        # (R3). "first" names no companion, so the trigger refused it and the
        # bus copy carries no ``viewer_id`` bridge for the v1 consumer (R1).
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
async def test_rejected_trigger_reaches_no_bus_driven_model_runner() -> None:
    """R1/AC4: until the bus consumer of ``channel.chat.message`` is itself
    scheduler-driven (P16), it runs a model for every event it can read. The
    bus copy of an accepted event alone carries the ``viewer_id`` it reads;
    a rejected event is published as a fact it cannot run, so a rejected
    trigger calls no model even with that consumer on the bus. The scheduler
    receives the normalised event itself, without the bridge."""

    scheduler = RecordingScheduler()
    context = module_context(scheduler=scheduler)
    bus = context.bus
    model_runs: list[str] = []

    def v1_consumer(event: dict[str, Any]) -> None:
        # What the v1 consumer does with an input it can read: a model call.
        payload = event["payload"]
        viewer_id = payload.get("chatter_id", payload.get("viewer_id"))
        if isinstance(viewer_id, str) and viewer_id:
            model_runs.append(payload["message_id"])

    bus.subscribe("channel.chat.message", v1_consumer)
    session = FakeSession([FakeWebSocket(welcome("one"))])
    handle, _, diagnostics = await activate_with(session, context=context)
    try:
        await _ingest(handle, notification("plain-1", "nothing addressed to anyone"))
        await _ingest(handle, notification("addressed-1", "Hello Companion"))
        await _ingest(handle, notification("plain-2", "still nothing"))

        assert model_runs == ["addressed-1"]
        assert [e["type"] for e in bus.list_events()] == [
            "channel.chat.message", "input.trigger.rejected",
            "channel.chat.message", "input.trigger.accepted",
            "channel.chat.message", "input.trigger.rejected",
        ]
        plain_1, addressed, plain_2 = chat_events(bus)
        assert "viewer_id" not in plain_1["payload"]
        assert "viewer_id" not in plain_2["payload"]
        assert addressed["payload"]["viewer_id"] == "viewer-7"
        assert addressed["payload"]["author"]["id"] == "viewer-7"
        ((_, work),) = scheduler.admissions
        assert work.source_event_id == "addressed-1"
        assert "viewer_id" not in work.payload["payload"]
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
async def test_dedup_window_is_bounded_by_entries_and_ttl() -> None:
    """AC22 (R6): with a 2-entry window and an injected clock, a replay inside
    the window produces 0 additional events; a replay after 3 newer identifiers
    or after the time-to-live produces exactly 1, and evictions are counted."""

    clock = FakeClock()
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
        # The send fact was recorded before its trace, and the trace follows
        # the executor's own action.started: it exists only once the transport
        # confirmed (AC27). Its place against action.completed is not a
        # contract — the send hands the trace to the loop and returns, so the
        # executor's terminal trace never waits on it (R8).
        types = [e["type"] for e in bus.list_events()]
        assert types.index("action.started") < types.index("channel.chat.sent")
        assert types.count("action.completed") == 1

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
