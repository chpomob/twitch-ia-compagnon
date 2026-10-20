"""A guide and its translation stay a pair, not two drifting documents.

The repository keeps its guides in English and French: `X.md` and `X.fr.md`. A
translation is only useful if it is one, so the pairs are checked mechanically for
the things translation must never change:

* each names the other, so no reader is stranded in one language;
* the same section count and the same fenced blocks, so a section added to one side
  and forgotten on the other is caught;
* every command, flag and ``snake_case`` identifier the English guide uses appears
  in the French one — a run book is read by copy and paste, and a translated
  identifier is a broken command.

Prose is deliberately not compared: a translation is allowed to be better written
than its original, only not to change the facts.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

DOCS = Path(__file__).parents[1] / "docs"


def _pairs() -> list[tuple[Path, Path]]:
    """Every ``X.md``/``X.fr.md`` pair under ``docs/``, English first."""

    pairs = []
    for english in sorted(DOCS.glob("*.md")):
        if english.name.endswith(".fr.md"):
            continue
        french = english.with_name(f"{english.stem}.fr.md")
        if french.is_file():
            pairs.append((english, french))
    return pairs


_FENCE = re.compile(r"^```(?P<language>[a-z]*)$", re.MULTILINE)
_SECTION = re.compile(r"^## ", re.MULTILINE)
_CODE = re.compile(r"`([^`\n]+)`")
_TOKEN = re.compile(r"^(?:--[a-z][a-z-]*|python -m [\w.]+|twitch-ia-compagnon[\w.-]*|[a-z][a-z0-9]*(?:_[a-z0-9]+)+)$")

PAIRS = _pairs()

GUIDES = (
    "installation",
    "config-ui",
    "proxy-protocol",
    "design-v2",
    "automation-bridge",
    "web-knowledge",
)
"""The guides that must exist in both languages.

The rule is a repository rule, not an aspiration: a guide ships in English and in
French. The archived records are deliberately not in this list — `docs/README.md`,
`docs/steps/*` and `docs/campaigns/*` document what was decided and when, and
several are stored "moved without modification": a translated record is no longer a
record. Adding a guide means adding its name here and its translation beside it.
"""


def test_every_declared_guide_ships_in_both_languages() -> None:
    """The rule itself: a guide, its translation, and nothing in between."""

    for name in GUIDES:
        assert (DOCS / f"{name}.md").is_file(), f"{name}.md is missing"
        assert (DOCS / f"{name}.fr.md").is_file(), f"{name}.fr.md is missing"


def test_the_discovered_pairs_are_exactly_the_declared_guides() -> None:
    """A pair nobody declared, or a guide that lost its pair, is caught here."""

    assert {english.stem for english, _ in PAIRS} == set(GUIDES)


def test_there_is_at_least_one_pair_to_check() -> None:
    """The checks below are worthless if no pair is discovered."""

    assert PAIRS, "no docs/X.md + docs/X.fr.md pair found"


@pytest.mark.parametrize("pair", PAIRS, ids=lambda pair: pair[0].name)
def test_each_guide_names_its_translation(pair: tuple[Path, Path]) -> None:
    """Both directions of the language switch, spelled as a markdown link."""

    english, french = pair
    assert f"]({french.name})" in english.read_text(encoding="utf-8")
    assert f"]({english.name})" in french.read_text(encoding="utf-8")


@pytest.mark.parametrize("pair", PAIRS, ids=lambda pair: pair[0].name)
def test_the_translation_keeps_the_shape_of_its_guide(pair: tuple[Path, Path]) -> None:
    """Same sections, same fenced blocks: nothing added on one side only."""

    english, french = pair
    english_text = english.read_text(encoding="utf-8")
    french_text = french.read_text(encoding="utf-8")
    assert len(_SECTION.findall(english_text)) == len(_SECTION.findall(french_text)), (
        "section count differs"
    )
    english_fences = sorted(_FENCE.findall(english_text))
    french_fences = sorted(_FENCE.findall(french_text))
    assert english_fences == french_fences, "fenced blocks differ"


@pytest.mark.parametrize("pair", PAIRS, ids=lambda pair: pair[0].name)
def test_the_translation_keeps_every_command_and_identifier(pair: tuple[Path, Path]) -> None:
    """Commands, flags and identifiers are copied, never translated."""

    english, french = pair
    french_text = french.read_text(encoding="utf-8")
    missing = sorted(
        {
            token
            for token in _CODE.findall(english.read_text(encoding="utf-8"))
            if _TOKEN.match(token.strip())
        }
        - {
            token
            for token in _CODE.findall(french_text)
            if _TOKEN.match(token.strip())
        }
    )
    assert missing == [], f"{english.name}: {missing}"
