"""Contract tests for the automation bridge's manifest and settings hook.

Phase 5, plan step P12: the 18th shipped manifest, its run action's declared
contract (R4, AC15), its settings schema without any ``required`` key (R1,
AC5), the value-free settings hook (R6, AC22; R5, AC45 first half; R7, AC33
first half), the package-data line and unchanged dependencies (R10, AC41),
and the vendor-free kernel (AC11, re-run). Nothing here opens a socket.

Plan step P13 adds the instance fixtures' provenance guard (R8, AC35) and
:class:`FakeInstance`, the loopback WebSocket server that replays them; only
its own self-tests open a socket, on ``127.0.0.1``.

Plan step P14 adds the connection lifecycle against :class:`FakeInstance`:
startup that never fails (R1; AC3 run-action half, AC4), the challenge answer
and readiness only after the inventory (R6, AC24), the degraded/ready pair
around a lost connection and the capped backoff on an injected clock (AC25),
cancellation and shutdown (AC28 connection half), and the password sweep over
these scenarios (AC23).

Plan step P15 adds the allowlist, its reconciliation and the run provider:
one test per row of the R4 outcome table (AC16), the late acknowledgement
(AC17), the allowlist and its argument schemas (R5; AC18, AC19, AC20, AC45),
authorization first (AC21), the transmission boundary of D8 — before the
frame left, a pre-write failure, an interruption after the write, the static
shape of the segment and the canary on the installed client — the in-flight
run at shutdown (AC28) and the password sweep over the run scenarios (AC23).

Plan step P16 adds the subscription and the inbound path: the ``Subscribe``
request names only the categories the owned pairs need (R6, AC27), the
bounded queue drops and counts what overflows without stalling the connection
(AC26), and a cue envelope becomes one normalised ``cue`` event, decided by
the trigger policy under ``triggers.<bridge>`` (R7; AC29, AC30) whose
anti-echo refuses the configured companion identities (AC33, engine half).

Plan step P17 adds the chat mapping for owned platforms (R7b): each
documented chat and notice fixture of twitch, kick and youtube maps to one
event whose ``kind`` and payload key set equal the native module's — the
native shape computed inline by the native normaliser on its own test
payload — and an unmapped type is dropped and counted (AC43); instance roles
satisfy an audience rule only under ``trust_instance_roles`` (AC32); and the
companion identity is required by ``--check-config`` and enforced by the
engine on a mapped chat event (AC33).
"""

from __future__ import annotations

import ast
import asyncio
import base64
import contextlib
import copy
import dataclasses
import inspect
import itertools
import textwrap
import hashlib
import json
import logging
import math
import re
import socket
import sys
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised on the 3.10 floor only
    import tomli as tomllib

import aiohttp
import pytest
import yaml
from aiohttp import WSMsgType, web

from core import main as application
from core.actions import (
    EMISSION_EMITTED,
    EMISSION_NOT_EMITTED,
    ERROR_CANCELLED,
    ERROR_EXTERNAL_UNKNOWN,
    ERROR_INVALID_ARGUMENTS,
    ERROR_NOT_AUTHORIZED,
    ERROR_PROVIDER_FAILED,
    ERROR_PROVIDER_NOT_READY,
    ERROR_TIMED_OUT,
    ActionExecutor,
    AuthorizationRule,
)
from core.contracts import (
    COUNTER_INPUT_OWNERSHIP_REJECTIONS,
    TRACE_ACTION_COMPLETED,
    TRACE_MODULE_DEGRADED,
    TRACE_MODULE_READY,
    ActionCall,
    ActionObservation,
    ContractError,
    Destination,
    validate_against_schema,
)
from core.context import ChatContext
from core.lifecycle import DEFAULT_HOOK_TIMEOUT_SECONDS, PhaseCoordinator
from core.loader import ModuleLoader, _validate_manifest
from core.runtime import RUNTIME_API, OwnedInputs
from core.triggers import REASON_SELF_AUTHORED, TriggerRegistry
from conftest import ManualClock, RecordingScheduler, runtime_context
import test_kick as native_kick
import test_twitch as native_twitch
import test_youtube as native_youtube

from modules import kick as kick_module
from modules import streamerbot
from modules import twitch as twitch_module
from modules import youtube as youtube_module
from modules.streamerbot import (
    ACKNOWLEDGEMENT_MARGIN_SECONDS,
    MANIFEST_PATH,
    MODULE_NAME,
    RUN_ACTION,
    validate_settings,
)

ROOT = Path(__file__).resolve().parents[1]

#: The sentence AC15 requires the run action's description to contain.
NOT_CONFIRMATION_SENTENCE = (
    "Acceptance by the automation application is not a confirmation that the action ran."
)
#: A usable configuration: loopback, so plain ``ws`` is accepted.
BASE_SETTINGS: dict[str, Any] = {"url": "ws://127.0.0.1:9/", "companion_name": "Companion"}
PRINCIPAL = "brain"
#: The bridge's module name and the instance vendor's name, any spelling.
VENDOR_PATTERN = re.compile(r"streamer[\s._-]?bot", re.IGNORECASE)


def _manifest() -> Mapping[str, Any]:
    loaded = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert isinstance(loaded, Mapping)
    return loaded


def _diagnostic(field_name: str, reason: str) -> str:
    return f"module 'streamerbot': field '{field_name}': {reason}"


def _setting_nodes(node: Mapping[str, Any], path: str = "settings_schema") -> Iterator[
    tuple[str, Mapping[str, Any]]
]:
    """Every setting node: the root, each property, each ``items`` schema."""

    yield path, node
    for name, child in (node.get("properties") or {}).items():
        yield from _setting_nodes(child, f"{path}.properties.{name}")
    items = node.get("items")
    if isinstance(items, Mapping):
        yield from _setting_nodes(items, f"{path}.items")


def _keys_at_any_depth(value: Any) -> Iterator[str]:
    if isinstance(value, Mapping):
        for key, item in value.items():
            yield key
            yield from _keys_at_any_depth(item)
    elif isinstance(value, list):
        for item in value:
            yield from _keys_at_any_depth(item)


def _with(**overrides: Any) -> dict[str, Any]:
    return {**BASE_SETTINGS, **overrides}


def _run_call(arguments: Mapping[str, Any]) -> ActionCall:
    return ActionCall(
        action_name=RUN_ACTION,
        action_version=1,
        arguments=arguments,
        conversation_id="conversation-1",
        run_id="run-1",
        call_id="call-1",
        source_event_id="source-1",
        destination=Destination("twitch", "channel-1", "automation"),
        principal=PRINCIPAL,
        deadline=10_000.0,
        message_id="source-1",
    )


# -- the manifest ------------------------------------------------------------ #


def test_the_manifest_is_a_v2_input_declaring_its_families_and_password() -> None:
    """R1, R6: manifest v2, the ``input`` role, the chat event produced, the
    password as the one credential, the declared hook, and the three
    families — chat and viewers on the three platforms not by default, cues
    on every platform by default."""

    manifest = _manifest()
    assert manifest["name"] == MODULE_NAME == "streamerbot"
    assert manifest["manifest_version"] == 2
    assert manifest["runtime_api"] == RUNTIME_API == 2
    assert manifest["lifecycle"] == {"roles": ["input"]}
    assert manifest["produces"] == ["channel.chat.message"]
    assert manifest["consumes"] == []
    assert manifest["credentials"] == ["password"]
    assert manifest["settings_validator"] == "validate_settings"
    assert manifest["input_families"] == [
        {"family": "chat", "platforms": ["twitch", "kick", "youtube"], "default": False},
        {"family": "viewers", "platforms": ["twitch", "kick", "youtube"], "default": False},
        {"family": "cues", "platforms": ["*"], "default": True},
    ]
    chat = manifest["input_families"][0]
    assert tuple(chat["platforms"]) == streamerbot.CHAT_PLATFORMS


def test_the_triggers_declare_event_kind_and_accept_any_cue_or_a_mention() -> None:
    """R7: the native chat inputs' trigger types plus ``event_kind`` (which
    admits ``cue``); the default policy is ``any_of`` a cue or the companion
    name. With ``companion_name`` configured the registry accepts it."""

    triggers = _manifest()["triggers"]
    assert [entry["name"] for entry in triggers["types"]] == [
        "probability",
        "audience",
        "keyword",
        "event_kind",
    ]
    kinds = triggers["types"][3]["parameter_schema"]["properties"]["kinds"]["items"]["enum"]
    assert "cue" in kinds
    assert triggers["default_policy"] == {
        "combination": "any_of",
        "rules": [
            {"type": "event_kind", "parameters": {"kinds": ["cue"]}},
            {"type": "keyword", "parameters": {"keywords": ["${companion_name}"]}},
        ],
    }
    registry = TriggerRegistry()
    declared = _validate_manifest(MODULE_NAME, dict(_manifest())).triggers
    registry.register(MODULE_NAME, declared, companion_name="Companion")
    assert registry.spec(MODULE_NAME).declares("event_kind")


def test_users_read_is_not_copied_into_the_manifest() -> None:
    """P12: ``users.read`` stays single-sourced in the ``users`` manifest."""

    assert [entry["name"] for entry in _manifest()["actions"]] == [RUN_ACTION]


# -- AC15: the run action's discovered contract ------------------------------ #


def test_ac15_the_discovered_run_action_spec_is_exactly_the_r4_contract() -> None:
    """AC15 (R4): the spec the loader discovers has ``nature: write``,
    ``idempotency: none``, ``required_permissions: [automation.run]``, one
    destination ``*/*/automation``, ``delivery: {text_argument: none}``, is
    not model-proposable, has a timeout leaving the acknowledgement deadline
    its margin, and its description says acceptance is not confirmation."""

    declaration = _validate_manifest(MODULE_NAME, dict(_manifest()))
    (spec,) = declaration.actions
    assert spec.name == RUN_ACTION == "automation.action.run"
    assert spec.version == 1
    assert spec.nature == "write"
    assert spec.idempotency == "none"
    assert spec.required_permissions == ("automation.run",)
    assert spec.supported_destinations == (Destination("*", "*", "automation"),)
    assert dict(spec.delivery) == {"text_argument": "none"}
    assert spec.model_proposable is False
    assert NOT_CONFIRMATION_SENTENCE in " ".join(spec.description.split())
    assert spec.timeout_seconds == streamerbot.run_action_timeout_seconds()
    assert spec == streamerbot.declared_run_spec()


def test_ac15_the_argument_schema_accepts_an_action_and_refuses_the_rest() -> None:
    """AC15 (R4): the schema itself accepts ``{action: "a"}`` (and 128
    characters, with ``arguments``) and refuses a missing ``action``, an
    extra key, and an ``action`` of 0 or 129 characters."""

    (spec,) = _validate_manifest(MODULE_NAME, dict(_manifest())).actions
    schema = spec.argument_schema
    validate_against_schema({"action": "a"}, schema, label="arguments")
    validate_against_schema(
        {"action": "a" * 128, "arguments": {"n": 1}}, schema, label="arguments"
    )
    for refused in (
        {},
        {"arguments": {}},
        {"action": "a", "extra": 1},
        {"action": ""},
        {"action": "a" * 129},
    ):
        with pytest.raises(ContractError):
            validate_against_schema(refused, schema, label="arguments")


# -- AC5, AC23: the settings schema ------------------------------------------ #


def test_ac5_the_settings_schema_has_no_required_key_at_any_depth() -> None:
    """AC5 (R1): no ``required`` anywhere in the settings schema — the
    switch that would let an unavailable bridge fail startup. The hook, not
    the schema, refuses a missing ``url``."""

    schema = _manifest()["settings_schema"]
    assert "required" not in set(_keys_at_any_depth(schema))
    validate_against_schema({}, schema, label="settings")
    assert validate_settings({}) != []


def test_ac23_every_setting_node_has_a_title_and_password_is_a_credential() -> None:
    """AC23 (R6), titles half: every node carries a non-empty ``title``;
    ``password`` is the declared credential. R6: the endpoint, the password
    and the companion name carry no default; the settings are exactly the
    ones the step lists."""

    manifest = _manifest()
    schema = manifest["settings_schema"]
    nodes = list(_setting_nodes(schema))
    assert len(nodes) > 10
    for path, node in nodes:
        title = node.get("title")
        assert isinstance(title, str) and title.strip(), path
    assert "password" in manifest["credentials"]
    properties = schema["properties"]
    assert set(properties) == {
        "url",
        "password",
        "companion_name",
        "deadlines",
        "reconnect",
        "queue_max_events",
        "allowlist",
        "companion_identities",
        "instance_identity",
        "trust_instance_roles",
        "limits",
    }
    for name in ("url", "password", "companion_name"):
        assert properties[name]["type"] == "string"
        assert "default" not in properties[name], name
    assert set(properties["deadlines"]["properties"]) == {
        "connect_seconds",
        "authentication_seconds",
        "request_seconds",
        "acknowledgement_seconds",
    }
    assert set(properties["reconnect"]["properties"]) == {
        "min_delay_seconds",
        "max_delay_seconds",
    }
    assert properties["trust_instance_roles"]["default"] is False
    assert set(properties["allowlist"]["items"]["properties"]) == {
        "action",
        "group",
        "arguments",
    }


def test_the_declared_defaults_pass_the_hook() -> None:
    """Every machine-readable default, applied, is accepted by the hook —
    the acknowledgement default leaves the run action's timeout its margin."""

    properties = _manifest()["settings_schema"]["properties"]
    settings = _with(
        deadlines={
            name: node["default"] for name, node in properties["deadlines"]["properties"].items()
        },
        reconnect={
            name: node["default"] for name, node in properties["reconnect"]["properties"].items()
        },
        queue_max_events=properties["queue_max_events"]["default"],
        trust_instance_roles=properties["trust_instance_roles"]["default"],
    )
    validate_against_schema(settings, _manifest()["settings_schema"], label="settings")
    assert validate_settings(settings) == []


# -- AC22: the URL and the deadlines ----------------------------------------- #


@pytest.mark.parametrize("url", ["ws://127.0.0.1:9/", "wss://192.0.2.1:9/", "ws://localhost:9/"])
def test_ac22_loopback_ws_and_any_wss_are_accepted(url: str) -> None:
    assert validate_settings(_with(url=url)) == []


def test_ac22_plain_ws_to_a_non_loopback_host_is_refused_without_the_url() -> None:
    """AC22 (R6): ``ws://192.0.2.1:9/`` is refused, naming ``url`` only."""

    diagnostics = validate_settings(_with(url="ws://192.0.2.1:9/"))
    assert diagnostics == [
        _diagnostic("url", "must use wss unless the host is a loopback address")
    ]
    assert "192.0.2.1" not in diagnostics[0]


@pytest.mark.parametrize("url", ["http://127.0.0.1:9/", "127.0.0.1:9", "wss://"])
def test_a_url_that_is_not_a_websocket_address_is_refused(url: str) -> None:
    (diagnostic,) = validate_settings(_with(url=url))
    assert diagnostic.startswith(_diagnostic("url", ""))
    assert url not in diagnostic


def test_ac22_a_missing_url_is_refused_with_one_diagnostic() -> None:
    settings = dict(BASE_SETTINGS)
    del settings["url"]
    assert validate_settings(settings) == [_diagnostic("url", "is required")]


@pytest.mark.parametrize("value", [0, -1, math.nan, math.inf, True, "5"])
@pytest.mark.parametrize(
    "field_name",
    ["connect_seconds", "authentication_seconds", "request_seconds", "acknowledgement_seconds"],
)
def test_ac22_a_non_positive_or_non_finite_deadline_is_refused(
    field_name: str, value: Any
) -> None:
    """AC22 (R6): one value-free diagnostic naming the deadline's field."""

    diagnostics = validate_settings(_with(deadlines={field_name: value}))
    assert diagnostics == [
        _diagnostic(f"deadlines.{field_name}", "must be a finite positive number")
    ]


def test_r4_the_acknowledgement_deadline_leaves_the_run_timeout_its_margin() -> None:
    """R4: the run action's fixed timeout is never smaller than the
    acknowledgement deadline; the hook keeps a margin between them."""

    bound = streamerbot.run_action_timeout_seconds() - ACKNOWLEDGEMENT_MARGIN_SECONDS
    assert bound > 0
    assert validate_settings(_with(deadlines={"acknowledgement_seconds": bound})) == []
    (diagnostic,) = validate_settings(
        _with(deadlines={"acknowledgement_seconds": bound + 0.5})
    )
    assert diagnostic.startswith(_diagnostic("deadlines.acknowledgement_seconds", "must leave"))


def test_reconnect_delays_and_the_queue_bound_are_checked() -> None:
    assert validate_settings(
        _with(reconnect={"min_delay_seconds": 2, "max_delay_seconds": 1})
    ) == [_diagnostic("reconnect.min_delay_seconds", "must not exceed max_delay_seconds")]
    assert validate_settings(_with(reconnect={"max_delay_seconds": math.nan})) == [
        _diagnostic("reconnect.max_delay_seconds", "must be a finite positive number")
    ]
    # The bounds are compared after the defaults apply (min 1, max 60): a
    # configured maximum is never raised silently to the default minimum.
    assert validate_settings(_with(reconnect={"max_delay_seconds": 0.5})) == [
        _diagnostic(
            "reconnect.max_delay_seconds", "must not be below min_delay_seconds (default 1)"
        )
    ]
    assert validate_settings(_with(reconnect={"min_delay_seconds": 120})) == [
        _diagnostic("reconnect.min_delay_seconds", "must not exceed max_delay_seconds")
    ]
    assert validate_settings(
        _with(reconnect={"min_delay_seconds": 0.25, "max_delay_seconds": 0.5})
    ) == []
    for value in (0, -3, 1.5, True):
        assert validate_settings(_with(queue_max_events=value)) == [
            _diagnostic("queue_max_events", "must be a positive integer")
        ]
    assert validate_settings(_with(queue_max_events=1)) == []


def test_a_missing_or_blank_companion_name_is_refused() -> None:
    """P12 amendment: the default policy names ``${companion_name}``, so
    the hook refuses a missing one rather than letting trigger registration
    fail startup."""

    settings = dict(BASE_SETTINGS)
    del settings["companion_name"]
    assert validate_settings(settings) == [_diagnostic("companion_name", "is required")]
    assert validate_settings(_with(companion_name="  ")) == [
        _diagnostic("companion_name", "must be a non-empty string")
    ]


def test_an_undeclared_setting_is_refused_and_limits_is_accepted() -> None:
    assert validate_settings(_with(limits={"x": {}})) == []
    assert validate_settings(_with(endpoint="x")) == [
        _diagnostic("endpoint", "is not a setting this module declares")
    ]


def test_a_secret_never_reaches_a_diagnostic() -> None:
    password = "hunter2-very-secret"
    diagnostics = validate_settings(
        {"password": password, "url": "ws://192.0.2.1:9/", "deadlines": {"connect_seconds": 0}}
    )
    assert diagnostics
    assert all(password not in entry for entry in diagnostics)


# -- AC45 (validator half): the allowlist ------------------------------------ #


def test_ac45_duplicate_action_or_group_entries_are_refused() -> None:
    """AC45 (R5): two ``{action: "A"}`` or two ``{group: "G"}`` entries are
    refused with a per-field diagnostic that does not echo the name."""

    actions = validate_settings(_with(allowlist=[{"action": "A"}, {"action": "A"}]))
    assert actions == [
        _diagnostic("allowlist[1].action", "duplicates an earlier action entry")
    ]
    groups = validate_settings(_with(allowlist=[{"group": "G"}, {"group": "G"}]))
    assert groups == [_diagnostic("allowlist[1].group", "duplicates an earlier group entry")]
    # The same name as an action and as a group is two different entries.
    assert validate_settings(_with(allowlist=[{"action": "A"}, {"group": "A"}])) == []


@pytest.mark.parametrize(
    "entry", [{}, {"action": "A", "group": "G"}, {"arguments": {"type": "object"}}]
)
def test_an_entry_naming_both_or_neither_is_refused(entry: dict[str, Any]) -> None:
    assert validate_settings(_with(allowlist=[entry])) == [
        _diagnostic("allowlist[0]", "must name exactly one of action or group")
    ]


def test_an_entry_arguments_schema_outside_the_subset_is_refused() -> None:
    accepted = {
        "type": "object",
        "properties": {"n": {"type": "integer"}},
        "additionalProperties": False,
    }
    assert validate_settings(_with(allowlist=[{"action": "A", "arguments": accepted}])) == []
    refused = {"type": "object", "pattern": "x"}
    assert validate_settings(_with(allowlist=[{"action": "A", "arguments": refused}])) == [
        _diagnostic("allowlist[0].arguments", "is not a supported schema")
    ]


def test_the_allowlist_must_be_a_list() -> None:
    assert validate_settings(_with(allowlist={"action": "A"})) == [
        _diagnostic("allowlist", "must be a list of entries")
    ]


# -- AC33 (validator half): owned chat needs an identity --------------------- #


def test_ac33_owning_twitch_chat_without_its_companion_identity_is_refused() -> None:
    """AC33 (R7): owning (``chat``, twitch) without
    ``companion_identities.twitch`` gives one diagnostic naming ``twitch``;
    with it, or without the pair, nothing."""

    owned = OwnedInputs(pairs=frozenset({("chat", "twitch"), ("cues", "*")}))
    diagnostics = validate_settings(BASE_SETTINGS, owned_inputs=owned)
    assert len(diagnostics) == 1
    assert "twitch" in diagnostics[0]
    assert diagnostics[0].startswith(_diagnostic("companion_identities.twitch", ""))
    assert validate_settings(
        _with(companion_identities={"twitch": "bot-1"}), owned_inputs=owned
    ) == []
    cues_only = OwnedInputs(pairs=frozenset({("cues", "*")}))
    assert validate_settings(BASE_SETTINGS, owned_inputs=cues_only) == []
    # Outside the loader, the ownership-dependent check is skipped.
    assert validate_settings(BASE_SETTINGS) == []


def test_identity_maps_must_map_platforms_to_non_empty_text() -> None:
    assert validate_settings(_with(instance_identity={"twitch": ""})) == [
        _diagnostic("instance_identity.twitch", "must be a non-empty string")
    ]
    assert validate_settings(_with(companion_identities=["bot"])) == [
        _diagnostic("companion_identities", "must be a mapping of platform to identifier")
    ]


def test_a_companion_identity_holding_the_reserved_separator_is_refused() -> None:
    """R7a: ``:`` is reserved for the ``instance:<platform>`` cue author, so
    no companion identity can collide with it and turn every user-less cue
    into a self-authored one."""

    assert validate_settings(_with(companion_identities={"twitch": "instance:twitch"})) == [
        _diagnostic("companion_identities.twitch", "must not contain ':'")
    ]


# -- the loader, the placeholder provider ------------------------------------ #


async def test_the_loader_activates_the_bridge_and_a_run_is_refused_not_ready() -> None:
    """R1, R4: the shipped manifest loads, its triggers register with the
    configured companion name, ``prepare`` binds the run action to the very
    spec discovered, the module is never ready, so a granted run is refused
    ``provider_not_ready``; the placeholder provider answers the same."""

    context = runtime_context(clock=ManualClock(), trigger_registry=TriggerRegistry())
    loader = ModuleLoader(context.bus, ROOT / "modules", context=context, environ={})
    activations = await loader.activate_enabled(
        {"enabled_modules": [MODULE_NAME], "modules": {MODULE_NAME: dict(BASE_SETTINGS)}}
    )
    (activation,) = activations
    assert activation.manifest_version == 2
    assert RUN_ACTION in context.actions.discovered()

    diagnostics: list[str] = []
    coordinator = PhaseCoordinator(activations, tasks=context.tasks, reporter=diagnostics.append)
    await coordinator.start()
    try:
        (binding,) = context.actions.bindings(RUN_ACTION)
        assert binding.spec == context.actions.discovered()[RUN_ACTION]
        assert RUN_ACTION not in context.actions.registered_ready()

        context.actions._authorization.grant(
            AuthorizationRule(
                rule_id="grant", action_name=RUN_ACTION, granted_permissions=("automation.run",)
            )
        )
        observation = await context.executor.invoke(_run_call({"action": "A"}))
        assert observation.status == "refused"
        assert observation.error["code"] == ERROR_PROVIDER_NOT_READY
    finally:
        await coordinator.stop()


async def test_the_provider_of_a_disconnected_bridge_answers_provider_not_ready() -> None:
    """R4 (supersedes P12's placeholder provider, which took no bridge): a
    call reaching the provider while its bridge holds no connection is
    declared not emitted first and refused ``provider_not_ready``."""

    class _Invocation:
        call = _run_call({"action": "A"})
        emitted: bool | None = None

        def mark_not_emitted(self) -> None:
            self.emitted = False

    class _Disconnected:
        _connection = None
        connected = False

    invocation = _Invocation()
    observation = await streamerbot._RunProvider(_Disconnected()).invoke(invocation)
    assert invocation.emitted is False
    assert observation.status == "refused"
    assert observation.error["code"] == ERROR_PROVIDER_NOT_READY
    assert observation.error["retryable"] is False


async def test_a_bridge_without_companion_name_is_refused_by_the_hook_not_the_triggers(
) -> None:
    """P12 amendment: enabling the bridge without ``companion_name`` is a
    settings diagnostic naming the field — never a trigger-registration
    failure."""

    context = runtime_context(clock=ManualClock(), trigger_registry=TriggerRegistry())
    loader = ModuleLoader(context.bus, ROOT / "modules", context=context, environ={})
    with pytest.raises(Exception) as caught:
        await loader.activate_enabled(
            {
                "enabled_modules": [MODULE_NAME],
                "modules": {MODULE_NAME: {"url": "ws://127.0.0.1:9/"}},
            }
        )
    message = str(caught.value)
    assert "companion_name" in message
    assert "triggers" not in message


# -- AC41, AC11: packaging and the vendor-free kernel ------------------------ #


def test_ac41_dependencies_are_unchanged_and_the_manifest_ships() -> None:
    """AC41 (R10): exactly ``aiohttp`` and ``PyYAML`` at runtime, and the
    bridge's package-data line is present."""

    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert sorted(
        re.match(r"[A-Za-z0-9_.-]+", spec).group(0).lower()
        for spec in project["project"]["dependencies"]
    ) == ["aiohttp", "pyyaml"]
    package_data = project["tool"]["setuptools"]["package-data"]
    assert package_data["modules.streamerbot"] == ["module.yaml"]
    assert (ROOT / "modules" / "streamerbot" / "module.yaml").is_file()


def test_ac11_the_kernel_never_names_the_bridge_or_its_vendor() -> None:
    """AC11 (R2, R10), re-run: 0 matches in ``core/`` for the module name or
    the vendor name, in any case."""

    matches = [
        str(path.relative_to(ROOT))
        for path in sorted((ROOT / "core").rglob("*"))
        if path.is_file()
        and path.suffix != ".pyc"
        and VENDOR_PATTERN.search(path.read_text(encoding="utf-8", errors="ignore"))
    ]
    assert matches == []


# -- P13: the instance fixtures and their provenance (R8, AC35) -------------- #

FIXTURE_DIR = ROOT / "tests" / "fixtures" / "automation_bridge"
LEDGER_PATH = ROOT / "docs" / "research" / "sources-ledger-streamerbot.json"
TRIAL_RECORD = "docs/campaigns/phase5/bridge-trial-record.md"
#: Every fixture file, by stem. A file outside this list is a failure too.
FIXTURE_FILES = (
    "hello",
    "authentication",
    "get_actions",
    "do_action",
    "subscribe",
    "custom_event",
    "active_viewers",
    "chat_twitch",
    "chat_kick",
    "chat_youtube",
    "notices_twitch",
    "notices_kick",
    "notices_youtube",
    "error_response",
)
#: The section 9 questions of the integration study.
TRIAL_QUESTIONS = range(1, 9)
#: The event source each per-platform fixture's frames carry.
PLATFORM_SOURCES = {"twitch": "Twitch", "kick": "Kick", "youtube": "YouTube"}


def _ledger_ids() -> frozenset[int]:
    ledger = json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
    return frozenset(source["id"] for source in ledger["sources"])


def fixture_entries(name: str) -> list[dict[str, Any]]:
    """The entries of fixture *name*: provenance, optional question, frame."""

    loaded = json.loads((FIXTURE_DIR / f"{name}.json").read_text(encoding="utf-8"))
    assert isinstance(loaded, dict) and set(loaded) == {"frames"}, name
    assert isinstance(loaded["frames"], list) and loaded["frames"], name
    return loaded["frames"]


def fixture_frame(name: str, index: int = 0) -> dict[str, Any]:
    """A private copy of frame *index* of fixture *name*."""

    return copy.deepcopy(fixture_entries(name)[index]["frame"])


def _provenance_violations(
    name: str, entries: list[Any], ledger_ids: frozenset[int]
) -> list[str]:
    """Every AC35 violation of one fixture file, as ``file#index: reason``."""

    violations: list[str] = []
    for index, entry in enumerate(entries):
        where = f"{name}#{index}"
        if not isinstance(entry, Mapping) or not isinstance(entry.get("frame"), Mapping):
            violations.append(f"{where}: not a frame entry")
            continue
        if set(entry) - {"provenance", "question", "frame"}:
            violations.append(f"{where}: unknown keys")
        provenance = entry.get("provenance")
        kind = provenance.get("kind") if isinstance(provenance, Mapping) else None
        if kind == "documented":
            ledger_id = provenance.get("ledger_id")
            if (
                set(provenance) != {"kind", "ledger_id"}
                or isinstance(ledger_id, bool)
                or ledger_id not in ledger_ids
            ):
                violations.append(f"{where}: documented id absent from the ledger")
        elif kind == "observed":
            reference = provenance.get("trial_record")
            if set(provenance) != {"kind", "trial_record"} or not (
                isinstance(reference, str)
                and re.fullmatch(re.escape(TRIAL_RECORD) + r"#q[1-8]", reference)
            ):
                violations.append(f"{where}: observed without a trial-record reference")
        else:
            violations.append(f"{where}: no provenance")
        if "question" in entry:
            question = entry["question"]
            if isinstance(question, bool) or question not in TRIAL_QUESTIONS:
                violations.append(f"{where}: question outside 1-8")
            elif kind != "observed":
                violations.append(f"{where}: names question {question} but is not observed")
            elif provenance["trial_record"] != f"{TRIAL_RECORD}#q{question}":
                violations.append(f"{where}: observed for another question")
    return violations


def test_ac35_the_fixture_directory_holds_exactly_the_listed_files() -> None:
    """R8: the fixture set is closed — a replayed frame cannot come from a
    file the guard does not load."""

    stored = sorted(path.stem for path in FIXTURE_DIR.glob("*.json"))
    assert stored == sorted(FIXTURE_FILES)
    assert (FIXTURE_DIR / "README.md").is_file()


def test_ac35_every_frame_carries_a_provenance_and_no_question_is_answered() -> None:
    """AC35 (R8): no frame lacks a provenance, every ``documented`` one
    names a ledger id, a frame naming a question 1–8 is ``observed``; with
    no trial record, the count of frames naming a question is 0."""

    ledger_ids = _ledger_ids()
    violations: list[str] = []
    naming_a_question = 0
    for name in FIXTURE_FILES:
        entries = fixture_entries(name)
        violations += _provenance_violations(name, entries, ledger_ids)
        naming_a_question += sum(1 for entry in entries if "question" in entry)
    assert violations == []
    if not (ROOT / TRIAL_RECORD).exists():
        assert naming_a_question == 0


def test_ac35_the_guard_rejects_each_violation() -> None:
    """The guard itself: each kind of violation is reported, a valid
    observed frame for its own question passes."""

    ledger_ids = frozenset({2, 4, 6})
    frame = {"status": "ok", "id": "request-1"}
    observed = {"kind": "observed", "trial_record": f"{TRIAL_RECORD}#q1"}
    cases = {
        "no provenance": {"frame": frame},
        "documented id absent from the ledger": {
            "provenance": {"kind": "documented", "ledger_id": 99},
            "frame": frame,
        },
        "names question 1 but is not observed": {
            "provenance": {"kind": "documented", "ledger_id": 2},
            "question": 1,
            "frame": frame,
        },
        "observed without a trial-record reference": {
            "provenance": {"kind": "observed", "trial_record": "elsewhere.md#q1"},
            "frame": frame,
        },
        "question outside 1-8": {"provenance": observed, "question": 9, "frame": frame},
        "observed for another question": {"provenance": observed, "question": 6, "frame": frame},
    }
    for reason, entry in cases.items():
        assert _provenance_violations("case", [entry], ledger_ids) == [f"case#0: {reason}"]
    valid = {"provenance": observed, "question": 1, "frame": frame}
    assert _provenance_violations("case", [valid], ledger_ids) == []


def test_r8_the_fixtures_hold_the_planned_frames() -> None:
    """R8 contents: both hellos, the inventory, the run acknowledgement and
    error, the subscription, the custom event, the active viewers, one chat
    message per platform, and per-platform notices of that platform only."""

    hellos = [fixture_frame("hello", index) for index in range(len(fixture_entries("hello")))]
    assert [hello["request"] for hello in hellos] == ["Hello", "Hello"]
    assert ["authentication" in hello for hello in hellos] == [False, True]
    assert set(hellos[1]["authentication"]) == {"salt", "challenge"}
    assert fixture_frame("authentication")["status"] == "ok"
    assert fixture_frame("error_response")["status"] == "error"
    inventory = fixture_frame("get_actions")
    assert inventory["count"] == len(inventory["actions"]) > 0
    assert [entry["frame"]["status"] for entry in fixture_entries("do_action")] == ["ok", "error"]
    assert fixture_frame("subscribe")["status"] == "ok"
    assert fixture_frame("custom_event")["event"] == {"source": "Custom", "type": "Event"}
    assert fixture_frame("active_viewers")["viewers"]
    readme = (FIXTURE_DIR / "README.md").read_text(encoding="utf-8")
    for platform, source in PLATFORM_SOURCES.items():
        chat = fixture_entries(f"chat_{platform}")
        assert len(chat) == 1 and chat[0]["frame"]["event"]["source"] == source
        notices = [entry["frame"]["event"] for entry in fixture_entries(f"notices_{platform}")]
        assert {event["source"] for event in notices} == {source}
        types = [event["type"] for event in notices]
        assert len(types) == len(set(types))
        for event_type in types:
            # The README enumerates every documented notice kind (the AC43 list).
            assert f"| {platform} | `{event_type}` |" in readme


# -- P13: the fake instance -------------------------------------------------- #

LOOPBACK = "127.0.0.1"
#: The fault ``malformed_hello`` sends: not an instance frame, not JSON.
MALFORMED_HELLO = '{"request": "Hello"'
#: A bound on each self-test's wait; a deadline, never a sleep.
FAKE_WAIT_SECONDS = 5.0


class FakeInstance:
    """A loopback WebSocket server replaying the fixture frames (R8).

    It listens on ``127.0.0.1`` at an ephemeral port, sends the hello on each
    connection, records every received frame in arrival order and by request
    id, and answers each documented request with its fixture frame, the
    request's id substituted. The fault switches:

    - ``require_authentication`` sends the hello carrying ``authentication``;
    - ``refuse_authentication`` answers ``Authenticate`` with the error frame;
    - ``malformed_hello`` sends :data:`MALFORMED_HELLO` instead of a hello;
    - ``fail_inventory`` answers ``GetActions`` with the error frame;
    - ``inventory`` picks the ``get_actions`` frame (default: the first);
    - ``hold_inventory`` delays every ``GetActions`` answer until
      :meth:`release_inventory`;
    - ``reject_runs`` answers ``DoAction`` with the error frame;
    - ``hold_acknowledgements`` delays every ``DoAction`` answer until
      :meth:`release_acknowledgements`;
    - ``withhold_acknowledgements`` never answers ``DoAction``.

    :meth:`close_connection` closes the current socket, :meth:`push_events`
    sends one fixture event N times, :meth:`stop` shuts the server down. A
    request with no documented answer gets none.
    """

    def __init__(
        self,
        *,
        require_authentication: bool = False,
        refuse_authentication: bool = False,
        malformed_hello: bool = False,
        fail_inventory: bool = False,
        inventory: int = 0,
        hold_inventory: bool = False,
        reject_runs: bool = False,
        hold_acknowledgements: bool = False,
        withhold_acknowledgements: bool = False,
    ) -> None:
        self.require_authentication = require_authentication
        self.refuse_authentication = refuse_authentication
        self.malformed_hello = malformed_hello
        self.fail_inventory = fail_inventory
        self.inventory = inventory
        self.hold_inventory = hold_inventory
        self.reject_runs = reject_runs
        self.hold_acknowledgements = hold_acknowledgements
        self.withhold_acknowledgements = withhold_acknowledgements
        #: Every received frame, in arrival order (a non-JSON text as ``str``).
        self.received: list[Any] = []
        #: Every received frame carrying an ``id``, by that id.
        self.by_request_id: dict[str, list[dict[str, Any]]] = {}
        self.connections = 0
        self.disconnections = 0
        self.url = ""
        self._runner: web.AppRunner | None = None
        self._socket: web.WebSocketResponse | None = None
        self._released = asyncio.Event()
        self._inventory_released = asyncio.Event()
        self._changed = asyncio.Event()
        self._pending: set[asyncio.Task[None]] = set()

    async def __aenter__(self) -> FakeInstance:
        await self.start()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.stop()

    async def start(self) -> None:
        app = web.Application()
        app.router.add_get("/", self._handle)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, LOOPBACK, 0)
        await site.start()
        server = getattr(site, "_server", None)
        assert server is not None and server.sockets
        port = server.sockets[0].getsockname()[1]
        self.url = f"ws://{LOOPBACK}:{port}/"

    async def stop(self) -> None:
        for task in self._pending:
            task.cancel()
        await asyncio.gather(*self._pending, return_exceptions=True)
        await self.close_connection()
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

    def requests(self, name: str) -> list[dict[str, Any]]:
        """The received frames naming request *name*."""

        return [
            frame
            for frame in self.received
            if isinstance(frame, dict) and frame.get("request") == name
        ]

    async def wait_for_frames(self, count: int) -> None:
        """Return once *count* frames were received (the caller bounds it)."""

        while len(self.received) < count:
            self._changed.clear()
            await self._changed.wait()

    async def wait_for_disconnections(self, count: int) -> None:
        """Return once *count* connections ended (the caller bounds it)."""

        while self.disconnections < count:
            self._changed.clear()
            await self._changed.wait()

    def release_acknowledgements(self) -> None:
        self._released.set()

    def release_inventory(self) -> None:
        self._inventory_released.set()

    async def close_connection(self) -> None:
        socket = self._socket
        if socket is not None and not socket.closed:
            await socket.close()

    async def push_frames(self, frames: list[dict[str, Any]]) -> None:
        """Send each of *frames*, in order (built from fixture frames)."""

        socket = self._socket
        assert socket is not None and not socket.closed
        for frame in frames:
            await socket.send_json(frame)

    async def push_events(self, name: str, count: int, index: int = 0) -> None:
        """Send frame *index* of fixture *name* *count* times."""

        socket = self._socket
        assert socket is not None and not socket.closed
        frame = fixture_frame(name, index)
        for _ in range(count):
            await socket.send_json(frame)

    async def _handle(self, request: web.Request) -> web.WebSocketResponse:
        socket = web.WebSocketResponse()
        await socket.prepare(request)
        self.connections += 1
        self._socket = socket
        try:
            if self.malformed_hello:
                await socket.send_str(MALFORMED_HELLO)
            else:
                await socket.send_json(fixture_frame("hello", 1 if self.require_authentication else 0))
            async for message in socket:
                if message.type is not WSMsgType.TEXT:
                    continue
                frame = self._record(message.data)
                if isinstance(frame, dict):
                    await self._answer(socket, frame)
        finally:
            if self._socket is socket:
                self._socket = None
            self.disconnections += 1
            self._changed.set()
        return socket

    def _record(self, text: str) -> Any:
        try:
            frame: Any = json.loads(text)
        except ValueError:
            frame = text
        self.received.append(frame)
        if isinstance(frame, dict) and isinstance(frame.get("id"), str):
            self.by_request_id.setdefault(frame["id"], []).append(frame)
        self._changed.set()
        return frame

    async def _answer(self, socket: web.WebSocketResponse, frame: dict[str, Any]) -> None:
        name = frame.get("request")
        if name == "Authenticate":
            reply = fixture_frame("error_response" if self.refuse_authentication else "authentication")
        elif name == "GetActions":
            reply = (
                fixture_frame("error_response")
                if self.fail_inventory
                else fixture_frame("get_actions", self.inventory)
            )
            if self.hold_inventory:
                reply["id"] = frame.get("id")
                self._answer_later(socket, reply, self._inventory_released)
                return
        elif name == "Subscribe":
            reply = fixture_frame("subscribe")
            reply["events"] = copy.deepcopy(frame.get("events"))
        elif name == "GetActiveViewers":
            reply = fixture_frame("active_viewers")
        elif name == "DoAction":
            if self.withhold_acknowledgements:
                return
            reply = fixture_frame("do_action", 1 if self.reject_runs else 0)
            if self.hold_acknowledgements:
                reply["id"] = frame.get("id")
                self._answer_later(socket, reply, self._released)
                return
        else:
            return
        reply["id"] = frame.get("id")
        await socket.send_json(reply)

    def _answer_later(
        self, socket: web.WebSocketResponse, reply: dict[str, Any], gate: asyncio.Event
    ) -> None:
        task = asyncio.ensure_future(self._answer_when_released(socket, reply, gate))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def _answer_when_released(
        self, socket: web.WebSocketResponse, reply: dict[str, Any], gate: asyncio.Event
    ) -> None:
        await gate.wait()
        if not socket.closed:
            await socket.send_json(reply)


async def _receive(client: aiohttp.ClientWebSocketResponse) -> aiohttp.WSMessage:
    return await asyncio.wait_for(client.receive(), FAKE_WAIT_SECONDS)


async def _receive_json(client: aiohttp.ClientWebSocketResponse) -> Any:
    message = await _receive(client)
    assert message.type is WSMsgType.TEXT
    return json.loads(message.data)


async def test_fake_instance_answers_one_hello_and_records_one_frame() -> None:
    """R8 self-test: one hello replayed from its fixture, one request
    recorded by id and answered with the fixture frame under that id."""

    async with FakeInstance() as fake, aiohttp.ClientSession() as session:
        assert fake.url.startswith(f"ws://{LOOPBACK}:")
        async with session.ws_connect(fake.url) as client:
            assert await _receive_json(client) == fixture_frame("hello")
            await client.send_json({"request": "GetActions", "id": "r-1"})
            answer = await _receive_json(client)
        assert answer == {**fixture_frame("get_actions"), "id": "r-1"}
        assert fake.received == [{"request": "GetActions", "id": "r-1"}]
        assert fake.by_request_id == {"r-1": [{"request": "GetActions", "id": "r-1"}]}
        assert fake.connections == 1


async def test_fake_instance_authentication_switches() -> None:
    """The hello carries the challenge when authentication is required; a
    refusal answers the error frame."""

    async with FakeInstance(require_authentication=True) as fake, aiohttp.ClientSession() as session:
        async with session.ws_connect(fake.url) as client:
            assert await _receive_json(client) == fixture_frame("hello", 1)
            await client.send_json({"request": "Authenticate", "id": "a-1", "authentication": "x"})
            assert await _receive_json(client) == {**fixture_frame("authentication"), "id": "a-1"}
    async with FakeInstance(
        require_authentication=True, refuse_authentication=True
    ) as fake, aiohttp.ClientSession() as session:
        async with session.ws_connect(fake.url) as client:
            await _receive_json(client)
            await client.send_json({"request": "Authenticate", "id": "a-2", "authentication": "x"})
            assert await _receive_json(client) == {"status": "error", "id": "a-2"}


async def test_fake_instance_malformed_hello_and_failed_inventory() -> None:
    async with FakeInstance(malformed_hello=True, fail_inventory=True) as fake, (
        aiohttp.ClientSession()
    ) as session:
        async with session.ws_connect(fake.url) as client:
            message = await _receive(client)
            assert message.data == MALFORMED_HELLO
            await client.send_json({"request": "GetActions", "id": "g-1"})
            assert await _receive_json(client) == {"status": "error", "id": "g-1"}


async def test_fake_instance_holds_withholds_and_rejects_acknowledgements() -> None:
    """A held acknowledgement leaves only once released, a withheld one
    never, a rejected run answers the error frame; every run is recorded."""

    async with FakeInstance(hold_acknowledgements=True) as fake, aiohttp.ClientSession() as session:
        async with session.ws_connect(fake.url) as client:
            await _receive_json(client)
            await client.send_json({"request": "DoAction", "id": "d-1", "action": {"id": "x"}})
            await client.send_json({"request": "GetActions", "id": "g-1"})
            # The inventory answer overtakes the held acknowledgement.
            assert (await _receive_json(client))["id"] == "g-1"
            fake.release_acknowledgements()
            assert await _receive_json(client) == {"status": "ok", "id": "d-1"}
    async with FakeInstance(withhold_acknowledgements=True) as fake, (
        aiohttp.ClientSession()
    ) as session:
        async with session.ws_connect(fake.url) as client:
            await _receive_json(client)
            await client.send_json({"request": "DoAction", "id": "d-2", "action": {"id": "x"}})
            await client.send_json({"request": "GetActions", "id": "g-2"})
            assert (await _receive_json(client))["id"] == "g-2"
        assert [frame["id"] for frame in fake.requests("DoAction")] == ["d-2"]
    async with FakeInstance(reject_runs=True) as fake, aiohttp.ClientSession() as session:
        async with session.ws_connect(fake.url) as client:
            await _receive_json(client)
            await client.send_json({"request": "DoAction", "id": "d-3", "action": {"id": "x"}})
            assert await _receive_json(client) == {"status": "error", "id": "d-3"}


async def test_fake_instance_pushes_events_echoes_subscriptions_and_closes() -> None:
    async with FakeInstance() as fake, aiohttp.ClientSession() as session:
        async with session.ws_connect(fake.url) as client:
            await _receive_json(client)
            events = {"Custom": ["Event"]}
            await client.send_json({"request": "Subscribe", "id": "s-1", "events": events})
            assert await _receive_json(client) == {"status": "ok", "id": "s-1", "events": events}
            await fake.push_events("custom_event", 3)
            for _ in range(3):
                assert await _receive_json(client) == fixture_frame("custom_event")
            await fake.close_connection()
            assert (await _receive(client)).type in (WSMsgType.CLOSE, WSMsgType.CLOSED)
        await asyncio.wait_for(fake.wait_for_disconnections(1), FAKE_WAIT_SECONDS)
        assert fake.connections == fake.disconnections == 1


async def test_fake_instance_stop_ends_the_connection() -> None:
    fake = FakeInstance()
    await fake.start()
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(fake.url) as client:
            await _receive_json(client)
            await client.send_json({"request": "GetActiveViewers", "id": "v-1"})
            await asyncio.wait_for(fake.wait_for_frames(1), FAKE_WAIT_SECONDS)
            assert (await _receive_json(client))["viewers"]
            await fake.stop()
            assert (await _receive(client)).type in (WSMsgType.CLOSE, WSMsgType.CLOSED)
    assert fake.by_request_id["v-1"] == [{"request": "GetActiveViewers", "id": "v-1"}]


# -- P14: the connection lifecycle (R1, R6) --------------------------------- #

#: The configured password of the connection scenarios; every sweep looks for
#: it in every trace, diagnostic and log record (AC4, AC23).
PASSWORD = "p14-configured-password"
#: The bound every hook and every wait of these scenarios runs under.
CLOSE_BUDGET_SECONDS = FAKE_WAIT_SECONDS
RECONNECT = {"min_delay_seconds": 1, "max_delay_seconds": 4}


def _expected_authentication(password: str, salt: str, challenge: str) -> str:
    """AC24's formula, written out independently of the module."""

    inner = base64.b64encode(hashlib.sha256((password + salt).encode()).digest()).decode()
    return base64.b64encode(hashlib.sha256((inner + challenge).encode()).digest()).decode()


def _silent_loopback_port() -> int:
    """A loopback port nothing listens on: bound once, then released."""

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((LOOPBACK, 0))
        return int(probe.getsockname()[1])


async def _until(predicate: Any) -> None:
    """Yield to the loop until *predicate* holds, bounded; never a sleep."""

    async def poll() -> None:
        while not predicate():
            await asyncio.sleep(0)

    await asyncio.wait_for(poll(), FAKE_WAIT_SECONDS)


class _Bridge:
    """The bridge activated through the loader on a manual-clock runtime.

    Every trace is recorded; the reconnection sleeper records each delay it
    is asked for and parks on the manual clock until a test advances it.
    """

    def __init__(self, *, scheduler: Any = None) -> None:
        self.clock = ManualClock()
        self.registry = TriggerRegistry()
        self.context = runtime_context(
            clock=self.clock,
            trigger_registry=self.registry,
            scheduler=scheduler,
            chat=ChatContext(
                max_messages=16, max_bytes=8192, max_age_seconds=600.0, clock=self.clock
            ),
        )
        self.traces: list[dict[str, Any]] = []
        self.context.bus.subscribe("**", self.traces.append, order=100)
        self.delays: list[float] = []
        self.diagnostics: list[str] = []
        self.coordinator: PhaseCoordinator | None = None
        self.handle: Any = None

    async def sleeper(self, delay: float) -> None:
        self.delays.append(delay)
        await self.clock.sleep(delay)

    async def activate(
        self,
        *,
        inputs: Mapping[str, Any] | None = None,
        triggers: Mapping[str, Any] | None = None,
        **settings: Any,
    ) -> None:
        loader = ModuleLoader(self.context.bus, ROOT / "modules", context=self.context, environ={})
        config: dict[str, Any] = {
            "enabled_modules": [MODULE_NAME],
            "modules": {
                MODULE_NAME: {**BASE_SETTINGS, "_sleeper": self.sleeper, **settings}
            },
        }
        if inputs is not None:
            config["inputs"] = inputs
        if triggers is not None:
            config["triggers"] = triggers
        activations = await loader.activate_enabled(config)
        (activation,) = activations
        self.handle = activation.handle
        self.coordinator = PhaseCoordinator(
            activations,
            tasks=self.context.tasks,
            reporter=self.diagnostics.append,
            observer=self.context.health.observe_phase,
            hook_timeout_seconds=CLOSE_BUDGET_SECONDS,
        )

    async def start(self, **settings: Any) -> None:
        await self.activate(**settings)
        assert self.coordinator is not None
        report = await asyncio.wait_for(self.coordinator.start(), FAKE_WAIT_SECONDS)
        assert report.status == 0, report.diagnostics

    async def stop(self) -> None:
        assert self.coordinator is not None
        await asyncio.wait_for(self.coordinator.stop(), FAKE_WAIT_SECONDS)

    def records(self, event_type: str) -> list[Mapping[str, Any]]:
        return [
            trace["payload"]
            for trace in self.traces
            if trace["type"] == event_type and trace["payload"].get("module") == MODULE_NAME
        ]

    def health(self) -> list[tuple[str, str | None]]:
        """This module's ready/degraded records, in order, with their reason."""

        return [
            (trace["type"], trace["payload"].get("reason"))
            for trace in self.traces
            if trace["type"] in (TRACE_MODULE_READY, TRACE_MODULE_DEGRADED)
            and trace["payload"].get("module") == MODULE_NAME
        ]

    @property
    def state(self) -> str | None:
        return self.context.health.state(MODULE_NAME)

    @property
    def module(self) -> Any:
        """The bridge package as the loader imported it (its own name)."""

        return sys.modules[type(self.handle).__module__]

    @property
    def run_ready(self) -> bool:
        return RUN_ACTION in self.context.actions.registered_ready()

    def sweep(self, logs: str, *needles: str) -> None:
        """AC4/AC23: no needle in any trace, diagnostic or log record."""

        corpus = "\n".join([*map(repr, self.traces), *self.diagnostics, logs])
        for needle in needles:
            assert needle and corpus.count(needle) == 0, "a value leaked"


def _frame_texts(fake: FakeInstance) -> tuple[str, ...]:
    """The URL and the frame text the sweeps look for."""

    challenge = fixture_frame("hello", 1)["authentication"]
    return (
        PASSWORD,
        fake.url,
        fake.url.removeprefix("ws://").rstrip("/"),
        challenge["salt"],
        challenge["challenge"],
        MALFORMED_HELLO,
        fixture_frame("hello")["session"],
        fixture_frame("get_actions")["actions"][0]["id"],
    )


def _grant(destination: Mapping[str, str]) -> dict[str, Any]:
    return {
        "rule_id": "grant-run",
        "action_name": RUN_ACTION,
        "destination": dict(destination),
        "principals": [PRINCIPAL],
        "natures": ["write"],
        "granted_permissions": ["automation.run"],
    }


#: AC3's calls, one per action the bridge serves while unreachable. P18 adds
#: the ``users.read`` half here (finding P3).
AC3_CALLS = [
    pytest.param(
        _grant({"platform": "twitch", "channel_id": "channel-1", "scope": "automation"}),
        _run_call({"action": "A"}),
        id="run-action",
    ),
]


@pytest.mark.parametrize(("rule", "call"), AC3_CALLS)
async def test_ac3_an_unreachable_instance_leaves_a_ready_companion_and_a_degraded_bridge(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    rule: dict[str, Any],
    call: ActionCall,
) -> None:
    """AC3 (R1), through the whole ``run``: a profile enabling the bridge on a
    loopback port where nothing listens reaches readiness; the bridge has
    exactly 1 ``module.degraded`` (``unreachable``) and, after the startup
    budget, is still degraded with no ``module.ready`` after that record (the
    P14 amendment: the count alone would pass with a bridge left ready); a
    granted call ends ``refused provider_not_ready`` with the provider never
    invoked, so 0 frames. The outcome is asserted, not the exit status."""

    caplog.set_level(logging.DEBUG)
    url = f"ws://{LOOPBACK}:{_silent_loopback_port()}/"
    profile = {
        "modules_directory": str(ROOT / "modules"),
        "enabled_modules": [MODULE_NAME],
        "modules": {
            MODULE_NAME: {
                "url": url,
                "companion_name": "Companion",
                "password": "${BRIDGE_PASSWORD}",
            }
        },
        "actions": [rule],
        # The shipped profile's bounds: the trigger engine the bridge's
        # declaration registers with exists only with a limits block.
        "limits": yaml.safe_load((ROOT / "config.yaml.example").read_text(encoding="utf-8"))[
            "limits"
        ],
    }
    path = tmp_path / "profile.yaml"
    path.write_text(yaml.safe_dump(profile), encoding="utf-8")

    runtimes: list[Any] = []
    assemble = application._assemble_runtime

    def capture(config: Mapping[str, Any]) -> Any:
        runtime = assemble(config)
        runtimes.append(runtime)
        return runtime

    monkeypatch.setattr(application, "_assemble_runtime", capture)
    traces: list[dict[str, Any]] = []
    ready = asyncio.Event()
    stop = asyncio.Event()
    diagnostics: list[str] = []
    outcome: dict[str, Any] = {}

    def report_ready(_message: str) -> None:
        (runtime,) = runtimes
        runtime.bus.subscribe("**", traces.append, order=100)
        ready.set()

    async def drive() -> None:
        try:
            await asyncio.wait_for(ready.wait(), FAKE_WAIT_SECONDS)
            (runtime,) = runtimes
            health = runtime.context.health
            outcome["state"] = health.state(MODULE_NAME)
            executor = runtime.context.executor
            before = executor.provider_invocations
            outcome["observation"] = await executor.invoke(call)
            outcome["invocations"] = executor.provider_invocations - before
        finally:
            stop.set()

    await asyncio.gather(
        application.run(
            path,
            stop,
            environ={"BRIDGE_PASSWORD": PASSWORD},
            ready_reporter=report_ready,
            diagnostic_reporter=diagnostics.append,
        ),
        drive(),
    )
    (runtime,) = runtimes
    history = [*runtime.bus.list_events(), *traces]

    module_records = [
        (event["type"], event["payload"].get("reason"))
        for event in runtime.bus.list_events()
        if event["type"] in (TRACE_MODULE_READY, TRACE_MODULE_DEGRADED)
        and event["payload"].get("module") == MODULE_NAME
    ]
    degraded = [record for record in module_records if record[0] == TRACE_MODULE_DEGRADED]
    assert degraded == [(TRACE_MODULE_DEGRADED, "unreachable")]
    after = module_records[module_records.index(degraded[0]) + 1 :]
    assert all(kind != TRACE_MODULE_READY for kind, _ in after)
    assert outcome["state"] == "degraded"

    observation = outcome["observation"]
    assert observation.status == "refused"
    assert observation.error["code"] == ERROR_PROVIDER_NOT_READY
    assert outcome["invocations"] == 0

    corpus = "\n".join([*map(repr, history), *diagnostics, caplog.text])
    assert corpus.count(PASSWORD) == 0
    assert corpus.count(url) == 0


async def test_ac24_the_challenge_answer_and_readiness_only_after_the_inventory(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """AC24 (R6): the instance receives exactly
    ``base64(sha256(base64(sha256(password + salt)) + challenge))``, and the
    bridge is not ready while the inventory is still unanswered — only once
    it is read; the inventory is kept by name, with id, group and enabled.

    Superseded in part by R6 (AC27, plan step P16): the bridge now also
    subscribes, after the inventory and before readiness, so the frames end
    with one ``Subscribe``."""

    caplog.set_level(logging.DEBUG)
    hello = fixture_frame("hello", 1)["authentication"]
    expected = _expected_authentication(PASSWORD, hello["salt"], hello["challenge"])
    assert streamerbot.authentication_response(
        PASSWORD, hello["salt"], hello["challenge"]
    ) == expected

    async with FakeInstance(require_authentication=True, hold_inventory=True) as fake:
        bridge = _Bridge()
        await bridge.activate(url=fake.url, password=PASSWORD)
        assert bridge.coordinator is not None
        starting = asyncio.ensure_future(bridge.coordinator.start())
        try:
            await _until(lambda: len(fake.requests("GetActions")) == 1)
            (authenticate,) = fake.requests("Authenticate")
            assert authenticate["authentication"] == expected
            # Authenticated, inventory requested but unanswered: not ready.
            assert not starting.done()
            assert not bridge.run_ready
            assert not bridge.handle.connected
            assert bridge.records(TRACE_MODULE_DEGRADED) == []

            fake.release_inventory()
            report = await asyncio.wait_for(starting, FAKE_WAIT_SECONDS)
            assert report.status == 0
            assert bridge.run_ready and bridge.handle.connected
            assert bridge.state == "ready"
            assert bridge.records(TRACE_MODULE_DEGRADED) == []
            assert [frame["request"] for frame in fake.received] == [
                "Authenticate",
                "GetActions",
                "Subscribe",
            ]
            inventory = bridge.handle.inventory
            assert sorted(inventory) == ["A", "B", "C", "D"]
            assert len(inventory["C"]) == 2
            (disabled,) = inventory["D"]
            assert disabled == bridge.module.InstanceAction(
                id="00000000-0000-4000-8000-00000000000d",
                name="D",
                group="Companion",
                enabled=False,
            )
        finally:
            fake.release_inventory()
            await asyncio.gather(starting, return_exceptions=True)
            await bridge.stop()
        bridge.sweep(caplog.text, *_frame_texts(fake), expected)


async def test_r1_a_withheld_inventory_degrades_within_the_startup_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R1: an instance that withholds ``GetActions`` under a long accepted
    request deadline does not hold ``start_inputs`` past its startup bound —
    itself well inside the coordinator's default hook timeout. The bridge is
    left degraded (``unreachable``) with startup successful, and the pending
    attempt goes on in the owner: once answered, the bridge reports ready."""

    assert streamerbot._STARTUP_ATTEMPT_SECONDS < DEFAULT_HOOK_TIMEOUT_SECONDS
    async with FakeInstance(hold_inventory=True) as fake:
        bridge = _Bridge()
        await bridge.activate(url=fake.url, deadlines={"request_seconds": 120})
        monkeypatch.setattr(bridge.module, "_STARTUP_ATTEMPT_SECONDS", 0.2)
        assert bridge.coordinator is not None
        try:
            report = await asyncio.wait_for(bridge.coordinator.start(), FAKE_WAIT_SECONDS)
            assert report.status == 0, report.diagnostics
            assert len(fake.requests("GetActions")) == 1
            assert bridge.state == "degraded" and not bridge.run_ready
            assert bridge.health()[-1] == (TRACE_MODULE_DEGRADED, "unreachable")

            fake.release_inventory()
            await _until(lambda: bridge.state == "ready")
            assert bridge.health()[-2:] == [
                (TRACE_MODULE_DEGRADED, "unreachable"),
                (TRACE_MODULE_READY, None),
            ]
            assert bridge.run_ready and fake.connections == 1
        finally:
            fake.release_inventory()
            await bridge.stop()


async def test_an_instance_without_a_challenge_is_not_sent_an_authentication() -> None:
    """A hello without ``authentication``: no ``Authenticate`` frame, even
    with a password configured; ready once the inventory is read (and, since
    R6's subscription of plan step P16, the owned categories subscribed)."""

    async with FakeInstance() as fake:
        bridge = _Bridge()
        await bridge.start(url=fake.url, password=PASSWORD)
        try:
            assert bridge.run_ready and bridge.state == "ready"
            assert [frame["request"] for frame in fake.received] == ["GetActions", "Subscribe"]
        finally:
            await bridge.stop()


AC4_CASES = [
    pytest.param(
        {"require_authentication": True, "refuse_authentication": True},
        {"password": PASSWORD},
        "authentication_failed",
        id="wrong-password",
    ),
    pytest.param(
        {"require_authentication": True}, {}, "authentication_required", id="no-password"
    ),
    pytest.param({"malformed_hello": True}, {"password": PASSWORD}, "protocol_error", id="hello"),
    pytest.param(
        {"fail_inventory": True}, {"password": PASSWORD}, "inventory_unavailable", id="inventory"
    ),
]


@pytest.mark.parametrize(("faults", "settings", "reason"), AC4_CASES)
async def test_ac4_each_failure_mode_degrades_with_its_reason_and_leaks_nothing(
    caplog: pytest.LogCaptureFixture,
    faults: dict[str, Any],
    settings: dict[str, Any],
    reason: str,
) -> None:
    """AC4 (R1): startup succeeds, the bridge is degraded with the mode's
    reason and stays degraded, the run action is not ready, and no password,
    URL or frame text is in any trace, diagnostic or log (AC23 sweep)."""

    caplog.set_level(logging.DEBUG)
    async with FakeInstance(**faults) as fake:
        bridge = _Bridge()
        await bridge.start(url=fake.url, **settings)
        try:
            assert bridge.health() == [
                (TRACE_MODULE_READY, None),  # the coordinator's prepare observation
                (TRACE_MODULE_DEGRADED, reason),
            ]
            assert bridge.state == "degraded"
            assert not bridge.run_ready
            assert reason in streamerbot.DEGRADED_REASONS
        finally:
            await bridge.stop()
        if "password" not in settings:
            assert fake.requests("Authenticate") == []
        bridge.sweep(caplog.text, *_frame_texts(fake))


async def test_ac25_a_lost_connection_degrades_at_once_and_reconnects_after_the_minimum(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """AC25 (R6): closing the server's connection while ready gives 1
    ``module.degraded`` (``unreachable``) at once and withdraws readiness;
    advancing the injected clock by the minimum delay gives 1 ``module.ready``
    and a fresh handshake. No delay exceeds the maximum."""

    caplog.set_level(logging.DEBUG)
    async with FakeInstance(require_authentication=True) as fake:
        bridge = _Bridge()
        await bridge.start(url=fake.url, password=PASSWORD, reconnect=RECONNECT)
        try:
            assert bridge.run_ready
            before = len(bridge.health())

            await fake.close_connection()
            await _until(lambda: len(bridge.health()) == before + 1)
            assert bridge.health()[before:] == [(TRACE_MODULE_DEGRADED, "unreachable")]
            assert not bridge.run_ready and not bridge.handle.connected
            assert bridge.state == "degraded"

            await _until(lambda: bridge.clock.next_deadline() is not None)
            assert bridge.delays == [RECONNECT["min_delay_seconds"]]
            assert fake.connections == 1
            bridge.clock.advance(RECONNECT["min_delay_seconds"])
            await _until(lambda: len(bridge.health()) == before + 2)
            assert bridge.health()[before:] == [
                (TRACE_MODULE_DEGRADED, "unreachable"),
                (TRACE_MODULE_READY, None),
            ]
            assert bridge.run_ready and bridge.state == "ready"
            assert fake.connections == 2
            assert len(fake.requests("Authenticate")) == 2
            assert max(bridge.delays) <= RECONNECT["max_delay_seconds"]
        finally:
            await bridge.stop()
        bridge.sweep(caplog.text, *_frame_texts(fake))


async def test_ac25_the_backoff_doubles_from_the_minimum_and_is_capped_at_the_maximum(
) -> None:
    """R6: with nothing listening, each failed attempt doubles the delay from
    the minimum, never past the maximum, and repeats no record (the same
    ``unreachable`` reason is not a new fact)."""

    bridge = _Bridge()
    await bridge.start(url=f"ws://{LOOPBACK}:{_silent_loopback_port()}/", reconnect=RECONNECT)
    try:
        assert bridge.state == "degraded"
        for expected in (1, 2, 4, 4, 4):
            await _until(lambda: bridge.clock.next_deadline() is not None)
            assert bridge.delays[-1] == expected
            bridge.clock.advance(expected)
        await _until(lambda: len(bridge.delays) == 6)
        assert bridge.delays == [1, 2, 4, 4, 4, 4]
        assert max(bridge.delays) <= RECONNECT["max_delay_seconds"]
        assert bridge.records(TRACE_MODULE_DEGRADED) == [
            {**bridge.records(TRACE_MODULE_DEGRADED)[0], "reason": "unreachable"}
        ]
        assert bridge.records(TRACE_MODULE_READY)[1:] == []
    finally:
        await bridge.stop()


async def test_ac25_a_failed_start_retries_and_a_success_reports_ready() -> None:
    """R6, never silently: an instance unavailable at startup and reachable
    at the next attempt produces 1 ``module.ready`` after the degradation."""

    async with FakeInstance(fail_inventory=True) as fake:
        bridge = _Bridge()
        await bridge.start(url=fake.url, reconnect=RECONNECT)
        try:
            assert bridge.state == "degraded" and not bridge.run_ready
            fake.fail_inventory = False
            await _until(lambda: bridge.clock.next_deadline() is not None)
            bridge.clock.advance(RECONNECT["min_delay_seconds"])
            await _until(lambda: bridge.state == "ready")
            assert bridge.health()[-2:] == [
                (TRACE_MODULE_DEGRADED, "inventory_unavailable"),
                (TRACE_MODULE_READY, None),
            ]
            assert bridge.run_ready
        finally:
            await bridge.stop()


async def test_ac28_cancelling_the_owner_task_propagates_and_releases_the_connection(
) -> None:
    """AC28 (R6, connection half): cancelling the connection owner raises
    ``CancelledError`` to whoever awaits it; the connection is released, the
    bridge is no longer ready and the loss is reported degraded."""

    async with FakeInstance() as fake:
        bridge = _Bridge()
        await bridge.start(url=fake.url)
        try:
            owner = bridge.handle._owner
            assert owner is not None and not owner.done()
            owner.cancel()
            with pytest.raises(asyncio.CancelledError):
                await owner
            assert not bridge.run_ready and not bridge.handle.connected
            assert bridge.state == "degraded"
            assert bridge.health()[-1] == (TRACE_MODULE_DEGRADED, "unreachable")
            await asyncio.wait_for(fake.wait_for_disconnections(1), FAKE_WAIT_SECONDS)
        finally:
            await bridge.stop()


async def test_ac28_shutdown_closes_within_budget_and_fails_a_pending_request(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """AC28 (R6, connection half): shutdown fails a pending ``GetActions``
    request with :class:`ConnectionClosed` instead of leaving it hanging, and
    the fake server observes the close within the close budget. The in-flight
    run clause is P15's."""

    caplog.set_level(logging.DEBUG)
    async with FakeInstance() as fake:
        bridge = _Bridge()
        await bridge.start(url=fake.url, password=PASSWORD)
        assert bridge.run_ready
        fake.hold_inventory = True
        connection = bridge.handle._connection
        pending = asyncio.ensure_future(
            connection.request({"request": "GetActions"}, 10 * FAKE_WAIT_SECONDS)
        )
        await _until(lambda: len(fake.requests("GetActions")) == 2)
        assert connection.pending == 1

        await asyncio.wait_for(bridge.stop(), CLOSE_BUDGET_SECONDS)
        await asyncio.wait_for(fake.wait_for_disconnections(1), CLOSE_BUDGET_SECONDS)
        assert pending.done()
        with pytest.raises(bridge.module.ConnectionClosed):
            pending.result()
        assert connection.pending == 0
        assert not bridge.run_ready
        assert bridge.state == "stopped"
        assert bridge.handle._owner is None
        assert bridge.diagnostics == []
        fake.release_inventory()
    bridge.sweep(caplog.text, *_frame_texts(fake))


async def test_a_connection_loss_fails_every_pending_request() -> None:
    """The reader fails what waits when the server closes: no request is
    left hanging on a lost connection either."""

    async with FakeInstance() as fake:
        bridge = _Bridge()
        await bridge.start(url=fake.url, reconnect=RECONNECT)
        try:
            fake.hold_inventory = True
            connection = bridge.handle._connection
            pending = asyncio.ensure_future(
                connection.request({"request": "GetActions"}, 10 * FAKE_WAIT_SECONDS)
            )
            await _until(lambda: len(fake.requests("GetActions")) == 2)
            await fake.close_connection()
            with pytest.raises(bridge.module.ConnectionClosed):
                await asyncio.wait_for(pending, FAKE_WAIT_SECONDS)
            await _until(lambda: bridge.state == "degraded")
        finally:
            fake.release_inventory()
            await bridge.stop()


def test_the_sleeper_seam_must_be_callable() -> None:
    assert validate_settings(_with(_sleeper=1)) == [_diagnostic("_sleeper", "must be callable")]
    assert validate_settings(_with(_sleeper=asyncio.sleep)) == []


# -- P15: the allowlist, reconciliation and the run provider (R4, R5) -------- #

#: The acknowledgement deadline of the run scenarios, on the manual clock.
ACK_SECONDS = 2.0
#: The ids of the default inventory's single-named actions (C appears twice).
INSTANCE_IDS = {
    entry["name"]: entry["id"]
    for entry in fixture_frame("get_actions")["actions"]
    if entry["name"] != "C"
}
#: The group the default inventory files ``A`` (and the disabled ``D``) under.
GROUP_OF_A = "Companion"
#: AC19's entry schema.
SCHEMA_N_INTEGER = {
    "type": "object",
    "properties": {"n": {"type": "integer"}},
    "additionalProperties": False,
}
#: Every run observation the P15 scenarios produced (AC16's suite-level
#: assertion: none is a success).
RUN_OBSERVATIONS: list[ActionObservation] = []


class _Gate:
    """The ``_before_transmit`` seam, blocked until released."""

    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.released = asyncio.Event()

    async def __call__(self) -> None:
        self.entered.set()
        await self.released.wait()


class _Expiry:
    """An executor sleeper: its timer fires only when the test says so."""

    def __init__(self) -> None:
        self.armed = asyncio.Event()
        self.fired = asyncio.Event()

    async def __call__(self, delay: float) -> None:
        del delay
        self.armed.set()
        await self.fired.wait()


class _Runs(_Bridge):
    """The bridge, connected to *fake*, with run calls and their outcomes."""

    def __init__(self, fake: FakeInstance) -> None:
        super().__init__()
        self.fake = fake
        self._call_ids = itertools.count(1)
        self._stopped = False

    async def stop(self) -> None:
        if not self._stopped:
            self._stopped = True
            await super().stop()

    def call(self, name: str, arguments: Mapping[str, Any] | None = None) -> ActionCall:
        payload: dict[str, Any] = {"action": name}
        if arguments is not None:
            payload["arguments"] = dict(arguments)
        return dataclasses.replace(_run_call(payload), call_id=f"call-{next(self._call_ids)}")

    def executor(self, sleeper: Any) -> ActionExecutor:
        """An executor on the same registry and policy whose own timer is
        driven by *sleeper* (the default one sleeps in real time)."""

        return ActionExecutor(
            self.context.actions,
            self.context.actions._authorization,
            supervision=self.context.supervision,
            clock=self.clock,
            sleeper=sleeper,
        )

    async def run(self, call: ActionCall, executor: Any = None) -> ActionObservation:
        target = executor if executor is not None else self.context.executor
        return self.observed(await asyncio.wait_for(target.invoke(call), FAKE_WAIT_SECONDS))

    def observed(self, observation: ActionObservation | None) -> ActionObservation:
        """Record a run outcome; no run outcome is ever a success (R4)."""

        assert observation is not None
        RUN_OBSERVATIONS.append(observation)
        assert observation.status != "success"
        assert observation.error is not None and observation.error["retryable"] is False
        return observation

    def frames(self, call: ActionCall | None = None) -> list[dict[str, Any]]:
        """The ``DoAction`` request frames the fake received (for *call*)."""

        runs = self.fake.requests("DoAction")
        if call is None:
            return runs
        return [frame for frame in runs if frame["id"].endswith(f".{call.call_id}")]

    def reconciled(self) -> list[Mapping[str, Any]]:
        return self.records(streamerbot.TRACE_ALLOWLIST_RECONCILED)

    def gate(self) -> _Gate:
        gate = _Gate()
        self.handle._before_transmit = gate
        return gate


def _outcome(
    observation: ActionObservation, status: str, code: str, cause: str | None = None
) -> None:
    """Status, code and cause exactly, and never retryable (AC16)."""

    assert observation.status == status, observation
    assert observation.error is not None
    assert observation.error["code"] == code, observation.error
    assert observation.error.get("cause") == cause, observation.error
    assert observation.error["retryable"] is False
    if status == "external_unknown":
        assert streamerbot.UNCERTAINTY_SENTENCE in observation.error["message"]


@contextlib.asynccontextmanager
async def _scenario(
    caplog: pytest.LogCaptureFixture,
    *,
    allowlist: Any = None,
    faults: Mapping[str, Any] | None = None,
    grant: bool = True,
    before_start: Any = None,
    **settings: Any,
) -> Any:
    """A connected bridge against an instance requiring authentication.

    On exit: the bridge is stopped, no ``action.completed`` trace of the run
    action is a success, and the configured password appears 0 times in any
    trace, diagnostic or log record (AC23, run scenarios).
    """

    caplog.set_level(logging.DEBUG)
    async with FakeInstance(require_authentication=True, **(faults or {})) as fake:
        runs = _Runs(fake)
        if allowlist is not None:
            settings["allowlist"] = allowlist
        await runs.activate(
            url=fake.url,
            password=PASSWORD,
            deadlines={"acknowledgement_seconds": ACK_SECONDS},
            reconnect=RECONNECT,
            **settings,
        )
        if before_start is not None:
            before_start(runs)
        assert runs.coordinator is not None
        report = await asyncio.wait_for(runs.coordinator.start(), FAKE_WAIT_SECONDS)
        assert report.status == 0, report.diagnostics
        if grant:
            runs.context.actions._authorization.grant(
                AuthorizationRule(
                    rule_id="grant-run",
                    action_name=RUN_ACTION,
                    granted_permissions=("automation.run",),
                )
            )
        try:
            yield runs
        finally:
            await runs.stop()
        completed = [
            trace["payload"]
            for trace in runs.traces
            if trace["type"] == TRACE_ACTION_COMPLETED
            and trace["payload"].get("action") == RUN_ACTION
        ]
        assert all(payload["status"] != "success" for payload in completed)
        runs.sweep(caplog.text, PASSWORD)


# -- AC16: one test per row of the R4 table ---------------------------------- #


async def test_ac16_row_not_allowlisted(caplog: pytest.LogCaptureFixture) -> None:
    async with _scenario(caplog, allowlist=[{"action": "A"}]) as runs:
        _outcome(await runs.run(runs.call("B")), "refused", "not_allowlisted")
        assert runs.frames() == []


async def test_ac16_row_instance_action_missing(caplog: pytest.LogCaptureFixture) -> None:
    async with _scenario(caplog, allowlist=[{"action": "E"}]) as runs:
        _outcome(await runs.run(runs.call("E")), "refused", "instance_action_missing")
        assert runs.frames() == []


async def test_ac16_row_instance_action_ambiguous(caplog: pytest.LogCaptureFixture) -> None:
    async with _scenario(caplog, allowlist=[{"action": "C"}]) as runs:
        _outcome(await runs.run(runs.call("C")), "refused", "instance_action_ambiguous")
        assert runs.frames() == []


async def test_ac16_row_instance_action_disabled(caplog: pytest.LogCaptureFixture) -> None:
    async with _scenario(caplog, allowlist=[{"action": "D"}]) as runs:
        _outcome(await runs.run(runs.call("D")), "refused", "instance_action_disabled")
        assert runs.frames() == []


async def test_ac16_row_invalid_arguments(caplog: pytest.LogCaptureFixture) -> None:
    async with _scenario(caplog, allowlist=[{"action": "A"}]) as runs:
        _outcome(
            await runs.run(runs.call("A", {"x": 1})), "error", ERROR_INVALID_ARGUMENTS
        )
        assert runs.frames() == []


async def test_ac16_row_bridge_not_ready(caplog: pytest.LogCaptureFixture) -> None:
    """The existing executor rule: the provider is never invoked."""

    async with _scenario(
        caplog, allowlist=[{"action": "A"}], faults={"fail_inventory": True}
    ) as runs:
        assert runs.state == "degraded"
        before = runs.context.executor.provider_invocations
        _outcome(await runs.run(runs.call("A")), "refused", ERROR_PROVIDER_NOT_READY)
        assert runs.context.executor.provider_invocations == before
        assert runs.frames() == []


async def test_ac16_row_acknowledged_is_execution_unconfirmed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """One frame naming the instance action's *id* (never its name), and the
    acknowledgement reported as unconfirmed — never a success."""

    async with _scenario(caplog, allowlist=[{"action": "A"}]) as runs:
        call = runs.call("A")
        observation = await runs.run(call)
        _outcome(
            observation, "external_unknown", ERROR_EXTERNAL_UNKNOWN, "execution_unconfirmed"
        )
        assert observation.provenance["emission"] == EMISSION_EMITTED
        (frame,) = runs.frames()
        assert runs.frames(call) == [frame]
        assert frame["action"] == {"id": INSTANCE_IDS["A"]}
        assert frame["args"] == {}


async def test_ac16_row_instance_error_answer(caplog: pytest.LogCaptureFixture) -> None:
    async with _scenario(
        caplog, allowlist=[{"action": "A"}], faults={"reject_runs": True}
    ) as runs:
        _outcome(
            await runs.run(runs.call("A")), "error", ERROR_PROVIDER_FAILED, "instance_rejected"
        )
        assert len(runs.frames()) == 1


async def test_ac16_row_no_acknowledgement_before_the_deadline(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async with _scenario(
        caplog, allowlist=[{"action": "A"}], faults={"withhold_acknowledgements": True}
    ) as runs:
        running = asyncio.ensure_future(runs.context.executor.invoke(runs.call("A")))
        await _until(lambda: runs.clock.next_deadline() == runs.clock() + ACK_SECONDS)
        await _until(lambda: len(runs.frames()) == 1)
        assert not running.done()
        runs.clock.advance(ACK_SECONDS)
        observation = runs.observed(await asyncio.wait_for(running, FAKE_WAIT_SECONDS))
        _outcome(
            observation, "external_unknown", ERROR_EXTERNAL_UNKNOWN, "acknowledgement_lost"
        )
        assert len(runs.frames()) == 1


async def test_ac16_row_cancelled_before_the_frame_left(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """R4's last row: cancelled while the frame has not left — the existing
    ``cancelled`` code, no cause, nothing emitted, 0 frames."""

    async with _scenario(caplog, allowlist=[{"action": "A"}]) as runs:
        gate = runs.gate()
        call = runs.call("A")
        running = asyncio.ensure_future(runs.context.executor.invoke(call))
        await asyncio.wait_for(gate.entered.wait(), FAKE_WAIT_SECONDS)
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running
        observation = runs.observed(runs.context.executor.outcome(call.call_id))
        _outcome(observation, "cancelled", ERROR_CANCELLED)
        assert observation.provenance["emission"] == EMISSION_NOT_EMITTED
        assert runs.frames() == []


def test_ac16_no_run_observation_is_a_success() -> None:
    """AC16's suite-level assertion over every outcome the scenarios above
    recorded (each scenario also checks its own ``action.completed``
    traces): no run of the action is ever reported a success."""

    assert all(observation.status != "success" for observation in RUN_OBSERVATIONS)


# -- AC17: a late acknowledgement --------------------------------------------- #


async def test_ac17_a_late_acknowledgement_is_lost_and_never_re_invoked(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """AC17 (R4): the acknowledgement arrives after the deadline — the call
    is ``acknowledgement_lost``, the late answer changes nothing, the
    provider is not re-invoked, and the instance received exactly 1 frame
    for that ``call_id``."""

    async with _scenario(
        caplog, allowlist=[{"action": "A"}], faults={"hold_acknowledgements": True}
    ) as runs:
        executor = runs.context.executor
        call = runs.call("A")
        running = asyncio.ensure_future(executor.invoke(call))
        await _until(lambda: len(runs.frames(call)) == 1)
        await _until(lambda: runs.clock.next_deadline() is not None)
        runs.clock.advance(ACK_SECONDS)
        observation = runs.observed(await asyncio.wait_for(running, FAKE_WAIT_SECONDS))
        _outcome(
            observation, "external_unknown", ERROR_EXTERNAL_UNKNOWN, "acknowledgement_lost"
        )
        invocations = executor.provider_invocations

        runs.fake.release_acknowledgements()
        await _until(lambda: not runs.fake._pending)
        # The reader answers in order: once a later request is answered, the
        # late acknowledgement has been read (and dropped).
        connection = runs.handle._connection
        await connection.request({"request": "GetActions"}, FAKE_WAIT_SECONDS)
        assert executor.provider_invocations == invocations
        assert executor.outcome(call.call_id) == observation
        assert len(runs.frames(call)) == 1
        assert connection.pending == 0


async def test_ac17_an_answer_landing_while_the_timer_is_cleaned_up_is_lost(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC17 (R4): the deadline is latched when the wait returns — an answer
    resolved during the timer's cleanup is late, never accepted."""

    async with _scenario(caplog, allowlist=[{"action": "A"}]) as runs:
        bridge = runs.handle
        answer: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        deadline_passed = False
        gather = asyncio.gather

        def answering_gather(*aws: Any, **kwargs: Any) -> Any:
            # The timer's cleanup: the acknowledgement arrives just now.
            if deadline_passed and not answer.done():
                answer.set_result({"status": "ok"})
            return gather(*aws, **kwargs)

        monkeypatch.setattr(asyncio, "gather", answering_gather)
        waiting = asyncio.ensure_future(bridge._acknowledgement(answer))
        await _until(lambda: runs.clock.next_deadline() is not None)
        deadline_passed = True
        runs.clock.advance(ACK_SECONDS)
        reply = await asyncio.wait_for(waiting, FAKE_WAIT_SECONDS)
        monkeypatch.undo()
        assert answer.done()
        assert reply is None


# -- AC18, AC19, AC45, AC20: the allowlist ------------------------------------ #


async def test_ac18_only_allowlisted_names_are_reachable(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """AC18 (R5): ``B`` against ``[{action: A}]`` is refused with 0 frames."""

    async with _scenario(caplog, allowlist=[{"action": "A"}]) as runs:
        _outcome(await runs.run(runs.call("B")), "refused", "not_allowlisted")
        assert runs.frames() == []
        _outcome(
            await runs.run(runs.call("A")),
            "external_unknown",
            ERROR_EXTERNAL_UNKNOWN,
            "execution_unconfirmed",
        )
        assert len(runs.frames()) == 1


@pytest.mark.parametrize("allowlist", [[], None], ids=["empty", "absent"])
async def test_ac18_an_empty_or_absent_allowlist_refuses_everything(
    caplog: pytest.LogCaptureFixture, allowlist: Any
) -> None:
    async with _scenario(caplog, allowlist=allowlist) as runs:
        for name in ("A", "B", "C", "D", "E"):
            _outcome(await runs.run(runs.call(name)), "refused", "not_allowlisted")
        assert runs.frames() == []


async def test_ac18_a_group_entry_reaches_its_actions_only(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """AC18 (R5): with ``[{group: G}]``, ``A`` (in G) is reachable and ``B``
    (not in G) refused."""

    async with _scenario(caplog, allowlist=[{"group": GROUP_OF_A}]) as runs:
        _outcome(await runs.run(runs.call("B")), "refused", "not_allowlisted")
        assert runs.frames() == []
        _outcome(
            await runs.run(runs.call("A")),
            "external_unknown",
            ERROR_EXTERNAL_UNKNOWN,
            "execution_unconfirmed",
        )
        (frame,) = runs.frames()
        assert frame["action"] == {"id": INSTANCE_IDS["A"]}


async def test_ac19_the_entry_schema_decides_the_arguments(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """AC19 (R5): the schema refuses ``{n: "x"}`` and ``{m: 1}`` with 0
    frames; an entry without ``arguments`` refuses ``{x: 1}`` and accepts
    ``{}``."""

    allowlist = [{"action": "A", "arguments": SCHEMA_N_INTEGER}, {"action": "B"}]
    async with _scenario(caplog, allowlist=allowlist) as runs:
        for arguments in ({"n": "x"}, {"m": 1}):
            _outcome(
                await runs.run(runs.call("A", arguments)), "error", ERROR_INVALID_ARGUMENTS
            )
        _outcome(await runs.run(runs.call("B", {"x": 1})), "error", ERROR_INVALID_ARGUMENTS)
        assert runs.frames() == []

        _outcome(
            await runs.run(runs.call("B", {})),
            "external_unknown",
            ERROR_EXTERNAL_UNKNOWN,
            "execution_unconfirmed",
        )
        _outcome(
            await runs.run(runs.call("A", {"n": 1})),
            "external_unknown",
            ERROR_EXTERNAL_UNKNOWN,
            "execution_unconfirmed",
        )
        assert [frame["args"] for frame in runs.frames()] == [{}, {"n": 1}]


#: AC45's schemas: S1 accepts ``{n: 1}``, S2 refuses it; both accept ``{m: 2}``.
SCHEMA_S1 = {"type": "object", "properties": {"n": {"type": "integer"}}}
SCHEMA_S2 = {
    "type": "object",
    "properties": {"n": {"type": "string"}, "m": {"type": "integer"}},
}


async def test_ac45_the_action_entry_beats_the_group_entry(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """AC45 (R5): with ``{action: A, S1}`` and ``{group: G, S2}``, A in G,
    the action entry alone decides — ``A {n: 1}`` sends 1 frame. Without the
    action entry, the group's S2 refuses the same run (0 frames)."""

    allowlist = [
        {"action": "A", "arguments": SCHEMA_S1},
        {"group": GROUP_OF_A, "arguments": SCHEMA_S2},
    ]
    async with _scenario(caplog, allowlist=allowlist) as runs:
        _outcome(
            await runs.run(runs.call("A", {"n": 1})),
            "external_unknown",
            ERROR_EXTERNAL_UNKNOWN,
            "execution_unconfirmed",
        )
        assert len(runs.frames()) == 1
    async with _scenario(caplog, allowlist=allowlist[1:]) as runs:
        _outcome(await runs.run(runs.call("A", {"n": 1})), "error", ERROR_INVALID_ARGUMENTS)
        assert runs.frames() == []


async def test_ac45_every_matching_group_entry_must_accept(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """AC45 (R5): with ``{group: G, S1}`` and ``{group: H, S2}`` and A in G
    and H, ``A {n: 1}`` ends ``invalid_arguments`` with 0 frames, and
    arguments both schemas accept send 1 frame.

    The documented inventory files each action under one group, so the
    fixture cannot place ``A`` in two; the test widens the bridge's group
    membership predicate to put ``A`` in both ``G`` and ``H``.
    """

    def in_g_and_h(runs: _Runs) -> None:
        def member(action: Any, group: str) -> bool:
            if action.name == "A":
                return group in ("G", "H")
            return action.group == group

        runs.module._in_group = member

    allowlist = [{"group": "G", "arguments": SCHEMA_S1}, {"group": "H", "arguments": SCHEMA_S2}]
    async with _scenario(caplog, allowlist=allowlist, before_start=in_g_and_h) as runs:
        try:
            (reconciled,) = runs.reconciled()
            assert reconciled["unmatched_groups"] == []
            _outcome(
                await runs.run(runs.call("A", {"n": 1})), "error", ERROR_INVALID_ARGUMENTS
            )
            assert runs.frames() == []
            _outcome(
                await runs.run(runs.call("A", {"m": 2})),
                "external_unknown",
                ERROR_EXTERNAL_UNKNOWN,
                "execution_unconfirmed",
            )
            assert [frame["args"] for frame in runs.frames()] == [{"m": 2}]
        finally:
            runs.module._in_group = streamerbot._in_group


async def test_ac20_reconciliation_names_the_unmatched_entries_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """AC20 (R5): allowlist {A, C, D, E} against {A, C twice, D disabled}:
    exactly 1 reconciliation trace naming C, D and E and not A; C, D, E end
    ambiguous, disabled and missing with 0 frames; A sends exactly 1."""

    allowlist = [{"action": name} for name in ("A", "C", "D", "E")]
    async with _scenario(caplog, allowlist=allowlist) as runs:
        (reconciled,) = runs.reconciled()
        assert reconciled["unmatched_actions"] == ["C", "D", "E"]
        assert reconciled["unmatched_groups"] == []
        assert reconciled["reachable"] == 1
        assert "A" not in repr(reconciled)
        # Value-free: configured names and counts only.
        assert set(reconciled) == {
            "module",
            "entries",
            "reachable",
            "unmatched_actions",
            "unmatched_groups",
        }
        for name, code in (
            ("C", "instance_action_ambiguous"),
            ("D", "instance_action_disabled"),
            ("E", "instance_action_missing"),
        ):
            _outcome(await runs.run(runs.call(name)), "refused", code)
        assert runs.frames() == []
        _outcome(
            await runs.run(runs.call("A")),
            "external_unknown",
            ERROR_EXTERNAL_UNKNOWN,
            "execution_unconfirmed",
        )
        assert len(runs.frames()) == 1


async def test_r5_reconciliation_runs_again_at_every_connection(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """R5: a reconnection reconciles again — one more trace — and a group
    entry reaching nothing is named by its configured name."""

    allowlist = [{"action": "A"}, {"group": "Nowhere"}]
    async with _scenario(caplog, allowlist=allowlist) as runs:
        (first,) = runs.reconciled()
        assert first["unmatched_groups"] == ["Nowhere"]
        await runs.fake.close_connection()
        await _until(lambda: runs.state == "degraded")
        await _until(lambda: runs.clock.next_deadline() is not None)
        runs.clock.advance(RECONNECT["min_delay_seconds"])
        await _until(lambda: runs.state == "ready")
        assert len(runs.reconciled()) == 2


# -- AC21: authorization first ------------------------------------------------- #


async def test_ac21_a_principal_without_a_rule_is_refused_before_the_provider(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async with _scenario(caplog, allowlist=[{"action": "A"}], grant=False) as runs:
        before = runs.context.executor.provider_invocations
        _outcome(await runs.run(runs.call("A")), "refused", ERROR_NOT_AUTHORIZED)
        assert runs.context.executor.provider_invocations == before
        assert runs.frames() == []


# -- D8: the transmission boundary (finding P6) -------------------------------- #


async def test_d8_the_executor_deadline_before_the_frame_left_is_a_timeout(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async with _scenario(caplog, allowlist=[{"action": "A"}]) as runs:
        gate = runs.gate()
        expiry = _Expiry()
        observation_task = asyncio.ensure_future(runs.run(runs.call("A"), runs.executor(expiry)))
        await asyncio.wait_for(gate.entered.wait(), FAKE_WAIT_SECONDS)
        await asyncio.wait_for(expiry.armed.wait(), FAKE_WAIT_SECONDS)
        expiry.fired.set()
        observation = await asyncio.wait_for(observation_task, FAKE_WAIT_SECONDS)
        _outcome(observation, "timeout", ERROR_TIMED_OUT)
        assert observation.provenance["emission"] == EMISSION_NOT_EMITTED
        assert runs.frames() == []


async def test_d8_a_connection_closed_before_the_segment_is_provider_not_ready(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async with _scenario(caplog, allowlist=[{"action": "A"}]) as runs:
        gate = runs.gate()
        running = asyncio.ensure_future(runs.run(runs.call("A")))
        await asyncio.wait_for(gate.entered.wait(), FAKE_WAIT_SECONDS)
        await runs.fake.close_connection()
        await _until(lambda: not runs.handle.connected)
        gate.released.set()
        observation = await asyncio.wait_for(running, FAKE_WAIT_SECONDS)
        _outcome(observation, "refused", ERROR_PROVIDER_NOT_READY)
        assert observation.provenance["emission"] == EMISSION_NOT_EMITTED
        assert runs.frames() == []


@pytest.mark.parametrize("fault", ["transport-closed", "is-closing"])
async def test_d8_a_pre_write_failure_sends_nothing_and_withdraws_readiness(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    """Finding P6, round 2: the client's transport becomes closing with no
    loop turn reaching the bridge, then the hook is released. The pre-write
    check refuses: ``provider_not_ready``, nothing emitted, 0 frames, and
    the bridge withdraws readiness at once (then degrades)."""

    async with _scenario(caplog, allowlist=[{"action": "A"}]) as runs:
        gate = runs.gate()
        call = runs.call("A")
        running = asyncio.ensure_future(runs.context.executor.invoke(call))
        await asyncio.wait_for(gate.entered.wait(), FAKE_WAIT_SECONDS)
        transport = runs.handle._connection.ws._writer.transport
        if fault == "transport-closed":
            transport.close()
        else:
            monkeypatch.setattr(transport, "is_closing", lambda: True)
        assert runs.handle.connected and runs.run_ready
        gate.released.set()  # no await between the fault and the release
        observation = runs.observed(await asyncio.wait_for(running, FAKE_WAIT_SECONDS))
        _outcome(observation, "refused", ERROR_PROVIDER_NOT_READY)
        assert observation.provenance["emission"] == EMISSION_NOT_EMITTED
        assert not runs.run_ready
        assert not runs.handle.connected
        await _until(lambda: runs.state == "degraded")
        await connection_settled(runs)
        assert runs.frames() == []


async def connection_settled(runs: _Runs) -> None:
    """Wait for the fake to see the old connection end, so a frame that left
    would have been recorded by now."""

    await asyncio.wait_for(runs.fake.wait_for_disconnections(1), FAKE_WAIT_SECONDS)


async def test_d8_cancelling_after_the_segment_is_acknowledgement_lost(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async with _scenario(
        caplog, allowlist=[{"action": "A"}], faults={"withhold_acknowledgements": True}
    ) as runs:
        call = runs.call("A")
        running = asyncio.ensure_future(runs.context.executor.invoke(call))
        await _until(lambda: len(runs.frames(call)) == 1)
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running
        observation = runs.observed(runs.context.executor.outcome(call.call_id))
        _outcome(
            observation, "external_unknown", ERROR_EXTERNAL_UNKNOWN, "acknowledgement_lost"
        )
        assert observation.provenance["emission"] == EMISSION_EMITTED
        assert len(runs.frames()) == 1


@pytest.mark.parametrize("interruption", ["cancel", "executor-deadline"])
async def test_d8_an_interruption_in_the_drain_after_the_write_is_acknowledgement_lost(
    caplog: pytest.LogCaptureFixture, interruption: str
) -> None:
    """Post-write interruption: the protocol's writing is paused and the
    writer's output pushed over its limit, so ``send_str`` suspends in the
    drain *after* the write. A cancellation or the executor's deadline there
    is ``external_unknown`` (``acknowledgement_lost``), emission ``emitted``,
    and exactly 1 frame reaches the instance once the protocol resumes."""

    async with _scenario(caplog, allowlist=[{"action": "A"}]) as runs:
        gate = runs.gate()
        expiry = _Expiry()
        executor = runs.executor(expiry)
        call = runs.call("A")
        running = asyncio.ensure_future(executor.invoke(call))
        await asyncio.wait_for(gate.entered.wait(), FAKE_WAIT_SECONDS)
        writer = runs.handle._connection.ws._writer
        protocol = writer.protocol
        protocol.pause_writing()
        writer._output_size = writer._limit
        gate.released.set()
        await _until(lambda: protocol._drain_waiter is not None)
        assert not running.done()
        if interruption == "cancel":
            running.cancel()
            with pytest.raises(asyncio.CancelledError):
                await running
            observation = runs.observed(executor.outcome(call.call_id))
        else:
            expiry.fired.set()
            observation = runs.observed(await asyncio.wait_for(running, FAKE_WAIT_SECONDS))
        _outcome(
            observation, "external_unknown", ERROR_EXTERNAL_UNKNOWN, "acknowledgement_lost"
        )
        assert observation.provenance["emission"] == EMISSION_EMITTED
        protocol.resume_writing()
        await _until(lambda: len(runs.frames(call)) == 1)
        assert len(runs.frames()) == 1


def _invoke_tree() -> ast.AsyncFunctionDef:
    source = textwrap.dedent(inspect.getsource(streamerbot._RunProvider.invoke))
    (function,) = ast.parse(source).body
    assert isinstance(function, ast.AsyncFunctionDef)
    return function


def _calls_named(tree: ast.AST, name: str) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == name
    ]


def test_d8_the_segment_has_no_await_between_the_check_the_mark_and_the_send() -> None:
    """D8, static: in the provider's source, ``mark_not_emitted()`` is the
    first statement; the pre-write check comes before ``mark_emitted()``,
    which comes before ``ws.send_str``; and no ``await`` lies between the
    check and the send other than the one awaiting ``send_str`` itself."""

    function = _invoke_tree()
    first = function.body[0]
    if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
        first = function.body[1]
    assert isinstance(first, ast.Expr) and isinstance(first.value, ast.Call)
    assert first.value.func.attr == "mark_not_emitted"
    assert not any(
        isinstance(node, ast.Await)
        for node in ast.walk(function)
        if getattr(node, "lineno", first.lineno + 1) <= first.lineno
    )

    (check,) = _calls_named(function, "_writable")
    (mark,) = _calls_named(function, "mark_emitted")
    (send,) = _calls_named(function, "send_str")
    assert check.lineno < mark.lineno < send.lineno
    awaits = [
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Await) and check.lineno <= node.lineno <= send.lineno
    ]
    assert [node.value for node in awaits] == [send]
    (hook,) = _calls_named(function, "_before_transmit")
    assert hook.lineno < check.lineno


def test_d8_canary_the_installed_client_writes_before_its_first_suspension() -> None:
    """D8 canary on the installed client: in ``WebSocketWriter.send_frame``'s
    uncompressed branch no ``await`` precedes the ``_write_websocket_frame``
    call, and the only raises before the transport write — across
    ``send_frame`` and ``_write_websocket_frame`` — are its two
    closing-transport checks. If this fails after an upgrade, the bridge
    must take D8's fallback route: a module-local writer that writes the
    already-built masked frame synchronously through the protocol transport,
    then marks."""

    from aiohttp._websocket.writer import WebSocketWriter

    route = "D8 fallback route: module-local synchronous writer"

    def parse(function: Any) -> ast.AST:
        (tree,) = ast.parse(textwrap.dedent(inspect.getsource(function))).body
        return tree

    send_frame = parse(WebSocketWriter.send_frame)
    (write_call,) = _calls_named(send_frame, "_write_websocket_frame")
    branch = next(
        node
        for node in ast.walk(send_frame)
        if isinstance(node, ast.If)
        and any(write_call is child for item in node.body for child in ast.walk(item))
    )
    before_write = [
        node
        for node in ast.walk(send_frame)
        if getattr(node, "lineno", write_call.lineno) < write_call.lineno
    ]
    assert not any(isinstance(node, ast.Await) for node in before_write), route
    assert not any(isinstance(node, ast.Await) for node in ast.walk(branch.test)), route
    raises = [node for node in before_write if isinstance(node, ast.Raise)]

    write_frame = parse(WebSocketWriter._write_websocket_frame)
    transport_writes = [
        call
        for call in _calls_named(write_frame, "write")
        if isinstance(call.func.value, ast.Attribute) and call.func.value.attr == "transport"
    ]
    first_write = min(call.lineno for call in transport_writes)
    raises += [
        node
        for node in ast.walk(write_frame)
        if isinstance(node, ast.Raise) and node.lineno < first_write
    ]
    assert not any(isinstance(node, ast.Await) for node in ast.walk(write_frame)), route
    assert len(raises) == 2, route
    for node in raises:
        assert isinstance(node.exc, ast.Call), route
        assert getattr(node.exc.func, "id", None) == "ClientConnectionResetError", route
        assert node.exc.args and "closing" in getattr(node.exc.args[0], "value", ""), route


def test_d8_the_bridge_connects_without_compression() -> None:
    """D8: the uncompressed branch is the one the canary pins, so the
    bridge's single ``ws_connect`` call passes ``compress=0``."""

    tree = ast.parse(Path(streamerbot.__file__).read_text(encoding="utf-8"))
    (connect,) = _calls_named(tree, "ws_connect")
    (keyword,) = [item for item in connect.keywords if item.arg == "compress"]
    assert isinstance(keyword.value, ast.Constant) and keyword.value.value == 0


# -- AC28 (in-flight half): a run in flight at shutdown ---------------------- #


async def test_ac28_a_run_in_flight_at_shutdown_is_acknowledgement_lost(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """AC28 (R6, moved from P14): a run whose frame was sent and whose
    acknowledgement is withheld, then the bridge shuts down — the failed
    request future maps to ``external_unknown`` (``acknowledgement_lost``),
    with 1 frame."""

    async with _scenario(
        caplog, allowlist=[{"action": "A"}], faults={"withhold_acknowledgements": True}
    ) as runs:
        call = runs.call("A")
        running = asyncio.ensure_future(runs.context.executor.invoke(call))
        await _until(lambda: len(runs.frames(call)) == 1)
        await asyncio.wait_for(runs.stop(), CLOSE_BUDGET_SECONDS)
        observation = runs.observed(await asyncio.wait_for(running, FAKE_WAIT_SECONDS))
        _outcome(
            observation, "external_unknown", ERROR_EXTERNAL_UNKNOWN, "acknowledgement_lost"
        )
        assert len(runs.frames()) == 1
        assert runs.state == "stopped"


# -- P16: subscriptions, the bounded queue and cues (R6, R7a) ---------------- #

#: The broadcaster account the cues default to, and the companion's own one.
INSTANCE_IDENTITY = {"twitch": "90001"}
COMPANION_IDENTITIES = {"twitch": "companion-7"}


def _cue_frame(**envelope: Any) -> dict[str, Any]:
    """The documented custom-event frame carrying *envelope* as its ``args``.

    The frame is the fixture's; only ``args`` changes, and ``args`` is not
    the instance's to define — it is whatever the operator's action passes
    (here, the cue envelope R7a specifies)."""

    frame = fixture_frame("custom_event")
    frame["data"]["args"] = envelope
    return frame


def _cue(cue_id: str, **fields: Any) -> dict[str, Any]:
    return _cue_frame(**{"cue_id": cue_id, "platform": "twitch", "text": "!brb", **fields})


def _published(bridge: _Bridge) -> list[dict[str, Any]]:
    """The bridge's normalised events on the bus, in order."""

    return [
        trace
        for trace in bridge.traces
        if trace["type"] == "channel.chat.message"
        and trace["metadata"].get("source") == MODULE_NAME
    ]


def _decisions(bridge: _Bridge) -> list[Mapping[str, Any]]:
    return [
        {**trace["payload"], "accepted": trace["type"] == "input.trigger.accepted"}
        for trace in bridge.traces
        if trace["type"] in ("input.trigger.accepted", "input.trigger.rejected")
        and trace["payload"].get("input") == MODULE_NAME
    ]


@contextlib.asynccontextmanager
async def _cue_bridge(**settings: Any) -> Any:
    """A bridge connected to a fake instance, owning cues on every platform."""

    async with FakeInstance() as fake:
        bridge = _Bridge()
        await bridge.start(
            url=fake.url,
            instance_identity=INSTANCE_IDENTITY,
            companion_identities=COMPANION_IDENTITIES,
            **settings,
        )
        try:
            yield bridge, fake
        finally:
            await bridge.stop()


async def _push(bridge: _Bridge, fake: FakeInstance, *frames: dict[str, Any]) -> None:
    """Push *frames*; return once each was published or dropped (the
    consumer handles them in order, one at a time)."""

    def handled() -> int:
        return len(_published(bridge)) + bridge.handle.dropped_events

    expected = handled() + len(frames)
    await fake.push_frames(list(frames))
    await _until(lambda: handled() >= expected)


async def test_ac27_owning_only_cues_subscribes_to_the_custom_category_only() -> None:
    """AC27 (R6): with the bridge owning only (``cues``, ``*``) — the
    manifest default — the one ``Subscribe`` names the custom-event category
    and nothing else; viewers need no subscription."""

    async with FakeInstance() as fake:
        bridge = _Bridge()
        await bridge.start(url=fake.url, inputs={"viewers": {"twitch": MODULE_NAME}})
        try:
            (subscribe,) = fake.requests("Subscribe")
            assert subscribe["events"] == {"Custom": ["Event"]}
            assert bridge.state == "ready"
        finally:
            await bridge.stop()


async def test_ac27_owning_twitch_chat_adds_only_twitch_chat_and_notices() -> None:
    """AC27 (R6): adding (``chat``, twitch) adds the twitch chat-message
    category and the twitch notice categories — no other platform's."""

    async with FakeInstance() as fake:
        bridge = _Bridge()
        await bridge.start(
            url=fake.url,
            inputs={"chat": {"twitch": MODULE_NAME}},
            companion_identities=COMPANION_IDENTITIES,
        )
        try:
            (subscribe,) = fake.requests("Subscribe")
            assert subscribe["events"] == {
                "Custom": ["Event"],
                "Twitch": ["ChatMessage", "Sub", "ReSub", "GiftSub", "GiftBomb", "Raid", "Follow"],
            }
        finally:
            await bridge.stop()


def test_ac27_the_categories_follow_the_owned_pairs_only() -> None:
    """The pure mapping behind the request: nothing owned, nothing named;
    cues owned on one concrete platform still name the custom category."""

    assert streamerbot.subscription_events(OwnedInputs()) == {}
    assert streamerbot.subscription_events(
        OwnedInputs(pairs=frozenset({("viewers", "kick")}))
    ) == {}
    assert streamerbot.subscription_events(
        OwnedInputs(pairs=frozenset({("cues", "kick")}))
    ) == {"Custom": ["Event"]}
    assert set(
        streamerbot.subscription_events(
            OwnedInputs(pairs=frozenset({("chat", "kick"), ("chat", "youtube")}))
        )
    ) == {"Kick", "YouTube"}


async def test_r6_a_refused_subscription_leaves_the_bridge_not_ready() -> None:
    """R6: readiness follows the subscription; an instance refusing it
    leaves the bridge degraded (``protocol_error``), never ready."""

    async with FakeInstance() as fake:
        original = fake._answer

        async def refuse_subscribe(socket: Any, frame: dict[str, Any]) -> None:
            if frame.get("request") == "Subscribe":
                reply = fixture_frame("error_response")
                reply["id"] = frame.get("id")
                await socket.send_json(reply)
                return
            await original(socket, frame)

        fake._answer = refuse_subscribe  # type: ignore[method-assign]
        bridge = _Bridge()
        await bridge.start(url=fake.url, reconnect=RECONNECT)
        try:
            assert bridge.state == "degraded" and not bridge.run_ready
            assert bridge.health()[-1] == (TRACE_MODULE_DEGRADED, "protocol_error")
            assert len(fake.requests("Subscribe")) == 1
            await asyncio.wait_for(fake.wait_for_disconnections(1), FAKE_WAIT_SECONDS)
        finally:
            await bridge.stop()


async def test_ac26_a_full_queue_drops_and_counts_and_never_stalls_the_connection(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """AC26 (R6): with a bound of 4 and 10 events delivered before the
    consumer runs, the reader drops 6 and counts them without waiting; a run
    request sent afterwards is acknowledged; once the consumer runs, the 4
    queued cues are published, and the count is a readiness detail."""

    held = asyncio.Event()

    def hold_consumer(runs: _Runs) -> None:
        runs.handle._before_consume = held.wait

    async with _scenario(
        caplog,
        allowlist=[{"action": "A"}],
        before_start=hold_consumer,
        queue_max_events=4,
        instance_identity=INSTANCE_IDENTITY,
    ) as runs:
        await runs.fake.push_frames([_cue(f"cue-{index}") for index in range(10)])
        await _until(lambda: runs.handle.dropped_events == 6)
        assert runs.handle._queue.qsize() == 4
        assert _published(runs) == []

        # The reader was never blocked: the run is acknowledged.
        _outcome(
            await runs.run(runs.call("A")),
            "external_unknown",
            ERROR_EXTERNAL_UNKNOWN,
            "execution_unconfirmed",
        )

        held.set()
        await _until(lambda: len(_published(runs)) == 4)
        assert [event["payload"]["message_id"] for event in _published(runs)] == [
            "cue-0",
            "cue-1",
            "cue-2",
            "cue-3",
        ]
        assert runs.handle.dropped_events == 6

        # The count is a readiness detail: the bridge's own next ready
        # record, after a reconnection, carries it.
        await runs.fake.close_connection()
        await _until(lambda: runs.state == "degraded")
        await _until(lambda: runs.clock.next_deadline() is not None)
        runs.clock.advance(RECONNECT["min_delay_seconds"])
        await _until(lambda: runs.state == "ready")
        assert runs.records(TRACE_MODULE_READY)[-1]["dropped_events"] == 6


async def test_ac29_a_valid_cue_is_one_normalised_event_the_default_policy_accepts() -> None:
    """AC29 (R7): a valid envelope for ``twitch`` gives exactly 1 event with
    the exact fields, decided by the policy under ``triggers.<bridge>`` —
    the module default, which accepts every cue."""

    async with _cue_bridge() as (bridge, fake):
        await _push(bridge, fake, _cue("cue-29", text="intro"))
        (event,) = _published(bridge)
        assert event["type"] == "channel.chat.message"
        assert event["metadata"] == {"source": MODULE_NAME, "schema_version": 2}
        assert event["payload"] == {
            "platform": "twitch",
            "channel_id": INSTANCE_IDENTITY["twitch"],
            "author": {"id": "instance:twitch"},
            "message_id": "cue-29",
            "text": "intro",
            "kind": "cue",
        }
        policy = bridge.registry.resolve(MODULE_NAME, INSTANCE_IDENTITY["twitch"])
        assert policy is not None
        (decision,) = _decisions(bridge)
        assert decision["accepted"] is True
        assert decision["input"] == MODULE_NAME
        assert decision["source_event_id"] == "cue-29"
        assert decision["policy_version"] == policy.version
        recorded = bridge.context.triggers.recorded(
            platform="twitch", channel_id=INSTANCE_IDENTITY["twitch"], source_event_id="cue-29"
        )
        assert recorded is not None and recorded.accepted
        assert bridge.handle.dropped_events == 0


async def test_r7_an_accepted_cue_is_admitted_to_the_shared_scheduler() -> None:
    """R7: with a shared scheduler in the context (the brain then admits
    nothing itself), an accepted cue is admitted by the bridge under its
    author's session; a refused one is not."""

    triggers = {
        MODULE_NAME: {
            "channels": {
                "chan-strict": {
                    "combination": "all_of",
                    "rules": [{"type": "keyword", "parameters": {"keywords": ["!never"]}}],
                }
            }
        }
    }
    scheduler = RecordingScheduler()
    async with FakeInstance() as fake:
        bridge = _Bridge(scheduler=scheduler)
        await bridge.start(url=fake.url, instance_identity=INSTANCE_IDENTITY, triggers=triggers)
        try:
            await _push(bridge, fake, _cue("cue-refused", channel_id="chan-strict"), _cue("cue-ok"))
            ((session_key, work),) = scheduler.admissions
            assert session_key.platform == "twitch"
            assert session_key.channel_id == INSTANCE_IDENTITY["twitch"]
            assert session_key.viewer_id == "instance:twitch"
            assert work.source_event_id == "cue-ok"
            assert work.payload["payload"]["kind"] == "cue"
        finally:
            await bridge.stop()


async def test_ac29_a_configured_policy_under_the_bridge_decides_its_cues() -> None:
    """AC29 (R7): the policy configured under ``triggers.<bridge>`` for a
    channel replaces the default there — a cue to that channel is refused
    by it (still published, as a decided fact), the default still accepts
    elsewhere; and an envelope's own channel and user are kept."""

    triggers = {
        MODULE_NAME: {
            "channels": {
                "chan-strict": {
                    "combination": "all_of",
                    "rules": [{"type": "keyword", "parameters": {"keywords": ["!never"]}}],
                }
            }
        }
    }
    async with FakeInstance() as fake:
        bridge = _Bridge()
        await bridge.start(url=fake.url, instance_identity=INSTANCE_IDENTITY, triggers=triggers)
        try:
            await _push(
                bridge,
                fake,
                _cue("cue-a", channel_id="chan-strict", user_id="555", user_name="Viewer"),
                _cue("cue-b"),
            )
            strict, default = _published(bridge)
            assert strict["payload"]["channel_id"] == "chan-strict"
            assert strict["payload"]["author"] == {"id": "555", "display_name": "Viewer"}
            assert [decision["accepted"] for decision in _decisions(bridge)] == [False, True]
            assert default["payload"]["channel_id"] == INSTANCE_IDENTITY["twitch"]
        finally:
            await bridge.stop()


async def test_ac30_a_cue_without_user_has_one_stable_author_unlike_the_companion() -> None:
    """AC30 (R7): two cues without ``user_id`` share one author id, distinct
    from every configured companion identity (case-insensitively too)."""

    async with _cue_bridge() as (bridge, fake):
        await _push(bridge, fake, _cue("cue-1"), _cue("cue-2"))
        first, second = _published(bridge)
        assert first["payload"]["author"]["id"] == second["payload"]["author"]["id"]
        author = first["payload"]["author"]["id"]
        assert author.casefold() not in {
            identity.casefold() for identity in COMPANION_IDENTITIES.values()
        }
        assert all(decision["accepted"] for decision in _decisions(bridge))


AC30_REFUSED = [
    pytest.param({"cue_id": "c", "platform": "twitch", "text": "é" * 501}, id="501-code-points"),
    pytest.param({"cue_id": "", "platform": "twitch", "text": "x"}, id="empty-cue-id"),
    pytest.param({"cue_id": "c", "text": "x"}, id="no-platform"),
]


@pytest.mark.parametrize("envelope", AC30_REFUSED)
async def test_ac30_a_malformed_or_oversized_envelope_is_dropped_and_counted(
    envelope: dict[str, Any],
) -> None:
    """AC30 (R7): 501 code points of text, an empty ``cue_id`` or no
    ``platform`` publishes 0 events and adds exactly 1 to the drop count —
    never truncated or repaired, never decided."""

    async with _cue_bridge() as (bridge, fake):
        await _push(bridge, fake, _cue_frame(**envelope))
        assert _published(bridge) == []
        assert _decisions(bridge) == []
        assert bridge.handle.dropped_events == 1


def test_r7_the_envelope_rules_at_their_bounds() -> None:
    """R7a, strictly: 500 code points and 128 characters pass; one more
    fails; a missing default channel, a lone ``user_name``, a reserved
    ``:`` in ``user_id``, a non-string text and a non-cue frame all fail."""

    identity = INSTANCE_IDENTITY
    assert streamerbot.cue_event(_cue("c", text="é" * 500), identity) is not None
    assert streamerbot.cue_event(_cue("c" * 128), identity) is not None
    assert streamerbot.cue_event(_cue("c" * 129), identity) is None
    assert streamerbot.cue_event(_cue("c", platform="kick"), identity) is None
    assert streamerbot.cue_event(_cue("c", platform="kick", channel_id="k1"), identity) is not None
    assert streamerbot.cue_event(_cue("c", user_name="Viewer"), identity) is None
    assert streamerbot.cue_event(_cue("c", user_id="1"), identity) is None
    assert streamerbot.cue_event(_cue("c", user_id="instance:twitch", user_name="X"), identity) is None
    assert streamerbot.cue_event(_cue("c", text=None), identity) is None
    assert streamerbot.cue_event(_cue_frame(), identity) is None
    assert streamerbot.cue_event(fixture_frame("custom_event"), identity) is None
    assert streamerbot.cue_event(fixture_frame("chat_twitch"), identity) is None
    # Keys the operator's action passes along besides the envelope are ignored.
    assert streamerbot.cue_event(_cue("c", scene="intro"), identity) is not None


async def test_r7_a_cue_for_a_platform_not_owned_is_dropped_by_the_bridge() -> None:
    """Plan risk (wildcard platform): a cue for a platform whose cues another
    module owns is dropped and counted by the bridge itself — the ownership
    guard never sees it — and an event with no mapping is counted too."""

    async with FakeInstance() as fake:
        bridge = _Bridge()
        await bridge.start(
            url=fake.url,
            inputs={"cues": {"kick": "none"}},
            instance_identity={"kick": "k1", **INSTANCE_IDENTITY},
        )
        try:
            await _push(bridge, fake, _cue("cue-k", platform="kick"), fixture_frame("chat_twitch"))
            assert _published(bridge) == []
            assert bridge.handle.dropped_events == 2
            snapshot = bridge.context.supervision.snapshot()
            assert snapshot[COUNTER_INPUT_OWNERSHIP_REJECTIONS] == 0
        finally:
            await bridge.stop()


async def test_r7_a_replayed_cue_id_is_neither_republished_nor_redecided() -> None:
    """Plan risk (replays): a ``cue_id`` the instance sends again inside the
    engine's dedup window reuses the recorded decision and is not published
    a second time."""

    async with _cue_bridge() as (bridge, fake):
        await _push(bridge, fake, _cue("cue-r"))
        # The replay is handled before the next cue: one consumer, in order.
        await fake.push_frames([_cue("cue-r")])
        await _push(bridge, fake, _cue("cue-s"))
        assert [event["payload"]["message_id"] for event in _published(bridge)] == [
            "cue-r",
            "cue-s",
        ]
        assert len(_decisions(bridge)) == 2
        assert bridge.handle.dropped_events == 0


def _authored_by(author_id: str, message_id: str) -> dict[str, Any]:
    """A relayed twitch chat event authored by *author_id*, as the bridge
    publishes one (the shape P17's chat mapping produces)."""

    return {
        "type": "channel.chat.message",
        "payload": {
            "platform": "twitch",
            "channel_id": INSTANCE_IDENTITY["twitch"],
            "author": {"id": author_id, "display_name": "Companion"},
            "message_id": message_id,
            "text": "Companion said this",
        },
        "metadata": {"source": MODULE_NAME, "schema_version": 2},
    }


async def test_ac33_engine_half_an_event_authored_by_the_companion_is_refused() -> None:
    """AC33 (R7, engine half; D6): owning (``chat``, twitch) with
    ``companion_identities.twitch`` set, the bridge hands the identity to the
    trigger engine at activation. An event it relays authored by that
    identity — whatever its case — is rejected by the engine's anti-echo
    invariant on the bridge's own evaluate → publish path, while a viewer's
    is accepted. (P17 feeds the chat-mapped instance event through the same
    path.)"""

    async with _cue_bridge(inputs={"chat": {"twitch": MODULE_NAME}}) as (bridge, fake):
        await _push(
            bridge,
            fake,
            _cue("cue-echo", user_id="COMPANION-7", user_name="Companion"),
            _cue("cue-viewer", user_id="555", user_name="Viewer"),
        )
        assert [
            (decision["source_event_id"], decision["accepted"], decision["reason"])
            for decision in _decisions(bridge)
        ][0] == ("cue-echo", False, REASON_SELF_AUTHORED)
        assert _decisions(bridge)[1]["accepted"] is True

        echo = bridge.context.triggers.evaluate(
            _authored_by(COMPANION_IDENTITIES["twitch"], "chat-echo")
        )
        assert not echo.accepted and echo.reason == REASON_SELF_AUTHORED


async def test_ac33_the_refusal_comes_from_the_identities_the_bridge_hands_over() -> None:
    """The control for AC33: a bridge configured without companion
    identities leaves the engine accepting what the configured one refuses."""

    async with FakeInstance() as fake:
        bridge = _Bridge()
        await bridge.start(url=fake.url, instance_identity=INSTANCE_IDENTITY)
        try:
            await _push(bridge, fake, _cue("cue-echo", user_id="companion-7", user_name="C"))
            ((_, accepted, reason),) = [
                (d["source_event_id"], d["accepted"], d["reason"]) for d in _decisions(bridge)
            ]
            assert accepted and reason != REASON_SELF_AUTHORED
            decision = bridge.context.triggers.evaluate(
                _authored_by(COMPANION_IDENTITIES["twitch"], "chat-echo")
            )
            assert decision.reason != REASON_SELF_AUTHORED
        finally:
            await bridge.stop()


# -- P17: chat mapping for owned platforms and role trust (R7b) -------------- #

#: The broadcaster accounts the bridge falls back to, and the companions.
CHAT_INSTANCE_IDENTITY = {"twitch": "90001", "kick": "k-broadcaster", "youtube": "UC-broadcaster"}
CHAT_COMPANIONS = {"twitch": "companion-7", "kick": "companion-k", "youtube": "UC-companion"}
CHAT_FIXTURES = {
    platform: (f"chat_{platform}", f"notices_{platform}")
    for platform in ("twitch", "kick", "youtube")
}
#: A type the events index (ledger id 6) documents for each platform that has
#: no native notice kind (fixtures README, "left out on purpose").
UNDOCUMENTED_TYPES = {"twitch": "Cheer", "kick": "KicksGifted", "youtube": "JewelsGifted"}


def _native_twitch() -> dict[str, dict[str, Any]]:
    """The twitch module's own events, per kind, from its own test payloads."""

    kinds = frozenset(twitch_module.NOTICE_KINDS)
    envelopes = {
        "message": native_twitch.notification("n-message"),
        **{
            kind: native_twitch.chat_notice(f"n-{kind}", kind)
            for kind in ("sub", "resub", "sub_gift", "community_sub_gift")
        },
        "raid": native_twitch.raid_notice("n-raid"),
        "follow": native_twitch.follow_notice("n-follow"),
    }
    return {
        kind: twitch_module._normalize_notification(envelope, kinds).event()
        for kind, envelope in envelopes.items()
    }


def _native_kick() -> dict[str, dict[str, Any]]:
    kinds = frozenset(kick_module.NOTICE_KINDS)
    channel = native_kick.person(int(native_kick.CHANNEL), "streamer")
    deliveries = [(kick_module.EVENT_CHAT_MESSAGE, native_kick.chat_body("hello"))] + [
        (event_type, {"broadcaster": channel, **fields})
        for event_type, _kind, fields in native_kick.NOTICE_CASES
    ]
    events = [
        kick_module.normalize_delivery(event_type, body, f"d-{event_type}", kinds).event()
        for event_type, body in deliveries
    ]
    return {event["payload"].get("kind", "message"): event for event in events}


def _native_youtube() -> dict[str, dict[str, Any]]:
    kinds = frozenset(youtube_module.NOTICE_KINDS)
    items = [native_youtube.chat_item("y-message")] + [
        native_youtube.chat_item(f"y-{item_type}", "", item_type=item_type, details=details)
        for item_type, _kind, details in native_youtube.NOTICE_CASES
    ]
    events = [
        youtube_module.normalize_item(item, native_youtube.CHANNEL, kinds).event()
        for item in items
    ]
    return {event["payload"].get("kind", "message"): event for event in events}


NATIVE_EVENTS = {"twitch": _native_twitch, "kick": _native_kick, "youtube": _native_youtube}


def _chat_frames(platform: str) -> list[dict[str, Any]]:
    """Every documented chat and notice frame of *platform*, chat first."""

    return [
        copy.deepcopy(entry["frame"])
        for name in CHAT_FIXTURES[platform]
        for entry in fixture_entries(name)
    ]


def _retyped(platform: str, event_type: str) -> dict[str, Any]:
    """A documented notice frame of *platform* naming *event_type* instead.

    Only ``event.type`` changes, to a type the events index documents with no
    mapping here; the body stays the fixture's, so the frame claims nothing
    about the instance the fixtures do not (the drop happens on the type)."""

    frame = fixture_frame(CHAT_FIXTURES[platform][1])
    frame["event"]["type"] = event_type
    return frame


def _shape(event: Mapping[str, Any]) -> tuple[str | None, frozenset[str]]:
    payload = event["payload"]
    return payload.get("kind"), frozenset(payload)


@contextlib.asynccontextmanager
async def _chat_bridge(owned: tuple[str, ...], **settings: Any) -> Any:
    """A bridge connected to a fake instance, owning (``chat``, P) for each
    P in *owned*, with every companion and broadcaster identity set."""

    async with FakeInstance() as fake:
        bridge = _Bridge()
        await bridge.start(
            url=fake.url,
            inputs={"chat": {platform: MODULE_NAME for platform in owned}},
            instance_identity=CHAT_INSTANCE_IDENTITY,
            companion_identities=CHAT_COMPANIONS,
            **settings,
        )
        try:
            yield bridge, fake
        finally:
            await bridge.stop()


def test_ac43_the_table_is_the_subscribed_list_and_covers_every_native_kind() -> None:
    """R7b: the mapping table is keyed by (platform, documented event type)
    and is exactly what the subscription names; per platform, its kinds are
    ``message`` plus every notice kind the native module produces."""

    table = streamerbot._CHAT_MAPPINGS
    assert set(table) == {
        (platform, event_type)
        for platform, types in streamerbot._CHAT_EVENT_TYPES.items()
        for event_type in types
    }
    natives = {
        "twitch": twitch_module.NOTICE_KINDS,
        "kick": kick_module.NOTICE_KINDS,
        "youtube": youtube_module.NOTICE_KINDS,
    }
    for platform, notice_kinds in natives.items():
        kinds = {mapping.kind for (owner, _), mapping in table.items() if owner == platform}
        assert kinds == {"message", *notice_kinds}, platform
        assert set(NATIVE_EVENTS[platform]()) == kinds, platform


@pytest.mark.parametrize("platform", ["twitch", "kick", "youtube"])
def test_ac43_each_fixture_maps_to_the_native_kind_and_key_set(platform: str) -> None:
    """AC43, the mapping alone: each documented chat and notice fixture of P
    gives one event whose ``kind`` and payload key set equal those P's native
    module produces for the equivalent input — the native shape computed by
    the native normaliser on its own test payload — and the author carries
    the native author keys; with no role trust, no ``roles``."""

    native = NATIVE_EVENTS[platform]()
    frames = _chat_frames(platform)
    assert len(frames) == len(native) + (1 if platform == "youtube" else 0)
    for frame in frames:
        mapped = streamerbot.chat_event(frame, CHAT_INSTANCE_IDENTITY)
        assert mapped is not None, frame["event"]
        event, claims = mapped
        kind = event["payload"].get("kind", "message")
        assert _shape(event) == _shape(native[kind]), frame["event"]
        assert event["type"] == native[kind]["type"] == "channel.chat.message"
        assert event["metadata"] == {"source": MODULE_NAME, "schema_version": 2}
        assert event["payload"]["platform"] == platform
        assert set(event["payload"]["author"]) == {"id", "display_name"}
        assert claims == ()


@pytest.mark.parametrize("platform", ["twitch", "kick", "youtube"])
async def test_ac43_owning_chat_each_fixture_gives_one_event_and_unmapped_is_dropped(
    platform: str,
) -> None:
    """AC43 (R7b) through the bridge: owning (``chat``, P), every documented
    chat and notice frame of P is published as exactly 1 event with the
    native kind and key set; a notice of a type with no documented mapping
    publishes 0 events and adds exactly 1 to the drop count."""

    native = NATIVE_EVENTS[platform]()
    frames = _chat_frames(platform)
    async with _chat_bridge((platform,)) as (bridge, fake):
        for index, frame in enumerate(frames):
            await _push(bridge, fake, frame)
            assert len(_published(bridge)) == index + 1, frame["event"]
        assert bridge.handle.dropped_events == 0
        for event in _published(bridge):
            kind = event["payload"].get("kind", "message")
            assert _shape(event) == _shape(native[kind])
        # Every published event was decided by the engine, under this input.
        assert len(_decisions(bridge)) == len(frames)

        await _push(bridge, fake, _retyped(platform, UNDOCUMENTED_TYPES[platform]))
        assert len(_published(bridge)) == len(frames)
        assert bridge.handle.dropped_events == 1


async def test_ac43_owning_only_twitch_the_kick_and_youtube_fixtures_give_nothing() -> None:
    """AC43 (R7b): owning only (``chat``, twitch), every kick and youtube
    chat and notice frame publishes 0 events and is counted as dropped by
    the bridge itself — the ownership guard never sees one."""

    others = _chat_frames("kick") + _chat_frames("youtube")
    async with _chat_bridge(("twitch",)) as (bridge, fake):
        await _push(bridge, fake, *others)
        assert _published(bridge) == []
        assert _decisions(bridge) == []
        assert bridge.handle.dropped_events == len(others)
        await _push(bridge, fake, fixture_frame("chat_twitch"))
        assert len(_published(bridge)) == 1
        snapshot = bridge.context.supervision.snapshot()
        assert snapshot[COUNTER_INPUT_OWNERSHIP_REJECTIONS] == 0


def test_r7b_a_notice_lacking_a_field_its_mapping_needs_is_not_mapped() -> None:
    """R7b, by rule: no author, an author id holding ``:``, no chat text,
    no event id where one is documented, no channel at all, an anonymous
    gift — none is repaired; an event without a documented id gets a stable
    one derived from its frame."""

    identity = CHAT_INSTANCE_IDENTITY
    no_user = fixture_frame("chat_twitch")
    del no_user["data"]["user"]
    reserved = fixture_frame("chat_twitch")
    reserved["data"]["user"]["id"] = "instance:twitch"
    no_text = fixture_frame("chat_youtube")
    del no_text["data"]["message"]
    no_id = fixture_frame("notices_youtube", 0)
    del no_id["data"]["eventId"]
    anonymous = fixture_frame("notices_kick", 3)
    anonymous["data"]["isAnonymous"] = True
    no_system_message = fixture_frame("notices_twitch", 0)
    del no_system_message["data"]["systemMessage"]
    for frame in (no_user, reserved, no_text, no_id, anonymous, no_system_message):
        assert streamerbot.chat_event(frame, identity) is None
    # A youtube notice's comment is the viewer's optional text: absent, empty.
    for index in (1, 3):
        uncommented = fixture_frame("notices_youtube", index)
        del uncommented["data"]["message"]
        event, _ = streamerbot.chat_event(uncommented, identity)
        assert event["payload"]["text"] == ""
    # Kick events carry no channel: without the instance identity, no event.
    assert streamerbot.chat_event(fixture_frame("chat_kick"), {}) is None
    assert streamerbot.chat_event(fixture_frame("custom_event"), identity) is None

    raid = fixture_frame("notices_twitch", 4)
    first, _ = streamerbot.chat_event(raid, identity)
    again, _ = streamerbot.chat_event(fixture_frame("notices_twitch", 4), identity)
    assert first["payload"]["message_id"] == again["payload"]["message_id"]
    assert first["payload"]["channel_id"] == identity["twitch"]
    follow, _ = streamerbot.chat_event(fixture_frame("notices_twitch", 5), identity)
    assert follow["payload"]["message_id"] != first["payload"]["message_id"]
    assert ":" not in first["payload"]["message_id"]


async def test_r7b_a_replayed_event_without_an_id_is_not_published_twice() -> None:
    """R7b: the derived id of a frame sent twice is the same, so the
    engine's dedup window keeps the replay from a second publication."""

    async with _chat_bridge(("twitch",)) as (bridge, fake):
        await _push(bridge, fake, fixture_frame("notices_twitch", 4))
        await fake.push_frames([fixture_frame("notices_twitch", 4)])
        await _push(bridge, fake, fixture_frame("chat_twitch"))
        assert [event["payload"].get("kind") for event in _published(bridge)] == [
            "raid",
            None,
        ]
        assert bridge.handle.dropped_events == 0


async def test_r7b_mapped_chat_feeds_the_shared_transcript_once() -> None:
    """R7b: owning (``chat``, twitch), the bridge feeds the shared chat
    transcript the native input no longer feeds — a chat message and a
    notice, each once, a replay not again."""

    async with _chat_bridge(("twitch",)) as (bridge, fake):
        chat = fixture_frame("chat_twitch")
        await _push(bridge, fake, chat, fixture_frame("notices_twitch", 0))
        await fake.push_frames([fixture_frame("chat_twitch")])
        await _push(bridge, fake, fixture_frame("notices_twitch", 5))
        records = bridge.context.chat.read(
            "twitch", CHAT_INSTANCE_IDENTITY["twitch"], limit=10
        )
        published = _published(bridge)
        assert len(published) == 3
        assert [(r.author_id, r.message_id, r.text) for r in records] == [
            (
                event["payload"]["author"]["id"],
                event["payload"]["message_id"],
                event["payload"]["text"],
            )
            for event in published
        ]
        assert records[0].text == chat["data"]["text"]


AUDIENCE_MODERATORS = {
    MODULE_NAME: {
        "channels": {
            CHAT_INSTANCE_IDENTITY["twitch"]: {
                "combination": "all_of",
                "rules": [{"type": "audience", "parameters": {"audience": "moderators"}}],
            }
        }
    }
}


@pytest.mark.parametrize("trusted", [False, True])
async def test_ac32_instance_roles_satisfy_an_audience_only_when_trusted(
    trusted: bool,
) -> None:
    """AC32 (R7b): owning (``chat``, twitch), the twitch chat fixture —
    author with a ``moderator`` badge — maps to the native key set (no
    ``kind``); an ``audience: moderators`` rule under ``triggers.<bridge>``
    is unmet with ``trust_instance_roles`` false (no ``roles`` key, no
    claim) and met with it true (``roles_provenance`` names the bridge)."""

    settings: dict[str, Any] = {"triggers": AUDIENCE_MODERATORS}
    if trusted:
        settings["trust_instance_roles"] = True
    async with _chat_bridge(("twitch",), **settings) as (bridge, fake):
        await _push(bridge, fake, fixture_frame("chat_twitch"))
        (event,) = _published(bridge)
        assert _shape(event) == _shape(_native_twitch()["message"])
        assert "kind" not in event["payload"]
        author = event["payload"]["author"]
        (decision,) = _decisions(bridge)
        if trusted:
            assert author["roles"] == ["moderator"]
            assert author["roles_provenance"] == MODULE_NAME
            assert decision["accepted"] is True
            assert decision["reason"] == "accepted:audience_moderators"
        else:
            assert set(author) == {"id", "display_name"}
            assert decision["accepted"] is False
            assert decision["reason"] == "rejected:audience_moderators_untrusted"


def test_r7b_trusted_roles_read_badges_and_flags_with_the_bridge_provenance() -> None:
    """R7b: under trust, twitch reads the badge list and youtube the user
    flags, each claim tagged with the bridge's module; the broadcaster is
    recognised by identity; a user with no role source carries no roles."""

    identity = CHAT_INSTANCE_IDENTITY
    event, claims = streamerbot.chat_event(
        fixture_frame("chat_twitch"), identity, trust_roles=True
    )
    assert event["payload"]["author"]["roles"] == ["moderator"]
    assert [(claim.name, claim.provenance) for claim in claims] == [("moderator", MODULE_NAME)]

    sponsor, claims = streamerbot.chat_event(
        fixture_frame("notices_youtube", 0), identity, trust_roles=True
    )
    assert sponsor["payload"]["author"]["roles"] == ["subscription"]
    assert sponsor["payload"]["author"]["roles_provenance"] == MODULE_NAME

    raid, claims = streamerbot.chat_event(
        fixture_frame("notices_twitch", 4), identity, trust_roles=True
    )
    assert set(raid["payload"]["author"]) == {"id", "display_name"} and claims == ()

    broadcaster = fixture_frame("chat_twitch")
    broadcaster["data"]["user"]["id"] = identity["twitch"]
    _, claims = streamerbot.chat_event(broadcaster, identity, trust_roles=True)
    assert [claim.name for claim in claims] == ["broadcaster", "moderator"]
    # Untrusted, the same frames carry no claim at all.
    _, claims = streamerbot.chat_event(broadcaster, identity)
    assert claims == ()


def _bridge_profile(tmp_path: Path, **settings: Any) -> Path:
    """A profile enabling the bridge alone, owning (``chat``, twitch), with
    the shipped example's ``limits`` (so the runtime has a trigger engine)."""

    shipped = yaml.safe_load((ROOT / "config.yaml.example").read_text(encoding="utf-8"))
    profile = {
        "modules_directory": application.MODULES_DIRECTORY_BUILTIN,
        "limits": shipped["limits"],
        "enabled_modules": [MODULE_NAME],
        "modules": {MODULE_NAME: {**BASE_SETTINGS, **settings}},
        "inputs": {"chat": {"twitch": MODULE_NAME}},
    }
    path = tmp_path / "profile.yaml"
    path.write_text(yaml.safe_dump(profile), encoding="utf-8")
    return path


async def test_ac33_check_config_refuses_owned_twitch_chat_without_an_identity(
    tmp_path: Path,
) -> None:
    """AC33 (R7b), through ``--check-config``: a profile handing the bridge
    (``chat``, twitch) with no ``companion_identities.twitch`` fails with a
    diagnostic naming ``twitch``; with the identity set, it is accepted."""

    diagnostics: list[str] = []
    status = await application.check_config(
        _bridge_profile(tmp_path), environ={}, diagnostic_reporter=diagnostics.append
    )
    assert status == 2
    assert len(diagnostics) == 1 and "twitch" in diagnostics[0]
    assert "companion_identities.twitch" in diagnostics[0]

    diagnostics.clear()
    status = await application.check_config(
        _bridge_profile(tmp_path, companion_identities={"twitch": "1002"}),
        environ={},
        diagnostic_reporter=diagnostics.append,
    )
    assert (status, diagnostics) == (0, [])


async def test_ac33_a_mapped_chat_event_authored_by_the_companion_is_refused() -> None:
    """AC33 (R7b, engine half): with ``companion_identities.twitch`` equal to
    the twitch chat fixture's author, the mapped instance chat event is
    rejected by the engine's anti-echo invariant; with another identity, the
    same event is not refused for that reason."""

    author = fixture_frame("chat_twitch")["data"]["user"]["id"]
    async with FakeInstance() as fake:
        bridge = _Bridge()
        await bridge.start(
            url=fake.url,
            inputs={"chat": {"twitch": MODULE_NAME}},
            instance_identity=CHAT_INSTANCE_IDENTITY,
            companion_identities={"twitch": author},
        )
        try:
            await _push(bridge, fake, fixture_frame("chat_twitch"))
            (event,) = _published(bridge)
            assert event["payload"]["author"]["id"] == author
            (decision,) = _decisions(bridge)
            assert decision["accepted"] is False
            assert decision["reason"] == REASON_SELF_AUTHORED
        finally:
            await bridge.stop()

    async with _chat_bridge(("twitch",)) as (bridge, fake):
        await _push(bridge, fake, fixture_frame("chat_twitch"))
        (decision,) = _decisions(bridge)
        assert decision["reason"] != REASON_SELF_AUTHORED
