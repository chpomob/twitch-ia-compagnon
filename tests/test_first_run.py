"""Phase 9: the first run (R1–R8).

P1 (plan DD1, DD2): the per-user locations of A1 and A2, and the two core
platform scans adapted to skip exactly the three paths that hold the
platform-named bytes phase 9 places under ``core/`` (flagged out-of-list).

P3 (plan DD4): the string table and its loader (R2; AC5, AC6's discovery
half), with the AST collector of the string keys ``wizard.py`` and
``console.py`` reference, run again by P12–P15 as keys appear.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

import pytest

from core.config_ui import i18n, locations
from core.config_ui.i18n import StringTable, StringTableError
from core.config_ui.locations import (
    PRODUCT,
    UNKNOWN_VERSION,
    credential_directory,
    credential_file_name,
    default_profile_path,
    product_version,
)
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
