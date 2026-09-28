"""Contract tests for manifest presentation metadata (phase 4).

AC14 (R3): a schema node may carry a ``default`` annotation. The validator
accepts it only when it satisfies the node that declares it, reports a bad
one as ``<label>.default``, and the annotation has no effect on the values
validated against the schema.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from core.contracts import (
    ContractError,
    SchemaError,
    validate_against_schema,
    validate_schema,
)

_REPO = Path(__file__).resolve().parent.parent
_AUDIO_OUTPUT_MANIFEST = _REPO / "modules" / "audio_output" / "module.yaml"


def test_default_satisfying_the_node_is_accepted() -> None:
    validate_schema({"type": "integer", "minimum": 1, "default": 5}, label="limit")


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "integer", "minimum": 1, "default": 0},
        {"type": "string", "default": 3},
    ],
)
def test_default_violating_the_node_is_refused_as_default(schema: dict) -> None:
    with pytest.raises(SchemaError) as caught:
        validate_schema(schema, label="limit")
    assert caught.value.field == "limit.default"
    assert "unsupported" not in caught.value.reason
    assert str(caught.value).startswith("limit.default:")


def test_default_violating_enum_is_refused() -> None:
    with pytest.raises(SchemaError) as caught:
        validate_schema({"enum": ["a", "b"], "default": "c"}, label="mode")
    assert caught.value.field == "mode.default"
    assert "unsupported" not in caught.value.reason


def test_default_does_not_constrain_validated_values() -> None:
    bare = {"type": "integer", "minimum": 1}
    annotated = {"type": "integer", "minimum": 1, "default": 5}

    def outcome(value: object, schema: dict) -> object:
        try:
            validate_against_schema(value, schema, label="limit")
        except ContractError as exc:
            return (type(exc), exc.field, exc.reason)
        return None

    for value in (7, 5, 0, "7", None):
        assert outcome(value, annotated) == outcome(value, bare)
    assert outcome(7, annotated) is None


def test_nested_default_violation_names_the_nested_path() -> None:
    schema = {
        "type": "object",
        "properties": {"x": {"type": "integer", "minimum": 1, "default": 0}},
    }
    with pytest.raises(SchemaError) as caught:
        validate_schema(schema, label="settings_schema")
    assert caught.value.field == "settings_schema.properties.x.default"
    assert "unsupported" not in caught.value.reason


def test_nested_items_default_violation_names_the_nested_path() -> None:
    schema = {"type": "array", "items": {"type": "string", "default": 1}}
    with pytest.raises(SchemaError) as caught:
        validate_schema(schema, label="s")
    assert caught.value.field == "s.items.default"
    assert "unsupported" not in caught.value.reason


def test_object_default_is_checked_against_its_properties() -> None:
    schema = {
        "type": "object",
        "properties": {"n": {"type": "integer"}},
        "required": ["n"],
        "default": {"n": "one"},
    }
    with pytest.raises(SchemaError) as caught:
        validate_schema(schema, label="s")
    assert caught.value.field == "s.default"
    assert "unsupported" not in caught.value.reason
    assert "s.default.n" in caught.value.reason
    validate_schema({**schema, "default": {"n": 1}}, label="s")


def test_property_named_default_is_a_property_not_an_annotation() -> None:
    """A property literally named ``default`` is a name, never the annotation."""

    schema = {
        "type": "object",
        "properties": {"default": {"type": "string"}},
        "required": ["default"],
    }
    validate_schema(schema, label="s")
    validate_against_schema({"default": "x"}, schema, label="s")
    with pytest.raises(ContractError):
        validate_against_schema({"default": 3}, schema, label="s")


def test_audio_output_voices_schema_keeps_allowed_and_default() -> None:
    manifest = yaml.safe_load(_AUDIO_OUTPUT_MANIFEST.read_text(encoding="utf-8"))
    settings_schema = manifest["settings_schema"]
    voices = settings_schema["properties"]["voices"]

    assert set(voices["properties"]) == {"allowed", "default"}
    assert "default" not in voices  # the node itself carries no annotation
    validate_schema(voices, label="settings_schema.properties.voices")
    validate_schema(settings_schema, label="settings_schema")
