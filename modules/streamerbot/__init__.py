"""Automation bridge on the v2 runtime (phase 5 R1, R4, R6, R7, R10).

The manifest beside this package declares ``manifest_version: 2``: the
loader runs :func:`validate_settings` before anything is activated, hands
:func:`activate` a scoped runtime context, and drives the returned handle
through the hooks of the ``input`` role.

The bridge is optional in every case (R1). Its settings schema requires
nothing; this package's hook refuses a configuration it cannot use — a
missing ``url`` or ``companion_name``, a plain ``ws`` URL to a non-loopback
host, a non-finite or non-positive deadline, an ambiguous allowlist — with
one value-free diagnostic per field, so ``--check-config`` reports it before
any socket opens. Ownership-aware (D7): when the runtime hands it the pairs
this module owns, a (``chat``, P) pair without a configured companion
identity for P is refused naming the platform.

**The connection (R6).** One ``aiohttp`` client session holds at most one
WebSocket to the configured ``url``. ``prepare`` binds
``automation.action.run``; ``start_inputs`` makes the first attempt — read
the hello, answer its challenge when it carries one, read the inventory — and
only then marks the action ready and reports ``module.ready``. Each step has
its own deadline. An attempt that fails never fails startup (R1): it is one
``module.degraded`` record carrying one of five value-free reasons
(``unreachable``, ``authentication_failed``, ``authentication_required``,
``protocol_error``, ``inventory_unavailable``), and a connection-owner task
spawned through the context retries with a capped, jitter-free exponential
backoff on the context clock. A lost connection withdraws readiness at once
and reports ``degraded`` (``unreachable``); a reconnection that succeeds
reports ``ready`` again. Events the instance sends while disconnected are
lost.

One reader per connection resolves each answer to its request's future; when
the connection ends, every pending future fails with
:class:`ConnectionClosed`. ``stop_inputs`` stops the owner and leaves an open
connection to ``close``, which fails what is still pending and closes the
socket within the coordinator's close budget. A cancellation of the owner
outside shutdown releases the connection and propagates.

**The allowlist (R5).** At every successful connection, before readiness,
the configured allowlist is reconciled against the inventory just read: the
reachable set is every configured ``action:`` name, plus every instance
action of a configured ``group:``, that exists exactly once and is enabled,
and one value-free ``automation.allowlist.reconciled`` trace names the
entries that reach nothing.

**The run provider (R4).** It declares nothing emitted first, then decides —
not allowlisted, missing, ambiguous, disabled, arguments refused by the
deciding schema — strictly before any frame. A call that passes sends
exactly one ``DoAction`` frame naming the instance action's *id*, never its
name. The send is the transmission boundary of D8: in one synchronous
segment, with no ``await`` before the transport write, the provider checks
that the frame can be written (the bridge still holds this connection, the
WebSocket is not closed, the writer is not closing, the protocol's transport
is present and not closing), marks the call emitted, and hands the frame to
``ws.send_str`` — whose uncompressed path (``compress=0``) writes before its
first suspension. A failed check is ``refused provider_not_ready`` with
nothing sent; anything after the mark is ``external_unknown``. The
instance's acceptance does not say the action ran, so an acknowledged run is
``external_unknown`` (``execution_unconfirmed``), an error answer is ``error
provider_failed`` (``instance_rejected``), and a missing acknowledgement, a
lost connection or a cancellation after sending is ``external_unknown``
(``acknowledgement_lost``). Never a success, never a retry.

**Subscriptions and the inbound queue (R6).** Before readiness, one
``Subscribe`` request names only the event categories the owned pairs need:
the custom-event category when the bridge owns cues on any platform, and for
each owned (``chat``, P) P's chat-message and notice categories; viewers are
read on demand and need none. The reader never waits on an event: it hands
each one to a queue bounded by ``queue_max_events`` without blocking, an
event arriving at a full queue is dropped and counted (``dropped_events``,
also a detail of every ``module.ready`` record the bridge reports), and
request answers keep flowing. One consumer task drains the queue.

**Cues (R7a).** A custom event whose ``args`` carry the cue envelope —
``cue_id`` (non-empty, at most 128 characters), ``platform`` (non-empty), an
optional ``channel_id`` (by default the ``instance_identity`` of that
platform; with neither, the cue is dropped), an optional ``user_id`` with its
``user_name``, and ``text`` (a string of at most 500 code points) — becomes
one schema-version-2 ``channel.chat.message`` with ``payload.kind: cue``,
``message_id`` = ``cue_id`` and ``metadata.source`` = this module. Other
``args`` keys — the instance hands an action's own arguments along — are
ignored. Its author is the envelope user or, absent one, the stable identity
``instance:<platform>``: every platform module refuses an account id holding
``:``, so it is distinct from every viewer and every configured companion
identity, and an envelope ``user_id`` holding ``:`` is refused. A malformed
or oversized envelope, a cue for a platform this module does not own, and
any event with no mapping are dropped and counted, never truncated or
repaired. A cue is evaluated by the trigger policy under
``triggers.<module>`` with no role claims, then published on the bus, and
its decision traced, as the native chat inputs do; an accepted cue is
admitted to the runtime's shared scheduler when the context carries one. A
companion identity holding ``:`` is refused. The companion identities
are handed to the trigger engine at activation, so its anti-echo refuses
what the companion authored. A ``cue_id`` the instance sends again inside
the engine's dedup window reuses the decision already recorded and is not
published twice: the instance must not reuse a ``cue_id`` for a new cue.

**Chat (R7b).** For each owned (``chat``, P), P's documented chat-message
event and each documented notice event — one module-local table keyed by
(platform, instance event type), the fixtures README's enumerated list —
becomes one ``channel.chat.message`` whose payload keys and ``kind`` are
those P's native input publishes: ``platform``, ``channel_id``, ``author``
(``id``, ``display_name``), ``message_id``, ``text``, and ``kind`` for a
notice only. The channel is the event's broadcaster account when it carries
one, else ``instance_identity.<P>``; an event the instance documents
without an id (a raid, a follow, every kick notice) is identified by the
digest of its frame, so a replay falls in the dedup window. A documented
field the mapping needs and the frame lacks, an anonymous gift and any
other event type are dropped and counted. Role claims from the instance —
badges, or the user flags on youtube — are trusted only when
``trust_instance_roles`` is true: then the author carries ``roles`` and
``roles_provenance`` = this module and the claims reach the trigger
engine; otherwise the event carries no ``roles`` and is evaluated with no
claim, so no audience rule above ``everyone`` is met. Chat events take the
cue path — dedup, decision, publication, trace, admission — so the engine's
anti-echo refuses what a companion identity authored.

**Viewers (R7c).** ``prepare`` also registers ``users.read`` with the spec
read from the ``users`` manifest itself, so the two declarations are equal
and merge; the runtime binds it only for the (``viewers``, P) pairs this
module owns. It answers for ``instance_identity.<P>`` only — any other
channel is ``refused unsupported_destination`` — with one page of the
instance's active-viewer list of P, fetched within ``request_seconds``:
at most ``limit`` users, ``coverage.kind: active_viewers``,
``coverage.complete: false``. The list carries no per-viewer timestamp, so
``first_seen``, ``last_seen`` and ``freshness.observed_at`` are the fetch
instant and ``freshness.window_seconds`` is 0. A ``cursor`` pages over the
same fetched snapshot (the latest per platform, at most
:data:`VIEWER_SNAPSHOT_MAX_USERS` users) and is refused for another
destination. A read is never emitted, so its deadline stays ``timeout``.

Nothing here logs: the URL, the password and frame text never reach a trace
or a diagnostic, only reasons do. No endpoint, port, password, version or
action name has a default anywhere (R6).

**Seams, for the tests.** ``_sleeper`` (the sleep the backoff and the
acknowledgement deadline wait on, ``asyncio.sleep`` by default) is read from
*settings* at ``activate`` and never from a configuration file.
``AutomationBridge._before_transmit`` is awaited by the run provider after
every decision and strictly before the transmission segment; by default it
returns at once. Replacing it makes the interval before the frame leaves
observable. ``AutomationBridge._before_consume`` is awaited by the consumer
before it takes each event from the queue; by default it returns at once.
Replacing it holds the consumer, so the queue's bound is observable.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import ipaddress
import itertools
import json
import math
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any
from urllib.parse import quote, unquote, urlsplit

import aiohttp
import yaml

from core.actions import (
    ERROR_CANCELLED,
    ERROR_EXTERNAL_UNKNOWN,
    ERROR_INVALID_ARGUMENTS,
    ERROR_PROVIDER_FAILED,
    ERROR_PROVIDER_NOT_READY,
    ERROR_TIMED_OUT,
    ERROR_UNSUPPORTED_DESTINATION,
)
from core.contracts import (
    CUE_KIND,
    EVENT_KIND_DEFAULT,
    PART_TYPE_TEXT,
    TRACE_INPUT_TRIGGER_ACCEPTED,
    TRACE_INPUT_TRIGGER_REJECTED,
    ActionObservation,
    ActionSpec,
    ContractError,
    Destination,
    SessionKey,
    mask_known,
    validate_against_schema,
    validate_schema,
)
from core.admission import Work
from core.context import ChatEntry
from core.triggers import NORMALIZED_SCHEMA_VERSION, TriggerContext, TrustedClaim

MODULE_NAME = "streamerbot"

RUN_ACTION = "automation.action.run"
RUN_PROVIDER = "automation-bridge"
"""Provider name in bindings and traces."""

MANIFEST_PATH = Path(__file__).with_name("module.yaml")

#: The viewer read this module serves for the (``viewers``, P) pairs it owns
#: (R7c). Its contract is the ``users`` module's, read from that manifest.
USERS_READ_ACTION = "users.read"
USERS_MANIFEST_PATH = MANIFEST_PATH.parent.parent / "users" / "module.yaml"
#: What the bridge's pages list: the users the instance reports as active.
COVERAGE_ACTIVE_VIEWERS = "active_viewers"
#: The most users one fetched snapshot retains; the rest are counted in
#: ``coverage.evicted``, never kept (bounded retention).
VIEWER_SNAPSHOT_MAX_USERS = 1000

#: How much of the run action's fixed ``timeout_seconds`` the acknowledgement
#: deadline must leave free, so the executor's own timeout never pre-empts the
#: bridge's ``acknowledgement_lost`` cause (R4).
ACKNOWLEDGEMENT_MARGIN_SECONDS = 1.0

#: The platforms whose chat input the manifest lets this module own; owning
#: one requires a companion identity for it (R7, AC33).
CHAT_PLATFORMS = ("twitch", "kick", "youtube")
_CHAT_FAMILY = "chat"
_CUES_FAMILY = "cues"
_VIEWERS_FAMILY = "viewers"

_SCHEME_PLAIN = "ws"
_SCHEME_TLS = "wss"
_LOOPBACK_NAMES = frozenset({"localhost"})

_SETTING_URL = "url"
_SETTING_PASSWORD = "password"
_SETTING_COMPANION_NAME = "companion_name"
_SETTING_DEADLINES = "deadlines"
_SETTING_RECONNECT = "reconnect"
_SETTING_QUEUE_MAX_EVENTS = "queue_max_events"
_SETTING_ALLOWLIST = "allowlist"
_SETTING_COMPANION_IDENTITIES = "companion_identities"
_SETTING_INSTANCE_IDENTITY = "instance_identity"
_SETTING_TRUST_INSTANCE_ROLES = "trust_instance_roles"
#: The reserved setting the entry point hands its accepted ``limits`` block
#: over in (``core.main.LIMITS_KEY``). Accepted, never read.
_ACCEPTED_LIMITS_SETTING = "limits"
#: The test seam: the sleeper the reconnection backoff waits on. Read at
#: ``activate`` from code-built settings, never from a configuration file.
_SEAM_SLEEPER = "_sleeper"

_SETTINGS = frozenset(
    {
        _SETTING_URL,
        _SETTING_PASSWORD,
        _SETTING_COMPANION_NAME,
        _SETTING_DEADLINES,
        _SETTING_RECONNECT,
        _SETTING_QUEUE_MAX_EVENTS,
        _SETTING_ALLOWLIST,
        _SETTING_COMPANION_IDENTITIES,
        _SETTING_INSTANCE_IDENTITY,
        _SETTING_TRUST_INSTANCE_ROLES,
        _ACCEPTED_LIMITS_SETTING,
        _SEAM_SLEEPER,
    }
)
_DEADLINE_FIELDS = (
    "connect_seconds",
    "authentication_seconds",
    "request_seconds",
    "acknowledgement_seconds",
)
_ACKNOWLEDGEMENT_FIELD = "acknowledgement_seconds"
_MIN_DELAY_FIELD = "min_delay_seconds"
_MAX_DELAY_FIELD = "max_delay_seconds"
_RECONNECT_FIELDS = (_MIN_DELAY_FIELD, _MAX_DELAY_FIELD)
_ENTRY_KEYS = ("action", "group")
_ENTRY_ARGUMENTS = "arguments"


class AutomationBridgeError(RuntimeError):
    """A refused activation or preparation; the message carries no value."""


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #


def validate_settings(settings: Any, *, owned_inputs: Any = None) -> list[str]:
    """Check this module's settings; return one diagnostic per offending field.

    This is the hook ``settings_validator`` in the manifest names. Each
    diagnostic is ``module 'streamerbot': field 'X': reason`` and echoes no
    configured value. The schema requires nothing (R1, AC5); this hook is
    what refuses a missing ``url`` or ``companion_name`` (AC22).

    *owned_inputs* is this module's read-only ownership view (D7), handed
    over by the loader; ``None`` — a call outside the loader — skips the
    ownership-dependent check.

    A field path names configured keys, and a key may be the configured
    password itself. Called outside the loader, every diagnostic leaves
    through :func:`_redacted`, which masks the password out of its field and
    reason (R3, F4). The loader's call — the one that hands *owned_inputs* —
    gets the diagnostics unmasked: the loader holds every enabled module's
    credentials and applies them together, refusing any field that carries
    one. Masking the password first could cut an overlapping credential
    down to a fragment the loader no longer recognises.
    """

    if not isinstance(settings, Mapping):
        return [_setting_diagnostic("settings", "must be a mapping")]
    diagnostics = _settings_diagnostics(settings, owned_inputs)
    if owned_inputs is not None:
        return diagnostics
    return _redacted(diagnostics, settings)


def _redacted(diagnostics: list[str], settings: Mapping[str, Any]) -> list[str]:
    """*diagnostics* with the configured password masked by :func:`mask_known`.

    The module and the ``field`` label are this module's own text and stay
    as they are; what follows them — the field path and the reason — is
    masked, so the path keeps its skeleton (R3, F4).
    """

    password = settings.get(_SETTING_PASSWORD)
    credentials = (password,) if isinstance(password, str) and password else ()
    prefix = f"module {MODULE_NAME!r}: field "
    return [
        prefix + mask_known(entry[len(prefix) :], credentials)
        if entry.startswith(prefix)
        else mask_known(entry, credentials)
        for entry in diagnostics
    ]


def _settings_diagnostics(settings: Mapping[str, Any], owned_inputs: Any) -> list[str]:
    """Every diagnostic :func:`validate_settings` reports, before redaction."""

    diagnostics: list[str] = []

    for field_name in settings:
        if field_name not in _SETTINGS:
            diagnostics.append(
                _setting_diagnostic(str(field_name), "is not a setting this module declares")
            )

    url = settings.get(_SETTING_URL)
    if url is None:
        diagnostics.append(_setting_diagnostic(_SETTING_URL, "is required"))
    elif not _is_text(url):
        diagnostics.append(_setting_diagnostic(_SETTING_URL, "must be a non-empty string"))
    else:
        diagnostics.extend(_url_diagnostics(url))

    if _SEAM_SLEEPER in settings and not callable(settings[_SEAM_SLEEPER]):
        diagnostics.append(_setting_diagnostic(_SEAM_SLEEPER, "must be callable"))

    if _SETTING_PASSWORD in settings and not isinstance(settings[_SETTING_PASSWORD], str):
        diagnostics.append(_setting_diagnostic(_SETTING_PASSWORD, "must be a string"))

    companion_name = settings.get(_SETTING_COMPANION_NAME)
    if companion_name is None:
        diagnostics.append(_setting_diagnostic(_SETTING_COMPANION_NAME, "is required"))
    elif not _is_text(companion_name):
        diagnostics.append(
            _setting_diagnostic(_SETTING_COMPANION_NAME, "must be a non-empty string")
        )

    diagnostics.extend(_deadline_diagnostics(settings))
    diagnostics.extend(_reconnect_diagnostics(settings))

    if _SETTING_QUEUE_MAX_EVENTS in settings and not _is_positive_int(
        settings[_SETTING_QUEUE_MAX_EVENTS]
    ):
        diagnostics.append(
            _setting_diagnostic(_SETTING_QUEUE_MAX_EVENTS, "must be a positive integer")
        )

    if _SETTING_ALLOWLIST in settings:
        diagnostics.extend(_allowlist_diagnostics(settings[_SETTING_ALLOWLIST]))

    identities_valid = True
    for name in (_SETTING_COMPANION_IDENTITIES, _SETTING_INSTANCE_IDENTITY):
        if name in settings:
            found = _identity_diagnostics(name, settings[name])
            diagnostics.extend(found)
            if found and name == _SETTING_COMPANION_IDENTITIES:
                identities_valid = False

    if _SETTING_TRUST_INSTANCE_ROLES in settings and not isinstance(
        settings[_SETTING_TRUST_INSTANCE_ROLES], bool
    ):
        diagnostics.append(
            _setting_diagnostic(_SETTING_TRUST_INSTANCE_ROLES, "must be a boolean")
        )

    if owned_inputs is not None and identities_valid:
        identities = settings.get(_SETTING_COMPANION_IDENTITIES) or {}
        for platform in CHAT_PLATFORMS:
            if owned_inputs.owns(_CHAT_FAMILY, platform) and platform not in identities:
                diagnostics.append(
                    _setting_diagnostic(
                        f"{_SETTING_COMPANION_IDENTITIES}.{platform}",
                        f"is required: this module owns the chat input of platform {platform}",
                    )
                )
    return diagnostics


def _url_diagnostics(url: str) -> list[str]:
    """The R6 rule on ``url``: ``ws`` on loopback only, ``wss`` anywhere.

    Mirrors the rule of ``modules.agent_link`` (whose helpers are private).
    """

    try:
        parts = urlsplit(url.strip())
        host = parts.hostname
    except ValueError:
        return [_setting_diagnostic(_SETTING_URL, "is not a valid URL")]
    if parts.scheme not in (_SCHEME_PLAIN, _SCHEME_TLS):
        return [_setting_diagnostic(_SETTING_URL, "must use the ws or wss scheme")]
    if not host:
        return [_setting_diagnostic(_SETTING_URL, "must name a host")]
    if parts.scheme == _SCHEME_PLAIN and not _is_loopback(host):
        return [
            _setting_diagnostic(
                _SETTING_URL, "must use wss unless the host is a loopback address"
            )
        ]
    return []


def _deadline_diagnostics(settings: Mapping[str, Any]) -> list[str]:
    if _SETTING_DEADLINES not in settings:
        return []
    deadlines = settings[_SETTING_DEADLINES]
    if not isinstance(deadlines, Mapping):
        return [_setting_diagnostic(_SETTING_DEADLINES, "must be a mapping")]
    diagnostics: list[str] = []
    for key in deadlines:
        label = f"{_SETTING_DEADLINES}.{key}"
        if key not in _DEADLINE_FIELDS:
            diagnostics.append(_setting_diagnostic(label, "is not a deadlines field"))
        elif not _is_positive_number(deadlines[key]):
            diagnostics.append(_setting_diagnostic(label, "must be a finite positive number"))
        elif key == _ACKNOWLEDGEMENT_FIELD:
            bound = run_action_timeout_seconds() - ACKNOWLEDGEMENT_MARGIN_SECONDS
            if deadlines[key] > bound:
                diagnostics.append(
                    _setting_diagnostic(
                        label,
                        "must leave the run action's timeout a margin of "
                        f"{ACKNOWLEDGEMENT_MARGIN_SECONDS:g} second(s)",
                    )
                )
    return diagnostics


def _reconnect_diagnostics(settings: Mapping[str, Any]) -> list[str]:
    if _SETTING_RECONNECT not in settings:
        return []
    reconnect = settings[_SETTING_RECONNECT]
    if not isinstance(reconnect, Mapping):
        return [_setting_diagnostic(_SETTING_RECONNECT, "must be a mapping")]
    diagnostics: list[str] = []
    valid: dict[str, float] = {}
    for key in reconnect:
        label = f"{_SETTING_RECONNECT}.{key}"
        if key not in _RECONNECT_FIELDS:
            diagnostics.append(_setting_diagnostic(label, "is not a reconnect field"))
        elif not _is_positive_number(reconnect[key]):
            diagnostics.append(_setting_diagnostic(label, "must be a finite positive number"))
        else:
            valid[key] = reconnect[key]
    if any(key in reconnect and key not in valid for key in _RECONNECT_FIELDS):
        return diagnostics
    # The bounds are compared after the defaults apply: an omitted field is
    # its default, and a configured maximum is never raised silently (R6).
    minimum = valid.get(_MIN_DELAY_FIELD, _DEFAULT_MIN_DELAY_SECONDS)
    maximum = valid.get(_MAX_DELAY_FIELD, _DEFAULT_MAX_DELAY_SECONDS)
    if minimum > maximum:
        if _MIN_DELAY_FIELD in valid:
            diagnostics.append(
                _setting_diagnostic(
                    f"{_SETTING_RECONNECT}.{_MIN_DELAY_FIELD}",
                    f"must not exceed {_MAX_DELAY_FIELD}",
                )
            )
        else:
            diagnostics.append(
                _setting_diagnostic(
                    f"{_SETTING_RECONNECT}.{_MAX_DELAY_FIELD}",
                    f"must not be below {_MIN_DELAY_FIELD} "
                    f"(default {_DEFAULT_MIN_DELAY_SECONDS:g})",
                )
            )
    return diagnostics


def _allowlist_diagnostics(allowlist: Any) -> list[str]:
    """Each entry names exactly one action or one group, never twice (R5, AC45)."""

    if isinstance(allowlist, (str, bytes, Mapping)) or not isinstance(allowlist, Sequence):
        return [_setting_diagnostic(_SETTING_ALLOWLIST, "must be a list of entries")]
    diagnostics: list[str] = []
    seen: dict[str, set[str]] = {key: set() for key in _ENTRY_KEYS}
    for index, entry in enumerate(allowlist):
        label = f"{_SETTING_ALLOWLIST}[{index}]"
        if not isinstance(entry, Mapping):
            diagnostics.append(_setting_diagnostic(label, "must be a mapping"))
            continue
        unknown = [key for key in entry if key not in (*_ENTRY_KEYS, _ENTRY_ARGUMENTS)]
        for key in unknown:
            diagnostics.append(
                _setting_diagnostic(f"{label}.{key}", "is not an allowlist entry field")
            )
        named = [key for key in _ENTRY_KEYS if key in entry]
        if len(named) != 1:
            diagnostics.append(
                _setting_diagnostic(label, "must name exactly one of action or group")
            )
        else:
            key = named[0]
            value = entry[key]
            if not _is_text(value):
                diagnostics.append(
                    _setting_diagnostic(f"{label}.{key}", "must be a non-empty string")
                )
            elif value in seen[key]:
                diagnostics.append(
                    _setting_diagnostic(
                        f"{label}.{key}", f"duplicates an earlier {key} entry"
                    )
                )
            else:
                seen[key].add(value)
        if _ENTRY_ARGUMENTS in entry:
            try:
                validate_schema(entry[_ENTRY_ARGUMENTS], label=_ENTRY_ARGUMENTS)
            except ContractError:
                diagnostics.append(
                    _setting_diagnostic(
                        f"{label}.{_ENTRY_ARGUMENTS}", "is not a supported schema"
                    )
                )
    return diagnostics


def _identity_diagnostics(name: str, identities: Any) -> list[str]:
    """A platform → account identifier mapping, both sides non-empty text.

    A companion identity holding ``:`` is refused: that separator is reserved
    for the ``instance:<platform>`` author of a cue without a user, which must
    never be taken for the companion (R7a).
    """

    if not isinstance(identities, Mapping):
        return [_setting_diagnostic(name, "must be a mapping of platform to identifier")]
    diagnostics: list[str] = []
    for platform, identity in identities.items():
        if not _is_text(platform):
            diagnostics.append(_setting_diagnostic(name, "platform names must be non-empty"))
        elif not _is_text(identity):
            diagnostics.append(
                _setting_diagnostic(f"{name}.{platform}", "must be a non-empty string")
            )
        elif name == _SETTING_COMPANION_IDENTITIES and _RESERVED_SEPARATOR in identity:
            diagnostics.append(
                _setting_diagnostic(
                    f"{name}.{platform}", f"must not contain {_RESERVED_SEPARATOR!r}"
                )
            )
    return diagnostics


def _setting_diagnostic(field_name: str, reason: str) -> str:
    return f"module {MODULE_NAME!r}: field {field_name!r}: {reason}"


def _is_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def _is_positive_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value > 0
    )


def _is_loopback(host: str) -> bool:
    """Whether *host* names the local machine only (R6 TLS rule)."""

    candidate = host.strip().lower()
    if candidate in _LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(candidate.strip("[]")).is_loopback
    except ValueError:
        return False


# --------------------------------------------------------------------------- #
# The run action's declared contract
# --------------------------------------------------------------------------- #


def _declared_entry(path: Path, name: str) -> Mapping[str, Any]:
    manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
    return next(
        item
        for item in manifest["actions"]
        if isinstance(item, Mapping) and item.get("name") == name
    )


def _declared_run_entry() -> Mapping[str, Any]:
    return _declared_entry(MANIFEST_PATH, RUN_ACTION)


def _spec_from_entry(entry: Mapping[str, Any]) -> ActionSpec:
    """One manifest ``actions`` entry as the :class:`ActionSpec` it declares."""

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


def run_action_timeout_seconds() -> float:
    """The run action's fixed ``timeout_seconds``, read from the manifest."""

    return float(_declared_run_entry()["timeout_seconds"])


def declared_run_spec() -> ActionSpec:
    """Build the run action's contract from the colocated manifest (R4).

    The spec registered at ``prepare`` must equal the one the loader declared
    at discovery, field for field: reading the same file keeps the two from
    drifting, and the registry refuses a redeclaration that differs.
    """

    try:
        return _spec_from_entry(_declared_run_entry())
    except Exception:
        raise AutomationBridgeError(
            f"{MODULE_NAME} prepare: manifest declaration of {RUN_ACTION!r} is invalid"
        ) from None


def declared_users_read_spec() -> ActionSpec:
    """Build ``users.read`` from the ``users`` manifest itself (R7c).

    The action is shared: the ``users`` module declares it from the same
    file, so the two specs are equal field for field and the registry merges
    the second declaration instead of refusing it. Nothing of the contract is
    copied here.
    """

    try:
        return _spec_from_entry(_declared_entry(USERS_MANIFEST_PATH, USERS_READ_ACTION))
    except Exception:
        raise AutomationBridgeError(
            f"{MODULE_NAME} prepare: manifest declaration of {USERS_READ_ACTION!r} is invalid"
        ) from None


# --------------------------------------------------------------------------- #
# The connection (R6)
# --------------------------------------------------------------------------- #

#: The five value-free reasons a connection attempt reports (R1).
REASON_UNREACHABLE = "unreachable"
REASON_AUTHENTICATION_FAILED = "authentication_failed"
REASON_AUTHENTICATION_REQUIRED = "authentication_required"
REASON_PROTOCOL_ERROR = "protocol_error"
REASON_INVENTORY_UNAVAILABLE = "inventory_unavailable"
DEGRADED_REASONS = (
    REASON_UNREACHABLE,
    REASON_AUTHENTICATION_FAILED,
    REASON_AUTHENTICATION_REQUIRED,
    REASON_PROTOCOL_ERROR,
    REASON_INVENTORY_UNAVAILABLE,
)

#: The manifest's declared defaults, applied when a field is absent.
_DEFAULT_DEADLINES = {
    "connect_seconds": 5.0,
    "authentication_seconds": 5.0,
    "request_seconds": 5.0,
    "acknowledgement_seconds": 10.0,
}
_DEFAULT_MIN_DELAY_SECONDS = 1.0
_DEFAULT_MAX_DELAY_SECONDS = 60.0

_OWNER_TASK = "automation-bridge.connection"
_CONSUMER_TASK = "automation-bridge.events"
_DEFAULT_QUEUE_MAX_EVENTS = 256

#: How long ``start_inputs`` waits for the first attempt to settle. Well
#: inside the coordinator's default hook timeout (60 s), whatever deadlines
#: are configured: an instance that withholds its answers leaves the bridge
#: degraded instead of exhausting the startup budget (R1).
_STARTUP_ATTEMPT_SECONDS = 10.0

_REQUEST_HELLO = "Hello"
_REQUEST_AUTHENTICATE = "Authenticate"
_REQUEST_GET_ACTIONS = "GetActions"
_REQUEST_DO_ACTION = "DoAction"
_REQUEST_SUBSCRIBE = "Subscribe"
_REQUEST_GET_ACTIVE_VIEWERS = "GetActiveViewers"
_STATUS_OK = "ok"
_STATUS_ERROR = "error"

Sleeper = Callable[[float], Awaitable[Any]]


class ConnectionClosed(Exception):
    """A request whose connection ended before its answer arrived.

    The generic outcome every pending request future is failed with when the
    connection is lost or the bridge shuts down; the run waiter (P15) maps it
    to ``acknowledgement_lost`` (D8). The message carries no value.
    """

    def __init__(self) -> None:
        super().__init__(f"{MODULE_NAME}: the connection to the instance is closed")


class _HandshakeFailure(Exception):
    """One connection attempt that ended with a degraded *reason*."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class InstanceAction:
    """One action of the instance's inventory, as ``GetActions`` lists it."""

    id: str
    name: str
    group: str | None
    enabled: bool


Inventory = Mapping[str, tuple[InstanceAction, ...]]


def authentication_response(password: str, salt: str, challenge: str) -> str:
    """The instance's documented challenge answer (R6, AC24).

    ``base64(sha256(base64(sha256(password + salt)) + challenge))``.
    """

    secret = base64.b64encode(hashlib.sha256((password + salt).encode("utf-8")).digest())
    answer = hashlib.sha256(secret.decode("ascii").encode("utf-8") + challenge.encode("utf-8"))
    return base64.b64encode(answer.digest()).decode("ascii")


def _parse_inventory(answer: Any) -> Inventory | None:
    """Name → entries of a ``GetActions`` answer; ``None`` when malformed."""

    if not isinstance(answer, Mapping) or answer.get("status") != _STATUS_OK:
        return None
    actions = answer.get("actions")
    if not isinstance(actions, list):
        return None
    inventory: dict[str, list[InstanceAction]] = {}
    for item in actions:
        if not isinstance(item, Mapping):
            return None
        identifier, name = item.get("id"), item.get("name")
        group, enabled = item.get("group"), item.get("enabled")
        if not _is_text(identifier) or not _is_text(name) or not isinstance(enabled, bool):
            return None
        if group is not None and not isinstance(group, str):
            return None
        inventory.setdefault(name, []).append(
            InstanceAction(id=identifier, name=name, group=group or None, enabled=enabled)
        )
    return MappingProxyType({name: tuple(entries) for name, entries in inventory.items()})


class _Connection:
    """One open WebSocket, its reader and its pending requests.

    The reader is the only consumer of the socket: it resolves each answer
    to the future registered under its request id and hands every other
    frame to *on_event*. When the socket ends, for any reason, every pending
    future fails with :class:`ConnectionClosed` and :attr:`lost` is set.
    """

    def __init__(self, ws: Any, on_event: Callable[[Mapping[str, Any]], None]) -> None:
        self.ws = ws
        self.lost = asyncio.Event()
        self._on_event = on_event
        self._pending: dict[str, asyncio.Future[Any]] = {}
        self._ids = itertools.count(1)
        self.reader: asyncio.Task[None] = asyncio.ensure_future(self._read())

    @property
    def pending(self) -> int:
        return len(self._pending)

    def new_request_id(self, suffix: str | None = None) -> str:
        """A request id unique on this connection, optionally tagged."""

        request_id = f"{RUN_PROVIDER}-{next(self._ids)}"
        return request_id if suffix is None else f"{request_id}.{suffix}"

    def register(self, request_id: str) -> asyncio.Future[Any]:
        """The future the reader resolves with the answer to *request_id*."""

        answer: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = answer
        return answer

    def forget(self, request_id: str) -> None:
        """Stop waiting for *request_id*; a late answer to it is dropped."""

        answer = self._pending.pop(request_id, None)
        if answer is not None and not answer.done():
            answer.cancel()

    def abandon(self) -> None:
        """Give the connection up without a loop turn: fail what waits and
        mark it lost, so its owner closes it and reports the loss."""

        self.fail_pending()
        self.lost.set()

    async def request(self, frame: Mapping[str, Any], deadline_seconds: float) -> Any:
        """Send *frame* under a fresh id and return its answer.

        Raises :class:`ConnectionClosed` when the connection ends first and
        :class:`asyncio.TimeoutError` past *deadline_seconds*.
        """

        if self.lost.is_set() or self.ws.closed:
            raise ConnectionClosed()
        request_id = self.new_request_id()
        answer = self.register(request_id)
        try:
            try:
                await self.ws.send_str(json.dumps({**frame, "id": request_id}))
            except (ConnectionError, aiohttp.ClientError, RuntimeError):
                raise ConnectionClosed() from None
            return await asyncio.wait_for(answer, deadline_seconds)
        finally:
            self._pending.pop(request_id, None)

    def fail_pending(self) -> None:
        """Fail every request still waiting; none is left hanging."""

        pending, self._pending = self._pending, {}
        for answer in pending.values():
            if not answer.done():
                answer.set_exception(ConnectionClosed())

    async def close(self) -> None:
        """Fail what waits, close the socket and stop the reader."""

        self.fail_pending()
        try:
            await self.ws.close()
        finally:
            if not self.reader.done():
                self.reader.cancel()
            await asyncio.gather(self.reader, return_exceptions=True)
            self.lost.set()

    async def _read(self) -> None:
        try:
            async for message in self.ws:
                if message.type is not aiohttp.WSMsgType.TEXT:
                    continue
                try:
                    frame = json.loads(message.data)
                except ValueError:
                    continue
                if not isinstance(frame, Mapping):
                    continue
                request_id = frame.get("id")
                if isinstance(request_id, str) and "event" not in frame:
                    answer = self._pending.get(request_id)
                    if answer is not None and not answer.done():
                        answer.set_result(frame)
                    # A late answer to an abandoned request is dropped.
                    continue
                self._on_event(frame)
        except (ConnectionError, aiohttp.ClientError):
            pass
        finally:
            self.fail_pending()
            self.lost.set()


# --------------------------------------------------------------------------- #
# Subscriptions and cues (R6, R7)
# --------------------------------------------------------------------------- #

#: The instance's custom-event category: the one cues arrive in.
_CUSTOM_SOURCE = "Custom"
_CUSTOM_TYPE = "Event"

#: Each chat platform's event source, as the instance spells it.
_PLATFORM_SOURCES = MappingProxyType(
    {"twitch": "Twitch", "kick": "Kick", "youtube": "YouTube"}
)
#: Each chat platform's chat-message event type first, then its documented
#: notice types (the fixtures README's enumerated list).
_CHAT_EVENT_TYPES = MappingProxyType(
    {
        "twitch": ("ChatMessage", "Sub", "ReSub", "GiftSub", "GiftBomb", "Raid", "Follow"),
        "kick": (
            "ChatMessage",
            "Follow",
            "Subscription",
            "Resubscription",
            "GiftSubscription",
        ),
        "youtube": (
            "Message",
            "NewSponsor",
            "MemberMileStone",
            "MembershipGift",
            "SuperChat",
            "SuperSticker",
        ),
    }
)

#: The cue envelope's bounds (R7a).
CUE_ID_MAX_CHARACTERS = 128
CUE_TEXT_MAX_CODE_POINTS = 500
#: The author prefix of a cue without a user; ``:`` is refused in every
#: platform account id, so the identity is never a viewer's or the companion's.
INSTANCE_AUTHOR_PREFIX = "instance:"
_RESERVED_SEPARATOR = ":"
_CHAT_EVENT = "channel.chat.message"


def subscription_events(inputs: Any) -> dict[str, list[str]]:
    """The categories the ``Subscribe`` request names, from the owned pairs.

    Cues owned on any platform → the custom-event category only; each owned
    (``chat``, P) → P's chat-message and notice categories only; viewers are
    pulled on demand and need none (R6, AC27).
    """

    events: dict[str, list[str]] = {}
    if inputs.platforms(_CUES_FAMILY):
        events[_CUSTOM_SOURCE] = [_CUSTOM_TYPE]
    for platform in CHAT_PLATFORMS:
        if inputs.owns(_CHAT_FAMILY, platform):
            events[_PLATFORM_SOURCES[platform]] = list(_CHAT_EVENT_TYPES[platform])
    return events


def _is_cue_frame(frame: Mapping[str, Any]) -> bool:
    event = frame.get("event")
    return (
        isinstance(event, Mapping)
        and event.get("source") == _CUSTOM_SOURCE
        and event.get("type") == _CUSTOM_TYPE
    )


def cue_event(
    frame: Mapping[str, Any], instance_identity: Mapping[str, str]
) -> dict[str, Any] | None:
    """The normalised event of one custom-event frame, or ``None`` (R7a).

    The envelope is the frame's ``data.args``. It is checked strictly and
    never repaired: a missing, empty or overlong ``cue_id``, a missing
    ``platform``, a ``channel_id`` neither given nor configured, a
    ``user_name`` without ``user_id`` (or the reverse), a ``user_id`` holding
    ``:``, or a ``text`` that is not a string of at most 500 code points
    gives ``None``.
    """

    if not _is_cue_frame(frame):
        return None
    data = frame.get("data")
    envelope = data.get("args") if isinstance(data, Mapping) else None
    if not isinstance(envelope, Mapping):
        return None
    cue_id = envelope.get("cue_id")
    if not _is_text(cue_id) or len(cue_id) > CUE_ID_MAX_CHARACTERS:
        return None
    platform = envelope.get("platform")
    if not _is_text(platform):
        return None
    channel_id = envelope.get("channel_id", instance_identity.get(platform))
    if not _is_text(channel_id):
        return None
    text = envelope.get("text")
    if not isinstance(text, str) or len(text) > CUE_TEXT_MAX_CODE_POINTS:
        return None
    user_id, user_name = envelope.get("user_id"), envelope.get("user_name")
    if user_id is None and user_name is None:
        author = {"id": f"{INSTANCE_AUTHOR_PREFIX}{platform}"}
    elif (
        _is_text(user_id)
        and _is_text(user_name)
        and _RESERVED_SEPARATOR not in user_id
    ):
        author = {"id": user_id, "display_name": user_name}
    else:
        return None
    return {
        "type": _CHAT_EVENT,
        "payload": {
            "platform": platform,
            "channel_id": channel_id,
            "author": author,
            "message_id": cue_id,
            "text": text,
            "kind": CUE_KIND,
        },
        "metadata": {"source": MODULE_NAME, "schema_version": NORMALIZED_SCHEMA_VERSION},
    }

# --------------------------------------------------------------------------- #
# Chat mapping for owned platforms (R7b)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class _ChatMapping:
    """How one documented instance event becomes one normalised event.

    ``author`` names the ``data`` key holding the author user; ``message_id``
    the key of the event's own identifier, ``None`` when the instance
    documents none (the identifier is then derived from the frame);
    ``text`` the key of the text, ``None`` when the event carries none (the
    text is then empty, as the native notice without one); ``text_optional``
    whether that text is a viewer's optional comment (absent, it is empty)
    rather than a required field (absent, the event is dropped); ``channel`` the
    path to the broadcaster's account id, ``None`` when the event carries
    none (the ``instance_identity`` of the platform is used).
    """

    kind: str
    author: str
    message_id: str | None
    text: str | None
    channel: tuple[str, ...] | None
    text_optional: bool = False


_TWITCH_CHANNEL = ("broadcaster", "id")
_YOUTUBE_CHANNEL = ("broadcast", "channelId")

#: The documented chat-message and notice events, keyed by (platform,
#: instance event type), each with the native kind it maps to (the fixtures
#: README's enumerated list). Anything else is dropped and counted (R7b).
_CHAT_MAPPINGS: Mapping[tuple[str, str], _ChatMapping] = MappingProxyType(
    {
        ("twitch", "ChatMessage"): _ChatMapping(
            EVENT_KIND_DEFAULT, "user", "messageId", "text", _TWITCH_CHANNEL
        ),
        ("twitch", "Sub"): _ChatMapping(
            "sub", "user", "messageId", "systemMessage", _TWITCH_CHANNEL
        ),
        ("twitch", "ReSub"): _ChatMapping(
            "resub", "user", "messageId", "systemMessage", _TWITCH_CHANNEL
        ),
        ("twitch", "GiftSub"): _ChatMapping(
            "sub_gift", "user", "messageId", "systemMessage", _TWITCH_CHANNEL
        ),
        ("twitch", "GiftBomb"): _ChatMapping(
            "community_sub_gift", "user", "messageId", "systemMessage", _TWITCH_CHANNEL
        ),
        ("twitch", "Raid"): _ChatMapping("raid", "raider", None, None, _TWITCH_CHANNEL),
        ("twitch", "Follow"): _ChatMapping("follow", "targetUser", None, None, _TWITCH_CHANNEL),
        ("kick", "ChatMessage"): _ChatMapping(
            EVENT_KIND_DEFAULT, "user", "messageId", "text", None
        ),
        ("kick", "Follow"): _ChatMapping("follow", "user", None, None, None),
        ("kick", "Subscription"): _ChatMapping("sub", "user", None, None, None),
        ("kick", "Resubscription"): _ChatMapping("resub", "user", None, None, None),
        ("kick", "GiftSubscription"): _ChatMapping("sub_gift", "user", None, None, None),
        ("youtube", "Message"): _ChatMapping(
            EVENT_KIND_DEFAULT, "user", "eventId", "message", _YOUTUBE_CHANNEL
        ),
        ("youtube", "NewSponsor"): _ChatMapping(
            "sub", "user", "eventId", None, _YOUTUBE_CHANNEL
        ),
        ("youtube", "MemberMileStone"): _ChatMapping(
            "resub", "user", "eventId", "message", _YOUTUBE_CHANNEL, text_optional=True
        ),
        ("youtube", "MembershipGift"): _ChatMapping(
            "sub_gift", "user", "eventId", None, _YOUTUBE_CHANNEL
        ),
        ("youtube", "SuperChat"): _ChatMapping(
            "tip", "user", "eventId", "message", _YOUTUBE_CHANNEL, text_optional=True
        ),
        ("youtube", "SuperSticker"): _ChatMapping(
            "tip", "user", "eventId", None, _YOUTUBE_CHANNEL
        ),
    }
)

#: The instance's badge names mapped to the claim each attests (twitch, kick).
_BADGE_CLAIMS: Mapping[str, str] = MappingProxyType(
    {
        "broadcaster": "broadcaster",
        "moderator": "moderator",
        "subscriber": "subscription",
        "founder": "subscription",
        "vip": "vip",
    }
)
#: The instance's user flags mapped to the claim each attests (youtube).
_FLAG_CLAIMS: tuple[tuple[str, str], ...] = (
    ("isOwner", "broadcaster"),
    ("isModerator", "moderator"),
    ("isSponsor", "subscription"),
)
#: Where each platform's instance user reports its roles.
_ROLES_FROM_BADGES = "badges"
_ROLES_FROM_FLAGS = "flags"
_ROLE_SOURCES: Mapping[str, str] = MappingProxyType(
    {"twitch": _ROLES_FROM_BADGES, "kick": _ROLES_FROM_BADGES, "youtube": _ROLES_FROM_FLAGS}
)
_DERIVED_ID_LENGTH = 32


def _chat_platform(frame: Mapping[str, Any]) -> tuple[str, str] | None:
    """The (platform, instance event type) a frame's envelope names."""

    event = frame.get("event")
    if not isinstance(event, Mapping):
        return None
    source, event_type = event.get("source"), event.get("type")
    for platform, spelled in _PLATFORM_SOURCES.items():
        if source == spelled and isinstance(event_type, str):
            return platform, event_type
    return None


def _identifier(value: Any) -> str | None:
    """An account or event id as text: an integer, or a non-blank string."""

    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value)
    return value if _is_text(value) else None


def _channel_at(data: Mapping[str, Any], path: tuple[str, ...] | None) -> str | None:
    """The broadcaster's account id at *path* in *data*, when documented."""

    if path is None:
        return None
    value: Any = data
    for key in path:
        value = value.get(key) if isinstance(value, Mapping) else None
    return _identifier(value)


def _derived_message_id(frame: Mapping[str, Any]) -> str | None:
    """A stable id for an event the instance documents without one.

    The digest of the whole frame: the same frame sent again is the same
    event (the engine's dedup window then applies), and two events differ by
    their timestamp or their author.
    """

    try:
        canonical = json.dumps(frame, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        return None
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"{MODULE_NAME}-{digest[:_DERIVED_ID_LENGTH]}"


def _instance_claims(
    platform: str, user: Mapping[str, Any], author_id: str, channel_id: str
) -> tuple[tuple[TrustedClaim, ...], bool]:
    """The role claims the instance reports for *user*, and whether it
    reported any role source at all (a badge list, the user flags, or the
    author being the broadcaster). Every claim's provenance is this module."""

    claims: list[TrustedClaim] = []
    names: set[str] = set()

    def claim(name: str) -> None:
        if name not in names:
            names.add(name)
            claims.append(TrustedClaim(name, provenance=MODULE_NAME))

    attested = author_id == channel_id
    if attested:
        claim("broadcaster")
    if _ROLE_SOURCES[platform] == _ROLES_FROM_FLAGS:
        if any(flag in user for flag, _ in _FLAG_CLAIMS):
            attested = True
        for flag, name in _FLAG_CLAIMS:
            if user.get(flag) is True:
                claim(name)
    else:
        badges = user.get(_ROLES_FROM_BADGES)
        if isinstance(badges, (list, tuple)):
            attested = True
            for badge in badges:
                badge_name = badge.get("name") if isinstance(badge, Mapping) else None
                name = _BADGE_CLAIMS.get(badge_name) if isinstance(badge_name, str) else None
                if name is not None:
                    claim(name)
    return tuple(claims), attested


def chat_event(
    frame: Mapping[str, Any],
    instance_identity: Mapping[str, str],
    *,
    trust_roles: bool = False,
) -> tuple[dict[str, Any], tuple[TrustedClaim, ...]] | None:
    """The normalised event of one chat or notice frame and its trusted
    claims, or ``None`` (R7b).

    The frame's (platform, event type) must be in :data:`_CHAT_MAPPINGS`.
    The payload has exactly the native input's keys — ``platform``,
    ``channel_id``, ``author``, ``message_id``, ``text``, and ``kind`` only
    for a notice. A documented field the mapping needs and the frame lacks —
    the author, its id, the chat or required notice text, the channel with no
    ``instance_identity`` for the platform — gives ``None``, as does an
    author id holding ``:`` or an anonymous gift (no author to map). Role
    claims are returned, and ``author.roles`` with ``roles_provenance`` =
    this module emitted, only when *trust_roles* is true and the instance
    reported a role source; otherwise there are none.
    """

    named = _chat_platform(frame)
    mapping = _CHAT_MAPPINGS.get(named) if named is not None else None
    data = frame.get("data")
    if mapping is None or not isinstance(data, Mapping):
        return None
    platform = named[0]
    if data.get("isAnonymous") is True:
        return None
    user = data.get(mapping.author)
    if not isinstance(user, Mapping):
        return None
    author_id = _identifier(user.get("id"))
    if author_id is None or _RESERVED_SEPARATOR in author_id:
        return None
    channel_id = _channel_at(data, mapping.channel) or instance_identity.get(platform)
    if not _is_text(channel_id):
        return None
    if mapping.message_id is None:
        message_id = _derived_message_id(frame)
    else:
        message_id = _identifier(data.get(mapping.message_id))
    if message_id is None:
        return None
    text = data.get(mapping.text) if mapping.text is not None else ""
    if not isinstance(text, str):
        if not mapping.text_optional:
            return None
        text = ""

    author: dict[str, Any] = {"id": author_id}
    if _is_text(user.get("name")):
        author["display_name"] = user["name"]
    claims: tuple[TrustedClaim, ...] = ()
    if trust_roles:
        claims, attested = _instance_claims(platform, user, author_id, channel_id)
        if attested:
            author["roles"] = [claim.name for claim in claims]
            author["roles_provenance"] = MODULE_NAME
    payload: dict[str, Any] = {
        "platform": platform,
        "channel_id": channel_id,
        "author": author,
        "message_id": message_id,
        "text": text,
    }
    if mapping.kind != EVENT_KIND_DEFAULT:
        payload["kind"] = mapping.kind
    event = {
        "type": _CHAT_EVENT,
        "payload": payload,
        "metadata": {"source": MODULE_NAME, "schema_version": NORMALIZED_SCHEMA_VERSION},
    }
    return event, claims


# --------------------------------------------------------------------------- #
# The handle
# --------------------------------------------------------------------------- #


#: The trace reconciliation emits at every successful connection (R5).
TRACE_ALLOWLIST_RECONCILED = "automation.allowlist.reconciled"

#: The R4 refusal codes the provider decides before any frame.
ERROR_NOT_ALLOWLISTED = "not_allowlisted"
ERROR_INSTANCE_ACTION_MISSING = "instance_action_missing"
ERROR_INSTANCE_ACTION_AMBIGUOUS = "instance_action_ambiguous"
ERROR_INSTANCE_ACTION_DISABLED = "instance_action_disabled"

#: The R4 causes of the outcomes that follow a sent frame.
CAUSE_EXECUTION_UNCONFIRMED = "execution_unconfirmed"
CAUSE_INSTANCE_REJECTED = "instance_rejected"
CAUSE_ACKNOWLEDGEMENT_LOST = "acknowledgement_lost"

#: What every uncertain outcome says in words (R4).
UNCERTAINTY_SENTENCE = "the companion does not know whether the action ran"


@dataclass(frozen=True, slots=True)
class AllowlistEntry:
    """One configured allowlist entry: an action or a group, and its schema.

    ``schema`` is ``None`` when the entry declares no ``arguments`` schema;
    such an entry accepts only an empty ``arguments`` object (R5).
    """

    kind: str
    name: str
    schema: Mapping[str, Any] | None


def _parse_allowlist(value: Any) -> tuple[AllowlistEntry, ...]:
    """The validated ``allowlist`` setting as entries, in configured order."""

    entries: list[AllowlistEntry] = []
    for item in value or ():
        kind = next(key for key in _ENTRY_KEYS if key in item)
        entries.append(
            AllowlistEntry(kind=kind, name=item[kind], schema=item.get(_ENTRY_ARGUMENTS))
        )
    return tuple(entries)


def _in_group(action: InstanceAction, group: str) -> bool:
    """Whether the instance files *action* under *group*."""

    return action.group == group


def _plain(value: Any) -> Any:
    """A frozen argument value as plain JSON-serialisable containers."""

    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _run_provenance() -> dict[str, Any]:
    return {"provider": RUN_PROVIDER, "route": RUN_ACTION}


def _refused(code: str, message: str) -> ActionObservation:
    return ActionObservation(
        status="refused",
        provenance=_run_provenance(),
        error={"code": code, "message": f"{MODULE_NAME}: {message}", "retryable": False},
    )


def _not_ready() -> ActionObservation:
    return _refused(ERROR_PROVIDER_NOT_READY, "the automation instance is not connected")


def _uncertain(cause: str, message: str, *, status: str = "external_unknown") -> ActionObservation:
    """An outcome after the frame was handed over: never a success (R4).

    *status* is ``external_unknown`` for an outcome the provider settles
    itself, ``cancelled`` for its interruption record — which the executor
    resolves to ``external_unknown`` through the emission rule, keeping this
    cause and message (D8; the clips precedent).
    """

    return ActionObservation(
        status=status,
        provenance=_run_provenance(),
        error={
            "code": ERROR_EXTERNAL_UNKNOWN if status == "external_unknown" else ERROR_CANCELLED,
            "message": f"{MODULE_NAME}: {message}; {UNCERTAINTY_SENTENCE}",
            "retryable": False,
            "cause": cause,
        },
    )


def _acknowledgement_lost(*, cancelled: bool = False) -> ActionObservation:
    if cancelled:
        return _uncertain(
            CAUSE_ACKNOWLEDGEMENT_LOST,
            "the call was cancelled after the request was sent",
            status="cancelled",
        )
    return _uncertain(
        CAUSE_ACKNOWLEDGEMENT_LOST,
        "the request was sent and no acknowledgement arrived",
    )


class _RunProvider:
    """The run action's provider: the R4 outcome table, one frame at most.

    Every decision is taken before any frame (R5). The transmission segment
    — the pre-write check, ``mark_emitted()`` and the ``ws.send_str`` call —
    contains no ``await`` before the transport write (D8); a static test of
    this method's source keeps it that way.
    """

    __slots__ = ("_bridge",)

    name = RUN_PROVIDER

    def __init__(self, bridge: Any) -> None:
        self._bridge = bridge

    async def invoke(self, invocation: Any) -> ActionObservation:
        invocation.mark_not_emitted()
        bridge = self._bridge
        connection = bridge._connection
        if connection is None or not bridge.connected:
            return _not_ready()
        decided = bridge._decide(invocation.call.arguments)
        if isinstance(decided, ActionObservation):
            return decided
        instance_action, arguments = decided
        request_id = connection.new_request_id(str(invocation.call.call_id))
        try:
            frame = json.dumps(
                {
                    "request": _REQUEST_DO_ACTION,
                    "action": {"id": instance_action.id},
                    "args": _plain(arguments),
                    "id": request_id,
                }
            )
        except (TypeError, ValueError):
            return ActionObservation(
                status="error",
                provenance=_run_provenance(),
                error={
                    "code": ERROR_INVALID_ARGUMENTS,
                    "message": f"{MODULE_NAME}: the arguments cannot be serialised",
                    "retryable": False,
                },
            )

        # Before the boundary: a deadline or a cancellation here leaves the
        # call ``not_emitted``, so the executor reports ``timeout`` or
        # ``cancelled`` (R4's last row).
        await bridge._before_transmit()

        sent = False
        answer: asyncio.Future[Any] | None = None
        try:
            # -- the transmission segment (D8): no await before the write -- #
            if not bridge._writable(connection):
                bridge._withdraw(connection)
                return _not_ready()
            answer = connection.register(request_id)
            invocation.mark_emitted()
            sent = True
            await connection.ws.send_str(frame)
            # -- the frame was handed to the transport -------------------- #
            reply = await bridge._acknowledgement(answer)
        except asyncio.CancelledError:
            if not sent:
                raise
            return _acknowledgement_lost(cancelled=True)
        except Exception:
            if not sent:
                raise
            return _acknowledgement_lost()
        finally:
            if answer is not None:
                connection.forget(request_id)
        if not isinstance(reply, Mapping):
            return _acknowledgement_lost()
        status = reply.get("status")
        if status == _STATUS_OK:
            return _uncertain(
                CAUSE_EXECUTION_UNCONFIRMED,
                "the instance accepted the request, which does not confirm that it ran",
            )
        if status == _STATUS_ERROR:
            return ActionObservation(
                status="error",
                provenance=_run_provenance(),
                error={
                    "code": ERROR_PROVIDER_FAILED,
                    "message": f"{MODULE_NAME}: the instance answered the request with an error",
                    "retryable": False,
                    "cause": CAUSE_INSTANCE_REJECTED,
                },
            )
        return _acknowledgement_lost()


# --------------------------------------------------------------------------- #
# The viewer read (R7c)
# --------------------------------------------------------------------------- #

#: The page size bounds of the shared ``users.read`` argument schema.
VIEWERS_MIN_LIMIT = 1
VIEWERS_MAX_LIMIT = 100
#: The tag that tells this module's cursors from every other.
_VIEWER_CURSOR_TAG = "active"
_VIEWER_CURSOR_SEPARATOR = "/"
_EMPTY_VIEWER_PAGE = "(no active viewers)"


@dataclass(frozen=True, slots=True)
class _ViewerSnapshot:
    """One fetched active-viewer list of one destination.

    Every user carries the fetch instant as ``first_seen`` and ``last_seen``:
    the instance's list has no per-viewer timestamp the runtime clock could
    read, so the page states the instant the list was true at, and nothing
    older. ``evicted`` counts the users past :data:`VIEWER_SNAPSHOT_MAX_USERS`.
    """

    serial: int
    platform: str
    channel_id: str
    fetched_at: float
    users: tuple[Mapping[str, Any], ...]
    evicted: int


@dataclass(frozen=True, slots=True)
class _ViewerCursor:
    """A position in one snapshot: the destination, the snapshot, the offset."""

    platform: str
    channel_id: str
    serial: int
    offset: int

    def encode(self) -> str:
        raw = _VIEWER_CURSOR_SEPARATOR.join(
            quote(field, safe="")
            for field in (
                _VIEWER_CURSOR_TAG,
                self.platform,
                self.channel_id,
                str(self.serial),
                str(self.offset),
            )
        )
        return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")

    @classmethod
    def decode(cls, value: Any) -> "_ViewerCursor | None":
        """Decode *value*; ``None`` when it is not a cursor this module minted."""

        if not isinstance(value, str) or not value:
            return None
        try:
            raw = base64.urlsafe_b64decode(value.encode("ascii")).decode("utf-8")
        except (ValueError, binascii.Error, UnicodeError):
            return None
        fields = [unquote(field) for field in raw.split(_VIEWER_CURSOR_SEPARATOR)]
        if len(fields) != 5 or fields[0] != _VIEWER_CURSOR_TAG:
            return None
        _, platform, channel_id, serial, offset = fields
        if not (platform and channel_id and serial.isdigit() and offset.isdigit()):
            return None
        return cls(platform=platform, channel_id=channel_id, serial=int(serial), offset=int(offset))


def _active_viewers(
    answer: Any, platform: str, fetched_at: float
) -> tuple[tuple[Mapping[str, Any], ...], int] | None:
    """The users of a ``GetActiveViewers`` answer on *platform*, and how many
    were left out past the bound; ``None`` when the answer is not usable.

    The request takes no documented platform parameter: each viewer carries
    its platform in ``type``, compared without case (the trial confirms the
    spelling). The order is the instance's; a repeated id is kept once. A
    viewer without an id is skipped. Role claims are not reported: a page
    carries ``roles`` only with an attestation, and the instance's roles are
    not one.
    """

    if not isinstance(answer, Mapping) or answer.get("status") != _STATUS_OK:
        return None
    viewers = answer.get("viewers")
    if not isinstance(viewers, list):
        return None
    users: list[Mapping[str, Any]] = []
    seen: set[str] = set()
    evicted = 0
    for viewer in viewers:
        if not isinstance(viewer, Mapping):
            continue
        kind = viewer.get("type")
        if not isinstance(kind, str) or kind.lower() != platform:
            continue
        user_id = _identifier(viewer.get("id"))
        if user_id is None or user_id in seen:
            continue
        seen.add(user_id)
        if len(users) >= VIEWER_SNAPSHOT_MAX_USERS:
            evicted += 1
            continue
        display = next(
            (
                value
                for value in (viewer.get("display"), viewer.get("login"))
                if _is_text(value)
            ),
            user_id,
        )
        users.append(
            MappingProxyType(
                {
                    "user_id": user_id,
                    "display_name": display,
                    "first_seen": fetched_at,
                    "last_seen": fetched_at,
                }
            )
        )
    return tuple(users), evicted


def render_viewer_page(users: Sequence[Mapping[str, Any]], page: Mapping[str, Any]) -> str:
    """One page as the one ``text`` part of a read: one line per user, then
    whether more follow, so the model knows the page is a cut of the users
    the instance reports as active, never the audience."""

    lines = [
        f"{user['display_name']} <{user['user_id']}> active at {float(user['last_seen']):.3f}"
        for user in users
    ]
    if not lines:
        lines.append(_EMPTY_VIEWER_PAGE)
    more = "more follow" if page["has_more"] else "no more"
    lines.append(f"page: {page['returned']} returned, {more}")
    return "\n".join(lines)


class _ViewersProvider:
    """The bridge's ``users.read`` provider (R7c).

    Bound by the runtime only where this module owns (``viewers``, P). It
    answers for one channel per platform — ``instance_identity.<P>``, the
    broadcaster the instance runs for — and refuses any other one
    ``unsupported_destination``. A first page fetches the instance's
    active-viewer list once, within ``request_seconds``; a ``cursor`` pages
    over that same snapshot without another request. Each destination keeps
    only its latest snapshot, so a cursor into an older one is refused.
    Reading has no external effect: the call is declared not emitted, so a
    deadline stays ``timeout``.
    """

    __slots__ = ("_bridge",)

    name = RUN_PROVIDER

    def __init__(self, bridge: Any) -> None:
        self._bridge = bridge

    async def invoke(self, invocation: Any) -> ActionObservation:
        invocation.mark_not_emitted()
        bridge = self._bridge
        destination = invocation.call.destination
        platform, channel_id = destination.platform, destination.channel_id
        provenance = {
            "provider": RUN_PROVIDER,
            "platform": platform,
            "channel_id": channel_id,
            "route": USERS_READ_ACTION,
        }

        def outcome(status: str, code: str, message: str) -> ActionObservation:
            return ActionObservation(
                status=status,
                provenance=provenance,
                error={"code": code, "message": f"{MODULE_NAME}: {message}", "retryable": False},
            )

        connection = bridge._connection
        if connection is None or not bridge.connected:
            return outcome(
                "refused", ERROR_PROVIDER_NOT_READY, "the automation instance is not connected"
            )
        if (
            not bridge._owns(_VIEWERS_FAMILY, platform)
            or bridge._instance_identity.get(platform) != channel_id
        ):
            return outcome(
                "refused",
                ERROR_UNSUPPORTED_DESTINATION,
                "the instance reports active viewers for its broadcaster's channel only",
            )
        arguments = invocation.call.arguments
        limit = arguments.get("limit")
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not VIEWERS_MIN_LIMIT <= limit <= VIEWERS_MAX_LIMIT
        ):
            return outcome(
                "error",
                ERROR_INVALID_ARGUMENTS,
                f"limit must be an integer between {VIEWERS_MIN_LIMIT} and {VIEWERS_MAX_LIMIT}",
            )

        if arguments.get("cursor") is not None:
            cursor = _ViewerCursor.decode(arguments["cursor"])
            if cursor is None:
                return outcome(
                    "error", ERROR_INVALID_ARGUMENTS, "cursor is not one this action issued"
                )
            if (cursor.platform, cursor.channel_id) != (platform, channel_id):
                return outcome(
                    "error", ERROR_INVALID_ARGUMENTS, "cursor was issued for another destination"
                )
            snapshot = bridge._viewer_snapshots.get(platform)
            if snapshot is None or snapshot.serial != cursor.serial:
                return outcome(
                    "error",
                    ERROR_INVALID_ARGUMENTS,
                    "cursor belongs to a viewer list that was read again since",
                )
            offset = cursor.offset
        else:
            try:
                answer = await connection.request(
                    {"request": _REQUEST_GET_ACTIVE_VIEWERS}, bridge._request_seconds
                )
            except ConnectionClosed:
                return outcome(
                    "refused",
                    ERROR_PROVIDER_NOT_READY,
                    "the connection to the instance ended before the viewer list arrived",
                )
            except asyncio.TimeoutError:
                return outcome(
                    "timeout",
                    ERROR_TIMED_OUT,
                    "the instance did not answer the viewer read within its deadline",
                )
            fetched_at = float(bridge._clock())
            parsed = _active_viewers(answer, platform, fetched_at)
            if parsed is None:
                return outcome(
                    "error",
                    ERROR_PROVIDER_FAILED,
                    "the instance's answer to the viewer read is not a viewer list",
                )
            users, evicted = parsed
            snapshot = _ViewerSnapshot(
                serial=next(bridge._viewer_serials),
                platform=platform,
                channel_id=channel_id,
                fetched_at=fetched_at,
                users=users,
                evicted=evicted,
            )
            bridge._viewer_snapshots[platform] = snapshot
            offset = 0

        selected = [dict(user) for user in snapshot.users[offset : offset + limit]]
        has_more = offset + limit < len(snapshot.users)
        page = {
            "returned": len(selected),
            "has_more": has_more,
            "next_cursor": (
                _ViewerCursor(platform, channel_id, snapshot.serial, offset + limit).encode()
                if has_more
                else None
            ),
        }
        result = {
            "users": selected,
            "page": page,
            # The list is true at the fetch instant and no longer: a user
            # stays listed for no time after it, hence a zero window.
            "freshness": {"observed_at": snapshot.fetched_at, "window_seconds": 0.0},
            "coverage": {
                "kind": COVERAGE_ACTIVE_VIEWERS,
                # The instance's active viewers are never the whole audience.
                "complete": False,
                "retained_users": len(snapshot.users),
                "evicted": snapshot.evicted,
            },
        }
        return ActionObservation(
            status="success",
            provenance=provenance,
            result=result,
            parts=({"type": PART_TYPE_TEXT, "text": render_viewer_page(selected, page)},),
        )


class AutomationBridge:
    """The v2 handle: one connection, its readiness and its reconnection.

    ``prepare`` binds the run action. ``start_inputs`` spawns the connection
    owner — a task spawned through the context — whose first attempt it
    waits for, bounded, and never raises for its failure: a failure is one
    ``module.degraded`` record with its reason, and the owner retries with
    bounded backoff. The
    bridge is ready only while connected, authenticated and holding the
    instance's inventory; a lost connection withdraws readiness at once.
    """

    def __init__(
        self, context: Any, settings: Mapping[str, Any], *, sleeper: Sleeper
    ) -> None:
        self._actions = context.actions
        self._supervision = context.supervision
        self._tasks = context.tasks
        self._clock: Callable[[], float] = context.clock
        self._sleeper = sleeper
        self._url: str = settings[_SETTING_URL]
        password = settings.get(_SETTING_PASSWORD)
        self._password: str | None = password if password else None
        deadlines = {**_DEFAULT_DEADLINES, **(settings.get(_SETTING_DEADLINES) or {})}
        self._connect_seconds = float(deadlines["connect_seconds"])
        self._authentication_seconds = float(deadlines["authentication_seconds"])
        self._request_seconds = float(deadlines["request_seconds"])
        self._acknowledgement_seconds = float(deadlines[_ACKNOWLEDGEMENT_FIELD])
        self._allowlist = _parse_allowlist(settings.get(_SETTING_ALLOWLIST))
        reconnect = settings.get(_SETTING_RECONNECT) or {}
        self._min_delay = float(reconnect.get(_MIN_DELAY_FIELD, _DEFAULT_MIN_DELAY_SECONDS))
        self._max_delay = float(reconnect.get(_MAX_DELAY_FIELD, _DEFAULT_MAX_DELAY_SECONDS))
        self._provider = _RunProvider(self)
        self._viewers = _ViewersProvider(self)
        #: The latest active-viewer snapshot per owned platform (R7c).
        self._viewer_snapshots: dict[str, _ViewerSnapshot] = {}
        self._viewer_serials = itertools.count(1)
        self._bus = getattr(context, "bus", None)
        self._triggers = getattr(context, "triggers", None)
        self._inputs = getattr(context, "inputs", None)
        self._chat = getattr(context, "chat", None)
        # A shared scheduler is fed by the producer that decided the trigger
        # (the brain admits into an owned one only), so an accepted cue is
        # admitted here, as the native chat inputs admit theirs.
        scheduler = getattr(context, "scheduler", None)
        self._scheduler = scheduler if callable(getattr(scheduler, "admit", None)) else None
        self._instance_identity: Mapping[str, str] = MappingProxyType(
            dict(settings.get(_SETTING_INSTANCE_IDENTITY) or {})
        )
        self._trust_roles = settings.get(_SETTING_TRUST_INSTANCE_ROLES) is True
        self._queue: asyncio.Queue[Mapping[str, Any]] = asyncio.Queue(
            maxsize=int(settings.get(_SETTING_QUEUE_MAX_EVENTS, _DEFAULT_QUEUE_MAX_EVENTS))
        )
        self._dropped_events = 0
        self._consumer: asyncio.Task[None] | None = None
        self._session: aiohttp.ClientSession | None = None
        self._connection: _Connection | None = None
        self._owner: asyncio.Task[None] | None = None
        self._inventory: Inventory = MappingProxyType({})
        # Health reports are serialised so the startup verdict and the
        # first attempt's own report cannot interleave.
        self._health = asyncio.Lock()
        self._first_settled = asyncio.Event()
        self._first_reported = False
        self._prepared = False
        self._started = False
        self._stopping = False
        self._closed = False

    @property
    def connected(self) -> bool:
        """Whether a handshake completed and its connection is still open."""

        connection = self._connection
        return connection is not None and not connection.lost.is_set()

    @property
    def inventory(self) -> Inventory:
        """The inventory read at the last successful connection."""

        return self._inventory

    @property
    def dropped_events(self) -> int:
        """Inbound events dropped: a full queue, a malformed or oversized
        envelope, a platform not owned, an event with no mapping (R6, R7)."""

        return self._dropped_events

    async def _before_transmit(self) -> None:
        """The test seam before the transmission segment; returns at once."""

    async def _before_consume(self) -> None:
        """The test seam before each dequeue; returns at once."""

    # -- inbound events (R6, R7) ------------------------------------------- #

    def _on_event(self, frame: Mapping[str, Any]) -> None:
        """Hand one inbound event to the bounded queue, never waiting.

        Called by the reader: a full queue drops the event and counts it, so
        the reader goes on and request answers keep flowing (R6, AC26).
        """

        try:
            self._queue.put_nowait(frame)
        except asyncio.QueueFull:
            self._drop()

    def _drop(self) -> None:
        """Count one dropped inbound event."""

        self._dropped_events += 1

    async def _consume(self) -> None:
        """Drain the queue, one event at a time, until cancelled."""

        while True:
            await self._before_consume()
            frame = await self._queue.get()
            try:
                await self._ingest(frame)
            except asyncio.CancelledError:
                raise
            except Exception:
                # One event that cannot be handled never stops the consumer.
                self._drop()

    async def _ingest(self, frame: Mapping[str, Any]) -> None:
        """Map, evaluate, publish, trace and admit one inbound event (R7).

        The order mirrors the native chat inputs: the dedup window first (a
        replay is neither re-published nor re-decided), then the trigger
        decision, then the publication — the bus carries the event decided
        either way, and its consumer reads the recorded decision — then the
        decision's trace, then — accepted, with a scheduler in the context —
        the admission. A chat event past the dedup window feeds the shared
        transcript before it is decided, as the native inputs feed theirs. A cue or a chat event for a platform this module does
        not own is dropped here, before the ownership guard would count it.
        """

        mapped = self._map(frame)
        if mapped is None:
            self._drop()
            return
        event, claims = mapped
        payload = event["payload"]
        engine = self._triggers
        decision = None
        if engine is not None and engine.recorded(
            platform=payload["platform"],
            channel_id=payload["channel_id"],
            source_event_id=payload["message_id"],
        ) is not None:
            return
        if not _is_cue_frame(frame):
            self._feed_chat_context(payload)
        if engine is not None:
            try:
                decision = engine.evaluate(event, context=TriggerContext(claims))
            except Exception:
                # Fail closed: published below, but nothing reads a decision.
                decision = None
        if self._bus is not None:
            try:
                await self._bus.publish(_CHAT_EVENT, payload, event["metadata"])
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
        if decision is not None:
            await self._trace_decision(decision)
            if decision.accepted and self._scheduler is not None:
                self._admit(event)

    def _map(
        self, frame: Mapping[str, Any]
    ) -> tuple[dict[str, Any], tuple[TrustedClaim, ...]] | None:
        """The normalised event of *frame* and its trusted claims, or
        ``None`` for anything to drop (R7).

        A custom event is a cue, kept when this module owns (``cues``, P);
        any other frame is mapped through :data:`_CHAT_MAPPINGS`, kept when
        this module owns (``chat``, P). A cue never carries claims; a chat
        event carries the instance's only under ``trust_instance_roles``.
        """

        if _is_cue_frame(frame):
            event = cue_event(frame, self._instance_identity)
            mapped = None if event is None else (event, ())
            family = _CUES_FAMILY
        else:
            mapped = chat_event(
                frame, self._instance_identity, trust_roles=self._trust_roles
            )
            family = _CHAT_FAMILY
        if mapped is None or not self._owns(family, mapped[0]["payload"]["platform"]):
            return None
        return mapped

    def _feed_chat_context(self, payload: Mapping[str, Any]) -> None:
        """Append one mapped chat event to the shared transcript (R7b).

        The bridge owning (chat, P) means the native input no longer feeds it;
        a failed append changes nothing about what is decided.
        """

        if self._chat is None:
            return
        try:
            self._chat.append(
                payload["platform"],
                payload["channel_id"],
                ChatEntry(
                    author_id=payload["author"]["id"],
                    message_id=payload["message_id"],
                    text=payload["text"],
                ),
            )
        except Exception:
            pass

    def _admit(self, event: Mapping[str, Any]) -> None:
        """Hand the accepted event to the scheduler and return at once.

        The scheduler copies the event, decides and traces its own admission;
        a refusal or a failure here changes nothing about what was decided.
        """

        payload = event["payload"]
        try:
            self._scheduler.admit(
                SessionKey(
                    platform=payload["platform"],
                    channel_id=payload["channel_id"],
                    viewer_id=payload["author"]["id"],
                ),
                Work(
                    payload=event,
                    source_event_id=payload["message_id"],
                    kind="chat.message",
                ),
            )
        except Exception:
            pass

    def _owns(self, family: str, platform: str) -> bool:
        inputs = self._inputs
        return inputs is not None and inputs.owns(family, platform)

    async def _trace_decision(self, decision: Any) -> None:
        """The decision's supervision fact, as the native inputs trace it."""

        if not getattr(decision, "traceable", True):
            return
        try:
            await self._supervision.emit(
                TRACE_INPUT_TRIGGER_ACCEPTED
                if decision.accepted
                else TRACE_INPUT_TRIGGER_REJECTED,
                {
                    "source_event_id": decision.source_event_id,
                    "input": decision.input_name,
                    "platform": decision.platform,
                    "channel_id": decision.channel_id,
                    "policy_version": decision.policy_version,
                    "reason": decision.reason,
                },
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            # A lost trace changes nothing about what was decided.
            pass

    async def _subscribe(self, connection: _Connection) -> None:
        """One ``Subscribe`` naming only what the owned pairs need (R6).

        Nothing is sent when nothing is owned that arrives as an event.
        """

        events = subscription_events(self._inputs) if self._inputs is not None else {}
        if not events:
            return
        try:
            answer = await connection.request(
                {"request": _REQUEST_SUBSCRIBE, "events": events}, self._request_seconds
            )
        except ConnectionClosed:
            raise _HandshakeFailure(REASON_UNREACHABLE) from None
        except asyncio.TimeoutError:
            raise _HandshakeFailure(REASON_PROTOCOL_ERROR) from None
        if not isinstance(answer, Mapping) or answer.get("status") != _STATUS_OK:
            raise _HandshakeFailure(REASON_PROTOCOL_ERROR)

    # -- the run provider's decisions and boundary (R4, R5, D8) ------------- #

    def _decide(
        self, call_arguments: Mapping[str, Any]
    ) -> ActionObservation | tuple[InstanceAction, Mapping[str, Any]]:
        """The R4 refusals, in order, before any frame; else the target.

        1. neither an ``action:`` entry nor a matching ``group:`` entry →
           ``not_allowlisted`` (an empty allowlist refuses everything);
        2. absent from the inventory → ``instance_action_missing``;
        3. two or more instance actions of that name → ``ambiguous``;
        4. disabled → ``instance_action_disabled``;
        5. the deciding schema refuses the arguments → ``invalid_arguments``:
           the ``action:`` entry's schema alone when one names the action,
           otherwise every matching ``group:`` entry's schema.
        """

        name = call_arguments.get("action")
        arguments = call_arguments.get("arguments", MappingProxyType({}))
        candidates = self._inventory.get(name, ()) if isinstance(name, str) else ()
        named = next(
            (entry for entry in self._allowlist if entry.kind == "action" and entry.name == name),
            None,
        )
        grouped = [
            entry
            for entry in self._allowlist
            if entry.kind == "group"
            and any(_in_group(candidate, entry.name) for candidate in candidates)
        ]
        if named is None and not grouped:
            return _refused(ERROR_NOT_ALLOWLISTED, "the action is not in the allowlist")
        if not candidates:
            return _refused(
                ERROR_INSTANCE_ACTION_MISSING, "the instance has no action of that name"
            )
        if len(candidates) > 1:
            return _refused(
                ERROR_INSTANCE_ACTION_AMBIGUOUS,
                "the instance has more than one action of that name",
            )
        (target,) = candidates
        if not target.enabled:
            return _refused(
                ERROR_INSTANCE_ACTION_DISABLED, "the instance action is disabled"
            )
        deciding = (
            [named]
            if named is not None
            else [entry for entry in grouped if _in_group(target, entry.name)]
        )
        for entry in deciding:
            if not _arguments_accepted(arguments, entry.schema):
                return ActionObservation(
                    status="error",
                    provenance=_run_provenance(),
                    error={
                        "code": ERROR_INVALID_ARGUMENTS,
                        "message": (
                            f"{MODULE_NAME}: the arguments do not satisfy the "
                            "allowlist entry's schema"
                        ),
                        "retryable": False,
                    },
                )
        return target, arguments

    def _writable(self, connection: _Connection) -> bool:
        """The pre-write check of D8, synchronous.

        Mirrors the installed client's only pre-write failures (the writer's
        closing flag, the transport closing) and the states they follow
        from: the bridge still holds *connection*, the WebSocket is not
        closed, the protocol's transport is present and not closing.
        """

        ws = connection.ws
        writer = getattr(ws, "_writer", None)
        transport = getattr(writer, "transport", None)
        protocol = getattr(writer, "protocol", None)
        return (
            self._connection is connection
            and self.connected
            and not ws.closed
            and writer is not None
            and not getattr(writer, "_closing", False)
            and transport is not None
            and getattr(protocol, "transport", None) is not None
            and not transport.is_closing()
        )

    def _withdraw(self, connection: _Connection) -> None:
        """A connection that cannot be written: not ready at once.

        The connection is abandoned without a loop turn; its owner closes
        it, reports ``degraded`` and reconnects, as for any loss.
        """

        if self._connection is connection:
            self._actions.mark_not_ready()
            connection.abandon()

    async def _acknowledgement(self, answer: asyncio.Future[Any]) -> Any:
        """The instance's answer, or ``None`` past the acknowledgement
        deadline or when the connection ended first (``ConnectionClosed``).

        The deadline runs on the context clock, through the sleeper. The
        decision is latched when the wait returns: an answer landing while
        the timer is cleaned up is late, and lost (R4).
        """

        timer = asyncio.ensure_future(self._pause(self._acknowledgement_seconds))
        try:
            done, _ = await asyncio.wait(
                {answer, timer}, return_when=asyncio.FIRST_COMPLETED
            )
        finally:
            if not timer.done():
                timer.cancel()
            await asyncio.gather(timer, return_exceptions=True)
        if answer not in done or answer.cancelled() or answer.exception() is not None:
            return None
        return answer.result()

    async def _reconcile(self, inventory: Inventory) -> None:
        """Match the allowlist to *inventory*; one value-free trace (R5).

        An entry is unmatched when it reaches no action that exists exactly
        once and is enabled; it is named by its configured name only.
        """

        reachable: set[str] = set()
        unmatched: dict[str, list[str]] = {key: [] for key in _ENTRY_KEYS}
        for entry in self._allowlist:
            if entry.kind == "action":
                found = [entry.name] if _reachable(inventory.get(entry.name, ())) else []
            else:
                found = [
                    name
                    for name, actions in inventory.items()
                    if any(_in_group(action, entry.name) for action in actions)
                    and _reachable(actions)
                ]
            if found:
                reachable.update(found)
            else:
                unmatched[entry.kind].append(entry.name)
        await self._supervision.emit(
            TRACE_ALLOWLIST_RECONCILED,
            {
                "module": MODULE_NAME,
                "entries": len(self._allowlist),
                "reachable": len(reachable),
                "unmatched_actions": unmatched["action"],
                "unmatched_groups": unmatched["group"],
            },
        )

    async def prepare(self) -> None:
        """Bind the run action and the owned viewer reads; the bridge is not
        ready until connected."""

        if self._prepared or self._closed:
            return
        spec = declared_run_spec()
        viewers = declared_users_read_spec()
        try:
            self._actions.register(spec, self._provider, provider_name=RUN_PROVIDER)
            # The runtime binds the shared viewer read only where this module
            # owns (``viewers``, P), and only declares it otherwise (R3, D5).
            self._actions.register(viewers, self._viewers, provider_name=RUN_PROVIDER)
        except Exception:
            raise AutomationBridgeError(
                f"{MODULE_NAME} prepare: action binding failed"
            ) from None
        self._prepared = True

    async def start_inputs(self) -> None:
        """Hand the connection to its owner and wait for its first attempt.

        Never raises for an unavailable instance (R1): the attempt's failure
        is reported ``degraded`` with its reason, and the owner retries. The
        wait is bounded by :data:`_STARTUP_ATTEMPT_SECONDS`; an attempt still
        pending then is reported ``degraded`` (``unreachable``) and goes on in
        the owner, which reports its outcome when it settles.
        """

        if self._started or self._closed:
            return
        self._started = True
        self._session = aiohttp.ClientSession()
        self._consumer = self._tasks.spawn(self._consume(), name=_CONSUMER_TASK)
        owner = self._owner = self._tasks.spawn(self._own(), name=_OWNER_TASK)
        settled = asyncio.ensure_future(self._first_settled.wait())
        try:
            await asyncio.wait(
                {settled, owner},
                timeout=_STARTUP_ATTEMPT_SECONDS,
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            settled.cancel()
        async with self._health:
            if not self._first_reported:
                self._first_reported = True
                await self._supervision.degraded(reason=REASON_UNREACHABLE)

    async def stop_inputs(self) -> None:
        """Stop the connection owner; an open connection stays for ``close``."""

        self._stopping = True
        await self._stop_owner()
        await self._stop_consumer()

    async def close(self) -> None:
        """Withdraw readiness, fail every pending request, close the socket.

        Idempotent. Bounded by the coordinator's close budget.
        """

        if self._closed:
            return
        self._closed = True
        self._stopping = True
        if self._prepared:
            self._actions.mark_not_ready()
        await self._stop_owner()
        await self._stop_consumer()
        self._viewer_snapshots.clear()
        connection, self._connection = self._connection, None
        try:
            if connection is not None:
                await connection.close()
        finally:
            session, self._session = self._session, None
            if session is not None:
                await session.close()

    # -- the connection owner ------------------------------------------------ #

    async def _own(self) -> None:
        """Hold the connection; on loss, degrade at once and reconnect.

        The delay starts at the minimum after every loss and doubles after
        each failed attempt, capped at the maximum (no jitter). A cancellation
        outside shutdown releases the connection and propagates.
        """

        delay = self._min_delay
        try:
            await self._attempt()
            while True:
                connection = self._connection
                if connection is not None:
                    await connection.lost.wait()
                    await self._lose(connection)
                    delay = self._min_delay
                await self._pause(delay)
                delay = min(delay * 2, self._max_delay)
                await self._attempt()
        except asyncio.CancelledError:
            if not self._stopping:
                # Cancelled outside shutdown: the connection has no owner any
                # more, so it is released and the loss is reported (R6).
                connection, self._connection = self._connection, None
                self._actions.mark_not_ready()
                try:
                    if connection is not None:
                        await connection.close()
                finally:
                    await self._supervision.degraded(reason=REASON_UNREACHABLE)
            raise

    async def _pause(self, delay: float) -> None:
        """Wait *delay* seconds on the context clock, via the sleeper."""

        resume_at = self._clock() + delay
        remaining = delay
        while remaining > 0:
            await self._sleeper(remaining)
            remaining = resume_at - self._clock()

    async def _stop_owner(self) -> None:
        owner, self._owner = self._owner, None
        if owner is None or owner.done():
            return
        owner.cancel()
        await asyncio.gather(owner, return_exceptions=True)

    async def _stop_consumer(self) -> None:
        consumer, self._consumer = self._consumer, None
        if consumer is None or consumer.done():
            return
        consumer.cancel()
        await asyncio.gather(consumer, return_exceptions=True)

    async def _lose(self, connection: _Connection) -> None:
        """A lost connection: not ready at once, then one degraded record."""

        if self._connection is connection:
            self._connection = None
        self._actions.mark_not_ready()
        await connection.close()
        await self._supervision.degraded(reason=REASON_UNREACHABLE)

    async def _attempt(self) -> bool:
        """One connection attempt; ready on success, degraded otherwise."""

        try:
            connection, inventory = await self._handshake()
        except _HandshakeFailure as failure:
            return await self._failed(failure.reason)
        self._inventory = inventory
        self._connection = connection
        await self._reconcile(inventory)
        try:
            await self._subscribe(connection)
        except _HandshakeFailure as failure:
            if self._connection is connection:
                self._connection = None
            await connection.close()
            return await self._failed(failure.reason)
        async with self._health:
            self._first_reported = True
            self._actions.mark_ready()
            await self._supervision.ready(dropped_events=self._dropped_events)
        self._first_settled.set()
        return True

    async def _failed(self, reason: str) -> bool:
        """One failed attempt: one degraded record with its *reason*."""

        async with self._health:
            self._first_reported = True
            await self._supervision.degraded(reason=reason)
        self._first_settled.set()
        return False

    async def _handshake(self) -> tuple[_Connection, Inventory]:
        """Connect, read the hello, authenticate, read the inventory.

        Each step is bounded by its own deadline; a failure closes whatever
        was opened and raises :class:`_HandshakeFailure` with its reason.
        """

        session = self._session
        if session is None:
            raise _HandshakeFailure(REASON_UNREACHABLE)
        try:
            ws = await asyncio.wait_for(
                session.ws_connect(self._url, compress=0), self._connect_seconds
            )
        except (asyncio.TimeoutError, aiohttp.ClientError, OSError, ValueError):
            raise _HandshakeFailure(REASON_UNREACHABLE) from None
        connection: _Connection | None = None
        try:
            hello = await self._receive_hello(ws)
            connection = _Connection(ws, self._on_event)
            await self._authenticate(connection, hello)
            try:
                answer = await connection.request(
                    {"request": _REQUEST_GET_ACTIONS}, self._request_seconds
                )
            except ConnectionClosed:
                raise _HandshakeFailure(REASON_UNREACHABLE) from None
            except asyncio.TimeoutError:
                raise _HandshakeFailure(REASON_INVENTORY_UNAVAILABLE) from None
            inventory = _parse_inventory(answer)
            if inventory is None:
                raise _HandshakeFailure(REASON_INVENTORY_UNAVAILABLE)
            if connection.lost.is_set():
                raise _HandshakeFailure(REASON_UNREACHABLE)
            return connection, inventory
        except BaseException:
            if connection is not None:
                await connection.close()
            else:
                await ws.close()
            raise

    async def _receive_hello(self, ws: Any) -> Mapping[str, Any]:
        try:
            message = await asyncio.wait_for(ws.receive(), self._authentication_seconds)
        except asyncio.TimeoutError:
            raise _HandshakeFailure(REASON_PROTOCOL_ERROR) from None
        except (ConnectionError, aiohttp.ClientError):
            raise _HandshakeFailure(REASON_UNREACHABLE) from None
        if message.type in (
            aiohttp.WSMsgType.CLOSE,
            aiohttp.WSMsgType.CLOSING,
            aiohttp.WSMsgType.CLOSED,
            aiohttp.WSMsgType.ERROR,
        ):
            raise _HandshakeFailure(REASON_UNREACHABLE)
        if message.type is not aiohttp.WSMsgType.TEXT:
            raise _HandshakeFailure(REASON_PROTOCOL_ERROR)
        try:
            hello = json.loads(message.data)
        except ValueError:
            raise _HandshakeFailure(REASON_PROTOCOL_ERROR) from None
        if not isinstance(hello, Mapping) or hello.get("request") != _REQUEST_HELLO:
            raise _HandshakeFailure(REASON_PROTOCOL_ERROR)
        return hello

    async def _authenticate(self, connection: _Connection, hello: Mapping[str, Any]) -> None:
        """Answer the hello's challenge, when it carries one."""

        if "authentication" not in hello:
            return
        challenge = hello["authentication"]
        if not isinstance(challenge, Mapping) or not all(
            isinstance(challenge.get(key), str) for key in ("salt", "challenge")
        ):
            raise _HandshakeFailure(REASON_PROTOCOL_ERROR)
        if self._password is None:
            raise _HandshakeFailure(REASON_AUTHENTICATION_REQUIRED)
        response = authentication_response(
            self._password, challenge["salt"], challenge["challenge"]
        )
        try:
            answer = await connection.request(
                {"request": _REQUEST_AUTHENTICATE, "authentication": response},
                self._authentication_seconds,
            )
        except ConnectionClosed:
            raise _HandshakeFailure(REASON_UNREACHABLE) from None
        except asyncio.TimeoutError:
            raise _HandshakeFailure(REASON_AUTHENTICATION_FAILED) from None
        if not isinstance(answer, Mapping) or answer.get("status") != _STATUS_OK:
            raise _HandshakeFailure(REASON_AUTHENTICATION_FAILED)


def _reachable(actions: Sequence[InstanceAction]) -> bool:
    """One action of that name, and enabled (R5)."""

    return len(actions) == 1 and actions[0].enabled


def _arguments_accepted(arguments: Any, schema: Mapping[str, Any] | None) -> bool:
    """Whether *arguments* satisfy an entry's schema; no schema accepts ``{}``."""

    if schema is None:
        return isinstance(arguments, Mapping) and not arguments
    try:
        validate_against_schema(arguments, schema, label=_ENTRY_ARGUMENTS)
    except ContractError:
        return False
    return True


# --------------------------------------------------------------------------- #
# Activation
# --------------------------------------------------------------------------- #


async def activate(
    context: Any,
    settings: Mapping[str, Any],
    catalog: Mapping[str, Mapping[str, Any]],
) -> AutomationBridge:
    """Build the handle from the scoped runtime context.

    Nothing here reaches the network. Settings are checked through the same
    hook the loader ran, so a handle built outside the loader is refused on
    the same terms; the diagnostics themselves are never raised. The
    ``_sleeper`` seam is read from *settings* (``asyncio.sleep`` by default).
    """

    del catalog
    actions = getattr(context, "actions", None)
    supervision = getattr(context, "supervision", None)
    tasks = getattr(context, "tasks", None)
    if (
        actions is None
        or not all(
            callable(getattr(actions, method, None))
            for method in ("register", "mark_ready", "mark_not_ready")
        )
        or not all(
            callable(getattr(supervision, method, None))
            for method in ("ready", "degraded", "emit")
        )
        or not callable(getattr(tasks, "spawn", None))
        or not callable(getattr(context, "clock", None))
    ):
        raise AutomationBridgeError(f"{MODULE_NAME} activation: runtime context is invalid")
    if not isinstance(settings, Mapping):
        raise AutomationBridgeError(f"{MODULE_NAME} configuration: settings must be a mapping")
    diagnostics = validate_settings(settings)
    if diagnostics:
        raise AutomationBridgeError(
            f"{MODULE_NAME} configuration: settings were refused "
            f"({len(diagnostics)} diagnostics)"
        )
    identities = settings.get(_SETTING_COMPANION_IDENTITIES) or {}
    triggers = getattr(context, "triggers", None)
    if identities and triggers is not None:
        # The engine's anti-echo refuses what the companion authored on every
        # platform this module relays (D6).
        triggers.add_companion_identities(identities.values())
    return AutomationBridge(
        context, settings, sleeper=settings.get(_SEAM_SLEEPER, asyncio.sleep)
    )


__all__ = [
    "ACKNOWLEDGEMENT_MARGIN_SECONDS",
    "CAUSE_ACKNOWLEDGEMENT_LOST",
    "CAUSE_EXECUTION_UNCONFIRMED",
    "CAUSE_INSTANCE_REJECTED",
    "CHAT_PLATFORMS",
    "COVERAGE_ACTIVE_VIEWERS",
    "CUE_ID_MAX_CHARACTERS",
    "CUE_TEXT_MAX_CODE_POINTS",
    "DEGRADED_REASONS",
    "ERROR_INSTANCE_ACTION_AMBIGUOUS",
    "ERROR_INSTANCE_ACTION_DISABLED",
    "ERROR_INSTANCE_ACTION_MISSING",
    "ERROR_NOT_ALLOWLISTED",
    "INSTANCE_AUTHOR_PREFIX",
    "MANIFEST_PATH",
    "MODULE_NAME",
    "REASON_AUTHENTICATION_FAILED",
    "REASON_AUTHENTICATION_REQUIRED",
    "REASON_INVENTORY_UNAVAILABLE",
    "REASON_PROTOCOL_ERROR",
    "REASON_UNREACHABLE",
    "RUN_ACTION",
    "RUN_PROVIDER",
    "TRACE_ALLOWLIST_RECONCILED",
    "UNCERTAINTY_SENTENCE",
    "USERS_MANIFEST_PATH",
    "USERS_READ_ACTION",
    "VIEWER_SNAPSHOT_MAX_USERS",
    "AllowlistEntry",
    "AutomationBridge",
    "AutomationBridgeError",
    "ConnectionClosed",
    "InstanceAction",
    "activate",
    "authentication_response",
    "cue_event",
    "declared_run_spec",
    "declared_users_read_spec",
    "render_viewer_page",
    "run_action_timeout_seconds",
    "subscription_events",
    "validate_settings",
]
