"""Kick signed-webhook chat input on the v2 runtime (phase 3 R7).

The manifest beside this package declares ``manifest_version: 2``, so the
loader hands :func:`activate` a scoped runtime context, and the handle it
returns is driven by the phase coordinator through the hooks of the ``input``
role it declares (R4):

``validate_settings``  The module-owned hook the manifest names: one
                       value-free diagnostic per offending field (R7).
``activate``           Parses the settings and creates the HTTP session. It
                       opens no listener and produces no input.
``prepare``            Reads the platform public key: the configured PEM, or
                       one request to the platform's public-key endpoint
                       through the injected transport. Still no input.
``start_inputs``       After the readiness barrier: binds the ``aiohttp.web``
                       listener on ``listener.host``/``listener.port``.
``stop_inputs``        Unbinds the listener; accepted publications keep
                       running.
``drain``              Lets accepted publications finish inside the budget
                       and cancels the rest.
``close``              Releases the listener and the session exactly once.

**Accepting a delivery** (R7). The platform posts each event to the listener.
A delivery is accepted only if all of these hold, checked in this order:

1. its body is at most :data:`MAX_BODY_BYTES` bytes, read with a bounded
   reader so a larger body is never buffered whole;
2. ``Kick-Event-Signature`` verifies over ``message id + "." + timestamp +
   "." + raw body`` against the platform key (RSASSA-PKCS1-v1_5/SHA-256,
   standard library only — plan decision 9);
3. ``Kick-Event-Message-Timestamp`` is within :data:`TIMESTAMP_TOLERANCE_SECONDS`
   of the injected wall clock;
4. ``Kick-Event-Message-Id`` is not in the bounded dedup window.

Anything else is answered with a 4xx status and counted per reason in
:attr:`KickModule.counts` (``too_large``, ``malformed``, ``bad_signature``,
``stale_timestamp``, ``duplicate``); a refused delivery is never published.

**Reception** (R1, R6, R7). An accepted delivery is normalised once into the
schema-version-2 chat event under ``metadata.source = "kick"``:
``chat.message.sent`` is a ``message``; ``channel.followed``,
``channel.subscription.new``, ``channel.subscription.renewal`` and
``channel.subscription.gifts`` are the notices ``follow``, ``sub``, ``resub``
and ``sub_gift``, ingested only when ``notices.kinds`` lists them (an
unlisted or unmapped type is counted in ``notices_ignored``). The sender's
badges ``broadcaster``, ``moderator``, ``vip`` and ``subscriber`` are its
trusted roles, and the channel's own account is its broadcaster. Then, as the
twitch input does: an author identifier containing ``:`` is refused and
counted in ``invalid``; the companion's own messages are dropped (anti-echo);
the chat context is fed; the trigger engine decides; the event is published;
the decision is traced; an accepted event is admitted to the scheduler — and
the handler returns without awaiting any run. An anonymous gift is fed and
published under :data:`ANONYMOUS_AUTHOR` and never decided or admitted.

The listener must be reachable by the platform over public HTTPS; that is a
deployment prerequisite this module documents and does not provide.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import hmac
import inspect
import json
import sys
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from core.admission import Work
from core.context import ChatEntry
from core.contracts import (
    EVENT_KIND_DEFAULT,
    TRACE_INPUT_TRIGGER_ACCEPTED,
    TRACE_INPUT_TRIGGER_REJECTED,
    SessionKey,
)
from core.triggers import NORMALIZED_SCHEMA_VERSION, TriggerContext, TrustedClaim

try:  # Keep the module importable for transport-injected contract tests.
    import aiohttp
    from aiohttp import web
except ModuleNotFoundError:  # pragma: no cover - production installs dependencies
    aiohttp = None  # type: ignore[assignment]
    web = None  # type: ignore[assignment]


MODULE_NAME = "kick"

PLATFORM = "kick"
"""``payload.platform`` of every event this input normalises (R3).

A stable platform identifier, never derived from the module name, which is a
deployment label.
"""

PUBLIC_KEY_URL = "https://api.kick.com/public/v1/public-key"
"""Where the platform publishes its webhook signing key, fetched once at
``prepare`` when no ``public_key`` is configured."""

HEADER_MESSAGE_ID = "Kick-Event-Message-Id"
HEADER_TIMESTAMP = "Kick-Event-Message-Timestamp"
HEADER_SIGNATURE = "Kick-Event-Signature"
HEADER_EVENT_TYPE = "Kick-Event-Type"

MAX_BODY_BYTES = 65536
"""The largest delivery body accepted; one byte more is refused unread."""

TIMESTAMP_TOLERANCE_SECONDS = 300.0
"""How far a delivery timestamp may sit from the injected clock, either way."""

DEFAULT_DEDUP_MAX_ENTRIES = 4096
DEFAULT_DEDUP_WINDOW_SECONDS = 600
_DEDUP_MAX_ENTRIES_BOUNDS = (1, 100_000)
_DEDUP_WINDOW_BOUNDS = (int(TIMESTAMP_TOLERANCE_SECONDS), 86_400)

PUBLIC_KEY_FETCH_TIMEOUT_SECONDS = 10.0
"""Budget of the one public-key request ``prepare`` may make."""

LISTENER_SHUTDOWN_SECONDS = 2.0
"""How long unbinding the listener waits for in-flight requests."""

# Event types, as the platform names them, mapped to the event kind (R1).
EVENT_CHAT_MESSAGE = "chat.message.sent"
EVENT_KINDS: Mapping[str, str] = {
    EVENT_CHAT_MESSAGE: EVENT_KIND_DEFAULT,
    "channel.followed": "follow",
    "channel.subscription.new": "sub",
    "channel.subscription.renewal": "resub",
    "channel.subscription.gifts": "sub_gift",
}
NOTICE_KINDS: tuple[str, ...] = ("sub", "resub", "sub_gift", "follow")
"""Every kind ``notices.kinds`` may list."""

ANONYMOUS_AUTHOR = "system:anonymous"
"""The reserved author an anonymous gift is fed under (plan decision 3)."""

RESERVED_SEPARATOR = ":"
"""No platform author identifier may contain it (R6): reserved identities do."""

# Delivery refusals, one counter each (R7).
REFUSED_TOO_LARGE = "too_large"
REFUSED_MALFORMED = "malformed"
REFUSED_BAD_SIGNATURE = "bad_signature"
REFUSED_STALE_TIMESTAMP = "stale_timestamp"
REFUSED_DUPLICATE = "duplicate"
REFUSAL_STATUS: Mapping[str, int] = {
    REFUSED_TOO_LARGE: 413,
    REFUSED_MALFORMED: 400,
    REFUSED_BAD_SIGNATURE: 401,
    REFUSED_STALE_TIMESTAMP: 400,
    REFUSED_DUPLICATE: 409,
}
# Reception outcomes of accepted deliveries.
COUNT_INVALID = "invalid"
COUNT_NOTICES_IGNORED = "notices_ignored"
COUNT_CHANNEL_IGNORED = "channel_ignored"

# Badge types mapped to the trusted claim they attest. Only a platform-attested
# badge becomes a claim; nothing in the message body can (R1, AC4).
_BADGE_CLAIMS: Mapping[str, str] = {
    "broadcaster": "broadcaster",
    "moderator": "moderator",
    "vip": "vip",
    "subscriber": "subscription",
}
_BADGE_PROVENANCE = "kick.badges"
_BROADCASTER_PROVENANCE = "kick.broadcaster_user_id"

_CHAT_EVENT = "channel.chat.message"

# RSASSA-PKCS1-v1_5 (plan decision 9): the DER DigestInfo prefix of SHA-256,
# the rsaEncryption OID of a SubjectPublicKeyInfo, and the smallest modulus
# accepted.
SHA256_DIGEST_INFO = bytes.fromhex("3031300d060960864801650304020105000420")
_RSA_ENCRYPTION_OID = bytes.fromhex("2a864886f70d010101")
_MIN_MODULUS_BITS = 2048
_DER_SEQUENCE = 0x30
_DER_INTEGER = 0x02
_DER_BIT_STRING = 0x03
_DER_OID = 0x06


class KickModuleError(RuntimeError):
    """A Kick operation failure whose text is safe to surface."""


# --------------------------------------------------------------------------- #
# Signature verification — standard library only (plan decision 9)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, repr=False, slots=True)
class RsaPublicKey:
    """An RSA public key: modulus ``n`` and public exponent ``e``."""

    n: int
    e: int

    @property
    def size_bytes(self) -> int:
        return (self.n.bit_length() + 7) // 8


def parse_public_key(pem: str) -> RsaPublicKey:
    """Read a PEM ``PUBLIC KEY`` (SubjectPublicKeyInfo) or ``RSA PUBLIC KEY``.

    A minimal DER reader: exactly the structures an RSA public key needs, and
    ``ValueError`` for anything else — another algorithm, trailing bytes, a
    modulus under 2048 bits or an even exponent.
    """

    if not isinstance(pem, str):
        raise ValueError("public key must be a PEM string")
    lines = [line.strip() for line in pem.strip().splitlines() if line.strip()]
    if len(lines) < 3:
        raise ValueError("public key is not PEM")
    label = lines[0]
    if label == "-----BEGIN PUBLIC KEY-----" and lines[-1] == "-----END PUBLIC KEY-----":
        spki = True
    elif (
        label == "-----BEGIN RSA PUBLIC KEY-----"
        and lines[-1] == "-----END RSA PUBLIC KEY-----"
    ):
        spki = False
    else:
        raise ValueError("public key is not PEM")
    try:
        der = base64.b64decode("".join(lines[1:-1]), validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("public key is not base64") from None

    if spki:
        tag, outer, end = _der_read(der, 0)
        if tag != _DER_SEQUENCE or end != len(der):
            raise ValueError("public key is not a SubjectPublicKeyInfo")
        tag, algorithm, offset = _der_read(outer, 0)
        if tag != _DER_SEQUENCE:
            raise ValueError("public key algorithm is malformed")
        tag, oid, _ = _der_read(algorithm, 0)
        if tag != _DER_OID or oid != _RSA_ENCRYPTION_OID:
            raise ValueError("public key is not an RSA key")
        tag, bits, end = _der_read(outer, offset)
        if tag != _DER_BIT_STRING or end != len(outer) or not bits or bits[0] != 0:
            raise ValueError("public key bit string is malformed")
        der = bits[1:]

    tag, rsa_key, end = _der_read(der, 0)
    if tag != _DER_SEQUENCE or end != len(der):
        raise ValueError("public key is not an RSA public key")
    tag, modulus, offset = _der_read(rsa_key, 0)
    if tag != _DER_INTEGER:
        raise ValueError("public key modulus is malformed")
    tag, exponent, end = _der_read(rsa_key, offset)
    if tag != _DER_INTEGER or end != len(rsa_key):
        raise ValueError("public key exponent is malformed")
    n = int.from_bytes(modulus, "big", signed=True)
    e = int.from_bytes(exponent, "big", signed=True)
    if n.bit_length() < _MIN_MODULUS_BITS or e < 3 or e % 2 == 0 or e >= n:
        raise ValueError("public key parameters are unacceptable")
    return RsaPublicKey(n=n, e=e)


def _der_read(data: bytes, offset: int) -> tuple[int, bytes, int]:
    """One DER TLV at *offset*: ``(tag, value, offset past it)``."""

    if offset + 2 > len(data):
        raise ValueError("DER is truncated")
    tag = data[offset]
    length = data[offset + 1]
    offset += 2
    if length & 0x80:
        count = length & 0x7F
        if count == 0 or count > 4 or offset + count > len(data):
            raise ValueError("DER length is malformed")
        length = int.from_bytes(data[offset : offset + count], "big")
        offset += count
    end = offset + length
    if end > len(data):
        raise ValueError("DER is truncated")
    return tag, data[offset:end], end


def emsa_pkcs1_v15_sha256(message: bytes, size: int) -> bytes:
    """The EMSA-PKCS1-v1_5 encoding of SHA-256(*message*) in *size* bytes."""

    digest_info = SHA256_DIGEST_INFO + hashlib.sha256(message).digest()
    padding = size - len(digest_info) - 3
    if padding < 8:
        raise ValueError("modulus too short for the digest")
    return b"\x00\x01" + b"\xff" * padding + b"\x00" + digest_info


def verify_signature(key: RsaPublicKey, message: bytes, signature: str) -> bool:
    """Whether base64 *signature* is ``key``'s RSASSA-PKCS1-v1_5/SHA-256
    signature of *message*.

    The signature must be exactly the modulus length and numerically below
    the modulus; the recovered encoding is compared in constant time with the
    whole expected one — padding, DigestInfo and digest — never parsed.
    """

    if not isinstance(signature, str) or not signature:
        return False
    try:
        raw = base64.b64decode(signature.strip(), validate=True)
    except (binascii.Error, ValueError):
        return False
    size = key.size_bytes
    if len(raw) != size:
        return False
    value = int.from_bytes(raw, "big")
    if value >= key.n:
        return False
    recovered = pow(value, key.e, key.n).to_bytes(size, "big")
    try:
        expected = emsa_pkcs1_v15_sha256(message, size)
    except ValueError:
        return False
    return hmac.compare_digest(recovered, expected)


def signed_content(message_id: str, timestamp: str, body: bytes) -> bytes:
    """What ``Kick-Event-Signature`` signs: ``id . timestamp . raw body``."""

    return message_id.encode("utf-8") + b"." + timestamp.encode("utf-8") + b"." + body


def parse_timestamp(value: str) -> float | None:
    """An RFC 3339 timestamp with an offset as epoch seconds, else ``None``."""

    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.timestamp()


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #


_REQUIRED_STRINGS: tuple[str, ...] = ("client_secret", "access_token", "companion_name")


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
    diagnostics.extend(_validate_listener(settings.get("listener")))
    diagnostics.extend(_validate_channels(settings.get("channels")))
    if settings.get("public_key") is not None:
        try:
            parse_public_key(settings["public_key"])
        except ValueError:
            diagnostics.append(
                _setting_diagnostic("public_key", "must be a PEM RSA public key")
            )
    bot_user_id = settings.get("bot_user_id")
    if bot_user_id is not None and (
        not isinstance(bot_user_id, str) or not bot_user_id.strip()
    ):
        diagnostics.append(_setting_diagnostic("bot_user_id", "must be a non-empty string"))
    if settings.get("notices") is not None:
        diagnostics.extend(_validate_notices(settings["notices"]))
    if settings.get("dedup") is not None:
        diagnostics.extend(_validate_dedup(settings["dedup"]))
    return diagnostics


def _validate_listener(listener: Any) -> list[str]:
    if not isinstance(listener, Mapping) or set(listener) - {"host", "port"}:
        return [_setting_diagnostic("listener", "must be a mapping of host and port")]
    diagnostics: list[str] = []
    host = listener.get("host")
    if not isinstance(host, str) or not host.strip():
        diagnostics.append(_setting_diagnostic("listener.host", "must be a non-empty string"))
    port = listener.get("port")
    if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
        diagnostics.append(
            _setting_diagnostic("listener.port", "must be an integer from 0 to 65535")
        )
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


def _validate_dedup(dedup: Any) -> list[str]:
    if not isinstance(dedup, Mapping) or set(dedup) - {"max_entries", "window_seconds"}:
        return [
            _setting_diagnostic("dedup", "must be a mapping of max_entries and window_seconds")
        ]
    diagnostics: list[str] = []
    for name, (low, high) in (
        ("max_entries", _DEDUP_MAX_ENTRIES_BOUNDS),
        ("window_seconds", _DEDUP_WINDOW_BOUNDS),
    ):
        value = dedup.get(name)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            diagnostics.append(
                _setting_diagnostic(f"dedup.{name}", f"must be an integer from {low} to {high}")
            )
    return diagnostics


def _setting_diagnostic(field_name: str, reason: str) -> str:
    return f"module {MODULE_NAME!r}: field {field_name!r}: {reason}"


@dataclass(frozen=True, repr=False, slots=True)
class _Settings:
    listener_host: str
    listener_port: int
    channels: frozenset[str]
    companion_name: str
    public_key: RsaPublicKey | None
    bot_user_id: str | None
    notice_kinds: frozenset[str]
    dedup_max_entries: int
    dedup_window_seconds: float

    @classmethod
    def from_mapping(cls, settings: Mapping[str, Any]) -> "_Settings":
        """Parse accepted settings; the first diagnostic of a refusal is raised."""

        diagnostics = validate_settings(settings)
        if diagnostics:
            raise KickModuleError(diagnostics[0])
        listener = settings["listener"]
        dedup = settings.get("dedup") or {}
        notices = settings.get("notices") or {}
        public_key = settings.get("public_key")
        return cls(
            listener_host=listener["host"],
            listener_port=listener["port"],
            channels=frozenset(settings["channels"]),
            companion_name=settings["companion_name"],
            public_key=parse_public_key(public_key) if public_key is not None else None,
            bot_user_id=settings.get("bot_user_id"),
            notice_kinds=frozenset(notices.get("kinds") or ()),
            dedup_max_entries=dedup.get("max_entries", DEFAULT_DEDUP_MAX_ENTRIES),
            dedup_window_seconds=float(
                dedup.get("window_seconds", DEFAULT_DEDUP_WINDOW_SECONDS)
            ),
        )


# --------------------------------------------------------------------------- #
# Normalisation
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class _Notification:
    """One accepted delivery, normalised exactly once at the boundary (R3).

    ``author_id`` is ``None`` when the platform supplied no viewer identity;
    ``claims`` are the platform-attested facts about the author; only when
    ``roles_attested`` does the event carry ``author.roles``. ``anonymous``
    marks a gift attributed to no one, never decided or admitted.
    """

    channel_id: str
    author_id: str | None
    author_name: str | None
    message_id: str
    text: str
    claims: tuple[TrustedClaim, ...]
    roles_attested: bool = False
    kind: str = EVENT_KIND_DEFAULT
    anonymous: bool = False

    def event(self) -> dict[str, Any]:
        """The schema-version-2 bus event this delivery normalises to."""

        author: dict[str, Any] = {}
        if self.author_id is not None:
            author["id"] = self.author_id
        if self.author_name is not None:
            author["display_name"] = self.author_name
        if self.author_id is not None and self.roles_attested:
            author["roles"] = [claim.name for claim in self.claims]
            author["roles_provenance"] = _roles_provenance(self.claims)
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


def normalize_delivery(
    event_type: str,
    body: Mapping[str, Any],
    delivery_id: str,
    notice_kinds: frozenset[str] = frozenset(),
) -> _Notification | None:
    """Translate one accepted delivery, once (R1, R3, R7).

    ``None`` means an ignored delivery: an event type this module does not
    map, or a notice kind ``notices.kinds`` does not list. Raises
    ``ValueError`` for a body lacking the broadcaster or, on a chat message,
    the text. A notice carries no message identifier of its own: the
    delivery's ``Kick-Event-Message-Id`` identifies it.
    """

    kind = EVENT_KINDS.get(event_type)
    if kind is None or (kind != EVENT_KIND_DEFAULT and kind not in notice_kinds):
        return None
    broadcaster = _mapping_at(body, "broadcaster")
    channel_id = _identifier(broadcaster.get("user_id"))
    if channel_id is None:
        raise ValueError

    if kind == EVENT_KIND_DEFAULT:
        sender = _mapping_at(body, "sender")
        text = body.get("content")
        if not isinstance(text, str):
            raise ValueError
        author_id = _identifier(sender.get("user_id"))
        identity = sender.get("identity")
        badges = identity.get("badges") if isinstance(identity, Mapping) else None
        return _Notification(
            channel_id=channel_id,
            author_id=author_id,
            author_name=_optional_string(sender.get("username")),
            message_id=_identifier(body.get("message_id")) or delivery_id,
            text=text,
            claims=_trusted_claims(badges, author_id, channel_id),
            roles_attested=isinstance(badges, (list, tuple))
            or (author_id is not None and author_id == channel_id),
        )

    person_key = {"follow": "follower", "sub_gift": "gifter"}.get(kind, "subscriber")
    person = body.get(person_key)
    if kind == "sub_gift" and (
        not isinstance(person, Mapping) or person.get("is_anonymous") is True
    ):
        return _Notification(
            channel_id=channel_id,
            author_id=ANONYMOUS_AUTHOR,
            author_name=None,
            message_id=delivery_id,
            text="",
            claims=(),
            kind=kind,
            anonymous=True,
        )
    if not isinstance(person, Mapping):
        raise ValueError
    author_id = _identifier(person.get("user_id"))
    return _Notification(
        channel_id=channel_id,
        author_id=author_id,
        author_name=_optional_string(person.get("username")),
        message_id=delivery_id,
        text="",
        claims=_trusted_claims(None, author_id, channel_id),
        roles_attested=author_id is not None and author_id == channel_id,
        kind=kind,
    )


def _trusted_claims(
    badges: Any, author_id: str | None, channel_id: str
) -> tuple[TrustedClaim, ...]:
    """The platform-attested facts about the author, tagged by provenance.

    The broadcaster is recognised by identity — the author *is* the channel —
    and every other role by the badges the platform attached to the sender.
    The message body is never consulted (R1, AC4).
    """

    claims: list[TrustedClaim] = []
    names: set[str] = set()
    if author_id is not None and author_id == channel_id:
        claims.append(TrustedClaim("broadcaster", provenance=_BROADCASTER_PROVENANCE))
        names.add("broadcaster")
    if isinstance(badges, (list, tuple)):
        for badge in badges:
            if not isinstance(badge, Mapping):
                continue
            claim = _BADGE_CLAIMS.get(badge.get("type"))
            if claim is None or claim in names:
                continue
            claims.append(TrustedClaim(claim, provenance=_BADGE_PROVENANCE))
            names.add(claim)
    return tuple(claims)


def _roles_provenance(claims: tuple[TrustedClaim, ...]) -> str:
    provenances: list[str] = []
    for claim in claims:
        if claim.provenance and claim.provenance not in provenances:
            provenances.append(claim.provenance)
    return "+".join(provenances) or _BADGE_PROVENANCE


def _mapping_at(value: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    item = value.get(key)
    if not isinstance(item, Mapping):
        raise ValueError
    return item


def _identifier(value: Any) -> str | None:
    """A platform identifier as text: an integer, or a non-blank string."""

    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value)
    return _optional_string(value)


def _optional_string(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value
    return None


# --------------------------------------------------------------------------- #
# The handle
# --------------------------------------------------------------------------- #


class KickModule:
    """The v2 handle: one webhook listener, one HTTP session, phase hooks.

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
    ) -> None:
        runtime = _runtime_surfaces(context)
        self._bus = runtime.bus
        self._supervision = runtime.supervision
        self._triggers = runtime.triggers
        self._chat = runtime.chat
        self._scheduler = runtime.scheduler
        self._settings = settings
        self._session = session
        self._reporter = reporter
        self._clock = wall_clock
        self._public_key: RsaPublicKey | None = settings.public_key
        self._counts: dict[str, int] = {
            reason: 0 for reason in REFUSAL_STATUS
        } | {COUNT_INVALID: 0, COUNT_NOTICES_IGNORED: 0, COUNT_CHANNEL_IGNORED: 0}
        # Delivery identifiers retained, oldest first, with when each was seen.
        self._seen: OrderedDict[str, float] = OrderedDict()
        self._publication_tasks: set[asyncio.Task[None]] = set()
        self._runner: Any | None = None
        self._address: tuple[str, int] | None = None
        self._prepared = False
        self._accepting = False
        self._stopping = False
        self._closed = False
        self._close_lock = asyncio.Lock()

    @property
    def counts(self) -> Mapping[str, int]:
        """Delivery refusals — ``too_large``, ``malformed``, ``bad_signature``,
        ``stale_timestamp``, ``duplicate`` — and reception outcomes of accepted
        deliveries: ``invalid`` (an author identifier containing ``:``),
        ``notices_ignored`` and ``channel_ignored``."""

        return dict(self._counts)

    @property
    def address(self) -> tuple[str, int] | None:
        """The ``(host, port)`` the listener is bound to, the port resolved;
        ``None`` while it is not bound."""

        return self._address

    @property
    def dedup_size(self) -> int:
        """How many delivery identifiers the dedup window retains now."""

        return len(self._seen)

    def build_application(self) -> Any:
        """The ``aiohttp.web`` application serving deliveries.

        ``start_inputs`` binds one on the configured address; a harness may
        serve another through an in-process test server. Either way a
        delivery is refused until the inputs are started.
        """

        if web is None:  # pragma: no cover - production installs dependencies
            raise KickModuleError("kick listener: aiohttp is not installed")
        application = web.Application(client_max_size=MAX_BODY_BYTES + 1)
        application.router.add_post("/{tail:.*}", self._handle_delivery)
        return application

    # -- startup phases ---------------------------------------------------- #

    async def prepare(self) -> None:
        """Hold the platform key: the configured one, or one fetched once."""

        if self._prepared or self._closed:
            return
        if self._public_key is None:
            self._public_key = await self._fetch_public_key()
        self._prepared = True

    async def _fetch_public_key(self) -> RsaPublicKey:
        try:
            # One budget covers the request and the body: a peer that answers
            # headers promptly but stalls the body must not hold readiness.
            status, body = await asyncio.wait_for(
                self._request_public_key(), PUBLIC_KEY_FETCH_TIMEOUT_SECONDS
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            self._diagnose("kick prepare: public key request failed")
            raise KickModuleError("kick prepare: public key unavailable") from None
        try:
            if status != 200 or not isinstance(body, Mapping):
                raise ValueError
            data = _mapping_at(body, "data")
            return parse_public_key(data.get("public_key"))
        except ValueError:
            self._diagnose(f"kick prepare: public key answer refused ({_safe_status(status)})")
            raise KickModuleError("kick prepare: public key unavailable") from None

    async def _request_public_key(self) -> tuple[int | None, Any]:
        response = await _resolve(self._session.get(PUBLIC_KEY_URL))
        return await _read_response(response)

    async def start_inputs(self) -> None:
        """Bind the listener. Only the coordinator, past the barrier, calls this."""

        if self._runner is not None or self._stopping or self._closed:
            return
        if not self._prepared:
            raise KickModuleError("kick lifecycle: start_inputs requires prepare")
        runner = web.AppRunner(
            self.build_application(),
            access_log=None,
            shutdown_timeout=LISTENER_SHUTDOWN_SECONDS,
        )
        await runner.setup()
        try:
            site = web.TCPSite(
                runner, self._settings.listener_host, self._settings.listener_port
            )
            await site.start()
        except BaseException:
            with suppress(Exception):
                await runner.cleanup()
            raise
        self._runner = runner
        addresses = [
            address for address in runner.addresses if isinstance(address, tuple)
        ]
        if addresses:
            self._address = (str(addresses[0][0]), int(addresses[0][1]))
        self._accepting = True

    # -- shutdown phases --------------------------------------------------- #

    async def stop_inputs(self) -> None:
        """Unbind the listener; accepted publications finish in :meth:`drain`."""

        self._accepting = False
        self._stopping = True
        await self._unbind()

    async def drain(self, deadline_seconds: float) -> None:
        """Let accepted publications finish inside the budget; cancel the rest."""

        publications = tuple(
            task for task in self._publication_tasks if not task.done()
        )
        if not publications:
            return
        try:
            _, pending = await asyncio.wait(
                publications, timeout=max(float(deadline_seconds), 0.0)
            )
        except asyncio.CancelledError:
            for task in publications:
                if not task.done():
                    task.cancel()
            raise
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    async def close(self) -> None:
        """Release the listener and the session exactly once."""

        async with self._close_lock:
            if self._closed:
                return
            self._accepting = False
            self._stopping = True
            try:
                await self._unbind()
                publications = tuple(self._publication_tasks)
                if publications:
                    await asyncio.gather(*publications, return_exceptions=True)
            finally:
                self._closed = True
                await _close_session(self._session)

    async def _unbind(self) -> None:
        runner, self._runner = self._runner, None
        self._address = None
        if runner is not None:
            with suppress(Exception):
                await runner.cleanup()

    # -- deliveries -------------------------------------------------------- #

    async def _handle_delivery(self, request: Any) -> Any:
        """Accept one delivery in the documented order, or refuse it (R7)."""

        if not self._accepting or self._public_key is None:
            return web.Response(status=503)
        body = await _read_bounded(request, MAX_BODY_BYTES)
        if body is None:
            return self._refuse(REFUSED_TOO_LARGE)

        headers = request.headers
        message_id = headers.get(HEADER_MESSAGE_ID, "")
        timestamp = headers.get(HEADER_TIMESTAMP, "")
        signature = headers.get(HEADER_SIGNATURE, "")
        event_type = headers.get(HEADER_EVENT_TYPE, "")
        if not (message_id and timestamp and signature and event_type):
            return self._refuse(REFUSED_MALFORMED)
        if not verify_signature(
            self._public_key, signed_content(message_id, timestamp, body), signature
        ):
            return self._refuse(REFUSED_BAD_SIGNATURE)
        sent_at = parse_timestamp(timestamp)
        if sent_at is None:
            return self._refuse(REFUSED_MALFORMED)
        now = float(self._clock())
        if abs(now - sent_at) > TIMESTAMP_TOLERANCE_SECONDS:
            return self._refuse(REFUSED_STALE_TIMESTAMP)
        # Checked and recorded with no await in between, so two concurrent
        # redeliveries cannot both pass.
        self._evict_seen(now)
        if message_id in self._seen:
            return self._refuse(REFUSED_DUPLICATE)
        self._seen[message_id] = now
        while len(self._seen) > self._settings.dedup_max_entries:
            self._seen.popitem(last=False)

        try:
            decoded = json.loads(body.decode("utf-8"))
            if not isinstance(decoded, Mapping):
                raise ValueError
            notification = normalize_delivery(
                event_type, decoded, message_id, self._settings.notice_kinds
            )
        except ValueError:
            return self._refuse(REFUSED_MALFORMED)
        if notification is None:
            self._counts[COUNT_NOTICES_IGNORED] += 1
            return web.Response(status=200)

        publication = asyncio.create_task(
            self._publish(notification), name="kick-webhook-publication"
        )
        self._publication_tasks.add(publication)
        publication.add_done_callback(self._publication_finished)
        # Shielded: a client that hangs up does not cancel an accepted
        # delivery halfway through its publication.
        await asyncio.shield(publication)
        return web.Response(status=200)

    def _refuse(self, reason: str) -> Any:
        self._counts[reason] += 1
        self._diagnose(f"kick webhook: delivery refused ({reason})")
        return web.Response(status=REFUSAL_STATUS[reason])

    def _evict_seen(self, now: float) -> None:
        horizon = now - self._settings.dedup_window_seconds
        while self._seen:
            oldest_id, seen_at = next(iter(self._seen.items()))
            if seen_at >= horizon:
                break
            del self._seen[oldest_id]

    def _publication_finished(self, task: asyncio.Task[None]) -> None:
        self._publication_tasks.discard(task)
        if not task.cancelled():
            task.exception()

    async def _publish(self, notification: _Notification) -> None:
        """Refuse reserved authors, drop echoes, feed, decide, publish, trace,
        admit — the twitch input's order (R1, R6)."""

        if notification.channel_id not in self._settings.channels:
            self._counts[COUNT_CHANNEL_IGNORED] += 1
            return
        if (
            not notification.anonymous
            and notification.author_id is not None
            and RESERVED_SEPARATOR in notification.author_id
        ):
            self._counts[COUNT_INVALID] += 1
            self._diagnose("kick delivery: reserved author identity refused")
            return
        if self._is_own_message(notification):
            return

        engine = None if notification.anonymous else self._triggers
        if engine is not None and engine.recorded(
            platform=PLATFORM,
            channel_id=notification.channel_id,
            source_event_id=notification.message_id,
        ) is not None:
            return
        event = notification.event()
        if notification.author_id is not None:
            self._feed_chat_context(notification)
        decision = None
        if engine is not None:
            try:
                decision = engine.evaluate(
                    event, context=TriggerContext(notification.claims)
                )
            except Exception:
                self._diagnose("kick trigger evaluation: failed")

        if notification.author_id is None:
            if decision is not None:
                await self._trace_decision(decision)
            return
        try:
            await self._bus.publish(_CHAT_EVENT, event["payload"], event["metadata"])
        except asyncio.CancelledError:
            raise
        except Exception:
            self._diagnose("kick delivery publish: failed")
        if decision is None:
            return
        await self._trace_decision(decision)
        if decision.accepted and self._scheduler is not None:
            self._admit(notification, event)

    def _is_own_message(self, notification: _Notification) -> bool:
        """The companion's own account: its identifier, when configured, or
        its name, compared case-insensitively (anti-echo, R7)."""

        if notification.anonymous or notification.author_id is None:
            return False
        bot_user_id = self._settings.bot_user_id
        if bot_user_id is not None and notification.author_id == bot_user_id:
            return True
        name = notification.author_name
        return (
            name is not None
            and name.casefold() == self._settings.companion_name.casefold()
        )

    def _feed_chat_context(self, notification: _Notification) -> None:
        if self._chat is None or notification.author_id is None:
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
            self._diagnose("kick chat context: append failed")

    async def _trace_decision(self, decision: Any) -> None:
        if not getattr(decision, "traceable", True):
            return
        event_type = (
            TRACE_INPUT_TRIGGER_ACCEPTED
            if decision.accepted
            else TRACE_INPUT_TRIGGER_REJECTED
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
            self._diagnose("kick trigger trace: publication failed")

    def _admit(self, notification: _Notification, event: Mapping[str, Any]) -> None:
        assert notification.author_id is not None
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
            self._diagnose("kick admission: failed")

    def _diagnose(self, message: str) -> None:
        with suppress(Exception):
            self._reporter(message)


async def _read_bounded(request: Any, limit: int) -> bytes | None:
    """The request body, or ``None`` once it exceeds *limit* bytes.

    A declared length over the limit is refused unread; otherwise at most
    ``limit + 1`` bytes are ever buffered.
    """

    declared = getattr(request, "content_length", None)
    if isinstance(declared, int) and declared > limit:
        return None
    chunks: list[bytes] = []
    size = 0
    while True:
        chunk = await request.content.read(limit + 1 - size)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)
        size += len(chunk)
        if size > limit:
            return None


# --------------------------------------------------------------------------- #
# Activation
# --------------------------------------------------------------------------- #


def _default_session_factory() -> Any:
    if aiohttp is None:
        raise RuntimeError("aiohttp is not installed")
    return aiohttp.ClientSession()


SESSION_FACTORY: Callable[[], Any] = _default_session_factory
WALL_CLOCK: Callable[[], float] = time.time


async def activate(
    context: Any,
    settings: Mapping[str, Any],
    catalog: Mapping[str, Mapping[str, Any]],
) -> KickModule:
    """Build the handle from the scoped runtime context (R7).

    Nothing here reaches the network — the key is read at ``prepare`` and the
    listener bound at ``start_inputs`` — so a refused activation has nothing
    to undo. ``_session_factory`` and ``_wall_clock`` are the injected
    transport and clock (a timestamp is compared with wall time, never with
    the runtime's monotonic clock).
    """

    del catalog
    _runtime_surfaces(context)
    parsed = _Settings.from_mapping(settings)
    reporter = settings.get("diagnostic_reporter", _default_reporter)
    session_factory = settings.get("_session_factory", SESSION_FACTORY)
    wall_clock = settings.get("_wall_clock", WALL_CLOCK)
    if not callable(reporter):
        raise KickModuleError("kick configuration: diagnostic reporter is invalid")
    if not callable(session_factory) or not callable(wall_clock):
        raise KickModuleError("kick configuration: transport seam is invalid")
    try:
        created = session_factory()
        session = await created if inspect.isawaitable(created) else created
    except Exception:
        _safe_report(reporter, "kick transport: session creation failed")
        raise KickModuleError("kick transport initialization failed") from None
    return KickModule(context, parsed, session, reporter, wall_clock)


@dataclass(frozen=True, slots=True)
class _RuntimeSurfaces:
    bus: Any
    supervision: Any
    triggers: Any | None
    chat: Any | None
    scheduler: Any | None


_REQUIRED_SURFACES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("bus", ("publish",)),
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
            raise KickModuleError("kick activation: runtime context is invalid")
        surfaces[name] = value
    for name, methods in _OPTIONAL_SURFACES:
        value = getattr(context, name, None)
        if value is not None and not all(
            callable(getattr(value, m, None)) for m in methods
        ):
            raise KickModuleError("kick activation: runtime context is invalid")
        surfaces[name] = value
    return _RuntimeSurfaces(**surfaces)


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
    "ANONYMOUS_AUTHOR",
    "EVENT_KINDS",
    "HEADER_EVENT_TYPE",
    "HEADER_MESSAGE_ID",
    "HEADER_SIGNATURE",
    "HEADER_TIMESTAMP",
    "KickModule",
    "KickModuleError",
    "MAX_BODY_BYTES",
    "MODULE_NAME",
    "PLATFORM",
    "PUBLIC_KEY_URL",
    "RsaPublicKey",
    "SHA256_DIGEST_INFO",
    "TIMESTAMP_TOLERANCE_SECONDS",
    "activate",
    "emsa_pkcs1_v15_sha256",
    "normalize_delivery",
    "parse_public_key",
    "parse_timestamp",
    "signed_content",
    "validate_settings",
    "verify_signature",
]
