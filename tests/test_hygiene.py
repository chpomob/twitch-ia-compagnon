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
* AC34 (phase 2 R9): ``docs/README.md`` has the section "Essais
  d'intégration par fournisseur (phase 2)" whose table holds exactly the six
  provider rows, each with its five fields — commit, settings shape, outcome,
  limits, date — recorded; and the section "Versionnement de la spec phase 1
  (phase 2)" names every test of the phase 2 spec's allowlist, read from the
  spec itself.
* AC41 (phase 2 R10): the README's playback and capture subsections describe
  no lead — none of ``lead``, ``marge``, ``margin`` appears in them.
* AC41 (phase 3 R8): the README's phase 3 section names
  ``tests/test_phase3_trials.py``; its trial table holds exactly the six R8
  rows in order, each with its five fields recorded (a commit hash, a
  settings shape carrying no secret, a cited ``PHASE3-TRIAL`` line or "Non
  exécuté", the limits, a date); and its versioning subsection names every
  test of the phase 3 spec's allowlist, read from the archived spec
  ``docs/campaigns/phase3/spec.md``.
* AC7 (phase 3 R1): the presence table has a row per presence item, each
  "configuration ou code" cell non-empty and naming a shipped module, the
  chat-command polls and the channel-point redemptions saying "code".
* Target list (gate finding F5): the phase 1 spec's ``targets`` front matter
  is the authority on which repository files the phase touches, so every
  file a plan step names must be a target, lie in the permitted scope the
  spec states (the campaign's own directory), or be one the spec records
  as planned but untouched; the spec must state that permitted scope in so
  many words; and every target must exist, so the list names no file the
  branch never produced.
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
CAMPAIGN_DIR = ROOT / "docs" / "campaigns" / "phase1"
SPEC = CAMPAIGN_DIR / "spec.md"
PLAN = CAMPAIGN_DIR / "plan.md"

SLEEP_MODULES = ("asyncio", "time")
ZERO_LITERALS = ("0", "0.0")
MODEL_LITERALS = ("gpt-", "claude-", "llama", "mistral", "gemini")

SCOPE_SECTION_TITLE = "Target list and permitted scope"
#: The one place the spec lets a step write outside its targets: the
#: campaign's own documents and scaffolding, as a repository-relative prefix.
PERMITTED_SCOPE = "docs/campaigns/phase1/"
#: Plan steps name the repository-root ``spec.md`` / ``plan.md``; the step
#: generator redirects them to the campaign directory, which the spec's
#: scope section records, so they resolve inside the permitted scope.
ROOT_DOCUMENT_ALIASES = {"spec.md": "docs/campaigns/phase1/spec.md", "plan.md": "docs/campaigns/phase1/plan.md"}
#: Files the plan schedules an edit of that turned out to need none; the
#: spec must say so by name, since they are not targets.
PLANNED_BUT_UNTOUCHED = ("tests/test_lifecycle.py", "tests/test_retention.py")

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

PHASE2_SPEC = ROOT / "docs" / "campaigns" / "phase2" / "spec.md"
PHASE2_ALLOWLIST_TITLE = "Test failure allowlist"
PHASE2_VERSIONING_TITLE = "Versionnement de la spec phase 1 (phase 2)"
PHASE2_TRIAL_TITLE = "Essais d'intégration par fournisseur (phase 2)"
#: The six providers of phase 2 R9, as the README's table labels them, in
#: the spec's order: speech synthesis, transcription, playback command,
#: capture command, scene provider, platform polls.
PHASE2_TRIAL_ROWS = (
    "Synthèse vocale",
    "Transcription",
    "Commande de lecture",
    "Commande de capture",
    "Fournisseur de scènes",
    "Sondages de plateforme",
)
#: The five fields of each row (R9, AC34), after the provider label.
PHASE2_TRIAL_FIELDS = ("Commit", "Forme des réglages", "Résultat", "Limites", "Date")
#: The README subsections that state the playback and capture deadlines
#: (AC41): none may describe a lead, in either language.
PHASE2_DEADLINE_SUBSECTIONS = ("Lecture (`audio.speak`", "Capture (`audio.capture`")
LEAD_WORDS = re.compile(r"\b(lead|marge|margin)", re.IGNORECASE)
#: A settings key naming a secret must be followed by a placeholder
#: (``<…>``, ``${…}``) or be recorded as empty, never by a value.
SECRET_SETTING = re.compile(
    r"\b(api_key|password|access_token|client_secret|token)\s*:\s*(?!<|\$\{|vide\b)[^\s,}]",
    re.IGNORECASE,
)

PHASE3_SPEC = ROOT / "docs" / "campaigns" / "phase3" / "spec.md"
PHASE3_SECTION_TITLE = "Phase 3 — présence, mémoire des spectateurs, modération et plateformes"
PHASE3_VERSIONING_TITLE = "Versionnement de la spec phase 2 (phase 3)"
PHASE3_PRESENCE_TITLE = "Pack de présence : configuration ou code"
PHASE3_TRIAL_TITLE = "Essais réels par plateforme (phase 3)"
PHASE3_TRIALS_FILE = "tests/test_phase3_trials.py"
#: The six trials of phase 3 R8, as the README's table labels them, in the
#: spec's order: platform clips, platform moderation, community notices,
#: Kick, YouTube, screen watch.
PHASE3_TRIAL_ROWS = (
    "clips de plateforme",
    "modération de plateforme",
    "notifications communautaires",
    "Kick",
    "YouTube",
    "veille d'écran",
)
#: The five fields of each row (R8, AC41), after the trial label; the same
#: labels as the phase 2 table.
PHASE3_TRIAL_FIELDS = PHASE2_TRIAL_FIELDS
#: The presence items of AC7 (at least these eleven), plus the three the plan
#: (P25) adds because they still need code.
PHASE3_PRESENCE_ITEMS = (
    "Accueil",
    "Remerciements",
    "Résumé (« qu'ai-je manqué ? »)",
    "Traduction",
    "Sondages (commande de chat)",
    "Scènes",
    "Voix",
    "Jingles",
    "Réactions à l'écran",
    "Clips",
    "Mémoire",
    "Récompenses de points de chaîne",
    "Annonce du lien du clip",
    "Remerciement d'un donateur anonyme",
)
#: The items AC7 requires to say "code": chat-command polls and channel-point
#: redemptions.
PHASE3_CODE_ITEMS = ("Sondages (commande de chat)", "Récompenses de points de chaîne")
PHASE3_PRESENCE_COLUMN = "Configuration ou code"

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


# --------------------------------------------------------------------------- #
# AC34, AC41 (phase 2)
# --------------------------------------------------------------------------- #


def _table_cells(section: str) -> dict[str, list[str]]:
    """Every data row of the one table in *section*, keyed by its first cell,
    the header and the separator excluded."""

    rows = [line for line in section.splitlines() if line.startswith("|")]
    assert len(rows) >= 2, "the section holds no table"
    header, separator, *data = rows
    assert set(separator.replace("|", "").split()) <= {"---"}, separator
    table: dict[str, list[str]] = {}
    for row in data:
        cells = [cell.strip() for cell in row.strip().strip("|").split("|")]
        assert cells[0] not in table, f"row {cells[0]!r} appears twice"
        table[cells[0]] = cells[1:]
    table[""] = [cell.strip() for cell in header.strip().strip("|").split("|")]
    return table


def _allowlisted_tests(spec: str) -> list[str]:
    """The test ids the spec's "Test failure allowlist" section lists."""

    section = _section(spec, PHASE2_ALLOWLIST_TITLE)
    return re.findall(r"^- `(tests/[^`]+)`", section, re.MULTILINE)


def _lead_hits(subsection: str) -> list[str]:
    return [line for line in subsection.splitlines() if LEAD_WORDS.search(line)]


def test_ac34_readme_records_the_phase_2_provider_trials() -> None:
    """AC34 (phase 2 R9): the trial table has exactly the six provider rows,
    each with its five fields recorded — a commit hash, a settings shape
    carrying no secret, an outcome that is a cited trial line or "not run"
    with its reason, the limits, the date — and the versioning section names
    every test of the phase 2 allowlist."""

    readme = README.read_text(encoding="utf-8")
    section = _section(readme, PHASE2_TRIAL_TITLE)
    table = _table_cells(section)
    header = table.pop("")
    assert tuple(header[1:]) == PHASE2_TRIAL_FIELDS, header
    assert tuple(table) == PHASE2_TRIAL_ROWS, list(table)
    for label, cells in table.items():
        assert len(cells) == len(PHASE2_TRIAL_FIELDS), (label, cells)
        values = dict(zip(PHASE2_TRIAL_FIELDS, cells))
        for name, value in values.items():
            assert value, f"row {label!r}: field {name!r} has no recorded value"
        assert re.search(r"`[0-9a-f]{7,40}`", values["Commit"]), (label, values["Commit"])
        assert not SECRET_SETTING.search(values["Forme des réglages"]), (label, "secret value")
        outcome = values["Résultat"]
        assert "PHASE2-TRIAL" in outcome or "Non exécuté" in outcome, (label, outcome)
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", values["Date"]), (label, values["Date"])
    assert "tests/test_phase2_trials.py" in section

    allowlisted = _allowlisted_tests(PHASE2_SPEC.read_text(encoding="utf-8"))
    # Spec v1.3 (P22F2, gate 1 F5): the eight v1.2 entries plus the four
    # reconciled ones.
    assert len(allowlisted) == 12, allowlisted
    versioning = _section(readme, PHASE2_VERSIONING_TITLE)
    missing = [test for test in allowlisted if f"`{test}`" not in versioning]
    assert missing == [], f"allowlisted tests the versioning section does not name: {missing}"


def test_ac41_the_readme_describes_no_lead_for_playback_or_capture() -> None:
    """AC41 (phase 2 R10): the README's playback and capture subsections say
    the stop and the kill happen at the call deadline, and none of ``lead``,
    ``marge``, ``margin`` appears in them."""

    versioning = _section(README.read_text(encoding="utf-8"), PHASE2_VERSIONING_TITLE)
    for title in PHASE2_DEADLINE_SUBSECTIONS:
        subsection = _subsection(versioning, title)
        assert "**à** l'échéance de l'appel" in subsection, title
        assert _lead_hits(subsection) == [], (title, _lead_hits(subsection))


def test_the_phase_2_readme_checks_read_the_documents_shape() -> None:
    """The parsers and the two word checks themselves, on samples."""

    section = (
        "## Essais\n\n| Fournisseur | A | B |\n| --- | --- | --- |\n"
        "| One | x | y |\n| Two | | z |\n"
    )
    table = _table_cells(section)
    assert table == {"One": ["x", "y"], "Two": ["", "z"], "": ["Fournisseur", "A", "B"]}
    spec = "## Test failure allowlist\n\n- `tests/a.py::t[x]` — why\n- `tests/b.py::u` — why\n## Next\n- `tests/c.py::v`\n"
    assert _allowlisted_tests(spec) == ["tests/a.py::t[x]", "tests/b.py::u"]
    assert _lead_hits("stops at the deadline\nno Margin here\nune marge de 0,1 s\nthe lead") == [
        "no Margin here",
        "une marge de 0,1 s",
        "the lead",
    ]
    assert _lead_hits("à l'échéance, rien n'en est soustrait") == []
    assert SECRET_SETTING.search("password: hunter2")
    assert SECRET_SETTING.search("api_key: sk-1")
    for shape in ("password: ${OBS_PASSWORD}", "api_key: vide", "access_token: <jeton>"):
        assert not SECRET_SETTING.search(shape), shape


# --------------------------------------------------------------------------- #
# AC7, AC41 (phase 3)
# --------------------------------------------------------------------------- #


def _trial_row_problems(label: str, values: dict[str, str], marker: str) -> list[str]:
    """What is wrong with one trial row: an empty field, a commit without a
    7–40-hex hash, a secret value in the settings shape, an outcome that is
    neither a cited ``<marker>`` line nor "Non exécuté", a date that is not
    ``YYYY-MM-DD``."""

    problems = [f"{label}: {name} is empty" for name, value in values.items() if not value]
    if not re.search(r"`[0-9a-f]{7,40}`", values.get("Commit", "")):
        problems.append(f"{label}: no commit hash")
    if SECRET_SETTING.search(values.get("Forme des réglages", "")):
        problems.append(f"{label}: secret value in the settings shape")
    outcome = values.get("Résultat", "")
    if marker not in outcome and "Non exécuté" not in outcome:
        problems.append(f"{label}: outcome cites no {marker} line and is not 'Non exécuté'")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", values.get("Date", "")):
        problems.append(f"{label}: date is not YYYY-MM-DD")
    return problems


def _named_modules(cell: str, modules: set[str]) -> list[str]:
    """The shipped module names *cell* quotes as `` `name` ``, in order."""

    return [name for name in re.findall(r"`([a-z_]+)`", cell) if name in modules]


def _is_code(cell: str) -> bool:
    """A presence cell says "code" when it opens with that word."""

    return re.match(r"code\b", cell, re.IGNORECASE) is not None


def test_ac41_readme_records_the_phase_3_trials_and_versioning() -> None:
    """AC41 (phase 3 R8): the phase 3 trial table has exactly the six rows in
    the R8 order, each with 5 non-empty fields — a 7–40-hex commit hash, a
    settings shape with no secret value, an outcome citing a
    ``PHASE3-TRIAL`` line or "Non exécuté", a ``YYYY-MM-DD`` date; the
    section names ``tests/test_phase3_trials.py``; and the versioning
    subsection names all 6 tests of the phase 3 spec's allowlist, read from
    the archived spec itself."""

    readme = README.read_text(encoding="utf-8")
    section = _section(readme, PHASE3_SECTION_TITLE)
    assert PHASE3_TRIALS_FILE in section

    table = _table_cells(_subsection(section, PHASE3_TRIAL_TITLE))
    header = table.pop("")
    assert tuple(header[1:]) == PHASE3_TRIAL_FIELDS, header
    assert tuple(table) == PHASE3_TRIAL_ROWS, list(table)
    problems: list[str] = []
    for label, cells in table.items():
        assert len(cells) == len(PHASE3_TRIAL_FIELDS), (label, cells)
        values = dict(zip(PHASE3_TRIAL_FIELDS, cells))
        problems += _trial_row_problems(label, values, "PHASE3-TRIAL")
    assert problems == [], problems

    allowlisted = _allowlisted_tests(PHASE3_SPEC.read_text(encoding="utf-8"))
    assert len(allowlisted) == 6, allowlisted
    versioning = _subsection(section, PHASE3_VERSIONING_TITLE)
    missing = [test for test in allowlisted if f"`{test}`" not in versioning]
    assert missing == [], f"allowlisted tests the versioning subsection does not name: {missing}"


def test_ac7_readme_presence_table_says_configuration_or_code() -> None:
    """AC7 (phase 3 R1): the presence table has one row per presence item —
    at least the eleven of AC7, plus the redemptions, the clip-link
    announcement and the anonymous-gifter thanks — each "configuration ou
    code" cell non-empty, saying which of the two and naming the module;
    the chat-command polls and the channel-point redemptions say "code"."""

    section = _section(README.read_text(encoding="utf-8"), PHASE3_SECTION_TITLE)
    table = _table_cells(_subsection(section, PHASE3_PRESENCE_TITLE))
    header = table.pop("")
    assert PHASE3_PRESENCE_COLUMN in header, header
    column = header.index(PHASE3_PRESENCE_COLUMN) - 1
    assert len(table) >= 11, list(table)
    missing = [item for item in PHASE3_PRESENCE_ITEMS if item not in table]
    assert missing == [], f"presence items without a row: {missing}"

    modules = {child.name for child in MODULES_DIR.iterdir() if (child / "module.yaml").is_file()}
    for label, cells in table.items():
        assert len(cells) == len(header) - 1, (label, cells)
        assert all(cells), (label, cells)
        cell = cells[column]
        assert _is_code(cell) or cell.startswith("Configuration"), (label, cell)
        assert _named_modules(cell, modules), (label, "names no module", cell)
    for item in PHASE3_CODE_ITEMS:
        assert _is_code(table[item][column]), (item, table[item][column])


def test_the_phase_3_readme_checks_read_the_documents_shape() -> None:
    """The phase 3 parsers and row checks themselves, on samples: a trial
    table inside a ``###`` subsection of a ``##`` section, each kind of bad
    row, and the presence cell readers."""

    readme = (
        "## Phase 3 — x\n\nIntro naming tests/test_phase3_trials.py.\n\n"
        "### Other\n\n| A | B |\n| --- | --- |\n| a | b |\n\n"
        "### Essais\n\n| Essai | Commit | Résultat |\n| --- | --- | --- |\n"
        "| one | `abc1234` | ok |\n\n#### Deeper\nstill inside\n\n## Next\n| z | z |\n"
    )
    section = _section(readme, "Phase 3")
    assert "## Next" not in section
    assert _table_cells(_subsection(section, "Essais")) == {
        "one": ["`abc1234`", "ok"],
        "": ["Essai", "Commit", "Résultat"],
    }

    good = {
        "Commit": "`0c170ef` (P24)",
        "Forme des réglages": "`x {client_secret: ${X_SECRET}, refresh_token: ${X_TOKEN}}`",
        "Résultat": "Exécuté : `PHASE3-TRIAL x commit=0c170ef outcome=success date=2026-09-27`",
        "Limites": "none measured",
        "Date": "2026-09-27",
    }
    assert _trial_row_problems("x", good, "PHASE3-TRIAL") == []
    assert _trial_row_problems("x", good | {"Résultat": "**Non exécuté** — why"}, "PHASE3-TRIAL") == []
    for field, value in (
        ("Commit", "0c170ef"),
        ("Commit", "`0c17`"),
        ("Forme des réglages", "access_token: abc"),
        ("Résultat", "PHASE2-TRIAL x"),
        ("Date", "27/09/2026"),
        ("Limites", ""),
    ):
        assert len(_trial_row_problems("x", good | {field: value}, "PHASE3-TRIAL")) == 1, field

    modules = {"clips", "brain", "twitch"}
    assert _named_modules("Configuration — `clips` et `brain` (route `clip`)", modules) == ["clips", "brain"]
    assert _named_modules("Configuration — route `thanks`", modules) == []
    assert _is_code("Code — `twitch`") and _is_code("code seulement")
    assert not _is_code("Configuration — `brain`") and not _is_code("Codec")


# --------------------------------------------------------------------------- #
# Target list (gate finding F5)
# --------------------------------------------------------------------------- #


def _spec_targets(text: str) -> list[str]:
    """The ``file:`` entries of the spec's ``targets`` front matter, in order."""

    match = re.match(r"---\n(.*?)\n---\n", text, re.DOTALL)
    assert match is not None, "the spec has no front matter"
    return re.findall(r"^  - file: (\S+)$", match.group(1), re.MULTILINE)


def _subsection(markdown: str, title: str) -> str:
    """The body of the ``###`` subsection titled *title*, up to the next
    heading of level three or above."""

    lines = markdown.splitlines()
    starts = [index for index, line in enumerate(lines) if line.startswith("### ") and title in line]
    assert len(starts) == 1, f"expected exactly one subsection titled {title!r}, found {len(starts)}"
    start = starts[0]
    end = next(
        (index for index in range(start + 1, len(lines)) if re.match(r"^#{1,3} ", lines[index])),
        len(lines),
    )
    return "\n".join(lines[start:end])


def _plan_files(text: str) -> dict[str, list[str]]:
    """Every file a plan step's ``**Files:**`` line names, by step title."""

    steps: dict[str, list[str]] = {}
    title = ""
    for line in text.splitlines():
        heading = re.match(r"^### (P\d+[A-Z0-9]*):", line)
        if heading:
            title = heading.group(1)
            continue
        files = re.match(r"^- \*\*Files:\*\* \[(.*)\]$", line)
        if files and title:
            steps.setdefault(title, []).extend(
                name.strip().strip("`") for name in files.group(1).split(",") if name.strip()
            )
    return steps


def test_the_spec_target_list_covers_every_file_the_plan_edits() -> None:
    """Gate finding F5: the spec's ``targets`` list is the authority on the
    files the phase touches, so each file a plan step names is a target,
    inside the permitted scope the spec states, or one the spec records as
    planned but untouched — and every target exists on disk."""

    spec = SPEC.read_text(encoding="utf-8")
    targets = _spec_targets(spec)
    assert targets, "the spec's front matter names no target"
    assert len(targets) == len(set(targets)), "a target is listed twice"
    missing = [name for name in targets if not (ROOT / name).is_file()]
    assert missing == [], f"targets the tree does not have: {missing}"

    section = _subsection(spec, SCOPE_SECTION_TITLE)
    assert f"`{PERMITTED_SCOPE}`" in section, "the spec does not state the permitted scope"
    assert "`docs/design-v2.md`" in section, "the design authority is not named as untouched"
    for name in PLANNED_BUT_UNTOUCHED:
        assert f"`{name}`" in section, f"{name} is neither a target nor recorded as untouched"

    steps = _plan_files(PLAN.read_text(encoding="utf-8"))
    assert len(steps) >= 24, sorted(steps)
    covered = set(targets)
    outside: list[str] = []
    for step, names in steps.items():
        for name in names:
            resolved = ROOT_DOCUMENT_ALIASES.get(name, name)
            if resolved in covered or resolved.startswith(PERMITTED_SCOPE):
                continue
            if name in PLANNED_BUT_UNTOUCHED:
                continue
            outside.append(f"{step}: {name}")
    assert outside == [], "plan files outside the spec's targets:\n" + "\n".join(outside)


def test_the_target_list_parsers_read_the_documents_shape(tmp_path: Path) -> None:
    spec = "---\nname: x\ntargets:\n  - file: a/b.py\n    description: \"one\"\n  - file: c.md\n    description: \"two\"\n---\n\n# Body\n  - file: not/front/matter.py\n"
    assert _spec_targets(spec) == ["a/b.py", "c.md"]
    plan = "### P1: First\n- **Files:** [`a/b.py`, `c.md`]\n- **Tests:** none\n### P2: Second\n- **Files:** [`d.py`]\n"
    assert _plan_files(plan) == {"P1": ["a/b.py", "c.md"], "P2": ["d.py"]}
    body = "## Top\n\n### Other\nx\n\n### Scope here\nline one\n\n#### Deeper\nstill inside\n\n### Next\nout\n"
    assert _subsection(body, "Scope here") == "### Scope here\nline one\n\n#### Deeper\nstill inside\n"
