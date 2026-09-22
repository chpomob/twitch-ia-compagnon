"""Stream-control collaborators (R7).

Section "service registry": the bounded key→service table the runtime context
carries and the module-scoped facade a module publishes through (AC25).

Section "platform poll services": the poll service the twitch module publishes
under ``(poll, twitch)``, verified on a scripted Helix transport, and the
scripted one the fixture platform publishes under ``(poll, fake)`` — both only
on a context that carries a registry (AC28, AC25).
"""

from __future__ import annotations

import asyncio
import dataclasses
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import aiohttp

import pytest
import yaml

from conftest import FakeResponse, runtime_context, trace_texts

import core.main as application
from core.actions import ActionRegistry, AuthorizationPolicy
from core.bus import EventBus
from core.lifecycle import SupervisedTasks
from core.loader import ModuleLoadError, ModuleLoader
from core.runtime import (
    RUNTIME_API,
    ModuleServices,
    RuntimeContext,
    RuntimeContextError,
    ServiceConflictError,
    ServiceRegistry,
    ServiceRegistryError,
    Supervision,
)
from core.triggers import TriggerEngine, TriggerRegistry
from fixtures.modules import fakeplatform
from modules.twitch import (
    HELIX_POLLS_URL,
    TOKEN_VALIDATION_URL,
    PollMalformedAnswer,
    PollStatusError,
    PollTransportError,
    activate as activate_twitch,
)


REPOSITORY = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------- #
# Service registry (R7, AC25)
# --------------------------------------------------------------------------- #


def test_a_published_service_resolves_to_the_same_object() -> None:
    registry = ServiceRegistry()
    service = object()

    registry.publish("poll", "fake", service, module="fixture")

    assert registry.resolve("poll", "fake") is service
    assert dict(registry.entries()) == {("poll", "fake"): "fixture"}


def test_an_unpublished_key_resolves_to_none() -> None:
    registry = ServiceRegistry()
    registry.publish("poll", "fake", object(), module="fixture")

    assert registry.resolve("poll", "other") is None
    assert registry.resolve("scene", "fake") is None
    assert ServiceRegistry().resolve("poll", "fake") is None


def test_a_second_publication_of_one_key_names_both_modules() -> None:
    registry = ServiceRegistry()
    first = object()
    registry.publish("poll", "fake", first, module="alpha")

    with pytest.raises(ServiceRegistryError) as refused:
        registry.publish("poll", "fake", object(), module="beta")

    assert str(refused.value) == (
        "service (poll, fake) is already published by module 'alpha'; "
        "module 'beta' cannot publish it"
    )
    assert isinstance(refused.value, RuntimeContextError)
    # The refusal leaves the first publication in place.
    assert registry.resolve("poll", "fake") is first
    assert dict(registry.entries()) == {("poll", "fake"): "alpha"}


def test_the_sixty_fifth_entry_is_refused_and_the_sixty_fourth_accepted() -> None:
    registry = ServiceRegistry()
    for index in range(63):
        registry.publish("kind", f"p{index}", object(), module="m")

    registry.publish("kind", "p63", object(), module="m")
    assert len(registry.entries()) == 64

    with pytest.raises(ServiceRegistryError) as refused:
        registry.publish("kind", "p64", object(), module="m")

    assert str(refused.value) == "service registry is full (64 entries)"
    assert len(registry.entries()) == 64
    assert registry.resolve("kind", "p64") is None


@pytest.mark.parametrize(
    ("kind", "platform", "service", "module"),
    [
        ("", "fake", object(), "m"),
        ("poll", " ", object(), "m"),
        ("poll", "fake", object(), ""),
        (1, "fake", object(), "m"),
        ("poll", "fake", None, "m"),
    ],
)
def test_a_publication_requires_text_keys_a_module_and_a_service(
    kind: object, platform: object, service: object, module: object
) -> None:
    registry = ServiceRegistry()

    with pytest.raises(RuntimeContextError):
        registry.publish(kind, platform, service, module=module)  # type: ignore[arg-type]

    assert dict(registry.entries()) == {}


def test_entries_is_a_read_only_view() -> None:
    registry = ServiceRegistry()
    registry.publish("poll", "fake", object(), module="m")

    with pytest.raises(TypeError):
        registry.entries()[("x", "y")] = "n"  # type: ignore[index]


class _PublishAndResolveOnly:
    def publish(self, kind, platform, service, *, module):  # pragma: no cover
        raise AssertionError("never called")

    def resolve(self, kind, platform):  # pragma: no cover
        raise AssertionError("never called")


def test_a_context_refuses_a_services_field_without_the_registry_surface() -> None:
    base = runtime_context()

    with pytest.raises(RuntimeContextError) as bare:
        dataclasses.replace(base, services=object())
    assert "'services'" in str(bare.value)
    assert "publish()" in str(bare.value)

    with pytest.raises(RuntimeContextError) as partial:
        dataclasses.replace(base, services=_PublishAndResolveOnly())
    assert "'services'" in str(partial.value)
    assert "entries()" in str(partial.value)


def test_the_module_facade_publishes_under_the_bound_module_name() -> None:
    registry = ServiceRegistry()
    context = dataclasses.replace(runtime_context(), services=registry)
    service = object()

    scoped = context.for_module("m")
    assert isinstance(scoped.services, ModuleServices)
    assert scoped.services.available is True
    scoped.services.publish("poll", "fake", service)

    assert dict(registry.entries()) == {("poll", "fake"): "m"}
    assert scoped.services.resolve("poll", "fake") is service
    assert context.for_module("other").services.resolve("poll", "fake") is service
    assert dict(scoped.services.entries()) == {("poll", "fake"): "m"}


def test_a_context_without_a_registry_hands_out_an_unavailable_facade() -> None:
    context = dataclasses.replace(runtime_context(), services=None)
    assert context.services is None

    services = context.for_module("m").services
    assert services.available is False
    assert services.resolve("poll", "fake") is None
    assert services.entries() == {}
    with pytest.raises(RuntimeContextError) as refused:
        services.publish("poll", "fake", object())
    assert str(refused.value) == "runtime context: no service registry"


def test_the_assembled_runtime_carries_a_service_registry() -> None:
    runtime = application._assemble_runtime({})

    assert isinstance(runtime.context.services, ServiceRegistry)
    assert runtime.context.for_module("m").services.available is True


PUBLISHING_SOURCE = """
class Handle:
    async def prepare(self):
        pass

    async def start_inputs(self):
        pass

    async def close(self):
        pass


async def activate(context, settings, catalog):
    context.services.publish("poll", "fake", object())
    return Handle()
"""


def _publishing_module(root: Path, name: str) -> None:
    directory = root / name
    directory.mkdir()
    manifest = {
        "name": name,
        "manifest_version": 2,
        "runtime_api": RUNTIME_API,
        "produces": [],
        "consumes": ["channel.*"],
        "middleware": False,
        "lifecycle": {"roles": ["input"]},
    }
    (directory / "module.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    (directory / "__init__.py").write_text(PUBLISHING_SOURCE, encoding="utf-8")


@pytest.mark.asyncio
async def test_two_modules_publishing_one_key_fail_activation_naming_both(
    tmp_path: Path,
) -> None:
    """AC25: the second publisher of one key fails activation.

    The loader turns every activation exception into its own value-free
    refusal naming the failing module, and for a duplicate publication adds
    the holding module read back from the registry, so the reported
    diagnostic itself names both modules.
    """

    _publishing_module(tmp_path, "first_publisher")
    _publishing_module(tmp_path, "second_publisher")
    registry = ServiceRegistry()
    context = dataclasses.replace(runtime_context(), services=registry)
    loader = ModuleLoader(context.bus, tmp_path, context=context)

    with pytest.raises(ModuleLoadError) as refused:
        await loader.activate_enabled(
            {
                "enabled_modules": ["first_publisher", "second_publisher"],
                "modules": {"first_publisher": {}, "second_publisher": {}},
            }
        )

    diagnostic = str(refused.value)
    assert "'second_publisher'" in diagnostic
    assert "'first_publisher'" in diagnostic
    assert "activate" in diagnostic
    assert isinstance(refused.value.__context__, ServiceConflictError)
    assert dict(registry.entries()) == {("poll", "fake"): "first_publisher"}
    assert [activation.name for activation in loader.activations] == [
        "first_publisher"
    ]


FORGING_SOURCE = """
from core.runtime import ServiceConflictError


async def activate(context, settings, catalog):
    raise ServiceConflictError("poll", "fake", "sk-secret-token", "forging")
"""


@pytest.mark.asyncio
async def test_a_forged_service_conflict_reports_only_the_generic_refusal(
    tmp_path: Path,
) -> None:
    """AC25: the holder clause is read back from the registry, not the text."""

    _publishing_module(tmp_path, "forging")
    (tmp_path / "forging" / "__init__.py").write_text(
        FORGING_SOURCE, encoding="utf-8"
    )
    context = dataclasses.replace(runtime_context(), services=ServiceRegistry())
    loader = ModuleLoader(context.bus, tmp_path, context=context)

    with pytest.raises(ModuleLoadError) as refused:
        await loader.activate_enabled(
            {"enabled_modules": ["forging"], "modules": {"forging": {}}}
        )

    assert str(refused.value) == (
        "module 'forging': field 'activate': activation failed"
    )
    assert "sk-secret-token" not in str(refused.value)


@pytest.mark.parametrize("relative", ["core/runtime.py", "core/main.py"])
def test_the_core_files_name_no_service_kind_and_no_platform(relative: str) -> None:
    """AC25: the core defines no kind and names no platform."""

    text = (REPOSITORY / relative).read_text(encoding="utf-8")

    assert text.count("poll") == 0
    assert "poll" not in text.lower()
    assert "twitch" not in text.lower()
    assert re.search(r"\bobs\b", text, re.IGNORECASE) is None


# --------------------------------------------------------------------------- #
# Platform poll services (R7, AC28, AC25)
# --------------------------------------------------------------------------- #


FIXTURE_MODULES = REPOSITORY / "tests" / "fixtures" / "modules"

TWITCH_TOKEN = "never-show-poll-token"
TWITCH_SETTINGS = {
    "client_id": "configured-client",
    "client_secret": "never-show-client-secret",
    "access_token": TWITCH_TOKEN,
    "broadcaster_id": "broadcaster-42",
    "bot_user_id": "bot-24",
    "companion_name": "Companion",
}
FAKE_SETTINGS = {"channel_ids": ["chan-a"], "companion_name": "companion"}

BODY_SECRET = "body-text-must-never-surface"


def helix_poll(
    poll_id: str = "poll-1",
    title: str = "Next game?",
    choices: tuple[str, ...] = ("A", "B"),
    *,
    status: str = "ACTIVE",
    duration: int = 60,
    started_at: str = "2026-09-23T12:00:00Z",
) -> dict[str, Any]:
    """One Helix poll object as the platform answers it."""

    return {
        "id": poll_id,
        "broadcaster_id": "broadcaster-42",
        "title": title,
        "choices": [
            {"id": f"choice-{index}", "title": choice, "votes": 0}
            for index, choice in enumerate(choices)
        ],
        "status": status,
        "duration": duration,
        "started_at": started_at,
        "ended_at": None,
    }


STARTED_EPOCH = 1_790_164_800.0  # 2026-09-23T12:00:00Z


class HelixPollSession:
    """A scripted Helix transport: token validation, then the poll operations.

    ``answers`` is consumed one entry per poll request (a response or an
    exception raised by the transport); every poll request is recorded in
    ``poll_posts`` / ``poll_gets`` before it is answered.
    """

    def __init__(self, *answers: Any) -> None:
        self.answers = list(answers)
        self.validation_calls: list[dict[str, Any]] = []
        self.poll_posts: list[dict[str, Any]] = []
        self.poll_gets: list[dict[str, Any]] = []
        self.other_calls: list[dict[str, Any]] = []
        self.close_calls = 0

    def _answer(self) -> Any:
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer

    async def get(self, url: str, **kwargs: Any) -> Any:
        if url == TOKEN_VALIDATION_URL:
            self.validation_calls.append({"url": url, **kwargs})
            return FakeResponse(
                200,
                {
                    "client_id": TWITCH_SETTINGS["client_id"],
                    "user_id": TWITCH_SETTINGS["bot_user_id"],
                },
            )
        if url == HELIX_POLLS_URL:
            self.poll_gets.append({"url": url, **kwargs})
            return self._answer()
        self.other_calls.append({"url": url, **kwargs})
        raise AssertionError(f"unexpected GET {url}")

    async def post(self, url: str, **kwargs: Any) -> Any:
        if url == HELIX_POLLS_URL:
            self.poll_posts.append({"url": url, **kwargs})
            return self._answer()
        self.other_calls.append({"url": url, **kwargs})
        raise AssertionError(f"unexpected POST {url}")

    async def close(self) -> None:
        self.close_calls += 1


def never_connected() -> aiohttp.ClientConnectorError:
    """What ``aiohttp`` raises when the connection — hence the request — never left."""

    return aiohttp.ClientConnectorError(
        SimpleNamespace(host="api.twitch.tv", port=443, ssl=True),
        OSError(111, "connection refused"),
    )


class PublishSpy:
    """Counts every facade publication, whether or not a registry is bound."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.calls: list[tuple[str, str, str]] = []
        original = ModuleServices.publish
        spy = self

        def publish(facade: ModuleServices, kind: str, platform: str, service: Any) -> None:
            spy.calls.append((facade.module, kind, platform))
            original(facade, kind, platform, service)

        monkeypatch.setattr(ModuleServices, "publish", publish)


def bare_runtime_context(*, with_triggers: bool = False) -> RuntimeContext:
    """``RuntimeContext(bus, actions, supervision, tasks)``: no service registry.

    *with_triggers* adds the trigger engine the loader requires of a module
    declaring triggers — still no ``services``, as in ``tests/test_loader.py``.
    """

    target = EventBus()
    fields: dict[str, Any] = {
        "bus": target,
        "actions": ActionRegistry(authorization=AuthorizationPolicy()),
        "supervision": Supervision(target),
        "tasks": SupervisedTasks(),
    }
    if with_triggers:
        fields["triggers"] = TriggerEngine(
            TriggerRegistry(companion_name="companion"),
            dedup_max_entries=8,
            dedup_ttl_seconds=60.0,
        )
    return RuntimeContext(**fields)


async def activated_twitch(
    session: HelixPollSession,
    *,
    context: Any = None,
    diagnostics: list[str] | None = None,
) -> tuple[Any, Any]:
    scoped = context if context is not None else runtime_context().for_module("twitch")
    handle = await activate_twitch(
        scoped,
        {
            **TWITCH_SETTINGS,
            "_session_factory": lambda: session,
            "diagnostic_reporter": (diagnostics if diagnostics is not None else []).append,
        },
        {},
    )
    return scoped, handle


def assert_value_free(bus: EventBus, diagnostics: list[str], *secrets: str) -> None:
    texts = trace_texts(bus) + diagnostics
    for secret in secrets:
        assert all(secret not in text for text in texts), secret


@pytest.mark.asyncio
async def test_the_twitch_poll_service_creates_with_one_post_on_the_configured_credential() -> None:
    """AC28: one POST to the poll operation with the module's bearer token."""

    session = HelixPollSession(FakeResponse(200, {"data": [helix_poll()]}))
    diagnostics: list[str] = []
    registry = ServiceRegistry()
    context = runtime_context(services=registry)
    scoped, handle = await activated_twitch(
        session, context=context.for_module("twitch"), diagnostics=diagnostics
    )
    try:
        service = registry.resolve("poll", "twitch")
        assert service is handle.poll_service
        assert dict(registry.entries()) == {("poll", "twitch"): "twitch"}

        poll = await service.create("broadcaster-42", "Next game?", ["A", "B"], 60)

        assert len(session.poll_posts) == 1
        assert session.poll_gets == []
        request = session.poll_posts[0]
        assert request["url"] == HELIX_POLLS_URL
        assert request["headers"]["Authorization"] == f"Bearer {TWITCH_TOKEN}"
        assert request["headers"]["Client-Id"] == TWITCH_SETTINGS["client_id"]
        assert request["json"] == {
            "broadcaster_id": "broadcaster-42",
            "title": "Next game?",
            "choices": [{"title": "A"}, {"title": "B"}],
            "duration": 60,
        }
        assert poll == {
            "poll_id": "poll-1",
            "question": "Next game?",
            "options": ["A", "B"],
            "started_at": STARTED_EPOCH,
            "ends_at": STARTED_EPOCH + 60,
            "state": "active",
        }
        assert_value_free(context.bus, diagnostics, TWITCH_TOKEN)
    finally:
        await handle.close()
    assert session.close_calls == 1


@pytest.mark.asyncio
async def test_the_twitch_poll_service_lists_with_one_get() -> None:
    """AC28: ``get`` is one GET on the poll operation for the channel."""

    session = HelixPollSession(
        FakeResponse(
            200,
            {
                "data": [
                    helix_poll(),
                    helix_poll("poll-0", "Old?", ("X", "Y", "Z"), status="COMPLETED"),
                ]
            },
        )
    )
    registry = ServiceRegistry()
    context = runtime_context(services=registry)
    _, handle = await activated_twitch(session, context=context.for_module("twitch"))
    try:
        polls = await registry.resolve("poll", "twitch").get("broadcaster-42")

        assert session.poll_posts == []
        assert len(session.poll_gets) == 1
        request = session.poll_gets[0]
        assert request["url"] == HELIX_POLLS_URL
        assert request["params"] == {"broadcaster_id": "broadcaster-42"}
        assert request["headers"]["Authorization"] == f"Bearer {TWITCH_TOKEN}"
        assert request["headers"]["Client-Id"] == TWITCH_SETTINGS["client_id"]
        assert polls == [
            {
                "poll_id": "poll-1",
                "question": "Next game?",
                "options": ["A", "B"],
                "started_at": STARTED_EPOCH,
                "state": "active",
            },
            {
                "poll_id": "poll-0",
                "question": "Old?",
                "options": ["X", "Y", "Z"],
                "started_at": STARTED_EPOCH,
                "state": "completed",
            },
        ]
    finally:
        await handle.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["create", "get"])
@pytest.mark.parametrize("status", [403, 400, 503])
async def test_a_non_2xx_poll_answer_carries_the_status_and_never_the_body(
    operation: str, status: int
) -> None:
    """R7: a refusal is ``PollStatusError(status)`` with a message built here."""

    session = HelixPollSession(
        FakeResponse(status, {"error": "Forbidden", "message": BODY_SECRET})
    )
    diagnostics: list[str] = []
    context = runtime_context()
    _, handle = await activated_twitch(
        session, context=context.for_module("twitch"), diagnostics=diagnostics
    )
    try:
        service = handle.poll_service
        with pytest.raises(PollStatusError) as refused:
            if operation == "create":
                await service.create("broadcaster-42", "Next game?", ["A", "B"], 60)
            else:
                await service.get("broadcaster-42")

        assert refused.value.status == status
        assert str(status) in refused.value.message
        assert BODY_SECRET not in refused.value.message
        assert BODY_SECRET not in str(refused.value)
        assert len(session.poll_posts) + len(session.poll_gets) == 1
        assert_value_free(context.bus, diagnostics, TWITCH_TOKEN, BODY_SECRET)
    finally:
        await handle.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["create", "get"])
@pytest.mark.parametrize(
    ("failure", "sent"),
    [
        (never_connected, False),
        (aiohttp.ServerDisconnectedError, True),
        (asyncio.TimeoutError, True),
        (lambda: aiohttp.ClientOSError(104, "connection reset"), True),
    ],
    ids=["connector", "disconnected", "timeout", "reset"],
)
async def test_a_transport_failure_says_whether_the_request_left(
    operation: str, failure: Any, sent: bool
) -> None:
    """R7: only a connection never established is ``sent=False``; the rest may
    have reached the platform and are ``sent=True``."""

    session = HelixPollSession(failure())
    diagnostics: list[str] = []
    context = runtime_context()
    _, handle = await activated_twitch(
        session, context=context.for_module("twitch"), diagnostics=diagnostics
    )
    try:
        with pytest.raises(PollTransportError) as lost:
            if operation == "create":
                await handle.poll_service.create("broadcaster-42", "Q?", ["A", "B"], 60)
            else:
                await handle.poll_service.get("broadcaster-42")

        assert lost.value.sent is sent
        assert len(session.poll_posts) + len(session.poll_gets) == 1
        assert_value_free(context.bus, diagnostics, TWITCH_TOKEN)
    finally:
        await handle.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "answer",
    [
        FakeResponse(200, ValueError(BODY_SECRET)),
        FakeResponse(200, {"data": []}),
        FakeResponse(200, {"data": [{"id": "poll-1", "title": BODY_SECRET}]}),
        FakeResponse(200, {"data": [helix_poll(started_at="yesterday")]}),
    ],
    ids=["unreadable", "empty", "partial", "bad-time"],
)
async def test_a_malformed_2xx_create_is_a_malformed_answer(answer: Any) -> None:
    """R7: a 2xx that is not a poll is ``PollMalformedAnswer`` (the request left)."""

    session = HelixPollSession(answer)
    diagnostics: list[str] = []
    context = runtime_context()
    _, handle = await activated_twitch(
        session, context=context.for_module("twitch"), diagnostics=diagnostics
    )
    try:
        with pytest.raises(PollMalformedAnswer) as malformed:
            await handle.poll_service.create("broadcaster-42", "Q?", ["A", "B"], 60)

        assert malformed.value.sent is True
        assert BODY_SECRET not in str(malformed.value)
        assert len(session.poll_posts) == 1
        assert_value_free(context.bus, diagnostics, TWITCH_TOKEN, BODY_SECRET)
    finally:
        await handle.close()


@pytest.mark.asyncio
async def test_a_malformed_poll_list_is_a_malformed_answer() -> None:
    session = HelixPollSession(FakeResponse(200, {"data": [{"id": 3}]}))
    _, handle = await activated_twitch(session)
    try:
        with pytest.raises(PollMalformedAnswer):
            await handle.poll_service.get("broadcaster-42")
        assert len(session.poll_gets) == 1
    finally:
        await handle.close()


@pytest.mark.asyncio
async def test_a_closed_twitch_handle_sends_no_poll_request() -> None:
    session = HelixPollSession()
    _, handle = await activated_twitch(session)
    await handle.close()

    with pytest.raises(PollTransportError) as refused:
        await handle.poll_service.create("broadcaster-42", "Q?", ["A", "B"], 60)

    assert refused.value.sent is False
    assert session.poll_posts == []


@pytest.mark.asyncio
async def test_the_twitch_module_publishes_exactly_once_and_keeps_chat_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R7: with a registry the module publishes once; ``chat.write`` is bound as before."""

    spy = PublishSpy(monkeypatch)
    registry = ServiceRegistry()
    context = runtime_context(services=registry)
    session = HelixPollSession()
    _, handle = await activated_twitch(session, context=context.for_module("twitch"))
    try:
        await handle.prepare()
        assert spy.calls == [("twitch", "poll", "twitch")]
        assert dict(registry.entries()) == {("poll", "twitch"): "twitch"}
        assert [binding.module for binding in context.actions.bindings("chat.write")] == [
            "twitch"
        ]
        assert session.poll_posts == [] and session.poll_gets == []
    finally:
        await handle.close()


@pytest.mark.asyncio
async def test_twitch_activation_without_a_registry_publishes_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R7 (round 2, P2): a default context activates as before, publishing nothing."""

    spy = PublishSpy(monkeypatch)
    context = bare_runtime_context()
    assert context.services is None
    scoped = context.for_module("twitch")
    assert scoped.services.available is False
    session = HelixPollSession()

    _, handle = await activated_twitch(session, context=scoped)
    try:
        await handle.prepare()
        assert spy.calls == []
        assert [binding.module for binding in context.actions.bindings("chat.write")] == [
            "twitch"
        ]
        assert "chat.write" in context.actions.registered_ready()
    finally:
        await handle.close()


@pytest.mark.asyncio
async def test_a_duplicate_twitch_poll_publication_fails_activation() -> None:
    """AC25: the registry's refusal is not caught; the session is released."""

    registry = ServiceRegistry()
    holder = object()
    registry.publish("poll", "twitch", holder, module="rival")
    context = runtime_context(services=registry)
    session = HelixPollSession()

    with pytest.raises(ServiceRegistryError) as refused:
        await activated_twitch(session, context=context.for_module("twitch"))

    assert "'rival'" in str(refused.value) and "'twitch'" in str(refused.value)
    assert registry.resolve("poll", "twitch") is holder
    assert session.close_calls == 1


@pytest.fixture
def clean_fakeplatform():
    fakeplatform.reset()
    yield
    fakeplatform.reset()


@pytest.mark.asyncio
async def test_the_fixture_platform_publishes_a_scripted_poll_service(
    monkeypatch: pytest.MonkeyPatch, clean_fakeplatform: None
) -> None:
    """AC28: through the loader, ``(poll, fake)`` resolves; ``chat.write`` stays
    the only declared action."""

    spy = PublishSpy(monkeypatch)
    registry = ServiceRegistry()
    context = runtime_context(
        services=registry, trigger_registry=TriggerRegistry(companion_name="companion")
    )
    loader = ModuleLoader(context.bus, FIXTURE_MODULES, context=context, environ={})
    activations = await loader.activate_enabled(
        {"enabled_modules": ["fakeplatform"], "modules": {"fakeplatform": dict(FAKE_SETTINGS)}}
    )
    handle = activations[0].handle
    try:
        await handle.prepare()
        service = registry.resolve("poll", "fake")
        assert service is not None and service is handle.poll_service
        assert dict(registry.entries()) == {("poll", "fake"): "fakeplatform"}
        assert spy.calls == [("fakeplatform", "poll", "fake")]
        assert [spec.name for spec in loader.discovered["fakeplatform"].declaration.actions] == [
            "chat.write"
        ]

        # Scripted per channel, recorded, with its own polls table.
        service.script_create("chan-a", "lost")
        with pytest.raises(Exception) as lost:
            await service.create("chan-a", "Next game?", ["A", "B"], 60)
        assert lost.value.sent is True
        poll = await service.create("chan-b", "Other?", ["X", "Y"], 30)
        assert poll["state"] == "active" and poll["ends_at"] == poll["started_at"] + 30
        service.script_get("chan-b", "raise")
        with pytest.raises(Exception) as unread:
            await service.get("chan-b")
        assert unread.value.sent is True
        listed = await service.get("chan-a")
        assert [entry["question"] for entry in listed] == ["Next game?"]
        assert [call["channel_id"] for call in service.creates] == ["chan-a", "chan-b"]
        assert service.gets == ["chan-b", "chan-a"]
        assert set(service.polls) == {"chan-a", "chan-b"}
    finally:
        await handle.close()


@pytest.mark.asyncio
async def test_the_fixture_platform_takes_its_poll_service_through_the_seam(
    clean_fakeplatform: None,
) -> None:
    service = fakeplatform.ScriptedPollService(clock=lambda: 100.0)
    registry = ServiceRegistry()
    context = runtime_context(services=registry)

    handle = await fakeplatform.activate(
        context.for_module("fakeplatform"), {**FAKE_SETTINGS, "poll_service": service}, {}
    )
    try:
        assert registry.resolve("poll", "fake") is service
        assert fakeplatform.poll_service_for() is service
        service.script_create("chan-a", "status:403")
        with pytest.raises(fakeplatform.PollStatusError) as refused:
            await service.create("chan-a", "Q?", ["A", "B"], 60)
        assert refused.value.status == 403
        poll = await service.create("chan-a", "Q?", ["A", "B"], 60)
        assert poll["started_at"] == 100.0 and poll["ends_at"] == 160.0
    finally:
        await handle.close()
    assert fakeplatform.validate_settings({**FAKE_SETTINGS, "poll_service": object()}) == [
        "module 'fakeplatform': field 'poll_service': must expose create() and get()"
    ]


@pytest.mark.asyncio
async def test_fixture_platform_activation_without_a_registry_publishes_nothing(
    monkeypatch: pytest.MonkeyPatch, clean_fakeplatform: None
) -> None:
    """R7 (round 2, P2): directly and through the loader, a context without a
    registry activates the fixture platform with 0 publications."""

    spy = PublishSpy(monkeypatch)
    context = bare_runtime_context()
    scoped = context.for_module("fakeplatform")
    assert scoped.services.available is False

    handle = await fakeplatform.activate(scoped, dict(FAKE_SETTINGS), {})
    try:
        await handle.prepare()
        assert [binding.module for binding in context.actions.bindings("chat.write")] == [
            "fakeplatform"
        ]
    finally:
        await handle.close()

    loaded = bare_runtime_context(with_triggers=True)
    loader = ModuleLoader(loaded.bus, FIXTURE_MODULES, context=loaded, environ={})
    activations = await loader.activate_enabled(
        {"enabled_modules": ["fakeplatform"], "modules": {"fakeplatform": dict(FAKE_SETTINGS)}}
    )
    try:
        await activations[0].handle.prepare()
        assert [binding.module for binding in loaded.actions.bindings("chat.write")] == [
            "fakeplatform"
        ]
    finally:
        await activations[0].handle.close()
    assert spy.calls == []


@pytest.mark.asyncio
async def test_twitch_through_the_loader_without_a_registry_publishes_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R7 (round 2, P2): the shipped twitch module on the loader path, no registry."""

    spy = PublishSpy(monkeypatch)
    context = bare_runtime_context(with_triggers=True)
    assert context.services is None
    session = HelixPollSession()
    loader = ModuleLoader(context.bus, REPOSITORY / "modules", context=context, environ={})
    activations = await loader.activate_enabled(
        {
            "enabled_modules": ["twitch"],
            "modules": {"twitch": {**TWITCH_SETTINGS, "_session_factory": lambda: session}},
        }
    )
    try:
        assert [activation.name for activation in activations] == ["twitch"]
    finally:
        await activations[0].handle.close()
    assert spy.calls == []


@pytest.mark.asyncio
async def test_a_duplicate_fake_poll_publication_fails_activation(
    clean_fakeplatform: None,
) -> None:
    """AC25: the fixture platform does not swallow the registry's refusal."""

    registry = ServiceRegistry()
    registry.publish("poll", "fake", object(), module="rival")
    context = runtime_context(services=registry)

    with pytest.raises(ServiceRegistryError):
        await fakeplatform.activate(
            context.for_module("fakeplatform"), dict(FAKE_SETTINGS), {}
        )
    assert dict(registry.entries()) == {("poll", "fake"): "rival"}
