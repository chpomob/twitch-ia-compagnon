"""The three shipped profiles and the eight shipped manifests (R1, R5, R6, R7).

The profiles are ``config.yaml.example`` (PC: chat input, ``chat_context``,
``users``, local ``capture``, brain, audit), ``config.server.yaml.example``
(server: the same minus ``capture``, plus ``proxy`` lending ``screen.capture``
from a paired agent) and ``agent.yaml.example`` (the PC agent: ``capture``
and ``agent_link``). Every one is loaded through the same path ``core.main``
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
accepts its section. The profile suite (``tests/test_profiles.py``) adds the
``--check-config`` and installation checks (AC40, AC41, AC43).
"""

from __future__ import annotations

import importlib
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
import yaml

import core.main as application
from core.contracts import Destination
from core.lifecycle import ROLE_INPUT, ROLE_OBSERVATION
from core.loader import ModuleLoader
from core.main import LIMITS_KEY, load_config
from core.runtime import RUNTIME_API
from conftest import FakeCaptureSource, FakeResponse, FakeSession
from modules.capture import SCREEN_CAPTURE_PROVIDER
from modules.proxy import PROVIDER_NAME as PROXY_PROVIDER


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
)
ENV_REFERENCE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}\Z")

#: The enabled set of each profile, in activation order (R7).
ENABLED_MODULES = {
    "pc": ["twitch", "chat_context", "users", "capture", "brain", "audit"],
    "server": ["twitch", "chat_context", "users", "brain", "audit", "proxy"],
    "agent": ["capture", "agent_link"],
}
#: The provided-action set of each profile, as R7 defines it (AC42).
PROVIDED_ACTIONS = {
    "pc": {"chat.read", "users.read", "screen.capture", "chat.write"},
    "server": {"chat.read", "users.read", "screen.capture", "chat.write"},
    "agent": {"screen.capture"},
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
}
# The declarations each manifest carries beyond the routing keys (R1, R5,
# R8): only the chat input declares triggers; the input and the three
# capability modules declare an action each; the two transport modules
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
}
#: The manifests that declare exactly one action, and its name.
DECLARED_ACTIONS = {
    "twitch": "chat.write",
    "chat_context": "chat.read",
    "users": "users.read",
    "capture": "screen.capture",
}

SECRET_SETTINGS = {
    "twitch": ("client_id", "client_secret", "access_token"),
    "brain": ("api_key",),
    "proxy": ("pairing_token",),
    "agent_link": ("pairing_token",),
}
NON_SECRET_SETTINGS = {
    "twitch": ("broadcaster_id", "bot_user_id", "companion_name"),
    "brain": ("endpoint", "model"),
    "agent_link": ("brain_url", "agent_id"),
}


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


def _provided_actions(config: Mapping[str, Any]) -> set[str]:
    """The provided-action set of a profile as R7 defines it: (a) the actions
    the manifests of its enabled modules declare, (b) the ``actions``
    allowlist of an enabled ``proxy`` module."""

    manifests = _manifests()
    provided: set[str] = set()
    for module_name in config["enabled_modules"]:
        provided |= _declared_action_names(manifests[module_name])
    if "proxy" in config["enabled_modules"]:
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
    """R7 supersedes the former whole-manifest equality (allowlisted).

    Every shipped manifest — the eight R8 names — is v2: it declares the
    routing keys exactly as before, the runtime contract it is built
    against, its lifecycle roles, a settings schema and a settings hook its
    package really implements. Only the chat input declares triggers; it
    and the three capability modules declare one action each; the two
    transport modules declare a credential and no action. The manifests
    carry no key beyond those, so nothing is declared that the runtime
    does not read.
    """

    manifests = _manifests()

    names = [manifest["name"] for manifest in manifests.values()]
    assert len(names) == len(set(names))
    assert set(names) == set(MODULE_NAMES)
    assert len(names) == 8

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
            assert schema["properties"][setting_name]["type"] == "string"

        module = importlib.import_module(f"modules.{module_name}")
        assert callable(getattr(module, manifest["settings_validator"]))

    for manifest in manifests.values():
        assert type(manifest["manifest_version"]) is int
        assert type(manifest["middleware"]) is bool
    assert type(manifests["audit"]["order"]) is int
    assert isinstance(manifests["twitch"]["triggers"], Mapping)
    assert isinstance(manifests["twitch"]["actions"], list) and manifests["twitch"]["actions"]
    for module_name, action_name in DECLARED_ACTIONS.items():
        assert isinstance(manifests[module_name]["actions"], list), module_name
        assert _declared_action_names(manifests[module_name]) == {action_name}
    # R7: the proxy manifest itself declares no action, nor does the agent's
    # link — what they lend is declared by the capability modules.
    for module_name in ("proxy", "agent_link"):
        assert "actions" not in manifests[module_name]
    assert set(DECLARED_ACTIONS.values()) == PROVIDED_ACTIONS["pc"]


@pytest.mark.parametrize("profile", sorted(PROFILES))
def test_profile_enables_its_modules_and_contains_no_literal_credentials(
    profile: str,
) -> None:
    """R7, AC42 on each profile — replaces the former PC-only completeness
    test (allowlisted): the PC profile enables the six modules of R7, the
    server profile swaps ``capture`` for ``proxy``, the agent profile runs
    ``capture`` and ``agent_link``; every configured module is enabled and
    every enabled module configured; every credential a manifest declares
    is a ``${NAME}`` reference — 0 literal credentials — as is every
    ``secrets`` entry, and the expanded profile resolves each of them."""

    path = PROFILES[profile]
    config = _read_yaml(path)
    manifests = _manifests()

    assert isinstance(config.get("modules_directory"), str)
    assert config["modules_directory"].strip()
    assert config.get("enabled_modules") == ENABLED_MODULES[profile]
    if profile == "pc":
        assert len(config["enabled_modules"]) == 6
        assert set(config["enabled_modules"]) == {
            "twitch", "chat_context", "users", "capture", "brain", "audit"
        }

    modules = config.get("modules")
    assert isinstance(modules, Mapping)
    assert set(modules) == set(config["enabled_modules"])
    for module_name in config["enabled_modules"]:
        assert isinstance(modules[module_name], Mapping), module_name
        schema = manifests[module_name]["settings_schema"]
        if schema.get("required"):
            assert modules[module_name], module_name
            assert set(schema["required"]) <= set(modules[module_name]), module_name

    # AC42: every credential setting of every configured module is a
    # reference — read from the manifests, so a credential added to a
    # manifest later is checked here without a table change.
    referenced_variables: set[str] = set()
    for module_name in config["enabled_modules"]:
        declared = tuple(manifests[module_name].get("credentials", ()))
        assert declared == SECRET_SETTINGS.get(module_name, ())
        for setting_name in declared:
            value = modules[module_name].get(setting_name)
            assert isinstance(value, str), f"{module_name}.{setting_name}"
            match = ENV_REFERENCE.fullmatch(value)
            assert match is not None, f"{module_name}.{setting_name}"
            referenced_variables.add(match.group(1))
    assert referenced_variables

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
            resolved = expanded["modules"][module_name][setting_name]
            assert isinstance(resolved, str) and resolved
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
    brain URL the ``wss://`` form the agent link accepts off loopback (R6)."""

    if name.endswith("ENDPOINT"):
        return f"https://sample.invalid/{name.lower()}"
    if name.endswith("_URL"):
        return "wss://brain.example:8765"
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


async def _refuse_listener(handler: Any, **kwargs: Any) -> Any:
    raise AssertionError("unexpected listener beyond preparation")


async def _refuse_dial(*args: Any, **kwargs: Any) -> Any:
    raise AssertionError("unexpected dial beyond preparation")


def _point_seams_at_fakes(
    config: dict[str, Any], environ: Mapping[str, str], diagnostics: list[str]
) -> None:
    """Point every transport seam of the profile's modules at fakes.

    One entry per shipped module that owns an edge the process cannot own
    in a test: the chat input's session, the brain's model session (probe-
    aware), the audit writer, the capture module's screen (a
    :class:`FakeCaptureSource` handed in through its source seam, so no
    command runs and no path is read), the proxy's listener and the agent
    link's dial (both refused: neither is reached before ``start_inputs``,
    which these tests never call). A module the profile does not enable is
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
    }
    for module_name, extra in seams.items():
        if module_name in config["modules"]:
            config["modules"][module_name].update(extra)


async def _activate_example(
    environ: Mapping[str, str],
    path: Path = EXAMPLE_PATH,
) -> tuple[Any, list[Any], list[str]]:
    """Load the profile at *path* as ``core.main`` does, assemble the runtime
    from its limits and its authorization rules, and activate the enabled
    modules through the real loader — with the transport seams pointed at
    fakes, so nothing reaches a network — returning the runtime, the
    activations and the diagnostics the modules reported."""

    config = load_config(path, environ=environ)
    runtime = application._assemble_runtime(config)
    diagnostics: list[str] = []
    _point_seams_at_fakes(config, environ, diagnostics)

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


@pytest.mark.parametrize("profile", sorted(PROFILES))
async def test_profile_grants_exactly_its_provided_action_set(profile: str) -> None:
    """AC42 (R5, R7) on each profile — replaces the former PC-only grant test
    (allowlisted): the action names the rules grant equal the profile's
    provided-action set — PC and server exactly the four names, ``screen.capture``
    coming from the ``proxy`` allowlist and from no enabled manifest in the
    server file, from the ``capture`` manifest in the PC file; agent exactly
    ``screen.capture``. Then through the real loader: every granted action
    is bound by exactly one provider, the one decision 7 selects for the
    profile, and the view offered to the brain's principal on the served
    destinations is exactly the granted set — and nothing anywhere the rules
    do not reach: another channel, another scope, another principal.
    """

    path = PROFILES[profile]
    config = _read_yaml(path)
    manifests = _manifests()

    granted = _granted_actions(config)
    provided = _provided_actions(config)
    assert granted == provided == PROVIDED_ACTIONS[profile]
    assert len(granted) == len(config["actions"])
    declared_by_enabled = {
        name
        for module_name in config["enabled_modules"]
        for name in _declared_action_names(manifests[module_name])
    }
    if profile == "server":
        assert config["modules"]["proxy"]["actions"] == ["screen.capture"]
        assert "screen.capture" not in declared_by_enabled
        assert declared_by_enabled == {"chat.read", "users.read", "chat.write"}
    else:
        assert "proxy" not in config["enabled_modules"]
        assert granted == declared_by_enabled
        assert "screen.capture" in _declared_action_names(manifests["capture"])
    # Every grant is a rule for principal ``brain`` on its action's declared
    # scope, with the matching nature and permission (R5).
    for rule in config["actions"]:
        assert rule["principals"] == ["brain"]
        assert rule["granted_permissions"] == [rule["action_name"]]
        assert rule["natures"] == (["write"] if rule["action_name"] == "chat.write" else ["read"])
        assert rule["destination"]["scope"] == (
            "capture" if rule["action_name"] == "screen.capture" else "chat"
        )

    environ = _sample_environ(path)
    runtime, activations, diagnostics = await _activate_example(environ, path)
    try:
        registry = runtime.context.actions
        # Before preparation: declared, bound by nobody, offered to nobody.
        assert set(registry.discovered()) == declared_by_enabled
        assert registry.bindings() == ()
        assert registry.authorized(principal="brain") == {}

        await _prepare_all(activations)

        assert set(registry.discovered()) == provided
        for name in granted:
            bindings = registry.bindings(name)
            assert len(bindings) == 1, name
            if name == "screen.capture":
                assert bindings[0].provider_name == SCREEN_CAPTURE_PROVIDERS[profile]
                assert bindings[0].module == (
                    "proxy" if profile == "server" else "capture"
                )
        if profile == "server":
            # The remote provider becomes ready when an agent pairs
            # (tests/test_proxy.py); the grants, not the pairing, are under
            # test here, so readiness is given by hand to read the view.
            assert "screen.capture" not in registry.registered_ready()
            registry.mark_ready("proxy")

        if profile == "agent":
            platform, channel = "anyplatform", "any-channel"
        else:
            platform, channel = "twitch", environ["TWITCH_BROADCASTER_ID"]
        chat = Destination(platform, channel, "chat")
        capture = Destination(platform, channel, "capture")
        offered = set(registry.authorized(principal="brain", destination=chat)) | set(
            registry.authorized(principal="brain", destination=capture)
        )
        assert offered == granted
        assert set(registry.authorized(principal="brain", destination=capture)) == {"screen.capture"}
        assert "screen.capture" not in registry.authorized(principal="brain", destination=chat)
        if profile == "agent":
            # The agent's one rule is on ``*/*/capture``: any platform and
            # channel of the capture scope, that scope only.
            assert set(
                registry.authorized(principal="brain", destination=Destination("fake", "c", "capture"))
            ) == {"screen.capture"}
            assert registry.authorized(principal="brain", destination=chat) == {}
        else:
            for other in (
                Destination(platform, "another-channel", "chat"),
                Destination(platform, "another-channel", "capture"),
            ):
                assert registry.authorized(principal="brain", destination=other) == {}
        for principal in ("twitch", "audit", "capture", "proxy", "agent_link", "viewer"):
            assert registry.authorized(principal=principal, destination=chat) == {}
            assert registry.authorized(principal=principal, destination=capture) == {}
        assert diagnostics == []
    finally:
        await _close_all(activations)
