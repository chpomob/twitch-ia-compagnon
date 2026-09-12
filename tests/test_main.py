from __future__ import annotations

import asyncio
import json
import math
import re
import signal
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

import pytest
import yaml

import core.main as application


MODULE_SOURCE = """
from pathlib import Path


def record(settings, operation):
    path = Path(settings["lifecycle_log"])
    with path.open("a", encoding="utf-8") as stream:
        stream.write(f"{operation}:{settings['label']}\\n")


class Handle:
    def __init__(self, settings):
        self.settings = settings

    async def close(self):
        record(self.settings, "close")


async def activate(bus, settings, catalog):
    record(settings, "activate")
    return Handle(settings)
"""


SETTINGS_RECORDER_SOURCE = """
import json
from pathlib import Path


class Handle:
    async def close(self):
        return None


async def activate(bus, settings, catalog):
    path = Path(settings["settings_log"])
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(settings, sort_keys=True) + "\\n")
    return Handle()
"""


PHASED_MODULE_SOURCE = """
import json
from pathlib import Path


def record(settings, operation):
    path = Path(settings["lifecycle_log"])
    with path.open("a", encoding="utf-8") as stream:
        stream.write(f"{operation}:{settings['label']}\\n")


class Handle:
    def __init__(self, settings):
        self.settings = settings

    async def prepare(self):
        record(self.settings, "prepare")

    async def start_inputs(self):
        record(self.settings, "start_inputs")

    async def stop_inputs(self):
        record(self.settings, "stop_inputs")

    async def drain(self, allowance):
        record(self.settings, "drain")

    async def flush(self, allowance):
        record(self.settings, "flush")

    async def close(self):
        record(self.settings, "close")


async def activate(context, settings, catalog):
    record(settings, "activate")
    context_log = settings.get("context_log")
    if context_log:
        with Path(context_log).open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({
                "module": context.module,
                "runtime_api": context.runtime_api,
                "chat": context.chat is not None,
                "attachments": context.attachments is not None,
                "triggers": context.triggers is not None,
                "executor": context.executor is not None,
            }, sort_keys=True) + "\\n")
    return Handle(settings)
"""


REFUSING_HOOK_SOURCE = PHASED_MODULE_SOURCE + """

def validate_settings(settings):
    # A module-authored refusal that carries the rejected credential in its
    # message: the entry point must not echo it (R7, AC24).
    raise ValueError(f"refused {settings['api_key']}")
"""


MISSING_FIELD_HOOK_SOURCE = PHASED_MODULE_SOURCE + """

def validate_settings(settings):
    # The module, not the core, knows which of its settings are required.
    for field_name in ("access_token", "api_key"):
        value = settings.get(field_name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field_name} is required, got {settings!r}")
"""


ACCEPTING_HOOK_SOURCE = PHASED_MODULE_SOURCE + """

def validate_settings(settings):
    return None
"""


def _make_module(root: Path, name: str, source: str = MODULE_SOURCE) -> None:
    directory = root / name
    directory.mkdir()
    manifest = {
        "name": name,
        "produces": [],
        "consumes": [],
        "middleware": False,
    }
    (directory / "module.yaml").write_text(
        yaml.safe_dump(manifest), encoding="utf-8"
    )
    (directory / "__init__.py").write_text(source, encoding="utf-8")


def _make_phased_module(
    root: Path,
    name: str,
    *,
    roles: tuple[str, ...] = (),
    source: str = PHASED_MODULE_SOURCE,
    settings_schema: dict[str, Any] | None = None,
    settings_validator: str | None = None,
) -> None:
    """Write a ``manifest_version: 2`` module declaring *roles* and hooks."""

    directory = root / name
    directory.mkdir()
    manifest: dict[str, Any] = {
        "name": name,
        "manifest_version": 2,
        "runtime_api": 2,
        "produces": [],
        "consumes": [],
        "middleware": False,
        "lifecycle": {"roles": list(roles)},
    }
    if settings_schema is not None:
        manifest["settings_schema"] = settings_schema
    if settings_validator is not None:
        manifest["settings_validator"] = settings_validator
    (directory / "module.yaml").write_text(
        yaml.safe_dump(manifest), encoding="utf-8"
    )
    (directory / "__init__.py").write_text(source, encoding="utf-8")


def _finite_limits() -> dict[str, dict[str, Any]]:
    """Every retention and admission limit, finite and positive (AC32)."""

    return {
        "bus_history": {"max_events": 100, "max_bytes": 65536, "max_age_seconds": 60},
        "observation_queue": {"max_records": 64, "max_bytes": 65536},
        "dedup": {"max_entries": 32, "ttl_seconds": 30.0},
        "attachments": {
            "max_object_bytes": 1024,
            "max_objects": 8,
            "max_total_bytes": 4096,
            "max_bytes_per_run": 2048,
            "ttl_seconds": 30.0,
        },
        "conversation_memory": {
            "max_sessions": 4,
            "max_exchanges": 6,
            "max_bytes": 4096,
            "max_age_seconds": 120.0,
        },
        "chat_context": {
            "max_messages": 16,
            "max_bytes": 4096,
            "max_age_seconds": 60.0,
            "max_channels": 4,
        },
        "admission": {
            "session_queue_capacity": 2,
            "global_pending_capacity": 8,
            "max_sessions": 4,
            "workers": 2,
            "wait_seconds": 5.0,
            "total_run_seconds": 20.0,
        },
    }


def _phased_config(
    modules_directory: str,
    lifecycle_log: Path,
    names: tuple[str, ...],
    *,
    limits: bool = True,
) -> dict[str, Any]:
    config: dict[str, Any] = {
        "modules_directory": modules_directory,
        "enabled_modules": list(names),
        "modules": {
            name: {"lifecycle_log": str(lifecycle_log), "label": name}
            for name in names
        },
    }
    if limits:
        config["limits"] = _finite_limits()
    return config


def _opaque(label: str) -> str:
    return f"{label}-{uuid4().hex}"


def _valid_config(modules_directory: str, lifecycle_log: Path) -> dict:
    common = {"lifecycle_log": str(lifecycle_log)}
    return {
        "modules_directory": modules_directory,
        "enabled_modules": ["twitch", "brain", "audit"],
        "modules": {
            "twitch": {
                **common,
                "label": "twitch",
                "client_id": _opaque("client"),
                "client_secret": _opaque("secret"),
                "access_token": _opaque("token"),
                "broadcaster_id": _opaque("channel"),
                "bot_user_id": _opaque("bot"),
            },
            "brain": {
                **common,
                "label": "brain",
                "endpoint": _opaque("url"),
                "model": _opaque("engine"),
                "api_key": _opaque("key"),
            },
            "audit": {**common, "label": "audit"},
        },
    }


def _write_config(path: Path, config: dict) -> None:
    path.write_text(yaml.safe_dump(config), encoding="utf-8")


def _configured_credentials(config: dict[str, Any]) -> set[str]:
    values = [
        config["modules"]["twitch"].get("client_id"),
        config["modules"]["twitch"].get("client_secret"),
        config["modules"]["twitch"].get("access_token"),
        config["modules"]["brain"].get("api_key"),
    ]
    return {value for value in values if isinstance(value, str)}


def _set_nested(config: dict[str, Any], path: tuple[object, ...], value: Any) -> None:
    target: Any = config
    for component in path[:-1]:
        target = target[component]
    target[path[-1]] = value


def _delete_nested(config: dict[str, Any], path: tuple[object, ...]) -> None:
    target: Any = config
    for component in path[:-1]:
        target = target[component]
    del target[path[-1]]


@pytest.mark.asyncio
async def test_valid_config_waits_for_stop_and_unwinds_declared_phases_in_order(
    tmp_path: Path,
) -> None:
    """Phase-ordered replacement of the name-ordered close assertion (R4, AC13).

    The former ``twitch, brain, audit`` close order came from a name map in
    ``core/main.py``; R4 forbids ordering by name. The order now follows the
    lifecycle roles each manifest declares: the observation service is
    activated *first* and named to sort first, yet it flushes and closes last;
    the producer is started only after every module prepared and stopped
    first; ordinary resources unwind in reverse activation order.
    """

    modules = tmp_path / "modules"
    modules.mkdir()
    _make_phased_module(modules, "alpha", roles=("observation",))
    _make_phased_module(modules, "source", roles=("input",))
    _make_phased_module(modules, "relay")

    lifecycle_log = tmp_path / "lifecycle.log"
    config_path = tmp_path / "config.yaml"
    _write_config(
        config_path,
        _phased_config("./modules", lifecycle_log, ("alpha", "source", "relay")),
    )

    stop = asyncio.Event()
    ready = asyncio.Event()
    readiness: list[str] = []
    diagnostics: list[str] = []

    def report_ready(message: str) -> None:
        readiness.append(message)
        ready.set()

    task = asyncio.create_task(
        application.run(
            config_path,
            stop,
            ready_reporter=report_ready,
            diagnostic_reporter=diagnostics.append,
        )
    )
    await asyncio.wait_for(ready.wait(), timeout=1)

    assert task.done() is False
    assert readiness == ["ready"]
    started = [
        "activate:alpha",
        "activate:source",
        "activate:relay",
        "prepare:alpha",
        "prepare:source",
        "prepare:relay",
        "start_inputs:source",
    ]
    assert lifecycle_log.read_text(encoding="utf-8").splitlines() == started

    stop.set()
    assert await asyncio.wait_for(task, timeout=1) == 0
    assert diagnostics == []
    assert lifecycle_log.read_text(encoding="utf-8").splitlines() == started + [
        "stop_inputs:source",
        "drain:relay",
        "drain:source",
        "drain:alpha",
        "close:relay",
        "close:source",
        "flush:alpha",
        "close:alpha",
    ]


@pytest.mark.asyncio
async def test_v1_source_closes_before_the_versioned_sinks_it_feeds(
    tmp_path: Path,
) -> None:
    """A mixed pipeline unwinds in an order that is safe in both directions.

    The v1 source's ``close()`` is its stop, drain and release in one; it
    runs after the versioned producer stopped and drained, and before the
    versioned relay closes or the observation service flushes — so whatever
    the v1 source still delivers while closing has somewhere to go.
    """

    modules = tmp_path / "modules"
    modules.mkdir()
    _make_phased_module(modules, "alpha", roles=("observation",))
    _make_module(modules, "legacy")
    _make_phased_module(modules, "source", roles=("input",))
    _make_phased_module(modules, "relay")
    lifecycle_log = tmp_path / "lifecycle.log"
    config_path = tmp_path / "config.yaml"
    _write_config(
        config_path,
        _phased_config(
            "./modules", lifecycle_log, ("alpha", "legacy", "source", "relay")
        ),
    )
    stop = asyncio.Event()
    stop.set()

    status = await application.run(
        config_path,
        stop,
        ready_reporter=lambda _message: None,
        diagnostic_reporter=lambda message: pytest.fail(message),
    )

    assert status == 0
    assert lifecycle_log.read_text(encoding="utf-8").splitlines() == [
        "activate:alpha",
        "activate:legacy",
        "activate:source",
        "activate:relay",
        "prepare:alpha",
        "prepare:source",
        "prepare:relay",
        "start_inputs:source",
        "stop_inputs:source",
        "drain:relay",
        "drain:source",
        "drain:alpha",
        "close:legacy",
        "close:relay",
        "close:source",
        "flush:alpha",
        "close:alpha",
    ]


@pytest.mark.asyncio
async def test_failed_startup_unwinds_the_v1_route_through_the_same_coordinator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R4: one coordinator, one shutdown deadline, for both routes.

    The v1 activation is handed to the coordinator as its compatibility
    step, so a failed preparation unwinds it inside the same sequence and
    budget as the versioned modules instead of a second, fresh one.
    """

    constructed: list[tuple[list[str], list[str]]] = []
    real_coordinator = application.PhaseCoordinator

    class Recording(real_coordinator):  # type: ignore[misc, valid-type]
        def __init__(self, modules: Any, **options: Any) -> None:
            constructed.append(
                (
                    [module.name for module in modules],
                    [module.name for module in options.get("compatibility", ())],
                )
            )
            super().__init__(modules, **options)

    monkeypatch.setattr(application, "PhaseCoordinator", Recording)
    modules = tmp_path / "modules"
    modules.mkdir()
    _make_module(modules, "legacy")
    _make_phased_module(
        modules,
        "relay",
        source=PHASED_MODULE_SOURCE.replace(
            'record(self.settings, "prepare")',
            'record(self.settings, "prepare")\n'
            '        raise RuntimeError(self.settings["label"])',
        ),
    )
    lifecycle_log = tmp_path / "lifecycle.log"
    config_path = tmp_path / "config.yaml"
    _write_config(
        config_path, _phased_config("./modules", lifecycle_log, ("legacy", "relay"))
    )
    readiness: list[str] = []
    diagnostics: list[str] = []

    status = await application.run(
        config_path,
        asyncio.Event(),
        ready_reporter=readiness.append,
        diagnostic_reporter=diagnostics.append,
    )

    assert status != 0
    assert readiness == []
    assert diagnostics == ["module 'relay': phase 'prepare' failed"]
    assert constructed == [(["relay"], ["legacy"])]
    assert lifecycle_log.read_text(encoding="utf-8").splitlines() == [
        "activate:legacy",
        "activate:relay",
        "prepare:relay",
        "drain:relay",
        "close:legacy",
        "close:relay",
    ]


@pytest.mark.asyncio
async def test_versioned_module_receives_the_runtime_built_from_limits(
    tmp_path: Path,
) -> None:
    """R7: activation receives the versioned context, not a bare bus."""

    modules = tmp_path / "modules"
    modules.mkdir()
    _make_phased_module(modules, "relay")
    lifecycle_log = tmp_path / "lifecycle.log"
    context_log = tmp_path / "context.jsonl"
    config = _phased_config("./modules", lifecycle_log, ("relay",))
    config["modules"]["relay"]["context_log"] = str(context_log)
    config_path = tmp_path / "config.yaml"
    _write_config(config_path, config)
    stop = asyncio.Event()
    stop.set()

    status = await application.run(
        config_path,
        stop,
        ready_reporter=lambda _message: None,
        diagnostic_reporter=lambda message: pytest.fail(message),
    )

    assert status == 0
    assert [
        json.loads(line)
        for line in context_log.read_text(encoding="utf-8").splitlines()
    ] == [
        {
            "module": "relay",
            "runtime_api": 2,
            "chat": True,
            "attachments": True,
            "triggers": True,
            "executor": True,
        }
    ]


def test_entry_point_contains_no_module_name_literal_or_core_field_table() -> None:
    """AC13, AC24: the literal grep the specification asks for."""

    source = (
        Path(__file__).resolve().parents[1] / "core" / "main.py"
    ).read_text(encoding="utf-8")

    assert source.count("_MODULE_REQUIRED_FIELDS") == 0
    for literal in ("twitch", "brain", "audit"):
        assert source.lower().count(literal) == 0, literal


def test_entry_point_drives_the_coordinator_under_finite_global_deadlines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R4: one finite startup deadline and one finite shutdown deadline."""

    constructed: list[dict[str, Any]] = []
    real_coordinator = application.PhaseCoordinator

    class Recording(real_coordinator):  # type: ignore[misc, valid-type]
        def __init__(self, modules: Any, **options: Any) -> None:
            constructed.append(dict(options))
            super().__init__(modules, **options)

    monkeypatch.setattr(application, "PhaseCoordinator", Recording)
    modules = tmp_path / "modules"
    modules.mkdir()
    _make_phased_module(modules, "relay")
    config_path = tmp_path / "config.yaml"
    _write_config(
        config_path, _phased_config("./modules", tmp_path / "lifecycle.log", ("relay",))
    )
    stop = asyncio.Event()
    stop.set()

    status = asyncio.run(
        application.run(
            config_path,
            stop,
            ready_reporter=lambda _message: None,
            diagnostic_reporter=lambda message: pytest.fail(message),
        )
    )

    assert status == 0
    assert len(constructed) == 1
    options = constructed[0]
    for deadline in ("startup_deadline_seconds", "shutdown_deadline_seconds"):
        value = options[deadline]
        assert isinstance(value, (int, float)) and not isinstance(value, bool)
        assert math.isfinite(value) and value > 0
    assert options["hook_timeout_seconds"] <= options["shutdown_deadline_seconds"]


@pytest.mark.asyncio
async def test_failed_preparation_unwinds_partial_startup_without_readiness(
    tmp_path: Path,
) -> None:
    """R4: a failed phase stops startup, names the module and cleans up."""

    modules = tmp_path / "modules"
    modules.mkdir()
    _make_phased_module(modules, "source", roles=("input",))
    _make_phased_module(
        modules,
        "relay",
        source=PHASED_MODULE_SOURCE.replace(
            'record(self.settings, "prepare")',
            'record(self.settings, "prepare")\n'
            '        raise RuntimeError(self.settings["label"])',
        ),
    )
    lifecycle_log = tmp_path / "lifecycle.log"
    config_path = tmp_path / "config.yaml"
    _write_config(
        config_path, _phased_config("./modules", lifecycle_log, ("source", "relay"))
    )
    readiness: list[str] = []
    diagnostics: list[str] = []

    status = await application.run(
        config_path,
        asyncio.Event(),
        ready_reporter=readiness.append,
        diagnostic_reporter=diagnostics.append,
    )

    assert status != 0
    assert readiness == []
    assert diagnostics == ["module 'relay': phase 'prepare' failed"]
    assert lifecycle_log.read_text(encoding="utf-8").splitlines() == [
        "activate:source",
        "activate:relay",
        "prepare:source",
        "prepare:relay",
        # Never started, still stopped: the hooks are idempotent by contract.
        "stop_inputs:source",
        "drain:relay",
        "drain:source",
        "close:relay",
        "close:source",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("file_contents", "expected_diagnostic"),
    [
        pytest.param(None, "configuration file: is not readable", id="unreadable"),
        pytest.param("modules: [", "configuration file: is not valid YAML", id="malformed"),
        pytest.param("- not\n- a\n- mapping\n", "configuration: must be a mapping", id="non-mapping"),
    ],
)
async def test_invalid_configuration_files_fail_before_readiness(
    tmp_path: Path,
    file_contents: str | None,
    expected_diagnostic: str,
) -> None:
    config_path = tmp_path / "config.yaml"
    if file_contents is not None:
        config_path.write_text(file_contents, encoding="utf-8")
    readiness: list[str] = []
    diagnostics: list[str] = []

    status = await application.run(
        config_path,
        asyncio.Event(),
        ready_reporter=readiness.append,
        diagnostic_reporter=diagnostics.append,
    )

    assert status != 0
    assert readiness == []
    assert diagnostics == [expected_diagnostic]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("setting", "expected_diagnostic"),
    [
        ("modules_directory", "modules_directory: must be a non-empty path string"),
        ("enabled_modules", "enabled_modules: must be a list"),
        ("modules", "modules: must be a mapping"),
    ],
)
async def test_each_missing_top_level_setting_fails_before_readiness(
    tmp_path: Path,
    setting: str,
    expected_diagnostic: str,
) -> None:
    config = _valid_config("./modules", tmp_path / "unused.log")
    credentials = _configured_credentials(config)
    _delete_nested(config, (setting,))
    config_path = tmp_path / "config.yaml"
    _write_config(config_path, config)
    readiness: list[str] = []
    diagnostics: list[str] = []

    status = await application.run(
        config_path,
        asyncio.Event(),
        ready_reporter=readiness.append,
        diagnostic_reporter=diagnostics.append,
    )

    assert status != 0
    assert readiness == []
    assert diagnostics == [expected_diagnostic]
    assert all(value not in diagnostics[0] for value in credentials)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("setting", "invalid_value", "expected_diagnostic"),
    [
        pytest.param(
            "modules_directory",
            42,
            "modules_directory: must be a non-empty path string",
            id="non-string-modules-directory",
        ),
        pytest.param(
            "modules_directory",
            " \t",
            "modules_directory: must be a non-empty path string",
            id="blank-modules-directory",
        ),
        pytest.param(
            "enabled_modules",
            {},
            "enabled_modules: must be a list",
            id="non-list-enabled-modules",
        ),
        pytest.param(
            "modules",
            [],
            "modules: must be a mapping",
            id="non-mapping-modules",
        ),
    ],
)
async def test_each_type_invalid_top_level_setting_fails_before_readiness(
    tmp_path: Path,
    setting: str,
    invalid_value: Any,
    expected_diagnostic: str,
) -> None:
    config = _valid_config("./modules", tmp_path / "unused.log")
    credentials = _configured_credentials(config)
    _set_nested(config, (setting,), invalid_value)
    config_path = tmp_path / "config.yaml"
    _write_config(config_path, config)
    readiness: list[str] = []
    diagnostics: list[str] = []

    status = await application.run(
        config_path,
        asyncio.Event(),
        ready_reporter=readiness.append,
        diagnostic_reporter=diagnostics.append,
    )

    assert status != 0
    assert readiness == []
    assert diagnostics == [expected_diagnostic]
    assert all(value not in diagnostics[0] for value in credentials)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mutate", "expected_diagnostic"),
    [
        pytest.param(
            lambda config: config["enabled_modules"].__setitem__(0, 42),
            "enabled_modules[0]: must be a non-empty string",
            id="non-string-enabled-name",
        ),
        pytest.param(
            lambda config: config["enabled_modules"].__setitem__(0, " "),
            "enabled_modules[0]: must be a non-empty string",
            id="empty-enabled-name",
        ),
        pytest.param(
            lambda config: config["enabled_modules"].append("twitch"),
            "enabled_modules[3]: module names must be unique",
            id="duplicate-enabled-name",
        ),
        pytest.param(
            lambda config: config["modules"].update({1: {}}),
            "modules: keys must be non-empty strings",
            id="non-string-module-key",
        ),
        pytest.param(
            lambda config: config["modules"].update({"": {}}),
            "modules: keys must be non-empty strings",
            id="empty-module-key",
        ),
        pytest.param(
            lambda config: config["modules"].__setitem__("audit", []),
            "modules.audit: must be a mapping",
            id="non-mapping-module-settings",
        ),
        pytest.param(
            lambda config: config["modules"].pop("audit"),
            "modules.audit: settings are required",
            id="missing-enabled-module-settings",
        ),
    ],
)
async def test_invalid_configuration_structure_fails_before_readiness(
    tmp_path: Path,
    mutate: Callable[[dict[str, Any]], object],
    expected_diagnostic: str,
) -> None:
    config = _valid_config("./modules", tmp_path / "unused.log")
    credentials = _configured_credentials(config)
    mutate(config)
    config_path = tmp_path / "config.yaml"
    _write_config(config_path, config)
    readiness: list[str] = []
    diagnostics: list[str] = []

    status = await application.run(
        config_path,
        asyncio.Event(),
        ready_reporter=readiness.append,
        diagnostic_reporter=diagnostics.append,
    )

    assert status != 0
    assert readiness == []
    assert diagnostics == [expected_diagnostic]
    assert all(value not in diagnostics[0] for value in credentials)


def _hooked_config(
    modules_directory: str, lifecycle_log: Path
) -> tuple[dict[str, Any], set[str]]:
    """Two enabled modules whose credentials must never reach a diagnostic."""

    config = _phased_config(modules_directory, lifecycle_log, ("gateway", "engine"))
    credentials = {
        "gateway": {
            "access_token": _opaque("token"),
            "api_key": _opaque("gateway-key"),
        },
        "engine": {
            "access_token": _opaque("engine-token"),
            "api_key": _opaque("key"),
        },
    }
    for name, settings in credentials.items():
        config["modules"][name].update(settings)
    values = {value for settings in credentials.values() for value in settings.values()}
    return config, values


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("module_name", "field_name", "invalid_value"),
    [
        ("gateway", "access_token", None),
        ("gateway", "access_token", 42),
        ("gateway", "api_key", " "),
        ("engine", "api_key", None),
        ("engine", "api_key", ["not", "a", "string"]),
        ("engine", "access_token", ""),
    ],
)
async def test_each_type_invalid_module_setting_is_rejected_by_the_module_hook(
    tmp_path: Path,
    module_name: str,
    field_name: str,
    invalid_value: Any,
) -> None:
    """Module-hook replacement of the core-owned required-field table (R7).

    The former assertion expected ``core/main.py`` to emit
    ``modules.<name>.<field>: must be a non-empty string`` from its own field
    table; R7 moves the knowledge of which settings are required into each
    module's declared ``settings_validator`` hook. The core no longer knows
    the field, so the diagnostic names the module and the hook that refused,
    and it never echoes the refused value — the hook's own message does.
    """

    modules = tmp_path / "modules"
    modules.mkdir()
    for name in ("gateway", "engine"):
        _make_phased_module(
            modules,
            name,
            source=MISSING_FIELD_HOOK_SOURCE,
            settings_validator="validate_settings",
        )
    lifecycle_log = tmp_path / "lifecycle.log"
    config, credentials = _hooked_config("./modules", lifecycle_log)
    if invalid_value is None:
        _delete_nested(config, ("modules", module_name, field_name))
    else:
        _set_nested(config, ("modules", module_name, field_name), invalid_value)
    config_path = tmp_path / "config.yaml"
    _write_config(config_path, config)
    diagnostics: list[str] = []

    status = await application.run(
        config_path,
        asyncio.Event(),
        ready_reporter=lambda _message: pytest.fail("reported readiness"),
        diagnostic_reporter=diagnostics.append,
    )

    assert status != 0
    assert diagnostics == [
        f"module {module_name!r}: field 'validate_settings': "
        "settings were refused by the module"
    ]
    assert all(value not in diagnostics[0] for value in credentials)
    assert not lifecycle_log.exists()


@pytest.mark.asyncio
async def test_missing_setting_fails_before_readiness_naming_module_and_field(
    tmp_path: Path,
) -> None:
    """Module-owned replacement of the core-owned ``access_token`` check (R7).

    The module declares the field through its ``settings_schema``; the core
    reports the module and the field it declared, with 0 configured values.
    """

    modules = tmp_path / "modules"
    modules.mkdir()
    _make_phased_module(
        modules,
        "gateway",
        settings_schema={
            "type": "object",
            "properties": {
                "access_token": {"type": "string"},
                "api_key": {"type": "string"},
            },
            "required": ["access_token", "api_key"],
        },
    )
    _make_phased_module(modules, "engine")
    lifecycle_log = tmp_path / "lifecycle.log"
    config, credentials = _hooked_config(str(modules), lifecycle_log)
    del config["modules"]["gateway"]["access_token"]
    config_path = tmp_path / "config.yaml"
    _write_config(config_path, config)
    readiness: list[str] = []
    diagnostics: list[str] = []

    status = await application.run(
        config_path,
        asyncio.Event(),
        ready_reporter=readiness.append,
        diagnostic_reporter=diagnostics.append,
    )

    combined = "\n".join(diagnostics)
    assert status != 0
    assert readiness == []
    assert diagnostics == [
        "module 'gateway': field 'settings.access_token': is required and missing"
    ]
    assert all(value not in combined for value in credentials)
    assert not lifecycle_log.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["both", "second-only"])
async def test_invalid_modules_are_refused_before_either_opens_a_transport(
    tmp_path: Path, invalid: str
) -> None:
    """AC24: every enabled module is validated before any is activated.

    With both modules refusing their settings, startup stops naming the
    refusing module and field and echoing 0 credential values; with only the
    second refusing, the first — valid — module is still never activated, so
    0 transports are opened either way. The loader stops at its first
    refusal, so the second module's own diagnostic is not reported yet: that
    half of AC24 needs the loader to collect refusals across modules.
    """

    modules = tmp_path / "modules"
    modules.mkdir()
    _make_phased_module(
        modules,
        "gateway",
        roles=("input",),
        source=REFUSING_HOOK_SOURCE if invalid == "both" else ACCEPTING_HOOK_SOURCE,
        settings_validator="validate_settings",
    )
    _make_phased_module(
        modules,
        "engine",
        source=REFUSING_HOOK_SOURCE,
        settings_validator="validate_settings",
    )
    lifecycle_log = tmp_path / "lifecycle.log"
    config, credentials = _hooked_config("./modules", lifecycle_log)
    config_path = tmp_path / "config.yaml"
    _write_config(config_path, config)
    readiness: list[str] = []
    diagnostics: list[str] = []

    status = await application.run(
        config_path,
        asyncio.Event(),
        ready_reporter=readiness.append,
        diagnostic_reporter=diagnostics.append,
    )

    refused = "gateway" if invalid == "both" else "engine"
    assert status != 0
    assert readiness == []
    assert diagnostics[0] == (
        f"module {refused!r}: field 'validate_settings': "
        "settings were refused by the module"
    )
    assert all(
        value not in diagnostic for diagnostic in diagnostics for value in credentials
    )
    # 0 activations means 0 transports: nothing was opened only to be closed.
    assert not lifecycle_log.exists()


@pytest.mark.asyncio
async def test_declared_hook_that_cannot_be_resolved_stops_startup(
    tmp_path: Path,
) -> None:
    """A manifest naming a hook its module lacks is a failure, never "valid"."""

    modules = tmp_path / "modules"
    modules.mkdir()
    _make_phased_module(modules, "gateway", settings_validator="validate_settings")
    _make_phased_module(modules, "engine")
    lifecycle_log = tmp_path / "lifecycle.log"
    config, credentials = _hooked_config("./modules", lifecycle_log)
    config_path = tmp_path / "config.yaml"
    _write_config(config_path, config)
    readiness: list[str] = []
    diagnostics: list[str] = []

    status = await application.run(
        config_path,
        asyncio.Event(),
        ready_reporter=readiness.append,
        diagnostic_reporter=diagnostics.append,
    )

    assert status != 0
    assert readiness == []
    assert diagnostics == [
        "module 'gateway': field 'settings_validator': "
        "names a settings hook the module does not define"
    ]
    assert all(value not in diagnostics[0] for value in credentials)
    assert not lifecycle_log.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mutate", "expected_diagnostic"),
    [
        pytest.param(
            lambda limits: limits["bus_history"].pop("max_events"),
            "limits.bus_history.max_events: is required",
            id="absent-bus-history-event-limit",
        ),
        pytest.param(
            lambda limits: limits["observation_queue"].__setitem__("max_bytes", -1),
            "limits.observation_queue.max_bytes: must be a positive integer",
            id="negative-observation-queue-byte-limit",
        ),
        pytest.param(
            lambda limits: limits["dedup"].__setitem__("ttl_seconds", "soon"),
            "limits.dedup.ttl_seconds: must be a finite positive number",
            id="non-numeric-dedup-ttl",
        ),
        pytest.param(
            lambda limits: limits["attachments"].__setitem__(
                "max_total_bytes", float("inf")
            ),
            "limits.attachments.max_total_bytes: must be a positive integer",
            id="infinite-attachment-total-volume",
        ),
        pytest.param(
            lambda limits: limits["conversation_memory"].pop("max_exchanges"),
            "limits.conversation_memory.max_exchanges: is required",
            id="absent-conversation-memory-exchange-limit",
        ),
    ],
)
async def test_each_bad_retention_limit_stops_startup_before_any_transport(
    tmp_path: Path,
    mutate: Callable[[dict[str, Any]], object],
    expected_diagnostic: str,
) -> None:
    """AC32: one bad limit at a time stops startup naming that setting."""

    modules = tmp_path / "modules"
    modules.mkdir()
    _make_phased_module(modules, "source", roles=("input",))
    _make_module(modules, "sink")
    lifecycle_log = tmp_path / "lifecycle.log"
    config = _phased_config("./modules", lifecycle_log, ("source", "sink"))
    mutate(config["limits"])
    config_path = tmp_path / "config.yaml"
    _write_config(config_path, config)
    readiness: list[str] = []
    diagnostics: list[str] = []

    status = await application.run(
        config_path,
        asyncio.Event(),
        ready_reporter=readiness.append,
        diagnostic_reporter=diagnostics.append,
    )

    assert status != 0
    assert readiness == []
    assert diagnostics == [expected_diagnostic]
    assert not lifecycle_log.exists()


@pytest.mark.asyncio
async def test_all_finite_retention_limits_start_the_application(
    tmp_path: Path,
) -> None:
    """AC32's control: the same configuration with finite limits starts."""

    modules = tmp_path / "modules"
    modules.mkdir()
    _make_phased_module(modules, "source", roles=("input",))
    _make_module(modules, "sink")
    lifecycle_log = tmp_path / "lifecycle.log"
    config_path = tmp_path / "config.yaml"
    _write_config(
        config_path, _phased_config("./modules", lifecycle_log, ("source", "sink"))
    )
    stop = asyncio.Event()
    stop.set()
    readiness: list[str] = []

    status = await application.run(
        config_path,
        stop,
        ready_reporter=readiness.append,
        diagnostic_reporter=lambda message: pytest.fail(message),
    )

    assert status == 0
    assert readiness == ["ready"]
    assert lifecycle_log.read_text(encoding="utf-8").splitlines() == [
        "activate:source",
        "activate:sink",
        "prepare:source",
        "start_inputs:source",
        "stop_inputs:source",
        "drain:source",
        # The v1 sink's close is its whole shutdown; it runs after the
        # versioned producer stopped and drained, before that producer's
        # resources close.
        "close:sink",
        "close:source",
    ]


@pytest.mark.parametrize(
    ("mutate", "expected_diagnostic"),
    [
        pytest.param(
            lambda config: config.__setitem__("limits", []),
            "limits: must be a mapping",
            id="non-mapping-limits",
        ),
        pytest.param(
            lambda config: config["limits"].pop("admission"),
            "limits.admission: is required",
            id="absent-limit-group",
        ),
        pytest.param(
            lambda config: config["limits"].__setitem__("admission", 3),
            "limits.admission: must be a mapping",
            id="non-mapping-limit-group",
        ),
        pytest.param(
            lambda config: config["limits"].__setitem__("history", {}),
            "limits.history: is not a known limit group",
            id="unknown-limit-group",
        ),
        pytest.param(
            lambda config: config["limits"]["dedup"].__setitem__("max_age", 1),
            "limits.dedup.max_age: is not a known limit",
            id="unknown-limit",
        ),
        pytest.param(
            lambda config: config["limits"]["admission"].__setitem__("workers", True),
            "limits.admission.workers: must be a positive integer",
            id="boolean-count-limit",
        ),
        pytest.param(
            lambda config: config["limits"]["admission"].__setitem__("workers", 0),
            "limits.admission.workers: must be a positive integer",
            id="zero-count-limit",
        ),
        pytest.param(
            lambda config: config["limits"]["admission"].__setitem__(
                "wait_seconds", float("nan")
            ),
            "limits.admission.wait_seconds: must be a finite positive number",
            id="nan-duration-limit",
        ),
        pytest.param(
            lambda config: config["limits"]["chat_context"].__setitem__(
                "max_age_seconds", 0
            ),
            "limits.chat_context.max_age_seconds: must be a finite positive number",
            id="zero-duration-limit",
        ),
    ],
)
def test_load_config_validates_every_limit_group_and_kind(
    tmp_path: Path,
    mutate: Callable[[dict[str, Any]], object],
    expected_diagnostic: str,
) -> None:
    """R6: every admission and retention limit is validated by the core."""

    config = _phased_config("./modules", tmp_path / "unused.log", ("relay",))
    mutate(config)
    config_path = tmp_path / "config.yaml"
    _write_config(config_path, config)

    with pytest.raises(application.ConfigurationError) as raised:
        application.load_config(config_path, environ={})

    assert str(raised.value) == expected_diagnostic


def test_load_config_leaves_disabled_module_references_unresolved(
    tmp_path: Path,
) -> None:
    """R7: a disabled module's secrets are not resolved, so none is required."""

    config = _valid_config("./modules", tmp_path / "unused.log")
    config["enabled_modules"] = ["twitch", "audit"]
    config["modules"]["brain"]["api_key"] = "${MISSING_MODEL_API_KEY}"
    config["modules"]["twitch"]["access_token"] = "${TWITCH_ACCESS_TOKEN}"
    expected = _opaque("token")
    config_path = tmp_path / "config.yaml"
    _write_config(config_path, config)

    loaded = application.load_config(
        config_path, environ={"TWITCH_ACCESS_TOKEN": expected}
    )

    assert loaded["modules"]["twitch"]["access_token"] == expected
    assert loaded["modules"]["brain"]["api_key"] == "${MISSING_MODEL_API_KEY}"


def test_load_config_expands_environment_and_resolves_relative_directory(
    tmp_path: Path,
) -> None:
    modules = tmp_path / "relative-modules"
    modules.mkdir()
    lifecycle_log = tmp_path / "unused.log"
    config = _valid_config("./relative-modules", lifecycle_log)
    expected = _opaque("resolved")
    config["modules"]["brain"]["api_key"] = "${MODEL_API_KEY}"
    config_path = tmp_path / "config.yaml"
    _write_config(config_path, config)

    loaded = application.load_config(
        config_path, environ={"MODEL_API_KEY": expected}
    )

    assert loaded["modules_directory"] == str(modules.resolve())
    assert loaded["modules"]["brain"]["api_key"] == expected


@pytest.mark.parametrize(
    "reference",
    ["${MODEL_API_KEY} ", "${MODEL_API_KEY}-suffix", "prefix-${MODEL_API_KEY}"],
)
def test_load_config_rejects_partial_environment_references(
    tmp_path: Path, reference: str
) -> None:
    config = _valid_config("./modules", tmp_path / "unused.log")
    config["modules"]["brain"]["api_key"] = reference
    config_path = tmp_path / "config.yaml"
    _write_config(config_path, config)

    with pytest.raises(
        application.ConfigurationError,
        match=r"^modules\.brain\.api_key: environment reference is invalid$",
    ):
        application.load_config(config_path, environ={})


def test_load_config_reports_invalid_modules_directory_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_expanduser = application.Path.expanduser

    def expanduser(path: Path) -> Path:
        if str(path).startswith("~invalid-user"):
            raise RuntimeError("unknown user")
        return original_expanduser(path)

    monkeypatch.setattr(application.Path, "expanduser", expanduser)
    config = _valid_config(
        "~invalid-user/modules", tmp_path / "unused.log"
    )
    config_path = tmp_path / "config.yaml"
    _write_config(config_path, config)

    with pytest.raises(
        application.ConfigurationError,
        match=r"^modules_directory: must be a valid path$",
    ):
        application.load_config(config_path)


def test_load_config_normalizes_invalid_configuration_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_expanduser(_path: Path) -> Path:
        raise RuntimeError("unknown user")

    monkeypatch.setattr(application.Path, "expanduser", fail_expanduser)

    with pytest.raises(
        application.ConfigurationError,
        match=r"^configuration file: path is not valid$",
    ):
        application.load_config("~invalid-user/config.yaml")


@pytest.mark.asyncio
async def test_config_only_value_changes_reach_modules_on_each_start(
    tmp_path: Path,
) -> None:
    modules = tmp_path / "modules"
    modules.mkdir()
    for name in ("twitch", "brain", "audit"):
        _make_module(modules, name, SETTINGS_RECORDER_SOURCE)

    settings_log = tmp_path / "settings.jsonl"
    expected_settings: list[dict[str, Any]] = []
    expected_by_run: list[dict[str, dict[str, Any]]] = []
    configured_values: set[str] = set()
    environment_fields = {
        ("brain", "api_key"): "MODEL_API_KEY",
        ("twitch", "client_id"): "TWITCH_CLIENT_ID",
        ("twitch", "client_secret"): "TWITCH_CLIENT_SECRET",
        ("twitch", "access_token"): "TWITCH_ACCESS_TOKEN",
    }

    for run_number in range(2):
        config = _valid_config("./modules", tmp_path / "unused.log")
        direct_fields = {
            ("brain", "endpoint"): _opaque(f"endpoint-run-{run_number}"),
            ("brain", "model"): _opaque(f"model-run-{run_number}"),
            ("twitch", "broadcaster_id"): _opaque(
                f"broadcaster-run-{run_number}"
            ),
            ("twitch", "bot_user_id"): _opaque(f"bot-run-{run_number}"),
        }
        environ = {
            variable: _opaque(f"{variable.lower()}-run-{run_number}")
            for variable in environment_fields.values()
        }

        for settings in config["modules"].values():
            settings["settings_log"] = str(settings_log)
        for (module_name, field_name), value in direct_fields.items():
            config["modules"][module_name][field_name] = value
        for (module_name, field_name), variable in environment_fields.items():
            config["modules"][module_name][field_name] = f"${{{variable}}}"

        expected_for_run = json.loads(json.dumps(config["modules"]))
        for (module_name, field_name), variable in environment_fields.items():
            expected_for_run[module_name][field_name] = environ[variable]
        expected_by_run.append(expected_for_run)
        expected_settings.extend(
            expected_for_run[name] for name in ("twitch", "brain", "audit")
        )
        configured_values.update(direct_fields.values())
        configured_values.update(environ.values())

        config_path = tmp_path / f"config-{run_number}.yaml"
        _write_config(config_path, config)
        stop = asyncio.Event()
        stop.set()

        assert await application.run(
            config_path,
            stop,
            environ=environ,
            ready_reporter=lambda _message: None,
            diagnostic_reporter=lambda message: pytest.fail(message),
        ) == 0

    recorded_settings = [
        json.loads(line)
        for line in settings_log.read_text(encoding="utf-8").splitlines()
    ]
    assert recorded_settings == expected_settings

    config_driven_fields = set(environment_fields) | {
        ("brain", "endpoint"),
        ("brain", "model"),
        ("twitch", "broadcaster_id"),
        ("twitch", "bot_user_id"),
    }
    for module_name, field_name in config_driven_fields:
        assert (
            expected_by_run[0][module_name][field_name]
            != expected_by_run[1][module_name][field_name]
        )

    project_root = Path(__file__).resolve().parents[1]
    executable_source = "\n".join(
        path.read_text(encoding="utf-8")
        for source_directory in ("core", "modules")
        for path in (project_root / source_directory).rglob("*.py")
    )
    assert all(value not in executable_source for value in configured_values)


@pytest.mark.asyncio
async def test_unresolved_environment_reference_names_setting_path_only(
    tmp_path: Path,
) -> None:
    lifecycle_log = tmp_path / "unused.log"
    config = _valid_config("./modules", lifecycle_log)
    config["modules"]["brain"]["api_key"] = "${MISSING_MODEL_API_KEY}"
    config_path = tmp_path / "config.yaml"
    _write_config(config_path, config)
    diagnostics: list[str] = []

    status = await application.run(
        config_path,
        asyncio.Event(),
        environ={},
        ready_reporter=lambda _message: pytest.fail("reported readiness"),
        diagnostic_reporter=diagnostics.append,
    )

    assert status != 0
    assert diagnostics == [
        "modules.brain.api_key: environment reference is unresolved"
    ]


@pytest.mark.asyncio
async def test_activation_failure_closes_partial_startup_without_leaking_values(
    tmp_path: Path,
) -> None:
    modules = tmp_path / "modules"
    modules.mkdir()
    _make_module(modules, "twitch")
    _make_module(
        modules,
        "brain",
        source="""
async def activate(bus, settings, catalog):
    raise RuntimeError(settings["api_key"])
""",
    )
    _make_module(modules, "audit")
    lifecycle_log = tmp_path / "lifecycle.log"
    config = _valid_config(str(modules), lifecycle_log)
    secret_values = {
        config["modules"]["twitch"]["client_secret"],
        config["modules"]["twitch"]["access_token"],
        config["modules"]["brain"]["api_key"],
    }
    config_path = tmp_path / "config.yaml"
    _write_config(config_path, config)
    readiness: list[str] = []
    diagnostics: list[str] = []

    status = await application.run(
        config_path,
        asyncio.Event(),
        ready_reporter=readiness.append,
        diagnostic_reporter=diagnostics.append,
    )

    combined = "\n".join(diagnostics)
    assert status != 0
    assert readiness == []
    assert "brain" in combined
    assert lifecycle_log.read_text(encoding="utf-8").splitlines() == [
        "activate:twitch",
        "close:twitch",
    ]
    assert all(value not in combined for value in secret_values)


@pytest.mark.asyncio
async def test_shutdown_failure_is_nonzero_and_does_not_skip_other_modules(
    tmp_path: Path,
) -> None:
    modules = tmp_path / "modules"
    modules.mkdir()
    _make_module(modules, "twitch")
    _make_module(
        modules,
        "brain",
        source=MODULE_SOURCE.replace(
            'record(self.settings, "close")',
            'record(self.settings, "close")\n        raise RuntimeError(self.settings["api_key"])',
        ),
    )
    _make_module(modules, "audit")
    lifecycle_log = tmp_path / "lifecycle.log"
    config = _valid_config(str(modules), lifecycle_log)
    secret = config["modules"]["brain"]["api_key"]
    config_path = tmp_path / "config.yaml"
    _write_config(config_path, config)
    stop = asyncio.Event()
    stop.set()
    diagnostics: list[str] = []

    status = await application.run(
        config_path,
        stop,
        ready_reporter=lambda _message: None,
        diagnostic_reporter=diagnostics.append,
    )

    assert status != 0
    assert lifecycle_log.read_text(encoding="utf-8").splitlines()[-3:] == [
        "close:twitch",
        "close:brain",
        "close:audit",
    ]
    assert diagnostics == ["module 'brain': shutdown failed"]
    assert secret not in "\n".join(diagnostics)


@pytest.mark.asyncio
async def test_owned_signal_handlers_stop_run_and_are_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    modules = tmp_path / "modules"
    modules.mkdir()
    config_path = tmp_path / "config.yaml"
    _write_config(
        config_path,
        {
            "modules_directory": str(modules),
            "enabled_modules": [],
            "modules": {},
        },
    )
    loop = asyncio.get_running_loop()
    callbacks: dict[signal.Signals, object] = {}
    removed: list[signal.Signals] = []

    def add_handler(signum: signal.Signals, callback: object) -> None:
        callbacks[signum] = callback

    def remove_handler(signum: signal.Signals) -> bool:
        removed.append(signum)
        return True

    monkeypatch.setattr(loop, "add_signal_handler", add_handler)
    monkeypatch.setattr(loop, "remove_signal_handler", remove_handler)

    def report_ready(_message: str) -> None:
        callback = callbacks[signal.SIGTERM]
        assert callable(callback)
        callback()

    assert await application.run(config_path, ready_reporter=report_ready) == 0
    assert set(callbacks) == {signal.SIGINT, signal.SIGTERM}
    assert removed == [signal.SIGINT, signal.SIGTERM]


@pytest.mark.asyncio
async def test_unrestorable_fallback_signal_handler_fails_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    modules = tmp_path / "modules"
    modules.mkdir()
    config_path = tmp_path / "config.yaml"
    _write_config(
        config_path,
        {
            "modules_directory": str(modules),
            "enabled_modules": [],
            "modules": {},
        },
    )
    loop = asyncio.get_running_loop()
    diagnostics: list[str] = []

    def unsupported_handler_api(
        _signum: signal.Signals, _callback: object
    ) -> None:
        raise NotImplementedError

    monkeypatch.setattr(loop, "add_signal_handler", unsupported_handler_api)
    monkeypatch.setattr(application.signal, "getsignal", lambda _signum: None)

    status = await application.run(
        config_path,
        ready_reporter=lambda _message: pytest.fail("reported readiness"),
        diagnostic_reporter=diagnostics.append,
    )

    assert status == 1
    assert diagnostics == ["signal handlers: could not be installed"]


def test_main_requires_config_argument(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def unexpected_run(_config_path: str) -> int:
        pytest.fail("run was called without the required CLI argument")

    monkeypatch.setattr(application, "run", unexpected_run)

    with pytest.raises(SystemExit) as raised:
        application.main([])

    assert raised.value.code == 2
    assert "the following arguments are required: --config" in capsys.readouterr().err


def test_main_propagates_run_status(monkeypatch: pytest.MonkeyPatch) -> None:
    received_paths: list[str] = []

    async def fake_run(config_path: str) -> int:
        received_paths.append(config_path)
        return 7

    monkeypatch.setattr(application, "run", fake_run)

    assert application.main(["--config", "chosen.yaml"]) == 7
    assert received_paths == ["chosen.yaml"]


def test_main_treats_keyboard_interrupt_as_normal_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def interrupt(awaitable: Any) -> int:
        awaitable.close()
        raise KeyboardInterrupt

    monkeypatch.setattr(application.asyncio, "run", interrupt)

    assert application.main(["--config", "chosen.yaml"]) == 0


def test_main_sanitizes_unexpected_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    diagnostics: list[str] = []

    def fail(awaitable: Any) -> int:
        awaitable.close()
        raise RuntimeError(_opaque("sensitive"))

    monkeypatch.setattr(application.asyncio, "run", fail)
    monkeypatch.setattr(
        application, "_default_diagnostic_reporter", diagnostics.append
    )

    assert application.main(["--config", "chosen.yaml"]) == 1
    assert diagnostics == ["application: unexpected failure"]


# --------------------------------------------------------------------------- #
# The explicit authorization rules and the trigger channel keys (R5, R1)
# --------------------------------------------------------------------------- #


_CHAT_WRITE_SPEC = {
    "name": "chat.write",
    "version": 1,
    "description": "Send one chat message",
    "argument_schema": {"type": "object"},
    "result_schema": {"type": "object"},
    "nature": "write",
    "required_permissions": ["chat.write"],
    "supported_destinations": [
        {"platform": "twitch", "channel_id": "*", "scope": "chat"}
    ],
    "timeout_seconds": 5,
    "idempotency": "none",
}


class _Provider:
    name = "recorder"

    async def invoke(self, invocation: Any) -> None:
        raise AssertionError("no call is made while listing authorized actions")


def _declared_registry(runtime: Any) -> Any:
    """The assembled registry with chat.write declared, bound and ready."""

    from core.contracts import ActionSpec, Destination

    spec = ActionSpec(
        name=_CHAT_WRITE_SPEC["name"],
        version=_CHAT_WRITE_SPEC["version"],
        description=_CHAT_WRITE_SPEC["description"],
        argument_schema=_CHAT_WRITE_SPEC["argument_schema"],
        result_schema=_CHAT_WRITE_SPEC["result_schema"],
        nature=_CHAT_WRITE_SPEC["nature"],
        required_permissions=tuple(_CHAT_WRITE_SPEC["required_permissions"]),
        supported_destinations=tuple(
            Destination(**entry)
            for entry in _CHAT_WRITE_SPEC["supported_destinations"]
        ),
        timeout_seconds=_CHAT_WRITE_SPEC["timeout_seconds"],
        idempotency=_CHAT_WRITE_SPEC["idempotency"],
    )
    registry = runtime.context.actions
    registry.declare(spec, module="brain")
    registry.bind(
        "chat.write",
        _Provider(),
        module="brain",
        destinations=None,
    )
    registry.mark_ready(module="brain")
    return registry


def test_assembled_runtime_grants_only_the_configured_action_rules() -> None:
    """R5: the actions block is the one source of grants, and none is default."""

    from core.contracts import Destination

    rule = {
        "rule_id": "brain-delivers-chat-replies",
        "action_name": "chat.write",
        "destination": {
            "platform": "twitch",
            "channel_id": "42",
            "scope": "chat",
        },
        "principals": ["brain"],
        "natures": ["write"],
        "granted_permissions": ["chat.write"],
    }
    config = {"limits": _finite_limits(), "actions": [rule]}

    granted = _declared_registry(application._assemble_runtime(config))
    assert set(
        granted.authorized(
            principal="brain", destination=Destination("twitch", "42", "chat")
        )
    ) == {"chat.write"}
    # Default-deny everywhere the rule does not reach: another channel,
    # another principal, and the whole block absent.
    assert not granted.authorized(
        principal="brain", destination=Destination("twitch", "43", "chat")
    )
    assert not granted.authorized(
        principal="viewer", destination=Destination("twitch", "42", "chat")
    )

    refused = _declared_registry(application._assemble_runtime(dict(config, actions=[])))
    assert not refused.authorized(
        principal="brain", destination=Destination("twitch", "42", "chat")
    )


@pytest.mark.parametrize(
    ("rule", "diagnostic"),
    [
        ({"rule_id": "x", "natures": ["scribble"]},
         "actions[0].natures: must be one of read, write (scribble)"),
        ({"rule_id": "x", "principals": []},
         "actions[0].principals: must be a list of non-empty strings"),
        ({"rule_id": "x", "bogus": 1},
         "actions[0]: is not a known authorization rule key (bogus)"),
        ({"action_name": "chat.write"},
         "actions[0].rule_id: must be a non-empty string"),
        ("not-a-mapping", "actions[0]: must be a mapping"),
        ({"rule_id": "x", "destination": "twitch"},
         "actions[0].destination: must be a mapping"),
        ({"rule_id": "x", "destination": {"bogus": "twitch"}},
         "actions[0].destination: is not a known destination key (bogus)"),
    ],
)
def test_load_config_validates_the_actions_block(
    tmp_path: Path, rule: object, diagnostic: str
) -> None:
    """R5: a malformed grant stops startup before any runtime exists."""

    config = _valid_config("./modules", tmp_path / "unused.log")
    config["actions"] = [rule]
    config_path = tmp_path / "config.yaml"
    _write_config(config_path, config)

    with pytest.raises(
        application.ConfigurationError, match=f"^{re.escape(diagnostic)}$"
    ):
        application.load_config(config_path, environ={})


def test_load_config_resolves_environment_references_in_mapping_keys(
    tmp_path: Path,
) -> None:
    """R1: the channel a policy selects may be named by ${NAME}."""

    config = _valid_config("./modules", tmp_path / "unused.log")
    config["triggers"] = {
        "twitch": {"channels": {"${CHANNEL}": {"rules": []}}}
    }
    config_path = tmp_path / "config.yaml"
    _write_config(config_path, config)

    loaded = application.load_config(
        config_path, environ={"CHANNEL": "42"}
    )

    assert next(iter(loaded["triggers"]["twitch"]["channels"])) == "42"

    with pytest.raises(
        application.ConfigurationError,
        match=r"^triggers\.twitch\.channels\.\$\{CHANNEL\}: "
        r"environment reference is unresolved$",
    ):
        application.load_config(config_path, environ={})
