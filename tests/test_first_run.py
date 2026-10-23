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
"""

from __future__ import annotations

import ast
import builtins
import hashlib
import io
import json
import os
import re
import secrets
import shutil
import stat
import sys
import webbrowser
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
import yaml

import core.config_ui as config_ui
from conftest import baseline_numstat
from core import main as application
from core.config_ui import i18n, locations, wizard
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
