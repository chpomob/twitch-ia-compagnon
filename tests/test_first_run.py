"""Phase 9: the first run (R1–R8).

P1 (plan DD1, DD2): the per-user locations of A1 and A2, and the two core
platform scans adapted to skip exactly the three paths that hold the
platform-named bytes phase 9 places under ``core/`` (flagged out-of-list).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.config_ui import locations
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
