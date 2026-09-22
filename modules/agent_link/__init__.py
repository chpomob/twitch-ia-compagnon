"""Agent-side link: WebSocket JSON client dialing the brain's proxy (R6).

The PC agent holds a capability the brain needs — ``screen.capture`` in
phase 1 — and this module is how it lends it: it dials ``brain_url``, pairs
with the brain-side ``modules/proxy`` and executes every ``call`` it receives
**through the agent's own default-deny executor** with the principal the
brain forwarded. Peer authentication grants nothing: a call the agent's
authorization rules do not permit is ``refused not_authorized`` locally, 0
providers entered, exactly as it would be for a local caller. Nothing else
crosses the wire: no bus subscription, no trace and no run state
(``docs/proxy-protocol.md`` is the normative description of the frames; the
codes are the ``core.contracts`` constants).

**Preparation and pairing.** ``prepare`` resolves every name of ``actions``
from the local discovered catalog — the specs the agent's own modules
declared — and fails naming ``actions[<i>]`` for a name no local manifest
declares. ``start_inputs`` spawns the dial loop through the supervised task
registry; each attempt sends ``hello`` (a nonce drawn from the injected
random source, ``agent_id``, the token, the ActionSpec-shaped declarations
of the resolved specs — ``delivery`` included — and ``max_frame_bytes``) and
awaits ``welcome``. A ``welcome`` resets the backoff, resets ``last_seq`` to
0, clears the call table, reports the module ready with the accepted
actions and sends ``event agent.status {state: paired}``.

**Reconnection (decision 9, AC37).** A connection failure, a refused pairing
or a drop is one consecutive failure *n*: the loop waits a full-jitter draw
``rng.random() × min(max_seconds, initial_seconds × multiplier^n)`` on the
injected sleeper and dials again, without limit, until ``stop_inputs``. A
``welcome`` resets *n* to 0, so the next failure starts from the base again.

**Call identity (R6, AC36).** Per session the agent keeps ``last_seq`` and a
call table of at most ``max_entries`` ``(call_id → (seq, observation))``
entries, each retained ``ttl_seconds`` on the agent's injected clock from
the instant the call was accepted or until displaced by a newer entry. A
``call`` with ``seq > last_seq`` is new: it is executed and ``last_seq``
becomes its ``seq``. ``seq ≤ last_seq`` with a retained ``call_id`` re-sends
the retained observation, byte-identical, without executing — a repeat that
arrives while the first execution still runs waits for the same
observation. ``seq ≤ last_seq`` with a ``call_id`` not retained is refused
``error duplicate_call_unknown`` (``retryable: false``), 0 executions. A
missing or non-positive ``seq``, or a ``call_id`` retained under another
``seq``, is ``error invalid_frame``. Both are reset on every ``welcome``.

**Deadlines (AC34).** The local :class:`~core.contracts.ActionCall` carries
``deadline = now + min(remaining_ms / 1000, action_seconds)`` on the agent's
monotonic clock; ``deadline_utc`` is informational and never read for the
bound, so wall-clock skew between the hosts cannot lengthen a call.

**Attachments (AC33; phase 2 R5).** For every ``image_ref`` or
``audio_ref`` part of the local observation the bytes are read from the agent's store, sent as one
``attachment`` header plus exactly one binary frame under the send lock,
and the ``attachment_ack`` awaited **before** the ``observation`` leaves. An
ack that is not ``accepted`` — or bytes the store no longer holds, or a
payload above the call's ``max_attachment_bytes`` — drops the part and turns
the observation into ``error attachment_refused``. The local bytes are
discarded once transferred or refused: the agent retains no image past its
observation. No observation carries a filesystem path or a payload.

**Drops.** ``cancel`` cancels the local execution task — found by
``call_id`` among the running executions, which the bounded call table
does not govern: an execution evicted from the table while still running
stays cancellable. The executor's own classification stands, nothing is
sent for a cancelled call, and no image the local run leased survives,
whether the cancel lands during the provider call or during the transfer
of its image (the ack awaited). A
disconnection — transport close, a ``pong`` missing
``heartbeat_timeout_seconds`` after a ``ping`` sent every
``heartbeat_seconds``, the brain closing — reports the module degraded and
starts the backoff; an execution still running finishes locally and its
observation is not sent on a later session (nothing is retransmitted).

**Seams, for the tests.** ``connection_handler(ws)`` is the whole
per-connection protocol and runs unchanged over the shared in-memory pair;
``_connector`` replaces the ``aiohttp`` dial, ``_sleeper`` the backoff and
heartbeat sleep; the monotonic clock and the random source come from the
runtime context. On the real transport ``max_frame_bytes`` is also enforced
by the client's ``max_msg_size``; the explicit length check here is what the
in-memory path enforces.
"""

from __future__ import annotations

import asyncio
import inspect
import ipaddress
import json
import math
import sys
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from core.actions import ERROR_PROVIDER_FAILED
from core.contracts import (
    ATTACHMENT_ACK_TOO_LARGE,
    BRAIN_ERROR_ATTACHMENT_REFUSED,
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
    PART_TYPE_AUDIO_REF,
    PART_TYPE_IMAGE_REF,
    PROXY_CLOSE_BAD_REQUEST,
    PROXY_CLOSE_FRAME_TOO_LARGE,
    PROXY_DEFAULT_MAX_FRAME_BYTES,
    PROXY_ERROR_DUPLICATE_CALL_UNKNOWN,
    PROXY_ERROR_FRAME_TOO_LARGE,
    PROXY_ERROR_INVALID_FRAME,
    PROXY_ERROR_UNKNOWN_FRAME,
    PROXY_ERROR_UNKNOWN_SESSION,
    PROXY_ERROR_UNSUPPORTED_PROTOCOL_VERSION,
    PROXY_FRAME_TYPES,
    PROXY_PROTOCOL_VERSION,
    ActionCall,
    ActionObservation,
    ActionSpec,
    ContractError,
    Destination,
)


MODULE_NAME = "agent_link"

MANIFEST_PATH = Path(__file__).with_name("module.yaml")

AGENT_STATUS_EVENT = "agent.status"
STATE_PAIRED = "paired"
"""The ``agent.status`` state sent right after every ``welcome``."""

ERROR_ATTACHMENT_REFUSED = BRAIN_ERROR_ATTACHMENT_REFUSED
"""The observation error code when the brain refused an attachment transfer."""

_REFERENCE_PART_TYPES = frozenset({PART_TYPE_IMAGE_REF, PART_TYPE_AUDIO_REF})
"""Part types naming an attachment the agent transfers before the observation (R5)."""

DEFAULT_RECONNECT_INITIAL_SECONDS = 1.0
DEFAULT_RECONNECT_MULTIPLIER = 2.0
DEFAULT_RECONNECT_MAX_SECONDS = 30.0
DEFAULT_HEARTBEAT_SECONDS = 15.0
DEFAULT_HEARTBEAT_TIMEOUT_SECONDS = 10.0
DEFAULT_CALL_TABLE_MAX_ENTRIES = 1024
DEFAULT_CALL_TABLE_TTL_SECONDS = 300.0
DEFAULT_ACTION_SECONDS = 10.0

CLOSE_GOING_AWAY = 1001
"""The close code the agent sends when it drops a session itself."""

#: The ack code recorded when the session dropped before the ack arrived.
_ACK_DISCONNECTED = "disconnected"
#: The ack code recorded when the local store no longer held the bytes.
_ACK_UNAVAILABLE = "attachment_unavailable"

# Message types as ``aiohttp`` numbers them; the shared in-memory pair uses
# the same values, so one comparison serves both transports.
_WS_TEXT = 1
_WS_BINARY = 2
_WS_CLOSE = 8
_WS_CLOSING = 256
_WS_CLOSED = 257
_WS_ERROR = 258
_WS_TERMINAL = frozenset({_WS_CLOSE, _WS_CLOSING, _WS_CLOSED, _WS_ERROR})

#: Frame types only the agent sends; received from the brain they are malformed.
_AGENT_ONLY_FRAMES = frozenset({FRAME_HELLO, FRAME_OBSERVATION, FRAME_ATTACHMENT, FRAME_EVENT})

#: The reserved setting the entry point hands its accepted ``limits`` block over in.
_ACCEPTED_LIMITS_SETTING = "limits"

_SETTING_BRAIN_URL = "brain_url"
_SETTING_PAIRING_TOKEN = "pairing_token"
_SETTING_AGENT_ID = "agent_id"
_SETTING_ACTIONS = "actions"
_SETTING_RECONNECT = "reconnect"
_SETTING_HEARTBEAT_SECONDS = "heartbeat_seconds"
_SETTING_HEARTBEAT_TIMEOUT_SECONDS = "heartbeat_timeout_seconds"
_SETTING_CALL_TABLE = "call_table"
_SETTING_MAX_FRAME_BYTES = "max_frame_bytes"
_SETTING_ACTION_SECONDS = "action_seconds"
_SETTINGS = frozenset(
    {
        _SETTING_BRAIN_URL,
        _SETTING_PAIRING_TOKEN,
        _SETTING_AGENT_ID,
        _SETTING_ACTIONS,
        _SETTING_RECONNECT,
        _SETTING_HEARTBEAT_SECONDS,
        _SETTING_HEARTBEAT_TIMEOUT_SECONDS,
        _SETTING_CALL_TABLE,
        _SETTING_MAX_FRAME_BYTES,
        _SETTING_ACTION_SECONDS,
    }
)
_RECONNECT_FIELDS = ("initial_seconds", "multiplier", "max_seconds")
_CALL_TABLE_FIELDS = ("max_entries", "ttl_seconds")

#: The injection seams: read from *settings*, never from a configuration file.
_SEAM_CONNECTOR = "_connector"
_SEAM_SLEEPER = "_sleeper"
_SEAM_REPORTER = "diagnostic_reporter"
_SEAMS = frozenset({_SEAM_CONNECTOR, _SEAM_SLEEPER, _SEAM_REPORTER})

_SCHEME_PLAIN = "ws"
_SCHEME_TLS = "wss"
_LOOPBACK_NAMES = frozenset({"localhost"})

Clock = Callable[[], float]
Sleeper = Callable[[float], Awaitable[None]]


class AgentLinkError(RuntimeError):
    """A configuration or lifecycle failure of the agent link."""


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #


def validate_settings(settings: Any) -> list[str]:
    """Check this module's settings; return one diagnostic per offending field.

    This is the hook ``settings_validator`` in the manifest names. Each
    diagnostic names the module and the field and nothing else — no URL, no
    token is echoed. ``brain_url`` must use ``ws`` or ``wss``; a
    non-loopback host must use ``wss`` (R6); every ``actions`` entry is a
    non-empty, unique action name; every bound is a finite positive number.
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

    url = settings.get(_SETTING_BRAIN_URL)
    if url is None:
        diagnostics.append(_setting_diagnostic(_SETTING_BRAIN_URL, "is required"))
    elif not _is_text(url):
        diagnostics.append(_setting_diagnostic(_SETTING_BRAIN_URL, "must be a non-empty string"))
    else:
        diagnostics.extend(_url_diagnostics(url))

    token = settings.get(_SETTING_PAIRING_TOKEN)
    if token is None:
        diagnostics.append(_setting_diagnostic(_SETTING_PAIRING_TOKEN, "is required"))
    elif not _is_text(token):
        diagnostics.append(
            _setting_diagnostic(_SETTING_PAIRING_TOKEN, "must be a non-empty string")
        )

    agent_id = settings.get(_SETTING_AGENT_ID)
    if agent_id is None:
        diagnostics.append(_setting_diagnostic(_SETTING_AGENT_ID, "is required"))
    elif not _is_text(agent_id):
        diagnostics.append(_setting_diagnostic(_SETTING_AGENT_ID, "must be a non-empty string"))

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

    if _SETTING_RECONNECT in settings:
        reconnect = settings[_SETTING_RECONNECT]
        if not isinstance(reconnect, Mapping):
            diagnostics.append(_setting_diagnostic(_SETTING_RECONNECT, "must be a mapping"))
        else:
            for key in reconnect:
                label = f"{_SETTING_RECONNECT}.{key}"
                if key not in _RECONNECT_FIELDS:
                    diagnostics.append(_setting_diagnostic(label, "is not a reconnect field"))
                elif not _is_positive_number(reconnect[key]):
                    diagnostics.append(
                        _setting_diagnostic(label, "must be a finite positive number")
                    )
                elif key == "multiplier" and reconnect[key] < 1:
                    diagnostics.append(_setting_diagnostic(label, "must be at least 1"))

    if _SETTING_CALL_TABLE in settings:
        table = settings[_SETTING_CALL_TABLE]
        if not isinstance(table, Mapping):
            diagnostics.append(_setting_diagnostic(_SETTING_CALL_TABLE, "must be a mapping"))
        else:
            for key in table:
                label = f"{_SETTING_CALL_TABLE}.{key}"
                if key not in _CALL_TABLE_FIELDS:
                    diagnostics.append(_setting_diagnostic(label, "is not a call_table field"))
                elif key == "max_entries" and not _is_positive_int(table[key]):
                    diagnostics.append(_setting_diagnostic(label, "must be a positive integer"))
                elif key == "ttl_seconds" and not _is_positive_number(table[key]):
                    diagnostics.append(
                        _setting_diagnostic(label, "must be a finite positive number")
                    )

    if _SETTING_MAX_FRAME_BYTES in settings and not _is_positive_int(
        settings[_SETTING_MAX_FRAME_BYTES]
    ):
        diagnostics.append(
            _setting_diagnostic(_SETTING_MAX_FRAME_BYTES, "must be a positive integer")
        )
    for name in (
        _SETTING_HEARTBEAT_SECONDS,
        _SETTING_HEARTBEAT_TIMEOUT_SECONDS,
        _SETTING_ACTION_SECONDS,
    ):
        if name in settings and not _is_positive_number(settings[name]):
            diagnostics.append(_setting_diagnostic(name, "must be a finite positive number"))
    return diagnostics


def _url_diagnostics(url: str) -> list[str]:
    """The R6 rule on ``brain_url``: ``ws`` on loopback only, ``wss`` anywhere."""

    try:
        parts = urlsplit(url.strip())
        host = parts.hostname
    except ValueError:
        return [_setting_diagnostic(_SETTING_BRAIN_URL, "is not a valid URL")]
    if parts.scheme not in (_SCHEME_PLAIN, _SCHEME_TLS):
        return [_setting_diagnostic(_SETTING_BRAIN_URL, "must use the ws or wss scheme")]
    if not host:
        return [_setting_diagnostic(_SETTING_BRAIN_URL, "must name a host")]
    if parts.scheme == _SCHEME_PLAIN and not _is_loopback(host):
        return [
            _setting_diagnostic(
                _SETTING_BRAIN_URL, "must use wss unless the host is a loopback address"
            )
        ]
    return []


def _setting_diagnostic(field_name: str, reason: str) -> str:
    return f"module {MODULE_NAME!r}: field {field_name!r}: {reason}"


def _is_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def _is_non_negative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


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
class _Reconnect:
    initial_seconds: float
    multiplier: float
    max_seconds: float


@dataclass(frozen=True, slots=True)
class _CallTable:
    max_entries: int
    ttl_seconds: float


@dataclass(frozen=True, slots=True)
class _Settings:
    """The accepted settings, typed."""

    brain_url: str
    pairing_token: str
    agent_id: str
    actions: tuple[str, ...]
    reconnect: _Reconnect
    heartbeat_seconds: float
    heartbeat_timeout_seconds: float
    call_table: _CallTable
    max_frame_bytes: int
    action_seconds: float

    @classmethod
    def from_mapping(cls, settings: Mapping[str, Any]) -> "_Settings":
        reconnect = settings.get(_SETTING_RECONNECT)
        reconnect = reconnect if isinstance(reconnect, Mapping) else {}
        table = settings.get(_SETTING_CALL_TABLE)
        table = table if isinstance(table, Mapping) else {}
        return cls(
            brain_url=str(settings[_SETTING_BRAIN_URL]).strip(),
            pairing_token=str(settings[_SETTING_PAIRING_TOKEN]),
            agent_id=str(settings[_SETTING_AGENT_ID]).strip(),
            actions=tuple(str(name) for name in settings[_SETTING_ACTIONS]),
            reconnect=_Reconnect(
                initial_seconds=float(
                    reconnect.get("initial_seconds", DEFAULT_RECONNECT_INITIAL_SECONDS)
                ),
                multiplier=float(reconnect.get("multiplier", DEFAULT_RECONNECT_MULTIPLIER)),
                max_seconds=float(reconnect.get("max_seconds", DEFAULT_RECONNECT_MAX_SECONDS)),
            ),
            heartbeat_seconds=float(
                settings.get(_SETTING_HEARTBEAT_SECONDS, DEFAULT_HEARTBEAT_SECONDS)
            ),
            heartbeat_timeout_seconds=float(
                settings.get(
                    _SETTING_HEARTBEAT_TIMEOUT_SECONDS, DEFAULT_HEARTBEAT_TIMEOUT_SECONDS
                )
            ),
            call_table=_CallTable(
                max_entries=int(table.get("max_entries", DEFAULT_CALL_TABLE_MAX_ENTRIES)),
                ttl_seconds=float(table.get("ttl_seconds", DEFAULT_CALL_TABLE_TTL_SECONDS)),
            ),
            max_frame_bytes=int(
                settings.get(_SETTING_MAX_FRAME_BYTES, PROXY_DEFAULT_MAX_FRAME_BYTES)
            ),
            action_seconds=float(settings.get(_SETTING_ACTION_SECONDS, DEFAULT_ACTION_SECONDS)),
        )


# --------------------------------------------------------------------------- #
# Spec declarations on the wire
# --------------------------------------------------------------------------- #


def spec_declaration(spec: ActionSpec) -> dict[str, Any]:
    """The JSON-shaped declaration of *spec* sent in ``hello`` (protocol §5.1).

    Every compared field of the :class:`~core.contracts.ActionSpec` — name,
    version, description, schemas, nature, permissions, destinations,
    timeout, idempotency and the optional ``delivery`` capability — so the
    brain's comparison against its catalog finds the declaration identical.
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


def _jsonable(value: Any) -> Any:
    """A plain JSON value from the frozen contract mappings and tuples."""

    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _encode(frame: Mapping[str, Any]) -> str:
    return json.dumps({"v": PROXY_PROTOCOL_VERSION, **frame}, separators=(",", ":"))


# --------------------------------------------------------------------------- #
# Connection and session state
# --------------------------------------------------------------------------- #


@dataclass(slots=True, eq=False)
class _CallEntry:
    """One accepted ``call``: retained from acceptance until it expires (§8)."""

    call_id: str
    seq: int
    accepted_at: float
    #: The encoded ``observation`` frame once terminal; a repeat re-sends it.
    future: "asyncio.Future[str | None]"


@dataclass(slots=True, eq=False)
class _Connection:
    """One dialed WebSocket, paired or not."""

    ws: Any
    nonce: str
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    closed: bool = False
    paired: bool = False
    session: "_Session | None" = None


@dataclass(slots=True, eq=False)
class _Session:
    """The paired brain: its session id, the accepted actions and the bounded call state."""

    connection: _Connection
    session_id: str
    accepted: frozenset[str]
    limits: Mapping[str, Any]
    last_seq: int = 0
    table: "OrderedDict[str, _CallEntry]" = field(default_factory=OrderedDict)
    #: Every execution still running, by ``call_id``: what ``cancel`` targets.
    #: Independent of ``table`` — eviction from the bounded cache (max_entries,
    #: ttl) never makes a running call uncancellable.
    running: "dict[str, asyncio.Task[None]]" = field(default_factory=dict)
    transfer_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    #: The ``attachment`` header awaiting its ack: ``(attachment_id, future)``.
    pending_ack: "tuple[str, asyncio.Future[str | None]] | None" = None
    heartbeat_task: "asyncio.Task[None] | None" = None
    awaiting_pong: bool = False
    closed: bool = False


# --------------------------------------------------------------------------- #
# The module
# --------------------------------------------------------------------------- #


class AgentLinkModule:
    """The v2 handle: resolve at ``prepare``, dial at ``start_inputs``, serve calls."""

    def __init__(
        self,
        context: Any,
        settings: _Settings,
        *,
        connector: Callable[..., Any],
        sleeper: Sleeper,
        reporter: Callable[[str], Any],
    ) -> None:
        self._actions = context.actions
        self._executor = context.executor
        self._attachments = context.attachments
        self._tasks = context.tasks
        self._clock = context.clock
        self._rng = context.rng
        self._supervision = getattr(context, "supervision", None)
        self._settings = settings
        self._connector = connector
        self._sleeper = sleeper
        self._reporter = reporter
        self._specs: dict[str, ActionSpec] = {}
        self._connection: _Connection | None = None
        self._session: _Session | None = None
        self._dial_task: "asyncio.Task[None] | None" = None
        self._executions: set["asyncio.Task[None]"] = set()
        self._attempts = 0
        self._failures = 0
        self._pairings = 0
        self._execution_count = 0
        self._errors_received = 0
        self._prepared = False
        self._stopping = False
        self._closed = False

    # -- read-only surface --------------------------------------------------- #

    @property
    def settings(self) -> _Settings:
        return self._settings

    @property
    def declared_actions(self) -> Mapping[str, ActionSpec]:
        """The resolved specs ``hello`` declares, in ``actions`` order."""

        return dict(self._specs)

    @property
    def paired(self) -> bool:
        return self._session is not None

    @property
    def session_id(self) -> str | None:
        return self._session.session_id if self._session is not None else None

    @property
    def accepted_actions(self) -> frozenset[str]:
        """The declared actions the current ``welcome`` accepted."""

        return self._session.accepted if self._session is not None else frozenset()

    @property
    def last_seq(self) -> int:
        return self._session.last_seq if self._session is not None else 0

    @property
    def retained_calls(self) -> tuple[str, ...]:
        """The ``call_id`` of every entry the call table holds, oldest first."""

        return tuple(self._session.table) if self._session is not None else ()

    @property
    def in_flight(self) -> tuple[str, ...]:
        """The ``call_id`` of every execution still running."""

        if self._session is None:
            return ()
        return tuple(call_id for call_id, task in self._session.running.items() if not task.done())

    @property
    def attempts(self) -> int:
        """How many dials the loop made so far."""

        return self._attempts

    @property
    def consecutive_failures(self) -> int:
        """The backoff exponent: failures since the last ``welcome``."""

        return self._failures

    @property
    def pairings(self) -> int:
        return self._pairings

    @property
    def executions(self) -> int:
        """How many ``call`` frames reached the local executor."""

        return self._execution_count

    @property
    def errors_received(self) -> int:
        """How many ``error`` frames the brain sent."""

        return self._errors_received

    @property
    def running(self) -> bool:
        return self._dial_task is not None and not self._dial_task.done()

    # -- lifecycle hooks (R4) ----------------------------------------------- #

    async def prepare(self) -> None:
        """Resolve every listed action from the local discovered catalog.

        A name no local manifest declares fails preparation naming
        ``actions[<i>]``: the diagnostic is reported ``module.degraded`` and
        raised, and nothing is dialed. Pairing, not preparation, is what
        lends the actions to the brain.
        """

        if self._prepared or self._closed:
            return
        discovered = self._actions.discovered()
        resolved: dict[str, ActionSpec] = {}
        for index, name in enumerate(self._settings.actions):
            spec = discovered.get(name)
            if spec is None:
                diagnostic = (
                    f"{MODULE_NAME} prepare: field 'actions[{index}]': {name!r} is not "
                    "declared by any local manifest"
                )
                self._diagnose(diagnostic)
                await self._report_degraded(diagnostic)
                raise AgentLinkError(diagnostic)
            resolved[name] = spec
        self._specs = resolved
        self._prepared = True

    async def start_inputs(self) -> None:
        """Spawn the dial loop. Only the coordinator, past the barrier, calls this."""

        if self.running or self._stopping or self._closed:
            return
        if not self._prepared:
            raise AgentLinkError(f"{MODULE_NAME} lifecycle: start_inputs requires prepare")
        self._dial_task = self._tasks.spawn(self._dial_forever(), name="agent-link-dial")

    async def stop_inputs(self) -> None:
        """Close the connection and stop the dial loop; nothing reconnects."""

        self._stopping = True
        connection = self._connection
        if connection is not None:
            await self._close_ws(connection, CLOSE_GOING_AWAY)
        task, self._dial_task = self._dial_task, None
        if task is not None and not task.done() and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        session = self._session
        if session is not None:
            await self._teardown_session(session, "agent link stopping")

    async def close(self) -> None:
        """Stop, then cancel every execution still running."""

        if self._closed:
            return
        self._closed = True
        await self.stop_inputs()
        running = tuple(task for task in self._executions if not task.done())
        for task in running:
            task.cancel()
        if running:
            await asyncio.gather(*running, return_exceptions=True)

    # -- the dial loop (§13) ------------------------------------------------- #

    async def _dial_forever(self) -> None:
        while not self._stopping:
            self._attempts += 1
            try:
                ws = await self._connect()
            except asyncio.CancelledError:
                raise
            except Exception:
                self._diagnose(f"{MODULE_NAME} dial: the connection could not be opened")
                if self._stopping:
                    return
                await self._backoff()
                continue
            await self.connection_handler(ws)
            if self._stopping:
                return
            await self._backoff()

    async def _connect(self) -> Any:
        created = self._connector(
            self._settings.brain_url, max_frame_bytes=self._settings.max_frame_bytes
        )
        return await created if inspect.isawaitable(created) else created

    async def _backoff(self) -> None:
        """Wait the full-jitter delay of the current failure count, then count it."""

        delay = self._rng.random() * self._delay_base(self._failures)
        self._failures += 1
        await self._sleeper(delay)

    def _delay_base(self, failures: int) -> float:
        reconnect = self._settings.reconnect
        try:
            grown = reconnect.initial_seconds * (reconnect.multiplier ** failures)
        except OverflowError:
            return reconnect.max_seconds
        return min(reconnect.max_seconds, grown)

    # -- one connection (§1–§4) -------------------------------------------- #

    async def connection_handler(self, ws: Any) -> bool:
        """Run protocol v1 over *ws* until it closes. The whole agent side.

        Sends ``hello`` first, then serves every frame. Returns whether a
        ``welcome`` was received on this connection. Usable directly with an
        in-memory end: everything the dial loop does is hand each opened
        socket to this coroutine.
        """

        connection = _Connection(ws=ws, nonce=self._new_nonce())
        self._connection = connection
        try:
            await self._send(connection, self._hello_frame(connection.nonce))
            while not connection.closed:
                message = await ws.receive()
                kind = message.type
                if kind in _WS_TERMINAL:
                    if kind == _WS_ERROR:
                        self._diagnose(f"{MODULE_NAME} connection: frame refused by the transport")
                    break
                if kind == _WS_TEXT:
                    await self._on_text(connection, message.data)
                elif kind == _WS_BINARY:
                    await self._error(
                        connection, None, PROXY_ERROR_INVALID_FRAME,
                        "the brain sends no binary frame",
                    )
        except asyncio.CancelledError:
            raise
        except Exception:
            self._diagnose(f"{MODULE_NAME} connection: the transport failed")
        finally:
            connection.closed = True
            session = connection.session
            if session is not None:
                await self._teardown_session(session, "connection closed")
            await self._close_ws(connection, CLOSE_GOING_AWAY)
            if self._connection is connection:
                self._connection = None
        return connection.paired

    def _hello_frame(self, nonce: str) -> dict[str, Any]:
        return {
            "type": FRAME_HELLO,
            "id": nonce,
            "agent_id": self._settings.agent_id,
            "token": self._settings.pairing_token,
            "actions": [spec_declaration(spec) for spec in self._specs.values()],
            "max_frame_bytes": self._settings.max_frame_bytes,
        }

    def _new_nonce(self) -> str:
        """A hello nonce from the injected random source, unique per attempt."""

        draws = "".join(f"{int(self._rng.random() * 65536) & 0xFFFF:04x}" for _ in range(4))
        return f"{self._attempts:x}-{draws}"

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
            if frame_type == FRAME_WELCOME:
                await self._on_welcome(connection, frame)
            elif frame_type == FRAME_ERROR:
                self._on_error_frame(frame)
            else:
                await self._error(
                    connection, None, PROXY_ERROR_UNKNOWN_SESSION, "no session: welcome first"
                )
            return

        if frame_type == FRAME_WELCOME:
            await self._error(connection, answer_id, PROXY_ERROR_INVALID_FRAME, "already paired")
        elif frame_type in _AGENT_ONLY_FRAMES:
            await self._error(
                connection, answer_id, PROXY_ERROR_INVALID_FRAME, "frame is sent by the agent only"
            )
        elif frame_type == FRAME_ERROR:
            self._on_error_frame(frame)
        elif frame_type == FRAME_CALL:
            await self._on_call(session, frame)
        elif frame_type == FRAME_CANCEL:
            self._on_cancel(session, frame)
        else:
            # Session-scoped: ping, pong, attachment_ack (§3).
            if frame_id != session.session_id:
                await self._error(
                    connection, answer_id, PROXY_ERROR_UNKNOWN_SESSION, "id is not the session"
                )
                return
            if frame_type == FRAME_PING:
                await self._send(connection, {"type": FRAME_PONG, "id": session.session_id})
            elif frame_type == FRAME_PONG:
                session.awaiting_pong = False
            elif frame_type == FRAME_ATTACHMENT_ACK:
                self._on_attachment_ack(session, frame)

    # -- pairing (§4) -------------------------------------------------------- #

    async def _on_welcome(self, connection: _Connection, frame: Mapping[str, Any]) -> None:
        if frame.get("id") != connection.nonce:
            await self._error(
                connection, None, PROXY_ERROR_INVALID_FRAME, "welcome does not answer this hello"
            )
            return
        session_id = frame.get("session_id")
        accepted = frame.get("actions")
        limits = frame.get("limits")
        if (
            not _is_text(session_id)
            or isinstance(accepted, str)
            or not isinstance(accepted, Sequence)
            or not all(isinstance(name, str) for name in accepted)
        ):
            await self._refuse(
                connection, connection.nonce, PROXY_ERROR_INVALID_FRAME,
                "welcome requires a session_id and actions[]", PROXY_CLOSE_BAD_REQUEST,
            )
            return

        # Every welcome starts a fresh session: seq from 0, an empty table.
        session = _Session(
            connection=connection,
            session_id=str(session_id),
            accepted=frozenset(name for name in accepted if name in self._specs),
            limits=dict(limits) if isinstance(limits, Mapping) else {},
        )
        connection.session = session
        connection.paired = True
        self._session = session
        self._failures = 0
        self._pairings += 1
        excluded = [name for name in self._specs if name not in session.accepted]
        if excluded:
            self._diagnose(
                f"{MODULE_NAME} pairing: the brain did not accept "
                f"{len(excluded)} of {len(self._specs)} declared actions"
            )
        await self._send(
            connection,
            {
                "type": FRAME_EVENT,
                "id": session.session_id,
                "event_type": AGENT_STATUS_EVENT,
                "payload": {"state": STATE_PAIRED},
            },
        )
        if connection.closed:
            return
        try:
            session.heartbeat_task = self._tasks.spawn(
                self._heartbeat(session), name=f"agent-link-heartbeat-{self._pairings}"
            )
        except Exception:
            # The registry is closed: shutdown is under way, the session ends
            # with the connection and needs no heartbeat.
            session.heartbeat_task = None
        await self._report_ready(session)

    def _on_error_frame(self, frame: Mapping[str, Any]) -> None:
        """An ``error`` from the brain: logged by code, never by payload."""

        self._errors_received += 1
        code = frame.get("code")
        self._diagnose(
            f"{MODULE_NAME} connection: the brain answered error "
            f"{code if _is_text(code) else 'unknown'!r}"
        )

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
        """Drop *session* from the agent's side: tear down, then close the socket."""

        await self._teardown_session(session, reason)
        await self._close_ws(session.connection, CLOSE_GOING_AWAY)

    async def _teardown_session(self, session: _Session, reason: str) -> None:
        """Resolve the drop exactly once: pending ack, heartbeat, health."""

        if session.closed:
            return
        session.closed = True
        session.connection.session = None
        if self._session is session:
            self._session = None

        task = session.heartbeat_task
        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel()

        pending = session.pending_ack
        session.pending_ack = None
        if pending is not None and not pending[1].done():
            pending[1].set_result(_ACK_DISCONNECTED)

        if not self._stopping:
            await self._report_degraded(f"{MODULE_NAME}: brain disconnected ({reason})")

    # -- calls (§5.4, §8, §9) ------------------------------------------------ #

    async def _on_call(self, session: _Session, frame: Mapping[str, Any]) -> None:
        call_id = frame.get("id")
        answer_id = call_id if isinstance(call_id, str) else None
        if not _is_text(call_id) or frame.get("call_id", call_id) != call_id:
            await self._error(
                session.connection, answer_id, PROXY_ERROR_INVALID_FRAME,
                "call requires a non-empty string id equal to its call_id",
            )
            return
        seq = frame.get("seq")
        if not _is_positive_int(seq):
            await self._error(
                session.connection, call_id, PROXY_ERROR_INVALID_FRAME,
                "call requires a positive integer seq",
            )
            return

        now = self._clock()
        self._expire(session, now)
        entry = session.table.get(call_id)
        if entry is not None:
            if entry.seq != seq:
                await self._error(
                    session.connection, call_id, PROXY_ERROR_INVALID_FRAME,
                    "call_id is retained under a different seq",
                )
                return
            # A repeat: the retained observation, once it exists; 0 executions.
            self._spawn_execution(self._resend(session, entry), name="agent-link-resend")
            return
        if seq <= session.last_seq:
            await self._error(
                session.connection, call_id, PROXY_ERROR_DUPLICATE_CALL_UNKNOWN,
                "call is not retained: evicted or never seen",
            )
            return

        call = self._action_call(frame, call_id, now)
        if call is None:
            await self._error(
                session.connection, call_id, PROXY_ERROR_INVALID_FRAME,
                "call does not carry a valid ActionCall",
            )
            return

        session.last_seq = seq
        entry = _CallEntry(
            call_id=call_id,
            seq=seq,
            accepted_at=now,
            future=asyncio.get_running_loop().create_future(),
        )
        session.table[call_id] = entry
        while len(session.table) > self._settings.call_table.max_entries:
            session.table.popitem(last=False)
        max_attachment = frame.get("max_attachment_bytes")
        if not _is_positive_int(max_attachment):
            max_attachment = session.limits.get("max_attachment_bytes")
        bound = int(max_attachment) if _is_positive_int(max_attachment) else None
        task = self._spawn_execution(
            self._execute(session, entry, call, bound), name=f"agent-link-call-{seq}"
        )
        if task is None:
            return
        session.running[call_id] = task

        def _forget(done: "asyncio.Task[None]", *, call_id: str = call_id) -> None:
            if session.running.get(call_id) is done:
                del session.running[call_id]

        task.add_done_callback(_forget)

    def _expire(self, session: _Session, now: float) -> None:
        ttl = self._settings.call_table.ttl_seconds
        while session.table:
            oldest = next(iter(session.table.values()))
            if now - oldest.accepted_at > ttl:
                session.table.popitem(last=False)
            else:
                break

    def _action_call(self, frame: Mapping[str, Any], call_id: str, now: float) -> ActionCall | None:
        """The local call, bounded by ``min(remaining_ms, action_seconds)`` (§9)."""

        remaining_ms = frame.get("remaining_ms")
        destination = frame.get("destination")
        arguments = frame.get("arguments")
        if (
            not _is_non_negative_int(remaining_ms)
            or not isinstance(destination, Mapping)
            or not isinstance(arguments, Mapping)
        ):
            return None
        budget = min(remaining_ms / 1000.0, self._settings.action_seconds)
        try:
            call = ActionCall(
                action_name=frame.get("action_name"),
                action_version=frame.get("action_version"),
                arguments=arguments,
                conversation_id=frame.get("conversation_id"),
                run_id=frame.get("run_id"),
                call_id=call_id,
                source_event_id=frame.get("source_event_id"),
                destination=Destination(
                    platform=destination.get("platform"),
                    channel_id=destination.get("channel_id"),
                    scope=destination.get("scope"),
                ),
                principal=frame.get("principal"),
                deadline=now + budget,
                message_id=frame.get("message_id"),
                **(
                    {"contract_version": frame["contract_version"]}
                    if "contract_version" in frame
                    else {}
                ),
            )
        except (ContractError, TypeError, ValueError):
            return None
        return call

    def _spawn_execution(self, coro: Awaitable[None], *, name: str) -> "asyncio.Task[None] | None":
        try:
            task = self._tasks.spawn(coro, name=name)
        except Exception:
            # The registry is closed: shutdown is under way and the brain
            # classifies the call on the drop that follows.
            self._diagnose(f"{MODULE_NAME} call: execution refused, the link is stopping")
            return None
        self._executions.add(task)
        task.add_done_callback(self._executions.discard)
        return task

    async def _execute(
        self, session: _Session, entry: _CallEntry, call: ActionCall, max_attachment_bytes: int | None
    ) -> None:
        self._execution_count += 1
        observation: ActionObservation | None = None
        try:
            try:
                observation = await self._executor.invoke(call)
            except Exception as exc:
                observation = ActionObservation(
                    status="error",
                    provenance={"provider": MODULE_NAME},
                    error={
                        "code": ERROR_PROVIDER_FAILED,
                        "message": f"the local executor failed: {type(exc).__name__}",
                        "retryable": False,
                    },
                )
            frame = await self._observation_with_transfers(
                session, call, observation, max_attachment_bytes
            )
            text = _encode(frame)
            self._retain(entry, text)
            await self._send_retained(session, text)
        except asyncio.CancelledError:
            # ``cancel`` from the brain, or the link closing — during the
            # provider call or while an image transfer awaits its ack: the
            # executor recorded its own classification and the brain no
            # longer waits; nothing is sent, nothing is retained to re-send,
            # and no image the local run leased survives.
            if observation is None:
                outcome = getattr(self._executor, "outcome", None)
                recorded = outcome(call.call_id) if callable(outcome) else None
                if isinstance(recorded, ActionObservation):
                    observation = recorded
            if observation is not None:
                self._discard_images(observation.parts)
            self._retain(entry, None)
            raise

    def _retain(self, entry: _CallEntry, text: str | None) -> None:
        if not entry.future.done():
            entry.future.set_result(text)

    async def _resend(self, session: _Session, entry: _CallEntry) -> None:
        await self._send_retained(session, await entry.future)

    async def _send_retained(self, session: _Session, text: str | None) -> None:
        """Send an observation frame unless the session is gone; a transport
        that closed meanwhile loses it — the brain classifies the drop."""

        if text is None or session.closed:
            return
        try:
            await self._send_text(session.connection, text)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass

    def _on_cancel(self, session: _Session, frame: Mapping[str, Any]) -> None:
        """Cancel the running execution of ``id``; unknown or finished: ignored."""

        call_id = frame.get("id")
        task = session.running.get(call_id) if isinstance(call_id, str) else None
        if task is None or task.done():
            return
        task.cancel()

    # -- observations and attachments (§5.5, §10) --------------------------- #

    async def _observation_with_transfers(
        self,
        session: _Session,
        call: ActionCall,
        observation: ActionObservation,
        max_attachment_bytes: int | None,
    ) -> dict[str, Any]:
        """Transfer every attachment the observation names, then shape the frame.

        ``image_ref`` and ``audio_ref`` parts are handled alike (phase 2 R5):
        a refused transfer drops the part — never a dangling reference — and
        the observation becomes ``error attachment_refused``.
        """

        parts: list[Any] = []
        refused: str | None = None
        for part in observation.parts:
            if not isinstance(part, Mapping) or part.get("type") not in _REFERENCE_PART_TYPES:
                parts.append(_jsonable(part))
                continue
            attachment_id = part.get("attachment_id")
            if refused is not None:
                self._discard(attachment_id)
                continue
            data = self._read_attachment(attachment_id)
            if data is None:
                refused = _ACK_UNAVAILABLE
                continue
            if max_attachment_bytes is not None and len(data) > max_attachment_bytes:
                refused = ATTACHMENT_ACK_TOO_LARGE
                self._discard(attachment_id)
                continue
            code = await self._transfer(session, call, str(attachment_id), str(part.get("content_type")), data)
            self._discard(attachment_id)
            if code is None:
                parts.append(_jsonable(part))
            else:
                refused = code
        if refused is None:
            return self._observation_frame(call, observation, parts)
        return {
            "type": FRAME_OBSERVATION,
            "id": call.call_id,
            "status": "error",
            "provenance": _jsonable(observation.provenance),
            "error": {
                "code": ERROR_ATTACHMENT_REFUSED,
                "message": f"the attachment of {call.action_name!r} was not transferred ({refused})",
                "retryable": False,
            },
            "parts": parts,
        }

    def _observation_frame(
        self, call: ActionCall, observation: ActionObservation, parts: list[Any]
    ) -> dict[str, Any]:
        frame: dict[str, Any] = {
            "type": FRAME_OBSERVATION,
            "id": call.call_id,
            "status": observation.status,
            "provenance": _jsonable(observation.provenance),
            "parts": [_jsonable(part) for part in parts],
        }
        if observation.result is not None:
            frame["result"] = _jsonable(observation.result)
        if observation.error is not None:
            frame["error"] = _jsonable(observation.error)
        return frame

    def _read_attachment(self, attachment_id: Any) -> bytes | None:
        store = self._attachments
        if store is None or not _is_text(attachment_id):
            return None
        try:
            ref = store.lookup(attachment_id)
            if ref is None:
                return None
            return bytes(store.get(ref))
        except Exception:
            return None

    def _discard(self, attachment_id: Any) -> None:
        """Drop the local bytes: transferred, refused or unreadable, none is kept."""

        store = self._attachments
        if store is None or not _is_text(attachment_id):
            return
        try:
            store.discard(attachment_id)
        except Exception:
            pass

    def _discard_images(self, parts: Sequence[Mapping[str, Any]]) -> None:
        for part in parts:
            if isinstance(part, Mapping) and part.get("type") in _REFERENCE_PART_TYPES:
                self._discard(part.get("attachment_id"))

    async def _transfer(
        self, session: _Session, call: ActionCall, attachment_id: str, content_type: str, data: bytes
    ) -> str | None:
        """Header + exactly one binary frame, then the ack: ``None`` when accepted."""

        connection = session.connection
        async with session.transfer_lock:
            if session.closed:
                return _ACK_DISCONNECTED
            future: "asyncio.Future[str | None]" = asyncio.get_running_loop().create_future()
            session.pending_ack = (attachment_id, future)
            header = {
                "type": FRAME_ATTACHMENT,
                "id": session.session_id,
                "attachment_id": attachment_id,
                "content_type": content_type,
                "size": len(data),
                "call_id": call.call_id,
            }
            try:
                # No other frame may come between the header and its payload.
                async with connection.send_lock:
                    await connection.ws.send_str(_encode(header))
                    await connection.ws.send_bytes(data)
            except asyncio.CancelledError:
                session.pending_ack = None
                raise
            except Exception:
                session.pending_ack = None
                return _ACK_DISCONNECTED
            try:
                return await future
            finally:
                if session.pending_ack is not None and session.pending_ack[1] is future:
                    session.pending_ack = None

    def _on_attachment_ack(self, session: _Session, frame: Mapping[str, Any]) -> None:
        pending = session.pending_ack
        if pending is None or pending[0] != frame.get("attachment_id") or pending[1].done():
            return
        if frame.get("accepted") is True:
            pending[1].set_result(None)
        else:
            code = frame.get("code")
            pending[1].set_result(code if _is_text(code) else "refused")

    # -- sending ------------------------------------------------------------- #

    async def _send(self, connection: _Connection, frame: Mapping[str, Any]) -> None:
        await self._send_text(connection, _encode(frame))

    async def _send_text(self, connection: _Connection, text: str) -> None:
        async with connection.send_lock:
            await connection.ws.send_str(text)

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
        close_code: int,
    ) -> None:
        await self._error(connection, answer_id, code, message)
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
            outcome = ready(capabilities=sorted(session.accepted), session_id=session.session_id)
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
# The default dial
# --------------------------------------------------------------------------- #


class _ClientConnection:
    """The dialed socket and the client session that owns it, closed together."""

    __slots__ = ("_session", "_ws")

    def __init__(self, session: Any, ws: Any) -> None:
        self._session = session
        self._ws = ws

    @property
    def closed(self) -> bool:
        return bool(self._ws.closed)

    @property
    def close_code(self) -> int | None:
        return self._ws.close_code

    async def receive(self) -> Any:
        return await self._ws.receive()

    async def send_str(self, data: str) -> None:
        await self._ws.send_str(data)

    async def send_bytes(self, data: bytes) -> None:
        await self._ws.send_bytes(data)

    async def close(self, code: int = 1000, message: bytes = b"") -> bool:
        try:
            return bool(await self._ws.close(code=code, message=message))
        finally:
            await self._session.close()


async def default_connector(url: str, *, max_frame_bytes: int) -> _ClientConnection:
    """Dial *url* with ``aiohttp``; text frames above *max_frame_bytes* are cut.

    Built on first use so importing this module never imports ``aiohttp``.
    A ``wss`` URL is verified against the system certificate store.
    """

    import aiohttp

    session = aiohttp.ClientSession()
    try:
        ws = await session.ws_connect(url, max_msg_size=max_frame_bytes, autoping=True)
    except BaseException:
        await session.close()
        raise
    return _ClientConnection(session, ws)


# --------------------------------------------------------------------------- #
# Activation (R7)
# --------------------------------------------------------------------------- #


async def activate(
    context: Any,
    settings: Mapping[str, Any],
    catalog: Mapping[str, Mapping[str, Any]],
) -> AgentLinkModule:
    """Build the handle from the scoped runtime context (R7).

    Settings are checked through the same hook the loader ran, so a handle
    built outside the loader is refused on the same terms. The seams
    ``_connector``, ``_sleeper`` and ``diagnostic_reporter`` are read from
    *settings* and default to the ``aiohttp`` dial, ``asyncio.sleep`` and
    stderr. Nothing here reaches the network: the dial starts in
    ``start_inputs``.
    """

    del catalog  # The served actions are resolved from the local registry.
    _require_runtime_surfaces(context)
    if not isinstance(settings, Mapping):
        raise AgentLinkError(f"{MODULE_NAME} configuration: settings must be a mapping")
    diagnostics = validate_settings(settings)
    if diagnostics:
        raise AgentLinkError(
            f"{MODULE_NAME} configuration: settings were refused "
            f"({len(diagnostics)} diagnostics)"
        )
    parsed = _Settings.from_mapping(settings)
    return AgentLinkModule(
        context,
        parsed,
        connector=settings.get(_SEAM_CONNECTOR, default_connector),
        sleeper=settings.get(_SEAM_SLEEPER, asyncio.sleep),
        reporter=settings.get(_SEAM_REPORTER, _default_reporter),
    )


def _require_runtime_surfaces(context: Any) -> None:
    """Refuse a context lacking the actions facade, an executor, tasks, a clock or a random source."""

    actions = getattr(context, "actions", None)
    executor = getattr(context, "executor", None)
    tasks = getattr(context, "tasks", None)
    clock = getattr(context, "clock", None)
    rng = getattr(context, "rng", None)
    if (
        actions is None
        or not callable(getattr(actions, "discovered", None))
        or executor is None
        or not callable(getattr(executor, "invoke", None))
        or tasks is None
        or not callable(getattr(tasks, "spawn", None))
        or not callable(clock)
        or rng is None
        or not callable(getattr(rng, "random", None))
    ):
        raise AgentLinkError(f"{MODULE_NAME} activation: runtime context is invalid")


__all__ = [
    "AGENT_STATUS_EVENT",
    "CLOSE_GOING_AWAY",
    "DEFAULT_ACTION_SECONDS",
    "DEFAULT_CALL_TABLE_MAX_ENTRIES",
    "DEFAULT_CALL_TABLE_TTL_SECONDS",
    "DEFAULT_HEARTBEAT_SECONDS",
    "DEFAULT_HEARTBEAT_TIMEOUT_SECONDS",
    "DEFAULT_RECONNECT_INITIAL_SECONDS",
    "DEFAULT_RECONNECT_MAX_SECONDS",
    "DEFAULT_RECONNECT_MULTIPLIER",
    "ERROR_ATTACHMENT_REFUSED",
    "MANIFEST_PATH",
    "MODULE_NAME",
    "STATE_PAIRED",
    "AgentLinkError",
    "AgentLinkModule",
    "activate",
    "default_connector",
    "spec_declaration",
    "validate_settings",
]
