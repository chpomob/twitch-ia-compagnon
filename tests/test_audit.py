from __future__ import annotations

import asyncio
import importlib
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from core import EventBus
from core.contracts import COUNTER_AUDIT_RECORD_LOSSES
from core.runtime import ModuleContext
from conftest import runtime_context


audit = importlib.import_module("modules.audit")

MANIFEST = {
    "name": "audit",
    "manifest_version": 2,
    "runtime_api": 2,
    "produces": [],
    "consumes": ["**"],
    "middleware": True,
    "order": 90,
    "lifecycle": {"roles": ["observation"]},
    "settings_validator": "validate_settings",
}
"""The v2 manifest the audit module ships, minus the settings schema."""

SETTINGS = {
    "output": "stdout",
    "queue": {"max_records": 1000, "max_bytes": 4 * 1024 * 1024},
}
"""The settings the manifest requires, as the example configuration ships them."""


def module_context(bus: EventBus | None = None) -> ModuleContext:
    return runtime_context(bus).for_module("audit")


async def activate(
    bus: EventBus, settings: dict | None = None
) -> audit.AuditModule:
    """Activate on a fresh runtime over *bus* and register the middleware."""

    handle = await audit.activate(module_context(bus), {**SETTINGS, **(settings or {})}, {})
    await handle.prepare()
    return handle


def test_manifest_declares_catch_all_middleware_at_order_90() -> None:
    """R7/R4 supersede the former whole-manifest equality: the manifest is
    v2 — ``manifest_version``, the ``runtime_api`` it is built against, the
    settings schema with its declared ``settings_validator`` hook, and the
    declared ``observation`` lifecycle role that makes the coordinator flush
    it last, instead of the core ordering it by name."""

    path = Path(audit.__file__).with_name("module.yaml")
    manifest = yaml.safe_load(path.read_text(encoding="utf-8"))

    schema = manifest.pop("settings_schema")
    assert manifest == MANIFEST
    assert type(manifest["order"]) is int
    assert schema["type"] == "object"
    assert sorted(schema["required"]) == ["output", "queue"]
    assert sorted(schema["properties"]["queue"]["required"]) == ["max_bytes", "max_records"]
    assert callable(getattr(audit, manifest["settings_validator"]))


@pytest.mark.asyncio
async def test_writes_one_json_line_and_preserves_event(capsys) -> None:
    bus = EventBus()
    handle = await activate(bus)
    seen: list[dict] = []
    bus.subscribe("**", lambda event: seen.append(event), order=100)

    event = await bus.publish("channel.chat.message", {"text": "hello"}, {})
    await handle.close()

    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0]) == {
        "type": "channel.chat.message",
        "payload": {"text": "hello"},
        "metadata": {},
    }
    assert seen == [event]


@pytest.mark.asyncio
async def test_activation_refuses_a_bare_bus_and_refused_settings() -> None:
    """AC26: the handle is built from the scoped context, never from a bus."""

    bus = EventBus()
    with pytest.raises(audit.AuditModuleError):
        await audit.activate(bus, SETTINGS, {})
    with pytest.raises(audit.AuditModuleError):
        await audit.activate(module_context(bus), {"output": "stdout"}, {})
    assert bus.list_events() == []


@pytest.mark.asyncio
async def test_middleware_registers_at_prepare_and_losses_reach_supervision() -> None:
    """R4, R6: registration is the prepare hook; a loss is counted in supervision."""

    context = runtime_context()
    writes: list[str] = []
    handle = await audit.activate(
        context.for_module("audit"),
        {**SETTINGS, "_writer": writes.append, "queue": {"max_records": 1, "max_bytes": 1}},
        {},
    )
    await context.bus.publish("before.prepare", {}, {})
    assert handle.pending == 0

    await handle.prepare()
    await context.bus.publish("after.prepare", {}, {})
    assert handle.losses == 1
    assert context.supervision.snapshot()[COUNTER_AUDIT_RECORD_LOSSES] == 1
    await handle.flush(0.1)
    await handle.close()
    assert writes == []


@pytest.mark.asyncio
async def test_a_blocked_writer_saturating_the_queue_drops_new_records_and_counts_the_loss() -> None:
    """AC21 (R6): with an audit queue capacity of 2 records and a writer
    held blocked, publishing 5 events completes 5 publications, at most 2
    records are admitted (one in the writer's hands, one queued), the loss
    counter reads 3 outside the queue, and 0 publications are lost."""

    context = runtime_context()
    started = asyncio.Event()
    never = asyncio.Event()
    handed: list[str] = []

    async def blocked_writer(line: str) -> None:
        handed.append(line)
        started.set()
        await never.wait()

    handle = await audit.activate(
        context.for_module("audit"),
        {
            **SETTINGS,
            "_writer": blocked_writer,
            "queue": {"max_records": 2, "max_bytes": 4096},
        },
        {},
    )
    bus = context.bus
    try:
        await handle.prepare()
        for index in range(5):
            # Each publication completes: admission never blocks on the writer.
            await asyncio.wait_for(
                bus.publish(f"saturated.event.{index}", {"value": index}, {}),
                timeout=0.5,
            )
        await asyncio.wait_for(started.wait(), timeout=1.0)

        published = [
            event
            for event in bus.list_events()
            if event["type"].startswith("saturated.event.")
        ]
        assert len(published) == 5
        assert handle.pending == 2
        assert len(handed) <= 2
        assert handle.losses == 3
        # The counter is readable outside the saturated queue, from the
        # registry in memory, never from a bus event it would itself feed.
        assert context.supervision.snapshot()[COUNTER_AUDIT_RECORD_LOSSES] == 3
    finally:
        never.set()
        await handle.close()


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
    handle = await activate(
        bus, {"_writer": delayed_failure, "_close_timeout_seconds": 0.1}
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


@pytest.mark.asyncio
async def test_records_abandoned_at_close_are_counted_as_losses() -> None:
    """R6: a cancelled writer and the records queued behind it are losses."""

    counted: list[str] = []

    class Supervision:
        def count(self, name: str) -> None:
            counted.append(name)

    started = asyncio.Event()
    never = asyncio.Event()

    async def stuck_writer(line: str) -> None:
        started.set()
        await never.wait()

    handle = audit.AuditModule(
        stuck_writer,
        max_records=2,
        close_timeout_seconds=0.05,
        supervision=Supervision(),
    )
    handle.handle_event({"type": "first", "payload": {}, "metadata": {}})
    handle.handle_event({"type": "second", "payload": {}, "metadata": {}})
    await asyncio.wait_for(started.wait(), 1.0)
    assert handle.pending == 2

    await handle.close()

    assert handle.losses == 2
    assert counted == [audit.COUNTER_AUDIT_RECORD_LOSSES] * 2
    assert handle.pending == 0
    assert handle.pending_bytes == 0


@pytest.mark.asyncio
async def test_close_does_not_block_the_loop_on_a_stalled_file_sink(
    tmp_path: Path,
) -> None:
    """A3: closing the file sink runs off the loop and within the budget."""

    sink = audit._FileSink(str(tmp_path / "audit.log"))
    handle = audit.AuditModule(sink, close_timeout_seconds=0.05)
    handle.handle_event({"type": "first", "payload": {}, "metadata": {}})
    await handle.close()
    assert handle.losses == 0

    stalled = audit._FileSink(str(tmp_path / "stalled.log"))
    loop = asyncio.get_running_loop()
    pings: asyncio.Queue[None] = asyncio.Queue()
    pong = threading.Event()
    release = threading.Event()

    def blocking_close() -> None:
        # Two round trips through the loop while this close is under way:
        # each ping is answered by a coroutine, so the loop must be serving
        # while the close is stalled — an inline close could answer none.
        for _ in range(2):
            pong.clear()
            loop.call_soon_threadsafe(pings.put_nowait, None)
            pong.wait(2.0)
        release.wait(2.0)

    # The stalled file handle is a stand-in, not a patched file object: a
    # real file's finalizer would call the patched close a second time.
    stalled._handle = SimpleNamespace(close=blocking_close)
    handle = audit.AuditModule(stalled, close_timeout_seconds=0.05)

    ticks = 0

    async def heartbeat() -> None:
        nonlocal ticks
        while True:
            await pings.get()
            ticks += 1
            pong.set()

    beat = asyncio.create_task(heartbeat())
    await asyncio.wait_for(handle.close(), 1.0)
    beat.cancel()
    release.set()

    assert ticks >= 2


# --------------------------------------------------------------------------- #
# The accepted limits block against the owned queue bounds (R6, P24 F6)
# --------------------------------------------------------------------------- #


def test_settings_hook_accepts_a_queue_equal_to_the_accepted_observation_queue() -> None:
    """R6: handed the accepted ``limits`` block, an equal ``queue`` passes;
    without the block, or with a block that lacks the group, the ``queue``
    settings are the only copy and pass on their own terms."""

    accepted = {"observation_queue": dict(SETTINGS["queue"]), "dedup": {"max_entries": 1}}

    assert audit.validate_settings({**SETTINGS, "limits": accepted}) == []
    assert audit.validate_settings(SETTINGS) == []
    assert audit.validate_settings({**SETTINGS, "limits": {"dedup": {"max_entries": 1}}}) == []


@pytest.mark.parametrize(
    ("queue", "expected"),
    [
        (
            {"max_records": 999, "max_bytes": 4 * 1024 * 1024},
            ["module 'audit': field 'queue.max_records': must equal limits.observation_queue.max_records"],
        ),
        (
            {"max_records": 1000, "max_bytes": 1},
            ["module 'audit': field 'queue.max_bytes': must equal limits.observation_queue.max_bytes"],
        ),
        (
            {"max_records": 2, "max_bytes": 1},
            [
                "module 'audit': field 'queue.max_records': must equal limits.observation_queue.max_records",
                "module 'audit': field 'queue.max_bytes': must equal limits.observation_queue.max_bytes",
            ],
        ),
    ],
)
def test_settings_hook_refuses_a_queue_bound_that_differs_from_the_accepted_one(
    queue: dict[str, int], expected: list[str]
) -> None:
    """R6 (P24 F6): a ``queue`` bound that differs from the accepted
    ``limits.observation_queue`` is refused by field, value-free, so the queue
    is never bounded by a mirror the entry point did not accept."""

    settings = {**SETTINGS, "queue": queue, "limits": {"observation_queue": dict(SETTINGS["queue"])}}

    diagnostics = audit.validate_settings(settings)

    assert diagnostics == expected
    for diagnostic in diagnostics:
        for value in (*queue.values(), *SETTINGS["queue"].values()):
            assert str(value) not in diagnostic


def test_settings_hook_reports_a_malformed_accepted_block_by_field() -> None:
    """A handed block that is not a mapping, or a group that is not, is a
    defect of whoever built the settings and is named as such."""

    assert audit.validate_settings({**SETTINGS, "limits": "1000"}) == [
        "module 'audit': field 'limits': must be a mapping of limit groups"
    ]
    assert audit.validate_settings({**SETTINGS, "limits": {"observation_queue": [1000]}}) == [
        "module 'audit': field 'limits.observation_queue': must be a mapping of limits"
    ]


@pytest.mark.asyncio
async def test_activation_refuses_a_queue_that_differs_from_the_accepted_bounds() -> None:
    """R6: the same hook decides at activation, so a handle is never built on
    a bound the configuration was not accepted with."""

    bus = EventBus()
    with pytest.raises(audit.AuditModuleError):
        await audit.activate(
            module_context(bus),
            {**SETTINGS, "limits": {"observation_queue": {"max_records": 2, "max_bytes": 4096}}},
            {},
        )
    assert bus.list_events() == []
