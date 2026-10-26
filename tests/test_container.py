"""Phase 10's container checks (R7's test policy).

The file holds two kinds of node. *Ordinary checks* run in every
``python -m pytest tests/`` under the AC33 isolation guard, opening no
socket and starting no process: they read the files that describe the image
and drive the UI's settings, records and rules against fakes. *Trial checks*
(``test_trial_container_*``) need a Docker daemon: each
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

Then ``--start-runtime`` (R1 d, AC4): once listening, the console Start's
own entry runs exactly once when a completed profile is present, with that
Start's refusals; with no profile or an unfinished wizard nothing starts and
one value-free reason line is logged.

Then the health command (R6 rows 1–13, A6, AC19 rows, AC20): against fake
records, a fake probe, a fake liveness judgement and an injected clock,
``--health`` prints exactly one of the table's lines and exits with its
row's status, the earlier row deciding a case that matches two; the probe
carries the record's token and no cookie, and the default probe connects
only to ``127.0.0.1`` and, never answered, ends within 5 s of its clock.

Then the invariants (R1, R5, AC5, AC18): under each of the 16 combinations
of ``--token-file``, ``--no-browser``, ``--start-runtime`` and a non-loopback
bind, the UI ``main`` builds answers its route table, driven in-process, with
phase 9's three route classes, every class-(iii) refusal equal to the one a
default :class:`UISettings` gives; and a credential both inherited and stored
reaches the supervised child with its stored value.

Then the image's static contracts (R3, R5, AC11, AC17's ``.dockerignore``
part): the ``Dockerfile``'s two stages, digest-pinned base, wheel-only
runtime stage, numeric user, A2's default command and the health command;
and ``.dockerignore`` as an allowlist, judged by a pure-Python model of
Docker's matching rules over a synthetic path list (no daemon: the trial's
image inspection is the real check).

Then the compose file's static contract (R4, A1, A4, AC14): ``compose.yaml``
parsed with PyYAML has one service and one named volume mounted at
``/data``, the loopback-published port, ``init``, a restart policy, the
health command as its healthcheck with the Dockerfile's timings, A1's exact
build context, nothing Docker-Desktop-specific and no other file required;
its optional credential and token-file entries are comments, not keys.

Then the CI workflow's static contract (R7, AC23's CI part): ``ci.yml``
gains exactly the ``container-trial`` job, which runs R7's invocation with
the variable set to ``1`` and the build context ``.``; the ``tests`` job's
steps equal a literal copy of those at the brief's HEAD (``git show`` would
start a process); and no step pushes, logs in, tags or releases.

Then the self-hosting guide pair (R8, AC24, AC18's and AC25's guide and link
parts): ``docs/self-hosting.md`` names every file of the ``/data`` volume by
the name the code derives, every R1 option, A6's five states, uid ``10001``
and each limit; both guides state the credential precedence; the
quick start is at most three commands, none a clone or a build; the
documentation index links the pair and each installation guide links the
guide of its language.

Last, the container trial itself (R7, AC23's default parts): the five
``test_trial_container_*`` nodes, each starting with phase 2's
``required(CONTAINER_TRIAL_VARIABLE)``, build the image from
``TWITCH_IA_COMPAGNON_BUILD_CONTEXT`` and drive it with ``docker compose``
under a project name of their own, waiting only through Docker's bounded
waits and subprocess timeouts, and removing what they created. Whatever they
run inside the container is piped from strings of this file to its own
interpreter, never baked. The ordinary checks of this part read the file's
own shape and run each trial body with ``docker`` unfindable: a failure
naming it, never a skip.
"""

from __future__ import annotations

import ast
import base64
import contextlib
import hashlib
import inspect
import json
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from test_config_ui import _request
from test_phase2_trials import required

import core.config_ui as config_ui
from core.config_ui import console, locations, wizard
from core.config_ui.locations import PRODUCT
from core.overlay import default_feed_path, default_status_path, implicit_overlay_path

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
    files = {"/proc/4242/stat": _stat_line(comm, 123400)}
    started = config_ui.process_started_at(
        4242,
        read_text=_reader(files),
        clock=lambda: 10000.25,
        boot_clock=lambda: 1234.56,
        ticks_per_second=100,
        platform="linux",
    )
    assert started == pytest.approx(10000.25 - 1234.56 + 1234.00, abs=1e-9)
    assert started is not None and round(started % 1, 2) == 0.69


@pytest.mark.parametrize("fraction", [0.0, 0.001, 0.0035, 0.0099])
def test_ac10_the_uptime_is_not_truncated(fraction: float) -> None:
    """The uptime is ``CLOCK_BOOTTIME`` at its own resolution, neither cut to
    the centiseconds ``/proc/uptime`` prints (a runtime whose supervision
    record was written 1 ms after its start was judged stale: up to 8 ms late
    in the container trial, so ``--health`` said ``unhealthy: runtime
    exited``) nor rounded up to them (a pid reused 1 ms after a publication
    passed as its publisher)."""

    uptime = 1234.56 + fraction
    wall = 10000.25
    true_start = wall - uptime + 1234.00
    files = {"/proc/4242/stat": _stat_line("p", 123400)}
    started = config_ui.process_started_at(
        4242,
        read_text=_reader(files),
        clock=lambda: wall,
        boot_clock=lambda: uptime,
        ticks_per_second=100,
        platform="linux",
    )
    assert started == pytest.approx(true_start, abs=1e-9)
    assert config_ui.record_pid_live(4242, true_start + 0.001, _live, lambda pid: started) is True
    assert config_ui.record_pid_live(4242, true_start - 0.001, _live, lambda pid: started) is False


def test_ac10_the_default_uptime_is_the_boot_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """The default uptime is ``CLOCK_BOOTTIME``, the clock ``starttime``
    counts in, read through no text (no truncation)."""

    asked: list[int] = []

    def clock_gettime(clock_id: int) -> float:
        asked.append(clock_id)
        return 1234.5678

    monkeypatch.setattr(time, "CLOCK_BOOTTIME", 7, raising=False)
    monkeypatch.setattr(time, "clock_gettime", clock_gettime)
    files = {"/proc/4242/stat": _stat_line("p", 123400)}
    started = config_ui.process_started_at(
        4242, read_text=_reader(files), clock=lambda: 10000.25, ticks_per_second=100, platform="linux"
    )
    assert asked == [7]
    assert started == pytest.approx(10000.25 - 1234.5678 + 1234.00, abs=1e-9)


def test_ac10_the_wall_clock_is_read_before_the_uptime() -> None:
    """A delay between the two samples moves the computed start earlier,
    never later: the wall clock is sampled before the uptime."""

    files = {"/proc/4242/stat": _stat_line("p", 123400)}
    order: list[str] = []

    def clock() -> float:
        order.append("clock")
        return 10000.25

    def boot_clock() -> float:
        order.append("uptime")
        return 1234.56

    config_ui.process_started_at(
        4242, read_text=_reader(files), clock=clock, boot_clock=boot_clock, ticks_per_second=100, platform="linux"
    )
    assert order == ["clock", "uptime"]


def _no_boot_clock() -> float:
    raise OSError("no CLOCK_BOOTTIME")


@pytest.mark.parametrize(
    ("files", "boot_clock"),
    [
        pytest.param({}, lambda: 1.0, id="no_stat"),
        pytest.param({"/proc/4242/stat": _stat_line("p", 5)}, _no_boot_clock, id="no_uptime"),
        pytest.param({"/proc/4242/stat": "4242 no parenthesis"}, lambda: 1.0, id="no_paren"),
        pytest.param({"/proc/4242/stat": "4242 (p) S 1 2"}, lambda: 1.0, id="short"),
        pytest.param({"/proc/4242/stat": _stat_line("p", 5)}, lambda: math.nan, id="bad_uptime"),
        pytest.param(
            {"/proc/4242/stat": _stat_line("p", 5).replace(" 5 1000", " five 1000")}, lambda: 1.0, id="bad_starttime"
        ),
    ],
)
def test_ac10_an_unreadable_start_time_is_none(files: dict[str, str], boot_clock: Callable[[], float]) -> None:
    assert config_ui.process_started_at(
        4242,
        read_text=_reader(files),
        clock=lambda: 100.0,
        boot_clock=boot_clock,
        ticks_per_second=100,
        platform="linux",
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


# -- --start-runtime (R1 d, AC4) --------------------------------------------- #

#: What a start-runtime log line may never hold.
TOKEN_TEXT = "token="


def _wizard_state(base: Path, completed: bool) -> None:
    document = {"format": config_ui.wizard.STATE_FORMAT, "completed": completed}
    Path(os.fspath(base) + ".wizard.json").write_text(json.dumps(document), encoding="utf-8")


class _ListeningServe:
    """A fake ``serve``: fakes the supervisor's launches, counts its starts
    before and after the fake listening signal (``on_listening``, called
    twice: the hook runs once per UI start), and reads the supervision record."""

    def __init__(self, prepare: Callable[[Any], None] | None = None) -> None:
        self.prepare = prepare
        self.starts: list[int] = []
        # Set at the first start, for an opener that waits on it.
        self.started = threading.Event()
        self.before: int | None = None
        self.supervision: dict[str, Any] | None = None
        self.console_outcome: str | None = None
        self.ui: Any = None

    def __call__(self, ui: Any, settings: Any) -> None:
        self.ui = ui
        try:
            _fake_launches(ui)
            start = ui.supervisor.start

            def counted() -> Any:
                self.starts.append(1)
                self.started.set()
                return start()

            ui.supervisor.start = counted
            if self.prepare is not None:
                self.prepare(ui)
            self.before = len(self.starts)
            if ui.on_listening is not None:
                ui.on_listening()
                ui.on_listening()
            if settings.supervision_path.exists():
                self.supervision = _supervision(settings.supervision_path)
            if self.prepare is not None:
                # The console's own Start, for its refusal.
                self.console_outcome = ui.start_runtime().outcome
        finally:
            config_ui._LOG_REDACTION.sources.discard(ui)


def _start_lines(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [record.getMessage() for record in caplog.records if record.getMessage().startswith("start-runtime")]


def _assert_value_free(lines: list[str], ui: Any, root: Path) -> None:
    for line in lines:
        assert TOKEN_TEXT not in line and ui.token not in line and ui.instance_marker not in line
        assert os.fspath(root) not in line


def test_ac4_a_completed_profile_starts_once_after_listening(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("INFO", logger="core.config_ui")
    base = _profile(tmp_path)
    _wizard_state(base, completed=True)
    serve = _ListeningServe()
    opened: list[str] = []
    status = config_ui.main(
        ["--config", str(base), "--start-runtime"],
        opener=opened.append,
        serve=serve,
        instance=config_ui.InstanceSeams(platform="linux"),
    )
    assert status == 0
    assert serve.before == 0 and len(serve.starts) == 1
    # P2's opener is unchanged and still runs first, once per signal.
    assert opened == [serve.ui.access_url] * 2
    assert serve.supervision is not None
    assert serve.supervision["state"] == "launched" and serve.supervision["pid"] == 5001
    assert _start_lines(caplog) == []
    assert any("runtime start unconfirmed" in record.getMessage() for record in caplog.records)
    assert capsys.readouterr().out.count("Configuration UI: ") == 1


def test_ac4_a_blocking_opener_does_not_hold_the_runtime_start(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # A terminal browser blocks until it closes: the runtime starts meanwhile.
    base = _profile(tmp_path)
    _wizard_state(base, completed=True)
    serve = _ListeningServe()
    seen: list[bool] = []

    def blocking_opener(url: str) -> None:
        seen.append(serve.started.wait(5.0) and bool(serve.starts))

    status = config_ui.main(
        ["--config", str(base), "--start-runtime"],
        opener=blocking_opener,
        serve=serve,
        instance=config_ui.InstanceSeams(platform="linux"),
    )
    assert status == 0 and len(serve.starts) == 1
    # Each signal's opener saw the runtime start while it was still open.
    assert seen == [True, True]


def test_ac4_a_completed_profile_without_a_wizard_state_starts_too(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # A --config profile no wizard wrote lands on the editor: it starts.
    base = _profile(tmp_path)
    serve = _ListeningServe()
    status = config_ui.main(
        ["--config", str(base), "--start-runtime", "--no-browser"],
        opener=lambda url: None,
        serve=serve,
        instance=config_ui.InstanceSeams(platform="linux"),
    )
    assert status == 0 and len(serve.starts) == 1


def test_ac4_an_unfinished_wizard_starts_nothing_and_logs_a_value_free_reason(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("INFO", logger="core.config_ui")
    base = _profile(tmp_path)
    _wizard_state(base, completed=False)
    serve = _ListeningServe()
    status = config_ui.main(
        ["--config", str(base), "--start-runtime", "--no-browser"],
        opener=lambda url: None,
        serve=serve,
        instance=config_ui.InstanceSeams(platform="linux"),
    )
    assert status == 0 and serve.starts == []
    lines = _start_lines(caplog)
    assert lines == ["start-runtime: wizard unfinished"]
    _assert_value_free(lines, serve.ui, tmp_path)
    assert serve.supervision is not None and serve.supervision["state"] == "not_started"


@pytest.mark.usefixtures("linux_platform")
def test_ac4_no_profile_starts_nothing_and_logs_a_value_free_reason(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("INFO", logger="core.config_ui")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.delenv(config_ui.FEED_FILE_VARIABLE, raising=False)
    serve = _ListeningServe()
    status = config_ui.main(
        ["--start-runtime", "--no-browser"],
        opener=lambda url: None,
        serve=serve,
        instance=config_ui.InstanceSeams(platform="linux"),
    )
    assert status == 0 and serve.starts == []
    assert not serve.ui.settings.base_path.exists()
    lines = _start_lines(caplog)
    assert lines == ["start-runtime: no profile"]
    _assert_value_free(lines, serve.ui, tmp_path)


def test_ac4_a_live_status_record_refuses_as_the_console_start_does(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("INFO", logger="core.config_ui")
    base = _profile(tmp_path)
    _wizard_state(base, completed=True)

    def live_record(ui: Any) -> None:
        ui.probe = lambda pid, child: pid == FOREIGN_PID
        ui.started_at = _started(PUBLISHED - 60.0)
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

    serve = _ListeningServe(live_record)
    status = config_ui.main(
        ["--config", str(base), "--start-runtime", "--no-browser"],
        opener=lambda url: None,
        serve=serve,
        instance=config_ui.InstanceSeams(platform="linux"),
    )
    assert status == 0 and serve.starts == []
    assert serve.console_outcome == console.START_ANOTHER_INSTANCE
    lines = _start_lines(caplog)
    assert lines == [f"start-runtime: {console.START_ANOTHER_INSTANCE}"]
    _assert_value_free(lines, serve.ui, tmp_path)
    assert serve.supervision is not None and serve.supervision["state"] == "not_started"


@pytest.mark.parametrize("extra", [[], ["--no-browser"]], ids=["opener", "no_browser"])
def test_ac4_without_the_option_nothing_starts(
    extra: list[str], tmp_path: Path, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("INFO", logger="core.config_ui")
    base = _profile(tmp_path)
    _wizard_state(base, completed=True)
    serve = _ListeningServe()
    status = config_ui.main(
        ["--config", str(base), *extra],
        opener=lambda url: None,
        serve=serve,
        instance=config_ui.InstanceSeams(platform="linux"),
    )
    assert status == 0 and serve.starts == []
    assert _start_lines(caplog) == []
    assert serve.supervision is not None and serve.supervision["state"] == "not_started"


def test_r1_start_runtime_is_absent_by_default() -> None:
    assert config_ui.UISettings.from_argv(["--config", "x.yaml"]).start_runtime is False
    assert config_ui.UISettings.from_argv(["--config", "x.yaml", "--start-runtime"]).start_runtime is True


# -- the health command (R6 rows 1–13, A6, AC19 rows, AC20) ---------------- #

HEALTH_MARKER = "c" * 32
#: The recorded token; no health line may hold it.
HEALTH_TOKEN = "Hk0_health-token-never-printed-AAAAAAAAAAAA"
assert len(HEALTH_TOKEN) == 43
HEALTH_PORT = 8765
HEALTH_URL = f"http://127.0.0.1:{HEALTH_PORT}/?token={HEALTH_TOKEN}"
#: The supervised child's pid and its ``launched_at``, also in epoch seconds.
CHILD_PID = 6001
LAUNCHED_AT = "2026-10-24T08:00:00.000Z"
LAUNCHED = datetime(2026, 10, 24, 8, 0, 0, tzinfo=timezone.utc).timestamp()
#: A value a status record entry carries that no line may quote.
MODULE_VALUE = "module-value-7d1c2e-never-printed"


def _answer(marker: str = HEALTH_MARKER) -> tuple[int, bytes]:
    return 200, json.dumps({"marker": marker, "version": "1.0"}).encode("utf-8")


class _HealthProbe:
    """A fake probe: records each call and answers *outcome* (a status and
    body), or raises it (an exception)."""

    def __init__(self, outcome: Any) -> None:
        self.outcome = outcome
        self.calls: list[tuple[Any, ...]] = []

    def __call__(self, *args: Any, **kwargs: Any) -> tuple[int, bytes]:
        self.calls.append((*args, *kwargs.items()))
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


class _Health:
    """The records of one health case under ``<root>/config.yaml``, each
    replaceable; the defaults are row 10's (ready)."""

    def __init__(self, root: Path) -> None:
        self.base = _profile(root)
        self.instance: Any = {
            "pid": os.getpid(),
            "port": HEALTH_PORT,
            "marker": HEALTH_MARKER,
            "access_url": HEALTH_URL,
        }
        self.probe = _HealthProbe(_answer())
        self.supervision: Any = {
            "version": 1,
            "marker": HEALTH_MARKER,
            "state": "launched",
            "pid": CHILD_PID,
            "launched_at": LAUNCHED_AT,
        }
        self.status: Any = self.status_document(CHILD_PID, {"chat": "ready"})
        self.wizard: bool | None = None
        self.live = True
        self.started: float | None = None
        self.now = LAUNCHED + 10.0

    @staticmethod
    def status_document(pid: int, modules: dict[str, str]) -> dict[str, Any]:
        return {
            "version": 1,
            "pid": pid,
            "started_at": LAUNCHED_AT,
            "published_at": LAUNCHED_AT,
            "sequence": 1,
            "digest": "0" * 64,
            "state": "ready",
            "modules": {name: {"state": state, "detail": MODULE_VALUE} for name, state in modules.items()},
        }

    def _write(self, path: Path, content: Any) -> None:
        if content is None:
            return
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(json.dumps(content), encoding="utf-8")

    def run(self, capsys: pytest.CaptureFixture[str]) -> tuple[str, int]:
        """``main(["--config", base, "--health"])``: the one line and status."""

        settings = config_ui.UISettings.from_argv(["--config", str(self.base)])
        self._write(settings.instance_path, self.instance)
        self._write(settings.supervision_path, self.supervision)
        self._write(settings.status_path, self.status)
        if self.wizard is not None:
            _wizard_state(self.base, completed=self.wizard)
        before = _tree(self.base.parent)
        seams = config_ui.HealthSeams(
            probe=self.probe,
            liveness=lambda pid: self.live and pid == CHILD_PID,
            started_at=lambda pid: self.started,
            clock=lambda: self.now,
            platform="linux",
        )

        def refuse(*args: Any, **kwargs: Any) -> None:
            raise AssertionError("the health command serves nothing and opens nothing")

        status = config_ui.main(
            ["--config", str(self.base), "--health"],
            opener=refuse,
            serve=refuse,
            instance=None,
            health_seams=seams,
        )
        captured = capsys.readouterr()
        assert captured.err == ""
        lines = captured.out.splitlines()
        assert len(lines) == 1 and captured.out == lines[0] + "\n"
        line = lines[0]
        for value in (HEALTH_TOKEN, TOKEN_TEXT, HEALTH_URL, HEALTH_MARKER, MODULE_VALUE, os.fspath(self.base.parent)):
            assert value not in line
        # Nothing is written, created or removed.
        assert _tree(self.base.parent) == before
        return line, status


def _tree(root: Path) -> dict[str, bytes]:
    return {
        os.fspath(path.relative_to(root)): path.read_bytes() if path.is_file() else b"<dir>"
        for path in sorted(root.rglob("*"))
    }


def _no_instance(case: _Health) -> None:
    case.instance = None


def _incomplete_instance(case: _Health) -> None:
    del case.instance["access_url"]


def _instance_not_json(case: _Health) -> None:
    case.instance = b"{not json"


def _refused(case: _Health) -> None:
    case.probe.outcome = ConnectionRefusedError("refused")


def _silent(case: _Health) -> None:
    case.probe.outcome = TimeoutError("silent")


def _token_refused(case: _Health) -> None:
    case.probe.outcome = (401, b"401 Unauthorized")


def _other_marker(case: _Health) -> None:
    case.probe.outcome = _answer("d" * 32)


def _not_200(case: _Health) -> None:
    case.probe.outcome = (503, b"busy")


def _bad_answer(case: _Health) -> None:
    case.probe.outcome = (200, b"<html>")


def _supervision_not_json(case: _Health) -> None:
    case.supervision = b"{not json"


def _supervision_bad_state(case: _Health) -> None:
    case.supervision["state"] = "running"


def _supervision_bad_version(case: _Health) -> None:
    case.supervision["version"] = 2


def _supervision_no_pid(case: _Health) -> None:
    del case.supervision["pid"]


def _supervision_bad_launched_at(case: _Health) -> None:
    case.supervision["launched_at"] = "yesterday"


def _supervision_extra_field(case: _Health) -> None:
    case.supervision = {"version": 1, "marker": HEALTH_MARKER, "state": "stopped", "pid": CHILD_PID}


def _no_supervision_unfinished_wizard(case: _Health) -> None:
    case.supervision = None
    case.wizard = False


def _no_supervision_no_profile(case: _Health) -> None:
    case.supervision = None
    case.base.unlink()


def _other_marker_unfinished_wizard(case: _Health) -> None:
    case.supervision["marker"] = "e" * 32
    case.wizard = False


def _not_started_unfinished_wizard(case: _Health) -> None:
    case.supervision = {"version": 1, "marker": HEALTH_MARKER, "state": "not_started"}
    case.wizard = False


def _no_supervision_completed(case: _Health) -> None:
    case.supervision = None
    case.wizard = True


def _other_marker_completed(case: _Health) -> None:
    case.supervision["marker"] = "e" * 32


def _not_started_completed(case: _Health) -> None:
    case.supervision = {"version": 1, "marker": HEALTH_MARKER, "state": "not_started"}


def _stopped(case: _Health) -> None:
    case.supervision = {"version": 1, "marker": HEALTH_MARKER, "state": "stopped"}


def _exited(case: _Health) -> None:
    case.live = False


def _pid_reused(case: _Health) -> None:
    # Live, but started after launched_at: not the launched child (R2).
    case.started = LAUNCHED + 0.001


def _ready(case: _Health) -> None:
    case.started = LAUNCHED - 1.0


def _degraded(case: _Health) -> None:
    case.status = case.status_document(CHILD_PID, {"zeta": "degraded", "mid": "ready", "alpha": "degraded"})


def _no_status_within_window(case: _Health) -> None:
    case.status = None
    case.now = LAUNCHED + 60.0


def _other_pid_within_window(case: _Health) -> None:
    case.status = case.status_document(CHILD_PID + 1, {"chat": "ready"})


def _unusable_status_within_window(case: _Health) -> None:
    case.status = b"{not json"


def _no_status_past_window(case: _Health) -> None:
    case.status = None
    case.now = LAUNCHED + 60.001


def _other_pid_past_window(case: _Health) -> None:
    case.status = case.status_document(CHILD_PID + 1, {"chat": "ready"})
    case.now = LAUNCHED + 3600.0


HEALTH_ROWS = [
    pytest.param(_no_instance, "unhealthy: no instance record", 1, id="row1_absent"),
    pytest.param(_incomplete_instance, "unhealthy: no instance record", 1, id="row1_incomplete"),
    pytest.param(_instance_not_json, "unhealthy: no instance record", 1, id="row1_not_json"),
    pytest.param(_refused, "unhealthy: not answering", 1, id="row2_refused"),
    pytest.param(_silent, "unhealthy: not answering", 1, id="row2_silent"),
    pytest.param(_token_refused, "unhealthy: token refused", 1, id="row3"),
    pytest.param(_other_marker, "unhealthy: another instance", 1, id="row4_marker"),
    pytest.param(_not_200, "unhealthy: another instance", 1, id="row4_status"),
    pytest.param(_bad_answer, "unhealthy: another instance", 1, id="row4_body"),
    pytest.param(_supervision_not_json, "unhealthy: supervision record unusable", 1, id="row5_not_json"),
    pytest.param(_supervision_bad_state, "unhealthy: supervision record unusable", 1, id="row5_state"),
    pytest.param(_supervision_bad_version, "unhealthy: supervision record unusable", 1, id="row5_version"),
    pytest.param(_supervision_no_pid, "unhealthy: supervision record unusable", 1, id="row5_pid"),
    pytest.param(_supervision_bad_launched_at, "unhealthy: supervision record unusable", 1, id="row5_time"),
    pytest.param(_supervision_extra_field, "unhealthy: supervision record unusable", 1, id="row5_extra"),
    pytest.param(_no_supervision_unfinished_wizard, "idle: wizard unfinished", 0, id="row6_absent"),
    pytest.param(_no_supervision_no_profile, "idle: wizard unfinished", 0, id="row6_no_profile"),
    pytest.param(_other_marker_unfinished_wizard, "idle: wizard unfinished", 0, id="row6_marker"),
    pytest.param(_not_started_unfinished_wizard, "idle: wizard unfinished", 0, id="row6_not_started"),
    pytest.param(_no_supervision_completed, "idle: not started", 0, id="row7_absent"),
    pytest.param(_other_marker_completed, "idle: not started", 0, id="row7_marker"),
    pytest.param(_not_started_completed, "idle: not started", 0, id="row7_not_started"),
    pytest.param(_stopped, "idle: stopped", 0, id="row8"),
    pytest.param(_exited, "unhealthy: runtime exited", 1, id="row9_dead"),
    pytest.param(_pid_reused, "unhealthy: runtime exited", 1, id="row9_reused"),
    pytest.param(_ready, "ready", 0, id="row10"),
    pytest.param(_degraded, "degraded: alpha,zeta", 0, id="row11"),
    pytest.param(_no_status_within_window, "starting", 0, id="row12_absent_at_60s"),
    pytest.param(_other_pid_within_window, "starting", 0, id="row12_other_pid"),
    pytest.param(_unusable_status_within_window, "starting", 0, id="row12_unusable"),
    pytest.param(_no_status_past_window, "unhealthy: runtime not ready", 1, id="row13_absent"),
    pytest.param(_other_pid_past_window, "unhealthy: runtime not ready", 1, id="row13_other_pid"),
]


@pytest.mark.parametrize(("setup", "line", "exit_status"), HEALTH_ROWS)
def test_ac19_each_row_prints_its_line_and_exits_with_its_status(
    setup: Callable[[_Health], None],
    line: str,
    exit_status: int,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case = _Health(tmp_path)
    setup(case)
    assert case.run(capsys) == (line, exit_status)


def test_ac19_every_row_is_covered() -> None:
    rows = {int(param.id.split("_")[0][3:]) for param in HEALTH_ROWS}  # type: ignore[union-attr]
    assert rows == set(range(1, 14))
    lines = {param.values[1] for param in HEALTH_ROWS}
    fixed = {value for name, value in vars(config_ui).items() if name.startswith("HEALTH_") and isinstance(value, str)}
    assert lines - {"degraded: alpha,zeta"} == fixed - {
        config_ui.HEALTH_DEGRADED_PREFIX,
        config_ui.HEALTH_PROBE_HOST,
    }


def _combined(*setups: Callable[[_Health], None]) -> Callable[[_Health], None]:
    def setup(case: _Health) -> None:
        for each in setups:
            each(case)

    return setup


@pytest.mark.parametrize(
    ("setup", "line"),
    [
        pytest.param(_combined(_token_refused, _supervision_not_json), "unhealthy: token refused", id="3_over_5"),
        pytest.param(_combined(_no_instance, _supervision_not_json), "unhealthy: no instance record", id="1_over_5"),
        pytest.param(_combined(_refused, _exited), "unhealthy: not answering", id="2_over_9"),
        pytest.param(_combined(_other_marker, _stopped), "unhealthy: another instance", id="4_over_8"),
        pytest.param(
            _combined(_supervision_bad_state, _no_supervision_no_profile),
            "idle: wizard unfinished",
            id="absent_supervision_is_not_unusable",
        ),
        pytest.param(_combined(_supervision_extra_field, _degraded), "unhealthy: supervision record unusable", id="5_over_11"),
        pytest.param(_combined(_stopped, _degraded), "idle: stopped", id="8_over_11"),
        pytest.param(_combined(_exited, _ready), "unhealthy: runtime exited", id="9_over_10"),
        pytest.param(_combined(_degraded, _no_status_past_window), "unhealthy: runtime not ready", id="13_without_status"),
    ],
)
def test_ac19_a_case_matching_two_rows_takes_the_earlier(
    setup: Callable[[_Health], None], line: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    case = _Health(tmp_path)
    setup(case)
    assert case.run(capsys)[0] == line


def test_ac19_the_probe_carries_the_records_token_and_no_session(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    case = _Health(tmp_path)
    assert case.run(capsys) == ("ready", 0)
    # One probe, with the record's port, its access URL (for the token only)
    # and the 5-second budget; nothing else is handed to it, no cookie.
    assert case.probe.calls == [(HEALTH_PORT, HEALTH_URL, 5.0)]
    assert config_ui._url_token(case.probe.calls[0][1]) == HEALTH_TOKEN


def test_ac19_no_probe_is_made_without_a_usable_record(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    case = _Health(tmp_path)
    _incomplete_instance(case)
    assert case.run(capsys)[0] == "unhealthy: no instance record"
    assert case.probe.calls == []


def test_ac19_the_windows_record_has_no_token_so_row_1_decides(tmp_path: Path) -> None:
    case = _Health(tmp_path)
    settings = config_ui.UISettings.from_argv(["--config", str(case.base)])
    del case.instance["access_url"]
    settings.instance_path.write_text(json.dumps(case.instance), encoding="utf-8")
    seams = config_ui.HealthSeams(probe=case.probe, platform=locations.WINDOWS)
    assert config_ui.health(settings, seams) == ("unhealthy: no instance record", 1)
    assert case.probe.calls == []


def test_ac19_a_degraded_name_that_cannot_stand_in_one_line_is_not_printed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    case = _Health(tmp_path)
    case.status = case.status_document(CHILD_PID, {"bad\nname": "degraded", "a,b": "degraded"})
    assert case.run(capsys) == ("starting", 0)


def test_ac19_the_records_the_ui_writes_are_usable() -> None:
    child = SimpleNamespace(pid=CHILD_PID)
    launched = config_ui.parse_supervision_record(
        config_ui.supervision_document(HEALTH_MARKER, "launched", child)
    )
    assert (launched.marker, launched.state, launched.pid) == (HEALTH_MARKER, "launched", CHILD_PID)
    assert launched.launched_at is not None
    for state in ("not_started", "stopped"):
        parsed = config_ui.parse_supervision_record(config_ui.supervision_document(HEALTH_MARKER, state))
        assert (parsed.state, parsed.pid, parsed.launched_at) == (state, None, None)


@pytest.mark.parametrize(
    "raw",
    [
        b'{"version": 1, "version": 1, "marker": "m", "state": "stopped"}',
        b'{"version": 1, "marker": "m", "state": "stopped", "x": NaN}',
    ],
    ids=["duplicate_key", "nan"],
)
def test_ac19_a_record_the_json_hooks_refuse_is_unusable(raw: bytes) -> None:
    with pytest.raises(config_ui.SupervisionUnusable):
        config_ui.parse_supervision_record(raw)


def test_ac21_an_unreaped_runtime_is_not_live(monkeypatch: pytest.MonkeyPatch) -> None:
    """A runtime killed after startup stays a zombie until its UI reaps it;
    signal 0 still succeeds, so the default liveness reads its state."""

    monkeypatch.setattr(os, "kill", lambda pid, signal_number: None)
    monkeypatch.setattr(sys, "platform", "linux")
    stat = {"text": f"{CHILD_PID} (python3 (rt)) Z 1 2 3"}
    monkeypatch.setattr(config_ui, "_read_proc_text", lambda path: stat["text"])
    assert config_ui.process_alive(CHILD_PID, None) is False
    assert config_ui.HealthSeams().liveness(CHILD_PID) is False
    stat["text"] = f"{CHILD_PID} (python3) S 1 2 3"
    assert config_ui.process_alive(CHILD_PID, None) is True
    assert config_ui.process_is_zombie(CHILD_PID, read_text=lambda path: "garbage") is False
    assert config_ui.process_is_zombie(CHILD_PID, read_text=lambda path: stat["text"], platform="darwin") is False


def test_ac19_health_resolves_the_default_location_and_creates_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], linux_platform: None
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    probe = _HealthProbe(_answer())
    seams = config_ui.HealthSeams(probe=probe, platform="linux")
    assert config_ui.main(["--health"], health_seams=seams) == 1
    assert capsys.readouterr().out == "unhealthy: no instance record\n"
    assert list(tmp_path.iterdir()) == []
    directory = tmp_path / PRODUCT
    directory.mkdir(mode=0o700)
    record = directory / ("profile.yaml" + config_ui.INSTANCE_SUFFIX)
    record.write_text(
        json.dumps({"pid": 1, "port": 9999, "marker": HEALTH_MARKER, "access_url": HEALTH_URL}),
        encoding="utf-8",
    )
    assert config_ui.main(["--health"], health_seams=seams) == 0
    assert capsys.readouterr().out == "idle: wizard unfinished\n"
    assert probe.calls == [(9999, HEALTH_URL, 5.0)]


def test_r6_health_is_absent_by_default() -> None:
    assert config_ui.UISettings.from_argv(["--config", "x.yaml"]).health is False
    assert config_ui.UISettings.from_argv(["--config", "x.yaml", "--health"]).health is True


def test_r6_the_default_probe_is_looked_up_when_called(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[Any] = []
    monkeypatch.setattr(config_ui, "_health_probe", lambda *args: calls.append(args) or (200, b""))
    assert config_ui.HealthSeams().probe(1, "u", 5.0) == (200, b"")
    assert calls == [(1, "u", 5.0)]


# -- AC20: the default probe -------------------------------------------------- #


class _Peer:
    """A fake connection for the default probe: answers *chunks* one per
    ``recv``; with none left, it never answers (each ``recv`` waits its whole
    timeout on the fake clock, then times out)."""

    def __init__(self, clock: list[float], chunks: list[bytes]) -> None:
        self.clock, self.chunks = clock, list(chunks)
        self.sent = b""
        self.timeout: float | None = None

    def __enter__(self) -> _Peer:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def settimeout(self, timeout: float) -> None:
        self.timeout = timeout

    def sendall(self, data: bytes) -> None:
        self.sent += data

    def recv(self, _size: int) -> bytes:
        if self.chunks:
            return self.chunks.pop(0)
        assert self.timeout is not None
        self.clock[0] += self.timeout
        raise TimeoutError("silent")


class _Connector:
    def __init__(self, peer: _Peer | None, clock: list[float]) -> None:
        self.peer, self.clock = peer, clock
        self.addresses: list[tuple[str, int]] = []

    def __call__(self, address: tuple[str, int], timeout: float) -> _Peer:
        self.addresses.append(address)
        if self.peer is None:
            # A connection that never completes: it waits its timeout.
            self.clock[0] += timeout
            raise TimeoutError("no connection")
        return self.peer


def _default_probe(connector: _Connector, clock: list[float]) -> Callable[[int, str, float], tuple[int, bytes]]:
    def probe(port: int, url: str, timeout: float) -> tuple[int, bytes]:
        return config_ui._health_probe(port, url, timeout, connect=connector, clock=lambda: clock[0])

    return probe


def test_ac20_the_default_probe_targets_loopback_with_the_token_and_no_cookie() -> None:
    clock = [100.0]
    body = json.dumps({"marker": HEALTH_MARKER, "version": "1.0"}).encode("utf-8")
    peer = _Peer(clock, [b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\n\r\n" % len(body) + body, b""])
    connector = _Connector(peer, clock)
    # Whatever host the access URL names, the probe goes to 127.0.0.1.
    url = f"http://localhost:{HEALTH_PORT}/?token={HEALTH_TOKEN}"
    assert _default_probe(connector, clock)(HEALTH_PORT, url, 5.0) == (200, body)
    assert connector.addresses == [("127.0.0.1", HEALTH_PORT)]
    head = peer.sent.decode("ascii")
    assert head.startswith(f"GET /instance?token={HEALTH_TOKEN} HTTP/1.1\r\n")
    assert f"\r\nHost: 127.0.0.1:{HEALTH_PORT}\r\n" in head
    assert "cookie" not in head.lower()


@pytest.mark.parametrize("connects", [True, False], ids=["silent_peer", "never_connects"])
def test_ac20_a_probe_that_never_answers_ends_within_5_seconds_unhealthy(
    connects: bool, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    clock = [100.0]
    connector = _Connector(_Peer(clock, []) if connects else None, clock)
    case = _Health(tmp_path)
    case.probe = _default_probe(connector, clock)  # type: ignore[assignment]
    assert case.run(capsys) == ("unhealthy: not answering", 1)
    assert connector.addresses == [("127.0.0.1", HEALTH_PORT)]
    assert 0 < clock[0] - 100.0 <= 5.0


# -- route-class invariance (R1, AC5) ---------------------------------------- #

#: The four container-mode settings AC5 combines, each as ``main``'s
#: arguments; ``--token-file``'s path is filled in per test.
CONTAINER_OPTIONS = ("token_file", "no_browser", "start_runtime", "non_loopback")
COMBINATIONS = [
    pytest.param(chosen, id="+".join(chosen) or "none")
    for mask in range(1 << len(CONTAINER_OPTIONS))
    for chosen in [tuple(name for bit, name in enumerate(CONTAINER_OPTIONS) if mask >> bit & 1)]
]
assert len(COMBINATIONS) == 16
#: The accepted Host every request names: loopback, which a wildcard bind
#: also accepts (A8), on the default port.
ROUTE_HOST = f"127.0.0.1:{config_ui.DEFAULT_PORT}"
#: A session cookie no UI issued.
FORGED_COOKIE = f"{config_ui.SESSION_COOKIE}=forged"


def _route_request(method: str, path: str, *, headers: dict[str, str] | None = None) -> Any:
    return _request(method, path, host=ROUTE_HOST, headers=headers)


def _class_iii_requests(ui: Any) -> dict[tuple[str, str, str], Any]:
    """Every route of *ui*'s table but ``/instance`` (class (i)), by GET and
    by a CSRF-carrying form POST, without a cookie and with a forged one;
    ``GET /`` carries no token, so it is not the access URL (class (ii))."""

    requests: dict[tuple[str, str, str], Any] = {}
    form = {"content-type": "application/x-www-form-urlencoded"}
    for route in ui.routes:
        if route == config_ui.INSTANCE_ROUTE:
            continue
        path = route + "twitch" if route == config_ui.MODULE_PAGE_PREFIX else route
        for cookie in ("none", "forged"):
            jar = {} if cookie == "none" else {"cookie": FORGED_COOKIE}
            requests[("GET", path, cookie)] = _route_request("GET", path, headers=jar)
            post = _request(
                "POST", path, host=ROUTE_HOST, headers={**form, **jar}, body=b"csrf_token=guessed"
            )
            requests[("POST", path, cookie)] = post
    return requests


def _answers(ui: Any, requests: dict[tuple[str, str, str], Any]) -> dict[tuple[str, str, str], tuple[Any, ...]]:
    answered = {}
    for key, request in requests.items():
        response = ui.handle(request)
        answered[key] = (response.status, response.body, response.content_type, response.headers)
    return answered


def _header_names(response: Any) -> set[str]:
    return {name.lower() for name, _value in response.headers}


class _RouteServe:
    """A fake ``serve``: fakes the supervisor's launches, gives the
    listening signal as the real one does, then drives the route table
    in-process (no socket) and keeps what it answered."""

    def __init__(self) -> None:
        self.ui: Any = None
        self.settings: Any = None
        self.class_iii: dict[tuple[str, str, str], tuple[Any, ...]] = {}
        self.instance: Any = None
        self.instance_without_token: Any = None
        self.instance_wrong_token: Any = None
        self.access: Any = None
        self.sessions_after_instance: int | None = None
        self.session_from_access: Any = None

    def __call__(self, ui: Any, settings: Any) -> None:
        self.ui, self.settings = ui, settings
        try:
            _fake_launches(ui)
            if ui.on_listening is not None:
                ui.on_listening()
            self.class_iii = _answers(ui, _class_iii_requests(ui))
            route = config_ui.INSTANCE_ROUTE
            sessions = len(ui._sessions)
            self.instance = ui.handle(_request("GET", route, host=ROUTE_HOST, query={"token": ui.token}))
            self.instance_without_token = ui.handle(_route_request("GET", route))
            wrong = ui.token[:-1] + ("B" if ui.token[-1] != "B" else "C")
            self.instance_wrong_token = ui.handle(_request("GET", route, host=ROUTE_HOST, query={"token": wrong}))
            self.sessions_after_instance = len(ui._sessions) - sessions
            self.access = ui.handle(_request("GET", "/", host=ROUTE_HOST, query={"token": ui.token}))
            cookie = dict(self.access.headers).get("Set-Cookie", "").split(";")[0]
            self.session_from_access = ui._session(_route_request("GET", "/", headers={"cookie": cookie}))
        finally:
            config_ui._LOG_REDACTION.sources.discard(ui)


def _reference_refusals(base: Path) -> tuple[set[str], dict[tuple[str, str, str], tuple[Any, ...]]]:
    """The route table and the class-(iii) refusals of a UI built from a
    default :class:`UISettings` over *base*: what every request gets today."""

    ui = config_ui.ConfigUI(config_ui.UISettings.from_argv(["--config", str(base)]))
    try:
        return set(ui.routes), _answers(ui, _class_iii_requests(ui))
    finally:
        config_ui._LOG_REDACTION.sources.discard(ui)


@pytest.mark.parametrize("chosen", COMBINATIONS)
def test_ac5_the_route_classes_are_phase_9s_under_every_combination(
    chosen: tuple[str, ...], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    base = _profile(tmp_path)
    routes, reference = _reference_refusals(base)
    # The reference is a refusal on every class-(iii) route, never a page.
    assert reference and {answer[0] for answer in reference.values()} == {401}

    token_file = tmp_path / "token"
    token_file.write_text(VALID_TOKEN + "\n", encoding="utf-8")
    argv = ["--config", str(base)]
    if "token_file" in chosen:
        argv += ["--token-file", str(token_file)]
    if "no_browser" in chosen:
        argv.append("--no-browser")
    if "start_runtime" in chosen:
        argv.append("--start-runtime")
    if "non_loopback" in chosen:
        argv += ["--host", "0.0.0.0", "--allow-non-loopback"]
    serve = _RouteServe()
    opened: list[str] = []
    status = config_ui.main(argv, opener=opened.append, serve=serve, instance=config_ui.InstanceSeams(platform="linux"))
    capsys.readouterr()
    assert status == 0 and serve.ui is not None

    # The combination is the one in effect.
    settings, ui = serve.settings, serve.ui
    assert settings.no_browser is ("no_browser" in chosen)
    assert settings.start_runtime is ("start_runtime" in chosen)
    assert settings.host == ("0.0.0.0" if "non_loopback" in chosen else "127.0.0.1")
    assert (ui.token == VALID_TOKEN) is ("token_file" in chosen)
    assert len(opened) == (0 if "no_browser" in chosen else 1)

    # Class (iii): the same route table, and every route refused exactly as
    # the default configuration refuses it.
    assert set(ui.routes) == routes
    assert serve.class_iii == reference

    # Class (i): the token, no session cookie → the marker and version only,
    # no cookie and no session; without the token (or a wrong one) → 401.
    instance = serve.instance
    assert instance.status == 200 and instance.content_type == "application/json"
    document = json.loads(instance.body)
    assert set(document) == {"marker", "version"}
    assert document == {"marker": ui.instance_marker, "version": locations.product_version()}
    assert "set-cookie" not in _header_names(instance)
    assert serve.instance_without_token.status == 401
    assert serve.instance_wrong_token.status == 401
    assert "set-cookie" not in _header_names(serve.instance_without_token)
    assert serve.sessions_after_instance == 0

    # Class (ii): the access URL → a redirect setting the session cookie,
    # no content.
    access = serve.access
    assert access.status == 303 and access.body == b""
    headers = dict(access.headers)
    assert "Location" in headers
    assert headers["Set-Cookie"].startswith(config_ui.SESSION_COOKIE + "=")
    assert serve.session_from_access is not None


# -- credential precedence (R5, AC18) ---------------------------------------- #

#: The credential both inherited and stored, and its two sentinel values.
CREDENTIAL = "TWITCH_ACCESS_TOKEN"
INHERITED_VALUE = "inherited-sentinel-7c1d90a4"
STORED_VALUE = "stored-sentinel-e52b6f13"


def test_ac18_the_stored_credential_wins_in_the_supervised_childs_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, linux_platform: None
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv(CREDENTIAL, INHERITED_VALUE)
    base = _profile(tmp_path)
    base.write_text(
        f"modules_directory: ./mods\nenabled_modules: []\nsecrets: ['${{{CREDENTIAL}}}']\n", encoding="utf-8"
    )
    state = config_ui.wizard.WizardState(base)
    store = config_ui.wizard.CredentialStore(base, locations.running_platform(), os.environ)
    assert store.write({CREDENTIAL: STORED_VALUE}, [f"${{{CREDENTIAL}}}"], state) is None
    state.completed = True
    state.save(store)
    assert store.path is not None and STORED_VALUE in store.path.read_text(encoding="utf-8")

    # The UI's environment is the process's own, inherited one.
    settings = config_ui.UISettings.from_argv(["--config", str(base)])
    ui = config_ui.ConfigUI(settings)
    try:
        assert os.environ[CREDENTIAL] == INHERITED_VALUE
        # The existing builder, called directly.
        built = config_ui._LaunchEnviron(lambda: ui.environ, settings.feed_path)
        assert built[CREDENTIAL] == STORED_VALUE
        assert dict(built)[CREDENTIAL] == STORED_VALUE

        # And what the supervisor actually hands its child.
        launched: list[dict[str, str]] = []

        def popen(*args: Any, **options: Any) -> _FakeChild:
            launched.append(dict(options["env"]))
            return _FakeChild(5001)

        ui.supervisor.popen = popen
        ui.supervisor.start()
        ui.supervisor.stop()
        (env,) = launched
        assert env[CREDENTIAL] == STORED_VALUE
        assert INHERITED_VALUE not in env.values()
    finally:
        ui.supervisor.close()
        config_ui._LOG_REDACTION.sources.discard(ui)


# -- the image's static contracts (R3, R5, AC11, AC17) ------------------------ #

ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = ROOT / "Dockerfile"
DOCKERIGNORE = ROOT / ".dockerignore"
PROFILES = ROOT / "core" / "config_ui" / "profiles"

#: AC11: the final base names a supported Python, a variant and a digest.
PINNED_BASE = re.compile(r"python:3\.(1[0-3])[^@]*-[a-z0-9]+@sha256:[0-9a-f]{64}")
#: A2's default command, argument for argument.
CONTAINER_COMMAND = [
    "twitch-ia-compagnon-config-ui",
    "--host",
    "0.0.0.0",
    "--allow-non-loopback",
    "--no-browser",
    "--start-runtime",
]
#: Stage 1 copies these sources by name and nothing else.
BUILD_SOURCES = ["pyproject.toml", "LICENSE", "core", "modules"]
#: The final stage's whole environment: the base's ``PATH`` and ``LANG``,
#: the companion's three; never the base's ``GPG_KEY`` (AC17) or its other
#: build variables.
FINAL_ENVIRONMENT = {
    "PATH": "/usr/local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    "LANG": "C.UTF-8",
    "XDG_CONFIG_HOME": "/data",
    "HOME": "/data",
    "PYTHONUNBUFFERED": "1",
}
#: AC17: names an image environment variable or label must never match.
SECRET_NAME = re.compile(r".*_KEY|.*_TOKEN|.*_SECRET|.*PASSWORD.*")


def _instructions(text: str) -> list[tuple[str, str]]:
    """The Dockerfile's instructions as ``(KEYWORD, arguments)``, continuations joined."""

    instructions: list[tuple[str, str]] = []
    pending = ""
    for line in text.splitlines():
        stripped = line.strip()
        if not pending and (not stripped or stripped.startswith("#")):
            continue
        if pending and stripped.startswith("#"):
            continue
        if stripped.endswith("\\"):
            pending += stripped[:-1] + " "
            continue
        whole = pending + stripped
        pending = ""
        keyword, _, arguments = whole.partition(" ")
        instructions.append((keyword.upper(), arguments.strip()))
    assert not pending, "the Dockerfile ends inside a continuation"
    return instructions


def _stages() -> list[list[tuple[str, str]]]:
    """The instructions grouped by stage, each list starting with its ``FROM``."""

    stages: list[list[tuple[str, str]]] = []
    for keyword, arguments in _instructions(DOCKERFILE.read_text(encoding="utf-8")):
        if keyword == "FROM":
            stages.append([])
        assert stages, f"{keyword} before the first FROM"
        stages[-1].append((keyword, arguments))
    return stages


def _arguments(stage: list[tuple[str, str]], keyword: str) -> list[str]:
    return [arguments for name, arguments in stage if name == keyword]


def _copy_sources(arguments: str) -> tuple[list[str], list[str]]:
    """A ``COPY``'s ``--flags`` and its sources (every word but the destination)."""

    words = json.loads(arguments) if arguments.startswith("[") else arguments.split()
    flags = [word for word in words if word.startswith("--")]
    paths = [word for word in words if not word.startswith("--")]
    return flags, paths[:-1]


def _env_and_label_names(stage: list[tuple[str, str]]) -> list[str]:
    names: list[str] = []
    for keyword, arguments in stage:
        if keyword in ("ENV", "LABEL"):
            names.extend(word.split("=", 1)[0].strip('"') for word in arguments.split())
        if keyword == "ARG":
            names.append(arguments.split("=", 1)[0])
    return names


def _credential_names() -> set[str]:
    names = set()
    for path in sorted(PROFILES.glob("*.json")):
        profile = json.loads(path.read_text(encoding="utf-8"))
        names.update(credential["name"] for credential in profile["credentials"])
    return names


def test_ac11_the_build_and_runtime_stages_on_one_digest_pinned_base() -> None:
    stages = _stages()
    assert len(stages) == 3
    (build_from,), (runtime_from,), (final_from,) = (_arguments(stage, "FROM") for stage in stages)
    runtime_words = runtime_from.split()
    runtime_image = runtime_words[0]
    assert PINNED_BASE.fullmatch(runtime_image), runtime_image
    assert [word.lower() for word in runtime_words[1:]] == ["as", "runtime"]
    build_words = build_from.split()
    assert build_words[0] == runtime_image
    assert [word.lower() for word in build_words[1:]] == ["as", "build"]
    # The final stage is the runtime stage's files without its base's
    # environment: Docker cannot unset an inherited variable (AC17).
    assert final_from == "scratch"


def test_ac11_the_final_stage_is_the_runtime_stages_files_and_nothing_else() -> None:
    _, runtime, final = _stages()
    runtime_name = _arguments(runtime, "FROM")[0].split()[-1]
    copies = [_copy_sources(arguments) for arguments in _arguments(final, "COPY")]
    assert copies == [([f"--from={runtime_name}"], ["/"])]
    assert _arguments(final, "COPY")[0].split()[-1] == "/"
    assert not _arguments(final, "RUN") and not _arguments(final, "ADD")


def test_ac11_the_final_stage_installs_the_first_stages_wheel_and_no_checkout() -> None:
    build, runtime, _ = _stages()
    build_name = _arguments(build, "FROM")[0].split()[-1]
    copies = [_copy_sources(arguments) for arguments in _arguments(runtime, "COPY")]
    assert copies == [([f"--from={build_name}"], ["/wheels"])]
    assert "/wheels" in " ".join(_arguments(build, "RUN"))
    assert "--wheel-dir /wheels" in " ".join(_arguments(build, "RUN"))
    installs = [run for run in _arguments(runtime, "RUN") if "pip install" in run]
    assert len(installs) == 1 and re.search(r"pip install [^&]*/wheels/\*\.whl", installs[0])
    assert not _arguments(runtime, "ADD")


def test_ac11_the_build_stage_copies_only_the_wheels_files_by_name() -> None:
    for stage in _stages():
        assert not _arguments(stage, "ADD")
        for arguments in _arguments(stage, "COPY"):
            flags, sources = _copy_sources(arguments)
            if any(flag.startswith("--from=") for flag in flags):
                # A stage's files, not the context's (the final stage's ``/``).
                continue
            assert all(source.rstrip("/") not in (".", "./", "") for source in sources), arguments
    build = _stages()[0]
    sources = [source for arguments in _arguments(build, "COPY") for source in _copy_sources(arguments)[1]]
    assert sources == BUILD_SOURCES


def test_ac11_the_final_user_is_numeric_10001_with_its_data_volume() -> None:
    _, runtime, final = _stages()
    assert _arguments(final, "USER")[-1] == "10001"
    assert _arguments(final, "VOLUME") == ["/data"]
    assert _arguments(final, "EXPOSE") == ["8765"]
    runs = " ".join(_arguments(runtime, "RUN"))
    assert "mkdir -m 0700 /data" in runs and "chown 10001:10001 /data" in runs
    assert "--uid 10001" in runs and "--gid 10001" in runs and "--home-dir /data" in runs


def test_ac17_the_final_stage_declares_exactly_the_companions_environment() -> None:
    build, runtime, final = _stages()
    words = " ".join(_arguments(final, "ENV")).split()
    assert dict(word.split("=", 1) for word in words) == FINAL_ENVIRONMENT
    assert len(words) == len(FINAL_ENVIRONMENT)
    # The final stage starts from nothing, so its ENV is the image's whole
    # environment; the stages before it declare none of their own.
    assert not _arguments(build, "ENV") and not _arguments(runtime, "ENV")


def test_ac11_the_default_command_is_a2s_and_parses_as_container_mode() -> None:
    final = _stages()[-1]
    (command,) = _arguments(final, "CMD")
    argv = json.loads(command)
    assert argv == CONTAINER_COMMAND
    settings = config_ui.UISettings.from_argv(argv[1:])
    assert settings.host == "0.0.0.0" and settings.allow_non_loopback
    assert settings.no_browser and settings.start_runtime and settings.default_location
    assert settings.token_file is None and not settings.health


def test_ac11_the_healthcheck_runs_the_health_command() -> None:
    final = _stages()[-1]
    (healthcheck,) = _arguments(final, "HEALTHCHECK")
    options, _, command = healthcheck.partition(" CMD ")
    assert json.loads(command) == ["twitch-ia-compagnon-config-ui", "--health"]
    flags = dict(option.lstrip("-").split("=", 1) for option in options.split())
    assert flags["timeout"] == "10s"
    assert {"interval", "start-period"} <= set(flags)
    assert config_ui.UISettings.from_argv(["--health"]).health


def test_ac17_no_image_environment_label_or_argument_names_a_secret() -> None:
    credentials = _credential_names()
    assert credentials, "the bundled profiles name no credential"
    names = [name for stage in _stages() for name in _env_and_label_names(stage)]
    assert names
    assert [name for name in names if name in credentials or SECRET_NAME.fullmatch(name)] == []


# .dockerignore: Docker's rules modelled in Python (finding P2).

#: AC17's and the plan's deny patterns, in their order after the re-includes.
DENY_PATTERNS = [
    ".git",
    ".venv",
    "venv",
    "**/__pycache__",
    ".pytest_cache",
    "*.egg-info",
    "build",
    "dist",
    "**/profile.yaml",
    "**/*.local.yaml",
    "**/*.status.json",
    "**/*.credentials.json",
    "**/*.instance.json",
    "**/*.supervisor.json",
    "**/*.wizard.json",
    "**/*.feed*",
    "**/.env*",
    "docs/runs/",
    "findings.json",
]
RE_INCLUDES = ["!pyproject.toml", "!LICENSE", "!core/", "!modules/"]
EXCLUDED = [
    "profile.yaml",
    "config/profile.yaml",
    "core/profile.yaml",
    "profile.instance.json",
    "core/x.instance.json",
    "profile.supervisor.json",
    "profile.local.yaml",
    "profile.credentials.json",
    "profile.status.json",
    ".env",
    ".env.local",
    "docs/runs/x.md",
    ".git/config",
]
INCLUDED_FIXED = [
    "pyproject.toml",
    "LICENSE",
    "core/config_ui/__init__.py",
    "core/config_ui/strings/en.json",
    "modules/twitch/module.yaml",
]


def _patterns() -> list[str]:
    """``.dockerignore``'s patterns: comments and blank lines dropped, each trimmed."""

    lines = DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")]


def _pattern_regex(pattern: str) -> re.Pattern[str]:
    """One pattern as Docker's pattern matcher compiles it (``/`` separators)."""

    pattern = os.path.normpath(pattern).replace(os.sep, "/").lstrip("/")
    parts: list[str] = []
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if pattern.startswith("**", index):
            index += 2
            if pattern.startswith("/", index):
                index += 1
            parts.append(".*" if index == len(pattern) else "(.*/)?")
            continue
        if char == "*":
            parts.append("[^/]*")
        elif char == "?":
            parts.append("[^/]")
        elif char == "\\" and index + 1 < len(pattern):
            index += 1
            parts.append(re.escape(pattern[index]))
        elif char == "[":
            end = pattern.index("]", index)
            parts.append(pattern[index : end + 1])
            index = end
        else:
            parts.append(re.escape(char))
        index += 1
    return re.compile("".join(parts))


def _excluded(path: str, patterns: list[str]) -> bool:
    """Whether Docker leaves *path* out of the context: the last matching pattern
    decides, and a pattern matches a path or any of its parent directories."""

    components = path.split("/")
    candidates = ["/".join(components[: count + 1]) for count in range(len(components))]
    excluded = False
    for pattern in patterns:
        negated = pattern.startswith("!")
        regex = _pattern_regex(pattern[1:] if negated else pattern)
        if any(regex.fullmatch(candidate) for candidate in candidates):
            excluded = not negated
    return excluded


def _included_paths() -> list[str]:
    bundled = sorted(path.relative_to(ROOT).as_posix() for path in PROFILES.glob("*.json"))
    assert len(bundled) == 4, bundled
    return INCLUDED_FIXED + bundled


def test_ac17_the_evaluator_follows_dockers_documented_rules() -> None:
    # The rules the model claims, on patterns unrelated to the file.
    assert _excluded("a/b/c.txt", ["a"])
    assert not _excluded("x/a", ["a"])
    assert _excluded("x/y/a", ["**/a"]) and _excluded("a", ["**/a"])
    assert not _excluded("x/a.md", ["*.md"]) and _excluded("a.md", ["*.md"])
    assert not _excluded("keep.md", ["*.md", "!keep.md"])
    assert _excluded("keep.md", ["!keep.md", "*.md"])
    assert _excluded("d/e/f", ["d/**"]) and _excluded("d/e", ["d/"])
    assert not _excluded("d", ["d?"]) and _excluded("dx", ["d?"])


def test_ac17_dockerignore_is_an_allowlist_with_the_deny_patterns_after_it() -> None:
    patterns = _patterns()
    assert patterns[0] == "*"
    assert [pattern for pattern in patterns if pattern.startswith("!")] == RE_INCLUDES
    assert patterns[1 : 1 + len(RE_INCLUDES)] == RE_INCLUDES
    after = patterns[1 + len(RE_INCLUDES) :]
    for required in DENY_PATTERNS:
        assert required in after, required
    for named in ("**/profile.yaml", "**/*.instance.json", "**/*.supervisor.json", "**/*.wizard.json"):
        assert named in after


def test_ac17_dockerignore_excludes_every_operator_file_and_keeps_the_wheels() -> None:
    patterns = _patterns()
    assert {path: _excluded(path, patterns) for path in EXCLUDED} == dict.fromkeys(EXCLUDED, True)
    included = _included_paths()
    assert {path: _excluded(path, patterns) for path in included} == dict.fromkeys(included, False)


def test_ac17_the_package_data_stays_in_the_context() -> None:
    """Every shipped manifest, string table and bundled profile survives the rules."""

    patterns = _patterns()
    shipped = [
        path.relative_to(ROOT).as_posix()
        for pattern in ("modules/*/module.yaml", "core/config_ui/strings/*.json", "core/config_ui/profiles/*")
        for path in sorted(ROOT.glob(pattern))
    ]
    assert len(shipped) > 4
    assert [path for path in shipped if _excluded(path, patterns)] == []


def test_ac17_the_instance_and_supervision_records_are_excluded_by_name_too() -> None:
    """Without the leading ``*`` the records still stay out of the context."""

    by_name = _patterns()[1:]
    for path in ("profile.instance.json", "core/x.instance.json", "profile.supervisor.json", "a/b.supervisor.json"):
        assert _excluded(path, by_name), path


# -- the compose file's static contract (R4, A1, A4, AC14) ------------------- #

COMPOSE = ROOT / "compose.yaml"
#: A1: the Git URL at ``main``, overridable by one environment variable.
BUILD_CONTEXT = "${TWITCH_IA_COMPAGNON_BUILD_CONTEXT:-https://github.com/chpomob/twitch-ia-compagnon.git#main}"


def _compose() -> dict[str, Any]:
    document = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


def _service() -> dict[str, Any]:
    (service,) = _compose()["services"].values()
    return service


def test_ac14_one_service_and_one_named_volume_mounted_at_data() -> None:
    document = _compose()
    assert list(document["services"]) == ["companion"]
    assert list(document["volumes"]) == ["companion-data"]
    assert _service()["volumes"] == ["companion-data:/data"]


def test_ac14_the_port_is_published_on_the_hosts_loopback() -> None:
    assert _service()["ports"] == ["127.0.0.1:8765:8765"]


def test_ac14_init_restart_and_the_image_name() -> None:
    service = _service()
    assert service["init"] is True
    assert service["restart"] == "unless-stopped"
    assert service["image"] == "twitch-ia-compagnon:local"


def test_ac14_the_healthcheck_runs_the_health_command_with_the_dockerfiles_timings() -> None:
    healthcheck = _service()["healthcheck"]
    assert healthcheck["test"] == ["CMD", "twitch-ia-compagnon-config-ui", "--health"]
    assert "--health" in healthcheck["test"]
    final = _stages()[-1]
    (dockerfile,) = _arguments(final, "HEALTHCHECK")
    options, _, _ = dockerfile.partition(" CMD ")
    flags = dict(option.lstrip("-").split("=", 1) for option in options.split())
    assert healthcheck["interval"] == flags["interval"] == "30s"
    assert healthcheck["start_period"] == flags["start-period"] == "90s"
    assert healthcheck["timeout"] == flags["timeout"]
    assert str(healthcheck["retries"]) == flags["retries"]


def test_ac21_the_trials_health_wait_outlasts_the_declared_start_period_and_retries() -> None:
    # The trial must not give up while Docker itself still calls it starting.
    timeout = _seconds(_service()["healthcheck"]["timeout"])
    assert _health_deadline() >= 90 + (30 + timeout) * 3


def test_ac14_the_build_context_is_a1s_git_url_overridable_by_one_variable() -> None:
    assert _service()["build"] == {"context": BUILD_CONTEXT}


def test_ac14_nothing_desktop_specific_and_no_other_file_required() -> None:
    text = COMPOSE.read_text(encoding="utf-8")
    for forbidden in ("host.docker.internal", "extensions", "x-docker-desktop"):
        assert forbidden not in text, forbidden

    def keys(node: object) -> set[str]:
        if isinstance(node, dict):
            return set(node).union(*(keys(value) for value in node.values()))
        if isinstance(node, list):
            return set().union(*(keys(value) for value in node))
        return set()

    found = keys(_compose())
    assert not found & {"extends", "include", "profiles"}
    assert not [key for key in found if str(key).startswith("x-")]


def test_ac14_the_optional_entries_are_comments_not_keys() -> None:
    """The credential and token-file blocks are offered, never active."""

    text = COMPOSE.read_text(encoding="utf-8")
    for offered in ("# env_file:", "# environment:", "# secrets:", "--token-file", "/run/secrets/companion_token"):
        assert offered in text, offered
    document, service = _compose(), _service()
    assert set(document) == {"services", "volumes"}
    assert not {"env_file", "environment", "secrets", "command"} & set(service)
    assert "--token-file" not in json.dumps(document)


# -- the CI workflow's static contract (R7, AC23's CI part) ------------------- #

WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
#: The ``tests`` job's steps as committed at the brief's HEAD (``ff4f523``),
#: copied here because reading them with ``git show`` would start a process.
TESTS_STEPS: list[dict[str, Any]] = [
    {"uses": "actions/checkout@v4", "with": {"fetch-depth": 0}},
    {
        "uses": "actions/setup-python@v5",
        "with": {
            "python-version": "${{ matrix.python-version }}",
            "cache": "pip",
            "cache-dependency-path": "pyproject.toml",
        },
    },
    {
        "name": "Install the project with its test extra",
        "run": 'python -m pip install --upgrade pip\npython -m pip install -e ".[test]"\n',
    },
    {"name": "Test suite", "run": "python -m pytest tests/ -q -p no:cacheprovider"},
    {
        "name": "Packaging and entry points",
        "run": (
            'python -c "import core.main, core.config_ui; print(\'imports ok\')"\n'
            "twitch-ia-compagnon --help > /dev/null\n"
            "twitch-ia-compagnon-config-ui --help > /dev/null\n"
            "python -m demo --no-colour > /dev/null\n"
            "python -m pip wheel . --no-deps --wheel-dir /tmp/wheel\n"
            "ls -1 /tmp/wheel\n"
        ),
    },
]
#: R7's trial invocation, its variables supplied by the step's ``env:``.
TRIAL_COMMAND = "python -m pytest tests/test_container.py -q -p no:cacheprovider -rs"
#: Nothing is published: no step may push, log in, tag or release.
PUBLISHING = ("docker push", "docker login", "docker tag", "git tag", "gh release")


def _jobs() -> dict[str, Any]:
    document = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document["jobs"]


def test_ac23_the_workflow_gains_exactly_the_container_trial_job() -> None:
    assert list(_jobs()) == ["tests", "container-trial"]


def test_ac23_the_tests_jobs_steps_are_unchanged() -> None:
    tests = _jobs()["tests"]
    assert tests["steps"] == TESTS_STEPS
    assert tests["strategy"]["matrix"]["python-version"] == ["3.10", "3.11", "3.12", "3.13"]


def test_ac23_the_container_trial_job_runs_r7s_invocation() -> None:
    job = _jobs()["container-trial"]
    assert job["runs-on"] == "ubuntu-latest"
    assert 0 < job["timeout-minutes"] <= 60
    assert "permissions" not in job  # the workflow's read-only permissions apply
    steps = job["steps"]
    assert [step.get("uses") for step in steps[:2]] == ["actions/checkout@v4", "actions/setup-python@v5"]
    assert steps[1]["with"]["python-version"] == "3.12"
    assert 'python -m pip install -e ".[test]"' in steps[2]["run"]
    (trial,) = [step for step in steps if step.get("name") == "Container trial"]
    assert trial is steps[-1]
    assert trial["run"].strip() == TRIAL_COMMAND
    assert trial["env"] == {CONTAINER_TRIAL_VARIABLE: "1", BUILD_CONTEXT_VARIABLE: "."}


def test_ac23_no_step_publishes_anything() -> None:
    for name, job in _jobs().items():
        for step in job["steps"]:
            text = json.dumps(step)
            assert [word for word in PUBLISHING if word in text] == [], (name, step)
            uses = step.get("uses", "")
            assert "release" not in uses and not uses.startswith("docker/"), (name, uses)


# -- the self-hosting guide pair (R8, AC24, AC25 link parts, AC18 guide part) - #

DOCS = ROOT / "docs"
GUIDE = DOCS / "self-hosting.md"
GUIDE_FR = DOCS / "self-hosting.fr.md"
#: R1's options, each named by the guide.
R1_OPTIONS = (
    "--no-browser",
    "--token-file",
    "--start-runtime",
    "--health",
    "--allow-non-loopback",
    "--host",
    "--allowed-host",
)
#: A6's five health states.
HEALTH_STATES = ("ready", "degraded", "starting", "idle", "unhealthy")
#: R8's limits of container mode, each spelled in the English guide.
LIMITS = ("no desktop session", "no browser opener", "no launcher", "no microphone", "no native bundle")
#: AC18's guide part: the precedence, stated in each language.
PRECEDENCE = {
    GUIDE: "a stored credential overrides an inherited variable of the same name",
    GUIDE_FR: "un identifiant enregistré l'emporte sur une variable héritée du même nom",
}
_GUIDE_SECTION = re.compile(r"^## (?P<title>.+)$", re.MULTILINE)
_GUIDE_BLOCK = re.compile(r"^```(?P<language>[a-z]*)\n(?P<body>.*?)^```$", re.MULTILINE | re.DOTALL)
_SIZE = re.compile(r"\b\d+\.\d MB\b")


def _guide_sections(path: Path) -> dict[str, str]:
    """Each ``##`` section's text, keyed by its title without its number."""

    text = path.read_text(encoding="utf-8")
    matches = list(_GUIDE_SECTION.finditer(text))
    sections = {}
    for match, following in zip(matches, [*matches[1:], None]):
        end = following.start() if following is not None else len(text)
        title = re.sub(r"^\d+\. ", "", match.group("title"))
        sections[title] = text[match.end() : end]
    return sections


def _volume_file_names() -> list[str]:
    """AC7's files for the image's default profile, named as the code derives them."""

    profile = Path(DATA_DIRECTORY) / locations.PROFILE_FILE_NAME
    overlay = implicit_overlay_path(profile)
    assert overlay is not None
    return [
        profile.name,
        overlay.name,
        default_status_path(profile).name,
        default_feed_path(profile).name,
        wizard.state_path(profile).name,
        locations.credential_file_name(profile),
        profile.name + config_ui.INSTANCE_SUFFIX,
        profile.name + config_ui.SUPERVISION_SUFFIX,
    ]


def test_ac24_the_guide_names_every_volume_file_under_the_data_directory() -> None:
    text = GUIDE.read_text(encoding="utf-8")
    names = _volume_file_names()
    assert len(set(names)) == 8
    for name in names:
        assert f"`{DATA_DIRECTORY}/{name}`" in text, name


def test_ac24_the_guide_names_every_r1_option_the_states_the_user_and_the_limits() -> None:
    text = GUIDE.read_text(encoding="utf-8")
    for option in R1_OPTIONS:
        assert f"`{option}" in text, option
    for state in HEALTH_STATES:
        assert f"`{state}`" in text, state
    assert "`10001`" in text
    limits = _guide_sections(GUIDE)["Limits"]
    for limit in LIMITS:
        assert limit in limits, limit


def test_ac24_the_guide_prints_every_health_line_the_command_prints() -> None:
    lines = [
        config_ui.HEALTH_NO_INSTANCE,
        config_ui.HEALTH_NOT_ANSWERING,
        config_ui.HEALTH_TOKEN_REFUSED,
        config_ui.HEALTH_ANOTHER_INSTANCE,
        config_ui.HEALTH_SUPERVISION_UNUSABLE,
        config_ui.HEALTH_WIZARD_UNFINISHED,
        config_ui.HEALTH_NOT_STARTED,
        config_ui.HEALTH_STOPPED,
        config_ui.HEALTH_RUNTIME_EXITED,
        config_ui.HEALTH_READY,
        config_ui.HEALTH_STARTING,
        config_ui.HEALTH_NOT_READY,
    ]
    table = _guide_sections(GUIDE)["Healthcheck states"]
    for line in lines:
        assert f"| `{line}` |" in table, line
    assert f"| `{config_ui.HEALTH_DEGRADED_PREFIX}<names>` |" in table


@pytest.mark.parametrize("guide", [GUIDE, GUIDE_FR], ids=lambda path: path.name)
def test_ac18_each_guide_states_the_precedence(guide: Path) -> None:
    assert PRECEDENCE[guide] in guide.read_text(encoding="utf-8")


@pytest.mark.parametrize("guide", [GUIDE, GUIDE_FR], ids=lambda path: path.name)
def test_ac15_the_quick_start_is_at_most_three_commands_and_no_clone_or_build(guide: Path) -> None:
    quick_start = next(iter(_guide_sections(guide).values()))
    block = _GUIDE_BLOCK.search(quick_start)
    assert block is not None
    commands = [line.strip() for line in block.group("body").splitlines() if line.strip()]
    assert 0 < len(commands) <= 3
    assert commands[0] == (
        "curl -fsSLO https://raw.githubusercontent.com/chpomob/twitch-ia-compagnon/main/compose.yaml"
    )
    assert "docker compose up -d" in commands
    for command in commands:
        assert "git clone" not in command and "docker build" not in command, command


@pytest.mark.parametrize("guide", [GUIDE, GUIDE_FR], ids=lambda path: path.name)
def test_ac13_each_guide_states_one_size_in_mb_to_one_decimal(guide: Path) -> None:
    (size_section,) = [text for title, text in _guide_sections(guide).items() if "MB" in text]
    assert len(_SIZE.findall(size_section)) == 1


#: R8's trial record (``docs/campaigns/phase10/trial-record.md``).
TRIAL_RECORD = DOCS / "campaigns" / "phase10" / "trial-record.md"
#: The fields the spec's target list and P12 name for it, in its order.
TRIAL_RECORD_FIELDS = (
    "Who",
    "Host",
    "Docker Engine version",
    "Docker Compose version",
    "Commit",
    "Commands",
    "Trial start",
    "Trial end",
    "Image size (bytes)",
    "Health states observed",
    "First run",
    "Restart over the restored volume",
    "Trial nodes",
    "Outcome",
    "Limits",
)
_RECORD_LINE = re.compile(r"^- (?P<label>[^:\n]+):(?P<value>.*)$")
_RECORD_NON_VALUES = {"", "-", "?", "...", "…", "n/a", "tbd", "todo", "unknown"}


def _record_fields(text: str) -> dict[str, list[str]]:
    """Each top-level ``- Label: value`` of the ``## Record`` section; a
    value is its line's text plus the indented lines under it."""

    _, heading, record = text.partition("\n## Record\n")
    assert heading, "the trial record has no ## Record section"
    fields: dict[str, list[str]] = {}
    label = None
    for line in record.splitlines():
        if line.startswith("## "):
            break
        match = _RECORD_LINE.match(line)
        if match is not None:
            label = match["label"]
            fields.setdefault(label, []).append(match["value"].strip())
        elif label is not None and line.startswith((" ", "\t")) and line.strip():
            fields[label][-1] = (fields[label][-1] + "\n" + line.strip()).strip()
    return fields


def test_ac25_the_trial_record_fills_every_field_once() -> None:
    fields = _record_fields(TRIAL_RECORD.read_text(encoding="utf-8"))
    assert list(fields) == list(TRIAL_RECORD_FIELDS)
    for label in TRIAL_RECORD_FIELDS:
        (value,) = fields[label]
        assert value.strip("`").strip().lower() not in _RECORD_NON_VALUES, label
    assert re.fullmatch(r"[0-9a-f]{40}", fields["Commit"][0].strip("`")), fields["Commit"]
    for label in ("Trial start", "Trial end"):
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", fields[label][0]), fields[label]
    for node in TRIAL_NODES:
        assert node in fields["Trial nodes"][0], node


@pytest.mark.parametrize("guide", [GUIDE, GUIDE_FR], ids=lambda path: path.name)
def test_ac13_the_guides_size_agrees_with_the_records_bytes(guide: Path) -> None:
    (recorded,) = _record_fields(TRIAL_RECORD.read_text(encoding="utf-8"))["Image size (bytes)"]
    size = int(recorded.strip("`"))
    (size_section,) = [text for title, text in _guide_sections(guide).items() if "MB" in text]
    (stated,) = _SIZE.findall(size_section)
    megabytes = float(stated.removesuffix(" MB"))
    assert megabytes > 0
    assert abs(megabytes * 1_000_000 - size) <= 0.05 * size, (stated, size)


def test_ac25_the_documentation_index_links_both_guides() -> None:
    text = (DOCS / "README.md").read_text(encoding="utf-8")
    assert "](self-hosting.md)" in text
    assert "](self-hosting.fr.md)" in text


@pytest.mark.parametrize(
    ("installation", "guide"),
    [("installation.md", "self-hosting.md"), ("installation.fr.md", "self-hosting.fr.md")],
)
def test_ac25_each_installation_guide_links_the_guide_of_its_language(
    installation: str, guide: str
) -> None:
    assert f"]({guide})" in (DOCS / installation).read_text(encoding="utf-8")


# -- the container trial (R7, AC7–AC9, AC12, AC13, AC16, AC17, AC21–AC23) ---- #

CONTAINER_TRIAL_VARIABLE = "TWITCH_IA_COMPAGNON_CONTAINER_TRIAL"
BUILD_CONTEXT_VARIABLE = "TWITCH_IA_COMPAGNON_BUILD_CONTEXT"

#: R7: the trial nodes, exactly these five.
TRIAL_NODES = (
    "test_trial_container_image",
    "test_trial_container_volume",
    "test_trial_container_secrets",
    "test_trial_container_health",
    "test_trial_container_profiles",
)

SERVICE = "companion"
#: The per-user directory inside the image (A3).
DATA_DIRECTORY = "/data/twitch-ia-compagnon"
#: Bounds of every Docker call: the build, anything else.
BUILD_SECONDS = 1800
COMMAND_SECONDS = 180
#: How often the trial reads the health status while it waits.
HEALTH_POLL_SECONDS = 5
#: The healthcheck's interval and timeout (the Dockerfile's, AC21's unit).
HEALTH_INTERVAL_SECONDS = 30
HEALTH_TIMEOUT_SECONDS = 10
#: Where the trial writes its measurement (AC13): a run artefact, not a source.
TRIAL_ARTEFACT = ROOT / "docs" / "runs" / "phase10-container-trial.json"
#: AC12: ``pyproject.toml``'s dependencies at the brief's HEAD.
DECLARED_DEPENDENCIES = ["aiohttp>=3.9,<4", "PyYAML>=6,<7"]
#: AC17 and finding P2: file names no layer of the image may hold.
FORBIDDEN_NAMES = [
    "profile.yaml",
    "*.instance.json",
    "*.supervisor.json",
    "*.credentials.json",
    "*.local.yaml",
    ".env*",
]
#: Finding P2: decoys written into the build context, which must stay out.
DECOYS = ["profile.yaml", "core/profile.yaml", "x.instance.json"]

#: The broadcaster's channel the fake platform answer names (fixture).
TRIAL_USER_ID = "41245072"
TRIAL_IDENTITIES = {"broadcaster_id": TRIAL_USER_ID, "bot_user_id": "90210417"}
_TRIAL_MODEL = {"model_endpoint": "https://model.invalid/v1", "model_name": "trial-model"}
#: AC22: the streamer's answers per profile, as phase 9's fixtures give them.
TRIAL_PARAMETERS: dict[str, dict[str, Any]] = {
    "chat_qa": {"companion_name": "Nova", **_TRIAL_MODEL},
    "channel_points": {"reward_id": "5f1c0d7e-reward-fixture", **_TRIAL_MODEL},
    "spoken_keyword": {
        "wake_word": "hey nova",
        "recorder": ["recorder", "--wav", "-"],
        "detector": {"kind": "command", "argv": ["detector", "--phrase", "hey nova"]},
        "transcription_endpoint": "https://transcription.invalid/v1",
        "transcription_model": "trial-transcriber",
        **_TRIAL_MODEL,
    },
    "silent": {},
}
#: AC22: each profile's declared dry-run outcome.
EXPECTED_OUTCOMES = {
    "chat_qa": "completed",
    "channel_points": "completed",
    "spoken_keyword": "completed",
    "silent": "not_triggered",
}


def _sentinel(name: str) -> str:
    """AC16: one distinct 24-character value per credential name."""

    return ("sentinel" + hashlib.sha256(name.encode("utf-8")).hexdigest())[:24]


def _sentinels() -> dict[str, str]:
    return {name: _sentinel(name) for name in sorted(_credential_names())}


#: Run inside the container: the access URL's exchange, redirects not followed.
ACCESS_PROOF = """
import http.client, json
connection = http.client.HTTPConnection("127.0.0.1", ARGS["port"], timeout=10)
connection.request("GET", "/?token=" + ARGS["token"])
response = connection.getresponse()
print(json.dumps({"status": response.status, "location": response.getheader("Location")}))
"""

#: Run inside the container: screens 1–5 of the wizard over the default
#: location, through the image's own ``Wizard`` (a ``ConfigUI`` built like
#: the serving one); only the platform's validation answer is faked, so no
#: real platform credential is needed, and screen 5 is the production dry run.
WIZARD_PROOF = """
import json
from core import config_ui
from core.config_ui import wizard

profile = {item.id: item for item in wizard.load_profiles()}[ARGS["profile"]]

async def platform(method, url, *, headers, body, timeout):
    answer = {
        "client_id": ARGS["credentials"]["TWITCH_CLIENT_ID"],
        "login": "trialchannel",
        "user_id": ARGS["user_id"],
        "scopes": list(profile.scopes),
        "expires_in": 3600,
    }
    return wizard.HttpAnswer(200, json.dumps(answer).encode("utf-8"))

settings = config_ui.UISettings.from_argv([])
ui = config_ui.ConfigUI(settings, wizard_seams=wizard.WizardSeams(platform_transport=platform))
screens = [
    [("screen", "1"), ("profile", profile.id)],
    [("screen", "2")] + [
        (wizard.CREDENTIAL_FIELD + item.name, ARGS["credentials"][item.name])
        for item in profile.credentials
        if item.screen == "platform"
    ],
    [("screen", "3")],
    [("screen", "4")],
    [("screen", "5")],
]
for fields in screens:
    ui.wizard.post(fields, "")
print(json.dumps({"passed": list(ui.wizard.passed_screens()), "completed": ui.wizard.state.completed}))
"""

#: Run inside the container: the files written since the container started
#: (temporary and kernel trees excluded), every path whose stat failed (a
#: finding of the scan, never dropped), and the names the product's own
#: settings give the files of the default location (AC7).
FILES_PROOF = """
import json, os
from core import config_ui

skipped = {"/proc", "/sys", "/dev", "/run", "/tmp"}
# The files Docker mounts into every container and rewrites at each start
# (/etc/hostname, /etc/hosts, /etc/resolv.conf): mount points, not the image's
# filesystem, and not writable by uid 10001.
with open("/proc/self/mountinfo", encoding="utf-8") as handle:
    mounted = {line.split()[4] for line in handle}
written, unreadable = [], []
for root, directories, files in os.walk("/"):
    directories[:] = [name for name in directories if os.path.join(root, name) not in skipped]
    for name in files:
        path = os.path.join(root, name)
        if path in mounted:
            continue
        try:
            if os.lstat(path).st_mtime > ARGS["since"]:
                written.append(path)
        except OSError as error:
            unreadable.append({"path": path, "error": f"{type(error).__name__}: {error.strerror or error}"})
settings = config_ui.UISettings.from_argv([])
named = {
    "profile": settings.base_path,
    "overlay": settings.overlay_path,
    "status": settings.status_path,
    "feed": settings.feed_path,
    "wizard": settings.wizard_path,
    "credentials": settings.credential_path,
    "instance": settings.instance_path,
    "supervision": settings.supervision_path,
}
print(json.dumps({
    "written": sorted(written),
    "unreadable": unreadable,
    "named": {role: None if path is None else str(path) for role, path in named.items()},
    "present": sorted(os.listdir(ARGS["directory"])),
}))
"""

#: Run inside the container: one record's content, base64, or null.
READ_PROOF = """
import base64, json, os
path = ARGS["path"]
print(json.dumps({"content": base64.b64encode(open(path, "rb").read()).decode() if os.path.exists(path) else None}))
"""

#: Run inside the container: SIGKILL one pid (the runtime, then the UI).
KILL_PROOF = """
import json, os, signal
os.kill(ARGS["pid"], signal.SIGKILL)
print(json.dumps({"killed": ARGS["pid"]}))
"""

#: The fake Twitch the runtime nodes (AC9, AC16, AC21) start beside the
#: companion. Their volume holds a completed wizard whose credentials are the
#: trial's sentinels, so on the restart ``--start-runtime`` launches the
#: runtime and the twitch module validates the sentinel token: against the
#: real ``id.twitch.tv`` that is a 401, ``prepare`` fails and the runtime
#: exits — a rejected token stops startup by design, and the health line
#: ``unhealthy: runtime exited`` is then the truth. The control run of the
#: shipped compose file over an empty volume never launches a runtime, which
#: is why it is healthy. So the platform's edges are faked here, as the
#: wizard proof fakes its validation: a service on the project's network
#: answers for Twitch's host names over TLS signed by its own authority, and
#: the companion trusts that authority through ``SSL_CERT_FILE``; the image's
#: code, its module and the health contract are untouched.
FAKE_TWITCH = """
import asyncio, json, os, ssl, subprocess
from aiohttp import web

directory = ARGS["directory"]
authority_key, authority = os.path.join(directory, "authority.key"), os.path.join(directory, "authority.pem")
key, certificate = os.path.join(directory, "server.key"), os.path.join(directory, "server.pem")


def openssl(*arguments):
    subprocess.run(["openssl", *arguments], check=True, capture_output=True)


openssl("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2", "-subj", "/CN=tiac trial authority",
        "-keyout", authority_key, "-out", authority)
openssl("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2", "-subj", "/CN=" + ARGS["hosts"][0],
        "-CA", authority, "-CAkey", authority_key,
        "-addext", "subjectAltName=" + ",".join("DNS:" + host for host in ARGS["hosts"]),
        "-keyout", key, "-out", certificate)
os.chmod(authority, 0o644)


async def validate(request):
    return web.json_response(ARGS["validation"])


async def eventsub(request):
    websocket = web.WebSocketResponse()
    await websocket.prepare(request)
    welcome = {"metadata": {"message_type": "session_welcome"}, "payload": {"session": {"id": "tiac-trial-session"}}}
    await websocket.send_str(json.dumps(welcome))
    async for _ in websocket:
        pass
    return websocket


async def subscribe(request):
    return web.json_response({"data": [{"id": "tiac-trial-subscription", "status": "enabled"}]}, status=202)


async def main():
    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    context.load_cert_chain(certificate, key)
    application = web.Application()
    application.router.add_get(ARGS["paths"]["validate"], validate)
    application.router.add_get(ARGS["paths"]["eventsub"], eventsub)
    application.router.add_post(ARGS["paths"]["subscriptions"], subscribe)
    runner = web.AppRunner(application, access_log=None)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", 443, ssl_context=context).start()
    open(os.path.join(directory, "ready"), "w").close()
    await asyncio.Event().wait()


asyncio.run(main())
"""
#: Where the fake's authority is shared with the companion (read-only there).
FAKE_TWITCH_DIRECTORY = "/run/tiac-trial-twitch"

#: Run inside an image container as root: the files no layer may hold, the
#: repository's leftovers, ``/data`` and the shipped profiles (AC12, AC17, P2).
IMAGE_FILES_PROOF = """
import fnmatch, json, os
from importlib import metadata

skipped = {"/proc", "/sys", "/dev"}
forbidden, pyprojects, tests = [], [], []
for root, directories, files in os.walk("/"):
    directories[:] = [name for name in directories if os.path.join(root, name) not in skipped]
    for name in files:
        path = os.path.join(root, name)
        if any(fnmatch.fnmatchcase(name, pattern) for pattern in ARGS["forbidden"]):
            forbidden.append(path)
        if name == "pyproject.toml":
            pyprojects.append(path)
        if name in ("test_container.py", "conftest.py"):
            tests.append(path)
try:
    distribution = [str(item) for item in metadata.files("twitch-ia-compagnon") or ()]
    profiles_directory = None
    from core.config_ui import wizard
    profiles_directory = str(wizard.PROFILES_DIRECTORY)
    profiles = sorted(os.listdir(profiles_directory))
except (metadata.PackageNotFoundError, ImportError):
    distribution, profiles = None, []
print(json.dumps({
    "forbidden": sorted(forbidden),
    "pyprojects": sorted(pyprojects),
    "tests": sorted(tests),
    "git": os.path.exists("/.git"),
    "data": sorted(os.listdir("/data")) if os.path.isdir("/data") else None,
    "distribution_tests": None if distribution is None else [item for item in distribution if item.startswith("tests/")],
    "profiles": profiles,
}))
"""

#: Run inside the image: the uid, and the requirement closure of the two
#: declared dependencies read from the installed metadata (AC12).
CLOSURE_PROOF = """
import json, os
from importlib import metadata
from pip._vendor.packaging.requirements import Requirement
from pip._vendor.packaging.utils import canonicalize_name

installed = {canonicalize_name(item.metadata["Name"]): item for item in metadata.distributions()}
closure, pending = set(), ["aiohttp", "PyYAML"]
while pending:
    name = canonicalize_name(pending.pop())
    if name in closure:
        continue
    closure.add(name)
    for text in installed[name].requires or ():
        requirement = Requirement(text)
        if requirement.marker is None or requirement.marker.evaluate({"extra": ""}):
            pending.append(requirement.name)
print(json.dumps({"uid": os.getuid(), "closure": sorted(closure)}))
"""

#: Run inside the container (AC22): the shipped profiles' digests, then each
#: profile rendered by the wizard's generator with the fixture parameters,
#: checked by ``twitch-ia-compagnon --check-config`` and dry-run with a fake
#: model transport; every socket connection is refused and counted.
PROFILES_PROOF = """
import asyncio, hashlib, json, os, socket, subprocess, tempfile
from pathlib import Path
from core import main as application
from core.contracts import PROBE_TOOL
from core.config_ui import wizard

connections = []

def refused(self, address, *args):
    connections.append(repr(address))
    raise OSError("the trial refuses every connection")

socket.socket.connect = refused
socket.socket.connect_ex = refused


class Answer:
    def __init__(self, body):
        self.status = 200
        self.body = body

    async def json(self):
        return self.body

    def release(self):
        pass


class Model:
    async def post(self, url, **kwargs):
        body = kwargs.get("json")
        choice = body.get("tool_choice") if isinstance(body, dict) else None
        function = choice.get("function") if isinstance(choice, dict) else None
        if isinstance(function, dict) and function.get("name") == PROBE_TOOL:
            call = {"id": "call-1", "type": "function", "function": {"name": PROBE_TOOL, "arguments": json.dumps({"ok": True})}}
            return Answer({"choices": [{"message": {"content": None, "tool_calls": [call]}, "finish_reason": "tool_calls"}]})
        return Answer({"choices": [{"message": {"content": "bonjour"}}]})

    async def close(self):
        pass


digests = {
    path.name: hashlib.sha256(path.read_bytes()).hexdigest()
    for path in sorted(wizard.PROFILES_DIRECTORY.glob("*.json"))
}
environ = {**os.environ, **ARGS["credentials"]}
work = Path(tempfile.mkdtemp())
results = {}
for profile in wizard.load_profiles():
    parameters = ARGS["parameters"][profile.id]
    document = wizard.generate_profile(profile, parameters, ARGS["identities"])
    path = work / (profile.id + ".yaml")
    path.write_text(wizard.render_profile_yaml(document), encoding="utf-8")
    check = subprocess.run(
        ["twitch-ia-compagnon", "--config", str(path), "--check-config"],
        env=environ, capture_output=True, text=True, timeout=120,
    )
    event = wizard.dry_run_event(profile, document, parameters)
    record = asyncio.run(application.dry_run(path, event, environ=environ, model_transport=Model))
    results[profile.id] = {
        "check": check.returncode,
        "diagnostics": check.stderr[-2000:],
        "outcome": record.outcome,
        "origin": record.origin,
    }
print(json.dumps({"uid": os.getuid(), "digests": digests, "results": results, "connections": connections}))
"""


def _docker() -> str:
    """The ``docker`` executable; missing, a failure, never a skip (AC23)."""

    found = shutil.which("docker")
    if found is None:
        pytest.fail("docker executable not found")
    return found


def _command(
    argv: list[str],
    *,
    cause: str,
    timeout: float = COMMAND_SECONDS,
    stdin: str | None = None,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """One bounded command; a timeout or (with *check*) a non-zero status
    is a failure naming *cause* and the command's own error output."""

    try:
        result = subprocess.run(
            argv, input=stdin, capture_output=True, text=True, timeout=timeout, env=env
        )
    except subprocess.TimeoutExpired:
        pytest.fail(f"{cause}: timed out after {timeout:g} s")
    if check and result.returncode != 0:
        pytest.fail(f"{cause}: exit {result.returncode}: {result.stderr.strip()[-3000:]}")
    return result


def _daemon(docker: str) -> None:
    """An unreachable daemon or a missing Compose plugin is a failure (AC23)."""

    _command([docker, "info", "--format", "{{.ServerVersion}}"], cause="docker daemon unreachable", timeout=60)
    _command([docker, "compose", "version"], cause="docker compose plugin unavailable", timeout=60)


def _build_context() -> str:
    """``TWITCH_IA_COMPAGNON_BUILD_CONTEXT``, a local path made absolute."""

    value = os.environ.get(BUILD_CONTEXT_VARIABLE, "").strip()
    if not value:
        pytest.fail(f"{BUILD_CONTEXT_VARIABLE} is unset; the trial builds the image from it")
    if "://" in value or value.startswith("git@"):
        return value
    return str(Path(value).resolve())


def _image() -> str:
    return str(_service()["image"])


def _seconds(duration: str) -> int:
    """A compose duration of whole seconds (``"90s"``) as an int."""

    match = re.fullmatch(r"(\d+)s", str(duration))
    assert match, duration
    return int(match.group(1))


def _health_deadline() -> int:
    """How long :meth:`_Project.up` waits: the compose file's own
    ``start_period + (interval + timeout) × (retries + 1)``. Docker schedules
    each interval after the previous probe ends, so every counted run adds its
    own timeout; the one extra run covers a probe begun inside the start
    period, whose failure does not count."""

    healthcheck = _service()["healthcheck"]
    probe = _seconds(healthcheck["interval"]) + _seconds(healthcheck["timeout"])
    return _seconds(healthcheck["start_period"]) + probe * (int(healthcheck["retries"]) + 1)


def _proof(script: str, arguments: dict[str, Any]) -> str:
    """*script* with its arguments bound: piped to an interpreter, never baked."""

    return f"import json\nARGS = json.loads({json.dumps(json.dumps(arguments))})\n{script}"


def _answered(result: subprocess.CompletedProcess[str], cause: str) -> dict[str, Any]:
    lines = result.stdout.strip().splitlines()
    if not lines:
        pytest.fail(f"{cause}: no answer: {result.stderr.strip()[-3000:]}")
    return json.loads(lines[-1])


class _Project:
    """One trial's compose project: its own name, the shipped compose file
    (plus, per node, overrides: AC16's sentinel environment, AC21's restart
    policy, the runtime nodes' :data:`FAKE_TWITCH`) and the build context
    from the environment."""

    def __init__(self, docker: str, node: str, files: list[Path]) -> None:
        self.docker = docker
        self.name = f"tiac-{node.removeprefix('test_trial_container_')}-{uuid.uuid4().hex[:8]}"
        self.files = files
        self.env = {**os.environ, BUILD_CONTEXT_VARIABLE: _build_context()}

    def compose(self, *arguments: str, cause: str, timeout: float = COMMAND_SECONDS, stdin: str | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
        files = [word for path in self.files for word in ("-f", str(path))]
        return _command(
            [self.docker, "compose", "-p", self.name, *files, *arguments],
            cause=cause, timeout=timeout, stdin=stdin, env=self.env, check=check,
        )

    def build(self) -> None:
        self.compose("build", cause="docker build failed", timeout=BUILD_SECONDS)

    def up(self) -> None:
        """``up -d``, then the health status polled until Docker settles it:
        ``healthy`` passes; ``unhealthy``, an exit or the deadline (never
        shorter than the compose file's ``start_period + (interval + timeout) ×
        retries``)
        fails with the container's last health log."""

        self.compose("up", "-d", cause="docker compose up -d failed")
        deadline = time.monotonic() + _health_deadline()
        while True:
            state = self.inspect()["State"]
            health = state.get("Health") or {}
            status = health.get("Status", "")
            if status == "healthy":
                return
            if status == "unhealthy" or not state.get("Running") or time.monotonic() >= deadline:
                outputs = [entry["Output"].strip() for entry in health.get("Log") or ()]
                pytest.fail(
                    f"the service is {status or state.get('Status')!r}, not healthy"
                    f" (exit code {state.get('ExitCode')}); last health log: {outputs[-3:]}"
                )
            # The wait between reads: ``docker events --until``, as everywhere here.
            _events(self, "health_status", time.time(), HEALTH_POLL_SECONDS)

    def container(self) -> str:
        found = self.compose("ps", "-a", "-q", SERVICE, cause="docker compose ps failed").stdout.split()
        if len(found) != 1:
            pytest.fail(f"docker compose ps: expected 1 container, found {len(found)}")
        return found[0]

    def python(self, script: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """*script* piped to the container's own interpreter, as its user."""

        return self.python_output(script, arguments)[0]

    def python_output(self, script: str, arguments: dict[str, Any]) -> tuple[dict[str, Any], str]:
        """:meth:`python`'s answer and the exec's combined output: what the
        proof printed never reaches ``docker compose logs``."""

        result = self.compose(
            "exec", "-T", SERVICE, "python", "-",
            cause="the proof code failed in the container", stdin=_proof(script, arguments),
        )
        return _answered(result, "the proof code"), result.stdout + result.stderr

    def logs(self) -> str:
        return self.compose("logs", "--no-color", SERVICE, cause="docker compose logs failed").stdout

    def inspect(self) -> dict[str, Any]:
        result = _command([self.docker, "inspect", self.container()], cause="docker inspect failed")
        return json.loads(result.stdout)[0]

    def health(self) -> tuple[str, list[str]]:
        """The health status and the health log's outputs, oldest first."""

        state = self.inspect()["State"].get("Health") or {}
        return state.get("Status", ""), [entry["Output"].strip() for entry in state.get("Log") or ()]

    def kill(self) -> None:
        _command([self.docker, "kill", self.container()], cause="docker kill failed")

    def down(self) -> None:
        self.compose(
            "down", "-v", "--remove-orphans", cause="docker compose down failed", check=False,
            timeout=COMMAND_SECONDS,
        )


@contextlib.contextmanager
def _project(docker: str, node: str, files: list[Path] | None = None) -> Any:
    project = _Project(docker, node, files or [COMPOSE])
    try:
        project.build()
        yield project
    finally:
        project.down()


def _events(project: _Project, event: str, since: float, seconds: float) -> list[str]:
    """The container's *event* actions from *since* to ``now + seconds``:
    ``docker events --until`` is the wait, the subprocess timeout its guard."""

    until = time.time() + seconds
    result = _command(
        [
            project.docker, "events",
            "--filter", f"container={project.container()}",
            "--filter", f"event={event}",
            "--since", f"{since:.3f}",
            "--until", f"{until:.3f}",
            "--format", "{{.Action}}",
        ],
        cause="docker events failed", timeout=seconds + COMMAND_SECONDS,
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _tokens(logs: str) -> list[str]:
    return re.findall(r"\?token=([A-Za-z0-9_-]+)", logs)


def _landing(project: _Project) -> str:
    """Where the latest access URL of the log lands, from inside the container."""

    tokens = _tokens(project.logs())
    if not tokens:
        pytest.fail("the container log holds no access URL")
    answer = project.python(ACCESS_PROOF, {"port": config_ui.DEFAULT_PORT, "token": tokens[-1]})
    assert answer["status"] in (302, 303), answer["status"]
    return re.sub(r"^https?://[^/]+", "", answer["location"] or "")


def _fake_twitch(root: Path, credentials: dict[str, str]) -> Path:
    """A compose override adding :data:`FAKE_TWITCH` as a ``twitch`` service
    the companion waits for; the override's path. The fake answers the
    validation the wizard proof's platform answers, at the module's own URLs."""

    from urllib.parse import urlsplit

    from modules import twitch

    urls = {
        "validate": twitch.TOKEN_VALIDATION_URL,
        "eventsub": twitch.EVENTSUB_URL,
        "subscriptions": twitch.EVENTSUB_SUBSCRIPTIONS_URL,
    }
    hosts = sorted({urlsplit(url).hostname for url in urls.values()})
    profile = {item.id: item for item in wizard.load_profiles()}["silent"]
    validation = {
        "client_id": credentials["TWITCH_CLIENT_ID"],
        "login": "trialchannel",
        "user_id": TRIAL_USER_ID,
        "scopes": list(profile.scopes),
        "expires_in": 86400,
    }
    script = _proof(
        FAKE_TWITCH,
        {
            "directory": FAKE_TWITCH_DIRECTORY,
            "hosts": hosts,
            "paths": {role: urlsplit(url).path for role, url in urls.items()},
            "validation": validation,
        },
    )
    services = {
        SERVICE: {
            "depends_on": {"twitch": {"condition": "service_healthy"}},
            "environment": {"SSL_CERT_FILE": f"{FAKE_TWITCH_DIRECTORY}/authority.pem"},
            "volumes": [f"tiac-trial-twitch:{FAKE_TWITCH_DIRECTORY}:ro"],
        },
        "twitch": {
            "image": _image(),
            "user": "0",
            "command": ["python", "-c", script],
            "volumes": [f"tiac-trial-twitch:{FAKE_TWITCH_DIRECTORY}"],
            "networks": {"default": {"aliases": hosts}},
            "healthcheck": {
                "test": ["CMD", "test", "-f", f"{FAKE_TWITCH_DIRECTORY}/ready"],
                "interval": "1s",
                "timeout": "5s",
                "retries": 120,
            },
        },
    }
    override = root / "compose.twitch.yaml"
    override.write_text(
        yaml.safe_dump({"services": services, "volumes": {"tiac-trial-twitch": {}}}), encoding="utf-8"
    )
    return override


def _complete_wizard(project: _Project, credentials: dict[str, str]) -> str:
    """Screens 1–5 (screen 5 the dry run); the proof's combined output."""

    answer, output = project.python_output(
        WIZARD_PROOF, {"profile": "silent", "credentials": credentials, "user_id": TRIAL_USER_ID}
    )
    assert answer == {"passed": [1, 2, 3, 4, 5], "completed": True}, answer
    return output


def _started_at(project: _Project) -> float:
    started = project.inspect()["State"]["StartedAt"]
    whole, _, fraction = started.rstrip("Z").partition(".")
    epoch = datetime.fromisoformat(whole).replace(tzinfo=timezone.utc).timestamp()
    return epoch + float(f"0.{fraction or '0'}")


def _record(project: _Project, path: str) -> bytes | None:
    content = project.python(READ_PROOF, {"path": path})["content"]
    return None if content is None else base64.b64decode(content)


def _runtime_reports(project: _Project, since: float) -> str:
    """Wait, through the healthcheck's own runs, for ``ready`` or ``degraded``."""

    def last() -> str:
        outputs = project.health()[1]
        return outputs[-1] if outputs else ""

    if not last().startswith(("ready", "degraded")):
        _events(project, "exec_die", since, 3 * HEALTH_INTERVAL_SECONDS + HEALTH_TIMEOUT_SECONDS)
    return last()


def _git_files() -> list[str]:
    result = _command(["git", "-C", str(ROOT), "ls-files", "-z"], cause="git ls-files failed")
    return [name for name in result.stdout.split("\0") if name]


def _decoy_context(root: Path) -> Path:
    """Finding P2: the checkout's tracked files plus the decoys, in *root*."""

    for name in _git_files():
        source = ROOT / name
        if source.is_file():
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    for name in DECOYS:
        (root / name).write_text("decoy: never in the image\n", encoding="utf-8")
    return root


def _image_files(docker: str, image: str) -> dict[str, Any]:
    result = _command(
        [docker, "run", "--rm", "-i", "--user", "0", "--entrypoint", "python", image, "-"],
        cause=f"listing the files of {image} failed",
        stdin=_proof(IMAGE_FILES_PROOF, {"forbidden": FORBIDDEN_NAMES}),
    )
    return _answered(result, "the image listing")


def _pip_names(docker: str, image: str) -> set[str]:
    result = _command(
        [docker, "run", "--rm", "--entrypoint", "python", image, "-m", "pip", "list", "--format=json", "--disable-pip-version-check"],
        cause=f"pip list in {image} failed",
    )
    return {re.sub(r"[-_.]+", "-", entry["name"]).lower() for entry in json.loads(result.stdout)}


def test_trial_container_image(tmp_path: Path) -> None:
    required(CONTAINER_TRIAL_VARIABLE)
    # AC12, AC13's measurement, AC17's image part; finding P2's build stage.
    docker = _docker()
    _daemon(docker)
    image = _image()
    with _project(docker, "test_trial_container_image"):
        result = _command(
            [docker, "run", "--rm", "-i", "--entrypoint", "python", image, "-"],
            cause="the closure proof failed", stdin=_proof(CLOSURE_PROOF, {}),
        )
        closure = _answered(result, "the closure proof")
        assert closure["uid"] == 10001
        uid = _command([docker, "run", "--rm", "--entrypoint", "id", image, "-u"], cause="id -u failed")
        assert uid.stdout.strip() == "10001"
        base = _arguments(_stages()[1], "FROM")[0].split()[0]
        added = _pip_names(docker, image) - _pip_names(docker, base)
        assert added == {"twitch-ia-compagnon", *closure["closure"]}, added
        if sys.version_info >= (3, 11):
            import tomllib
        else:  # pragma: no cover - the 3.10 floor reads it through the backport
            import tomli as tomllib
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
        assert project["dependencies"] == DECLARED_DEPENDENCIES

        files = _image_files(docker, image)
        assert files["forbidden"] == [] and files["git"] is False
        assert files["pyprojects"] == [] and files["tests"] == []
        assert files["distribution_tests"] == [] and files["data"] == []
        assert files["profiles"] == sorted(path.name for path in PROFILES.glob("*.json"))

        inspected = json.loads(_command([docker, "image", "inspect", image], cause="docker image inspect failed").stdout)[0]
        config = inspected["Config"]
        names = [entry.split("=", 1)[0] for entry in config.get("Env") or ()] + list(config.get("Labels") or {})
        assert not [name for name in names if name in _credential_names() or SECRET_NAME.fullmatch(name)], names
        assert dict(entry.split("=", 1) for entry in config.get("Env") or ()) == FINAL_ENVIRONMENT
        size = int(_command([docker, "image", "inspect", "--format", "{{.Size}}", image], cause="docker image inspect failed").stdout.strip())
        commit = _command(["git", "-C", str(ROOT), "rev-parse", "HEAD"], cause="git rev-parse failed").stdout.strip()
        TRIAL_ARTEFACT.parent.mkdir(parents=True, exist_ok=True)
        TRIAL_ARTEFACT.write_text(
            json.dumps(
                {"commit": commit, "image": image, "size_bytes": size, "measured_at": datetime.now(timezone.utc).isoformat()},
                indent=2,
            ) + "\n",
            encoding="utf-8",
        )
        print(f"PHASE10-TRIAL image size: {size} bytes ({size / 1_000_000:.1f} MB)")

        # Finding P2: the build stage itself, over a context holding decoys.
        context = _decoy_context(tmp_path / "context")
        throwaway = f"tiac-build-stage:{uuid.uuid4().hex[:12]}"
        final = f"tiac-final:{uuid.uuid4().hex[:12]}"
        try:
            _command([docker, "build", "--target", "build", "-t", throwaway, str(context)], cause="docker build --target build failed", timeout=BUILD_SECONDS)
            _command([docker, "build", "-t", final, str(context)], cause="docker build of the decoy context failed", timeout=BUILD_SECONDS)
            for built in (throwaway, final):
                assert _image_files(docker, built)["forbidden"] == [], built
        finally:
            _command([docker, "image", "rm", "-f", throwaway, final], cause="docker image rm failed", check=False)


def test_trial_container_volume(tmp_path: Path) -> None:
    required(CONTAINER_TRIAL_VARIABLE)
    # AC8 (empty volume → wizard), AC9 (killed, restored → console, same
    # credentials, runtime running), AC7 (every write under the per-user directory).
    docker = _docker()
    _daemon(docker)
    twitch = _fake_twitch(tmp_path, _sentinels())
    with _project(docker, "test_trial_container_volume", [COMPOSE, twitch]) as project:
        project.up()
        since = _started_at(project)
        assert _landing(project) == config_ui.LANDING_WIZARD
        _complete_wizard(project, _sentinels())
        named = project.python(FILES_PROOF, {"since": since, "directory": DATA_DIRECTORY})["named"]
        before = _record(project, named["credentials"])
        assert before is not None
        project.kill()
        restarted = time.time()
        project.up()
        assert project.health()[0] != "unhealthy"
        assert _landing(project) == config_ui.LANDING_CONSOLE
        assert _record(project, named["credentials"]) == before
        last = _runtime_reports(project, restarted)
        assert last.startswith(("ready", "degraded")), last

        listing = project.python(FILES_PROOF, {"since": since, "directory": DATA_DIRECTORY})
        assert listing["unreadable"] == [], listing["unreadable"]
        outside = [path for path in listing["written"] if not path.startswith(DATA_DIRECTORY + "/")]
        assert outside == [], outside
        expected = {
            Path(path).name
            for role, path in listing["named"].items()
            if path is not None and (role != "overlay" or Path(path).name in listing["present"])
        }
        assert all(Path(path).parent == Path(DATA_DIRECTORY) for path in listing["named"].values() if path)
        assert expected <= set(listing["present"]), (expected, listing["present"])


def test_trial_container_secrets(tmp_path: Path) -> None:
    required(CONTAINER_TRIAL_VARIABLE)
    # AC16: sentinel credentials in the environment and the credential file,
    # a wizard run and its dry run, a restart; no sentinel in any log. The
    # logs are read before the restart and again after it; the wizard's own
    # output (an exec, outside the compose log) is asserted on as well.
    docker = _docker()
    _daemon(docker)
    sentinels = _sentinels()
    override = tmp_path / "compose.secrets.yaml"
    override.write_text(yaml.safe_dump({"services": {SERVICE: {"environment": sentinels}}}), encoding="utf-8")
    twitch = _fake_twitch(tmp_path, sentinels)
    with _project(docker, "test_trial_container_secrets", [COMPOSE, override, twitch]) as project:
        project.up()
        wizard_output = _complete_wizard(project, sentinels)
        first_logs = project.logs()
        first_health = "\n".join(project.health()[1])
        for name, value in sentinels.items():
            assert value not in wizard_output, name
            assert value not in first_logs, name
            assert value not in first_health, name
        first_tokens = _tokens(first_logs)
        assert len(first_tokens) == 1, len(first_tokens)
        assert first_tokens[0] not in first_health and first_tokens[0] not in wizard_output
        assert "token=" not in first_health and "http://" not in first_health

        project.kill()
        restarted = time.time()
        project.up()
        _runtime_reports(project, restarted)
        logs = project.logs()
        _, health_log = project.health()
        health_text = "\n".join(health_log)
        for name, value in sentinels.items():
            assert value not in logs, name
            assert value not in health_text, name
        tokens = _tokens(logs)
        assert len(tokens) == 2 and len(set(tokens)) == 2, len(tokens)
        for token in tokens:
            assert logs.count(token) == 1
            assert token not in health_text
        assert "token=" not in health_text and "http://" not in health_text


def test_trial_container_health(tmp_path: Path) -> None:
    required(CONTAINER_TRIAL_VARIABLE)
    # AC21: healthy once serving; the runtime SIGKILLed → unhealthy within 3
    # intervals, ``unhealthy: runtime exited``; the UI killed → never healthy.
    # The shipped ``restart: unless-stopped`` is turned off for this trial
    # only, so no replacement can be the container observed after the kill.
    docker = _docker()
    _daemon(docker)
    override = tmp_path / "compose.health.yaml"
    override.write_text(yaml.safe_dump({"services": {SERVICE: {"restart": "no"}}}), encoding="utf-8")
    twitch = _fake_twitch(tmp_path, _sentinels())
    with _project(docker, "test_trial_container_health", [COMPOSE, override, twitch]) as project:
        project.up()
        assert project.health()[0] == "healthy"
        _complete_wizard(project, _sentinels())
        project.kill()
        project.up()
        assert project.health()[0] == "healthy"
        supervision = json.loads(_record(project, f"{DATA_DIRECTORY}/{locations.PROFILE_FILE_NAME}{config_ui.SUPERVISION_SUFFIX}") or b"{}")
        assert supervision.get("state") == "launched", supervision
        killed = time.time()
        project.python(KILL_PROOF, {"pid": supervision["pid"]})
        statuses = _events(project, "health_status", killed, 3 * HEALTH_INTERVAL_SECONDS + HEALTH_TIMEOUT_SECONDS)
        assert [status for status in statuses if status.endswith("unhealthy")], statuses
        status, health_log = project.health()
        assert status == "unhealthy" and health_log[-1] == "unhealthy: runtime exited", health_log[-1:]

        instance = json.loads(_record(project, f"{DATA_DIRECTORY}/{locations.PROFILE_FILE_NAME}{config_ui.INSTANCE_SUFFIX}") or b"{}")
        before = project.inspect()
        assert before["HostConfig"]["RestartPolicy"]["Name"] == "no", before["HostConfig"]["RestartPolicy"]
        killed = time.time()
        project.python(KILL_PROOF, {"pid": instance["pid"]})
        exited = "die" in _events(project, "die", killed, 2 * HEALTH_INTERVAL_SECONDS)
        after = project.inspect()
        state = after["State"]
        # The instance observed is the one killed: same container, not restarted.
        assert (after["Id"], after["RestartCount"], state["StartedAt"]) == (
            before["Id"], before["RestartCount"], before["State"]["StartedAt"]
        )
        assert exited or (state.get("Health") or {}).get("Status") == "unhealthy"
        assert (state.get("Health") or {}).get("Status") != "healthy"
        assert "health_status: healthy" not in _events(project, "health_status", killed, 0)


def test_trial_container_profiles(tmp_path: Path) -> None:
    required(CONTAINER_TRIAL_VARIABLE)
    # AC22: the shipped profiles byte-identical inside the container, each
    # rendered, checked and dry-run there as uid 10001, no connection made.
    docker = _docker()
    _daemon(docker)
    with _project(docker, "test_trial_container_profiles") as project:
        project.up()
        answer = project.python(
            PROFILES_PROOF,
            {"parameters": TRIAL_PARAMETERS, "identities": TRIAL_IDENTITIES, "credentials": _sentinels()},
        )
        assert answer["uid"] == 10001
        assert answer["digests"] == {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(PROFILES.glob("*.json"))
        }
        assert set(answer["results"]) == set(EXPECTED_OUTCOMES)
        for profile_id, outcome in EXPECTED_OUTCOMES.items():
            result = answer["results"][profile_id]
            assert result["check"] == 0, (profile_id, result["diagnostics"])
            assert (result["outcome"], result["origin"]) == (outcome, "dry_run"), (profile_id, result)
        assert answer["connections"] == []


# -- the trial file's own shape (R7, AC23) ----------------------------------- #

#: Spelled in pieces so this file's source never holds them whole.
NOT_IN_SOURCE = ("skip" "if", "x" "fail", "import" "orskip", "pytest." "skip(")


def test_ac23_the_file_has_exactly_the_five_gated_trial_nodes() -> None:
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    trials = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_trial_")
    ]
    assert sorted(node.name for node in trials) == sorted(TRIAL_NODES)
    for node in trials:
        first = node.body[0]
        assert isinstance(first, ast.Expr) and isinstance(first.value, ast.Call), node.name
        call = first.value
        assert isinstance(call.func, ast.Name) and call.func.id == "required", node.name
        assert [ast.dump(argument) for argument in call.args] == [
            ast.dump(ast.Name(id="CONTAINER_TRIAL_VARIABLE", ctx=ast.Load()))
        ] and not call.keywords, node.name
    assert required.__module__ == "test_phase2_trials"


def test_ac23_the_file_never_skips_or_expects_a_failure() -> None:
    source = Path(__file__).read_text(encoding="utf-8")
    assert [word for word in NOT_IN_SOURCE if word in source] == []


def test_ac23_the_gate_skips_with_the_variable_named(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(CONTAINER_TRIAL_VARIABLE, raising=False)
    with pytest.raises(pytest.skip.Exception, match=f"^{CONTAINER_TRIAL_VARIABLE} is unset$"):
        required(CONTAINER_TRIAL_VARIABLE)
    monkeypatch.setenv(CONTAINER_TRIAL_VARIABLE, "  ")
    with pytest.raises(pytest.skip.Exception, match=f"^{CONTAINER_TRIAL_VARIABLE} is unset$"):
        required(CONTAINER_TRIAL_VARIABLE)


@pytest.mark.parametrize("name", TRIAL_NODES)
def test_ac23_a_switched_on_trial_fails_naming_docker_when_it_is_unfindable(
    name: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Switched on with ``docker`` unfindable, the body fails (not skips)
    before it starts anything; the AC33 guard would refuse a process."""

    monkeypatch.setenv(CONTAINER_TRIAL_VARIABLE, "1")
    monkeypatch.setenv(BUILD_CONTEXT_VARIABLE, ".")
    monkeypatch.setattr(shutil, "which", lambda *args, **kwargs: None)
    body = globals()[name]
    arguments = {"tmp_path": tmp_path} if "tmp_path" in inspect.signature(body).parameters else {}
    with pytest.raises(pytest.fail.Exception, match="docker") as failed:
        body(**arguments)
    assert "docker executable not found" in str(failed.value)
