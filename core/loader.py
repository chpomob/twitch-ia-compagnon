"""Discover, validate, and activate filesystem-defined modules (R7, R5, R4).

A manifest is now **versioned**. ``manifest_version: 2`` is the target
contract; an *absent* ``manifest_version`` means v1 and keeps the original
activation shape through an explicit compatibility route (R7, R4). The two
routes are one ``if`` in :meth:`ModuleLoader.activate_enabled` and nothing
else:

===================  =======================================================
Manifest             Activation
===================  =======================================================
v1 (absent version)  ``activate(bus, settings, catalog)`` returning a handle
                     whose async ``close()`` is **required**. This is the
                     compatibility route: the transport a v1 module opens is
                     released by that ``close()`` and by nothing else, so a
                     v1 module routed through the v2 path would leak it.
v2                   ``activate(context, settings, catalog)`` returning a
                     handle exposing the phase hooks of
                     :mod:`core.lifecycle`. The context is the versioned
                     :class:`~core.runtime.RuntimeContext`, scoped to the
                     module.
===================  =======================================================

A ``manifest_version`` that is present but unknown is **refused**, never
guessed, and a v1 manifest may not declare any v2 key: a manifest belongs to
exactly one contract.

**Nothing is inferred from a module name.** The lifecycle roles a module plays
are read from ``lifecycle.roles`` in its manifest and carried on
:class:`ModuleActivation`, so :func:`core.lifecycle.module_roles` answers from
the declaration rather than from a name map (R4). This is also where R4's v1
producer refusal lives: a v1 module has no ``start_inputs`` phase, so it can
only open its source inside ``activate`` — ahead of the readiness barrier. A
v1 manifest declaring the ``input`` role is therefore refused, before anything
is activated, with a diagnostic naming the module.

**Declaring is not authorizing.** Declared ``actions`` are recorded in the
action registry's *discovered* view and declared ``triggers`` in the trigger
registry. Neither grants anything: an action with no applicable authorization
rule stays out of the *authorized* view and the executor refuses it, read
nature included (R7, R5). The same holds for ``produces``, ``consumes`` and
``middleware``, which keep their purely event-routing meaning.

**A module declares which of its settings are credentials** (R8, AC29). The
v2 manifest key ``credentials`` lists setting paths — each one a property its
``settings_schema`` declares as a string — and the loader hands the accepted
value of every such setting of every enabled module, literal or resolved from
``${NAME}``, to the runtime context's supervision before the first module is
activated, so no trace is ever published with one of them unredacted. The core
learns no business field name from this: it reads the declaration. A path the
schema does not declare is refused at discovery, so a misspelt declaration
cannot silently leave a credential unredacted.

**Enabled and disabled are not validated alike** (R7, AC25). Every discovered
manifest is validated, disabled ones included, so a broken manifest stops
startup wherever it sits. But a disabled module's secrets are *not* resolved
and its business settings are *not* required: settings-schema validation, the
declared settings-validation hook and ``${NAME}`` resolution all run for the
enabled set only, and all of them run before any module is activated, so no
module has produced a network effect when the first diagnostic is reported.
"""

from __future__ import annotations

import asyncio
import importlib.util
import inspect
import math
import os
import re
import sys
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType, ModuleType
from typing import Any
from uuid import uuid4

import yaml

from .contracts import (
    ActionSpec,
    ContractError,
    Destination,
    SchemaError,
    TriggerPolicy,
    TriggerRule,
    TriggerSpec,
    TriggerTypeDeclaration,
    validate_against_schema,
    validate_schema,
)
from .lifecycle import (
    DEFAULT_CANCEL_GRACE_SECONDS,
    DEFAULT_HOOK_TIMEOUT_SECONDS,
    HOOK_NAMES,
    MANIFEST_LIFECYCLE_KEY,
    MANIFEST_ROLES_KEY,
    ROLE_INPUT,
    ROLES,
    Clock,
    Sleeper,
    _cancellation_requested,
    _consume_result,
    _discard,
    _wait_bounded,
    request_cancellation,
)
from .runtime import SUPPORTED_RUNTIME_APIS, ServiceConflictError
from .triggers import COMPANION_NAME_SETTING


__all__: Sequence[str] = (
    "MANIFEST_VERSION_V1",
    "MANIFEST_VERSION_V2",
    "SUPPORTED_MANIFEST_VERSIONS",
    "DiscoveredModule",
    "ManifestDeclaration",
    "ModuleActivation",
    "ModuleLoadError",
    "ModuleLoader",
)


class ModuleLoadError(RuntimeError):
    """Raised when module discovery, validation, import, or activation fails.

    ``diagnostics`` carries one entry per refusal. Settings validation runs
    for every enabled module before the error is raised, so a configuration
    with two invalid modules names both — each by module and field — in one
    error, and the entry point reports each entry on its own line (R7, AC24).
    """

    def __init__(self, message: str, *, diagnostics: Sequence[str] | None = None) -> None:
        super().__init__(message)
        self.diagnostics: tuple[str, ...] = (
            tuple(diagnostics) if diagnostics else (message,)
        )


# --------------------------------------------------------------------------- #
# Manifest vocabulary (R7)
# --------------------------------------------------------------------------- #

MANIFEST_VERSION_V1 = 1
"""What an *absent* ``manifest_version`` means. Never written in a manifest."""

MANIFEST_VERSION_V2 = 2
"""The target manifest contract: a runtime context and declared phase hooks."""

SUPPORTED_MANIFEST_VERSIONS: tuple[int, ...] = (
    MANIFEST_VERSION_V1,
    MANIFEST_VERSION_V2,
)
"""Every manifest contract this loader can activate. Anything else is refused."""

MANIFEST_VERSION_KEY = "manifest_version"
MANIFEST_RUNTIME_API_KEY = "runtime_api"
MANIFEST_SETTINGS_SCHEMA_KEY = "settings_schema"
MANIFEST_SETTINGS_VALIDATOR_KEY = "settings_validator"
MANIFEST_ACTIONS_KEY = "actions"
MANIFEST_TRIGGERS_KEY = "triggers"
MANIFEST_CREDENTIALS_KEY = "credentials"
"""The settings a module declares as credentials, as dotted setting paths.

Each path names a ``settings_schema`` property of type ``string``. The accepted
value of that setting — literal or resolved — is redacted from every trace
before the module is activated (R8, AC29).
"""

_V2_ONLY_KEYS: tuple[str, ...] = (
    MANIFEST_RUNTIME_API_KEY,
    MANIFEST_SETTINGS_SCHEMA_KEY,
    MANIFEST_SETTINGS_VALIDATOR_KEY,
    MANIFEST_ACTIONS_KEY,
    MANIFEST_TRIGGERS_KEY,
    MANIFEST_CREDENTIALS_KEY,
)
"""Keys a v1 manifest may not declare: a manifest belongs to one contract."""

#: The phase hooks a v2 handle may expose, from :mod:`core.lifecycle`. Whatever
#: it exposes must be an async callable; what it omits is a no-op phase.
_V2_HOOK_NAMES: frozenset[str] = frozenset(HOOK_NAMES.values())

_ACTION_KEYS: frozenset[str] = frozenset(
    {
        "name",
        "version",
        "description",
        "argument_schema",
        "result_schema",
        "nature",
        "required_permissions",
        "supported_destinations",
        "timeout_seconds",
        "idempotency",
        "delivery",
        "model_proposable",
    }
)

_DESTINATION_KEYS: frozenset[str] = frozenset({"platform", "channel_id", "scope"})
_TRIGGER_KEYS: frozenset[str] = frozenset({"types", "combinations", "default_policy"})
_TRIGGER_TYPE_KEYS: frozenset[str] = frozenset({"name", "parameter_schema"})
_POLICY_KEYS: frozenset[str] = frozenset({"rules", "combination"})
_RULE_KEYS: frozenset[str] = frozenset({"type", "parameters"})

#: A whole-string ``${NAME}`` reference, the only secret shape configuration
#: uses. Identical to the one the entry point applies to the rest of the
#: configuration, so a module setting is resolved by exactly one rule.
_ENV_REFERENCE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}\Z")


@dataclass(frozen=True, slots=True)
class ManifestDeclaration:
    """Everything a validated manifest declares, parsed into contracts.

    The raw ``manifest`` mapping is kept as it was read, because it is what the
    immutable capability catalog publishes. The parsed fields beside it are
    what the runtime acts on, so a manifest is turned into contracts exactly
    once — at discovery, before any module is imported.
    """

    manifest: dict[str, Any]
    manifest_version: int
    runtime_api: int | None = None
    roles: frozenset[str] = frozenset()
    settings_schema: Mapping[str, Any] | None = None
    settings_validator: str | None = None
    actions: tuple[ActionSpec, ...] = ()
    triggers: TriggerSpec | None = None
    #: The declared credential settings, each as its path of mapping keys.
    credentials: tuple[tuple[str, ...], ...] = ()

    @property
    def is_v2(self) -> bool:
        return self.manifest_version >= MANIFEST_VERSION_V2


@dataclass(frozen=True, slots=True)
class DiscoveredModule:
    """A validated module manifest and its filesystem location."""

    name: str
    directory: Path
    manifest: dict[str, Any]
    declaration: ManifestDeclaration | None = None


@dataclass(slots=True)
class ModuleActivation:
    """An activated module with an idempotent asynchronous lifecycle.

    ``roles`` carries the lifecycle roles the manifest declared, so the phase
    coordinator asks the activation what it does rather than deriving it from
    ``name`` (R4). ``None`` means "not supplied by the loader", which is how a
    hand-built activation falls back to its manifest.
    """

    name: str
    manifest: dict[str, Any]
    handle: Any
    roles: frozenset[str] | None = None
    manifest_version: int = MANIFEST_VERSION_V1
    _closed: bool = field(default=False, init=False, repr=False)
    _close_lock: asyncio.Lock = field(
        default_factory=asyncio.Lock, init=False, repr=False
    )

    @property
    def closed(self) -> bool:
        return self._closed

    async def close(self) -> None:
        """Call the module's close hook no more than once.

        A v1 handle always has one: it is required at activation and it is the
        only thing that releases the transport. A v2 handle may legitimately
        have none — closing is a declared phase like any other and a module
        with no role in it supplies nothing — so the call is a no-op there
        instead of a failure.
        """

        async with self._close_lock:
            if self._closed:
                return
            self._closed = True
            hook = getattr(self.handle, "close", None)
            if hook is None and self.manifest_version >= MANIFEST_VERSION_V2:
                return
            try:
                result = hook() if hook is not None else self.handle.close()
                if not inspect.isawaitable(result):
                    raise TypeError("close must be asynchronous")
                await result
            except Exception:
                raise ModuleLoadError(
                    f"module {self.name!r}: field 'close': shutdown failed"
                ) from None


class ModuleLoader:
    """Load modules declared by immediate children of a directory."""

    def __init__(
        self,
        bus: Any,
        modules_directory: str | Path,
        *,
        context: Any = None,
        environ: Mapping[str, str] | None = None,
        clock: Clock | None = None,
        sleeper: Sleeper | None = None,
        cancel_grace_seconds: float = DEFAULT_CANCEL_GRACE_SECONDS,
        late_close_seconds: float = DEFAULT_HOOK_TIMEOUT_SECONDS,
    ) -> None:
        """Build a loader over *modules_directory*.

        *context* is the versioned :class:`~core.runtime.RuntimeContext` a v2
        activation receives; without one only v1 modules can be activated, and
        an enabled v2 module is refused rather than handed a bus it does not
        expect. *environ* is the mapping ``${NAME}`` module settings resolve
        against, injected so a test never touches the process environment.
        *clock* measures the startup budget :meth:`activate_enabled` is given
        per call and the cleanup deadline of :attr:`cleanup_deadline_at`;
        *sleeper* spends every bounded wait below, so a test drives each
        budget from an injected timeline. *cancel_grace_seconds* bounds how
        long a hung validator or activation hook is given to observe its
        cancellation — at that budget's expiry or when the caller itself is
        cancelled — and still return the handle it opened (R4, AC15).
        *late_close_seconds* bounds the close of a handle returned only after
        that grace, which this loader owns and closes itself since no caller
        can still register it; what that close reports goes to
        :attr:`late_reporter` when one is set, since the close may finish
        after every caller has returned. That budget, and the grace after it,
        are capped by what remains of :attr:`cleanup_deadline_at` once one is
        set (R4).
        """

        if context is not None and getattr(context, "bus", bus) is not bus:
            raise _field_error(
                "<runtime context>", "bus", "must be the bus given to the loader"
            )
        self.bus = bus
        self.context = context
        self.environ: Mapping[str, str] = os.environ if environ is None else environ
        self.modules_directory = Path(modules_directory).expanduser().resolve()
        self.catalog: Mapping[str, Mapping[str, Any]] = MappingProxyType({})
        self.discovered: dict[str, DiscoveredModule] = {}
        self.activations: list[ModuleActivation] = []
        self.resolved_secrets: int = 0
        #: How many declared credential values reached supervision's redaction
        #: on the last :meth:`activate_enabled`, literal and resolved alike.
        self.redacted_credentials: int = 0
        # The one global startup deadline loading runs under (R4): an absolute
        # point on *clock*, established by the entry point before the first
        # enabled module is validated or activated and handed to every await
        # below through :meth:`activate_enabled`. ``None`` — a standalone
        # loader with no budget — keeps the previous unbounded behaviour.
        self.clock: Clock = time.monotonic if clock is None else clock
        self._sleep: Sleeper = asyncio.sleep if sleeper is None else sleeper
        self._cancel_grace_seconds = cancel_grace_seconds
        self._deadline_at: float | None = None
        self._late_close_seconds = late_close_seconds
        #: The one global cleanup deadline, an absolute point on *clock*, that
        #: the entry point establishes when a startup fails and shares with
        #: the coordinator unwinding the partial activation (R4). Every late
        #: close beginning after it is set caps its budget and its grace by
        #: what remains, and :meth:`settle_late_results` gives up on what is
        #: still unfinished at that point: the handle is closed by this
        #: loader, never by a fresh budget the caller did not wait for.
        self.cleanup_deadline_at: float | None = None
        # Tasks given up on stay referenced here so the collector cannot
        # destroy a pending task nothing else holds.
        self._abandoned: list[asyncio.Task[Any]] = []
        # The bounded closes of handles returned after their activation was
        # given up on: each is owned here, since the caller that could have
        # registered the handle has already been answered (N2).
        self._late_closes: list[_LateClose] = []
        #: Diagnostics of the late closes above, each naming the module.
        self.late_diagnostics: list[str] = []
        #: Where a late close reports its diagnostic the moment it has one.
        #: A late close may finish after the caller has been answered and
        #: after :meth:`settle_late_results` has returned — the abandoned
        #: hook returns its handle whenever it does — so a reporter that
        #: outlives the call is the only owner that can still report it.
        #: Without one the diagnostic waits for the next
        #: :meth:`settle_late_results`.
        self.late_reporter: Callable[[str], None] | None = None
        self._unreported_late: list[str] = []
        #: Diagnostics for a cancellation that interrupted loading, naming the
        #: module and the step it interrupted (AC15). The entry point reports
        #: them before it lets the cancellation propagate.
        self.cancellation_diagnostics: list[str] = []

    @property
    def abandoned(self) -> tuple[asyncio.Task[Any], ...]:
        """The hooks and late closes given up on that have still not ended.

        Each is already reported; this loader holds them so their eventual
        end is observed by the loop's teardown, not the collector — cleanup
        ownership is retained past every deadline, never extended for a
        caller (R4).
        """

        self._abandoned = [task for task in self._abandoned if not task.done()]
        return tuple(self._abandoned)

    async def activate_enabled(
        self,
        config: Mapping[str, Any],
        *,
        deadline_at: float | None = None,
    ) -> list[ModuleActivation]:
        """Discover all modules, validate everything, then activate enabled ones.

        The order is the guarantee. Every manifest — disabled ones included —
        is validated at discovery; every enabled entry point is imported; every
        enabled module's secrets are resolved and its own settings validated
        through its declared hook; every declared action and trigger is
        recorded. Only then is the first module activated, so a diagnostic is
        always reported with 0 modules activated and 0 transports opened
        (R7, AC24, AC25).

        Enabled modules are activated in ``enabled_modules`` order and receive
        their settings mapping — the caller's own object when it holds no
        ``${NAME}`` reference to resolve.

        *deadline_at* is the absolute point on the loader's clock by which
        validation and activation must be done: the one global startup
        deadline, established before this call and shared with the phase
        startup that follows it, so a validator or an activation hook that
        never returns cannot block startup indefinitely (R4). Without it the
        awaits of this method are unbounded, which is the previous behaviour
        of a standalone loader.
        """

        discovered = self._discover()
        enabled, settings = self._validate_config(config, discovered)

        # Import every enabled entry point before activation. This keeps import
        # failures from leaving an avoidable partially activated application.
        entry_points = {
            name: _entry_point(self._load_entry_point(discovered[name]))
            for name in enabled
        }

        self.discovered = discovered
        self.catalog = MappingProxyType(
            {
                name: _freeze_mapping(module.manifest)
                for name, module in discovered.items()
            }
        )
        self.activations = []
        self.resolved_secrets = 0
        self.redacted_credentials = 0
        self._deadline_at = deadline_at
        self.cancellation_diagnostics = []

        declarations = {name: _declaration(discovered[name]) for name in enabled}
        self._validate_compatibility(enabled, declarations)

        # Secrets belong to the enabled set only (R7, AC25). A disabled module's
        # ``${NAME}`` reference is never looked up, so an unresolvable one does
        # not stop an application that does not run that module.
        resolved = {
            name: self._resolve_secrets(name, settings[name]) for name in enabled
        }

        # Every enabled module validates its own settings, all of them before
        # any of them is activated, and every refusal is collected before the
        # first is raised: two invalid modules are both reported, each naming
        # its module and field, with 0 transports opened (R7, AC24).
        refusals: list[str] = []
        for name in enabled:
            refusals.extend(
                await self._validate_settings(
                    name, declarations[name], entry_points[name], resolved[name]
                )
            )
        if refusals:
            raise ModuleLoadError("\n".join(refusals), diagnostics=refusals)

        # Every value a manifest declares as a credential — accepted just
        # above, literal or resolved — is redacted from every trace before
        # the first module can publish one (R8, AC29).
        self._redact_credentials(enabled, declarations, resolved)

        # Declaring records; it never grants (R7, R5).
        for name in enabled:
            self._register_declarations(name, declarations[name], resolved[name])

        # What configuration selects, applied after every input registered its
        # declaration and before any module is activated (R1, R7).
        self._apply_trigger_configuration(config)

        for name in enabled:
            declaration = declarations[name]
            activate = entry_points[name].activate
            handle = await self._activate(
                name, declaration, activate, resolved[name],
                discovered[name].manifest,
            )

            # Retain the handle as soon as activation succeeds. If its lifecycle
            # contract is invalid, callers can still attempt reverse-order cleanup
            # of the complete partially activated set.
            activation = ModuleActivation(
                name=name,
                manifest=discovered[name].manifest,
                handle=handle,
                roles=declaration.roles,
                manifest_version=declaration.manifest_version,
            )
            self.activations.append(activation)

            if declaration.is_v2:
                self._validate_v2_handle(name, declaration, handle)
            else:
                self._validate_v1_handle(name, handle)

        return list(self.activations)

    async def load(self, config: Mapping[str, Any]) -> list[ModuleActivation]:
        """Compatibility alias for :meth:`activate_enabled`."""

        return await self.activate_enabled(config)

    # -- the v1/v2 fork ---------------------------------------------------- #

    def _validate_compatibility(
        self,
        enabled: Sequence[str],
        declarations: Mapping[str, ManifestDeclaration],
    ) -> None:
        """Refuse, by declaration, what neither activation shape could honour.

        R4: a v1 module has no ``start_inputs`` phase, so a v1 producer could
        only open its source inside ``activate``, ahead of the barrier; it is
        refused before any module is activated. A v2 module needs the runtime
        context its activation is handed, so a loader built without one
        refuses it just as early. This runs on the startup path and on the
        configuration check alike, so the check cannot accept what startup
        rejects.
        """

        for name in enabled:
            declaration = declarations[name]
            if not declaration.is_v2 and ROLE_INPUT in declaration.roles:
                raise _field_error(
                    name,
                    f"{MANIFEST_LIFECYCLE_KEY}.{MANIFEST_ROLES_KEY}",
                    "a v1 module cannot honour the readiness barrier; declare "
                    f"{MANIFEST_VERSION_KEY} {MANIFEST_VERSION_V2} to produce inputs",
                )
            if declaration.is_v2 and self.context is None:
                raise _field_error(
                    name,
                    MANIFEST_VERSION_KEY,
                    f"{MANIFEST_VERSION_KEY} {MANIFEST_VERSION_V2} requires a runtime "
                    "context, and the loader was built without one",
                )

    async def _activate(
        self,
        name: str,
        declaration: ManifestDeclaration,
        activate: Any,
        settings: Mapping[str, Any],
        manifest: Mapping[str, Any],
    ) -> Any:
        """Call *activate* through exactly one of the two contracts.

        This ``if`` is the whole fork. It is written once, it reads from the
        declared manifest version and from nothing else, and both arms are
        covered by their own test: a v1 module routed through the v2 arm would
        never be asked for the ``close()`` that releases its transport.
        """

        if declaration.is_v2:
            first_argument: Any = self.context.for_module(name)
        else:
            # --- v1 COMPATIBILITY ROUTE ---------------------------------- #
            # The bus itself, positionally, exactly as before v2 existed. No
            # collaborator is smuggled onto it: the bus keeps the attributes
            # its class defines (AC26).
            first_argument = self.bus

        def activation_of(late: Any) -> ModuleActivation:
            return ModuleActivation(
                name=name,
                manifest=manifest,
                handle=late,
                roles=declaration.roles,
                manifest_version=declaration.manifest_version,
            )

        def preserve(late: Any) -> None:
            # An activation that observed its cancellation can still return
            # its handle within the grace; registering it keeps that handle
            # reachable by the startup cleanup that closes `self.activations`
            # in reverse order, before the caller has been answered.
            if late is not None:
                self.activations.append(activation_of(late))

        def release(late: Any) -> None:
            # A handle returned after the grace has no caller left to register
            # it with — the refusal or the cancellation is already on its way,
            # and the entry point snapshots `self.activations` when it lands —
            # so this loader is its cleanup owner and closes it, bounded (N2).
            if late is not None:
                self._close_late(activation_of(late))

        try:
            pending_handle = activate(first_argument, settings, self.catalog)
            if not inspect.isawaitable(pending_handle):
                raise TypeError("activate must be asynchronous")
            status, handle = await self._await_module(
                pending_handle,
                module=name,
                field_name="activate",
                operation="activation",
                preserve=preserve,
                release=release,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Everything the module raised — a ModuleLoadError of its own
            # included — becomes the loader's generic refusal: the module was
            # handed its settings, so its exception text may echo a
            # configured credential into the diagnostics. A duplicate service
            # publication adds the holding module's name, read back from the
            # registry rather than from the exception text (AC25).
            holder = self._verified_service_holder(name, exc)
            conflict = (
                ""
                if holder is None
                else f": a service it published is already published by "
                f"module {holder!r}"
            )
            raise ModuleLoadError(
                f"module {name!r}: field 'activate': activation failed{conflict}"
            ) from None
        if status == "deadline":
            raise _field_error(
                name, "activate", "exceeded the global startup deadline"
            )
        if status == "cancelled":
            # A handle that ended cancelled freed nothing it opened, and
            # the caller was not cancelled: this is a failed activation
            # the report names, not a cancellation to propagate (AC15).
            raise _field_error(name, "activate", "was cancelled")
        return handle

    async def _await_module(
        self,
        awaitable: Awaitable[Any],
        *,
        module: str,
        field_name: str,
        operation: str,
        preserve: Callable[[Any], None] | None = None,
        release: Callable[[Any], None] | None = None,
    ) -> tuple[str, Any]:
        """Await one module awaitable inside the global startup deadline (R4).

        Returns ``(status, result)``. ``"ok"`` when it returned;
        ``"deadline"`` when the remaining startup budget expired — the
        awaitable was cancelled, given a bounded grace window and left with
        its late outcome consumed, never awaited forever, or refused before
        being scheduled at all once the budget was already spent; ``"cancelled"``
        when the awaitable itself ended cancelled while this loader kept
        control. A cancellation of the *caller* propagates after a sanitised
        diagnostic naming the module it interrupted is recorded (AC15):
        ``cancellation_diagnostics`` is what the entry point reports before
        letting the cancellation reach its own caller — after the same
        bounded grace the deadline gives, so a hook that answers its
        cancellation by returning the handle it opened is not discarded.
        *preserve*, when given, receives a result the hook still returned
        within that grace, so the caller keeps what only cleanup can
        release; *release* receives one returned after it, once the caller
        has been answered and can register nothing more.
        """

        remaining = (
            math.inf
            if self._deadline_at is None
            else max(self._deadline_at - self.clock(), 0.0)
        )
        if self._deadline_at is not None and remaining <= 0.0:
            # The budget is spent before the hook is scheduled: it never runs,
            # so it cannot open resources past the deadline and then report
            # readiness. The never-scheduled awaitable is closed, not leaked.
            _discard(awaitable)
            return "deadline", None
        task = asyncio.ensure_future(awaitable)
        try:
            finished = await _wait_bounded({task}, remaining, self._sleep)
            if task not in finished:
                request_cancellation(task)
                await _wait_bounded({task}, self._cancel_grace_seconds, self._sleep)
                self._salvage(task, preserve)
                self._abandon(task, release)
                return "deadline", None
            if task.cancelled():
                return "cancelled", None
            return "ok", task.result()
        except asyncio.CancelledError:
            request_cancellation(task)
            try:
                # The hook is given the bounded grace to observe its
                # cancellation before what it returned is looked at: without
                # a scheduling turn here, a hook returning its handle from its
                # cancellation handler would never be seen to have done so.
                # A second cancellation landing inside the grace still leaves
                # through the clauses below.
                await _wait_bounded({task}, self._cancel_grace_seconds, self._sleep)
            finally:
                self._salvage(task, preserve)
                self._abandon(task, release)
                self.cancellation_diagnostics.append(
                    _field_diagnostic(module, field_name, f"{operation} was cancelled")
                )
            raise

    def _salvage(
        self,
        task: "asyncio.Task[Any]",
        preserve: Callable[[Any], None] | None,
    ) -> None:
        """Keep the result a cancelled hook still returned.

        A hook that observed its cancellation can complete during the grace
        window — an ``activate`` then returns the handle whose resources only
        cleanup can release — and a caller cancellation can land after the
        hook already completed. *preserve* receives such a result; the task's
        outcome is still consumed through :meth:`_abandon`.
        """

        if preserve is None or not task.done() or task.cancelled():
            return
        try:
            result = task.result()
        except Exception:
            return
        preserve(result)

    def _abandon(
        self,
        task: "asyncio.Task[Any]",
        release: Callable[[Any], None] | None = None,
    ) -> None:
        """Give up on *task*: its late outcome is consumed, never awaited.

        *release*, when given, is the explicit owner of a result the task
        still returns later: a handle that arrives once the grace has passed
        is handed to it, and to nothing else, so it is closed rather than
        appended to an activation list the caller has already read.
        """

        task.add_done_callback(_consume_result)
        if task.done():
            return
        if release is not None:
            task.add_done_callback(lambda done: _hand_result(done, release))
        self._abandoned.append(task)

    def _verified_service_holder(self, name: str, exc: BaseException) -> str | None:
        """Return the module holding the key *name* failed to publish, if real.

        Only a :class:`~core.runtime.ServiceConflictError` naming *name* as the
        refused publisher counts, and only when the registry itself records
        its key as held by another module this loader activated: a module
        cannot forge the clause, and the name reported is one the loader
        already knows, never text the module supplied.
        """

        if not isinstance(exc, ServiceConflictError) or exc.module != name:
            return None
        registry = getattr(self.context, "services", None)
        if registry is None:
            return None
        try:
            holder = registry.entries().get((exc.kind, exc.platform))
        except Exception:
            return None
        activated = {activation.name for activation in self.activations}
        if holder == exc.holder and holder != name and holder in activated:
            return holder
        return None

    def _close_late(self, activation: ModuleActivation) -> None:
        """Own the bounded close of a handle returned after the grace."""

        late = _LateClose()
        late.task = asyncio.ensure_future(self._close_late_activation(activation, late))
        late.task.add_done_callback(_consume_result)
        self._late_closes.append(late)

    def _cleanup_allowance(
        self, seconds: float, deadline_at: float | None
    ) -> tuple[float, bool]:
        """Cap *seconds* by what remains until *deadline_at* on the clock.

        Returns the allowance and whether the deadline is what bounds it: a
        close that overruns a capped allowance exceeded the global deadline,
        not a budget of its own. Without a deadline the budget stands alone.
        """

        if deadline_at is None:
            return seconds, False
        remaining = max(deadline_at - self.clock(), 0.0)
        return min(seconds, remaining), remaining < seconds

    async def _close_late_activation(
        self, activation: ModuleActivation, late: "_LateClose"
    ) -> None:
        allowance, capped = self._cleanup_allowance(
            self._late_close_seconds, self.cleanup_deadline_at
        )
        task = asyncio.ensure_future(activation.close())
        try:
            finished = await _wait_bounded({task}, allowance, self._sleep)
            if task not in finished:
                request_cancellation(task)
                grace, _ = self._cleanup_allowance(
                    self._cancel_grace_seconds, self.cleanup_deadline_at
                )
                await _wait_bounded({task}, grace, self._sleep)
                self._abandon(task)
                self._report_late(
                    _field_diagnostic(
                        activation.name,
                        "close",
                        _EXCEEDED_CLEANUP_DEADLINE if capped else "late handle close timed out",
                    )
                )
                return
            if task.cancelled():
                raise RuntimeError("close was cancelled")
            task.result()
        except asyncio.CancelledError:
            # Cancelled once: a close already asked — by the overrun above
            # when the deadline and this cancellation coincide — is not asked
            # again, so one that resists the request is not killed by it.
            if not _cancellation_requested(task):
                request_cancellation(task)
            if late.expired_at is None:
                # The loop tearing this task down, not the deadline: nothing
                # is reported, as with any other abandoned owner.
                self._abandon(task)
                raise
            # settle_late_results gave up on this close at its cleanup
            # deadline (R4): the close is given the grace that remains — none
            # past the deadline, beyond one scheduling turn — then abandoned
            # with its outcome consumed, and reported as unfinished. The
            # loader keeps owning the task; no caller waits for it.
            grace, _ = self._cleanup_allowance(
                self._cancel_grace_seconds, late.expired_at
            )
            try:
                await _wait_bounded({task}, grace, self._sleep)
            finally:
                self._abandon(task)
                self._report_late(
                    _field_diagnostic(
                        activation.name, "close", _EXCEEDED_CLEANUP_DEADLINE
                    )
                )
            raise
        except Exception:
            self._report_late(
                _field_diagnostic(activation.name, "close", "late handle close failed")
            )

    def _report_late(self, diagnostic: str) -> None:
        """Record a late close's diagnostic and hand it to its reporting owner.

        The reporter receives it now, whether or not anyone still awaits
        :meth:`settle_late_results`; without a reporter it is held for the
        next such call. Either way it is handed over exactly once.
        """

        self.late_diagnostics.append(diagnostic)
        reporter = self.late_reporter
        if reporter is None:
            self._unreported_late.append(diagnostic)
            return
        try:
            reporter(diagnostic)
        except Exception:
            # A reporter that fails must not lose the diagnostic: it is held
            # for the next settle, the same as with no reporter at all.
            self._unreported_late.append(diagnostic)

    async def settle_late_results(
        self, *, deadline_at: float | None = None
    ) -> list[str]:
        """Wait for the late closes already under way; hand over what they
        reported and nobody else has been handed yet.

        *deadline_at* is the absolute point on the loader's clock by which
        this returns: the one global cleanup deadline the entry point shares
        with the coordinator (R4), so the wait here never adds a budget of
        its own to the coordinator's. A close still unfinished at that point
        is given up on — cancelled, granted the grace that remains, reported
        as having exceeded the deadline — and stays owned by this loader,
        which never extends the caller's wait for it. Without a deadline each
        close is bounded on its own, so this waits at most one close budget
        plus one grace. Either way it never waits for the abandoned hook that
        may still return a handle: such a handle is closed by this loader
        when it arrives, whether or not anyone is still waiting here, and
        what that close reports reaches :attr:`late_reporter` at that
        moment. The returned diagnostics are the ones no reporter received;
        returning them hands them over, so a second call does not repeat
        them.
        """

        pending = [late for late in self._late_closes if not late.task.done()]
        if pending:
            tasks = {late.task for late in pending}
            if deadline_at is None:
                await asyncio.wait(tasks)
            else:
                remaining = max(deadline_at - self.clock(), 0.0)
                finished = await _wait_bounded(tasks, remaining, self._sleep)
                unfinished = [late for late in pending if late.task not in finished]
                for late in unfinished:
                    late.expired_at = deadline_at
                    request_cancellation(late.task)
                # Each expiry settles within scheduling turns, never time:
                # the grace left past the deadline is one turn.
                if unfinished:
                    await asyncio.wait({late.task for late in unfinished})
        self._late_closes = [late for late in self._late_closes if not late.task.done()]
        handed_over = list(self._unreported_late)
        self._unreported_late.clear()
        return handed_over

    def _validate_v1_handle(self, name: str, handle: Any) -> None:
        """A v1 handle must expose the async ``close()`` that frees it."""

        close = None if handle is None else getattr(handle, "close", None)
        if not _is_async_callable(close):
            raise _field_error(
                name, "close", "async lifecycle hook is required"
            )

    def _validate_v2_handle(
        self, name: str, declaration: ManifestDeclaration, handle: Any
    ) -> None:
        """A v2 handle exposes phase hooks; whatever it exposes must be async.

        A phase it says nothing about is a no-op for it, which is the
        lifecycle's own rule. The one hook that is *required* is
        ``start_inputs`` on a module declaring the ``input`` role: without it
        the module could only produce from ``activate``, which is the very
        thing the readiness barrier forbids (R4).
        """

        if handle is None:
            raise _field_error(
                name, "activate", "must return a handle exposing its phase hooks"
            )
        for hook_name in sorted(_V2_HOOK_NAMES):
            hook = getattr(handle, hook_name, None)
            if hook is None:
                continue
            if not _is_async_callable(hook):
                raise _field_error(
                    name, hook_name, "phase hook must be asynchronous"
                )
        if ROLE_INPUT in declaration.roles and not _is_async_callable(
            getattr(handle, "start_inputs", None)
        ):
            raise _field_error(
                name,
                "start_inputs",
                "a module declaring the 'input' role must open its source in "
                "start_inputs, after the readiness barrier",
            )

    # -- settings, secrets and declarations -------------------------------- #

    def _redact_credentials(
        self,
        enabled: Sequence[str],
        declarations: Mapping[str, ManifestDeclaration],
        settings: Mapping[str, Mapping[str, Any]],
    ) -> None:
        """Hand every declared credential value to supervision's redaction.

        Runs after every enabled module's settings were accepted and before
        any module is activated, so the first trace a module publishes is
        already redacted of every credential any module was configured with.
        The value is read from the accepted settings: a literal is as much a
        credential as a resolved reference. A declared path the settings do
        not carry — an optional credential left unset — contributes nothing.
        """

        values: list[str] = []
        for name in enabled:
            for path in declarations[name].credentials:
                value = _value_at(settings[name], path)
                if isinstance(value, str) and value:
                    values.append(value)
        if not values:
            return
        supervision = getattr(self.context, "supervision", None)
        redact = getattr(supervision, "redact", None)
        if not callable(redact):
            # Refused rather than activated unredacted: a runtime whose
            # supervision cannot be told the credentials would publish them.
            raise _field_error(
                "<runtime context>",
                "supervision",
                "must expose redact() for the credentials the enabled modules declare",
            )
        self.redacted_credentials = int(redact(tuple(values)))

    def _resolve_secrets(self, name: str, settings: Mapping[str, Any]) -> Any:
        """Resolve ``${NAME}`` references inside one enabled module's settings.

        Called for enabled modules only (R7, AC25). Settings holding no
        reference are returned **unchanged**, the caller's own object and not a
        copy of it, so resolution is observable exactly where it happened and
        nowhere else.
        """

        resolved, changed = self._resolve_value(
            settings, f"modules.{name}", set(), name
        )
        return resolved if changed else settings

    def _resolve_value(
        self, value: Any, path: str, active: set[int], name: str
    ) -> tuple[Any, bool]:
        if isinstance(value, str):
            match = _ENV_REFERENCE.fullmatch(value)
            if match is None:
                return value, False
            variable = match.group(1)
            if variable not in self.environ:
                raise _field_error(name, path, "environment reference is unresolved")
            self.resolved_secrets += 1
            return self.environ[variable], True

        if isinstance(value, Mapping):
            if id(value) in active:
                raise _field_error(name, path, "recursive value is invalid")
            active.add(id(value))
            try:
                mapping: dict[Any, Any] = {}
                changed = False
                for key, item in value.items():
                    label = f"{path}.{key}" if isinstance(key, str) and key else path
                    mapping[key], item_changed = self._resolve_value(
                        item, label, active, name
                    )
                    changed = changed or item_changed
            finally:
                active.discard(id(value))
            return (mapping, True) if changed else (value, False)

        if isinstance(value, list):
            if id(value) in active:
                raise _field_error(name, path, "recursive value is invalid")
            active.add(id(value))
            try:
                sequence: list[Any] = []
                changed = False
                for index, item in enumerate(value):
                    resolved, item_changed = self._resolve_value(
                        item, f"{path}[{index}]", active, name
                    )
                    sequence.append(resolved)
                    changed = changed or item_changed
            finally:
                active.discard(id(value))
            return (sequence, True) if changed else (value, False)

        return value, False

    async def _validate_settings(
        self,
        name: str,
        declaration: ManifestDeclaration,
        entry_point: "_EntryPoint",
        settings: Mapping[str, Any],
    ) -> list[str]:
        """Run this module's own settings validation, before any activation.

        Returns the diagnostics, one per refusal, each naming the module and
        a field and carrying no configured value (R7, AC24). The declared
        schema runs first; a mapping it refuses is reported by field and the
        hook is not asked about a shape it never declared. The hook then
        refuses in one of two ways:

        by raising
            Nothing it raises reaches the report, whatever its type: the
            message is module-authored text and the hook was handed the
            settings, so it may carry the rejected credential back out. The
            diagnostic names the module and the declared hook instead.

        by returning a non-empty list
            Each entry is the module's own ``module 'x': field 'y': reason``
            and is passed through — that is what lets the report name the
            field the module knows and the core does not — after
            :func:`_module_diagnostic` has held it to this module's name, to
            a field shaped like a setting path and free of any configured
            value, and to a reason from which every configured value has
            been redacted.
        """

        if declaration.settings_schema is not None:
            try:
                validate_against_schema(
                    settings, declaration.settings_schema, label="settings"
                )
            except ContractError as exc:
                return [
                    _field_diagnostic(name, exc.field, _value_free_reason(exc.reason))
                ]

        validator = entry_point.settings_validator
        if validator is None:
            if declaration.settings_validator is not None:
                return [
                    _field_diagnostic(
                        name,
                        MANIFEST_SETTINGS_VALIDATOR_KEY,
                        "names a settings hook that could not be resolved",
                    )
                ]
            return []
        hook_name = declaration.settings_validator or MANIFEST_SETTINGS_VALIDATOR_KEY
        try:
            outcome = validator(settings)
            if inspect.isawaitable(outcome):
                status, result = await self._await_module(
                    outcome,
                    module=name,
                    field_name=hook_name,
                    operation="settings validation",
                )
                if status == "deadline":
                    return [
                        _field_diagnostic(
                            name, hook_name, "exceeded the global startup deadline"
                        )
                    ]
                if status == "cancelled":
                    return [_field_diagnostic(name, hook_name, "was cancelled")]
                outcome = result
        except Exception:
            return [_field_diagnostic(name, hook_name, "settings were refused by the module")]
        if isinstance(outcome, (list, tuple)) and outcome:
            values = _configured_values(settings)
            return [
                _module_diagnostic(name, entry, hook_name, values=values)
                for entry in outcome
            ]
        return []

    def _register_declarations(
        self,
        name: str,
        declaration: ManifestDeclaration,
        settings: Mapping[str, Any],
    ) -> None:
        """Record declared actions and triggers. Recording is not granting.

        A declared action lands in the registry's *discovered* view and stays
        there until some module binds a provider to it and some rule
        authorizes it. Neither the declaration, the module's capabilities, its
        produced events nor a ``read`` nature moves it into the *authorized*
        view (R7, R5).

        A declared trigger policy may refer to the companion name by token;
        the name itself is business configuration of the declaring input, so
        it is read from that module's own validated *settings* and handed to
        the registry for this input only (R1). The core's configuration never
        carries it.
        """

        if not declaration.is_v2:
            return

        scoped = self.context.for_module(name)
        for spec in declaration.actions:
            try:
                scoped.actions.declare(spec)
            except Exception as exc:
                raise _field_error(
                    name, MANIFEST_ACTIONS_KEY, _sanitised_reason(str(exc))
                ) from None

        if declaration.triggers is None:
            return
        engine = getattr(self.context, "triggers", None)
        registry = getattr(engine, "registry", engine)
        if registry is None or not callable(getattr(registry, "register", None)):
            raise _field_error(
                name,
                MANIFEST_TRIGGERS_KEY,
                "declares triggers, and the runtime context carries no trigger engine",
            )
        companion_name = settings.get(COMPANION_NAME_SETTING)
        if not isinstance(companion_name, str) or not companion_name.strip():
            companion_name = None
        try:
            registry.register(
                name, declaration.triggers, companion_name=companion_name
            )
        except Exception as exc:
            raise _field_error(
                name, MANIFEST_TRIGGERS_KEY, _sanitised_reason(str(exc))
            ) from None

    def _apply_trigger_configuration(
        self, config: Mapping[str, Any]
    ) -> None:
        """Select the trigger policies configuration asks for (R1).

        Runs after every enabled input registered its declaration, so each
        configured policy is validated against what its input really declares
        — a type the module does not declare, a parameter its schema refuses
        or an operator it does not support stops startup here, before any
        module is activated (R7) — and the selected policy replaces the module
        default entirely, per channel (AC1).
        """

        configured = config.get("triggers")
        if configured is None:
            return
        engine = getattr(self.context, "triggers", None)
        registry = getattr(engine, "registry", engine)
        if registry is None or not callable(getattr(registry, "configure", None)):
            raise _field_error(
                "<config>",
                "triggers",
                "selects trigger policies, and the runtime context carries no "
                "trigger engine; the limits block is required",
            )
        if not isinstance(configured, Mapping):
            raise _field_error("<config>", "triggers", "must be a mapping")
        for input_name, entry in configured.items():
            if not isinstance(input_name, str) or not input_name.strip():
                raise _field_error(
                    "<config>", "triggers", "must use non-empty input names"
                )
            if not isinstance(entry, Mapping):
                raise _field_error(input_name, "triggers", "must be a mapping")
            label = f"triggers.{input_name}"
            unknown = sorted(set(entry) - {"channels"})
            if unknown:
                raise _field_error(
                    input_name,
                    label,
                    f"is not a known trigger configuration key ({', '.join(unknown)})",
                )
            channels = entry.get("channels")
            if not isinstance(channels, Mapping):
                raise _field_error(input_name, f"{label}.channels", "must be a mapping")
            for channel_id, channel_entry in channels.items():
                if not isinstance(channel_id, str) or not channel_id.strip():
                    raise _field_error(
                        input_name,
                        f"{label}.channels",
                        "must use non-empty channel identifiers",
                    )
                channel_label = f"{label}.channels.{channel_id}"
                policy = _trigger_policy(input_name, channel_label, channel_entry)
                try:
                    registry.configure(input_name, policy, channel_id=channel_id)
                except Exception as exc:
                    raise _field_error(
                        input_name, channel_label, _sanitised_reason(str(exc))
                    ) from None

    # -- discovery --------------------------------------------------------- #

    def _discover(self) -> dict[str, DiscoveredModule]:
        root = self.modules_directory
        if not root.is_dir():
            raise ModuleLoadError(
                "module '<modules_directory>': field 'modules_directory': "
                "directory is not readable"
            )

        discovered: dict[str, DiscoveredModule] = {}
        try:
            children = sorted(root.iterdir(), key=lambda path: path.name)
        except OSError:
            raise ModuleLoadError(
                "module '<modules_directory>': field 'modules_directory': "
                "directory is not readable"
            ) from None

        for directory in children:
            # A directory becomes a module candidate by containing a manifest.
            # Ignore caches, VCS metadata, editor directories, and symlinked
            # directories rather than importing code outside the configured root.
            if directory.is_symlink() or not directory.is_dir():
                continue
            manifest_path = directory / "module.yaml"
            if not manifest_path.is_file():
                continue

            try:
                resolved_directory = directory.resolve(strict=True)
            except OSError:
                raise _field_error(
                    directory.name, "module.yaml", "directory is not readable"
                ) from None
            if resolved_directory.parent != root:
                raise _field_error(
                    directory.name,
                    "module.yaml",
                    "module directory must be an immediate child",
                )

            try:
                with manifest_path.open("r", encoding="utf-8") as stream:
                    manifest = yaml.safe_load(stream)
            except (OSError, yaml.YAMLError):
                raise _field_error(
                    directory.name, "module.yaml", "must contain readable YAML"
                ) from None

            # Every discovered manifest is validated, enabled or not (R7, AC25).
            declaration = _validate_manifest(directory.name, manifest)
            name = declaration.manifest["name"]
            if name in discovered:
                raise _field_error(name, "name", "must be unique")

            discovered[name] = DiscoveredModule(
                name=name,
                directory=resolved_directory,
                manifest=declaration.manifest,
                declaration=declaration,
            )

        return discovered

    def _validate_config(
        self,
        config: Mapping[str, Any],
        discovered: Mapping[str, DiscoveredModule],
    ) -> tuple[list[str], Mapping[str, Mapping[str, Any]]]:
        if not isinstance(config, Mapping):
            raise _field_error("<config>", "config", "must be a mapping")

        enabled = config.get("enabled_modules")
        if not isinstance(enabled, list):
            raise _field_error(
                "<config>", "enabled_modules", "must be a list"
            )

        seen: set[str] = set()
        for name in enabled:
            if not isinstance(name, str) or not name.strip():
                raise _field_error(
                    "<config>", "enabled_modules", "must contain non-empty names"
                )
            if name in seen:
                raise _field_error(name, "enabled_modules", "must be unique")
            seen.add(name)
            if name not in discovered:
                raise _field_error(name, "enabled_modules", "has no manifest")

        module_settings = config.get("modules")
        if not isinstance(module_settings, Mapping):
            raise _field_error("<config>", "modules", "must be a mapping")

        for name in module_settings:
            if not isinstance(name, str) or not name.strip():
                raise _field_error(
                    "<config>", "modules", "must use non-empty module names"
                )
            if name not in discovered:
                raise _field_error(name, "modules", "has no manifest")

        # Settings are required when the module is enabled, and only then: a
        # disabled module's business settings are not required (R7, AC25).
        for name in enabled:
            if name not in module_settings:
                raise _field_error(
                    name, "modules", "settings are required when enabled"
                )
            if not isinstance(module_settings[name], Mapping):
                raise _field_error(name, "modules", "settings must be a mapping")

        return list(enabled), module_settings

    def _load_entry_point(self, module: DiscoveredModule) -> "_EntryPoint":
        init_path = (module.directory / "__init__.py").resolve()
        if module.directory.parent != self.modules_directory:
            raise _field_error(
                module.name,
                "__init__.py",
                "must belong to an immediate module directory",
            )
        if init_path.parent != module.directory:
            raise _field_error(
                module.name, "__init__.py", "must stay inside its module directory"
            )
        if not init_path.is_file():
            raise _field_error(module.name, "__init__.py", "is required")

        internal_name = f"_companion_module_{uuid4().hex}"
        spec = importlib.util.spec_from_file_location(
            internal_name,
            init_path,
            submodule_search_locations=[str(module.directory)],
        )
        if spec is None or spec.loader is None:
            raise _field_error(module.name, "__init__.py", "could not be imported")

        imported = importlib.util.module_from_spec(spec)
        sys.modules[internal_name] = imported
        try:
            spec.loader.exec_module(imported)
        except Exception:
            _remove_imported_module(internal_name)
            raise _field_error(
                module.name, "__init__.py", "could not be imported"
            ) from None

        try:
            return _validate_entry_point(module.name, imported, _declaration(module))
        except Exception:
            _remove_imported_module(internal_name)
            raise


@dataclass(frozen=True, slots=True)
class _EntryPoint:
    """The callables a module's ``__init__.py`` must supply.

    ``settings_validator`` is resolved here rather than at validation time: a
    manifest naming a hook the module does not define is a startup failure
    naming that module, never a module silently treated as valid.
    """

    activate: Any
    settings_validator: Any = None


def _entry_point(value: Any) -> _EntryPoint:
    """Normalise what :meth:`ModuleLoader._load_entry_point` handed back.

    That method is an injection seam. A caller substituting it may return the
    bare ``activate`` callable, which is the whole v1 entry-point contract; a
    manifest declaring a settings hook that this route cannot resolve is then
    refused in :meth:`ModuleLoader._validate_settings`, never treated as valid.
    """

    if isinstance(value, _EntryPoint):
        return value
    return _EntryPoint(activate=value)


def _declaration(module: DiscoveredModule) -> ManifestDeclaration:
    """The parsed declaration of *module*, re-parsing a hand-built one."""

    if module.declaration is not None:
        return module.declaration
    return _validate_manifest(module.name, module.manifest)


# --------------------------------------------------------------------------- #
# Manifest validation (R7)
# --------------------------------------------------------------------------- #


def _validate_manifest(directory_name: str, value: Any) -> ManifestDeclaration:
    """Validate one manifest and parse its declarations into contracts.

    The v1 keys keep their exact meaning and their exact diagnostics. The v2
    keys are accepted only on a v2 manifest, and every one of them is turned
    into the contract the runtime will act on right here, so a manifest that
    could only fail at ingestion or at the first call fails at discovery.
    """

    if not isinstance(value, Mapping):
        raise _field_error(directory_name, "module.yaml", "must be a mapping")

    manifest = dict(value)
    name = manifest.get("name")
    if not isinstance(name, str) or not name.strip():
        raise _field_error(directory_name, "name", "must be a non-empty string")

    for field_name in ("produces", "consumes"):
        patterns = manifest.get(field_name)
        if not isinstance(patterns, list):
            raise _field_error(name, field_name, "must be a list")
        for pattern in patterns:
            try:
                _validate_event_pattern(pattern)
            except (TypeError, ValueError):
                raise _field_error(
                    name, field_name, "contains an invalid event pattern"
                ) from None

    if "middleware" not in manifest:
        raise _field_error(name, "middleware", "is required")
    if not isinstance(manifest["middleware"], bool):
        raise _field_error(name, "middleware", "must be a Boolean")

    if manifest["middleware"]:
        order = manifest.get("order")
        if not isinstance(order, int) or isinstance(order, bool):
            raise _field_error(name, "order", "must be an integer")

    manifest_version = _manifest_version(name, manifest)
    roles = _manifest_roles(name, manifest)

    if manifest_version == MANIFEST_VERSION_V1:
        for key in _V2_ONLY_KEYS:
            if key in manifest:
                raise _field_error(
                    name,
                    key,
                    f"is a v{MANIFEST_VERSION_V2} declaration; a manifest with no "
                    f"{MANIFEST_VERSION_KEY} is v{MANIFEST_VERSION_V1}",
                )
        return ManifestDeclaration(
            manifest=manifest,
            manifest_version=MANIFEST_VERSION_V1,
            roles=roles,
        )

    settings_schema = _manifest_settings_schema(name, manifest)
    return ManifestDeclaration(
        manifest=manifest,
        manifest_version=manifest_version,
        runtime_api=_manifest_runtime_api(name, manifest),
        roles=roles,
        settings_schema=settings_schema,
        settings_validator=_manifest_settings_validator(name, manifest),
        actions=_manifest_actions(name, manifest),
        triggers=_manifest_triggers(name, manifest),
        credentials=_manifest_credentials(name, manifest, settings_schema),
    )


def _manifest_version(name: str, manifest: Mapping[str, Any]) -> int:
    """The declared contract version. Absent means v1; unknown is refused."""

    if MANIFEST_VERSION_KEY not in manifest:
        return MANIFEST_VERSION_V1
    version = manifest[MANIFEST_VERSION_KEY]
    if not isinstance(version, int) or isinstance(version, bool):
        raise _field_error(name, MANIFEST_VERSION_KEY, "must be an integer")
    if version not in SUPPORTED_MANIFEST_VERSIONS:
        supported = ", ".join(str(item) for item in SUPPORTED_MANIFEST_VERSIONS)
        # Refuse rather than guess: routing an unknown contract through either
        # arm of the fork is exactly how a handle loses its close hook.
        raise _field_error(
            name,
            MANIFEST_VERSION_KEY,
            f"is not a manifest version this runtime can load; supported: {supported}",
        )
    return version


def _manifest_runtime_api(name: str, manifest: Mapping[str, Any]) -> int:
    """The runtime contract the module was built against (R7)."""

    if MANIFEST_RUNTIME_API_KEY not in manifest:
        raise _field_error(
            name,
            MANIFEST_RUNTIME_API_KEY,
            f"is required by a v{MANIFEST_VERSION_V2} manifest",
        )
    declared = manifest[MANIFEST_RUNTIME_API_KEY]
    if not isinstance(declared, int) or isinstance(declared, bool):
        raise _field_error(name, MANIFEST_RUNTIME_API_KEY, "must be an integer")
    if declared not in SUPPORTED_RUNTIME_APIS:
        supported = ", ".join(str(item) for item in sorted(SUPPORTED_RUNTIME_APIS))
        raise _field_error(
            name,
            MANIFEST_RUNTIME_API_KEY,
            f"is not a runtime contract this runtime provides; supported: {supported}",
        )
    return declared


def _manifest_roles(name: str, manifest: Mapping[str, Any]) -> frozenset[str]:
    """The declared lifecycle roles, parsed once so no phase reads a name."""

    section = manifest.get(MANIFEST_LIFECYCLE_KEY)
    if section is None:
        return frozenset()
    field_name = MANIFEST_LIFECYCLE_KEY
    if not isinstance(section, Mapping):
        raise _field_error(name, field_name, "must be a mapping")
    declared = section.get(MANIFEST_ROLES_KEY)
    if declared is None:
        return frozenset()
    field_name = f"{MANIFEST_LIFECYCLE_KEY}.{MANIFEST_ROLES_KEY}"
    if isinstance(declared, str) or not isinstance(declared, (list, tuple)):
        raise _field_error(name, field_name, "must be a list of roles")
    roles: set[str] = set()
    for role in declared:
        if not isinstance(role, str) or role not in ROLES:
            known = ", ".join(sorted(ROLES))
            raise _field_error(
                name, field_name, f"unknown role; declared roles are: {known}"
            )
        roles.add(role)
    return frozenset(roles)


def _manifest_settings_schema(
    name: str, manifest: Mapping[str, Any]
) -> Mapping[str, Any] | None:
    if MANIFEST_SETTINGS_SCHEMA_KEY not in manifest:
        return None
    schema = manifest[MANIFEST_SETTINGS_SCHEMA_KEY]
    try:
        validate_schema(schema, label=MANIFEST_SETTINGS_SCHEMA_KEY)
    except SchemaError as exc:
        raise _field_error(name, exc.field, exc.reason) from None
    return schema


def _manifest_credentials(
    name: str, manifest: Mapping[str, Any], schema: Mapping[str, Any] | None
) -> tuple[tuple[str, ...], ...]:
    """Parse the declared ``credentials`` into setting paths (R8, AC29).

    Every entry must name, by dotted path, a property the settings schema
    declares as a string: a declaration the schema does not back would be
    a path nothing is ever read from, and a misspelt one must not leave a
    credential unredacted in silence.
    """

    if MANIFEST_CREDENTIALS_KEY not in manifest:
        return ()
    declared = manifest[MANIFEST_CREDENTIALS_KEY]
    field_name = MANIFEST_CREDENTIALS_KEY
    if isinstance(declared, str) or not isinstance(declared, (list, tuple)):
        raise _field_error(name, field_name, "must be a list of setting paths")
    if declared and schema is None:
        raise _field_error(
            name,
            field_name,
            f"requires a {MANIFEST_SETTINGS_SCHEMA_KEY} declaring each listed setting",
        )
    paths: list[tuple[str, ...]] = []
    for index, entry in enumerate(declared):
        label = f"{field_name}[{index}]"
        if not isinstance(entry, str) or not entry:
            raise _field_error(name, label, "must be a non-empty setting path")
        path = tuple(entry.split("."))
        if any(not segment for segment in path):
            raise _field_error(name, label, "must be a dotted setting path")
        if path in paths:
            raise _field_error(name, label, "must be unique")
        if not _schema_declares_string(schema, path):
            raise _field_error(
                name,
                label,
                f"must name a string property {MANIFEST_SETTINGS_SCHEMA_KEY} declares",
            )
        paths.append(path)
    return tuple(paths)


def _schema_declares_string(schema: Any, path: Sequence[str]) -> bool:
    """Whether *schema* declares *path* as a property of type ``string``."""

    node: Any = schema
    for segment in path:
        if not isinstance(node, Mapping):
            return False
        properties = node.get("properties")
        if not isinstance(properties, Mapping) or segment not in properties:
            return False
        node = properties[segment]
    return isinstance(node, Mapping) and node.get("type") == "string"


def _value_at(settings: Any, path: Sequence[str]) -> Any:
    """The value at *path* inside *settings*, or ``None`` when absent."""

    node: Any = settings
    for segment in path:
        if not isinstance(node, Mapping) or segment not in node:
            return None
        node = node[segment]
    return node


def _manifest_settings_validator(
    name: str, manifest: Mapping[str, Any]
) -> str | None:
    if MANIFEST_SETTINGS_VALIDATOR_KEY not in manifest:
        return None
    declared = manifest[MANIFEST_SETTINGS_VALIDATOR_KEY]
    if not isinstance(declared, str) or not declared.isidentifier():
        raise _field_error(
            name,
            MANIFEST_SETTINGS_VALIDATOR_KEY,
            "must name a callable defined by the module",
        )
    return declared


def _manifest_actions(name: str, manifest: Mapping[str, Any]) -> tuple[ActionSpec, ...]:
    """Parse the declared ``actions`` into :class:`ActionSpec` contracts.

    Declaring one records it; it authorizes nothing, whatever its nature (R5).
    """

    if MANIFEST_ACTIONS_KEY not in manifest:
        return ()
    declared = manifest[MANIFEST_ACTIONS_KEY]
    if isinstance(declared, (str, Mapping)) or not isinstance(declared, (list, tuple)):
        raise _field_error(name, MANIFEST_ACTIONS_KEY, "must be a list")
    specs: list[ActionSpec] = []
    seen: set[str] = set()
    for index, entry in enumerate(declared):
        field_name = f"{MANIFEST_ACTIONS_KEY}[{index}]"
        if not isinstance(entry, Mapping):
            raise _field_error(name, field_name, "must be a mapping")
        unknown = sorted(set(entry) - _ACTION_KEYS)
        if unknown:
            raise _field_error(
                name, field_name, f"declares unknown keys: {', '.join(unknown)}"
            )
        destinations = entry.get("supported_destinations")
        if isinstance(destinations, (str, Mapping)) or not isinstance(
            destinations, (list, tuple)
        ):
            raise _field_error(
                name,
                f"{field_name}.supported_destinations",
                "must be a list of destinations",
            )
        # Checked here rather than left to ``tuple()``: that conversion accepts
        # anything iterable, so a bare "read" would silently become four
        # single-letter permissions and a ``false`` an empty requirement —
        # a malformed manifest quietly widening a grant (R5, R7).
        permissions = entry.get("required_permissions")
        if permissions is None:
            permissions = ()
        if isinstance(permissions, (str, bytes, Mapping)) or not isinstance(
            permissions, (list, tuple)
        ):
            raise _field_error(
                name,
                f"{field_name}.required_permissions",
                "must be a list of permission names",
            )
        try:
            spec = ActionSpec(
                name=entry.get("name"),
                version=entry.get("version"),
                description=entry.get("description"),
                argument_schema=entry.get("argument_schema"),
                result_schema=entry.get("result_schema"),
                nature=entry.get("nature"),
                required_permissions=tuple(permissions),
                supported_destinations=tuple(
                    _destination(name, f"{field_name}.supported_destinations[{at}]", item)
                    for at, item in enumerate(destinations)
                ),
                timeout_seconds=entry.get("timeout_seconds"),
                idempotency=entry.get("idempotency"),
                delivery=entry.get("delivery"),
                model_proposable=entry.get("model_proposable", False),
            )
        except ModuleLoadError:
            raise
        except ContractError as exc:
            # The contract names the field it refused (``ActionSpec.delivery``
            # on a ``read`` action, AC58): the diagnostic names that field of
            # this very action rather than the whole entry, and the action by
            # name once its name was accepted (``model_proposable`` on a read
            # or a delivery-capable action, AC25).
            raise _field_error(
                name,
                _action_field(field_name, exc),
                _sanitised_reason(_action_reason(entry.get("name"), exc)),
            ) from None
        except (TypeError, ValueError) as exc:
            raise _field_error(name, field_name, _sanitised_reason(str(exc))) from None
        if spec.name in seen:
            raise _field_error(
                name, field_name, f"declares action {spec.name!r} twice"
            )
        seen.add(spec.name)
        specs.append(spec)
    return tuple(specs)


_ACTION_SPEC_FIELD_PREFIX = "ActionSpec."


def _action_field(field_name: str, exc: ContractError) -> str:
    """The manifest field a refused ``ActionSpec`` field corresponds to.

    ``ActionSpec.delivery`` on ``actions[2]`` reads ``actions[2].delivery``;
    a contract field named otherwise leaves the entry named as a whole.
    """

    field = getattr(exc, "field", None)
    if isinstance(field, str) and field.startswith(_ACTION_SPEC_FIELD_PREFIX):
        return f"{field_name}.{field[len(_ACTION_SPEC_FIELD_PREFIX):]}"
    return field_name


def _action_reason(action: Any, exc: ContractError) -> str:
    """The refusal of an ``ActionSpec``, naming the action it refused.

    A refused name is not repeated: the field already says what is wrong
    with it, and the value itself is never echoed.
    """

    if getattr(exc, "field", None) == "ActionSpec.name" or not isinstance(action, str):
        return exc.reason
    return f"action {action!r}: {exc.reason}"


def _destination(name: str, field_name: str, value: Any) -> Destination:
    if not isinstance(value, Mapping):
        raise _field_error(name, field_name, "must be a mapping")
    unknown = sorted(set(value) - _DESTINATION_KEYS)
    if unknown:
        raise _field_error(
            name, field_name, f"declares unknown keys: {', '.join(unknown)}"
        )
    try:
        return Destination(
            platform=value.get("platform"),
            channel_id=value.get("channel_id"),
            scope=value.get("scope"),
        )
    except (ContractError, TypeError, ValueError) as exc:
        raise _field_error(name, field_name, _sanitised_reason(str(exc))) from None


def _manifest_triggers(name: str, manifest: Mapping[str, Any]) -> TriggerSpec | None:
    """Parse the declared ``triggers`` into a :class:`TriggerSpec` (R1, R7)."""

    if MANIFEST_TRIGGERS_KEY not in manifest:
        return None
    declared = manifest[MANIFEST_TRIGGERS_KEY]
    if not isinstance(declared, Mapping):
        raise _field_error(name, MANIFEST_TRIGGERS_KEY, "must be a mapping")
    unknown = sorted(set(declared) - _TRIGGER_KEYS)
    if unknown:
        raise _field_error(
            name,
            MANIFEST_TRIGGERS_KEY,
            f"declares unknown keys: {', '.join(unknown)}",
        )

    raw_types = declared.get("types", [])
    if isinstance(raw_types, (str, Mapping)) or not isinstance(
        raw_types, (list, tuple)
    ):
        raise _field_error(name, f"{MANIFEST_TRIGGERS_KEY}.types", "must be a list")
    types: list[TriggerTypeDeclaration] = []
    for index, entry in enumerate(raw_types):
        field_name = f"{MANIFEST_TRIGGERS_KEY}.types[{index}]"
        if not isinstance(entry, Mapping):
            raise _field_error(name, field_name, "must be a mapping")
        unknown = sorted(set(entry) - _TRIGGER_TYPE_KEYS)
        if unknown:
            raise _field_error(
                name, field_name, f"declares unknown keys: {', '.join(unknown)}"
            )
        try:
            types.append(
                TriggerTypeDeclaration(
                    name=entry.get("name"),
                    parameter_schema=entry.get("parameter_schema"),
                )
            )
        except (ContractError, TypeError, ValueError) as exc:
            raise _field_error(name, field_name, _sanitised_reason(str(exc))) from None

    raw_combinations = declared.get("combinations", [])
    if isinstance(raw_combinations, str) or not isinstance(
        raw_combinations, (list, tuple)
    ):
        raise _field_error(
            name, f"{MANIFEST_TRIGGERS_KEY}.combinations", "must be a list"
        )

    default_policy = None
    if declared.get("default_policy") is not None:
        default_policy = _trigger_policy(
            name,
            f"{MANIFEST_TRIGGERS_KEY}.default_policy",
            declared["default_policy"],
        )

    try:
        return TriggerSpec(
            types=tuple(types),
            combinations=tuple(raw_combinations),
            default_policy=default_policy,
        )
    except (ContractError, TypeError, ValueError) as exc:
        raise _field_error(
            name, MANIFEST_TRIGGERS_KEY, _sanitised_reason(str(exc))
        ) from None


def _trigger_policy(name: str, field_name: str, value: Any) -> TriggerPolicy:
    if not isinstance(value, Mapping):
        raise _field_error(name, field_name, "must be a mapping")
    unknown = sorted(set(value) - _POLICY_KEYS)
    if unknown:
        raise _field_error(
            name, field_name, f"declares unknown keys: {', '.join(unknown)}"
        )
    raw_rules = value.get("rules")
    if isinstance(raw_rules, (str, Mapping)) or not isinstance(
        raw_rules, (list, tuple)
    ):
        raise _field_error(name, f"{field_name}.rules", "must be a list")
    rules: list[TriggerRule] = []
    for index, entry in enumerate(raw_rules):
        location = f"{field_name}.rules[{index}]"
        if not isinstance(entry, Mapping):
            raise _field_error(name, location, "must be a mapping")
        unknown = sorted(set(entry) - _RULE_KEYS)
        if unknown:
            raise _field_error(
                name, location, f"declares unknown keys: {', '.join(unknown)}"
            )
        try:
            rules.append(
                TriggerRule(
                    type=entry.get("type"),
                    parameters=entry.get("parameters") or {},
                )
            )
        except (ContractError, TypeError, ValueError) as exc:
            raise _field_error(name, location, _sanitised_reason(str(exc))) from None
    try:
        return TriggerPolicy(
            rules=tuple(rules), combination=value.get("combination", "all_of")
        )
    except (ContractError, TypeError, ValueError) as exc:
        raise _field_error(name, field_name, _sanitised_reason(str(exc))) from None


def _validate_event_pattern(pattern: Any) -> None:
    if not isinstance(pattern, str):
        raise TypeError("pattern must be a string")
    if not pattern:
        raise ValueError("pattern must not be empty")
    segments = pattern.split(".")
    if any(not segment for segment in segments):
        raise ValueError("pattern must contain no empty segments")
    if any("*" in segment and segment not in {"*", "**"} for segment in segments):
        raise ValueError("wildcards must occupy a complete segment")


def _validate_entry_point(
    name: str, imported: ModuleType, declaration: ManifestDeclaration
) -> _EntryPoint:
    activate = getattr(imported, "activate", None)
    if not _is_async_callable(activate):
        raise _field_error(name, "activate", "async entry point is required")

    validator = None
    if declaration.settings_validator is not None:
        validator = getattr(imported, declaration.settings_validator, None)
        if not callable(validator):
            raise _field_error(
                name,
                MANIFEST_SETTINGS_VALIDATOR_KEY,
                "names a settings hook the module does not define",
            )
    return _EntryPoint(activate=activate, settings_validator=validator)


def _freeze_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return a recursively read-only snapshot of a manifest mapping."""

    return MappingProxyType(
        {key: _freeze_value(item) for key, item in value.items()}
    )


def _freeze_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {key: _freeze_value(item) for key, item in value.items()}
        )
    if isinstance(value, list):
        return tuple(_freeze_value(item) for item in value)
    if isinstance(value, set):
        return frozenset(_freeze_value(item) for item in value)
    return value


def _remove_imported_module(internal_name: str) -> None:
    """Remove a failed private package import and any relative submodules."""

    prefix = f"{internal_name}."
    for name in tuple(sys.modules):
        if name == internal_name or name.startswith(prefix):
            sys.modules.pop(name, None)


def _is_async_callable(value: Any) -> bool:
    return callable(value) and (
        inspect.iscoroutinefunction(value)
        or inspect.iscoroutinefunction(getattr(value, "__call__", None))
    )


_MAX_REASON_LENGTH = 200


def _sanitised_reason(reason: str) -> str:
    """Bound a borrowed diagnostic so a manifest cannot write a report.

    Contract diagnostics name fields and types, not values, which is what keeps
    a rejected credential out of the report (R7, AC24). The cap is the last
    guard against a pathological manifest string.
    """

    collapsed = " ".join(str(reason).split())
    if len(collapsed) > _MAX_REASON_LENGTH:
        return collapsed[: _MAX_REASON_LENGTH - 1] + "…"
    return collapsed


_OBSERVED_VALUE_SEPARATOR = ", got "


def _value_free_reason(reason: str) -> str:
    """Bound a settings diagnostic and drop the value it observed (R7, AC24).

    A settings diagnostic names the module and the field; it must not quote the
    value, because that value may be the configured credential that was
    rejected. Every diagnostic :mod:`core.contracts` produces places the
    observed value last, after ``", got "``, so cutting at the *first*
    separator keeps the explanation and drops the value whole. The first, not
    the last: a rejected value that itself contains ``", got "`` would
    otherwise leave its own prefix behind.
    """

    return _sanitised_reason(
        str(reason).split(_OBSERVED_VALUE_SEPARATOR, 1)[0]
    )


_EXCEEDED_CLEANUP_DEADLINE = "late handle close exceeded the global shutdown deadline"


@dataclass(eq=False)
class _LateClose:
    """One late handle close the loader owns, bounded by the cleanup deadline.

    ``expired_at`` records the deadline at which
    :meth:`ModuleLoader.settle_late_results` gave up on it, which is the one
    cancellation of ``task`` the close reports as unfinished — with the grace
    that deadline leaves; any other cancellation is the loop tearing it down.
    """

    task: "asyncio.Task[Any]" = field(init=False)
    expired_at: float | None = None


def _hand_result(task: "asyncio.Task[Any]", release: Callable[[Any], None]) -> None:
    """Hand a task's successful late result to its owner; nothing else."""

    if task.cancelled():
        return
    if task.exception() is not None:
        return
    release(task.result())


def _field_diagnostic(module: str, field_name: str, reason: str) -> str:
    return f"module {module!r}: field {field_name!r}: {reason}"


def _field_error(module: str, field_name: str, reason: str) -> ModuleLoadError:
    return ModuleLoadError(_field_diagnostic(module, field_name, reason))


_HOOK_DIAGNOSTIC = re.compile(r"module '(?P<module>[^']*)': field '(?P<field>[^']*)': (?P<reason>.*)\Z", re.DOTALL)
_REDACTED_VALUE = "<redacted>"
#: A configured string this short is a flag or a code, never a credential;
#: redacting it would only cut words out of the module's own explanation.
_MIN_REDACTED_LENGTH = 2


def _configured_values(settings: Any) -> tuple[str, ...]:
    """Every string a module was handed, longest first, for redaction."""

    found: set[str] = set()
    pending: list[Any] = [settings]
    seen: set[int] = set()
    while pending:
        value = pending.pop()
        if isinstance(value, str):
            if len(value) >= _MIN_REDACTED_LENGTH:
                found.add(value)
        elif isinstance(value, Mapping):
            if id(value) in seen:
                continue
            seen.add(id(value))
            pending.extend(value.values())
        elif isinstance(value, (list, tuple)):
            if id(value) in seen:
                continue
            seen.add(id(value))
            pending.extend(value)
    return tuple(sorted(found, key=len, reverse=True))


def _redact_values(text: str, values: Sequence[str]) -> str:
    for value in values:
        if value in text:
            text = text.replace(value, _REDACTED_VALUE)
    return text


_FIELD_PATH = re.compile(r"[A-Za-z_][A-Za-z0-9_.\-\[\]]{0,119}\Z")


def _known_field(field_path: str, values: Sequence[str]) -> bool:
    """Whether *field_path* can stand in the field slot of a diagnostic.

    A field in a hook's diagnostic is module-authored text. It is accepted
    when it is shaped like a setting path — an identifier, dotted or
    indexed, of bounded length — and contains no configured value, so a
    credential can never be smuggled out in the field slot; a field the
    settings lack is still nameable, which is what a "required and missing"
    diagnostic needs.
    """

    if _FIELD_PATH.fullmatch(field_path) is None:
        return False
    return not any(value in field_path for value in values)


def _module_diagnostic(
    module: str,
    entry: Any,
    hook_name: str,
    *,
    values: Sequence[str],
) -> str:
    """One hook-returned diagnostic, held to the shape the report promises.

    The module name is this module's, whatever the entry said; the field is
    the entry's when :func:`_known_field` accepts it and the hook's name
    otherwise; the reason is the entry's, with every configured value
    redacted and its length bounded. An entry that is not text at all is
    reported as a refusal by the hook.

    Redaction is literal: a configured value that is itself an ordinary
    word is still cut out of the reason wherever it appears. A mangled
    explanation is the price of never echoing a value, and the module and
    field the diagnostic names are untouched by it.
    """

    if not isinstance(entry, str) or not entry.strip():
        return _field_diagnostic(module, hook_name, "settings were refused by the module")
    match = _HOOK_DIAGNOSTIC.fullmatch(entry.strip())
    if match is None:
        field_name, reason = hook_name, entry
    else:
        field_name, reason = match.group("field"), match.group("reason")
        if not _known_field(field_name, values):
            field_name = hook_name
    return _field_diagnostic(
        module, field_name, _sanitised_reason(_redact_values(reason, values))
    )
