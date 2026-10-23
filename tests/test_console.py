"""The runtime's supervision feed (R6; AC18, AC19).

The feed publisher is driven against a real :class:`~core.bus.EventBus` with
an injected writer, and ``run`` against fixture modules written to
``tmp_path`` whose activation hands their context to a hook registry the test
installs in ``sys.modules``. No socket is opened and no test sleeps for a
positive duration: the background writer is reached by yielding once to the
loop, and ``close`` writes what it has not written yet.
"""

from __future__ import annotations

import asyncio
import json
import os
import stat
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

import core.main as application
from core.bus import EventBus
from core.contracts import (
    TRACE_ACTION_COMPLETED,
    TRACE_BRAIN_ADMISSION_ACCEPTED,
    TRACE_BRAIN_ADMISSION_REJECTED,
    TRACE_BRAIN_RUN_COMPLETED,
    TRACE_BRAIN_RUN_STARTED,
    TRACE_INPUT_TRIGGER_ACCEPTED,
    TRACE_INPUT_TRIGGER_REJECTED,
    TRACE_MODULE_DEGRADED,
    TRACE_MODULE_READY,
    TRACE_MODULE_STOPPED,
)
from core.overlay import (
    FEED_FILE_VARIABLE,
    STATUS_FILE_VARIABLE,
    default_feed_path,
    feed_path_collision,
)


CHAT_EVENT = "channel.chat.message"
SECRET = "feed-secret-value-7c3a"
HOOKS_MODULE = "_console_feed_fixture_hooks"
TOKEN_VARIABLE = "CONSOLE_FEED_TEST_TOKEN"
CANARY = "CANARY-FEED-91b4"
M, N = "mfeed", "nfeed"


class _Recorder:
    """An injected writer keeping every text it was handed."""

    def __init__(self) -> None:
        self.texts: list[str] = []

    def __call__(self, path: Path, text: str) -> None:
        self.texts.append(text)

    def lines(self) -> list[dict[str, Any]]:
        return [json.loads(line) for line in self.texts[-1].splitlines()]


def _publisher(
    tmp_path: Path,
    *,
    writer: Any = None,
    secrets: Any = (),
    reports: list[str] | None = None,
) -> application._FeedPublisher:
    return application._FeedPublisher(
        tmp_path / "config.yaml.feed.jsonl",
        secrets=secrets if callable(secrets) else (lambda: secrets),
        report=(reports if reports is not None else []).append,
        writer=writer,
    )


async def _until(predicate: Any, timeout: float = 5.0) -> None:
    """Yield until *predicate* holds: the feed writes from its own thread."""

    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "timed out"
        await asyncio.sleep(0)


def _chat(text: str, *, author: str = "viewer", platform: str = "fakeplatform") -> dict[str, Any]:
    return {
        "platform": platform,
        "channel_id": "c1",
        "author": {"id": "u1", "display_name": author},
        "message_id": "m1",
        "text": text,
    }


# --------------------------------------------------------------------------- #
# Overlay helpers (DD9)
# --------------------------------------------------------------------------- #


def test_feed_variable_and_default_path() -> None:
    """DD9: the variable is named like the status one; the default path is
    the base file's full name with ``.feed.jsonl`` appended."""

    assert FEED_FILE_VARIABLE == "TWITCH_IA_COMPAGNON_FEED_FILE"
    assert default_feed_path("/x/profile.yaml") == Path("/x/profile.yaml.feed.jsonl")


def test_feed_path_collision_names_the_file_by_identity(tmp_path: Path) -> None:
    """AC19: base, overlay and status are each named; identity, not path text."""

    base = tmp_path / "config.yaml"
    base.write_text("{}\n", encoding="utf-8")
    overlay = tmp_path / "config.local.yaml"
    status = tmp_path / "config.yaml.status.json"
    linked = tmp_path / "linked.jsonl"
    os.link(base, linked)

    assert feed_path_collision(linked, base, overlay, status) == "base"
    assert feed_path_collision(tmp_path / "x" / ".." / "config.local.yaml", base, overlay, status) == "overlay"
    assert feed_path_collision(status, base, overlay, status) == "status"
    assert feed_path_collision(status, base, None, None) is None
    assert feed_path_collision(default_feed_path(base), base, overlay, status) is None


# --------------------------------------------------------------------------- #
# AC18 — bounded, redacted, never in the way of the bus
# --------------------------------------------------------------------------- #


async def test_250_facts_keep_exactly_the_latest_200_in_publication_order(
    tmp_path: Path,
) -> None:
    """AC18: 250 supervision facts leave exactly the latest 200, in order."""

    bus = EventBus()
    writer = _Recorder()
    feed = _publisher(tmp_path, writer=writer)
    feed.attach(bus)
    facts = [
        (TRACE_MODULE_READY, {"module": f"m{index}", "state": "ready"})
        if index % 2
        else (CHAT_EVENT, _chat(f"message {index}"))
        for index in range(250)
    ]
    for event_type, payload in facts:
        await bus.publish(event_type, payload, {})
    feed.close()

    lines = writer.lines()
    assert len(lines) == 200
    assert [line["seq"] for line in lines] == list(range(51, 251))
    for line, (event_type, payload) in zip(lines, facts[50:]):
        assert line["fact"] == event_type
        if event_type == CHAT_EVENT:
            assert line["excerpt"] == payload["text"]
            assert line["author"] == "viewer"
            assert line["platform"] == "fakeplatform"
            assert line["kind"] == "message"
        else:
            assert line["module"] == payload["module"]


async def test_every_supervision_fact_is_an_entry_and_nothing_else_is(
    tmp_path: Path,
) -> None:
    """R6: the facts the console reads, with their reason tokens; a completed
    action, an action starting, a message sent and an unrelated event are not
    facts, and only scalar correlation fields are copied."""

    bus = EventBus()
    writer = _Recorder()
    feed = _publisher(tmp_path, writer=writer)
    feed.attach(bus)
    published = [
        (TRACE_INPUT_TRIGGER_ACCEPTED, {"input": "chat", "platform": "p", "reason": "matched"}),
        (TRACE_INPUT_TRIGGER_REJECTED, {"input": "chat", "platform": "p", "reason": "cooldown"}),
        (TRACE_BRAIN_ADMISSION_ACCEPTED, {"run_id": "r1", "platform": "p", "kind": "message", "reason": "admitted"}),
        (TRACE_BRAIN_ADMISSION_REJECTED, {"platform": "p", "kind": "message", "reason": "queue_full"}),
        (TRACE_BRAIN_RUN_STARTED, {"run_id": "r1", "platform": "p", "kind": "message"}),
        (TRACE_BRAIN_RUN_COMPLETED, {"run_id": "r1", "status": "failed", "reason": "budget_exhausted"}),
        (TRACE_ACTION_COMPLETED, {"action": "chat.write", "status": "succeeded", "error_code": None}),
        (TRACE_ACTION_COMPLETED, {"action": "chat.write", "status": "refused", "error_code": "not_authorized", "run_id": "r1"}),
        (TRACE_MODULE_DEGRADED, {"module": "m", "state": "degraded", "reason": "socket lost"}),
        (TRACE_MODULE_STOPPED, {"module": "m", "state": "stopped"}),
        ("action.started", {"action": "chat.write"}),
        ("channel.chat.sent", {"text": "hello"}),
        ("some.other.event", {"x": 1}),
    ]
    for event_type, payload in published:
        await bus.publish(event_type, payload, {})
    feed.runtime_fact(application.FEED_RUNTIME_STOPPING)
    feed.close()

    lines = [{k: v for k, v in line.items() if k not in ("seq", "at")} for line in writer.lines()]
    assert lines == [
        {"fact": TRACE_INPUT_TRIGGER_ACCEPTED, "input": "chat", "platform": "p", "reason": "matched"},
        {"fact": TRACE_INPUT_TRIGGER_REJECTED, "input": "chat", "platform": "p", "reason": "cooldown"},
        {"fact": TRACE_BRAIN_ADMISSION_ACCEPTED, "run_id": "r1", "platform": "p", "kind": "message", "reason": "admitted"},
        {"fact": TRACE_BRAIN_ADMISSION_REJECTED, "platform": "p", "kind": "message", "reason": "queue_full"},
        {"fact": TRACE_BRAIN_RUN_STARTED, "run_id": "r1", "platform": "p", "kind": "message"},
        {"fact": TRACE_BRAIN_RUN_COMPLETED, "run_id": "r1", "status": "failed", "reason": "budget_exhausted"},
        {"fact": TRACE_ACTION_COMPLETED, "action": "chat.write", "status": "refused", "error_code": "not_authorized", "run_id": "r1"},
        {"fact": TRACE_MODULE_DEGRADED, "module": "m", "reason": "socket lost"},
        {"fact": TRACE_MODULE_STOPPED, "module": "m"},
        {"fact": "runtime.stopping"},
    ]


async def test_a_5000_character_message_is_bounded(tmp_path: Path) -> None:
    """AC18: entry ≤ 1 024 bytes, excerpt ≤ 80 characters, even when every
    field is long and multi-byte."""

    bus = EventBus()
    writer = _Recorder()
    feed = _publisher(tmp_path, writer=writer)
    feed.attach(bus)
    await bus.publish(CHAT_EVENT, _chat("é" * 5000), {})
    await bus.publish(CHAT_EVENT, _chat("x" * 5000, author="ü" * 5000, platform="p" * 5000), {})
    feed.close()

    raw = writer.texts[-1].splitlines()
    assert len(raw) == 2
    for text, line in zip(raw, writer.lines()):
        assert len(text.encode("utf-8")) <= 1024
        assert len(line["excerpt"]) <= 80
    assert writer.lines()[0]["excerpt"] == "é" * 80
    assert writer.lines()[1]["truncated"] is True


async def test_a_secret_is_redacted_before_the_entry_is_cut(tmp_path: Path) -> None:
    """AC18: a ``secrets`` value in the text is redacted — including where the
    excerpt cut falls inside it, so no prefix of it is left."""

    bus = EventBus()
    writer = _Recorder()
    feed = _publisher(tmp_path, writer=writer, secrets=(SECRET,))
    feed.attach(bus)
    await bus.publish(CHAT_EVENT, _chat("a" * 74 + SECRET + " tail"), {})
    await bus.publish(CHAT_EVENT, _chat(f"key {SECRET} end", author=SECRET * 300), {})
    await bus.publish(TRACE_MODULE_DEGRADED, {"module": "m", "reason": f"bad {SECRET}"}, {})
    feed.close()

    text = writer.texts[-1]
    assert SECRET[:4] not in text
    lines = writer.lines()
    assert lines[0]["excerpt"] == "a" * 74 + "[REDAC"
    assert lines[1]["excerpt"] == "key [REDACTED] end"
    assert lines[1]["author"] == "[REDACTED]"
    assert lines[2]["reason"] == "bad [REDACTED]"


async def test_the_secrets_are_read_at_each_entry(tmp_path: Path) -> None:
    """The loader learns the declared credentials after the feed subscribed:
    a value known by the time an entry is built is redacted from it."""

    known: list[str] = []
    bus = EventBus()
    writer = _Recorder()
    feed = _publisher(tmp_path, writer=writer, secrets=lambda: tuple(known))
    feed.attach(bus)
    known.append(SECRET)
    await bus.publish(CHAT_EVENT, _chat(SECRET), {})
    feed.close()

    assert SECRET not in writer.texts[-1]


async def test_a_raising_writer_never_reaches_the_bus(tmp_path: Path) -> None:
    """AC18: a feed write that raises leaves every publication completing and
    every handler invoked; the failure is reported once, value-free."""

    bus = EventBus()
    calls: list[str] = []
    reports: list[str] = []
    bus.subscribe(CHAT_EVENT, lambda event: calls.append("before"), order=-5)

    def failing(path: Path, text: str) -> None:
        calls.append("write")
        raise OSError(SECRET)

    feed = _publisher(tmp_path, writer=failing, secrets=(SECRET,), reports=reports)
    feed.attach(bus)
    # Registered after the feed and at a lower order: still invoked.
    bus.subscribe(CHAT_EVENT, lambda event: calls.append("after"), order=10)

    for index in range(3):
        event = await bus.publish(CHAT_EVENT, _chat(f"hello {index}"), {})
        assert event["payload"]["text"] == f"hello {index}"
        # Let the writer thread run, and fail, before the next one.
        await _until(lambda: calls.count("write") > index)
    feed.close()

    assert calls.count("before") == 3 and calls.count("after") == 3
    assert calls.count("write") >= 3
    assert reports == ["feed: could not be written"]
    assert [record["type"] for record in bus.list_events()] == [CHAT_EVENT] * 3
    assert len(feed.entries) == 3


async def test_a_stalled_writer_never_holds_up_the_bus(tmp_path: Path) -> None:
    """R6: the file is written off the event loop, so a write that stalls
    leaves every publication completing; closing waits for it only within
    its bound, and says so, value-free."""

    bus = EventBus()
    reports: list[str] = []
    writing = threading.Event()
    release = threading.Event()

    def stalled(path: Path, text: str) -> None:
        writing.set()
        release.wait(10)

    feed = application._FeedPublisher(
        tmp_path / "config.yaml.feed.jsonl",
        secrets=lambda: (),
        report=reports.append,
        writer=stalled,
        close_timeout=0.05,
    )
    feed.attach(bus)
    await bus.publish(CHAT_EVENT, _chat("first"), {})
    await _until(writing.is_set)
    for index in range(5):
        await asyncio.wait_for(bus.publish(CHAT_EVENT, _chat(f"more {index}"), {}), 1)
    assert len(feed.entries) == 6

    feed.close()
    release.set()

    assert reports == ["feed: could not be written before shutdown"]


async def test_a_failing_entry_never_fails_the_publication(tmp_path: Path) -> None:
    """The handler never raises: an entry that cannot be built is dropped."""

    def broken() -> tuple[str, ...]:
        raise RuntimeError("no secrets")

    bus = EventBus()
    writer = _Recorder()
    feed = _publisher(tmp_path, writer=writer, secrets=broken)
    feed.attach(bus)
    await bus.publish(CHAT_EVENT, _chat("hello"), {})
    feed.close()

    assert [record["type"] for record in bus.list_events()] == [CHAT_EVENT]
    assert writer.texts == []


async def test_the_handler_is_synchronous_and_subscribed_last(tmp_path: Path) -> None:
    """Risk: the handler awaits nothing and sees the event after every other
    handler, so it neither reorders nor slows the chain."""

    bus = EventBus()
    feed = _publisher(tmp_path, writer=_Recorder())
    feed.attach(bus)
    seen: list[str] = []
    bus.subscribe(CHAT_EVENT, lambda event: {**event, "payload": {**event["payload"], "text": "replaced"}}, order=1000)
    assert feed.handle({"type": CHAT_EVENT, "payload": _chat("direct")}) is None
    await bus.publish(CHAT_EVENT, _chat("original"), {})
    seen.extend(entry for entry in feed.entries)
    feed.close()

    assert json.loads(seen[-1])["excerpt"] == "replaced"


# --------------------------------------------------------------------------- #
# The runtime: AC19, and the feed through ``run``
# --------------------------------------------------------------------------- #


FIXTURE_SOURCE = f"""
import {HOOKS_MODULE} as hooks


class Handle:
    async def prepare(self):
        return None

    async def close(self):
        return None


async def activate(context, settings, catalog):
    hooks.contexts[context.module] = context
    hooks.buses.append(context.bus)
    return Handle()
"""


@pytest.fixture
def hooks(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    registry = SimpleNamespace(contexts={}, buses=[])
    monkeypatch.setitem(sys.modules, HOOKS_MODULE, registry)
    monkeypatch.delenv(FEED_FILE_VARIABLE, raising=False)
    monkeypatch.delenv(STATUS_FILE_VARIABLE, raising=False)
    return registry


def _setup(tmp_path: Path) -> Path:
    for name in (M, N):
        directory = tmp_path / "modules" / name
        directory.mkdir(parents=True)
        manifest = {
            "name": name,
            "manifest_version": 2,
            "runtime_api": 2,
            "produces": [],
            "consumes": [],
            "middleware": False,
            "lifecycle": {"roles": []},
        }
        (directory / "module.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
        (directory / "__init__.py").write_text(FIXTURE_SOURCE, encoding="utf-8")
    config = {
        "modules_directory": "./modules",
        "enabled_modules": [M, N],
        "modules": {name: {"token": "${" + TOKEN_VARIABLE + "}"} for name in (M, N)},
        "secrets": ["${" + TOKEN_VARIABLE + "}"],
    }
    base = tmp_path / "config.yaml"
    base.write_text(yaml.safe_dump(config), encoding="utf-8")
    return base


async def _run(
    base: Path, environ: dict[str, str], hooks: SimpleNamespace
) -> tuple[int, list[str], list[str]]:
    """Run until ready, degrade M with the canary in its reason, stop.

    Returns the status, the diagnostics and the bus history's event types.
    """

    diagnostics: list[str] = []
    ready = asyncio.Event()
    stop = asyncio.Event()
    task = asyncio.create_task(
        application.run(
            base,
            stop,
            environ={TOKEN_VARIABLE: CANARY, **environ},
            ready_reporter=lambda _message: ready.set(),
            diagnostic_reporter=diagnostics.append,
        )
    )
    waiter = asyncio.create_task(ready.wait())
    await asyncio.wait({task, waiter}, return_when=asyncio.FIRST_COMPLETED)
    waiter.cancel()
    assert ready.is_set(), diagnostics
    await hooks.contexts[M].supervision.degraded(reason=f"lost {CANARY}")
    await hooks.contexts[M].supervision.ready()
    history = [record["type"] for record in hooks.buses[0].list_events()]
    stop.set()
    status = await task
    return status, diagnostics, history


async def test_without_the_variable_no_feed_is_written_and_nothing_changes(
    tmp_path: Path, hooks: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC19: without the variable, 0 feed files, no publisher constructed,
    and the same bus history and status records as with the feed."""

    base = _setup(tmp_path)
    status = tmp_path / "config.yaml.status.json"
    feed_file = tmp_path / "elsewhere.feed.jsonl"

    status_with, diagnostics_with, history_with = await _run(
        base, {STATUS_FILE_VARIABLE: str(status), FEED_FILE_VARIABLE: str(feed_file)}, hooks
    )
    record_with = json.loads(status.read_text(encoding="utf-8"))
    assert feed_file.exists()
    feed_file.unlink()
    status.unlink()

    hooks.contexts.clear()
    hooks.buses.clear()
    monkeypatch.setattr(
        application,
        "_FeedPublisher",
        lambda *args, **kwargs: pytest.fail("feed publisher constructed"),
    )
    status_without, diagnostics_without, history_without = await _run(
        base, {STATUS_FILE_VARIABLE: str(status)}, hooks
    )
    record_without = json.loads(status.read_text(encoding="utf-8"))

    assert diagnostics_with == diagnostics_without == []
    assert status_with == status_without
    assert sorted(path.name for path in tmp_path.rglob("*.feed.jsonl")) == []
    assert history_with == history_without
    for record in (record_with, record_without):
        for key in ("pid", "started_at", "published_at"):
            record.pop(key)
    assert record_with == record_without


async def test_the_feed_through_run(tmp_path: Path, hooks: SimpleNamespace) -> None:
    """R6: the runtime's facts reach the feed, redacted of the configured
    secrets, in an owner-only file beside the status record."""

    base = _setup(tmp_path)
    feed_file = default_feed_path(base)

    status, diagnostics, _ = await _run(base, {FEED_FILE_VARIABLE: str(feed_file)}, hooks)

    assert diagnostics == []
    assert status in (0, 1)  # the coordinator's drain tie on shutdown (memory)
    text = feed_file.read_text(encoding="utf-8")
    assert CANARY not in text
    lines = [json.loads(line) for line in text.splitlines()]
    facts = [line["fact"] for line in lines]
    assert facts.count("runtime.started") == 1 and facts.count("runtime.stopping") == 1
    assert facts.index(TRACE_MODULE_READY) < facts.index("runtime.started")
    assert facts.index("runtime.started") < facts.index(TRACE_MODULE_DEGRADED)
    assert facts.index("runtime.stopping") < facts.index(TRACE_MODULE_STOPPED)
    assert [line["seq"] for line in lines] == list(range(1, len(lines) + 1))
    degraded = lines[facts.index(TRACE_MODULE_DEGRADED)]
    assert degraded["module"] == M and degraded["reason"].startswith("lost ")
    if os.name == "posix":
        assert stat.S_IMODE(feed_file.stat().st_mode) == 0o600
    assert not [entry for entry in tmp_path.iterdir() if entry.suffix == ".tmp"]


@pytest.mark.parametrize("case", ["base", "overlay", "status"])
async def test_a_colliding_feed_path_is_refused_at_startup(
    tmp_path: Path, hooks: SimpleNamespace, case: str
) -> None:
    """AC19: a feed path resolving to the base, overlay or status file is
    refused before the configuration is read, naming which."""

    base = _setup(tmp_path)
    overlay = tmp_path / "config.local.yaml"
    status = tmp_path / "config.yaml.status.json"
    target = {"base": base, "overlay": overlay, "status": status}[case]
    # A different spelling of the same file: identity, not path text.
    spelled = tmp_path / "modules" / ".." / target.name
    before = base.read_bytes()
    diagnostics: list[str] = []

    result = await application.run(
        base,
        asyncio.Event(),
        environ={
            TOKEN_VARIABLE: CANARY,
            STATUS_FILE_VARIABLE: str(status),
            FEED_FILE_VARIABLE: str(spelled),
        },
        ready_reporter=lambda _message: pytest.fail("ready"),
        diagnostic_reporter=diagnostics.append,
    )

    assert result == 2
    assert len(diagnostics) == 1
    assert diagnostics[0].startswith("feed_path_collision: feed file collides with the ")
    assert f"collides with the {case} file {target}" in diagnostics[0]
    assert CANARY not in diagnostics[0]
    assert hooks.contexts == {}
    assert base.read_bytes() == before
    assert not overlay.exists() and not status.exists()


async def test_the_feed_check_runs_before_the_configuration_is_read(
    tmp_path: Path, hooks: SimpleNamespace
) -> None:
    """AC19: the refusal precedes the load, so an unreadable configuration
    still reports the collision and nothing else."""

    base = tmp_path / "config.yaml"
    base.write_text(": not yaml : [", encoding="utf-8")
    diagnostics: list[str] = []

    result = await application.run(
        base,
        asyncio.Event(),
        environ={FEED_FILE_VARIABLE: str(base)},
        diagnostic_reporter=diagnostics.append,
    )

    assert result == 2
    assert diagnostics == [f"feed_path_collision: feed file collides with the base file {base}"]
