"""Stream-control collaborators (R7).

Section "service registry": the bounded key→service table the runtime context
carries and the module-scoped facade a module publishes through (AC25).

Section "platform poll services": the poll service the twitch module publishes
under ``(poll, twitch)``, verified on a scripted Helix transport, and the
scripted one the fixture platform publishes under ``(poll, fake)`` — both only
on a context that carries a registry (AC28, AC25).

Section "scenes": ``modules/stream_control`` — the manifest and settings, the
``stream.scene.set`` contract over the scripted scene provider (confirmation,
reconciliation, the call deadline), its serialization and drain, readiness
and the ``required`` policy for scenes and polls (R6, R8, R9, R10; AC21,
AC22, AC23, AC24, AC29, AC41).
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


# --------------------------------------------------------------------------- #
# Scenes (R6, R8, R10; AC21, AC22, AC23, AC24, AC29, AC41)
# --------------------------------------------------------------------------- #

from conftest import (  # noqa: E402 - the section's own doubles
    SCENE_RAISE,
    SCENE_SWALLOW,
    ManualClock,
    ScriptedSceneProvider,
    events_of,
    settle,
    wait_until,
)
from core.actions import (  # noqa: E402
    ERROR_EXTERNAL_UNKNOWN,
    ERROR_PROVIDER_NOT_READY,
    ERROR_TIMED_OUT,
    AuthorizationRule,
)
from core.attachments import AttachmentStore  # noqa: E402
from core.contracts import ActionCall, ActionObservation, Destination  # noqa: E402
from core.lifecycle import PhaseCoordinator  # noqa: E402
from modules.stream_control import (  # noqa: E402
    CAUSE_CONFIRMATION_LOST,
    ERROR_PROVIDER_UNAVAILABLE,
    ERROR_RESOURCE_BUSY,
    ERROR_SCENE_NOT_ALLOWED,
    ERROR_SCENE_NOT_APPLIED,
    ERROR_SCENE_UNKNOWN,
    FIELD_POLLS_ENABLED,
    FIELD_SCENES_PROVIDER_URL,
    MANIFEST_PATH as STREAM_CONTROL_MANIFEST,
    MODULE_NAME as STREAM_CONTROL,
    POLL_ACTION,
    PROVIDER_NAME as STREAM_CONTROL_PROVIDER,
    REASON_NO_POLL_SERVICE,
    SCENE_ACTION,
    StreamControlModule,
    StreamControlModuleError,
    activate as activate_stream_control,
    validate_settings as validate_stream_control,
)


STREAM = Destination("fake", "channel-9", "stream")
SCENE_START = 1000.0
#: The manifest's ``timeout_seconds`` for ``stream.scene.set``.
SCENE_TIMEOUT = 15.0
SCENE_PASSWORD = "scene-password-7c1e40a9"
LOOPBACK_URL = "ws://127.0.0.1:4455"
AC41_PATTERN = re.compile(
    r"adoption_lead|lead_seconds|deadline_margin|early_stop|deadline_lead", re.IGNORECASE
)


class LoggedSceneProvider(ScriptedSceneProvider):
    """The scripted scene provider with an ordered request log and held answers.

    ``log`` records every request in provider order as ``(kind, value)``:
    ``("read", answer)`` once a read answered, ``("set", name)`` once a set
    command reached the provider. ``hold_sets`` holds the answer of that many
    set commands *after* the scene was applied (the command left and took
    effect, its answer is pending); ``hold_reads`` holds the reads at those
    0-based indices before they answer. ``read_calls``/``set_calls`` count
    requests at entry, held ones included; :meth:`release` answers every
    held request.
    """

    def __init__(
        self,
        *args: Any,
        hold_sets: int = 0,
        hold_reads: tuple[int, ...] = (),
        hold_lists: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.log: list[tuple[str, Any]] = []
        self.hold_sets = hold_sets
        self.hold_reads = set(hold_reads)
        self.hold_lists = hold_lists
        self.read_calls = 0
        self.set_calls = 0
        self.gates: list[asyncio.Future[None]] = []

    async def _held(self) -> None:
        gate: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self.gates.append(gate)
        await gate

    def release(self) -> None:
        for gate in self.gates:
            if not gate.done():
                gate.set_result(None)

    async def list_scenes(self) -> list[str]:
        if self.hold_lists:
            await self._held()
        return await super().list_scenes()

    async def current_scene(self) -> str:
        index = self.read_calls
        self.read_calls += 1
        if index in self.hold_reads:
            await self._held()
        answer = await super().current_scene()
        self.log.append(("read", answer))
        return answer

    async def set_scene(self, name: str) -> dict[str, Any]:
        self.set_calls += 1
        self.log.append(("set", name))
        answer = await super().set_scene(name)
        if self.hold_sets > 0:
            self.hold_sets -= 1
            await self._held()
        return answer


def scene_settings(
    provider: Any,
    clock: ManualClock,
    *,
    allowed: Sequence[str] = ("Talking",),
    kind: str = "scripted",
    scenes: dict[str, Any] | None = None,
    provider_fields: dict[str, Any] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    settings: dict[str, Any] = {
        "scenes": {
            "provider": {"kind": kind, **(provider_fields or {})},
            "allowed": list(allowed),
            **(scenes or {}),
        },
        "_sleeper": clock.sleep,
        **extra,
    }
    if provider is not None:
        settings["_scene_provider"] = provider
    return settings


def grant_scene(context: Any) -> None:
    context.actions._authorization.grant(
        AuthorizationRule(
            rule_id="grant-scene", action_name=SCENE_ACTION, granted_permissions=("stream.scene",)
        )
    )


class SceneHarness:
    """One stream_control handle on a real executor under a rule granting the scene."""

    def __init__(
        self, context: Any, handle: StreamControlModule, provider: Any, clock: ManualClock
    ) -> None:
        self.context = context
        self.handle = handle
        self.provider = provider
        self.clock = clock
        self.deadline = 100_000.0
        self._calls = 0

    @property
    def runtime(self) -> RuntimeContext:
        return self.context._runtime

    def call(self, scene: str) -> ActionCall:
        self._calls += 1
        return ActionCall(
            action_name=SCENE_ACTION,
            action_version=1,
            arguments={"scene": scene},
            conversation_id="conversation-1",
            run_id="run-1",
            call_id=f"scene-call-{self._calls}",
            source_event_id="source-1",
            destination=STREAM,
            principal="brain",
            deadline=self.deadline,
            message_id="source-1",
        )

    async def set(self, scene: str) -> ActionObservation:
        return await self.runtime.executor.invoke(self.call(scene))

    def start(self, scene: str) -> "asyncio.Future[ActionObservation]":
        return asyncio.ensure_future(self.set(scene))

    def degraded(self) -> list[dict[str, Any]]:
        return [event["payload"] for event in events_of(self.runtime.bus, "module.degraded")]


async def scene_harness(
    provider: Any = None,
    *,
    allowed: Sequence[str] = ("Talking",),
    prepare: bool = True,
    **settings: Any,
) -> SceneHarness:
    clock = ManualClock(SCENE_START)
    if provider is None:
        provider = LoggedSceneProvider()
    runtime = runtime_context(clock=clock)
    context = runtime.for_module(STREAM_CONTROL)
    handle = await activate_stream_control(
        context, scene_settings(provider, clock, allowed=allowed, **settings), {}
    )
    if prepare:
        await handle.prepare()
    grant_scene(runtime)
    return SceneHarness(context, handle, provider, clock)


def assert_scene_failure(observation: ActionObservation, status: str, code: str) -> dict[str, Any]:
    assert observation.status == status, observation
    assert observation.result is None
    assert observation.error is not None
    assert observation.error["code"] == code, observation.error
    return dict(observation.error)


async def finished(future: "asyncio.Future[Any]") -> Any:
    await wait_until(future.done)
    return future.result()


# -- AC21: allowed scene → confirmed state --------------------------------- #


@pytest.mark.asyncio
async def test_an_allowed_scene_is_set_once_and_confirmed_by_one_read_back() -> None:
    """AC21: current ``Gaming``, ``allowed: [Talking]``: ``{scene: Talking}``
    ends ``success`` with ``previous_scene: Gaming`` and ``reconciled:
    false``; the provider saw exactly 1 set command and 1 read-back, after the
    previous-scene read, in that order."""

    h = await scene_harness()
    try:
        assert h.provider.lists == 1  # the prepare probe
        observation = await h.set("Talking")

        assert observation.status == "success", observation.error
        assert observation.result == {
            "scene": "Talking",
            "previous_scene": "Gaming",
            "confirmed_at": SCENE_START,
            "reconciled": False,
        }
        assert h.provider.sets == ["Talking"]
        assert h.provider.log == [("read", "Gaming"), ("set", "Talking"), ("read", "Talking")]
        read_backs = h.provider.log[h.provider.log.index(("set", "Talking")) + 1 :]
        assert read_backs == [("read", "Talking")]
        assert h.provider.current == "Talking"
        assert observation.provenance["provider"] == STREAM_CONTROL_PROVIDER
        assert observation.provenance["emission"] == "emitted"
    finally:
        await h.handle.close()


@pytest.mark.asyncio
async def test_a_scene_outside_the_allowlist_is_refused_with_the_provider_uninvoked() -> None:
    """AC21: ``{scene: Gaming}`` with ``allowed: [Talking]`` is ``refused
    scene_not_allowed`` with 0 provider calls."""

    h = await scene_harness()
    try:
        observation = await h.set("Gaming")

        assert_scene_failure(observation, "refused", ERROR_SCENE_NOT_ALLOWED)
        assert h.provider.log == []
        assert h.provider.read_calls == 0 and h.provider.set_calls == 0
        assert h.provider.lists == 1
    finally:
        await h.handle.close()


@pytest.mark.asyncio
async def test_a_scene_the_provider_does_not_know_is_scene_unknown_with_no_effect() -> None:
    """AC21: ``allowed: [Talking, Ghost]`` and ``{scene: Ghost}`` →
    ``error scene_unknown``. The provider's refusal of the one command is the
    only way the module learns the scene is unknown (R6: the provider's
    status 600): no set command took effect — nothing applied, the current
    scene unchanged — and no read-back follows."""

    h = await scene_harness(allowed=("Talking", "Ghost"))
    try:
        observation = await h.set("Ghost")

        assert_scene_failure(observation, "error", ERROR_SCENE_UNKNOWN)
        assert h.provider.applied == []
        assert h.provider.current == "Gaming"
        assert h.provider.log == [("read", "Gaming"), ("set", "Ghost")]
    finally:
        await h.handle.close()


@pytest.mark.asyncio
async def test_a_read_back_naming_another_scene_is_scene_not_applied() -> None:
    """AC21: the read-back after the set returns ``Gaming`` → ``error
    scene_not_applied``; one set, one read-back, no reconciliation."""

    h = await scene_harness(LoggedSceneProvider(read_answers=["Gaming", "Gaming"]))
    try:
        observation = await h.set("Talking")

        assert_scene_failure(observation, "error", ERROR_SCENE_NOT_APPLIED)
        assert h.provider.sets == ["Talking"]
        assert h.provider.read_calls == 2
    finally:
        await h.handle.close()


@pytest.mark.asyncio
async def test_another_refusal_status_is_scene_not_applied_with_its_code() -> None:
    """R6: any other unsuccessful status on the set command is ``error
    scene_not_applied`` carrying the numeric code; no read-back."""

    h = await scene_harness(LoggedSceneProvider(set_answers=[702]))
    try:
        observation = await h.set("Talking")

        error = assert_scene_failure(observation, "error", ERROR_SCENE_NOT_APPLIED)
        assert error["status_code"] == 702
        assert "702" in error["message"]
        assert h.provider.log == [("read", "Gaming"), ("set", "Talking")]
    finally:
        await h.handle.close()


# -- AC22: lost answers, reconciliation, disconnection, the deadline ------- #


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("set_answer", "reconciliation", "expected"),
    [
        (SCENE_SWALLOW, "Talking", "success"),
        (SCENE_SWALLOW, "Gaming", "external_unknown"),
        (SCENE_SWALLOW, ConnectionError("read lost"), "external_unknown"),
        (SCENE_RAISE, "Gaming", "external_unknown"),
    ],
)
async def test_a_lost_set_answer_is_reconciled_by_exactly_one_read(
    set_answer: str, reconciliation: Any, expected: str
) -> None:
    """AC22: a provider swallowing the set command's answer: exactly 1 set
    command, then 1 reconciliation read — ``Talking`` → ``success
    reconciled: true``; ``Gaming`` → ``external_unknown`` cause
    ``confirmation_lost``; a failing read → ``external_unknown``. The set
    command count stays 1 in every case."""

    provider = LoggedSceneProvider(
        set_answers=[set_answer], read_answers=["Gaming", reconciliation]
    )
    h = await scene_harness(provider)
    try:
        observation = await h.set("Talking")

        assert observation.status == expected, observation
        if expected == "success":
            assert observation.result["reconciled"] is True
            assert observation.result["previous_scene"] == "Gaming"
        else:
            assert observation.error["code"] == ERROR_EXTERNAL_UNKNOWN
            assert observation.error["cause"] == CAUSE_CONFIRMATION_LOST
        assert provider.sets == ["Talking"]
        assert provider.set_calls == 1
        assert provider.read_calls == 2
    finally:
        await h.handle.close()


@pytest.mark.asyncio
async def test_a_failed_read_back_gets_one_reconciliation_read_and_no_second_set() -> None:
    """R6: a disconnection before the read-back answered → exactly one
    reconciliation read; ``Talking`` → ``success reconciled: true``."""

    provider = LoggedSceneProvider(read_answers=["Gaming", ConnectionError("lost"), "Talking"])
    h = await scene_harness(provider)
    try:
        observation = await h.set("Talking")

        assert observation.status == "success", observation.error
        assert observation.result["reconciled"] is True
        assert provider.set_calls == 1
        assert provider.read_calls == 3
    finally:
        await h.handle.close()


@pytest.mark.asyncio
async def test_a_provider_disconnected_at_call_time_is_unavailable_and_withdraws_readiness() -> None:
    """AC22: disconnected at call time → ``error provider_unavailable``, one
    ``module.degraded`` with ``capabilities: ["stream.scene.set"]`` and a
    value-free reason, the action absent from the registered-ready view; the
    next call is ``refused provider_not_ready`` before the provider."""

    h = await scene_harness()
    try:
        assert SCENE_ACTION in h.runtime.actions.registered_ready()
        h.provider.disconnect()

        observation = await h.set("Talking")

        assert_scene_failure(observation, "error", ERROR_PROVIDER_UNAVAILABLE)
        (degraded,) = h.degraded()
        assert degraded["capabilities"] == [SCENE_ACTION]
        assert SCENE_ACTION not in h.runtime.actions.registered_ready()
        assert SCENE_ACTION in h.runtime.actions.discovered()
        assert h.provider.read_calls == 0 and h.provider.set_calls == 0

        invocations = h.runtime.executor.provider_invocations
        again = await h.set("Talking")
        assert_scene_failure(again, "refused", ERROR_PROVIDER_NOT_READY)
        assert h.runtime.executor.provider_invocations == invocations
        assert len(h.degraded()) == 1
    finally:
        await h.handle.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("pending", ["read-back", "set"])
async def test_a_request_pending_at_the_call_deadline_is_external_unknown(pending: str) -> None:
    """AC22/R10: the read-back (or the set command's answer) is held and the
    clock is driven to ``expiry``: nothing happens at ``expiry − 0.01``; at
    ``expiry`` the call ends ``external_unknown`` — the module's own
    ``timeout`` resolved by the emission rule — with 1 set command and 0
    further requests, one record for the call."""

    if pending == "read-back":
        provider = LoggedSceneProvider(hold_reads=(1,))
    else:
        provider = LoggedSceneProvider(hold_sets=1)
    h = await scene_harness(provider)
    h.deadline = SCENE_START + 10.0  # expiry = min(deadline, entry + 15)
    try:
        call = h.start("Talking")
        await wait_until(lambda: len(provider.gates) == 1)
        requests = provider.read_calls + provider.set_calls

        h.clock.advance(9.99)
        await settle()
        assert not call.done()

        h.clock.advance(0.01)
        observation = await finished(call)

        assert observation.status == "external_unknown", observation
        assert observation.error["code"] == ERROR_EXTERNAL_UNKNOWN
        assert "timed out with emission" not in observation.error["message"]
        assert provider.set_calls == 1
        assert provider.read_calls + provider.set_calls == requests
        assert len(h.runtime.executor.outcomes()) == 1
    finally:
        provider.release()
        await h.handle.close()


@pytest.mark.asyncio
async def test_a_deadline_before_the_set_is_a_certain_timeout_with_no_set() -> None:
    """R10: the previous-scene read still pending at ``expiry`` → ``timeout``
    (nothing emitted), 0 set commands."""

    provider = LoggedSceneProvider(hold_reads=(0,))
    h = await scene_harness(provider)
    h.deadline = SCENE_START + 10.0
    try:
        call = h.start("Talking")
        await wait_until(lambda: len(provider.gates) == 1)
        h.clock.advance(10.0)
        observation = await finished(call)

        assert_scene_failure(observation, "timeout", ERROR_TIMED_OUT)
        assert provider.set_calls == 0
    finally:
        provider.release()
        await h.handle.close()


# -- AC23: serialization and drain ---------------------------------------- #


@pytest.mark.asyncio
async def test_two_sessions_are_serialized_and_a_third_is_refused_busy() -> None:
    """AC23: two concurrent sessions on one provider — the second set command
    is issued after the first read-back completed (provider order); a third
    concurrent call with ``max_waiters: 1`` is ``refused resource_busy`` with
    no provider request of its own."""

    provider = LoggedSceneProvider(hold_sets=1)
    h = await scene_harness(provider, allowed=("Talking", "Gaming"))
    try:
        first = h.start("Talking")
        await wait_until(lambda: len(provider.gates) == 1)
        second = h.start("Gaming")
        await settle()
        requests = len(provider.log)

        third = await h.set("Talking")
        assert_scene_failure(third, "refused", ERROR_RESOURCE_BUSY)
        assert len(provider.log) == requests
        assert provider.set_calls == 1

        provider.release()
        one = await finished(first)
        two = await finished(second)

        assert one.status == "success" and two.status == "success", (one, two)
        assert two.result["previous_scene"] == "Talking"
        assert provider.log == [
            ("read", "Gaming"),
            ("set", "Talking"),
            ("read", "Talking"),
            ("read", "Talking"),
            ("set", "Gaming"),
            ("read", "Gaming"),
        ]
        first_read_back = provider.log.index(("read", "Talking"))
        assert provider.log.index(("set", "Gaming")) > first_read_back
        assert h.handle._slot.occupants == 0 and not h.handle._slot.lock.locked()
    finally:
        provider.release()
        await h.handle.close()


@pytest.mark.asyncio
async def test_drain_cancels_the_waiting_call_and_confirms_the_one_in_flight() -> None:
    """AC23: during drain the waiting call ends ``cancelled`` with 0 set
    commands; the call whose set command is in flight receives its read-back
    within the drain deadline and ends ``success``; the drain returns before
    its deadline on the clock."""

    provider = LoggedSceneProvider(hold_sets=1)
    h = await scene_harness(provider, allowed=("Talking", "Gaming"))
    try:
        first = h.start("Talking")
        await wait_until(lambda: len(provider.gates) == 1)
        second = h.start("Gaming")
        await settle()

        drain = asyncio.ensure_future(h.handle.drain(5.0))
        waiting = await finished(second)
        assert_scene_failure(waiting, "cancelled", "cancelled")
        assert provider.sets == ["Talking"]
        assert not drain.done()

        h.clock.advance(1.0)
        provider.release()
        confirmed = await finished(first)
        await finished(drain)

        assert confirmed.status == "success", confirmed
        assert confirmed.result["reconciled"] is False
        assert provider.set_calls == 1
        assert h.clock.now == SCENE_START + 1.0
        late = await h.set("Talking")
        assert late.status == "cancelled"
        assert provider.set_calls == 1
    finally:
        provider.release()
        await h.handle.close()


@pytest.mark.asyncio
async def test_drain_cancels_a_call_still_reading_the_previous_scene() -> None:
    """AC23: a call that holds the provider but has not sent its set command
    (its previous-scene read pending) is a waiting call too: the drain ends
    it ``cancelled`` at once, with 0 set commands, and returns."""

    provider = LoggedSceneProvider(hold_reads=(0,))
    h = await scene_harness(provider)
    try:
        call = h.start("Talking")
        await wait_until(lambda: len(provider.gates) == 1)

        drain = asyncio.ensure_future(h.handle.drain(5.0))
        observation = await finished(call)
        await finished(drain)

        assert_scene_failure(observation, "cancelled", "cancelled")
        assert provider.set_calls == 0
        assert h.clock.now == SCENE_START
    finally:
        provider.release()
        await h.handle.close()


@pytest.mark.asyncio
async def test_a_drain_deadline_passing_first_is_external_unknown_with_no_further_read() -> None:
    """AC23: the in-flight set command's answer is still pending when the
    clock passes the drain deadline → the call ends ``external_unknown``
    (cause ``confirmation_lost``) and no read is started after it; the drain
    returns at its deadline."""

    provider = LoggedSceneProvider(hold_sets=1)
    h = await scene_harness(provider)
    try:
        call = h.start("Talking")
        await wait_until(lambda: len(provider.gates) == 1)

        drain = asyncio.ensure_future(h.handle.drain(5.0))
        await settle()
        h.clock.advance(4.99)
        await settle()
        assert not call.done() and not drain.done()

        h.clock.advance(0.01)
        observation = await finished(call)
        await finished(drain)

        assert observation.status == "external_unknown", observation
        assert observation.error["cause"] == CAUSE_CONFIRMATION_LOST
        assert provider.set_calls == 1
        assert provider.read_calls == 1  # the previous-scene read only
        provider.release()
        await settle()
        assert provider.read_calls == 1
    finally:
        provider.release()
        await h.handle.close()


@pytest.mark.asyncio
async def test_a_previous_read_answering_as_the_drain_starts_sends_no_set_command() -> None:
    """AC23: the previous-scene read answers on the turn the drain begins —
    both are done when the call resumes. No set command leaves: the call
    ends ``cancelled`` with 0 set commands."""

    class DrainOnRead(LoggedSceneProvider):
        handle: Any = None
        drain: Any = None

        async def current_scene(self) -> str:
            self.drain = asyncio.ensure_future(self.handle.drain(5.0))
            await asyncio.sleep(0)
            return await super().current_scene()

    provider = DrainOnRead()
    h = await scene_harness(provider)
    provider.handle = h.handle
    try:
        observation = await h.set("Talking")
        await finished(provider.drain)

        assert_scene_failure(observation, "cancelled", "cancelled")
        assert provider.set_calls == 0
    finally:
        await h.handle.close()


@pytest.mark.asyncio
async def test_a_set_answer_arriving_past_the_drain_deadline_starts_no_read_back() -> None:
    """AC23: the set answer is pending when the drain deadline passes and
    answers on that same turn → the call ends ``external_unknown`` and the
    read-back is never started."""

    provider = LoggedSceneProvider(hold_sets=1)
    h = await scene_harness(provider)
    try:
        call = h.start("Talking")
        await wait_until(lambda: len(provider.gates) == 1)
        drain = asyncio.ensure_future(h.handle.drain(5.0))
        await settle()

        h.handle._drain_expired.set_result(None)
        provider.release()
        observation = await finished(call)
        await finished(drain)

        assert observation.status == "external_unknown", observation
        assert observation.error["cause"] == CAUSE_CONFIRMATION_LOST
        assert provider.set_calls == 1
        assert provider.read_calls == 1  # the previous-scene read only
    finally:
        provider.release()
        await h.handle.close()


async def loaded_stream_control(
    provider: Any,
    clock: ManualClock,
    *,
    extra_modules: dict[str, dict[str, Any]] | None = None,
    attachments: Any = None,
    **settings: Any,
) -> tuple[Any, ModuleLoader, list[Any]]:
    context = runtime_context(clock=clock, attachments=attachments)
    loader = ModuleLoader(context.bus, REPOSITORY / "modules", context=context, environ={})
    modules = {STREAM_CONTROL: {**scene_settings(provider, clock, **settings), "limits": {}}}
    modules.update(extra_modules or {})
    activations = await loader.activate_enabled(
        {"enabled_modules": list(modules), "modules": modules}
    )
    grant_scene(context)
    return context, loader, activations


def loader_call(call_id: str, scene: str, deadline: float = 100_000.0) -> ActionCall:
    return ActionCall(
        action_name=SCENE_ACTION,
        action_version=1,
        arguments={"scene": scene},
        conversation_id="conversation-1",
        run_id="run-1",
        call_id=call_id,
        source_event_id="source-1",
        destination=STREAM,
        principal="brain",
        deadline=deadline,
        message_id="source-1",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("confirmation", ["in time", "too late"])
async def test_the_coordinator_shutdown_completes_within_its_deadline(confirmation: str) -> None:
    """AC23: through the loader and the phase coordinator on the injected
    clock, a shutdown requested while a set command is in flight completes
    within the global shutdown deadline, whether the read-back arrives within
    the drain deadline (``success``) or the clock passes it first
    (``external_unknown``, at the drain deadline itself); the provider is
    closed."""

    clock = ManualClock(SCENE_START)
    provider = LoggedSceneProvider(hold_sets=1)
    context, _loader, activations = await loaded_stream_control(provider, clock)
    coordinator = PhaseCoordinator(
        activations,
        tasks=context.tasks,
        clock=clock,
        sleeper=clock.sleep,
        shutdown_deadline_seconds=10.0,
        hook_timeout_seconds=4.0,
    )
    assert (await coordinator.start()).status == 0
    call = asyncio.ensure_future(context.executor.invoke(loader_call("shutdown-1", "Talking")))
    await wait_until(lambda: len(provider.gates) == 1)

    stop = asyncio.ensure_future(coordinator.stop())
    requested_at = clock.now
    await settle()
    if confirmation == "in time":
        clock.advance(1.0)
        provider.release()
    else:
        for _ in range(8):
            if call.done():
                break
            clock.advance(1.0)
            await settle()
    observation = await finished(call)
    await finished(stop)

    assert clock.now <= requested_at + 10.0
    if confirmation == "in time":
        assert observation.status == "success", observation
        assert stop.result().status == 0, stop.result()
    else:
        assert observation.status == "external_unknown", observation
        assert observation.error["cause"] == CAUSE_CONFIRMATION_LOST
        # The drain hook is handed its whole allowance as its budget, so the
        # module's drain deadline and the coordinator's hook bound fall on
        # one clock instant; whichever is noticed first, the call ends at it
        # and nothing else fails.
        assert clock.now == requested_at + 4.0
        assert all("phase 'drain'" in failure for failure in stop.result().failures)
    assert provider.set_calls == 1
    assert provider.close_calls == 1
    assert SCENE_ACTION not in context.actions.registered_ready()


# -- AC24: settings, credential, kind none ---------------------------------- #


@pytest.mark.parametrize(
    "url",
    ["ws://192.0.2.1:4455", "http://127.0.0.1:4455", "ws://example.invalid:4455", ""],
)
def test_validate_settings_refuses_a_non_loopback_ws_url_naming_only_the_field(url: str) -> None:
    """AC24: a ``websocket`` URL that is not ``ws://`` on loopback nor
    ``wss://`` is refused naming ``scenes.provider.url`` and never the
    value."""

    diagnostics = validate_stream_control(
        {"scenes": {"provider": {"kind": "websocket", "url": url}, "allowed": ["Talking"]}}
    )
    assert len(diagnostics) == 1, diagnostics
    assert FIELD_SCENES_PROVIDER_URL in diagnostics[0]
    assert STREAM_CONTROL in diagnostics[0]
    if url:
        assert url not in diagnostics[0]
    assert "192.0.2.1" not in diagnostics[0]


@pytest.mark.parametrize(
    "url",
    [
        "ws://127.0.0.1:4455",
        "ws://127.8.9.10:4455",
        "ws://localhost:4455",
        "ws://[::1]:4455",
        "wss://example.invalid:4455",
    ],
)
def test_validate_settings_accepts_loopback_ws_and_any_wss(url: str) -> None:
    """AC24: ``ws://127.0.0.1:4455`` and ``wss://example.invalid:4455`` (and
    the other loopback forms) are accepted."""

    assert (
        validate_stream_control(
            {
                "scenes": {
                    "provider": {"kind": "websocket", "url": url, "password": SCENE_PASSWORD},
                    "allowed": ["Talking"],
                }
            }
        )
        == []
    )


@pytest.mark.parametrize(
    ("settings", "field"),
    [
        ({"scenes": {"allowed": ["x" * 65]}}, "scenes.allowed"),
        ({"scenes": {"allowed": [""]}}, "scenes.allowed"),
        ({"scenes": {"allowed": "Talking"}}, "scenes.allowed"),
        ({"scenes": {"provider": {"kind": "scripted"}}}, "scenes.provider.kind"),
        ({"scenes": {"provider": {"kind": "other"}}}, "scenes.provider.kind"),
        ({"scenes": {"provider": {"password": 7}}}, "scenes.provider.password"),
        (
            {"scenes": {"provider": {"connect_timeout_seconds": 0}}},
            "scenes.provider.connect_timeout_seconds",
        ),
        ({"scenes": {"max_waiters": -1}}, "scenes.max_waiters"),
        ({"polls": {"enabled": "yes"}}, "polls.enabled"),
        ({"polls": {"max_options": 0}}, "polls.max_options"),
        ({"polls": {"min_options": 6}}, "polls.min_options"),
        ({"required": "no"}, "required"),
        ({"lead": 1}, "lead"),
    ],
)
def test_validate_settings_names_each_refused_field(settings: dict[str, Any], field: str) -> None:
    diagnostics = validate_stream_control(settings)
    assert any(f"'{field}'" in message for message in diagnostics), diagnostics


def test_validate_settings_accepts_the_declared_defaults() -> None:
    assert validate_stream_control({}) == []
    assert validate_stream_control({"scenes": {"provider": {"kind": "none"}}, "limits": {}}) == []
    assert validate_stream_control(
        {"scenes": {"allowed": ["x" * 64]}, "polls": {"enabled": True}, "required": True}
    ) == []


@pytest.mark.asyncio
async def test_the_password_appears_in_no_trace_or_diagnostic() -> None:
    """AC24: the declared credential is redacted by the loader and never
    appears in a trace, an observation or a diagnostic — a ``websocket``
    provider with no protocol in this step (unreachable, degraded) and a
    scripted provider serving a call, both configured with the password."""

    clock = ManualClock(SCENE_START)
    context, loader, activations = await loaded_stream_control(
        None,
        clock,
        kind="websocket",
        provider_fields={"url": LOOPBACK_URL, "password": SCENE_PASSWORD},
    )
    assert loader.redacted_credentials == 1
    (activation,) = activations
    await activation.handle.prepare()
    degraded = [e["payload"] for e in events_of(context.bus, "module.degraded")]
    assert [d["capabilities"] for d in degraded] == [[SCENE_ACTION]]
    for text in trace_texts(context.bus):
        assert SCENE_PASSWORD not in text
        assert LOOPBACK_URL not in text and "4455" not in text
    await activation.handle.close()

    provider = LoggedSceneProvider()
    context, loader, activations = await loaded_stream_control(
        provider, clock, provider_fields={"password": SCENE_PASSWORD}
    )
    assert loader.redacted_credentials == 1
    (activation,) = activations
    await activation.handle.prepare()
    observation = await context.executor.invoke(loader_call("password-1", "Talking"))
    assert observation.status == "success", observation
    assert SCENE_PASSWORD not in repr(observation)
    for text in trace_texts(context.bus):
        assert SCENE_PASSWORD not in text
    await activation.handle.close()


@pytest.mark.asyncio
async def test_kind_none_declares_the_action_unbound_and_a_proxy_binds_it() -> None:
    """AC24: with ``kind: none`` the action is discovered and not bound; a
    ``proxy`` allowlisting ``stream.scene.set`` binds it, the one provider,
    without an ambiguity diagnostic, through the loader and the coordinator."""

    clock = ManualClock(SCENE_START)
    servers: list[Any] = []

    async def server_factory(handler: Any, **kwargs: Any) -> Any:
        server = SimpleNamespace(kwargs=kwargs, stop=_noop, close=_noop)
        servers.append(server)
        return server

    context, _loader, activations = await loaded_stream_control(
        None,
        clock,
        kind="none",
        # The proxy leases what an agent uploads: it requires a store.
        attachments=AttachmentStore(
            clock=clock,
            max_object_bytes=65_536,
            max_objects=4,
            max_total_bytes=262_144,
            max_bytes_per_run=131_072,
            ttl_seconds=300.0,
        ),
        extra_modules={
            "proxy": {
                "listen": {"host": "127.0.0.1", "port": 8765},
                "pairing_token": "pairing-secret-token",
                "actions": [SCENE_ACTION],
                "_server_factory": server_factory,
                "_sleeper": clock.sleep,
            }
        },
    )
    diagnostics: list[str] = []
    coordinator = PhaseCoordinator(activations, tasks=context.tasks, reporter=diagnostics.append)
    report = await coordinator.start()
    try:
        assert report.status == 0, (report, diagnostics)
        assert SCENE_ACTION in context.actions.discovered()
        (binding,) = context.actions.bindings(SCENE_ACTION)
        assert binding.module == "proxy"
        assert binding.provider_name != STREAM_CONTROL_PROVIDER
        reasons = [e["payload"].get("reason", "") for e in events_of(context.bus, "module.degraded")]
        assert all("Ambiguous" not in reason for reason in reasons), reasons
        assert all("Ambiguous" not in message for message in diagnostics), diagnostics
        assert [e["payload"]["module"] for e in events_of(context.bus, "module.degraded")] == []
    finally:
        await coordinator.stop()


async def _noop() -> None:
    return None


# -- AC29: the required policy for scenes and polls -------------------------- #


def assert_reason_value_free(reason: str) -> None:
    for value in (LOOPBACK_URL, "127.0.0.1", "4455", SCENE_PASSWORD, "ws://"):
        assert value not in reason, reason


@pytest.mark.asyncio
async def test_an_unreachable_provider_degrades_once_and_readiness_is_reached() -> None:
    """AC29 (``required: false``): an unreachable scripted provider at
    ``prepare`` → exactly 1 ``module.degraded`` naming ``stream.scene.set``
    with a value-free reason; the coordinator reaches readiness; the action is
    in the discovered view, not in the registered-ready view, and a call is
    ``refused provider_not_ready`` with 0 provider invocations."""

    clock = ManualClock(SCENE_START)
    provider = LoggedSceneProvider(connected=False)
    context, _loader, activations = await loaded_stream_control(provider, clock)
    diagnostics: list[str] = []
    coordinator = PhaseCoordinator(activations, tasks=context.tasks, reporter=diagnostics.append)
    try:
        assert (await coordinator.start()).status == 0, diagnostics
        (degraded,) = [e["payload"] for e in events_of(context.bus, "module.degraded")]
        assert degraded["module"] == STREAM_CONTROL
        assert degraded["capabilities"] == [SCENE_ACTION]
        assert_reason_value_free(degraded["reason"])
        assert SCENE_ACTION in context.actions.discovered()
        assert SCENE_ACTION not in context.actions.registered_ready()

        observation = await context.executor.invoke(loader_call("unready-1", "Talking"))
        assert_scene_failure(observation, "refused", ERROR_PROVIDER_NOT_READY)
        assert context.executor.provider_invocations == 0
        assert provider.set_calls == 0 and provider.read_calls == 0
    finally:
        await coordinator.stop()


@pytest.mark.asyncio
async def test_a_probe_that_never_answers_is_bounded_on_the_clock() -> None:
    """AC29: the ``list_scenes`` probe is bounded by the provider's connect
    and request timeouts on the injected clock — it does not outlive them."""

    provider = LoggedSceneProvider(hold_lists=True)
    h = await scene_harness(
        provider,
        prepare=False,
        provider_fields={"connect_timeout_seconds": 2, "request_timeout_seconds": 3},
    )
    try:
        prepare = asyncio.ensure_future(h.handle.prepare())
        await wait_until(lambda: len(provider.gates) == 1)
        h.clock.advance(4.99)
        await settle()
        assert not prepare.done()
        h.clock.advance(0.01)
        await finished(prepare)
        assert [d["capabilities"] for d in h.degraded()] == [[SCENE_ACTION]]
        assert SCENE_ACTION not in h.runtime.actions.registered_ready()
    finally:
        provider.release()
        await h.handle.close()


@pytest.mark.asyncio
async def test_a_probe_that_ignores_cancellation_still_ends_at_its_bound() -> None:
    """AC29: a ``list_scenes`` request that swallows its cancellation does
    not hold prepare past the probe bound."""

    class Stubborn(LoggedSceneProvider):
        async def list_scenes(self) -> list[str]:
            gate: asyncio.Future[None] = asyncio.get_running_loop().create_future()
            self.gates.append(gate)
            while True:
                try:
                    await asyncio.shield(gate)
                    return []
                except asyncio.CancelledError:
                    continue

    provider = Stubborn()
    h = await scene_harness(
        provider,
        prepare=False,
        provider_fields={"connect_timeout_seconds": 2, "request_timeout_seconds": 3},
    )
    try:
        prepare = asyncio.ensure_future(h.handle.prepare())
        await wait_until(lambda: len(provider.gates) == 1)
        h.clock.advance(5.0)
        await finished(prepare)
        assert [d["capabilities"] for d in h.degraded()] == [[SCENE_ACTION]]
    finally:
        provider.release()
        await settle()
        await h.handle.close()


@pytest.mark.asyncio
async def test_cancelling_prepare_cancels_the_probe_request() -> None:
    """A prepare cancelled while its probe is pending leaves no
    ``list_scenes`` request running."""

    provider = LoggedSceneProvider(hold_lists=True)
    h = await scene_harness(provider, prepare=False)
    try:
        prepare = asyncio.ensure_future(h.handle.prepare())
        await wait_until(lambda: len(provider.gates) == 1)
        prepare.cancel()
        await settle()
        assert prepare.cancelled()
        assert provider.gates[0].cancelled()
    finally:
        await h.handle.close()


@pytest.mark.asyncio
async def test_required_with_an_unreachable_provider_fails_prepare_naming_the_field() -> None:
    """AC29 (``required: true``): prepare raises naming ``stream_control`` and
    ``scenes.provider.url``; the provider is closed, nothing bound."""

    provider = LoggedSceneProvider(connected=False)
    h = await scene_harness(provider, prepare=False, required=True)
    with pytest.raises(StreamControlModuleError) as refused:
        await h.handle.prepare()
    message = str(refused.value)
    assert STREAM_CONTROL in message and FIELD_SCENES_PROVIDER_URL in message
    assert provider.close_calls == 1
    assert h.runtime.actions.bindings(SCENE_ACTION) == ()
    assert h.degraded() == []
    await h.handle.close()
    assert provider.close_calls == 1


@pytest.mark.asyncio
async def test_required_with_polls_enabled_fails_prepare_naming_polls_enabled() -> None:
    """AC29 / finding P1 (``required: true``): scenes reachable,
    ``polls.enabled: true`` and no poll service resolved → prepare raises
    naming ``stream_control`` and ``polls.enabled`` (not
    ``scenes.provider.url``), after the scene provider was closed (0
    transports open), ``stream.scene.set`` not left bound."""

    provider = LoggedSceneProvider()
    h = await scene_harness(provider, prepare=False, required=True, polls={"enabled": True})
    with pytest.raises(StreamControlModuleError) as refused:
        await h.handle.prepare()
    message = str(refused.value)
    assert message == (
        "stream_control prepare: field 'polls.enabled' unavailable: "
        "no poll service published for any platform"
    )
    assert FIELD_POLLS_ENABLED in message and FIELD_SCENES_PROVIDER_URL not in message
    assert provider.lists == 1
    assert provider.close_calls == 1 and not provider.connected
    assert h.runtime.actions.bindings(SCENE_ACTION) == ()
    assert SCENE_ACTION not in h.runtime.actions.registered_ready()
    assert h.degraded() == []


@pytest.mark.asyncio
async def test_required_checks_scenes_before_polls() -> None:
    """R8 / finding P1: dependencies are checked in settings order — an
    unreachable scene provider with polls enabled names ``scenes.provider.url``;
    ``kind: none`` with polls enabled names ``polls.enabled``."""

    provider = LoggedSceneProvider(connected=False)
    h = await scene_harness(provider, prepare=False, required=True, polls={"enabled": True})
    with pytest.raises(StreamControlModuleError) as refused:
        await h.handle.prepare()
    assert FIELD_SCENES_PROVIDER_URL in str(refused.value)
    assert FIELD_POLLS_ENABLED not in str(refused.value)

    h = await scene_harness(
        None, prepare=False, required=True, kind="none", polls={"enabled": True}
    )
    with pytest.raises(StreamControlModuleError) as refused:
        await h.handle.prepare()
    assert FIELD_POLLS_ENABLED in str(refused.value)


@pytest.mark.asyncio
async def test_required_with_polls_disabled_reaches_readiness() -> None:
    """AC29: ``required: true`` with ``polls.enabled: false`` → ready, the
    scene action bound, no ``module.degraded``."""

    h = await scene_harness(required=True, polls={"enabled": False})
    try:
        assert SCENE_ACTION in h.runtime.actions.registered_ready()
        assert h.runtime.actions.is_ready(STREAM_CONTROL)
        assert h.degraded() == []
        assert h.runtime.actions.bindings(POLL_ACTION) == ()
    finally:
        await h.handle.close()


@pytest.mark.asyncio
async def test_polls_enabled_without_a_service_degrades_the_poll_action_only() -> None:
    """AC29 / finding P1 (``required: false``): ``polls.enabled: true`` with
    no poll service → ``stream.poll.create`` discovered and unbound with one
    ``module.degraded`` (reason ``"no poll service published"``), the scene
    action bound and the module ready."""

    h = await scene_harness(polls={"enabled": True})
    try:
        (degraded,) = h.degraded()
        assert degraded["capabilities"] == [POLL_ACTION]
        assert degraded["reason"] == REASON_NO_POLL_SERVICE
        assert POLL_ACTION in h.runtime.actions.discovered()
        assert h.runtime.actions.bindings(POLL_ACTION) == ()
        assert POLL_ACTION not in h.runtime.actions.registered_ready()
        assert SCENE_ACTION in h.runtime.actions.registered_ready()
    finally:
        await h.handle.close()


@pytest.mark.asyncio
async def test_kind_none_with_polls_disabled_never_degrades() -> None:
    h = await scene_harness(None, kind="none")
    try:
        assert h.degraded() == []
        assert h.runtime.actions.bindings(SCENE_ACTION) == ()
        assert SCENE_ACTION in h.runtime.actions.discovered()
        assert not h.runtime.actions.is_ready(STREAM_CONTROL)
    finally:
        await h.handle.close()


# -- Manifest, packaging, AC41 --------------------------------------------- #


def test_the_manifest_declares_both_actions_and_the_credential() -> None:
    manifest = yaml.safe_load(STREAM_CONTROL_MANIFEST.read_text(encoding="utf-8"))
    assert manifest["name"] == STREAM_CONTROL
    assert manifest["produces"] == [] and manifest["consumes"] == []
    assert manifest["lifecycle"]["roles"] == []
    assert manifest["credentials"] == ["scenes.provider.password"]
    actions = {entry["name"]: entry for entry in manifest["actions"]}
    scene, poll = actions[SCENE_ACTION], actions[POLL_ACTION]
    assert scene["nature"] == poll["nature"] == "write"
    assert scene["required_permissions"] == ["stream.scene"]
    assert poll["required_permissions"] == ["stream.poll"]
    assert scene["supported_destinations"] == [{"platform": "*", "channel_id": "*", "scope": "stream"}]
    assert poll["supported_destinations"] == [{"platform": "*", "channel_id": "*", "scope": "poll"}]
    assert scene["delivery"] == {"text_argument": "none"}
    assert "delivery" not in poll
    assert scene["timeout_seconds"] == 15 and poll["timeout_seconds"] == 20
    assert set(scene["result_schema"]["required"]) == {
        "scene", "previous_scene", "confirmed_at", "reconciled"
    }
    assert set(poll["result_schema"]["required"]) == {
        "poll_id", "state", "question", "options", "started_at", "ends_at", "reconciled"
    }
    assert set(poll["argument_schema"]["required"]) == {"question", "options", "duration_seconds"}
    schema = manifest["settings_schema"]["properties"]
    assert set(schema) == {"scenes", "polls", "required", "limits"}
    assert set(schema["scenes"]["properties"]["provider"]["properties"]["kind"]["enum"]) == {
        "none", "scripted", "websocket"
    }
    assert "twitch" not in STREAM_CONTROL_MANIFEST.read_text(encoding="utf-8").lower()


def test_pyproject_ships_the_stream_control_manifest() -> None:
    """R9 (this module's line): the package-data guard of
    ``tests/test_main.py`` requires it as soon as the directory exists."""

    text = (REPOSITORY / "pyproject.toml").read_text(encoding="utf-8")
    assert '"modules.stream_control" = ["module.yaml"]' in text


def test_no_lead_or_margin_exists_in_stream_control() -> None:
    """AC41: the regex matches 0 lines in ``modules/stream_control/`` (code
    and manifest), and the settings schema declares no lead/margin key."""

    directory = REPOSITORY / "modules" / "stream_control"
    files = sorted(path for path in directory.rglob("*") if path.suffix in {".py", ".yaml"})
    assert files
    for path in files:
        for line in path.read_text(encoding="utf-8").splitlines():
            assert not AC41_PATTERN.search(line), (path, line)

    def keys(schema: Any) -> list[str]:
        found: list[str] = []
        if isinstance(schema, dict):
            for name, value in (schema.get("properties") or {}).items():
                found.append(name)
                found.extend(keys(value))
        return found

    manifest = yaml.safe_load(STREAM_CONTROL_MANIFEST.read_text(encoding="utf-8"))
    for name in keys(manifest["settings_schema"]):
        assert not re.search(r"lead|margin|early_stop", name), name
