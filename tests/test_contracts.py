import pytest

from core.contracts import (
    IMAGE_REF_FIELDS,
    ActionObservation,
    ActionSpec,
    ContractError,
    Destination,
    TriggerPolicy,
    TriggerRule,
    sanitize_trace,
    validate_against_schema,
    validate_parts,
)


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
