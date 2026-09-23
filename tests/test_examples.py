"""The three shipped profiles and the eleven shipped manifests (R1, R5, R6, R7;
phase 2: R8, R9).

The profiles are ``config.yaml.example`` (PC: chat input, ``chat_context``,
``users``, local ``capture``, ``audio_input``, ``audio_output``,
``stream_control``, brain, audit), ``config.server.yaml.example`` (server:
the chat input, ``chat_context``, ``users``, ``stream_control`` for the
platform polls, brain, audit, plus ``proxy`` lending the five device actions
from a paired agent) and ``agent.yaml.example`` (the PC agent: the four
device modules and ``agent_link``) — the module lists and provided-action
sets of phase 2's R9, which replace phase 1's (allowlisted). Every one is
loaded through the same path ``core.main``
uses — never a permissive parser — and the manifests are checked as the
versioned runtime reads them: every one is v2, declares the runtime contract
it is built against, its lifecycle roles, its settings schema and the hook
its package implements. The former whole-manifest equality (allowlisted; R7)
is replaced by this shape check: the routing keys stay exact, the contract
declarations are asserted for presence and coherence, and their detail
belongs to each module's own suite.

What every profile must satisfy (R7, AC42, AC58): its rules grant exactly
its provided-action set — the actions the enabled manifests declare, plus
the ones an enabled ``proxy`` allowlists — every credential is a ``${NAME}``
reference, the brain profiles carry R3's acted budgets and the single fixed
``chat.write`` delivery entry, and every module-owned settings validator
accepts its section. Phase 2 adds (R8, R9; AC30, AC32, AC43): a credential is
a ``${NAME}`` reference or, for the optional speech and transcription keys
shipped unconfigured, empty — never a literal — and the chat-only profile
(the PC profile reduced to the six phase 1 modules) starts, runs the phase 1
scenario and offers exactly the phase 1 actions. The profile suite
(``tests/test_profiles.py``) adds the ``--check-config``, degraded-startup
and installation checks (AC40, AC41, AC43; AC31, AC32, AC33, AC43).
"""

from __future__ import annotations

import importlib
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import yaml

import core.main as application
from core.contracts import (
    TRACE_BRAIN_RUN_COMPLETED,
    TRACE_CHANNEL_CHAT_SENT,
    TRACE_MODULE_DEGRADED,
    Destination,
)
from core.lifecycle import ROLE_INPUT, ROLE_OBSERVATION
from core.loader import ModuleLoader
from core.main import LIMITS_KEY, load_config
from core.runtime import RUNTIME_API
from conftest import (
    FakeAudioSource,
    FakeCaptureSource,
    FakeResponse,
    FakeSession,
    ManualClock,
    RecordingPlayerRunner,
    ScriptedModel,
    ScriptedSpeechTransport,
    ScriptedTranscriptionTransport,
    events_of,
    final,
    tool_call,
    wait_until,
    wav_bytes,
)
from modules.capture import SCREEN_CAPTURE_PROVIDER
from modules.proxy import PROVIDER_NAME as PROXY_PROVIDER
from test_integration import (
    FakeTwitchSession,
    FakeWebSocket,
    notification,
    sent_response,
    welcome,
)
from test_stream_control import ScenePeer


ROOT = Path(__file__).parents[1]
PC_PROFILE = ROOT / "config.yaml.example"
SERVER_PROFILE = ROOT / "config.server.yaml.example"
AGENT_PROFILE = ROOT / "agent.yaml.example"
PROFILES = {"pc": PC_PROFILE, "server": SERVER_PROFILE, "agent": AGENT_PROFILE}
#: The profiles that run a brain, hence a chat input and a delivery list.
BRAIN_PROFILES = ("pc", "server")
EXAMPLE_PATH = PC_PROFILE
MODULE_NAMES = (
    "twitch",
    "brain",
    "audit",
    "chat_context",
    "users",
    "capture",
    "proxy",
    "agent_link",
    "audio_output",
    "audio_input",
    "stream_control",
    "clips",
    "viewer_memory",
    "moderation",
    "watch",
    "kick",
)
ENV_REFERENCE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}\Z")

#: The six modules of phase 1's PC profile: the chat-only profile (R8, AC30).
PHASE1_MODULES = ["twitch", "chat_context", "users", "capture", "brain", "audit"]
#: The three optional phase 2 device modules (R8).
PHASE2_MODULES = ("audio_input", "audio_output", "stream_control")
#: The enabled set of each profile, in activation order (R9, replacing R7's).
ENABLED_MODULES = {
    "pc": [
        "twitch",
        "chat_context",
        "users",
        "capture",
        "audio_input",
        "audio_output",
        "stream_control",
        "brain",
        "audit",
    ],
    "server": ["twitch", "chat_context", "users", "stream_control", "brain", "audit", "proxy"],
    "agent": ["capture", "audio_input", "audio_output", "stream_control", "agent_link"],
}
#: The phase 1 actions, and the five device actions phase 2 adds (R9).
PHASE1_ACTIONS = {"chat.read", "users.read", "screen.capture", "chat.write"}
#: The phase 1 read actions: the only tools a chat-only brain is offered.
PHASE1_READ_ACTIONS = {"chat.read", "users.read", "screen.capture"}
DEVICE_ACTIONS = {
    "screen.capture",
    "audio.capture",
    "audio.speak",
    "audio.play",
    "stream.scene.set",
}
PHASE2_ACTIONS = {
    "audio.capture",
    "audio.speak",
    "audio.play",
    "stream.scene.set",
    "stream.poll.create",
}
#: The provided-action set of each profile, as R9 lists it (AC32: 9, 9, 5).
PROVIDED_ACTIONS = {
    "pc": PHASE1_ACTIONS | PHASE2_ACTIONS,
    "server": PHASE1_ACTIONS | PHASE2_ACTIONS,
    "agent": set(DEVICE_ACTIONS),
}
#: The server's ``proxy`` allowlist, in R9's order.
SERVER_PROXY_ACTIONS = [
    "screen.capture",
    "audio.capture",
    "audio.speak",
    "audio.play",
    "stream.scene.set",
]
#: The scope each action is addressed on, its nature and its permission, as
#: the manifests declare them (R5): the grant of each profile follows them.
ACTION_SCOPES = {
    "chat.read": "chat",
    "users.read": "chat",
    "chat.write": "chat",
    "screen.capture": "capture",
    "audio.capture": "audio",
    "audio.speak": "audio",
    "audio.play": "audio",
    "stream.scene.set": "stream",
    "stream.poll.create": "poll",
}
READ_ACTIONS = {"chat.read", "users.read", "screen.capture", "audio.capture"}
ACTION_PERMISSIONS = {
    **{name: name for name in ACTION_SCOPES},
    "stream.scene.set": "stream.scene",
    "stream.poll.create": "stream.poll",
}
#: Which provider serves ``screen.capture`` in each profile (decision 7).
SCREEN_CAPTURE_PROVIDERS = {
    "pc": SCREEN_CAPTURE_PROVIDER,
    "server": PROXY_PROVIDER,
    "agent": SCREEN_CAPTURE_PROVIDER,
}
#: R3's acted defaults, in the order AC42 lists them: the eight ``budget``
#: fields, then ``admission.wait_seconds`` and ``admission.total_run_seconds``.
ACTED_BUDGETS = (5, 30, 10, 8192, 5242880, 6, 2, 10, 30, 60)
BUDGET_FIELDS = (
    "model_turns",
    "model_call_seconds",
    "action_seconds",
    "max_tokens",
    "max_observation_bytes",
    "max_action_calls",
    "max_repeated_actions",
    "delivery_reserve_seconds",
)
#: The delivery group both brain profiles carry (AC58, decision 1).
FIXED_DELIVERY = {
    "mode": "fixed",
    "actions": [{"action": "chat.write", "text_argument": "text"}],
}

# What every v2 manifest declares exactly: the routing keys of v1, the
# version and runtime contract, the lifecycle roles the coordinator reads
# instead of the module name (R4), and the name of the settings hook the
# package implements (R7). The contract declarations beside them — the
# settings schema, the chat input's triggers, the actions of the input and
# of the three capability modules — are asserted for presence here and in
# detail by the module's own suite.
EXPECTED_MANIFESTS = {
    "twitch": {
        "name": "twitch",
        "manifest_version": 2,
        "runtime_api": RUNTIME_API,
        "produces": ["channel.chat.message"],
        "consumes": ["channel.chat.send"],
        "middleware": False,
        "lifecycle": {"roles": [ROLE_INPUT]},
        "settings_validator": "validate_settings",
    },
    "brain": {
        "name": "brain",
        "manifest_version": 2,
        "runtime_api": RUNTIME_API,
        "produces": ["channel.chat.send"],
        "consumes": ["channel.chat.message"],
        "middleware": False,
        "lifecycle": {"roles": []},
        "settings_validator": "validate_settings",
    },
    "audit": {
        "name": "audit",
        "manifest_version": 2,
        "runtime_api": RUNTIME_API,
        "produces": [],
        "consumes": ["**"],
        "middleware": True,
        "order": 90,
        "lifecycle": {"roles": [ROLE_OBSERVATION]},
        "settings_validator": "validate_settings",
    },
    "chat_context": {
        "name": "chat_context",
        "manifest_version": 2,
        "runtime_api": RUNTIME_API,
        "produces": [],
        "consumes": [],
        "middleware": False,
        "lifecycle": {"roles": []},
        "settings_validator": "validate_settings",
    },
    "users": {
        "name": "users",
        "manifest_version": 2,
        "runtime_api": RUNTIME_API,
        "produces": [],
        "consumes": ["channel.chat.message"],
        "middleware": False,
        "lifecycle": {"roles": []},
        "settings_validator": "validate_settings",
    },
    "capture": {
        "name": "capture",
        "manifest_version": 2,
        "runtime_api": RUNTIME_API,
        "produces": [],
        "consumes": [],
        "middleware": False,
        "lifecycle": {"roles": []},
        "settings_validator": "validate_settings",
    },
    "proxy": {
        "name": "proxy",
        "manifest_version": 2,
        "runtime_api": RUNTIME_API,
        "produces": ["agent.status"],
        "consumes": [],
        "middleware": False,
        "lifecycle": {"roles": [ROLE_INPUT]},
        "settings_validator": "validate_settings",
    },
    "agent_link": {
        "name": "agent_link",
        "manifest_version": 2,
        "runtime_api": RUNTIME_API,
        "produces": [],
        "consumes": [],
        "middleware": False,
        "lifecycle": {"roles": [ROLE_INPUT]},
        "settings_validator": "validate_settings",
    },
    "audio_output": {
        "name": "audio_output",
        "manifest_version": 2,
        "runtime_api": RUNTIME_API,
        "produces": [],
        "consumes": [],
        "middleware": False,
        "lifecycle": {"roles": []},
        "settings_validator": "validate_settings",
    },
    "audio_input": {
        "name": "audio_input",
        "manifest_version": 2,
        "runtime_api": RUNTIME_API,
        "produces": [],
        "consumes": [],
        "middleware": False,
        "lifecycle": {"roles": []},
        "settings_validator": "validate_settings",
    },
    "stream_control": {
        "name": "stream_control",
        "manifest_version": 2,
        "runtime_api": RUNTIME_API,
        "produces": [],
        "consumes": [],
        "middleware": False,
        "lifecycle": {"roles": []},
        "settings_validator": "validate_settings",
    },
    "clips": {
        "name": "clips",
        "manifest_version": 2,
        "runtime_api": RUNTIME_API,
        "produces": [],
        "consumes": [],
        "middleware": False,
        "lifecycle": {"roles": []},
        "settings_validator": "validate_settings",
    },
    "viewer_memory": {
        "name": "viewer_memory",
        "manifest_version": 2,
        "runtime_api": RUNTIME_API,
        "produces": [],
        "consumes": ["channel.chat.message"],
        "middleware": False,
        "lifecycle": {"roles": ["input"]},
        "settings_validator": "validate_settings",
    },
    "moderation": {
        "name": "moderation",
        "manifest_version": 2,
        "runtime_api": RUNTIME_API,
        "produces": [],
        "consumes": ["channel.chat.message"],
        "middleware": False,
        "lifecycle": {"roles": []},
        "settings_validator": "validate_settings",
    },
    "watch": {
        "name": "watch",
        "manifest_version": 2,
        "runtime_api": RUNTIME_API,
        "produces": [],
        "consumes": ["channel.chat.message"],
        "middleware": False,
        "lifecycle": {"roles": ["input"]},
        "settings_validator": "validate_settings",
    },
    "kick": {
        "name": "kick",
        "manifest_version": 2,
        "runtime_api": RUNTIME_API,
        "produces": ["channel.chat.message"],
        "consumes": [],
        "middleware": False,
        "lifecycle": {"roles": ["input"]},
        "settings_validator": "validate_settings",
    },
}
# The declarations each manifest carries beyond the routing keys (R1, R5,
# R8): only the chat input — and, from phase 3's R6, the watch input —
# declares triggers; the chat input and the six
# capability modules declare their actions; the two transport modules
# declare a credential and no action (R7: the proxy binds allowlisted
# actions from the catalog, it declares none of its own).
DECLARATION_KEYS = {
    "twitch": {"triggers", "actions", "credentials"},
    "brain": {"credentials"},
    "audit": set(),
    "chat_context": {"actions", "credentials"},
    "users": {"actions", "credentials"},
    "capture": {"actions", "credentials"},
    "proxy": {"credentials"},
    "agent_link": {"credentials"},
    "audio_output": {"actions", "credentials"},
    "audio_input": {"actions", "credentials"},
    "stream_control": {"actions", "credentials"},
    "clips": {"actions"},
    # Phase 3 R4 (plan P11): the store declares its recall and record.
    "viewer_memory": {"actions"},
    # Phase 3 R5 (plan P14): the one model-proposable write.
    "moderation": {"actions"},
    # Phase 3 R6 (plan P16): the watch input declares its `event_kind`
    # trigger type and no action.
    "watch": {"triggers"},
    # Phase 3 R7 (plans P18, P19): the kick chat input declares its triggers,
    # its credentials and its `chat.write`.
    "kick": {"triggers", "actions", "credentials"},
}
#: The manifests that declare actions, and the names each declares.
DECLARED_ACTIONS = {
    "twitch": {"chat.write"},
    "chat_context": {"chat.read"},
    "users": {"users.read"},
    "capture": {"screen.capture"},
    "audio_input": {"audio.capture"},
    "audio_output": {"audio.speak", "audio.play"},
    "stream_control": {"stream.scene.set", "stream.poll.create"},
    "clips": {"stream.clip.create"},
    "viewer_memory": {"memory.recall", "memory.record"},
    "moderation": {"moderation.request"},
    # Phase 3 R7 (plan P19): kick declares the same `chat.write` over its
    # own destinations — a second declarer, not a new action name.
    "kick": {"chat.write"},
}

#: The credentials each manifest declares, by dotted setting path (R8).
SECRET_SETTINGS = {
    "twitch": ("client_id", "client_secret", "access_token"),
    "brain": ("api_key",),
    "proxy": ("pairing_token",),
    "agent_link": ("pairing_token",),
    "audio_output": ("synthesis.api_key",),
    "audio_input": ("transcription.api_key",),
    "stream_control": ("scenes.provider.password",),
    "kick": ("client_secret", "access_token"),
}
#: The optional credentials shipped unconfigured — empty, never a literal —
#: with the speech and transcription endpoints they go with (AC43).
UNCONFIGURED_CREDENTIALS = {"audio_output.synthesis.api_key", "audio_input.transcription.api_key"}
#: The credentials a profile has no setting for at all: the server runs no
#: scene provider (``kind: none``), so it configures no scene password.
ABSENT_CREDENTIALS = {"server": {"stream_control.scenes.provider.password"}}
#: The endpoint settings of every module, by dotted path: each is a
#: ``${NAME}`` reference, except the speech and transcription endpoints,
#: which ship EMPTY — no provider is assumed (R9, AC32, AC43).
ENDPOINT_SETTINGS = {
    "brain": ("endpoint",),
    "agent_link": ("brain_url",),
    "stream_control": ("scenes.provider.url",),
}
EMPTY_ENDPOINTS = {
    "audio_output": ("synthesis.endpoint", "synthesis.model", "synthesis.api_key"),
    "audio_input": ("transcription.endpoint", "transcription.model", "transcription.api_key"),
}
NON_SECRET_SETTINGS = {
    "twitch": ("broadcaster_id", "bot_user_id", "companion_name"),
    "brain": ("endpoint", "model"),
    "agent_link": ("brain_url", "agent_id"),
}


def _setting(settings: Mapping[str, Any], dotted: str) -> Any:
    """The value at the dotted setting path *dotted*, ``None`` when absent."""

    value: Any = settings
    for part in dotted.split("."):
        if not isinstance(value, Mapping):
            return None
        value = value.get(part)
    return value


def _schema_property(schema: Mapping[str, Any], dotted: str) -> Mapping[str, Any]:
    """The schema of the setting at the dotted path *dotted*."""

    node: Mapping[str, Any] = schema
    for part in dotted.split("."):
        node = node["properties"][part]
    return node


def _read_yaml(path: Path) -> Mapping[str, Any]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, Mapping)
    return loaded


def _assert_sample_or_environment_reference(value: Any) -> None:
    assert isinstance(value, str)
    assert value.strip()
    if value.startswith("${"):
        assert ENV_REFERENCE.fullmatch(value)


def _manifests() -> dict[str, Mapping[str, Any]]:
    return {
        module_name: _read_yaml(ROOT / "modules" / module_name / "module.yaml")
        for module_name in MODULE_NAMES
    }


def _declared_action_names(manifest: Mapping[str, Any]) -> set[str]:
    return {entry["name"] for entry in manifest.get("actions") or ()}


def _declared_by_enabled(config: Mapping[str, Any]) -> set[str]:
    """The actions the manifests of the profile's enabled modules declare."""

    manifests = _manifests()
    return {
        name
        for module_name in config["enabled_modules"]
        for name in _declared_action_names(manifests[module_name])
    }


def _provided_actions(config: Mapping[str, Any]) -> set[str]:
    """The provided-action set of a profile as R7 defines it: (a) the actions
    the manifests of its enabled modules declare, (b) the ``actions``
    allowlist of an enabled ``proxy`` module — and, for the agent profile,
    as R9 lists it: a process without a brain is called only through its
    ``agent_link``, so what it provides is what the link lends (the
    ``stream_control`` it enables also declares ``stream.poll.create``,
    which the agent neither binds nor lends: polls belong to the platform's
    side, R9)."""

    enabled = config["enabled_modules"]
    if "agent_link" in enabled and "brain" not in enabled:
        lent = set(config["modules"]["agent_link"]["actions"])
        assert lent <= _declared_by_enabled(config)
        return lent
    provided = _declared_by_enabled(config)
    if "proxy" in enabled:
        provided |= set(config["modules"]["proxy"]["actions"])
    return provided


def _granted_actions(config: Mapping[str, Any]) -> set[str]:
    return {rule["action_name"] for rule in config["actions"]}


def _string_leaves(value: Any) -> list[str]:
    """Every string in *value*, keys included, in document order."""

    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        return [
            leaf
            for key, item in value.items()
            for leaf in _string_leaves(key) + _string_leaves(item)
        ]
    if isinstance(value, list):
        return [leaf for item in value for leaf in _string_leaves(item)]
    return []


def test_manifests_are_unique_and_have_coherent_capabilities() -> None:
    """R7 supersedes the former whole-manifest equality (allowlisted); phase
    2's R9 extends the catalog from 8 to 11 manifests (allowlisted); phase
    3's R1 adds the ``notices`` setting and the ``event_kind`` trigger type to
    ``twitch`` (allowlisted, plan decision 11); phase 3's R2 adds the
    ``clips`` manifest and its ``stream.clip.create`` (allowlisted, running
    value of plan step P9: 12 manifests, 10 action names); phase 3's R3 adds
    the ``viewer_memory`` manifest (allowlisted, running value of plan step
    P10: 13 manifests, 10 action names); phase 3's R4 adds its
    ``memory.recall`` and ``memory.record`` (allowlisted, running value of
    plan step P11: 13 manifests, 12 action names); phase 3's R5 adds the
    ``moderation`` manifest and its ``moderation.request`` (allowlisted,
    running value of plan step P14: 14 manifests, 13 action names); phase
    3's R6 adds the ``watch`` input manifest, which declares triggers and no
    action (allowlisted, running value of plan step P16: 15 manifests, 13
    action names); phase 3's R7 adds the ``kick`` input manifest, which
    declares triggers and credentials (allowlisted, running value of plan
    step P18: 16 manifests, 13 action names), then its ``chat.write`` (plan
    step P19, decision 11): the one multiplicity assertion is rewritten to
    the running value — every action name is declared by exactly one
    manifest except ``chat.write``, declared by exactly twitch and kick —
    while the manifest count and the action-name count stay as they were.

    Every shipped manifest — the eight R8 names and phase 2's
    ``audio_output``, ``audio_input`` and ``stream_control`` — is v2: it
    declares the routing keys exactly as before, the runtime contract it is
    built against, its lifecycle roles, a settings schema and a settings
    hook its package really implements. Only the chat input and the
    ``watch`` input (phase 3 R6) declare triggers; it and the six capability modules declare their actions (two
    each for ``audio_output`` and ``stream_control``); the two transport
    modules declare a credential and no action. The manifests carry no key
    beyond those, so nothing is declared that the runtime does not read.
    """

    manifests = _manifests()

    names = [manifest["name"] for manifest in manifests.values()]
    assert len(names) == len(set(names))
    assert set(names) == set(MODULE_NAMES)
    assert len(names) == 16
    assert set(EXPECTED_MANIFESTS) == set(MODULE_NAMES)

    for module_name, expected in EXPECTED_MANIFESTS.items():
        manifest = manifests[module_name]
        assert {key: manifest.get(key) for key in expected} == expected, module_name
        assert set(manifest) == set(expected) | {"settings_schema"} | DECLARATION_KEYS[module_name]

        schema = manifest["settings_schema"]
        assert isinstance(schema, Mapping) and schema.get("type") == "object"
        assert isinstance(schema.get("properties"), Mapping) and schema["properties"]
        assert set(schema.get("required", ())) <= set(schema["properties"])
        # R8: every credential the module is configured with is declared, so
        # the loader redacts it whether or not the configuration lists it.
        assert tuple(manifest.get("credentials", ())) == SECRET_SETTINGS.get(module_name, ())
        for setting_name in manifest.get("credentials", ()):
            assert _schema_property(schema, setting_name)["type"] == "string"

        module = importlib.import_module(f"modules.{module_name}")
        assert callable(getattr(module, manifest["settings_validator"]))

    for manifest in manifests.values():
        assert type(manifest["manifest_version"]) is int
        assert type(manifest["middleware"]) is bool
    assert type(manifests["audit"]["order"]) is int
    assert isinstance(manifests["twitch"]["triggers"], Mapping)
    assert isinstance(manifests["twitch"]["actions"], list) and manifests["twitch"]["actions"]
    # Phase 3 R1 (plan P7, decision 11): the twitch manifest gains the
    # optional `notices` setting and the `event_kind` trigger type; its keys,
    # required settings and actions are otherwise unchanged.
    assert "notices" in manifests["twitch"]["settings_schema"]["properties"]
    assert "notices" not in manifests["twitch"]["settings_schema"]["required"]
    assert [entry["name"] for entry in manifests["twitch"]["triggers"]["types"]] == [
        "probability",
        "audience",
        "keyword",
        "event_kind",
    ]
    for module_name, action_names in DECLARED_ACTIONS.items():
        assert isinstance(manifests[module_name]["actions"], list), module_name
        assert _declared_action_names(manifests[module_name]) == action_names
    # R7: the proxy manifest itself declares no action, nor does the agent's
    # link — what they lend is declared by the capability modules.
    for module_name in ("proxy", "agent_link"):
        assert "actions" not in manifests[module_name]
    declared = set().union(*DECLARED_ACTIONS.values())
    # Phase 3 R2 (plan P9), R4 (plan P11) and R5 (plan P14): `clips`,
    # `viewer_memory` and `moderation` are shipped and enabled by no phase 2
    # profile, so the catalog is the PC profile's set plus their actions.
    assert declared == PROVIDED_ACTIONS["pc"] | {
        "stream.clip.create",
        "memory.recall",
        "memory.record",
        "moderation.request",
    }
    assert len(declared) == 13
    # Phase 3 R7 (plan P19, decision 11), superseding "declared once each, by
    # one manifest": each action name has exactly one declaring manifest,
    # except `chat.write`, declared by exactly twitch and kick (running value;
    # P21 adds youtube, P23 pins the final value).
    declarers = {
        name: {module for module, names in DECLARED_ACTIONS.items() if name in names}
        for name in declared
    }
    assert declarers.pop("chat.write") == {"twitch", "kick"}
    assert all(len(modules) == 1 for modules in declarers.values()), declarers


@pytest.mark.parametrize("profile", sorted(PROFILES))
def test_profile_enables_its_modules_and_contains_no_literal_credentials(
    profile: str,
) -> None:
    """R7, AC42 on each profile — replaces the former PC-only completeness
    test (allowlisted) — with phase 2's module lists (R9, AC32; allowlisted):
    the PC profile enables R9's nine modules in order, the server profile
    adds ``stream_control`` before the brain and keeps ``proxy`` in place of
    ``capture``, the agent profile runs the four device modules and
    ``agent_link``; every configured module is enabled and every enabled
    module configured; every credential a manifest declares is a ``${NAME}``
    reference — 0 literal credentials — except the optional speech and
    transcription keys, which ship EMPTY with their endpoints (AC43: no
    provider is assumed); every other endpoint is a reference too, as is
    every ``secrets`` entry, and the expanded profile resolves each of
    them."""

    path = PROFILES[profile]
    config = _read_yaml(path)
    manifests = _manifests()

    assert isinstance(config.get("modules_directory"), str)
    assert config["modules_directory"].strip()
    assert config.get("enabled_modules") == ENABLED_MODULES[profile]
    if profile == "pc":
        assert len(config["enabled_modules"]) == 9
        assert [name for name in config["enabled_modules"] if name not in PHASE2_MODULES] == (
            PHASE1_MODULES
        )
    if profile == "server":
        enabled = config["enabled_modules"]
        assert enabled.index("stream_control") == enabled.index("brain") - 1

    modules = config.get("modules")
    assert isinstance(modules, Mapping)
    assert set(modules) == set(config["enabled_modules"])
    for module_name in config["enabled_modules"]:
        assert isinstance(modules[module_name], Mapping), module_name
        schema = manifests[module_name]["settings_schema"]
        if schema.get("required"):
            assert modules[module_name], module_name
            assert set(schema["required"]) <= set(modules[module_name]), module_name

    # AC42, AC32: every credential setting of every configured module is a
    # reference — read from the manifests, so a credential added to a
    # manifest later is checked here without a table change — or, for the
    # two optional keys AC43 ships unconfigured, empty. Never a literal.
    referenced_variables: set[str] = set()
    empty_credentials: set[str] = set()
    absent_credentials: set[str] = set()
    for module_name in config["enabled_modules"]:
        declared = tuple(manifests[module_name].get("credentials", ()))
        assert declared == SECRET_SETTINGS.get(module_name, ())
        for setting_name in declared:
            label = f"{module_name}.{setting_name}"
            value = _setting(modules[module_name], setting_name)
            if value is None:
                absent_credentials.add(label)
                continue
            assert isinstance(value, str), label
            if value == "":
                assert label in UNCONFIGURED_CREDENTIALS, label
                empty_credentials.add(label)
                continue
            match = ENV_REFERENCE.fullmatch(value)
            assert match is not None, label
            referenced_variables.add(match.group(1))
    assert referenced_variables
    assert absent_credentials == ABSENT_CREDENTIALS.get(profile, set())
    assert empty_credentials == {
        label for label in UNCONFIGURED_CREDENTIALS if label.split(".")[0] in modules
    }

    # AC32: every endpoint is a ``${NAME}`` reference; AC43: the speech and
    # transcription endpoints, models and keys ship empty — no localhost,
    # vendor or model default — and nothing names a loopback host.
    for module_name, setting_names in ENDPOINT_SETTINGS.items():
        for setting_name in setting_names if module_name in modules else ():
            value = _setting(modules[module_name], setting_name)
            if (
                module_name == "stream_control"
                and _setting(modules[module_name], "scenes.provider.kind") == "none"
            ):
                # No scene provider, hence no address (the server, R9).
                assert value is None
                continue
            assert isinstance(value, str) and ENV_REFERENCE.fullmatch(value), (
                f"{module_name}.{setting_name}"
            )
    for module_name, setting_names in EMPTY_ENDPOINTS.items():
        for setting_name in setting_names if module_name in modules else ():
            assert _setting(modules[module_name], setting_name) == "", (
                f"{module_name}.{setting_name}"
            )
    for leaf in _string_leaves(config):
        assert not any(host in leaf for host in ("localhost", "127.0.0.1", "::1")), leaf

    for module_name, setting_names in NON_SECRET_SETTINGS.items():
        if module_name not in modules:
            continue
        for setting_name in setting_names:
            _assert_sample_or_environment_reference(modules[module_name].get(setting_name))

    # The TLS material and the listed secrets are references too, and no
    # string of the file is a half-formed reference that would resolve to a
    # literal by accident.
    if "proxy" in modules:
        for key in ("certfile", "keyfile"):
            assert ENV_REFERENCE.fullmatch(modules["proxy"]["tls"][key])
    if "agent_link" in modules:
        assert ENV_REFERENCE.fullmatch(modules["agent_link"]["brain_url"])
    for entry in config.get("secrets") or ():
        assert ENV_REFERENCE.fullmatch(entry), entry
    for leaf in _string_leaves(config):
        assert ("${" in leaf) == bool(ENV_REFERENCE.fullmatch(leaf)), leaf

    expanded = load_config(path, environ=_sample_environ(path))
    assert Path(expanded["modules_directory"]) == ROOT / "modules"
    for module_name in config["enabled_modules"]:
        for setting_name in manifests[module_name].get("credentials", ()):
            if f"{module_name}.{setting_name}" in absent_credentials:
                continue
            resolved = _setting(expanded["modules"][module_name], setting_name)
            assert isinstance(resolved, str)
            assert bool(resolved) == (f"{module_name}.{setting_name}" not in empty_credentials)
            assert not ENV_REFERENCE.fullmatch(resolved)
    # Every listed secret was resolved by an enabled module's setting: the
    # redaction list carries a value per entry, never a dangling reference.
    assert len(expanded["secrets"]) == len(config.get("secrets") or ())
    assert all(not ENV_REFERENCE.fullmatch(value) for value in expanded["secrets"])


@pytest.mark.parametrize("profile", sorted(PROFILES))
def test_profile_carries_what_the_versioned_runtime_requires(profile: str) -> None:
    """R1, R6: each profile starts the versioned runtime.

    A v2 manifest is refused by the loader without the ``limits`` block —
    the chat input declares triggers, which need a trigger engine; the
    agent's link is built against the versioned context — and the chat
    input's default policy names the companion by token, which resolves
    from that module's own settings. A profile missing the limits block or,
    where twitch runs, the companion name would turn startup validation
    into a broken quick-start.
    """

    config = _read_yaml(PROFILES[profile])

    limits = config.get(LIMITS_KEY)
    assert isinstance(limits, Mapping)
    assert limits
    assert set(limits) == set(application._LIMITS)
    for group, section in limits.items():
        assert isinstance(section, Mapping), group
        assert section, group
        for field_name, value in section.items():
            assert type(value) in (int, float), f"{group}.{field_name}"
            assert value > 0 and value != float("inf"), f"{group}.{field_name}"

    if "twitch" in config["modules"]:
        companion_name = config["modules"]["twitch"].get("companion_name")
        assert isinstance(companion_name, str)
        assert companion_name.strip()
        assert ENV_REFERENCE.fullmatch(companion_name) is None

    # The capture a profile configures stays under the store's object bound:
    # a larger capture would be refused by the store, never retained.
    if "capture" in config["modules"]:
        capture = config["modules"]["capture"]
        assert capture["max_bytes"] <= limits["attachments"]["max_object_bytes"]
        assert capture["default_source"] in capture["sources"]


@pytest.mark.parametrize("profile", BRAIN_PROFILES)
def test_brain_profiles_carry_the_acted_budget_defaults(profile: str) -> None:
    """AC42 (R3, R7): the brain budgets of both brain profiles equal R3's
    acted defaults, in AC42's order — the eight ``budget`` fields, then the
    admission wait and total — and the admission mirror equals ``limits``."""

    config = _read_yaml(PROFILES[profile])
    brain = config["modules"]["brain"]

    budgets = tuple(brain["budget"][field_name] for field_name in BUDGET_FIELDS) + (
        brain["admission"]["wait_seconds"],
        brain["admission"]["total_run_seconds"],
    )
    assert budgets == ACTED_BUDGETS
    assert set(brain["budget"]) == set(BUDGET_FIELDS)
    assert brain["admission"] == config[LIMITS_KEY]["admission"]
    assert brain["conversation_memory"] == config[LIMITS_KEY]["conversation_memory"]
    assert brain["fallback"] == {"enabled": True, "text": "I could not answer in time."}
    assert brain["capabilities"] == {"required": ["structured_output", "vision"]}


@pytest.mark.parametrize("profile", BRAIN_PROFILES)
def test_brain_profiles_carry_the_single_fixed_delivery_entry(profile: str) -> None:
    """AC58 (R7, decision 1): both brain profiles carry ``modules.brain.delivery``
    equal to the single fixed ``chat.write`` entry handing the text under
    ``text``, and no ``overrides`` — so their observable delivery is one
    ``chat.write`` per run, and the two profiles deliver identically."""

    delivery = _read_yaml(PROFILES[profile])["modules"]["brain"]["delivery"]

    assert delivery == FIXED_DELIVERY
    assert "overrides" not in delivery
    assert "preference" not in delivery
    assert _read_yaml(PC_PROFILE)["modules"]["brain"]["delivery"] == (
        _read_yaml(SERVER_PROFILE)["modules"]["brain"]["delivery"]
    )


@pytest.mark.parametrize("profile", sorted(PROFILES))
def test_profile_satisfies_every_module_owned_settings_validator(profile: str) -> None:
    """R7, AC42: each profile passes each enabled module's own preflight.

    A module manifest may require settings beyond the credentials — the
    brain requires its admission, budget and conversation memory limits,
    the capture module its sources — and the loader runs the module's
    ``settings_validator`` on ``modules.<name>`` as shipped, with the
    accepted ``limits`` block as the module's reserved copy and nothing
    else merged in. A profile that satisfied the core's limits but not a
    module's would refuse to start with valid credentials, so every
    validator is run here on the expanded profile with sample values for
    its references.
    """

    path = PROFILES[profile]
    config = _read_yaml(path)
    expanded = load_config(path, environ=_sample_environ(path))

    checked = 0
    for module_name in config["enabled_modules"]:
        manifest = _read_yaml(ROOT / "modules" / module_name / "module.yaml")
        hook_name = manifest.get("settings_validator")
        if hook_name is None:
            continue
        module = importlib.import_module(f"modules.{module_name}")
        validator = getattr(module, hook_name)
        assert validator(expanded["modules"][module_name]) == [], module_name
        checked += 1
    assert checked == len(config["enabled_modules"])


# --------------------------------------------------------------------------- #
# The profiles through the real assembly and the real loader (R1, R5, R6)
# --------------------------------------------------------------------------- #


def _referenced_variables(value: Any) -> set[str]:
    """Every ``${NAME}`` the profile references, in values and mapping keys."""

    found: set[str] = set()
    if isinstance(value, str):
        match = ENV_REFERENCE.fullmatch(value)
        if match is not None:
            found.add(match.group(1))
    elif isinstance(value, Mapping):
        for key, item in value.items():
            found |= _referenced_variables(key)
            found |= _referenced_variables(item)
    elif isinstance(value, list):
        for item in value:
            found |= _referenced_variables(item)
    return found


def _sample_value(name: str) -> str:
    """A dummy value per referenced variable: endpoints get a URL shape, the
    brain URL (and the scene provider's) the ``wss://`` form accepted off
    loopback (R6); the recorder and the player name an executable present on
    every machine — the interpreter — which the device seams never run, so a
    player resolves as the operator's would (R1, R3)."""

    if name.endswith("ENDPOINT"):
        return f"https://sample.invalid/{name.lower()}"
    if name.endswith("_URL"):
        return "wss://brain.example:8765"
    if name.endswith(("_PLAYER", "_RECORDER")):
        return sys.executable
    return f"sample-{name.lower()}"


def _sample_environ(path: Path = EXAMPLE_PATH) -> dict[str, str]:
    """A dummy value per variable the profile at *path* references."""

    return {name: _sample_value(name) for name in _referenced_variables(_read_yaml(path))}


class _TwitchSession:
    """The chat input's transport, reaching preparation and nothing beyond.

    Preparation validates the credential over ``get``; no subscription, no
    socket and no send is ever requested by these tests.
    """

    def __init__(self, environ: Mapping[str, str]) -> None:
        self._identity = {
            "client_id": environ["TWITCH_CLIENT_ID"],
            "user_id": environ["TWITCH_BOT_USER_ID"],
        }
        self.close_calls = 0

    async def get(self, url: str, **kwargs: Any) -> FakeResponse:
        return FakeResponse(200, dict(self._identity))

    async def post(self, url: str, **kwargs: Any) -> Any:
        raise AssertionError(f"unexpected request beyond preparation: {url}")

    async def ws_connect(self, url: str) -> Any:
        raise AssertionError(f"unexpected socket beyond preparation: {url}")

    async def close(self) -> None:
        self.close_calls += 1


class _ModelSession(FakeSession):
    """The brain's model transport, probe-aware and nothing beyond.

    Every capability probe the brain makes at ``prepare`` (R2) is answered
    by the shared fake with one valid forced tool call, so a prepared brain
    becomes ready; a scenario request is never expected by these tests and
    is refused rather than answered.
    """

    async def _answer(self, url: str, kwargs: dict[str, Any]) -> Any:
        raise AssertionError(f"unexpected model request beyond preparation: {url}")


async def _refuse_scene_dial(url: str) -> Any:
    """The scene provider's dial at a closed port: refused, as a real one is."""

    raise ConnectionRefusedError("connection refused")


@dataclass
class DeviceEdges:
    """The edges of the three phase 2 device modules, all in process.

    The audio source records a scripted 5-s segment and has no probe (so it
    is usable); the speech and transcription transports count every factory
    call and request — the shipped profiles configure no endpoint, so both
    stay at 0 (AC43); the player runner starts nothing; the scene provider's
    dial is refused like a closed port unless a :class:`ScenePeer` is given.
    """

    speech: ScriptedSpeechTransport = field(default_factory=ScriptedSpeechTransport)
    stt: ScriptedTranscriptionTransport = field(default_factory=ScriptedTranscriptionTransport)
    runner: RecordingPlayerRunner = field(default_factory=RecordingPlayerRunner)
    source: FakeAudioSource = field(default_factory=lambda: FakeAudioSource(wav_bytes(5.0)))
    peer: ScenePeer | None = None
    #: The clock every bound of the three modules waits on: nobody advances
    #: it, so no probe or call bound fires because a test waited.
    clock: ManualClock = field(default_factory=ManualClock)

    def seams(self) -> dict[str, dict[str, Any]]:
        return {
            "audio_input": {
                "_source_factory": lambda spec: self.source,
                "_transcription_transport": self.stt,
                "_sleeper": self.clock.sleep,
            },
            "audio_output": {
                "_synthesis_transport": self.speech,
                "_player_runner": self.runner,
                "_sleeper": self.clock.sleep,
            },
            "stream_control": {
                "_websocket_factory": (
                    self.peer.factory if self.peer is not None else _refuse_scene_dial
                ),
                "_sleeper": self.clock.sleep,
            },
        }

    def requests(self) -> int:
        """Every speech and transcription transport made or request sent."""

        return (
            self.speech.factory_calls
            + len(self.speech.requests)
            + self.stt.factory_calls
            + len(self.stt.requests)
        )

    async def close(self) -> None:
        if self.peer is not None:
            await self.peer.stop()


async def _refuse_listener(handler: Any, **kwargs: Any) -> Any:
    raise AssertionError("unexpected listener beyond preparation")


async def _refuse_dial(*args: Any, **kwargs: Any) -> Any:
    raise AssertionError("unexpected dial beyond preparation")


def _point_seams_at_fakes(
    config: dict[str, Any],
    environ: Mapping[str, str],
    diagnostics: list[str],
    edges: DeviceEdges | Mapping[str, Mapping[str, Any]] | None = None,
) -> None:
    """Point every transport seam of the profile's modules at fakes.

    One entry per shipped module that owns an edge the process cannot own
    in a test: the chat input's session, the brain's model session (probe-
    aware), the audit writer, the capture module's screen (a
    :class:`FakeCaptureSource` handed in through its source seam, so no
    command runs and no path is read), the proxy's listener and the agent
    link's dial (both refused: neither is reached before ``start_inputs``,
    which these tests never call), and the three phase 2 device modules'
    edges (:class:`DeviceEdges`). A module the profile does not enable is
    left alone — the seams are applied to the modules the profile
    configures, whatever it lists — and a module without an edge
    (``chat_context``, ``users``) needs none.
    """

    seams: dict[str, dict[str, Any]] = {
        "twitch": {
            "_session_factory": lambda: _TwitchSession(environ),
            "diagnostic_reporter": diagnostics.append,
        },
        "brain": {"_session_factory": _ModelSession, "diagnostic_reporter": diagnostics.append},
        "audit": {"_writer": lambda line: None},
        "capture": {"_source_factory": lambda spec: FakeCaptureSource()},
        "proxy": {"_server_factory": _refuse_listener, "diagnostic_reporter": diagnostics.append},
        "agent_link": {"_connector": _refuse_dial, "diagnostic_reporter": diagnostics.append},
        **(
            edges.seams()
            if isinstance(edges, DeviceEdges)
            else dict(edges) if edges is not None else DeviceEdges().seams()
        ),
    }
    for module_name, extra in seams.items():
        if module_name in config["modules"]:
            config["modules"][module_name].update(extra)


async def _activate_example(
    environ: Mapping[str, str],
    path: Path = EXAMPLE_PATH,
    edges: DeviceEdges | Mapping[str, Mapping[str, Any]] | None = None,
) -> tuple[Any, list[Any], list[str]]:
    """Load the profile at *path* as ``core.main`` does, assemble the runtime
    from its limits and its authorization rules, and activate the enabled
    modules through the real loader — with the transport seams pointed at
    fakes, so nothing reaches a network — returning the runtime, the
    activations and the diagnostics the modules reported."""

    config = load_config(path, environ=environ)
    runtime = application._assemble_runtime(config)
    diagnostics: list[str] = []
    _point_seams_at_fakes(config, environ, diagnostics, edges)

    loader = ModuleLoader(runtime.bus, config["modules_directory"])
    loader.context = runtime.context
    loader.environ = environ
    activations = await loader.activate_enabled(config)
    return runtime, activations, diagnostics


async def _prepare_all(activations: list[Any]) -> None:
    """Run every activation's ``prepare`` in activation order — the phase in
    which providers bind (R4) — through the fakes, opening nothing."""

    for activation in activations:
        prepare = getattr(activation.handle, "prepare", None)
        if callable(prepare):
            await prepare()


async def _close_all(activations: list[Any]) -> None:
    for activation in reversed(activations):
        await activation.handle.close()


def _chat_event(channel_id: str, message_id: str, text: str) -> dict[str, Any]:
    """A schema-version-2 normalised chat message, as the chat input publishes it."""

    return {
        "type": "channel.chat.message",
        "payload": {
            "platform": "twitch",
            "channel_id": channel_id,
            "author": {"id": "viewer-1", "display_name": "Viewer"},
            "message_id": message_id,
            "text": text,
        },
        "metadata": {"source": "twitch", "schema_version": 2},
    }


@pytest.mark.parametrize("profile", BRAIN_PROFILES)
async def test_example_trigger_policies_give_the_two_channels_two_distinct_policies(
    profile: str,
) -> None:
    """AC1 (R1) on both twitch profiles: the served channel carries an
    explicit ``keyword`` rule and accepts exactly 1 of 2 messages — the one
    with the keyword, not the one that merely mentions the companion —
    while a channel with no entry falls back to the manifest default and
    accepts only the 1 message of 2 that mentions the configured companion
    name: 2 accepted and 2 rejected decisions, from one registry in one
    process."""

    path = PROFILES[profile]
    environ = _sample_environ(path)
    runtime, activations, diagnostics = await _activate_example(environ, path)
    try:
        engine = runtime.context.triggers
        served = environ["TWITCH_BROADCASTER_ID"]
        other = "another-channel"
        companion = _read_yaml(path)["modules"]["twitch"]["companion_name"]
        keyword = "!ask"
        assert companion.casefold() not in keyword.casefold()

        decisions = {
            "served-keyword": engine.evaluate(_chat_event(served, "s1", f"{keyword} what time is it")),
            "served-mention": engine.evaluate(_chat_event(served, "s2", f"{companion}, what time is it")),
            "other-keyword": engine.evaluate(_chat_event(other, "o1", f"{keyword} what time is it")),
            "other-mention": engine.evaluate(_chat_event(other, "o2", f"{companion}, what time is it")),
        }

        assert {name: decision.accepted for name, decision in decisions.items()} == {
            "served-keyword": True,
            "served-mention": False,
            "other-keyword": False,
            "other-mention": True,
        }
        assert sum(decision.accepted for decision in decisions.values()) == 2
        # Two distinct policies, not one merged one: the served channel's
        # version differs from the default's.
        assert decisions["served-keyword"].policy_version != decisions["other-mention"].policy_version
        assert decisions["served-keyword"].policy_version == decisions["served-mention"].policy_version
        assert diagnostics == []
    finally:
        await _close_all(activations)


#: The module each granted action is bound to once the profile is prepared
#: through the device seams (decision 7; R9): the server's five device
#: actions go to the ``proxy`` provider, its poll to ``stream_control``.
BOUND_MODULES = {
    "pc": {
        "chat.read": "chat_context",
        "users.read": "users",
        "chat.write": "twitch",
        "screen.capture": "capture",
        "audio.capture": "audio_input",
        "audio.play": "audio_output",
        "stream.scene.set": "stream_control",
    },
    "server": {
        "chat.read": "chat_context",
        "users.read": "users",
        "chat.write": "twitch",
        **{name: "proxy" for name in SERVER_PROXY_ACTIONS},
        "stream.poll.create": "stream_control",
    },
    "agent": {
        "screen.capture": "capture",
        "audio.capture": "audio_input",
        "audio.play": "audio_output",
        "stream.scene.set": "stream_control",
    },
}
#: The granted actions the shipped profile leaves unbound (R8): no speech
#: endpoint is configured, so ``audio.speak`` is not bound (AC43); the PC
#: profile ships polls disabled (plan decision 6).
UNBOUND_ACTIONS = {
    "pc": {"audio.speak", "stream.poll.create"},
    "server": set(),
    "agent": {"audio.speak"},
}


@pytest.mark.parametrize("profile", sorted(PROFILES))
async def test_profile_grants_exactly_its_provided_action_set(profile: str) -> None:
    """AC42 (R5, R7) on each profile — replaces the former PC-only grant test
    (allowlisted) — with phase 2's provided-action sets (R9, AC32;
    allowlisted): the action names the rules grant equal the profile's
    provided-action set — PC and server the nine names, the five device
    actions coming from the ``proxy`` allowlist in the server file (with
    ``stream.poll.create`` from ``stream_control``), from the device
    manifests in the PC file; agent exactly the five device actions. Each
    rule grants its action's own permission, nature and scope as its
    manifest declares them. Then through the real loader, with the device
    edges in process: every granted action the profile can bind is bound by
    exactly one provider — the one decision 7 selects — with no ambiguity;
    ``audio.speak`` stays unbound with 0 speech requests, as no speech
    endpoint is configured (AC43), and the PC profile's disabled poll too;
    and the view offered to the brain's principal on the served
    destinations is exactly the bound granted set — and nothing anywhere the
    rules do not reach: another channel, another principal.
    """

    path = PROFILES[profile]
    config = _read_yaml(path)
    manifests = _manifests()

    granted = _granted_actions(config)
    provided = _provided_actions(config)
    assert granted == provided == PROVIDED_ACTIONS[profile]
    assert len(granted) == len(config["actions"]) == {"pc": 9, "server": 9, "agent": 5}[profile]
    declared_by_enabled = _declared_by_enabled(config)
    if profile == "server":
        assert config["modules"]["proxy"]["actions"] == SERVER_PROXY_ACTIONS
        # Only the scene change is declared locally too — by
        # ``stream_control`` with ``kind: none``, which binds nothing.
        assert declared_by_enabled & set(SERVER_PROXY_ACTIONS) == {"stream.scene.set"}
        assert declared_by_enabled == {
            "chat.read", "users.read", "chat.write", "stream.scene.set", "stream.poll.create"
        }
    elif profile == "agent":
        assert config["modules"]["agent_link"]["actions"] == SERVER_PROXY_ACTIONS
        assert config["modules"]["stream_control"]["polls"]["enabled"] is False
        assert declared_by_enabled - granted == {"stream.poll.create"}
    else:
        assert "proxy" not in config["enabled_modules"]
        assert granted == declared_by_enabled
    # Every grant is a rule for principal ``brain`` on its action's declared
    # scope, with the matching nature and permission (R5) — the tables
    # agree with the manifests that declare the actions.
    specs = {
        entry["name"]: entry
        for manifest in manifests.values()
        for entry in manifest.get("actions") or ()
    }
    for rule in config["actions"]:
        name = rule["action_name"]
        assert rule["principals"] == ["brain"]
        assert rule["granted_permissions"] == [ACTION_PERMISSIONS[name]]
        assert specs[name]["required_permissions"] == [ACTION_PERMISSIONS[name]]
        assert rule["natures"] == (["read"] if name in READ_ACTIONS else ["write"])
        assert specs[name]["nature"] == rule["natures"][0]
        assert rule["destination"]["scope"] == ACTION_SCOPES[name]
        assert {d["scope"] for d in specs[name]["supported_destinations"]} == {ACTION_SCOPES[name]}

    environ = _sample_environ(path)
    edges = DeviceEdges(peer=ScenePeer(scenes=("Main", "Break"), current="Main"))
    runtime, activations, diagnostics = await _activate_example(environ, path, edges)
    try:
        registry = runtime.context.actions
        # Before preparation: declared, bound by nobody, offered to nobody.
        assert set(registry.discovered()) == declared_by_enabled
        assert registry.bindings() == ()
        assert registry.authorized(principal="brain") == {}

        await _prepare_all(activations)

        unbound = UNBOUND_ACTIONS[profile]
        bound = granted - unbound
        assert set(registry.discovered()) == provided | declared_by_enabled
        assert registry.bindings("stream.poll.create") == () or profile == "server"
        assert set(BOUND_MODULES[profile]) == bound
        for name in bound:
            bindings = registry.bindings(name)
            assert len(bindings) == 1, name
            assert bindings[0].module == BOUND_MODULES[profile][name], name
            if name == "screen.capture":
                assert bindings[0].provider_name == SCREEN_CAPTURE_PROVIDERS[profile]
            if bindings[0].module == "proxy":
                assert bindings[0].provider_name == PROXY_PROVIDER
        for name in unbound:
            assert registry.bindings(name) == (), name
        # AC43: no speech or transcription endpoint, no request of either.
        assert edges.requests() == 0
        if profile == "server":
            # The remote provider becomes ready when an agent pairs
            # (tests/test_proxy.py); the grants, not the pairing, are under
            # test here, so readiness is given by hand to read the view.
            assert not set(SERVER_PROXY_ACTIONS) & set(registry.registered_ready())
            registry.mark_ready("proxy")
        assert set(registry.registered_ready()) == bound

        if profile == "agent":
            platform, channel = "anyplatform", "any-channel"
        else:
            platform, channel = "twitch", environ["TWITCH_BROADCASTER_ID"]
        scopes = sorted(set(ACTION_SCOPES.values()))
        offered: dict[str, set[str]] = {
            scope: set(
                registry.authorized(principal="brain", destination=Destination(platform, channel, scope))
            )
            for scope in scopes
        }
        assert set().union(*offered.values()) == bound
        for scope, names in offered.items():
            assert names == {name for name in bound if ACTION_SCOPES[name] == scope}, scope
        if profile == "agent":
            # The agent's rules are on ``*/*/<scope>``: any platform and
            # channel of each scope, those scopes only.
            assert set(
                registry.authorized(principal="brain", destination=Destination("fake", "c", "capture"))
            ) == {"screen.capture"}
            assert registry.authorized(
                principal="brain", destination=Destination(platform, channel, "chat")
            ) == {}
        else:
            for scope in scopes:
                other = Destination(platform, "another-channel", scope)
                assert registry.authorized(principal="brain", destination=other) == {}, scope
        for principal in ("twitch", "audit", "capture", "proxy", "agent_link", "viewer"):
            for scope in scopes:
                destination = Destination(platform, channel, scope)
                assert registry.authorized(principal=principal, destination=destination) == {}
        assert diagnostics == []
    finally:
        await _close_all(activations)
        await edges.close()


# --------------------------------------------------------------------------- #
# A profile started through the real coordinator (R8; AC30, AC31, AC43)
# --------------------------------------------------------------------------- #


#: The keyword the served channel's policy requires, and the scenario's answer.
KEYWORD = "!ask"
ANSWER = "Hello there"


@dataclass
class StartedProfile:
    """A profile loaded as ``core.main`` loads it, started by the coordinator.

    Only the network edges are fakes: the chat input's platform session (a
    welcome, a subscription, one confirmed send), the brain's model
    transport (a :class:`ScriptedModel`), the audit writer, the screen and
    whatever *device seams* the caller hands the three phase 2 modules.
    """

    environ: Mapping[str, str]
    runtime: Any
    loader: ModuleLoader
    coordinator: Any
    report: Any
    model: ScriptedModel
    platform: FakeTwitchSession
    websocket: FakeWebSocket
    diagnostics: list[str]
    reported: list[str]
    stopped: bool = False

    @property
    def registry(self) -> Any:
        return self.runtime.context.actions

    @property
    def bus(self) -> Any:
        return self.runtime.bus

    def degraded(self) -> list[dict[str, Any]]:
        """The payload of every ``module.degraded`` trace, in order."""

        return [event["payload"] for event in events_of(self.bus, TRACE_MODULE_DEGRADED)]

    def catalog_actions(self) -> list[str]:
        """Every action the discovered manifests declare — the catalog."""

        return [
            entry["name"]
            for manifest in self.loader.catalog.values()
            for entry in manifest.get("actions") or ()
        ]

    async def chat_scenario(self) -> dict[str, Any]:
        """One keyword message on the served channel, driven to its record."""

        channel = self.environ["TWITCH_BROADCASTER_ID"]
        self.websocket.feed(notification(channel, "incoming-1", f"{KEYWORD} Companion, hello"))
        await wait_until(
            lambda: len(events_of(self.bus, TRACE_BRAIN_RUN_COMPLETED)) == 1, turns=20000
        )
        (completed,) = events_of(self.bus, TRACE_BRAIN_RUN_COMPLETED)
        return completed["payload"]

    async def stop(self) -> Any:
        if self.stopped:
            return None
        self.stopped = True
        return await self.coordinator.stop()


async def _start_profile(
    path: Path,
    environ: Mapping[str, str],
    *,
    model: ScriptedModel,
    device_seams: DeviceEdges | Mapping[str, Mapping[str, Any]] | None = None,
) -> StartedProfile:
    """Load, activate and start the profile at *path* through the real
    loader and the coordinator ``core.main`` builds (prepare in activation
    order, the readiness barrier, ``start_inputs``)."""

    config = load_config(path, environ=environ)
    runtime = application._assemble_runtime(config)
    diagnostics: list[str] = []
    reported: list[str] = []
    _point_seams_at_fakes(config, environ, diagnostics, device_seams)
    websocket = FakeWebSocket(welcome())
    platform = FakeTwitchSession(
        client_id=environ["TWITCH_CLIENT_ID"],
        bot_user_id=environ["TWITCH_BOT_USER_ID"],
        websocket=websocket,
        sends=[sent_response("sent-1")],
    )
    config["modules"]["twitch"]["_session_factory"] = lambda: platform
    config["modules"]["brain"]["_session_factory"] = lambda: model

    loader = ModuleLoader(runtime.bus, config["modules_directory"])
    loader.context = runtime.context
    loader.environ = environ
    activations = await loader.activate_enabled(config)
    coordinator = application._coordinator(activations, runtime, reported.append)
    report = await coordinator.start()
    return StartedProfile(
        environ=environ,
        runtime=runtime,
        loader=loader,
        coordinator=coordinator,
        report=report,
        model=model,
        platform=platform,
        websocket=websocket,
        diagnostics=diagnostics,
        reported=reported,
    )


def _chat_scenario_model() -> ScriptedModel:
    """The phase 1 scenario: ``chat.read`` proposed, then a final answer."""

    return ScriptedModel(tool_call("chat.read", {"limit": 5}), final(ANSWER))


def _offered_tools(model: ScriptedModel) -> set[str]:
    """The tools the brain offered the model on the run's first turn."""

    first = model.requests()[0]
    return {tool["function"]["name"] for tool in first.get("tools") or ()}


def _assert_the_chat_scenario_ran(started: StartedProfile, completed: Mapping[str, Any]) -> None:
    """``chat.read`` → final → ``chat.write``: two model turns, one call, one send."""

    assert completed["status"] == "success", (completed, started.diagnostics)
    assert completed["sends"] == 1
    assert completed["model_calls"] == 2
    assert len(started.model.requests()) == 2
    assert [call["json"]["message"] for call in started.platform.helix_calls] == [ANSWER]
    assert len(events_of(started.bus, TRACE_CHANNEL_CHAT_SENT)) == 1
    second = started.model.requests()[1]
    assert any(message.get("role") == "tool" for message in second["messages"])


def _chat_only_profile(directory: Path) -> Path:
    """The PC profile with ``enabled_modules`` reduced to the six phase 1
    modules — nothing else changed (R8): the three phase 2 sections stay
    in the file, disabled, and their references are never resolved."""

    config = dict(_read_yaml(PC_PROFILE))
    config["enabled_modules"] = [
        name for name in config["enabled_modules"] if name not in PHASE2_MODULES
    ]
    assert config["enabled_modules"] == PHASE1_MODULES
    config["modules_directory"] = str(ROOT / "modules")
    path = directory / "chat-only.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return path


def _phase1_environ(path: Path) -> dict[str, str]:
    """The sample environment of *path* without a single phase 2 variable."""

    environ = _sample_environ(path)
    phase2 = _referenced_variables(
        {name: _read_yaml(PC_PROFILE)["modules"][name] for name in PHASE2_MODULES}
    )
    assert phase2
    return {name: value for name, value in environ.items() if name not in phase2}


async def test_ac30_the_chat_only_profile_starts_and_runs_the_phase_1_scenario(
    tmp_path: Path,
) -> None:
    """AC30 (R8), AC43 (chat-only clause): the PC profile with
    ``enabled_modules`` reduced to the six phase 1 modules — and not one
    phase 2 variable in the environment — passes ``--check-config`` with
    exit 0, reaches readiness through the real coordinator with 0
    ``module.degraded``, and runs ``chat.read`` → final → ``chat.write``
    with 1 send; its registered-ready view is exactly {``chat.read``,
    ``users.read``, ``screen.capture``, ``chat.write``} and the brain offers
    no phase 2 action, while the catalog of discovered manifests declares
    the 13 actions — phase 3's R2 adds ``stream.clip.create`` to the 9 of
    phase 2 (allowlisted, running value of plan step P9), R4 adds
    ``memory.recall`` and ``memory.record`` (running value of plan step
    P11) and R5 adds ``moderation.request`` (running value of plan step
    P14). Phase 3's R7 (plan step P19, decision 11) makes kick a second
    declarer of ``chat.write``: the catalog still names 13 actions, each
    declared once except ``chat.write``, declared twice (twitch and
    kick)."""

    path = _chat_only_profile(tmp_path)
    environ = _phase1_environ(path)
    check_diagnostics: list[str] = []

    status = await application.check_config(
        path, environ=environ, diagnostic_reporter=check_diagnostics.append
    )
    assert status == 0, check_diagnostics
    assert check_diagnostics == []

    started = await _start_profile(path, environ, model=_chat_scenario_model())
    try:
        assert started.report.status == 0, (started.report, started.reported)
        assert started.degraded() == []
        assert set(started.registry.registered_ready()) == PHASE1_ACTIONS
        assert set(started.registry.discovered()) == PHASE1_ACTIONS
        catalog = started.catalog_actions()
        assert len(set(catalog)) == 13
        assert catalog.count("chat.write") == 2
        assert all(catalog.count(name) == 1 for name in set(catalog) - {"chat.write"})
        assert set(catalog) == PHASE1_ACTIONS | PHASE2_ACTIONS | {
            "stream.clip.create",
            "memory.recall",
            "memory.record",
            "moderation.request",
        }

        completed = await started.chat_scenario()
        _assert_the_chat_scenario_ran(started, completed)
        assert _offered_tools(started.model) == PHASE1_READ_ACTIONS
        assert started.diagnostics == [] and started.reported == []
    finally:
        await started.stop()
