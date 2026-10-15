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
