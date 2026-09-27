"""The Kick signed-webhook chat input (phase 3 R7, plan step P18).

AC34: a delivery signed with the test key and a timestamp within 300 s is
published once as a ``kick`` chat event and admitted per policy; a bad
signature, a 301 s-old timestamp, a 65537-byte body and a duplicate message
id are each refused, counted and yield 0 events; the listener runs on the
configured host and port. AC35 (plan step P18's part): the badges
``broadcaster``, ``moderator``, ``vip`` and ``subscriber`` satisfy the
matching ``audience`` rules, and follows, subscriptions, renewals and gifts
map to ``follow``, ``sub``, ``resub`` and ``sub_gift``. AC32 (kick half): an
author identifier containing ``:`` yields 0 admissions and ``invalid`` + 1.

AC35, the rest (plan step P19): ``chat.write`` of 500 characters sends 1
request, 501 is ``error text_too_long`` with 0; a 429 announcing 30 s is
``error rate_limited`` and a send 29 s later ``refused rate_limited`` with 0
requests; the moderation service sends a 90 s timeout as 2 minutes and
refuses 10081 minutes with 0 requests. AC39 (kick half): ``chat.write`` is
bound on ``kick/*/chat`` only, beside twitch's binding, and no clip or poll
service is published for kick.

Every delivery is signed by :class:`conftest.SignedWebhookSender` with the
test-only :data:`conftest.KICK_TEST_KEY` and posted through an in-process
``aiohttp`` test client; the wall clock is a :class:`conftest.ManualClock`
set on an epoch instant, so nothing sleeps and nothing leaves the process.
"""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiohttp
import pytest
import yaml
from aiohttp.test_utils import TestClient, TestServer

from conftest import (
    KICK_TEST_KEY,
    FakeResponse,
    ManualClock,
    RecordingScheduler,
    SignedWebhookSender,
    events_of,
    runtime_context,
    settle,
    trace_texts,
)
import modules.kick as kick_module
from core.actions import AuthorizationRule
from core.context import ChatContext
from core.contracts import ActionCall, ActionObservation, Destination, TriggerPolicy, TriggerRule
from core.loader import ModuleLoader
from core.runtime import RUNTIME_API, RuntimeContext
from core.triggers import TriggerRegistry
from modules.brain import _message_of_work
from modules.kick import (
    CHAT_URL,
    CHAT_WRITE_ACTION,
    DEFAULT_RATE_LIMIT_SECONDS,
    MAX_BODY_BYTES,
    MAX_RATE_LIMIT_SECONDS,
    MODERATION_BANS_URL,
    PUBLIC_KEY_URL,
    RETRY_SOURCE_DEFAULT,
    RETRY_SOURCE_RESET,
    RETRY_SOURCE_RETRY_AFTER,
    KickModule,
    KickModuleError,
    activate,
    normalize_delivery,
    parse_public_key,
    rate_limit_until,
    signed_content,
    validate_settings,
    verify_signature,
)

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "modules" / "kick" / "module.yaml"
NOW = 1_790_000_000.0
CHANNEL = "4242"
VIEWER = "777"
COMPANION = "Robo"
CHAT = "chat.message.sent"

BASE_SETTINGS: dict[str, Any] = {
    "client_secret": "kick-client-secret-value",
    "access_token": "kick-access-token-value",
    "listener": {"host": "127.0.0.1", "port": 0},
    "public_key": KICK_TEST_KEY.public_pem,
    "channels": [CHANNEL],
    "companion_name": COMPANION,
}


def chat_body(
    text: str,
    *,
    sender_id: Any = VIEWER,
    username: str = "viewer",
    badges: list[dict[str, Any]] | None = None,
    message_id: str = "chat-1",
    channel: Any = CHANNEL,
) -> dict[str, Any]:
    """A ``chat.message.sent`` body in the documented shape (ids as integers
    when numeric, as the platform sends them)."""

    def as_id(value: Any) -> Any:
        return int(value) if isinstance(value, str) and value.isdigit() else value

    return {
        "message_id": message_id,
        "broadcaster": {"is_anonymous": False, "user_id": as_id(channel), "username": "streamer"},
        "sender": {
            "is_anonymous": False,
            "user_id": as_id(sender_id),
            "username": username,
            "identity": {"username_color": "#FF5733", "badges": badges or []},
        },
        "content": text,
        "emotes": [],
        "created_at": "2026-09-23T12:00:00Z",
    }


def person(user_id: Any, username: str = "fan", **extra: Any) -> dict[str, Any]:
    return {"is_anonymous": False, "user_id": user_id, "username": username, **extra}


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


class PublicKeySession:
    """The injected transport: answers the public-key endpoint and, in order,
    the POSTs to the chat and bans endpoints; records every call."""

    def __init__(self, *answers: Any, posts: tuple[Any, ...] = ()) -> None:
        self.answers = list(answers)
        self.post_answers = list(posts)
        self.get_calls: list[str] = []
        self.posts: list[dict[str, Any]] = []
        self.close_calls = 0

    async def get(self, url: str, **kwargs: Any) -> Any:
        self.get_calls.append(url)
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer

    async def post(self, url: str, **kwargs: Any) -> Any:
        self.posts.append({"url": url, **kwargs})
        answer = self.post_answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer

    async def close(self) -> None:
        self.close_calls += 1


@dataclass
class Harness:
    runtime: RuntimeContext
    module: KickModule
    registry: TriggerRegistry
    scheduler: RecordingScheduler
    chat: ChatContext
    clock: ManualClock
    sender: SignedWebhookSender
    client: TestClient
    diagnostics: list[str]
    session: PublicKeySession

    def events(self) -> list[dict[str, Any]]:
        return events_of(self.runtime.bus, "channel.chat.message")

    async def post(self, event_type: str, payload: Any, **options: Any) -> int:
        return await self.sender.post(self.client, event_type, payload, **options)

    async def close(self) -> None:
        await self.client.close()
        await self.module.stop_inputs()
        await self.module.close()


async def start_harness(
    *, policy: TriggerPolicy | None = None, session: PublicKeySession | None = None, **overrides: Any
) -> Harness:
    """Activate ``kick`` through the real loader, prepare it, start its
    inputs (listener on loopback, port 0) and open a test client on an
    in-process server of the same handler."""

    clock = ManualClock(NOW)
    registry = TriggerRegistry()
    scheduler = RecordingScheduler()
    chat = ChatContext(max_messages=16, max_bytes=8192, max_age_seconds=600.0, clock=clock)
    runtime = runtime_context(
        clock=clock, trigger_registry=registry, scheduler=scheduler, chat=chat
    )
    diagnostics: list[str] = []
    transport = session if session is not None else PublicKeySession()
    settings = {
        **BASE_SETTINGS,
        **overrides,
        "_session_factory": lambda: transport,
        "_wall_clock": clock,
        "diagnostic_reporter": diagnostics.append,
    }
    settings = {key: value for key, value in settings.items() if value is not None}
    loader = ModuleLoader(runtime.bus, ROOT / "modules", context=runtime, environ={})
    (activation,) = await loader.activate_enabled(
        {"enabled_modules": ["kick"], "modules": {"kick": settings}}
    )
    module = activation.handle
    if policy is not None:
        registry.configure("kick", policy)
    await module.prepare()
    await module.start_inputs()
    client = TestClient(TestServer(module.build_application()))
    await client.start_server()
    return Harness(
        runtime,
        module,
        registry,
        scheduler,
        chat,
        clock,
        SignedWebhookSender(KICK_TEST_KEY, clock),
        client,
        diagnostics,
        transport,
    )


# -- the manifest and the validator --------------------------------------------- #


def test_manifest_declares_a_v2_input_with_the_chat_trigger_types() -> None:
    """R7: role ``input``, the four trigger types and the companion-mention
    default, the two credentials, and no action (``chat.write`` is P19's)."""

    manifest = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert manifest["name"] == "kick"
    assert manifest["manifest_version"] == 2
    assert manifest["runtime_api"] == RUNTIME_API
    assert manifest["produces"] == ["channel.chat.message"]
    assert manifest["lifecycle"] == {"roles": ["input"]}
    assert manifest["settings_validator"] == "validate_settings"
    assert manifest["credentials"] == ["client_secret", "access_token"]
    # R7 (plan step P19) supersedes P18's "no action": the manifest now
    # declares exactly `chat.write`, the same contract as twitch's but for
    # `kick/*/chat`, with the same delivery text mapping.
    [action] = manifest["actions"]
    twitch = yaml.safe_load((ROOT / "modules" / "twitch" / "module.yaml").read_text("utf-8"))
    [twitch_action] = twitch["actions"]
    assert action["name"] == CHAT_WRITE_ACTION
    assert action["supported_destinations"] == [
        {"platform": "kick", "channel_id": "*", "scope": "chat"}
    ]
    assert action["delivery"] == {"text_argument": "text"}
    assert {k: v for k, v in action.items() if k != "supported_destinations"} == {
        k: v for k, v in twitch_action.items() if k != "supported_destinations"
    }
    properties = manifest["settings_schema"]["properties"]
    assert {
        "listener", "public_key", "channels", "companion_name", "notices", "dedup",
        "client_secret", "access_token",
    } <= set(properties)
    assert set(properties["listener"]["properties"]) == {"host", "port"}
    assert "public_key" not in manifest["settings_schema"]["required"]
    assert [entry["name"] for entry in manifest["triggers"]["types"]] == [
        "probability", "audience", "keyword", "event_kind",
    ]
    assert manifest["triggers"]["default_policy"] == {
        "combination": "all_of",
        "rules": [{"type": "keyword", "parameters": {"keywords": ["${companion_name}"]}}],
    }


def test_validator_accepts_the_base_settings_and_names_each_bad_field() -> None:
    assert validate_settings(BASE_SETTINGS) == []
    assert validate_settings({**BASE_SETTINGS, "public_key": None}) == []
    cases = {
        "listener.port": {"listener": {"host": "127.0.0.1", "port": 70000}},
        "listener.host": {"listener": {"host": "", "port": 8080}},
        "public_key": {"public_key": "-----BEGIN PUBLIC KEY-----\nnot a key\n-----END PUBLIC KEY-----"},
        "channels": {"channels": []},
        "companion_name": {"companion_name": " "},
        "notices.kinds": {"notices": {"kinds": ["raid"]}},
        "dedup.window_seconds": {"dedup": {"window_seconds": 299}},
        "dedup.max_entries": {"dedup": {"max_entries": 0}},
        "access_token": {"access_token": 7},
    }
    for field_name, override in cases.items():
        diagnostics = validate_settings({**BASE_SETTINGS, **override})
        assert len(diagnostics) == 1, (field_name, diagnostics)
        assert f"field {field_name!r}" in diagnostics[0] and "'kick'" in diagnostics[0]
        assert BASE_SETTINGS["client_secret"] not in diagnostics[0]


# -- the verifier (plan decision 9) --------------------------------------------- #


def test_the_verifier_accepts_the_documented_signature_and_nothing_else() -> None:
    key = parse_public_key(KICK_TEST_KEY.public_pem)
    sender = SignedWebhookSender(KICK_TEST_KEY, ManualClock(NOW))
    content = signed_content("id-1", "2026-09-23T12:00:00Z", b'{"a":1}')
    good = sender.sign("id-1", "2026-09-23T12:00:00Z", b'{"a":1}')
    assert verify_signature(key, content, good)
    assert not verify_signature(key, content + b" ", good)
    assert not verify_signature(key, content, good[:-4])
    assert not verify_signature(key, content, "not base64 !")


def test_the_verifier_rejects_a_signature_at_or_above_the_modulus() -> None:
    """A signature value ``s + n`` recovers the same encoding as ``s`` (it is
    congruent modulo ``n``) and still fits the modulus length: only the
    ``s < n`` check refuses it."""

    key = parse_public_key(KICK_TEST_KEY.public_pem)
    sender = SignedWebhookSender(KICK_TEST_KEY, ManualClock(NOW))
    size = KICK_TEST_KEY.size_bytes
    for index in range(200):
        content = signed_content(f"id-{index}", "t", b"{}")
        value = int.from_bytes(base64.b64decode(sender.sign(f"id-{index}", "t", b"{}")), "big")
        if value + key.n < 1 << (8 * size):
            break
    else:  # pragma: no cover - probability ~ 0.66 per draw
        raise AssertionError("no small signature found")
    assert verify_signature(key, content, base64.b64encode(value.to_bytes(size, "big")).decode())
    lifted = base64.b64encode((value + key.n).to_bytes(size, "big")).decode()
    assert pow(value + key.n, key.e, key.n) == pow(value, key.e, key.n)
    assert not verify_signature(key, content, lifted)
    exactly_n = base64.b64encode(key.n.to_bytes(size, "big")).decode()
    assert not verify_signature(key, content, exactly_n)


def test_the_verifier_rejects_a_wrong_digest_info() -> None:
    """A correct SHA-256 digest under another DigestInfo (the SHA-512 OID's
    prefix, re-lengthed; and a one-byte change) is refused: the whole encoding
    is compared, not the trailing digest."""

    key = parse_public_key(KICK_TEST_KEY.public_pem)
    sender = SignedWebhookSender(KICK_TEST_KEY, ManualClock(NOW))
    content = signed_content("id", "t", b"{}")
    wrong_oid = bytes.fromhex("3031300d060960864801650304020305000420")
    altered = bytearray(bytes.fromhex("3031300d060960864801650304020105000420"))
    altered[-1] ^= 0x01
    for prefix in (wrong_oid, bytes(altered)):
        forged = sender.sign_encoded(sender.encode(content, digest_info_prefix=prefix))
        assert not verify_signature(key, content, forged)
    assert verify_signature(key, content, sender.sign_encoded(sender.encode(content)))


def test_the_public_key_parser_refuses_what_is_not_an_rsa_spki() -> None:
    with pytest.raises(ValueError):
        parse_public_key("-----BEGIN PUBLIC KEY-----\nAAAA\n-----END PUBLIC KEY-----")
    with pytest.raises(ValueError):
        parse_public_key(KICK_TEST_KEY.public_pem.replace("PUBLIC KEY", "CERTIFICATE"))


# -- AC34 ----------------------------------------------------------------------- #


async def test_ac34_a_valid_delivery_is_published_once_and_admitted_per_policy() -> None:
    harness = await start_harness()
    try:
        status = await harness.post(CHAT, chat_body(f"hello {COMPANION}", message_id="m-1"))
        assert status == 200
        (event,) = harness.events()
        payload = event["payload"]
        assert payload["platform"] == "kick" and payload["channel_id"] == CHANNEL
        assert payload["author"]["id"] == VIEWER and payload["message_id"] == "m-1"
        assert payload["text"] == f"hello {COMPANION}" and "kind" not in payload
        assert event["metadata"]["source"] == "kick"
        (admitted,) = harness.scheduler.admissions
        assert (admitted[0].platform, admitted[0].channel_id, admitted[0].viewer_id) == (
            "kick", CHANNEL, VIEWER,
        )
        assert [record.text for record in harness.chat.read("kick", CHANNEL, limit=8)] == [
            f"hello {COMPANION}"
        ]

        # Default policy: no mention, published and fed but not admitted.
        assert await harness.post(CHAT, chat_body("just chatting", message_id="m-2")) == 200
        assert len(harness.events()) == 2
        assert len(harness.scheduler.admissions) == 1
        assert all(count == 0 for count in harness.module.counts.values())
    finally:
        await harness.close()


@pytest.mark.parametrize(
    "refusal",
    ["bad_signature", "stale_timestamp", "too_large", "duplicate"],
)
async def test_ac34_each_refusal_is_answered_4xx_counted_and_publishes_nothing(
    refusal: str,
) -> None:
    harness = await start_harness()
    try:
        body = chat_body(f"hi {COMPANION}")
        options: dict[str, Any] = {}
        if refusal == "bad_signature":
            other = SignedWebhookSender(KICK_TEST_KEY, harness.clock)
            options["signature"] = other.sign("another-id", other.timestamp(), b"{}")
        elif refusal == "stale_timestamp":
            options["timestamp"] = harness.sender.timestamp(NOW - 301)
        elif refusal == "too_large":
            encoded = SignedWebhookSender.body(body)
            body = encoded[:-1] + b" " * (MAX_BODY_BYTES + 1 - len(encoded)) + b"}"
            assert len(body) == MAX_BODY_BYTES + 1
        elif refusal == "duplicate":
            assert await harness.post(CHAT, body, message_id="same-id") == 200
            assert len(harness.events()) == 1
            body = chat_body(f"hi again {COMPANION}", message_id="chat-2")
            options["message_id"] = "same-id"
        before_events = len(harness.events())
        before_admissions = len(harness.scheduler.admissions)

        status = await harness.post(CHAT, body, **options)

        assert 400 <= status < 500
        assert harness.module.counts[refusal] == 1
        assert sum(harness.module.counts.values()) == 1
        assert len(harness.events()) == before_events
        assert len(harness.scheduler.admissions) == before_admissions
        assert any(refusal in line for line in harness.diagnostics)
    finally:
        await harness.close()


async def test_ac34_the_timestamp_bound_is_inclusive_at_300_seconds_either_way() -> None:
    harness = await start_harness()
    try:
        for index, offset in enumerate((-300, 300)):
            stamp = harness.sender.timestamp(NOW + offset)
            body = chat_body(f"{COMPANION}?", message_id=f"edge-{index}")
            assert await harness.post(CHAT, body, timestamp=stamp) == 200
        assert await harness.post(
            CHAT, chat_body("late", message_id="future"), timestamp=harness.sender.timestamp(NOW + 301)
        ) == 400
        assert len(harness.events()) == 2
        assert harness.module.counts["stale_timestamp"] == 1
    finally:
        await harness.close()


async def test_ac34_a_65536_byte_body_is_still_accepted() -> None:
    harness = await start_harness()
    try:
        encoded = SignedWebhookSender.body(chat_body(f"{COMPANION}!"))
        body = encoded[:-1] + b" " * (MAX_BODY_BYTES - len(encoded)) + b"}"
        assert len(body) == MAX_BODY_BYTES
        assert await harness.post(CHAT, body) == 200
        assert len(harness.events()) == 1
    finally:
        await harness.close()


async def test_ac34_the_listener_runs_on_the_configured_host_and_port() -> None:
    """Loopback and port 0: the bound port is resolved, a signed delivery
    posted to it over a real socket is published, and ``stop_inputs``
    unbinds it."""

    harness = await start_harness()
    try:
        address = harness.module.address
        assert address is not None
        host, port = address
        assert host == "127.0.0.1" and port > 0
        async with aiohttp.ClientSession() as http:
            status = await harness.sender.post(
                http, CHAT, chat_body(f"over the wire {COMPANION}"),
                path=f"http://{host}:{port}/webhooks/kick",
            )
        assert status == 200
        assert len(harness.events()) == 1 and len(harness.scheduler.admissions) == 1

        await harness.module.stop_inputs()
        assert harness.module.address is None
        async with aiohttp.ClientSession() as http:
            with pytest.raises(aiohttp.ClientConnectionError):
                await harness.sender.post(
                    http, CHAT, chat_body("after stop"), path=f"http://{host}:{port}/"
                )
    finally:
        await harness.close()


async def test_a_delivery_before_start_inputs_is_not_accepted() -> None:
    clock = ManualClock(NOW)
    runtime = runtime_context(clock=clock, trigger_registry=TriggerRegistry())
    module = await activate(
        runtime.for_module("kick"),
        {**BASE_SETTINGS, "_session_factory": PublicKeySession, "_wall_clock": clock},
        {},
    )
    await module.prepare()
    async with TestClient(TestServer(module.build_application())) as client:
        sender = SignedWebhookSender(KICK_TEST_KEY, clock)
        assert await sender.post(client, CHAT, chat_body(COMPANION)) == 503
    assert events_of(runtime.bus, "channel.chat.message") == []
    await module.close()


# -- the public key: configured, or fetched once at prepare --------------------- #


async def test_the_public_key_is_fetched_once_at_prepare_when_not_configured() -> None:
    session = PublicKeySession(
        FakeResponse(200, {"data": {"public_key": KICK_TEST_KEY.public_pem}, "message": "OK"})
    )
    harness = await start_harness(session=session, public_key=None)
    try:
        assert session.get_calls == [PUBLIC_KEY_URL]
        await harness.module.prepare()
        assert await harness.post(CHAT, chat_body(COMPANION)) == 200
        assert await harness.post(CHAT, chat_body(COMPANION, message_id="c-2")) == 200
        assert session.get_calls == [PUBLIC_KEY_URL]
        assert len(harness.events()) == 2
    finally:
        await harness.close()
    assert session.close_calls == 1


class StalledBodyResponse(FakeResponse):
    """Headers arrive at once; the body never does."""

    async def json(self) -> Any:
        await asyncio.Event().wait()


async def test_a_stalled_public_key_body_fails_prepare_within_the_fetch_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(kick_module, "PUBLIC_KEY_FETCH_TIMEOUT_SECONDS", 0.05)
    clock = ManualClock(NOW)
    runtime = runtime_context(clock=clock, trigger_registry=TriggerRegistry())
    diagnostics: list[str] = []
    response = StalledBodyResponse(200, {})
    settings = {key: value for key, value in BASE_SETTINGS.items() if key != "public_key"}
    module = await activate(
        runtime.for_module("kick"),
        {
            **settings,
            "_session_factory": lambda: PublicKeySession(response),
            "_wall_clock": clock,
            "diagnostic_reporter": diagnostics.append,
        },
        {},
    )
    with pytest.raises(KickModuleError):
        await asyncio.wait_for(module.prepare(), 5)
    assert response.release_calls == 1
    assert diagnostics
    await module.close()


@pytest.mark.parametrize(
    "answer",
    [FakeResponse(500, {}), FakeResponse(200, {"data": {"public_key": "nope"}}), OSError("down")],
)
async def test_an_unusable_public_key_answer_fails_prepare(answer: Any) -> None:
    clock = ManualClock(NOW)
    runtime = runtime_context(clock=clock, trigger_registry=TriggerRegistry())
    diagnostics: list[str] = []
    settings = {key: value for key, value in BASE_SETTINGS.items() if key != "public_key"}
    module = await activate(
        runtime.for_module("kick"),
        {
            **settings,
            "_session_factory": lambda: PublicKeySession(answer),
            "_wall_clock": clock,
            "diagnostic_reporter": diagnostics.append,
        },
        {},
    )
    with pytest.raises(KickModuleError):
        await module.prepare()
    with pytest.raises(KickModuleError):
        await module.start_inputs()
    await module.close()
    assert diagnostics and all(BASE_SETTINGS["access_token"] not in line for line in diagnostics)


# -- AC35 (this step's part): badges and event-kind mapping ---------------------- #


@pytest.mark.parametrize(
    ("badge", "audience"),
    [
        ("broadcaster", "broadcaster"),
        ("moderator", "moderators"),
        ("vip", "vips"),
        ("subscriber", "subscribers"),
    ],
)
async def test_ac35_each_badge_satisfies_its_audience_rule(badge: str, audience: str) -> None:
    harness = await start_harness(policy=audience_policy(audience))
    try:
        assert await harness.post(
            CHAT, chat_body("with badge", badges=[{"text": badge.title(), "type": badge}],
                            message_id="with")
        ) == 200
        assert await harness.post(
            CHAT, chat_body("plain", badges=[{"text": "OG", "type": "og"}], message_id="without")
        ) == 200
        # Claiming the role in the text is not a badge.
        assert await harness.post(CHAT, chat_body(f"I am {badge}", message_id="claimed")) == 200
        assert len(harness.events()) == 3
        assert [work.source_event_id for _key, work in harness.scheduler.admissions] == ["with"]
        with_badge = harness.events()[0]["payload"]["author"]
        assert with_badge["roles"] == ["subscription" if badge == "subscriber" else badge]
        assert with_badge["roles_provenance"] == "kick.badges"
    finally:
        await harness.close()


async def test_ac35_the_channel_account_is_the_broadcaster_by_identity() -> None:
    harness = await start_harness(policy=audience_policy("broadcaster"))
    try:
        assert await harness.post(CHAT, chat_body("from the streamer", sender_id=CHANNEL)) == 200
        assert len(harness.scheduler.admissions) == 1
    finally:
        await harness.close()


NOTICE_CASES = [
    ("channel.followed", "follow", {"follower": person(901, "follower")}),
    (
        "channel.subscription.new",
        "sub",
        {"subscriber": person(902, "newsub"), "duration": 1,
         "created_at": "2026-09-23T12:00:00Z", "expires_at": "2026-10-23T12:00:00Z"},
    ),
    (
        "channel.subscription.renewal",
        "resub",
        {"subscriber": person(903, "oldsub"), "duration": 3,
         "created_at": "2026-09-23T12:00:00Z", "expires_at": "2026-10-23T12:00:00Z"},
    ),
    (
        "channel.subscription.gifts",
        "sub_gift",
        {"gifter": person(904, "gifter"), "giftees": [person(905, "lucky")],
         "created_at": "2026-09-23T12:00:00Z", "expires_at": "2026-10-23T12:00:00Z"},
    ),
]


@pytest.mark.parametrize(("event_type", "kind", "fields"), NOTICE_CASES)
async def test_ac35_each_notice_type_maps_to_its_kind_and_is_admitted_by_kind(
    event_type: str, kind: str, fields: dict[str, Any]
) -> None:
    harness = await start_harness(
        policy=event_kind_policy(kind),
        notices={"kinds": ["follow", "sub", "resub", "sub_gift"]},
    )
    try:
        body = {"broadcaster": person(int(CHANNEL), "streamer"), **fields}
        assert await harness.post(event_type, body, message_id=f"notice-{kind}") == 200
        (event,) = harness.events()
        payload = event["payload"]
        assert payload["kind"] == kind and payload["platform"] == "kick"
        assert payload["message_id"] == f"notice-{kind}"
        author = next(value for value in fields.values() if isinstance(value, dict))
        assert payload["author"]["id"] == str(author["user_id"])
        assert len(harness.scheduler.admissions) == 1
        # The empty-text notice reaches the brain as its kind (gate 1, F1).
        ((_key, work),) = harness.scheduler.admissions
        message = _message_of_work(work)
        assert (message.kind, message.text) == (kind, "")
    finally:
        await harness.close()


async def test_an_unlisted_notice_and_an_unmapped_type_are_ignored_and_counted() -> None:
    harness = await start_harness(notices={"kinds": ["sub"]})
    try:
        body = {"broadcaster": person(int(CHANNEL)), "follower": person(901)}
        assert await harness.post("channel.followed", body) == 200
        assert await harness.post("livestream.status.updated", {"broadcaster": person(1)}) == 200
        assert harness.events() == []
        assert harness.module.counts["notices_ignored"] == 2
    finally:
        await harness.close()


async def test_an_anonymous_gift_is_published_but_never_admitted() -> None:
    harness = await start_harness(
        policy=event_kind_policy("sub_gift"), notices={"kinds": ["sub_gift"]}
    )
    try:
        body = {
            "broadcaster": person(int(CHANNEL)),
            "gifter": {"is_anonymous": True, "user_id": None, "username": None},
            "giftees": [person(905)],
        }
        assert await harness.post("channel.subscription.gifts", body) == 200
        (event,) = harness.events()
        assert event["payload"]["author"]["id"] == "system:anonymous"
        assert harness.scheduler.admissions == []
    finally:
        await harness.close()


def test_the_mapping_covers_exactly_the_five_documented_types() -> None:
    kinds = frozenset({"follow", "sub", "resub", "sub_gift"})
    broadcaster = {"broadcaster": person(1)}
    assert normalize_delivery(CHAT, chat_body("x", channel="1"), "d", kinds).kind == "message"
    for event_type, kind, fields in NOTICE_CASES:
        notice = normalize_delivery(event_type, {**broadcaster, **fields}, "d", kinds)
        assert notice is not None and notice.kind == kind
    assert normalize_delivery("channel.subscription.new", {**broadcaster}, "d", frozenset()) is None


# -- AC32 (kick half), anti-echo and channels ------------------------------------ #


async def test_ac32_an_author_id_with_a_colon_is_refused_and_counted() -> None:
    harness = await start_harness(policy=audience_policy("everyone"))
    try:
        body = chat_body(f"hi {COMPANION}", sender_id="system:watch", message_id="colon")
        assert await harness.post(CHAT, body) == 200
        assert harness.scheduler.admissions == []
        assert harness.events() == []
        assert harness.chat.read("kick", CHANNEL, limit=8) == ()
        assert harness.module.counts["invalid"] == 1
        # A clean author afterwards is admitted: the refusal is per delivery.
        assert await harness.post(CHAT, chat_body(f"hi {COMPANION}", message_id="clean")) == 200
        assert len(harness.scheduler.admissions) == 1
        assert harness.module.counts["invalid"] == 1
    finally:
        await harness.close()


async def test_the_companion_own_messages_are_dropped_before_anything() -> None:
    harness = await start_harness(policy=audience_policy("everyone"), bot_user_id="555")
    try:
        assert await harness.post(CHAT, chat_body("by id", sender_id="555", message_id="a")) == 200
        assert await harness.post(
            CHAT, chat_body("by name", username=COMPANION.lower(), message_id="b")
        ) == 200
        assert harness.events() == [] and harness.scheduler.admissions == []
        assert harness.chat.read("kick", CHANNEL, limit=8) == ()
    finally:
        await harness.close()


async def test_a_delivery_for_an_unconfigured_channel_is_ignored_and_counted() -> None:
    harness = await start_harness(policy=audience_policy("everyone"))
    try:
        assert await harness.post(CHAT, chat_body("elsewhere", channel="9999")) == 200
        assert harness.events() == [] and harness.scheduler.admissions == []
        assert harness.module.counts["channel_ignored"] == 1
    finally:
        await harness.close()


async def test_the_dedup_window_is_bounded_by_its_settings() -> None:
    harness = await start_harness(dedup={"max_entries": 2, "window_seconds": 300})
    try:
        for index in range(3):
            assert await harness.post(
                CHAT, chat_body("x", message_id=f"c-{index}"), message_id=f"d-{index}"
            ) == 200
        assert harness.module.dedup_size == 2
        harness.clock.advance(301)
        assert await harness.post(CHAT, chat_body("y", message_id="c-9"), message_id="d-9") == 200
        assert harness.module.dedup_size == 1
    finally:
        await harness.close()


# -- AC35 (rest): chat.write, the rate limit and the moderation service ----- #


class AnswerResponse(FakeResponse):
    """A scripted API answer with headers."""

    def __init__(self, status: int, body: Any = None, headers: dict[str, str] | None = None) -> None:
        super().__init__(status, body if body is not None else {})
        self.headers = headers or {}


def sent(message_id: str = "kick-msg-1") -> AnswerResponse:
    return AnswerResponse(200, {"data": {"is_sent": True, "message_id": message_id}, "message": "OK"})


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
        destination=Destination("kick", channel, "chat"),
        principal="brain",
        deadline=h.clock.now + 100.0,
    )
    return await h.runtime.executor.invoke(call)


def chat_posts(session: PublicKeySession) -> list[dict[str, Any]]:
    return [post for post in session.posts if post["url"] == CHAT_URL]


def completed_traces(h: Harness) -> list[dict[str, Any]]:
    return [event["payload"] for event in events_of(h.runtime.bus, "action.completed")]


@pytest.mark.asyncio
async def test_chat_write_is_bound_on_kick_chat_and_ready_after_prepare() -> None:
    """R7: ``prepare`` binds the declared ``chat.write`` over
    ``kick/*/chat`` (one binding) and marks the module ready."""

    h = await start_harness()
    try:
        [binding] = h.runtime.actions.bindings(CHAT_WRITE_ACTION)
        assert binding.destination == Destination("kick", "*", "chat")
        assert binding.module == "kick"
        assert CHAT_WRITE_ACTION in h.runtime.actions.registered_ready()
    finally:
        await h.close()
    assert CHAT_WRITE_ACTION not in h.runtime.actions.registered_ready()


class TwitchValidationSession:
    """Twitch's injected transport, answering only the token validation
    ``prepare`` performs; nothing here starts its inputs."""

    def __init__(self) -> None:
        self.get_calls: list[str] = []

    async def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.get_calls.append(url)
        return FakeResponse(200, {"client_id": "twitch-client", "user_id": "bot-24"})

    async def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_twitch_and_kick_load_and_prepare_together_on_one_runtime() -> None:
    """R5/R7: both platforms declare ``chat.write`` with one contract over
    their own destinations; loading them together records one action
    supporting both, and each ``prepare`` binds only its own platform."""

    runtime = runtime_context(clock=ManualClock(NOW), trigger_registry=TriggerRegistry())
    twitch_session = TwitchValidationSession()
    loader = ModuleLoader(runtime.bus, ROOT / "modules", context=runtime, environ={})
    activations = await loader.activate_enabled(
        {
            "enabled_modules": ["twitch", "kick"],
            "modules": {
                "twitch": {
                    "client_id": "twitch-client",
                    "client_secret": "twitch-client-secret-value",
                    "access_token": "twitch-access-token-value",
                    "broadcaster_id": "broadcaster-42",
                    "bot_user_id": "bot-24",
                    "companion_name": COMPANION,
                    "_session_factory": lambda: twitch_session,
                },
                "kick": {**BASE_SETTINGS, "_session_factory": PublicKeySession},
            },
        }
    )
    twitch, kick = (activation.handle for activation in activations)
    try:
        spec = runtime.actions.discovered()[CHAT_WRITE_ACTION]
        assert spec.supported_destinations == (
            Destination("twitch", "*", "chat"),
            Destination("kick", "*", "chat"),
        )
        await twitch.prepare()
        await kick.prepare()
        assert [
            (binding.module, binding.destination)
            for binding in runtime.actions.bindings(CHAT_WRITE_ACTION)
        ] == [
            ("twitch", Destination("twitch", "broadcaster-42", "chat")),
            ("kick", Destination("kick", "*", "chat")),
        ]
        assert CHAT_WRITE_ACTION in runtime.actions.registered_ready()
    finally:
        await kick.close()
        await twitch.close()


@pytest.mark.asyncio
async def test_ac35_500_characters_send_one_request_and_501_send_none() -> None:
    """AC35: a 500-character text is 1 POST to the chat endpoint, confirmed
    by the platform's acknowledgement; 501 characters is ``error
    text_too_long`` with 0 requests."""

    session = PublicKeySession(posts=(sent("kick-msg-1"),))
    h = await start_harness(session=session)
    try:
        grant_chat_write(h.runtime)
        observation = await write(h, "a" * 500, parent_message_id="parent-9")
        assert observation.status == "success", observation
        assert observation.result == {
            "message_id": "kick-msg-1",
            "destination": {"platform": "kick", "channel_id": CHANNEL},
        }
        [post] = chat_posts(session)
        assert post["json"] == {
            "broadcaster_user_id": int(CHANNEL),
            "content": "a" * 500,
            "type": "user",
            "reply_to_message_id": "parent-9",
        }
        assert post["headers"]["Authorization"] == "Bearer kick-access-token-value"

        refused = await write(h, "a" * 501)
        assert refused.status == "error"
        assert refused.error["code"] == "text_too_long"
        assert len(session.posts) == 1
        # The credential reaches the request header only, never a trace.
        for text in trace_texts(h.runtime.bus) + h.diagnostics:
            assert "kick-access-token-value" not in text
    finally:
        await h.close()


@pytest.mark.asyncio
async def test_ac35_a_429_blocks_sends_until_the_announced_retry_instant() -> None:
    """AC35: a 429 with ``Retry-After: 30`` is ``error rate_limited``; a send
    29 s later is ``refused rate_limited`` with 0 requests; at 30 s the next
    send leaves. The wait and its source are traced on ``action.completed``."""

    session = PublicKeySession(
        posts=(AnswerResponse(429, {"message": "Too Many Requests"}, {"Retry-After": "30"}), sent())
    )
    h = await start_harness(session=session)
    try:
        grant_chat_write(h.runtime)
        limited = await write(h, "hello")
        assert limited.status == "error" and limited.error["code"] == "rate_limited"
        assert len(chat_posts(session)) == 1
        assert h.module.sends_blocked_until == NOW + 30.0
        assert completed_traces(h)[-1]["retry_after_seconds"] == 30.0
        assert completed_traces(h)[-1]["retry_source"] == RETRY_SOURCE_RETRY_AFTER

        h.clock.advance(29.0)
        blocked = await write(h, "hello again")
        assert blocked.status == "refused" and blocked.error["code"] == "rate_limited"
        assert len(chat_posts(session)) == 1

        h.clock.advance(1.0)
        assert (await write(h, "hello at last")).status == "success"
        assert len(chat_posts(session)) == 2
    finally:
        await h.close()


@pytest.mark.asyncio
async def test_an_unreadable_429_blocks_for_the_bounded_default_and_is_traced() -> None:
    """Risk (P19): a 429 whose retry instant cannot be read blocks sends for
    :data:`DEFAULT_RATE_LIMIT_SECONDS`, traced with source ``default`` and
    diagnosed, value-free."""

    session = PublicKeySession(
        posts=(AnswerResponse(429, None, {"Retry-After": "soon, maybe"}), sent())
    )
    h = await start_harness(session=session)
    try:
        grant_chat_write(h.runtime)
        limited = await write(h, "hello")
        assert limited.error["code"] == "rate_limited"
        trace = completed_traces(h)[-1]
        assert trace["retry_source"] == RETRY_SOURCE_DEFAULT
        assert trace["retry_after_seconds"] == DEFAULT_RATE_LIMIT_SECONDS
        assert any("rate-limit answer unreadable" in line for line in h.diagnostics)
        h.clock.advance(DEFAULT_RATE_LIMIT_SECONDS - 1)
        assert (await write(h, "still blocked")).status == "refused"
        h.clock.advance(1)
        assert (await write(h, "free")).status == "success"
        assert len(chat_posts(session)) == 2
    finally:
        await h.close()


@pytest.mark.parametrize(
    ("headers", "delay", "source"),
    [
        ({"Retry-After": "30"}, 30.0, RETRY_SOURCE_RETRY_AFTER),
        ({"retry-after": "2.5"}, 2.5, RETRY_SOURCE_RETRY_AFTER),
        ({"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}, None, RETRY_SOURCE_RETRY_AFTER),
        ({"X-RateLimit-Reset": str(int(NOW) + 45)}, 45.0, RETRY_SOURCE_RESET),
        ({"X-RateLimit-Reset": str((int(NOW) + 12) * 1000)}, 12.0, RETRY_SOURCE_RESET),
        ({"RateLimit-Reset": "20"}, 20.0, RETRY_SOURCE_RESET),
        ({"X-RateLimit-Reset": "2026-09-23T12:00:00+00:00"}, None, RETRY_SOURCE_RESET),
        ({"Retry-After": "86400000"}, MAX_RATE_LIMIT_SECONDS, RETRY_SOURCE_RETRY_AFTER),
        ({"X-RateLimit-Reset": str(int(NOW) - 100)}, 0.0, RETRY_SOURCE_RESET),
        ({"Retry-After": "-5"}, DEFAULT_RATE_LIMIT_SECONDS, RETRY_SOURCE_DEFAULT),
        ({"Retry-After": "nan"}, DEFAULT_RATE_LIMIT_SECONDS, RETRY_SOURCE_DEFAULT),
        ({}, DEFAULT_RATE_LIMIT_SECONDS, RETRY_SOURCE_DEFAULT),
        (None, DEFAULT_RATE_LIMIT_SECONDS, RETRY_SOURCE_DEFAULT),
    ],
)
def test_the_retry_instant_is_read_defensively(headers: Any, delay: float | None, source: str) -> None:
    """Risk (P19): ``Retry-After`` in seconds or as a date, a reset header as
    an epoch instant (seconds or milliseconds), a delay or a time; clamped to
    ``0..MAX_RATE_LIMIT_SECONDS``; anything unreadable is the default."""

    until, found = rate_limit_until(headers, NOW)
    assert found == source
    wait = until - NOW
    assert 0.0 <= wait <= MAX_RATE_LIMIT_SECONDS
    if delay is not None:
        assert wait == pytest.approx(delay)


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", [AnswerResponse(503), AnswerResponse(502), ConnectionResetError()])
async def test_a_lost_or_5xx_send_is_external_unknown_and_never_retried(answer: Any) -> None:
    """R7: a lost answer or a 5xx is ``external_unknown`` after exactly one
    request; nothing is retried and no block is set."""

    session = PublicKeySession(posts=(answer,))
    h = await start_harness(session=session)
    try:
        grant_chat_write(h.runtime)
        observation = await write(h, "hello")
        assert observation.status == "external_unknown", observation
        assert len(chat_posts(session)) == 1
        await settle()
        assert len(chat_posts(session)) == 1
        assert h.module.sends_blocked_until is None
    finally:
        await h.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("answer", "status", "code"),
    [
        (AnswerResponse(403, {"message": "Forbidden"}), "error", "platform_rejected"),
        (AnswerResponse(200, {"data": {"is_sent": False}}), "error", "platform_rejected"),
        (AnswerResponse(200, {"data": {"is_sent": True}}), "external_unknown", "external_effect_unknown"),
    ],
)
async def test_each_other_answer_is_classified_after_one_request(
    answer: Any, status: str, code: str
) -> None:
    session = PublicKeySession(posts=(answer,))
    h = await start_harness(session=session)
    try:
        grant_chat_write(h.runtime)
        observation = await write(h, "hello")
        assert observation.status == status, observation
        if status == "error":
            assert observation.error["code"] == code
        assert len(chat_posts(session)) == 1
    finally:
        await h.close()


@pytest.mark.asyncio
async def test_a_channel_outside_the_configured_ones_is_refused_with_no_request() -> None:
    session = PublicKeySession()
    h = await start_harness(session=session)
    try:
        grant_chat_write(h.runtime)
        observation = await write(h, "hello", channel="9999")
        assert observation.status == "error"
        assert observation.error["code"] == "unsupported_destination"
        assert session.posts == []
    finally:
        await h.close()


@pytest.mark.asyncio
async def test_the_moderation_service_is_timeout_only_and_no_clip_or_poll_is_published() -> None:
    """R2, R5, AC39 (kick half): with a service registry, activation publishes
    ``(moderation, kick)`` offering ``{timeout}`` and nothing else — no clip,
    no poll service."""

    h = await start_harness()
    try:
        kinds = {key for key in h.runtime.services.entries() if key[1] == "kick"}
        assert kinds == {("moderation", "kick")}
        service = h.runtime.services.resolve("moderation", "kick")
        assert service.operations == frozenset({"timeout"})
    finally:
        await h.close()


@pytest.mark.asyncio
async def test_a_context_without_a_registry_publishes_no_service() -> None:
    import dataclasses

    runtime = dataclasses.replace(
        runtime_context(trigger_registry=TriggerRegistry()), services=None
    )
    settings = {**BASE_SETTINGS, "_session_factory": PublicKeySession}
    module = await activate(runtime.for_module("kick"), settings, {})
    try:
        assert module.moderation_service.operations == frozenset({"timeout"})
    finally:
        await module.close()


@pytest.mark.asyncio
async def test_ac35_a_90_second_timeout_is_sent_as_2_minutes() -> None:
    """AC35: the duration is rounded **up** to whole minutes; one POST to
    the bans endpoint, always with a ``duration``."""

    session = PublicKeySession(posts=(AnswerResponse(200, {"data": {}, "message": "OK"}),))
    h = await start_harness(session=session)
    try:
        service = h.module.moderation_service
        assert service.round_duration(90) == 120
        assert service.round_duration(60) == 60
        assert service.round_duration(61) == 120
        answer = await service.apply(
            "timeout", channel_id=CHANNEL, target_author_id=VIEWER,
            duration_seconds=90, reason="spam",
        )
        assert answer.outcome == "ok"
        [post] = session.posts
        assert post["url"] == MODERATION_BANS_URL
        assert post["json"] == {
            "broadcaster_user_id": int(CHANNEL),
            "user_id": int(VIEWER),
            "duration": 2,
            "reason": "spam",
        }
    finally:
        await h.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation", "arguments"),
    [
        ("timeout", {"duration_seconds": 10081 * 60}),
        ("timeout", {"duration_seconds": 10080 * 60 + 1}),
        ("timeout", {"duration_seconds": 0}),
        ("timeout", {"duration_seconds": None}),
        ("timeout", {"duration_seconds": 60, "target_author_id": ""}),
        ("delete_message", {"message_id": "m-1"}),
    ],
)
async def test_ac35_the_service_refuses_locally_with_no_request(
    operation: str, arguments: dict[str, Any]
) -> None:
    """AC35: 10081 minutes (or anything rounding past 10080) is refused by
    the service with 0 requests; so is a timeout without a duration (a
    permanent ban) or a target, and any operation but ``timeout``."""

    session = PublicKeySession()
    h = await start_harness(session=session)
    try:
        answer = await h.module.moderation_service.apply(
            operation, channel_id=CHANNEL, **{"target_author_id": VIEWER, **arguments}
        )
        assert answer.outcome == "rejected"
        assert session.posts == []
    finally:
        await h.close()


@pytest.mark.asyncio
async def test_the_largest_timeout_is_10080_minutes_and_is_sent() -> None:
    session = PublicKeySession(posts=(AnswerResponse(200, {}),))
    h = await start_harness(session=session)
    try:
        answer = await h.module.moderation_service.apply(
            "timeout", channel_id=CHANNEL, target_author_id=VIEWER, duration_seconds=10080 * 60
        )
        assert answer.outcome == "ok"
        assert session.posts[0]["json"]["duration"] == 10080
    finally:
        await h.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("answer", "outcome"),
    [
        (AnswerResponse(429), "rate"),
        (AnswerResponse(403), "rejected"),
        (AnswerResponse(500), "uncertain"),
        (ConnectionResetError(), "uncertain"),
    ],
)
async def test_each_ban_answer_is_classified_after_one_request(answer: Any, outcome: str) -> None:
    session = PublicKeySession(posts=(answer,))
    h = await start_harness(session=session)
    try:
        result = await h.module.moderation_service.apply(
            "timeout", channel_id=CHANNEL, target_author_id=VIEWER, duration_seconds=60
        )
        assert result.outcome == outcome
        assert len(session.posts) == 1
    finally:
        await h.close()
