from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import core.main as application
from core.loader import ModuleActivation
from modules import audit, brain, twitch
from modules.twitch import HELIX_CHAT_URL
from test_brain import VALID_SETTINGS as BRAIN_SETTINGS, chat_payload, completion
from test_brain import FakeResponse as ModelResponse
from test_brain import FakeSession as ModelSession
from test_brain import runtime_context as brain_runtime_context
from test_main import _make_module, _valid_config, _write_config
from test_twitch import SETTINGS as TWITCH_SETTINGS
from test_twitch import FakeResponse, FakeSession, FakeWebSocket, notification, welcome


AUDIT_SETTINGS = {
    "output": "stdout",
    "queue": {"max_records": 1000, "max_bytes": 4 * 1024 * 1024},
}
"""The settings the audit manifest requires; the writer seam replaces the output."""

PIPELINE_TRACES = (
    "channel.chat.message",
    "brain.admission.accepted",
    "brain.run.started",
    "action.started",
    "channel.chat.sent",
    "action.completed",
    "brain.run.completed",
)
"""The traces of one accepted message through the real pipeline, in order.

The confirmed-send fact precedes the executor's terminal trace because the
send service records and emits it before it returns the observation (R8).
"""


def _grant_brain_delivery(monkeypatch) -> None:
    """Hand the entry point the one grant the brain's reply needs.

    The runtime's policy holds exactly what the actions block configures (R5:
    declaring an action never authorizes it), and this harness stubs
    load_config down to a bare mapping, so the grant rides the stubbed
    configuration — the same route production configuration takes.
    """

    monkeypatch.setattr(
        application,
        "load_config",
        lambda *args, **kwargs: {
            "modules_directory": ".",
            "actions": [
                {
                    "rule_id": "brain-chat-write",
                    "action_name": brain.DELIVERY_ACTION,
                    "principals": [brain.PRINCIPAL],
                    "granted_permissions": ["chat.write"],
                }
            ],
        },
    )


@pytest.mark.parametrize("phase", ["model", "send", "stuck_model", "startup"])
async def test_shutdown_drains_real_pipeline(monkeypatch, phase: str) -> None:
    """Shutdown drains the in-flight run before any transport closes.

    The brain is a versioned module now (R2): its handler admits the input and
    returns, and the run — model call, executor delivery, confirmed send — is
    owned by the scheduler the brain built. The coordinator stops the input
    first, drains the run under the drain hook (R4), then closes the
    transports. The former record order ``channel.chat.send`` then
    ``channel.chat.message`` — the output-before-input invariant of the
    synchronous publication chain — is superseded by R2's detached run
    ownership and R4's phase order: the input is recorded at ingestion and
    the run's own traces follow it, the confirmed send before the run's
    completion (R8). The former ``module 'twitch': phase 'drain' timed out``
    of a model that never answers is therefore the brain's drain timing out.
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
        200, completion("bonjour")
    )
    send_response = (DelayedResponse if phase == "send" else FakeResponse)(
        200, {"data": [{"message_id": "sent", "is_sent": True}]}
    )
    model_session = ModelSession(model_response)
    websocket = FakeWebSocket(welcome("session"), notification("in-flight"))
    twitch_session = FakeSession([websocket], sends=[send_response])

    class Loader:
        def __init__(self, bus, directory):
            self.bus = bus
            self.context = None
            self.activations = []
            handles["bus"] = bus

        async def activate_enabled(self, config):
            handles["brain"] = await brain.activate(
                self.context.for_module("brain"),
                {**BRAIN_SETTINGS, "_session_factory": lambda: model_session},
                {},
            )
            handles["audit"] = await audit.activate(
                self.context.for_module("audit"),
                {**AUDIT_SETTINGS, "_writer": records.append},
                {},
            )
            handles["twitch"] = await twitch.activate(
                self.context.for_module("twitch"),
                {**TWITCH_SETTINGS, "_session_factory": lambda: twitch_session},
                {},
            )
            # Match the production activation order that formerly deadlocked:
            # the input first, then the two consumers; the brain declares no
            # lifecycle role and the audit declares the observation role, so
            # it flushes and closes after every ordinary resource (R4).
            self.activations = [
                ModuleActivation(
                    "twitch",
                    {},
                    handles["twitch"],
                    roles=frozenset({"input"}),
                    manifest_version=2,
                ),
                ModuleActivation(
                    "brain", {}, handles["brain"], roles=frozenset(), manifest_version=2
                ),
                ModuleActivation(
                    "audit",
                    {},
                    handles["audit"],
                    roles=frozenset({"observation"}),
                    manifest_version=2,
                ),
            ]
            if phase == "startup":
                # The coordinator never starts here, so the harness prepares
                # both versioned modules and opens the producer itself to hold
                # a run in flight; the coordinator's own calls, if any, are
                # idempotent no-ops.
                await handles["brain"].prepare()
                await handles["audit"].prepare()
                await handles["twitch"].prepare()
                await handles["twitch"].start_inputs()
                await asyncio.Future()
            return self.activations

    monkeypatch.setattr(application, "ModuleLoader", Loader)
    _grant_brain_delivery(monkeypatch)
    if phase == "stuck_model":
        # Below the brain's drain poll interval, so the coordinator's timer
        # and the drain's own deadline check never coincide: the hook is
        # reported as overrunning, deterministically, while the run is held.
        monkeypatch.setattr(application, "_CLOSE_TIMEOUT_SECONDS", 0.02)
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
    # Wait until shutdown has stopped the producer while the run is held.
    await asyncio.wait_for(asyncio.gather(receiver, return_exceptions=True), 1)
    assert not task.done()
    assert not handles["audit"]._closed
    assert twitch_session.close_calls == 0
    assert model_session.close_calls == 0
    websocket.feed(notification("too-late"))
    if phase == "stuck_model":
        assert await asyncio.wait_for(task, 1) == 1
        assert diagnostics == ["module 'brain': phase 'drain' timed out"]
        assert model_session.close_calls == twitch_session.close_calls == 1
        assert handles["audit"]._writer_task.done()
        assert not handles["twitch"]._publication_tasks
        # The held run was ended by the close, recorded once, cancelled, with
        # the model call it had already issued counted (R2, R8).
        (record,) = handles["brain"].scheduler.run_records().values()
        assert record.status == "cancelled"
        assert record.model_calls == 1
        assert record.sends == 0
        # The completion is traced once, on the bus, and the audit — an
        # observation service, flushed and closed after every ordinary
        # resource (R4) — still records the trace the brain's close
        # published, so its records end with the cancelled completion.
        bus_types = [event["type"] for event in handles["bus"].list_events()]
        assert bus_types.count("brain.run.started") == 1
        assert bus_types.count("brain.run.completed") == 1
        decoded = [json.loads(record) for record in records]
        assert [r["type"] for r in decoded].count("brain.run.completed") == 1
        assert decoded[-1]["type"] == "brain.run.completed"
        assert decoded[-1]["payload"]["status"] == "cancelled"
        assert handles["audit"].losses == 0
        return
    release.set()
    if phase == "startup":
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1)
    else:
        assert await asyncio.wait_for(task, 1) == 0
    assert diagnostics == []
    decoded = [json.loads(record) for record in records]
    pipeline = [record for record in decoded if record["type"] in PIPELINE_TRACES]
    assert [record["type"] for record in pipeline] == list(PIPELINE_TRACES)
    assert pipeline[0]["payload"]["message_id"] == "in-flight"
    run_ids = {record["payload"]["run_id"] for record in pipeline[1:]}
    assert len(run_ids) == 1
    assert pipeline[-1]["payload"]["status"] == "success"
    assert pipeline[-1]["payload"]["delivery"] == "success"
    assert [r["type"] for r in decoded].count("channel.chat.send") == 0
    helix_calls = [
        call for call in twitch_session.post_calls if call["url"] == HELIX_CHAT_URL
    ]
    assert [call["json"]["message"] for call in helix_calls] == ["bonjour"]
    (record,) = handles["brain"].scheduler.run_records().values()
    assert record.status == "success"
    assert record.sends == 1
    assert model_session.close_calls == twitch_session.close_calls == 1
    assert handles["audit"]._writer_task.done()
    assert not handles["twitch"]._publication_tasks


async def test_brain_close_ends_the_held_run_without_blocking_the_publisher() -> None:
    """Replaces "close waits for the handler" (allowlisted; R2, R4).

    The former test asserted that the brain's close blocked while the
    publication handler awaited the model. Under R2 the handler admits and
    returns before the model responds, so the publisher is never held; the
    run is the brain's own admitted work, and close ends it ``cancelled``
    (R4) instead of waiting on a model that has not answered — the record
    and its one ``brain.run.completed`` are written before the session closes.
    """

    entered = asyncio.Event()
    release = asyncio.Event()

    class Response(ModelResponse):
        async def json(self):
            entered.set()
            await release.wait()
            return await super().json()

    session = ModelSession(Response(200, completion("hello")))
    context = brain_runtime_context()
    handle = await brain.activate(
        context.for_module("brain"),
        {**BRAIN_SETTINGS, "_session_factory": lambda: session},
        {},
    )
    await handle.prepare()
    published = asyncio.Event()

    async def caller():
        await context.bus.publish("channel.chat.message", chat_payload(), {})
        published.set()
        await asyncio.Future()

    task = asyncio.create_task(caller())
    try:
        # The publication returns while the model is still held (R2).
        await asyncio.wait_for(published.wait(), 1)
        await asyncio.wait_for(entered.wait(), 1)
        assert not task.done()
        assert handle.scheduler.run_records() == {}

        # Close does not wait for the model: the held run is cancelled.
        await asyncio.wait_for(handle.close(), 1)
        assert not release.is_set()
        (record,) = handle.scheduler.run_records().values()
        assert record.status == "cancelled"
        assert record.model_calls == 1
        completions = [
            event for event in context.bus.list_events()
            if event["type"] == "brain.run.completed"
        ]
        assert len(completions) == 1
        assert completions[0]["payload"]["status"] == "cancelled"
        assert not task.done()
        assert session.close_calls == 1
    finally:
        release.set()
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
