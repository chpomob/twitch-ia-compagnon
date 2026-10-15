"""Contract tests for the automation bridge's manifest and settings hook.

Phase 5, plan step P12: the 18th shipped manifest, its run action's declared
contract (R4, AC15), its settings schema without any ``required`` key (R1,
AC5), the value-free settings hook (R6, AC22; R5, AC45 first half; R7, AC33
first half), the package-data line and unchanged dependencies (R10, AC41),
and the vendor-free kernel (AC11, re-run). Nothing here opens a socket.
"""

from __future__ import annotations

import math
import re
import sys
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised on the 3.10 floor only
    import tomli as tomllib

import pytest
import yaml

from core.actions import ERROR_PROVIDER_NOT_READY, AuthorizationRule
from core.contracts import (
    ActionCall,
    ContractError,
    Destination,
    validate_against_schema,
)
from core.lifecycle import PhaseCoordinator
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
