from __future__ import annotations

import importlib
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from core.main import LIMITS_KEY, load_config


ROOT = Path(__file__).parents[1]
EXAMPLE_PATH = ROOT / "config.yaml.example"
MODULE_NAMES = ("twitch", "brain", "audit")
ENV_REFERENCE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}\Z")

# The routing keys every manifest declares, v1 or v2. A v2 manifest carries
# its contract declarations beside them; those are asserted by the module's
# own suite, not by this cross-manifest coherence check.
EXPECTED_MANIFESTS = {
    "twitch": {
        "name": "twitch",
        "produces": ["channel.chat.message"],
        "consumes": ["channel.chat.send"],
        "middleware": False,
    },
    "brain": {
        "name": "brain",
        "produces": ["channel.chat.send"],
        "consumes": ["channel.chat.message"],
        "middleware": False,
    },
    "audit": {
        "name": "audit",
        "produces": [],
        "consumes": ["**"],
        "middleware": True,
        "order": 90,
    },
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


def test_manifests_are_unique_and_have_coherent_capabilities() -> None:
    """R7: the former whole-manifest equality is superseded by versioning.

    Every manifest still declares exactly the expected routing keys; a v2
    manifest (twitch, since R7 requires ``manifest_version``) additionally
    carries its contract declarations, checked here only for presence.
    """

    manifests = {
        module_name: _read_yaml(ROOT / "modules" / module_name / "module.yaml")
        for module_name in MODULE_NAMES
    }

    names = [manifest["name"] for manifest in manifests.values()]
    assert len(names) == len(set(names))
    assert set(names) == set(MODULE_NAMES)
    for module_name, expected in EXPECTED_MANIFESTS.items():
        manifest = manifests[module_name]
        assert {key: manifest.get(key) for key in expected} == expected
        if manifest.get("manifest_version") is None:
            assert manifest == expected
        else:
            assert manifest["manifest_version"] == 2
            assert set(expected) < set(manifest)
    assert manifests["twitch"]["manifest_version"] == 2

    for manifest in manifests.values():
        assert type(manifest["middleware"]) is bool
    assert type(manifests["audit"]["order"]) is int


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
