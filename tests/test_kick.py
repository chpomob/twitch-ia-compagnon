"""The Kick signed-webhook chat input (phase 3 R7, plan step P18).

AC34: a delivery signed with the test key and a timestamp within 300 s is
published once as a ``kick`` chat event and admitted per policy; a bad
signature, a 301 s-old timestamp, a 65537-byte body and a duplicate message
id are each refused, counted and yield 0 events; the listener runs on the
configured host and port. AC35 (this step's part): the badges
``broadcaster``, ``moderator``, ``vip`` and ``subscriber`` satisfy the
matching ``audience`` rules, and follows, subscriptions, renewals and gifts
map to ``follow``, ``sub``, ``resub`` and ``sub_gift``. AC32 (kick half): an
author identifier containing ``:`` yields 0 admissions and ``invalid`` + 1.

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
)
import modules.kick as kick_module
from core.context import ChatContext
from core.contracts import TriggerPolicy, TriggerRule
from core.loader import ModuleLoader
from core.runtime import RUNTIME_API, RuntimeContext
from core.triggers import TriggerRegistry
from modules.kick import (
    MAX_BODY_BYTES,
    PUBLIC_KEY_URL,
    KickModule,
    KickModuleError,
    activate,
    normalize_delivery,
    parse_public_key,
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
    """The injected transport: answers the public-key endpoint, counts calls."""

    def __init__(self, *answers: Any) -> None:
        self.answers = list(answers)
        self.get_calls: list[str] = []
        self.close_calls = 0

    async def get(self, url: str, **kwargs: Any) -> Any:
        self.get_calls.append(url)
        answer = self.answers.pop(0)
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
    assert "actions" not in manifest
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
