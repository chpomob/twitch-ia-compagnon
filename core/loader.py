"""Discover, validate, and activate filesystem-defined modules."""

from __future__ import annotations

import asyncio
import importlib.util
import inspect
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType, ModuleType
from typing import Any
from uuid import uuid4

import yaml


class ModuleLoadError(RuntimeError):
    """Raised when module discovery, validation, import, or activation fails."""


@dataclass(frozen=True, slots=True)
class DiscoveredModule:
    """A validated module manifest and its filesystem location."""

    name: str
    directory: Path
    manifest: dict[str, Any]


@dataclass(slots=True)
class ModuleActivation:
    """An activated module with an idempotent asynchronous lifecycle."""

    name: str
    manifest: dict[str, Any]
    handle: Any
    _closed: bool = field(default=False, init=False, repr=False)
    _close_lock: asyncio.Lock = field(
        default_factory=asyncio.Lock, init=False, repr=False
    )

    @property
    def closed(self) -> bool:
        return self._closed

    async def close(self) -> None:
        """Call the module's close hook no more than once."""

        async with self._close_lock:
            if self._closed:
                return
            self._closed = True
            try:
                result = self.handle.close()
                if not inspect.isawaitable(result):
                    raise TypeError("close must be asynchronous")
                await result
            except Exception:
                raise ModuleLoadError(
                    f"module {self.name!r}: field 'close': shutdown failed"
                ) from None


class ModuleLoader:
    """Load modules declared by immediate children of a directory."""

    def __init__(self, bus: Any, modules_directory: str | Path) -> None:
        self.bus = bus
        self.modules_directory = Path(modules_directory).expanduser().resolve()
        self.catalog: Mapping[str, Mapping[str, Any]] = MappingProxyType({})
        self.discovered: dict[str, DiscoveredModule] = {}
        self.activations: list[ModuleActivation] = []

    async def activate_enabled(
        self, config: Mapping[str, Any]
    ) -> list[ModuleActivation]:
        """Discover all modules, validate configuration, then activate enabled ones.

        Every manifest and all module-name references are checked before module
        code is activated. Enabled modules are activated in ``enabled_modules``
        order and receive their original settings mapping.
        """

        discovered = self._discover()
        enabled, settings = self._validate_config(config, discovered)

        # Import every enabled entry point before activation. This keeps import
        # failures from leaving an avoidable partially activated application.
        entry_points = {
            name: self._load_entry_point(discovered[name]) for name in enabled
        }

        self.discovered = discovered
        self.catalog = MappingProxyType(
            {
                name: _freeze_mapping(module.manifest)
                for name, module in discovered.items()
            }
        )
        self.activations = []

        for name in enabled:
            activate = entry_points[name]
            try:
                pending_handle = activate(self.bus, settings[name], self.catalog)
                if not inspect.isawaitable(pending_handle):
                    raise TypeError("activate must be asynchronous")
                handle = await pending_handle
            except Exception:
                raise ModuleLoadError(
                    f"module {name!r}: field 'activate': activation failed"
                ) from None

            # Retain the handle as soon as activation succeeds. If its lifecycle
            # contract is invalid, callers can still attempt reverse-order cleanup
            # of the complete partially activated set.
            activation = ModuleActivation(
                name=name,
                manifest=discovered[name].manifest,
                handle=handle,
            )
            self.activations.append(activation)

            close = None if handle is None else getattr(handle, "close", None)
            if not _is_async_callable(close):
                raise ModuleLoadError(
                    f"module {name!r}: field 'close': async lifecycle hook is required"
                )

        return list(self.activations)

    async def load(self, config: Mapping[str, Any]) -> list[ModuleActivation]:
        """Compatibility alias for :meth:`activate_enabled`."""

        return await self.activate_enabled(config)

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

            validated = _validate_manifest(directory.name, manifest)
            name = validated["name"]
            if name in discovered:
                raise _field_error(name, "name", "must be unique")

            discovered[name] = DiscoveredModule(
                name=name,
                directory=resolved_directory,
                manifest=validated,
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

        for name in enabled:
            if name not in module_settings:
                raise _field_error(
                    name, "modules", "settings are required when enabled"
                )
            if not isinstance(module_settings[name], Mapping):
                raise _field_error(name, "modules", "settings must be a mapping")

        return list(enabled), module_settings

    def _load_entry_point(self, module: DiscoveredModule):
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
            return _validate_entry_point(module.name, imported)
        except Exception:
            _remove_imported_module(internal_name)
            raise


def _validate_manifest(directory_name: str, value: Any) -> dict[str, Any]:
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

    return manifest


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


def _validate_entry_point(name: str, imported: ModuleType):
    activate = getattr(imported, "activate", None)
    if not _is_async_callable(activate):
        raise _field_error(name, "activate", "async entry point is required")
    return activate


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


def _field_error(module: str, field_name: str, reason: str) -> ModuleLoadError:
    return ModuleLoadError(f"module {module!r}: field {field_name!r}: {reason}")


__all__: Sequence[str] = (
    "DiscoveredModule",
    "ModuleActivation",
    "ModuleLoadError",
    "ModuleLoader",
)
