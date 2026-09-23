"""``moderation.request`` — configurable moderation under strict rules (R5).

**The action.** ``moderation.request`` (version 1, write, permission
``moderation.request``, destinations ``*/*/moderation``, no delivery,
idempotency ``none``) is the one write a manifest flags ``model_proposable``:
the brain offers it to the model only when a configured rule grants it, and
the executor re-checks every call. Its arguments are ``operation``
(``delete_message`` or ``timeout``), ``message_id``, ``reason`` (at most
:data:`REASON_MAX_CHARS` characters) and, for ``timeout`` only and required
with it, ``duration_seconds``. What the schema subset cannot say is checked
here: a violation ends ``error invalid_arguments`` with 0 platform requests.
No operation removes a viewer permanently: the only operations are a message
deletion and a timeout whose duration is at least 1 s and at most
``max_timeout_seconds``.

**The mode** (``mode``, default ``alert``; ``channels.<platform>/<channel_id>
.mode`` overrides it for one channel, resolved per request):

- ``alert`` records the request — ``success`` with disposition ``alerted`` —
  and sends 0 platform requests;
- ``propose`` is declared with its ``propose.*`` settings; its proposal table
  and approval commands arrive with plan step P15, and until then a request
  in that mode is ``refused mode_unavailable`` with 0 platform requests;
- ``act`` applies the request at once through :meth:`ModerationModule.apply`.

**The application** — the one path every application takes (P15's approvals
and ``auto_apply`` call the same method). The strict rules are checked in the
specification's order, each refusing with its code and 0 platform requests:

1. ``operation_not_allowed`` — the operation is not in ``act.operations``
   (default ``[delete_message]``);
2. ``target_unknown`` — the message is not in the channel's retained chat
   context, **or** not in this module's message index (below);
3. ``target_protected`` — its indexed author holds a trusted broadcaster,
   moderator or VIP role, or is the companion (not configurable);
4. ``duration_out_of_range`` — a timeout whose duration, after the platform
   service's ``round_duration`` hook, is outside 1..``max_timeout_seconds``;
5. ``rate_limited`` — ``max_actions_per_window`` requests were already sent
   in the channel within ``window_seconds``;
6. ``target_cooldown`` — a request aimed at the same **author** (not the same
   message) of the channel was sent within ``per_target_cooldown_seconds``;
7. ``platform_unsupported`` — the platform published no ``moderation``
   service, or its service does not offer the operation.

**Serialization and reservation.** Several calls can apply concurrently and
the platform request is awaited. One :class:`asyncio.Lock` per ``(platform,
channel_id)`` covers the check-and-reserve section only: under it every rule
is evaluated against the current counters and, when allowed, the slot is
reserved — the window instant appended and the target author's cooldown
instant set. The lock is released, then the call is declared emitted and the
one request is sent. Nothing between the reservation and the send can
refuse, so the counters count every sent request, and a reservation is never
rolled back whatever the platform answers (a lost or refused request still
consumed its slot). A slow platform never holds the lock.

**The request** is sent once, never retried: ``ok`` → ``success`` disposition
``applied``; ``rejected`` → ``error platform_rejected``; ``rate`` → ``error
rate_limited_platform``; ``uncertain``, a raised or unreadable answer →
``external_unknown``. The call deadline is the executor's: a request still
pending when it passes ends ``external_unknown`` by the executor's emission
rule, since the call was declared emitted before the request left.

**The message index** (plan decision 7). The module consumes
``channel.chat.message`` and keeps, per channel, ``message_id → (author id,
trusted roles)`` for events of kind ``message`` only — a notice is never a
target. Roles are trusted only when the event carries ``author.roles`` with a
non-empty ``author.roles_provenance`` (the platform attested them). The index
is capped by the chat context's own bounds, read from the reserved
``limits.chat_context`` group (``max_messages`` per channel, ``max_channels``
channels), oldest first. A message older than the index but still in the chat
context is therefore ``target_unknown`` — the conservative choice.

**The companion identity.** A platform's ``moderation`` service may state the
companion's own author identity as ``companion_id`` (a string, or a
collection of strings), read once at ``prepare`` and compared
case-insensitively, as the trigger engine's anti-echo does. A platform that
drops the companion's own messages at ingestion (twitch does) never indexes
one, so such a message is ``target_unknown`` there — refused either way.

**Facts.** Every request that reaches this module — whatever its mode and
outcome — publishes exactly one ``moderation.decision`` fact through
supervision: ``mode``, ``status``, ``disposition`` (on success), ``code`` (on
failure), ``operation``, ``platform``, ``channel_id``, ``message_id``,
``target_author_id`` (when indexed), ``reason`` (at most 200 characters) and
the call's correlation. On the executor's path the fact is handed over once
the executor recorded the terminal observation, and carries *that*
observation's status and code, so the audit (whose ``**`` subscription
records every published type) reads the completion first and the fact after
it. A call the executor refuses before the provider runs (``not_authorized``,
``invalid_arguments`` against the schema, the brain's ``run_limit``) never
reaches this module and publishes none.

**Seams, for the tests.** ``_sleeper`` (the drain's wait, ``asyncio.sleep``
by default) is read from *settings* at ``activate`` and never from a
configuration file.
"""

from __future__ import annotations

import asyncio
import inspect
import time
from collections import OrderedDict, deque
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from core.actions import (
    ERROR_CANCELLED,
    ERROR_EXTERNAL_UNKNOWN,
    ERROR_INVALID_ARGUMENTS,
    PROVENANCE_TRACE_FIELDS,
)
from core.context import DEFAULT_MAX_CHANNELS
from core.contracts import EVENT_KIND_DEFAULT, ActionObservation, ActionSpec, Destination


MODULE_NAME = "moderation"
MANIFEST_PATH = Path(__file__).with_name("module.yaml")

MODERATION_ACTION = "moderation.request"
PROVIDER_NAME = "moderation"
MODERATION_SCOPE = "moderation"

#: The service kind a platform publishes its moderation service under (R5).
#: The name lives in this module and in the publishers only: the core defines none.
MODERATION_SERVICE_KIND = "moderation"

#: The fact every request, approval, rejection and expiry publishes.
FACT_MODERATION_DECISION = "moderation.decision"

MODE_ALERT = "alert"
MODE_PROPOSE = "propose"
MODE_ACT = "act"
MODES = (MODE_ALERT, MODE_PROPOSE, MODE_ACT)
DEFAULT_MODE = MODE_ALERT

OPERATION_DELETE_MESSAGE = "delete_message"
OPERATION_TIMEOUT = "timeout"
OPERATIONS = (OPERATION_DELETE_MESSAGE, OPERATION_TIMEOUT)

DISPOSITION_ALERTED = "alerted"
DISPOSITION_PROPOSED = "proposed"
DISPOSITION_APPLIED = "applied"

# The strict rules, in the order they are checked (R5).
REFUSAL_OPERATION_NOT_ALLOWED = "operation_not_allowed"
REFUSAL_TARGET_UNKNOWN = "target_unknown"
REFUSAL_TARGET_PROTECTED = "target_protected"
REFUSAL_DURATION_OUT_OF_RANGE = "duration_out_of_range"
REFUSAL_RATE_LIMITED = "rate_limited"
REFUSAL_TARGET_COOLDOWN = "target_cooldown"
REFUSAL_PLATFORM_UNSUPPORTED = "platform_unsupported"
STRICT_RULES = (
    REFUSAL_OPERATION_NOT_ALLOWED,
    REFUSAL_TARGET_UNKNOWN,
    REFUSAL_TARGET_PROTECTED,
    REFUSAL_DURATION_OUT_OF_RANGE,
    REFUSAL_RATE_LIMITED,
    REFUSAL_TARGET_COOLDOWN,
    REFUSAL_PLATFORM_UNSUPPORTED,
)

ERROR_PLATFORM_REJECTED = "platform_rejected"
ERROR_RATE_LIMITED_PLATFORM = "rate_limited_platform"
#: Mode ``propose`` before its proposal table exists (plan step P15).
ERROR_MODE_UNAVAILABLE = "mode_unavailable"
_ERROR_PROVIDER_CLOSED = "provider_closed"

# The classified answers a moderation service returns.
ANSWER_OK = "ok"
ANSWER_REJECTED = "rejected"
ANSWER_RATE = "rate"

#: The trusted roles that protect a message's author (not configurable).
PROTECTED_ROLES = frozenset({"broadcaster", "moderator", "vip"})

REASON_MAX_CHARS = 200

DEFAULT_ACT_OPERATIONS = (OPERATION_DELETE_MESSAGE,)
DEFAULT_MAX_TIMEOUT_SECONDS = 300
MAX_TIMEOUT_BOUNDS = (1, 3600)
DEFAULT_MAX_ACTIONS_PER_WINDOW = 3
MAX_ACTIONS_BOUNDS = (1, 20)
DEFAULT_WINDOW_SECONDS = 600
WINDOW_BOUNDS = (60, 86400)
DEFAULT_PER_TARGET_COOLDOWN_SECONDS = 600
COOLDOWN_BOUNDS = (0, 86400)
DEFAULT_MAX_PENDING = 32
MAX_PENDING_BOUNDS = (1, 256)
DEFAULT_PROPOSAL_TTL_SECONDS = 300
PROPOSAL_TTL_BOUNDS = (30, 3600)
DEFAULT_APPROVE_COMMAND = "!modok"
DEFAULT_REJECT_COMMAND = "!modno"
COMMAND_MAX_CHARS = 32

#: The index bound when the configuration carries no ``limits.chat_context``.
DEFAULT_INDEX_MAX_MESSAGES = 256

_CHAT_EVENT = "channel.chat.message"
#: Author identities in this namespace are reserved (R6); never a target.
_RESERVED_SEPARATOR = ":"

# Setting names — referenced by name, never by value, in diagnostics.
_ACCEPTED_LIMITS_SETTING = "limits"
_SETTING_MODE = "mode"
_SETTING_CHANNELS = "channels"
_SETTING_ACT = "act"
_SETTING_OPERATIONS = "operations"
_SETTING_MAX_TIMEOUT = "max_timeout_seconds"
_SETTING_MAX_ACTIONS = "max_actions_per_window"
_SETTING_WINDOW = "window_seconds"
_SETTING_COOLDOWN = "per_target_cooldown_seconds"
_SETTING_PROPOSE = "propose"
_PROPOSE_MAX_PENDING = "max_pending"
_PROPOSE_TTL = "proposal_ttl_seconds"
_PROPOSE_AUTO_APPLY = "auto_apply"
_PROPOSE_APPROVE = "approve_command"
_PROPOSE_REJECT = "reject_command"
_SETTINGS = frozenset(
    {
        _SETTING_MODE,
        _SETTING_CHANNELS,
        _SETTING_ACT,
        _SETTING_MAX_TIMEOUT,
        _SETTING_MAX_ACTIONS,
        _SETTING_WINDOW,
        _SETTING_COOLDOWN,
        _SETTING_PROPOSE,
    }
)
_PROPOSE_SETTINGS = frozenset(
    {_PROPOSE_MAX_PENDING, _PROPOSE_TTL, _PROPOSE_AUTO_APPLY, _PROPOSE_APPROVE, _PROPOSE_REJECT}
)

_SEAM_SLEEPER = "_sleeper"
_SEAMS = frozenset({_SEAM_SLEEPER})

Sleeper = Callable[[float], Awaitable[Any]]
ChannelKey = tuple[str, str]


class ModerationModuleError(RuntimeError):
    """A setup failure whose message contains no configured value."""


# --------------------------------------------------------------------------- #
# Settings validation hook
# --------------------------------------------------------------------------- #


def validate_settings(settings: Any) -> list[str]:
    """Check this module's settings; return one diagnostic per offending field.

    This is the hook ``settings_validator`` in the manifest names. Each
    diagnostic names the module and the field and nothing else; an empty list
    means the settings are accepted. The reserved ``limits`` block is accepted
    and read for its ``chat_context`` group only; any other unknown key is
    refused by name.
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
    if _SEAM_SLEEPER in settings and not callable(settings[_SEAM_SLEEPER]):
        diagnostics.append(_setting_diagnostic(_SEAM_SLEEPER, "must be callable"))
    if _SETTING_MODE in settings and settings[_SETTING_MODE] not in MODES:
        diagnostics.append(_setting_diagnostic(_SETTING_MODE, "must be one of alert, propose, act"))
    if _SETTING_CHANNELS in settings:
        diagnostics.extend(_validate_channels(settings[_SETTING_CHANNELS]))
    if _SETTING_ACT in settings:
        diagnostics.extend(_validate_act(settings[_SETTING_ACT]))
    for field_name, (low, high) in (
        (_SETTING_MAX_TIMEOUT, MAX_TIMEOUT_BOUNDS),
        (_SETTING_MAX_ACTIONS, MAX_ACTIONS_BOUNDS),
        (_SETTING_WINDOW, WINDOW_BOUNDS),
        (_SETTING_COOLDOWN, COOLDOWN_BOUNDS),
    ):
        if field_name in settings and not _is_int_within(settings[field_name], low, high):
            diagnostics.append(
                _setting_diagnostic(field_name, f"must be an integer from {low} to {high}")
            )
    if _SETTING_PROPOSE in settings:
        diagnostics.extend(_validate_propose(settings[_SETTING_PROPOSE]))
    return diagnostics


def _validate_channels(value: Any) -> list[str]:
    if not isinstance(value, Mapping):
        return [_setting_diagnostic(_SETTING_CHANNELS, "must be a mapping")]
    diagnostics: list[str] = []
    for key, override in value.items():
        if _channel_key(key) is None:
            diagnostics.append(
                _setting_diagnostic(_SETTING_CHANNELS, "keys must be <platform>/<channel_id>")
            )
            continue
        if (
            not isinstance(override, Mapping)
            or set(override) != {_SETTING_MODE}
            or override[_SETTING_MODE] not in MODES
        ):
            diagnostics.append(
                _setting_diagnostic(
                    f"{_SETTING_CHANNELS}.<channel>",
                    "must be a mapping whose only key is mode, one of alert, propose, act",
                )
            )
    return diagnostics


def _validate_act(value: Any) -> list[str]:
    if not isinstance(value, Mapping):
        return [_setting_diagnostic(_SETTING_ACT, "must be a mapping")]
    diagnostics = [
        _setting_diagnostic(f"{_SETTING_ACT}.{key}", "is not a setting this module declares")
        for key in value
        if key != _SETTING_OPERATIONS
    ]
    if _SETTING_OPERATIONS in value and not _is_operation_list(value[_SETTING_OPERATIONS]):
        diagnostics.append(
            _setting_diagnostic(
                f"{_SETTING_ACT}.{_SETTING_OPERATIONS}",
                "must be a list of distinct operations among delete_message, timeout",
            )
        )
    return diagnostics


def _validate_propose(value: Any) -> list[str]:
    if not isinstance(value, Mapping):
        return [_setting_diagnostic(_SETTING_PROPOSE, "must be a mapping")]
    diagnostics = [
        _setting_diagnostic(f"{_SETTING_PROPOSE}.{key}", "is not a setting this module declares")
        for key in value
        if key not in _PROPOSE_SETTINGS
    ]
    for field_name, (low, high) in (
        (_PROPOSE_MAX_PENDING, MAX_PENDING_BOUNDS),
        (_PROPOSE_TTL, PROPOSAL_TTL_BOUNDS),
    ):
        if field_name in value and not _is_int_within(value[field_name], low, high):
            diagnostics.append(
                _setting_diagnostic(
                    f"{_SETTING_PROPOSE}.{field_name}", f"must be an integer from {low} to {high}"
                )
            )
    if _PROPOSE_AUTO_APPLY in value and not _is_operation_list(value[_PROPOSE_AUTO_APPLY]):
        diagnostics.append(
            _setting_diagnostic(
                f"{_SETTING_PROPOSE}.{_PROPOSE_AUTO_APPLY}",
                "must be a list of distinct operations among delete_message, timeout",
            )
        )
    for field_name in (_PROPOSE_APPROVE, _PROPOSE_REJECT):
        if field_name in value and not _is_command(value[field_name]):
            diagnostics.append(
                _setting_diagnostic(
                    f"{_SETTING_PROPOSE}.{field_name}",
                    f"must be a non-empty word of at most {COMMAND_MAX_CHARS} characters",
                )
            )
    approve = value.get(_PROPOSE_APPROVE, DEFAULT_APPROVE_COMMAND)
    reject = value.get(_PROPOSE_REJECT, DEFAULT_REJECT_COMMAND)
    if _is_command(approve) and _is_command(reject) and approve == reject:
        diagnostics.append(
            _setting_diagnostic(
                f"{_SETTING_PROPOSE}.{_PROPOSE_REJECT}", "must differ from approve_command"
            )
        )
    return diagnostics


def _setting_diagnostic(field_name: str, reason: str) -> str:
    return f"module {MODULE_NAME!r}: field {field_name!r}: {reason}"


def _is_int_within(value: Any, low: int, high: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high


def _is_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_operation_list(value: Any) -> bool:
    return (
        isinstance(value, list)
        and all(isinstance(item, str) and item in OPERATIONS for item in value)
        and len(set(value)) == len(value)
    )


def _is_command(value: Any) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= COMMAND_MAX_CHARS
        and not any(character.isspace() for character in value)
    )


def _channel_key(value: Any) -> ChannelKey | None:
    """``<platform>/<channel_id>`` → ``(platform, channel_id)``, else ``None``."""

    if not isinstance(value, str):
        return None
    platform, separator, channel_id = value.partition("/")
    if not separator or not _is_text(platform) or not _is_text(channel_id):
        return None
    return platform, channel_id


def _positive_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


@dataclass(frozen=True, slots=True)
class ProposeSettings:
    """The ``propose.*`` settings; read by the proposal table (plan step P15)."""

    max_pending: int
    proposal_ttl_seconds: int
    auto_apply: frozenset[str]
    approve_command: str
    reject_command: str


@dataclass(frozen=True, slots=True)
class ModerationSettings:
    """The accepted settings, parsed once."""

    mode: str
    channel_modes: Mapping[ChannelKey, str]
    act_operations: frozenset[str]
    max_timeout_seconds: int
    max_actions_per_window: int
    window_seconds: int
    per_target_cooldown_seconds: int
    propose: ProposeSettings
    index_max_messages: int
    index_max_channels: int

    @classmethod
    def from_mapping(cls, settings: Mapping[str, Any]) -> "ModerationSettings":
        channels = settings.get(_SETTING_CHANNELS) or {}
        channel_modes: dict[ChannelKey, str] = {}
        for key, override in channels.items():
            parsed = _channel_key(key)
            if parsed is not None:
                channel_modes[parsed] = override[_SETTING_MODE]
        act = settings.get(_SETTING_ACT) or {}
        propose = settings.get(_SETTING_PROPOSE) or {}
        limits = settings.get(_ACCEPTED_LIMITS_SETTING)
        chat_limits = limits.get("chat_context") if isinstance(limits, Mapping) else None
        if not isinstance(chat_limits, Mapping):
            chat_limits = {}
        return cls(
            mode=settings.get(_SETTING_MODE, DEFAULT_MODE),
            channel_modes=channel_modes,
            act_operations=frozenset(act.get(_SETTING_OPERATIONS, DEFAULT_ACT_OPERATIONS)),
            max_timeout_seconds=int(settings.get(_SETTING_MAX_TIMEOUT, DEFAULT_MAX_TIMEOUT_SECONDS)),
            max_actions_per_window=int(
                settings.get(_SETTING_MAX_ACTIONS, DEFAULT_MAX_ACTIONS_PER_WINDOW)
            ),
            window_seconds=int(settings.get(_SETTING_WINDOW, DEFAULT_WINDOW_SECONDS)),
            per_target_cooldown_seconds=int(
                settings.get(_SETTING_COOLDOWN, DEFAULT_PER_TARGET_COOLDOWN_SECONDS)
            ),
            propose=ProposeSettings(
                max_pending=int(propose.get(_PROPOSE_MAX_PENDING, DEFAULT_MAX_PENDING)),
                proposal_ttl_seconds=int(propose.get(_PROPOSE_TTL, DEFAULT_PROPOSAL_TTL_SECONDS)),
                auto_apply=frozenset(propose.get(_PROPOSE_AUTO_APPLY, ())),
                approve_command=propose.get(_PROPOSE_APPROVE, DEFAULT_APPROVE_COMMAND),
                reject_command=propose.get(_PROPOSE_REJECT, DEFAULT_REJECT_COMMAND),
            ),
            index_max_messages=_positive_int(chat_limits.get("max_messages"))
            or DEFAULT_INDEX_MAX_MESSAGES,
            index_max_channels=_positive_int(chat_limits.get("max_channels"))
            or DEFAULT_MAX_CHANNELS,
        )

    def mode_for(self, platform: str, channel_id: str) -> str:
        """The channel's override, else the module's ``mode``."""

        return self.channel_modes.get((platform, channel_id), self.mode)


# --------------------------------------------------------------------------- #
# Requests, targets and outcomes
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class ModerationRequest:
    """One request's arguments, checked beyond the schema subset."""

    operation: str
    message_id: str
    reason: str
    duration_seconds: int | None = None

    @classmethod
    def parse(cls, arguments: Any) -> "ModerationRequest":
        """The request, or :class:`ValueError` naming what is wrong (no value)."""

        if not isinstance(arguments, Mapping):
            raise ValueError("arguments must be an object")
        operation = arguments.get("operation")
        if operation not in OPERATIONS:
            raise ValueError("operation must be delete_message or timeout")
        message_id = arguments.get("message_id")
        if not _is_text(message_id):
            raise ValueError("message_id must be a non-empty string")
        reason = arguments.get("reason")
        if not isinstance(reason, str) or len(reason) > REASON_MAX_CHARS:
            raise ValueError(f"reason must be a string of at most {REASON_MAX_CHARS} characters")
        duration = arguments.get("duration_seconds")
        if operation == OPERATION_TIMEOUT:
            if isinstance(duration, bool) or not isinstance(duration, int):
                raise ValueError("duration_seconds is required with timeout")
        elif duration is not None:
            raise ValueError("duration_seconds is only valid with timeout")
        return cls(operation, message_id, reason, duration)


@dataclass(frozen=True, slots=True)
class _Target:
    """One indexed message: its author and the trusted roles attested with it."""

    author_id: str
    roles: frozenset[str]


@dataclass(frozen=True, slots=True)
class ApplicationOutcome:
    """What one application ended with: ``status`` and ``disposition``/``code``."""

    status: str
    disposition: str | None = None
    code: str | None = None
    message: str = ""
    requests: int = 0


class _Uncertain:
    """A service request that raised: its answer is lost."""

    __slots__ = ()


_LOST = _Uncertain()


class _ChannelLock:
    """One channel's lock and how many applications hold or await it."""

    __slots__ = ("lock", "users")

    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.users = 0


class _ModerationActionProvider:
    """The provider bound to ``moderation.request``: hands each call to the module."""

    __slots__ = ("_module",)

    name = PROVIDER_NAME

    def __init__(self, module: "ModerationModule") -> None:
        self._module = module

    async def invoke(self, invocation: Any) -> ActionObservation:
        return await self._module._invoke(invocation)


# --------------------------------------------------------------------------- #
# The handle
# --------------------------------------------------------------------------- #


class ModerationModule:
    """The v2 handle: resolve and bind at ``prepare``, index the chat, moderate."""

    def __init__(self, context: Any, settings: ModerationSettings, *, sleeper: Sleeper) -> None:
        self._bus = context.bus
        self._actions = context.actions
        self._supervision = getattr(context, "supervision", None)
        self._services = getattr(context, "services", None)
        self._executor = getattr(context, "executor", None)
        self._chat: Any = None
        self._context = context
        self._clock: Callable[[], float] = getattr(context, "clock", None) or time.monotonic
        self._settings = settings
        self._sleeper = sleeper
        self._provider = _ModerationActionProvider(self)
        # ``platform → moderation service``, resolved once at ``prepare``.
        self._moderation_services: dict[str, Any] = {}
        # ``platform → casefolded companion identities`` its service states.
        self._companions: dict[str, frozenset[str]] = {}
        # ``(platform, channel_id) → message_id → target``, oldest first; both
        # levels bounded by the chat context's own bounds.
        self._index: OrderedDict[ChannelKey, OrderedDict[str, _Target]] = OrderedDict()
        # The instants of the requests each channel sent within its window.
        self._windows: dict[ChannelKey, deque[float]] = {}
        # ``(platform, channel_id, author_id) → instant`` of the last request
        # aimed at that author; entries past the cooldown are dropped.
        self._target_sent: dict[tuple[str, str, str], float] = {}
        self._locks: dict[ChannelKey, _ChannelLock] = {}
        # One future per running call, resolved when it returned.
        self._calls: set[asyncio.Future[None]] = set()
        # The fact publications handed to the loop and not yet finished.
        self._fact_tasks: set[asyncio.Future[None]] = set()
        self._diagnostics: list[str] = []
        self._prepared = False
        self._draining = False
        self._closed = False

    @property
    def settings(self) -> ModerationSettings:
        return self._settings

    @property
    def bound_platforms(self) -> tuple[str, ...]:
        """The platforms whose moderation service resolved at ``prepare``."""

        return tuple(sorted(self._moderation_services))

    @property
    def diagnostics(self) -> tuple[str, ...]:
        return tuple(self._diagnostics)

    def indexed(self, platform: str, channel_id: str) -> tuple[str, ...]:
        """The message ids indexed for one channel, oldest first."""

        return tuple(self._index.get((platform, channel_id), ()))

    def window(self, platform: str, channel_id: str) -> tuple[float, ...]:
        """The instants of the channel's requests still inside its window."""

        return tuple(self._prune_window((platform, channel_id), self._clock()))

    # -- lifecycle hooks ---------------------------------------------------- #

    async def prepare(self) -> None:
        """Resolve the moderation services, read the chat context, bind, subscribe."""

        if self._prepared or self._closed:
            return
        self._chat = getattr(self._context, "chat", None)
        self._moderation_services, self._companions = self._resolve_services()
        try:
            self._actions.register(_declared_spec(), self._provider, provider_name=PROVIDER_NAME)
        except ModerationModuleError:
            raise
        except Exception:
            raise ModerationModuleError(f"{MODULE_NAME} prepare: action binding failed") from None
        self._bus.subscribe(_CHAT_EVENT, self.handle_chat_message)
        self._prepared = True
        self._actions.mark_ready()

    def _resolve_services(self) -> tuple[dict[str, Any], dict[str, frozenset[str]]]:
        """``platform → service`` for every published moderation service, and
        ``platform → companion identities`` each one states.

        Reads ``entries()`` and ``resolve()`` of the service facade and nothing
        else; a service is kept when ``apply`` is callable. A context without a
        registry hands out an empty view.
        """

        facade = self._services
        entries = getattr(facade, "entries", None)
        resolve = getattr(facade, "resolve", None)
        if not callable(entries) or not callable(resolve):
            return {}, {}
        services: dict[str, Any] = {}
        companions: dict[str, frozenset[str]] = {}
        for key in list(entries()):
            if not isinstance(key, tuple) or len(key) != 2:
                continue
            kind, platform = key
            if kind != MODERATION_SERVICE_KIND or not _is_text(platform):
                continue
            service = resolve(kind, platform)
            if service is None or not callable(getattr(service, "apply", None)):
                continue
            services[platform] = service
            companions[platform] = _identities(getattr(service, "companion_id", None))
        return services, companions

    async def drain(self, deadline_seconds: float) -> None:
        """Refuse new requests; give the ones in flight the drain deadline."""

        self._draining = True
        pending = self._pending_calls()
        if not pending:
            return
        timer = asyncio.ensure_future(self._sleeper(max(float(deadline_seconds), 0.0)))
        try:
            while pending and not timer.done():
                await asyncio.wait(pending | {timer}, return_when=asyncio.FIRST_COMPLETED)
                pending = self._pending_calls()
        finally:
            await _settle(timer)

    async def close(self) -> None:
        """Withdraw readiness; wait for the facts already handed over."""

        if self._closed:
            return
        self._closed = True
        self._draining = True
        if self._prepared:
            self._actions.mark_not_ready()
        if self._fact_tasks:
            await asyncio.wait(set(self._fact_tasks))

    def _pending_calls(self) -> set[asyncio.Future[None]]:
        return {call for call in self._calls if not call.done()}

    # -- the message index (plan decision 7) --------------------------------- #

    async def handle_chat_message(self, event: Mapping[str, Any]) -> None:
        """Index one ordinary message's author and trusted roles.

        Never raises and never returns a replacement: this consumer sits in
        the publisher's chain and must not change or fail it.
        """

        if self._closed or not self._prepared:
            return
        try:
            self._index_event(event)
        except Exception:  # noqa: BLE001 - a consumer must not fail the publisher
            self._diagnose("index: failed")

    def _index_event(self, event: Any) -> None:
        payload = event.get("payload") if isinstance(event, Mapping) else None
        if not isinstance(payload, Mapping):
            return
        kind = payload.get("kind", EVENT_KIND_DEFAULT)
        if kind is not None and kind != EVENT_KIND_DEFAULT:
            # A notice is never a moderation target (plan decision 2).
            return
        platform = payload.get("platform")
        channel_id = payload.get("channel_id")
        message_id = payload.get("message_id")
        author = payload.get("author")
        author_id = author.get("id") if isinstance(author, Mapping) else None
        if not all(_is_text(part) for part in (platform, channel_id, message_id, author_id)):
            return
        if _RESERVED_SEPARATOR in author_id:
            return
        roles: frozenset[str] = frozenset()
        claimed = author.get("roles")
        if _is_text(author.get("roles_provenance")) and isinstance(claimed, (list, tuple)):
            roles = frozenset(role for role in claimed if isinstance(role, str))
        key = (platform, channel_id)
        messages = self._index.get(key)
        if messages is None:
            messages = self._index[key] = OrderedDict()
        else:
            self._index.move_to_end(key)
        messages[message_id] = _Target(author_id, roles)
        messages.move_to_end(message_id)
        while len(messages) > self._settings.index_max_messages:
            messages.popitem(last=False)
        while len(self._index) > self._settings.index_max_channels:
            self._index.popitem(last=False)

    def _target(self, key: ChannelKey, message_id: str) -> _Target | None:
        messages = self._index.get(key)
        return None if messages is None else messages.get(message_id)

    def _in_chat_context(self, key: ChannelKey, message_id: str) -> bool:
        chat = self._chat
        read = getattr(chat, "read", None)
        if not callable(read):
            return False
        try:
            records = read(key[0], key[1], limit=self._settings.index_max_messages)
        except Exception:  # noqa: BLE001 - an unreadable context knows no target
            self._diagnose("chat context: read failed")
            return False
        return any(getattr(record, "message_id", None) == message_id for record in records)

    # -- moderation.request --------------------------------------------------- #

    async def _invoke(self, invocation: Any) -> ActionObservation:
        """Serve one ``moderation.request`` call for the executor.

        Whatever ends the call — an outcome, the deadline, a cancellation —
        one ``moderation.decision`` fact is handed over once the executor has
        recorded the terminal observation, carrying that observation.
        """

        # Nothing has left before the platform request: an interruption until
        # then is a certain ``timeout``/``cancelled``.
        invocation.mark_not_emitted()
        call = invocation.call
        destination = call.destination
        key = (str(destination.platform), str(destination.channel_id))
        mode = self._settings.mode_for(*key)
        arguments = call.arguments if isinstance(call.arguments, Mapping) else {}
        fact = _fact_base(mode, key, arguments, call)
        own: list[ActionObservation] = []
        running: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._calls.add(running)
        try:
            observation = await self._serve(invocation, key, mode, arguments, fact)
            own.append(observation)
            return observation
        finally:
            self._calls.discard(running)
            _resolve(running)
            invocation.after_completion(
                lambda: self._hand_over_fact(fact, call.call_id, own, invocation.emitted)
            )

    async def _serve(
        self,
        invocation: Any,
        key: ChannelKey,
        mode: str,
        arguments: Mapping[str, Any],
        fact: dict[str, Any],
    ) -> ActionObservation:
        provenance = _provenance(key, mode)
        if self._closed:
            return _observation(
                provenance,
                ApplicationOutcome(
                    "error", code=_ERROR_PROVIDER_CLOSED, message=f"{MODULE_NAME}: closed"
                ),
            )
        if self._draining:
            return _observation(
                provenance,
                ApplicationOutcome(
                    "cancelled", code=ERROR_CANCELLED, message=f"{MODULE_NAME}: shutting down"
                ),
            )
        try:
            request = ModerationRequest.parse(arguments)
        except ValueError as error:
            return _observation(
                provenance,
                ApplicationOutcome("error", code=ERROR_INVALID_ARGUMENTS, message=str(error)),
            )
        target = self._target(key, request.message_id)
        if target is not None:
            fact["target_author_id"] = target.author_id
        if mode == MODE_ALERT:
            outcome = ApplicationOutcome("success", disposition=DISPOSITION_ALERTED)
        elif mode == MODE_PROPOSE:
            outcome = ApplicationOutcome(
                "refused",
                code=ERROR_MODE_UNAVAILABLE,
                message="mode propose is not available yet: nothing was sent",
            )
        else:
            outcome = await self.apply(key, request, mark_emitted=invocation.mark_emitted)
        return _observation(provenance, outcome, request)

    async def apply(
        self,
        key: ChannelKey,
        request: ModerationRequest,
        *,
        mark_emitted: Callable[[], Any] | None = None,
    ) -> ApplicationOutcome:
        """Apply *request* on channel *key* under the strict rules.

        The one application path. Under the channel's lock, every rule is
        checked against the current counters and, when allowed, the slot is
        reserved; outside it, *mark_emitted* is called and the one request is
        sent. A refusal sends nothing; a reservation is never rolled back.
        """

        channel_lock = self._locks.get(key)
        if channel_lock is None:
            channel_lock = self._locks[key] = _ChannelLock()
        channel_lock.users += 1
        try:
            async with channel_lock.lock:
                refusal, prepared = self._check_and_reserve(key, request)
        finally:
            channel_lock.users -= 1
            if channel_lock.users == 0 and self._locks.get(key) is channel_lock:
                del self._locks[key]
        if refusal is not None:
            return ApplicationOutcome("refused", code=refusal, message=_REFUSAL_MESSAGES[refusal])
        service, target, duration = prepared
        # From here the effect may happen: a lost answer is uncertain.
        if mark_emitted is not None:
            mark_emitted()
        answer = await self._send(service, key, request, target, duration)
        return _classify(answer)

    def _check_and_reserve(
        self, key: ChannelKey, request: ModerationRequest
    ) -> tuple[str | None, Any]:
        """Evaluate the strict rules in order; reserve the slot when allowed.

        Synchronous: called under the channel's lock, it never awaits, so the
        reservation is made on the very counters the rules were checked on.
        """

        settings = self._settings
        if request.operation not in settings.act_operations:
            return REFUSAL_OPERATION_NOT_ALLOWED, None
        target = self._target(key, request.message_id)
        if target is None or not self._in_chat_context(key, request.message_id):
            return REFUSAL_TARGET_UNKNOWN, None
        if target.roles & PROTECTED_ROLES or self._is_companion(key[0], target.author_id):
            return REFUSAL_TARGET_PROTECTED, None
        service = self._moderation_services.get(key[0])
        duration: int | None = None
        if request.operation == OPERATION_TIMEOUT:
            duration = _rounded(service, request.duration_seconds)
            if duration is None or not 1 <= duration <= settings.max_timeout_seconds:
                return REFUSAL_DURATION_OUT_OF_RANGE, None
        now = self._clock()
        window = self._prune_window(key, now)
        if len(window) >= settings.max_actions_per_window:
            return REFUSAL_RATE_LIMITED, None
        target_key = (key[0], key[1], target.author_id)
        last = self._target_sent.get(target_key)
        if last is not None and now - last < settings.per_target_cooldown_seconds:
            return REFUSAL_TARGET_COOLDOWN, None
        if service is None or request.operation not in _offered(service):
            return REFUSAL_PLATFORM_UNSUPPORTED, None
        # Allowed: reserve the slot before anything can await. Only here is
        # the window stored, so a refusal never leaves an entry behind.
        self._windows[key] = window
        window.append(now)
        self._stamp_target(target_key, now)
        return None, (service, target, duration)

    def _prune_window(self, key: ChannelKey, now: float) -> deque[float]:
        # An absent or emptied window is not kept: the table holds only
        # channels with a request still counting.
        window = self._windows.get(key)
        if window is None:
            return deque()
        horizon = now - self._settings.window_seconds
        while window and window[0] <= horizon:
            window.popleft()
        if not window:
            del self._windows[key]
        return window

    def _stamp_target(self, target_key: tuple[str, str, str], now: float) -> None:
        cooldown = self._settings.per_target_cooldown_seconds
        # Entries past their cooldown no longer refuse anything: dropped, so
        # the table holds only authors still cooling down.
        for other in [k for k, at in self._target_sent.items() if now - at >= cooldown]:
            del self._target_sent[other]
        # A channel whose last request left its window counts nothing.
        horizon = now - self._settings.window_seconds
        for other in [
            k for k, window in self._windows.items() if not window or window[-1] <= horizon
        ]:
            del self._windows[other]
        if cooldown > 0:
            self._target_sent[target_key] = now

    def _is_companion(self, platform: str, author_id: str) -> bool:
        return author_id.casefold() in self._companions.get(platform, frozenset())

    async def _send(
        self,
        service: Any,
        key: ChannelKey,
        request: ModerationRequest,
        target: _Target,
        duration: int | None,
    ) -> Any:
        """The one platform request, never retried; :data:`_LOST` when it raised."""

        try:
            return await service.apply(
                request.operation,
                channel_id=key[1],
                message_id=request.message_id,
                target_author_id=target.author_id,
                duration_seconds=duration,
                reason=request.reason,
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a lost answer is an uncertain outcome
            self._diagnose("request: answer lost")
            return _LOST

    # -- facts ---------------------------------------------------------------- #

    def _hand_over_fact(
        self,
        fact: dict[str, Any],
        call_id: str,
        own: list[ActionObservation],
        emitted: bool,
    ) -> None:
        """Complete *fact* with the call's terminal observation; publish it.

        Runs once the executor recorded the call. Its record is the one the
        fact states; without an executor record, the module's own observation,
        else the interruption the emission signal implies.
        """

        observation = None
        outcome = getattr(self._executor, "outcome", None)
        if callable(outcome):
            try:
                observation = outcome(call_id)
            except Exception:  # noqa: BLE001 - fall back on the module's record
                observation = None
        if observation is None and own:
            observation = own[0]
        if observation is None:
            status = "external_unknown" if emitted else "cancelled"
            fact.update(status=status, code=ERROR_EXTERNAL_UNKNOWN if emitted else ERROR_CANCELLED)
        else:
            fact.update(_observed(observation))
        task = asyncio.ensure_future(self._emit_fact(fact))
        self._fact_tasks.add(task)
        task.add_done_callback(self._fact_tasks.discard)

    async def _emit_fact(self, fact: Mapping[str, Any]) -> None:
        emit = getattr(self._supervision, "emit", None)
        if not callable(emit):
            return
        try:
            outcome = emit(FACT_MODERATION_DECISION, dict(fact))
            if inspect.isawaitable(outcome):
                await outcome
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a lost fact must not undo an outcome
            self._diagnose("fact: not published")

    def _diagnose(self, text: str) -> None:
        self._diagnostics.append(f"{MODULE_NAME} {text}")
        del self._diagnostics[:-16]


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #


_REFUSAL_MESSAGES = {
    REFUSAL_OPERATION_NOT_ALLOWED: "the operation is not allowed by this channel's settings",
    REFUSAL_TARGET_UNKNOWN: "the message is not in this channel's retained chat",
    REFUSAL_TARGET_PROTECTED: "the message's author is protected",
    REFUSAL_DURATION_OUT_OF_RANGE: "the timeout duration is out of the allowed range",
    REFUSAL_RATE_LIMITED: "this channel reached its moderation rate",
    REFUSAL_TARGET_COOLDOWN: "this author was moderated too recently",
    REFUSAL_PLATFORM_UNSUPPORTED: "the platform does not offer this operation",
}


def _identities(value: Any) -> frozenset[str]:
    if isinstance(value, str):
        return frozenset({value.casefold()}) if _is_text(value) else frozenset()
    if isinstance(value, Iterable):
        return frozenset(item.casefold() for item in value if _is_text(item))
    return frozenset()


def _offered(service: Any) -> frozenset[str]:
    operations = getattr(service, "operations", None)
    if isinstance(operations, (set, frozenset, list, tuple)):
        return frozenset(item for item in operations if isinstance(item, str))
    return frozenset()


def _rounded(service: Any, seconds: int | None) -> int | None:
    """*seconds* after the platform's unit rounding; ``None`` when unusable."""

    if seconds is None:
        return None
    hook = getattr(service, "round_duration", None)
    if not callable(hook):
        return seconds
    try:
        rounded = hook(seconds)
    except Exception:  # noqa: BLE001 - an unusable rounding is out of range
        return None
    if isinstance(rounded, bool) or not isinstance(rounded, int):
        return None
    return rounded


def _classify(answer: Any) -> ApplicationOutcome:
    outcome = getattr(answer, "outcome", None)
    if outcome == ANSWER_OK:
        return ApplicationOutcome("success", disposition=DISPOSITION_APPLIED, requests=1)
    if outcome == ANSWER_REJECTED:
        return ApplicationOutcome(
            "error",
            code=ERROR_PLATFORM_REJECTED,
            message="the platform refused the request",
            requests=1,
        )
    if outcome == ANSWER_RATE:
        return ApplicationOutcome(
            "error",
            code=ERROR_RATE_LIMITED_PLATFORM,
            message="the platform refused the request for its rate",
            requests=1,
        )
    return ApplicationOutcome(
        "external_unknown",
        code=ERROR_EXTERNAL_UNKNOWN,
        message="the request left and its answer is lost",
        requests=1,
    )


def _provenance(key: ChannelKey, mode: str) -> dict[str, Any]:
    return {
        "provider": PROVIDER_NAME,
        "platform": key[0],
        "channel_id": key[1],
        "route": MODERATION_ACTION,
        "mode": mode,
    }


def _observation(
    provenance: Mapping[str, Any],
    outcome: ApplicationOutcome,
    request: ModerationRequest | None = None,
) -> ActionObservation:
    if outcome.status == "success" and request is not None:
        return ActionObservation(
            status="success",
            provenance={
                **provenance,
                PROVENANCE_TRACE_FIELDS: {"disposition": outcome.disposition},
            },
            result={
                "disposition": outcome.disposition,
                "operation": request.operation,
                "message_id": request.message_id,
            },
        )
    return ActionObservation(
        status=outcome.status,
        provenance=provenance,
        error={"code": outcome.code, "message": outcome.message, "retryable": False},
    )


def _fact_base(
    mode: str, key: ChannelKey, arguments: Mapping[str, Any], call: Any
) -> dict[str, Any]:
    """What a decision fact states before its outcome is known."""

    reason = arguments.get("reason")
    duration = arguments.get("duration_seconds")
    fact: dict[str, Any] = {
        "mode": mode,
        "status": None,
        "disposition": None,
        "code": None,
        "operation": _text_or_none(arguments.get("operation")),
        "platform": key[0],
        "channel_id": key[1],
        "message_id": _text_or_none(arguments.get("message_id")),
        "target_author_id": None,
        "reason": reason[:REASON_MAX_CHARS] if isinstance(reason, str) else None,
        "run_id": getattr(call, "run_id", None),
        "call_id": getattr(call, "call_id", None),
    }
    if isinstance(duration, int) and not isinstance(duration, bool):
        fact["duration_seconds"] = duration
    return fact


def _text_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _observed(observation: Any) -> dict[str, Any]:
    status = getattr(observation, "status", None)
    result = getattr(observation, "result", None)
    error = getattr(observation, "error", None)
    return {
        "status": status,
        "disposition": result.get("disposition") if isinstance(result, Mapping) else None,
        "code": error.get("code") if isinstance(error, Mapping) else None,
    }


def _resolve(future: "asyncio.Future[None]") -> None:
    if not future.done():
        future.set_result(None)


async def _settle(task: "asyncio.Future[Any]") -> None:
    """Cancel *task* if still running and wait for it, consuming its outcome."""

    if not task.done():
        task.cancel()
    await asyncio.wait({task})
    if not task.cancelled():
        task.exception()


# --------------------------------------------------------------------------- #
# Activation
# --------------------------------------------------------------------------- #


async def activate(
    context: Any,
    settings: Mapping[str, Any],
    catalog: Mapping[str, Mapping[str, Any]],
) -> ModerationModule:
    """Build the handle from the scoped runtime context.

    Settings are checked through the same hook the loader ran, so a handle
    built outside the loader is refused on the same terms. Nothing is resolved
    or bound here: the platforms publish their services at their own
    activation, which ``prepare`` follows.
    """

    del catalog  # Capabilities are declared by the colocated manifest.
    bus = getattr(context, "bus", None)
    actions = getattr(context, "actions", None)
    if not callable(getattr(bus, "subscribe", None)) or not all(
        callable(getattr(actions, method, None))
        for method in ("register", "mark_ready", "mark_not_ready")
    ):
        raise ModerationModuleError(f"{MODULE_NAME} activation: runtime context is invalid")
    if not isinstance(settings, Mapping):
        raise ModerationModuleError(f"{MODULE_NAME} configuration: settings must be a mapping")
    diagnostics = validate_settings(settings)
    if diagnostics:
        raise ModerationModuleError(
            f"{MODULE_NAME} configuration: settings were refused "
            f"({len(diagnostics)} diagnostics)"
        )
    return ModerationModule(
        context,
        ModerationSettings.from_mapping(settings),
        sleeper=settings.get(_SEAM_SLEEPER, asyncio.sleep),
    )


def _declared_spec() -> ActionSpec:
    """Build ``moderation.request``'s contract from the colocated manifest.

    The spec registered at ``prepare`` must equal the one the loader declared
    at discovery, field for field: reading the same file keeps the two from
    drifting, and the registry refuses a redeclaration that differs.
    """

    try:
        manifest = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))
        entry = next(
            item
            for item in manifest["actions"]
            if isinstance(item, Mapping) and item.get("name") == MODERATION_ACTION
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
        raise ModerationModuleError(
            f"{MODULE_NAME} prepare: manifest declaration of {MODERATION_ACTION!r} is invalid"
        ) from None


__all__ = [
    "ANSWER_OK",
    "ANSWER_RATE",
    "ANSWER_REJECTED",
    "DEFAULT_ACT_OPERATIONS",
    "DEFAULT_APPROVE_COMMAND",
    "DEFAULT_INDEX_MAX_MESSAGES",
    "DEFAULT_MAX_ACTIONS_PER_WINDOW",
    "DEFAULT_MAX_PENDING",
    "DEFAULT_MAX_TIMEOUT_SECONDS",
    "DEFAULT_MODE",
    "DEFAULT_PER_TARGET_COOLDOWN_SECONDS",
    "DEFAULT_PROPOSAL_TTL_SECONDS",
    "DEFAULT_REJECT_COMMAND",
    "DEFAULT_WINDOW_SECONDS",
    "DISPOSITION_ALERTED",
    "DISPOSITION_APPLIED",
    "DISPOSITION_PROPOSED",
    "ERROR_MODE_UNAVAILABLE",
    "ERROR_PLATFORM_REJECTED",
    "ERROR_RATE_LIMITED_PLATFORM",
    "FACT_MODERATION_DECISION",
    "MANIFEST_PATH",
    "MODERATION_ACTION",
    "MODERATION_SCOPE",
    "MODERATION_SERVICE_KIND",
    "MODES",
    "MODE_ACT",
    "MODE_ALERT",
    "MODE_PROPOSE",
    "MODULE_NAME",
    "OPERATIONS",
    "OPERATION_DELETE_MESSAGE",
    "OPERATION_TIMEOUT",
    "PROTECTED_ROLES",
    "PROVIDER_NAME",
    "REASON_MAX_CHARS",
    "REFUSAL_DURATION_OUT_OF_RANGE",
    "REFUSAL_OPERATION_NOT_ALLOWED",
    "REFUSAL_PLATFORM_UNSUPPORTED",
    "REFUSAL_RATE_LIMITED",
    "REFUSAL_TARGET_COOLDOWN",
    "REFUSAL_TARGET_PROTECTED",
    "REFUSAL_TARGET_UNKNOWN",
    "STRICT_RULES",
    "ApplicationOutcome",
    "ModerationModule",
    "ModerationModuleError",
    "ModerationRequest",
    "ModerationSettings",
    "ProposeSettings",
    "activate",
    "validate_settings",
]
