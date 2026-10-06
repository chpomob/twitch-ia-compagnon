"""Portable parsing of the RFC 3339 instants the platforms send.

``datetime.fromisoformat`` is not a portable parser: Python 3.10 refuses a
trailing ``Z`` and more than six fractional digits, both of which 3.11+ accept,
so a module calling it directly admits a delivery on one Python and refuses it
on another. :func:`parse_instant` reads the format itself, the same way on
every supported Python.

It imports only the standard library and names no module.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

__all__ = ["parse_instant"]

_INSTANT = re.compile(
    r"(\d{4})-(\d{2})-(\d{2})[Tt ](\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?"
    r"(?:([Zz])|([+-])(\d{2}):?(\d{2}))",
    re.ASCII,
)


def parse_instant(value: Any) -> float | None:
    """An RFC 3339 date-time as epoch seconds, else ``None``.

    The offset is required — ``Z`` or ``±HH:MM`` (``±HHMM`` is tolerated) — so
    a value without one is refused rather than given a guessed timezone.
    Fractional seconds may have any number of digits; those beyond the
    microsecond are dropped. Surrounding whitespace is ignored. Anything else,
    including an impossible date, time or offset, is ``None``.
    """

    if not isinstance(value, str):
        return None
    match = _INSTANT.fullmatch(value.strip())
    if match is None:
        return None
    year, month, day, hour, minute, second, fraction, zulu, sign, off_h, off_m = match.groups()
    microsecond = int(fraction[:6].ljust(6, "0")) if fraction else 0
    try:
        if zulu:
            zone = timezone.utc
        else:
            if int(off_m) > 59:
                return None
            offset = timedelta(hours=int(off_h), minutes=int(off_m))
            zone = timezone(-offset if sign == "-" else offset)
        parsed = datetime(
            int(year), int(month), int(day),
            int(hour), int(minute), int(second), microsecond,
            tzinfo=zone,
        )
    except (ValueError, OverflowError):
        return None
    return parsed.timestamp()
