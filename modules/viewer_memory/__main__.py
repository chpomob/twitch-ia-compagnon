"""``python -m modules.viewer_memory`` — the offline memory command (phase 3 R3).

Three subcommands, each taking the runtime's YAML configuration file:

- ``forget --config <file> --platform <p> --channel <c> --viewer <v>``
  deletes the one memory file of that key (its hashed name,
  :func:`~modules.viewer_memory.memory_file_name`) and prints
  ``removed=<n>`` (0 when there was none);
- ``forget-all --config <file>`` deletes every file named like a memory file
  (:data:`~modules.viewer_memory.MEMORY_FILE_PATTERN`) and keeps the rest,
  printing ``removed=<n>``;
- ``stats --config <file>`` prints ``files=<n> bytes=<b>`` over the
  memory-named files.

**Reading.** The file is read with PyYAML and only the subtree
``modules.viewer_memory`` is looked at — the settings path the runtime hands
the module — then checked by the module's own
:func:`~modules.viewer_memory.validate_settings`. No ``${…}`` reference is
resolved anywhere: one in ``directory`` is refused (the command cannot know
the value), and those elsewhere in the profile, secrets included, are never
read.

**Exit codes.** 0 on success; 2 on an unreadable or invalid configuration
(and on a usage error, as ``argparse`` does); 1 when a file operation fails.
Messages name the field, never a configured value.

**Isolation.** The command starts no runtime, opens no socket and imports no
network client. It works on the directory alone: a running runtime's
in-memory index does not see its deletions until the next prepare.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Mapping

import yaml

from modules.viewer_memory import (
    MEMORY_FILE_PATTERN,
    MODULE_NAME,
    memory_file_name,
    validate_settings,
)


EXIT_OK = 0
EXIT_FAILED = 1
EXIT_INVALID_CONFIGURATION = 2

_REFERENCE_MARKER = "${"


class _ConfigurationError(Exception):
    """An unreadable or invalid configuration; the message is value-free."""


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        directory = _memory_directory(Path(arguments.config))
    except _ConfigurationError as error:
        print(f"{MODULE_NAME}: {error}", file=sys.stderr)
        return EXIT_INVALID_CONFIGURATION
    try:
        if arguments.command == "forget":
            name = memory_file_name(arguments.platform, arguments.channel, arguments.viewer)
            print(f"removed={_forget(directory, name)}")
        elif arguments.command == "forget-all":
            print(f"removed={sum(_forget(directory, name) for name in _memory_names(directory))}")
        else:
            names = _memory_names(directory)
            size = sum((directory / name).stat().st_size for name in names)
            print(f"files={len(names)} bytes={size}")
    except OSError as error:
        print(f"{MODULE_NAME}: {arguments.command} failed: {type(error).__name__}", file=sys.stderr)
        return EXIT_FAILED
    return EXIT_OK


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=f"python -m modules.{MODULE_NAME}",
        description="Inspect or erase the viewer memory files offline.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    forget = commands.add_parser("forget", help="delete one viewer's memory file")
    forget_all = commands.add_parser("forget-all", help="delete every memory file")
    stats = commands.add_parser("stats", help="print the memory file count and bytes")
    for command in (forget, forget_all, stats):
        command.add_argument("--config", required=True, help="the YAML configuration file")
    forget.add_argument("--platform", required=True, type=_non_empty)
    forget.add_argument("--channel", required=True, type=_non_empty)
    forget.add_argument("--viewer", required=True, type=_non_empty)
    return parser


def _non_empty(value: str) -> str:
    if not value:
        raise argparse.ArgumentTypeError("must be non-empty")
    return value


def _memory_directory(path: Path) -> Path:
    """The validated ``directory`` of ``modules.viewer_memory`` in *path*."""

    try:
        config = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as error:
        raise _ConfigurationError(
            f"configuration is unreadable: {type(error).__name__}"
        ) from None
    except yaml.YAMLError:
        raise _ConfigurationError("configuration is not valid YAML") from None
    modules = config.get("modules") if isinstance(config, Mapping) else None
    if not isinstance(modules, Mapping):
        raise _ConfigurationError("field 'modules' must be a mapping")
    settings: Any = modules.get(MODULE_NAME)
    if settings is None:
        raise _ConfigurationError(f"field 'modules.{MODULE_NAME}' is missing")
    diagnostics = validate_settings(settings)
    if diagnostics:
        raise _ConfigurationError("; ".join(diagnostics))
    directory = settings["directory"]
    if _REFERENCE_MARKER in directory:
        raise _ConfigurationError(
            f"field 'modules.{MODULE_NAME}.directory' holds a reference this "
            "command does not resolve"
        )
    return Path(directory)


def _memory_names(directory: Path) -> list[str]:
    """The names of the memory-named regular files; none when *directory* is absent."""

    if not directory.is_dir():
        return []
    with os.scandir(directory) as entries:
        return sorted(
            entry.name
            for entry in entries
            if MEMORY_FILE_PATTERN.fullmatch(entry.name)
            and entry.is_file(follow_symlinks=False)
        )


def _forget(directory: Path, name: str) -> int:
    try:
        (directory / name).unlink()
    except FileNotFoundError:
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
