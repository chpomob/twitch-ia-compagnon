from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

import core.main as application
from modules.twitch import (
    EVENTSUB_SUBSCRIPTIONS_URL,
    EVENTSUB_URL,
    HELIX_CHAT_URL,
    TOKEN_VALIDATION_URL,
)


ROOT = Path(__file__).parents[1]
EXAMPLE_CONFIG = ROOT / "config.yaml.example"


class FakeResponse:
    def __init__(self, status: int, body: Any) -> None:
        self.status = status
        self._body = body
        self.release_calls = 0

    async def json(self) -> Any:
        return self._body

    def release(self) -> None:
        self.release_calls += 1


class FakeWebSocket:
    def __init__(self, welcome: Mapping[str, Any]) -> None:
        self._frames: asyncio.Queue[Any] = asyncio.Queue()
        self._frames.put_nowait(welcome)
        self.close_calls = 0

    async def receive(self) -> Any:
        return await self._frames.get()

    async def close(self) -> None:
        self.close_calls += 1

    def feed(self, frame: Mapping[str, Any]) -> None:
        self._frames.put_nowait(frame)


class FakeTwitchSession:
    def __init__(
        self,
        environ: Mapping[str, str],
        websocket: FakeWebSocket,
        send_responses: list[FakeResponse],
    ) -> None:
        self._environ = environ
        self._websocket = websocket
        self._send_responses = list(send_responses)
        self.get_calls: list[dict[str, Any]] = []
        self.post_calls: list[dict[str, Any]] = []
        self.ws_calls: list[str] = []
        self.close_calls = 0
        self.helix_called = asyncio.Event()

    async def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.get_calls.append({"url": url, **kwargs})
        return FakeResponse(
            200,
            {
                "client_id": self._environ["TWITCH_CLIENT_ID"],
                "user_id": self._environ["TWITCH_BOT_USER_ID"],
            },
        )

    async def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.post_calls.append({"url": url, **kwargs})
        if url == EVENTSUB_SUBSCRIPTIONS_URL:
            return FakeResponse(202, {"data": [{"id": "subscription-created"}]})
        if url == HELIX_CHAT_URL and self._send_responses:
            response = self._send_responses.pop(0)
            self.helix_called.set()
            return response
        raise AssertionError("unexpected Twitch POST operation")

    async def ws_connect(self, url: str) -> FakeWebSocket:
        self.ws_calls.append(url)
        return self._websocket

    async def close(self) -> None:
        self.close_calls += 1


class FakeBrainSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self._responses = list(responses)
        self.post_calls: list[dict[str, Any]] = []
        self.close_calls = 0

    async def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.post_calls.append({"url": url, **kwargs})
        if not self._responses:
            raise AssertionError("unexpected model request")
        return self._responses.pop(0)

    async def close(self) -> None:
        self.close_calls += 1


def _environment() -> dict[str, str]:
    unique = uuid4().hex
    return {
        "TWITCH_CLIENT_ID": "-".join(("client", unique)),
        "TWITCH_CLIENT_SECRET": "-".join(("secret", unique)),
        "TWITCH_ACCESS_TOKEN": "-".join(("token", unique)),
        "TWITCH_BROADCASTER_ID": "-".join(("broadcaster", unique)),
        "TWITCH_BOT_USER_ID": "-".join(("bot", unique)),
        "OPENAI_ENDPOINT": "/".join(
            ("https:", "", unique + ".invalid", "chat", "completions")
        ),
        "OPENAI_MODEL": "-".join(("model", unique)),
        "OPENAI_API_KEY": "-".join(("key", unique)),
    }


def _welcome() -> dict[str, Any]:
    return {
        "metadata": {"message_type": "session_welcome"},
        "payload": {"session": {"id": "integration-session"}},
    }


def _notification(
    environ: Mapping[str, str], message_id: str, text: str
) -> dict[str, Any]:
    return {
        "metadata": {
            "message_type": "notification",
            "subscription_type": "channel.chat.message",
        },
        "payload": {
            "subscription": {"type": "channel.chat.message"},
            "event": {
                "broadcaster_user_id": environ["TWITCH_BROADCASTER_ID"],
                "chatter_user_id": "integration-viewer",
                "chatter_user_name": "Integration Viewer",
                "message_id": message_id,
                "message": {"text": text},
            },
        },
    }


def _completion(text: str) -> dict[str, Any]:
    return {"choices": [{"message": {"content": text}}]}


def _inject_boundaries(
    monkeypatch: pytest.MonkeyPatch,
    *,
    environ: Mapping[str, str],
    twitch_session: FakeTwitchSession,
    brain_session: FakeBrainSession,
    audit_writer: Callable[[str], Any],
    diagnostics: list[str],
    resolved_configs: list[dict[str, Any]],
) -> None:
    real_load_config = application.load_config

    def load_with_boundaries(
        path: str | Path, *, environ: Mapping[str, str] | None = None
    ) -> dict[str, Any]:
        config = real_load_config(path, environ=environ)
        resolved_configs.append(config)
        config["modules"]["twitch"].update(
            {
                "_session_factory": lambda: twitch_session,
                "diagnostic_reporter": diagnostics.append,
            }
        )
        config["modules"]["brain"].update(
            {
                "_session_factory": lambda: brain_session,
                "diagnostic_reporter": diagnostics.append,
            }
        )
        config["modules"]["audit"]["_writer"] = audit_writer
        return config

    monkeypatch.setattr(application, "load_config", load_with_boundaries)


async def _start_application(
    environ: Mapping[str, str],
    stop: asyncio.Event,
    ready: asyncio.Event,
    diagnostics: list[str],
) -> asyncio.Task[int]:
    def report_ready(message: str) -> None:
        assert message == "ready"
        ready.set()

    task = asyncio.create_task(
        application.run(
            EXAMPLE_CONFIG,
            stop,
            environ=environ,
            ready_reporter=report_ready,
            diagnostic_reporter=diagnostics.append,
        )
    )
    await asyncio.wait_for(ready.wait(), timeout=1)
    return task


@pytest.mark.asyncio
async def test_example_config_drives_full_chat_pipeline_and_clean_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environ = _environment()
    websocket = FakeWebSocket(_welcome())
    twitch_session = FakeTwitchSession(
        environ,
        websocket,
        [FakeResponse(200, {"data": [{"message_id": "sent", "is_sent": True}]})],
    )
    brain_session = FakeBrainSession(
        [FakeResponse(200, _completion("[send:channel.chat.send]Hello there"))]
    )
    audit_lines: list[str] = []
    audit_complete = asyncio.Event()
    diagnostics: list[str] = []
    resolved_configs: list[dict[str, Any]] = []

    async def write_audit(line: str) -> None:
        audit_lines.append(line)
        if len(audit_lines) == 2:
            audit_complete.set()

    _inject_boundaries(
        monkeypatch,
        environ=environ,
        twitch_session=twitch_session,
        brain_session=brain_session,
        audit_writer=write_audit,
        diagnostics=diagnostics,
        resolved_configs=resolved_configs,
    )

    stop = asyncio.Event()
    ready = asyncio.Event()
    task = await _start_application(environ, stop, ready, diagnostics)
    try:
        websocket.feed(_notification(environ, "incoming-one", "Hello companion"))
        await asyncio.wait_for(audit_complete.wait(), timeout=1)
    finally:
        stop.set()
    assert await asyncio.wait_for(task, timeout=1) == 0

    assert diagnostics == []
    assert len(brain_session.post_calls) == 1
    model_call = brain_session.post_calls[0]
    assert model_call["url"] == environ["OPENAI_ENDPOINT"]
    assert model_call["json"]["model"] == environ["OPENAI_MODEL"]
    assert model_call["headers"]["Authorization"] == (
        f"Bearer {environ['OPENAI_API_KEY']}"
    )

    assert twitch_session.ws_calls == [EVENTSUB_URL]
    assert twitch_session.get_calls == [
        {
            "url": TOKEN_VALIDATION_URL,
            "headers": {"Authorization": f"OAuth {environ['TWITCH_ACCESS_TOKEN']}"},
        }
    ]
    subscription_calls = [
        call
        for call in twitch_session.post_calls
        if call["url"] == EVENTSUB_SUBSCRIPTIONS_URL
    ]
    helix_calls = [
        call for call in twitch_session.post_calls if call["url"] == HELIX_CHAT_URL
    ]
    assert len(subscription_calls) == 1
    assert subscription_calls[0]["json"]["condition"] == {
        "broadcaster_user_id": environ["TWITCH_BROADCASTER_ID"],
        "user_id": environ["TWITCH_BOT_USER_ID"],
    }
    assert len(helix_calls) == 1
    assert helix_calls[0]["json"] == {
        "broadcaster_id": environ["TWITCH_BROADCASTER_ID"],
        "sender_id": environ["TWITCH_BOT_USER_ID"],
        "message": "Hello there",
    }
    assert helix_calls[0]["headers"]["Authorization"] == (
        f"Bearer {environ['TWITCH_ACCESS_TOKEN']}"
    )
    assert helix_calls[0]["headers"]["Client-Id"] == environ["TWITCH_CLIENT_ID"]

    audit_records = [json.loads(line) for line in audit_lines]
    assert len(audit_records) == 2
    assert {record["type"] for record in audit_records} == {
        "channel.chat.message",
        "channel.chat.send",
    }
    assert next(
        record for record in audit_records if record["type"] == "channel.chat.send"
    )["payload"] == {"text": "Hello there"}

    assert len(resolved_configs) == 1
    resolved = resolved_configs[0]["modules"]
    assert resolved["brain"]["endpoint"] == environ["OPENAI_ENDPOINT"]
    assert resolved["brain"]["model"] == environ["OPENAI_MODEL"]
    assert resolved["brain"]["api_key"] == environ["OPENAI_API_KEY"]
    assert resolved["twitch"]["client_secret"] == environ["TWITCH_CLIENT_SECRET"]
    assert twitch_session.close_calls == 1
    assert brain_session.close_calls == 1
    assert websocket.close_calls == 1


@pytest.mark.asyncio
async def test_failed_helix_publication_is_sanitized_and_next_one_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environ = _environment()
    websocket = FakeWebSocket(_welcome())
    leaked_body = {"message": "|".join(environ.values())}
    twitch_session = FakeTwitchSession(
        environ,
        websocket,
        [
            FakeResponse(503, leaked_body),
            FakeResponse(
                200,
                {"data": [{"message_id": "recovered", "is_sent": True}]},
            ),
        ],
    )
    brain_session = FakeBrainSession(
        [
            FakeResponse(200, _completion("[send:channel.chat.send]First")),
            FakeResponse(200, _completion("[send:channel.chat.send]Second")),
        ]
    )
    audit_lines: list[str] = []
    audit_complete = asyncio.Event()
    diagnostics: list[str] = []

    async def write_audit(line: str) -> None:
        audit_lines.append(line)
        if len(audit_lines) == 4:
            audit_complete.set()

    _inject_boundaries(
        monkeypatch,
        environ=environ,
        twitch_session=twitch_session,
        brain_session=brain_session,
        audit_writer=write_audit,
        diagnostics=diagnostics,
        resolved_configs=[],
    )

    stop = asyncio.Event()
    ready = asyncio.Event()
    task = await _start_application(environ, stop, ready, diagnostics)
    try:
        websocket.feed(_notification(environ, "failed-publication", "First request"))
        await asyncio.wait_for(twitch_session.helix_called.wait(), timeout=1)
        websocket.feed(_notification(environ, "later-publication", "Second request"))
        await asyncio.wait_for(audit_complete.wait(), timeout=1)
    finally:
        stop.set()
    assert await asyncio.wait_for(task, timeout=1) == 0

    helix_calls = [
        call for call in twitch_session.post_calls if call["url"] == HELIX_CHAT_URL
    ]
    assert [call["json"]["message"] for call in helix_calls] == ["First", "Second"]
    assert diagnostics == ["twitch chat send: rejected (status 503)"]
    rendered_diagnostics = "\n".join(diagnostics)
    assert all(value not in rendered_diagnostics for value in environ.values())
    assert len(brain_session.post_calls) == 2
    assert len(audit_lines) == 4
    audit_types = [json.loads(line)["type"] for line in audit_lines]
    assert audit_types.count("channel.chat.message") == 2
    assert audit_types.count("channel.chat.send") == 2


def test_configured_runtime_values_are_absent_from_python_sources() -> None:
    environ = _environment()
    config = application.load_config(EXAMPLE_CONFIG, environ=environ)
    modules = config["modules"]
    configured_values = {
        modules["brain"]["endpoint"],
        modules["brain"]["model"],
        modules["brain"]["api_key"],
        modules["twitch"]["client_id"],
        modules["twitch"]["client_secret"],
        modules["twitch"]["access_token"],
        modules["twitch"]["broadcaster_id"],
        modules["twitch"]["bot_user_id"],
    }
    python_sources = "\n".join(
        path.read_text(encoding="utf-8") for path in ROOT.rglob("*.py")
    )

    assert all(value not in python_sources for value in configured_values)
