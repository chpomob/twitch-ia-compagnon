"""``python -m demo``: the offline demo, and its refusal path (README).

The README promises a newcomer one command that shows a viewer's message
becoming a written and spoken answer, with no credential and no network. These
tests hold that promise: the demo drives the shipped phase 2 scenario to its
terminal success — the scenario's own assertions run inside it, so a green run
here is the run the phase 2 spec already validates — narrates what a newcomer is
told to expect, drives the second platform the same way, maps every
``--platform`` choice to a harness edge, and refuses with a sentence rather than
a traceback when it is not run from a checkout.

Assertions read the narration through :func:`_narration`, which collapses runs of
whitespace: the demo aligns its columns, and a test that pinned that padding
would fail on a cosmetic edit.

Nothing here sleeps: the demo is driven at its default pace (0), and the
scenario rides the harness's :class:`ManualClock`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from demo import PLATFORMS, _harness, _platform_key, main

SCENARIO = _harness.load_scenario()
"""Loaded at import time: ``tests/conftest.py`` must be imported before any loop."""

ACTS = (
    "ACT 1 — bring the companion up",
    "ACT 2 — a viewer speaks up",
    "ACT 3 — the brain runs its bounded loop",
    "ACT 4 — shutdown",
)
"""The four acts a recording of the demo shows, in order."""

PROMISES = (
    "'companion, can you hear me?'",
    "accepted: keyword_match",
    "admitted session viewer-7",
    "1 queued",
    "audio.capture [read] → twitch/broadcaster-42/audio",
    "← audio.capture 96 044 B · audio_ref audio/wav (3 000 ms)",
    "chat.write [write] → twitch/broadcaster-42/chat",
    "audio.speak [write] → twitch/broadcaster-42/audio",
    "delivered as sent-1",
    "2 model turns, 3 action calls",
    "transcript 'hello companion, can you hear me'",
    "'I heard you say hello, welcome to the stream!'",
    "audio.speak → playback completed, 1 500 ms played",
    "the lease was released with the run",
    "status 0 · 0 failures",
    "8 counters are all zero",
)
"""What the narration must say: the run a newcomer is promised, end to end."""


def _narration(output: str) -> str:
    """*output* with every run of whitespace collapsed to one space."""

    return " ".join(output.split())


def test_the_demo_drives_the_shipped_scenario_and_narrates_it(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The whole promise of the README's one command, in one run."""

    assert main(["--no-colour"]) == 0
    said = _narration(capsys.readouterr().out)
    positions = [said.find(act) for act in ACTS]
    assert all(position >= 0 for position in positions), said
    assert positions == sorted(positions), "the acts are narrated in order"
    for promise in PROMISES:
        assert _narration(promise) in said, promise
    assert "no credential is read, no model is called" in said
    assert "nothing sleeps, no wall-clock second is spent" in said


def test_the_demo_drives_the_second_platform_with_the_core_unchanged(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Same scenario, the fixture platform: the run is not twitch-shaped."""

    assert main(["--no-colour", "--platform", "fake"]) == 0
    said = _narration(capsys.readouterr().out)
    assert "ready fakeplatform" in said
    assert "input fakeplatform · channel chan-a" in said
    assert "chat.write [write] → fake/chan-a/chat (fake-chat)" in said
    assert "'I heard you say hello, welcome to the stream!'" in said
    assert "the lease was released with the run" in said


def test_every_platform_choice_maps_to_a_harness_edge() -> None:
    """``--platform`` offers exactly the edges the scenario defines, by name."""

    offered = {_platform_key(SCENARIO, choice) for choice in PLATFORMS}
    assert offered == set(SCENARIO.PLATFORMS)
    assert SCENARIO.FAKEPLATFORM in offered


def test_the_demo_refuses_outside_a_checkout(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A wheel has no ``tests/``: the demo says so instead of raising."""

    assert main(["--repository-root", str(tmp_path)]) == 3
    captured = capsys.readouterr()
    assert "no scenario harness" in captured.err
    assert "checkout" in captured.err
    assert captured.out == ""


def test_no_narration_line_is_wider_than_a_terminal(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The narration fits a terminal, which is also what a recording needs."""

    assert main(["--no-colour"]) == 0
    widths = [len(line) for line in capsys.readouterr().out.splitlines()]
    assert widths, "the demo said nothing"
    assert max(widths) <= 100, max(widths)


def test_the_demo_reads_its_script_off_the_scenario() -> None:
    """No number and no text is written twice: the harness is the source."""

    first = SCENARIO.SCENARIO[0]["choices"][0]["message"]["tool_calls"][0]["function"]
    assert first["name"] == "audio.capture"
    assert SCENARIO.SCENARIO[1]["choices"][0]["message"]["content"] == SCENARIO.ANSWER
