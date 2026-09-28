"""``modules/users``: the directory of observed authors and ``users.read`` (R5).

AC27 — bounded directory and paging: 150 authors on a channel bounded at
100 page as 40, 40 and 20 with ``has_more`` true, true, false, no author
repeated, ``coverage.complete`` false on every page and ``coverage.evicted
== 50``; a cursor minted for another channel is ``invalid_arguments``.
AC28 — freshness and attested roles: an author whose last message is older
than ``max_age_seconds`` on the injected clock is absent; ``roles`` is
present only for an author whose event carried ``author.roles`` with
``author.roles_provenance`` and absent (not empty) otherwise, on the fake
platform's own events; ``freshness.observed_at`` equals the injected clock
at read. AC30 — default-deny: without an applicable rule the call is
``refused not_authorized`` and the provider is entered 0 times, on two
platforms. Channels are bounded; the manifest is v2 and declares the action
without granting it; no platform name appears in the module.

Every read goes through the real executor built by ``runtime_context`` —
authorization, argument schema, result schema and parts validation are the
executor's, not mocked — and every author reaches the directory through a
real bus publication of ``channel.chat.message`` on the injected clock.
"""

from __future__ import annotations

import base64
import inspect
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from core.actions import (
    ERROR_INVALID_ARGUMENTS,
    ERROR_NOT_AUTHORIZED,
    AuthorizationRule,
)
from core.contracts import ActionCall, ActionObservation, Destination
from core.lifecycle import PhaseCoordinator
from core.loader import ModuleLoader
from core.runtime import RUNTIME_API, ModuleContext, RuntimeContext
from core.triggers import TriggerRegistry
from conftest import ManualClock, runtime_context

from modules.users import (
    CHAT_EVENT,
    COVERAGE_KIND,
    MANIFEST_PATH,
    MAX_LIMIT,
    MIN_LIMIT,
    MODULE_NAME,
    USERS_READ_ACTION,
    USERS_READ_PROVIDER,
    UsersDirectory,
    UsersModule,
    UsersModuleError,
    activate,
    render_page,
    validate_settings,
)


ROOT = Path(__file__).parents[1]
MODULE_DIR = ROOT / "modules" / "users"
FIXTURE_MODULES = ROOT / "tests" / "fixtures" / "modules"

MAX_CHANNELS = 4
MAX_USERS_PER_CHANNEL = 100
MAX_AGE_SECONDS = 600.0
SETTINGS: dict[str, Any] = {
    "max_channels": MAX_CHANNELS,
    "max_users_per_channel": MAX_USERS_PER_CHANNEL,
    "max_age_seconds": MAX_AGE_SECONDS,
}

#: The accepted ``limits`` block as the entry point hands it to every enabled
#: module: accepted by this module's schema and hook, never read.
LIMITS: dict[str, Any] = {
    "chat_context": {
        "max_messages": 8,
        "max_bytes": 65536,
        "max_age_seconds": 600.0,
        "max_channels": 8,
    },
}

PRINCIPAL = "brain"

TWITCH_CHAT = Destination("twitch", "channel-1", "chat")
FAKE_CHAT = Destination("fake", "channel-9", "chat")


# --------------------------------------------------------------------------- #
# Harness
# --------------------------------------------------------------------------- #


def module_context(clock: ManualClock | None = None) -> ModuleContext:
    target = clock if clock is not None else ManualClock()
    return runtime_context(clock=target).for_module(MODULE_NAME)


def runtime_of(context: ModuleContext) -> RuntimeContext:
    return context._runtime


def grant_users_read(context: ModuleContext, destination: Destination | None = None) -> None:
    rule: dict[str, Any] = {
        "rule_id": "grant-users-read",
        "action_name": USERS_READ_ACTION,
        "granted_permissions": ("users.read",),
    }
    if destination is not None:
        rule["destination"] = destination
    runtime_of(context).actions._authorization.grant(AuthorizationRule(**rule))


async def prepared_module(
    context: ModuleContext, settings: dict[str, Any] | None = None
) -> UsersModule:
    handle = await activate(context, settings if settings is not None else SETTINGS, {})
    await handle.prepare()
    return handle


def chat_event(
    platform: str,
    channel_id: str,
    author: Any,
    message_id: str = "m",
) -> tuple[dict[str, Any], dict[str, Any]]:
    """The schema-version-2 payload and metadata every input publishes."""

    payload = {
        "platform": platform,
        "channel_id": channel_id,
        "author": author,
        "message_id": message_id,
        "text": "hello",
    }
    return payload, {"source": platform, "schema_version": 2}


async def speak(
    context: ModuleContext,
    platform: str,
    channel_id: str,
    user_ids: list[str],
    *,
    step: float = 1.0,
) -> None:
    """Publish one message per author on the bus, advancing the clock between them."""

    clock = runtime_of(context).clock
    bus = runtime_of(context).bus
    for index, user_id in enumerate(user_ids):
        payload, metadata = chat_event(
            platform, channel_id, {"id": user_id, "display_name": user_id.upper()}, f"m{index}"
        )
        await bus.publish(CHAT_EVENT, payload, metadata)
        clock.advance(step)


def authors(count: int, prefix: str = "u") -> list[str]:
    return [f"{prefix}{index:03d}" for index in range(count)]


def users_read_call(
    destination: Destination,
    arguments: dict[str, Any],
    call_id: str = "call-1",
) -> ActionCall:
    return ActionCall(
        action_name=USERS_READ_ACTION,
        action_version=1,
        arguments=arguments,
        conversation_id="conversation-1",
        run_id="run-1",
        call_id=call_id,
        source_event_id="source-1",
        destination=destination,
        principal=PRINCIPAL,
        deadline=10_000.0,
        message_id="source-1",
    )


async def read(
    context: ModuleContext,
    destination: Destination,
    limit: Any,
    cursor: Any = None,
    call_id: str = "call-1",
) -> ActionObservation:
    arguments: dict[str, Any] = {"limit": limit}
    if cursor is not None:
        arguments["cursor"] = cursor
    return await runtime_of(context).executor.invoke(
        users_read_call(destination, arguments, call_id)
    )


def ids_of(observation: ActionObservation) -> list[str]:
    assert observation.status == "success", observation.error
    assert observation.result is not None
    return [user["user_id"] for user in observation.result["users"]]


def cursor_for(platform: str, channel_id: str, last_seen: float, user_id: str) -> str:
    """A cursor as the module mints it, for the foreign-destination case."""

    raw = "/".join((platform, channel_id, repr(float(last_seen)), user_id))
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


async def walk(
    context: ModuleContext, destination: Destination, limit: int
) -> list[ActionObservation]:
    """Every page of the destination, following ``next_cursor`` to the end."""

    pages: list[ActionObservation] = []
    cursor: str | None = None
    while True:
        page = await read(context, destination, limit, cursor, f"call-page-{len(pages)}")
        assert page.status == "success", page.error
        assert page.result is not None
        pages.append(page)
        if not page.result["page"]["has_more"]:
            return pages
        cursor = page.result["page"]["next_cursor"]
        assert isinstance(cursor, str) and cursor


# --------------------------------------------------------------------------- #
# AC27: bounded directory, ordered pages, stable cursor
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("destination", [TWITCH_CHAT, FAKE_CHAT], ids=["twitch", "fake"])
async def test_150_authors_on_a_bound_of_100_page_as_40_40_20_without_repeat(
    destination: Destination,
) -> None:
    """AC27: pages of 40 return 40, 40 and 20 with ``has_more`` true, true,
    false; no author repeated; ``coverage.complete`` false on every page,
    ``evicted == 50`` — the 50 oldest-seen authors — and the retained 100
    are the most recently seen, newest first."""

    clock = ManualClock()
    context = module_context(clock)
    handle = await prepared_module(context)
    grant_users_read(context)
    try:
        spoken = authors(150)
        await speak(context, destination.platform, destination.channel_id, spoken)

        pages = await walk(context, destination, 40)

        assert [p.result["page"]["returned"] for p in pages] == [40, 40, 20]
        assert [p.result["page"]["has_more"] for p in pages] == [True, True, False]
        assert pages[-1].result["page"]["next_cursor"] is None
        seen = [user_id for page in pages for user_id in ids_of(page)]
        assert len(seen) == 100 == len(set(seen))
        # The 50 evicted are the oldest ``last_seen``; the rest come newest first.
        assert seen == list(reversed(spoken[50:]))
        for page in pages:
            coverage = dict(page.result["coverage"])
            assert coverage == {
                "kind": COVERAGE_KIND,
                "complete": False,
                "retained_users": MAX_USERS_PER_CHANNEL,
                "evicted": 50,
            }
            assert page.result["freshness"]["window_seconds"] == MAX_AGE_SECONDS
            assert page.provenance["provider"] == USERS_READ_PROVIDER
        assert runtime_of(context).executor.provider_invocations == 3
    finally:
        await handle.close()


async def test_a_cursor_from_another_channel_or_a_malformed_one_is_invalid_arguments() -> None:
    """AC27: a cursor minted on another channel — and one on another platform,
    one that does not decode, an empty one — is ``error invalid_arguments``
    with no page returned; the same cursor on its own channel pages on."""

    clock = ManualClock()
    context = module_context(clock)
    handle = await prepared_module(context)
    grant_users_read(context)
    try:
        other = Destination("fake", "channel-2", "chat")
        await speak(context, FAKE_CHAT.platform, FAKE_CHAT.channel_id, authors(5, "a"))
        await speak(context, other.platform, other.channel_id, authors(5, "b"))

        first = await read(context, FAKE_CHAT, 2)
        cursor = first.result["page"]["next_cursor"]
        assert isinstance(cursor, str)

        foreign = await read(context, other, 2, cursor, "call-foreign")
        assert foreign.status == "error"
        assert foreign.error is not None
        assert foreign.error["code"] == ERROR_INVALID_ARGUMENTS
        assert foreign.result is None

        other_platform = await read(
            context,
            Destination("twitch", FAKE_CHAT.channel_id, "chat"),
            2,
            cursor,
            "call-other-platform",
        )
        assert other_platform.status == "error"
        assert other_platform.error["code"] == ERROR_INVALID_ARGUMENTS

        minted_elsewhere = cursor_for("fake", "channel-2", 1001.0, "b001")
        crossed = await read(context, FAKE_CHAT, 2, minted_elsewhere, "call-crossed")
        assert crossed.status == "error"
        assert crossed.error["code"] == ERROR_INVALID_ARGUMENTS

        for index, bad in enumerate(["", "not base64!", base64.b64encode(b"a/b").decode()]):
            garbage = await read(context, FAKE_CHAT, 2, bad, f"call-garbage-{index}")
            assert garbage.status == "error", bad
            assert garbage.error["code"] == ERROR_INVALID_ARGUMENTS

        own = await read(context, FAKE_CHAT, 2, cursor, "call-own")
        assert ids_of(own) == ["a002", "a001"]
        assert own.result["page"]["has_more"] is True
    finally:
        await handle.close()


async def test_cursor_encodes_the_sort_key_so_eviction_between_pages_never_repeats() -> None:
    """P15 risk: the cursor is a sort key, not an offset. Authors who speak
    between two pages evict the tail and move ahead of the cursor, so the
    second page is shorter and repeats nobody; an author already returned
    who speaks again is not returned again."""

    clock = ManualClock()
    context = module_context(clock)
    handle = await prepared_module(
        context, {**SETTINGS, "max_users_per_channel": 6}
    )
    grant_users_read(context)
    try:
        await speak(context, "fake", "c", ["a", "b", "c", "d", "e", "f"])
        first = await read(context, Destination("fake", "c", "chat"), 3, None, "call-1")
        assert ids_of(first) == ["f", "e", "d"]
        cursor = first.result["page"]["next_cursor"]

        # Two newcomers evict ``a`` and ``b`` (oldest seen); ``f`` speaks
        # again and moves ahead of the cursor.
        await speak(context, "fake", "c", ["g", "h", "f"])

        second = await read(context, Destination("fake", "c", "chat"), 3, cursor, "call-2")
        assert ids_of(second) == ["c"]
        assert second.result["page"]["has_more"] is False
        assert second.result["coverage"]["evicted"] == 2
        assert second.result["coverage"]["retained_users"] == 6
        assert set(ids_of(first)).isdisjoint(ids_of(second))

        fresh = await read(context, Destination("fake", "c", "chat"), 10, None, "call-3")
        assert ids_of(fresh) == ["f", "h", "g", "e", "d", "c"]
    finally:
        await handle.close()


async def test_order_is_last_seen_desc_then_user_id_and_a_page_boundary_holds_ties() -> None:
    """R5: authors seen at the same instant order by identifier; a cursor at
    a tie continues with the next identifier, no repeat, no gap."""

    clock = ManualClock()
    context = module_context(clock)
    handle = await prepared_module(context)
    grant_users_read(context)
    try:
        # Same instant for all five: order falls back on the identifier.
        await speak(context, "fake", "tie", ["e", "c", "a", "d", "b"], step=0.0)
        pages = await walk(context, Destination("fake", "tie", "chat"), 2)
        assert [ids_of(page) for page in pages] == [["a", "b"], ["c", "d"], ["e"]]
    finally:
        await handle.close()


@pytest.mark.parametrize("limit", [0, 101], ids=["zero", "hundred-and-one"])
async def test_limit_outside_1_to_100_is_invalid_arguments_before_the_provider(
    limit: int,
) -> None:
    """R5: ``limit`` 0 and 101 are ``error invalid_arguments``, refused by the
    executor's schema check — the provider is entered 0 times."""

    context = module_context()
    handle = await prepared_module(context)
    grant_users_read(context)
    try:
        await speak(context, FAKE_CHAT.platform, FAKE_CHAT.channel_id, ["a"])

        observation = await read(context, FAKE_CHAT, limit)

        assert observation.status == "error"
        assert observation.error is not None
        assert observation.error["code"] == ERROR_INVALID_ARGUMENTS
        assert observation.result is None
        assert runtime_of(context).executor.provider_invocations == 0
    finally:
        await handle.close()


async def test_provider_refuses_an_out_of_range_limit_on_its_own() -> None:
    """R5: invoked outside the executor, the provider re-checks ``limit`` and
    the cursor and answers ``invalid_arguments`` rather than paging."""

    context = module_context()
    handle = await prepared_module(context)
    try:
        (binding,) = runtime_of(context).actions.bindings(USERS_READ_ACTION)

        class _Invocation:
            def __init__(self, call: ActionCall) -> None:
                self.call = call
                self.emission: list[str] = []

            def mark_not_emitted(self) -> None:
                self.emission.append("not_emitted")

        for bad in ({"limit": 0}, {"limit": 101}, {"limit": True}, {"limit": "3"}, {}):
            invocation = _Invocation(users_read_call(FAKE_CHAT, bad))
            observation = await binding.provider.invoke(invocation)
            assert observation.status == "error", bad
            assert observation.error is not None
            assert observation.error["code"] == ERROR_INVALID_ARGUMENTS
            assert invocation.emission == ["not_emitted"]
        invocation = _Invocation(users_read_call(FAKE_CHAT, {"limit": 1, "cursor": 7}))
        observation = await binding.provider.invoke(invocation)
        assert observation.status == "error"
        assert observation.error["code"] == ERROR_INVALID_ARGUMENTS
    finally:
        await handle.close()


# --------------------------------------------------------------------------- #
# AC28: age window, attested roles, freshness
# --------------------------------------------------------------------------- #


async def test_an_author_older_than_max_age_seconds_is_absent_and_freshness_is_the_clock() -> None:
    """AC28: an author whose last message is older than ``max_age_seconds``
    on the injected clock is absent from the page and from
    ``retained_users``; one who spoke again inside the window stays;
    ``freshness.observed_at`` equals the injected clock at read."""

    clock = ManualClock()
    context = module_context(clock)
    handle = await prepared_module(context)
    grant_users_read(context)
    try:
        await speak(context, FAKE_CHAT.platform, FAKE_CHAT.channel_id, ["old", "kept"])
        clock.advance(MAX_AGE_SECONDS - 1.0)
        await speak(context, FAKE_CHAT.platform, FAKE_CHAT.channel_id, ["kept"])
        # ``old`` last spoke at 1000; the horizon is now past it.
        clock.advance(2.0)
        assert clock() - 1000.0 > MAX_AGE_SECONDS
        read_at = clock()

        observation = await read(context, FAKE_CHAT, 10)

        assert ids_of(observation) == ["kept"]
        assert observation.result["coverage"]["retained_users"] == 1
        # An aged-out author is not a bound eviction.
        assert observation.result["coverage"]["evicted"] == 0
        assert dict(observation.result["freshness"]) == {
            "observed_at": read_at,
            "window_seconds": MAX_AGE_SECONDS,
        }
        (kept,) = observation.result["users"]
        assert kept["first_seen"] == 1001.0
        assert kept["last_seen"] == 1002.0 + MAX_AGE_SECONDS - 1.0

        clock.advance(MAX_AGE_SECONDS + 1.0)
        aged = await read(context, FAKE_CHAT, 10, None, "call-aged")
        assert ids_of(aged) == []
        assert aged.result["coverage"]["retained_users"] == 0
        assert aged.result["freshness"]["observed_at"] == clock()
        assert aged.result["page"] == {"returned": 0, "has_more": False, "next_cursor": None}
    finally:
        await handle.close()


async def test_roles_appear_only_with_a_provenance_on_the_fake_platforms_events() -> None:
    """AC28/decision 5: driven through the fake platform's own reception, an
    author whose message carried ``author.roles`` with
    ``author.roles_provenance`` reads with both; one whose message carried
    neither has no ``roles`` key — absent, not empty."""

    clock = ManualClock()
    context = runtime_context(clock=clock, trigger_registry=TriggerRegistry(companion_name="Ada"))
    users_context = context.for_module(MODULE_NAME)
    handle = await prepared_module(users_context)
    grant_users_read(users_context)
    fixtures = ModuleLoader(context.bus, FIXTURE_MODULES, context=context, environ={})
    (activation,) = await fixtures.activate_enabled(
        {
            "enabled_modules": ["fakeplatform"],
            "modules": {
                "fakeplatform": {"channel_ids": ["channel-9"], "companion_name": "Ada"},
            },
        }
    )
    platform = activation.handle
    await platform.prepare()
    try:
        attested = await platform.inject(
            {
                "channel_id": "channel-9",
                "author": {
                    "id": "mod-1",
                    "display_name": "Mod",
                    "roles": ["moderator", "vip"],
                    "roles_provenance": "fake.badges",
                },
                "message_id": "m1",
                "text": "hi",
            }
        )
        assert attested is not None
        assert attested["payload"]["author"]["roles"] == ["moderator", "vip"]
        clock.advance(1.0)
        plain = await platform.inject(
            {
                "channel_id": "channel-9",
                "author": {"id": "viewer-1", "display_name": "Viewer"},
                "message_id": "m2",
                "text": "hello",
            }
        )
        assert plain is not None
        assert "roles" not in plain["payload"]["author"]
        clock.advance(1.0)
        bare = await platform.inject(
            {"channel_id": "channel-9", "author": "viewer-2", "message_id": "m3", "text": "yo"}
        )
        assert bare is not None

        observation = await read(users_context, FAKE_CHAT, 10)

        assert ids_of(observation) == ["viewer-2", "viewer-1", "mod-1"]
        by_id = {user["user_id"]: dict(user) for user in observation.result["users"]}
        assert by_id["mod-1"] == {
            "user_id": "mod-1",
            "display_name": "Mod",
            "roles": ("moderator", "vip"),
            "roles_provenance": "fake.badges",
            "first_seen": 1000.0,
            "last_seen": 1000.0,
        }
        assert by_id["viewer-1"] == {
            "user_id": "viewer-1",
            "display_name": "Viewer",
            "first_seen": 1001.0,
            "last_seen": 1001.0,
        }
        assert "roles" not in by_id["viewer-1"] and "roles_provenance" not in by_id["viewer-1"]
        # No display name on the event: the identifier stands in.
        assert by_id["viewer-2"]["display_name"] == "viewer-2"
        assert "roles" not in by_id["viewer-2"]
    finally:
        await platform.close()
        await handle.close()


async def test_roles_without_a_provenance_or_not_a_list_of_strings_are_not_recorded() -> None:
    """Decision 5: on the raw bus event, ``roles`` without a non-empty
    ``roles_provenance``, or not a list of strings, attests nothing — the
    author reads without ``roles``; a later attested event adds them and a
    later unattested one leaves the attestation in place."""

    clock = ManualClock()
    context = module_context(clock)
    handle = await prepared_module(context)
    grant_users_read(context)
    bus = runtime_of(context).bus
    try:
        for author in (
            {"id": "u1", "roles": ["moderator"]},
            {"id": "u2", "roles": ["moderator"], "roles_provenance": ""},
            {"id": "u3", "roles": "moderator", "roles_provenance": "badges"},
            {"id": "u4", "roles": ["moderator", 7], "roles_provenance": "badges"},
            {"id": "u5", "roles": [], "roles_provenance": "badges"},
        ):
            await bus.publish(CHAT_EVENT, *chat_event("fake", "channel-9", author))
            clock.advance(1.0)

        observation = await read(context, FAKE_CHAT, 10)
        by_id = {user["user_id"]: user for user in observation.result["users"]}
        for user_id in ("u1", "u2", "u3", "u4"):
            assert "roles" not in by_id[user_id], user_id
            assert "roles_provenance" not in by_id[user_id], user_id
        # An attested empty list is a claim of no roles, and is reported as such.
        assert by_id["u5"]["roles"] == ()
        assert by_id["u5"]["roles_provenance"] == "badges"

        await bus.publish(
            CHAT_EVENT,
            *chat_event("fake", "channel-9", {"id": "u1", "roles": ["vip"], "roles_provenance": "badges"}),
        )
        clock.advance(1.0)
        await bus.publish(CHAT_EVENT, *chat_event("fake", "channel-9", {"id": "u1"}))
        later = await read(context, FAKE_CHAT, 10, None, "call-later")
        (u1,) = [user for user in later.result["users"] if user["user_id"] == "u1"]
        assert u1["roles"] == ("vip",)
        assert u1["roles_provenance"] == "badges"
        assert u1["last_seen"] == clock()
    finally:
        await handle.close()


async def test_display_name_follows_the_latest_event_and_first_seen_stays() -> None:
    """R5: ``first_seen`` is the first observation, ``last_seen`` the latest,
    and the display name is the latest one carried."""

    clock = ManualClock()
    context = module_context(clock)
    handle = await prepared_module(context)
    grant_users_read(context)
    bus = runtime_of(context).bus
    try:
        await bus.publish(CHAT_EVENT, *chat_event("fake", "channel-9", {"id": "u", "display_name": "Old"}))
        clock.advance(5.0)
        await bus.publish(CHAT_EVENT, *chat_event("fake", "channel-9", {"id": "u", "display_name": "New"}))
        clock.advance(5.0)
        await bus.publish(CHAT_EVENT, *chat_event("fake", "channel-9", {"id": "u", "display_name": ""}))

        observation = await read(context, FAKE_CHAT, 1)
        (user,) = observation.result["users"]
        assert dict(user) == {
            "user_id": "u",
            "display_name": "New",
            "first_seen": 1000.0,
            "last_seen": 1010.0,
        }
    finally:
        await handle.close()


# --------------------------------------------------------------------------- #
# Bounded channels; the synchronous observer
# --------------------------------------------------------------------------- #


async def test_channels_beyond_max_channels_drop_the_least_recently_updated() -> None:
    """R5/R6: with ``max_channels: 2``, a third channel drops the least
    recently updated one whole — its authors and its eviction count — and
    that channel then pages as empty; the two others keep their state."""

    clock = ManualClock()
    context = module_context(clock)
    handle = await prepared_module(context, {**SETTINGS, "max_channels": 2})
    grant_users_read(context)
    try:
        await speak(context, "fake", "one", ["a"])
        await speak(context, "twitch", "two", ["b"])
        # ``one`` is refreshed, so ``two`` is the least recently updated.
        await speak(context, "fake", "one", ["a2"])
        await speak(context, "fake", "three", ["c"])

        assert handle.directory.channels() == (("fake", "one"), ("fake", "three"))
        dropped = await read(context, Destination("twitch", "two", "chat"), 10, None, "call-two")
        assert ids_of(dropped) == []
        assert dropped.result["coverage"] == {
            "kind": COVERAGE_KIND, "complete": False, "retained_users": 0, "evicted": 0,
        }
        kept = await read(context, Destination("fake", "one", "chat"), 10, None, "call-one")
        assert ids_of(kept) == ["a2", "a"]
        third = await read(context, Destination("fake", "three", "chat"), 10, None, "call-three")
        assert ids_of(third) == ["c"]

        # A channel that comes back starts over.
        await speak(context, "twitch", "two", ["b2"])
        assert ("fake", "one") not in handle.directory.channels()
        back = await read(context, Destination("twitch", "two", "chat"), 10, None, "call-back")
        assert ids_of(back) == ["b2"]
    finally:
        await handle.close()


async def test_the_observer_is_synchronous_ignores_malformed_events_and_never_fails_a_publication() -> None:
    """P15 risk: the handler is a plain function (never awaits), returns
    ``None`` so the event is neither replaced nor stopped, and records
    nothing for an event lacking ``platform``, ``channel_id`` or
    ``author.id`` — without failing the input's publication."""

    clock = ManualClock()
    context = module_context(clock)
    handle = await prepared_module(context)
    grant_users_read(context)
    bus = runtime_of(context).bus
    try:
        assert not inspect.iscoroutinefunction(handle.observe_event)
        assert handle.observe_event({"type": CHAT_EVENT, "payload": {}, "metadata": {}}) is None

        malformed = [
            {"channel_id": "channel-9", "author": {"id": "x"}},
            {"platform": "fake", "author": {"id": "x"}},
            {"platform": "fake", "channel_id": "channel-9"},
            {"platform": "fake", "channel_id": "channel-9", "author": "x"},
            {"platform": "fake", "channel_id": "channel-9", "author": {"id": ""}},
            {"platform": "fake", "channel_id": "channel-9", "author": {"id": 4}},
            {"platform": "", "channel_id": "channel-9", "author": {"id": "x"}},
            {"platform": "fake", "channel_id": 9, "author": {"id": "x"}},
        ]
        for payload in malformed:
            published = await bus.publish(CHAT_EVENT, payload, {})
            assert published["payload"] == payload
        # Another event type on the same bus is not observed either.
        await bus.publish("channel.chat.send", {"platform": "fake", "channel_id": "channel-9", "author": {"id": "y"}}, {})

        observation = await read(context, FAKE_CHAT, 10)
        assert ids_of(observation) == []
        assert observation.result["coverage"]["retained_users"] == 0
    finally:
        await handle.close()


def test_directory_observe_reports_whether_an_event_was_recorded() -> None:
    """R5: the pure directory records a well-formed event and refuses a
    malformed one by return value, never by raising."""

    from modules.users import _Settings

    directory = UsersDirectory(_Settings(max_channels=1, max_users_per_channel=1, max_age_seconds=1.0))
    assert directory.observe({"payload": {"platform": "p", "channel_id": "c", "author": {"id": "u"}}}, 1.0)
    assert not directory.observe({"payload": {"platform": "p", "channel_id": "c", "author": {}}}, 1.0)
    assert not directory.observe("nope", 1.0)
    assert not directory.observe({"payload": []}, 1.0)
    assert directory.channels() == (("p", "c"),)
    page = directory.page("p", "c", limit=5, cursor=None, now=1.5)
    assert [user["user_id"] for user in page.users] == ["u"]
    assert directory.page("p", "other", limit=5, cursor=None, now=1.5).users == ()


# --------------------------------------------------------------------------- #
# AC30: default-deny on both platforms
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("destination", [TWITCH_CHAT, FAKE_CHAT], ids=["twitch", "fake"])
async def test_read_without_a_rule_is_refused_and_the_provider_is_not_entered(
    destination: Destination,
) -> None:
    """AC30: no applicable rule → ``refused not_authorized``, provider invoked
    0 times, on ``twitch/*/chat`` and on ``fake/*/chat`` alike — declaring
    the action and binding the provider granted nothing."""

    context = module_context()
    handle = await prepared_module(context)
    try:
        await speak(context, destination.platform, destination.channel_id, ["a", "b"])
        assert USERS_READ_ACTION in runtime_of(context).actions.registered_ready()
        assert runtime_of(context).actions.authorized(principal=PRINCIPAL) == {}

        observation = await read(context, destination, 10)

        assert observation.status == "refused"
        assert observation.error is not None
        assert observation.error["code"] == ERROR_NOT_AUTHORIZED
        assert observation.result is None
        assert observation.parts == ()
        assert runtime_of(context).executor.provider_invocations == 0
    finally:
        await handle.close()


async def test_a_rule_scoped_to_one_platform_does_not_reach_the_other() -> None:
    """AC30/R5: a grant on ``fake/*/chat`` authorizes the fake channel and
    leaves the twitch one refused, the provider entered exactly once."""

    context = module_context()
    handle = await prepared_module(context)
    grant_users_read(context, Destination("fake", "*", "chat"))
    try:
        await speak(context, FAKE_CHAT.platform, FAKE_CHAT.channel_id, ["a"])
        await speak(context, TWITCH_CHAT.platform, TWITCH_CHAT.channel_id, ["b"])

        allowed = await read(context, FAKE_CHAT, 1, None, "call-fake")
        refused = await read(context, TWITCH_CHAT, 1, None, "call-twitch")

        assert ids_of(allowed) == ["a"]
        assert refused.status == "refused"
        assert refused.error is not None
        assert refused.error["code"] == ERROR_NOT_AUTHORIZED
        assert runtime_of(context).executor.provider_invocations == 1
    finally:
        await handle.close()


# --------------------------------------------------------------------------- #
# The text part (R4)
# --------------------------------------------------------------------------- #


async def test_text_part_equals_the_rendered_page() -> None:
    """R4/R5: the observation carries exactly one ``text`` part rendering the
    page — one line per author with name, identifier, instants and attested
    roles, then the page line — and nothing else."""

    clock = ManualClock()
    context = module_context(clock)
    handle = await prepared_module(context)
    grant_users_read(context)
    bus = runtime_of(context).bus
    try:
        await bus.publish(
            CHAT_EVENT,
            *chat_event("fake", "channel-9", {"id": "m1", "display_name": "Mod", "roles": ["moderator"], "roles_provenance": "badges"}),
        )
        clock.advance(0.5)
        await bus.publish(CHAT_EVENT, *chat_event("fake", "channel-9", {"id": "v1"}))
        clock.advance(0.25)
        await bus.publish(CHAT_EVENT, *chat_event("fake", "channel-9", {"id": "v2", "display_name": "Vee"}))

        observation = await read(context, FAKE_CHAT, 2)

        assert observation.status == "success", observation.error
        expected = (
            "Vee <v2> first 1000.750 last 1000.750\n"
            "v1 <v1> first 1000.500 last 1000.500\n"
            "page: 2 returned, more follow"
        )
        assert observation.parts == ({"type": "text", "text": expected},)
        assert observation.parts[0]["text"] == render_page(
            observation.result["users"], observation.result["page"]
        )

        rest = await read(context, FAKE_CHAT, 2, observation.result["page"]["next_cursor"], "call-2")
        assert rest.parts == (
            {
                "type": "text",
                "text": "Mod <m1> first 1000.000 last 1000.000 roles moderator (badges)\n"
                "page: 1 returned, no more",
            },
        )
    finally:
        await handle.close()


def test_render_page_shape() -> None:
    assert render_page([], {"returned": 0, "has_more": False}) == (
        "(no observed users)\npage: 0 returned, no more"
    )
    assert render_page(
        [
            {"user_id": "x", "display_name": "X", "first_seen": 1.0, "last_seen": 2.25},
            {
                "user_id": "y",
                "display_name": "Y",
                "first_seen": 1.0,
                "last_seen": 1.0,
                "roles": [],
                "roles_provenance": "p",
            },
        ],
        {"returned": 2, "has_more": True},
    ) == (
        "X <x> first 1.000 last 2.250\n"
        "Y <y> first 1.000 last 1.000 roles (none) (p)\n"
        "page: 2 returned, more follow"
    )


# --------------------------------------------------------------------------- #
# Manifest, settings hook, activation (R7)
# --------------------------------------------------------------------------- #


def test_manifest_is_v2_and_declares_users_read_without_granting_it() -> None:
    """R7/R5: manifest v2 with no role, consuming the chat event and producing
    nothing, no credential, a settings schema requiring the three bounds
    (plus the reserved ``limits`` block), the declared hook, and exactly the
    ``users.read`` read action over ``*/*/chat`` — a `read` carries no
    delivery capability and no grant lives in the file.

    Phase-4 R3 supersedes the former exact-dict assertion on
    ``max_channels``: the node now carries a ``title``, so the check keeps
    ``type``/``minimum``/``description`` and bounds the allowed keywords."""

    manifest = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert MANIFEST_PATH == MODULE_DIR / "module.yaml"

    assert manifest["name"] == MODULE_NAME == "users"
    assert manifest["manifest_version"] == 2
    assert manifest["runtime_api"] == RUNTIME_API
    assert manifest["produces"] == []
    assert manifest["consumes"] == [CHAT_EVENT] == ["channel.chat.message"]
    assert manifest["middleware"] is False
    assert manifest["lifecycle"] == {"roles": []}
    assert manifest["credentials"] == []
    assert manifest["settings_validator"] == "validate_settings"

    schema = manifest["settings_schema"]
    assert schema["type"] == "object"
    assert set(schema["properties"]) == {
        "max_channels", "max_users_per_channel", "max_age_seconds", "limits",
    }
    assert set(schema["required"]) == {"max_channels", "max_users_per_channel", "max_age_seconds"}
    # R3 (phase 4) supersedes the former exact-dict check: every setting node
    # now carries a ``title`` (and may carry a ``default``). The type and
    # minimum checks are kept; no other keyword may appear.
    max_channels = schema["properties"]["max_channels"]
    assert max_channels["type"] == "integer"
    assert max_channels["minimum"] == 1
    assert isinstance(max_channels["description"], str)
    assert max_channels["description"].strip()
    assert isinstance(max_channels["title"], str) and max_channels["title"].strip()
    assert set(max_channels) <= {"type", "minimum", "description", "title", "default"}
    assert schema["properties"]["max_users_per_channel"]["type"] == "integer"
    assert schema["properties"]["max_age_seconds"]["type"] == "number"
    assert schema["properties"]["limits"]["type"] == "object"
    assert schema["additionalProperties"] is False

    (action,) = manifest["actions"]
    assert action["name"] == "users.read"
    assert action["version"] == 1
    assert action["nature"] == "read"
    assert action["required_permissions"] == ["users.read"]
    assert action["supported_destinations"] == [
        {"platform": "*", "channel_id": "*", "scope": "chat"}
    ]
    assert action["argument_schema"]["required"] == ["limit"]
    limit = action["argument_schema"]["properties"]["limit"]
    assert (limit["type"], limit["minimum"], limit["maximum"]) == ("integer", MIN_LIMIT, MAX_LIMIT)
    assert (MIN_LIMIT, MAX_LIMIT) == (1, 100)
    assert action["argument_schema"]["properties"]["cursor"]["type"] == "string"
    assert set(action["argument_schema"]["properties"]) == {"limit", "cursor"}
    assert action["argument_schema"]["additionalProperties"] is False
    result = action["result_schema"]
    assert set(result["required"]) == {"users", "page", "freshness", "coverage"}
    assert result["additionalProperties"] is False
    user = result["properties"]["users"]["items"]
    assert set(user["properties"]) == {
        "user_id", "display_name", "roles", "roles_provenance", "first_seen", "last_seen",
    }
    assert set(user["required"]) == {"user_id", "display_name", "first_seen", "last_seen"}
    assert user["properties"]["roles"] == {
        "type": "array",
        "items": {"type": "string"},
        "description": user["properties"]["roles"]["description"],
    }
    assert set(result["properties"]["page"]["required"]) == {"returned", "has_more", "next_cursor"}
    assert set(result["properties"]["freshness"]["required"]) == {"observed_at", "window_seconds"}
    coverage = result["properties"]["coverage"]
    assert set(coverage["required"]) == {"kind", "complete", "retained_users", "evicted"}
    assert coverage["properties"]["kind"]["enum"] == [COVERAGE_KIND] == ["observed_authors"]
    assert coverage["properties"]["complete"]["enum"] == [False]
    assert action["timeout_seconds"] == 5
    assert action["idempotency"] == "natural"
    assert "delivery" not in action
    assert set(action) == {
        "name",
        "version",
        "description",
        "argument_schema",
        "result_schema",
        "nature",
        "required_permissions",
        "supported_destinations",
        "timeout_seconds",
        "idempotency",
    }
    assert set(manifest) == {
        "name",
        "manifest_version",
        "runtime_api",
        "produces",
        "consumes",
        "middleware",
        "lifecycle",
        "settings_schema",
        "settings_validator",
        "credentials",
        "actions",
    }
    # No grant lives in the file: the permission is *required* by the
    # action and appears nowhere else in the declaration.
    assert not any(key in manifest for key in ("rules", "authorization", "grants"))


def test_module_names_no_platform() -> None:
    """R5/AC31: ``grep twitch`` over the module directory is 0 lines — the
    platform is the event's and the destination's, a runtime value, never a
    name in code."""

    completed = subprocess.run(
        ["grep", "-r", "-i", "-c", "twitch", str(MODULE_DIR / "__init__.py"), str(MANIFEST_PATH)],
        capture_output=True,
        text=True,
        check=False,
    )
    # ``grep -c`` prints ``<file>:0`` per file and exits 1 when nothing matched.
    assert completed.returncode == 1, completed.stdout
    assert all(line.endswith(":0") for line in completed.stdout.splitlines())
    for path in (MODULE_DIR / "__init__.py", MANIFEST_PATH):
        assert "twitch" not in path.read_text(encoding="utf-8").lower()


def test_settings_hook_requires_the_three_bounds_and_names_fields() -> None:
    """R7/AC24: the three bounds are required, finite and positive; the
    reserved ``limits`` block is accepted; any other key is refused by name;
    one diagnostic per field, no value echoed."""

    assert validate_settings(SETTINGS) == []
    assert validate_settings({**SETTINGS, "limits": LIMITS}) == []

    assert validate_settings({}) == [
        "module 'users': field 'max_channels': is required",
        "module 'users': field 'max_users_per_channel': is required",
        "module 'users': field 'max_age_seconds': is required",
    ]
    bad = {"max_channels": 0, "max_users_per_channel": 2.5, "max_age_seconds": float("inf")}
    diagnostics = validate_settings(bad)
    assert diagnostics == [
        "module 'users': field 'max_channels': must be a positive integer",
        "module 'users': field 'max_users_per_channel': must be a positive integer",
        "module 'users': field 'max_age_seconds': must be a finite positive number",
    ]
    assert validate_settings(
        {"max_channels": True, "max_users_per_channel": "9", "max_age_seconds": -1}
    ) == [
        "module 'users': field 'max_channels': must be a positive integer",
        "module 'users': field 'max_users_per_channel': must be a positive integer",
        "module 'users': field 'max_age_seconds': must be a finite positive number",
    ]
    assert validate_settings({**SETTINGS, "max_age_seconds": 0}) == [
        "module 'users': field 'max_age_seconds': must be a finite positive number",
    ]
    assert validate_settings({**SETTINGS, "max_age_seconds": 1}) == []
    assert validate_settings({**SETTINGS, "companion_name": "Ada"}) == [
        "module 'users': field 'companion_name': is not a setting this module declares",
    ]
    assert validate_settings(["not", "a", "mapping"]) == [
        "module 'users': field 'settings': must be a mapping",
    ]
    rendered = "\n".join(validate_settings(bad))
    assert "2.5" not in rendered and "inf" not in rendered.replace("invalid", "")


async def test_activation_refuses_bad_settings_and_a_runtime_without_a_bus_or_clock() -> None:
    """R7: a handle built outside the loader is refused on the hook's terms,
    and a context lacking the bus, the actions facade or a clock is refused
    at activation, declaring nothing."""

    context = module_context()
    with pytest.raises(UsersModuleError):
        await activate(context, {}, {})
    with pytest.raises(UsersModuleError):
        await activate(context, "settings", {})  # type: ignore[arg-type]
    with pytest.raises(UsersModuleError):
        await activate(context, {**SETTINGS, "max_channels": -1}, {})

    class _Missing:
        def __init__(self, **fields: Any) -> None:
            self.__dict__.update(fields)

    for broken in (
        _Missing(bus=context.bus, clock=context.clock),
        _Missing(actions=context.actions, clock=context.clock),
        _Missing(actions=context.actions, bus=object(), clock=context.clock),
        _Missing(actions=context.actions, bus=context.bus, clock=None),
    ):
        with pytest.raises(UsersModuleError):
            await activate(broken, SETTINGS, {})
    assert USERS_READ_ACTION not in runtime_of(context).actions.discovered()


async def test_prepare_subscribes_and_binds_the_declared_spec_and_close_withdraws() -> None:
    """R5/R4: ``prepare`` subscribes to the chat event, registers the
    manifest's own contract over ``*/*/chat`` under the provider name and
    marks the module ready — once, idempotently; nothing is observed before
    it. ``close`` withdraws readiness, releases the directory and stops
    observing, so a later call is refused ``provider_not_ready`` with the
    provider entered 0 times and a later event is not recorded."""

    clock = ManualClock()
    context = module_context(clock)
    handle = await activate(context, SETTINGS, {})
    registry = runtime_of(context).actions
    bus = runtime_of(context).bus
    assert USERS_READ_ACTION not in registry.discovered()

    # Published before ``prepare``: not observed.
    await bus.publish(CHAT_EVENT, *chat_event("fake", "channel-9", {"id": "early"}))

    await handle.prepare()
    await handle.prepare()

    (binding,) = registry.bindings(USERS_READ_ACTION)
    assert binding.provider_name == USERS_READ_PROVIDER
    assert binding.module == MODULE_NAME
    assert binding.destination == Destination("*", "*", "chat")
    assert binding.spec == registry.discovered()[USERS_READ_ACTION]
    assert binding.spec.nature == "read"
    assert binding.spec.delivery is None
    assert USERS_READ_ACTION in registry.registered_ready()

    grant_users_read(context)
    await speak(context, "fake", "channel-9", ["a"])
    # Subscribed once: one event, one author, one observation.
    assert ids_of(await read(context, FAKE_CHAT, 10)) == ["a"]

    await handle.close()
    await handle.close()
    assert USERS_READ_ACTION not in registry.registered_ready()
    assert handle.directory.channels() == ()
    await speak(context, "fake", "channel-9", ["late"])
    assert handle.directory.channels() == ()
    observation = await read(context, FAKE_CHAT, 1, None, "call-closed")
    assert observation.status == "refused"
    assert observation.error is not None
    assert observation.error["code"] == "provider_not_ready"
    assert runtime_of(context).executor.provider_invocations == 1


async def test_loader_activates_the_manifest_and_the_coordinator_drives_prepare() -> None:
    """R4/R7: the real loader validates the manifest, runs the declared hook
    with the reserved ``limits`` block alongside the bounds, declares the
    action at discovery and activates through the context; the
    coordinator's ``prepare`` binds the provider to the very spec the loader
    declared and a granted read then serves a page fed by a bus publication;
    ``stop`` withdraws readiness. A profile missing a bound is refused by
    the hook, naming the field."""

    clock = ManualClock()
    context = runtime_context(clock=clock)
    loader = ModuleLoader(context.bus, ROOT / "modules", context=context, environ={})
    diagnostics: list[str] = []

    activations = await loader.activate_enabled(
        {
            "enabled_modules": [MODULE_NAME],
            "modules": {MODULE_NAME: {**SETTINGS, "limits": LIMITS}},
        }
    )
    (activation,) = activations
    assert activation.manifest_version == 2
    assert activation.roles == frozenset()
    assert type(activation.handle).__name__ == UsersModule.__name__
    assert USERS_READ_ACTION in context.actions.discovered()
    assert context.actions.bindings(USERS_READ_ACTION) == ()

    coordinator = PhaseCoordinator(activations, tasks=context.tasks, reporter=diagnostics.append)
    assert (await coordinator.start()).status == 0
    (binding,) = context.actions.bindings(USERS_READ_ACTION)
    assert binding.spec == context.actions.discovered()[USERS_READ_ACTION]
    assert USERS_READ_ACTION in context.actions.registered_ready()

    context.actions._authorization.grant(
        AuthorizationRule(
            rule_id="grant", action_name=USERS_READ_ACTION, granted_permissions=("users.read",)
        )
    )
    await context.bus.publish(CHAT_EVENT, *chat_event("fake", "channel-9", {"id": "ada"}))
    observation = await context.executor.invoke(users_read_call(FAKE_CHAT, {"limit": 5}))
    assert ids_of(observation) == ["ada"]
    assert observation.result["coverage"]["retained_users"] == 1
    assert observation.result["coverage"]["complete"] is False

    assert (await coordinator.stop()).status == 0
    assert diagnostics == []
    assert USERS_READ_ACTION not in context.actions.registered_ready()

    # The same profile without a bound: refused before activation.
    refusing = ModuleLoader(context.bus, ROOT / "modules", context=context, environ={})
    with pytest.raises(Exception) as refused:
        await refusing.activate_enabled(
            {
                "enabled_modules": [MODULE_NAME],
                "modules": {
                    MODULE_NAME: {"max_channels": 4, "max_users_per_channel": 100, "limits": LIMITS}
                },
            }
        )
    assert "max_age_seconds" in str(refused.value)
    assert "users" in str(refused.value)


def test_module_package_is_importable_by_its_shipped_name() -> None:
    """P6 packaging: the directory is a package the distribution can ship."""

    assert (MODULE_DIR / "__init__.py").is_file()
    assert sys.modules["modules.users"].__name__ == "modules.users"
