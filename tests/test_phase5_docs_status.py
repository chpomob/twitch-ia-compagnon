"""The bridge is not "delivered" before its real-instance trial (R8; AC37, AC36).

AC37. While the trial record ``docs/campaigns/phase5/bridge-trial-record.md``
does not exist:

* ``README.md`` and ``docs/README.md`` each state the bridge's status
  "not delivered" on a line naming the bridge (or its guide);
* no tracked Markdown file applies the word "delivered" to the bridge: on
  every line naming the bridge or its guide, each whole-word "delivered" must
  be negated ("not delivered", "not yet delivered") or quoted as a word
  (``"delivered"``). "delivery", "undelivered" and lines that do not name the
  bridge are not matched. The campaign papers under ``docs/campaigns/`` are
  excluded: they are the authority that states this very rule, and quote it.

Once the record exists, it must carry the instance version (a line naming
``instance version`` with a dotted number), a date (a line naming ``date``
with an ISO ``YYYY-MM-DD`` date) and one answer for each study question 1–8:
exactly one table row per question whose first cell is the number (``1`` or
``Q1``) and whose last cell is a non-empty answer, not a placeholder.

Plan step P20 marked the status test ``xfail(strict=True)`` until the README
and index rows existed; plan step P21 added them with the operator guide and
removed the marker, so the test now runs unmarked.

AC36's meta-check lives here, not in ``tests/test_phase5_trials.py``, so that
file collects exactly its eight trial nodes (finding P5): run with no trial
variable, it collects 8 nodes, skips 8, each reason naming that node's
variables, and the whole run creates 0 sockets.
"""

from __future__ import annotations

import datetime
import os
import re
import importlib.util
import socket
import subprocess
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

import pytest

from conftest import tracked_files

REPOSITORY = Path(__file__).resolve().parent.parent
RECORD = REPOSITORY / "docs" / "campaigns" / "phase5" / "bridge-trial-record.md"
STATUS_FILES = ("README.md", "docs/README.md")
#: The authority and working papers of the campaigns, which quote the rule.
EXCLUDED_PREFIXES = ("docs/campaigns/",)
QUESTIONS = range(1, 9)

#: A line naming the bridge or its guide (English or French wording).
BRIDGE = re.compile(
    r"automation[- ]bridge|\bbridge\b|streamer\.?bot|pont d['’]automatisation",
    re.IGNORECASE,
)
DELIVERED = re.compile(r"\bdelivered\b", re.IGNORECASE)
NEGATED = re.compile(r"\bnot\s+(?:yet\s+)?delivered\b", re.IGNORECASE)
QUOTED = re.compile(r"[\"“'‘`«]\s*delivered\s*[\"”'’`»]", re.IGNORECASE)

VERSION = re.compile(r"instance version.*?\bv?\d+(?:\.\d+)+\b", re.IGNORECASE)
DATE_LINE = re.compile(r"\bdate\b.*?\b(\d{4}-\d{2}-\d{2})\b", re.IGNORECASE)
ROW = re.compile(r"^\s*\|\s*(?:Q)?([1-8])\s*\|(.*)\|\s*$", re.IGNORECASE)
PLACEHOLDERS = frozenset({"", "-", "—", "–", "?", "tbd", "todo", "n/a", "…", "..."})


# --------------------------------------------------------------------------- #
# The rules
# --------------------------------------------------------------------------- #


def names_bridge(line: str) -> bool:
    return BRIDGE.search(line) is not None


def claims(line: str) -> int:
    """How many times *line* applies "delivered" to the bridge."""

    if not names_bridge(line):
        return 0
    return len(DELIVERED.findall(QUOTED.sub("", NEGATED.sub("", line))))


def states_not_delivered(text: str) -> bool:
    return any(names_bridge(line) and NEGATED.search(line) for line in text.splitlines())


def claim_lines(path: str, text: str) -> list[str]:
    return [
        f"{path}:{number}: {line.strip()}"
        for number, line in enumerate(text.splitlines(), start=1)
        if claims(line)
    ]


def record_problems(text: str) -> list[str]:
    """What the trial record lacks: version, date, one answer per question."""

    problems: list[str] = []
    if not any(VERSION.search(line) for line in text.splitlines()):
        problems.append("no instance version")
    dates = [match.group(1) for match in map(DATE_LINE.search, text.splitlines()) if match]
    valid = []
    for value in dates:
        try:
            valid.append(datetime.date.fromisoformat(value))
        except ValueError:
            pass
    if not valid:
        problems.append("no date")
    answers: dict[int, list[str]] = {question: [] for question in QUESTIONS}
    for line in text.splitlines():
        row = ROW.match(line)
        if row is None:
            continue
        cells = [cell.strip() for cell in row.group(2).split("|")]
        answers[int(row.group(1))].append(cells[-1])
    for question, found in answers.items():
        if len(found) != 1:
            problems.append(f"question {question}: {len(found)} rows, expected 1")
        elif found[0].strip("*_` ").lower() in PLACEHOLDERS:
            problems.append(f"question {question}: no answer")
    return problems


def tracked_markdown() -> list[str]:
    """``git ls-files -- '*.md'`` read from the index in-process (AC33, P15a)."""

    listed = tracked_files("*.md", REPOSITORY)
    return sorted(path for path in listed if path and not path.startswith(EXCLUDED_PREFIXES))


def read(path: str) -> str:
    return (REPOSITORY / path).read_text(encoding="utf-8")


def every_claim(paths: Iterable[str]) -> list[str]:
    found: list[str] = []
    for path in paths:
        if (REPOSITORY / path).is_file():
            found.extend(claim_lines(path, read(path)))
    return found


# --------------------------------------------------------------------------- #
# AC37 on the repository
# --------------------------------------------------------------------------- #


def test_ac37_readme_and_index_state_the_bridge_not_delivered() -> None:
    """AC37: without the trial record, both the README and the documentation
    index state the bridge "not delivered"; with it, the record is complete."""

    if RECORD.exists():
        assert record_problems(RECORD.read_text(encoding="utf-8")) == []
        return
    missing = [path for path in STATUS_FILES if not states_not_delivered(read(path))]
    assert missing == [], f"no 'not delivered' status for the bridge in {missing}"


def test_ac37_no_document_calls_the_bridge_delivered() -> None:
    """AC37: without the trial record, no tracked Markdown file applies
    "delivered" to the bridge; with it, the record is complete."""

    if RECORD.exists():
        assert record_problems(RECORD.read_text(encoding="utf-8")) == []
        return
    paths = tracked_markdown()
    assert set(STATUS_FILES) <= set(paths)
    assert every_claim(paths) == []


# --------------------------------------------------------------------------- #
# AC37: the rules themselves
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "line",
    [
        "| automation bridge | delivered |",
        "The bridge is delivered.",
        "See [the guide](docs/automation-bridge.md): Delivered in phase 5.",
        "Le module streamerbot est delivered.",
        "The bridge is not delivered, and the bridge is delivered.",
    ],
)
def test_ac37_a_claim_on_a_bridge_line_is_found(line: str) -> None:
    assert claims(line) >= 1
    assert claim_lines("x.md", f"intro\n{line}\n") == [f"x.md:2: {line.strip()}"]


@pytest.mark.parametrize(
    "line",
    [
        "| automation bridge | not delivered — awaiting real-instance trial |",
        "The bridge is NOT delivered yet.",
        "The bridge is not yet delivered.",
        'The bridge is never called "delivered" before the record.',
        "The bridge's delivery deadline is the acknowledgement.",
        "An undelivered bridge event is dropped and counted.",
        "The reply was delivered to the chat.",
        "Phase 4 is delivered.",
    ],
)
def test_ac37_a_line_that_is_not_a_bridge_claim_is_not_found(line: str) -> None:
    assert claims(line) == 0


def test_ac37_the_status_needs_a_negated_line_naming_the_bridge() -> None:
    assert states_not_delivered("| Automation bridge | not delivered — awaiting real-instance trial |")
    assert states_not_delivered("- `streamerbot` (bridge): not delivered")
    assert not states_not_delivered("| Automation bridge | awaiting real-instance trial |")
    assert not states_not_delivered("Phase 3 is not delivered.")
    assert not states_not_delivered("")


COMPLETE_RECORD = "\n".join(
    [
        "# Bridge trial record",
        "",
        "Instance version: 1.0.4",
        "Date: 2026-11-02",
        "",
        "| Question | Subject | Answer |",
        "|---|---|---|",
        *(f"| {number} | subject {number} | answer {number} |" for number in QUESTIONS),
    ]
)


def test_ac37_a_complete_record_has_no_problem() -> None:
    assert record_problems(COMPLETE_RECORD) == []
    assert record_problems(COMPLETE_RECORD.replace("| 3 |", "| Q3 |")) == []


@pytest.mark.parametrize(
    ("edit", "problem"),
    [
        (("Instance version: 1.0.4", "Instance version: unknown"), "no instance version"),
        (("Date: 2026-11-02", "Date: soon"), "no date"),
        (("Date: 2026-11-02", "Date: 2026-13-40"), "no date"),
        (("| 5 | subject 5 | answer 5 |", ""), "question 5: 0 rows, expected 1"),
        (("| 6 | subject 6 | answer 6 |", "| 6 | subject 6 | TBD |"), "question 6: no answer"),
        (("| 7 | subject 7 | answer 7 |", "| 7 | subject 7 |  |"), "question 7: no answer"),
        (
            ("| 8 | subject 8 | answer 8 |", "| 8 | subject 8 | answer 8 |\n| 8 | again | x |"),
            "question 8: 2 rows, expected 1",
        ),
    ],
)
def test_ac37_an_incomplete_record_is_refused(edit: tuple[str, str], problem: str) -> None:
    old, new = edit
    assert old in COMPLETE_RECORD
    assert problem in record_problems(COMPLETE_RECORD.replace(old, new))


def test_ac37_the_exclusion_covers_only_the_campaign_papers() -> None:
    paths = tracked_markdown()
    assert "README.md" in paths and "docs/README.md" in paths
    assert not any(path.startswith("docs/campaigns/") for path in paths)
    # The study names the bridge on many lines; it is scanned.
    assert "docs/research/streamerbot-etude-2026-10.md" in paths


# --------------------------------------------------------------------------- #
# AC36: the trial file, run with no trial variable
# --------------------------------------------------------------------------- #

#: The ``socket`` entry points the counter wraps, and whether each reaches
#: the network: ``socket.socket`` is classified by its family.
COUNTED_FUNCTIONS = (
    ("socketpair", False),
    ("fromfd", False),
    ("create_connection", True),
    ("create_server", True),
)
#: What the suite's isolation guard (``tests/conftest.py``, phase 8 P1)
#: raises when a node creates a socket.
GUARD_REFUSAL = re.compile(r"^AC33: \S+ created a socket$")


class _Unopened:
    """What a counted socket attempt hands back once the guard refused it:
    closable, holding no descriptor."""

    def close(self) -> None:
        return None


class SocketCount:
    """Every socket a call attempts, counted in-process under the guard.

    Phase 8 (R9, AC33, plan P15e) supersedes the child interpreter that
    counted sockets with real ones: no node may create a socket at all, so
    each entry point the guard installed is wrapped and every attempt is
    counted — ``nodes``, every socket attempted; ``network``, the non-local
    ones. Each attempt must end in the guard's own refusal (``refusals``);
    the caller is then handed an unopened stand-in (no descriptor), so a
    node that only opens and closes a socket runs on, as it did when the
    socket was real. An attempt the guard let through would be a socket
    created: it is counted in ``created``.
    """

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.nodes: list[str] = []
        self.network: list[str] = []
        self.refusals: list[str] = []
        self.created: list[str] = []
        guarded_init = socket.socket.__init__

        def init(
            sock: socket.socket, family: Any = -1, type: Any = -1, proto: Any = -1, fileno: Any = None
        ) -> None:
            remote = fileno is None and family in (-1, socket.AF_INET, socket.AF_INET6)
            if self._attempt("socket", remote, lambda: guarded_init(sock, family, type, proto, fileno)) is _REFUSED:
                # Never initialised: its descriptor is -1, so closing it closes nothing.
                sock._io_refs = 0  # type: ignore[attr-defined]
                sock._closed = False  # type: ignore[attr-defined]

        monkeypatch.setattr(socket.socket, "__init__", init)
        for name, remote in COUNTED_FUNCTIONS:
            monkeypatch.setattr(socket, name, self._counting(name, getattr(socket, name), remote))

    def _counting(self, name: str, guarded: Callable[..., Any], remote: bool) -> Callable[..., Any]:
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            result = self._attempt(name, remote, lambda: guarded(*args, **kwargs))
            if result is not _REFUSED:
                return result
            return (_Unopened(), _Unopened()) if name == "socketpair" else _Unopened()

        return wrapper

    def _attempt(self, name: str, remote: bool, call: Callable[[], Any]) -> Any:
        self.nodes.append(name)
        if remote:
            self.network.append(name)
        try:
            result = call()
        except AssertionError as refused:
            if not GUARD_REFUSAL.match(str(refused)):
                raise
            self.refusals.append(name)
            return _REFUSED
        self.created.append(name)
        return result


#: What :meth:`SocketCount._attempt` returns for an attempt the guard refused.
_REFUSED = object()


def counted_run(path: str | Path) -> subprocess.CompletedProcess[str]:
    """Run the test file at *path* in-process under a :class:`SocketCount`,
    with no trial variable in the environment, reported as the child
    interpreter reported it: ``pytest -q -rs`` lines (one ``SKIPPED`` line
    per skipped node, then the count line) and the
    ``PHASE5-SOCKETS nodes=<n> network=<n>`` line; ``returncode`` 0 unless a
    node failed. Every counted attempt was the guard's refusal: no socket was
    created to be counted (phase 8 R9, AC33)."""

    target = Path(path) if Path(path).is_absolute() else REPOSITORY / path
    shown = target.relative_to(REPOSITORY) if target.is_relative_to(REPOSITORY) else target
    outcomes: dict[str, int] = {}
    lines: list[str] = []
    with pytest.MonkeyPatch.context() as monkeypatch:
        for name in list(os.environ):
            if name.startswith("PHASE5_TRIAL"):
                monkeypatch.delenv(name)
        spec = importlib.util.spec_from_file_location(f"_counted_{target.stem}", target)
        assert spec is not None and spec.loader is not None, target
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        count = SocketCount(monkeypatch)
        for name in sorted(name for name in vars(module) if name.startswith("test_")):
            try:
                getattr(module, name)()
            except pytest.skip.Exception as skip:
                outcomes["skipped"] = outcomes.get("skipped", 0) + 1
                lines.append(f"SKIPPED [1] {shown}::{name}: {skip.msg}")
            except BaseException as exc:  # noqa: BLE001 - every other outcome is reported
                outcomes["failed"] = outcomes.get("failed", 0) + 1
                lines.append(f"FAILED {shown}::{name} - {type(exc).__name__}: {exc}")
            else:
                outcomes["passed"] = outcomes.get("passed", 0) + 1
    assert count.refusals == count.nodes and count.created == [], (count.nodes, count.created)
    summary = ", ".join(f"{number} {outcome}" for outcome, number in sorted(outcomes.items()))
    lines.append(f"{summary} in 0.00s")
    lines.append(f"PHASE5-SOCKETS nodes={len(count.nodes)} network={len(count.network)}")
    return subprocess.CompletedProcess(
        ("counted_run", str(shown)), 1 if "failed" in outcomes else 0, "\n".join(lines) + "\n", ""
    )


def test_ac36_the_socket_count_sees_a_local_and_a_network_socket(tmp_path: Path) -> None:
    """The counter is not blind: a node creating a socket pair and a network
    socket is counted in both windows (phase 8 R9, AC33: each attempt is the
    guard's refusal, counted, and no socket is created)."""

    probe = tmp_path / "test_socket_probe.py"
    probe.write_text(
        "import socket\n\n\n"
        "def test_opens() -> None:\n"
        "    for end in socket.socketpair():\n"
        "        end.close()\n"
        "    socket.socket(socket.AF_INET, socket.SOCK_STREAM).close()\n",
        encoding="utf-8",
    )
    run = counted_run(probe)
    assert run.returncode == 0, run.stdout + run.stderr
    (line,) = [text for text in run.stdout.splitlines() if text.startswith("PHASE5-SOCKETS ")]
    counts = dict(field.split("=") for field in line.split()[1:])
    assert int(counts["nodes"]) >= 2 and int(counts["network"]) == 1, line


def test_ac36_the_trial_file_skips_its_eight_nodes_opening_no_socket() -> None:
    """AC36: with every trial variable unset, the file collects 8 nodes, all
    skipped, each reason naming its node's variables, and 0 sockets open."""

    import test_phase5_trials as trials

    run = counted_run("tests/test_phase5_trials.py")
    output = run.stdout
    assert run.returncode == 0, output + run.stderr
    assert "PHASE5-SOCKETS nodes=0 network=0" in output.splitlines(), output
    assert re.search(r"^8 skipped in ", output, re.MULTILINE), output
    assert not re.search(r"\b(passed|failed|error|errors|xfailed|xpassed)\b", output), output
    reasons = [line for line in output.splitlines() if line.startswith("SKIPPED ")]
    assert len(reasons) == 8, output
    assert sorted(trials.QUESTIONS) == list(QUESTIONS)
    assert sorted(trials.NODES.values()) == list(QUESTIONS)
    for question, (_, variables) in trials.QUESTIONS.items():
        (reason,) = [line for line in reasons if f": q{question} " in line]
        for variable in variables:
            assert variable in reason, (question, variable)
