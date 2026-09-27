"""The YouTube live-chat input (phase 3 R7, plan steps P20 and P21).

AC36: with a scripted token endpoint issuing a 3600 s token, a refresh
happens before expiry and none before it is needed; a refused refresh yields
1 ``module.degraded`` naming ``auth_refresh_failed``; polling follows
max(server interval 2000 ms, ``min_poll_interval_seconds: 5``) = 5 s (at most
12 list requests per 60 s of injected clock) and max(8000 ms, 5 s) = 8 s when
the server asks for longer.

AC37: with ``quota.daily_units: 100``, a list cost of 5, a send cost of 50
and ``write_reserve_units: 50``, the module issues exactly 10 list requests,
then reports ``quota_exhausted`` and issues 0 more reads while a send still
succeeds (1 insert); a second send is ``refused quota_exhausted`` with 0
requests (the send half, plan step P21); after 00:00 America/Los_Angeles on
the injected clock, reads resume. The LA-midnight function is tested at both
daylight-saving transitions and on ordinary days (plan decision 10).

AC38 (P21): ``chat.write`` of 200 characters sends 1 request, 201 is ``error
text_too_long`` with 0; owner, moderator and member author details satisfy
``broadcaster``, ``moderators`` and ``subscribers``; member, milestone, gift
and paid-message items map to ``sub``, ``resub``, ``sub_gift``, ``tip``; the
moderation service performs ``delete_message`` and ``timeout`` with 1 request
each, and never builds a ban without a duration.

AC32, the youtube half (P21): an author id containing ``:`` yields 0
admissions and ``invalid`` + 1.

AC39 (P21): with twitch, kick and youtube enabled on one runtime,
``chat.write`` has exactly 3 bindings on disjoint destinations; the same
viewer id on two platforms gets two sessions; ``stream.clip.create`` and
``stream.poll.create`` are unbound for kick and youtube; a case-insensitive
word scan of ``core/`` finds 0 ``kick``/``youtube``.

The session is a :class:`conftest.ScriptedLiveChatAPI` routing the token
exchange to a :class:`conftest.ScriptedTokenEndpoint`; the wall clock and the
sleeper are one :class:`conftest.ManualClock` set on an epoch instant, so
nothing sleeps and nothing leaves the process.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
import yaml

from aiohttp.test_utils import TestClient, TestServer

from conftest import (
    KICK_TEST_KEY,
    TOKEN_LOST,
    TOKEN_REFUSED,
    TOKEN_SERVER_ERROR,
    FakeResponse,
    ManualClock,
    RecordingScheduler,
    ScriptedLiveChatAPI,
    ScriptedTokenEndpoint,
    SignedWebhookSender,
    events_of,
    runtime_context,
    settle,
    trace_texts,
)
from core.actions import AuthorizationRule
from core.admission import AdmissionScheduler
from core.context import ChatContext
from core.contracts import (
    ActionCall,
    ActionObservation,
    Destination,
    SessionKey,
    TriggerPolicy,
    TriggerRule,
)
from core.loader import ModuleLoader
from core.runtime import RUNTIME_API, RuntimeContext
from core.triggers import TriggerRegistry
from modules.brain import _message_of_work
from modules.youtube import (
    BANS_URL,
    CHAT_WRITE_ACTION,
    DEFAULT_RATE_LIMIT_SECONDS,
    EVENT_KINDS,
    LOOKUP_RETRY_SECONDS,
    MESSAGES_URL,
    REASON_AUTH_REFRESH_FAILED,
    REASON_QUOTA_EXHAUSTED,
    REFRESH_MARGIN_SECONDS,
    TRANSIENT_RETRY_SECONDS,
    QuotaLedger,
    YouTubeModule,
    activate,
    next_quota_reset,
    normalize_item,
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
    registry: TriggerRegistry
    scheduler: RecordingScheduler
    chat: ChatContext

    def events(self) -> list[dict[str, Any]]:
        return events_of(self.runtime.bus, "channel.chat.message")

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
    policy: TriggerPolicy | None = None,
    inserts: tuple[Any, ...] = (),
    deletes: tuple[Any, ...] = (),
    bans: tuple[Any, ...] = (),
    **overrides: Any,
) -> Harness:
    """Activate ``youtube`` through the real loader, prepare it and start its
    pollers on the injected clock and sleeper; ingestion admits to a
    recording scheduler and feeds a real chat context."""

    clock = ManualClock(start)
    registry = TriggerRegistry()
    scheduler = RecordingScheduler()
    chat = ChatContext(max_messages=16, max_bytes=8192, max_age_seconds=600.0, clock=clock)
    runtime = runtime_context(
        clock=clock, trigger_registry=registry, scheduler=scheduler, chat=chat
    )
    tokens = ScriptedTokenEndpoint(clock, *token_script)
    api = ScriptedLiveChatAPI(
        clock,
        token_endpoint=tokens,
        broadcasts={CHANNEL: LIVE_CHAT} if broadcasts is None else broadcasts,
        polling_interval_ms=polling_interval_ms,
        pages=pages,
        lookups=lookups,
        inserts=inserts,
        deletes=deletes,
        bans=bans,
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
    if policy is not None:
        registry.configure("youtube", policy)
    await module.prepare()
    assert tokens.refreshes == 0 and api.requests == [], "prepare must not reach the network"
    await module.start_inputs()
    return Harness(runtime, module, clock, tokens, api, diagnostics, registry, scheduler, chat)


# -- the manifest and the validator --------------------------------------------- #


def test_manifest_declares_a_v2_input_with_the_chat_trigger_types() -> None:
    """R7: role ``input``, the four trigger types and the companion-mention
    default, the two credentials beside a non-secret ``client_id``, the
    polling and quota settings, and — superseding P20's "no action" by R7's
    ``chat.write`` for ``youtube/*/chat`` (plan step P21) — exactly the
    ``chat.write`` contract kick declares, over this platform's own
    destinations."""

    manifest = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert manifest["name"] == "youtube"
    assert manifest["manifest_version"] == 2
    assert manifest["runtime_api"] == RUNTIME_API
    assert manifest["produces"] == ["channel.chat.message"]
    assert manifest["lifecycle"] == {"roles": ["input"]}
    assert manifest["settings_validator"] == "validate_settings"
    assert manifest["credentials"] == ["client_secret", "refresh_token"]
    [action] = manifest["actions"]
    assert action["supported_destinations"] == [
        {"platform": "youtube", "channel_id": "*", "scope": "chat"}
    ]
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
    [kick_action] = kick["actions"]
    assert {**action, "supported_destinations": None} == {
        **kick_action, "supported_destinations": None,
    }


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


# -- P21: shared helpers ---------------------------------------------------------- #


VIEWER = "UC-viewer-7"
AUDIENCES = ("broadcaster", "everyone", "moderators", "subscribers", "vips")


def chat_item(
    message_id: str,
    text: str = "hello",
    *,
    author: str = VIEWER,
    name: str = "viewer",
    item_type: str = "textMessageEvent",
    details: dict[str, Any] | None = None,
    **flags: bool,
) -> dict[str, Any]:
    """One listed live-chat item in the documented shape."""

    snippet: dict[str, Any] = {
        "type": item_type,
        "liveChatId": LIVE_CHAT,
        "authorChannelId": author,
        "publishedAt": "2026-09-23T20:00:00Z",
        "hasDisplayContent": True,
        "displayMessage": text,
    }
    if item_type == "textMessageEvent":
        snippet["textMessageDetails"] = {"messageText": text}
    snippet.update(details or {})
    return {
        "kind": "youtube#liveChatMessage",
        "id": message_id,
        "snippet": snippet,
        "authorDetails": {
            "channelId": author,
            "displayName": name,
            "isVerified": False,
            "isChatOwner": False,
            "isChatSponsor": False,
            "isChatModerator": False,
            **flags,
        },
    }


def page(*items: dict[str, Any]) -> dict[str, Any]:
    return {"items": list(items), "nextPageToken": "next", "pollingIntervalMillis": 5000}


def audience_policy(audience: str) -> TriggerPolicy:
    return TriggerPolicy(
        rules=(TriggerRule(type="audience", parameters={"audience": audience}),),
        combination="all_of",
    )


def event_kind_policy(*kinds: str) -> TriggerPolicy:
    return TriggerPolicy(
        rules=(TriggerRule(type="event_kind", parameters={"kinds": list(kinds)}),),
        combination="all_of",
    )


class AnswerResponse(FakeResponse):
    """A scripted API answer with headers."""

    def __init__(self, status: int, body: Any = None, headers: dict[str, str] | None = None) -> None:
        super().__init__(status, body if body is not None else {})
        self.headers = headers or {}


def api_error(status: int, reason: str) -> AnswerResponse:
    return AnswerResponse(
        status, {"error": {"code": status, "errors": [{"reason": reason, "domain": "youtube"}]}}
    )


def grant_chat_write(runtime: RuntimeContext) -> None:
    runtime.actions._authorization.grant(
        AuthorizationRule(
            rule_id="grant-chat-write",
            action_name=CHAT_WRITE_ACTION,
            granted_permissions=("chat.write",),
        )
    )


_CALLS = iter(range(1, 1_000_000))


async def write(h: Harness, text: str, *, channel: str = CHANNEL, **arguments: Any) -> ActionObservation:
    number = next(_CALLS)
    call = ActionCall(
        action_name=CHAT_WRITE_ACTION,
        action_version=1,
        arguments={"text": text, **arguments},
        conversation_id="conversation-1",
        run_id=f"run-{number}",
        call_id=f"call-{number}",
        source_event_id="source-1",
        destination=Destination("youtube", channel, "chat"),
        principal="brain",
        deadline=h.clock.now + 100.0,
    )
    return await h.runtime.executor.invoke(call)


async def live(**options: Any) -> Harness:
    """A started harness whose live chat is resolved, granted ``chat.write``."""

    h = await start_harness(**options)
    await h.drive(0)
    assert h.module.live_chat_id(CHANNEL) == LIVE_CHAT
    grant_chat_write(h.runtime)
    return h


def completed_traces(h: Harness) -> list[dict[str, Any]]:
    return [event["payload"] for event in events_of(h.runtime.bus, "action.completed")]


# -- AC38: chat.write ------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_chat_write_is_bound_on_youtube_chat_and_ready_after_prepare() -> None:
    """R7: ``prepare`` binds the declared ``chat.write`` over
    ``youtube/*/chat`` (one binding), with no request, and ``close`` marks it
    not ready."""

    h = await start_harness()
    try:
        [binding] = h.runtime.actions.bindings(CHAT_WRITE_ACTION)
        assert binding.destination == Destination("youtube", "*", "chat")
        assert binding.module == "youtube"
        assert CHAT_WRITE_ACTION in h.runtime.actions.registered_ready()
    finally:
        await h.close()
    assert CHAT_WRITE_ACTION not in h.runtime.actions.registered_ready()


@pytest.mark.asyncio
async def test_ac38_200_characters_send_one_insert_and_201_send_none() -> None:
    """AC38: a 200-character text is 1 insert into the resolved live chat,
    confirmed by the platform's message id; 201 characters is ``error
    text_too_long`` with 0 requests. The credentials reach no trace."""

    h = await live(inserts=(FakeResponse(200, {"id": "yt-msg-1"}),))
    try:
        observation = await write(h, "a" * 200, parent_message_id="parent-9")
        assert observation.status == "success", observation
        assert observation.result == {
            "message_id": "yt-msg-1",
            "destination": {"platform": "youtube", "channel_id": CHANNEL},
        }
        [insert] = [request for request in h.api.requests if request["endpoint"] == "insert"]
        assert insert["params"] == {"part": "snippet"}
        assert insert["json"] == {
            "snippet": {
                "liveChatId": LIVE_CHAT,
                "type": "textMessageEvent",
                "textMessageDetails": {"messageText": "a" * 200},
            }
        }
        assert insert["token"] == "access-1"

        refused = await write(h, "a" * 201)
        assert refused.status == "error" and refused.error["code"] == "text_too_long"
        assert h.api.counts["insert"] == 1
        texts = "\n".join(trace_texts(h.runtime.bus) + h.diagnostics)
        assert BASE_SETTINGS["refresh_token"] not in texts
        assert BASE_SETTINGS["client_secret"] not in texts
        assert "access-1" not in texts
    finally:
        await h.close()


@pytest.mark.asyncio
async def test_ac37_after_read_exhaustion_one_send_succeeds_and_a_second_is_refused() -> None:
    """AC37, the send half: once the reads have spent down to the reserve
    (10 lists, ``quota_exhausted``), a send still leaves (``remaining`` 50 ≥
    cost 50: 1 insert); a second is ``refused quota_exhausted`` with 0
    requests. No read follows either send."""

    h = await start_harness(quota=AC37_QUOTA)
    try:
        await h.drive(3600)
        assert h.api.counts["list"] == 10
        assert [payload["reason"] for payload in h.degraded()] == [REASON_QUOTA_EXHAUSTED]
        grant_chat_write(h.runtime)

        sent = await write(h, "still here")
        assert sent.status == "success", sent
        assert h.api.counts["insert"] == 1
        assert h.module.ledger.remaining == 0

        refused = await write(h, "and again")
        assert refused.status == "refused", refused
        assert refused.error["code"] == "quota_exhausted"
        assert h.api.counts["insert"] == 1
        await h.drive(3600)
        assert h.api.counts["list"] == 10
    finally:
        await h.close()


@pytest.mark.asyncio
async def test_a_429_blocks_sends_until_the_announced_retry_instant() -> None:
    """R7: a 429 with ``Retry-After: 30`` is ``error rate_limited``; a send
    29 s later is ``refused rate_limited`` with 0 requests; at 30 s the next
    send leaves. The wait and its source are traced."""

    h = await live(
        inserts=(AnswerResponse(429, {}, {"Retry-After": "30"}), FakeResponse(200, {"id": "m-2"}))
    )
    try:
        start = h.clock.now
        limited = await write(h, "hello")
        assert limited.status == "error" and limited.error["code"] == "rate_limited"
        assert h.module.sends_blocked_until == start + 30.0
        assert completed_traces(h)[-1]["retry_after_seconds"] == 30.0
        assert completed_traces(h)[-1]["retry_source"] == "retry_after"
        h.clock.advance(29.0)
        blocked = await write(h, "hello again")
        assert blocked.status == "refused" and blocked.error["code"] == "rate_limited"
        assert h.api.counts["insert"] == 1
        h.clock.advance(1.0)
        assert (await write(h, "hello at last")).status == "success"
        assert h.api.counts["insert"] == 2
    finally:
        await h.close()


@pytest.mark.asyncio
async def test_a_403_rate_limit_exceeded_is_rate_limited_for_the_default_block() -> None:
    """Risk (P21): a 403 whose first reason is ``rateLimitExceeded`` is a rate
    refusal, not a rejection: ``error rate_limited``, sends blocked for
    :data:`DEFAULT_RATE_LIMIT_SECONDS` when no retry is announced."""

    h = await live(inserts=(api_error(403, "rateLimitExceeded"), FakeResponse(200, {"id": "m"})))
    try:
        limited = await write(h, "hello")
        assert limited.status == "error" and limited.error["code"] == "rate_limited"
        assert completed_traces(h)[-1]["retry_source"] == "default"
        h.clock.advance(DEFAULT_RATE_LIMIT_SECONDS - 1)
        assert (await write(h, "blocked")).status == "refused"
        assert h.api.counts["insert"] == 1
        h.clock.advance(1)
        assert (await write(h, "free")).status == "success"
    finally:
        await h.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["forbidden", "insufficientPermissions", "liveChatDisabled"])
async def test_a_403_for_another_reason_is_rejected_and_sets_no_block(reason: str) -> None:
    h = await live(inserts=(api_error(403, reason),))
    try:
        observation = await write(h, "hello")
        assert observation.status == "error"
        assert observation.error["code"] == "platform_rejected"
        assert h.module.sends_blocked_until is None
        assert h.api.counts["insert"] == 1
    finally:
        await h.close()


@pytest.mark.asyncio
async def test_a_403_quota_exceeded_syncs_the_ledger_to_zero() -> None:
    """Risk (P21): a platform ``quotaExceeded`` on a send is ``error
    quota_exhausted``; the local ledger is synced to 0, so the next send is
    ``refused quota_exhausted`` with 0 requests and reads stop too."""

    h = await live(inserts=(api_error(403, "quotaExceeded"),))
    try:
        observation = await write(h, "hello")
        assert observation.status == "error" and observation.error["code"] == "quota_exhausted"
        assert h.module.ledger.remaining == 0
        refused = await write(h, "again")
        assert refused.status == "refused" and refused.error["code"] == "quota_exhausted"
        assert h.api.counts["insert"] == 1
        lists = h.api.counts["list"]
        await h.drive(60)
        assert h.api.counts["list"] == lists
        assert [payload["reason"] for payload in h.degraded()] == [REASON_QUOTA_EXHAUSTED]
    finally:
        await h.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "answer", [AnswerResponse(503), AnswerResponse(500), ConnectionResetError(), FakeResponse(200, {})]
)
async def test_an_uncertain_send_is_external_unknown_and_never_retried(answer: Any) -> None:
    h = await live(inserts=(answer,))
    try:
        observation = await write(h, "hello")
        assert observation.status == "external_unknown", observation
        assert h.api.counts["insert"] == 1
        await settle()
        assert h.api.counts["insert"] == 1
        assert h.module.sends_blocked_until is None
    finally:
        await h.close()


@pytest.mark.asyncio
async def test_an_unconfigured_channel_or_an_unresolved_chat_sends_nothing() -> None:
    h = await start_harness(broadcasts={})
    try:
        await h.drive(0)
        grant_chat_write(h.runtime)
        other = await write(h, "hello", channel="UC-elsewhere")
        assert other.error["code"] == "unsupported_destination"
        unresolved = await write(h, "hello")
        assert unresolved.status == "error" and unresolved.error["code"] == "no_live_chat"
        assert h.api.counts["insert"] == 0
    finally:
        await h.close()


# -- AC38: roles and notices -------------------------------------------------------- #


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("flag", "audience", "role"),
    [
        ("isChatOwner", "broadcaster", "broadcaster"),
        ("isChatModerator", "moderators", "moderator"),
        ("isChatSponsor", "subscribers", "subscription"),
    ],
)
async def test_ac38_each_author_flag_satisfies_its_audience_rule(
    flag: str, audience: str, role: str
) -> None:
    """AC38: owner → broadcaster, moderator → moderators, member →
    subscribers. A plain author, or one claiming the role in the text, is
    not admitted."""

    h = await start_harness(
        policy=audience_policy(audience),
        pages=(page(
            chat_item("with", **{flag: True}),
            chat_item("without"),
            chat_item("claimed", f"I am the {audience}"),
        ),),
    )
    try:
        await h.drive(0)
        assert len(h.events()) == 3
        assert [work.source_event_id for _key, work in h.scheduler.admissions] == ["with"]
        [(key, _work)] = h.scheduler.admissions
        assert key == SessionKey(platform="youtube", channel_id=CHANNEL, viewer_id=VIEWER)
        author = h.events()[0]["payload"]["author"]
        assert author["roles"] == [role]
        assert author["roles_provenance"] == "youtube.author_details"
        assert h.events()[1]["payload"]["author"]["roles"] == []
        assert h.events()[0]["metadata"] == {"source": "youtube", "schema_version": 2}
    finally:
        await h.close()


NOTICE_CASES = [
    ("newSponsorEvent", "sub", {"newSponsorDetails": {"memberLevelName": "Fan", "isUpgrade": False}}),
    (
        "memberMilestoneChatEvent",
        "resub",
        {"memberMilestoneChatDetails": {"memberMonth": 6, "userComment": "six months"}},
    ),
    (
        "membershipGiftingEvent",
        "sub_gift",
        {"membershipGiftingDetails": {"giftMembershipsCount": 5, "giftMembershipsLevelName": "Fan"}},
    ),
    (
        "superChatEvent",
        "tip",
        {"superChatDetails": {"amountMicros": "5000000", "currency": "USD", "userComment": "gg"}},
    ),
    ("superStickerEvent", "tip", {"superStickerDetails": {"amountMicros": "2000000", "currency": "USD"}}),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("item_type", "kind", "details"), NOTICE_CASES)
async def test_ac38_each_notice_type_maps_to_its_kind_and_is_admitted_by_kind(
    item_type: str, kind: str, details: dict[str, Any]
) -> None:
    """AC38: member, milestone, gift and paid-message items map to ``sub``,
    ``resub``, ``sub_gift`` and ``tip``, and an ``event_kind`` rule admits
    them."""

    h = await start_harness(
        policy=event_kind_policy(kind),
        notices={"kinds": ["sub", "resub", "sub_gift", "tip"]},
        pages=(page(chat_item(f"notice-{kind}", "", item_type=item_type, details=details)),),
    )
    try:
        await h.drive(0)
        (event,) = h.events()
        payload = event["payload"]
        assert payload["kind"] == kind and payload["platform"] == "youtube"
        assert payload["message_id"] == f"notice-{kind}"
        assert payload["author"]["id"] == VIEWER
        assert len(h.scheduler.admissions) == 1
        # The notice, empty text included, reaches the brain as its kind
        # (gate 1, F1).
        ((_key, work),) = h.scheduler.admissions
        message = _message_of_work(work)
        assert (message.kind, message.text) == (kind, payload["text"])
    finally:
        await h.close()


@pytest.mark.asyncio
async def test_an_unlisted_notice_and_an_unmapped_type_are_ignored_and_counted() -> None:
    h = await start_harness(
        policy=audience_policy("everyone"),
        notices={"kinds": ["sub"]},
        pages=(page(
            chat_item("tip-1", item_type="superChatEvent"),
            chat_item("deleted-1", item_type="messageDeletedEvent"),
            chat_item("ended-1", item_type="chatEndedEvent"),
        ),),
    )
    try:
        await h.drive(0)
        assert h.events() == [] and h.scheduler.admissions == []
        assert h.module.counts["notices_ignored"] == 3
    finally:
        await h.close()


def test_the_mapping_covers_exactly_the_six_documented_types() -> None:
    assert EVENT_KINDS == {
        "textMessageEvent": "message",
        "newSponsorEvent": "sub",
        "memberMilestoneChatEvent": "resub",
        "membershipGiftingEvent": "sub_gift",
        "superChatEvent": "tip",
        "superStickerEvent": "tip",
    }
    kinds = frozenset({"sub", "resub", "sub_gift", "tip"})
    assert normalize_item(chat_item("m", "hi"), CHANNEL, kinds).kind == "message"
    for item_type, kind, details in NOTICE_CASES:
        notice = normalize_item(chat_item("n", item_type=item_type, details=details), CHANNEL, kinds)
        assert notice is not None and notice.kind == kind
    assert normalize_item(chat_item("n", item_type="newSponsorEvent"), CHANNEL) is None
    for broken in ({}, {"id": "x", "snippet": {"type": "textMessageEvent"}}, "text"):
        with pytest.raises(ValueError):
            normalize_item(broken, CHANNEL, kinds)


# -- AC32 (youtube half), anti-echo and dedup -------------------------------------- #


@pytest.mark.asyncio
async def test_ac32_an_author_id_with_a_colon_is_refused_and_counted() -> None:
    """AC32: an author id containing ``:`` yields 0 admissions, 0 events, 0
    chat-context entries and ``invalid`` + 1; a clean author afterwards is
    admitted."""

    h = await start_harness(
        policy=audience_policy("everyone"),
        pages=(page(chat_item("colon", f"hi {COMPANION}", author="system:watch")),
               page(chat_item("clean", f"hi {COMPANION}"))),
    )
    try:
        await h.drive(0)
        assert h.scheduler.admissions == [] and h.events() == []
        assert h.chat.read("youtube", CHANNEL, limit=8) == ()
        assert h.module.counts["invalid"] == 1
        await h.drive(5)
        assert len(h.scheduler.admissions) == 1
        assert h.module.counts["invalid"] == 1
    finally:
        await h.close()


@pytest.mark.asyncio
async def test_the_companion_own_messages_are_dropped_before_anything() -> None:
    """R7 anti-echo: a message under the companion's name, and a message
    this module itself inserted, listed back, are neither fed, published
    nor admitted."""

    h = await live(
        policy=audience_policy("everyone"),
        inserts=(FakeResponse(200, {"id": "own-1"}),),
    )
    try:
        assert (await write(h, "hi chat")).status == "success"
        h.api.pages.append(page(
            chat_item("own-1", "hi chat", author="UC-bot", name="Some Channel"),
            chat_item("by-name", "hello", author="UC-bot-2", name=COMPANION.lower()),
        ))
        await h.drive(5)
        assert h.events() == [] and h.scheduler.admissions == []
        assert h.chat.read("youtube", CHANNEL, limit=8) == ()
    finally:
        await h.close()


@pytest.mark.asyncio
async def test_a_relisted_message_is_ingested_once_and_counted() -> None:
    h = await start_harness(
        policy=audience_policy("everyone"),
        pages=(page(chat_item("m-1", "one")), page(chat_item("m-1", "one"), chat_item("m-2"))),
    )
    try:
        await h.drive(5)
        assert [event["payload"]["message_id"] for event in h.events()] == ["m-1", "m-2"]
        assert len(h.scheduler.admissions) == 2
        assert h.module.counts["duplicates"] == 1
        assert [entry.message_id for entry in h.chat.read("youtube", CHANNEL, limit=8)] == [
            "m-1", "m-2",
        ]
    finally:
        await h.close()


@pytest.mark.asyncio
async def test_the_default_policy_admits_a_mention_of_the_companion() -> None:
    h = await start_harness(pages=(page(chat_item("m-1", "hello"), chat_item("m-2", f"hey {COMPANION}")),))
    try:
        await h.drive(0)
        assert len(h.events()) == 2
        assert [work.source_event_id for _key, work in h.scheduler.admissions] == ["m-2"]
    finally:
        await h.close()


# -- AC38: the moderation service ----------------------------------------------------- #


@pytest.mark.asyncio
async def test_the_moderation_service_offers_delete_and_timeout_and_no_clip_or_poll() -> None:
    """R2, R5: with a service registry, activation publishes ``(moderation,
    youtube)`` offering ``{delete_message, timeout}`` and nothing else."""

    h = await start_harness()
    try:
        kinds = {key for key in h.runtime.services.entries() if key[1] == "youtube"}
        assert kinds == {("moderation", "youtube")}
        service = h.runtime.services.resolve("moderation", "youtube")
        assert service.operations == frozenset({"delete_message", "timeout"})
        assert service is h.module.moderation_service
    finally:
        await h.close()


@pytest.mark.asyncio
async def test_a_context_without_a_registry_publishes_no_service() -> None:
    import dataclasses

    clock = ManualClock(NOW)
    runtime = dataclasses.replace(runtime_context(clock=clock), services=None)
    api = ScriptedLiveChatAPI(clock)
    settings = {**BASE_SETTINGS, "_session_factory": lambda: api, "_wall_clock": clock}
    module = await activate(runtime.for_module("youtube"), settings, {})
    try:
        assert module.moderation_service.operations == frozenset({"delete_message", "timeout"})
    finally:
        await module.close()


@pytest.mark.asyncio
async def test_ac38_delete_and_timeout_make_one_request_each() -> None:
    """AC38: ``delete_message`` is 1 ``liveChatMessages.delete`` of the
    message id; ``timeout`` is 1 ``liveChatBans.insert`` of type
    ``temporary`` carrying ``banDurationSeconds``. Each is charged its cost."""

    h = await live()
    try:
        service = h.module.moderation_service
        remaining = h.module.ledger.remaining
        deleted = await service.apply(
            "delete_message", channel_id=CHANNEL, message_id="m-9", target_author_id=VIEWER
        )
        assert deleted.outcome == "ok"
        timed_out = await service.apply(
            "timeout", channel_id=CHANNEL, target_author_id=VIEWER, duration_seconds=90,
            reason="spam",
        )
        assert timed_out.outcome == "ok"
        assert (h.api.counts["delete"], h.api.counts["ban"]) == (1, 1)
        [delete] = [request for request in h.api.requests if request["endpoint"] == "delete"]
        assert delete["params"] == {"id": "m-9"}
        [ban] = [request for request in h.api.requests if request["endpoint"] == "ban"]
        assert ban["params"] == {"part": "snippet"}
        assert ban["json"] == {
            "snippet": {
                "liveChatId": LIVE_CHAT,
                "type": "temporary",
                "banDurationSeconds": 90,
                "bannedUserDetails": {"channelId": VIEWER},
            }
        }
        assert h.module.ledger.remaining == remaining - 50 - 50
    finally:
        await h.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation", "arguments"),
    [
        ("timeout", {"duration_seconds": None}),
        ("timeout", {"duration_seconds": 0}),
        ("timeout", {"duration_seconds": -60}),
        ("timeout", {"duration_seconds": True}),
        ("timeout", {"duration_seconds": 60, "target_author_id": ""}),
        ("delete_message", {"message_id": None}),
        ("ban", {"duration_seconds": 60}),
    ],
)
async def test_the_service_refuses_locally_with_no_request(
    operation: str, arguments: dict[str, Any]
) -> None:
    """R5: a timeout without a positive whole duration — which would be a
    ban that never ends — is never built; nor one without a target, a delete
    without a message id, or any other operation. 0 requests, 0 units."""

    h = await live()
    try:
        remaining = h.module.ledger.remaining
        answer = await h.module.moderation_service.apply(
            operation, channel_id=CHANNEL, **{"target_author_id": VIEWER, **arguments}
        )
        assert answer.outcome == "rejected"
        assert (h.api.counts["delete"], h.api.counts["ban"]) == (0, 0)
        assert h.module.ledger.remaining == remaining
    finally:
        await h.close()


@pytest.mark.asyncio
async def test_a_moderation_request_the_ledger_cannot_cover_is_not_sent() -> None:
    h = await live(quota={"daily_units": 100, "write_reserve_units": 0,
                          "costs": {"list": 0, "broadcast_lookup": 0, "ban": 101}})
    try:
        answer = await h.module.moderation_service.apply(
            "timeout", channel_id=CHANNEL, target_author_id=VIEWER, duration_seconds=60
        )
        assert answer.outcome == "rejected" and h.api.counts["ban"] == 0
    finally:
        await h.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("answer", "outcome"),
    [
        (AnswerResponse(429), "rate"),
        (api_error(403, "rateLimitExceeded"), "rate"),
        (api_error(403, "forbidden"), "rejected"),
        (api_error(403, "insufficientPermissions"), "rejected"),
        (api_error(403, "quotaExceeded"), "rejected"),
        (AnswerResponse(500), "uncertain"),
        (ConnectionResetError(), "uncertain"),
    ],
)
async def test_each_ban_answer_is_classified_after_one_request(answer: Any, outcome: str) -> None:
    h = await live(bans=(answer,))
    try:
        result = await h.module.moderation_service.apply(
            "timeout", channel_id=CHANNEL, target_author_id=VIEWER, duration_seconds=60
        )
        assert result.outcome == outcome
        assert h.api.counts["ban"] == 1
        if isinstance(answer, AnswerResponse) and "quotaExceeded" in str(answer.body):
            assert h.module.ledger.remaining == 0
    finally:
        await h.close()


@pytest.mark.asyncio
async def test_a_moderation_rate_refusal_blocks_every_write_until_the_retry_instant() -> None:
    """R7: a ban's 429 with ``Retry-After: 30`` is ``rate`` and blocks writes:
    a send and a delete 29 s later leave no request (``refused rate_limited``
    and ``rate``); at 30 s the next ban leaves."""

    h = await live(
        bans=(AnswerResponse(429, {}, {"Retry-After": "30"}), FakeResponse(200, {})),
        inserts=(FakeResponse(200, {"id": "m"}),),
    )
    try:
        start = h.clock.now
        service = h.module.moderation_service
        limited = await service.apply(
            "timeout", channel_id=CHANNEL, target_author_id=VIEWER, duration_seconds=60
        )
        assert limited.outcome == "rate"
        assert h.module.sends_blocked_until == start + 30.0
        h.clock.advance(29.0)
        blocked = await write(h, "hello")
        assert blocked.status == "refused" and blocked.error["code"] == "rate_limited"
        deleted = await service.apply("delete_message", channel_id=CHANNEL, message_id="m-1")
        assert deleted.outcome == "rate"
        assert (h.api.counts["insert"], h.api.counts["delete"], h.api.counts["ban"]) == (0, 0, 1)
        h.clock.advance(1.0)
        again = await service.apply(
            "timeout", channel_id=CHANNEL, target_author_id=VIEWER, duration_seconds=60
        )
        assert again.outcome == "ok" and h.api.counts["ban"] == 2
    finally:
        await h.close()


@pytest.mark.asyncio
async def test_a_send_rate_refusal_blocks_moderation_with_no_request() -> None:
    """R7: the block a send's rate refusal sets also holds moderation back."""

    h = await live(inserts=(AnswerResponse(429, {}, {"Retry-After": "30"}),))
    try:
        assert (await write(h, "hello")).error["code"] == "rate_limited"
        remaining = h.module.ledger.remaining
        answer = await h.module.moderation_service.apply(
            "timeout", channel_id=CHANNEL, target_author_id=VIEWER, duration_seconds=60
        )
        assert answer.outcome == "rate"
        assert h.api.counts["ban"] == 0 and h.module.ledger.remaining == remaining
    finally:
        await h.close()


def test_no_ban_type_but_temporary_is_ever_built() -> None:
    """R5, the P26 gate's grep: the module source names no ban type but
    ``temporary`` and never builds a ban without ``banDurationSeconds``."""

    source = (ROOT / "modules" / "youtube" / "__init__.py").read_text(encoding="utf-8")
    assert "permanent" not in source.lower()
    assert source.count('"type": BAN_TYPE_TEMPORARY') == 1
    assert source.count('"banDurationSeconds": duration_seconds') == 1


# -- AC39: twitch, kick and youtube on one runtime ----------------------------------- #


class TwitchValidationSession:
    """Twitch's injected transport, answering only the token validation
    ``prepare`` performs; any other request is recorded."""

    def __init__(self) -> None:
        self.requests: list[str] = []

    async def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.requests.append(url)
        return FakeResponse(200, {"client_id": "twitch-client", "user_id": "bot-24"})

    async def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.requests.append(url)
        raise AssertionError(f"unexpected POST {url}")

    async def close(self) -> None:
        return None


class KickSession:
    """Kick's injected transport: its key is configured, so any request is a
    test failure, recorded."""

    def __init__(self) -> None:
        self.requests: list[str] = []

    async def get(self, url: str, **kwargs: Any) -> Any:
        self.requests.append(url)
        raise AssertionError(f"unexpected GET {url}")

    async def post(self, url: str, **kwargs: Any) -> Any:
        self.requests.append(url)
        raise AssertionError(f"unexpected POST {url}")

    async def close(self) -> None:
        return None


SHARED_VIEWER = "777"
KICK_CHANNEL = "4242"


async def _no_delay(_seconds: float) -> None:
    return None


@pytest.mark.asyncio
async def test_ac39_three_platforms_on_one_runtime() -> None:
    """AC39: with twitch, kick and youtube enabled together (plus ``clips``
    and ``stream_control`` with polls on), ``chat.write`` is one contract
    over the three wildcard destinations with exactly 3 bindings on disjoint
    platforms; the same viewer id on kick and on youtube is admitted into two
    sessions of the real scheduler; ``stream.clip.create`` is unbound for
    kick and youtube with ``platform_unsupported`` and ``stream.poll.create``
    is bound for twitch only (no poll service is published for kick or
    youtube), a call on either platform making 0 requests."""

    clock = ManualClock(NOW)
    registry = TriggerRegistry()

    async def no_run(_context: Any) -> None:
        return None

    scheduler = AdmissionScheduler(
        no_run,
        session_queue_capacity=4,
        global_pending_capacity=16,
        max_sessions=8,
        workers=1,
        total_run_seconds=60.0,
        wait_seconds=30.0,
        clock=clock,
        sleeper=clock.sleep,
    )
    runtime = runtime_context(clock=clock, trigger_registry=registry, scheduler=scheduler)
    twitch_session = TwitchValidationSession()
    kick_session = KickSession()
    tokens = ScriptedTokenEndpoint(clock, 3600)
    api = ScriptedLiveChatAPI(
        clock,
        token_endpoint=tokens,
        broadcasts={CHANNEL: LIVE_CHAT},
        pages=(page(chat_item("yt-1", "hello", author=SHARED_VIEWER)),),
    )
    loader = ModuleLoader(runtime.bus, ROOT / "modules", context=runtime, environ={})
    activations = await loader.activate_enabled(
        {
            "enabled_modules": ["twitch", "kick", "youtube", "clips", "stream_control"],
            "modules": {
                "twitch": {
                    "client_id": "twitch-client",
                    "client_secret": "twitch-client-secret-value",
                    "access_token": "twitch-access-token-value",
                    "broadcaster_id": "broadcaster-42",
                    "bot_user_id": "bot-24",
                    "companion_name": COMPANION,
                    "_session_factory": lambda: twitch_session,
                    "_retry_delay": _no_delay,
                },
                "kick": {
                    "client_secret": "kick-client-secret-value",
                    "access_token": "kick-access-token-value",
                    "listener": {"host": "127.0.0.1", "port": 0},
                    "public_key": KICK_TEST_KEY.public_pem,
                    "channels": [KICK_CHANNEL],
                    "companion_name": COMPANION,
                    "_session_factory": lambda: kick_session,
                    "_wall_clock": clock,
                },
                "youtube": {
                    **BASE_SETTINGS,
                    "_session_factory": lambda: api,
                    "_wall_clock": clock,
                    "_sleeper": clock.sleep,
                },
                "clips": {"_sleeper": clock.sleep},
                "stream_control": {
                    "scenes": {"provider": {"kind": "none"}},
                    "polls": {"enabled": True},
                },
            },
        }
    )
    handles = {activation.name: activation.handle for activation in activations}
    client: TestClient | None = None
    try:
        for name in ("twitch", "kick", "youtube", "clips", "stream_control"):
            await handles[name].prepare()

        # chat.write: one contract, three wildcard destinations, 3 bindings.
        spec = runtime.actions.discovered()[CHAT_WRITE_ACTION]
        assert spec.supported_destinations == (
            Destination("twitch", "*", "chat"),
            Destination("kick", "*", "chat"),
            Destination("youtube", "*", "chat"),
        )
        bindings = runtime.actions.bindings(CHAT_WRITE_ACTION)
        assert len(bindings) == 3
        assert [(binding.module, binding.destination.platform) for binding in bindings] == [
            ("twitch", "twitch"), ("kick", "kick"), ("youtube", "youtube"),
        ]
        assert all(binding.destination.scope == "chat" for binding in bindings)
        assert len({binding.destination.platform for binding in bindings}) == 3

        # Clips and polls: bound for twitch only.
        entries = set(runtime.services.entries())
        for platform in ("kick", "youtube"):
            assert ("moderation", platform) in entries
            assert ("clip", platform) not in entries and ("poll", platform) not in entries
        assert handles["clips"].unbound == {
            "kick": "platform_unsupported", "youtube": "platform_unsupported",
        }
        assert handles["stream_control"].poll_platforms == ("twitch",)
        for action in ("stream.clip.create", "stream.poll.create"):
            assert [binding.destination.platform for binding in runtime.actions.bindings(action)] == [
                "twitch"
            ], action

        # A call on kick or youtube finds no provider and makes 0 requests.
        for action, permission, arguments in (
            ("stream.clip.create", "stream.clip", {}),
            ("stream.poll.create", "stream.poll",
             {"question": "Next?", "options": ["A", "B"], "duration_seconds": 60}),
        ):
            runtime.actions._authorization.grant(
                AuthorizationRule(
                    rule_id=f"grant-{action}", action_name=action,
                    granted_permissions=(permission,),
                )
            )
            for platform, channel in (("kick", KICK_CHANNEL), ("youtube", CHANNEL)):
                number = next(_CALLS)
                refused = await runtime.executor.invoke(
                    ActionCall(
                        action_name=action,
                        action_version=1,
                        arguments=arguments,
                        conversation_id="conversation-1",
                        run_id=f"run-{number}",
                        call_id=f"call-{number}",
                        source_event_id="source-1",
                        destination=Destination(platform, channel, action.split(".")[1]),
                        principal="brain",
                        deadline=clock.now + 100.0,
                    )
                )
                assert refused.status == "error", (action, platform, refused)
                assert refused.error["code"] == "no_provider", (action, platform, refused)
        assert kick_session.requests == [] and twitch_session.requests == [
            "https://id.twitch.tv/oauth2/validate"
        ]

        # The same viewer id on two platforms: two sessions.
        registry.configure("kick", audience_policy("everyone"))
        registry.configure("youtube", audience_policy("everyone"))
        for name in ("kick", "youtube"):
            await handles[name].start_inputs()
        client = TestClient(TestServer(handles["kick"].build_application()))
        await client.start_server()
        sender = SignedWebhookSender(KICK_TEST_KEY, clock)
        status = await sender.post(
            client,
            "chat.message.sent",
            {
                "message_id": "kick-1",
                "broadcaster": {"is_anonymous": False, "user_id": int(KICK_CHANNEL),
                                "username": "streamer"},
                "sender": {"is_anonymous": False, "user_id": int(SHARED_VIEWER),
                           "username": "viewer", "identity": {"badges": []}},
                "content": "hello",
            },
        )
        assert status == 200
        await settle(100)
        assert api.counts["list"] == 1
        assert scheduler.live_sessions == 2
        for key in (
            SessionKey(platform="kick", channel_id=KICK_CHANNEL, viewer_id=SHARED_VIEWER),
            SessionKey(platform="youtube", channel_id=CHANNEL, viewer_id=SHARED_VIEWER),
        ):
            assert scheduler.queue_depth(key) == 1, key

        assert kick_session.requests == []
        assert api.counts["insert"] == api.counts["ban"] == api.counts["delete"] == 0
    finally:
        if client is not None:
            await client.close()
        for name in ("stream_control", "clips", "youtube", "kick", "twitch"):
            handle = handles[name]
            if hasattr(handle, "stop_inputs"):
                await handle.stop_inputs()
            await handle.close()
        await scheduler.aclose()


_PLATFORM_WORD = re.compile(r"\b(kick|youtube)\b", re.IGNORECASE)


def test_ac39_core_names_no_platform() -> None:
    """AC39: a case-insensitive word scan of ``core/`` finds 0 occurrences
    of ``kick`` and ``youtube``: no platform literal enters the core."""

    hits = [
        f"{path.relative_to(ROOT)}:{number}"
        for path in sorted((ROOT / "core").rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts
        for number, line in enumerate(
            path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1
        )
        if _PLATFORM_WORD.search(line)
    ]
    assert hits == []
    assert _PLATFORM_WORD.search("a Kick webhook") and _PLATFORM_WORD.search("YOUTUBE")
    assert not _PLATFORM_WORD.search("kicked youtuber")
