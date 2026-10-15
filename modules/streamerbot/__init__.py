"""Automation bridge on the v2 runtime (phase 5 R1, R4, R6, R10).

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

Nothing here logs: the URL, the password and frame text never reach a trace
or a diagnostic, only reasons do. No endpoint, port, password, version or
action name has a default anywhere (R6).

**Seams, for the tests.** ``_sleeper`` (the sleep the backoff and the
acknowledgement deadline wait on, ``asyncio.sleep`` by default) is read from
*settings* at ``activate`` and never from a configuration file.
``AutomationBridge._before_transmit`` is awaited by the run provider after
every decision and strictly before the transmission segment; by default it
returns at once. Replacing it makes the interval before the frame leaves
observable.
"""

from __future__ import annotations

import asyncio
import base64
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
from urllib.parse import urlsplit

import aiohttp
import yaml

from core.actions import (
    ERROR_CANCELLED,
    ERROR_EXTERNAL_UNKNOWN,
    ERROR_INVALID_ARGUMENTS,
    ERROR_PROVIDER_FAILED,
    ERROR_PROVIDER_NOT_READY,
)
from core.contracts import (
    ActionObservation,
    ActionSpec,
    ContractError,
    Destination,
    validate_against_schema,
    validate_schema,
)

MODULE_NAME = "streamerbot"

RUN_ACTION = "automation.action.run"
RUN_PROVIDER = "automation-bridge"
"""Provider name in bindings and traces."""

MANIFEST_PATH = Path(__file__).with_name("module.yaml")

#: How much of the run action's fixed ``timeout_seconds`` the acknowledgement
#: deadline must leave free, so the executor's own timeout never pre-empts the
#: bridge's ``acknowledgement_lost`` cause (R4).
ACKNOWLEDGEMENT_MARGIN_SECONDS = 1.0

#: The platforms whose chat input the manifest lets this module own; owning
#: one requires a companion identity for it (R7, AC33).
CHAT_PLATFORMS = ("twitch", "kick", "youtube")
_CHAT_FAMILY = "chat"

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
    """

    if not isinstance(settings, Mapping):
        return [_setting_diagnostic("settings", "must be a mapping")]
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
    """A platform → account identifier mapping, both sides non-empty text."""

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


def _declared_run_entry() -> Mapping[str, Any]:
    manifest = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))
    return next(
        item
        for item in manifest["actions"]
        if isinstance(item, Mapping) and item.get("name") == RUN_ACTION
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
        entry = _declared_run_entry()
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
        raise AutomationBridgeError(
            f"{MODULE_NAME} prepare: manifest declaration of {RUN_ACTION!r} is invalid"
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

#: How long ``start_inputs`` waits for the first attempt to settle. Well
#: inside the coordinator's default hook timeout (60 s), whatever deadlines
#: are configured: an instance that withholds its answers leaves the bridge
#: degraded instead of exhausting the startup budget (R1).
_STARTUP_ATTEMPT_SECONDS = 10.0

_REQUEST_HELLO = "Hello"
_REQUEST_AUTHENTICATE = "Authenticate"
_REQUEST_GET_ACTIONS = "GetActions"
_REQUEST_DO_ACTION = "DoAction"
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

    async def _before_transmit(self) -> None:
        """The test seam before the transmission segment; returns at once."""

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
        """Bind the run action; the bridge is not ready until connected."""

        if self._prepared or self._closed:
            return
        spec = declared_run_spec()
        try:
            self._actions.register(spec, self._provider, provider_name=RUN_PROVIDER)
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
            async with self._health:
                self._first_reported = True
                await self._supervision.degraded(reason=failure.reason)
            self._first_settled.set()
            return False
        self._inventory = inventory
        self._connection = connection
        await self._reconcile(inventory)
        # Subscribe (P16) goes here, before readiness.
        async with self._health:
            self._first_reported = True
            self._actions.mark_ready()
            await self._supervision.ready()
        self._first_settled.set()
        return True

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

    def _on_event(self, frame: Mapping[str, Any]) -> None:
        """An inbound event. Subscriptions and the bounded queue come in P16;
        until then nothing is subscribed and an event is not ingested."""

        del frame


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
    return AutomationBridge(
        context, settings, sleeper=settings.get(_SEAM_SLEEPER, asyncio.sleep)
    )


__all__ = [
    "ACKNOWLEDGEMENT_MARGIN_SECONDS",
    "CAUSE_ACKNOWLEDGEMENT_LOST",
    "CAUSE_EXECUTION_UNCONFIRMED",
    "CAUSE_INSTANCE_REJECTED",
    "CHAT_PLATFORMS",
    "DEGRADED_REASONS",
    "ERROR_INSTANCE_ACTION_AMBIGUOUS",
    "ERROR_INSTANCE_ACTION_DISABLED",
    "ERROR_INSTANCE_ACTION_MISSING",
    "ERROR_NOT_ALLOWLISTED",
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
    "AllowlistEntry",
    "AutomationBridge",
    "AutomationBridgeError",
    "ConnectionClosed",
    "InstanceAction",
    "activate",
    "authentication_response",
    "declared_run_spec",
    "run_action_timeout_seconds",
    "validate_settings",
]
