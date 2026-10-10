"""The install guide's profile is a profile the code accepts (docs/installation.md).

``docs/installation.md`` hands a non-technical reader ~100 lines of YAML to copy,
eight values to export and two commands to run. A guide that drifts from the code
is worse than no guide: the reader's first command would fail and nothing in the
document would say why. So the document is exercised here:

* the profile block it prints is written out and checked by the real entry point's
  ``--check-config``, with exactly the eight values the guide tells the reader to
  export — so the block is validated by the code path the reader runs;
* every ``${...}`` reference in that block is one of the eight documented exports
  (an undocumented reference is precisely the first error a reader would hit);
* the command and the flags the guide prints still exist.

The environment is built explicitly rather than inherited: the check must pass on
the guide's eight values, not on whatever the developer happens to have exported.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
GUIDE = ROOT / "docs" / "installation.md"

PROFILE_NAME = "mon-compagnon.yaml"
"""The file name the guide tells the reader to save the profile under."""

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
"""The eight values the guide's section 7 exports; none is a real credential."""

_TEXT = GUIDE.read_text(encoding="utf-8")
_BLOCK = re.compile(r"^```(?P<language>[a-z]+)\n(?P<body>.*?)^```$", re.MULTILINE | re.DOTALL)
_REFERENCES = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_EXPORTS = re.compile(r"^(?:export\s+)?(?:\$env:)?([A-Z][A-Z0-9_]*)[=|]", re.MULTILINE)


def _profile() -> str:
    """The guide's one profile block, with the modules directory made absolute.

    The block ships ``modules_directory: ./modules`` because the reader runs from
    the checkout; this test writes the file elsewhere, so only that one path is
    pointed back at the repository.
    """

    bodies = [
        match.group("body")
        for match in _BLOCK.finditer(_TEXT)
        if match.group("language") == "yaml"
    ]
    assert len(bodies) == 1, "the guide prints exactly one profile block"
    (body,) = bodies
    assert "modules_directory: ./modules" in body, "the guide no longer ships that path"
    return body.replace(
        "modules_directory: ./modules", f"modules_directory: {ROOT / 'modules'}"
    )


def test_the_guides_profile_is_accepted_by_the_real_entry_point(tmp_path: Path) -> None:
    """``--check-config`` over the printed block, with the eight printed values."""

    config = tmp_path / PROFILE_NAME
    config.write_text(_profile(), encoding="utf-8")
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


def test_every_reference_in_the_profile_is_an_export_the_guide_gives() -> None:
    """No ``${...}`` the reader was never told to export."""

    referenced = set(_REFERENCES.findall(_profile()))
    assert referenced == set(VALUES)
    documented = set(_EXPORTS.findall(_TEXT))
    assert referenced <= documented, sorted(referenced - documented)


def test_the_guide_prints_the_commands_the_code_still_has() -> None:
    """The two commands, and the check flag, are the ones the entry points expose."""

    assert f"--config {PROFILE_NAME} --check-config" in _TEXT
    assert f"python -m core.main --config {PROFILE_NAME}" in _TEXT
    assert f"python -m core.config_ui --config {PROFILE_NAME}" in _TEXT
    assert "python -m demo" in _TEXT
