from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
import yaml

from core.actions import ActionExecutor, ActionRegistry, AuthorizationPolicy
from core.bus import EventBus
from core.contracts import Counters
from core.lifecycle import PhaseCoordinator, SupervisedTasks
from core.loader import ModuleLoader
from core.runtime import RUNTIME_API, RuntimeContext, Supervision
from modules.brain import MODULE_NAME, activate, validate_settings


ROOT = Path(__file__).parents[1]

SETTINGS = {
    "endpoint": "https://configured.invalid/chat/completions",
    "model": "configured-model",
    "api_key": "never-show-api-key",
}

# The limits the engine owns (R2, R6): required by its manifest's schema and
# checked finite and positive by its declared hook.
LIMITS = {
    "admission": {
        "session_queue_capacity": 4,
        "global_pending_capacity": 64,
        "max_sessions": 32,
        "workers": 4,
        "wait_seconds": 30,
        "total_run_seconds": 120,
    },
    "budget": {
        "model_turns": 5,
        "model_call_seconds": 30,
        "action_seconds": 10,
        "max_tokens": 8192,
        "max_observation_bytes": 5_242_880,
    },
    "conversation_memory": {
        "max_sessions": 64,
        "max_exchanges": 20,
        "max_bytes": 65_536,
        "max_age_seconds": 3600.5,
    },
}

VALID_SETTINGS = {**SETTINGS, **LIMITS}

CATALOG = {
    "twitch": {
        "name": "twitch",
        "produces": ("channel.chat.message",),
        "consumes": ("channel.chat.send",),
        "middleware": False,
    },
    "brain": {
        "name": "brain",
        "produces": ("channel.chat.send",),
        "consumes": ("channel.chat.message",),
        "middleware": False,
    },
    "audit": {
        "name": "audit",
        "produces": (),
        "consumes": ("**",),
        "middleware": True,
        "order": 90,
    },
}


class FakeResponse:
    def __init__(self, status: int, body: Any) -> None:
        self.status = status
        self.body = body
        self.release_calls = 0

    async def json(self) -> Any:
        if isinstance(self.body, Exception):
            raise self.body
        return self.body

    def release(self) -> None:
        self.release_calls += 1


class FakeSession:
    def __init__(self, *results: Any) -> None:
        self.results = list(results)
        self.post_calls: list[dict[str, Any]] = []
        self.close_calls = 0

    async def post(self, url: str, **kwargs: Any) -> Any:
        self.post_calls.append({"url": url, **kwargs})
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result

    async def close(self) -> None:
        self.close_calls += 1


def completion(content: str) -> dict[str, Any]:
    return {"choices": [{"message": {"content": content}}]}


async def activate_with(
    *results: Any, settings_overrides: dict[str, Any] | None = None
):
    session = FakeSession(*results)
    bus = EventBus()
    diagnostics: list[str] = []
    handle = await activate(
        bus,
        {
            **SETTINGS,
            **(settings_overrides or {}),
            "_session_factory": lambda: session,
            "diagnostic_reporter": diagnostics.append,
        },
        CATALOG,
    )
    return handle, bus, session, diagnostics


async def send_input(
    bus: EventBus,
    *,
    text: str = "What is up?",
    message_id: str = "message-7",
    viewer_id: str = "viewer-4",
) -> None:
    await bus.publish(
        "channel.chat.message",
        {
            "text": text,
            "message_id": message_id,
            "chatter_id": viewer_id,
        },
        {"source": "test"},
    )


def outbound(bus: EventBus) -> list[dict[str, Any]]:
    return [
        event
        for event in bus.list_events()
        if event["type"] == "channel.chat.send"
    ]


def assert_sanitized(diagnostics: list[str]) -> None:
    rendered = "\n".join(diagnostics)
    assert SETTINGS["api_key"] not in rendered
    assert SETTINGS["endpoint"] not in rendered
    assert "What is up?" not in rendered


def copy_settings(settings: dict[str, Any]) -> dict[str, Any]:
    return {
        key: dict(value) if isinstance(value, dict) else value
        for key, value in settings.items()
    }


def runtime_context(bus: EventBus | None = None) -> RuntimeContext:
    """The versioned runtime over a real bus, registry, executor and tasks.

    No trigger engine: the engine's manifest declares no triggers, so the
    loader has nothing to register and nothing to refuse.
    """

    target_bus = bus or EventBus()
    counters = Counters()
    policy = AuthorizationPolicy()
    actions = ActionRegistry(authorization=policy)
    supervision = Supervision(target_bus, counters=counters)
    return RuntimeContext(
        bus=target_bus,
        actions=actions,
        supervision=supervision,
        tasks=SupervisedTasks(),
        executor=ActionExecutor(
            actions, policy, supervision=supervision, counters=counters
        ),
    )


def test_manifest_declares_v2_shape_settings_hook_and_no_grant() -> None:
    """R7 supersedes the former whole-manifest equality.

    The manifest carries ``manifest_version`` and ``runtime_api``, keeps the
    routing keys, declares no lifecycle role, states its settings schema and
    names its settings-validation hook. It declares no trigger and no action:
    ``produces`` and ``consumes`` describe routing and authorize nothing.
    """

    manifest_path = ROOT / "modules" / "brain" / "module.yaml"
    manifest_text = manifest_path.read_text(encoding="utf-8")
    manifest = yaml.safe_load(manifest_text)

    assert manifest["name"] == MODULE_NAME == "brain"
    assert manifest["manifest_version"] == 2
    assert manifest["runtime_api"] == RUNTIME_API
    assert manifest["produces"] == ["channel.chat.send"]
    assert manifest["consumes"] == ["channel.chat.message"]
    assert manifest["middleware"] is False
    assert manifest["lifecycle"] == {"roles": []}
    assert "triggers" not in manifest
    assert "actions" not in manifest

    schema = manifest["settings_schema"]
    assert schema["type"] == "object"
    assert set(schema["required"]) == set(VALID_SETTINGS)
    assert set(schema["properties"]) == set(VALID_SETTINGS)
    for group, limits in LIMITS.items():
        group_schema = schema["properties"][group]
        assert group_schema["type"] == "object"
        assert set(group_schema["required"]) == set(limits)
        assert set(group_schema["properties"]) == set(limits)
        for name in limits:
            declared = group_schema["properties"][name]["type"]
            assert declared == ("number" if name.endswith("_seconds") else "integer")
    assert manifest["settings_validator"] == "validate_settings"
    assert callable(validate_settings)
    for value in SETTINGS.values():
        assert value not in manifest_text


def test_settings_hook_names_module_and_field_without_values() -> None:
    """R7/AC24: one diagnostic per offending field; no configured value echoed."""

    assert validate_settings(VALID_SETTINGS) == []

    invalid = copy_settings(VALID_SETTINGS)
    invalid["endpoint"] = "configured.invalid/chat/completions"
    del invalid["conversation_memory"]["max_exchanges"]
    diagnostics = validate_settings(invalid)

    assert diagnostics == [
        "module 'brain': field 'endpoint': must be a well-formed http(s) URL",
        "module 'brain': field 'conversation_memory.max_exchanges': is required",
    ]
    assert len(diagnostics) == 2
    for diagnostic in diagnostics:
        assert "'brain'" in diagnostic
    rendered = "\n".join(diagnostics)
    assert rendered.count(SETTINGS["api_key"]) == 0
    assert rendered.count(invalid["endpoint"]) == 0
    assert validate_settings(["not", "a", "mapping"]) == [
        "module 'brain': field 'settings': must be a mapping"
    ]


@pytest.mark.parametrize(
    ("path", "value", "expected"),
    [
        (("endpoint",), "ftp://configured.invalid/x", "field 'endpoint': must be a well-formed http(s) URL"),
        (("endpoint",), "https://", "field 'endpoint': must be a well-formed http(s) URL"),
        (("endpoint",), "https://host.invalid/a b", "field 'endpoint': must be a well-formed http(s) URL"),
        (("endpoint",), "https://host.invalid:abc/chat/completions", "field 'endpoint': must be a well-formed http(s) URL"),
        (("endpoint",), "https://host.invalid:99999/chat/completions", "field 'endpoint': must be a well-formed http(s) URL"),
        (("endpoint",), None, "field 'endpoint': is required"),
        (("model",), "   ", "field 'model': must be a non-empty string"),
        (("model",), 3, "field 'model': must be a non-empty string"),
        (("api_key",), "", "field 'api_key': must be a non-empty string"),
        (("api_key",), "${MODEL_API_KEY}", "field 'api_key': is an unresolved environment reference"),
        (("admission",), None, "field 'admission': is required"),
        (("budget",), [5], "field 'budget': must be a mapping of limits"),
        (("admission", "workers"), 0, "field 'admission.workers': must be a positive integer"),
        (("admission", "workers"), 2.0, "field 'admission.workers': must be a positive integer"),
        (("admission", "workers"), True, "field 'admission.workers': must be a positive integer"),
        (("admission", "wait_seconds"), float("inf"), "field 'admission.wait_seconds': must be a finite positive number"),
        (("admission", "wait_seconds"), float("nan"), "field 'admission.wait_seconds': must be a finite positive number"),
        (("admission", "wait_seconds"), "30", "field 'admission.wait_seconds': must be a finite positive number"),
        (("budget", "model_call_seconds"), 0, "field 'budget.model_call_seconds': must be a finite positive number"),
        (("budget", "max_tokens"), -1, "field 'budget.max_tokens': must be a positive integer"),
        (("conversation_memory", "max_age_seconds"), -0.5, "field 'conversation_memory.max_age_seconds': must be a finite positive number"),
        (("conversation_memory", "max_exchange"), 3, "field 'conversation_memory.max_exchange': is not a limit this module owns"),
    ],
)
def test_settings_hook_refuses_each_unevaluable_field_with_one_diagnostic(
    path: tuple[str, ...], value: Any, expected: str
) -> None:
    """R2/R6/R7: an absent, non-numeric, infinite or non-positive limit is a diagnostic."""

    invalid = copy_settings(VALID_SETTINGS)
    target: Any = invalid
    for component in path[:-1]:
        target = target[component]
    if value is None:
        del target[path[-1]]
    else:
        target[path[-1]] = value

    diagnostics = validate_settings(invalid)

    assert f"module 'brain': {expected}" in diagnostics
    assert len(diagnostics) == 1
    assert_sanitized(diagnostics)


@pytest.mark.asyncio
async def test_loader_resolves_the_declared_hook_and_grants_nothing_from_produces() -> None:
    """R7: the declared hook name resolves to this package's callable, and
    ``produces: [channel.chat.send]`` puts 0 actions in the registry's
    discovered, ready and authorized views.
    """

    session = FakeSession()
    diagnostics: list[str] = []
    context = runtime_context()
    loader = ModuleLoader(context.bus, ROOT / "modules", context=context, environ={})
    settings = {
        **copy_settings(VALID_SETTINGS),
        "_session_factory": lambda: session,
        "diagnostic_reporter": diagnostics.append,
    }

    activations = await loader.activate_enabled(
        {"enabled_modules": ["brain"], "modules": {"brain": settings}}
    )

    (activation,) = activations
    assert activation.name == "brain"
    assert activation.manifest_version == 2
    assert activation.roles == frozenset()
    assert type(activation.handle).__name__ == "BrainModule"
    assert diagnostics == []
    assert dict(context.actions.discovered()) == {}
    assert dict(context.actions.registered_ready()) == {}
    assert dict(context.actions.authorized(principal="viewer")) == {}
    assert context.actions.bindings() == ()

    coordinator = PhaseCoordinator(
        activations, tasks=context.tasks, reporter=diagnostics.append
    )
    assert (await coordinator.start()).status == 0
    assert (await coordinator.stop()).status == 0
    assert diagnostics == []
    assert session.close_calls == 1


@pytest.mark.asyncio
async def test_loader_refuses_settings_the_hook_rejects_before_activation() -> None:
    """R7/AC24: the declared hook refuses through the loader, value-free."""

    from core.loader import ModuleLoadError

    context = runtime_context()
    loader = ModuleLoader(context.bus, ROOT / "modules", context=context, environ={})
    # Well-formed for the schema, refused by the hook: the schema cannot see
    # that this endpoint has no scheme or that this limit is infinite.
    invalid = copy_settings(VALID_SETTINGS)
    invalid["endpoint"] = "configured.invalid/chat/completions"
    invalid["conversation_memory"]["max_age_seconds"] = float("inf")
    activated: list[str] = []
    invalid["_session_factory"] = lambda: activated.append("session")

    with pytest.raises(ModuleLoadError) as caught:
        await loader.activate_enabled(
            {"enabled_modules": ["brain"], "modules": {"brain": invalid}}
        )

    assert "'brain'" in str(caught.value)
    assert "validate_settings" in str(caught.value)
    assert "2 diagnostics" in str(caught.value)
    assert_sanitized([str(caught.value)])
    assert invalid["endpoint"] not in str(caught.value)
    assert activated == []
    assert loader.activations == []


@pytest.mark.asyncio
async def test_configured_request_contains_capabilities_and_viewer_context() -> None:
    response = FakeResponse(200, completion("[send:channel.chat.send]Hello"))
    handle, bus, session, diagnostics = await activate_with(response)
    try:
        await send_input(bus)

        assert len(session.post_calls) == 1
        request = session.post_calls[0]
        assert request["url"] == SETTINGS["endpoint"]
        assert request["json"]["model"] == SETTINGS["model"]
        assert request["headers"]["Authorization"] == (
            f"Bearer {SETTINGS['api_key']}"
        )
        messages = request["json"]["messages"]
        assert messages[0]["role"] == "system"
        system = messages[0]["content"]
        for module, produced, consumed in (
            ("audit", "", "**"),
            ("brain", "channel.chat.send", "channel.chat.message"),
            ("twitch", "channel.chat.message", "channel.chat.send"),
        ):
            assert module in system
            assert produced in system
            assert consumed in system
        assert system.index("audit") < system.index("brain") < system.index("twitch")
        assert "What is up?" in messages[-1]["content"]
        assert "viewer-4" in messages[-1]["content"]
        assert "message-7" in messages[-1]["content"]
        assert diagnostics == []
        assert response.release_calls == 1
    finally:
        await handle.close()


@pytest.mark.asyncio
async def test_configured_request_strips_string_settings() -> None:
    handle, bus, session, diagnostics = await activate_with(
        FakeResponse(200, completion("[send:channel.chat.send]Hello")),
        settings_overrides={
            "endpoint": f"  {SETTINGS['endpoint']}\n",
            "model": f" {SETTINGS['model']} ",
            "api_key": f"{SETTINGS['api_key']}\n",
        },
    )
    try:
        await send_input(bus)

        request = session.post_calls[0]
        assert request["url"] == SETTINGS["endpoint"]
        assert request["json"]["model"] == SETTINGS["model"]
        assert request["headers"]["Authorization"] == (
            f"Bearer {SETTINGS['api_key']}"
        )
        assert diagnostics == []
    finally:
        await handle.close()


@pytest.mark.asyncio
async def test_tagged_output_is_published_with_source_context() -> None:
    handle, bus, _, diagnostics = await activate_with(
        FakeResponse(200, completion("[send:channel.chat.send]Hello there"))
    )
    try:
        await send_input(bus)

        assert outbound(bus) == [
            {
                "type": "channel.chat.send",
                "payload": {"text": "Hello there"},
                "metadata": {
                    "source": "brain",
                    "source_message_id": "message-7",
                    "viewer_id": "viewer-4",
                },
            }
        ]
        assert diagnostics == []
    finally:
        await handle.close()


@pytest.mark.asyncio
async def test_multiple_sections_preserve_source_order() -> None:
    handle, bus, _, diagnostics = await activate_with(
        FakeResponse(
            200,
            completion(
                "[send:channel.chat.send]First\n"
                "[send:channel.chat.send]Second"
            ),
        )
    )
    try:
        await send_input(bus)

        assert [event["payload"]["text"] for event in outbound(bus)] == [
            "First",
            "Second",
        ]
        assert diagnostics == []
    finally:
        await handle.close()


@pytest.mark.asyncio
async def test_malformed_send_prefix_in_section_body_is_plain_text() -> None:
    text = "The literal [send:example is an incomplete routing tag."
    handle, bus, _, diagnostics = await activate_with(
        FakeResponse(
            200,
            completion(f"[send:channel.chat.send]{text}"),
        )
    )
    try:
        await send_input(bus)

        assert [event["payload"]["text"] for event in outbound(bus)] == [text]
        assert diagnostics == []
    finally:
        await handle.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content",
    [
        "[send:unsupported.event]No",
        "[send:channel.chat.send]",
        "[send:channel.chat.send][send:channel.chat.send]Second",
        "preface[send:channel.chat.send]No",
        "plain untagged output",
        "[send:channel.chat.send]Valid[send:unsupported.event]Invalid",
    ],
)
async def test_invalid_tagged_output_is_rejected_atomically(content: str) -> None:
    handle, bus, _, diagnostics = await activate_with(
        FakeResponse(200, completion(content))
    )
    try:
        await send_input(bus)

        assert outbound(bus) == []
        assert diagnostics == ["brain model response: invalid tagged output"]
        assert_sanitized(diagnostics)
    finally:
        await handle.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (FakeResponse(500, {"api_key": SETTINGS["api_key"]}), "non-success"),
        (FakeResponse(200, {}), "malformed"),
        (asyncio.TimeoutError(SETTINGS["api_key"]), "timed out"),
        (RuntimeError(SETTINGS["api_key"]), "transport failed"),
    ],
)
async def test_model_failures_publish_nothing_and_are_sanitized(
    result: Any, expected: str
) -> None:
    handle, bus, _, diagnostics = await activate_with(result)
    try:
        await send_input(bus)

        assert outbound(bus) == []
        assert len(diagnostics) == 1
        assert expected in diagnostics[0]
        assert_sanitized(diagnostics)
    finally:
        await handle.close()


@pytest.mark.asyncio
async def test_history_is_retained_per_viewer_and_session_close_is_idempotent() -> None:
    handle, bus, session, diagnostics = await activate_with(
        FakeResponse(200, completion("[send:channel.chat.send]First reply")),
        FakeResponse(200, completion("[send:channel.chat.send]Second reply")),
        FakeResponse(200, completion("[send:channel.chat.send]Other reply")),
    )
    try:
        await send_input(bus, text="First question", message_id="one")
        await send_input(bus, text="Second question", message_id="two")
        await send_input(
            bus,
            text="Separate viewer",
            message_id="three",
            viewer_id="viewer-other",
        )

        second_messages = session.post_calls[1]["json"]["messages"]
        assert any("First question" in item["content"] for item in second_messages)
        assert any("First reply" in item["content"] for item in second_messages)
        other_messages = session.post_calls[2]["json"]["messages"]
        assert all("First question" not in item["content"] for item in other_messages)
        assert diagnostics == []
    finally:
        await asyncio.gather(handle.close(), handle.close())

    assert session.close_calls == 1


@pytest.mark.asyncio
async def test_model_requests_for_different_viewers_can_overlap() -> None:
    class CoordinatedSession(FakeSession):
        def __init__(self) -> None:
            super().__init__(
                FakeResponse(200, completion("[send:channel.chat.send]One")),
                FakeResponse(200, completion("[send:channel.chat.send]Two")),
            )
            self.both_started = asyncio.Event()
            self.release = asyncio.Event()

        async def post(self, url: str, **kwargs: Any) -> Any:
            self.post_calls.append({"url": url, **kwargs})
            result = self.results.pop(0)
            if len(self.post_calls) == 2:
                self.both_started.set()
            await self.release.wait()
            return result

    session = CoordinatedSession()
    bus = EventBus()
    diagnostics: list[str] = []
    handle = await activate(
        bus,
        {
            **SETTINGS,
            "_session_factory": lambda: session,
            "diagnostic_reporter": diagnostics.append,
        },
        CATALOG,
    )
    first = asyncio.create_task(
        send_input(bus, message_id="one", viewer_id="viewer-one")
    )
    second = asyncio.create_task(
        send_input(bus, message_id="two", viewer_id="viewer-two")
    )
    try:
        await asyncio.wait_for(session.both_started.wait(), timeout=1)
        session.release.set()
        await asyncio.gather(first, second)

        assert len(session.post_calls) == 2
        assert diagnostics == []
    finally:
        session.release.set()
        await asyncio.gather(first, second, return_exceptions=True)
        await handle.close()


@pytest.mark.asyncio
async def test_viewer_histories_are_lru_bounded() -> None:
    handle, bus, session, diagnostics = await activate_with(
        *(
            FakeResponse(
                200,
                completion(f"[send:channel.chat.send]reply-{index}"),
            )
            for index in range(4)
        ),
        settings_overrides={"max_history_viewers": 2},
    )
    try:
        await send_input(
            bus,
            text="evicted viewer question",
            message_id="one",
            viewer_id="viewer-one",
        )
        await send_input(bus, message_id="two", viewer_id="viewer-two")
        await send_input(bus, message_id="three", viewer_id="viewer-three")
        await send_input(
            bus,
            text="viewer one returns",
            message_id="four",
            viewer_id="viewer-one",
        )

        returning_messages = session.post_calls[3]["json"]["messages"]
        assert all(
            "evicted viewer question" not in message["content"]
            for message in returning_messages
        )
        assert len(handle._histories) == 2
        assert diagnostics == []
    finally:
        await handle.close()


@pytest.mark.asyncio
async def test_partial_publish_failure_remembers_only_delivered_sections() -> None:
    handle, bus, session, diagnostics = await activate_with(
        FakeResponse(
            200,
            completion(
                "[send:channel.chat.send]Delivered reply"
                "[send:channel.chat.send]Undelivered reply"
            ),
        ),
        FakeResponse(200, completion("[send:channel.chat.send]Next reply")),
    )

    async def reject_undelivered(event: dict[str, Any]) -> None:
        if event["payload"]["text"] == "Undelivered reply":
            raise RuntimeError("send failed")

    bus.subscribe("channel.chat.send", reject_undelivered)
    try:
        await send_input(bus, text="First question", message_id="one")
        await send_input(bus, text="Next question", message_id="two")

        next_messages = session.post_calls[1]["json"]["messages"]
        prior_content = "\n".join(
            message["content"] for message in next_messages[:-1]
        )
        assert "Delivered reply" in prior_content
        assert "Undelivered reply" not in prior_content
        assert diagnostics == ["brain output publish: failed"]
    finally:
        await handle.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("first_sent", [False, True])
async def test_delivery_outcome_excludes_refused_sections_from_history(
    first_sent: bool,
) -> None:
    handle, bus, session, diagnostics = await activate_with(
        FakeResponse(200, completion(
            "[send:channel.chat.send]First reply"
            "[send:channel.chat.send]Refused reply"
            "[send:channel.chat.send]Last reply"
        )),
        FakeResponse(200, completion("[send:channel.chat.send]Next reply")),
    )

    def report_delivery(event: dict[str, Any]) -> dict[str, Any]:
        sent = first_sent and event["payload"]["text"] != "Refused reply"
        return {
            **event,
            "metadata": {
                **event["metadata"],
                "delivery_status": "sent" if sent else "failed",
            },
        }

    bus.subscribe("channel.chat.send", report_delivery)
    try:
        await send_input(bus, message_id="one")
        await send_input(bus, message_id="two")
        prior = session.post_calls[1]["json"]["messages"][1:-1]
        rendered = "\n".join(message["content"] for message in prior)
        assert "Refused reply" not in rendered
        if first_sent:
            assert "First reply" in rendered
            assert "Last reply" in rendered
        else:
            assert prior == []
        assert len(outbound(bus)) == 4
        assert diagnostics == []
    finally:
        await handle.close()
