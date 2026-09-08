"""Command-line entry point and application lifecycle coordination."""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import signal
import sys
import threading
from collections.abc import Callable, Mapping, Sequence
from contextvars import ContextVar
from pathlib import Path
from typing import Any

import yaml

from .bus import EventBus
from .loader import ModuleActivation, ModuleLoadError, ModuleLoader


class ConfigurationError(RuntimeError):
    """A configuration defect safe to show without revealing its value."""


# Each hook gets a grace period; the CLI watchdog also covers cancellation-
# resistant tasks and asyncio.run() joining a blocked default-executor writer.
# On expiry the process exits with status 1; undrained records may be lost.
_SHUTDOWN_TIMEOUT_SECONDS = 120.0
_CLOSE_TIMEOUT_SECONDS = 60.0
_CANCEL_TIMEOUT_SECONDS = 0.1
_shutdown_watchdog: ContextVar[threading.Timer | None] = ContextVar(
    "shutdown_watchdog", default=None
)


def _arm_shutdown_watchdog() -> None:
    watchdog = _shutdown_watchdog.get()
    if watchdog is not None and not watchdog.is_alive():
        watchdog.start()


Reporter = Callable[[str], None]
_ENV_REFERENCE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}\Z")
_MODULE_REQUIRED_FIELDS: Mapping[str, tuple[str, ...]] = {
    "brain": ("endpoint", "model", "api_key"),
    "twitch": (
        "client_id",
        "client_secret",
        "access_token",
        "broadcaster_id",
        "bot_user_id",
    ),
}


def load_config(
    config_path: str | os.PathLike[str],
    *,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Load, expand, and validate one YAML configuration file.

    Relative module directories are resolved from the configuration file rather
    than from the process working directory. Errors contain setting paths, but
    never interpolate setting values or YAML parser excerpts.
    """

    try:
        path = Path(config_path).expanduser().resolve()
    except (OSError, RuntimeError, ValueError, TypeError):
        raise ConfigurationError("configuration file: path is not valid") from None
    try:
        with path.open("r", encoding="utf-8") as stream:
            loaded = yaml.safe_load(stream)
    except OSError:
        raise ConfigurationError("configuration file: is not readable") from None
    except yaml.YAMLError:
        raise ConfigurationError("configuration file: is not valid YAML") from None

    if not isinstance(loaded, Mapping):
        raise ConfigurationError("configuration: must be a mapping")

    expanded = _resolve_environment(
        loaded,
        environ=os.environ if environ is None else environ,
        path="configuration",
        active=set(),
    )
    config = dict(expanded)
    _validate_config(config)

    raw_modules_directory = config["modules_directory"]
    try:
        modules_directory = Path(raw_modules_directory).expanduser()
        if not modules_directory.is_absolute():
            modules_directory = path.parent / modules_directory
        config["modules_directory"] = str(modules_directory.resolve())
    except (OSError, RuntimeError, ValueError, TypeError):
        raise ConfigurationError(
            "modules_directory: must be a valid path"
        ) from None

    return config


async def run(
    config_path: str | os.PathLike[str],
    stop_event: asyncio.Event | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    ready_reporter: Reporter | None = None,
    diagnostic_reporter: Reporter | None = None,
) -> int:
    """Run the application until stopped and return a process-style status.

    ``stop_event`` is an injection seam for embedding and tests. When omitted,
    this coroutine owns SIGINT/SIGTERM handlers for the duration of startup,
    operation, and shutdown.
    """

    report_ready = ready_reporter or _default_ready_reporter
    report_diagnostic = diagnostic_reporter or _default_diagnostic_reporter

    try:
        config = load_config(config_path, environ=environ)
    except ConfigurationError as exc:
        report_diagnostic(str(exc))
        return 2
    except Exception:
        report_diagnostic("configuration file: could not be loaded")
        return 2

    owned_stop_event = stop_event is None
    stop = asyncio.Event() if stop_event is None else stop_event
    remove_signal_handlers: Callable[[], None] = lambda: None

    if owned_stop_event:
        try:
            remove_signal_handlers = _install_signal_handlers(stop)
        except Exception:
            report_diagnostic("signal handlers: could not be installed")
            return 1

    loader: ModuleLoader | None = None
    activations: list[ModuleActivation] = []
    try:
        try:
            bus = EventBus()
            loader = ModuleLoader(bus, config["modules_directory"])
            activations = await loader.activate_enabled(config)
        except Exception as exc:
            partial = (
                []
                if loader is None
                else list(getattr(loader, "activations", ()))
            )
            close_failures = await _close_activations(partial)
            report_diagnostic(_startup_diagnostic(exc))
            for failure in close_failures:
                report_diagnostic(failure)
            return 1

        try:
            report_ready("ready")
        except Exception:
            close_failures = await _close_activations(activations)
            report_diagnostic("readiness: could not be reported")
            for failure in close_failures:
                report_diagnostic(failure)
            return 1

        try:
            await stop.wait()
        except asyncio.CancelledError:
            # Cancellation is not the normal signal path, but embedded callers
            # still receive the same cleanup guarantee before it propagates.
            close_failures = await _close_activations(activations)
            for failure in close_failures:
                report_diagnostic(failure)
            raise

        close_failures = await _close_activations(activations)
        for failure in close_failures:
            report_diagnostic(failure)
        return 1 if close_failures else 0
    finally:
        remove_signal_handlers()


def main(argv: Sequence[str] | None = None) -> int:
    """Execute the application with a hard 120-second shutdown deadline.

    Module hooks have a 60-second grace period and 100 ms for cancellation.
    The global deadline starts at cleanup and includes asyncio's task/executor
    teardown. If a task or synchronous writer cannot stop, os._exit(1) bypasses
    interpreter thread joins and stream flushing; pending records may be lost.
    Embedded users of run() own their process/executor teardown policy.
    """

    parser = argparse.ArgumentParser(description="Run the Twitch AI companion")
    parser.add_argument(
        "--config",
        required=True,
        metavar="PATH",
        help="path to the YAML configuration file",
    )
    arguments = parser.parse_args(argv)
    watchdog = threading.Timer(_SHUTDOWN_TIMEOUT_SECONDS, os._exit, args=(1,))
    watchdog.daemon = True
    token = _shutdown_watchdog.set(watchdog)

    async def execute() -> int:
        try:
            return await run(arguments.config)
        finally:
            # Keep the deadline active through the runner's executor cleanup.
            _arm_shutdown_watchdog()

    try:
        return asyncio.run(execute())
    except KeyboardInterrupt:
        # This fallback is only reached on platforms where asyncio cannot own
        # signal handlers. The runner's cancellation cleanup has already run.
        return 0
    except Exception:
        _default_diagnostic_reporter("application: unexpected failure")
        return 1
    finally:
        watchdog.cancel()
        _shutdown_watchdog.reset(token)


def _resolve_environment(
    value: Any,
    *,
    environ: Mapping[str, str],
    path: str,
    active: set[int],
) -> Any:
    if isinstance(value, str):
        match = _ENV_REFERENCE.fullmatch(value)
        if match is not None:
            variable = match.group(1)
            if variable not in environ:
                raise ConfigurationError(
                    f"{_display_path(path)}: environment reference is unresolved"
                )
            resolved = environ[variable]
            if not isinstance(resolved, str):
                raise ConfigurationError(
                    f"{_display_path(path)}: environment reference must resolve "
                    "to a string"
                )
            return resolved
        if "${" in value:
            raise ConfigurationError(
                f"{_display_path(path)}: environment reference is invalid"
            )
        return value

    if isinstance(value, Mapping):
        identity = id(value)
        if identity in active:
            raise ConfigurationError(
                f"{_display_path(path)}: recursive value is invalid"
            )
        active.add(identity)
        try:
            return {
                key: _resolve_environment(
                    item,
                    environ=environ,
                    path=_mapping_path(path, key),
                    active=active,
                )
                for key, item in value.items()
            }
        finally:
            active.remove(identity)

    if isinstance(value, list):
        identity = id(value)
        if identity in active:
            raise ConfigurationError(
                f"{_display_path(path)}: recursive value is invalid"
            )
        active.add(identity)
        try:
            return [
                _resolve_environment(
                    item,
                    environ=environ,
                    path=f"{path}[{index}]",
                    active=active,
                )
                for index, item in enumerate(value)
            ]
        finally:
            active.remove(identity)

    return value


def _validate_config(config: Mapping[str, Any]) -> None:
    modules_directory = config.get("modules_directory")
    if not isinstance(modules_directory, str) or not modules_directory.strip():
        raise ConfigurationError(
            "modules_directory: must be a non-empty path string"
        )

    enabled = config.get("enabled_modules")
    if not isinstance(enabled, list):
        raise ConfigurationError("enabled_modules: must be a list")

    seen: set[str] = set()
    for index, name in enumerate(enabled):
        if not isinstance(name, str) or not name.strip():
            raise ConfigurationError(
                f"enabled_modules[{index}]: must be a non-empty string"
            )
        if name in seen:
            raise ConfigurationError(
                f"enabled_modules[{index}]: module names must be unique"
            )
        seen.add(name)

    modules = config.get("modules")
    if not isinstance(modules, Mapping):
        raise ConfigurationError("modules: must be a mapping")

    for name, settings in modules.items():
        if not isinstance(name, str) or not name.strip():
            raise ConfigurationError("modules: keys must be non-empty strings")
        if not isinstance(settings, Mapping):
            raise ConfigurationError(f"modules.{name}: must be a mapping")

    for name in enabled:
        if name not in modules:
            raise ConfigurationError(f"modules.{name}: settings are required")
        settings = modules[name]

        for field_name in _MODULE_REQUIRED_FIELDS.get(name, ()):
            value = settings.get(field_name)
            if not isinstance(value, str) or not value.strip():
                raise ConfigurationError(
                    f"modules.{name}.{field_name}: must be a non-empty string"
                )


async def _close_activations(
    activations: Sequence[ModuleActivation],
) -> list[str]:
    _arm_shutdown_watchdog()
    failures: list[str] = []
    # Twitch stops reception and drains accepted publications while its send
    # transport is still usable. Brain then releases its resources; audit last.
    ordered = sorted(
        reversed(activations),
        key=lambda activation: {"twitch": 0, "audit": 2}.get(activation.name, 1),
    )
    for activation in ordered:
        task = asyncio.create_task(activation.close())
        try:
            done, _ = await asyncio.wait({task}, timeout=_CLOSE_TIMEOUT_SECONDS)
            if not done:
                task.cancel()
                await asyncio.wait({task}, timeout=_CANCEL_TIMEOUT_SECONDS)
                # Retrieve even late exceptions without waiting indefinitely.
                task.add_done_callback(_consume_close_result)
                raise TimeoutError
            if task.cancelled():
                raise RuntimeError("close was cancelled")
            task.result()
        except asyncio.CancelledError:
            task.cancel()
            task.add_done_callback(_consume_close_result)
            raise
        except Exception:
            name = getattr(activation, "name", "<unknown>")
            if not isinstance(name, str) or not name:
                name = "<unknown>"
            failures.append(f"module {name!r}: shutdown failed")
    return failures


def _consume_close_result(task: asyncio.Task[Any]) -> None:
    if not task.cancelled():
        task.exception()


def _startup_diagnostic(exc: Exception) -> str:
    if isinstance(exc, ModuleLoadError):
        return str(exc)
    return "module activation: startup failed"


def _install_signal_handlers(stop_event: asyncio.Event) -> Callable[[], None]:
    loop = asyncio.get_running_loop()
    loop_registered: list[signal.Signals] = []
    fallback_registered: dict[signal.Signals, Any] = {}

    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(signum, stop_event.set)
                loop_registered.append(signum)
            except (NotImplementedError, RuntimeError):
                previous = signal.getsignal(signum)
                if previous is None:
                    raise RuntimeError(
                        "signal handler cannot be restored safely"
                    )

                def handle_signal(
                    _signum: int,
                    _frame: Any,
                    *,
                    event: asyncio.Event = stop_event,
                    event_loop: asyncio.AbstractEventLoop = loop,
                ) -> None:
                    event_loop.call_soon_threadsafe(event.set)

                signal.signal(signum, handle_signal)
                fallback_registered[signum] = previous
    except Exception:
        _remove_signal_handlers(loop, loop_registered, fallback_registered)
        raise

    def remove() -> None:
        _remove_signal_handlers(loop, loop_registered, fallback_registered)

    return remove


def _remove_signal_handlers(
    loop: asyncio.AbstractEventLoop,
    loop_registered: Sequence[signal.Signals],
    fallback_registered: Mapping[signal.Signals, Any],
) -> None:
    for signum in loop_registered:
        loop.remove_signal_handler(signum)
    for signum, previous in fallback_registered.items():
        signal.signal(signum, previous)


def _mapping_path(parent: str, key: Any) -> str:
    if isinstance(key, str) and key:
        return f"{parent}.{key}"
    return f"{parent}.<key>"


def _display_path(path: str) -> str:
    prefix = "configuration."
    return path[len(prefix) :] if path.startswith(prefix) else path


def _default_ready_reporter(message: str) -> None:
    print(message, flush=True)


def _default_diagnostic_reporter(message: str) -> None:
    print(f"error: {message}", file=sys.stderr, flush=True)


__all__ = ["ConfigurationError", "load_config", "main", "run"]


if __name__ == "__main__":
    raise SystemExit(main())
