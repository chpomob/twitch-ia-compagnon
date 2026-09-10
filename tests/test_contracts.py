import pytest

from core.contracts import (
    ActionSpec,
    ContractError,
    Destination,
    validate_against_schema,
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
