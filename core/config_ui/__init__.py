"""Local configuration UI, run as its own process (R1, D2, D3).

``python -m core.config_ui --config PATH`` serves a local-only web page that
edits the managed overlay file merged over the untouched base configuration
(R2, R6). It is never a remote administration channel: it binds a loopback
address unless ``--allow-non-loopback`` is given, every request must carry an
accepted ``Host`` (A8) and a session derived from the access token printed
once at startup, and every state-changing request must be a POST with an
accepted ``Origin`` (when one is sent) and the session's CSRF token.

The request handling is a pure, synchronous core (:meth:`ConfigUI.handle`
over the plain :class:`UIRequest`/:class:`UIResponse` dataclasses) so the
whole policy is testable without opening any socket (D2). Only :func:`serve`
touches aiohttp, and it never calls ``handle`` on its serving loop: the
request bridge (:func:`_to_ui_request`, :func:`_dispatch`) runs it on the
dedicated ``config-ui`` thread pool (D8).

The module is split into delimited sections (D3): settings and startup
checks, authorities, the configuration model, the status record reader
(R7), the redaction guard (R8), the rendering helpers (R9), the request core
and its pages, the draft edits and Check (R5), Save and remove-override (R6),
the supervised restart of Apply (R7, D9), the socket-free event loop (D7), the request bridge and the entry point. No
section names a module: every page lists or renders what discovery found
(A2).
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import hmac
import html
import ipaddress
import json
import logging
import math
import os
import re
import secrets
import selectors
import stat
import subprocess
import sys
import tempfile
import threading
import time
import weakref
from contextlib import suppress
from contextvars import ContextVar
from collections import OrderedDict
from datetime import datetime, timezone
from collections.abc import Awaitable, Callable, Coroutine, Iterable, Mapping, Sequence
from concurrent.futures import Executor, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, TypeVar
from urllib.parse import parse_qsl

import yaml

from core.actions import ANY_ACTION
from core.bus import EventBus
from core.contracts import ContractError, validate_against_schema, validate_schema
from core.loader import (
    MANIFEST_SETTINGS_SCHEMA_KEY,
    MANIFEST_TRIGGERS_KEY,
    DiscoveredModule,
    ModuleLoader,
    ModuleLoadError,
    _trigger_policy,
    _validate_manifest,
)
from core.main import (
    _ENV_REFERENCE,
    ACTIONS_KEY,
    LIMIT_DECLARATION,
    LIMIT_KIND_COUNT,
    LIMITS_KEY,
    MODULES_DIRECTORY_BUILTIN,
    MODULES_KEY,
    SECRETS_KEY,
    _builtin_modules_directory,
    check_config,
)
from core.overlay import (
    STATUS_FILE_VARIABLE,
    OverlayError,
    canonical_overlay_path,
    canonical_path,
    deep_merge,
    default_status_path,
    on_disk_digest,
    read_base,
    read_overlay,
    same_file,
    status_path_collision,
)

__all__ = [
    "ApplyReport",
    "CheckResult",
    "ConfigUI",
    "ConfigView",
    "RunningState",
    "StatusReading",
    "StatusRecord",
    "Supervisor",
    "UIRequest",
    "UIResponse",
    "UISettings",
    "WriteResult",
    "accepted_authorities",
    "default_launch_argv",
    "main",
    "process_alive",
    "read_status",
    "serve",
    "startup_checks",
]

#: The UI's logger. It never receives the access token, a session id or a
#: CSRF token (AC2).
logger = logging.getLogger("core.config_ui")

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765

_PROG = "python -m core.config_ui"


# ---------------------------------------------------------------------------
# Settings and startup checks (R1, R6, A1, A5)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UISettings:
    """The UI's command line, parsed once at startup."""

    config: Path
    overlay: Path | None = None
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    allow_non_loopback: bool = False
    allowed_hosts: tuple[str, ...] = ()
    status_file: Path | None = None
    launch_argv: tuple[str, ...] = ()

    @classmethod
    def from_argv(cls, argv: Sequence[str]) -> UISettings:
        """Parse *argv*; everything after the first ``--`` is the launch argv.

        The split happens before argparse, which would otherwise treat ``--``
        itself as an option terminator and swallow the launch argv.
        """

        arguments = list(argv)
        if "--" in arguments:
            split = arguments.index("--")
            head, launch = arguments[:split], arguments[split + 1 :]
        else:
            head, launch = arguments, []
        parsed = _parser().parse_args(head)
        return cls(
            config=Path(parsed.config),
            overlay=None if parsed.overlay is None else Path(parsed.overlay),
            host=parsed.host,
            port=parsed.port,
            allow_non_loopback=parsed.allow_non_loopback,
            allowed_hosts=tuple(parsed.allowed_hosts),
            status_file=None if parsed.status_file is None else Path(parsed.status_file),
            launch_argv=tuple(launch),
        )

    # Every path below is in its one canonical form (``core.overlay.
    # canonical_path``): the startup refusal, the Save destination, the
    # status-path rules and the launched runtime all use these values, so no
    # spelling of the base file can pass for the overlay (R6, R7). Whether
    # two of them are one file is always decided by ``core.overlay.
    # same_file``, on file identity, so no hard link or other alias can
    # either (gate-2 F1). Each
    # raises ``OverlayError`` for a path that cannot be canonicalised;
    # :func:`startup_checks` turns that into a refusal before anything else.

    @property
    def base_path(self) -> Path:
        """The base configuration file, canonical, as the runtime opens it."""

        return canonical_path(self.config)

    @property
    def overlay_path(self) -> Path | None:
        """The managed overlay path, canonical: ``--overlay`` or the A1 path."""

        return canonical_overlay_path(self.config, self.overlay)

    @property
    def status_path(self) -> Path:
        """The one status path watched, canonical: ``--status-file`` or A5."""

        if self.status_file is not None:
            return canonical_path(self.status_file)
        return canonical_path(default_status_path(self.config))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=_PROG,
        description="Local configuration UI (arguments after -- launch the main process).",
    )
    parser.add_argument("--config", required=True, help="base configuration file")
    parser.add_argument("--overlay", help="managed overlay file (default: derived from --config)")
    parser.add_argument("--host", default=DEFAULT_HOST, help="bind host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="bind port (default: 8765)")
    parser.add_argument(
        "--allow-non-loopback",
        action="store_true",
        help="allow binding a non-loopback host",
    )
    parser.add_argument(
        "--allowed-host",
        dest="allowed_hosts",
        action="append",
        default=[],
        metavar="NAME",
        help="extra accepted host name for a wildcard bind (repeatable)",
    )
    parser.add_argument("--status-file", help="status record path (default: <config>.status.json)")
    return parser


def startup_checks(settings: UISettings) -> list[str]:
    """Every reason the UI must refuse to start, checked before any bind.

    Pure: it reads no file and resolves no host name. An empty list means the
    UI may start.
    """

    problems: list[str] = []
    if not 0 < settings.port < 65536:
        problems.append(f"port {settings.port} is not a valid TCP port")
    if not settings.allow_non_loopback and not _is_loopback(settings.host):
        problems.append(
            f"refusing to bind non-loopback host {settings.host}: "
            "the configuration UI is local-only; pass --allow-non-loopback to override"
        )
    try:
        base = settings.base_path
        overlay = settings.overlay_path
        status = settings.status_path
    except OverlayError as exc:
        # Refuse rather than guess which file a path names (R6, R7).
        problems.append(f"path_not_canonical: {exc}")
        return problems
    if overlay is None:
        problems.append(
            f"no overlay path can be derived from configuration file {base}: "
            "--overlay PATH is required"
        )
    elif same_file(overlay, base):
        # File identity, not path text: a hard link or any other alias of
        # the base is the base (gate-2 F1).
        given = settings.config if settings.overlay is None else settings.overlay
        problems.append(
            f"overlay_path_collision: overlay path {given} resolves to the "
            f"configuration file {base} itself"
        )
    collision = status_path_collision(status, base, overlay)
    if collision is not None:
        named = base if collision == "base" else overlay
        problems.append(
            f"status_path_collision: status file {status} collides with the "
            f"{collision} file {named}"
        )
    return problems


# ---------------------------------------------------------------------------
# Accepted client authorities (A8)
# ---------------------------------------------------------------------------

_LOOPBACK_NAMES = ("localhost", "127.0.0.1", "[::1]")


def _ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    text = host[1:-1] if host.startswith("[") and host.endswith("]") else host
    try:
        return ipaddress.ip_address(text)
    except ValueError:
        return None


def _is_loopback(host: str) -> bool:
    """Loopback by name or literal only; never a DNS lookup."""

    if host.lower() == "localhost":
        return True
    address = _ip(host)
    return address is not None and address.is_loopback


def _is_wildcard(host: str) -> bool:
    address = _ip(host)
    return address is not None and address.is_unspecified


def _host_form(host: str) -> str:
    """Lower-cased host name, IPv6 literal canonical and in brackets."""

    address = _ip(host)
    if isinstance(address, ipaddress.IPv6Address):
        return f"[{address.compressed}]"
    if address is not None:
        return str(address)
    return host.lower()


def accepted_authorities(
    host: str, port: int, allowed_hosts: Sequence[str] = ()
) -> frozenset[str]:
    """The ``host:port`` authorities a request may name (A8).

    The stored form is lower-cased, IPv6 literals in brackets, the port always
    explicit. ``--allowed-host`` names count only for a wildcard bind.
    """

    names = {_host_form(host)}
    wildcard = _is_wildcard(host)
    if wildcard or _is_loopback(host):
        names.update(_LOOPBACK_NAMES)
    if wildcard:
        names.update(_host_form(name) for name in allowed_hosts)
    return frozenset(f"{name}:{port}" for name in names)


def access_authority(host: str, port: int) -> str:
    """The authority of the printed access URL; ``127.0.0.1`` for a wildcard."""

    name = "127.0.0.1" if _is_wildcard(host) else _host_form(host)
    return f"{name}:{port}"


def _normalize_authority(text: str) -> str | None:
    """Parse a ``Host`` value into the stored authority form, or ``None``.

    A missing port means 80. Anything that is not exactly ``host[:port]``
    (a user-info part, a path, whitespace, a bad port) is rejected.
    """

    if not text or any(char in text for char in "@/?#\\ \t,") or not text.isascii():
        return None
    if text.startswith("["):
        end = text.find("]")
        if end < 0:
            return None
        name, rest = text[: end + 1], text[end + 1 :]
        if not isinstance(_ip(name), ipaddress.IPv6Address):
            return None
    else:
        name, colon, port_text = text.partition(":")
        rest = colon + port_text
        if not name or ":" in port_text:
            return None
    if rest:
        if not rest.startswith(":"):
            return None
        port_text = rest[1:]
        if not port_text.isdigit() or not 0 < int(port_text) < 65536:
            return None
        port = int(port_text)
    else:
        port = 80
    return f"{_host_form(name)}:{port}"


def _origin_authority(origin: str) -> str | None:
    """The authority of an ``http://<authority>`` origin with no path, or ``None``."""

    scheme = "http://"
    if origin[: len(scheme)].lower() != scheme:
        return None
    return _normalize_authority(origin[len(scheme) :])


# ---------------------------------------------------------------------------
# Configuration model (R2, R4, R8, A3, A9, D6)
#
# One immutable snapshot of the configuration as the files on disk describe
# it: the base and the overlay raw, their merge, the modules discovered from
# the merged ``modules_directory`` through the loader's own discovery (D6),
# every ``${NAME}`` the two files reference, and the secret set the
# redaction guard hides. Nothing here resolves a reference into a page.
# ---------------------------------------------------------------------------

ORIGIN_OVERLAY = "overlay"
ORIGIN_BASE = "base"
ORIGIN_DEFAULT = "default-not-set"
ORIGIN_REFERENCE = "environment reference"

HIDDEN_LITERAL = "literal value configured (hidden)"
READ_ONLY_REASON = (
    "read-only in v1: editing could widen the authorized actions / expose secrets"
)
LOCAL_ONLY_STATEMENT = (
    "This configuration UI is local-only: it edits the configuration files of "
    "this machine and is never a remote administration channel."
)

_MISSING: Any = object()


def _lookup(document: Any, path: Sequence[Any]) -> Any:
    """The value at *path* inside *document*, or :data:`_MISSING`."""

    node = document
    for segment in path:
        if isinstance(node, Mapping):
            if segment not in node:
                return _MISSING
            node = node[segment]
        elif isinstance(node, list) and isinstance(segment, int):
            if not 0 <= segment < len(node):
                return _MISSING
            node = node[segment]
        else:
            return _MISSING
    return node


def reference_name(value: Any) -> str | None:
    """The variable a whole-string ``${NAME}`` reference names, else ``None``."""

    if isinstance(value, str):
        match = _ENV_REFERENCE.fullmatch(value)
        if match is not None:
            return match.group(1)
    return None


def _referenced(document: Any) -> list[str]:
    """Every ``${NAME}`` in *document*, as a value or as a mapping key.

    A pure walk, safe on aliased or recursive YAML; order of first sight.
    """

    found: dict[str, None] = {}
    pending: list[Any] = [document]
    seen: set[int] = set()
    while pending:
        item = pending.pop()
        name = reference_name(item)
        if name is not None:
            found.setdefault(name)
            continue
        if isinstance(item, (Mapping, list)):
            if id(item) in seen:
                continue
            seen.add(id(item))
            if isinstance(item, Mapping):
                for key, value in reversed(list(item.items())):
                    pending.append(value)
                    pending.append(key)
            else:
                pending.extend(reversed(item))
    return list(found)


def _discover_valid(directory: Path) -> dict[str, DiscoveredModule]:
    """The modules of *directory* whose manifest loads, skipping the others.

    The fallback when :meth:`ModuleLoader._discover` refuses the directory as
    a whole; it applies the same candidate rules, one manifest at a time.
    """

    discovered: dict[str, DiscoveredModule] = {}
    try:
        children = sorted(directory.iterdir(), key=lambda path: path.name)
    except OSError:
        return discovered
    for child in children:
        manifest_path = child / "module.yaml"
        try:
            if child.is_symlink() or not child.is_dir() or not manifest_path.is_file():
                continue
            resolved = child.resolve(strict=True)
            if resolved.parent != directory:
                continue
            with manifest_path.open("r", encoding="utf-8") as stream:
                manifest = yaml.safe_load(stream)
            declaration = _validate_manifest(child.name, manifest)
        except (OSError, yaml.YAMLError, ModuleLoadError):
            continue
        name = declaration.manifest["name"]
        if name in discovered:
            continue
        discovered[name] = DiscoveredModule(
            name=name, directory=resolved, manifest=declaration.manifest, declaration=declaration
        )
    return discovered


def _literals(document: Any) -> list[str]:
    """Every non-reference scalar in *document*, as text (booleans excluded)."""

    found: list[str] = []
    pending: list[Any] = [document]
    seen: set[int] = set()
    while pending:
        item = pending.pop()
        if isinstance(item, (Mapping, list)):
            if id(item) in seen:
                continue
            seen.add(id(item))
            pending.extend(item.values() if isinstance(item, Mapping) else item)
        elif isinstance(item, (str, int, float)) and not isinstance(item, bool):
            if reference_name(item) is None:
                found.append(str(item))
    return found


def _file_state(path: Path | None) -> tuple[bool, str | None, int | None]:
    """Whether *path* exists, the SHA-256 of its bytes and its mtime (ns)."""

    if path is None:
        return False, None, None
    try:
        data = path.read_bytes()
        mtime = path.stat().st_mtime_ns
    except FileNotFoundError:
        return False, None, None
    except OSError:
        return True, None, None
    return True, hashlib.sha256(data).hexdigest(), mtime


@dataclass(frozen=True)
class ConfigView:
    """A snapshot of the configuration the UI renders (R4, R8).

    ``overlay_digest`` and ``overlay_mtime_ns`` describe the overlay bytes the
    snapshot was read from, so a save can detect a file changed underneath it.
    ``diagnostics`` are value-free: a file that cannot be read or a manifest
    that cannot be discovered is reported, never quoted, and the pages still
    render.
    """

    base_path: Path
    overlay_path: Path | None
    overlay_exists: bool
    overlay_digest: str | None
    overlay_mtime_ns: int | None
    base: Mapping[str, Any]
    overlay: Mapping[str, Any]
    merged: Mapping[str, Any]
    modules_directory: Path | None
    modules: Mapping[str, DiscoveredModule]
    references: Mapping[str, bool]
    secret_values: frozenset[str]
    diagnostics: tuple[str, ...] = ()

    @classmethod
    def load(cls, settings: UISettings, environ: Mapping[str, str]) -> ConfigView:
        """Read both files, merge them, discover modules and collect secrets."""

        diagnostics: list[str] = []
        base_path = settings.base_path
        overlay_path = settings.overlay_path
        try:
            base: Mapping[str, Any] = read_base(base_path)
        except OverlayError as exc:
            diagnostics.append(str(exc))
            base = {}
        exists, digest, mtime = _file_state(overlay_path)
        try:
            overlay = read_overlay(overlay_path)
        except OverlayError as exc:
            diagnostics.append(str(exc))
            overlay = {}
        merged = deep_merge(base, overlay)

        modules_directory = cls._modules_directory(base_path, merged, environ, diagnostics)
        modules: dict[str, DiscoveredModule] = {}
        complete = True
        if modules_directory is not None:
            try:
                modules = dict(ModuleLoader(EventBus(), modules_directory)._discover())
            except ModuleLoadError as exc:
                diagnostics.extend(exc.diagnostics)
                # One bad manifest must not hide the credential paths of the
                # valid ones: the secret set is built from what still loads.
                modules = _discover_valid(modules_directory)
                complete = False

        names = _referenced(base)
        names.extend(name for name in _referenced(overlay) if name not in names)
        references = {name: name in environ for name in names}

        secret_values: set[str] = set()
        for name in names:
            if name in environ:
                secret_values.add(environ[name])
        for document in (base, overlay):
            listed = _lookup(document, (SECRETS_KEY,))
            for entry in listed if isinstance(listed, list) else ():
                name = reference_name(entry)
                if name is not None and name in environ:
                    secret_values.add(environ[name])
        for name, module in modules.items():
            declaration = module.declaration
            for credential in declaration.credentials if declaration is not None else ():
                for document in (base, overlay):
                    literal = _lookup(document, ("modules", name, *credential))
                    if literal is _MISSING or literal is None or isinstance(literal, bool):
                        continue
                    if isinstance(literal, (str, int, float)) and reference_name(literal) is None:
                        secret_values.add(str(literal))
        if not complete:
            # A module whose manifest did not load has unknown credential
            # paths: every literal under its settings is treated as one.
            for document in (base, overlay):
                settings_by_module = document.get("modules")
                if not isinstance(settings_by_module, Mapping):
                    continue
                for name, module_settings in settings_by_module.items():
                    if name not in modules:
                        secret_values.update(_literals(module_settings))

        return cls(
            base_path=base_path,
            overlay_path=overlay_path,
            overlay_exists=exists,
            overlay_digest=digest,
            overlay_mtime_ns=mtime,
            base=base,
            overlay=overlay,
            merged=merged,
            modules_directory=modules_directory,
            modules=modules,
            references=references,
            secret_values=frozenset(value for value in secret_values if value),
            diagnostics=tuple(diagnostics),
        )

    @staticmethod
    def _modules_directory(
        base_path: Path,
        merged: Mapping[str, Any],
        environ: Mapping[str, str],
        diagnostics: list[str],
    ) -> Path | None:
        """The merged ``modules_directory``, resolved as the runtime resolves it."""

        raw = merged.get("modules_directory")
        name = reference_name(raw)
        if name is not None:
            raw = environ.get(name)
        if raw == MODULES_DIRECTORY_BUILTIN:
            try:
                return _builtin_modules_directory()
            except Exception:
                diagnostics.append("modules_directory: the shipped modules package is not available")
                return None
        if not isinstance(raw, str) or not raw.strip():
            diagnostics.append("modules_directory: is not configured")
            return None
        try:
            directory = Path(raw).expanduser()
            if not directory.is_absolute():
                directory = base_path.expanduser().resolve().parent / directory
            return directory.resolve()
        except (OSError, RuntimeError, ValueError):
            diagnostics.append("modules_directory: must be a valid path")
            return None

    # -- queries --------------------------------------------------------------

    def origin(self, path: Sequence[Any]) -> str:
        """``overlay``, else ``base``, else ``default-not-set`` for *path*."""

        if _lookup(self.overlay, path) is not _MISSING:
            return ORIGIN_OVERLAY
        if _lookup(self.base, path) is not _MISSING:
            return ORIGIN_BASE
        return ORIGIN_DEFAULT

    def value(self, path: Sequence[Any]) -> Any:
        """The merged, unresolved value at *path*, or :data:`_MISSING`."""

        return _lookup(self.merged, path)

    def is_credential(self, path: Sequence[Any]) -> bool:
        """Whether *path* is a declared credential of a discovered module."""

        if len(path) < 3 or path[0] != "modules":
            return False
        module = self.modules.get(path[1])
        if module is None or module.declaration is None:
            return False
        return tuple(path[2:]) in module.declaration.credentials

    def display(self, path: Sequence[Any]) -> str | None:
        """The text shown for the value at *path*; ``None`` when not configured.

        A ``${NAME}`` reference is shown as itself, never resolved; a literal
        at a credential path is never shown at all.
        """

        value = self.value(path)
        if value is _MISSING:
            return None
        name = reference_name(value)
        if name is not None:
            return f"${{{name}}}"
        if self.is_credential(path):
            return HIDDEN_LITERAL
        return _plain(value)

    def enabled_modules(self) -> list[str]:
        """The merged ``enabled_modules`` names, in order."""

        listed = self.merged.get("enabled_modules")
        if not isinstance(listed, list):
            return []
        return [name for name in listed if isinstance(name, str)]

    def rules(self) -> list[Any]:
        """The merged ``actions`` rules, unresolved."""

        rules = self.merged.get(ACTIONS_KEY)
        return list(rules) if isinstance(rules, list) else []

    def uncovered_actions(self) -> list[tuple[str, str]]:
        """``(action, module)`` for each action an enabled module declares
        that no rule covers: none has an equal ``action_name`` or omits it."""

        rules = [rule for rule in self.rules() if isinstance(rule, Mapping)]
        uncovered: list[tuple[str, str]] = []
        for name in self.enabled_modules():
            module = self.modules.get(name)
            if module is None or module.declaration is None:
                continue
            for spec in module.declaration.actions:
                if not any(
                    rule.get("action_name", ANY_ACTION) in (ANY_ACTION, None, spec.name)
                    for rule in rules
                ):
                    uncovered.append((spec.name, name))
        return uncovered


def _plain(value: Any) -> str:
    """A configured value as text: strings as-is, anything else as JSON."""

    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)


# ---------------------------------------------------------------------------
# Status record reader, liveness and drift (R7 reader side, R4 readiness)
#
# The status record is untrusted input: the reader performs exactly the
# reader-verifiable validity checks of R7 — no coercion, no partial trust —
# and a failure is reported by check name, field name and expected type or
# set, never by any byte of the record. The publisher obligations (sequence
# order, container keys matching the enabled modules, ...) are never checked
# here: a usable record is trusted for them (R7, AC42).
# ---------------------------------------------------------------------------

#: The largest status record accepted, in bytes (R7).
STATUS_MAX_BYTES = 1024 * 1024
STATUS_VERSION = 1
STATUS_REQUIRED_FIELDS = (
    "version",
    "pid",
    "started_at",
    "published_at",
    "sequence",
    "digest",
    "state",
    "modules",
)
PID_MAX = 2147483647
SEQUENCE_MAX = 2**53 - 1
STATUS_STATES = ("ready",)
MODULE_STATES = ("ready", "degraded")

_TIMESTAMP = re.compile(
    r"([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})(?:\.([0-9]{1,6}))?Z"
)
_DIGEST = re.compile(r"[0-9a-f]{64}")

READING_ABSENT = "absent"
READING_UNUSABLE = "unusable"
READING_USABLE = "usable"

NO_STATUS_RECORD = "no status record"
STATUS_RECORD_UNUSABLE = "status record unusable"
DRIFT_IN_SYNC = "in sync"
DRIFT_DIFFERS = "differs"
DRIFT_UNKNOWN = "unknown"
NOT_SUPERVISED = "not supervised"
SUPERVISED = "supervised by this UI"
NO_RUNNING_PROCESS = "no running process is known"
PROCESS_NOT_RUNNING = "the recorded process is not running"
NOT_REPORTED = "not reported by the running process"


@dataclass(frozen=True)
class StatusRecord:
    """The fields of a usable status record the UI uses; extra keys are dropped."""

    pid: int
    started_at: str
    published_at: str
    sequence: int
    digest: str
    #: Module name → ``ready`` or ``degraded``, as the publisher reported.
    modules: Mapping[str, str]


@dataclass(frozen=True)
class StatusReading:
    """One of ``absent``, ``unusable(reason)`` or ``usable(record)`` (R7)."""

    kind: str
    reason: str | None = None
    record: StatusRecord | None = None

    @classmethod
    def absent(cls) -> StatusReading:
        return cls(READING_ABSENT)

    @classmethod
    def unusable(cls, reason: str) -> StatusReading:
        return cls(READING_UNUSABLE, reason=reason)

    @classmethod
    def usable(cls, record: StatusRecord) -> StatusReading:
        return cls(READING_USABLE, record=record)


class _Unusable(Exception):
    """A failed validity check; its message is value-free by construction."""

    def __init__(self, check: str, field_name: str, expected: str) -> None:
        super().__init__(f"{check}: {field_name}, expected {expected}")


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            # The key itself is record content: it is never named.
            raise _Unusable("duplicate key", "an object", "each key at most once")
        result[key] = value
    return result


def _no_constant(token: str) -> Any:
    raise _Unusable("not JSON", "a number", "a finite JSON number (no NaN or Infinity)")


def _is_integer(value: Any) -> bool:
    # ``type(...) is int`` excludes ``bool`` and every float (``1.0``, ``1e0``).
    return type(value) is int


def _check_timestamp(record: Mapping[str, Any], name: str) -> str:
    value = record[name]
    expected = "a UTC timestamp YYYY-MM-DDTHH:MM:SS[.ffffff]Z"
    if not isinstance(value, str):
        raise _Unusable("wrong type", f"field {name}", expected)
    match = _TIMESTAMP.fullmatch(value)
    if match is None:
        raise _Unusable("wrong format", f"field {name}", expected)
    year, month, day, hour, minute, second, fraction = match.groups()
    try:
        datetime(
            int(year),
            int(month),
            int(day),
            int(hour),
            int(minute),
            int(second),
            int((fraction or "0").ljust(6, "0")),
            tzinfo=timezone.utc,
        )
    except ValueError:
        raise _Unusable("invalid date or time", f"field {name}", expected) from None
    return value


def _check_record(document: Any) -> StatusRecord:
    if not isinstance(document, dict):
        raise _Unusable("wrong type", "top level", "a JSON object")
    for name in STATUS_REQUIRED_FIELDS:
        if name not in document:
            raise _Unusable("missing", f"field {name}", "present")
    version = document["version"]
    if not _is_integer(version) or version != STATUS_VERSION:
        raise _Unusable("wrong value", "field version", f"the integer {STATUS_VERSION}")
    pid = document["pid"]
    if not _is_integer(pid) or not 1 <= pid <= PID_MAX:
        raise _Unusable("wrong value", "field pid", f"an integer in 1..{PID_MAX}")
    started_at = _check_timestamp(document, "started_at")
    published_at = _check_timestamp(document, "published_at")
    sequence = document["sequence"]
    if not _is_integer(sequence) or not 1 <= sequence <= SEQUENCE_MAX:
        raise _Unusable("wrong value", "field sequence", f"an integer in 1..{SEQUENCE_MAX}")
    digest = document["digest"]
    if not isinstance(digest, str) or _DIGEST.fullmatch(digest) is None:
        raise _Unusable("wrong value", "field digest", "64 lowercase hexadecimal characters")
    state = document["state"]
    if not isinstance(state, str) or state not in STATUS_STATES:
        raise _Unusable("wrong value", "field state", "one of {" + ", ".join(STATUS_STATES) + "}")
    container = document["modules"]
    if not isinstance(container, dict):
        raise _Unusable("wrong type", "field modules", "a JSON object")
    modules: dict[str, str] = {}
    for key, entry in container.items():
        if not isinstance(key, str) or not key:
            raise _Unusable("wrong key", "field modules", "non-empty string keys")
        if not isinstance(entry, dict):
            raise _Unusable("wrong type", "a modules entry", "a JSON object")
        if "state" not in entry:
            raise _Unusable("missing", "state of a modules entry", "present")
        module_state = entry["state"]
        if not isinstance(module_state, str) or module_state not in MODULE_STATES:
            raise _Unusable(
                "wrong value",
                "state of a modules entry",
                "one of {" + ", ".join(MODULE_STATES) + "}",
            )
        modules[key] = module_state
    return StatusRecord(
        pid=pid,
        started_at=started_at,
        published_at=published_at,
        sequence=sequence,
        digest=digest,
        modules=modules,
    )


def read_status(path: str | os.PathLike[str]) -> StatusReading:
    """Classify the status record at *path* (R7): absent, unusable or usable."""

    unreadable = StatusReading.unusable("unreadable: the status path, expected a readable file")
    too_large = StatusReading.unusable(
        f"too large: the status file, expected at most {STATUS_MAX_BYTES} bytes"
    )
    try:
        found = os.stat(path)
    except FileNotFoundError:
        return StatusReading.absent()
    except (OSError, ValueError):
        return unreadable
    if not stat.S_ISREG(found.st_mode):
        return StatusReading.unusable("not a regular file: the status path, expected a regular file")
    if found.st_size > STATUS_MAX_BYTES:
        return too_large
    try:
        with open(path, "rb") as handle:
            # Capped: the file may have grown since the stat.
            data = handle.read(STATUS_MAX_BYTES + 1)
    except FileNotFoundError:
        return StatusReading.absent()
    except OSError:
        return unreadable
    if len(data) > STATUS_MAX_BYTES:
        return too_large
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return StatusReading.unusable("not UTF-8: the status file, expected UTF-8 text")
    try:
        document = json.loads(text, object_pairs_hook=_unique_pairs, parse_constant=_no_constant)
        return StatusReading.usable(_check_record(document))
    except _Unusable as exc:
        return StatusReading.unusable(str(exc))
    except (ValueError, RecursionError):
        return StatusReading.unusable("not JSON: the status file, expected one JSON text")


class ChildHandle(Protocol):
    """What the UI knows of the process it launched: a ``subprocess.Popen``."""

    pid: int

    def poll(self) -> int | None: ...


#: ``probe(pid, child)`` → whether the process *pid* is alive (R7).
Probe = Callable[[int, "ChildHandle | None"], bool]


def process_alive(pid: int, child: ChildHandle | None) -> bool:
    """The default liveness probe (R7).

    The UI's own child handle is authoritative for its child; any other pid
    is probed with signal 0: "no such process" is dead, success or
    "permission denied" is alive.
    """

    if child is not None and child.pid == pid:
        return child.poll() is None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@dataclass(frozen=True)
class RunningState:
    """The status record as the pages show it (R4, R7)."""

    reading: StatusReading
    #: A usable record whose process is alive: only then is there running state.
    alive: bool
    drift: str
    supervised: bool

    @property
    def live_record(self) -> StatusRecord | None:
        return self.reading.record if self.alive else None

    def record_text(self) -> str:
        """The record status: "no status record", "status record unusable: …" or its time."""

        if self.reading.kind == READING_ABSENT:
            return NO_STATUS_RECORD
        if self.reading.kind == READING_UNUSABLE:
            return f"{STATUS_RECORD_UNUSABLE}: {self.reading.reason}"
        record = self.reading.record
        assert record is not None
        return f"status record published at {record.published_at}"

    def supervision_text(self) -> str:
        record = self.reading.record
        if record is None:
            return NO_RUNNING_PROCESS
        label = SUPERVISED if self.supervised else NOT_SUPERVISED
        if not self.alive:
            return f"{PROCESS_NOT_RUNNING} ({label})"
        return label

    def module_text(self, name: str) -> tuple[str | None, str]:
        """Module *name*'s running state and its text; ``None`` when there is none."""

        record = self.live_record
        if record is None:
            return None, NO_RUNNING_PROCESS
        state = record.modules.get(name)
        if state is None:
            return None, NOT_REPORTED
        label = SUPERVISED if self.supervised else NOT_SUPERVISED
        return state, f"running: {state} at {record.published_at} ({label})"


# ---------------------------------------------------------------------------
# Redaction guard (R8)
#
# Defence in depth: the renderers never place a secret-set value in a page
# (credentials and references are shown as ``${NAME}`` or as
# :data:`HIDDEN_LITERAL`); this guard replaces each secret-set value by
# ``[hidden]`` anyway, in every configured value as :func:`esc` renders it
# and in every log record. It works on values before they are serialized,
# never on the finished body or headers, so a secret that happens to equal a
# path, a field name or any other piece of markup cannot rewrite it. Every
# secret-set value is replaced whatever its length, a single character
# included (gate-1 F2): the replacement only ever touches configured text.
# Declared identifiers (module directory names, manifest field and limit
# names, ``${NAME}`` variable names) go through :func:`ident`, which never
# redacts. A configured key (a trigger channel, a named entry, a rule id) is
# never an identifier: it is shown as text through :func:`esc`, and every
# generated identifier naming it (a control ``name``, a ``data-`` hook, a
# Remove target) stands for it by its position alone (:func:`_field_name`,
# :func:`_key_token`), which the parser maps back (gate-2 F2).
# ---------------------------------------------------------------------------

REDACTED = "[hidden]"
#: The prefix of the opaque name :func:`_field_name` gives a path whose
#: configured key has no position in the configuration it is named from.
OPAQUE_FIELD_PREFIX = "@field-"
#: The prefix of :func:`_key_token`, the positional stand-in for a key.
KEY_TOKEN_PREFIX = "@"
#: Keys :data:`OPAQUE_FIELD_PREFIX` names rendered without a UI.
_PROCESS_FIELD_KEY = secrets.token_bytes(32)

#: The UI answering the current request; :func:`esc` redacts its secret set
#: as it stands when the value is rendered (the handler refreshes it first).
_RENDERING_FOR: ContextVar[ConfigUI | None] = ContextVar("_RENDERING_FOR", default=None)


def _redaction_forms(secret_values: Iterable[str]) -> list[str]:
    """Every form a value can take in a response, longest first."""

    forms: set[str] = set()
    for value in secret_values:
        if not value:
            continue
        forms.add(value)
        forms.add(html.escape(value, quote=True))
    return sorted(forms, key=len, reverse=True)


def _redact(text: str, secret_values: Iterable[str]) -> str:
    """*text* with every secret-set value replaced by ``[hidden]``."""

    for form in _redaction_forms(secret_values):
        text = text.replace(form, REDACTED)
    return text


class _RedactionFilter(logging.Filter):
    """Redacts the secret set of every live :class:`ConfigUI` from UI log records."""

    def __init__(self) -> None:
        super().__init__()
        self.sources: weakref.WeakSet[ConfigUI] = weakref.WeakSet()

    def filter(self, record: logging.LogRecord) -> bool:
        values = [value for source in list(self.sources) for value in source.secret_values]
        if values:
            message = record.getMessage()
            redacted = _redact(message, values)
            if redacted != message:
                record.msg, record.args = redacted, None
        return True


_LOG_REDACTION = _RedactionFilter()
logger.addFilter(_LOG_REDACTION)


# ---------------------------------------------------------------------------
# Rendering (R4, R9)
#
# Every configured value goes through :func:`esc`; the CSS and the script are
# inline constants, every form posts to a relative path and no page names an
# external URL. No page is written for any module: the base page lists what
# discovery found.
# ---------------------------------------------------------------------------


def esc(value: Any) -> str:
    """HTML-escape *value* (quotes included), as text, secret-set values redacted."""

    text = value if isinstance(value, str) else _plain(value)
    source = _RENDERING_FOR.get()
    if source is not None:
        text = _redact(text, source.secret_values)
    return html.escape(text, quote=True)


def _guarded(text: str) -> str:
    """*text* as :func:`esc` would show it, before HTML escaping: redacted."""

    source = _RENDERING_FOR.get()
    return text if source is None else _redact(text, source.secret_values)


def _key_token(position: int) -> str:
    """The positional stand-in for the configured key at *position* (gate-2 F2).

    The index of the key among its mapping's keys, in configuration order:
    it names the key in generated identifiers without embedding its text.
    """

    return f"{KEY_TOKEN_PREFIX}{position}"


def _schema_configured(
    schema: Any, path: Sequence[Any], start: int, configured: set[int]
) -> None:
    """Add to *configured* each segment of ``path[start:]`` *schema* does not declare.

    A segment is declared text only where it is a ``properties`` key of the
    schema node it is looked up in (or the ``[]``/``<entry>`` notation of a
    described node); any other key, a named entry included, is configured.
    Below a key the schema does not describe, every key is configured.
    """

    node = schema
    for index in range(start, len(path)):
        segment = path[index]
        mapping = node if isinstance(node, Mapping) else {}
        if isinstance(segment, int) and not isinstance(segment, bool):
            # A list position; :func:`_configured_segments` checks it
            # against the document.
            node = mapping.get("items")
            continue
        if segment == ITEMS_SEGMENT:
            node = mapping.get("items")
            continue
        elif segment == ENTRY_SEGMENT:
            node = mapping.get("additionalProperties")
            continue
        properties = mapping.get("properties")
        if isinstance(segment, str) and isinstance(properties, Mapping) and segment in properties:
            node = properties[segment]
            continue
        configured.add(index)
        node = mapping.get("additionalProperties")


def _configured_segments(view: ConfigView, path: Sequence[Any]) -> set[int]:
    """The indexes of *path*'s segments that are configured keys (gate-2 F2).

    Decided by the segment's position in the document's structure, never by
    its text: a channel key spelled like a declared name (``combination``,
    ``rules``, a module name) is still a configured key. An index into a
    configured list is positional already and is never one; an integer key
    of a mapping (a numeric channel id) is.
    """

    configured: set[int] = set()

    def keys_from(start: int) -> None:
        configured.update(range(start, len(path)))

    head = path[0] if path else None
    module = view.modules.get(path[1]) if len(path) > 1 else None
    if head == "modules":
        if module is None:
            keys_from(1)
        else:
            _schema_configured(module.manifest.get(MANIFEST_SETTINGS_SCHEMA_KEY), path, 2, configured)
    elif head == "triggers":
        if module is None or (len(path) > 2 and path[2] != "channels"):
            keys_from(1)
        elif len(path) > 3:
            configured.add(3)  # the channel
            tail = tuple(path[4:])
            if len(tail) > 2 and tail[0] == "rules" and tail[2] == "parameters":
                rule = _lookup(view.merged, path[:6])
                rule_type = rule.get("type") if isinstance(rule, Mapping) else None
                types = _trigger_types(module.manifest)
                schema = types.get(rule_type) if isinstance(rule_type, str) else None
                _schema_configured(schema, path, 7, configured)
            elif len(tail) > 2 and tail[0] == "rules" and tail[2] == "type":
                keys_from(7)
            elif tail and tail[0] == "rules":
                keys_from(6)
            elif tail and tail[0] == "combination":
                keys_from(5)
            else:
                keys_from(4)
    elif head == "limits":
        fields = LIMIT_DECLARATION.get(path[1]) if len(path) > 1 else None
        if fields is None:
            keys_from(1)
        elif len(path) > 2 and path[2] not in fields:
            keys_from(2)
        else:
            keys_from(3)
    elif head in _TOP_LEVEL_SEGMENTS:
        keys_from(1)
    else:
        keys_from(0)
    # The document decides what an integer is: a list position, or a key.
    for index, segment in enumerate(path):
        parent = _lookup(view.merged, path[:index])
        if isinstance(parent, list):
            configured.discard(index)
        elif isinstance(parent, Mapping) and isinstance(segment, int):
            configured.add(index)
        elif segment in (ITEMS_SEGMENT, ENTRY_SEGMENT) and parent is _MISSING:
            # The notation of a described node, not a key of the document.
            configured.discard(index)
    return configured


def _field_name(path: Sequence[Any], view: ConfigView | None) -> str:
    """The form-field name, ``data-path`` and Remove target of setting *path*.

    Declared segments (see :func:`_configured_segments`) keep their text; a
    configured key (a trigger channel, a named entry) is replaced by
    :func:`_key_token`, its position among its mapping's keys, so no
    configured key ever reaches a generated identifier, whatever its
    spelling or length (gate-2 F2). The parser reads a posted form back
    through this same function over the same configuration. A key with no
    position in *view* (never rendered) gives the whole path an opaque
    keyed-digest name. Without a *view* (no configuration to take positions
    from) the path's text is returned.
    """

    if view is None:
        return _path_text(path)
    configured = _configured_segments(view, path)
    if not configured:
        return _path_text(path)
    text = ""
    for index, segment in enumerate(path):
        if index in configured:
            parent = _lookup(view.merged, path[:index])
            keys = list(parent) if isinstance(parent, Mapping) else []
            if segment not in keys:
                source = _RENDERING_FOR.get()
                key = _PROCESS_FIELD_KEY if source is None else source.field_key
                digest = hmac.new(key, _path_text(path).encode("utf-8"), hashlib.sha256).hexdigest()
                return OPAQUE_FIELD_PREFIX + digest[:32]
            text += ("." if text else "") + _key_token(keys.index(segment))
        elif isinstance(segment, int) and not isinstance(segment, bool):
            text += f"[{segment}]"
        elif segment == ITEMS_SEGMENT:
            text += ITEMS_SEGMENT
        else:
            text += ("." if text else "") + _plain(segment)
    return text


def _names_path(posted: str, path: Sequence[Any], view: ConfigView | None) -> bool:
    """Whether *posted* names *path*: by its :func:`_field_name`, nothing else."""

    return posted == _field_name(path, view)


def ident(name: str) -> str:
    """HTML-escape a structural identifier, never redacted.

    Module names (discovered directories), limit group and field names (the
    declaration) and ``${NAME}`` variable names are markup: they build paths,
    control names and ``data-`` hooks. Redacting one that happens to equal a
    secret-set value would break a link or a form field, and none of them is
    a configured value.
    """

    return html.escape(name, quote=True)


_CSS = """
:root { color-scheme: light dark; font-family: system-ui, sans-serif; }
body { margin: 0 auto; max-width: 60rem; padding: 1rem; line-height: 1.4; }
table { border-collapse: collapse; width: 100%; margin: 0.5rem 0; }
th, td { border-bottom: 1px solid #8884; padding: 0.3rem 0.5rem; text-align: left; vertical-align: top; }
section { margin: 1.5rem 0; }
.read-only { border-left: 4px solid #c90; padding-left: 0.75rem; }
.reason, .origin, .kind { color: #777; font-size: 0.9em; }
.diagnostics { color: #b00; }
form.inline { display: inline; margin: 0; }
code { font-family: ui-monospace, monospace; }
fieldset { margin: 0.5rem 0; }
.field, .notice { margin: 0.5rem 0; }
.help, .meta { margin: 0.2rem 0; color: #777; font-size: 0.9em; }
.not-editable { border-left: 4px solid #c90; padding-left: 0.75rem; }
.required { color: #b00; text-decoration: none; }
textarea { width: 100%; font-family: ui-monospace, monospace; }
"""

_SCRIPT = """
document.addEventListener("submit", function (event) {
  var buttons = event.target.querySelectorAll("button");
  for (var i = 0; i < buttons.length; i++) { buttons[i].disabled = true; }
});
"""


def _csrf_field(session: _Session) -> str:
    return f'<input type="hidden" name="{CSRF_FIELD}" value="{html.escape(session.csrf_token, quote=True)}">'


#: The field carrying the overlay fingerprint a form was rendered from, so a
#: Save or Remove of a file changed since is refused as stale (R6).
FINGERPRINT_FIELD = "fingerprint"
#: The fingerprint of an overlay file that does not exist.
FINGERPRINT_ABSENT = "absent"


def _view_fingerprint(view: ConfigView) -> str:
    """The overlay fingerprint of *view*: its bytes' SHA-256, or ``absent``.

    An overlay that exists but could not be read has no fingerprint: its
    forms carry an empty one, which never matches, so nothing overwrites it.
    """

    if not view.overlay_exists:
        return FINGERPRINT_ABSENT
    return view.overlay_digest or ""


#: The field carrying the key layout a form's positional names were rendered
#: from (see :func:`_view_layout`).
LAYOUT_FIELD = "layout"


def _key_skeleton(node: Any) -> Any:
    """The keys of every mapping under *node*, in order, and list lengths."""

    if isinstance(node, Mapping):
        return [[f"{type(key).__name__}:{key}", _key_skeleton(value)] for key, value in node.items()]
    if isinstance(node, list):
        return [_key_skeleton(item) for item in node]
    return None


def _view_layout(view: ConfigView) -> str:
    """A keyed digest of the merged configuration's key order.

    :func:`_field_name` names a configured key by its position, so a form is
    only meaningful against the key order it was rendered from. The overlay
    fingerprint does not cover the base file: reordering the base's keys
    would retarget a posted positional name at another key. A form carries
    this digest and a write whose current layout differs is stale. It is
    keyed by the UI's field key, so no key text can be guessed from it.
    """

    source = _RENDERING_FOR.get()
    key = _PROCESS_FIELD_KEY if source is None else source.field_key
    skeleton = json.dumps(_key_skeleton(view.merged), ensure_ascii=False, separators=(",", ":"))
    return hmac.new(key, skeleton.encode("utf-8"), hashlib.sha256).hexdigest()


def _write_guard(session: _Session, view: ConfigView) -> str:
    """The hidden fields every form that saves or removes carries."""

    return (
        f"{_csrf_field(session)}"
        f'<input type="hidden" name="{FINGERPRINT_FIELD}" value="{ident(_view_fingerprint(view))}">'
        f'<input type="hidden" name="{LAYOUT_FIELD}" value="{ident(_view_layout(view))}">'
    )


#: The field naming the page a form was posted from, so Check answers with
#: that page's view of the diagnostics (R5).
PAGE_FIELD = "page"
CORE_PAGE = "/core"

#: Posts the enclosing form, unsaved edits included, to Check (R5).
#: ``formnovalidate``: a value the browser's constraints reject (a number
#: below its minimum) still reaches Check, whose diagnostics name it.
_CHECK_BUTTON = '<button type="submit" formaction="/check" formnovalidate>Check</button>'


def _page_field(page: str) -> str:
    return f'<input type="hidden" name="{PAGE_FIELD}" value="{ident(page)}">'


def _page(title: str, session: _Session, body: str) -> UIResponse:
    document = (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"<meta name=\"csrf-token\" content=\"{html.escape(session.csrf_token, quote=True)}\">"
        f"<title>{esc(title)}</title><style>{_CSS}</style></head>"
        f"<body>{body}<script>{_SCRIPT}</script></body></html>"
    )
    return UIResponse(
        status=200, body=document.encode("utf-8"), content_type="text/html; charset=utf-8"
    )


def _diagnostics(view: ConfigView) -> str:
    if not view.diagnostics:
        return ""
    items = "".join(f"<li>{esc(line)}</li>" for line in view.diagnostics)
    return f'<section class="diagnostics" id="diagnostics"><h2>Diagnostics</h2><ul>{items}</ul></section>'


def _state(is_set: bool) -> str:
    return "set" if is_set else "unset"


def _listing(value: Any) -> str:
    """A rule field as escaped text, ${NAME} references left unresolved."""

    if value is None:
        return ""
    if isinstance(value, Mapping):
        return "<br>".join(f"{esc(_plain(key))}: {esc(item)}" for key, item in value.items())
    if isinstance(value, list):
        return ", ".join(esc(item) for item in value)
    return esc(value)


# ---------------------------------------------------------------------------
# Generated module pages (R4b, A2, A7, R9)
#
# A module page is generated from its manifest alone: the settings form from
# ``settings_schema`` and the trigger policies from each declared trigger
# type's ``parameter_schema``, both through :class:`_SchemaRenderer` over the
# subset ``type``, ``properties``, ``required``, ``additionalProperties``,
# ``enum``, ``items``, ``minimum`` and ``maximum``. Every rendered node
# carries its setting path in ``data-path`` (see :func:`_path_text`); a node
# the page cannot edit is shown with a "not editable here" notice naming its
# path, never silently omitted.
# ---------------------------------------------------------------------------

NOT_EDITABLE = "not editable here"
BASE_ENTRY_NOTE = "the overlay cannot delete a base entry"
LIMITS_NOTICE = "the reserved limits setting is handed to every module from the core limits"

#: Setting-path segments standing for "each item" of an array and "each
#: named entry" of an ``additionalProperties`` mapping: ``a.b[]`` and
#: ``a.<entry>`` (the notation the tests' schema walker uses too).
ITEMS_SEGMENT = "[]"
ENTRY_SEGMENT = "<entry>"

#: The top-level keys of the configuration document the pages name.
_TOP_LEVEL_SEGMENTS = frozenset(
    {"modules", "modules_directory", "enabled_modules", "limits", "secrets", "actions", "triggers"}
)


_SCALAR_KINDS = ("string", "integer", "number", "boolean")

#: The form value of a boolean select's option that keeps a configured
#: non-boolean value: distinct from ``true``, ``false`` and ``""`` (inherit),
#: so it only ever posts "left untouched".
_KEEP_NON_BOOLEAN = "keep-configured"


def _path_text(path: Sequence[Any]) -> str:
    """A setting path as text: dotted names, ``[i]`` indexes, ``[]`` items."""

    text = ""
    for segment in path:
        if isinstance(segment, int) and not isinstance(segment, bool):
            text += f"[{segment}]"
        elif segment == ITEMS_SEGMENT:
            text += ITEMS_SEGMENT
        else:
            text += ("." if text else "") + _plain(segment)
    return text


def _schema_kind(schema: Mapping[str, Any]) -> str | None:
    """The node's type; an untyped node with ``properties`` or ``items`` is inferred."""

    kind = schema.get("type")
    if isinstance(kind, str):
        return kind
    if kind is None:
        if isinstance(schema.get("properties"), Mapping) or isinstance(
            schema.get("additionalProperties"), Mapping
        ):
            return "object"
        if "items" in schema:
            return "array"
    return None


def _trigger_types(manifest: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    """The declared trigger types, name → ``parameter_schema``, in order."""

    declared = manifest.get(MANIFEST_TRIGGERS_KEY)
    if not isinstance(declared, Mapping):
        return {}
    types: dict[str, Mapping[str, Any]] = {}
    listed = declared.get("types")
    for entry in listed if isinstance(listed, list) else []:
        if isinstance(entry, Mapping) and isinstance(entry.get("name"), str):
            schema = entry.get("parameter_schema")
            types[entry["name"]] = schema if isinstance(schema, Mapping) else {}
    return types


def _key_label(key: Any, view: ConfigView) -> str:
    """A configured mapping key as text; a ``${NAME}`` key stays a reference."""

    name = reference_name(key)
    if name is not None:
        return f"<code>${{{ident(name)}}}</code> ({_state(view.references.get(name, False))})"
    return f"<code>{esc(_plain(key))}</code>"


@dataclass(frozen=True)
class _Control:
    """One rendered form control: what its posted text means (R5, R6).

    ``kind`` is how the posted text is read back (see :func:`_coerce`);
    ``rendered`` is the text the control posts when left untouched, so a
    posted field equal to it is no edit at all.
    """

    path: tuple[Any, ...]
    kind: str
    rendered: str
    schema: Mapping[str, Any] = field(default_factory=dict)


class _SchemaRenderer:
    """Renders a JSON-schema subset as form controls over one configuration view.

    A *live* node edits the configured value at its path and shows its
    current value and origin; a node under an array's ``items`` or a
    mapping's named entries is *descriptive*: it documents the shape the
    enclosing JSON editor accepts, with no control of its own. Every live
    control is recorded in :attr:`controls` by its field name, so a posted
    form is read back through the very controls the page rendered.
    """

    def __init__(self, view: ConfigView) -> None:
        self.view = view
        self.controls: dict[str, _Control] = {}
        #: ``new_entry_*`` field suffix → the mapping path a new entry joins.
        self.entry_mappings: dict[str, tuple[Any, ...]] = {}

    def register(
        self, path: Sequence[Any], kind: str, rendered: str, schema: Mapping[str, Any] | None = None
    ) -> None:
        # Keyed by the name the control renders and holding the text it posts
        # untouched: both are what the page shows, secret-set values redacted.
        self.controls[_field_name(path, self.view)] = _Control(tuple(path), kind, _guarded(rendered), schema or {})

    # -- values -------------------------------------------------------------

    def origin(self, path: Sequence[Any]) -> str:
        return ConfigUI._origin(self.view, path)

    def _masked(self, path: Sequence[Any], value: Any) -> Any:
        """*value* with every nested credential literal replaced (R8)."""

        if reference_name(value) is not None:
            return value
        if self.view.is_credential(path):
            return HIDDEN_LITERAL
        if isinstance(value, Mapping):
            return {key: self._masked((*path, key), item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._masked((*path, index), item) for index, item in enumerate(value)]
        return value

    def configured_text(self, path: Sequence[Any]) -> str | None:
        """The unresolved configured text at *path*, credentials hidden."""

        value = self.view.value(path)
        if value is _MISSING:
            return None
        return _plain(self._masked(path, value))

    def _json_text(self, path: Sequence[Any]) -> str:
        value = self.view.value(path)
        if value is _MISSING:
            return ""
        return json.dumps(self._masked(path, value), ensure_ascii=False, default=str)

    # -- nodes --------------------------------------------------------------

    def children(self, schema: Mapping[str, Any], path: Sequence[Any], live: bool = True) -> str:
        """The properties (and named entries) of an object node at *path*."""

        properties = schema.get("properties")
        properties = properties if isinstance(properties, Mapping) else {}
        listed = schema.get("required")
        required = set(listed) if isinstance(listed, list) else set()
        parts = [
            self.node(sub, (*path, key), required=key in required, live=live)
            for key, sub in properties.items()
        ]
        entries = schema.get("additionalProperties")
        if isinstance(entries, Mapping):
            parts.append(self._entries(entries, path, set(properties), live))
        return "".join(parts)

    def node(
        self, schema: Any, path: Sequence[Any], *, required: bool = False, live: bool = True
    ) -> str:
        if not isinstance(schema, Mapping):
            return self.notice(path, "its schema is not a mapping", live=live)
        if live and len(path) == 3 and path[0] == "modules" and path[2] == LIMITS_KEY:
            return self.notice(path, LIMITS_NOTICE, schema, required)
        kind = _schema_kind(schema)
        enum = schema.get("enum")
        if isinstance(enum, list) and kind in (None, *_SCALAR_KINDS):
            return self._field(schema, path, required, live, "enum")
        if kind in _SCALAR_KINDS:
            return self._field(schema, path, required, live, kind)
        if kind == "object":
            has_properties = isinstance(schema.get("properties"), Mapping)
            if has_properties or isinstance(schema.get("additionalProperties"), Mapping):
                return (
                    f'<fieldset class="object" {self._attributes(path, required, live)}>'
                    f"<legend>{self._label(schema, path, required)}</legend>"
                    f"{self._help(schema, path, live)}{self.children(schema, path, live)}</fieldset>"
                )
            return self.notice(path, "an object without properties", schema, required, live)
        if kind == "array":
            items = schema.get("items")
            if isinstance(items, Mapping):
                return self._list(schema, items, path, required, live)
            return self.notice(path, "an array without items", schema, required, live)
        if kind == "null":
            return self.notice(path, "a null-typed setting", schema, required, live)
        return self.notice(path, "a type outside the rendered subset", schema, required, live)

    def notice(
        self,
        path: Sequence[Any],
        reason: str,
        schema: Mapping[str, Any] | None = None,
        required: bool = False,
        live: bool = True,
    ) -> str:
        """A "not editable here" notice naming *path*, its configured text read-only.

        Under a JSON editor (*live* false) the node is edited through that
        editor, so it is only described.
        """

        if not live:
            described = schema or {}
            return (
                f'<div class="field described" {self._attributes(path, required, live)}>'
                f"{self._label(described, path, required)}{self._help(described, path, live)}</div>"
            )
        label = self._label(schema, path, required) if schema is not None else ""
        configured = ""
        if live:
            text = self.configured_text(path)
            configured = (
                '<span class="value">not configured</span>'
                if text is None
                else f'<pre class="value">{esc(text)}</pre>'
            )
        return (
            f'<div class="notice not-editable" {self._attributes(path, required, live)} data-notice="true">'
            f"{label} <strong>{NOT_EDITABLE}</strong>: <code>{esc(_path_text(path))}</code> "
            f'<span class="reason">({esc(reason)})</span>'
            f"{self._help(schema or {}, path, False)}{configured}</div>"
        )

    # -- pieces -------------------------------------------------------------

    def _attributes(self, path: Sequence[Any], required: bool, live: bool) -> str:
        attributes = f'data-path="{ident(_field_name(path, self.view))}"'
        if live:
            attributes += f' data-origin="{ident(self.view.origin(path))}"'
        if required:
            attributes += ' data-required="true"'
        return attributes

    @staticmethod
    def _label(schema: Mapping[str, Any], path: Sequence[Any], required: bool) -> str:
        title = schema.get("title")
        last = path[-1] if path else ""
        if isinstance(title, str) and title:
            text = title
        elif last == ITEMS_SEGMENT:
            text = "each item"
        elif last == ENTRY_SEGMENT:
            text = "each entry"
        else:
            text = _plain(last)
        marker = ' <abbr class="required" title="required">*</abbr>' if required else ""
        return f'<span class="title">{ident(text)}</span>{marker}'

    def _help(self, schema: Mapping[str, Any], path: Sequence[Any], live: bool) -> str:
        parts: list[str] = []
        description = schema.get("description")
        if isinstance(description, str) and description:
            parts.append(f'<p class="help">{ident(description)}</p>')
        meta: list[str] = []
        if "default" in schema:
            meta.append(f'default: <code class="default">{esc(_plain(schema["default"]))}</code>')
        if live:
            meta.append(f"current: {self._current(path)}")
            meta.append(f'origin: <span class="origin">{esc(self.origin(path))}</span>')
            if self.view.origin(path) == ORIGIN_OVERLAY:
                meta.append(
                    '<button type="submit" formaction="/remove" name="path" '
                    f'value="{ident(_field_name(path, self.view))}">Remove override</button>'
                )
        if meta:
            parts.append(f'<p class="meta">{" · ".join(meta)}</p>')
        return "".join(parts)

    def _current(self, path: Sequence[Any]) -> str:
        value = self.view.value(path)
        if value is _MISSING:
            return '<span class="value">not set</span>'
        name = reference_name(value)
        if name is not None:
            is_set = self.view.references.get(name, False)
            return (
                f'<code class="value">${{{ident(name)}}}</code> '
                f'<span class="reference-state">({_state(is_set)})</span>'
            )
        return f'<code class="value">{esc(_plain(self._masked(path, value)))}</code>'

    def _field(
        self, schema: Mapping[str, Any], path: Sequence[Any], required: bool, live: bool, kind: str
    ) -> str:
        control = self._control(schema, path, kind) if live else ""
        return (
            f'<div class="field" {self._attributes(path, required, live)}>'
            f"<label>{self._label(schema, path, required)} {control}</label>"
            f"{self._help(schema, path, live)}</div>"
        )

    def _control(self, schema: Mapping[str, Any], path: Sequence[Any], kind: str) -> str:
        name = ident(_field_name(path, self.view))
        value = self.view.value(path)
        if reference_name(value) is not None:
            # A reference is edited as its text, whatever the declared type.
            self.register(path, kind, value, schema)
            return f'<input type="text" name="{name}" value="{esc(value)}">'
        if self.view.is_credential(path):
            # A literal credential is never placed in the page (R8),
            # whatever control its declared type would get (enum, number, ...).
            # It posts "" when left untouched, whether a literal is configured or not.
            self.register(path, kind, "", schema)
            return f'<input type="text" name="{name}" value="" placeholder="{esc(HIDDEN_LITERAL)}">'
        if kind == "enum":
            members = schema["enum"]
            selected = [member for member in members if value is not _MISSING and value == member]
            # An unselected <select> posts its first option.
            chosen = selected[0] if selected else (members[0] if members else "")
            self.register(path, kind, _plain(chosen), schema)
            options = "".join(
                f'<option value="{esc(_plain(member))}"'
                f'{" selected" if value is not _MISSING and value == member else ""}>'
                f"{esc(_plain(member))}</option>"
                for member in schema["enum"]
            )
            return f'<select name="{name}">{options}</select>'
        if kind == "boolean":
            return self._boolean(schema, path, name, value)
        if kind in ("integer", "number") and (
            value is _MISSING or (isinstance(value, (int, float)) and not isinstance(value, bool))
        ):
            bounds = ""
            for keyword, attribute in (("minimum", "min"), ("maximum", "max")):
                bound = schema.get(keyword)
                if isinstance(bound, (int, float)) and not isinstance(bound, bool):
                    bounds += f' {attribute}="{esc(_plain(bound))}"'
            step = "1" if kind == "integer" else "any"
            shown = "" if value is _MISSING else _plain(value)
            self.register(path, kind, shown, schema)
            if _guarded(shown) != shown:
                # A number input would post its redacted text as "".
                return f'<input type="text" name="{name}" value="{esc(shown)}">'
            return f'<input type="number" name="{name}"{bounds} step="{step}" value="{esc(shown)}">'
        shown = "" if value is _MISSING else _plain(value)
        self.register(path, kind, shown, schema)
        return f'<input type="text" name="{name}" value="{esc(shown)}">'

    def _boolean(self, schema: Mapping[str, Any], path: Sequence[Any], name: str, value: Any) -> str:
        """A three-state boolean: not set (inherit), ``true`` or ``false`` (gate F3).

        A checkbox has two states, so an unset setting whose default is
        ``true`` drew unchecked and an explicit ``false`` posted as "left
        untouched". The ``<select>`` always shows the effective value
        (``data-effective``): when not set, its first option inherits and
        names the declared default, and ``false`` or ``true`` is an explicit
        edit. A configured value is removed with "Remove override"; one that
        is not a boolean is kept as its own option, so it posts untouched.
        That option posts ``_KEEP_NON_BOOLEAN``, never the value's own text:
        a configured string ``'true'`` must leave the real ``true`` choice
        selectable, so it can be corrected to the boolean.
        """

        default = schema.get("default")
        if value is _MISSING:
            effective = _plain(default) if isinstance(default, bool) else ""
            inherit = f"not set (default: {effective})" if effective else "not set"
            current: tuple[str, str] | None = ("", inherit)
        elif isinstance(value, bool):
            effective, current = _plain(value), None
        else:
            effective = _plain(value)
            current = (_KEEP_NON_BOOLEAN, f"{effective} (not a boolean)")
        rendered = current[0] if current is not None else effective
        self.register(path, "boolean", rendered, schema)
        choices = ([current] if current is not None else []) + [
            (option, option) for option in ("true", "false")
        ]
        options = "".join(
            f'<option value="{esc(option)}"{" selected" if option == rendered else ""}>'
            f"{esc(label)}</option>"
            for option, label in choices
        )
        return (
            f'<select name="{name}" data-kind="boolean" data-effective="{esc(effective)}">'
            f"{options}</select>"
        )

    def _list(
        self,
        schema: Mapping[str, Any],
        items: Mapping[str, Any],
        path: Sequence[Any],
        required: bool,
        live: bool,
    ) -> str:
        """A list editor: one JSON text area, validated server-side."""

        if live:
            self.register(path, "json", self._json_text(path), schema)
        editor = (
            f'<textarea name="{ident(_field_name(path, self.view))}" data-json="list" rows="3">'
            f"{esc(self._json_text(path))}</textarea>"
            if live
            else ""
        )
        return (
            f'<div class="field list" {self._attributes(path, required, live)}>'
            f"<label>{self._label(schema, path, required)} {editor}</label>"
            f"{self._help(schema, path, live)}"
            '<div class="items">Each item (JSON list):'
            f"{self.node(items, (*path, ITEMS_SEGMENT), live=False)}</div></div>"
        )

    def _entries(
        self, schema: Mapping[str, Any], path: Sequence[Any], known: set[Any], live: bool
    ) -> str:
        """A named-entries editor: one JSON text area per entry, and an add row."""

        rows: list[str] = []
        if live:
            configured = self.view.value(path)
            entries = configured if isinstance(configured, Mapping) else {}
            for position, key in enumerate(entries):
                if key in known:
                    continue
                entry_path = (*path, key)
                self.register(entry_path, "json", self._json_text(entry_path), schema)
                rows.append(
                    f'<div class="entry" data-entry="{_key_token(position)}">'
                    f"<label>Entry {_key_label(key, self.view)} "
                    f'<textarea name="{ident(_field_name(entry_path, self.view))}" data-json="entry" rows="2">'
                    f"{esc(self._json_text(entry_path))}</textarea></label> "
                    f'<span class="origin">origin: {esc(self.origin(entry_path))}</span></div>'
                )
            self.entry_mappings[_field_name(path, self.view)] = tuple(path)
            prefix = ident(_field_name(path, self.view))
            rows.append(
                f'<div class="entry new">New entry name <input type="text" name="new_entry_name:{prefix}"> '
                f'value (JSON) <textarea name="new_entry_value:{prefix}" rows="2"></textarea></div>'
            )
        return (
            '<div class="entries">Named entries:'
            f"{self.node(schema, (*path, ENTRY_SEGMENT), live=False)}{''.join(rows)}</div>"
        )


# ---------------------------------------------------------------------------
# Request core (D2): guards, sessions, route table
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UIRequest:
    """One HTTP request, as plain data. Header names are lower-case."""

    method: str
    path: str
    query: Mapping[str, str] = field(default_factory=dict)
    headers: Mapping[str, str] = field(default_factory=dict)
    body: bytes = b""


@dataclass(frozen=True)
class UIResponse:
    """One HTTP response, as plain data."""

    status: int
    body: bytes = b""
    content_type: str = "text/plain; charset=utf-8"
    headers: tuple[tuple[str, str], ...] = ()


@dataclass
class _Session:
    csrf_token: str


Handler = Callable[[UIRequest, _Session], UIResponse]

#: Paths whose requests change state: POST only, Origin- and CSRF-checked.
STATE_CHANGING_PATHS = frozenset({"/save", "/remove", "/check", "/apply"})

#: Every discovered module's page lives under this prefix (R4b).
MODULE_PAGE_PREFIX = "/module/"

SESSION_COOKIE = "config_ui_session"
CSRF_HEADER = "x-csrf-token"
CSRF_FIELD = "csrf_token"

#: Bounded session table: the oldest session is dropped past this size.
MAX_SESSIONS = 32

_SECURITY_HEADERS = (
    ("Cache-Control", "no-store"),
    ("Referrer-Policy", "no-referrer"),
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
)


def _text(status: int, message: str, headers: tuple[tuple[str, str], ...] = ()) -> UIResponse:
    return UIResponse(status=status, body=message.encode("utf-8"), headers=headers)


_FORBIDDEN = "403 Forbidden"
_UNAUTHORIZED = "401 Unauthorized: open the access URL printed when the UI started."


def _equal(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def _cookie(headers: Mapping[str, str], name: str) -> str | None:
    for part in headers.get("cookie", "").split(";"):
        key, sep, value = part.strip().partition("=")
        if sep and key == name:
            return value
    return None


class ConfigUI:
    """The socket-free request core of the configuration UI (R1, D2).

    :meth:`handle` runs the guards in a fixed order — ``Host``, session,
    method, ``Origin``, CSRF — before any route handler, so a handler only
    ever sees an authenticated request from an accepted client. ``handle``
    may be reached from several ``config-ui`` worker threads (D8): the
    session table has its own lock, and state-changing handlers run under one
    mutation lock.
    """

    def __init__(
        self,
        settings: UISettings,
        *,
        environ: Mapping[str, str] | None = None,
        checker: Checker | None = None,
        probe: Probe | None = None,
        supervisor: Supervisor | None = None,
    ) -> None:
        self.settings = settings
        #: The UI process's own environment: "set/unset" is judged here, the
        #: environment a UI-launched main process inherits (A3).
        self.environ: Mapping[str, str] = os.environ if environ is None else environ
        #: The secret set of the last configuration snapshot (R8).
        self.secret_values: frozenset[str] = frozenset()
        self._secrets_lock = threading.Lock()
        self.token = secrets.token_urlsafe(32)
        #: Keys the opaque field names of :func:`_field_name` (R8).
        self.field_key = secrets.token_bytes(32)
        self.authorities = accepted_authorities(
            settings.host, settings.port, settings.allowed_hosts
        )
        self.base_path = settings.base_path
        self.overlay_path = settings.overlay_path
        #: The one file a Save or Remove may replace, resolved once here (R6).
        self._managed_overlay = settings.overlay_path
        self.status_path = settings.status_path
        self._sessions: OrderedDict[str, _Session] = OrderedDict()
        self._sessions_lock = threading.Lock()
        self._mutation_lock = threading.Lock()
        #: Runs the settings phase of Check; the seam exists for unit tests,
        #: production always uses :func:`_default_checker` (R5).
        self.checker: Checker = _default_checker if checker is None else checker
        #: The verdict of the last Check, shown as readiness (R4).
        self.last_check: CheckResult | None = None
        #: The liveness probe of a status record's process; a seam for tests,
        #: production always uses :func:`process_alive` (R7).
        self.probe: Probe = process_alive if probe is None else probe
        #: Stops and starts the main process this UI launched (R7, A4).
        self.supervisor = (
            Supervisor(default_launch_argv(settings), self.status_path)
            if supervisor is None
            else supervisor
        )
        #: The last Apply's report, shown on the pages (R7).
        self.last_apply: ApplyReport | None = None
        _LOG_REDACTION.sources.add(self)
        self.routes: dict[str, tuple[frozenset[str], Handler]] = {
            "/": (frozenset({"GET"}), self._base_page),
            "/core": (frozenset({"GET"}), self._core_page),
            MODULE_PAGE_PREFIX: (frozenset({"GET"}), self._module_page),
            "/save": (frozenset({"POST"}), self._save_endpoint),
            "/remove": (frozenset({"POST"}), self._remove_endpoint),
            "/check": (frozenset({"POST"}), self._check_endpoint),
            "/apply": (frozenset({"POST"}), self._apply_endpoint),
        }

    def __repr__(self) -> str:
        return f"ConfigUI(authority={self.access_authority!r})"

    @property
    def child(self) -> ChildHandle | None:
        """The main process this UI launched, if any: the only one it
        supervises (A4); a record naming another pid is "not supervised"."""

        return self.supervisor.child  # type: ignore[no-any-return]

    @child.setter
    def child(self, child: ChildHandle | None) -> None:
        self.supervisor.child = child

    def view(self) -> ConfigView:
        """Load a fresh configuration snapshot and remember its secret set."""

        view = ConfigView.load(self.settings, self.environ)
        # A union, so a snapshot loaded concurrently never drops a value an
        # earlier one still rendered around (R8).
        with self._secrets_lock:
            self.secret_values = self.secret_values | view.secret_values
        return view

    @property
    def access_authority(self) -> str:
        return access_authority(self.settings.host, self.settings.port)

    @property
    def access_url(self) -> str:
        """The access URL; it carries the token, so it is printed once, never logged."""

        return f"http://{self.access_authority}/?token={self.token}"

    # -- sessions -----------------------------------------------------------

    def _new_session(self) -> str:
        session_id = secrets.token_urlsafe(32)
        with self._sessions_lock:
            self._sessions[session_id] = _Session(csrf_token=secrets.token_urlsafe(32))
            while len(self._sessions) > MAX_SESSIONS:
                self._sessions.popitem(last=False)
        return session_id

    def _session(self, request: UIRequest) -> _Session | None:
        presented = _cookie(request.headers, SESSION_COOKIE)
        if not presented:
            return None
        with self._sessions_lock:
            for session_id, session in self._sessions.items():
                if _equal(session_id, presented):
                    return session
        return None

    def _csrf(self, request: UIRequest) -> str | None:
        header = request.headers.get(CSRF_HEADER)
        if header is not None:
            return header
        content_type = request.headers.get("content-type", "")
        if content_type.split(";")[0].strip().lower() == "application/x-www-form-urlencoded":
            try:
                fields = parse_qsl(request.body.decode("utf-8"), keep_blank_values=True)
            except UnicodeDecodeError:
                return None
            for name, value in fields:
                if name == CSRF_FIELD:
                    return value
        return None

    # -- the guard chain ----------------------------------------------------

    def handle(self, request: UIRequest) -> UIResponse:
        """Answer one request; the guards run before any route handler.

        Every configured value rendered for it passes the redaction guard
        (R8): :func:`esc` redacts this UI's secret set.
        """

        token = _RENDERING_FOR.set(self)
        try:
            response = self._guarded(request)
        finally:
            _RENDERING_FOR.reset(token)
        return UIResponse(
            status=response.status,
            body=response.body,
            content_type=response.content_type,
            headers=response.headers + _SECURITY_HEADERS,
        )

    def _guarded(self, request: UIRequest) -> UIResponse:
        method = request.method.upper()
        host = request.headers.get("host")
        if host is None or _normalize_authority(host) not in self.authorities:
            return _text(403, _FORBIDDEN)
        if method == "GET" and request.path == "/" and "token" in request.query:
            if not _equal(request.query["token"], self.token):
                return _text(401, _UNAUTHORIZED)
            session_id = self._new_session()
            return UIResponse(
                status=303,
                headers=(
                    ("Location", "/"),
                    (
                        "Set-Cookie",
                        f"{SESSION_COOKIE}={session_id}; HttpOnly; SameSite=Strict; Path=/",
                    ),
                ),
            )
        session = self._session(request)
        if session is None:
            return _text(401, _UNAUTHORIZED)
        if request.path in STATE_CHANGING_PATHS and method != "POST":
            return _text(405, "405 Method Not Allowed", (("Allow", "POST"),))
        if method == "POST":
            origin = request.headers.get("origin")
            if origin is not None and _origin_authority(origin) not in self.authorities:
                return _text(403, _FORBIDDEN)
            presented = self._csrf(request)
            if presented is None or not _equal(presented, session.csrf_token):
                return _text(403, _FORBIDDEN)
        route = self.routes.get(request.path)
        if route is None and request.path.startswith(MODULE_PAGE_PREFIX):
            route = self.routes.get(MODULE_PAGE_PREFIX)
        if route is None:
            return _text(404, "404 Not Found")
        methods, handler = route
        if method not in methods:
            return _text(405, "405 Method Not Allowed", (("Allow", ", ".join(sorted(methods))),))
        if request.path in STATE_CHANGING_PATHS:
            with self._mutation_lock:
                return handler(request, session)
        return handler(request, session)

    # -- the status record (R7, R4) --------------------------------------------

    def running_state(self) -> RunningState:
        """Read the one watched status path and judge liveness and drift (R7).

        Only :attr:`status_path` is ever read (A5). Drift is ``unknown`` for
        an absent, unusable or dead record, else the record's digest compared
        with the on-disk digest recomputed from the base and overlay files.
        """

        reading = read_status(self.status_path)
        record = reading.record
        if record is None:
            return RunningState(reading, alive=False, drift=DRIFT_UNKNOWN, supervised=False)
        child = self.child
        supervised = child is not None and child.pid == record.pid
        alive = self.probe(record.pid, child)
        drift = DRIFT_UNKNOWN
        if alive:
            try:
                on_disk = on_disk_digest(self.base_path, self.overlay_path)
            except OverlayError:
                on_disk = None
            if on_disk is not None:
                drift = DRIFT_IN_SYNC if record.digest == on_disk else DRIFT_DIFFERS
        return RunningState(reading, alive=alive, drift=drift, supervised=supervised)

    @staticmethod
    def _running_section(running: RunningState) -> str:
        """The record status, the drift state and the supervision label (R7)."""

        return (
            '<section id="running"><h2>Running process</h2><table>'
            f'<tr><th>Status record</th><td id="record-status" data-record="{running.reading.kind}">'
            f"{esc(running.record_text())}</td></tr>"
            f'<tr><th>Disk and running process</th><td id="drift" data-drift="{running.drift}">'
            f"{running.drift}</td></tr>"
            f'<tr><th>Supervision</th><td id="supervision">{esc(running.supervision_text())}</td></tr>'
            "</table></section>"
        )

    def _readiness_markup(self, name: str, running: RunningState) -> str:
        """The last Check verdict plus module *name*'s running state (R4)."""

        state, text = running.module_text(name)
        attribute = "" if state is None else f' data-running="{state}"'
        return (
            f'<span class="check">{esc(self._readiness(name))}</span><br>'
            f'<span class="running"{attribute}>{esc(text)}</span>'
        )

    # -- route handlers (placeholders replaced by later steps) --------------

    def _readiness(self, name: str) -> str:
        """The readiness cell of module *name*: the last Check verdict (R4)."""

        result = self.last_check
        if result is None:
            return "not checked yet"
        if result.passed:
            return "last Check passed"
        if result.for_module(name):
            return "last Check failed"
        if not result.settings_phase_reached:
            return f"last Check failed ({CHECK_NOT_REACHED})"
        return "last Check failed (no diagnostic names this module)"

    def _base_page(self, request: UIRequest, session: _Session) -> UIResponse:
        """Every discovered module and the configuration's origin (R4a)."""

        view = self.view()
        running = self.running_state()
        enabled = view.enabled_modules()
        rows: list[str] = []
        for name in sorted(view.modules):
            is_enabled = name in enabled
            state = "enabled" if is_enabled else "disabled"
            rows.append(
                f'<tr data-module="{ident(name)}" data-enabled="{str(is_enabled).lower()}">'
                f'<td><a href="{MODULE_PAGE_PREFIX}{ident(name)}">{ident(name)}</a></td>'
                f"<td>{state}"
                f'<form class="inline" method="post" action="/save">{_write_guard(session, view)}'
                f'{_page_field("/")}<input type="hidden" name="enabled_modules.{ident(name)}" '
                f'value="{str(not is_enabled).lower()}">'
                f'<button type="submit">{"Disable" if is_enabled else "Enable"}</button>'
                "</form></td>"
                f'<td class="readiness">{self._readiness_markup(name, running)}</td></tr>'
            )
        missing = [name for name in enabled if name not in view.modules]
        missing_note = (
            "<p class=\"diagnostics\">Enabled but not discovered: "
            + ", ".join(f"<code>{esc(name)}</code>" for name in missing)
            + "</p>"
            if missing
            else ""
        )
        variables = "".join(
            f'<tr data-variable="{ident(name)}" data-state="{_state(is_set)}">'
            f"<td><code>${{{ident(name)}}}</code></td><td>{_state(is_set)}</td></tr>"
            for name, is_set in view.references.items()
        )
        overlay_state = "exists" if view.overlay_exists else "absent"
        overlay_text = "(none)" if view.overlay_path is None else str(view.overlay_path)
        body = (
            "<h1>Configuration</h1>"
            f'<p class="local-only" id="local-only">{esc(LOCAL_ONLY_STATEMENT)}</p>'
            f"{self._running_section(running)}"
            f"{_diagnostics(view)}"
            '<section id="origin"><h2>Configuration origin</h2><table>'
            f'<tr><th>Base file</th><td id="base-path"><code>{esc(str(view.base_path))}</code></td></tr>'
            f'<tr><th>Overlay file</th><td id="overlay-path"><code>{esc(overlay_text)}</code> '
            f'<span id="overlay-state">({overlay_state})</span></td></tr>'
            "</table><h3>Referenced environment variables</h3>"
            f'<table id="variables"><tr><th>Variable</th><th>State</th></tr>{variables}</table>'
            "</section>"
            '<section id="modules"><h2>Modules</h2>'
            "<table><tr><th>Module</th><th>Enabled</th><th>Readiness</th></tr>"
            f"{''.join(rows)}</table>{missing_note}</section>"
            '<section id="actions-bar">'
            '<p><a href="/core">Core settings</a></p>'
            f'<form method="post" action="/check">{_csrf_field(session)}{_page_field("/")}'
            '<button type="submit">Check</button></form>'
            f'<form method="post" action="/apply">{_csrf_field(session)}{_page_field("/")}'
            '<button type="submit">Apply (restart the main process)</button></form></section>'
        )
        return _page("Configuration", session, body)

    def _core_page(self, request: UIRequest, session: _Session) -> UIResponse:
        """``modules_directory``, ``limits`` and, read-only, ``secrets`` and
        ``actions`` (R4c, A2, A9)."""

        view = self.view()
        directory = view.display(("modules_directory",)) or ""
        body = [
            "<h1>Core settings</h1>",
            '<p><a href="/">Back to the configuration</a></p>',
            self._running_section(self.running_state()),
            _diagnostics(view),
            '<section id="modules-directory"><h2>Modules directory</h2>'
            f'<form method="post" action="/save">{_write_guard(session, view)}{_page_field(CORE_PAGE)}'
            '<label>modules_directory '
            f'<input type="text" name="modules_directory" value="{esc(directory)}"></label> '
            f'<span class="origin">origin: {esc(self._origin(view, ("modules_directory",)))}</span> '
            f'<button type="submit">Save</button> {_CHECK_BUTTON}</form></section>',
            self._limits_section(view, session),
            self._secrets_section(view),
            self._actions_section(view),
        ]
        return _page("Core settings", session, "".join(body))

    @staticmethod
    def _origin(view: ConfigView, path: Sequence[Any]) -> str:
        """The origin shown for *path*; a ``${NAME}`` value is a reference."""

        origin = view.origin(path)
        if reference_name(view.value(path)) is not None:
            return f"{ORIGIN_REFERENCE} ({origin})"
        return origin

    def _limits_section(self, view: ConfigView, session: _Session) -> str:
        groups: list[str] = []
        for group, fields in LIMIT_DECLARATION.items():
            rows: list[str] = []
            for field_name, kind in fields.items():
                path = ("limits", group, field_name)
                shown = view.display(path)
                reference = reference_name(view.value(path)) is not None
                if reference or _guarded(shown or "") != (shown or ""):
                    # A number input would post a redacted value as "".
                    control = 'type="text"'
                elif kind == LIMIT_KIND_COUNT:
                    control = 'type="number" min="1" step="1"'
                else:
                    control = 'type="number" min="0" step="any"'
                rows.append(
                    f'<tr data-limit="{ident(group)}.{ident(field_name)}">'
                    f"<td><label>{ident(field_name)}</label></td>"
                    f'<td class="kind">{ident(kind)}</td>'
                    f'<td><input {control} name="{ident(_field_name(path, view))}" '
                    f'value="{esc(shown or "")}"></td>'
                    f'<td class="origin">{esc(self._origin(view, path))}</td></tr>'
                )
            groups.append(
                f'<fieldset data-limit-group="{ident(group)}"><legend>{ident(group)}</legend>'
                "<table><tr><th>Field</th><th>Kind</th><th>Value</th><th>Origin</th></tr>"
                f"{''.join(rows)}</table></fieldset>"
            )
        return (
            '<section id="limits"><h2>Limits</h2>'
            f'<form method="post" action="/save">{_write_guard(session, view)}{_page_field(CORE_PAGE)}'
            f"{''.join(groups)}"
            f'<button type="submit">Save</button> {_CHECK_BUTTON}</form></section>'
        )

    def _secrets_section(self, view: ConfigView) -> str:
        listed = view.value((SECRETS_KEY,))
        entries = listed if isinstance(listed, list) else []
        items: list[str] = []
        for entry in entries:
            name = reference_name(entry)
            if name is None:
                items.append(f"<li>{esc(HIDDEN_LITERAL)}</li>")
            else:
                is_set = view.references.get(name, name in self.environ)
                items.append(
                    f'<li data-secret="{ident(name)}"><code>${{{ident(name)}}}</code> '
                    f"({_state(is_set)})</li>"
                )
        content = f"<ul>{''.join(items)}</ul>" if items else "<p>No entry.</p>"
        return (
            '<section id="secrets" class="read-only"><h2>Secrets</h2>'
            f'<p class="reason">{esc(READ_ONLY_REASON)}</p>'
            f'<p class="origin">origin: {esc(view.origin((SECRETS_KEY,)))}</p>'
            f"{content}</section>"
        )

    def _actions_section(self, view: ConfigView) -> str:
        rules = view.rules()
        origin = view.origin((ACTIONS_KEY,))
        rows: list[str] = []
        for position, rule in enumerate(rules):
            # The row is named by its position: a rule id is configured text,
            # shown in its cell, never an identifier (gate-2 F2).
            if not isinstance(rule, Mapping):
                rows.append(
                    f'<tr data-rule="{position}" data-origin="{esc(origin)}">'
                    '<td colspan="7">invalid rule</td></tr>'
                )
                continue
            rule_id = rule.get("rule_id", "")
            action = rule.get("action_name", ANY_ACTION)
            action_text = "any" if action in (ANY_ACTION, None) else _plain(action)
            rows.append(
                f'<tr data-rule="{position}" data-origin="{esc(origin)}">'
                f"<td><code>{esc(_plain(rule_id))}</code></td>"
                f"<td>{esc(action_text)}</td>"
                f"<td>{_listing(rule.get('destination'))}</td>"
                f"<td>{_listing(rule.get('principals'))}</td>"
                f"<td>{_listing(rule.get('natures'))}</td>"
                f"<td>{_listing(rule.get('granted_permissions'))}</td>"
                f'<td class="origin">{esc(origin)}</td></tr>'
            )
        uncovered = view.uncovered_actions()
        uncovered_items = "".join(
            f'<li data-uncovered="{esc(action)}"><code>{esc(action)}</code> '
            f"(declared by {esc(module)})</li>"
            for action, module in uncovered
        )
        uncovered_list = (
            f'<ul id="uncovered">{uncovered_items}</ul>'
            if uncovered
            else '<p id="uncovered">Every declared action is covered by a rule.</p>'
        )
        return (
            '<section id="actions" class="read-only"><h2>Action authorization rules</h2>'
            f'<p class="reason">{esc(READ_ONLY_REASON)}</p>'
            f'<p>Rules: <span id="rule-count" data-rule-count="{len(rules)}">{len(rules)}</span> '
            f'<span class="origin">(origin: {esc(origin)})</span></p>'
            "<table><tr><th>rule_id</th><th>action_name</th><th>destination</th>"
            "<th>principals</th><th>natures</th><th>granted permissions</th><th>origin</th></tr>"
            f"{''.join(rows)}</table>"
            "<h3>Actions not covered by any rule</h3>"
            f"{uncovered_list}</section>"
        )

    def _module_page(self, request: UIRequest, session: _Session) -> UIResponse:
        """A discovered module's page, generated from its manifest (R4b, A2, A7).

        The settings form renders ``settings_schema`` through
        :class:`_SchemaRenderer`; the trigger section, present only when the
        manifest declares trigger types, renders each configured channel
        policy with the same renderer over each type's ``parameter_schema``.
        """

        name = request.path[len(MODULE_PAGE_PREFIX) :]
        view = self.view()
        module = view.modules.get(name)
        if module is None:
            return _text(404, "404 Not Found")
        renderer = _SchemaRenderer(view)
        fields = self._module_fields(renderer, name, module)
        page = f"{MODULE_PAGE_PREFIX}{name}"
        form_id = "settings-form"
        running = self.running_state()
        body = [
            f"<h1>Module {ident(name)}</h1>",
            '<p><a href="/">Back to the configuration</a></p>',
            self._running_section(running),
            '<section id="readiness"><h2>Readiness</h2>'
            f"<p>{self._readiness_markup(name, running)}</p></section>",
            _diagnostics(view),
            f'<section id="settings"><h2>Settings</h2>'
            f'<form id="{form_id}" method="post" action="/save">{_write_guard(session, view)}'
            f"{_page_field(page)}{fields}"
            f'<button type="submit">Save</button> {_CHECK_BUTTON}</form></section>',
        ]
        declared = _trigger_types(module.manifest)
        if declared:
            body.append(self._trigger_section(view, renderer, session, name, module.manifest))
        return _page(f"Module {name}", session, "".join(body))

    @staticmethod
    def _module_fields(renderer: _SchemaRenderer, name: str, module: DiscoveredModule) -> str:
        """The settings controls of module *name*, from its ``settings_schema``."""

        root = ("modules", name)
        schema = module.manifest.get(MANIFEST_SETTINGS_SCHEMA_KEY)
        if isinstance(schema, Mapping) and _schema_kind(schema) == "object" and (
            isinstance(schema.get("properties"), Mapping)
            or isinstance(schema.get("additionalProperties"), Mapping)
        ):
            fields = renderer.children(schema, root)
        elif isinstance(schema, Mapping):
            # A root the renderer cannot expand still names the settings
            # "not editable here", with their configured text read-only.
            reason = (
                "an object without properties"
                if _schema_kind(schema) == "object"
                else "a settings_schema that is not an object"
            )
            fields = renderer.notice(root, reason, schema)
        else:
            fields = renderer.notice(root, "the manifest declares no settings_schema")
        return fields

    def _trigger_section(
        self,
        view: ConfigView,
        renderer: _SchemaRenderer,
        session: _Session,
        name: str,
        manifest: Mapping[str, Any],
    ) -> str:
        """Each configured channel policy of ``triggers.<name>`` and an add form."""

        types = _trigger_types(manifest)
        declared = manifest.get(MANIFEST_TRIGGERS_KEY)
        combinations = [
            item
            for item in (declared.get("combinations", []) if isinstance(declared, Mapping) else [])
            if isinstance(item, str)
        ]
        channels_path = ("triggers", name, "channels")
        configured = view.value(channels_path)
        channels = configured if isinstance(configured, Mapping) else {}
        policies: list[str] = []
        for position, (key, policy) in enumerate(channels.items()):
            path = (*channels_path, key)
            label = _key_label(key, view)
            parts = [
                f'<fieldset class="channel" data-channel="{_key_token(position)}">'
                f"<legend>Channel {label}</legend>",
                f'<form method="post" action="/save">{_write_guard(session, view)}'
                f"{_page_field(MODULE_PAGE_PREFIX + name)}",
            ]
            if not isinstance(policy, Mapping):
                parts.append(renderer.notice(path, "the channel policy is not a mapping"))
            else:
                combination = policy.get("combination", "all_of")
                renderer.register(
                    (*path, "combination"),
                    "enum",
                    _plain(combination if combination in combinations else (combinations or [""])[0]),
                    {"enum": combinations},
                )
                options = "".join(
                    f'<option value="{ident(item)}"'
                    f'{" selected" if item == combination else ""}>{ident(item)}</option>'
                    for item in combinations
                )
                parts.append(
                    f'<p><label>Combination <select name="{ident(_field_name((*path, "combination"), view))}" '
                    f'data-combination="{esc(_plain(combination))}">{options}</select></label> '
                    f'<span class="origin">{esc(renderer.origin((*path, "combination")))}</span></p>'
                )
                rules = policy.get("rules")
                for index, rule in enumerate(rules if isinstance(rules, list) else []):
                    rule_path = (*path, "rules", index)
                    rule_type = rule.get("type") if isinstance(rule, Mapping) else None
                    schema = types.get(rule_type) if isinstance(rule_type, str) else None
                    if schema is None:
                        parts.append(renderer.notice(rule_path, "the rule type is not declared"))
                        continue
                    parts.append(
                        f'<fieldset class="rule" data-rule-type="{ident(rule_type)}">'
                        f"<legend>Rule {index + 1}: {ident(rule_type)}</legend>"
                        f"{renderer.children(schema, (*rule_path, 'parameters'))}</fieldset>"
                    )
            parts.append(f'<button type="submit">Save</button> {_CHECK_BUTTON}')
            if _lookup(view.base, path) is not _MISSING:
                parts.append(f'<p class="note">{esc(BASE_ENTRY_NOTE)}</p>')
            else:
                parts.append(
                    f'<button type="submit" formaction="/remove" name="path" '
                    f'value="{ident(_field_name(path, view))}">Delete this channel policy</button>'
                )
            parts.append("</form></fieldset>")
            policies.append("".join(parts))
        type_options = "".join(
            f'<option value="{ident(item)}">{ident(item)}</option>' for item in types
        )
        combination_options = "".join(
            f'<option value="{ident(item)}">{ident(item)}</option>' for item in combinations
        )
        add = (
            '<form id="add-channel-policy" method="post" action="/save">'
            f"{_write_guard(session, view)}{_page_field(MODULE_PAGE_PREFIX + name)}<h3>Add channel policy</h3>"
            f'<input type="hidden" name="add_channel_policy" value="{ident(_field_name(channels_path, view))}">'
            '<label>Channel <input type="text" name="channel"></label> '
            f'<label>Combination <select name="combination">{combination_options}</select></label> '
            f'<label>Rule type <select name="rule_type">{type_options}</select></label> '
            '<label>Rule parameters (JSON) <textarea name="parameters" rows="2">{}</textarea></label> '
            '<button type="submit">Add</button></form>'
        )
        content = "".join(policies) or "<p>No channel policy is configured: every channel uses the module default.</p>"
        return f'<section id="triggers"><h2>Trigger policies</h2>{content}{add}</section>'

    # -- Apply (R7, A4) --------------------------------------------------------

    def apply(self) -> ApplyReport:
        """Check the on-disk configuration, then restart the supervised child (R7).

        A failing Check refuses with its diagnostics before anything is
        stopped or started. Only the child this UI launched is ever
        signalled (A4). The report is remembered for the pages and logged
        with the paths and the outcome only.
        """

        result = self.check()
        if not result.passed:
            report = ApplyReport(APPLY_REFUSED, diagnostics=result.diagnostics, check_refused=True)
        else:
            try:
                digest = on_disk_digest(self.base_path, self.overlay_path)
            except OverlayError as exc:
                report = ApplyReport(
                    APPLY_REFUSED,
                    diagnostics=(_redact(str(exc), self.secret_values),),
                    check_refused=True,
                )
            else:
                report = self.supervisor.restart(digest, self.secret_values)
        self.last_apply = report
        logger.info(
            "configuration apply %s: base %s, overlay %s, status file %s",
            report.outcome,
            os.fspath(self.base_path),
            "(none)" if self.overlay_path is None else os.fspath(self.overlay_path),
            os.fspath(self.status_path),
            extra={"config_ui_operation": "apply", "config_ui_outcome": report.outcome},
        )
        return report

    def _apply_endpoint(self, request: UIRequest, session: _Session) -> UIResponse:
        """``POST /apply``: the supervised restart and its report (R7)."""

        fields = _form_fields(request)
        page = self._posted_page(fields, self.view())
        report = self.apply()
        titles = {
            APPLY_ACCEPTED: "Apply accepted",
            APPLY_REFUSED: "Apply refused",
            APPLY_UNKNOWN: "Apply outcome unknown",
        }
        parts = [
            f'<h1 id="apply-outcome" data-outcome="{report.outcome}">{titles[report.outcome]}</h1>',
            f'<p><a href="{ident(page)}">Back</a></p>',
            self._running_section(self.running_state()),
        ]
        if report.check_refused:
            parts.append("<p>The on-disk Check failed: nothing was stopped or started.</p>")
        if report.diagnostics:
            items = "".join(f'<li class="diagnostic">{esc(line)}</li>' for line in report.diagnostics)
            parts.append(f'<section class="diagnostics" id="apply-diagnostics"><ul>{items}</ul></section>')
        if report.exit_status is not None:
            parts.append(f'<p id="exit-status" data-status="{report.exit_status}">'
                         f"The process exited with status {report.exit_status}.</p>")
            parts.append(f'<pre id="stderr-tail">{esc(report.stderr_tail)}</pre>')
        if report.outcome == APPLY_UNKNOWN:
            parts.append(
                f"<p>No status record of the new process reported ready with the on-disk "
                f"configuration within {self.supervisor.apply_window:g} s.</p>"
            )
        return _page(titles[report.outcome], session, "".join(parts))

    # -- Check (R5) -----------------------------------------------------------

    def _controls(self, view: ConfigView) -> _SchemaRenderer:
        """Every control the pages render over *view*, by field name.

        The module pages are rendered (their HTML discarded) through the same
        renderer, so a posted field is read back exactly as its page drew it.
        """

        renderer = _SchemaRenderer(view)
        placeholder = _Session(csrf_token="")
        for name, module in view.modules.items():
            self._module_fields(renderer, name, module)
            if _trigger_types(module.manifest):
                self._trigger_section(view, renderer, placeholder, name, module.manifest)
        directory = ("modules_directory",)
        renderer.register(directory, "string", view.display(directory) or "")
        for group, fields in LIMIT_DECLARATION.items():
            for field_name, kind in fields.items():
                path = ("limits", group, field_name)
                renderer.register(
                    path,
                    "integer" if kind == LIMIT_KIND_COUNT else "number",
                    view.display(path) or "",
                )
        return renderer

    def parse_edits(
        self, view: ConfigView, fields: Sequence[tuple[str, str]]
    ) -> tuple[list[Edit], list[str]]:
        """The draft edits a posted form makes, and the fields it cannot read.

        A field left as its page rendered it is no edit. A trigger-policy
        field is lifted to its whole channel policy (the unit a policy is
        written in, R6); a base-page toggle ``enabled_modules.<name>`` edits
        the whole ``enabled_modules`` list; the add-channel-policy form makes
        a new one-rule channel policy.
        """

        renderer = self._controls(view)
        posted: dict[str, str] = {}
        for name, value in fields:
            posted[name] = value  # the last value wins (a checkbox after its hidden twin)
        edits: dict[tuple[Any, ...], Any] = {}
        problems: list[str] = []
        enabled: list[str] | None = None
        policies: dict[tuple[Any, ...], Any] = {}
        if _ADD_CHANNEL_POLICY in posted:
            self._added_policy(view, posted, policies, problems)
        for name, text in posted.items():
            if name in _NOT_SETTING_FIELDS:
                continue
            if name.startswith(_ENABLED_TOGGLE):
                module = name[len(_ENABLED_TOGGLE) :]
                if module not in view.modules or text not in ("true", "false"):
                    problems.append(f"{name}: is not a module toggle")
                    continue
                enabled = list(view.enabled_modules()) if enabled is None else enabled
                if text == "true" and module not in enabled:
                    enabled.append(module)
                elif text == "false":
                    enabled = [item for item in enabled if item != module]
                continue
            if name.startswith(_NEW_ENTRY_NAME):
                mapping = renderer.entry_mappings.get(name[len(_NEW_ENTRY_NAME) :])
                if mapping is None:
                    problems.append(f"{name}: is not a setting this UI edits")
                elif text:
                    raw = posted.get(_NEW_ENTRY_VALUE + name[len(_NEW_ENTRY_NAME) :], "")
                    _draft_edit(edits, policies, view, (*mapping, text), _coerce("json", {}, raw))
                continue
            if name.startswith(_NEW_ENTRY_VALUE):
                if name[len(_NEW_ENTRY_VALUE) :] not in renderer.entry_mappings:
                    problems.append(f"{name}: is not a setting this UI edits")
                continue
            control = renderer.controls.get(name)
            if control is None:
                problems.append(f"{name}: is not a setting this UI edits")
                continue
            if text == control.rendered:
                continue
            value = _coerce(control.kind, control.schema, text)
            _draft_edit(edits, policies, view, control.path, value)
        ordered: list[Edit] = []
        if enabled is not None:
            ordered.append((("enabled_modules",), enabled))
        ordered.extend(edits.items())
        ordered.extend(policies.items())
        return ordered, problems

    @staticmethod
    def _added_policy(
        view: ConfigView,
        posted: Mapping[str, str],
        policies: dict[tuple[Any, ...], Any],
        problems: list[str],
    ) -> None:
        """The channel policy the add-channel-policy form posts, as one edit."""

        target = posted[_ADD_CHANNEL_POLICY]
        inputs = [
            name
            for name, module in view.modules.items()
            if _trigger_types(module.manifest)
            and _names_path(target, ("triggers", name, "channels"), view)
        ]
        if len(inputs) != 1:
            problems.append(f"{_ADD_CHANNEL_POLICY}: is not a trigger input this UI edits")
            return
        channels = ("triggers", inputs[0], "channels")
        channel = posted.get("channel", "").strip()
        if not channel:
            problems.append(f"{_path_text(channels)}: the channel must be a non-empty identifier")
            return
        path = (*channels, channel)
        if view.value(path) is not _MISSING:
            problems.append(f"{_path_text(path)}: is already configured; edit it instead")
            return
        policies[path] = {
            "combination": posted.get("combination", ""),
            "rules": [
                {
                    "type": posted.get("rule_type", ""),
                    "parameters": _coerce("json", {}, posted.get("parameters", "") or "{}"),
                }
            ],
        }

    def check(
        self,
        edits: Sequence[Edit] = (),
        *,
        problems: Sequence[str] = (),
        view: ConfigView | None = None,
    ) -> CheckResult:
        """Validate the draft: the on-disk overlay with *edits* applied (R5).

        Nothing is written, nothing is signalled or started, and no socket is
        created. First every unresolved ``${NAME}`` of the draft is reported
        by setting path and variable name; only when there is none does the
        settings phase run, through ``core.main.check_config`` on the draft.
        The verdict is remembered for readiness (R4); every diagnostic has
        passed the redaction guard (R8).
        """

        view = self.view() if view is None else view
        secret_values = set(self.secret_values)
        try:
            base = read_base(self.base_path)
            overlay = read_overlay(self.overlay_path)
            draft = _apply_edits(overlay, edits)
        except (OverlayError, ValueError) as exc:
            result = CheckResult(False, (*problems, str(exc)), settings_phase_reached=False)
        else:
            merged = deep_merge(base, draft)
            secret_values.update(_credential_literals(view, draft))
            unresolved = _unresolved_references(merged, self.environ)
            if unresolved:
                result = CheckResult(
                    False, (*problems, *unresolved), settings_phase_reached=False
                )
            else:
                passed, diagnostics = self.checker(
                    self.base_path, self.environ, self.overlay_path, draft
                )
                result = CheckResult(
                    passed and not problems,
                    (*problems, *diagnostics),
                    settings_phase_reached=True,
                )
        result = CheckResult(
            result.passed,
            tuple(_redact(line, secret_values) for line in result.diagnostics),
            result.settings_phase_reached,
        )
        self.last_check = result
        return result

    def _check_endpoint(self, request: UIRequest, session: _Session) -> UIResponse:
        """``POST /check``: Check the posting page's draft and report (R5).

        A module page lists the diagnostics naming that module; the base and
        core-settings pages list all of them.
        """

        fields = _form_fields(request)
        view = self.view()
        page = "/"
        for name, value in fields:
            if name == PAGE_FIELD:
                page = value
        module: str | None = None
        if page.startswith(MODULE_PAGE_PREFIX) and page[len(MODULE_PAGE_PREFIX) :] in view.modules:
            module = page[len(MODULE_PAGE_PREFIX) :]
        elif page != CORE_PAGE:
            page = "/"
        edits, problems = self.parse_edits(view, fields)
        result = self.check(edits, problems=problems, view=view)
        shown = result.diagnostics if module is None else result.for_module(module)
        others = len(result.diagnostics) - len(shown)
        verdict = "Check passed" if result.passed else "Check failed"
        items = "".join(f'<li class="diagnostic">{esc(line)}</li>' for line in shown)
        parts = [
            f'<h1 id="verdict" data-passed="{str(result.passed).lower()}">{verdict}</h1>',
            f'<p><a href="{ident(page)}">Back</a> (the edits are not saved)</p>',
        ]
        if not result.settings_phase_reached:
            parts.append(f'<p id="settings-phase">{CHECK_NOT_REACHED}</p>')
        if shown:
            parts.append(f'<section class="diagnostics" id="check-diagnostics"><ul>{items}</ul></section>')
        if others:
            parts.append(
                f'<p id="other-diagnostics">{others} other diagnostic(s) name no setting of '
                "this module; the base page lists all of them.</p>"
            )
        return _page(verdict, session, "".join(parts))

    # -- Save and remove-override (R6, A7, A9) ----------------------------------

    def save(
        self,
        edits: Sequence[Edit],
        *,
        fingerprint: str,
        page: str = "/",
        problems: Sequence[str] = (),
        view: ConfigView | None = None,
    ) -> WriteResult:
        """Write *edits* to the overlay, or refuse them all and write nothing.

        The order is fixed: stale detection, then the 6c scope and the
        protections of every edit (a refusal), then the value checks (the
        per-field diagnostics). Only when all pass is the overlay replaced,
        atomically, with the on-disk overlay plus the edits — through
        :func:`_apply_edits`, the draft Check validates.
        """

        prepared = self._prepare_write(fingerprint)
        if isinstance(prepared, WriteResult):
            return prepared
        base, overlay = prepared
        view = self.view() if view is None else view
        refused = list(problems)
        invalid: list[str] = []
        accepted: list[Edit] = []
        for path, value in edits:
            path = tuple(path)
            reason = _scope_refusal(view, path)
            if reason is not None:
                refused.append(f"{_path_text(path)}: {reason}")
                continue
            value, protection = _protect_save(view, path, value)
            if protection:
                refused.extend(protection)
                continue
            invalid.extend(_validate_edit(view, path, value))
            accepted.append((path, value))
        if refused:
            return self._refused(WriteResult(OUTCOME_REFUSED, tuple(refused)))
        if invalid:
            return self._refused(WriteResult(OUTCOME_INVALID, tuple(invalid)))
        try:
            draft = _apply_edits(overlay, accepted)
        except ValueError as exc:
            return self._refused(WriteResult(OUTCOME_INVALID, (str(exc),)))
        if draft == overlay:
            return WriteResult(OUTCOME_UNCHANGED)
        paths = tuple(_path_text(path) for path, _ in accepted)
        return self._commit(draft, OUTCOME_SAVED, page, paths, view)

    def remove(
        self,
        path_text: str,
        *,
        fingerprint: str,
        page: str = "/",
        view: ConfigView | None = None,
    ) -> WriteResult:
        """Delete one key path from the overlay, pruning emptied mappings (R6).

        The effective value becomes the base value again; a key the base
        defines stays defined (A7). Refused, writing nothing, outside the 6c
        scope, at a protected field, or when a protected descendant's
        effective text would change.
        """

        prepared = self._prepare_write(fingerprint)
        if isinstance(prepared, WriteResult):
            return prepared
        base, overlay = prepared
        view = self.view() if view is None else view
        candidates = [path for path in _overlay_key_paths(overlay) if _names_path(path_text, path, view)]
        if len(candidates) != 1:
            reason = "is not overridden in the overlay" if not candidates else "is ambiguous"
            return self._refused(WriteResult(OUTCOME_REFUSED, (f"{path_text}: {reason}",)))
        path = candidates[0]
        reason = _scope_refusal(view, path)
        if reason is not None:
            return self._refused(WriteResult(OUTCOME_REFUSED, (f"{path_text}: {reason}",)))
        draft = _remove_path(overlay, path)
        refusals = _protect_remove(view, path, deep_merge(base, draft))
        if refusals:
            return self._refused(WriteResult(OUTCOME_REFUSED, tuple(refusals)))
        return self._commit(draft, OUTCOME_REMOVED, page, (path_text,), view)

    def _prepare_write(
        self, fingerprint: str
    ) -> tuple[Mapping[str, Any], Mapping[str, Any]] | WriteResult:
        """Both files as parsed, or why no write may happen (stale included)."""

        if self.overlay_path is None:
            return WriteResult(OUTCOME_REFUSED, ("no managed overlay path is configured",))
        if self.overlay_path != self._managed_overlay or same_file(self.overlay_path, self.base_path):
            # The destination is never the base file, whatever its spelling
            # (gate-1 F1); startup refuses this, a UI built past it does too.
            return WriteResult(
                OUTCOME_REFUSED, ("the overlay path is the configuration file itself",)
            )
        current = _file_fingerprint(self.overlay_path)
        if current is None:
            return WriteResult(OUTCOME_FAILED, ("the overlay file is not readable",))
        if not fingerprint or not _equal(fingerprint, current):
            return WriteResult(
                OUTCOME_STALE,
                ("the overlay file changed on disk since this page was rendered; reload it",),
            )
        try:
            return read_base(self.base_path), read_overlay(self.overlay_path)
        except OverlayError as exc:
            return WriteResult(OUTCOME_REFUSED, (str(exc),))

    def _refused(self, result: WriteResult) -> WriteResult:
        """*result* with every diagnostic value-free and redacted (R8)."""

        return WriteResult(
            result.outcome,
            tuple(_redact(_value_free(line), self.secret_values) for line in result.diagnostics),
        )

    def _commit(
        self,
        draft: Mapping[str, Any],
        outcome: str,
        page: str,
        paths: Sequence[str],
        view: ConfigView,
    ) -> WriteResult:
        """Replace the overlay with *draft* and log the operation, never a value.

        A path can carry an operator-chosen key (a channel identifier); the
        logged page and paths, message and record fields alike, pass the
        redaction guard with the draft's credential literals included (R8).
        """

        try:
            _write_overlay(self.overlay_path, self._managed_overlay, self.base_path, draft)
        except (OSError, RuntimeError):
            return WriteResult(OUTCOME_FAILED, ("the overlay file could not be written",))
        secret_values = set(self.secret_values) | _credential_literals(view, draft)
        logged_page = _redact(page, secret_values)
        logged_paths = tuple(_redact(path, secret_values) for path in paths)
        logger.info(
            "configuration %s at %s: page %s, setting paths: %s",
            _OPERATION[outcome],
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
            logged_page,
            ", ".join(logged_paths),
            extra={
                "config_ui_operation": _OPERATION[outcome],
                "config_ui_page": logged_page,
                "config_ui_paths": logged_paths,
            },
        )
        return WriteResult(outcome, paths=tuple(paths))

    def _posted_page(self, fields: Sequence[tuple[str, str]], view: ConfigView) -> str:
        """The page a form was posted from: ``/``, ``/core`` or a module page."""

        page = "/"
        for name, value in fields:
            if name == PAGE_FIELD:
                page = value
        if page == CORE_PAGE:
            return page
        if page.startswith(MODULE_PAGE_PREFIX) and page[len(MODULE_PAGE_PREFIX) :] in view.modules:
            return page
        return "/"

    def _save_endpoint(self, request: UIRequest, session: _Session) -> UIResponse:
        """``POST /save``: write the posting page's edits to the overlay (R6)."""

        fields = _form_fields(request)
        view = self.view()
        page = self._posted_page(fields, view)
        stale = _stale_layout(fields, view)
        if stale is not None:
            return self._write_response(stale, page, session)
        edits, problems = self.parse_edits(view, fields)
        result = self.save(
            edits, fingerprint=_posted(fields, FINGERPRINT_FIELD), page=page, problems=problems, view=view
        )
        return self._write_response(result, page, session)

    def _remove_endpoint(self, request: UIRequest, session: _Session) -> UIResponse:
        """``POST /remove``: delete one override from the overlay (R6, A7)."""

        fields = _form_fields(request)
        view = self.view()
        page = self._posted_page(fields, view)
        stale = _stale_layout(fields, view)
        if stale is not None:
            return self._write_response(stale, page, session)
        result = self.remove(
            _posted(fields, "path"),
            fingerprint=_posted(fields, FINGERPRINT_FIELD),
            page=page,
            view=view,
        )
        return self._write_response(result, page, session)

    @staticmethod
    def _write_response(result: WriteResult, page: str, session: _Session) -> UIResponse:
        if result.accepted:
            return UIResponse(status=303, headers=(("Location", page),))
        titles = {
            OUTCOME_UNCHANGED: "Nothing to save",
            OUTCOME_STALE: "Refused: stale page",
            OUTCOME_REFUSED: "Refused",
            OUTCOME_INVALID: "Not saved: invalid values",
            OUTCOME_FAILED: "Not saved: write failed",
        }
        title = titles[result.outcome]
        items = "".join(f'<li class="diagnostic">{esc(line)}</li>' for line in result.diagnostics)
        body = (
            f'<h1 id="outcome" data-outcome="{ident(result.outcome)}">{esc(title)}</h1>'
            f'<p><a href="{ident(page)}">Back</a> (the overlay file is unchanged)</p>'
            + (f'<section class="diagnostics" id="write-diagnostics"><ul>{items}</ul></section>' if items else "")
        )
        response = _page(title, session, body)
        return UIResponse(
            status=_OUTCOME_STATUS[result.outcome],
            body=response.body,
            content_type=response.content_type,
        )


# ---------------------------------------------------------------------------
# Draft edits and Check (R5)
#
# A draft is the on-disk overlay with a page's unsaved edits applied by
# :func:`_apply_edits`, the one path-setting routine Save uses too. Check
# never writes it anywhere: it reports every unresolved ``${NAME}`` of the
# draft, and only when there is none runs ``core.main.check_config`` on it,
# on a socket-free loop of its own (D7), from a ``config-ui`` worker thread
# (D8) and never on a running loop.
# ---------------------------------------------------------------------------

#: One draft edit: a setting path and the value set there.
Edit = tuple[tuple[Any, ...], Any]

#: The settings phase: ``(base, environ, overlay path, draft)`` → whether
#: the draft passes, and every diagnostic reported.
Checker = Callable[[Path, Mapping[str, str], Path | None, Mapping[str, Any]], tuple[bool, list[str]]]

CHECK_NOT_REACHED = "settings phase not reached"

#: The add-channel-policy form's field naming ``triggers.<input>.channels``.
_ADD_CHANNEL_POLICY = "add_channel_policy"

#: Posted fields that carry no setting: the guard's token, the page, and the
#: operations that only Save and Remove perform.
_NOT_SETTING_FIELDS = frozenset(
    {
        CSRF_FIELD,
        PAGE_FIELD,
        FINGERPRINT_FIELD,
        LAYOUT_FIELD,
        "path",
        _ADD_CHANNEL_POLICY,
        "channel",
        "combination",
        "rule_type",
        "parameters",
    }
)
_ENABLED_TOGGLE = "enabled_modules."
_NEW_ENTRY_NAME = "new_entry_name:"
_NEW_ENTRY_VALUE = "new_entry_value:"
_INTEGER_TEXT = re.compile(r"[+-]?[0-9]+\Z")


@dataclass(frozen=True)
class CheckResult:
    """The verdict of one Check (R5): every diagnostic, value-free."""

    passed: bool
    diagnostics: tuple[str, ...]
    #: ``False`` when unresolved references stopped Check before the
    #: module-settings phase.
    settings_phase_reached: bool = True

    def for_module(self, name: str) -> tuple[str, ...]:
        """The diagnostics naming module *name* (what its page lists)."""

        return tuple(line for line in self.diagnostics if _diagnostic_names_module(line, name))


def _diagnostic_names_module(diagnostic: str, name: str) -> bool:
    """Whether *diagnostic* names module *name*.

    The shapes are the ones the check path reports: the loader's
    ``module 'name': field 'f': reason`` (``_field_diagnostic``, which
    ``_module_diagnostic`` also returns), a configuration path
    ``modules.name…`` or ``triggers.name…`` (``load_config`` and the
    unresolved-reference phase), and a ``name: …`` or ``module name …``
    prefix.
    """

    quoted = re.escape(name)
    if diagnostic.startswith(f"{name}:"):
        return True
    if re.match(rf"module\s+['\"]?{quoted}['\"]?(?=[:\s]|\Z)", diagnostic):
        return True
    return (
        re.search(
            rf"(?<![\w.])(?:{MODULES_KEY}|triggers)\.{quoted}(?=[.:\[\s'\"]|\Z)", diagnostic
        )
        is not None
    )


def _child(node: Any, segment: Any) -> Any:
    if isinstance(node, dict):
        return node.get(segment)
    if isinstance(node, list) and isinstance(segment, int) and 0 <= segment < len(node):
        return node[segment]
    return None


def _set_path(document: dict[Any, Any], path: Sequence[Any], value: Any) -> None:
    """Set *value* at *path* inside *document*, creating missing mappings.

    A list item is set only where it exists: a path never invents one.
    """

    if not path:
        raise ValueError("an edit needs a setting path")
    node: Any = document
    for index, segment in enumerate(path[:-1]):
        following = path[index + 1]
        wants_list = isinstance(following, int) and not isinstance(following, bool)
        child = _child(node, segment)
        if not isinstance(child, list if wants_list else dict):
            if wants_list or not isinstance(node, dict):
                raise ValueError(f"{_path_text(path)}: no such item")
            child = {}
            node[segment] = child
        node = child
    last = path[-1]
    if isinstance(node, dict):
        node[last] = _detached(value)
    elif isinstance(node, list) and isinstance(last, int) and 0 <= last < len(node):
        node[last] = _detached(value)
    else:
        raise ValueError(f"{_path_text(path)}: no such item")


def _draft_edit(
    edits: dict[tuple[Any, ...], Any],
    policies: dict[tuple[Any, ...], Any],
    view: ConfigView,
    path: tuple[Any, ...],
    value: Any,
) -> None:
    """Record one posted edit at *path*.

    A path inside a trigger channel policy is lifted to the whole policy
    (the unit a policy is written in, R6): it is set in a copy of the merged
    policy, so an edit never walks a rule list the overlay does not hold,
    and several edits to one channel land in the same copy.
    """

    if path[0] == "triggers" and len(path) > 4:
        channel = path[:4]
        if channel not in policies:
            policies[channel] = _detached(view.value(channel))
        _set_path(policies[channel], path[4:], value)
    else:
        edits[path] = value


def _detached(value: Any) -> Any:
    """A copy of *value* sharing no container, with itself or with *value*.

    ``copy.deepcopy`` keeps a YAML alias an alias: two paths anchored to one
    mapping stay one object in the copy, so setting a child through one path
    would change the other. Every occurrence here gets its own container.
    """

    if isinstance(value, Mapping):
        return {key: _detached(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_detached(item) for item in value]
    return copy.deepcopy(value)


def _apply_edits(overlay: Mapping[str, Any], edits: Iterable[Edit]) -> dict[str, Any]:
    """*overlay* with each edit's value set at its path; *overlay* is untouched.

    The one path-setting routine: Check validates exactly the draft Save
    would write.
    """

    draft: dict[str, Any] = _detached(overlay)
    for path, value in edits:
        _set_path(draft, path, value)
    return draft


def _coerce(kind: str, schema: Mapping[str, Any], text: str) -> Any:
    """A posted field's text read as its control's kind.

    A ``${NAME}`` reference stays its text whatever the kind; text that does
    not read as the kind is kept as text, for validation to report.
    """

    if reference_name(text) is not None:
        return text
    stripped = text.strip()
    if kind == "integer":
        return int(stripped) if _INTEGER_TEXT.match(stripped) else text
    if kind == "number":
        if _INTEGER_TEXT.match(stripped):
            return int(stripped)
        try:
            number = float(stripped)
        except ValueError:
            return text
        return number if math.isfinite(number) else text
    if kind == "boolean":
        return {"true": True, "false": False}.get(text, text)
    if kind == "enum":
        members = schema.get("enum")
        for member in members if isinstance(members, list) else []:
            if _plain(member) == text:
                return member
        return text
    if kind == "json":
        try:
            return json.loads(text)
        except ValueError:
            return text
    return text


def _form_fields(request: UIRequest) -> list[tuple[str, str]]:
    """The fields of a form-encoded body, in order; none for any other body."""

    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type != "application/x-www-form-urlencoded" or not request.body:
        return []
    try:
        return parse_qsl(request.body.decode("utf-8"), keep_blank_values=True)
    except UnicodeDecodeError:
        return []


def _scan_references(
    value: Any,
    path: tuple[Any, ...],
    environ: Mapping[str, str],
    found: list[str],
    active: set[int],
) -> None:
    name = reference_name(value)
    if name is not None:
        if name not in environ:
            found.append(f"{_path_text(path)}: ${{{name}}} is unresolved")
        return
    if not isinstance(value, (Mapping, list)) or id(value) in active:
        return
    active.add(id(value))
    try:
        items = value.items() if isinstance(value, Mapping) else enumerate(value)
        for key, item in items:
            key_name = reference_name(key)
            if key_name is not None and key_name not in environ:
                found.append(f"{_path_text((*path, key))}: ${{{key_name}}} is unresolved")
            _scan_references(item, (*path, key), environ, found, active)
    finally:
        active.discard(id(value))


def _unresolved_references(merged: Mapping[str, Any], environ: Mapping[str, str]) -> list[str]:
    """``<setting path>: ${NAME} is unresolved`` for each reference of *merged*
    whose variable *environ* lacks.

    Scanned: every top-level block but ``modules`` and ``secrets``, and the
    settings of the enabled modules — what ``load_config`` resolves. A
    variable name is not a secret; its value is never looked at.
    """

    found: list[str] = []
    for key, value in merged.items():
        if key in (MODULES_KEY, SECRETS_KEY):
            continue
        _scan_references(value, (key,), environ, found, set())
    modules = merged.get(MODULES_KEY)
    enabled = merged.get("enabled_modules")
    if isinstance(modules, Mapping) and isinstance(enabled, list):
        for name in dict.fromkeys(item for item in enabled if isinstance(item, str)):
            if name in modules:
                _scan_references(modules[name], (MODULES_KEY, name), environ, found, set())
    return found


def _credential_literals(view: ConfigView, draft: Mapping[str, Any]) -> set[str]:
    """The credential literals a draft carries, for the redaction guard."""

    found: set[str] = set()
    for name, module in view.modules.items():
        declaration = module.declaration
        for credential in declaration.credentials if declaration is not None else ():
            literal = _lookup(draft, (MODULES_KEY, name, *credential))
            if (
                isinstance(literal, (str, int, float))
                and not isinstance(literal, bool)
                and reference_name(literal) is None
            ):
                found.add(str(literal))
    return found


def _default_checker(
    base_path: Path,
    environ: Mapping[str, str],
    overlay_path: Path | None,
    draft: Mapping[str, Any],
) -> tuple[bool, list[str]]:
    """The settings phase through ``core.main.check_config`` (R5, D7, D8).

    It runs the check on its own socket-free loop, never ``asyncio.run``
    (whose self-pipe is a socketpair), and refuses to run on a thread that
    already runs a loop: in production it is reached on a ``config-ui``
    worker through :func:`_dispatch`.
    """

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        raise RuntimeError("Check must not run on a running event loop")
    collected: list[str] = []
    status = _run_socket_free(
        check_config(
            base_path,
            environ=environ,
            overlay=overlay_path,
            overlay_document=draft,
            diagnostic_reporter=collected.append,
        )
    )
    return status == 0, collected


# ---------------------------------------------------------------------------
# Save and remove-override (R6, A7, A9)
#
# A write targets only the 6c scope — ``enabled_modules``,
# ``modules.<name>.<setting path>``, ``triggers.<input>.channels.<channel>``,
# ``limits.<group>.<field>`` and ``modules_directory`` — and never a
# protected field: a declared credential path, or a field whose configured
# (merged, unresolved) text is a ``${NAME}`` reference. A target that is an
# ancestor of protected fields must keep each one's configured text exactly,
# a referenced mapping key included (compared as key text, never resolved).
# The new overlay is the file as parsed with the edit applied, so a
# hand-written ``secrets`` or ``actions`` block is kept as parsed (A9); it is
# written to a temporary file beside the overlay, synced, then renamed over
# it, so a reader sees the old or the new file, never a partial one.
# ---------------------------------------------------------------------------

OUTCOME_SAVED = "saved"
OUTCOME_REMOVED = "removed"
OUTCOME_UNCHANGED = "unchanged"
OUTCOME_STALE = "stale"
OUTCOME_REFUSED = "refused"
OUTCOME_INVALID = "invalid"
OUTCOME_FAILED = "failed"

_OPERATION = {OUTCOME_SAVED: "save", OUTCOME_REMOVED: "remove-override"}
_OUTCOME_STATUS = {
    OUTCOME_UNCHANGED: 200,
    OUTCOME_STALE: 409,
    OUTCOME_REFUSED: 403,
    OUTCOME_INVALID: 422,
    OUTCOME_FAILED: 500,
}

_PROTECTED_REASON = "a protected field must keep its configured text"
_READ_ONLY_BLOCK = "is read-only in v1"
_OUTSIDE_SCOPE = "is outside the settings this UI writes"


@dataclass(frozen=True)
class WriteResult:
    """The outcome of one Save or Remove (R6): value-free diagnostics."""

    outcome: str
    diagnostics: tuple[str, ...] = ()
    #: The setting paths an accepted operation touched.
    paths: tuple[str, ...] = ()

    @property
    def accepted(self) -> bool:
        return self.outcome in (OUTCOME_SAVED, OUTCOME_REMOVED)


def _posted(fields: Sequence[tuple[str, str]], name: str) -> str:
    """The last value posted for *name*, or ``""``."""

    value = ""
    for key, item in fields:
        if key == name:
            value = item
    return value


def _stale_layout(fields: Sequence[tuple[str, str]], view: ConfigView) -> WriteResult | None:
    """A stale result when the posted form's key layout is not *view*'s.

    Positional field names read back against another key order would name
    other keys; the form is refused before any of its names is parsed.
    """

    posted = _posted(fields, LAYOUT_FIELD)
    if posted and _equal(posted, _view_layout(view)):
        return None
    return WriteResult(
        OUTCOME_STALE,
        ("the configuration changed on disk since this page was rendered; reload it",),
    )


def _file_fingerprint(path: Path) -> str | None:
    """The overlay fingerprint on disk; ``None`` when the file cannot be read."""

    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except FileNotFoundError:
        return FINGERPRINT_ABSENT
    except OSError:
        return None


_GOT_VALUE = re.compile(r",? got .*\Z", re.S)


def _value_free(line: str) -> str:
    """*line* without the ``got <value>`` tail a contract diagnostic may carry."""

    return _GOT_VALUE.sub("", line)


def _scope_refusal(view: ConfigView, path: tuple[Any, ...]) -> str | None:
    """Why *path* is outside the 6c writing scope, or ``None`` inside it."""

    if not path:
        return "is not a setting path"
    head = path[0]
    if head in (SECRETS_KEY, ACTIONS_KEY):
        return _READ_ONLY_BLOCK
    if head in ("enabled_modules", "modules_directory"):
        return None if len(path) == 1 else _OUTSIDE_SCOPE
    if head == MODULES_KEY:
        if len(path) < 3 or path[1] not in view.modules:
            return "is not a setting of a discovered module"
        if path[2] == LIMITS_KEY:
            return LIMITS_NOTICE
        return None
    if head == "triggers":
        module = view.modules.get(path[1]) if len(path) == 4 else None
        if (
            module is None
            or path[2] != "channels"
            or not _trigger_types(module.manifest)
            or not isinstance(path[3], str)
            or not path[3].strip()
        ):
            return "is not a whole channel policy of a discovered trigger input"
        return None
    if head == LIMITS_KEY:
        if len(path) == 3 and path[2] in LIMIT_DECLARATION.get(path[1], {}):
            return None
        return "is not a declared limit"
    return _OUTSIDE_SCOPE


def _direct_protection(view: ConfigView, path: tuple[Any, ...]) -> list[str]:
    """Refusals for a target that is, or lies inside, a protected field."""

    for end in range(1, len(path) + 1):
        prefix = path[:end]
        inside = end < len(path)
        if view.is_credential(prefix):
            where = "is inside" if inside else "is"
            return [f"{_path_text(path)}: {where} a declared credential"]
        if reference_name(view.value(prefix)) is not None:
            where = "is inside a field whose" if inside else "its"
            return [f"{_path_text(path)}: {where} configured value is a ${{NAME}} reference"]
    return []


def _protected_descendants(
    view: ConfigView, target: tuple[Any, ...]
) -> list[tuple[tuple[Any, ...], str]]:
    """``(relative path, kind)`` of every protected field below *target*.

    ``kind`` is ``value`` for a ``${NAME}`` value, ``key`` for a ``${NAME}``
    mapping key and ``credential`` for a declared credential path, present or
    not.
    """

    found: list[tuple[tuple[Any, ...], str]] = []
    active: set[int] = set()

    def walk(value: Any, relative: tuple[Any, ...]) -> None:
        if relative and reference_name(value) is not None:
            found.append((relative, "value"))
            return
        if not isinstance(value, (Mapping, list)) or id(value) in active:
            return
        active.add(id(value))
        items = value.items() if isinstance(value, Mapping) else enumerate(value)
        for key, item in items:
            if isinstance(value, Mapping) and reference_name(key) is not None:
                found.append(((*relative, key), "key"))
            walk(item, (*relative, key))
        active.discard(id(value))

    current = view.value(target)
    if current is not _MISSING:
        walk(current, ())
    if target[0] == MODULES_KEY and len(target) >= 2:
        module = view.modules.get(target[1])
        declaration = module.declaration if module is not None else None
        setting = tuple(target[2:])
        for credential in declaration.credentials if declaration is not None else ():
            if len(credential) > len(setting) and tuple(credential[: len(setting)]) == setting:
                found.append((tuple(credential[len(setting) :]), "credential"))
    return found


def _same(left: Any, right: Any) -> bool:
    """Both absent, or the same configured text of the same type."""

    if left is _MISSING or right is _MISSING:
        return left is right
    return type(left) is type(right) and left == right


def _protect_save(view: ConfigView, path: tuple[Any, ...], value: Any) -> tuple[Any, list[str]]:
    """*value* with hidden credentials restored, and every protection refusal.

    A credential the page showed as :data:`HIDDEN_LITERAL` inside a JSON
    editor is put back as its configured literal before the comparison.
    """

    refusals = _direct_protection(view, path)
    if refusals:
        return value, refusals
    current = view.value(path)
    for relative, kind in _protected_descendants(view, path):
        configured = _lookup(current, relative) if current is not _MISSING else _MISSING
        proposed = _lookup(value, relative)
        if kind == "credential" and proposed == HIDDEN_LITERAL and configured is not _MISSING:
            value = copy.deepcopy(value)
            _set_path(value, relative, configured)
            proposed = configured
        kept = proposed is not _MISSING if kind == "key" else _same(proposed, configured)
        if not kept:
            refusals.append(f"{_path_text((*path, *relative))}: {_PROTECTED_REASON}")
    return value, refusals


def _protect_remove(
    view: ConfigView, path: tuple[Any, ...], merged_after: Mapping[str, Any]
) -> list[str]:
    """Refusals for removing *path* when a protected effective text would change."""

    refusals = _direct_protection(view, path)
    if refusals:
        return refusals
    for relative, kind in _protected_descendants(view, path):
        full = (*path, *relative)
        before, after = view.value(full), _lookup(merged_after, full)
        kept = after is not _MISSING if kind == "key" else _same(after, before)
        if not kept:
            refusals.append(f"{_path_text(full)}: {_PROTECTED_REASON}")
    return refusals


def _validate_edit(view: ConfigView, path: tuple[Any, ...], value: Any) -> list[str]:
    """The per-field diagnostics of one in-scope edit; empty when valid."""

    label = _path_text(path)
    head = path[0]
    if head == "enabled_modules":
        if not isinstance(value, list):
            return [f"{label}: must be a list of module names"]
        found: list[str] = []
        seen: set[str] = set()
        for index, name in enumerate(value):
            if not isinstance(name, str):
                found.append(f"{label}[{index}]: must be a module name")
            elif name in seen:
                found.append(f"{label}[{index}]: repeats an enabled module")
            elif name not in view.modules:
                found.append(f"{label}[{index}]: is not a discovered module")
            else:
                seen.add(name)
        return found
    if reference_name(value) is not None and head != "triggers":
        # Resolved when the configuration loads; Check reports it unresolved.
        return []
    if head == "modules_directory":
        if isinstance(value, str) and value.strip():
            return []
        return [f"{label}: must be a non-empty string"]
    if head == LIMITS_KEY:
        kind = LIMIT_DECLARATION[path[1]][path[2]]
        if kind == LIMIT_KIND_COUNT:
            if isinstance(value, int) and not isinstance(value, bool) and value >= 1:
                return []
            return [f"{label}: must be a positive integer"]
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
            and value > 0
        ):
            return []
        return [f"{label}: must be a finite positive number"]
    module = view.modules[path[1]]
    if head == "triggers":
        spec = module.declaration.triggers if module.declaration is not None else None
        try:
            policy = _trigger_policy(path[1], label, value)
            if spec is None:
                return [f"{label}: the module declares no trigger type"]
            spec.validate_policy(policy, label=label)
        except ModuleLoadError as exc:
            return list(exc.diagnostics)
        except ContractError as exc:
            return [f"{exc.field}: {exc.reason}"]
        return []
    schema = _setting_schema(module.manifest.get(MANIFEST_SETTINGS_SCHEMA_KEY), path[2:])
    if schema is None:
        return [f"{label}: is not a declared setting"]
    found = []
    try:
        validate_schema(schema, label=label)
    except ContractError as exc:
        return [f"{exc.field}: {exc.reason}"]
    _validate_setting(value, schema, label, found)
    return found


def _setting_schema(schema: Any, setting: Sequence[Any]) -> Mapping[str, Any] | None:
    """The ``settings_schema`` node describing *setting*, or ``None``."""

    node = schema
    for segment in setting:
        if not isinstance(node, Mapping):
            return None
        if isinstance(segment, int) and not isinstance(segment, bool):
            node = node.get("items")
            continue
        properties = node.get("properties")
        if isinstance(properties, Mapping) and segment in properties:
            node = properties[segment]
        elif isinstance(node.get("additionalProperties"), Mapping):
            node = node["additionalProperties"]
        else:
            return None
    return node if isinstance(node, Mapping) else None


#: The schema keywords :func:`_validate_setting` walks itself.
_CHILD_KEYWORDS = frozenset({"properties", "items", "required", "additionalProperties"})


def _validate_setting(value: Any, schema: Mapping[str, Any], label: str, found: list[str]) -> None:
    """Validate *value* against *schema* through ``core.contracts``.

    A ``${NAME}`` reference anywhere in the value is accepted as it stands:
    the configuration resolves it before validating, and Check reports it
    unresolved. The node's own keywords are checked by
    :func:`validate_against_schema`; its children are walked here so that a
    reference child is skipped rather than checked as text.
    """

    if reference_name(value) is not None:
        return
    own = {key: item for key, item in schema.items() if key not in _CHILD_KEYWORDS}
    try:
        validate_against_schema(value, own, label=label)
    except ContractError as exc:
        found.append(f"{exc.field}: {exc.reason}")
        return
    object_keywords = {"required", "properties", "additionalProperties"} & set(schema)
    if object_keywords:
        if not isinstance(value, Mapping):
            found.append(f"{label}: must be a mapping")
            return
        properties = schema.get("properties")
        properties = properties if isinstance(properties, Mapping) else {}
        for name in schema.get("required", ()):
            if name not in value:
                found.append(f"{label}.{name}: is required and missing")
        entries = schema.get("additionalProperties")
        for key, item in value.items():
            if not isinstance(key, str):
                found.append(f"{label}: must use string keys")
            elif key in properties:
                _validate_setting(item, properties[key], f"{label}.{key}", found)
            elif entries is False:
                found.append(f"{label}.{key}: is not an allowed property")
            elif isinstance(entries, Mapping):
                _validate_setting(item, entries, f"{label}.{key}", found)
    if "items" in schema:
        if not isinstance(value, list):
            found.append(f"{label}: must be a list")
            return
        for index, item in enumerate(value):
            _validate_setting(item, schema["items"], f"{label}[{index}]", found)


def _overlay_key_paths(overlay: Mapping[str, Any]) -> list[tuple[Any, ...]]:
    """Every mapping-key path of *overlay* (list items are not removable)."""

    found: list[tuple[Any, ...]] = []
    pending: list[tuple[tuple[Any, ...], Any]] = [((), overlay)]
    seen: set[int] = set()
    while pending:
        prefix, node = pending.pop()
        if not isinstance(node, Mapping) or id(node) in seen:
            continue
        seen.add(id(node))
        for key, item in node.items():
            found.append((*prefix, key))
            pending.append(((*prefix, key), item))
    return found


def _remove_path(overlay: Mapping[str, Any], path: Sequence[Any]) -> dict[str, Any]:
    """*overlay* without *path*, emptied parent mappings pruned; a copy."""

    draft: dict[str, Any] = _detached(overlay)
    chain: list[tuple[dict[Any, Any], Any]] = []
    node: Any = draft
    for segment in path[:-1]:
        chain.append((node, segment))
        node = node[segment]
    del node[path[-1]]
    for parent, segment in reversed(chain):
        if parent[segment] == {}:
            del parent[segment]
        else:
            break
    return draft


def _write_overlay(
    target: Path | None, managed: Path | None, base: Path, document: Mapping[str, Any]
) -> None:
    """Replace the managed overlay with *document*, atomically (R6).

    The target must be the overlay path resolved at startup and never the
    base. The text goes to a temporary file in the overlay's directory, is
    synced, then renamed over the overlay; on any failure the temporary file
    is removed and the previous overlay stays as it was.
    """

    if (
        target is None
        or managed is None
        or target != managed
        or target != canonical_path(target)
        or same_file(target, base)
    ):
        raise RuntimeError("refusing to write a file other than the managed overlay")
    text = yaml.safe_dump(dict(document), sort_keys=False, allow_unicode=True)
    directory = target.parent
    handle, temporary = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=directory
    )
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(text.encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    except BaseException:
        with suppress(OSError):
            os.unlink(temporary)
        raise


# ---------------------------------------------------------------------------
# Supervised restart (R7 Apply, A4, D9)
#
# Apply runs an on-disk Check, stops the one child this UI launched
# (terminate, a bounded wait, then kill and a bounded wait), starts the launch argv
# without a shell with the status-file variable set, and polls the status
# path within a bounded window for one of three outcomes. A status record
# naming any other pid is never signalled. The child's stdout is inherited,
# so it can never fill a pipe; its stderr is a pipe drained for the child's
# whole life by a :class:`_StderrDrain` thread, which forwards it to the
# UI's own stderr (the terminal, never a page or a log record) and keeps a
# bounded tail for a refused Apply's report.
# ---------------------------------------------------------------------------

APPLY_ACCEPTED = "accepted"
APPLY_REFUSED = "refused"
APPLY_UNKNOWN = "unknown"

#: The documented defaults (R7): the stop grace and the Apply window, in seconds.
DEFAULT_STOP_GRACE = 10.0
DEFAULT_APPLY_WINDOW = 60.0
#: How long a stop waits for the child to be reaped after kill (gate F5).
#: A stop is bounded by grace + kill wait + drain join, before the Apply
#: window (which counts from the new child's start) begins.
DEFAULT_KILL_WAIT = 5.0
APPLY_POLL_SECONDS = 0.05
#: How long a stop or a refused Apply waits for the drain to read EOF.
DRAIN_JOIN_SECONDS = 2.0
#: The drain keeps the last 8 KiB; a report shows at most the last 2 KiB.
DRAIN_TAIL_BYTES = 8 * 1024
REPORT_TAIL_BYTES = 2 * 1024
_DRAIN_CHUNK = 4096


class _StderrDrain(threading.Thread):
    """Reads one child's stderr pipe until EOF (D9).

    Each chunk is forwarded to the UI's own ``sys.stderr.buffer`` (a failed
    write only skips forwarding; draining never stops early) and appended to
    a tail trimmed to the last :data:`DRAIN_TAIL_BYTES` under a lock.
    """

    def __init__(self, fd: int, stream: Any = None) -> None:
        super().__init__(name="stderr-drain", daemon=True)
        self._fd = fd
        #: The pipe's file object, closed by :meth:`close` once drained.
        self._stream = stream
        self._tail = bytearray()
        self._lock = threading.Lock()
        self._finished = False
        self._close_when_finished = False

    def run(self) -> None:
        try:
            while True:
                try:
                    chunk = os.read(self._fd, _DRAIN_CHUNK)
                except OSError:
                    break
                if not chunk:
                    break
                self._forward(chunk)
                with self._lock:
                    self._tail += chunk
                    if len(self._tail) > DRAIN_TAIL_BYTES:
                        del self._tail[:-DRAIN_TAIL_BYTES]
        finally:
            with self._lock:
                self._finished = True
                close = self._close_when_finished
            if close:
                self._close_stream()

    @staticmethod
    def _forward(chunk: bytes) -> None:
        try:
            sys.stderr.buffer.write(chunk)
            sys.stderr.buffer.flush()
        except Exception:  # noqa: BLE001 - forwarding only; the drain goes on
            pass

    def tail(self, limit: int) -> str:
        """The last *limit* bytes drained, decoded with ``errors="replace"``."""

        with self._lock:
            data = bytes(self._tail[-limit:]) if limit > 0 else b""
        return data.decode("utf-8", errors="replace")

    def close(self, timeout: float = DRAIN_JOIN_SECONDS) -> None:
        """Join (bounded), then close the pipe.

        A drain still blocked after *timeout* (a grandchild holding the write
        end) closes the pipe itself on EOF, so its descriptor is never closed
        under a pending read.
        """

        self.join(timeout)
        with self._lock:
            if not self._finished:
                self._close_when_finished = True
                return
        self._close_stream()

    def _close_stream(self) -> None:
        if self._stream is not None:
            with suppress(OSError, ValueError):
                self._stream.close()


@dataclass(frozen=True)
class ApplyReport:
    """The outcome of one Apply (R7); every text is value-free and redacted."""

    outcome: str
    #: Why Apply refused before stopping anything: the on-disk Check's diagnostics.
    diagnostics: tuple[str, ...] = ()
    #: The exit status of a child that exited within the window.
    exit_status: int | None = None
    #: The last :data:`REPORT_TAIL_BYTES` of that child's stderr.
    stderr_tail: str = ""
    #: ``True`` when the on-disk Check refused and nothing was stopped or started.
    check_refused: bool = False


def default_launch_argv(settings: UISettings) -> tuple[str, ...]:
    """The launch argv: the one given after ``--``, else the runtime on this base (R7)."""

    if settings.launch_argv:
        return settings.launch_argv
    argv = [sys.executable, "-m", "core.main", "--config", os.fspath(settings.base_path)]
    # The managed overlay is always passed, even when derived implicitly: the
    # runtime would derive its own from the canonical base's name, which is a
    # different file when the given base path is a link (R7).
    overlay = settings.overlay_path
    if overlay is not None:
        argv += ["--overlay", os.fspath(overlay)]
    return tuple(argv)


def _real_wait(interval: float) -> None:
    threading.Event().wait(interval)


class SupervisorClosed(RuntimeError):
    """The UI is shutting down: :meth:`Supervisor.start` launches nothing more."""


class Supervisor:
    """Stops and starts the one main process this UI launched (R7, A4, D9).

    Every wait is bounded: ``clock`` and ``wait`` drive the Apply window (a
    test injects both), ``stop_grace`` bounds the wait after terminate and
    ``kill_wait`` the wait after kill. An Apply thus takes at most
    ``stop_grace + kill_wait + DRAIN_JOIN_SECONDS`` to stop the old child,
    then the Apply window from the new child's start.
    """

    def __init__(
        self,
        argv: Sequence[str],
        status_path: str | os.PathLike[str],
        *,
        stop_grace: float = DEFAULT_STOP_GRACE,
        kill_wait: float = DEFAULT_KILL_WAIT,
        apply_window: float = DEFAULT_APPLY_WINDOW,
        poll_interval: float = APPLY_POLL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        wait: Callable[[float], None] = _real_wait,
        popen: Callable[..., Any] = subprocess.Popen,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self.argv = tuple(argv)
        self.status_path = os.fspath(status_path)
        self.stop_grace = stop_grace
        self.kill_wait = kill_wait
        self.apply_window = apply_window
        self.poll_interval = poll_interval
        self.clock = clock
        self.wait = wait
        self.popen = popen
        self._environ = environ
        #: The child this UI launched, if any: the only process ever signalled.
        self.child: Any = None
        self._drain: _StderrDrain | None = None
        #: Guards :attr:`child` against a launch racing :meth:`close`.
        self._lock = threading.Lock()
        self._closed = False

    def close(self) -> None:
        """Forbid every later launch, then stop the child (the UI's final cleanup).

        A restart still waiting on the mutation lock after the HTTP server
        stopped then starts nothing, so no runtime outlives the UI.
        """

        with self._lock:
            self._closed = True
        if not self.stop():
            logger.warning(
                "the supervised process %s did not end within %g s after kill",
                self.child.pid,
                self.kill_wait,
                extra={"config_ui_operation": "close", "config_ui_outcome": "unconfirmed"},
            )

    def stop(self) -> bool:
        """Stop the child this UI launched, if any; whether it is known to be gone.

        Terminate, a bounded wait, kill, a bounded wait (gate F5). A child
        still not reaped after the kill wait stays :attr:`child` (with its
        drain), so it is neither forgotten nor replaced: ``False`` is
        returned, and a later stop signals and waits on it again.
        """

        with self._lock:
            child, drain = self.child, self._drain
            self.child, self._drain = None, None
        if child is None:
            return True
        child.terminate()
        try:
            child.wait(timeout=self.stop_grace)
        except subprocess.TimeoutExpired:
            child.kill()
            try:
                child.wait(timeout=self.kill_wait)
            except subprocess.TimeoutExpired:
                with self._lock:
                    if self.child is None:
                        self.child, self._drain = child, drain
                return False
        if drain is not None:
            drain.close()
        return True

    def start(self) -> Any:
        """Start the launch argv without a shell, and its stderr drain (D9).

        Raises :class:`SupervisorClosed` after :meth:`close`, and ``OSError``
        when the launch argv cannot be executed.
        """

        environ = os.environ if self._environ is None else self._environ
        with self._lock:
            if self._closed:
                raise SupervisorClosed("the configuration UI is shutting down")
            child = self.popen(
                list(self.argv),
                shell=False,
                env={**environ, STATUS_FILE_VARIABLE: self.status_path},
                stdout=None,
                stderr=subprocess.PIPE,
                start_new_session=False,
            )
            self.child = child
            stream = child.stderr
            drain = _StderrDrain(stream.fileno(), stream) if stream is not None else None
            self._drain = drain
        if drain is not None:
            drain.start()
        return child

    def restart(self, digest: str, secret_values: Iterable[str] = ()) -> ApplyReport:
        """Stop, start, then poll the status path within the window (R7).

        Accepted: a usable record of this child, ``ready``, with *digest*.
        Refused: the child exited; its status and stderr tail are reported.
        Unknown: the window elapsed. An unusable record never counts.
        A launch that fails (the argv cannot be executed, or the UI is
        shutting down) is refused with a redacted diagnostic, and so is a
        restart whose old child is not reaped within the kill wait: nothing
        new is started beside it (gate F5).
        """

        if not self.stop():
            return ApplyReport(
                APPLY_REFUSED,
                diagnostics=(
                    f"the running process (pid {self.child.pid}) did not end within "
                    f"{self.kill_wait:g} s after kill; nothing new was started",
                ),
            )
        try:
            child = self.start()
        except SupervisorClosed as exc:
            return ApplyReport(APPLY_REFUSED, diagnostics=(str(exc),))
        except OSError as exc:
            reason = _value_free(_redact(str(exc), secret_values))
            return ApplyReport(
                APPLY_REFUSED,
                diagnostics=(f"the launch command could not be started: {reason}",),
            )
        drain = self._drain
        deadline = self.clock() + self.apply_window
        while True:
            returncode = child.poll()
            if returncode is not None:
                tail = ""
                if drain is not None:
                    drain.join(DRAIN_JOIN_SECONDS)
                    tail = _report_tail(drain.tail(DRAIN_TAIL_BYTES), secret_values)
                return ApplyReport(APPLY_REFUSED, exit_status=returncode, stderr_tail=tail)
            record = read_status(self.status_path).record
            if record is not None and record.pid == child.pid and record.digest == digest:
                # A usable record's state is always ``ready`` (the reader
                # refuses every other value).
                return ApplyReport(APPLY_ACCEPTED)
            if self.clock() >= deadline:
                return ApplyReport(APPLY_UNKNOWN)
            self.wait(self.poll_interval)


def _report_tail(text: str, secret_values: Iterable[str]) -> str:
    """The last :data:`REPORT_TAIL_BYTES` of *text*, redacted and value-free (R8).

    Redaction runs on the whole drained tail first, so a secret cut by the
    2 KiB boundary was already replaced.
    """

    redacted = _redact(text, secret_values)
    lines = "\n".join(_value_free(line) for line in redacted.split("\n"))
    data = lines.encode("utf-8")[-REPORT_TAIL_BYTES:]
    return data.decode("utf-8", errors="ignore")


# ---------------------------------------------------------------------------
# Socket-free event loop (D7)
#
# A standard selector loop creates a self-pipe socketpair; this one creates no
# socket at all. The CPython self-pipe hooks are no-ops and the selector caps
# every wait at 10 ms, so a callback scheduled from another thread (which
# would normally write to the self-pipe) is still picked up by polling.
# These hooks are private CPython API, pinned by a test.
# ---------------------------------------------------------------------------

_POLL_SECONDS = 0.01

_T = TypeVar("_T")


class _CappedSelector(selectors.DefaultSelector):  # type: ignore[misc, valid-type]
    """A selector whose ``select`` never waits longer than 10 ms."""

    def select(self, timeout: float | None = None) -> list[Any]:
        if timeout is None or timeout > _POLL_SECONDS:
            timeout = _POLL_SECONDS
        return super().select(timeout)


class _SocketFreeEventLoop(asyncio.SelectorEventLoop):  # type: ignore[misc, valid-type]
    """A selector event loop without the self-pipe socketpair."""

    def __init__(self) -> None:
        super().__init__(_CappedSelector())

    def _make_self_pipe(self) -> None:
        self._ssock = None
        self._csock = None

    def _close_self_pipe(self) -> None:
        return None

    def _write_to_self(self) -> None:
        return None


def _cancel_remaining(loop: asyncio.AbstractEventLoop) -> None:
    pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
    if not pending:
        return
    for task in pending:
        task.cancel()
    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))


def _run_socket_free(coro: Coroutine[Any, Any, _T]) -> _T:
    """Run *coro* to completion on a fresh :class:`_SocketFreeEventLoop`.

    Mirrors ``asyncio.run``: remaining tasks are cancelled, async generators
    and the default executor are shut down and the loop is closed, whatever
    the outcome.
    """

    loop = _SocketFreeEventLoop()
    try:
        return loop.run_until_complete(coro)
    finally:
        try:
            _cancel_remaining(loop)
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.run_until_complete(loop.shutdown_default_executor())
        finally:
            loop.close()


# ---------------------------------------------------------------------------
# Request bridge (D8) and the aiohttp adapter
# ---------------------------------------------------------------------------

EXECUTOR_THREAD_PREFIX = "config-ui"
EXECUTOR_WORKERS = 4
MAX_REQUEST_BYTES = 1024 * 1024
#: Requests admitted at once, running or queued for a worker (gate F4). The
#: admission is taken before the body is read, and each admitted body is at
#: most MAX_REQUEST_BYTES, so the bridge retains at most 16 MiB of requests.
MAX_ADMITTED_REQUESTS = 4 * EXECUTOR_WORKERS
#: A refused request's ``Retry-After``, in seconds.
BUSY_RETRY_SECONDS = 1
BUSY_STATUS = 503


class _Admission:
    """A bounded count of requests admitted to the worker pool (gate F4).

    Entered on the event loop before the body is read; left when the
    worker's future completes (whatever thread completes it), so a
    request whose client went away still counts while it runs.
    """

    def __init__(self, capacity: int = MAX_ADMITTED_REQUESTS) -> None:
        self.capacity = capacity
        self.admitted = 0
        #: Requests refused because the bridge was full.
        self.refused = 0
        self._lock = threading.Lock()

    def try_enter(self) -> bool:
        with self._lock:
            if self.admitted >= self.capacity:
                self.refused += 1
                return False
            self.admitted += 1
            return True

    def leave(self, _future: Any = None) -> None:
        with self._lock:
            self.admitted -= 1


def _busy_response() -> UIResponse:
    """The refusal of a request the full bridge cannot admit: nothing ran."""

    return UIResponse(
        BUSY_STATUS,
        b"The configuration UI is busy: nothing was done, retry shortly.\n",
        headers=(("Retry-After", str(BUSY_RETRY_SECONDS)),),
    )


def _to_ui_request(
    method: str,
    path: str,
    query: Mapping[str, str],
    headers: Any,
    body: bytes,
) -> UIRequest:
    """Convert an HTTP request into a :class:`UIRequest`; pure.

    Header names are lower-cased. A repeated header is folded into one value
    (``Cookie`` with ``; ``, any other with ``, ``), so a duplicated ``Host``
    never matches an accepted authority.
    """

    items = headers.items() if hasattr(headers, "items") else headers
    folded: dict[str, str] = {}
    for name, value in items:
        key = name.lower()
        if key in folded:
            separator = "; " if key == "cookie" else ", "
            folded[key] = folded[key] + separator + value
        else:
            folded[key] = value
    return UIRequest(
        method=method,
        path=path,
        query={key: value for key, value in query.items()},
        headers=folded,
        body=bytes(body),
    )


async def _dispatch(
    ui: ConfigUI,
    request: UIRequest | Callable[[], Awaitable[UIRequest]],
    executor: Executor,
    admission: _Admission,
) -> UIResponse:
    """Run ``ui.handle`` on *executor*, never on the calling loop (D8).

    The request is first admitted (gate F4): when *admission* is full it is
    refused with :data:`BUSY_STATUS` and nothing is read or queued. *request*
    is a :class:`UIRequest`, or an async callable reading it (the HTTP body),
    awaited only once admitted.
    """

    if not admission.try_enter():
        return _busy_response()
    submitted = False
    try:
        if not isinstance(request, UIRequest):
            request = await request()
        future = executor.submit(ui.handle, request)
        future.add_done_callback(admission.leave)
        submitted = True
    finally:
        if not submitted:
            admission.leave()
    return await asyncio.wrap_future(future)


def serve(ui: ConfigUI, settings: UISettings) -> None:
    """Serve *ui* over HTTP on the bound host and port until interrupted."""

    from aiohttp import web

    executor = ThreadPoolExecutor(
        max_workers=EXECUTOR_WORKERS, thread_name_prefix=EXECUTOR_THREAD_PREFIX
    )
    admission = _Admission()

    async def catch_all(request: web.Request) -> web.StreamResponse:
        async def read() -> UIRequest:
            body = await request.read()
            return _to_ui_request(
                request.method, request.path, request.query, request.headers.items(), body
            )

        response = await _dispatch(ui, read, executor, admission)
        reply = web.Response(status=response.status, body=response.body)
        reply.headers["Content-Type"] = response.content_type
        for name, value in response.headers:
            reply.headers.add(name, value)
        return reply

    app = web.Application(client_max_size=MAX_REQUEST_BYTES)
    app.router.add_route("*", "/{tail:.*}", catch_all)
    try:
        # access_log=None: the access URL's query carries the token (AC2).
        web.run_app(app, host=settings.host, port=settings.port, print=None, access_log=None)
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    """Run the configuration UI; return the process exit status."""

    try:
        settings = UISettings.from_argv(sys.argv[1:] if argv is None else argv)
    except SystemExit as exit_:
        return exit_.code if isinstance(exit_.code, int) else 2
    problems = startup_checks(settings)
    if problems:
        for problem in problems:
            print(f"config_ui: {problem}", file=sys.stderr)
        return 2
    ui = ConfigUI(settings)
    print(f"Configuration UI: {ui.access_url}", flush=True)
    logger.info("configuration UI serving on %s", ui.access_authority)
    try:
        serve(ui, settings)
    finally:
        ui.supervisor.close()
    return 0
