"""``modules/chat_context``: the ``chat.read`` action over the core transcript (R5).

AC26 — page shape and coverage: the newest ``limit`` retained messages,
oldest first, ``retained`` counted from the same read, ``complete`` only
when everything retained was returned and the count bound was not reached;
``limit`` 0 and 51 are ``invalid_arguments`` at the executor. AC30 —
default-deny: without an applicable rule the call is ``refused
not_authorized`` and the provider is entered 0 times, on two platforms. The
``text`` part equals the rendered page; the manifest is v2 and declares the
action without granting it; no platform name appears in the module.

Every read goes through the real executor built by ``runtime_context`` —
authorization, argument schema, result schema and parts validation are the
executor's, not mocked — with the transcript on the injected clock.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from core.actions import (
    ERROR_INVALID_ARGUMENTS,
    ERROR_NOT_AUTHORIZED,
    AuthorizationRule,
)
from core.context import ChatContext, ChatEntry
from core.contracts import ActionCall, ActionObservation, Destination
from core.lifecycle import PhaseCoordinator
from core.loader import ModuleLoader
from core.runtime import RUNTIME_API, ModuleContext, RuntimeContext
from conftest import ManualClock, runtime_context

from modules.chat_context import (
    CHAT_READ_ACTION,
    CHAT_READ_PROVIDER,
    MANIFEST_PATH,
    MODULE_NAME,
    ChatContextModule,
    ChatContextModuleError,
    activate,
    render_transcript,
    validate_settings,
)


ROOT = Path(__file__).parents[1]
MODULE_DIR = ROOT / "modules" / "chat_context"

#: The accepted ``limits`` block as the entry point hands it over: the
#: ``chat_context`` group is the one this module reads; the others are
#: present so the block looks like the real one.
MAX_MESSAGES = 8
MAX_AGE_SECONDS = 600.0
LIMITS: dict[str, Any] = {
    "chat_context": {
        "max_messages": MAX_MESSAGES,
        "max_bytes": 65536,
        "max_age_seconds": MAX_AGE_SECONDS,
        "max_channels": 8,
    },
    "attachments": {
        "max_objects": 4,
        "max_object_bytes": 1024,
        "max_total_bytes": 4096,
        "max_bytes_per_run": 2048,
        "ttl_seconds": 30,
    },
}
SETTINGS: dict[str, Any] = {"limits": LIMITS}

PRINCIPAL = "brain"


# --------------------------------------------------------------------------- #
# Harness
# --------------------------------------------------------------------------- #


def chat_context(clock: ManualClock) -> ChatContext:
    group = LIMITS["chat_context"]
    return ChatContext(clock=clock, **group)


def module_context(clock: ManualClock | None = None) -> ModuleContext:
    target = clock if clock is not None else ManualClock()
    return runtime_context(clock=target, chat=chat_context(target)).for_module(MODULE_NAME)


def runtime_of(context: ModuleContext) -> RuntimeContext:
    return context._runtime


def grant_chat_read(context: ModuleContext, destination: Destination | None = None) -> None:
    rule: dict[str, Any] = {
        "rule_id": "grant-chat-read",
        "action_name": CHAT_READ_ACTION,
        "granted_permissions": ("chat.read",),
    }
    if destination is not None:
        rule["destination"] = destination
    runtime_of(context).actions._authorization.grant(AuthorizationRule(**rule))


async def prepared_module(context: ModuleContext) -> ChatContextModule:
    handle = await activate(context, SETTINGS, {})
    await handle.prepare()
    return handle


def speak(
    context: ModuleContext,
    platform: str,
    channel_id: str,
    count: int,
    *,
    prefix: str = "m",
) -> list[str]:
    """Append *count* messages to the channel, advancing the clock between them."""

    clock = runtime_of(context).clock
    chat = runtime_of(context).chat
    ids: list[str] = []
    for index in range(1, count + 1):
        message_id = f"{prefix}{index}"
        chat.append(
            platform,
            channel_id,
            ChatEntry(author_id=f"viewer-{index}", message_id=message_id, text=f"hello {index}"),
        )
        ids.append(message_id)
        clock.advance(1.0)
    return ids


def chat_read_call(
    destination: Destination,
    limit: Any,
    call_id: str = "call-1",
) -> ActionCall:
    return ActionCall(
        action_name=CHAT_READ_ACTION,
        action_version=1,
        arguments={"limit": limit},
        conversation_id="conversation-1",
        run_id="run-1",
        call_id=call_id,
        source_event_id="source-1",
        destination=destination,
        principal=PRINCIPAL,
        deadline=10_000.0,
        message_id="source-1",
    )


async def read(
    context: ModuleContext, destination: Destination, limit: Any, call_id: str = "call-1"
) -> ActionObservation:
    return await runtime_of(context).executor.invoke(chat_read_call(destination, limit, call_id))


TWITCH_CHAT = Destination("twitch", "channel-1", "chat")
FAKE_CHAT = Destination("fake", "channel-9", "chat")


# --------------------------------------------------------------------------- #
# AC26: page, order, coverage, argument bounds
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("destination", [TWITCH_CHAT, FAKE_CHAT], ids=["twitch", "fake"])
async def test_read_returns_newest_limit_oldest_first_with_coverage(
    destination: Destination,
) -> None:
    """AC26: ``limit: 3`` on 5 retained → the 3 newest oldest-first,
    ``returned 3``, ``retained 5``, ``complete false``."""

    clock = ManualClock()
    context = module_context(clock)
    handle = await prepared_module(context)
    grant_chat_read(context)
    try:
        # 5 retained on a channel whose bound is 8: below the bound, so the
        # incompleteness below comes from the page size alone.
        speak(context, destination.platform, destination.channel_id, 5)
        read_at = clock()

        observation = await read(context, destination, 3)

        assert observation.status == "success", observation.error
        assert observation.result is not None
        messages = observation.result["messages"]
        assert [m["message_id"] for m in messages] == ["m3", "m4", "m5"]
        assert [m["author_id"] for m in messages] == ["viewer-3", "viewer-4", "viewer-5"]
        assert [m["text"] for m in messages] == ["hello 3", "hello 4", "hello 5"]
        # Dated by the transcript, in ingestion order.
        assert [m["observed_at"] for m in messages] == [1002.0, 1003.0, 1004.0]
        assert observation.result["observed_at"] == read_at
        assert dict(observation.result["coverage"]) == {
            "returned": 3,
            "retained": 5,
            "window_seconds": MAX_AGE_SECONDS,
            "complete": False,
        }
        assert observation.provenance["provider"] == CHAT_READ_PROVIDER
        assert runtime_of(context).executor.provider_invocations == 1
    finally:
        await handle.close()


async def test_read_is_complete_only_below_the_retention_bound() -> None:
    """AC26: ``limit: 10`` on a channel holding 5 of a bound of 8 returns 5
    with ``complete true``; the same limit on a channel *at* the bound returns
    8 with ``complete false`` — every retained message was returned, but the
    bound was reached so older history may already have been evicted."""

    context = module_context()
    handle = await prepared_module(context)
    grant_chat_read(context)
    try:
        below = Destination("fake", "quiet", "chat")
        at_bound = Destination("fake", "busy", "chat")
        speak(context, below.platform, below.channel_id, 5, prefix="q")
        # 10 spoken on a bound of 8: the transcript evicted the 2 oldest.
        speak(context, at_bound.platform, at_bound.channel_id, 10, prefix="b")

        complete = await read(context, below, 10, "call-below")
        assert complete.status == "success", complete.error
        assert complete.result is not None
        assert [m["message_id"] for m in complete.result["messages"]] == [
            "q1", "q2", "q3", "q4", "q5",
        ]
        assert dict(complete.result["coverage"]) == {
            "returned": 5,
            "retained": 5,
            "window_seconds": MAX_AGE_SECONDS,
            "complete": True,
        }

        saturated = await read(context, at_bound, 10, "call-at-bound")
        assert saturated.status == "success", saturated.error
        assert saturated.result is not None
        assert [m["message_id"] for m in saturated.result["messages"]] == [
            "b3", "b4", "b5", "b6", "b7", "b8", "b9", "b10",
        ]
        assert dict(saturated.result["coverage"]) == {
            "returned": 8,
            "retained": MAX_MESSAGES,
            "window_seconds": MAX_AGE_SECONDS,
            "complete": False,
        }

        # Exactly at the bound with no eviction yet is still incomplete: the
        # rule is the bound being reached, not history being lost.
        exact = Destination("fake", "exact", "chat")
        speak(context, exact.platform, exact.channel_id, MAX_MESSAGES, prefix="e")
        at_exact = await read(context, exact, 50, "call-exact")
        assert at_exact.status == "success", at_exact.error
        assert at_exact.result is not None
        assert at_exact.result["coverage"]["returned"] == MAX_MESSAGES
        assert at_exact.result["coverage"]["retained"] == MAX_MESSAGES
        assert at_exact.result["coverage"]["complete"] is False
    finally:
        await handle.close()


async def test_read_of_an_unknown_or_aged_out_channel_is_empty_and_complete() -> None:
    """R5: a channel never spoken in, or whose every message outlived
    ``max_age_seconds`` on the injected clock, reads as 0 of 0, complete,
    with the empty-transcript marker as its text part; ``retained`` and the
    page come from the same read so the two never drift."""

    clock = ManualClock()
    context = module_context(clock)
    handle = await prepared_module(context)
    grant_chat_read(context)
    try:
        silent = await read(context, Destination("fake", "silent", "chat"), 5, "call-silent")
        assert silent.status == "success", silent.error
        assert silent.result is not None
        assert silent.result["messages"] == ()
        assert dict(silent.result["coverage"]) == {
            "returned": 0,
            "retained": 0,
            "window_seconds": MAX_AGE_SECONDS,
            "complete": True,
        }
        assert silent.parts == ({"type": "text", "text": "(no retained messages)"},)

        speak(context, FAKE_CHAT.platform, FAKE_CHAT.channel_id, 2)
        clock.advance(MAX_AGE_SECONDS + 1.0)
        aged = await read(context, FAKE_CHAT, 5, "call-aged")
        assert aged.status == "success", aged.error
        assert aged.result is not None
        assert aged.result["coverage"]["retained"] == 0
        assert aged.result["coverage"]["returned"] == 0
        assert aged.result["coverage"]["complete"] is True
    finally:
        await handle.close()


@pytest.mark.parametrize("limit", [0, 51], ids=["zero", "fifty-one"])
async def test_limit_outside_1_to_50_is_invalid_arguments_before_the_provider(
    limit: int,
) -> None:
    """AC26: ``limit: 0`` and ``limit: 51`` are ``error invalid_arguments``,
    refused by the executor's schema check — the provider is entered 0 times."""

    context = module_context()
    handle = await prepared_module(context)
    grant_chat_read(context)
    try:
        speak(context, FAKE_CHAT.platform, FAKE_CHAT.channel_id, 2)

        observation = await read(context, FAKE_CHAT, limit)

        assert observation.status == "error"
        assert observation.error is not None
        assert observation.error["code"] == ERROR_INVALID_ARGUMENTS
        assert observation.result is None
        assert runtime_of(context).executor.provider_invocations == 0
    finally:
        await handle.close()


async def test_provider_refuses_an_out_of_range_limit_on_its_own() -> None:
    """R5: invoked outside the executor, the provider re-checks ``limit`` and
    answers ``invalid_arguments`` rather than paging with a bad size."""

    context = module_context()
    handle = await prepared_module(context)
    try:
        (binding,) = runtime_of(context).actions.bindings(CHAT_READ_ACTION)

        class _Invocation:
            def __init__(self, call: ActionCall) -> None:
                self.call = call
                self.emission: list[str] = []

            def mark_not_emitted(self) -> None:
                self.emission.append("not_emitted")

        for bad in (0, 51, True, "3"):
            invocation = _Invocation(chat_read_call(FAKE_CHAT, bad))
            observation = await binding.provider.invoke(invocation)
            assert observation.status == "error"
            assert observation.error is not None
            assert observation.error["code"] == ERROR_INVALID_ARGUMENTS
            assert invocation.emission == ["not_emitted"]
    finally:
        await handle.close()


# --------------------------------------------------------------------------- #
# AC30: default-deny on both platforms
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("destination", [TWITCH_CHAT, FAKE_CHAT], ids=["twitch", "fake"])
async def test_read_without_a_rule_is_refused_and_the_provider_is_not_entered(
    destination: Destination,
) -> None:
    """AC30: no applicable rule → ``refused not_authorized``, provider invoked
    0 times, on ``twitch/*/chat`` and on ``fake/*/chat`` alike — declaring
    the action and binding the provider granted nothing."""

    context = module_context()
    handle = await prepared_module(context)
    try:
        speak(context, destination.platform, destination.channel_id, 3)
        assert CHAT_READ_ACTION in runtime_of(context).actions.registered_ready()
        assert runtime_of(context).actions.authorized(principal=PRINCIPAL) == {}

        observation = await read(context, destination, 3)

        assert observation.status == "refused"
        assert observation.error is not None
        assert observation.error["code"] == ERROR_NOT_AUTHORIZED
        assert observation.result is None
        assert observation.parts == ()
        assert runtime_of(context).executor.provider_invocations == 0
    finally:
        await handle.close()


async def test_a_rule_scoped_to_one_platform_does_not_reach_the_other() -> None:
    """AC30/R5: a grant on ``fake/*/chat`` authorizes the fake channel and
    leaves the twitch one refused, the provider entered exactly once."""

    context = module_context()
    handle = await prepared_module(context)
    grant_chat_read(context, Destination("fake", "*", "chat"))
    try:
        speak(context, FAKE_CHAT.platform, FAKE_CHAT.channel_id, 1)
        speak(context, TWITCH_CHAT.platform, TWITCH_CHAT.channel_id, 1)

        allowed = await read(context, FAKE_CHAT, 1, "call-fake")
        refused = await read(context, TWITCH_CHAT, 1, "call-twitch")

        assert allowed.status == "success", allowed.error
        assert refused.status == "refused"
        assert refused.error is not None
        assert refused.error["code"] == ERROR_NOT_AUTHORIZED
        assert runtime_of(context).executor.provider_invocations == 1
    finally:
        await handle.close()


# --------------------------------------------------------------------------- #
# The text part (R4)
# --------------------------------------------------------------------------- #


async def test_text_part_equals_the_rendered_page() -> None:
    """R4/R5: the observation carries exactly one ``text`` part rendering the
    returned messages — one line per message with its transcript instant,
    author and text, oldest first — and nothing else."""

    context = module_context()
    handle = await prepared_module(context)
    grant_chat_read(context)
    try:
        chat = runtime_of(context).chat
        clock = runtime_of(context).clock
        chat.append(
            "fake", "channel-9", ChatEntry(author_id="ada", message_id="a", text="first")
        )
        clock.advance(0.5)
        chat.append(
            "fake", "channel-9", ChatEntry(author_id="bob", message_id="b", text="two\nlines")
        )
        clock.advance(0.25)
        chat.append("fake", "channel-9", ChatEntry(author_id="cy", message_id="c", text=""))

        observation = await read(context, FAKE_CHAT, 2)

        assert observation.status == "success", observation.error
        assert observation.result is not None
        expected = "[1000.500] bob: two lines\n[1000.750] cy: "
        assert observation.parts == ({"type": "text", "text": expected},)
        assert observation.parts[0]["text"] == render_transcript(
            observation.result["messages"]
        )
    finally:
        await handle.close()


def test_render_transcript_shape() -> None:
    assert render_transcript([]) == "(no retained messages)"
    assert (
        render_transcript(
            [
                {"observed_at": 1.0, "author_id": "x", "text": "hi"},
                {"observed_at": 2.25, "author_id": "y", "text": "there"},
            ]
        )
        == "[1.000] x: hi\n[2.250] y: there"
    )


# --------------------------------------------------------------------------- #
# Manifest, settings hook, activation (R7)
# --------------------------------------------------------------------------- #


def test_manifest_is_v2_and_declares_chat_read_without_granting_it() -> None:
    """R7/R5: manifest v2 with no role, no event, no credential, a settings
    schema accepting only the reserved ``limits`` block, the declared hook,
    and exactly the ``chat.read`` read action over ``*/*/chat`` — a `read`
    carries no delivery capability and no grant lives in the file."""

    manifest = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert MANIFEST_PATH == MODULE_DIR / "module.yaml"

    assert manifest["name"] == MODULE_NAME == "chat_context"
    assert manifest["manifest_version"] == 2
    assert manifest["runtime_api"] == RUNTIME_API
    assert manifest["produces"] == []
    assert manifest["consumes"] == []
    assert manifest["middleware"] is False
    assert manifest["lifecycle"] == {"roles": []}
    assert manifest["credentials"] == []
    assert manifest["settings_validator"] == "validate_settings"

    schema = manifest["settings_schema"]
    assert schema["type"] == "object"
    assert set(schema["properties"]) == {"limits"}
    assert schema["properties"]["limits"]["type"] == "object"
    assert schema["additionalProperties"] is False
    assert "required" not in schema

    (action,) = manifest["actions"]
    assert action["name"] == "chat.read"
    assert action["version"] == 1
    assert action["nature"] == "read"
    assert action["required_permissions"] == ["chat.read"]
    assert action["supported_destinations"] == [
        {"platform": "*", "channel_id": "*", "scope": "chat"}
    ]
    assert action["argument_schema"]["required"] == ["limit"]
    limit = action["argument_schema"]["properties"]["limit"]
    assert (limit["type"], limit["minimum"], limit["maximum"]) == ("integer", 1, 50)
    assert action["argument_schema"]["additionalProperties"] is False
    result = action["result_schema"]
    assert set(result["required"]) == {"messages", "observed_at", "coverage"}
    message = result["properties"]["messages"]["items"]
    assert set(message["required"]) == {"author_id", "message_id", "text", "observed_at"}
    assert set(result["properties"]["coverage"]["required"]) == {
        "returned", "retained", "window_seconds", "complete",
    }
    assert action["timeout_seconds"] == 5
    assert action["idempotency"] == "natural"
    assert "delivery" not in action
    assert set(action) == {
        "name",
        "version",
        "description",
        "argument_schema",
        "result_schema",
        "nature",
        "required_permissions",
        "supported_destinations",
        "timeout_seconds",
        "idempotency",
    }
    assert set(manifest) == {
        "name",
        "manifest_version",
        "runtime_api",
        "produces",
        "consumes",
        "middleware",
        "lifecycle",
        "settings_schema",
        "settings_validator",
        "credentials",
        "actions",
    }
    # No grant lives in the file: the permission is *required* by the
    # action and appears nowhere else in the declaration.
    assert yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8")) == manifest
    assert not any(key in manifest for key in ("rules", "authorization", "grants"))


def test_module_names_no_platform() -> None:
    """R5/AC31: ``grep twitch`` over the module directory is 0 lines — the
    platform is the destination's, a runtime value, never a name in code."""

    completed = subprocess.run(
        ["grep", "-r", "-i", "-c", "twitch", str(MODULE_DIR / "__init__.py"), str(MANIFEST_PATH)],
        capture_output=True,
        text=True,
        check=False,
    )
    # ``grep -c`` prints ``<file>:0`` per file and exits 1 when nothing matched.
    assert completed.returncode == 1, completed.stdout
    assert all(line.endswith(":0") for line in completed.stdout.splitlines())
    for path in (MODULE_DIR / "__init__.py", MANIFEST_PATH):
        assert "twitch" not in path.read_text(encoding="utf-8").lower()


def test_settings_hook_requires_the_accepted_limits_group_and_names_fields() -> None:
    """R7/AC24: the accepted block is the only setting; ``limits.chat_context``
    is required, its two read limits finite and positive; one diagnostic per
    field, no value echoed."""

    assert validate_settings(SETTINGS) == []

    assert validate_settings({}) == [
        "module 'chat_context': field 'limits': is required",
    ]
    assert validate_settings({"limits": {"attachments": LIMITS["attachments"]}}) == [
        "module 'chat_context': field 'limits.chat_context': is required",
    ]
    assert validate_settings({"limits": "nope"}) == [
        "module 'chat_context': field 'limits': must be a mapping of limit groups",
    ]
    assert validate_settings({"limits": {"chat_context": [5]}}) == [
        "module 'chat_context': field 'limits.chat_context': must be a mapping of limits",
    ]

    bad = {
        "limits": {
            "chat_context": {
                "max_messages": 0,
                "max_bytes": 65536,
                "max_age_seconds": float("inf"),
            }
        }
    }
    diagnostics = validate_settings(bad)
    assert diagnostics == [
        "module 'chat_context': field 'limits.chat_context.max_messages': "
        "must be a positive integer",
        "module 'chat_context': field 'limits.chat_context.max_age_seconds': "
        "must be a finite positive number",
    ]
    missing = {"limits": {"chat_context": {"max_bytes": 65536}}}
    assert validate_settings(missing) == [
        "module 'chat_context': field 'limits.chat_context.max_messages': is required",
        "module 'chat_context': field 'limits.chat_context.max_age_seconds': is required",
    ]
    assert validate_settings({"limits": {"chat_context": {"max_messages": True, "max_age_seconds": "9"}}}) == [
        "module 'chat_context': field 'limits.chat_context.max_messages': "
        "must be a positive integer",
        "module 'chat_context': field 'limits.chat_context.max_age_seconds': "
        "must be a finite positive number",
    ]
    assert validate_settings({**SETTINGS, "companion_name": "Ada"}) == [
        "module 'chat_context': field 'companion_name': is not a setting this module declares",
    ]
    assert validate_settings(["not", "a", "mapping"]) == [
        "module 'chat_context': field 'settings': must be a mapping",
    ]
    rendered = "\n".join(validate_settings(bad))
    assert "65536" not in rendered and "inf" not in rendered.replace("invalid", "")


async def test_activation_refuses_bad_settings_and_a_runtime_without_a_transcript() -> None:
    """R7: a handle built outside the loader is refused on the hook's terms,
    and a runtime assembled without a chat context is refused at activation."""

    context = module_context()
    with pytest.raises(ChatContextModuleError):
        await activate(context, {}, {})
    with pytest.raises(ChatContextModuleError):
        await activate(context, "settings", {})  # type: ignore[arg-type]

    without_chat = runtime_context().for_module(MODULE_NAME)
    with pytest.raises(ChatContextModuleError):
        await activate(without_chat, SETTINGS, {})
    # Nothing was declared or bound by a refused activation.
    assert CHAT_READ_ACTION not in runtime_of(without_chat).actions.discovered()


async def test_prepare_binds_the_declared_spec_over_any_platform_and_close_withdraws() -> None:
    """R5/R4: ``prepare`` registers the manifest's own contract over
    ``*/*/chat`` under the provider name and marks the module ready — once,
    idempotently; ``close`` withdraws readiness so a later call is refused
    ``provider_not_ready`` with the provider entered 0 times."""

    context = module_context()
    handle = await activate(context, SETTINGS, {})
    registry = runtime_of(context).actions
    assert CHAT_READ_ACTION not in registry.discovered()

    await handle.prepare()
    await handle.prepare()

    (binding,) = registry.bindings(CHAT_READ_ACTION)
    assert binding.provider_name == CHAT_READ_PROVIDER
    assert binding.module == MODULE_NAME
    assert binding.destination == Destination("*", "*", "chat")
    assert binding.spec == registry.discovered()[CHAT_READ_ACTION]
    assert binding.spec.nature == "read"
    assert binding.spec.delivery is None
    assert CHAT_READ_ACTION in registry.registered_ready()

    grant_chat_read(context)
    await handle.close()
    await handle.close()
    assert CHAT_READ_ACTION not in registry.registered_ready()
    observation = await read(context, FAKE_CHAT, 1)
    assert observation.status == "refused"
    assert observation.error is not None
    assert observation.error["code"] == "provider_not_ready"
    assert runtime_of(context).executor.provider_invocations == 0


async def test_loader_activates_the_manifest_and_the_coordinator_drives_prepare() -> None:
    """R4/R7: the real loader validates the manifest, runs the declared hook
    with the accepted ``limits`` block, declares the action at discovery and
    activates through the context; the coordinator's ``prepare`` binds the
    provider to the very spec the loader declared, and a granted read then
    serves a page; ``stop`` withdraws readiness. A profile without
    ``limits.chat_context`` is refused by the hook, naming the field."""

    clock = ManualClock()
    context = runtime_context(clock=clock, chat=chat_context(clock))
    loader = ModuleLoader(context.bus, ROOT / "modules", context=context, environ={})
    diagnostics: list[str] = []

    activations = await loader.activate_enabled(
        {"enabled_modules": [MODULE_NAME], "modules": {MODULE_NAME: SETTINGS}}
    )
    (activation,) = activations
    assert activation.manifest_version == 2
    assert activation.roles == frozenset()
    assert type(activation.handle).__name__ == ChatContextModule.__name__
    assert CHAT_READ_ACTION in context.actions.discovered()
    assert context.actions.bindings(CHAT_READ_ACTION) == ()

    coordinator = PhaseCoordinator(activations, tasks=context.tasks, reporter=diagnostics.append)
    assert (await coordinator.start()).status == 0
    (binding,) = context.actions.bindings(CHAT_READ_ACTION)
    assert binding.spec == context.actions.discovered()[CHAT_READ_ACTION]
    assert CHAT_READ_ACTION in context.actions.registered_ready()

    context.actions._authorization.grant(
        AuthorizationRule(
            rule_id="grant", action_name=CHAT_READ_ACTION, granted_permissions=("chat.read",)
        )
    )
    context.chat.append("fake", "channel-9", ChatEntry("ada", "a", "hi"))
    observation = await context.executor.invoke(chat_read_call(FAKE_CHAT, 5))
    assert observation.status == "success", observation.error
    assert observation.result is not None
    assert [m["message_id"] for m in observation.result["messages"]] == ["a"]
    assert observation.result["coverage"]["retained"] == 1

    assert (await coordinator.stop()).status == 0
    assert diagnostics == []
    assert CHAT_READ_ACTION not in context.actions.registered_ready()

    # The same profile without the accepted group: refused before activation.
    refusing = ModuleLoader(context.bus, ROOT / "modules", context=context, environ={})
    with pytest.raises(Exception) as refused:
        await refusing.activate_enabled(
            {
                "enabled_modules": [MODULE_NAME],
                "modules": {MODULE_NAME: {"limits": {"attachments": LIMITS["attachments"]}}},
            }
        )
    assert "'limits.chat_context'" in str(refused.value)
    assert "chat_context" in str(refused.value)


def test_module_package_is_importable_by_its_shipped_name() -> None:
    """P6 packaging: the directory is a package the distribution can ship."""

    assert (MODULE_DIR / "__init__.py").is_file()
    assert sys.modules["modules.chat_context"].__name__ == "modules.chat_context"
