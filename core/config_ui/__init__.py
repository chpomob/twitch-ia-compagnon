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
checks, authorities, the configuration model, the redaction guard (R8), the
rendering helpers (R9), the request core and its pages, the socket-free event
loop (D7), the request bridge and the entry point. No section names a module:
every page lists or renders what discovery found (A2).
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import html
import ipaddress
import json
import logging
import os
import secrets
import selectors
import sys
import threading
import weakref
from contextvars import ContextVar
from collections import OrderedDict
from collections.abc import Callable, Coroutine, Iterable, Mapping, Sequence
from concurrent.futures import Executor, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar
from urllib.parse import parse_qsl

import yaml

from core.actions import ANY_ACTION
from core.bus import EventBus
from core.loader import (
    MANIFEST_SETTINGS_SCHEMA_KEY,
    MANIFEST_TRIGGERS_KEY,
    DiscoveredModule,
    ModuleLoader,
    ModuleLoadError,
    _validate_manifest,
)
from core.main import (
    _ENV_REFERENCE,
    ACTIONS_KEY,
    LIMIT_DECLARATION,
    LIMIT_KIND_COUNT,
    LIMITS_KEY,
    MODULES_DIRECTORY_BUILTIN,
    SECRETS_KEY,
    _builtin_modules_directory,
)
from core.overlay import (
    OverlayError,
    deep_merge,
    default_status_path,
    read_base,
    read_overlay,
    resolve_overlay_path,
    same_file,
    status_path_collision,
)

__all__ = [
    "ConfigUI",
    "ConfigView",
    "UIRequest",
    "UIResponse",
    "UISettings",
    "accepted_authorities",
    "main",
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

    @property
    def base_path(self) -> Path:
        """The base configuration file, as the runtime opens it."""

        return self.config.expanduser()

    @property
    def overlay_path(self) -> Path | None:
        """The managed overlay path: ``--overlay`` or the implicit A1 path."""

        return resolve_overlay_path(self.base_path, self.overlay)

    @property
    def status_path(self) -> Path:
        """The one status path watched: ``--status-file`` or the A5 default."""

        if self.status_file is not None:
            return self.status_file
        return default_status_path(self.base_path)


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
    base = settings.base_path
    overlay = settings.overlay_path
    if overlay is None:
        problems.append(
            f"no overlay path can be derived from configuration file {base}: "
            "--overlay PATH is required"
        )
    elif same_file(overlay, base):
        problems.append(f"overlay path {overlay} is the configuration file itself")
    status = settings.status_path
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
# Redaction guard (R8)
#
# Defence in depth: the renderers never place a secret-set value in a page
# (credentials and references are shown as ``${NAME}`` or as
# :data:`HIDDEN_LITERAL`); this guard replaces each secret-set value by
# ``[hidden]`` anyway, in every configured value as :func:`esc` renders it
# and in every log record. It works on values before they are serialized,
# never on the finished body or headers, so a secret that happens to equal a
# path, a field name or any other piece of markup cannot rewrite it; the
# structural identifiers that build paths and control names (module, limit
# and variable names) go through :func:`ident`, which never redacts.
# Only values of at least ``REDACT_MIN_LENGTH`` characters are replaced:
# redacting a one-character secret such as ``"1"`` would mangle every page,
# and a shorter secret is still never rendered because the renderers show
# references only.
# ---------------------------------------------------------------------------

REDACT_MIN_LENGTH = 4
REDACTED = "[hidden]"

#: The UI answering the current request; :func:`esc` redacts its secret set
#: as it stands when the value is rendered (the handler refreshes it first).
_RENDERING_FOR: ContextVar[ConfigUI | None] = ContextVar("_RENDERING_FOR", default=None)


def _redaction_forms(secret_values: Iterable[str]) -> list[str]:
    """Every form a value can take in a response, longest first."""

    forms: set[str] = set()
    for value in secret_values:
        if len(value) < REDACT_MIN_LENGTH:
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

_SCALAR_KINDS = ("string", "integer", "number", "boolean")


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


class _SchemaRenderer:
    """Renders a JSON-schema subset as form controls over one configuration view.

    A *live* node edits the configured value at its path and shows its
    current value and origin; a node under an array's ``items`` or a
    mapping's named entries is *descriptive*: it documents the shape the
    enclosing JSON editor accepts, with no control of its own.
    """

    def __init__(self, view: ConfigView) -> None:
        self.view = view

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
            f"{label} <strong>{NOT_EDITABLE}</strong>: <code>{ident(_path_text(path))}</code> "
            f'<span class="reason">({esc(reason)})</span>'
            f"{self._help(schema or {}, path, False)}{configured}</div>"
        )

    # -- pieces -------------------------------------------------------------

    def _attributes(self, path: Sequence[Any], required: bool, live: bool) -> str:
        attributes = f'data-path="{ident(_path_text(path))}"'
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
                    f'value="{ident(_path_text(path))}">Remove override</button>'
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
        name = ident(_path_text(path))
        value = self.view.value(path)
        if reference_name(value) is not None:
            # A reference is edited as its text, whatever the declared type.
            return f'<input type="text" name="{name}" value="{esc(value)}">'
        if self.view.is_credential(path):
            # A literal credential is never placed in the page (R8),
            # whatever control its declared type would get (enum, number, ...).
            return f'<input type="text" name="{name}" value="" placeholder="{esc(HIDDEN_LITERAL)}">'
        if kind == "enum":
            options = "".join(
                f'<option value="{esc(_plain(member))}"'
                f'{" selected" if value is not _MISSING and value == member else ""}>'
                f"{esc(_plain(member))}</option>"
                for member in schema["enum"]
            )
            return f'<select name="{name}">{options}</select>'
        if kind == "boolean":
            checked = " checked" if value is True else ""
            return (
                f'<input type="hidden" name="{name}" value="false">'
                f'<input type="checkbox" name="{name}" value="true"{checked}>'
            )
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
            return f'<input type="number" name="{name}"{bounds} step="{step}" value="{esc(shown)}">'
        shown = "" if value is _MISSING else _plain(value)
        return f'<input type="text" name="{name}" value="{esc(shown)}">'

    def _list(
        self,
        schema: Mapping[str, Any],
        items: Mapping[str, Any],
        path: Sequence[Any],
        required: bool,
        live: bool,
    ) -> str:
        """A list editor: one JSON text area, validated server-side."""

        editor = (
            f'<textarea name="{ident(_path_text(path))}" data-json="list" rows="3">'
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
            for key in entries:
                if key in known:
                    continue
                entry_path = (*path, key)
                rows.append(
                    f'<div class="entry" data-entry="{esc(_plain(key))}">'
                    f"<label>Entry {_key_label(key, self.view)} "
                    f'<textarea name="{ident(_path_text(entry_path))}" data-json="entry" rows="2">'
                    f"{esc(self._json_text(entry_path))}</textarea></label> "
                    f'<span class="origin">origin: {esc(self.origin(entry_path))}</span></div>'
                )
            prefix = ident(_path_text(path))
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
        self, settings: UISettings, *, environ: Mapping[str, str] | None = None
    ) -> None:
        self.settings = settings
        #: The UI process's own environment: "set/unset" is judged here, the
        #: environment a UI-launched main process inherits (A3).
        self.environ: Mapping[str, str] = os.environ if environ is None else environ
        #: The secret set of the last configuration snapshot (R8).
        self.secret_values: frozenset[str] = frozenset()
        self._secrets_lock = threading.Lock()
        self.token = secrets.token_urlsafe(32)
        self.authorities = accepted_authorities(
            settings.host, settings.port, settings.allowed_hosts
        )
        self.base_path = settings.base_path
        self.overlay_path = settings.overlay_path
        self.status_path = settings.status_path
        self._sessions: OrderedDict[str, _Session] = OrderedDict()
        self._sessions_lock = threading.Lock()
        self._mutation_lock = threading.Lock()
        _LOG_REDACTION.sources.add(self)
        self.routes: dict[str, tuple[frozenset[str], Handler]] = {
            "/": (frozenset({"GET"}), self._base_page),
            "/core": (frozenset({"GET"}), self._core_page),
            MODULE_PAGE_PREFIX: (frozenset({"GET"}), self._module_page),
            "/save": (frozenset({"POST"}), self._not_available),
            "/remove": (frozenset({"POST"}), self._not_available),
            "/check": (frozenset({"POST"}), self._not_available),
            "/apply": (frozenset({"POST"}), self._not_available),
        }

    def __repr__(self) -> str:
        return f"ConfigUI(authority={self.access_authority!r})"

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

    # -- route handlers (placeholders replaced by later steps) --------------

    def _readiness(self, name: str) -> str:
        """The readiness cell of module *name*: the last Check verdict (R4)."""

        return "not checked yet"

    def _base_page(self, request: UIRequest, session: _Session) -> UIResponse:
        """Every discovered module and the configuration's origin (R4a)."""

        view = self.view()
        enabled = view.enabled_modules()
        rows: list[str] = []
        for name in sorted(view.modules):
            is_enabled = name in enabled
            state = "enabled" if is_enabled else "disabled"
            rows.append(
                f'<tr data-module="{ident(name)}" data-enabled="{str(is_enabled).lower()}">'
                f'<td><a href="{MODULE_PAGE_PREFIX}{ident(name)}">{ident(name)}</a></td>'
                f"<td>{state}"
                f'<form class="inline" method="post" action="/save">{_csrf_field(session)}'
                f'<input type="hidden" name="enabled_modules.{ident(name)}" '
                f'value="{str(not is_enabled).lower()}">'
                f'<button type="submit">{"Disable" if is_enabled else "Enable"}</button>'
                "</form></td>"
                f'<td class="readiness">{esc(self._readiness(name))}</td></tr>'
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
            f'<form method="post" action="/check">{_csrf_field(session)}'
            '<button type="submit">Check</button></form></section>'
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
            _diagnostics(view),
            '<section id="modules-directory"><h2>Modules directory</h2>'
            f'<form method="post" action="/save">{_csrf_field(session)}'
            '<label>modules_directory '
            f'<input type="text" name="modules_directory" value="{esc(directory)}"></label> '
            f'<span class="origin">origin: {esc(self._origin(view, ("modules_directory",)))}</span> '
            '<button type="submit">Save</button></form></section>',
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
                if reference:
                    control = 'type="text"'
                elif kind == LIMIT_KIND_COUNT:
                    control = 'type="number" min="1" step="1"'
                else:
                    control = 'type="number" min="0" step="any"'
                rows.append(
                    f'<tr data-limit="{ident(group)}.{ident(field_name)}">'
                    f"<td><label>{ident(field_name)}</label></td>"
                    f'<td class="kind">{ident(kind)}</td>'
                    f'<td><input {control} name="limits.{ident(group)}.{ident(field_name)}" '
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
            f'<form method="post" action="/save">{_csrf_field(session)}'
            f"{''.join(groups)}"
            '<button type="submit">Save</button></form></section>'
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
        for rule in rules:
            if not isinstance(rule, Mapping):
                rows.append(f'<tr data-rule="" data-origin="{esc(origin)}"><td colspan="7">invalid rule</td></tr>')
                continue
            rule_id = rule.get("rule_id", "")
            action = rule.get("action_name", ANY_ACTION)
            action_text = "any" if action in (ANY_ACTION, None) else _plain(action)
            rows.append(
                f'<tr data-rule="{esc(_plain(rule_id))}" data-origin="{esc(origin)}">'
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
        form_id = "settings-form"
        body = [
            f"<h1>Module {ident(name)}</h1>",
            '<p><a href="/">Back to the configuration</a></p>',
            _diagnostics(view),
            f'<section id="settings"><h2>Settings</h2>'
            f'<form id="{form_id}" method="post" action="/save">{_csrf_field(session)}'
            f"{fields}"
            '<button type="submit">Save</button></form></section>',
        ]
        declared = _trigger_types(module.manifest)
        if declared:
            body.append(self._trigger_section(view, renderer, session, name, module.manifest))
        return _page(f"Module {name}", session, "".join(body))

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
        for key, policy in channels.items():
            path = (*channels_path, key)
            label = _key_label(key, view)
            parts = [
                f'<fieldset class="channel" data-channel="{esc(_plain(key))}">'
                f"<legend>Channel {label}</legend>",
                f'<form method="post" action="/save">{_csrf_field(session)}',
            ]
            if not isinstance(policy, Mapping):
                parts.append(renderer.notice(path, "the channel policy is not a mapping"))
            else:
                combination = policy.get("combination", "all_of")
                options = "".join(
                    f'<option value="{ident(item)}"'
                    f'{" selected" if item == combination else ""}>{ident(item)}</option>'
                    for item in combinations
                )
                parts.append(
                    f'<p><label>Combination <select name="{ident(_path_text((*path, "combination")))}" '
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
            parts.append('<button type="submit">Save</button>')
            if _lookup(view.base, path) is not _MISSING:
                parts.append(f'<p class="note">{esc(BASE_ENTRY_NOTE)}</p>')
            else:
                parts.append(
                    f'<button type="submit" formaction="/remove" name="path" '
                    f'value="{ident(_path_text(path))}">Delete this channel policy</button>'
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
            f"{_csrf_field(session)}<h3>Add channel policy</h3>"
            f'<input type="hidden" name="add_channel_policy" value="{ident(_path_text(channels_path))}">'
            '<label>Channel <input type="text" name="channel"></label> '
            f'<label>Combination <select name="combination">{combination_options}</select></label> '
            f'<label>Rule type <select name="rule_type">{type_options}</select></label> '
            '<button type="submit">Add</button></form>'
        )
        content = "".join(policies) or "<p>No channel policy is configured: every channel uses the module default.</p>"
        return f'<section id="triggers"><h2>Trigger policies</h2>{content}{add}</section>'

    def _not_available(self, request: UIRequest, session: _Session) -> UIResponse:
        return _text(501, "501 Not Implemented")


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


async def _dispatch(ui: ConfigUI, request: UIRequest, executor: Executor) -> UIResponse:
    """Run ``ui.handle`` on *executor*, never on the calling loop (D8)."""

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(executor, ui.handle, request)


def serve(ui: ConfigUI, settings: UISettings) -> None:
    """Serve *ui* over HTTP on the bound host and port until interrupted."""

    from aiohttp import web

    executor = ThreadPoolExecutor(
        max_workers=EXECUTOR_WORKERS, thread_name_prefix=EXECUTOR_THREAD_PREFIX
    )

    async def catch_all(request: web.Request) -> web.StreamResponse:
        body = await request.read()
        ui_request = _to_ui_request(
            request.method, request.path, request.query, request.headers.items(), body
        )
        response = await _dispatch(ui, ui_request, executor)
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
    serve(ui, settings)
    return 0
