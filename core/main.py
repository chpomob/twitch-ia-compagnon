"""Command-line entry point: configuration, runtime assembly, phase lifecycle.

The entry point knows no module by name (R4). It loads one configuration
file, validates what the core owns — structure, environment references, paths,
declared versions and every retention or admission limit (R6, R7) — assembles
the versioned runtime the modules are activated with, and then drives the
:class:`~core.lifecycle.PhaseCoordinator` under one finite global startup
deadline and one finite global shutdown deadline. Business settings are the
modules' own business: each enabled module validates them through the hook its
manifest declares, all of them before any module opens a transport (R7).

Two activation contracts leave the loader, and the coordinator is told which
route each activation took so that one shutdown sequence, under one deadline,
covers both:

versioned (``manifest_version: 2``)
    The coordinator reads the activation's declared lifecycle roles and the
    hooks its handle exposes. Producers are started only after the readiness
    barrier and stopped first, accepted work is drained under the shutdown
    deadline, ordinary resources close, and declared observation services
    flush and close last.

compatibility (no ``manifest_version``)
    A v1 module has exactly one lifecycle hook, ``close()``, which stops its
    inputs, drains what it accepted and releases its resources all at once.
    The coordinator runs these closes as one step of its sequence, in the
    order the modules were activated, after the versioned producers have
    stopped and drained and before any versioned resource closes: a v1
    configuration lists its pipeline from source to sink, so the work a v1
    source accepted reaches every sink — v1 or versioned, including the
    observation services that flush last — before those close. That is the
    guarantee the previous, name-ordered entry point gave, now given without
    a name and extended to a mixed pipeline in both directions.

Diagnostics name settings, modules, fields and phases. They never interpolate
a configured value.
"""

from __future__ import annotations

import argparse
import asyncio
import math
import os
import random
import re
import signal
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .actions import ActionExecutor, ActionRegistry, AuthorizationPolicy
from .attachments import AttachmentStore
from .bus import EventBus
from .context import ChatContext
from .contracts import Counters
from .lifecycle import (
    DEFAULT_CANCEL_GRACE_SECONDS,
    DEFAULT_DRAIN_DEADLINE_SECONDS,
    DEFAULT_HOOK_TIMEOUT_SECONDS,
    DEFAULT_SHUTDOWN_DEADLINE_SECONDS,
    DEFAULT_STARTUP_DEADLINE_SECONDS,
    PhaseCoordinator,
    ProcessWatchdog,
    SupervisedTasks,
    arm_shutdown_watchdog,
    install_shutdown_watchdog,
    reset_shutdown_watchdog,
)
from .loader import (
    MANIFEST_VERSION_V2,
    ModuleActivation,
    ModuleLoadError,
    ModuleLoader,
)
from .runtime import RuntimeContext, Supervision
from .triggers import TriggerEngine, TriggerRegistry


class ConfigurationError(RuntimeError):
    """A configuration defect safe to show without revealing its value."""


# One finite global deadline for startup and one for shutdown (R4). Each phase
# hook is additionally bounded by a per-hook grace period that the global
# deadline caps, so local timeouts never sum past the budget. The process
# watchdog is the last resort behind the shutdown deadline: it also covers
# cancellation-resistant tasks and asyncio.run() joining a blocked
# default-executor writer. On expiry the process exits with status 1;
# undrained records may be lost.
_STARTUP_DEADLINE_SECONDS = DEFAULT_STARTUP_DEADLINE_SECONDS
_SHUTDOWN_DEADLINE_SECONDS = DEFAULT_SHUTDOWN_DEADLINE_SECONDS
_SHUTDOWN_TIMEOUT_SECONDS = DEFAULT_SHUTDOWN_DEADLINE_SECONDS
_CLOSE_TIMEOUT_SECONDS = DEFAULT_HOOK_TIMEOUT_SECONDS
_CANCEL_TIMEOUT_SECONDS = DEFAULT_CANCEL_GRACE_SECONDS
_DRAIN_DEADLINE_SECONDS = DEFAULT_DRAIN_DEADLINE_SECONDS

Reporter = Callable[[str], None]
_ENV_REFERENCE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}\Z")

# --------------------------------------------------------------------------- #
# Retention and admission limits (R6)
# --------------------------------------------------------------------------- #

LIMITS_KEY = "limits"
"""The configuration block holding every retention and admission limit.

The block is optional as a whole and all-or-nothing in content. Without it the
application runs the compatibility runtime: the bus at its own built-in
bounds, and no chat context, attachment store or trigger engine — a versioned
module that declares triggers is then refused by the loader with a diagnostic
naming it. With the block present, every limit below is required and must be
finite and positive, so an absent, negative, non-numeric or infinite limit
stops startup before any module is activated (R6, AC32).
"""

_COUNT = "count"
"""A positive integer: events, bytes, records, entries, objects, workers."""

_SECONDS = "seconds"
"""A finite, strictly positive number of seconds."""

_LIMITS: Mapping[str, Mapping[str, str]] = {
    "bus_history": {
        "max_events": _COUNT,
        "max_bytes": _COUNT,
        "max_age_seconds": _SECONDS,
    },
    "observation_queue": {
        "max_records": _COUNT,
        "max_bytes": _COUNT,
    },
    "dedup": {
        "max_entries": _COUNT,
        "ttl_seconds": _SECONDS,
    },
    "attachments": {
        "max_object_bytes": _COUNT,
        "max_objects": _COUNT,
        "max_total_bytes": _COUNT,
        "max_bytes_per_run": _COUNT,
        "ttl_seconds": _SECONDS,
    },
    "conversation_memory": {
        "max_sessions": _COUNT,
        "max_exchanges": _COUNT,
        "max_bytes": _COUNT,
        "max_age_seconds": _SECONDS,
    },
    "chat_context": {
        "max_messages": _COUNT,
        "max_bytes": _COUNT,
        "max_age_seconds": _SECONDS,
        "max_channels": _COUNT,
    },
    "admission": {
        "session_queue_capacity": _COUNT,
        "global_pending_capacity": _COUNT,
        "max_sessions": _COUNT,
        "workers": _COUNT,
        "wait_seconds": _SECONDS,
        "total_run_seconds": _SECONDS,
    },
}
"""Every limit the core validates at startup, by group, with its kind.

The core-owned components — the bus history, the trigger engine's dedup
window, the attachment store and the chat context — are built from their
groups here. The observation queue, the conversation memory and the admission
scheduler are owned by the modules that hold that state; their limits are
validated here so that a deployment never starts with one of them absent or
infinite, and handing them to their owners is part of those modules' move to
the versioned runtime.
"""


def load_config(
    config_path: str | os.PathLike[str],
    *,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Load, expand, and validate one YAML configuration file.

    Relative module directories are resolved from the configuration file rather
    than from the process working directory. Errors contain setting paths, but
    never interpolate setting values or YAML parser excerpts.

    ``${NAME}`` references are resolved everywhere except inside the settings
    of a module that is not enabled: a disabled module's secrets are never
    looked up, so an unresolvable reference there does not stop an
    application that does not run that module (R7).
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

    resolver = os.environ if environ is None else environ
    config = {
        key: (
            value
            if key == "modules"
            else _resolve_environment(
                value,
                environ=resolver,
                path=_mapping_path("configuration", key),
                active=set(),
            )
        )
        for key, value in loaded.items()
    }
    _validate_config(config)

    modules = dict(config["modules"])
    for name in config["enabled_modules"]:
        modules[name] = _resolve_environment(
            modules[name],
            environ=resolver,
            path=f"configuration.modules.{name}",
            active=set(),
        )
    config["modules"] = modules

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

    try:
        try:
            runtime = _assemble_runtime(config)
        except Exception:
            report_diagnostic("runtime: could not be assembled")
            return 1

        loader: ModuleLoader | None = None
        try:
            loader = ModuleLoader(runtime.bus, config["modules_directory"])
            # The context and the environment are handed over after
            # construction so the loader keeps its original two-argument
            # shape, which is also the shape an embedding substitutes.
            loader.context = runtime.context
            loader.environ = os.environ if environ is None else environ
            activations = await loader.activate_enabled(config)
        except (Exception, asyncio.CancelledError) as exc:
            # The loader owns the handles already returned by successful
            # activation hooks; the assignment above has not completed.
            partial = [] if loader is None else list(getattr(loader, "activations", ()))
            coordinator = _coordinator(partial, runtime, report_diagnostic)
            # Nothing was started, yet everything activated is unwound through
            # the ordinary sequence, both routes under one shutdown deadline.
            await coordinator.stop()
            if isinstance(exc, asyncio.CancelledError):
                raise
            report_diagnostic(_startup_diagnostic(exc))
            return 1

        coordinator = _coordinator(activations, runtime, report_diagnostic)
        # A failed or cancelled startup is unwound by the coordinator itself,
        # compatibility closes included, under the one shutdown deadline it
        # took when the failure was met; the diagnostics were reported as they
        # happened.
        startup = await coordinator.start()
        if startup.status != 0:
            return 1

        try:
            report_ready("ready")
        except Exception:
            await coordinator.stop()
            report_diagnostic("readiness: could not be reported")
            return 1

        try:
            await stop.wait()
        except asyncio.CancelledError:
            # Cancellation is not the normal signal path, but embedded callers
            # still receive the same cleanup guarantee before it propagates.
            await coordinator.stop()
            raise

        report = await coordinator.stop()
        return 1 if report.failures else 0
    finally:
        remove_signal_handlers()


def main(argv: Sequence[str] | None = None) -> int:
    """Execute the application behind a hard process shutdown deadline.

    Phase hooks have a bounded grace period and a short cancellation window,
    all capped by the global shutdown deadline. The process watchdog starts at
    cleanup and includes asyncio's task/executor teardown. If a task or
    synchronous writer cannot stop, os._exit(1) bypasses interpreter thread
    joins and stream flushing; pending records may be lost. Embedded users of
    run() own their process/executor teardown policy.
    """

    parser = argparse.ArgumentParser(description="Run the chat companion")
    parser.add_argument(
        "--config",
        required=True,
        metavar="PATH",
        help="path to the YAML configuration file",
    )
    arguments = parser.parse_args(argv)
    watchdog = ProcessWatchdog(_SHUTDOWN_TIMEOUT_SECONDS)
    token = install_shutdown_watchdog(watchdog)

    async def execute() -> int:
        try:
            return await run(arguments.config)
        finally:
            # Keep the deadline active through the runner's executor cleanup.
            arm_shutdown_watchdog()

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
        reset_shutdown_watchdog(token)


# --------------------------------------------------------------------------- #
# Runtime assembly (R7)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class _Runtime:
    """The core-owned collaborators one run of the application is built on."""

    bus: EventBus
    context: RuntimeContext
    tasks: SupervisedTasks
    clock: Callable[[], float]


def _assemble_runtime(config: Mapping[str, Any]) -> _Runtime:
    """Build the bus and the versioned runtime context from validated limits.

    Every component validates its own bounds again at construction; the
    limits were validated by :func:`load_config` first so that a rejected
    limit is reported by its configuration path, before anything exists.
    """

    clock = time.monotonic
    rng = random.Random()
    counters = Counters()
    limits = config.get(LIMITS_KEY)

    if limits is None:
        bus = EventBus(clock=clock)
        triggers = chat = attachments = None
    else:
        bus_history = limits["bus_history"]
        bus = EventBus(
            history_max_events=bus_history["max_events"],
            history_max_bytes=bus_history["max_bytes"],
            history_max_age_seconds=bus_history["max_age_seconds"],
            clock=clock,
        )
        dedup = limits["dedup"]
        triggers = TriggerEngine(
            TriggerRegistry(),
            dedup_max_entries=dedup["max_entries"],
            dedup_ttl_seconds=dedup["ttl_seconds"],
            clock=clock,
            rng=rng,
            counters=counters,
        )
        chat = ChatContext(clock=clock, **limits["chat_context"])
        attachments = AttachmentStore(clock=clock, **limits["attachments"])

    tasks = SupervisedTasks(clock=clock)
    supervision = Supervision(bus, counters=counters)
    # No rule is configured here, and a policy that says nothing refuses
    # everything: declaring an action never authorizes it (R5, R7).
    authorization = AuthorizationPolicy()
    registry = ActionRegistry(authorization=authorization)
    executor = ActionExecutor(
        registry,
        authorization,
        supervision=supervision,
        counters=counters,
        clock=clock,
    )
    context = RuntimeContext(
        bus=bus,
        actions=registry,
        supervision=supervision,
        tasks=tasks,
        executor=executor,
        triggers=triggers,
        chat=chat,
        attachments=attachments,
        clock=clock,
        rng=rng,
    )
    return _Runtime(bus=bus, context=context, tasks=tasks, clock=clock)


# --------------------------------------------------------------------------- #
# Lifecycle (R4)
# --------------------------------------------------------------------------- #


def _split_routes(
    activations: Sequence[ModuleActivation],
) -> tuple[list[ModuleActivation], list[ModuleActivation]]:
    """Separate the versioned activations from the compatibility ones.

    The split reads the declared manifest version carried by each activation
    and nothing else: a module with no declared version took the v1 route
    through the loader and keeps it here.
    """

    versioned: list[ModuleActivation] = []
    legacy: list[ModuleActivation] = []
    for activation in activations:
        declared = getattr(activation, "manifest_version", None)
        if isinstance(declared, int) and declared >= MANIFEST_VERSION_V2:
            versioned.append(activation)
        else:
            legacy.append(activation)
    return versioned, legacy


def _coordinator(
    activations: Sequence[ModuleActivation],
    runtime: _Runtime,
    reporter: Reporter,
) -> PhaseCoordinator:
    """The coordinator driving *activations*, each on the route it declared.

    The coordinator reports every diagnostic through *reporter* as it happens,
    so a caller only reads the status of the report it returns. One global
    startup deadline and one global shutdown deadline cover both routes.
    """

    versioned, legacy = _split_routes(activations)
    return PhaseCoordinator(
        versioned,
        compatibility=legacy,
        startup_deadline_seconds=_STARTUP_DEADLINE_SECONDS,
        shutdown_deadline_seconds=_SHUTDOWN_DEADLINE_SECONDS,
        hook_timeout_seconds=_CLOSE_TIMEOUT_SECONDS,
        drain_deadline_seconds=_DRAIN_DEADLINE_SECONDS,
        cancel_grace_seconds=_CANCEL_TIMEOUT_SECONDS,
        clock=runtime.clock,
        tasks=runtime.tasks,
        reporter=reporter,
    )


def _startup_diagnostic(exc: Exception) -> str:
    if isinstance(exc, ModuleLoadError):
        return str(exc)
    return "module activation: startup failed"


# --------------------------------------------------------------------------- #
# Configuration validation (R6, R7)
# --------------------------------------------------------------------------- #


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
    """Validate what the core owns: structure, paths and limits (R6, R7).

    A module's business settings are not inspected here. They belong to the
    module, which validates them through its declared hook before any module
    is activated.
    """

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

    _validate_limits(config)


def _validate_limits(config: Mapping[str, Any]) -> None:
    """Every configured limit is present, numeric, finite and positive (R6)."""

    if LIMITS_KEY not in config:
        return
    block = config[LIMITS_KEY]
    if not isinstance(block, Mapping):
        raise ConfigurationError(f"{LIMITS_KEY}: must be a mapping")
    for group in block:
        if group not in _LIMITS:
            raise ConfigurationError(
                f"{_mapping_path(LIMITS_KEY, group)}: is not a known limit group"
            )
    for group, fields in _LIMITS.items():
        label = f"{LIMITS_KEY}.{group}"
        if group not in block:
            raise ConfigurationError(f"{label}: is required")
        section = block[group]
        if not isinstance(section, Mapping):
            raise ConfigurationError(f"{label}: must be a mapping")
        for field_name in section:
            if field_name not in fields:
                raise ConfigurationError(
                    f"{_mapping_path(label, field_name)}: is not a known limit"
                )
        for field_name, kind in fields.items():
            _validate_limit(section, field_name, f"{label}.{field_name}", kind)


def _validate_limit(
    section: Mapping[str, Any], field_name: str, label: str, kind: str
) -> None:
    if field_name not in section or section[field_name] is None:
        raise ConfigurationError(f"{label}: is required")
    value = section[field_name]
    if kind == _COUNT:
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ConfigurationError(f"{label}: must be a positive integer")
        return
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0
    ):
        raise ConfigurationError(f"{label}: must be a finite positive number")


# --------------------------------------------------------------------------- #
# Signals and reporting
# --------------------------------------------------------------------------- #


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


__all__ = ["LIMITS_KEY", "ConfigurationError", "load_config", "main", "run"]


if __name__ == "__main__":
    raise SystemExit(main())
