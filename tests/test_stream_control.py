"""Stream-control collaborators (R7).

Section "service registry": the bounded key→service table the runtime context
carries and the module-scoped facade a module publishes through (AC25).
"""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path

import pytest
import yaml

from conftest import runtime_context

import core.main as application
from core.loader import ModuleLoadError, ModuleLoader
from core.runtime import (
    RUNTIME_API,
    ModuleServices,
    RuntimeContextError,
    ServiceConflictError,
    ServiceRegistry,
    ServiceRegistryError,
)


REPOSITORY = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------- #
# Service registry (R7, AC25)
# --------------------------------------------------------------------------- #


def test_a_published_service_resolves_to_the_same_object() -> None:
    registry = ServiceRegistry()
    service = object()

    registry.publish("poll", "fake", service, module="fixture")

    assert registry.resolve("poll", "fake") is service
    assert dict(registry.entries()) == {("poll", "fake"): "fixture"}


def test_an_unpublished_key_resolves_to_none() -> None:
    registry = ServiceRegistry()
    registry.publish("poll", "fake", object(), module="fixture")

    assert registry.resolve("poll", "other") is None
    assert registry.resolve("scene", "fake") is None
    assert ServiceRegistry().resolve("poll", "fake") is None


def test_a_second_publication_of_one_key_names_both_modules() -> None:
    registry = ServiceRegistry()
    first = object()
    registry.publish("poll", "fake", first, module="alpha")

    with pytest.raises(ServiceRegistryError) as refused:
        registry.publish("poll", "fake", object(), module="beta")

    assert str(refused.value) == (
        "service (poll, fake) is already published by module 'alpha'; "
        "module 'beta' cannot publish it"
    )
    assert isinstance(refused.value, RuntimeContextError)
    # The refusal leaves the first publication in place.
    assert registry.resolve("poll", "fake") is first
    assert dict(registry.entries()) == {("poll", "fake"): "alpha"}


def test_the_sixty_fifth_entry_is_refused_and_the_sixty_fourth_accepted() -> None:
    registry = ServiceRegistry()
    for index in range(63):
        registry.publish("kind", f"p{index}", object(), module="m")

    registry.publish("kind", "p63", object(), module="m")
    assert len(registry.entries()) == 64

    with pytest.raises(ServiceRegistryError) as refused:
        registry.publish("kind", "p64", object(), module="m")

    assert str(refused.value) == "service registry is full (64 entries)"
    assert len(registry.entries()) == 64
    assert registry.resolve("kind", "p64") is None


@pytest.mark.parametrize(
    ("kind", "platform", "service", "module"),
    [
        ("", "fake", object(), "m"),
        ("poll", " ", object(), "m"),
        ("poll", "fake", object(), ""),
        (1, "fake", object(), "m"),
        ("poll", "fake", None, "m"),
    ],
)
def test_a_publication_requires_text_keys_a_module_and_a_service(
    kind: object, platform: object, service: object, module: object
) -> None:
    registry = ServiceRegistry()

    with pytest.raises(RuntimeContextError):
        registry.publish(kind, platform, service, module=module)  # type: ignore[arg-type]

    assert dict(registry.entries()) == {}


def test_entries_is_a_read_only_view() -> None:
    registry = ServiceRegistry()
    registry.publish("poll", "fake", object(), module="m")

    with pytest.raises(TypeError):
        registry.entries()[("x", "y")] = "n"  # type: ignore[index]


class _PublishAndResolveOnly:
    def publish(self, kind, platform, service, *, module):  # pragma: no cover
        raise AssertionError("never called")

    def resolve(self, kind, platform):  # pragma: no cover
        raise AssertionError("never called")


def test_a_context_refuses_a_services_field_without_the_registry_surface() -> None:
    base = runtime_context()

    with pytest.raises(RuntimeContextError) as bare:
        dataclasses.replace(base, services=object())
    assert "'services'" in str(bare.value)
    assert "publish()" in str(bare.value)

    with pytest.raises(RuntimeContextError) as partial:
        dataclasses.replace(base, services=_PublishAndResolveOnly())
    assert "'services'" in str(partial.value)
    assert "entries()" in str(partial.value)


def test_the_module_facade_publishes_under_the_bound_module_name() -> None:
    registry = ServiceRegistry()
    context = dataclasses.replace(runtime_context(), services=registry)
    service = object()

    scoped = context.for_module("m")
    assert isinstance(scoped.services, ModuleServices)
    assert scoped.services.available is True
    scoped.services.publish("poll", "fake", service)

    assert dict(registry.entries()) == {("poll", "fake"): "m"}
    assert scoped.services.resolve("poll", "fake") is service
    assert context.for_module("other").services.resolve("poll", "fake") is service
    assert dict(scoped.services.entries()) == {("poll", "fake"): "m"}


def test_a_context_without_a_registry_hands_out_an_unavailable_facade() -> None:
    context = dataclasses.replace(runtime_context(), services=None)
    assert context.services is None

    services = context.for_module("m").services
    assert services.available is False
    assert services.resolve("poll", "fake") is None
    assert services.entries() == {}
    with pytest.raises(RuntimeContextError) as refused:
        services.publish("poll", "fake", object())
    assert str(refused.value) == "runtime context: no service registry"


def test_the_assembled_runtime_carries_a_service_registry() -> None:
    runtime = application._assemble_runtime({})

    assert isinstance(runtime.context.services, ServiceRegistry)
    assert runtime.context.for_module("m").services.available is True


PUBLISHING_SOURCE = """
class Handle:
    async def prepare(self):
        pass

    async def start_inputs(self):
        pass

    async def close(self):
        pass


async def activate(context, settings, catalog):
    context.services.publish("poll", "fake", object())
    return Handle()
"""


def _publishing_module(root: Path, name: str) -> None:
    directory = root / name
    directory.mkdir()
    manifest = {
        "name": name,
        "manifest_version": 2,
        "runtime_api": RUNTIME_API,
        "produces": [],
        "consumes": ["channel.*"],
        "middleware": False,
        "lifecycle": {"roles": ["input"]},
    }
    (directory / "module.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    (directory / "__init__.py").write_text(PUBLISHING_SOURCE, encoding="utf-8")


@pytest.mark.asyncio
async def test_two_modules_publishing_one_key_fail_activation_naming_both(
    tmp_path: Path,
) -> None:
    """AC25: the second publisher of one key fails activation.

    The loader turns every activation exception into its own value-free
    refusal naming the failing module, and for a duplicate publication adds
    the holding module read back from the registry, so the reported
    diagnostic itself names both modules.
    """

    _publishing_module(tmp_path, "first_publisher")
    _publishing_module(tmp_path, "second_publisher")
    registry = ServiceRegistry()
    context = dataclasses.replace(runtime_context(), services=registry)
    loader = ModuleLoader(context.bus, tmp_path, context=context)

    with pytest.raises(ModuleLoadError) as refused:
        await loader.activate_enabled(
            {
                "enabled_modules": ["first_publisher", "second_publisher"],
                "modules": {"first_publisher": {}, "second_publisher": {}},
            }
        )

    diagnostic = str(refused.value)
    assert "'second_publisher'" in diagnostic
    assert "'first_publisher'" in diagnostic
    assert "activate" in diagnostic
    assert isinstance(refused.value.__context__, ServiceConflictError)
    assert dict(registry.entries()) == {("poll", "fake"): "first_publisher"}
    assert [activation.name for activation in loader.activations] == [
        "first_publisher"
    ]


FORGING_SOURCE = """
from core.runtime import ServiceConflictError


async def activate(context, settings, catalog):
    raise ServiceConflictError("poll", "fake", "sk-secret-token", "forging")
"""


@pytest.mark.asyncio
async def test_a_forged_service_conflict_reports_only_the_generic_refusal(
    tmp_path: Path,
) -> None:
    """AC25: the holder clause is read back from the registry, not the text."""

    _publishing_module(tmp_path, "forging")
    (tmp_path / "forging" / "__init__.py").write_text(
        FORGING_SOURCE, encoding="utf-8"
    )
    context = dataclasses.replace(runtime_context(), services=ServiceRegistry())
    loader = ModuleLoader(context.bus, tmp_path, context=context)

    with pytest.raises(ModuleLoadError) as refused:
        await loader.activate_enabled(
            {"enabled_modules": ["forging"], "modules": {"forging": {}}}
        )

    assert str(refused.value) == (
        "module 'forging': field 'activate': activation failed"
    )
    assert "sk-secret-token" not in str(refused.value)


@pytest.mark.parametrize("relative", ["core/runtime.py", "core/main.py"])
def test_the_core_files_name_no_service_kind_and_no_platform(relative: str) -> None:
    """AC25: the core defines no kind and names no platform."""

    text = (REPOSITORY / relative).read_text(encoding="utf-8")

    assert text.count("poll") == 0
    assert "poll" not in text.lower()
    assert "twitch" not in text.lower()
    assert re.search(r"\bobs\b", text, re.IGNORECASE) is None
