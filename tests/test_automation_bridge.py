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
"""

from __future__ import annotations

import asyncio
import base64
import copy
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
from core.actions import ERROR_PROVIDER_NOT_READY, AuthorizationRule
from core.contracts import (
    TRACE_MODULE_DEGRADED,
    TRACE_MODULE_READY,
    ActionCall,
    ContractError,
    Destination,
    validate_against_schema,
)
from core.lifecycle import DEFAULT_HOOK_TIMEOUT_SECONDS, PhaseCoordinator
from core.loader import ModuleLoader, _validate_manifest
from core.runtime import RUNTIME_API, OwnedInputs
from core.triggers import TriggerRegistry
from conftest import ManualClock, runtime_context

from modules import streamerbot
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


async def test_the_placeholder_provider_answers_provider_not_ready() -> None:
    class _Invocation:
        call = _run_call({"action": "A"})
        emitted: bool | None = None

        def mark_not_emitted(self) -> None:
            self.emitted = False

    invocation = _Invocation()
    observation = await streamerbot._RunProvider().invoke(invocation)
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

    def __init__(self) -> None:
        self.clock = ManualClock()
        self.context = runtime_context(clock=self.clock, trigger_registry=TriggerRegistry())
        self.traces: list[dict[str, Any]] = []
        self.context.bus.subscribe("**", self.traces.append, order=100)
        self.delays: list[float] = []
        self.diagnostics: list[str] = []
        self.coordinator: PhaseCoordinator | None = None
        self.handle: Any = None

    async def sleeper(self, delay: float) -> None:
        self.delays.append(delay)
        await self.clock.sleep(delay)

    async def activate(self, **settings: Any) -> None:
        loader = ModuleLoader(self.context.bus, ROOT / "modules", context=self.context, environ={})
        activations = await loader.activate_enabled(
            {
                "enabled_modules": [MODULE_NAME],
                "modules": {
                    MODULE_NAME: {**BASE_SETTINGS, "_sleeper": self.sleeper, **settings}
                },
            }
        )
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
    it is read; the inventory is kept by name, with id, group and enabled."""

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
    with a password configured; ready once the inventory is read."""

    async with FakeInstance() as fake:
        bridge = _Bridge()
        await bridge.start(url=fake.url, password=PASSWORD)
        try:
            assert bridge.run_ready and bridge.state == "ready"
            assert [frame["request"] for frame in fake.received] == ["GetActions"]
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
