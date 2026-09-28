"""Contract tests for manifest presentation metadata (phase 4).

AC14 (R3): a schema node may carry a ``default`` annotation. The validator
accepts it only when it satisfies the node that declares it, reports a bad
one as ``<label>.default``, and the annotation has no effect on the values
validated against the schema.

AC12 / AC13 (R3): every setting node of each manifest in ``PRESENTED``
carries a non-empty ``title``, and every node whose description documents
``Default <literal>.`` declares that literal as its ``default``.
"""

from __future__ import annotations

import re
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


# --- AC12 / AC13 (R3): titles and documented defaults on shipped manifests ---

# The manifests whose settings schemas carry presentation metadata so far.
# Grows step by step until it lists every shipped manifest.
PRESENTED = (
    "agent_link",
    "audio_input",
    "audio_output",
    "audit",
    "brain",
    "capture",
    "chat_context",
    "clips",
    "kick",
    "moderation",
    "proxy",
    "stream_control",
)

_DEFAULT_LITERAL = re.compile(r"Default (`[^`]*`|\S+?)\.(\s|$)")
_EMPTY_BY_TYPE = {"array": [], "object": {}, "string": ""}


def _settings_schema(module: str) -> dict:
    path = _REPO / "modules" / module / "module.yaml"
    manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
    return manifest["settings_schema"]


def _setting_nodes(node: dict, path: str = "settings_schema"):
    """Yield ``(path, node)`` for every setting node, per R3.

    The root, every ``properties`` entry recursively, and every ``items``
    sub-schema having ``properties``. Property names are keys of
    ``properties`` only, so a property literally named ``default`` is a node,
    never the annotation.
    """

    yield path, node
    for name, child in (node.get("properties") or {}).items():
        yield from _setting_nodes(child, f"{path}.properties.{name}")
    items = node.get("items")
    if isinstance(items, dict) and "properties" in items:
        yield from _setting_nodes(items, f"{path}.items")


_NO_DEFAULT = object()


def _documented_default(node: dict) -> object:
    """The default a node's description documents, or ``_NO_DEFAULT``."""

    match = _DEFAULT_LITERAL.search(node.get("description", ""))
    if match is None:
        return _NO_DEFAULT
    literal = match.group(1)
    quoted = literal.startswith("`")
    if quoted:
        literal = literal[1:-1]
    if literal == "empty":
        return _EMPTY_BY_TYPE[node["type"]]
    try:
        return yaml.safe_load(literal)
    except yaml.YAMLError:
        # A backtick-quoted text that is not a YAML value on its own (a chat
        # command such as `!modok` reads as a tag) is the string it shows.
        if quoted:
            return literal
        raise


def test_extractor_reads_literals_and_ignores_prose() -> None:
    assert _documented_default({"description": "Bound. Default 400."}) == 400
    assert _documented_default({"description": "Default true. Then more."}) is True
    assert _documented_default({"description": "Default `alert`."}) == "alert"
    assert _documented_default({"description": "Default `[delete_message]`."}) == [
        "delete_message"
    ]
    assert _documented_default({"type": "array", "description": "Default empty."}) == []
    assert _documented_default({"description": 'Default "ready".'}) == "ready"
    assert _documented_default({"description": "Default `!modok`."}) == "!modok"
    assert _documented_default({"description": "Default 1.5 seconds."}) is _NO_DEFAULT
    assert _documented_default({"description": "Default behaviour applies."}) is (
        _NO_DEFAULT
    )


def test_walker_treats_a_property_named_default_as_a_node() -> None:
    schema = _settings_schema("audio_output")
    paths = [path for path, _ in _setting_nodes(schema)]
    assert "settings_schema.properties.voices.properties.default" in paths
    assert "settings_schema.properties.voices.properties.allowed" in paths


@pytest.mark.parametrize("module", PRESENTED)
def test_every_setting_node_has_a_title(module: str) -> None:
    nodes = list(_setting_nodes(_settings_schema(module)))
    assert len(nodes) > 0
    for path, node in nodes:
        title = node.get("title")
        assert isinstance(title, str) and title.strip(), path


@pytest.mark.parametrize("module", PRESENTED)
def test_every_documented_default_is_declared(module: str) -> None:
    for path, node in _setting_nodes(_settings_schema(module)):
        expected = _documented_default(node)
        if expected is _NO_DEFAULT:
            continue
        assert "default" in node, path
        assert node["default"] == expected, path
        assert type(node["default"]) is type(expected), path


@pytest.mark.parametrize("module", PRESENTED)
def test_every_declared_default_validates_against_its_node(module: str) -> None:
    schema = _settings_schema(module)
    validate_schema(schema, label="settings_schema")
    for path, node in _setting_nodes(schema):
        if "default" in node:
            validate_schema(node, label=path)


def test_audio_output_max_text_chars_default_is_400() -> None:
    node = _settings_schema("audio_output")["properties"]["max_text_chars"]
    assert node["default"] == 400
    assert node["description"].endswith("Default 400.")


def test_clips_required_default_is_boolean_false() -> None:
    node = _settings_schema("clips")["properties"]["required"]
    assert node["default"] is False
    assert node["description"].endswith("Default false.")


def test_walker_reaches_brain_items_properties() -> None:
    paths = {path for path, _ in _setting_nodes(_settings_schema("brain"))}
    assert "settings_schema.properties.routes.items.properties.match" in paths
    assert (
        "settings_schema.properties.delivery.properties.actions.items"
        ".properties.text_argument"
    ) in paths


def test_moderation_mode_backtick_string_default_is_alert() -> None:
    node = _settings_schema("moderation")["properties"]["mode"]
    assert node["default"] == "alert"
    assert node["description"].endswith("Default `alert`.")


def test_moderation_operations_backtick_list_default_is_delete_message() -> None:
    node = _settings_schema("moderation")["properties"]["act"]["properties"][
        "operations"
    ]
    assert node["default"] == ["delete_message"]
    assert node["description"].endswith("Default `[delete_message]`.")


def test_moderation_auto_apply_empty_default_is_empty_list() -> None:
    node = _settings_schema("moderation")["properties"]["propose"]["properties"][
        "auto_apply"
    ]
    assert node["default"] == []
    assert node["description"].endswith("Default empty.")


def test_moderation_command_defaults_are_the_command_strings() -> None:
    propose = _settings_schema("moderation")["properties"]["propose"]["properties"]
    assert propose["approve_command"]["default"] == "!modok"
    assert propose["reject_command"]["default"] == "!modno"
