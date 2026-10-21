"""The ``operator`` input: manifest, settings, request core, page and overlay,
lifecycle (phase 8 R6, plan step P9; AC21 page and overlay halves, AC22,
AC23, AC24), and the press command ``python -m modules.operator press``
(plan step P10; AC40, AC21's hotkey third) through a fake opener that hands
its request to the input's request core.

The module is discovered and activated by the real loader over the shipped
``modules/`` directory, on a :func:`~conftest.runtime_context` whose
:class:`~conftest.ManualClock` is the core's clock. Its listener is the
:class:`FakeServerFactory` handed through the ``_server_factory`` seam: it
counts the listeners opened and hands each request to the module's dispatch,
exactly as the aiohttp listener does — no socket is opened. Time moves only by
:meth:`ManualClock.advance`; no positive-duration sleep. No Streamer.bot
module is enabled anywhere here (AC24).
"""

from __future__ import annotations

import email.message
import functools
import http.client
import importlib
import io
import json
import logging
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import pytest
import yaml

from conftest import ManualClock, RecordingScheduler, events_of, runtime_context, trace_texts
from core.contracts import (
    EVENT_KINDS,
    TRACE_INPUT_TRIGGER_ACCEPTED,
    TRACE_INPUT_TRIGGER_REJECTED,
)
from core.loader import ModuleLoader, ModuleLoadError
from core.triggers import TriggerRegistry
import modules.operator as operator
import modules.operator.__main__ as press_command
from modules.brain import BrainModule
from modules.operator import (
    COUNT_ADMITTED,
    COUNT_NOT_ADMITTED,
    COUNT_PRESSED,
    COUNT_PUBLISHED,
    COUNT_REFUSED_CLOSED,
    COUNT_REFUSED_COOLDOWN,
    COUNT_REFUSED_HOST,
    COUNT_REFUSED_ORIGIN,
    COUNT_REFUSED_ROUTE,
    COUNT_REFUSED_SURFACE,
    COUNT_REFUSED_TOKEN,
    COUNT_REFUSED_UNKNOWN_BUTTON,
    REFUSAL_COUNTS,
    AiohttpListener,
    OperatorModule,
    validate_settings,
)


REPOSITORY = Path(__file__).resolve().parents[1]
MODULES = REPOSITORY / "modules"
START = 1000.0
PLATFORM = "fake"
CHANNEL = "chan-a"
PORT = 8765
HOST = f"127.0.0.1:{PORT}"
ORIGIN = f"http://{HOST}"
#: A 40-character token (AC21), resolved from the environment.
TOKEN = "Op3rat0r-T0ken-" + "q" * 25
TOKEN_VARIABLE = "OPERATOR_TOKEN"
IMAGE = "https://images.invalid/overlay.png"
HYPE_PROMPT = "Celebrate the hype train with the chat."


def operator_settings(**changes: Any) -> dict[str, Any]:
    """The settings of AC21: all three surfaces, a 40-character token."""

    settings: dict[str, Any] = {
        "buttons": [
            {
                "id": "hype",
                "label": "Hype!",
                "prompt_text": HYPE_PROMPT,
                "cooldown_seconds": 2,
                "rect": {"x": 10, "y": 20, "w": 30, "h": 10},
            },
            {"id": "other", "label": "Other", "prompt_text": "Say something else."},
        ],
        "destination": {"platform": PLATFORM, "channel_id": CHANNEL},
        "surfaces": {"page": True, "hotkey": True, "overlay": True},
        "listen": {"host": "127.0.0.1", "port": PORT},
        "token": f"${{{TOKEN_VARIABLE}}}",
        "overlay": {"image": IMAGE},
    }
    settings.update(changes)
    return settings


class FakeListener:
    """One listener the fake factory opened: it hands requests to the dispatch."""

    def __init__(self, host: str, port: int, dispatch: Any) -> None:
        self.host = host
        self.port = port
        self.dispatch = dispatch
        self.started = False
        self.closed = False

    async def start(self) -> None:
        self.started = True

    async def close(self) -> None:
        self.closed = True

    @property
    def open(self) -> bool:
        return self.started and not self.closed

    async def request(
        self, method: str, path: str, headers: dict[str, str], body: bytes = b""
    ) -> tuple[int, dict[str, Any] | str] | None:
        """``None`` when the listener is not open: nothing reaches the core."""

        if not self.open:
            return None
        status, text, kind = await self.dispatch(method, path, headers, body)
        return status, (json.loads(text) if kind == "application/json" else text)


class FakeServerFactory:
    """The ``_server_factory`` seam: counts every listener it builds."""

    def __init__(self) -> None:
        self.listeners: list[FakeListener] = []

    def __call__(self, host: str, port: int, dispatch: Any) -> FakeListener:
        listener = FakeListener(host, port, dispatch)
        self.listeners.append(listener)
        return listener

    @property
    def opened(self) -> int:
        return sum(1 for listener in self.listeners if listener.started)

    @property
    def open(self) -> int:
        return sum(1 for listener in self.listeners if listener.open)

    @property
    def listener(self) -> FakeListener:
        (listener,) = self.listeners
        return listener


class Operated:
    """One operator module activated by the real loader on a manual clock."""

    def __init__(self, runtime: Any, clock: ManualClock, factory: FakeServerFactory,
                 scheduler: RecordingScheduler, handle: OperatorModule) -> None:
        self.runtime = runtime
        self.clock = clock
        self.factory = factory
        self.scheduler = scheduler
        self.handle = handle

    async def press(
        self,
        button: str = "hype",
        surface: str = "page",
        *,
        token: str | None = TOKEN,
        host: str = HOST,
        origin: str | None = ORIGIN,
        in_query: bool = False,
    ) -> tuple[int, Any] | None:
        headers = {"Host": host, "Content-Type": "application/json"}
        if origin is not None:
            headers["Origin"] = origin
        path = "/press"
        if token is not None:
            if in_query:
                path += f"?token={token}"
            else:
                headers["X-Operator-Token"] = token
        body = json.dumps({"button": button, "surface": surface}).encode("utf-8")
        listener = self.factory.listeners[-1] if self.factory.listeners else None
        if listener is None:
            return None
        return await listener.request("POST", path, headers, body)

    async def get(self, path: str, *, token: str = TOKEN) -> tuple[int, Any] | None:
        listener = self.factory.listeners[-1]
        return await listener.request("GET", f"{path}?token={token}", {"Host": HOST})

    def events(self) -> list[dict[str, Any]]:
        """The ``manual`` events admitted, oldest first."""

        return [work.payload for _session, work in self.scheduler.admissions]

    def decisions(self) -> list[dict[str, Any]]:
        return [
            event["payload"]
            for event_type in (TRACE_INPUT_TRIGGER_ACCEPTED, TRACE_INPUT_TRIGGER_REJECTED)
            for event in events_of(self.runtime.bus, event_type)
        ]


async def operated(
    *,
    started: bool = True,
    scheduler: RecordingScheduler | None = None,
    shared: bool = True,
    triggers: dict[str, Any] | None = None,
    **changes: Any,
) -> Operated:
    """The operator, enabled alone, activated by the loader; started if asked.

    ``shared=False`` wires it as ``core.main`` does: no scheduler on the
    context, the brain's own published as the ``(admission, runs)`` service.
    """

    clock = ManualClock(START)
    recording = scheduler or RecordingScheduler()
    runtime = runtime_context(
        clock=clock,
        trigger_registry=TriggerRegistry(),
        scheduler=recording if shared else None,
    )
    if not shared:
        runtime.services.publish("admission", "runs", recording, module="brain")
    factory = FakeServerFactory()
    config: dict[str, Any] = {
        "enabled_modules": ["operator"],
        "modules": {"operator": {**operator_settings(**changes), "_server_factory": factory}},
    }
    if triggers is not None:
        config["triggers"] = triggers
    loader = ModuleLoader(
        runtime.bus, MODULES, context=runtime, environ={TOKEN_VARIABLE: TOKEN}
    )
    (activation,) = await loader.activate_enabled(config)
    assert "streamerbot" not in config["enabled_modules"]
    harness = Operated(runtime, clock, factory, recording, activation.handle)
    await harness.handle.prepare()
    if started:
        await harness.handle.start_inputs()
    return harness


# -- the manifest ---------------------------------------------------------------- #


def test_the_manifest_declares_an_input_with_its_button_type_and_token() -> None:
    """R6: role ``input``, produces the chat event, the token a credential,
    ``event_kind`` narrowed to ``manual``, the declared ``button`` type over
    ``button.id``, and the default policy ``event_kind{[manual]}``."""

    manifest = yaml.safe_load((MODULES / "operator" / "module.yaml").read_text())
    assert manifest["name"] == "operator"
    assert manifest["manifest_version"] == 2
    assert manifest["lifecycle"] == {"roles": ["input"]}
    assert manifest["produces"] == ["channel.chat.message"]
    assert manifest["credentials"] == ["token"]
    assert "actions" not in manifest
    assert callable(getattr(operator, manifest["settings_validator"]))
    properties = manifest["settings_schema"]["properties"]
    assert set(properties) == {"buttons", "destination", "surfaces", "listen", "token", "overlay"}
    surfaces = properties["surfaces"]["properties"]
    assert {name: node["default"] for name, node in surfaces.items()} == {
        "page": False,
        "hotkey": False,
        "overlay": False,
    }
    assert properties["listen"]["properties"]["host"]["default"] == "127.0.0.1"
    assert properties["listen"]["properties"]["allow_non_loopback"]["default"] is False
    button = properties["buttons"]["items"]["properties"]
    assert button["cooldown_seconds"]["default"] == 2
    assert (button["cooldown_seconds"]["minimum"], button["cooldown_seconds"]["maximum"]) == (0, 3600)
    triggers = manifest["triggers"]
    assert triggers["fields"] == {"button.id": "string", "button.surface": "string"}
    types = {entry["name"]: entry for entry in triggers["types"]}
    assert set(types) == {"event_kind", "button"}
    assert types["event_kind"]["parameter_schema"]["properties"]["kinds"]["items"]["enum"] == [
        "manual"
    ]
    assert types["button"]["kinds"] == ["manual"]
    assert types["button"]["parameter_schema"]["required"] == ["button_ids"]
    assert types["button"]["match"] == {
        "in": [{"field": "button.id"}, {"param": "button_ids"}]
    }
    assert triggers["default_policy"] == {
        "combination": "all_of",
        "rules": [{"type": "event_kind", "parameters": {"kinds": ["manual"]}}],
    }
    assert "manual" in EVENT_KINDS


def test_importing_the_package_imports_no_runtime_loader_or_lifecycle() -> None:
    """AC40's last clause, held by this step: ``modules/operator/__init__.py``
    imports nothing from ``core`` at module level (the ``core`` package
    imports its loader), so the press command can run without the runtime."""

    guarded = ("core.runtime", "core.loader", "core.lifecycle")
    saved = {
        name: module
        for name, module in sys.modules.items()
        if name == "core" or name.startswith("core.") or name.startswith("modules.operator")
    }
    for name in saved:
        del sys.modules[name]
    try:
        __import__("modules.operator")
        assert [name for name in guarded if name in sys.modules] == []
    finally:
        for name in [n for n in sys.modules if n == "core" or n.startswith("core.")]:
            del sys.modules[name]
        for name in [n for n in sys.modules if n.startswith("modules.operator")]:
            del sys.modules[name]
        sys.modules.update(saved)


# -- AC21: the page and the overlay ------------------------------------------- #


async def test_ac21_no_listener_before_start_inputs_and_a_press_from_page_and_overlay() -> None:
    """AC21 (page and overlay halves): 0 listeners before ``start_inputs``;
    after it, a press of ``hype`` from the page and one from the overlay give
    2 ``manual`` events with ``payload.button``, the prompt text, the author
    ``operator:hype`` with its label, distinct message ids and the configured
    destination, each decided and admitted through the scheduler."""

    h = await operated(started=False)
    assert h.factory.opened == 0 and not h.handle.listening
    # Nothing can reach the core before the listener opens: it refuses too.
    assert h.handle.core.handle("POST", "/press", {"Host": HOST}, b"{}")[0] == 503

    await h.handle.start_inputs()
    assert h.factory.opened == 1 and h.factory.open == 1
    assert (h.factory.listener.host, h.factory.listener.port) == ("127.0.0.1", PORT)

    assert await h.press(surface="page") == (200, {"status": "accepted"})
    h.clock.advance(2)
    assert await h.press(surface="overlay") == (200, {"status": "accepted"})

    events = h.events()
    assert len(events) == 2
    for event, surface in zip(events, ("page", "overlay")):
        payload = event["payload"]
        assert event["type"] == "channel.chat.message"
        assert event["metadata"] == {"source": "operator", "schema_version": 2}
        assert payload["kind"] == "manual"
        assert payload["button"] == {"id": "hype", "surface": surface}
        assert payload["text"] == HYPE_PROMPT
        assert payload["author"] == {"id": "operator:hype", "display_name": "Hype!"}
        assert (payload["platform"], payload["channel_id"]) == (PLATFORM, CHANNEL)
        prefix, _, suffix = payload["message_id"].rpartition(":")
        assert prefix == "operator:hype" and len(suffix) == 32
        int(suffix, 16)
    assert events[0]["payload"]["message_id"] != events[1]["payload"]["message_id"]
    for session, work in h.scheduler.admissions:
        assert (session.platform, session.channel_id, session.viewer_id) == (
            PLATFORM,
            CHANNEL,
            "operator:hype",
        )
        assert work.source_event_id == work.payload["payload"]["message_id"]
    decisions = h.decisions()
    assert [d["reason"] for d in decisions] == ["accepted:event_kind_manual"] * 2
    assert {d["input"] for d in decisions} == {"operator"}
    assert [d["source_event_id"] for d in decisions] == [
        event["payload"]["message_id"] for event in events
    ]
    counts = h.handle.counts()
    assert (counts[COUNT_PRESSED], counts[COUNT_ADMITTED], counts[COUNT_PUBLISHED]) == (2, 2, 2)
    # R6: one published ``manual`` event per accepted press — the admitted one.
    published = events_of(h.runtime.bus, "channel.chat.message")
    assert [event["payload"] for event in published] == [event["payload"] for event in events]
    assert all(event["metadata"]["source"] == "operator" for event in published)
    await h.handle.close()


async def test_ac21_after_stop_inputs_the_listener_is_closed_and_presses_give_nothing() -> None:
    """AC21: after ``stop_inputs`` the listener is closed; a press of
    ``hype`` from each surface — through the closed listener, or handed to
    the core directly — produces 0 events."""

    h = await operated()
    assert await h.press(surface="page") is not None
    assert len(h.events()) == 1
    await h.handle.stop_inputs()
    assert h.factory.open == 0 and h.factory.listener.closed
    h.clock.advance(60)
    for surface in ("page", "hotkey", "overlay"):
        assert await h.press(surface=surface) is None
        body = json.dumps({"button": "hype", "surface": surface}).encode()
        status, _ = h.handle.core.handle(
            "POST", "/press", {"Host": HOST, "X-Operator-Token": TOKEN}, body
        )
        assert status == 503
    assert len(h.events()) == 1
    assert h.handle.counts()[COUNT_REFUSED_CLOSED] == 3
    await h.handle.close()


def brain_owned_consumer(runtime: Any, scheduler: RecordingScheduler) -> list[str]:
    """Subscribe the brain's real ``channel.chat.message`` consumer, as it
    runs when the brain owns *scheduler*; returns its diagnostics."""

    diagnostics: list[str] = []
    brain = BrainModule.__new__(BrainModule)
    brain._closed = False
    brain._owns_scheduler = True
    brain._scheduler = scheduler
    brain._triggers = runtime.triggers
    brain._diagnose = diagnostics.append
    runtime.bus.subscribe(
        "channel.chat.message", functools.partial(BrainModule.handle_chat_message, brain)
    )
    return diagnostics


async def test_a_press_is_admitted_once_by_the_brain_owned_scheduler_when_the_context_has_none() -> None:
    """Wired as ``core.main`` wires it — no scheduler on the context, the
    brain owning its own and publishing it as ``(admission, runs)`` — the
    published press is admitted exactly once, by the brain's consumer reading
    the recorded decision; the input does not admit it a second time (R6)."""

    h = await operated(shared=False)
    diagnostics = brain_owned_consumer(h.runtime, h.scheduler)
    assert h.runtime.scheduler is None
    assert await h.press() == (200, {"status": "accepted"})
    (event,) = events_of(h.runtime.bus, "channel.chat.message")
    (work,) = h.events()
    assert work["payload"] == event["payload"]
    (session, admitted) = h.scheduler.admissions[0]
    assert (session.platform, session.channel_id, session.viewer_id) == (
        PLATFORM,
        CHANNEL,
        "operator:hype",
    )
    assert admitted.source_event_id == event["payload"]["message_id"]
    counts = h.handle.counts()
    assert (counts[COUNT_PUBLISHED], counts[COUNT_ADMITTED]) == (1, 0)
    assert diagnostics == []
    await h.handle.close()


async def test_a_shared_scheduler_admits_a_press_once_though_it_is_published() -> None:
    """With the scheduler shared through the context, the input admits the
    press and the brain's consumer admits nothing into it: the published
    copy and the admission are one press each (R6)."""

    h = await operated()
    shared_consumer = BrainModule.__new__(BrainModule)
    shared_consumer._closed = False
    shared_consumer._owns_scheduler = False
    h.runtime.bus.subscribe(
        "channel.chat.message", functools.partial(BrainModule.handle_chat_message, shared_consumer)
    )
    assert await h.press() == (200, {"status": "accepted"})
    assert len(events_of(h.runtime.bus, "channel.chat.message")) == 1
    assert len(h.events()) == 1
    assert h.handle.counts()[COUNT_ADMITTED] == 1
    await h.handle.close()


async def test_a_rejected_press_is_published_and_admitted_by_no_one() -> None:
    """A press the policy rejects is still published — decided either way,
    like the twitch input's events — and neither route admits it (R6)."""

    policy = {
        "combination": "all_of",
        "rules": [{"type": "button", "parameters": {"button_ids": ["other"]}}],
    }
    h = await operated(shared=False, triggers={"operator": {"channels": {CHANNEL: policy}}})
    brain_owned_consumer(h.runtime, h.scheduler)
    assert await h.press() == (200, {"status": "accepted"})
    assert len(events_of(h.runtime.bus, "channel.chat.message")) == 1
    assert h.events() == []
    assert h.handle.counts()[COUNT_NOT_ADMITTED] == 1
    await h.handle.close()


async def test_the_page_lists_the_buttons_and_the_overlay_places_them_without_the_token() -> None:
    """``GET /`` lists every button; ``GET /overlay`` shows the image with
    each button that has a ``rect`` absolutely placed in percent; neither
    page contains the token, and both post back to ``/press``."""

    h = await operated()
    status, page = await h.get("/")
    assert status == 200
    assert 'data-button="hype"' in page and "Hype!" in page
    assert 'data-button="other"' in page and "Other" in page
    assert 'data-surface="page"' in page and "/press" in page
    status, overlay = await h.get("/overlay")
    assert status == 200
    assert f'src="{IMAGE}"' in overlay
    assert "left:10%;top:20%;width:30%;height:10%" in overlay
    assert 'data-button="hype"' in overlay and 'data-button="other"' not in overlay
    assert 'data-surface="overlay"' in overlay
    for text in (page, overlay):
        assert TOKEN not in text
        assert 'name="referrer" content="no-referrer"' in text
    await h.handle.close()


async def test_a_page_of_a_disabled_surface_is_refused() -> None:
    h = await operated(surfaces={"hotkey": True})
    for path in ("/", "/overlay"):
        assert (await h.get(path))[0] == 403
    assert await h.press(surface="page") == (403, {"status": "refused", "reason": "surface"})
    assert h.handle.counts()[COUNT_REFUSED_SURFACE] == 3
    assert (await h.get("/elsewhere"))[0] == 404
    assert h.handle.counts()[COUNT_REFUSED_ROUTE] == 1
    assert h.events() == []
    await h.handle.close()


# -- AC22: the guards ------------------------------------------------------------ #


async def test_ac22_every_refusal_gives_no_event_and_its_count_and_echoes_nothing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """AC22: no token, a wrong token, a wrong ``Host``, a foreign ``Origin``
    on a press, an unknown button, and a second press of ``hype`` 1 s after
    the first (cooldown 2) each give 0 events and one refusal count; a press
    2 s after the last accepted one is accepted. Neither the token nor any
    request header value reaches a trace, a diagnostic or a log record."""

    caplog.set_level(logging.DEBUG)
    h = await operated()
    wrong_token = "W" * 40
    wrong_host = "evil-host.invalid:8765"
    foreign_origin = "http://foreign-origin.invalid"

    refused = [
        (await h.press(token=None), COUNT_REFUSED_TOKEN, 401),
        (await h.press(token=wrong_token), COUNT_REFUSED_TOKEN, 401),
        (await h.press(token=wrong_token, in_query=True), COUNT_REFUSED_TOKEN, 401),
        (await h.press(host=wrong_host), COUNT_REFUSED_HOST, 403),
        (await h.press(origin=foreign_origin), COUNT_REFUSED_ORIGIN, 403),
        (await h.press(button="unknown-button"), COUNT_REFUSED_UNKNOWN_BUTTON, 404),
    ]
    for answer, count, status in refused:
        assert answer is not None and answer[0] == status, (answer, count)
    assert h.events() == [] and h.decisions() == []
    counts = h.handle.counts()
    assert counts[COUNT_REFUSED_TOKEN] == 3
    for name in (COUNT_REFUSED_HOST, COUNT_REFUSED_ORIGIN, COUNT_REFUSED_UNKNOWN_BUTTON):
        assert counts[name] == 1, name

    # The token in the query is accepted like the header; no Origin is fine.
    assert await h.press(in_query=True, origin=None) == (200, {"status": "accepted"})
    assert len(h.events()) == 1
    h.clock.advance(1)
    assert await h.press() == (429, {"status": "refused", "reason": "cooldown"})
    assert len(h.events()) == 1 and h.handle.counts()[COUNT_REFUSED_COOLDOWN] == 1
    # The cooldown runs from the last *accepted* press: 2 s after it.
    h.clock.advance(1)
    assert await h.press() == (200, {"status": "accepted"})
    assert len(h.events()) == 2
    # Another button has its own cooldown.
    assert await h.press(button="other") == (200, {"status": "accepted"})
    assert len(h.events()) == 3
    await h.handle.stop_inputs()

    secrets = (TOKEN, wrong_token, wrong_host, "evil-host", foreign_origin, "foreign-origin")
    texts = trace_texts(h.runtime.bus)
    diagnostics = list(h.handle.diagnostics)
    records = [record.getMessage() for record in caplog.records] + [
        repr(record.args) for record in caplog.records
    ]
    for secret in secrets:
        for text in texts + diagnostics + records:
            assert secret not in text, secret
    for name in REFUSAL_COUNTS:
        assert TOKEN not in name


async def test_the_core_compares_the_token_in_constant_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The token comparison is :func:`hmac.compare_digest`, whatever the
    candidate."""

    calls: list[tuple[bytes, bytes]] = []
    real = operator.hmac.compare_digest

    def recording(a: bytes, b: bytes) -> bool:
        calls.append((a, b))
        return real(a, b)

    monkeypatch.setattr(operator.hmac, "compare_digest", recording)
    h = await operated()
    await h.press(token="short")
    await h.press()
    assert [candidate for candidate, _ in calls] == [b"short", TOKEN.encode()]
    assert {expected for _, expected in calls} == {TOKEN.encode()}
    await h.handle.close()


async def test_the_default_listener_disables_the_access_log_and_forbids_a_referer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC22 risk: aiohttp's access log would log the query string — the
    token — so the default listener builds its ``AppRunner`` with
    ``access_log=None``; its ``TCPSite`` binds the listen address; every
    response sets ``Referrer-Policy: no-referrer``. aiohttp's runner and
    site are faked: no socket opens."""

    from aiohttp import web

    built: dict[str, Any] = {}

    class FakeRunner:
        def __init__(self, app: Any, **kwargs: Any) -> None:
            built["runner"] = kwargs
            built["app"] = app
            self.cleaned = False

        async def setup(self) -> None:
            built["setup"] = True

        async def cleanup(self) -> None:
            built["cleanup"] = True

    class FakeSite:
        def __init__(self, runner: Any, host: str, port: int) -> None:
            built["site"] = (host, port)

        async def start(self) -> None:
            built["started"] = True

    monkeypatch.setattr(web, "AppRunner", FakeRunner)
    monkeypatch.setattr(web, "TCPSite", FakeSite)
    seen: list[tuple[str, str, bytes]] = []

    async def dispatch(method: str, path: str, headers: Any, body: bytes) -> tuple[int, str, str]:
        seen.append((method, path, body))
        return 200, '{"status":"accepted"}', "application/json"

    listener = AiohttpListener("127.0.0.1", PORT, dispatch)
    await listener.start()
    assert built["runner"]["access_log"] is None
    assert built["site"] == ("127.0.0.1", PORT) and built["started"]

    class FakeRequest:
        method = "POST"
        path_qs = "/press?token=x"
        headers = {"Host": HOST}

        async def read(self) -> bytes:
            return b"{}"

    response = await listener._handle(FakeRequest())
    assert seen == [("POST", "/press?token=x", b"{}")]
    assert response.status == 200
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert response.headers["Cache-Control"] == "no-store"
    await listener.close()
    assert built["cleanup"]


# -- AC23: the settings ---------------------------------------------------------- #


#: AC23: each refused configuration and the field its diagnostic names.
AC23_VARIANTS = {
    "no-surface": ({"surfaces": {"page": False, "hotkey": False, "overlay": False}}, "surfaces"),
    "token-31": ({"token": "k" * 31}, "token"),
    "token-empty": ({"token": ""}, "token"),
    "button-without-prompt": (
        {"buttons": [{"id": "hype", "label": "Hype!", "cooldown_seconds": 2}]},
        "buttons[0].prompt_text",
    ),
    "non-loopback": ({"listen": {"host": "0.0.0.0", "port": PORT}}, "listen.host"),
}


@pytest.mark.parametrize("variant", sorted(AC23_VARIANTS))
async def test_ac23_each_refused_setting_is_a_startup_diagnostic_naming_it(variant: str) -> None:
    """AC23: no surface enabled, a 31-character token, an empty token, a
    button without ``prompt_text`` and the bind address ``0.0.0.0`` without
    ``allow_non_loopback`` are each one loader diagnostic naming the field,
    with no configured value, and 0 listeners opened."""

    changes, field_name = AC23_VARIANTS[variant]
    factory = FakeServerFactory()
    runtime = runtime_context(clock=ManualClock(START), trigger_registry=TriggerRegistry())
    loader = ModuleLoader(runtime.bus, MODULES, context=runtime, environ={TOKEN_VARIABLE: TOKEN})
    settings = {**operator_settings(**changes), "_server_factory": factory}

    with pytest.raises(ModuleLoadError) as caught:
        await loader.activate_enabled(
            {"enabled_modules": ["operator"], "modules": {"operator": settings}}
        )

    diagnostics = caught.value.diagnostics
    assert len(diagnostics) == 1, diagnostics
    assert diagnostics[0].startswith(f"module 'operator': field '{field_name}':"), diagnostics
    for value in (TOKEN, "k" * 31, "0.0.0.0", HYPE_PROMPT):
        assert value not in diagnostics[0]
    assert factory.listeners == []


LAN_ADDRESS = "192.168.1.20"


def test_ac23_the_validator_accepts_the_ac21_settings_and_a_non_loopback_opt_in() -> None:
    """A specific non-loopback address is accepted with the opt-in; a
    wildcard one is refused even then — a request names the address it
    reached, never the wildcard, so the accepted ``Host`` set would refuse
    every request (R6: the accepted ``Host`` set is the listen address)."""

    accepted = {**operator_settings(), "token": TOKEN}
    assert validate_settings(accepted) == []
    opted_in = {
        **accepted,
        "listen": {"host": LAN_ADDRESS, "port": PORT, "allow_non_loopback": True},
    }
    assert validate_settings(opted_in) == []
    for wildcard in ("0.0.0.0", "::", "[::]"):
        refused = {
            **accepted,
            "listen": {"host": wildcard, "port": PORT, "allow_non_loopback": True},
        }
        (diagnostic,) = validate_settings(refused)
        assert diagnostic.startswith("module 'operator': field 'listen.host':"), diagnostic
        assert wildcard not in diagnostic


async def test_a_listener_bound_to_a_lan_address_accepts_requests_naming_it() -> None:
    """Bound to a specific LAN address with the opt-in, a press naming that
    address in ``Host`` and ``Origin`` is accepted; a loopback name is not."""

    lan = f"{LAN_ADDRESS}:{PORT}"
    h = await operated(listen={"host": LAN_ADDRESS, "port": PORT, "allow_non_loopback": True})
    assert await h.press(host=lan, origin=f"http://{lan}") == (200, {"status": "accepted"})
    h.clock.advance(2)
    assert (await h.press(host=HOST, origin=None))[0] == 403
    assert len(h.events()) == 1
    assert h.handle.counts()[COUNT_REFUSED_HOST] == 1
    await h.handle.close()


async def test_on_port_80_an_origin_without_the_default_port_is_the_pages_own() -> None:
    """On port 80 a browser sends ``Origin: http://127.0.0.1`` — the default
    port left out — for the page it loaded from ``127.0.0.1``: the same
    origin, accepted whether ``Host`` carries ``:80`` or not; a foreign
    origin, or another port, is still refused."""

    h = await operated(listen={"host": "127.0.0.1", "port": 80})
    assert await h.press(host="127.0.0.1", origin="http://127.0.0.1") == (
        200,
        {"status": "accepted"},
    )
    h.clock.advance(2)
    assert await h.press(host="127.0.0.1:80", origin="http://127.0.0.1") == (
        200,
        {"status": "accepted"},
    )
    h.clock.advance(2)
    assert await h.press(host="127.0.0.1", origin="http://127.0.0.1:80") == (
        200,
        {"status": "accepted"},
    )
    h.clock.advance(2)
    for foreign in ("http://127.0.0.1:8080", "https://127.0.0.1", "http://evil.invalid"):
        assert (await h.press(host="127.0.0.1", origin=foreign))[0] == 403
    assert len(h.events()) == 3
    assert h.handle.counts()[COUNT_REFUSED_ORIGIN] == 3
    await h.handle.close()


async def test_ac23_a_profile_that_does_not_enable_operator_opens_no_listener(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC23: a profile enabling another input and not ``operator`` opens 0
    listeners through every lifecycle phase."""

    opened: list[Any] = []

    async def counting_start(self: AiohttpListener) -> None:
        opened.append(self)

    monkeypatch.setattr(AiohttpListener, "start", counting_start)
    clock = ManualClock(START)
    runtime = runtime_context(clock=clock, trigger_registry=TriggerRegistry())
    loader = ModuleLoader(runtime.bus, MODULES, context=runtime, environ={})
    config = {
        "enabled_modules": ["watch"],
        "modules": {"watch": {"channels": [f"{PLATFORM}/{CHANNEL}"], "_sleeper": clock.sleep}},
    }
    activations = await loader.activate_enabled(config)
    assert [activation.name for activation in activations] == ["watch"]
    for activation in activations:
        await activation.handle.prepare()
        await activation.handle.start_inputs()
        await activation.handle.stop_inputs()
        await activation.handle.close()
    assert opened == []
    assert "operator" not in config["enabled_modules"]
    assert "streamerbot" not in config["enabled_modules"]


# -- AC24: the policies ---------------------------------------------------------- #


async def test_ac24_the_default_policy_accepts_a_manual_event() -> None:
    """AC24: with no configured policy the default ``event_kind{[manual]}``
    accepts a press; no Streamer.bot module is enabled."""

    h = await operated()
    assert await h.press() == (200, {"status": "accepted"})
    (decision,) = h.decisions()
    assert decision["reason"] == "accepted:event_kind_manual"
    assert len(h.events()) == 1
    await h.handle.close()


async def test_ac24_a_button_policy_accepts_hype_and_rejects_other() -> None:
    """AC24: ``button{button_ids: [hype]}`` accepts a press of ``hype`` and
    rejects one of ``other`` with ``rejected:button_no_match``; the
    rejected press is traced and never admitted."""

    policy = {
        "combination": "all_of",
        "rules": [{"type": "button", "parameters": {"button_ids": ["hype"]}}],
    }
    h = await operated(triggers={"operator": {"channels": {CHANNEL: policy}}})
    assert await h.press(button="hype") == (200, {"status": "accepted"})
    assert await h.press(button="other") == (200, {"status": "accepted"})
    accepted = events_of(h.runtime.bus, TRACE_INPUT_TRIGGER_ACCEPTED)
    rejected = events_of(h.runtime.bus, TRACE_INPUT_TRIGGER_REJECTED)
    assert [event["payload"]["reason"] for event in accepted] == ["accepted:button_match"]
    assert [event["payload"]["reason"] for event in rejected] == ["rejected:button_no_match"]
    (event,) = h.events()
    assert event["payload"]["button"]["id"] == "hype"
    counts = h.handle.counts()
    assert (counts[COUNT_PRESSED], counts[COUNT_ADMITTED]) == (2, 1)
    await h.handle.close()


def test_ac24_no_streamerbot_module_is_needed_or_enabled() -> None:
    """AC24: the operator is native — nothing in its package names the
    automation bridge."""

    source = (MODULES / "operator" / "__init__.py").read_text(encoding="utf-8")
    manifest = (MODULES / "operator" / "module.yaml").read_text(encoding="utf-8")
    for text in (source, manifest):
        assert "streamerbot" not in text.lower()


async def test_the_handle_settings_never_show_the_token() -> None:
    h = await operated()
    assert TOKEN not in repr(h.handle.settings)
    await h.handle.close()


# -- AC40 and AC21's third surface: the press command ----------------------------- #


class CoreOpener:
    """The press command's ``opener``: records the ``Request`` the command
    built and hands it to the input's request core, as the listener would.

    The ``Host`` header is the one ``urllib`` sends, taken from the URL; a
    non-2xx answer is raised as :class:`urllib.error.HTTPError`, as the real
    opener's error processor does. No socket is opened.
    """

    def __init__(self, harness: Operated) -> None:
        self.harness = harness
        self.requests: list[Any] = []
        self.timeouts: list[Any] = []

    def __call__(self, request: Any, timeout: Any = None) -> Any:
        self.requests.append(request)
        self.timeouts.append(timeout)
        headers = {name: value for name, value in request.header_items()}
        headers["Host"] = request.host
        parts = urllib.parse.urlsplit(request.full_url)
        path = parts.path + (f"?{parts.query}" if parts.query else "")
        status, text = self.harness.handle.core.handle(
            request.get_method(), path, headers, request.data
        )
        if not 200 <= status < 300:
            raise urllib.error.HTTPError(
                request.full_url, status, "refused", email.message.Message(), io.BytesIO(text.encode())
            )
        return Answer(text.encode())


class Answer(io.BytesIO):
    """The response a successful ``open`` returns: a readable context manager."""


class FailingOpener:
    def __init__(self, error: BaseException) -> None:
        self.error = error
        self.calls = 0

    def __call__(self, request: Any, timeout: Any = None) -> Any:
        self.calls += 1
        raise self.error


def write_profile(tmp_path: Path, **changes: Any) -> Path:
    """A profile enabling the operator with the AC21 settings, token by reference."""

    profile = tmp_path / "profile.yaml"
    document = {
        "enabled_modules": ["operator"],
        "modules": {"operator": operator_settings(**changes)},
    }
    profile.write_text(yaml.safe_dump(document), encoding="utf-8")
    return profile


@pytest.fixture
def token_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(TOKEN_VARIABLE, TOKEN)


def assert_no_token(capsys: pytest.CaptureFixture[str]) -> None:
    captured = capsys.readouterr()
    assert TOKEN not in captured.out and TOKEN not in captured.err
    assert TOKEN_VARIABLE not in captured.out and TOKEN_VARIABLE not in captured.err


async def test_ac40_one_post_to_the_press_endpoint_naming_hype_hotkey_and_the_token(
    tmp_path: Path, token_environment: None, capsys: pytest.CaptureFixture[str]
) -> None:
    """AC40: exactly 1 POST to the configured address's ``/press``, its JSON
    body naming ``hype`` with surface ``hotkey`` and the token in the header,
    a 5 s timeout; the core answers accepted, so the command returns 0."""

    h = await operated()
    opener = CoreOpener(h)
    profile = write_profile(tmp_path)
    assert press_command.main(["press", "hype", "--profile", str(profile)], opener=opener) == 0

    (request,) = opener.requests
    assert request.full_url == f"http://127.0.0.1:{PORT}/press"
    assert request.get_method() == "POST"
    assert json.loads(request.data) == {"button": "hype", "surface": "hotkey"}
    headers = {name.lower(): value for name, value in request.header_items()}
    assert headers["x-operator-token"] == TOKEN
    assert headers["content-type"] == "application/json"
    assert opener.timeouts == [5]
    (event,) = h.events()
    assert event["payload"]["button"] == {"id": "hype", "surface": "hotkey"}
    assert h.handle.counts()[COUNT_PRESSED] == 1
    assert_no_token(capsys)
    await h.handle.close()


async def test_ac40_each_core_refusal_returns_2_with_no_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    token_environment: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """AC40: an unknown button, a press inside the cooldown, a wrong token
    and a disabled ``hotkey`` surface are each the core's refusal: the
    command returns 2 and the refusal adds 0 events."""

    profile = write_profile(tmp_path)
    argv = ["press", "hype", "--profile", str(profile)]

    h = await operated()
    opener = CoreOpener(h)
    assert press_command.main(["press", "unknown-button", "--profile", str(profile)], opener=opener) == 2
    assert h.events() == [] and h.handle.counts()[COUNT_REFUSED_UNKNOWN_BUTTON] == 1

    assert press_command.main(argv, opener=opener) == 0
    h.clock.advance(1)
    assert press_command.main(argv, opener=opener) == 2
    assert len(h.events()) == 1 and h.handle.counts()[COUNT_REFUSED_COOLDOWN] == 1

    h.clock.advance(60)
    monkeypatch.setenv(TOKEN_VARIABLE, "W" * 40)
    assert press_command.main(argv, opener=opener) == 2
    assert len(h.events()) == 1 and h.handle.counts()[COUNT_REFUSED_TOKEN] == 1
    assert len(opener.requests) == 4
    await h.handle.close()

    monkeypatch.setenv(TOKEN_VARIABLE, TOKEN)
    disabled = await operated(surfaces={"page": True, "hotkey": False, "overlay": True})
    opener = CoreOpener(disabled)
    assert press_command.main(argv, opener=opener) == 2
    assert disabled.events() == [] and disabled.handle.counts()[COUNT_REFUSED_SURFACE] == 1
    assert len(opener.requests) == 1
    assert_no_token(capsys)
    await disabled.handle.close()


@pytest.mark.parametrize(
    "error",
    [
        urllib.error.URLError(ConnectionRefusedError(111, "Connection refused")),
        ConnectionRefusedError(111, "Connection refused"),
        TimeoutError("timed out"),
        socket.timeout("timed out"),
        urllib.error.URLError(TimeoutError("timed out")),
        http.client.IncompleteRead(b'{"sta', 10),
    ],
    ids=["url-error", "connection-refused", "timeout", "socket-timeout", "url-timeout", "incomplete-read"],
)
def test_ac40_a_connection_error_or_a_timeout_returns_3(
    error: BaseException,
    tmp_path: Path,
    token_environment: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """AC40: the input cannot be reached — refused or timed out — 3."""

    opener = FailingOpener(error)
    profile = write_profile(tmp_path)
    assert press_command.main(["press", "hype", "--profile", str(profile)], opener=opener) == 3
    assert opener.calls == 1
    assert_no_token(capsys)


def _profile_variant(tmp_path: Path, variant: str) -> str:
    if variant == "missing-file":
        return str(tmp_path / "absent.yaml")
    profile = tmp_path / "profile.yaml"
    if variant == "invalid-yaml":
        profile.write_text(f"modules: {{operator: [token: {TOKEN}\n", encoding="utf-8")
    elif variant == "not-a-mapping":
        profile.write_text("- one\n- two\n", encoding="utf-8")
    elif variant == "no-operator":
        profile.write_text(yaml.safe_dump({"modules": {}}), encoding="utf-8")
    elif variant == "no-listen":
        settings = operator_settings()
        del settings["listen"]
        profile.write_text(yaml.safe_dump({"modules": {"operator": settings}}), encoding="utf-8")
    elif variant == "no-port":
        write_profile(tmp_path, listen={"host": "127.0.0.1"})
    elif variant == "no-token":
        settings = operator_settings()
        del settings["token"]
        profile.write_text(yaml.safe_dump({"modules": {"operator": settings}}), encoding="utf-8")
    elif variant == "unset-variable":
        write_profile(tmp_path, token="${OPERATOR_TOKEN_UNSET}")
    elif variant == "partial-reference":
        write_profile(tmp_path, token=f"prefix-${{{TOKEN_VARIABLE}}}")
    elif variant == "newline-token":
        write_profile(tmp_path, token=f"{TOKEN}\n")
    elif variant == "inner-line-break-token":
        write_profile(tmp_path, token=f"{TOKEN}\r\nX-Other: 1")
    elif variant == "non-latin-1-token":
        write_profile(tmp_path, token=f"{TOKEN}\u2603")
    else:  # pragma: no cover - the parametrisation names every variant
        raise AssertionError(variant)
    return str(profile)


@pytest.mark.parametrize(
    "variant",
    [
        "missing-file",
        "invalid-yaml",
        "not-a-mapping",
        "no-operator",
        "no-listen",
        "no-port",
        "no-token",
        "unset-variable",
        "partial-reference",
        "newline-token",
        "inner-line-break-token",
        "non-latin-1-token",
    ],
)
def test_ac40_an_unreadable_or_incomplete_profile_returns_64_and_sends_nothing(
    variant: str,
    tmp_path: Path,
    token_environment: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """AC40: a profile that cannot be read, is not YAML, or lacks the listen
    address or the token is a usage error: 64, no request, no token printed."""

    opener = FailingOpener(AssertionError("no request is sent"))
    profile = _profile_variant(tmp_path, variant)
    assert press_command.main(["press", "hype", "--profile", profile], opener=opener) == 64
    assert opener.calls == 0
    assert_no_token(capsys)


def test_ac40_a_referenced_token_that_is_no_header_value_returns_64_and_sends_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A token whose environment value ends in a line break would make
    ``http.client`` raise an error quoting it: it is refused as a profile
    error before any request, and never printed."""

    monkeypatch.setenv(TOKEN_VARIABLE, f"{TOKEN}\n")
    opener = FailingOpener(AssertionError("no request is sent"))
    profile = write_profile(tmp_path)
    assert press_command.main(["press", "hype", "--profile", str(profile)], opener=opener) == 64
    assert opener.calls == 0
    assert_no_token(capsys)


@pytest.mark.parametrize(
    "argv",
    [["press"], [], ["press", "hype", "extra"], ["release", "hype"], ["press", "hype", "--profile"]],
    ids=["no-button", "no-command", "extra-argument", "unknown-command", "profile-without-path"],
)
def test_ac40_a_usage_error_returns_64_with_a_fixed_message(
    argv: list[str], tmp_path: Path, token_environment: None, capsys: pytest.CaptureFixture[str]
) -> None:
    """AC40: no button argument (or any other usage error) is 64, not
    argparse's 2, and the message never echoes the arguments."""

    opener = FailingOpener(AssertionError("no request is sent"))
    assert press_command.main(argv, opener=opener) == 64
    assert opener.calls == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.strip() == press_command.MESSAGE_USAGE


def test_ac40_the_default_opener_is_urllib_with_a_5_second_timeout(
    tmp_path: Path, token_environment: None, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Without an ``opener`` the command opens through
    ``urllib.request.build_opener().open`` with a 5 s timeout — faked here."""

    calls: list[tuple[Any, Any]] = []

    class FakeDirector:
        def open(self, request: Any, timeout: Any = None) -> Any:
            calls.append((request, timeout))
            return Answer(b'{"status":"accepted"}')

    monkeypatch.setattr(urllib.request, "build_opener", lambda *handlers: FakeDirector())
    profile = write_profile(tmp_path)
    assert press_command.main(["press", "hype", "--profile", str(profile)]) == 0
    (request, timeout), = calls
    assert timeout == 5 and request.full_url == f"http://127.0.0.1:{PORT}/press"
    assert_no_token(capsys)


def test_ac40_the_command_repeats_the_input_names_it_must_not_import() -> None:
    """The command keeps its own copy of the input's route, header and
    surface names (it imports only the standard library and ``yaml``)."""

    assert press_command.PATH_PRESS == operator.PATH_PRESS
    assert press_command.TOKEN_HEADER.lower() == operator.TOKEN_HEADER
    assert press_command.SURFACE_HOTKEY == operator.SURFACE_HOTKEY
    assert press_command.MODULE_NAME == operator.MODULE_NAME
    assert press_command.DEFAULT_HOST == operator.DEFAULT_HOST
    source = (MODULES / "operator" / "__main__.py").read_text(encoding="utf-8")
    imported = {
        line.split()[1].split(".")[0]
        for line in source.splitlines()
        if line.startswith(("import ", "from "))
    }
    assert imported <= {"__future__", "argparse", "http", "json", "os", "re", "sys", "urllib",
                        "collections", "typing", "yaml"}


def test_ac40_importing_the_command_imports_no_runtime_loader_or_lifecycle() -> None:
    """AC40 / A8: with ``modules.operator``, ``modules.operator.__main__``,
    ``core.runtime``, ``core.loader`` and ``core.lifecycle`` removed from
    ``sys.modules``, importing ``modules.operator.__main__`` leaves the last
    three absent."""

    guarded = ("core.runtime", "core.loader", "core.lifecycle")
    removed = ("modules.operator", "modules.operator.__main__", *guarded)
    saved = {name: sys.modules[name] for name in removed if name in sys.modules}
    for name in saved:
        del sys.modules[name]
    try:
        importlib.import_module("modules.operator.__main__")
        assert [name for name in guarded if name in sys.modules] == []
    finally:
        for name in removed:
            sys.modules.pop(name, None)
        sys.modules.update(saved)


async def test_ac21_a_press_from_each_surface_gives_three_manual_events(
    tmp_path: Path, token_environment: None, capsys: pytest.CaptureFixture[str]
) -> None:
    """AC21 (the hotkey third, completing P9's page and overlay halves): a
    press of ``hype`` from the page, from the press command — through a fake
    opener that hands its request to the input's request core — and from
    the overlay produce 3 ``manual`` events, each with ``payload.button.id``
    ``hype``, its surface, the prompt text, author ``operator:hype``, the
    configured destination and a distinct ``message_id``, each decided and
    admitted through the scheduler. After ``stop_inputs`` the command's
    press produces 0 events."""

    h = await operated()
    opener = CoreOpener(h)
    argv = ["press", "hype", "--profile", str(write_profile(tmp_path))]

    assert await h.press(surface="page") == (200, {"status": "accepted"})
    h.clock.advance(2)
    assert press_command.main(argv, opener=opener) == 0
    h.clock.advance(2)
    assert await h.press(surface="overlay") == (200, {"status": "accepted"})

    events = h.events()
    assert [event["payload"]["button"] for event in events] == [
        {"id": "hype", "surface": surface} for surface in ("page", "hotkey", "overlay")
    ]
    for event in events:
        payload = event["payload"]
        assert event["type"] == "channel.chat.message"
        assert payload["kind"] == "manual"
        assert payload["text"] == HYPE_PROMPT
        assert payload["author"] == {"id": "operator:hype", "display_name": "Hype!"}
        assert (payload["platform"], payload["channel_id"]) == (PLATFORM, CHANNEL)
    assert len({event["payload"]["message_id"] for event in events}) == 3
    assert len(h.scheduler.admissions) == 3
    assert h.handle.counts()[COUNT_ADMITTED] == 3
    # The hotkey press was decided too; its trace is published with the next
    # request's flush, so all three are recorded by now.
    decisions = h.decisions()
    assert [d["reason"] for d in decisions] == ["accepted:event_kind_manual"] * 3
    assert sorted(d["source_event_id"] for d in decisions) == sorted(
        event["payload"]["message_id"] for event in events
    )

    await h.handle.stop_inputs()
    h.clock.advance(60)
    assert press_command.main(argv, opener=opener) == 2
    assert len(h.events()) == 3 and h.handle.counts()[COUNT_REFUSED_CLOSED] == 1
    assert_no_token(capsys)
    await h.handle.close()
