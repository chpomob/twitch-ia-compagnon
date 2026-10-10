"""The install guides are guides the code accepts (docs/installation.md, .fr.md).

``docs/installation.md`` and its French counterpart hand a non-technical reader
~100 lines of YAML to copy, eight values to export and three commands to run. A
guide that drifts from the code is worse than no guide: the reader's first command
would fail and nothing in the document would say why. So both documents are
exercised here, by the same rules:

* the profile block each one prints is written out and checked by the real entry
  point's ``--check-config``, with exactly the eight values the guide tells the
  reader to export — so the block is validated by the code path the reader runs;
* every ``${...}`` reference in that block is one of the eight documented exports
  (an undocumented reference is precisely the first error a reader would hit);
* the commands, the flags and the file name the guide prints still exist;
* the two documents name each other and print the same profile, which is what makes
  the pair a translation rather than two documents that happen to share a subject.

The environment is built explicitly rather than inherited: the check must pass on
the guide's eight values, not on whatever the developer happens to have exported.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parents[1]
GUIDES = (ROOT / "docs" / "installation.md", ROOT / "docs" / "installation.fr.md")
"""The English guide and its French counterpart, in that order."""

PROFILE_NAME = "mon-compagnon.yaml"
"""The file name both guides tell the reader to save the profile under."""

VALUES = {
    "TWITCH_CLIENT_ID": "guide-client-id",
    "TWITCH_CLIENT_SECRET": "guide-client-secret",
    "TWITCH_ACCESS_TOKEN": "guide-access-token",
    "TWITCH_BROADCASTER_ID": "123456789",
    "TWITCH_BOT_USER_ID": "987654321",
    "MODEL_ENDPOINT": "https://api.example.com/v1",
    "MODEL_NAME": "guide-model",
    "MODEL_API_KEY": "guide-api-key",
}
"""The eight values both guides export in their section 7; none is a real credential."""

_BLOCK = re.compile(r"^```(?P<language>[a-z]+)\n(?P<body>.*?)^```$", re.MULTILINE | re.DOTALL)
_REFERENCES = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_EXPORTS = re.compile(r"^(?:export\s+)?(?:\$env:)?([A-Z][A-Z0-9_]*)[=]", re.MULTILINE)
_TITLE = re.compile(r"^# (.+)$", re.MULTILINE)


def _text(guide: Path) -> str:
    return guide.read_text(encoding="utf-8")


def _profile(guide: Path) -> str:
    """A guide's one profile block, with the modules directory made absolute.

    The block ships ``modules_directory: ./modules`` because the reader runs from
    the checkout; this test writes the file elsewhere, so only that one path is
    pointed back at the repository.
    """

    bodies = [
        match.group("body")
        for match in _BLOCK.finditer(_text(guide))
        if match.group("language") == "yaml"
    ]
    assert len(bodies) == 1, f"{guide.name}: exactly one profile block"
    (body,) = bodies
    assert "modules_directory: ./modules" in body, f"{guide.name}: that path is gone"
    return body.replace(
        "modules_directory: ./modules", f"modules_directory: {ROOT / 'modules'}"
    )


@pytest.mark.parametrize("guide", GUIDES, ids=lambda path: path.name)
def test_the_guides_profile_is_accepted_by_the_real_entry_point(
    guide: Path, tmp_path: Path
) -> None:
    """``--check-config`` over the printed block, with the eight printed values."""

    config = tmp_path / PROFILE_NAME
    config.write_text(_profile(guide), encoding="utf-8")
    # The guide's own values, over whatever this developer has exported.
    environment = {**os.environ, **VALUES, "NO_COLOR": "1"}

    completed = subprocess.run(
        [sys.executable, "-m", "core.main", "--config", str(config), "--check-config"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert completed.stdout == "" and completed.stderr == ""


@pytest.mark.parametrize("guide", GUIDES, ids=lambda path: path.name)
def test_every_reference_in_the_profile_is_an_export_the_guide_gives(guide: Path) -> None:
    """No ``${...}`` the reader was never told to export."""

    referenced = set(_REFERENCES.findall(_profile(guide)))
    assert referenced == set(VALUES)
    documented = set(_EXPORTS.findall(_text(guide)))
    assert referenced <= documented, sorted(referenced - documented)


@pytest.mark.parametrize("guide", GUIDES, ids=lambda path: path.name)
def test_the_guide_prints_the_commands_the_code_still_has(guide: Path) -> None:
    """The three commands, and the check flag, are the ones the entry points expose."""

    text = _text(guide)
    assert f"--config {PROFILE_NAME} --check-config" in text
    assert f"python -m core.main --config {PROFILE_NAME}" in text
    assert f"python -m core.config_ui --config {PROFILE_NAME}" in text
    assert "python -m demo" in text


def test_the_two_guides_are_a_translation_pair() -> None:
    """Each names the other, and both print the same profile.

    The blocks are compared as loaded YAML, not as text: the comments are
    translated, and the only literal that is legitimately translated is the
    fallback sentence the bot says. Everything else — every key, every bound,
    every reference — must be identical, or one language is teaching a profile the
    other does not.
    """

    english, french = GUIDES
    assert "Français](installation.fr.md)" in _text(english)
    assert "English](installation.md)" in _text(french)

    profiles = []
    for guide in GUIDES:
        profile = yaml.safe_load(_profile(guide))
        fallback = profile["modules"]["brain"]["fallback"]["text"]
        profile["modules"]["brain"]["fallback"]["text"] = "<translated>"
        profiles.append((profile, fallback))
    (english_profile, english_fallback), (french_profile, french_fallback) = profiles
    assert english_profile == french_profile, "the two profiles drifted apart"
    assert english_fallback != french_fallback, "the French guide kept the English sentence"

    titles = [_TITLE.search(_text(guide)).group(1) for guide in GUIDES]
    assert titles[0] != titles[1], "the two guides print the same title"
