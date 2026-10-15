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


# --------------------------------------------------------------------------- #
# P4: the runtime guard — owned view, guarded facades, counter, diagnostic (R3)
# --------------------------------------------------------------------------- #

from core.contracts import COUNTER_LOST_TRACES  # noqa: E402
from core.runtime import ModuleContext, _OwnedBus  # noqa: E402
from core.triggers import TriggerDecision  # noqa: E402

#: A module whose activation publishes ``settings["count"]`` chat events for
#: ``settings["platform"]`` through the bus it received — the context's for a
#: v2 manifest, the positional bus for a v1 one.
_PUBLISHER_SOURCE = """
class Handle:
    async def close(self):
        pass


def validate_settings(settings):
    pass


async def activate(first, settings, catalog):
    bus = first.bus if settings["v2"] else first
    settings["received"].append((first, bus))
    for index in range(settings["count"]):
        await bus.publish(
            "channel.chat.message",
            {
                "platform": settings["platform"],
                "channel_id": "c",
                "message_id": "m%d" % index,
                "text": "hi",
                "author": {"id": "u"},
            },
            {"source": "publisher"},
        )
    return Handle()
"""

#: A module that keeps the context it was handed.
_KEEPER_SOURCE = """
class Handle:
    async def close(self):
        pass


def validate_settings(settings):
    pass


async def activate(context, settings, catalog):
    settings["contexts"].append(context)
    return Handle()
"""

#: The four consumers AC13 names, by the order they would subscribe in.
_CONSUMERS = (("triggers", -100), ("users", 0), ("chat_context", 10), ("brain", 20))


def _make_v1(root: Path, name: str, source: str) -> None:
    directory = root / name
    directory.mkdir()
    (directory / "module.yaml").write_text(
        yaml.safe_dump(_manifest(name, None, version=None)), encoding="utf-8"
    )
    (directory / "__init__.py").write_text(source, encoding="utf-8")


class _Recorder:
    """Recording subscribers standing in for every chat consumer, plus traces."""

    def __init__(self, bus: Any) -> None:
        self.delivered: dict[str, list[Any]] = {name: [] for name, _ in _CONSUMERS}
        self.traces: list[Any] = []
        for name, order in _CONSUMERS:
            bus.subscribe("channel.chat.message", self._handler(name), order=order)
        bus.subscribe(TRACE_INPUT_OWNERSHIP_REJECTED, self._trace)

    def _handler(self, name: str) -> Any:
        async def handle(event: Any) -> None:
            self.delivered[name].append(event)

        return handle

    async def _trace(self, event: Any) -> None:
        self.traces.append(dict(event["payload"]))

    def counts(self) -> set[int]:
        return {len(events) for events in self.delivered.values()}


def _rejections(loader: ModuleLoader) -> int:
    return loader.context.supervision.snapshot()[COUNTER_INPUT_OWNERSHIP_REJECTIONS]


def _publisher_settings(platform: str = "x", *, v2: bool = True) -> dict[str, Any]:
    return {"platform": platform, "count": 3, "v2": v2, "received": []}


async def _run_publisher(
    tmp_path: Path,
    *,
    publisher_families: list[dict[str, Any]] | None,
    others: dict[str, list[dict[str, Any]]],
    inputs: Any = None,
    v1: bool = False,
) -> tuple[ModuleLoader, _Recorder, dict[str, Any]]:
    if v1:
        _make_v1(tmp_path, "publisher", _PUBLISHER_SOURCE)
    else:
        _make(tmp_path, "publisher", publisher_families, _PUBLISHER_SOURCE)
    for name, families in others.items():
        _make(tmp_path, name, families)
    loader = _loader(tmp_path)
    recorder = _Recorder(loader.bus)
    settings = _publisher_settings(v2=not v1)
    config: dict[str, Any] = {
        "enabled_modules": [*others, "publisher"],
        "modules": {
            "publisher": settings,
            **{name: {"calls": []} for name in others},
        },
    }
    if inputs is not None:
        config["inputs"] = inputs
    await loader.activate_enabled(config)
    return loader, recorder, settings


@pytest.mark.asyncio
async def test_ac13_a_declared_module_never_delivers_a_pair_it_does_not_own(
    tmp_path: Path,
) -> None:
    """AC13: 3 non-owned publications reach no subscriber, count 3, trace once."""

    loader, recorder, _ = await _run_publisher(
        tmp_path,
        publisher_families=_families(("chat", ["y"], True)),
        others={"native": NATIVE},
    )
    assert recorder.counts() == {0}
    assert _rejections(loader) == 3
    assert recorder.traces == [{"module": "publisher", "family": "chat", "platform": "x"}]


@pytest.mark.asyncio
async def test_ac13_the_owner_itself_delivers(tmp_path: Path) -> None:
    loader, recorder, _ = await _run_publisher(
        tmp_path, publisher_families=NATIVE, others={}
    )
    assert recorder.counts() == {3}
    assert _rejections(loader) == 0
    assert recorder.traces == []


@pytest.mark.asyncio
@pytest.mark.parametrize("v1", [False, True], ids=["v2", "v1"])
async def test_ac44_an_undeclared_publisher_is_delivered_when_nobody_owns_the_pair(
    tmp_path: Path, v1: bool
) -> None:
    """AC44 case 1, v2 and v1 (finding P1): delivered as today, counter 0."""

    loader, recorder, settings = await _run_publisher(
        tmp_path, publisher_families=None, others={}, v1=v1
    )
    assert recorder.counts() == {3}
    assert _rejections(loader) == 0
    assert recorder.traces == []
    ((_, bus),) = settings["received"]
    # Nothing is constrained, so the shared bus itself is handed out (D3).
    assert bus is loader.bus


@pytest.mark.asyncio
@pytest.mark.parametrize("v1", [False, True], ids=["v2", "v1"])
@pytest.mark.parametrize(
    ("others", "inputs"),
    [({"native": NATIVE}, None), ({}, {"chat": {"x": "none"}})],
    ids=["second-module-default", "inputs-none"],
)
async def test_ac44_an_undeclared_publisher_is_refused_a_resolved_or_none_pair(
    tmp_path: Path, v1: bool, others: dict[str, Any], inputs: Any
) -> None:
    """AC44 cases 2 and 3, v2 and v1: 0 delivered, counter 3, 1 diagnostic."""

    loader, recorder, settings = await _run_publisher(
        tmp_path, publisher_families=None, others=others, inputs=inputs, v1=v1
    )
    assert recorder.counts() == {0}
    assert _rejections(loader) == 3
    assert recorder.traces == [{"module": "publisher", "family": "chat", "platform": "x"}]
    ((first, bus),) = settings["received"]
    assert isinstance(bus, _OwnedBus)
    assert isinstance(first, ModuleContext) is not v1


@pytest.mark.asyncio
async def test_ac9_the_native_view_is_empty_and_the_owner_view_holds_the_pair(
    tmp_path: Path,
) -> None:
    _make(tmp_path, "twitch", _families(("chat", ["twitch"], True)), _KEEPER_SOURCE)
    _make(tmp_path, "bridge", _families(("chat", ["twitch"], False)), _KEEPER_SOURCE)
    settings = {name: {"contexts": []} for name in ("twitch", "bridge")}
    loader = _loader(tmp_path)
    await loader.activate_enabled(
        {
            "enabled_modules": ["twitch", "bridge"],
            "modules": settings,
            "inputs": {"chat": {"twitch": "bridge"}},
        }
    )
    (native,) = settings["twitch"]["contexts"]
    (owner,) = settings["bridge"]["contexts"]
    assert native.inputs.pairs == frozenset()
    assert not native.inputs.owns("chat", "twitch")
    assert owner.inputs.pairs == frozenset({("chat", "twitch")})
    assert owner.inputs.owns("chat", "twitch")


# -- the facades, built through for_module ------------------------------------ #


class _FakeEngine:
    def __init__(self) -> None:
        self.calls: list[Any] = []

    def evaluate(self, event: Any, **kwargs: Any) -> str:
        self.calls.append(event)
        return "evaluated"

    def recorded(self) -> str:
        return "delegated"


class _FakeChat:
    def __init__(self) -> None:
        self.appended: list[tuple[str, str, Any]] = []

    def append(self, platform: str, channel_id: str, entry: Any) -> str:
        self.appended.append((platform, channel_id, entry))
        return "record"


def _guarded_runtime() -> tuple[RuntimeContext, _FakeEngine, _FakeChat]:
    bus = EventBus()
    engine, chat = _FakeEngine(), _FakeChat()
    runtime = RuntimeContext(
        bus=bus,
        actions=ActionRegistry(authorization=AuthorizationPolicy()),
        supervision=Supervision(bus),
        tasks=SupervisedTasks(),
        triggers=engine,
        chat=chat,
    )
    return runtime, engine, chat


def _chat_event(platform: str, kind: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "platform": platform,
        "channel_id": "c",
        "message_id": "m1",
        "text": "hi",
        "author": {"id": "u"},
    }
    if kind is not None:
        payload["kind"] = kind
    return {
        "type": "channel.chat.message",
        "payload": payload,
        "metadata": {"source": "intruder"},
    }


def _intruder(runtime: RuntimeContext) -> ModuleContext:
    ownership = _resolve({"native": NATIVE, "intruder": _families(("chat", ["y"], True))})
    return runtime.for_module(
        "intruder",
        owned_inputs=ownership.view_for("intruder"),
        ownership=ownership,
        declared=True,
    )


def test_for_module_without_ownership_returns_the_shared_objects() -> None:
    runtime, engine, chat = _guarded_runtime()
    context = runtime.for_module("m")
    assert context.bus is runtime.bus
    assert context.triggers is engine
    assert context.chat is chat
    assert context.inputs == OwnedInputs.unrestricted()
    assert context.inputs.owns("chat", "anything")
    with pytest.raises(AttributeError):
        context.inputs = OwnedInputs()  # type: ignore[misc]


def test_for_module_with_ownership_returns_stable_scoped_facades() -> None:
    runtime, engine, chat = _guarded_runtime()
    context = _intruder(runtime)
    assert context.bus is not runtime.bus and context.bus is context.bus
    assert context.triggers is not engine and context.chat is not chat
    assert context.inputs.pairs == frozenset({("chat", "y")})
    # Every other attribute is the shared object's own.
    assert context.bus.list_events() == runtime.bus.list_events()
    assert context.bus.subscribe.__self__ is runtime.bus
    assert context.triggers.recorded() == "delegated"


@pytest.mark.asyncio
async def test_watch_ticks_and_other_events_pass_the_bus_facade_untouched() -> None:
    runtime, _, _ = _guarded_runtime()
    context = _intruder(runtime)
    recorder = _Recorder(runtime.bus)
    other: list[Any] = []

    async def record(event: Any) -> None:
        other.append(event)

    runtime.bus.subscribe("module.custom.event", record)
    for kind in ("watch_tick", "not-a-kind"):
        event = _chat_event("x", kind)
        await context.bus.publish(event["type"], event["payload"], event["metadata"])
    await context.bus.publish("module.custom.event", {"platform": "x"}, {})
    # An event with no platform belongs to no family (D1).
    await context.bus.publish("channel.chat.message", {"text": "hi"}, {})

    assert recorder.counts() == {3}
    assert len(other) == 1
    assert runtime.supervision.snapshot()[COUNTER_INPUT_OWNERSHIP_REJECTIONS] == 0
    assert recorder.traces == []


@pytest.mark.asyncio
async def test_notices_and_cues_are_guarded_like_messages() -> None:
    runtime, _, _ = _guarded_runtime()
    context = _intruder(runtime)
    recorder = _Recorder(runtime.bus)
    for kind in ("message", PLATFORM_NOTICE_KINDS[0], CUE_KIND):
        event = _chat_event("x", kind)
        await context.bus.publish(event["type"], event["payload"], event["metadata"])
    assert recorder.counts() == {0}
    assert runtime.supervision.snapshot()[COUNTER_INPUT_OWNERSHIP_REJECTIONS] == 3
    assert recorder.traces == [
        {"module": "intruder", "family": "chat", "platform": "x"},
        {"module": "intruder", "family": "cues", "platform": "x"},
    ]


@pytest.mark.asyncio
async def test_a_refused_event_reaches_not_even_a_first_ordered_middleware() -> None:
    runtime, _, _ = _guarded_runtime()
    seen: list[Any] = []

    async def audit(event: Any) -> None:
        seen.append(event)

    runtime.bus.subscribe("**", audit, order=-10_000)
    context = _intruder(runtime)
    event = _chat_event("x")
    result = await context.bus.publish(event["type"], event["payload"], event["metadata"])
    assert result is None
    assert [item["type"] for item in seen] == [TRACE_INPUT_OWNERSHIP_REJECTED]


def test_the_evaluate_facade_refuses_without_counting() -> None:
    runtime, engine, _ = _guarded_runtime()
    context = _intruder(runtime)
    decision = context.triggers.evaluate(_chat_event("x"))
    assert isinstance(decision, TriggerDecision)
    assert decision.accepted is False
    assert decision.reason == "input_not_owned"
    assert (decision.platform, decision.channel_id) == ("x", "c")
    assert decision.source_event_id == "m1"
    assert engine.calls == []
    assert runtime.supervision.snapshot()[COUNTER_INPUT_OWNERSHIP_REJECTIONS] == 0

    # An owned event and an event with no family reach the engine.
    assert context.triggers.evaluate(_chat_event("y")) == "evaluated"
    assert context.triggers.evaluate(_chat_event("x", "watch_tick")) == "evaluated"
    assert len(engine.calls) == 2


def test_the_chat_facade_drops_a_non_owned_append() -> None:
    runtime, _, chat = _guarded_runtime()
    context = _intruder(runtime)
    assert context.chat.append("x", "c", "entry") is None
    assert context.chat.append("y", "c", "entry") == "record"
    assert chat.appended == [("y", "c", "entry")]


@pytest.mark.asyncio
async def test_the_diagnostic_set_is_bounded_and_the_counter_is_not() -> None:
    from core import runtime as runtime_module

    runtime, _, _ = _guarded_runtime()
    context = _intruder(runtime)
    recorder = _Recorder(runtime.bus)
    cap = runtime_module.MAX_OWNERSHIP_DIAGNOSTICS
    for index in range(cap + 5):
        event = _chat_event(f"p{index}")
        await context.bus.publish(event["type"], event["payload"], event["metadata"])
    assert runtime.supervision.snapshot()[COUNTER_INPUT_OWNERSHIP_REJECTIONS] == cap + 5
    assert len(recorder.traces) == cap


@pytest.mark.asyncio
async def test_a_lost_rejection_trace_is_counted_and_never_propagates() -> None:
    bus = EventBus()

    class _FailingSupervision:
        def __init__(self) -> None:
            self.counters: dict[str, int] = {}

        def count(self, name: str, amount: int = 1) -> int:
            self.counters[name] = self.counters.get(name, 0) + amount
            return self.counters[name]

        async def emit(self, *args: Any, **kwargs: Any) -> None:
            raise RuntimeError("trace sink down")

    supervision = _FailingSupervision()
    facade = _OwnedBus(
        bus,
        module="legacy",
        ownership=_resolve({"native": NATIVE}),
        declared=False,
        supervision=supervision,
    )
    event = _chat_event("x")
    assert await facade.publish(event["type"], event["payload"], event["metadata"]) is None
    assert supervision.counters == {
        COUNTER_INPUT_OWNERSHIP_REJECTIONS: 1,
        COUNTER_LOST_TRACES: 1,
    }


@pytest.mark.asyncio
async def test_the_v1_facade_delegates_every_other_bus_call() -> None:
    """A v1 module subscribing through its facade receives what it may."""

    bus = EventBus()
    facade = _OwnedBus(
        bus,
        module="legacy",
        ownership=_resolve({"native": NATIVE}),
        declared=False,
        supervision=Supervision(bus),
    )
    received: list[Any] = []

    async def handle(event: Any) -> None:
        received.append(event["payload"]["platform"])

    facade.subscribe("channel.chat.message", handle)
    for platform in ("x", "z"):
        event = _chat_event(platform)
        await facade.publish(event["type"], event["payload"], event["metadata"])
    # (chat, x) is the native module's; (chat, z) has no owner: delivered.
    assert received == ["z"]
