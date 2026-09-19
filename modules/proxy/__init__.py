"""Brain-side proxy: WebSocket JSON server for one paired PC agent (R6).

The brain reaches a capability the streamer's PC holds — ``screen.capture``
in phase 1 — through this module: it listens, accepts **exactly one paired
agent**, and binds itself as the provider of every action its ``actions``
allowlist names, with the specification taken from the brain's discovered
catalog (decision 7). The brain calls the executor exactly as it would for a
local provider; the executor resolves the binding to this module's
:class:`_RemoteProvider`, which forwards the :class:`~core.contracts.ActionCall`
as a ``call`` frame and awaits the matching ``observation``. Nothing else
crosses the wire: no bus subscription, no trace and no run state
(``docs/proxy-protocol.md`` is the normative description of the frames; the
codes are the ``core.contracts`` constants).

**Binding at ``prepare``, readiness at pairing.** ``prepare`` binds the remote
provider over each allowlisted spec's declared destinations. The spec is the
registry's discovered one when an enabled manifest declares the action; when
none does — the server profile, where ``capture`` is disabled and
``screen.capture`` is served remotely only — it is taken from the loader's
full manifest catalog, disabled modules included, and declared under this
module's name before binding, so the same contract is served whichever
module is enabled (decision 7). A name no manifest in the catalog declares
fails preparation naming ``actions[<i>]``; a destination a
local provider already covers raises the registry's
:class:`~core.actions.AmbiguousBindingError`, so enabling ``capture`` and
``proxy`` for the same action fails preparation with the diagnostic naming the
action and both providers (AC41). The module is **not** marked ready at
``prepare``: a call before pairing is ``refused provider_not_ready`` by the
executor, 0 providers entered. Readiness comes with a ``welcome``: every
declared action whose spec is *identical* to the catalog's — name, version,
schemas, nature, permissions, destinations, timeout, idempotency and the
optional ``delivery`` capability — is accepted; a differing one is excluded
with ``error action_mismatch`` and leaves the others. The registry's readiness
is per module, so the module marks itself ready when at least one action is
accepted and the provider itself answers ``refused provider_not_ready`` for an
allowlisted action the agent did not get accepted.

**Session rules (R6, AC32, AC48).** Before ``welcome`` only ``hello`` is
processed; any other frame is ``error unknown_session`` with ``id: null`` and
the connection kept. ``v`` ≠ 1 is ``unsupported_protocol_version`` and close
4400; a ``hello`` with a missing or empty ``id`` is ``invalid_frame`` and close
4400; a token that fails the constant-time comparison is ``auth_failed`` and
close 4401; a second authenticated agent while one is paired — or while a
``hello`` is still being answered — is ``agent_limit`` and close 4409, the
first staying connected; a text frame
above ``max_frame_bytes`` — measured on the UTF-8 encoded frame — is
``frame_too_large`` and close 4413; an unknown ``type`` is ``unknown_frame``
with the connection kept. ``welcome`` echoes the ``hello`` nonce as ``id``,
issues a ``session_id`` drawn from the injected random source and unique per
brain process, lists the accepted action names and the brain's limits.

**Calls and attachments (AC33).** Each ``call`` carries the ``ActionCall``
fields, a per-session ``seq`` from 1, ``deadline_utc`` (informational),
``remaining_ms`` computed from the call deadline on the brain's monotonic
clock, and ``max_attachment_bytes``. Images travel by reference: the agent
sends an ``attachment`` header then exactly one binary frame; the bytes are
stored through ``context.attachments.put`` under the run of the call in
flight — the one the header's optional ``call_id`` names, else the most
recently issued — and acknowledged ``accepted`` or refused
``attachment_too_large`` / ``store_full`` / ``unexpected_binary``. An
``image_ref`` part of the observation names the agent's acknowledged
``attachment_id``; it is rewritten to the store's own reference before the
executor validates the lease, so an id that was never acknowledged reaches
the executor unchanged and is refused ``invalid_result`` there. No observation
carries a filesystem path or a payload.

**Drops (AC35, AC36).** On a disconnection — transport close, a ``pong``
missing ``heartbeat_timeout_seconds`` after a ``ping`` sent every
``heartbeat_seconds`` on the injected sleeper, or the module closing — the
module marks itself not ready and resolves every in-flight call by the nature
of its action: a ``write`` is ``external_unknown`` (cause
``proxy_disconnected``; the provider signalled emission the moment the
``call`` frame left), a ``read`` is ``error proxy_disconnected`` with
``retryable: true``. Nothing is retransmitted. An ``observation`` for a call
that is already terminal — resolved on a drop, timed out or cancelled by the
executor, or already observed — or unknown changes nothing and increments
:attr:`ProxyModule.late_observations`. When the executor cancels a call (run
cancellation, deadline), ``cancel`` is sent best-effort and the executor's
own classification stands.

**Event adapter (AC38).** An ``event`` frame reaches the local bus only for
``event_type`` in :data:`EVENT_ALLOWLIST` (``agent.status``), payload
validated, with ``metadata.source = "proxy"`` and ``metadata.provider_id`` =
the paired agent's id, whatever metadata the frame carried; every other type,
the supervision traces and ``channel.*`` included, is ``error
event_not_allowed`` and publishes nothing.

**Seams, for the tests.** ``connection_handler(ws)`` is the whole per-connection
protocol and runs unchanged over the shared in-memory pair; ``_server_factory``
replaces the ``aiohttp.web`` listener, ``_sleeper`` the heartbeat sleep and
``_wall_clock`` the wall-clock read behind ``deadline_utc``; the monotonic
clock and the random source come from the runtime context. On the real
transport ``max_frame_bytes`` and the binary bound are also enforced on the
frame header, each for its own opcode, by a gate in front of aiohttp's
reader (:class:`_FrameSizeGate`): an oversized text or binary frame is cut
before its payload is buffered (aiohttp then closes 1009 on its own); the
explicit length checks here are what the in-memory path enforces.
"""

from __future__ import annotations

import asyncio
import hmac
import inspect
import ipaddress
import json
import math
import sys
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.actions import ERROR_PROVIDER_NOT_READY, AmbiguousBindingError
from core.attachments import AttachmentRefused
from core.contracts import (
    ATTACHMENT_ACK_STORE_FULL,
    ATTACHMENT_ACK_TOO_LARGE,
    ATTACHMENT_ACK_UNEXPECTED_BINARY,
    BRAIN_ERROR_INVALID_RESULT,
    FRAME_ATTACHMENT,
    FRAME_ATTACHMENT_ACK,
    FRAME_CALL,
    FRAME_CANCEL,
    FRAME_ERROR,
    FRAME_EVENT,
    FRAME_HELLO,
    FRAME_OBSERVATION,
    FRAME_PING,
    FRAME_PONG,
    FRAME_WELCOME,
    IMAGE_CONTENT_TYPES,
    PART_TYPE_IMAGE_REF,
    PROXY_CLOSE_AGENT_LIMIT,
    PROXY_CLOSE_AUTH_FAILED,
    PROXY_CLOSE_BAD_REQUEST,
    PROXY_CLOSE_FRAME_TOO_LARGE,
    PROXY_DEFAULT_MAX_FRAME_BYTES,
    PROXY_ERROR_ACTION_MISMATCH,
    PROXY_ERROR_AGENT_LIMIT,
    PROXY_ERROR_AUTH_FAILED,
    PROXY_ERROR_EVENT_NOT_ALLOWED,
    PROXY_ERROR_FRAME_TOO_LARGE,
    PROXY_ERROR_INVALID_FRAME,
    PROXY_ERROR_PROXY_DISCONNECTED,
    PROXY_ERROR_UNKNOWN_FRAME,
    PROXY_ERROR_UNKNOWN_SESSION,
    PROXY_ERROR_UNSUPPORTED_PROTOCOL_VERSION,
    PROXY_FRAME_TYPES,
    PROXY_PROTOCOL_VERSION,
    ActionObservation,
    ActionSpec,
    ContractError,
    Destination,
)


MODULE_NAME = "proxy"

PROVIDER_NAME = "proxy"
"""Provider name in bindings and traces: what AC41's ambiguity message prints."""

MANIFEST_PATH = Path(__file__).with_name("module.yaml")

AGENT_STATUS_EVENT = "agent.status"
EVENT_ALLOWLIST = frozenset({AGENT_STATUS_EVENT})
"""The only event types the source adapter publishes (R6, AC38)."""

DEFAULT_MAX_ATTACHMENT_BYTES = 5_242_880
DEFAULT_HEARTBEAT_SECONDS = 15.0
DEFAULT_HEARTBEAT_TIMEOUT_SECONDS = 10.0
DEFAULT_LATE_RESULT_SECONDS = 60.0

MAX_TERMINAL_CALLS = 1024
"""How many terminal call identities are remembered for late observations."""

CLOSE_GOING_AWAY = 1001
"""The close code the brain sends when it drops a session itself."""

#: The manifest key the loader's catalog carries the declared actions under.
_MANIFEST_ACTIONS_KEY = "actions"
#: Sentinel: an allowlisted name two catalog manifests declare differently.
_CONFLICTING = object()

# Message types as ``aiohttp`` numbers them; the shared in-memory pair uses
# the same values, so one comparison serves both transports.
_WS_TEXT = 1
_WS_BINARY = 2
_WS_CLOSE = 8
_WS_PING = 9
_WS_PONG = 10
_WS_CLOSING = 256
_WS_CLOSED = 257
_WS_ERROR = 258
_WS_TERMINAL = frozenset({_WS_CLOSE, _WS_CLOSING, _WS_CLOSED, _WS_ERROR})

#: Frame types only the brain sends; received from the agent they are malformed.
_BRAIN_ONLY_FRAMES = frozenset({FRAME_WELCOME, FRAME_CALL, FRAME_CANCEL, FRAME_ATTACHMENT_ACK})

#: The reserved setting the entry point hands its accepted ``limits`` block over in.
_ACCEPTED_LIMITS_SETTING = "limits"

_SETTING_LISTEN = "listen"
_SETTING_TLS = "tls"
_SETTING_PAIRING_TOKEN = "pairing_token"
_SETTING_ACTIONS = "actions"
_SETTING_MAX_FRAME_BYTES = "max_frame_bytes"
_SETTING_MAX_ATTACHMENT_BYTES = "max_attachment_bytes"
_SETTING_HEARTBEAT_SECONDS = "heartbeat_seconds"
_SETTING_HEARTBEAT_TIMEOUT_SECONDS = "heartbeat_timeout_seconds"
_SETTING_LATE_RESULT_SECONDS = "late_result_seconds"
_SETTINGS = frozenset(
    {
        _SETTING_LISTEN,
        _SETTING_TLS,
        _SETTING_PAIRING_TOKEN,
        _SETTING_ACTIONS,
        _SETTING_MAX_FRAME_BYTES,
        _SETTING_MAX_ATTACHMENT_BYTES,
        _SETTING_HEARTBEAT_SECONDS,
        _SETTING_HEARTBEAT_TIMEOUT_SECONDS,
        _SETTING_LATE_RESULT_SECONDS,
    }
)

#: The injection seams: read from *settings*, never from a configuration file.
_SEAM_SERVER_FACTORY = "_server_factory"
_SEAM_SLEEPER = "_sleeper"
_SEAM_WALL_CLOCK = "_wall_clock"
_SEAM_REPORTER = "diagnostic_reporter"
_SEAMS = frozenset({_SEAM_SERVER_FACTORY, _SEAM_SLEEPER, _SEAM_WALL_CLOCK, _SEAM_REPORTER})

_LOOPBACK_NAMES = frozenset({"localhost"})

Clock = Callable[[], float]
Sleeper = Callable[[float], Awaitable[None]]


class ProxyModuleError(RuntimeError):
    """A configuration or lifecycle failure of the proxy module."""


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #


def validate_settings(settings: Any) -> list[str]:
    """Check this module's settings; return one diagnostic per offending field.

    This is the hook ``settings_validator`` in the manifest names. Each
    diagnostic names the module and the field and nothing else — no host, no
    token, no path is echoed. ``listen.host`` must be a loopback address
    unless ``tls`` is configured (R6); every allowlist entry is a non-empty,
    unique action name; every bound is a finite positive number.
    """

    if not isinstance(settings, Mapping):
        return [_setting_diagnostic("settings", "must be a mapping")]
    diagnostics: list[str] = []

    for field_name in settings:
        if (
            field_name not in _SETTINGS
            and field_name != _ACCEPTED_LIMITS_SETTING
            and field_name not in _SEAMS
        ):
            diagnostics.append(
                _setting_diagnostic(str(field_name), "is not a setting this module declares")
            )
    for seam in _SEAMS:
        if seam in settings and not callable(settings[seam]):
            diagnostics.append(_setting_diagnostic(seam, "must be callable"))

    tls_configured = False
    if _SETTING_TLS in settings:
        tls = settings[_SETTING_TLS]
        if not isinstance(tls, Mapping):
            diagnostics.append(_setting_diagnostic(_SETTING_TLS, "must be a mapping"))
        else:
            tls_configured = True
            for key in ("certfile", "keyfile"):
                if not _is_text(tls.get(key)):
                    diagnostics.append(
                        _setting_diagnostic(f"{_SETTING_TLS}.{key}", "must be a non-empty string")
                    )
            for key in tls:
                if key not in ("certfile", "keyfile"):
                    diagnostics.append(
                        _setting_diagnostic(f"{_SETTING_TLS}.{key}", "is not a tls field")
                    )

    listen = settings.get(_SETTING_LISTEN)
    if listen is None:
        diagnostics.append(_setting_diagnostic(_SETTING_LISTEN, "is required"))
    elif not isinstance(listen, Mapping):
        diagnostics.append(_setting_diagnostic(_SETTING_LISTEN, "must be a mapping"))
    else:
        host = listen.get("host")
        if not _is_text(host):
            diagnostics.append(
                _setting_diagnostic(f"{_SETTING_LISTEN}.host", "must be a non-empty string")
            )
        elif not tls_configured and not _is_loopback(host):
            diagnostics.append(
                _setting_diagnostic(
                    f"{_SETTING_LISTEN}.host",
                    "must be a loopback address unless tls is configured",
                )
            )
        port = listen.get("port")
        if not _is_positive_int(port) or port > 65535:
            diagnostics.append(
                _setting_diagnostic(f"{_SETTING_LISTEN}.port", "must be an integer in 1..65535")
            )
        for key in listen:
            if key not in ("host", "port"):
                diagnostics.append(
                    _setting_diagnostic(f"{_SETTING_LISTEN}.{key}", "is not a listen field")
                )

    token = settings.get(_SETTING_PAIRING_TOKEN)
    if token is None:
        diagnostics.append(_setting_diagnostic(_SETTING_PAIRING_TOKEN, "is required"))
    elif not _is_text(token):
        diagnostics.append(
            _setting_diagnostic(_SETTING_PAIRING_TOKEN, "must be a non-empty string")
        )

    actions = settings.get(_SETTING_ACTIONS)
    if actions is None:
        diagnostics.append(_setting_diagnostic(_SETTING_ACTIONS, "is required"))
    elif isinstance(actions, str) or not isinstance(actions, Sequence):
        diagnostics.append(_setting_diagnostic(_SETTING_ACTIONS, "must be a list of action names"))
    else:
        seen: set[str] = set()
        for index, entry in enumerate(actions):
            label = f"{_SETTING_ACTIONS}[{index}]"
            if not _is_text(entry):
                diagnostics.append(_setting_diagnostic(label, "must be a non-empty action name"))
            elif entry in seen:
                diagnostics.append(_setting_diagnostic(label, "duplicates an earlier entry"))
            else:
                seen.add(entry)

    for name in (_SETTING_MAX_FRAME_BYTES, _SETTING_MAX_ATTACHMENT_BYTES):
        if name in settings and not _is_positive_int(settings[name]):
            diagnostics.append(_setting_diagnostic(name, "must be a positive integer"))
    for name in (
        _SETTING_HEARTBEAT_SECONDS,
        _SETTING_HEARTBEAT_TIMEOUT_SECONDS,
        _SETTING_LATE_RESULT_SECONDS,
    ):
        if name in settings and not _is_positive_number(settings[name]):
            diagnostics.append(_setting_diagnostic(name, "must be a finite positive number"))
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


@dataclass(frozen=True, slots=True)
class _Listen:
    host: str
    port: int


@dataclass(frozen=True, slots=True)
class _Tls:
    certfile: str
    keyfile: str


@dataclass(frozen=True, slots=True)
class _Settings:
    """The accepted settings, typed."""

    listen: _Listen
    tls: _Tls | None
    pairing_token: str
    actions: tuple[str, ...]
    max_frame_bytes: int
    max_attachment_bytes: int
    heartbeat_seconds: float
    heartbeat_timeout_seconds: float
    late_result_seconds: float
    #: The store's own per-object bound when the entry point handed the
    #: accepted ``limits`` block over; tightens the binary bound (§6).
    store_max_object_bytes: int | None

    @classmethod
    def from_mapping(cls, settings: Mapping[str, Any]) -> "_Settings":
        listen = settings[_SETTING_LISTEN]
        tls = settings.get(_SETTING_TLS)
        limits = settings.get(_ACCEPTED_LIMITS_SETTING)
        store_bound: int | None = None
        if isinstance(limits, Mapping) and isinstance(limits.get("attachments"), Mapping):
            candidate = limits["attachments"].get("max_object_bytes")
            if _is_positive_int(candidate):
                store_bound = int(candidate)
        return cls(
            listen=_Listen(host=str(listen["host"]).strip(), port=int(listen["port"])),
            tls=(
                _Tls(certfile=str(tls["certfile"]), keyfile=str(tls["keyfile"]))
                if isinstance(tls, Mapping)
                else None
            ),
            pairing_token=str(settings[_SETTING_PAIRING_TOKEN]),
            actions=tuple(str(name) for name in settings[_SETTING_ACTIONS]),
            max_frame_bytes=int(
                settings.get(_SETTING_MAX_FRAME_BYTES, PROXY_DEFAULT_MAX_FRAME_BYTES)
            ),
            max_attachment_bytes=int(
                settings.get(_SETTING_MAX_ATTACHMENT_BYTES, DEFAULT_MAX_ATTACHMENT_BYTES)
            ),
            heartbeat_seconds=float(
                settings.get(_SETTING_HEARTBEAT_SECONDS, DEFAULT_HEARTBEAT_SECONDS)
            ),
            heartbeat_timeout_seconds=float(
                settings.get(
                    _SETTING_HEARTBEAT_TIMEOUT_SECONDS, DEFAULT_HEARTBEAT_TIMEOUT_SECONDS
                )
            ),
            late_result_seconds=float(
                settings.get(_SETTING_LATE_RESULT_SECONDS, DEFAULT_LATE_RESULT_SECONDS)
            ),
            store_max_object_bytes=store_bound,
        )

    @property
    def binary_bound(self) -> int:
        """``min(store max_object_bytes, max_attachment_bytes)`` (§6)."""

        if self.store_max_object_bytes is None:
            return self.max_attachment_bytes
        return min(self.store_max_object_bytes, self.max_attachment_bytes)


# --------------------------------------------------------------------------- #
# Spec declarations on the wire
# --------------------------------------------------------------------------- #


def spec_declaration(spec: ActionSpec) -> dict[str, Any]:
    """The JSON-shaped declaration of *spec* an agent sends in ``hello`` (§5.1).

    Round-trips through :func:`spec_from_declaration` to an equal
    :class:`~core.contracts.ActionSpec`, ``delivery`` included.
    """

    declaration: dict[str, Any] = {
        "name": spec.name,
        "version": spec.version,
        "description": spec.description,
        "argument_schema": _jsonable(spec.argument_schema),
        "result_schema": _jsonable(spec.result_schema),
        "nature": spec.nature,
        "required_permissions": list(spec.required_permissions),
        "supported_destinations": [
            {"platform": d.platform, "channel_id": d.channel_id, "scope": d.scope}
            for d in spec.supported_destinations
        ],
        "timeout_seconds": spec.timeout_seconds,
        "idempotency": spec.idempotency,
    }
    if spec.delivery is not None:
        declaration["delivery"] = _jsonable(spec.delivery)
    return declaration


def spec_from_declaration(declaration: Any) -> ActionSpec | None:
    """Rebuild the :class:`~core.contracts.ActionSpec` a declaration carries.

    ``None`` when the declaration is not an ActionSpec-shaped object: such a
    declaration cannot equal any catalog spec, so it is a mismatch.
    """

    if not isinstance(declaration, Mapping):
        return None
    try:
        destinations = declaration["supported_destinations"]
        if isinstance(destinations, str) or not isinstance(destinations, Sequence):
            return None
        return ActionSpec(
            name=declaration["name"],
            version=declaration["version"],
            description=declaration["description"],
            argument_schema=declaration["argument_schema"],
            result_schema=declaration["result_schema"],
            nature=declaration["nature"],
            required_permissions=tuple(declaration.get("required_permissions", ())),
            supported_destinations=tuple(
                Destination(
                    platform=item.get("platform"),
                    channel_id=item.get("channel_id"),
                    scope=item.get("scope"),
                )
                for item in destinations
                if isinstance(item, Mapping)
            ),
            timeout_seconds=declaration["timeout_seconds"],
            idempotency=declaration["idempotency"],
            delivery=declaration.get("delivery"),
        )
    except (ContractError, KeyError, TypeError, ValueError):
        return None


def _jsonable(value: Any) -> Any:
    """A plain JSON value from the frozen contract mappings and tuples."""

    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


# --------------------------------------------------------------------------- #
# Connection and session state
# --------------------------------------------------------------------------- #


@dataclass(slots=True, eq=False)
class _PendingCall:
    """One ``call`` in flight on a session, awaited by the executor's task."""

    call: Any
    spec: ActionSpec
    seq: int
    future: "asyncio.Future[ActionObservation]"


@dataclass(slots=True, eq=False)
class _Connection:
    """One accepted WebSocket, paired or not."""

    ws: Any
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    closed: bool = False
    session: "_Session | None" = None


@dataclass(slots=True, eq=False)
class _Session:
    """The paired agent: its identity, accepted actions and in-flight state."""

    connection: _Connection
    agent_id: str
    session_id: str
    accepted: frozenset[str]
    next_seq: int = 1
    calls: "OrderedDict[str, _PendingCall]" = field(default_factory=OrderedDict)
    #: Agent-chosen ``attachment_id`` → the store's reference, once acknowledged.
    acknowledged: dict[str, Any] = field(default_factory=dict)
    #: The ``attachment`` header awaiting its one binary frame.
    pending_header: dict[str, Any] | None = None
    heartbeat_task: "asyncio.Task[None] | None" = None
    awaiting_pong: bool = False
    closed: bool = False


# --------------------------------------------------------------------------- #
# The remote provider
# --------------------------------------------------------------------------- #


class _RemoteProvider:
    """The provider bound for every allowlisted action: forwards to the agent."""

    __slots__ = ("_module",)

    name = PROVIDER_NAME

    def __init__(self, module: "ProxyModule") -> None:
        self._module = module

    async def invoke(self, invocation: Any) -> ActionObservation:
        return await self._module._invoke_remote(invocation)


# --------------------------------------------------------------------------- #
# The module
# --------------------------------------------------------------------------- #


class ProxyModule:
    """The v2 handle: bind at ``prepare``, listen at ``start_inputs``, pair once."""

    def __init__(
        self,
        context: Any,
        settings: _Settings,
        *,
        server_factory: Callable[..., Any],
        sleeper: Sleeper,
        wall_clock: Clock,
        reporter: Callable[[str], Any],
        catalog: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> None:
        self._bus = context.bus
        self._actions = context.actions
        self._attachments = context.attachments
        self._tasks = context.tasks
        self._clock = context.clock
        self._rng = context.rng
        self._supervision = getattr(context, "supervision", None)
        self._settings = settings
        self._server_factory = server_factory
        self._sleeper = sleeper
        self._wall_clock = wall_clock
        self._reporter = reporter
        self._provider = _RemoteProvider(self)
        # Every discovered manifest, disabled ones included: where an
        # allowlisted spec comes from when no enabled manifest declares it.
        self._catalog: Mapping[str, Mapping[str, Any]] = catalog if catalog is not None else {}
        self._bound: dict[str, ActionSpec] = {}
        self._server: Any = None
        self._session: _Session | None = None
        # The connection whose ``hello`` is being answered: the pairing slot is
        # taken from the moment the checks pass until the session is assigned,
        # so a second ``hello`` arriving while ``action_mismatch`` replies are
        # in flight is ``agent_limit`` rather than a second pairing.
        self._pairing: _Connection | None = None
        self._connections: set[_Connection] = set()
        self._pairings = 0
        self._late_observations = 0
        self._dropped_events = 0
        # Terminal call identities → the instant they became terminal, so a
        # late observation is told apart from an unknown one in the
        # diagnostic; bounded by count and by ``late_result_seconds``.
        self._terminal: "OrderedDict[str, float]" = OrderedDict()
        self._prepared = False
        self._closed = False

    # -- read-only surface --------------------------------------------------- #

    @property
    def settings(self) -> _Settings:
        return self._settings

    @property
    def late_observations(self) -> int:
        """Observations ignored because their call was terminal or unknown (AC36)."""

        return self._late_observations

    @property
    def dropped_events(self) -> int:
        """Allowlisted events the local bus refused to publish."""

        return self._dropped_events

    @property
    def paired(self) -> bool:
        return self._session is not None

    @property
    def session_id(self) -> str | None:
        return self._session.session_id if self._session is not None else None

    @property
    def agent_id(self) -> str | None:
        return self._session.agent_id if self._session is not None else None

    @property
    def accepted_actions(self) -> frozenset[str]:
        """The allowlisted actions the paired agent declared identically."""

        return self._session.accepted if self._session is not None else frozenset()

    @property
    def bound_actions(self) -> Mapping[str, ActionSpec]:
        return dict(self._bound)

    @property
    def in_flight(self) -> tuple[str, ...]:
        """The ``call_id`` of every call awaiting its observation."""

        return tuple(self._session.calls) if self._session is not None else ()

    # -- lifecycle hooks (R4) ----------------------------------------------- #

    async def prepare(self) -> None:
        """Bind the remote provider for every allowlisted action; ready later.

        The spec comes from the registry's discovered view when an enabled
        manifest declares the action, else from the full manifest catalog —
        declared here under this module's name first, since the registry
        binds declared actions only. A name no manifest declares, or a
        destination a local provider already covers, fails preparation: the
        diagnostic is reported ``module.degraded`` and raised, nothing is
        marked ready (AC41). Pairing, not preparation, makes the actions ready.
        """

        if self._prepared or self._closed:
            return
        discovered = self._actions.discovered()
        for index, name in enumerate(self._settings.actions):
            field_name = f"field 'actions[{index}]'"
            spec = discovered.get(name)
            if spec is None:
                spec = self._spec_from_catalog(name)
                if spec is None:
                    await self._fail_prepare(
                        f"{MODULE_NAME} prepare: {field_name}: {name!r} is not declared "
                        "by any manifest in the catalog"
                    )
                if spec is _CONFLICTING:
                    await self._fail_prepare(
                        f"{MODULE_NAME} prepare: {field_name}: {name!r} is declared "
                        "differently by several manifests in the catalog"
                    )
                try:
                    self._actions.declare(spec)
                except Exception:
                    await self._fail_prepare(
                        f"{MODULE_NAME} prepare: {field_name}: {name!r} could not be "
                        "declared from the catalog"
                    )
            try:
                self._actions.bind(name, self._provider, provider_name=PROVIDER_NAME)
            except AmbiguousBindingError as exc:
                # Names the action and both providers (AC16, AC41).
                await self._fail_prepare(f"{MODULE_NAME} prepare: {exc}")
            except Exception:
                await self._fail_prepare(f"{MODULE_NAME} prepare: {field_name}: binding failed")
            self._bound[name] = spec
        self._prepared = True

    def _spec_from_catalog(self, name: str) -> Any:
        """The spec a manifest of the full catalog declares for *name*.

        ``None`` when no manifest declares it, :data:`_CONFLICTING` when two
        manifests declare it differently — the registry would refuse such a
        pair enabled together, so neither is trusted. Every manifest of the
        catalog was validated at discovery, enabled or not, so an entry that
        does not rebuild into a spec is treated as absent.
        """

        found: ActionSpec | None = None
        for manifest in self._catalog.values():
            entries = manifest.get(_MANIFEST_ACTIONS_KEY) if isinstance(manifest, Mapping) else None
            if isinstance(entries, (str, Mapping)) or not isinstance(entries, Sequence):
                continue
            for entry in entries:
                if not isinstance(entry, Mapping) or entry.get("name") != name:
                    continue
                spec = spec_from_declaration(entry)
                if spec is None:
                    continue
                if found is not None and spec != found:
                    return _CONFLICTING
                found = spec
        return found

    async def _fail_prepare(self, diagnostic: str) -> None:
        self._diagnose(diagnostic)
        await self._report_degraded(diagnostic)
        raise ProxyModuleError(diagnostic)

    async def start_inputs(self) -> None:
        """Open the listener. Only the coordinator, past the barrier, calls this."""

        if self._server is not None or self._closed:
            return
        if not self._prepared:
            raise ProxyModuleError(f"{MODULE_NAME} lifecycle: start_inputs requires prepare")
        ssl_context = self._ssl_context()
        try:
            created = self._server_factory(
                self.connection_handler,
                host=self._settings.listen.host,
                port=self._settings.listen.port,
                ssl_context=ssl_context,
                max_frame_bytes=self._settings.max_frame_bytes,
                max_attachment_bytes=self._settings.binary_bound,
                diagnose=self._diagnose,
            )
            server = await created if inspect.isawaitable(created) else created
        except ProxyModuleError:
            raise
        except Exception:
            diagnostic = f"{MODULE_NAME} start_inputs: the listener could not be opened"
            self._diagnose(diagnostic)
            raise ProxyModuleError(diagnostic) from None
        self._server = server

    def _ssl_context(self) -> Any:
        tls = self._settings.tls
        if tls is None:
            return None
        import ssl

        try:
            context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
            context.load_cert_chain(certfile=tls.certfile, keyfile=tls.keyfile)
        except Exception:
            diagnostic = f"{MODULE_NAME} start_inputs: field 'tls': certificate or key could not be loaded"
            self._diagnose(diagnostic)
            raise ProxyModuleError(diagnostic) from None
        return context

    async def stop_inputs(self) -> None:
        """Stop accepting connections; the paired agent stays until ``close``."""

        server = self._server
        if server is None:
            return
        stop = getattr(server, "stop", None)
        if callable(stop):
            try:
                outcome = stop()
                if inspect.isawaitable(outcome):
                    await outcome
            except asyncio.CancelledError:
                raise
            except Exception:
                self._diagnose(f"{MODULE_NAME} stop_inputs: the listener did not stop cleanly")

    async def close(self) -> None:
        """Drop the paired agent, close every connection and the listener."""

        if self._closed:
            return
        self._closed = True
        try:
            session = self._session
            if session is not None:
                await self._teardown_session(session, "proxy closing")
            for connection in tuple(self._connections):
                await self._close_ws(connection, CLOSE_GOING_AWAY)
        finally:
            server, self._server = self._server, None
            if server is not None:
                closer = getattr(server, "close", None)
                if callable(closer):
                    try:
                        outcome = closer()
                        if inspect.isawaitable(outcome):
                            await outcome
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        self._diagnose(f"{MODULE_NAME} close: the listener did not close cleanly")
            if self._bound:
                try:
                    self._actions.mark_not_ready()
                except Exception:
                    pass

    # -- one connection (§1–§4) -------------------------------------------- #

    async def connection_handler(self, ws: Any) -> None:
        """Run protocol v1 over *ws* until it closes. The whole brain side.

        Usable directly with an in-memory end: everything the listener does
        is hand each accepted WebSocket to this coroutine.
        """

        connection = _Connection(ws=ws)
        self._connections.add(connection)
        try:
            while not connection.closed:
                message = await ws.receive()
                kind = message.type
                if kind in _WS_TERMINAL:
                    if kind == _WS_ERROR:
                        # aiohttp cut the frame (its size bound, a protocol
                        # error) and closed on its own; say why, best-effort.
                        await self._refuse(
                            connection, None, PROXY_ERROR_FRAME_TOO_LARGE,
                            "frame refused by the transport", PROXY_CLOSE_FRAME_TOO_LARGE,
                        )
                    break
                if kind == _WS_TEXT:
                    await self._on_text(connection, message.data)
                elif kind == _WS_BINARY:
                    await self._on_binary(connection, message.data)
                # Transport-level ping/pong are answered by the transport.
        finally:
            self._connections.discard(connection)
            connection.closed = True
            session = connection.session
            if session is not None:
                await self._teardown_session(session, "connection closed")

    async def _on_text(self, connection: _Connection, data: Any) -> None:
        text = data if isinstance(data, str) else bytes(data).decode("utf-8", "replace")
        if len(text.encode("utf-8")) > self._settings.max_frame_bytes:
            await self._refuse(
                connection, None, PROXY_ERROR_FRAME_TOO_LARGE,
                f"text frame above max_frame_bytes ({self._settings.max_frame_bytes})",
                PROXY_CLOSE_FRAME_TOO_LARGE,
            )
            return
        try:
            frame = json.loads(text)
        except ValueError:
            await self._error(connection, None, PROXY_ERROR_INVALID_FRAME, "frame is not JSON")
            return
        if not isinstance(frame, Mapping):
            await self._error(connection, None, PROXY_ERROR_INVALID_FRAME, "frame is not an object")
            return

        frame_id = frame.get("id")
        answer_id = frame_id if isinstance(frame_id, str) else None
        version = frame.get("v")
        if isinstance(version, bool) or version != PROXY_PROTOCOL_VERSION:
            await self._refuse(
                connection, answer_id, PROXY_ERROR_UNSUPPORTED_PROTOCOL_VERSION,
                f"protocol version must be {PROXY_PROTOCOL_VERSION}", PROXY_CLOSE_BAD_REQUEST,
            )
            return
        frame_type = frame.get("type")
        if not isinstance(frame_type, str):
            await self._error(connection, answer_id, PROXY_ERROR_INVALID_FRAME, "frame has no type")
            return
        if frame_type not in PROXY_FRAME_TYPES:
            await self._error(connection, answer_id, PROXY_ERROR_UNKNOWN_FRAME, "unknown frame type")
            return

        session = connection.session
        if session is None:
            if frame_type != FRAME_HELLO:
                await self._error(
                    connection, None, PROXY_ERROR_UNKNOWN_SESSION, "no session: hello first"
                )
                return
            await self._on_hello(connection, frame)
            return

        if frame_type == FRAME_HELLO:
            await self._error(connection, answer_id, PROXY_ERROR_INVALID_FRAME, "already paired")
        elif frame_type in _BRAIN_ONLY_FRAMES:
            await self._error(
                connection, answer_id, PROXY_ERROR_INVALID_FRAME, "frame is sent by the brain only"
            )
        elif frame_type == FRAME_ERROR:
            self._on_error_frame(session, frame)
        elif frame_type == FRAME_OBSERVATION:
            await self._on_observation(session, frame)
        else:
            # Session-scoped: ping, pong, event, attachment (§3).
            if frame_id != session.session_id:
                await self._error(
                    connection, answer_id, PROXY_ERROR_UNKNOWN_SESSION, "id is not the session"
                )
                return
            if frame_type == FRAME_PING:
                await self._send(connection, {"type": FRAME_PONG, "id": session.session_id})
            elif frame_type == FRAME_PONG:
                session.awaiting_pong = False
            elif frame_type == FRAME_EVENT:
                await self._on_event(session, frame)
            elif frame_type == FRAME_ATTACHMENT:
                await self._on_attachment_header(session, frame)

    # -- pairing (§4) -------------------------------------------------------- #

    async def _on_hello(self, connection: _Connection, frame: Mapping[str, Any]) -> None:
        nonce = frame.get("id")
        if not isinstance(nonce, str) or not nonce:
            await self._refuse(
                connection, None, PROXY_ERROR_INVALID_FRAME,
                "hello requires a non-empty string id", PROXY_CLOSE_BAD_REQUEST,
            )
            return
        token = frame.get("token")
        if not isinstance(token, str):
            await self._refuse(
                connection, nonce, PROXY_ERROR_INVALID_FRAME,
                "hello requires a token", PROXY_CLOSE_BAD_REQUEST,
            )
            return
        if not hmac.compare_digest(
            token.encode("utf-8"), self._settings.pairing_token.encode("utf-8")
        ):
            await self._refuse(
                connection, nonce, PROXY_ERROR_AUTH_FAILED,
                "pairing token refused", PROXY_CLOSE_AUTH_FAILED,
            )
            return
        if self._session is not None or self._pairing is not None:
            await self._refuse(
                connection, nonce, PROXY_ERROR_AGENT_LIMIT,
                "an agent is already paired", PROXY_CLOSE_AGENT_LIMIT, retryable=True,
            )
            return
        agent_id = frame.get("agent_id")
        declared = frame.get("actions")
        max_frame_bytes = frame.get("max_frame_bytes")
        if (
            not _is_text(agent_id)
            or isinstance(declared, str)
            or not isinstance(declared, Sequence)
            or not all(isinstance(item, Mapping) and _is_text(item.get("name")) for item in declared)
            or not _is_positive_int(max_frame_bytes)
        ):
            await self._refuse(
                connection, nonce, PROXY_ERROR_INVALID_FRAME,
                "hello requires agent_id, actions[] and max_frame_bytes", PROXY_CLOSE_BAD_REQUEST,
            )
            return

        # From here to the session assignment nothing else may pair: the
        # slot is reserved across the awaited ``action_mismatch`` replies and
        # released whatever the outcome.
        self._pairing = connection
        try:
            accepted: list[str] = []
            for declaration in declared:
                name = declaration["name"]
                catalog_spec = self._bound.get(name)
                if catalog_spec is None:
                    continue  # Declared but not allowlisted: ignored (§4).
                if spec_from_declaration(declaration) == catalog_spec:
                    if name not in accepted:
                        accepted.append(name)
                else:
                    await self._error(
                        connection, nonce, PROXY_ERROR_ACTION_MISMATCH,
                        f"declared action {name!r} differs from the catalog specification",
                    )
            if connection.closed or self._closed:
                return

            self._pairings += 1
            session = _Session(
                connection=connection,
                agent_id=str(agent_id),
                session_id=self._new_session_id(),
                accepted=frozenset(accepted),
            )
            connection.session = session
            self._session = session
        finally:
            if self._pairing is connection:
                self._pairing = None
        await self._send(
            connection,
            {
                "type": FRAME_WELCOME,
                "id": nonce,
                "session_id": session.session_id,
                "actions": [name for name in self._settings.actions if name in session.accepted],
                "limits": {
                    "max_frame_bytes": self._settings.max_frame_bytes,
                    "max_attachment_bytes": self._settings.binary_bound,
                    "heartbeat_seconds": self._settings.heartbeat_seconds,
                    "heartbeat_timeout_seconds": self._settings.heartbeat_timeout_seconds,
                },
            },
        )
        if connection.closed:
            return
        if session.accepted:
            self._actions.mark_ready()
        session.heartbeat_task = self._tasks.spawn(
            self._heartbeat(session), name=f"proxy-heartbeat-{self._pairings}"
        )
        await self._report_ready(session)

    def _new_session_id(self) -> str:
        """A session identifier from the injected random source, never reused.

        The pairing counter guarantees uniqueness within the process whatever
        the source returns; the random part keeps it unguessable.
        """

        draws = "".join(f"{int(self._rng.random() * 65536) & 0xFFFF:04x}" for _ in range(8))
        return f"{self._pairings:x}-{draws}"

    # -- heartbeat (§12) ----------------------------------------------------- #

    async def _heartbeat(self, session: _Session) -> None:
        settings = self._settings
        while not session.closed:
            await self._sleeper(settings.heartbeat_seconds)
            if session.closed:
                return
            session.awaiting_pong = True
            try:
                await self._send(session.connection, {"type": FRAME_PING, "id": session.session_id})
            except Exception:
                await self._drop(session, "ping could not be sent")
                return
            await self._sleeper(settings.heartbeat_timeout_seconds)
            if session.closed:
                return
            if session.awaiting_pong:
                await self._drop(session, "heartbeat timeout")
                return

    async def _drop(self, session: _Session, reason: str) -> None:
        """Drop *session* from the brain's side: classify, then close the socket."""

        await self._teardown_session(session, reason)
        await self._close_ws(session.connection, CLOSE_GOING_AWAY)

    # -- disconnection (§11) ------------------------------------------------- #

    async def _teardown_session(self, session: _Session, reason: str) -> None:
        """Resolve the drop exactly once: readiness, in-flight calls, health."""

        if session.closed:
            return
        session.closed = True
        session.connection.session = None
        if self._session is session:
            self._session = None
        if self._bound:
            self._actions.mark_not_ready()

        task = session.heartbeat_task
        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel()

        now = self._clock()
        pending = list(session.calls.values())
        session.calls.clear()
        session.acknowledged.clear()
        session.pending_header = None
        for entry in pending:
            self._remember_terminal(entry.call.call_id, now)
            if entry.future.done():
                continue
            entry.future.set_result(self._disconnected_observation(session, entry))

        if not self._closed:
            # A drop while running is a degradation; the module's own close
            # is reported ``stopped`` by the coordinator, not degraded here.
            await self._report_degraded(f"{MODULE_NAME}: agent disconnected ({reason})")

    def _disconnected_observation(self, session: _Session, entry: _PendingCall) -> ActionObservation:
        """The drop classification by nature (R6, AC35)."""

        provenance = self._provenance(session, entry.seq)
        if entry.spec.nature == "write":
            return ActionObservation(
                status="external_unknown",
                provenance=provenance,
                error={
                    "code": PROXY_ERROR_PROXY_DISCONNECTED,
                    "message": (
                        f"the agent disconnected while {entry.call.action_name!r} was in "
                        "flight; the effect may have been engaged"
                    ),
                    "retryable": False,
                },
            )
        return ActionObservation(
            status="error",
            provenance=provenance,
            error={
                "code": PROXY_ERROR_PROXY_DISCONNECTED,
                "message": (
                    f"the agent disconnected while {entry.call.action_name!r} was in flight"
                ),
                "retryable": True,
            },
        )

    # -- calls (§5.4, §9) ---------------------------------------------------- #

    async def _invoke_remote(self, invocation: Any) -> ActionObservation:
        call = invocation.call
        spec = invocation.spec
        session = self._session
        if session is None or call.action_name not in session.accepted:
            return ActionObservation(
                status="refused",
                provenance={"provider": PROVIDER_NAME},
                error={
                    "code": ERROR_PROVIDER_NOT_READY,
                    "message": (
                        f"no paired agent serves {call.action_name!r}"
                        if session is None
                        else f"the paired agent did not declare {call.action_name!r} identically"
                    ),
                    "retryable": False,
                },
            )

        seq = session.next_seq
        session.next_seq += 1
        loop = asyncio.get_running_loop()
        entry = _PendingCall(call=call, spec=spec, seq=seq, future=loop.create_future())
        session.calls[call.call_id] = entry

        remaining = max(0.0, float(call.deadline) - self._clock())
        frame = {
            "type": FRAME_CALL,
            "id": call.call_id,
            "action_name": call.action_name,
            "action_version": call.action_version,
            "arguments": _jsonable(call.arguments),
            "conversation_id": call.conversation_id,
            "run_id": call.run_id,
            "call_id": call.call_id,
            "source_event_id": call.source_event_id,
            "destination": {
                "platform": call.destination.platform,
                "channel_id": call.destination.channel_id,
                "scope": call.destination.scope,
            },
            "principal": call.principal,
            "message_id": call.message_id,
            "contract_version": call.contract_version,
            "seq": seq,
            "deadline_utc": self._deadline_utc(remaining),
            "remaining_ms": int(remaining * 1000),
            "max_attachment_bytes": self._settings.binary_bound,
        }
        try:
            await self._send(session.connection, frame)
        except asyncio.CancelledError:
            session.calls.pop(call.call_id, None)
            self._remember_terminal(call.call_id, self._clock())
            raise
        except Exception:
            # The transport refused the frame: the call is in flight from the
            # executor's point of view and is classified by nature, the
            # conservative side for a write whose bytes may have left.
            session.calls.pop(call.call_id, None)
            self._remember_terminal(call.call_id, self._clock())
            return self._disconnected_observation(session, entry)
        if spec.nature == "write":
            invocation.mark_emitted()

        try:
            return await entry.future
        except asyncio.CancelledError:
            # Run cancellation or deadline: the executor classifies; tell the
            # agent best-effort and forget the call (a later observation is
            # late, AC36).
            session.calls.pop(call.call_id, None)
            self._remember_terminal(call.call_id, self._clock())
            if not session.closed:
                try:
                    await self._send(session.connection, {"type": FRAME_CANCEL, "id": call.call_id})
                except Exception:
                    pass
            raise

    def _deadline_utc(self, remaining: float) -> str:
        instant = datetime.fromtimestamp(float(self._wall_clock()) + remaining, tz=timezone.utc)
        return instant.isoformat(timespec="milliseconds").replace("+00:00", "Z")

    def _provenance(self, session: _Session, seq: int) -> dict[str, Any]:
        return {
            "provider": PROVIDER_NAME,
            "agent_id": session.agent_id,
            "session_id": session.session_id,
            "seq": seq,
        }

    def _remember_terminal(self, call_id: str, now: float) -> None:
        self._terminal[call_id] = now
        self._terminal.move_to_end(call_id)
        ttl = self._settings.late_result_seconds
        while self._terminal:
            oldest_id, oldest_at = next(iter(self._terminal.items()))
            if len(self._terminal) > MAX_TERMINAL_CALLS or now - oldest_at > ttl:
                self._terminal.popitem(last=False)
            else:
                break

    # -- observations (§5.5, §11) ------------------------------------------- #

    async def _on_observation(self, session: _Session, frame: Mapping[str, Any]) -> None:
        call_id = frame.get("id")
        entry = session.calls.get(call_id) if isinstance(call_id, str) else None
        if entry is None or entry.future.done():
            self._late_observations += 1
            return
        session.calls.pop(call_id, None)
        self._remember_terminal(call_id, self._clock())
        entry.future.set_result(self._observation_from_frame(session, entry, frame))

    def _observation_from_frame(
        self, session: _Session, entry: _PendingCall, frame: Mapping[str, Any]
    ) -> ActionObservation:
        provenance = self._provenance(session, entry.seq)
        remote = frame.get("provenance")
        if isinstance(remote, Mapping) and remote:
            provenance["remote"] = _jsonable(remote)
        parts = frame.get("parts", [])
        try:
            if isinstance(parts, str) or not isinstance(parts, Sequence):
                raise ContractError("ActionObservation.parts", "must be a list")
            return ActionObservation(
                status=frame.get("status"),
                provenance=provenance,
                result=frame.get("result"),
                error=frame.get("error"),
                parts=tuple(self._resolve_part(session, part) for part in parts),
            )
        except (ContractError, TypeError, ValueError) as exc:
            # A malformed frame for a write the agent did not say it refused:
            # the effect is uncertain, so the status stays on the
            # conservative side (R2, R5).
            uncertain = entry.spec.nature == "write" and frame.get("status") != "refused"
            return ActionObservation(
                status="external_unknown" if uncertain else "error",
                provenance=provenance,
                error={
                    "code": BRAIN_ERROR_INVALID_RESULT,
                    "message": f"observation frame is malformed: {exc}",
                    "retryable": False,
                },
            )

    def _resolve_part(self, session: _Session, part: Any) -> Any:
        """Rewrite an acknowledged ``image_ref`` to the store's own reference."""

        if not isinstance(part, Mapping) or part.get("type") != PART_TYPE_IMAGE_REF:
            return part
        ref = session.acknowledged.get(part.get("attachment_id"))
        if ref is None:
            return part
        resolved = dict(part)
        resolved["attachment_id"] = ref.attachment_id
        return resolved

    def _on_error_frame(self, session: _Session, frame: Mapping[str, Any]) -> None:
        """An ``error`` answering one of our ``call`` frames resolves that call.

        The agent states it did not process the call (a malformed frame, a
        duplicate it cannot vouch for), so the outcome is an ``error`` with
        the agent's code; an ``error`` naming no in-flight call is ignored.
        """

        call_id = frame.get("id")
        entry = session.calls.get(call_id) if isinstance(call_id, str) else None
        if entry is None or entry.future.done():
            return
        session.calls.pop(call_id, None)
        self._remember_terminal(call_id, self._clock())
        code = frame.get("code")
        retryable = frame.get("retryable")
        entry.future.set_result(
            ActionObservation(
                status="error",
                provenance=self._provenance(session, entry.seq),
                error={
                    "code": code if _is_text(code) else PROXY_ERROR_INVALID_FRAME,
                    "message": "the agent refused the call frame",
                    "retryable": retryable if isinstance(retryable, bool) else False,
                },
            )
        )

    # -- attachments (§10) --------------------------------------------------- #

    async def _on_attachment_header(self, session: _Session, frame: Mapping[str, Any]) -> None:
        attachment_id = frame.get("attachment_id")
        content_type = frame.get("content_type")
        size = frame.get("size")
        if (
            not _is_text(attachment_id)
            or content_type not in IMAGE_CONTENT_TYPES
            or not _is_positive_int(size)
        ):
            session.pending_header = None
            await self._error(
                session.connection, session.session_id, PROXY_ERROR_INVALID_FRAME,
                "attachment requires attachment_id, an image content_type and a positive size",
            )
            return
        session.pending_header = None
        if size > self._settings.binary_bound:
            await self._ack(session, attachment_id, ATTACHMENT_ACK_TOO_LARGE)
            return
        call_id = frame.get("call_id")
        run_id = self._run_for_attachment(session, call_id)
        if run_id is None:
            await self._ack(session, attachment_id, ATTACHMENT_ACK_UNEXPECTED_BINARY)
            return
        session.pending_header = {
            "attachment_id": attachment_id,
            "content_type": content_type,
            "size": size,
            "run_id": run_id,
        }

    def _run_for_attachment(self, session: _Session, call_id: Any) -> str | None:
        """The run an attachment is leased to: the named call's, else the latest's."""

        if isinstance(call_id, str) and call_id in session.calls:
            return session.calls[call_id].call.run_id
        if not session.calls:
            return None
        latest = max(session.calls.values(), key=lambda entry: entry.seq)
        return latest.call.run_id

    async def _on_binary(self, connection: _Connection, data: Any) -> None:
        session = connection.session
        payload = bytes(data) if not isinstance(data, bytes) else data
        length = len(payload)
        if session is None:
            await self._error(
                connection, None, PROXY_ERROR_UNKNOWN_SESSION, "binary frame before pairing"
            )
            return
        header = session.pending_header
        session.pending_header = None
        bound = self._settings.binary_bound
        if length > bound or (header is not None and length > header["size"]):
            await self._refuse(
                connection, session.session_id, PROXY_ERROR_FRAME_TOO_LARGE,
                "binary frame above its bound", PROXY_CLOSE_FRAME_TOO_LARGE,
            )
            return
        if header is None or length != header["size"]:
            await self._ack(
                session,
                header["attachment_id"] if header is not None else None,
                ATTACHMENT_ACK_UNEXPECTED_BINARY,
            )
            return
        try:
            ref = self._attachments.put(
                header["run_id"], payload, content_type=header["content_type"]
            )
        except AttachmentRefused as refused:
            code = (
                ATTACHMENT_ACK_TOO_LARGE
                if refused.limit == "max_object_bytes"
                else ATTACHMENT_ACK_STORE_FULL
            )
            await self._ack(session, header["attachment_id"], code)
            return
        except Exception:
            await self._ack(session, header["attachment_id"], ATTACHMENT_ACK_STORE_FULL)
            return
        session.acknowledged[header["attachment_id"]] = ref
        await self._ack(session, header["attachment_id"], None)

    async def _ack(self, session: _Session, attachment_id: str | None, code: str | None) -> None:
        frame: dict[str, Any] = {
            "type": FRAME_ATTACHMENT_ACK,
            "id": session.session_id,
            "attachment_id": attachment_id,
            "accepted": code is None,
        }
        if code is not None:
            frame["code"] = code
        await self._send(session.connection, frame)

    # -- events (§15) -------------------------------------------------------- #

    async def _on_event(self, session: _Session, frame: Mapping[str, Any]) -> None:
        event_type = frame.get("event_type")
        if not isinstance(event_type, str) or event_type not in EVENT_ALLOWLIST:
            await self._error(
                session.connection, session.session_id, PROXY_ERROR_EVENT_NOT_ALLOWED,
                "event type is not allowlisted",
            )
            return
        payload = frame.get("payload")
        if not isinstance(payload, Mapping) or not _is_text(payload.get("state")):
            await self._error(
                session.connection, session.session_id, PROXY_ERROR_INVALID_FRAME,
                f"{event_type} requires a payload with a non-empty state",
            )
            return
        metadata = {
            "source": MODULE_NAME,
            "provider_id": session.agent_id,
            "session_id": session.session_id,
        }
        try:
            await self._bus.publish(event_type, dict(payload), metadata)
        except asyncio.CancelledError:
            raise
        except Exception:
            self._dropped_events += 1

    # -- sending ------------------------------------------------------------- #

    async def _send(self, connection: _Connection, frame: Mapping[str, Any]) -> None:
        body = {"v": PROXY_PROTOCOL_VERSION, **frame}
        async with connection.send_lock:
            await connection.ws.send_str(json.dumps(body, separators=(",", ":")))

    async def _error(
        self, connection: _Connection, answer_id: str | None, code: str, message: str,
        *, retryable: bool = False,
    ) -> None:
        try:
            await self._send(
                connection,
                {"type": FRAME_ERROR, "id": answer_id, "code": code,
                 "message": message, "retryable": retryable},
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            pass

    async def _refuse(
        self, connection: _Connection, answer_id: str | None, code: str, message: str,
        close_code: int, *, retryable: bool = False,
    ) -> None:
        await self._error(connection, answer_id, code, message, retryable=retryable)
        await self._close_ws(connection, close_code)

    async def _close_ws(self, connection: _Connection, code: int) -> None:
        connection.closed = True
        try:
            await connection.ws.close(code=code)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass

    # -- health and diagnostics --------------------------------------------- #

    async def _report_ready(self, session: _Session) -> None:
        ready = getattr(self._supervision, "ready", None)
        if not callable(ready):
            return
        try:
            outcome = ready(capabilities=sorted(session.accepted), agent_id=session.agent_id)
            if inspect.isawaitable(outcome):
                await outcome
        except asyncio.CancelledError:
            raise
        except Exception:
            return

    async def _report_degraded(self, reason: str) -> None:
        degraded = getattr(self._supervision, "degraded", None)
        if not callable(degraded):
            return
        try:
            outcome = degraded(reason=reason)
            if inspect.isawaitable(outcome):
                await outcome
        except asyncio.CancelledError:
            raise
        except Exception:
            return

    def _diagnose(self, message: str) -> None:
        try:
            self._reporter(message)
        except Exception:
            pass


def _default_reporter(message: str) -> None:
    print(message, file=sys.stderr)


# --------------------------------------------------------------------------- #
# The default listener
# --------------------------------------------------------------------------- #

# RFC 6455 framing, the little the gate reads: opcodes and header layout.
_OP_CONTINUATION = 0x0
_OP_TEXT = 0x1
_OP_BINARY = 0x2
_OP_NOT_SET = -1
_GATE_HEADER = 0
_GATE_LENGTH = 1
_GATE_PAYLOAD = 2
_WS_CLOSE_MESSAGE_TOO_BIG = 1009


class _FrameTooLarge(Exception):
    """A data frame whose declared length would take its message past its bound."""


class _MessageTooBig(Exception):
    """The gate's own reader failure: close code 1009 and its reason.

    The shipped listener hands the gate ``aiohttp``'s ``WebSocketError`` in
    its place; this default keeps the gate, and its tests, free of ``aiohttp``.
    """

    def __init__(self, code: int, message: str) -> None:
        super().__init__(code, message)
        self.code = code
        self.message = message

    def __str__(self) -> str:
        return self.message


class _FrameSizeGate:
    """Bound text and binary messages apart, on the frame header (§6).

    ``aiohttp`` bounds every message with one ``max_msg_size``; protocol v1
    bounds text frames by ``max_frame_bytes`` and binary frames by the binary
    bound, and requires the latter applied **before** the payload is buffered.
    This gate sits in front of ``aiohttp``'s parser: it reads each frame
    header — opcode and declared payload length, the only fields it needs —
    and, when a text or binary message would grow past its own bound, fails
    the socket's reader with ``MESSAGE_TOO_BIG`` (close 1009) without handing
    the payload on. Every other byte passes through untouched, so framing,
    masking, control frames and close handshakes stay ``aiohttp``'s business.
    Fragmented messages are bounded on their accumulated length, as
    ``aiohttp`` does. Compression is off on the listener, so a declared
    length is the payload's length.

    ``error`` builds the exception the reader is failed with, from the close
    code and a reason; the listener passes ``aiohttp``'s ``WebSocketError``
    so the reader closes 1009 as for its own bound. The gate itself never
    imports ``aiohttp``.
    """

    __slots__ = (
        "_binary_bound",
        "_error",
        "_failed",
        "_header",
        "_inner",
        "_message_len",
        "_message_opcode",
        "_queue",
        "_skip",
        "_state",
        "_text_bound",
    )

    def __init__(
        self,
        inner: Any,
        queue: Any,
        text_bound: int,
        binary_bound: int,
        error: Callable[[int, str], BaseException] = _MessageTooBig,
    ) -> None:
        self._inner = inner
        self._queue = queue
        self._error = error
        self._text_bound = int(text_bound)
        self._binary_bound = int(binary_bound)
        self._state = _GATE_HEADER
        self._header = bytearray()
        self._skip = 0
        self._message_opcode = _OP_NOT_SET
        self._message_len = 0
        self._failed = False

    @property
    def failed(self) -> bool:
        return self._failed

    def feed_eof(self) -> None:
        self._inner.feed_eof()

    def feed_data(self, data: Any) -> tuple[bool, bytes]:
        if type(data) is not bytes:
            data = bytes(data)
        if self._failed:
            return True, data
        try:
            self._scan(data)
        except _FrameTooLarge as exc:
            self._failed = True
            self._queue.set_exception(self._error(_WS_CLOSE_MESSAGE_TOO_BIG, str(exc)))
            return True, b""
        return self._inner.feed_data(data)

    def _scan(self, data: bytes) -> None:
        """Walk the frame headers of *data*; raise before an oversized payload."""

        position = 0
        length = len(data)
        while position < length:
            if self._state == _GATE_PAYLOAD:
                taken = min(self._skip, length - position)
                self._skip -= taken
                position += taken
                if self._skip == 0:
                    self._state = _GATE_HEADER
                continue
            self._header.append(data[position])
            position += 1
            if self._state == _GATE_HEADER:
                if len(self._header) < 2:
                    continue
                length_flag = self._header[1] & 0x7F
                if length_flag < 126:
                    self._frame(length_flag)
                else:
                    self._state = _GATE_LENGTH
                continue
            # _GATE_LENGTH: 2 more bytes after flag 126, 8 after 127.
            wanted = 4 if (self._header[1] & 0x7F) == 126 else 10
            if len(self._header) < wanted:
                continue
            self._frame(int.from_bytes(self._header[2:wanted], "big"))

    def _frame(self, payload_len: int) -> None:
        """One complete header: bound a data frame, then skip mask and payload."""

        first, second = self._header[0], self._header[1]
        fin = bool(first & 0x80)
        opcode = first & 0x0F
        masked = bool(second & 0x80)
        if opcode in (_OP_TEXT, _OP_BINARY, _OP_CONTINUATION):
            message_opcode = opcode if opcode != _OP_CONTINUATION else self._message_opcode
            projected = self._message_len + payload_len
            if message_opcode == _OP_BINARY and projected > self._binary_bound:
                raise _FrameTooLarge(
                    f"binary message of {projected} bytes exceeds the binary bound "
                    f"{self._binary_bound}"
                )
            if message_opcode == _OP_TEXT and projected > self._text_bound:
                raise _FrameTooLarge(
                    f"text message of {projected} bytes exceeds max_frame_bytes "
                    f"{self._text_bound}"
                )
            # An unknown continuation is aiohttp's protocol error to raise.
            if fin:
                self._message_opcode = _OP_NOT_SET
                self._message_len = 0
            else:
                self._message_opcode = message_opcode
                self._message_len = projected
        self._header.clear()
        self._skip = payload_len + (4 if masked else 0)
        self._state = _GATE_PAYLOAD if self._skip else _GATE_HEADER


def _bounded_response_class() -> Any:
    """The ``aiohttp.web.WebSocketResponse`` subclass carrying the gate.

    Built on first use so importing this module never imports ``aiohttp``.
    """

    from aiohttp import WebSocketError, web

    class _BoundedWebSocketResponse(web.WebSocketResponse):
        """A ``WebSocketResponse`` whose reader is fronted by :class:`_FrameSizeGate`.

        ``max_msg_size`` stays the larger of the two bounds so ``aiohttp``'s own
        check never precedes the gate's; the gate is installed right after
        ``aiohttp`` sets its parser. ``gate_installed`` says whether it was:
        the installation reaches two private ``aiohttp`` seams (the response's
        ``_post_start`` and the request handler's parser slot) and degrades
        to the single reader bound, reported by the listener, if they moved.
        """

        gate_installed = False

        def __init__(self, *, text_bound: int, binary_bound: int) -> None:
            super().__init__(
                max_msg_size=max(text_bound, binary_bound), autoping=True, compress=False
            )
            self._gate_text_bound = text_bound
            self._gate_binary_bound = binary_bound

        def _post_start(self, request: Any, protocol: Any, writer: Any) -> None:
            super()._post_start(request, protocol, writer)
            handler = getattr(request, "protocol", None)
            inner = getattr(handler, "_payload_parser", None)
            queue = getattr(self, "_reader", None)
            if (
                inner is None
                or queue is None
                or not callable(getattr(inner, "feed_data", None))
                or not callable(getattr(queue, "set_exception", None))
            ):
                return
            handler._payload_parser = _FrameSizeGate(
                inner,
                queue,
                self._gate_text_bound,
                self._gate_binary_bound,
                error=WebSocketError,
            )
            self.gate_installed = True

    return _BoundedWebSocketResponse


class _AiohttpServer:
    """The shipped listener: an ``aiohttp.web`` site handing sockets to the handler."""

    __slots__ = ("_runner", "_site")

    def __init__(self, runner: Any, site: Any) -> None:
        self._runner = runner
        self._site = site

    async def stop(self) -> None:
        """Stop listening; connections already accepted stay open."""

        await self._site.stop()

    async def close(self) -> None:
        await self._runner.cleanup()


async def default_server_factory(
    handler: Callable[[Any], Awaitable[None]],
    *,
    host: str,
    port: int,
    ssl_context: Any,
    max_frame_bytes: int,
    max_attachment_bytes: int,
    diagnose: Callable[[str], Any] = _default_reporter,
) -> _AiohttpServer:
    """Open the ``aiohttp.web`` WebSocket listener behind ``start_inputs``.

    Every accepted socket is a bounded ``WebSocketResponse``: text frames are
    bounded by *max_frame_bytes* and binary frames by *max_attachment_bytes*,
    each on its frame header, before the payload is buffered (§6). Should the
    gate not install on this ``aiohttp`` — its private seams moved — the
    reader's single bound, the larger of the two, still applies and
    *diagnose* is told once per listener.
    """

    from aiohttp import web

    response_class = _bounded_response_class()
    reported = False

    async def endpoint(request: Any) -> Any:
        nonlocal reported
        ws = response_class(text_bound=max_frame_bytes, binary_bound=max_attachment_bytes)
        if not ws.can_prepare(request).ok:
            return web.Response(status=426, text="websocket required")
        await ws.prepare(request)
        if not ws.gate_installed and not reported:
            reported = True
            diagnose(
                f"{MODULE_NAME} start_inputs: the per-frame size gate did not install on this "
                "aiohttp; binary frames are bounded by the reader's single bound only"
            )
        await handler(ws)
        return ws

    app = web.Application()
    app.router.add_get("/{tail:.*}", endpoint)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port, ssl_context=ssl_context)
    try:
        await site.start()
    except BaseException:
        await runner.cleanup()
        raise
    return _AiohttpServer(runner, site)


# --------------------------------------------------------------------------- #
# Activation (R7)
# --------------------------------------------------------------------------- #


async def activate(
    context: Any,
    settings: Mapping[str, Any],
    catalog: Mapping[str, Mapping[str, Any]],
) -> ProxyModule:
    """Build the handle from the scoped runtime context (R7).

    Settings are checked through the same hook the loader ran, so a handle
    built outside the loader is refused on the same terms. The seams
    ``_server_factory``, ``_sleeper``, ``_wall_clock`` and
    ``diagnostic_reporter`` are read from *settings* and default to the
    ``aiohttp.web`` listener, ``asyncio.sleep``, ``time.time`` and stderr.
    *catalog* — every discovered manifest, disabled ones included — is kept
    for ``prepare``: an allowlisted action no enabled manifest declares takes
    its spec from there (decision 7). Nothing here reaches the network: the
    listener opens in ``start_inputs``.
    """

    _require_runtime_surfaces(context)
    if not isinstance(settings, Mapping):
        raise ProxyModuleError(f"{MODULE_NAME} configuration: settings must be a mapping")
    diagnostics = validate_settings(settings)
    if diagnostics:
        raise ProxyModuleError(
            f"{MODULE_NAME} configuration: settings were refused "
            f"({len(diagnostics)} diagnostics)"
        )
    parsed = _Settings.from_mapping(settings)
    return ProxyModule(
        context,
        parsed,
        server_factory=settings.get(_SEAM_SERVER_FACTORY, default_server_factory),
        sleeper=settings.get(_SEAM_SLEEPER, asyncio.sleep),
        wall_clock=settings.get(_SEAM_WALL_CLOCK, time.time),
        reporter=settings.get(_SEAM_REPORTER, _default_reporter),
        catalog=catalog if isinstance(catalog, Mapping) else None,
    )


def _require_runtime_surfaces(context: Any) -> None:
    """Refuse a context lacking the bus, the actions facade, a store, tasks or a clock."""

    actions = getattr(context, "actions", None)
    attachments = getattr(context, "attachments", None)
    tasks = getattr(context, "tasks", None)
    bus = getattr(context, "bus", None)
    clock = getattr(context, "clock", None)
    rng = getattr(context, "rng", None)
    if (
        actions is None
        or not all(
            callable(getattr(actions, method, None))
            for method in ("bind", "declare", "discovered", "mark_ready", "mark_not_ready")
        )
        or attachments is None
        or not callable(getattr(attachments, "put", None))
        or tasks is None
        or not callable(getattr(tasks, "spawn", None))
        or bus is None
        or not callable(getattr(bus, "publish", None))
        or not callable(clock)
        or rng is None
        or not callable(getattr(rng, "random", None))
    ):
        raise ProxyModuleError(f"{MODULE_NAME} activation: runtime context is invalid")


__all__ = [
    "AGENT_STATUS_EVENT",
    "CLOSE_GOING_AWAY",
    "DEFAULT_HEARTBEAT_SECONDS",
    "DEFAULT_HEARTBEAT_TIMEOUT_SECONDS",
    "DEFAULT_LATE_RESULT_SECONDS",
    "DEFAULT_MAX_ATTACHMENT_BYTES",
    "EVENT_ALLOWLIST",
    "MANIFEST_PATH",
    "MAX_TERMINAL_CALLS",
    "MODULE_NAME",
    "PROVIDER_NAME",
    "ProxyModule",
    "ProxyModuleError",
    "activate",
    "default_server_factory",
    "spec_declaration",
    "spec_from_declaration",
    "validate_settings",
]
