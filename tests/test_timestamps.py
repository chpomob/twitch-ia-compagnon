"""Portable RFC 3339 parsing shared by the Kick and Twitch modules (P20F1).

``datetime.fromisoformat`` refuses a trailing ``Z`` on Python 3.10, so a valid
Kick delivery was refused with 400 and a Twitch poll read as malformed on 3.10
only. These pin the forms the platforms send on every supported Python.
"""

import pytest

from core.timestamps import parse_instant
from modules.kick import parse_timestamp
from modules.twitch import _parse_poll

NOON_UTC = 1_790_164_800.0  # 2026-09-23T12:00:00Z


@pytest.mark.parametrize(
    "stamp, expected",
    [
        ("2026-09-23T12:00:00Z", NOON_UTC),
        ("2026-09-23T12:00:00z", NOON_UTC),
        ("2026-09-23t12:00:00Z", NOON_UTC),
        ("2026-09-23 12:00:00Z", NOON_UTC),
        ("2026-09-23T12:00:00+00:00", NOON_UTC),
        ("2026-09-23T14:30:00+02:30", NOON_UTC),
        ("2026-09-23T07:00:00-05:00", NOON_UTC),
        ("2026-09-23T07:00:00-0500", NOON_UTC),
        ("2026-09-23T12:00:00.5Z", NOON_UTC + 0.5),
        ("2026-09-23T12:00:00.250000+00:00", NOON_UTC + 0.25),
        # Helix sends nanoseconds; digits past the microsecond are dropped.
        ("2026-09-23T12:00:00.871278372Z", NOON_UTC + 0.871278),
        ("  2026-09-23T12:00:00Z\n", NOON_UTC),
    ],
)
def test_an_instant_with_an_offset_is_read_on_every_python(stamp: str, expected: float) -> None:
    assert parse_instant(stamp) == pytest.approx(expected, abs=1e-6)


@pytest.mark.parametrize(
    "stamp",
    [
        # No offset: the timezone is never guessed.
        "2026-09-23T12:00:00",
        "2026-09-23T12:00:00.5",
        "2026-09-23",
        # Malformed.
        "",
        "   ",
        "yesterday",
        "2026-09-23T12:00Z",
        "2026-09-23T12:00:00ZZ",
        "2026-09-23T12:00:00+2",
        "2026-09-23T12:00:00.Z",
        "2026-9-23T12:00:00Z",
        "２０２６-09-23T12:00:00Z",
        # Impossible values.
        "2026-02-30T12:00:00Z",
        "2026-09-23T24:00:00Z",
        "2026-09-23T12:60:00Z",
        "2026-09-23T12:00:61Z",
        "2026-09-23T12:00:00+24:00",
        "2026-09-23T12:00:00+01:60",
    ],
)
def test_a_malformed_or_ambiguous_instant_is_refused(stamp: str) -> None:
    assert parse_instant(stamp) is None


@pytest.mark.parametrize("value", [None, 1_790_164_800, 1.5, b"2026-09-23T12:00:00Z", ["x"]])
def test_a_non_text_instant_is_refused(value: object) -> None:
    assert parse_instant(value) is None


def test_the_kick_webhook_timestamp_accepts_a_trailing_z() -> None:
    assert parse_timestamp("2026-09-23T12:00:00Z") == NOON_UTC
    assert parse_timestamp("2026-09-23T12:00:00") is None


def test_a_twitch_poll_started_at_with_a_trailing_z_is_read() -> None:
    """The latent Twitch call site: a Helix ``started_at`` ending in ``Z``."""

    entry = {
        "id": "poll-1",
        "title": "Next game?",
        "choices": [{"id": "choice-0", "title": "A", "votes": 0}],
        "status": "ACTIVE",
        "duration": 60,
        "started_at": "2026-09-23T12:00:00.123456789Z",
    }
    poll = _parse_poll(entry)
    assert poll is not None
    assert poll["started_at"] == pytest.approx(NOON_UTC + 0.123456, abs=1e-6)
    assert poll["ends_at"] == pytest.approx(NOON_UTC + 60.123456, abs=1e-6)
    assert _parse_poll({**entry, "started_at": "2026-09-23T12:00:00"}) is None
