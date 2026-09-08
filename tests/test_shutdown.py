from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import core.main as application
from core.bus import EventBus
from core.loader import ModuleActivation
from modules import audit, brain, twitch
from test_brain import CATALOG, SETTINGS as BRAIN_SETTINGS, completion
from test_brain import FakeResponse as ModelResponse
from test_brain import FakeSession as ModelSession
from test_twitch import SETTINGS as TWITCH_SETTINGS
from test_twitch import FakeResponse, FakeSession, FakeWebSocket, notification, welcome


@pytest.mark.parametrize("phase", ["model", "send", "stuck_model"])
async def test_shutdown_drains_real_pipeline(monkeypatch, phase: str) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    records = []
    diagnostics = []
    handles = {}
    stop = asyncio.Event()

    class DelayedResponse(ModelResponse):
        async def json(self):
            entered.set()
            await release.wait()
            return await super().json()

    model_response = (DelayedResponse if phase != "send" else ModelResponse)(
        200, completion("[send:channel.chat.send]bonjour")
    )
    send_response = (DelayedResponse if phase == "send" else FakeResponse)(
        200, {"data": [{"is_sent": True}]}
    )
    model_session = ModelSession(model_response)
    websocket = FakeWebSocket(welcome("session"), notification("in-flight"))
    twitch_session = FakeSession([websocket], sends=[send_response])

    class Loader:
        def __init__(self, bus, directory):
            self.bus = bus
            self.activations = []

        async def activate_enabled(self, config):
            handles["brain"] = await brain.activate(
                self.bus,
                {**BRAIN_SETTINGS, "_session_factory": lambda: model_session},
                CATALOG,
            )
            handles["audit"] = await audit.activate(
                self.bus, {"_writer": records.append}, CATALOG
            )
            handles["twitch"] = await twitch.activate(
                self.bus,
                {**TWITCH_SETTINGS, "_session_factory": lambda: twitch_session},
                CATALOG,
            )
            # Match the production activation order that formerly deadlocked.
            self.activations = [
                ModuleActivation(name, {}, handles[name])
                for name in ("twitch", "brain", "audit")
            ]
            return self.activations

    monkeypatch.setattr(application, "ModuleLoader", Loader)
    monkeypatch.setattr(application, "load_config", lambda *a, **k: {"modules_directory": "."})
    if phase == "stuck_model":
        monkeypatch.setattr(application, "_CLOSE_TIMEOUT_SECONDS", 0.05)
    task = asyncio.create_task(application.run(
        "unused", stop, ready_reporter=lambda _: None,
        diagnostic_reporter=diagnostics.append,
    ))
    await asyncio.wait_for(entered.wait(), 1)
    receiver = handles["twitch"]._receive_task
    stop.set()
    # Wait until shutdown has stopped the producer while the publication is held.
    await asyncio.wait_for(asyncio.gather(receiver, return_exceptions=True), 1)
    assert not task.done()
    assert not handles["audit"]._closed
    assert twitch_session.close_calls == 0
    websocket.feed(notification("too-late"))
    if phase == "stuck_model":
        assert await asyncio.wait_for(task, 1) == 1
        assert diagnostics == ["module 'twitch': shutdown failed"]
        assert model_session.close_calls == twitch_session.close_calls == 1
        assert handles["audit"]._writer_task.done()
        assert not handles["twitch"]._publication_tasks
        return
    release.set()
    assert await asyncio.wait_for(task, 1) == 0
    assert diagnostics == []
    decoded = [json.loads(record) for record in records]
    assert [record["type"] for record in decoded] == [
        "channel.chat.send", "channel.chat.message",
    ]
    assert decoded[1]["payload"]["message_id"] == "in-flight"
    assert model_session.close_calls == twitch_session.close_calls == 1
    assert handles["audit"]._writer_task.done()
    assert not handles["twitch"]._publication_tasks


async def test_brain_close_waits_for_handler_not_long_lived_caller() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    class Response(ModelResponse):
        async def json(self):
            entered.set()
            await release.wait()
            return await super().json()

    session = ModelSession(Response(200, completion("[send:channel.chat.send]hello")))
    bus = EventBus()
    handle = await brain.activate(
        bus, {**BRAIN_SETTINGS, "_session_factory": lambda: session}, CATALOG
    )

    async def caller():
        await bus.publish("channel.chat.message", {
            "text": "hello", "message_id": "one", "chatter_id": "viewer",
        }, {})
        await asyncio.Future()

    task = asyncio.create_task(caller())
    try:
        await asyncio.wait_for(entered.wait(), 1)
        closing = asyncio.create_task(handle.close())
        await asyncio.sleep(0)
        assert not closing.done()
        release.set()
        await asyncio.wait_for(closing, 1)
        assert not task.done()
        assert session.close_calls == 1
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_stubborn_close_is_bounded_and_audit_still_closes(monkeypatch) -> None:
    release = asyncio.Event()
    closed = []
    tasks = []

    async def stubborn():
        tasks.append(asyncio.current_task())
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            await release.wait()
        raise RuntimeError("late failure")

    async def close_audit():
        closed.append("audit")

    monkeypatch.setattr(application, "_CLOSE_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(application, "_CANCEL_TIMEOUT_SECONDS", 0.01)
    try:
        failures = await asyncio.wait_for(application._close_activations([
            ModuleActivation("brain", {}, SimpleNamespace(close=stubborn)),
            ModuleActivation("audit", {}, SimpleNamespace(close=close_audit)),
        ]), 1)
        assert failures == ["module 'brain': shutdown failed"]
        assert closed == ["audit"]
    finally:
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.parametrize("blocked", [True, False])
def test_cli_process_exits_with_synchronous_audit_writer(blocked: bool) -> None:
    # A real subprocess is essential: coroutine-only tests miss the executor's
    # thread join during asyncio.run() and interpreter shutdown.
    script = '''
import asyncio
import threading
import core.main as app
from core.loader import ModuleActivation
from modules.audit import AuditModule

app._SHUTDOWN_TIMEOUT_SECONDS = 0.3
started = threading.Event()
blocked = BLOCKED

def writer(line):
    started.set()
    if blocked:
        threading.Event().wait()

async def run(config):
    handle = AuditModule(writer, close_timeout_seconds=0.02)
    handle.handle_event({"type": "test.event", "payload": {}})
    while not started.is_set():
        await asyncio.sleep(0.001)
    failures = await app._close_activations([ModuleActivation("audit", {}, handle)])
    assert not failures
    print("cleanup completed", flush=True)
    return 0

app.run = run
raise SystemExit(app.main(["--config", "unused"]))
'''.replace("BLOCKED", repr(blocked))
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=3,
    )
    assert result.returncode == (1 if blocked else 0)
    assert result.stdout == "cleanup completed\n"
    assert result.stderr == ""


def test_cli_deadline_covers_cancellation_resistant_task() -> None:
    script = """
import asyncio
from types import SimpleNamespace
import core.main as app
from core.loader import ModuleActivation

app._SHUTDOWN_TIMEOUT_SECONDS = 0.3
app._CLOSE_TIMEOUT_SECONDS = 0.01
app._CANCEL_TIMEOUT_SECONDS = 0.01

async def close():
    while True:
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            pass

async def run(config):
    failures = await app._close_activations([
        ModuleActivation("brain", {}, SimpleNamespace(close=close)),
    ])
    assert failures == ["module 'brain': shutdown failed"]
    print("cleanup bounded", flush=True)
    return 1

app.run = run
raise SystemExit(app.main(["--config", "unused"]))
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=3,
    )
    assert result.returncode == 1
    assert result.stdout == "cleanup bounded\n"
    assert result.stderr == ""
