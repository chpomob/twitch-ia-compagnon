"""The configuration UI core: CLI, bind policy, guards and startup refusals (P9).

The whole module runs with socket creation patched to raise (AC5): no test
here binds, connects or even creates a socket. The request core is driven
through :meth:`ConfigUI.handle` directly, and the one coroutine the module
runs (the request bridge, D8) goes through the socket-free loop of D7. There
is deliberately no pytest-asyncio test here: a fixture-provided event loop
would create its self-pipe socketpair under the patch.
"""

from __future__ import annotations

import asyncio
import gc
import logging
import os
import socket
import subprocess
import sys
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

import core.config_ui as config_ui
from core.config_ui import (
    ConfigUI,
    UIRequest,
    UIResponse,
    UISettings,
    _dispatch,
    _run_socket_free,
    _SocketFreeEventLoop,
    _to_ui_request,
    accepted_authorities,
    main,
    startup_checks,
)

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
