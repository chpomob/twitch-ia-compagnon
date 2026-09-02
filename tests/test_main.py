from __future__ import annotations

import asyncio
import json
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
async def test_valid_config_waits_for_stop_and_closes_in_reverse_order(
    tmp_path: Path,
) -> None:
    modules = tmp_path / "modules"
    modules.mkdir()
    for name in ("twitch", "brain", "audit"):
        _make_module(modules, name)

    lifecycle_log = tmp_path / "lifecycle.log"
    config_path = tmp_path / "config.yaml"
    _write_config(config_path, _valid_config("./modules", lifecycle_log))

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
    assert lifecycle_log.read_text(encoding="utf-8").splitlines() == [
        "activate:twitch",
        "activate:brain",
        "activate:audit",
    ]

    stop.set()
    assert await asyncio.wait_for(task, timeout=1) == 0
    assert diagnostics == []
    assert lifecycle_log.read_text(encoding="utf-8").splitlines() == [
        "activate:twitch",
        "activate:brain",
        "activate:audit",
        "close:audit",
        "close:brain",
        "close:twitch",
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("module_name", "field_name"),
    [
        ("brain", "endpoint"),
        ("brain", "model"),
        ("brain", "api_key"),
        ("twitch", "client_id"),
        ("twitch", "client_secret"),
        ("twitch", "access_token"),
        ("twitch", "broadcaster_id"),
        ("twitch", "bot_user_id"),
    ],
)
async def test_each_type_invalid_required_module_setting_is_rejected(
    tmp_path: Path,
    module_name: str,
    field_name: str,
) -> None:
    config = _valid_config("./modules", tmp_path / "unused.log")
    credentials = _configured_credentials(config)
    _set_nested(config, ("modules", module_name, field_name), None)
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
        f"modules.{module_name}.{field_name}: must be a non-empty string"
    ]
    assert all(value not in diagnostics[0] for value in credentials)


@pytest.mark.asyncio
async def test_missing_required_setting_fails_before_readiness_without_values(
    tmp_path: Path,
) -> None:
    modules = tmp_path / "modules"
    modules.mkdir()
    lifecycle_log = tmp_path / "unused.log"
    config = _valid_config(str(modules), lifecycle_log)
    configured_values = {
        config["modules"]["twitch"]["client_secret"],
        config["modules"]["twitch"]["access_token"],
        config["modules"]["brain"]["api_key"],
    }
    del config["modules"]["twitch"]["access_token"]
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
    assert "modules.twitch.access_token" in combined
    assert all(value not in combined for value in configured_values)


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
        "close:audit",
        "close:brain",
        "close:twitch",
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
