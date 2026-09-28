"""Managed overlay file shared by the runtime and the configuration UI (R2, R7).

The operator's base configuration file is never rewritten: edits go to an
overlay file merged over it. This module is the one implementation of that
rule, used by ``core.main`` and by the configuration UI alike, so both always
agree on the overlay path (A1), the merge precedence (R2), the digest of the
on-disk configuration and the status-path collision check (A5, R7).

It names no module and imports only the standard library and ``yaml``; in
particular it never imports ``core.main``, which maps :class:`OverlayError` to
its own ``ConfigurationError``. Every error message is value-free: it names the
file and the problem, never the file content or a YAML parser excerpt.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

#: Environment variable carrying the status record path (D4, A5).
STATUS_FILE_VARIABLE = "TWITCH_IA_COMPAGNON_STATUS_FILE"

_EXAMPLE_SUFFIX = ".example"
_YAML_SUFFIXES = (".yaml", ".yml")
_OVERLAY_SUFFIX = ".local.yaml"
_STATUS_SUFFIX = ".status.json"


class OverlayError(RuntimeError):
    """A base or overlay file that cannot be used; the message is value-free."""


def implicit_overlay_path(base: str | os.PathLike[str]) -> Path | None:
    """Return the overlay path derived from ``base`` (A1), or ``None``.

    A trailing ``.example`` is removed first, then a final ``.yaml``/``.yml``
    becomes ``.local.yaml`` in the same directory. Any other name has no
    implicit overlay.
    """

    path = Path(base)
    name = path.name
    if name.endswith(_EXAMPLE_SUFFIX):
        name = name[: -len(_EXAMPLE_SUFFIX)]
    for suffix in _YAML_SUFFIXES:
        stem = name[: -len(suffix)]
        if name.endswith(suffix) and stem:
            return path.with_name(stem + _OVERLAY_SUFFIX)
    return None


def resolve_overlay_path(
    base: str | os.PathLike[str],
    explicit: str | os.PathLike[str] | None,
) -> Path | None:
    """Return the explicit ``--overlay`` path when given, else the implicit one."""

    if explicit is not None:
        return Path(explicit)
    return implicit_overlay_path(base)


def read_overlay(path: str | os.PathLike[str] | None) -> Mapping[str, Any]:
    """Read one overlay file; an absent, empty or ``null`` file gives ``{}``."""

    if path is None:
        return {}
    label = f"overlay file {os.fspath(path)}"
    try:
        with open(path, "r", encoding="utf-8") as stream:
            loaded = yaml.safe_load(stream)
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeDecodeError):
        raise OverlayError(f"{label}: is not readable") from None
    except yaml.YAMLError:
        raise OverlayError(f"{label}: is not valid YAML") from None
    if loaded is None:
        return {}
    if not isinstance(loaded, Mapping):
        raise OverlayError(f"{label}: must be a mapping")
    return loaded


def read_base(path: str | os.PathLike[str]) -> Mapping[str, Any]:
    """Parse the base configuration file exactly as ``load_config`` does."""

    try:
        resolved = Path(path).expanduser().resolve()
    except (OSError, RuntimeError, ValueError, TypeError):
        raise OverlayError("configuration file: path is not valid") from None
    try:
        with resolved.open("r", encoding="utf-8") as stream:
            loaded = yaml.safe_load(stream)
    except OSError:
        raise OverlayError("configuration file: is not readable") from None
    except yaml.YAMLError:
        raise OverlayError("configuration file: is not valid YAML") from None
    if not isinstance(loaded, Mapping):
        raise OverlayError("configuration: must be a mapping")
    return loaded


def deep_merge(base: Mapping[str, Any], overlay: Mapping[str, Any]) -> dict[str, Any]:
    """Merge ``overlay`` over ``base`` with the R2 precedence.

    A mapping in the overlay merges key by key into a base mapping,
    recursively; any other overlay value (scalar, list, null) replaces the base
    value wholesale. Keys only in the base are kept. Neither input is mutated:
    the result is a fresh deep copy.
    """

    merged: dict[Any, Any] = {key: copy.deepcopy(value) for key, value in base.items()}
    for key, value in overlay.items():
        current = merged.get(key)
        if isinstance(value, Mapping) and isinstance(current, Mapping):
            merged[key] = deep_merge(current, value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _stringify_keys(value: Any) -> Any:
    """Convert every mapping key to ``str`` recursively, for ``sort_keys``."""

    if isinstance(value, Mapping):
        return {str(key): _stringify_keys(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_stringify_keys(item) for item in value]
    return value


def canonical_digest(document: Any) -> str:
    """Return the lowercase hex SHA-256 of the canonical JSON of ``document``."""

    text = json.dumps(
        _stringify_keys(document),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def on_disk_digest(
    base: str | os.PathLike[str],
    overlay_path: str | os.PathLike[str] | None,
) -> str:
    """Digest of the configuration on disk: the base merged with its overlay."""

    return canonical_digest(deep_merge(read_base(base), read_overlay(overlay_path)))


def _resolved(path: str | os.PathLike[str]) -> str:
    return os.path.realpath(os.path.abspath(os.fspath(path)))


def same_file(a: str | os.PathLike[str], b: str | os.PathLike[str]) -> bool:
    """Whether both paths name the same file once resolved (links followed)."""

    return _resolved(a) == _resolved(b)


def status_path_collision(
    status: str | os.PathLike[str],
    base: str | os.PathLike[str],
    overlay: str | os.PathLike[str] | None,
) -> str | None:
    """Name the configuration file the status path resolves to, if any (R7)."""

    if same_file(status, base):
        return "base"
    if overlay is not None and same_file(status, overlay):
        return "overlay"
    return None


def default_status_path(base: str | os.PathLike[str]) -> Path:
    """The base file's full name with ``.status.json`` appended (A5)."""

    path = Path(base)
    return path.with_name(path.name + _STATUS_SUFFIX)
