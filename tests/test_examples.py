"""The quick-start example and the shipped manifests (R1, R5, R6, R7).

The example is loaded through the same path ``core.main`` uses — never a
permissive parser — and the manifests are checked as the versioned runtime
reads them: every one is v2, declares the runtime contract it is built
against, its lifecycle roles, its settings schema and the hook its package
implements. The former whole-manifest equality (allowlisted; R7) is replaced
by this shape check: the routing keys stay exact, the contract declarations
are asserted for presence and coherence, and their detail belongs to each
module's own suite.
"""

from __future__ import annotations

import importlib
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

import core.main as application
from core.contracts import Destination
from core.lifecycle import ROLE_INPUT, ROLE_OBSERVATION
from core.loader import ModuleLoader
from core.main import LIMITS_KEY, load_config
from core.runtime import RUNTIME_API
from conftest import FakeResponse, FakeSession


ROOT = Path(__file__).parents[1]
EXAMPLE_PATH = ROOT / "config.yaml.example"
MODULE_NAMES = ("twitch", "brain", "audit")
ENV_REFERENCE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}\Z")

# What every v2 manifest declares exactly: the routing keys of v1, the
# version and runtime contract, the lifecycle roles the coordinator reads
# instead of the module name (R4), and the name of the settings hook the
# package implements (R7). The contract declarations beside them — the
# settings schema, the chat input's triggers and action — are asserted for
# presence here and in detail by the module's own suite.
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
}
# The declarations only the chat input carries (R1, R5).
DECLARATION_KEYS = {
    "twitch": {"triggers", "actions", "credentials"},
    "brain": {"credentials"},
    "audit": set(),
}

SECRET_SETTINGS = {
    "twitch": ("client_id", "client_secret", "access_token"),
    "brain": ("api_key",),
}
NON_SECRET_SETTINGS = {
    "twitch": ("broadcaster_id", "bot_user_id", "companion_name"),
    "brain": ("endpoint", "model"),
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


def test_manifests_are_unique_and_have_coherent_capabilities() -> None:
    """R7 supersedes the former whole-manifest equality (allowlisted).

    Every shipped manifest is v2: it declares the routing keys exactly as
    before, the runtime contract it is built against, its lifecycle roles,
    a settings schema and a settings hook its package really implements.
    Only the chat input declares triggers and an action; the manifests
    carry no key beyond those, so nothing is declared that the runtime
    does not read.
    """

    manifests = _manifests()

    names = [manifest["name"] for manifest in manifests.values()]
    assert len(names) == len(set(names))
    assert set(names) == set(MODULE_NAMES)

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


def test_example_config_is_complete_and_contains_no_literal_credentials() -> None:
    config = _read_yaml(EXAMPLE_PATH)

    assert isinstance(config.get("modules_directory"), str)
    assert config["modules_directory"].strip()
    assert config.get("enabled_modules") == list(MODULE_NAMES)

    modules = config.get("modules")
    assert isinstance(modules, Mapping)
    assert set(modules) == set(MODULE_NAMES)
    for module_name in MODULE_NAMES:
        assert isinstance(modules[module_name], Mapping)
        assert modules[module_name]

    referenced_variables: set[str] = set()
    for module_name, setting_names in SECRET_SETTINGS.items():
        for setting_name in setting_names:
            value = modules[module_name].get(setting_name)
            assert isinstance(value, str)
            match = ENV_REFERENCE.fullmatch(value)
            assert match is not None
            referenced_variables.add(match.group(1))

    for module_name, setting_names in NON_SECRET_SETTINGS.items():
        for setting_name in setting_names:
            _assert_sample_or_environment_reference(
                modules[module_name].get(setting_name)
            )

    environ = {
        variable: f"resolved-{index}"
        for index, variable in enumerate(sorted(referenced_variables), start=1)
    }
    for module_name, setting_names in NON_SECRET_SETTINGS.items():
        for setting_name in setting_names:
            value = modules[module_name][setting_name]
            match = ENV_REFERENCE.fullmatch(value)
            if match is not None:
                environ[match.group(1)] = f"sample-{module_name}-{setting_name}"

    expanded = load_config(EXAMPLE_PATH, environ=environ)
    assert Path(expanded["modules_directory"]) == ROOT / "modules"
    for module_name, setting_names in SECRET_SETTINGS.items():
        for setting_name in setting_names:
            assert not ENV_REFERENCE.fullmatch(
                expanded["modules"][module_name][setting_name]
            )


def test_example_config_carries_what_the_versioned_runtime_requires() -> None:
    """R1, R6: the quick-start example starts the versioned runtime.

    The chat input's v2 manifest declares triggers, which the loader refuses
    without a trigger engine, and its default policy names the companion by
    token, which resolves from that module's own settings. An example missing
    the limits block or the companion name would turn startup validation into
    a broken quick-start.
    """

    config = _read_yaml(EXAMPLE_PATH)

    limits = config.get(LIMITS_KEY)
    assert isinstance(limits, Mapping)
    assert limits
    for group, section in limits.items():
        assert isinstance(section, Mapping), group
        assert section, group
        for field_name, value in section.items():
            assert type(value) in (int, float), f"{group}.{field_name}"
            assert value > 0 and value != float("inf"), f"{group}.{field_name}"

    companion_name = config["modules"]["twitch"].get("companion_name")
    assert isinstance(companion_name, str)
    assert companion_name.strip()
    assert ENV_REFERENCE.fullmatch(companion_name) is None



def test_example_config_satisfies_every_module_owned_settings_validator() -> None:
    """R7: the quick-start example passes each module's own preflight.

    A module manifest may require settings beyond the credentials — the brain
    requires its admission, budget and conversation memory limits — and the
    loader runs the module's ``settings_validator`` on ``modules.<name>`` as
    shipped, without merging the top-level ``limits`` block into it. An
    example that satisfied the core's limits but not a module's would refuse
    to start with valid credentials, so every validator is run here on the
    expanded example with sample values for its references.
    """

    config = _read_yaml(EXAMPLE_PATH)
    environ: dict[str, str] = {}
    for module_settings in config["modules"].values():
        for setting_name, value in module_settings.items():
            if isinstance(value, str):
                match = ENV_REFERENCE.fullmatch(value)
                if match is not None:
                    environ[match.group(1)] = f"https://sample.invalid/{setting_name}"
    expanded = load_config(EXAMPLE_PATH, environ=environ)

    for module_name in MODULE_NAMES:
        manifest = _read_yaml(ROOT / "modules" / module_name / "module.yaml")
        hook_name = manifest.get("settings_validator")
        if hook_name is None:
            continue
        module = importlib.import_module(f"modules.{module_name}")
        validator = getattr(module, hook_name)
        assert validator(expanded["modules"][module_name]) == [], module_name


# --------------------------------------------------------------------------- #
# The example through the real assembly and the real loader (R1, R5, R6)
# --------------------------------------------------------------------------- #


def _referenced_variables(value: Any) -> set[str]:
    """Every ``${NAME}`` the example references, in values and mapping keys."""

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


def _sample_environ() -> dict[str, str]:
    """A sample value per referenced variable; endpoints get a URL shape."""

    return {
        name: (
            f"https://sample.invalid/{name.lower()}"
            if name.endswith("ENDPOINT")
            else f"sample-{name.lower()}"
        )
        for name in _referenced_variables(_read_yaml(EXAMPLE_PATH))
    }


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


async def _activate_example(
    environ: Mapping[str, str],
) -> tuple[Any, list[Any], list[str]]:
    """Load the example as ``core.main`` does, assemble the runtime from its
    limits and its authorization rules, and activate the enabled modules
    through the real loader — with the transport seams pointed at fakes, so
    nothing reaches a network — returning the runtime, the activations and
    the diagnostics the modules reported."""

    config = load_config(EXAMPLE_PATH, environ=environ)
    runtime = application._assemble_runtime(config)
    diagnostics: list[str] = []
    config["modules"]["twitch"].update(
        {
            "_session_factory": lambda: _TwitchSession(environ),
            "diagnostic_reporter": diagnostics.append,
        }
    )
    config["modules"]["brain"].update(
        {"_session_factory": FakeSession, "diagnostic_reporter": diagnostics.append}
    )
    config["modules"]["audit"]["_writer"] = lambda line: None

    loader = ModuleLoader(runtime.bus, config["modules_directory"])
    loader.context = runtime.context
    loader.environ = environ
    activations = await loader.activate_enabled(config)
    return runtime, activations, diagnostics


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


async def test_example_trigger_policies_give_the_two_channels_two_distinct_policies() -> None:
    """AC1 (R1) on the example: the served channel carries an explicit
    ``keyword`` rule and accepts exactly 1 of 2 messages — the one with the
    keyword, not the one that merely mentions the companion — while a
    channel with no entry falls back to the manifest default and accepts
    only the 1 message of 2 that mentions the configured companion name:
    2 accepted and 2 rejected decisions, from one registry in one process."""

    environ = _sample_environ()
    runtime, activations, diagnostics = await _activate_example(environ)
    try:
        engine = runtime.context.triggers
        served = environ["TWITCH_BROADCASTER_ID"]
        other = "another-channel"
        companion = _read_yaml(EXAMPLE_PATH)["modules"]["twitch"]["companion_name"]
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
        for activation in reversed(activations):
            await activation.handle.close()


async def test_example_authorization_grants_exactly_the_actions_the_modules_declare() -> None:
    """R5 on the example: once the chat input has bound its provider, the
    authorized view offered to the run engine's principal on the served
    channel is exactly the set of actions the enabled manifests declare —
    and nothing anywhere the example's rules do not reach: another channel,
    another principal."""

    environ = _sample_environ()
    runtime, activations, diagnostics = await _activate_example(environ)
    try:
        registry = runtime.context.actions
        declared = set(registry.discovered())
        assert declared == {"chat.write"}
        assert registry.authorized(principal="brain") == {}

        (twitch,) = [activation for activation in activations if activation.name == "twitch"]
        await twitch.handle.prepare()
        served = Destination("twitch", environ["TWITCH_BROADCASTER_ID"], "chat")

        assert set(registry.authorized(principal="brain", destination=served)) == declared
        assert registry.authorized(principal="brain", destination=Destination("twitch", "another-channel", "chat")) == {}
        assert registry.authorized(principal="twitch", destination=served) == {}
        assert registry.authorized(principal="audit", destination=served) == {}

        # The rules grant exactly the declared actions: no rule names an
        # action nobody declares.
        rules = _read_yaml(EXAMPLE_PATH)["actions"]
        assert {rule["action_name"] for rule in rules} == declared
        assert diagnostics == []
    finally:
        for activation in reversed(activations):
            await activation.handle.close()
