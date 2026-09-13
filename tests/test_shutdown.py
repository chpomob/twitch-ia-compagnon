from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import core.main as application
from conftest import wait_until
from core.loader import ModuleActivation
from modules import audit, brain, twitch
from modules.twitch import HELIX_CHAT_URL
from test_brain import VALID_SETTINGS as BRAIN_SETTINGS, chat_payload, completion
from test_brain import FakeResponse as ModelResponse
from test_brain import FakeSession as ModelSession
from test_brain import runtime_context as brain_runtime_context
from test_main import (
    PHASED_MODULE_SOURCE,
    _make_module,
    _make_phased_module,
    _phased_config,
    _valid_config,
    _write_config,
)
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
    "action.completed",
    "channel.chat.sent",
    "brain.run.completed",
)
"""The traces of one accepted message through the real pipeline, in order.

AC27's order: the send service records the confirmed send before it returns
the observation, and hands the fact to the loop only through the invocation's
completion hook, after the executor's own ``action.completed`` (R8).
"""

HANDLE_GATES: dict[str, asyncio.Event] = {}
"""The gates a generated module's hooks wait on before they proceed.

Keyed by the module's ``label`` setting — the gate its ``activate()`` waits
on before returning the handle — or by ``<label>.close`` for the gate a held
``close()`` waits on; ``<label>.closing`` is the completion event that held
close sets on entry, which a test awaits instead of polling. A generated
module reaches its gate through :func:`handle_gate`, so the test that wrote
it decides — after it has observed the entry point's completion, or its
deadline — when the hook proceeds: the interleaving under test is an
explicit event, never an elapsed sleep.
"""


def handle_gate(key: str) -> asyncio.Event:
    """The gate registered under *key*; a generated module awaits it."""

    return HANDLE_GATES[key]


def _register_gate(monkeypatch, key: str) -> asyncio.Event:
    """Register a fresh, closed gate under *key* for the running loop."""

    gate = asyncio.Event()
    monkeypatch.setitem(HANDLE_GATES, key, gate)
    return gate


GATED_RETURN_ON_CANCEL_SOURCE = PHASED_MODULE_SOURCE.replace(
    "import json\n", "import asyncio\nimport json\n", 1
).replace(
    'record(settings, "activate")\n',
    'record(settings, "activate")\n'
    '    try:\n'
    '        await asyncio.Future()\n'
    '    except asyncio.CancelledError:\n'
    '        pass\n'
    '    # Cancelled, then still busy: the handle is returned only once the\n'
    '    # test opens the gate, after it observed the caller being answered.\n'
    '    from test_shutdown import handle_gate\n'
    '    await handle_gate(settings["label"]).wait()\n'
    '    record(settings, "returned")\n',
)
"""``activate()`` answering its cancellation by returning its handle late."""

HELD_CLOSE_SOURCE_FRAGMENT = (
    '        record(self.settings, "close")\n'
    '        from test_shutdown import handle_gate\n'
    '        handle_gate(self.settings["label"] + ".closing").set()\n'
    '        try:\n'
    '            await handle_gate(self.settings["label"] + ".close").wait()\n'
    '        except asyncio.CancelledError:\n'
    '            record(self.settings, "close-cancelled")\n'
    '            # Resists the one cancellation its owner gives it: it ends\n'
    '            # only when the test opens the gate, after the caller was\n'
    '            # answered, which is what retained ownership must cover.\n'
    '            await handle_gate(self.settings["label"] + ".close").wait()\n'
    '        record(self.settings, "close-released")\n'
)
"""A ``close()`` held on its gate that observes, then resists, cancellation."""


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

        async def activate_enabled(self, config, *, deadline_at=None):
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
        # published, followed only by the ``module.stopped`` facts the
        # coordinator reported for the two ordinary modules (R8): the brain
        # first, whose close published the completion, then the input.
        bus_types = [event["type"] for event in handles["bus"].list_events()]
        assert bus_types.count("brain.run.started") == 1
        assert bus_types.count("brain.run.completed") == 1
        decoded = [json.loads(record) for record in records]
        types = [r["type"] for r in decoded]
        assert types.count("brain.run.completed") == 1
        completed_at = types.index("brain.run.completed")
        assert decoded[completed_at]["payload"]["status"] == "cancelled"
        assert [
            (r["type"], r["payload"].get("module")) for r in decoded[completed_at + 1 :]
        ] == [("module.stopped", "brain"), ("module.stopped", "twitch")]
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
    """A close that resists cancellation is bounded; the observation role still closes.

    Both activations are versioned and declare their phase roles: the
    stubborn one has no role, so it closes as an ordinary resource; the
    other declares ``observation``, so the coordinator flushes and closes it
    after every ordinary close — by declared role, never by name (R4). The
    bounded close is reported against the module that overran, and the
    observation service is not skipped.
    """

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
        coordinator = application._coordinator(
            [
                ModuleActivation(
                    "brain",
                    {},
                    SimpleNamespace(close=stubborn),
                    roles=frozenset(),
                    manifest_version=2,
                ),
                ModuleActivation(
                    "audit",
                    {},
                    SimpleNamespace(close=close_audit),
                    roles=frozenset({"observation"}),
                    manifest_version=2,
                ),
            ],
            application._assemble_runtime({}),
            lambda _message: None,
        )
        report = await asyncio.wait_for(coordinator.stop(), 1)
        assert report.status == 1
        assert report.failures == ("module 'brain': phase 'close' timed out",)
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
blocked = BLOCKED
loop = None
started = None

def writer(line):
    # The writer runs in an executor thread: it signals its start to the
    # loop thread-safely, so the coroutine awaits an event, never a poll.
    loop.call_soon_threadsafe(started.set)
    if blocked:
        threading.Event().wait()

async def run(config):
    global loop, started
    loop = asyncio.get_running_loop()
    started = asyncio.Event()
    handle = AuditModule(writer, close_timeout_seconds=0.02)
    handle.handle_event({"type": "test.event", "payload": {}})
    await started.wait()
    # The declared observation role puts the writer in the flush and
    # observation-close phases; the coordinator never reads its name (R4).
    coordinator = app._coordinator(
        [ModuleActivation(
            "audit", {}, handle, roles=frozenset({"observation"}), manifest_version=2
        )],
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
    # A versioned activation with no declared role: closed as an ordinary
    # resource in the close phase, bounded and reported by module (R4).
    coordinator = app._coordinator(
        [ModuleActivation(
            "brain", {}, SimpleNamespace(close=close), roles=frozenset(), manifest_version=2
        )],
        app._assemble_runtime({}),
        lambda _message: None,
    )
    report = await coordinator.stop()
    assert report.status == 1
    assert report.failures == ("module 'brain': phase 'close' timed out",)
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
    """AC15: a cancellation during a real ``activate()``, end to end.

    The entry point is cancelled while the fourth module's ``activate()`` is
    still in flight. The module that was activating is named by a sanitised
    diagnostic before the cancellation reaches the caller, the modules whose
    activation already returned are closed through the ordinary sequence,
    and however a close behaved, the cancellation is not swallowed.
    """
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
        # AC15: the cancelling module is named, after the cleanup diagnostics
        # and before the cancellation itself reaches the caller.
        assert diagnostics == (
            ["module 'slow': field 'activate': activation was cancelled"]
            if close_mode == "normal"
            else [
                "module 'brain': shutdown failed",
                "module 'slow': field 'activate': activation was cancelled",
            ]
        )
        assert unhandled == []
    finally:
        task.cancel()
        for background in workers:
            background.cancel()
        await asyncio.gather(task, *workers, return_exceptions=True)
        loop.set_exception_handler(previous_handler)


async def test_cancelled_activation_through_the_entry_point_names_the_module(
    tmp_path, monkeypatch,
) -> None:
    """AC15: a ``CancelledError`` during a versioned ``activate()``, end to end.

    The entry point is cancelled while the last module's ``activate()`` is
    still in flight. The loader names the module it was activating, the
    entry point reports that diagnostic before the cancellation reaches the
    caller, and the modules whose activation already returned are unwound
    through the ordinary shutdown sequence.
    """

    modules = tmp_path / "modules"
    modules.mkdir()
    _make_phased_module(modules, "alpha", roles=("observation",))
    _make_phased_module(modules, "source", roles=("input",))
    _make_phased_module(
        modules,
        "slow",
        source=PHASED_MODULE_SOURCE.replace(
            "import json\n", "import asyncio\nimport json\n", 1
        ).replace(
            'record(settings, "activate")\n',
            'record(settings, "activate")\n'
            '    await asyncio.Future()\n',
        ),
    )
    lifecycle_log = tmp_path / "lifecycle.log"
    config_path = tmp_path / "config.yaml"
    _write_config(
        config_path,
        _phased_config("./modules", lifecycle_log, ("alpha", "source", "slow")),
    )
    readiness = []
    diagnostics = []
    removed = []
    monkeypatch.setattr(
        application, "_install_signal_handlers",
        lambda stop: lambda: removed.append(True),
    )

    def log_lines() -> list[str]:
        if not lifecycle_log.exists():
            return []
        return lifecycle_log.read_text(encoding="utf-8").splitlines()

    task = asyncio.create_task(application.run(
        config_path,
        ready_reporter=readiness.append,
        diagnostic_reporter=diagnostics.append,
    ))
    try:
        await wait_until(lambda: "activate:slow" in log_lines())
        task.cancel()
        done, _ = await asyncio.wait({task}, timeout=1)
        assert done == {task}, "startup cancellation cleanup hung"
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert readiness == []
    assert removed == [True]
    assert diagnostics == [
        "module 'slow': field 'activate': activation was cancelled"
    ]
    assert log_lines() == [
        "activate:alpha",
        "activate:source",
        "activate:slow",
        # slow never returned a handle: only alpha and source are unwound.
        "stop_inputs:source",
        "drain:source",
        "drain:alpha",
        "close:source",
        "flush:alpha",
        "close:alpha",
    ]


@pytest.mark.parametrize("returned", ["within-grace", "after-grace"])
async def test_a_handle_returned_to_a_cancelled_activation_is_closed_through_the_entry_point(
    tmp_path, monkeypatch, returned: str,
) -> None:
    """AC15 (P24 N2): a handle returned from ``activate()``'s cancellation
    handler is closed, end to end.

    The entry point is cancelled while the last module's ``activate()`` is
    in flight, and that hook answers its cancellation by returning the
    handle it opened — immediately, or only after the loader's grace. Within
    the grace the handle is registered before the cancellation propagates,
    so the entry point's snapshot of the activations unwinds it with the
    others; after the grace the loader is its cleanup owner and closes it
    itself, bounded, since the snapshot has already been taken.
    """

    modules = tmp_path / "modules"
    modules.mkdir()
    _make_phased_module(modules, "alpha", roles=("observation",))
    if returned == "within-grace":
        source = PHASED_MODULE_SOURCE.replace(
            "import json\n", "import asyncio\nimport json\n", 1
        ).replace(
            'record(settings, "activate")\n',
            'record(settings, "activate")\n'
            '    try:\n'
            '        await asyncio.Future()\n'
            '    except asyncio.CancelledError:\n'
            '        pass\n'
            '    record(settings, "returned")\n',
        )
    else:
        source = GATED_RETURN_ON_CANCEL_SOURCE
    _make_phased_module(modules, "slow", source=source)
    lifecycle_log = tmp_path / "lifecycle.log"
    config_path = tmp_path / "config.yaml"
    _write_config(
        config_path, _phased_config("./modules", lifecycle_log, ("alpha", "slow"))
    )
    readiness = []
    diagnostics = []
    monkeypatch.setattr(
        application, "_install_signal_handlers", lambda stop: lambda: None
    )
    gate = _register_gate(monkeypatch, "slow")

    def log_lines() -> list[str]:
        if not lifecycle_log.exists():
            return []
        return lifecycle_log.read_text(encoding="utf-8").splitlines()

    task = asyncio.create_task(application.run(
        config_path,
        ready_reporter=readiness.append,
        diagnostic_reporter=diagnostics.append,
    ))
    try:
        await wait_until(lambda: "activate:slow" in log_lines())
        task.cancel()
        done, _ = await asyncio.wait({task}, timeout=1)
        assert done == {task}, "startup cancellation cleanup hung"
        with pytest.raises(asyncio.CancelledError):
            await task
        if returned == "after-grace":
            # The caller has been answered and the handle is still held:
            # nothing has closed it. It is returned only now, so it arrives
            # after the snapshot the entry point took, and the loader closes
            # it, not that snapshot.
            assert "returned:slow" not in log_lines()
            assert "close:slow" not in log_lines()
            gate.set()
            await wait_until(lambda: "close:slow" in log_lines())
    finally:
        gate.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert readiness == []
    assert diagnostics == [
        "module 'slow': field 'activate': activation was cancelled"
    ]
    lines = log_lines()
    assert lines[:2] == ["activate:alpha", "activate:slow"]
    assert lines.count("close:slow") == 1
    if returned == "within-grace":
        # Registered in time: unwound through the ordinary sequence, as an
        # ordinary resource ahead of the observation service.
        assert lines[2:] == [
            "returned:slow", "drain:slow", "drain:alpha", "close:slow",
            "flush:alpha", "close:alpha",
        ]
    else:
        # The snapshot unwound alpha alone; the handle returned afterwards is
        # closed by the loader, once, after the caller was answered.
        assert lines[2:] == [
            "drain:alpha", "flush:alpha", "close:alpha", "returned:slow", "close:slow",
        ]


async def test_a_late_close_that_fails_after_the_entry_point_returned_is_reported(
    tmp_path, monkeypatch,
) -> None:
    """AC15 (P24 N2): a late close is never a silent cleanup failure.

    The last module's ``activate()`` returns its handle only after the
    loader's grace, so the handle arrives once ``run()`` has already been
    answered — after its settle of the closes under way found none. The
    loader closes it, and that close fails: with nothing in the entry point
    left to read the loader's record, the failure reaches the diagnostic
    reporter ``run()`` was given, which outlives the call. It is reported
    once, without the module's own message.
    """

    modules = tmp_path / "modules"
    modules.mkdir()
    _make_phased_module(modules, "alpha", roles=("observation",))
    _make_phased_module(
        modules,
        "slow",
        source=GATED_RETURN_ON_CANCEL_SOURCE.replace(
            '        record(self.settings, "close")\n',
            '        record(self.settings, "close")\n'
            '        raise RuntimeError("secret-bearing refusal to close")\n',
        ),
    )
    lifecycle_log = tmp_path / "lifecycle.log"
    config_path = tmp_path / "config.yaml"
    _write_config(
        config_path, _phased_config("./modules", lifecycle_log, ("alpha", "slow"))
    )
    readiness = []
    diagnostics = []
    monkeypatch.setattr(
        application, "_install_signal_handlers", lambda stop: lambda: None
    )
    gate = _register_gate(monkeypatch, "slow")

    def log_lines() -> list[str]:
        if not lifecycle_log.exists():
            return []
        return lifecycle_log.read_text(encoding="utf-8").splitlines()

    task = asyncio.create_task(application.run(
        config_path,
        ready_reporter=readiness.append,
        diagnostic_reporter=diagnostics.append,
    ))
    try:
        await wait_until(lambda: "activate:slow" in log_lines())
        task.cancel()
        done, _ = await asyncio.wait({task}, timeout=1)
        assert done == {task}, "startup cancellation cleanup hung"
        with pytest.raises(asyncio.CancelledError):
            await task
        # Answered before the handle arrived: the close is still to come, and
        # so is its diagnostic. The handle is returned only now.
        assert "returned:slow" not in log_lines()
        assert "close:slow" not in log_lines()
        assert diagnostics == [
            "module 'slow': field 'activate': activation was cancelled"
        ]
        gate.set()
        await wait_until(lambda: len(diagnostics) == 2)
    finally:
        gate.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert readiness == []
    assert diagnostics == [
        "module 'slow': field 'activate': activation was cancelled",
        "module 'slow': field 'close': late handle close failed",
    ]
    assert "secret-bearing" not in "\n".join(diagnostics)
    lines = log_lines()
    assert lines[:2] == ["activate:alpha", "activate:slow"]
    assert lines.count("close:slow") == 1
    assert lines[2:] == [
        "drain:alpha", "flush:alpha", "close:alpha", "returned:slow", "close:slow",
    ]


async def test_a_late_close_held_past_the_global_cleanup_deadline_does_not_extend_the_entry_point(
    tmp_path, monkeypatch,
) -> None:
    """R4/AC15 (P24 N4): one absolute cleanup deadline bounds the whole unwind.

    The entry point is cancelled while the last module's ``activate()`` is
    in flight. Its cleanup takes the one global shutdown deadline, and the
    coordinator's close of the first module is held while the abandoned
    activation returns its handle — so the handle arrives while that
    deadline is being spent, and its close is held for good. The loader's
    late close takes no fresh budget: the settle ``run()`` awaits after the
    coordinator is capped by what remains of the same deadline, at which
    point the held close is cancelled, reported as having exceeded the
    global shutdown deadline, and kept owned by the loader — which is
    where its resisted cancellation ends, after the caller was answered.
    """

    modules = tmp_path / "modules"
    modules.mkdir()
    _make_phased_module(
        modules,
        "alpha",
        roles=("observation",),
        source=PHASED_MODULE_SOURCE.replace(
            "import json\n", "import asyncio\nimport json\n", 1
        ).replace(
            '        record(self.settings, "close")\n',
            '        record(self.settings, "close")\n'
            '        from test_shutdown import handle_gate\n'
            '        handle_gate(self.settings["label"] + ".closing").set()\n'
            '        await handle_gate(self.settings["label"] + ".close").wait()\n',
        ),
    )
    _make_phased_module(
        modules,
        "slow",
        source=GATED_RETURN_ON_CANCEL_SOURCE.replace(
            '        record(self.settings, "close")\n', HELD_CLOSE_SOURCE_FRAGMENT
        ),
    )
    lifecycle_log = tmp_path / "lifecycle.log"
    config_path = tmp_path / "config.yaml"
    _write_config(
        config_path, _phased_config("./modules", lifecycle_log, ("alpha", "slow"))
    )
    readiness = []
    diagnostics = []
    loaders = []
    real_loader = application.ModuleLoader

    class Recording(real_loader):
        def __init__(self, *args, **options):
            super().__init__(*args, **options)
            loaders.append(self)

    monkeypatch.setattr(application, "ModuleLoader", Recording)
    monkeypatch.setattr(
        application, "_install_signal_handlers", lambda stop: lambda: None
    )
    # A short global shutdown deadline; the late close's own budget is left
    # at its default, far beyond it, so only the shared deadline can bound
    # the return below.
    monkeypatch.setattr(application, "_SHUTDOWN_DEADLINE_SECONDS", 0.25)
    monkeypatch.setattr(application, "_CANCEL_TIMEOUT_SECONDS", 0.01)
    returned = _register_gate(monkeypatch, "slow")
    alpha_closing = _register_gate(monkeypatch, "alpha.closing")
    alpha_close = _register_gate(monkeypatch, "alpha.close")
    slow_closing = _register_gate(monkeypatch, "slow.closing")
    slow_close = _register_gate(monkeypatch, "slow.close")

    def log_lines() -> list[str]:
        if not lifecycle_log.exists():
            return []
        return lifecycle_log.read_text(encoding="utf-8").splitlines()

    task = asyncio.create_task(application.run(
        config_path,
        ready_reporter=readiness.append,
        diagnostic_reporter=diagnostics.append,
    ))
    try:
        await wait_until(lambda: "activate:slow" in log_lines())
        task.cancel()
        # The cleanup deadline is running: the coordinator is inside the
        # first module's held close when the abandoned activation returns
        # its handle, so the loader's close of it begins under that deadline.
        await asyncio.wait_for(alpha_closing.wait(), 2)
        assert not task.done()
        returned.set()
        await asyncio.wait_for(slow_closing.wait(), 2)
        # The coordinator finishes; the held late close is all that is left.
        alpha_close.set()
        done, _ = await asyncio.wait({task}, timeout=2)
        assert done == {task}, "the late close extended the cleanup past its deadline"
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        returned.set()
        alpha_close.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert readiness == []
    # The unfinished cleanup is named before the caller is answered; the
    # interrupted activation is named last, just before the cancellation
    # propagates (AC15).
    assert diagnostics == [
        "module 'slow': field 'close': late handle close exceeded the global "
        "shutdown deadline",
        "module 'slow': field 'activate': activation was cancelled",
    ]
    assert log_lines() == [
        "activate:alpha",
        "activate:slow",
        "drain:alpha",
        "flush:alpha",
        "close:alpha",
        "returned:slow",
        "close:slow",
        # The cancellation reached the held close before the caller returned.
        "close-cancelled:slow",
    ]
    # Ownership is retained, not extended: the close that resisted its
    # cancellation is still held by the loader after the caller was
    # answered, and ends only when released — with nothing more reported.
    (loader,) = loaders
    assert len(loader.abandoned) == 1
    slow_close.set()
    await wait_until(lambda: loader.abandoned == ())
    assert log_lines()[-1] == "close-released:slow"
    assert log_lines().count("close:slow") == 1
    assert len(diagnostics) == 2
    assert await loader.settle_late_results() == []


async def test_cancelled_preparation_through_the_entry_point_names_the_module(
    tmp_path, monkeypatch,
) -> None:
    """AC15: a ``CancelledError`` during a versioned module's preparation.

    The coordinator is cancelled while the last module is still preparing.
    The entry point reports the phase the cancellation interrupted, against
    the module it interrupted, then unwinds every module already prepared
    through the ordinary shutdown sequence — the producer stopped and the
    resources closed — before the cancellation reaches the caller. The
    ``activate()`` cancellation, which the loader names instead of the
    coordinator, is covered separately.
    """

    modules = tmp_path / "modules"
    modules.mkdir()
    _make_phased_module(modules, "alpha", roles=("observation",))
    _make_phased_module(modules, "source", roles=("input",))
    _make_phased_module(
        modules,
        "slow",
        source=PHASED_MODULE_SOURCE.replace(
            'record(self.settings, "prepare")',
            'record(self.settings, "prepare")\n'
            '        await asyncio.Future()',
        ).replace("import json\n", "import asyncio\nimport json\n", 1),
    )
    lifecycle_log = tmp_path / "lifecycle.log"
    config_path = tmp_path / "config.yaml"
    _write_config(
        config_path,
        _phased_config("./modules", lifecycle_log, ("alpha", "source", "slow")),
    )
    readiness = []
    diagnostics = []
    removed = []
    monkeypatch.setattr(
        application, "_install_signal_handlers",
        lambda stop: lambda: removed.append(True),
    )

    def log_lines() -> list[str]:
        if not lifecycle_log.exists():
            return []
        return lifecycle_log.read_text(encoding="utf-8").splitlines()

    task = asyncio.create_task(application.run(
        config_path,
        ready_reporter=readiness.append,
        diagnostic_reporter=diagnostics.append,
    ))
    try:
        await wait_until(lambda: "prepare:slow" in log_lines())
        task.cancel()
        done, _ = await asyncio.wait({task}, timeout=1)
        assert done == {task}, "startup cancellation cleanup hung"
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert readiness == []
    assert removed == [True]
    assert diagnostics == ["module 'slow': phase 'prepare' was cancelled"]
    assert log_lines() == [
        "activate:alpha",
        "activate:source",
        "activate:slow",
        "prepare:alpha",
        "prepare:source",
        "prepare:slow",
        # Never started, still stopped and drained: hooks are idempotent.
        "stop_inputs:source",
        "drain:slow",
        "drain:source",
        "drain:alpha",
        "close:slow",
        "close:source",
        "flush:alpha",
        "close:alpha",
    ]
