from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from core.main import load_config


ROOT = Path(__file__).parents[1]
EXAMPLE_PATH = ROOT / "config.yaml.example"
MODULE_NAMES = ("twitch", "brain", "audit")
ENV_REFERENCE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}\Z")

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
    "twitch": ("broadcaster_id", "bot_user_id"),
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
    manifests = {
        module_name: _read_yaml(ROOT / "modules" / module_name / "module.yaml")
        for module_name in MODULE_NAMES
    }

    names = [manifest["name"] for manifest in manifests.values()]
    assert len(names) == len(set(names))
    assert set(names) == set(MODULE_NAMES)
    assert manifests == EXPECTED_MANIFESTS

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
