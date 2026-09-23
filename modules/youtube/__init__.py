"""YouTube live-chat input on the v2 runtime (phase 3 R7).

The manifest beside this package declares ``manifest_version: 2``, so the
loader hands :func:`activate` a scoped runtime context, and the handle it
returns is driven by the phase coordinator through the hooks of the ``input``
role it declares (R4):

``validate_settings``  The module-owned hook the manifest names: one
                       value-free diagnostic per offending field (R7).
``activate``           Parses the settings, creates the HTTP session and,
                       on a bound service registry, publishes the
                       moderation service. It makes no request and produces
                       no input.
``prepare``            Binds ``chat.write`` and marks it ready; nothing to
                       fetch: an access token is obtained only when a
                       request needs one.
``start_inputs``       After the readiness barrier: starts one poller per
                       configured channel, on the injected sleeper.
``stop_inputs``        Stops the pollers; no request is issued afterwards.
``drain``              Nothing is in flight once the pollers are stopped.
``close``              Marks ``chat.write`` not ready and releases the
                       session exactly once.

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

**Reception** (R1, R6, R7). Each listed item is normalised once into the
schema-version-2 chat event under ``metadata.source = "youtube"``:
``textMessageEvent`` is a ``message``; ``newSponsorEvent``,
``memberMilestoneChatEvent``, ``membershipGiftingEvent``, ``superChatEvent``
and ``superStickerEvent`` are the notices ``sub``, ``resub``, ``sub_gift``,
``tip`` and ``tip``, ingested only when ``notices.kinds`` lists them; an
unlisted or unmapped type is counted in ``notices_ignored``. The author's
``authorDetails`` flags ``isChatOwner``, ``isChatModerator`` and
``isChatSponsor`` are its trusted roles (broadcaster, moderator,
subscription). Then, in the other chat inputs' order: an item already seen
inside the bounded window (:data:`DEDUP_MAX_ENTRIES`) is counted in
``duplicates`` and dropped; an author identifier containing ``:`` is refused
and counted in ``invalid``; the companion's own messages — a message id this
module inserted, or the configured name — are dropped (anti-echo); the chat
context is fed; the trigger engine decides; the event is published; the
decision is traced; an accepted event is admitted to the scheduler, and the
poller goes on without awaiting any run.

**Sending** (R7). ``prepare`` binds this module's provider to the
``chat.write`` its manifest declares, over ``youtube/*/chat``; a call for a
channel outside ``channels`` is refused ``unsupported_destination`` with 0
requests. A send is one insert at :data:`MESSAGES_URL` into the channel's
resolved live chat, never retried: a text over :data:`MAX_CHAT_TEXT_CHARS`
characters is ``error text_too_long`` with 0 requests; a send the ledger
cannot cover (``remaining < cost``) is ``refused quota_exhausted`` with 0
requests; a 429, or a 403 whose ``error.errors[0].reason`` is
``rateLimitExceeded``, is ``error rate_limited`` and blocks every further
send until the announced ``Retry-After`` (``refused rate_limited``, 0
requests; :data:`DEFAULT_RATE_LIMIT_SECONDS` when none is readable); a 403
``quotaExceeded`` is ``error quota_exhausted`` and syncs the local ledger to
0 for the day; any other refusal (``forbidden``,
``insufficientPermissions``, …) is ``error platform_rejected``; a lost,
timed-out or 5xx answer is ``external_unknown``. The platform has no reply
thread: ``parent_message_id`` is accepted and not forwarded.

**Moderation** (R5). Only when the context carries a service registry,
``activate`` publishes a moderation service under ``(moderation, youtube)``
whose ``operations`` are ``{delete_message, timeout}``: a delete is one
``liveChatMessages.delete`` of the message id; a timeout is one
``liveChatBans.insert`` of type :data:`BAN_TYPE_TEMPORARY` that always
carries ``banDurationSeconds`` — no other ban type is ever built. Both are
charged to the ledger as sends and share the sends' rate block: a rate
refusal of either blocks both until the announced retry instant. No clip and no poll service is published:
those capabilities stay unbound for this platform (``platform_unsupported``).
"""

from __future__ import annotations

import asyncio
import email.utils
import inspect
import math
import sys
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from core.admission import Work
from core.context import ChatEntry
from core.contracts import (
    EVENT_KIND_DEFAULT,
    TRACE_INPUT_TRIGGER_ACCEPTED,
    TRACE_INPUT_TRIGGER_REJECTED,
    ActionObservation,
    ActionSpec,
    Destination,
    SessionKey,
)
from core.triggers import NORMALIZED_SCHEMA_VERSION, TriggerContext, TrustedClaim

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

# Listed item types, as the platform names them, mapped to the event kind (R1).
EVENT_TEXT_MESSAGE = "textMessageEvent"
EVENT_KINDS: Mapping[str, str] = {
    EVENT_TEXT_MESSAGE: EVENT_KIND_DEFAULT,
    "newSponsorEvent": "sub",
    "memberMilestoneChatEvent": "resub",
    "membershipGiftingEvent": "sub_gift",
    "superChatEvent": "tip",
    "superStickerEvent": "tip",
}

# The ``authorDetails`` flags mapped to the trusted claim they attest. Only a
# platform-attested flag becomes a claim; nothing in the message text can.
_AUTHOR_FLAG_CLAIMS: tuple[tuple[str, str], ...] = (
    ("isChatOwner", "broadcaster"),
    ("isChatModerator", "moderator"),
    ("isChatSponsor", "subscription"),
)
_ROLES_PROVENANCE = "youtube.author_details"

RESERVED_SEPARATOR = ":"
"""No platform author identifier may contain it (R6): reserved identities do."""

DEDUP_MAX_ENTRIES = 4096
"""How many listed message ids (and ids this module inserted) are retained."""

_CHAT_EVENT = "channel.chat.message"
_CHAT_SCOPE = "chat"

CHAT_WRITE_ACTION = "chat.write"
MANIFEST_PATH = Path(__file__).with_name("module.yaml")
CHAT_WRITE_PROVIDER = "youtube-chat"
"""The provider name ``chat.write`` is bound under."""

MAX_CHAT_TEXT_CHARS = 200
"""The longest text the platform accepts in one chat message (R7)."""

DEFAULT_RATE_LIMIT_SECONDS = 60.0
"""How long sends stay blocked after a rate refusal with no readable retry."""

MAX_RATE_LIMIT_SECONDS = 3600.0
"""The longest block any announced retry instant can impose."""

RETRY_SOURCE_RETRY_AFTER = "retry_after"
RETRY_SOURCE_DEFAULT = "default"

# `error.errors[0].reason` of a refusal: a rate refusal, a spent quota.
RATE_LIMIT_REASONS = frozenset({"rateLimitExceeded", "userRateLimitExceeded"})
QUOTA_REASONS = frozenset({"quotaExceeded", "dailyLimitExceeded"})

MODERATION_SERVICE_KIND = "moderation"
MODERATION_OPERATIONS = frozenset({"delete_message", "timeout"})
"""What this platform's moderation service offers (R5)."""

BAN_TYPE_TEMPORARY = "temporary"
"""The only ban type this module ever builds: a timeout with a duration."""

MODERATION_OK = "ok"
OUTCOME_REJECTED = "rejected"
OUTCOME_RATE = "rate"
OUTCOME_UNCERTAIN = "uncertain"

ERROR_TEXT_TOO_LONG = "text_too_long"
ERROR_RATE_LIMITED = "rate_limited"
ERROR_QUOTA_EXHAUSTED = "quota_exhausted"
ERROR_NO_LIVE_CHAT = "no_live_chat"
ERROR_AUTH_REFRESH_FAILED = "auth_refresh_failed"
_ERROR_INVALID_ARGUMENTS = "invalid_arguments"
_ERROR_UNSUPPORTED_DESTINATION = "unsupported_destination"
_ERROR_PROVIDER_CLOSED = "provider_closed"
_ERROR_PLATFORM_REJECTED = "platform_rejected"
_ERROR_TRANSPORT_FAILED = "transport_failed"
_ERROR_MALFORMED_RESPONSE = "malformed_response"

_STATUS_SUCCESS = "success"
_STATUS_REFUSED = "refused"
_STATUS_ERROR = "error"
_STATUS_EXTERNAL_UNKNOWN = "external_unknown"

# Counters of what the pollers did, readable through ``YouTubeModule.counts``.
COUNT_ITEMS_LISTED = "items_listed"
COUNT_READS_WITHHELD = "reads_withheld"
COUNT_INVALID = "invalid"
COUNT_NOTICES_IGNORED = "notices_ignored"
COUNT_DUPLICATES = "duplicates"
COUNT_MALFORMED = "malformed"

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
# Normalisation (R1, R3, R7)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class _Notification:
    """One listed item, normalised exactly once at the boundary (R3).

    ``claims`` are the platform-attested facts about the author; only when
    ``roles_attested`` does the event carry ``author.roles``.
    """

    channel_id: str
    author_id: str
    author_name: str | None
    message_id: str
    text: str
    claims: tuple[TrustedClaim, ...]
    roles_attested: bool = False
    kind: str = EVENT_KIND_DEFAULT

    def event(self) -> dict[str, Any]:
        """The schema-version-2 bus event this item normalises to."""

        author: dict[str, Any] = {"id": self.author_id}
        if self.author_name is not None:
            author["display_name"] = self.author_name
        if self.roles_attested:
            author["roles"] = [claim.name for claim in self.claims]
            author["roles_provenance"] = _ROLES_PROVENANCE
        payload: dict[str, Any] = {
            "platform": PLATFORM,
            "channel_id": self.channel_id,
            "author": author,
            "message_id": self.message_id,
            "text": self.text,
        }
        if self.kind != EVENT_KIND_DEFAULT:
            payload["kind"] = self.kind
        return {
            "type": _CHAT_EVENT,
            "payload": payload,
            "metadata": {
                "source": MODULE_NAME,
                "schema_version": NORMALIZED_SCHEMA_VERSION,
            },
        }


def normalize_item(
    item: Any,
    channel_id: str,
    notice_kinds: frozenset[str] = frozenset(),
) -> _Notification | None:
    """Translate one listed live-chat item of *channel_id*, once (R1, R3, R7).

    ``None`` means an ignored item: a type this module does not map, or a
    notice kind ``notices.kinds`` does not list. Raises ``ValueError`` for an
    item lacking its id, its type, its author or, on a text message, its
    text.
    """

    if not isinstance(item, Mapping):
        raise ValueError
    snippet = item.get("snippet")
    if not isinstance(snippet, Mapping) or not isinstance(snippet.get("type"), str):
        raise ValueError
    kind = EVENT_KINDS.get(snippet["type"])
    if kind is None or (kind != EVENT_KIND_DEFAULT and kind not in notice_kinds):
        return None
    message_id = _optional_string(item.get("id"))
    details = item.get("authorDetails")
    if message_id is None or not isinstance(details, Mapping):
        raise ValueError
    author_id = _optional_string(details.get("channelId")) or _optional_string(
        snippet.get("authorChannelId")
    )
    if author_id is None:
        raise ValueError
    if kind == EVENT_KIND_DEFAULT:
        message = snippet.get("textMessageDetails")
        text = message.get("messageText") if isinstance(message, Mapping) else None
        if not isinstance(text, str):
            text = snippet.get("displayMessage")
        if not isinstance(text, str):
            raise ValueError
    else:
        text = _notice_comment(snippet)
    return _Notification(
        channel_id=channel_id,
        author_id=author_id,
        author_name=_optional_string(details.get("displayName")),
        message_id=message_id,
        text=text,
        claims=_trusted_claims(details),
        roles_attested=True,
        kind=kind,
    )


def _notice_comment(snippet: Mapping[str, Any]) -> str:
    """The viewer's own words attached to a notice, when it carries any."""

    for key in ("superChatDetails", "memberMilestoneChatDetails"):
        details = snippet.get(key)
        comment = details.get("userComment") if isinstance(details, Mapping) else None
        if isinstance(comment, str):
            return comment
    return ""


def _trusted_claims(details: Mapping[str, Any]) -> tuple[TrustedClaim, ...]:
    """The platform-attested roles of the author, from ``authorDetails``."""

    return tuple(
        TrustedClaim(claim, provenance=_ROLES_PROVENANCE)
        for flag, claim in _AUTHOR_FLAG_CLAIMS
        if details.get(flag) is True
    )


def _optional_string(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value
    return None


# --------------------------------------------------------------------------- #
# Rate refusals — read defensively (R7)
# --------------------------------------------------------------------------- #


def rate_limit_until(headers: Any, now: float) -> tuple[float, str]:
    """When sends may resume after a rate refusal, and where that came from.

    ``Retry-After`` — a delay in seconds or an HTTP date — clamped to
    ``0..MAX_RATE_LIMIT_SECONDS``; when absent or unreadable, the source is
    :data:`RETRY_SOURCE_DEFAULT` and the wait :data:`DEFAULT_RATE_LIMIT_SECONDS`
    (a 403 ``rateLimitExceeded`` usually announces none).
    """

    delay = _retry_after_delay(_header(headers, "Retry-After"), now)
    if delay is None:
        return now + DEFAULT_RATE_LIMIT_SECONDS, RETRY_SOURCE_DEFAULT
    return now + min(max(delay, 0.0), MAX_RATE_LIMIT_SECONDS), RETRY_SOURCE_RETRY_AFTER


def _header(headers: Any, name: str) -> str | None:
    """One header value by case-insensitive name; ``None`` when absent."""

    if not isinstance(headers, Mapping):
        return None
    folded = name.casefold()
    with suppress(Exception):
        for key, value in list(headers.items()):
            if isinstance(key, str) and key.casefold() == folded and isinstance(value, str):
                return value
    return None


def _retry_after_delay(value: str | None, now: float) -> float | None:
    if value is None or not value.strip():
        return None
    try:
        number = float(value.strip())
    except ValueError:
        number = None
    if number is not None:
        return number if math.isfinite(number) and number >= 0 else None
    try:
        parsed = email.utils.parsedate_to_datetime(value.strip())
    except (TypeError, ValueError, IndexError):
        return None
    if parsed is None or parsed.tzinfo is None:
        return None
    return parsed.timestamp() - now


# --------------------------------------------------------------------------- #
# The chat send provider and the moderation service (R5, R7)
# --------------------------------------------------------------------------- #


class _ChatWriteProvider:
    """The ``chat.write`` provider the executor invokes (R7).

    A thin handle: the module owns the transport, the ledger, the rate-limit
    state and the lifecycle, so the provider holds no state of its own.
    """

    __slots__ = ("_module",)

    name = CHAT_WRITE_PROVIDER

    def __init__(self, module: "YouTubeModule") -> None:
        self._module = module

    async def invoke(self, invocation: Any) -> ActionObservation:
        return await self._module._invoke_chat_write(invocation)


@dataclass(frozen=True, slots=True)
class ModerationResult:
    """A classified moderation answer: ``ok``, ``rejected``, ``rate`` or
    ``uncertain`` (R5)."""

    outcome: str


class _YouTubeModerationService:
    """The moderation service this module publishes under ``(moderation,
    youtube)`` (R5).

    ``operations`` is :data:`MODERATION_OPERATIONS`. ``delete_message`` is
    one DELETE of the message id at :data:`MESSAGES_URL`; ``timeout`` is one
    POST at :data:`BANS_URL` of type :data:`BAN_TYPE_TEMPORARY` carrying
    ``banDurationSeconds``. Refused here with 0 requests: another operation,
    a delete without a message id, a timeout without a positive whole
    duration or a target, a channel whose live chat is not resolved, a send
    the ledger cannot cover, or a refused token; ``rate`` with 0 requests
    while a rate refusal's retry instant — a send's or a moderation
    request's — has not passed. Any 2xx is ``ok``; a 429 or a 403
    ``rateLimitExceeded`` is ``rate`` and blocks every write until the
    announced retry instant; another 4xx ``rejected`` (a 403
    ``quotaExceeded`` also syncs the ledger to 0); a 5xx, a lost answer or a
    request that may have left ``uncertain``. Never retried.
    """

    __slots__ = ("_module",)

    operations = MODERATION_OPERATIONS

    def __init__(self, module: "YouTubeModule") -> None:
        self._module = module

    async def apply(
        self,
        operation: str,
        *,
        channel_id: str,
        message_id: str | None = None,
        target_author_id: str | None = None,
        duration_seconds: int | None = None,
        reason: str | None = None,
    ) -> ModerationResult:
        del reason  # The platform's ban carries no reason.
        module = self._module
        if operation == "delete_message":
            if _optional_string(message_id) is None:
                module._diagnose("youtube moderation delete: refused (no message id)")
                return ModerationResult(OUTCOME_REJECTED)
            op, label = OP_DELETE, "youtube moderation delete"
            params = {"id": message_id}

            def send(session: Any, headers: Mapping[str, str]) -> Any:
                return session.delete(MESSAGES_URL, params=params, headers=headers)

        elif operation == "timeout":
            if (
                isinstance(duration_seconds, bool)
                or not isinstance(duration_seconds, int)
                or duration_seconds <= 0
            ):
                # A ban without a duration would not end (R5): never built.
                module._diagnose("youtube moderation timeout: refused (no duration)")
                return ModerationResult(OUTCOME_REJECTED)
            if _optional_string(target_author_id) is None:
                module._diagnose("youtube moderation timeout: refused (no target)")
                return ModerationResult(OUTCOME_REJECTED)
            live_chat_id = module.live_chat_id(channel_id)
            if live_chat_id is None:
                module._diagnose("youtube moderation timeout: refused (no live chat)")
                return ModerationResult(OUTCOME_REJECTED)
            op, label = OP_BAN, "youtube moderation timeout"
            ban = {
                "snippet": {
                    "liveChatId": live_chat_id,
                    "type": BAN_TYPE_TEMPORARY,
                    "banDurationSeconds": duration_seconds,
                    "bannedUserDetails": {"channelId": target_author_id},
                }
            }

            def send(session: Any, headers: Mapping[str, str]) -> Any:
                return session.post(
                    BANS_URL, params={"part": "snippet"}, headers=headers, json=ban
                )

        else:
            module._diagnose("youtube moderation: refused (operation not offered)")
            return ModerationResult(OUTCOME_REJECTED)

        if module._rate_block_remaining() is not None:
            module._diagnose(f"{label}: refused (rate limited)")
            return ModerationResult(OUTCOME_RATE)
        try:
            status, body, answer_headers = await module._write(op, label, send, on_emit=None)
        except _WriteWithheld as withheld:
            module._diagnose(f"{label}: refused ({withheld.code})")
            return ModerationResult(OUTCOME_REJECTED)
        if status is None or status >= 500:
            return ModerationResult(OUTCOME_UNCERTAIN)
        if 200 <= status < 300:
            return ModerationResult(MODERATION_OK)
        reason_code = _error_reason(body)
        if status == 429 or (status == 403 and reason_code in RATE_LIMIT_REASONS):
            module._block_sends(answer_headers, label)
            return ModerationResult(OUTCOME_RATE)
        module._check_quota_refusal(status, body)
        return ModerationResult(OUTCOME_REJECTED)


class _WriteWithheld(Exception):
    """A send left no request: ``(status, code, message)`` of its refusal."""

    def __init__(self, status: str, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class _SendOutcome:
    status: str
    code: str | None = None
    message: str = ""
    message_id: str | None = None
    trace_fields: Mapping[str, Any] | None = None


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
        runtime = _runtime_surfaces(context)
        self._bus = runtime.bus
        self._actions = runtime.actions
        self._supervision = runtime.supervision
        self._triggers = runtime.triggers
        self._chat = runtime.chat
        self._scheduler = runtime.scheduler
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
        self._counts: dict[str, int] = {
            COUNT_ITEMS_LISTED: 0,
            COUNT_READS_WITHHELD: 0,
            COUNT_INVALID: 0,
            COUNT_NOTICES_IGNORED: 0,
            COUNT_DUPLICATES: 0,
            COUNT_MALFORMED: 0,
        }
        # Listed message ids already ingested, and ids this module inserted
        # (anti-echo), each bounded to DEDUP_MAX_ENTRIES, oldest first.
        self._seen: OrderedDict[str, None] = OrderedDict()
        self._sent_ids: OrderedDict[str, None] = OrderedDict()
        self._provider = _ChatWriteProvider(self)
        self._moderation_service = _YouTubeModerationService(self)
        # The wall-clock instant before which no send may leave (rate refusal).
        self._sends_blocked_until: float | None = None
        self._bound = False
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
        """``items_listed`` (chat items received), ``reads_withheld`` (reads
        the ledger kept back), and reception outcomes of listed items:
        ``invalid`` (an author identifier containing ``:``),
        ``notices_ignored``, ``duplicates`` and ``malformed``."""

        return dict(self._counts)

    @property
    def moderation_service(self) -> _YouTubeModerationService:
        """The moderation service ``activate`` publishes under ``(moderation,
        youtube)`` when the context carries a service registry."""

        return self._moderation_service

    @property
    def sends_blocked_until(self) -> float | None:
        """The wall-clock instant sends resume after a rate refusal; ``None``
        when no block was ever announced."""

        return self._sends_blocked_until

    def live_chat_id(self, channel_id: str) -> str | None:
        """The resolved live chat of *channel_id*, ``None`` until looked up."""

        poller = self._pollers.get(channel_id)
        return poller.live_chat_id if poller is not None else None

    # -- phases -------------------------------------------------------------- #

    async def prepare(self) -> None:
        """Bind ``chat.write`` and mark it ready. No request: a token is
        obtained when the first request needs it."""

        if self._prepared or self._closed:
            return
        self._bind_chat_write()
        self._actions.mark_ready()
        self._prepared = True

    def _bind_chat_write(self) -> None:
        """Register this module's provider under the declared ``chat.write``.

        The contract is read from the colocated manifest — the declaration
        the loader recorded — and bound over ``youtube/*/chat``; a call for a
        channel this module does not serve is refused by the provider with 0
        requests. An ambiguous binding fails here, before any request.
        """

        if self._bound:
            return
        spec = _declared_chat_write_spec()
        try:
            self._actions.register(
                spec,
                self._provider,
                destinations=Destination(platform=PLATFORM, channel_id="*", scope=_CHAT_SCOPE),
                provider_name=CHAT_WRITE_PROVIDER,
            )
        except Exception as exc:
            self._diagnose(f"youtube prepare: {exc}")
            raise YouTubeModuleError("youtube prepare: action binding failed") from None
        self._bound = True

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
            if self._prepared:
                with suppress(Exception):
                    self._actions.mark_not_ready()
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
            for item in items:
                await self._ingest(poller.channel_id, item)
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
        """Send one request; ``(status, body)``, never raised."""

        status, body, _headers = await self._exchange_full(label, send)
        return status, body

    async def _exchange_full(
        self, label: str, send: Callable[[Any], Any]
    ) -> tuple[int | None, Any, Any]:
        """Send one request; ``(status, body, headers)``, never raised.

        ``status`` is ``None`` when the answer is lost. Diagnostics name the
        operation and the status only: credentials live in the request and
        the answer body never reaches them.
        """

        if self._closed:
            self._diagnose(f"{label}: request not sent (closed)")
            return None, None, None
        try:
            status, body, headers = await asyncio.wait_for(
                self._send_and_read(send), REQUEST_TIMEOUT_SECONDS
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            self._diagnose(f"{label}: request lost")
            return None, None, None
        if status is None:
            self._diagnose(f"{label}: answer lost")
        elif not 200 <= status < 300:
            self._diagnose(f"{label}: refused (status {status})")
        return status, body, headers

    async def _send_and_read(self, send: Callable[[Any], Any]) -> tuple[int | None, Any, Any]:
        response = await _resolve(send(self._session))
        headers = getattr(response, "headers", None)
        status, body = await _read_response(response)
        return status, body, headers

    async def _write(
        self,
        operation: str,
        label: str,
        send: Callable[[Any, Mapping[str, str]], Any],
        *,
        on_emit: Callable[[], None] | None,
    ) -> tuple[int | None, Any, Any]:
        """Obtain a token, charge the send, send it; ``(status, body, headers)``.

        Raises :class:`_WriteWithheld` — no request left — when the handle is
        closed, the ledger cannot cover the send (``remaining < cost``,
        checked before the token and charged after it, with no await in
        between), or the token cannot be obtained. *on_emit* is called right
        before the one request leaves.
        """

        if self._closed:
            raise _WriteWithheld(_STATUS_ERROR, _ERROR_PROVIDER_CLOSED, f"{label}: transport closed")
        if not self._ledger.can_send(operation):
            raise _WriteWithheld(
                _STATUS_REFUSED, ERROR_QUOTA_EXHAUSTED, f"{label}: daily quota exhausted"
            )
        try:
            token = await self._tokens.token()
        except AuthRefreshRefused:
            await self._report_auth_failed()
            raise _WriteWithheld(
                _STATUS_ERROR, ERROR_AUTH_REFRESH_FAILED, f"{label}: authentication refused"
            ) from None
        except AuthRefreshUnavailable:
            raise _WriteWithheld(
                _STATUS_ERROR, _ERROR_TRANSPORT_FAILED, f"{label}: token endpoint unavailable"
            ) from None
        if self._closed:
            raise _WriteWithheld(_STATUS_ERROR, _ERROR_PROVIDER_CLOSED, f"{label}: transport closed")
        if not self._ledger.try_send(operation):
            raise _WriteWithheld(
                _STATUS_REFUSED, ERROR_QUOTA_EXHAUSTED, f"{label}: daily quota exhausted"
            )
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        if on_emit is not None:
            on_emit()
        return await self._exchange_full(label, lambda session: send(session, headers))

    # -- sending ------------------------------------------------------------- #

    async def _invoke_chat_write(self, invocation: Any) -> ActionObservation:
        """Serve one ``chat.write`` call for the executor (R7).

        Everything decidable here is refused before emission, with 0
        requests: a blank text or reply parent, a text over
        :data:`MAX_CHAT_TEXT_CHARS`, a channel outside ``channels``, a closed
        handle, a send while a rate refusal's retry instant has not passed, a
        channel whose live chat is not resolved, and a send the ledger cannot
        cover. Emission is signalled right before the one request leaves, so
        an interruption after it is ``external_unknown``, never a certain
        timeout.
        """

        invocation.mark_not_emitted()
        call = invocation.call
        destination = call.destination
        provenance: dict[str, Any] = {
            "provider": CHAT_WRITE_PROVIDER,
            "platform": PLATFORM,
            "channel_id": destination.channel_id,
        }
        arguments = call.arguments
        text = arguments.get("text")
        parent = arguments.get("parent_message_id")
        blank_parent = parent is not None and (
            not isinstance(parent, str) or not parent.strip()
        )
        if not isinstance(text, str) or not text.strip() or blank_parent:
            self._diagnose("youtube chat send: invalid input")
            outcome = _SendOutcome(
                _STATUS_ERROR,
                _ERROR_INVALID_ARGUMENTS,
                "text and parent_message_id must be non-blank strings",
            )
        elif len(text) > MAX_CHAT_TEXT_CHARS:
            outcome = _SendOutcome(
                _STATUS_ERROR,
                ERROR_TEXT_TOO_LONG,
                f"text exceeds {MAX_CHAT_TEXT_CHARS} characters",
            )
        elif (
            destination.platform != PLATFORM
            or destination.channel_id not in self._settings.channels
        ):
            outcome = _SendOutcome(
                _STATUS_ERROR,
                _ERROR_UNSUPPORTED_DESTINATION,
                "destination is outside the configured channels",
            )
        else:
            outcome = await self._send(
                destination.channel_id, text, on_emit=invocation.mark_emitted
            )

        if outcome.trace_fields:
            provenance["trace_fields"] = dict(outcome.trace_fields)
        if outcome.status == _STATUS_SUCCESS:
            return ActionObservation(
                status=_STATUS_SUCCESS,
                provenance=provenance,
                result={
                    "message_id": outcome.message_id,
                    "destination": {
                        "platform": PLATFORM,
                        "channel_id": destination.channel_id,
                    },
                },
            )
        return ActionObservation(
            status=outcome.status,
            provenance=provenance,
            error={
                "code": outcome.code or _ERROR_TRANSPORT_FAILED,
                "message": outcome.message,
                "retryable": False,
            },
        )

    async def _send(
        self, channel_id: str, text: str, *, on_emit: Callable[[], None]
    ) -> _SendOutcome:
        """One insert at :data:`MESSAGES_URL`, classified; never retried (R7)."""

        label = "youtube chat send"
        remaining = self._rate_block_remaining()
        if not self._closed and remaining is not None:
            return _SendOutcome(
                _STATUS_REFUSED,
                ERROR_RATE_LIMITED,
                f"{label}: rate limited by the platform",
                trace_fields={"retry_after_seconds": round(remaining, 3)},
            )
        live_chat_id = self.live_chat_id(channel_id)
        if not self._closed and live_chat_id is None:
            return _SendOutcome(
                _STATUS_ERROR, ERROR_NO_LIVE_CHAT, f"{label}: no active live chat resolved"
            )
        body = {
            "snippet": {
                "liveChatId": live_chat_id,
                "type": EVENT_TEXT_MESSAGE,
                "textMessageDetails": {"messageText": text},
            }
        }
        try:
            status, answer, headers = await self._write(
                OP_INSERT,
                label,
                lambda session, request_headers: session.post(
                    MESSAGES_URL, params={"part": "snippet"}, headers=request_headers, json=body
                ),
                on_emit=on_emit,
            )
        except _WriteWithheld as withheld:
            return _SendOutcome(withheld.status, withheld.code, withheld.message)
        if status is None or status >= 500:
            return _SendOutcome(
                _STATUS_EXTERNAL_UNKNOWN,
                _ERROR_TRANSPORT_FAILED,
                f"{label}: outcome unknown ({_safe_status(status)})",
            )
        reason = _error_reason(answer)
        if status == 429 or (status == 403 and reason in RATE_LIMIT_REASONS):
            return self._rate_limited(headers)
        if status == 403 and reason in QUOTA_REASONS:
            self._ledger.exhaust()
            return _SendOutcome(
                _STATUS_ERROR, ERROR_QUOTA_EXHAUSTED, f"{label}: daily quota exhausted"
            )
        if status == 401:
            self._tokens.invalidate()
        if not 200 <= status < 300:
            return _SendOutcome(
                _STATUS_ERROR, _ERROR_PLATFORM_REJECTED, f"{label}: rejected (status {status})"
            )
        message_id = _optional_string(answer.get("id")) if isinstance(answer, Mapping) else None
        if message_id is None:
            self._diagnose(f"{label}: malformed response")
            return _SendOutcome(
                _STATUS_EXTERNAL_UNKNOWN, _ERROR_MALFORMED_RESPONSE, f"{label}: malformed response"
            )
        _remember(self._sent_ids, message_id)
        return _SendOutcome(_STATUS_SUCCESS, message_id=message_id)

    def _rate_block_remaining(self) -> float | None:
        """Seconds until writes may resume after a rate refusal; ``None``
        when no block is in force. Sends and moderation share it (R7)."""

        blocked_until = self._sends_blocked_until
        if blocked_until is None:
            return None
        now = float(self._clock())
        return blocked_until - now if now < blocked_until else None

    def _block_sends(self, headers: Any, label: str) -> tuple[float, str]:
        """Record the announced retry instant and block every write until it;
        ``(wait in seconds, source)`` (R7)."""

        now = float(self._clock())
        until, source = rate_limit_until(headers, now)
        if self._sends_blocked_until is None or until > self._sends_blocked_until:
            self._sends_blocked_until = until
        if source == RETRY_SOURCE_DEFAULT:
            self._diagnose(
                f"{label}: no readable retry instant; "
                f"sends blocked for {DEFAULT_RATE_LIMIT_SECONDS:g} s"
            )
        return until - now, source

    def _rate_limited(self, headers: Any) -> _SendOutcome:
        """A send's rate refusal: block writes until the retry instant (R7)."""

        wait, source = self._block_sends(headers, "youtube chat send")
        return _SendOutcome(
            _STATUS_ERROR,
            ERROR_RATE_LIMITED,
            "youtube chat send: rate limited by the platform",
            trace_fields={
                "retry_after_seconds": round(wait, 3),
                "retry_source": source,
            },
        )

    # -- reception ------------------------------------------------------------ #

    async def _ingest(self, channel_id: str, item: Any) -> None:
        """Normalise one listed item and run it through the pipeline; never
        raises but a cancellation."""

        try:
            notification = normalize_item(item, channel_id, self._settings.notice_kinds)
        except ValueError:
            self._counts[COUNT_MALFORMED] += 1
            self._diagnose("youtube chat list: malformed item ignored")
            return
        if notification is None:
            self._counts[COUNT_NOTICES_IGNORED] += 1
            return
        try:
            await self._publish(notification)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - one item must not stop the poller
            self._diagnose("youtube chat item: ingestion failed")

    async def _publish(self, notification: _Notification) -> None:
        """Dedup, refuse reserved authors, drop echoes, feed, decide, publish,
        trace, admit — the other chat inputs' order (R1, R6, R7)."""

        if notification.message_id in self._seen:
            self._counts[COUNT_DUPLICATES] += 1
            return
        _remember(self._seen, notification.message_id)
        if RESERVED_SEPARATOR in notification.author_id:
            self._counts[COUNT_INVALID] += 1
            self._diagnose("youtube chat item: reserved author identity refused")
            return
        if self._is_own_message(notification):
            return
        engine = self._triggers
        if engine is not None and engine.recorded(
            platform=PLATFORM,
            channel_id=notification.channel_id,
            source_event_id=notification.message_id,
        ) is not None:
            return
        event = notification.event()
        self._feed_chat_context(notification)
        decision = None
        if engine is not None:
            try:
                decision = engine.evaluate(event, context=TriggerContext(notification.claims))
            except Exception:
                self._diagnose("youtube trigger evaluation: failed")
        try:
            await self._bus.publish(_CHAT_EVENT, event["payload"], event["metadata"])
        except asyncio.CancelledError:
            raise
        except Exception:
            self._diagnose("youtube chat item publish: failed")
        if decision is None:
            return
        await self._trace_decision(decision)
        if decision.accepted and self._scheduler is not None:
            self._admit(notification, event)

    def _is_own_message(self, notification: _Notification) -> bool:
        """A message this module inserted, or one under the companion's name,
        compared case-insensitively (anti-echo, R7)."""

        if notification.message_id in self._sent_ids:
            return True
        name = notification.author_name
        return name is not None and name.casefold() == self._settings.companion_name.casefold()

    def _feed_chat_context(self, notification: _Notification) -> None:
        if self._chat is None:
            return
        try:
            self._chat.append(
                PLATFORM,
                notification.channel_id,
                ChatEntry(
                    author_id=notification.author_id,
                    message_id=notification.message_id,
                    text=notification.text,
                ),
            )
        except Exception:
            self._diagnose("youtube chat context: append failed")

    async def _trace_decision(self, decision: Any) -> None:
        if not getattr(decision, "traceable", True):
            return
        event_type = (
            TRACE_INPUT_TRIGGER_ACCEPTED if decision.accepted else TRACE_INPUT_TRIGGER_REJECTED
        )
        payload = {
            "source_event_id": decision.source_event_id,
            "input": decision.input_name,
            "platform": decision.platform,
            "channel_id": decision.channel_id,
            "policy_version": decision.policy_version,
            "reason": decision.reason,
        }
        try:
            await self._supervision.emit(event_type, payload)
        except asyncio.CancelledError:
            raise
        except Exception:
            self._diagnose("youtube trigger trace: publication failed")

    def _admit(self, notification: _Notification, event: Mapping[str, Any]) -> None:
        try:
            self._scheduler.admit(
                SessionKey(
                    platform=PLATFORM,
                    channel_id=notification.channel_id,
                    viewer_id=notification.author_id,
                ),
                Work(
                    payload=event,
                    source_event_id=notification.message_id,
                    kind="chat.message",
                ),
            )
        except Exception:
            self._diagnose("youtube admission: failed")

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


def _remember(window: OrderedDict[str, None], key: str) -> None:
    """Record *key* in a bounded window, evicting the oldest past the bound."""

    window[key] = None
    window.move_to_end(key)
    while len(window) > DEDUP_MAX_ENTRIES:
        window.popitem(last=False)


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
    _runtime_surfaces(context)
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
    module = YouTubeModule(context, parsed, session, reporter, wall_clock, sleeper)
    # Published only on a bound registry: a context without one publishes
    # nothing (R7). No clip and no poll service exists on this platform, so
    # those capabilities stay unbound here with `platform_unsupported` (R2).
    services = getattr(context, "services", None)
    if services is not None and getattr(services, "available", False) is True:
        try:
            services.publish(MODERATION_SERVICE_KIND, PLATFORM, module.moderation_service)
        except BaseException:
            await _close_session(session)
            raise
    return module


@dataclass(frozen=True, slots=True)
class _RuntimeSurfaces:
    bus: Any
    actions: Any
    supervision: Any
    triggers: Any | None
    chat: Any | None
    scheduler: Any | None


_REQUIRED_SURFACES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("bus", ("publish",)),
    ("actions", ("register", "mark_ready", "mark_not_ready")),
    ("supervision", ("emit",)),
)
_OPTIONAL_SURFACES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("triggers", ("evaluate", "recorded")),
    ("chat", ("append",)),
    ("scheduler", ("admit",)),
)


def _runtime_surfaces(context: Any) -> _RuntimeSurfaces:
    """Read the runtime surfaces from *context*, checked by shape."""

    surfaces: dict[str, Any] = {}
    for name, methods in _REQUIRED_SURFACES:
        value = getattr(context, name, None)
        if value is None or not all(callable(getattr(value, m, None)) for m in methods):
            raise YouTubeModuleError("youtube activation: runtime context is invalid")
        surfaces[name] = value
    for name, methods in _OPTIONAL_SURFACES:
        value = getattr(context, name, None)
        if value is not None and not all(callable(getattr(value, m, None)) for m in methods):
            raise YouTubeModuleError("youtube activation: runtime context is invalid")
        surfaces[name] = value
    return _RuntimeSurfaces(**surfaces)


def _declared_chat_write_spec() -> ActionSpec:
    """Build the ``chat.write`` contract from the colocated manifest (R7).

    Read from the same file the loader validated at discovery, so the module
    serves exactly the contract it declares. Any defect is one value-free
    diagnostic.
    """

    try:
        manifest = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))
        entries = manifest["actions"] if isinstance(manifest, Mapping) else None
        entry = next(
            item
            for item in (entries if isinstance(entries, list) else ())
            if isinstance(item, Mapping) and item.get("name") == CHAT_WRITE_ACTION
        )
        return ActionSpec(
            name=entry["name"],
            version=entry["version"],
            description=entry["description"],
            argument_schema=entry["argument_schema"],
            result_schema=entry["result_schema"],
            nature=entry["nature"],
            required_permissions=tuple(entry.get("required_permissions", ())),
            supported_destinations=tuple(
                Destination(
                    platform=item.get("platform"),
                    channel_id=item.get("channel_id"),
                    scope=item.get("scope"),
                )
                for item in entry["supported_destinations"]
            ),
            timeout_seconds=entry["timeout_seconds"],
            idempotency=entry["idempotency"],
            delivery=entry.get("delivery"),
            model_proposable=entry.get("model_proposable", False),
        )
    except Exception:
        raise YouTubeModuleError(
            f"youtube prepare: manifest declaration of {CHAT_WRITE_ACTION!r} is invalid"
        ) from None


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


def _safe_status(status: Any) -> str:
    return str(status) if isinstance(status, int) else "unknown"


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
    "BAN_TYPE_TEMPORARY",
    "BROADCASTS_URL",
    "CHAT_WRITE_ACTION",
    "CHAT_WRITE_PROVIDER",
    "DEDUP_MAX_ENTRIES",
    "DEFAULT_COSTS",
    "DEFAULT_MIN_POLL_INTERVAL_SECONDS",
    "DEFAULT_RATE_LIMIT_SECONDS",
    "ERROR_QUOTA_EXHAUSTED",
    "ERROR_RATE_LIMITED",
    "ERROR_TEXT_TOO_LONG",
    "EVENT_KINDS",
    "LOOKUP_RETRY_SECONDS",
    "MANIFEST_PATH",
    "MAX_CHAT_TEXT_CHARS",
    "MESSAGES_URL",
    "MODERATION_OPERATIONS",
    "MODERATION_SERVICE_KIND",
    "MODULE_NAME",
    "ModerationResult",
    "NOTICE_KINDS",
    "PLATFORM",
    "QuotaLedger",
    "RETRY_SOURCE_DEFAULT",
    "RETRY_SOURCE_RETRY_AFTER",
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
    "normalize_item",
    "pacific_utc_offset",
    "rate_limit_until",
    "poll_interval",
    "validate_settings",
]
