from __future__ import annotations

import asyncio
import importlib
import json
from pathlib import Path

import pytest
import yaml

from core import EventBus


audit = importlib.import_module("modules.audit")

CATALOG = {
    "audit": {
        "name": "audit",
        "produces": [],
        "consumes": ["**"],
        "middleware": True,
        "order": 90,
    }
}


def test_manifest_declares_catch_all_middleware_at_order_90() -> None:
    path = Path(audit.__file__).with_name("module.yaml")
    manifest = yaml.safe_load(path.read_text(encoding="utf-8"))

    assert manifest == CATALOG["audit"]
    assert type(manifest["order"]) is int


@pytest.mark.asyncio
async def test_writes_one_json_line_and_preserves_event(capsys) -> None:
    bus = EventBus()
    handle = await audit.activate(bus, {}, CATALOG)
    seen: list[dict] = []
    bus.subscribe("**", lambda event: seen.append(event), order=100)

    event = await bus.publish("channel.chat.message", {"text": "hello"}, {})
    await handle.close()

    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0]) == {
        "type": "channel.chat.message",
        "payload": {"text": "hello"},
    }
    assert seen == [event]


@pytest.mark.asyncio
async def test_handler_returns_identical_event_and_does_not_mutate_it() -> None:
    writes: list[str] = []
    handle = audit.AuditModule(writes.append)
    event = {
        "type": "test.event",
        "payload": {"API_KEY": "secret", "text": "unchanged"},
        "metadata": {},
    }

    returned = handle.handle_event(event)
    await handle.close()

    assert returned is event
    assert event["payload"]["API_KEY"] == "secret"


@pytest.mark.asyncio
async def test_delayed_and_failed_writes_do_not_delay_publication() -> None:
    gate = asyncio.Event()
    attempted: list[str] = []

    async def delayed_failure(line: str) -> None:
        attempted.append(line)
        await gate.wait()
        raise OSError("stdout unavailable")

    bus = EventBus()
    handle = await audit.activate(
        bus,
        {"_writer": delayed_failure, "_close_timeout_seconds": 0.1},
        CATALOG,
    )
    downstream = asyncio.Event()
    bus.subscribe("**", lambda event: downstream.set(), order=100)

    await asyncio.wait_for(bus.publish("test.event", {"value": 1}, {}), 0.1)
    assert downstream.is_set()
    gate.set()
    await handle.close()
    assert len(attempted) == 1


@pytest.mark.asyncio
async def test_recursively_redacts_every_sensitive_key_and_keeps_text() -> None:
    writes: list[str] = []
    handle = audit.AuditModule(writes.append)
    payload = {
        "Authorization": "secret-1",
        "nested": {
            "CLIENT_SECRET": "secret-2",
            "items": [
                {"access_TOKEN": "secret-3"},
                {
                    "API_Key": "secret-4",
                    "credential": "secret-5",
                    "Credentials": "secret-6",
                },
            ],
        },
        "text": "ordinary chat content",
    }

    handle.handle_event(
        {"type": "channel.chat.message", "payload": payload, "metadata": {}}
    )
    await handle.close()

    record = json.loads(writes[0])
    serialized = writes[0]
    assert "secret-" not in serialized
    assert record["payload"] == {
        "Authorization": "[REDACTED]",
        "nested": {
            "CLIENT_SECRET": "[REDACTED]",
            "items": [
                {"access_TOKEN": "[REDACTED]"},
                {
                    "API_Key": "[REDACTED]",
                    "credential": "[REDACTED]",
                    "Credentials": "[REDACTED]",
                },
            ],
        },
        "text": "ordinary chat content",
    }


@pytest.mark.asyncio
async def test_writer_continues_after_a_failed_record() -> None:
    successful: list[str] = []
    calls = 0

    def flaky_writer(line: str) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("first write failed")
        successful.append(line)

    handle = audit.AuditModule(flaky_writer)
    handle.handle_event({"type": "first", "payload": {}, "metadata": {}})
    handle.handle_event({"type": "second", "payload": {}, "metadata": {}})
    await handle.close()

    assert calls == 2
    assert json.loads(successful[0])["type"] == "second"
