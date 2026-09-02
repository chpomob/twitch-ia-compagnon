from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
import yaml

from core.loader import ModuleLoadError, ModuleLoader


MODULE_SOURCE = """
class Handle:
    def __init__(self, settings):
        self.settings = settings

    async def close(self):
        self.settings["closes"].append(self.settings["label"])


async def activate(bus, settings, catalog):
    settings["calls"].append((bus, settings, catalog))
    return Handle(settings)
"""


def make_module(
    root: Path,
    directory_name: str,
    *,
    manifest_name: str | None = None,
    manifest: dict | None = None,
    source: str = MODULE_SOURCE,
) -> Path:
    directory = root / directory_name
    directory.mkdir()
    data = manifest or {
        "name": manifest_name or directory_name,
        "produces": ["channel.chat.message"],
        "consumes": ["channel.*"],
        "middleware": False,
    }
    (directory / "module.yaml").write_text(
        yaml.safe_dump(data), encoding="utf-8"
    )
    (directory / "__init__.py").write_text(source, encoding="utf-8")
    return directory


@pytest.mark.asyncio
async def test_discovers_all_modules_and_activates_only_enabled(tmp_path: Path) -> None:
    make_module(tmp_path, "enabled")
    make_module(
        tmp_path,
        "disabled",
        source="async def activate(*args):\n    raise AssertionError('disabled')\n",
    )
    bus = object()
    enabled_settings = {"label": "enabled", "calls": [], "closes": []}
    disabled_settings = {"unused": True}
    loader = ModuleLoader(bus, tmp_path)

    activations = await loader.activate_enabled(
        {
            "enabled_modules": ["enabled"],
            "modules": {
                "enabled": enabled_settings,
                "disabled": disabled_settings,
            },
        }
    )

    assert list(loader.discovered) == ["disabled", "enabled"]
    assert set(loader.catalog) == {"disabled", "enabled"}
    assert [activation.name for activation in activations] == ["enabled"]
    assert len(enabled_settings["calls"]) == 1
    received_bus, received_settings, received_catalog = enabled_settings["calls"][0]
    assert received_bus is bus
    assert received_settings is enabled_settings
    assert received_catalog is loader.catalog
    assert set(received_catalog) == {"disabled", "enabled"}


@pytest.mark.asyncio
async def test_activation_close_is_idempotent_even_when_called_concurrently(
    tmp_path: Path,
) -> None:
    make_module(tmp_path, "one")
    settings = {"label": "one", "calls": [], "closes": []}
    activation = (
        await ModuleLoader(object(), tmp_path).load(
            {"enabled_modules": ["one"], "modules": {"one": settings}}
        )
    )[0]

    await asyncio.gather(activation.close(), activation.close(), activation.close())

    assert activation.closed is True
    assert settings["closes"] == ["one"]


def _base_config(*names: str) -> dict:
    return {
        "enabled_modules": list(names),
        "modules": {
            name: {"label": name, "calls": [], "closes": []} for name in names
        },
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case", "field"),
    [
        ("duplicate_name", "name"),
        ("non_list_produces", "produces"),
        ("missing_middleware", "middleware"),
        ("non_bool_middleware", "middleware"),
        ("non_int_order", "order"),
        ("bool_order", "order"),
        ("invalid_pattern", "consumes"),
        ("unknown_enabled", "enabled_modules"),
        ("missing_enabled_settings", "modules"),
        ("unknown_settings", "modules"),
    ],
)
async def test_validation_failure_prevents_every_activation(
    tmp_path: Path, case: str, field: str
) -> None:
    calls: list = []
    source = """
async def activate(bus, settings, catalog):
    settings["calls"].append("activated")
    raise AssertionError("validation should have happened first")
"""
    make_module(tmp_path, "valid", source=source)
    config = {"enabled_modules": ["valid"], "modules": {"valid": {"calls": calls}}}

    if case == "duplicate_name":
        make_module(tmp_path, "duplicate", manifest_name="valid")
    elif case == "non_list_produces":
        make_module(
            tmp_path,
            "broken",
            manifest={
                "name": "broken",
                "produces": "channel.chat.message",
                "consumes": [],
                "middleware": False,
            },
        )
    elif case == "missing_middleware":
        make_module(
            tmp_path,
            "broken",
            manifest={"name": "broken", "produces": [], "consumes": []},
        )
    elif case == "non_bool_middleware":
        make_module(
            tmp_path,
            "broken",
            manifest={
                "name": "broken",
                "produces": [],
                "consumes": [],
                "middleware": "false",
            },
        )
    elif case in {"non_int_order", "bool_order"}:
        make_module(
            tmp_path,
            "broken",
            manifest={
                "name": "broken",
                "produces": [],
                "consumes": ["**"],
                "middleware": True,
                "order": "90" if case == "non_int_order" else True,
            },
        )
    elif case == "invalid_pattern":
        make_module(
            tmp_path,
            "broken",
            manifest={
                "name": "broken",
                "produces": [],
                "consumes": ["channel*"],
                "middleware": False,
            },
        )
    elif case == "unknown_enabled":
        config["enabled_modules"].append("missing")
        config["modules"]["missing"] = {}
    elif case == "missing_enabled_settings":
        config["modules"].pop("valid")
    elif case == "unknown_settings":
        config["modules"]["missing"] = {}

    with pytest.raises(ModuleLoadError) as caught:
        await ModuleLoader(object(), tmp_path).load(config)

    assert field in str(caught.value)
    assert calls == []


@pytest.mark.asyncio
async def test_non_module_directories_are_ignored(tmp_path: Path) -> None:
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / ".git").mkdir()
    make_module(tmp_path, "enabled")
    settings = {"label": "enabled", "calls": [], "closes": []}

    activations = await ModuleLoader(object(), tmp_path).activate_enabled(
        {"enabled_modules": ["enabled"], "modules": {"enabled": settings}}
    )

    assert [activation.name for activation in activations] == ["enabled"]


@pytest.mark.asyncio
async def test_symlinked_module_directory_is_not_discovered(tmp_path: Path) -> None:
    modules_root = tmp_path / "modules"
    modules_root.mkdir()
    outside = tmp_path / "outside"
    make_module(outside.parent, outside.name)
    (modules_root / "linked").symlink_to(outside, target_is_directory=True)

    loader = ModuleLoader(object(), modules_root)
    activations = await loader.activate_enabled(
        {"enabled_modules": [], "modules": {}}
    )

    assert activations == []
    assert loader.discovered == {}


@pytest.mark.asyncio
async def test_entry_point_symlink_cannot_escape_module_directory(
    tmp_path: Path,
) -> None:
    module = make_module(tmp_path, "linked_init")
    external_source = tmp_path / "external.py"
    external_source.write_text(MODULE_SOURCE, encoding="utf-8")
    (module / "__init__.py").unlink()
    (module / "__init__.py").symlink_to(external_source)

    with pytest.raises(ModuleLoadError, match="stay inside"):
        await ModuleLoader(object(), tmp_path).activate_enabled(
            _base_config("linked_init")
        )


@pytest.mark.asyncio
async def test_failed_entry_point_validation_does_not_leak_imports(
    tmp_path: Path,
) -> None:
    module = make_module(
        tmp_path,
        "invalid_entry",
        source="from . import helper\nVALUE = helper.VALUE\n",
    )
    (module / "helper.py").write_text("VALUE = 1\n", encoding="utf-8")
    before = {name for name in sys.modules if name.startswith("_companion_module_")}

    with pytest.raises(ModuleLoadError, match="activate"):
        await ModuleLoader(object(), tmp_path).activate_enabled(
            _base_config("invalid_entry")
        )

    after = {name for name in sys.modules if name.startswith("_companion_module_")}
    assert after == before


@pytest.mark.asyncio
async def test_invalid_close_hook_remains_in_partial_activation_set(
    tmp_path: Path,
) -> None:
    make_module(
        tmp_path,
        "invalid_close",
        source="""
class Handle:
    pass

async def activate(bus, settings, catalog):
    settings["activated"] = True
    return Handle()
""",
    )
    loader = ModuleLoader(object(), tmp_path)
    settings: dict = {}

    with pytest.raises(ModuleLoadError, match="close"):
        await loader.activate_enabled(
            {
                "enabled_modules": ["invalid_close"],
                "modules": {"invalid_close": settings},
            }
        )

    assert settings["activated"] is True
    assert [activation.name for activation in loader.activations] == [
        "invalid_close"
    ]


@pytest.mark.asyncio
async def test_capability_catalog_is_recursively_read_only(tmp_path: Path) -> None:
    make_module(tmp_path, "one")
    loader = ModuleLoader(object(), tmp_path)
    settings = {"label": "one", "calls": [], "closes": []}

    await loader.activate_enabled(
        {"enabled_modules": ["one"], "modules": {"one": settings}}
    )

    with pytest.raises(TypeError):
        loader.catalog["two"] = {}  # type: ignore[index]
    with pytest.raises(TypeError):
        loader.catalog["one"]["name"] = "renamed"  # type: ignore[index]
    assert loader.catalog["one"]["produces"] == ("channel.chat.message",)


@pytest.mark.asyncio
async def test_arbitrary_filesystem_module_needs_no_core_registration(
    tmp_path: Path,
) -> None:
    # Naming the directory after a standard-library package catches accidental
    # package-name imports instead of loading this exact __init__.py file.
    make_module(tmp_path, "json", manifest_name="fourth_party")
    settings = {
        "label": "fourth_party",
        "calls": [],
        "closes": [],
    }

    activations = await ModuleLoader(object(), tmp_path).load(
        {
            "enabled_modules": ["fourth_party"],
            "modules": {"fourth_party": settings},
        }
    )

    assert [activation.name for activation in activations] == ["fourth_party"]
    assert len(settings["calls"]) == 1


@pytest.mark.asyncio
async def test_import_and_activation_errors_are_sanitized(tmp_path: Path) -> None:
    make_module(tmp_path, "broken_import", source="raise RuntimeError('secret-value')")
    with pytest.raises(ModuleLoadError) as imported:
        await ModuleLoader(object(), tmp_path).load(
            _base_config("broken_import")
        )
    assert "broken_import" in str(imported.value)
    assert "__init__.py" in str(imported.value)
    assert "secret-value" not in str(imported.value)

    other_root = tmp_path / "other"
    other_root.mkdir()
    make_module(
        other_root,
        "broken_activation",
        source="async def activate(*args):\n    raise RuntimeError('secret-value')\n",
    )
    with pytest.raises(ModuleLoadError) as activated:
        await ModuleLoader(object(), other_root).load(
            _base_config("broken_activation")
        )
    assert "broken_activation" in str(activated.value)
    assert "activate" in str(activated.value)
    assert "secret-value" not in str(activated.value)
