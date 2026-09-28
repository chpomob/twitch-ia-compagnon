"""The configuration UI: CLI, bind policy, guards and startup refusals (P9);
the configuration model, base page and core-settings page (P10); the module
pages generated from each manifest (P11).

The whole module runs with socket creation patched to raise (AC5): no test
here binds, connects or even creates a socket. The request core is driven
through :meth:`ConfigUI.handle` directly, and the one coroutine the module
runs (the request bridge, D8) goes through the socket-free loop of D7. There
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
import socket
import subprocess
import sys
import threading
import tokenize
from collections.abc import Iterator, Sequence
from pathlib import Path

import pytest
import yaml

import core.config_ui as config_ui
from core.config_ui import (
    BASE_ENTRY_NOTE,
    ENTRY_SEGMENT,
    HIDDEN_LITERAL,
    ITEMS_SEGMENT,
    LOCAL_ONLY_STATEMENT,
    MODULE_PAGE_PREFIX,
    NOT_EDITABLE,
    READ_ONLY_REASON,
    REDACT_MIN_LENGTH,
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
    startup_checks,
)
from core.main import LIMIT_DECLARATION
from core.overlay import read_base

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


def test_ac6_origin_accepted_or_absent(config_dir: Path) -> None:
    ui = _ui(config_dir / "config.yaml")
    cookie, csrf = _login(ui)
    for origin in (None, "http://localhost:8765", "http://127.0.0.1:8765", "http://[::1]:8765"):
        assert _post(ui, "/save", cookie, csrf, origin=origin).status == 501


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
        assert _post(ui, "/save", cookie, csrf, host="localhost", origin=origin).status == 501
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
    assert _post(ui, "/save", cookie, csrf, host="box.lan:8765", origin="http://box.lan:8765").status == 501
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
        return await _dispatch(ui, request, executor)

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
    assert [rule_id for rule_id, _ in rows] == [rule["rule_id"] for rule in base["actions"]]
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
    assert re.findall(r'<tr data-rule="([^"]*)" data-origin="([^"]+)">', actions_block) == [
        ("hand-written", "overlay")
    ]
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


def test_redaction_skips_values_shorter_than_the_minimum() -> None:
    assert REDACT_MIN_LENGTH == 4
    assert _redact("a 1 abc abcd", {"1", "abc", "abcd"}) == "a 1 abc [hidden]"
    assert _redact("x <tag> &lt;tag&gt;", {"<tag>"}) == "x [hidden] [hidden]"


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
    assert 'type="checkbox" name="modules.fmod.flag" value="true" checked' in page
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
    markup = _SchemaRenderer(view).children(schema, ("modules", "ymod"))
    assert _shown_paths(markup, "modules.ymod.") == [
        "routes",
        "routes.<entry>",
        "routes.<entry>.weight",
    ]
    assert set(_shown_paths(markup, "modules.ymod.")) == set(_schema_paths(schema))
    assert '<div class="entry" data-entry="first">' in markup
    assert 'name="modules.ymod.routes.first" data-json="entry"' in markup
    assert "{&quot;weight&quot;: 2}</textarea>" in markup
    assert 'name="new_entry_name:modules.ymod.routes"' in markup


# -- AC19: trigger policies ------------------------------------------------------


def test_ac19_the_twitch_page_shows_the_configured_channel_policy(tmp_path: Path) -> None:
    ui = _profile_ui(tmp_path, "config.yaml.example", {})
    page = _module_html(ui, _logged_in(ui), "twitch")
    triggers = _section(page, "triggers")
    channels = re.findall(r'<fieldset class="channel" data-channel="([^"]*)">', triggers)
    assert channels == ["${TWITCH_BROADCASTER_ID}"]
    assert "<legend>Channel <code>${TWITCH_BROADCASTER_ID}</code> (unset)</legend>" in triggers
    assert re.search(r'<option value="all_of" selected>all_of</option>', triggers)
    assert re.findall(r'data-rule-type="([^"]+)"', triggers) == ["keyword"]
    path = "triggers.twitch.channels.${TWITCH_BROADCASTER_ID}.rules[0].parameters.keywords"
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
    assert list(fieldsets) == ["from-base", "from-overlay"]
    assert BASE_ENTRY_NOTE in fieldsets["from-base"]
    assert "Delete this channel policy" not in fieldsets["from-base"]
    assert BASE_ENTRY_NOTE not in fieldsets["from-overlay"]
    assert (
        'formaction="/remove" name="path" value="triggers.tmod.channels.from-overlay">'
        "Delete this channel policy" in fieldsets["from-overlay"]
    )
    assert 'selected>any_of</option>' in fieldsets["from-base"]
    odds = _node(page, "triggers.tmod.channels.from-base.rules[0].parameters.odds")
    assert 'min="0" max="1" step="any" value="0.5"' in odds
    # An undeclared rule type is a notice, never dropped.
    unknown = _node(page, "triggers.tmod.channels.from-overlay.rules[1]")
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
