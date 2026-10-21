"""``python -m modules.operator press <button>`` — the hotkey surface (phase 8 R6, A8).

A global hotkey needs OS APIs the companion does not ship, so the hotkey
surface is this command: bind it to a key in the desktop, the stream deck or
whatever launches commands. ``press <button> [--profile PATH]`` (default
``config.yaml``) sends **one** ``POST /press`` to the running input's listener
naming the button with surface ``hotkey``, the token in the
``X-Operator-Token`` header, and reports the input's answer.

**Reading.** The profile is read with :func:`yaml.safe_load` and only
``modules.operator.listen`` (``host``, default ``127.0.0.1``, and ``port``)
and ``modules.operator.token`` are looked at. The token is either a literal
or exactly one ``${NAME}`` reference, resolved from :data:`os.environ`; no
other interpolation is done and no loader runs.

**Exit codes.** :data:`EXIT_ACCEPTED` (0) when the input answered
``accepted``; :data:`EXIT_REFUSED` (2) when it answered anything else — every
refusal of its request core (token, host, surface, unknown button, cooldown,
closed); :data:`EXIT_UNREACHABLE` (3) on a connection error or a timeout
(:data:`TIMEOUT_SECONDS`) or a connection that closes mid-answer;
:data:`EXIT_USAGE` (64) on a usage error or a profile that cannot be read, is
not YAML, or lacks the listen address or a token that can be sent as a header
value. Every message is a fixed string: none carries the token, a header or a
configured value.

**Isolation.** The standard library and ``yaml`` only: importing this module
imports nothing from ``core`` — neither the runtime, the loader nor the
lifecycle — and opens nothing until :func:`main` sends its one request.
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from typing import Any

import yaml


EXIT_ACCEPTED = 0
EXIT_REFUSED = 2
EXIT_UNREACHABLE = 3
EXIT_USAGE = 64

#: How long the one request may take, in seconds.
TIMEOUT_SECONDS = 5

DEFAULT_PROFILE = "config.yaml"
DEFAULT_HOST = "127.0.0.1"

# The input's own names (``modules.operator``), repeated here so the command
# imports nothing but the standard library and ``yaml`` (A8).
MODULE_NAME = "operator"
PATH_PRESS = "/press"
TOKEN_HEADER = "X-Operator-Token"
SURFACE_HOTKEY = "hotkey"
ANSWER_ACCEPTED = "accepted"

_ENV_REFERENCE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}\Z")
_REFERENCE_MARKER = "${"

MESSAGE_USAGE = "operator: usage: python -m modules.operator press <button> [--profile PATH]"
MESSAGE_PROFILE = "operator: the profile cannot be read or has no operator listen address and token"
MESSAGE_ACCEPTED = "operator: press accepted"
MESSAGE_REFUSED = "operator: press refused"
MESSAGE_UNREACHABLE = "operator: the operator input cannot be reached"

Opener = Callable[..., Any]


class _UsageError(Exception):
    """A usage or profile error; the command prints a fixed message."""


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> Any:  # noqa: ARG002 - never echoed
        # argparse would print the offending arguments and exit 2: a usage
        # error is 64 here, with a fixed message.
        raise _UsageError(MESSAGE_USAGE)


def main(argv: Sequence[str] | None = None, *, opener: Opener | None = None) -> int:
    try:
        arguments = _parser().parse_args(argv)
    except _UsageError as error:
        print(error, file=sys.stderr)
        return EXIT_USAGE
    try:
        url, token = _press_address(arguments.profile)
    except _UsageError as error:
        print(error, file=sys.stderr)
        return EXIT_USAGE
    request = press_request(url, token, arguments.button)
    send = opener if opener is not None else urllib.request.build_opener().open
    try:
        with send(request, timeout=TIMEOUT_SECONDS) as response:
            answer = response.read()
    except urllib.error.HTTPError as error:
        # The input answered, with a refusal status.
        answer = _read_quietly(error)
    except (urllib.error.URLError, OSError, http.client.HTTPException):
        # ``TimeoutError`` and ``socket.timeout`` are ``OSError``s; a
        # connection closed mid-answer is an ``http.client.IncompleteRead``.
        print(MESSAGE_UNREACHABLE, file=sys.stderr)
        return EXIT_UNREACHABLE
    if _status_of(answer) == ANSWER_ACCEPTED:
        print(MESSAGE_ACCEPTED)
        return EXIT_ACCEPTED
    print(MESSAGE_REFUSED, file=sys.stderr)
    return EXIT_REFUSED


def press_request(url: str, token: str, button: str) -> urllib.request.Request:
    """The one ``POST`` of a hotkey press of *button*."""

    body = json.dumps({"button": button, "surface": SURFACE_HOTKEY}).encode("utf-8")
    return urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", TOKEN_HEADER: token},
    )


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="python -m modules.operator", description="The operator's hotkey surface.")
    commands = parser.add_subparsers(dest="command", required=True, parser_class=_Parser)
    press = commands.add_parser("press", help="press one configured button")
    press.add_argument("button")
    press.add_argument("--profile", default=DEFAULT_PROFILE)
    return parser


def _press_address(profile: str) -> tuple[str, str]:
    """The press endpoint's URL and the resolved token, from *profile*."""

    try:
        with open(profile, encoding="utf-8") as stream:
            document = yaml.safe_load(stream)
    except (OSError, UnicodeError, yaml.YAMLError) as error:
        raise _UsageError(MESSAGE_PROFILE) from error
    settings = _mapping_at(document, "modules", MODULE_NAME)
    listen = settings.get("listen") if settings is not None else None
    if not isinstance(listen, Mapping):
        raise _UsageError(MESSAGE_PROFILE)
    host = listen.get("host", DEFAULT_HOST)
    port = listen.get("port")
    if (
        not isinstance(host, str)
        or not host
        or any(character.isspace() or character in "/?#@" for character in host)
        or isinstance(port, bool)
        or not isinstance(port, int)
        or not 1 <= port <= 65535
    ):
        raise _UsageError(MESSAGE_PROFILE)
    token = _resolved_token(settings.get("token"))
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"http://{host}:{port}{PATH_PRESS}", token


def _mapping_at(document: Any, *keys: str) -> Mapping[str, Any] | None:
    node = document
    for key in keys:
        if not isinstance(node, Mapping):
            return None
        node = node.get(key)
    return node if isinstance(node, Mapping) else None


def _resolved_token(value: Any) -> str:
    """A literal token, or the value of its one ``${NAME}`` reference."""

    if not isinstance(value, str) or not value:
        raise _UsageError(MESSAGE_PROFILE)
    match = _ENV_REFERENCE.fullmatch(value)
    if match is not None:
        resolved = os.environ.get(match.group(1))
        if not resolved:
            raise _UsageError(MESSAGE_PROFILE)
        return _header_value(resolved)
    if _REFERENCE_MARKER in value:
        # No other interpolation: a partial reference is not a token.
        raise _UsageError(MESSAGE_PROFILE)
    return _header_value(value)


def _header_value(token: str) -> str:
    """*token*, if ``http.client`` can send it as a header value.

    ``http.client`` refuses a value with a line break, or one it cannot
    encode, with an error quoting the value: the token is checked here so
    the refusal is the fixed profile message instead.
    """

    if token != token.strip() or not token.isprintable():
        raise _UsageError(MESSAGE_PROFILE)
    try:
        token.encode("latin-1")
    except UnicodeEncodeError as error:
        raise _UsageError(MESSAGE_PROFILE) from error
    return token


def _read_quietly(response: Any) -> bytes:
    try:
        return response.read()
    except Exception:  # noqa: BLE001 - an unreadable refusal is still a refusal
        return b""


def _status_of(answer: Any) -> str | None:
    try:
        decoded = json.loads(bytes(answer).decode("utf-8"))
    except (TypeError, ValueError):
        return None
    status = decoded.get("status") if isinstance(decoded, Mapping) else None
    return status if isinstance(status, str) else None


if __name__ == "__main__":
    sys.exit(main())
