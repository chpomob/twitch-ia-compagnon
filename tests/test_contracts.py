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
    TriggerRule,
    sanitize_trace,
    validate_against_schema,
    validate_event_kind,
    validate_parts,
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
    )
    assert EVENT_KIND_SET == frozenset(EVENT_KINDS)
    assert EVENT_KIND_DEFAULT == "message"


def test_platform_notice_kinds_exclude_message_and_watch_tick() -> None:
    """R1 route safety: a notice kind is never an ordinary message or a tick."""

    assert len(PLATFORM_NOTICE_KINDS) == 7
    assert set(PLATFORM_NOTICE_KINDS) == EVENT_KIND_SET - {"message", "watch_tick"}
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
