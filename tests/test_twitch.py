from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from core.bus import EventBus
from modules.twitch import (
    EVENTSUB_SUBSCRIPTIONS_URL,
    EVENTSUB_URL,
    HELIX_CHAT_URL,
    TOKEN_VALIDATION_URL,
    TwitchModule,
    TwitchModuleError,
    activate,
)


SETTINGS = {
    "client_id": "configured-client",
    "client_secret": "never-show-client-secret",
    "access_token": "never-show-access-token",
    "broadcaster_id": "broadcaster-42",
    "bot_user_id": "bot-24",
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
    handle = await activate(target_bus, settings, {})
    return handle, target_bus, target_diagnostics


def assert_sanitized(diagnostics: list[str]) -> None:
    rendered = "\n".join(diagnostics)
    assert SETTINGS["client_secret"] not in rendered
    assert SETTINGS["access_token"] not in rendered


def test_manifest_declares_twitch_source_and_sink() -> None:
    manifest_path = Path(__file__).parents[1] / "modules" / "twitch" / "module.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))

    assert manifest == {
        "name": "twitch",
        "produces": ["channel.chat.message"],
        "consumes": ["channel.chat.send"],
        "middleware": False,
    }


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
    handle = await activate(EventBus(), settings, {})
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
    handle = await activate(EventBus(), settings, {})
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
        await activate(EventBus(), settings, {})

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
        await activate(bus, settings, {})

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
        await activate(EventBus(), settings, {})

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
        await activate(EventBus(), settings, {})

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
        bus, SimpleNamespace(**identity), FakeSession([]), diagnostics.append, no_delay
    )
    try:
        await handle._publish_notification(notification("viewer-message"))
        assert len(bus.list_events()) == 1
        assert bus.list_events()[0]["payload"]["chatter_id"] == "viewer-7"
        assert diagnostics == []
    finally:
        await handle.close()
