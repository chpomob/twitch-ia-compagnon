"""The runtime's status record publisher (R7, A5; AC33, AC34, AC41, AC43),
and the UI reader's classification of the records it publishes (P14).

Fixture modules M, N and P are written to ``tmp_path``. Each one's activation
hands its per-module context to a hook registry the test installs in
``sys.modules``, so the test drives the module's own health owner
(``context.supervision.degraded/ready``) on the same event loop as ``run``:
every transition is published synchronously through the bus before the
awaited call returns, so no test waits on a positive-duration sleep.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

import core.main as application
from core.config_ui import ConfigUI, StatusReading, UISettings, read_status
from core.overlay import STATUS_FILE_VARIABLE, on_disk_digest


HOOKS_MODULE = "_status_record_fixture_hooks"

FIXTURE_SOURCE = f"""
from pathlib import Path

import {HOOKS_MODULE} as hooks


class Handle:
    async def prepare(self):
        return None

    async def close(self):
        return None


async def activate(context, settings, catalog):
    Path(settings["activation_log"]).open("a", encoding="utf-8").write(
        context.module + "\\n"
    )
    hooks.contexts[context.module] = context
    return Handle()
"""

M, N, P = "mmod", "nmod", "pmod"
TOKEN_VARIABLE = "STATUS_RECORD_TEST_TOKEN"
CANARY = "CANARY-STATUS-5d1e"
TIMESTAMP = re.compile(r"\A\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z\Z")


@pytest.fixture
def hooks(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    registry = SimpleNamespace(contexts={})
    monkeypatch.setitem(sys.modules, HOOKS_MODULE, registry)
    return registry


def _make_module(root: Path, name: str) -> None:
    directory = root / name
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


def _setup(
    tmp_path: Path,
    enabled: list[str],
    *,
    names: tuple[str, ...] = (M, N, P),
) -> Path:
    """Write the fixture modules and a base enabling *enabled*; return the base."""

    for name in names:
        _make_module(tmp_path / "modules", name)
    activation_log = tmp_path / "activations.log"
    config = {
        "modules_directory": "./modules",
        "enabled_modules": enabled,
        "modules": {
            name: {
                "activation_log": str(activation_log),
                # An unresolved reference: the record's digest covers the
                # reference, never the value it resolves to.
                "token": "${" + TOKEN_VARIABLE + "}",
            }
            for name in names
        },
    }
    base = tmp_path / "config.yaml"
    base.write_text(yaml.safe_dump(config), encoding="utf-8")
    return base


def _environ(status: Path | str | None) -> dict[str, str]:
    environ = {TOKEN_VARIABLE: CANARY}
    if status is not None:
        environ[STATUS_FILE_VARIABLE] = os.fspath(status)
    return environ


class _Running:
    """``run`` as a task on the test's loop, stopped on exit."""

    def __init__(self, base: Path, environ: dict[str, str], **options: Any) -> None:
        self.base = base
        self.environ = environ
        self.options = options
        self.diagnostics: list[str] = []
        self.ready = asyncio.Event()
        self.stop = asyncio.Event()
        self.status: int | None = None

    async def __aenter__(self) -> "_Running":
        self.task = asyncio.create_task(
            application.run(
                self.base,
                self.stop,
                environ=self.environ,
                ready_reporter=lambda _message: self.ready.set(),
                diagnostic_reporter=self.diagnostics.append,
                **self.options,
            )
        )
        ready = asyncio.create_task(self.ready.wait())
        await asyncio.wait({self.task, ready}, return_when=asyncio.FIRST_COMPLETED)
        ready.cancel()
        assert self.ready.is_set(), self.diagnostics
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        self.stop.set()
        self.status = await self.task


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _leftovers(directory: Path) -> list[str]:
    return sorted(entry.name for entry in directory.iterdir() if entry.suffix == ".tmp")


# --------------------------------------------------------------------------- #
# AC33 — publisher side
# --------------------------------------------------------------------------- #


async def test_three_transitions_publish_three_records_in_order(
    tmp_path: Path, hooks: SimpleNamespace
) -> None:
    """AC33: ready, M degraded, M ready again — three records, in order."""

    base = _setup(tmp_path, [M, N], names=(M, N))
    status = tmp_path / "config.yaml.status.json"
    overlay = tmp_path / "config.local.yaml"
    records: list[dict[str, Any]] = []
    readings: list[StatusReading] = []

    async with _Running(base, _environ(status)) as running:
        records.append(_read(status))
        readings.append(read_status(status))
        await hooks.contexts[M].supervision.degraded(reason="fixture degraded")
        records.append(_read(status))
        readings.append(read_status(status))
        await hooks.contexts[M].supervision.ready()
        records.append(_read(status))
        readings.append(read_status(status))

    assert running.status == 0
    assert running.diagnostics == []
    assert [record["modules"] for record in records] == [
        {M: {"state": "ready"}, N: {"state": "ready"}},
        {M: {"state": "degraded"}, N: {"state": "ready"}},
        {M: {"state": "ready"}, N: {"state": "ready"}},
    ]
    sequences = [record["sequence"] for record in records]
    assert sequences[0] == 1
    assert sequences[0] < sequences[1] < sequences[2]
    expected_digest = on_disk_digest(base, overlay)
    for record in records:
        assert record["version"] == 1
        assert record["pid"] == os.getpid()
        assert record["started_at"] == records[0]["started_at"]
        assert TIMESTAMP.match(record["started_at"])
        assert TIMESTAMP.match(record["published_at"])
        assert record["digest"] == expected_digest
        assert record["state"] == "ready"
        assert set(record["modules"]) == {M, N}
    # Reader side (P14): each of the 3 records is classified usable, and
    # the reader's view of it is the record as published.
    for record, reading in zip(records, readings):
        assert reading.kind == "usable", reading.reason
        assert reading.record is not None
        assert reading.record.pid == record["pid"]
        assert reading.record.sequence == record["sequence"]
        assert reading.record.digest == record["digest"]
        assert reading.record.published_at == record["published_at"]
        assert dict(reading.record.modules) == {
            name: entry["state"] for name, entry in record["modules"].items()
        }
    # The record never carries a resolved value.
    assert CANARY not in status.read_text(encoding="utf-8")
    # Shutdown publishes no ``stopped`` module state.
    assert _read(status) == records[-1]
    assert _leftovers(tmp_path) == []


async def test_a_repeated_state_publishes_no_new_record(
    tmp_path: Path, hooks: SimpleNamespace
) -> None:
    """Only a changed ready/degraded state of an enabled module publishes."""

    base = _setup(tmp_path, [M, N], names=(M, N))
    status = tmp_path / "status.json"

    async with _Running(base, _environ(status)):
        await hooks.contexts[N].supervision.ready()
        assert _read(status)["sequence"] == 1
        await hooks.contexts[N].supervision.degraded(reason="one")
        assert _read(status)["sequence"] == 2
        # A new reason is a new health fact, but the state is unchanged.
        await hooks.contexts[N].supervision.degraded(reason="two")
        assert _read(status)["sequence"] == 2


async def test_without_the_variable_no_file_is_written(
    tmp_path: Path, hooks: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC33: ``run`` without the variable writes no file."""

    monkeypatch.delenv(STATUS_FILE_VARIABLE, raising=False)
    base = _setup(tmp_path, [M, N], names=(M, N))
    constructed: list[Any] = []
    monkeypatch.setattr(
        application,
        "_StatusPublisher",
        lambda *args, **kwargs: constructed.append(args) or pytest.fail("constructed"),
    )
    before = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*"))

    async with _Running(base, _environ(None)) as running:
        await hooks.contexts[M].supervision.degraded(reason="fixture degraded")

    after = sorted(
        path.relative_to(tmp_path)
        for path in tmp_path.rglob("*")
        if "__pycache__" not in path.parts
    )
    assert running.status == 0
    assert constructed == []
    assert set(after) == set(before) | {Path("activations.log")}


# --------------------------------------------------------------------------- #
# AC34 — atomic replacement
# --------------------------------------------------------------------------- #


async def test_an_interrupted_replacement_leaves_the_previous_record(
    tmp_path: Path, hooks: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC34: the second publish fails before replacing; the first survives."""

    base = _setup(tmp_path, [M, N], names=(M, N))
    status = tmp_path / "status.json"
    real_replace = os.replace
    calls: list[str] = []

    def failing_replace(source: Any, destination: Any) -> None:
        calls.append(os.fspath(destination))
        if len(calls) == 2:
            raise OSError("simulated interruption")
        real_replace(source, destination)

    monkeypatch.setattr(os, "replace", failing_replace)

    async with _Running(base, _environ(status)) as running:
        first = status.read_text(encoding="utf-8")
        await hooks.contexts[M].supervision.degraded(reason="fixture degraded")
        # The runtime keeps running and the next change still publishes.
        assert status.read_text(encoding="utf-8") == first
        assert _leftovers(tmp_path) == []
        await hooks.contexts[N].supervision.degraded(reason="fixture degraded")
        latest = _read(status)

    assert json.loads(first)["sequence"] == 1
    assert json.loads(first)["modules"][M] == {"state": "ready"}
    assert running.status == 0
    assert running.diagnostics == ["status record: could not be written"]
    # A failed write does not consume a sequence number.
    assert latest["sequence"] == 2
    assert latest["modules"] == {M: {"state": "degraded"}, N: {"state": "degraded"}}
    assert _leftovers(tmp_path) == []


async def test_a_reader_never_parses_a_partial_record(
    tmp_path: Path, hooks: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC34: parsing the status path before every replacement always succeeds."""

    base = _setup(tmp_path, [M, N], names=(M, N))
    status = tmp_path / "status.json"
    real_writer = application._write_status_record
    real_replace = os.replace
    observed: list[dict[str, Any] | None] = []

    def reading_replace(source: Any, destination: Any) -> None:
        # The new content is complete in the temporary file, the destination
        # still holds the previous record (or nothing, the first time).
        assert json.loads(Path(source).read_text(encoding="utf-8"))["version"] == 1
        observed.append(_read(status) if status.exists() else None)
        real_replace(source, destination)

    def wrapped_writer(path: Path, text: str) -> None:
        monkeypatch.setattr(os, "replace", reading_replace)
        try:
            real_writer(path, text)
        finally:
            monkeypatch.setattr(os, "replace", real_replace)
        observed.append(_read(status))

    monkeypatch.setattr(application, "_write_status_record", wrapped_writer)

    async with _Running(base, _environ(status)):
        await hooks.contexts[M].supervision.degraded(reason="fixture degraded")
        await hooks.contexts[M].supervision.ready()

    assert observed[0] is None
    parsed = [record for record in observed[1:]]
    assert all(isinstance(record, dict) for record in parsed)
    assert [record["sequence"] for record in parsed] == [1, 1, 2, 2, 3]


# --------------------------------------------------------------------------- #
# AC41 — runtime half of the collision refusal
# --------------------------------------------------------------------------- #


def _snapshot(*paths: Path) -> list[tuple[bytes, int] | None]:
    return [
        (path.read_bytes(), path.stat().st_mtime_ns) if path.exists() else None
        for path in paths
    ]


@pytest.mark.parametrize("case", ["base", "implicit-overlay", "explicit-relative"])
async def test_a_status_path_naming_a_configuration_file_is_refused(
    tmp_path: Path,
    hooks: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    """AC41: base, absent implicit overlay, relative explicit overlay."""

    base = _setup(tmp_path, [M, N], names=(M, N))
    implicit = tmp_path / "config.local.yaml"
    explicit = tmp_path / "chosen.local.yaml"
    (tmp_path / "sub").mkdir()
    options: dict[str, Any] = {}
    if case == "base":
        status: str = os.fspath(base)
    elif case == "implicit-overlay":
        assert not implicit.exists()
        status = os.fspath(implicit)
    else:
        explicit.write_text("enabled_modules: [mmod]\n", encoding="utf-8")
        options["overlay"] = explicit
        monkeypatch.chdir(tmp_path)
        status = "./sub/../chosen.local.yaml"
    before = _snapshot(base, implicit, explicit)
    diagnostics: list[str] = []

    result = await application.run(
        base,
        asyncio.Event(),
        environ=_environ(status),
        ready_reporter=lambda message: pytest.fail(message),
        diagnostic_reporter=diagnostics.append,
        **options,
    )

    assert result != 0
    assert len(diagnostics) == 1
    assert "status_path_collision" in diagnostics[0]
    expected = "base" if case == "base" else "overlay"
    assert f"collides with the {expected} file" in diagnostics[0]
    assert CANARY not in diagnostics[0]
    assert hooks.contexts == {}
    assert not (tmp_path / "activations.log").exists()
    assert _snapshot(base, implicit, explicit) == before
    assert not implicit.exists()


def test_the_collision_is_refused_through_main(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """AC41: ``--overlay`` given on the command line is the path compared."""

    base = _setup(tmp_path, [], names=())
    overlay = tmp_path / "chosen.local.yaml"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(STATUS_FILE_VARIABLE, "chosen.local.yaml")

    result = application.main(["--config", os.fspath(base), "--overlay", os.fspath(overlay)])

    assert result != 0
    assert "status_path_collision" in capsys.readouterr().err
    assert not overlay.exists()


def test_a_hard_linked_status_file_is_refused_through_main(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Gate-2 F1: a status path that is a hard link of the base file is the
    base file; the runtime's check compares file identity, not path text."""

    base = _setup(tmp_path, [], names=())
    status = tmp_path / "status.json"
    os.link(base, status)
    before = base.read_bytes()
    monkeypatch.setenv(STATUS_FILE_VARIABLE, os.fspath(status))

    result = application.main(["--config", os.fspath(base)])

    assert result != 0
    err = capsys.readouterr().err
    assert "status_path_collision" in err and "collides with the base file" in err
    assert base.read_bytes() == before and os.path.samefile(base, status)


async def test_a_tilde_overlay_is_compared_as_the_file_that_is_read(
    tmp_path: Path, hooks: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC41: an explicit ``~/…`` overlay is expanded like every other path, so
    the status path naming the ``$HOME/…`` file it reads is refused.

    Inverted by gate-1 F1: the overlay used to be read literally (``./~/…``),
    a spelling the configuration UI's collision check did not share; every
    path now goes through the one ``core.overlay.canonical_path`` form.
    """

    base = _setup(tmp_path, [M], names=(M,))
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", os.fspath(home))
    monkeypatch.chdir(tmp_path)
    expanded = home / "chosen.local.yaml"
    expanded.write_text("enabled_modules: [mmod]\n", encoding="utf-8")
    literal = tmp_path / "~" / "chosen.local.yaml"
    literal.parent.mkdir()
    literal.write_text("enabled_modules: [mmod]\n", encoding="utf-8")
    before = _snapshot(expanded, literal)
    diagnostics: list[str] = []

    result = await application.run(
        base,
        asyncio.Event(),
        environ=_environ(expanded),
        ready_reporter=lambda message: pytest.fail(message),
        diagnostic_reporter=diagnostics.append,
        overlay="~/chosen.local.yaml",
    )

    assert result != 0
    assert len(diagnostics) == 1
    assert "collides with the overlay file" in diagnostics[0]
    assert os.fspath(expanded) in diagnostics[0]
    assert hooks.contexts == {}
    assert _snapshot(expanded, literal) == before


# --------------------------------------------------------------------------- #
# AC43 — the record describes the loaded configuration
# --------------------------------------------------------------------------- #


async def test_the_record_describes_the_loaded_configuration(
    tmp_path: Path, hooks: SimpleNamespace
) -> None:
    """AC43: overlay-selected modules and digest, unchanged by a later edit."""

    base = _setup(tmp_path, [M, N, P])
    overlay = tmp_path / "config.local.yaml"
    overlay.write_text(yaml.safe_dump({"enabled_modules": [M, N]}), encoding="utf-8")
    loaded_digest = on_disk_digest(base, overlay)
    status = tmp_path / "status.json"

    async with _Running(base, _environ(status)) as running:
        first = _read(status)
        overlay.write_text(
            yaml.safe_dump(
                {"enabled_modules": [M], "modules": {M: {"token": "changed"}}}
            ),
            encoding="utf-8",
        )
        assert on_disk_digest(base, overlay) != loaded_digest
        await hooks.contexts[N].supervision.degraded(reason="fixture degraded")
        second = _read(status)
        # The UI reading it, while this live process wrote it, sees drift.
        ui = ConfigUI(
            UISettings.from_argv(["--config", str(base), "--status-file", str(status)])
        )
        running_state = ui.running_state()

    assert running.status == 0
    assert set(hooks.contexts) == {M, N}
    assert set(first["modules"]) == {M, N}
    assert first["digest"] == loaded_digest
    assert second["modules"] == {M: {"state": "ready"}, N: {"state": "degraded"}}
    assert second["started_at"] == first["started_at"]
    assert second["digest"] == first["digest"]
    assert second["sequence"] > first["sequence"]
    assert running_state.reading.kind == "usable"
    assert running_state.drift == "differs"


async def test_a_run_enabling_no_module_publishes_an_empty_container(
    tmp_path: Path, hooks: SimpleNamespace
) -> None:
    """AC43: a loaded configuration enabling no module gives ``modules: {}``."""

    base = _setup(tmp_path, [])
    status = tmp_path / "status.json"

    async with _Running(base, _environ(status)) as running:
        record = _read(status)

    assert running.status == 0
    assert record["modules"] == {}
    assert record["sequence"] == 1
    assert record["digest"] == on_disk_digest(base, tmp_path / "config.local.yaml")
