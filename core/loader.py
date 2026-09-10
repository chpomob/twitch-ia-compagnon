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
import os
import re
import sys
from collections.abc import Mapping, Sequence
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
    HOOK_NAMES,
    MANIFEST_LIFECYCLE_KEY,
    MANIFEST_ROLES_KEY,
    ROLE_INPUT,
    ROLES,
)
from .runtime import SUPPORTED_RUNTIME_APIS


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
    """Raised when module discovery, validation, import, or activation fails."""


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

_V2_ONLY_KEYS: tuple[str, ...] = (
    MANIFEST_RUNTIME_API_KEY,
    MANIFEST_SETTINGS_SCHEMA_KEY,
    MANIFEST_SETTINGS_VALIDATOR_KEY,
    MANIFEST_ACTIONS_KEY,
    MANIFEST_TRIGGERS_KEY,
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
    ) -> None:
        """Build a loader over *modules_directory*.

        *context* is the versioned :class:`~core.runtime.RuntimeContext` a v2
        activation receives; without one only v1 modules can be activated, and
        an enabled v2 module is refused rather than handed a bus it does not
        expect. *environ* is the mapping ``${NAME}`` module settings resolve
        against, injected so a test never touches the process environment.
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

    async def activate_enabled(
        self, config: Mapping[str, Any]
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

        declarations = {name: _declaration(discovered[name]) for name in enabled}

        # R4: a v1 module has no ``start_inputs`` phase, so a v1 producer could
        # only open its source inside ``activate``, ahead of the barrier. It is
        # refused here, by declaration, before any module is activated.
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

        # Secrets belong to the enabled set only (R7, AC25). A disabled module's
        # ``${NAME}`` reference is never looked up, so an unresolvable one does
        # not stop an application that does not run that module.
        resolved = {
            name: self._resolve_secrets(name, settings[name]) for name in enabled
        }

        # Every enabled module validates its own settings, all of them before
        # any of them is activated (R7).
        for name in enabled:
            await self._validate_settings(
                name, declarations[name], entry_points[name], resolved[name]
            )

        # Declaring records; it never grants (R7, R5).
        for name in enabled:
            self._register_declarations(name, declarations[name])

        for name in enabled:
            declaration = declarations[name]
            activate = entry_points[name].activate
            handle = await self._activate(name, declaration, activate, resolved[name])

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

    async def _activate(
        self,
        name: str,
        declaration: ManifestDeclaration,
        activate: Any,
        settings: Mapping[str, Any],
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

        try:
            pending_handle = activate(first_argument, settings, self.catalog)
            if not inspect.isawaitable(pending_handle):
                raise TypeError("activate must be asynchronous")
            return await pending_handle
        except Exception:
            raise ModuleLoadError(
                f"module {name!r}: field 'activate': activation failed"
            ) from None

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
    ) -> None:
        """Run this module's own settings validation, before any activation.

        The declared schema first, then the declared hook. Diagnostics name the
        module and the field and never interpolate a value, so a rejected
        credential is not echoed into the report (R7, AC24).
        """

        if declaration.settings_schema is not None:
            try:
                validate_against_schema(
                    settings, declaration.settings_schema, label="settings"
                )
            except ContractError as exc:
                raise _field_error(
                    name, exc.field, _value_free_reason(exc.reason)
                ) from None

        validator = entry_point.settings_validator
        if validator is None:
            if declaration.settings_validator is not None:
                raise _field_error(
                    name,
                    MANIFEST_SETTINGS_VALIDATOR_KEY,
                    "names a settings hook that could not be resolved",
                )
            return
        try:
            outcome = validator(settings)
            if inspect.isawaitable(outcome):
                await outcome
        except Exception:
            # Nothing the hook raises reaches the report, whatever its type.
            # Its message and its field are module-authored text, and the hook
            # was handed the settings, so either one may carry the rejected
            # credential back out. The diagnostic is built here instead, from
            # the module name and the declared hook alone (R7, AC24).
            raise _field_error(
                name,
                declaration.settings_validator or MANIFEST_SETTINGS_VALIDATOR_KEY,
                "settings were refused by the module",
            ) from None

    def _register_declarations(
        self, name: str, declaration: ManifestDeclaration
    ) -> None:
        """Record declared actions and triggers. Recording is not granting.

        A declared action lands in the registry's *discovered* view and stays
        there until some module binds a provider to it and some rule
        authorizes it. Neither the declaration, the module's capabilities, its
        produced events nor a ``read`` nature moves it into the *authorized*
        view (R7, R5).
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
        try:
            registry.register(name, declaration.triggers)
        except Exception as exc:
            raise _field_error(
                name, MANIFEST_TRIGGERS_KEY, _sanitised_reason(str(exc))
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

    return ManifestDeclaration(
        manifest=manifest,
        manifest_version=manifest_version,
        runtime_api=_manifest_runtime_api(name, manifest),
        roles=roles,
        settings_schema=_manifest_settings_schema(name, manifest),
        settings_validator=_manifest_settings_validator(name, manifest),
        actions=_manifest_actions(name, manifest),
        triggers=_manifest_triggers(name, manifest),
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
            )
        except ModuleLoadError:
            raise
        except (ContractError, TypeError, ValueError) as exc:
            raise _field_error(name, field_name, _sanitised_reason(str(exc))) from None
        if spec.name in seen:
            raise _field_error(
                name, field_name, f"declares action {spec.name!r} twice"
            )
        seen.add(spec.name)
        specs.append(spec)
    return tuple(specs)


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


def _field_error(module: str, field_name: str, reason: str) -> ModuleLoadError:
    return ModuleLoadError(f"module {module!r}: field {field_name!r}: {reason}")
