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
import re
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
#: Marks, on the walk's stack, the end of one link's target: the link is resolved.
_LINK_DONE = object()


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


def canonical_overlay_path(
    base: str | os.PathLike[str],
    explicit: str | os.PathLike[str] | None,
) -> Path | None:
    """The managed overlay path (A1) in its :func:`canonical_path` form.

    The implicit path is derived from the base file's name as given, then
    canonicalised like an explicit one; ``None`` when no path can be derived.
    """

    overlay = resolve_overlay_path(base, explicit)
    return None if overlay is None else canonical_path(overlay)


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


#: An environment reference ``os.path.expandvars`` left in place: its
#: variable is unset, so the path it names is unknown.
_UNEXPANDED_VARIABLE = re.compile(r"\$(?:\{[^}]*\}|[A-Za-z_][A-Za-z0-9_]*)")


def canonical_path(path: str | os.PathLike[str]) -> Path:
    """The one canonical form of a configuration, overlay or status path.

    ``~`` and environment references are expanded, the result is made
    absolute against the working directory, and symbolic links and ``..``
    are resolved whether or not the file exists yet. Every path the runtime
    and the UI compare, read or replace goes through this function, so two
    spellings of one file can never be told apart (A5, R6, R7).

    A path that cannot be canonicalised raises :class:`OverlayError` rather
    than being guessed at: a ``~user`` that names no user, a reference to an
    unset variable, a link loop or an invalid path. The message names the
    path as given and no file content.
    """

    raw = os.fspath(path)
    label = f"path {raw}"
    try:
        expanded = os.path.expandvars(os.path.expanduser(raw))
    except (TypeError, ValueError, KeyError, RuntimeError):
        raise OverlayError(f"{label}: cannot be canonicalised") from None
    if expanded.startswith("~"):
        raise OverlayError(f"{label}: cannot be canonicalised (unknown home directory)")
    if _UNEXPANDED_VARIABLE.search(expanded):
        raise OverlayError(f"{label}: cannot be canonicalised (unset environment variable)")
    try:
        resolved = _resolve_links(expanded)
    except (OSError, ValueError, RuntimeError):
        raise OverlayError(f"{label}: cannot be canonicalised") from None
    if resolved is None:
        raise OverlayError(f"{label}: cannot be canonicalised (link loop)")
    return resolved


def _resolve_links(expanded: str) -> Path | None:
    """*expanded* made absolute with every link followed, or ``None`` on a loop.

    The walk is done here rather than by ``Path.resolve`` or
    ``os.path.realpath``, whose handling of a link loop differs between the
    supported Python versions (3.13 stopped raising). Each component is
    looked up in turn: a link is replaced by its target, and a ``..`` is
    applied to the path resolved so far, as the filesystem does
    (``os.path.abspath`` would collapse ``link/..`` lexically and name
    another file). A component that does not exist, or is not a link, is kept
    as written.

    A loop is an actual cycle, not a long chain: each link is remembered while
    its target is being walked and, once walked, with what it resolved to. A
    link met again while still being walked never settles and the result is
    ``None``; one met again after it settled is reused. A finite chain of any
    length therefore resolves (P20F1 review A1).
    """

    absolute = Path(expanded if os.path.isabs(expanded) else os.path.join(os.getcwd(), expanded))
    resolved = absolute.anchor
    pending: list[object] = list(reversed(absolute.parts[1:]))
    # link path -> what it resolved to, or None while its target is walked.
    seen: dict[str, str | None] = {}
    while pending:
        name = pending.pop()
        if name is _LINK_DONE:
            seen[str(pending.pop())] = resolved
            continue
        assert isinstance(name, str)
        if name in ("", "."):
            continue
        if name == "..":
            resolved = os.path.dirname(resolved)
            continue
        candidate = os.path.join(resolved, name)
        if candidate in seen:
            settled = seen[candidate]
            if settled is None:
                return None
            resolved = settled
            continue
        try:
            target = Path(os.readlink(candidate))
        except OSError:
            resolved = candidate
            continue
        seen[candidate] = None
        pending.append(candidate)
        pending.append(_LINK_DONE)
        if target.anchor:
            resolved = target.anchor
            pending.extend(reversed(target.parts[1:]))
        else:
            pending.extend(reversed(target.parts))
    return Path(resolved)


def _existing_anchor(path: Path) -> tuple[Path, tuple[str, ...]]:
    """The deepest existing ancestor of canonical *path* and the names below it.

    *path* itself when it exists; ``(path, ())`` then. The walk stops at the
    filesystem root, which always exists.
    """

    missing: list[str] = []
    current = path
    while not os.path.lexists(current) and current.parent != current:
        missing.append(current.name)
        current = current.parent
    return current, tuple(reversed(missing))


def _case_insensitive(directory: Path) -> bool:
    """Whether names in *directory* are looked up ignoring case.

    Probed on the directory's own entry: the case-swapped spelling of its name
    is the same directory only on a case-insensitive filesystem. A name with
    no cased letter cannot be probed and is taken as case-sensitive.
    """

    swapped = directory.name.swapcase()
    if swapped == directory.name:
        return False
    try:
        return os.path.samefile(directory, directory.parent / swapped)
    except OSError:
        return False


def same_file(a: str | os.PathLike[str], b: str | os.PathLike[str]) -> bool:
    """Whether both paths name the same file: file identity, not path text.

    Both sides go through :func:`canonical_path` first, so ``~``, environment
    references, relative spellings, ``..`` and symbolic links name the file
    they resolve to. When both files exist the decision is their identity
    (device and inode, ``os.path.samefile``): a hard link, a bind-mount alias
    or a differing case on a case-insensitive filesystem is the same file
    whatever its path text (gate-2 F1).

    Where no identity can be compared because a path does not exist yet, the
    comparison falls back to canonicalised paths: each side is split into its
    deepest existing ancestor and the names below it, the ancestors are
    compared by identity and the remaining names as text (ignoring case where
    that ancestor's filesystem does). A file not created yet is thus the same
    file as the one another spelling will create, and never the same as an
    existing file, which it cannot be.
    """

    first, second = canonical_path(a), canonical_path(b)
    if first == second:
        return True
    try:
        return os.path.samefile(first, second)
    except OSError:
        # At least one side does not exist yet (or cannot be examined): fall
        # back to the canonicalised-path comparison described above.
        pass
    first_anchor, first_names = _existing_anchor(first)
    second_anchor, second_names = _existing_anchor(second)
    if not first_names or not second_names or len(first_names) != len(second_names):
        return False
    try:
        if not os.path.samefile(first_anchor, second_anchor):
            return False
    except OSError:
        return False
    if first_names == second_names:
        return True
    if _case_insensitive(first_anchor):
        return tuple(name.casefold() for name in first_names) == tuple(
            name.casefold() for name in second_names
        )
    return False


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
