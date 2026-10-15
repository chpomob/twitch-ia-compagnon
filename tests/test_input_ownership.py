"""Phase 5 input ownership: the kernel vocabulary and declaration contract (P1).

R2 (declaration contract), R3 (counter and trace), A4 (families), A5 (the
``cue`` kind), D1 (``chat`` family membership) and R10/AC11 (the kernel
contract file names no vendor and no bridge module).
"""

from __future__ import annotations

import re
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pytest

from core import contracts
from core.contracts import (
    CHAT_FAMILY_KINDS,
    COUNTER_INPUT_OWNERSHIP_REJECTIONS,
    CUE_KIND,
    DIAGNOSTIC_COUNTERS,
    EVENT_KIND_SET,
    EVENT_KINDS,
    INPUT_FAMILIES,
    INPUT_FAMILY_GATED_ACTIONS,
    INPUT_FAMILY_SET,
    INPUT_OWNER_NONE,
    PLATFORM_NOTICE_KINDS,
    REQUIRED_COUNTERS,
    TRACE_EVENT_TYPES,
    TRACE_INPUT_OWNERSHIP_REJECTED,
    ContractError,
    Counters,
    InputFamilyDeclaration,
    input_family_of_event,
    parse_input_families,
)

ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------- #
# Vocabulary
# --------------------------------------------------------------------------- #


def test_cue_is_the_tenth_kind_and_not_a_platform_notice() -> None:
    assert EVENT_KINDS[-1] == CUE_KIND == "cue"
    assert "cue" in EVENT_KIND_SET
    assert "cue" not in PLATFORM_NOTICE_KINDS
    assert len(PLATFORM_NOTICE_KINDS) == 7


def test_family_vocabulary() -> None:
    assert INPUT_FAMILIES == ("chat", "viewers", "cues")
    assert INPUT_FAMILY_SET == frozenset(INPUT_FAMILIES)
    assert INPUT_OWNER_NONE == "none"
    assert CHAT_FAMILY_KINDS == ("message", *PLATFORM_NOTICE_KINDS)
    assert "watch_tick" not in CHAT_FAMILY_KINDS and "cue" not in CHAT_FAMILY_KINDS


def test_gated_actions_map_viewers_to_the_existing_user_read() -> None:
    assert isinstance(INPUT_FAMILY_GATED_ACTIONS, MappingProxyType)
    assert dict(INPUT_FAMILY_GATED_ACTIONS) == {"viewers": "users.read"}
    with pytest.raises(TypeError):
        INPUT_FAMILY_GATED_ACTIONS["chat"] = "x"  # type: ignore[index]


def test_counter_and_trace_names() -> None:
    assert COUNTER_INPUT_OWNERSHIP_REJECTIONS == "input_ownership_rejections"
    assert COUNTER_INPUT_OWNERSHIP_REJECTIONS in DIAGNOSTIC_COUNTERS
    assert COUNTER_INPUT_OWNERSHIP_REJECTIONS not in REQUIRED_COUNTERS
    assert TRACE_INPUT_OWNERSHIP_REJECTED == "input.ownership.rejected"
    assert TRACE_INPUT_OWNERSHIP_REJECTED not in TRACE_EVENT_TYPES


def test_a_fresh_counters_exposes_input_ownership_rejections_at_zero() -> None:
    counters = Counters()
    assert counters.snapshot()[COUNTER_INPUT_OWNERSHIP_REJECTIONS] == 0
    assert counters.increment(COUNTER_INPUT_OWNERSHIP_REJECTIONS) == 1


def test_every_new_name_is_exported() -> None:
    for name in (
        "CHAT_FAMILY_KINDS",
        "COUNTER_INPUT_OWNERSHIP_REJECTIONS",
        "CUE_KIND",
        "INPUT_FAMILIES",
        "INPUT_FAMILY_GATED_ACTIONS",
        "INPUT_FAMILY_SET",
        "INPUT_OWNER_NONE",
        "TRACE_INPUT_OWNERSHIP_REJECTED",
        "InputFamilyDeclaration",
        "input_family_of_event",
        "parse_input_families",
    ):
        assert name in contracts.__all__, name
        assert hasattr(contracts, name), name


# --------------------------------------------------------------------------- #
# input_family_of_event (A4, D1)
# --------------------------------------------------------------------------- #


def _event(payload: Any, event_type: str = "channel.chat.message") -> dict[str, Any]:
    return {"type": event_type, "payload": payload, "metadata": {}}


def test_a_message_without_kind_is_chat() -> None:
    assert input_family_of_event(_event({"platform": "alpha", "text": "hi"})) == (
        "chat",
        "alpha",
    )


@pytest.mark.parametrize("kind", ["message", *PLATFORM_NOTICE_KINDS])
def test_message_and_each_notice_kind_is_chat(kind: str) -> None:
    assert input_family_of_event(_event({"platform": "beta", "kind": kind})) == ("chat", "beta")


def test_cue_is_the_cues_family() -> None:
    assert input_family_of_event(_event({"platform": "gamma", "kind": "cue"})) == (
        "cues",
        "gamma",
    )


@pytest.mark.parametrize(
    "event",
    [
        _event({"platform": "alpha", "kind": "watch_tick"}),
        _event({"platform": "alpha", "kind": "unknown"}),
        _event({"platform": "alpha", "kind": 3}),
        _event({"platform": "alpha"}, event_type="channel.chat.sent"),
        _event({"platform": "alpha"}, event_type="users.seen"),
        _event({"kind": "message"}),
        _event({"platform": "", "kind": "message"}),
        _event({"platform": 7, "kind": "cue"}),
        _event("not a mapping"),
        {"payload": {"platform": "alpha"}},
        "not an event",
    ],
)
def test_events_outside_every_family_return_none(event: Any) -> None:
    assert input_family_of_event(event) is None


# --------------------------------------------------------------------------- #
# parse_input_families (R2)
# --------------------------------------------------------------------------- #


SHIPPED_SHAPES = [
    # a native chat input (one per platform module)
    [{"family": "chat", "platforms": ["alpha"], "default": True}],
    # the native viewer read
    [{"family": "viewers", "platforms": ["*"], "default": True}],
    # the bridge: chat and viewers opt-in, cues by default
    [
        {"family": "chat", "platforms": ["alpha", "beta", "gamma"], "default": False},
        {"family": "viewers", "platforms": ["alpha", "beta", "gamma"], "default": False},
        {"family": "cues", "platforms": ["*"], "default": True},
    ],
    # an explicit empty declaration (a declared publisher owning nothing)
    [],
]


@pytest.mark.parametrize("value", SHIPPED_SHAPES)
def test_parse_accepts_the_shipped_shapes(value: list[dict[str, Any]]) -> None:
    parsed = parse_input_families(value, "input_families")
    assert parsed == tuple(
        InputFamilyDeclaration(entry["family"], tuple(entry["platforms"]), entry["default"])
        for entry in value
    )


def test_parsed_declaration_is_frozen() -> None:
    (declaration,) = parse_input_families(
        [{"family": "viewers", "platforms": ["*"], "default": True}], "input_families"
    )
    assert declaration.platforms == ("*",)
    with pytest.raises(AttributeError):
        declaration.default = False  # type: ignore[misc]


def _entry(**overrides: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {"family": "chat", "platforms": ["alpha"], "default": True}
    entry.update(overrides)
    return entry


@pytest.mark.parametrize(
    ("value", "field"),
    [
        ({"family": "chat"}, "m.input_families"),
        ("chat", "m.input_families"),
        (None, "m.input_families"),
        (["chat"], "m.input_families[0]"),
        ([{"family": "chat", "platforms": ["alpha"]}], "m.input_families[0]"),
        ([{**_entry(), "extra": 1}], "m.input_families[0]"),
        ([_entry(family="presence")], "m.input_families[0].family"),
        ([_entry(family=1)], "m.input_families[0].family"),
        ([_entry(platforms=[])], "m.input_families[0].platforms"),
        ([_entry(platforms="alpha")], "m.input_families[0].platforms"),
        ([_entry(platforms=["alpha", 2])], "m.input_families[0].platforms[1]"),
        ([_entry(platforms=[" "])], "m.input_families[0].platforms[0]"),
        ([_entry(platforms=["*", "alpha"])], "m.input_families[0].platforms"),
        ([_entry(platforms=["alpha", "*"])], "m.input_families[0].platforms"),
        ([_entry(platforms=["alpha", "alpha"])], "m.input_families[0].platforms[1]"),
        (
            [_entry(platforms=["alpha"]), _entry(platforms=["beta", "alpha"], default=False)],
            "m.input_families[1].platforms[1]",
        ),
        ([_entry(default="yes")], "m.input_families[0].default"),
        ([_entry(default=1)], "m.input_families[0].default"),
        ([_entry(default=None)], "m.input_families[0].default"),
    ],
)
def test_parse_refuses_malformed_values_naming_the_field(value: Any, field: str) -> None:
    with pytest.raises(ContractError) as caught:
        parse_input_families(value, "m.input_families")
    assert caught.value.field == field


def test_same_platform_in_two_families_is_not_a_duplicate() -> None:
    parsed = parse_input_families(
        [_entry(), _entry(family="cues", default=False)], "input_families"
    )
    assert [d.family for d in parsed] == ["chat", "cues"]


# --------------------------------------------------------------------------- #
# R10 / AC11 (partial): the kernel contract file is vendor-free
# --------------------------------------------------------------------------- #

_VENDOR_OR_BRIDGE = re.compile(r"streamer[._\- ]?bot", re.IGNORECASE)


def test_contracts_names_no_vendor_and_no_bridge_module() -> None:
    text = (ROOT / "core" / "contracts.py").read_text(encoding="utf-8")
    assert _VENDOR_OR_BRIDGE.findall(text) == []
    assert _VENDOR_OR_BRIDGE.search("modules/streamerbot")
    assert _VENDOR_OR_BRIDGE.search("Streamer.bot")


# --------------------------------------------------------------------------- #
# P2: the loader resolves ownership before any preparation (R2, D2, D3, D7)
# --------------------------------------------------------------------------- #

import asyncio  # noqa: E402
import socket  # noqa: E402

import yaml  # noqa: E402

from core.actions import ActionRegistry, AuthorizationPolicy  # noqa: E402
from core.bus import EventBus  # noqa: E402
from core.lifecycle import SupervisedTasks  # noqa: E402
from core.loader import (  # noqa: E402
    InputOwnership,
    ManifestDeclaration,
    ModuleLoader,
    ModuleLoadError,
    OwnedInputs,
    _validate_manifest,
    resolve_input_ownership,
)
from core.runtime import RUNTIME_API, RuntimeContext, Supervision  # noqa: E402

#: Every call an ownership test module receives, by module name.
_SPY_SOURCE = """
class Handle:
    def __init__(self, settings):
        self.settings = settings

    async def prepare(self):
        self.settings["calls"].append("prepare")

    async def close(self):
        pass


def validate_settings(settings):
    settings["calls"].append("validate")


async def activate(context, settings, catalog):
    settings["calls"].append("activate")
    return Handle(settings)
"""

_AWARE_SOURCE = """
class Handle:
    async def close(self):
        pass


def validate_settings(settings, *, owned_inputs=None):
    settings["calls"].append(("validate", owned_inputs))


async def activate(context, settings, catalog):
    settings["calls"].append("activate")
    return Handle()
"""

_PLAIN_HOOK_SOURCE = """
class Handle:
    async def close(self):
        pass


def validate_settings(*args):
    args[0]["calls"].append(("validate", len(args)))


async def activate(context, settings, catalog):
    return Handle()
"""


def _families(*entries: tuple[str, list[str], bool]) -> list[dict[str, Any]]:
    return [
        {"family": family, "platforms": platforms, "default": default}
        for family, platforms, default in entries
    ]


def _manifest(
    name: str, families: list[dict[str, Any]] | None, *, version: int | None = 2
) -> dict[str, Any]:
    manifest: dict[str, Any] = {
        "name": name,
        "produces": [],
        "consumes": [],
        "middleware": False,
    }
    if version is not None:
        manifest["manifest_version"] = version
        manifest["runtime_api"] = RUNTIME_API
        manifest["settings_validator"] = "validate_settings"
    if families is not None:
        manifest["input_families"] = families
    return manifest


def _make(
    root: Path,
    name: str,
    families: list[dict[str, Any]] | None,
    source: str = _SPY_SOURCE,
) -> None:
    directory = root / name
    directory.mkdir()
    (directory / "module.yaml").write_text(
        yaml.safe_dump(_manifest(name, families)), encoding="utf-8"
    )
    (directory / "__init__.py").write_text(source, encoding="utf-8")


def _context(bus: EventBus) -> RuntimeContext:
    return RuntimeContext(
        bus=bus,
        actions=ActionRegistry(authorization=AuthorizationPolicy()),
        supervision=Supervision(bus),
        tasks=SupervisedTasks(),
    )


def _loader(root: Path, environ: dict[str, str] | None = None) -> ModuleLoader:
    bus = EventBus()
    return ModuleLoader(bus, root, context=_context(bus), environ=environ or {})


def _config(
    names: list[str], inputs: Any = None
) -> tuple[dict[str, Any], dict[str, list[Any]]]:
    settings = {name: {"calls": []} for name in names}
    config: dict[str, Any] = {"enabled_modules": names, "modules": settings}
    if inputs is not None:
        config["inputs"] = inputs
    return config, {name: settings[name]["calls"] for name in names}


def _decl(families: list[dict[str, Any]] | None, name: str = "m") -> ManifestDeclaration:
    return _validate_manifest(name, _manifest(name, families))


def _resolve(
    modules: dict[str, list[dict[str, Any]] | None], inputs: Any = None
) -> InputOwnership:
    return resolve_input_ownership(
        list(modules),
        {name: _decl(families, name) for name, families in modules.items()},
        inputs,
    )


def _refusals(modules: dict[str, Any], inputs: Any) -> list[str]:
    with pytest.raises(ModuleLoadError) as caught:
        _resolve(modules, inputs)
    return list(caught.value.diagnostics)


NATIVE = _families(("chat", ["x"], True))
BRIDGE = _families(
    ("chat", ["x", "y"], False),
    ("viewers", ["x", "y"], False),
    ("cues", ["*"], True),
)


# -- manifest key ------------------------------------------------------------ #


def test_the_manifest_key_is_parsed_and_absent_is_distinct_from_empty() -> None:
    absent = _decl(None)
    empty = _decl([])
    declared = _decl(NATIVE)
    assert (absent.input_families, absent.declares_input_families) == ((), False)
    assert (empty.input_families, empty.declares_input_families) == ((), True)
    assert declared.input_families == (InputFamilyDeclaration("chat", ("x",), True),)
    assert declared.declares_input_families


def test_a_malformed_manifest_declaration_is_refused_naming_the_field() -> None:
    with pytest.raises(ModuleLoadError, match=r"module 'm': field 'input_families\[0\]\.family'"):
        _decl(_families(("weather", ["x"], True)))


def test_a_v1_manifest_declaring_the_key_is_refused() -> None:
    """D3: a v1 module is always an undeclared publisher."""

    with pytest.raises(ModuleLoadError, match="module 'm': field 'input_families'"):
        _validate_manifest("m", _manifest("m", NATIVE, version=None))
    assert not _validate_manifest("m", _manifest("m", None, version=None)).declares_input_families


# -- AC7: ambiguity refused before any effect -------------------------------- #


@pytest.mark.asyncio
async def test_ac7_two_default_declarers_are_refused_before_any_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _make(tmp_path, "a", NATIVE)
    _make(tmp_path, "b", NATIVE)
    attempts: list[Any] = []

    def refuse(*args: Any, **kwargs: Any) -> None:
        attempts.append(args)
        raise AssertionError("socket created")

    monkeypatch.setattr(socket.socket, "__init__", refuse)
    for name in ("socketpair", "fromfd", "create_connection", "create_server"):
        monkeypatch.setattr(socket, name, refuse)
    config, calls = _config(["a", "b"])
    loader = _loader(tmp_path)

    with pytest.raises(ModuleLoadError) as caught:
        await loader.activate_enabled(config)

    assert list(caught.value.diagnostics) == [
        "inputs: family 'chat': platform 'x': owned by default by both 'a' and 'b'; "
        "set inputs.chat.x"
    ]
    # No setting validated, no module activated or prepared, no socket.
    assert calls == {"a": [], "b": []}
    assert loader.activations == []
    assert attempts == []


@pytest.mark.asyncio
async def test_an_explicit_entry_decides_the_ambiguity_and_is_stored(tmp_path: Path) -> None:
    _make(tmp_path, "a", NATIVE)
    _make(tmp_path, "b", NATIVE)
    config, calls = _config(["a", "b"], {"chat": {"x": "b"}})
    loader = _loader(tmp_path)
    await loader.activate_enabled(config)
    assert loader.input_ownership is not None
    assert loader.input_ownership.owner("chat", "x") == "b"
    assert calls == {"a": ["validate", "activate"], "b": ["validate", "activate"]}


def test_a_wildcard_ambiguity_is_reported_once_on_the_wildcard_row() -> None:
    star = _families(("cues", ["*"], True))
    refusals = _refusals({"a": star, "b": star, "c": _families(("cues", ["x"], False))}, None)
    assert refusals == [
        "inputs: family 'cues': platform '*': owned by default by both 'a' and 'b'; "
        "set inputs.cues.*"
    ]
    assert _resolve({"a": star, "b": star}, {"cues": {"*": "a"}}).owner("cues", "x") == "a"


# -- AC8: invalid explicit owners -------------------------------------------- #


def test_ac8_an_owner_list_of_two_is_refused_naming_both() -> None:
    refusals = _refusals(
        {"native": NATIVE, "bridge": BRIDGE}, {"chat": {"x": ["native", "bridge"]}}
    )
    assert len(refusals) == 1
    assert "family 'chat'" in refusals[0] and "platform 'x'" in refusals[0]
    assert "'native'" in refusals[0] and "'bridge'" in refusals[0]


def test_ac8_an_owner_that_is_not_enabled_is_refused() -> None:
    refusals = _refusals({"native": NATIVE}, {"chat": {"x": "bridge"}})
    assert refusals == ["inputs: family 'chat': platform 'x': owner 'bridge' is not enabled"]


def test_ac8_an_owner_that_does_not_declare_the_platform_is_refused() -> None:
    refusals = _refusals({"native": NATIVE, "bridge": BRIDGE}, {"chat": {"z": "bridge"}})
    assert refusals == [
        "inputs: family 'chat': platform 'z': owner 'bridge' does not declare "
        "family 'chat' for platform 'z'"
    ]


def test_ac8_the_wildcard_key_needs_a_wildcard_declarer() -> None:
    refusals = _refusals({"native": NATIVE, "bridge": BRIDGE}, {"chat": {"*": "bridge"}})
    assert len(refusals) == 1
    assert "platform '*'" in refusals[0] and "'bridge'" in refusals[0]


def test_ac8_a_family_outside_the_vocabulary_names_family_platform_and_value() -> None:
    refusals = _refusals({"native": NATIVE}, {"weather": {"x": "native"}})
    assert len(refusals) == 1
    assert "family 'weather'" in refusals[0]
    assert "platform 'x'" in refusals[0]
    assert "'native'" in refusals[0]


@pytest.mark.parametrize(
    "inputs",
    [
        ["chat"],
        {"chat": "native"},
        {"chat": {3: "native"}},
        {"chat": {"x": 3}},
        {"chat": {"x": []}},
        {7: {"x": "native"}},
    ],
)
def test_a_malformed_inputs_entry_is_refused(inputs: Any) -> None:
    assert len(_refusals({"native": NATIVE}, inputs)) == 1


def test_every_refusal_is_collected_into_one_error() -> None:
    refusals = _refusals(
        {"a": NATIVE, "b": NATIVE},
        {"weather": {"x": "a"}, "viewers": {"x": "ghost"}},
    )
    assert len(refusals) == 3


def test_an_owner_value_matching_a_secret_is_never_quoted() -> None:
    with pytest.raises(ModuleLoadError) as caught:
        resolve_input_ownership(
            ["native"],
            {"native": _decl(NATIVE, "native")},
            {"chat": {"x": "hunter2-token"}, "viewers": {"x": "two words!"}},
            redacted=frozenset({"hunter2-token"}),
        )
    text = "\n".join(caught.value.diagnostics)
    assert "hunter2" not in text and "two words" not in text
    assert text.count("<redacted>") == 2


@pytest.mark.asyncio
async def test_the_loader_never_quotes_an_environment_value(tmp_path: Path) -> None:
    _make(tmp_path, "native", NATIVE)
    loader = _loader(tmp_path, {"TOKEN": "s3cr3t-value"})
    config, _ = _config(["native"], {"chat": {"x": "s3cr3t-value"}})
    with pytest.raises(ModuleLoadError) as caught:
        await loader.activate_enabled(config)
    assert "s3cr3t" not in "\n".join(caught.value.diagnostics)


@pytest.mark.asyncio
async def test_the_loader_never_quotes_a_literal_credential(tmp_path: Path) -> None:
    """An owner equal to a configured credential literal is not quoted, though
    the diagnostic is raised before credential redaction runs."""

    _make(tmp_path, "native", NATIVE)
    manifest = _manifest("native", NATIVE)
    manifest["settings_schema"] = {
        "type": "object",
        "properties": {"calls": {"type": "array"}, "token": {"type": "string"}},
    }
    manifest["credentials"] = ["token"]
    (tmp_path / "native" / "module.yaml").write_text(
        yaml.safe_dump(manifest), encoding="utf-8"
    )
    loader = _loader(tmp_path)
    config, _ = _config(["native"], {"chat": {"x": "literal-token"}})
    config["modules"]["native"]["token"] = "literal-token"
    with pytest.raises(ModuleLoadError) as caught:
        await loader.activate_enabled(config)
    text = "\n".join(caught.value.diagnostics)
    assert "literal-token" not in text
    assert "<redacted>" in text


# -- D2: explicit and default precedence -------------------------------------- #


def test_ac6_shape_default_owners_and_no_chat_pair_for_the_bridge() -> None:
    ownership = _resolve({"native": NATIVE, "bridge": BRIDGE})
    assert (ownership.owner("chat", "x"), ownership.origin("chat", "x")) == ("native", "default")
    assert ownership.owner("chat", "y") is None
    assert ownership.origin("chat", "y") == "unowned"
    assert ownership.owner("cues", "*") == "bridge"
    assert ownership.owner("cues", "x") == "bridge"
    assert ownership.view_for("bridge").platforms("chat") == ()
    assert ownership.candidates("chat", "x") == ("bridge", "native")


def test_ac9_explicit_beats_default() -> None:
    ownership = _resolve({"native": NATIVE, "bridge": BRIDGE}, {"chat": {"x": "bridge"}})
    assert (ownership.owner("chat", "x"), ownership.origin("chat", "x")) == ("bridge", "explicit")
    assert ownership.view_for("native").pairs == frozenset()
    assert ("chat", "x") in ownership.view_for("bridge").pairs


def test_the_wildcard_key_applies_to_undeclared_platforms() -> None:
    star = _families(("viewers", ["*"], False))
    ownership = _resolve({"reader": star}, {"viewers": {"*": "reader"}})
    assert ownership.owner("viewers", "zz") == "reader"
    assert ownership.origin("viewers", "zz") == "explicit"


def test_the_explicit_wildcard_key_beats_a_concrete_default() -> None:
    star = _families(("chat", ["*"], False))
    ownership = _resolve({"native": NATIVE, "all": star}, {"chat": {"*": "all"}})
    assert ownership.owner("chat", "x") == "all"


def test_a_concrete_declarer_beats_a_wildcard_declarer() -> None:
    star = _families(("viewers", ["*"], True))
    concrete = _families(("viewers", ["x"], True))
    ownership = _resolve({"wide": star, "narrow": concrete})
    assert ownership.owner("viewers", "x") == "narrow"
    assert ownership.owner("viewers", "y") == "wide"
    assert ownership.origin("viewers", "x") == "default"


def test_a_wildcard_only_declarer_owns_by_fallback() -> None:
    ownership = _resolve({"wide": _families(("viewers", ["*"], True))})
    assert ownership.owner("viewers", "x") == "wide"
    assert ownership.view_for("wide").owns("viewers", "x")


def test_ac9_none_leaves_the_pair_unowned() -> None:
    ownership = _resolve({"native": NATIVE, "bridge": BRIDGE}, {"chat": {"x": "none"}})
    assert ownership.owner("chat", "x") is None
    assert ownership.origin("chat", "x") == "explicit"
    assert ownership.is_set_none("chat", "x")
    assert not ownership.is_set_none("chat", "y")
    cues_off = _resolve({"bridge": BRIDGE}, {"cues": {"*": "none"}})
    assert cues_off.owner("cues", "*") is None
    assert cues_off.owner("cues", "anything") is None
    assert cues_off.view_for("bridge").pairs == frozenset()


def test_rows_are_the_sorted_domain() -> None:
    ownership = _resolve({"native": NATIVE, "bridge": BRIDGE}, {"viewers": {"q": "none"}})
    assert ownership.rows() == (
        ("chat", "*"),
        ("chat", "x"),
        ("chat", "y"),
        ("cues", "*"),
        ("viewers", "*"),
        ("viewers", "q"),
        ("viewers", "x"),
        ("viewers", "y"),
    )


# -- views and exclusions ----------------------------------------------------- #


def test_view_for_and_excluded_for_a_wildcard_owner() -> None:
    star = _families(("viewers", ["*"], True))
    ownership = _resolve(
        {"native": star, "bridge": BRIDGE}, {"viewers": {"x": "bridge"}}
    )
    assert ownership.excluded("viewers") == frozenset({"x"})
    native = ownership.view_for("native")
    assert native.pairs == frozenset({("viewers", "*"), ("viewers", "y")})
    assert native.owns("viewers", "y") and native.owns("viewers", "other")
    assert not native.owns("viewers", "x")
    assert native.platforms("viewers") == ("*", "y")
    bridge = ownership.view_for("bridge")
    assert bridge.pairs == frozenset({("viewers", "x"), ("cues", "*")})
    assert bridge.owns("viewers", "x") and not bridge.owns("viewers", "y")
    assert bridge.owns("cues", "x")


def test_the_unrestricted_view_owns_everything() -> None:
    view = OwnedInputs.unrestricted()
    for family in INPUT_FAMILIES:
        assert view.owns(family, "x") and view.owns(family, "*")
    with pytest.raises(AttributeError):
        view.pairs = frozenset()  # type: ignore[misc]


def test_constrains_any() -> None:
    assert not _resolve({"plain": None}).constrains_any()
    assert not _resolve({"opt_in": _families(("chat", ["x"], False))}).constrains_any()
    assert _resolve({"native": NATIVE}).constrains_any()
    assert _resolve({"plain": None}, {"chat": {"x": "none"}}).constrains_any()


# -- D7: the ownership-aware settings hook ----------------------------------- #


@pytest.mark.asyncio
async def test_an_aware_hook_receives_the_view_and_a_plain_hook_one_argument(
    tmp_path: Path,
) -> None:
    _make(tmp_path, "aware", _families(("chat", ["x"], True)), _AWARE_SOURCE)
    _make(tmp_path, "plain", None, _PLAIN_HOOK_SOURCE)
    config, calls = _config(["aware", "plain"])
    loader = _loader(tmp_path)
    await loader.activate_enabled(config)
    (validated, view), activated = calls["aware"]
    assert (validated, activated) == ("validate", "activate")
    assert isinstance(view, OwnedInputs)
    assert view.pairs == frozenset({("chat", "x")})
    assert calls["plain"] == [("validate", 1)]


# -- R2 / AC11: the resolver names no platform and no vendor ----------------- #


def test_the_ownership_code_in_the_loader_names_no_platform_or_vendor() -> None:
    text = (ROOT / "core" / "loader.py").read_text(encoding="utf-8")
    section = text[text.index("# Input ownership (R2"):]
    assert _VENDOR_OR_BRIDGE.findall(text) == []
    for platform in ("twitch", "kick", "youtube"):
        assert platform not in section.lower(), platform


def test_resolution_is_pure() -> None:
    inputs = {"chat": {"x": "bridge"}}
    snapshot = repr(inputs)
    first = _resolve({"native": NATIVE, "bridge": BRIDGE}, inputs)
    second = _resolve({"native": NATIVE, "bridge": BRIDGE}, inputs)
    assert repr(inputs) == snapshot
    assert first == second
    assert asyncio.iscoroutinefunction(resolve_input_ownership) is False


# --------------------------------------------------------------------------- #
# P3: the ``inputs`` block through load_config, run and --check-config (R2, R1)
# --------------------------------------------------------------------------- #

from core import main as application  # noqa: E402
from core.config_ui import _run_socket_free  # noqa: E402
from test_integration import _environment  # noqa: E402

EXAMPLE_CONFIG = ROOT / "config.yaml.example"

#: A test module that records every hook it receives in the file its settings
#: name, so a profile run from disk can still be observed.
_MARKER_SOURCE = """
from pathlib import Path


def _mark(settings, step):
    with Path(settings["marker"]).open("a", encoding="utf-8") as marker:
        marker.write(step + "\\n")


class Handle:
    def __init__(self, settings):
        self.settings = settings

    async def prepare(self):
        _mark(self.settings, "prepare")

    async def close(self):
        pass


def validate_settings(settings):
    _mark(settings, "validate")


async def activate(context, settings, catalog):
    _mark(settings, "activate")
    return Handle(settings)
"""

_AC7_DIAGNOSTIC = (
    "inputs: family 'chat': platform 'x': owned by default by both 'a' and 'b'; "
    "set inputs.chat.x"
)


def _profile(tmp_path: Path, inputs: Any = None, *, omit: bool = False) -> tuple[Path, Path]:
    """A profile enabling the two AC7 test modules, and their marker file."""

    modules = tmp_path / "modules"
    modules.mkdir(exist_ok=True)
    for name in ("a", "b"):
        if not (modules / name).exists():
            _make(modules, name, NATIVE, _MARKER_SOURCE)
    marker = tmp_path / "marker.log"
    profile: dict[str, Any] = {
        "modules_directory": str(modules),
        "enabled_modules": ["a", "b"],
        "modules": {name: {"marker": str(marker)} for name in ("a", "b")},
    }
    if not omit:
        profile["inputs"] = inputs
    path = tmp_path / "profile.yaml"
    path.write_text(yaml.safe_dump(profile), encoding="utf-8")
    return path, marker


def _marks(marker: Path) -> list[str]:
    return marker.read_text(encoding="utf-8").split() if marker.exists() else []


def test_ac7_end_to_end_check_config_exits_2_with_one_diagnostic_and_no_socket(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """AC7 through the command line: the ambiguity is refused by
    ``--check-config`` with exactly the resolver's one diagnostic, 0 modules
    touched and 0 sockets created — the event loop included."""

    path, marker = _profile(tmp_path, omit=True)
    attempts: list[Any] = []

    def refuse(*args: Any, **kwargs: Any) -> None:
        attempts.append(args)
        raise AssertionError("socket created")

    monkeypatch.setattr(asyncio, "run", _run_socket_free)
    monkeypatch.setattr(socket.socket, "__init__", refuse)
    for name in ("socketpair", "fromfd", "create_connection", "create_server"):
        monkeypatch.setattr(socket, name, refuse)

    status = application.main(["--config", str(path), "--check-config"])

    assert status == 2
    assert capsys.readouterr().err == f"error: {_AC7_DIAGNOSTIC}\n"
    assert _marks(marker) == []
    assert attempts == []


@pytest.mark.asyncio
async def test_an_explicit_entry_lets_the_check_accept_the_ac7_modules(
    tmp_path: Path,
) -> None:
    path, marker = _profile(tmp_path, {"chat": {"x": "b"}})
    diagnostics: list[str] = []

    status = await application.check_config(
        path, environ={}, diagnostic_reporter=diagnostics.append
    )

    assert (status, diagnostics) == (0, [])
    # Settings validated, nothing activated or prepared.
    assert _marks(marker) == ["validate", "validate"]


@pytest.mark.parametrize(
    ("inputs", "expected"),
    [
        (["chat"], "inputs: must be a mapping of family to platform to owner"),
        ("chat", "inputs: must be a mapping of family to platform to owner"),
        (None, "inputs: must be a mapping of family to platform to owner"),
        ({"chat": ["x"]}, "inputs.chat: must be a mapping of platform to owner"),
        ({"chat": {1: "a"}}, "inputs.chat: platform keys must be strings"),
        (
            {"chat": {"x": {"owner": "a"}}},
            "inputs.chat.x: owner must be a module name or a list of module names",
        ),
        (
            {"chat": {"x": ["a", 2]}},
            "inputs.chat.x: owner must be a module name or a list of module names",
        ),
    ],
)
@pytest.mark.asyncio
async def test_a_malformed_inputs_block_is_refused_naming_inputs(
    tmp_path: Path, inputs: Any, expected: str
) -> None:
    path, marker = _profile(tmp_path, inputs)
    diagnostics: list[str] = []

    status = await application.check_config(
        path, environ={}, diagnostic_reporter=diagnostics.append
    )

    assert (status, diagnostics) == (2, [expected])
    assert _marks(marker) == []
    with pytest.raises(application.ConfigurationError) as caught:
        application.load_config(path, environ={})
    assert str(caught.value) == expected


def test_load_config_leaves_inputs_unresolved_and_absent_means_absent(
    tmp_path: Path,
) -> None:
    """Owner names are not secrets: a ``${NAME}`` there is neither looked up
    nor counted as a reference; a profile without the block is unchanged."""

    path, _marker = _profile(tmp_path, {"chat": {"x": "${OWNER}", "*": ["a", "b"]}})
    config = application.load_config(path, environ={})
    assert config["inputs"] == {"chat": {"x": "${OWNER}", "*": ["a", "b"]}}

    path, _marker = _profile(tmp_path, omit=True)
    assert "inputs" not in application.load_config(path, environ={})


@pytest.mark.asyncio
async def test_a_profile_without_inputs_checks_exactly_as_before(tmp_path: Path) -> None:
    """R1/AC1 shape: a tmp copy of the fixture profile ``tests/test_main.py``
    checks (the shipped example) gives the same status and diagnostics —
    accepted with its environment, the one unresolved-reference diagnostic
    without the access token."""

    profile = yaml.safe_load(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
    assert "inputs" not in profile
    profile["modules_directory"] = application.MODULES_DIRECTORY_BUILTIN
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(profile), encoding="utf-8")

    environ = _environment()
    diagnostics: list[str] = []
    status = await application.check_config(
        path, environ=environ, diagnostic_reporter=diagnostics.append
    )
    assert (status, diagnostics) == (0, [])

    del environ["TWITCH_ACCESS_TOKEN"]
    status = await application.check_config(
        path, environ=environ, diagnostic_reporter=diagnostics.append
    )
    assert (status, diagnostics) == (
        2,
        ["modules.twitch.access_token: environment reference is unresolved"],
    )


@pytest.mark.parametrize(
    "inputs",
    [
        None,  # AC7: two default declarers, no explicit entry
        {"chat": {"x": ["a", "b"]}},  # AC8: an owner list of two
        {"chat": {"x": "c"}, "weather": {"x": "a"}},  # AC8: two refusals at once
    ],
)
@pytest.mark.asyncio
async def test_run_and_check_config_give_the_same_diagnostics(
    tmp_path: Path, inputs: Any
) -> None:
    """R2 parity: the same invalid profile is refused through ``run`` and
    ``--check-config`` with the same diagnostic list, before any module is
    validated, activated or prepared."""

    path, marker = _profile(tmp_path, inputs, omit=inputs is None)

    checked: list[str] = []
    check_status = await application.check_config(
        path, environ={}, diagnostic_reporter=checked.append
    )
    ran: list[str] = []
    ready: list[str] = []
    run_status = await application.run(
        path,
        asyncio.Event(),
        environ={},
        ready_reporter=ready.append,
        diagnostic_reporter=ran.append,
    )

    assert check_status == 2 and run_status == 1
    assert checked and ran == checked
    assert ready == []
    assert _marks(marker) == []
    if inputs is None:
        assert checked == [_AC7_DIAGNOSTIC]
