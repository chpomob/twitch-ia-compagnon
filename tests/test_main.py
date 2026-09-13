from __future__ import annotations

import asyncio
import json
import math
import os
import re
import signal
import sys
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable
from uuid import uuid4

import pytest
import yaml

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised on the 3.10 floor only
    import tomli as tomllib

import core.main as application
from core.bus import EventBus
from core.contracts import (
    COUNTER_AUDIT_RECORD_LOSSES,
    COUNTER_LOST_TRACES,
    COUNTER_TRIGGER_REJECTIONS,
    TRACE_BRAIN_ADMISSION_ACCEPTED,
    TRACE_BRAIN_ADMISSION_REJECTED,
    TRACE_BRAIN_RUN_COMPLETED,
)
from core.loader import ModuleLoader
from core.runtime import Supervision
from conftest import FakeResponse, FakeSession, completion, wait_until
from test_integration import (
    FakeTwitchSession,
    FakeWebSocket,
    _environment,
    notification,
    sent_response,
    welcome,
)


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

MODULE_NAME = Path(__file__).parent.name


def validate_settings(settings):
    # The module, not the core, knows which of its settings are required:
    # one diagnostic per offending field, naming module and field, as the
    # shipped modules' hooks do (R7).
    diagnostics = []
    for field_name in ("access_token", "api_key"):
        value = settings.get(field_name)
        if not isinstance(value, str) or not value.strip():
            diagnostics.append(
                f"module {MODULE_NAME!r}: field {field_name!r}: must be a non-empty string"
            )
    return diagnostics
"""


ECHOING_FIELD_HOOK_SOURCE = PHASED_MODULE_SOURCE + """

MODULE_NAME = Path(__file__).parent.name


def validate_settings(settings):
    # A careless module-authored diagnostic that names the field and echoes
    # the rejected credential: the field must reach the report, the value
    # must not (R7, AC24).
    return [
        f"module {MODULE_NAME!r}: field 'api_key': rejected {settings.get('api_key')!r}"
    ]
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
    credentials: list[str] | None = None,
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
    if credentials is not None:
        manifest["credentials"] = credentials
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
async def test_a_failed_activation_is_unwound_under_one_shared_cleanup_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R4 (P24 N4): both cleanup owners are given the same absolute deadline.

    A partial startup has two cleanup owners: the coordinator unwinding the
    handles already returned, and the loader closing a handle an abandoned
    activation returns later. The entry point establishes one absolute
    shutdown deadline on the runtime's clock, before the coordinator's
    unwinding begins, and hands that same point to the coordinator's stop,
    to the loader ahead of any late close, and to the settle it awaits — so
    neither owner takes a budget of its own.
    """

    stops: list[dict[str, Any]] = []
    settles: list[tuple[float | None, float | None]] = []
    real_coordinator = application.PhaseCoordinator
    real_loader = application.ModuleLoader

    class RecordingCoordinator(real_coordinator):  # type: ignore[misc, valid-type]
        async def stop(self, **options: Any) -> Any:
            stops.append(dict(options))
            return await super().stop(**options)

    class RecordingLoader(real_loader):  # type: ignore[misc, valid-type]
        async def settle_late_results(self, **options: Any) -> Any:
            settles.append((options.get("deadline_at"), self.cleanup_deadline_at))
            return await super().settle_late_results(**options)

    monkeypatch.setattr(application, "PhaseCoordinator", RecordingCoordinator)
    monkeypatch.setattr(application, "ModuleLoader", RecordingLoader)
    modules = tmp_path / "modules"
    modules.mkdir()
    _make_phased_module(modules, "relay")
    _make_phased_module(
        modules,
        "boom",
        source=PHASED_MODULE_SOURCE.replace(
            'async def activate(context, settings, catalog):\n'
            '    record(settings, "activate")\n',
            'async def activate(context, settings, catalog):\n'
            '    record(settings, "activate")\n'
            '    raise RuntimeError(settings["label"])\n',
        ),
    )
    lifecycle_log = tmp_path / "lifecycle.log"
    config_path = tmp_path / "config.yaml"
    _write_config(
        config_path, _phased_config("./modules", lifecycle_log, ("relay", "boom"))
    )
    diagnostics: list[str] = []

    before = time.monotonic()
    status = await application.run(
        config_path,
        asyncio.Event(),
        ready_reporter=lambda _message: pytest.fail("became ready"),
        diagnostic_reporter=diagnostics.append,
    )
    after = time.monotonic()

    assert status == 1
    assert diagnostics == ["module 'boom': field 'activate': activation failed"]
    assert lifecycle_log.read_text(encoding="utf-8").splitlines() == [
        "activate:relay",
        "activate:boom",
        "drain:relay",
        "close:relay",
    ]
    # One absolute point, taken when the failure was met and shared by both
    # owners: the coordinator's stop, the loader before any late close could
    # begin, and the settle the entry point awaits after the coordinator.
    assert len(stops) == 1 and len(settles) == 1
    deadline_at = stops[0]["deadline_at"]
    assert settles[0] == (deadline_at, deadline_at)
    budget = application._SHUTDOWN_DEADLINE_SECONDS
    assert before + budget <= deadline_at <= after + budget


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
async def test_a_hanging_settings_validator_is_bounded_by_the_startup_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R4: the global startup deadline starts before enabled-module validation.

    An enabled module whose declared settings hook never returns cannot
    block startup indefinitely: the hook is cancelled under a bounded grace
    at the deadline, and the refusal names the module and its hook, with no
    module ever activated (AC24).
    """

    modules = tmp_path / "modules"
    modules.mkdir()
    _make_phased_module(modules, "relay")
    _make_phased_module(
        modules,
        "hang",
        settings_validator="validate_settings",
        source=(
            PHASED_MODULE_SOURCE.replace("import json\n", "import asyncio\nimport json\n", 1)
            + "\n\n"
            + "async def validate_settings(settings):\n"
            + "    await asyncio.Future()\n"
        ),
    )
    lifecycle_log = tmp_path / "lifecycle.log"
    config_path = tmp_path / "config.yaml"
    _write_config(
        config_path, _phased_config("./modules", lifecycle_log, ("relay", "hang"))
    )
    monkeypatch.setattr(application, "_STARTUP_DEADLINE_SECONDS", 0.05)
    readiness: list[str] = []
    diagnostics: list[str] = []

    status = await asyncio.wait_for(
        application.run(
            config_path,
            asyncio.Event(),
            ready_reporter=readiness.append,
            diagnostic_reporter=diagnostics.append,
        ),
        timeout=2,
    )

    assert status != 0
    assert readiness == []
    assert diagnostics == [
        "module 'hang': field 'validate_settings': exceeded the global startup "
        "deadline"
    ]
    # Refused before activation: no handle was ever returned to clean up.
    assert not lifecycle_log.exists()


@pytest.mark.asyncio
async def test_a_hanging_activation_is_bounded_by_the_startup_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R4: activation is inside the same global startup deadline (F2).

    The second module's ``activate`` never returns. Loading is bounded by
    the one deadline, the hanging activation is cancelled under a bounded
    grace, the refusal names the module, and the handle the first module
    already returned is closed through the ordinary shutdown sequence.
    """

    modules = tmp_path / "modules"
    modules.mkdir()
    _make_phased_module(modules, "relay")
    _make_phased_module(
        modules,
        "hang",
        source=PHASED_MODULE_SOURCE.replace(
            "import json\n", "import asyncio\nimport json\n", 1
        ).replace(
            'async def activate(context, settings, catalog):\n'
            '    record(settings, "activate")\n',
            'async def activate(context, settings, catalog):\n'
            '    record(settings, "activate")\n'
            '    await asyncio.Future()\n',
        ),
    )
    lifecycle_log = tmp_path / "lifecycle.log"
    config_path = tmp_path / "config.yaml"
    _write_config(
        config_path, _phased_config("./modules", lifecycle_log, ("relay", "hang"))
    )
    monkeypatch.setattr(application, "_STARTUP_DEADLINE_SECONDS", 0.05)
    readiness: list[str] = []
    diagnostics: list[str] = []

    status = await asyncio.wait_for(
        application.run(
            config_path,
            asyncio.Event(),
            ready_reporter=readiness.append,
            diagnostic_reporter=diagnostics.append,
        ),
        timeout=2,
    )

    assert status != 0
    assert readiness == []
    assert diagnostics == [
        "module 'hang': field 'activate': exceeded the global startup deadline"
    ]
    # The returned handle of the first module is cleaned up, not leaked.
    assert lifecycle_log.read_text(encoding="utf-8").splitlines() == [
        "activate:relay",
        "activate:hang",
        "drain:relay",
        "close:relay",
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
    the field: the module's own diagnostic names it, the entry point reports
    it as the module wrote it, and 0 configured values are echoed (AC24).
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
    # Redaction is literal: the configured value ``["not", "a", "string"]``
    # puts the word "string" among the configured values, so it is cut out
    # of the module's own explanation too. Module and field still stand.
    reason = "must be a non-empty string"
    if isinstance(invalid_value, list):
        reason = "must be a non-empty <redacted>"
    assert diagnostics == [f"module {module_name!r}: field {field_name!r}: {reason}"]
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

    With both modules refusing their settings, startup stops reporting
    *both* diagnostics — the first module's, naming its module and field
    with the credential it echoed redacted; the second's, whose hook raised,
    naming the module and the hook — and echoing 0 credential values; with
    only the second refusing, the first — valid — module is still never
    activated, so 0 transports are opened either way.
    """

    modules = tmp_path / "modules"
    modules.mkdir()
    _make_phased_module(
        modules,
        "gateway",
        roles=("input",),
        source=ECHOING_FIELD_HOOK_SOURCE if invalid == "both" else ACCEPTING_HOOK_SOURCE,
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

    assert status != 0
    assert readiness == []
    engine = "module 'engine': field 'validate_settings': settings were refused by the module"
    if invalid == "both":
        assert diagnostics == [
            "module 'gateway': field 'api_key': rejected '<redacted>'",
            engine,
        ]
    else:
        assert diagnostics == [engine]
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
    """The v1 compatibility route: activation order is the only order it has (R4)."""

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
async def test_versioned_activation_failure_unwinds_prepared_modules_by_phase(
    tmp_path: Path,
) -> None:
    """Phase-ordered counterpart of the v1 partial-startup test (R4, AC15).

    Three versioned modules; the last one fails inside ``activate`` with the
    configured credential in its message. The loader hands the two already
    activated to the coordinator, which unwinds them through the declared
    phases — the producer stopped and drained before its close, the
    observation service flushed and closed after every ordinary resource —
    under one shutdown deadline, with a non-zero status, a diagnostic naming
    the failing module and 0 credential values echoed.
    """

    modules = tmp_path / "modules"
    modules.mkdir()
    _make_phased_module(modules, "alpha", roles=("observation",))
    _make_phased_module(modules, "source", roles=("input",))
    _make_phased_module(
        modules,
        "relay",
        source=PHASED_MODULE_SOURCE.replace(
            'record(settings, "activate")',
            'raise RuntimeError(settings["api_key"])',
        ),
    )
    lifecycle_log = tmp_path / "lifecycle.log"
    config = _phased_config("./modules", lifecycle_log, ("alpha", "source", "relay"))
    secret = _opaque("key")
    config["modules"]["relay"]["api_key"] = secret
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
    assert diagnostics == ["module 'relay': field 'activate': activation failed"]
    assert secret not in "\n".join(diagnostics)
    assert lifecycle_log.read_text(encoding="utf-8").splitlines() == [
        "activate:alpha",
        "activate:source",
        # Never prepared or started, still stopped and drained: the hooks are
        # idempotent by contract, and nothing was skipped.
        "stop_inputs:source",
        "drain:source",
        "drain:alpha",
        "close:source",
        "flush:alpha",
        "close:alpha",
    ]


@pytest.mark.asyncio
async def test_shutdown_failure_is_nonzero_and_does_not_skip_other_modules(
    tmp_path: Path,
) -> None:
    """The v1 compatibility route: one ``close()`` each, in activation order (R4)."""

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
async def test_versioned_close_failure_is_nonzero_and_later_phases_still_run(
    tmp_path: Path,
) -> None:
    """Phase-ordered counterpart of the v1 shutdown-failure test (R4).

    A versioned module whose ``close`` raises — with the configured
    credential in its message — is reported against its module and phase,
    the status is non-zero, and the phases that follow still run: the other
    ordinary resource closes and the declared observation service flushes and
    closes last, so the failure's own trace has somewhere to go.
    """

    modules = tmp_path / "modules"
    modules.mkdir()
    _make_phased_module(modules, "alpha", roles=("observation",))
    _make_phased_module(modules, "source", roles=("input",))
    _make_phased_module(
        modules,
        "relay",
        source=PHASED_MODULE_SOURCE.replace(
            'record(self.settings, "close")',
            'record(self.settings, "close")\n'
            '        raise RuntimeError(self.settings["api_key"])',
        ),
    )
    lifecycle_log = tmp_path / "lifecycle.log"
    config = _phased_config("./modules", lifecycle_log, ("alpha", "source", "relay"))
    secret = _opaque("key")
    config["modules"]["relay"]["api_key"] = secret
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
    assert diagnostics == ["module 'relay': phase 'close' failed"]
    assert secret not in "\n".join(diagnostics)
    assert lifecycle_log.read_text(encoding="utf-8").splitlines()[-8:] == [
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


# --------------------------------------------------------------------------- #
# The accepted limits control their owners (R6, AC32 — P24 F6)
# --------------------------------------------------------------------------- #

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_CONFIG = ROOT / "config.yaml.example"


def _example_config() -> dict[str, Any]:
    """The shipped example, pointed at the shipped modules by absolute path."""

    config = yaml.safe_load(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
    config["modules_directory"] = str(ROOT / "modules")
    return config


def _inject_real_module_boundaries(
    monkeypatch: pytest.MonkeyPatch,
    *,
    twitch_session_factory: Callable[[], Any],
    brain_session_factory: Callable[[], Any],
    audit_writer: Callable[[str], Any],
    diagnostics: list[str],
) -> None:
    """Point the shipped modules' transport seams at fakes, after the real load."""

    real_load_config = application.load_config

    def load_with_boundaries(
        path: str | Path, *, environ: Mapping[str, str] | None = None
    ) -> dict[str, Any]:
        config = real_load_config(path, environ=environ)
        config["modules"]["twitch"].update(
            {"_session_factory": twitch_session_factory, "diagnostic_reporter": diagnostics.append}
        )
        config["modules"]["brain"].update(
            {"_session_factory": brain_session_factory, "diagnostic_reporter": diagnostics.append}
        )
        config["modules"]["audit"]["_writer"] = audit_writer
        return config

    monkeypatch.setattr(application, "load_config", load_with_boundaries)


def _capture_supervision(monkeypatch: pytest.MonkeyPatch) -> list[Supervision]:
    """Every supervision facade the entry point builds, in construction order."""

    built: list[Supervision] = []
    real = application.Supervision

    class Recording(real):  # type: ignore[misc, valid-type]
        def __init__(self, bus: Any, **options: Any) -> None:
            super().__init__(bus, **options)
            built.append(self)

    monkeypatch.setattr(application, "Supervision", Recording)
    return built


class _GatedModelSession(FakeSession):
    """A model transport that holds its first request until released."""

    def __init__(self, *results: Any) -> None:
        super().__init__(*results)
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def post(self, url: str, **kwargs: Any) -> Any:
        self.post_calls.append({"url": url, **kwargs})
        result = self.results.pop(0)
        if len(self.post_calls) == 1:
            self.entered.set()
            await self.release.wait()
        return result


def test_load_config_hands_the_accepted_limits_to_every_enabled_module(
    tmp_path: Path,
) -> None:
    """R6 (P24 F6): the accepted block reaches each enabled module as its
    reserved ``limits`` setting, as a copy of its own; a disabled module is
    handed nothing; a module setting already named ``limits`` is refused."""

    modules = tmp_path / "modules"
    modules.mkdir()
    config = _phased_config("./modules", tmp_path / "unused.log", ("source", "sink"))
    config["enabled_modules"] = ["source"]
    config_path = tmp_path / "config.yaml"
    _write_config(config_path, config)

    loaded = application.load_config(config_path)

    assert loaded["modules"]["source"]["limits"] == _finite_limits()
    assert loaded["modules"]["source"]["limits"] is not loaded["limits"]
    assert loaded["modules"]["source"]["limits"]["admission"] is not loaded["limits"]["admission"]
    assert "limits" not in loaded["modules"]["sink"]
    assert loaded["modules"]["source"]["label"] == "source"

    config["modules"]["source"]["limits"] = {"admission": {"workers": 1}}
    _write_config(config_path, config)
    with pytest.raises(application.ConfigurationError) as caught:
        application.load_config(config_path)
    assert str(caught.value) == "modules.source.limits: is reserved for the accepted limits block"

    del config["limits"]
    del config["modules"]["source"]["limits"]
    _write_config(config_path, config)
    assert "limits" not in application.load_config(config_path)["modules"]["source"]


@pytest.mark.asyncio
async def test_a_module_copy_differing_from_the_accepted_limits_stops_startup_before_any_transport(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """R6 (P24 F6): with the shipped modules and deliberately different root
    and module values for the scheduler, the memory and the audit queue,
    startup stops naming each differing field, with 0 sessions created and
    0 transports opened — no bound ever exists that the accepted block did
    not set."""

    environ = _environment()
    config = _example_config()
    config["limits"]["admission"]["workers"] = 2
    config["modules"]["brain"]["admission"]["workers"] = 4
    config["limits"]["conversation_memory"]["max_exchanges"] = 3
    config["modules"]["brain"]["conversation_memory"]["max_exchanges"] = 20
    config["limits"]["observation_queue"]["max_records"] = 10
    config["modules"]["audit"]["queue"]["max_records"] = 1000
    config_path = tmp_path / "config.yaml"
    _write_config(config_path, config)
    sessions: list[str] = []
    diagnostics: list[str] = []
    readiness: list[str] = []
    _inject_real_module_boundaries(
        monkeypatch,
        twitch_session_factory=lambda: sessions.append("twitch"),
        brain_session_factory=lambda: sessions.append("brain"),
        audit_writer=lambda line: sessions.append("audit"),
        diagnostics=diagnostics,
    )

    status = await application.run(
        config_path,
        asyncio.Event(),
        environ=environ,
        ready_reporter=readiness.append,
        diagnostic_reporter=diagnostics.append,
    )

    assert status == 1
    assert readiness == []
    assert diagnostics == [
        "module 'brain': field 'admission.workers': must equal limits.admission.workers",
        "module 'brain': field 'conversation_memory.max_exchanges': "
        "must equal limits.conversation_memory.max_exchanges",
        "module 'audit': field 'queue.max_records': "
        "must equal limits.observation_queue.max_records",
    ]
    assert sessions == []
    rendered = "\n".join(diagnostics)
    for value in ("2", "4", "3", "20", "10", "1000"):
        assert value not in rendered.replace("'", "")


@pytest.mark.asyncio
async def test_the_accepted_limits_bound_the_real_scheduler_memory_and_audit_queue(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """R2/R6 (P24 F6): through ``core.main.run`` and the shipped modules, the
    bounds observed in behaviour are the accepted ones — a session queue of
    1 rejects the third message of a session at depth 1 while its first run
    is held, a memory of 1 exchange leaves exactly 1 earlier exchange in the
    next request, and an observation queue of 16 records admits exactly 14
    more records behind a held writer and drops the 10 offered after that —
    none of which the example's default values (4, 20, 1000) would produce."""

    environ = _environment()
    channel = environ["TWITCH_BROADCASTER_ID"]
    admission = {
        "session_queue_capacity": 1,
        "global_pending_capacity": 8,
        "max_sessions": 4,
        "workers": 1,
        "wait_seconds": 5,
        "total_run_seconds": 20,
    }
    memory = {"max_sessions": 4, "max_exchanges": 1, "max_bytes": 65536, "max_age_seconds": 3600}
    queue = {"max_records": 16, "max_bytes": 65536}
    config = _example_config()
    config["limits"]["admission"] = dict(admission)
    config["limits"]["conversation_memory"] = dict(memory)
    config["limits"]["observation_queue"] = dict(queue)
    config["modules"]["brain"]["admission"] = dict(admission)
    config["modules"]["brain"]["conversation_memory"] = dict(memory)
    config["modules"]["audit"]["queue"] = dict(queue)
    config_path = tmp_path / "config.yaml"
    _write_config(config_path, config)

    websocket = FakeWebSocket(welcome())
    twitch_session = FakeTwitchSession(
        client_id=environ["TWITCH_CLIENT_ID"],
        bot_user_id=environ["TWITCH_BOT_USER_ID"],
        websocket=websocket,
        sends=[sent_response(f"sent-{index}") for index in range(3)],
    )
    model = _GatedModelSession(
        *(FakeResponse(200, completion(f"reply-{index}")) for index in range(1, 4))
    )
    audit_lines: list[str] = []
    writer_blocked = asyncio.Event()
    writer_release = asyncio.Event()

    async def write_audit(line: str) -> None:
        record = json.loads(line)
        if record["type"] == "channel.chat.message" and record["payload"].get("message_id") == "marker":
            writer_blocked.set()
            await writer_release.wait()
        audit_lines.append(line)

    def records_of(event_type: str) -> list[dict[str, Any]]:
        return [
            record
            for record in (json.loads(line) for line in audit_lines)
            if record["type"] == event_type
        ]

    diagnostics: list[str] = []
    _inject_real_module_boundaries(
        monkeypatch,
        twitch_session_factory=lambda: twitch_session,
        brain_session_factory=lambda: model,
        audit_writer=write_audit,
        diagnostics=diagnostics,
    )
    supervisions = _capture_supervision(monkeypatch)
    stop = asyncio.Event()
    ready = asyncio.Event()
    task = asyncio.create_task(
        application.run(
            config_path,
            stop,
            environ=environ,
            ready_reporter=lambda _message: ready.set(),
            diagnostic_reporter=diagnostics.append,
        )
    )
    try:
        await asyncio.wait_for(ready.wait(), timeout=1)
        (supervision,) = supervisions

        # Scheduler: the first run of session A is held in its model call; the
        # second message queues at depth 1; the third is refused at depth 1.
        websocket.feed(notification(channel, "a-1", "!ask first question", viewer_id="viewer-a"))
        await asyncio.wait_for(model.entered.wait(), timeout=1)
        websocket.feed(notification(channel, "a-2", "!ask second question", viewer_id="viewer-a"))
        await wait_until(lambda: len(records_of(TRACE_BRAIN_ADMISSION_ACCEPTED)) == 2)
        websocket.feed(notification(channel, "a-3", "!ask third question", viewer_id="viewer-a"))
        await wait_until(lambda: len(records_of(TRACE_BRAIN_ADMISSION_REJECTED)) == 1)
        (rejected,) = records_of(TRACE_BRAIN_ADMISSION_REJECTED)
        assert rejected["payload"]["source_event_id"] == "a-3"
        assert rejected["payload"]["reason"] == "session_queue_full"
        assert rejected["payload"]["queue_depth"] == 1
        assert len(model.post_calls) == 1

        # Memory: after 2 completed exchanges, the next request carries
        # exactly 1 earlier exchange — the second, not the first.
        model.release.set()
        await wait_until(lambda: len(records_of(TRACE_BRAIN_RUN_COMPLETED)) == 2)
        websocket.feed(notification(channel, "a-4", "!ask fourth question", viewer_id="viewer-a"))
        await wait_until(lambda: len(records_of(TRACE_BRAIN_RUN_COMPLETED)) == 3)
        assert len(model.post_calls) == 3
        messages = model.post_calls[2]["json"]["messages"]
        history = messages[1:-1]
        assert len(history) == 2
        assert [message["role"] for message in history] == ["user", "assistant"]
        assert "source_message_id: a-2" in history[0]["content"]
        assert history[1]["content"] == "reply-2"
        assert "source_message_id: a-1" not in json.dumps(messages)
        assert "source_message_id: a-4" in messages[-1]["content"]

        # Audit queue: the writer holds the marker's first record and its
        # second is admitted behind it; of the 24 records a burst of 12
        # refused messages then offers (message and refusal each), the
        # first 14 fill the queue of 16 and the 10 after that are dropped
        # and counted — the newly offered record, never an admitted one.
        assert supervision.snapshot()[COUNTER_AUDIT_RECORD_LOSSES] == 0
        websocket.feed(notification(channel, "marker", "hello there", viewer_id="viewer-b"))
        await asyncio.wait_for(writer_blocked.wait(), timeout=1)
        for index in range(12):
            websocket.feed(
                notification(channel, f"burst-{index:02d}", "hello again", viewer_id="viewer-b")
            )
        await wait_until(lambda: supervision.snapshot()[COUNTER_TRIGGER_REJECTIONS] == 13)
        assert supervision.snapshot()[COUNTER_AUDIT_RECORD_LOSSES] == 10
        assert not any("burst-" in line for line in audit_lines)
    finally:
        # Whatever failed above, nothing stays held: the shutdown that
        # follows must not wait on a writer or a model this test holds.
        writer_release.set()
        model.release.set()
        stop.set()
    assert await asyncio.wait_for(task, timeout=2) == 0

    assert diagnostics == []
    assert supervision.snapshot()[COUNTER_AUDIT_RECORD_LOSSES] == 10
    records = [json.loads(line) for line in audit_lines]
    assert [
        record["type"]
        for record in records
        if record["payload"].get("message_id") == "marker"
        or record["payload"].get("source_event_id") == "marker"
    ] == ["channel.chat.message", "input.trigger.rejected"]
    burst_records = [
        (record["type"], record["payload"].get("message_id") or record["payload"].get("source_event_id"))
        for record in records
        if str(record["payload"].get("message_id", "")).startswith("burst-")
        or str(record["payload"].get("source_event_id", "")).startswith("burst-")
    ]
    assert burst_records == [
        (kind, f"burst-{index:02d}")
        for index in range(7)
        for kind in ("channel.chat.message", "input.trigger.rejected")
    ]
    assert [call["json"]["message"] for call in twitch_session.helix_calls] == [
        "reply-1",
        "reply-2",
        "reply-3",
    ]


# --------------------------------------------------------------------------- #
# Configured secrets reach supervision before any module exists (R8 — P24 F7)
# --------------------------------------------------------------------------- #

LEAKING_PROBE_SOURCE = '''
"""A module that puts its configured credential into a trace and a lost trace.

At `prepare` it emits one diagnostic trace carrying the credential verbatim,
nested and embedded; then it publishes a terminal-style trace whose handler
raises with the credential in its message, so the publication is lost and
its diagnostic is retained by supervision. What the bus delivered is logged.
"""

import json
from pathlib import Path


class Handle:
    def __init__(self, context, settings):
        self.context = context
        self.settings = settings

    def log(self, entry):
        with Path(self.settings["log"]).open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(entry, sort_keys=True) + "\\n")

    async def prepare(self):
        token = self.settings["token"]
        bus = self.context.bus
        bus.subscribe("probe.diagnostic", lambda event: self.log({"trace": event}))

        def explode(event):
            raise RuntimeError("handler refused " + token)

        bus.subscribe("probe.lost", explode)
        await self.context.supervision.emit(
            "probe.diagnostic",
            {
                "reason": "backend refused " + token,
                "detail": {"nested": {"credential": token}},
                "identifiers": [token, "id-" + token + "-suffix"],
            },
            metadata={"note": token},
        )
        await self.context.supervision.record_and_emit(
            lambda: None, "probe.lost", {"reason": token}
        )
        self.log({"snapshot": dict(self.context.supervision.snapshot())})

    async def close(self):
        return None


async def activate(context, settings, catalog):
    return Handle(context, settings)
'''


class _RecordingEnviron(dict):
    """An environment that records every variable looked up or tested."""

    def __init__(self, values: dict[str, str]) -> None:
        super().__init__(values)
        self.queried: list[str] = []

    def __contains__(self, key: object) -> bool:
        self.queried.append(str(key))
        return super().__contains__(key)

    def __getitem__(self, key: str) -> str:
        self.queried.append(key)
        return super().__getitem__(key)


@pytest.mark.asyncio
async def test_configured_secrets_are_redacted_from_traces_and_loss_diagnostics_through_the_entry_point(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """R8/AC29 (P24 F7): a credential the ``secrets`` block names reaches
    the entry point's supervision before any module exists, so a diagnostic
    trace carrying it verbatim, nested, embedded and in metadata is delivered
    redacted, and the diagnostic of a lost trace whose failure text carried
    it is retained redacted — while a disabled module's listed credential is
    never looked up."""

    modules = tmp_path / "modules"
    modules.mkdir()
    _make_phased_module(modules, "probe", source=LEAKING_PROBE_SOURCE)
    _make_phased_module(modules, "vault")
    log = tmp_path / "probe.jsonl"
    token = _opaque("credential")
    config = _phased_config("./modules", tmp_path / "lifecycle.log", ("probe", "vault"))
    config["enabled_modules"] = ["probe"]
    config["modules"]["probe"].update({"token": "${PROBE_TOKEN}", "log": str(log)})
    config["modules"]["vault"]["token"] = "${VAULT_TOKEN}"
    config["secrets"] = ["${PROBE_TOKEN}", "${VAULT_TOKEN}"]
    config_path = tmp_path / "config.yaml"
    _write_config(config_path, config)
    environ = _RecordingEnviron({"PROBE_TOKEN": token})
    supervisions = _capture_supervision(monkeypatch)
    stop = asyncio.Event()
    stop.set()

    status = await application.run(
        config_path,
        stop,
        environ=environ,
        ready_reporter=lambda _message: None,
        diagnostic_reporter=lambda message: pytest.fail(message),
    )

    assert status == 0
    assert "VAULT_TOKEN" not in environ.queried
    entries = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert [sorted(entry) for entry in entries] == [["trace"], ["snapshot"]]

    delivered = entries[0]["trace"]
    rendered = json.dumps(delivered)
    assert token not in rendered
    assert delivered["payload"]["reason"] != "backend refused " + token
    assert token not in delivered["payload"]["reason"]
    assert token not in delivered["payload"]["detail"]["nested"]["credential"]
    assert all(token not in item for item in delivered["payload"]["identifiers"])
    assert token not in delivered["metadata"]["note"]
    assert "backend refused" in delivered["payload"]["reason"]
    assert "id-" in delivered["payload"]["identifiers"][1]

    (supervision,) = supervisions
    assert entries[1]["snapshot"][COUNTER_LOST_TRACES] == 1
    (loss,) = supervision.losses
    assert loss.startswith("probe.lost: PublicationError")
    assert "handler refused" in loss
    assert token not in loss


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case", ["list-absent", "list-incomplete", "literal-value", "literal-and-incomplete"]
)
async def test_declared_credentials_are_redacted_without_a_secrets_entry_through_the_entry_point(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, case: str
) -> None:
    """R8/AC29 (P24 F7): redaction is not opt-in. A credential the module's
    manifest declares reaches supervision before the module is activated —
    with the ``secrets`` block absent, with a block that lists another
    reference but not this one, and when the credential is configured as a
    literal value no reference could ever list — so the trace carrying it
    verbatim, nested, embedded and in metadata is delivered redacted, and
    the diagnostic of the lost trace whose failure text carried it is
    retained redacted."""

    modules = tmp_path / "modules"
    modules.mkdir()
    _make_phased_module(
        modules,
        "probe",
        source=LEAKING_PROBE_SOURCE,
        settings_schema={
            "type": "object",
            "properties": {"token": {"type": "string"}, "other": {"type": "string"}},
            "required": ["token"],
        },
        credentials=["token"],
    )
    log = tmp_path / "probe.jsonl"
    token = _opaque("credential")
    other = _opaque("other")
    config = _phased_config("./modules", tmp_path / "lifecycle.log", ("probe",))
    config["modules"]["probe"]["log"] = str(log)
    config["modules"]["probe"]["other"] = "${OTHER_TOKEN}"
    environ = {"OTHER_TOKEN": other}
    if case.startswith("literal"):
        config["modules"]["probe"]["token"] = token
    else:
        config["modules"]["probe"]["token"] = "${PROBE_TOKEN}"
        environ["PROBE_TOKEN"] = token
    if case.endswith("incomplete"):
        config["secrets"] = ["${OTHER_TOKEN}"]
    else:
        assert "secrets" not in config
    config_path = tmp_path / "config.yaml"
    _write_config(config_path, config)
    supervisions = _capture_supervision(monkeypatch)
    stop = asyncio.Event()
    stop.set()

    status = await application.run(
        config_path,
        stop,
        environ=environ,
        ready_reporter=lambda _message: None,
        diagnostic_reporter=lambda message: pytest.fail(message),
    )

    assert status == 0
    entries = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert [sorted(entry) for entry in entries] == [["trace"], ["snapshot"]]

    delivered = entries[0]["trace"]
    assert token not in json.dumps(delivered)
    assert delivered["payload"]["reason"] != "backend refused " + token
    assert token not in delivered["payload"]["reason"]
    assert token not in delivered["payload"]["detail"]["nested"]["credential"]
    assert all(token not in item for item in delivered["payload"]["identifiers"])
    assert token not in delivered["metadata"]["note"]
    assert "backend refused" in delivered["payload"]["reason"]
    assert "id-" in delivered["payload"]["identifiers"][1]

    (supervision,) = supervisions
    assert entries[1]["snapshot"][COUNTER_LOST_TRACES] == 1
    (loss,) = supervision.losses
    assert loss.startswith("probe.lost: PublicationError")
    assert "handler refused" in loss
    assert token not in loss


def test_load_config_resolves_listed_secrets_only_where_an_enabled_setting_resolved_them(
    tmp_path: Path,
) -> None:
    """R7/R8: the block is replaced by the values its references resolved to
    through enabled settings — in list order, top-level references included —
    and a disabled module's listed credential stays unresolved. The block is
    a complement, not the credential list: the credentials proper are the
    settings each module's manifest declares, which the loader redacts from
    their accepted values whether or not they are listed here (P24 F7), so
    an absent block is accepted and hands the runtime an empty complement."""

    config = _valid_config("./modules", tmp_path / "unused.log")
    config["enabled_modules"] = ["twitch", "audit"]
    config["modules"]["twitch"]["access_token"] = "${TWITCH_ACCESS_TOKEN}"
    config["modules"]["twitch"]["broadcaster_id"] = "${TWITCH_BROADCASTER_ID}"
    config["modules"]["brain"]["api_key"] = "${MODEL_API_KEY}"
    config["secrets"] = ["${MODEL_API_KEY}", "${TWITCH_ACCESS_TOKEN}"]
    config_path = tmp_path / "config.yaml"
    _write_config(config_path, config)
    environ = _RecordingEnviron(
        {"TWITCH_ACCESS_TOKEN": _opaque("token"), "TWITCH_BROADCASTER_ID": _opaque("channel")}
    )

    loaded = application.load_config(config_path, environ=environ)

    assert loaded["secrets"] == [environ["TWITCH_ACCESS_TOKEN"]]
    assert "MODEL_API_KEY" not in environ.queried
    assert loaded["modules"]["brain"]["api_key"] == "${MODEL_API_KEY}"

    del config["secrets"]
    _write_config(config_path, config)
    loaded = application.load_config(config_path, environ=environ)
    assert loaded["secrets"] == []
    assert loaded["modules"]["twitch"]["access_token"] == environ["TWITCH_ACCESS_TOKEN"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("secrets", "expected_diagnostic"),
    [
        pytest.param({"a": 1}, "secrets: must be a list", id="not-a-list"),
        pytest.param(
            ["${TWITCH_ACCESS_TOKEN}", "literal-value"],
            "secrets[1]: must be an environment reference of the form ${NAME}",
            id="not-a-reference",
        ),
        pytest.param(
            ["${TWITCH_ACCESS_TOKEN}", "${TWITCH_ACCESS_TOKEN}"],
            "secrets[1]: must be unique",
            id="duplicate",
        ),
        pytest.param(
            ["${TWITCH_ACCES_TOKEN}"],
            "secrets[0]: is referenced by no setting",
            id="misspelt",
        ),
    ],
)
async def test_each_invalid_secrets_block_fails_before_readiness(
    tmp_path: Path, secrets: Any, expected_diagnostic: str
) -> None:
    """A malformed or misspelt secrets block stops startup naming the entry,
    before any module is activated, so a credential is never left unredacted
    by a typo."""

    modules = tmp_path / "modules"
    modules.mkdir()
    for name in ("twitch", "brain", "audit"):
        _make_module(modules, name)
    lifecycle_log = tmp_path / "lifecycle.log"
    config = _valid_config("./modules", lifecycle_log)
    config["modules"]["twitch"]["access_token"] = "${TWITCH_ACCESS_TOKEN}"
    config["secrets"] = secrets
    config_path = tmp_path / "config.yaml"
    _write_config(config_path, config)
    readiness: list[str] = []
    diagnostics: list[str] = []
    # Already set: a block that wrongly got past validation stops at once
    # with status 0 and fails below, rather than running forever.
    stop = asyncio.Event()
    stop.set()

    status = await application.run(
        config_path,
        stop,
        environ={"TWITCH_ACCESS_TOKEN": _opaque("token")},
        ready_reporter=readiness.append,
        diagnostic_reporter=diagnostics.append,
    )

    assert status == 2
    assert readiness == []
    assert diagnostics == [expected_diagnostic]
    assert not lifecycle_log.exists()


def test_example_config_names_its_credentials_and_nothing_traces_correlate_by() -> None:
    """The shipped example lists every credential reference and no identifier."""

    config = yaml.safe_load(EXAMPLE_CONFIG.read_text(encoding="utf-8"))

    assert config["secrets"] == [
        "${TWITCH_CLIENT_SECRET}",
        "${TWITCH_ACCESS_TOKEN}",
        "${OPENAI_API_KEY}",
        "${OPENAI_ENDPOINT}",
    ]
    environ = _environment()
    loaded = application.load_config(EXAMPLE_CONFIG, environ=environ)
    assert loaded["secrets"] == [
        environ["TWITCH_CLIENT_SECRET"],
        environ["TWITCH_ACCESS_TOKEN"],
        environ["OPENAI_API_KEY"],
        environ["OPENAI_ENDPOINT"],
    ]
    assert environ["TWITCH_BROADCASTER_ID"] not in loaded["secrets"]


# --------------------------------------------------------------------------- #
# `modules_directory: builtin` and `--check-config` (R7, R8; AC40, AC43)
# --------------------------------------------------------------------------- #


def _discovered_names(modules_directory: str) -> list[str]:
    """The manifests a loader over *modules_directory* discovers, by name."""

    loader = ModuleLoader(EventBus(), modules_directory)
    return sorted(loader._discover())


def _builtin_config(tmp_path: Path) -> Path:
    """The shipped example, pointed at the shipped modules by the literal."""

    config = yaml.safe_load(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
    config["modules_directory"] = application.MODULES_DIRECTORY_BUILTIN
    config_path = tmp_path / "config.yaml"
    _write_config(config_path, config)
    return config_path


def test_builtin_modules_directory_resolves_to_the_shipped_package(
    tmp_path: Path,
) -> None:
    """R8 (AC43 groundwork): the literal names the importable package, not a
    path relative to the configuration file; a loader over it discovers the
    same manifests as one over the checkout's ``./modules``."""

    environ = _environment()
    builtin = application.load_config(_builtin_config(tmp_path), environ=environ)

    relative = yaml.safe_load(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
    relative["modules_directory"] = os.path.relpath(ROOT / "modules", tmp_path)
    relative_path = tmp_path / "relative.yaml"
    _write_config(relative_path, relative)
    checkout = application.load_config(relative_path, environ=environ)

    assert builtin["modules_directory"] == str((ROOT / "modules").resolve())
    assert builtin["modules_directory"] == checkout["modules_directory"]
    names = _discovered_names(builtin["modules_directory"])
    assert names == _discovered_names(checkout["modules_directory"])
    assert names == sorted(
        child.name for child in (ROOT / "modules").iterdir()
        if (child / "module.yaml").is_file()
    )
    assert names


@pytest.mark.parametrize(
    ("spec", "reason"),
    [
        (None, "is not importable"),
        (SimpleNamespace(origin=None), "has no directory"),
        (SimpleNamespace(origin=""), "has no directory"),
    ],
)
def test_builtin_modules_directory_names_the_field_when_the_package_is_unresolvable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, spec: Any, reason: str
) -> None:
    """R8: a literal that selects a package the interpreter cannot import, or
    one with no directory, is a configuration defect naming the field."""

    monkeypatch.setattr(
        application.importlib.util, "find_spec", lambda _name: spec
    )

    with pytest.raises(
        application.ConfigurationError,
        match=rf"^modules_directory: builtin names a modules package that {reason}$",
    ):
        application.load_config(_builtin_config(tmp_path), environ=_environment())


def test_builtin_modules_directory_names_the_field_when_the_origin_is_gone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R8: an origin whose directory no longer exists names the field too."""

    missing = tmp_path / "gone" / "__init__.py"
    monkeypatch.setattr(
        application.importlib.util,
        "find_spec",
        lambda _name: SimpleNamespace(origin=str(missing)),
    )

    with pytest.raises(
        application.ConfigurationError,
        match=r"^modules_directory: builtin names a modules package that has no directory$",
    ):
        application.load_config(_builtin_config(tmp_path), environ=_environment())


def test_other_modules_directory_values_keep_their_relative_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R8: only the exact literal is reserved; ``./builtin`` is still a path
    relative to the configuration file, resolved without the import system."""

    def unexpected_find_spec(_name: str) -> Any:
        pytest.fail("a relative path must not consult the import system")

    monkeypatch.setattr(application.importlib.util, "find_spec", unexpected_find_spec)
    (tmp_path / "builtin").mkdir()
    config = _valid_config("./builtin", tmp_path / "unused.log")
    config_path = tmp_path / "config.yaml"
    _write_config(config_path, config)

    loaded = application.load_config(config_path)

    assert loaded["modules_directory"] == str((tmp_path / "builtin").resolve())


def _spy_entry_points(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[list[str], list[str]]:
    """Wrap every entry point the loader imports; record loads and activations.

    Returns ``(loaded, activated)``: the module names whose entry point was
    imported, and the names whose ``activate`` was called. A check must fill
    the first and leave the second empty (AC40).
    """

    loaded: list[str] = []
    activated: list[str] = []
    real_load = ModuleLoader._load_entry_point

    def load_with_spy(self: ModuleLoader, module: Any) -> Any:
        entry_point = real_load(self, module)
        loaded.append(module.name)
        real_activate = entry_point.activate

        async def spied_activate(*args: Any, **kwargs: Any) -> Any:
            activated.append(module.name)
            return await real_activate(*args, **kwargs)

        return replace(entry_point, activate=spied_activate)

    monkeypatch.setattr(ModuleLoader, "_load_entry_point", load_with_spy)
    return loaded, activated


@pytest.mark.asyncio
async def test_check_config_accepts_the_example_and_activates_no_module(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC40: the shipped example with dummy environment values is accepted —
    every enabled entry point imported and validated, 0 activated."""

    loaded, activated = _spy_entry_points(monkeypatch)
    diagnostics: list[str] = []

    status = await application.check_config(
        EXAMPLE_CONFIG,
        environ=_environment(),
        diagnostic_reporter=diagnostics.append,
    )

    assert status == 0
    assert diagnostics == []
    enabled = yaml.safe_load(EXAMPLE_CONFIG.read_text(encoding="utf-8"))["enabled_modules"]
    assert loaded == enabled
    assert activated == []


@pytest.mark.asyncio
async def test_check_config_accepts_the_example_through_the_builtin_literal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R8: the same check passes when the profile names the shipped package."""

    _loaded, activated = _spy_entry_points(monkeypatch)
    diagnostics: list[str] = []

    status = await application.check_config(
        _builtin_config(tmp_path),
        environ=_environment(),
        diagnostic_reporter=diagnostics.append,
    )

    assert (status, diagnostics, activated) == (0, [], [])


@pytest.mark.asyncio
async def test_check_config_names_the_unset_credential_path_and_no_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC40: with ``TWITCH_ACCESS_TOKEN`` unset the check exits 2 and the one
    diagnostic names the setting path — never a configured value."""

    _loaded, activated = _spy_entry_points(monkeypatch)
    environ = _environment()
    del environ["TWITCH_ACCESS_TOKEN"]
    diagnostics: list[str] = []

    status = await application.check_config(
        EXAMPLE_CONFIG,
        environ=environ,
        diagnostic_reporter=diagnostics.append,
    )

    assert status == 2
    assert diagnostics == [
        "modules.twitch.access_token: environment reference is unresolved"
    ]
    assert all(value not in diagnostics[0] for value in environ.values())
    assert activated == []


@pytest.mark.asyncio
async def test_check_config_reports_every_refusing_module_by_module_and_field(
    tmp_path: Path,
) -> None:
    """R7: the check runs each enabled module's own settings hook through the
    loader's pre-activation path — both refusals are reported, each naming
    module and field with the echoed credential redacted, and 0 modules are
    activated (AC24 applied to the check)."""

    modules = tmp_path / "modules"
    modules.mkdir()
    _make_phased_module(
        modules,
        "gateway",
        roles=("input",),
        source=ECHOING_FIELD_HOOK_SOURCE,
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
    diagnostics: list[str] = []

    status = await application.check_config(
        config_path, environ={}, diagnostic_reporter=diagnostics.append
    )

    assert status == 2
    assert diagnostics == [
        "module 'gateway': field 'api_key': rejected '<redacted>'",
        "module 'engine': field 'validate_settings': settings were refused by the module",
    ]
    assert all(
        value not in diagnostic for diagnostic in diagnostics for value in credentials
    )
    assert not lifecycle_log.exists()


@pytest.mark.asyncio
async def test_check_config_accepts_a_valid_profile_and_activates_nothing(
    tmp_path: Path,
) -> None:
    """R7: an accepted profile leaves the lifecycle log untouched — no hook
    past validation ran — and hands the accepted limits to the hook."""

    modules = tmp_path / "modules"
    modules.mkdir()
    _make_phased_module(
        modules,
        "gateway",
        roles=("input",),
        source=ACCEPTING_HOOK_SOURCE,
        settings_validator="validate_settings",
    )
    lifecycle_log = tmp_path / "lifecycle.log"
    config = _phased_config("./modules", lifecycle_log, ("gateway",))
    config["modules"]["gateway"]["api_key"] = "${GATEWAY_KEY}"
    config_path = tmp_path / "config.yaml"
    _write_config(config_path, config)
    diagnostics: list[str] = []

    status = await application.check_config(
        config_path,
        environ={"GATEWAY_KEY": _opaque("key")},
        diagnostic_reporter=diagnostics.append,
    )

    assert (status, diagnostics) == (0, [])
    assert not lifecycle_log.exists()


@pytest.mark.asyncio
async def test_check_config_reports_a_manifest_the_enabled_set_lacks(
    tmp_path: Path,
) -> None:
    """R7: the enabled set is validated against every discovered manifest."""

    (tmp_path / "modules").mkdir()
    config = _phased_config("./modules", tmp_path / "unused.log", ("absent",))
    config_path = tmp_path / "config.yaml"
    _write_config(config_path, config)
    diagnostics: list[str] = []

    status = await application.check_config(
        config_path, environ={}, diagnostic_reporter=diagnostics.append
    )

    assert status == 2
    assert diagnostics == ["module 'absent': field 'enabled_modules': has no manifest"]


@pytest.mark.asyncio
async def test_check_config_refuses_the_v1_input_startup_refuses(
    tmp_path: Path,
) -> None:
    """R4, R7: the check runs the loader's compatibility validation, so a v1
    manifest declaring the ``input`` role is refused here exactly as startup
    refuses it before activation — never accepted by the check alone."""

    modules = tmp_path / "modules"
    modules.mkdir()
    _make_module(modules, "legacy")
    manifest_path = modules / "legacy" / "module.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest["lifecycle"] = {"roles": ["input"]}
    manifest_path.write_text(yaml.safe_dump(manifest), encoding="utf-8")
    config = _phased_config("./modules", tmp_path / "unused.log", ("legacy",))
    config_path = tmp_path / "config.yaml"
    _write_config(config_path, config)
    diagnostics: list[str] = []

    status = await application.check_config(
        config_path, environ={}, diagnostic_reporter=diagnostics.append
    )

    assert status == 2
    assert len(diagnostics) == 1
    assert diagnostics[0].startswith("module 'legacy': field 'lifecycle.roles': ")
    assert "cannot honour the readiness barrier" in diagnostics[0]


def _example_environment(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Export the dummy environment the example references into the process."""

    environ = _environment()
    for name, value in environ.items():
        monkeypatch.setenv(name, value)
    return environ


def _forbid_the_watchdog(monkeypatch: pytest.MonkeyPatch) -> None:
    def unexpected(*_args: Any, **_kwargs: Any) -> Any:
        pytest.fail("--check-config must not install the process watchdog")

    monkeypatch.setattr(application, "ProcessWatchdog", unexpected)
    monkeypatch.setattr(application, "install_shutdown_watchdog", unexpected)
    monkeypatch.setattr(application, "arm_shutdown_watchdog", unexpected)


def test_main_check_config_returns_the_check_status_without_the_watchdog(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """AC40: ``main`` dispatches the flag to the check and returns its status
    — 0 with the environment exported, 2 without the access token, the
    diagnostic on stderr — and never installs the watchdog's process exit."""

    _forbid_the_watchdog(monkeypatch)
    _loaded, activated = _spy_entry_points(monkeypatch)
    _example_environment(monkeypatch)

    assert application.main(["--config", str(EXAMPLE_CONFIG), "--check-config"]) == 0
    assert capsys.readouterr().err == ""

    monkeypatch.delenv("TWITCH_ACCESS_TOKEN")

    assert application.main(["--check-config", "--config", str(EXAMPLE_CONFIG)]) == 2
    assert capsys.readouterr().err == (
        "error: modules.twitch.access_token: environment reference is unresolved\n"
    )
    assert activated == []


def test_main_check_config_does_not_run_the_application(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def unexpected_run(*_args: Any, **_kwargs: Any) -> int:
        pytest.fail("--check-config must not run the application")

    async def fake_check(config_path: str, **_kwargs: Any) -> int:
        checked.append(config_path)
        return 2

    checked: list[str] = []
    _forbid_the_watchdog(monkeypatch)
    monkeypatch.setattr(application, "run", unexpected_run)
    monkeypatch.setattr(application, "check_config", fake_check)

    assert application.main(["--config", "chosen.yaml", "--check-config"]) == 2
    assert checked == ["chosen.yaml"]


def test_main_check_config_sanitizes_unexpected_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    diagnostics: list[str] = []

    async def failing_check(_config_path: str, **_kwargs: Any) -> int:
        raise RuntimeError(_opaque("sensitive"))

    _forbid_the_watchdog(monkeypatch)
    monkeypatch.setattr(application, "check_config", failing_check)
    monkeypatch.setattr(
        application, "_default_diagnostic_reporter", diagnostics.append
    )

    assert application.main(["--config", "chosen.yaml", "--check-config"]) == 1
    assert diagnostics == ["configuration check: unexpected failure"]


def test_main_help_exits_zero_and_documents_the_check(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """R8 (AC43 groundwork): ``--help`` exits 0 and names both options."""

    with pytest.raises(SystemExit) as raised:
        application.main(["--help"])

    assert raised.value.code == 0
    usage = capsys.readouterr().out
    assert "--config PATH" in usage
    assert "--check-config" in usage


def _pyproject() -> dict[str, Any]:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_pyproject_ships_core_and_modules_with_every_manifest() -> None:
    """R8 (AC43): the distribution declares both package globs and the
    package-data glob that carries every shipped ``module.yaml`` (plus one
    per subpackage), so a clean install discovers the same manifests as the
    checkout; every shipped module directory is a package."""

    project = _pyproject()
    setuptools = project["tool"]["setuptools"]

    assert setuptools["packages"]["find"]["include"] == ["core*", "modules*"]
    package_data = setuptools["package-data"]
    assert package_data["modules"] == ["*/module.yaml"]

    shipped = sorted(
        child.name for child in (ROOT / "modules").iterdir()
        if (child / "module.yaml").is_file()
    )
    assert shipped
    for name in shipped:
        assert (ROOT / "modules" / name / "__init__.py").is_file(), name
        assert package_data[f"modules.{name}"] == ["module.yaml"], name
    assert (ROOT / "modules" / "__init__.py").is_file()


def test_pyproject_declares_the_console_script_and_the_build_backend_for_tests() -> None:
    """R8 (AC43): the console script points at ``core.main:main``; the
    runtime dependencies stay ``aiohttp`` and ``PyYAML``; the ``test`` extra
    carries the declared build backend (``setuptools>=69``) so the install
    test can build without isolation or network and a missing backend is an
    environment error, never a skip."""

    project = _pyproject()

    assert project["project"]["scripts"] == {
        "twitch-ia-compagnon": "core.main:main"
    }
    assert project["build-system"]["build-backend"] == "setuptools.build_meta"
    assert sorted(
        re.match(r"[A-Za-z0-9_.-]+", spec).group(0).lower()
        for spec in project["project"]["dependencies"]
    ) == ["aiohttp", "pyyaml"]
    assert "setuptools>=69" in project["project"]["optional-dependencies"]["test"]
    assert "setuptools>=69" in project["build-system"]["requires"]
