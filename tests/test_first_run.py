"""Phase 9: the first run (R1–R8).

P1 (plan DD1, DD2): the per-user locations of A1 and A2, and the two core
platform scans adapted to skip exactly the three paths that hold the
platform-named bytes phase 9 places under ``core/`` (flagged out-of-list).

P3 (plan DD4): the string table and its loader (R2; AC5, AC6's discovery
half), with the AST collector of the string keys ``wizard.py`` and
``console.py`` reference, run again by P12–P15 as keys appear.

P4 (plan DD5): the four ready-made profiles and their generator (R3; AC7's
data half, AC8, AC10), validated against the shipped manifests themselves.

P5 (A1, A7, R4): the credential store and the wizard state file (AC11's
storage half, AC30's state-file clause), with the platform and environment
forced, so the Windows branch runs on any host.

P6 (R1): ``--config`` optional (AC1), the startup collisions of the added
files (AC1), the landing routing (AC2's routing half: the redirect and its
session cookie; P12 and P13 follow it to the pages) and the injected browser
opener (AC3). The editor's pre-phase markup is the fixture
``tests/fixtures/phase9/editor_base_page.html``, captured from the code before
P6 changed any render.

P8 (R1): the instance record, the ``/instance`` route and the second launch
(AC31 with a fake opener, serve, liveness test, probe and clock; AC4's
``/instance`` row). The probe fake answers through the first instance's own
``ConfigUI.handle``, so the token check it passes is the route's.

P9 (R1, A3): the OS launcher writer and ``--install-launcher`` (AC27), with
the platform forced and every launcher directory redirected, so each
platform's format is written and checked on any host.

P10 (R5 screens 2–3, D3): the platform, reward-list and model validators
(AC14, AC15, AC17's validator half) behind fake transports on the manual
clock, their failure map swept for traces, class names and submitted values,
and the production transport against a fake ``aiohttp`` session.

P11 (R5 screen 5, DD6): ``core.main.dry_run`` over each generated profile
with a fake model answering ``"bonjour"`` (AC9, AC29's record half): the run
id read back against the scheduler's record and the feed's run entries, the
platform's sessions inert and never asked anything, no process started.

P12 (R5, A7, R2, R4): the five wizard screens through ``ConfigUI.handle``
with fake platform and model endpoints, a fake (and, for AC29, the real)
dry-run entry and the manual clock: the order gating and its fingerprint
table (AC13, the replaced-credential case and the state-file sweep of every
HMAC), the language choice (AC6, AC7), screen 4's refusals, check and
read-only view (AC16, AC11's response sweep), the failure pages (AC17),
regeneration of the wizard's own files (AC28), screen 5's record (AC29) and
the re-entry after a restart (AC30).

P16 (R8): the package data of the string tables and the profiles, and the
runtime dependency set, read from ``pyproject.toml`` in process (AC26's
pyproject half; the installed-distribution node in ``test_profiles.py``
asserts the shipped bytes).

P17 (R8): the first-run guide pair (AC24's content half; the pair's
discovery, shape and identifiers are ``test_docs_bilingual.py``'s): the
default location and the credential directory on each platform, spelled
from :mod:`core.config_ui.locations`, the credential file's name, the
install path and ``--install-launcher``, and the documentation index's links.

P18 (R8, A3b): the trial-record template and its approval checker (AC25):
``trial_record_problems`` returns every reason the phase gate refuses an
approval. The fifteen-minute claim rests on the operator's record only (D1);
no test here claims it.
"""

from __future__ import annotations

import ast
import asyncio
import builtins
import hashlib
import io
import json
import os
import re
import secrets
import shlex
import shutil
import stat
import sys
import threading
import webbrowser
from datetime import datetime
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
import yaml

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - the 3.10 floor reads through the backport
    import tomli as tomllib

import core.config_ui as config_ui
from conftest import (
    FakeResponse,
    FakeSession,
    HeldSession,
    ManualClock,
    baseline_numstat,
    final,
)
from core import main as application
from core.admission import AdmissionScheduler
from core.config_ui import i18n, launcher, locations, wizard
from core.config_ui.i18n import StringTable, StringTableError
from core.config_ui.locations import (
    PRODUCT,
    UNKNOWN_VERSION,
    credential_directory,
    credential_file_name,
    default_profile_path,
    product_version,
)
from core.overlay import FEED_FILE_VARIABLE
from test_agentic_loop import PLATFORM_SCAN_EXEMPT, core_twitch_scan
from test_youtube import BROAD_SCAN_EXEMPT, broad_core_scan

# --------------------------------------------------------------------------- #
# A1, A2: the per-user locations (AC1's path half, AC11's naming)
# --------------------------------------------------------------------------- #


def test_the_product_directory_is_the_distribution_name() -> None:
    assert PRODUCT == "twitch-ia-compagnon"


def test_ac1_linux_default_profile_under_xdg_config_home(tmp_path: Path) -> None:
    """AC1 (A2): with ``XDG_CONFIG_HOME=<tmp>`` the default profile is
    ``<tmp>/twitch-ia-compagnon/profile.yaml``."""

    environ = {"XDG_CONFIG_HOME": str(tmp_path), "HOME": str(tmp_path / "elsewhere")}
    assert default_profile_path("linux", environ) == (
        tmp_path / "twitch-ia-compagnon" / "profile.yaml"
    )


def test_ac1_linux_default_profile_falls_back_to_home_config(tmp_path: Path) -> None:
    """AC1 (A2): with ``XDG_CONFIG_HOME`` unset and ``HOME=<tmp>`` the
    default is ``<tmp>/.config/twitch-ia-compagnon/profile.yaml``; an empty
    or relative ``XDG_CONFIG_HOME`` is ignored as the XDG rules say."""

    expected = tmp_path / ".config" / "twitch-ia-compagnon" / "profile.yaml"
    assert default_profile_path("linux", {"HOME": str(tmp_path)}) == expected
    for ignored in ("", "relative/dir"):
        environ = {"HOME": str(tmp_path), "XDG_CONFIG_HOME": ignored}
        assert default_profile_path("linux", environ) == expected


def test_a2_windows_default_profile_under_appdata(tmp_path: Path) -> None:
    environ = {"APPDATA": str(tmp_path), "LOCALAPPDATA": str(tmp_path / "local")}
    assert default_profile_path("windows", environ) == (
        tmp_path / "twitch-ia-compagnon" / "profile.yaml"
    )


def test_a2_macos_default_profile_under_application_support(tmp_path: Path) -> None:
    environ = {"HOME": str(tmp_path), "XDG_CONFIG_HOME": str(tmp_path / "xdg")}
    assert default_profile_path("macos", environ) == (
        tmp_path / "Library" / "Application Support" / "twitch-ia-compagnon" / "profile.yaml"
    )


def test_a1_credential_directory_per_platform(tmp_path: Path) -> None:
    """A1: Linux and macOS use the A2 per-user directory; Windows uses
    ``%LOCALAPPDATA%\\twitch-ia-compagnon\\`` (not ``APPDATA``), and names
    none when ``LOCALAPPDATA`` is unset."""

    assert credential_directory("linux", {"XDG_CONFIG_HOME": str(tmp_path)}) == (
        tmp_path / "twitch-ia-compagnon"
    )
    assert credential_directory("linux", {"HOME": str(tmp_path)}) == (
        tmp_path / ".config" / "twitch-ia-compagnon"
    )
    assert credential_directory("macos", {"HOME": str(tmp_path)}) == (
        tmp_path / "Library" / "Application Support" / "twitch-ia-compagnon"
    )
    windows = {"LOCALAPPDATA": str(tmp_path / "local"), "APPDATA": str(tmp_path / "roaming")}
    assert credential_directory("windows", windows) == tmp_path / "local" / "twitch-ia-compagnon"
    assert credential_directory("windows", {"APPDATA": str(tmp_path / "roaming")}) is None
    assert credential_directory("windows", {"LOCALAPPDATA": ""}) is None


def test_ac11_credential_file_named_from_the_full_profile_file_name(tmp_path: Path) -> None:
    """AC11 (A1): ``<profile-file-name>.credentials.json``; ``a.yaml`` and
    ``a.yml`` map to two distinct files."""

    assert credential_file_name(tmp_path / "profile.yaml") == "profile.yaml.credentials.json"
    yaml_name = credential_file_name(tmp_path / "a.yaml")
    yml_name = credential_file_name(str(tmp_path / "a.yml"))
    assert (yaml_name, yml_name) == ("a.yaml.credentials.json", "a.yml.credentials.json")
    assert yaml_name != yml_name
    # Profiles in different directories with one file name share the name:
    # A1's digest check, not the name, keeps them apart.
    assert credential_file_name(tmp_path / "x" / "a.yaml") == yaml_name


def test_product_version_reads_the_installed_distribution(monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[str] = []

    def version(name: str) -> str:
        asked.append(name)
        return "9.8.7"

    monkeypatch.setattr(locations.metadata, "version", version)
    assert product_version() == "9.8.7"
    assert asked == ["twitch-ia-compagnon"]


def test_product_version_has_a_stable_fallback_when_not_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing(name: str) -> str:
        raise locations.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(locations.metadata, "version", missing)
    assert product_version() == UNKNOWN_VERSION == product_version()
    assert UNKNOWN_VERSION == "0+unknown"


def test_running_platform_names_one_of_the_three() -> None:
    assert locations.running_platform() in {"linux", "windows", "macos"}


# --------------------------------------------------------------------------- #
# DD1: the two adapted core scans, proved on a tmp tree
# --------------------------------------------------------------------------- #

#: Fixture contents matched to each scan's contract: the lowercase word
#: satisfies AC31's case-sensitive substring and the broad scan's
#: case-insensitive word; the capitalised one only the broad scan's.
LOWERCASE = "reads the twitch chat\n"
CAPITALISED = "the Twitch companion\n"

EXEMPT_FILES = (
    "core/config_ui/locations.py",
    "core/config_ui/profiles/chat_qa.json",
    "core/config_ui/strings/en.json",
)


def _tree(root: Path, files: dict[str, str]) -> Path:
    for relative, text in {"core/main.py": "neutral\n", **files}.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def test_both_scans_pin_the_same_three_exempt_paths() -> None:
    expected = {
        "core/config_ui/locations.py",
        "core/config_ui/profiles/",
        "core/config_ui/strings/",
    }
    assert PLATFORM_SCAN_EXEMPT == BROAD_SCAN_EXEMPT == expected


def test_a_lowercase_platform_word_outside_the_exemption_fails_both_scans(tmp_path: Path) -> None:
    root = _tree(tmp_path, {"core/config_ui/wizard.py": LOWERCASE})
    matches, read = core_twitch_scan(root)
    hits, broad_read = broad_core_scan(root)
    # The AC31 scan reports grep's native ``<path>:<line>``; the whole string
    # is compared, so a drive-qualified path is never split at its colon.
    assert matches == [f"{root / 'core' / 'config_ui' / 'wizard.py'}:{LOWERCASE.rstrip()}"]
    assert hits == ["core/config_ui/wizard.py:1"]
    assert read == broad_read == sorted([root / "core/config_ui/wizard.py", root / "core/main.py"])


def test_a_capitalised_platform_word_outside_the_exemption_fails_the_broad_scan(
    tmp_path: Path,
) -> None:
    """The broad scan keeps its case-insensitivity; the case-sensitive AC31
    scan is not claimed to catch this fixture."""

    root = _tree(tmp_path, {"core/overlay.py": CAPITALISED})
    hits, read = broad_core_scan(root)
    assert hits == ["core/overlay.py:1"]
    assert root / "core/overlay.py" in read


@pytest.mark.parametrize("text", [LOWERCASE, CAPITALISED])
def test_the_same_fixtures_inside_the_exemption_fail_neither_scan(
    tmp_path: Path, text: str
) -> None:
    root = _tree(tmp_path, {relative: text for relative in EXEMPT_FILES})
    matches, read = core_twitch_scan(root)
    hits, broad_read = broad_core_scan(root)
    assert matches == [] and hits == []
    # Only the exempt files were skipped: the neutral file is still read.
    assert read == broad_read == [root / "core/main.py"]


def test_a_file_beside_an_exempt_directory_is_still_scanned(tmp_path: Path) -> None:
    """The directory exemptions cover their contents only, never a sibling
    whose name merely starts the same way."""

    root = _tree(
        tmp_path,
        {"core/config_ui/profiles.py": LOWERCASE, "core/config_ui/locations.pyc.txt": LOWERCASE},
    )
    matches, _ = core_twitch_scan(root)
    hits, _ = broad_core_scan(root)
    assert len(matches) == 2
    assert sorted(hits) == ["core/config_ui/locations.pyc.txt:1", "core/config_ui/profiles.py:1"]


def test_both_adapted_scans_pass_on_the_repository() -> None:
    root = Path(__file__).resolve().parents[1]
    matches, read = core_twitch_scan(root)
    hits, broad_read = broad_core_scan(root)
    assert matches == [] and hits == []
    assert root / "core" / "config_ui" / "locations.py" not in read
    assert root / "core" / "config_ui" / "__init__.py" in read
    assert set(read) == set(broad_read)


# --------------------------------------------------------------------------- #
# R2 (plan DD4): the string table — AC5, AC6's discovery half
# --------------------------------------------------------------------------- #

ROOT = Path(__file__).resolve().parents[1]
CONFIG_UI = ROOT / "core" / "config_ui"
STRINGS = CONFIG_UI / "strings"

#: The sources whose string keys AC5 checks (P12–P15 create them).
KEY_SOURCES = (CONFIG_UI / "wizard.py", CONFIG_UI / "console.py")

#: Functions of the key sources allowed to build a key at run time: only the
#: reason-token mapping, whose coverage AC20 checks separately (P13 adds it).
DYNAMIC_KEY_EXEMPT: frozenset[str] = frozenset()


def _string_files() -> dict[str, dict[str, str]]:
    return {
        code: json.loads((STRINGS / f"{code}.json").read_text(encoding="utf-8"))
        for code in ("en", "fr")
    }


def referenced_keys(paths: tuple[Path, ...] | list[Path]) -> tuple[set[str], list[str]]:
    """The string keys *paths* reference, and every dynamic key site.

    A key is referenced by a call to ``sentence`` (``sentence(language, key,
    ...)``, any receiver) or to a ``_t`` helper (``_t(key, ...)``), positionally
    or as ``key=``. A key that is not a string literal is a dynamic site,
    reported as ``<file>:<line>`` unless it sits in a function named in
    :data:`DYNAMIC_KEY_EXEMPT`: a collector that skipped them would report a
    false 0 missing.
    """

    keys: set[str] = set()
    dynamic: list[str] = []
    for path in paths:
        if not path.exists():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        functions = [
            node for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        exempt_lines = {
            line
            for function in functions
            if function.name in DYNAMIC_KEY_EXEMPT
            for line in range(function.lineno, (function.end_lineno or function.lineno) + 1)
        }
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if name not in {"sentence", "_t"}:
                continue
            position = 1 if name == "sentence" else 0
            key = next((kw.value for kw in node.keywords if kw.arg == "key"), None)
            if key is None and len(node.args) > position:
                key = node.args[position]
                if any(isinstance(arg, ast.Starred) for arg in node.args[: position + 1]):
                    key = None
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                keys.add(key.value)
            elif node.lineno not in exempt_lines:
                dynamic.append(f"{path.name}:{node.lineno}")
    return keys, dynamic


def test_ac5_the_shipped_string_files_have_equal_complete_key_sets() -> None:
    """AC5: both files parse, have equal key sets of at least 1 key, every
    value is a non-empty string and every key's placeholder set is equal."""

    files = _string_files()
    english, french = files["en"], files["fr"]
    assert len(english) >= 1
    assert english.keys() == french.keys()
    for sentences in files.values():
        assert all(isinstance(value, str) and value.strip() for value in sentences.values())
    for key in english:
        assert i18n.placeholders(english[key]) == i18n.placeholders(french[key]), key
    table = StringTable.load()
    assert table.languages() == [("en", "English"), ("fr", "Français")]
    assert dict(table.refused) == {}


def test_ac5_the_shipped_files_hold_the_phase_sentences() -> None:
    """R2: the four profiles' names and descriptions, the generic reason
    sentence and the raw-detail disclosure label are in the table."""

    keys = _string_files()["en"].keys()
    for profile in ("chat_qa", "channel_points", "spoken_keyword", "silent"):
        assert {f"profile.{profile}.name", f"profile.{profile}.description"} <= keys
    assert {i18n.NAME_KEY, i18n.UNKNOWN_REASON_KEY, "detail.disclosure"} <= keys


def test_the_strings_name_no_other_platform_nor_vendor() -> None:
    """AC39 still scans the strings for the two other platforms; the
    vendor words are refused here too (plan P3 risks)."""

    for path in sorted(STRINGS.glob("*.json")):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"\b(kick|youtube|openai|anthropic|gemini)\b", text, re.I), path


def test_ac5_every_key_referenced_by_the_wizard_and_console_exists() -> None:
    """AC5: 0 missing keys over ``wizard.py`` and ``console.py`` (whichever
    exist yet), and 0 dynamically built keys outside the exemption."""

    keys, dynamic = referenced_keys(KEY_SOURCES)
    files = _string_files()
    assert dynamic == []
    for code, sentences in files.items():
        assert sorted(keys - sentences.keys()) == [], code


def test_the_key_collector_reads_literals_and_flags_dynamic_keys(tmp_path: Path) -> None:
    source = tmp_path / "wizard.py"
    source.write_text(
        "def page(table, language, token):\n"
        "    table.sentence(language, 'a.one', name=1)\n"
        "    sentence(language, key='a.two')\n"
        "    _t('a.three')\n"
        "    _t(key='a.four', x=2)\n"
        "    table.sentence(language, 'reason.' + token)\n"
        "    _t(f'b.{token}')\n"
        "    table.sentence(*arguments)\n"
        "    unrelated('not.a.key')\n",
        encoding="utf-8",
    )
    keys, dynamic = referenced_keys([source, tmp_path / "console.py"])
    assert keys == {"a.one", "a.two", "a.three", "a.four"}
    assert dynamic == ["wizard.py:6", "wizard.py:7", "wizard.py:8"]
    # A missing key is reported against the shipped table.
    assert sorted(keys - _string_files()["en"].keys()) == ["a.four", "a.one", "a.three", "a.two"]


def test_the_key_collector_honours_only_the_named_exemption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "console.py"
    source.write_text(
        "def reason_sentence(table, language, token):\n"
        "    return table.sentence(language, 'reason.' + token)\n"
        "def other(table, language, token):\n"
        "    return table.sentence(language, 'reason.' + token)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sys.modules[__name__], "DYNAMIC_KEY_EXEMPT", frozenset({"reason_sentence"}))
    assert referenced_keys([source]) == (set(), ["console.py:4"])


def _tree_digest(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts
    }


def _fixture_strings(tmp_path: Path) -> Path:
    directory = tmp_path / "strings"
    shutil.copytree(STRINGS, directory)
    return directory


def test_ac6_a_copied_language_file_is_offered_with_no_source_change(tmp_path: Path) -> None:
    """AC6 (discovery half): ``en.json`` copied as ``de.json`` into a fixture
    strings directory offers ``de``, and 0 source files change."""

    before = _tree_digest(CONFIG_UI)
    directory = _fixture_strings(tmp_path)
    assert "de" not in dict(StringTable.load(directory).languages())
    shutil.copyfile(directory / "en.json", directory / "de.json")
    table = StringTable.load(directory)
    assert [code for code, _ in table.languages()] == ["de", "en", "fr"]
    assert table.offers("de") and dict(table.refused) == {}
    assert table.sentence("de", "profile.silent.name") == "Silent"
    assert _tree_digest(CONFIG_UI) == before


def test_ac6_deleting_one_key_withdraws_the_language(tmp_path: Path) -> None:
    directory = _fixture_strings(tmp_path)
    copy = json.loads((directory / "en.json").read_text(encoding="utf-8"))
    removed = sorted(copy)[0]
    del copy[removed]
    (directory / "de.json").write_text(json.dumps(copy), encoding="utf-8")
    table = StringTable.load(directory)
    assert [code for code, _ in table.languages()] == ["en", "fr"]
    assert removed in table.refused["de"]
    assert table.language_or_default("de") == "en"
    assert table.language_or_default("fr") == "fr"
    assert table.language_or_default(None) == "en"


def test_ac6_a_mismatched_placeholder_withdraws_the_language(tmp_path: Path) -> None:
    directory = _fixture_strings(tmp_path)
    copy = json.loads((directory / "en.json").read_text(encoding="utf-8"))
    copy[i18n.UNKNOWN_REASON_KEY] = "Refused: {reason}."
    (directory / "de.json").write_text(json.dumps(copy), encoding="utf-8")
    table = StringTable.load(directory)
    assert not table.offers("de")
    assert "placeholders differ" in table.refused["de"]


@pytest.mark.parametrize(
    "mutation",
    [
        lambda copy: copy.update({"language.name": ""}),
        lambda copy: copy.update({"language.name": "   "}),
        lambda copy: copy.update({"language.name": 7}),
        lambda copy: copy.update({"extra.key": "More"}),
        lambda copy: copy.update({i18n.UNKNOWN_REASON_KEY: "Refused {token"}),
        lambda copy: copy.update({i18n.UNKNOWN_REASON_KEY: "Refused {token.__class__}"}),
    ],
    ids=["empty", "blank", "not-a-string", "extra-key", "unbalanced", "attribute"],
)
def test_an_incomplete_or_malformed_language_is_not_offered(tmp_path: Path, mutation) -> None:
    directory = _fixture_strings(tmp_path)
    copy = json.loads((directory / "en.json").read_text(encoding="utf-8"))
    mutation(copy)
    (directory / "de.json").write_text(json.dumps(copy), encoding="utf-8")
    (directory / "xx.json").write_text("{not json", encoding="utf-8")
    table = StringTable.load(directory)
    assert [code for code, _ in table.languages()] == ["en", "fr"]
    assert set(table.refused) == {"de", "xx"}


def test_the_reference_file_must_be_present_and_well_formed(tmp_path: Path) -> None:
    directory = _fixture_strings(tmp_path)
    (directory / "en.json").unlink()
    with pytest.raises(StringTableError, match="en.json"):
        StringTable.load(directory)
    (directory / "en.json").write_text(json.dumps({"language.name": "English"}), encoding="utf-8")
    with pytest.raises(StringTableError, match="reason.unknown"):
        StringTable.load(directory)


def test_sentence_formats_with_exactly_the_declared_placeholders() -> None:
    table = StringTable.load()
    assert table.sentence("fr", "profile.chat_qa.name") == "Questions du chat"
    with pytest.raises(StringTableError, match="given"):
        table.sentence("en", "profile.chat_qa.name", channel="x")
    with pytest.raises(StringTableError, match="given"):
        table.sentence("en", i18n.UNKNOWN_REASON_KEY)
    with pytest.raises(StringTableError, match="given"):
        table.sentence("en", i18n.UNKNOWN_REASON_KEY, token="t", extra="e")
    with pytest.raises(StringTableError, match="not offered"):
        table.sentence("de", "profile.chat_qa.name")
    with pytest.raises(StringTableError, match="unknown string key"):
        table.sentence("en", "no.such.key")


def test_a_value_is_quoted_data_never_a_format_template() -> None:
    table = StringTable.load()
    hostile = "{token} {0} {__class__}"
    assert hostile in table.unknown_reason("en", hostile)


@pytest.mark.parametrize("language", ["en", "fr"])
def test_unknown_reason_names_the_token(language: str) -> None:
    """The generic sentence AC20 renders for ``zz_unknown`` (P13 asserts it
    on the console)."""

    sentence = StringTable.load().unknown_reason(language, "zz_unknown")
    assert "zz_unknown" in sentence
    assert sentence.replace("zz_unknown", "").strip()


# --------------------------------------------------------------------------- #
# R3 (plan DD5): the four ready-made profiles — AC7's data half, AC8, AC10
# --------------------------------------------------------------------------- #

PROFILES = CONFIG_UI / "profiles"
MODULES = ROOT / "modules"

#: AC7: the four profiles, in the order the choice screen offers them.
PROFILE_ORDER = ("chat_qa", "channel_points", "spoken_keyword", "silent")

#: The broadcaster and bot identities, as the platform's validation answer
#: hands them over (P10): parameters of the generator, never typed.
FIXTURE_IDENTITIES = {"broadcaster_id": "41245072", "bot_user_id": "90210417"}

#: The stored credentials, by reference name: none may reach a profile file.
FIXTURE_CREDENTIALS = {
    "TWITCH_CLIENT_ID": "fixture-client-id-q8r",
    "TWITCH_CLIENT_SECRET": "fixture-client-secret-k2v",
    "TWITCH_ACCESS_TOKEN": "tok-XYZ-fixture-access-token",
    "MODEL_API_KEY": "fixture-model-key-77w",
    "TRANSCRIPTION_API_KEY": "fixture-transcription-key-3jd",
}

_MODEL = {"model_endpoint": "https://model.invalid/v1", "model_name": "fixture-model"}

#: The streamer's answers per profile (R3: companion name; reward; wake word,
#: detector and transcription settings; none).
FIXTURE_PARAMETERS: dict[str, dict[str, Any]] = {
    "chat_qa": {"companion_name": "Nova", **_MODEL},
    "channel_points": {"reward_id": "5f1c0d7e-reward-fixture", **_MODEL},
    "spoken_keyword": {
        "wake_word": "hey nova",
        "recorder": ["recorder", "--wav", "-"],
        "detector": {"kind": "command", "argv": ["detector", "--phrase", "hey nova"]},
        "transcription_endpoint": "https://transcription.invalid/v1",
        "transcription_model": "fixture-transcriber",
        **_MODEL,
    },
    "silent": {},
}

#: The four shipped ``*.example`` profiles as they are at ``cb0f5b1``
#: (SHA-256 of ``git show cb0f5b1:<file>``, taken when this test was written).
EXAMPLES_AT_CB0F5B1 = {
    "config.yaml.example": "6589ef82efdddc191e768c06bd6aca26d3d5e7a3a495bb75bed822d35fddd65a",
    "config.server.yaml.example": "ae6ea176b28cae5a0e720d7150ad263dae7539fb1a66dee9546587b737990aa5",
    "agent.yaml.example": "32805b564f01cf719240b9a72f588f9577885fef410ee95fe9acb69a07fbab69",
    "presence.yaml.example": "427a78b166ab657dfe22dde8f1ff18deb6521bc576945819de046beae997425d",
}

_REFERENCE = re.compile(r"\$\{([^}]*)\}")


def _manifests() -> dict[str, dict[str, Any]]:
    """The shipped manifests, read from the real ``modules/`` directory."""

    manifests = {}
    for path in sorted(MODULES.glob("*/module.yaml")):
        manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
        manifests[manifest["name"]] = manifest
    return manifests


def _trigger_types(manifest: dict[str, Any]) -> set[str]:
    return {entry["name"] for entry in (manifest.get("triggers") or {}).get("types", [])}


def _declared_event_kinds(manifests: dict[str, dict[str, Any]]) -> set[str]:
    """Every event kind a shipped manifest declares: a declared type's
    ``kinds`` and the ``event_kind`` type's enumeration."""

    kinds: set[str] = set()
    for manifest in manifests.values():
        for entry in (manifest.get("triggers") or {}).get("types", []):
            kinds.update(entry.get("kinds", []))
            if entry["name"] == "event_kind":
                schema = entry["parameter_schema"]["properties"]["kinds"]["items"]
                kinds.update(schema.get("enum", []))
    return kinds


def _value_at(document: Any, dotted: str) -> Any:
    for key in dotted.split("."):
        if not isinstance(document, dict) or key not in document:
            return None
        document = document[key]
    return document


def _profiles() -> dict[str, wizard.Profile]:
    return {profile.id: profile for profile in wizard.load_profiles()}


def _generated(profile_id: str) -> dict[str, Any]:
    profile = _profiles()[profile_id]
    return wizard.generate_profile(
        profile, FIXTURE_PARAMETERS[profile_id], dict(FIXTURE_IDENTITIES)
    )


def _write(tmp_path: Path, profile_id: str) -> Path:
    path = tmp_path / f"{profile_id}.yaml"
    path.write_text(wizard.render_profile_yaml(_generated(profile_id)), encoding="utf-8")
    return path


def _stored_credentials(profile_id: str) -> dict[str, str]:
    return {
        credential.name: FIXTURE_CREDENTIALS[credential.name]
        for credential in _profiles()[profile_id].credentials
        if credential.parameter is None
    }


def test_ac7_four_profiles_load_in_the_stated_order() -> None:
    """AC7 (data half): exactly 4 profiles, chat Q&A, channel points, spoken
    keyword, silent, from the shipped package-data directory."""

    profiles = wizard.load_profiles()
    assert tuple(profile.id for profile in profiles) == PROFILE_ORDER
    assert wizard.load_profiles(PROFILES) == profiles
    assert sorted(path.name for path in PROFILES.iterdir()) == sorted(
        f"{name}.json" for name in PROFILE_ORDER
    )


@pytest.mark.parametrize("language", ["en", "fr"])
def test_ac7_each_profile_names_and_describes_itself_in_both_languages(language: str) -> None:
    """AC7 (data half): each profile's name and description keys are in the
    string table, with a non-empty sentence, in English and in French."""

    table = StringTable.load()
    for profile in wizard.load_profiles():
        assert profile.name_key == f"profile.{profile.id}.name"
        assert profile.description_key == f"profile.{profile.id}.description"
        for key in (profile.name_key, profile.description_key):
            assert table.sentence(language, key).strip(), (profile.id, key)


def test_r3_each_profile_declares_its_parameters_and_data() -> None:
    """R3: the parameters the streamer supplies (companion name; reward;
    wake word, detector and transcription settings; none — the model screen's
    endpoint and model aside), the identities taken from the validation
    answer, the scopes, the endpoints and the dry run's event and outcome."""

    profiles = _profiles()
    choice = {
        profile_id: [p.name for p in profile.parameters if p.screen != "model"]
        for profile_id, profile in profiles.items()
    }
    assert choice == {
        "chat_qa": ["companion_name"],
        "channel_points": ["reward_id"],
        "spoken_keyword": [
            "wake_word", "recorder", "detector", "transcription_endpoint", "transcription_model",
        ],
        "silent": [],
    }
    assert profiles["channel_points"].parameter("reward_id").source == "reward_list"
    for profile_id, profile in profiles.items():
        model = [p.name for p in profile.parameters if p.screen == "model"]
        assert profile.model_needed is (profile_id != "silent")
        assert model == (["model_endpoint", "model_name"] if profile.model_needed else [])
        assert profile.identities == ("broadcaster_id", "bot_user_id")
        assert profile.platform_module in _manifests()
        assert profile.validation_endpoint.startswith("https://")
        assert profile.reward_list_endpoint.startswith("https://")
        assert "user:read:chat" in profile.scopes
    assert "channel:read:redemptions" in profiles["channel_points"].scopes
    assert "channel:read:redemptions" not in profiles["chat_qa"].scopes
    assert {
        profile_id: (profile.dry_run_event_kind, profile.dry_run_outcome)
        for profile_id, profile in profiles.items()
    } == {
        "chat_qa": ("message", "completed"),
        "channel_points": ("reward", "completed"),
        "spoken_keyword": ("speech", "completed"),
        "silent": ("message", "not_triggered"),
    }


@pytest.mark.parametrize("profile_id", PROFILE_ORDER)
def test_r3_credentials_are_exactly_the_enabled_manifests_declared_ones(profile_id: str) -> None:
    """R3: the profile's credential references fill exactly the settings
    the enabled modules' manifests declare as credentials and the template
    sets; any other declared credential setting is absent. An optional
    (parameter) credential is written only when supplied, so it is listed
    apart: the spoken keyword's HTTP detector key."""

    manifests = _manifests()
    document = _generated(profile_id)
    expected: set[tuple[str, str]] = set()
    for module in document["enabled_modules"]:
        settings = document["modules"].get(module, {})
        for setting in manifests[module].get("credentials", []):
            value = _value_at(settings, setting)
            if value is None:
                continue
            assert isinstance(value, str) and _REFERENCE.fullmatch(value), (module, setting)
            expected.add((module, setting))
    credentials = _profiles()[profile_id].credentials
    declared = {(item.module, item.setting) for item in credentials if item.parameter is None}
    assert declared == expected
    optional = {(item.module, item.setting) for item in credentials if item.parameter is not None}
    assert optional == (
        {("audio_input", "wake_word.detector.api_key")} if profile_id == "spoken_keyword" else set()
    )
    # Every module whose manifest *requires* a credential setting has it.
    for module in document["enabled_modules"]:
        required = set(manifests[module]["settings_schema"].get("required", []))
        for setting in set(manifests[module].get("credentials", [])) & required:
            assert (module, setting) in declared, (module, setting)


@pytest.mark.parametrize("profile_id", PROFILE_ORDER)
async def test_ac8_a_generated_profile_is_reference_only_and_passes_check_config(
    profile_id: str, tmp_path: Path
) -> None:
    """AC8 (R3, R4): generated with fixture parameters into tmp, the file
    holds 0 fixture credential values, every ``${NAME}`` it holds is listed
    under ``secrets``, and ``check_config`` with the stored credentials as
    environment returns 0 with 0 diagnostics (the autouse AC33 guard fails
    the test on any socket)."""

    path = _write(tmp_path, profile_id)
    text = path.read_text(encoding="utf-8")
    for value in FIXTURE_CREDENTIALS.values():
        assert value not in text
    document = yaml.safe_load(text)
    references = set(_REFERENCE.findall(text))
    assert references, profile_id
    assert references == {
        credential.name
        for credential in _profiles()[profile_id].credentials
        if credential.parameter is None
    }
    assert {f"${{{name}}}" for name in references} <= set(document["secrets"])
    assert document == _generated(profile_id)
    assert document["modules_directory"] == "builtin"

    diagnostics: list[str] = []
    status = await application.check_config(
        path,
        environ=_stored_credentials(profile_id),
        diagnostic_reporter=diagnostics.append,
    )
    assert (status, diagnostics) == (0, [])


@pytest.mark.parametrize("profile_id", PROFILE_ORDER)
def test_identities_and_parameters_reach_the_document(profile_id: str) -> None:
    """The identities from the validation answer fill the platform settings
    and the served channel; each parameter fills its slot, typed as given."""

    document = _generated(profile_id)
    platform = document["modules"][_profiles()[profile_id].platform_module]
    assert platform["broadcaster_id"] == FIXTURE_IDENTITIES["broadcaster_id"]
    assert platform["bot_user_id"] == FIXTURE_IDENTITIES["bot_user_id"]
    for channels in (entry["channels"] for entry in document.get("triggers", {}).values()):
        assert list(channels) == [FIXTURE_IDENTITIES["broadcaster_id"]]
    for rule in document.get("actions", []):
        assert rule["destination"]["channel_id"] == FIXTURE_IDENTITIES["broadcaster_id"]
    text = json.dumps(document)
    assert "$parameter:" not in text and "$identity:" not in text and "$credential:" not in text
    if profile_id == "chat_qa":
        assert platform["companion_name"] == "Nova"
    if profile_id == "channel_points":
        rule = document["triggers"]["twitch"]["channels"]["41245072"]["rules"][0]
        assert rule == {"type": "reward", "parameters": {"reward_ids": ["5f1c0d7e-reward-fixture"]}}
        assert platform["notices"] == {"kinds": ["reward"]}
    if profile_id == "spoken_keyword":
        audio = document["modules"]["audio_input"]
        assert audio["wake_word"]["detector"] == FIXTURE_PARAMETERS["spoken_keyword"]["detector"]
        assert audio["sources"]["microphone"]["argv"] == ["recorder", "--wav", "-"]
        assert audio["listen"]["destination"] == {"platform": "twitch", "channel_id": "41245072"}
        rules = document["triggers"]["audio_input"]["channels"]["41245072"]["rules"]
        assert rules == [
            {"type": "wake_word", "parameters": {}},
            {"type": "keyword", "parameters": {"keywords": ["hey nova"]}},
        ]


def test_r3_silent_connects_the_input_with_every_trigger_off() -> None:
    """R3: silent enables the platform input and no model module, and its
    every configured policy is one that never matches (no input declares an
    explicit "off": a probability-0 rule is the configured off)."""

    document = _generated("silent")
    manifests = _manifests()
    assert document["enabled_modules"] == ["twitch"]
    assert "input" in manifests["twitch"]["lifecycle"]["roles"]
    assert "brain" not in document["modules"] and "actions" not in document
    never = {"combination": "all_of", "rules": [{"type": "probability", "parameters": {"probability": 0}}]}
    inputs = [name for name in document["enabled_modules"] if manifests[name].get("triggers")]
    assert sorted(document["triggers"]) == sorted(inputs)
    for entry in document["triggers"].values():
        assert list(entry["channels"].values()) == [never]
    # Spoken keyword keeps its chat input connected with the same off policy.
    assert _generated("spoken_keyword")["triggers"]["twitch"]["channels"]["41245072"] == never


def test_ac10_the_shipped_examples_are_byte_identical_to_cb0f5b1() -> None:
    """AC10: the 4 ``*.example`` profiles are byte-identical to ``cb0f5b1``,
    read in-process (no ``git``): against the digests of that commit's bytes
    and against the committed phase-8 baseline copies, which hold the same
    bytes."""

    assert sorted(EXAMPLES_AT_CB0F5B1) == sorted(path.name for path in ROOT.glob("*.example"))
    for name, digest in EXAMPLES_AT_CB0F5B1.items():
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest, name
    numstat = baseline_numstat("f900f27", tuple(EXAMPLES_AT_CB0F5B1))
    assert (numstat.returncode, numstat.stdout) == (0, "")


@pytest.mark.parametrize("profile_id", PROFILE_ORDER)
def test_ac10_a_generated_profile_uses_only_shipped_declarations(profile_id: str) -> None:
    """AC10: ``enabled_modules`` is a subset of the 21 shipped manifests'
    names, and 0 action names, trigger types or event kinds are not
    declared by a shipped manifest."""

    manifests = _manifests()
    assert len(manifests) == 21
    document = _generated(profile_id)
    enabled = document["enabled_modules"]
    assert set(enabled) <= set(manifests)
    assert set(document["modules"]) <= set(enabled)

    declared_actions = {
        action["name"] for manifest in manifests.values() for action in manifest.get("actions", [])
    }
    used_actions = {rule["action_name"] for rule in document.get("actions", [])}
    brain = document["modules"].get("brain")
    if brain is not None:
        used_actions |= {entry["action"] for entry in brain["delivery"]["actions"]}
    assert used_actions <= declared_actions
    enabled_actions = {
        action["name"] for name in enabled for action in manifests[name].get("actions", [])
    }
    assert used_actions <= enabled_actions

    for input_name, entry in document.get("triggers", {}).items():
        assert input_name in enabled
        types = {rule["type"] for policy in entry["channels"].values() for rule in policy["rules"]}
        assert types <= _trigger_types(manifests[input_name]), input_name

    kinds = {_profiles()[profile_id].dry_run_event_kind}
    for settings in document["modules"].values():
        kinds.update((settings.get("notices") or {}).get("kinds", []))
    assert kinds <= _declared_event_kinds(manifests)


def test_render_profile_yaml_round_trips_in_block_style() -> None:
    document = _generated("chat_qa")
    text = wizard.render_profile_yaml(document)
    assert yaml.safe_load(text) == document
    assert list(yaml.safe_load(text)) == list(document)
    # Block style: no flow sequence (an empty mapping is the only flow node).
    assert text.startswith("# ") and "[" not in text


def test_the_loaded_template_is_never_shared_with_a_generated_document() -> None:
    profile = _profiles()["chat_qa"]
    first = wizard.generate_profile(profile, FIXTURE_PARAMETERS["chat_qa"], FIXTURE_IDENTITIES)
    first["modules"]["twitch"]["companion_name"] = "changed"
    first["limits"]["admission"]["workers"] = 99
    second = wizard.generate_profile(profile, FIXTURE_PARAMETERS["chat_qa"], FIXTURE_IDENTITIES)
    assert second["modules"]["twitch"]["companion_name"] == "Nova"
    assert second["limits"]["admission"]["workers"] == 4


@pytest.mark.parametrize(
    ("profile_id", "parameters", "identities", "named"),
    [
        ("chat_qa", {"companion_name": "${HOME}", **_MODEL}, FIXTURE_IDENTITIES, "companion_name"),
        ("chat_qa", {"companion_name": "  ", **_MODEL}, FIXTURE_IDENTITIES, "companion_name"),
        ("chat_qa", {**_MODEL}, FIXTURE_IDENTITIES, "companion_name"),
        ("chat_qa", {"companion_name": "Nova", "extra": "x", **_MODEL}, FIXTURE_IDENTITIES, "not declare"),
        ("silent", {}, {"broadcaster_id": "41245072"}, "bot_user_id"),
        ("silent", {}, {**FIXTURE_IDENTITIES, "bot_user_id": "${TOKEN}"}, "bot_user_id"),
        (
            "spoken_keyword",
            {
                **FIXTURE_PARAMETERS["spoken_keyword"],
                "detector": {"kind": "http", "endpoint": "https://d.invalid", "api_key": "k-123"},
            },
            FIXTURE_IDENTITIES,
            "detector",
        ),
        (
            "spoken_keyword",
            {**FIXTURE_PARAMETERS["spoken_keyword"], "recorder": []},
            FIXTURE_IDENTITIES,
            "recorder",
        ),
    ],
)
def test_generation_refuses_references_credentials_and_undeclared_inputs(
    profile_id: str, parameters: dict[str, Any], identities: dict[str, str], named: str
) -> None:
    """A supplied value carrying ``${`` (the loader would resolve it), a
    credential inside a parameter, a missing or undeclared input: refused,
    the message naming the declared input (an undeclared name is typed input:
    never echoed) and never echoing a value."""

    with pytest.raises(wizard.ProfileError) as refused:
        wizard.generate_profile(_profiles()[profile_id], parameters, identities)
    message = str(refused.value)
    assert named in message
    for value in ("${HOME}", "${TOKEN}", "k-123", "extra"):
        assert value not in message


#: An HTTP detector that needs its bearer key, supplied apart as a credential.
_HTTP_DETECTOR = {"kind": "http", "endpoint": "https://detector.invalid/wake"}


async def test_an_http_detector_key_is_a_supplied_credential_reference(tmp_path: Path) -> None:
    """The detector's optional ``api_key`` is collected apart: named among the
    supplied credentials, it is written into the detector as its reference
    only, listed under ``secrets``, and ``check_config`` passes with it stored
    (the autouse AC33 guard fails the test on any socket). Not named, the
    detector carries no key and ``secrets`` does not list it."""

    profile = _profiles()["spoken_keyword"]
    parameters = {**FIXTURE_PARAMETERS["spoken_keyword"], "detector": dict(_HTTP_DETECTOR)}
    document = wizard.generate_profile(
        profile, parameters, FIXTURE_IDENTITIES, credentials=["WAKE_WORD_DETECTOR_API_KEY"]
    )
    detector = document["modules"]["audio_input"]["wake_word"]["detector"]
    assert detector == {**_HTTP_DETECTOR, "api_key": "${WAKE_WORD_DETECTOR_API_KEY}"}
    assert "${WAKE_WORD_DETECTOR_API_KEY}" in document["secrets"]
    assert parameters["detector"] == _HTTP_DETECTOR

    without = wizard.generate_profile(profile, parameters, FIXTURE_IDENTITIES)
    assert "api_key" not in without["modules"]["audio_input"]["wake_word"]["detector"]
    assert "${WAKE_WORD_DETECTOR_API_KEY}" not in without["secrets"]

    path = tmp_path / "spoken_keyword.yaml"
    path.write_text(wizard.render_profile_yaml(document), encoding="utf-8")
    assert "detector-key-value" not in path.read_text(encoding="utf-8")
    diagnostics: list[str] = []
    status = await application.check_config(
        path,
        environ={
            **_stored_credentials("spoken_keyword"),
            "WAKE_WORD_DETECTOR_API_KEY": "detector-key-value",
        },
        diagnostic_reporter=diagnostics.append,
    )
    assert (status, diagnostics) == (0, [])


@pytest.mark.parametrize(
    "names", [["MODEL_API_KEY"], ["NOT_A_CREDENTIAL"], "WAKE_WORD_DETECTOR_API_KEY"]
)
def test_only_an_optional_credential_may_be_supplied_by_name(names: Any) -> None:
    """An always-written credential, an unknown name or a bare string is
    refused, the message never echoing the typed name."""

    with pytest.raises(wizard.ProfileError) as refused:
        wizard.generate_profile(
            _profiles()["spoken_keyword"],
            FIXTURE_PARAMETERS["spoken_keyword"],
            FIXTURE_IDENTITIES,
            credentials=names,
        )
    assert "NOT_A_CREDENTIAL" not in str(refused.value)


async def test_a_malformed_detector_is_left_to_check_config(tmp_path: Path) -> None:
    """Phase 8 R7's schema is the module's: the generator passes a detector
    of an unknown kind through, and ``--check-config`` refuses it with exit 2
    naming the field (what AC16 provokes)."""

    parameters = {**FIXTURE_PARAMETERS["spoken_keyword"], "detector": {"kind": "telepathy"}}
    document = wizard.generate_profile(_profiles()["spoken_keyword"], parameters, FIXTURE_IDENTITIES)
    path = tmp_path / "spoken_keyword.yaml"
    path.write_text(wizard.render_profile_yaml(document), encoding="utf-8")
    diagnostics: list[str] = []
    status = await application.check_config(
        path, environ=_stored_credentials("spoken_keyword"), diagnostic_reporter=diagnostics.append
    )
    assert status == 2
    assert any("wake_word.detector.kind" in line for line in diagnostics), diagnostics


def _profile_tree(tmp_path: Path, mutate) -> Path:
    directory = tmp_path / "profiles"
    shutil.copytree(PROFILES, directory)
    path = directory / "chat_qa.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    mutate(data, directory)
    path.write_text(json.dumps(data), encoding="utf-8")
    return directory


def _rename(data: dict[str, Any], directory: Path) -> None:
    data["template"]["modules"]["twitch"]["companion_name"] = "$parameter:nickname"


def _unused(data: dict[str, Any], directory: Path) -> None:
    data["parameters"].append({"name": "unused", "kind": "text", "screen": "choice"})


def _literal_reference(data: dict[str, Any], directory: Path) -> None:
    data["template"]["modules"]["brain"]["endpoint"] = "${MODEL_ENDPOINT}"


def _declared_secrets(data: dict[str, Any], directory: Path) -> None:
    data["template"]["secrets"] = []


def _misplaced_credential(data: dict[str, Any], directory: Path) -> None:
    data["credentials"][0]["setting"] = "client_secret"


def _misplaced_parameter_credential(data: dict[str, Any], directory: Path) -> None:
    data["credentials"].append(
        {
            "name": "COMPANION_KEY",
            "module": "twitch",
            "setting": "companion_name.api_key",
            "screen": "choice",
            "parameter": "companion_name",
        }
    )


def _duplicate_order(data: dict[str, Any], directory: Path) -> None:
    data["order"] = 2


def _wrong_id(data: dict[str, Any], directory: Path) -> None:
    data["id"] = "other"


def _unknown_slot(data: dict[str, Any], directory: Path) -> None:
    data["template"]["modules"]["twitch"]["bot_user_id"] = "$secret:x"


@pytest.mark.parametrize(
    ("mutate", "named"),
    [
        (_rename, "undeclared parameter slots nickname"),
        (_unused, "unused by the template: unused"),
        (_literal_reference, "reference outside a credential slot"),
        (_declared_secrets, "template.secrets"),
        (_misplaced_credential, "does not fill modules.twitch.client_secret"),
        (_misplaced_parameter_credential, "inside a mapping parameter"),
        (_duplicate_order, "'order' repeats"),
        (_wrong_id, "'id' must be the file name"),
        (_unknown_slot, "unknown slot"),
    ],
)
def test_load_profiles_refuses_a_malformed_profile(tmp_path: Path, mutate, named: str) -> None:
    with pytest.raises(wizard.ProfileError, match=re.escape(named)):
        wizard.load_profiles(_profile_tree(tmp_path, mutate))


def test_the_profiles_name_no_other_platform_nor_vendor() -> None:
    """AC39 still scans the profiles directory for the two other platforms;
    the vendor words are refused there too (plan DD1, DD2)."""

    for path in sorted(PROFILES.glob("*.json")):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"\b(kick|youtube|openai|anthropic|gemini)\b", text, re.I), path


# --------------------------------------------------------------------------- #
# P5: the credential store (A1, R4) and the wizard state file (A7)
# --------------------------------------------------------------------------- #

posix_only = pytest.mark.skipif(os.name != "posix", reason="POSIX modes and owners")


def _wizard_profile(directory: Path, profile_id: str = "chat_qa", name: str = "profile.yaml"):
    """A generated profile written at ``directory/name``, and its document."""

    directory.mkdir(parents=True, exist_ok=True)
    document = _generated(profile_id)
    path = directory / name
    path.write_text(wizard.render_profile_yaml(document), encoding="utf-8")
    return path, document


def _linux(tmp_path: Path) -> dict[str, str]:
    return {"XDG_CONFIG_HOME": str(tmp_path / "xdg"), "HOME": str(tmp_path / "home")}


def _files_holding(roots: list[Path], value: str) -> list[Path]:
    needle = value.encode()
    return [
        path
        for root in roots
        if root.exists()
        for path in root.rglob("*")
        if path.is_file() and needle in path.read_bytes()
    ]


class _OpenSpy:
    """Every path opened through ``open``, ``io.open`` or ``os.open``."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.opened: list[str] = []
        self.modes: dict[str, int] = {}
        for owner, name in ((builtins, "open"), (io, "open"), (os, "open")):
            monkeypatch.setattr(owner, name, self._wrap(getattr(owner, name), owner is os))

    def _wrap(self, real, low_level: bool):
        def spy(file, *args, **kwargs):
            if isinstance(file, (str, bytes, os.PathLike)):
                key = os.path.abspath(os.fsdecode(file))
                self.opened.append(key)
                if low_level and len(args) >= 2:
                    self.modes[key] = args[1]
            return real(file, *args, **kwargs)

        return spy

    def touched(self, path: Path) -> bool:
        return os.path.abspath(path) in self.opened


@posix_only
def test_ac11_the_credential_file_is_owner_only_under_the_per_user_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC11 (storage half, A1): the file is ``profile.yaml.credentials.json``
    in the per-user directory (created 0700), mode ``0o600``, and holds the
    value under its reference name and no profile content: only the names
    the profile lists under ``secrets``. Its temporary is created 0600 at
    ``os.open`` and no temporary is left behind."""

    profile, document = _wizard_profile(tmp_path / "profiles")
    environ = _linux(tmp_path)
    state = wizard.WizardState(profile)
    store = wizard.CredentialStore(profile, "linux", environ)
    directory = tmp_path / "xdg" / PRODUCT
    assert store.directory == directory and not directory.exists()
    assert store.path == directory / "profile.yaml.credentials.json"
    assert store.location_reason == wizard.LOCATION_OWNER_ONLY

    spy = _OpenSpy(monkeypatch)
    assert store.write(FIXTURE_CREDENTIALS, document["secrets"], state) is None
    monkeypatch.undo()

    assert stat.S_IMODE(directory.stat().st_mode) & 0o077 == 0
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    temporaries = [key for key in spy.modes if key != os.path.abspath(store.path)]
    assert temporaries and all(spy.modes[key] == 0o600 for key in temporaries)
    assert sorted(path.name for path in directory.iterdir()) == [store.path.name]

    names = [reference[2:-1] for reference in document["secrets"]]
    assert "TWITCH_ACCESS_TOKEN" in names and "MODEL_API_KEY" in names
    stored = json.loads(store.path.read_text(encoding="utf-8"))
    assert stored == {name: FIXTURE_CREDENTIALS[name] for name in names}
    assert stored["TWITCH_ACCESS_TOKEN"] == "tok-XYZ-fixture-access-token"
    # Not a resolved profile: none of its keys, none of its parameter values.
    text = store.path.read_text(encoding="utf-8")
    for word in ("modules", "enabled_modules", "secrets", "Nova", FIXTURE_IDENTITIES["bot_user_id"]):
        assert word not in text
    # A collected value the profile does not list is not stored.
    assert FIXTURE_CREDENTIALS["TRANSCRIPTION_API_KEY"] not in text
    assert state.owns(store.path)
    assert sorted(store.values()) == sorted(stored.values())
    assert store.read(state) == stored
    for value in FIXTURE_CREDENTIALS.values():
        assert value not in profile.read_text(encoding="utf-8")


def test_ac11_a_yaml_and_a_yml_get_two_files_and_the_other_is_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC11 (A1): two profiles of one directory, ``a.yaml`` and ``a.yml``,
    map to 2 distinct credential files, and writing one leaves the other
    byte-identical and unopened."""

    environ = _linux(tmp_path)
    first, document = _wizard_profile(tmp_path / "profiles", name="a.yaml")
    second, _ = _wizard_profile(tmp_path / "profiles", name="a.yml")
    store_a = wizard.CredentialStore(first, "linux", environ)
    store_b = wizard.CredentialStore(second, "linux", environ)
    assert store_a.path != store_b.path
    assert {store_a.path.name, store_b.path.name} == {
        "a.yaml.credentials.json", "a.yml.credentials.json",
    }

    assert store_a.write(FIXTURE_CREDENTIALS, document["secrets"], wizard.WizardState(first)) is None
    before = store_a.path.read_bytes()
    other = dict(FIXTURE_CREDENTIALS, TWITCH_ACCESS_TOKEN="tok-other-profile")
    spy = _OpenSpy(monkeypatch)
    assert store_b.write(other, document["secrets"], wizard.WizardState(second)) is None
    monkeypatch.undo()
    assert not spy.touched(store_a.path)
    assert store_a.path.read_bytes() == before
    assert json.loads(store_b.path.read_text())["TWITCH_ACCESS_TOKEN"] == "tok-other-profile"
    assert len(list(store_a.path.parent.glob("*.credentials.json"))) == 2


def test_ac11_windows_stores_under_local_appdata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC11 (A1): with the platform forced to Windows and
    ``LOCALAPPDATA=<tmp>``, the file is under ``<tmp>/twitch-ia-compagnon/``
    and the store names why; no POSIX owner check runs (``os.getuid`` is
    absent on Windows)."""

    profile, document = _wizard_profile(tmp_path / "profiles")
    local = tmp_path / "local"
    local.mkdir()
    monkeypatch.delattr(os, "getuid", raising=False)
    store = wizard.CredentialStore(profile, "windows", {"LOCALAPPDATA": str(local)})
    state = wizard.WizardState(profile)
    assert store.write(FIXTURE_CREDENTIALS, document["secrets"], state) is None
    assert store.path == local / PRODUCT / "profile.yaml.credentials.json"
    assert store.path.is_file()
    assert store.directory == local / PRODUCT
    assert store.location_reason == wizard.LOCATION_LOCAL_APPDATA
    assert json.loads(store.path.read_text())["TWITCH_ACCESS_TOKEN"] == (
        FIXTURE_CREDENTIALS["TWITCH_ACCESS_TOKEN"]
    )


@posix_only
@pytest.mark.parametrize("mode", [0o720, 0o702, 0o777])
def test_ac11_a_group_or_other_writable_directory_is_refused(
    tmp_path: Path, mode: int
) -> None:
    """AC11 (A1): the per-user directory made group- or other-writable →
    a value-free refusal naming the directory, no credential file, and no
    file in the profile's or the per-user directory holds the value."""

    profile, document = _wizard_profile(tmp_path / "profiles")
    directory = tmp_path / "xdg" / PRODUCT
    directory.mkdir(parents=True)
    directory.chmod(mode)
    try:
        store = wizard.CredentialStore(profile, "linux", _linux(tmp_path))
        state = wizard.WizardState(profile)
        refusal = store.write(FIXTURE_CREDENTIALS, document["secrets"], state)
        assert refusal == wizard.Refusal(wizard.REFUSAL_SHARED, directory)
        assert not store.path.exists()
        assert list(directory.iterdir()) == []
        assert store.values() == ()
        assert state.written == {}
        for value in FIXTURE_CREDENTIALS.values():
            assert _files_holding([tmp_path / "profiles", directory], value) == []
            assert value not in repr(refusal)
        assert store.read(state) == refusal
    finally:
        directory.chmod(0o700)


@posix_only
def test_a1_a_directory_owned_by_another_user_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A1: a per-user directory not owned by the user is refused (the owner
    is compared with ``os.stat``), and nothing is written."""

    profile, document = _wizard_profile(tmp_path / "profiles")
    store = wizard.CredentialStore(profile, "linux", _linux(tmp_path))
    real = os.getuid()
    monkeypatch.setattr(os, "getuid", lambda: real + 1)
    refusal = store.write(FIXTURE_CREDENTIALS, document["secrets"], wizard.WizardState(profile))
    assert refusal == wizard.Refusal(wizard.REFUSAL_NOT_OWNED, store.directory)
    assert not store.path.exists()


@pytest.mark.parametrize("local", [None, "", "missing"])
def test_ac11_windows_without_local_appdata_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, local: str | None
) -> None:
    """AC11 (A1): on Windows, ``LOCALAPPDATA`` unset (or empty, or not an
    existing directory) → refused; no credential file, no directory created,
    and no file in the profile's directory or under the tmp tree holds the
    value."""

    profile, document = _wizard_profile(tmp_path / "profiles")
    monkeypatch.delattr(os, "getuid", raising=False)
    environ = {"APPDATA": str(tmp_path / "roaming")}
    if local is not None:
        environ["LOCALAPPDATA"] = str(tmp_path / local) if local else ""
    store = wizard.CredentialStore(profile, "windows", environ)
    state = wizard.WizardState(profile)
    refusal = store.write(FIXTURE_CREDENTIALS, document["secrets"], state)
    if local == "missing":
        assert refusal == wizard.Refusal(wizard.REFUSAL_UNAVAILABLE, tmp_path / "missing")
        assert not (tmp_path / "missing").exists()
    else:
        assert refusal == wizard.Refusal(wizard.REFUSAL_NO_DIRECTORY, None)
        assert store.path is None
    for value in FIXTURE_CREDENTIALS.values():
        assert _files_holding([tmp_path], value) == []
    assert sorted(path.name for path in tmp_path.iterdir()) == ["profiles"]
    assert store.read(state) == refusal


def test_ac11_an_unrecorded_credential_file_is_neither_read_nor_replaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC11 (A1, A7): a credential file of that name whose digest the
    profile's state does not record (another profile's, same file name) is
    left byte-identical and is never opened; the refusal names its path."""

    environ = _linux(tmp_path)
    profile, document = _wizard_profile(tmp_path / "profiles")
    store = wizard.CredentialStore(profile, "linux", environ)
    assert store.check_directory() is None
    foreign = b'{"TWITCH_ACCESS_TOKEN": "someone-elses-token"}\n'
    store.path.write_bytes(foreign)
    state = wizard.WizardState(profile)

    spy = _OpenSpy(monkeypatch)
    refused = wizard.Refusal(wizard.REFUSAL_NOT_WRITTEN, store.path)
    assert store.write(FIXTURE_CREDENTIALS, document["secrets"], state) == refused
    assert store.read(state) == refused
    assert not state.owns(store.path)
    monkeypatch.undo()

    assert not spy.touched(store.path)
    assert store.path.read_bytes() == foreign
    assert store.values() == ()
    assert sorted(path.name for path in store.path.parent.iterdir()) == [store.path.name]


def test_a7_a_credential_file_changed_since_written_is_not_replaced(tmp_path: Path) -> None:
    """A7: a file the wizard wrote is replaced again while unchanged; once
    changed by another writer it is neither replaced nor read."""

    profile, document = _wizard_profile(tmp_path / "profiles")
    store = wizard.CredentialStore(profile, "linux", _linux(tmp_path))
    state = wizard.WizardState(profile)
    assert store.write(FIXTURE_CREDENTIALS, document["secrets"], state) is None
    renewed = dict(FIXTURE_CREDENTIALS, TWITCH_ACCESS_TOKEN="tok-renewed")
    assert store.write(renewed, document["secrets"], state) is None
    assert store.read(state)["TWITCH_ACCESS_TOKEN"] == "tok-renewed"

    edited = store.path.read_bytes().replace(b"tok-renewed", b"tok-by-hand")
    store.path.write_bytes(edited)
    changed = wizard.Refusal(wizard.REFUSAL_CHANGED, store.path)
    assert store.write(FIXTURE_CREDENTIALS, document["secrets"], state) == changed
    assert store.read(state) == changed
    assert store.path.read_bytes() == edited


def test_r4_a_failed_replace_leaves_no_temporary_and_no_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R4: the file is replaced atomically — when publication (``os.link``,
    ``os.rename`` or ``os.replace``) fails the temporary is removed, so no
    file holds the value, and an existing file is left as it was."""

    profile, document = _wizard_profile(tmp_path / "profiles")
    store = wizard.CredentialStore(profile, "linux", _linux(tmp_path))

    def failing(source, target):
        raise OSError("publication failed")

    for name in ("replace", "link", "rename"):
        monkeypatch.setattr(os, name, failing)
    with pytest.raises(OSError):
        store.write(FIXTURE_CREDENTIALS, document["secrets"], wizard.WizardState(profile))
    monkeypatch.undo()
    assert list(store.directory.iterdir()) == []
    assert _files_holding([tmp_path], FIXTURE_CREDENTIALS["TWITCH_ACCESS_TOKEN"]) == []

    state = wizard.WizardState(profile)
    assert store.write(FIXTURE_CREDENTIALS, document["secrets"], state) is None
    before = store.path.read_bytes()
    monkeypatch.setattr(os, "replace", failing)
    renewed = dict(FIXTURE_CREDENTIALS, TWITCH_ACCESS_TOKEN="tok-renewed")
    with pytest.raises(OSError):
        store.write(renewed, document["secrets"], state)
    monkeypatch.undo()
    assert sorted(path.name for path in store.directory.iterdir()) == [store.path.name]
    assert store.path.read_bytes() == before


def test_a1_a_file_created_after_the_ownership_check_is_not_overwritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A1, A7: two profiles named ``profile.yaml`` in different directories
    share one credential file. When the other profile's wizard creates it
    between this wizard's ownership check and its publication, the file is
    published exclusively: the other's bytes stay, nothing is recorded, no
    temporary is left, and the refusal names the path."""

    environ = _linux(tmp_path)
    first, document = _wizard_profile(tmp_path / "one")
    second, _ = _wizard_profile(tmp_path / "two")
    store = wizard.CredentialStore(first, "linux", environ)
    assert store.path == wizard.CredentialStore(second, "linux", environ).path
    foreign = b'{"TWITCH_ACCESS_TOKEN": "the-other-profiles-token"}\n'
    lexists = os.path.lexists

    def racing(path):
        absent = not lexists(path)
        if absent and os.path.abspath(path) == os.path.abspath(store.path):
            store.path.write_bytes(foreign)
        return not absent

    monkeypatch.setattr(os.path, "lexists", racing)
    state = wizard.WizardState(first)
    refusal = store.write(FIXTURE_CREDENTIALS, document["secrets"], state)
    monkeypatch.undo()
    assert refusal == wizard.Refusal(wizard.REFUSAL_NOT_WRITTEN, store.path)
    assert store.path.read_bytes() == foreign
    assert state.written == {} and store.values() == ()
    assert sorted(path.name for path in store.directory.iterdir()) == [store.path.name]


@posix_only
def test_a1_concurrent_wizards_check_and_replace_one_at_a_time(tmp_path: Path) -> None:
    """A1: the ownership check and the replacement hold an exclusive lock on
    the credential directory: while another holder has it, a write waits,
    and it completes once the lock is released."""

    import fcntl
    import threading

    profile, document = _wizard_profile(tmp_path / "profiles")
    store = wizard.CredentialStore(profile, "linux", _linux(tmp_path))
    assert store.check_directory() is None
    state = wizard.WizardState(profile)
    results: list[Any] = []
    descriptor = os.open(store.directory, os.O_RDONLY)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        writer = threading.Thread(
            target=lambda: results.append(
                store.write(FIXTURE_CREDENTIALS, document["secrets"], state)
            )
        )
        writer.start()
        writer.join(0.3)
        assert writer.is_alive() and not store.path.exists()
    finally:
        os.close(descriptor)
    writer.join(5)
    assert results == [None] and state.owns(store.path)


@posix_only
def test_a7_the_state_file_is_owner_only_and_persists_only_screen_one(tmp_path: Path) -> None:
    """A7: ``<base>.wizard.json`` beside the profile, mode 0600, replaced
    atomically; it keeps the profile choice, its non-secret parameters, the
    language, the written digests and ``completed``, and a restart resumes
    screen 1's pass only — every later success is for this process alone."""

    profile, _ = _wizard_profile(tmp_path / "profiles")
    state = wizard.WizardState(profile)
    assert state.path == tmp_path / "profiles" / "profile.yaml.wizard.json"
    assert wizard.state_path(profile) == state.path
    assert not state.exists and not state.path.exists()

    state.profile = "chat_qa"
    state.parameters = dict(FIXTURE_PARAMETERS["chat_qa"])
    state.language = "fr"
    for screen in wizard.SCREEN_NUMBERS[:4]:
        state.pass_screen(screen)
    state.record(profile)
    state.save()
    assert stat.S_IMODE(state.path.stat().st_mode) == 0o600
    assert sorted(path.name for path in state.path.parent.iterdir()) == [
        "profile.yaml", "profile.yaml.wizard.json",
    ]
    persisted = json.loads(state.path.read_text(encoding="utf-8"))
    assert persisted["passed"] == [1] and persisted["completed"] is False

    again = wizard.WizardState(profile)
    assert again.exists
    assert (again.profile, again.parameters, again.language) == (
        "chat_qa", FIXTURE_PARAMETERS["chat_qa"], "fr",
    )
    assert [again.passed(screen) for screen in wizard.SCREEN_NUMBERS] == [
        True, False, False, False, False,
    ]
    assert again.owns(profile) and not again.completed

    again.pass_screen(2)
    again.pass_screen(3)
    again.invalidate(2)
    assert [again.passed(screen) for screen in wizard.SCREEN_NUMBERS] == [
        True, False, False, False, False,
    ]
    again.completed = True
    again.save()
    assert wizard.WizardState(profile).completed is True


def test_a7_owns_only_while_the_bytes_hash_to_the_recorded_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A7: ``owns(path)`` is true only while the file's bytes hash to the
    digest recorded at write time; a path with no record is never opened."""

    profile, _ = _wizard_profile(tmp_path / "profiles")
    state = wizard.WizardState(profile)
    spy = _OpenSpy(monkeypatch)
    assert not state.owns(profile)
    monkeypatch.undo()
    assert not spy.touched(profile)

    state.record(profile)
    assert state.written == {
        str(profile.absolute()): hashlib.sha256(profile.read_bytes()).hexdigest()
    }
    assert state.owns(profile)
    profile.write_text(profile.read_text() + "# edited by hand\n", encoding="utf-8")
    assert not state.owns(profile)
    profile.unlink()
    assert not state.owns(profile)


def test_ac30_the_state_file_never_holds_a_stored_or_submitted_value(tmp_path: Path) -> None:
    """AC30 (state-file clause, A7): after screen 4's files are written and
    recorded, the state file contains no stored credential value; a save that
    would carry a stored or a submitted value — plain or JSON-escaped — is
    refused, and the file on disk is left as it was."""

    profile, document = _wizard_profile(tmp_path / "profiles")
    store = wizard.CredentialStore(profile, "linux", _linux(tmp_path))
    state = wizard.WizardState(profile)
    state.profile = "chat_qa"
    state.parameters = dict(FIXTURE_PARAMETERS["chat_qa"])
    state.pass_screen(1)
    assert store.write(FIXTURE_CREDENTIALS, document["secrets"], state) is None
    state.record(profile)
    submitted = ['tok-"quoted"\\submitted', "tok-XYZ-fixture-access-token"]
    state.save(store, submitted)
    saved = state.path.read_bytes()
    for value in [*FIXTURE_CREDENTIALS.values(), *submitted]:
        assert value.encode() not in saved
        assert json.dumps(value)[1:-1].encode() not in saved
    assert str(store.path.absolute()) in json.loads(saved)["written"]

    state.parameters["companion_name"] = FIXTURE_CREDENTIALS["TWITCH_ACCESS_TOKEN"]
    with pytest.raises(wizard.WizardStateError, match="credential value") as raised:
        state.save(store)
    assert FIXTURE_CREDENTIALS["TWITCH_ACCESS_TOKEN"] not in str(raised.value)
    state.parameters["companion_name"] = submitted[0]
    with pytest.raises(wizard.WizardStateError, match="credential value"):
        state.save(store, submitted)
    assert state.path.read_bytes() == saved
    assert sorted(path.name for path in state.path.parent.iterdir()) == [
        "profile.yaml", "profile.yaml.wizard.json",
    ]


@pytest.mark.parametrize(
    "content",
    [
        b"not json",
        b"[]",
        b'{"format": 2}',
        b'{"format": 1, "passed": [2]}',
        b'{"format": 1, "token": "x"}',
        b'{"format": 1, "completed": "yes"}',
    ],
)
def test_a7_a_malformed_state_file_is_refused(tmp_path: Path, content: bytes) -> None:
    profile, _ = _wizard_profile(tmp_path / "profiles")
    wizard.state_path(profile).write_bytes(content)
    with pytest.raises(wizard.WizardStateError):
        wizard.WizardState(profile)


# --------------------------------------------------------------------------- #
# P6 (R1): `--config` optional, the extended startup collisions, the landing
# routing (AC2's routing half) and the browser opener (A3)
# --------------------------------------------------------------------------- #

EDITOR_FIXTURE = ROOT / "tests" / "fixtures" / "phase9" / "editor_base_page.html"
#: Stands for the run's temporary directory in the captured editor page.
ROOT_PLACEHOLDER = "{ROOT}"
EDITOR_MODULE = (
    "name: xmod\nmanifest_version: 2\nruntime_api: 2\nproduces: []\nconsumes: []\n"
    "middleware: false\nsettings_schema:\n  type: object\n  properties:\n"
    "    token:\n      type: string\n    channel:\n      type: string\n"
    "credentials: [token]\n"
)
EDITOR_BASE = (
    "modules_directory: ./mods\nenabled_modules: [xmod]\n"
    "secrets: ['${XMOD_TOKEN}']\nmodules:\n  xmod:\n    token: ${XMOD_TOKEN}\n    channel: fixture\n"
)


@contextmanager
def _pinned_secrets() -> Iterator[None]:
    """Tokens, nonces and field keys pinned, so one fixture renders one page."""

    counter = iter(range(1, 1_000_000))
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(secrets, "token_urlsafe", lambda n=32: f"pinned-{next(counter):04d}")
        patch.setattr(secrets, "token_hex", lambda n=32: "ab" * n)
        patch.setattr(secrets, "token_bytes", lambda n=32: b"\x01" * n)
        yield


def _editor_fixture(root: Path) -> Path:
    (root / "mods" / "xmod").mkdir(parents=True)
    (root / "mods" / "xmod" / "module.yaml").write_text(EDITOR_MODULE, encoding="utf-8")
    base = root / "config.yaml"
    base.write_text(EDITOR_BASE, encoding="utf-8")
    return base


def _editor_page(root: Path) -> tuple[str, Any]:
    """The editor's base page for the fixed fixture, reached through the
    access URL with ``--config``; the temporary directory is replaced by
    :data:`ROOT_PLACEHOLDER`. Returns the page and the access-URL response."""

    base = _editor_fixture(root)
    with _pinned_secrets():
        ui = config_ui.ConfigUI(
            config_ui.UISettings.from_argv(["--config", str(base)]), environ={}
        )
        landing = ui.handle(_ui_request("GET", "/", query={"token": ui.token}))
        cookie = dict(landing.headers)["Set-Cookie"].split(";")[0]
        page = ui.handle(_ui_request("GET", "/", headers={"cookie": cookie}))
    assert page.status == 200
    return page.body.decode("utf-8").replace(str(root.resolve()), ROOT_PLACEHOLDER), landing


def _ui_request(
    method: str, path: str, *, query: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
) -> Any:
    return config_ui.UIRequest(
        method=method, path=path, query=query or {},
        headers={"host": "127.0.0.1:8765", **(headers or {})}, body=b"",
    )


#: Written to a colliding file; no diagnostic may quote it.
COLLIDED_CONTENT = "fixture-content-7f3a9c-never-quoted"


def _default_environment(monkeypatch: pytest.MonkeyPatch, root: Path) -> Path:
    """Point A2 at *root*; return the default profile path (not created)."""

    monkeypatch.setenv("XDG_CONFIG_HOME", str(root))
    monkeypatch.setenv("HOME", str(root))
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    return root / PRODUCT / "profile.yaml"


class _Calls:
    """A fake ``serve`` or opener: records its arguments, optionally raises."""

    def __init__(self, error: BaseException | None = None) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.error = error

    def __call__(self, *args: Any) -> None:
        self.calls.append(args)
        if self.error is not None:
            raise self.error


class _Serving(_Calls):
    """A fake ``serve`` that is listening at once: it runs the UI's
    ``on_listening`` hook, as the real :func:`config_ui.serve` does once its
    listener accepts connections."""

    def __call__(self, *args: Any) -> None:
        super().__call__(*args)
        ui = args[0]
        assert ui.on_listening is not None
        ui.on_listening()


def test_ac1_no_config_resolves_the_a2_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile = _default_environment(monkeypatch, tmp_path)
    settings = config_ui.UISettings.from_argv([])
    assert settings.config == profile == tmp_path / PRODUCT / "profile.yaml"
    assert settings.default_location is True
    assert config_ui.startup_checks(settings) == []
    assert config_ui.UISettings.from_argv(["--config", "c.yaml"]).default_location is False

    exposed = config_ui.UISettings.from_argv(["--host", "0.0.0.0"])
    assert config_ui.startup_checks(exposed) == [
        "refusing to bind non-loopback host 0.0.0.0: the configuration UI is local-only; "
        "pass --allow-non-loopback to override"
    ]

    monkeypatch.delenv("XDG_CONFIG_HOME")
    fallback = config_ui.UISettings.from_argv([])
    assert fallback.config == tmp_path / ".config" / PRODUCT / "profile.yaml"
    assert config_ui.startup_checks(fallback) == []


def test_ac1_the_added_paths_are_canonical(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    profile = _default_environment(monkeypatch, tmp_path)
    settings = config_ui.UISettings.from_argv([])
    assert settings.wizard_path == profile.with_name("profile.yaml.wizard.json")
    assert settings.instance_path == profile.with_name("profile.yaml.instance.json")
    assert settings.feed_path == profile.with_name("profile.yaml.feed.jsonl")
    assert settings.credential_path == tmp_path / PRODUCT / "profile.yaml.credentials.json"

    (tmp_path / "real").mkdir()
    (tmp_path / "link").symlink_to(tmp_path / "real")
    given = config_ui.UISettings.from_argv(["--config", str(tmp_path / "link" / "a.yml")])
    assert given.wizard_path == tmp_path / "real" / "a.yml.wizard.json"
    assert given.instance_path == tmp_path / "real" / "a.yml.instance.json"
    assert given.credential_path == tmp_path / PRODUCT / "a.yml.credentials.json"

    monkeypatch.setattr(locations, "running_platform", lambda: locations.WINDOWS)
    assert config_ui.UISettings.from_argv([]).credential_path is None


_ADDED_FILES = {
    "wizard": ("wizard_state", "wizard state", lambda root: root / PRODUCT / "profile.yaml.wizard.json"),
    "instance": ("instance", "instance record", lambda root: root / PRODUCT / "profile.yaml.instance.json"),
    "credential": ("credential", "credential", lambda root: root / PRODUCT / "profile.yaml.credentials.json"),
}


@pytest.mark.parametrize("option, collided", [("--status-file", "status"), ("--overlay", "overlay")])
@pytest.mark.parametrize("added", sorted(_ADDED_FILES))
def test_ac1_an_added_file_colliding_refuses_to_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    option: str,
    collided: str,
    added: str,
) -> None:
    _default_environment(monkeypatch, tmp_path)
    token, role, locate = _ADDED_FILES[added]
    target = locate(tmp_path)
    target.parent.mkdir()
    target.write_text(COLLIDED_CONTENT, encoding="utf-8")

    (problem,) = config_ui.startup_checks(config_ui.UISettings.from_argv([option, str(target)]))
    assert problem == (
        f"{token}_path_collision: {role} file {target} collides with the {collided} file {target}"
    )
    assert COLLIDED_CONTENT not in problem

    serve, opener = _Calls(), _Calls()
    assert config_ui.main([option, str(target)], serve=serve, opener=opener) != 0
    captured = capsys.readouterr()
    assert f"{token}_path_collision" in captured.err
    assert COLLIDED_CONTENT not in captured.out + captured.err
    assert serve.calls == [] and opener.calls == []
    assert target.read_text(encoding="utf-8") == COLLIDED_CONTENT


def test_r1_the_feed_and_the_added_files_collide_once_each(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile = _default_environment(monkeypatch, tmp_path)
    feed = profile.with_name("profile.yaml.feed.jsonl")
    (problem,) = config_ui.startup_checks(config_ui.UISettings.from_argv(["--status-file", str(feed)]))
    assert problem == f"feed_path_collision: feed file {feed} collides with the status file {feed}"

    # The credential file named after a base whose directory is the
    # credential directory and whose name ends like the wizard state's:
    # every added file is checked against the ones before it.
    base = tmp_path / PRODUCT / "x.yaml"
    monkeypatch.setattr(locations, "CREDENTIAL_SUFFIX", ".wizard.json")
    (problem,) = config_ui.startup_checks(config_ui.UISettings.from_argv(["--config", str(base)]))
    wizard_state = tmp_path / PRODUCT / "x.yaml.wizard.json"
    assert problem == (
        f"credential_path_collision: credential file {wizard_state} collides with the "
        f"wizard state file {wizard_state}"
    )


def _access(ui: Any) -> tuple[Any, str]:
    """GET the access URL; return the response and its session cookie pair."""

    response = ui.handle(_ui_request("GET", "/", query={"token": ui.token}))
    assert response.status == 303
    cookie = dict(response.headers)["Set-Cookie"]
    assert cookie.startswith(f"{config_ui.SESSION_COOKIE}=")
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie
    pair = cookie.split(";")[0]
    assert ui._session(_ui_request("GET", "/", headers={"cookie": pair})) is not None
    return response, pair


def _location(ui: Any) -> str:
    return dict(_access(ui)[0].headers)["Location"]


def test_ac2_first_run_lands_on_the_wizard(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    profile = _default_environment(monkeypatch, tmp_path)
    ui = config_ui.ConfigUI(config_ui.UISettings.from_argv([]), environ={})
    assert not profile.exists()
    assert _location(ui) == config_ui.LANDING_WIZARD == "/wizard"
    # A2: with --config, a path that does not exist is also a first run.
    given = config_ui.ConfigUI(
        config_ui.UISettings.from_argv(["--config", str(tmp_path / "absent.yaml")]), environ={}
    )
    assert _location(given) == "/wizard"


def test_ac2_an_unfinished_wizard_lands_on_the_wizard_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile = _default_environment(monkeypatch, tmp_path)
    profile.parent.mkdir()
    profile.write_text("enabled_modules: []\n", encoding="utf-8")
    state = wizard.WizardState(profile)
    state.profile = "silent"
    state.save()
    ui = config_ui.ConfigUI(config_ui.UISettings.from_argv([]), environ={})
    assert _location(ui) == "/wizard"
    # The same over --config: unfinished wins over the editor too.
    given = config_ui.ConfigUI(config_ui.UISettings.from_argv(["--config", str(profile)]), environ={})
    assert _location(given) == "/wizard"

    state.completed = True
    state.save()
    assert _location(ui) == "/console"
    assert _location(given) == "/"

    wizard.state_path(profile).write_bytes(b"not json")
    assert _location(ui) == "/wizard"


def test_ac2_an_unfinished_wizard_is_found_through_a_home_relative_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The state read is the canonical ``settings.wizard_path``: ``~`` is
    expanded, never looked up as a literal directory."""

    _default_environment(monkeypatch, tmp_path)
    monkeypatch.chdir(tmp_path)
    profile = tmp_path / "home.yaml"
    profile.write_text("enabled_modules: []\n", encoding="utf-8")
    state = wizard.WizardState(profile)
    state.profile = "silent"
    state.save()
    settings = config_ui.UISettings.from_argv(["--config", "~/home.yaml"])
    assert settings.wizard_path == state.path.resolve()
    assert _location(config_ui.ConfigUI(settings, environ={})) == "/wizard"


def test_ac2_a_profile_at_the_default_location_lands_on_the_console(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile = _default_environment(monkeypatch, tmp_path)
    profile.parent.mkdir()
    profile.write_text("enabled_modules: []\n", encoding="utf-8")
    settings = config_ui.UISettings.from_argv([])
    assert config_ui.landing_target(settings, None) == "/console"
    ui = config_ui.ConfigUI(settings, environ={})
    assert _location(ui) == config_ui.LANDING_CONSOLE == "/console"


def test_ac2_config_lands_on_the_unchanged_editor_with_one_console_link(tmp_path: Path) -> None:
    """The editor's markup equals the page captured from the pre-phase code
    (P6, before any render changed) except for DD7's one added link."""

    page, landing = _editor_page(tmp_path)
    assert dict(landing.headers)["Location"] == "/"
    assert page.count(config_ui.CONSOLE_LINK) == 1
    assert page.count("<a ") == EDITOR_FIXTURE.read_text(encoding="utf-8").count("<a ") + 1
    assert page.replace(config_ui.CONSOLE_LINK, "", 1) == EDITOR_FIXTURE.read_text(encoding="utf-8")


def _no_token_logged(caplog: pytest.LogCaptureFixture, token: str) -> None:
    for record in caplog.records:
        assert token not in record.getMessage()
        assert token not in repr(record.args)


def test_ac3_the_access_url_is_opened_once_and_printed_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    profile = _default_environment(monkeypatch, tmp_path)
    caplog.set_level("DEBUG")
    serve, opener = _Serving(), _Calls()
    assert config_ui.main([], opener=opener, serve=serve) == 0
    ((ui, settings),) = serve.calls
    assert settings.config == profile
    assert opener.calls == [(ui.access_url,)]
    out = capsys.readouterr().out
    assert out.count(ui.access_url) == 1
    _no_token_logged(caplog, ui.token)
    # The default profile's directory is created owner-only; the profile is not.
    assert stat.S_IMODE(profile.parent.stat().st_mode) == 0o700
    assert not profile.exists()


def test_ac3_the_browser_is_opened_only_once_serve_is_listening(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``main`` never calls the opener itself: a serve that has not reached
    its listener has opened nothing, and its hook opens the URL once."""

    _default_environment(monkeypatch, tmp_path)
    serve, opener = _Calls(), _Calls()
    assert config_ui.main([], opener=opener, serve=serve) == 0
    ((ui, _settings),) = serve.calls
    assert opener.calls == []
    assert ui.on_listening is not None
    ui.on_listening()
    assert opener.calls == [(ui.access_url,)]
    capsys.readouterr()


def test_ac3_a_raising_opener_leaves_the_printed_url_and_exit_zero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    _default_environment(monkeypatch, tmp_path)
    caplog.set_level("DEBUG")
    serve = _Serving()
    error = _Calls(OSError("no browser for http://127.0.0.1:8765/?token=leak"))
    assert config_ui.main([], opener=error, serve=serve) == 0
    ((ui, _settings),) = serve.calls
    assert error.calls == [(ui.access_url,)]
    assert capsys.readouterr().out.count(ui.access_url) == 1
    _no_token_logged(caplog, ui.token)
    assert not any("leak" in record.getMessage() for record in caplog.records)
    assert any("browser could not be opened" in record.getMessage() for record in caplog.records)


def test_a2_a_config_run_creates_no_default_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _default_environment(monkeypatch, tmp_path / "xdg")
    base = _editor_fixture(tmp_path)
    serve, opener = _Serving(), _Calls()
    assert config_ui.main(["--config", str(base)], opener=opener, serve=serve) == 0
    assert len(serve.calls) == 1 and len(opener.calls) == 1
    assert not (tmp_path / "xdg").exists()
    capsys.readouterr()


def test_a3_the_default_opener_is_refused_by_the_ac33_guard_and_swallowed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The existing ``main([...])`` tests reach the real ``webbrowser.open``:
    every Linux path of it starts a process, which the suite's guard refuses
    (AC33); ``main`` swallows the failure and still serves. The browser
    registry is reset and ``BROWSER`` named so a process start is certain."""

    for name in ("DISPLAY", "WAYLAND_DISPLAY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("BROWSER", "fixture-browser")
    monkeypatch.setattr(webbrowser, "_tryorder", None)
    monkeypatch.setattr(webbrowser, "_browsers", {})
    caplog.set_level("DEBUG")
    base = _editor_fixture(tmp_path)
    serve = _Serving()
    assert config_ui.main(["--config", str(base)], serve=serve) == 0
    ((ui, _settings),) = serve.calls
    assert capsys.readouterr().out.count(ui.access_url) == 1
    assert any("browser could not be opened" in record.getMessage() for record in caplog.records)
    _no_token_logged(caplog, ui.token)


# --------------------------------------------------------------------------- #
# P7 (R4): stored credentials join the secret set and every launched
# environment (AC12)
# --------------------------------------------------------------------------- #

STORED_MODULE = EDITOR_MODULE
STORED_BASE = (
    "modules_directory: ./mods\nenabled_modules: [xmod]\n"
    "secrets: ['${TWITCH_ACCESS_TOKEN}']\nmodules:\n  xmod:\n"
    "    token: ${TWITCH_ACCESS_TOKEN}\n    channel: fixture\n"
)


def _stored_profile(
    monkeypatch: pytest.MonkeyPatch, root: Path, values: dict[str, str]
) -> Path:
    """A finished wizard's profile whose credential file holds *values*,
    under a per-user directory pointed at *root*."""

    _default_environment(monkeypatch, root / "xdg")
    (root / "mods" / "xmod").mkdir(parents=True)
    (root / "mods" / "xmod" / "module.yaml").write_text(STORED_MODULE, encoding="utf-8")
    base = root / "profile.yaml"
    base.write_text(STORED_BASE, encoding="utf-8")
    state = wizard.WizardState(base)
    store = wizard.CredentialStore(base, locations.running_platform(), os.environ)
    assert store.write(values, ["${TWITCH_ACCESS_TOKEN}"], state) is None
    state.completed = True
    state.save(store)
    return base


class _ExitedChild:
    """A fake child that exited 1 with *stderr* on a real pipe (no process)."""

    pid = 424242

    def __init__(self, stderr: bytes) -> None:
        read, write = os.pipe()
        os.write(write, stderr)
        os.close(write)
        self.stderr = os.fdopen(read, "rb")

    def poll(self) -> int:
        return 1

    def wait(self, timeout: float | None = None) -> int:
        return 1

    def terminate(self) -> None:
        return None

    def kill(self) -> None:
        return None


@posix_only
def test_ac12_the_kept_credential_reaches_every_environment_redacted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """AC12 (R4): the stored value wins over the inherited one in the
    environment given to the supervisor's ``popen`` (with the feed path and
    never the access token), to ``check_config`` through Check and Apply,
    and to the dry run (P11/P12 hand it ``ui.environ``); the editor shows the
    credential as set; a log record and an Apply stderr tail carrying the
    stored value render it redacted."""

    base = _stored_profile(monkeypatch, tmp_path, {"TWITCH_ACCESS_TOKEN": "stored"})
    settings = config_ui.UISettings.from_argv(["--config", str(base)])
    ui = config_ui.ConfigUI(settings, environ={"TWITCH_ACCESS_TOKEN": "inherited", "KEEP": "1"})
    try:
        # The environment every seam is given: the dry run's included.
        assert ui.environ["TWITCH_ACCESS_TOKEN"] == "stored"
        assert ui.environ["KEEP"] == "1"
        assert "stored" in ui.secret_values

        checked: list[dict[str, str]] = []

        async def fake_check_config(base_path: Any, *, environ: Any, **options: Any) -> int:
            checked.append(dict(environ))
            return 0

        monkeypatch.setattr(config_ui, "check_config", fake_check_config)
        assert ui.check().passed

        launched: list[dict[str, str]] = []

        def popen(argv: list[str], **options: Any) -> _ExitedChild:
            launched.append(dict(options["env"]))
            return _ExitedChild(b"Traceback (most recent call last):\ntoken=stored\n")

        ui.supervisor.popen = popen
        report = ui.apply()
        ui.supervisor.stop()
        capsys.readouterr()

        assert len(checked) == 2
        assert all(environ["TWITCH_ACCESS_TOKEN"] == "stored" for environ in checked)
        (env,) = launched
        assert env["TWITCH_ACCESS_TOKEN"] == "stored" and env["KEEP"] == "1"
        assert env[config_ui.FEED_FILE_VARIABLE] == str(settings.feed_path)
        assert env[config_ui.STATUS_FILE_VARIABLE] == str(ui.status_path)
        assert sum(ui.token in value for value in env.values()) == 0

        assert report.outcome == config_ui.APPLY_REFUSED and report.exit_status == 1
        assert "stored" not in report.stderr_tail
        assert config_ui.REDACTED in report.stderr_tail

        with caplog.at_level("DEBUG", logger="core.config_ui"):
            config_ui.logger.warning("the child wrote %s", "token=stored")
        (record,) = [r for r in caplog.records if r.getMessage().startswith("the child wrote")]
        assert "stored" not in record.getMessage()
        assert config_ui.REDACTED in record.getMessage()

        _, cookie = _access(ui)
        page = ui.handle(_ui_request("GET", "/core", headers={"cookie": cookie}))
        assert page.status == 200
        body = page.body.decode("utf-8")
        assert '<code>${TWITCH_ACCESS_TOKEN}</code> (set)' in body
        assert "stored" not in body and "inherited" not in body
    finally:
        config_ui._LOG_REDACTION.sources.discard(ui)


@posix_only
def test_ac12_a_kept_credential_alone_counts_as_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R4: with nothing inherited, the stored value is what makes the
    referenced credential set."""

    base = _stored_profile(monkeypatch, tmp_path, {"TWITCH_ACCESS_TOKEN": "stored"})
    ui = config_ui.ConfigUI(config_ui.UISettings.from_argv(["--config", str(base)]), environ={})
    try:
        assert dict(ui.environ) == {"TWITCH_ACCESS_TOKEN": "stored"}
        assert ui.view().references == {"TWITCH_ACCESS_TOKEN": True}
    finally:
        config_ui._LOG_REDACTION.sources.discard(ui)


def test_r4_without_a_usable_credential_file_the_environment_is_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The merge is a no-op when the store is not usable for the profile: no
    wizard state, or a credential file the state does not own. The given
    environment is kept as the same object and the secret set starts empty;
    the per-user directory is not created."""

    _default_environment(monkeypatch, tmp_path / "xdg")
    base = _editor_fixture(tmp_path)
    settings = config_ui.UISettings.from_argv(["--config", str(base)])
    environ = {"TWITCH_ACCESS_TOKEN": "inherited"}
    ui = config_ui.ConfigUI(settings, environ=environ)
    assert ui.environ is environ and ui.secret_values == frozenset()
    assert not (tmp_path / "xdg").exists()

    # A credential file the state records no digest for is never read.
    wizard.WizardState(base).save()
    directory = tmp_path / "xdg" / PRODUCT
    directory.mkdir(parents=True, mode=0o700)
    (directory / "config.yaml.credentials.json").write_text(
        '{"TWITCH_ACCESS_TOKEN": "foreign"}', encoding="utf-8"
    )
    other = config_ui.ConfigUI(settings, environ=environ)
    assert other.environ is environ and other.secret_values == frozenset()


# --------------------------------------------------------------------------- #
# P8 (R1): the instance record, the `/instance` route and the second launch
# (AC31, AC4's `/instance` row)
# --------------------------------------------------------------------------- #

#: The English table, for the report sentences the launch must print.
_EN = StringTable.load()


def _say(key: str, **values: object) -> str:
    return _EN.sentence("en", key, **values)


class _Clock:
    """A manual monotonic clock; ``sleep`` advances it (no real wait)."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        assert seconds > 0
        self.sleeps.append(seconds)
        self.now += seconds


class _Probe:
    """A fake ``/instance`` probe on the manual clock. *answer(index, port,
    url, timeout)* returns ``(status, body)`` or raises; every attempt is
    recorded with its start time on the clock."""

    def __init__(self, clock: _Clock, answer: Any) -> None:
        self.clock = clock
        self.answer = answer
        self.attempts: list[tuple[float, int, str, float]] = []
        self.bodies: list[bytes] = []

    def __call__(self, port: int, url: str, timeout: float) -> tuple[int, bytes]:
        self.attempts.append((self.clock.now, port, url, timeout))
        status, body = self.answer(len(self.attempts) - 1, port, url, timeout)
        self.bodies.append(body)
        return status, body


def _through(ui: Any) -> Any:
    """An answer that GETs ``/instance`` on *ui* with the URL's token, on the
    probed port, exactly as the production probe asks for it."""

    def answer(index: int, port: int, url: str, timeout: float) -> tuple[int, bytes]:
        token = config_ui._url_token(url)
        response = ui.handle(
            config_ui.UIRequest(
                method="GET", path="/instance", query={} if token is None else {"token": token},
                headers={"host": f"127.0.0.1:{port}"}, body=b"",
            )
        )
        return response.status, response.body

    return answer


def _timed_out(clock: _Clock) -> Any:
    def answer(index: int, port: int, url: str, timeout: float) -> tuple[int, bytes]:
        clock.now += timeout
        raise TimeoutError("timed out")

    return answer


def _refused(index: int, port: int, url: str, timeout: float) -> tuple[int, bytes]:
    raise ConnectionRefusedError("refused")


def _answering(marker: str, version: str) -> Any:
    def answer(index: int, port: int, url: str, timeout: float) -> tuple[int, bytes]:
        return 200, json.dumps({"marker": marker, "version": version}).encode("utf-8")

    return answer


def _seams(
    clock: _Clock, probe: _Probe | None = None, *, live: bool = True, **overrides: Any
) -> Any:
    def unexpected(port: int, url: str, timeout: float) -> tuple[int, bytes]:
        raise AssertionError("no probe expected")

    return config_ui.InstanceSeams(
        liveness=lambda pid: live,
        probe=unexpected if probe is None else probe,
        clock=clock,
        sleep=clock.sleep,
        **overrides,
    )


def _instance_profile(monkeypatch: pytest.MonkeyPatch, root: Path) -> Path:
    """The A2 default profile under *root*, its directory created; returns
    the instance record's path."""

    profile = _default_environment(monkeypatch, root)
    profile.parent.mkdir(mode=0o700)
    return profile.with_name(profile.name + config_ui.INSTANCE_SUFFIX)


def _first_instance(record: Path, *argv: str, platform: str = "linux") -> tuple[Any, bytes]:
    """A first UI (not served) and the record it would publish, written
    under *record*; returns the UI and the record's bytes."""

    first = config_ui.ConfigUI(config_ui.UISettings.from_argv(list(argv)), environ={})
    content = config_ui.instance_document(first, platform)
    descriptor = os.open(record, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(content)
    return first, content


class _Recording(_Serving):
    """A listening fake ``serve`` that records the instance record's bytes
    and mode while it serves, and runs *during* (if any) at that moment."""

    def __init__(self, record: Path, during: Any = None) -> None:
        super().__init__()
        self.record = record
        self.during = during
        self.seen: list[tuple[bytes, int]] = []

    def __call__(self, *args: Any) -> None:
        super().__call__(*args)
        self.seen.append((self.record.read_bytes(), stat.S_IMODE(self.record.stat().st_mode)))
        if self.during is not None:
            self.during(*args)


def _no_token_anywhere(
    token: str, caplog: pytest.LogCaptureFixture, captured: Any, *, out: bool = False
) -> None:
    """The token in no log record and no diagnostic (standard error); with
    *out*, not on standard output either."""

    _no_token_logged(caplog, token)
    assert token not in captured.err
    if out:
        assert token not in captured.out


def test_ac4_instance_route_guards_and_answer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """AC4, ``/instance`` row: a wrong Host, a wrong method and a missing or
    wrong token are refused with the existing statuses (the token refusal is
    the access URL's), with 0 sessions and 0 marker bytes; a good GET answers
    200 with exactly the marker and the version and creates 0 sessions. 0
    checker, supervisor or file-write calls are recorded."""

    _default_environment(monkeypatch, tmp_path)
    calls: list[str] = []

    class _Watched:
        def __getattr__(self, name: str) -> Any:
            calls.append(name)
            return lambda *args, **kwargs: None

    ui = config_ui.ConfigUI(
        config_ui.UISettings.from_argv([]), environ={},
        checker=lambda *args, **kwargs: calls.append("checker"),  # type: ignore[arg-type,return-value]
        supervisor=_Watched(),  # type: ignore[arg-type]
    )
    before = sorted(tmp_path.rglob("*"))
    marker = ui.instance_marker.encode("utf-8")
    assert len(ui.instance_marker) == 32 and int(ui.instance_marker, 16) >= 0
    assert ui.instance_marker not in ui.token
    wrong_token_status = ui.handle(_ui_request("GET", "/", query={"token": "x" * 43})).status
    assert wrong_token_status == 401
    sessions = len(ui._sessions)
    assert sessions == 0

    refused = [
        (403, _ui_request("GET", "/instance", query={"token": ui.token}, headers={"host": "evil.example:8765"})),
        (403, config_ui.UIRequest(method="GET", path="/instance", query={"token": ui.token}, headers={})),
        (403, _ui_request("POST", "/instance", query={"token": ui.token}, headers={"host": "evil.example:8765"})),
        (405, _ui_request("POST", "/instance", query={"token": ui.token})),
        (405, _ui_request("PUT", "/instance", query={"token": ui.token})),
        (405, _ui_request("DELETE", "/instance", query={"token": ui.token})),
        (wrong_token_status, _ui_request("GET", "/instance")),
        (wrong_token_status, _ui_request("GET", "/instance", query={"token": ""})),
        (wrong_token_status, _ui_request("GET", "/instance", query={"token": ui.token[:-1]})),
        (wrong_token_status, _ui_request("GET", "/instance", query={"token": "x" * 43})),
    ]
    for status, request in refused:
        response = ui.handle(request)
        assert response.status == status, request
        assert marker not in response.body
        assert not any(name == "Set-Cookie" for name, _ in response.headers)
        assert len(ui._sessions) == 0

    good = ui.handle(_ui_request("GET", "/instance", query={"token": ui.token}))
    assert good.status == 200
    assert good.content_type == "application/json"
    assert json.loads(good.body) == {"marker": ui.instance_marker, "version": product_version()}
    assert ui.token.encode("utf-8") not in good.body
    assert not any(name == "Set-Cookie" for name, _ in good.headers)
    assert len(ui._sessions) == 0
    # A session cookie does not stand in for the token.
    _response, cookie = _access(ui)
    with_cookie = ui.handle(_ui_request("GET", "/instance", headers={"cookie": cookie}))
    assert with_cookie.status == wrong_token_status and marker not in with_cookie.body
    assert calls == []
    assert sorted(tmp_path.rglob("*")) == before
    config_ui._LOG_REDACTION.sources.discard(ui)


def test_ac31_the_instance_keys_exist_in_both_languages() -> None:
    """R2: every sentence the launch prints is a string-table key present in
    English and French, and every ``instance.*`` key is used."""

    keys, _dynamic = referenced_keys([CONFIG_UI / "__init__.py"])
    files = _string_files()
    for code, sentences in files.items():
        assert sorted(keys - sentences.keys()) == [], code
    shipped = {key for key in files["en"] if key.startswith("instance.")}
    assert {key for key in keys if key.startswith("instance.")} == shipped
    assert len(shipped) >= 10


@posix_only
def test_ac31_a_confirmed_instance_of_this_version_is_opened_not_served(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    record = _instance_profile(monkeypatch, tmp_path)
    caplog.set_level("DEBUG")
    first, content = _first_instance(record)
    clock = _Clock()
    probe = _Probe(clock, _through(first))
    serve, opener = _Serving(), _Calls()
    assert config_ui.main([], opener=opener, serve=serve, instance=_seams(clock, probe)) == 0
    assert serve.calls == []
    assert opener.calls == [(first.access_url,)]
    captured = capsys.readouterr()
    line = _say("instance.already_running", url=first.access_url)
    assert captured.out.count(line) == 1 and captured.out.count(first.access_url) == 1
    assert [(port, url) for _start, port, url, _timeout in probe.attempts] == [(8765, first.access_url)]
    assert record.read_bytes() == content
    _no_token_anywhere(first.token, caplog, captured)
    # The probe's answer is the marker and the version only.
    assert all(first.token.encode("utf-8") not in body for body in probe.bodies)
    config_ui._LOG_REDACTION.sources.discard(first)


@posix_only
def test_ac31_a_slow_first_attempt_then_a_confirming_one_within_ten_seconds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    record = _instance_profile(monkeypatch, tmp_path)
    first, _content = _first_instance(record)
    clock = _Clock()
    start = clock.now
    slow, through = _timed_out(clock), _through(first)

    def answer(index: int, port: int, url: str, timeout: float) -> tuple[int, bytes]:
        return (slow if index < 3 else through)(index, port, url, timeout)

    probe = _Probe(clock, answer)
    serve, opener = _Serving(), _Calls()
    assert config_ui.main([], opener=opener, serve=serve, instance=_seams(clock, probe)) == 0
    assert serve.calls == [] and opener.calls == [(first.access_url,)]
    assert len(probe.attempts) == 4
    assert all(timeout <= 2.0 for *_rest, timeout in probe.attempts)
    assert all(started - start < 10.0 for started, *_rest in probe.attempts)
    assert capsys.readouterr().out.count(_say("instance.already_running", url=first.access_url)) == 1
    config_ui._LOG_REDACTION.sources.discard(first)


@posix_only
def test_ac31_the_recorded_port_wins_over_the_asked_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    record = _instance_profile(monkeypatch, tmp_path)
    first, _content = _first_instance(record, "--port", "8766")
    clock = _Clock()
    probe = _Probe(clock, _through(first))
    serve, opener = _Serving(), _Calls()
    assert config_ui.main(["--port", "8765"], opener=opener, serve=serve, instance=_seams(clock, probe)) == 0
    assert serve.calls == []
    assert opener.calls == [(first.access_url,)]
    assert first.access_url.startswith("http://127.0.0.1:8766/?token=")
    assert {port for _start, port, _url, _timeout in probe.attempts} == {8766}
    capsys.readouterr()
    config_ui._LOG_REDACTION.sources.discard(first)


@posix_only
def test_ac31_a_raising_opener_still_serves_nothing_and_prints_the_line(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    record = _instance_profile(monkeypatch, tmp_path)
    caplog.set_level("DEBUG")
    first, _content = _first_instance(record)
    clock = _Clock()
    serve = _Serving()
    opener = _Calls(OSError(f"no browser for {first.access_url}"))
    status = config_ui.main([], opener=opener, serve=serve, instance=_seams(clock, _Probe(clock, _through(first))))
    assert status == 0
    assert serve.calls == [] and opener.calls == [(first.access_url,)]
    captured = capsys.readouterr()
    assert captured.out.count(_say("instance.already_running", url=first.access_url)) == 1
    _no_token_anywhere(first.token, caplog, captured)
    assert any("browser could not be opened" in r.getMessage() for r in caplog.records)
    config_ui._LOG_REDACTION.sources.discard(first)


@posix_only
@pytest.mark.parametrize("mode", ["refused", "timeout", "wrong_token", "mixed"])
def test_ac31_an_unconfirmed_live_record_is_reported_and_kept(
    mode: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A slow UI is not a dead one: no attempt confirms within 10 s → the
    record is reported (its path, the port fact, a cause and a next action,
    0 URLs), kept byte-identical, and nothing is served or opened."""

    record = _instance_profile(monkeypatch, tmp_path)
    caplog.set_level("DEBUG")
    first, content = _first_instance(record)
    stranger = config_ui.ConfigUI(config_ui.UISettings.from_argv([]), environ={})
    clock = _Clock()
    start = clock.now
    timed_out = _timed_out(clock)
    # The wrong-token status: the first UI's port held by a UI whose token differs.
    wrong_token = _through(stranger)
    answers = {
        "refused": _refused,
        "timeout": timed_out,
        "wrong_token": wrong_token,
        "mixed": lambda index, *rest: (_refused, timed_out, wrong_token)[index % 3](index, *rest),
    }
    probe = _Probe(clock, answers[mode])
    serve, opener = _Serving(), _Calls()
    status = config_ui.main([], opener=opener, serve=serve, instance=_seams(clock, probe))
    assert status != 0
    assert serve.calls == [] and opener.calls == []
    assert record.read_bytes() == content
    assert len(probe.attempts) > 1
    starts = [started for started, *_rest in probe.attempts]
    assert all(timeout <= 2.0 for *_rest, timeout in probe.attempts)
    assert all(started - start < 10.0 for started in starts)
    assert all(later - earlier >= 0.5 for earlier, later in zip(starts, starts[1:]))
    if mode == "refused":
        # Every 0.5 s from the first: 0, 0.5, …, 9.5.
        assert len(probe.attempts) == 20
    captured = capsys.readouterr()
    report = captured.out + captured.err
    assert _say("instance.record_exists", path=record.resolve()) in report
    fact = "instance.port.silent" if mode in ("refused", "timeout") else "instance.port.unauthenticated"
    assert _say(fact, port=8765) in report
    assert _say("instance.not_confirmed.cause") in report
    assert _say("instance.not_confirmed.next", pid=first_pid(content)) in report
    assert "http://" not in report and "?token=" not in report
    _no_token_anywhere(first.token, caplog, captured, out=True)
    assert all(first.token.encode("utf-8") not in body for body in probe.bodies)
    for ui in (first, stranger):
        config_ui._LOG_REDACTION.sources.discard(ui)


def first_pid(content: bytes) -> int:
    pid = json.loads(content)["pid"]
    assert isinstance(pid, int)
    return pid


def _truncated(content: bytes) -> bytes:
    return content[: len(content) // 2]


def _missing_marker(content: bytes) -> bytes:
    document = json.loads(content)
    del document["marker"]
    return json.dumps(document).encode("utf-8")


@posix_only
@pytest.mark.parametrize("case", ["a_not_live", "b_reused_pid", "c_truncated", "d_missing_field"])
def test_ac31_a_stale_record_is_replaced_and_this_launch_serves(
    case: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    record = _instance_profile(monkeypatch, tmp_path)
    caplog.set_level("DEBUG")
    first, content = _first_instance(record)
    if case == "c_truncated":
        record.write_bytes(_truncated(content))
    elif case == "d_missing_field":
        record.write_bytes(_missing_marker(content))
    clock = _Clock()
    probe = _Probe(clock, _answering("f" * 32, product_version()))
    seams = _seams(clock, probe, live=case != "a_not_live")
    serve, opener = _Recording(record), _Calls()
    assert config_ui.main([], opener=opener, serve=serve, instance=seams) == 0
    ((ui, _settings),) = serve.calls
    assert opener.calls == [(ui.access_url,)]
    assert all(first.access_url not in call for call in opener.calls)
    ((published, mode),) = serve.seen
    assert mode == 0o600
    assert json.loads(published) == {
        "pid": os.getpid(), "port": 8765, "marker": ui.instance_marker, "access_url": ui.access_url,
    }
    assert len(probe.attempts) == (1 if case == "b_reused_pid" else 0)
    captured = capsys.readouterr()
    assert (captured.out + captured.err).count(_say("instance.stale")) == 1
    assert first.access_url not in captured.out + captured.err
    _no_token_anywhere(ui.token, caplog, captured)
    # A clean exit removed this instance's record; nothing was left aside.
    assert not record.exists()
    assert sorted(path.name for path in record.parent.iterdir()) == []
    config_ui._LOG_REDACTION.sources.discard(first)


@posix_only
def test_ac31_the_published_record_is_owner_only_and_no_secret_leaves_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The record is mode 0600 from publication and holds the token only in
    its access URL: no other file, log record, diagnostic, the supervised
    child's environment, the status record nor ``/instance`` carries it; a
    clean exit removes it."""

    record = _instance_profile(monkeypatch, tmp_path)
    caplog.set_level("DEBUG")
    holders: list[list[Path]] = []

    def during(ui: Any, settings: Any) -> None:
        holders.append([path for path in _files_holding([tmp_path], ui.token)])
        environ = dict(ui.supervisor._environ)
        assert all(ui.token not in value for value in environ.values())
        assert all(ui.token not in name for name in environ)
        response = ui.handle(_ui_request("GET", "/instance", query={"token": ui.token}))
        assert response.status == 200 and ui.token.encode("utf-8") not in response.body
        assert not settings.status_path.exists() or ui.token not in settings.status_path.read_text()

    serve, opener = _Recording(record, during), _Calls()
    clock = _Clock()
    assert config_ui.main([], opener=opener, serve=serve, instance=_seams(clock)) == 0
    ((ui, _settings),) = serve.calls
    ((published, mode),) = serve.seen
    assert mode == 0o600
    assert holders == [[record]]
    assert ui.token in published.decode("utf-8")
    captured = capsys.readouterr()
    assert captured.out.count(ui.access_url) == 1
    _no_token_anywhere(ui.token, caplog, captured)
    assert not record.exists()


def test_ac31_a_clean_exit_keeps_a_record_that_is_no_longer_its_own(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    record = _instance_profile(monkeypatch, tmp_path)
    foreign = b'{"marker": "another", "pid": 1, "port": 8765}\n'

    def during(ui: Any, settings: Any) -> None:
        record.unlink()
        record.write_bytes(foreign)

    clock = _Clock()
    assert config_ui.main([], opener=_Calls(), serve=_Recording(record, during), instance=_seams(clock)) == 0
    assert record.read_bytes() == foreign
    assert sorted(path.name for path in record.parent.iterdir()) == [record.name]
    capsys.readouterr()


def test_ac31_a_record_removed_while_read_is_no_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Plan risk: a record that disappears between the judgement and its
    removal (a clean exit elsewhere) is no record, never a crash."""

    record = _instance_profile(monkeypatch, tmp_path)
    record.write_bytes(b"{truncated")
    real_rename = os.rename
    gone: list[bool] = []

    def vanishing(source: Any, target: Any) -> None:
        if Path(source) == record.resolve() and not gone:
            gone.append(True)
            os.unlink(source)
        real_rename(source, target)

    monkeypatch.setattr(os, "rename", vanishing)
    serve = _Recording(record)
    clock = _Clock()
    assert config_ui.main([], opener=_Calls(), serve=serve, instance=_seams(clock)) == 0
    assert gone == [True] and len(serve.calls) == 1
    captured = capsys.readouterr()
    assert _say("instance.stale") not in captured.err


@pytest.mark.parametrize("platform", ["windows", "freebsd"])
def test_ac31_a_non_posix_record_holds_no_token_and_a_live_one_is_not_probed(
    platform: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    record = _instance_profile(monkeypatch, tmp_path)
    caplog.set_level("DEBUG")
    second: dict[str, Any] = {}

    def during(ui: Any, settings: Any) -> None:
        capsys.readouterr()  # the first launch's own access URL line
        content = record.read_bytes()
        assert _files_holding([tmp_path], ui.token) == []
        clock = _Clock()
        probe = _Probe(clock, _through(ui))
        serve, opener = _Serving(), _Calls()
        status = config_ui.main(
            [], opener=opener, serve=serve, instance=_seams(clock, probe, platform=platform)
        )
        second.update(
            status=status, serve=serve.calls, opener=opener.calls, attempts=probe.attempts,
            kept=record.read_bytes() == content, captured=capsys.readouterr(),
        )

    serve = _Recording(record, during)
    clock = _Clock()
    assert config_ui.main([], opener=_Calls(), serve=serve, instance=_seams(clock, platform=platform)) == 0
    ((ui, _settings),) = serve.calls
    ((published, _mode),) = serve.seen
    document = json.loads(published)
    assert document == {"pid": os.getpid(), "port": 8765, "marker": ui.instance_marker}
    assert ui.token not in published.decode("utf-8")

    assert second["status"] != 0
    assert second["serve"] == [] and second["opener"] == [] and second["attempts"] == []
    assert second["kept"] is True
    captured = second["captured"]
    report = captured.out + captured.err
    assert report.count(_say("instance.unconfirmable", pid=os.getpid())) == 1
    assert "running but not answering" in report
    assert _say("instance.record_exists", path=record.resolve()) in report
    assert _say("instance.unconfirmable.next", port=8765, pid=os.getpid()) in report
    assert ui.token not in report and "http://" not in report
    _no_token_logged(caplog, ui.token)
    capsys.readouterr()
    assert not record.exists()


def test_ac31_a_non_posix_record_whose_pid_is_not_live_is_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    record = _instance_profile(monkeypatch, tmp_path)
    first, _content = _first_instance(record, platform="windows")
    clock = _Clock()
    serve = _Recording(record)
    seams = _seams(clock, live=False, platform="windows")
    assert config_ui.main([], opener=_Calls(), serve=serve, instance=seams) == 0
    ((ui, _settings),) = serve.calls
    ((published, _mode),) = serve.seen
    assert json.loads(published)["marker"] == ui.instance_marker
    assert capsys.readouterr().err.count(_say("instance.stale")) == 1
    config_ui._LOG_REDACTION.sources.discard(first)


@posix_only
def test_ac31_another_version_is_reported_and_never_opened(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    record = _instance_profile(monkeypatch, tmp_path)
    caplog.set_level("DEBUG")
    first, content = _first_instance(record)
    clock = _Clock()
    other = "999.0-fixture"
    assert other != product_version()
    probe = _Probe(clock, _answering(first.instance_marker, other))
    serve, opener = _Serving(), _Calls()
    status = config_ui.main([], opener=opener, serve=serve, instance=_seams(clock, probe))
    assert status != 0
    assert serve.calls == [] and opener.calls == []
    assert record.read_bytes() == content
    captured = capsys.readouterr()
    assert _say("instance.version_mismatch", running=other, own=product_version()) in captured.out
    assert _say("instance.version_mismatch.next", url=first.access_url) in captured.out
    _no_token_anywhere(first.token, caplog, captured)
    config_ui._LOG_REDACTION.sources.discard(first)


@pytest.mark.parametrize(
    "failure", [OSError(5, "injected"), PermissionError(1, "hard links unsupported")]
)
def test_ac31_a_failed_publication_leaves_no_record_and_serves_nothing(
    failure: OSError,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A failure between the temporary write and the publication (or a file
    system without hard links) leaves no file under the record's name, no
    temporary file, and refuses to serve rather than replace."""

    record = _instance_profile(monkeypatch, tmp_path)
    written: list[bytes] = []

    def failing(temp: Path, target: Path) -> None:
        written.append(temp.read_bytes())
        raise failure

    clock = _Clock()
    serve, opener = _Serving(), _Calls()
    status = config_ui.main([], opener=opener, serve=serve, instance=_seams(clock, publish=failing))
    assert status != 0
    assert serve.calls == [] and opener.calls == []
    assert len(written) == 1 and b'"marker"' in written[0]
    assert not record.exists()
    assert list(record.parent.iterdir()) == []
    captured = capsys.readouterr()
    assert _say("instance.unusable", path=record.resolve()) in captured.err
    assert "injected" not in captured.err and "hard links" not in captured.err
    assert "http://" not in captured.out + captured.err


@posix_only
def test_ac31_two_launches_racing_serve_once_and_the_loser_opens_the_winner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The loser of the no-replace publication re-reads the record and
    behaves as a second launch: 1 ``serve`` (the winner's), and 1 opener call
    from the loser with the winner's URL."""

    record = _instance_profile(monkeypatch, tmp_path)
    serving, finish = threading.Event(), threading.Event()
    winner: dict[str, Any] = {}

    def winner_serve(ui: Any, settings: Any) -> None:
        winner.setdefault("served", []).append(ui)
        serving.set()
        assert finish.wait(30)

    def run_winner() -> None:
        try:
            winner["status"] = config_ui.main(
                [], opener=_Calls(), serve=winner_serve, instance=_seams(_Clock())
            )
        except BaseException as error:  # pragma: no cover - surfaced below
            winner["error"] = error
            serving.set()

    thread = threading.Thread(target=run_winner, name="fixture-winner")

    def racing_publish(temp: Path, target: Path) -> None:
        # The winner judges, publishes and serves between this launch's
        # temporary write and its publication.
        thread.start()
        assert serving.wait(30)
        config_ui._no_replace_rename(temp, target)

    def through_winner(index: int, port: int, url: str, timeout: float) -> tuple[int, bytes]:
        return _through(winner["served"][0])(index, port, url, timeout)

    clock = _Clock()
    probe = _Probe(clock, through_winner)
    serve, opener = _Serving(), _Calls()
    try:
        status = config_ui.main(
            [], opener=opener, serve=serve, instance=_seams(clock, probe, publish=racing_publish)
        )
    finally:
        finish.set()
        thread.join(30)
    assert "error" not in winner
    (winning,) = winner["served"]
    assert status == 0 and winner["status"] == 0
    assert serve.calls == []
    assert opener.calls == [(winning.access_url,)]
    assert not record.exists()
    assert list(record.parent.iterdir()) == []
    capsys.readouterr()


@posix_only
def test_ac31_a_removal_holds_the_name_against_a_publication(tmp_path: Path) -> None:
    """While a removal has renamed the record aside, a publication waits for
    it: the record it puts back is never replaced and the publication loses,
    so two launches never both hold a published record."""

    record = tmp_path / "profile.instance.json"
    live = b'{"marker": "live"}\n'
    record.write_bytes(live)
    outcome: dict[str, Any] = {}

    def publish() -> None:
        outcome["published"] = config_ui._publish_instance(
            record, b'{"marker": "third"}\n', "fixture-third", config_ui._no_replace_rename
        )

    publisher = threading.Thread(target=publish, name="fixture-third")

    def stale_judged(found: bytes) -> bool:
        # The name is vacated here: a publication must not take it.
        assert not record.exists()
        publisher.start()
        publisher.join(0.2)
        outcome["waited"] = publisher.is_alive() and not record.exists()
        return False

    aside = record.with_name(f".{record.name}.fixture.stale")
    assert config_ui._remove_instance_if(record, aside, stale_judged) is False
    publisher.join(30)
    assert outcome == {"waited": True, "published": False}
    assert record.read_bytes() == live
    assert sorted(path.name for path in tmp_path.iterdir()) == [record.name]


def test_ac31_windows_liveness_never_signals_the_recorded_pid(monkeypatch: pytest.MonkeyPatch) -> None:
    """On Windows ``os.kill`` terminates the process: liveness is queried."""

    queried: list[int] = []

    def refuse_kill(*args: object) -> None:  # pragma: no cover - the failure itself
        raise AssertionError(f"os.kill{args}")

    def query(pid: int) -> bool:
        queried.append(pid)
        return True

    monkeypatch.setattr(os, "kill", refuse_kill)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(config_ui, "_windows_process_alive", query)
    assert config_ui.process_alive(4242, None) is True
    assert queried == [4242]


class _TricklingPeer:
    """A fake connection for the production probe: it answers *chunks* one
    per ``recv``, each taking *step* seconds of the fake clock."""

    def __init__(self, clock: list[float], chunks: list[bytes], step: float) -> None:
        self.clock, self.chunks, self.step = clock, list(chunks), step
        self.sent = b""
        self.timeouts: list[float] = []

    def __enter__(self) -> "_TricklingPeer":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def settimeout(self, timeout: float) -> None:
        self.timeouts.append(timeout)

    def sendall(self, data: bytes) -> None:
        self.sent += data

    def recv(self, _size: int) -> bytes:
        self.clock[0] += self.step
        return self.chunks.pop(0) if self.chunks else b""


def _production_probe(
    monkeypatch: pytest.MonkeyPatch, chunks: list[bytes], step: float
) -> tuple[_TricklingPeer, Any]:
    import socket

    clock = [100.0]
    peer = _TricklingPeer(clock, chunks, step)
    monkeypatch.setattr(config_ui.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(socket, "create_connection", lambda address, timeout: peer)
    url = "http://127.0.0.1:8765/?token=" + "a" * 43
    return peer, lambda: config_ui._probe_instance(8765, url, 2.0)


def test_ac31_the_production_probe_reads_one_answer_within_its_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = b'{"marker": "m", "version": "v"}'
    answer = b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\n\r\n" % len(body) + body
    peer, probe = _production_probe(monkeypatch, [answer[:20], answer[20:]], 0.1)
    assert probe() == (200, body)
    assert peer.sent.startswith(b"GET /instance?token=" + b"a" * 43 + b" HTTP/1.1\r\n")
    assert all(0 < timeout <= 2.0 for timeout in peer.timeouts)


def test_ac31_a_peer_that_keeps_sending_is_bounded_by_the_whole_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each byte arrives within any per-operation timeout, yet the attempt
    ends once its 2 s have elapsed in total (R1)."""

    peer, probe = _production_probe(monkeypatch, [b"HTTP/1.1 200 OK\r\n"] + [b"X: y\r\n"] * 1000, 0.5)
    with pytest.raises(TimeoutError):
        probe()
    assert len(peer.chunks) > 990
    assert peer.timeouts[-1] <= 0.5


# --------------------------------------------------------------------------- #
# P9 (R1, A3): the OS launcher and ``--install-launcher`` (AC27)
# --------------------------------------------------------------------------- #

LAUNCHER_PLATFORMS = [("linux", ".desktop"), ("win32", ".cmd"), ("darwin", ".command")]
LAUNCHER_TOKEN = "launcher-token-3b9e1f-never-written"
LAUNCHER_CREDENTIAL = "launcher-credential-5c2d8a-never-written"


def _launcher_seams(directory: Path, platform: str) -> Any:
    """Every platform's launcher directory redirected to *directory*."""

    return config_ui.LauncherSeams(
        platform=platform,
        directories={name: directory for name in (locations.LINUX, locations.WINDOWS, locations.MACOS)},
    )


def _install(directory: Path, platform: str, *argv: str) -> tuple[int, _Calls, _Calls]:
    serve, opener = _Serving(), _Calls()
    status = config_ui.main(
        ["--install-launcher", *argv],
        opener=opener,
        serve=serve,
        launcher=_launcher_seams(directory, platform),
    )
    return status, serve, opener


@pytest.mark.parametrize(("platform", "suffix"), LAUNCHER_PLATFORMS)
def test_ac27_install_launcher_writes_one_launcher_and_serves_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    platform: str,
    suffix: str,
) -> None:
    """AC27 (R1): with the platform forced and its launcher directory
    redirected, ``main(["--install-launcher"])`` writes exactly 1 launcher of
    the platform's suffix naming ``sys.executable`` and ``-m core.config_ui``,
    with no ``--config``, 0 occurrences of the access token and of a stored
    credential value, not group- or world-writable; ``serve`` and the opener
    are called 0 times, no other file is created or changed (no instance
    record), and the exit status is 0. Running it again replaces it (1 file)."""

    _stored_profile(monkeypatch, tmp_path, {"TWITCH_ACCESS_TOKEN": LAUNCHER_CREDENTIAL})
    monkeypatch.setattr(config_ui.secrets, "token_urlsafe", lambda size=None: LAUNCHER_TOKEN)
    directory = tmp_path / "launchers"
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    for attempt in range(2):
        status, serve, opener = _install(directory, platform)
        assert (status, serve.calls, opener.calls) == (0, [], [])
        (written,) = list(directory.iterdir())
        assert written.name == PRODUCT + suffix
        text = written.read_text(encoding="utf-8")
        assert sys.executable in text
        assert "-m core.config_ui" in text
        assert "--config" not in text
        assert LAUNCHER_TOKEN not in text
        assert LAUNCHER_CREDENTIAL not in text
        assert config_ui.launcher.MARKER in text
        mode = stat.S_IMODE(written.stat().st_mode)
        assert mode & (stat.S_IWGRP | stat.S_IWOTH) == 0
        assert mode == (0o644 if suffix == ".cmd" else 0o755)
        captured = capsys.readouterr()
        assert captured.out == f"Launcher: {written}\n"
        assert LAUNCHER_TOKEN not in captured.out + captured.err
        others = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
        assert written in others
        del others[written]
        assert others == before


@pytest.mark.parametrize(("platform", "suffix"), LAUNCHER_PLATFORMS)
def test_ac27_a_foreign_file_at_the_launcher_path_is_left_byte_identical(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], platform: str, suffix: str
) -> None:
    """AC27 (R1): a pre-existing file without the marker line at the
    launcher's path is left byte-identical, the exit status is non-zero, and
    the refusal quotes none of its content."""

    directory = tmp_path / "launchers"
    directory.mkdir()
    foreign = directory / (PRODUCT + suffix)
    content = f"#!/bin/sh\n# not ours\necho {COLLIDED_CONTENT}\n".encode()
    foreign.write_bytes(content)
    status, serve, opener = _install(directory, platform)
    assert status != 0
    assert (serve.calls, opener.calls) == ([], [])
    assert foreign.read_bytes() == content
    assert list(directory.iterdir()) == [foreign]
    captured = capsys.readouterr()
    assert captured.out == ""
    assert str(foreign) in captured.err
    assert COLLIDED_CONTENT not in captured.err


def test_ac27_a_marker_in_the_content_but_not_as_its_line_is_foreign(tmp_path: Path) -> None:
    directory = tmp_path / "launchers"
    directory.mkdir()
    foreign = directory / (PRODUCT + ".desktop")
    content = f"Comment=see {config_ui.launcher.MARKER}\n".encode()
    foreign.write_bytes(content)
    assert _install(directory, "linux")[0] != 0
    assert foreign.read_bytes() == content


def test_ac27_a_link_at_the_launcher_path_is_refused(tmp_path: Path) -> None:
    """A symbolic link is not a launcher this option wrote, even when its
    target carries the marker line: neither is replaced."""

    directory = tmp_path / "launchers"
    assert _install(directory, "linux")[0] == 0
    written = directory / (PRODUCT + ".desktop")
    target = tmp_path / "target.desktop"
    target.write_bytes(written.read_bytes())
    written.unlink()
    written.symlink_to(target)
    assert _install(directory, "linux")[0] != 0
    assert written.is_symlink()
    assert target.read_bytes() == written.read_bytes()


def _desktop_exec(text: str) -> list[str]:
    """The ``Exec=`` arguments, decoded as the desktop-entry rules read them:
    string-value unescaping, then field-code ``%%``, then the quoting rule."""

    (line,) = [line for line in text.splitlines() if line.startswith("Exec=")]
    value = line[len("Exec="):].replace("\\\\", "\\").replace("%%", "%")
    return shlex.split(value)


def test_r1_a_given_config_with_spaces_is_quoted_in_each_format(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R1 (risk: paths with spaces): ``--config`` reaches each launcher made
    absolute and quoted so it reads back as one argument, with ``%`` kept."""

    monkeypatch.chdir(tmp_path)
    folder = tmp_path / "Application Support" / "100% mine"
    folder.mkdir(parents=True)
    profile = folder / "my profile.yaml"
    executable = str(tmp_path / "Python Install" / "python")
    expected = [executable, "-m", "core.config_ui", "--config", str(profile)]
    relative = os.path.relpath(profile)

    linux = launcher.render_launcher("linux", executable, profile)
    assert _desktop_exec(linux) == expected
    macos = launcher.render_launcher("darwin", executable, profile)
    (command,) = [line for line in macos.splitlines() if line.startswith("exec ")]
    assert shlex.split(command) == ["exec", *expected]
    windows = launcher.render_launcher("win32", executable, profile)
    assert windows.endswith("\r\n")
    assert f'"{executable}" -m core.config_ui --config "{str(profile).replace("%", "%%")}"' in windows

    # Through main: the relative --config is made absolute.
    directory = tmp_path / "launchers"
    status, serve, _ = _install(directory, "linux", "--config", relative)
    assert (status, serve.calls) == (0, [])
    written = directory / (PRODUCT + ".desktop")
    assert _desktop_exec(written.read_text(encoding="utf-8"))[1:] == expected[1:]
    assert _desktop_exec(written.read_text(encoding="utf-8"))[0] == sys.executable


def test_r1_a_given_config_reaches_the_launcher_canonical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R1: ``--config`` reaches the launcher as the launch would open it:
    ``~`` expanded and links resolved, never joined onto the working
    directory as written."""

    home = tmp_path / "home"
    real = home / "real"
    real.mkdir(parents=True)
    (home / "link").symlink_to(real, target_is_directory=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(tmp_path)
    directory = tmp_path / "launchers"
    status, serve, _ = _install(directory, "linux", "--config", "~/link/profile.yaml")
    assert (status, serve.calls) == (0, [])
    written = directory / (PRODUCT + ".desktop")
    exec_line = _desktop_exec(written.read_text(encoding="utf-8"))
    assert exec_line[-2:] == ["--config", str(real.resolve() / "profile.yaml")]


def test_r1_the_windows_launcher_selects_the_utf8_code_page() -> None:
    """R1: the ``.cmd`` file is UTF-8 and switches cmd to code page 65001
    before its command line, so a non-ASCII path reads back unchanged."""

    executable = "C:\\Users\\Andr\u00e9\\python.exe"
    lines = launcher.render_launcher("win32", executable).split("\r\n")
    command = next(index for index, line in enumerate(lines) if "core.config_ui" in line)
    assert lines.index("chcp 65001 >nul") < command
    assert executable in lines[command]


def test_r1_a_control_character_or_a_quote_in_an_argument_is_refused() -> None:
    with pytest.raises(launcher.LauncherError):
        launcher.render_launcher("linux", "/usr/bin/python", "/a\nb.yaml")
    with pytest.raises(launcher.LauncherError):
        launcher.render_launcher("win32", 'C:\\a"b\\python.exe')


def test_a3_the_default_launcher_directories(tmp_path: Path) -> None:
    """A3: ``$XDG_DATA_HOME/applications`` (else ``~/.local/share/
    applications``), the Start Menu programs folder under ``%APPDATA%``, and
    ``~/Applications``; the file is named after the product."""

    home = tmp_path / "home"
    environ = {"HOME": str(home), "XDG_DATA_HOME": str(tmp_path / "data"), "APPDATA": str(tmp_path / "roaming")}
    directories = launcher.launcher_directories(environ)
    assert directories[locations.LINUX] == tmp_path / "data" / "applications"
    assert directories[locations.WINDOWS] == (
        tmp_path / "roaming" / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    )
    assert directories[locations.MACOS] == home / "Applications"
    for relative in ("", "data"):
        fallback = launcher.launcher_directories({"HOME": str(home), "XDG_DATA_HOME": relative})
        assert fallback[locations.LINUX] == home / ".local" / "share" / "applications"
    assert launcher.launcher_path("darwin", directories) == home / "Applications" / (PRODUCT + ".command")
    assert launcher.platform_name("win32") == locations.WINDOWS
    assert launcher.platform_name("darwin") == locations.MACOS
    assert launcher.platform_name("linux") == locations.LINUX


# --------------------------------------------------------------------------- #
# P10 (R5 screens 2–3, D3): the platform and model validators (AC14, AC15,
# AC17's validator half), behind injected transports and a manual clock
# --------------------------------------------------------------------------- #

#: The values a streamer submits on screens 2 and 3; none may reach a
#: rendered failure (AC17).
SUBMITTED_TOKEN = "tok-submitted-9f2kq"
SUBMITTED_CLIENT_ID = "client-submitted-4hz"
SUBMITTED_ENDPOINT = "https://model-submitted.invalid/v1/chat/completions"
SUBMITTED_MODEL = "model-submitted-x1"
SUBMITTED_KEY = "key-submitted-7ww"
SUBMITTED = (SUBMITTED_TOKEN, SUBMITTED_CLIENT_ID, SUBMITTED_ENDPOINT, SUBMITTED_MODEL, SUBMITTED_KEY)


class _FakeTransport:
    """A :class:`wizard.Transport` answering from *answer(clock)*: an
    :class:`wizard.HttpAnswer`, or a raise. Every request is recorded; no
    socket is ever created (AC33)."""

    def __init__(self, clock: _Clock, answer: Any) -> None:
        self.clock = clock
        self.answer = answer
        self.requests: list[dict[str, Any]] = []

    async def __call__(
        self, method: str, url: str, *, headers: Any, body: bytes | None, timeout: float
    ) -> wizard.HttpAnswer:
        self.requests.append(
            {"method": method, "url": url, "headers": dict(headers), "body": body, "timeout": timeout}
        )
        return self.answer(self.clock)


def _json(status: int, document: Any) -> Any:
    return lambda clock: wizard.HttpAnswer(status, json.dumps(document).encode())


def _raw(status: int, body: bytes) -> Any:
    return lambda clock: wizard.HttpAnswer(status, body)


def _raising(error: BaseException, advance: float = 0.0) -> Any:
    def answer(clock: _Clock) -> wizard.HttpAnswer:
        clock.now += advance
        raise error

    return answer


def _late(answer: Any, advance: float) -> Any:
    """*answer*, arriving *advance* seconds later on the manual clock."""

    def late(clock: _Clock) -> wizard.HttpAnswer:
        clock.now += advance
        return answer(clock)

    return late


def _validation_answer(**overrides: Any) -> dict[str, Any]:
    answer = {
        "client_id": SUBMITTED_CLIENT_ID,
        "login": "streamer_x",
        "scopes": list(_profiles()["channel_points"].scopes),
        "user_id": "123",
        "expires_in": 5000,
    }
    answer.update(overrides)
    return answer


#: AC14's six platform outcomes: the transport's answer and the token.
PLATFORM_OUTCOMES = {
    "connection_refused": (
        _raising(ConnectionRefusedError(f"refused {SUBMITTED_TOKEN}")), wizard.CONNECTION_REFUSED
    ),
    "no_answer_in_10_s": (_raising(TimeoutError("timed out"), 10.0), wizard.TIMEOUT),
    "http_401": (_json(401, {"status": 401, "message": "invalid access token"}), wizard.UNAUTHORIZED),
    "other_client_id": (
        _json(200, _validation_answer(client_id="someone-else")), wizard.CLIENT_ID_MISMATCH
    ),
    "missing_scope": (
        _json(200, _validation_answer(scopes=["user:read:chat"])), wizard.MISSING_SCOPE
    ),
    "malformed": (_raw(200, b"<html>not json</html>"), wizard.MALFORMED_ANSWER),
}

#: AC15's six model outcomes.
MODEL_OUTCOMES = {
    "connection_refused": (
        _raising(ConnectionRefusedError(f"refused {SUBMITTED_ENDPOINT}")), wizard.CONNECTION_REFUSED
    ),
    "no_answer_in_30_s": (_raising(TimeoutError("timed out"), 30.0), wizard.TIMEOUT),
    "http_401": (_json(401, {"error": {"message": "bad key"}}), wizard.UNAUTHORIZED),
    "http_403": (_json(403, {"error": {"message": "forbidden"}}), wizard.UNAUTHORIZED),
    "http_404": (_json(404, {"error": {"message": "no such model"}}), wizard.NOT_FOUND),
    "not_a_completion": (_json(200, {"object": "list", "data": []}), wizard.NOT_A_COMPLETION),
    "empty_answer": (
        _json(200, {"choices": [{"message": {"role": "assistant", "content": "  "}}]}),
        wizard.EMPTY_ANSWER,
    ),
}

_COMPLETION = {"choices": [{"index": 0, "message": {"role": "assistant", "content": "bonjour"}}]}


async def _platform(answer: Any, profile_id: str = "channel_points") -> tuple[Any, _FakeTransport]:
    clock = _Clock()
    transport = _FakeTransport(clock, answer)
    result = await wizard.validate_platform(
        _profiles()[profile_id], SUBMITTED_TOKEN, SUBMITTED_CLIENT_ID,
        transport=transport, clock=clock,
    )
    return result, transport


async def _rewards(answer: Any) -> tuple[Any, _FakeTransport]:
    clock = _Clock()
    transport = _FakeTransport(clock, answer)
    result = await wizard.list_rewards(
        _profiles()["channel_points"], SUBMITTED_TOKEN, SUBMITTED_CLIENT_ID, "123",
        transport=transport, clock=clock,
    )
    return result, transport


async def _model(answer: Any, profile_id: str | None = None) -> tuple[Any, _FakeTransport]:
    clock = _Clock()
    transport = _FakeTransport(clock, answer)
    result = await wizard.validate_model(
        SUBMITTED_ENDPOINT, SUBMITTED_MODEL, SUBMITTED_KEY, transport=transport, clock=clock,
        profile=None if profile_id is None else _profiles()[profile_id],
    )
    return result, transport


def _rendered(failure: wizard.Failure) -> dict[str, tuple[str, str]]:
    return {
        language: wizard.failure_sentences(_EN, language, failure) for language in ("en", "fr")
    }


async def test_ac14_each_platform_outcome_is_a_distinct_cause_and_keeps_next_closed() -> None:
    """AC14: connection refused, no answer within 10 s (manual clock), 401,
    another client id, a missing scope and a malformed answer each end with
    their own token and a distinct string-table cause with a next action,
    after one request; nothing passes, so no identity is filled."""

    causes: dict[str, set[str]] = {"en": set(), "fr": set()}
    for name, (answer, token) in PLATFORM_OUTCOMES.items():
        result, transport = await _platform(answer)
        assert not result.passed, name
        assert result.failure.token == token, name
        assert (result.login, result.user_id, dict(result.identities)) == (None, None, {}), name
        assert len(transport.requests) == 1, name
        for language, (cause, action) in _rendered(result.failure).items():
            assert cause.strip() and action.strip() and cause != action, name
            causes[language].add(cause)
    assert all(len(found) == len(PLATFORM_OUTCOMES) for found in causes.values())


async def test_ac14_the_request_is_one_get_of_the_profile_endpoint_with_the_token() -> None:
    result, transport = await _platform(_json(200, _validation_answer()))
    assert result.passed
    (request,) = transport.requests
    assert request["method"] == "GET" and request["body"] is None
    assert request["url"] == _profiles()["channel_points"].validation_endpoint
    assert request["headers"] == {"Authorization": f"OAuth {SUBMITTED_TOKEN}"}
    assert request["timeout"] == wizard.PLATFORM_TIMEOUT_SECONDS == 10


async def test_ac14_the_missing_scope_names_are_shown() -> None:
    result, _transport = await _platform(PLATFORM_OUTCOMES["missing_scope"][0])
    assert result.failure.scopes == ("user:write:chat", "channel:read:redemptions")
    for cause, _action in _rendered(result.failure).values():
        assert "user:write:chat" in cause and "channel:read:redemptions" in cause


async def test_ac14_success_shows_the_login_and_fills_both_identities(tmp_path: Path) -> None:
    """AC14: a 200 naming ``streamer_x`` and ``123`` shows ``streamer_x``,
    and the generated profile's broadcaster and bot identities are ``123``,
    taken from the answer, never typed."""

    for profile_id in PROFILE_ORDER:
        result, transport = await _platform(_json(200, _validation_answer()), profile_id)
        assert result.passed and len(transport.requests) == 1
        assert (result.login, result.user_id) == ("streamer_x", "123")
        for language in ("en", "fr"):
            assert "streamer_x" in wizard.platform_sentence(_EN, language, result)
        profile = _profiles()[profile_id]
        document = wizard.generate_profile(
            profile, FIXTURE_PARAMETERS[profile_id], dict(result.identities)
        )
        platform = document["modules"][profile.platform_module]
        assert (platform["broadcaster_id"], platform["bot_user_id"]) == ("123", "123")


@pytest.mark.parametrize(
    "body",
    [
        _validation_answer(login=7),
        _validation_answer(user_id=123),
        _validation_answer(user_id=""),
        _validation_answer(user_id="${TWITCH_ACCESS_TOKEN}"),
        _validation_answer(scopes="user:read:chat"),
        _validation_answer(scopes=[1, 2]),
        _validation_answer(client_id=None),
        ["not", "an", "object"],
        None,
    ],
)
async def test_ac14_a_200_whose_fields_have_wrong_types_is_malformed_not_raised(body: Any) -> None:
    """Plan P10 risk: a malformed but 200 answer maps to ``malformed_answer``."""

    result, _transport = await _platform(_json(200, body))
    assert result.failure.token == wizard.MALFORMED_ANSWER


@pytest.mark.parametrize("status", [200, 500, 302])
async def test_an_unreadable_or_unexpected_platform_answer_is_malformed(status: int) -> None:
    result, _transport = await _platform(_raw(status, b"\xff\xfe" if status == 200 else b"{}"))
    assert result.failure.token == wizard.MALFORMED_ANSWER


async def test_ac14_an_answer_arriving_after_10_s_is_no_answer() -> None:
    """Plan P10 risk: the validator's own bound on the injected clock — not
    the transport's — decides: a valid answer 10 s late is a timeout."""

    result, _transport = await _platform(_late(_json(200, _validation_answer()), 10.0))
    assert result.failure == wizard.Failure(wizard.STAGE_PLATFORM, wizard.TIMEOUT, seconds=10.0)
    result, _transport = await _platform(_late(_json(200, _validation_answer()), 9.5))
    assert result.passed


async def test_ac14_a_pending_request_ends_when_the_injected_clock_passes_the_bound() -> None:
    """Review A1: a transport that never answers ends as a timeout once the
    manual clock moves past 10 s — not at the loop's real deadline."""

    clock = _Clock()
    never = asyncio.Event()

    async def pending(method: str, url: str, *, headers: Any, body: bytes | None, timeout: float) -> Any:
        await never.wait()

    validation = asyncio.ensure_future(
        wizard.validate_platform(
            _profiles()["channel_points"], SUBMITTED_TOKEN, SUBMITTED_CLIENT_ID,
            transport=pending, clock=clock,
        )
    )
    await asyncio.sleep(0)
    assert not validation.done()
    clock.now += 10.0
    result = await asyncio.wait_for(validation, 5.0)
    assert result.failure == wizard.Failure(wizard.STAGE_PLATFORM, wizard.TIMEOUT, seconds=10.0)


async def test_an_asyncio_timeout_from_the_transport_is_a_timeout() -> None:
    """Review A2: before Python 3.11 ``asyncio.TimeoutError`` is its own class;
    it keeps the timeout's cause and next action either way."""

    result, _transport = await _platform(_raising(asyncio.TimeoutError()))
    assert result.failure == wizard.Failure(wizard.STAGE_PLATFORM, wizard.TIMEOUT, seconds=10.0)


async def test_an_answer_nested_past_the_decoder_is_malformed_not_raised() -> None:
    """Review A3: nesting that exhausts ``json.loads`` is a malformed answer."""

    deep = b"[" * 100000 + b"]" * 100000
    result, _transport = await _platform(_raw(200, deep))
    assert result.failure.token == wizard.MALFORMED_ANSWER
    result, _transport = await _model(_raw(200, deep))
    assert result.failure.token == wizard.NOT_A_COMPLETION


async def test_a_token_no_header_can_carry_is_refused_without_a_request() -> None:
    clock = _Clock()
    transport = _FakeTransport(clock, _json(200, _validation_answer()))
    result = await wizard.validate_platform(
        _profiles()["chat_qa"], "tok\r\nX-Injected: 1", SUBMITTED_CLIENT_ID,
        transport=transport, clock=clock,
    )
    assert result.failure.token == wizard.UNAUTHORIZED and transport.requests == []


async def test_ac14_two_rewards_render_two_choices_by_title() -> None:
    """AC14 (A5): one bounded read of the reward list for the validated user
    id, with the credentials in headers only; 2 rewards → 2 titled choices."""

    rewards = {"data": [{"id": "r-1", "title": "Hydrate"}, {"id": "r-2", "title": "Ask Nova"}]}
    result, transport = await _rewards(_json(200, rewards))
    assert result.passed
    assert [(reward.id, reward.title) for reward in result.rewards] == [
        ("r-1", "Hydrate"), ("r-2", "Ask Nova")
    ]
    (request,) = transport.requests
    assert request["method"] == "GET"
    assert request["url"] == (
        _profiles()["channel_points"].reward_list_endpoint + "?broadcaster_id=123"
    )
    assert SUBMITTED_TOKEN not in request["url"] and SUBMITTED_CLIENT_ID not in request["url"]
    assert request["headers"] == {
        "Authorization": f"Bearer {SUBMITTED_TOKEN}", "Client-Id": SUBMITTED_CLIENT_ID
    }


async def test_ac14_an_empty_reward_list_is_its_own_cause() -> None:
    """AC14: an empty list yields "create the reward first" and keeps *next*
    closed; its cause differs from every platform cause."""

    result, _transport = await _rewards(_json(200, {"data": []}))
    assert not result.passed and result.failure.token == wizard.NO_REWARDS
    cause, action = wizard.failure_sentences(_EN, "en", result.failure)
    assert "Create the reward first" in action
    platform_causes = {
        wizard.failure_sentences(_EN, "en", wizard.Failure(wizard.STAGE_PLATFORM, token))[0]
        for token in wizard.PLATFORM_FAILURES
    }
    assert cause not in platform_causes


@pytest.mark.parametrize(
    ("answer", "token"),
    [
        (PLATFORM_OUTCOMES["connection_refused"][0], wizard.CONNECTION_REFUSED),
        (PLATFORM_OUTCOMES["no_answer_in_10_s"][0], wizard.TIMEOUT),
        (PLATFORM_OUTCOMES["http_401"][0], wizard.UNAUTHORIZED),
        (_json(200, {"data": [{"id": 1, "title": "x"}]}), wizard.MALFORMED_ANSWER),
        (_json(200, {"data": {"id": "r"}}), wizard.MALFORMED_ANSWER),
        (_raw(200, b"nope"), wizard.MALFORMED_ANSWER),
    ],
)
async def test_the_reward_list_ends_with_the_same_transport_failures(answer: Any, token: str) -> None:
    result, transport = await _rewards(answer)
    assert result.failure.token == token and result.rewards == ()
    assert len(transport.requests) == 1


async def test_ac15_each_model_outcome_is_a_distinct_cause_and_keeps_next_closed() -> None:
    """AC15: connection refused, no answer within 30 s (manual clock),
    401/403, 404, a body that is not a chat completion and an empty answer
    each end with a distinct string-table cause with a next action, after
    one request (401 and 403 share the unauthorized cause: six outcomes)."""

    causes: dict[str, dict[str, str]] = {"en": {}, "fr": {}}
    for name, (answer, token) in MODEL_OUTCOMES.items():
        result, transport = await _model(answer)
        assert not result.passed and not result.not_needed, name
        assert result.failure.token == token, name
        assert len(transport.requests) == 1, name
        for language, (cause, action) in _rendered(result.failure).items():
            assert cause.strip() and action.strip() and cause != action, name
            causes[language][token] = cause
    for found in causes.values():
        assert len(found) == len(set(found.values())) == len(wizard.MODEL_FAILURES) == 6


async def test_ac15_an_answer_opens_next_after_exactly_one_request() -> None:
    result, transport = await _model(_json(200, _COMPLETION))
    assert result.passed and not result.not_needed
    (request,) = transport.requests
    assert request["method"] == "POST" and request["url"] == SUBMITTED_ENDPOINT
    assert request["headers"] == {
        "Authorization": f"Bearer {SUBMITTED_KEY}", "Content-Type": "application/json"
    }
    body = json.loads(request["body"])
    assert body["model"] == SUBMITTED_MODEL and body["messages"][0]["role"] == "user"
    assert request["timeout"] == wizard.MODEL_TIMEOUT_SECONDS == 30
    assert wizard.model_sentence(_EN, "en", result) == _say("model.answered")


async def test_ac15_the_silent_profile_needs_no_model_and_makes_no_request() -> None:
    result, transport = await _model(_json(200, _COMPLETION), "silent")
    assert result.passed and result.not_needed and transport.requests == []
    assert wizard.model_sentence(_EN, "fr", result) == _EN.sentence("fr", "model.not_needed")
    for profile_id in ("chat_qa", "channel_points", "spoken_keyword"):
        result, transport = await _model(_json(200, _COMPLETION), profile_id)
        assert result.passed and not result.not_needed and len(transport.requests) == 1


@pytest.mark.parametrize(
    "body",
    [
        {"choices": []},
        {"choices": "bonjour"},
        {"choices": [{"text": "bonjour"}]},
        {"choices": [{"message": {"content": ["bonjour"]}}]},
        {"choices": [{"message": "bonjour"}]},
        ["bonjour"],
    ],
)
async def test_ac15_a_2xx_body_of_another_shape_is_not_a_completion(body: Any) -> None:
    result, _transport = await _model(_json(200, body))
    assert result.failure.token == wizard.NOT_A_COMPLETION


@pytest.mark.parametrize(
    "body",
    [
        {"choices": [{"message": {"role": "assistant", "content": None}}]},
        {"choices": [{"message": {"role": "assistant"}}]},
        {"choices": [{"message": {"content": ""}}]},
    ],
)
async def test_ac15_no_text_is_an_empty_answer(body: Any) -> None:
    result, _transport = await _model(_json(200, body))
    assert result.failure.token == wizard.EMPTY_ANSWER


async def test_ac15_an_answer_arriving_after_30_s_is_no_answer() -> None:
    result, _transport = await _model(_late(_json(200, _COMPLETION), 30.0))
    assert result.failure == wizard.Failure(wizard.STAGE_MODEL, wizard.TIMEOUT, seconds=30.0)
    for language, (cause, _action) in _rendered(result.failure).items():
        assert "30" in cause, language


async def test_an_oversized_or_undecodable_model_answer_is_not_a_completion() -> None:
    big = b'{"choices": [{"message": {"content": "' + b"a" * wizard.MAX_ANSWER_BYTES + b'"}}]}'
    for body in (big, b"\xff", b"not json"):
        result, _transport = await _model(_raw(200, body))
        assert result.failure.token == wizard.NOT_A_COMPLETION
    result, _transport = await _model(_raw(500, b"{}"))
    assert result.failure.token == wizard.NOT_A_COMPLETION


async def test_any_transport_error_is_a_connection_failure_without_its_text() -> None:
    for error in (OSError("dns"), ValueError(f"bad url {SUBMITTED_ENDPOINT}"), RuntimeError("x")):
        result, _transport = await _model(_raising(error))
        assert result.failure == wizard.Failure(wizard.STAGE_MODEL, wizard.CONNECTION_REFUSED)


def _exception_class_names() -> set[str]:
    import aiohttp

    names = {
        name for name, value in vars(builtins).items()
        if isinstance(value, type) and issubclass(value, BaseException)
    }
    names |= {
        name for name, value in vars(aiohttp).items()
        if isinstance(value, type) and issubclass(value, BaseException)
    }
    return names | {"JSONDecodeError", "CancelledError", "ProfileError", "StringTableError"}


def _all_failures() -> list[wizard.Failure]:
    failures = [
        wizard.Failure(wizard.STAGE_PLATFORM, token, scopes=("user:write:chat",), seconds=10.0)
        for token in wizard.PLATFORM_FAILURES
    ]
    failures += [
        wizard.Failure(wizard.STAGE_REWARDS, token, seconds=10.0) for token in wizard.REWARD_FAILURES
    ]
    failures += [
        wizard.Failure(wizard.STAGE_MODEL, token, seconds=30.0) for token in wizard.MODEL_FAILURES
    ]
    return failures


async def test_ac17_no_failure_text_holds_a_trace_a_class_name_or_a_submitted_value() -> None:
    """AC17 (validator half): every cause and next action, rendered in every
    language for every token of every stage — and the failures the fakes
    provoke with the submitted values inside their exception texts — hold
    0 ``Traceback``, 0 ``File "``, 0 exception class names and 0 submitted
    values. Every token of every stage has its sentences."""

    classes = re.compile(r"\b(" + "|".join(map(re.escape, _exception_class_names())) + r")\b")
    provoked = [(await _platform(answer))[0].failure for answer, _ in PLATFORM_OUTCOMES.values()]
    provoked += [(await _model(answer))[0].failure for answer, _ in MODEL_OUTCOMES.values()]
    provoked += [
        (await _rewards(answer))[0].failure
        for answer in (_json(200, {"data": []}), PLATFORM_OUTCOMES["connection_refused"][0])
    ]
    swept = 0
    for failure in _all_failures() + provoked:
        for language in _EN.sentences:
            for text in wizard.failure_sentences(_EN, language, failure):
                assert "Traceback" not in text and 'File "' not in text, text
                assert classes.search(text) is None, text
                assert not any(value in text for value in SUBMITTED), text
                swept += 1
    assert swept == 2 * 2 * (len(_all_failures()) + len(provoked))
    # The results carry nothing of an exception or of a submitted value.
    for failure in provoked:
        assert not any(value in repr(failure) for value in SUBMITTED)


def test_ac5_the_validator_keys_are_in_both_files() -> None:
    """AC5 re-run: the keys P10 references exist in both files with the same
    placeholders (the referenced-keys check above covers ``wizard.py``)."""

    keys, dynamic = referenced_keys([CONFIG_UI / "wizard.py"])
    assert dynamic == []
    assert {
        "platform.connected", "platform.missing_scope", "rewards.no_rewards",
        "model.not_needed", "model.empty_answer.next",
    } <= keys
    files = _string_files()
    for code, sentences in files.items():
        assert keys <= sentences.keys(), code
    assert i18n.placeholders(files["fr"]["platform.missing_scope"]) == {"scopes"}


def test_the_validators_log_nothing_and_name_no_platform() -> None:
    """Plan P10 risk and DD1: no validator logs (the request URL included),
    and the endpoints come from the profile's data."""

    source = (CONFIG_UI / "wizard.py").read_text(encoding="utf-8")
    assert "logging" not in source and "print(" not in source
    assert not re.search(r"twitch|helix|oauth2", source, re.I)


class _FakeContent:
    def __init__(self, body: bytes) -> None:
        self.body = body

    async def read(self, size: int) -> bytes:
        chunk, self.body = self.body[:size], self.body[size:]
        return chunk


class _FakeResponse:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self.content = _FakeContent(body)

    async def __aenter__(self) -> _FakeResponse:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None


class _FakeSession:
    """Stands in for ``aiohttp.ClientSession`` (no connector, no socket)."""

    calls: list[tuple[Any, ...]] = []

    def __init__(self, outcome: Any, **kwargs: Any) -> None:
        self.outcome = outcome
        self.kwargs = kwargs

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    def request(self, method: str, url: str, **kwargs: Any) -> _FakeResponse:
        self.calls.append((method, url, kwargs, self.kwargs))
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


async def test_the_production_transport_maps_client_failures_and_bounds_the_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import aiohttp

    def install(outcome: Any) -> None:
        monkeypatch.setattr(aiohttp, "ClientSession", lambda **kwargs: _FakeSession(outcome, **kwargs))

    install(_FakeResponse(200, b"x" * (wizard.MAX_ANSWER_BYTES + 70000)))
    answer = await wizard.aiohttp_transport(
        "GET", "https://example.invalid/", headers={"A": "b"}, body=None, timeout=10.0
    )
    assert answer.status == 200 and len(answer.body) == wizard.MAX_ANSWER_BYTES + 1
    method, url, kwargs, session = _FakeSession.calls[-1]
    assert kwargs["allow_redirects"] is False and session["timeout"].total == 10.0
    for error, raised in (
        (aiohttp.ClientConnectionError(SUBMITTED_TOKEN), ConnectionError),
        (aiohttp.InvalidURL(SUBMITTED_ENDPOINT), ConnectionError),
        (asyncio.TimeoutError(), TimeoutError),
    ):
        install(error)
        with pytest.raises(raised) as caught:
            await wizard.aiohttp_transport(
                "GET", SUBMITTED_ENDPOINT, headers={}, body=None, timeout=1.0
            )
        assert SUBMITTED_TOKEN not in str(caught.value)
        assert SUBMITTED_ENDPOINT not in str(caught.value)
        assert caught.value.__cause__ is None


# --------------------------------------------------------------------------- #
# P11 (R5 screen 5, DD6): the dry-run entry and its run record (AC9, AC29)
# --------------------------------------------------------------------------- #

#: The fake model's one answer (AC9).
DRY_RUN_ANSWER = "bonjour"
DRY_RUN_VIEWER = {"id": "viewer-dry-1", "display_name": "Viewer"}


def _dry_event(
    source: str, text: str, *, kind: str | None = None, **extra: Any
) -> dict[str, Any]:
    """A schema-2 input event as the input module would publish it."""

    payload: dict[str, Any] = {
        "platform": "twitch",
        "channel_id": FIXTURE_IDENTITIES["broadcaster_id"],
        "author": dict(DRY_RUN_VIEWER),
        "message_id": f"dry-{secrets.token_hex(4)}",
        "text": text,
    }
    if kind is not None:
        payload["kind"] = kind
    payload.update(extra)
    return {
        "type": "channel.chat.message",
        "payload": payload,
        "metadata": {"source": source, "schema_version": 2},
    }


def _mention() -> dict[str, Any]:
    return _dry_event("twitch", "Nova, tu es là ?")


def _redemption(reward_id: str) -> dict[str, Any]:
    return _dry_event(
        "twitch", "Ask", kind="reward", reward={"id": reward_id, "title": "Ask", "cost": 100}
    )


def _speech() -> dict[str, Any]:
    """The speech candidate after the wake-word decision: the verdict the
    hooked type reads, and a fake transcript as the text (no process)."""

    event = _dry_event(
        "audio_input",
        "hey nova quelle heure est-il",
        kind="speech",
        wake_word={"detected": True, "phrase": "hey nova"},
    )
    event["payload"]["author"] = {"id": "voice:streamer", "display_name": "streamer"}
    return event


class _Spied:
    """What a dry run touched: the inert sessions, the scheduler records it
    read, and every process start attempted."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.sessions: list[Any] = []
        self.refused: list[str] = []
        self.records: dict[str, Any] = {}
        self.processes = 0
        spied = self

        class Inert(application._InertSession):
            def __init__(self) -> None:
                super().__init__()
                spied.sessions.append(self)

            def __getattr__(self, name: str) -> Any:
                spied.refused.append(name)
                return super().__getattr__(name)

        monkeypatch.setattr(application, "_InertSession", Inert)
        real_record = AdmissionScheduler.run_record

        def run_record(scheduler: Any, run_id: str) -> Any:
            record = real_record(scheduler, run_id)
            if record is not None:
                spied.records[run_id] = record
            return record

        monkeypatch.setattr(AdmissionScheduler, "run_record", run_record)

        async def no_process(*args: Any, **kwargs: Any) -> Any:
            spied.processes += 1
            raise AssertionError("a dry run started a process")

        monkeypatch.setattr(asyncio, "create_subprocess_exec", no_process)
        monkeypatch.setattr(asyncio, "create_subprocess_shell", no_process)


def _feed(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


async def _dry(
    tmp_path: Path,
    profile_id: str,
    event: dict[str, Any],
    model: FakeSession | None,
    **kwargs: Any,
) -> tuple[application.DryRunRecord, list[dict[str, Any]]]:
    path = _write(tmp_path, profile_id)
    feed = tmp_path / f"{profile_id}.feed.jsonl"
    feed.unlink(missing_ok=True)
    environ = {**_stored_credentials(profile_id), FEED_FILE_VARIABLE: str(feed)}
    record = await application.dry_run(
        path,
        event,
        environ=environ,
        model_transport=None if model is None else (lambda: model),
        **kwargs,
    )
    return record, _feed(feed)


def _facts(feed: list[dict[str, Any]], fact: str) -> list[dict[str, Any]]:
    return [entry for entry in feed if entry.get("fact") == fact]


def _assert_one_completed_run(
    record: application.DryRunRecord,
    feed: list[dict[str, Any]],
    spied: _Spied,
    event: dict[str, Any],
    model: FakeSession,
) -> None:
    """AC9 + AC29: one run started and completed with success, its id the
    scheduler record's and the feed's, the answer held, nothing sent."""

    started = _facts(feed, "brain.run.started")
    completed = _facts(feed, "brain.run.completed")
    assert len(started) == 1 and len(completed) == 1, feed
    assert completed[0]["status"] == "success"
    assert record.run_id and record.run_id == started[0]["run_id"] == completed[0]["run_id"]
    assert list(spied.records) == [record.run_id]
    scheduler_record = spied.records[record.run_id]
    assert scheduler_record.status == "success" and scheduler_record.reason == "completed"
    assert (record.outcome, record.reason) == ("completed", "success")
    assert record.origin == "dry_run"
    assert dict(record.event) == {
        "input": event["metadata"]["source"],
        "kind": event["payload"].get("kind", "message"),
        "message_id": event["payload"]["message_id"],
    }
    assert record.answer == DRY_RUN_ANSWER
    # 1 model request, 0 messages to the platform: the platform's sessions
    # are inert and never asked anything, no send is traced.
    assert len(model.post_calls) == 1
    assert spied.sessions and all(session.closed for session in spied.sessions)
    assert spied.refused == []
    assert _facts(feed, "channel.chat.sent") == []
    assert spied.processes == 0


def _assert_not_triggered(
    record: application.DryRunRecord,
    feed: list[dict[str, Any]],
    spied: _Spied,
    event: dict[str, Any],
) -> None:
    rejected = _facts(feed, "input.trigger.rejected")
    assert len(rejected) == 1 and rejected[0]["reason"], feed
    assert _facts(feed, "brain.run.started") == []
    assert _facts(feed, "brain.admission.accepted") == []
    assert spied.records == {}
    assert record.outcome == "not_triggered"
    assert record.reason == rejected[0]["reason"]
    assert record.run_id.startswith("dry_run-") and record.origin == "dry_run"
    assert record.event["message_id"] == event["payload"]["message_id"]
    assert record.answer is None
    assert spied.refused == [] and spied.processes == 0


def _bonjour() -> FakeSession:
    return FakeSession(FakeResponse(200, final(DRY_RUN_ANSWER)))


async def test_ac9_chat_qa_mention_runs_once_and_sends_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC9, AC29: a chat message mentioning the companion name → exactly 1
    run started and 1 completed with success, answer ``bonjour`` held by the
    recording provider, 0 messages to the platform; the run id is the
    scheduler record's and the feed's."""

    spied = _Spied(monkeypatch)
    model = _bonjour()
    event = _mention()
    record, feed = await _dry(tmp_path, "chat_qa", event, model)
    _assert_one_completed_run(record, feed, spied, event, model)
    assert len(_facts(feed, "input.trigger.accepted")) == 1


async def test_ac9_chat_qa_without_the_name_is_one_rejection_and_no_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC9: a message not mentioning the name → 0 runs, 1 trigger rejection
    with its reason, 0 model requests."""

    spied = _Spied(monkeypatch)
    model = _bonjour()
    event = _dry_event("twitch", "salut tout le monde")
    record, feed = await _dry(tmp_path, "chat_qa", event, model)
    _assert_not_triggered(record, feed, spied, event)
    assert model.post_calls == []


async def test_ac9_channel_points_runs_only_for_the_picked_reward(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC9, AC29: a redemption of the picked reward id → 1 run; another
    reward id → 0 runs."""

    spied = _Spied(monkeypatch)
    model = _bonjour()
    event = _redemption(FIXTURE_PARAMETERS["channel_points"]["reward_id"])
    record, feed = await _dry(tmp_path, "channel_points", event, model)
    _assert_one_completed_run(record, feed, spied, event, model)

    spied = _Spied(monkeypatch)
    model = _bonjour()
    event = _redemption("another-reward-id")
    record, feed = await _dry(tmp_path, "channel_points", event, model)
    _assert_not_triggered(record, feed, spied, event)
    assert model.post_calls == []


async def test_ac9_spoken_keyword_wake_word_runs_once_without_a_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC9, AC29: the speech event carrying the wake word, fed after the
    wake-word decision through the hooked type with a fake transcript → 1
    run; no detector, recorder or transcription process is started."""

    spied = _Spied(monkeypatch)
    model = _bonjour()
    event = _speech()
    record, feed = await _dry(tmp_path, "spoken_keyword", event, model)
    _assert_one_completed_run(record, feed, spied, event, model)
    accepted = _facts(feed, "input.trigger.accepted")
    assert [entry.get("input") for entry in accepted] == ["audio_input"]


async def test_ac9_silent_records_not_triggered_with_zero_model_requests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC9, AC29: silent with a chat message (even naming the companion) →
    0 runs, 1 rejection with its reason, 0 model requests, and a record whose
    outcome is ``not_triggered`` with the rejection's reason token."""

    spied = _Spied(monkeypatch)
    model = _bonjour()
    event = _dry_event("twitch", "Companion, bonjour ?")
    record, feed = await _dry(tmp_path, "silent", event, model)
    _assert_not_triggered(record, feed, spied, event)
    assert model.post_calls == [] and model.probe_calls == []
    assert record.outcome == _profiles()["silent"].dry_run_outcome


@pytest.mark.parametrize("profile_id", PROFILE_ORDER)
async def test_ac29_each_profile_records_its_expected_outcome(
    profile_id: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC29 (record half): for each of the 4 profiles the record carries a
    non-empty run id, the profile's expected outcome and origin
    ``dry_run`` naming the event it drove."""

    _Spied(monkeypatch)
    event = {
        "chat_qa": _mention,
        "channel_points": lambda: _redemption(FIXTURE_PARAMETERS["channel_points"]["reward_id"]),
        "spoken_keyword": _speech,
        "silent": lambda: _dry_event("twitch", "hello"),
    }[profile_id]()
    record, _feed_entries = await _dry(tmp_path, profile_id, event, _bonjour())
    assert record.run_id
    assert record.outcome == _profiles()[profile_id].dry_run_outcome
    assert record.origin == application.DRY_RUN_ORIGIN
    assert record.event["message_id"] == event["payload"]["message_id"]


async def test_r5_a_dry_run_past_its_bound_returns_no_record_and_closes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R5: a model holding its answer holds the run; once the injected
    clock reaches the bound the dry run raises ``timed_out`` (no record) and
    the runtime is closed — the inert sessions are closed. The answer is
    released right after the clock moved, so it may land, but past the
    bound: it never makes a record (and the brain's own model-call bound is
    not waited out in real time)."""

    spied = _Spied(monkeypatch)
    clock = ManualClock()
    model = HeldSession(FakeResponse(200, final(DRY_RUN_ANSWER)))

    async def advance() -> None:
        await model.entered.wait()
        clock.advance(application.DRY_RUN_TIMEOUT_SECONDS)
        model.release.set()

    advancing = asyncio.ensure_future(advance())
    with pytest.raises(application.DryRunError) as caught:
        await _dry(tmp_path, "chat_qa", _mention(), model, clock=clock)
    await advancing
    assert caught.value.reason == application.DRY_RUN_TIMED_OUT
    assert spied.sessions and all(session.closed for session in spied.sessions)
    assert spied.refused == []


async def test_r4_the_held_answer_is_redacted_of_every_known_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R4: an answer echoing a stored credential reaches the record masked."""

    _Spied(monkeypatch)
    token = FIXTURE_CREDENTIALS["TWITCH_ACCESS_TOKEN"]
    model = FakeSession(FakeResponse(200, final(f"bonjour {token}")))
    record, feed = await _dry(tmp_path, "chat_qa", _mention(), model)
    assert record.outcome == "completed"
    assert record.answer is not None and record.answer.startswith("bonjour ")
    assert token not in record.answer
    assert all(token not in json.dumps(entry) for entry in feed)


@pytest.mark.parametrize(
    "event",
    [None, {}, {"type": "other", "payload": {}, "metadata": {}}],
)
async def test_r5_a_malformed_event_is_refused_before_anything_runs(
    event: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spied = _Spied(monkeypatch)
    with pytest.raises(application.DryRunError) as caught:
        await _dry(tmp_path, "chat_qa", event, _bonjour())
    assert caught.value.reason == application.DRY_RUN_INVALID_EVENT
    assert spied.sessions == []


async def test_r6_a_feed_naming_the_status_record_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R6: the feed's rewrites never replace the status record the
    environment names; the dry run refuses before anything runs."""

    spied = _Spied(monkeypatch)
    path = _write(tmp_path, "chat_qa")
    shared = tmp_path / "shared.jsonl"
    environ = {
        **_stored_credentials("chat_qa"),
        FEED_FILE_VARIABLE: str(shared),
        config_ui.STATUS_FILE_VARIABLE: str(shared),
    }
    with pytest.raises(application.DryRunError) as caught:
        await application.dry_run(path, _mention(), environ=environ)
    assert caught.value.reason == application.DRY_RUN_FEED_COLLISION
    assert spied.sessions == []
    assert not shared.exists()


async def test_r5_a_refused_profile_is_no_record(tmp_path: Path) -> None:
    path = tmp_path / "broken.yaml"
    path.write_text("modules_directory: [\n", encoding="utf-8")
    with pytest.raises(application.DryRunError) as caught:
        await application.dry_run(path, _mention(), environ={})
    assert caught.value.reason == application.DRY_RUN_CONFIGURATION_REFUSED


def test_dd6_the_dry_run_names_no_module() -> None:
    """DD1/DD6: the dry run identifies the platform module from manifest
    input ownership and the run owner from its declared consumption; the
    entry point gains no module name."""

    source = Path(application.__file__).read_text(encoding="utf-8")
    start = source.index("# The dry run (phase 9 R5 screen 5, DD6)")
    end = source.index("def main(argv")
    block = source[start:end]
    for name in _manifests():
        assert f'"{name}"' not in block and f"'{name}'" not in block, name


def test_dd6_the_command_line_is_unchanged() -> None:
    """``core.main``'s command line has no dry-run option (P11)."""

    with pytest.raises(SystemExit):
        application.main(["--config", "x.yaml", "--dry-run"])


# --------------------------------------------------------------------------- #
# P12 (R5, A7, R2, R4): the five screens, their server-side gating, the
# regeneration of the wizard's own files and the re-entry after a restart
# --------------------------------------------------------------------------- #

import base64  # noqa: E402
import hmac  # noqa: E402
import html as _html  # noqa: E402
from urllib.parse import quote, urlencode  # noqa: E402

TOK_A = "tok_A-wizard-7c1f9e"
TOK_B = "tok_B-wizard-2d9e4a"
WIZARD_CLIENT_ID = "client-wizard-5q2xk"
WIZARD_SECRET = "secret-wizard-8n4pd"
WIZARD_MODEL_KEY = "key-wizard-3m7ur"
WIZARD_MODEL_KEY_B = "key-wizard-b-6t1zo"
WIZARD_TRANSCRIPTION_KEY = "key-transcription-9v5wc"
WIZARD_VALUES = (
    TOK_A, TOK_B, WIZARD_CLIENT_ID, WIZARD_SECRET, WIZARD_MODEL_KEY, WIZARD_MODEL_KEY_B,
    WIZARD_TRANSCRIPTION_KEY,
)
WIZARD_REWARDS = [
    {"id": "reward-id-ask-1", "title": "Ask the companion"},
    {"id": "reward-id-hug-2", "title": "Send a hug"},
]


class _PlatformFake:
    """The platform's validation and reward-list endpoints (no socket): the
    validation answers by the presented token, every request is recorded."""

    def __init__(self, clock: _Clock) -> None:
        self.clock = clock
        self.tokens: dict[str, Any] = {}
        self.default: Any = None
        self.rewards: Any = _json(200, {"data": WIZARD_REWARDS})
        self.validations: list[str] = []
        self.reward_reads: list[str] = []

    def accept(self, token: str, **overrides: Any) -> None:
        answer = {
            "client_id": WIZARD_CLIENT_ID,
            "login": "streamer_x",
            "scopes": sorted({scope for profile in _profiles().values() for scope in profile.scopes}),
            "user_id": "123",
            "expires_in": 5000,
        }
        answer.update(overrides)
        self.tokens[token] = _json(200, answer)

    async def __call__(
        self, method: str, url: str, *, headers: Any, body: bytes | None, timeout: float
    ) -> wizard.HttpAnswer:
        authorization = dict(headers)["Authorization"]
        token = authorization.split(" ", 1)[1]
        if "reward" in url:
            self.reward_reads.append(token)
            return self.rewards(self.clock)
        self.validations.append(token)
        answer = self.tokens.get(token, self.default)
        if answer is None:
            answer = _json(401, {"status": 401, "message": "invalid access token"})
        return answer(self.clock)


class _DryRuns:
    """A fake dry-run entry: records each call and answers ``make(event)``."""

    def __init__(self, clock: _Clock, make: Any = None) -> None:
        self.clock = clock
        self.calls: list[dict[str, Any]] = []
        self.make = make if make is not None else _dry_record()

    async def __call__(self, path: Path, event: Any, *, environ: Any, clock: Any, timeout: float) -> Any:
        self.calls.append({"path": Path(path), "event": event, "environ": dict(environ), "timeout": timeout})
        return self.make(event, self.clock)


def _dry_record(outcome: str = "completed", **overrides: Any) -> Any:
    def make(event: dict[str, Any], clock: _Clock) -> Any:
        fields = {
            "run_id": "run-dry-0001",
            "outcome": outcome,
            "reason": "success" if outcome == "completed" else "probability",
            "origin": "dry_run",
            "event": {
                "input": event["metadata"]["source"],
                "kind": event["payload"].get("kind", "message"),
                "message_id": event["payload"]["message_id"],
            },
        }
        fields.update(overrides)
        return application.DryRunRecord(**fields)

    return make


class _Flow:
    """One ConfigUI over a first-run profile, driven through its routes."""

    def __init__(
        self,
        root: Path,
        *,
        name: str = "profile.yaml",
        clock: _Clock | None = None,
        platform: _PlatformFake | None = None,
        dry: _DryRuns | None = None,
        model: Any = None,
        default: bool = False,
    ) -> None:
        self.root = root
        if default:
            # The A2 default location (no --config): the per-user directory,
            # created owner-only as main() creates it.
            self.profile = root / "xdg" / PRODUCT / "profile.yaml"
            self.profile.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            argv: list[str] = []
        else:
            self.profile = root / "site" / name
            self.profile.parent.mkdir(parents=True, exist_ok=True)
            argv = ["--config", str(self.profile)]
        self.clock = clock or _Clock()
        self.platform = platform or _PlatformFake(self.clock)
        self.model = _FakeTransport(self.clock, model or _json(200, _COMPLETION))
        self.dry = dry or _DryRuns(self.clock)
        self.ui = config_ui.ConfigUI(
            config_ui.UISettings.from_argv(argv),
            environ={},
            wizard_seams=wizard.WizardSeams(
                platform_transport=self.platform,
                model_transport=self.model,
                dry_runner=self.dry,
                clock=self.clock,
            ),
        )
        self.starts = 0

        def start(*args: Any, **kwargs: Any) -> Any:
            self.starts += 1
            raise AssertionError("the wizard started the runtime")

        self.ui.supervisor.start = start  # type: ignore[method-assign]
        self.landing, self.cookie = _access(self.ui)
        session = self.ui._session(_ui_request("GET", "/", headers={"cookie": self.cookie}))
        assert session is not None
        self.csrf = session.csrf_token
        self.responses: list[tuple[str, Any]] = [("GET /", self.landing)]

    def close(self) -> None:
        config_ui._LOG_REDACTION.sources.discard(self.ui)

    @property
    def location(self) -> str:
        return dict(self.landing.headers)["Location"]

    def get(self, path: str = "/wizard", **query: str) -> Any:
        response = self.ui.handle(_ui_request("GET", path, query=query, headers={"cookie": self.cookie}))
        self.responses.append((f"GET {path} {query}", response))
        return response

    def post(self, path: str, fields: dict[str, str], *, content_type: str | None = None, body: bytes | None = None) -> Any:
        payload = body if body is not None else urlencode({**fields, "csrf_token": self.csrf}).encode()
        request = config_ui.UIRequest(
            method="POST", path=path, query={},
            headers={
                "host": "127.0.0.1:8765", "cookie": self.cookie, "x-csrf-token": self.csrf,
                "content-type": content_type or "application/x-www-form-urlencoded",
            },
            body=payload,
        )
        response = self.ui.handle(request)
        self.responses.append((f"POST {path} {sorted(fields)}", response))
        return response

    def screen(self, number: int, **fields: str) -> Any:
        return self.post("/wizard", {"screen": str(number), **fields})

    # -- the screens, passed with the fixture inputs ----------------------- #

    def choice(self, profile_id: str = "chat_qa", language: str = "en", **overrides: str) -> Any:
        fields = {"language": language, "profile": profile_id, **_choice_fields(profile_id)}
        fields.update(overrides)
        return self.screen(1, **fields)

    def platform_screen(self, token: str = TOK_A, client_id: str = WIZARD_CLIENT_ID, **extra: str) -> Any:
        fields = {
            "credential.TWITCH_CLIENT_ID": client_id,
            "credential.TWITCH_CLIENT_SECRET": WIZARD_SECRET,
            "credential.TWITCH_ACCESS_TOKEN": token,
        }
        fields.update(extra)
        return self.screen(2, **fields)

    def model_screen(self, profile_id: str = "chat_qa", key: str = WIZARD_MODEL_KEY, **extra: str) -> Any:
        fields = {
            f"parameter.{profile_id}.model_endpoint": "https://model.invalid/v1/chat/completions",
            f"parameter.{profile_id}.model_name": "fixture-model",
            "credential.MODEL_API_KEY": key,
        }
        fields.update(extra)
        return self.screen(3, **fields)

    def through(self, profile_id: str = "chat_qa", upto: int = 5) -> None:
        """Pass screens 1..*upto* with the fixture inputs, each asserted."""

        self.platform.accept(TOK_A)
        steps = [
            lambda: self.choice(profile_id),
            lambda: self._platform_pass(profile_id),
            lambda: self.model_screen(profile_id) if profile_id != "silent" else self.screen(3),
            lambda: self.screen(4),
            lambda: self.screen(5),
        ]
        for number, step in enumerate(steps[:upto], start=1):
            response = step()
            assert _passed(response), (number, _cause(response), _text_of(response))

    def _platform_pass(self, profile_id: str) -> Any:
        response = self.platform_screen()
        if profile_id == "channel_points":
            assert _cause(response) == wizard.REWARD_NOT_PICKED
            response = self.screen(2, **{"parameter.channel_points.reward_id": WIZARD_REWARDS[0]["id"]})
        return response

    def bodies(self) -> list[tuple[str, str]]:
        found = []
        for label, response in self.responses:
            found.append((label, response.body.decode("utf-8")))
            found += [(f"{label} header", f"{name}: {value}") for name, value in response.headers]
        return found

    def state(self) -> dict[str, Any]:
        return json.loads(wizard.state_path(self.ui.base_path).read_text(encoding="utf-8"))


def _choice_fields(profile_id: str) -> dict[str, str]:
    if profile_id == "chat_qa":
        return {"parameter.chat_qa.companion_name": "Nova"}
    if profile_id == "spoken_keyword":
        return {
            "parameter.spoken_keyword.wake_word": "hey nova",
            "parameter.spoken_keyword.recorder": "recorder --wav -",
            "parameter.spoken_keyword.detector.kind": "command",
            "parameter.spoken_keyword.detector.argv": "detector --phrase 'hey nova'",
            "parameter.spoken_keyword.transcription_endpoint": "https://transcription.invalid/v1",
            "parameter.spoken_keyword.transcription_model": "fixture-transcriber",
            "credential.TRANSCRIPTION_API_KEY": WIZARD_TRANSCRIPTION_KEY,
        }
    return {}


def _text_of(response: Any) -> str:
    return response.body.decode("utf-8")


def _passed(response: Any) -> bool:
    return 'data-passed="true"' in _text_of(response)


def _screen_of(response: Any) -> int | None:
    match = re.search(r'<main id="wizard" data-screen="(\d)"', _text_of(response))
    return None if match is None else int(match.group(1))


def _cause(response: Any) -> str | None:
    match = re.search(r'class="failure" data-cause="([^"]+)"', _text_of(response))
    return None if match is None else match.group(1)


def _next_open(response: Any) -> bool:
    return 'id="next"' in _text_of(response)


def _value_forms(value: str) -> list[str]:
    """A value and its URL-encoded, HTML-escaped and JSON-escaped forms."""

    return [value, quote(value, safe=""), _html.escape(value), json.dumps(value)[1:-1]]


@pytest.fixture
def flow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """A factory of :class:`_Flow` over one per-user directory (A1, linux)."""

    _default_environment(monkeypatch, tmp_path / "xdg")
    made: list[_Flow] = []

    def make(**options: Any) -> _Flow:
        made.append(_Flow(tmp_path, **options))
        return made[-1]

    yield make
    for item in made:
        item.close()


def _credential_files(tmp_path: Path) -> list[Path]:
    return sorted((tmp_path / "xdg" / PRODUCT).glob("*.credentials.json"))


def test_ac2_following_the_first_run_redirect_renders_screen_one(flow) -> None:
    """AC2 (wizard half, R1): at first run the access URL's redirect, followed
    with the session cookie, renders the wizard's first screen with its 4
    profile options."""

    run = flow()
    assert not run.profile.exists()
    assert run.location == "/wizard"
    page = run.get(run.location)
    assert page.status == 200 and page.content_type.startswith("text/html")
    assert _screen_of(page) == 1
    assert _text_of(page).count('type="radio" name="profile"') == 4
    # The guards are the existing chain: no session, no wizard.
    anonymous = run.ui.handle(_ui_request("GET", "/wizard"))
    assert anonymous.status == 401 and b"wizard" not in anonymous.body


@pytest.mark.parametrize("language", ["en", "fr"])
def test_ac7_the_choice_screen_offers_four_profiles_in_order(flow, language: str) -> None:
    """AC7 (R3): exactly 4 options, chat Q&A, channel points, spoken keyword,
    silent, each with its name and description from the table."""

    run = flow()
    if language != "en":
        moved = run.post("/language", {"language": language})
        assert moved.status == 303 and dict(moved.headers)["Location"] == "/wizard"
    page = _text_of(run.get())
    assert f'<html lang="{language}">' in page
    assert re.findall(r'type="radio" name="profile" value="([a-z_]+)"', page) == list(PROFILE_ORDER)
    names = re.findall(r'<span class="profile-name">([^<]*)</span>', page)
    descriptions = re.findall(r'<p class="profile-description">([^<]*)</p>', page)
    assert names == [_html.escape(_EN.sentence(language, f"profile.{pid}.name")) for pid in PROFILE_ORDER]
    assert descriptions == [
        _html.escape(_EN.sentence(language, f"profile.{pid}.description")) for pid in PROFILE_ORDER
    ]


def test_ac6_french_on_screen_one_renders_the_next_page_in_french_and_survives(flow) -> None:
    """AC6 (choice half, R2, DD3): ``fr`` chosen on screen 1 renders the next
    page with its French sentences, and the choice survives a new ConfigUI
    over the same profile directory (it lives in the state file)."""

    run = flow()
    assert _passed(run.choice("chat_qa", language="fr"))
    following = _text_of(run.get(screen="2"))
    assert '<html lang="fr">' in following
    for key in ("wizard.screen2.title", "wizard.credential.access_token", "wizard.check"):
        assert _html.escape(_EN.sentence("fr", key)) in following, key
        assert _html.escape(_EN.sentence("en", key)) not in following, key
    assert run.state()["language"] == "fr"

    again = flow()
    assert again.location == "/wizard"
    page = _text_of(again.get())
    assert '<html lang="fr">' in page and _html.escape(_EN.sentence("fr", "wizard.title")) in page
    # /language moves it back, persisted the same way.
    assert again.post("/language", {"language": "en"}).status == 303
    assert again.state()["language"] == "en"
    assert again.post("/language", {"language": "zz"}).status == 409
    assert again.state()["language"] == "en"


def _calls_of(run: _Flow, screen: int) -> int:
    """The calls a screen's own validator received so far."""

    if screen == 2:
        return len(run.platform.validations)
    if screen == 3:
        return len(run.model.requests)
    if screen == 4:
        return int(run.profile.exists())
    return len(run.dry.calls)


@pytest.mark.parametrize("n", [1, 2, 3, 4])
def test_ac13_each_screen_opens_only_once_the_previous_passed(flow, n: int) -> None:
    """AC13 (R5): advancing to screen n+1 before screen n's success is refused,
    re-renders screen n and makes 0 calls on the next screen's validator; a
    GET of screen n+1 is refused the same way; once n passed, the next one
    opens."""

    run = flow()
    run.platform.accept(TOK_A)
    if n > 1:
        run.through("chat_qa", upto=n - 1)
    before = _calls_of(run, n + 1)
    refused = run.screen(n + 1)
    assert refused.status == 409 and _screen_of(refused) == n
    assert _cause(refused) == wizard.ORDER_REFUSED and not _next_open(refused)
    assert _calls_of(run, n + 1) == before
    looked = run.get(screen=str(n + 1))
    assert looked.status == 409 and _screen_of(looked) == n
    step = {
        1: lambda: run.choice(),
        2: lambda: run.platform_screen(),
        3: lambda: run.model_screen(),
        4: lambda: run.screen(4),
    }[n]
    assert _passed(step())
    opened = run.get(screen=str(n + 1))
    assert opened.status == 200 and _screen_of(opened) == n + 1
    assert run.starts == 0


@pytest.mark.parametrize(
    "n, change",
    [
        (1, lambda run: run.choice("chat_qa", **{"parameter.chat_qa.companion_name": "Orion"})),
        (2, lambda run: run.platform_screen(client_id="client-other-0x")),
        (3, lambda run: run.model_screen(**{"parameter.chat_qa.model_name": "other-model"})),
    ],
)
def test_ac13_changing_a_validated_input_invalidates_it_and_every_later_screen(
    flow, n: int, change: Any
) -> None:
    """AC13 (R5): after screens 1–4 passed, changing an input of screen n
    leaves only the screens before n passed (screen n again when its new
    input passes); a request for screen n+1 is refused again until n passes."""

    run = flow()
    run.through("chat_qa", upto=4)
    assert run.ui.wizard.passed_screens() == (1, 2, 3, 4)
    response = change(run)
    kept = tuple(range(1, n + 1)) if _passed(response) else tuple(range(1, n))
    assert run.ui.wizard.passed_screens() == kept
    assert not any(run.ui.wizard.passed(number) for number in range(n + 1, 6))
    refused = run.screen(n + 2) if n + 2 <= 5 else run.get(screen="5")
    assert refused.status == 409 and _screen_of(refused) <= n + 1
    if n == 2:
        # A changed client id is validated again and refused: 2 stays open.
        assert _cause(response) == wizard.CLIENT_ID_MISMATCH and not _next_open(response)
        assert run.screen(3).status == 409


def test_r5_a_replaced_credential_invalidates_its_screen_and_no_hmac_is_written(flow) -> None:
    """R5 (plan P12, F-P3): fingerprints are value-sensitive without
    persisting a secret. Replacing the token on screen 2 invalidates 2–5 and
    the validator is called once with the new token; resubmitting it
    unchanged keeps 2–5's records; replacing the model key on screen 3
    invalidates 3–5, not 2. The state file holds no value, no HMAC and no
    fingerprint computed with them, in hex or base64 (recomputed here through
    the UI's own key), and not the key."""

    run = flow()
    wizard_ = run.ui.wizard
    seen: set[str] = set()

    def observe() -> None:
        for number in wizard.SCREEN_NUMBERS:
            seen.add(wizard_.fingerprint_of(number))

    run.through("chat_qa", upto=3)
    observe()
    assert wizard_.passed_screens() == (1, 2, 3)

    # tok_B refused first: 2–5 not passed, 3 refused until 2 passes again.
    replaced = run.platform_screen(token=TOK_B)
    observe()
    assert run.platform.validations == [TOK_A, TOK_B]
    assert _cause(replaced) == wizard.UNAUTHORIZED and wizard_.passed_screens() == (1,)
    assert run.screen(3).status == 409
    assert run.get(screen="3").status == 409

    # tok_B accepted: exactly one more call, with tok_B; 3–5 must pass again.
    run.platform.accept(TOK_B)
    accepted = run.platform_screen(token=TOK_B)
    observe()
    assert run.platform.validations == [TOK_A, TOK_B, TOK_B]
    assert _passed(accepted) and wizard_.passed_screens() == (1, 2)
    assert run.screen(4).status == 409
    assert _passed(run.model_screen())
    assert wizard_.passed_screens() == (1, 2, 3)

    # The same value resubmitted (typed again, or left empty: kept) changes nothing.
    assert _passed(run.platform_screen(token=TOK_B))
    assert _passed(run.platform_screen(token="", client_id=""))
    assert run.platform.validations == [TOK_A, TOK_B, TOK_B]
    assert wizard_.passed_screens() == (1, 2, 3)

    # The model key: 3–5 invalidated, 2 kept, one more request with the new key.
    requests = len(run.model.requests)
    rekeyed = run.model_screen(key=WIZARD_MODEL_KEY_B)
    observe()
    assert _passed(rekeyed) and wizard_.passed_screens() == (1, 2, 3)
    assert len(run.model.requests) == requests + 1
    assert run.model.requests[-1]["headers"]["Authorization"].endswith(WIZARD_MODEL_KEY_B)
    run.model.answer = _json(401, {"error": {"message": "bad key"}})
    refused = run.model_screen(key=WIZARD_MODEL_KEY)
    observe()
    assert _cause(refused) == wizard.UNAUTHORIZED and wizard_.passed_screens() == (1, 2)
    run.model.answer = _json(200, _COMPLETION)
    assert _passed(run.model_screen(key=WIZARD_MODEL_KEY_B))
    assert _passed(run.screen(4))
    observe()

    data = wizard.state_path(run.ui.base_path).read_bytes()
    key = run.ui.fingerprint_key
    digests = set(seen)
    for value in (TOK_A, TOK_B, WIZARD_CLIENT_ID, WIZARD_SECRET, WIZARD_MODEL_KEY, WIZARD_MODEL_KEY_B):
        assert value.encode() not in data
        mac = hmac.new(key, value.encode("utf-8"), hashlib.sha256)
        assert mac.hexdigest() == wizard_.credential_digest(value)
        digests.add(mac.hexdigest())
    forms: set[bytes] = {key.hex().encode(), base64.b64encode(key), base64.urlsafe_b64encode(key)}
    for digest in digests:
        raw = bytes.fromhex(digest)
        forms |= {digest.encode(), base64.b64encode(raw), base64.urlsafe_b64encode(raw)}
    assert not [form for form in forms if form in data]
    assert len(digests) > 10
    # The key and the table reach no response either.
    for label, text in run.bodies():
        assert key.hex() not in text and not any(digest in text for digest in digests), label


def test_ac16_a_profile_that_appeared_after_detection_is_never_overwritten(flow) -> None:
    """AC16 (R5, A7): a profile file that appears at the target after the UI
    detected the first run makes screen 4 refuse; the file is byte-identical
    afterwards and *next* stays closed."""

    run = flow()
    run.through("chat_qa", upto=3)
    run.profile.write_text("enabled_modules: []\n# hand-written\n", encoding="utf-8")
    before = run.profile.read_bytes()
    refused = run.screen(4)
    assert _cause(refused) == wizard.REFUSAL_APPEARED and not _next_open(refused)
    assert run.profile.read_bytes() == before
    assert _credential_files(run.root) == []
    assert not run.ui.wizard.passed(4)


def test_ac16_a_malformed_detector_names_the_field_and_keeps_next_closed(flow) -> None:
    """AC16 (R5): the generated spoken-keyword profile with a malformed
    detector fails the real configuration check (exit 2); screen 4 names
    the offending field path in plain language and keeps *next* closed."""

    run = flow()
    run.platform.accept(TOK_A)
    assert _passed(run.choice("spoken_keyword", **{"parameter.spoken_keyword.detector.kind": "telepathy"}))
    assert _passed(run.platform_screen())
    assert _passed(run.model_screen("spoken_keyword"))
    failed = run.screen(4)
    assert _cause(failed) == wizard.CHECK_FAILED_AT and not _next_open(failed)
    field = "modules.audio_input.wake_word.detector.kind"
    expected = _EN.sentence("en", "wizard.check_failed_at", field=field)
    assert _html.escape(expected) in _text_of(failed)
    assert "telepathy" not in _text_of(failed)
    assert run.screen(5).status == 409 and run.dry.calls == []


def test_ac16_the_profile_view_is_read_only_and_no_new_route_takes_yaml(flow) -> None:
    """AC16 (R5, D6): the read-only view renders the written profile with 0
    form controls; neither ``/wizard`` nor ``/language`` accepts YAML content
    (a YAML body or field writes nothing); the editor's routes are unchanged."""

    run = flow()
    run.through("chat_qa", upto=4)
    view = run.get(view="profile")
    page = _text_of(view)
    assert view.status == 200
    assert _html.escape(run.profile.read_text(encoding="utf-8"), quote=True) in page
    assert re.search(r"<(form|input|textarea|select|button)\b", page) is None
    assert 'id="view-profile"' in _text_of(run.get(screen="4"))

    written = run.profile.read_bytes()
    marker = "modules_directory: /yaml-injected\nenabled_modules: [shell]\n"
    for path in ("/wizard", "/language"):
        run.post(path, {}, content_type="application/yaml", body=marker.encode())
        run.post(path, {"screen": "4", "yaml": marker, "profile": marker, "content": marker})
        assert run.profile.read_bytes() == written
    assert run.ui.wizard.owns_profile()
    assert "yaml-injected" not in run.profile.read_text(encoding="utf-8")
    assert "yaml-injected" not in json.dumps(run.state())

    editor = {
        "/": frozenset({"GET"}), "/core": frozenset({"GET"}), "/module/": frozenset({"GET"}),
        "/save": frozenset({"POST"}), "/remove": frozenset({"POST"}),
        "/check": frozenset({"POST"}), "/apply": frozenset({"POST"}),
    }
    assert {path: run.ui.routes[path][0] for path in editor} == editor
    assert {path: run.ui.routes[path][0] for path in ("/wizard", "/language")} == {
        "/wizard": frozenset({"GET", "POST"}), "/language": frozenset({"POST"}),
    }


def test_the_wizard_routes_sit_behind_the_existing_guards(flow) -> None:
    """R1: Host, session, method, Origin and CSRF refuse with the existing
    routes' status codes, before any wizard work is done."""

    run = flow()
    run.platform.accept(TOK_A)
    assert _passed(run.choice())
    state_before = wizard.state_path(run.ui.base_path).read_bytes()

    def request(method: str, path: str, **headers: str) -> Any:
        body = urlencode({"screen": "2", "language": "fr", "csrf_token": headers.pop("csrf", run.csrf)})
        return run.ui.handle(
            config_ui.UIRequest(
                method=method, path=path, query={},
                headers={"content-type": "application/x-www-form-urlencoded", **headers},
                body=body.encode(),
            )
        )

    good = {"host": "127.0.0.1:8765", "cookie": run.cookie}
    for path in ("/wizard", "/language"):
        assert request("POST", path, host="evil.invalid", cookie=run.cookie).status == 403
        assert request("POST", path, host="127.0.0.1:8765").status == 401
        assert request("POST", path, **good, origin="http://evil.invalid").status == 403
        assert request("POST", path, **good, csrf="wrong").status == 403
    assert request("GET", "/language", **good).status == 405
    assert request("PUT", "/wizard", **good, **{"x-csrf-token": run.csrf}).status == 405
    assert run.platform.validations == [] and run.model.requests == []
    assert wizard.state_path(run.ui.base_path).read_bytes() == state_before


def _sweep_failures(texts: list[tuple[str, str]], submitted: tuple[str, ...]) -> None:
    classes = re.compile(r"\b(" + "|".join(map(re.escape, _exception_class_names())) + r")\b")
    for label, text in texts:
        assert "Traceback" not in text and 'File "' not in text, label
        assert classes.search(text) is None, (label, classes.search(text))
        for value in submitted:
            assert not any(form in text for form in _value_forms(value)), (label, value)


def test_ac17_every_failure_page_is_plain_and_a_full_pass_starts_nothing(
    flow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC17 (R5): every failure of screens 2–4 (AC14–AC16, through the pages)
    and a dry run past 60 s on the fake clock render 0 ``Traceback``, 0
    ``File "``, 0 exception class names and 0 submitted values; a complete
    successful pass through screens 1–5 calls the supervisor's ``start`` 0
    times."""

    run = flow()
    assert _passed(run.choice("channel_points"))
    texts: list[tuple[str, str]] = []
    for name, (answer, token) in PLATFORM_OUTCOMES.items():
        run.platform.default = answer
        run.platform.tokens.clear()
        # The AC14 answers name P10's submitted client id.
        response = run.platform_screen(token=f"{TOK_A}-{name}", client_id=SUBMITTED_CLIENT_ID)
        assert _cause(response) == token and not _next_open(response), name
        texts.append((name, _text_of(response)))
    run.platform.default = None
    run.platform.accept(TOK_A)
    run.platform.rewards = _json(200, {"data": []})
    empty = run.platform_screen()
    assert _cause(empty) == wizard.NO_REWARDS and not _next_open(empty)
    texts.append(("no rewards", _text_of(empty)))
    run.platform.rewards = _json(200, {"data": WIZARD_REWARDS})
    run.platform.accept(TOK_B)
    listed = run.platform_screen(token=TOK_B)
    assert _cause(listed) == wizard.REWARD_NOT_PICKED
    assert all(_html.escape(reward["title"]) in _text_of(listed) for reward in WIZARD_REWARDS)
    assert _text_of(listed).count('class="reward"') == 2
    assert _passed(run.screen(2, **{"parameter.channel_points.reward_id": WIZARD_REWARDS[1]["id"]}))
    for name, (answer, token) in MODEL_OUTCOMES.items():
        run.model.answer = answer
        response = run.model_screen("channel_points", key=f"{WIZARD_MODEL_KEY}-{name}")
        assert _cause(response) == token and not _next_open(response), name
        texts.append((name, _text_of(response)))
    run.model.answer = _json(200, _COMPLETION)
    assert _passed(run.model_screen("channel_points"))

    # Screen 4's refusal (AC16) and screen 5 past its bound.
    run.profile.write_text("enabled_modules: []\n", encoding="utf-8")
    texts.append(("appeared", _text_of(run.screen(4))))
    run.profile.unlink()
    assert _passed(run.screen(4))

    def late(event: Any, clock: _Clock) -> Any:
        clock.now += 61.0
        return _dry_record()(event, clock)

    run.dry.make = late
    slow = run.screen(5)
    assert _cause(slow) == wizard.DRY_RUN_TIMED_OUT and not run.state()["completed"]
    texts.append(("dry run > 60 s", _text_of(slow)))
    submitted = WIZARD_VALUES + (SUBMITTED_CLIENT_ID,) + tuple(f"{TOK_A}-{name}" for name in PLATFORM_OUTCOMES) + tuple(
        f"{WIZARD_MODEL_KEY}-{name}" for name in MODEL_OUTCOMES
    )
    _sweep_failures(texts, submitted)
    _sweep_failures(run.bodies(), submitted)

    # A complete successful pass, on another profile directory: 0 starts.
    other = flow(name="other.yaml")
    other.through("chat_qa")
    assert other.state()["completed"] is True
    assert other.starts == 0 and run.starts == 0
    assert other.ui.child is None


def test_ac14_the_platform_answer_fills_the_identities_of_the_written_profile(flow) -> None:
    """AC14 (R5, through the screens): the login is shown, and the
    broadcaster and bot identities of the written profile are the answer's
    user id, never typed."""

    run = flow()
    run.through("chat_qa", upto=1)
    response = run.platform_screen()
    assert _html.escape(_EN.sentence("en", "platform.connected", login="streamer_x")) in _text_of(response)
    shown = _text_of(run.get(screen="2"))
    assert _passed(run.model_screen()) and _passed(run.screen(4))
    document = yaml.safe_load(run.profile.read_text(encoding="utf-8"))
    assert document["modules"]["twitch"]["broadcaster_id"] == "123"
    assert document["modules"]["twitch"]["bot_user_id"] == "123"
    assert document["modules"]["twitch"]["companion_name"] == "Nova"
    assert 'value=""' in shown


@posix_only
def test_ac11_no_response_carries_a_value_and_inputs_render_empty_and_kept(flow) -> None:
    """AC11 (response sweep, R4): over a full pass — the read-only profile
    view and screen 2's re-render after a wrong client id included — the
    profile, the overlay and every response hold 0 occurrences of a value
    in plain, URL-encoded, HTML-escaped or JSON-escaped form; a credential
    input renders empty and "kept"; the credential file is owner-only and
    holds the values under their names."""

    run = flow()
    run.platform.accept(TOK_A)
    assert _passed(run.choice())
    wrong = run.platform_screen(client_id="client-wrong-1y")
    assert _cause(wrong) == wizard.CLIENT_ID_MISMATCH
    assert _passed(run.platform_screen())
    assert _passed(run.model_screen()) and _passed(run.screen(4))
    run.get(view="profile")
    kept = _text_of(run.get(screen="2"))
    for name in ("TWITCH_CLIENT_ID", "TWITCH_CLIENT_SECRET", "TWITCH_ACCESS_TOKEN"):
        field = re.search(rf'<input type="password"[^>]*name="credential\.{name}"[^>]*>', kept)
        assert field is not None and 'value=""' in field.group(0) and 'data-state="kept"' in field.group(0)
    assert _html.escape(_EN.sentence("en", "wizard.kept")) in kept
    assert _passed(run.screen(5))

    (stored,) = _credential_files(run.root)
    assert stored.name == "profile.yaml.credentials.json"
    assert stat.S_IMODE(stored.stat().st_mode) == 0o600
    held = json.loads(stored.read_text(encoding="utf-8"))
    assert held == {
        "TWITCH_CLIENT_ID": WIZARD_CLIENT_ID, "TWITCH_CLIENT_SECRET": WIZARD_SECRET,
        "TWITCH_ACCESS_TOKEN": TOK_A, "MODEL_API_KEY": WIZARD_MODEL_KEY,
    }
    files = [run.profile, run.profile.with_name("profile.local.yaml"), wizard.state_path(run.profile)]
    texts = run.bodies() + [(os.fspath(path), path.read_text(encoding="utf-8")) for path in files if path.exists()]
    for value in (TOK_A, WIZARD_CLIENT_ID, WIZARD_SECRET, WIZARD_MODEL_KEY, "client-wrong-1y"):
        for label, text in texts:
            assert not any(form in text for form in _value_forms(value)), (label, value)
    location = _EN.sentence(
        "en", "wizard.credentials.owner_only", directory=os.fspath(run.root / "xdg" / PRODUCT)
    )
    screen_four = [text for label, text in run.bodies() if label.startswith("POST /wizard") and 'data-screen="4"' in text]
    assert screen_four and all(_html.escape(location) in text for text in screen_four)


def test_ac11_screen_four_names_the_windows_directory_and_why(
    flow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC11 (R4, A1): with the platform forced to Windows and
    ``LOCALAPPDATA=<tmp>``, the credential file is under
    ``<tmp>/twitch-ia-compagnon/`` and screen 4 names that directory and why."""

    local = tmp_path / "local"
    local.mkdir()
    monkeypatch.setattr(locations, "running_platform", lambda: locations.WINDOWS)
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    run = flow()
    run.through("chat_qa", upto=3)
    page = _text_of(run.get(screen="4"))
    directory = local / PRODUCT
    sentence = _EN.sentence("en", "wizard.credentials.local_appdata", directory=os.fspath(directory))
    assert _html.escape(sentence) in page
    assert _passed(run.screen(4))
    assert (directory / "profile.yaml.credentials.json").exists()


@pytest.mark.parametrize("platform", ["posix", "windows"])
def test_ac11_a_refused_directory_stores_nothing_and_keeps_next_closed(
    flow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, platform: str
) -> None:
    """AC11 (R4, A1): a group-writable per-user directory (POSIX) or an unset
    ``LOCALAPPDATA`` (Windows): no credential file exists, no file in the
    profile's or the per-user directory holds a value, screen 4 states the
    refusal and keeps *next* closed."""

    if platform == "posix":
        if os.name != "posix":
            pytest.skip("POSIX modes")
        directory = tmp_path / "xdg" / PRODUCT
        directory.mkdir(parents=True)
        directory.chmod(0o770)
        token = wizard.REFUSAL_SHARED
    else:
        monkeypatch.setattr(locations, "running_platform", lambda: locations.WINDOWS)
        monkeypatch.delenv("LOCALAPPDATA", raising=False)
        token = wizard.REFUSAL_NO_DIRECTORY
    run = flow()
    run.through("chat_qa", upto=3)
    refused = run.screen(4)
    assert _cause(refused) == token and not _next_open(refused)
    assert not run.ui.wizard.passed(4) and run.screen(5).status == 409
    assert _credential_files(run.root) == []
    for value in (TOK_A, WIZARD_CLIENT_ID, WIZARD_SECRET, WIZARD_MODEL_KEY):
        assert _files_holding([run.profile.parent, tmp_path / "xdg"], value) == []
    assert run.dry.calls == []


def test_ac28_a_corrected_name_regenerates_the_wizard_own_files(flow, tmp_path: Path) -> None:
    """AC28 (R5, A7): after screen 4 wrote the files, a companion-name change
    on screen 1 invalidates 1–5 (screen 5 refused); re-passing 1–4 replaces
    the profile with the new name, leaving 1 profile and 1 credential file.
    Once screen 5 completed, a new ConfigUI lands on the console (the A2
    default location; with ``--config`` it is the editor, AC2)."""

    run = flow(default=True)
    run.through("chat_qa", upto=4)
    assert "Nova" in run.profile.read_text(encoding="utf-8")
    changed = run.choice("chat_qa", **{"parameter.chat_qa.companion_name": "Orion"})
    assert _passed(changed) and run.ui.wizard.passed_screens() == (1,)
    assert run.get(screen="5").status == 409 and run.screen(5).status == 409
    assert _passed(run.platform_screen(token="", client_id=""))
    assert _passed(run.model_screen(key=""))
    assert _passed(run.screen(4))
    document = yaml.safe_load(run.profile.read_text(encoding="utf-8"))
    assert document["modules"]["twitch"]["companion_name"] == "Orion"
    assert sorted(path.name for path in run.profile.parent.glob("*.yaml")) == ["profile.yaml"]
    assert [path.name for path in _credential_files(tmp_path)] == ["profile.yaml.credentials.json"]
    assert _passed(run.screen(5))
    assert run.state()["completed"] is True
    after = flow(default=True)
    assert after.location == "/console"


def test_ac28_a_profile_changed_by_another_writer_is_refused_naming_the_editor(flow) -> None:
    """AC28 (A7): 1 byte appended to the written profile by another writer →
    re-passing screen 4 refuses, the file is byte-identical afterwards and
    the cause names the editor as the next action."""

    run = flow()
    run.through("chat_qa", upto=4)
    with open(run.profile, "ab") as handle:
        handle.write(b"#")
    before = run.profile.read_bytes()
    refused = run.screen(4)
    assert _cause(refused) == wizard.REFUSAL_CHANGED and not _next_open(refused)
    assert run.profile.read_bytes() == before
    next_action = re.search(r'<p class="next-action">([^<]*)</p>', _text_of(refused)).group(1)
    assert next_action == _html.escape(_EN.sentence("en", "wizard.refusal.file_changed_outside_wizard.next"))
    assert "editor" in next_action
    assert not run.ui.wizard.owns_profile()


@pytest.mark.parametrize(
    "make, cause",
    [
        (lambda event, clock: None, wizard.DRY_RUN_NO_RECORD),
        (_dry_record(run_id=""), wizard.DRY_RUN_INCOMPLETE),
        (_dry_record(outcome=""), wizard.DRY_RUN_INCOMPLETE),
        (_dry_record(origin="runtime"), wizard.DRY_RUN_INCOMPLETE),
        (_dry_record(event={"input": "twitch", "kind": "message", "message_id": "another"}),
         wizard.DRY_RUN_INCOMPLETE),
        (_dry_record(outcome="not_triggered"), wizard.DRY_RUN_UNEXPECTED),
        (_dry_record(outcome="refused"), wizard.DRY_RUN_UNEXPECTED),
    ],
)
def test_ac29_screen_five_completes_only_on_a_recorded_run(flow, make: Any, cause: str) -> None:
    """AC29 (screen half, R5): no record, a record without a run id, an
    outcome or the ``dry_run`` origin naming the driven event, or one whose
    outcome is not the profile's expected one: screen 5 stays open, the state
    is not completed, a cause and next action are shown, and another attempt
    on the corrected entry completes it."""

    run = flow()
    run.through("chat_qa", upto=4)
    run.dry.make = make
    failed = run.screen(5)
    assert _cause(failed) == cause and _screen_of(failed) == 5
    assert 'class="next-action"' in _text_of(failed) and 'name="screen" value="5"' in _text_of(failed)
    assert run.state()["completed"] is False and not run.ui.wizard.passed(5)
    assert _location(run.ui) == "/wizard"
    run.dry.make = _dry_record()
    done = run.screen(5)
    assert _passed(done) and 'id="console"' in _text_of(done)
    assert _html.escape(_EN.sentence("en", "wizard.dry_run_completed", run_id="run-dry-0001")) in _text_of(done)
    assert run.state()["completed"] is True and len(run.dry.calls) == 2
    assert run.get().status == 303


def test_ac29_a_dry_run_past_its_bound_or_refused_by_the_entry_stays_open(flow) -> None:
    """AC29 (R5): an entry still running at 60 s on the fake clock, or one
    raising its value-free refusal, leaves screen 5 open and retryable."""

    run = flow()
    run.through("chat_qa", upto=4)

    async def stuck(path: Path, event: Any, **options: Any) -> Any:
        run.clock.now += 60.0
        await asyncio.Event().wait()

    run.ui.wizard.seams.dry_runner = stuck
    stalled = run.screen(5)
    assert _cause(stalled) == wizard.DRY_RUN_TIMED_OUT and run.state()["completed"] is False

    async def refused(path: Path, event: Any, **options: Any) -> Any:
        raise application.DryRunError(application.DRY_RUN_NO_RECORD)

    run.ui.wizard.seams.dry_runner = refused
    assert _cause(run.screen(5)) == wizard.DRY_RUN_NO_RECORD
    run.ui.wizard.seams.dry_runner = run.dry
    assert _passed(run.screen(5)) and run.state()["completed"] is True


@pytest.mark.parametrize("profile_id", PROFILE_ORDER)
def test_ac29_each_profile_completes_on_the_real_dry_run_record(
    flow, monkeypatch: pytest.MonkeyPatch, profile_id: str
) -> None:
    """AC29 (R5, with P11's real entry and a fake model answering
    ``"bonjour"``): each profile's screen 5 completes on the record the dry
    run returns — a completed run for the three triggering profiles, "not
    triggered" with its reason and 0 model requests for silent — the event
    it drove being the one the wizard built; nothing is started."""

    spied = _Spied(monkeypatch)
    model = _bonjour()
    records: list[Any] = []

    async def real(path: Path, event: Any, *, environ: Any, clock: Any, timeout: float) -> Any:
        record = await application.dry_run(
            path, event, environ=environ, model_transport=lambda: model, timeout=timeout
        )
        records.append(record)
        return record

    run = flow()
    run.dry.make = None
    run.ui.wizard.seams.dry_runner = real
    run.through(profile_id)
    (record,) = records
    assert run.state()["completed"] is True and run.starts == 0
    assert record.origin == "dry_run" and record.run_id
    page = _text_of(run.responses[-1][1])
    if profile_id == "silent":
        assert record.outcome == "not_triggered" and model.post_calls == []
        sentence = _EN.sentence(
            "en", "wizard.dry_run_not_triggered", run_id=record.run_id, reason=record.reason
        )
    else:
        assert record.outcome == "completed" and list(spied.records) == [record.run_id]
        assert len(model.post_calls) == 1
        sentence = _EN.sentence("en", "wizard.dry_run_completed", run_id=record.run_id)
    assert _html.escape(sentence) in page
    assert spied.processes == 0


def _fresh(flow, platform: _PlatformFake | None = None, **options: Any) -> _Flow:
    """A new ConfigUI over the same directory, with fresh fake endpoints."""

    clock = _Clock()
    return flow(clock=clock, platform=platform or _PlatformFake(clock), **options)


def test_ac30_a_restart_before_screen_five_resumes_only_screen_one(flow) -> None:
    """AC30 (a): after screen 4 wrote the files, a new instance lands on the
    wizard; screen 1 shows its recorded inputs as passed, the credentials
    render empty and "kept"; screens 3–5 are refused until screen 2 passes
    again; passing 2–4 makes exactly 1 platform validation and 1 model
    request, and screen 5 runs its dry run once."""

    first = flow()
    first.through("chat_qa", upto=4)
    assert first.state()["passed"] == [1]

    second = _fresh(flow)
    second.platform.accept(TOK_A)
    assert second.location == "/wizard"
    one = _text_of(second.get(screen="1"))
    assert 'data-screen="1" data-passed="true"' in one and 'value="Nova"' in one
    two = _text_of(second.get(screen="2"))
    assert two.count('value="" autocomplete="off" data-state="kept"') == 3
    for screen in ("3", "4", "5"):
        assert second.get(screen=screen).status == 409
        assert second.screen(int(screen)).status == 409
    assert second.platform.validations == [] and second.model.requests == []
    assert _passed(second.platform_screen(token="", client_id=""))
    assert _passed(second.model_screen(key=""))
    assert _passed(second.screen(4))
    assert second.platform.validations == [TOK_A] and len(second.model.requests) == 1
    assert _passed(second.screen(5))
    assert len(second.dry.calls) == 1 and second.dry.calls[0]["environ"]["TWITCH_ACCESS_TOKEN"] == TOK_A
    _no_value_in_state(second)


def _no_value_in_state(run: _Flow) -> None:
    data = wizard.state_path(run.ui.base_path).read_bytes()
    for value in WIZARD_VALUES + ("tok-new-wizard-1q",):
        assert value.encode() not in data


def test_ac30_b_an_expired_stored_token_is_refused_and_a_new_one_passes(flow) -> None:
    """AC30 (b): the platform now answers 401 for the stored token: screen 2
    shows the 401 cause with *next* closed; a new token on the same screen
    passes it."""

    first = flow()
    first.through("chat_qa", upto=4)
    second = _fresh(flow)
    refused = second.platform_screen(token="", client_id="")
    assert _cause(refused) == wizard.UNAUTHORIZED and not _next_open(refused)
    assert second.platform.validations == [TOK_A]
    second.platform.accept("tok-new-wizard-1q")
    assert _passed(second.platform_screen(token="tok-new-wizard-1q", client_id=""))
    assert second.platform.validations == [TOK_A, "tok-new-wizard-1q"]
    assert _passed(second.model_screen(key="")) and _passed(second.screen(4))
    stored = json.loads(_credential_files(second.root)[0].read_text(encoding="utf-8"))
    assert stored["TWITCH_ACCESS_TOKEN"] == "tok-new-wizard-1q"
    _no_value_in_state(second)


def test_ac30_c_a_failed_screen_five_is_retried_after_a_restart(flow) -> None:
    """AC30 (c): after a failed screen 5, a new instance lands on the wizard
    and screen 5 can be retried to success."""

    first = flow()
    first.through("chat_qa", upto=4)
    first.dry.make = lambda event, clock: None
    assert _cause(first.screen(5)) == wizard.DRY_RUN_NO_RECORD
    second = _fresh(flow)
    second.platform.accept(TOK_A)
    assert second.location == "/wizard"
    assert _passed(second.platform_screen(token="", client_id=""))
    assert _passed(second.model_screen(key="")) and _passed(second.screen(4))
    assert _passed(second.screen(5)) and second.state()["completed"] is True


def test_ac30_d_a_completed_wizard_is_never_offered_again(flow) -> None:
    """AC30 (d): after screen 5 completed, the access URL lands on the
    console and every wizard route redirects to it, with no wizard screen."""

    flow(default=True).through("chat_qa")
    second = _fresh(flow, default=True)
    assert second.location == "/console"
    for response in (
        second.get(),
        second.get(screen="3"),
        second.get(view="profile"),
        second.screen(1, profile="silent"),
        second.screen(5),
    ):
        assert response.status == 303 and dict(response.headers)["Location"] == "/console"
        assert b"data-screen" not in response.body
    assert second.platform.validations == [] and second.dry.calls == []


def test_ac30_e_a_profile_changed_after_screen_four_is_refused_after_a_restart(flow) -> None:
    """AC30 (e): the profile changed by another writer after screen 4: the
    new instance lands on the wizard, screen 4 refuses and leaves the file
    byte-identical, and the cause names the editor."""

    first = flow()
    first.through("chat_qa", upto=4)
    first.profile.write_text(first.profile.read_text(encoding="utf-8") + "# edited\n", encoding="utf-8")
    before = first.profile.read_bytes()
    second = _fresh(flow)
    second.platform.accept(TOK_A)
    assert second.location == "/wizard"
    assert _passed(second.platform_screen(token="", client_id=""))
    assert _passed(second.model_screen(key=""))
    refused = second.screen(4)
    assert _cause(refused) == wizard.REFUSAL_CHANGED and not _next_open(refused)
    assert second.profile.read_bytes() == before
    assert "editor" in _EN.sentence("en", "wizard.refusal.file_changed_outside_wizard.next")
    _no_value_in_state(second)


def test_ac5_the_wizard_screen_keys_are_in_both_files() -> None:
    """AC5 re-run (P12): the collector sees the screens' keys — every
    failure, refusal and location sentence of the wizard — and each is in
    both files with the same placeholders."""

    keys, dynamic = referenced_keys(KEY_SOURCES)
    assert dynamic == []
    files = _string_files()
    wizard_keys = {key for key in keys if key.startswith("wizard.")}
    assert len(wizard_keys) >= 90
    for token in (*wizard._WIZARD_SENTENCES, *wizard._REFUSAL_SENTENCES):
        assert f"wizard.{token}" in wizard_keys or f"wizard.refusal.{token}" in wizard_keys, token
    for key in wizard_keys:
        assert key in files["en"] and key in files["fr"], key
        assert i18n.placeholders(files["en"][key]) == i18n.placeholders(files["fr"][key]), key


def test_ac14_a_cached_validation_does_not_carry_over_to_another_profile(flow) -> None:
    """AC14 (P12 fix round 1): a validation that passed for chat Q&A is not
    reused for channel points with the same credentials — its scopes are
    checked again, so the missing redemption scope fails screen 2."""

    run = flow()
    run.platform.accept(TOK_A, scopes=sorted(_profiles()["chat_qa"].scopes))
    assert _passed(run.choice("chat_qa"))
    assert _passed(run.platform_screen())
    assert run.platform.validations == [TOK_A]
    assert _passed(run.choice("channel_points"))
    response = run.platform_screen()
    assert _cause(response) == wizard.MISSING_SCOPE and not _next_open(response)
    assert run.platform.validations == [TOK_A, TOK_A]


def test_ac11_a_variable_in_the_config_path_reloads_the_stored_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC11 (A1, P12 fix round 1): with ``--config ${VAR}.yaml`` the UI reads
    back the credential file the wizard wrote under the expanded name."""

    _default_environment(monkeypatch, tmp_path / "xdg")
    profile, document = _wizard_profile(tmp_path / "site")
    monkeypatch.setenv("WIZARD_PROFILE", "profile")
    settings = config_ui.UISettings.from_argv(["--config", str(tmp_path / "site" / "${WIZARD_PROFILE}.yaml")])
    assert settings.base_path == profile.resolve()
    state = wizard.WizardState(settings.base_path)
    store = wizard.CredentialStore(settings.base_path, "linux", os.environ)
    assert store.write(FIXTURE_CREDENTIALS, document["secrets"], state) is None
    state.save(store)
    assert store.path is not None and store.path.name == "profile.yaml.credentials.json"
    assert settings.credential_path == store.path.resolve()
    names = [reference[2:-1] for reference in document["secrets"]]
    assert config_ui._stored_credentials(settings) == {name: FIXTURE_CREDENTIALS[name] for name in names}


# --------------------------------------------------------------------------- #
# P16 (R8): the package data and the dependency set (AC26)
# --------------------------------------------------------------------------- #


def test_ac26_pyproject_ships_the_strings_and_profiles_with_two_dependencies() -> None:
    """AC26 (R8): the runtime dependencies are exactly ``aiohttp`` and
    ``PyYAML``, and the package data has a ``core.config_ui`` line of exactly
    ``strings/*.json`` and ``profiles/*``. Read in process; nothing spawned."""

    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    names = [
        re.match(r"[A-Za-z0-9_.-]+", requirement).group(0)
        for requirement in project["project"]["dependencies"]
    ]
    assert names == ["aiohttp", "PyYAML"]
    package_data = project["tool"]["setuptools"]["package-data"]
    assert package_data["core.config_ui"] == ["strings/*.json", "profiles/*"]
    assert any(
        pattern == "core*" for pattern in project["tool"]["setuptools"]["packages"]["find"]["include"]
    )


# --------------------------------------------------------------------------- #
# P17 (R8): the first-run guide pair (AC24)
# --------------------------------------------------------------------------- #

FIRST_RUN_GUIDES = (ROOT / "docs" / "first-run.md", ROOT / "docs" / "first-run.fr.md")

#: Each platform's base directory as the guide spells it, and the environment
#: that makes :mod:`locations` resolve it under a placeholder root.
_GUIDE_BASES = {
    locations.LINUX: ("$XDG_CONFIG_HOME", {"XDG_CONFIG_HOME": "/placeholder"}),
    locations.WINDOWS: ("%APPDATA%", {"APPDATA": "/placeholder", "LOCALAPPDATA": "/local"}),
    locations.MACOS: ("~", {"HOME": "/placeholder"}),
}


def _as_guide_spells(platform: str, path: Path, root: str, spelling: str) -> str:
    """*path* under *root*, written from *spelling* with the platform's separator."""

    separator = "\\" if platform == locations.WINDOWS else "/"
    return separator.join((spelling, *path.relative_to(root).parts))


def _code_spans(path: Path) -> set[str]:
    return set(re.findall(r"`([^`\n]+)`", path.read_text(encoding="utf-8")))


def test_ac24_the_guide_names_the_default_location_on_each_platform() -> None:
    """AC24 (R8): both guides name, in a code span, the A2 default profile of
    Linux, Windows and macOS, as :func:`locations.default_profile_path`
    resolves it (and the Linux fallback without ``XDG_CONFIG_HOME``)."""

    expected = {
        _as_guide_spells(
            platform, locations.default_profile_path(platform, environ), "/placeholder", spelling
        )
        for platform, (spelling, environ) in _GUIDE_BASES.items()
    }
    fallback = locations.default_profile_path(locations.LINUX, {"HOME": "/placeholder"})
    expected.add(_as_guide_spells(locations.LINUX, fallback, "/placeholder", "~"))
    assert expected == {
        "$XDG_CONFIG_HOME/twitch-ia-compagnon/profile.yaml",
        "~/.config/twitch-ia-compagnon/profile.yaml",
        "%APPDATA%\\twitch-ia-compagnon\\profile.yaml",
        "~/Library/Application Support/twitch-ia-compagnon/profile.yaml",
    }
    for guide in FIRST_RUN_GUIDES:
        assert expected <= _code_spans(guide), (guide.name, sorted(expected - _code_spans(guide)))


def test_ac24_the_guide_names_the_credential_file_and_its_directory_on_each_platform() -> None:
    """AC24 (R8, A1): both guides name the credential file by its name,
    ``<profile-file-name>.credentials.json`` and its value at the default,
    and its per-user directory on each platform as
    :func:`locations.credential_directory` resolves it (``%LOCALAPPDATA%`` on
    Windows, never the roaming ``%APPDATA%``), trailing separator included."""

    names = {
        "<profile-file-name>" + locations.CREDENTIAL_SUFFIX,
        locations.credential_file_name(locations.PROFILE_FILE_NAME),
    }
    assert names == {"<profile-file-name>.credentials.json", "profile.yaml.credentials.json"}
    bases = dict(_GUIDE_BASES)
    bases[locations.WINDOWS] = ("%LOCALAPPDATA%", {"APPDATA": "/roaming", "LOCALAPPDATA": "/placeholder"})
    directories = set()
    for platform, (spelling, environ) in bases.items():
        directory = locations.credential_directory(platform, environ)
        assert directory is not None
        separator = "\\" if platform == locations.WINDOWS else "/"
        directories.add(_as_guide_spells(platform, directory, "/placeholder", spelling) + separator)
    assert directories == {
        "$XDG_CONFIG_HOME/twitch-ia-compagnon/",
        "%LOCALAPPDATA%\\twitch-ia-compagnon\\",
        "~/Library/Application Support/twitch-ia-compagnon/",
    }
    for guide in FIRST_RUN_GUIDES:
        spans = _code_spans(guide)
        assert names | directories <= spans, (guide.name, sorted((names | directories) - spans))


def test_ac24_the_guide_documents_the_install_path_and_the_launcher() -> None:
    """R8 (A3b): both guides point to the install path, ``docs/installation.md``,
    and give the ``--install-launcher`` command as a fenced command."""

    for guide in FIRST_RUN_GUIDES:
        text = guide.read_text(encoding="utf-8")
        assert "](installation.md)" in text, guide.name
        assert "\npython -m core.config_ui --install-launcher\n" in text, guide.name


def test_ac24_the_documentation_index_links_both_guides() -> None:
    """AC24 (R8): ``docs/README.md`` links the English guide and its translation."""

    index = (ROOT / "docs" / "README.md").read_text(encoding="utf-8")
    for guide in FIRST_RUN_GUIDES:
        assert guide.is_file(), guide.name
        assert f"]({guide.name})" in index, guide.name


# --------------------------------------------------------------------------- #
# P18 (R8, A3b): the trial-record template and its approval checker (AC25)
# --------------------------------------------------------------------------- #

TRIAL_RECORD = ROOT / "docs" / "campaigns" / "phase9" / "trial-record.md"

#: Amendment A1's criterion, as the record must state it.
TRIAL_CRITERION = (
    "from a working install to a first run in under fifteen minutes, "
    "without a terminal and without YAML"
)

#: The 13 labelled fields of R8, in the template's order.
TRIAL_FIELDS = (
    "Date",
    "Commit",
    "Machine and platform",
    "Install method (who, by which means, terminal used)",
    "Install duration (minutes)",
    "Profile",
    "Trial start",
    "Per-screen times",
    "First-run time",
    "Elapsed minutes",
    "Terminal/YAML touched",
    "Outcome",
    "Limits",
)
_INSTALL_METHOD, _INSTALL_DURATION, _ELAPSED = TRIAL_FIELDS[3], TRIAL_FIELDS[4], TRIAL_FIELDS[9]
_PLATFORM, _START, _SCREENS, _FIRST_RUN, _TOUCHED = (
    TRIAL_FIELDS[2],
    TRIAL_FIELDS[6],
    TRIAL_FIELDS[7],
    TRIAL_FIELDS[8],
    TRIAL_FIELDS[10],
)
#: The five sub-fields of "Per-screen times".
TRIAL_SCREENS = tuple(f"Screen {n} passed" for n in range(1, 6))
TRIAL_STATUSES = ("not run", "run", "approved")

#: The single time format the template prescribes: ISO local time, to the
#: second, with its offset. ``Z``, a space, a missing offset or missing
#: seconds are refused rather than guessed.
_TRIAL_TIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}")
_TRIAL_MINUTES = re.compile(r"\d+(?:\.\d+)?")
_TRIAL_PLATFORM = re.compile(r"\b(?:Linux|macOS|Windows)\b", re.IGNORECASE)
_NON_VALUES = {"-", "--", "\u2013", "\u2014", "?", "...", "\u2026", "n/a", "na", "none", "tbd", "todo", "unknown"}
_TRIAL_LINE = re.compile(r"^(?P<indent>\s*)- (?P<label>[^:\n]+):(?P<value>.*)$")


def _trial_values(text: str) -> dict[str, list[str]]:
    """Every known label of the ``## Record`` section mapped to its values
    (top-level fields at no indent, screen sub-fields indented), a value
    stripped of whitespace and of one pair of enclosing backticks."""

    _, heading, record = text.partition("\n## Record\n")
    values: dict[str, list[str]] = {}
    if not heading:
        return values
    for line in record.splitlines():
        if line.startswith("## "):
            break
        match = _TRIAL_LINE.match(line)
        if match is None:
            continue
        label = match["label"]
        known = TRIAL_SCREENS if match["indent"] else TRIAL_FIELDS
        if label in known:
            value = match["value"].strip()
            if len(value) >= 2 and value[0] == value[-1] == "`":
                value = value[1:-1].strip()
            values.setdefault(label, []).append(value)
    return values


def _trial_status(text: str) -> str | None:
    match = re.search(r"^Status: (.*)$", text, re.MULTILINE)
    return match[1].strip() if match else None


def trial_record_problems(text: str) -> list[str]:
    """Every reason AC25 refuses an approval of the trial record *text*; an
    empty list is an approvable record. It checks completeness and
    consistency only: whether a person was fast is the record's to say (D1)."""

    values = _trial_values(text)
    problems: list[str] = []
    given: dict[str, str] = {}
    for label in (*TRIAL_FIELDS, *TRIAL_SCREENS):
        found = values.get(label, [])
        if not found:
            problems.append(f"{label}: absent")
        elif len(found) > 1:
            problems.append(f"{label}: given {len(found)} times")
        elif label != _SCREENS:
            if found[0]:
                given[label] = found[0]
            else:
                problems.append(f"{label}: empty")

    method = given.get(_INSTALL_METHOD)
    if method is not None and (method.lower() in _NON_VALUES or re.fullmatch(r"<.*>", method)):
        problems.append(f"{_INSTALL_METHOD}: not a value ({method!r})")
    minutes: dict[str, float] = {}
    for label in (_INSTALL_DURATION, _ELAPSED):
        value = given.get(label)
        if value is None:
            continue
        if _TRIAL_MINUTES.fullmatch(value):
            minutes[label] = float(value)
        else:
            problems.append(f"{label}: not a value, a number of minutes is expected ({value!r})")
    platform = given.get(_PLATFORM)
    if platform is not None and not _TRIAL_PLATFORM.search(platform):
        problems.append(f"{_PLATFORM}: names none of Linux, macOS or Windows ({platform!r})")
    touched = given.get(_TOUCHED)
    if touched is not None and touched.lower() != "no":
        problems.append(f"{_TOUCHED}: not \"no\" ({touched!r})")

    times: dict[str, datetime] = {}
    for label in (_START, *TRIAL_SCREENS, _FIRST_RUN):
        value = given.get(label)
        if value is None:
            continue
        if _TRIAL_TIME.fullmatch(value):
            try:
                times[label] = datetime.fromisoformat(value)
            except ValueError as error:
                problems.append(f"{label}: not a real date and time ({value!r}: {error})")
        else:
            problems.append(
                f"{label}: not an ISO local time with offset, as 2026-10-25T14:03:00+02:00 ({value!r})"
            )
    sequence = [(label, times[label]) for label in (*TRIAL_SCREENS, _FIRST_RUN) if label in times]
    start = times.get(_START)
    if start is not None:
        early = [label for label, moment in sequence if moment < start]
        if early:
            problems.append(f"before the trial start: {', '.join(early)}")
    backwards = [
        f"{later} before {earlier}"
        for (earlier, first), (later, second) in zip(sequence, sequence[1:])
        if second < first
    ]
    if backwards:
        problems.append(f"times not non-decreasing: {', '.join(backwards)}")
    elapsed = minutes.get(_ELAPSED)
    if elapsed is not None:
        if start is not None and _FIRST_RUN in times:
            measured = (times[_FIRST_RUN] - start).total_seconds() / 60
            if abs(elapsed - measured) > 1:
                problems.append(
                    f"{_ELAPSED}: {_minutes_text(elapsed)} is not first-run time minus trial start "
                    f"({_minutes_text(measured)}, to the minute)"
                )
        if elapsed >= 15:
            problems.append(f"{_ELAPSED}: {_minutes_text(elapsed)} is 15 or more")
    return problems


def _minutes_text(minutes: float) -> str:
    return f"{minutes:g}"


_CONFORMING_TRIAL = {
    "Date": "2026-10-25",
    "Commit": "`2c123b8`",
    "Machine and platform": "the streamer's desktop, Windows 11 23H2",
    _INSTALL_METHOD: "the operator, by following docs/installation.md, with a terminal",
    _INSTALL_DURATION: "35",
    "Profile": "chat Q&A",
    _START: "2026-10-25T14:00:00+02:00",
    _SCREENS: "",
    "Screen 1 passed": "2026-10-25T14:01:10+02:00",
    "Screen 2 passed": "2026-10-25T14:03:00+02:00",
    "Screen 3 passed": "2026-10-25T14:05:20+02:00",
    "Screen 4 passed": "2026-10-25T14:08:00+02:00",
    "Screen 5 passed": "2026-10-25T14:10:40+02:00",
    _FIRST_RUN: "2026-10-25T14:11:30+02:00",
    _ELAPSED: "11.5",
    _TOUCHED: "no",
    "Outcome": "a run recorded in the console for a chat question",
    "Limits": "the install still needs a terminal (Phase 10)",
}


def _trial_record(**changes: str | None) -> str:
    """A record rendered as the template lays it out, the conforming values
    replaced by *changes* (a label given ``None`` is left out)."""

    fields = {**_CONFORMING_TRIAL, **changes}
    lines = ["# Trial record", "", "Status: run", "", "## Record", ""]
    for label in TRIAL_FIELDS:
        if fields.get(label) is not None:
            lines.append(f"- {label}: {fields[label]}".rstrip())
        if label == _SCREENS:
            lines.extend(
                f"  - {screen}: {fields[screen]}".rstrip()
                for screen in TRIAL_SCREENS
                if fields.get(screen) is not None
            )
    return "\n".join(lines) + "\n"


def _changed(label: str, value: str | None) -> dict[str, str | None]:
    return {label: value}


_TRIAL_VIOLATIONS = [
    *[
        pytest.param(_changed(label, None), f"{label}: absent", id=f"absent-{label}")
        for label in (*TRIAL_FIELDS, *TRIAL_SCREENS)
    ],
    *[
        pytest.param(_changed(label, ""), f"{label}: empty", id=f"empty-{label}")
        for label in (*TRIAL_FIELDS, *TRIAL_SCREENS)
        if label != _SCREENS
    ],
    *[
        pytest.param(_changed(_INSTALL_METHOD, value), "not a value", id=f"install-method-{value}")
        for value in ("n/a", "TBD", "-", "<who, means, terminal>")
    ],
    pytest.param(_changed(_INSTALL_DURATION, "about half an hour"), "number of minutes", id="duration-words"),
    pytest.param(_changed(_INSTALL_DURATION, "?"), "number of minutes", id="duration-placeholder"),
    pytest.param(_changed(_ELAPSED, "soon"), "number of minutes", id="elapsed-words"),
    pytest.param(_changed(_ELAPSED, "11 min"), "number of minutes", id="elapsed-unit"),
    pytest.param(_changed(_PLATFORM, "a laptop, Ubuntu 24.04"), "names none", id="platform-distribution"),
    pytest.param(_changed(_PLATFORM, "a Mac"), "names none", id="platform-mac"),
    pytest.param(_changed(_TOUCHED, "yes"), 'not "no"', id="touched-yes"),
    pytest.param(_changed(_TOUCHED, "only once"), 'not "no"', id="touched-once"),
    pytest.param(
        {"Screen 3 passed": "2026-10-25T14:08:00+02:00", "Screen 4 passed": "2026-10-25T14:05:20+02:00"},
        "Screen 4 passed before Screen 3 passed",
        id="screens-out-of-order",
    ),
    pytest.param(
        _changed("Screen 1 passed", "2026-10-25T13:59:00+02:00"),
        "before the trial start: Screen 1 passed",
        id="screen-before-start",
    ),
    pytest.param(
        {_START: "2026-10-25T14:10:50+02:00", _ELAPSED: "0.67"},
        "before the trial start: Screen 1 passed, Screen 2 passed, Screen 3 passed, "
        "Screen 4 passed, Screen 5 passed",
        id="start-after-the-screens",
    ),
    pytest.param(
        {"Screen 5 passed": "2026-10-25T14:12:00+02:00"},
        "First-run time before Screen 5 passed",
        id="first-run-before-screen-five",
    ),
    pytest.param(_changed(_ELAPSED, "9"), "is not first-run time minus trial start", id="elapsed-mismatch"),
    pytest.param(_changed(_ELAPSED, "13"), "is not first-run time minus trial start", id="elapsed-past-one"),
    pytest.param(
        {_FIRST_RUN: "2026-10-25T14:15:00+02:00", _ELAPSED: "15"}, "is 15 or more", id="elapsed-fifteen"
    ),
    pytest.param(
        {_FIRST_RUN: "2026-10-25T14:22:00+02:00", _ELAPSED: "22"}, "is 15 or more", id="elapsed-twenty-two"
    ),
    *[
        pytest.param(_changed(_START, value), "not an ISO local time with offset", id=f"start-{value}")
        for value in (
            "2026-10-25 14:00:00+02:00",
            "2026-10-25T14:00:00Z",
            "2026-10-25T14:00:00",
            "2026-10-25T14:00+02:00",
            "14:00",
        )
    ],
    pytest.param(
        _changed("Screen 2 passed", "25/10/2026 14:03"), "not an ISO local time with offset", id="screen-format"
    ),
    *[
        pytest.param(_changed(label, value), f"{label}: not a real date and time", id=f"impossible-{value}")
        for label, value in (
            (_START, "2026-02-30T14:00:00+02:00"),
            (_START, "2026-13-25T14:00:00+02:00"),
            ("Screen 3 passed", "2026-10-25T25:05:20+02:00"),
            ("Screen 4 passed", "2026-10-25T14:61:00+02:00"),
            (_FIRST_RUN, "2026-10-25T14:11:60+02:00"),
            (_FIRST_RUN, "2026-10-25T14:11:30+24:00"),
        )
    ],
]


def test_ac25_the_template_carries_the_thirteen_labels_and_the_criterion() -> None:
    """AC25 (R8, A3b): the record states the criterion in Amendment A1's
    words and lays out the 13 labelled fields of R8 once each, the five
    per-screen times under theirs, with the install among the limits; its
    status is one of the three the gate reads."""

    text = TRIAL_RECORD.read_text(encoding="utf-8")
    assert TRIAL_CRITERION in " ".join(text.split())
    assert len(TRIAL_FIELDS) == 13
    values = _trial_values(text)
    assert {label: len(found) for label, found in values.items()} == {
        label: 1 for label in (*TRIAL_FIELDS, *TRIAL_SCREENS)
    }
    assert "Limits lists what remains, the install step first" in text
    assert _trial_status(text) in TRIAL_STATUSES
    example = re.search(r"`(\d{4}-[^`]+)`", text)
    assert example is not None and _TRIAL_TIME.fullmatch(example[1])


def test_ac25_the_unfilled_template_is_not_approvable() -> None:
    """AC25: the record with every value blanked (the template as shipped)
    is refused, every field and screen named among the reasons."""

    text = TRIAL_RECORD.read_text(encoding="utf-8")
    blank = "\n".join(
        line[: match.end("label") + 1] if (match := _TRIAL_LINE.match(line)) else line
        for line in text.splitlines()
    )
    problems = trial_record_problems(blank)
    named = {problem.split(":", 1)[0] for problem in problems}
    assert named == {*TRIAL_FIELDS, *TRIAL_SCREENS} - {_SCREENS}, problems


def test_ac25_the_record_on_file_is_approved_only_without_a_problem() -> None:
    """AC25 (D1): the record on file is the operator's. While it is ``not
    run`` the checker reports it not approvable (asserted, not a failure);
    once its status says ``approved`` the checker must find no problem."""

    text = TRIAL_RECORD.read_text(encoding="utf-8")
    status = _trial_status(text)
    problems = trial_record_problems(text)
    if status == "approved":
        assert problems == []
    elif status == "not run":
        assert problems


def test_ac25_the_checker_accepts_a_conforming_record() -> None:
    """AC25: a complete, consistent record under fifteen minutes passes, with
    ±1 minute of rounding on the elapsed minutes either way."""

    assert trial_record_problems(_trial_record()) == []
    assert trial_record_problems(_trial_record(**{_ELAPSED: "12"})) == []
    assert trial_record_problems(_trial_record(**{_ELAPSED: "10.5"})) == []
    assert trial_record_problems(_trial_record(**{_TOUCHED: "No", _PLATFORM: "MacBook, macOS 15.1"})) == []


@pytest.mark.parametrize(("changes", "reason"), _TRIAL_VIOLATIONS)
def test_ac25_the_checker_refuses_each_violation_with_one_problem(
    changes: dict[str, str | None], reason: str
) -> None:
    """AC25: each refusal reason, alone in its own fixture, is the one problem
    reported."""

    problems = trial_record_problems(_trial_record(**changes))
    assert len(problems) == 1 and reason in problems[0], problems


def test_ac25_a_field_given_twice_or_outside_the_record_section_is_refused() -> None:
    """AC25: a label given twice is refused, and a record without its
    ``## Record`` section reports every field absent."""

    record = _trial_record().replace("- Outcome:", "- Outcome: first\n- Outcome:")
    assert trial_record_problems(record) == ["Outcome: given 2 times"]
    unsectioned = trial_record_problems(_trial_record().replace("## Record", "## Notes"))
    assert unsectioned == [f"{label}: absent" for label in (*TRIAL_FIELDS, *TRIAL_SCREENS)]


def test_ac25_an_impossible_time_is_reported_alongside_the_other_problems() -> None:
    """AC25: a time of the prescribed shape that names no real moment is a
    refusal reason like any other, not an error that hides the rest."""

    problems = trial_record_problems(
        _trial_record(**{_START: "2026-02-30T14:00:00+02:00", _TOUCHED: "yes", "Outcome": None})
    )
    assert [problem.split(":", 1)[0] for problem in problems] == ["Outcome", _TOUCHED, _START], problems
    assert "not a real date and time" in problems[2]
