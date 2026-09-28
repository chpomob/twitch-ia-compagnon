"""Command-line entry point: configuration, runtime assembly, phase lifecycle.

The entry point knows no module by name (R4). It loads one configuration
file, validates what the core owns — structure, environment references, paths,
declared versions and every retention or admission limit (R6, R7) — assembles
the versioned runtime the modules are activated with, and then drives the
:class:`~core.lifecycle.PhaseCoordinator` under one finite global startup
deadline and one finite global shutdown deadline. Business settings are the
modules' own business: each enabled module validates them through the hook its
manifest declares, all of them before any module opens a transport (R7).

Two things the core owns are handed over generically, by key and never by
module name. The accepted ``limits`` block reaches every enabled module as the
reserved ``limits`` setting, so the module that owns a group validates its own
copy against the one value the entry point accepted and a differing copy stops
startup before any activation (R6). Credentials are redacted from every trace
and every lost-trace diagnostic from the first publication on (R8), and the
entry point learns no business field name to do it: each module's manifest
declares which of its settings are credentials, and the loader hands their
accepted values — literal or resolved — to supervision before the first module
is activated. The optional ``secrets`` block complements that declaration with
whole-string references the entry point resolves elsewhere in the
configuration, such as a backend URL that may embed a credential.

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
import importlib.util
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
from types import MappingProxyType
from typing import Any

from .actions import (
    ACTION_NATURES,
    ANY_ACTION,
    ANY_PRINCIPAL,
    ActionExecutor,
    ActionRegistry,
    AuthorizationPolicy,
    AuthorizationRule,
)
from .attachments import AttachmentStore
from .bus import EventBus
from .context import ChatContext
from .contracts import Counters, Destination, WILDCARD
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
    _declaration,
    _entry_point,
)
from .overlay import (
    OverlayError,
    deep_merge,
    read_base,
    read_overlay,
    resolve_overlay_path,
)
from .runtime import RuntimeContext, ServiceRegistry, Supervision
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

MODULES_DIRECTORY_BUILTIN = "builtin"
"""The reserved ``modules_directory`` value naming the shipped modules (R8).

An installed distribution has no checkout for a relative path to point into:
the literal resolves to the directory of the importable ``modules`` package,
wherever the interpreter finds it, so a clean environment discovers the
shipped manifests from a configuration file kept anywhere. Any other value
keeps its meaning: a path, relative to the configuration file.
"""
_BUILTIN_MODULES_PACKAGE = "modules"

# --------------------------------------------------------------------------- #
# Retention and admission limits (R6)
# --------------------------------------------------------------------------- #

MODULES_KEY = "modules"
"""The configuration block holding every module's own business settings."""

LIMITS_KEY = "limits"
"""The configuration block holding every retention and admission limit.

The block is optional as a whole and all-or-nothing in content. Without it the
application runs the compatibility runtime: the bus at its own built-in
bounds, and no chat context, attachment store or trigger engine — a versioned
module that declares triggers is then refused by the loader with a diagnostic
naming it. With the block present, every limit below is required and must be
finite and positive, so an absent, negative, non-numeric or infinite limit
stops startup before any module is activated (R6, AC32).

The accepted block is also handed to every enabled module, as the reserved
setting of the same name inside ``modules.<name>``, so the module that owns a
group — the observation queue, the conversation memory, the admission
scheduler — checks its own copy against the one value accepted here, through
its declared settings hook and before any activation. A module setting named
``limits`` is therefore refused: that slot belongs to the accepted block.
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
infinite, and the whole accepted block is handed to every enabled module
under :data:`LIMITS_KEY` so the owner validates its copy against it: the
value a module runs on is never allowed to differ from the value accepted
here (R6).
"""

LIMIT_KIND_COUNT = _COUNT
"""The kind of a limit that is a positive integer."""

LIMIT_KIND_SECONDS = _SECONDS
"""The kind of a limit that is a finite, strictly positive number of seconds."""

LIMIT_DECLARATION: Mapping[str, Mapping[str, str]] = MappingProxyType(
    {group: MappingProxyType(fields) for group, fields in _LIMITS.items()}
)
"""A read-only view of :data:`_LIMITS`: group → field → limit kind.

The configuration UI renders the ``limits`` block from it. The views wrap the
very mappings :func:`_validate_limits` reads, so the two cannot drift.
"""

SECRETS_KEY = "secrets"
"""The configuration block naming extra references every trace is redacted of.

The credentials themselves are not opt-in: every setting an enabled module's
manifest declares as a credential is redacted, literal or resolved, whether
or not it is listed here — the loader collects those from the declaration
before any module is activated (R8, AC29). This optional block adds
whole-string ``${NAME}`` references — the only secret shape configuration
uses — that no module declares, such as a backend URL that may embed a
credential; each must be referenced by some setting. It is never resolved on
its own: a listed reference takes the value an enabled setting resolved it to,
so a credential of a disabled module is never looked up (R7, AC25).
``load_config`` replaces the block with exactly those values, an empty list
when the configuration names none.
"""


def load_config(
    config_path: str | os.PathLike[str],
    *,
    environ: Mapping[str, str] | None = None,
    overlay: str | os.PathLike[str] | None = None,
    overlay_document: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Load, expand, and validate one YAML configuration file and its overlay.

    The overlay is ``overlay`` when given, else the path derived from the
    base file's name (A1); an absent overlay file, or a base name with no
    derived overlay, leaves the base alone. ``overlay_document``, when given,
    is an in-memory draft used instead of any overlay file (D1). The overlay is
    deep-merged over the base (R2) before any ``${NAME}`` reference is
    resolved and before validation, so both documents are validated as one.
    An overlay that cannot be read, is not valid YAML or is not a mapping is a
    configuration error naming the overlay file, never quoting its content.
    Neither file is ever written.

    Relative module directories are resolved from the configuration file rather
    than from the process working directory; the reserved value
    :data:`MODULES_DIRECTORY_BUILTIN` resolves to the shipped modules package
    instead (R8). Errors contain setting paths, but never interpolate setting
    values or YAML parser excerpts.

    ``${NAME}`` references are resolved everywhere except inside the settings
    of a module that is not enabled: a disabled module's secrets are never
    looked up, so an unresolvable reference there does not stop an
    application that does not run that module (R7). The ``secrets`` block is
    not resolved either: it is replaced by the values the references it
    lists were resolved to elsewhere — an empty list when absent — which is
    what keeps a listed credential of a disabled module unresolved (R8). The
    credentials the enabled modules declare need no entry here: the loader
    redacts them from their accepted settings before activation.

    The returned settings of every enabled module carry the accepted
    ``limits`` block under :data:`LIMITS_KEY`, so a module that owns a limit
    group validates its copy against the accepted value (R6).
    """

    try:
        path = Path(config_path).expanduser().resolve()
    except (OSError, RuntimeError, ValueError, TypeError):
        raise ConfigurationError("configuration file: path is not valid") from None
    try:
        base = read_base(path)
        if overlay_document is None:
            overlay_document = read_overlay(
                resolve_overlay_path(Path(config_path).expanduser(), overlay)
            )
        elif not isinstance(overlay_document, Mapping):
            raise OverlayError("overlay: must be a mapping")
    except OverlayError as exc:
        raise ConfigurationError(str(exc)) from None
    # Relative paths still resolve from the base file's directory: the merge
    # changes the document, never ``path``.
    loaded = deep_merge(base, overlay_document)

    resolver = os.environ if environ is None else environ
    # Every resolution performed, by variable: the secrets block is served
    # from here rather than from the environment, so it can never cause a
    # lookup of its own.
    resolved: dict[str, str] = {}
    config = {
        key: (
            value
            if key in (MODULES_KEY, SECRETS_KEY)
            else _resolve_environment(
                value,
                environ=resolver,
                path=_mapping_path("configuration", key),
                active=set(),
                resolved=resolved,
            )
        )
        for key, value in loaded.items()
    }
    _validate_config(config)
    _validate_secrets(
        config,
        referenced=_referenced_variables(
            {key: value for key, value in loaded.items() if key != SECRETS_KEY}
        ),
    )

    modules = dict(config[MODULES_KEY])
    for name in config["enabled_modules"]:
        modules[name] = _resolve_environment(
            modules[name],
            environ=resolver,
            path=f"configuration.{MODULES_KEY}.{name}",
            active=set(),
            resolved=resolved,
        )
        if LIMITS_KEY in config:
            # The accepted block, as its own copy: an owner reads the group it
            # owns from it and cannot reshape what another owner is handed.
            modules[name] = {
                **modules[name],
                LIMITS_KEY: {
                    group: dict(section)
                    for group, section in config[LIMITS_KEY].items()
                },
            }
    config[MODULES_KEY] = modules

    config[SECRETS_KEY] = [
        resolved[variable]
        for variable in _listed_secrets(config.get(SECRETS_KEY) or ())
        if variable in resolved
    ]

    raw_modules_directory = config["modules_directory"]
    if raw_modules_directory == MODULES_DIRECTORY_BUILTIN:
        config["modules_directory"] = str(_builtin_modules_directory())
        return config
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


def _overlay_arguments(
    overlay: str | os.PathLike[str] | None,
    overlay_document: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """The overlay keywords actually given, for :func:`load_config`.

    An omitted argument is not forwarded, so a caller that gives none calls
    ``load_config(path, environ=...)`` exactly as before the overlay existed,
    and ``load_config`` derives the overlay path itself (A1).
    """

    arguments: dict[str, Any] = {}
    if overlay is not None:
        arguments["overlay"] = overlay
    if overlay_document is not None:
        arguments["overlay_document"] = overlay_document
    return arguments


def _builtin_modules_directory() -> Path:
    """The directory of the importable shipped modules package (R8).

    Resolved through the import system and never through the checkout, so
    the same configuration file discovers the same manifests from an
    installed distribution. A package the interpreter cannot import, or one
    with no directory of its own, is a configuration defect naming the
    field: the literal selects a package that is not there to select.
    """

    try:
        spec = importlib.util.find_spec(_BUILTIN_MODULES_PACKAGE)
    except (ImportError, ValueError):
        spec = None
    if spec is None:
        raise ConfigurationError(
            f"modules_directory: {MODULES_DIRECTORY_BUILTIN} names a modules "
            "package that is not importable"
        )
    origin = getattr(spec, "origin", None)
    directory = None if not isinstance(origin, str) or not origin else Path(origin).parent
    if directory is None or not directory.is_dir():
        raise ConfigurationError(
            f"modules_directory: {MODULES_DIRECTORY_BUILTIN} names a modules "
            "package that has no directory"
        )
    return directory.resolve()


async def run(
    config_path: str | os.PathLike[str],
    stop_event: asyncio.Event | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    ready_reporter: Reporter | None = None,
    diagnostic_reporter: Reporter | None = None,
    overlay: str | os.PathLike[str] | None = None,
) -> int:
    """Run the application until stopped and return a process-style status.

    ``stop_event`` is an injection seam for embedding and tests. When omitted,
    this coroutine owns SIGINT/SIGTERM handlers for the duration of startup,
    operation, and shutdown. ``overlay`` is the explicit overlay path; when
    omitted, the one derived from the base file's name is merged (R2).
    """

    report_ready = ready_reporter or _default_ready_reporter
    report_diagnostic = diagnostic_reporter or _default_diagnostic_reporter

    try:
        config = load_config(
            config_path, environ=environ, **_overlay_arguments(overlay)
        )
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
        # R4: one finite global startup deadline for the whole startup. It is
        # established here, before the first enabled module is validated or
        # activated, so a validator or an activation hook that never returns
        # cannot block startup indefinitely; loading and the coordinator's
        # phase startup then share the same absolute point instead of each
        # taking a fresh budget.
        startup_deadline_at = runtime.clock() + _STARTUP_DEADLINE_SECONDS
        try:
            loader = ModuleLoader(runtime.bus, config["modules_directory"])
            # The context and the environment are handed over after
            # construction so the loader keeps its original two-argument
            # shape, which is also the shape an embedding substitutes.
            loader.context = runtime.context
            loader.environ = os.environ if environ is None else environ
            # Both global deadlines are absolute points on the runtime's
            # clock, so the loader measures them on that clock too.
            loader.clock = runtime.clock
            # A handle an interrupted activation returns only after its
            # grace is closed by the loader, and that close can finish after
            # this coroutine has returned: the reporter outlives the call,
            # so it is the owner that still reports the close's failure.
            loader.late_reporter = report_diagnostic
            activations = await loader.activate_enabled(
                config, deadline_at=startup_deadline_at
            )
        except (Exception, asyncio.CancelledError) as exc:
            # The loader owns the handles already returned by successful
            # activation hooks; the assignment above has not completed.
            partial = [] if loader is None else list(getattr(loader, "activations", ()))
            coordinator = _coordinator(partial, runtime, report_diagnostic)
            # R4: one finite global shutdown deadline for the whole cleanup,
            # established here and shared by both of its owners — the
            # coordinator unwinding the snapshot and the loader closing the
            # handles that arrive after it — so neither takes a fresh budget
            # once the other has spent this one. The loader learns it before
            # the first late close can begin: a handle is handed over on a
            # later scheduling turn, and there is no await before this.
            cleanup_deadline_at = runtime.clock() + _SHUTDOWN_DEADLINE_SECONDS
            if loader is not None:
                loader.cleanup_deadline_at = cleanup_deadline_at
            # Nothing was started, yet everything activated is unwound through
            # the ordinary sequence, both routes under one shutdown deadline.
            await coordinator.stop(deadline_at=cleanup_deadline_at)
            # A handle an interrupted activation returned only after its
            # grace is not in the snapshot above: the loader owns its bounded
            # close. A close already under way is settled before this returns
            # — within what remains of the same deadline, never beyond it —
            # and reported here if the loader did not report it itself; one
            # that begins later reports through the reporter handed over
            # above, since nothing here is left to read it.
            for diagnostic in await _late_diagnostics(
                loader, deadline_at=cleanup_deadline_at
            ):
                report_diagnostic(diagnostic)
            if isinstance(exc, asyncio.CancelledError):
                # The cancellation still reaches the caller, but not before the
                # module it interrupted is named (AC15): the loader recorded
                # the diagnostic when its own await was cancelled.
                for diagnostic in getattr(loader, "cancellation_diagnostics", ()):
                    report_diagnostic(diagnostic)
                raise
            for diagnostic in _startup_diagnostics(exc):
                report_diagnostic(diagnostic)
            return 1

        coordinator = _coordinator(activations, runtime, report_diagnostic)
        # A failed or cancelled startup is unwound by the coordinator itself,
        # compatibility closes included, under the one shutdown deadline it
        # took when the failure was met; the diagnostics were reported as they
        # happened. Phase startup spends what remains of the same global
        # startup deadline loading ran under (R4).
        startup = await coordinator.start(deadline_at=startup_deadline_at)
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


async def check_config(
    config_path: str | os.PathLike[str],
    *,
    environ: Mapping[str, str] | None = None,
    diagnostic_reporter: Reporter | None = None,
    overlay: str | os.PathLike[str] | None = None,
    overlay_document: Mapping[str, Any] | None = None,
) -> int:
    """Validate a profile without opening a transport; return its status (R7).

    The profile is the base file merged with its overlay exactly as
    :func:`run` loads it (R2); ``overlay_document`` checks an in-memory draft
    in place of the overlay file (D1).

    Everything :func:`run` checks before it activates the first module is
    checked here, in the same order and through the same code: the
    configuration file, every discovered manifest — disabled ones included —
    the enabled set, the ``${NAME}`` references of the enabled modules, the
    credentials the manifests declare (handed to redaction, so the runtime
    contract holds) and each enabled module's own settings hook, given the
    accepted ``limits`` block, under the same global startup deadline. The
    runtime is assembled because the loader validates against it; assembly
    constructs collaborators and opens nothing, and no entry point's
    ``activate`` is called, so 0 modules are activated and 0 sockets are
    opened (AC40).

    Returns 0 when the profile is accepted, 2 otherwise — with one diagnostic
    per failure, each naming the module and the field and never a configured
    value, reported through *diagnostic_reporter* as it is found.
    """

    report_diagnostic = diagnostic_reporter or _default_diagnostic_reporter

    try:
        config = load_config(
            config_path,
            environ=environ,
            **_overlay_arguments(overlay, overlay_document),
        )
    except ConfigurationError as exc:
        report_diagnostic(str(exc))
        return 2
    except Exception:
        report_diagnostic("configuration file: could not be loaded")
        return 2

    try:
        runtime = _assemble_runtime(config)
    except Exception:
        report_diagnostic("runtime: could not be assembled")
        return 2

    loader = ModuleLoader(runtime.bus, config["modules_directory"])
    loader.context = runtime.context
    loader.environ = os.environ if environ is None else environ
    loader.clock = runtime.clock
    try:
        refusals = await _validate_enabled(
            loader,
            config,
            deadline_at=runtime.clock() + _STARTUP_DEADLINE_SECONDS,
        )
    except ModuleLoadError as exc:
        for diagnostic in exc.diagnostics:
            report_diagnostic(diagnostic)
        return 2
    except Exception:
        report_diagnostic("configuration check: could not be completed")
        return 2
    for diagnostic in refusals:
        report_diagnostic(diagnostic)
    return 2 if refusals else 0


async def _validate_enabled(
    loader: ModuleLoader,
    config: Mapping[str, Any],
    *,
    deadline_at: float,
) -> list[str]:
    """The loader's pre-activation path, stopped short of the first activation.

    The steps are the ones :meth:`~core.loader.ModuleLoader.activate_enabled`
    takes before it activates anything, called in its order on its own
    methods so the check cannot drift from what startup enforces: discovery
    validates every manifest, the enabled set is checked against it, every
    enabled entry point is imported, the declarations are checked for what
    no activation shape could honour (a v1 input, a v2 module without a
    runtime context), secrets are resolved for the enabled
    modules only, every settings hook runs and every refusal is collected
    before any is reported, the declared credentials reach redaction, and
    the declarations and the configured trigger policies are registered
    against the runtime (R7, AC24, AC25). What one of those steps raises is
    the loader's own :class:`~core.loader.ModuleLoadError`; the hooks'
    refusals are returned, one diagnostic each.
    """

    discovered = loader._discover()
    enabled, settings = loader._validate_config(config, discovered)
    entry_points = {
        name: _entry_point(loader._load_entry_point(discovered[name]))
        for name in enabled
    }
    declarations = {name: _declaration(discovered[name]) for name in enabled}
    loader._validate_compatibility(enabled, declarations)
    # The one global startup deadline every awaited hook is bounded by: the
    # attribute activate_enabled sets from its own argument (R4).
    loader._deadline_at = deadline_at
    resolved = {name: loader._resolve_secrets(name, settings[name]) for name in enabled}

    refusals: list[str] = []
    for name in enabled:
        refusals.extend(
            await loader._validate_settings(
                name, declarations[name], entry_points[name], resolved[name]
            )
        )
    if refusals:
        return refusals

    loader._redact_credentials(enabled, declarations, resolved)
    for name in enabled:
        loader._register_declarations(name, declarations[name], resolved[name])
    loader._apply_trigger_configuration(config)
    return []


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
    parser.add_argument(
        "--check-config",
        action="store_true",
        help=(
            "validate the configuration, the module manifests and each "
            "enabled module's settings without opening any transport, then "
            "exit 0 (accepted) or 2 (diagnostics)"
        ),
    )
    parser.add_argument(
        "--overlay",
        metavar="PATH",
        help=(
            "path to the overlay file merged over the configuration file "
            "(default: derived from the configuration file's name)"
        ),
    )
    arguments = parser.parse_args(argv)
    # Only an explicit overlay is forwarded: without one, run and the check
    # derive the overlay path from the configuration file themselves (A1).
    overlay_argument = _overlay_arguments(arguments.overlay)

    if arguments.check_config:
        # Nothing is activated, so there is no cleanup for the watchdog to
        # bound: the check returns its status and the process exits by it.
        try:
            return asyncio.run(check_config(arguments.config, **overlay_argument))
        except KeyboardInterrupt:
            return 1
        except Exception:
            _default_diagnostic_reporter("configuration check: unexpected failure")
            return 1

    watchdog = ProcessWatchdog(_SHUTDOWN_TIMEOUT_SECONDS)
    token = install_shutdown_watchdog(watchdog)

    async def execute() -> int:
        try:
            return await run(arguments.config, **overlay_argument)
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
    # The extra references the configuration names, redacted from every
    # trace and lost-trace diagnostic from the first publication on: they
    # are known here, before the first module is activated. The credentials
    # the modules declare join them through the loader, still before the
    # first activation (R8, AC29).
    supervision = Supervision(
        bus, counters=counters, secrets=tuple(config.get(SECRETS_KEY) or ())
    )
    # The only rules are the ones the actions block grants explicitly, and a
    # policy without an applicable rule refuses the call: declaring an action
    # never authorizes it (R5, R7).
    authorization = AuthorizationPolicy(_authorization_rules(config))
    registry = ActionRegistry(authorization=authorization)
    # The executor checks every ``image_ref`` part against the same store the
    # modules lease from, so a provider naming an attachment of another run,
    # expired or of the wrong size is refused before the model sees it (R4).
    # ``max_observation_bytes`` stays unset here: the observation-size rule
    # is enforced per run by the module that owns the run budget, through
    # its own ``budget.max_observation_bytes`` setting; the executor's bound
    # is a runtime-wide guard an embedder may set — one rule, two enforcement
    # points, the same ``observation_too_large`` code.
    executor = ActionExecutor(
        registry,
        authorization,
        supervision=supervision,
        counters=counters,
        clock=clock,
        attachments=attachments,
        max_observation_bytes=None,
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
        services=ServiceRegistry(),
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
        # Every transition the coordinator completes reaches the runtime's
        # health owner, which is where ``module.ready``, ``module.degraded``
        # and ``module.stopped`` come from (R8). Both routes report: a v1
        # close is that module's whole shutdown.
        observer=runtime.context.health.observe_phase,
    )


async def _late_diagnostics(
    loader: ModuleLoader | None, *, deadline_at: float
) -> list[str]:
    """Settle the late handle closes the loader owns; return what is unreported.

    *deadline_at* is the global shutdown deadline the coordinator has just
    spent part of: the settle is bounded by what remains of it (R4). A
    loader given the entry point's reporter has already reported the
    diagnostics of the closes it settled here, so this returns nothing for
    it; a substituted loader without that seam hands them over here instead.
    """

    settle = None if loader is None else getattr(loader, "settle_late_results", None)
    if not callable(settle):
        return []
    return list(await settle(deadline_at=deadline_at))


def _startup_diagnostics(exc: Exception) -> tuple[str, ...]:
    """Every diagnostic a failed startup carries, one report line each.

    The loader validates every enabled module's settings before it raises,
    so its error may name several refusals — one per module and field —
    and each is reported on its own (R7, AC24).
    """

    if isinstance(exc, ModuleLoadError):
        return exc.diagnostics
    return ("module activation: startup failed",)


# --------------------------------------------------------------------------- #
# Explicit authorization rules (R5)
# --------------------------------------------------------------------------- #

ACTIONS_KEY = "actions"
"""The configuration block holding every explicit authorization grant.

The block is optional as a whole; without it the policy holds no rule and
refuses every call, read nature included, which is default-deny as shipped
(R5). Every field below a rule is validated here, value-free, before any
runtime exists; :func:`_authorization_rules` then turns the validated block
into the rules the executor re-evaluates on every call.
"""

_RULE_KEYS: frozenset[str] = frozenset(
    {
        "rule_id",
        "action_name",
        "destination",
        "principals",
        "natures",
        "granted_permissions",
    }
)
_DESTINATION_KEYS: frozenset[str] = frozenset({"platform", "channel_id", "scope"})


def _validate_actions(config: Mapping[str, Any]) -> None:
    """Every configured grant is well formed, before anything exists (R5)."""

    if ACTIONS_KEY not in config:
        return
    declared = config[ACTIONS_KEY]
    if not isinstance(declared, list):
        raise ConfigurationError(f"{ACTIONS_KEY}: must be a list")
    seen: set[str] = set()
    for index, entry in enumerate(declared):
        label = f"{ACTIONS_KEY}[{index}]"
        if not isinstance(entry, Mapping):
            raise ConfigurationError(f"{label}: must be a mapping")
        unknown = sorted(set(entry) - _RULE_KEYS)
        if unknown:
            raise ConfigurationError(
                f"{label}: is not a known authorization rule key ({', '.join(unknown)})"
            )
        rule_id = entry.get("rule_id")
        if not _is_text(rule_id):
            raise ConfigurationError(f"{label}.rule_id: must be a non-empty string")
        if rule_id in seen:
            raise ConfigurationError(f"{label}.rule_id: must be unique")
        seen.add(rule_id)
        for field_name in ("action_name",):
            value = entry.get(field_name)
            if value is not None and not _is_text(value):
                raise ConfigurationError(
                    f"{label}.{field_name}: must be a non-empty string"
                )
        destination = entry.get("destination")
        if destination is not None:
            if not isinstance(destination, Mapping):
                raise ConfigurationError(f"{label}.destination: must be a mapping")
            unknown = sorted(set(destination) - _DESTINATION_KEYS)
            if unknown:
                raise ConfigurationError(
                    f"{label}.destination: is not a known destination key "
                    f"({', '.join(unknown)})"
                )
            for field_name in sorted(_DESTINATION_KEYS):
                value = destination.get(field_name)
                if value is not None and not _is_text(value):
                    raise ConfigurationError(
                        f"{label}.destination.{field_name}: must be a non-empty string"
                    )
        minimums = (("principals", 1), ("natures", 1), ("granted_permissions", 0))
        for field_name, minimum in minimums:
            value = entry.get(field_name)
            if value is None:
                continue
            if not isinstance(value, list) or len(value) < minimum or not all(
                _is_text(item) for item in value
            ):
                raise ConfigurationError(
                    f"{label}.{field_name}: must be a list of non-empty strings"
                )
        natures = entry.get("natures")
        if natures is not None:
            invalid = sorted(set(natures) - ACTION_NATURES)
            if invalid:
                allowed = ", ".join(sorted(ACTION_NATURES))
                raise ConfigurationError(
                    f"{label}.natures: must be one of {allowed} ({', '.join(invalid)})"
                )


def _authorization_rules(config: Mapping[str, Any]) -> list[AuthorizationRule]:
    """The explicit grants the ``actions`` block configures, or none (R5)."""

    rules: list[AuthorizationRule] = []
    for entry in config.get(ACTIONS_KEY) or ():
        destination = entry.get("destination") or {}
        rules.append(
            AuthorizationRule(
                rule_id=entry["rule_id"],
                action_name=entry.get("action_name", ANY_ACTION),
                destination=Destination(
                    platform=destination.get("platform", WILDCARD),
                    channel_id=destination.get("channel_id", WILDCARD),
                    scope=destination.get("scope", WILDCARD),
                ),
                principals=tuple(entry.get("principals") or (ANY_PRINCIPAL,)),
                natures=tuple(entry.get("natures") or sorted(ACTION_NATURES)),
                granted_permissions=tuple(entry.get("granted_permissions") or ()),
            )
        )
    return rules


def _is_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


# --------------------------------------------------------------------------- #
# Configuration validation (R6, R7)
# --------------------------------------------------------------------------- #


def _resolve_environment(
    value: Any,
    *,
    environ: Mapping[str, str],
    path: str,
    active: set[int],
    resolved: dict[str, str] | None = None,
) -> Any:
    """Resolve every ``${NAME}`` inside *value*, recording each in *resolved*."""

    if isinstance(value, str):
        match = _ENV_REFERENCE.fullmatch(value)
        if match is not None:
            variable = match.group(1)
            if variable not in environ:
                raise ConfigurationError(
                    f"{_display_path(path)}: environment reference is unresolved"
                )
            looked_up = environ[variable]
            if not isinstance(looked_up, str):
                raise ConfigurationError(
                    f"{_display_path(path)}: environment reference must resolve "
                    "to a string"
                )
            if resolved is not None:
                resolved[variable] = looked_up
            return looked_up
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
            # Keys resolve like values, so a channel selected by an
            # environment reference names the channel it means (R1).
            mapping: dict[Any, Any] = {}
            for key, item in value.items():
                if isinstance(key, str) and "${" in key:
                    key = _resolve_environment(
                        key,
                        environ=environ,
                        path=_mapping_path(path, key),
                        active=active,
                        resolved=resolved,
                    )
                mapping[key] = _resolve_environment(
                    item,
                    environ=environ,
                    path=_mapping_path(path, key),
                    active=active,
                    resolved=resolved,
                )
            return mapping
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
                    resolved=resolved,
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

    modules = config.get(MODULES_KEY)
    if not isinstance(modules, Mapping):
        raise ConfigurationError(f"{MODULES_KEY}: must be a mapping")

    for name, settings in modules.items():
        if not isinstance(name, str) or not name.strip():
            raise ConfigurationError(f"{MODULES_KEY}: keys must be non-empty strings")
        if not isinstance(settings, Mapping):
            raise ConfigurationError(f"{MODULES_KEY}.{name}: must be a mapping")
        if LIMITS_KEY in settings:
            # The slot the accepted block is handed over in: a module's own
            # value there would be a second, unvalidated copy.
            raise ConfigurationError(
                f"{MODULES_KEY}.{name}.{LIMITS_KEY}: is reserved for the accepted "
                f"{LIMITS_KEY} block"
            )

    for name in enabled:
        if name not in modules:
            raise ConfigurationError(f"{MODULES_KEY}.{name}: settings are required")

    _validate_limits(config)
    _validate_actions(config)


def _validate_secrets(config: Mapping[str, Any], *, referenced: set[str]) -> None:
    """Every listed secret is a reference some setting makes (R8).

    The list is validated by shape only — never resolved here: a listed
    reference that no enabled setting resolves stays unresolved, which is
    how a disabled module's credential is never looked up (AC25). A
    reference no setting makes at all is refused, so a misspelt name cannot
    silently leave a value unredacted. The block may be absent: the
    credentials proper are declared by the modules, not listed here.
    """

    if SECRETS_KEY not in config:
        return
    declared = config[SECRETS_KEY]
    if not isinstance(declared, list):
        raise ConfigurationError(f"{SECRETS_KEY}: must be a list")
    seen: set[str] = set()
    for index, entry in enumerate(declared):
        label = f"{SECRETS_KEY}[{index}]"
        match = _ENV_REFERENCE.fullmatch(entry) if isinstance(entry, str) else None
        if match is None:
            raise ConfigurationError(
                f"{label}: must be an environment reference of the form ${{NAME}}"
            )
        variable = match.group(1)
        if variable in seen:
            raise ConfigurationError(f"{label}: must be unique")
        seen.add(variable)
        if variable not in referenced:
            raise ConfigurationError(f"{label}: is referenced by no setting")


def _listed_secrets(declared: Sequence[str]) -> list[str]:
    """The variable names a validated secrets block lists, in order."""

    names: list[str] = []
    for entry in declared:
        match = _ENV_REFERENCE.fullmatch(entry)
        if match is not None:
            names.append(match.group(1))
    return names


def _referenced_variables(value: Any) -> set[str]:
    """Every ``${NAME}`` the raw configuration makes, disabled modules included.

    A pure walk: nothing is looked up, so a reference inside a disabled
    module's settings counts as made without its secret being resolved.
    """

    found: set[str] = set()
    pending: list[Any] = [value]
    seen: set[int] = set()
    while pending:
        item = pending.pop()
        if isinstance(item, str):
            match = _ENV_REFERENCE.fullmatch(item)
            if match is not None:
                found.add(match.group(1))
        elif isinstance(item, Mapping):
            if id(item) in seen:
                continue
            seen.add(id(item))
            pending.extend(item.keys())
            pending.extend(item.values())
        elif isinstance(item, (list, tuple)):
            if id(item) in seen:
                continue
            seen.add(id(item))
            pending.extend(item)
    return found


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


__all__ = [
    "LIMITS_KEY",
    "MODULES_DIRECTORY_BUILTIN",
    "SECRETS_KEY",
    "ConfigurationError",
    "check_config",
    "load_config",
    "main",
    "run",
]


if __name__ == "__main__":
    raise SystemExit(main())
