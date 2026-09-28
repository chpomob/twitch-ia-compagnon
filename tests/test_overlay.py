"""Shared overlay module: path, read, merge, digest, status-path collision (R2, R7)."""

import copy
import hashlib
import json
import re
from pathlib import Path

import pytest
import yaml

from core import overlay
from core.overlay import (
    STATUS_FILE_VARIABLE,
    OverlayError,
    canonical_digest,
    deep_merge,
    default_status_path,
    implicit_overlay_path,
    on_disk_digest,
    read_base,
    read_overlay,
    resolve_overlay_path,
    same_file,
    status_path_collision,
)

HEX64 = re.compile(r"[0-9a-f]{64}")


# --- AC7: merge precedence -------------------------------------------------


def test_ac7_merged_document_is_exact_and_inputs_are_unmutated():
    base = {"a": {"b": 1, "c": [1, 2]}, "d": "x"}
    over = {"a": {"b": 2, "c": [3]}, "e": "y"}
    base_before = copy.deepcopy(base)
    over_before = copy.deepcopy(over)

    merged = deep_merge(base, over)

    assert merged == {"a": {"b": 2, "c": [3]}, "d": "x", "e": "y"}
    assert base == base_before
    assert over == over_before
    # A fresh deep copy: mutating the result reaches neither input.
    merged["a"]["c"].append(9)
    merged["a"]["b"] = 7
    assert base == base_before
    assert over == over_before


def test_ac7_overlay_null_replaces_the_base_value():
    merged = deep_merge({"a": {"b": 1}, "d": "x"}, {"a": None})
    assert merged == {"a": None, "d": "x"}


def test_overlay_scalar_or_list_replaces_a_mapping_and_vice_versa():
    assert deep_merge({"a": {"b": 1}}, {"a": [1]}) == {"a": [1]}
    assert deep_merge({"a": 3}, {"a": {"b": 1}}) == {"a": {"b": 1}}


def test_merge_keeps_keys_only_in_base_at_every_depth():
    base = {"m": {"keep": 1, "n": {"deep": True, "x": 1}}}
    merged = deep_merge(base, {"m": {"n": {"x": 2}}})
    assert merged == {"m": {"keep": 1, "n": {"deep": True, "x": 2}}}


# --- AC8: overlay path derivation ------------------------------------------


@pytest.mark.parametrize(
    ("base", "expected"),
    [
        ("config.yaml", "config.local.yaml"),
        ("presence.yaml.example", "presence.local.yaml"),
        ("x.yml", "x.local.yaml"),
        ("x.yml.example", "x.local.yaml"),
    ],
)
def test_ac8_implicit_overlay_path(tmp_path, base, expected):
    assert implicit_overlay_path(tmp_path / base) == tmp_path / expected
    assert implicit_overlay_path(Path(base)) == Path(expected)


@pytest.mark.parametrize("base", ["config.txt", "config", "config.example", ".yaml"])
def test_ac8_other_names_have_no_implicit_overlay(base):
    assert implicit_overlay_path(Path(base)) is None


def test_ac8_explicit_overlay_wins(tmp_path):
    explicit = tmp_path / "elsewhere" / "mine.yaml"
    assert resolve_overlay_path(tmp_path / "config.yaml", explicit) == explicit
    assert resolve_overlay_path(tmp_path / "config.txt", explicit) == explicit
    assert resolve_overlay_path(tmp_path / "config.yaml", None) == (
        tmp_path / "config.local.yaml"
    )
    assert resolve_overlay_path(tmp_path / "config.txt", None) is None


# --- read_overlay ------------------------------------------------------------


def test_absent_and_empty_overlay_read_as_empty(tmp_path):
    assert read_overlay(tmp_path / "missing.local.yaml") == {}
    assert read_overlay(None) == {}
    empty = tmp_path / "empty.local.yaml"
    empty.write_text("", encoding="utf-8")
    assert read_overlay(empty) == {}
    null = tmp_path / "null.local.yaml"
    null.write_text("null\n", encoding="utf-8")
    assert read_overlay(null) == {}


def test_mapping_overlay_is_returned(tmp_path):
    path = tmp_path / "config.local.yaml"
    path.write_text("limits:\n  dedup:\n    max_entries: 5\n", encoding="utf-8")
    assert read_overlay(path) == {"limits": {"dedup": {"max_entries": 5}}}


def test_list_overlay_is_refused_naming_the_path(tmp_path):
    path = tmp_path / "config.local.yaml"
    path.write_text("[1, 2]\n", encoding="utf-8")
    with pytest.raises(OverlayError) as caught:
        read_overlay(path)
    assert str(caught.value) == f"overlay file {path}: must be a mapping"


def test_invalid_yaml_overlay_never_quotes_the_content(tmp_path):
    path = tmp_path / "config.local.yaml"
    path.write_text("a: [CANARY-OVERLAY\nb: {\n", encoding="utf-8")
    with pytest.raises(OverlayError) as caught:
        read_overlay(path)
    message = str(caught.value)
    assert message == f"overlay file {path}: is not valid YAML"
    assert "CANARY-OVERLAY" not in message
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__


def test_unreadable_overlay_is_refused(tmp_path):
    path = tmp_path / "config.local.yaml"
    path.mkdir()
    with pytest.raises(OverlayError) as caught:
        read_overlay(path)
    assert str(caught.value) == f"overlay file {path}: is not readable"


# --- read_base -----------------------------------------------------------------


def test_read_base_matches_load_config_messages(tmp_path):
    with pytest.raises(OverlayError, match=r"^configuration file: is not readable$"):
        read_base(tmp_path / "missing.yaml")
    bad = tmp_path / "bad.yaml"
    bad.write_text("a: [CANARY-BASE\n", encoding="utf-8")
    with pytest.raises(OverlayError) as caught:
        read_base(bad)
    assert str(caught.value) == "configuration file: is not valid YAML"
    listed = tmp_path / "list.yaml"
    listed.write_text("[1]\n", encoding="utf-8")
    with pytest.raises(OverlayError, match=r"^configuration: must be a mapping$"):
        read_base(listed)
    empty = tmp_path / "empty.yaml"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(OverlayError, match=r"^configuration: must be a mapping$"):
        read_base(empty)
    good = tmp_path / "good.yaml"
    good.write_text("a: 1\n", encoding="utf-8")
    assert read_base(good) == {"a": 1}


def test_overlay_error_is_a_runtime_error_and_module_stays_standalone():
    assert issubclass(OverlayError, RuntimeError)
    source = Path(overlay.__file__).read_text(encoding="utf-8")
    imported = set(re.findall(r"^\s*(?:from|import)\s+([\w.]+)", source, re.M))
    assert not {name for name in imported if name.split(".")[0] in {"core", "modules"}}
    assert STATUS_FILE_VARIABLE == "TWITCH_IA_COMPAGNON_STATUS_FILE"


# --- canonical_digest ------------------------------------------------------------


def test_digest_is_stable_under_key_order():
    one = {"a": 1, "b": {"x": [1, 2], "y": None}}
    two = {"b": {"y": None, "x": [1, 2]}, "a": 1}
    assert canonical_digest(one) == canonical_digest(two)
    assert canonical_digest(one) != canonical_digest({"a": 2, "b": one["b"]})


def test_digest_hashes_non_ascii_verbatim_as_utf8():
    document = {"salut": "café ☕"}
    expected = hashlib.sha256('{"salut":"café ☕"}'.encode("utf-8")).hexdigest()
    assert canonical_digest(document) == expected


def test_digest_stringifies_dates_and_non_string_keys():
    document = yaml.safe_load("when: 2026-09-28\n1: one\nb: two\n")
    text = json.dumps(
        {"1": "one", "b": "two", "when": "2026-09-28"},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    assert canonical_digest(document) == hashlib.sha256(text.encode("utf-8")).hexdigest()
    nested = {"outer": [{2: "x", "a": "y"}]}
    assert canonical_digest(nested) == canonical_digest({"outer": [{"2": "x", "a": "y"}]})


@pytest.mark.parametrize("document", [{}, {"a": 1}, {"é": [1, None, 2.5]}, {3: {4: 5}}])
def test_digest_is_64_lowercase_hex(document):
    assert HEX64.fullmatch(canonical_digest(document))


def test_on_disk_digest_is_the_digest_of_the_merged_document(tmp_path):
    base = tmp_path / "config.yaml"
    base.write_text("a:\n  b: 1\nd: x\n", encoding="utf-8")
    overlay_path = tmp_path / "config.local.yaml"
    assert on_disk_digest(base, overlay_path) == canonical_digest({"a": {"b": 1}, "d": "x"})
    overlay_path.write_text("a:\n  b: 2\n", encoding="utf-8")
    assert on_disk_digest(base, overlay_path) == canonical_digest({"a": {"b": 2}, "d": "x"})


# --- status path ---------------------------------------------------------------------


def test_default_status_path_appends_to_the_full_name(tmp_path):
    assert default_status_path(tmp_path / "config.yaml") == tmp_path / "config.yaml.status.json"
    assert default_status_path(Path("p.yaml.example")) == Path("p.yaml.example.status.json")


def _layout(tmp_path):
    base = tmp_path / "config.yaml"
    base.write_text("a: 1\n", encoding="utf-8")
    overlay_path = tmp_path / "config.local.yaml"
    overlay_path.write_text("a: 2\n", encoding="utf-8")
    return base, overlay_path


def test_collision_with_the_file_itself(tmp_path):
    base, overlay_path = _layout(tmp_path)
    assert status_path_collision(base, base, overlay_path) == "base"
    assert status_path_collision(overlay_path, base, overlay_path) == "overlay"


def test_collision_with_a_relative_non_normalised_spelling(tmp_path, monkeypatch):
    base, overlay_path = _layout(tmp_path)
    (tmp_path / "sub").mkdir()
    monkeypatch.chdir(tmp_path)
    assert status_path_collision("./sub/../config.local.yaml", base, overlay_path) == "overlay"
    assert status_path_collision("./sub/../config.yaml", base, overlay_path) == "base"
    assert same_file("./sub/../config.yaml", base)


def test_collision_through_a_symlink(tmp_path):
    base, overlay_path = _layout(tmp_path)
    link = tmp_path / "status.json"
    link.symlink_to(base)
    assert status_path_collision(link, base, overlay_path) == "base"


def test_collision_with_a_not_yet_existing_overlay(tmp_path):
    base = tmp_path / "config.yaml"
    base.write_text("a: 1\n", encoding="utf-8")
    overlay_path = tmp_path / "config.yaml.status.json"
    assert not overlay_path.exists()
    assert status_path_collision(default_status_path(base), base, overlay_path) == "overlay"


def test_collision_through_a_symlinked_parent_of_a_missing_file(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    base = real / "config.yaml"
    base.write_text("a: 1\n", encoding="utf-8")
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    # Neither file exists: the linked parent directory is still resolved.
    assert status_path_collision(alias / "x.local.yaml", base, real / "x.local.yaml") == "overlay"


def test_a_different_name_does_not_collide(tmp_path):
    base, overlay_path = _layout(tmp_path)
    assert status_path_collision(default_status_path(base), base, overlay_path) is None
    assert status_path_collision(tmp_path / "other.json", base, None) is None
    assert not same_file(tmp_path / "other.json", base)
