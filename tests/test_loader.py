from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
import yaml

from core.actions import ActionRegistry, AuthorizationPolicy, AuthorizationRule
from core.bus import EventBus
from core.contracts import ContractError, TriggerPolicy, TriggerRule
from core.lifecycle import SupervisedTasks, module_roles
from core.loader import ModuleLoadError, ModuleLoader
from core.runtime import RUNTIME_API, ModuleContext, RuntimeContext, Supervision
from core.triggers import TriggerEngine, TriggerRegistry


MODULE_SOURCE = """
class Handle:
    def __init__(self, settings):
        self.settings = settings

    async def close(self):
        self.settings["closes"].append(self.settings["label"])


async def activate(bus, settings, catalog):
    settings["calls"].append((bus, settings, catalog))
    return Handle(settings)
"""


def make_module(
    root: Path,
    directory_name: str,
    *,
    manifest_name: str | None = None,
    manifest: dict | None = None,
    source: str = MODULE_SOURCE,
) -> Path:
    directory = root / directory_name
    directory.mkdir()
    data = manifest or {
        "name": manifest_name or directory_name,
        "produces": ["channel.chat.message"],
        "consumes": ["channel.*"],
        "middleware": False,
    }
    (directory / "module.yaml").write_text(
        yaml.safe_dump(data), encoding="utf-8"
    )
    (directory / "__init__.py").write_text(source, encoding="utf-8")
    return directory


@pytest.mark.asyncio
async def test_discovers_all_modules_and_activates_only_enabled(tmp_path: Path) -> None:
    make_module(tmp_path, "enabled")
    make_module(
        tmp_path,
        "disabled",
        source="async def activate(*args):\n    raise AssertionError('disabled')\n",
    )
    bus = object()
    enabled_settings = {"label": "enabled", "calls": [], "closes": []}
    disabled_settings = {"unused": True}
    loader = ModuleLoader(bus, tmp_path)

    activations = await loader.activate_enabled(
        {
            "enabled_modules": ["enabled"],
            "modules": {
                "enabled": enabled_settings,
                "disabled": disabled_settings,
            },
        }
    )

    assert list(loader.discovered) == ["disabled", "enabled"]
    assert set(loader.catalog) == {"disabled", "enabled"}
    assert [activation.name for activation in activations] == ["enabled"]
    assert len(enabled_settings["calls"]) == 1
    received_bus, received_settings, received_catalog = enabled_settings["calls"][0]
    assert received_bus is bus
    assert received_settings is enabled_settings
    assert received_catalog is loader.catalog
    assert set(received_catalog) == {"disabled", "enabled"}


@pytest.mark.asyncio
async def test_activation_close_is_idempotent_even_when_called_concurrently(
    tmp_path: Path,
) -> None:
    make_module(tmp_path, "one")
    settings = {"label": "one", "calls": [], "closes": []}
    activation = (
        await ModuleLoader(object(), tmp_path).load(
            {"enabled_modules": ["one"], "modules": {"one": settings}}
        )
    )[0]

    await asyncio.gather(activation.close(), activation.close(), activation.close())

    assert activation.closed is True
    assert settings["closes"] == ["one"]


def _base_config(*names: str) -> dict:
    return {
        "enabled_modules": list(names),
        "modules": {
            name: {"label": name, "calls": [], "closes": []} for name in names
        },
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case", "field"),
    [
        ("duplicate_name", "name"),
        ("non_list_produces", "produces"),
        ("missing_middleware", "middleware"),
        ("non_bool_middleware", "middleware"),
        ("non_int_order", "order"),
        ("bool_order", "order"),
        ("invalid_pattern", "consumes"),
        ("unknown_enabled", "enabled_modules"),
        ("missing_enabled_settings", "modules"),
        ("unknown_settings", "modules"),
    ],
)
async def test_validation_failure_prevents_every_activation(
    tmp_path: Path, case: str, field: str
) -> None:
    calls: list = []
    source = """
async def activate(bus, settings, catalog):
    settings["calls"].append("activated")
    raise AssertionError("validation should have happened first")
"""
    make_module(tmp_path, "valid", source=source)
    config = {"enabled_modules": ["valid"], "modules": {"valid": {"calls": calls}}}

    if case == "duplicate_name":
        make_module(tmp_path, "duplicate", manifest_name="valid")
    elif case == "non_list_produces":
        make_module(
            tmp_path,
            "broken",
            manifest={
                "name": "broken",
                "produces": "channel.chat.message",
                "consumes": [],
                "middleware": False,
            },
        )
    elif case == "missing_middleware":
        make_module(
            tmp_path,
            "broken",
            manifest={"name": "broken", "produces": [], "consumes": []},
        )
    elif case == "non_bool_middleware":
        make_module(
            tmp_path,
            "broken",
            manifest={
                "name": "broken",
                "produces": [],
                "consumes": [],
                "middleware": "false",
            },
        )
    elif case in {"non_int_order", "bool_order"}:
        make_module(
            tmp_path,
            "broken",
            manifest={
                "name": "broken",
                "produces": [],
                "consumes": ["**"],
                "middleware": True,
                "order": "90" if case == "non_int_order" else True,
            },
        )
    elif case == "invalid_pattern":
        make_module(
            tmp_path,
            "broken",
            manifest={
                "name": "broken",
                "produces": [],
                "consumes": ["channel*"],
                "middleware": False,
            },
        )
    elif case == "unknown_enabled":
        config["enabled_modules"].append("missing")
        config["modules"]["missing"] = {}
    elif case == "missing_enabled_settings":
        config["modules"].pop("valid")
    elif case == "unknown_settings":
        config["modules"]["missing"] = {}

    with pytest.raises(ModuleLoadError) as caught:
        await ModuleLoader(object(), tmp_path).load(config)

    assert field in str(caught.value)
    assert calls == []


@pytest.mark.asyncio
async def test_non_module_directories_are_ignored(tmp_path: Path) -> None:
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / ".git").mkdir()
    make_module(tmp_path, "enabled")
    settings = {"label": "enabled", "calls": [], "closes": []}

    activations = await ModuleLoader(object(), tmp_path).activate_enabled(
        {"enabled_modules": ["enabled"], "modules": {"enabled": settings}}
    )

    assert [activation.name for activation in activations] == ["enabled"]


@pytest.mark.asyncio
async def test_symlinked_module_directory_is_not_discovered(tmp_path: Path) -> None:
    modules_root = tmp_path / "modules"
    modules_root.mkdir()
    outside = tmp_path / "outside"
    make_module(outside.parent, outside.name)
    (modules_root / "linked").symlink_to(outside, target_is_directory=True)

    loader = ModuleLoader(object(), modules_root)
    activations = await loader.activate_enabled(
        {"enabled_modules": [], "modules": {}}
    )

    assert activations == []
    assert loader.discovered == {}


@pytest.mark.asyncio
async def test_entry_point_symlink_cannot_escape_module_directory(
    tmp_path: Path,
) -> None:
    module = make_module(tmp_path, "linked_init")
    external_source = tmp_path / "external.py"
    external_source.write_text(MODULE_SOURCE, encoding="utf-8")
    (module / "__init__.py").unlink()
    (module / "__init__.py").symlink_to(external_source)

    with pytest.raises(ModuleLoadError, match="stay inside"):
        await ModuleLoader(object(), tmp_path).activate_enabled(
            _base_config("linked_init")
        )


@pytest.mark.asyncio
async def test_failed_entry_point_validation_does_not_leak_imports(
    tmp_path: Path,
) -> None:
    module = make_module(
        tmp_path,
        "invalid_entry",
        source="from . import helper\nVALUE = helper.VALUE\n",
    )
    (module / "helper.py").write_text("VALUE = 1\n", encoding="utf-8")
    before = {name for name in sys.modules if name.startswith("_companion_module_")}

    with pytest.raises(ModuleLoadError, match="activate"):
        await ModuleLoader(object(), tmp_path).activate_enabled(
            _base_config("invalid_entry")
        )

    after = {name for name in sys.modules if name.startswith("_companion_module_")}
    assert after == before


@pytest.mark.asyncio
async def test_invalid_close_hook_remains_in_partial_activation_set(
    tmp_path: Path,
) -> None:
    make_module(
        tmp_path,
        "invalid_close",
        source="""
class Handle:
    pass

async def activate(bus, settings, catalog):
    settings["activated"] = True
    return Handle()
""",
    )
    loader = ModuleLoader(object(), tmp_path)
    settings: dict = {}

    with pytest.raises(ModuleLoadError, match="close"):
        await loader.activate_enabled(
            {
                "enabled_modules": ["invalid_close"],
                "modules": {"invalid_close": settings},
            }
        )

    assert settings["activated"] is True
    assert [activation.name for activation in loader.activations] == [
        "invalid_close"
    ]


@pytest.mark.asyncio
async def test_capability_catalog_is_recursively_read_only(tmp_path: Path) -> None:
    make_module(tmp_path, "one")
    loader = ModuleLoader(object(), tmp_path)
    settings = {"label": "one", "calls": [], "closes": []}

    await loader.activate_enabled(
        {"enabled_modules": ["one"], "modules": {"one": settings}}
    )

    with pytest.raises(TypeError):
        loader.catalog["two"] = {}  # type: ignore[index]
    with pytest.raises(TypeError):
        loader.catalog["one"]["name"] = "renamed"  # type: ignore[index]
    assert loader.catalog["one"]["produces"] == ("channel.chat.message",)


@pytest.mark.asyncio
async def test_arbitrary_filesystem_module_needs_no_core_registration(
    tmp_path: Path,
) -> None:
    # Naming the directory after a standard-library package catches accidental
    # package-name imports instead of loading this exact __init__.py file.
    make_module(tmp_path, "json", manifest_name="fourth_party")
    settings = {
        "label": "fourth_party",
        "calls": [],
        "closes": [],
    }

    activations = await ModuleLoader(object(), tmp_path).load(
        {
            "enabled_modules": ["fourth_party"],
            "modules": {"fourth_party": settings},
        }
    )

    assert [activation.name for activation in activations] == ["fourth_party"]
    assert len(settings["calls"]) == 1


@pytest.mark.asyncio
async def test_import_and_activation_errors_are_sanitized(tmp_path: Path) -> None:
    make_module(tmp_path, "broken_import", source="raise RuntimeError('secret-value')")
    with pytest.raises(ModuleLoadError) as imported:
        await ModuleLoader(object(), tmp_path).load(
            _base_config("broken_import")
        )
    assert "broken_import" in str(imported.value)
    assert "__init__.py" in str(imported.value)
    assert "secret-value" not in str(imported.value)

    other_root = tmp_path / "other"
    other_root.mkdir()
    make_module(
        other_root,
        "broken_activation",
        source="async def activate(*args):\n    raise RuntimeError('secret-value')\n",
    )
    with pytest.raises(ModuleLoadError) as activated:
        await ModuleLoader(object(), other_root).load(
            _base_config("broken_activation")
        )
    assert "broken_activation" in str(activated.value)
    assert "activate" in str(activated.value)
    assert "secret-value" not in str(activated.value)


# --------------------------------------------------------------------------- #
# Manifest v2, the v1 compatibility route, and declared actions/triggers
# (R7, R5, R4 — AC25, AC26)
# --------------------------------------------------------------------------- #

V2_MODULE_SOURCE = """
from core.contracts import ActionSpec, Destination


class Provider:
    name = "recorder"

    async def invoke(self, invocation):
        raise AssertionError("no call is made during activation")


class Handle:
    def __init__(self, settings):
        self.settings = settings

    async def prepare(self):
        self.settings["prepared"] = True

    async def start_inputs(self):
        self.settings["started"] = True

    async def close(self):
        self.settings["closes"].append(self.settings["label"])


def validate_settings(settings):
    settings["validated"] = True


async def activate(context, settings, catalog):
    settings["calls"].append((context, settings, catalog))
    settings["module"] = context.module
    settings["runtime_api"] = context.runtime_api
    settings["bus"] = context.bus
    context.actions.bind(
        "chat.write",
        Provider(),
        destinations=Destination("twitch", "*", "chat"),
    )
    context.actions.mark_ready()
    return Handle(settings)
"""


def v2_manifest(
    name: str,
    *,
    roles: list[str] | None = None,
    actions: list[dict] | None = None,
    triggers: dict | None = None,
    settings_schema: dict | None = None,
    settings_validator: str | None = None,
    runtime_api: int = RUNTIME_API,
    manifest_version: int = 2,
) -> dict:
    """A minimal v2 manifest, with only the declarations a test cares about."""

    manifest: dict = {
        "name": name,
        "manifest_version": manifest_version,
        "runtime_api": runtime_api,
        "produces": [],
        "consumes": ["channel.*"],
        "middleware": False,
    }
    if roles is not None:
        manifest["lifecycle"] = {"roles": roles}
    if actions is not None:
        manifest["actions"] = actions
    if triggers is not None:
        manifest["triggers"] = triggers
    if settings_schema is not None:
        manifest["settings_schema"] = settings_schema
    if settings_validator is not None:
        manifest["settings_validator"] = settings_validator
    return manifest


CHAT_WRITE_ACTION = {
    "name": "chat.write",
    "version": 1,
    "description": "Send one chat message",
    "argument_schema": {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
        "additionalProperties": False,
    },
    "result_schema": {
        "type": "object",
        "properties": {"delivered": {"type": "boolean"}},
        "required": ["delivered"],
    },
    "nature": "write",
    "required_permissions": ["chat.write"],
    "supported_destinations": [
        {"platform": "twitch", "channel_id": "*", "scope": "chat"}
    ],
    "timeout_seconds": 5,
    "idempotency": "none",
}

KEYWORD_TRIGGERS = {
    "types": [
        {
            "name": "keyword",
            "parameter_schema": {
                "type": "object",
                "properties": {
                    "keywords": {"type": "array", "items": {"type": "string"}}
                },
                "required": ["keywords"],
                "additionalProperties": False,
            },
        }
    ],
    "combinations": ["all_of", "any_of"],
    "default_policy": {
        "combination": "all_of",
        "rules": [{"type": "keyword", "parameters": {"keywords": ["${companion_name}"]}}],
    },
}


def runtime_context(**overrides: object) -> RuntimeContext:
    """A runtime context over a real bus, a real registry and real supervision."""

    bus = overrides.pop("bus", None) or EventBus()
    authorization = overrides.pop("authorization", None) or AuthorizationPolicy()
    fields: dict = {
        "bus": bus,
        "actions": ActionRegistry(authorization=authorization),
        "supervision": Supervision(bus),
        "tasks": SupervisedTasks(),
        "triggers": TriggerEngine(
            TriggerRegistry(companion_name="companion"),
            dedup_max_entries=8,
            dedup_ttl_seconds=60.0,
        ),
    }
    fields.update(overrides)
    return RuntimeContext(**fields)


def v2_settings(label: str = "v2") -> dict:
    return {"label": label, "calls": [], "closes": []}


@pytest.mark.asyncio
async def test_v2_module_receives_the_runtime_context_and_registers_its_actions(
    tmp_path: Path,
) -> None:
    """AC26: a manifest_version 2 module is handed the context and declares through it."""

    make_module(
        tmp_path,
        "chat",
        manifest=v2_manifest("chat", roles=["input"], actions=[CHAT_WRITE_ACTION]),
        source=V2_MODULE_SOURCE,
    )
    context = runtime_context()
    settings = v2_settings("chat")
    loader = ModuleLoader(context.bus, tmp_path, context=context)

    activations = await loader.activate_enabled(
        {"enabled_modules": ["chat"], "modules": {"chat": settings}}
    )

    # The first activation argument is the module's scoped context, not the bus.
    assert len(settings["calls"]) == 1
    received_context, received_settings, received_catalog = settings["calls"][0]
    assert isinstance(received_context, ModuleContext)
    assert received_context.module == "chat"
    assert received_settings is settings
    assert received_catalog is loader.catalog
    assert settings["runtime_api"] == RUNTIME_API
    assert settings["bus"] is context.bus

    # The declared action is discovered, and the provider bound through the
    # scoped facade is bound under this module's name.
    assert set(context.actions.discovered()) == {"chat.write"}
    assert [binding.module for binding in context.actions.bindings()] == ["chat"]
    assert set(context.actions.registered_ready()) == {"chat.write"}

    # The declared phase roles travel on the activation, never a name lookup.
    activation = activations[0]
    assert activation.roles == frozenset({"input"})
    assert activation.manifest_version == 2
    assert module_roles(activation) == frozenset({"input"})


@pytest.mark.asyncio
async def test_module_without_manifest_version_keeps_the_v1_route(
    tmp_path: Path,
) -> None:
    """AC26: no manifest_version means activate(bus, settings, catalog) and close()."""

    make_module(tmp_path, "legacy")
    context = runtime_context()
    settings = {"label": "legacy", "calls": [], "closes": []}
    loader = ModuleLoader(context.bus, tmp_path, context=context)

    activations = await loader.activate_enabled(
        {"enabled_modules": ["legacy"], "modules": {"legacy": settings}}
    )

    # Even with a context available, a v1 manifest is handed the bare bus.
    received_bus, received_settings, received_catalog = settings["calls"][0]
    assert received_bus is context.bus
    assert not isinstance(received_bus, ModuleContext)
    assert received_settings is settings
    assert received_catalog is loader.catalog
    assert activations[0].manifest_version == 1
    assert activations[0].roles == frozenset()

    # The v1 route is the one that still owns close(): the transport a v1
    # module opened is released by that call and by nothing else.
    await activations[0].close()
    assert settings["closes"] == ["legacy"]


@pytest.mark.asyncio
async def test_activation_adds_no_attribute_to_the_bus(tmp_path: Path) -> None:
    """AC26: comparing the bus attribute set before and after shows 0 additions."""

    make_module(tmp_path, "legacy")
    make_module(
        tmp_path,
        "chat",
        manifest=v2_manifest("chat", actions=[CHAT_WRITE_ACTION]),
        source=V2_MODULE_SOURCE,
    )
    bus = EventBus()
    context = runtime_context(bus=bus)
    before = set(dir(bus)) | set(vars(bus))

    await ModuleLoader(bus, tmp_path, context=context).activate_enabled(
        {
            "enabled_modules": ["legacy", "chat"],
            "modules": {
                "legacy": {"label": "legacy", "calls": [], "closes": []},
                "chat": v2_settings("chat"),
            },
        }
    )

    after = set(dir(bus)) | set(vars(bus))
    assert after - before == set()
    assert after == before


@pytest.mark.asyncio
async def test_disabled_module_manifest_is_validated_but_its_secrets_are_not(
    tmp_path: Path, monkeypatch
) -> None:
    """AC25: an invalid disabled manifest stops startup; an unresolved disabled secret does not."""

    broken_root = tmp_path / "broken"
    broken_root.mkdir()
    make_module(broken_root, "enabled")
    make_module(
        broken_root,
        "disabled",
        manifest={"name": "disabled", "produces": [], "consumes": []},
    )
    config = {
        "enabled_modules": ["enabled"],
        "modules": {
            "enabled": {"label": "enabled", "calls": [], "closes": []},
            "disabled": {"api_key": "${COMPANION_ABSENT}"},
        },
    }

    with pytest.raises(ModuleLoadError) as caught:
        await ModuleLoader(object(), broken_root).activate_enabled(config)
    assert "'disabled'" in str(caught.value)
    assert "middleware" in str(caught.value)

    # Same disabled module, this time with a valid manifest: its unresolvable
    # secret is never looked up, so the application starts.
    valid_root = tmp_path / "valid"
    valid_root.mkdir()
    make_module(valid_root, "enabled")
    make_module(valid_root, "disabled")
    settings = {"label": "enabled", "calls": [], "closes": []}
    loader = ModuleLoader(
        object(),
        valid_root,
        environ={},
    )

    activations = await loader.activate_enabled(
        {
            "enabled_modules": ["enabled"],
            "modules": {
                "enabled": settings,
                "disabled": {"api_key": "${COMPANION_ABSENT}"},
            },
        }
    )

    assert [activation.name for activation in activations] == ["enabled"]
    assert loader.resolved_secrets == 0
    assert set(loader.discovered) == {"disabled", "enabled"}


@pytest.mark.asyncio
async def test_enabled_module_secret_is_resolved_and_an_absent_one_stops_startup(
    tmp_path: Path,
) -> None:
    """The enabled half of AC25: its references are resolved, and counted."""

    make_module(tmp_path, "enabled")
    settings = {"label": "enabled", "calls": [], "closes": [], "api_key": "${TOKEN}"}
    loader = ModuleLoader(object(), tmp_path, environ={"TOKEN": "s3cret"})

    await loader.activate_enabled(
        {"enabled_modules": ["enabled"], "modules": {"enabled": settings}}
    )

    assert loader.resolved_secrets == 1
    resolved = settings["calls"][0][1]
    assert resolved is not settings
    assert resolved["api_key"] == "s3cret"

    other = ModuleLoader(object(), tmp_path, environ={})
    with pytest.raises(ModuleLoadError) as caught:
        await other.activate_enabled(
            {
                "enabled_modules": ["enabled"],
                "modules": {"enabled": {"calls": [], "api_key": "${TOKEN}"}},
            }
        )
    assert "'enabled'" in str(caught.value)
    assert "api_key" in str(caught.value)
    assert "s3cret" not in str(caught.value)
    assert other.resolved_secrets == 0


@pytest.mark.asyncio
async def test_declared_action_is_discovered_but_never_authorized_by_declaring(
    tmp_path: Path,
) -> None:
    """R7/R5: declaring an action, a capability or a produced event grants nothing."""

    make_module(
        tmp_path,
        "chat",
        manifest=v2_manifest(
            "chat",
            actions=[CHAT_WRITE_ACTION, {**CHAT_WRITE_ACTION, "name": "chat.read", "nature": "read"}],
        ),
        source=V2_MODULE_SOURCE,
    )
    authorization = AuthorizationPolicy()
    context = runtime_context(authorization=authorization)

    await ModuleLoader(context.bus, tmp_path, context=context).activate_enabled(
        {"enabled_modules": ["chat"], "modules": {"chat": v2_settings("chat")}}
    )

    registry = context.actions
    assert set(registry.discovered()) == {"chat.write", "chat.read"}
    # A bound, ready provider with no applicable rule is still not authorized,
    # and the read nature is refused exactly like the write.
    assert set(registry.registered_ready()) == {"chat.write"}
    assert dict(registry.authorized(principal="viewer")) == {}

    authorization.grant(
        AuthorizationRule(
            rule_id="chat-write",
            action_name="chat.write",
            granted_permissions=("chat.write",),
        )
    )
    assert set(registry.authorized(principal="viewer")) == {"chat.write"}


@pytest.mark.asyncio
async def test_declared_triggers_reach_the_trigger_registry(tmp_path: Path) -> None:
    """R1/R7: a declared TriggerSpec is registered under the module's input name."""

    make_module(
        tmp_path,
        "chat",
        manifest=v2_manifest(
            "chat", triggers=KEYWORD_TRIGGERS, actions=[CHAT_WRITE_ACTION]
        ),
        source=V2_MODULE_SOURCE,
    )
    context = runtime_context()

    await ModuleLoader(context.bus, tmp_path, context=context).activate_enabled(
        {"enabled_modules": ["chat"], "modules": {"chat": v2_settings("chat")}}
    )

    registry = context.triggers.registry
    assert registry.inputs() == ("chat",)
    spec = registry.spec("chat")
    assert spec.declares("keyword")
    assert spec.combinations == ("all_of", "any_of")
    # An undeclared type is refused against that very declaration.
    with pytest.raises(ContractError):
        registry.validate_policy(
            "chat",
            TriggerPolicy(
                rules=(TriggerRule(type="probability", parameters={"probability": 1}),)
            ),
        )


@pytest.mark.asyncio
async def test_settings_schema_and_declared_hook_run_before_any_activation(
    tmp_path: Path,
) -> None:
    """R7: every enabled module validates its own settings before any is activated."""

    schema = {
        "type": "object",
        "properties": {"channel": {"type": "string", "enum": ["general", "vip"]}},
        "required": ["channel"],
    }
    make_module(
        tmp_path,
        "chat",
        manifest=v2_manifest(
            "chat",
            settings_schema=schema,
            settings_validator="validate_settings",
            actions=[CHAT_WRITE_ACTION],
        ),
        source=V2_MODULE_SOURCE,
    )
    make_module(
        tmp_path,
        "other",
        manifest=v2_manifest("other"),
        source=V2_MODULE_SOURCE.replace(
            '    context.actions.bind(\n'
            '        "chat.write",\n'
            '        Provider(),\n'
            '        destinations=Destination("twitch", "*", "chat"),\n'
            '    )\n',
            "",
        ),
    )
    context = runtime_context()
    chat = {**v2_settings("chat"), "channel": "general"}
    other = v2_settings("other")

    await ModuleLoader(context.bus, tmp_path, context=context).activate_enabled(
        {
            "enabled_modules": ["chat", "other"],
            "modules": {"chat": chat, "other": other},
        }
    )
    assert chat["validated"] is True

    # A settings mapping the declared schema refuses stops startup with 0
    # activations, naming the module and the field and quoting no value.
    context = runtime_context()
    chat = {**v2_settings("chat"), "channel": "s3cret"}
    other = v2_settings("other")
    with pytest.raises(ModuleLoadError) as caught:
        await ModuleLoader(context.bus, tmp_path, context=context).activate_enabled(
            {
                "enabled_modules": ["chat", "other"],
                "modules": {"chat": chat, "other": other},
            }
        )
    assert "'chat'" in str(caught.value)
    assert "channel" in str(caught.value)
    # The rejected value is never quoted back: it may be the credential (AC24).
    assert "s3cret" not in str(caught.value)
    assert chat["calls"] == []
    assert other["calls"] == []


@pytest.mark.asyncio
async def test_declared_settings_hook_the_module_does_not_define_stops_startup(
    tmp_path: Path,
) -> None:
    """R7: an unresolvable hook is a startup failure naming the module, never 'valid'."""

    make_module(
        tmp_path,
        "chat",
        manifest=v2_manifest("chat", settings_validator="check_settings"),
        source=V2_MODULE_SOURCE,
    )
    context = runtime_context()
    settings = v2_settings("chat")

    with pytest.raises(ModuleLoadError) as caught:
        await ModuleLoader(context.bus, tmp_path, context=context).activate_enabled(
            {"enabled_modules": ["chat"], "modules": {"chat": settings}}
        )

    assert "'chat'" in str(caught.value)
    assert "settings_validator" in str(caught.value)
    assert settings["calls"] == []


LEAKY_VALIDATOR_SOURCE = V2_MODULE_SOURCE.replace(
    'def validate_settings(settings):\n    settings["validated"] = True\n',
    "def validate_settings(settings):\n"
    "    from core.loader import ModuleLoadError\n"
    "\n"
    "    raise ModuleLoadError(settings['api_key'])\n",
)


@pytest.mark.asyncio
async def test_settings_hook_cannot_echo_the_rejected_credential(
    tmp_path: Path,
) -> None:
    """R7/AC24: what the hook raises never becomes the diagnostic, whatever its type."""

    make_module(
        tmp_path,
        "chat",
        manifest=v2_manifest("chat", settings_validator="validate_settings"),
        source=LEAKY_VALIDATOR_SOURCE,
    )
    context = runtime_context()
    settings = {**v2_settings("chat"), "api_key": "s3cret"}

    with pytest.raises(ModuleLoadError) as caught:
        await ModuleLoader(context.bus, tmp_path, context=context).activate_enabled(
            {"enabled_modules": ["chat"], "modules": {"chat": settings}}
        )

    # A hook raising the loader's own error type is not a pass-through: the
    # module was handed the settings, so its message may be the credential.
    assert "s3cret" not in str(caught.value)
    assert "'chat'" in str(caught.value)
    assert "validate_settings" in str(caught.value)
    assert settings["calls"] == []


@pytest.mark.asyncio
async def test_schema_diagnostic_drops_a_value_that_contains_the_separator(
    tmp_path: Path,
) -> None:
    """R7/AC24: the observed value is cut whole, not down to its first ', got '."""

    make_module(
        tmp_path,
        "chat",
        manifest=v2_manifest(
            "chat",
            settings_schema={
                "type": "object",
                "properties": {"channel": {"type": "string", "enum": ["general"]}},
                "required": ["channel"],
            },
        ),
        source=V2_MODULE_SOURCE,
    )
    context = runtime_context()
    settings = {**v2_settings("chat"), "channel": "s3cret, got tail"}

    with pytest.raises(ModuleLoadError) as caught:
        await ModuleLoader(context.bus, tmp_path, context=context).activate_enabled(
            {"enabled_modules": ["chat"], "modules": {"chat": settings}}
        )

    assert "channel" in str(caught.value)
    assert "s3cret" not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("declared", ["read", False, 0, {"chat.write": True}])
async def test_permission_declaration_that_is_not_a_list_is_a_manifest_error(
    tmp_path: Path, declared: object
) -> None:
    """R5/R7: a malformed permission requirement is refused, never normalised away.

    ``"read"`` must not become four single-letter permissions and ``False``
    must not become an empty requirement: both would change what a rule has to
    grant for the action to be permitted.
    """

    make_module(
        tmp_path,
        "chat",
        manifest=v2_manifest(
            "chat",
            actions=[{**CHAT_WRITE_ACTION, "required_permissions": declared}],
        ),
        source=V2_MODULE_SOURCE,
    )
    context = runtime_context()
    settings = v2_settings("chat")

    with pytest.raises(ModuleLoadError) as caught:
        await ModuleLoader(context.bus, tmp_path, context=context).activate_enabled(
            {"enabled_modules": ["chat"], "modules": {"chat": settings}}
        )

    assert "'chat'" in str(caught.value)
    assert "required_permissions" in str(caught.value)
    assert settings["calls"] == []


@pytest.mark.asyncio
async def test_unknown_manifest_version_is_refused_rather_than_guessed(
    tmp_path: Path,
) -> None:
    """R7/R4: neither arm of the fork may be guessed for an unknown contract."""

    make_module(
        tmp_path,
        "future",
        manifest=v2_manifest("future", manifest_version=3),
        source=V2_MODULE_SOURCE,
    )
    context = runtime_context()

    with pytest.raises(ModuleLoadError) as caught:
        await ModuleLoader(context.bus, tmp_path, context=context).activate_enabled(
            {"enabled_modules": ["future"], "modules": {"future": v2_settings()}}
        )

    assert "'future'" in str(caught.value)
    assert "manifest_version" in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("manifest", "field"),
    [
        (v2_manifest("broken", runtime_api=RUNTIME_API + 1), "runtime_api"),
        (
            {
                key: value
                for key, value in v2_manifest("broken").items()
                if key != "runtime_api"
            },
            "runtime_api",
        ),
        (
            v2_manifest("broken", actions=[{**CHAT_WRITE_ACTION, "nature": "sideways"}]),
            "actions[0]",
        ),
        (
            v2_manifest("broken", settings_schema={"type": "object", "unknown": 1}),
            "settings_schema",
        ),
        (
            v2_manifest(
                "broken",
                triggers={**KEYWORD_TRIGGERS, "combinations": ["most_of"]},
            ),
            "triggers",
        ),
        (v2_manifest("broken", roles=["producer"]), "lifecycle.roles"),
    ],
)
async def test_invalid_v2_declaration_stops_startup(
    tmp_path: Path, manifest: dict, field: str
) -> None:
    """Every v2 declaration is turned into its contract at discovery, or refused."""

    make_module(tmp_path, "broken", manifest=manifest, source=V2_MODULE_SOURCE)
    context = runtime_context()

    with pytest.raises(ModuleLoadError) as caught:
        await ModuleLoader(context.bus, tmp_path, context=context).activate_enabled(
            {"enabled_modules": ["broken"], "modules": {"broken": v2_settings()}}
        )

    assert "'broken'" in str(caught.value)
    assert field in str(caught.value)


@pytest.mark.asyncio
async def test_v1_manifest_may_not_declare_a_v2_key(tmp_path: Path) -> None:
    """A manifest belongs to exactly one contract, so the fork is never ambiguous."""

    manifest = {
        "name": "mixed",
        "produces": [],
        "consumes": [],
        "middleware": False,
        "actions": [CHAT_WRITE_ACTION],
    }
    make_module(tmp_path, "mixed", manifest=manifest)
    context = runtime_context()

    with pytest.raises(ModuleLoadError) as caught:
        await ModuleLoader(context.bus, tmp_path, context=context).activate_enabled(
            _base_config("mixed")
        )

    assert "'mixed'" in str(caught.value)
    assert "actions" in str(caught.value)


@pytest.mark.asyncio
async def test_v1_producer_that_cannot_honour_the_barrier_is_refused(
    tmp_path: Path,
) -> None:
    """R4: a v1 module has no start_inputs phase, so it may not declare the input role."""

    manifest = {
        "name": "feed",
        "produces": ["channel.chat.message"],
        "consumes": [],
        "middleware": False,
        "lifecycle": {"roles": ["input"]},
    }
    make_module(tmp_path, "feed", manifest=manifest)
    settings = {"label": "feed", "calls": [], "closes": []}
    context = runtime_context()

    with pytest.raises(ModuleLoadError) as caught:
        await ModuleLoader(context.bus, tmp_path, context=context).activate_enabled(
            {"enabled_modules": ["feed"], "modules": {"feed": settings}}
        )

    assert "module 'feed'" in str(caught.value)
    assert "readiness barrier" in str(caught.value)
    assert settings["calls"] == []


@pytest.mark.asyncio
async def test_v2_input_module_must_open_its_source_in_start_inputs(
    tmp_path: Path,
) -> None:
    """R4: the same barrier obligation, checked against the v2 handle."""

    source = V2_MODULE_SOURCE.replace("    async def start_inputs(self):\n        self.settings[\"started\"] = True\n\n", "")
    make_module(
        tmp_path,
        "feed",
        manifest=v2_manifest("feed", roles=["input"], actions=[CHAT_WRITE_ACTION]),
        source=source,
    )
    context = runtime_context()

    with pytest.raises(ModuleLoadError) as caught:
        await ModuleLoader(context.bus, tmp_path, context=context).activate_enabled(
            {"enabled_modules": ["feed"], "modules": {"feed": v2_settings("feed")}}
        )

    assert "'feed'" in str(caught.value)
    assert "start_inputs" in str(caught.value)


@pytest.mark.asyncio
async def test_enabled_v2_module_without_a_runtime_context_is_refused(
    tmp_path: Path,
) -> None:
    """Without a context there is nothing to hand a v2 module; refuse, never fall back."""

    make_module(
        tmp_path,
        "chat",
        manifest=v2_manifest("chat"),
        source=V2_MODULE_SOURCE,
    )
    settings = v2_settings("chat")

    with pytest.raises(ModuleLoadError) as caught:
        await ModuleLoader(object(), tmp_path).activate_enabled(
            {"enabled_modules": ["chat"], "modules": {"chat": settings}}
        )

    assert "'chat'" in str(caught.value)
    assert "manifest_version" in str(caught.value)
    assert settings["calls"] == []


@pytest.mark.asyncio
async def test_v2_handle_close_is_optional_and_idempotent(tmp_path: Path) -> None:
    """Closing is a declared phase: a v2 handle with no close hook is not a defect."""

    source = V2_MODULE_SOURCE.replace(
        "    async def close(self):\n"
        "        self.settings[\"closes\"].append(self.settings[\"label\"])\n",
        "",
    )
    make_module(
        tmp_path,
        "chat",
        manifest=v2_manifest("chat", actions=[CHAT_WRITE_ACTION]),
        source=source,
    )
    context = runtime_context()

    activations = await ModuleLoader(
        context.bus, tmp_path, context=context
    ).activate_enabled(
        {"enabled_modules": ["chat"], "modules": {"chat": v2_settings("chat")}}
    )

    await activations[0].close()
    await activations[0].close()
    assert activations[0].closed is True


@pytest.mark.asyncio
async def test_declarations_are_recorded_before_the_first_activation(
    tmp_path: Path,
) -> None:
    """A module may bind at activation to an action another module declared."""

    provider_source = """
from core.contracts import Destination


class Provider:
    name = "late"

    async def invoke(self, invocation):
        raise AssertionError("no call is made during activation")


class Handle:
    async def close(self):
        return None


async def activate(context, settings, catalog):
    settings["discovered"] = sorted(context.actions.discovered())
    context.actions.bind(
        "chat.write", Provider(), destinations=Destination("twitch", "*", "chat")
    )
    return Handle()
"""
    make_module(
        tmp_path,
        "declarer",
        manifest=v2_manifest("declarer", actions=[CHAT_WRITE_ACTION]),
        source="""
class Handle:
    async def close(self):
        return None


async def activate(context, settings, catalog):
    return Handle()
""",
    )
    make_module(
        tmp_path,
        "binder",
        manifest=v2_manifest("binder"),
        source=provider_source,
    )
    context = runtime_context()
    binder: dict = {}

    await ModuleLoader(context.bus, tmp_path, context=context).activate_enabled(
        {
            "enabled_modules": ["binder", "declarer"],
            "modules": {"binder": binder, "declarer": {}},
        }
    )

    # "binder" is activated first and still sees the declaration of "declarer".
    assert binder["discovered"] == ["chat.write"]
    assert [binding.module for binding in context.actions.bindings()] == ["binder"]
