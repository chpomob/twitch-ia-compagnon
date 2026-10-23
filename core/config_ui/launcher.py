"""The per-user OS launcher that ``--install-launcher`` writes (R1, A3).

One file per platform, so later launches need no terminal: a ``.desktop``
entry in the user's applications directory (Linux), a ``.cmd`` file in the
user's Start Menu programs folder (Windows), a ``.command`` file in
``~/Applications`` (macOS). Its command runs the given interpreter with
``-m core.config_ui`` (and ``--config <path>`` only when one was given); it
carries a marker line and never a token or a secret. An existing file at the
launcher's path is replaced only when it carries that marker line.

The directories are injectable (:class:`LauncherSeams`, the *directories*
argument of :func:`write_launcher`); only :func:`launcher_directories`
derives the defaults, from the environment it is given. The display name and
the file name are the product name of :mod:`core.config_ui.locations`.
"""

from __future__ import annotations

import os
import re
import shlex
import stat
import tempfile
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from core.config_ui import locations

#: The text of the marker line every written launcher carries.
MARKER = f"{locations.PRODUCT} launcher, written by --install-launcher"

#: The launcher's file suffix, per platform.
SUFFIXES = {
    locations.LINUX: ".desktop",
    locations.WINDOWS: ".cmd",
    locations.MACOS: ".command",
}

#: The launcher's mode: owner-writable, never group- or world-writable.
_MODES = {
    locations.LINUX: 0o755,
    locations.WINDOWS: 0o644,
    locations.MACOS: 0o755,
}


#: An argument every launcher format reads as itself, unquoted.
_BARE = re.compile(r"[A-Za-z0-9_.-]+")


class LauncherError(Exception):
    """The launcher was not written. The message names the path and the
    reason only, never a file's content."""


@dataclass(frozen=True)
class LauncherSeams:
    """What ``--install-launcher`` uses; tests replace them, production keeps
    every default. ``platform``: the platform name (``None``: the running
    one; ``sys.platform`` spellings are accepted). ``directories``: the
    target directory per platform (``None``: :func:`launcher_directories`
    of the process environment). ``executable``: the interpreter the
    launcher runs (``None``: ``sys.executable``)."""

    platform: str | None = None
    directories: Mapping[str, Path] | None = None
    executable: str | None = None


def platform_name(platform: str) -> str:
    """*platform* in :mod:`locations`' names; ``sys.platform`` spellings
    (``win32``, ``darwin``) are mapped, anything else is a POSIX desktop."""

    if platform == locations.WINDOWS or platform.startswith("win"):
        return locations.WINDOWS
    if platform in (locations.MACOS, "darwin"):
        return locations.MACOS
    return locations.LINUX


def _home(environ: Mapping[str, str]) -> Path:
    home = environ.get("HOME") or environ.get("USERPROFILE")
    return Path(home) if home else Path.home()


def launcher_directories(environ: Mapping[str, str]) -> dict[str, Path]:
    """Each platform's per-user launcher directory, from *environ* (A3)."""

    home = _home(environ)
    # The XDG base-directory rules ignore an empty or relative value.
    data_home = environ.get("XDG_DATA_HOME", "")
    data = Path(data_home) if data_home and Path(data_home).is_absolute() else home / ".local" / "share"
    appdata = environ.get("APPDATA")
    roaming = Path(appdata) if appdata else home / "AppData" / "Roaming"
    return {
        locations.LINUX: data / "applications",
        locations.WINDOWS: roaming / "Microsoft" / "Windows" / "Start Menu" / "Programs",
        locations.MACOS: home / "Applications",
    }


def launcher_path(platform: str, directories: Mapping[str, Path]) -> Path:
    """Where *platform*'s launcher is written."""

    name = platform_name(platform)
    return Path(directories[name]) / (locations.PRODUCT + SUFFIXES[name])


def _plain(argument: str) -> str:
    if any(ord(character) < 0x20 or character == "\x7f" for character in argument):
        raise LauncherError("a launcher argument holds a control character")
    return argument


def _desktop_argument(argument: str) -> str:
    # Exec quoting: inside double quotes, `"`, `` ` ``, `$` and `\` are
    # backslash-escaped; `%` is a field code, doubled; then the string-value
    # escaping doubles every backslash again.
    if _BARE.fullmatch(argument):
        return argument
    quoted = "".join("\\" + c if c in '"`$\\' else c for c in _plain(argument))
    return ('"' + quoted + '"').replace("%", "%%").replace("\\", "\\\\")


def _cmd_argument(argument: str) -> str:
    # A batch file expands `%` even inside quotes; `"` cannot appear in a
    # Windows path, so it is refused rather than escaped.
    if '"' in _plain(argument):
        raise LauncherError("a launcher argument holds a double quote")
    if _BARE.fullmatch(argument):
        return argument
    return '"' + argument.replace("%", "%%") + '"'


def render_launcher(platform: str, executable: str, config: str | Path | None = None) -> str:
    """The launcher's text for *platform*: the marker line and a command
    running *executable* ``-m core.config_ui`` (``--config`` only when
    *config* is given)."""

    name = platform_name(platform)
    arguments = [str(executable), "-m", "core.config_ui"]
    if config is not None:
        arguments += ["--config", str(config)]
    if name == locations.WINDOWS:
        command = " ".join(_cmd_argument(argument) for argument in arguments)
        # The file is UTF-8; cmd decodes each later line in the active code
        # page, so it is switched to UTF-8 (65001) before the command line.
        lines = ["@echo off", f"REM {MARKER}", "chcp 65001 >nul", command]
        return "\r\n".join(lines) + "\r\n"
    if name == locations.MACOS:
        command = " ".join(shlex.quote(_plain(argument)) for argument in arguments)
        return "\n".join(["#!/bin/sh", f"# {MARKER}", f"exec {command}"]) + "\n"
    command = " ".join(_desktop_argument(argument) for argument in arguments)
    lines = [
        "[Desktop Entry]",
        f"# {MARKER}",
        "Type=Application",
        f"Name={locations.PRODUCT}",
        f"Exec={command}",
        "Terminal=false",
    ]
    return "\n".join(lines) + "\n"


def _marker_line(platform: str) -> str:
    return f"REM {MARKER}" if platform_name(platform) == locations.WINDOWS else f"# {MARKER}"


def _ours(path: Path, platform: str) -> bool:
    """Whether the existing *path* is a regular file carrying the marker line."""

    try:
        if not stat.S_ISREG(path.lstat().st_mode):
            return False
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return _marker_line(platform) in (line.strip() for line in text.splitlines())


def write_launcher(
    platform: str,
    *,
    directories: Mapping[str, Path],
    executable: str,
    config: str | Path | None = None,
) -> Path:
    """Write *platform*'s launcher under *directories* and return its path.

    The file is published atomically with its final mode. An existing file
    at that path that does not carry the marker line is left untouched and
    :class:`LauncherError` is raised.
    """

    name = platform_name(platform)
    path = launcher_path(name, directories)
    content = render_launcher(name, executable, config).encode("utf-8")
    if os.path.lexists(path) and not _ours(path, name):
        raise LauncherError(
            f"{path} exists and was not written by --install-launcher; it is left unchanged"
        )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".", suffix=".tmp")
    except OSError as exc:
        raise LauncherError(f"{path.parent} cannot hold the launcher: {exc.strerror}") from None
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
        os.chmod(temporary, _MODES[name])
        os.replace(temporary, path)
    except OSError as exc:
        with suppress(OSError):
            os.unlink(temporary)
        raise LauncherError(f"{path} cannot be written: {exc.strerror}") from None
    return path

