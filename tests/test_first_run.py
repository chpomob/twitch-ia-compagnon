"""Phase 9: the first run (R1–R8).

P1 (plan DD1, DD2): the per-user locations of A1 and A2, and the two core
platform scans adapted to skip exactly the three paths that hold the
platform-named bytes phase 9 places under ``core/`` (flagged out-of-list).

P3 (plan DD4): the string table and its loader (R2; AC5, AC6's discovery
half), with the AST collector of the string keys ``wizard.py`` and
``console.py`` reference, run again by P12–P15 as keys appear.

P4 (plan DD5): the four ready-made profiles and their generator (R3; AC7's
data half, AC8, AC10), validated against the shipped manifests themselves.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

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
