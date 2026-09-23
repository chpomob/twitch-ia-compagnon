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

**Sending** (R7). ``prepare`` binds this module's provider to the
``chat.write`` its manifest declares, over ``kick/*/chat``; a call for a
channel outside ``channels`` is refused ``unsupported_destination`` with 0
requests. A send is one POST to :data:`CHAT_URL`, never retried: a text over
:data:`MAX_CHAT_TEXT_CHARS` characters is ``error text_too_long`` with 0
requests; a 429 is ``error rate_limited`` and blocks every further send until
the retry instant the platform announced (``refused rate_limited``, 0
requests) — read defensively from ``Retry-After`` (seconds or an HTTP date)
or a reset header (an epoch instant, a delay or an RFC 3339 time), clamped to
:data:`MAX_RATE_LIMIT_SECONDS`, and :data:`DEFAULT_RATE_LIMIT_SECONDS` when
nothing is readable; the source and the wait are traced on the call's
``action.completed``. A lost, timed-out or 5xx answer is ``external_unknown``;
another refusal ``error platform_rejected``; only the platform's own
acknowledgement with a message identifier is a ``success``.

**Moderation** (R5). Only when the context carries a service registry,
``activate`` publishes a moderation service under ``(moderation, kick)``
whose ``operations`` are ``{timeout}``: the platform's timeout unit is the
minute, so a duration is rounded **up** to whole minutes, and a result
outside :data:`MIN_TIMEOUT_MINUTES`..:data:`MAX_TIMEOUT_MINUTES` is refused
here with 0 requests. A timeout is one POST to :data:`MODERATION_BANS_URL`
that always carries ``duration`` — without it the endpoint bans permanently.
No clip and no poll service is published: those capabilities stay unbound
for this platform (``platform_unsupported``).

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
import email.utils
import json
import math
import sys
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
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

CHAT_URL = "https://api.kick.com/public/v1/chat"
"""The platform chat endpoint one ``chat.write`` call posts to."""

MODERATION_BANS_URL = "https://api.kick.com/public/v1/moderation/bans"
"""The platform bans endpoint a timeout posts to, always with a duration."""

CHAT_WRITE_ACTION = "chat.write"
"""The action contract the manifest declares and :meth:`KickModule.prepare` binds."""

MANIFEST_PATH = Path(__file__).with_name("module.yaml")
"""The colocated manifest: the one declaration of ``chat.write`` this module serves."""

CHAT_WRITE_PROVIDER = "kick-chat"
"""Provider name in bindings and traces."""

MAX_CHAT_TEXT_CHARS = 500
"""The longest message text the platform accepts, in characters."""

CHAT_SEND_TIMEOUT_SECONDS = 10.0
"""Budget of one send request, matching the manifest's ``timeout_seconds``."""

DEFAULT_RATE_LIMIT_SECONDS = 60.0
"""How long sends stay blocked after a 429 whose retry instant is unreadable."""

MAX_RATE_LIMIT_SECONDS = 3600.0
"""The longest block an announced retry instant may impose."""

RETRY_SOURCE_RETRY_AFTER = "retry_after"
RETRY_SOURCE_RESET = "reset"
RETRY_SOURCE_DEFAULT = "default"
_RETRY_AFTER_HEADER = "Retry-After"
_RESET_HEADERS: tuple[str, ...] = ("X-RateLimit-Reset", "RateLimit-Reset")
# A numeric reset at or above this is an epoch instant (seconds, or
# milliseconds above the second bound); below it, a delay in seconds.
_EPOCH_SECONDS_FLOOR = 1e9
_EPOCH_MILLISECONDS_FLOOR = 1e12

MODERATION_SERVICE_KIND = "moderation"
"""The service kind of the moderation service (R5)."""

MODERATION_OPERATIONS = frozenset({"timeout"})
"""What the ``moderation`` service offers on this platform (R5): no delete."""

MIN_TIMEOUT_MINUTES = 1
MAX_TIMEOUT_MINUTES = 10080
"""The timeout range the platform accepts, in whole minutes (seven days)."""

_MAX_BAN_REASON_CHARS = 100

# Moderation answers, the taxonomy the moderation consumer reads (R5).
MODERATION_OK = "ok"
OUTCOME_REJECTED = "rejected"
OUTCOME_RATE = "rate"
OUTCOME_UNCERTAIN = "uncertain"

# Error codes a send is normalised to (R5, R7).
ERROR_TEXT_TOO_LONG = "text_too_long"
ERROR_RATE_LIMITED = "rate_limited"
_ERROR_INVALID_ARGUMENTS = "invalid_arguments"
_ERROR_UNSUPPORTED_DESTINATION = "unsupported_destination"
_ERROR_PROVIDER_CLOSED = "provider_closed"
_ERROR_PLATFORM_REJECTED = "platform_rejected"
_ERROR_TRANSPORT_FAILED = "transport_failed"
_ERROR_TIMED_OUT = "timed_out"
_ERROR_MALFORMED_RESPONSE = "malformed_response"

_STATUS_SUCCESS = "success"
_STATUS_REFUSED = "refused"
_STATUS_ERROR = "error"
_STATUS_EXTERNAL_UNKNOWN = "external_unknown"
_CHAT_SCOPE = "chat"

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
# Rate-limit answers — read defensively (R7)
# --------------------------------------------------------------------------- #


def rate_limit_until(headers: Any, now: float) -> tuple[float, str]:
    """When sends may resume after a 429, and where that instant came from.

    ``Retry-After`` is read first — a delay in seconds or an HTTP date — then
    a reset header — an epoch instant (seconds or milliseconds), a delay in
    seconds, or an RFC 3339 time. The wait is clamped to
    ``0..MAX_RATE_LIMIT_SECONDS``, so no answer blocks sends for longer. When
    nothing is readable the source is :data:`RETRY_SOURCE_DEFAULT` and the
    wait :data:`DEFAULT_RATE_LIMIT_SECONDS`.
    """

    delay = _retry_after_delay(_header(headers, _RETRY_AFTER_HEADER), now)
    source = RETRY_SOURCE_RETRY_AFTER
    if delay is None:
        source = RETRY_SOURCE_RESET
        for name in _RESET_HEADERS:
            delay = _reset_delay(_header(headers, name), now)
            if delay is not None:
                break
    if delay is None:
        return now + DEFAULT_RATE_LIMIT_SECONDS, RETRY_SOURCE_DEFAULT
    return now + min(max(delay, 0.0), MAX_RATE_LIMIT_SECONDS), source


def _header(headers: Any, name: str) -> str | None:
    """One header value by case-insensitive name; ``None`` when absent."""

    if not isinstance(headers, Mapping):
        return None
    with suppress(Exception):
        value = headers.get(name)
        if isinstance(value, str):
            return value
    folded = name.casefold()
    for key, value in list(headers.items()):
        if isinstance(key, str) and key.casefold() == folded and isinstance(value, str):
            return value
    return None


def _finite_number(value: str) -> float | None:
    try:
        number = float(value.strip())
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _retry_after_delay(value: str | None, now: float) -> float | None:
    if value is None or not value.strip():
        return None
    number = _finite_number(value)
    if number is not None:
        return number if number >= 0 else None
    return _http_date_delay(value, now)


def _reset_delay(value: str | None, now: float) -> float | None:
    if value is None or not value.strip():
        return None
    number = _finite_number(value)
    if number is not None:
        if number < 0:
            return None
        if number >= _EPOCH_MILLISECONDS_FLOOR:
            return number / 1000.0 - now
        if number >= _EPOCH_SECONDS_FLOOR:
            return number - now
        return number
    instant = parse_timestamp(value)
    if instant is not None:
        return instant - now
    return _http_date_delay(value, now)


def _http_date_delay(value: str, now: float) -> float | None:
    try:
        parsed = email.utils.parsedate_to_datetime(value.strip())
    except (TypeError, ValueError, IndexError):
        return None
    if parsed is None or parsed.tzinfo is None:
        return None
    return parsed.timestamp() - now


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
    access_token: str
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
            access_token=settings["access_token"],
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
# The chat send provider and the moderation service (R5, R7)
# --------------------------------------------------------------------------- #


class _ChatWriteProvider:
    """The ``chat.write`` provider the executor invokes (R7).

    A thin handle: the module owns the transport, the rate-limit state and the
    lifecycle, so the provider holds no state of its own.
    """

    __slots__ = ("_module",)

    name = CHAT_WRITE_PROVIDER

    def __init__(self, module: "KickModule") -> None:
        self._module = module

    async def invoke(self, invocation: Any) -> ActionObservation:
        return await self._module._invoke_chat_write(invocation)


@dataclass(frozen=True, slots=True)
class ModerationResult:
    """A classified moderation answer: ``ok``, ``rejected``, ``rate`` or
    ``uncertain`` (R5)."""

    outcome: str


class _KickModerationService:
    """The moderation service this module publishes under ``(moderation,
    kick)`` (R5).

    ``operations`` is :data:`MODERATION_OPERATIONS` — ``timeout`` only; the
    platform has no single-message delete here, so the moderation consumer
    refuses a delete ``platform_unsupported`` before reaching this service.
    :meth:`round_duration` rounds a duration **up** to whole minutes (in
    seconds), so the consumer checks its bound on what is really sent.
    ``apply`` refuses, with 0 requests, anything but a timeout with a target
    and a positive whole duration whose minutes lie in
    :data:`MIN_TIMEOUT_MINUTES`..:data:`MAX_TIMEOUT_MINUTES`; otherwise it is
    one POST to :data:`MODERATION_BANS_URL` carrying ``duration`` in minutes,
    never retried. Any 2xx is ``ok``; 429 ``rate``; another 4xx
    ``rejected``; a 5xx, a lost answer or a request that may have left
    ``uncertain``.
    """

    __slots__ = ("_module",)

    operations = MODERATION_OPERATIONS

    def __init__(self, module: "KickModule") -> None:
        self._module = module

    def round_duration(self, seconds: int) -> int:
        return _timeout_minutes(seconds) * 60

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
        del message_id
        module = self._module
        if operation != "timeout":
            module._diagnose("kick moderation: refused (operation not offered)")
            return ModerationResult(OUTCOME_REJECTED)
        if (
            isinstance(duration_seconds, bool)
            or not isinstance(duration_seconds, int)
            or duration_seconds <= 0
        ):
            # No duration would be a permanent ban on this endpoint (R5).
            module._diagnose("kick moderation timeout: refused (no duration)")
            return ModerationResult(OUTCOME_REJECTED)
        minutes = _timeout_minutes(duration_seconds)
        if not MIN_TIMEOUT_MINUTES <= minutes <= MAX_TIMEOUT_MINUTES:
            module._diagnose("kick moderation timeout: refused (duration out of range)")
            return ModerationResult(OUTCOME_REJECTED)
        if not isinstance(target_author_id, str) or not target_author_id.strip():
            module._diagnose("kick moderation timeout: refused (no target)")
            return ModerationResult(OUTCOME_REJECTED)
        ban: dict[str, Any] = {
            "broadcaster_user_id": _platform_id(channel_id),
            "user_id": _platform_id(target_author_id),
            "duration": minutes,
        }
        if isinstance(reason, str) and reason.strip():
            ban["reason"] = reason[:_MAX_BAN_REASON_CHARS]
        status, _body, _headers = await module._exchange(
            "kick moderation timeout",
            lambda session: session.post(
                MODERATION_BANS_URL, headers=module._api_headers(), json=ban
            ),
        )
        if status is None:
            return ModerationResult(OUTCOME_UNCERTAIN)
        if 200 <= status < 300:
            return ModerationResult(MODERATION_OK)
        if status == 429:
            return ModerationResult(OUTCOME_RATE)
        if 400 <= status < 500:
            return ModerationResult(OUTCOME_REJECTED)
        return ModerationResult(OUTCOME_UNCERTAIN)


def _timeout_minutes(seconds: int) -> int:
    """*seconds* rounded **up** to whole minutes."""

    return -(-int(seconds) // 60)


def _platform_id(value: str) -> Any:
    """A platform identifier as the API expects it: an integer when numeric."""

    text = value.strip()
    return int(text) if text.isdigit() else text


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
        self._actions = runtime.actions
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
        self._provider = _ChatWriteProvider(self)
        self._moderation_service = _KickModerationService(self)
        # The wall-clock instant before which no send may leave (after a 429).
        self._sends_blocked_until: float | None = None
        self._bound = False
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
    def moderation_service(self) -> _KickModerationService:
        """The moderation service ``activate`` publishes under ``(moderation,
        kick)`` when the context carries a service registry."""

        return self._moderation_service

    @property
    def sends_blocked_until(self) -> float | None:
        """The wall-clock instant sends resume after a 429; ``None`` when no
        block was ever announced."""

        return self._sends_blocked_until

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
        """Bind ``chat.write``, then hold the platform key: the configured one,
        or one fetched once. Ready for the executor only once both hold."""

        if self._prepared or self._closed:
            return
        self._bind_chat_write()
        if self._public_key is None:
            self._public_key = await self._fetch_public_key()
        self._actions.mark_ready()
        self._prepared = True

    def _bind_chat_write(self) -> None:
        """Register this module's provider under the declared ``chat.write``.

        The contract is read from the colocated manifest — the declaration
        the loader recorded — and bound over ``kick/*/chat``; a call for a
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
            self._diagnose(f"kick prepare: {exc}")
            raise KickModuleError("kick prepare: action binding failed") from None
        self._bound = True

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
            if self._prepared:
                with suppress(Exception):
                    self._actions.mark_not_ready()
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

    # -- sending ----------------------------------------------------------- #

    async def _invoke_chat_write(self, invocation: Any) -> ActionObservation:
        """Serve one ``chat.write`` call for the executor (R7).

        Everything decidable here is refused before emission, with 0
        requests: a blank text or reply parent, a text over
        :data:`MAX_CHAT_TEXT_CHARS`, a channel outside ``channels``, a closed
        handle, and a send while a 429's retry instant has not passed.
        Emission is signalled right before the one request leaves, so an
        interruption after it is ``external_unknown``, never a certain
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
            self._diagnose("kick chat send: invalid input")
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
                destination.channel_id, text, parent, on_emit=invocation.mark_emitted
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
        self,
        channel_id: str,
        text: str,
        parent_message_id: str | None,
        *,
        on_emit: Callable[[], None],
    ) -> _SendOutcome:
        """One POST to :data:`CHAT_URL`, classified; never retried (R7)."""

        if self._closed:
            return _SendOutcome(
                _STATUS_ERROR, _ERROR_PROVIDER_CLOSED, "kick chat send: transport closed"
            )
        now = float(self._clock())
        blocked_until = self._sends_blocked_until
        if blocked_until is not None and now < blocked_until:
            wait = blocked_until - now
            return _SendOutcome(
                _STATUS_REFUSED,
                ERROR_RATE_LIMITED,
                "kick chat send: rate limited by the platform",
                trace_fields={"retry_after_seconds": round(wait, 3)},
            )
        body: dict[str, Any] = {
            "broadcaster_user_id": _platform_id(channel_id),
            "content": text,
            "type": "user",
        }
        if parent_message_id is not None:
            body["reply_to_message_id"] = parent_message_id

        on_emit()
        try:
            status, answer, headers = await asyncio.wait_for(
                self._exchange(
                    "kick chat send",
                    lambda session: session.post(
                        CHAT_URL, headers=self._api_headers(), json=body
                    ),
                ),
                timeout=CHAT_SEND_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            self._diagnose("kick chat send: request timed out")
            return _SendOutcome(
                _STATUS_EXTERNAL_UNKNOWN, _ERROR_TIMED_OUT, "kick chat send: request timed out"
            )
        if status is None or status >= 500:
            return _SendOutcome(
                _STATUS_EXTERNAL_UNKNOWN,
                _ERROR_TRANSPORT_FAILED,
                f"kick chat send: outcome unknown ({_safe_status(status)})",
            )
        if status == 429:
            return self._rate_limited(headers)
        if not 200 <= status < 300:
            return _SendOutcome(
                _STATUS_ERROR,
                _ERROR_PLATFORM_REJECTED,
                f"kick chat send: rejected (status {status})",
            )
        data = answer.get("data") if isinstance(answer, Mapping) else None
        sent = data.get("is_sent") if isinstance(data, Mapping) else None
        message_id = data.get("message_id") if isinstance(data, Mapping) else None
        if sent is False:
            self._diagnose(f"kick chat send: rejected (status {status})")
            return _SendOutcome(
                _STATUS_ERROR,
                _ERROR_PLATFORM_REJECTED,
                f"kick chat send: rejected (status {status})",
            )
        if sent is not True or not isinstance(message_id, str) or not message_id.strip():
            self._diagnose("kick chat send: malformed response")
            return _SendOutcome(
                _STATUS_EXTERNAL_UNKNOWN,
                _ERROR_MALFORMED_RESPONSE,
                "kick chat send: malformed response",
            )
        return _SendOutcome(_STATUS_SUCCESS, message_id=message_id)

    def _rate_limited(self, headers: Any) -> _SendOutcome:
        """Record the announced retry instant; block sends until it (R7)."""

        now = float(self._clock())
        until, source = rate_limit_until(headers, now)
        if self._sends_blocked_until is None or until > self._sends_blocked_until:
            self._sends_blocked_until = until
        if source == RETRY_SOURCE_DEFAULT:
            self._diagnose(
                "kick chat send: rate-limit answer unreadable; "
                f"sends blocked for {DEFAULT_RATE_LIMIT_SECONDS:g} s"
            )
        return _SendOutcome(
            _STATUS_ERROR,
            ERROR_RATE_LIMITED,
            "kick chat send: rate limited by the platform",
            trace_fields={
                "retry_after_seconds": round(until - now, 3),
                "retry_source": source,
            },
        )

    def _api_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._settings.access_token}",
            "Accept": "application/json",
        }

    async def _exchange(
        self, label: str, send: Callable[[Any], Any]
    ) -> tuple[int | None, Any, Any]:
        """Send one API request; ``(status, body, headers)``, never raised.

        ``status`` is ``None`` when the answer is lost. Diagnostics name the
        operation and the status only: the credential lives in the headers
        and the answer body never reaches them.
        """

        if self._closed:
            self._diagnose(f"{label}: request not sent (closed)")
            return None, None, None
        try:
            response = await _resolve(send(self._session))
        except asyncio.CancelledError:
            raise
        except Exception:
            self._diagnose(f"{label}: request lost")
            return None, None, None
        try:
            headers = getattr(response, "headers", None)
            status, body = await _read_response(response)
        except asyncio.CancelledError:
            raise
        except Exception:
            self._diagnose(f"{label}: answer lost")
            return None, None, None
        if status is None:
            self._diagnose(f"{label}: answer lost")
        elif not 200 <= status < 300:
            self._diagnose(f"{label}: refused (status {status})")
        return status, body, headers

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
    module = KickModule(context, parsed, session, reporter, wall_clock)
    # Published only on a bound registry: a context without one publishes
    # nothing (R7). ``timeout`` is the only operation; no clip and no poll
    # service exists on this platform, so those capabilities stay unbound
    # here with ``platform_unsupported`` (R2, R5).
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
        raise KickModuleError(
            f"kick prepare: manifest declaration of {CHAT_WRITE_ACTION!r} is invalid"
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
    "ANONYMOUS_AUTHOR",
    "CHAT_URL",
    "CHAT_WRITE_ACTION",
    "CHAT_WRITE_PROVIDER",
    "DEFAULT_RATE_LIMIT_SECONDS",
    "ERROR_RATE_LIMITED",
    "ERROR_TEXT_TOO_LONG",
    "MANIFEST_PATH",
    "MAX_CHAT_TEXT_CHARS",
    "MAX_RATE_LIMIT_SECONDS",
    "MAX_TIMEOUT_MINUTES",
    "MIN_TIMEOUT_MINUTES",
    "MODERATION_BANS_URL",
    "MODERATION_OPERATIONS",
    "MODERATION_SERVICE_KIND",
    "ModerationResult",
    "RETRY_SOURCE_DEFAULT",
    "RETRY_SOURCE_RESET",
    "RETRY_SOURCE_RETRY_AFTER",
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
    "rate_limit_until",
    "signed_content",
    "validate_settings",
    "verify_signature",
]
