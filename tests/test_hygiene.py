"""Hygiene of the delivered tree (R8; AC44, AC45).

Three checks over the files themselves, none over the runtime:

* AC45, sleeps: no ``asyncio.sleep(...)`` / ``time.sleep(...)`` **call** in
  ``tests/`` whose argument is anything but the literal ``0`` or ``0.0``.
  Matching is done on the token stream of each test module, not on its raw
  text, so that the plan's tolerances hold by construction: ``async def
  sleep(self, delay)`` on ``ManualClock`` is a definition, not a call;
  ``asyncio.sleep(0)`` in ``settle()`` yields to the loop without waiting;
  ``asyncio.sleep`` passed by reference to a sleeper seam is not a call. It
  also means a sleep inside a *string literal* is not a call of the suite:
  ``tests/test_capture.py`` embeds ``time.sleep(120)`` in the source of the
  child programs it spawns and kills (the long-lived capture program whose
  termination the test awaits on a pidfd), and that idle is the subject of
  the kill, not a wait of the test. A raw ``grep`` finds those three lines;
  this check does not, on purpose. To close the aliasing loophole a raw
  grep would also miss, ``from asyncio import sleep``, ``from time import
  sleep`` and ``import ... as`` aliases of the two modules are refused too.
* AC45, model names: no ``gpt-``, ``claude-``, ``llama``, ``mistral`` or
  ``gemini`` literal anywhere under ``core/`` and ``modules/`` (every file,
  case-insensitively, byte-compiled caches excluded).
* AC44: ``docs/README.md`` has the section "Phase 1 topology trial" with its
  six fields — commit, profiles, hosts, TLS, outcome, limits — each with a
  recorded value, the commit one naming a commit hash.
"""

from __future__ import annotations

import ast
import io
import re
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = ROOT / "tests"
CORE_DIR = ROOT / "core"
MODULES_DIR = ROOT / "modules"
README = ROOT / "docs" / "README.md"

SLEEP_MODULES = ("asyncio", "time")
ZERO_LITERALS = ("0", "0.0")
MODEL_LITERALS = ("gpt-", "claude-", "llama", "mistral", "gemini")

TRIAL_SECTION_TITLE = "Phase 1 topology trial"
#: The six fields of R8 / AC44, as the README's table labels them (French,
#: like the rest of the document): commit, the two profiles used, hosts
#: (distinct or loopback), TLS (used or not), observed outcome, remaining
#: limits.
TRIAL_FIELDS = (
    "Commit",
    "Profils utilisés",
    "Hôtes",
    "TLS",
    "Résultat observé",
    "Limites restantes",
)

_INSIGNIFICANT = {
    tokenize.NL,
    tokenize.NEWLINE,
    tokenize.COMMENT,
    tokenize.INDENT,
    tokenize.DEDENT,
    tokenize.ENCODING,
}


def _label(path: Path) -> str:
    """``path`` relative to the checkout when it is inside it (the hits of
    the tree), as given otherwise (the samples of the check's own tests)."""

    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _python_files(directory: Path) -> list[Path]:
    return sorted(
        path for path in directory.rglob("*.py") if "__pycache__" not in path.parts
    )


def _all_files(directory: Path) -> list[Path]:
    return sorted(
        path
        for path in directory.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    )


def _code_tokens(path: Path) -> list[tokenize.TokenInfo]:
    source = path.read_text(encoding="utf-8")
    return [
        token
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if token.type not in _INSIGNIFICANT
    ]


def _positive_sleep_calls(path: Path) -> list[str]:
    """Every ``asyncio.sleep(`` / ``time.sleep(`` call site of ``path`` whose
    argument list is not exactly the literal ``0`` or ``0.0``."""

    tokens = _code_tokens(path)
    found: list[str] = []
    for index in range(len(tokens) - 3):
        module, dot, name, paren = tokens[index : index + 4]
        if not (
            module.type == tokenize.NAME
            and module.string in SLEEP_MODULES
            and dot.string == "."
            and name.type == tokenize.NAME
            and name.string == "sleep"
            and paren.string == "("
        ):
            continue
        arguments = tokens[index + 4 : index + 6]
        literal_zero = (
            len(arguments) == 2
            and arguments[0].type == tokenize.NUMBER
            and arguments[0].string in ZERO_LITERALS
            and arguments[1].string == ")"
        )
        if not literal_zero:
            line = module.start[0]
            found.append(f"{_label(path)}:{line}: {module.line.strip()}")
    return found


def _sleep_aliases(path: Path) -> list[str]:
    """``from asyncio import sleep``-style imports and ``import time as t``
    aliases that would let a positive sleep hide from the call check."""

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[str] = []
    for node in ast.walk(tree):
        aliased = False
        if isinstance(node, ast.ImportFrom):
            aliased = node.module in SLEEP_MODULES and any(
                alias.name == "sleep" for alias in node.names
            )
        elif isinstance(node, ast.Import):
            aliased = any(
                alias.name in SLEEP_MODULES and alias.asname is not None for alias in node.names
            )
        if aliased:
            found.append(f"{_label(path)}:{node.lineno}: {ast.unparse(node)}")
    return found


def _model_literal_hits(directory: Path) -> list[str]:
    found: list[str] = []
    for path in _all_files(directory):
        for number, line in enumerate(path.read_bytes().splitlines(), start=1):
            lowered = line.lower()
            for literal in MODEL_LITERALS:
                if literal.encode() in lowered:
                    found.append(f"{_label(path)}:{number}: {literal}")
    return found


def _section(markdown: str, title: str) -> str:
    """The body of the ``##`` section whose heading contains ``title``, up to
    the next ``##`` heading."""

    lines = markdown.splitlines()
    starts = [
        index
        for index, line in enumerate(lines)
        if line.startswith("## ") and title in line
    ]
    assert len(starts) == 1, f"expected exactly one section titled {title!r}, found {len(starts)}"
    start = starts[0]
    end = next(
        (index for index in range(start + 1, len(lines)) if lines[index].startswith("## ")),
        len(lines),
    )
    return "\n".join(lines[start:end])


def _table_row(section: str, label: str) -> str:
    rows = [
        line
        for line in section.splitlines()
        if line.startswith("|") and line.split("|")[1].strip() == label
    ]
    assert len(rows) == 1, f"expected exactly one table row labelled {label!r}, found {len(rows)}"
    cells = [cell.strip() for cell in rows[0].strip().strip("|").split("|")]
    assert len(cells) >= 2, rows[0]
    return cells[1]


# --------------------------------------------------------------------------- #
# AC45
# --------------------------------------------------------------------------- #


def test_ac45_the_suite_makes_no_positive_duration_sleep_call() -> None:
    """AC45 (R8): ``tests/`` contains 0 ``asyncio.sleep(``/``time.sleep(``
    calls with any argument other than the literal ``0``/``0.0``, and no
    import that would alias either module's ``sleep`` out of that check."""

    files = _python_files(TESTS_DIR)
    assert Path(__file__) in files

    positive = [hit for path in files for hit in _positive_sleep_calls(path)]
    assert positive == [], "positive-duration sleep calls in tests/:\n" + "\n".join(positive)

    aliases = [hit for path in files for hit in _sleep_aliases(path)]
    assert aliases == [], "sleep aliases in tests/:\n" + "\n".join(aliases)


def test_the_sleep_check_sees_calls_and_only_calls(tmp_path: Path) -> None:
    """The check itself, on a file it would not otherwise see: it flags the
    positive and the non-literal forms, and tolerates exactly what the plan
    says it must — the literal zero, a ``sleep`` method definition, the
    function passed by reference, and a sleep inside a string literal."""

    sample = tmp_path / "sample.py"
    sample.write_text(
        "\n".join(
            [
                "import asyncio",
                "import time",
                "class ManualClock:",
                "    async def sleep(self, delay):",
                "        pass",
                "async def settle():",
                "    await asyncio.sleep(0)",
                "    await asyncio.sleep(0.0)",
                "sleeper = asyncio.sleep",
                'PROGRAM = """import time; time.sleep(120)"""',
                "async def bad():",
                "    await asyncio.sleep(0.1)",
                "    time.sleep(1)",
                "    await asyncio.sleep(delay)",
                "    await asyncio.sleep(0, result=None)",
            ]
        ),
        encoding="utf-8",
    )
    hits = _positive_sleep_calls(sample)
    assert [hit.split(":")[1] for hit in hits] == ["12", "13", "14", "15"], hits

    aliased = tmp_path / "aliased.py"
    aliased.write_text(
        "from asyncio import sleep\nfrom time import sleep as nap\nimport time as t\n",
        encoding="utf-8",
    )
    assert len(_sleep_aliases(aliased)) == 3
    clean = tmp_path / "clean.py"
    clean.write_text("from asyncio import Queue\nimport time\n", encoding="utf-8")
    assert _sleep_aliases(clean) == []


def test_ac45_core_and_modules_name_no_model() -> None:
    """AC45 (R8): 0 occurrences of ``gpt-``, ``claude-``, ``llama``,
    ``mistral``, ``gemini`` under ``core/`` and ``modules/``, in any file,
    in any case."""

    hits = _model_literal_hits(CORE_DIR) + _model_literal_hits(MODULES_DIR)
    assert hits == [], "model-name literals:\n" + "\n".join(hits)


def test_the_model_literal_check_reads_every_file_kind(tmp_path: Path) -> None:
    sample = tmp_path / "tree"
    (sample / "pkg").mkdir(parents=True)
    (sample / "pkg" / "module.yaml").write_text("model: Gemini-pro\n", encoding="utf-8")
    (sample / "pkg" / "__init__.py").write_text("DEFAULT = 'gpt-x'\n", encoding="utf-8")
    (sample / "pkg" / "__pycache__").mkdir()
    (sample / "pkg" / "__pycache__" / "x.pyc").write_bytes(b"llama")
    (sample / "pkg" / "clean.py").write_text("MODEL = settings['model']\n", encoding="utf-8")
    hits = _model_literal_hits(sample)
    assert sorted(hit.rsplit(": ", 1)[1] for hit in hits) == ["gemini", "gpt-"]


# --------------------------------------------------------------------------- #
# AC44
# --------------------------------------------------------------------------- #


def test_ac44_readme_records_the_phase_1_topology_trial() -> None:
    """AC44 (R8): ``docs/README.md`` has a "Phase 1 topology trial" section
    with its 6 fields — the commit, the two profiles used, distinct hosts
    or loopback, TLS used or not, the observed outcome, the remaining
    limits — each carrying a recorded value."""

    section = _section(README.read_text(encoding="utf-8"), TRIAL_SECTION_TITLE)
    values = {label: _table_row(section, label) for label in TRIAL_FIELDS}
    assert len(values) == 6
    for label, value in values.items():
        assert value, f"field {label!r} has no recorded value"

    assert re.search(r"`[0-9a-f]{7,40}`", values["Commit"]), values["Commit"]
    assert "config.server.yaml.example" in values["Profils utilisés"]
    assert "agent.yaml.example" in values["Profils utilisés"]
    assert re.search(r"loopback|distinct", values["Hôtes"], re.IGNORECASE)
    assert re.search(r"utilisé|used", values["TLS"], re.IGNORECASE)
    assert "test_ac39_two_process_topology_over_loopback" in section
