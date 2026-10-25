"""Phase 10's container checks (R7's test policy).

The file holds two kinds of node. *Ordinary checks* run in every
``python -m pytest tests/`` under the AC33 isolation guard, opening no
socket and starting no process: they read the files that describe the image
and drive the UI's settings, records and rules against fakes. *Trial checks*
(``test_trial_container_*``, added by later steps) need a Docker daemon: each
is gated on ``TWITCH_IA_COMPAGNON_CONTAINER_TRIAL`` and skips with that reason
when it is unset, and once it is set a missing daemon is a failure, never a
skip.

The first ordinary checks are the stale-record rule (R2, AC10): a recorded
pid whose live process started after the record was published is not the
process that published it, so the instance record is removed as stale and
Start is not refused by the status record; a start time before publication,
or one that cannot be read, leaves phase 9's judgement unchanged. The
comparison is strict, with no tolerance (plan finding P1).
"""

from __future__ import annotations

import ast
import inspect
import json
import os
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import core.config_ui as config_ui
from core.config_ui import console, locations
from core.config_ui.locations import PRODUCT

#: A pid no fixture expects to be a real process; liveness is faked for it.
FOREIGN_PID = 424242
#: The status record's publication time, and that time in epoch seconds.
PUBLISHED_AT = "2026-10-24T08:00:01.250Z"
PUBLISHED = datetime(2026, 10, 24, 8, 0, 1, 250000, tzinfo=timezone.utc).timestamp()

#: Start times relative to publication and whether the pid is then live
#: (finding P1: strictly after is not live, whatever the margin).
BOUNDARIES = [
    pytest.param(0.5, False, id="after_0.5s"),
    pytest.param(0.001, False, id="after_1ms"),
    pytest.param(1.5, False, id="after_1.5s"),
    pytest.param(0.0, True, id="equal"),
    pytest.param(-0.001, True, id="before_1ms"),
]


# -- the helper ------------------------------------------------------------- #


def _live(pid: int) -> bool:
    return True


def _dead(pid: int) -> bool:
    return False


def test_ac10_record_pid_live_is_strict_and_passes_unknowns_through() -> None:
    def at(seconds: float | None) -> Callable[[int], float | None]:
        return lambda pid: seconds

    judge = config_ui.record_pid_live
    assert judge(7, 100.0, _live, at(100.001)) is False
    assert judge(7, 100.0, _live, at(100.0)) is True
    assert judge(7, 100.0, _live, at(99.999)) is True
    assert judge(7, 100.0, _live, at(None)) is True
    assert judge(7, 100.0, _dead, at(200.0)) is False
    assert judge(7, 100.0, _dead, at(None)) is False

    def never(pid: int) -> float | None:
        raise AssertionError("a dead pid's start time is never read")

    assert judge(7, 100.0, _dead, never) is False


def test_ac10_the_comparison_is_raw_with_no_tolerance_constant() -> None:
    """Finding P1: the helper compares the raw values with ``>``, with no
    tolerance or grace constant applied to the comparison."""

    tree = ast.parse(inspect.getsource(config_ui.record_pid_live))
    ordering = (ast.Gt, ast.GtE, ast.Lt, ast.LtE)
    comparisons = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Compare) and any(isinstance(op, ordering) for op in node.ops)
    ]
    assert len(comparisons) == 1
    (comparison,) = comparisons
    assert [type(op) for op in comparison.ops] == [ast.Gt]
    assert isinstance(comparison.left, ast.Name) and comparison.left.id == "started"
    (right,) = comparison.comparators
    assert isinstance(right, ast.Name) and right.id == "published_at"
    # No arithmetic and no number in the body (annotations aside).
    (function,) = tree.body
    assert isinstance(function, ast.FunctionDef)
    body = [node for statement in function.body for node in ast.walk(statement)]
    assert not [node for node in body if isinstance(node, ast.BinOp)]
    assert not [
        node for node in body
        if isinstance(node, ast.Constant)
        and isinstance(node.value, (int, float))
        and not isinstance(node.value, bool)
    ]
    # Nothing from outside reaches the comparison: the body reads only the
    # parameters and its own two locals, so no module constant is applied.
    loaded = {node.id for node in body if isinstance(node, ast.Name)}
    assert loaded == {"pid", "published_at", "liveness", "started_at", "live", "started"}


# -- the /proc reader -------------------------------------------------------- #


def _stat_line(comm: str, starttime: int) -> str:
    # Fields 3..21 then starttime (22) and a few more.
    middle = " ".join(["S"] + ["0"] * 18)
    return f"4242 ({comm}) {middle} {starttime} 1000 2000\n"


def _reader(files: dict[str, str]) -> Callable[[str], str]:
    def read(path: str) -> str:
        if path not in files:
            raise FileNotFoundError(path)
        return files[path]

    return read


@pytest.mark.parametrize("comm", ["python", "a b) (c", ") )", "x)"])
def test_ac10_the_start_time_reader_keeps_sub_second_precision(comm: str) -> None:
    files = {"/proc/4242/stat": _stat_line(comm, 123400), "/proc/uptime": "1234.56 99.0\n"}
    started = config_ui.process_started_at(
        4242, read_text=_reader(files), clock=lambda: 10000.25, ticks_per_second=100, platform="linux"
    )
    assert started == pytest.approx(10000.25 - 1234.56 + 1234.00, abs=1e-9)
    assert started is not None and round(started % 1, 2) == 0.69


def test_ac10_the_wall_clock_is_read_before_the_uptime() -> None:
    """A delay between the two samples moves the computed start earlier,
    never later: the wall clock is sampled before ``/proc/uptime`` is read."""

    files = {"/proc/4242/stat": _stat_line("p", 123400), "/proc/uptime": "1234.56 99.0\n"}
    order: list[str] = []
    read = _reader(files)

    def traced(path: str) -> str:
        order.append(path)
        return read(path)

    def clock() -> float:
        order.append("clock")
        return 10000.25

    config_ui.process_started_at(4242, read_text=traced, clock=clock, ticks_per_second=100, platform="linux")
    assert order.index("clock") < order.index("/proc/uptime")


@pytest.mark.parametrize(
    "files",
    [
        pytest.param({"/proc/uptime": "1.0 1.0"}, id="no_stat"),
        pytest.param({"/proc/4242/stat": _stat_line("p", 5)}, id="no_uptime"),
        pytest.param({"/proc/4242/stat": "4242 no parenthesis", "/proc/uptime": "1.0 1.0"}, id="no_paren"),
        pytest.param({"/proc/4242/stat": "4242 (p) S 1 2", "/proc/uptime": "1.0 1.0"}, id="short"),
        pytest.param({"/proc/4242/stat": _stat_line("p", 5), "/proc/uptime": ""}, id="empty_uptime"),
        pytest.param({"/proc/4242/stat": _stat_line("p", 5), "/proc/uptime": "x 1"}, id="bad_uptime"),
        pytest.param(
            {"/proc/4242/stat": _stat_line("p", 5).replace(" 5 1000", " five 1000"), "/proc/uptime": "1 1"},
            id="bad_starttime",
        ),
    ],
)
def test_ac10_an_unreadable_start_time_is_none(files: dict[str, str]) -> None:
    assert config_ui.process_started_at(
        4242, read_text=_reader(files), clock=lambda: 100.0, ticks_per_second=100, platform="linux"
    ) is None


def test_ac10_the_start_time_is_none_off_linux() -> None:
    def read(path: str) -> str:
        raise AssertionError("nothing is read off Linux")

    for platform in ("darwin", "win32"):
        assert config_ui.process_started_at(1, read_text=read, clock=lambda: 1.0, platform=platform) is None


# -- the instance record (claim path) ---------------------------------------- #


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class _Serve:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    def __call__(self, ui: Any, settings: Any) -> None:
        self.calls.append((ui, settings))


@pytest.fixture
def linux_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    """The A2 default location and the record's POSIX form, whatever the host."""

    monkeypatch.setattr(locations, "running_platform", lambda: locations.LINUX)


def _instance_record(monkeypatch: pytest.MonkeyPatch, root: Path) -> Path:
    """A foreign pid's complete record under the A2 default profile, its
    ``st_mtime`` set to :data:`PUBLISHED`; returns its path."""

    monkeypatch.setenv("XDG_CONFIG_HOME", str(root))
    monkeypatch.setenv("HOME", str(root))
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    profile = root / PRODUCT / "profile.yaml"
    profile.parent.mkdir(mode=0o700)
    record = profile.with_name(profile.name + config_ui.INSTANCE_SUFFIX)
    document = {
        "pid": FOREIGN_PID,
        "port": 8765,
        "marker": "f" * 32,
        "access_url": "http://127.0.0.1:8765/?token=" + "a" * 43,
    }
    record.write_text(json.dumps(document, sort_keys=True) + "\n", encoding="utf-8")
    os.utime(record, (PUBLISHED, PUBLISHED))
    return record


def _launch(started_at: Callable[[int], float | None]) -> tuple[int, _Serve, list[Any]]:
    """``main([])`` with the foreign pid live and its recorded port silent:
    judged live, the instance is unconfirmed and this launch is refused."""

    probed: list[Any] = []
    clock = _Clock()

    def probe(port: int, url: str, timeout: float) -> tuple[int, bytes]:
        probed.append(port)
        clock.now += timeout
        raise TimeoutError("silent")

    seams = config_ui.InstanceSeams(
        liveness=lambda pid: pid == FOREIGN_PID,
        probe=probe,
        clock=clock,
        sleep=clock.sleep,
        platform="linux",
        started_at=started_at,
    )
    serve = _Serve()
    status = config_ui.main([], opener=lambda url: None, serve=serve, instance=seams)
    return status, serve, probed


def _started(seconds: float | None) -> Callable[[int], float | None]:
    def started_at(pid: int) -> float | None:
        assert pid == FOREIGN_PID
        return seconds

    return started_at


def _assert_stale_and_served(record: Path, status: int, serve: _Serve, probed: list[Any]) -> None:
    assert status == 0
    assert len(serve.calls) == 1
    assert probed == []
    # Replaced by this launch's own, removed again on its clean exit.
    assert not record.exists()


def _assert_judged_as_phase_9(record: Path, status: int, serve: _Serve, probed: list[Any]) -> None:
    # A live, unconfirmed instance: probed, refused, the record left alone.
    assert status == config_ui.EXIT_INSTANCE
    assert serve.calls == []
    assert probed
    assert json.loads(record.read_text(encoding="utf-8"))["pid"] == FOREIGN_PID


@pytest.mark.usefixtures("linux_platform")
def test_ac10_an_instance_record_of_a_later_process_is_stale_and_this_launch_serves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    record = _instance_record(monkeypatch, tmp_path)
    _assert_stale_and_served(record, *_launch(_started(PUBLISHED + 60.0)))


@pytest.mark.usefixtures("linux_platform")
@pytest.mark.parametrize("started", [PUBLISHED - 60.0, None], ids=["earlier", "unreadable"])
def test_ac10_an_instance_record_of_an_earlier_or_unknown_start_is_judged_as_today(
    started: float | None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    record = _instance_record(monkeypatch, tmp_path)
    _assert_judged_as_phase_9(record, *_launch(_started(started)))


@pytest.mark.usefixtures("linux_platform")
@pytest.mark.parametrize(("offset", "live"), BOUNDARIES)
def test_ac10_the_instance_record_boundaries(
    offset: float,
    live: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    record = _instance_record(monkeypatch, tmp_path)
    published = record.stat().st_mtime
    outcome = _launch(_started(published + offset))
    if live:
        _assert_judged_as_phase_9(record, *outcome)
    else:
        _assert_stale_and_served(record, *outcome)


# -- the status record (Start's refusal) ------------------------------------- #


def _status_ui(
    root: Path, started_at: Callable[[int], float | None] | None
) -> tuple[config_ui.ConfigUI, list[str]]:
    """A UI over a profile whose status record names the live foreign pid;
    its supervisor records each start and launches nothing."""

    (root / "mods").mkdir()
    base = root / "config.yaml"
    base.write_text("modules_directory: ./mods\nenabled_modules: []\n", encoding="utf-8")
    starts: list[str] = []

    def start() -> Any:
        starts.append("start")
        raise OSError("no launch in this test")

    supervisor = SimpleNamespace(child=None, start=start, close=lambda: None)
    ui = config_ui.ConfigUI(
        config_ui.UISettings.from_argv(["--config", str(base)]),
        environ={},
        probe=lambda pid, child: pid == FOREIGN_PID,
        supervisor=supervisor,  # type: ignore[arg-type]
        started_at=started_at,
    )
    ui.status_path.write_text(
        json.dumps(
            {
                "version": 1,
                "pid": FOREIGN_PID,
                "started_at": "2026-10-24T08:00:00Z",
                "published_at": PUBLISHED_AT,
                "sequence": 1,
                "digest": "0" * 64,
                "state": "ready",
                "modules": {},
            }
        ),
        encoding="utf-8",
    )
    return ui, starts


def _start(root: Path, started_at: Callable[[int], float | None] | None) -> tuple[str, list[str]]:
    ui, starts = _status_ui(root, started_at)
    try:
        return ui.start_runtime().outcome, starts
    finally:
        config_ui._LOG_REDACTION.sources.discard(ui)


def test_ac10_start_is_not_refused_by_a_status_record_of_a_later_process(tmp_path: Path) -> None:
    outcome, starts = _start(tmp_path, _started(PUBLISHED + 60.0))
    assert outcome == console.START_LAUNCH_FAILED
    assert starts == ["start"]


@pytest.mark.parametrize(
    "started_at",
    [_started(PUBLISHED - 60.0), _started(None), None],
    ids=["earlier", "unreadable", "default_under_a_fake_probe"],
)
def test_ac10_start_with_an_earlier_or_unknown_start_is_refused_as_today(
    started_at: Callable[[int], float | None] | None, tmp_path: Path
) -> None:
    outcome, starts = _start(tmp_path, started_at)
    assert outcome == console.START_ANOTHER_INSTANCE
    assert starts == []


@pytest.mark.parametrize(("offset", "live"), BOUNDARIES)
def test_ac10_the_status_record_boundaries(offset: float, live: bool, tmp_path: Path) -> None:
    outcome, starts = _start(tmp_path, _started(PUBLISHED + offset))
    if live:
        assert (outcome, starts) == (console.START_ANOTHER_INSTANCE, [])
    else:
        assert (outcome, starts) == (console.START_LAUNCH_FAILED, ["start"])


@pytest.mark.parametrize(
    ("started_at", "alive"),
    [(_started(PUBLISHED + 60.0), False), (_started(PUBLISHED - 60.0), True), (_started(None), True)],
    ids=["later", "earlier", "unreadable"],
)
def test_ac10_the_console_and_stop_judge_the_status_record_as_start_does(
    started_at: Callable[[int], float | None], alive: bool, tmp_path: Path
) -> None:
    """The stale rule is not Start's alone: a record of a later process
    leaves the console's runtime state "not running", so the console offers
    Start, and Stop finds nothing; an earlier or unreadable start shows the
    unsupervised instance as in phase 9."""

    ui, starts = _status_ui(tmp_path, started_at)
    try:
        page = ui._console_response(config_ui._Session(csrf_token="t")).body.decode("utf-8")
        state = console.STATE_UNSUPERVISED if alive else console.STATE_NOT_RUNNING
        assert f'id="runtime-state" data-state="{state}"' in page
        assert ('action="/start"' in page) is not alive
        assert ui.stop_runtime().outcome == (console.STOP_NOT_OURS if alive else console.STOP_NOTHING)
        assert starts == []
    finally:
        config_ui._LOG_REDACTION.sources.discard(ui)


def test_ac10_the_status_publication_time_falls_back_to_the_file_time(tmp_path: Path) -> None:
    ui, _starts = _status_ui(tmp_path, None)
    try:
        record = config_ui.read_status(ui.status_path).record
        assert record is not None
        assert ui._published_seconds(record) == pytest.approx(PUBLISHED)
        os.utime(ui.status_path, (PUBLISHED + 5.0, PUBLISHED + 5.0))
        unparsable = config_ui.StatusRecord(
            pid=record.pid, started_at=record.started_at, published_at="not a timestamp",
            sequence=record.sequence, digest=record.digest, modules=record.modules,
        )
        assert ui._published_seconds(unparsable) == PUBLISHED + 5.0
    finally:
        config_ui._LOG_REDACTION.sources.discard(ui)
