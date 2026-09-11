from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from core.actions import ActionRegistry, AuthorizationPolicy
from core.bus import EventBus
from core.lifecycle import PhaseCoordinator, SupervisedTasks
from core.loader import ModuleLoader
from core.runtime import ModuleContext, RuntimeContext, Supervision
from core.triggers import TriggerEngine, TriggerRegistry
from modules.twitch import (
    EVENTSUB_SUBSCRIPTIONS_URL,
    EVENTSUB_URL,
    HELIX_CHAT_URL,
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


def runtime_context(bus: EventBus | None = None) -> RuntimeContext:
    """The versioned runtime over a real bus, registry, supervision and tasks."""

    target_bus = bus or EventBus()
    return RuntimeContext(
        bus=target_bus,
        actions=ActionRegistry(authorization=AuthorizationPolicy()),
        supervision=Supervision(target_bus),
        tasks=SupervisedTasks(),
        triggers=TriggerEngine(
            TriggerRegistry(companion_name=SETTINGS["companion_name"]),
            dedup_max_entries=8,
            dedup_ttl_seconds=60.0,
        ),
    )


def module_context(bus: EventBus | None = None) -> ModuleContext:
    return runtime_context(bus).for_module("twitch")


async def start_module(settings: dict[str, Any], bus: EventBus | None = None):
    """Activate and run the startup phases the coordinator would run (R4).

    A failing phase unwinds through ``close()`` exactly as the coordinator
    does before the failure propagates.
    """

    handle = await activate(module_context(bus), settings, {})
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
):
    target_bus = bus or EventBus()
    target_diagnostics = diagnostics if diagnostics is not None else []
    settings = {
        **SETTINGS,
        "_session_factory": lambda: session,
        "_retry_delay": no_delay,
        "diagnostic_reporter": target_diagnostics.append,
    }
    handle = await start_module(settings, target_bus)
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
    context = runtime_context()
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
    assert "chat.write" in context.actions.discovered()
    assert context.triggers.registry.spec("twitch") is not None

    coordinator = PhaseCoordinator(
        activations, tasks=context.tasks, reporter=diagnostics.append
    )
    assert (await coordinator.start()).status == 0
    assert coordinator.ready
    assert [call["url"] for call in session.get_calls] == [TOKEN_VALIDATION_URL]
    assert session.ws_calls == [EVENTSUB_URL]

    websocket.feed(notification("loaded-1"))
    await wait_until(lambda: len(context.bus.list_events()) == 1)
    assert context.bus.list_events()[0]["payload"]["message_id"] == "loaded-1"

    report = await coordinator.stop()
    assert report.status == 0
    assert diagnostics == []
    assert websocket.close_calls == 1
    assert session.close_calls == 1
    assert context.tasks.active == 0


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
        await wait_until(lambda: len(bus.list_events()) == 2)

        events = bus.list_events()
        assert events[0] == {
            "type": "channel.chat.message",
            "payload": {
                "broadcaster_id": SETTINGS["broadcaster_id"],
                "chatter_id": "viewer-7",
                "chatter_name": "ViewerName",
                "message_id": "message-1",
                "text": "first",
            },
            "metadata": {"source": "twitch"},
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
        await wait_until(lambda: len(bus.list_events()) == 1)
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
        await wait_until(lambda: len(bus.list_events()) == 1)
        assert bus.list_events()[0]["payload"]["message_id"] == "message-old"

        second.feed(welcome("session-2"))
        second.feed(notification("message-new", "from replacement socket"))
        await wait_until(lambda: len(bus.list_events()) == 2)

        assert [
            event["payload"]["message_id"] for event in bus.list_events()
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
        await wait_until(lambda: len(bus.list_events()) == 1)
        await asyncio.sleep(0)

        assert len(bus.list_events()) == 1
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
        await wait_until(lambda: len(bus.list_events()) == 1)
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
        assert result["metadata"] == {"source": "test", "delivery_status": "sent"}
        assert metadata == {"source": "test", "delivery_status": "failed"}
        assert response.release_calls == 1
        assert diagnostics == []
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
@pytest.mark.parametrize("response, diagnostic", [
    (FakeResponse(503, {"message": SETTINGS["access_token"]}), "rejected (status 503)"),
    (FakeResponse(401, {"message": SETTINGS["client_secret"]}), "rejected (status 401)"),
    (FakeResponse(202, {"data": [{"is_sent": True, "message_id": "id"}]}), "rejected (status 202)"),
    (FakeResponse(200, None), "malformed response"),
    (FakeResponse(200, {"data": []}), "malformed response"),
    (FakeResponse(200, {"data": {}}), "malformed response"),
    (FakeResponse(200, {"data": [None]}), "malformed response"),
    (FakeResponse(200, {"data": [{}, {}]}), "malformed response"),
    (FakeResponse(200, {"data": [{"message_id": "id"}]}), "malformed response"),
    (FakeResponse(200, {"data": [{"is_sent": 1, "message_id": "id"}]}), "malformed response"),
    (FakeResponse(200, {"data": [{"is_sent": False, "drop_reason": SETTINGS["access_token"]}]}), "rejected (status 200)"),
    (FakeResponse(200, {"data": [{"is_sent": True}]}), "malformed response"),
    (FakeResponse(200, {"data": [{"is_sent": True, "message_id": " "}]}), "malformed response"),
    (FakeResponse(200, {"data": [{"is_sent": True, "message_id": 1}]}), "malformed response"),
    (FakeResponse(200, ValueError(SETTINGS["access_token"])), "malformed response"),
    (OSError(SETTINGS["access_token"]), "request failed"),
    (asyncio.TimeoutError(SETTINGS["client_secret"]), "request timed out"),
])
async def test_send_failure_continues_chain_and_next_send_succeeds(
    response: Any, diagnostic: str,
) -> None:
    success = sent_response()
    session = FakeSession([FakeWebSocket(welcome("one"))], sends=[response, success])
    handle, bus, diagnostics = await activate_with(session)
    observed = []
    bus.subscribe("**", lambda event: observed.append(event), order=90)
    try:
        first = await bus.publish("channel.chat.send", {"text": "first"}, {})
        second = await bus.publish("channel.chat.send", {"text": "next"}, {})
        assert first["metadata"]["delivery_status"] == "failed"
        assert second["metadata"]["delivery_status"] == "sent"
        assert observed == bus.list_events() == [first, second]
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
        assert len(bus.list_events()) == 1
        assert bus.list_events()[0]["payload"]["chatter_id"] == "viewer-7"
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
        bus,
        module_context(bus).tasks,
        SimpleNamespace(**identity),
        FakeSession([]),
        diagnostics.append,
        no_delay,
    )
    try:
        await handle._publish_notification(notification("viewer-message"))
        assert len(bus.list_events()) == 1
        assert bus.list_events()[0]["payload"]["chatter_id"] == "viewer-7"
        assert diagnostics == []
    finally:
        await handle.close()
