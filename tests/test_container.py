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

Then the container-mode settings (R1, AC1–AC3): the one value-free warning
for a bind that is not loopback, ``--no-browser`` (the opener is never
called, the access URL is still printed once) and ``--token-file`` (the
token read from a file under A5's rules, an unusable file refusing to start
with a diagnostic that names the path and never the content).

Then the supervision record (R6 item 3, AC19): published ``not_started``
by ``main``'s claim path, rewritten ``launched`` each time the supervisor
returns a child and ``stopped`` by the console's Stop, removed at release
when it carries this UI's marker; a UI built without the claim writes none,
and a file at its path refuses to start (``supervision_path_collision``).
"""

from __future__ import annotations

import ast
import inspect
import json
import os
import re
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from test_config_ui import _request

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


# -- container-mode settings (R1) -------------------------------------------- #

#: The 43-character token of AC3's valid file.
VALID_TOKEN = "Tk0_valid-container-token-AAAAAAAAAAAAAAAAA"
assert len(VALID_TOKEN) == 43


class _ServeUI:
    """A fake ``serve``: records the UI and what standard error held when it
    was called, and runs ``on_listening`` as the real one does."""

    def __init__(self, capsys: pytest.CaptureFixture[str]) -> None:
        self.capsys = capsys
        self.calls: list[Any] = []
        self.stderr_before: list[str] = []
        self.stdout_before: list[str] = []
        self.record: dict[str, Any] | None = None

    def __call__(self, ui: Any, settings: Any) -> None:
        captured = self.capsys.readouterr()
        self.stdout_before.append(captured.out)
        self.stderr_before.append(captured.err)
        self.calls.append(ui)
        self.record = json.loads(settings.instance_path.read_text(encoding="utf-8"))
        if ui.on_listening is not None:
            ui.on_listening()


def _profile(root: Path) -> Path:
    (root / "mods").mkdir()
    base = root / "config.yaml"
    base.write_text("modules_directory: ./mods\nenabled_modules: []\n", encoding="utf-8")
    return base


def _run(
    root: Path, capsys: pytest.CaptureFixture[str], *extra: str
) -> tuple[int, _ServeUI, list[str], str, str]:
    """``main`` over a fresh profile with *extra*; returns the status, the
    fake serve, the opener's calls and the rest of stdout and stderr."""

    base = _profile(root)
    opened: list[str] = []
    serve = _ServeUI(capsys)
    seams = config_ui.InstanceSeams(platform="linux")
    status = config_ui.main(
        ["--config", str(base), *extra], opener=opened.append, serve=serve, instance=seams
    )
    after = capsys.readouterr()
    return status, serve, opened, after.out, after.err


def _warnings(text: str) -> list[str]:
    return [line for line in text.splitlines() if "reachable from the network" in line]


def test_ac1_a_wildcard_bind_writes_one_value_free_warning_before_serving(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    status, serve, _opened, _out, err = _run(
        tmp_path, capsys, "--host", "0.0.0.0", "--port", "18765", "--allow-non-loopback"
    )
    assert status == 0 and len(serve.calls) == 1
    (before,) = serve.stderr_before
    (line,) = _warnings(before)
    assert "0.0.0.0" in line and "18765" in line and "token" in line
    (ui,) = serve.calls
    assert ui.token not in line
    assert ui.token not in before
    assert ui.instance_marker not in line
    assert _warnings(err) == []


@pytest.mark.parametrize("host", ["::", "192.0.2.10"])
def test_ac1_every_non_loopback_bind_warns_once(
    host: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _status, serve, _opened, _out, err = _run(tmp_path, capsys, "--host", host, "--allow-non-loopback")
    (before,) = serve.stderr_before
    assert len(_warnings(before)) == 1 and host in _warnings(before)[0]
    assert _warnings(err) == []


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_ac1_a_loopback_bind_writes_no_warning(
    host: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    status, serve, _opened, _out, err = _run(tmp_path, capsys, "--host", host)
    assert status == 0 and len(serve.calls) == 1
    assert _warnings(serve.stderr_before[0]) == [] and _warnings(err) == []


def test_ac1_the_warning_is_built_from_host_and_port_only() -> None:
    line = config_ui.non_loopback_warning("0.0.0.0", 8765)
    assert line is not None and "\n" not in line
    assert config_ui.non_loopback_warning("127.0.0.1", 8765) is None
    assert inspect.signature(config_ui.non_loopback_warning).parameters.keys() == {"host", "port"}


@pytest.mark.parametrize(("extra", "calls"), [((), 1), (("--no-browser",), 0)], ids=["default", "no_browser"])
def test_ac2_no_browser_leaves_the_opener_uncalled_and_prints_the_url_once(
    extra: tuple[str, ...], calls: int, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    status, serve, opened, out, _err = _run(tmp_path, capsys, *extra)
    assert status == 0 and len(serve.calls) == 1
    (ui,) = serve.calls
    printed = serve.stdout_before[0] + out
    assert printed.count(ui.access_url) == 1
    assert opened == [ui.access_url] * calls


def test_ac2_no_browser_covers_the_running_instance_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A confirmed instance of this version is handed to the opener today;
    with ``--no-browser`` its access URL is printed once and nothing opens."""

    monkeypatch.setattr(locations, "running_platform", lambda: locations.LINUX)
    record = _instance_record(monkeypatch, tmp_path)
    url = json.loads(record.read_text(encoding="utf-8"))["access_url"]
    answer = json.dumps({"marker": "f" * 32, "version": locations.product_version()}).encode("utf-8")

    def launch(*extra: str) -> tuple[int, list[str], str]:
        clock = _Clock()
        seams = config_ui.InstanceSeams(
            liveness=lambda pid: pid == FOREIGN_PID,
            probe=lambda port, probed, timeout: (200, answer),
            clock=clock,
            sleep=clock.sleep,
            platform="linux",
            started_at=_started(PUBLISHED - 60.0),
        )
        opened: list[str] = []
        serve = _Serve()
        status = config_ui.main(list(extra), opener=opened.append, serve=serve, instance=seams)
        assert serve.calls == []
        return status, opened, capsys.readouterr().out

    status, opened, out = launch("--no-browser")
    assert (status, opened, out.count(url)) == (0, [], 1)
    status, opened, out = launch()
    assert (status, opened, out.count(url)) == (0, [url], 1)


def test_ac3_a_valid_token_file_supplies_the_token(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    token_file = tmp_path / "token"
    token_file.write_text(VALID_TOKEN + "\n", encoding="utf-8")
    status, serve, _opened, out, _err = _run(tmp_path, capsys, "--token-file", str(token_file))
    assert status == 0 and len(serve.calls) == 1
    (ui,) = serve.calls
    printed = serve.stdout_before[0] + out
    (line,) = [line for line in printed.splitlines() if "?token=" in line]
    assert line.endswith("?token=" + VALID_TOKEN)
    assert ui.token == VALID_TOKEN
    # The instance record persists it on POSIX, where the health probe reads it.
    assert serve.record is not None and serve.record["access_url"].endswith("?token=" + VALID_TOKEN)
    try:
        response = ui.handle(_request("GET", "/", query={"token": VALID_TOKEN}))
        assert response.status == 303
        cookie = dict(response.headers)["Set-Cookie"].split(";")[0]
        assert ui._session(_request("GET", "/", headers={"cookie": cookie})) is not None
        refused = ui.handle(_request("GET", "/", query={"token": VALID_TOKEN[:-1] + "B"}))
        assert "Set-Cookie" not in dict(refused.headers)
    finally:
        config_ui._LOG_REDACTION.sources.discard(ui)


@pytest.mark.parametrize(
    ("content", "rule"),
    [
        pytest.param(None, "unreadable", id="missing"),
        pytest.param("", "empty", id="empty"),
        pytest.param("Mx42" + "q" * 38, "length 43–128", id="42_chars"),
        pytest.param("Lx129" + "r" * 124, "length 43–128", id="129_chars"),
        pytest.param("Px+" + "s" * 41, "alphabet A–Z a–z 0–9 - _", id="plus"),
    ],
)
def test_ac3_an_unusable_token_file_refuses_to_start(
    content: str | None, rule: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    token_file = tmp_path / "token"
    if content is not None:
        assert content == "" or len(content) in (42, 129, 44)
        token_file.write_text(content + "\n", encoding="utf-8")
    status, serve, opened, _out, err = _run(tmp_path, capsys, "--token-file", str(token_file))
    assert status == 2
    assert serve.calls == [] and opened == []
    lines = err.strip().splitlines()
    assert len(lines) == 1
    assert str(token_file) in lines[0] and rule in lines[0]
    if content:
        assert content not in err
    # Refused before any claim: no instance record was published.
    assert not (tmp_path / ("config.yaml" + config_ui.INSTANCE_SUFFIX)).exists()


def test_ac3_whitespace_around_the_token_is_stripped(tmp_path: Path) -> None:
    token_file = tmp_path / "token"
    token_file.write_bytes(b"  \t" + VALID_TOKEN.encode("ascii") + b"\r\n\n")
    assert config_ui.read_token_file(token_file) == VALID_TOKEN
    token_file.write_bytes(b"\xff" * 50)
    with pytest.raises(config_ui.TokenFileError, match="unreadable"):
        config_ui.read_token_file(token_file)


def test_r1_the_new_options_are_absent_by_default() -> None:
    settings = config_ui.UISettings.from_argv(["--config", "x.yaml"])
    assert settings.no_browser is False and settings.token_file is None
    parsed = config_ui.UISettings.from_argv(["--config", "x.yaml", "--no-browser", "--token-file", "t"])
    assert parsed.no_browser is True and parsed.token_file == Path("t")


# -- the supervision record (R6 item 3, AC19) -------------------------------- #

#: The status record's timestamp format, which ``launched_at`` uses.
STATUS_TIMESTAMP = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z")
#: A fixture content no diagnostic may quote.
FIXTURE_CONTENT = "supervision-fixture-4b81e0-never-quoted"


class _FakeChild:
    """A child that runs until terminated; no process is started."""

    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.stderr = None
        self.returncode: int | None = None

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.returncode = -15

    kill = terminate

    def wait(self, timeout: float | None = None) -> int | None:
        return self.returncode


def _fake_launches(ui: Any) -> list[_FakeChild]:
    """Make *ui*'s own supervisor start fake children (pids 5001, 5002, …),
    watch none of them past one poll, and let every Check pass."""

    children: list[_FakeChild] = []

    def popen(*args: Any, **kwargs: Any) -> _FakeChild:
        children.append(_FakeChild(5000 + len(children) + 1))
        return children[-1]

    ui.supervisor.popen = popen
    ui.supervisor.apply_window = 0.0
    ui.checker = lambda *args, **kwargs: (True, ())
    return children


def _supervision(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _claimed(root: Path, during: Callable[[Any, Path], None]) -> tuple[int, Path]:
    """``main`` over a fresh profile, *during* run with the UI and its
    supervision record's path while it serves; returns the exit status and
    that path."""

    base = _profile(root)
    record = root / ("config.yaml" + config_ui.SUPERVISION_SUFFIX)
    calls: list[Any] = []

    def serve(ui: Any, settings: Any) -> None:
        calls.append(ui)
        try:
            during(ui, record)
        finally:
            config_ui._LOG_REDACTION.sources.discard(ui)

    seams = config_ui.InstanceSeams(platform="linux")
    status = config_ui.main(["--config", str(base)], opener=lambda url: None, serve=serve, instance=seams)
    assert len(calls) == 1
    return status, record


def test_ac19_the_supervision_path_is_derived_as_the_instance_records(tmp_path: Path) -> None:
    (tmp_path / "real").mkdir()
    (tmp_path / "link").symlink_to(tmp_path / "real")
    settings = config_ui.UISettings.from_argv(["--config", str(tmp_path / "link" / "a.yml")])
    expected = tmp_path / "real" / "a.yml.supervisor.json"
    assert config_ui.supervision_path(settings) == settings.supervision_path == expected
    assert settings.supervision_path.parent == settings.instance_path.parent


def test_ac19_the_record_follows_the_supervisor_and_release_removes_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: list[dict[str, Any]] = []

    def during(ui: Any, record: Path) -> None:
        children = _fake_launches(ui)
        seen.append(_supervision(record))
        if os.name != "nt":
            assert record.stat().st_mode & 0o777 == 0o600
        assert ui.start_runtime().outcome == console.START_UNCONFIRMED
        seen.append(_supervision(record))
        ui.apply()
        seen.append(_supervision(record))
        assert ui.stop_runtime().outcome == console.STOP_STOPPED
        seen.append(_supervision(record))
        assert [child.pid for child in children] == [5001, 5002]
        # Apply's own stop is no console Stop: the record stays launched.
        ui.start_runtime()
        ui.apply()
        seen.append(_supervision(record))
        assert ui.instance_marker == seen[0]["marker"]

    status, record = _claimed(tmp_path, during)
    assert status == 0
    marker = seen[0]["marker"]
    assert seen[0] == {"version": 1, "marker": marker, "state": "not_started"}
    for document, pid in [(seen[1], 5001), (seen[2], 5002), (seen[4], 5004)]:
        assert set(document) == {"version", "marker", "state", "pid", "launched_at"}
        assert (document["version"], document["marker"], document["state"]) == (1, marker, "launched")
        assert document["pid"] == pid
        assert STATUS_TIMESTAMP.fullmatch(document["launched_at"])
        assert config_ui._timestamp_seconds(document["launched_at"]) is not None
    assert seen[3] == {"version": 1, "marker": marker, "state": "stopped"}
    assert not record.exists()
    assert sorted(path.name for path in tmp_path.iterdir()) == ["config.yaml", "mods"]


def test_ac19_publication_replaces_a_record_present(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stale = tmp_path / ("config.yaml" + config_ui.SUPERVISION_SUFFIX)
    stale.write_text('{"version": 1, "marker": "' + "e" * 32 + '", "state": "stopped"}\n', encoding="utf-8")
    seen: list[dict[str, Any]] = []
    status, record = _claimed(tmp_path, lambda ui, record: seen.append(_supervision(record)))
    assert status == 0
    assert seen[0]["state"] == "not_started" and seen[0]["marker"] != "e" * 32
    assert not record.exists()


def test_ac19_release_removes_its_record_when_the_instance_record_was_replaced(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    foreign = json.dumps({"pid": FOREIGN_PID, "port": 8765, "marker": "f" * 32}) + "\n"

    def during(ui: Any, record: Path) -> None:
        ui.settings.instance_path.write_text(foreign, encoding="utf-8")

    status, record = _claimed(tmp_path, during)
    assert status == 0
    assert not record.exists()
    instance = tmp_path / ("config.yaml" + config_ui.INSTANCE_SUFFIX)
    assert instance.read_text(encoding="utf-8") == foreign
    assert sorted(path.name for path in tmp_path.iterdir()) == sorted(["config.yaml", "mods", instance.name])


def test_ac19_a_record_with_another_marker_is_byte_identical_after_release(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    other = b'{"marker": "' + b"d" * 32 + b'", "state": "launched", "version": 1}\n'

    def during(ui: Any, record: Path) -> None:
        record.write_bytes(other)

    status, record = _claimed(tmp_path, during)
    assert status == 0
    assert record.read_bytes() == other


def test_ac19_a_ui_built_without_the_claim_writes_none(tmp_path: Path) -> None:
    base = _profile(tmp_path)
    ui = config_ui.ConfigUI(config_ui.UISettings.from_argv(["--config", str(base)]), environ={})
    try:
        children = _fake_launches(ui)
        before = sorted(path.name for path in tmp_path.iterdir())
        assert ui.start_runtime().outcome == console.START_UNCONFIRMED
        ui.apply()
        assert ui.stop_runtime().outcome == console.STOP_STOPPED
        assert len(children) == 2
        assert sorted(path.name for path in tmp_path.iterdir()) == before
        assert not ui.settings.supervision_path.exists()
        ui.release_supervision()
    finally:
        ui.supervisor.close()
        config_ui._LOG_REDACTION.sources.discard(ui)


@pytest.mark.usefixtures("linux_platform")
@pytest.mark.parametrize("configured", ["status", "overlay", "feed", "token"])
def test_ac19_a_file_at_the_supervision_path_refuses_to_start(
    configured: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.delenv(config_ui.FEED_FILE_VARIABLE, raising=False)
    target = tmp_path / PRODUCT / "profile.yaml.supervisor.json"
    target.parent.mkdir()
    target.write_text(FIXTURE_CONTENT, encoding="utf-8")
    argv: list[str] = []
    if configured == "feed":
        monkeypatch.setenv(config_ui.FEED_FILE_VARIABLE, str(target))
    elif configured == "token":
        argv = ["--token-file", str(target)]
    else:
        argv = ["--status-file" if configured == "status" else "--overlay", str(target)]

    problems = config_ui.startup_checks(config_ui.UISettings.from_argv(argv))
    assert problems == [
        f"supervision_path_collision: supervision record file {target} collides with "
        f"the {configured} file {target}"
    ]
    assert FIXTURE_CONTENT not in problems[0]

    serve = _Serve()
    assert config_ui.main(argv, opener=lambda url: None, serve=serve) != 0
    captured = capsys.readouterr()
    assert "supervision_path_collision" in captured.err
    assert FIXTURE_CONTENT not in captured.out + captured.err
    assert serve.calls == []
    assert target.read_text(encoding="utf-8") == FIXTURE_CONTENT
    assert sorted(path.name for path in target.parent.iterdir()) == [target.name]


@pytest.mark.usefixtures("linux_platform")
def test_ac19_a_token_file_aliasing_the_supervision_path_refuses_to_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A symbolic link to the supervision record is that file: publication
    # would overwrite the token and release would delete it.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.delenv(config_ui.FEED_FILE_VARIABLE, raising=False)
    target = tmp_path / PRODUCT / "profile.yaml.supervisor.json"
    target.parent.mkdir()
    target.write_text("a" * 32 + "\n", encoding="utf-8")
    alias = tmp_path / "token"
    alias.symlink_to(target)

    problems = config_ui.startup_checks(
        config_ui.UISettings.from_argv(["--token-file", str(alias)])
    )
    assert problems == [
        f"supervision_path_collision: supervision record file {target} collides with "
        f"the token file {target}"
    ]


@pytest.mark.usefixtures("linux_platform")
def test_ac19_the_default_location_has_no_supervision_collision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.delenv(config_ui.FEED_FILE_VARIABLE, raising=False)
    assert config_ui.startup_checks(config_ui.UISettings.from_argv([])) == []
