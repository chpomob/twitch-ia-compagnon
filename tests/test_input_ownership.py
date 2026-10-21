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
    """Phase 8 R4 supersedes ``cue`` being last: it stays the tenth kind
    (index 9) and the four appended kinds end with ``timer``."""

    assert EVENT_KINDS[9] == CUE_KIND == "cue"
    assert EVENT_KINDS[-1] == "timer"
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
    _redact_known,
    _redact_values,
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


# -- F4: a configured credential used as a key is never echoed ---------------- #

_F4_SECRET = "review-secret-4521"
_F4_VOCABULARY = "(chat, viewers, cues)"


def _f4_leaks(text: str, secret: str) -> bool:
    """Whether *text* holds *secret* or any prefix of it longer than a word stem."""

    return any(secret[:end] in text for end in range(8, len(secret) + 1))


async def _f4_streamerbot_refusal(inputs: Any) -> tuple[str, ModuleLoader]:
    loader = _loader(ROOT / "modules")
    with pytest.raises(ModuleLoadError) as caught:
        await loader.activate_enabled({
            "enabled_modules": ["streamerbot"],
            "modules": {"streamerbot": {
                "url": "ws://127.0.0.1:9/",
                "companion_name": "Companion",
                "password": _F4_SECRET,
            }},
            "inputs": inputs,
        })
    return str(caught.value), loader


@pytest.mark.asyncio
async def test_f4_a_credential_used_as_a_family_key_is_never_echoed() -> None:
    """F4: ownership resolution runs before credential redaction, so it redacts
    the configured password out of the family key itself; the offender, the
    vocabulary and the reason stay identifiable, and nothing is activated."""

    text, loader = await _f4_streamerbot_refusal({_F4_SECRET: {"x": "streamerbot"}})
    assert not _f4_leaks(text, _F4_SECRET)
    assert text == (
        "inputs: family '<redacted>': platform 'x': value 'streamerbot': "
        f"family is outside the vocabulary {_F4_VOCABULARY}"
    )
    assert loader.activations == []


@pytest.mark.asyncio
async def test_f4_a_credential_used_as_a_platform_key_is_never_echoed() -> None:
    """F4: the same password as a platform key is redacted; the family and the
    rejected owner are still named, with the refusal reason."""

    text, loader = await _f4_streamerbot_refusal({"chat": {_F4_SECRET: "not-enabled"}})
    assert not _f4_leaks(text, _F4_SECRET)
    assert text == (
        "inputs: family 'chat': platform '<redacted>': owner 'not-enabled' is not enabled"
    )
    assert loader.activations == []


@pytest.mark.asyncio
async def test_f4_a_refusal_with_no_credential_involved_is_unchanged() -> None:
    """F4's redaction is not over-broad: with the password configured but used
    nowhere, both refusals keep their full text."""

    text, _ = await _f4_streamerbot_refusal({
        "weather": {"x": "streamerbot"},
        "chat": {"twitch": "not-enabled"},
    })
    assert text == (
        "inputs: family 'weather': platform 'x': value 'streamerbot': "
        f"family is outside the vocabulary {_F4_VOCABULARY}\n"
        "inputs: family 'chat': platform 'twitch': owner 'not-enabled' is not enabled"
    )


def test_f4_an_embedded_credential_leaves_no_prefix_behind_the_key_bound() -> None:
    """F4: a credential embedded in a long key, or in an owner value, is cut out
    before the key is bounded, so the bound cannot leave a prefix of it."""

    secret = "embedded-credential-" + "q" * 60
    padding = "k" * 50
    refusals = []
    for inputs in (
        {padding + secret: {"x": "native"}},
        {"chat": {padding + secret: "native"}},
        {"chat": {"x": f"mod-{secret}"}},
        {"chat": {"x": ["native", f"mod-{secret}"]}},
        {"chat": {"x": "native", "y" + secret: "native"}},
    ):
        with pytest.raises(ModuleLoadError) as caught:
            resolve_input_ownership(
                ["native"],
                {"native": _decl(NATIVE, "native")},
                inputs,
                redacted=frozenset({secret, ""}),
            )
        refusals.extend(caught.value.diagnostics)
        assert str(caught.value) == "\n".join(caught.value.diagnostics)
    assert len(refusals) == 5
    assert not any(_f4_leaks(line, secret) for line in refusals)
    assert all("<redacted>" in line for line in refusals)
    assert refusals[1].startswith("inputs: family 'chat': platform 'kkkk")
    assert refusals[4].endswith(
        "does not declare family 'chat' for platform 'y<redacted>'"
    )


def test_f4_an_unknown_credential_set_is_refused_before_any_diagnostic() -> None:
    """F4: with no known-credential set the resolver stops rather than report
    a diagnostic it could not redact."""

    with pytest.raises(TypeError, match="known-credential set"):
        resolve_input_ownership(
            ["native"],
            {"native": _decl(NATIVE, "native")},
            {"weather": {"x": "native"}},
            redacted=None,  # type: ignore[arg-type]
        )


# -- N1-B: every dynamic part of a refusal is redacted, the correction too ---- #


def _n1_fragments(secret: str) -> tuple[str, ...]:
    """The whole credential, its first half and its last half."""

    half = len(secret) // 2
    return (secret, secret[:half], secret[half:])


def _n1_default_conflict(platform: str) -> list[str]:
    declarations = {
        name: _decl(_families(("chat", [platform], True)), name) for name in ("a", "b")
    }
    with pytest.raises(ModuleLoadError) as caught:
        resolve_input_ownership(
            ["a", "b"], declarations, {}, redacted=frozenset({_F4_SECRET})
        )
    assert str(caught.value) == "\n".join(caught.value.diagnostics)
    return list(caught.value.diagnostics)


def test_n1_b_a_default_conflict_on_a_credential_platform_never_echoes_it() -> None:
    """N1-B, R8: a declared platform equal to a configured credential is cut
    out of the location *and* of the correction the refusal suggests; the
    refusal, both conflicting providers and the correction path remain."""

    refusals = _n1_default_conflict(_F4_SECRET)
    assert refusals == [
        "inputs: family 'chat': platform '<redacted>': owned by default by both "
        "'a' and 'b'; set inputs.chat.<redacted>"
    ]
    joined = "\n".join(refusals)
    assert not any(fragment in joined for fragment in _n1_fragments(_F4_SECRET))


def test_n1_b_a_default_conflict_without_a_credential_is_unchanged() -> None:
    """N1-B's redaction is not over-broad: a non-secret platform keeps the
    refusal's full original text, its correction path included."""

    assert _n1_default_conflict("twitch") == [
        "inputs: family 'chat': platform 'twitch': owned by default by both "
        "'a' and 'b'; set inputs.chat.twitch"
    ]


def test_a_default_conflict_names_declared_providers_outside_the_field_path_shape() -> None:
    """A declared provider name is shown whatever its spelling: the
    field-path shape guards owners typed in the ``inputs`` block, not the
    module names the manifests declare; only a known credential is cut."""

    names = ("émetteur_a", "émetteur_b")
    declarations = {name: _decl(_families(("chat", ["x"], True)), name) for name in names}
    with pytest.raises(ModuleLoadError) as caught:
        resolve_input_ownership(list(names), declarations, {}, redacted=frozenset())
    assert list(caught.value.diagnostics) == [
        "inputs: family 'chat': platform 'x': owned by default by both "
        "'émetteur_a' and 'émetteur_b'; set inputs.chat.x"
    ]
    with pytest.raises(ModuleLoadError) as caught:
        resolve_input_ownership(
            list(names), declarations, {}, redacted=frozenset({"émetteur_b"})
        )
    assert list(caught.value.diagnostics) == [
        "inputs: family 'chat': platform 'x': owned by default by both "
        "'émetteur_a' and '<redacted>'; set inputs.chat.x"
    ]


def test_n1_every_resolver_refusal_shape_leaves_no_credential_fragment() -> None:
    """N1: whichever refusal a credential-bearing key or owner reaches —
    vocabulary, shape, owner list, unknown or non-declaring owner, default
    conflict — no fragment of the credential survives the joined text, and
    the fixed vocabulary of each refusal is intact."""

    secret = _F4_SECRET
    native = {"native": _decl(NATIVE, "native")}
    refusals: list[str] = []
    for inputs in (
        {secret: {"x": "native"}},
        {secret: "native"},
        {"chat": {secret: 3}},
        {"chat": {secret: ["native", secret]}},
        {"chat": {secret: "ghost"}},
        {"chat": {secret: "native"}},
    ):
        with pytest.raises(ModuleLoadError) as caught:
            resolve_input_ownership(
                ["native"], native, inputs, redacted=frozenset({secret})
            )
        refusals.extend(caught.value.diagnostics)
    refusals.extend(_n1_default_conflict(secret))
    joined = "\n".join(refusals)
    assert not any(fragment in joined for fragment in _n1_fragments(secret))
    assert refusals == [
        "inputs: family '<redacted>': platform 'x': value 'native': "
        f"family is outside the vocabulary {_F4_VOCABULARY}",
        "inputs: family '<redacted>': family is outside the vocabulary "
        f"{_F4_VOCABULARY}",
        "inputs: family 'chat': platform '<redacted>': owner must be one enabled "
        "module name or 'none'",
        "inputs: family 'chat': platform '<redacted>': names more than one owner, "
        "both 'native' and '<redacted>'; name one module or 'none'",
        "inputs: family 'chat': platform '<redacted>': owner 'ghost' is not enabled",
        "inputs: family 'chat': platform '<redacted>': owner 'native' does not "
        "declare family 'chat' for platform '<redacted>'",
        "inputs: family 'chat': platform '<redacted>': owned by default by both "
        "'a' and 'b'; set inputs.chat.<redacted>",
    ]


# -- N2: redaction masks the union of every credential match ---------------- #

_N2_SECRET = "review-secret-4521"
_N2_OTHER = "context-prefix-review-"
_N2_KEY = "context-prefix-" + _N2_SECRET


def test_n2_an_overlapping_known_value_leaves_no_fragment_of_the_credential() -> None:
    """N2, R8: a known value overlapping the beginning of a credential no
    longer breaks the credential's match during redaction. The conflict's
    location and its correction both lose the whole key; neither known value
    nor either half of the credential survives the joined text, and the
    refusal, both providers and the correction path remain."""

    declarations = {
        name: _decl(_families(("chat", [_N2_KEY], True)), name) for name in ("a", "b")
    }
    with pytest.raises(ModuleLoadError) as caught:
        resolve_input_ownership(
            ["a", "b"], declarations, {},
            redacted=frozenset({_N2_SECRET, _N2_OTHER}),
        )
    refusals = list(caught.value.diagnostics)
    assert refusals == [
        "inputs: family 'chat': platform '<redacted>': owned by default by both "
        "'a' and 'b'; set inputs.chat.<redacted>"
    ]
    joined = "\n".join(refusals)
    assert str(caught.value) == joined
    for fragment in (*_n1_fragments(_N2_SECRET), _N2_OTHER):
        assert fragment not in joined


@pytest.mark.parametrize(
    ("text", "values", "expected"),
    [
        # N2 itself: a longer value overlapping the start of a shorter one.
        (
            "k=context-prefix-review-secret-4521;",
            {"review-secret-4521", "context-prefix-review-"},
            "k=<redacted>;",
        ),
        # One value nested inside another.
        ("a=xxsecretyy b=secret", {"secret", "xxsecretyy"}, "a=<redacted> b=<redacted>"),
        # Two values sharing a prefix.
        ("[abcdef] [abcxyz]", {"abcdef", "abcxyz"}, "[<redacted>] [<redacted>]"),
        ("[abcdefxyz]", {"abcdef", "abcxyz"}, "[<redacted>xyz]"),
        # Two values sharing a suffix, overlapping in the text.
        ("[12345678]", {"123456", "345678"}, "[<redacted>]"),
        # Identical values, given twice.
        ("p=secret!", ["secret", "secret"], "p=<redacted>!"),
        # Three-way overlap chain.
        ("<aaaabbbbccccdddd>", {"aaaabbbb", "bbbbcccc", "ccccdddd"}, "<<redacted>>"),
        # A value overlapping itself, and repeated after its first match.
        ("x=abababa y=aba", {"aba"}, "x=<redacted> y=<redacted>"),
        # A value that only appears once another is cut out is not formed.
        ("[sec<REM>ret]", {"<REM>", "secret"}, "[sec<redacted>ret]"),
        # A value equal to the replacement marker.
        ("v=<redacted> w=<redacted>x", {"<redacted>"}, "v=<redacted> w=<redacted>x"),
        ("v=secret", {"<redacted>", "secret"}, "v=<redacted>"),
        # An empty value, alone and among others.
        ("unchanged text", {""}, "unchanged text"),
        ("v=secret", {"", "secret"}, "v=<redacted>"),
        # No values at all; and an empty text.
        ("unchanged text", set(), "unchanged text"),
        ("", {"secret"}, ""),
        # The whole text is a credential.
        ("secret", {"secret"}, "<redacted>"),
    ],
)
def test_n2_the_redaction_primitive_masks_the_union_of_all_matches(
    text: str, values: Any, expected: str
) -> None:
    """N2, R8: every match of every value is found in the *original* text,
    overlaps included, and their union is masked in one pass — whatever the
    order the values come in."""

    assert _redact_values(text, values) == expected
    assert _redact_values(text, list(values)[::-1]) == expected
    assert _redact_known(text, frozenset(values)) == expected
    for value in values:
        if value and value != "<redacted>":
            assert value not in expected


def test_n2_the_primitive_leaves_no_half_of_any_overlapping_credential() -> None:
    """N2: across every arrangement of two overlapping credentials in a key,
    neither credential nor either half of either survives redaction."""

    first, second = "review-secret-4521", "context-prefix-review-"
    for text in (
        "context-prefix-review-secret-4521",
        "review-secret-4521context-prefix-review-",
        "xx" + first + second + first + "yy",
        second[:-7] + first,
    ):
        out = _redact_values(text, (second, first))
        for fragment in (*_n1_fragments(first), first):
            assert fragment not in out
        assert second not in out


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
from core.runtime import (  # noqa: E402
    ModuleContext,
    RuntimeContextError,
    SupervisionError,
    _OwnedBus,
)
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


# -- every public publication route shares the guard (F1) --------------------- #


async def _publish_through(context: ModuleContext, route: str, event: Any) -> list[str]:
    """Publish *event* through one of a module's three public routes.

    Returns what the recorder of ``record_and_emit`` saw, so a caller can
    check the owner's write ran.
    """

    records: list[str] = []
    if route == "bus":
        await context.bus.publish(event["type"], event["payload"], event["metadata"])
    elif route == "emit":
        await context.supervision.emit(
            event["type"], event["payload"], metadata=event["metadata"]
        )
    else:
        await context.supervision.record_and_emit(
            lambda: records.append("recorded"),
            event["type"],
            event["payload"],
            metadata=event["metadata"],
        )
    return records


_ROUTES = ("bus", "emit", "record_and_emit")


@pytest.mark.asyncio
async def test_f1_no_public_route_publishes_a_non_owned_input() -> None:
    """F1: the supervision surface no longer bypasses input ownership (R3).

    The closing gate's counterexample: through a context owning (chat, y),
    with a native owner of (chat, x), a chat/x event published through the
    bus, ``supervision.emit`` and ``supervision.record_and_emit`` reaches no
    subscriber, and each attempt is counted. The diagnostic is the bus
    route's own: one shared guard, so one trace for the pair across routes.
    """

    runtime, _, _ = _guarded_runtime()
    context = _intruder(runtime)
    recorder = _Recorder(runtime.bus)
    seen: list[Any] = []
    runtime.bus.subscribe("channel.chat.message", lambda event: seen.append(event))
    for route in _ROUTES:
        await _publish_through(context, route, _chat_event("x"))
    assert seen == []
    assert recorder.counts() == {0}
    assert context.supervision.snapshot()[COUNTER_INPUT_OWNERSHIP_REJECTIONS] == 3
    assert recorder.traces == [{"module": "intruder", "family": "chat", "platform": "x"}]


@pytest.mark.asyncio
async def test_f1_each_route_diagnoses_its_rejection_like_the_bus_route() -> None:
    """F1: a rejection on any route is traced with the bus route's shape."""

    runtime, _, _ = _guarded_runtime()
    context = _intruder(runtime)
    recorder = _Recorder(runtime.bus)
    for route, platform in zip(_ROUTES, ("x", "z", "w")):
        await _publish_through(context, route, _chat_event(platform))
    assert recorder.counts() == {0}
    assert runtime.supervision.snapshot()[COUNTER_INPUT_OWNERSHIP_REJECTIONS] == 3
    assert recorder.traces == [
        {"module": "intruder", "family": "chat", "platform": platform}
        for platform in ("x", "z", "w")
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("route", _ROUTES)
async def test_f1_an_owned_pair_is_delivered_once_through_each_route(route: str) -> None:
    runtime, _, _ = _guarded_runtime()
    context = _intruder(runtime)
    recorder = _Recorder(runtime.bus)
    records = await _publish_through(context, route, _chat_event("y"))
    assert recorder.counts() == {1}
    assert runtime.supervision.snapshot()[COUNTER_INPUT_OWNERSHIP_REJECTIONS] == 0
    assert recorder.traces == []
    if route == "record_and_emit":
        assert records == ["recorded"]


@pytest.mark.asyncio
async def test_f1_a_refused_record_and_emit_still_records_its_record() -> None:
    """F1, chosen behaviour: the owner's write runs, only the publication is refused.

    ``record_and_emit`` orders the owner's record before its publication
    (R8); the record is the module's own state, so the guard leaves it in
    place and refuses the publication that would follow it — counted and
    diagnosed exactly as a refused bus publication.
    """

    runtime, _, _ = _guarded_runtime()
    context = _intruder(runtime)
    recorder = _Recorder(runtime.bus)
    order: list[str] = []

    async def record() -> None:
        order.append("recorded")

    async def trace(event: Any) -> None:
        order.append("traced")

    runtime.bus.subscribe(TRACE_INPUT_OWNERSHIP_REJECTED, trace)
    event = _chat_event("x")
    await context.supervision.record_and_emit(
        record, event["type"], event["payload"], metadata=event["metadata"]
    )
    assert order == ["recorded", "traced"]
    assert recorder.counts() == {0}
    assert runtime.supervision.snapshot()[COUNTER_INPUT_OWNERSHIP_REJECTIONS] == 1
    with pytest.raises(SupervisionError):
        context.supervision.record_and_emit(
            "not callable", event["type"], event["payload"]  # type: ignore[arg-type]
        )


@pytest.mark.asyncio
async def test_f1_supervision_without_ownership_is_unguarded() -> None:
    runtime, _, _ = _guarded_runtime()
    context = runtime.for_module("m")
    recorder = _Recorder(runtime.bus)
    for route in _ROUTES:
        await _publish_through(context, route, _chat_event("x"))
    assert recorder.counts() == {3}
    assert runtime.supervision.snapshot()[COUNTER_INPUT_OWNERSHIP_REJECTIONS] == 0


# -- the check reads the payload as it is published (F1, round 2) ------------- #


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("start", "change"),
    [
        (_chat_event("y"), {"platform": "x"}),
        (_chat_event("x", kind="not-an-input"), {"kind": "message"}),
    ],
    ids=["platform", "kind"],
)
async def test_f1_a_recorder_that_changes_the_payload_is_checked_after_it(
    start: dict[str, Any], change: dict[str, Any]
) -> None:
    """The check follows *record*: a payload it turns non-owned is refused.

    Admitted as called — chat/y is owned, an unknown kind belongs to no
    family — but *record* turns it into chat/x, which the module does not
    own: it reaches no subscriber, and is counted and diagnosed.
    """

    runtime, _, _ = _guarded_runtime()
    context = _intruder(runtime)
    recorder = _Recorder(runtime.bus)
    payload = dict(start["payload"])
    await context.supervision.record_and_emit(
        lambda: payload.update(change),
        start["type"],
        payload,
        metadata=start["metadata"],
    )
    assert recorder.counts() == {0}
    assert runtime.supervision.snapshot()[COUNTER_INPUT_OWNERSHIP_REJECTIONS] == 1
    assert recorder.traces == [{"module": "intruder", "family": "chat", "platform": "x"}]


@pytest.mark.asyncio
async def test_f1_a_recorder_that_makes_the_payload_owned_is_delivered() -> None:
    runtime, _, _ = _guarded_runtime()
    context = _intruder(runtime)
    recorder = _Recorder(runtime.bus)
    event = _chat_event("x")
    payload = dict(event["payload"])
    await context.supervision.record_and_emit(
        lambda: payload.update(platform="y"),
        event["type"],
        payload,
        metadata=event["metadata"],
    )
    assert recorder.counts() == {1}
    assert runtime.supervision.snapshot()[COUNTER_INPUT_OWNERSHIP_REJECTIONS] == 0
    assert recorder.traces == []


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["emit", "record_and_emit"])
async def test_f1_a_payload_changed_between_call_and_await_is_checked(route: str) -> None:
    """The supervision routes check when they publish, not when called."""

    runtime, _, _ = _guarded_runtime()
    context = _intruder(runtime)
    recorder = _Recorder(runtime.bus)
    event = _chat_event("y")
    payload = dict(event["payload"])
    if route == "emit":
        pending = context.supervision.emit(
            event["type"], payload, metadata=event["metadata"]
        )
    else:
        pending = context.supervision.record_and_emit(
            lambda: None, event["type"], payload, metadata=event["metadata"]
        )
    payload["platform"] = "x"
    await pending
    assert recorder.counts() == {0}
    assert runtime.supervision.snapshot()[COUNTER_INPUT_OWNERSHIP_REJECTIONS] == 1
    assert recorder.traces == [{"module": "intruder", "family": "chat", "platform": "x"}]


@pytest.mark.asyncio
async def test_f1_guarded_routes_keep_the_call_site_refusals() -> None:
    """A terminal trace on emit, or a blank type, still fails at the call."""

    runtime, _, _ = _guarded_runtime()
    context = _intruder(runtime)
    with pytest.raises(SupervisionError):
        context.supervision.emit("module.stopped", {})
    recorded: list[str] = []
    with pytest.raises(RuntimeContextError):
        context.supervision.record_and_emit(lambda: recorded.append("x"), 7, {})  # type: ignore[arg-type]
    assert recorded == []


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


# --------------------------------------------------------------------------- #
# P5: family-gated binding — `users.read` bound only where owned (R3, D5)
# --------------------------------------------------------------------------- #

from core.actions import (  # noqa: E402
    ActionBinding,
    ActionExecutor,
    ActionSpec,
    AmbiguousBindingError,
    AuthorizationRule,
)
from core.contracts import (  # noqa: E402
    WILDCARD,
    ActionCall,
    ActionObservation,
    Destination,
)

_GATED = INPUT_FAMILY_GATED_ACTIONS["viewers"]
_OPERATOR = "operator:companion"
_ANY_CHAT = Destination(WILDCARD, WILDCARD, "chat")


def _gated_spec(name: str = _GATED) -> ActionSpec:
    return ActionSpec(
        name=name,
        version=1,
        description="Read one page of the observed chat authors.",
        argument_schema={
            "type": "object",
            "properties": {"limit": {"type": "integer", "minimum": 1}},
            "additionalProperties": False,
        },
        result_schema={"type": "object"},
        nature="read",
        required_permissions=("users.read",),
        supported_destinations=(_ANY_CHAT,),
        timeout_seconds=5.0,
        idempotency="natural",
    )


class _Spy:
    """A provider counting what it was actually asked to do."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.calls: list[Any] = []

    async def invoke(self, invocation: Any) -> ActionObservation:
        self.calls.append(invocation)
        return ActionObservation(
            status="success", provenance={"transport": "fake"}, result={}
        )


#: A module registering the gated action in activation, as `users` does in
#: preparation, with the spec and the provider its settings carry.
_REGISTRAR_SOURCE = """
class Handle:
    async def close(self):
        pass


def validate_settings(settings):
    pass


async def activate(context, settings, catalog):
    settings["bound"].extend(
        context.actions.register(settings["spec"], settings["provider"])
    )
    context.actions.mark_ready()
    return Handle()
"""

#: The shipped shape: a `*` default owner and a concrete non-default declarer.
STAR = _families(("viewers", ["*"], True))
SECOND = _families(("viewers", ["twitch", "kick", "youtube"], False))
SECOND_WITH_STAR = _families(
    ("viewers", ["*"], False), ("viewers", ["twitch", "kick", "youtube"], False)
)
DOMAIN = ("twitch", "kick", "youtube")


def _gated_context(
    runtime: RuntimeContext, ownership: InputOwnership, name: str
) -> ModuleContext:
    return runtime.for_module(
        name, owned_inputs=ownership.view_for(name), ownership=ownership, declared=True
    )


def _bare_runtime() -> RuntimeContext:
    return _context(EventBus())


def _register_both(
    ownership: InputOwnership,
) -> tuple[ActionRegistry, dict[str, tuple[ActionBinding, ...]]]:
    runtime = _bare_runtime()
    bound = {
        name: _gated_context(runtime, ownership, name).actions.register(
            _gated_spec(), _Spy(f"{name}-provider")
        )
        for name in ("star", "second")
    }
    return runtime.actions, bound


def _shape(bindings: tuple[ActionBinding, ...]) -> list[tuple[str, frozenset[str]]]:
    return [(b.destination.platform, b.excluded_platforms) for b in bindings]


# -- the registry's exclusion (D5) -------------------------------------------- #


def _registry_with(spec: ActionSpec | None = None) -> ActionRegistry:
    registry = ActionRegistry(authorization=AuthorizationPolicy())
    registry.declare(spec or _gated_spec(), module="a")
    return registry


@pytest.mark.parametrize("wildcard_first", [True, False])
def test_an_excluded_platform_is_not_overlapped(wildcard_first: bool) -> None:
    registry = _registry_with()

    def wildcard() -> Any:
        return registry.bind(
            _GATED, _Spy("w"), module="a", excluded_platforms=["twitch"]
        )

    def concrete() -> Any:
        return registry.bind(
            _GATED, _Spy("c"), module="b",
            destinations=Destination("twitch", WILDCARD, "chat"),
        )

    first, second = (wildcard, concrete) if wildcard_first else (concrete, wildcard)
    first()
    second()
    bindings = {b.provider_name: b for b in registry.bindings(_GATED)}
    assert bindings["w"].excluded_platforms == frozenset({"twitch"})
    assert bindings["c"].excluded_platforms == frozenset()
    # A platform the wildcard does not exclude still overlaps it.
    with pytest.raises(AmbiguousBindingError):
        registry.bind(
            _GATED, _Spy("k"), module="b",
            destinations=Destination("kick", WILDCARD, "chat"),
        )
    assert registry.resolve(_GATED, Destination("twitch", "c", "chat")) is bindings["c"]
    assert registry.resolve(_GATED, Destination("kick", "c", "chat")) is bindings["w"]


def test_resolve_returns_none_for_an_excluded_platform_with_no_other_binding() -> None:
    registry = _registry_with()
    (binding,) = registry.bind(
        _GATED, _Spy("w"), module="a", excluded_platforms=("kick", "twitch")
    )
    assert registry.resolve(_GATED, Destination("kick", "c", "chat")) is None
    assert registry.resolve(_GATED, Destination("twitch", "c", "chat")) is None
    assert registry.resolve(_GATED, Destination("other", "c", "chat")) is binding


def test_existing_overlap_refusals_are_unchanged_without_an_exclusion() -> None:
    registry = _registry_with()
    registry.bind(_GATED, _Spy("w"), module="a")
    with pytest.raises(AmbiguousBindingError):
        registry.bind(
            _GATED, _Spy("c"), module="b",
            destinations=Destination("twitch", WILDCARD, "chat"),
        )
    with pytest.raises(AmbiguousBindingError):
        registry.bind(_GATED, _Spy("w2"), module="b", excluded_platforms=["twitch"])
    # A refused binding records nothing.
    assert [b.provider_name for b in registry.bindings(_GATED)] == ["w"]
    assert registry.bindings(_GATED)[0].excluded_platforms == frozenset()


def test_register_passes_the_exclusion_through() -> None:
    registry = ActionRegistry(authorization=AuthorizationPolicy())
    (binding,) = registry.register(
        _gated_spec(), _Spy("w"), module="a", excluded_platforms=["twitch"]
    )
    assert binding.excluded_platforms == frozenset({"twitch"})


@pytest.mark.parametrize("excluded", ["twitch", ["*"], [""], [3]])
def test_a_malformed_exclusion_is_refused(excluded: Any) -> None:
    registry = _registry_with()
    with pytest.raises(ContractError):
        registry.bind(_GATED, _Spy("w"), module="a", excluded_platforms=excluded)
    assert registry.bindings(_GATED) == ()


# -- the facade's rewrite --------------------------------------------------- #


@pytest.mark.asyncio
async def test_ac14_stand_ins_split_the_family_between_its_two_providers(
    tmp_path: Path,
) -> None:
    """AC14 (stand-ins): twitch reaches the second; kick and youtube the `*` owner."""

    _make(tmp_path, "star", STAR, _REGISTRAR_SOURCE)
    _make(tmp_path, "second", SECOND, _REGISTRAR_SOURCE)
    policy = AuthorizationPolicy()
    bus = EventBus()
    runtime = RuntimeContext(
        bus=bus,
        actions=ActionRegistry(authorization=policy),
        supervision=Supervision(bus),
        tasks=SupervisedTasks(),
    )
    loader = ModuleLoader(bus, tmp_path, context=runtime, environ={})
    spies = {name: _Spy(f"{name}-provider") for name in ("star", "second")}
    settings = {
        name: {"spec": _gated_spec(), "provider": spies[name], "bound": []}
        for name in spies
    }
    await loader.activate_enabled(
        {
            "enabled_modules": ["star", "second"],
            "modules": settings,
            "inputs": {"viewers": {"twitch": "second"}},
        }
    )

    assert _shape(tuple(settings["star"]["bound"])) == [("*", frozenset({"twitch"}))]
    assert _shape(tuple(settings["second"]["bound"])) == [("twitch", frozenset())]
    assert len(runtime.actions.bindings(_GATED)) == 2

    policy.grant(
        AuthorizationRule(
            rule_id="read",
            action_name=_GATED,
            destination=_ANY_CHAT,
            principals=(_OPERATOR,),
            natures=("read",),
            granted_permissions=("users.read",),
        )
    )
    executor = ActionExecutor(runtime.actions, policy, clock=lambda: 0.0)

    async def call(platform: str) -> str:
        observation = await executor.invoke(
            ActionCall(
                action_name=_GATED,
                action_version=1,
                arguments={"limit": 5},
                conversation_id="conv-1",
                run_id="run-1",
                call_id=f"call-{platform}",
                source_event_id="evt-1",
                destination=Destination(platform, "c", "chat"),
                principal=_OPERATOR,
                deadline=1000.0,
            )
        )
        return observation.status

    assert await call("twitch") == "success"
    assert (executor.provider_invocations, len(spies["second"].calls)) == (1, 1)
    assert spies["star"].calls == []
    assert await call("kick") == "success"
    assert await call("youtube") == "success"
    assert executor.provider_invocations == 3
    assert (len(spies["star"].calls), len(spies["second"].calls)) == (2, 1)


#: Every table the non-overlap invariant is checked over, with the bindings
#: the facade must produce for each module.
_INVARIANT_TABLES = {
    "no inputs": (SECOND, None, [("*", frozenset())], []),
    "twitch to second": (
        SECOND,
        {"viewers": {"twitch": "second"}},
        [("*", frozenset({"twitch"}))],
        [("twitch", frozenset())],
    ),
    "twitch to second, kick to none": (
        SECOND,
        {"viewers": {"twitch": "second", "kick": "none"}},
        [("*", frozenset({"kick", "twitch"}))],
        [("twitch", frozenset())],
    ),
    "star to second": (
        SECOND_WITH_STAR,
        {"viewers": {"*": "second"}},
        [],
        [("*", frozenset())],
    ),
    "second owns nothing": (
        SECOND,
        {"viewers": {"twitch": "star"}},
        [("*", frozenset())],
        [],
    ),
}


@pytest.mark.parametrize("table", sorted(_INVARIANT_TABLES))
def test_the_gated_bindings_never_overlap(table: str) -> None:
    second, inputs, star_shape, second_shape = _INVARIANT_TABLES[table]
    ownership = _resolve({"star": STAR, "second": second}, inputs)
    registry, bound = _register_both(ownership)

    assert _shape(bound["star"]) == star_shape
    assert _shape(bound["second"]) == second_shape
    bindings = registry.bindings(_GATED)
    assert len(bindings) == len(star_shape) + len(second_shape)
    for index, left in enumerate(bindings):
        for right in bindings[index + 1 :]:
            assert not left.overlaps(right.destination, right.excluded_platforms)
            assert not right.overlaps(left.destination, left.excluded_platforms)
    for platform in (*DOMAIN, "undeclared"):
        destination = Destination(platform, "c", "chat")
        covering = [b for b in bindings if b.covers(destination)]
        assert len(covering) <= 1, platform
        resolved = registry.resolve(_GATED, destination)
        assert resolved is (covering[0] if covering else None)
    if "none" in table:
        # The operator set (viewers, kick) unowned: no provider serves it.
        assert registry.resolve(_GATED, Destination("kick", "c", "chat")) is None


def test_a_module_owning_nothing_declares_but_does_not_bind() -> None:
    ownership = _resolve({"star": STAR, "second": SECOND})
    runtime = _bare_runtime()
    context = _gated_context(runtime, ownership, "second")
    assert context.actions.register(_gated_spec(), _Spy("p")) == ()
    assert _GATED in runtime.actions.discovered()
    assert runtime.actions.bindings(_GATED) == ()
    # Binding an already declared gated action binds nothing either.
    assert context.actions.bind(_GATED, _Spy("p")) == ()
    assert runtime.actions.bindings(_GATED) == ()


def test_a_concrete_owner_binds_through_bind_too() -> None:
    ownership = _resolve(
        {"star": STAR, "second": SECOND}, {"viewers": {"twitch": "second", "kick": "second"}}
    )
    runtime = _bare_runtime()
    context = _gated_context(runtime, ownership, "second")
    context.actions.declare(_gated_spec())
    bound = context.actions.bind(_GATED, _Spy("p"))
    assert _shape(bound) == [("kick", frozenset()), ("twitch", frozenset())]
    assert {b.module for b in bound} == {"second"}


def test_without_ownership_a_gated_action_binds_as_before() -> None:
    """A harness registering before any table exists keeps today's binding."""

    runtime = _bare_runtime()
    (binding,) = runtime.for_module("m").actions.register(_gated_spec(), _Spy("p"))
    assert binding.destination == _ANY_CHAT
    assert binding.excluded_platforms == frozenset()


def test_an_undeclared_module_binds_a_gated_action_as_before() -> None:
    """R3's compatibility rule: no ``input_families`` key, today's binding."""

    ownership = _resolve({"star": STAR}, {"viewers": {"kick": "none"}})
    runtime = _bare_runtime()
    context = runtime.for_module(
        "legacy", owned_inputs=ownership.view_for("legacy"), ownership=ownership
    )
    (binding,) = context.actions.register(_gated_spec(), _Spy("p"))
    assert binding.destination == _ANY_CHAT
    assert binding.excluded_platforms == frozenset()


def test_an_ungated_action_is_never_rewritten() -> None:
    ownership = _resolve({"star": STAR, "second": SECOND}, {"viewers": {"twitch": "second"}})
    runtime = _bare_runtime()
    context = _gated_context(runtime, ownership, "second")
    (binding,) = context.actions.register(_gated_spec("other.read"), _Spy("p"))
    assert binding.destination == _ANY_CHAT
    assert binding.excluded_platforms == frozenset()


def test_a_concrete_owner_keeps_the_requested_restrictions() -> None:
    """Ownership narrows the request; it never widens its platform or channel."""

    ownership = _resolve(
        {"star": STAR, "second": SECOND}, {"viewers": {"twitch": "second", "kick": "second"}}
    )
    runtime = _bare_runtime()
    context = _gated_context(runtime, ownership, "second")
    context.actions.declare(_gated_spec())
    (binding,) = context.actions.bind(
        _GATED, _Spy("p"), destinations=Destination("twitch", "channel-a", "chat")
    )
    assert binding.destination == Destination("twitch", "channel-a", "chat")
    narrowed = context.actions.bind(
        _GATED, _Spy("q"), destinations=Destination(WILDCARD, "channel-b", "chat")
    )
    assert [b.destination for b in narrowed] == [
        Destination("kick", "channel-b", "chat"),
        Destination("twitch", "channel-b", "chat"),
    ]
    # A requested platform owned elsewhere is dropped, not rebound.
    assert context.actions.bind(
        _GATED, _Spy("r"), destinations=Destination("youtube", "channel-a", "chat")
    ) == ()


def test_the_wildcard_owner_keeps_the_requested_restrictions() -> None:
    ownership = _resolve({"star": STAR, "second": SECOND}, {"viewers": {"twitch": "second"}})
    runtime = _bare_runtime()
    context = _gated_context(runtime, ownership, "star")
    context.actions.declare(_gated_spec())
    (channel,) = context.actions.bind(
        _GATED, _Spy("p"), destinations=Destination(WILDCARD, "channel-a", "chat")
    )
    assert channel.destination == Destination(WILDCARD, "channel-a", "chat")
    assert channel.excluded_platforms == frozenset({"twitch"})
    (kick,) = context.actions.bind(
        _GATED, _Spy("q"), destinations=Destination("kick", "channel-b", "chat")
    )
    assert (kick.destination, kick.excluded_platforms) == (
        Destination("kick", "channel-b", "chat"),
        frozenset(),
    )
    assert context.actions.bind(
        _GATED, _Spy("r"), destinations=Destination("twitch", "channel-b", "chat")
    ) == ()


def test_the_authorized_view_honours_the_exclusion() -> None:
    """A platform the wildcard excludes is not advertised through it."""

    registry = ActionRegistry(
        authorization=AuthorizationPolicy(
            rules=(
                AuthorizationRule(
                    rule_id="rule",
                    action_name=_GATED,
                    destination=_ANY_CHAT,
                    principals=(_OPERATOR,),
                    natures=("read",),
                    granted_permissions=("users.read",),
                ),
            )
        )
    )
    registry.register(
        _gated_spec(), _Spy("w"), module="w", excluded_platforms=("kick",)
    )
    registry.mark_ready("w")
    assert _GATED in registry.authorized(
        principal=_OPERATOR, destination=Destination("twitch", "c", "chat")
    )
    assert registry.resolve(_GATED, Destination("kick", "c", "chat")) is None
    assert _GATED not in registry.authorized(
        principal=_OPERATOR, destination=Destination("kick", "c", "chat")
    )


# --------------------------------------------------------------------------- #
# P6: engine anti-echo registration (R7; AC33 engine half — D6)
# --------------------------------------------------------------------------- #

from core.triggers import REASON_SELF_AUTHORED, TriggerEngine  # noqa: E402
from test_triggers import _audience_policy, _engine, _message, _registry  # noqa: E402
from test_triggers import CHAT_INPUT as _TRIGGER_INPUT  # noqa: E402

#: The companion's own account on the relayed input, as configured; the
#: events below carry it in other cases.
_RELAYED_IDENTITY = "Relay-Companion"


def _engine_without_identity() -> TriggerEngine:
    """An engine whose policy accepts everyone and which knows no identity —
    the shape ``core.main`` builds — so only the added identity can refuse."""

    registry = _registry()
    registry.configure(_TRIGGER_INPUT, _audience_policy("everyone"))
    return _engine(registry, companion_identity=None)


def test_ac33_an_added_identity_is_refused_by_the_anti_echo_invariant() -> None:
    """AC33 (engine half): before the call the author's event is accepted;
    after it, the same author in any case is refused through the existing
    anti-echo reason, before any rule."""

    engine = _engine_without_identity()
    assert engine.evaluate(_message(message_id="m0", author_id=_RELAYED_IDENTITY)).accepted

    engine.add_companion_identities([_RELAYED_IDENTITY])
    evaluated = engine.rule_evaluations
    for index, author in enumerate(
        (_RELAYED_IDENTITY, _RELAYED_IDENTITY.upper(), _RELAYED_IDENTITY.lower())
    ):
        decision = engine.evaluate(_message(message_id=f"m{index + 1}", author_id=author))
        assert decision.accepted is False
        assert decision.reason == REASON_SELF_AUTHORED
    assert engine.rule_evaluations == evaluated
    # Another viewer is still accepted: the identity is the companion's only.
    assert engine.evaluate(_message(message_id="m9", author_id="viewer-1")).accepted


def test_adding_the_same_identity_twice_is_a_no_op() -> None:
    engine = _engine_without_identity()
    engine.add_companion_identities([_RELAYED_IDENTITY])
    once = engine._companion_identities
    engine.add_companion_identities([_RELAYED_IDENTITY, _RELAYED_IDENTITY.upper()])
    engine.add_companion_identities(())
    assert engine._companion_identities == once == frozenset({_RELAYED_IDENTITY.casefold()})


def test_adding_identities_is_append_only() -> None:
    """The constructor's identity survives an addition, and the set accepts
    any iterable (a mapping's values included)."""

    engine = _engine(_registry(), companion_identity="built-in")
    engine.add_companion_identities({"p": "One", "q": "Two"}.values())
    assert engine._companion_identities == frozenset({"built-in", "one", "two"})


@pytest.mark.parametrize(
    ("identities", "field"),
    [
        ("Relay-Companion", "companion_identities"),
        (None, "companion_identities"),
        (["ok", ""], "companion_identities[1]"),
        (["ok", "  "], "companion_identities[1]"),
        (["ok", 7], "companion_identities[1]"),
    ],
)
def test_an_invalid_identity_is_refused_and_nothing_is_added(
    identities: Any, field: str
) -> None:
    engine = _engine_without_identity()
    with pytest.raises(ContractError) as caught:
        engine.add_companion_identities(identities)
    assert caught.value.field == field
    assert engine._companion_identities == frozenset()


def test_the_owned_triggers_facade_delegates_the_registration() -> None:
    """A module handed the guarded facade (P4) registers on the shared engine."""

    engine = _engine_without_identity()
    runtime = RuntimeContext(
        bus=EventBus(),
        actions=ActionRegistry(authorization=AuthorizationPolicy()),
        supervision=Supervision(EventBus()),
        tasks=SupervisedTasks(),
        triggers=engine,
        chat=_FakeChat(),
    )
    context = _intruder(runtime)
    assert context.triggers is not engine
    context.triggers.add_companion_identities([_RELAYED_IDENTITY])
    assert engine._companion_identities == frozenset({_RELAYED_IDENTITY.casefold()})


def test_the_registration_names_no_vendor_and_no_platform() -> None:
    text = (ROOT / "core" / "triggers.py").read_text(encoding="utf-8")
    start = text.index("    def add_companion_identities(")
    section = text[start : text.index("    def evaluate(", start)]
    assert _VENDOR_OR_BRIDGE.findall(section) == []
    for platform in ("twitch", "kick", "youtube"):
        assert platform not in section.lower(), platform


# --------------------------------------------------------------------------- #
# P18: AC14 with the real modules — `users` and the bridge share the read
# --------------------------------------------------------------------------- #

from test_automation_bridge import (  # noqa: E402
    FAKE_WAIT_SECONDS,
    PRINCIPAL,
    VIEWER_IDENTITY,
    FakeInstance,
    _Runs,
    _users_read_call,
)

_BRIDGE = "streamerbot"
_USERS_SETTINGS = {"max_channels": 4, "max_users_per_channel": 8, "max_age_seconds": 600.0}


@pytest.mark.asyncio
async def test_ac14_the_real_users_and_bridge_split_the_viewer_read() -> None:
    """AC14 (R3), real modules: ``users`` and the bridge enabled with
    ``inputs: {viewers: {twitch: <bridge>}}`` prepare without an
    ``AmbiguousBindingError``; a twitch ``users.read`` reaches the bridge's
    provider and a kick one the ``users`` provider — 1 invocation each, 0 of
    the other. The ``users`` page still reports ``observed_authors``."""

    async with FakeInstance() as fake:
        runs = _Runs(fake)
        await runs.start(
            url=fake.url,
            inputs={"viewers": {"twitch": _BRIDGE}},
            instance_identity=VIEWER_IDENTITY,
            modules={"users": _USERS_SETTINGS},
        )
        try:
            bindings = runs.context.actions.bindings(_GATED)
            assert sorted(
                (binding.module, binding.destination.platform, binding.excluded_platforms)
                for binding in bindings
            ) == [
                (_BRIDGE, "twitch", frozenset()),
                ("users", WILDCARD, frozenset({"twitch"})),
            ]
            runs.context.actions._authorization.grant(
                AuthorizationRule(
                    rule_id="read",
                    action_name=_GATED,
                    destination=_ANY_CHAT,
                    principals=(PRINCIPAL,),
                    natures=("read",),
                    granted_permissions=("users.read",),
                )
            )
            executor = runs.context.executor

            async def read(platform: str) -> ActionObservation:
                before = executor.provider_invocations
                observation = await asyncio.wait_for(
                    executor.invoke(
                        _users_read_call(
                            {"limit": 10},
                            platform=platform,
                            channel_id=VIEWER_IDENTITY[platform],
                            call_id=f"call-{platform}",
                        )
                    ),
                    FAKE_WAIT_SECONDS,
                )
                assert executor.provider_invocations - before == 1
                return observation

            twitch = await read("twitch")
            assert twitch.status == "success", twitch
            assert twitch.provenance["provider"] == "automation-bridge"
            assert twitch.result["coverage"]["kind"] == "active_viewers"
            assert len(fake.requests("GetActiveViewers")) == 1

            kick = await read("kick")
            assert kick.status == "success", kick
            assert kick.provenance["provider"] == "users-directory"
            assert kick.result["coverage"]["kind"] == "observed_authors"
            # The kick read never reached the bridge.
            assert len(fake.requests("GetActiveViewers")) == 1
        finally:
            await runs.stop()


# --------------------------------------------------------------------------- #
# P19: R1 no-change guarantees and the core vendor-name guard
# (R1, R3, R10; AC1, AC2, AC5, AC11, AC44 shipped profiles)
# --------------------------------------------------------------------------- #

import ast  # noqa: E402
import contextlib  # noqa: E402
import inspect  # noqa: E402
import itertools  # noqa: E402
from collections.abc import Callable  # noqa: E402

from conftest import (  # noqa: E402
    AC33_PENDING,
    ac33_node,
    baseline_numstat,
    ownership_rejection_probe,
)

from core.contracts import (  # noqa: E402
    COUNTER_INPUT_OWNERSHIP_REJECTIONS as _REJECTIONS,
    TRACE_MODULE_DEGRADED,
)
from test_automation_bridge import _Bridge  # noqa: E402
from test_examples import _sample_environ  # noqa: E402
from test_presence_pack import presence_environ  # noqa: E402
from test_twitch import SETTINGS as _TWITCH_SETTINGS  # noqa: E402
from test_twitch import FakeSession as _TwitchSession  # noqa: E402
from test_twitch import no_delay as _no_delay  # noqa: E402
from test_twitch import notification as _twitch_notification  # noqa: E402

#: The commit the phase starts from (the plan's phase base).
PHASE_BASE = "f900f27"

#: The 4 shipped profiles R1 freezes (AC1, AC2).
SHIPPED_PROFILES = (
    "config.yaml.example",
    "config.server.yaml.example",
    "agent.yaml.example",
    "presence.yaml.example",
)

#: AC1 baseline, captured once by running ``--check-config`` on each profile
#: at the phase base ``f900f27`` (not on the edited tree), with the
#: ``${NAME}`` values of ``tests/test_examples.py`` (``_sample_environ``):
#: profile -> (exit status, diagnostic lines). The presence profile's
#: ``watch.channels`` needs a ``<platform>/<channel_id>`` value the generic
#: sample does not give, so it was refused there before this phase too —
#: the guarantee is "the same output", which this pins.
CHECK_CONFIG_BASELINE_SAMPLE_VALUES: dict[str, tuple[int, list[str]]] = {
    "config.yaml.example": (0, []),
    "config.server.yaml.example": (0, []),
    "agent.yaml.example": (0, []),
    "presence.yaml.example": (
        2,
        ["module 'watch': field 'channels': entries must be <platform>/<channel_id>"],
    ),
}

#: AC1 baseline at ``f900f27`` with each profile's own test environment —
#: ``tests/test_examples.py`` values for the three phase-2 profiles, the
#: presence pack's ``presence_environ`` for the fourth — through the command
#: line: exit status and the whole stderr/stdout text. Every profile exits 0
#: with no output at all.
CHECK_CONFIG_BASELINE_OWN_VALUES: dict[str, tuple[int, str]] = {
    name: (0, "") for name in SHIPPED_PROFILES
}

_INPUTS_KEY_LINE = re.compile(r"^\s*inputs\s*:", re.MULTILINE)


def _own_environ(name: str, directory: Path) -> dict[str, str]:
    if name == "presence.yaml.example":
        return presence_environ(directory)
    return _sample_environ(ROOT / name)


def _is_ownership_diagnostic(line: str) -> bool:
    return line.startswith("inputs:") or "input ownership" in line.lower()


# -- AC1: --check-config gives the pre-phase output --------------------------- #


@pytest.mark.parametrize("name", SHIPPED_PROFILES)
@pytest.mark.asyncio
async def test_ac1_check_config_gives_the_pre_phase_diagnostics_with_the_sample_values(
    name: str,
) -> None:
    """AC1 (R1): with the ``${NAME}`` values of ``tests/test_examples.py``,
    each shipped profile's ``--check-config`` status and diagnostic list are
    exactly the ones captured at the phase base, and none is about input
    ownership."""

    diagnostics: list[str] = []
    status = await application.check_config(
        ROOT / name,
        environ=_sample_environ(ROOT / name),
        diagnostic_reporter=diagnostics.append,
    )
    assert (status, diagnostics) == CHECK_CONFIG_BASELINE_SAMPLE_VALUES[name]
    assert not any(_is_ownership_diagnostic(line) for line in diagnostics)


@pytest.mark.parametrize("name", SHIPPED_PROFILES)
def test_ac1_main_check_config_exits_zero_with_the_pre_phase_output(
    name: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """AC1 (R1), through the command line: ``--config <profile>
    --check-config`` with the profile's test environment exported exits 0
    and prints exactly what it printed at the phase base — nothing — so in
    particular no ownership diagnostic; the bridge is not enabled."""

    path = ROOT / name
    profile = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert _BRIDGE not in profile["enabled_modules"]
    for variable, value in _own_environ(name, tmp_path).items():
        monkeypatch.setenv(variable, value)

    status = application.main(["--config", str(path), "--check-config"])
    captured = capsys.readouterr()

    output = captured.err + captured.out
    assert (status, output) == CHECK_CONFIG_BASELINE_OWN_VALUES[name]
    assert not any(_is_ownership_diagnostic(line) for line in output.splitlines())


# -- AC2: the shipped profiles are untouched ---------------------------------- #


@pytest.mark.parametrize("name", SHIPPED_PROFILES)
def test_ac2_no_shipped_profile_names_the_bridge_or_an_inputs_key(name: str) -> None:
    """AC2 (R1): no shipped profile contains the bridge's module name (any
    spelling of the vendor's) or an ``inputs:`` key at any depth."""

    text = (ROOT / name).read_text(encoding="utf-8")
    assert _VENDOR_OR_BRIDGE.findall(text) == []
    assert _BRIDGE not in text.lower()
    assert _INPUTS_KEY_LINE.findall(text) == []
    assert "inputs" not in yaml.safe_load(text)
    # The key pattern does catch the key it guards against, nested or not.
    assert _INPUTS_KEY_LINE.search("modules: {}\ninputs:\n  chat: {}\n")
    assert _INPUTS_KEY_LINE.search("a:\n  inputs: {}\n")


def test_ac2_the_shipped_profiles_have_zero_changed_lines_since_the_phase_base() -> None:
    """AC2 (R1): ``git diff --stat <phase base>`` over the 4 shipped profiles
    — the working tree against ``f900f27`` — reports 0 changed lines.

    Read in-process (AC33, P15a): the profiles as they were at the phase
    base are committed under ``tests/fixtures/baselines/f900f27/`` (taken
    with ``git show``, the hash in its ``SOURCE``), and
    :func:`conftest.baseline_numstat` compares them byte for byte with the
    working tree — the same claim as an empty ``--numstat``."""

    diff = baseline_numstat(PHASE_BASE, SHIPPED_PROFILES)
    assert diff.returncode == 0, diff.stderr
    assert diff.stdout.strip() == ""
    numstat = baseline_numstat(PHASE_BASE, SHIPPED_PROFILES)
    assert numstat.returncode == 0 and numstat.stdout.strip() == ""
    # Each profile exists at the phase base, so an empty diff is no accident
    # of a path the base never had.
    for name in SHIPPED_PROFILES:
        assert baseline_numstat(PHASE_BASE, [name]).returncode == 0, name


# -- AC44, shipped half: no undeclared publisher meets a resolved owner ------- #

#: The event type whose payload carries a (family, platform) pair (D1).
_FAMILY_EVENT = "channel.chat.message"

#: Undeclared shipped modules that produce the family event with a payload
#: kind no family covers: ``audio_input`` publishes only ``speech`` (R7), so
#: no (family, platform) pair it builds can meet a resolved owner.
_NO_FAMILY_PRODUCERS = frozenset({"audio_input"})


def _manifest_of(name: str) -> dict[str, Any]:
    loaded = yaml.safe_load(
        (ROOT / "modules" / name / "module.yaml").read_text(encoding="utf-8")
    )
    assert isinstance(loaded, dict)
    return loaded


@pytest.mark.parametrize("name", SHIPPED_PROFILES)
@pytest.mark.asyncio
async def test_ac44_no_undeclared_shipped_publisher_meets_a_resolved_owner(
    name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC44 (R3, R1), shipped half: the ownership ``--check-config`` resolves
    for each shipped profile — through the real discovery and the real
    resolver — gives an owner only to declaring modules, and every enabled
    module with no ``input_families`` key that may publish a family-carrying
    event finds no owner and no ``none`` on any row of the families it could
    publish, so its guard admits every pair it publishes as before."""

    resolved: list[tuple[list[str], Any]] = []
    real = application.resolve_input_ownership

    def spy(enabled: Any, declarations: Any, inputs: Any, **kwargs: Any) -> Any:
        ownership = real(enabled, declarations, inputs, **kwargs)
        resolved.append((list(enabled), ownership))
        return ownership

    monkeypatch.setattr(application, "resolve_input_ownership", spy)
    diagnostics: list[str] = []
    status = await application.check_config(
        ROOT / name,
        environ=_own_environ(name, tmp_path),
        diagnostic_reporter=diagnostics.append,
    )
    assert (status, diagnostics) == (0, [])
    assert len(resolved) == 1
    enabled, ownership = resolved[0]

    declaring = {
        module for module in enabled if "input_families" in _manifest_of(module)
    }
    undeclared = [module for module in enabled if module not in declaring]
    # The bridge is not enabled; every profile enables an undeclared module.
    assert _BRIDGE not in enabled
    assert undeclared

    rows = ownership.rows()
    # Each family's wildcard row is always in the domain.
    assert {("chat", WILDCARD), ("cues", WILDCARD), ("viewers", WILDCARD)} <= set(rows)
    for family, platform in rows:
        owner = ownership.owner(family, platform)
        assert owner is None or owner in declaring, (family, platform, owner)
        assert not ownership.is_set_none(family, platform)
    assert not any(owner_origin == "explicit" for owner_origin in (
        ownership.origin(family, platform) for family, platform in rows
    ))

    for module in undeclared:
        produced = _manifest_of(module).get("produces") or []
        if module in _NO_FAMILY_PRODUCERS:
            continue
        families = ("chat", "cues") if _FAMILY_EVENT in produced else ()
        for family in families:
            for row_family, platform in rows:
                if row_family == family:
                    assert ownership.owner(family, platform) is None, (module, platform)
        # An undeclared module publishes no family pair in the shipped set.
        assert _FAMILY_EVENT not in produced, module


#: The shipped-profile activations of ``tests/test_examples.py`` the probe
#: runs beside every scenario of ``tests/test_integration.py``.
_EXAMPLE_ACTIVATIONS = (
    "test_example_trigger_policies_give_the_two_channels_two_distinct_policies",
    "test_profile_grants_exactly_its_provided_action_set",
    "test_ac30_the_chat_only_profile_starts_and_runs_the_phase_1_scenario",
    "test_ac12_every_shipped_example_passes_check_config",
)


def _test_names(module: Any) -> list[str]:
    """The test functions *module*'s source defines at top level, in order."""

    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    return [
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
    ]


def _parameter_sets(function: Callable[..., Any]) -> list[dict[str, Any]]:
    """Every argument set the function's ``parametrize`` marks expand to."""

    axes: list[list[dict[str, Any]]] = []
    for mark in getattr(function, "pytestmark", []):
        if mark.name != "parametrize":
            continue
        names, values = mark.args[0], mark.args[1]
        if isinstance(names, str):
            names = [name.strip() for name in names.split(",") if name.strip()]
        rows = []
        for value in values:
            value = getattr(value, "values", value)
            if len(names) == 1 and not (isinstance(value, tuple) and len(value) == 1):
                value = (value,)
            rows.append(dict(zip(names, value)))
        axes.append(rows)
    return [
        {key: item for row in combination for key, item in row.items()}
        for combination in itertools.product(*axes)
    ]


def _run_scenario(
    function: Callable[..., Any], arguments: dict[str, Any], scratch: Path
) -> None:
    """Run one test function in-process with the fixtures it names."""

    with pytest.MonkeyPatch.context() as monkeypatch:
        fixtures = {"tmp_path": scratch, "monkeypatch": monkeypatch}
        for name in inspect.signature(function).parameters:
            if name not in arguments:
                assert name in fixtures, f"{function.__name__}: fixture {name} is not provided"
                arguments[name] = fixtures[name]
        scratch.mkdir(parents=True)
        result = function(**arguments)
        if inspect.iscoroutine(result):
            asyncio.run(result)


def test_ac44_the_integration_scenarios_count_zero_ownership_rejections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC44 (R3, R1), shipped half: ``tests/test_integration.py`` and the
    shipped-profile activations of ``tests/test_examples.py``, run whole
    with a counter probe, pass and record ``input_ownership_rejections == 0``
    while ownership guards were built and consulted — the shipped modules'
    declarations leave every pair they publish admitted.

    Run in-process (AC33, P15a), not in a child ``pytest``: the scenarios are
    read from each file with ``ast`` and called with their parameter sets,
    under counting wrappers of the input guard and of the rejection counter.
    An integration node still in ``AC33_PENDING`` opens a socket or starts a
    process, so it is left out here and runs under the same probe in its own
    execution (``tests/test_integration.py``'s ``_ac44_pending_probe``)
    until its conversion removes it and it joins this run."""

    import test_examples
    import test_integration

    seen = ownership_rejection_probe(monkeypatch)

    scenarios = [
        (test_integration, name)
        for name in _test_names(test_integration)
        if ac33_node(f"tests/test_integration.py::{name}") not in AC33_PENDING
    ] + [(test_examples, name) for name in _EXAMPLE_ACTIVATIONS]
    assert set(_EXAMPLE_ACTIVATIONS) <= set(_test_names(test_examples))
    failures: list[str] = []
    ran = 0
    for module, name in scenarios:
        function = getattr(module, name)
        for arguments in _parameter_sets(function) or [{}]:
            ran += 1
            label = f"{module.__name__}::{name}{sorted(arguments.items())}"
            try:
                _run_scenario(function, dict(arguments), tmp_path / f"scenario-{ran}")
            except pytest.skip.Exception:
                continue
            except (Exception, pytest.fail.Exception) as error:
                failures.append(f"{label}: {type(error).__name__}: {error}")
    assert ran > 0
    assert failures == [], "\n".join(failures)
    assert seen["guards"] > 0 and seen["admits"] > 0, seen
    assert seen["rejections"] == 0 and seen["counted"] == 0, seen


# -- AC5 end to end: the bridge owns (chat, twitch), the instance is down ---- #


def _keys_anywhere(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            found.add(key)
            found |= _keys_anywhere(item)
    elif isinstance(value, list):
        for item in value:
            found |= _keys_anywhere(item)
    return found


def test_ac5_the_bridge_settings_schema_has_no_required_key() -> None:
    """AC5 (R1): the bridge's settings schema has no ``required`` — the
    switch another module uses to turn an unavailable dependency into a
    failed startup — at any depth."""

    schema = _manifest_of(_BRIDGE)["settings_schema"]
    assert schema and "properties" in schema
    assert "required" not in _keys_anywhere(schema)


@pytest.mark.asyncio
async def test_ac5_a_twitch_event_is_dropped_while_the_owning_bridge_is_down() -> None:
    """AC5 (R1, R3), end to end through the real loader and coordinator:
    the real bridge owns (``chat``, twitch) by an explicit ``inputs`` entry,
    its fake instance has been stopped, and the real ``twitch`` module is
    enabled beside it. Startup succeeds with the bridge degraded
    (``unreachable``); a chat notification driven into the twitch handler
    (P8's driver) reaches no subscriber and is counted once — the native
    module is never a fallback."""

    async with FakeInstance() as fake:
        url = fake.url
    # The instance is stopped: nothing listens at its URL any more.

    bridge = _Bridge()
    session = _TwitchSession([])
    twitch_diagnostics: list[str] = []
    await bridge.activate(
        url=url,
        inputs={"chat": {"twitch": _BRIDGE}},
        companion_identities={"twitch": _TWITCH_SETTINGS["bot_user_id"]},
        modules={
            "twitch": {
                **_TWITCH_SETTINGS,
                "_session_factory": lambda: session,
                "_retry_delay": _no_delay,
                "diagnostic_reporter": twitch_diagnostics.append,
            }
        },
    )
    assert bridge.coordinator is not None
    report = await asyncio.wait_for(bridge.coordinator.start(), FAKE_WAIT_SECONDS)
    try:
        assert report.status == 0, report.diagnostics
        assert (TRACE_MODULE_DEGRADED, "unreachable") in bridge.health()
        # The native module opened no ingestion source: it does not own the pair.
        assert session.ws_calls == []
        assert twitch_diagnostics == ["twitch: chat ingestion owned elsewhere"]

        delivered: list[dict[str, Any]] = []
        bridge.context.bus.subscribe(_FAMILY_EVENT, delivered.append, order=-100)
        twitch = next(
            activation.handle
            for activation in bridge.coordinator._modules
            if activation.name == "twitch"
        )
        before = bridge.context.supervision.snapshot()[_REJECTIONS]
        await twitch._publish_notification(_twitch_notification("m-ac5", "hello Companion"))

        assert delivered == []
        assert not any(trace["type"] == _FAMILY_EVENT for trace in bridge.traces)
        assert bridge.context.supervision.snapshot()[_REJECTIONS] == before + 1
    finally:
        with contextlib.suppress(Exception):
            await bridge.stop()


# -- AC11: core names neither the vendor nor the bridge; ownership code no platform #

_PLATFORM_NAMES = ("twitch", "kick", "youtube")

#: The ownership code of the two kernel files AC11 constrains, by name.
_OWNERSHIP_CODE = {
    "loader.py": (
        "_manifest_input_families",
        "_Row",
        "InputOwnership",
        "_owner_text",
        "_module_text",
        "_inputs_line",
        "resolve_input_ownership",
        "_declares_owned_inputs",
    ),
    "runtime.py": (
        "ModuleActions",
        "OwnedInputs",
        "_InputGuard",
        "_OwnedBus",
        "_OwnedTriggers",
        "_OwnedChat",
        "_ModuleInputs",
        "for_module",
    ),
}


def _core_files() -> list[Path]:
    return sorted(
        path
        for path in (ROOT / "core").rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    )


def test_ac11_no_file_under_core_names_the_vendor_or_the_bridge_module() -> None:
    """AC11 (R2, R10): a case-insensitive grep of every file under ``core/``
    (``__pycache__`` excluded) for the bridge module name and the vendor's
    name returns 0 matches."""

    files = _core_files()
    assert any(path.name == "loader.py" for path in files)
    assert not any("__pycache__" in path.parts for path in files)
    matches = [
        (str(path.relative_to(ROOT)), match)
        for path in files
        for match in _VENDOR_OR_BRIDGE.findall(path.read_text(encoding="utf-8", errors="replace"))
    ]
    assert matches == []
    for spelling in (_BRIDGE, _BRIDGE.upper(), "Streamer.bot", "streamer bot", "Streamer_Bot"):
        assert _VENDOR_OR_BRIDGE.search(spelling), spelling


def _platform_literals(tree: ast.AST) -> list[str]:
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and any(platform in node.value.lower() for platform in _PLATFORM_NAMES)
    ]


@pytest.mark.parametrize("filename", sorted(_OWNERSHIP_CODE))
def test_ac11_the_ownership_code_has_no_platform_name_literal(filename: str) -> None:
    """AC11 (R2, R10): every ownership function and class of
    ``core/loader.py`` and ``core/runtime.py`` — found by name in the AST, all
    of them present — holds no string constant naming twitch, kick or
    youtube, in any case and as any substring (docstrings included)."""

    tree = ast.parse((ROOT / "core" / filename).read_text(encoding="utf-8"))
    wanted = set(_OWNERSHIP_CODE[filename])
    found: dict[str, list[ast.AST]] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name in wanted:
                found.setdefault(node.name, []).append(node)
    assert set(found) == wanted, wanted - set(found)
    offending = {
        name: literals
        for name, nodes in found.items()
        for literals in [[value for node in nodes for value in _platform_literals(node)]]
        if literals
    }
    assert offending == {}
    # The scan does see a platform literal where one is.
    planted = ast.parse('def f():\n    return {"family": "chat", "platform": "Twitch"}\n')
    assert _platform_literals(planted) == ["Twitch"]


# --------------------------------------------------------------------------- #
# N3 (gate 4): one protection contract reaches every consumer that emits
# ownership data — the configuration UI's page and the rejection trace
# --------------------------------------------------------------------------- #

_N3_PUBLIC = "review-public-room"


def _n3_configuration() -> dict[str, Any]:
    """N3's configuration: complete accepted settings, both credentials
    declared, an ``inputs`` key that is one credential behind the other's
    prefix, and a non-secret control key."""

    from test_main import _finite_limits

    return {
        "modules_directory": str((ROOT / "modules").resolve()),
        "enabled_modules": [_BRIDGE, "twitch"],
        "modules": {
            _BRIDGE: {
                "url": "ws://127.0.0.1:9/",
                "companion_name": "Companion",
                "password": _N2_SECRET,
            },
            "twitch": {
                "client_id": "review-client-id",
                "client_secret": "review-native-key",
                "access_token": _N2_OTHER,
                "broadcaster_id": "channel-1",
                "bot_user_id": "bot-1",
                "companion_name": "Companion",
            },
        },
        "limits": _finite_limits(),
        "inputs": {"chat": {_N2_KEY: "none", _N3_PUBLIC: "none"}},
    }


def _n3_fragments_absent(text: str) -> None:
    for fragment in (*_n1_fragments(_N2_SECRET), *_n1_fragments(_N2_OTHER)):
        assert fragment not in text, fragment


def test_n3_the_ownership_page_and_the_rejection_trace_leave_no_credential_fragment() -> None:
    """N3, R8: the gate-4 reproduction end to end. The configuration passes
    the real ``check_config`` with status 0; the authenticated UI request
    renders the ownership table with neither credential nor either half of
    either in any row (cell text and data attribute); the rejection trace
    carries no fragment in any key or value and the chat publication is
    still refused. The non-secret controls keep their exact full text, so
    the protection is not over-broad."""

    from unittest.mock import patch

    import core.config_ui as ui_app
    from test_config_ui import _get, _logged_in, _section

    config = _n3_configuration()

    async def checked() -> tuple[int, list[str]]:
        lines: list[str] = []
        with patch.object(application, "read_base", return_value=config), patch.object(
            application, "read_overlay", return_value={}
        ):
            status = await application.check_config(
                "config.yaml.example", environ={}, diagnostic_reporter=lines.append
            )
        return status, lines

    assert asyncio.run(checked()) == (0, [])

    with patch.object(ui_app, "read_base", return_value=config), patch.object(
        ui_app, "read_overlay", return_value={}
    ):
        ui = ui_app.ConfigUI(
            ui_app.UISettings.from_argv(["--config", "config.yaml.example"]), environ={}
        )
        response = _get(ui, "/core", _logged_in(ui))
    assert response.status == 200
    assert {_N2_SECRET, _N2_OTHER} <= ui.secret_values
    body = response.body.decode()
    section = _section(body, "input-ownership")
    rows = re.findall(r"<tr.*?</tr>", section)
    _n3_fragments_absent(body)
    _n3_fragments_absent("\n".join(rows))
    hidden = ui_app.REDACTED
    assert (
        f'<tr data-input-family="chat" data-input-platform="{hidden}"><td>chat</td>'
        f'<td>{hidden}</td><td class="owner">none</td><td class="origin">explicit</td>'
        '<td class="candidates"></td></tr>'
    ) in rows
    assert (
        f'<tr data-input-family="chat" data-input-platform="{_N3_PUBLIC}"><td>chat</td>'
        f'<td>{_N3_PUBLIC}</td><td class="owner">none</td><td class="origin">explicit</td>'
        '<td class="candidates"></td></tr>'
    ) in rows

    async def traced() -> tuple[list[Any], list[Any], Any, Any]:
        runtime, engine, chat = _guarded_runtime()
        runtime.supervision.redact((_N2_SECRET, _N2_OTHER))
        context = _intruder(runtime)
        seen: list[Any] = []

        async def observe(event: Any) -> None:
            seen.append(event)

        runtime.bus.subscribe("**", observe)
        results = []
        for platform in (_N2_KEY, _N3_PUBLIC):
            event = _chat_event(platform)
            results.append(
                await context.bus.publish(event["type"], event["payload"], event["metadata"])
            )
        return seen, results, runtime.supervision.snapshot(), chat

    seen, results, snapshot, chat = asyncio.run(traced())
    assert results == [None, None]
    assert chat.appended == []
    assert snapshot[COUNTER_INPUT_OWNERSHIP_REJECTIONS] == 2
    assert [event["type"] for event in seen] == [TRACE_INPUT_OWNERSHIP_REJECTED] * 2
    assert [event["payload"] for event in seen] == [
        {"module": "intruder", "family": "chat", "platform": contracts.REDACTION_PLACEHOLDER},
        {"module": "intruder", "family": "chat", "platform": _N3_PUBLIC},
    ]
    trace_text = "\n".join(
        f"{key}={value}" for event in seen for key, value in event["payload"].items()
    )
    _n3_fragments_absent(trace_text)
