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


# --------------------------------------------------------------------------- #
# P13: the console page, the feed poll and plain-language reasons
# (R7, R2; AC2's console half, AC20, AC21)
# --------------------------------------------------------------------------- #

import ast  # noqa: E402
import importlib  # noqa: E402
import re  # noqa: E402
from html import escape as html_escape  # noqa: E402

import core.config_ui as config_ui  # noqa: E402
from core.config_ui import console  # noqa: E402
from core.config_ui.i18n import StringTable  # noqa: E402
from core.config_ui.locations import PRODUCT  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
TABLE = StringTable.load()
HOST = "127.0.0.1:8765"
STATUS_PID = 4242


def _ui_request(
    method: str,
    path: str,
    *,
    query: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
    body: bytes = b"",
    host: str = HOST,
) -> config_ui.UIRequest:
    return config_ui.UIRequest(
        method=method, path=path, query=query or {}, headers={"host": host, **(headers or {})}, body=body
    )


def _profile(root: Path, modules: tuple[str, ...] = ("audio_output", "twitch")) -> Path:
    (root / "mods").mkdir(parents=True, exist_ok=True)
    base = root / "config.yaml"
    base.write_text(
        "modules_directory: ./mods\n"
        f"enabled_modules: [{', '.join(modules)}]\n"
        "actions:\n"
        "  - rule_id: brain-reads-chat\n    action_name: chat.read\n    principals: [brain]\n",
        encoding="utf-8",
    )
    return base


def _ui(base: Path, **options: Any) -> config_ui.ConfigUI:
    return config_ui.ConfigUI(
        config_ui.UISettings.from_argv(["--config", str(base)]), environ={}, **options
    )


def _login(ui: config_ui.ConfigUI) -> tuple[str, str]:
    landing = ui.handle(_ui_request("GET", "/", query={"token": ui.token}))
    assert landing.status == 303
    cookie = dict(landing.headers)["Set-Cookie"].split(";")[0]
    session = ui._session(_ui_request("GET", "/", headers={"cookie": cookie}))
    assert session is not None
    return cookie, session.csrf_token


def _get(ui: config_ui.ConfigUI, path: str, cookie: str, **query: str) -> config_ui.UIResponse:
    return ui.handle(_ui_request("GET", path, query=query, headers={"cookie": cookie}))


def _choose(ui: config_ui.ConfigUI, cookie: str, csrf: str, language: str) -> None:
    moved = ui.handle(
        _ui_request(
            "POST",
            "/language",
            headers={"cookie": cookie, "content-type": "application/x-www-form-urlencoded"},
            body=f"csrf_token={csrf}&language={language}".encode("utf-8"),
        )
    )
    assert moved.status == 303


def _write_feed(path: Path, entries: list[dict[str, Any]], tail: str = "") -> None:
    path.write_text("".join(json.dumps(entry) + "\n" for entry in entries) + tail, encoding="utf-8")


def _write_status(path: Path, modules: dict[str, str]) -> None:
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "pid": STATUS_PID,
                "started_at": "2026-10-24T08:00:00Z",
                "published_at": "2026-10-24T08:00:01Z",
                "sequence": 1,
                "digest": "0" * 64,
                "state": "ready",
                "modules": {name: {"state": state} for name, state in modules.items()},
            }
        ),
        encoding="utf-8",
    )


#: AC21's fixture feed: arrived, run started, run completed, a
#: ``self_authored`` rejection and ``audio_output`` degraded with a cause.
DEGRADED_CAUSE = "no audio device named 'desk' is connected"
AC21_FEED: list[dict[str, Any]] = [
    {"seq": 11, "at": "2026-10-24T08:00:02.000Z", "fact": CHAT_EVENT, "kind": "message",
     "platform": "fakeplatform", "author": "Viewer One", "excerpt": "hello companion"},
    {"seq": 12, "at": "2026-10-24T08:00:03.000Z", "fact": TRACE_BRAIN_RUN_STARTED, "run_id": "run-1"},
    {"seq": 13, "at": "2026-10-24T08:00:04.000Z", "fact": TRACE_BRAIN_RUN_COMPLETED, "run_id": "run-1",
     "status": "success", "reason": "completed"},
    {"seq": 14, "at": "2026-10-24T08:00:05.000Z", "fact": TRACE_INPUT_TRIGGER_REJECTED, "input": "fakeplatform",
     "reason": "self_authored"},
    {"seq": 15, "at": "2026-10-24T08:00:06.000Z", "fact": TRACE_MODULE_DEGRADED, "module": "audio_output",
     "reason": DEGRADED_CAUSE},
]


def _items(body: str) -> list[tuple[int, str, str]]:
    """``(seq, fact, inner markup)`` of every feed item, in page order."""

    return [
        (int(seq), fact, inner)
        for seq, fact, inner in re.findall(
            r'<li class="feed-item" data-seq="(\d+)" data-fact="([^"]*)">(.*?)</li>', body
        )
    ]


# -- read_feed: bounded and tolerant ---------------------------------------- #


def test_read_feed_tolerates_a_missing_or_partly_written_file(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml.feed.jsonl"
    assert console.read_feed(path) == []
    _write_feed(path, AC21_FEED[:2], tail='{"seq": 13, "fact": "brain.run.compl')
    assert [entry["seq"] for entry in console.read_feed(path)] == [11, 12]
    # Lines that are not whole entries are skipped, never raised on.
    path.write_text(
        '[1, 2]\n{"seq": "7", "fact": "x"}\n{"seq": 0, "fact": "x"}\n{"seq": 3}\n'
        '\xff\n{"seq": 5, "fact": "module.ready", "module": "m"}\n',
        encoding="utf-8",
    )
    assert [entry["seq"] for entry in console.read_feed(path)] == [5]


def test_read_feed_returns_only_entries_past_the_cursor_in_sequence_order(tmp_path: Path) -> None:
    path = tmp_path / "feed.jsonl"
    _write_feed(path, list(reversed(AC21_FEED)))
    assert [entry["seq"] for entry in console.read_feed(path)] == [11, 12, 13, 14, 15]
    assert [entry["seq"] for entry in console.read_feed(path, after=13)] == [14, 15]
    assert console.read_feed(path, after=15) == []
    assert console.feed_cursor([], 15) == 15
    assert console.feed_cursor(console.read_feed(path)) == 15


def test_read_feed_reads_a_bounded_tail_of_an_oversized_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The read is bounded: a file past the bound is read from its end, the
    cut first line skipped."""

    path = tmp_path / "feed.jsonl"
    entries = [{"seq": index, "fact": "module.ready", "module": "m" * 40} for index in range(1, 101)]
    _write_feed(path, entries)
    line = len(json.dumps(entries[0])) + 1
    monkeypatch.setattr(console, "FEED_READ_BYTES", line * 10 + line // 2)
    read = console.read_feed(path)
    assert [entry["seq"] for entry in read] == list(range(91, 101))
    assert console.FEED_READ_BYTES < path.stat().st_size


# -- AC2 (console half) ------------------------------------------------------ #


@pytest.mark.parametrize("finished", [False, True])
def test_ac2_following_the_console_redirect_renders_the_console(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, finished: bool
) -> None:
    """AC2 (console half, R1/R7): a profile at the default location, no
    ``--config``, the wizard state absent or finished: following the access
    URL's redirect with the session cookie renders the console page, 200,
    with the runtime state and the link to the editor."""

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    profile = tmp_path / PRODUCT / "profile.yaml"
    profile.parent.mkdir()
    profile.write_text("enabled_modules: []\n", encoding="utf-8")
    if finished:
        state = config_ui.WizardState(profile)
        state.profile, state.completed = "silent", True
        state.save()
    ui = config_ui.ConfigUI(config_ui.UISettings.from_argv([]), environ={})
    landing = ui.handle(_ui_request("GET", "/", query={"token": ui.token}))
    headers = dict(landing.headers)
    assert landing.status == 303 and headers["Location"] == "/console"
    cookie = headers["Set-Cookie"].split(";")[0]

    page = _get(ui, headers["Location"], cookie)

    assert page.status == 200 and page.content_type.startswith("text/html")
    body = page.body.decode("utf-8")
    assert f'data-state="{console.STATE_NOT_RUNNING}"' in body
    assert html_escape(TABLE.sentence("en", "console.state.not_running")) in body
    assert re.search(r'<a id="editor-link" href="/">[^<]+</a>', body)
    # The guards are the existing chain: no session, no console.
    assert ui.handle(_ui_request("GET", "/console")).status == 401


# -- the runtime state, from the status record and the supervisor's child ---- #


def test_the_console_names_the_three_runtime_states(tmp_path: Path) -> None:
    base = _profile(tmp_path)
    status = tmp_path / "config.yaml.status.json"
    ui = _ui(base, probe=lambda pid, child: True)
    cookie, _csrf = _login(ui)

    def state() -> str:
        found = re.search(r'id="runtime-state" data-state="([a-z_]+)"', _get(ui, "/console", cookie).body.decode())
        assert found
        return found.group(1)

    assert state() == console.STATE_NOT_RUNNING
    _write_status(status, {"audio_output": "ready"})
    assert state() == console.STATE_UNSUPERVISED
    ui.child = SimpleNamespace(pid=STATUS_PID, poll=lambda: None)
    assert state() == console.STATE_RUNNING_HERE
    ui.probe = lambda pid, child: False
    assert state() == console.STATE_NOT_RUNNING


# -- AC20: every reason token the runtime emits has a sentence ---------------- #

#: The core sources whose exported ``REASON_*`` constants AC20 names.
REASON_SOURCES = ("core/triggers.py", "core/admission.py", "core/actions.py")
#: Stands for a name an f-string builds a token from (a trigger type's name,
#: a role, an event kind): the family sentence must cover any value.
SAMPLE = "zzsample"
_TOKEN = re.compile(r"[a-z][a-z0-9_]*")
#: A code-carrying constant of a shipped module or the core: the codes a
#: refused action's observation carries, however a helper passes them on.
_CODE_CONSTANT = re.compile(r"_?(?:[A-Z]+_)?(?:ERROR|REFUSAL)_[A-Z0-9_]+")


def _string_constants(tree: ast.Module) -> dict[str, str]:
    """Module-level ``NAME = "text"`` constants, ``from core... import``
    names whose value is a string, and aliases of either."""

    found: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] == "core":
            imported = importlib.import_module(node.module)
            for alias in node.names:
                value = getattr(imported, alias.name, None)
                if isinstance(value, str):
                    found[alias.asname or alias.name] = value
    for _ in range(2):
        for node in tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                value = node.value
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    found[node.targets[0].id] = value.value
                elif isinstance(value, ast.Name) and value.id in found:
                    found[node.targets[0].id] = found[value.id]
    return found


def _texts(node: ast.AST, constants: dict[str, str]) -> list[str] | None:
    """The token(s) *node* can evaluate to; ``None`` when it is not static.

    An f-string's constant parts are kept and each value resolved to its
    constant or :data:`SAMPLE`; a conditional yields both branches.
    """

    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.Name):
        return [constants[node.id]] if node.id in constants else None
    if isinstance(node, ast.IfExp):
        body, orelse = _texts(node.body, constants), _texts(node.orelse, constants)
        return None if body is None or orelse is None else body + orelse
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant):
                parts.append(str(value.value))
            else:
                resolved = _texts(value.value, constants)
                parts.append(resolved[0] if resolved and len(resolved) == 1 else SAMPLE)
        return ["".join(parts)]
    return None


def collect_reason_tokens(paths: list[Path]) -> tuple[dict[str, str], list[str]]:
    """Every reason or code token *paths* can emit, and every dynamic site.

    Collected from the source (AST), in-process: the ``REASON_*``/code
    constants a source defines or imports, the string literals (or
    constants) passed as ``reason=`` or ``code=`` or under a
    ``"reason"``/``"code"`` key, the trigger engine's
    ``_Outcome`` and ``_InvariantRejection`` tokens, its hook outcome
    suffixes and the decision's ``no_rule_matched``. A ``reason=`` site whose
    value is neither a literal nor a constant is reported as dynamic unless
    it is a name or an attribute (a value handed on, collected at its
    origin), so a new literal token cannot hide. Returns ``{token: where}``.
    """

    tokens: dict[str, str] = {}
    dynamic: list[str] = []

    def add(found: list[str] | None, where: str) -> None:
        for token in found or ():
            base = token.split(":", 1)[0]
            if _TOKEN.fullmatch(base) and base.count(SAMPLE) < 2:
                tokens.setdefault(token, where)

    for path in paths:
        relative = path.relative_to(ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
        constants = _string_constants(tree)
        # Defined or imported: a code a source imports is one it can emit.
        for name, value in constants.items():
            if _CODE_CONSTANT.fullmatch(name) or (
                relative.startswith("core/") and name.startswith("REASON_")
            ):
                add([value], f"{relative}:{name}")
        hook_suffixes: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                callee = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", None)
                for keyword in node.keywords:
                    if keyword.arg in ("reason", "code"):
                        found = _texts(keyword.value, constants)
                        if found is not None:
                            add(found, f"{relative}:{node.lineno}")
                        elif not isinstance(keyword.value, (ast.Name, ast.Attribute, ast.Subscript, ast.Call)):
                            dynamic.append(f"{relative}:{node.lineno}")
                if callee == "_Outcome" and len(node.args) >= 2:
                    add(_texts(node.args[1], constants), f"{relative}:{node.lineno}")
                if callee == "_InvariantRejection" and node.args:
                    add(_texts(node.args[0], constants), f"{relative}:{node.lineno}")
                if callee == "_hook_outcome" and len(node.args) >= 2:
                    hook_suffixes += _texts(node.args[1], constants) or []
            if isinstance(node, ast.Dict):
                for key, value in zip(node.keys, node.values):
                    if isinstance(key, ast.Constant) and key.value in ("reason", "code"):
                        add(_texts(value, constants), f"{relative}:{node.lineno}")
        add([f"{SAMPLE}_{suffix}" for suffix in hook_suffixes], f"{relative}:hook outcomes")
        if "_NO_RULE_MATCHED" in constants:
            add([constants["_NO_RULE_MATCHED"]], f"{relative}:_NO_RULE_MATCHED")
    return tokens, dynamic


def _shipped_sources() -> list[Path]:
    return [
        *(ROOT / name for name in REASON_SOURCES),
        ROOT / "core" / "runtime.py",
        *sorted((ROOT / "modules").glob("*/__init__.py")),
    ]


def _exported_reasons() -> dict[str, str]:
    exported: dict[str, str] = {}
    for name in REASON_SOURCES:
        module = importlib.import_module(name[:-3].replace("/", "."))
        for attribute in getattr(module, "__all__", ()):
            value = getattr(module, attribute)
            if attribute.startswith("REASON_") and isinstance(value, str):
                exported[value] = f"{name}:{attribute}"
    return exported


def test_ac20_every_emitted_reason_token_has_a_sentence_in_both_languages() -> None:
    """AC20 (R6, R7): every exported ``REASON_*`` of the trigger, admission
    and action cores, and every token the shipped sources emit on
    ``input.trigger.rejected``, ``brain.admission.rejected``,
    ``brain.run.completed`` and refused actions, has its own sentence (not
    the generic one) in English and French — a decision token with its
    ``rejected:`` prefix and an invariant's ``:<field>`` detail included."""

    tokens, dynamic = collect_reason_tokens(_shipped_sources())
    exported = _exported_reasons()
    assert {"self_authored", "no_policy", "session_queue_full", "run_failed", "no_applicable_rule"} <= set(exported)
    assert set(exported) <= set(tokens)
    # The engine's own outcome tokens are found, families included.
    assert {"keyword_no_match", "no_rule_matched", "probability_hit", f"{SAMPLE}_hook_timeout",
            "invalid_payload:payload.text", "not_a_read_action", "provider_not_ready"} <= set(tokens)
    assert len(tokens) > 100
    assert dynamic == []
    missing = []
    for token, where in sorted(tokens.items()):
        for language in ("en", "fr"):
            for spelled in (token, f"rejected:{token}"):
                generic = TABLE.unknown_reason(language, spelled)
                sentence = console.reason_sentence(spelled, language, TABLE)
                if not sentence or sentence == generic:
                    missing.append((spelled, language, where))
    assert missing == []


def test_ac20_an_unknown_token_renders_the_generic_sentence_naming_it() -> None:
    for language in ("en", "fr"):
        sentence = console.reason_sentence("zz_unknown", language, TABLE)
        assert sentence.strip() and "zz_unknown" in sentence
        assert sentence == TABLE.unknown_reason(language, "zz_unknown")
    page = console.render_feed_items(
        [{"seq": 1, "fact": TRACE_INPUT_TRIGGER_REJECTED, "input": "i", "reason": "zz_unknown"}],
        "fr",
        TABLE,
    )
    assert len(page) == 1 and "zz_unknown" in page[0]


def test_ac20_the_collector_fails_on_a_new_uncovered_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A module emitting a new literal ``reason=`` token, a new code
    constant, or a ``reason=`` built at run time is caught."""

    module = tmp_path / "modules" / "newmod" / "__init__.py"
    module.parent.mkdir(parents=True)
    module.write_text(
        'ERROR_BRAND_NEW = "brand_new_code"\n'
        "async def handle(supervision, decision, token):\n"
        '    await supervision.emit("input.trigger.rejected", {"reason": "zz_fresh_reason"})\n'
        '    refuse(reason="zz_literal_reason")\n'
        '    refuse(reason="prefix_" + token)\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(sys.modules[__name__], "ROOT", tmp_path)
    tokens, dynamic = collect_reason_tokens([module])
    assert {"brand_new_code", "zz_fresh_reason", "zz_literal_reason"} <= set(tokens)
    assert dynamic == ["modules/newmod/__init__.py:5"]
    for token in ("brand_new_code", "zz_fresh_reason", "zz_literal_reason"):
        assert console.reason_sentence(token, "en", TABLE) == TABLE.unknown_reason("en", token)


# -- AC21: the fixture feed on the console and the poll ---------------------- #


@pytest.fixture
def ac21(tmp_path: Path) -> SimpleNamespace:
    base = _profile(tmp_path)
    ui = _ui(base, probe=lambda pid, child: True)
    _write_feed(ui.settings.feed_path, AC21_FEED)
    cookie, csrf = _login(ui)
    return SimpleNamespace(ui=ui, base=base, cookie=cookie, csrf=csrf)


@pytest.mark.parametrize("language", ["en", "fr"])
def test_ac21_the_console_renders_the_feed_newest_first(ac21: SimpleNamespace, language: str) -> None:
    """AC21 (R7): exactly 5 items, one per entry, the degraded entry first
    and the arrived entry last; the ``self_authored`` rejection as its
    sentence in the chosen language; the degraded module with its cause,
    quoted, and a next action."""

    if language != "en":
        _choose(ac21.ui, ac21.cookie, ac21.csrf, language)
    page = _get(ac21.ui, "/console", ac21.cookie)
    assert page.status == 200
    body = page.body.decode("utf-8")
    assert f'<html lang="{language}">' in body
    items = _items(body)
    assert [seq for seq, _fact, _inner in items] == [15, 14, 13, 12, 11]
    assert [fact for _seq, fact, _inner in items] == [
        TRACE_MODULE_DEGRADED, TRACE_INPUT_TRIGGER_REJECTED, TRACE_BRAIN_RUN_COMPLETED,
        TRACE_BRAIN_RUN_STARTED, CHAT_EVENT,
    ]
    degraded, rejection = items[0][2], items[1][2]
    assert html_escape(TABLE.sentence(language, "reason.self_authored")) in rejection
    if language == "fr":
        assert "Le compagnon a écrit ce message lui-même" in rejection
        assert TABLE.sentence("en", "reason.self_authored") not in body
    assert f'<q class="cause-text">{html_escape(DEGRADED_CAUSE)}</q>' in degraded
    assert html_escape(TABLE.sentence(language, "console.module.degraded.next")) in degraded
    assert html_escape(TABLE.sentence(language, "console.module.degraded", module="audio_output")) in degraded
    assert '<q class="excerpt">hello companion</q>' in items[4][2]
    # The cause is data, never a sentence of the product: it is quoted, not
    # translated, and it is not a reason token looked up in the table.
    assert DEGRADED_CAUSE not in TABLE.sentences[language].values()


def test_ac21_the_module_list_shows_the_degraded_module_with_cause_and_next_action(
    ac21: SimpleNamespace,
) -> None:
    _write_status(ac21.ui.status_path, {"audio_output": "degraded", "twitch": "ready"})
    body = _get(ac21.ui, "/console", ac21.cookie).body.decode("utf-8")
    modules = dict(
        re.findall(r'<li class="module" data-module="([^"]+)" data-module-state="[a-z]+">(.*?)</li>', body)
    )
    assert set(modules) == {"audio_output", "twitch"}
    assert 'data-module="audio_output" data-module-state="degraded"' in body
    assert f'<q class="cause-text">{html_escape(DEGRADED_CAUSE)}</q>' in modules["audio_output"]
    assert html_escape(TABLE.sentence("en", "console.module.degraded.next")) in modules["audio_output"]
    assert modules["twitch"] == html_escape(TABLE.sentence("en", "console.module.ready", module="twitch"))
    # The configured grant is listed, read-only.
    assert "<code>brain-reads-chat</code>" in body and "<form" not in body


def test_ac21_the_poll_with_the_newest_cursor_returns_no_entry(ac21: SimpleNamespace) -> None:
    newest = _get(ac21.ui, "/feed", ac21.cookie, after="15")
    assert newest.status == 200 and newest.content_type == "application/json"
    assert json.loads(newest.body) == {"cursor": 15, "at": AC21_FEED[-1]["at"], "reset": False, "items": []}
    everything = json.loads(_get(ac21.ui, "/feed", ac21.cookie).body)
    assert everything["cursor"] == 15
    assert [int(re.search(r'data-seq="(\d+)"', item).group(1)) for item in everything["items"]] == [15, 14, 13, 12, 11]
    later = json.loads(_get(ac21.ui, "/feed", ac21.cookie, after="13").body)
    assert later["cursor"] == 15 and len(later["items"]) == 2
    # A new entry appears on the next poll past the old cursor.
    _write_feed(ac21.ui.settings.feed_path, [*AC21_FEED, {"seq": 16, "fact": "runtime.stopping"}])
    following = json.loads(_get(ac21.ui, "/feed", ac21.cookie, after="15").body)
    assert following["cursor"] == 16 and len(following["items"]) == 1
    assert _get(ac21.ui, "/feed", ac21.cookie, after="-1").status == 400
    assert _get(ac21.ui, "/feed", ac21.cookie, after="x").status == 400
    assert _get(ac21.ui, "/feed", ac21.cookie, after="15", at="t" * 65).status == 400


def test_a_restarted_runtime_s_feed_resets_the_cursor(tmp_path: Path) -> None:
    """A new runtime numbers its feed from 1 again: a cursor from the previous
    one must not hide the new entries, whether the new feed is still below
    the old cursor or has already passed it."""

    path = tmp_path / "feed.jsonl"
    old = [{"seq": seq, "at": f"old-{seq}", "fact": "runtime.started"} for seq in range(1, 6)]
    _write_feed(path, old)
    same = console.poll_feed(path, 5, "old-5")
    assert (same.entries, same.cursor, same.at, same.reset) == ([], 5, "old-5", False)
    grown = console.poll_feed(path, 3, "old-3")
    assert [entry["seq"] for entry in grown.entries] == [4, 5] and grown.at == "old-5"
    assert not grown.reset
    # Restarted, still below the old cursor: every retained entry, reset.
    _write_feed(path, [{"seq": 1, "at": "new-1", "fact": "runtime.started"}])
    below = console.poll_feed(path, 5, "old-5")
    assert below.reset and [entry["seq"] for entry in below.entries] == [1]
    assert (below.cursor, below.at) == (1, "new-1")
    assert console.poll_feed(path, 5).reset
    # Restarted and already past the old cursor: the cursor entry's time differs.
    new = [{"seq": seq, "at": f"new-{seq}", "fact": "runtime.started"} for seq in range(1, 8)]
    _write_feed(path, new)
    past = console.poll_feed(path, 5, "old-5")
    assert past.reset and [entry["seq"] for entry in past.entries] == list(range(1, 8))
    assert (past.cursor, past.at) == (7, "new-7")
    # Restarted with nothing written yet: the list empties, the cursor is 0.
    path.unlink()
    gone = console.poll_feed(path, 7, "new-7")
    assert (gone.entries, gone.cursor, gone.at, gone.reset) == ([], 0, None, True)


def test_the_poll_script_waits_for_each_answer_and_bounds_the_list() -> None:
    script = console._POLL_SCRIPT
    assert "setInterval" not in script and ".then(next)" in script
    assert f"list.children.length > {console.FEED_MAX_ENTRIES}" in script
    assert "document_.reset" in script and f"&{console.FEED_CURSOR_AT}=" in script


def test_the_console_routes_are_behind_the_existing_guards(
    ac21: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``/console`` and ``/feed`` answer only an accepted Host with a
    session and GET; a refused request reads no feed."""

    reads: list[Any] = []
    real = console.read_feed
    monkeypatch.setattr(console, "read_feed", lambda *args, **kwargs: reads.append(args) or real(*args, **kwargs))
    ui, cookie = ac21.ui, ac21.cookie
    for path in ("/console", "/feed"):
        assert ui.handle(_ui_request("GET", path, headers={"cookie": cookie}, host="evil.example")).status == 403
        assert ui.handle(_ui_request("GET", path)).status == 401
        assert ui.handle(_ui_request("GET", path, headers={"cookie": "companion_session=forged"})).status == 401
        posted = ui.handle(
            _ui_request(
                "POST",
                path,
                headers={"cookie": cookie, "content-type": "application/x-www-form-urlencoded"},
                body=f"csrf_token={ac21.csrf}".encode("utf-8"),
            )
        )
        assert posted.status == 405
    assert reads == []
    assert _get(ui, "/console", cookie).status == 200 and len(reads) == 1


def test_the_console_redacts_the_secret_set_in_every_feed_value(tmp_path: Path) -> None:
    """R4: a configured secret reaching the feed is hidden on the page and in
    the poll, in the excerpt, the cause and the reason alike."""

    base = _profile(tmp_path)
    base.write_text(base.read_text(encoding="utf-8") + "secrets: ['${CONSOLE_SECRET}']\n", encoding="utf-8")
    ui = config_ui.ConfigUI(
        config_ui.UISettings.from_argv(["--config", str(base)]), environ={"CONSOLE_SECRET": SECRET}
    )
    _write_feed(
        ui.settings.feed_path,
        [
            {"seq": 1, "fact": CHAT_EVENT, "author": SECRET, "excerpt": f"say {SECRET}"},
            {"seq": 2, "fact": TRACE_MODULE_DEGRADED, "module": "audio_output", "reason": f"key {SECRET} refused"},
            {"seq": 3, "fact": TRACE_ACTION_COMPLETED, "action": "chat.send", "error_code": SECRET},
        ],
    )
    cookie, _csrf = _login(ui)
    poll = _get(ui, "/feed", cookie)
    page = _get(ui, "/console", cookie)
    for response in (poll, page):
        assert response.status == 200
        assert SECRET.encode("utf-8") not in response.body
        assert config_ui.REDACTED.encode("utf-8") in response.body
