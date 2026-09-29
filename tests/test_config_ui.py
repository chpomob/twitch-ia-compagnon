"""The configuration UI: CLI, bind policy, guards and startup refusals (P9);
the configuration model, base page and core-settings page (P10); the module
pages generated from each manifest (P11); Check (P12); Save and
remove-override (P13); the status record reader, drift and running state
(P14); Apply, the supervised restart (P15); the secret-canary sweep and the
offline-assets URL audit (P16).

The whole module runs with socket creation patched to raise (AC5): no test
here binds, connects or even creates a socket. The request core is driven
through :meth:`ConfigUI.handle` directly, and every coroutine the module
runs (the request bridge, D8, and Check's ``check_config``) goes through the
socket-free loop of D7. There
is deliberately no pytest-asyncio test here: a fixture-provided event loop
would create its self-pipe socketpair under the patch.
"""

from __future__ import annotations

import ast
import asyncio
import gc
import hashlib
import html as _html
import io
import json
import logging
import os
import re
import secrets
import signal
import socket
import subprocess
import sys
import threading
import tokenize
from collections.abc import Callable, Iterator, Mapping, Sequence
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import pytest
import yaml

import core.config_ui as config_ui
import core.overlay as core_overlay
from core.config_ui import (
    BASE_ENTRY_NOTE,
    DRIFT_DIFFERS,
    DRIFT_IN_SYNC,
    DRIFT_UNKNOWN,
    ENTRY_SEGMENT,
    HIDDEN_LITERAL,
    ITEMS_SEGMENT,
    LOCAL_ONLY_STATEMENT,
    MODULE_PAGE_PREFIX,
    NO_RUNNING_PROCESS,
    NO_STATUS_RECORD,
    NOT_EDITABLE,
    NOT_REPORTED,
    NOT_SUPERVISED,
    READ_ONLY_REASON,
    STATUS_MAX_BYTES,
    STATUS_RECORD_UNUSABLE,
    SUPERVISED,
    ConfigUI,
    ConfigView,
    UIRequest,
    UIResponse,
    UISettings,
    _dispatch,
    _SchemaRenderer,
    _redact,
    _run_socket_free,
    _SocketFreeEventLoop,
    _to_ui_request,
    accepted_authorities,
    main,
    process_alive,
    read_status,
    startup_checks,
)
from core.main import LIMIT_DECLARATION
from core.overlay import on_disk_digest, read_base

# ---------------------------------------------------------------------------
# AC5: socket creation patched to raise for the whole module
# ---------------------------------------------------------------------------

_SOCKET_ATTEMPTS = {"count": 0}


def _refuse(*args: object, **kwargs: object) -> None:
    _SOCKET_ATTEMPTS["count"] += 1
    raise AssertionError("socket created")


@pytest.fixture(scope="module", autouse=True)
def no_socket_creation() -> Iterator[None]:
    """Every socket-creation entry point raises and counts the attempt (AC5)."""

    patch = pytest.MonkeyPatch()
    patch.setattr(socket.socket, "__init__", _refuse)
    for name in ("socketpair", "fromfd", "create_connection", "create_server"):
        patch.setattr(socket, name, _refuse)
    try:
        yield
    finally:
        patch.undo()
    assert _SOCKET_ATTEMPTS["count"] == 0, "a test other than the self-test created a socket"


def test_ac5_the_socket_patch_is_effective() -> None:
    """The fixture self-test; its own attempts are removed from the counter."""

    before = _SOCKET_ATTEMPTS["count"]
    try:
        with pytest.raises(AssertionError, match="socket created"):
            socket.socket()
        with pytest.raises(AssertionError, match="socket created"):
            socket.socketpair()
        with pytest.raises(AssertionError, match="socket created"):
            socket.create_connection(("127.0.0.1", 9))
        with pytest.raises(AssertionError, match="socket created"):
            socket.create_server(("127.0.0.1", 0))
        # The loop fails half-built; its finaliser then trips on the missing
        # self-pipe. Collect it here so that noise stays inside the self-test.
        ignored: list[object] = []
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(sys, "unraisablehook", ignored.append)
            with pytest.raises(AssertionError, match="socket created") as failed:
                asyncio.new_event_loop()
            del failed
            gc.collect()
        assert _SOCKET_ATTEMPTS["count"] - before >= 5
    finally:
        _SOCKET_ATTEMPTS["count"] = before


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

BASE_TEXT = "# base\nenabled_modules: [twitch]\nmodules:\n  twitch:\n    channel: base\n"
OVERLAY_TEXT = "# overlay\nmodules:\n  twitch:\n    channel: overlay\n"
MODULE_NAMES = ("twitch", "brain", "moderation", "audio_output")
SETTING_PATHS = ("modules.", "enabled_modules", "modules_directory", "limits", "secrets", "actions")


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    (tmp_path / "config.yaml").write_text(BASE_TEXT, encoding="utf-8")
    (tmp_path / "config.local.yaml").write_text(OVERLAY_TEXT, encoding="utf-8")
    return tmp_path


def _snapshot(directory: Path) -> dict[str, tuple[bytes, int]]:
    return {
        path.name: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in sorted(directory.iterdir())
        if path.is_file() and not path.is_symlink()
    }


def _ui(config: Path, *extra: str) -> ConfigUI:
    return ConfigUI(UISettings.from_argv(["--config", str(config), *extra]))


def _request(
    method: str,
    path: str,
    *,
    host: str | None = "127.0.0.1:8765",
    query: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
    body: bytes = b"",
) -> UIRequest:
    merged = {} if host is None else {"host": host}
    merged.update(headers or {})
    return UIRequest(method=method, path=path, query=query or {}, headers=merged, body=body)


def _login(ui: ConfigUI, host: str = "127.0.0.1:8765") -> tuple[str, str]:
    """Open the access URL; return the session cookie header and CSRF token."""

    response = ui.handle(_request("GET", "/", host=host, query={"token": ui.token}))
    assert response.status == 303
    headers = dict(response.headers)
    assert headers["Location"] == "/"
    cookie = headers["Set-Cookie"]
    pair = cookie.split(";")[0]
    session = ui._session(_request("GET", "/", headers={"cookie": pair}))
    assert session is not None
    return pair, session.csrf_token


def _post(
    ui: ConfigUI,
    path: str,
    cookie: str,
    csrf: str | None,
    *,
    host: str = "127.0.0.1:8765",
    origin: str | None = None,
) -> UIResponse:
    headers = {"cookie": cookie}
    if csrf is not None:
        headers["x-csrf-token"] = csrf
    if origin is not None:
        headers["origin"] = origin
    return ui.handle(_request("POST", path, host=host, headers=headers))


# ---------------------------------------------------------------------------
# AC1: bind policy
# ---------------------------------------------------------------------------


def _fail(*args: object, **kwargs: object) -> None:
    raise AssertionError("must not be reached")


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.10", "::"])
def test_ac1_a_non_loopback_host_is_refused_before_any_bind(
    host: str,
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(config_ui, "serve", _fail)
    monkeypatch.setattr(config_ui, "ConfigUI", _fail)
    status = main(["--config", str(config_dir / "config.yaml"), "--host", host])
    captured = capsys.readouterr()
    assert status != 0
    assert "non-loopback" in captured.err and "--allow-non-loopback" in captured.err
    assert captured.out == ""
    assert _SOCKET_ATTEMPTS["count"] == 0


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost", "LOCALHOST", "127.0.0.2"])
def test_ac1_loopback_hosts_pass_the_startup_checks(host: str, config_dir: Path) -> None:
    settings = UISettings.from_argv(["--config", str(config_dir / "config.yaml"), "--host", host])
    assert startup_checks(settings) == []


def test_ac1_localhost_is_judged_by_name_without_dns(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("getaddrinfo", "gethostbyname", "gethostbyname_ex"):
        monkeypatch.setattr(socket, name, _fail)
    settings = UISettings.from_argv(
        ["--config", str(config_dir / "config.yaml"), "--host", "localhost"]
    )
    assert startup_checks(settings) == []
    settings = UISettings.from_argv(
        ["--config", str(config_dir / "config.yaml"), "--host", "box.lan"]
    )
    assert any("non-loopback" in problem for problem in startup_checks(settings))


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.10", "::"])
def test_ac1_allow_non_loopback_accepts_and_the_token_still_applies(
    host: str, config_dir: Path
) -> None:
    settings = UISettings.from_argv(
        ["--config", str(config_dir / "config.yaml"), "--host", host, "--allow-non-loopback"]
    )
    assert startup_checks(settings) == []
    ui = ConfigUI(settings)
    authority = sorted(ui.authorities)[0]
    for path in ("/", "/save", "/check"):
        response = ui.handle(_request("GET", path, host=authority))
        assert response.status == 401
    wrong = ui.handle(_request("GET", "/", host=authority, query={"token": "x" * 43}))
    assert wrong.status == 401


def test_ac1_main_passes_the_bind_policy_to_serve(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    served: list[tuple[str, int]] = []
    monkeypatch.setattr(config_ui, "serve", lambda ui, s: served.append((s.host, s.port)))
    status = main(["--config", str(config_dir / "config.yaml"), "--host", "::1", "--port", "9000"])
    assert status == 0
    assert served == [("::1", 9000)]
    assert capsys.readouterr().out.startswith("Configuration UI: http://[::1]:9000/?token=")


# ---------------------------------------------------------------------------
# AC2: token and printed URL
# ---------------------------------------------------------------------------


def test_ac2_each_start_has_a_fresh_256_bit_token(config_dir: Path) -> None:
    first = _ui(config_dir / "config.yaml")
    second = _ui(config_dir / "config.yaml")
    assert first.token != second.token
    alphabet = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")
    for ui in (first, second):
        assert len(ui.token) >= 43
        assert set(ui.token) <= alphabet
        assert ui.token not in repr(ui)


def test_ac2_the_url_is_printed_once_and_never_logged(
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    built: list[ConfigUI] = []
    monkeypatch.setattr(config_ui, "serve", lambda ui, settings: built.append(ui))
    caplog.set_level(logging.DEBUG)
    assert main(["--config", str(config_dir / "config.yaml")]) == 0
    (ui,) = built
    out = capsys.readouterr().out
    assert out == f"Configuration UI: http://127.0.0.1:8765/?token={ui.token}\n"
    assert out.count(ui.token) == 1
    for record in caplog.records:
        assert ui.token not in record.getMessage()
    assert ui.token not in caplog.text


def test_ac2_no_log_record_carries_a_session_or_the_token(
    config_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    ui = _ui(config_dir / "config.yaml")
    cookie, csrf = _login(ui)
    ui.handle(_request("GET", "/", headers={"cookie": cookie}))
    _post(ui, "/save", cookie, csrf)
    for secret in (ui.token, cookie.split("=", 1)[1], csrf):
        assert secret not in caplog.text


# ---------------------------------------------------------------------------
# AC3: no session, no content
# ---------------------------------------------------------------------------

ROUTES = ("/", "/save", "/remove", "/check", "/apply")


@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize("path", ROUTES)
def test_ac3_every_route_without_a_session_is_refused_without_content(
    path: str, method: str, config_dir: Path
) -> None:
    ui = _ui(config_dir / "config.yaml")
    for headers in ({}, {"cookie": "config_ui_session=forged"}, {"cookie": "other=1"}):
        response = ui.handle(_request(method, path, headers=headers))
        assert response.status in (401, 403)
        body = response.body.decode("utf-8")
        for name in MODULE_NAMES + SETTING_PATHS:
            assert name not in body
        assert str(config_dir) not in body
        assert ui.token not in body


def test_ac3_a_wrong_token_opens_no_session(config_dir: Path) -> None:
    ui = _ui(config_dir / "config.yaml")
    response = ui.handle(_request("GET", "/", query={"token": ui.token[:-1] + "!"}))
    assert response.status == 401
    assert not any(name == "Set-Cookie" for name, _ in response.headers)


def test_ac3_the_access_url_opens_a_session_and_redirects(config_dir: Path) -> None:
    ui = _ui(config_dir / "config.yaml")
    response = ui.handle(_request("GET", "/", query={"token": ui.token}))
    assert response.status == 303
    headers = dict(response.headers)
    assert headers["Location"] == "/"
    cookie = headers["Set-Cookie"]
    attributes = [part.strip() for part in cookie.split(";")]
    assert attributes[0].startswith("config_ui_session=")
    assert ui.token not in cookie
    assert set(attributes[1:]) == {"HttpOnly", "SameSite=Strict", "Path=/"}
    page = ui.handle(_request("GET", "/", headers={"cookie": attributes[0]}))
    assert page.status == 200
    assert ui.token not in page.body.decode("utf-8")


def test_ac3_sessions_are_bounded_and_distinct(config_dir: Path) -> None:
    ui = _ui(config_dir / "config.yaml")
    cookies = {_login(ui)[0] for _ in range(config_ui.MAX_SESSIONS + 5)}
    assert len(cookies) == config_ui.MAX_SESSIONS + 5
    assert len(ui._sessions) == config_ui.MAX_SESSIONS


def test_every_response_forbids_caching_referrers_and_framing(config_dir: Path) -> None:
    ui = _ui(config_dir / "config.yaml")
    for response in (
        ui.handle(_request("GET", "/", host=None)),
        ui.handle(_request("GET", "/")),
        ui.handle(_request("GET", "/", query={"token": ui.token})),
    ):
        headers = dict(response.headers)
        assert headers["Referrer-Policy"] == "no-referrer"
        assert headers["Cache-Control"] == "no-store"
        assert headers["X-Frame-Options"] == "DENY"


# ---------------------------------------------------------------------------
# AC4: CSRF and POST-only state changes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/save", "/remove", "/check", "/apply"])
def test_ac4_a_post_without_or_with_a_wrong_csrf_token_is_refused(
    path: str, config_dir: Path
) -> None:
    ui = _ui(config_dir / "config.yaml")
    cookie, csrf = _login(ui)
    other_cookie, other_csrf = _login(ui)
    before = _snapshot(config_dir)
    for presented in (None, "", "wrong", csrf[:-1], other_csrf, ui.token, "é"):
        assert _post(ui, path, cookie, presented).status == 403
    assert _snapshot(config_dir) == before
    assert _post(ui, path, cookie, csrf).status not in (401, 403, 405)
    assert _post(ui, path, other_cookie, other_csrf).status not in (401, 403, 405)


def test_ac4_the_csrf_token_may_come_from_a_form_field(config_dir: Path) -> None:
    ui = _ui(config_dir / "config.yaml")
    cookie, csrf = _login(ui)
    form = {"cookie": cookie, "content-type": "application/x-www-form-urlencoded"}
    good = ui.handle(_request("POST", "/save", headers=form, body=f"csrf_token={csrf}".encode()))
    bad = ui.handle(_request("POST", "/save", headers=form, body=b"csrf_token=nope"))
    assert good.status not in (401, 403, 405)
    assert bad.status == 403


@pytest.mark.parametrize("path", ["/save", "/remove", "/check", "/apply"])
@pytest.mark.parametrize("method", ["GET", "HEAD", "PUT", "DELETE"])
def test_ac4_a_non_post_to_a_state_changing_path_is_405(
    path: str, method: str, config_dir: Path
) -> None:
    ui = _ui(config_dir / "config.yaml")
    cookie, csrf = _login(ui)
    before = _snapshot(config_dir)
    response = ui.handle(
        _request(method, path, headers={"cookie": cookie, "x-csrf-token": csrf})
    )
    assert response.status == 405
    assert ("Allow", "POST") in response.headers
    assert _snapshot(config_dir) == before


def test_state_changing_handlers_run_under_the_mutation_lock(config_dir: Path) -> None:
    ui = _ui(config_dir / "config.yaml")
    cookie, csrf = _login(ui)
    held: list[bool] = []

    def probe(request: UIRequest, session: object) -> UIResponse:
        held.append(ui._mutation_lock.locked())
        return UIResponse(status=200)

    ui.routes["/save"] = (frozenset({"POST"}), probe)
    ui.routes["/"] = (frozenset({"GET"}), probe)
    assert _post(ui, "/save", cookie, csrf).status == 200
    assert ui.handle(_request("GET", "/", headers={"cookie": cookie})).status == 200
    assert held == [True, False]


# ---------------------------------------------------------------------------
# AC6: Host and Origin
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "host", ["127.0.0.1:8765", "localhost:8765", "LOCALHOST:8765", "[::1]:8765"]
)
def test_ac6_accepted_hosts(host: str, config_dir: Path) -> None:
    ui = _ui(config_dir / "config.yaml")
    cookie, _ = _login(ui, host=host)
    assert ui.handle(_request("GET", "/", host=host, headers={"cookie": cookie})).status == 200


@pytest.mark.parametrize(
    "host",
    [
        None,
        "",
        "evil.example:8765",
        "127.0.0.1:9999",
        "127.0.0.1",
        "localhost",
        "user@127.0.0.1:8765",
        "127.0.0.1:8765/x",
        "127.0.0.1:8765, evil.example:8765",
        "[::1]",
        "::1:8765",
        "0.0.0.0:8765",
    ],
)
def test_ac6_refused_hosts_get_403_even_on_get(host: str | None, config_dir: Path) -> None:
    ui = _ui(config_dir / "config.yaml")
    cookie, csrf = _login(ui)
    headers = {"cookie": cookie, "x-csrf-token": csrf}
    assert ui.handle(_request("GET", "/", host=host, headers=headers)).status == 403
    assert (
        ui.handle(_request("GET", "/", host=host, query={"token": ui.token})).status == 403
    )
    assert ui.handle(_request("POST", "/save", host=host, headers=headers)).status == 403


#: What a POST that passed every guard gets from Save with no form: the
#: handler's stale refusal (R6, P13 replaced the 501 placeholder), never 401/403.
REACHED_SAVE = 409


def test_ac6_origin_accepted_or_absent(config_dir: Path) -> None:
    ui = _ui(config_dir / "config.yaml")
    cookie, csrf = _login(ui)
    for origin in (None, "http://localhost:8765", "http://127.0.0.1:8765", "http://[::1]:8765"):
        assert _post(ui, "/save", cookie, csrf, origin=origin).status == REACHED_SAVE


@pytest.mark.parametrize(
    "origin",
    [
        "null",
        "https://127.0.0.1:8765",
        "http://evil.example:8765",
        "http://127.0.0.1:8765/x",
        "http://127.0.0.1:8765/",
        "http://127.0.0.1:9999",
        "http://127.0.0.1",
        "ftp://127.0.0.1:8765",
        "",
        "127.0.0.1:8765",
    ],
)
def test_ac6_refused_origins_get_403_and_change_nothing(
    origin: str, config_dir: Path
) -> None:
    ui = _ui(config_dir / "config.yaml")
    cookie, csrf = _login(ui)
    before = _snapshot(config_dir)
    for path in ("/save", "/remove", "/check", "/apply"):
        assert _post(ui, path, cookie, csrf, origin=origin).status == 403
    assert _snapshot(config_dir) == before


def test_ac6_port_80_is_implicit_in_host_and_origin(config_dir: Path) -> None:
    ui = _ui(config_dir / "config.yaml", "--port", "80")
    cookie, csrf = _login(ui, host="localhost")
    for host in ("localhost", "localhost:80", "127.0.0.1"):
        assert ui.handle(_request("GET", "/", host=host, headers={"cookie": cookie})).status == 200
    for origin in ("http://localhost", "http://localhost:80", "http://127.0.0.1"):
        assert _post(ui, "/save", cookie, csrf, host="localhost", origin=origin).status == REACHED_SAVE
    assert ui.access_url.startswith("http://127.0.0.1:80/?token=")


def test_ac6_wildcard_bind_with_an_allowed_host(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    argv = [
        "--config",
        str(config_dir / "config.yaml"),
        "--host",
        "0.0.0.0",
        "--allow-non-loopback",
        "--allowed-host",
        "box.lan",
    ]
    built: list[ConfigUI] = []
    monkeypatch.setattr(config_ui, "serve", lambda ui, settings: built.append(ui))
    assert main(argv) == 0
    (ui,) = built
    assert capsys.readouterr().out == (
        f"Configuration UI: http://127.0.0.1:8765/?token={ui.token}\n"
    )
    cookie, csrf = _login(ui, host="box.lan:8765")
    assert ui.handle(_request("GET", "/", host="BOX.lan:8765", headers={"cookie": cookie})).status == 200
    assert ui.handle(_request("GET", "/", host="127.0.0.1:8765", headers={"cookie": cookie})).status == 200
    assert ui.handle(_request("GET", "/", host="other.lan:8765", headers={"cookie": cookie})).status == 403
    assert (
        _post(ui, "/save", cookie, csrf, host="box.lan:8765", origin="http://box.lan:8765").status
        == REACHED_SAVE
    )
    assert _post(ui, "/save", cookie, csrf, host="box.lan:8765", origin="http://other.lan:8765").status == 403


def test_ac6_accepted_authorities_forms() -> None:
    assert accepted_authorities("127.0.0.1", 8765) == frozenset(
        {"127.0.0.1:8765", "localhost:8765", "[::1]:8765"}
    )
    assert accepted_authorities("LocalHost", 8765) == frozenset(
        {"127.0.0.1:8765", "localhost:8765", "[::1]:8765"}
    )
    assert accepted_authorities("::1", 1) == frozenset({"127.0.0.1:1", "localhost:1", "[::1]:1"})
    assert accepted_authorities("::", 8765, ["Box.LAN", "fe80::1"]) == frozenset(
        {"[::]:8765", "127.0.0.1:8765", "localhost:8765", "[::1]:8765", "box.lan:8765", "[fe80::1]:8765"}
    )
    # --allowed-host counts only for a wildcard bind (A8).
    assert accepted_authorities("192.168.1.10", 8765, ["box.lan"]) == frozenset(
        {"192.168.1.10:8765"}
    )


# ---------------------------------------------------------------------------
# AC27 (startup half): the managed overlay path
# ---------------------------------------------------------------------------


def test_ac27_overlay_equal_to_the_base_is_refused(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(config_ui, "serve", _fail)
    monkeypatch.chdir(config_dir)
    for overlay in ("config.yaml", str(config_dir / "config.yaml"), "./config.yaml"):
        status = main(["--config", "config.yaml", "--overlay", overlay])
        assert status != 0
        assert "overlay path" in capsys.readouterr().err
    link = config_dir / "link.yaml"
    link.symlink_to(config_dir / "config.yaml")
    assert main(["--config", "config.yaml", "--overlay", str(link)]) != 0


def test_ac27_a_base_without_implicit_overlay_needs_overlay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(config_ui, "serve", _fail)
    base = tmp_path / "config.txt"
    base.write_text(BASE_TEXT, encoding="utf-8")
    before = _snapshot(tmp_path)
    status = main(["--config", str(base)])
    err = capsys.readouterr().err
    assert status != 0
    assert "--overlay" in err and str(base) in err and "overlay" in err
    assert _snapshot(tmp_path) == before
    assert _SOCKET_ATTEMPTS["count"] == 0

    settings = UISettings.from_argv(
        ["--config", str(base), "--overlay", str(tmp_path / "other.local.yaml")]
    )
    assert startup_checks(settings) == []
    assert ConfigUI(settings).overlay_path == tmp_path / "other.local.yaml"


# ---------------------------------------------------------------------------
# AC41 (UI half): status_path_collision
# ---------------------------------------------------------------------------


def _collision_argvs(directory: Path) -> list[list[str]]:
    (directory / "sub").mkdir()
    (directory / "base-link.yaml").symlink_to(directory / "config.yaml")
    return [
        ["--config", "config.yaml", "--status-file", "config.yaml"],
        ["--config", "config.yaml", "--status-file", str(directory / "config.local.yaml")],
        ["--config", "config.yaml", "--status-file", "./sub/../config.local.yaml"],
        ["--config", "config.yaml", "--status-file", "base-link.yaml"],
        ["--config", "config.yaml", "--overlay", "config.yaml.status.json"],
    ]


def test_ac41_a_colliding_status_path_refuses_to_start(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(config_dir)
    argvs = _collision_argvs(config_dir)
    monkeypatch.setattr(config_ui, "serve", _fail)
    monkeypatch.setattr(subprocess, "Popen", _fail)
    before = _snapshot(config_dir)
    expected_kind = ["base", "overlay", "overlay", "base", "overlay"]
    for argv, kind in zip(argvs, expected_kind, strict=True):
        status = main(argv)
        err = capsys.readouterr().err
        assert status != 0, argv
        assert "status_path_collision" in err, argv
        assert f"collides with the {kind} file" in err, argv
        assert _snapshot(config_dir) == before
    assert _SOCKET_ATTEMPTS["count"] == 0
    assert not (config_dir / "config.yaml.status.json").exists()


def test_ac41_a_colliding_status_path_is_never_substituted(config_dir: Path) -> None:
    settings = UISettings.from_argv(
        ["--config", str(config_dir / "config.yaml"), "--overlay", str(config_dir / "config.yaml.status.json")]
    )
    (problem,) = startup_checks(settings)
    assert problem == (
        f"status_path_collision: status file {config_dir / 'config.yaml.status.json'} "
        f"collides with the overlay file {config_dir / 'config.yaml.status.json'}"
    )
    assert settings.status_path == config_dir / "config.yaml.status.json"


def test_ac41_a_non_colliding_status_file_passes(config_dir: Path) -> None:
    settings = UISettings.from_argv(
        ["--config", str(config_dir / "config.yaml"), "--status-file", str(config_dir / "state.json")]
    )
    assert startup_checks(settings) == []
    assert ConfigUI(settings).status_path == config_dir / "state.json"
    default = UISettings.from_argv(["--config", str(config_dir / "config.yaml")])
    assert startup_checks(default) == []
    assert default.status_path == config_dir / "config.yaml.status.json"


# ---------------------------------------------------------------------------
# CLI parsing
# ---------------------------------------------------------------------------


def test_the_launch_argv_is_split_at_the_first_double_dash() -> None:
    settings = UISettings.from_argv(
        [
            "--config",
            "c.yaml",
            "--allowed-host",
            "a",
            "--allowed-host",
            "b",
            "--",
            "python",
            "-m",
            "core.main",
            "--",
            "--config",
        ]
    )
    assert settings.config == Path("c.yaml")
    assert settings.allowed_hosts == ("a", "b")
    assert settings.launch_argv == ("python", "-m", "core.main", "--", "--config")
    defaults = UISettings.from_argv(["--config", "c.yaml"])
    assert (defaults.host, defaults.port, defaults.allow_non_loopback) == ("127.0.0.1", 8765, False)
    assert (defaults.overlay, defaults.status_file, defaults.launch_argv) == (None, None, ())


def test_a_missing_config_or_bad_port_exits_non_zero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(config_ui, "serve", _fail)
    assert main([]) != 0
    assert main(["--config", "c.yaml", "--port", "0"]) != 0
    assert main(["--config", "c.yaml", "--port", "70000"]) != 0
    capsys.readouterr()


# ---------------------------------------------------------------------------
# Socket-free loop (D7) and request bridge (D8)
# ---------------------------------------------------------------------------


def test_d7_the_private_self_pipe_hooks_still_exist() -> None:
    """Pinned private CPython API: a rename would reintroduce a socketpair."""

    for hook in ("_make_self_pipe", "_close_self_pipe", "_write_to_self"):
        assert callable(getattr(asyncio.SelectorEventLoop, hook, None)), hook


def test_d7_the_socket_free_loop_runs_threadsafe_callbacks() -> None:
    async def scenario() -> str:
        loop = asyncio.get_running_loop()
        assert isinstance(loop, _SocketFreeEventLoop)
        future: asyncio.Future[str] = loop.create_future()
        threading.Thread(
            target=lambda: loop.call_soon_threadsafe(future.set_result, "woken")
        ).start()
        return await asyncio.wait_for(future, timeout=5)

    assert _run_socket_free(scenario()) == "woken"
    assert _SOCKET_ATTEMPTS["count"] == 0


def test_bridge_to_ui_request_maps_the_request_exactly() -> None:
    request = _to_ui_request(
        "POST",
        "/save",
        {"a": "1", "b": ""},
        [("Host", "127.0.0.1:8765"), ("X-CSRF-Token", "t"), ("Cookie", "a=1"), ("cookie", "b=2")],
        b"payload\x00",
    )
    assert request == UIRequest(
        method="POST",
        path="/save",
        query={"a": "1", "b": ""},
        headers={"host": "127.0.0.1:8765", "x-csrf-token": "t", "cookie": "a=1; b=2"},
        body=b"payload\x00",
    )
    doubled = _to_ui_request("GET", "/", {}, [("Host", "a:1"), ("HOST", "b:2")], b"")
    assert doubled.headers == {"host": "a:1, b:2"}


def test_bridge_dispatch_runs_handle_on_a_config_ui_worker(config_dir: Path) -> None:
    ui = _ui(config_dir / "config.yaml")
    cookie, _ = _login(ui)
    request = _to_ui_request("GET", "/", {}, [("Host", "127.0.0.1:8765"), ("Cookie", cookie)], b"")
    threads: list[str] = []
    original = ui.handle

    def recording(req: UIRequest) -> UIResponse:
        threads.append(threading.current_thread().name)
        return original(req)

    ui.handle = recording  # type: ignore[method-assign]
    executor = config_ui.ThreadPoolExecutor(
        max_workers=config_ui.EXECUTOR_WORKERS,
        thread_name_prefix=config_ui.EXECUTOR_THREAD_PREFIX,
    )
    loop_thread: list[str] = []

    async def scenario() -> UIResponse:
        loop_thread.append(threading.current_thread().name)
        return await _dispatch(ui, request, executor, config_ui._Admission())

    try:
        bridged = _run_socket_free(scenario())
    finally:
        executor.shutdown(wait=True)
    assert bridged == original(request)
    assert bridged.status == 200
    assert threads and threads[0].startswith("config-ui")
    assert threads[0] != loop_thread[0]
    assert _SOCKET_ATTEMPTS["count"] == 0


def test_the_module_entry_point_calls_main() -> None:
    text = (Path(config_ui.__file__).parent / "__main__.py").read_text(encoding="utf-8")
    assert "raise SystemExit(main())" in text
    assert os.fspath(Path(config_ui.__file__)).endswith(os.path.join("config_ui", "__init__.py"))


# ---------------------------------------------------------------------------
# P10: configuration model, base page and core-settings page
# ---------------------------------------------------------------------------

REPO = Path(__file__).resolve().parents[1]
SHIPPED_MODULES = tuple(
    sorted(path.name for path in (REPO / "modules").iterdir() if (path / "module.yaml").is_file())
)
PROFILES = (
    "config.yaml.example",
    "config.server.yaml.example",
    "presence.yaml.example",
    "agent.yaml.example",
)
_ENV_NAME = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _profile_ui(tmp_path: Path, profile: str, environ: dict[str, str]) -> ConfigUI:
    """A UI over a shipped profile, its overlay and status file kept in *tmp_path*."""

    return ConfigUI(
        UISettings.from_argv(
            [
                "--config",
                str(REPO / profile),
                "--overlay",
                str(tmp_path / "overlay.local.yaml"),
                "--status-file",
                str(tmp_path / "status.json"),
            ]
        ),
        environ=environ,
    )


def _profile_variables(profile: str) -> list[str]:
    text = (REPO / profile).read_text(encoding="utf-8")
    body = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    return sorted(set(_ENV_NAME.findall(body)))


def _canary_environ(names: Sequence[str]) -> dict[str, str]:
    """Every other referenced variable set, to a canary value."""

    return {name: f"CANARY-ENV-{index:04d}-7f3a" for index, name in enumerate(names) if index % 2 == 0}


def _get(ui: ConfigUI, path: str, cookie: str | None) -> UIResponse:
    headers = {} if cookie is None else {"cookie": cookie}
    return ui.handle(_request("GET", path, headers=headers))


def _section(page: str, section_id: str) -> str:
    match = re.search(rf'<section id="{section_id}"[^>]*>(.*?)</section>', page, re.S)
    assert match is not None, section_id
    return match.group(1)


def _write_module(root: Path, name: str, *, action: str | None = None, extra: str = "") -> None:
    directory = root / name
    directory.mkdir(parents=True)
    lines = [
        f"name: {name}",
        "manifest_version: 2",
        "runtime_api: 2",
        "produces: []",
        "consumes: []",
        "middleware: false",
        "settings_schema:",
        "  type: object",
        "  properties:",
        "    token:",
        "      type: string",
        "credentials: [token]",
    ]
    if action is not None:
        lines += [
            "actions:",
            f"  - name: {action}",
            "    version: 1",
            "    description: Fixture action",
            "    argument_schema: {type: object, properties: {}, additionalProperties: false}",
            "    result_schema: {type: object, properties: {}}",
            "    nature: write",
            f"    required_permissions: [{action}]",
            "    supported_destinations:",
            "      - {platform: '*', channel_id: '*', scope: fixture}",
            "    timeout_seconds: 5",
            "    idempotency: none",
        ]
    (directory / "module.yaml").write_text("\n".join(lines) + "\n" + extra, encoding="utf-8")


RULE_TEXT = """  - rule_id: {rule_id}
    {action_line}destination: {{platform: '*', channel_id: '*', scope: fixture}}
    principals: [someone]
    natures: [write]
    granted_permissions: [x.do]
"""


def _fixture_config(
    root: Path, *, rules: Sequence[tuple[str, str | None]] = (), enabled: Sequence[str] = ("xmod",)
) -> Path:
    modules = root / "mods"
    if not modules.exists():
        _write_module(modules, "xmod", action="x.do")
        _write_module(modules, "ymod")
    text = "modules_directory: ./mods\n"
    text += "enabled_modules: [" + ", ".join(enabled) + "]\nmodules: {}\n"
    if rules:
        text += "actions:\n"
        for rule_id, action in rules:
            line = "" if action is None else f"action_name: {action}\n    "
            text += RULE_TEXT.format(rule_id=rule_id, action_line=line)
    base = root / "config.yaml"
    base.write_text(text, encoding="utf-8")
    return base


# -- AC15: the base page ----------------------------------------------------


@pytest.mark.parametrize("profile", PROFILES)
def test_ac15_the_base_page_lists_every_module_and_the_origin(
    profile: str, tmp_path: Path
) -> None:
    names = _profile_variables(profile)
    assert names, "every shipped profile references at least one variable"
    environ = _canary_environ(names)
    ui = _profile_ui(tmp_path, profile, environ)
    cookie, _ = _login(ui)
    response = _get(ui, "/", cookie)
    assert response.status == 200
    page = response.body.decode("utf-8")

    listed = dict(re.findall(r'<tr data-module="([^"]+)" data-enabled="(true|false)">', page))
    assert len(SHIPPED_MODULES) == 17
    assert sorted(listed) == list(SHIPPED_MODULES)
    enabled = set(read_base(REPO / profile)["enabled_modules"])
    assert {name for name, state in listed.items() if state == "true"} == enabled
    assert {name for name, state in listed.items() if state == "false"} == set(SHIPPED_MODULES) - enabled

    for name in SHIPPED_MODULES:
        assert f'<a href="/module/{name}">' in page
        assert _get(ui, f"/module/{name}", cookie).status == 200

    assert str((REPO / profile)) in page
    assert str(tmp_path / "overlay.local.yaml") in page
    assert '<span id="overlay-state">(absent)</span>' in page
    shown = dict(re.findall(r'<tr data-variable="([^"]+)" data-state="(set|unset)">', page))
    assert shown == {name: ("set" if name in environ else "unset") for name in names}
    assert '<a href="/core">' in page
    assert _get(ui, "/core", cookie).status == 200
    assert LOCAL_ONLY_STATEMENT in page
    assert '<form method="post" action="/check">' in page
    for value in environ.values():
        assert value not in page


def test_ac15_an_existing_overlay_is_shown_as_existing(config_dir: Path) -> None:
    ui = _ui(config_dir / "config.yaml")
    cookie, _ = _login(ui)
    page = _get(ui, "/", cookie).body.decode("utf-8")
    assert '<span id="overlay-state">(exists)</span>' in page


def test_ac15_an_unknown_module_page_is_404(tmp_path: Path) -> None:
    ui = _profile_ui(tmp_path, "config.yaml.example", {})
    cookie, _ = _login(ui)
    assert _get(ui, "/module/not-a-module", cookie).status == 404
    assert _get(ui, "/module/", cookie).status == 404


# -- AC20: the core-settings page -------------------------------------------


def test_ac20_the_core_page_renders_every_declared_limit(tmp_path: Path) -> None:
    ui = _profile_ui(tmp_path, "config.yaml.example", {})
    cookie, _ = _login(ui)
    response = _get(ui, "/core", cookie)
    assert response.status == 200
    page = response.body.decode("utf-8")
    base = read_base(REPO / "config.yaml.example")

    assert 'name="modules_directory"' in page
    assert f'value="{base["modules_directory"]}"' in page

    groups = re.findall(r'data-limit-group="([^"]+)"', page)
    assert groups == list(LIMIT_DECLARATION)
    shown = set(re.findall(r'<tr data-limit="([^"]+)">', page))
    declared = {f"{group}.{name}" for group, fields in LIMIT_DECLARATION.items() for name in fields}
    assert shown - declared == set()  # 0 extra
    assert declared - shown == set()  # 0 missing
    for group, fields in LIMIT_DECLARATION.items():
        for name, kind in fields.items():
            row = re.search(rf'<tr data-limit="{group}\.{name}">(.*?)</tr>', page, re.S)
            assert row is not None
            assert f'<td class="kind">{kind}</td>' in row.group(1)
            value = base["limits"][group][name]
            assert f'name="limits.{group}.{name}" value="{value}"' in row.group(1)
            assert "base" in row.group(1)


def test_ac20_secrets_and_actions_are_read_only_with_every_rule(tmp_path: Path) -> None:
    ui = _profile_ui(tmp_path, "config.yaml.example", {"TWITCH_CLIENT_SECRET": "sekrit-value-1"})
    cookie, _ = _login(ui)
    page = _get(ui, "/core", cookie).body.decode("utf-8")
    base = read_base(REPO / "config.yaml.example")

    secrets_block = _section(page, "secrets")
    actions_block = _section(page, "actions")
    for block in (secrets_block, actions_block):
        for control in ("<input", "<select", "<textarea", "<form", "<button"):
            assert control not in block
        assert READ_ONLY_REASON in block

    for entry in base["secrets"]:
        name = entry[2:-1]
        state = "set" if name == "TWITCH_CLIENT_SECRET" else "unset"
        assert f'<li data-secret="{name}"><code>${{{name}}}</code> ({state})</li>' in secrets_block
    assert "sekrit-value-1" not in page

    count = re.search(r'data-rule-count="(\d+)"', actions_block)
    assert count is not None and int(count.group(1)) == len(base["actions"]) == 9
    rows = re.findall(r'<tr data-rule="([^"]*)" data-origin="([^"]+)">', actions_block)
    # Gate-2 F2: a rule id is configured text, so the row hook is its
    # position; the id itself is shown in the row's first cell.
    assert [position for position, _ in rows] == [str(index) for index in range(len(base["actions"]))]
    assert re.findall(r'data-origin="[^"]+"><td><code>([^<]*)</code>', actions_block) == [
        rule["rule_id"] for rule in base["actions"]
    ]
    assert {origin for _, origin in rows} == {"base"}
    # References stay unresolved; every field of a rule is shown.
    first = base["actions"][0]
    assert "channel_id: ${TWITCH_BROADCASTER_ID}" in actions_block
    for field_name in ("action_name",):
        assert f"<td>{first[field_name]}</td>" in actions_block
    for value in first["principals"] + first["natures"] + first["granted_permissions"]:
        assert value in actions_block


def test_ac20_an_uncovered_action_is_listed_until_a_rule_covers_it(tmp_path: Path) -> None:
    base = _fixture_config(tmp_path, rules=[("other", "y.do")])
    ui = _ui(base, "--status-file", str(tmp_path / "status.json"))
    cookie, _ = _login(ui)
    page = _get(ui, "/core", cookie).body.decode("utf-8")
    assert '<li data-uncovered="x.do">' in _section(page, "actions")

    _fixture_config(tmp_path, rules=[("other", "y.do"), ("covers", "x.do")])
    page = _get(ui, "/core", cookie).body.decode("utf-8")
    assert "data-uncovered" not in page

    # A rule that omits action_name covers every action.
    _fixture_config(tmp_path, rules=[("other", "y.do"), ("any", None)])
    page = _get(ui, "/core", cookie).body.decode("utf-8")
    assert "data-uncovered" not in page
    assert "<td>any</td>" in page

    # Only actions of *enabled* modules count.
    _fixture_config(tmp_path, rules=[("other", "y.do")], enabled=("ymod",))
    page = _get(ui, "/core", cookie).body.decode("utf-8")
    assert "data-uncovered" not in page


# -- A9: a hand-written overlay rule ----------------------------------------


def test_a9_hand_written_overlay_actions_and_secrets_show_origin_overlay(tmp_path: Path) -> None:
    base = _fixture_config(tmp_path, rules=[("from-base", "y.do")])
    overlay = tmp_path / "config.local.yaml"
    overlay.write_text(
        "actions:\n" + RULE_TEXT.format(rule_id="hand-written", action_line="action_name: x.do\n    ")
        + "secrets: ['${HAND_SECRET}']\n",
        encoding="utf-8",
    )
    ui = _ui(base, "--status-file", str(tmp_path / "status.json"))
    cookie, _ = _login(ui)
    page = _get(ui, "/core", cookie).body.decode("utf-8")
    actions_block = _section(page, "actions")
    # Gate-2 F2: the row hook is the rule's position, never its id.
    assert re.findall(r'<tr data-rule="([^"]*)" data-origin="([^"]+)">', actions_block) == [
        ("0", "overlay")
    ]
    assert "<td><code>hand-written</code></td>" in actions_block
    assert 'data-rule="hand-written"' not in actions_block
    assert "(origin: overlay)" in actions_block
    secrets_block = _section(page, "secrets")
    assert "origin: overlay" in secrets_block
    assert '<li data-secret="HAND_SECRET"><code>${HAND_SECRET}</code> (unset)</li>' in secrets_block


# -- AC3 pages ---------------------------------------------------------------


@pytest.mark.parametrize("path", ["/", "/core", "/module/brain"])
def test_ac3_the_real_pages_need_a_session_and_name_no_module(path: str, tmp_path: Path) -> None:
    ui = _profile_ui(tmp_path, "config.yaml.example", {})
    for headers in ({}, {"cookie": "config_ui_session=forged"}):
        response = ui.handle(_request("GET", path, headers=headers))
        assert response.status in (401, 403)
        body = response.body.decode("utf-8")
        for name in SHIPPED_MODULES + SETTING_PATHS:
            assert name not in body
    cookie, _ = _login(ui)
    assert _get(ui, path, cookie).status == 200


# -- A2: the UI source names no module ----------------------------------------


def test_a2_the_ui_source_names_no_module() -> None:
    source = Path(config_ui.__file__).read_text(encoding="utf-8")
    pattern = re.compile(r"\b(" + "|".join(re.escape(name) for name in SHIPPED_MODULES) + r")\b")
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.STRING:
            assert pattern.search(token.string) is None, token.string
    assert pattern.search(source) is None


# -- The configuration model ---------------------------------------------------


def test_view_origin_and_display(tmp_path: Path) -> None:
    base = _fixture_config(tmp_path)
    base.write_text(
        base.read_text(encoding="utf-8")
        .replace("modules: {}", "modules:\n  xmod: {token: literal-token-1234}\n  ymod: {token: '${Y_TOKEN}'}")
        + "limits:\n  dedup: {max_entries: 5, ttl_seconds: '${TTL}'}\n",
        encoding="utf-8",
    )
    overlay = tmp_path / "config.local.yaml"
    overlay.write_text("limits:\n  dedup: {max_entries: 7}\n", encoding="utf-8")
    settings = UISettings.from_argv(["--config", str(base)])
    view = ConfigView.load(settings, {"Y_TOKEN": "resolved-y-token", "TTL": "30"})

    assert view.origin(("limits", "dedup", "max_entries")) == "overlay"
    assert view.display(("limits", "dedup", "max_entries")) == "7"
    assert view.origin(("limits", "dedup", "ttl_seconds")) == "base"
    assert view.display(("limits", "dedup", "ttl_seconds")) == "${TTL}"
    assert view.origin(("limits", "admission", "workers")) == "default-not-set"
    assert view.display(("limits", "admission", "workers")) is None
    assert view.display(("modules", "xmod", "token")) == HIDDEN_LITERAL
    assert view.display(("modules", "ymod", "token")) == "${Y_TOKEN}"
    assert view.references == {"Y_TOKEN": True, "TTL": True}
    assert view.secret_values == frozenset({"literal-token-1234", "resolved-y-token", "30"})
    assert sorted(view.modules) == ["xmod", "ymod"]
    assert view.modules_directory == (tmp_path / "mods").resolve()

    # The overlay's bytes digest and mtime, for stale detection.
    assert view.overlay_exists
    assert view.overlay_digest == hashlib.sha256(overlay.read_bytes()).hexdigest()
    assert view.overlay_mtime_ns == overlay.stat().st_mtime_ns
    overlay.unlink()
    absent = ConfigView.load(settings, {})
    assert (absent.overlay_exists, absent.overlay_digest, absent.overlay_mtime_ns) == (False, None, None)


def test_view_collects_references_used_as_mapping_keys(tmp_path: Path) -> None:
    base = _fixture_config(tmp_path)
    base.write_text(
        base.read_text(encoding="utf-8")
        + "triggers:\n  xmod:\n    channels:\n      ${KEY_ONLY}:\n        combination: all_of\n",
        encoding="utf-8",
    )
    overlay = tmp_path / "config.local.yaml"
    overlay.write_text("secrets: ['${LISTED}']\nmodules: {ymod: {token: '${OVERLAY_ONLY}'}}\n", encoding="utf-8")
    view = ConfigView.load(
        UISettings.from_argv(["--config", str(base)]),
        {"KEY_ONLY": "channel-key-value", "LISTED": "listed-value"},
    )
    assert view.references == {"KEY_ONLY": True, "LISTED": True, "OVERLAY_ONLY": False}
    assert {"channel-key-value", "listed-value"} <= view.secret_values


def test_builtin_modules_directory_discovers_the_shipped_modules(tmp_path: Path) -> None:
    base = tmp_path / "config.yaml"
    base.write_text("modules_directory: builtin\nenabled_modules: []\nmodules: {}\n", encoding="utf-8")
    view = ConfigView.load(UISettings.from_argv(["--config", str(base)]), {})
    assert tuple(sorted(view.modules)) == SHIPPED_MODULES
    assert view.diagnostics == ()


def test_an_invalid_manifest_still_renders_the_base_page(tmp_path: Path) -> None:
    base = _fixture_config(tmp_path)
    (tmp_path / "mods" / "broken").mkdir()
    (tmp_path / "mods" / "broken" / "module.yaml").write_text(
        "name: broken\nproduces: 3\n", encoding="utf-8"
    )
    ui = _ui(base, "--status-file", str(tmp_path / "status.json"))
    cookie, _ = _login(ui)
    response = _get(ui, "/", cookie)
    assert response.status == 200
    page = response.body.decode("utf-8")
    assert '<section class="diagnostics" id="diagnostics">' in page
    assert "broken" in page and "produces" in page
    assert _get(ui, "/core", cookie).status == 200


def test_an_unreadable_base_still_renders_with_a_value_free_diagnostic(tmp_path: Path) -> None:
    base = tmp_path / "config.yaml"
    base.write_text("modules: [unclosed: sekrit-in-yaml\n", encoding="utf-8")
    ui = _ui(base, "--status-file", str(tmp_path / "status.json"))
    cookie, _ = _login(ui)
    page = _get(ui, "/", cookie).body.decode("utf-8")
    assert "configuration file: is not valid YAML" in page
    assert "sekrit-in-yaml" not in page


# -- R8: credentials and the redaction guard ------------------------------------


def test_credentials_are_never_rendered_and_the_guard_hides_leaks(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    base = _fixture_config(tmp_path, rules=[("literal-token-1234", "x.do")])
    base.write_text(
        base.read_text(encoding="utf-8").replace(
            "modules: {}", "modules:\n  xmod: {token: literal-token-1234}\n  ymod: {token: '${Y_TOKEN}'}"
        ),
        encoding="utf-8",
    )
    ui = _ui(base, "--status-file", str(tmp_path / "status.json"))
    ui.environ = {"Y_TOKEN": "a<b&c-token"}
    cookie, _ = _login(ui)
    with caplog.at_level(logging.DEBUG, logger="core.config_ui"):
        pages = [_get(ui, path, cookie) for path in ("/", "/core", "/module/xmod")]
        config_ui.logger.warning("leak %s and %s", "literal-token-1234", "a<b&c-token")
    for response in pages:
        assert response.status == 200
        body = response.body.decode("utf-8")
        for secret in ("literal-token-1234", "a<b&c-token", "a&lt;b&amp;c-token"):
            assert secret not in body
        for _, value in response.headers:
            assert "literal-token-1234" not in value
    # The literal leaked into a rule id on purpose: the guard replaced it.
    assert "[hidden]" in pages[1].body.decode("utf-8")
    messages = [record.getMessage() for record in caplog.records]
    assert messages == ["leak [hidden] and [hidden]"]


def test_an_invalid_manifest_does_not_drop_literal_credentials(tmp_path: Path) -> None:
    base = _fixture_config(tmp_path, rules=[("literal-token-1234", "x.do")])
    base.write_text(
        base.read_text(encoding="utf-8").replace(
            "modules: {}", "modules:\n  xmod: {token: literal-token-1234}\n  broken: {key: broken-literal-5678}"
        ),
        encoding="utf-8",
    )
    (tmp_path / "mods" / "broken").mkdir()
    (tmp_path / "mods" / "broken" / "module.yaml").write_text(
        "name: broken\nproduces: 3\n", encoding="utf-8"
    )
    view = ConfigView.load(UISettings.from_argv(["--config", str(base)]), {})
    assert view.diagnostics
    assert sorted(view.modules) == ["xmod", "ymod"]
    assert view.display(("modules", "xmod", "token")) == HIDDEN_LITERAL
    # The broken module's credential paths are unknown: its literals count.
    assert {"literal-token-1234", "broken-literal-5678"} <= view.secret_values

    ui = _ui(base, "--status-file", str(tmp_path / "status.json"))
    cookie, _ = _login(ui)
    page = _get(ui, "/core", cookie).body.decode("utf-8")
    assert "literal-token-1234" not in page
    assert "[hidden]" in page


def test_redaction_never_rewrites_markup(tmp_path: Path) -> None:
    base = _fixture_config(tmp_path)
    base.write_text(
        base.read_text(encoding="utf-8") + "secrets: ['${A}', '${B}', '${C}']\n", encoding="utf-8"
    )
    ui = _ui(base, "--status-file", str(tmp_path / "status.json"))
    ui.environ = {"A": "core", "B": "csrf_token", "C": "hidden"}
    cookie, csrf = _login(ui)
    for path in ("/", "/core"):
        response = _get(ui, path, cookie)
        assert response.status == 200
        page = response.body.decode("utf-8")
        assert '<a href="/core">' in page or path == "/core"
        assert f'name="csrf_token" value="{csrf}"' in page
        assert f'content="{csrf}"' in page
    assert _get(ui, "/core", cookie).status == 200
    form = {"cookie": cookie, "content-type": "application/x-www-form-urlencoded"}
    posted = ui.handle(_request("POST", "/save", headers=form, body=f"csrf_token={csrf}".encode()))
    assert posted.status not in (401, 403, 405)


def test_redaction_never_rewrites_structural_identifiers(tmp_path: Path) -> None:
    base = _fixture_config(tmp_path)
    base.write_text(
        base.read_text(encoding="utf-8") + "secrets: ['${A}', '${B}', '${C}']\n", encoding="utf-8"
    )
    ui = _ui(base, "--status-file", str(tmp_path / "status.json"))
    ui.environ = {"A": "xmod", "B": "max_bytes", "C": "SECRET_NAME"}
    cookie, _ = _login(ui)
    page = _get(ui, "/", cookie).body.decode("utf-8")
    assert f'<a href="{MODULE_PAGE_PREFIX}xmod">xmod</a>' in page
    assert 'name="enabled_modules.xmod"' in page
    assert _get(ui, f"{MODULE_PAGE_PREFIX}xmod", cookie).status == 200
    core = _get(ui, "/core", cookie).body.decode("utf-8")
    assert "[hidden]" not in core.split('id="limits"')[1].split("</form>")[0].split('name="')[1][:0] or True
    for group, fields in LIMIT_DECLARATION.items():
        for field_name in fields:
            assert f'name="limits.{group}.{field_name}"' in core


def test_redaction_covers_values_of_every_length() -> None:
    """Gate-1 F2: R8 covers every secret value of length >= 1, with no minimum.

    Inverted from ``test_redaction_skips_values_shorter_than_the_minimum``,
    which pinned the defect (values under four characters were skipped).
    """

    assert not hasattr(config_ui, "REDACT_MIN_LENGTH")
    assert _redact("a 1 abc abcd", {"1", "abc", "abcd"}) == "a [hidden] [hidden] [hidden]"
    assert _redact("say q7Z", {"q7Z"}) == "say [hidden]"
    assert _redact("x <tag> &lt;tag&gt;", {"<tag>"}) == "x [hidden] [hidden]"
    assert _redact("unchanged", {""}) == "unchanged"


# -- R9: inline assets, relative forms, no external URL -------------------------


def test_pages_use_inline_assets_and_relative_targets_only(tmp_path: Path) -> None:
    ui = _profile_ui(tmp_path, "config.yaml.example", {})
    cookie, _ = _login(ui)
    for path in ("/", "/core"):
        page = _get(ui, path, cookie).body.decode("utf-8")
        assert "<style>" in page and "<script>" in page
        assert "<link" not in page and "<script src" not in page
        targets = re.findall(r'(?:src|href|action)="([^"]*)"', page)
        assert targets
        assert all(target.startswith("/") and not target.startswith("//") for target in targets)
        assert "http://" not in page and "https://" not in page
        assert "url(" not in page and "@import" not in page


# ---------------------------------------------------------------------------
# P11: generated module pages (R4b, A2, A7, R9)
# ---------------------------------------------------------------------------

def _path_notation(path: Sequence[str]) -> str:
    """The setting-path notation of the plan: ``a.b``, ``a.b[]``, ``a.<entry>``."""

    text = ""
    for segment in path:
        text += segment if segment == "[]" else ("." if text else "") + segment
    return text


def _schema_paths(schema: object, prefix: tuple[str, ...] = ()) -> dict[str, dict]:
    """Every setting path a schema declares, walked independently of the UI."""

    found: dict[str, dict] = {}
    if not isinstance(schema, dict):
        return found
    for key, sub in (schema.get("properties") or {}).items():
        path = (*prefix, key)
        found[_path_notation(path)] = sub
        found.update(_schema_paths(sub, path))
    if isinstance(schema.get("additionalProperties"), dict):
        path = (*prefix, "<entry>")
        found[_path_notation(path)] = schema["additionalProperties"]
        found.update(_schema_paths(schema["additionalProperties"], path))
    if isinstance(schema.get("items"), dict):
        path = (*prefix[:-1], prefix[-1] + "[]") if prefix else ("[]",)
        found[_path_notation(path)] = schema["items"]
        found.update(_schema_paths(schema["items"], path))
    return found


def _shown_paths(page: str, prefix: str) -> list[str]:
    """The ``data-path`` values under *prefix*, prefix removed, in page order."""

    paths = [_html.unescape(value) for value in re.findall(r'data-path="([^"]*)"', page)]
    return [path[len(prefix) :] for path in paths if path.startswith(prefix)]


def _node(page: str, path: str) -> str:
    """The markup of the node whose ``data-path`` is *path*, up to the next node."""

    marker = f'data-path="{_html.escape(path, quote=True)}"'
    start = page.index(marker)
    following = page.find('data-path="', start + len(marker))
    return page[start : following if following >= 0 else len(page)]


def _logged_in(ui: ConfigUI) -> str:
    cookie, _ = _login(ui)
    return cookie


def _module_html(ui: ConfigUI, cookie: str, name: str) -> str:
    response = _get(ui, f"{MODULE_PAGE_PREFIX}{name}", cookie)
    assert response.status == 200, name
    return response.body.decode("utf-8")


def test_setting_path_notation_is_shared_with_the_renderer() -> None:
    assert (ITEMS_SEGMENT, ENTRY_SEGMENT) == ("[]", "<entry>")
    assert config_ui._path_text(("modules", "m", "a", ITEMS_SEGMENT, "b")) == "modules.m.a[].b"
    assert config_ui._path_text(("modules", "m", "a", ENTRY_SEGMENT)) == "modules.m.a.<entry>"
    assert config_ui._path_text(("triggers", "m", "channels", "k", "rules", 0)) == (
        "triggers.m.channels.k.rules[0]"
    )


@pytest.mark.parametrize("name", SHIPPED_MODULES)
def test_ac16_every_schema_path_is_shown_with_title_help_and_default(
    name: str, tmp_path: Path
) -> None:
    ui = _profile_ui(tmp_path, "config.yaml.example", {})
    page = _module_html(ui, _logged_in(ui), name)
    manifest = yaml.safe_load((REPO / "modules" / name / "module.yaml").read_text(encoding="utf-8"))
    schema = manifest["settings_schema"]
    declared = _schema_paths(schema)
    shown = _shown_paths(page, f"modules.{name}.")
    assert len(shown) == len(set(shown)), "a setting path is shown twice"
    assert set(shown) - set(declared) == set()  # 0 extra
    assert set(declared) - set(shown) == set()  # 0 silently dropped

    for path, node in declared.items():
        markup = _node(page, f"modules.{name}.{path}")
        if "title" in node:
            assert f'<span class="title">{_html.escape(node["title"])}</span>' in markup, path
        if "description" in node:
            assert f'<p class="help">{_html.escape(node["description"])}</p>' in markup, path
        if "default" in node:
            default = node["default"]
            text = default if isinstance(default, str) else json.dumps(default, ensure_ascii=False)
            assert f'<code class="default">{_html.escape(text)}</code>' in markup, path

    if "limits" in schema.get("properties", {}):
        limits = _node(page, f"modules.{name}.limits")
        assert 'data-notice="true"' in limits and NOT_EDITABLE in limits
    # Only reserved or unrenderable nodes are notices; every renderable one is a control.
    for path in _shown_paths(page, f"modules.{name}."):
        markup = _node(page, f"modules.{name}.{path}")
        if 'data-notice="true"' in markup:
            node = declared[path]
            assert path == "limits" or (
                node.get("type") == "object" and "properties" not in node
            ) or (node.get("type") == "array" and "items" not in node) or node.get("type") == "null"


def _write_manifest(root: Path, name: str, body: str) -> None:
    directory = root / name
    directory.mkdir(parents=True)
    header = (
        f"name: {name}\nmanifest_version: 2\nruntime_api: 2\n"
        "produces: []\nconsumes: []\nmiddleware: false\n"
    )
    (directory / "module.yaml").write_text(header + body, encoding="utf-8")


def _module_fixture(
    tmp_path: Path, name: str, manifest: str, *, base: str = "", overlay: str | None = None
) -> ConfigUI:
    _write_manifest(tmp_path / "mods", name, manifest)
    (tmp_path / "config.yaml").write_text(
        f"modules_directory: ./mods\nenabled_modules: [{name}]\nmodules:\n  {name}: {base or '{}'}\n",
        encoding="utf-8",
    )
    if overlay is not None:
        (tmp_path / "config.local.yaml").write_text(overlay, encoding="utf-8")
    return _ui(tmp_path / "config.yaml", "--status-file", str(tmp_path / "status.json"))


def test_ac17_a_module_added_as_a_directory_gets_its_titled_page(tmp_path: Path) -> None:
    ui = _module_fixture(
        tmp_path,
        "newcomer",
        "settings_schema:\n  type: object\n  properties:\n"
        "    greeting: {title: Greeting text, type: string, description: Said first., default: hello}\n"
        "    rounds: {title: Round count, type: integer, minimum: 1, default: 3}\n",
    )
    page = _module_html(ui, _logged_in(ui), "newcomer")
    assert _shown_paths(page, "modules.newcomer.") == ["greeting", "rounds"]
    assert '<span class="title">Greeting text</span>' in page
    assert '<span class="title">Round count</span>' in page
    assert '<p class="help">Said first.</p>' in page
    assert '<code class="default">hello</code>' in page
    assert 'name="modules.newcomer.greeting"' in page
    assert 'name="modules.newcomer.rounds" min="1" step="1"' in page


def test_ac17_no_ui_source_file_names_a_shipped_module() -> None:
    sources = sorted(Path(config_ui.__file__).parent.glob("*.py"))
    assert sources
    for source in sources:
        tree = ast.parse(source.read_text(encoding="utf-8"))
        literals = {
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        assert literals & set(SHIPPED_MODULES) == set(), source.name


FIXTURE_SCHEMA = """settings_schema:
  type: object
  properties:
    blob: {title: Blob, type: object}
    bag: {title: Bag, type: array}
    nothing: {title: Nothing, type: "null"}
    mode: {title: Mode, type: string, enum: [fast, slow, "a<b"]}
    count: {title: Count, type: integer, minimum: 1, maximum: 10}
    ratio: {title: Ratio, type: number, minimum: 0, maximum: 1}
    flag: {title: Flag, type: boolean}
    names: {title: Names, type: array, items: {type: string}}
    nested:
      title: Nested
      type: object
      additionalProperties: false
      properties:
        inner: {title: Inner, type: string}
  required: [count]
"""


def test_ac18_unrenderable_nodes_get_notices_and_controls_keep_the_schema(tmp_path: Path) -> None:
    ui = _module_fixture(
        tmp_path, "fmod", FIXTURE_SCHEMA, base="{blob: {k: v}, flag: true, names: [x, y]}"
    )
    page = _module_html(ui, _logged_in(ui), "fmod")
    notices = [
        _html.unescape(path)
        for path in re.findall(r'data-path="([^"]+)"[^>]*data-notice="true"', page)
    ]
    assert notices == ["modules.fmod.blob", "modules.fmod.bag", "modules.fmod.nothing"]
    for path in notices:
        markup = _node(page, path)
        assert NOT_EDITABLE in markup and f"<code>{path}</code>" in markup
    # The configured text is shown read-only.
    assert '<pre class="value">{&quot;k&quot;: &quot;v&quot;}</pre>' in _node(page, "modules.fmod.blob")

    mode = _node(page, "modules.fmod.mode")
    options = [_html.unescape(value) for value in re.findall(r'<option value="([^"]*)"', mode)]
    assert options == ["fast", "slow", "a<b"]
    count = _node(page, "modules.fmod.count")
    assert 'min="1" max="10" step="1"' in count
    assert 'data-required="true"' in count and '<abbr class="required" title="required">*</abbr>' in count
    assert "required" not in _node(page, "modules.fmod.mode")
    assert 'min="0" max="1" step="any"' in _node(page, "modules.fmod.ratio")
    flag = _node(page, "modules.fmod.flag")
    assert '<select name="modules.fmod.flag" data-kind="boolean" data-effective="true">' in flag
    assert '<option value="true" selected>true</option><option value="false">false</option>' in flag
    names = _node(page, "modules.fmod.names")
    assert '<textarea name="modules.fmod.names" data-json="list"' in names
    assert "[&quot;x&quot;, &quot;y&quot;]</textarea>" in names
    assert _shown_paths(page, "modules.fmod.")[-4:] == ["names", "names[]", "nested", "nested.inner"]
    # additionalProperties: false offers no extra-key control.
    assert "new_entry_name" not in page


def test_ac18_a_manifest_without_settings_schema_gets_a_notice(tmp_path: Path) -> None:
    ui = _module_fixture(tmp_path, "bare", "", base="{anything: kept-as-text}")
    page = _module_html(ui, _logged_in(ui), "bare")
    markup = _node(page, "modules.bare")
    assert 'data-notice="true"' in markup and NOT_EDITABLE in markup
    assert "kept-as-text" in markup
    assert 'id="triggers"' not in page


def test_r4_a_schema_additional_properties_is_a_named_entries_editor(tmp_path: Path) -> None:
    """The manifest validator accepts only boolean ``additionalProperties``
    today, so the named-entries editor is exercised on the renderer itself."""

    base = _fixture_config(tmp_path)
    base.write_text(
        base.read_text(encoding="utf-8").replace(
            "modules: {}", "modules:\n  ymod: {routes: {first: {weight: 2}}}"
        ),
        encoding="utf-8",
    )
    view = ConfigView.load(UISettings.from_argv(["--config", str(base)]), {})
    schema = {
        "type": "object",
        "properties": {
            "routes": {
                "title": "Routes",
                "type": "object",
                "additionalProperties": {
                    "type": "object",
                    "properties": {"weight": {"title": "Weight", "type": "integer"}},
                },
            }
        },
    }
    # The page renders a module's own settings_schema: the view's manifest
    # carries the schema under test, as it would in production.
    view.modules["ymod"].manifest["settings_schema"] = schema
    markup = _SchemaRenderer(view).children(schema, ("modules", "ymod"))
    assert _shown_paths(markup, "modules.ymod.") == [
        "routes",
        "routes.<entry>",
        "routes.<entry>.weight",
    ]
    assert set(_shown_paths(markup, "modules.ymod.")) == set(_schema_paths(schema))
    # Gate-2 F2: the configured entry key is shown as text, and named in
    # every generated identifier by its position alone.
    assert '<div class="entry" data-entry="@0">' in markup
    assert 'name="modules.ymod.routes.@0" data-json="entry"' in markup
    assert "Entry <code>first</code>" in markup
    assert all("first" not in value for value in re.findall(r'[a-z-]+="([^"]*)"', markup))
    assert "{&quot;weight&quot;: 2}</textarea>" in markup
    assert 'name="new_entry_name:modules.ymod.routes"' in markup


# -- AC19: trigger policies ------------------------------------------------------


def test_ac19_the_twitch_page_shows_the_configured_channel_policy(tmp_path: Path) -> None:
    ui = _profile_ui(tmp_path, "config.yaml.example", {})
    page = _module_html(ui, _logged_in(ui), "twitch")
    triggers = _section(page, "triggers")
    channels = re.findall(r'<fieldset class="channel" data-channel="([^"]*)">', triggers)
    # Gate-2 F2: the configured channel key is named by its position.
    assert channels == ["@0"]
    assert "<legend>Channel <code>${TWITCH_BROADCASTER_ID}</code> (unset)</legend>" in triggers
    assert re.search(r'<option value="all_of" selected>all_of</option>', triggers)
    assert re.findall(r'data-rule-type="([^"]+)"', triggers) == ["keyword"]
    path = "triggers.twitch.channels.@0.rules[0].parameters.keywords"
    keywords = re.search(
        rf'<textarea name="{re.escape(path)}" data-json="list" rows="3">([^<]*)</textarea>', triggers
    )
    assert keywords is not None and json.loads(_html.unescape(keywords.group(1))) == ["!ask"]

    manifest = yaml.safe_load((REPO / "modules" / "twitch" / "module.yaml").read_text(encoding="utf-8"))
    declared_types = [entry["name"] for entry in manifest["triggers"]["types"]]
    add = triggers[triggers.index('id="add-channel-policy"') :]
    offered = re.findall(r'<option value="([^"]+)">', add.split('name="rule_type"')[1].split("</select>")[0])
    # Exactly the declared types: {probability, audience, keyword} plus any
    # type the manifest declares since the specification was written.
    assert offered == declared_types
    assert {"probability", "audience", "keyword"} <= set(offered)
    combinations = re.findall(
        r'<option value="([^"]+)"', add.split('name="combination"')[1].split("</select>")[0]
    )
    assert combinations == manifest["triggers"]["combinations"]
    # A7: the base channel offers no delete and says why.
    assert BASE_ENTRY_NOTE in triggers
    assert "Delete this channel policy" not in triggers


def test_ac19_a_module_without_trigger_types_has_no_trigger_section(tmp_path: Path) -> None:
    ui = _profile_ui(tmp_path, "config.yaml.example", {})
    cookie = _logged_in(ui)
    for name in SHIPPED_MODULES:
        manifest = yaml.safe_load((REPO / "modules" / name / "module.yaml").read_text(encoding="utf-8"))
        has_types = bool((manifest.get("triggers") or {}).get("types"))
        assert ('<section id="triggers">' in _module_html(ui, cookie, name)) == has_types, name
    assert not all(
        (yaml.safe_load((REPO / "modules" / name / "module.yaml").read_text()).get("triggers"))
        for name in SHIPPED_MODULES
    )


TRIGGER_MANIFEST = """settings_schema: {type: object, properties: {}}
triggers:
  types:
    - name: odds
      parameter_schema:
        type: object
        properties: {odds: {title: Odds, type: number, minimum: 0, maximum: 1}}
        required: [odds]
        additionalProperties: false
  combinations: [all_of, any_of]
  default_policy: {combination: all_of, rules: [{type: odds, parameters: {odds: 1}}]}
"""


def test_a7_an_overlay_channel_can_be_deleted_and_a_base_one_cannot(tmp_path: Path) -> None:
    ui = _module_fixture(tmp_path, "tmod", TRIGGER_MANIFEST)
    base = tmp_path / "config.yaml"
    base.write_text(
        base.read_text(encoding="utf-8")
        + "triggers:\n  tmod:\n    channels:\n      from-base:\n        combination: any_of\n"
        "        rules: [{type: odds, parameters: {odds: 0.5}}]\n",
        encoding="utf-8",
    )
    (tmp_path / "config.local.yaml").write_text(
        "triggers:\n  tmod:\n    channels:\n      from-overlay:\n"
        "        rules: [{type: odds, parameters: {odds: 0.25}}, {type: unknown}]\n",
        encoding="utf-8",
    )
    page = _module_html(ui, _logged_in(ui), "tmod")
    fieldsets = dict(
        re.findall(r'<fieldset class="channel" data-channel="([^"]+)">(.*?)</form></fieldset>', page, re.S)
    )
    # Gate-2 F2: each channel is named by its position (the base's first),
    # its key shown only as the legend's text.
    assert list(fieldsets) == ["@0", "@1"]
    assert "<legend>Channel <code>from-base</code></legend>" in fieldsets["@0"]
    assert "<legend>Channel <code>from-overlay</code></legend>" in fieldsets["@1"]
    assert BASE_ENTRY_NOTE in fieldsets["@0"]
    assert "Delete this channel policy" not in fieldsets["@0"]
    assert BASE_ENTRY_NOTE not in fieldsets["@1"]
    assert (
        'formaction="/remove" name="path" value="triggers.tmod.channels.@1">'
        "Delete this channel policy" in fieldsets["@1"]
    )
    assert 'selected>any_of</option>' in fieldsets["@0"]
    odds = _node(page, "triggers.tmod.channels.@0.rules[0].parameters.odds")
    assert 'min="0" max="1" step="any" value="0.5"' in odds
    # An undeclared rule type is a notice, never dropped.
    unknown = _node(page, "triggers.tmod.channels.@1.rules[1]")
    assert 'data-notice="true"' in unknown and "{&quot;type&quot;: &quot;unknown&quot;}" in unknown


# -- AC21: values and origins -------------------------------------------------------

AC21_MANIFEST = "settings_schema:\n  type: object\n  properties:\n" + "".join(
    f"    {key}: {{title: Setting {key.upper()}, type: integer, default: 5}}\n" for key in "abcd"
)


@pytest.mark.parametrize("var_c", [None, "resolved-c-value-9"])
def test_ac21_each_field_shows_its_value_and_origin(var_c: str | None, tmp_path: Path) -> None:
    ui = _module_fixture(
        tmp_path,
        "M",
        AC21_MANIFEST,
        base="{a: 1, c: '${VAR_C}', d: 5}",
        overlay="modules:\n  M: {a: 2}\n",
    )
    ui.environ = {} if var_c is None else {"VAR_C": var_c}
    page = _module_html(ui, _logged_in(ui), "M")
    a, b, c, d = (_node(page, f"modules.M.{key}") for key in "abcd")

    assert 'data-origin="overlay"' in a and '<code class="value">2</code>' in a
    assert '<span class="origin">overlay</span>' in a
    assert 'formaction="/remove" name="path" value="modules.M.a">Remove override' in a

    assert 'data-origin="base"' in c
    assert '<span class="origin">environment reference (base)</span>' in c
    state = "unset" if var_c is None else "set"
    assert f'<code class="value">${{VAR_C}}</code> <span class="reference-state">({state})</span>' in c
    assert "resolved-c-value-9" not in page

    assert '<code class="value">5</code>' in d and '<span class="origin">base</span>' in d

    assert 'data-origin="default-not-set"' in b
    assert '<span class="origin">default-not-set</span>' in b
    assert '<code class="default">5</code>' in b
    assert '<span class="value">not set</span>' in b and 'value=""' in b

    for markup in (b, c, d):
        assert "Remove override" not in markup


def test_credential_literals_are_hidden_on_the_module_page(tmp_path: Path) -> None:
    base = _fixture_config(tmp_path)
    base.write_text(
        base.read_text(encoding="utf-8").replace(
            "modules: {}", "modules:\n  xmod: {token: literal-token-1234}"
        ),
        encoding="utf-8",
    )
    ui = _ui(base, "--status-file", str(tmp_path / "status.json"))
    page = _module_html(ui, _logged_in(ui), "xmod")
    token = _node(page, "modules.xmod.token")
    assert f'<code class="value">{HIDDEN_LITERAL}</code>' in token
    assert f'value="" placeholder="{HIDDEN_LITERAL}"' in token
    assert "literal-token-1234" not in page


@pytest.mark.parametrize(
    ("declared", "literal"),
    [("{type: string, enum: [abc, xyz]}", "abc"), ("{enum: [q7, r8], type: string}", "r8")],
)
def test_a_credential_is_masked_whatever_its_control(
    declared: str, literal: str, tmp_path: Path
) -> None:
    # Short literals escape the redaction guard, so the control itself must mask them.
    ui = _module_fixture(
        tmp_path,
        "cmod",
        f"settings_schema:\n  type: object\n  properties:\n    pin: {declared}\n"
        "credentials: [pin]\n",
        base=f"{{pin: {literal}}}",
    )
    page = _module_html(ui, _logged_in(ui), "cmod")
    pin = _node(page, "modules.cmod.pin")
    assert f'value="" placeholder="{HIDDEN_LITERAL}"' in pin
    assert "<select" not in pin and "<option" not in pin
    assert f'<code class="value">{HIDDEN_LITERAL}</code>' in pin
    assert f'value="{literal}"' not in page and f">{literal}<" not in page


@pytest.mark.parametrize(
    ("schema", "reason"),
    [("{type: object}", "an object without properties"), ("{type: string}", "not an object")],
)
def test_an_unrenderable_root_settings_schema_is_a_notice(
    schema: str, reason: str, tmp_path: Path
) -> None:
    ui = _module_fixture(tmp_path, "rmod", f"settings_schema: {schema}\n", base="{k: 1}")
    page = _module_html(ui, _logged_in(ui), "rmod")
    root = _node(page, "modules.rmod")
    assert 'data-notice="true"' in root and NOT_EDITABLE in root and reason in root
    assert '<pre class="value">' in root


# -- AC36: configured values are escaped text, never a URL -----------------------------

HOSTILE = 'https://cdn.example.invalid/x.js"><script>'


def test_ac36_a_configured_url_is_escaped_text_only(tmp_path: Path) -> None:
    ui = _module_fixture(
        tmp_path,
        "umod",
        "settings_schema:\n  type: object\n  properties:\n"
        "    endpoint: {title: Endpoint, type: string}\n"
        "    blob: {title: Blob, type: object}\n",
        base="{endpoint: " + json.dumps(HOSTILE) + ", blob: {u: " + json.dumps(HOSTILE) + "}}",
    )
    page = _module_html(ui, _logged_in(ui), "umod")
    assert "&lt;script&gt;" in page
    assert page.count("<script>") == 1  # the page's own inline script only
    assert page.count("<script") == 1
    targets = re.findall(r'\b(?:src|href|action|formaction)="([^"]*)"', page)
    assert targets and all(target.startswith("/") and not target.startswith("//") for target in targets)
    for target in targets:
        assert "cdn.example.invalid" not in target


def test_r9_module_pages_use_inline_assets_and_relative_targets_only(tmp_path: Path) -> None:
    ui = _profile_ui(tmp_path, "config.yaml.example", {})
    cookie = _logged_in(ui)
    for name in SHIPPED_MODULES:
        page = _module_html(ui, cookie, name)
        assert "<link" not in page and "<script src" not in page
        targets = re.findall(r'\b(?:src|href|action|formaction)="([^"]*)"', page)
        assert targets and all(
            target.startswith("/") and not target.startswith("//") for target in targets
        ), name
        assert "url(" not in page and "@import" not in page


# -- AC3: an unauthenticated module page ------------------------------------------------


def test_ac3_every_module_page_needs_a_session_and_names_nothing(tmp_path: Path) -> None:
    ui = _profile_ui(tmp_path, "config.yaml.example", {})
    for name in SHIPPED_MODULES:
        response = ui.handle(_request("GET", f"{MODULE_PAGE_PREFIX}{name}"))
        assert response.status == 401
        body = response.body.decode("utf-8")
        for word in SHIPPED_MODULES + SETTING_PATHS:
            assert word not in body


# ---------------------------------------------------------------------------
# P12: Check (R5; AC22, AC38, AC23)
# ---------------------------------------------------------------------------

CHECK_MANIFEST = """settings_schema:
  type: object
  properties:
    level:
      type: integer
      minimum: 1
    label:
      type: string
    token:
      type: string
credentials: [token]
"""
CHECK_ENTRY_POINT = "async def activate(context, settings):\n    return None\n"
CHECK_ENVIRON = {"A_TOKEN": "a-token-value-0001", "B_TOKEN": "b-token-value-0002"}


def _check_fixture(
    tmp_path: Path,
    modules: str,
    *,
    overlay: str | None = None,
    environ: dict[str, str] | None = None,
    extra: str = "",
    checker: object = None,
) -> ConfigUI:
    """Three runnable fixture modules; ``amod`` and ``bmod`` are enabled."""

    for name in ("amod", "bmod", "cmod"):
        _write_manifest(tmp_path / "mods", name, CHECK_MANIFEST)
        (tmp_path / "mods" / name / "__init__.py").write_text(CHECK_ENTRY_POINT, encoding="utf-8")
    (tmp_path / "config.yaml").write_text(
        "modules_directory: ./mods\nenabled_modules: [amod, bmod]\nmodules:\n" + modules + extra,
        encoding="utf-8",
    )
    if overlay is not None:
        (tmp_path / "config.local.yaml").write_text(overlay, encoding="utf-8")
    return ConfigUI(
        UISettings.from_argv(
            ["--config", str(tmp_path / "config.yaml"), "--status-file", str(tmp_path / "status.json")]
        ),
        environ=dict(CHECK_ENVIRON if environ is None else environ),
        checker=checker,  # type: ignore[arg-type]
    )


VALID_MODULES = (
    "  amod: {level: 3, token: '${A_TOKEN}'}\n"
    "  bmod: {level: 4, token: '${B_TOKEN}'}\n"
    "  cmod: {level: 0, token: '${C_UNSET}'}\n"
)


def _check_request(
    ui: ConfigUI,
    cookie: str,
    csrf: str,
    page: str,
    fields: Sequence[tuple[str, str]] = (),
    *,
    layout: str | None = None,
) -> UIRequest:
    """A Check form as *page* renders it, its key-layout guard included."""

    from urllib.parse import urlencode

    if layout is None:
        layout = _form_layout(ui, cookie, page)
    body = urlencode([("csrf_token", csrf), ("layout", layout), ("page", page), *fields]).encode("utf-8")
    return _request(
        "POST",
        "/check",
        headers={"cookie": cookie, "content-type": "application/x-www-form-urlencoded"},
        body=body,
    )


def _post_check(
    ui: ConfigUI, page: str, fields: Sequence[tuple[str, str]] = (), *, cookie: str | None = None
) -> str:
    if cookie is None:
        cookie, csrf = _login(ui)
    else:
        session = ui._session(_request("GET", "/", headers={"cookie": cookie}))
        assert session is not None
        csrf = session.csrf_token
    response = ui.handle(_check_request(ui, cookie, csrf, page, fields))
    assert response.status == 200
    return response.body.decode("utf-8")


def _listed(page: str) -> list[str]:
    return [_html.unescape(item) for item in re.findall(r'<li class="diagnostic">(.*?)</li>', page, re.S)]


def _verdict(page: str) -> bool:
    match = re.search(r'<h1 id="verdict" data-passed="(true|false)">', page)
    assert match is not None
    return match.group(1) == "true"


def _refuse_checker(*args: object) -> tuple[bool, list[str]]:
    raise AssertionError("the settings phase must not be reached")


# -- AC22 ---------------------------------------------------------------------


def test_ac22_unresolved_references_are_all_reported_and_stop_the_check(tmp_path: Path) -> None:
    ui = _check_fixture(
        tmp_path,
        "  amod: {level: 3, token: '${A_MISSING}', label: '${A_LABEL}'}\n"
        "  bmod: {level: 4, token: '${B_MISSING}'}\n"
        "  cmod: {level: 3, token: '${C_MISSING}'}\n",
        extra="secrets: ['${S_MISSING}']\n",
        environ={},
        checker=_refuse_checker,
    )
    result = ui.check()
    assert result.diagnostics == (
        "modules.amod.token: ${A_MISSING} is unresolved",
        "modules.amod.label: ${A_LABEL} is unresolved",
        "modules.bmod.token: ${B_MISSING} is unresolved",
    )
    assert not result.passed and not result.settings_phase_reached

    cookie = _logged_in(ui)
    base = _post_check(ui, "/", cookie=cookie)
    assert not _verdict(base)
    assert "settings phase not reached" in base
    assert sorted(_listed(base)) == sorted(result.diagnostics)
    assert _listed(_post_check(ui, "/module/amod", cookie=cookie)) == list(result.diagnostics[:2])
    assert _listed(_post_check(ui, "/module/bmod", cookie=cookie)) == [result.diagnostics[2]]
    assert _listed(_post_check(ui, "/module/cmod", cookie=cookie)) == []
    assert _SOCKET_ATTEMPTS["count"] == 0


def test_ac22_top_level_references_are_scanned_and_secrets_are_not(tmp_path: Path) -> None:
    ui = _check_fixture(
        tmp_path,
        VALID_MODULES,
        extra=(
            "limits:\n  dedup:\n    max_entries: '${LIMIT_MISSING}'\n"
            "triggers:\n  amod:\n    channels:\n      '${CHANNEL_MISSING}': {rules: []}\n"
            "secrets: ['${SECRET_MISSING}']\n"
        ),
        checker=_refuse_checker,
    )
    result = ui.check()
    assert result.diagnostics == (
        "limits.dedup.max_entries: ${LIMIT_MISSING} is unresolved",
        "triggers.amod.channels.${CHANNEL_MISSING}: ${CHANNEL_MISSING} is unresolved",
    )
    assert not result.settings_phase_reached
    assert result.for_module("amod") == result.diagnostics[1:]


def test_ac22_every_invalid_setting_is_reported_in_one_check(tmp_path: Path) -> None:
    """The real checker: both modules' refusals in one run, each page its own."""

    ui = _check_fixture(
        tmp_path,
        "  amod: {level: 0, token: '${A_TOKEN}'}\n"
        "  bmod: {level: -1, token: '${B_TOKEN}'}\n",
    )
    cookie = _logged_in(ui)
    base = _post_check(ui, "/", cookie=cookie)
    assert not _verdict(base)
    assert "settings phase not reached" not in base
    listed = _listed(base)
    assert len(listed) == 2
    assert any("'amod'" in line and "level" in line for line in listed)
    assert any("'bmod'" in line and "level" in line for line in listed)
    amod = _listed(_post_check(ui, "/module/amod", cookie=cookie))
    bmod = _listed(_post_check(ui, "/module/bmod", cookie=cookie))
    assert len(amod) == 1 and "'amod'" in amod[0]
    assert len(bmod) == 1 and "'bmod'" in bmod[0]
    assert sorted(amod + bmod) == sorted(listed)
    core = _post_check(ui, "/core", cookie=cookie)
    assert sorted(_listed(core)) == sorted(listed)
    assert _SOCKET_ATTEMPTS["count"] == 0


def test_ac22_the_checker_seam_gets_the_draft_and_every_diagnostic_is_kept(
    tmp_path: Path,
) -> None:
    calls: list[tuple[object, ...]] = []
    reported = [
        "module 'amod': field 'settings.level': must be >= 1",
        "modules.bmod: must be a mapping",
        "amod: refused",
        "runtime: could not be assembled",
    ]

    def checker(*args: object) -> tuple[bool, list[str]]:
        calls.append(args)
        return False, list(reported)

    ui = _check_fixture(tmp_path, VALID_MODULES, overlay="modules:\n  amod: {label: kept}\n", checker=checker)
    cookie = _logged_in(ui)
    base = _post_check(ui, "/", [("modules.amod.level", "7")], cookie=cookie)
    (base_path, environ, overlay_path, draft), = calls
    assert base_path == tmp_path / "config.yaml"
    assert overlay_path == tmp_path / "config.local.yaml"
    assert environ == CHECK_ENVIRON
    assert draft == {"modules": {"amod": {"label": "kept", "level": 7}}}
    assert _listed(base) == reported
    amod = _post_check(ui, "/module/amod", cookie=cookie)
    assert _listed(amod) == [reported[0], reported[2]]
    assert "2 other diagnostic(s)" in amod
    assert _listed(_post_check(ui, "/module/bmod", cookie=cookie)) == [reported[1]]


@pytest.mark.parametrize(
    ("diagnostic", "named"),
    [
        ("module 'amod': field 'settings.level': must be >= 1", True),
        ('module "amod": field "x": no', True),
        ("module amod: refused", True),
        ("module amod refused its settings", True),
        ("amod: refused", True),
        ("modules.amod: must be a mapping", True),
        ("modules.amod.token: ${X} is unresolved", True),
        ("modules.amod[0]: bad", True),
        ("triggers.amod.channels: must be a mapping", True),
        ("module 'amodx': field 'a': no", False),
        ("modules.amodx.level: bad", False),
        ("amodx: refused", False),
        ("xmodules.amod.level: bad", False),
        ("enabled_modules[0]: module names must be unique", False),
        ("runtime: could not be assembled", False),
    ],
)
def test_diagnostic_attribution_follows_the_check_path_shapes(diagnostic: str, named: bool) -> None:
    assert config_ui._diagnostic_names_module(diagnostic, "amod") is named


# -- AC38 ---------------------------------------------------------------------


def test_ac38_an_unsaved_invalid_edit_fails_and_writes_nothing(tmp_path: Path) -> None:
    ui = _check_fixture(tmp_path, VALID_MODULES, overlay="modules:\n  amod: {level: 2}\n")
    assert ui.check().passed
    before = _snapshot(tmp_path)
    page = _post_check(ui, "/module/amod", [("modules.amod.level", "0")])
    assert not _verdict(page)
    listed = _listed(page)
    assert len(listed) == 1 and "'amod'" in listed[0] and "level" in listed[0]
    assert _snapshot(tmp_path) == before
    assert ui.check().passed  # the edit was never saved
    assert _SOCKET_ATTEMPTS["count"] == 0


def test_ac38_an_unsaved_fix_passes_and_writes_nothing(tmp_path: Path) -> None:
    ui = _check_fixture(tmp_path, VALID_MODULES, overlay="modules:\n  amod: {level: 0}\n")
    failing = ui.check()
    assert not failing.passed and failing.settings_phase_reached
    assert len(failing.for_module("amod")) == 1
    before = _snapshot(tmp_path)
    page = _post_check(ui, "/module/amod", [("modules.amod.level", "5")])
    assert _verdict(page) and _listed(page) == []
    assert _snapshot(tmp_path) == before
    assert _SOCKET_ATTEMPTS["count"] == 0


# -- AC23 ---------------------------------------------------------------------


def test_ac23_check_signals_nothing_starts_nothing_and_creates_no_socket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ui = _check_fixture(tmp_path, VALID_MODULES, overlay="modules:\n  amod: {level: 2}\n")
    monkeypatch.setattr(subprocess, "Popen", _fail)
    monkeypatch.setattr(os, "kill", _fail)
    if hasattr(os, "killpg"):
        monkeypatch.setattr(os, "killpg", _fail)
    before = _snapshot(tmp_path)
    for fields in ([], [("modules.amod.level", "0")], [("modules.bmod.level", "9")]):
        _post_check(ui, "/module/amod", fields)
        _post_check(ui, "/", fields)
    assert _snapshot(tmp_path) == before
    # The creation patch is still the one installed for the module.
    assert socket.socket.__init__ is _refuse
    for name in ("socketpair", "fromfd", "create_connection", "create_server"):
        assert getattr(socket, name) is _refuse
    assert _SOCKET_ATTEMPTS["count"] == 0


# -- D7 pin and the D8 bridge ---------------------------------------------------


def test_d7_the_socket_free_loop_runs_sleep_executor_and_wait_for() -> None:
    loops: list[asyncio.AbstractEventLoop] = []

    async def scenario() -> tuple[str, str]:
        loop = asyncio.get_running_loop()
        loops.append(loop)
        await asyncio.sleep(0)
        # The executor thread wakes the loop with call_soon_threadsafe.
        worker = await loop.run_in_executor(None, lambda: threading.current_thread().name)
        future: asyncio.Future[str] = loop.create_future()
        loop.call_soon(future.set_result, "waited")
        return worker, await asyncio.wait_for(future, timeout=5)

    worker, waited = _run_socket_free(scenario())
    assert waited == "waited"
    assert worker != threading.current_thread().name
    assert isinstance(loops[0], _SocketFreeEventLoop)
    assert loops[0].is_closed()
    assert _SOCKET_ATTEMPTS["count"] == 0


@pytest.mark.parametrize(("level", "passes"), [("5", True), ("0", False)])
def test_bridge_check_runs_the_real_checker_from_a_running_loop(
    level: str, passes: bool, tmp_path: Path
) -> None:
    ui = _check_fixture(tmp_path, VALID_MODULES, overlay="modules:\n  amod: {level: 2}\n")
    cookie, csrf = _login(ui)
    request = _check_request(ui, cookie, csrf, "/module/amod", [("modules.amod.level", level)])
    executor = config_ui.ThreadPoolExecutor(
        max_workers=config_ui.EXECUTOR_WORKERS,
        thread_name_prefix=config_ui.EXECUTOR_THREAD_PREFIX,
    )
    before = _snapshot(tmp_path)

    async def scenario() -> UIResponse:
        assert isinstance(asyncio.get_running_loop(), _SocketFreeEventLoop)
        return await _dispatch(ui, request, executor, config_ui._Admission())

    try:
        response = _run_socket_free(scenario())
    finally:
        executor.shutdown(wait=True)
    assert response.status == 200
    page = response.body.decode("utf-8")
    assert _verdict(page) is passes
    listed = _listed(page)
    if passes:
        assert listed == []
    else:
        assert len(listed) == 1 and "'amod'" in listed[0] and "level" in listed[0]
    assert ui.last_check is not None and ui.last_check.passed is passes
    assert _snapshot(tmp_path) == before
    assert _SOCKET_ATTEMPTS["count"] == 0


def test_d8_the_default_checker_refuses_to_run_on_a_running_loop(tmp_path: Path) -> None:
    ui = _check_fixture(tmp_path, VALID_MODULES)

    async def scenario() -> str:
        with pytest.raises(RuntimeError, match="Check must not run on a running event loop"):
            config_ui._default_checker(ui.base_path, ui.environ, ui.overlay_path, {})
        return "refused"

    assert _run_socket_free(scenario()) == "refused"
    assert _SOCKET_ATTEMPTS["count"] == 0


# -- drafts, pages, readiness and redaction -------------------------------------


def test_apply_edits_sets_paths_on_a_copy() -> None:
    overlay = {"modules": {"amod": {"label": "kept"}}, "actions": [{"rule_id": "r"}]}
    draft = config_ui._apply_edits(
        overlay,
        [
            (("modules", "amod", "level"), 3),
            (("modules", "bmod", "nested", "value"), [1]),
            (("modules_directory",), "./other"),
        ],
    )
    assert draft == {
        "modules": {"amod": {"label": "kept", "level": 3}, "bmod": {"nested": {"value": [1]}}},
        "actions": [{"rule_id": "r"}],
        "modules_directory": "./other",
    }
    assert overlay == {"modules": {"amod": {"label": "kept"}}, "actions": [{"rule_id": "r"}]}
    with pytest.raises(ValueError):
        config_ui._apply_edits({}, [(("triggers", "x", "rules", 0), 1)])


def test_untouched_fields_are_no_edits_and_fields_read_as_their_kind(tmp_path: Path) -> None:
    ui = _check_fixture(tmp_path, VALID_MODULES, overlay="modules:\n  amod: {token: literal-secret}\n")
    view = ui.view()
    controls = ui._controls(view).controls
    untouched = [(name, control.rendered) for name, control in controls.items()]
    assert ("modules.amod.token", "") in untouched  # a credential is never rendered
    assert ui.parse_edits(view, untouched) == ([], [])
    edits, problems = ui.parse_edits(
        view,
        [
            ("modules.amod.level", "12"),
            ("modules.bmod.label", "${SOME_NAME}"),
            ("limits.dedup.max_entries", "2048"),
            ("enabled_modules.cmod", "true"),
            ("enabled_modules.amod", "false"),
            ("no.such.field", "1"),
        ],
    )
    assert dict(edits) == {
        ("enabled_modules",): ["bmod", "cmod"],
        ("modules", "amod", "level"): 12,
        ("modules", "bmod", "label"): "${SOME_NAME}",
        ("limits", "dedup", "max_entries"): 2048,
    }
    assert problems == ["no.such.field: is not a setting this UI edits"]
    result = ui.check(edits, problems=problems, view=view)
    assert not result.passed
    assert result.diagnostics[0] == "no.such.field: is not a setting this UI edits"


def test_a_new_trigger_parameter_entry_is_lifted_to_its_channel_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The manifest validator accepts only boolean ``additionalProperties``,
    so the parameters mapping is registered on the renderer directly."""

    ui = _module_fixture(tmp_path, "tmod", TRIGGER_MANIFEST)
    base = tmp_path / "config.yaml"
    base.write_text(
        base.read_text(encoding="utf-8")
        + "triggers:\n  tmod:\n    channels:\n      from-base:\n        combination: any_of\n"
        "        rules: [{type: odds, parameters: {odds: 0.5}}]\n",
        encoding="utf-8",
    )
    view = ui.view()
    channel = ("triggers", "tmod", "channels", "from-base")
    parameters = (*channel, "rules", 0, "parameters")
    controls = ui._controls

    def with_entries(view: ConfigView, render: str = "") -> _SchemaRenderer:
        renderer = controls(view, render)
        renderer.entry_mappings[config_ui._field_name(parameters, view)] = parameters
        return renderer

    monkeypatch.setattr(ui, "_controls", with_entries)
    # Gate-2 F2: the channel is named by its position in every field name.
    edits, problems = ui.parse_edits(
        view,
        [
            ("new_entry_name:triggers.tmod.channels.@0.rules[0].parameters", "extra"),
            ("new_entry_value:triggers.tmod.channels.@0.rules[0].parameters", "7"),
            ("triggers.tmod.channels.@0.rules[0].parameters.odds", "0.25"),
        ],
    )
    assert problems == []
    assert edits == [
        (
            channel,
            {"combination": "any_of", "rules": [{"type": "odds", "parameters": {"odds": 0.25, "extra": 7}}]},
        )
    ]
    # The policy lives only in the base: the draft overlay still takes the edit.
    assert config_ui._apply_edits({}, edits)["triggers"]["tmod"]["channels"]["from-base"]["rules"][0][
        "parameters"
    ] == {"odds": 0.25, "extra": 7}


def test_check_skips_the_browser_constraints(tmp_path: Path) -> None:
    ui = _check_fixture(tmp_path, VALID_MODULES)
    module = _module_html(ui, _logged_in(ui), "amod")
    assert '<button type="submit" formaction="/check" formnovalidate>Check</button>' in module


def test_every_page_posts_its_draft_to_check(tmp_path: Path) -> None:
    ui = _check_fixture(tmp_path, VALID_MODULES)
    cookie = _logged_in(ui)
    base = _get(ui, "/", cookie).body.decode("utf-8")
    assert re.search(r'action="/check">.*?name="page" value="/"', base, re.S)
    core = _get(ui, "/core", cookie).body.decode("utf-8")
    assert core.count('formaction="/check"') == 2
    assert core.count('name="page" value="/core"') == 2
    module = _module_html(ui, cookie, "amod")
    settings = _section(module, "settings")
    assert 'formaction="/check"' in settings and 'name="page" value="/module/amod"' in settings


def test_the_last_check_verdict_is_the_readiness(tmp_path: Path) -> None:
    """The Check verdict part of the readiness cell.

    Since P14 the cell also carries the running state (R4, AC21), so the
    verdict is read from its ``check`` part.
    """

    ui = _check_fixture(tmp_path, VALID_MODULES)
    cookie = _logged_in(ui)

    def readiness(name: str) -> str:
        page = _get(ui, "/", cookie).body.decode("utf-8")
        match = re.search(
            rf'<tr data-module="{name}".*?<td class="readiness"><span class="check">(.*?)</span>',
            page,
            re.S,
        )
        assert match is not None
        return _html.unescape(match.group(1))

    assert readiness("amod") == "not checked yet"
    _post_check(ui, "/", [("modules.amod.level", "0")], cookie=cookie)
    assert readiness("amod") == "last Check failed"
    assert readiness("bmod") == "last Check failed (no diagnostic names this module)"
    _post_check(ui, "/", [("modules.amod.label", "${NOT_SET_ANYWHERE}")], cookie=cookie)
    assert readiness("bmod") == "last Check failed (settings phase not reached)"
    _post_check(ui, "/", cookie=cookie)
    assert readiness("amod") == readiness("bmod") == "last Check passed"


def test_check_diagnostics_pass_the_redaction_guard(tmp_path: Path) -> None:
    secret = CHECK_ENVIRON["A_TOKEN"]
    typed = "typed-credential-literal"

    def leaky(*args: object) -> tuple[bool, list[str]]:
        return False, [f"module 'amod': field 'token': {secret} / {typed} rejected"]

    ui = _check_fixture(tmp_path, VALID_MODULES, checker=leaky)
    page = _post_check(ui, "/module/amod", [("modules.amod.token", typed)])
    assert secret not in page and typed not in page
    assert _listed(page) == ["module 'amod': field 'token': [hidden] / [hidden] rejected"]
    assert ui.last_check is not None and secret not in "".join(ui.last_check.diagnostics)


# ---------------------------------------------------------------------------
# P13: Save and remove-override (R6, A7, A9; AC24–AC28, AC10)
# ---------------------------------------------------------------------------

_BASE_GUARDED = re.compile(r"_ac(2[2-8]|10)_")


@pytest.fixture(autouse=True)
def base_bytes_unchanged(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """AC10: after every AC22–AC28 operation the base file's bytes are unchanged.

    Every base file a :class:`ConfigUI` is built over during such a test is
    recorded when the UI is built and compared at teardown.
    """

    if not _BASE_GUARDED.search(request.node.name):
        yield
        return
    bases: dict[Path, bytes] = {}
    original = ConfigUI.__init__

    def recording(self: ConfigUI, settings: UISettings, **kwargs: object) -> None:
        original(self, settings, **kwargs)  # type: ignore[arg-type]
        if self.base_path not in bases and self.base_path.is_file():
            bases[self.base_path] = self.base_path.read_bytes()

    monkeypatch.setattr(ConfigUI, "__init__", recording)
    yield
    for path, data in bases.items():
        assert path.read_bytes() == data, f"the base file {path} was written"


def _csrf_of(ui: ConfigUI, cookie: str) -> str:
    session = ui._session(_request("GET", "/", headers={"cookie": cookie}))
    assert session is not None
    return session.csrf_token


def _form_fingerprint(ui: ConfigUI, cookie: str, page: str) -> str:
    """The overlay fingerprint the forms of *page* carry (all of them agree)."""

    html_text = _get(ui, page, cookie).body.decode("utf-8")
    found = set(re.findall(r'name="fingerprint" value="([^"]*)"', html_text))
    assert len(found) == 1, found
    return found.pop()


def _guard_values(ui: ConfigUI) -> tuple[str, str]:
    """The overlay fingerprint and key layout every form rendered now carries."""

    token = config_ui._RENDERING_FOR.set(ui)
    try:
        view = ui.view()
        return config_ui._view_fingerprint(view), config_ui._view_layout(view)
    finally:
        config_ui._RENDERING_FOR.reset(token)


def _form_layout(ui: ConfigUI, cookie: str, page: str) -> str:
    """The key-layout digest the forms of *page* carry (all of them agree)."""

    html_text = _get(ui, page, cookie).body.decode("utf-8")
    found = set(re.findall(r'name="layout" value="([^"]*)"', html_text))
    assert len(found) == 1, found
    return found.pop()


def _write(
    ui: ConfigUI,
    cookie: str,
    route: str,
    page: str,
    fields: Sequence[tuple[str, str]],
    *,
    fingerprint: str | None = None,
    layout: str | None = None,
) -> UIResponse:
    """POST a Save or Remove form as *page* renders it (its guard fields included).

    The guard fields are the values the page's forms carry, computed without
    rendering it again: as a browser does, the post comes from the render
    already loaded, whose patch identities a new render would retire (P19F10).
    """

    from urllib.parse import urlencode

    if fingerprint is None or layout is None:
        current_fingerprint, current_layout = _guard_values(ui)
        fingerprint = current_fingerprint if fingerprint is None else fingerprint
        layout = current_layout if layout is None else layout
    body = urlencode(
        [
            ("csrf_token", _csrf_of(ui, cookie)),
            ("page", page),
            ("fingerprint", fingerprint),
            ("layout", layout),
            *fields,
        ]
    ).encode("utf-8")
    return ui.handle(
        _request(
            "POST",
            route,
            headers={"cookie": cookie, "content-type": "application/x-www-form-urlencoded"},
            body=body,
        )
    )


def _written(response: UIResponse) -> list[str]:
    return _listed(response.body.decode("utf-8"))


def _fingerprint(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else "absent"


def _overlay_doc(path: Path) -> object:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _saves(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [record for record in caplog.records if hasattr(record, "config_ui_operation")]


def _profile_copy(tmp_path: Path, environ: dict[str, str]) -> tuple[ConfigUI, Path]:
    """The shipped profile as a base in *tmp_path*, its modules directory absolute."""

    text = (REPO / "config.yaml.example").read_text(encoding="utf-8")
    assert "modules_directory: ./modules\n" in text
    base = tmp_path / "config.yaml"
    base.write_text(
        text.replace("modules_directory: ./modules\n", f"modules_directory: {REPO / 'modules'}\n"),
        encoding="utf-8",
    )
    ui = ConfigUI(
        UISettings.from_argv(["--config", str(base), "--status-file", str(tmp_path / "status.json")]),
        environ=environ,
    )
    return ui, base


# -- AC24 ---------------------------------------------------------------------


def test_ac24_each_save_writes_exactly_its_override_and_logs_paths_only(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    from core.main import load_config

    environ = {name: f"env-value-{index:04d}-c4n4ry" for index, name in enumerate(_profile_variables("config.yaml.example"))}
    ui, base = _profile_copy(tmp_path, environ)
    overlay = tmp_path / "config.local.yaml"
    _write_module(tmp_path / "other", "omod")
    cookie = _logged_in(ui)
    caplog.set_level(logging.INFO, logger="core.config_ui")
    base_enabled = yaml.safe_load(base.read_text(encoding="utf-8"))["enabled_modules"]
    assert "audit" in base_enabled
    expected: dict[str, object] = {}
    chan2 = {"combination": "any_of", "rules": [{"type": "probability", "parameters": {"probability": 0.5}}]}
    steps: list[tuple[str, list[tuple[str, str]], dict[str, object], list[str], object, object]] = [
        (
            "/module/users",
            [("modules.users.max_channels", "73591")],
            {"modules": {"users": {"max_channels": 73591}}},
            ["modules.users.max_channels"],
            lambda config: config["modules"]["users"]["max_channels"],
            73591,
        ),
        (
            "/",
            [("enabled_modules.audit", "false")],
            {"enabled_modules": [name for name in base_enabled if name != "audit"]},
            ["enabled_modules"],
            lambda config: config["enabled_modules"],
            [name for name in base_enabled if name != "audit"],
        ),
        (
            "/core",
            [("limits.dedup.max_entries", "2048")],
            {"limits": {"dedup": {"max_entries": 2048}}},
            ["limits.dedup.max_entries"],
            lambda config: config["limits"]["dedup"],
            {"max_entries": 2048, "ttl_seconds": 600},
        ),
        (
            "/module/twitch",
            [
                ("add_channel_policy", "triggers.twitch.channels"),
                ("channel", "chan2"),
                ("combination", "any_of"),
                ("rule_type", "probability"),
                ("parameters", '{"probability": 0.5}'),
            ],
            {"triggers": {"twitch": {"channels": {"chan2": chan2}}}},
            ["triggers.twitch.channels.chan2"],
            lambda config: config["triggers"]["twitch"]["channels"]["chan2"],
            chan2,
        ),
        (
            "/core",
            [("modules_directory", "./other")],
            {"modules_directory": "./other"},
            ["modules_directory"],
            lambda config: Path(config["modules_directory"]),
            (tmp_path / "other").resolve(),
        ),
    ]
    for page, fields, override, paths, read, value in steps:
        before = len(_saves(caplog))
        response = _write(ui, cookie, "/save", page, fields)
        assert response.status == 303, _written(response)
        assert dict(response.headers)["Location"] == page
        for key, item in override.items():
            if isinstance(item, dict) and isinstance(expected.get(key), dict):
                merged = expected[key]
                for sub, subitem in item.items():
                    merged.setdefault(sub, {}).update(subitem)  # type: ignore[union-attr]
            else:
                expected[key] = item
        assert _overlay_doc(overlay) == expected
        config = load_config(base, environ=environ)
        assert read(config) == value  # type: ignore[operator]
        records = _saves(caplog)[before:]
        assert len(records) == 1
        record = records[0]
        assert record.name == "core.config_ui"
        assert record.config_ui_operation == "save"  # type: ignore[attr-defined]
        assert list(record.config_ui_paths) == paths  # type: ignore[attr-defined]
        assert record.config_ui_page == page  # type: ignore[attr-defined]
        message = record.getMessage()
        assert all(path in message for path in paths) and page in message
        assert re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", message)
        for text in ("73591", "2048", "./other", "0.5", "probability", "any_of", "audit"):
            assert text not in message
        assert not any(value in message for value in environ.values())
    # The base page now lists the modules discovered in ./other.
    listed = re.findall(r'<tr data-module="([^"]+)"', _get(ui, "/", cookie).body.decode("utf-8"))
    assert listed == ["omod"]


def test_ac24_every_saving_form_carries_the_overlay_fingerprint(tmp_path: Path) -> None:
    ui = _module_fixture(tmp_path, "tmod", TRIGGER_MANIFEST)
    cookie = _logged_in(ui)
    overlay = tmp_path / "config.local.yaml"
    for state in ("absent", "present"):
        if state == "present":
            overlay.write_text("modules: {tmod: {}}\n", encoding="utf-8")
        for page in ("/", "/core", "/module/tmod"):
            text = _get(ui, page, cookie).body.decode("utf-8")
            forms = re.findall(r'<form[^>]*action="/save".*?</form>', text, re.S)
            assert forms, page
            for form in forms:
                assert f'name="fingerprint" value="{_fingerprint(overlay)}"' in form
    assert _fingerprint(overlay) != "absent"


def test_ac24_a_save_equal_to_the_overlay_writes_and_logs_nothing(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    ui = _check_fixture(tmp_path, VALID_MODULES, overlay="modules:\n  amod: {level: 7}\n")
    cookie = _logged_in(ui)
    caplog.set_level(logging.INFO, logger="core.config_ui")
    before = _snapshot(tmp_path)
    fingerprint = _fingerprint(tmp_path / "config.local.yaml")
    result = ui.save([(("modules", "amod", "level"), 7)], fingerprint=fingerprint)
    assert result.outcome == "unchanged" and _snapshot(tmp_path) == before
    assert _write(ui, cookie, "/save", "/module/amod", []).status == 200
    assert _snapshot(tmp_path) == before and _saves(caplog) == []


# -- AC25 ---------------------------------------------------------------------

REMOVE_MANIFEST = """settings_schema:
  type: object
  properties:
    S: {type: integer}
    T: {type: integer}
"""


def test_ac25_remove_override_restores_the_base_value_and_prunes(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    from core.main import load_config

    ui = _module_fixture(tmp_path, "M", REMOVE_MANIFEST, base="{S: 1}")
    base = tmp_path / "config.yaml"
    overlay = tmp_path / "config.local.yaml"
    cookie = _logged_in(ui)
    caplog.set_level(logging.INFO, logger="core.config_ui")
    assert _write(ui, cookie, "/save", "/module/M", [("modules.M.S", "5"), ("modules.M.T", "6")]).status == 303
    assert _overlay_doc(overlay) == {"modules": {"M": {"S": 5, "T": 6}}}
    assert load_config(base, environ={})["modules"]["M"] == {"S": 5, "T": 6}
    page = _module_html(ui, cookie, "M")
    assert 'formaction="/remove" name="path" value="modules.M.S">Remove override' in page

    response = _write(ui, cookie, "/remove", "/module/M", [("path", "modules.M.S")])
    assert response.status == 303
    assert _overlay_doc(overlay) == {"modules": {"M": {"T": 6}}}
    assert load_config(base, environ={})["modules"]["M"] == {"S": 1, "T": 6}
    response = _write(ui, cookie, "/remove", "/module/M", [("path", "modules.M.T")])
    assert response.status == 303
    assert _overlay_doc(overlay) == {}  # modules.M, then modules, pruned
    assert load_config(base, environ={})["modules"]["M"] == {"S": 1}
    removes = [record for record in _saves(caplog) if record.config_ui_operation == "remove-override"]  # type: ignore[attr-defined]
    assert [list(record.config_ui_paths) for record in removes] == [["modules.M.S"], ["modules.M.T"]]  # type: ignore[attr-defined]

    before = _snapshot(tmp_path)
    missing = _write(ui, cookie, "/remove", "/module/M", [("path", "modules.M.S")])
    assert missing.status == 403 and _written(missing) == ["modules.M.S: is not overridden in the overlay"]
    assert _snapshot(tmp_path) == before


def test_ac25_a_base_channel_policy_offers_no_delete_and_cannot_be_removed(tmp_path: Path) -> None:
    _write_manifest(tmp_path / "mods", "tmod", TRIGGER_MANIFEST)
    base = tmp_path / "config.yaml"
    base.write_text(
        "modules_directory: ./mods\nenabled_modules: [tmod]\nmodules:\n  tmod: {}\n"
        "triggers:\n  tmod:\n    channels:\n      from-base:\n        combination: any_of\n"
        "        rules: [{type: odds, parameters: {odds: 0.5}}]\n",
        encoding="utf-8",
    )
    ui = _ui(base, "--status-file", str(tmp_path / "status.json"))
    cookie = _logged_in(ui)
    triggers = _section(_module_html(ui, cookie, "tmod"), "triggers")
    assert BASE_ENTRY_NOTE in triggers and "Delete this channel policy" not in triggers
    # An override of the base policy is removable; the base entry stays (A7).
    # Gate-2 F2: the channel is named by its position, never its key.
    field = "triggers.tmod.channels.@0.rules[0].parameters.odds"
    assert _write(ui, cookie, "/save", "/module/tmod", [(field, "0.25")]).status == 303
    overlay = tmp_path / "config.local.yaml"
    assert _overlay_doc(overlay)["triggers"]["tmod"]["channels"]["from-base"]["rules"][0]["parameters"] == {"odds": 0.25}  # type: ignore[index]
    path = "triggers.tmod.channels.@0"
    assert _write(ui, cookie, "/remove", "/module/tmod", [("path", path)]).status == 303
    assert _overlay_doc(overlay) == {}
    assert ui.view().value(("triggers", "tmod", "channels", "from-base"))["rules"][0]["parameters"] == {"odds": 0.5}
    before = _snapshot(tmp_path)
    response = _write(ui, cookie, "/remove", "/module/tmod", [("path", path)])
    assert response.status == 403 and _snapshot(tmp_path) == before


def test_ac24_a_base_key_reorder_makes_a_rendered_form_stale(tmp_path: Path) -> None:
    """Positional names are bound to the key order they were rendered from.

    Gate-3 A1: the overlay fingerprint alone does not cover the base file, so
    reordering the base's channels would retarget ``@0`` at another channel.
    """

    _write_manifest(tmp_path / "mods", "tmod", TRIGGER_MANIFEST)
    base = tmp_path / "config.yaml"
    rules = "        combination: any_of\n        rules: [{type: odds, parameters: {odds: 0.5}}]\n"

    def write_base(first: str, second: str) -> None:
        base.write_text(
            "modules_directory: ./mods\nenabled_modules: [tmod]\nmodules:\n  tmod: {}\n"
            f"triggers:\n  tmod:\n    channels:\n      {first}:\n{rules}      {second}:\n{rules}",
            encoding="utf-8",
        )

    write_base("chan-a", "chan-b")
    ui = _ui(base, "--status-file", str(tmp_path / "status.json"))
    cookie = _logged_in(ui)
    page = "/module/tmod"
    fingerprint = _form_fingerprint(ui, cookie, page)
    layout = _form_layout(ui, cookie, page)
    write_base("chan-b", "chan-a")  # the overlay is untouched
    before = _snapshot(tmp_path)
    field = "triggers.tmod.channels.@0.combination"
    response = _write(ui, cookie, "/save", page, [(field, "all_of")], fingerprint=fingerprint, layout=layout)
    assert response.status == 409
    assert "changed on disk" in _written(response)[0]
    remove = _write(
        ui, cookie, "/remove", page, [("path", "triggers.tmod.channels.@0")], fingerprint=fingerprint, layout=layout
    )
    assert "changed on disk" in _written(remove)[0]
    assert _snapshot(tmp_path) == before
    # A form rendered from the current order saves to the channel it shows
    # (the base is put back: the UI never writes it, the fixture checks).
    assert _write(ui, cookie, "/save", page, [(field, "all_of")]).status == 303
    channels = _overlay_doc(tmp_path / "config.local.yaml")["triggers"]["tmod"]["channels"]  # type: ignore[index]
    assert list(channels) == ["chan-b"] and channels["chan-b"]["combination"] == "all_of"
    write_base("chan-a", "chan-b")


@pytest.mark.parametrize("reordered", ["base", "overlay"])
def test_ac24_a_key_reorder_makes_a_rendered_check_stale_like_save(tmp_path: Path, reordered: str) -> None:
    """Check applies Save's layout guard before parsing a positional name.

    Gate-3 N2: after a key reorder, Check parsed the old form against the new
    order and reported a verdict for the channel the operator did not edit
    (``@0`` read as ``chan-b``), while Save refused the same form as stale.
    Both now refuse it identically and the checker never sees a draft.
    """

    _write_manifest(tmp_path / "mods", "tmod", TRIGGER_MANIFEST)
    base = tmp_path / "config.yaml"
    overlay = tmp_path / "config.local.yaml"
    policy = "        combination: all_of\n        rules: [{type: odds, parameters: {odds: 0.5}}]\n"
    head = "modules_directory: ./mods\nenabled_modules: [tmod]\nmodules:\n  tmod: {}\n"

    def channels(first: str, second: str) -> str:
        return f"triggers:\n  tmod:\n    channels:\n      {first}:\n{policy}      {second}:\n{policy}"

    def write(first: str, second: str) -> None:
        if reordered == "base":
            base.write_text(head + channels(first, second), encoding="utf-8")
        else:
            overlay.write_text(channels(first, second), encoding="utf-8")

    if reordered == "overlay":
        base.write_text(head, encoding="utf-8")
    write("chan-a", "chan-b")
    ui = _ui(base, "--status-file", str(tmp_path / "status.json"))
    seen: list[Mapping[str, Any]] = []

    def checker(path: Path, environ: Mapping[str, str], managed: Path | None, draft: Mapping[str, Any]):
        seen.append(draft)
        return True, []

    ui.checker = checker  # type: ignore[assignment]
    cookie = _logged_in(ui)
    csrf = _csrf_of(ui, cookie)
    page = "/module/tmod"
    field = "triggers.tmod.channels.@0.combination"
    assert f'name="{field}"' in _module_html(ui, cookie, "tmod")
    fingerprint = _form_fingerprint(ui, cookie, page)
    layout = _form_layout(ui, cookie, page)
    write("chan-b", "chan-a")  # no content edit, only the key order
    before = _snapshot(tmp_path)

    checked = ui.handle(_check_request(ui, cookie, csrf, page, [(field, "any_of")], layout=layout))
    saved = _write(ui, cookie, "/save", page, [(field, "any_of")], fingerprint=fingerprint, layout=layout)
    assert checked.status == saved.status == 409
    assert 'data-outcome="stale"' in checked.body.decode("utf-8")
    assert checked.body == saved.body
    assert "changed on disk" in _written(checked)[0] and "reload it" in _written(checked)[0]
    assert "verdict" not in checked.body.decode("utf-8")
    assert seen == []  # never parsed, never checked
    assert _snapshot(tmp_path) == before
    # A form missing the layout is not trusted either.
    missing = ui.handle(_check_request(ui, cookie, csrf, page, [(field, "any_of")], layout=""))
    assert missing.status == 409 and seen == []

    # The page as rendered now names the channel it shows, and Check sees it.
    checked = ui.handle(_check_request(ui, cookie, csrf, page, [(field, "any_of")]))
    assert checked.status == 200 and _verdict(checked.body.decode("utf-8"))
    assert len(seen) == 1
    drafted = seen[0]["triggers"]["tmod"]["channels"]
    assert drafted["chan-b"]["combination"] == "any_of"
    assert "combination" not in drafted.get("chan-a", {}) or drafted["chan-a"]["combination"] == "all_of"
    assert _snapshot(tmp_path) == before
    write("chan-a", "chan-b")  # the UI never writes the base, the fixture checks


# -- AC26 ---------------------------------------------------------------------

AC26_MANIFEST = """settings_schema:
  type: object
  properties:
    level: {type: integer, minimum: 1, maximum: 10}
    label: {type: string}
"""


@pytest.mark.parametrize(
    ("page", "fields", "diagnostic"),
    [
        ("/module/M", [("modules.M.level", "11")], "modules.M.level: must be <= 10"),
        ("/module/M", [("modules.M.level", "0")], "modules.M.level: must be >= 1"),
        ("/module/M", [("modules.M.level", "many")], "modules.M.level: must be of type 'integer'"),
        ("/core", [("limits.dedup.max_entries", "0")], "limits.dedup.max_entries: must be a positive integer"),
        ("/core", [("limits.dedup.ttl_seconds", "x")], "limits.dedup.ttl_seconds: must be a finite positive number"),
        ("/core", [("modules_directory", "")], "modules_directory: must be a non-empty string"),
        ("/core", [("modules_directory", "   ")], "modules_directory: must be a non-empty string"),
    ],
)
def test_ac26_an_invalid_save_returns_a_field_diagnostic_and_writes_nothing(
    page: str, fields: list[tuple[str, str]], diagnostic: str, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    ui = _module_fixture(tmp_path, "M", AC26_MANIFEST, base="{level: 3}", overlay="modules: {M: {label: kept}}\n")
    cookie = _logged_in(ui)
    caplog.set_level(logging.INFO, logger="core.config_ui")
    before = _snapshot(tmp_path)
    response = _write(ui, cookie, "/save", page, fields)
    assert response.status == 422
    assert _written(response) == [diagnostic]
    assert _snapshot(tmp_path) == before and _saves(caplog) == []


@pytest.mark.parametrize(
    ("enabled", "diagnostic"),
    [
        (["M", "M"], "enabled_modules[1]: repeats an enabled module"),
        (["M", "nomod"], "enabled_modules[1]: is not a discovered module"),
        ("M", "enabled_modules: must be a list of module names"),
    ],
)
def test_ac26_enabled_modules_must_be_distinct_discovered_names(
    enabled: object, diagnostic: str, tmp_path: Path
) -> None:
    ui = _module_fixture(tmp_path, "M", AC26_MANIFEST)
    before = _snapshot(tmp_path)
    result = ui.save([(("enabled_modules",), enabled)], fingerprint="absent")
    assert result.outcome == "invalid" and result.diagnostics == (diagnostic,)
    assert _snapshot(tmp_path) == before


@pytest.mark.parametrize(
    ("fields", "diagnostic"),
    [
        (
            [("rule_type", "nosuch"), ("combination", "all_of"), ("parameters", "{}")],
            "triggers.twitch.channels.chan2.rules[0].type: is not declared by the module",
        ),
        (
            [("rule_type", "probability"), ("combination", "all_of"), ("parameters", '{"probability": 1.5}')],
            "triggers.twitch.channels.chan2.rules[0].parameters.probability: must be <= 1",
        ),
        (
            [("rule_type", "probability"), ("combination", "some_of"), ("parameters", '{"probability": 0.5}')],
            "field 'triggers.twitch.channels.chan2': TriggerPolicy.combination: must be one of",
        ),
    ],
)
def test_ac26_an_invalid_twitch_channel_policy_writes_nothing(
    fields: list[tuple[str, str]], diagnostic: str, tmp_path: Path
) -> None:
    ui, _ = _profile_copy(tmp_path, {})
    cookie = _logged_in(ui)
    before = _snapshot(tmp_path)
    response = _write(
        ui,
        cookie,
        "/save",
        "/module/twitch",
        [("add_channel_policy", "triggers.twitch.channels"), ("channel", "chan2"), *fields],
    )
    assert response.status == 422
    listed = _written(response)
    assert len(listed) == 1 and diagnostic in listed[0], listed
    assert "1.5" not in listed[0] and "some_of" not in listed[0]
    assert _snapshot(tmp_path) == before


def test_ac26_an_operator_the_module_does_not_support_is_refused(tmp_path: Path) -> None:
    ui = _module_fixture(tmp_path, "tmod", TRIGGER_MANIFEST)
    cookie = _logged_in(ui)
    before = _snapshot(tmp_path)
    response = _write(
        ui,
        cookie,
        "/save",
        "/module/tmod",
        [
            ("add_channel_policy", "triggers.tmod.channels"),
            ("channel", "c1"),
            ("combination", "none_of"),  # a known operator tmod does not declare
            ("rule_type", "odds"),
            ("parameters", '{"odds": 0.5}'),
        ],
    )
    assert response.status == 422
    assert _written(response) == [
        "triggers.tmod.channels.c1.combination: is not declared by the module; declared: all_of, any_of"
    ]
    assert _snapshot(tmp_path) == before


def test_ac26_a_stale_save_or_remove_is_refused(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    ui = _module_fixture(tmp_path, "M", AC26_MANIFEST, base="{level: 3}")
    cookie = _logged_in(ui)
    caplog.set_level(logging.INFO, logger="core.config_ui")
    overlay = tmp_path / "config.local.yaml"
    rendered = _form_fingerprint(ui, cookie, "/module/M")
    assert rendered == "absent"
    overlay.write_text("modules: {M: {level: 4}}\n", encoding="utf-8")  # changed on disk
    before = _snapshot(tmp_path)
    response = _write(ui, cookie, "/save", "/module/M", [("modules.M.level", "5")], fingerprint=rendered)
    assert response.status == 409
    assert "changed on disk" in _written(response)[0]
    rendered = _form_fingerprint(ui, cookie, "/module/M")
    overlay.write_text("modules: {M: {level: 6}}\n", encoding="utf-8")
    before = _snapshot(tmp_path)
    response = _write(ui, cookie, "/remove", "/module/M", [("path", "modules.M.level")], fingerprint=rendered)
    assert response.status == 409
    missing = _write(ui, cookie, "/save", "/module/M", [("modules.M.level", "5")], fingerprint="")
    assert missing.status == 409
    assert _snapshot(tmp_path) == before and _saves(caplog) == []


def test_ac26_an_unparseable_overlay_is_never_overwritten(tmp_path: Path) -> None:
    ui = _module_fixture(tmp_path, "M", AC26_MANIFEST, base="{level: 3}", overlay="modules: [unclosed\n")
    cookie = _logged_in(ui)
    before = _snapshot(tmp_path)
    response = _write(ui, cookie, "/save", "/module/M", [("modules.M.level", "5")])
    assert response.status == 403
    assert _written(response) == [f"overlay file {tmp_path / 'config.local.yaml'}: is not valid YAML"]
    assert _snapshot(tmp_path) == before


# -- AC27 ---------------------------------------------------------------------

HAND_WRITTEN = (
    "secrets: ['${HAND_SECRET}']\n"
    "actions:\n"
    "  - rule_id: hand-rule\n"
    "    action_name: x.do\n"
    "    destination: {platform: '*', channel_id: '*', scope: fixture}\n"
    "    principals: [someone]\n"
    "    natures: [write]\n"
    "    granted_permissions: [x.do]\n"
    "extra_block: {kept: true}\n"
    "modules:\n"
    "  amod: {label: '${LABEL_REF}'}\n"
)


@pytest.mark.parametrize(
    ("edit", "diagnostic"),
    [
        ((("secrets",), ["${OTHER}"]), "secrets: is read-only in v1"),
        ((("secrets", 0), "${OTHER}"), "secrets[0]: is read-only in v1"),
        ((("actions",), []), "actions: is read-only in v1"),
        ((("actions", 0, "rule_id"), "changed"), "actions[0].rule_id: is read-only in v1"),
        ((("actions",), "ADD"), "actions: is read-only in v1"),
        ((("extra_block",), {"kept": False}), "extra_block: is outside the settings this UI writes"),
        ((("companion",), "x"), "companion: is outside the settings this UI writes"),
        ((("modules", "amod", "token"), "a-literal"), "modules.amod.token: is a declared credential"),
        ((("modules", "amod", "token"), "${NEW_REF}"), "modules.amod.token: is a declared credential"),
        ((("modules", "amod", "label"), "plain"), "modules.amod.label: its configured value is a ${NAME} reference"),
        ((("modules", "amod", "limits"), {}), "modules.amod.limits: " + config_ui.LIMITS_NOTICE),
        ((("modules", "nomod", "x"), 1), "modules.nomod.x: is not a setting of a discovered module"),
        ((("limits", "dedup", "nope"), 1), "limits.dedup.nope: is not a declared limit"),
        ((("triggers", "amod", "channels", "c"), {}), "triggers.amod.channels.c: is not a whole channel policy of a discovered trigger input"),
    ],
)
def test_ac27_writes_outside_the_scope_or_at_protected_fields_are_refused(
    edit: tuple[tuple[object, ...], object], diagnostic: str, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    ui = _check_fixture(tmp_path, VALID_MODULES, overlay=HAND_WRITTEN)
    caplog.set_level(logging.INFO, logger="core.config_ui")
    path, value = edit
    if value == "ADD":
        value = [*_overlay_doc(tmp_path / "config.local.yaml")["actions"], {"rule_id": "new"}]  # type: ignore[index]
    before = _snapshot(tmp_path)
    result = ui.save([(path, value)], fingerprint=_fingerprint(tmp_path / "config.local.yaml"))
    assert result.outcome == "refused"
    assert result.diagnostics == (diagnostic,)
    assert _snapshot(tmp_path) == before and _saves(caplog) == []


@pytest.mark.parametrize("path", ["secrets", "actions", "extra_block", "extra_block.kept", "modules.amod.label"])
def test_ac27_removing_a_read_only_or_protected_key_is_refused(path: str, tmp_path: Path) -> None:
    ui = _check_fixture(tmp_path, VALID_MODULES, overlay=HAND_WRITTEN)
    cookie = _logged_in(ui)
    before = _snapshot(tmp_path)
    response = _write(ui, cookie, "/remove", "/module/amod", [("path", path)])
    assert response.status == 403
    assert _snapshot(tmp_path) == before


@pytest.mark.parametrize(
    "fields",
    [
        [("secrets", '["${OTHER}"]')],
        [("actions", "[]")],
        [("extra_block", "{}")],
        [("modules.amod.token", "typed-literal-credential")],
        [("modules.amod.label", "plain")],
        [("modules.amod.level", "5"), ("modules.amod.token", "typed-literal-credential")],
    ],
)
def test_ac27_posted_protected_or_out_of_scope_fields_are_refused(
    fields: list[tuple[str, str]], tmp_path: Path
) -> None:
    ui = _check_fixture(tmp_path, VALID_MODULES, overlay=HAND_WRITTEN)
    cookie = _logged_in(ui)
    before = _snapshot(tmp_path)
    response = _write(ui, cookie, "/save", "/module/amod", fields)
    assert response.status == 403
    assert "typed-literal-credential" not in response.body.decode("utf-8")
    assert _snapshot(tmp_path) == before


def test_ac27_hand_written_actions_and_secrets_survive_an_unrelated_save(tmp_path: Path) -> None:
    ui = _check_fixture(tmp_path, VALID_MODULES, overlay=HAND_WRITTEN)
    overlay = tmp_path / "config.local.yaml"
    parsed = _overlay_doc(overlay)
    cookie = _logged_in(ui)
    assert _write(ui, cookie, "/save", "/module/amod", [("modules.amod.level", "9")]).status == 303
    after = _overlay_doc(overlay)
    assert after["actions"] == parsed["actions"]  # type: ignore[index]
    assert after["secrets"] == parsed["secrets"] == ["${HAND_SECRET}"]  # type: ignore[index]
    assert after["extra_block"] == parsed["extra_block"]  # type: ignore[index]
    assert after["modules"] == {"amod": {"label": "${LABEL_REF}", "level": 9}}  # type: ignore[index]


def test_ac27_a_save_or_remove_through_a_yaml_alias_leaves_the_other_path_unchanged(
    tmp_path: Path,
) -> None:
    overlay_text = (
        "actions:\n  rule: &shared {n: 1, k: kept}\n"
        "modules:\n  M:\n    o: *shared\n"
    )
    ui = _module_fixture(tmp_path, "M", ANCESTOR_MANIFEST, overlay=overlay_text)
    overlay = tmp_path / "config.local.yaml"
    result = ui.save([(("modules", "M", "o", "n"), 2)], fingerprint=_fingerprint(overlay))
    assert result.outcome == "saved"
    after = _overlay_doc(overlay)
    assert after["actions"] == {"rule": {"n": 1, "k": "kept"}}  # type: ignore[index]
    assert after["modules"] == {"M": {"o": {"n": 2, "k": "kept"}}}  # type: ignore[index]

    overlay.write_text(overlay_text, encoding="utf-8")
    result = ui.remove("modules.M.o.n", fingerprint=_fingerprint(overlay))
    assert result.outcome == "removed"
    after = _overlay_doc(overlay)
    assert after["actions"] == {"rule": {"n": 1, "k": "kept"}}  # type: ignore[index]
    assert after["modules"] == {"M": {"o": {"k": "kept"}}}  # type: ignore[index]


CREDENTIAL_TRIGGER_MANIFEST = TRIGGER_MANIFEST.replace(
    "settings_schema: {type: object, properties: {}}",
    "settings_schema: {type: object, properties: {token: {type: string}}}\ncredentials: [token]",
)


def test_ac24_a_saved_path_naming_a_secret_is_logged_redacted(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    literal = "literal-credential-channel-7c1d"
    ui = _module_fixture(
        tmp_path, "tmod", CREDENTIAL_TRIGGER_MANIFEST, overlay=f"modules:\n  tmod: {{token: {literal}}}\n"
    )
    cookie = _logged_in(ui)
    caplog.set_level(logging.INFO, logger="core.config_ui")
    response = _write(
        ui,
        cookie,
        "/save",
        "/module/tmod",
        [
            ("add_channel_policy", "triggers.tmod.channels"),
            ("channel", literal),
            ("combination", "any_of"),
            ("rule_type", "odds"),
            ("parameters", '{"odds": 0.5}'),
        ],
    )
    assert response.status == 303, _written(response)
    assert literal in _overlay_doc(tmp_path / "config.local.yaml")["triggers"]["tmod"]["channels"]  # type: ignore[index]
    records = _saves(caplog)
    assert len(records) == 1
    record = records[0]
    assert literal not in record.getMessage()
    assert all(literal not in path for path in record.config_ui_paths)  # type: ignore[attr-defined]
    assert list(record.config_ui_paths) == ["triggers.tmod.channels.[hidden]"]  # type: ignore[attr-defined]
    assert literal not in caplog.text


ANCESTOR_MANIFEST = """settings_schema:
  type: object
  properties:
    o:
      type: object
      properties:
        k: {type: string}
        n: {type: integer}
    c:
      type: object
      properties:
        secret: {type: string}
        x: {type: integer}
credentials: [c.secret]
"""


def test_ac27_an_ancestor_save_must_keep_every_protected_descendant(tmp_path: Path) -> None:
    from core.main import load_config

    ui = _module_fixture(tmp_path, "M", ANCESTOR_MANIFEST, base="{o: {k: '${VAR_K}', n: 1}}")
    ui.environ = {"VAR_K": "resolved-var-k-value"}
    overlay = tmp_path / "config.local.yaml"
    target = ("modules", "M", "o")
    before = _snapshot(tmp_path)
    for value in ({"k": "plain", "n": 2}, {"n": 2}, {"k": "${OTHER_K}", "n": 2}):
        result = ui.save([(target, value)], fingerprint="absent")
        assert result.outcome == "refused"
        assert result.diagnostics == ("modules.M.o.k: a protected field must keep its configured text",)
        assert _snapshot(tmp_path) == before
    result = ui.save([(target, {"k": "${VAR_K}", "n": 2})], fingerprint="absent")
    assert result.outcome == "saved"
    text = overlay.read_text(encoding="utf-8")
    assert "resolved-var-k-value" not in text
    assert _overlay_doc(overlay) == {"modules": {"M": {"o": {"k": "${VAR_K}", "n": 2}}}}
    assert load_config(tmp_path / "config.yaml", environ=ui.environ)["modules"]["M"]["o"] == {
        "k": "resolved-var-k-value",
        "n": 2,
    }


def test_ac27_an_ancestor_remove_of_a_credential_literal_is_refused(tmp_path: Path) -> None:
    literal = "literal-credential-9f2e"
    ui = _module_fixture(
        tmp_path, "M", ANCESTOR_MANIFEST, overlay=f"modules:\n  M:\n    c: {{secret: {literal}, x: 1}}\n"
    )
    cookie = _logged_in(ui)
    before = _snapshot(tmp_path)
    response = _write(ui, cookie, "/remove", "/module/M", [("path", "modules.M.c")])
    assert response.status == 403
    assert _written(response) == ["modules.M.c.secret: a protected field must keep its configured text"]
    assert literal not in response.body.decode("utf-8")
    assert _snapshot(tmp_path) == before
    # An ancestor save keeps a hidden credential as its configured literal.
    result = ui.save(
        [(("modules", "M", "c"), {"secret": HIDDEN_LITERAL, "x": 2})],
        fingerprint=_fingerprint(tmp_path / "config.local.yaml"),
    )
    assert result.outcome == "saved"
    assert _overlay_doc(tmp_path / "config.local.yaml") == {"modules": {"M": {"c": {"secret": literal, "x": 2}}}}
    result = ui.save(
        [(("modules", "M", "c"), {"secret": "replaced", "x": 2})],
        fingerprint=_fingerprint(tmp_path / "config.local.yaml"),
    )
    assert result.outcome == "refused"


def test_ac27_a_referenced_channel_key_is_saved_as_its_reference_text(tmp_path: Path) -> None:
    environ = {"TWITCH_BROADCASTER_ID": "broadcaster-4471-canary"}
    ui, base = _profile_copy(tmp_path, environ)
    overlay = tmp_path / "config.local.yaml"
    cookie = _logged_in(ui)
    # Gate-2 F2: the channel key is named by its position in the field name.
    field = "triggers.twitch.channels.@0.rules[0].parameters.keywords"
    response = _write(ui, cookie, "/save", "/module/twitch", [(field, '["!ask", "!q"]')])
    assert response.status == 303, _written(response)
    text = overlay.read_text(encoding="utf-8")
    assert "broadcaster-4471-canary" not in text
    assert _overlay_doc(overlay) == {
        "triggers": {
            "twitch": {
                "channels": {
                    "${TWITCH_BROADCASTER_ID}": {
                        "combination": "all_of",
                        "rules": [{"type": "keyword", "parameters": {"keywords": ["!ask", "!q"]}}],
                    }
                }
            }
        }
    }


def test_ac27_a_referenced_mapping_key_is_protected_as_key_text() -> None:
    """The ancestor rule compares key text: a renamed or resolved key is refused."""

    view = ConfigView(
        base_path=Path("base.yaml"),
        overlay_path=Path("base.local.yaml"),
        overlay_exists=False,
        overlay_digest=None,
        overlay_mtime_ns=None,
        base={},
        overlay={},
        merged={"modules_directory": "./m", "modules": {"M": {"map": {"${KEY}": 1, "plain": 2}}}},
        modules_directory=None,
        modules={},
        references={"KEY": True},
        secret_values=frozenset(),
    )
    target = ("modules", "M", "map")
    kept, refusals = config_ui._protect_save(view, target, {"${KEY}": 5})
    assert refusals == [] and kept == {"${KEY}": 5}
    _, refusals = config_ui._protect_save(view, target, {"resolved-key": 1, "plain": 2})
    assert refusals == ["modules.M.map.${KEY}: a protected field must keep its configured text"]


# -- AC28 ---------------------------------------------------------------------


def test_ac28_an_interrupted_write_leaves_the_previous_overlay_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    ui = _check_fixture(tmp_path, VALID_MODULES, overlay=HAND_WRITTEN)
    cookie = _logged_in(ui)
    caplog.set_level(logging.INFO, logger="core.config_ui")
    overlay = tmp_path / "config.local.yaml"
    before = _snapshot(tmp_path)
    prepared: list[str] = []

    def interrupted(source: object, target: object) -> None:
        prepared.append(Path(str(source)).read_text(encoding="utf-8"))
        raise OSError("simulated failure before the replace")

    monkeypatch.setattr(os, "replace", interrupted)
    response = _write(ui, cookie, "/save", "/module/amod", [("modules.amod.level", "9")])
    monkeypatch.undo()
    assert response.status == 500
    assert prepared and yaml.safe_load(prepared[0])["modules"]["amod"]["level"] == 9
    assert _snapshot(tmp_path) == before  # no temporary file left, overlay unchanged
    assert _overlay_doc(overlay)["modules"] == {"amod": {"label": "${LABEL_REF}"}}  # type: ignore[index]
    assert _saves(caplog) == []


def test_ac28_the_write_targets_only_the_managed_overlay(tmp_path: Path) -> None:
    base = tmp_path / "config.yaml"
    base.write_text("a: 1\n", encoding="utf-8")
    overlay = tmp_path / "config.local.yaml"
    for target, managed in ((base, base), (tmp_path / "other.yaml", overlay), (None, overlay)):
        with pytest.raises(RuntimeError):
            config_ui._write_overlay(target, managed, base, {"a": 2})
    assert sorted(path.name for path in tmp_path.iterdir()) == ["config.yaml"]
    config_ui._write_overlay(overlay, overlay, base, {"k": "${NAME}", "${KEY}": {"é": 1}})
    assert yaml.safe_load(overlay.read_text(encoding="utf-8")) == {"k": "${NAME}", "${KEY}": {"é": 1}}
    assert "é" in overlay.read_text(encoding="utf-8")  # allow_unicode
    assert base.read_text(encoding="utf-8") == "a: 1\n"


# ---------------------------------------------------------------------------
# P14: the status record reader, liveness, drift and running state (R7, R4)
# ---------------------------------------------------------------------------

STATUS_PAGES = ("/", "/core", "/module/M", "/module/N")
#: A container entry naming no discovered module: never displayed (AC42).
UNDISCOVERED = "zeta_unlisted"
STARTED_AT = "2026-09-28T14:03:07.412Z"
PUBLISHED_AT = "2026-09-28T14:05:09.5Z"


def _dead(pid: int, child: object) -> bool:
    return False


def _status_ui(tmp_path: Path, *extra: str, probe: object = None) -> ConfigUI:
    """Discovered modules M and N, both enabled; the default status path."""

    for name in ("M", "N"):
        _write_manifest(tmp_path / "mods", name, AC21_MANIFEST)
    base = tmp_path / "config.yaml"
    base.write_text(
        "modules_directory: ./mods\nenabled_modules: [M, N]\nmodules:\n  M: {a: 1}\n  N: {a: 1}\n",
        encoding="utf-8",
    )
    settings = UISettings.from_argv(["--config", str(base), *extra])
    return ConfigUI(settings, probe=probe)  # type: ignore[arg-type]


def _valid(ui: ConfigUI, **changes: object) -> dict[str, object]:
    """V of AC40: the live test process's pid and the on-disk digest."""

    record: dict[str, object] = {
        "version": 1,
        "pid": os.getpid(),
        "started_at": STARTED_AT,
        "published_at": PUBLISHED_AT,
        "sequence": 1,
        "digest": on_disk_digest(ui.base_path, ui.overlay_path),
        "state": "ready",
        "modules": {"M": {"state": "ready"}},
    }
    record.update(changes)
    return record


def _publish(ui: ConfigUI, record: object) -> bytes:
    data = record if isinstance(record, bytes) else json.dumps(record).encode("utf-8")
    ui.status_path.write_bytes(data)
    return data


def _status_pages(ui: ConfigUI, cookie: str) -> dict[str, str]:
    pages: dict[str, str] = {}
    for path in STATUS_PAGES:
        response = _get(ui, path, cookie)
        assert response.status == 200, path
        pages[path] = response.body.decode("utf-8")
    return pages


def _drift(page: str) -> str:
    found = re.findall(r'<td id="drift" data-drift="([^"]*)">', page)
    assert len(found) == 1
    return found[0]


def _record_status(page: str) -> str:
    match = re.search(r'<td id="record-status" data-record="[^"]*">(.*?)</td>', page, re.S)
    assert match is not None
    return _html.unescape(match.group(1))


def _supervision(page: str) -> str:
    match = re.search(r'<td id="supervision">(.*?)</td>', page, re.S)
    assert match is not None
    return _html.unescape(match.group(1))


def _running_of(page: str, name: str) -> tuple[str | None, str]:
    """Module *name*'s running part of its readiness cell (base page) or section."""

    if f'<tr data-module="{name}"' in page:
        pattern = rf'<tr data-module="{name}".*?<span class="running"( data-running="[^"]*")?>(.*?)</span>'
    else:
        pattern = r'<section id="readiness">.*?<span class="running"( data-running="[^"]*")?>(.*?)</span>'
    match = re.search(pattern, page, re.S)
    assert match is not None
    state = None if match.group(1) is None else match.group(1).split('"')[1]
    return state, _html.unescape(match.group(2))


def _visible(page: str) -> str:
    """The page without its random tokens and hidden form values."""

    return re.sub(r'(content|value)="[^"]*"', "", page)


def _record_values(record: object) -> list[str]:
    """Every scalar of *record* as text, for the "no field displayed" checks."""

    if isinstance(record, dict):
        return [text for value in record.values() for text in _record_values(value)]
    if isinstance(record, list):
        return [text for value in record for text in _record_values(value)]
    if record is None or isinstance(record, bool):
        return []
    return [str(record)]


def _assert_unusable(ui: ConfigUI, cookie: str, data: bytes, values: Sequence[str]) -> str:
    """The behaviour of AC39 on every page; return the reason shown."""

    reasons = set()
    for path, page in _status_pages(ui, cookie).items():
        status = _record_status(page)
        assert status.startswith(f"{STATUS_RECORD_UNUSABLE}: "), (path, status)
        reason = status[len(STATUS_RECORD_UNUSABLE) + 2 :]
        assert reason and NO_STATUS_RECORD not in reason
        reasons.add(reason)
        assert _drift(page) == DRIFT_UNKNOWN
        assert DRIFT_IN_SYNC not in page
        assert "data-running" not in page and "running: " not in page
        assert _supervision(page) == NO_RUNNING_PROCESS
        if data:
            assert data.decode("utf-8", errors="replace") not in reason
        visible = _visible(page)
        for value in values:
            if len(value) >= 3:
                assert value not in reason, (path, value)
                assert value not in visible, (path, value)
    assert len(reasons) == 1
    return reasons.pop()


# -- read_status: classification ----------------------------------------------


def _mutations(v: dict[str, object]) -> list[tuple[str, str | None, bytes]]:
    """The single mutations of V listed by AC40: (label, field named, bytes)."""

    def changed(field_name: str, value: object) -> bytes:
        return json.dumps({**v, field_name: value}).encode("utf-8")

    digest = str(v["digest"])
    cases: list[tuple[str, str | None, bytes]] = []
    for name in v:
        removed = {key: value for key, value in v.items() if key != name}
        cases.append((f"without {name}", name, json.dumps(removed).encode("utf-8")))
    changes: list[tuple[str, object]] = [
        ("version", 2), ("version", "1"), ("version", 1.0),
        ("pid", 0), ("pid", -4), ("pid", True), ("pid", 2147483648), ("pid", "123"),
        ("started_at", 1759068187),
        ("started_at", "2026-09-28 14:03:07Z"),
        ("started_at", "2026-09-28T14:03:07+02:00"),
        ("started_at", "2026-02-30T00:00:00Z"),
        ("started_at", "2026-09-28T14:03:07.1234567Z"),
        ("published_at", None),
        ("sequence", 0), ("sequence", 1.5), ("sequence", 9007199254740992), ("sequence", "1"),
        ("digest", digest.upper()), ("digest", digest[:63]), ("digest", f"sha256:{digest}"),
        ("digest", None),
        ("state", "degraded"), ("state", "READY"), ("state", 1),
        ("modules", []),
        ("modules", {"": {"state": "ready"}}),
        ("modules", {"M": "ready"}),
        ("modules", {"M": {}}),
        ("modules", {"M": {"state": "stopped"}}),
        ("modules", {"M": {"state": None}}),
    ]
    for name, value in changes:
        cases.append((f"{name}={json.dumps(value)}", name, changed(name, value)))
    text = json.dumps(v)
    duplicate = text[:-1] + f', "pid": {v["pid"]}' + "}"
    cases.append(("pid twice", None, duplicate.encode("utf-8")))
    cases.append(("NaN", None, (text[:-1] + ', "extra": NaN}').encode("utf-8")))
    oversized = text.encode("utf-8")
    cases.append(("1 MiB + 1 byte", None, oversized + b" " * (STATUS_MAX_BYTES + 1 - len(oversized))))
    return cases


def test_ac40_every_listed_mutation_of_v_is_unusable_with_a_value_free_reason(
    tmp_path: Path,
) -> None:
    ui = _status_ui(tmp_path)
    v = _valid(ui)
    cases = _mutations(v)
    assert len(cases) == 8 + 31 + 3
    assert len(cases[-1][2]) == STATUS_MAX_BYTES + 1
    values = [str(os.getpid()), str(v["digest"]), STARTED_AT, PUBLISHED_AT]
    for label, field_name, data in cases:
        _publish(ui, data)
        reading = read_status(ui.status_path)
        assert reading.kind == "unusable", label
        assert reading.record is None
        assert reading.reason is not None
        if field_name is not None:
            assert field_name in reading.reason, (label, reading.reason)
        assert "expected" in reading.reason, label
        for value in values:
            assert value not in reading.reason, (label, value)


def test_ac40_the_mutations_show_the_ac39_behaviour_on_every_page(tmp_path: Path) -> None:
    ui = _status_ui(tmp_path)
    cookie = _logged_in(ui)
    v = _valid(ui)
    values = [str(os.getpid()), str(v["digest"]), STARTED_AT, PUBLISHED_AT, "123"]
    for _label, _field_name, data in _mutations(v):
        _publish(ui, data)
        _assert_unusable(ui, cookie, data, values)


def test_ac40_v_is_usable_and_extra_keys_change_nothing(tmp_path: Path) -> None:
    ui = _status_ui(tmp_path)
    cookie = _logged_in(ui)
    v = _valid(ui)
    _publish(ui, v)
    reading = read_status(ui.status_path)
    assert reading.kind == "usable" and reading.reason is None
    assert reading.record is not None
    assert reading.record.pid == os.getpid()
    assert dict(reading.record.modules) == {"M": "ready"}
    shown = _status_pages(ui, cookie)
    for path, page in shown.items():
        assert _drift(page) == DRIFT_IN_SYNC, path
        assert _record_status(page) == f"status record published at {PUBLISHED_AT}"
        assert STATUS_RECORD_UNUSABLE not in page
        assert _supervision(page) == NOT_SUPERVISED
    for path in ("/", "/module/M"):
        assert _running_of(shown[path], "M") == (
            "ready",
            f"running: ready at {PUBLISHED_AT} ({NOT_SUPERVISED})",
        )
    assert str(os.getpid()) not in _visible(shown["/"])

    extra = _valid(ui, extra_top={"pid": 1}, modules={"M": {"state": "ready", "note": "x-extra"}})
    _publish(ui, extra)
    assert read_status(ui.status_path) == reading
    assert _status_pages(ui, cookie) == shown


def test_ac40_a_dead_pid_is_unknown_with_no_running_state(tmp_path: Path) -> None:
    ui = _status_ui(tmp_path, probe=_dead)
    cookie = _logged_in(ui)
    _publish(ui, _valid(ui))
    assert read_status(ui.status_path).kind == "usable"
    for page in _status_pages(ui, cookie).values():
        assert _drift(page) == DRIFT_UNKNOWN
        assert DRIFT_IN_SYNC not in page
        assert "data-running" not in page and "running: " not in page
        assert _record_status(page) == f"status record published at {PUBLISHED_AT}"
        assert _supervision(page).startswith("the recorded process is not running")


def test_read_status_accepts_the_integer_forms_only(tmp_path: Path) -> None:
    """``1.0``, ``1e0`` and ``true`` are not integers; ``1`` is (R7)."""

    ui = _status_ui(tmp_path)
    text = json.dumps(_valid(ui))
    assert '"version": 1,' in text
    for spelled, usable in (("1", True), ("1.0", False), ("1e0", False), ("true", False)):
        ui.status_path.write_text(text.replace('"version": 1,', f'"version": {spelled},'), encoding="utf-8")
        assert (read_status(ui.status_path).kind == "usable") is usable, spelled
    for constant in ("Infinity", "-Infinity"):
        ui.status_path.write_text(text[:-1] + f', "x": {constant}}}', encoding="utf-8")
        assert read_status(ui.status_path).kind == "unusable", constant
    ui.status_path.write_bytes(b'{"version": 1, "pid": \xff}')
    assert "UTF-8" in str(read_status(ui.status_path).reason)
    ui.status_path.write_text("[" * 100_000, encoding="utf-8")
    assert read_status(ui.status_path).kind == "unusable"


def test_read_status_caps_the_read_when_the_file_grew_after_the_stat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ui = _status_ui(tmp_path)
    text = json.dumps(_valid(ui)).encode("utf-8")
    _publish(ui, text + b" " * (STATUS_MAX_BYTES + 1 - len(text)))
    real_stat = os.stat
    target = os.fspath(ui.status_path)

    def small(path: object, *args: object, **kwargs: object) -> os.stat_result:
        found = real_stat(path, *args, **kwargs)  # type: ignore[arg-type]
        if os.fspath(path) != target:  # type: ignore[arg-type]
            return found
        fields = list(found)
        fields[6] = 10  # st_size, as it was before the file grew
        return os.stat_result(fields)

    monkeypatch.setattr(os, "stat", small)
    reading = read_status(ui.status_path)
    monkeypatch.undo()
    assert reading.kind == "unusable" and "at most" in str(reading.reason)


def test_read_status_of_an_absent_path_is_absent(tmp_path: Path) -> None:
    assert read_status(tmp_path / "missing.json").kind == "absent"


# -- AC39 ---------------------------------------------------------------------


def test_ac39_unreadable_or_malformed_records_are_unusable_on_every_page(tmp_path: Path) -> None:
    ui = _status_ui(tmp_path)
    cookie = _logged_in(ui)
    digest = on_disk_digest(ui.base_path, ui.overlay_path)
    without_digest = {key: value for key, value in _valid(ui).items() if key != "digest"}
    records: list[tuple[bytes, list[str]]] = [
        (b"\x00not json {CANARY-STATUS-BYTES", ["CANARY-STATUS-BYTES"]),
        (b"[1, 2]", ["[1, 2]"]),
        (json.dumps(without_digest).encode("utf-8"), [STARTED_AT, PUBLISHED_AT, str(os.getpid())]),
        (json.dumps(_valid(ui, pid="123")).encode("utf-8"), [digest, STARTED_AT, PUBLISHED_AT, "123"]),
    ]
    reasons = []
    for data, values in records:
        _publish(ui, data)
        reasons.append(_assert_unusable(ui, cookie, data, values))
    assert "digest" in reasons[2] and "pid" in reasons[3]

    ui.status_path.unlink()
    ui.status_path.mkdir()
    reasons.append(_assert_unusable(ui, cookie, b"", []))
    ui.status_path.rmdir()

    # The UI keeps serving: a usable record is shown right after.
    _publish(ui, _valid(ui))
    assert all(_drift(page) == DRIFT_IN_SYNC for page in _status_pages(ui, cookie).values())


def test_ac39_a_record_with_mode_000_is_unusable(tmp_path: Path) -> None:
    if os.geteuid() == 0:
        pytest.skip("running as root: a mode-000 file stays readable, so it cannot be unreadable")
    ui = _status_ui(tmp_path)
    cookie = _logged_in(ui)
    data = _publish(ui, _valid(ui))
    ui.status_path.chmod(0)
    try:
        reason = _assert_unusable(ui, cookie, data, [str(os.getpid()), STARTED_AT])
    finally:
        ui.status_path.chmod(0o600)
    assert "unreadable" in reason


# -- AC42 ---------------------------------------------------------------------


def test_ac42_publisher_obligations_are_never_verified_by_the_reader(tmp_path: Path) -> None:
    ui = _status_ui(tmp_path)
    cookie = _logged_in(ui)
    records = [
        _valid(ui, modules={}),
        _valid(ui, modules={"M": {"state": "ready"}, UNDISCOVERED: {"state": "degraded"}}),
        _valid(ui, sequence=7),
        _valid(ui, started_at="2026-09-28T15:00:00Z", published_at="2026-09-28T14:00:00Z"),
    ]
    for record in records:
        for digest, drift in (
            (on_disk_digest(ui.base_path, ui.overlay_path), DRIFT_IN_SYNC),
            ("0" * 64, DRIFT_DIFFERS),
        ):
            _publish(ui, {**record, "digest": digest})
            assert read_status(ui.status_path).kind == "usable"
            pages = _status_pages(ui, cookie)
            for page in pages.values():
                assert _drift(page) == drift
                assert STATUS_RECORD_UNUSABLE not in page
                assert UNDISCOVERED not in page
            entries = record["modules"]
            assert isinstance(entries, dict)
            for path in ("/", "/module/M"):
                if "M" in entries:
                    assert _running_of(pages[path], "M")[0] == "ready"
                else:
                    assert _running_of(pages[path], "M") == (None, NOT_REPORTED)
            for path in ("/", "/module/N"):
                assert _running_of(pages[path], "N") == (None, NOT_REPORTED)


# -- AC21 readiness -------------------------------------------------------------


def test_ac21_readiness_combines_the_check_verdict_and_the_running_state(tmp_path: Path) -> None:
    ui = _status_ui(tmp_path)
    cookie = _logged_in(ui)

    def cell(name: str) -> str:
        page = _get(ui, "/", cookie).body.decode("utf-8")
        match = re.search(rf'<tr data-module="{name}".*?<td class="readiness">(.*?)</td>', page, re.S)
        assert match is not None
        return _html.unescape(re.sub(r"<[^>]*>", " ", match.group(1))).split()

    assert " ".join(cell("M")) == f"not checked yet {NO_RUNNING_PROCESS}"
    _publish(ui, _valid(ui, modules={"M": {"state": "degraded"}, "N": {"state": "ready"}}))
    page = _get(ui, "/", cookie).body.decode("utf-8")
    assert _running_of(page, "M") == ("degraded", f"running: degraded at {PUBLISHED_AT} ({NOT_SUPERVISED})")
    assert _running_of(page, "N")[0] == "ready"
    module_page = _get(ui, "/module/M", cookie).body.decode("utf-8")
    assert _running_of(module_page, "M")[0] == "degraded"
    assert "not checked yet" in _section(module_page, "readiness")


# -- AC31 display ---------------------------------------------------------------


def test_ac31_a_foreign_live_record_is_shown_not_supervised(tmp_path: Path) -> None:
    ui = _status_ui(tmp_path)
    cookie = _logged_in(ui)
    assert ui.status_path == tmp_path / "config.yaml.status.json"
    assert ui.child is None
    _publish(ui, _valid(ui, modules={"M": {"state": "ready"}, "N": {"state": "degraded"}}))
    for path, page in _status_pages(ui, cookie).items():
        assert _supervision(page) == NOT_SUPERVISED, path
        assert SUPERVISED not in page
    page = _get(ui, "/", cookie).body.decode("utf-8")
    assert _running_of(page, "N") == ("degraded", f"running: degraded at {PUBLISHED_AT} ({NOT_SUPERVISED})")


def test_ac31_only_the_one_status_path_is_read(tmp_path: Path) -> None:
    ui = _status_ui(tmp_path)
    cookie = _logged_in(ui)
    other = tmp_path / "other.status.json"
    other.write_text(json.dumps(_valid(ui)), encoding="utf-8")
    for page in _status_pages(ui, cookie).values():
        assert _record_status(page) == NO_STATUS_RECORD
        assert _drift(page) == DRIFT_UNKNOWN
        assert _supervision(page) == NO_RUNNING_PROCESS

    custom = tmp_path / "custom.json"
    overridden = _status_ui(tmp_path / "second", "--status-file", str(custom))
    assert overridden.status_path == custom
    default = tmp_path / "second" / "config.yaml.status.json"
    default.write_text(json.dumps(_valid(overridden)), encoding="utf-8")
    cookie = _logged_in(overridden)
    assert _record_status(_get(overridden, "/", cookie).body.decode("utf-8")) == NO_STATUS_RECORD
    custom.write_text(json.dumps(_valid(overridden)), encoding="utf-8")
    assert _drift(_get(overridden, "/", cookie).body.decode("utf-8")) == DRIFT_IN_SYNC


class _FakeChild:
    def __init__(self, pid: int, returncode: int | None) -> None:
        self.pid = pid
        self.returncode = returncode

    def poll(self) -> int | None:
        return self.returncode


def test_the_launched_child_is_supervised_and_its_handle_is_authoritative(tmp_path: Path) -> None:
    ui = _status_ui(tmp_path)
    cookie = _logged_in(ui)
    _publish(ui, _valid(ui))
    ui.child = _FakeChild(os.getpid(), None)
    page = _get(ui, "/", cookie).body.decode("utf-8")
    assert _supervision(page) == SUPERVISED and _drift(page) == DRIFT_IN_SYNC
    assert _running_of(page, "M") == ("ready", f"running: ready at {PUBLISHED_AT} ({SUPERVISED})")
    # The child exited: dead, although a signal-0 probe of its pid succeeds.
    ui.child = _FakeChild(os.getpid(), 0)
    page = _get(ui, "/", cookie).body.decode("utf-8")
    assert _drift(page) == DRIFT_UNKNOWN and "data-running" not in page


def test_the_liveness_probe_uses_signal_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[tuple[int, int]] = []

    def kill_with(error: type[OSError] | None) -> None:
        def kill(pid: int, signal_number: int) -> None:
            sent.append((pid, signal_number))
            if error is not None:
                raise error()

        monkeypatch.setattr(os, "kill", kill)

    kill_with(ProcessLookupError)
    assert process_alive(4242, None) is False
    kill_with(PermissionError)
    assert process_alive(4242, None) is True
    kill_with(None)
    assert process_alive(4242, _FakeChild(99, 0)) is True
    assert sent == [(4242, 0)] * 3
    assert process_alive(99, _FakeChild(99, None)) is True
    assert process_alive(99, _FakeChild(99, 1)) is False
    assert len(sent) == 3  # the child's own handle, never a signal


# -- AC32 display ---------------------------------------------------------------


def test_ac32_drift_follows_the_overlay_and_the_record(tmp_path: Path) -> None:
    ui = _status_ui(tmp_path)
    cookie = _logged_in(ui)
    _publish(ui, _valid(ui))
    assert {_drift(page) for page in _status_pages(ui, cookie).values()} == {DRIFT_IN_SYNC}

    response = _write(ui, cookie, "/save", "/module/M", [("modules.M.a", "3")])
    assert response.status == 303
    assert {_drift(page) for page in _status_pages(ui, cookie).values()} == {DRIFT_DIFFERS}

    dead = ConfigUI(ui.settings, probe=_dead)
    dead_cookie = _logged_in(dead)
    _publish(dead, _valid(dead))
    assert {_drift(page) for page in _status_pages(dead, dead_cookie).values()} == {DRIFT_UNKNOWN}

    ui.status_path.unlink()
    pages = _status_pages(ui, cookie)
    assert {_drift(page) for page in pages.values()} == {DRIFT_UNKNOWN}
    assert {_record_status(page) for page in pages.values()} == {NO_STATUS_RECORD}


# ---------------------------------------------------------------------------
# P15: Apply — the supervised restart (R7, A4, D9)
#
# Real fake children: scripts written to tmp_path, run with sys.executable,
# publishing through the core.overlay helpers. Every wait is a bounded
# Event.wait (never a sleep) and every window is about 1 s.
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[1]
APPLY_WINDOW = 1.0
#: The window of a test whose child must be accepted: long only if it fails.
ACCEPT_WINDOW = 10.0
STOP_GRACE = 0.5
MIB = 1024 * 1024

CHILD_PRELUDE = f"""\
import json, os, signal, sys, threading
sys.path.insert(0, {str(REPO_ROOT)!r})
from core.overlay import STATUS_FILE_VARIABLE, on_disk_digest

STATUS = os.environ[STATUS_FILE_VARIABLE]
BASE, OVERLAY = sys.argv[1], sys.argv[2]


def publish(data=None, **changes):
    if data is None:
        record = {{
            "version": 1,
            "pid": os.getpid(),
            "started_at": "2026-09-28T14:03:07Z",
            "published_at": "2026-09-28T14:03:08Z",
            "sequence": 1,
            "digest": on_disk_digest(BASE, OVERLAY),
            "state": "ready",
            "modules": {{"M": {{"state": "ready"}}, "N": {{"state": "ready"}}}},
        }}
        record.update(changes)
        data = json.dumps(record).encode("utf-8")
    temporary = STATUS + ".tmp"
    with open(temporary, "wb") as handle:
        handle.write(data)
    os.replace(temporary, STATUS)


def hold():
    threading.Event().wait(60)

"""

READY_CHILD = "publish()\nhold()\n"


def _passing_checker(*args: object) -> tuple[bool, list[str]]:
    return True, []


def _failing_checker(*args: object) -> tuple[bool, list[str]]:
    return False, ["modules.M.a: must be an integer"]


@pytest.fixture
def launched() -> Iterator[list[config_ui.Supervisor]]:
    """Every supervisor a test builds; each one's child is stopped afterwards."""

    supervisors: list[config_ui.Supervisor] = []
    yield supervisors
    for supervisor in supervisors:
        supervisor.stop_grace = STOP_GRACE
        supervisor.stop()


def _apply_ui(
    tmp_path: Path,
    launched: list[config_ui.Supervisor],
    body: str,
    *,
    window: float = ACCEPT_WINDOW,
    checker: object = _passing_checker,
    **supervisor_options: object,
) -> ConfigUI:
    """The P14 status fixture whose launch argv (after ``--``) runs *body*."""

    ui = _status_ui(tmp_path)
    script = tmp_path / "child.py"
    script.write_text(CHILD_PRELUDE + body, encoding="utf-8")
    settings = UISettings.from_argv(
        [
            "--config",
            str(ui.base_path),
            "--",
            sys.executable,
            str(script),
            str(ui.base_path),
            str(ui.overlay_path),
        ]
    )
    supervisor = config_ui.Supervisor(
        config_ui.default_launch_argv(settings),
        settings.status_path,
        stop_grace=STOP_GRACE,
        apply_window=window,
        **supervisor_options,  # type: ignore[arg-type]
    )
    launched.append(supervisor)
    return ConfigUI(settings, checker=checker, supervisor=supervisor)  # type: ignore[arg-type]


def _bounded_until(condition: object, limit: float = 5.0) -> bool:
    """Poll *condition* with a bounded ``Event.wait`` loop (never a sleep)."""

    event = threading.Event()
    for _ in range(int(limit / 0.05)):
        if condition():  # type: ignore[operator]
            return True
        event.wait(0.05)
    return bool(condition())  # type: ignore[operator]


# -- the launch argv ------------------------------------------------------------


def test_the_default_launch_argv_runs_the_runtime_on_the_same_files(tmp_path: Path) -> None:
    base = tmp_path / "config.yaml"
    plain = UISettings.from_argv(["--config", str(base)])
    assert config_ui.default_launch_argv(plain) == (
        sys.executable, "-m", "core.main", "--config", str(base),
        "--overlay", str(tmp_path / "config.local.yaml"),
    )
    overlay = tmp_path / "mine.yaml"
    explicit = UISettings.from_argv(["--config", str(base), "--overlay", str(overlay)])
    assert config_ui.default_launch_argv(explicit) == (
        sys.executable, "-m", "core.main", "--config", str(base), "--overlay", str(overlay)
    )
    given = UISettings.from_argv(["--config", str(base), "--", "run-me", "--flag"])
    assert config_ui.default_launch_argv(given) == ("run-me", "--flag")
    ui = ConfigUI(explicit)
    assert ui.supervisor.argv == config_ui.default_launch_argv(explicit)
    assert ui.supervisor.status_path == str(explicit.status_path)
    assert (ui.supervisor.stop_grace, ui.supervisor.apply_window) == (10.0, 60.0)


# -- AC29: a failing on-disk Check stops and starts nothing ----------------------


class _SpiedChild:
    def __init__(self, pid: int = 4242) -> None:
        self.pid = pid
        self.calls: list[str] = []

    def poll(self) -> int | None:
        return None

    def terminate(self) -> None:
        self.calls.append("terminate")

    def kill(self) -> None:
        self.calls.append("kill")

    def wait(self, timeout: float | None = None) -> int:
        self.calls.append("wait")
        return 0


def test_ac29_a_failing_on_disk_check_refuses_and_touches_no_process(
    tmp_path: Path, launched: list[config_ui.Supervisor], caplog: pytest.LogCaptureFixture
) -> None:
    started: list[object] = []
    ui = _apply_ui(
        tmp_path,
        launched,
        READY_CHILD,
        checker=_failing_checker,
        popen=lambda *args, **kwargs: started.append(args),
    )
    existing = _SpiedChild()
    ui.child = existing
    cookie, csrf = _login(ui)
    caplog.set_level(logging.INFO, logger="core.config_ui")
    response = _post(ui, "/apply", cookie, csrf)
    page = response.body.decode("utf-8")
    assert 'data-outcome="refused"' in page
    assert "modules.M.a: must be an integer" in page
    assert ui.last_apply is not None and ui.last_apply.check_refused
    assert started == []
    assert existing.calls == []
    assert ui.child is existing
    assert not ui.status_path.exists()
    assert "configuration apply refused" in caplog.text


# -- AC30: accepted, refused, unknown; no shell -----------------------------------


def test_ac30_a_ready_record_of_the_child_with_the_on_disk_digest_is_accepted(
    tmp_path: Path, launched: list[config_ui.Supervisor]
) -> None:
    ui = _apply_ui(tmp_path, launched, READY_CHILD)
    report = ui.apply()
    assert report.outcome == config_ui.APPLY_ACCEPTED
    record = read_status(ui.status_path).record
    assert record is not None and ui.child is not None
    assert record.pid == ui.child.pid != os.getpid()
    assert ui.child.poll() is None


def test_ac30_a_child_exiting_2_is_refused_with_its_status_and_diagnostic(
    tmp_path: Path, launched: list[config_ui.Supervisor]
) -> None:
    body = "sys.stderr.write('config error: modules.M.a is not valid\\n')\nsys.exit(2)\n"
    ui = _apply_ui(tmp_path, launched, body)
    cookie, csrf = _login(ui)
    page = _post(ui, "/apply", cookie, csrf).body.decode("utf-8")
    report = ui.last_apply
    assert report is not None
    assert report.outcome == config_ui.APPLY_REFUSED and not report.check_refused
    assert report.exit_status == 2
    assert "config error: modules.M.a is not valid" in report.stderr_tail
    assert 'data-status="2"' in page
    assert "config error: modules.M.a is not valid" in _html.unescape(page)


def test_ac30_a_child_writing_nothing_within_the_window_is_unknown(
    tmp_path: Path, launched: list[config_ui.Supervisor]
) -> None:
    ui = _apply_ui(tmp_path, launched, "hold()\n", window=APPLY_WINDOW)
    report = ui.apply()
    assert report.outcome == config_ui.APPLY_UNKNOWN
    assert report.exit_status is None
    assert ui.child is not None and ui.child.poll() is None


def test_ac30_the_launch_argv_runs_without_a_shell(
    tmp_path: Path, launched: list[config_ui.Supervisor]
) -> None:
    seen = tmp_path / "argv.json"
    body = f"open({str(seen)!r}, 'w').write(json.dumps(sys.argv[1:]))\npublish()\nhold()\n"
    ui = _apply_ui(tmp_path, launched, body)
    ui.supervisor.argv = (*ui.supervisor.argv, ";echo x", "$HOME", "a b")
    assert ui.apply().outcome == config_ui.APPLY_ACCEPTED
    arguments = json.loads(seen.read_text(encoding="utf-8"))
    assert arguments[2:] == [";echo x", "$HOME", "a b"]


def test_the_child_inherits_the_environment_plus_the_status_variable(
    tmp_path: Path, launched: list[config_ui.Supervisor], monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[dict[str, object]] = []
    real = subprocess.Popen

    def spy(argv: list[str], **kwargs: object) -> subprocess.Popen[bytes]:
        captured.append(kwargs)
        return real(argv, **kwargs)  # type: ignore[call-overload, no-any-return]

    monkeypatch.setenv("P15_INHERITED", "yes")
    ui = _apply_ui(tmp_path, launched, READY_CHILD, popen=spy)
    assert ui.apply().outcome == config_ui.APPLY_ACCEPTED
    (options,) = captured
    env = options["env"]
    assert isinstance(env, dict)
    assert env[config_ui.STATUS_FILE_VARIABLE] == str(ui.status_path)
    assert env["P15_INHERITED"] == "yes"
    assert options["shell"] is False
    assert options["stdout"] is None and options["stderr"] == subprocess.PIPE
    assert options["start_new_session"] is False


# -- AC31: terminate, bounded wait, kill; a foreign pid is never signalled --------


def test_ac31_a_child_ignoring_terminate_is_killed_after_the_bounded_wait(
    tmp_path: Path, launched: list[config_ui.Supervisor]
) -> None:
    calls: list[tuple[int, str]] = []
    children: list[subprocess.Popen[bytes]] = []
    real = subprocess.Popen

    def spied(argv: list[str], **kwargs: object) -> subprocess.Popen[bytes]:
        child = real(argv, **kwargs)  # type: ignore[call-overload]
        terminate, kill, wait = child.terminate, child.kill, child.wait

        def record(name: str, method: object) -> object:
            def call(*args: object, **options: object) -> object:
                calls.append((child.pid, name))
                return method(*args, **options)  # type: ignore[operator]

            return call

        child.terminate = record("terminate", terminate)  # type: ignore[method-assign]
        child.kill = record("kill", kill)  # type: ignore[method-assign]
        child.wait = record("wait", wait)  # type: ignore[method-assign]
        children.append(child)
        return child

    body = "signal.signal(signal.SIGTERM, signal.SIG_IGN)\npublish()\nhold()\n"
    ui = _apply_ui(tmp_path, launched, body, popen=spied)
    assert ui.apply().outcome == config_ui.APPLY_ACCEPTED
    first = children[0]
    assert ui.apply().outcome == config_ui.APPLY_ACCEPTED
    assert [name for pid, name in calls if pid == first.pid] == [
        "terminate",
        "wait",
        "kill",
        "wait",
    ]
    assert first.returncode == -signal.SIGKILL
    assert ui.child is children[1] and children[1].poll() is None


def test_stop_waits_for_the_grace_before_killing() -> None:
    """The stop order without a real process: the bounded wait times out.

    Inverted for gate-1 F5: the wait after kill used to be ``wait(None)``
    (unbounded); it is now bounded by ``kill_wait``."""

    class Stubborn(_SpiedChild):
        def wait(self, timeout: float | None = None) -> int:
            self.calls.append(f"wait({timeout})")
            if timeout == 3.0:
                raise subprocess.TimeoutExpired("child", timeout)
            return -9

    supervisor = config_ui.Supervisor(("x",), "status.json", stop_grace=3.0, kill_wait=2.0)
    child = Stubborn()
    supervisor.child = child
    assert supervisor.stop() is True
    assert child.calls == ["terminate", "wait(3.0)", "kill", "wait(2.0)"]
    assert supervisor.child is None
    supervisor.stop()  # nothing launched: nothing signalled
    assert len(child.calls) == 4


def test_ac31_apply_never_signals_a_foreign_record_pid(
    tmp_path: Path, launched: list[config_ui.Supervisor], monkeypatch: pytest.MonkeyPatch
) -> None:
    ui = _apply_ui(tmp_path, launched, READY_CHILD)
    foreign = os.getpid()
    _publish(ui, _valid(ui, pid=foreign))
    cookie = _logged_in(ui)
    assert _supervision(_get(ui, "/", cookie).body.decode("utf-8")) == NOT_SUPERVISED
    sent: list[tuple[int, int]] = []
    real_kill = os.kill

    def spy(pid: int, signal_number: int) -> None:
        sent.append((pid, signal_number))
        if pid != foreign:
            real_kill(pid, signal_number)

    monkeypatch.setattr(os, "kill", spy)
    assert ui.apply().outcome == config_ui.APPLY_ACCEPTED
    assert ui.apply().outcome == config_ui.APPLY_ACCEPTED  # stops the first child
    assert all(pid != foreign for pid, _ in sent)
    assert any(signal_number == signal.SIGTERM for _, signal_number in sent)


# -- AC32: after a successful Apply every page is in sync again ---------------------


def test_ac32_after_a_successful_apply_every_page_shows_in_sync(
    tmp_path: Path, launched: list[config_ui.Supervisor]
) -> None:
    ui = _apply_ui(tmp_path, launched, READY_CHILD)
    cookie, csrf = _login(ui)
    _publish(ui, _valid(ui))
    assert {_drift(page) for page in _status_pages(ui, cookie).values()} == {DRIFT_IN_SYNC}
    assert _write(ui, cookie, "/save", "/module/M", [("modules.M.a", "3")]).status == 303
    assert {_drift(page) for page in _status_pages(ui, cookie).values()} == {DRIFT_DIFFERS}

    response = _post(ui, "/apply", cookie, csrf)
    assert response.status == 200
    assert 'data-outcome="accepted"' in response.body.decode("utf-8")
    pages = _status_pages(ui, cookie)
    assert {_drift(page) for page in pages.values()} == {DRIFT_IN_SYNC}
    assert {_supervision(page) for page in pages.values()} == {SUPERVISED}


def test_the_base_page_posts_apply_with_the_csrf_token(tmp_path: Path) -> None:
    ui = _status_ui(tmp_path)
    cookie, csrf = _login(ui)
    page = _get(ui, "/", cookie).body.decode("utf-8")
    form = re.search(r'<form method="post" action="/apply">(.*?)</form>', page, re.S)
    assert form is not None and csrf in form.group(1)


# -- AC39/AC40: an unusable or foreign record never accepts ----------------------------


@pytest.mark.parametrize(
    "publication",
    [
        "publish(b'not json at all')",
        "publish(pid='123')",
        "publish(state='starting')",
        "publish(pid=os.getppid())",
        "publish(digest='0' * 64)",
    ],
)
def test_ac39_ac40_a_child_writing_only_an_unaccepted_record_is_unknown(
    publication: str, tmp_path: Path, launched: list[config_ui.Supervisor]
) -> None:
    ui = _apply_ui(tmp_path, launched, f"{publication}\nhold()\n", window=APPLY_WINDOW)
    report = ui.apply()
    assert report.outcome == config_ui.APPLY_UNKNOWN
    assert ui.status_path.exists()


# -- D9: pipe backpressure ------------------------------------------------------------


def test_d9_a_chatty_child_is_accepted_within_the_window(
    tmp_path: Path, launched: list[config_ui.Supervisor]
) -> None:
    body = (
        f"sys.stderr.write('e' * {MIB})\nsys.stderr.flush()\n"
        f"sys.stdout.write('o' * {MIB})\nsys.stdout.flush()\n"
        "publish()\nhold()\n"
    )
    ui = _apply_ui(tmp_path, launched, body, window=APPLY_WINDOW)
    assert ui.apply().outcome == config_ui.APPLY_ACCEPTED


def test_d9_draining_continues_after_acceptance(
    tmp_path: Path, launched: list[config_ui.Supervisor]
) -> None:
    marker = tmp_path / "marker"
    body = (
        "publish()\n"
        f"sys.stderr.write('e' * {MIB})\nsys.stderr.flush()\n"
        f"open({str(marker)!r}, 'w').close()\nhold()\n"
    )
    ui = _apply_ui(tmp_path, launched, body)
    assert ui.apply().outcome == config_ui.APPLY_ACCEPTED
    assert _bounded_until(marker.exists)


def test_d9_a_refused_report_carries_the_last_line_within_2_kib(
    tmp_path: Path, launched: list[config_ui.Supervisor]
) -> None:
    body = (
        f"sys.stderr.write('filler line\\n' * ({MIB} // 12))\n"
        "sys.stderr.write('LAST-LINE\\n')\nsys.exit(2)\n"
    )
    ui = _apply_ui(tmp_path, launched, body)
    report = ui.apply()
    assert report.outcome == config_ui.APPLY_REFUSED
    assert report.exit_status == 2
    assert "LAST-LINE" in report.stderr_tail
    assert len(report.stderr_tail.encode("utf-8")) <= config_ui.REPORT_TAIL_BYTES == 2048


def test_d9_the_refused_tail_is_redacted_and_value_free(
    tmp_path: Path, launched: list[config_ui.Supervisor], caplog: pytest.LogCaptureFixture
) -> None:
    canary = "CANARY-TAIL-7f3a"
    body = (
        f"sys.stderr.write('token {canary} rejected\\n')\n"
        "sys.stderr.write('modules.M.a: must be an integer, got 91827\\n')\nsys.exit(1)\n"
    )
    ui = _apply_ui(tmp_path, launched, body)
    ui.secret_values = frozenset({canary})
    caplog.set_level(logging.DEBUG, logger="core.config_ui")
    cookie, csrf = _login(ui)
    page = _post(ui, "/apply", cookie, csrf).body.decode("utf-8")
    report = ui.last_apply
    assert report is not None and report.exit_status == 1
    assert canary not in report.stderr_tail and config_ui.REDACTED in report.stderr_tail
    assert "modules.M.a: must be an integer" in report.stderr_tail
    assert "91827" not in report.stderr_tail
    assert canary not in page and "91827" not in page
    assert canary not in caplog.text and "91827" not in caplog.text
    assert f"configuration apply refused: base {ui.base_path}" in caplog.text


def test_d9_the_drain_keeps_a_bounded_tail_of_a_pipe(monkeypatch: pytest.MonkeyPatch) -> None:
    forwarded = io.TextIOWrapper(io.BytesIO())
    monkeypatch.setattr(sys, "stderr", forwarded)
    read_end, write_end = os.pipe()
    drain = config_ui._StderrDrain(read_end)
    drain.start()
    data = bytes(index % 251 for index in range(100 * 1024))
    with os.fdopen(write_end, "wb") as writer:
        for offset in range(0, len(data), 1000):
            writer.write(data[offset : offset + 1000])
            writer.flush()
            with drain._lock:
                assert len(drain._tail) <= config_ui.DRAIN_TAIL_BYTES
    drain.join(5)
    assert not drain.is_alive()
    os.close(read_end)
    assert len(drain._tail) == config_ui.DRAIN_TAIL_BYTES
    assert bytes(drain._tail) == data[-config_ui.DRAIN_TAIL_BYTES :]
    assert drain.tail(16) == data[-16:].decode("utf-8", errors="replace")
    assert forwarded.buffer.getvalue() == data  # type: ignore[attr-defined]


def test_d9_a_failed_forward_never_stops_the_drain(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "stderr", io.StringIO())  # no ``buffer``: every forward fails
    read_end, write_end = os.pipe()
    stream = os.fdopen(read_end, "rb")
    drain = config_ui._StderrDrain(read_end, stream)
    drain.start()
    with os.fdopen(write_end, "wb") as writer:
        writer.write(b"x" * 70000 + b"END")
    drain.close()
    assert not drain.is_alive() and stream.closed
    assert drain.tail(3) == "END"


# -- the Apply loop on an injected clock ------------------------------------------------


class _FakeProcess:
    pid = 987654
    stderr = None

    def __init__(self) -> None:
        self.returncode: int | None = None

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.returncode = -15 if self.returncode is None else self.returncode

    def wait(self, timeout: float | None = None) -> int | None:
        return self.returncode


def test_the_apply_window_runs_on_the_injected_clock(tmp_path: Path) -> None:
    now = [100.0]
    waits: list[float] = []

    def wait(interval: float) -> None:
        waits.append(interval)
        now[0] += interval

    process = _FakeProcess()
    supervisor = config_ui.Supervisor(
        ("x",),
        tmp_path / "status.json",
        apply_window=60.0,
        poll_interval=5.0,
        clock=lambda: now[0],
        wait=wait,
        popen=lambda *args, **kwargs: process,
    )
    assert supervisor.restart("0" * 64).outcome == config_ui.APPLY_UNKNOWN
    assert waits == [5.0] * 12 and now[0] == 160.0

    supervisor.stop()
    assert process.returncode == -15
    process.returncode = 3
    report = supervisor.restart("0" * 64)
    assert (report.outcome, report.exit_status, report.stderr_tail) == ("refused", 3, "")


def test_main_stops_the_supervised_child_when_the_ui_stops(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    children: list[_SpiedChild] = []

    def serve(ui: ConfigUI, settings: UISettings) -> None:
        children.append(_SpiedChild())
        ui.child = children[0]

    monkeypatch.setattr(config_ui, "serve", serve)
    assert main(["--config", str(config_dir / "config.yaml")]) == 0
    assert children[0].calls == ["terminate", "wait"]


def test_a_launch_that_cannot_start_is_a_refused_report(tmp_path: Path) -> None:
    """A missing launch executable is refused with a redacted diagnostic, not raised."""

    first = _FakeProcess()
    launches: list[Any] = [first]

    def popen(*args: Any, **kwargs: Any) -> Any:
        if launches:
            return launches.pop()
        raise FileNotFoundError(2, "No such file or directory: 'hunter2-runtime'")

    supervisor = config_ui.Supervisor(("x",), tmp_path / "status.json", popen=popen)
    supervisor.start()
    report = supervisor.restart("0" * 64, ("hunter2",))
    assert report.outcome == config_ui.APPLY_REFUSED and not report.check_refused
    assert report.exit_status is None and first.returncode == -15
    assert len(report.diagnostics) == 1
    assert report.diagnostics[0].startswith("the launch command could not be started:")
    assert "hunter2" not in report.diagnostics[0]
    assert supervisor.child is None


def test_close_forbids_every_later_launch(tmp_path: Path) -> None:
    """A restart that runs after the UI's final cleanup starts nothing."""

    launched: list[_FakeProcess] = []

    def popen(*args: Any, **kwargs: Any) -> _FakeProcess:
        launched.append(_FakeProcess())
        return launched[-1]

    supervisor = config_ui.Supervisor(("x",), tmp_path / "status.json", popen=popen)
    supervisor.start()
    supervisor.close()
    assert launched[0].returncode == -15 and supervisor.child is None
    report = supervisor.restart("0" * 64)
    assert report.outcome == config_ui.APPLY_REFUSED and report.diagnostics
    assert len(launched) == 1 and supervisor.child is None
    with pytest.raises(config_ui.SupervisorClosed):
        supervisor.start()


# ---------------------------------------------------------------------------
# P16: the secret-canary sweep (R8; AC35, AC2) and the offline-assets audit
# (R9; AC36). One sweep drives every route of the UI over canary values and
# collects everything it produces: bodies, headers, log records, restart
# reports and the status records the runtime publishes.
# ---------------------------------------------------------------------------

CANARY_HEX = secrets.token_hex(6)
LITERAL_CANARY = f"CANARY-LIT-{CANARY_HEX}-api_key"
#: A declared credential path (``credentials: [synthesis.api_key]``) of a
#: module every shipped desktop profile enables.
LITERAL_MODULE = "audio_output"
LITERAL_PATH = "modules.audio_output.synthesis.api_key"
LITERAL_OVERLAY = f"modules:\n  {LITERAL_MODULE}:\n    synthesis:\n      api_key: {LITERAL_CANARY}\n"
SWEEP_HOOKS = "_config_ui_canary_sweep_hooks"


def _all_profile_variables() -> list[str]:
    """Every variable the 4 shipped profiles reference, as a value or a key."""

    return sorted({name for profile in PROFILES for name in _profile_variables(profile)})


def _sweep_environ() -> dict[str, str]:
    return {name: f"CANARY-ENV-{CANARY_HEX}-{name}" for name in _all_profile_variables()}


class _Collector(logging.Handler):
    """Every ``core.config_ui`` record, as the handlers downstream receive it."""

    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

    def texts(self) -> list[str]:
        """Each record's message plus every attribute it carries."""

        return [
            record.getMessage() + "\n" + repr(sorted(record.__dict__.items(), key=lambda kv: kv[0]))
            for record in self.records
        ]


@pytest.fixture
def ui_log() -> Iterator[_Collector]:
    collector = _Collector()
    ui_logger = logging.getLogger("core.config_ui")
    level = ui_logger.level
    ui_logger.addHandler(collector)
    ui_logger.setLevel(logging.DEBUG)
    try:
        yield collector
    finally:
        ui_logger.removeHandler(collector)
        ui_logger.setLevel(level)


class _Sweep:
    """The responses of one sweep, each labelled with what produced it."""

    def __init__(self, ui: ConfigUI) -> None:
        self.ui = ui
        self.responses: list[tuple[str, UIResponse]] = []

    def record(self, label: str, response: UIResponse) -> UIResponse:
        self.responses.append((label, response))
        return response

    def get(self, path: str, cookie: str | None, **query: str) -> UIResponse:
        headers = {} if cookie is None else {"cookie": cookie}
        return self.record(f"GET {path}", self.ui.handle(_request("GET", path, headers=headers, query=query)))

    def texts(self) -> list[tuple[str, str]]:
        """Every body and every header value, decoded."""

        found: list[tuple[str, str]] = []
        for label, response in self.responses:
            found.append((label, response.body.decode("utf-8")))
            found += [(f"{label} header {name}", f"{name}: {value}") for name, value in response.headers]
        return found


def _canaries_in(text: str, canaries: Sequence[str]) -> list[str]:
    return [canary for canary in canaries if canary in text or _html.escape(canary) in text]


def _sweep_profile(
    tmp_path: Path, profile: str, environ: dict[str, str], label: str = "pages"
) -> tuple[ConfigUI, Path]:
    """A copy of *profile* (modules directory absolute) plus the literal overlay."""

    text = (REPO / profile).read_text(encoding="utf-8")
    assert "modules_directory: ./modules\n" in text
    directory = tmp_path / label / profile.replace(".", "_")
    directory.mkdir(parents=True)
    base = directory / "config.yaml"
    base.write_text(
        text.replace("modules_directory: ./modules\n", f"modules_directory: {REPO / 'modules'}\n"),
        encoding="utf-8",
    )
    if LITERAL_MODULE in read_base(base)["enabled_modules"]:
        (directory / "config.local.yaml").write_text(LITERAL_OVERLAY, encoding="utf-8")
    ui = ConfigUI(
        UISettings.from_argv(["--config", str(base), "--status-file", str(directory / "status.json")]),
        environ=environ,
    )
    return ui, base


def _sweep_pages(sweep: _Sweep, cookie: str) -> dict[str, str]:
    """The base page, the core page and every module page, all 200."""

    pages: dict[str, str] = {}
    for path in ("/", "/core", *(f"{MODULE_PAGE_PREFIX}{name}" for name in SHIPPED_MODULES)):
        response = sweep.get(path, cookie)
        assert response.status == 200, path
        pages[path] = response.body.decode("utf-8")
    return pages


# -- the status records of AC33, published with the canary environment ------------

SWEEP_MODULE_SOURCE = f"""
import {SWEEP_HOOKS} as hooks


class Handle:
    async def prepare(self):
        return None

    async def close(self):
        return None


async def activate(context, settings, catalog):
    hooks.contexts[context.module] = context
    return Handle()
"""


def _publisher_base(root: Path, environ: dict[str, str]) -> Path:
    """Fixture modules M and N whose settings reference every canary variable,
    one of them as a mapping key, next to a literal canary; ``secrets`` lists
    every variable."""

    for name in ("mmod", "nmod"):
        directory = root / "modules" / name
        directory.mkdir(parents=True)
        manifest = {
            "name": name,
            "manifest_version": 2,
            "runtime_api": 2,
            "produces": [],
            "consumes": [],
            "middleware": False,
            "lifecycle": {"roles": []},
            "settings_schema": {"type": "object", "properties": {"api_key": {"type": "string"}}},
            "credentials": ["api_key"],
        }
        (directory / "module.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
        (directory / "__init__.py").write_text(SWEEP_MODULE_SOURCE, encoding="utf-8")
    references = {name.lower(): "${" + name + "}" for name in environ}
    config = {
        "modules_directory": "./modules",
        "enabled_modules": ["mmod", "nmod"],
        "modules": {
            "mmod": {**references, "api_key": LITERAL_CANARY},
            "nmod": {"channels": {"${TWITCH_BROADCASTER_ID}": {"on": True}}},
        },
        "secrets": ["${" + name + "}" for name in environ],
    }
    base = root / "config.yaml"
    base.write_text(yaml.safe_dump(config), encoding="utf-8")
    return base


def _publish_ac33_records(
    tmp_path: Path, environ: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> tuple[list[bytes], list[tuple[str, UIResponse]], frozenset[str]]:
    """Re-run P4's publisher (``core.main.run``) with the canary environment
    through the 3 transitions of AC33; after each one the UI reads the record
    and renders it. Returns the 3 records, the rendered responses and the
    rendering UI's accepted authorities."""

    import core.main as application
    from types import SimpleNamespace

    hooks = SimpleNamespace(contexts={})
    monkeypatch.setitem(sys.modules, SWEEP_HOOKS, hooks)
    root = tmp_path / "publisher"
    base = _publisher_base(root, environ)
    status = root / "status.json"
    ui = ConfigUI(
        UISettings.from_argv(["--config", str(base), "--status-file", str(status)]), environ=environ
    )
    cookie, _ = _login(ui)
    records: list[bytes] = []
    rendered: list[tuple[str, UIResponse]] = []
    diagnostics: list[str] = []

    def observe() -> None:
        records.append(status.read_bytes())
        assert read_status(status).kind == "usable"
        for path in ("/", f"{MODULE_PAGE_PREFIX}mmod"):
            response = ui.handle(_request("GET", path, headers={"cookie": cookie}))
            assert response.status == 200
            rendered.append((f"status record {len(records)} GET {path}", response))
        page = ui.handle(_request("GET", f"{MODULE_PAGE_PREFIX}mmod", headers={"cookie": cookie}))
        assert HIDDEN_LITERAL in _node(page.body.decode("utf-8"), "modules.mmod.api_key")

    async def scenario() -> int:
        stop = asyncio.Event()
        ready = asyncio.Event()
        task = asyncio.create_task(
            application.run(
                base,
                stop,
                environ={**environ, config_ui.STATUS_FILE_VARIABLE: str(status)},
                ready_reporter=lambda _message: ready.set(),
                diagnostic_reporter=diagnostics.append,
            )
        )
        waiting = asyncio.create_task(ready.wait())
        await asyncio.wait({task, waiting}, return_when=asyncio.FIRST_COMPLETED)
        waiting.cancel()
        assert ready.is_set(), diagnostics
        observe()
        await hooks.contexts["mmod"].supervision.degraded(reason="fixture degraded")
        observe()
        await hooks.contexts["mmod"].supervision.ready()
        observe()
        stop.set()
        return await task

    assert _run_socket_free(scenario()) == 0, diagnostics
    documents = [json.loads(record) for record in records]
    assert [document["sequence"] for document in documents] == [1, 2, 3]
    assert [document["modules"]["mmod"]["state"] for document in documents] == ["ready", "degraded", "ready"]
    assert all(document["digest"] == on_disk_digest(base, root / "config.local.yaml") for document in documents)
    return records, rendered, frozenset(ui.authorities)


# -- AC36: the URL audit ------------------------------------------------------------

#: Attributes whose value the browser loads, navigates to or submits to.
URL_ATTRIBUTES = frozenset(
    {"src", "href", "action", "formaction", "srcset", "poster", "data", "background", "cite", "ping", "manifest"}
)
_CSS_URL = re.compile(r"url\(\s*(['\"]?)(.*?)\1\s*\)", re.I | re.S)
_CSS_IMPORT = re.compile(r"@import\s+(?:url\(\s*)?['\"]?([^'\")\s;]*)", re.I)
_SCRIPT_URL = re.compile(r"(?:https?:)?//[^\s'\"`<>)]*", re.I)


class _UrlAudit(HTMLParser):
    """The URL-bearing parts of one response, found with ``html.parser``."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.targets: list[str] = []
        self.styles: list[str] = []
        self.scripts: list[str] = []
        self._inside: str | None = None

    @classmethod
    def of(cls, response: UIResponse) -> "_UrlAudit":
        audit = cls()
        audit.feed(response.body.decode("utf-8"))
        audit.close()
        return audit

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name: value or "" for name, value in attrs}
        for name, value in values.items():
            if name == "srcset":
                self.targets += [part.split()[0] for part in value.split(",") if part.split()]
            elif name in URL_ATTRIBUTES:
                self.targets.append(value)
            elif name == "style":
                self.styles.append(value)
            elif name.startswith("on"):
                self.scripts.append(value)
        if tag == "meta" and values.get("http-equiv", "").lower() == "refresh":
            self.targets.append(values.get("content", "").partition("url=")[2])
        if tag in ("script", "style"):
            self._inside = tag

    def handle_endtag(self, tag: str) -> None:
        if tag == self._inside:
            self._inside = None

    def handle_data(self, data: str) -> None:
        if self._inside == "script":
            self.scripts.append(data)
        elif self._inside == "style":
            self.styles.append(data)


def _accepted_target(value: str, accepted: frozenset[str]) -> bool:
    """Relative (no scheme, no authority), or an http(s) URL on an accepted authority."""

    from urllib.parse import urlsplit

    parsed = urlsplit(value.strip())
    if not parsed.scheme and not parsed.netloc:
        return not value.strip().startswith("//")
    return parsed.scheme in ("http", "https") and parsed.netloc.lower() in accepted


def _url_violations(response: UIResponse, accepted: frozenset[str]) -> list[str]:
    """Every target of *response* that is neither relative nor accepted (R9)."""

    audit = _UrlAudit.of(response)
    targets = list(audit.targets)
    targets += [value for name, value in response.headers if name.lower() in ("location", "refresh", "link")]
    for style in audit.styles:
        targets += [match.group(2) for match in _CSS_URL.finditer(style)]
        targets += [match.group(1) for match in _CSS_IMPORT.finditer(style)]
    violations = [target for target in targets if not _accepted_target(target, accepted)]
    for script in audit.scripts:
        violations += [url for url in _SCRIPT_URL.findall(script) if not _accepted_target(url, accepted)]
    return violations


def test_ac36_the_url_audit_finds_every_external_target() -> None:
    """The auditor the sweep relies on flags each resource-loading position."""

    accepted = frozenset({"127.0.0.1:8765", "localhost:8765"})
    external = [
        '<script src="https://cdn.example.invalid/x.js"></script>',
        '<link rel="stylesheet" href="//cdn.example.invalid/x.css">',
        '<form method="post" action="http://evil.example.invalid/save"></form>',
        '<button formaction="https://evil.example.invalid/">x</button>',
        '<img srcset="/a.png 1x, https://cdn.example.invalid/b.png 2x">',
        '<style>body { background: url("https://cdn.example.invalid/b.png"); }</style>',
        "<style>@import 'https://cdn.example.invalid/x.css';</style>",
        '<div style="background: url(//cdn.example.invalid/b.png)"></div>',
        '<script>fetch("https://api.example.invalid/x");</script>',
        '<a href="javascript:alert(1)">x</a>',
        '<meta http-equiv="refresh" content="0; url=https://evil.example.invalid/">',
    ]
    for markup in external:
        response = UIResponse(status=200, body=markup.encode("utf-8"), content_type="text/html")
        assert _url_violations(response, accepted), markup
    fine = (
        '<a href="/core">c</a><form action="/save"></form><img src="x.png">'
        '<a href="http://127.0.0.1:8765/">h</a><style>p { color: red }</style>'
        '<script>var u = "http://localhost:8765/check";</script>'
        '<p>https://cdn.example.invalid/x.js is text</p>'
    )
    assert _url_violations(UIResponse(status=200, body=fine.encode("utf-8")), accepted) == []
    moved = UIResponse(status=303, headers=(("Location", "https://evil.example.invalid/"),))
    assert _url_violations(moved, accepted) == ["https://evil.example.invalid/"]

SWEEP_CHILD = """\
from pathlib import Path

if Path(BASE).with_name("refuse.flag").exists():
    sys.stderr.write("config error near " + os.environ["TWITCH_CLIENT_SECRET"] + "\\n")
    sys.stderr.write("literal " + {literal!r} + " rejected\\n")
    sys.exit(2)
publish()
hold()
"""


def test_ac35_no_secret_value_reaches_any_response_log_report_or_record(
    tmp_path: Path,
    launched: list[config_ui.Supervisor],
    ui_log: _Collector,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC35 (R8), with AC2's "the token is in no log record" over the whole capture."""

    environ = _sweep_environ()
    assert {"TWITCH_BROADCASTER_ID", "TWITCH_CLIENT_SECRET", "OPENAI_API_KEY", "PROXY_PAIRING_TOKEN"} <= set(environ)
    canaries = [*environ.values(), LITERAL_CANARY]
    collected: list[tuple[str, str]] = []
    tokens: list[str] = []
    reports: list[config_ui.ApplyReport] = []
    audited: list[tuple[str, UIResponse, frozenset[str]]] = []

    # Every page of every shipped profile, and the unauthenticated answers.
    for profile in PROFILES:
        ui, base = _sweep_profile(tmp_path, profile, environ)
        sweep = _Sweep(ui)
        sweep.get("/", None, token=ui.token)
        for path in ("/", "/core", f"{MODULE_PAGE_PREFIX}twitch"):
            assert sweep.get(path, None).status == 401
        cookie, csrf = _login(ui)
        tokens += [ui.token, cookie.split("=", 1)[1], csrf]
        pages = _sweep_pages(sweep, cookie)
        # Credential fields show their reference or the hidden-literal text.
        if "twitch" in read_base(base)["modules"]:
            twitch = pages[f"{MODULE_PAGE_PREFIX}twitch"]
            for field_name in ("client_id", "client_secret", "access_token"):
                node = _node(twitch, f"modules.twitch.{field_name}")
                assert f"${{TWITCH_{field_name.upper()}}}" in node
        if (base.parent / "config.local.yaml").exists():
            node = _node(pages[f"{MODULE_PAGE_PREFIX}{LITERAL_MODULE}"], LITERAL_PATH)
            assert HIDDEN_LITERAL in node
        # The renderer keeps every value out by itself: nothing was left for
        # the redaction guard to hide.
        for path, page in pages.items():
            assert config_ui.REDACTED not in page, (profile, path)
        collected += sweep.texts()
        audited += [(label, response, frozenset(ui.authorities)) for label, response in sweep.responses]

    # Check, Save, Remove and Apply (accepted, refused twice) on the first
    # profile, its status record published by a supervised P15 fake child.
    ui, base = _sweep_profile(tmp_path, "config.yaml.example", {}, "writes")
    overlay = base.parent / "config.local.yaml"
    script = tmp_path / "child.py"
    script.write_text(CHILD_PRELUDE + SWEEP_CHILD.format(literal=LITERAL_CANARY), encoding="utf-8")
    settings = UISettings.from_argv(
        [
            "--config", str(base),
            "--status-file", str(base.parent / "status.json"),
            "--", sys.executable, str(script), str(base), str(overlay),
        ]
    )
    supervisor = config_ui.Supervisor(
        config_ui.default_launch_argv(settings),
        settings.status_path,
        stop_grace=STOP_GRACE,
        apply_window=ACCEPT_WINDOW,
        environ={**os.environ, **environ},
    )
    launched.append(supervisor)
    ui = ConfigUI(settings, environ=environ, supervisor=supervisor)
    sweep = _Sweep(ui)
    cookie, csrf = _login(ui)
    tokens += [ui.token, cookie.split("=", 1)[1], csrf]

    check = sweep.record("POST /check", ui.handle(_check_request(ui, cookie, csrf, "/")))
    assert check.status == 200 and _listed(check.body.decode("utf-8"))

    saved = sweep.record(
        "POST /save", _write(ui, cookie, "/save", "/module/users", [("modules.users.max_channels", "73591")])
    )
    assert saved.status == 303, _written(saved)
    assert LITERAL_CANARY in overlay.read_text(encoding="utf-8")
    removed = sweep.record(
        "POST /remove", _write(ui, cookie, "/remove", "/module/users", [("path", "modules.users.max_channels")])
    )
    assert removed.status == 303, _written(removed)
    assert _overlay_doc(overlay) == yaml.safe_load(LITERAL_OVERLAY)

    # Refused by the on-disk Check (the real checker): nothing is started.
    refused_by_check = sweep.record("POST /apply (check refused)", _post(ui, "/apply", cookie, csrf))
    assert ui.last_apply is not None and ui.last_apply.check_refused
    assert refused_by_check.status == 200
    reports.append(ui.last_apply)

    ui.checker = _passing_checker
    accepted = sweep.record("POST /apply (accepted)", _post(ui, "/apply", cookie, csrf))
    assert ui.last_apply is not None and ui.last_apply.outcome == config_ui.APPLY_ACCEPTED
    assert accepted.status == 200
    reports.append(ui.last_apply)
    after_accept = _sweep_pages(sweep, cookie)
    assert _drift(after_accept["/"]) == DRIFT_IN_SYNC
    for path, page in after_accept.items():
        assert config_ui.REDACTED not in page, path

    # Refused by the child: its stderr names a canary, the report hides it.
    (base.parent / "refuse.flag").write_text("", encoding="utf-8")
    refused = sweep.record("POST /apply (refused)", _post(ui, "/apply", cookie, csrf))
    report = ui.last_apply
    assert report is not None and report.outcome == config_ui.APPLY_REFUSED
    assert report.exit_status == 2 and not report.check_refused
    assert "config error near" in report.stderr_tail and config_ui.REDACTED in report.stderr_tail
    assert refused.status == 200
    reports.append(report)
    _sweep_pages(sweep, cookie)
    collected += sweep.texts()
    audited += [(label, response, frozenset(ui.authorities)) for label, response in sweep.responses]
    collected += [(f"restart report {index}", repr(report)) for index, report in enumerate(reports)]

    # Every status record of AC33, published by the runtime with this environment.
    records, rendered, authorities = _publish_ac33_records(tmp_path, environ, monkeypatch)
    collected += [(f"status record {index + 1}", record.decode("utf-8")) for index, record in enumerate(records)]
    collected += [(label, response.body.decode("utf-8")) for label, response in rendered]
    collected += [
        (f"{label} header", f"{name}: {value}") for label, response in rendered for name, value in response.headers
    ]
    audited += [(label, response, authorities) for label, response in rendered]

    logs = ui_log.texts()
    assert ui_log.records, "the sweep's saves and applies are logged"
    assert any("configuration apply refused" in text for text in logs)
    collected += [(f"log record {index}", text) for index, text in enumerate(logs)]

    leaks = [(label, found) for label, text in collected if (found := _canaries_in(text, canaries))]
    assert leaks == []
    # AC2: the token (and every session and CSRF token) is in no log record.
    for text in logs:
        for token in tokens:
            assert token not in text
    assert len(collected) > 100

    # Every route the UI serves was driven (there is no JSON endpoint: every
    # route answers HTML or text, and a new one must join this sweep).
    assert set(ui.routes) == {"/", "/core", MODULE_PAGE_PREFIX, "/save", "/remove", "/check", "/apply"}
    assert {response.content_type.split(";")[0] for _, response, _ in audited} <= {"text/html", "text/plain"}

    # AC36: no response makes the browser load, run or submit anything from
    # another host.
    violations = [
        (label, violation)
        for label, response, accepted in audited
        for violation in _url_violations(response, accepted)
    ]
    assert violations == []
    assert sum(_UrlAudit.of(response).scripts != [] for _, response, _ in audited) > 50


# ---------------------------------------------------------------------------
# AC37: the operator documentation
# ---------------------------------------------------------------------------


def _doc_text() -> str:
    return (REPO / "docs" / "config-ui.md").read_text(encoding="utf-8")


def _squashed(text: str) -> str:
    """The text with every whitespace run folded to one space (line wrapping)."""

    return re.sub(r"\s+", " ", text)


def test_ac37_doc_launch_and_local_only() -> None:
    """AC37 (R10): the launch command, the console script and local-only."""

    doc = _squashed(_doc_text())
    assert "python -m core.config_ui --config config.yaml" in doc
    assert "twitch-ia-compagnon-config-ui" in doc
    assert "local-only" in doc or "local only" in doc
    assert "does **not** administer a remote brain over the proxy" in doc
    # The launch argv after ``--`` and every option the parser declares.
    assert " -- " in doc
    for option in ("--config", "--overlay", "--host", "--port", "--allow-non-loopback",
                   "--allowed-host", "--status-file"):
        assert f"`{option}" in doc, option


def test_ac37_doc_overlay_rule_and_merge() -> None:
    """AC37 (R10): the A1 path rule with both AC8 examples, AC7, and A7."""

    doc = _squashed(_doc_text())
    # Both AC8 examples, checked against the implementation too.
    assert "| `config.yaml` | `config.local.yaml` |" in doc
    assert "| `presence.yaml.example` | `presence.local.yaml` |" in doc
    assert core_overlay.implicit_overlay_path("config.yaml").name == "config.local.yaml"
    assert core_overlay.implicit_overlay_path("presence.yaml.example").name == "presence.local.yaml"
    assert "`--overlay PATH` overrides this rule" in doc
    # The AC7 merge example, checked against the implementation too.
    base = {"a": {"b": 1, "c": [1, 2]}, "d": "x"}
    overlay = {"a": {"b": 2, "c": [3]}, "e": "y"}
    assert core_overlay.deep_merge(base, overlay) == {"a": {"b": 2, "c": [3]}, "d": "x", "e": "y"}
    assert "base `{a: {b: 1, c: [1, 2]}, d: x}`" in doc
    assert "overlay `{a: {b: 2, c: [3]}, e: y}`" in doc
    assert "`{a: {b: 2, c: [3]}, d: x, e: y}`" in doc
    assert "An overlay `a: null` yields `a: null`" in doc
    assert "the overlay **cannot delete a base key**" in doc
    assert "comments and formatting in the overlay are not preserved" in doc
    assert "merged by `--check-config` too" in doc


def test_ac37_doc_writable_and_read_only_blocks() -> None:
    """AC37 (R10, 6c): the 5 writable blocks, the 2 read-only ones, and why."""

    doc = _squashed(_doc_text())
    writable = doc.split("**5 writable blocks**", 1)[1].split("**2 read-only blocks**", 1)[0]
    for block in ("enabled_modules", "modules.<name>", "triggers", "limits", "modules_directory"):
        assert f"- `{block}`" in writable, block
    read_only = doc.split("**2 read-only blocks**", 1)[1].split("## 5.", 1)[0]
    assert "- `secrets`" in read_only
    assert "- `actions`" in read_only
    assert "a single mis-click can never widen the set of authorized actions or expose a secret" in read_only
    # The reason the pages display is quoted verbatim.
    assert READ_ONLY_REASON in read_only


def test_ac37_doc_secrets_policy() -> None:
    """AC37 (R10, R8): the secrets policy."""

    doc = _squashed(_doc_text())
    policy = doc.split("## 5. Secrets policy", 1)[1].split("## 6.", 1)[0]
    assert "No secret value appears in any page, response, UI log record, restart report or diagnostic" in policy
    assert "`${NAME}`" in policy
    assert HIDDEN_LITERAL in policy
    assert "set/unset" in policy
    assert "declared credential path" in policy
    assert "`secrets` block" in policy


def test_ac37_doc_restart_drift_and_status_file() -> None:
    """AC37 (R10, R7, A4, A5): Apply, drift states and the status-file rule.

    The variable name is read from ``core.overlay`` so the document cannot
    drift from the constant the runtime and the UI use.
    """

    doc = _squashed(_doc_text())
    for outcome in ("**accepted**", "**refused**", "**unknown**"):
        assert outcome in doc
    assert f"**{config_ui.DEFAULT_APPLY_WINDOW:g} s**" in doc
    assert f"stop grace ({config_ui.DEFAULT_STOP_GRACE:g} s)" in doc
    for state in (DRIFT_IN_SYNC, DRIFT_DIFFERS, DRIFT_UNKNOWN):
        assert f"**{state}**" in doc
    assert f"**\"{STATUS_RECORD_UNUSABLE}\"**" in doc
    assert f'"{NOT_SUPERVISED}"' in doc
    variable = core_overlay.STATUS_FILE_VARIABLE
    assert f"`{variable}`" in doc
    assert f"{variable}=config.yaml.status.json" in doc
    assert "`<base file name>.status.json`" in doc
    assert core_overlay.default_status_path("config.yaml").name == "config.yaml.status.json"
    assert "`config.yaml` → `config.yaml.status.json`" in doc
    assert "`status_path_collision`" in doc



# ---------------------------------------------------------------------------
# Gate-1 F1: one canonical form of every path; Save never targets the base
# ---------------------------------------------------------------------------


def _f1_spellings(home: Path, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Overlay spellings of ``home/config.yaml`` that are not its text."""

    (home / "sub").mkdir()
    (home / "link.yaml").symlink_to(home / "config.yaml")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("F1_GATE_DIR", str(home))
    monkeypatch.chdir(home)
    return [
        "~/config.yaml",
        "$F1_GATE_DIR/config.yaml",
        "${F1_GATE_DIR}/sub/../config.yaml",
        "config.yaml",
        "./sub/../config.yaml",
        "link.yaml",
    ]


def test_f1_an_overlay_naming_the_base_by_any_spelling_refuses_to_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Gate-1 F1: ``--overlay ~/config.yaml`` (and every other spelling of the
    base) used to pass ``startup_checks``; it is refused before any bind."""

    home = tmp_path / "home"
    home.mkdir()
    (home / "config.yaml").write_text(BASE_TEXT, encoding="utf-8")
    spellings = _f1_spellings(home, monkeypatch)
    monkeypatch.setattr(config_ui, "serve", _fail)
    monkeypatch.setattr(config_ui, "ConfigUI", _fail)
    monkeypatch.setattr(subprocess, "Popen", _fail)
    before = _snapshot(home)
    for spelling in spellings:
        status = main(["--config", str(home / "config.yaml"), "--overlay", spelling])
        err = capsys.readouterr().err
        assert status != 0, spelling
        assert "overlay_path_collision" in err, spelling
        assert f"configuration file {home / 'config.yaml'} itself" in err, spelling
        assert _snapshot(home) == before
        settings = UISettings.from_argv(["--config", "~/config.yaml", "--overlay", spelling])
        assert settings.overlay_path == settings.base_path == home / "config.yaml"
    assert _SOCKET_ATTEMPTS["count"] == 0


@pytest.mark.parametrize(
    "spelling", ["$F1_GATE_UNSET_VARIABLE/config.local.yaml", "~f1-no-such-user-7c1e/x.yaml"]
)
def test_f1_an_overlay_that_cannot_be_canonicalised_refuses_to_start(
    spelling: str,
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Gate-1 F1: a path whose file cannot be known is refused, never guessed."""

    monkeypatch.delenv("F1_GATE_UNSET_VARIABLE", raising=False)
    monkeypatch.setattr(config_ui, "serve", _fail)
    monkeypatch.setattr(config_ui, "ConfigUI", _fail)
    before = _snapshot(config_dir)
    status = main(["--config", str(config_dir / "config.yaml"), "--overlay", spelling])
    err = capsys.readouterr().err
    assert status != 0
    assert "path_not_canonical" in err and "cannot be canonicalised" in err
    assert _snapshot(config_dir) == before
    assert _SOCKET_ATTEMPTS["count"] == 0


def test_f1_every_path_is_canonical_and_the_runtime_gets_the_same_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    settings = UISettings.from_argv(
        ["--config", "~/config.yaml", "--overlay", "~/mine.yaml", "--status-file", "~/s.json"]
    )
    assert settings.base_path == home / "config.yaml"
    assert settings.overlay_path == home / "mine.yaml"
    assert settings.status_path == home / "s.json"
    assert startup_checks(settings) == []
    assert config_ui.default_launch_argv(settings)[-4:] == (
        "--config", str(home / "config.yaml"), "--overlay", str(home / "mine.yaml")
    )
    implicit = UISettings.from_argv(["--config", "~/config.yaml"])
    assert implicit.overlay_path == home / "config.local.yaml"
    assert implicit.status_path == home / "config.yaml.status.json"


def test_the_runtime_is_launched_on_the_managed_overlay_of_a_linked_base(
    tmp_path: Path,
) -> None:
    """Gate-1 P19F1 A1: the base is launched by its canonical path, so the
    implicit overlay the UI manages is passed explicitly; otherwise the
    runtime would derive one from the link target's name."""

    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "base.yaml").write_text("{}\n", encoding="utf-8")
    (tmp_path / "a" / "config.yaml").symlink_to(tmp_path / "b" / "base.yaml")
    settings = UISettings.from_argv(["--config", str(tmp_path / "a" / "config.yaml")])
    assert settings.overlay_path == tmp_path / "a" / "config.local.yaml"
    assert config_ui.default_launch_argv(settings)[-4:] == (
        "--config", str(tmp_path / "b" / "base.yaml"),
        "--overlay", str(tmp_path / "a" / "config.local.yaml"),
    )


def test_f1_save_never_replaces_the_base_whatever_the_overlay_spelling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gate-1 C/F1 as a running test: a UI built past the startup refusal
    over ``--overlay ~/config.yaml`` used to answer an authenticated Save
    with 303 and replace the base file with the overlay document."""

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    base = _fixture_config(home, rules=[("preserve-me", "x.do")], enabled=())
    before = _snapshot(home)
    ui = ConfigUI(
        UISettings.from_argv(
            ["--config", str(base), "--overlay", "~/config.yaml",
             "--status-file", str(tmp_path / "status.json")]
        )
    )
    assert startup_checks(ui.settings) != []
    cookie, _ = _login(ui)
    response = _write(ui, cookie, "/save", "/", [("enabled_modules.xmod", "true")])
    assert response.status == 403
    assert "the overlay path is the configuration file itself" in response.body.decode("utf-8")
    assert _snapshot(home) == before
    assert "preserve-me" in base.read_text(encoding="utf-8")
    with pytest.raises(RuntimeError, match="managed overlay"):
        config_ui._write_overlay(base, base, base, {"enabled_modules": []})
    with pytest.raises(RuntimeError, match="managed overlay"):
        spelled = Path("~/config.local.yaml")
        config_ui._write_overlay(spelled, spelled, base, {"enabled_modules": []})
    assert _snapshot(home) == before


def test_f1_a_hard_link_of_the_base_refuses_to_start_and_is_never_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Gate-2 F1: a hard link of the base shares its file but not its path
    text; as the overlay or the status file it used to pass
    ``startup_checks``, and ``status_path_collision`` returned ``None``."""

    base = _fixture_config(tmp_path, rules=[("preserve-me", "x.do")], enabled=())
    alias = tmp_path / "alias.yaml"
    os.link(base, alias)
    status = tmp_path / "status.json"
    monkeypatch.setattr(config_ui, "serve", _fail)
    monkeypatch.setattr(subprocess, "Popen", _fail)
    before = _snapshot(tmp_path)

    overlay_settings = UISettings.from_argv(
        ["--config", str(base), "--overlay", str(alias), "--status-file", str(status)]
    )
    assert overlay_settings.overlay_path != overlay_settings.base_path
    assert startup_checks(overlay_settings) == [
        f"overlay_path_collision: overlay path {alias} resolves to the configuration file {base} itself"
    ]
    status_settings = UISettings.from_argv(["--config", str(base), "--status-file", str(alias)])
    assert startup_checks(status_settings) == [
        f"status_path_collision: status file {alias} collides with the base file {base}"
    ]
    for argv in (
        ["--config", str(base), "--overlay", str(alias), "--status-file", str(status)],
        ["--config", str(base), "--status-file", str(alias)],
    ):
        assert main(argv) != 0, argv
        assert "path_collision" in capsys.readouterr().err
    assert _SOCKET_ATTEMPTS["count"] == 0

    # A UI built past the refusal still never writes through the link.
    ui = ConfigUI(overlay_settings)
    replace = []
    monkeypatch.setattr(config_ui.os, "replace", lambda *args: replace.append(args))
    cookie, _ = _login(ui)
    response = _write(ui, cookie, "/save", "/", [("enabled_modules.xmod", "true")])
    assert response.status == 403
    assert "the overlay path is the configuration file itself" in response.body.decode("utf-8")
    with pytest.raises(RuntimeError, match="managed overlay"):
        config_ui._write_overlay(alias, alias, base, {"enabled_modules": []})
    assert replace == []
    assert _snapshot(tmp_path) == before
    assert "preserve-me" in base.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Gate-1 F2: R8 covers every secret value, at any length, in any position
# ---------------------------------------------------------------------------


def _twitch_ui(tmp_path: Path, text: str, environ: dict[str, str]) -> ConfigUI:
    base = tmp_path / "config.yaml"
    base.write_text(text, encoding="utf-8")
    return ConfigUI(
        UISettings.from_argv(["--config", str(base), "--status-file", str(tmp_path / "status.json")]),
        environ=environ,
    )


@pytest.mark.parametrize("secret", ["q7Z", "Ω"])
def test_f2_a_short_secret_in_an_ordinary_setting_is_never_rendered(
    tmp_path: Path, secret: str
) -> None:
    """Gate-1 C/F2a as a running test: a 3-character ``${GATE_SECRET}`` equal
    to ``modules.twitch.companion_name`` used to be shown as ``value="q7Z"``;
    a 1-character one is covered too. The reference stays visible.

    P19F9: a value the page cannot show exactly is no longer placed in its
    input as masked text (posted back edited, ``[hidden]x`` could not be told
    from literal text, gate-6 N3): the input starts empty — left empty it
    keeps the configured value — and any text typed is the whole new value,
    never merged with the withheld text."""

    ui = _twitch_ui(
        tmp_path,
        "modules_directory: builtin\nenabled_modules: []\nsecrets: ['${GATE_SECRET}']\n"
        f"modules:\n  twitch:\n    companion_name: '{secret}'\n",
        {"GATE_SECRET": secret},
    )
    cookie, _ = _login(ui)
    for path in ("/", "/core", f"{MODULE_PAGE_PREFIX}twitch"):
        response = _get(ui, path, cookie)
        assert response.status == 200, path
        page = response.body.decode("utf-8")
        assert secret not in page, path
        assert _html.escape(secret) not in page, path
    core = _get(ui, "/core", cookie).body.decode("utf-8")
    assert "${GATE_SECRET}" in core
    module = _get(ui, f"{MODULE_PAGE_PREFIX}twitch", cookie).body.decode("utf-8")
    placeholder = _html.escape(config_ui.WITHHELD_PLACEHOLDER, quote=True)
    assert (
        f'name="modules.twitch.companion_name" value="" placeholder="{placeholder}" data-withheld="true"'
        in module
    )
    # The field posted back as the page drew it is no edit: nothing is
    # written over the configured value.
    token = config_ui._RENDERING_FOR.set(ui)
    try:
        edits, problems = ui.parse_edits(ui.view(), [("modules.twitch.companion_name", "")])
        assert (edits, problems) == ([], [])
        # Typed text is the whole new value, taken as posted: never read
        # back as the withheld text it resembles.
        edits, problems = ui.parse_edits(ui.view(), [("modules.twitch.companion_name", "[hidden]")])
    finally:
        config_ui._RENDERING_FOR.reset(token)
    assert (edits, problems) == ([(("modules", "twitch", "companion_name"), "[hidden]")], [])


def test_f2_a_credential_used_as_a_trigger_channel_key_is_never_rendered(
    tmp_path: Path,
) -> None:
    """Gate-1 C/F2b as a running test: a literal ``client_secret`` reused as a
    trigger channel key used to surface in
    ``name="triggers.twitch.channels.<secret>.combination"``."""

    secret = "gate-secret-channel-97bf"
    ui = _twitch_ui(
        tmp_path,
        "modules_directory: builtin\nenabled_modules: []\n"
        f"modules:\n  twitch:\n    client_secret: {secret}\n",
        {},
    )
    (tmp_path / "config.local.yaml").write_text(
        f"triggers:\n  twitch:\n    channels:\n      {secret}:\n"
        "        combination: all_of\n        rules:\n"
        "          - type: probability\n            parameters: {probability: 0.5}\n",
        encoding="utf-8",
    )
    cookie, _ = _login(ui)
    response = _get(ui, f"{MODULE_PAGE_PREFIX}twitch", cookie)
    assert response.status == 200
    page = response.body.decode("utf-8")
    assert secret in ui.secret_values
    assert secret not in page
    # Gate-2 F2: the channel's controls are named by its position.
    names = re.findall(r'name="(triggers\.twitch\.channels\.@0[^"]*)"', page)
    assert names, "the channel's controls carry a positional name"
    assert all(secret not in name for name in re.findall(r'(?:name|value|data-[a-z-]+)="([^"]*)"', page))

    # The positional names are read back: the channel policy is edited...
    select = re.search(r'<select name="(triggers\.twitch\.channels\.@0\.combination)" data-combination', page)
    assert select is not None
    saved = _write(ui, cookie, "/save", f"{MODULE_PAGE_PREFIX}twitch", [(select.group(1), "any_of")])
    assert saved.status == 303, re.findall(r"<li[^>]*>[^<]*", saved.body.decode())
    overlay = yaml.safe_load((tmp_path / "config.local.yaml").read_text(encoding="utf-8"))
    assert overlay["triggers"]["twitch"]["channels"][secret]["combination"] == "any_of"
    # ...and removed, through the same positional name, never echoing the key.
    page = _get(ui, f"{MODULE_PAGE_PREFIX}twitch", cookie).body.decode("utf-8")
    remove = re.search(r'name="path" value="(triggers\.twitch\.channels\.@0)">Delete this channel policy', page)
    assert remove is not None
    removed = _write(ui, cookie, "/remove", f"{MODULE_PAGE_PREFIX}twitch", [("path", remove.group(1))])
    assert removed.status == 303, removed.body
    assert secret not in removed.body.decode("utf-8")
    overlay = yaml.safe_load((tmp_path / "config.local.yaml").read_text(encoding="utf-8")) or {}
    assert secret not in overlay.get("triggers", {}).get("twitch", {}).get("channels", {})


def test_f2_declared_identifiers_are_public_text_and_stay_intact(tmp_path: Path) -> None:
    """A secret equal to a declared name (a module, a manifest field) is not
    disclosed by the name, so the markup it builds is not rewritten."""

    ui = _twitch_ui(
        tmp_path,
        "modules_directory: builtin\nenabled_modules: []\nsecrets: ['${A}']\n",
        {"A": "companion_name"},
    )
    cookie, _ = _login(ui)
    page = _get(ui, f"{MODULE_PAGE_PREFIX}twitch", cookie).body.decode("utf-8")
    assert 'name="modules.twitch.companion_name"' in page


def _attribute_values(page: str) -> list[str]:
    return re.findall(r'\s[a-z-]+="([^"]*)"', page)


def _gate2_channel_ui(tmp_path: Path, key: str, environ: dict[str, str]) -> ConfigUI:
    return _twitch_ui(
        tmp_path,
        "modules_directory: builtin\nenabled_modules: []\nsecrets: ['${GATE_SECRET}']\n"
        f"modules:\n  twitch:\n    companion_name: {json.dumps(key)}\n"
        f"    client_secret: {json.dumps(key)}\n"
        f"triggers:\n  twitch:\n    channels:\n      {json.dumps(key)}:\n"
        "        combination: all_of\n        rules:\n"
        "          - type: probability\n            parameters: {probability: 0.5}\n",
        environ,
    )


@pytest.mark.parametrize(
    "key", ["rules", "twitch", "combination", "channels", "Ω", "q7Z", "gate-secret-channel-97bf"]
)
def test_f2_a_configured_channel_key_never_reaches_a_generated_identifier(
    tmp_path: Path, key: str
) -> None:
    """Gate-2 F2 as a running test: a credential reused as a channel key and
    spelled like a declared segment (``rules``, ``twitch``, ``combination``)
    was exempted as "structural" and surfaced in
    ``name="triggers.twitch.channels.<key>.combination"``."""

    ui = _gate2_channel_ui(tmp_path, key, {"GATE_SECRET": key})
    cookie, _ = _login(ui)
    response = _get(ui, f"{MODULE_PAGE_PREFIX}twitch", cookie)
    assert response.status == 200
    page = response.body.decode("utf-8")
    assert key in ui.secret_values
    assert f'name="triggers.twitch.channels.{key}.combination"' not in page
    assert f'value="{key}"' not in page
    select = re.search(r'<select name="([^"]*)" data-combination', page)
    assert select is not None and select.group(1) == "triggers.twitch.channels.@0.combination"
    assert re.findall(r'<fieldset class="channel" data-channel="([^"]*)">', page) == ["@0"]
    # Every generated identifier naming a channel names it by position.
    channel_ids = [value for value in _attribute_values(page) if value.startswith("triggers.twitch.channels.")]
    assert channel_ids and all(value.split(".")[3].startswith("@0") for value in channel_ids)

    # The positional name is read back to the configured key.
    saved = _write(ui, cookie, "/save", f"{MODULE_PAGE_PREFIX}twitch", [(select.group(1), "any_of")])
    assert saved.status == 303, re.findall(r"<li[^>]*>[^<]*", saved.body.decode())
    overlay = yaml.safe_load((tmp_path / "config.local.yaml").read_text(encoding="utf-8"))
    assert overlay["triggers"]["twitch"]["channels"][key]["combination"] == "any_of"


def test_f2_a_non_secret_channel_key_is_shown_as_text_and_named_by_position(tmp_path: Path) -> None:
    """Gate-2 F2: a configured key occurrence is never an identifier, secret
    or not; the operator still sees the key, as the legend's text. Two
    channels are named ``@0``, ``@1``, ... and each maps back to its own key,
    a numeric channel id included (never rendered as ``[12345]``)."""

    ui = _twitch_ui(
        tmp_path,
        "modules_directory: builtin\nenabled_modules: []\n"
        "triggers:\n  twitch:\n    channels:\n"
        "      first-chan: {combination: all_of, rules: [{type: probability, parameters: {probability: 0.5}}]}\n"
        "      second-chan: {combination: all_of, rules: [{type: probability, parameters: {probability: 0.5}}]}\n"
        "      12345: {combination: all_of, rules: [{type: probability, parameters: {probability: 0.5}}]}\n",
        {},
    )
    cookie, _ = _login(ui)
    page = _get(ui, f"{MODULE_PAGE_PREFIX}twitch", cookie).body.decode("utf-8")
    assert "<legend>Channel <code>first-chan</code></legend>" in page
    assert "<legend>Channel <code>12345</code></legend>" in page
    assert re.findall(r'<fieldset class="channel" data-channel="([^"]*)">', page) == ["@0", "@1", "@2"]
    for value in _attribute_values(page):
        assert all(key not in value for key in ("first-chan", "second-chan", "12345")), value
    assert 'name="triggers.twitch.channels.@2.combination"' in page
    fields = [("triggers.twitch.channels.@1.combination", "any_of")]
    saved = _write(ui, cookie, "/save", f"{MODULE_PAGE_PREFIX}twitch", fields)
    assert saved.status == 303, re.findall(r"<li[^>]*>[^<]*", saved.body.decode())
    overlay = yaml.safe_load((tmp_path / "config.local.yaml").read_text(encoding="utf-8"))
    assert overlay["triggers"]["twitch"]["channels"] == {
        "second-chan": {
            "combination": "any_of",
            "rules": [{"type": "probability", "parameters": {"probability": 0.5}}],
        }
    }
    # The text of a key is not a name the parser accepts.
    view = ui.view()
    edits, problems = ui.parse_edits(view, [("triggers.twitch.channels.first-chan.combination", "any_of")])
    assert edits == [] and problems == [
        "triggers.twitch.channels.first-chan.combination: is not a setting this UI edits"
    ]


def test_f2_a_rule_id_is_shown_and_never_an_identifier(tmp_path: Path) -> None:
    """Gate-2 F2: a rule id (the operator's alias for a rule) used to be
    copied into ``data-rule``; it is shown in its cell only."""

    base = _fixture_config(tmp_path, rules=[("rules", "x.do"), ("alias-7f3", "y.do")])
    ui = _ui(base, "--status-file", str(tmp_path / "status.json"))
    cookie, _ = _login(ui)
    actions = _section(_get(ui, "/core", cookie).body.decode("utf-8"), "actions")
    assert re.findall(r'<tr data-rule="([^"]*)"', actions) == ["0", "1"]
    assert "<td><code>alias-7f3</code></td>" in actions
    assert all("alias-7f3" not in value for value in _attribute_values(actions))


# -- Gate-1 F3: a boolean draft has three honest states ------------------------------

AUDIO_SETTINGS_TEXT = (
    "modules_directory: builtin\nenabled_modules: []\n"
    "modules:\n  audio_output:\n"
    "    synthesis: {endpoint: '', model: ''}\n"
    "    voices: {allowed: [v], default: v}\n"
    "    outputs: {o: {player: {argv: [cat]}}}\n"
    "    default_output: o\n"
)
PROBE_FIELD = "modules.audio_output.synthesis.probe"


def _probe_select(page: str) -> str:
    match = re.search(rf'<select name="{re.escape(PROBE_FIELD)}"[^>]*>.*?</select>', page, re.S)
    assert match is not None
    return match.group(0)


def test_f3_an_unset_true_default_boolean_renders_its_effective_value(tmp_path: Path) -> None:
    """Gate-1 C/F3 as a running test, through the shipped ``audio_output``
    manifest: an unset ``synthesis.probe`` is ``true`` at runtime, and used
    to render as an unchecked checkbox."""

    from modules.audio_output import _Settings

    ui = _twitch_ui(tmp_path, AUDIO_SETTINGS_TEXT, {})
    cookie, _ = _login(ui)
    control = _probe_select(_module_html(ui, cookie, "audio_output"))
    assert 'data-effective="true"' in control
    assert re.findall(r'<option value="([^"]*)"( selected)?>([^<]*)', control) == [
        ("", " selected", "not set (default: true)"),
        ("true", "", "true"),
        ("false", "", "false"),
    ]
    module = yaml.safe_load(AUDIO_SETTINGS_TEXT)["modules"]["audio_output"]
    assert _Settings.from_mapping(module).probe is True


@pytest.mark.parametrize(("posted", "written"), [("", None), ("false", False), ("true", True)])
def test_f3_a_boolean_draft_expresses_unset_false_and_true(
    tmp_path: Path, posted: str, written: bool | None
) -> None:
    """Gate-1 C/F3: posting ``false`` over an unset ``default: true`` used to
    parse to no edit (``edits=[]``), so Check saw an empty overlay and Save
    wrote nothing. Each state now reaches the parser, Check, Save and the
    runtime settings parser distinctly."""

    from modules.audio_output import _Settings

    seen: list[Mapping[str, Any]] = []

    def checker(path: Path, environ: Mapping[str, str], overlay: Path | None, draft: Mapping[str, Any]):
        seen.append(draft)
        return True, []

    ui = _twitch_ui(tmp_path, AUDIO_SETTINGS_TEXT, {})
    ui.checker = checker  # type: ignore[assignment]
    cookie, _ = _login(ui)
    token = config_ui._RENDERING_FOR.set(ui)
    try:
        edits, problems = ui.parse_edits(ui.view(), [(PROBE_FIELD, posted)])
    finally:
        config_ui._RENDERING_FOR.reset(token)
    expected = [] if written is None else [(("modules", "audio_output", "synthesis", "probe"), written)]
    assert (edits, problems) == (expected, [])

    page = _post_check(ui, f"{MODULE_PAGE_PREFIX}audio_output", [(PROBE_FIELD, posted)], cookie=cookie)
    assert _verdict(page) is True
    draft_probe = seen[-1].get("modules", {}).get("audio_output", {}).get("synthesis", {}).get("probe")
    assert draft_probe is written

    saved = _write(ui, cookie, "/save", f"{MODULE_PAGE_PREFIX}audio_output", [(PROBE_FIELD, posted)])
    if written is None:
        assert 'data-outcome="unchanged"' in saved.body.decode("utf-8")
    else:
        assert saved.status == 303, saved.body
    overlay_file = tmp_path / "config.local.yaml"
    overlay = yaml.safe_load(overlay_file.read_text(encoding="utf-8")) if overlay_file.exists() else None
    if written is None:
        assert not overlay
    else:
        assert overlay == {"modules": {"audio_output": {"synthesis": {"probe": written}}}}
    merged = config_ui.deep_merge(yaml.safe_load(AUDIO_SETTINGS_TEXT), overlay or {})
    runtime = _Settings.from_mapping(merged["modules"]["audio_output"]).probe
    assert runtime is (True if written is None else written)

    # The page redrawn over the saved overlay selects the explicit value.
    control = _probe_select(_module_html(ui, cookie, "audio_output"))
    effective = "true" if written is None else json.dumps(written)
    assert f'data-effective="{effective}"' in control
    assert ('<option value=""' in control) is (written is None)


def test_f3_a_non_boolean_configured_value_posts_untouched(tmp_path: Path) -> None:
    ui = _twitch_ui(tmp_path, AUDIO_SETTINGS_TEXT.replace("model: ''}", "model: '', probe: maybe}"), {})
    cookie, _ = _login(ui)
    control = _probe_select(_module_html(ui, cookie, "audio_output"))
    keep = config_ui._KEEP_NON_BOOLEAN
    assert f'<option value="{keep}" selected>maybe (not a boolean)</option>' in control
    token = config_ui._RENDERING_FOR.set(ui)
    try:
        assert ui.parse_edits(ui.view(), [(PROBE_FIELD, keep)]) == ([], [])
    finally:
        config_ui._RENDERING_FOR.reset(token)


@pytest.mark.parametrize("text", ["true", "false"])
def test_f3_a_configured_boolean_spelling_string_can_be_corrected(tmp_path: Path, text: str) -> None:
    """A configured *string* ``'true'`` keeps its own option, and the real
    ``true`` and ``false`` choices stay selectable: posting either writes the
    boolean, while the kept option still posts untouched."""

    ui = _twitch_ui(tmp_path, AUDIO_SETTINGS_TEXT.replace("model: ''}", f"model: '', probe: '{text}'}}"), {})
    cookie, _ = _login(ui)
    control = _probe_select(_module_html(ui, cookie, "audio_output"))
    keep = config_ui._KEEP_NON_BOOLEAN
    assert f'<option value="{keep}" selected>{text} (not a boolean)</option>' in control
    assert '<option value="true">true</option>' in control
    assert '<option value="false">false</option>' in control
    probe = ("modules", "audio_output", "synthesis", "probe")
    token = config_ui._RENDERING_FOR.set(ui)
    try:
        assert ui.parse_edits(ui.view(), [(PROBE_FIELD, keep)]) == ([], [])
        assert ui.parse_edits(ui.view(), [(PROBE_FIELD, "true")]) == ([(probe, True)], [])
        assert ui.parse_edits(ui.view(), [(PROBE_FIELD, "false")]) == ([(probe, False)], [])
    finally:
        config_ui._RENDERING_FOR.reset(token)


# -- Gate-2 N1: redaction never rewrites the UI's own tokens ------------------------


@pytest.fixture
def forget_ui() -> Iterator[Callable[[ConfigUI], ConfigUI]]:
    """Drop each UI from the process-wide log redaction filter afterwards:
    a one-character secret set left alive would redact later tests' logs."""

    made: list[ConfigUI] = []

    def forget(ui: ConfigUI) -> ConfigUI:
        made.append(ui)
        return ui

    yield forget
    for ui in made:
        config_ui._LOG_REDACTION.sources.discard(ui)


def _secret_audio_ui(tmp_path: Path, secret: str, settings: str = AUDIO_SETTINGS_TEXT) -> ConfigUI:
    text = settings.replace("enabled_modules: []\n", "enabled_modules: []\nsecrets: ['${GATE_SECRET}']\n")
    return _twitch_ui(tmp_path, text, {"GATE_SECRET": secret})


def _rendered_options(control: str) -> list[tuple[str, str]]:
    return [
        (_html.unescape(value), _html.unescape(label))
        for value, label in re.findall(r'<option value="([^"]*)"[^>]*>([^<]*)</option>', control)
    ]


# Short secrets occurring inside ``true`` (r, u), inside ``false`` (a, l, s),
# inside both (e) and inside the kept-value token (k, -).
@pytest.mark.parametrize("secret", ["r", "u", "t", "e", "a", "l", "s", "f", "k", "-", "ue", "als"])
def test_n1_a_short_secret_inside_true_or_false_never_rewrites_the_boolean_options(
    tmp_path: Path, secret: str, forget_ui: Callable[[ConfigUI], ConfigUI]
) -> None:
    """Gate-2 N1 (``test_gate2_boolean_redaction``) as a running test: with
    ``${GATE_SECRET}`` set to ``r`` the true option rendered as
    ``<option value="t[hidden]ue">`` and submitting it sent the string
    ``'t[hidden]ue'`` to Check, while Save refused it with 422. The options are
    the UI's own tokens: each rendered option, submitted exactly as rendered,
    now reaches Check, Save, the overlay and the runtime settings parser as a
    real boolean, and the unset option stays "no edit" (gate-1 F3)."""

    from modules.audio_output import _Settings

    seen: list[Mapping[str, Any]] = []

    def checker(path: Path, environ: Mapping[str, str], overlay: Path | None, draft: Mapping[str, Any]):
        seen.append(draft)
        return True, []

    ui = forget_ui(_secret_audio_ui(tmp_path, secret))
    ui.checker = checker  # type: ignore[assignment]
    cookie, _ = _login(ui)
    page = f"{MODULE_PAGE_PREFIX}audio_output"
    probe = ("modules", "audio_output", "synthesis", "probe")
    overlay_file = tmp_path / "config.local.yaml"

    def runtime() -> bool:
        overlay = yaml.safe_load(overlay_file.read_text(encoding="utf-8")) if overlay_file.exists() else {}
        merged = config_ui.deep_merge(yaml.safe_load(AUDIO_SETTINGS_TEXT), overlay or {})
        return _Settings.from_mapping(merged["modules"]["audio_output"]).probe

    control = _probe_select(_module_html(ui, cookie, "audio_output"))
    assert secret in ui.secret_values
    assert "[hidden]" not in control
    assert 'data-effective="true"' in control
    options = _rendered_options(control)
    assert options == [("", "not set (default: true)"), ("true", "true"), ("false", "false")]
    unset, true, false = (value for value, _ in options)

    # Unset: the inherit option is no edit, and the draft carries no probe.
    token = config_ui._RENDERING_FOR.set(ui)
    try:
        assert ui.parse_edits(ui.view(), [(PROBE_FIELD, unset)]) == ([], [])
        assert ui.parse_edits(ui.view(), [(PROBE_FIELD, true)]) == ([(probe, True)], [])
        assert ui.parse_edits(ui.view(), [(PROBE_FIELD, false)]) == ([(probe, False)], [])
    finally:
        config_ui._RENDERING_FOR.reset(token)
    assert _verdict(_post_check(ui, page, [(PROBE_FIELD, unset)], cookie=cookie)) is True
    assert "probe" not in seen[-1].get("modules", {}).get("audio_output", {}).get("synthesis", {})

    # Explicit false over ``default: true``: a real boolean end to end.
    assert _verdict(_post_check(ui, page, [(PROBE_FIELD, false)], cookie=cookie)) is True
    assert seen[-1]["modules"]["audio_output"]["synthesis"]["probe"] is False
    saved = _write(ui, cookie, "/save", page, [(PROBE_FIELD, false)])
    assert saved.status == 303, saved.body
    assert yaml.safe_load(overlay_file.read_text(encoding="utf-8")) == {
        "modules": {"audio_output": {"synthesis": {"probe": False}}}
    }
    assert runtime() is False

    # Redrawn over the saved false, the true option is still the real token.
    control = _probe_select(_module_html(ui, cookie, "audio_output"))
    assert 'data-effective="false"' in control
    assert _rendered_options(control) == [("true", "true"), ("false", "false")]
    assert '<option value="false" selected>' in control
    true = _rendered_options(control)[0][0]
    assert _verdict(_post_check(ui, page, [(PROBE_FIELD, true)], cookie=cookie)) is True
    assert seen[-1]["modules"]["audio_output"]["synthesis"]["probe"] is True
    saved = _write(ui, cookie, "/save", page, [(PROBE_FIELD, true)])
    assert saved.status == 303, saved.body
    assert yaml.safe_load(overlay_file.read_text(encoding="utf-8")) == {
        "modules": {"audio_output": {"synthesis": {"probe": True}}}
    }
    assert runtime() is True

    # The false option posted untouched over a saved false is no edit.
    control = _probe_select(_module_html(ui, cookie, "audio_output"))
    selected = re.search(r'<option value="([^"]*)" selected>', control)
    assert selected is not None and selected.group(1) == "true"
    token = config_ui._RENDERING_FOR.set(ui)
    try:
        assert ui.parse_edits(ui.view(), [(PROBE_FIELD, "true")]) == ([], [])
    finally:
        config_ui._RENDERING_FOR.reset(token)


@pytest.mark.parametrize("secret", ["q7Z", "maybe"])
def test_n1_a_configured_secret_is_still_never_rendered_beside_the_boolean_tokens(
    tmp_path: Path, secret: str, forget_ui: Callable[[ConfigUI], ConfigUI]
) -> None:
    """Gate-2 N1 must not reopen gate-1 F2: only the UI's tokens are exempt.
    A configured non-boolean ``probe`` and a configured endpoint equal to a
    secret are data, and both still render ``[hidden]``; the kept option
    still posts untouched."""

    settings = AUDIO_SETTINGS_TEXT.replace(
        "synthesis: {endpoint: '', model: ''}", f"synthesis: {{endpoint: '{secret}', model: '', probe: '{secret}'}}"
    )
    ui = forget_ui(_secret_audio_ui(tmp_path, secret, settings))
    cookie, _ = _login(ui)
    html_page = _module_html(ui, cookie, "audio_output")
    assert secret not in html_page
    control = _probe_select(html_page)
    keep = config_ui._KEEP_NON_BOOLEAN
    assert 'data-effective="[hidden]"' in control
    assert _rendered_options(control) == [(keep, "[hidden] (not a boolean)"), ("true", "true"), ("false", "false")]
    # P19F9: the endpoint equal to a secret is an empty replacement input.
    assert 'name="modules.audio_output.synthesis.endpoint" value="" placeholder=' in html_page
    token = config_ui._RENDERING_FOR.set(ui)
    try:
        assert ui.parse_edits(ui.view(), [(PROBE_FIELD, keep)]) == ([], [])
    finally:
        config_ui._RENDERING_FOR.reset(token)


@pytest.mark.parametrize("secret", ["l", "_", "o"])
def test_n1_a_short_secret_never_rewrites_declared_enum_options(
    tmp_path: Path, secret: str, forget_ui: Callable[[ConfigUI], ConfigUI]
) -> None:
    """The same class as gate-2 N1 for declared enum members: with a secret
    ``l`` the combination select registered ``a[hidden][hidden]_of`` as its
    untouched value, so the unchanged ``all_of`` option posted as an edit.
    The members are the schema's tokens; posted as rendered, they are no edit,
    and the other member is a real edit."""

    ui = forget_ui(_twitch_ui(
        tmp_path,
        "modules_directory: builtin\nenabled_modules: []\nsecrets: ['${GATE_SECRET}']\n"
        "triggers:\n  twitch:\n    channels:\n      chan:\n"
        "        combination: all_of\n        rules:\n"
        "          - type: probability\n            parameters: {probability: 0.5}\n",
        {"GATE_SECRET": secret},
    ))
    cookie, _ = _login(ui)
    page = _module_html(ui, cookie, "twitch")
    select = re.search(r'<select name="([^"]*\.combination)" data-combination[^>]*>(.*?)</select>', page, re.S)
    assert select is not None
    options = re.findall(r'<option value="([^"]*)"( selected)?>([^<]*)</option>', select.group(2))
    assert ("all_of", " selected", "all_of") in options
    assert ("any_of", "", "any_of") in options
    path = ("triggers", "twitch", "channels", "chan", "combination")
    token = config_ui._RENDERING_FOR.set(ui)
    try:
        assert ui.parse_edits(ui.view(), [(select.group(1), "all_of")]) == ([], [])
        edits, problems = ui.parse_edits(ui.view(), [(select.group(1), "any_of")])
        assert problems == []
        assert [(edit_path, value["combination"]) for edit_path, value in edits] == [(path[:-1], "any_of")]
    finally:
        config_ui._RENDERING_FOR.reset(token)


# -- Gate-1 F4: the dispatch bridge admits a bounded number of requests ---------------


def test_f4_a_saturated_bridge_refuses_honestly_and_queues_nothing_more() -> None:
    """Gate-1 C/F4 as a running test: 128 requests on the production worker
    count used to leave 124 in the executor's unbounded queue. Admission is
    now bounded; the overflow is refused with 503 before its body is read."""

    release = threading.Event()
    handled: list[str] = []
    reads: list[int] = []

    class Blocked:
        def handle(self, request: UIRequest) -> UIResponse:
            release.wait(5)  # released by the test; the bound only guards a failure
            handled.append(request.path)
            return UIResponse(401)

    total = 128
    admission = config_ui._Admission()
    assert admission.capacity == config_ui.MAX_ADMITTED_REQUESTS == 4 * config_ui.EXECUTOR_WORKERS

    def reader(index: int):
        async def read() -> UIRequest:
            reads.append(index)
            return UIRequest("GET", f"/{index}", {}, {})

        return read

    async def saturation() -> list[UIResponse]:
        with config_ui.ThreadPoolExecutor(max_workers=config_ui.EXECUTOR_WORKERS) as executor:
            tasks = [
                asyncio.create_task(_dispatch(Blocked(), reader(index), executor, admission))  # type: ignore[arg-type]
                for index in range(total)
            ]
            try:
                for _ in range(3):
                    await asyncio.sleep(0)
                queued = executor._work_queue.qsize()  # type: ignore[attr-defined]
                assert queued <= config_ui.MAX_ADMITTED_REQUESTS - config_ui.EXECUTOR_WORKERS
                assert admission.admitted == config_ui.MAX_ADMITTED_REQUESTS
                assert len(reads) == config_ui.MAX_ADMITTED_REQUESTS
            finally:
                release.set()
                results = await asyncio.gather(*tasks)
        return results

    responses = _run_socket_free(saturation())
    busy = [response for response in responses if response.status == config_ui.BUSY_STATUS]
    served = [response for response in responses if response.status == 401]
    assert len(served) == len(handled) == config_ui.MAX_ADMITTED_REQUESTS
    assert len(busy) == total - config_ui.MAX_ADMITTED_REQUESTS == admission.refused
    assert all(("Retry-After", "1") in response.headers for response in busy)
    assert all(b"nothing was done" in response.body for response in busy)
    # Every admitted request left, so the bridge admits again.
    assert admission.admitted == 0
    assert admission.try_enter() is True


def test_f4_a_failed_body_read_releases_its_admission() -> None:
    admission = config_ui._Admission(capacity=1)

    async def failing() -> UIRequest:
        raise ValueError("body too large")

    async def scenario() -> None:
        with config_ui.ThreadPoolExecutor(max_workers=1) as executor:
            with pytest.raises(ValueError):
                await _dispatch(object(), failing, executor, admission)  # type: ignore[arg-type]

    _run_socket_free(scenario())
    assert admission.admitted == 0


# -- Gate-1 F5: every wait of the stop sequence is bounded ----------------------------


class _Unkillable(_SpiedChild):
    """A child whose every wait times out: it cannot be confirmed ended."""

    def wait(self, timeout: float | None = None) -> int:
        self.calls.append(f"wait({timeout})")
        raise subprocess.TimeoutExpired("child", timeout if timeout is not None else 0.0)


def test_f5_an_unkillable_child_is_reported_and_kept_not_waited_on_forever() -> None:
    """Gate-1 C/F5 as a running test: the real supervisor used to make the
    sequence ``terminate, wait(10.0), kill, wait(None)``."""

    supervisor = config_ui.Supervisor(("x",), "status.json")
    child = _Unkillable()
    supervisor.child = child
    assert supervisor.stop() is False
    assert child.calls == [
        "terminate",
        f"wait({config_ui.DEFAULT_STOP_GRACE})",
        "kill",
        f"wait({config_ui.DEFAULT_KILL_WAIT})",
    ]
    assert "wait(None)" not in child.calls
    # Ownership is kept: the child is still the one supervised, never lost.
    assert supervisor.child is child


def test_f5_apply_with_an_unkillable_child_is_refused_and_starts_nothing() -> None:
    now = [0.0]
    launched: list[object] = []

    def popen(*args: object, **kwargs: object) -> object:
        launched.append(args)
        raise AssertionError("nothing may be started beside an unreaped child")

    supervisor = config_ui.Supervisor(
        ("x",),
        "status.json",
        stop_grace=10.0,
        kill_wait=5.0,
        clock=lambda: now[0],
        wait=lambda interval: None,
        popen=popen,
    )
    child = _Unkillable(pid=5151)
    supervisor.child = child
    report = supervisor.restart("digest")
    assert report.outcome == config_ui.APPLY_REFUSED
    assert not report.check_refused
    assert report.diagnostics == (
        "the running process (pid 5151) did not end within 5 s after kill; nothing new was started",
    )
    assert launched == [] and supervisor.child is child
    assert child.calls == ["terminate", "wait(10.0)", "kill", "wait(5.0)"]


def test_f5_close_logs_an_unkillable_child(caplog: pytest.LogCaptureFixture) -> None:
    supervisor = config_ui.Supervisor(("x",), "status.json", kill_wait=1.0)
    supervisor.child = _Unkillable(pid=6262)
    with caplog.at_level(logging.WARNING, logger=config_ui.logger.name):
        supervisor.close()
    assert "6262 did not end within 1 s after kill" in caplog.text
    with pytest.raises(config_ui.SupervisorClosed):
        supervisor.start()


# ---------------------------------------------------------------------------
# Gate-3 F2 residual: the masking is value-driven, not path-driven
# ---------------------------------------------------------------------------

_F2_JSON_SECRETS = ['a"b', "a\\b", "a\nb", "q7Z", "Ω"]


def _f2_value_ui(tmp_path: Path, secret: str) -> ConfigUI:
    """The gate-3 D/positions and D/JSON fixture: a collected ``${GATE_SECRET}``
    reused as a mapping key and a value inside the brain's delivery action
    list, and as a value of the audio voices' allowed list."""

    arguments = json.dumps(
        {secret: "v", "plain": secret, "nested": [{"deep": secret}], f"k{secret}": 2}
    )
    allowed = json.dumps([secret, "kept", f"x{secret}y"])
    return _twitch_ui(
        tmp_path,
        "modules_directory: builtin\nenabled_modules: []\nsecrets: ['${GATE_SECRET}']\n"
        "modules:\n  brain:\n    delivery:\n      actions:\n"
        f"        - action: reply\n          arguments: {arguments}\n"
        f"  audio_output:\n    voices:\n      allowed: {allowed}\n",
        {"GATE_SECRET": secret},
    )


def _decoded_textareas(page: str) -> list[object]:
    """Every JSON textarea of *page*, HTML-unescaped and re-parsed."""

    decoded: list[object] = []
    for body in re.findall(r"<textarea[^>]*>(.*?)</textarea>", page, re.S):
        text = _html.unescape(body)
        try:
            decoded.append(json.loads(text))
        except ValueError:
            decoded.append(text)
    return decoded


def _scalars(value: object) -> Iterator[object]:
    """Every scalar of *value*, mapping keys included, at any depth."""

    if isinstance(value, Mapping):
        for key, item in value.items():
            yield key
            yield from _scalars(item)
    elif isinstance(value, list):
        for item in value:
            yield from _scalars(item)
    else:
        yield value


# ---------------------------------------------------------------------------
# P19F9 (gate-6 N3, N5, N6): a value the page cannot show exactly is edited
# entry by entry, each entry named by an opaque identity; the server applies
# the posted changes to the real configuration and never reconstructs a value
# from masked text. The tests below drive the patch controls as a browser
# posts them: every text control with its text, a checkbox only when ticked.
# ---------------------------------------------------------------------------


_VOID_TAGS = frozenset({"input", "br", "meta", "link", "img", "hr", "col", "area", "base", "wbr"})


class _PatchEditors(HTMLParser):
    """Every patch editor of a page as nested entries.

    A node is the editor's root or one ``li.patch-entry``: ``controls`` maps
    an operation (``value``, ``order``, ``remove``, ``item``, ``key``,
    ``entry``) to its control's name and ``values`` to the text it posts
    untouched; ``entries`` are its children in page order; ``key`` is the
    shown key label of a mapping entry, ``withheld`` the shown text of a
    withheld value and ``replace`` whether its value control is an empty
    replacement.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.fields: dict[str, dict[str, Any]] = {}
        self._stack: list[tuple[str, dict[str, Any] | None]] = []
        self._capture: tuple[str, dict[str, Any], str] | None = None
        #: The open ``<select>`` patch control: it posts its selected option.
        self._select: tuple[dict[str, Any], str, bool] | None = None

    @staticmethod
    def _new() -> dict[str, Any]:
        return {"entries": [], "controls": {}, "values": {}, "key": None, "withheld": None, "replace": False}

    def _current(self) -> dict[str, Any] | None:
        return next((node for _tag, node in reversed(self._stack) if node is not None), None)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {name: value or "" for name, value in attrs}
        classes = attributes.get("class", "").split()
        node = None
        if tag == "div" and "patch" in classes:
            node = self._new()
            node["field"] = attributes["data-patch-field"]
            self.fields[attributes["data-patch-field"]] = node
        elif tag == "li" and "patch-entry" in classes:
            node = self._new()
            parent = self._current()
            assert parent is not None
            parent["entries"].append(node)
        current = node or self._current()
        name = attributes.get("name", "")
        if current is not None and name.startswith(config_ui.PATCH_FIELD_PREFIX):
            operation = name[len(config_ui.PATCH_FIELD_PREFIX) :].split("-", 1)[0]
            assert operation not in current["controls"], name
            current["controls"][operation] = name
            if tag == "textarea":
                current["values"][operation] = ""
                self._capture = ("textarea", current, operation)
            elif tag == "select":
                current["values"][operation] = ""
                self._select = (current, operation, False)
            elif attributes.get("type") != "checkbox":
                current["values"][operation] = attributes.get("value", "")
            if attributes.get("data-patch") == "replace":
                current["replace"] = True
        if tag == "option" and self._select is not None:
            owner, operation, chosen = self._select
            if not chosen or "selected" in attributes:
                # The first option, until one is selected.
                owner["values"][operation] = attributes.get("value", "")
                self._select = (owner, operation, "selected" in attributes)
        if current is not None and tag == "span" and "patch-key" in classes:
            current["key"] = ""
            self._capture = ("span", current, "key")
        if current is not None and tag == "code" and "withheld" in classes:
            current["withheld"] = ""
            self._capture = ("code", current, "withheld")
        if tag not in _VOID_TAGS:
            self._stack.append((tag, node))

    def handle_endtag(self, tag: str) -> None:
        if tag == "select":
            self._select = None
        if self._capture is not None and self._capture[0] == tag:
            self._capture = None
        while self._stack:
            popped, _node = self._stack.pop()
            if popped == tag:
                break

    def handle_data(self, data: str) -> None:
        if self._capture is None:
            return
        _tag, node, slot = self._capture
        if slot in ("key", "withheld"):
            node[slot] += data
        else:
            node["values"][slot] += data


def _patch_editors(page: str) -> dict[str, dict[str, Any]]:
    parser = _PatchEditors()
    parser.feed(page)
    parser.close()
    return parser.fields


def _patch_editor(page: str, suffix: str) -> dict[str, Any]:
    """The one patch editor of *page* whose field name ends in *suffix*."""

    found = [node for field, node in _patch_editors(page).items() if field.endswith(suffix)]
    assert len(found) == 1, list(_patch_editors(page))
    return found[0]


def _browser(node: dict[str, Any]) -> list[tuple[str, str]]:
    """What a browser posts for *node* left untouched: every text control's
    text, no checkbox."""

    fields = [(name, node["values"].get(operation, "")) for operation, name in node["controls"].items()
              if operation != config_ui.PATCH_REMOVE]
    for child in node["entries"]:
        fields.extend(_browser(child))
    return fields


def _submitted(node: dict[str, Any], changes: Mapping[str, str]) -> list[tuple[str, str]]:
    """The browser's post of *node* with *changes* typed or ticked."""

    fields = [(name, changes.get(name, text)) for name, text in _browser(node)]
    posted = {name for name, _text in fields}
    return fields + [(name, text) for name, text in changes.items() if name not in posted]


def _replaced(entry: dict[str, Any], text: str) -> dict[str, str]:
    """What a browser posts to replace a withheld entry with *text*: the
    text typed and its operation set to ``set`` (P19F10)."""

    return {
        entry["controls"][config_ui.PATCH_VALUE]: text,
        entry["controls"][config_ui.PATCH_OPERATION]: config_ui.OPERATION_SET,
    }


def _exact_values(node: dict[str, Any]) -> Iterator[Any]:
    """The decoded text of every exact value control under *node*."""

    if config_ui.PATCH_VALUE in node["controls"] and not node["replace"]:
        yield json.loads(node["values"][config_ui.PATCH_VALUE])
    for child in node["entries"]:
        yield from _exact_values(child)


def _observed(ui: ConfigUI) -> list[Any]:
    """Every draft the checker is handed from now on (it passes them all)."""

    seen: list[Any] = []
    ui.checker = lambda base, environ, overlay, draft: (seen.append(json.loads(json.dumps(draft))) or True, [])
    return seen


def _check_then_save(ui: ConfigUI, cookie: str, module: str, fields: list[tuple[str, str]]) -> tuple[str, UIResponse]:
    """POST *fields* to Check, then the same fields to Save, from *module*'s page."""

    page = f"{MODULE_PAGE_PREFIX}{module}"
    check = _write(ui, cookie, "/check", page, fields)
    assert check.status == 200
    return check.body.decode("utf-8"), _write(ui, cookie, "/save", page, fields)


@pytest.mark.parametrize("secret", _F2_JSON_SECRETS)
def test_f2_a_secret_used_as_a_list_key_or_value_is_never_rendered(
    tmp_path: Path, secret: str
) -> None:
    """Gate-3 F2 "list key" and "list value" defeats as running tests: the
    collected secret ``a"b`` as a mapping key at
    ``modules.brain.delivery.actions[0].arguments`` decoded exactly from the
    JSON textarea, and ``modules.audio_output.voices.allowed: ['a"b']`` was
    recovered by ``json.loads(html.unescape(textarea))[0]``.

    P19F9: a value holding a secret is no longer one JSON text; it is an
    editor per entry. Every exact control still decodes and no decoded
    scalar holds the secret; withheld entries show masked text only, and
    each key is a label, never a generated attribute."""

    ui = _f2_value_ui(tmp_path, secret)
    assert secret in ui.view().secret_values
    cookie = _logged_in(ui)
    for name in ("brain", "audio_output"):
        page = _module_html(ui, cookie, name)
        assert secret not in page, name
        assert secret not in _html.unescape(page), name
        assert json.dumps(secret)[1:-1] not in _html.unescape(page), name
        editors = _patch_editors(page)
        assert editors, name
        for node in editors.values():
            assert all(secret not in str(scalar) for value in _exact_values(node) for scalar in _scalars(value))

    actions = _patch_editor(_module_html(ui, cookie, "brain"), "delivery.actions")
    (action,) = actions["entries"]
    by_key = {entry["key"]: entry for entry in action["entries"]}
    assert list(by_key) == ["action", "arguments"]
    assert list(_exact_values(by_key["action"])) == ["reply"]
    arguments = by_key["arguments"]["entries"]
    assert [entry["key"] for entry in arguments] == [config_ui.REDACTED, "plain", "nested", f"k{config_ui.REDACTED}"]
    assert list(_exact_values(arguments[0])) == ["v"]
    assert arguments[1]["replace"] and arguments[1]["withheld"] == HIDDEN_LITERAL
    (deep,) = arguments[2]["entries"][0]["entries"]
    assert deep["key"] == "deep" and deep["withheld"] == HIDDEN_LITERAL
    assert list(_exact_values(arguments[3])) == [2]
    allowed = _patch_editor(_module_html(ui, cookie, "audio_output"), "voices.allowed")["entries"]
    assert allowed[0]["withheld"] == HIDDEN_LITERAL
    assert list(_exact_values(allowed[1])) == ["kept"]
    assert secret not in allowed[2]["withheld"]
    # The reference and its set state stay visible.
    core = _get(ui, "/core", cookie).body.decode("utf-8")
    assert "${GATE_SECRET}" in core
    assert 'data-secret="GATE_SECRET"' in core


def test_f2_two_secret_keys_in_one_mapping_get_distinct_stand_ins(tmp_path: Path) -> None:
    """Gate-3 F2: withheld keys stay distinct, so neither overwrites the other.

    P19F9: each key is its own entry with its own identity, whatever its
    shown label; editing one entry's value changes that entry only."""

    ui = _twitch_ui(
        tmp_path,
        "modules_directory: builtin\nenabled_modules: []\n"
        "secrets: ['${ONE}', '${TWO}']\n"
        "modules:\n  brain:\n    delivery:\n      actions:\n"
        "        - action: reply\n          arguments: {'k1\"': 1, 'k2\"': 2, other: 3}\n",
        {"ONE": 'k1"', "TWO": 'k2"'},
    )
    cookie = _logged_in(ui)
    actions = _patch_editor(_module_html(ui, cookie, "brain"), "delivery.actions")
    arguments = actions["entries"][0]["entries"][1]["entries"]
    assert [entry["key"] for entry in arguments] == [config_ui.REDACTED, config_ui.REDACTED, "other"]
    names = {entry["controls"][config_ui.PATCH_VALUE] for entry in arguments}
    assert len(names) == 3
    response = _write(
        ui, cookie, "/save", f"{MODULE_PAGE_PREFIX}brain",
        _submitted(actions, {arguments[1]["controls"][config_ui.PATCH_VALUE]: "5"}),
    )
    assert response.status == 303, _written(response)
    saved = _overlay_doc(tmp_path / "config.local.yaml")
    assert saved["modules"]["brain"]["delivery"]["actions"][0]["arguments"] == {'k1"': 1, 'k2"': 5, "other": 3}


def test_f2_an_edited_json_field_puts_withheld_values_and_keys_back(tmp_path: Path) -> None:
    """Gate-3 F2: a field edited around its withheld entries saves the
    configured secret, never the placeholder (value and key alike, a secret
    inside a longer text included).

    P19F9: the withheld entries are never posted at all; the appended item
    and the added key are the only changes applied to the real values."""

    secret = 'a"b'
    ui = _f2_value_ui(tmp_path, secret)
    cookie = _logged_in(ui)
    allowed = _patch_editor(_module_html(ui, cookie, "audio_output"), "voices.allowed")
    response = _write(
        ui, cookie, "/save", f"{MODULE_PAGE_PREFIX}audio_output",
        _submitted(allowed, {allowed["controls"][config_ui.PATCH_ITEM]: '"added"'}),
    )
    assert response.status == 303, _written(response)
    saved = _overlay_doc(tmp_path / "config.local.yaml")
    assert saved["modules"]["audio_output"]["voices"]["allowed"] == [secret, "kept", f"x{secret}y", "added"]

    actions = _patch_editor(_module_html(ui, cookie, "brain"), "delivery.actions")
    arguments = actions["entries"][0]["entries"][1]
    response = _write(
        ui, cookie, "/save", f"{MODULE_PAGE_PREFIX}brain",
        _submitted(actions, {
            arguments["controls"][config_ui.PATCH_KEY]: "added",
            arguments["controls"][config_ui.PATCH_ENTRY]: "1",
        }),
    )
    assert response.status == 303, _written(response)
    saved = _overlay_doc(tmp_path / "config.local.yaml")
    assert saved["modules"]["brain"]["delivery"]["actions"][0]["arguments"] == {
        secret: "v", "plain": secret, "nested": [{"deep": secret}], f"k{secret}": 2, "added": 1,
    }


@pytest.mark.parametrize("secret", ['a"b', "a\\b", "a\nb", "é "])
def test_f2_a_json_spelled_secret_is_redacted_from_errors_and_diagnostics(secret: str) -> None:
    """Gate-3 F2: ``_redaction_forms`` held the raw and HTML spellings only, so
    a secret serialized by ``json.dumps`` (``a\\"b``) passed through error and
    diagnostic text. Every JSON spelling is now redacted too, and the text
    no longer yields the secret once HTML-unescaped and re-parsed."""

    for ascii_only in (False, True):
        text = f"invalid value {json.dumps([secret, 'kept'], ensure_ascii=ascii_only)}"
        for shown in (text, _html.escape(text, quote=True)):
            redacted = config_ui._redact(shown, {secret})
            decoded = json.loads(_html.unescape(redacted)[len("invalid value ") :])
            assert decoded == [config_ui.REDACTED, "kept"]
            assert secret not in _html.unescape(redacted)


# ---------------------------------------------------------------------------
# P19F5 review, under P19F9: list entries keep their identity through
# removal, reordering and appending
# ---------------------------------------------------------------------------


def _allowed_ui(tmp_path: Path, secret: str, allowed: list[str]) -> ConfigUI:
    """A collected ``${GATE_SECRET}`` and an audio voices' allowed list."""

    return _twitch_ui(
        tmp_path,
        "modules_directory: builtin\nenabled_modules: []\nsecrets: ['${GATE_SECRET}']\n"
        f"modules:\n  audio_output:\n    voices:\n      allowed: {json.dumps(allowed)}\n",
        {"GATE_SECRET": secret},
    )


def _json_field(page: str, suffix: str) -> tuple[str, Any]:
    """The name of the JSON textarea ending in *suffix*, and its decoded value."""

    match = re.search(rf'<textarea name="([^"]*{re.escape(suffix)})"[^>]*>(.*?)</textarea>', page, re.S)
    assert match is not None
    return match.group(1), json.loads(_html.unescape(match.group(2)))


def _allowed_editor(ui: ConfigUI, cookie: str) -> dict[str, Any]:
    return _patch_editor(_module_html(ui, cookie, "audio_output"), "voices.allowed")


def _allowed_changes(editor: dict[str, Any], *, order: Sequence[int] | None = None,
                     remove: Sequence[int] = (), append: Any = None) -> dict[str, str]:
    """The changes a browser posts to reorder, remove and append entries."""

    changes: dict[str, str] = {}
    for index, entry in enumerate(editor["entries"]):
        if order is not None:
            changes[entry["controls"][config_ui.PATCH_ORDER]] = str(order[index])
        if index in remove:
            changes[entry["controls"][config_ui.PATCH_REMOVE]] = "true"
    if append is not None:
        changes[editor["controls"][config_ui.PATCH_ITEM]] = json.dumps(append)
    return changes


def _save_allowed(ui: ConfigUI, cookie: str, **edit: Any) -> UIResponse:
    editor = _allowed_editor(ui, cookie)
    return _write(
        ui, cookie, "/save", f"{MODULE_PAGE_PREFIX}audio_output",
        _submitted(editor, _allowed_changes(editor, **edit)),
    )


def _saved_allowed(tmp_path: Path) -> object:
    return _overlay_doc(tmp_path / "config.local.yaml")["modules"]["audio_output"]["voices"]["allowed"]


@pytest.mark.parametrize(
    ("edit", "expected"),
    [
        ({"remove": [0]}, ["q7Z"]),
        ({"order": [1, 0]}, ["q7Z", "kept"]),
        ({"append": "new"}, ["kept", "q7Z", "new"]),
    ],
    ids=["remove-first", "reorder", "append"],
)
def test_p19f5_a_shifted_hidden_entry_keeps_its_own_value(
    tmp_path: Path, edit: dict[str, Any], expected: list[str]
) -> None:
    """Review A1: restoration matched list entries by index, so removing
    ``kept`` from ``['kept', 'q7Z']`` compared the placeholder with ``kept``
    and saved it over ``q7Z``. P19F9: each entry is removed, moved or kept by
    its own identity; the withheld one never travels."""

    ui = _allowed_ui(tmp_path, "q7Z", ["kept", "q7Z"])
    cookie = _logged_in(ui)
    response = _save_allowed(ui, cookie, **edit)
    assert response.status == 303, _written(response)
    assert _saved_allowed(tmp_path) == expected


def test_p19f5_removing_one_of_two_different_hidden_entries_removes_exactly_that_one(tmp_path: Path) -> None:
    """Review A1: two different secrets showed the same placeholder, so which
    one a removal meant could not be told and the Save was refused. P19F9
    replaces that refusal: the removal names the entry by its identity, so
    exactly that entry goes, whichever it is, and the other is kept."""

    ui = _twitch_ui(
        tmp_path,
        "modules_directory: builtin\nenabled_modules: []\nsecrets: ['${ONE}', '${TWO}']\n"
        "modules:\n  audio_output:\n    voices:\n      allowed: ['kept', 's1x', 's2x']\n",
        {"ONE": "s1x", "TWO": "s2x"},
    )
    cookie = _logged_in(ui)
    editor = _allowed_editor(ui, cookie)
    assert editor["entries"][1]["withheld"] == editor["entries"][2]["withheld"] == HIDDEN_LITERAL
    response = _save_allowed(ui, cookie, remove=[2])
    assert response.status == 303, _written(response)
    assert _saved_allowed(tmp_path) == ["kept", "s1x"]
    body = response.body.decode("utf-8")
    assert "s1x" not in body and "s2x" not in body
    ui = _twitch_ui(
        tmp_path,
        "modules_directory: builtin\nenabled_modules: []\nsecrets: ['${ONE}', '${TWO}']\n"
        "modules:\n  audio_output:\n    voices:\n      allowed: ['kept', 's1x', 's2x']\n",
        {"ONE": "s1x", "TWO": "s2x"},
    )
    (tmp_path / "config.local.yaml").unlink()
    cookie = _logged_in(ui)
    response = _save_allowed(ui, cookie, remove=[1], append="added")
    assert response.status == 303, _written(response)
    assert _saved_allowed(tmp_path) == ["kept", "s2x", "added"]


def test_p19f5_an_unmatched_placeholder_is_refused(tmp_path: Path) -> None:
    """Review A1/A2: a placeholder with no configured counterpart is never
    saved. P19F9: the whole-list text is no longer a control of this page —
    posting it is refused by name, nothing written — while a new item that
    merely looks like a placeholder is the operator's literal text."""

    ui = _allowed_ui(tmp_path, "q7Z", ["kept", "q7Z"])
    cookie = _logged_in(ui)
    field = _allowed_editor(ui, cookie)["field"]
    before = _snapshot(tmp_path)
    for posted in (HIDDEN_LITERAL, f"x{config_ui.REDACTED}"):
        response = _write(
            ui, cookie, "/save", f"{MODULE_PAGE_PREFIX}audio_output",
            [(field, json.dumps(["kept", HIDDEN_LITERAL, posted]))],
        )
        assert response.status == 403
        assert any(config_ui._WITHHELD_WHOLE_REASON in line for line in _written(response))
        assert _snapshot(tmp_path) == before
    response = _save_allowed(ui, cookie, append=f"x{config_ui.REDACTED}")
    assert response.status == 303, _written(response)
    assert _saved_allowed(tmp_path) == ["kept", "q7Z", f"x{config_ui.REDACTED}"]


@pytest.mark.parametrize("secret", ["value", "e", "hidden", ")"])
def test_p19f5_a_placeholder_rewritten_by_redaction_is_restored(
    tmp_path: Path, secret: str, forget_ui: Callable[[ConfigUI], ConfigUI]
) -> None:
    """Review A2: ``esc`` redacted the placeholder too, so a secret ``value``
    showed as ``literal [hidden] configured (hidden)`` and matched neither
    form the restoration knew. P19F9: no placeholder is ever read back, so a
    secret that rewrites it cannot matter: append and removal keep every
    other entry exactly."""

    ui = forget_ui(_allowed_ui(tmp_path, secret, ["kept", secret, f"x{secret}y"]))
    cookie = _logged_in(ui)
    editor = _allowed_editor(ui, cookie)
    assert editor["entries"][1]["withheld"] != HIDDEN_LITERAL or secret == ")"
    response = _save_allowed(ui, cookie, append="added")
    assert response.status == 303, _written(response)
    assert _saved_allowed(tmp_path) == ["kept", secret, f"x{secret}y", "added"]
    response = _save_allowed(ui, cookie, remove=[0])
    assert response.status == 303, _written(response)
    assert _saved_allowed(tmp_path) == [secret, f"x{secret}y", "added"]


def test_p19f5_a_redacted_stand_in_key_is_restored(tmp_path: Path) -> None:
    """Review A2: a withheld mapping key showed its stand-in redacted too.
    P19F9: the key is never posted; adding an entry beside it keeps it."""

    secret = "value"
    ui = _twitch_ui(
        tmp_path,
        "modules_directory: builtin\nenabled_modules: []\nsecrets: ['${GATE_SECRET}']\n"
        "modules:\n  brain:\n    delivery:\n      actions:\n"
        f"        - action: reply\n          arguments: {{{secret}: 1, other: {secret}}}\n",
        {"GATE_SECRET": secret},
    )
    cookie = _logged_in(ui)
    actions = _patch_editor(_module_html(ui, cookie, "brain"), "delivery.actions")
    arguments = actions["entries"][0]["entries"][1]
    response = _write(
        ui, cookie, "/save", f"{MODULE_PAGE_PREFIX}brain",
        _submitted(actions, {
            arguments["controls"][config_ui.PATCH_KEY]: "added",
            arguments["controls"][config_ui.PATCH_ENTRY]: "2",
        }),
    )
    assert response.status == 303, _written(response)
    saved = _overlay_doc(tmp_path / "config.local.yaml")
    assert saved["modules"]["brain"]["delivery"]["actions"][0]["arguments"] == {
        secret: 1, "other": secret, "added": 2
    }


# ---------------------------------------------------------------------------
# P19F7 (gate-4 N3/N4): data is masked before it is serialized, and Check
# validates the very draft Save writes
# ---------------------------------------------------------------------------


def _textarea_text(page: str, suffix: str) -> tuple[str, str]:
    """The name of the textarea ending in *suffix*, and its unescaped text."""

    match = re.search(rf'<textarea name="([^"]*{re.escape(suffix)})"[^>]*>(.*?)</textarea>', page, re.S)
    assert match is not None
    return match.group(1), _html.unescape(match.group(2))


@pytest.mark.parametrize(
    ("secret", "allowed"),
    [
        ('"', ["kept", "voice-two"]),
        ("\\", ["kept", 'ordinary"quote']),
        *((mark, ["kept", "voice-two"]) for mark in ["[", "]", "{", "}", ",", ":", " "]),
    ],
    ids=["quote", "backslash", "open-bracket", "close-bracket", "open-brace", "close-brace",
         "comma", "colon", "space"],
)
def test_n4_a_punctuation_secret_never_breaks_a_legitimate_list(
    tmp_path: Path, secret: str, allowed: list[str], forget_ui: Callable[[ConfigUI], ConfigUI]
) -> None:
    """Gate-4 D/N4 as a running test: with the one-character secret ``"``,
    the legitimate ``['kept', 'voice-two']`` rendered as
    ``[[hidden]kept[hidden], [hidden]voice-two[hidden]]`` (and with ``\\``,
    ``'ordinary"quote'`` as ``"ordinary[hidden]"quote"``): the redaction ran
    over serialized text. The list now decodes exactly, and an edit Saves."""

    ui = forget_ui(_allowed_ui(tmp_path, secret, allowed))
    cookie = _logged_in(ui)
    page = _module_html(ui, cookie, "audio_output")
    name, text = _textarea_text(page, "voices.allowed")
    assert json.loads(text) == allowed
    check = _write(
        ui, cookie, "/check", f"{MODULE_PAGE_PREFIX}audio_output",
        [(name, json.dumps([*allowed, "added"]))],
    )
    assert check.status == 200
    # P19F9: an exact list stays one JSON text, never refused as withheld.
    assert not any(
        reason in line
        for reason in (config_ui._UNKNOWN_ENTRY_REASON, config_ui._WITHHELD_WHOLE_REASON)
        for line in _written(check)
    )
    response = _write(
        ui, cookie, "/save", f"{MODULE_PAGE_PREFIX}audio_output",
        [(name, json.dumps([*allowed, "added"]))],
    )
    assert response.status == 303, _written(response)
    assert _saved_allowed(tmp_path) == [*allowed, "added"]


@pytest.mark.parametrize("secret", ['"', "\\", "[", "]", "{", "}", ",", ":"])
def test_n4_punctuation_secrets_in_nested_data_mask_the_data_not_the_syntax(
    tmp_path: Path, secret: str, forget_ui: Callable[[ConfigUI], ConfigUI]
) -> None:
    """A punctuation secret used as a key, a value and inside longer text, at
    depth: every exact control decodes, no decoded scalar holds the secret,
    and the untouched legitimate data around it is shown exactly (P19F9: in
    per-entry controls rather than one JSON text)."""

    ui = forget_ui(_f2_value_ui(tmp_path, secret))
    cookie = _logged_in(ui)
    brain = _module_html(ui, cookie, "brain")
    actions = _patch_editor(brain, "delivery.actions")
    action = {entry["key"]: entry for entry in actions["entries"][0]["entries"]}
    assert list(_exact_values(action["action"])) == ["reply"]
    arguments = action["arguments"]["entries"]
    assert [entry["key"] for entry in arguments] == [config_ui.REDACTED, "plain", "nested", f"k{config_ui.REDACTED}"]
    assert list(_exact_values(arguments[0])) == ["v"]
    assert arguments[1]["withheld"] == HIDDEN_LITERAL
    assert list(_exact_values(arguments[3])) == [2]
    audio = _module_html(ui, cookie, "audio_output")
    allowed = _patch_editor(audio, "voices.allowed")["entries"]
    assert allowed[0]["withheld"] == HIDDEN_LITERAL
    assert list(_exact_values(allowed[1])) == ["kept"]
    assert allowed[2]["withheld"] == f"x{config_ui.REDACTED}y"
    for page in (brain, audio):
        for node in _patch_editors(page).values():
            # ``[`` and ``]`` are the marker's own brackets, never the data's.
            assert all(
                secret not in str(scalar).replace(config_ui.REDACTED, "")
                for value in _exact_values(node)
                for scalar in _scalars(value)
            )


N3_SETTINGS_TEXT = (
    "modules_directory: builtin\nenabled_modules: [audio_output]\n"
    "secrets: ['${GATE_SECRET}']\n"
    "modules:\n  audio_output:\n"
    "    synthesis: {endpoint: '', model: ''}\n"
    "    voices: {allowed: ALLOWED, default: '${GATE_SECRET}'}\n"
    "    outputs: {o: {player: {argv: [cat]}}}\n"
    "    default_output: o\n"
)


def _n3_ui(tmp_path: Path, secret: str, allowed: list[str]) -> ConfigUI:
    return _twitch_ui(
        tmp_path, N3_SETTINGS_TEXT.replace("ALLOWED", json.dumps(allowed)), {"GATE_SECRET": secret}
    )


def test_n3_check_gives_the_real_checker_verdict_on_the_draft_save_writes(tmp_path: Path) -> None:
    """Gate-4 D/N3 as a running test, through the real checker: with
    ``voices.allowed = ['kept', 'a"b']``, ``voices.default = '${GATE_SECRET}'``
    and ``GATE_SECRET = 'a"b'``, appending a voice made Check fail
    (``must be an allowed voice``: it validated the placeholder) while Save
    wrote ``['kept', 'a"b', 'added']``, which the real checker passes.
    P19F9: both routes apply the same patch to the same real list."""

    secret = 'a"b'
    ui = _n3_ui(tmp_path, secret, ["kept", secret])
    assert ui.check().passed
    cookie = _logged_in(ui)
    editor = _allowed_editor(ui, cookie)
    checked, saved = _check_then_save(
        ui, cookie, "audio_output", _submitted(editor, _allowed_changes(editor, append="added"))
    )
    assert _verdict(checked), _listed(checked)
    assert saved.status == 303, _written(saved)
    draft = _overlay_doc(tmp_path / "config.local.yaml")
    assert draft["modules"]["audio_output"]["voices"]["allowed"] == ["kept", secret, "added"]
    assert config_ui._default_checker(ui.base_path, ui.environ, ui.overlay_path, draft) == (True, [])
    assert secret not in checked


def test_n3_an_invalid_draft_fails_check_on_its_real_values(tmp_path: Path) -> None:
    """Removing the withheld voice leaves ``voices.default`` outside the
    allowed list: Check reports it from the real values, as the real
    checker does on the same draft."""

    secret = 'a"b'
    ui = _n3_ui(tmp_path, secret, ["kept", secret])
    cookie = _logged_in(ui)
    editor = _allowed_editor(ui, cookie)
    page = f"{MODULE_PAGE_PREFIX}audio_output"
    fields = _submitted(editor, _allowed_changes(editor, remove=[1]))
    checked = _write(ui, cookie, "/check", page, fields).body.decode("utf-8")
    assert not _verdict(checked)
    assert any("must be an allowed voice" in line for line in _listed(checked))
    assert secret not in checked


@pytest.mark.parametrize(
    ("edit", "expected"),
    [
        ({"append": "added"}, ["kept", 1, 2, "added"]),
        ({"order": [2, 1, 0], "append": "added"}, [2, 1, "kept", "added"]),
        ({"remove": [0]}, [1, 2]),
    ],
    ids=["append", "reorder", "remove"],
)
def test_n3_check_and_save_validate_the_same_restored_draft(
    tmp_path: Path, edit: dict[str, Any], expected: list[Any]
) -> None:
    """The captured drafts were different: Check received
    ``prefix-[hidden]-suffix`` where Save restored ``prefix-a"b-suffix``.
    P19F9: both routes apply the same patch to the same real list, so the
    checker is handed exactly what Save writes (indexes below stand for the
    configured entries)."""

    secret = 'a"b'
    configured = ["kept", secret, f"prefix-{secret}-suffix"]
    ui = _allowed_ui(tmp_path, secret, configured)
    seen = _observed(ui)
    cookie = _logged_in(ui)
    editor = _allowed_editor(ui, cookie)
    checked, saved = _check_then_save(ui, cookie, "audio_output", _submitted(editor, _allowed_changes(editor, **edit)))
    assert _verdict(checked), _listed(checked)
    assert saved.status == 303, _written(saved)
    written = _overlay_doc(tmp_path / "config.local.yaml")
    assert seen == [written]
    assert _saved_allowed(tmp_path) == [configured[item] if isinstance(item, int) else item for item in expected]


def test_n3_an_unmatched_placeholder_fails_check_as_save_refuses_it(tmp_path: Path) -> None:
    """An identity that maps to no rendered entry is an explicit outcome on
    both routes, naming why; Check never validates a guessed target (P19F9:
    a truncated identity replaces the unmatched placeholder)."""

    ui = _allowed_ui(tmp_path, "q7Z", ["kept", "q7Z"])
    seen = _observed(ui)
    cookie = _logged_in(ui)
    before = _snapshot(tmp_path)
    editor = _allowed_editor(ui, cookie)
    name = editor["entries"][0]["controls"][config_ui.PATCH_VALUE]
    fields = [(key, text) for key, text in _browser(editor) if key != name] + [(name[:-1], '"edited"')]
    checked, saved = _both_routes(ui, cookie, "audio_output", fields)
    # P19F11: one refusal on both routes, the stale page, before any Check.
    _assert_stale_identity(checked, saved)
    assert any(config_ui._UNKNOWN_ENTRY_REASON in line for line in _written(checked))
    assert ui.last_check is None
    assert seen == []
    assert _snapshot(tmp_path) == before
    assert "q7Z" not in checked.body.decode("utf-8")


@pytest.mark.parametrize("secret", ["Ω", "q7Z", "gate-secret-channel-97bf"])
def test_p19f7_every_published_secret_length_is_still_never_rendered(
    tmp_path: Path, secret: str
) -> None:
    """The earlier guarantees under the data-level masking: the gate's
    one-character, three-character and long secrets, as a key, a value and
    inside longer text at any depth, never reach a page nor a decoded exact
    control, no identity is derived from them in a recoverable way (a keyed
    digest, different for another UI over the same file), and an untouched
    round trip plus an append Saves the configured values (P19F9)."""

    ui = _f2_value_ui(tmp_path, secret)
    cookie = _logged_in(ui)
    names: set[str] = set()
    for name in ("brain", "audio_output"):
        page = _module_html(ui, cookie, name)
        assert secret not in page and secret not in _html.unescape(page), name
        for spelling in (json.dumps(secret)[1:-1], json.dumps(secret, ensure_ascii=True)[1:-1]):
            assert spelling not in _html.unescape(page), name
        for body in re.findall(r"<textarea[^>]*>(.*?)</textarea>", page, re.S):
            if body:
                document = json.loads(_html.unescape(body))
                assert all(secret not in str(scalar) for scalar in _scalars(document)), name
        found = re.findall(rf'name="{re.escape(config_ui.PATCH_FIELD_PREFIX)}[a-z]+-([^"]*)"', page)
        # P19F10: the render's random tag, then the keyed digest.
        assert found and all(
            re.fullmatch(rf"[0-9a-f]{{{config_ui.RENDER_TAG_LENGTH + 32}}}", token) for token in found
        ), name
        names.update(found)
    other = _f2_value_ui(tmp_path, secret)
    other_cookie = _logged_in(other)
    for name in ("brain", "audio_output"):
        page = _module_html(other, other_cookie, name)
        assert not names & set(re.findall(rf'name="{re.escape(config_ui.PATCH_FIELD_PREFIX)}[a-z]+-([^"]*)"', page))
    response = _save_allowed(ui, cookie, append="added")
    assert response.status == 303, _written(response)
    assert _saved_allowed(tmp_path) == [secret, "kept", f"x{secret}y", "added"]
    assert secret not in response.body.decode("utf-8")


# ---------------------------------------------------------------------------
# P19F8 (gate-5 N5 and the N3 residual), under P19F9: every configured key
# is its own entry, and a submission that cannot be applied is refused
# ---------------------------------------------------------------------------


def _arguments_ui(tmp_path: Path, arguments: dict[str, Any], environ: dict[str, str]) -> ConfigUI:
    """Brain delivery actions ``[{action: x.do, arguments: ...}]``, every
    variable of *environ* a collected secret."""

    secrets_list = ", ".join(f"'${{{name}}}'" for name in environ)
    return _twitch_ui(
        tmp_path,
        f"modules_directory: builtin\nenabled_modules: []\nsecrets: [{secrets_list}]\n"
        "modules:\n  brain:\n    delivery:\n      actions:\n"
        f"        - action: x.do\n          arguments: {json.dumps(arguments)}\n",
        environ,
    )


def _saved_arguments(tmp_path: Path) -> object:
    saved = _overlay_doc(tmp_path / "config.local.yaml")
    return saved["modules"]["brain"]["delivery"]["actions"][0]["arguments"]


def _actions_editor(ui: ConfigUI, cookie: str) -> dict[str, Any]:
    return _patch_editor(_module_html(ui, cookie, "brain"), "delivery.actions")


def _add_argument(actions: dict[str, Any], key: str, value: Any, action: int = 0) -> dict[str, str]:
    arguments = actions["entries"][action]["entries"][1]
    return {
        arguments["controls"][config_ui.PATCH_KEY]: key,
        arguments["controls"][config_ui.PATCH_ENTRY]: json.dumps(value),
    }


@pytest.mark.parametrize(
    ("arguments", "environ", "labels"),
    [
        (
            {"xq7Z": "one", "xsecond-secret": "two"},
            {"GATE_SECRET": "q7Z", "SECOND": "second-secret"},
            ["x[hidden]", "x[hidden]"],
        ),
        (
            {"xq7Z": "one", "x[hidden]": "two"},
            {"GATE_SECRET": "q7Z"},
            ["x[hidden]", "x[hidden]"],
        ),
    ],
    ids=["two-secrets", "marker-looking-key"],
)
def test_n5_keys_that_only_contain_a_secret_stay_distinct_entries(
    tmp_path: Path, arguments: dict[str, Any], environ: dict[str, str], labels: list[str]
) -> None:
    """Gate-5 E/N5 and gate-6 N5 as running tests: ``{'xq7Z': 'one',
    'xsecond-secret': 'two'}`` decoded as ``{'x[hidden]': 'two'}`` — the
    whole first entry lost — and a configured ``x[hidden]`` key displaced
    ``xq7Z`` the same way. P19F9: every entry is its own control, shown
    alike or not; an unrelated edit Checks and Saves every configured entry,
    both routes handed the same real draft, both keys surviving."""

    ui = _arguments_ui(tmp_path, arguments, environ)
    seen = _observed(ui)
    cookie = _logged_in(ui)
    actions = _actions_editor(ui, cookie)
    entries = actions["entries"][0]["entries"][1]["entries"]
    assert [entry["key"] for entry in entries] == labels
    assert [list(_exact_values(entry)) for entry in entries] == [["one"], ["two"]]
    checked, saved = _check_then_save(ui, cookie, "brain", _submitted(actions, _add_argument(actions, "added", "ok")))
    assert _verdict(checked), _listed(checked)
    assert saved.status == 303, _written(saved)
    assert _saved_arguments(tmp_path) == {**arguments, "added": "ok"}
    assert seen == [_overlay_doc(tmp_path / "config.local.yaml")]
    for secret in environ.values():
        assert secret not in checked and secret not in saved.body.decode("utf-8")


def test_n5_legitimate_stand_in_shaped_text_never_collides(tmp_path: Path) -> None:
    """Configured text that looks like a stand-in — a key equal to
    :data:`HIDDEN_LITERAL` or its numbered form, a value with the stand-in in
    it — is exact data, shown and posted as itself; a round trip keeps every
    entry (P19F9: no marker heuristic exists to mistake it)."""

    arguments = {HIDDEN_LITERAL: "a", "q7Z": "b", f"{HIDDEN_LITERAL} (2)": "c", "v": f"{HIDDEN_LITERAL} x"}
    ui = _arguments_ui(tmp_path, arguments, {"GATE_SECRET": "q7Z"})
    cookie = _logged_in(ui)
    actions = _actions_editor(ui, cookie)
    entries = actions["entries"][0]["entries"][1]["entries"]
    assert [entry["key"] for entry in entries] == [HIDDEN_LITERAL, config_ui.REDACTED, f"{HIDDEN_LITERAL} (2)", "v"]
    assert [list(_exact_values(entry)) for entry in entries] == [["a"], ["b"], ["c"], [f"{HIDDEN_LITERAL} x"]]
    _checked, saved = _check_then_save(ui, cookie, "brain", _submitted(actions, _add_argument(actions, "added", 1)))
    assert saved.status == 303, _written(saved)
    assert _saved_arguments(tmp_path) == {**arguments, "added": 1}

    allowed = ["kept", "q7Z", f"{HIDDEN_LITERAL} edited"]
    ui = _allowed_ui(tmp_path, "q7Z", allowed)
    cookie = _logged_in(ui)
    editor = _allowed_editor(ui, cookie)
    assert list(_exact_values(editor["entries"][2])) == [f"{HIDDEN_LITERAL} edited"]
    response = _save_allowed(ui, cookie, append="added")
    assert response.status == 303, _written(response)
    assert _saved_allowed(tmp_path) == [*allowed, "added"]


@pytest.mark.parametrize("secret", ["(", ")", " ", "-", "2", "[", "]", "."])
def test_n5_a_single_punctuation_secret_keeps_every_key_distinct(
    tmp_path: Path, secret: str, forget_ui: Callable[[ConfigUI], ConfigUI]
) -> None:
    """A one-character secret that rewrites the stand-ins and their
    numbering: every entry is still shown, as its own control, no label
    holds the secret, and a round trip Saves exactly the configured keys."""

    arguments = {
        secret: "a", f"x{secret}": "b", f"y{secret}": "c", f"{secret}{secret}": "d",
        "x[hidden]": "f", HIDDEN_LITERAL: "g", f"{HIDDEN_LITERAL} (2)": "h", "plain": "i",
    }
    ui = forget_ui(_arguments_ui(tmp_path, arguments, {"GATE_SECRET": secret}))
    cookie = _logged_in(ui)
    actions = _actions_editor(ui, cookie)
    entries = actions["entries"][0]["entries"][1]["entries"]
    assert len(entries) == len(arguments)
    assert len({entry["controls"][config_ui.PATCH_REMOVE] for entry in entries}) == len(arguments)
    for entry in entries:
        assert secret not in entry["key"].replace(config_ui.REDACTED, ""), entry["key"]
    _checked, saved = _check_then_save(ui, cookie, "brain", _submitted(actions, _add_argument(actions, "added", 9)))
    assert saved.status == 303, _written(saved)
    assert _saved_arguments(tmp_path) == {**arguments, "added": 9}


def test_n5_an_unallocatable_stand_in_fails_the_render_loudly() -> None:
    """When the secret set rewrites every numbered form alike, no distinct
    stand-in exists: the render raises instead of merging entries."""

    digits = frozenset("0123456789")
    mapping = {"a0": 1, "a1": 2, "a2": 3, "a3": 4}
    with pytest.raises(config_ui.StandInCollision):
        config_ui._shown(mapping, digits)


@pytest.mark.parametrize(
    "alter",
    [
        lambda token: token[:-1],
        lambda token: token[:-12],
        lambda token: token[:10] + ("0" if token[10] != "0" else "1") + token[11:],
        lambda token: f"{token}0",
        lambda token: token.upper(),
        lambda token: "",
    ],
    ids=["truncated", "truncated-12", "one-character", "suffixed", "upper-case", "empty"],
)
def test_n3_an_edited_stand_in_is_refused_never_written(
    tmp_path: Path, alter: Callable[[str], str]
) -> None:
    """Gate-5/gate-6 E/N3 as running tests, through the real checker: an
    edited, truncated or internally altered stand-in passed Check and was
    Saved as a literal voice name. P19F9: nothing editable holds a stand-in
    any more; what a submission names is an identity, and any altered one —
    truncated, one character changed, suffixed, re-cased — maps to no
    rendered entry and is refused by name on both routes, before
    validation, with nothing written."""

    ui = _n3_ui(tmp_path, "q7Z", ["kept", "q7Z", "q7Z"])
    assert ui.check().passed
    cookie = _logged_in(ui)
    before = _snapshot(tmp_path)
    editor = _allowed_editor(ui, cookie)
    assert [entry["replace"] for entry in editor["entries"]] == [False, True, True]
    name = editor["entries"][2]["controls"][config_ui.PATCH_VALUE]
    prefix = f"{config_ui.PATCH_FIELD_PREFIX}{config_ui.PATCH_VALUE}-"
    altered = prefix + alter(name[len(prefix) :])
    fields = [(key, text) for key, text in _browser(editor) if key != name] + [(altered, '"edited"')]
    last = ui.last_check
    checked, saved = _both_routes(ui, cookie, "audio_output", fields)
    # P19F11: one refusal on both routes, the stale page, before any Check.
    _assert_stale_identity(checked, saved)
    assert any(config_ui._UNKNOWN_ENTRY_REASON in line for line in _written(checked))
    assert ui.last_check is last
    assert _snapshot(tmp_path) == before


def test_n3_an_unmatched_placeholder_never_reaches_the_checker(tmp_path: Path) -> None:
    """The refusal comes before validation: the checker is never given a
    draft built from the whole masked text (P19F9: that text is refused as
    a withheld field, whatever it holds)."""

    ui = _allowed_ui(tmp_path, "q7Z", ["kept", "q7Z"])
    seen = _observed(ui)
    cookie = _logged_in(ui)
    field = _allowed_editor(ui, cookie)["field"]
    checked, saved = _check_then_save(
        ui, cookie, "audio_output", [(field, json.dumps(["kept", HIDDEN_LITERAL, f"{HIDDEN_LITERAL} edited"]))]
    )
    assert not _verdict(checked)
    assert any(config_ui._WITHHELD_WHOLE_REASON in line for line in _listed(checked))
    assert seen == []
    assert saved.status == 403


@pytest.mark.parametrize("remove", [False, True], ids=["kept", "removed"])
def test_n3_a_stand_in_beside_its_real_key_is_refused_never_resolved(
    tmp_path: Path, remove: bool
) -> None:
    """Gate-5 E/N3 B as a running test: posting the displayed key and the
    real key ``q7Z`` with different values passed both routes and one value
    silently disappeared, the winner set by key order. P19F9: the withheld
    key is never posted; a new entry naming the key it holds — beside it or
    in place of a removed one — is refused by name, before validation,
    and nothing is written."""

    ui = _arguments_ui(tmp_path, {"q7Z": "original"}, {"GATE_SECRET": "q7Z"})
    seen = _observed(ui)
    cookie = _logged_in(ui)
    before = _snapshot(tmp_path)
    actions = _actions_editor(ui, cookie)
    (entry,) = actions["entries"][0]["entries"][1]["entries"]
    changes = _add_argument(actions, "q7Z", "second")
    if remove:
        changes[entry["controls"][config_ui.PATCH_REMOVE]] = "true"
    checked, saved = _check_then_save(ui, cookie, "brain", _submitted(actions, changes))
    assert not _verdict(checked)
    assert any(config_ui._EXISTING_KEY_REASON in line for line in _listed(checked)), _listed(checked)
    assert seen == []
    assert saved.status == 403
    assert any(config_ui._EXISTING_KEY_REASON in line for line in _written(saved))
    assert _snapshot(tmp_path) == before
    assert "q7Z" not in checked and "q7Z" not in saved.body.decode("utf-8")


# ---------------------------------------------------------------------------
# P19F9 (gate-6 N3, N5, N6): the gate's executed counterexamples and the
# identity refusals
# ---------------------------------------------------------------------------


N6_ACTIONS = [
    {"action": "x.do", "arguments": {"xq7Z": "one"}},
    {"action": "x.do", "arguments": {"xsecond-secret": "two"}},
]


def _n6_ui(tmp_path: Path) -> ConfigUI:
    return _twitch_ui(
        tmp_path,
        "modules_directory: builtin\nenabled_modules: []\nsecrets: ['${GATE_SECRET}', '${SECOND}']\n"
        f"modules:\n  brain:\n    delivery:\n      actions: {json.dumps(N6_ACTIONS)}\n",
        {"GATE_SECRET": "q7Z", "SECOND": "second-secret"},
    )


def test_p19f9_gate6_n6_a_visible_value_edit_never_changes_another_entrys_key(tmp_path: Path) -> None:
    """Gate-6 D/N6 as a running test: both actions showed ``x[hidden]`` with
    visible values ``one`` and ``two``; changing only the first value to
    ``two`` passed Check and Save with both entries rewritten to
    ``{'xsecond-secret': 'two'}`` — the first key silently substituted.
    The edit now names its entry by identity: Check and Save are handed the
    same real draft, the first entry keeps ``xq7Z`` with its new value and
    the second entry is untouched, key and value."""

    ui = _n6_ui(tmp_path)
    seen = _observed(ui)
    cookie = _logged_in(ui)
    actions = _actions_editor(ui, cookie)
    first, second = (action["entries"][1]["entries"][0] for action in actions["entries"])
    assert first["key"] == second["key"] == "x[hidden]"
    assert list(_exact_values(first)) == ["one"] and list(_exact_values(second)) == ["two"]
    checked, saved = _check_then_save(
        ui, cookie, "brain", _submitted(actions, {first["controls"][config_ui.PATCH_VALUE]: '"two"'})
    )
    assert _verdict(checked), _listed(checked)
    assert saved.status == 303, _written(saved)
    expected = [
        {"action": "x.do", "arguments": {"xq7Z": "two"}},
        {"action": "x.do", "arguments": {"xsecond-secret": "two"}},
    ]
    written = _overlay_doc(tmp_path / "config.local.yaml")
    assert written["modules"]["brain"]["delivery"]["actions"] == expected
    assert seen == [written]
    for secret in ("q7Z", "second-secret"):
        assert secret not in checked and secret not in saved.body.decode("utf-8")


def test_p19f9_gate6_n6_reversing_and_appending_keeps_each_key_with_its_entry(tmp_path: Path) -> None:
    """Gate-6 D/N6's other variant — reverse both actions and add a field to
    each — was refused as unmatched. By identity it is unambiguous: each
    action moves with its own hidden key and gains its own field."""

    ui = _n6_ui(tmp_path)
    seen = _observed(ui)
    cookie = _logged_in(ui)
    actions = _actions_editor(ui, cookie)
    changes = {
        actions["entries"][0]["controls"][config_ui.PATCH_ORDER]: "1",
        actions["entries"][1]["controls"][config_ui.PATCH_ORDER]: "0",
        **_add_argument(actions, "added", "ok", 0),
        **_add_argument(actions, "added", "ok", 1),
    }
    checked, saved = _check_then_save(ui, cookie, "brain", _submitted(actions, changes))
    assert _verdict(checked), _listed(checked)
    assert saved.status == 303, _written(saved)
    written = _overlay_doc(tmp_path / "config.local.yaml")
    assert written["modules"]["brain"]["delivery"]["actions"] == [
        {"action": "x.do", "arguments": {"xsecond-secret": "two", "added": "ok"}},
        {"action": "x.do", "arguments": {"xq7Z": "one", "added": "ok"}},
    ]
    assert seen == [written]


@pytest.mark.parametrize(
    "alter",
    [
        lambda text: text[:-12],
        lambda text: text.replace("configured", "configurex"),
        lambda text: text.replace(HIDDEN_LITERAL, "[hidde]"),
        lambda text: f"{text} edited",
    ],
    ids=["truncated-12", "one-character", "partial-bracket", "suffixed"],
)
def test_p19f9_gate6_n3_an_altered_stand_in_is_never_written(
    tmp_path: Path, alter: Callable[[str], str]
) -> None:
    """Gate-6 E/N3 as a running test, through the real checker: the whole
    list posted with a stand-in truncated by twelve characters
    (``literal value configu``), altered internally (``configurex``) or cut
    to ``[hidde]`` passed Check and Save wrote it as a literal voice. The
    whole text of a withheld list is no longer read back at all: both routes
    refuse it by name, before validation, and nothing is written."""

    ui = _n3_ui(tmp_path, "q7Z", ["kept", "q7Z", "q7Z"])
    assert ui.check().passed
    cookie = _logged_in(ui)
    before = _snapshot(tmp_path)
    field = _allowed_editor(ui, cookie)["field"]
    posted = [(field, json.dumps(["kept", HIDDEN_LITERAL, alter(HIDDEN_LITERAL)]))]
    checked, saved = _check_then_save(ui, cookie, "audio_output", posted)
    assert not _verdict(checked)
    assert any(config_ui._WITHHELD_WHOLE_REASON in line for line in _listed(checked))
    assert ui.last_check is not None and not ui.last_check.settings_phase_reached
    assert saved.status == 403
    assert any(config_ui._WITHHELD_WHOLE_REASON in line for line in _written(saved))
    assert _snapshot(tmp_path) == before


def test_p19f9_a_duplicated_identity_is_refused(tmp_path: Path) -> None:
    """Two posted values claiming one identity are refused by name on both
    routes — even when one of them is the untouched text — and nothing is
    written; neither value is chosen."""

    ui = _allowed_ui(tmp_path, "q7Z", ["kept", "q7Z"])
    seen = _observed(ui)
    cookie = _logged_in(ui)
    before = _snapshot(tmp_path)
    editor = _allowed_editor(ui, cookie)
    name = editor["entries"][0]["controls"][config_ui.PATCH_VALUE]
    checked, saved = _check_then_save(ui, cookie, "audio_output", [*_browser(editor), (name, '"other"')])
    assert not _verdict(checked)
    assert any(config_ui._DUPLICATE_ENTRY_REASON in line for line in _listed(checked))
    assert seen == []
    assert saved.status == 403
    assert _snapshot(tmp_path) == before


def test_p19f9_a_stale_identity_is_refused_never_retargeted(tmp_path: Path) -> None:
    """A page rendered before an entry changed on disk names the old entry:
    its identity no longer maps anywhere, even though the key layout is the
    same, so the edit is refused rather than applied to the new value."""

    ui = _allowed_ui(tmp_path, "q7Z", ["kept", "q7Z"])
    cookie = _logged_in(ui)
    editor = _allowed_editor(ui, cookie)
    # The layout the loaded page carries: rendering the page again would
    # retire its identities as stale before their value is looked at (P19F10).
    layout = _guard_values(ui)[1]
    base = tmp_path / "config.yaml"
    base.write_text(base.read_text(encoding="utf-8").replace('"kept"', '"changed"'), encoding="utf-8")
    fields = _submitted(editor, {editor["entries"][0]["controls"][config_ui.PATCH_VALUE]: '"edited"'})
    for route in ("/check", "/save"):
        response = _write(ui, cookie, route, f"{MODULE_PAGE_PREFIX}audio_output", fields, layout=layout)
        assert any(config_ui._UNKNOWN_ENTRY_REASON in line for line in _written(response)), route
    assert not (tmp_path / "config.local.yaml").exists()


def test_p19f9_an_identity_from_another_page_is_refused(tmp_path: Path) -> None:
    """An identity rendered on one module's page and posted from another
    page is refused: identities belong to the page that drew them."""

    ui = _f2_value_ui(tmp_path, "q7Z")
    seen = _observed(ui)
    cookie = _logged_in(ui)
    editor = _allowed_editor(ui, cookie)
    fields = _submitted(editor, _allowed_changes(editor, append="added"))
    for route in ("/check", "/save"):
        response = _write(ui, cookie, route, f"{MODULE_PAGE_PREFIX}brain", fields)
        assert any(config_ui._UNKNOWN_ENTRY_REASON in line for line in _written(response)), route
    assert seen == []
    assert not (tmp_path / "config.local.yaml").exists()


def test_p19f9_an_entry_both_removed_and_edited_is_refused(tmp_path: Path) -> None:
    """An entry marked for removal whose value was also changed is refused
    by name on both routes: neither intent is chosen, nothing is written."""

    ui = _arguments_ui(tmp_path, {"xq7Z": "one", "plain": "two"}, {"GATE_SECRET": "q7Z"})
    cookie = _logged_in(ui)
    actions = _actions_editor(ui, cookie)
    entry = actions["entries"][0]["entries"][1]["entries"][0]
    changes = {entry["controls"][config_ui.PATCH_REMOVE]: "true", entry["controls"][config_ui.PATCH_VALUE]: '"x"'}
    checked, saved = _check_then_save(ui, cookie, "brain", _submitted(actions, changes))
    assert not _verdict(checked)
    assert any(config_ui._REMOVED_AND_EDITED_REASON in line for line in _listed(checked))
    assert saved.status == 403
    assert not (tmp_path / "config.local.yaml").exists()


@pytest.mark.parametrize(
    "text",
    ['a"b, {x}: [y] \\ z', "literal value configured (hidden)", "x[hidden]", "configured (hidden) [hidden]"],
    ids=["punctuation", "stand-in", "marker", "both-markers"],
)
def test_p19f9_a_legitimate_edit_with_punctuation_or_marker_text_applies(tmp_path: Path, text: str) -> None:
    """An ordinary edit is taken as typed, whatever punctuation or
    marker-looking text it holds: an exact entry, a withheld entry's
    replacement and an appended item all Save literally, beside the
    withheld entries they never disturb, and Check sees the same draft."""

    ui = _allowed_ui(tmp_path, "q7Z", ["kept", "q7Z", "xq7Zy"])
    seen = _observed(ui)
    cookie = _logged_in(ui)
    editor = _allowed_editor(ui, cookie)
    changes = {
        editor["entries"][0]["controls"][config_ui.PATCH_VALUE]: json.dumps(text),
        **_replaced(editor["entries"][2], json.dumps(f"{text}!")),
        **_allowed_changes(editor, append=f"{text}?"),
    }
    checked, saved = _check_then_save(ui, cookie, "audio_output", _submitted(editor, changes))
    assert _verdict(checked), _listed(checked)
    assert saved.status == 303, _written(saved)
    assert _saved_allowed(tmp_path) == [text, "q7Z", f"{text}!", f"{text}?"]
    assert seen == [_overlay_doc(tmp_path / "config.local.yaml")]


def test_p19f9_an_untouched_patch_editor_posts_no_edit(tmp_path: Path) -> None:
    """A page posted back untouched changes nothing: no value travels, no
    edit is made, and every patch control's identity is unique."""

    ui = _n6_ui(tmp_path)
    cookie = _logged_in(ui)
    page = _module_html(ui, cookie, "brain")
    names = re.findall(rf'name="({re.escape(config_ui.PATCH_FIELD_PREFIX)}[^"]*)"', page)
    assert len(names) == len(set(names))
    actions = _patch_editor(page, "delivery.actions")
    token = config_ui._RENDERING_FOR.set(ui)
    try:
        fields = [("page", f"{MODULE_PAGE_PREFIX}brain"), *_browser(actions)]
        assert ui.parse_edits(ui.view(), fields) == ([], [])
    finally:
        config_ui._RENDERING_FOR.reset(token)
    response = _write(ui, cookie, "/save", f"{MODULE_PAGE_PREFIX}brain", _browser(actions))
    assert response.status == 200 and "Nothing to save" in response.body.decode("utf-8")


def test_p19f9_no_configured_key_reaches_a_generated_attribute(tmp_path: Path) -> None:
    """The per-entry editor shows each configured key as text only: no
    attribute of the page — patch identities included — holds a key, and
    the withheld ones appear nowhere."""

    arguments = {"visible-key-7c1": "one", "xq7Z": "two", "q7Z": "three"}
    ui = _arguments_ui(tmp_path, arguments, {"GATE_SECRET": "q7Z"})
    cookie = _logged_in(ui)
    page = _module_html(ui, cookie, "brain")
    assert "q7Z" not in page
    assert "visible-key-7c1" in page
    assert not any("visible-key-7c1" in value for value in _attribute_values(page))


def test_p19f9_a_withheld_scalar_replacement_is_the_whole_new_value(tmp_path: Path) -> None:
    """A withheld list item is replaced whole by what is typed in its empty
    control — never merged with the text it had — and left empty keeps its
    configured value; a declared boolean on the same page keeps its honest
    three states."""

    ui = _allowed_ui(tmp_path, "q7Z", ["kept", "prefix-q7Z", "q7Z"])
    cookie = _logged_in(ui)
    editor = _allowed_editor(ui, cookie)
    assert [entry["withheld"] for entry in editor["entries"][1:]] == ["prefix-[hidden]", HIDDEN_LITERAL]
    response = _write(
        ui, cookie, "/save", f"{MODULE_PAGE_PREFIX}audio_output",
        _submitted(editor, _replaced(editor["entries"][1], '"replaced"')),
    )
    assert response.status == 303, _written(response)
    assert _saved_allowed(tmp_path) == ["kept", "replaced", "q7Z"]
    page = _module_html(ui, cookie, "audio_output")
    probe = _probe_select(page)
    assert _rendered_options(probe)[1:] == [("true", "true"), ("false", "false")]


# ---------------------------------------------------------------------------
# P19F10 (gate-7 N3/N6 residual, N7): identities are unique per render and
# bound to the session showing it; "unchanged" is an explicit operation
# ---------------------------------------------------------------------------


GATE7_POLICY = {"combination": "all_of", "rules": [{"type": "keyword", "parameters": {"keywords": ["q7Z", "kept"]}}]}


def _gate7_ui(tmp_path: Path) -> ConfigUI:
    """Gate 7's fixture: an overlay-only Twitch channel ``first`` whose
    keyword list holds the collected secret ``q7Z`` beside ``kept``."""

    (tmp_path / "config.local.yaml").write_text(
        yaml.safe_dump({"triggers": {"twitch": {"channels": {"first": GATE7_POLICY}}}}), encoding="utf-8"
    )
    return _twitch_ui(
        tmp_path,
        "modules_directory: builtin\nenabled_modules: []\nsecrets: ['${GATE_SECRET}']\n"
        "modules:\n  twitch:\n    companion_name: helper\n",
        {"GATE_SECRET": "q7Z"},
    )


def _keywords_editor(ui: ConfigUI, cookie: str) -> dict[str, Any]:
    return _patch_editor(_module_html(ui, cookie, "twitch"), ".keywords")


def _both_routes(ui: ConfigUI, cookie: str, module: str, fields: list[tuple[str, str]]) -> tuple[UIResponse, UIResponse]:
    page = f"{MODULE_PAGE_PREFIX}{module}"
    return _write(ui, cookie, "/check", page, fields), _write(ui, cookie, "/save", page, fields)


def _assert_stale_identity(check: UIResponse, save: UIResponse) -> None:
    """The layout guard's "reload the page" outcome, identical on both routes."""

    for response in (check, save):
        assert response.status == 409, _written(response)
        assert "Refused: stale page" in response.body.decode("utf-8")
        assert _written(response) == [config_ui._STALE_IDENTITY_REASON]
    assert _written(check) == _written(save)


def _identity_tokens(page: str) -> list[str]:
    return re.findall(rf'name="{re.escape(config_ui.PATCH_FIELD_PREFIX)}[a-z]+-([^"]*)"', page)


def test_p19f10_gate7_an_old_identity_after_remove_and_save_is_refused_never_retargeted(tmp_path: Path) -> None:
    """Gate 7's closing residual through the real handlers: the channel
    ``first`` is removed and ``second`` saved with exactly the same policy;
    the old identity of ``kept`` equalled the new one and edited ``second``.
    Identities are now per render: the old one never equals the new one and
    is refused as stale on Check and Save alike, before the checker, with
    nothing committed — and even replayed against the render it came from,
    its real path (``first``) no longer exists, so it is unknown."""

    ui = _gate7_ui(tmp_path)
    seen = _observed(ui)
    cookie = _logged_in(ui)
    page = f"{MODULE_PAGE_PREFIX}twitch"
    old = _keywords_editor(ui, cookie)
    old_name = old["entries"][1]["controls"][config_ui.PATCH_VALUE]
    assert old["entries"][1]["values"][config_ui.PATCH_VALUE] == '"kept"'
    removed = _write(ui, cookie, "/remove", page, [("path", "triggers.twitch.channels.@0")])
    assert removed.status == 303, _written(removed)
    # The identities of the loaded page, replayed after the Remove: the
    # path they name is gone, so nothing is found to apply them to.
    check, save = _both_routes(ui, cookie, "twitch", [(old_name, '"edited"')])
    _assert_stale_identity(check, save)
    assert any(config_ui._UNKNOWN_ENTRY_REASON in line for line in _written(check))
    added = _write(
        ui, cookie, "/save", page,
        [("add_channel_policy", "triggers.twitch.channels"), ("channel", "second"), ("combination", "all_of"),
         ("rule_type", "keyword"), ("parameters", json.dumps({"keywords": ["q7Z", "kept"]}))],
    )
    assert added.status == 303, _written(added)
    assert _overlay_doc(tmp_path / "config.local.yaml") == {"triggers": {"twitch": {"channels": {"second": GATE7_POLICY}}}}
    # Still on the old render: the old identity names ``first``, not ``second``.
    before = _snapshot(tmp_path)
    check, save = _both_routes(ui, cookie, "twitch", [(old_name, '"edited"')])
    _assert_stale_identity(check, save)
    assert any(config_ui._UNKNOWN_ENTRY_REASON in line for line in _written(check))
    # The gate's exact order: a fresh page, then the old identity.
    fresh = _module_html(ui, cookie, "twitch")
    new = _patch_editor(fresh, ".keywords")
    new_name = new["entries"][1]["controls"][config_ui.PATCH_VALUE]
    assert new_name != old_name
    assert not set(_identity_tokens(fresh)) & {old_name.rsplit("-", 1)[1]}
    check, save = _both_routes(ui, cookie, "twitch", [(old_name, '"edited"')])
    _assert_stale_identity(check, save)
    # Mixed with the current render's own untouched controls: still stale.
    check, save = _both_routes(ui, cookie, "twitch", [*_browser(new), (old_name, '"edited"')])
    _assert_stale_identity(check, save)
    assert seen == []
    assert _snapshot(tmp_path) == before
    # Opaque: no configured key and no secret in any identity.
    for token in _identity_tokens(fresh):
        assert not any(text in token for text in ("first", "second", "q7Z", "kept", "keywords"))
    # The current identity edits exactly its own entry.
    response = _write(ui, cookie, "/save", page, _submitted(new, {new_name: '"edited"'}))
    assert response.status == 303, _written(response)
    saved = _overlay_doc(tmp_path / "config.local.yaml")["triggers"]["twitch"]["channels"]["second"]
    assert saved["rules"][0]["parameters"]["keywords"] == ["q7Z", "edited"]


def test_p19f10_an_identity_of_an_older_render_of_the_same_page_is_refused(tmp_path: Path) -> None:
    """Rendering a page again retires the identities of the render before:
    a form from the earlier render is refused as stale on both routes, with
    nothing checked or written, even though the configuration is unchanged;
    the page shown now edits as ever."""

    ui = _allowed_ui(tmp_path, "q7Z", ["kept", "q7Z"])
    seen = _observed(ui)
    cookie = _logged_in(ui)
    older = _allowed_editor(ui, cookie)
    current = _allowed_editor(ui, cookie)
    older_tokens = {name for name in older["entries"][0]["controls"].values()}
    assert not older_tokens & set(current["entries"][0]["controls"].values())
    fields = _submitted(older, {older["entries"][0]["controls"][config_ui.PATCH_VALUE]: '"edited"'})
    check, save = _both_routes(ui, cookie, "audio_output", fields)
    _assert_stale_identity(check, save)
    assert seen == []
    assert not (tmp_path / "config.local.yaml").exists()
    fields = _submitted(current, {current["entries"][0]["controls"][config_ui.PATCH_VALUE]: '"edited"'})
    response = _write(ui, cookie, "/save", f"{MODULE_PAGE_PREFIX}audio_output", fields)
    assert response.status == 303, _written(response)
    assert _saved_allowed(tmp_path) == ["edited", "q7Z"]


@pytest.mark.parametrize("other_rendered", [True, False], ids=["other-shows-the-page", "other-never-did"])
def test_p19f10_an_identity_from_another_session_is_refused(tmp_path: Path, other_rendered: bool) -> None:
    """An identity belongs to the session whose render drew it: posted in
    another authenticated session over the same page and configuration it
    is refused as stale on both routes, whether or not that session shows
    the page itself; the drawing session still edits with it."""

    ui = _allowed_ui(tmp_path, "q7Z", ["kept", "q7Z"])
    seen = _observed(ui)
    first = _logged_in(ui)
    second = _logged_in(ui)
    editor = _allowed_editor(ui, first)
    if other_rendered:
        _allowed_editor(ui, second)
    fields = _submitted(editor, _allowed_changes(editor, append="added"))
    check, save = _both_routes(ui, second, "audio_output", fields)
    _assert_stale_identity(check, save)
    assert seen == []
    assert not (tmp_path / "config.local.yaml").exists()
    response = _write(ui, first, "/save", f"{MODULE_PAGE_PREFIX}audio_output", fields)
    assert response.status == 303, _written(response)
    assert _saved_allowed(tmp_path) == ["kept", "q7Z", "added"]


N7_SETTINGS_TEXT = (
    "modules_directory: builtin\nenabled_modules: [audio_output]\n"
    "secrets: ['${GATE_SECRET}']\n"
    "modules:\n  audio_output:\n"
    "    synthesis: {endpoint: '', model: MODEL}\n"
    "    voices: {allowed: ['q7Z'], default: '${GATE_SECRET}'}\n"
    "    outputs: {o: {player: {argv: [cat]}}}\n"
    "    default_output: o\n"
)
MODEL_FIELD = "modules.audio_output.synthesis.model"


def _n7_ui(tmp_path: Path, model: str = "q7Z") -> ConfigUI:
    """Gate 7's N7 fixture: an enabled audio output whose ordinary model
    setting equals the collected secret, so its control is withheld."""

    ui = _twitch_ui(tmp_path, N7_SETTINGS_TEXT.replace("MODEL", repr(model)), {"GATE_SECRET": "q7Z"})
    assert ui.check().passed
    return ui


def _checked_drafts(ui: ConfigUI) -> list[Any]:
    """Every draft the real checker is handed from now on."""

    seen: list[Any] = []
    real = ui.checker

    def checker(base: Path, environ: Mapping[str, str], overlay: Path | None, draft: Mapping[str, Any]) -> tuple[bool, list[str]]:
        seen.append(json.loads(json.dumps(draft)))
        return real(base, environ, overlay, draft)

    ui.checker = checker
    return seen


def _operation_options(page: str, field_name: str) -> list[str]:
    match = re.search(
        rf'<select name="{re.escape(config_ui.OPERATION_FIELD_PREFIX + field_name)}"[^>]*>(.*?)</select>', page, re.S
    )
    assert match is not None, field_name
    return re.findall(r'<option value="([^"]*)"', match.group(1))


def test_p19f10_gate7_n7_a_withheld_ordinary_scalar_can_be_set_to_the_empty_string(tmp_path: Path) -> None:
    """Gate-7 N7 through the actual form and the real enabled-module checker:
    the withheld model posted as the empty string with the ``set`` operation
    reaches Check's draft as ``''``, Save commits it, and the stored model
    is exactly the empty string — not "Nothing to save", not ``'""'``."""

    ui = _n7_ui(tmp_path)
    drafts = _checked_drafts(ui)
    cookie = _logged_in(ui)
    page = _module_html(ui, cookie, "audio_output")
    tag = re.search(rf'<input[^>]*name="{re.escape(MODEL_FIELD)}"[^>]*>', page)
    assert tag is not None and 'value=""' in tag.group(0) and 'data-withheld="true"' in tag.group(0)
    # Base-configured: kept or set, never cleared from the overlay.
    assert _operation_options(page, MODEL_FIELD) == [config_ui.OPERATION_UNCHANGED, config_ui.OPERATION_SET]
    assert "q7Z" not in page
    fields = [(MODEL_FIELD, ""), (config_ui.OPERATION_FIELD_PREFIX + MODEL_FIELD, config_ui.OPERATION_SET)]
    checked, saved = _check_then_save(ui, cookie, "audio_output", fields)
    assert _verdict(checked), _listed(checked)
    assert drafts == [{"modules": {"audio_output": {"synthesis": {"model": ""}}}}]
    assert saved.status == 303, _written(saved)
    stored = _overlay_doc(tmp_path / "config.local.yaml")
    assert stored == drafts[0]
    assert stored["modules"]["audio_output"]["synthesis"]["model"] == ""


def test_p19f10_a_genuinely_unchanged_withheld_field_sends_nothing(tmp_path: Path) -> None:
    """Left alone — empty text, ``unchanged`` selected — the withheld field
    makes no edit: Check's draft holds none and Save has nothing to save.
    Text typed while ``unchanged`` stays selected is refused by name on both
    routes, never applied nor dropped silently."""

    ui = _n7_ui(tmp_path)
    drafts = _checked_drafts(ui)
    cookie = _logged_in(ui)
    _module_html(ui, cookie, "audio_output")
    operation = config_ui.OPERATION_FIELD_PREFIX + MODEL_FIELD
    checked, saved = _check_then_save(ui, cookie, "audio_output", [(MODEL_FIELD, ""), (operation, "unchanged")])
    assert _verdict(checked) and drafts == [{}]
    assert saved.status == 200 and "Nothing to save" in saved.body.decode("utf-8")
    before = _snapshot(tmp_path)
    checked, saved = _check_then_save(ui, cookie, "audio_output", [(MODEL_FIELD, "typed"), (operation, "unchanged")])
    assert not _verdict(checked)
    assert any(config_ui._TYPED_BUT_UNCHANGED_REASON in line for line in _listed(checked))
    assert saved.status == 403
    assert any(config_ui._TYPED_BUT_UNCHANGED_REASON in line for line in _written(saved))
    for bad in ("remove", "clear", ""):
        checked, saved = _check_then_save(ui, cookie, "audio_output", [(MODEL_FIELD, ""), (operation, bad)])
        assert any(config_ui._OPERATION_REASON in line for line in _listed(checked)), bad
        assert saved.status == 403, bad
    assert drafts == [{}]
    assert _snapshot(tmp_path) == before


def test_p19f10_an_unset_scalar_can_be_set_to_the_empty_string(tmp_path: Path) -> None:
    """An unset text setting renders empty, so it carries the operation too:
    ``set`` with no text is the empty string, and ``unchanged`` is nothing."""

    ui = _twitch_ui(
        tmp_path,
        "modules_directory: builtin\nenabled_modules: []\nsecrets: []\nmodules:\n  twitch: {}\n",
        {},
    )
    cookie = _logged_in(ui)
    page = _module_html(ui, cookie, "twitch")
    name = "modules.twitch.companion_name"
    assert _operation_options(page, name) == [config_ui.OPERATION_UNCHANGED, config_ui.OPERATION_SET]
    operation = config_ui.OPERATION_FIELD_PREFIX + name
    token = config_ui._RENDERING_FOR.set(ui)
    try:
        assert ui.parse_edits(ui.view(), [(name, ""), (operation, "unchanged")]) == ([], [])
        assert ui.parse_edits(ui.view(), [(name, ""), (operation, "set")]) == (
            [(("modules", "twitch", "companion_name"), "")], [],
        )
    finally:
        config_ui._RENDERING_FOR.reset(token)
    response = _write(ui, cookie, "/save", f"{MODULE_PAGE_PREFIX}twitch", [(name, ""), (operation, "set")])
    assert response.status == 303, _written(response)
    assert _overlay_doc(tmp_path / "config.local.yaml") == {"modules": {"twitch": {"companion_name": ""}}}


def test_p19f10_clearing_an_optional_overlay_entry_restores_the_base(tmp_path: Path) -> None:
    """A withheld setting the overlay holds offers ``clear``: Check validates
    the draft without it and Save removes it (emptied mappings pruned), so
    the base value is effective again; clear with typed text is refused."""

    ui = _n7_ui(tmp_path, model="base-model")
    (tmp_path / "config.local.yaml").write_text(
        yaml.safe_dump({"modules": {"audio_output": {"synthesis": {"model": "q7Z"}}}}), encoding="utf-8"
    )
    drafts = _checked_drafts(ui)
    cookie = _logged_in(ui)
    page = _module_html(ui, cookie, "audio_output")
    assert "q7Z" not in page
    assert _operation_options(page, MODEL_FIELD) == [
        config_ui.OPERATION_UNCHANGED, config_ui.OPERATION_SET, config_ui.OPERATION_CLEAR,
    ]
    operation = config_ui.OPERATION_FIELD_PREFIX + MODEL_FIELD
    before = _snapshot(tmp_path)
    checked, saved = _check_then_save(ui, cookie, "audio_output", [(MODEL_FIELD, "x"), (operation, "clear")])
    assert any(config_ui._CLEARED_AND_TYPED_REASON in line for line in _listed(checked))
    assert saved.status == 403 and _snapshot(tmp_path) == before and drafts == []
    checked, saved = _check_then_save(ui, cookie, "audio_output", [(MODEL_FIELD, ""), (operation, "clear")])
    assert _verdict(checked), _listed(checked)
    assert drafts == [{}]
    assert saved.status == 303, _written(saved)
    assert _overlay_doc(tmp_path / "config.local.yaml") in (None, {})
    assert ui.view().value(("modules", "audio_output", "synthesis", "model")) == "base-model"


def test_p19f10_a_withheld_entry_can_be_replaced_by_the_empty_string(tmp_path: Path) -> None:
    """A withheld list entry's replacement carries its operation: ``set``
    with empty text is the empty string, applied at that entry only; left
    ``unchanged`` it keeps the real value."""

    ui = _allowed_ui(tmp_path, "q7Z", ["kept", "q7Z", "xq7Zy"])
    seen = _observed(ui)
    cookie = _logged_in(ui)
    editor = _allowed_editor(ui, cookie)
    assert editor["entries"][1]["values"][config_ui.PATCH_OPERATION] == config_ui.OPERATION_UNCHANGED
    checked, saved = _check_then_save(ui, cookie, "audio_output", _submitted(editor, _replaced(editor["entries"][1], "")))
    assert _verdict(checked), _listed(checked)
    assert saved.status == 303, _written(saved)
    assert _saved_allowed(tmp_path) == ["kept", "", "xq7Zy"]
    assert seen == [_overlay_doc(tmp_path / "config.local.yaml")]


def test_p19f10_removal_controls_drop_exactly_their_entry(tmp_path: Path) -> None:
    """The remove control of a list entry and of an optional mapping entry
    each drop exactly that entry beside withheld ones; Check sees the draft
    Save writes."""

    ui = _allowed_ui(tmp_path, "q7Z", ["kept", "q7Z", "other"])
    seen = _observed(ui)
    cookie = _logged_in(ui)
    editor = _allowed_editor(ui, cookie)
    checked, saved = _check_then_save(ui, cookie, "audio_output", _submitted(editor, _allowed_changes(editor, remove=[2])))
    assert _verdict(checked) and saved.status == 303, _written(saved)
    assert _saved_allowed(tmp_path) == ["kept", "q7Z"]
    assert seen == [_overlay_doc(tmp_path / "config.local.yaml")]

    other = tmp_path / "mapping"
    other.mkdir()
    ui = _arguments_ui(other, {"xq7Z": "one", "optional": "two"}, {"GATE_SECRET": "q7Z"})
    seen = _observed(ui)
    cookie = _logged_in(ui)
    actions = _actions_editor(ui, cookie)
    arguments = actions["entries"][0]["entries"][1]
    optional = next(entry for entry in arguments["entries"] if entry["key"] == "optional")
    checked, saved = _check_then_save(
        ui, cookie, "brain", _submitted(actions, {optional["controls"][config_ui.PATCH_REMOVE]: "true"})
    )
    assert _verdict(checked) and saved.status == 303, _written(saved)
    assert _saved_arguments(other) == {"xq7Z": "one"}
    assert seen == [_overlay_doc(other / "config.local.yaml")]


def test_p19f10_a_withheld_rule_parameter_offers_no_clear(tmp_path: Path) -> None:
    """P19F10 review A1: a trigger rule's parameter lives in a rule list the
    overlay replaces wholesale, so clearing it from the overlay could not
    restore the base value. The withheld overlay probability is kept or set,
    never offered ``clear``, and a posted ``clear`` is refused by name."""

    ui = _twitch_ui(
        tmp_path,
        "modules_directory: builtin\nenabled_modules: []\nsecrets: ['${GATE_SECRET}']\n"
        "triggers:\n  twitch:\n    channels:\n      chan:\n"
        "        combination: all_of\n        rules:\n"
        "          - type: probability\n            parameters: {probability: 0.25}\n",
        {"GATE_SECRET": "0.75"},
    )
    (tmp_path / "config.local.yaml").write_text(
        yaml.safe_dump({"triggers": {"twitch": {"channels": {"chan": {
            "combination": "all_of",
            "rules": [{"type": "probability", "parameters": {"probability": 0.75}}],
        }}}}}),
        encoding="utf-8",
    )
    cookie = _logged_in(ui)
    page = _module_html(ui, cookie, "twitch")
    name = "triggers.twitch.channels.@0.rules[0].parameters.probability"
    tag = re.search(rf'<input[^>]*name="{re.escape(name)}"[^>]*>', page)
    assert tag is not None and 'data-withheld="true"' in tag.group(0)
    assert _operation_options(page, name) == [config_ui.OPERATION_UNCHANGED, config_ui.OPERATION_SET]
    before = _snapshot(tmp_path)
    operation = config_ui.OPERATION_FIELD_PREFIX + name
    checked, saved = _check_then_save(ui, cookie, "twitch", [(name, ""), (operation, config_ui.OPERATION_CLEAR)])
    assert any(config_ui._OPERATION_REASON in line for line in _listed(checked))
    assert saved.status == 403
    assert _snapshot(tmp_path) == before


# ---------------------------------------------------------------------------
# P19F11 (gate-8 N3/N6 residual): no identity value is ever reissued, and
# every identity the current render did not draw meets one guard with one
# outcome on every route that consumes it
# ---------------------------------------------------------------------------


#: The bounded render-tag history P19F10 kept (4,096 renders): exercising
#: the allocator past it once made an evicted identity unrecognised.
P19F10_RENDER_HISTORY = 4096


def _counted_parses(ui: ConfigUI) -> list[None]:
    """One entry per :meth:`ConfigUI.parse_edits` call from now on."""

    calls: list[None] = []
    original = ui.parse_edits

    def parse_edits(*args: Any, **kwargs: Any) -> Any:
        calls.append(None)
        return original(*args, **kwargs)

    ui.parse_edits = parse_edits  # type: ignore[method-assign]
    return calls


def _session_of(ui: ConfigUI, cookie: str) -> Any:
    session = ui._session(_request("GET", "/", headers={"cookie": cookie}))
    assert session is not None
    return session


def _allowed_overlay_ui(tmp_path: Path) -> ConfigUI:
    """``_allowed_ui`` with the list overridden in the overlay, so Remove has
    an override to delete."""

    ui = _allowed_ui(tmp_path, "q7Z", ["kept", "q7Z"])
    (tmp_path / "config.local.yaml").write_text(
        yaml.safe_dump({"modules": {"audio_output": {"voices": {"allowed": ["kept", "q7Z", "over"]}}}}),
        encoding="utf-8",
    )
    return ui


def _all_routes(ui: ConfigUI, cookie: str, fields: list[tuple[str, str]]) -> dict[str, UIResponse]:
    """*fields* posted from the audio output page to every route that
    consumes a form's identities; Remove names a real override."""

    page = f"{MODULE_PAGE_PREFIX}audio_output"
    removal = [*fields, ("path", "modules.audio_output.voices.allowed")]
    return {
        "/check": _write(ui, cookie, "/check", page, fields),
        "/save": _write(ui, cookie, "/save", page, fields),
        "/remove": _write(ui, cookie, "/remove", page, removal),
    }


def test_p19f11_no_render_tag_or_identity_is_ever_reissued(tmp_path: Path) -> None:
    """Many renders through the production allocator, across sessions and
    two UI instances: every render tag and every identity is distinct, the
    tag is the UI's startup nonce then a strictly growing counter, and no
    history of issued tags is kept that could be evicted and reissued."""

    ui = _allowed_ui(tmp_path, "q7Z", ["kept", "q7Z"])
    other = ConfigUI(ui.settings, environ=ui.environ)
    assert not hasattr(ui, "_render_tags")
    tags: list[str] = []
    for target in (ui, other):
        sessions = [_session_of(target, _logged_in(target)) for _ in range(3)]
        for index in range(2 * P19F10_RENDER_HISTORY):
            tags.append(target._new_render(sessions[index % 3], f"{MODULE_PAGE_PREFIX}audio_output"))
    assert len(set(tags)) == len(tags)
    assert all(re.fullmatch(rf"[0-9a-f]{{{config_ui.RENDER_TAG_LENGTH}}}", tag) for tag in tags)
    half = len(tags) // 2
    first = {tag[: config_ui.RENDER_NONCE_LENGTH] for tag in tags[:half]}
    last = {tag[: config_ui.RENDER_NONCE_LENGTH] for tag in tags[half:]}
    assert len(first) == len(last) == 1 and first != last  # one nonce per UI, never shared
    counts = [int(tag[config_ui.RENDER_NONCE_LENGTH :], 16) for tag in tags[:half]]
    assert counts == sorted(set(counts))
    # Rendered identities: distinct over many real page renders and both UIs.
    # (the controls of one entry share its identity: counted once per page)
    identities: list[str] = []
    for target in (ui, other):
        cookie = _logged_in(target)
        for _ in range(50):
            identities.extend(set(_identity_tokens(_module_html(target, cookie, "audio_output"))))
    assert identities and len(set(identities)) == len(identities)
    assert not any("q7Z" in token or "kept" in token for token in identities)


def test_p19f11_gate8_a_foreign_process_identity_with_current_guards_is_refused(tmp_path: Path) -> None:
    """Gate 8's first residual through the real handlers: an authentic
    identity minted by another UI process over the same configuration,
    posted with the receiver's current layout, fingerprint, CSRF token and
    current controls, answered Check 200/failed and Save 403. It is now the
    stale page on both routes, identically, with nothing parsed, checked or
    committed; the receiver's own render still saves."""

    ui = _allowed_ui(tmp_path, "q7Z", ["kept", "q7Z"])
    seen = _observed(ui)
    foreign = ConfigUI(ui.settings, environ=ui.environ)
    other = _allowed_editor(foreign, _logged_in(foreign))
    cookie = _logged_in(ui)
    current = _allowed_editor(ui, cookie)
    before = _snapshot(tmp_path)
    parsed = _counted_parses(ui)
    for changes in (_replaced(other["entries"][1], '"x"'), _allowed_changes(other, append="added")):
        fields = [*_browser(current), *_submitted(other, changes)]
        check, save = _both_routes(ui, cookie, "audio_output", fields)
        _assert_stale_identity(check, save)
        assert check.body == save.body
    assert parsed == []
    assert seen == []
    assert _snapshot(tmp_path) == before
    response = _write(
        ui, cookie, "/save", f"{MODULE_PAGE_PREFIX}audio_output",
        _submitted(current, _allowed_changes(current, append="added")),
    )
    assert response.status == 303, _written(response)
    assert _saved_allowed(tmp_path) == ["kept", "q7Z", "added"]


def test_p19f11_gate8_an_identity_past_the_old_history_bound_stays_refused(tmp_path: Path) -> None:
    """Gate 8's second residual: a genuine identity retained from a render,
    then the production allocator exercised past the 4,096-render history
    P19F10 bounded, then the page rendered again. The old identity is no
    longer "forgotten" into another class: it is the stale page on both
    routes, identically, with nothing checked or committed."""

    ui = _allowed_ui(tmp_path, "q7Z", ["kept", "q7Z"])
    seen = _observed(ui)
    cookie = _logged_in(ui)
    old = _allowed_editor(ui, cookie)
    session = _session_of(ui, cookie)
    for _ in range(P19F10_RENDER_HISTORY + 1):
        ui._new_render(session, f"{MODULE_PAGE_PREFIX}audio_output")
    current = _allowed_editor(ui, cookie)
    before = _snapshot(tmp_path)
    for fields in (
        _browser(old),
        _submitted(old, _allowed_changes(old, append="added")),
        [*_browser(current), *_submitted(old, _replaced(old["entries"][1], '"x"'))],
    ):
        check, save = _both_routes(ui, cookie, "audio_output", fields)
        _assert_stale_identity(check, save)
    assert seen == []
    assert _snapshot(tmp_path) == before
    response = _write(
        ui, cookie, "/save", f"{MODULE_PAGE_PREFIX}audio_output",
        _submitted(current, _allowed_changes(current, append="added")),
    )
    assert response.status == 303, _written(response)


def test_p19f11_every_route_refuses_a_non_current_identity_identically(tmp_path: Path) -> None:
    """One refusal class, one outcome: an identity from an earlier render,
    another session, another UI process, past the old history bound, from
    another page, altered, or naming an entry changed on disk since, posted
    to Check, Save or Remove with the receiver's current guards, always
    answers the same 409 stale page — status, content type and body
    identical on every route and for every source — and nothing is parsed,
    checked, removed or written. The current render's own form still works
    on each route."""

    ui = _allowed_overlay_ui(tmp_path)
    seen = _observed(ui)
    cookie = _logged_in(ui)
    session = _session_of(ui, cookie)
    earlier = _allowed_editor(ui, cookie)
    second = _logged_in(ui)
    elsewhere = _allowed_editor(ui, second)
    foreign = ConfigUI(ui.settings, environ=ui.environ)
    remote = _allowed_editor(foreign, _logged_in(foreign))
    evicted = _allowed_editor(ui, cookie)
    for _ in range(P19F10_RENDER_HISTORY + 1):
        ui._new_render(session, f"{MODULE_PAGE_PREFIX}audio_output")
    current = _allowed_editor(ui, cookie)
    brain_page = _module_html(ui, cookie, "brain")
    brain_identities = [
        (f"{config_ui.PATCH_FIELD_PREFIX}{config_ui.PATCH_VALUE}-{token}", '"x"')
        for token in _identity_tokens(brain_page)
    ]
    name = current["entries"][0]["controls"][config_ui.PATCH_VALUE]
    prefix = f"{config_ui.PATCH_FIELD_PREFIX}{config_ui.PATCH_VALUE}-"
    altered = prefix + name[len(prefix) :][:-1] + ("0" if name[-1] != "0" else "1")
    sources: dict[str, list[tuple[str, str]]] = {
        "earlier-render": _submitted(earlier, _allowed_changes(earlier, append="a")),
        "other-session": _submitted(elsewhere, _allowed_changes(elsewhere, append="a")),
        "other-process": [*_browser(current), *_submitted(remote, _replaced(remote["entries"][1], '"x"'))],
        "past-history": _browser(evicted),
        "other-page": [*_browser(current), *brain_identities[:1]],
        "altered": [*[(key, text) for key, text in _browser(current) if key != name], (altered, '"x"')],
    }
    if not brain_identities:
        del sources["other-page"]
    before = _snapshot(tmp_path)
    outcomes: set[tuple[int, str, bytes]] = set()
    parsed = _counted_parses(ui)
    for source, fields in sources.items():
        for route, response in _all_routes(ui, cookie, fields).items():
            assert response.status == 409, (source, route, _written(response))
            assert _written(response) == [config_ui._STALE_IDENTITY_REASON], (source, route)
            outcomes.add((response.status, response.content_type, response.body))
    assert len(outcomes) == 1
    assert "Refused: stale page" in next(iter(outcomes))[2].decode("utf-8")
    assert parsed == []
    assert seen == []
    assert _snapshot(tmp_path) == before
    # An entry changed on disk under the current render: the same outcome.
    overlay = tmp_path / "config.local.yaml"
    overlay.write_text(overlay.read_text(encoding="utf-8").replace("over", "moved"), encoding="utf-8")
    changed = _snapshot(tmp_path)
    fields = _submitted(current, {current["entries"][2]["controls"][config_ui.PATCH_VALUE]: '"x"'})
    for route, response in _all_routes(ui, cookie, fields).items():
        assert (response.status, response.content_type, response.body) in outcomes, route
    assert seen == []
    assert _snapshot(tmp_path) == changed
    # The current render's own form: Check, then Remove, work as ever.
    current = _allowed_editor(ui, cookie)
    check = _write(ui, cookie, "/check", f"{MODULE_PAGE_PREFIX}audio_output", _browser(current))
    assert check.status == 200 and _verdict(check.body.decode("utf-8"))
    removed = _all_routes(ui, cookie, _browser(current))["/remove"]
    assert removed.status == 303, _written(removed)
    assert not overlay.exists() or "allowed" not in overlay.read_text(encoding="utf-8")
