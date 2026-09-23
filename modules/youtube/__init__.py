"""YouTube live-chat input on the v2 runtime (phase 3 R7).

The manifest beside this package declares ``manifest_version: 2``, so the
loader hands :func:`activate` a scoped runtime context, and the handle it
returns is driven by the phase coordinator through the hooks of the ``input``
role it declares (R4):

``validate_settings``  The module-owned hook the manifest names: one
                       value-free diagnostic per offending field (R7).
``activate``           Parses the settings and creates the HTTP session. It
                       makes no request and produces no input.
``prepare``            Nothing to fetch: an access token is obtained only
                       when a request needs one.
``start_inputs``       After the readiness barrier: starts one poller per
                       configured channel, on the injected sleeper.
``stop_inputs``        Stops the pollers; no request is issued afterwards.
``drain``              Nothing is in flight once the pollers are stopped.
``close``              Releases the session exactly once.

**Authentication** (R7). The configured ``refresh_token`` is exchanged at
:data:`TOKEN_URL` (``grant_type=refresh_token`` with ``client_id`` and
``client_secret``) for an access token and its lifetime. :class:`TokenManager`
refreshes lazily: only when a request needs a token and the held one is
missing or within :data:`REFRESH_MARGIN_SECONDS` of its expiry, so a token is
never used in its last minute and never refreshed earlier than that. A refused
refresh (a 4xx or an unusable answer) is final: one ``module.degraded`` names
:data:`REASON_AUTH_REFRESH_FAILED` and every poller stops. A lost or 5xx
answer is not a refusal: the poller retries after
:data:`TRANSIENT_RETRY_SECONDS`.

**Polling** (R7). Each poller resolves its channel's active broadcast with one
lookup at :data:`BROADCASTS_URL` (``broadcastStatus=active``; the item whose
``snippet.channelId`` is the channel gives ``snippet.liveChatId``), then lists
:data:`MESSAGES_URL` page after page, sleeping
``max(pollingIntervalMillis / 1000, min_poll_interval_seconds)`` on the
injected sleeper between two lists. No broadcast yet: the lookup is repeated
after :data:`LOOKUP_RETRY_SECONDS`. A chat that ended (403/404) is looked up
again; a 401 drops the held token so the next request refreshes it.

**Quota ledger** (R7). :class:`QuotaLedger` charges every API request its
configured cost (``quota.costs``) when it is issued — the token exchange is
not an API request and costs nothing. A read (a lookup or a list) is issued
only if ``remaining − cost ≥ write_reserve_units``; otherwise the module
reports ``module.degraded`` naming :data:`REASON_QUOTA_EXHAUSTED` — once per
quota day — and stops reading until the reset. A send is issued only if
``remaining ≥ cost`` (:meth:`QuotaLedger.try_send`, used by ``chat.write``
and the moderation service). The ledger resets at 00:00 America/Los_Angeles
on the injected wall clock (:func:`next_quota_reset`, plan decision 10): the
instant is computed from the US daylight-saving rule with integer arithmetic,
so no time-zone database is needed. When the reset passes, reads resume and
the module reports itself ready again.

Normalisation of listed messages, ``chat.write`` and the moderation service
are plan step P21's; this step lists pages and counts their items.
"""

from __future__ import annotations

import asyncio
import inspect
import math
import sys
import time
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

try:  # Keep the module importable for transport-injected contract tests.
    import aiohttp
except ModuleNotFoundError:  # pragma: no cover - production installs dependencies
    aiohttp = None  # type: ignore[assignment]


MODULE_NAME = "youtube"

PLATFORM = "youtube"
"""``payload.platform`` of every event this input normalises (R3).

A stable platform identifier, never derived from the module name, which is a
deployment label.
"""

TOKEN_URL = "https://oauth2.googleapis.com/token"
"""Where the refresh token is exchanged for an access token."""

API_BASE_URL = "https://www.googleapis.com/youtube/v3"
BROADCASTS_URL = f"{API_BASE_URL}/liveBroadcasts"
"""The active-broadcast lookup: gives the channel's ``liveChatId``."""

MESSAGES_URL = f"{API_BASE_URL}/liveChat/messages"
"""The live-chat messages endpoint: list (GET), insert (POST), delete (DELETE)."""

BANS_URL = f"{API_BASE_URL}/liveChat/bans"
"""The live-chat bans endpoint a timeout posts to."""

REFRESH_MARGIN_SECONDS = 60.0
"""How long before its expiry an access token stops being used (R7).

A fixed constant: a request needing a token at or after ``expires_at − 60 s``
refreshes it first, so a request never leaves with a token about to lapse in
flight, and no refresh happens earlier than that.
"""

DEFAULT_MIN_POLL_INTERVAL_SECONDS = 5
MIN_POLL_INTERVAL_BOUNDS = (1, 60)
"""The range of ``min_poll_interval_seconds``."""

LOOKUP_RETRY_SECONDS = 60.0
"""How long a poller waits before looking up a channel with no active broadcast."""

TRANSIENT_RETRY_SECONDS = 30.0
"""How long a poller waits after a lost or 5xx answer (token or API)."""

REQUEST_TIMEOUT_SECONDS = 10.0
"""Budget of one request, answer body included."""

DEFAULT_DAILY_UNITS = 10_000
DEFAULT_WRITE_RESERVE_UNITS = 1_000

OP_LIST = "list"
OP_INSERT = "insert"
OP_DELETE = "delete"
OP_BAN = "ban"
OP_BROADCAST_LOOKUP = "broadcast_lookup"
DEFAULT_COSTS: Mapping[str, int] = {
    OP_LIST: 5,
    OP_INSERT: 50,
    OP_DELETE: 50,
    OP_BAN: 50,
    OP_BROADCAST_LOOKUP: 1,
}
"""The units each request kind is charged unless ``quota.costs`` says otherwise."""

READ_OPERATIONS = frozenset({OP_LIST, OP_BROADCAST_LOOKUP})
SEND_OPERATIONS = frozenset({OP_INSERT, OP_DELETE, OP_BAN})

REASON_AUTH_REFRESH_FAILED = "auth_refresh_failed"
REASON_QUOTA_EXHAUSTED = "quota_exhausted"
"""The value-free reasons ``module.degraded`` carries (R7, R8)."""

NOTICE_KINDS: tuple[str, ...] = ("sub", "resub", "sub_gift", "tip")
"""Every kind ``notices.kinds`` may list."""

# Counters of what the pollers did, readable through ``YouTubeModule.counts``.
COUNT_ITEMS_LISTED = "items_listed"
COUNT_READS_WITHHELD = "reads_withheld"

_DAY_SECONDS = 86_400
_HOUR_SECONDS = 3_600
# America/Los_Angeles: UTC−8 standard, UTC−7 daylight (plan decision 10).
_PACIFIC_STANDARD_OFFSET = -8 * _HOUR_SECONDS
_PACIFIC_DAYLIGHT_OFFSET = -7 * _HOUR_SECONDS


class YouTubeModuleError(RuntimeError):
    """A YouTube operation failure whose text is safe to surface."""


# --------------------------------------------------------------------------- #
# The quota day: 00:00 America/Los_Angeles without a tz database (decision 10)
# --------------------------------------------------------------------------- #


def _days_from_civil(year: int, month: int, day: int) -> int:
    """Days since 1970-01-01 of a proleptic Gregorian date (integer only)."""

    year -= month <= 2
    era = (year if year >= 0 else year - 399) // 400
    year_of_era = year - era * 400
    day_of_year = (153 * (month + (-3 if month > 2 else 9)) + 2) // 5 + day - 1
    day_of_era = year_of_era * 365 + year_of_era // 4 - year_of_era // 100 + day_of_year
    return era * 146_097 + day_of_era - 719_468


def _year_of_day(days: int) -> int:
    """The Gregorian year of a day number (days since 1970-01-01)."""

    days += 719_468
    era = (days if days >= 0 else days - 146_096) // 146_097
    day_of_era = days - era * 146_097
    year_of_era = (
        day_of_era - day_of_era // 1460 + day_of_era // 36_524 - day_of_era // 146_096
    ) // 365
    day_of_year = day_of_era - (365 * year_of_era + year_of_era // 4 - year_of_era // 100)
    month_index = (5 * day_of_year + 2) // 153
    month = month_index + (3 if month_index < 10 else -9)
    return year_of_era + era * 400 + (month <= 2)


def _nth_sunday(year: int, month: int, nth: int) -> int:
    """The day number of the *nth* Sunday of a month."""

    first = _days_from_civil(year, month, 1)
    # 1970-01-01 was a Thursday: (days + 4) % 7 is 0 on a Sunday.
    first_sunday = first + (7 - (first + 4) % 7) % 7
    return first_sunday + 7 * (nth - 1)


def _pacific_daylight_span(year: int) -> tuple[int, int]:
    """The UTC instants daylight time starts and ends in *year*.

    US rule: the second Sunday of March at 02:00 standard (10:00 UTC) to the
    first Sunday of November at 02:00 daylight (09:00 UTC).
    """

    start = _nth_sunday(year, 3, 2) * _DAY_SECONDS + 2 * _HOUR_SECONDS - _PACIFIC_STANDARD_OFFSET
    end = _nth_sunday(year, 11, 1) * _DAY_SECONDS + 2 * _HOUR_SECONDS - _PACIFIC_DAYLIGHT_OFFSET
    return start, end


def pacific_utc_offset(instant: float) -> int:
    """The America/Los_Angeles UTC offset, in seconds, at an epoch *instant*."""

    whole = math.floor(instant)
    start, end = _pacific_daylight_span(_year_of_day(whole // _DAY_SECONDS))
    return _PACIFIC_DAYLIGHT_OFFSET if start <= whole < end else _PACIFIC_STANDARD_OFFSET


def next_quota_reset(instant: float) -> float:
    """The first 00:00 America/Los_Angeles strictly after an epoch *instant*.

    The local day of *instant* is found through its offset; the next local
    midnight is converted back through the offset in force at that midnight
    (daylight time changes at 02:00 local, never across a midnight), so the
    quota day is 23 hours long in March and 25 hours long in November.
    """

    whole = math.floor(instant)
    local_day = (whole + pacific_utc_offset(whole)) // _DAY_SECONDS
    midnight_standard = (local_day + 1) * _DAY_SECONDS - _PACIFIC_STANDARD_OFFSET
    offset = pacific_utc_offset(midnight_standard)
    return float((local_day + 1) * _DAY_SECONDS - offset)


# --------------------------------------------------------------------------- #
# The quota ledger (R7)
# --------------------------------------------------------------------------- #


class QuotaLedger:
    """The local daily ledger every API request is charged to (R7).

    ``remaining`` starts at ``daily_units`` each quota day. A read may be
    issued only while ``remaining − cost ≥ write_reserve_units``; a send only
    while ``remaining ≥ cost``. :meth:`try_read` and :meth:`try_send` check
    and charge in one step, with no await in between, so two concurrent
    requests cannot both pass on the same units.
    """

    __slots__ = ("_clock", "_costs", "_daily", "_exhausted", "_reserve", "_reset_at", "_used")

    def __init__(
        self,
        *,
        daily_units: int,
        write_reserve_units: int,
        costs: Mapping[str, int],
        clock: Callable[[], float],
    ) -> None:
        self._daily = int(daily_units)
        self._reserve = int(write_reserve_units)
        self._costs = dict(DEFAULT_COSTS) | dict(costs)
        self._clock = clock
        self._used = 0
        # The platform refused for quota: nothing, even a zero-cost request,
        # until the quota day resets.
        self._exhausted = False
        self._reset_at = next_quota_reset(float(clock()))

    @property
    def remaining(self) -> int:
        self._roll()
        return self._daily - self._used

    @property
    def reset_at(self) -> float:
        """The epoch instant the current quota day ends."""

        self._roll()
        return self._reset_at

    def cost(self, operation: str) -> int:
        return self._costs[operation]

    def can_read(self, operation: str) -> bool:
        remaining = self.remaining
        return not self._exhausted and remaining - self.cost(operation) >= self._reserve

    def can_send(self, operation: str) -> bool:
        remaining = self.remaining
        return not self._exhausted and remaining >= self.cost(operation)

    def try_read(self, operation: str) -> bool:
        """Charge a read if it leaves the write reserve intact."""

        if operation not in READ_OPERATIONS or not self.can_read(operation):
            return False
        self._used += self.cost(operation)
        return True

    def try_send(self, operation: str) -> bool:
        """Charge a send if the remaining units cover it."""

        if operation not in SEND_OPERATIONS or not self.can_send(operation):
            return False
        self._used += self.cost(operation)
        return True

    def exhaust(self) -> None:
        """The platform said the quota is spent: nothing more today."""

        self._roll()
        self._used = max(self._used, self._daily)
        self._exhausted = True

    def _roll(self) -> None:
        now = float(self._clock())
        if now >= self._reset_at:
            self._used = 0
            self._exhausted = False
            self._reset_at = next_quota_reset(now)


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #


_REQUIRED_STRINGS: tuple[str, ...] = (
    "client_id",
    "client_secret",
    "refresh_token",
    "companion_name",
)


def validate_settings(settings: Any) -> list[str]:
    """Check this module's settings; return one diagnostic per offending field.

    The hook ``settings_validator`` in the manifest names. Each diagnostic
    names the module and the field and nothing else: a rejected value may be
    a credential, so no value is ever echoed.
    """

    if not isinstance(settings, Mapping):
        return [_setting_diagnostic("settings", "must be a mapping")]
    diagnostics: list[str] = []
    for name in _REQUIRED_STRINGS:
        value = settings.get(name)
        if not isinstance(value, str) or not value.strip():
            diagnostics.append(_setting_diagnostic(name, "must be a non-empty string"))
    diagnostics.extend(_validate_channels(settings.get("channels")))
    interval = settings.get("min_poll_interval_seconds")
    low, high = MIN_POLL_INTERVAL_BOUNDS
    if interval is not None and (
        isinstance(interval, bool) or not isinstance(interval, int) or not low <= interval <= high
    ):
        diagnostics.append(
            _setting_diagnostic(
                "min_poll_interval_seconds", f"must be an integer from {low} to {high}"
            )
        )
    if settings.get("quota") is not None:
        diagnostics.extend(_validate_quota(settings["quota"]))
    if settings.get("notices") is not None:
        diagnostics.extend(_validate_notices(settings["notices"]))
    return diagnostics


def _validate_channels(channels: Any) -> list[str]:
    if (
        isinstance(channels, str)
        or not isinstance(channels, (list, tuple))
        or not channels
        or any(not isinstance(item, str) or not item.strip() for item in channels)
    ):
        return [
            _setting_diagnostic("channels", "must be a non-empty list of channel identifiers")
        ]
    if len(set(channels)) != len(channels):
        return [_setting_diagnostic("channels", "must be unique")]
    return []


def _is_count(value: Any, minimum: int) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and value >= minimum


def _validate_quota(quota: Any) -> list[str]:
    if not isinstance(quota, Mapping) or set(quota) - {
        "daily_units",
        "write_reserve_units",
        "costs",
    }:
        return [
            _setting_diagnostic(
                "quota", "must be a mapping of daily_units, write_reserve_units and costs"
            )
        ]
    diagnostics: list[str] = []
    daily = quota.get("daily_units")
    if daily is not None and not _is_count(daily, 1):
        diagnostics.append(_setting_diagnostic("quota.daily_units", "must be a positive integer"))
    reserve = quota.get("write_reserve_units")
    if reserve is not None and not _is_count(reserve, 0):
        diagnostics.append(
            _setting_diagnostic("quota.write_reserve_units", "must be a non-negative integer")
        )
    elif (
        _is_count(daily if daily is not None else DEFAULT_DAILY_UNITS, 1)
        and (reserve if reserve is not None else DEFAULT_WRITE_RESERVE_UNITS)
        >= (daily if daily is not None else DEFAULT_DAILY_UNITS)
    ):
        diagnostics.append(
            _setting_diagnostic("quota.write_reserve_units", "must be below quota.daily_units")
        )
    costs = quota.get("costs")
    if costs is not None:
        if not isinstance(costs, Mapping) or set(costs) - set(DEFAULT_COSTS):
            diagnostics.append(
                _setting_diagnostic(
                    "quota.costs", "must be a mapping of " + ", ".join(DEFAULT_COSTS)
                )
            )
        else:
            for name, value in costs.items():
                if not _is_count(value, 0):
                    diagnostics.append(
                        _setting_diagnostic(
                            f"quota.costs.{name}", "must be a non-negative integer"
                        )
                    )
    return diagnostics


def _validate_notices(notices: Any) -> list[str]:
    if not isinstance(notices, Mapping) or set(notices) - {"kinds"}:
        return [_setting_diagnostic("notices", "must be a mapping of kinds only")]
    kinds = notices.get("kinds")
    if kinds is None:
        return []
    if (
        isinstance(kinds, str)
        or not isinstance(kinds, (list, tuple))
        or any(kind not in NOTICE_KINDS for kind in kinds)
    ):
        return [
            _setting_diagnostic("notices.kinds", "must be a list of community-notice kinds")
        ]
    if len(set(kinds)) != len(kinds):
        return [_setting_diagnostic("notices.kinds", "must be unique")]
    return []


def _setting_diagnostic(field_name: str, reason: str) -> str:
    return f"module {MODULE_NAME!r}: field {field_name!r}: {reason}"


@dataclass(frozen=True, repr=False, slots=True)
class _Settings:
    client_id: str
    client_secret: str
    refresh_token: str
    channels: tuple[str, ...]
    companion_name: str
    min_poll_interval_seconds: int
    daily_units: int
    write_reserve_units: int
    costs: Mapping[str, int]
    notice_kinds: frozenset[str]

    @classmethod
    def from_mapping(cls, settings: Mapping[str, Any]) -> "_Settings":
        """Parse accepted settings; the first diagnostic of a refusal is raised."""

        diagnostics = validate_settings(settings)
        if diagnostics:
            raise YouTubeModuleError(diagnostics[0])
        quota = settings.get("quota") or {}
        notices = settings.get("notices") or {}
        return cls(
            client_id=settings["client_id"],
            client_secret=settings["client_secret"],
            refresh_token=settings["refresh_token"],
            channels=tuple(settings["channels"]),
            companion_name=settings["companion_name"],
            min_poll_interval_seconds=settings.get(
                "min_poll_interval_seconds", DEFAULT_MIN_POLL_INTERVAL_SECONDS
            ),
            daily_units=quota.get("daily_units", DEFAULT_DAILY_UNITS),
            write_reserve_units=quota.get("write_reserve_units", DEFAULT_WRITE_RESERVE_UNITS),
            costs=dict(DEFAULT_COSTS) | dict(quota.get("costs") or {}),
            notice_kinds=frozenset(notices.get("kinds") or ()),
        )


# --------------------------------------------------------------------------- #
# The token manager (R7)
# --------------------------------------------------------------------------- #


class AuthRefreshRefused(YouTubeModuleError):
    """The token endpoint refused the refresh token: final (R7)."""


class AuthRefreshUnavailable(YouTubeModuleError):
    """The token endpoint's answer was lost or a 5xx: retry later."""


class TokenManager:
    """Holds the access token and refreshes it only when a request needs it.

    :meth:`token` returns the held token while ``now < expires_at −
    REFRESH_MARGIN_SECONDS``; otherwise it performs one exchange at
    :data:`TOKEN_URL` first. Concurrent callers share one exchange. A refusal
    is remembered: every later call raises :class:`AuthRefreshRefused`
    without another request.
    """

    __slots__ = ("_access", "_clock", "_exchange", "_expires_at", "_lock", "_refused")

    def __init__(
        self,
        exchange: Callable[[], Awaitable[tuple[int | None, Any]]],
        clock: Callable[[], float],
    ) -> None:
        self._exchange = exchange
        self._clock = clock
        self._access: str | None = None
        self._expires_at = 0.0
        self._refused = False
        self._lock = asyncio.Lock()

    @property
    def expires_at(self) -> float | None:
        return self._expires_at if self._access is not None else None

    @property
    def refused(self) -> bool:
        return self._refused

    def invalidate(self) -> None:
        """Forget the held token: the platform no longer accepts it."""

        self._access = None

    async def token(self) -> str:
        async with self._lock:
            if self._refused:
                raise AuthRefreshRefused("youtube auth: refresh refused")
            now = float(self._clock())
            if self._access is not None and now < self._expires_at - REFRESH_MARGIN_SECONDS:
                return self._access
            self._access = None
            status, body = await self._exchange()
            if status is None or status >= 500:
                raise AuthRefreshUnavailable("youtube auth: token endpoint unavailable")
            access = body.get("access_token") if isinstance(body, Mapping) else None
            lifetime = body.get("expires_in") if isinstance(body, Mapping) else None
            if (
                status != 200
                or not isinstance(access, str)
                or not access.strip()
                or isinstance(lifetime, bool)
                or not isinstance(lifetime, (int, float))
                or not math.isfinite(lifetime)
                or lifetime <= REFRESH_MARGIN_SECONDS
            ):
                self._refused = True
                raise AuthRefreshRefused("youtube auth: refresh refused")
            self._access = access
            self._expires_at = now + float(lifetime)
            return access


# --------------------------------------------------------------------------- #
# The handle
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class _Poller:
    channel_id: str
    live_chat_id: str | None = None
    page_token: str | None = None
    task: Any = None


class _ReadWithheld(Exception):
    """The ledger would not let a read leave (no request was made)."""


class YouTubeModule:
    """The v2 handle: one HTTP session, one poller per channel, phase hooks.

    Every hook is idempotent and safe out of order — ``close`` after a failed
    ``prepare``, ``stop_inputs`` before any ``start_inputs`` — because the
    coordinator unwinds a failed startup through the ordinary shutdown
    sequence (R4).
    """

    def __init__(
        self,
        context: Any,
        settings: _Settings,
        session: Any,
        reporter: Callable[[str], None],
        wall_clock: Callable[[], float],
        sleeper: Callable[[float], Awaitable[Any]],
    ) -> None:
        self._supervision = getattr(context, "supervision", None)
        self._tasks = getattr(context, "tasks", None)
        self._settings = settings
        self._session = session
        self._reporter = reporter
        self._clock = wall_clock
        self._sleeper = sleeper
        self._ledger = QuotaLedger(
            daily_units=settings.daily_units,
            write_reserve_units=settings.write_reserve_units,
            costs=settings.costs,
            clock=wall_clock,
        )
        self._tokens = TokenManager(self._request_token, wall_clock)
        self._pollers = {channel: _Poller(channel) for channel in settings.channels}
        self._counts: dict[str, int] = {COUNT_ITEMS_LISTED: 0, COUNT_READS_WITHHELD: 0}
        # The reset instant of the quota day already reported exhausted.
        self._exhausted_day: float | None = None
        self._auth_failed = False
        self._prepared = False
        self._running = False
        self._stopping = False
        self._closed = False
        self._close_lock = asyncio.Lock()

    @property
    def ledger(self) -> QuotaLedger:
        """The daily quota ledger every request of this module is charged to."""

        return self._ledger

    @property
    def tokens(self) -> TokenManager:
        return self._tokens

    @property
    def counts(self) -> Mapping[str, int]:
        """``items_listed`` (chat items received) and ``reads_withheld``
        (reads the ledger kept back)."""

        return dict(self._counts)

    def live_chat_id(self, channel_id: str) -> str | None:
        """The resolved live chat of *channel_id*, ``None`` until looked up."""

        poller = self._pollers.get(channel_id)
        return poller.live_chat_id if poller is not None else None

    # -- phases -------------------------------------------------------------- #

    async def prepare(self) -> None:
        """No request: a token is obtained when the first request needs it."""

        if self._closed:
            return
        self._prepared = True

    async def start_inputs(self) -> None:
        """Start one poller per channel. Only the coordinator, past the
        barrier, calls this."""

        if self._running or self._stopping or self._closed:
            return
        if not self._prepared:
            raise YouTubeModuleError("youtube lifecycle: start_inputs requires prepare")
        self._running = True
        for index, poller in enumerate(self._pollers.values(), start=1):
            poller.task = self._spawn(self._poll_loop(poller), name=f"youtube-poll-{index}")

    async def stop_inputs(self) -> None:
        """Stop every poller; no request leaves afterwards."""

        self._stopping = True
        self._running = False
        await self._cancel_pollers()

    async def drain(self, deadline_seconds: float) -> None:
        """Nothing outlives :meth:`stop_inputs`: the pollers are the only work."""

        del deadline_seconds

    async def close(self) -> None:
        """Stop the pollers and release the session exactly once."""

        async with self._close_lock:
            if self._closed:
                return
            self._stopping = True
            self._running = False
            try:
                await self._cancel_pollers()
            finally:
                self._closed = True
                await _close_session(self._session)

    async def _cancel_pollers(self) -> None:
        tasks = []
        for poller in self._pollers.values():
            task, poller.task = poller.task, None
            if task is not None and not task.done():
                task.cancel()
                tasks.append(task)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _spawn(self, coro: Awaitable[Any], *, name: str) -> Any:
        spawn = getattr(self._tasks, "spawn", None)
        if callable(spawn):
            return spawn(coro, name=name)
        return asyncio.ensure_future(coro)

    # -- polling ------------------------------------------------------------- #

    async def _poll_loop(self, poller: _Poller) -> None:
        """Look the chat up, then list it at the governed cadence, until
        stopped, refused authentication, or closed."""

        while self._running and not self._auth_failed:
            try:
                if poller.live_chat_id is None:
                    delay = await self._look_up(poller)
                else:
                    delay = await self._list(poller)
            except _ReadWithheld:
                self._counts[COUNT_READS_WITHHELD] += 1
                await self._report_exhausted()
                reset_at = self._ledger.reset_at
                await self._sleeper(max(reset_at - float(self._clock()), 0.0))
                await self._report_resumed()
                continue
            except AuthRefreshRefused:
                await self._report_auth_failed()
                return
            except AuthRefreshUnavailable:
                self._diagnose("youtube auth: token endpoint unavailable; retrying")
                delay = TRANSIENT_RETRY_SECONDS
            await self._sleeper(delay)

    async def _look_up(self, poller: _Poller) -> float:
        """One active-broadcast lookup; the delay before the next request."""

        status, body = await self._read(
            OP_BROADCAST_LOOKUP,
            "youtube broadcast lookup",
            lambda session, headers: session.get(
                BROADCASTS_URL,
                params={"part": "snippet", "broadcastStatus": "active", "broadcastType": "all"},
                headers=headers,
            ),
        )
        if status is None or status >= 500:
            return TRANSIENT_RETRY_SECONDS
        if status == 401:
            self._tokens.invalidate()
            return float(self._settings.min_poll_interval_seconds)
        self._check_quota_refusal(status, body)
        live_chat_id = _active_live_chat(body, poller.channel_id) if status == 200 else None
        if live_chat_id is None:
            return LOOKUP_RETRY_SECONDS
        poller.live_chat_id = live_chat_id
        poller.page_token = None
        return 0.0

    async def _list(self, poller: _Poller) -> float:
        """One list request; the delay before the next one (R7)."""

        params = {"liveChatId": poller.live_chat_id, "part": "snippet,authorDetails"}
        if poller.page_token is not None:
            params["pageToken"] = poller.page_token
        status, body = await self._read(
            OP_LIST,
            "youtube chat list",
            lambda session, headers: session.get(MESSAGES_URL, params=params, headers=headers),
        )
        minimum = float(self._settings.min_poll_interval_seconds)
        if status is None or status >= 500:
            return max(minimum, TRANSIENT_RETRY_SECONDS)
        if status == 401:
            self._tokens.invalidate()
            return minimum
        self._check_quota_refusal(status, body)
        if status in (403, 404):
            # The chat ended or is gone: look the broadcast up again.
            poller.live_chat_id = None
            poller.page_token = None
            return minimum
        if status != 200 or not isinstance(body, Mapping):
            return minimum
        items = body.get("items")
        if isinstance(items, list):
            self._counts[COUNT_ITEMS_LISTED] += len(items)
        next_token = body.get("nextPageToken")
        if isinstance(next_token, str) and next_token:
            poller.page_token = next_token
        return poll_interval(body.get("pollingIntervalMillis"), minimum)

    def _check_quota_refusal(self, status: int, body: Any) -> None:
        """A platform ``quotaExceeded`` means today's units are spent."""

        if status == 403 and _error_reason(body) in {"quotaExceeded", "dailyLimitExceeded"}:
            self._ledger.exhaust()

    async def _read(
        self,
        operation: str,
        label: str,
        send: Callable[[Any, Mapping[str, str]], Any],
    ) -> tuple[int | None, Any]:
        """Obtain a token, charge the read, send it; ``(status, body)``.

        Raises :class:`_ReadWithheld` when the ledger keeps the read back —
        checked after the token so a refresh never races the charge — and
        lets the token manager's refusals through.
        """

        token = await self._tokens.token()
        if not self._ledger.try_read(operation):
            raise _ReadWithheld
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        return await self._exchange(label, lambda session: send(session, headers))

    async def _request_token(self) -> tuple[int | None, Any]:
        settings = self._settings
        return await self._exchange(
            "youtube token refresh",
            lambda session: session.post(
                TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "client_id": settings.client_id,
                    "client_secret": settings.client_secret,
                    "refresh_token": settings.refresh_token,
                },
                headers={"Accept": "application/json"},
            ),
        )

    async def _exchange(self, label: str, send: Callable[[Any], Any]) -> tuple[int | None, Any]:
        """Send one request; ``(status, body)``, never raised.

        ``status`` is ``None`` when the answer is lost. Diagnostics name the
        operation and the status only: credentials live in the request and
        the answer body never reaches them.
        """

        if self._closed:
            self._diagnose(f"{label}: request not sent (closed)")
            return None, None
        try:
            status, body = await asyncio.wait_for(
                self._send_and_read(send), REQUEST_TIMEOUT_SECONDS
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            self._diagnose(f"{label}: request lost")
            return None, None
        if status is None:
            self._diagnose(f"{label}: answer lost")
        elif not 200 <= status < 300:
            self._diagnose(f"{label}: refused (status {status})")
        return status, body

    async def _send_and_read(self, send: Callable[[Any], Any]) -> tuple[int | None, Any]:
        response = await _resolve(send(self._session))
        return await _read_response(response)

    # -- health ---------------------------------------------------------------- #

    async def _report_exhausted(self) -> None:
        """``quota_exhausted``, once per quota day."""

        day = self._ledger.reset_at
        if self._exhausted_day == day:
            return
        self._exhausted_day = day
        self._diagnose("youtube quota: reads withheld until the quota day resets")
        await self._health("degraded", reason=REASON_QUOTA_EXHAUSTED)

    async def _report_resumed(self) -> None:
        """The quota day reset: reads resume, and the module is ready again."""

        if self._exhausted_day is None or self._exhausted_day == self._ledger.reset_at:
            return
        if not self._auth_failed:
            await self._health("ready")

    async def _report_auth_failed(self) -> None:
        if self._auth_failed:
            return
        self._auth_failed = True
        self._diagnose("youtube auth: refresh refused; polling stopped")
        await self._health("degraded", reason=REASON_AUTH_REFRESH_FAILED)

    async def _health(self, state: str, **details: Any) -> None:
        report = getattr(self._supervision, state, None)
        if not callable(report):
            return
        try:
            outcome = report(**details)
            if inspect.isawaitable(outcome):
                await outcome
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a health report must not stop a poller
            self._diagnose(f"youtube health: {state} report failed")

    def _diagnose(self, message: str) -> None:
        with suppress(Exception):
            self._reporter(message)


def poll_interval(polling_interval_millis: Any, minimum: float) -> float:
    """``max(pollingIntervalMillis / 1000, minimum)`` (R7); an absent or
    unusable server interval leaves the minimum."""

    if (
        isinstance(polling_interval_millis, bool)
        or not isinstance(polling_interval_millis, (int, float))
        or not math.isfinite(polling_interval_millis)
        or polling_interval_millis < 0
    ):
        return minimum
    return max(polling_interval_millis / 1000.0, minimum)


def _active_live_chat(body: Any, channel_id: str) -> str | None:
    """The ``liveChatId`` of *channel_id*'s active broadcast in a lookup answer."""

    items = body.get("items") if isinstance(body, Mapping) else None
    for item in items if isinstance(items, list) else ():
        snippet = item.get("snippet") if isinstance(item, Mapping) else None
        if not isinstance(snippet, Mapping) or snippet.get("channelId") != channel_id:
            continue
        live_chat_id = snippet.get("liveChatId")
        if isinstance(live_chat_id, str) and live_chat_id.strip():
            return live_chat_id
    return None


def _error_reason(body: Any) -> str | None:
    error = body.get("error") if isinstance(body, Mapping) else None
    errors = error.get("errors") if isinstance(error, Mapping) else None
    first = errors[0] if isinstance(errors, list) and errors else None
    reason = first.get("reason") if isinstance(first, Mapping) else None
    return reason if isinstance(reason, str) else None


# --------------------------------------------------------------------------- #
# Activation
# --------------------------------------------------------------------------- #


def _default_session_factory() -> Any:
    if aiohttp is None:
        raise RuntimeError("aiohttp is not installed")
    return aiohttp.ClientSession()


SESSION_FACTORY: Callable[[], Any] = _default_session_factory
WALL_CLOCK: Callable[[], float] = time.time
SLEEPER: Callable[[float], Awaitable[Any]] = asyncio.sleep


async def activate(
    context: Any,
    settings: Mapping[str, Any],
    catalog: Mapping[str, Mapping[str, Any]],
) -> YouTubeModule:
    """Build the handle from the scoped runtime context (R7).

    Nothing here reaches the network, so a refused activation has nothing to
    undo. ``_session_factory``, ``_wall_clock`` and ``_sleeper`` are the
    injected transport, clock and sleeper: token expiry, the poll cadence and
    the quota day are all read on the wall clock, never on the runtime's
    monotonic one, because the quota day is a calendar fact.
    """

    del catalog
    _require_surfaces(context)
    parsed = _Settings.from_mapping(settings)
    reporter = settings.get("diagnostic_reporter", _default_reporter)
    session_factory = settings.get("_session_factory", SESSION_FACTORY)
    wall_clock = settings.get("_wall_clock", WALL_CLOCK)
    sleeper = settings.get("_sleeper", SLEEPER)
    if not callable(reporter):
        raise YouTubeModuleError("youtube configuration: diagnostic reporter is invalid")
    if not callable(session_factory) or not callable(wall_clock) or not callable(sleeper):
        raise YouTubeModuleError("youtube configuration: transport seam is invalid")
    try:
        created = session_factory()
        session = await created if inspect.isawaitable(created) else created
    except Exception:
        _safe_report(reporter, "youtube transport: session creation failed")
        raise YouTubeModuleError("youtube transport initialization failed") from None
    return YouTubeModule(context, parsed, session, reporter, wall_clock, sleeper)


_REQUIRED_SURFACES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("bus", ("publish",)),
    ("supervision", ("emit",)),
)


def _require_surfaces(context: Any) -> None:
    for name, methods in _REQUIRED_SURFACES:
        value = getattr(context, name, None)
        if value is None or not all(callable(getattr(value, m, None)) for m in methods):
            raise YouTubeModuleError("youtube activation: runtime context is invalid")


async def _read_response(response: Any) -> tuple[int | None, Any]:
    try:
        status = getattr(response, "status", None)
        try:
            body = await _resolve(response.json())
        except Exception:
            body = None
        return (status if isinstance(status, int) else None), body
    finally:
        release = getattr(response, "release", None)
        if callable(release):
            released = release()
            if inspect.isawaitable(released):
                await released


async def _resolve(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


async def _close_session(session: Any) -> None:
    close = getattr(session, "close", None)
    if callable(close):
        with suppress(Exception):
            result = close()
            if inspect.isawaitable(result):
                await result


def _safe_report(reporter: Callable[[str], None], message: str) -> None:
    with suppress(Exception):
        reporter(message)


def _default_reporter(message: str) -> None:
    print(message, file=sys.stderr)


__all__ = [
    "API_BASE_URL",
    "AuthRefreshRefused",
    "AuthRefreshUnavailable",
    "BANS_URL",
    "BROADCASTS_URL",
    "DEFAULT_COSTS",
    "DEFAULT_MIN_POLL_INTERVAL_SECONDS",
    "LOOKUP_RETRY_SECONDS",
    "MESSAGES_URL",
    "MODULE_NAME",
    "NOTICE_KINDS",
    "PLATFORM",
    "QuotaLedger",
    "REASON_AUTH_REFRESH_FAILED",
    "REASON_QUOTA_EXHAUSTED",
    "REFRESH_MARGIN_SECONDS",
    "TOKEN_URL",
    "TRANSIENT_RETRY_SECONDS",
    "TokenManager",
    "YouTubeModule",
    "YouTubeModuleError",
    "activate",
    "next_quota_reset",
    "pacific_utc_offset",
    "poll_interval",
    "validate_settings",
]
