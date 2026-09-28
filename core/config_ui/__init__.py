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
checks, authorities, the request core, the socket-free event loop (D7), the
request bridge and the entry point.
"""

from __future__ import annotations

import argparse
import asyncio
import hmac
import html
import ipaddress
import logging
import secrets
import selectors
import sys
import threading
from collections import OrderedDict
from collections.abc import Callable, Coroutine, Mapping, Sequence
from concurrent.futures import Executor, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar
from urllib.parse import parse_qsl

from core.overlay import (
    default_status_path,
    resolve_overlay_path,
    same_file,
    status_path_collision,
)

__all__ = [
    "ConfigUI",
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

    def __init__(self, settings: UISettings) -> None:
        self.settings = settings
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
        self.routes: dict[str, tuple[frozenset[str], Handler]] = {
            "/": (frozenset({"GET"}), self._base_page),
            "/save": (frozenset({"POST"}), self._not_available),
            "/remove": (frozenset({"POST"}), self._not_available),
            "/check": (frozenset({"POST"}), self._not_available),
            "/apply": (frozenset({"POST"}), self._not_available),
        }

    def __repr__(self) -> str:
        return f"ConfigUI(authority={self.access_authority!r})"

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
        """Answer one request; the guards run before any route handler."""

        response = self._guarded(request)
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

    def _base_page(self, request: UIRequest, session: _Session) -> UIResponse:
        page = (
            "<!doctype html><html><head><meta charset=\"utf-8\">"
            f"<meta name=\"csrf-token\" content=\"{html.escape(session.csrf_token)}\">"
            "<title>Configuration</title></head>"
            "<body><h1>Configuration</h1></body></html>"
        )
        return UIResponse(status=200, body=page.encode("utf-8"), content_type="text/html; charset=utf-8")

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
