"""Local configuration UI, run as its own process (R1, D2, D3).

``python -m core.config_ui --config PATH`` serves a local-only HTML page that
edits the managed overlay file merged over the untouched base configuration
(R2, R6). Without ``--config`` the per-user default profile is used (phase 9
A2), and the access URL lands on the first-run wizard until it is finished. It is never a remote administration channel: it binds a loopback
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

The first-run wizard's screens (``/wizard``) and the language choice
(``/language``) are routes of the same table, behind the same guards
(phase 9 R5, R2): :class:`core.config_ui.wizard.Wizard` renders and
validates the screens, and the UI hands it the per-process fingerprint key,
the first-run decision taken at construction, the stored credentials and
its environment. The wizard never calls the supervisor.

The console (``/console``) and its feed poll (``/feed``) are routes of the
same table too (phase 9 R7): :mod:`core.config_ui.console` reads the
runtime's supervision feed and renders the runtime state, each enabled
module, the feed newest first and the configured grants in the chosen
language, every value through :func:`esc`. Start (``/start``) and Stop
(``/stop``) are state-changing routes of the same table: Start launches
through :meth:`Supervisor.start` only when no live process holds the status
record, Stop stops only the child this UI launched, and a child that exits
during startup is shown as a translated cause beside its redacted raw
stderr tail, behind a closed disclosure.
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
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import weakref
import webbrowser
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from collections import OrderedDict
from datetime import datetime, timezone
from collections.abc import Awaitable, Callable, Coroutine, Iterable, Iterator, Mapping, Sequence
from concurrent.futures import Executor, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, TypeVar
from urllib.parse import parse_qsl

import yaml

from core.actions import ANY_ACTION
from core.bus import EventBus
from core.config_ui import console, locations
from core.config_ui.i18n import LanguageUnavailable, StringTable, StringTableError
from core.config_ui.launcher import (
    LauncherError,
    LauncherSeams,
    launcher_directories,
    write_launcher,
)
from core.config_ui.wizard import (
    LANGUAGE_ROUTE,
    WIZARD_ROUTE,
    CredentialStore,
    Wizard,
    WizardPage,
    WizardSeams,
    WizardState,
    WizardStateError,
    state_path,
)
from core.contracts import (
    INPUT_OWNER_NONE,
    ContractError,
    mask_known,
    validate_against_schema,
    validate_schema,
)
from core.loader import (
    MANIFEST_SETTINGS_SCHEMA_KEY,
    MANIFEST_TRIGGERS_KEY,
    DiscoveredModule,
    ModuleLoader,
    ModuleLoadError,
    _trigger_policy,
    _validate_manifest,
    resolve_input_ownership,
)
from core.main import (
    _ENV_REFERENCE,
    ACTIONS_KEY,
    INPUTS_KEY,
    LIMIT_DECLARATION,
    LIMIT_KIND_COUNT,
    LIMITS_KEY,
    MODULES_DIRECTORY_BUILTIN,
    MODULES_KEY,
    SECRETS_KEY,
    _STARTUP_DEADLINE_SECONDS,
    _builtin_modules_directory,
    _utc_timestamp,
    check_config,
)
from core.overlay import (
    FEED_FILE_VARIABLE,
    STATUS_FILE_VARIABLE,
    OverlayError,
    canonical_overlay_path,
    canonical_path,
    deep_merge,
    default_feed_path,
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
    "landing_target",
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

#: The instance record's suffix, appended to the base file's full name (R1).
INSTANCE_SUFFIX = ".instance.json"
#: The supervision record's suffix, derived as the instance record's (phase 10 R6).
SUPERVISION_SUFFIX = ".supervisor.json"
#: The supervision record's format version and its three states (R6).
SUPERVISION_VERSION = 1
SUPERVISION_NOT_STARTED = "not_started"
SUPERVISION_LAUNCHED = "launched"
SUPERVISION_STOPPED = "stopped"


# ---------------------------------------------------------------------------
# Settings and startup checks (R1, R6, A1, A5)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UISettings:
    """The UI's command line, parsed once at startup."""

    #: The base profile: ``--config``, or the A2 default when it is absent.
    config: Path
    overlay: Path | None = None
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    allow_non_loopback: bool = False
    allowed_hosts: tuple[str, ...] = ()
    status_file: Path | None = None
    launch_argv: tuple[str, ...] = ()
    #: ``--config`` was absent and :attr:`config` is the A2 default location.
    default_location: bool = False
    #: ``--install-launcher``: write the OS launcher and exit (R1, A3).
    install_launcher: bool = False
    #: ``--no-browser``: never call the browser opener (phase 10 R1).
    no_browser: bool = False
    #: ``--token-file``: the access token's file (phase 10 R1, A5).
    token_file: Path | None = None
    #: ``--start-runtime``: start the supervised runtime once listening (phase 10 R1).
    start_runtime: bool = False

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
        default_location = parsed.config is None
        return cls(
            config=(
                locations.default_profile_path(locations.running_platform(), os.environ)
                if default_location
                else Path(parsed.config)
            ),
            overlay=None if parsed.overlay is None else Path(parsed.overlay),
            host=parsed.host,
            port=parsed.port,
            allow_non_loopback=parsed.allow_non_loopback,
            allowed_hosts=tuple(parsed.allowed_hosts),
            status_file=None if parsed.status_file is None else Path(parsed.status_file),
            launch_argv=tuple(launch),
            default_location=default_location,
            install_launcher=parsed.install_launcher,
            no_browser=parsed.no_browser,
            token_file=None if parsed.token_file is None else Path(parsed.token_file),
            start_runtime=parsed.start_runtime,
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

    @property
    def wizard_path(self) -> Path:
        """The wizard state file ``<base>.wizard.json``, canonical (A7)."""

        return canonical_path(state_path(self.config))

    @property
    def credential_path(self) -> Path | None:
        """``<profile-file-name>.credentials.json`` in the per-user credential
        directory, canonical (A1); ``None`` where the platform names none."""

        directory = locations.credential_directory(locations.running_platform(), os.environ)
        if directory is None:
            return None
        return canonical_path(directory / locations.credential_file_name(self.base_path))

    @property
    def instance_path(self) -> Path:
        """The instance record ``<base>.instance.json``, canonical (R1)."""

        return canonical_path(self.config.with_name(self.config.name + INSTANCE_SUFFIX))

    @property
    def feed_path(self) -> Path:
        """The supervision feed ``<base>.feed.jsonl``, canonical (DD9)."""

        return canonical_path(default_feed_path(self.config))

    @property
    def supervision_path(self) -> Path:
        """The supervision record ``<base>.supervisor.json``, canonical (phase 10 R6)."""

        return supervision_path(self)


def supervision_path(settings: UISettings) -> Path:
    """``<base>.supervisor.json`` beside the profile, derived as the instance
    record ``<base>.instance.json`` is (phase 10 R6)."""

    return canonical_path(settings.config.with_name(settings.config.name + SUPERVISION_SUFFIX))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=_PROG,
        description="Local configuration UI (arguments after -- launch the main process).",
    )
    parser.add_argument(
        "--config", help="base configuration file (default: the per-user profile)"
    )
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
    parser.add_argument(
        "--install-launcher",
        action="store_true",
        help="write the per-user OS launcher for this interpreter, print its path and exit",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="never open a browser; the access URL is still printed",
    )
    parser.add_argument(
        "--token-file",
        metavar="PATH",
        help="read the access token from a file instead of generating one",
    )
    parser.add_argument(
        "--start-runtime",
        action="store_true",
        help="once listening, start the runtime when a completed profile is present",
    )
    return parser


#: A supplied access token: at least the 256 bits of a generated one, from
#: the URL-safe base64 alphabet (phase 10 A5).
_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{43,128}")


class TokenFileError(Exception):
    """A ``--token-file`` that cannot supply the token; the message names
    the path and the rule broken, never the file's content (phase 10 R1)."""


def read_token_file(path: Path) -> str:
    """The access token *path* holds, surrounding whitespace stripped (A5).

    Raises :class:`TokenFileError` for an unreadable, empty or invalid file.
    """

    try:
        text = path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        raise TokenFileError(f"token file {path}: unreadable") from None
    token = text.strip()
    if not token:
        raise TokenFileError(f"token file {path}: empty")
    if not 43 <= len(token) <= 128:
        raise TokenFileError(f"token file {path}: length 43–128")
    if _TOKEN_PATTERN.fullmatch(token) is None:
        raise TokenFileError(f"token file {path}: alphabet A–Z a–z 0–9 - _")
    return token


def non_loopback_warning(host: str, port: int) -> str | None:
    """The one start-up line for a bind that is not loopback, built from
    *host* and *port* only (phase 10 R1); ``None`` for a loopback bind."""

    if _is_loopback(host):
        return None
    return (
        f"config_ui: warning: bound to {host} port {port}: the configuration UI is "
        "reachable from the network and protected only by the access token"
    )


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
    try:
        added = [
            ("wizard_state", "wizard state", settings.wizard_path),
            ("credential", "credential", settings.credential_path),
            ("instance", "instance record", settings.instance_path),
            ("feed", "feed", settings.feed_path),
        ]
    except OverlayError as exc:
        problems.append(f"path_not_canonical: {exc}")
        return problems
    # Each file this phase adds beside the profile is checked against the
    # configuration files, the status record and the ones checked before it,
    # so a collision is reported once. Only paths are named, never content.
    against: list[tuple[str, Path | None]] = [
        ("base", base), ("overlay", overlay), ("status", status),
    ]
    for token, role, path in added:
        if path is None:
            continue
        for other, named in against:
            if named is not None and same_file(path, named):
                problems.append(
                    f"{token}_path_collision: {role} file {path} collides with the "
                    f"{other} file {named}"
                )
        against.append((role, path))
    # The supervision record (phase 10 R6) is checked after every other
    # member, so its collision is reported once, naming the first file hit.
    # The feed is also the one its environment variable names, as a runtime
    # started beside this UI with that environment would write it. The
    # --token-file input is checked too: publication would overwrite it and
    # release would delete it, so the next launch could not read its token.
    try:
        supervision = settings.supervision_path
        named_feed = os.environ.get(FEED_FILE_VARIABLE)
        if named_feed:
            against.append(("feed", canonical_path(named_feed)))
        if settings.token_file is not None:
            against.append(("token", canonical_path(settings.token_file)))
    except OverlayError as exc:
        problems.append(f"path_not_canonical: {exc}")
        return problems
    for other, named in against:
        if named is not None and same_file(supervision, named):
            problems.append(
                f"supervision_path_collision: supervision record file {supervision} "
                f"collides with the {other} file {named}"
            )
            break
    return problems


#: Where the access URL lands (R1, A7): the wizard, the console, the editor.
LANDING_WIZARD = "/wizard"
LANDING_CONSOLE = "/console"
LANDING_EDITOR = "/"


def landing_target(settings: UISettings, state: WizardState | None) -> str:
    """The page the access URL redirects to (R1).

    The wizard on a first run (the base profile does not exist) or while the
    wizard state file says it is unfinished; otherwise the console at the A2
    default location and the editor when ``--config`` was given.
    """

    if not settings.base_path.exists():
        return LANDING_WIZARD
    if state is not None and state.exists and not state.completed:
        return LANDING_WIZARD
    return LANDING_CONSOLE if settings.default_location else LANDING_EDITOR


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
OWNERSHIP_READ_ONLY_REASON = (
    "read-only: input ownership is set in the configuration file; Check reports its diagnostics"
)
OWNERSHIP_UNRESOLVED = "ownership could not be resolved; see Check"
OWNERSHIP_UNOWNED = "unowned"
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
        if isinstance(value, (Mapping, list)):
            return _shown_text(value, _secret_set(self))
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
    "permission denied" is alive. On Windows, where ``os.kill`` terminates
    the process, it is queried instead (:func:`_windows_process_alive`).
    """

    if child is not None and child.pid == pid:
        return child.poll() is None
    if sys.platform == "win32":
        return _windows_process_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _windows_process_alive(pid: int) -> bool:
    """Whether *pid* is live on Windows, without signalling it: a handle is
    opened for query and waited on for zero time. A process that cannot be
    opened for lack of rights is alive; any other refusal is dead."""

    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    synchronize, query_limited_information = 0x00100000, 0x00001000
    access_denied, wait_timeout = 5, 0x00000102
    handle = kernel32.OpenProcess(synchronize | query_limited_information, False, pid)
    if not handle:
        return ctypes.get_last_error() == access_denied
    try:
        return kernel32.WaitForSingleObject(handle, 0) == wait_timeout
    finally:
        kernel32.CloseHandle(handle)


def _read_proc_text(path: str) -> str:
    with open(path, encoding="ascii", errors="replace") as handle:
        return handle.read(4096)


def process_started_at(
    pid: int,
    *,
    read_text: Callable[[str], str] | None = None,
    clock: Callable[[], float] | None = None,
    ticks_per_second: int | None = None,
    platform: str | None = None,
) -> float | None:
    """When process *pid* started, in wall-clock UTC epoch seconds, or
    ``None`` when it cannot be read (phase 10 R2).

    Linux only: field 22 (``starttime``, clock ticks since boot) of
    ``/proc/<pid>/stat``, parsed after the last ``)`` (``comm`` may hold
    spaces and parentheses), over ``SC_CLK_TCK``, added to a boot time of
    ``clock()`` minus the first field of ``/proc/uptime``, both files read in
    this one call. That keeps centisecond precision, where ``btime`` of
    ``/proc/stat`` is whole seconds and would move a start up to 1 s earlier.
    The boot time follows the current wall clock, as ``st_mtime`` and the
    status record's timestamp do; the wall clock is read before
    ``/proc/uptime``, so a delay between the two reads only moves the
    computed start earlier. Any other platform, any ``OSError`` and
    any parse failure is ``None``. The keyword arguments are test seams.
    """

    if (sys.platform if platform is None else platform) != "linux":
        return None
    read = _read_proc_text if read_text is None else read_text
    now = time.time if clock is None else clock
    try:
        stat_text = read(f"/proc/{pid}/stat")
        # The wall clock is sampled before the uptime, never after: a delay
        # between the two samples then moves the boot time, and the start,
        # earlier (a genuine publisher is never judged stale for it) where
        # the other order would move them later by that delay.
        wall = now()
        uptime_text = read("/proc/uptime")
        ticks = os.sysconf("SC_CLK_TCK") if ticks_per_second is None else ticks_per_second
        _comm, closing, rest = stat_text.rpartition(")")
        fields = rest.split()
        # ``rest`` starts at field 3 (state): field 22 is its 20th entry.
        if not closing or len(fields) < 20 or ticks <= 0:
            return None
        starttime = int(fields[19])
        uptime = float(uptime_text.split()[0])
    except (OSError, ValueError, IndexError):
        return None
    if starttime < 0 or not math.isfinite(uptime):
        return None
    return wall - uptime + starttime / ticks


def record_pid_live(
    pid: int,
    published_at: float,
    liveness: Callable[[int], bool],
    started_at: Callable[[int], float | None],
) -> bool:
    """Whether a record published at *published_at* (epoch seconds) names a
    live publisher (phase 10 R2).

    ``liveness(pid)`` is returned unchanged when the pid is not live or its
    start time is unknown; a live process that started strictly after the
    publication is not the publisher, with no tolerance (R2, AC10). A
    genuine publisher started before it wrote its record, and tick
    truncation only moves a computed start earlier, so it is never judged
    stale. A backwards wall-clock step between publication and this
    judgement is outside R2 and may judge a genuine publisher stale.
    """

    live = liveness(pid)
    if not live:
        return live
    started = started_at(pid)
    if started is None:
        return live
    if started > published_at:
        return False
    return live


def _unknown_start(pid: int) -> float | None:
    """The start-time seam that never knows: the judgement is unchanged."""

    return None


def _timestamp_seconds(value: str) -> float | None:
    """A status record timestamp (already validated) as epoch seconds."""

    match = _TIMESTAMP.fullmatch(value)
    if match is None:
        return None
    year, month, day, hour, minute, second, fraction = match.groups()
    try:
        moment = datetime(
            int(year), int(month), int(day), int(hour), int(minute), int(second), tzinfo=timezone.utc
        )
    except ValueError:
        return None
    return moment.timestamp() + int((fraction or "0").ljust(6, "0")) / 1_000_000


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
# :data:`HIDDEN_LITERAL`, and structured data is masked value by value, keys
# included — :func:`_shown`, gate-3 F2); this guard replaces each secret-set
# value, in its raw, HTML and JSON spellings, by ``[hidden]`` anyway, in
# every configured scalar and key as :func:`esc` renders it and in every log
# record. It works on data before it is serialized, never on serialized text,
# the finished body or headers: a secret that happens to equal a path, a
# field name, a JSON delimiter or escape or any other piece of markup cannot
# rewrite it (gate-4 N4); the serialized form of masked data is a
# :class:`_Shown` text, escaped as it is and never redacted again. Every
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


def _redaction_forms(value: str) -> set[str]:
    """Every form *value* can take in a response.

    The raw text, its HTML form, and the body of its JSON string literal (both
    ``ensure_ascii`` spellings) with that body's HTML form: a secret with a
    quote, a backslash or a control character is spelled differently once
    serialized, and a substring of serialized configured data must still be
    caught (gate-3 F2). :func:`_redact` hands these forms to
    :func:`core.contracts.mask_known`, which masks the union of all of them
    against the original text, so no order among them matters (N3).
    """

    spellings = {value}
    for ascii_only in (False, True):
        spellings.add(json.dumps(value, ensure_ascii=ascii_only)[1:-1])
    forms: set[str] = set()
    for spelling in spellings:
        forms.add(spelling)
        forms.add(html.escape(spelling, quote=True))
    return forms


def _is_secret_scalar(value: Any, secret_values: frozenset[str] | set[str]) -> bool:
    """Whether *value* is a configured scalar whose text is a secret-set value."""

    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return False
    return reference_name(value) is None and str(value) in secret_values


class StandInCollision(RuntimeError):
    """A withheld mapping key could not be given a stand-in distinct from
    every other key of its mapping: the page refuses to render rather than
    show one entry in place of another (gate-5 N5). Value-free."""


def _hidden_keys(mapping: Mapping[Any, Any], secret_values: frozenset[str] | set[str]) -> dict[Any, Any]:
    """Every key of *mapping* as :func:`_shown` shows it, each one distinct.

    A key the redaction leaves unchanged is shown as itself and keeps that
    text. Every other key — equal to a secret-set value, or merely holding
    one — gets a stand-in: :data:`HIDDEN_LITERAL` for the former, its
    redacted text for the latter, the second and later of a form numbered
    ``(2)``, ``(3)``… Uniqueness is decided on the final, redacted form
    against every form already taken (the unchanged keys first), so two
    withheld keys, or a withheld key and legitimate text that looks like a
    stand-in, are never shown alike and never merge (gate-5 N5). The
    stand-ins are display text only: no posted text is ever mapped back
    through them (P19F9, see :func:`_patched`). When a secret-set value
    rewrites the numbering itself so that no distinct form can be found, :class:`StandInCollision` is raised:
    the render fails rather than lose an entry.
    """

    shown: dict[Any, Any] = {}
    taken: set[Any] = set()
    withheld: list[Any] = []
    for key in mapping:
        if _is_secret_scalar(key, secret_values) or _shown_key(key, secret_values) != key:
            withheld.append(key)
            continue
        shown[key] = key
        taken.add(key)
    for key in withheld:
        base = HIDDEN_LITERAL if _is_secret_scalar(key, secret_values) else _plain(key)
        for count in range(1, 2 * len(mapping) + 8):
            candidate = _shown_key(base if count == 1 else f"{base} ({count})", secret_values)
            if candidate not in taken:
                break
        else:
            raise StandInCollision("a withheld mapping key has no distinct stand-in")
        taken.add(candidate)
        shown[key] = candidate
    return {key: shown[key] for key in mapping}


def _shown(value: Any, secret_values: frozenset[str] | set[str]) -> Any:
    """*value* as a page shows it, as data: masked, then every scalar and key redacted.

    The one masking every rendering serializes (gate-4 N4): a scalar or key
    equal to a secret-set value is withheld, then the
    text of every string, number and key has each secret-set value replaced
    by ``[hidden]`` — the stand-ins included, so a secret that is a
    substring of :data:`HIDDEN_LITERAL` rewrites the placeholder itself. The
    result is serialized afterwards, so JSON's own delimiters and escapes are
    never taken for secret text. A number whose text holds a secret is shown
    as its redacted text; a boolean or null is a token, never data text
    (gate-2 N1). Every mapping key is shown distinct from its siblings
    (:func:`_hidden_keys`), so no entry is lost (gate-5 N5). It is read-only
    display: a value this changes is never placed in a control whose text
    is posted back (P19F9).
    """

    if isinstance(value, Mapping):
        keys = _hidden_keys(value, secret_values)
        # Distinct by construction: no entry is ever shown in place of another.
        return {keys[key]: _shown(item, secret_values) for key, item in value.items()}
    if isinstance(value, list):
        return [_shown(item, secret_values) for item in value]
    if _is_secret_scalar(value, secret_values):
        value = HIDDEN_LITERAL
    return _shown_key(value, secret_values)


def _shown_key(key: Any, secret_values: frozenset[str] | set[str]) -> Any:
    """A scalar or key as :func:`_shown` shows it: its text redacted."""

    if isinstance(key, str):
        return _redact(key, secret_values)
    if isinstance(key, bool) or key is None:
        return key
    text = _plain(key)
    redacted = _redact(text, secret_values)
    # Any other scalar (a date YAML parsed) is serialized as its text anyway.
    return key if redacted == text and isinstance(key, (int, float)) else redacted


class _Shown(str):
    """Text serialized from :func:`_shown` data: escaped as it is, never
    redacted again (a second pass would rewrite JSON syntax, gate-4 N4)."""


def _shown_text(value: Any, secret_values: frozenset[str] | set[str], *, as_json: bool = False) -> _Shown:
    """*value* masked as data (:func:`_shown`), then serialized.

    Strings are shown as themselves unless *as_json*, which serializes every
    value as JSON (the text a JSON editor posts back).
    """

    shown = _shown(value, secret_values)
    if as_json:
        return _Shown(json.dumps(shown, ensure_ascii=False, default=str))
    return _Shown(_plain(shown))


def _identical(left: Any, right: Any) -> bool:
    """Equal as configured data, ``1``, ``1.0`` and ``true`` told apart."""

    if isinstance(left, Mapping) and isinstance(right, Mapping):
        return left.keys() == right.keys() and all(_identical(left[key], right[key]) for key in left)
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(map(_identical, left, right))
    return type(left) is type(right) and left == right


def _exact_scalar(value: Any, secret_values: frozenset[str] | set[str]) -> bool:
    """Whether a page shows the scalar *value* exactly as it is configured.

    Not a secret-set value, no secret-set value inside its text, and a JSON
    round trip gives it back with its type (a YAML date would come back as
    text, a NaN as another NaN). Only such a value is ever placed in an
    editable control whose untouched text means "unchanged" (P19F9).
    """

    if _is_secret_scalar(value, secret_values):
        return False
    if not _identical(_shown_key(value, secret_values), value):
        return False
    try:
        return _identical(json.loads(json.dumps(value, ensure_ascii=False)), value)
    except (TypeError, ValueError):
        return False


def _tagged(value: Any) -> Any:
    """*value* as JSON-safe data with every scalar's type kept: the material
    of a patch identity (:meth:`_SchemaRenderer._patch_token`)."""

    if isinstance(value, Mapping):
        return ["map", [[_tagged(key), _tagged(item)] for key, item in value.items()]]
    if isinstance(value, (list, tuple)):
        return ["list", [_tagged(item) for item in value]]
    return [type(value).__name__, repr(value)]


# ---------------------------------------------------------------------------
# Patch controls (P19F9)
#
# A setting whose value a page cannot show exactly — a secret-set value or a
# declared credential somewhere inside it, or a secret inside a longer text —
# is never rendered as one text the browser posts back whole: masking is
# lossy, and the old scheme of reconstructing the real value from the masked
# text posted back mistook entries for one another (gate-6 N3, N5, N6). Such a
# value is edited entry by entry instead. Every entry gets an identity
# allocated at render time — a keyed digest of the render, the page, the
# setting's real path, the entry's real path below it and its real value —
# revealed only as an opaque token in the control's name, and mapped back to
# that exact place by rendering the page again over the same configuration.
# A submission carries, per control, its identity and either its untouched
# text (no change) or the new value; the server applies those changes to the
# real value it loaded, so an entry left alone keeps its real value without
# ever being sent, and Check and Save validate the same real result. A part
# the page withheld is never editable in place: it is kept, moved, removed or
# replaced whole.
#
# P19F10: every identity starts with its render's tag — a random tag drawn
# for each rendering of a page and remembered by the session showing it — so
# no identity of an earlier render, or of another session, can ever equal a
# current one (gate-7 N3/N6): such a submission is refused as stale, before
# anything is parsed. A control whose shown text is not its value (an empty
# replacement for a withheld value, an unset setting) posts an explicit
# operation beside it — ``unchanged``, ``set`` or ``clear`` — so "unchanged"
# is never inferred from the text, and any value, the empty string included,
# can be set (gate-7 N7).
# ---------------------------------------------------------------------------

#: The prefix of every patch control's name: ``@patch-<operation>-<token>``.
PATCH_FIELD_PREFIX = "@patch-"
#: The new value of an entry (its JSON text), or the whole replacement of a
#: withheld entry (empty: kept).
PATCH_VALUE = "value"
#: ``true``: drop this list item or mapping entry.
PATCH_REMOVE = "remove"
#: The position of a list item (its index when untouched).
PATCH_ORDER = "order"
#: A new item appended to a list (JSON), on the list's token.
PATCH_ITEM = "item"
#: A new mapping entry's key and its value (JSON), on the mapping's token.
PATCH_KEY = "key"
PATCH_ENTRY = "entry"
#: The explicit operation of a withheld entry's replacement control (P19F10).
PATCH_OPERATION = "op"
#: The prefix of the explicit operation posted beside an ordinary control
#: whose shown text is not its value: ``@op:<field name>`` (P19F10).
OPERATION_FIELD_PREFIX = "@op:"
#: The operations: keep the configured value (whatever the text holds —
#: text typed with it is refused), set the text as the whole new value (the
#: empty string included), or clear the setting from the overlay.
OPERATION_UNCHANGED = "unchanged"
OPERATION_SET = "set"
OPERATION_CLEAR = "clear"
#: Hex characters of the render tag every patch identity starts with: the
#: UI's startup nonce, then its render counter (P19F11).
RENDER_TAG_LENGTH = 32
#: Hex characters of the nonce each UI mints at startup: the first half of
#: every render tag it issues, so no tag of another UI process equals one of
#: its own (P19F11).
RENDER_NONCE_LENGTH = 16
#: The placeholder of a control whose configured text is withheld.
WITHHELD_PLACEHOLDER = "withheld: type a replacement, or leave empty to keep it"


@dataclass(frozen=True)
class _PatchControl:
    """One patch control: the place it edits and the text it posts untouched."""

    page: str
    #: The setting path the whole value is written at.
    top: tuple[Any, ...]
    #: The real keys and indexes of the entry below *top*.
    relative: tuple[Any, ...]
    operation: str
    rendered: str


def _render_tag(name: str) -> str | None:
    """The render tag a posted patch control's identity starts with, or
    ``None`` for a field that is no patch control (P19F10)."""

    if not name.startswith(PATCH_FIELD_PREFIX):
        return None
    _operation, sep, token = name[len(PATCH_FIELD_PREFIX) :].partition("-")
    return token[:RENDER_TAG_LENGTH] if sep else None


class _Cleared:
    """The value of an edit that clears its setting from the overlay: the
    ``clear`` operation (P19F10). One object, kept by every copy."""

    def __deepcopy__(self, memo: Any) -> _Cleared:
        return self

    def __repr__(self) -> str:
        return "<cleared>"


_CLEARED = _Cleared()


class _Refusal(str):
    """A problem that refuses the whole submission before validation: the
    posted changes cannot be applied unambiguously (P19F9). Check reports it
    without running the checker, Save refuses on it; nothing is guessed."""


def _patched(
    real: Any, relative: tuple[Any, ...], changes: Mapping[tuple[tuple[Any, ...], str], str], found: list[str]
) -> Any:
    """*real* with every posted change applied, and *found* extended with
    every reason a change cannot be applied (P19F9).

    *changes* maps ``(relative path, operation)`` to the posted text of each
    control that was not left untouched. An entry with no change is copied
    from *real* as it is: its value never travelled.
    """

    replaced = changes.get((relative, PATCH_VALUE))
    if replaced is not None:
        return _coerce("json", {}, replaced)
    if isinstance(real, Mapping):
        mapping: dict[Any, Any] = {}
        for key, item in real.items():
            child = (*relative, key)
            if not _removed(child, changes, found):
                mapping[key] = _patched(item, child, changes, found)
        key_text = changes.get((relative, PATCH_KEY), "")
        entry_text = changes.get((relative, PATCH_ENTRY), "")
        if key_text or entry_text:
            if not key_text:
                found.append(_NEW_KEY_REASON)
            elif any(_plain(key) == key_text for key in real):
                # Kept or removed: the new entry is never merged into one.
                found.append(_EXISTING_KEY_REASON)
            else:
                mapping[key_text] = _coerce("json", {}, entry_text)
        return mapping
    if isinstance(real, list):
        kept: list[tuple[int, int, Any]] = []
        for index, item in enumerate(real):
            child = (*relative, index)
            if _removed(child, changes, found):
                continue
            position = index
            order = changes.get((child, PATCH_ORDER))
            if order is not None:
                if _INTEGER_TEXT.match(order.strip()):
                    position = int(order.strip())
                else:
                    found.append(_ORDER_REASON)
            kept.append((position, index, _patched(item, child, changes, found)))
        # Equal positions keep their configured order.
        items = [item for _position, _index, item in sorted(kept, key=lambda entry: entry[:2])]
        added = changes.get((relative, PATCH_ITEM))
        if added is not None and added.strip():
            items.append(_coerce("json", {}, added))
        return items
    return _detached(real)


def _removed(
    child: tuple[Any, ...], changes: Mapping[tuple[tuple[Any, ...], str], str], found: list[str]
) -> bool:
    """Whether the entry at *child* is removed; an entry both removed and
    edited is refused, never resolved one way or the other."""

    flag = changes.get((child, PATCH_REMOVE))
    if flag is None:
        return False
    if flag != "true":
        found.append(_REMOVE_FLAG_REASON)
        return False
    for relative, operation in changes:
        if relative[: len(child)] == child and (relative, operation) != (child, PATCH_REMOVE):
            found.append(_REMOVED_AND_EDITED_REASON)
            break
    return True


def _secret_set(view: ConfigView | None = None) -> frozenset[str]:
    """The secret-set values in force: *view*'s and the rendering UI's."""

    source = _RENDERING_FOR.get()
    values = frozenset() if view is None else view.secret_values
    return values if source is None else values | source.secret_values


def _redact(text: str, secret_values: Iterable[str]) -> str:
    """*text* with every secret-set value, in every response form, masked as ``[hidden]``.

    One call of the shared contract, :func:`core.contracts.mask_known`: the
    union of every form of every value is located in the original text and
    masked in one pass (R8, N3).
    """

    return mask_known(text, secret_values, marker=REDACTED, forms=_redaction_forms)


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


def _live_secret_values() -> frozenset[str]:
    """The secret set of every live :class:`ConfigUI`, as the log filter sees it."""

    return frozenset(value for source in list(_LOG_REDACTION.sources) for value in source.secret_values)


# ---------------------------------------------------------------------------
# Rendering (R4, R9)
#
# Every configured value goes through :func:`esc`; the CSS and the script are
# inline constants, every form posts to a relative path and no page names an
# external URL. No page is written for any module: the base page lists what
# discovery found.
# ---------------------------------------------------------------------------


def esc(value: Any) -> str:
    """HTML-escape *value* (quotes included), as text, secret-set values redacted.

    A string is redacted as the text it is; structured data is masked value
    by value and key by key before it is serialized (:func:`_shown_text`);
    a :class:`_Shown` text is already masked and is only escaped (gate-4 N4).
    """

    return html.escape(_guarded(value), quote=True)


def _guarded(value: Any) -> str:
    """*value* as :func:`esc` shows it, before HTML escaping: redacted."""

    if isinstance(value, _Shown):
        return value
    source = _RENDERING_FOR.get()
    secret_values = frozenset() if source is None else source.secret_values
    if isinstance(value, str):
        return value if source is None else _redact(value, secret_values)
    return _shown_text(value, secret_values)


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


def _layout_field(view: ConfigView) -> str:
    """The hidden field carrying *view*'s key layout (see :func:`_stale_layout`)."""

    return f'<input type="hidden" name="{LAYOUT_FIELD}" value="{ident(_view_layout(view))}">'


def _write_guard(session: _Session, view: ConfigView) -> str:
    """The hidden fields every form that saves, removes or checks carries."""

    return (
        f"{_csrf_field(session)}"
        f'<input type="hidden" name="{FINGERPRINT_FIELD}" value="{ident(_view_fingerprint(view))}">'
        f"{_layout_field(view)}"
    )


#: The field naming the page a form was posted from, so Check answers with
#: that page's view of the diagnostics (R5).
PAGE_FIELD = "page"
#: The editor base page's one link to the console (plan DD7).
CONSOLE_LINK = '<p><a href="/console">Console</a></p>'
CORE_PAGE = "/core"

#: Posts the enclosing form, unsaved edits included, to Check (R5).
#: ``formnovalidate``: a value the browser's constraints reject (a number
#: below its minimum) still reaches Check, whose diagnostics name it.
_CHECK_BUTTON = '<button type="submit" formaction="/check" formnovalidate>Check</button>'


def _page_field(page: str) -> str:
    return f'<input type="hidden" name="{PAGE_FIELD}" value="{ident(page)}">'


def _page(title: str, session: _Session, body: str, *, language: str = "en") -> UIResponse:
    document = (
        f"<!doctype html><html lang=\"{html.escape(language, quote=True)}\"><head><meta charset=\"utf-8\">"
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


def _withheld_attributes() -> str:
    """The attributes of an empty control standing for a withheld value."""

    return f' placeholder="{ident(WITHHELD_PLACEHOLDER)}" data-withheld="true"'


def _operation_select(name: str, clearable: bool) -> str:
    """The explicit operation posted beside a control named *name* whose
    shown text is not its value (P19F10, gate-7 N7): ``unchanged`` is
    selected, so a control left alone sends no edit, whatever its text."""

    labels = [(OPERATION_UNCHANGED, "keep the current value"), (OPERATION_SET, "set to the text typed")]
    if clearable:
        labels.append((OPERATION_CLEAR, "clear this setting"))
    options = "".join(
        f'<option value="{value}"{" selected" if value == OPERATION_UNCHANGED else ""}>{label}</option>'
        for value, label in labels
    )
    return f'<select name="{name}" data-operation="true">{options}</select>'


def _editable(text: str) -> str:
    """The text a scalar control holds: *text*, or ``""`` when the page cannot
    show it exactly — the control is then an empty replacement (P19F9)."""

    return text if _guarded(text) == text else ""


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

    def __init__(self, view: ConfigView, page: str = "", render: str = "") -> None:
        self.view = view
        #: The page being rendered: a patch identity belongs to one page.
        self.page = page
        #: The tag of this rendering: a patch identity belongs to one render
        #: (P19F10); see :meth:`ConfigUI._new_render`.
        self.render = render
        #: Field name → whether its explicit operation offers ``clear``, for
        #: every control rendered with one (P19F10).
        self.operations: dict[str, bool] = {}
        self.controls: dict[str, _Control] = {}
        #: ``new_entry_*`` field suffix → the mapping path a new entry joins.
        self.entry_mappings: dict[str, tuple[Any, ...]] = {}
        #: Patch control name → the place it edits (P19F9).
        self.patches: dict[str, _PatchControl] = {}
        #: Field name → path of a value edited entry by entry, whose whole
        #: text is never posted (P19F9).
        self.withheld: dict[str, tuple[Any, ...]] = {}

    def register(
        self,
        path: Sequence[Any],
        kind: str,
        rendered: str,
        schema: Mapping[str, Any] | None = None,
        *,
        structural: bool = False,
    ) -> None:
        # Keyed by the name the control renders and holding the text it posts
        # untouched: both are what the page shows, secret-set values redacted.
        # A *structural* token (an option value the UI or the schema declares,
        # never configured data) is posted exactly as generated, so it is
        # held unredacted too (gate-2 N1).
        held = rendered if structural else _guarded(rendered)
        self.controls[_field_name(path, self.view)] = _Control(tuple(path), kind, held, schema or {})

    # -- values -------------------------------------------------------------

    def origin(self, path: Sequence[Any]) -> str:
        return ConfigUI._origin(self.view, path)

    def _masked(self, path: Sequence[Any], value: Any, *, as_json: bool = False) -> _Shown:
        """*value* shown with every nested credential literal and secret value withheld (R8).

        Declared credential paths are withheld whatever they hold; then every
        scalar or mapping key equal to a secret-set value is withheld, and
        every secret-set value in any other scalar or key redacted, whatever
        its path (gate-3 F2) — on the data, before it is serialized (gate-4 N4).
        """

        masked = self._credentials_masked(path, value)
        return _shown_text(masked, _secret_set(self.view), as_json=as_json)

    def _credentials_masked(self, path: Sequence[Any], value: Any) -> Any:
        if reference_name(value) is not None:
            return value
        if self.view.is_credential(path):
            return HIDDEN_LITERAL
        if isinstance(value, Mapping):
            return {key: self._credentials_masked((*path, key), item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._credentials_masked((*path, index), item) for index, item in enumerate(value)]
        return value

    def configured_text(self, path: Sequence[Any]) -> str | None:
        """The unresolved configured text at *path*, credentials hidden."""

        value = self.view.value(path)
        if value is _MISSING:
            return None
        return self._masked(path, value)

    def _json_text(self, path: Sequence[Any]) -> str:
        value = self.view.value(path)
        if value is _MISSING:
            return ""
        return self._masked(path, value, as_json=True)

    # -- patch editors (P19F9) ------------------------------------------------

    def _exact(self, path: Sequence[Any], value: Any) -> bool:
        """Whether the page shows *value* at *path* exactly: no declared
        credential literal, no secret-set value, no secret inside any text,
        key or value, and a JSON round trip gives it back unchanged."""

        secret_values = _secret_set(self.view)
        if reference_name(value) is None and self.view.is_credential(path):
            return False
        if isinstance(value, Mapping):
            return all(
                isinstance(key, str)
                and _exact_scalar(key, secret_values)
                and self._exact((*path, key), item)
                for key, item in value.items()
            )
        if isinstance(value, list):
            return all(self._exact((*path, index), item) for index, item in enumerate(value))
        return _exact_scalar(value, secret_values)

    def _json_editor(self, path: Sequence[Any], schema: Mapping[str, Any], role: str, rows: int) -> str:
        """The editor of the structured value at *path*.

        A value the page shows exactly is one JSON text area, as ever: its
        untouched text means "unchanged", and an edit is the new value. Any
        other value is edited entry by entry (:meth:`_patch_node`); its
        field name is then refused if posted (:data:`_WITHHELD_WHOLE_REASON`).
        """

        name = _field_name(path, self.view)
        value = self.view.value(path)
        if value is _MISSING or self._exact(path, value):
            text = self._json_text(path)
            self.register(path, "json", text, schema)
            return f'<textarea name="{ident(name)}" data-json="{role}" rows="{rows}">{esc(text)}</textarea>'
        self.withheld[name] = tuple(path)
        return (
            f'<div class="patch" data-json="{role}" data-patch-field="{ident(name)}">'
            f"{self._patch_node(tuple(path), (), value)}</div>"
        )

    def _patch_token(self, top: tuple[Any, ...], relative: tuple[Any, ...], value: Any) -> str:
        """The opaque identity of the entry at *relative* below *top*.

        The render's tag, then a keyed digest of the render, the page, the
        setting's real path (its configured keys themselves, never their
        positions: gate-7 N6), the entry's real path and its real value.
        Unique per render and place, meaningless without this UI's field
        key, and unknown once the entry's value or place changed. The tag is
        this UI's startup nonce and its render counter, never issued twice
        (P19F11), so an identity drawn by an earlier render, in another
        session or by another UI process never equals a current one: it is
        refused, never retargeted. No configured key or value appears in it.
        """

        source = _RENDERING_FOR.get()
        key = _PROCESS_FIELD_KEY if source is None else source.field_key
        material = json.dumps(
            [self.render, self.page, _tagged(top), _tagged(relative), _tagged(value)],
            ensure_ascii=False,
        )
        return self.render + hmac.new(key, material.encode("utf-8"), hashlib.sha256).hexdigest()[:32]

    def _patch_control(
        self, operation: str, token: str, top: tuple[Any, ...], relative: tuple[Any, ...], rendered: str
    ) -> str:
        name = f"{PATCH_FIELD_PREFIX}{operation}-{token}"
        self.patches[name] = _PatchControl(self.page, top, relative, operation, rendered)
        return ident(name)

    def _patch_node(self, top: tuple[Any, ...], relative: tuple[Any, ...], value: Any) -> str:
        """The controls of the entry at *relative* below *top*.

        Shown exactly: one control holding its JSON text. A list or mapping
        that is not: an entry per item or key, each with its own identity,
        a position (list) and a removal flag, and a row adding one entry. A
        withheld scalar or credential: its masked text, read-only, and an
        empty replacement control.
        """

        path = (*top, *relative)
        token = self._patch_token(top, relative, value)
        if self._exact(path, value):
            # Exact data: no secret in it, so only syntax is serialized (gate-4 N4).
            text = _Shown(json.dumps(value, ensure_ascii=False))
            name = self._patch_control(PATCH_VALUE, token, top, relative, text)
            if isinstance(value, (Mapping, list)):
                return f'<textarea name="{name}" data-patch="value" rows="2">{esc(text)}</textarea>'
            return f'<input type="text" name="{name}" data-patch="value" value="{esc(text)}">'
        withheld = reference_name(value) is None and self.view.is_credential(path)
        if isinstance(value, Mapping) and not withheld:
            rows: list[str] = []
            for position, (key, item) in enumerate(value.items()):
                child = (*relative, key)
                remove = self._patch_control(
                    PATCH_REMOVE, self._patch_token(top, child, item), top, child, ""
                )
                rows.append(
                    f'<li class="patch-entry" data-entry="{_key_token(position)}">'
                    f'<span class="patch-key">{_key_label(key, self.view)}</span> '
                    f"{self._patch_node(top, child, item)} "
                    f'<label><input type="checkbox" name="{remove}" value="true"> remove</label></li>'
                )
            key_name = self._patch_control(PATCH_KEY, token, top, relative, "")
            entry_name = self._patch_control(PATCH_ENTRY, token, top, relative, "")
            rows.append(
                f'<li class="patch-new">New entry <input type="text" name="{key_name}"> '
                f'value (JSON) <textarea name="{entry_name}" rows="1"></textarea></li>'
            )
            return f'<ul class="patch-mapping">{"".join(rows)}</ul>'
        if isinstance(value, list) and not withheld:
            rows = []
            for index, item in enumerate(value):
                child = (*relative, index)
                child_token = self._patch_token(top, child, item)
                order = self._patch_control(PATCH_ORDER, child_token, top, child, str(index))
                remove = self._patch_control(PATCH_REMOVE, child_token, top, child, "")
                rows.append(
                    '<li class="patch-entry">'
                    f'<label>position <input type="number" step="1" name="{order}" value="{index}"></label> '
                    f"{self._patch_node(top, child, item)} "
                    f'<label><input type="checkbox" name="{remove}" value="true"> remove</label></li>'
                )
            item_name = self._patch_control(PATCH_ITEM, token, top, relative, "")
            rows.append(
                f'<li class="patch-new">New item (JSON) <textarea name="{item_name}" rows="1"></textarea></li>'
            )
            return f'<ol class="patch-list" start="0">{"".join(rows)}</ol>'
        name = self._patch_control(PATCH_VALUE, token, top, relative, "")
        # The empty replacement says nothing by its text: its operation does
        # (gate-7 N7). Removal is the entry's own removal flag.
        operation = self._patch_control(PATCH_OPERATION, token, top, relative, OPERATION_UNCHANGED)
        return (
            f'<code class="value withheld">{esc(self._masked(path, value))}</code> '
            f'<input type="text" name="{name}" data-patch="replace" value=""{_withheld_attributes()}> '
            f"{_operation_select(operation, False)}"
        )

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
        return f'<code class="value">{esc(self._masked(path, value))}</code>'

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
            return self._text_input(path, kind, schema, name, value)
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
            # The members are the schema's declared tokens, not configured
            # data: redacting one would post a value outside the enum (gate-2 N1).
            self.register(path, kind, _plain(chosen), schema, structural=True)
            options = "".join(
                f'<option value="{ident(_plain(member))}"'
                f'{" selected" if value is not _MISSING and value == member else ""}>'
                f"{ident(_plain(member))}</option>"
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
            return self._text_input(path, kind, schema, name, shown, "number", f'{bounds} step="{step}"')
        shown = "" if value is _MISSING else _plain(value)
        return self._text_input(path, kind, schema, name, shown)

    def _text_input(
        self,
        path: Sequence[Any],
        kind: str,
        schema: Mapping[str, Any],
        name: str,
        text: str,
        control: str = "text",
        attributes: str = "",
    ) -> str:
        """An input holding *text*, or an empty replacement input when the
        page cannot show *text* exactly (P19F9).

        A text the redaction would change is never placed in the control:
        posted back edited, it could not be told from literal text, and
        reading it back would mean reconstructing the withheld part (gate-6
        N3). The control starts empty — left empty it keeps the configured
        value, and any text typed is the whole new value. The current value
        is shown masked beside it (``current:``).
        """

        configured = self.view.value(path)
        if _guarded(text) != text:
            self.register(path, kind, "", schema)
            return (
                f'<input type="text" name="{name}" value=""{_withheld_attributes()}> '
                f"{self._operation(path, name, configured)}"
            )
        self.register(path, kind, text, schema)
        markup = f'<input type="{control}" name="{name}"{attributes} value="{esc(text)}">'
        if configured is _MISSING:
            # Empty says "not set" here, so setting the empty string needs
            # the explicit operation too (gate-7 N7).
            markup += f" {self._operation(path, name, configured)}"
        return markup

    def _operation(self, path: Sequence[Any], name: str, configured: Any) -> str:
        """The explicit operation of the control *name* at *path* (P19F10):
        ``clear`` is offered when the overlay holds the setting, and never
        for one inside a list (a trigger rule's parameter, say): the overlay
        list replaces the base list wholesale, so deleting a field from it
        could not bring the base value back (P19F10 review A1)."""

        clearable = (
            configured is not _MISSING
            and self.view.origin(path) == ORIGIN_OVERLAY
            and not any(isinstance(segment, int) and not isinstance(segment, bool) for segment in path)
        )
        self.operations[_field_name(path, self.view)] = clearable
        return _operation_select(f"{OPERATION_FIELD_PREFIX}{name}", clearable)

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

        Gate-2 N1: the option values (``""``, ``true``, ``false``,
        ``_KEEP_NON_BOOLEAN``) and the labels built from a boolean are the
        UI's own tokens, so they are never redacted: a short secret occurring
        in ``true`` once made the option post ``t[hidden]ue``, a string. Only
        a configured non-boolean value is data, and it alone goes through
        :func:`esc`.
        """

        default = schema.get("default")
        if value is _MISSING:
            effective = _plain(default) if isinstance(default, bool) else ""
            inherit = f"not set (default: {effective})" if effective else "not set"
            current: tuple[str, str] | None = ("", ident(inherit))
            shown_effective = ident(effective)
        elif isinstance(value, bool):
            effective, current = _plain(value), None
            shown_effective = ident(effective)
        else:
            effective = _plain(value)
            current = (_KEEP_NON_BOOLEAN, f"{esc(effective)} (not a boolean)")
            shown_effective = esc(effective)
        rendered = current[0] if current is not None else effective
        self.register(path, "boolean", rendered, schema, structural=True)
        choices = ([current] if current is not None else []) + [
            (option, option) for option in ("true", "false")
        ]
        options = "".join(
            f'<option value="{ident(option)}"{" selected" if option == rendered else ""}>'
            f"{label}</option>"
            for option, label in choices
        )
        return (
            f'<select name="{name}" data-kind="boolean" data-effective="{shown_effective}">'
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
        """A list editor: one JSON text area, validated server-side, or a
        patch editor when the page cannot show the list exactly (P19F9)."""

        editor = self._json_editor(path, schema, "list", 3) if live else ""
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
                rows.append(
                    f'<div class="entry" data-entry="{_key_token(position)}">'
                    f"<label>Entry {_key_label(key, self.view)} "
                    f"{self._json_editor(entry_path, schema, 'entry', 2)}</label> "
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
    #: Page → the tag of the rendering of it this session currently shows
    #: (P19F10): only that render's patch identities are accepted from it.
    renders: dict[str, str] = field(default_factory=dict)


Handler = Callable[[UIRequest, _Session], UIResponse]

#: The GET-only, token-authenticated route a second launch probes (R1).
INSTANCE_ROUTE = "/instance"

#: Paths whose requests change state: POST only, Origin- and CSRF-checked.
STATE_CHANGING_PATHS = frozenset(
    {
        "/save",
        "/remove",
        "/check",
        "/apply",
        LANGUAGE_ROUTE,
        console.START_ROUTE,
        console.STOP_ROUTE,
        console.REVOKE_ROUTE,
    }
)

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


def _stored_credentials(settings: UISettings) -> dict[str, str]:
    """The ``{NAME: value}`` the profile's credential file holds (A1, R4).

    Empty unless the store is usable for this profile: the platform names a
    credential directory, the profile's wizard state file exists and loads,
    the credential file exists, and the state owns it byte for byte. Nothing
    is created or opened before each of those holds, so a profile without a
    wizard state leaves the per-user directory untouched.
    """

    store = CredentialStore(settings.base_path, locations.running_platform(), os.environ)
    if store.path is None or not os.path.lexists(state_path(settings.base_path)):
        return {}
    if not os.path.lexists(store.path):
        return {}
    try:
        state = WizardState(settings.base_path)
    except WizardStateError:
        return {}
    values = store.read(state)
    return values if isinstance(values, dict) else {}


class _LaunchEnviron(Mapping[str, str]):
    """The environment a UI-launched main process is given (R4, DD9).

    The UI's current environment (stored credentials included) plus
    :data:`FEED_FILE_VARIABLE` naming the feed path, read at each launch so a
    refreshed environment reaches the next one. The access token is never
    part of it.
    """

    def __init__(self, environ: Callable[[], Mapping[str, str]], feed_path: Path) -> None:
        self._environ = environ
        self._feed = os.fspath(feed_path)

    def __getitem__(self, name: str) -> str:
        if name == FEED_FILE_VARIABLE:
            return self._feed
        return self._environ()[name]

    def __iter__(self) -> Iterator[str]:
        yield from (name for name in self._environ() if name != FEED_FILE_VARIABLE)
        yield FEED_FILE_VARIABLE

    def __len__(self) -> int:
        return sum(1 for _ in self)


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
        wizard_seams: WizardSeams | None = None,
        started_at: Callable[[int], float | None] | None = None,
        token: str | None = None,
    ) -> None:
        self.settings = settings
        inherited: Mapping[str, str] = os.environ if environ is None else environ
        self._inherited = inherited
        stored = _stored_credentials(settings)
        self._stored = stored
        #: The UI process's own environment overlaid with the stored
        #: credentials (the stored value wins for the names it holds): "set/
        #: unset" is judged here, and Check, Apply and a UI-launched main
        #: process are given it (A3, R4). Without a usable credential file it
        #: is the given environment itself.
        self.environ: Mapping[str, str] = {**inherited, **stored} if stored else inherited
        #: The secret set of the last configuration snapshot (R8), seeded
        #: with the stored values and their redaction forms (R4).
        self.secret_values: frozenset[str] = frozenset(
            form for value in stored.values() if value for form in _redaction_forms(value)
        )
        self._secrets_lock = threading.Lock()
        #: The access token: a validated ``--token-file`` value, else minted
        #: per start (phase 10 A5); checked the same way whatever its source.
        self.token = secrets.token_urlsafe(32) if token is None else token
        #: This serving process's instance marker (R1): 128 random bits,
        #: distinct from the token, answered by ``/instance`` and written to
        #: the instance record, so a second launch can tell this UI from
        #: another program holding a reused pid or port.
        self.instance_marker = secrets.token_hex(16)
        #: Keys the opaque field names of :func:`_field_name` (R8).
        self.field_key = secrets.token_bytes(32)
        #: Keys the HMAC of each credential value a wizard screen's
        #: fingerprint covers (R5): minted per process, held in memory only,
        #: never written to the state file, a log record or a response.
        self.fingerprint_key = secrets.token_bytes(32)
        #: Keys the opaque token each Revoke control posts for its rule
        #: (:func:`console.grant_token`): a rule id may hold a secret-set
        #: value the page shows hidden, so the id itself is never posted.
        self.grant_key = secrets.token_bytes(32)
        self.authorities = accepted_authorities(
            settings.host, settings.port, settings.allowed_hosts
        )
        self.base_path = settings.base_path
        #: The first-run decision, taken once here (A2): a profile found at
        #: the target later appeared after detection, and screen 4 refuses
        #: to write over it (R5, AC16).
        self.first_run = not self.base_path.exists()
        #: What the wizard reaches outside the process (R5, D8).
        self.wizard_seams = WizardSeams() if wizard_seams is None else wizard_seams
        self._wizard: Wizard | None = None
        self._wizard_lock = threading.Lock()
        self.overlay_path = settings.overlay_path
        #: The one file a Save or Remove may replace, resolved once here (R6).
        self._managed_overlay = settings.overlay_path
        self.status_path = settings.status_path
        self._sessions: OrderedDict[str, _Session] = OrderedDict()
        self._sessions_lock = threading.Lock()
        #: Every render tag this UI issues is this nonce then the next count
        #: (P19F11): the counter only grows and the nonce is minted here, so
        #: no tag is ever issued twice, by this UI or by another, and no
        #: history of issued tags is kept (none can be evicted and reissued).
        self._render_nonce = secrets.token_hex(RENDER_NONCE_LENGTH // 2)
        self._render_count = 0
        self._mutation_lock = threading.Lock()
        #: Runs the settings phase of Check; the seam exists for unit tests,
        #: production always uses :func:`_default_checker` (R5).
        self.checker: Checker = _default_checker if checker is None else checker
        #: The verdict of the last Check, shown as readiness (R4).
        self.last_check: CheckResult | None = None
        #: The liveness probe of a status record's process; a seam for tests,
        #: production always uses :func:`process_alive` (R7).
        self.probe: Probe = process_alive if probe is None else probe
        #: The start-time reader of the status record's stale rule (phase
        #: 10 R2); a seam for tests. ``None`` is :func:`process_started_at` while
        #: :attr:`probe` is the default, and no start time under an injected
        #: probe: a fake probe's pid is no process of its record (fixtures
        #: hold fixed past timestamps and pids such as 4242, which a real
        #: process may hold), so the real reader would change its judgement.
        self.started_at: Callable[[int], float | None] | None = started_at
        #: Stops and starts the main process this UI launched (R7, A4).
        self.supervisor = (
            Supervisor(
                default_launch_argv(settings),
                self.status_path,
                environ=_LaunchEnviron(lambda: self.environ, settings.feed_path),
            )
            if supervisor is None
            else supervisor
        )
        if supervisor is None:
            # Every child Supervisor.start returns (console Start, Apply's
            # restart) is recorded as launched (phase 10 R6).
            self.supervisor.on_start = self._supervision_launched
        #: The supervision record this UI publishes (phase 10 R6): set only
        #: by main's claim path; while ``None`` no transition writes one.
        self._supervision: Path | None = None
        self._supervision_lock = threading.Lock()
        #: The last Apply's report, shown on the pages (R7).
        self.last_apply: ApplyReport | None = None
        #: Called once by :func:`serve` when the listener accepts connections,
        #: off the event loop; :func:`main` sets it to open the browser (A3).
        self.on_listening: Callable[[], object] | None = None
        #: ``--start-runtime`` has run (or was declined) for this UI start.
        self._listening_start_done = False
        _LOG_REDACTION.sources.add(self)
        self.routes: dict[str, tuple[frozenset[str], Handler]] = {
            "/": (frozenset({"GET"}), self._base_page),
            "/core": (frozenset({"GET"}), self._core_page),
            MODULE_PAGE_PREFIX: (frozenset({"GET"}), self._module_page),
            "/save": (frozenset({"POST"}), self._save_endpoint),
            "/remove": (frozenset({"POST"}), self._remove_endpoint),
            "/check": (frozenset({"POST"}), self._check_endpoint),
            "/apply": (frozenset({"POST"}), self._apply_endpoint),
            INSTANCE_ROUTE: (frozenset({"GET"}), self._instance_endpoint),
            WIZARD_ROUTE: (frozenset({"GET", "POST"}), self._wizard_endpoint),
            LANGUAGE_ROUTE: (frozenset({"POST"}), self._language_endpoint),
            LANDING_CONSOLE: (frozenset({"GET"}), self._console_page),
            console.FEED_ROUTE: (frozenset({"GET"}), self._feed_endpoint),
            console.START_ROUTE: (frozenset({"POST"}), self._start_endpoint),
            console.STOP_ROUTE: (frozenset({"POST"}), self._stop_endpoint),
            console.REVOKE_ROUTE: (frozenset({"POST"}), self._revoke_endpoint),
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

    def _new_render(self, session: _Session, page: str) -> str:
        """A fresh tag for one rendering of *page*, now the one *session*
        shows (P19F10): every identity it draws starts with it, and the
        identities of the render it replaces are refused from now on."""

        with self._sessions_lock:
            self._render_count += 1
            width = RENDER_TAG_LENGTH - RENDER_NONCE_LENGTH
            tag = f"{self._render_nonce}{self._render_count:0{width}x}"
            session.renders[page] = tag
        return tag

    def _live_render(self, page: str, fields: Sequence[tuple[str, str]]) -> str:
        """The render tag the patch identities of *fields* carry, when it is
        one some session currently shows for *page*; else ``""`` (no
        identity is then known). For :meth:`parse_edits` called without a
        session; the routes pass theirs."""

        tags = {_render_tag(name) for name, _text in fields} - {None}
        if len(tags) != 1:
            return ""
        tag = tags.pop()
        with self._sessions_lock:
            live = any(session.renders.get(page) == tag for session in self._sessions.values())
        return tag if live else ""

    def _stale_submission(
        self, fields: Sequence[tuple[str, str]], view: ConfigView, session: _Session, page: str
    ) -> WriteResult | None:
        """The one guard Check, Save and Remove run on a posted form before
        anything of it is parsed, checked or written (P19F11, gate-8 N3/N6):
        a stale result when its key layout is not the current one, or when
        any patch identity it posts is not one the render *session* shows
        for *page* drew over the current configuration. An earlier render,
        another session, another UI process, another page, an altered
        identity or an entry changed on disk since: one class, one outcome
        (409, "reload the page"), identical on every route. The decision is
        membership in the current render's identities, never recognition of
        a past one, so no forgotten history can let an identity through."""

        stale = _stale_layout(fields, view)
        if stale is not None:
            return stale
        names = [name for name, _text in fields if name.startswith(PATCH_FIELD_PREFIX)]
        if not names:
            return None
        with self._sessions_lock:
            current = session.renders.get(page)
        if current is not None and all(_render_tag(name) == current for name in names):
            drawn = self._controls(view, current).patches
            if all(name in drawn and drawn[name].page == page for name in names):
                return None
        return WriteResult(OUTCOME_STALE, (_STALE_IDENTITY_REASON,))

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
        if request.path == INSTANCE_ROUTE:
            # An authenticated route, not a session exception (R1): Host,
            # method, then the access token exactly as the access URL checks
            # it; no session is created.
            methods, handler = self.routes[INSTANCE_ROUTE]
            if method not in methods:
                return _text(405, "405 Method Not Allowed", (("Allow", ", ".join(sorted(methods))),))
            presented = request.query.get("token")
            if presented is None or not _equal(presented, self.token):
                return _text(401, _UNAUTHORIZED)
            return handler(request, None)
        if method == "GET" and request.path == "/" and "token" in request.query:
            if not _equal(request.query["token"], self.token):
                return _text(401, _UNAUTHORIZED)
            session_id = self._new_session()
            return UIResponse(
                status=303,
                headers=(
                    ("Location", self._landing()),
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
        try:
            if request.path in STATE_CHANGING_PATHS:
                with self._mutation_lock:
                    return handler(request, session)
            return handler(request, session)
        except LanguageUnavailable as refused:
            # The session's language no longer validates: refuse with the
            # plain cause, never render another language (P19F3).
            return _text(503, refused.cause)

    def _instance_endpoint(self, request: UIRequest, session: _Session | None) -> UIResponse:
        """``/instance`` (R1): the instance marker and the product version,
        nothing else; reached only through the token check of :meth:`_guarded`."""

        document = {"marker": self.instance_marker, "version": locations.product_version()}
        return UIResponse(
            status=200,
            body=json.dumps(document, sort_keys=True).encode("utf-8"),
            content_type="application/json",
        )

    # -- the first-run wizard (R5, A7) ---------------------------------------

    @property
    def wizard(self) -> Wizard:
        """The wizard over this UI's profile, built on first use with the
        first-run decision and the stored credentials taken at construction;
        it never calls the supervisor."""

        with self._wizard_lock:
            if self._wizard is None:
                self._wizard = Wizard(
                    self.base_path,
                    key=self.fingerprint_key,
                    table=StringTable.load(),
                    first_run=self.first_run,
                    stored=self._stored,
                    environ=lambda: self.environ,
                    checker=self._profile_check,
                    runner=_run_socket_free,
                    seams=self.wizard_seams,
                    on_stored=self._credentials_stored,
                    on_submitted=self._credential_submitted,
                    escape=esc,
                )
            return self._wizard

    def _profile_check(
        self, path: Path, environ: Mapping[str, str]
    ) -> tuple[bool, Sequence[str]]:
        """Screen 4's check: the profile as ``--check-config`` reads it."""

        return self.checker(path, environ, self.overlay_path, None)  # type: ignore[arg-type]

    def _credential_submitted(self, value: str) -> None:
        """A submitted credential joins the secret set at once (R4)."""

        forms = _redaction_forms(value)
        with self._secrets_lock:
            self.secret_values = self.secret_values | forms

    def _credentials_stored(self, values: Mapping[str, str]) -> None:
        """Screen 4 stored *values*: they join the secret set and, the
        stored value winning, the environment of Check, Apply, the dry run
        and a UI-launched main process (R4, plan P7)."""

        self._stored = dict(values)
        forms = {form for value in values.values() if value for form in _redaction_forms(value)}
        with self._secrets_lock:
            self.secret_values = self.secret_values | forms
        self.environ = {**self._inherited, **values} if values else self._inherited

    def _wizard_open(self) -> bool:
        return self._landing() == LANDING_WIZARD

    def _wizard_response(self, page: WizardPage, session: _Session) -> UIResponse:
        response = _page(page.title, session, page.body, language=page.language)
        return UIResponse(
            status=page.status,
            body=response.body,
            content_type=response.content_type,
            headers=response.headers,
        )

    def _wizard_endpoint(self, request: UIRequest, session: _Session) -> UIResponse:
        """``/wizard`` (R5): the screens while the wizard is unfinished; once
        it is completed — or for a profile it never ran for — every request
        is redirected to the console (A7)."""

        if not self._wizard_open():
            return UIResponse(status=303, headers=(("Location", LANDING_CONSOLE),))
        if request.method.upper() == "POST":
            with self._mutation_lock:
                page = self.wizard.post(_form_fields(request), _csrf_field(session))
        else:
            page = self.wizard.get(request.query, _csrf_field(session))
        return self._wizard_response(page, session)

    def _language_endpoint(self, request: UIRequest, session: _Session) -> UIResponse:
        """``/language`` (R2, DD3): persist the chosen language in the state
        file, then back to the landing page. A language not offered changes
        nothing."""

        fields = dict(_form_fields(request))
        language = fields.get("language", "")
        if not self.wizard.set_language(language):
            if language in self.wizard.table.refused:
                return _text(409, f"409 Conflict: {self.wizard.table.cause(language)}")
            return _text(409, "409 Conflict: this language is not offered.")
        return UIResponse(status=303, headers=(("Location", self._landing()),))

    def _landing(self) -> str:
        """:func:`landing_target` over the wizard state file read now. A state
        file that cannot be read is not a finished one: the wizard reports it."""

        try:
            # The canonical base, so the state read is the file startup
            # checked as settings.wizard_path ("~" and references expanded).
            state = WizardState(self.settings.base_path)
        except WizardStateError:
            return LANDING_WIZARD
        return landing_target(self.settings, state)

    # -- the console (R7, R2) -------------------------------------------------

    def _console_page(self, request: UIRequest, session: _Session) -> UIResponse:
        """``/console`` (R7): the runtime state from the status record and
        the supervisor's child, its Start or Stop control, each enabled
        module, the feed newest first, the configured grants and the editor
        link, in the chosen language. While the wizard is unfinished it is
        the landing page instead (A7)."""

        if self._wizard_open():
            return UIResponse(status=303, headers=(("Location", LANDING_WIZARD),))
        return self._console_response(session)

    def _console_response(
        self, session: _Session, outcome: console.RuntimeOutcome | None = None
    ) -> UIResponse:
        """The console page, with *outcome* (a Start, Stop or Revoke report) first."""

        view = self.view()
        running = self.running_state()
        feed = console.read_feed(self.settings.feed_path)
        record = running.live_record
        health = console.module_health(
            view.enabled_modules(), None if record is None else record.modules, feed
        )
        wizard = self.wizard
        language = wizard.language
        body = console.render_console(
            console.runtime_state(
                alive=self._holds_status_record(running),
                supervised=running.supervised,
                starting=self._child_alive(),
            ),
            feed,
            health,
            console.grants_of(view.rules(), any_action=ANY_ACTION, key=self.grant_key),
            language,
            wizard.table,
            escape=esc,
            csrf_field=_csrf_field(session),
            revoke_fields=(
                f"{_csrf_field(session)}"
                f'<input type="hidden" name="{FINGERPRINT_FIELD}" value="{ident(_view_fingerprint(view))}">'
            ),
            outcome=outcome,
        )
        if outcome is None:
            title = wizard.table.sentence(language, "console.title")
        else:
            title = console.outcome_title(outcome, language, wizard.table)
        return _page(title, session, body, language=language)

    def _start_endpoint(self, request: UIRequest, session: _Session) -> UIResponse:
        """``POST /start`` (R7): :meth:`start_runtime`, then the console with
        its report. While the wizard is unfinished nothing is started: the
        wizard is the landing page (A7)."""

        if self._wizard_open():
            return UIResponse(status=303, headers=(("Location", LANDING_WIZARD),))
        return self._console_response(session, self.start_runtime())

    def _stop_endpoint(self, request: UIRequest, session: _Session) -> UIResponse:
        """``POST /stop`` (R7): :meth:`stop_runtime`, then the console with its report."""

        if self._wizard_open():
            return UIResponse(status=303, headers=(("Location", LANDING_WIZARD),))
        return self._console_response(session, self.stop_runtime())

    def _revoke_endpoint(self, request: UIRequest, session: _Session) -> UIResponse:
        """``POST /revoke`` (R7, A6): :meth:`revoke_grant` of the posted grant
        token, then the console with its report."""

        if self._wizard_open():
            return UIResponse(status=303, headers=(("Location", LANDING_WIZARD),))
        fields = _form_fields(request)
        report = self.revoke_grant(
            _posted(fields, console.REVOKE_GRANT_FIELD), fingerprint=_posted(fields, FINGERPRINT_FIELD)
        )
        return self._console_response(session, report)

    def grant_token(self, rule_id: str) -> str:
        """The opaque token the console's Revoke control posts for *rule_id*."""

        return console.grant_token(self.grant_key, rule_id)

    def revoke_grant(self, token: str, *, fingerprint: str) -> console.RuntimeOutcome:
        """Remove the one ``actions`` rule named by *token* through the managed overlay (A6).

        *token* is the rule's :meth:`grant_token`, mapped back to the id as
        the unresolved files spell it; a token no rule's id matches names no
        rule, so nothing is removed.

        The base is never written: the overlay's ``actions`` becomes the
        merged list of the unresolved files minus that rule — the overlay
        replaces a list wholesale, so the removal is the whole remaining list
        — through :func:`_write_overlay`, with the stale check every write
        has. With a child of this UI running, the removal takes effect
        through exactly one supervised restart, :meth:`apply`, whose report
        is the outcome; otherwise at the next start. Nothing else of the
        configuration changes, and nothing is ever added to a grant.
        """

        revoke = console.ACTION_REVOKE
        prepared = self._prepare_write(fingerprint)
        if isinstance(prepared, WriteResult):
            outcome = console.REVOKE_STALE if prepared.outcome == OUTCOME_STALE else console.REVOKE_NOT_WRITTEN
            return self._revoked(console.RuntimeOutcome(revoke, outcome))
        base, overlay = prepared
        merged = deep_merge(base, overlay).get(ACTIONS_KEY)
        rules = merged if isinstance(merged, list) else []
        rule_id = console.rule_of_token(rules, self.grant_key, token)
        remaining = None if rule_id is None else console.revoked_rules(rules, rule_id)
        if rule_id is None:
            return self._revoked(console.RuntimeOutcome(revoke, console.REVOKE_UNKNOWN_RULE))
        if remaining is None:
            return self._revoked(console.RuntimeOutcome(revoke, console.REVOKE_UNKNOWN_RULE, rule_id=rule_id))
        draft = _detached(overlay)
        draft[ACTIONS_KEY] = remaining
        try:
            _write_overlay(self.overlay_path, self._managed_overlay, self.base_path, draft)
        except (OSError, RuntimeError):
            return self._revoked(console.RuntimeOutcome(revoke, console.REVOKE_NOT_WRITTEN, rule_id=rule_id))
        if not self._child_alive():
            return self._revoked(console.RuntimeOutcome(revoke, console.REVOKE_PENDING, rule_id=rule_id))
        report = self.apply()
        outcomes = {
            APPLY_ACCEPTED: console.REVOKE_APPLIED,
            APPLY_UNKNOWN: console.REVOKE_APPLY_UNKNOWN,
            APPLY_REFUSED: console.REVOKE_APPLY_REFUSED,
        }
        detail = "\n".join(part for part in (*report.diagnostics, report.stderr_tail) if part)
        return self._revoked(
            console.RuntimeOutcome(
                revoke,
                outcomes[report.outcome],
                exit_status=report.exit_status,
                raw_detail=_redact(detail, self.secret_values),
                rule_id=rule_id,
            )
        )

    def _revoked(self, report: console.RuntimeOutcome) -> console.RuntimeOutcome:
        """Log a Revoke with its outcome, the paths and the redacted rule id."""

        rule = _redact(report.rule_id, self.secret_values)
        logger.info(
            "grant revoke %s: rule %s, overlay %s",
            report.outcome,
            rule,
            "(none)" if self.overlay_path is None else os.fspath(self.overlay_path),
            extra={
                "config_ui_operation": "revoke",
                "config_ui_outcome": report.outcome,
                "config_ui_paths": (rule,),
            },
        )
        return report

    def start_runtime(self) -> console.RuntimeOutcome:
        """Launch the profile through :meth:`Supervisor.start` (R7).

        Only when no live process holds the status record: a live record of
        another pid is "another instance is running", one of this UI's child
        (or a child of this UI still starting) is "already running", and
        neither starts anything. A launched child is watched within the
        Apply window: ready, still silent, or exited during startup — then
        its exit status and its redacted, bounded stderr tail are the raw
        detail, and the cause is chosen from the outcome alone. Runs under
        the mutation lock (a state-changing route), so it never races Apply.
        """

        # A fresh snapshot: the configured secrets join the redaction set
        # before any tail is read (R4).
        self.view()
        running = self.running_state()
        if self._holds_status_record(running):
            outcome = console.START_RUNNING_HERE if running.supervised else console.START_ANOTHER_INSTANCE
            report = console.RuntimeOutcome(console.ACTION_START, outcome)
        elif self._child_alive():
            report = console.RuntimeOutcome(console.ACTION_START, console.START_RUNNING_HERE)
        else:
            report = self._launch()
        logger.info(
            "runtime start %s: base %s, status file %s",
            report.outcome,
            os.fspath(self.base_path),
            os.fspath(self.status_path),
            extra={"config_ui_operation": "start", "config_ui_outcome": report.outcome},
        )
        return report

    def start_runtime_at_listening(self) -> console.RuntimeOutcome | None:
        """``--start-runtime`` (phase 10 R1 d): :meth:`start_runtime`, the
        console Start's own entry with its refusals, when the access URL
        would land on the console or the editor; otherwise nothing starts and
        one value-free reason line is logged. A refusal or failure is logged
        by its outcome alone and the UI keeps serving. Runs at most once per
        UI, under the mutation lock the console's Start route takes."""

        with self._mutation_lock:
            if self._listening_start_done:
                return None
            self._listening_start_done = True
            if not self.base_path.exists():
                logger.warning("start-runtime: no profile")
                return None
            if self._landing() == LANDING_WIZARD:
                logger.warning("start-runtime: wizard unfinished")
                return None
            report = self.start_runtime()
        if report.outcome not in (console.START_STARTED, console.START_UNCONFIRMED):
            logger.warning("start-runtime: %s", report.outcome)
        return report

    def _holds_status_record(self, running: RunningState) -> bool:
        """Whether a live process holds the status record, for the runtime
        actions: Start's refusal, Stop's report and the console's runtime
        state, which offers Start only when this is false. A live pid whose
        process started after the record's publication is not its publisher
        and holds nothing (phase 10 R2, :func:`record_pid_live`), so a record
        left by a predecessor never blocks Start, from the console or not.
        The record and supervision sections keep phase 9's judgement."""

        record = running.live_record
        if record is None:
            return False
        return record_pid_live(
            record.pid,
            self._published_seconds(record),
            lambda pid: running.alive,
            self._start_reader(),
        )

    # -- The supervision record (phase 10 R6) ---------------------------------

    def publish_supervision(self, path: Path) -> None:
        """Publish ``not_started`` at *path*, replacing any record present;
        from now on each transition rewrites it. Only :func:`main`'s claim
        path calls this, right after the instance record is claimed."""

        with self._supervision_lock:
            self._supervision = path
        self._record_supervision(SUPERVISION_NOT_STARTED)

    def release_supervision(self) -> None:
        """Remove the supervision record when it carries this UI's marker;
        no transition writes one afterwards."""

        with self._supervision_lock:
            path, self._supervision = self._supervision, None
            if path is None:
                return
            try:
                _remove_supervision_if(path, self.settings.instance_path, self.instance_marker)
            except OSError:
                logger.warning("the supervision record could not be removed on exit")

    def _supervision_launched(self, child: Any) -> None:
        self._record_supervision(SUPERVISION_LAUNCHED, child)

    def _record_supervision(self, state: str, child: Any = None) -> None:
        """Rewrite the supervision record with *state*; nothing while this UI
        has published none. A failed write is logged, value-free."""

        with self._supervision_lock:
            path = self._supervision
            if path is None:
                return
            content = supervision_document(self.instance_marker, state, child)
            try:
                _write_supervision(path, self.settings.instance_path, content)
            except OSError:
                logger.warning("the supervision record could not be written (%s)", state)

    def _child_alive(self) -> bool:
        """Whether this UI's child is still alive, whatever its status record says."""

        child = self.child
        return child is not None and child.poll() is None

    def _launch(self) -> console.RuntimeOutcome:
        start = console.ACTION_START
        try:
            child = self.supervisor.start()
        except SupervisorClosed:
            return console.RuntimeOutcome(start, console.START_CLOSING)
        except OSError as exc:
            return console.RuntimeOutcome(
                start, console.START_LAUNCH_FAILED, raw_detail=_report_tail(str(exc), self.secret_values)
            )
        watched = self.supervisor.await_startup(child, None, self.secret_values)
        if watched.outcome == APPLY_ACCEPTED:
            return console.RuntimeOutcome(start, console.START_STARTED)
        if watched.outcome == APPLY_UNKNOWN:
            return console.RuntimeOutcome(start, console.START_UNCONFIRMED)
        return console.RuntimeOutcome(
            start, console.START_EXITED, exit_status=watched.exit_status, raw_detail=watched.stderr_tail
        )

    def stop_runtime(self) -> console.RuntimeOutcome:
        """Stop the child this UI launched, and only it (R7, A4): with no
        such child, :meth:`Supervisor.stop` is never called."""

        stop = console.ACTION_STOP
        if self.child is None:
            held = self._holds_status_record(self.running_state())
            outcome = console.STOP_NOT_OURS if held else console.STOP_NOTHING
            report = console.RuntimeOutcome(stop, outcome)
        elif self.supervisor.stop():
            report = console.RuntimeOutcome(stop, console.STOP_STOPPED)
            self._record_supervision(SUPERVISION_STOPPED)
        else:
            report = console.RuntimeOutcome(stop, console.STOP_UNCONFIRMED)
        logger.info(
            "runtime stop %s: status file %s",
            report.outcome,
            os.fspath(self.status_path),
            extra={"config_ui_operation": "stop", "config_ui_outcome": report.outcome},
        )
        return report

    def _feed_endpoint(self, request: UIRequest, session: _Session) -> UIResponse:
        """``/feed?after=<sequence>&at=<time>`` (R7): the feed entries past the
        cursor, rendered newest first as the console shows them, the new
        cursor and its time, and whether the feed is a restarted runtime's
        (then every retained entry is answered and the page replaces its list).
        Only rendered, redacted items leave: no raw entry field is answered."""

        presented = request.query.get(console.FEED_CURSOR, "0")
        if not presented.isascii() or not presented.isdigit():
            return _text(400, "400 Bad Request: the cursor must be a sequence number.")
        after = int(presented)
        at = request.query.get(console.FEED_CURSOR_AT, "")
        if len(at) > console.FEED_CURSOR_AT_MAX:
            return _text(400, "400 Bad Request: the cursor time is too long.")
        # A fresh snapshot first, as every page takes one: the redaction
        # guard then knows the configured secrets even when the poll is the
        # first request this UI answers (R4).
        self.view()
        page = console.poll_feed(self.settings.feed_path, after, at or None)
        wizard = self.wizard
        items = console.render_feed_items(page.entries, wizard.language, wizard.table, escape=esc)
        document = {"cursor": page.cursor, "at": page.at, "reset": page.reset, "items": items}
        return UIResponse(
            status=200,
            body=json.dumps(document, ensure_ascii=False).encode("utf-8"),
            content_type="application/json",
        )

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

    def _start_reader(self) -> Callable[[int], float | None]:
        """The stale rule's start-time reader (see :attr:`started_at`)."""

        if self.started_at is not None:
            return self.started_at
        if self.probe is process_alive:
            return lambda pid: process_started_at(pid)
        return _unknown_start

    def _published_seconds(self, record: StatusRecord) -> float:
        """The record's publication time: its own ``published_at``, falling
        back to the status file's ``st_mtime`` (phase 10 R2); with neither,
        infinity, so no start time is ever after it (the judgement is
        unchanged)."""

        seconds = _timestamp_seconds(record.published_at)
        if seconds is not None:
            return seconds
        try:
            return os.stat(self.status_path).st_mtime
        except OSError:
            return math.inf

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
            f"{CONSOLE_LINK}"
            f'<form method="post" action="/check">{_csrf_field(session)}{_layout_field(view)}{_page_field("/")}'
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
            f'<input type="text" name="modules_directory" value="{esc(_editable(directory))}"'
            f'{"" if _editable(directory) == directory else _withheld_attributes()}></label> '
            f'<span class="origin">origin: {esc(self._origin(view, ("modules_directory",)))}</span> '
            f'<button type="submit">Save</button> {_CHECK_BUTTON}</form></section>',
            self._limits_section(view, session),
            self._secrets_section(view),
            self._actions_section(view),
            self._ownership_section(view),
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
                    f'value="{esc(_editable(shown or ""))}"'
                    f'{"" if _editable(shown or "") == (shown or "") else _withheld_attributes()}></td>'
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

    def _ownership_section(self, view: ConfigView) -> str:
        """Every resolved (family, platform) pair, read-only (R2, A8).

        Resolved from the discovered manifests and the merged ``inputs``
        block by the resolver startup uses; no control is emitted and
        ``inputs`` stays outside the writable scope. A refused resolution
        shows a pointer to Check, which reports its diagnostics. An
        ``enabled_modules`` entry that is a ``${NAME}`` reference resolves
        against the UI's environment, as startup and Check resolve it; the
        resolved name is a secret-set value, so the response still hides it.
        """

        enabled: list[str] = []
        for name in view.enabled_modules():
            variable = reference_name(name)
            if variable is not None:
                name = self.environ.get(variable)
            if isinstance(name, str) and name in view.modules and name not in enabled:
                enabled.append(name)
        declarations = {
            name: view.modules[name].declaration
            for name in enabled
            if view.modules[name].declaration is not None
        }
        try:
            ownership = resolve_input_ownership(
                enabled,
                declarations,
                view.merged.get(INPUTS_KEY),
                redacted=_secret_set(view),
            )
        except ModuleLoadError:
            ownership = None
        if ownership is None:
            content = f'<p id="ownership-unresolved">{esc(OWNERSHIP_UNRESOLVED)}</p>'
        else:
            rows: list[str] = []
            for family, platform in ownership.rows():
                owner = ownership.owner(family, platform)
                origin = ownership.origin(family, platform)
                if owner is None:
                    owner_text = (
                        INPUT_OWNER_NONE
                        if ownership.is_set_none(family, platform)
                        else OWNERSHIP_UNOWNED
                    )
                else:
                    owner_text = owner
                candidates = ", ".join(sorted(ownership.candidates(family, platform)))
                rows.append(
                    f'<tr data-input-family="{esc(family)}" data-input-platform="{esc(platform)}">'
                    f"<td>{esc(family)}</td><td>{esc(platform)}</td>"
                    f'<td class="owner">{esc(owner_text)}</td>'
                    f'<td class="origin">{esc(origin)}</td>'
                    f'<td class="candidates">{esc(candidates)}</td></tr>'
                )
            content = (
                "<table><tr><th>family</th><th>platform</th><th>owner</th>"
                "<th>origin</th><th>candidates</th></tr>"
                f"{''.join(rows)}</table>"
            )
        return (
            '<section id="input-ownership" class="read-only"><h2>Input ownership</h2>'
            f'<p class="reason">{esc(OWNERSHIP_READ_ONLY_REASON)}</p>'
            f'<p class="origin">origin: {esc(view.origin((INPUTS_KEY,)))}</p>'
            f"{content}</section>"
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
        page = f"{MODULE_PAGE_PREFIX}{name}"
        renderer = _SchemaRenderer(view, page, self._new_render(session, page))
        fields = self._module_fields(renderer, name, module)
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
                    structural=True,
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

    def _controls(self, view: ConfigView, render: str = "") -> _SchemaRenderer:
        """Every control the pages render over *view*, by field name.

        The module pages are rendered (their HTML discarded) through the same
        renderer, so a posted field is read back exactly as its page drew it
        — by the render tagged *render* (P19F10).
        """

        renderer = _SchemaRenderer(view, render=render)
        placeholder = _Session(csrf_token="")
        for name, module in view.modules.items():
            renderer.page = f"{MODULE_PAGE_PREFIX}{name}"
            self._module_fields(renderer, name, module)
            if _trigger_types(module.manifest):
                self._trigger_section(view, renderer, placeholder, name, module.manifest)
        renderer.page = CORE_PAGE
        directory = ("modules_directory",)
        renderer.register(directory, "string", _editable(view.display(directory) or ""))
        for group, fields in LIMIT_DECLARATION.items():
            for field_name, kind in fields.items():
                path = ("limits", group, field_name)
                renderer.register(
                    path,
                    "integer" if kind == LIMIT_KIND_COUNT else "number",
                    _editable(view.display(path) or ""),
                )
        return renderer

    def parse_edits(
        self,
        view: ConfigView,
        fields: Sequence[tuple[str, str]],
        session: _Session | None = None,
    ) -> tuple[list[Edit], list[str]]:
        """The draft edits a posted form makes, and the fields it cannot read.

        Patch identities are read back through the render *session* shows
        for the posted page (without a session: a render some session
        shows, P19F10). A control posted with an explicit operation
        (``@op:<name>``) is read by that operation, never by its text
        (gate-7 N7); any other field left as its page rendered it is no
        edit. A trigger-policy
        field is lifted to its whole channel policy (the unit a policy is
        written in, R6); a base-page toggle ``enabled_modules.<name>`` edits
        the whole ``enabled_modules`` list; the add-channel-policy form makes
        a new one-rule channel policy.
        """

        page = self._posted_page(fields, view)
        if session is not None:
            render = session.renders.get(page, "")
        else:
            render = self._live_render(page, fields)
        renderer = self._controls(view, render)
        posted: dict[str, str] = {}
        patches: list[tuple[str, str]] = []
        operations: dict[str, str] = {}
        for name, value in fields:
            if name.startswith(PATCH_FIELD_PREFIX):
                patches.append((name, value))  # every one counted: a duplicate is refused
                continue
            if name.startswith(OPERATION_FIELD_PREFIX):
                operations[name[len(OPERATION_FIELD_PREFIX) :]] = value
                continue
            posted[name] = value  # the last value wins (a checkbox after its hidden twin)
        edits: dict[tuple[Any, ...], Any] = {}
        problems: list[str] = []
        for name in operations:
            if name not in renderer.operations or renderer.controls.get(name) is None:
                problems.append(f"{OPERATION_FIELD_PREFIX}{name}: is not a setting this UI edits")
            elif name not in posted:
                posted[name] = ""  # an operation without its text: the text is empty
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
                if name in renderer.withheld:
                    # Never read back: its text could only be applied by
                    # reconstructing what the page withheld (P19F9).
                    problems.append(_Refusal(f"{name}: {_WITHHELD_WHOLE_REASON}"))
                else:
                    problems.append(f"{name}: is not a setting this UI edits")
                continue
            if name in operations and name in renderer.operations:
                value = _operated(name, control, text, operations[name], renderer.operations[name], problems)
                if value is not _MISSING:
                    _draft_edit(edits, policies, view, control.path, value)
                continue
            if text == control.rendered:
                continue
            value = _coerce(control.kind, control.schema, text)
            _draft_edit(edits, policies, view, control.path, value)
        if patches:
            patched, refusals = self._patched_settings(view, renderer, fields, patches)
            problems.extend(refusals)
            for path, value in patched.items():
                _draft_edit(edits, policies, view, path, value)
        ordered: list[Edit] = []
        if enabled is not None:
            ordered.append((("enabled_modules",), enabled))
        ordered.extend(edits.items())
        ordered.extend(policies.items())
        return ordered, problems

    def _patched_settings(
        self,
        view: ConfigView,
        renderer: _SchemaRenderer,
        fields: Sequence[tuple[str, str]],
        patches: Sequence[tuple[str, str]],
    ) -> tuple[dict[tuple[Any, ...], Any], list[str]]:
        """The settings the posted patch controls change, each the real
        configured value with the changes applied (:func:`_patched`), and the
        refusals (P19F9).

        Each control is mapped back through the identity this page rendered
        over the current configuration. An unknown identity (never
        rendered, truncated, stale, from another page) or one posted twice
        refuses the submission by name; so does a change that cannot be
        applied unambiguously. A refused setting is left out whole: nothing
        is written from a submission that was not applied as posted.
        """

        page = self._posted_page(fields, view)
        # An unknown identity names no setting: it is reported on its page.
        where = page
        if page.startswith(MODULE_PAGE_PREFIX):
            where = f"{MODULES_KEY}.{page[len(MODULE_PAGE_PREFIX) :]}"
        refusals: list[str] = []
        seen: set[str] = set()
        changes: dict[tuple[Any, ...], dict[tuple[tuple[Any, ...], str], str]] = {}
        # A withheld entry's explicit operation, and its replacement's text.
        operations: dict[tuple[tuple[Any, ...], tuple[Any, ...]], str] = {}
        replacements: dict[tuple[tuple[Any, ...], tuple[Any, ...]], str] = {}
        for name, text in patches:
            control = renderer.patches.get(name)
            if control is None or control.page != page:
                refusals.append(_Refusal(f"{where}: {_UNKNOWN_ENTRY_REASON}"))
                continue
            label = _field_name(control.top, view)
            if name in seen:
                refusals.append(_Refusal(f"{label}: {_DUPLICATE_ENTRY_REASON}"))
                continue
            seen.add(name)
            place = (control.top, control.relative)
            if control.operation == PATCH_OPERATION:
                operations[place] = text
                continue
            if control.operation == PATCH_VALUE:
                replacements[place] = text
            if text != control.rendered:
                changes.setdefault(control.top, {})[(control.relative, control.operation)] = text
        for (top, relative), operation in operations.items():
            # The operation decides, never the text (gate-7 N7).
            text = replacements.get((top, relative), "")
            changed = changes.setdefault(top, {})
            if operation == OPERATION_SET:
                changed[(relative, PATCH_VALUE)] = text
            elif operation == OPERATION_UNCHANGED:
                changed.pop((relative, PATCH_VALUE), None)
                if text:
                    refusals.append(_Refusal(f"{_field_name(top, view)}: {_TYPED_BUT_UNCHANGED_REASON}"))
            else:
                refusals.append(_Refusal(f"{_field_name(top, view)}: {_OPERATION_REASON}"))
            if not changed:
                del changes[top]
        settings: dict[tuple[Any, ...], Any] = {}
        for top, changed in changes.items():
            found: list[str] = []
            value = _patched(view.value(top), (), changed, found)
            if found:
                label = _field_name(top, view)
                refusals.extend(_Refusal(f"{label}: {reason}") for reason in dict.fromkeys(found))
            else:
                settings[top] = value
        return settings, list(dict.fromkeys(refusals))

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
        # The draft Save would write: the same normalisation (gate-4 N3).
        edits = _draft_edits(view, edits)
        if any(isinstance(line, _Refusal) for line in problems):
            # A submission that cannot be applied as posted is refused before
            # validation, as Save refuses it: the checker never sees a value
            # chosen by guessing (P19F9).
            result = CheckResult(
                False,
                tuple(_redact(line, secret_values) for line in problems),
                settings_phase_reached=False,
            )
            self.last_check = result
            return result
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
        core-settings pages list all of them. A form rendered from another
        key layout is refused as stale, exactly as Save refuses it, before
        any of its positional names is parsed: read against the current key
        order they would name other keys, and the verdict would be about a
        draft the user never edited (gate-3 N2).
        """

        fields = _form_fields(request)
        view = self.view()
        page = self._posted_page(fields, view)
        stale = self._stale_submission(fields, view, session, page)
        if stale is not None:
            return self._write_response(stale, page, session)
        module: str | None = None
        if page.startswith(MODULE_PAGE_PREFIX):
            module = page[len(MODULE_PAGE_PREFIX) :]
        edits, problems = self.parse_edits(view, fields, session)
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

        A path can carry a user-chosen key (a channel identifier); the
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
        stale = self._stale_submission(fields, view, session, page)
        if stale is not None:
            return self._write_response(stale, page, session)
        edits, problems = self.parse_edits(view, fields, session)
        result = self.save(
            edits, fingerprint=_posted(fields, FINGERPRINT_FIELD), page=page, problems=problems, view=view
        )
        return self._write_response(result, page, session)

    def _remove_endpoint(self, request: UIRequest, session: _Session) -> UIResponse:
        """``POST /remove``: delete one override from the overlay (R6, A7)."""

        fields = _form_fields(request)
        view = self.view()
        page = self._posted_page(fields, view)
        stale = self._stale_submission(fields, view, session, page)
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
    if value is _CLEARED:
        _clear_path(document, path, prune=False)
        return
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


def _clear_path(document: dict[Any, Any], path: Sequence[Any], *, prune: bool) -> None:
    """Delete *path* from *document* when it is there; with *prune*, drop
    the mappings it leaves empty, as a removed override does (P19F10)."""

    chain: list[tuple[Any, Any]] = []
    node: Any = document
    for segment in path[:-1]:
        child = _child(node, segment)
        if not isinstance(child, (dict, list)):
            return
        chain.append((node, segment))
        node = child
    if not isinstance(node, dict) or path[-1] not in node:
        return
    del node[path[-1]]
    if prune:
        for parent, segment in reversed(chain):
            if parent[segment] != {}:
                break
            del parent[segment]


def _operated(
    name: str, control: _Control, text: str, operation: str, clearable: bool, problems: list[str]
) -> Any:
    """The value a control posted with an explicit operation sets, or
    ``_MISSING`` for none (P19F10, gate-7 N7).

    The operation decides, never the text: ``unchanged`` sends nothing (text
    typed with it is refused, not guessed at), ``set`` sets the text as the
    whole new value — the empty string included — and ``clear``, where the
    page offers it, clears the setting from the overlay.
    """

    if operation == OPERATION_UNCHANGED:
        if text != control.rendered:
            problems.append(_Refusal(f"{name}: {_TYPED_BUT_UNCHANGED_REASON}"))
        return _MISSING
    if operation == OPERATION_SET:
        return _coerce(control.kind, control.schema, text)
    if operation == OPERATION_CLEAR and clearable:
        if text != control.rendered:
            problems.append(_Refusal(f"{name}: {_CLEARED_AND_TYPED_REASON}"))
            return _MISSING
        return _CLEARED
    problems.append(_Refusal(f"{name}: {_OPERATION_REASON}"))
    return _MISSING


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
        if value is _CLEARED:
            # Cleared from the overlay: emptied mappings go too (P19F10).
            _clear_path(draft, path, prune=True)
        else:
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
    *,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
) -> tuple[bool, list[str]]:
    """The settings phase through ``core.main.check_config`` (R5, D7, D8).

    It runs the check on its own socket-free loop, never ``asyncio.run``
    (whose self-pipe is a socketpair), and refuses to run on a thread that
    already runs a loop: in production it is reached on a ``config-ui``
    worker through :func:`_dispatch`. The loop is released by
    :data:`CHECK_RELEASE_SECONDS` after the Check starts on *clock*, its
    cleanup included; a Check that did not finish by then fails with
    :data:`CHECK_TIMEOUT_DIAGNOSTIC` (R7, F35).
    """

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        raise RuntimeError("Check must not run on a running event loop")
    collected: list[str] = []
    released, status = _run_check_bounded(
        check_config(
            base_path,
            environ=environ,
            overlay=overlay_path,
            overlay_document=draft,
            diagnostic_reporter=collected.append,
        ),
        clock=clock,
        sleep=sleep,
    )
    if not released:
        collected.append(CHECK_TIMEOUT_DIAGNOSTIC)
        return False, collected
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
#: Why a patch submission is refused before validation (P19F9, value-free).
#: Why a posted patch identity is refused (P19F11, gate-8 N3/N6): one
#: reason for every identity the current render did not draw.
_STALE_IDENTITY_REASON = (
    "a posted entry is not one the page this session shows now drew from the current "
    "configuration (an earlier rendering, another session or UI process, another page, "
    "or an altered or changed entry); reload the page"
)
_UNKNOWN_ENTRY_REASON = _STALE_IDENTITY_REASON
_DUPLICATE_ENTRY_REASON = "an entry was posted more than once; post each entry once"
_WITHHELD_WHOLE_REASON = (
    "holds a withheld value and is edited entry by entry, never as one text; "
    "edit its entries, or edit it in the configuration file"
)
_REMOVED_AND_EDITED_REASON = "an entry marked for removal was also edited; remove it or edit it, not both"
_EXISTING_KEY_REASON = "a new entry names a key the setting already has; edit that entry instead"
_NEW_KEY_REASON = "a new entry needs a key"
_ORDER_REASON = "an entry position must be a whole number"
_REMOVE_FLAG_REASON = "a removal flag must be 'true'"
#: Why an explicit operation is refused (P19F10, gate-7 N7).
_OPERATION_REASON = "an operation must be 'unchanged', 'set' or 'clear' where the page offers it"
_TYPED_BUT_UNCHANGED_REASON = (
    "a value was typed but its operation is 'unchanged'; choose 'set' to apply it, or empty it"
)
_CLEARED_AND_TYPED_REASON = "a value was typed but its operation is 'clear'; clear it or set it, not both"
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


def _draft_edits(view: ConfigView, edits: Sequence[Edit]) -> list[Edit]:
    """*edits* as the draft holds them (:func:`_draft_value`): the draft
    Check validates and Save writes."""

    return [(tuple(path), _draft_value(view, tuple(path), value)) for path, value in edits]


def _draft_value(view: ConfigView, path: tuple[Any, ...], value: Any) -> Any:
    """*value* as the draft holds it: the one normalisation Check and Save
    share (gate-4 N3).

    A declared credential below *path* given as exactly
    :data:`HIDDEN_LITERAL` keeps its configured literal (a credential is
    protected: it can only keep its text). Nothing else is ever restored:
    the routes post real values — a patch applied to the configured value
    (:func:`_patched`) or the exact text of a control that showed its value
    exactly — so no value is ever inferred from masked text (P19F9).
    """

    current = view.value(path)
    if current is _MISSING or value is _CLEARED:
        return value
    for relative, kind in _protected_descendants(view, path):
        if kind != "credential":
            continue
        configured = _lookup(current, relative)
        proposed = _lookup(value, relative)
        if configured is not _MISSING and isinstance(proposed, str) and proposed == HIDDEN_LITERAL:
            value = copy.deepcopy(value)
            _set_path(value, relative, configured)
    return value


def _protect_save(view: ConfigView, path: tuple[Any, ...], value: Any) -> tuple[Any, list[str]]:
    """*value* as the draft holds it (:func:`_draft_value`), and every
    protection refusal."""

    refusals = _direct_protection(view, path)
    if refusals:
        return value, refusals
    if value is _CLEARED:
        # Clearing keeps no protected descendant (P19F10).
        return value, [
            f"{_path_text((*path, *relative))}: {_PROTECTED_REASON}"
            for relative, _kind in _protected_descendants(view, path)
        ]
    current = view.value(path)
    value = _draft_value(view, path, value)
    for relative, kind in _protected_descendants(view, path):
        configured = _lookup(current, relative) if current is not _MISSING else _MISSING
        proposed = _lookup(value, relative)
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
    if value is _CLEARED:
        # The effective value is the base's again, as after Remove.
        return []
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
# whole life by a :class:`_StderrDrain` thread, which redacts it, then
# forwards it to the UI's own stderr (the terminal, never a page or a log
# record) and keeps a bounded tail of the redacted bytes for a refused
# Apply's report. No byte of the child's stderr leaves the server before
# every complete occurrence of every secret-set value in it is masked.
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


def _secret_needles(secret_values: Iterable[str]) -> frozenset[str]:
    """The UTF-8 bytes of every response form of every value, spelled as latin-1 text.

    Latin-1 maps each byte to one character and back, so :func:`mask_known`
    masks a byte stream exactly, and leaves every other byte (valid UTF-8 or
    not) untouched.
    """

    return frozenset(
        form.encode("utf-8").decode("latin-1")
        for value in secret_values
        if value
        for form in _redaction_forms(value)
        if form
    )


def _covered(text: str, needles: Iterable[str]) -> list[bool]:
    """For each index of *text*, whether some occurrence of a needle covers it.

    The same union of spans :func:`core.contracts.mask_known` masks; the drain
    needs the spans themselves to emit a stream in pieces.
    """

    covered = [False] * len(text)
    for needle in needles:
        start = text.find(needle)
        while start != -1:
            covered[start : start + len(needle)] = [True] * len(needle)
            start = text.find(needle, start + 1)
    return covered


class _StderrDrain(threading.Thread):
    """Reads one child's stderr pipe until EOF (D9, R8).

    The bytes read are redacted before anything else happens to them:
    every complete occurrence, in every response form, of every value of
    *secrets* (asked again for each chunk) is masked with :data:`REDACTED`
    by :func:`core.contracts.mask_known`. A value can be split between two
    reads, so the last bytes that could still begin one are held back until
    the next read, or EOF, completes or rules it out. Every earlier byte is
    final and emitted at once, masked or not; the last emitted bytes are kept
    as context so an occurrence begun there still masks the bytes it reaches,
    and a masked run continued across reads stays one marker. Nothing held
    is ever longer than the longest form, however values overlap. Only the
    redacted bytes are then
    forwarded to the UI's own ``sys.stderr.buffer`` (a failed write only
    skips forwarding; draining never stops early) and appended to a tail
    trimmed to the last :data:`DRAIN_TAIL_BYTES` under a lock, so the tail
    is truncated after redaction and never keeps a fragment of a value.
    """

    def __init__(
        self,
        fd: int,
        stream: Any = None,
        secrets: Callable[[], Iterable[str]] = _live_secret_values,
    ) -> None:
        super().__init__(name="stderr-drain", daemon=True)
        self._fd = fd
        #: The pipe's file object, closed by :meth:`close` once drained.
        self._stream = stream
        self._secrets = secrets
        #: Bytes read but not yet redacted (latin-1 text): a value may continue.
        self._pending = ""
        #: The last bytes already emitted (raw), where a value may have begun.
        self._context = ""
        #: Whether the last byte emitted was masked (its marker is already out).
        self._open_run = False
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
                self._emit(self._redacted(chunk.decode("latin-1"), final=False))
        finally:
            self._emit(self._redacted("", final=True))
            with self._lock:
                self._finished = True
                close = self._close_when_finished
            if close:
                self._close_stream()

    def _redacted(self, text: str, *, final: bool) -> bytes:
        """The redacted bytes now known to hold no part of an unseen value."""

        try:
            needles = _secret_needles(self._secrets())
        except Exception:  # noqa: BLE001 - an unreadable secret set hides everything
            self._pending, self._context, self._open_run = "", "", False
            return REDACTED.encode("utf-8") + b"\n" if text or final else b""
        if not needles:
            ready = self._pending + text
            self._pending, self._context, self._open_run = "", "", False
            return ready.encode("latin-1")
        longest = max(len(needle) for needle in needles)
        whole = self._context + self._pending + text
        offset = len(self._context)
        # Every occurrence covering an index below ``cut`` starts at most
        # ``longest - 1`` before it (inside the context) and ends inside ``whole``.
        cut = len(whole) if final else max(offset, len(whole) - longest + 1)
        covered = _covered(whole, needles)
        pieces: list[str] = []
        index = offset
        while index < cut:
            end = index
            while end < cut and covered[end] == covered[index]:
                end += 1
            if not covered[index]:
                pieces.append(whole[index:end])
            elif not (index == offset and self._open_run):
                pieces.append(REDACTED)
            index = end
        if cut > offset:
            self._open_run = covered[cut - 1]
        self._context = whole[max(0, cut - longest + 1) : cut]
        self._pending = whole[cut:]
        return "".join(pieces).encode("latin-1")

    def _emit(self, data: bytes) -> None:
        if not data:
            return
        self._forward(data)
        with self._lock:
            self._tail += data
            if len(self._tail) > DRAIN_TAIL_BYTES:
                del self._tail[:-DRAIN_TAIL_BYTES]

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
        #: Values a restart was given, redacted from the drain beside the live UIs' set.
        self._secret_values: frozenset[str] = frozenset()
        #: Called with each child :meth:`start` returns, outside the lock;
        #: the UI records it as launched (phase 10 R6).
        self.on_start: Callable[[Any], None] | None = None

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

        The drain redacts the last restart's secret values and the secret
        set of every live configuration UI from each byte before it leaves
        the server (R8). Raises :class:`SupervisorClosed` after
        :meth:`close`, and ``OSError`` when the launch argv cannot be executed.
        """

        known = self._secret_values
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
            drain = (
                _StderrDrain(stream.fileno(), stream, lambda: known | _live_secret_values())
                if stream is not None
                else None
            )
            self._drain = drain
        if drain is not None:
            drain.start()
        if self.on_start is not None:
            self.on_start(child)
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
        secret_values = frozenset(secret_values)
        self._secret_values = secret_values
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
        return self.await_startup(child, digest, secret_values)

    def await_startup(
        self, child: Any, digest: str | None, secret_values: Iterable[str] = ()
    ) -> ApplyReport:
        """Poll the status path within the window for *child*, just started (R7).

        Accepted: a usable record of this child, ``ready``, with *digest*
        (any digest when *digest* is ``None``). Refused: the child exited;
        its status and redacted stderr tail are reported. Unknown: the
        window elapsed. An unusable record never counts.
        """

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
            if (
                record is not None
                and record.pid == child.pid
                and (digest is None or record.digest == digest)
            ):
                # A usable record's state is always ``ready`` (the reader
                # refuses every other value).
                return ApplyReport(APPLY_ACCEPTED)
            if self.clock() >= deadline:
                return ApplyReport(APPLY_UNKNOWN)
            self.wait(self.poll_interval)


def _report_tail(text: str, secret_values: Iterable[str]) -> str:
    """The last :data:`REPORT_TAIL_BYTES` of *text*, redacted and value-free (R8).

    The drain already redacted every byte before keeping its tail; redaction
    runs again on that whole tail first, so a secret cut by the 2 KiB
    boundary was already replaced.
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


#: The one release deadline of a Check's loop, from the moment it starts: the
#: loader's validator deadline plus one second of cleanup (R7, F35).
CHECK_RELEASE_SECONDS = _STARTUP_DEADLINE_SECONDS + 1.0
CHECK_TIMEOUT_DIAGNOSTIC = (
    "configuration check: timed out; the check was abandoned "
    f"after {CHECK_RELEASE_SECONDS:g} s"
)


def _drive_until(
    loop: asyncio.AbstractEventLoop,
    finished: Callable[[], bool],
    deadline: float,
    clock: Callable[[], float],
    sleep: Callable[[float], Awaitable[Any]],
    watched: "asyncio.Future[Any] | None" = None,
) -> bool:
    """Run *loop* until *finished* or *deadline* on *clock*; return which.

    Each turn waits at most what remains until the shared *deadline*, never
    a budget of its own, and ends early when *watched* completes.
    """

    while not finished():
        remaining = deadline - clock()
        if remaining <= 0:
            return False
        pause = loop.create_task(sleep(min(remaining, _POLL_SECONDS)))  # type: ignore[arg-type]
        awaited = {pause} if watched is None else {pause, watched}
        loop.run_until_complete(asyncio.wait(awaited, return_when=asyncio.FIRST_COMPLETED))
        if not pause.done():
            pause.cancel()
            loop.run_until_complete(asyncio.wait({pause}))
    return True


def _run_check_bounded(
    coro: Coroutine[Any, Any, _T],
    *,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
) -> tuple[bool, _T | None]:
    """Run a Check's *coro* on a socket-free loop released by one deadline.

    The deadline is :data:`CHECK_RELEASE_SECONDS` after the call on *clock*.
    Validation, then the cancellation of the remaining tasks, the shutdown
    of async generators and the shutdown of the loop's executor each wait
    only for what remains of it (R7, F35). Once it has passed, the stages
    not yet run are skipped, what is still pending is abandoned unawaited
    and the loop is closed; the executor is then shut down without waiting
    for a job that never returns. Returns ``(True, result)`` when every
    stage finished, ``(False, None)`` otherwise; an exception the coroutine
    raised propagates once the loop is closed.
    """

    deadline = clock() + CHECK_RELEASE_SECONDS
    loop = _SocketFreeEventLoop()
    # The loop's executor is created here so its shutdown can be bounded;
    # ``shutdown_default_executor`` would join its thread unconditionally.
    executor = ThreadPoolExecutor()
    loop.set_default_executor(executor)
    released = False
    try:
        main = loop.create_task(coro)
        if not _drive_until(loop, main.done, deadline, clock, sleep, main):
            main.cancel()
            return False, None
        pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
        for task in pending:
            task.cancel()
        if pending:
            gathered = asyncio.gather(*pending, return_exceptions=True)
            if not _drive_until(loop, gathered.done, deadline, clock, sleep, gathered):
                return False, None
        closing = loop.create_task(loop.shutdown_asyncgens())
        if not _drive_until(loop, closing.done, deadline, clock, sleep, closing):
            closing.cancel()
            return False, None
        stopper = threading.Thread(
            target=executor.shutdown, kwargs={"wait": True}, daemon=True
        )
        stopper.start()
        if not _drive_until(
            loop, lambda: not stopper.is_alive(), deadline, clock, sleep
        ):
            return False, None
        released = True
        return True, main.result()
    finally:
        if not released:
            for task in asyncio.all_tasks(loop):
                task.cancel()
        loop.close()
        if not released:
            executor.shutdown(wait=False, cancel_futures=True)


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

    async def run() -> None:
        # access_log=None: the access URL's query carries the token (AC2).
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        try:
            await web.TCPSite(runner, settings.host, settings.port).start()
            # Only now is anything listening; the hook runs on its own thread
            # so a blocking browser opener never holds the loop.
            if ui.on_listening is not None:
                threading.Thread(target=ui.on_listening, name="config-ui-opener", daemon=True).start()
            stopped = asyncio.Event()
            if os.name != "nt":
                # As aiohttp's run_app did: SIGTERM ends serving through the
                # finally clauses, so the supervised child is still closed.
                asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, stopped.set)
            await stopped.wait()
        finally:
            await runner.cleanup()

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


# ---------------------------------------------------------------------------
# The instance record and the second launch (R1)
# ---------------------------------------------------------------------------

#: One probe attempt's bound, the retry period and the total bound (R1).
PROBE_ATTEMPT_SECONDS = 2.0
PROBE_RETRY_SECONDS = 0.5
PROBE_TOTAL_SECONDS = 10.0
#: A record larger than this is not one a UI wrote: it is incomplete.
INSTANCE_MAX_BYTES = 64 * 1024
#: Stale removals and lost publications one launch goes through before it
#: reports the record unusable instead of looping.
INSTANCE_MAX_ROUNDS = 8
#: The exit status of a launch that found an instance it does not open.
EXIT_INSTANCE = 1

#: ``probe(port, url, timeout)`` → ``(status, body)`` of one GET of
#: ``/instance`` carrying the token of *url*; raises ``OSError`` when nothing
#: answers within *timeout* seconds.
InstanceProbe = Callable[[int, str, float], tuple[int, bytes]]


def _url_token(url: str) -> str | None:
    """The access token an access URL carries, or ``None``."""

    from urllib.parse import urlsplit

    try:
        query = urlsplit(url).query
    except ValueError:
        return None
    token = dict(parse_qsl(query)).get("token")
    return token or None


#: What a probe receives at most beyond the record-sized body: the headers.
PROBE_HEADER_BYTES = 64 * 1024


class _ReceivedAnswer:
    """The bytes one probe received, as the socket ``HTTPResponse`` parses."""

    def __init__(self, data: bytes) -> None:
        self._data = data

    def makefile(self, *_args: object, **_kwargs: object) -> Any:
        import io

        return io.BytesIO(self._data)


def _probe_instance(port: int, url: str, timeout: float) -> tuple[int, bytes]:
    """The production probe: one GET of ``/instance`` on the recorded port of
    the recorded access URL's loopback host, with its token (R1). The HTTP
    client is built here only, never in a test (AC33).

    *timeout* bounds the whole exchange, not each socket operation: every
    step is given only what is left of it, and a peer still sending when it
    elapses is no answer (``TimeoutError``)."""

    import http.client
    import socket
    from urllib.parse import quote, urlsplit

    deadline = time.monotonic() + timeout

    def remaining() -> float:
        left = deadline - time.monotonic()
        if left <= 0:
            raise TimeoutError("the probe's bound elapsed")
        return left

    host = urlsplit(url).hostname or DEFAULT_HOST
    authority = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
    target = f"{INSTANCE_ROUTE}?token={quote(_url_token(url) or '', safe='')}"
    request = f"GET {target} HTTP/1.1\r\nHost: {authority}\r\nConnection: close\r\n\r\n"
    received = bytearray()
    with socket.create_connection((host, port), timeout=remaining()) as connection:
        connection.settimeout(remaining())
        connection.sendall(request.encode("ascii"))
        while len(received) <= INSTANCE_MAX_BYTES + PROBE_HEADER_BYTES:
            connection.settimeout(remaining())
            chunk = connection.recv(65536)
            if not chunk:
                break
            received += chunk
    response = http.client.HTTPResponse(_ReceivedAnswer(bytes(received)))  # type: ignore[arg-type]
    try:
        response.begin()
        return response.status, response.read(INSTANCE_MAX_BYTES)
    except http.client.HTTPException as exc:
        # A malformed answer is no answer; the URL is never in the message.
        raise OSError(type(exc).__name__) from None
    finally:
        response.close()


def _no_replace_rename(source: Path, target: Path) -> None:
    """Give *source* the name *target*, raising ``FileExistsError`` when
    *target* exists (R1): a hard link then the removal of *source* on POSIX,
    a rename (which never replaces there) on Windows."""

    if os.name == "nt":
        os.rename(source, target)
        return
    os.link(source, target)
    with suppress(FileNotFoundError):
        os.unlink(source)


@dataclass(frozen=True)
class InstanceSeams:
    """What a launch's instance judgement uses beyond the record (R1); tests
    replace them, production keeps every default. Each default is looked up
    when called, never captured at import.

    ``liveness(pid)``: whether *pid* is live (:func:`process_alive`).
    ``probe``: an :data:`InstanceProbe`. ``clock``/``sleep``: monotonic
    seconds and a wait. ``platform``: the platform name (``None``: the
    running one); it decides whether the token is persisted. ``publish(temp,
    record)``: the no-replace step that gives the written temporary file the
    record's name, raising ``FileExistsError`` when the name is taken.
    ``started_at(pid)``: when *pid* started, in epoch seconds, or ``None``
    (:func:`process_started_at`); a live pid that started after the record
    file's ``st_mtime`` is judged not live (phase 10 R2).
    """

    liveness: Callable[[int], bool] = lambda pid: process_alive(pid, None)
    probe: InstanceProbe = lambda port, url, timeout: _probe_instance(port, url, timeout)
    clock: Callable[[], float] = lambda: time.monotonic()
    sleep: Callable[[float], None] = lambda seconds: time.sleep(seconds)
    platform: str | None = None
    publish: Callable[[Path, Path], None] = lambda temp, record: _no_replace_rename(temp, record)
    started_at: Callable[[int], float | None] = lambda pid: process_started_at(pid)


@dataclass(frozen=True)
class InstanceRecord:
    """A complete instance record (R1). Its access URL carries the token, so
    it is never in the representation."""

    pid: int
    port: int
    marker: str
    access_url: str | None = field(default=None, repr=False)


def _persists_token(platform: str) -> bool:
    """Only where the record's mode restricts it to its owner (POSIX)."""

    return platform in (locations.LINUX, locations.MACOS)


def instance_document(ui: ConfigUI, platform: str) -> bytes:
    """The record *ui* publishes: pid, port, marker and, only on POSIX, the
    access URL (R1)."""

    document: dict[str, Any] = {
        "pid": os.getpid(),
        "port": ui.settings.port,
        "marker": ui.instance_marker,
    }
    if _persists_token(platform):
        document["access_url"] = ui.access_url
    return (json.dumps(document, sort_keys=True) + "\n").encode("utf-8")


def parse_instance_record(raw: bytes, platform: str) -> InstanceRecord | None:
    """The record *raw* holds, or ``None`` when it is incomplete: it does not
    parse, or a field its platform requires is missing or of the wrong type."""

    if len(raw) > INSTANCE_MAX_BYTES:
        return None
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(document, dict):
        return None
    pid, port, marker = document.get("pid"), document.get("port"), document.get("marker")
    if not (_is_integer(pid) and 0 < pid <= PID_MAX):
        return None
    if not (_is_integer(port) and 0 < port < 65536):
        return None
    if not isinstance(marker, str) or not marker:
        return None
    access_url = None
    if _persists_token(platform):
        access_url = document.get("access_url")
        if not isinstance(access_url, str) or _url_token(access_url) is None:
            return None
    return InstanceRecord(pid=pid, port=port, marker=marker, access_url=access_url)


def _read_instance(path: Path) -> tuple[bytes, float] | None:
    """The record's bytes and its publication time (the file's
    ``st_mtime``, of the file read), or ``None`` when there is none (a
    record removed while it is read is no record)."""

    try:
        with open(path, "rb") as handle:
            published = os.fstat(handle.fileno()).st_mtime
            return handle.read(INSTANCE_MAX_BYTES + 1), published
    except FileNotFoundError:
        return None


@contextmanager
def _instance_lock(path: Path, *, exclusive: bool) -> Iterator[None]:
    """Hold the lock that serializes changes to the record's name (R1).

    A removal holds it exclusively, from renaming the record aside to
    putting it back, so no publication can take the name it vacates; a
    publication holds it shared (concurrent no-replace publications decide
    among themselves). On POSIX the lock is on the record's directory, so
    nothing is added to it; on Windows it is on a file in the temporary
    directory named after the record's path.
    """

    if os.name == "nt":
        digest = hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()[:32]
        target = Path(tempfile.gettempdir()) / f"config-ui-instance-{digest}.lock"
        descriptor = os.open(target, os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o600)
        try:
            _windows_lock(descriptor, exclusive)
            yield
        finally:
            os.close(descriptor)  # closing releases the lock
        return
    import fcntl

    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        yield
    finally:
        os.close(descriptor)  # closing releases the lock


def _windows_lock(descriptor: int, exclusive: bool) -> None:
    """Block until the first byte of *descriptor*'s file is locked."""

    import ctypes
    import msvcrt
    from ctypes import wintypes

    class _Overlapped(ctypes.Structure):
        _fields_ = [
            ("Internal", ctypes.c_void_p),
            ("InternalHigh", ctypes.c_void_p),
            ("Offset", wintypes.DWORD),
            ("OffsetHigh", wintypes.DWORD),
            ("hEvent", wintypes.HANDLE),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LockFileEx.restype = wintypes.BOOL
    kernel32.LockFileEx.argtypes = (
        wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
        ctypes.POINTER(_Overlapped),
    )
    lockfile_exclusive_lock = 0x00000002
    overlapped = _Overlapped()
    handle = msvcrt.get_osfhandle(descriptor)
    flags = lockfile_exclusive_lock if exclusive else 0
    if not kernel32.LockFileEx(handle, flags, 0, 1, 0, ctypes.byref(overlapped)):
        raise ctypes.WinError(ctypes.get_last_error())


def _remove_instance_if(path: Path, aside: Path, matches: Callable[[bytes], bool]) -> bool:
    """Remove the record only while its bytes satisfy *matches* (R1).

    It is renamed to the launch-unique *aside* first, so the bytes judged are
    the bytes deleted; a record that no longer matches is put back with a
    no-replace rename (a newer record under the name is never replaced).
    The whole step holds :func:`_instance_lock` exclusively, so no other
    launch publishes under the name while it is vacated and the record is
    always put back. Returns whether a record was removed.
    """

    with _instance_lock(path, exclusive=True):
        try:
            os.rename(path, aside)
        except FileNotFoundError:
            return False
        try:
            raw: bytes | None = aside.read_bytes()
        except OSError:
            raw = None
        if raw is not None and matches(raw):
            os.unlink(aside)
            return True
        with suppress(FileExistsError):
            _no_replace_rename(aside, path)
        return False


def _publish_instance(
    path: Path, content: bytes, unique: str, publish: Callable[[Path, Path], None]
) -> bool:
    """Write *content* to a temporary file beside *path* (0600 from its
    creation on POSIX) and publish it under *path* without replacing (R1).

    Returns ``False`` when another record holds the name; any other
    ``OSError`` (a file system without hard links included) propagates. The
    temporary file never outlives the call.
    """

    temp = path.with_name(f".{path.name}.{unique}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    descriptor = os.open(temp, flags, 0o600)
    try:
        try:
            view = memoryview(content)
            while view:
                view = view[os.write(descriptor, view) :]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        try:
            # Shared: never while a removal has vacated the name.
            with _instance_lock(path, exclusive=False):
                publish(temp, path)
        except FileExistsError:
            return False
        return True
    finally:
        with suppress(OSError):
            os.unlink(temp)


def _instance_answer(status: int, body: bytes) -> tuple[str, str] | None:
    """``(marker, version)`` of an authenticated ``/instance`` answer."""

    if status != 200:
        return None
    try:
        document = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(document, dict):
        return None
    marker, version = document.get("marker"), document.get("version")
    if not isinstance(marker, str) or not isinstance(version, str):
        return None
    return marker, version


#: What the probe observed of the recorded port when nothing confirmed.
PORT_UNAUTHENTICATED = "unauthenticated"
PORT_SILENT = "silent"


def _probe_record(record: InstanceRecord, seams: InstanceSeams) -> tuple[str, str] | str:
    """Probe the recorded port until an authenticated answer (returned as
    ``(marker, version)``) or the total bound (R1): each attempt is bounded
    to 2 s, the next starts 0.5 s after the last began, and none starts 10 s
    or more after the first. Without an answer, returns the port fact."""

    assert record.access_url is not None
    answered = False
    first = seams.clock()
    while True:
        started = seams.clock()
        remaining = PROBE_TOTAL_SECONDS - (started - first)
        if remaining <= 0:
            break
        try:
            status, body = seams.probe(
                record.port, record.access_url, min(PROBE_ATTEMPT_SECONDS, remaining)
            )
        except (OSError, ValueError):
            pass
        else:
            answer = _instance_answer(status, body)
            if answer is not None:
                return answer
            answered = True
        wake = started + PROBE_RETRY_SECONDS
        if wake - first >= PROBE_TOTAL_SECONDS:
            break
        delay = wake - seams.clock()
        if delay > 0:
            seams.sleep(delay)
    return PORT_UNAUTHENTICATED if answered else PORT_SILENT


class _SecondLaunch(Exception):
    """The launch ends here with :attr:`status` (R1): nothing is served."""

    def __init__(self, status: int) -> None:
        super().__init__(status)
        self.status = status


def string_table_refusal(settings: UISettings) -> str | None:
    """The plain cause the UI must not start for, from its string table:
    a shipped table that does not validate, or a persisted language that is
    no longer offered (P19F3); ``None`` when the table serves the profile."""

    try:
        table = StringTable.load()
    except StringTableError as error:
        return str(error)
    try:
        language = WizardState(settings.base_path).language
    except WizardStateError:
        return None
    try:
        table.language_or_default(language)
    except LanguageUnavailable as refused:
        return refused.cause
    return None


def _instance_sentences(settings: UISettings) -> Callable[..., str]:
    """``_t(key, **values)`` over the string table, in the profile's language
    (the wizard state's, defaulting to English: R2)."""

    table = StringTable.load()
    try:
        language = WizardState(settings.base_path).language
    except WizardStateError:
        language = None
    chosen = table.language_or_default(language)

    def _t(key: str, **values: object) -> str:
        return table.sentence(chosen, key, **values)

    return _t


def claim_instance(
    ui: ConfigUI, seams: InstanceSeams, opener: Callable[[str], object]
) -> int | None:
    """Judge the instance record and publish this launch's own (R1).

    Returns ``None`` once this launch's record is published (it may serve),
    else the exit status of a launch that serves nothing: 0 when a confirmed
    instance of this version was handed to *opener*, non-zero for every
    report. Reports come from the string table and carry no access URL,
    except the two that hand the user the confirmed instance's.
    """

    settings = ui.settings
    path = settings.instance_path
    platform = seams.platform if seams.platform is not None else locations.running_platform()
    unique = f"{os.getpid()}-{secrets.token_hex(8)}"
    aside = path.with_name(f".{path.name}.{unique}.stale")
    _t = _instance_sentences(settings)

    def report(*lines: str, stream: Any = None) -> None:
        print("\n".join(lines), file=sys.stderr if stream is None else stream, flush=True)

    def unusable() -> int:
        report(_t("instance.unusable", path=path), _t("instance.unusable.next"))
        return EXIT_INSTANCE

    def judge(raw: bytes, published: float) -> None:
        """Return when the record is stale; raise :class:`_SecondLaunch`
        when it names an instance this launch does not replace. A live pid
        that started after *published* is not the record's (phase 10 R2)."""

        record = parse_instance_record(raw, platform)
        if record is None or not record_pid_live(
            record.pid, published, seams.liveness, seams.started_at
        ):
            return
        exists = _t("instance.record_exists", path=path)
        if not _persists_token(platform):
            report(
                exists,
                _t("instance.unconfirmable", pid=record.pid),
                _t("instance.unconfirmable.next", port=record.port, pid=record.pid),
            )
            raise _SecondLaunch(EXIT_INSTANCE)
        outcome = _probe_record(record, seams)
        if isinstance(outcome, tuple):
            marker, version = outcome
            if marker != record.marker:
                return
            own = locations.product_version()
            assert record.access_url is not None
            if version == own:
                # Standard output, as A3's single print: a print is no log.
                report(_t("instance.already_running", url=record.access_url), stream=sys.stdout)
                if not settings.no_browser:
                    try:
                        opener(record.access_url)
                    except Exception:
                        logger.warning("the browser could not be opened: use the printed access URL")
                raise _SecondLaunch(0)
            report(
                _t("instance.version_mismatch", running=version, own=own),
                _t("instance.version_mismatch.next", url=record.access_url),
                stream=sys.stdout,
            )
            raise _SecondLaunch(EXIT_INSTANCE)
        report(
            exists,
            _t("instance.port.unauthenticated", port=record.port)
            if outcome == PORT_UNAUTHENTICATED
            else _t("instance.port.silent", port=record.port),
            _t("instance.not_confirmed.cause"),
            _t("instance.not_confirmed.next", pid=record.pid),
        )
        raise _SecondLaunch(EXIT_INSTANCE)

    content = instance_document(ui, platform)
    try:
        for _round in range(INSTANCE_MAX_ROUNDS):
            entry = _read_instance(path)
            if entry is not None:
                raw, published = entry
                judge(raw, published)
                if _remove_instance_if(path, aside, lambda found: found == raw):
                    report(_t("instance.stale"))
                # Removed or put back: read the name again either way.
                continue
            if _publish_instance(path, content, unique, seams.publish):
                return None
            # Lost the publication: the winner's record is judged next.
    except _SecondLaunch as ended:
        return ended.status
    except OSError:
        return unusable()
    return unusable()


def supervision_document(marker: str, state: str, child: Any = None) -> bytes:
    """The supervision record's bytes (phase 10 R6): version, marker and
    state, plus the child's pid and the status record's timestamp when
    *state* is ``launched``."""

    document: dict[str, Any] = {
        "version": SUPERVISION_VERSION,
        "marker": marker,
        "state": state,
    }
    if state == SUPERVISION_LAUNCHED:
        document["pid"] = child.pid
        document["launched_at"] = _utc_timestamp()
    return (json.dumps(document, sort_keys=True) + "\n").encode("utf-8")


def _write_supervision(path: Path, instance: Path, content: bytes) -> None:
    """Replace the supervision record at *path* with *content*, atomically
    (phase 10 R6): a temporary file beside it, owner-only from its creation
    on POSIX, then ``os.replace`` under the instance record's lock (shared,
    as a publication holds it; the write is short). The temporary file
    never outlives the call; an ``OSError`` propagates."""

    temp = path.with_name(f".{path.name}.{os.getpid()}-{secrets.token_hex(8)}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    descriptor = os.open(temp, flags, 0o600)
    try:
        try:
            view = memoryview(content)
            while view:
                view = view[os.write(descriptor, view) :]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        with _instance_lock(instance, exclusive=False):
            os.replace(temp, path)
    finally:
        with suppress(OSError):
            os.unlink(temp)


def _remove_supervision_if(path: Path, instance: Path, marker: str) -> bool:
    """Remove the supervision record only while it carries *marker* (R6),
    holding the instance record's lock exclusively so no write lands
    between the judgement and the removal. A record with another marker,
    or one that does not parse, is left untouched. Returns whether a
    record was removed."""

    with _instance_lock(instance, exclusive=True):
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            return False
        try:
            document = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return False
        if not isinstance(document, dict) or document.get("marker") != marker:
            return False
        os.unlink(path)
        return True


def release_instance(ui: ConfigUI, platform: str | None = None) -> None:
    """Remove the instance record on a clean exit, only while it still holds
    this instance's marker (R1). The supervision record goes first, when it
    carries this UI's marker, whether or not the instance record is still
    this UI's (phase 10 R6)."""

    try:
        ui.release_supervision()
    finally:
        _release_instance_record(ui, platform)


def _release_instance_record(ui: ConfigUI, platform: str | None) -> None:
    platform = platform if platform is not None else locations.running_platform()
    path = ui.settings.instance_path
    aside = path.with_name(f".{path.name}.{os.getpid()}-{secrets.token_hex(8)}.exit")

    def ours(raw: bytes) -> bool:
        record = parse_instance_record(raw, platform)
        return record is not None and record.marker == ui.instance_marker

    try:
        _remove_instance_if(path, aside, ours)
    except OSError:
        logger.warning("the instance record could not be removed on exit")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _install_launcher(settings: UISettings, seams: LauncherSeams) -> int:
    """Write the OS launcher, print its path; 0, or 2 on a refusal (R1, A3).

    ``--config`` reaches the launcher only when it was given, in its
    canonical form (:attr:`UISettings.base_path`: ``~`` and variables
    expanded, made absolute, links resolved), so the launcher opens the
    very file this launch would have.
    """

    platform = seams.platform if seams.platform is not None else locations.running_platform()
    directories = (
        seams.directories if seams.directories is not None else launcher_directories(os.environ)
    )
    executable = seams.executable if seams.executable is not None else sys.executable
    try:
        config = None if settings.default_location else settings.base_path
    except OverlayError as exc:
        print(f"config_ui: path_not_canonical: {exc}", file=sys.stderr)
        return 2
    try:
        path = write_launcher(
            platform, directories=directories, executable=executable, config=config
        )
    except LauncherError as exc:
        print(f"config_ui: {exc}", file=sys.stderr)
        return 2
    print(f"Launcher: {path}", flush=True)
    return 0


def main(
    argv: Sequence[str] | None = None,
    *,
    opener: Callable[[str], object] | None = None,
    serve: Callable[[ConfigUI, UISettings], None] | None = None,
    instance: InstanceSeams | None = None,
    launcher: LauncherSeams | None = None,
) -> int:
    """Run the configuration UI; return the process exit status.

    *opener* receives the access URL once (A3; default ``webbrowser.open``),
    from :attr:`ConfigUI.on_listening` once *serve* is listening, and *serve*
    serves the UI (default: this module's :func:`serve`); both are looked up
    when called, never captured at import. Before serving, the instance
    record is judged and this launch's own published (:func:`claim_instance`,
    over the *instance* seams); a launch that finds an instance it does not
    replace serves nothing, and a clean exit removes the record (R1).
    ``--install-launcher`` writes the OS launcher over the *launcher* seams
    and exits before any startup check, instance record or serve (R1, A3).
    ``--no-browser`` leaves *opener* uncalled, ``--token-file`` supplies the
    token (an unusable file exits 2 before any claim or serve), and a bind
    that is not loopback writes one warning line before serving, and
    ``--start-runtime`` starts the runtime once listening through
    :meth:`ConfigUI.start_runtime_at_listening` (phase 10 R1).
    """

    if opener is None:
        opener = webbrowser.open
    if serve is None:
        serve = globals()["serve"]

    try:
        settings = UISettings.from_argv(sys.argv[1:] if argv is None else argv)
    except SystemExit as exit_:
        return exit_.code if isinstance(exit_.code, int) else 2
    if settings.install_launcher:
        return _install_launcher(settings, LauncherSeams() if launcher is None else launcher)
    problems = startup_checks(settings)
    if problems:
        for problem in problems:
            print(f"config_ui: {problem}", file=sys.stderr)
        return 2
    refusal = string_table_refusal(settings)
    if refusal is not None:
        print(f"config_ui: string table refused: {refusal}", file=sys.stderr)
        return 2
    token = None
    if settings.token_file is not None:
        try:
            token = read_token_file(settings.token_file)
        except TokenFileError as exc:
            print(f"config_ui: {exc}", file=sys.stderr)
            return 2
    if settings.default_location:
        # Only the A2 default's directory is created; a --config run creates
        # nothing (A2: every existing behaviour is unchanged).
        directory = settings.base_path.parent
        try:
            if not directory.exists():
                directory.mkdir(mode=0o700, parents=True)
                if os.name != "nt":
                    os.chmod(directory, 0o700)
        except OSError as exc:
            print(
                f"config_ui: profile directory {directory} cannot be created: {exc.strerror}",
                file=sys.stderr,
            )
            return 2
    ui = ConfigUI(settings, token=token)
    seams = InstanceSeams() if instance is None else instance
    ended = claim_instance(ui, seams, opener)
    if ended is not None:
        ui.supervisor.close()
        return ended
    try:
        # Claimed: the supervision record is published now, replacing any
        # present, and removed by the release below (phase 10 R6).
        ui.publish_supervision(settings.supervision_path)
        print(f"Configuration UI: {ui.access_url}", flush=True)
        logger.info("configuration UI serving on %s", ui.access_authority)

        def open_browser() -> None:
            try:
                opener(ui.access_url)
            except Exception:
                # The printed URL is the fallback; the URL carries the token, so
                # neither it nor the failure's text (which may quote it) is logged.
                logger.warning("the browser could not be opened: use the printed access URL")

        def listening() -> None:
            # The opener first (P2's behaviour), then the runtime. The opener
            # may block (a terminal browser waits until it closes), so with
            # --start-runtime it runs on its own thread and the runtime starts
            # without waiting for it; the hook still returns after both.
            if not settings.start_runtime:
                open_browser()
                return
            opening = None
            if not settings.no_browser:
                opening = threading.Thread(target=open_browser, name="config-ui-browser", daemon=True)
                opening.start()
            try:
                ui.start_runtime_at_listening()
            except Exception:
                # Value-free: the failure's text may quote a path or a value.
                logger.warning("start-runtime: failed")
            if opening is not None:
                opening.join()

        if not settings.no_browser or settings.start_runtime:
            ui.on_listening = listening
        warning = non_loopback_warning(settings.host, settings.port)
        if warning is not None:
            print(warning, file=sys.stderr, flush=True)
        serve(ui, settings)
    finally:
        try:
            ui.supervisor.close()
        finally:
            release_instance(ui, seams.platform)
    return 0
