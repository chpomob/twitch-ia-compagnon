"""The YouTube live-chat input (phase 3 R7, plan step P20).

AC36: with a scripted token endpoint issuing a 3600 s token, a refresh
happens before expiry and none before it is needed; a refused refresh yields
1 ``module.degraded`` naming ``auth_refresh_failed``; polling follows
max(server interval 2000 ms, ``min_poll_interval_seconds: 5``) = 5 s (at most
12 list requests per 60 s of injected clock) and max(8000 ms, 5 s) = 8 s when
the server asks for longer.

AC37 (the read half; the send half is plan step P21's, once ``chat.write``
exists): with ``quota.daily_units: 100``, a list cost of 5, a send cost of 50
and ``write_reserve_units: 50``, the module issues exactly 10 list requests,
then reports ``quota_exhausted`` and issues 0 more reads; after 00:00
America/Los_Angeles on the injected clock, reads resume. The LA-midnight
function is tested at both daylight-saving transitions and on ordinary days
(plan decision 10).

The session is a :class:`conftest.ScriptedLiveChatAPI` routing the token
exchange to a :class:`conftest.ScriptedTokenEndpoint`; the wall clock and the
sleeper are one :class:`conftest.ManualClock` set on an epoch instant, so
nothing sleeps and nothing leaves the process.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
import yaml

from conftest import (
    TOKEN_LOST,
    TOKEN_REFUSED,
    TOKEN_SERVER_ERROR,
    FakeResponse,
    ManualClock,
    ScriptedLiveChatAPI,
    ScriptedTokenEndpoint,
    events_of,
    runtime_context,
    settle,
    trace_texts,
)
from core.loader import ModuleLoader
from core.runtime import RUNTIME_API, RuntimeContext
from core.triggers import TriggerRegistry
from modules.youtube import (
    LOOKUP_RETRY_SECONDS,
    REASON_AUTH_REFRESH_FAILED,
    REASON_QUOTA_EXHAUSTED,
    REFRESH_MARGIN_SECONDS,
    TRANSIENT_RETRY_SECONDS,
    QuotaLedger,
    YouTubeModule,
    next_quota_reset,
    pacific_utc_offset,
    poll_interval,
    validate_settings,
)

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "modules" / "youtube" / "module.yaml"
CHANNEL = "UC-channel-1"
LIVE_CHAT = "live-chat-1"
COMPANION = "Robo"


def utc(text: str) -> float:
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc).timestamp()


#: 2026-09-23 13:00 PDT; the next quota reset is 2026-09-24 00:00 PDT.
NOW = utc("2026-09-23T20:00:00")
NEXT_RESET = utc("2026-09-24T07:00:00")

BASE_SETTINGS: dict[str, Any] = {
    "client_id": "youtube-client-id",
    "client_secret": "youtube-client-secret-value",
    "refresh_token": "youtube-refresh-token-value",
    "channels": [CHANNEL],
    "companion_name": COMPANION,
}


@dataclass
class Harness:
    runtime: RuntimeContext
    module: YouTubeModule
    clock: ManualClock
    tokens: ScriptedTokenEndpoint
    api: ScriptedLiveChatAPI
    diagnostics: list[str]

    def degraded(self, reason: str | None = None) -> list[dict[str, Any]]:
        return [
            event["payload"]
            for event in events_of(self.runtime.bus, "module.degraded")
            if reason is None or event["payload"].get("reason") == reason
        ]

    async def drive(self, seconds: float) -> None:
        """Move the injected clock *seconds* forward, waking every sleeper at
        its own deadline in order, so each request happens at its instant."""

        end = self.clock.now + seconds
        while True:
            await settle(100)
            deadline = self.clock.next_deadline()
            if deadline is None or deadline > end:
                self.clock.advance(end - self.clock.now)
                await settle(100)
                return
            self.clock.advance(deadline - self.clock.now)

    async def close(self) -> None:
        await self.module.stop_inputs()
        await self.module.close()


async def start_harness(
    *token_script: Any,
    polling_interval_ms: int = 5000,
    broadcasts: dict[str, str] | None = None,
    pages: tuple[Any, ...] = (),
    lookups: tuple[Any, ...] = (),
    start: float = NOW,
    **overrides: Any,
) -> Harness:
    """Activate ``youtube`` through the real loader, prepare it and start its
    pollers on the injected clock and sleeper."""

    clock = ManualClock(start)
    runtime = runtime_context(clock=clock, trigger_registry=TriggerRegistry())
    tokens = ScriptedTokenEndpoint(clock, *token_script)
    api = ScriptedLiveChatAPI(
        clock,
        token_endpoint=tokens,
        broadcasts={CHANNEL: LIVE_CHAT} if broadcasts is None else broadcasts,
        polling_interval_ms=polling_interval_ms,
        pages=pages,
        lookups=lookups,
    )
    diagnostics: list[str] = []
    settings = {
        **BASE_SETTINGS,
        **overrides,
        "_session_factory": lambda: api,
        "_wall_clock": clock,
        "_sleeper": clock.sleep,
        "diagnostic_reporter": diagnostics.append,
    }
    loader = ModuleLoader(runtime.bus, ROOT / "modules", context=runtime, environ={})
    (activation,) = await loader.activate_enabled(
        {"enabled_modules": ["youtube"], "modules": {"youtube": settings}}
    )
    module = activation.handle
    await module.prepare()
    assert tokens.refreshes == 0 and api.requests == [], "prepare must not reach the network"
    await module.start_inputs()
    return Harness(runtime, module, clock, tokens, api, diagnostics)


# -- the manifest and the validator --------------------------------------------- #


def test_manifest_declares_a_v2_input_with_the_chat_trigger_types() -> None:
    """R7: role ``input``, the four trigger types and the companion-mention
    default, the two credentials beside a non-secret ``client_id``, the
    polling and quota settings, and no action (``chat.write`` is P21's)."""

    manifest = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert manifest["name"] == "youtube"
    assert manifest["manifest_version"] == 2
    assert manifest["runtime_api"] == RUNTIME_API
    assert manifest["produces"] == ["channel.chat.message"]
    assert manifest["lifecycle"] == {"roles": ["input"]}
    assert manifest["settings_validator"] == "validate_settings"
    assert manifest["credentials"] == ["client_secret", "refresh_token"]
    assert "actions" not in manifest
    schema = manifest["settings_schema"]
    properties = schema["properties"]
    assert {
        "client_id", "client_secret", "refresh_token", "channels", "companion_name",
        "min_poll_interval_seconds", "quota", "notices",
    } == set(properties)
    interval = properties["min_poll_interval_seconds"]
    assert (interval["minimum"], interval["maximum"]) == (1, 60)
    quota = properties["quota"]["properties"]
    assert set(quota) == {"daily_units", "write_reserve_units", "costs"}
    assert set(quota["costs"]["properties"]) == {
        "list", "insert", "delete", "ban", "broadcast_lookup",
    }
    assert set(schema["required"]) == {
        "client_id", "client_secret", "refresh_token", "channels", "companion_name",
    }
    assert [entry["name"] for entry in manifest["triggers"]["types"]] == [
        "probability", "audience", "keyword", "event_kind",
    ]
    assert manifest["triggers"]["default_policy"] == {
        "combination": "all_of",
        "rules": [{"type": "keyword", "parameters": {"keywords": ["${companion_name}"]}}],
    }
    kick = yaml.safe_load((ROOT / "modules" / "kick" / "module.yaml").read_text("utf-8"))
    assert manifest["triggers"] == kick["triggers"]


def test_validator_accepts_the_base_settings_and_names_each_bad_field() -> None:
    assert validate_settings(BASE_SETTINGS) == []
    assert validate_settings(
        {
            **BASE_SETTINGS,
            "min_poll_interval_seconds": 60,
            "quota": {
                "daily_units": 100,
                "write_reserve_units": 50,
                "costs": {"list": 5, "insert": 50, "broadcast_lookup": 0},
            },
            "notices": {"kinds": ["sub", "tip"]},
        }
    ) == []
    cases = {
        "client_id": {"client_id": ""},
        "client_secret": {"client_secret": 7},
        "refresh_token": {"refresh_token": " "},
        "channels": {"channels": []},
        "companion_name": {"companion_name": None},
        "min_poll_interval_seconds": {"min_poll_interval_seconds": 0},
        "quota.daily_units": {"quota": {"daily_units": 0}},
        "quota.write_reserve_units": {"quota": {"daily_units": 100, "write_reserve_units": 100}},
        "quota.costs": {"quota": {"costs": {"search": 100}}},
        "quota.costs.list": {"quota": {"costs": {"list": -1}}},
        "quota": {"quota": {"daily": 5}},
        "notices.kinds": {"notices": {"kinds": ["raid"]}},
    }
    for field_name, override in cases.items():
        diagnostics = validate_settings({**BASE_SETTINGS, **override})
        assert len(diagnostics) == 1, (field_name, diagnostics)
        assert f"field {field_name!r}" in diagnostics[0] and "'youtube'" in diagnostics[0]
        assert BASE_SETTINGS["client_secret"] not in diagnostics[0]
        assert BASE_SETTINGS["refresh_token"] not in diagnostics[0]
    assert len(validate_settings({**BASE_SETTINGS, "min_poll_interval_seconds": 61})) == 1
    assert len(validate_settings({**BASE_SETTINGS, "min_poll_interval_seconds": True})) == 1
    # The default reserve (1000) is checked against an explicit budget too.
    assert validate_settings({**BASE_SETTINGS, "quota": {"daily_units": 100}}) == [
        "module 'youtube': field 'quota.write_reserve_units': must be below quota.daily_units"
    ]


# -- AC36: the token manager ---------------------------------------------------- #


@pytest.mark.asyncio
async def test_ac36_a_3600_second_token_is_refreshed_once_before_expiry_and_not_earlier() -> None:
    """AC36: the first request obtains a token; the next exchange happens at
    the first request at or after ``expires_at − 60 s`` — before expiry, and
    not one poll earlier — and every request carries a token valid at its
    instant."""

    h = await start_harness(3600, 3600)
    try:
        await h.drive(0)
        assert [request["at"] for request in h.tokens.requests] == [NOW]
        assert h.tokens.requests[0] == {
            "at": NOW,
            "grant_type": "refresh_token",
            "refresh_token": BASE_SETTINGS["refresh_token"],
            "client_id": BASE_SETTINGS["client_id"],
            "has_client_secret": True,
        }
        await h.drive(3600 - REFRESH_MARGIN_SECONDS - 5)
        assert h.tokens.refreshes == 1, "no refresh before one is needed"
        assert h.clock.now == NOW + 3535
        await h.drive(5)
        assert [request["at"] for request in h.tokens.requests] == [NOW, NOW + 3540]
        assert h.tokens.requests[1]["at"] < h.tokens.issued[0]["expires_at"]
        await h.drive(600)
        assert h.tokens.refreshes == 2
        assert h.api.counts["list"] == 1 + (3540 + 600) // 5
        assert all(h.tokens.valid_at(request["token"], request["at"]) for request in h.api.requests)
        assert [request["token"] for request in h.api.requests][-1] == "access-2"
    finally:
        await h.close()


@pytest.mark.asyncio
async def test_ac36_a_refused_refresh_degrades_once_and_stops_polling() -> None:
    """AC36: a refused exchange gives exactly 1 ``module.degraded`` naming
    ``auth_refresh_failed``; no API request leaves, and no further exchange
    is attempted however long the clock runs."""

    h = await start_harness(TOKEN_REFUSED)
    try:
        await h.drive(600)
        [degraded] = h.degraded()
        assert degraded["reason"] == REASON_AUTH_REFRESH_FAILED
        assert degraded["module"] == "youtube"
        assert h.tokens.refreshes == 1
        assert sum(h.api.counts.values()) == 0
        assert h.module.tokens.refused
        assert h.clock.next_deadline() is None, "the poller must have stopped"
        texts = "\n".join(trace_texts(h.runtime.bus) + h.diagnostics)
        assert BASE_SETTINGS["refresh_token"] not in texts
        assert BASE_SETTINGS["client_secret"] not in texts
    finally:
        await h.close()


@pytest.mark.asyncio
async def test_a_refusal_at_renewal_stops_the_lists_that_were_running() -> None:
    h = await start_harness(3600, TOKEN_REFUSED)
    try:
        await h.drive(3600)
        lists = h.api.counts["list"]
        assert lists == 3540 // 5
        assert [payload["reason"] for payload in h.degraded()] == [REASON_AUTH_REFRESH_FAILED]
        await h.drive(3600)
        assert h.api.counts["list"] == lists and h.tokens.refreshes == 2
    finally:
        await h.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", [TOKEN_LOST, TOKEN_SERVER_ERROR])
async def test_a_lost_or_5xx_exchange_is_retried_and_does_not_degrade(answer: str) -> None:
    h = await start_harness(answer, 3600)
    try:
        await h.drive(TRANSIENT_RETRY_SECONDS - 1)
        assert h.tokens.refreshes == 1 and sum(h.api.counts.values()) == 0
        await h.drive(1)
        assert [request["at"] for request in h.tokens.requests] == [
            NOW, NOW + TRANSIENT_RETRY_SECONDS,
        ]
        assert h.api.counts["broadcast_lookup"] == 1 and h.api.counts["list"] == 1
        assert h.degraded() == []
    finally:
        await h.close()


@pytest.mark.asyncio
async def test_a_401_drops_the_token_and_the_next_request_refreshes_it() -> None:
    h = await start_harness(
        3600, 3600, pages=(FakeResponse(401, {"error": {"code": 401}}),)
    )
    try:
        await h.drive(5)
        assert [request["at"] for request in h.tokens.requests] == [NOW, NOW + 5]
        assert [request["token"] for request in h.api.requests] == [
            "access-1", "access-1", "access-2",
        ]
        assert h.degraded() == []
    finally:
        await h.close()


# -- AC36: the polling cadence -------------------------------------------------- #


@pytest.mark.asyncio
async def test_ac36_a_2000_ms_server_interval_is_held_to_the_5_second_minimum() -> None:
    """AC36: max(2000 ms, 5 s) = 5 s: at most 12 lists in any 60 s of
    injected clock — here exactly 12, 5 s apart."""

    h = await start_harness(polling_interval_ms=2000, min_poll_interval_seconds=5)
    try:
        await h.drive(120)
        times = h.api.times("list")
        assert times[0] == NOW
        assert {later - earlier for earlier, later in zip(times, times[1:])} == {5.0}
        for start in times:
            assert len([at for at in times if start <= at < start + 60]) <= 12
        assert len([at for at in times if NOW <= at < NOW + 60]) == 12
    finally:
        await h.close()


@pytest.mark.asyncio
async def test_ac36_an_8000_ms_server_interval_spaces_lists_8_seconds_apart() -> None:
    h = await start_harness(polling_interval_ms=8000, min_poll_interval_seconds=5)
    try:
        await h.drive(80)
        times = h.api.times("list")
        assert len(times) == 11
        assert {later - earlier for earlier, later in zip(times, times[1:])} == {8.0}
    finally:
        await h.close()


def test_the_interval_is_the_larger_of_server_and_minimum() -> None:
    assert poll_interval(2000, 5.0) == 5.0
    assert poll_interval(8000, 5.0) == 8.0
    assert poll_interval(5000, 5.0) == 5.0
    for unusable in (None, "8000", True, -1, float("nan"), float("inf")):
        assert poll_interval(unusable, 5.0) == 5.0


@pytest.mark.asyncio
async def test_the_broadcast_is_looked_up_first_and_pages_are_followed() -> None:
    """R7: one lookup resolves the channel's ``liveChatId``; each list names
    it and carries the previous page's ``nextPageToken``."""

    h = await start_harness(
        broadcasts={"UC-other": "other-chat", CHANNEL: LIVE_CHAT},
        pages=({"items": [{"id": "m1"}, {"id": "m2"}], "nextPageToken": "next-A",
                "pollingIntervalMillis": 5000},),
    )
    try:
        await h.drive(10)
        assert h.api.counts["broadcast_lookup"] == 1
        assert h.api.requests[0]["params"]["broadcastStatus"] == "active"
        assert h.module.live_chat_id(CHANNEL) == LIVE_CHAT
        lists = [request["params"] for request in h.api.requests if request["endpoint"] == "list"]
        assert [params["liveChatId"] for params in lists] == [LIVE_CHAT] * 3
        assert "pageToken" not in lists[0]
        assert lists[1]["pageToken"] == "next-A" and lists[2]["pageToken"] == "page-1"
        assert h.module.counts["items_listed"] == 2
    finally:
        await h.close()


@pytest.mark.asyncio
async def test_no_active_broadcast_is_looked_up_again_after_the_retry_delay() -> None:
    h = await start_harness(broadcasts={})
    try:
        await h.drive(LOOKUP_RETRY_SECONDS - 1)
        assert h.api.counts == {**h.api.counts, "broadcast_lookup": 1, "list": 0}
        h.api.broadcasts[CHANNEL] = LIVE_CHAT
        await h.drive(1)
        assert h.api.times("broadcast_lookup") == [NOW, NOW + LOOKUP_RETRY_SECONDS]
        assert h.api.counts["list"] == 1
    finally:
        await h.close()


@pytest.mark.asyncio
async def test_an_ended_chat_is_looked_up_again() -> None:
    h = await start_harness(
        pages=(FakeResponse(403, {"error": {"errors": [{"reason": "liveChatEnded"}]}}),)
    )
    try:
        await h.drive(5)
        assert [request["endpoint"] for request in h.api.requests] == [
            "broadcast_lookup", "list", "broadcast_lookup", "list",
        ]
        assert h.degraded() == []
    finally:
        await h.close()


@pytest.mark.asyncio
async def test_stop_inputs_stops_every_request_and_close_releases_the_session_once() -> None:
    h = await start_harness()
    await h.drive(20)
    await h.module.stop_inputs()
    issued = len(h.api.requests)
    await h.drive(600)
    assert len(h.api.requests) == issued
    await h.module.close()
    await h.module.close()
    assert h.api.close_calls == 1


# -- AC37: the quota ledger ----------------------------------------------------- #


AC37_QUOTA = {
    "daily_units": 100,
    "write_reserve_units": 50,
    # The broadcast lookup is charged 0 here (plan P20 risk), so every unit
    # the reads spend is a list's.
    "costs": {"list": 5, "insert": 50, "broadcast_lookup": 0},
}


@pytest.mark.asyncio
async def test_ac37_exactly_10_lists_then_quota_exhausted_then_reads_resume_after_la_midnight() -> None:
    """AC37: 100 units, lists at 5, reserve 50 → 10 lists spend 50 and leave
    exactly the reserve; the 11th would dip into it and is not issued. One
    ``quota_exhausted`` is reported for the day and 0 reads follow, however
    long the day runs; at 00:00 America/Los_Angeles the ledger resets and
    reads resume — and a new day's exhaustion is reported again."""

    h = await start_harness(quota=AC37_QUOTA)
    try:
        await h.drive(3600)
        assert h.api.counts["list"] == 10
        assert h.api.counts["broadcast_lookup"] == 1
        [degraded] = h.degraded()
        assert degraded["reason"] == REASON_QUOTA_EXHAUSTED
        assert h.module.ledger.remaining == 50
        # The reserve is intact for a send (the send path itself is P21's).
        assert h.module.ledger.can_send("insert")

        await h.drive(NEXT_RESET - 1 - h.clock.now)
        assert h.api.counts["list"] == 10, "no read while the day is exhausted"
        assert len(h.degraded()) == 1, "quota_exhausted is reported once per day"

        await h.drive(1)
        assert h.clock.now == NEXT_RESET
        assert h.api.times("list")[10] == NEXT_RESET
        assert events_of(h.runtime.bus, "module.ready")
        await h.drive(3600)
        assert h.api.counts["list"] == 20
        assert [payload["reason"] for payload in h.degraded()] == [REASON_QUOTA_EXHAUSTED] * 2
    finally:
        await h.close()


def test_ac37_the_read_boundary_is_remaining_minus_cost_at_least_reserve() -> None:
    clock = ManualClock(NOW)
    ledger = QuotaLedger(daily_units=100, write_reserve_units=50, costs=AC37_QUOTA["costs"], clock=clock)
    assert [ledger.try_read("list") for _ in range(11)] == [True] * 10 + [False]
    assert ledger.remaining == 50
    assert not ledger.can_read("list")
    # A zero-cost lookup still leaves the reserve intact, so it may leave.
    assert ledger.try_read("broadcast_lookup") and ledger.remaining == 50
    # A send needs only `remaining ≥ cost`: 50 ≥ 50, then 0 < 50.
    assert ledger.try_send("insert") and ledger.remaining == 0
    assert not ledger.try_send("insert") and ledger.remaining == 0
    assert not ledger.try_read("insert") and not ledger.try_send("list")
    clock.advance(NEXT_RESET - 1 - NOW)
    assert ledger.remaining == 0
    clock.advance(1)
    assert ledger.remaining == 100 and ledger.reset_at == utc("2026-09-25T07:00:00")


def test_a_platform_exhaustion_blocks_even_zero_cost_requests_until_the_reset() -> None:
    clock = ManualClock(NOW)
    ledger = QuotaLedger(
        daily_units=100,
        write_reserve_units=0,
        costs={"list": 5, "broadcast_lookup": 0, "insert": 0},
        clock=clock,
    )
    assert ledger.can_read("broadcast_lookup") and ledger.can_send("insert")
    ledger.exhaust()
    assert not ledger.try_read("broadcast_lookup")
    assert not ledger.try_send("insert")
    clock.advance(NEXT_RESET - NOW)
    assert ledger.try_read("broadcast_lookup") and ledger.try_send("insert")


@pytest.mark.asyncio
async def test_the_broadcast_lookup_is_charged_its_cost() -> None:
    h = await start_harness(
        quota={"daily_units": 100, "write_reserve_units": 50, "costs": {"list": 5, "broadcast_lookup": 3}}
    )
    try:
        await h.drive(0)
        assert h.api.counts == {**h.api.counts, "broadcast_lookup": 1, "list": 1}
        assert h.module.ledger.remaining == 100 - 3 - 5
    finally:
        await h.close()


@pytest.mark.asyncio
async def test_a_platform_quota_refusal_exhausts_the_day() -> None:
    h = await start_harness(
        pages=(FakeResponse(403, {"error": {"errors": [{"reason": "quotaExceeded"}]}}),)
    )
    try:
        await h.drive(3600)
        assert h.api.counts["list"] == 1
        assert [payload["reason"] for payload in h.degraded()] == [REASON_QUOTA_EXHAUSTED]
    finally:
        await h.close()


# -- the quota day: 00:00 America/Los_Angeles (plan decision 10) --------------- #


@pytest.mark.parametrize(
    ("instant", "expected"),
    [
        # Ordinary days: summer (UTC−7) and winter (UTC−8).
        ("2026-09-23T20:00:00", "2026-09-24T07:00:00"),
        ("2026-09-24T06:59:59", "2026-09-24T07:00:00"),
        ("2026-09-24T07:00:00", "2026-09-25T07:00:00"),
        ("2026-01-15T12:00:00", "2026-01-16T08:00:00"),
        ("2026-01-16T07:59:59", "2026-01-16T08:00:00"),
        ("2025-12-31T23:00:00", "2026-01-01T08:00:00"),
        # Daylight time starts Sunday 2026-03-08 at 02:00 PST (10:00 UTC):
        # that local day lasts 23 hours.
        ("2026-03-08T08:00:00", "2026-03-09T07:00:00"),
        ("2026-03-08T09:59:59", "2026-03-09T07:00:00"),
        ("2026-03-08T10:00:00", "2026-03-09T07:00:00"),
        ("2026-03-07T20:00:00", "2026-03-08T08:00:00"),
        # Daylight time ends Sunday 2026-11-01 at 02:00 PDT (09:00 UTC): that
        # local day lasts 25 hours, and both 01:30s map to the same midnight.
        ("2026-11-01T07:00:00", "2026-11-02T08:00:00"),
        ("2026-11-01T08:30:00", "2026-11-02T08:00:00"),
        ("2026-11-01T09:30:00", "2026-11-02T08:00:00"),
        ("2026-10-31T20:00:00", "2026-11-01T07:00:00"),
    ],
)
def test_the_quota_day_resets_at_los_angeles_midnight(instant: str, expected: str) -> None:
    assert next_quota_reset(utc(instant)) == utc(expected)


def test_the_transition_days_last_23_and_25_hours() -> None:
    march_start = utc("2026-03-08T08:00:00")
    assert next_quota_reset(march_start) - march_start == 23 * 3600
    november_start = utc("2026-11-01T07:00:00")
    assert next_quota_reset(november_start) - november_start == 25 * 3600
    ordinary = utc("2026-09-23T07:00:00")
    assert next_quota_reset(ordinary) - ordinary == 24 * 3600
    assert pacific_utc_offset(utc("2026-03-08T09:59:59")) == -8 * 3600
    assert pacific_utc_offset(utc("2026-03-08T10:00:00")) == -7 * 3600
    assert pacific_utc_offset(utc("2026-11-01T08:59:59")) == -7 * 3600
    assert pacific_utc_offset(utc("2026-11-01T09:00:00")) == -8 * 3600


def test_the_quota_day_agrees_with_the_tz_database_when_one_is_installed() -> None:
    """A cross-check over several years, hour by hour around every
    transition; the integer rule is the product, the database only a
    witness, so a host without one skips this check alone."""

    try:
        from zoneinfo import ZoneInfo

        zone = ZoneInfo("America/Los_Angeles")
    except Exception:  # noqa: BLE001 - no tz database on this host
        pytest.skip("no tz database installed")
    for year in range(2024, 2031):
        for day in range(0, 366, 1):
            base = datetime(year, 1, 1, tzinfo=timezone.utc) + timedelta(days=day)
            for hour in (0, 6, 7, 8, 9, 10, 11, 23):
                moment = base + timedelta(hours=hour)
                local = moment.astimezone(zone)
                midnight = datetime(local.year, local.month, local.day, tzinfo=zone) + timedelta(days=1)
                midnight = datetime(midnight.year, midnight.month, midnight.day, tzinfo=zone)
                assert next_quota_reset(moment.timestamp()) == midnight.timestamp(), moment
