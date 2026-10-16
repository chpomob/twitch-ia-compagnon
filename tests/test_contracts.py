import pytest

import dataclasses
import re
from pathlib import Path

import yaml

from core.contracts import (
    EVENT_KIND_DEFAULT,
    EVENT_KIND_SET,
    EVENT_KINDS,
    IMAGE_REF_FIELDS,
    PLATFORM_NOTICE_KINDS,
    ActionObservation,
    ActionSpec,
    ContractError,
    Destination,
    TriggerPolicy,
    SchemaError,
    TriggerRule,
    sanitize_trace,
    validate_against_schema,
    validate_event_kind,
    validate_parts,
    validate_schema,
)
from core.loader import ModuleLoader, ModuleLoadError
from core.runtime import RUNTIME_API


def _spec(result_schema: dict) -> ActionSpec:
    return ActionSpec(
        name="demo.pick",
        version=1,
        description="pick a value",
        argument_schema={"type": "object"},
        result_schema=result_schema,
        nature="read",
        required_permissions=(),
        supported_destinations=(Destination("twitch", "c1", "chat"),),
        timeout_seconds=1.0,
        idempotency="none",
    )


def test_enum_sees_through_frozen_containers() -> None:
    spec = _spec({"enum": [[1, 2], {"enabled": True}]})

    # The spec froze both members, yet the caller submits plain JSON values.
    spec.validate_result([1, 2])
    spec.validate_result({"enabled": True})

    with pytest.raises(ContractError):
        spec.validate_result([1, 2, 3])
    with pytest.raises(ContractError):
        spec.validate_result({"enabled": True, "extra": 1})


def test_enum_keeps_booleans_distinct_when_nested() -> None:
    with pytest.raises(ContractError):
        validate_against_schema({"enabled": 1}, {"enum": [{"enabled": True}]}, label="x")
    with pytest.raises(ContractError):
        validate_against_schema([True], {"enum": [[1]]}, label="x")
    with pytest.raises(ContractError):
        validate_against_schema(1, {"enum": [True]}, label="x")

    validate_against_schema([True], {"enum": [[True]]}, label="x")


def test_numeric_bounds_keep_integer_precision() -> None:
    schema = {"type": "integer", "maximum": 9007199254740992}
    validate_against_schema(9007199254740992, schema, label="x")
    with pytest.raises(ContractError):
        # Both sides collapse onto the same float, so only an exact comparison
        # rejects this value.
        validate_against_schema(9007199254740993, schema, label="x")

    schema = {"type": "integer", "minimum": -9007199254740992}
    validate_against_schema(-9007199254740992, schema, label="x")
    with pytest.raises(ContractError):
        validate_against_schema(-9007199254740993, schema, label="x")


def test_bounds_still_reject_non_numbers() -> None:
    with pytest.raises(ContractError):
        validate_against_schema("2", {"maximum": 2}, label="x")
    with pytest.raises(ContractError):
        validate_against_schema(True, {"maximum": 2}, label="x")


# --------------------------------------------------------------------------- #
# String length bounds (phase 5, P12F1)
# --------------------------------------------------------------------------- #


def test_string_length_bounds_are_enforced() -> None:
    schema = {"type": "string", "minLength": 1, "maxLength": 128}
    validate_against_schema("a", schema, label="x")
    validate_against_schema("a" * 128, schema, label="x")
    # Counted in code points, not bytes.
    validate_against_schema("é" * 128, schema, label="x")

    with pytest.raises(ContractError) as short:
        validate_against_schema("", schema, label="x")
    assert short.value.field == "x.minLength"
    assert "at least 1" in str(short.value)

    with pytest.raises(ContractError) as long:
        validate_against_schema("a" * 129, schema, label="x")
    assert long.value.field == "x.maxLength"
    assert "at most 128" in str(long.value)


def test_string_length_bounds_are_enforced_on_nested_nodes() -> None:
    schema = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "minLength": 1, "maxLength": 128},
            "tags": {"type": "array", "items": {"type": "string", "maxLength": 3}},
        },
        "required": ["action"],
        "additionalProperties": False,
    }
    validate_against_schema({"action": "go", "tags": ["abc"]}, schema, label="args")

    with pytest.raises(ContractError) as empty:
        validate_against_schema({"action": ""}, schema, label="args")
    assert empty.value.field == "args.action.minLength"

    with pytest.raises(ContractError) as long:
        validate_against_schema({"action": "a" * 129}, schema, label="args")
    assert long.value.field == "args.action.maxLength"

    with pytest.raises(ContractError) as item:
        validate_against_schema({"action": "go", "tags": ["ok", "toolong"]}, schema, label="args")
    assert item.value.field == "args.tags[1].maxLength"


def test_run_action_schema_is_accepted_by_an_action_spec() -> None:
    schema = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "minLength": 1, "maxLength": 128},
            "arguments": {"type": "object"},
        },
        "required": ["action"],
        "additionalProperties": False,
    }
    spec = dataclasses.replace(_spec({"type": "object"}), argument_schema=schema)
    spec.validate_arguments({"action": "a" * 128, "arguments": {}})
    for bad in ("", "a" * 129):
        with pytest.raises(ContractError):
            spec.validate_arguments({"action": bad})


@pytest.mark.parametrize(
    ("schema", "field"),
    [
        ({"type": "string", "minLength": True}, "s.minLength"),
        ({"type": "string", "maxLength": False}, "s.maxLength"),
        ({"type": "string", "minLength": -1}, "s.minLength"),
        ({"type": "string", "maxLength": -5}, "s.maxLength"),
        ({"type": "string", "maxLength": 2.0}, "s.maxLength"),
        ({"type": "string", "minLength": 3, "maxLength": 2}, "s.minLength"),
        (
            {"type": "object", "properties": {"a": {"type": "string", "minLength": -1}}},
            "s.properties.a.minLength",
        ),
        ({"type": "array", "items": {"type": "string", "maxLength": True}}, "s.items.maxLength"),
    ],
)
def test_malformed_length_bounds_are_refused(schema: dict, field: str) -> None:
    with pytest.raises(SchemaError) as refused:
        validate_schema(schema, label="s")
    assert refused.value.field == field


@pytest.mark.parametrize("declared", [None, "integer", "number", "array", "object", "boolean", "null"])
@pytest.mark.parametrize("bound", ["minLength", "maxLength"])
def test_length_bound_on_a_non_string_node_is_refused(declared: str | None, bound: str) -> None:
    schema: dict = {bound: 1}
    if declared is not None:
        schema["type"] = declared
    with pytest.raises(SchemaError) as refused:
        validate_schema(schema, label="s")
    assert refused.value.field == f"s.{bound}"
    assert "string" in refused.value.reason


def test_schemas_without_length_bounds_validate_as_before() -> None:
    schema = {
        "type": "object",
        "properties": {"n": {"type": "integer", "minimum": 0, "maximum": 3}},
        "required": ["n"],
        "additionalProperties": False,
    }
    validate_against_schema({"n": 2}, schema, label="x")
    with pytest.raises(ContractError) as high:
        validate_against_schema({"n": 4}, schema, label="x")
    assert high.value.field == "x.n"
    # A string node with no length bound still accepts any length.
    validate_against_schema("", {"type": "string"}, label="x")
    validate_against_schema("a" * 10_000, {"type": "string"}, label="x")
    # The vocabulary stays closed: other keywords are still refused.
    for keyword, value in (("pattern", "^a$"), ("minItems", 1), ("format", "uri")):
        with pytest.raises(SchemaError) as refused:
            validate_schema({"type": "string", keyword: value}, label="s")
        assert refused.value.field == f"s.{keyword}"
        assert "unsupported schema keyword" in refused.value.reason


def test_secret_never_reaches_a_trace_diagnostic() -> None:
    # The path is built from the redacted key: a raw one would carry the
    # configured credential out through the exception message.
    with pytest.raises(ContractError) as reserved:
        sanitize_trace({"s3cret": {"prompt": "x"}}, secrets=("s3cret",))
    assert "s3cret" not in str(reserved.value)

    with pytest.raises(ContractError) as collision:
        sanitize_trace({"a-s3cret": 1, "b-s3cret": 2}, secrets=("a-", "b-"))
    assert "a-" not in str(collision.value)


def test_malformed_combination_is_a_contract_error() -> None:
    # YAML can supply a list or a mapping here; both must name the field
    # rather than fail on an unhashable set lookup.
    for combination in ([], {"any_of": True}, 7):
        with pytest.raises(ContractError):
            TriggerPolicy((TriggerRule("keyword"),), combination=combination)


# --------------------------------------------------------------------------- #
# Observation parts (phase 1, R4, AC21)
# --------------------------------------------------------------------------- #


def _image_ref(**overrides: object) -> dict:
    part = {
        "type": "image_ref",
        "attachment_id": "att-1",
        "content_type": "image/png",
        "size": 1024,
        "width": 16,
        "height": 9,
        "captured_at": 12.5,
        "provider_id": "capture",
    }
    part.update(overrides)
    return part


def _observation(parts: object) -> ActionObservation:
    return ActionObservation("success", {"provider_id": "p"}, {"ok": True}, None, parts)


def test_observation_without_parts_constructs_as_before() -> None:
    # Positional construction predates ``parts``; the field is appended last so
    # every existing constructor keeps its meaning and gets an empty tuple.
    observation = ActionObservation("success", {"provider_id": "p"}, {"ok": True})
    assert observation.parts == ()
    assert observation.result == {"ok": True}

    failed = ActionObservation("error", {"provider_id": "p"}, None, {"code": "x", "message": ""})
    assert failed.parts == ()


def test_parts_accept_text_and_image_ref_and_are_frozen() -> None:
    observation = _observation([{"type": "text", "text": "héllo"}, _image_ref()])

    assert isinstance(observation.parts, tuple)
    assert len(observation.parts) == 2
    assert observation.parts[0]["text"] == "héllo"
    assert observation.parts[1]["captured_at"] == 12.5

    with pytest.raises(TypeError):
        observation.parts[0]["text"] = "changed"  # type: ignore[index]
    with pytest.raises(TypeError):
        observation.parts[1]["size"] = 1  # type: ignore[index]
    with pytest.raises(AttributeError):
        observation.parts = ()  # type: ignore[misc]


def test_unknown_part_type_names_the_field() -> None:
    with pytest.raises(ContractError) as refused:
        _observation([{"type": "text", "text": "ok"}, {"type": "audio", "text": "x"}])
    assert refused.value.field == "ActionObservation.parts[1].type"

    with pytest.raises(ContractError) as missing:
        _observation([{"text": "no type"}])
    assert missing.value.field == "ActionObservation.parts[0].type"


@pytest.mark.parametrize("value", [[], {}, ["text"], 3, None])
def test_unhashable_or_mistyped_discriminators_are_a_contract_error(value: object) -> None:
    # A list or dict discriminator is unhashable: a bare membership test would
    # leak a TypeError instead of the ContractError naming the field.
    with pytest.raises(ContractError) as refused_type:
        validate_parts([{"type": value, "text": "x"}])
    assert refused_type.value.field == "ActionObservation.parts[0].type"

    with pytest.raises(ContractError) as refused_content_type:
        validate_parts([_image_ref(content_type=value)])
    assert refused_content_type.value.field == "ActionObservation.parts[0].content_type"


def test_text_part_over_the_bound_is_refused_through_validate_parts() -> None:
    # The byte bound is the executor's (R4); construction only checks shape,
    # so the same 4-byte text passes here and fails against a 3-byte bound.
    parts = [{"type": "text", "text": "abcd"}]
    assert _observation(parts).parts[0]["text"] == "abcd"

    validate_parts(parts, max_text_bytes=4)
    with pytest.raises(ContractError) as refused:
        validate_parts(parts, max_text_bytes=3)
    assert refused.value.field == "ActionObservation.parts[0].text"

    # The bound counts UTF-8 bytes, not code points.
    with pytest.raises(ContractError):
        validate_parts([{"type": "text", "text": "éé"}], max_text_bytes=3)
    validate_parts([{"type": "text", "text": "éé"}], max_text_bytes=4)


def test_text_part_that_cannot_be_encoded_as_utf8_is_a_shape_failure() -> None:
    # A lone surrogate is a ``str`` with no UTF-8 form: it cannot be sized
    # under R4, so it is refused at construction and under a bound alike —
    # as a ContractError naming the field, never a UnicodeEncodeError.
    parts = [{"type": "text", "text": "ok\ud800"}]
    with pytest.raises(ContractError) as at_construction:
        _observation(parts)
    assert at_construction.value.field == "ActionObservation.parts[0].text"

    with pytest.raises(ContractError) as under_bound:
        validate_parts(parts, max_text_bytes=1024)
    assert under_bound.value.field == "ActionObservation.parts[0].text"
    assert "UTF-8" in str(under_bound.value)


@pytest.mark.parametrize("name", IMAGE_REF_FIELDS)
def test_image_ref_missing_any_of_its_seven_fields_names_it(name: str) -> None:
    part = _image_ref()
    del part[name]
    with pytest.raises(ContractError) as refused:
        _observation([part])
    assert refused.value.field == f"ActionObservation.parts[0].{name}"


def test_image_ref_field_types_are_checked() -> None:
    cases = {
        "attachment_id": "",
        "provider_id": 7,
        "content_type": "image/gif",
        "size": 0,
        "width": "16",
        "height": 0,
        "captured_at": float("inf"),
    }
    for name, value in cases.items():
        with pytest.raises(ContractError) as refused:
            _observation([_image_ref(**{name: value})])
        assert refused.value.field == f"ActionObservation.parts[0].{name}", name

    # ``bool`` is an ``int`` subclass: a ``True`` size is not a byte count.
    with pytest.raises(ContractError) as refused:
        _observation([_image_ref(size=True)])
    assert refused.value.field == "ActionObservation.parts[0].size"

    # A float ``captured_at`` is the normal case (clock timestamps) and an
    # integer one is accepted too.
    _observation([_image_ref(captured_at=3)])
    _observation([_image_ref(captured_at=0.0)])


def test_parts_refuse_unexpected_fields_and_non_mappings() -> None:
    with pytest.raises(ContractError) as extra:
        _observation([_image_ref(path="/tmp/shot.png")])
    assert extra.value.field == "ActionObservation.parts[0].path"

    with pytest.raises(ContractError) as text_extra:
        _observation([{"type": "text", "text": "x", "url": "data:"}])
    assert text_extra.value.field == "ActionObservation.parts[0].url"

    with pytest.raises(ContractError) as not_text:
        _observation([{"type": "text", "text": 1}])
    assert not_text.value.field == "ActionObservation.parts[0].text"

    with pytest.raises(ContractError) as not_mapping:
        _observation(["text"])
    assert not_mapping.value.field == "ActionObservation.parts[0]"

    with pytest.raises(ContractError) as not_sequence:
        _observation({"type": "text", "text": "x"})
    assert not_sequence.value.field == "ActionObservation.parts"


# --------------------------------------------------------------------------- #
# ActionSpec.delivery (R1 decision 1, R5)
# --------------------------------------------------------------------------- #

_TEXT_ARGUMENTS = {
    "type": "object",
    "properties": {"text": {"type": "string"}, "count": {"type": "integer"}},
    "required": ["text"],
    "additionalProperties": False,
}


def _write_spec(delivery: object = None, *, nature: str = "write") -> ActionSpec:
    return ActionSpec(
        name="chat.write",
        version=1,
        description="send text",
        argument_schema=_TEXT_ARGUMENTS,
        result_schema={"type": "object"},
        nature=nature,
        required_permissions=("chat.write",),
        supported_destinations=(Destination("twitch", "*", "chat"),),
        timeout_seconds=1.0,
        idempotency="none",
        delivery=delivery,
    )


def test_spec_without_delivery_is_unchanged() -> None:
    # The field is appended last and defaulted, so every existing constructor
    # keeps its meaning and declares no delivery capability.
    spec = _write_spec()
    assert spec.delivery is None
    assert spec.delivery_text_argument is None
    assert _spec({"type": "object"}).delivery is None


def test_delivery_on_a_write_spec_is_accepted_frozen_and_part_of_equality() -> None:
    spec = _write_spec({"text_argument": "text"})

    assert spec.delivery == {"text_argument": "text"}
    assert spec.delivery_text_argument == "text"
    with pytest.raises(TypeError):
        spec.delivery["text_argument"] = "count"  # type: ignore[index]

    # Two specs differing only by ``delivery`` are unequal: the proxy's
    # ``hello`` comparison and the registry's redeclaration check see it.
    assert spec == _write_spec({"text_argument": "text"})
    assert spec != _write_spec()
    assert spec != _write_spec({"text_argument": "none"})


def test_effect_only_delivery_declares_no_text_argument() -> None:
    spec = _write_spec({"text_argument": "none"})

    assert spec.delivery == {"text_argument": "none"}
    assert spec.delivery_text_argument is None


@pytest.mark.parametrize(
    ("delivery", "nature"),
    [
        ({"text_argument": "text"}, "read"),
        ({"text_argument": "none"}, "read"),
        ({"text_argument": "text", "mode": "fixed"}, "write"),
        ({}, "write"),
        ({"text_argument": "missing"}, "write"),
        ({"text_argument": "count"}, "write"),
        ("text", "write"),
        (["text"], "write"),
    ],
    ids=[
        "read-with-text",
        "read-effect-only",
        "unknown-key",
        "missing-text-argument",
        "argument-absent-from-schema",
        "argument-not-a-string",
        "not-a-mapping",
        "a-list",
    ],
)
def test_invalid_delivery_names_the_field(delivery: object, nature: str) -> None:
    """AC58 (contract half): a ``read`` action cannot declare a delivery."""

    with pytest.raises(ContractError) as caught:
        _write_spec(delivery, nature=nature)
    assert caught.value.field == "ActionSpec.delivery"


def test_delivery_text_argument_value_must_be_a_name() -> None:
    with pytest.raises(ContractError) as caught:
        _write_spec({"text_argument": 1})
    assert caught.value.field == "ActionSpec.delivery.text_argument"


# --------------------------------------------------------------------------- #
# Event-kind vocabulary (R1, R6)
# --------------------------------------------------------------------------- #


def test_event_kind_vocabulary_is_the_nine_names_in_order() -> None:
    """Superseded by phase 5 R7 (A5): ``cue`` is appended as the tenth kind,
    last, so the nine earlier names keep their order and indices."""

    assert EVENT_KINDS == (
        "message",
        "sub",
        "resub",
        "sub_gift",
        "community_sub_gift",
        "raid",
        "follow",
        "tip",
        "watch_tick",
        "cue",
    )
    assert len(EVENT_KINDS) == 10 and EVENT_KINDS[-1] == "cue"
    assert EVENT_KIND_SET == frozenset(EVENT_KINDS)
    assert EVENT_KIND_DEFAULT == "message"


def test_platform_notice_kinds_exclude_message_and_watch_tick() -> None:
    """R1 route safety: a notice kind is never an ordinary message or a tick.

    Phase 5 A5: nor a routed ``cue``, and the seven members are unchanged."""

    assert len(PLATFORM_NOTICE_KINDS) == 7
    assert set(PLATFORM_NOTICE_KINDS) == EVENT_KIND_SET - {"message", "watch_tick", "cue"}
    assert "cue" not in PLATFORM_NOTICE_KINDS
    assert PLATFORM_NOTICE_KINDS == (
        "sub",
        "resub",
        "sub_gift",
        "community_sub_gift",
        "raid",
        "follow",
        "tip",
    )
    assert list(PLATFORM_NOTICE_KINDS) == [
        kind for kind in EVENT_KINDS if kind in PLATFORM_NOTICE_KINDS
    ]


@pytest.mark.parametrize("kind", EVENT_KINDS)
def test_validate_event_kind_accepts_each_kind(kind: str) -> None:
    assert validate_event_kind(kind, "payload.kind") == kind


def test_validate_event_kind_defaults_none_to_message() -> None:
    assert validate_event_kind(None, "payload.kind") == "message"


@pytest.mark.parametrize("value", ["Raid", "", 1, "announcement", True, ["raid"]])
def test_validate_event_kind_refuses_and_names_the_field(value: object) -> None:
    with pytest.raises(ContractError) as caught:
        validate_event_kind(value, "payload.kind")
    assert caught.value.field == "payload.kind"


# --------------------------------------------------------------------------- #
# ActionSpec.model_proposable (R5, AC25 core half)
# --------------------------------------------------------------------------- #


def _proposable_spec(
    model_proposable: object, *, nature: str = "write", delivery: object = None
) -> ActionSpec:
    return dataclasses.replace(
        _write_spec(delivery, nature=nature), model_proposable=model_proposable
    )


def test_model_proposable_defaults_to_false_on_existing_constructions() -> None:
    assert _write_spec().model_proposable is False
    assert _write_spec({"text_argument": "text"}).model_proposable is False
    assert _write_spec(nature="read").model_proposable is False
    assert _spec({"type": "object"}).model_proposable is False


def test_a_write_without_delivery_may_be_model_proposable() -> None:
    spec = _proposable_spec(True)
    assert spec.model_proposable is True
    # The flag is part of equality: a lent spec that lost it is a mismatch.
    assert spec != _write_spec()
    assert spec == _proposable_spec(True)


def test_a_read_action_is_never_model_proposable() -> None:
    with pytest.raises(ContractError) as caught:
        _proposable_spec(True, nature="read")
    assert caught.value.field == "ActionSpec.model_proposable"
    assert caught.value.reason == "a read action is never model-proposable"


@pytest.mark.parametrize("delivery", [{"text_argument": "text"}, {"text_argument": "none"}])
def test_a_delivery_capable_action_is_never_model_proposable(delivery: dict) -> None:
    with pytest.raises(ContractError) as caught:
        _proposable_spec(True, delivery=delivery)
    assert caught.value.field == "ActionSpec.model_proposable"
    assert caught.value.reason == "a delivery-capable action is never model-proposable"


@pytest.mark.parametrize("value", ["yes", 1, 0, None])
def test_model_proposable_must_be_a_boolean(value: object) -> None:
    with pytest.raises(ContractError) as caught:
        _proposable_spec(value)
    assert caught.value.field == "ActionSpec.model_proposable"


def test_contracts_name_no_platform() -> None:
    """AC39 (core half): the contracts name no platform; P26 scans all of core/."""

    source = (Path(__file__).resolve().parents[1] / "core" / "contracts.py").read_text(
        encoding="utf-8"
    )
    for word in ("kick", "youtube", "twitch"):
        assert re.findall(rf"\b{word}\b", source, flags=re.IGNORECASE) == [], word


# ---------------------------------------------------------------------------
# Loader discovery of model_proposable (R5, AC25 discovery half)
# ---------------------------------------------------------------------------

_REPOSITORY = Path(__file__).resolve().parents[1]


def _manifest_action(**overrides: object) -> dict:
    action: dict = {
        "name": "moderation.request",
        "version": 1,
        "description": "ask for one moderation step",
        "argument_schema": _TEXT_ARGUMENTS,
        "result_schema": {"type": "object"},
        "nature": "write",
        "required_permissions": ["moderation.request"],
        "supported_destinations": [
            {"platform": "fake", "channel_id": "*", "scope": "chat"}
        ],
        "timeout_seconds": 1,
        "idempotency": "none",
    }
    action.update(overrides)
    return action


def _modules_root(root: Path, action: dict, *, directory: str = "proposer") -> Path:
    module = root / directory
    module.mkdir()
    manifest = {
        "name": directory,
        "manifest_version": 2,
        "runtime_api": RUNTIME_API,
        "produces": [],
        "consumes": [],
        "middleware": False,
        "actions": [action],
    }
    (module / "module.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    (module / "__init__.py").write_text("", encoding="utf-8")
    return root


async def _discover(root: Path) -> ModuleLoader:
    """Discover every module under *root*, activating none."""

    loader = ModuleLoader(object(), root, environ={})
    assert await loader.activate_enabled({"enabled_modules": [], "modules": {}}) == []
    return loader


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides",
    [
        {"nature": "read"},
        {"delivery": {"text_argument": "text"}},
        {"delivery": {"text_argument": "none"}},
    ],
    ids=["read", "delivery", "effect-only-delivery"],
)
async def test_discovery_refuses_model_proposable_naming_module_and_action(
    tmp_path: Path, overrides: dict
) -> None:
    root = _modules_root(
        tmp_path, _manifest_action(model_proposable=True, **overrides)
    )

    with pytest.raises(ModuleLoadError) as caught:
        await _discover(root)

    message = str(caught.value)
    assert message.startswith(
        "module 'proposer': field 'actions[0].model_proposable': "
    )
    assert "'moderation.request'" in message
    assert "never model-proposable" in message


@pytest.mark.asyncio
async def test_discovery_accepts_model_proposable_on_a_delivery_less_write(
    tmp_path: Path,
) -> None:
    loader = await _discover(_modules_root(tmp_path, _manifest_action(model_proposable=True)))

    (spec,) = loader.discovered["proposer"].declaration.actions
    assert spec.name == "moderation.request"
    assert spec.model_proposable is True


@pytest.mark.asyncio
async def test_discovery_defaults_model_proposable_to_false(tmp_path: Path) -> None:
    loader = await _discover(_modules_root(tmp_path, _manifest_action()))

    (spec,) = loader.discovered["proposer"].declaration.actions
    assert spec.model_proposable is False


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["yes", 1, None])
async def test_discovery_refuses_a_non_boolean_model_proposable(
    tmp_path: Path, value: object
) -> None:
    root = _modules_root(tmp_path, _manifest_action(model_proposable=value))

    with pytest.raises(ModuleLoadError) as caught:
        await _discover(root)

    assert str(caught.value).startswith(
        "module 'proposer': field 'actions[0].model_proposable': "
        "action 'moderation.request': "
    )


@pytest.mark.asyncio
async def test_discovery_still_refuses_any_other_unknown_action_key(
    tmp_path: Path,
) -> None:
    root = _modules_root(tmp_path, _manifest_action(foo=1))

    with pytest.raises(ModuleLoadError) as caught:
        await _discover(root)

    assert str(caught.value) == (
        "module 'proposer': field 'actions[0]': declares unknown keys: foo"
    )


@pytest.mark.asyncio
async def test_only_moderation_request_is_model_proposable() -> None:
    """R5/P14: moderation.request is the only shipped model-proposable action."""
    loader = await _discover(_REPOSITORY / "modules")

    specs = [
        spec
        for module in loader.discovered.values()
        if module.declaration is not None
        for spec in module.declaration.actions
    ]
    assert specs
    assert [spec.name for spec in specs if spec.model_proposable] == [
        "moderation.request"
    ]


# --------------------------------------------------------------------------- #
# N3 (gate 4): one known-value protection contract, and no copy of it
# --------------------------------------------------------------------------- #

import ast  # noqa: E402
import html as _html  # noqa: E402
import json as _json  # noqa: E402

from core.contracts import REDACTION_PLACEHOLDER, mask_known  # noqa: E402

_ROOT = Path(__file__).resolve().parents[1]

#: The three consumers that emit known-value-bearing text, by file: each
#: delegates to :func:`mask_known` and holds no replacement of its own.
_KNOWN_VALUE_CONSUMERS = {
    "core/loader.py": "_redact_values",
    "core/contracts.py": "_redact",
    "core/config_ui/__init__.py": "_redact",
}

#: The project's own source trees; tests, build output and the virtualenv
#: are not shipped code.
_SOURCE_TREES = ("core", "modules", "demo", "docs")


def _response_forms(value: str) -> set[str]:
    """Raw, JSON-escaped and HTML-escaped spellings of *value*."""

    json_form = _json.dumps(value)[1:-1]
    return {value, json_form, _html.escape(value), _html.escape(json_form)}


def _fragments(value: str) -> tuple[str, str, str]:
    half = len(value) // 2
    return value, value[:half], value[half:]


@pytest.mark.parametrize(
    ("text", "values", "expected"),
    [
        # One value raw, JSON-escaped and HTML-escaped in the same text.
        (
            'raw=a"b<c json=a\\"b<c html=a&quot;b&lt;c',
            ['a"b<c'],
            "raw=# json=# html=#",
        ),
        # A value overlapping another value's escaped form: the JSON form of
        # the first ends where the second begins.
        ('k=x\\"yz-secret-9;', ['x"y', "yz-secret-9"], "k=#;"),
        # A value overlapping its own escaped form: the raw ``&amp`` is the
        # start of its HTML form ``&amp;amp``, so masking the raw match first
        # would leave the escaped form's ``;amp`` tail behind.
        ("x&amp;amp;y", ["&amp"], "x#;y"),
        # Identical values, in two forms, given twice.
        ("a=<s> b=&lt;s&gt;", ["<s>", "<s>"], "a=# b=#"),
        # N3 itself, with the second value only present in an escaped form.
        (
            "p=context-prefix-review-secret-4521",
            ["review-secret-4521", "context-prefix-review-"],
            "p=#",
        ),
        # No form present: the text is returned exactly.
        ("plain review-public-room", ["review-secret-4521"], "plain review-public-room"),
    ],
)
def test_n3_the_contract_masks_the_union_of_every_form_of_every_value(
    text: str, values: list[str], expected: str
) -> None:
    """N3, R8: every form of every value is located in the original text and
    the union masked in one pass — independent of the order of the values
    and of the order of their forms."""

    def reversed_forms(value: str) -> list[str]:
        return sorted(_response_forms(value), key=len)

    def longest_first(value: str) -> list[str]:
        return sorted(_response_forms(value), key=len, reverse=True)

    for forms in (_response_forms, reversed_forms, longest_first):
        assert mask_known(text, values, marker="#", forms=forms) == expected
        assert mask_known(text, values[::-1], marker="#", forms=forms) == expected
    for value in values:
        for form in _response_forms(value):
            assert form not in expected


def test_n3_the_contract_defaults_to_the_raw_form_and_the_trace_marker() -> None:
    """Without *forms* only the raw value is a needle; the default marker is
    the trace's placeholder, and adjacent matches form one masked run."""

    assert mask_known("a=<s> b=&lt;s&gt;", ["<s>"]) == f"a={REDACTION_PLACEHOLDER} b=&lt;s&gt;"
    assert mask_known("xyxy", ["xy"]) == REDACTION_PLACEHOLDER
    assert mask_known("unchanged", ["", "absent"]) == "unchanged"


def test_n3_sanitize_trace_leaves_no_fragment_in_any_key_or_value() -> None:
    """N3, R8: ``sanitize_trace`` — the route of ``input.ownership.rejected``
    — masks the union of both credentials' matches in keys and values; a
    removal of the overlapping value no longer leaves the other's suffix."""

    secret, other = "review-secret-4521", "context-prefix-review-"
    key = "context-prefix-" + secret
    for secrets in ((secret, other), (other, secret)):
        cleaned = sanitize_trace(
            {"platform": key, key: [key, {"nested": key}], "public": "review-public-room"},
            secrets=secrets,
        )
        assert cleaned == {
            "platform": REDACTION_PLACEHOLDER,
            REDACTION_PLACEHOLDER: [REDACTION_PLACEHOLDER, {"nested": REDACTION_PLACEHOLDER}],
            "public": "review-public-room",
        }
        text = _json.dumps(cleaned)
        for fragment in (*_fragments(secret), *_fragments(other)):
            assert fragment not in text


def _names(node: ast.AST) -> set[str]:
    return {item.id for item in ast.walk(node) if isinstance(item, ast.Name)}


_COMPREHENSIONS = (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)


def _sequential_replacements(tree: ast.AST) -> list[int]:
    """Lines of every sequential known-value replacement in *tree*.

    A ``.replace``/``.sub`` call whose needle (the first argument, or the
    receiver of a compiled pattern's ``.sub``) depends on the target of an
    enclosing ``for`` loop or comprehension, or any non-literal ``.replace``
    inside a ``while`` loop: each searches a string the previous iteration
    already modified, which is the shape N3 found twice. ``os.replace`` (a
    file rename) and keyword-only ``.replace`` (``datetime``) are not text
    replacement.
    """

    found: list[int] = []

    def needle_names(call: ast.Call) -> set[str] | None:
        func = call.func
        if not isinstance(func, ast.Attribute) or func.attr not in ("replace", "sub"):
            return None
        if isinstance(func.value, ast.Name) and func.value.id == "os":
            return None
        if not call.args:
            return None
        names = _names(call.args[0])
        if func.attr == "sub":
            names |= _names(func.value)
        return names

    def visit(node: ast.AST, bound: frozenset[str], in_while: bool) -> None:
        if isinstance(node, (ast.For, ast.AsyncFor)):
            visit(node.iter, bound, in_while)
            inner = bound | _names(node.target)
            for child in (*node.body, *node.orelse):
                visit(child, inner, in_while)
            return
        if isinstance(node, ast.While):
            visit(node.test, bound, in_while)
            for child in (*node.body, *node.orelse):
                visit(child, bound, True)
            return
        if isinstance(node, _COMPREHENSIONS):
            inner = bound
            for generator in node.generators:
                inner = inner | _names(generator.target)
            for child in ast.iter_child_nodes(node):
                visit(child, inner, in_while)
            return
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            bound, in_while = frozenset(), False
        if isinstance(node, ast.Call):
            names = needle_names(node)
            if names is not None:
                literal = isinstance(node.args[0], ast.Constant)
                if names & bound or (in_while and not literal):
                    found.append(node.lineno)
        for child in ast.iter_child_nodes(node):
            visit(child, bound, in_while)

    visit(tree, frozenset(), False)
    return found


def _source_files() -> list[Path]:
    return sorted(
        path
        for tree in _SOURCE_TREES
        for path in (_ROOT / tree).rglob("*.py")
        if "__pycache__" not in path.parts
    )


def test_n3_no_module_defines_its_own_sequential_known_value_replacement() -> None:
    """N3, R8: the known-value protection has one implementation. No shipped
    source file replaces known values one after another; the three
    consumers that emit known-value-bearing text — the loader's
    diagnostics, ``sanitize_trace`` and the configuration UI's responses —
    each delegate to :func:`mask_known`, which is defined once, in
    ``core/contracts.py``. Ordering is part of no correctness argument."""

    files = _source_files()
    assert any(path.name == "contracts.py" for path in files)
    offending = {
        str(path.relative_to(_ROOT)): lines
        for path in files
        for lines in [_sequential_replacements(ast.parse(path.read_text(encoding="utf-8")))]
        if lines
    }
    assert offending == {}

    definitions = [
        str(path.relative_to(_ROOT))
        for path in files
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.FunctionDef) and node.name == "mask_known"
    ]
    assert definitions == ["core/contracts.py"]

    for filename, function in _KNOWN_VALUE_CONSUMERS.items():
        tree = ast.parse((_ROOT / filename).read_text(encoding="utf-8"))
        [definition] = [
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == function
        ]
        calls = [
            node.func.id
            for node in ast.walk(definition)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        ]
        assert "mask_known" in calls, filename
        assert not any(
            isinstance(node, (ast.For, ast.While, *_COMPREHENSIONS))
            for node in ast.walk(definition)
        ), filename


@pytest.mark.parametrize(
    "source",
    [
        # The pre-N3 trace redactor.
        "def _redact(text, secrets):\n"
        "    for secret in secrets:\n"
        "        if secret in text:\n"
        "            text = text.replace(secret, '[REDACTED]')\n"
        "    return text\n",
        # The pre-N3 UI redactor, over a helper's forms.
        "def _redact(text, values):\n"
        "    for form in _redaction_forms(values):\n"
        "        text = text.replace(form, '[hidden]')\n"
        "    return text\n",
        # A tuple target and a regular expression.
        "import re\n"
        "def scrub(text, pairs):\n"
        "    for _, value in pairs:\n"
        "        text = re.sub(re.escape(value), '#', text)\n"
        "    return text\n",
        # A compiled pattern per value.
        "import re\n"
        "def scrub(text, values):\n"
        "    for value in values:\n"
        "        text = re.compile(re.escape(value)).sub('#', text)\n"
        "    return text\n",
        # A reduction through a comprehension's side effect.
        "def scrub(text, values):\n"
        "    return [text := text.replace(value, '#') for value in values][-1]\n",
        # A while loop draining the values.
        "def scrub(text, values):\n"
        "    while values:\n"
        "        value = values.pop()\n"
        "        text = text.replace(value, '#')\n"
        "    return text\n",
    ],
)
def test_n3_the_structural_guard_fails_on_a_reintroduced_loop(source: str) -> None:
    """Negative control: the guard sees each spelling of a sequential
    known-value replacement a module could reintroduce."""

    assert _sequential_replacements(ast.parse(source)) != []


def test_n3_the_structural_guard_passes_a_delegating_redactor() -> None:
    """Positive control: delegation, a file rename, a keyword-only replace and
    a literal replace outside any loop are not sequential replacements."""

    source = (
        "import os\n"
        "def _redact(text, values):\n"
        "    return mask_known(text, values, marker='#')\n"
        "def save(names, temporary):\n"
        "    for name in names:\n"
        "        os.replace(temporary, name)\n"
        "def stamp(instant, items):\n"
        "    for item in items:\n"
        "        instant = instant.replace(tzinfo=item)\n"
        "    return instant.isoformat().replace('+00:00', 'Z')\n"
    )
    assert _sequential_replacements(ast.parse(source)) == []
