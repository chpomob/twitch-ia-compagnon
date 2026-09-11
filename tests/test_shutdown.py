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
from test_main import _make_module, _valid_config, _write_config
from test_twitch import SETTINGS as TWITCH_SETTINGS
from test_twitch import FakeResponse, FakeSession, FakeWebSocket, notification, welcome


@pytest.mark.parametrize("phase", ["model", "send", "stuck_model", "startup"])
async def test_shutdown_drains_real_pipeline(monkeypatch, phase: str) -> None:
    """Shutdown drains the in-flight publication before any transport closes.

    Twitch is a versioned producer now (R4): the coordinator stops its input
    first, drains its accepted publication under the drain hook, closes the
    v1 sinks it published into, then closes its send transport. The former
    ``module 'twitch': shutdown failed`` of the v1 close route is therefore
    the drain phase timing out when the model never answers.
    """

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
            self.context = None
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
                self.context.for_module("twitch"),
                {**TWITCH_SETTINGS, "_session_factory": lambda: twitch_session},
                CATALOG,
            )
            # Match the production activation order that formerly deadlocked.
            self.activations = [
                ModuleActivation(name, {}, handles[name])
                for name in ("brain", "audit")
            ]
            self.activations.insert(
                0,
                ModuleActivation(
                    "twitch",
                    {},
                    handles["twitch"],
                    roles=frozenset({"input"}),
                    manifest_version=2,
                ),
            )
            if phase == "startup":
                # The coordinator never starts here, so the harness opens the
                # producer itself to hold a publication in flight; the
                # coordinator's own calls, if any, are idempotent no-ops.
                await handles["twitch"].prepare()
                await handles["twitch"].start_inputs()
                await asyncio.Future()
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
    if phase == "startup":
        task.cancel()
    else:
        stop.set()
    # Wait until shutdown has stopped the producer while the publication is held.
    await asyncio.wait_for(asyncio.gather(receiver, return_exceptions=True), 1)
    assert not task.done()
    assert not handles["audit"]._closed
    assert twitch_session.close_calls == 0
    websocket.feed(notification("too-late"))
    if phase == "stuck_model":
        assert await asyncio.wait_for(task, 1) == 1
        assert diagnostics == ["module 'twitch': phase 'drain' timed out"]
        assert model_session.close_calls == twitch_session.close_calls == 1
        assert handles["audit"]._writer_task.done()
        assert not handles["twitch"]._publication_tasks
        return
    release.set()
    if phase == "startup":
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1)
    else:
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
        # Two v1 activations: the coordinator closes them as its compatibility
        # step, one bounded close after the other, in activation order.
        coordinator = application._coordinator(
            [
                ModuleActivation("brain", {}, SimpleNamespace(close=stubborn)),
                ModuleActivation("audit", {}, SimpleNamespace(close=close_audit)),
            ],
            application._assemble_runtime({}),
            lambda _message: None,
        )
        report = await asyncio.wait_for(coordinator.stop(), 1)
        assert report.failures == ("module 'brain': shutdown failed",)
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
    coordinator = app._coordinator(
        [ModuleActivation("audit", {}, handle)],
        app._assemble_runtime({}),
        lambda _message: None,
    )
    report = await coordinator.stop()
    assert not report.failures
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
    coordinator = app._coordinator(
        [ModuleActivation("brain", {}, SimpleNamespace(close=close))],
        app._assemble_runtime({}),
        lambda _message: None,
    )
    report = await coordinator.stop()
    assert report.failures == ("module 'brain': shutdown failed",)
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


@pytest.mark.parametrize("close_mode", ["normal", "blocked", "error"])
async def test_cancellation_during_activation_closes_partial_startup(
    tmp_path, monkeypatch, close_mode: str,
) -> None:
    modules = tmp_path / "modules"
    modules.mkdir()
    names = ["twitch", "brain", "audit", "slow", "never"]
    for name in names:
        _make_module(modules, name)
    config = _valid_config(str(modules), tmp_path / "unused.log")
    config["enabled_modules"] = names
    config["modules"].update({"slow": {}, "never": {}})
    config_path = tmp_path / "config.yaml"
    _write_config(config_path, config)
    entered = asyncio.Event()
    activated = []
    closed = []
    workers = []
    closers = []
    diagnostics = []
    readiness = []
    removed = []
    unhandled = []
    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()
    baseline = asyncio.all_tasks()

    async def worker():
        await asyncio.Future()

    def entry_point(self, discovered):
        name = discovered.name

        async def activate(bus, settings, catalog):
            if name == "slow":
                entered.set()
                await asyncio.Future()
            activated.append(name)
            background = asyncio.create_task(worker())
            workers.append(background)

            async def close():
                closers.append(asyncio.current_task())
                try:
                    if name == "brain" and close_mode == "blocked":
                        await asyncio.Future()
                    if name == "brain" and close_mode == "error":
                        raise RuntimeError("close failed")
                finally:
                    background.cancel()
                    await asyncio.gather(background, return_exceptions=True)
                    closed.append(name)

            return SimpleNamespace(close=close)

        return activate

    monkeypatch.setattr(application.ModuleLoader, "_load_entry_point", entry_point)
    monkeypatch.setattr(application, "_CLOSE_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(
        application, "_install_signal_handlers",
        lambda stop: lambda: removed.append(True),
    )
    loop.set_exception_handler(lambda loop, context: unhandled.append(context))
    task = asyncio.create_task(application.run(
        config_path, ready_reporter=readiness.append,
        diagnostic_reporter=diagnostics.append,
    ))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()
        done, _ = await asyncio.wait({task}, timeout=1)
        assert done == {task}, "startup cancellation cleanup hung"
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0)
        assert activated == ["twitch", "brain", "audit"]
        assert closed == ["twitch", "brain", "audit"]
        assert all(worker.done() for worker in workers)
        assert all(closer.done() for closer in closers)
        assert asyncio.all_tasks() <= baseline
        assert readiness == []
        assert removed == [True]
        assert diagnostics == (
            [] if close_mode == "normal" else ["module 'brain': shutdown failed"]
        )
        assert unhandled == []
    finally:
        task.cancel()
        for background in workers:
            background.cancel()
        await asyncio.gather(task, *workers, return_exceptions=True)
        loop.set_exception_handler(previous_handler)
