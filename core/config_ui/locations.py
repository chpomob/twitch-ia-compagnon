"""Where the product keeps its per-user files, and which version it is (A1, A2).

This is the one code module under ``core/`` that holds the product's
distribution name (plan DD1): the per-user directories are named after it, so
it is a constant here and nowhere else. Every function takes the platform and
the environment as parameters, so a caller (and every test) forces them; only
:func:`running_platform` reads the process.

The platform is ``"linux"``, ``"windows"`` or ``"macos"`` (any other value is
treated as a POSIX desktop following the XDG layout, like ``"linux"``).
"""

from __future__ import annotations

import sys
from collections.abc import Mapping
from importlib import metadata
from pathlib import Path

#: The distribution name, which is also the per-user directory name.
PRODUCT = "twitch-ia-compagnon"

#: The default profile's file name inside the per-user directory (A2).
PROFILE_FILE_NAME = "profile.yaml"

#: The suffix appended to a profile's full file name to name its credential
#: file (A1): ``a.yaml`` and ``a.yml`` never share one.
CREDENTIAL_SUFFIX = ".credentials.json"

#: :func:`product_version` when the distribution is not installed (a bare
#: checkout). Stable, so both sides of a version comparison agree.
UNKNOWN_VERSION = "0+unknown"

WINDOWS = "windows"
MACOS = "macos"
LINUX = "linux"


def running_platform() -> str:
    """The running process's platform, in the names the functions here take."""

    if sys.platform.startswith("win"):
        return WINDOWS
    if sys.platform == "darwin":
        return MACOS
    return LINUX


def _home(environ: Mapping[str, str]) -> Path:
    home = environ.get("HOME") or environ.get("USERPROFILE")
    return Path(home) if home else Path.home()


def _xdg_config_home(environ: Mapping[str, str]) -> Path:
    # The XDG base-directory rules ignore an empty or relative value.
    configured = environ.get("XDG_CONFIG_HOME", "")
    if configured and Path(configured).is_absolute():
        return Path(configured)
    return _home(environ) / ".config"


def user_directory(platform: str, environ: Mapping[str, str]) -> Path:
    """The per-user configuration directory the default profile lives in (A2)."""

    if platform == WINDOWS:
        appdata = environ.get("APPDATA")
        base = Path(appdata) if appdata else _home(environ) / "AppData" / "Roaming"
        return base / PRODUCT
    if platform == MACOS:
        return _home(environ) / "Library" / "Application Support" / PRODUCT
    return _xdg_config_home(environ) / PRODUCT


def default_profile_path(platform: str, environ: Mapping[str, str]) -> Path:
    """The profile used when the UI starts without ``--config`` (A2)."""

    return user_directory(platform, environ) / PROFILE_FILE_NAME


def credential_directory(platform: str, environ: Mapping[str, str]) -> Path | None:
    """The per-user directory every profile's credential file lives in (A1).

    On Linux and macOS it is :func:`user_directory`; on Windows it is
    ``%LOCALAPPDATA%\\<product>\\``, and ``None`` when ``LOCALAPPDATA`` is unset
    (the platform then names no per-user directory, and nothing is written).
    """

    if platform == WINDOWS:
        local = environ.get("LOCALAPPDATA")
        return Path(local) / PRODUCT if local else None
    return user_directory(platform, environ)


def credential_file_name(profile_path: str | Path) -> str:
    """``<profile-file-name>.credentials.json``, from the full file name (A1)."""

    return Path(profile_path).name + CREDENTIAL_SUFFIX


def product_version() -> str:
    """The installed distribution's version, or :data:`UNKNOWN_VERSION`."""

    try:
        return metadata.version(PRODUCT)
    except metadata.PackageNotFoundError:
        return UNKNOWN_VERSION
