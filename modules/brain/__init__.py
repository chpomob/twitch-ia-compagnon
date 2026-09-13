"""Language-model run engine on the versioned runtime (R2, R3, R5, R6, R7).

**Settings** (R7). The manifest beside this package declares
``manifest_version: 2`` and names :func:`validate_settings` as its
``settings_validator``. The loader runs that hook for every enabled module
before any of them is activated, so a misconfigured engine is reported with 0
transports opened (AC24). The hook is a standalone function that reads nothing
from the engine below; the engine parses its accepted settings through the
same hook, so a handle built outside the loader is refused on the same terms.

**Owned limits** (R6). The ``admission`` and ``conversation_memory`` groups
this module runs on are its own settings, and the entry point validates the
same groups under its top-level ``limits`` block, which it hands to every
enabled module as the reserved ``limits`` setting. The hook holds the two to
one value: when the accepted block is present, every owned limit must equal
the accepted one, field by field, and a differing copy is refused by name
before any module is activated — so the value the scheduler and the memory
are built from is always the value the entry point accepted, never a stale
mirror of it. A handle built with no accepted block (a harness, the
compatibility runtime) runs on its own settings, which are then the only
copy.

**Ingestion** (R2). :meth:`BrainModule.handle_chat_message` is the bus consumer
of ``channel.chat.message``. It validates the normalised event — platform,
channel, trusted ``author.id``, message identifier and text — copies it into a
:class:`~core.admission.Work`, admits it and returns. It never awaits the model:
the run executes on a worker of the admission scheduler, so a second message
and a keepalive are ingested while a first run is still held in a pending model
call (AC6). An event carrying no trusted viewer identity is refused (R3), and
when a trigger engine is present only an event whose recorded trigger decision
is *accepted* is admitted — a rejected trigger stays a traced decision that
calls no model (R1, AC4).

**Scheduler ownership.** The scheduler is taken from the runtime context when
the context carries one; otherwise the engine builds its own from the
``admission`` limits it owns, on the context's clock and supervision, with
:meth:`BrainModule.run` as its run body, starts it at ``prepare`` and closes it
at ``close``. The two cases differ in who admits: a scheduler shared through
the context is the producers' admission target — the input that decided the
trigger admits the normalised event itself (the twitch module does) — so the
bus copy is a fact for other consumers and this handler admits nothing into it,
which is what keeps every accepted message at exactly 1 admission (AC27). A
scheduler this module owns is reached through this handler alone. Whoever
builds a shared scheduler binds its run body to this module's :meth:`run`
(through a forwarder resolved after activation, since the scheduler exists
first) and wires its ``run_cleanup`` the same way this module does for the
scheduler it owns: to the context's attachment store, so the leases a
provider allocated under a run identity are released when the scheduler
writes that run's terminal record — success, error, expiry or cancellation —
rather than surviving until a time-to-live (R6, AC23, AC31).

**The run** (R5, R8). One admitted work is exactly one model call and exactly
one delivery through the :class:`~core.actions.ActionExecutor`: the model's
plain-text reply becomes a single ``chat.write`` call whose explicit
:class:`~core.contracts.ActionObservation` is the delivery outcome (AC19). The
model is offered only the registry's *authorized* action view for the reply's
destination, read afresh for every run. The ``budget.max_tokens`` limit is the
run's cumulative input + output bound (design §3.4): the prompt is composed
inside it — history is dropped oldest-first to fit, and a prompt that leaves no
room for a reply ends the run before the model is reached — the remaining room
is sent as the request's output cap, and a reply the backend's usage (or, when
it reports none, a conservative estimate flagged as such) puts over the bound
is discarded rather than delivered. There is no tagged output, no bus
republication and no second path: the compatibility ``channel.chat.send`` route
stays available to other producers on the same send service, and this module
never uses it. The run body returns a :class:`~core.admission.RunOutcome` —
terminal state, delivery outcome reported separately, model-call count and
correlation — and publishes **neither** ``brain.run.started`` **nor**
``brain.run.completed``: the scheduler is the single emission owner of both
and merges the outcome into its traced payload. The delivery's terminal trace
(``action.completed``) is the executor's.

**Conversation memory** (R3, R6). Keyed by
:class:`~core.contracts.SessionKey` ``(platform, channel_id, viewer_id)``, so
one viewer on two platforms or in two channels holds two memories and two
viewers in one channel never share one (AC10). It is bounded in sessions,
exchanges per session, bytes per session and age against the injected clock,
evicting oldest first on every axis (AC30). Only a ``success`` observation
writes an exchange back: a refused, failed, timed-out or ``external_unknown``
delivery leaves the memory untouched, so the model is never told it said
something it did not (R5).
"""

from __future__ import annotations

import asyncio
import inspect
import math
import re
import sys
import time
from collections import OrderedDict, deque
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, fields
from typing import Any
from urllib.parse import urlsplit

from core.admission import AdmissionScheduler, RunOutcome, Work
from core.contracts import (
    TERMINAL_STATUSES,
    ActionCall,
    ActionObservation,
    Destination,
    SessionKey,
)

try:  # Keep the module importable for transport-injected contract tests.
    import aiohttp
except ModuleNotFoundError:  # pragma: no cover - production installs dependencies
    aiohttp = None  # type: ignore[assignment]


MODULE_NAME = "brain"

PRINCIPAL = MODULE_NAME
"""The trusted principal every delivery call carries (R5).

The reply is the companion's act, so the rule that authorizes it names this
module — never the viewer, whose identity keys the session and grants nothing.
"""

DELIVERY_ACTION = "chat.write"
"""The action a reply is delivered through, at scope :data:`CHAT_SCOPE`."""

CHAT_SCOPE = "chat"

DELIVERY_NOT_ATTEMPTED = "not_attempted"
"""The delivery outcome of a run that ended before any executor call."""

WORK_KIND = "chat.message"

_INPUT_EVENT = "channel.chat.message"
_STATUS_SUCCESS = "success"
_STATUS_ERROR = "error"
_STATUS_TIMEOUT = "timeout"

# How often ``drain`` looks at the scheduler it owns while runs are still in
# flight, and the most looks it takes: bounded on the clock *and* in count, so
# a clock that never advances cannot turn the wait into a spin.
_DRAIN_POLL_SECONDS = 0.05
_DRAIN_MAX_POLLS = 10_000

# The token estimate ``budget.max_tokens`` is enforced with when the backend
# reports no usage (design §3.4): one token per this many characters plus a
# per-message overhead, which over-counts prose by a wide margin, so a prompt
# whose estimate fits the bound fits it.
_TOKEN_CHARS = 3
_TOKEN_MESSAGE_OVERHEAD = 4

# The share of ``budget.max_tokens`` retained history may fill: whatever the
# input leaves is the reply's room, so at least this much is reserved for the
# output whenever the mandatory messages alone fit inside it.
_HISTORY_TOKEN_SHARE = 0.5

# --------------------------------------------------------------------------- #
# Module-owned settings validation (R7)
# --------------------------------------------------------------------------- #

_COUNT = "count"
"""A positive integer: works, sessions, workers, turns, tokens, bytes."""

_SECONDS = "seconds"
"""A finite, strictly positive number of seconds."""

_ENDPOINT_SCHEMES = frozenset({"http", "https"})
_ENV_REFERENCE = re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*\}\Z")

#: The reserved setting the entry point hands its accepted ``limits`` block
#: over in (``core.main.LIMITS_KEY``): the groups it carries are the values
#: the configuration was accepted with, and the owned copies must equal them.
_ACCEPTED_LIMITS_SETTING = "limits"

#: Every limit this module owns, by group, with its kind. The vocabulary is
#: the one the manifest's ``settings_schema`` declares; the hook checks what
#: that schema cannot — finiteness and strict positivity — and, called on its
#: own, also presence and type, so a handle built outside the loader is
#: refused on the same terms.
_OWNED_LIMITS: Mapping[str, Mapping[str, str]] = {
    "admission": {
        "session_queue_capacity": _COUNT,
        "global_pending_capacity": _COUNT,
        "max_sessions": _COUNT,
        "workers": _COUNT,
        "wait_seconds": _SECONDS,
        "total_run_seconds": _SECONDS,
    },
    "budget": {
        "model_turns": _COUNT,
        "model_call_seconds": _SECONDS,
        "action_seconds": _SECONDS,
        "max_tokens": _COUNT,
        "max_observation_bytes": _COUNT,
    },
    "conversation_memory": {
        "max_sessions": _COUNT,
        "max_exchanges": _COUNT,
        "max_bytes": _COUNT,
        "max_age_seconds": _SECONDS,
    },
}


def validate_settings(settings: Any) -> list[str]:
    """Check this module's settings; return one diagnostic per offending field.

    This is the hook ``settings_validator`` in the manifest names. The loader
    runs it for every enabled module before any of them is activated (R7).
    Each diagnostic names the module and the field and nothing else: the
    endpoint and the key are configured values, so no value is ever echoed
    (AC24). An empty list means the settings are accepted.

    Beyond the shape the schema states, the hook checks that the endpoint is
    a well-formed ``http(s)`` URL, that the model name is non-empty, that the
    key resolved to a non-empty string rather than a still-unresolved
    ``${NAME}`` reference, and that every owned limit is present, numeric,
    finite and positive. When the entry point handed the accepted ``limits``
    block over, every owned limit must also equal the accepted one (R6): a
    copy that differs is refused by field, so no bound this module builds
    can disagree with the configuration that was accepted.
    """

    if not isinstance(settings, Mapping):
        return [_setting_diagnostic("settings", "must be a mapping")]
    diagnostics: list[str] = []

    endpoint = settings.get("endpoint")
    if endpoint is None:
        diagnostics.append(_setting_diagnostic("endpoint", "is required"))
    elif not _is_well_formed_url(endpoint):
        diagnostics.append(
            _setting_diagnostic("endpoint", "must be a well-formed http(s) URL")
        )

    model = settings.get("model")
    if model is None:
        diagnostics.append(_setting_diagnostic("model", "is required"))
    elif not isinstance(model, str) or not model.strip():
        diagnostics.append(_setting_diagnostic("model", "must be a non-empty string"))

    api_key = settings.get("api_key")
    if api_key is None:
        diagnostics.append(_setting_diagnostic("api_key", "is required"))
    elif not isinstance(api_key, str) or not api_key.strip():
        diagnostics.append(
            _setting_diagnostic("api_key", "must be a non-empty string")
        )
    elif _ENV_REFERENCE.match(api_key.strip()):
        diagnostics.append(
            _setting_diagnostic("api_key", "is an unresolved environment reference")
        )

    accepted = _accepted_limits(settings, diagnostics)
    for group, limits in _OWNED_LIMITS.items():
        section = settings.get(group)
        if section is None:
            diagnostics.append(_setting_diagnostic(group, "is required"))
            continue
        if not isinstance(section, Mapping):
            diagnostics.append(_setting_diagnostic(group, "must be a mapping of limits"))
            continue
        for field_name in section:
            if field_name not in limits:
                diagnostics.append(
                    _setting_diagnostic(
                        f"{group}.{field_name}", "is not a limit this module owns"
                    )
                )
        accepted_group = _accepted_group(accepted, group, diagnostics)
        for field_name, kind in limits.items():
            reason = _limit_reason(section.get(field_name), kind)
            if reason is not None:
                diagnostics.append(_setting_diagnostic(f"{group}.{field_name}", reason))
            elif (
                accepted_group is not None
                and field_name in accepted_group
                and section[field_name] != accepted_group[field_name]
            ):
                diagnostics.append(
                    _setting_diagnostic(
                        f"{group}.{field_name}",
                        f"must equal {_ACCEPTED_LIMITS_SETTING}.{group}.{field_name}",
                    )
                )
    return diagnostics


def _accepted_limits(
    settings: Mapping[str, Any], diagnostics: list[str]
) -> Mapping[str, Any] | None:
    """The accepted ``limits`` block the entry point handed over, if any.

    Absent means no block was accepted — a harness or the compatibility
    runtime — and the module's own settings are the only copy. Present but
    not a mapping is a defect of whoever built the settings, reported by
    field like any other.
    """

    accepted = settings.get(_ACCEPTED_LIMITS_SETTING)
    if accepted is None:
        return None
    if not isinstance(accepted, Mapping):
        diagnostics.append(
            _setting_diagnostic(_ACCEPTED_LIMITS_SETTING, "must be a mapping of limit groups")
        )
        return None
    return accepted


def _accepted_group(
    accepted: Mapping[str, Any] | None, group: str, diagnostics: list[str]
) -> Mapping[str, Any] | None:
    """The accepted values of one owned group, to compare field by field (R6).

    ``None`` when nothing was handed over for this group: the module's own
    settings are then the only copy. The comparison itself is value by value,
    never of whole mappings, so a diagnostic names the one field that differs
    and a field the accepted group lacks is simply not compared. No value is
    echoed: both are configured ones.
    """

    if accepted is None or group not in accepted:
        return None
    section = accepted[group]
    if not isinstance(section, Mapping):
        diagnostics.append(
            _setting_diagnostic(
                f"{_ACCEPTED_LIMITS_SETTING}.{group}", "must be a mapping of limits"
            )
        )
        return None
    return section


def _limit_reason(value: Any, kind: str) -> str | None:
    """Why *value* is not an acceptable limit of *kind*, or ``None`` if it is."""

    if value is None:
        return "is required"
    if isinstance(value, bool):
        return _limit_wording(kind)
    if kind == _COUNT:
        if not isinstance(value, int) or value < 1:
            return _limit_wording(kind)
        return None
    if not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        return _limit_wording(kind)
    return None


def _limit_wording(kind: str) -> str:
    if kind == _COUNT:
        return "must be a positive integer"
    return "must be a finite positive number"


def _is_well_formed_url(value: Any) -> bool:
    if not isinstance(value, str) or not value or value != value.strip():
        return False
    if any(character.isspace() for character in value):
        return False
    try:
        parts = urlsplit(value)
        hostname = parts.hostname
        # ``port`` is parsed lazily: a non-numeric or out-of-range port raises
        # only when read, so it is read here to fail preflight rather than the
        # first request.
        parts.port
    except ValueError:
        return False
    return parts.scheme in _ENDPOINT_SCHEMES and bool(parts.netloc) and bool(hostname)


def _setting_diagnostic(field_name: str, reason: str) -> str:
    return f"module {MODULE_NAME!r}: field {field_name!r}: {reason}"



# --------------------------------------------------------------------------- #
# Parsed settings
# --------------------------------------------------------------------------- #


def _default_session_factory() -> Any:
    if aiohttp is None:
        raise RuntimeError("aiohttp is not installed")
    return aiohttp.ClientSession()


SESSION_FACTORY: Callable[[], Any] = _default_session_factory


class BrainModuleError(RuntimeError):
    """A brain operation failure whose text is safe to surface."""


@dataclass(frozen=True, slots=True)
class _AdmissionLimits:
    """The scheduler bounds this module owns, handed to the scheduler it builds."""

    session_queue_capacity: int
    global_pending_capacity: int
    max_sessions: int
    workers: int
    wait_seconds: float
    total_run_seconds: float


@dataclass(frozen=True, slots=True)
class _Budget:
    """Per-run budgets (design §3.4). One model turn is spent per run here."""

    model_turns: int
    model_call_seconds: float
    action_seconds: float
    max_tokens: int
    max_observation_bytes: int


@dataclass(frozen=True, slots=True)
class _MemoryLimits:
    max_sessions: int
    max_exchanges: int
    max_bytes: int
    max_age_seconds: float


@dataclass(frozen=True, repr=False, slots=True)
class _Settings:
    endpoint: str
    model: str
    api_key: str
    admission: _AdmissionLimits
    budget: _Budget
    conversation_memory: _MemoryLimits

    @classmethod
    def from_mapping(cls, settings: Mapping[str, Any]) -> "_Settings":
        """Parse accepted settings; the first diagnostic of a refusal is raised.

        The same hook the loader ran decides here, so a handle built outside
        the loader is refused on the same terms and with the same value-free
        diagnostic.
        """

        diagnostics = validate_settings(settings)
        if diagnostics:
            raise BrainModuleError(diagnostics[0])
        return cls(
            endpoint=settings["endpoint"].strip(),
            model=settings["model"].strip(),
            api_key=settings["api_key"].strip(),
            admission=_group(_AdmissionLimits, settings["admission"]),
            budget=_group(_Budget, settings["budget"]),
            conversation_memory=_group(_MemoryLimits, settings["conversation_memory"]),
        )


def _group(kind: Any, section: Mapping[str, Any]) -> Any:
    return kind(**{field.name: section[field.name] for field in fields(kind)})


# --------------------------------------------------------------------------- #
# Conversation memory (R3, R6)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Exchange:
    """One retained exchange: what the viewer said and what was delivered.

    ``assistant`` is text the send service confirmed — nothing else is ever
    written here (R5). ``timestamp`` is the memory clock at write time and
    ``size`` the UTF-8 weight both texts count against the byte bound.
    """

    timestamp: float
    user: str
    assistant: str
    size: int


@dataclass(slots=True)
class _SessionMemory:
    exchanges: deque[Exchange]
    size: int = 0


class ConversationMemory:
    """Bounded, session-keyed conversation memory (R3, R6, AC10, AC30).

    Keyed by :class:`~core.contracts.SessionKey`, never by the viewer alone:
    the same ``viewer_id`` on another platform or in another channel is
    another memory, and it is never shared across platforms or channels. It is
    not the chat context either — that transcript belongs to the channel and
    is fed at ingestion whatever the run does; this store holds only the
    exchanges the model may be shown as its own.

    Every bound is required and validated as finite and positive at
    construction, raising :class:`BrainModuleError` naming the limit. Sessions
    are ordered least recently used first, so the session cap evicts the one
    that has been silent longest; within a session the count, byte and age
    bounds evict the oldest exchange first. Age is measured on the injected
    clock and re-applied on every read, so a recall alone never returns an
    exchange that has outlived ``max_age_seconds``.
    """

    __slots__ = (
        "_clock",
        "_max_age_seconds",
        "_max_bytes",
        "_max_exchanges",
        "_max_sessions",
        "_sessions",
    )

    def __init__(
        self,
        *,
        max_sessions: int,
        max_exchanges: int,
        max_bytes: int,
        max_age_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max_sessions = _memory_count(max_sessions, "max_sessions")
        self._max_exchanges = _memory_count(max_exchanges, "max_exchanges")
        self._max_bytes = _memory_count(max_bytes, "max_bytes")
        self._max_age_seconds = _memory_duration(max_age_seconds, "max_age_seconds")
        if not callable(clock):
            raise BrainModuleError("brain memory: clock must be callable")
        self._clock = clock
        self._sessions: "OrderedDict[str, _SessionMemory]" = OrderedDict()

    @property
    def limits(self) -> Mapping[str, float]:
        return {
            "max_sessions": self._max_sessions,
            "max_exchanges": self._max_exchanges,
            "max_bytes": self._max_bytes,
            "max_age_seconds": self._max_age_seconds,
        }

    def sessions(self) -> tuple[str, ...]:
        """The retained session keys, least recently used first."""

        return tuple(self._sessions)

    def recall(self, key: SessionKey) -> tuple[Exchange, ...]:
        """The retained exchanges of *key*, oldest first, after age eviction.

        Reading counts as use for the session cap: a session the model is
        being shown is not the one to evict next.
        """

        session_id = _session_id(key)
        session = self._sessions.get(session_id)
        if session is None:
            return ()
        self._evict(session_id, session)
        if session_id not in self._sessions:
            return ()
        self._sessions.move_to_end(session_id)
        return tuple(session.exchanges)

    def remember(self, key: SessionKey, user: str, assistant: str) -> Exchange:
        """Append one confirmed exchange to *key*, then enforce every bound.

        A new session evicts the least recently used sessions until the cap
        holds; the session itself then evicts its oldest exchanges until the
        count, byte and age bounds hold. An exchange that alone exceeds the
        byte bound is not retained — the bound wins over the memory.
        """

        session_id = _session_id(key)
        if not isinstance(user, str) or not isinstance(assistant, str):
            raise BrainModuleError("brain memory: an exchange holds two strings")
        exchange = Exchange(
            timestamp=self._clock(),
            user=user,
            assistant=assistant,
            size=_utf8_size(user) + _utf8_size(assistant),
        )
        session = self._sessions.get(session_id)
        if session is None:
            while len(self._sessions) >= self._max_sessions:
                self._sessions.popitem(last=False)
            session = _SessionMemory(exchanges=deque())
            self._sessions[session_id] = session
        else:
            self._sessions.move_to_end(session_id)
        session.exchanges.append(exchange)
        session.size += exchange.size
        self._evict(session_id, session)
        return exchange

    def forget(self, key: SessionKey) -> bool:
        """Drop one session's memory; return whether one was retained."""

        return self._sessions.pop(_session_id(key), None) is not None

    def clear(self) -> None:
        self._sessions.clear()

    def _evict(self, session_id: str, session: _SessionMemory) -> None:
        """Drop this session's oldest exchanges until every bound holds."""

        horizon = self._clock() - self._max_age_seconds
        exchanges = session.exchanges
        while exchanges and (
            len(exchanges) > self._max_exchanges
            or session.size > self._max_bytes
            or exchanges[0].timestamp < horizon
        ):
            session.size -= exchanges.popleft().size
        if not exchanges:
            # An emptied session holds nothing to recall; keeping it would let
            # dead sessions occupy the session cap against live ones.
            self._sessions.pop(session_id, None)


def _session_id(key: Any) -> str:
    if not isinstance(key, SessionKey):
        raise BrainModuleError("brain memory: a SessionKey is required")
    return key.serialize()


def _utf8_size(text: str) -> int:
    return len(text.encode("utf-8", "surrogatepass"))


def _memory_count(value: Any, name: str) -> int:
    return int(_memory_limit(value, name, _COUNT))


def _memory_duration(value: Any, name: str) -> float:
    return float(_memory_limit(value, name, _SECONDS))


def _memory_limit(value: Any, name: str, kind: str) -> Any:
    """The same terms the settings hook applies, for a store built directly."""

    if _limit_reason(value, kind) is not None:
        raise BrainModuleError(
            _setting_diagnostic(f"conversation_memory.{name}", _limit_wording(kind))
        )
    return value


# --------------------------------------------------------------------------- #
# The handle
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class _Message:
    """A normalised chat message, read from a bus event or an admitted copy."""

    platform: str
    channel_id: str
    viewer_id: str
    message_id: str
    text: str

    @property
    def session_key(self) -> SessionKey:
        return SessionKey(
            platform=self.platform, channel_id=self.channel_id, viewer_id=self.viewer_id
        )


class _NoTrustedIdentity(ValueError):
    """The event names no ``author.id``: refused, never admitted (R3)."""


@dataclass(frozen=True, slots=True)
class _RuntimeSurfaces:
    """The collaborators this module reads from its scoped context (R7)."""

    bus: Any
    actions: Any
    supervision: Any
    executor: Any
    clock: Callable[[], float]
    triggers: Any | None
    scheduler: Any | None
    attachments: Any | None


_REQUIRED_SURFACES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("bus", ("publish", "subscribe")),
    # The model is offered the registry's authorized view and nothing else,
    # so a facade with no such read cannot host this engine (R5).
    ("actions", ("authorized", "discovered", "mark_ready", "mark_not_ready")),
    ("supervision", ("emit", "record_and_emit")),
    # Delivery is one executor call and nothing else, so a runtime without an
    # executor cannot run this engine (R5).
    ("executor", ("invoke",)),
)
_OPTIONAL_SURFACES: tuple[tuple[str, tuple[str, ...]], ...] = (
    # Without a trigger engine nothing recorded a decision to consult;
    # without a shared scheduler the engine builds its own.
    ("triggers", ("recorded",)),
    ("scheduler", ("admit",)),
    # Without an attachment store a run leases nothing that its end must free.
    ("attachments", ("release",)),
)


class _SupervisionCounters:
    """Adapt the supervision facade's ``count`` to the scheduler's counters."""

    __slots__ = ("_supervision",)

    def __init__(self, supervision: Any) -> None:
        self._supervision = supervision

    def increment(self, name: str, amount: int = 1) -> int:
        return self._supervision.count(name, amount)


class BrainModule:
    """The v2 handle: the run body, its memory, one HTTP session, phase hooks.

    Every hook is idempotent and safe out of order — ``close`` after a failed
    ``prepare``, ``drain`` with nothing admitted — because the coordinator
    unwinds a failed startup through the ordinary shutdown sequence (R4).
    """

    def __init__(
        self,
        context: Any,
        settings: _Settings,
        session: Any,
        reporter: Callable[[str], None],
        *,
        sleeper: Callable[[float], Awaitable[None]] | None = None,
        run_id_factory: Callable[[], str] | None = None,
    ) -> None:
        runtime = _runtime_surfaces(context)
        self._bus = runtime.bus
        self._actions = runtime.actions
        self._supervision = runtime.supervision
        self._executor = runtime.executor
        self._triggers = runtime.triggers
        self._attachments = runtime.attachments
        self._clock = runtime.clock
        self._settings = settings
        self._budget = settings.budget
        self._session = session
        self._reporter = reporter
        self._sleep: Callable[[float], Awaitable[None]] = (
            sleeper if sleeper is not None else asyncio.sleep
        )
        self._memory = ConversationMemory(
            clock=runtime.clock,
            **{
                field.name: getattr(settings.conversation_memory, field.name)
                for field in fields(_MemoryLimits)
            },
        )
        self._owns_scheduler = runtime.scheduler is None
        if self._owns_scheduler:
            limits = settings.admission
            self._scheduler: Any = AdmissionScheduler(
                self.run,
                session_queue_capacity=limits.session_queue_capacity,
                global_pending_capacity=limits.global_pending_capacity,
                max_sessions=limits.max_sessions,
                workers=limits.workers,
                total_run_seconds=limits.total_run_seconds,
                wait_seconds=limits.wait_seconds,
                clock=runtime.clock,
                sleeper=sleeper,
                supervision=runtime.supervision,
                counters=(
                    _SupervisionCounters(runtime.supervision)
                    if callable(getattr(runtime.supervision, "count", None))
                    else None
                ),
                id_factory=run_id_factory,
                run_module=MODULE_NAME,
                # The run's end releases what it leased (R6): the scheduler
                # owns the terminal record, so it owns this call too.
                run_cleanup=self._release_run,
            )
        else:
            self._scheduler = runtime.scheduler
        self._prepared = False
        self._closed = False
        self._close_lock = asyncio.Lock()

    # -- read-only surface ------------------------------------------------- #

    @property
    def memory(self) -> ConversationMemory:
        """The conversation memory this module owns; see :class:`ConversationMemory`."""

        return self._memory

    @property
    def scheduler(self) -> Any:
        """The admission scheduler runs go through, shared or owned."""

        return self._scheduler

    @property
    def owns_scheduler(self) -> bool:
        """Whether the scheduler was built here rather than taken from the context."""

        return self._owns_scheduler

    # -- startup phases ---------------------------------------------------- #

    async def prepare(self) -> None:
        """Start the owned scheduler and register the consumer. No input yet.

        A scheduler shared through the context is started and closed by its
        owner. The consumer is routed before any producer may publish into it,
        and only then is the module marked past the barrier.
        """

        if self._prepared or self._closed:
            return
        if self._owns_scheduler:
            await self._scheduler.start()
        self._bus.subscribe(_INPUT_EVENT, self.handle_chat_message)
        self._actions.mark_ready()
        self._prepared = True

    # -- ingestion: validate, copy, admit, return (R2) --------------------- #

    def handle_chat_message(self, event: Mapping[str, Any]) -> None:
        """Admit one normalised chat message and return — synchronously.

        Nothing here awaits: not the model, not a worker, not a trace. The
        scheduler takes its own independent copy of the event, so whatever
        the bus does with it afterwards never reaches the queued work (AC6).
        A shared scheduler is fed by the producer that decided the trigger,
        so this handler admits into an owned scheduler only (see the module
        docstring); a refusal at admission is the scheduler's own traced
        decision and needs nothing from here (AC7).
        """

        if self._closed or not self._owns_scheduler:
            return
        try:
            message = _message_of_event(event)
        except _NoTrustedIdentity:
            self._diagnose("brain input: no trusted viewer identity")
            return
        except ValueError:
            self._diagnose("brain input: malformed chat message")
            return
        if not self._trigger_accepted(message):
            return
        try:
            self._scheduler.admit(
                message.session_key,
                Work(payload=event, source_event_id=message.message_id, kind=WORK_KIND),
            )
        except Exception:
            self._diagnose("brain admission: failed")

    def _trigger_accepted(self, message: _Message) -> bool:
        """Whether the recorded trigger decision lets this event run (R1, AC4).

        With no engine nothing decided and the publication is the whole route.
        With one, the decision the producer recorded for this source event is
        consulted; an event no decision covers is refused — fail closed — and
        a rejected one is left as the traced decision it already is.
        """

        engine = self._triggers
        if engine is None:
            return True
        try:
            decision = engine.recorded(
                platform=message.platform,
                channel_id=message.channel_id,
                source_event_id=message.message_id,
            )
        except Exception:
            self._diagnose("brain input: trigger decision lookup failed")
            return False
        if decision is None:
            self._diagnose("brain input: no trigger decision recorded")
            return False
        return bool(getattr(decision, "accepted", False))

    # -- the run body (R5, R8) --------------------------------------------- #

    async def run(self, run: Any) -> RunOutcome:
        """Take one admitted work to its :class:`~core.admission.RunOutcome`.

        Exactly one model call and exactly one executor call, each behind a
        boundary the run's budget is checked at: an expired budget ends the
        run before the model is reached, or before the delivery is started,
        with 0 further work. The token budget is checked at the same first
        boundary — a prompt that leaves the reply no room inside
        ``budget.max_tokens`` ends the run with 0 model calls — and again on
        the reply, which is discarded when the backend's usage, or the
        estimate standing in for it, puts the run over the bound (§3.4). The
        model call is counted before it is awaited, so a run the shutdown
        cancels while its request is pending is recorded with the call it
        already issued. The reply is delivered through one ``chat.write``
        call carrying the run's identity and its deadline, so a call still in
        flight at the total deadline is classified by the executor, never here
        (AC33). The memory write-back happens here, before the outcome is
        returned, and only for a ``success`` observation (AC19).

        Publishes neither ``brain.run.started`` nor ``brain.run.completed``:
        the scheduler owns both and merges what is returned into its trace.
        """

        try:
            message = _message_of_work(run.work)
        except ValueError:
            self._diagnose("brain run: malformed admitted work")
            return RunOutcome(
                status=_STATUS_ERROR,
                delivery=DELIVERY_NOT_ATTEMPTED,
                correlation={"failure": "malformed_work"},
            )

        key: SessionKey = run.session_key
        destination = Destination(
            platform=key.platform, channel_id=key.channel_id, scope=CHAT_SCOPE
        )
        user_content = _format_user_context(message)
        prompt = self._compose(destination, self._memory.recall(key), user_content)
        if prompt is None:
            self._diagnose("brain run: prompt exceeds the token budget")
            return RunOutcome(
                status=_STATUS_ERROR,
                delivery=DELIVERY_NOT_ATTEMPTED,
                correlation={"failure": "token_budget_exceeded"},
            )

        run.checkpoint()
        run.note_model_call()
        result = await self._request_model(
            prompt, budget_seconds=min(self._budget.model_call_seconds, run.remaining)
        )
        reply = result.content
        if reply is None:
            return RunOutcome(
                status=_STATUS_TIMEOUT if result.failure == "timed_out" else _STATUS_ERROR,
                delivery=DELIVERY_NOT_ATTEMPTED,
                model_calls=1,
                correlation={"failure": result.failure, **result.usage()},
            )

        run.checkpoint()
        call = self._delivery_call(run, message, destination, reply)
        observation = await self._deliver(call)
        delivered = observation.status == _STATUS_SUCCESS
        if delivered and not self._closed:
            self._memory.remember(key, user_content, reply)
        if delivered:
            run.note_send()

        correlation: dict[str, Any] = {
            "action": call.action_name,
            "action_version": call.action_version,
            "call_id": call.call_id,
            **result.usage(),
        }
        error = observation.error
        if isinstance(error, Mapping) and isinstance(error.get("code"), str):
            correlation["delivery_error"] = error["code"]
        return RunOutcome(
            status=observation.status,
            delivery=observation.status,
            model_calls=1,
            sends=1 if delivered else 0,
            correlation=correlation,
        )

    def _compose(
        self,
        destination: Destination,
        history: Sequence[Exchange],
        user_content: str,
    ) -> _Prompt | None:
        """The request messages inside the token budget, or ``None`` (§3.4).

        The instructions and the viewer's message are mandatory; when their
        estimate alone leaves the reply no room the prompt is not composed.
        History is then added newest-first while the input stays within its
        share of the budget, so an old exchange is dropped before the reply's
        room is — and what the input leaves is the output cap the request
        carries.
        """

        system = {"role": "system", "content": self._system_prompt(destination)}
        user = {"role": "user", "content": user_content}
        limit = self._budget.max_tokens
        input_tokens = _estimate_tokens(system["content"]) + _estimate_tokens(user_content)
        if input_tokens >= limit:
            return None
        history_limit = int(limit * _HISTORY_TOKEN_SHARE)
        kept: list[dict[str, str]] = []
        for exchange in reversed(history):
            cost = _estimate_tokens(exchange.user) + _estimate_tokens(exchange.assistant)
            if input_tokens + cost > history_limit:
                break
            input_tokens += cost
            kept.append({"role": "assistant", "content": exchange.assistant})
            kept.append({"role": "user", "content": exchange.user})
        kept.reverse()
        return _Prompt(
            messages=[system, *kept, user],
            input_tokens=input_tokens,
            output_tokens=limit - input_tokens,
        )

    def _system_prompt(self, destination: Destination) -> str:
        """The instructions, with the *authorized* action view and nothing more.

        The view is read for this run's destination and this module's
        principal, afresh every run: an action authorized at the start of a
        previous run is no permission now (R5). Declared or bound actions with
        no applicable rule are not offered.
        """

        lines = [
            "You are a live-stream chat companion.",
            "Reply to the viewer's message with one short plain-text chat message.",
            "Write the message body only: no tags, no markup, no instructions.",
        ]
        offered = self._authorized_actions(destination)
        if offered:
            lines.append("Actions authorized for this reply:")
            lines.extend(
                f"- {name}: {description}" for name, description in offered
            )
        else:
            lines.append("No action is authorized for this reply.")
        return "\n".join(lines)

    def _authorized_actions(self, destination: Destination) -> tuple[tuple[str, str], ...]:
        """The registry's authorized view for this destination, by name.

        Read through the scoped facade the runtime hands every module, so the
        rules in force reach the model in production and not only under an
        injected registry. A view that cannot be read offers nothing: default
        deny expressed as a surface rather than a guess about the rules (R5).
        """

        try:
            view = self._actions.authorized(principal=PRINCIPAL, destination=destination)
        except Exception:
            self._diagnose("brain actions: authorized view unavailable")
            return ()
        offered: list[tuple[str, str]] = []
        for name, spec in dict(view).items():
            description = getattr(spec, "description", "")
            if not isinstance(description, str):
                description = ""
            offered.append((str(name), description))
        return tuple(sorted(offered))

    def _delivery_call(
        self, run: Any, message: _Message, destination: Destination, reply: str
    ) -> ActionCall:
        """The single delivery call of this run, bounded by the run's budget."""

        spec = dict(self._actions.discovered()).get(DELIVERY_ACTION)
        version = getattr(spec, "version", 1) if spec is not None else 1
        return ActionCall(
            action_name=DELIVERY_ACTION,
            action_version=version,
            arguments={"text": reply},
            conversation_id=run.conversation_id,
            run_id=run.run_id,
            call_id=f"{run.run_id}/call-1",
            source_event_id=run.work.source_event_id,
            destination=destination,
            principal=PRINCIPAL,
            deadline=min(run.now + self._budget.action_seconds, run.total_deadline),
            message_id=message.message_id,
        )

    async def _deliver(self, call: ActionCall) -> ActionObservation:
        """One executor call, whose explicit observation is the outcome (R5).

        The executor normalises every expected failure into an observation.
        What still escapes — a defect in the executor itself — is reported as
        a certain ``error`` delivery, never as a success and never retried.
        """

        try:
            observation = await self._executor.invoke(call)
        except asyncio.CancelledError:
            raise
        except Exception:
            self._diagnose("brain delivery: executor call failed")
            return _failed_delivery("executor_failed")
        if (
            getattr(observation, "status", None) not in TERMINAL_STATUSES
            or (
                observation.status != _STATUS_SUCCESS
                and not isinstance(getattr(observation, "error", None), Mapping)
            )
        ):
            self._diagnose("brain delivery: malformed observation")
            return _failed_delivery("malformed_observation")
        return observation

    # -- shutdown phases --------------------------------------------------- #

    async def drain(self, deadline_seconds: float) -> None:
        """Let the owned scheduler finish its admitted runs inside the budget.

        Bounded on the clock and in count. Whatever is still in flight past
        the deadline is ended ``cancelled`` — with its record and its one
        ``brain.run.completed`` — by :meth:`close`. A shared scheduler is
        drained by its owner.
        """

        if not self._owns_scheduler or self._closed:
            return
        scheduler = self._scheduler
        deadline = self._clock() + max(float(deadline_seconds), 0.0)
        polls = 0
        while (
            (scheduler.pending or scheduler.active_runs)
            and self._clock() < deadline
            and polls < _DRAIN_MAX_POLLS
        ):
            polls += 1
            await self._sleep(_DRAIN_POLL_SECONDS)

    async def close(self) -> None:
        """Release everything owned exactly once.

        Closing the owned scheduler ends every run still in flight and every
        queued item ``cancelled``, each recorded and traced once by the
        scheduler, so no admitted work vanishes without its terminal record.
        The memory is cleared and the transport closed afterwards.
        """

        async with self._close_lock:
            if self._closed:
                return
            self._closed = True
            if self._prepared:
                with suppress(Exception):
                    self._actions.mark_not_ready()
            if self._owns_scheduler:
                try:
                    await self._scheduler.aclose()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    self._diagnose("brain scheduler: close failed")
            self._memory.clear()
            await _close_session(self._session)

    # -- the model call ---------------------------------------------------- #

    async def _request_model(
        self, prompt: _Prompt, *, budget_seconds: float
    ) -> _ModelReply:
        """One request bounded in time and in tokens (§3.4).

        The room the prompt leaves inside ``budget.max_tokens`` is sent as the
        request's ``max_tokens``; the run's total is then read from the
        backend's ``usage`` when it reports one and estimated — and reported
        as estimated — when it does not. A reply that puts the total over the
        bound is a failure, not a longer answer. Exception text and response
        bodies can contain credentials or prompt content, so neither is copied
        into diagnostics or into the outcome.
        """

        async def perform() -> _ModelReply:
            response: Any | None = None
            try:
                response = await _resolve(
                    self._session.post(
                        self._settings.endpoint,
                        headers={
                            "Authorization": f"Bearer {self._settings.api_key}",
                            "Content-Type": "application/json",
                        },
                        json={
                            "model": self._settings.model,
                            "messages": prompt.messages,
                            "max_tokens": prompt.output_tokens,
                        },
                    )
                )
                status = getattr(response, "status", None)
                if (
                    not isinstance(status, int)
                    or isinstance(status, bool)
                    or not 200 <= status < 300
                ):
                    raise _NonSuccessResponse
                try:
                    body = await _resolve(response.json())
                except asyncio.CancelledError:
                    raise
                except (asyncio.TimeoutError, TimeoutError):
                    raise
                except Exception:
                    raise _MalformedResponse from None
                content = _response_content(body)
                reported = _response_usage(body)
                estimated = reported is None
                tokens = (
                    prompt.input_tokens + _estimate_tokens(content)
                    if reported is None
                    else reported
                )
                if tokens > self._budget.max_tokens:
                    raise _OverBudgetResponse(tokens, estimated)
                return _ModelReply(content=content, tokens=tokens, estimated=estimated)
            finally:
                if response is not None:
                    await _release_response(response)

        try:
            return await asyncio.wait_for(perform(), timeout=max(budget_seconds, 0.0))
        except asyncio.CancelledError:
            raise
        except (asyncio.TimeoutError, TimeoutError):
            self._diagnose("brain model request: timed out")
            return _ModelReply(failure="timed_out")
        except _NonSuccessResponse:
            self._diagnose("brain model response: non-success status")
            return _ModelReply(failure="non_success_status")
        except _MalformedResponse:
            self._diagnose("brain model response: malformed data")
            return _ModelReply(failure="malformed_response")
        except _OverBudgetResponse as over:
            self._diagnose("brain model response: token budget exceeded")
            return _ModelReply(
                failure="token_budget_exceeded", tokens=over.tokens, estimated=over.estimated
            )
        except Exception:
            self._diagnose("brain model request: transport failed")
            return _ModelReply(failure="transport_failed")

    def _release_run(self, run_id: str) -> None:
        """Free every attachment leased under *run_id*; idempotent (R6).

        The scheduler calls this exactly once per run, when the terminal
        record is written, on every exit path. A store that refuses the call
        is reported as a diagnostic and nothing else: the record already
        stands, and the leases it could not free still fall to the store's
        time-to-live.
        """

        if self._attachments is None:
            return
        try:
            self._attachments.release(run_id)
        except Exception:
            self._diagnose("brain attachments: release failed at run end")

    def _diagnose(self, message: str) -> None:
        _safe_report(self._reporter, message)


class _NonSuccessResponse(Exception):
    pass


class _MalformedResponse(Exception):
    pass


class _OverBudgetResponse(Exception):
    """A reply whose run total exceeds ``budget.max_tokens`` (§3.4)."""

    def __init__(self, tokens: int, estimated: bool) -> None:
        super().__init__()
        self.tokens = tokens
        self.estimated = estimated


@dataclass(frozen=True, slots=True)
class _Prompt:
    """The composed request and the token room it leaves for the reply."""

    messages: list[dict[str, str]]
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True, slots=True)
class _ModelReply:
    """One model call's result: a reply or a failure, with its token usage.

    ``tokens`` is the run's total as the backend reported it, or as it was
    estimated when the backend reported none — ``estimated`` says which, so a
    trace never presents an estimate as a measurement. It is ``None`` when the
    call ended with nothing to count.
    """

    content: str | None = None
    failure: str | None = None
    tokens: int | None = None
    estimated: bool = False

    def usage(self) -> dict[str, Any]:
        """The correlation entries this result contributes to the outcome."""

        if self.tokens is None:
            return {}
        return {"tokens": self.tokens, "tokens_estimated": self.estimated}


def _failed_delivery(code: str) -> ActionObservation:
    return ActionObservation(
        status=_STATUS_ERROR,
        provenance={"source": MODULE_NAME, "route": "executor"},
        error={"code": code, "message": "", "retryable": False},
    )


# --------------------------------------------------------------------------- #
# Activation
# --------------------------------------------------------------------------- #


async def activate(
    context: Any,
    settings: Mapping[str, Any],
    catalog: Mapping[str, Mapping[str, Any]],
) -> BrainModule:
    """Build the handle from the scoped runtime context (R7, AC26).

    *context* is the module-scoped view of the versioned runtime: the bus, the
    executor, the trigger engine, the clock and supervision are read from it,
    and the scheduler is taken from it when it carries one — otherwise one is
    built here from the ``admission`` limits and started at ``prepare``.
    Nothing here reaches the network: the model transport is only opened
    lazily by the first request, so a refused activation has nothing to undo
    beyond the session it created.

    Seams, read from *settings* and never from configuration files:
    ``_session_factory`` builds the HTTP session, ``_sleeper`` replaces the
    sleeps the owned scheduler and ``drain`` wait with, ``_run_id_factory``
    names runs, and ``diagnostic_reporter`` receives value-free diagnostics.
    """

    del catalog  # Capabilities are declared by the colocated manifest.
    _runtime_surfaces(context)  # Refused here, before any session exists.
    parsed = _Settings.from_mapping(settings)
    reporter = settings.get("diagnostic_reporter", _default_reporter)
    if not callable(reporter):
        raise BrainModuleError("brain configuration: diagnostic reporter is invalid")
    session_factory = settings.get("_session_factory", SESSION_FACTORY)
    sleeper = settings.get("_sleeper")
    run_id_factory = settings.get("_run_id_factory")
    if (
        not callable(session_factory)
        or (sleeper is not None and not callable(sleeper))
        or (run_id_factory is not None and not callable(run_id_factory))
    ):
        raise BrainModuleError("brain configuration: transport seam is invalid")

    try:
        created = session_factory()
        session = await created if inspect.isawaitable(created) else created
    except Exception:
        _safe_report(reporter, "brain transport: session creation failed")
        raise BrainModuleError("brain transport initialization failed") from None

    try:
        return BrainModule(
            context,
            parsed,
            session,
            reporter,
            sleeper=sleeper,
            run_id_factory=run_id_factory,
        )
    except BaseException:
        await _close_session(session)
        raise


def _runtime_surfaces(context: Any) -> _RuntimeSurfaces:
    """Read the runtime surfaces from *context*, checked by shape (AC26).

    Duck-typed so a harness may inject fakes, while a context that is not a
    context — the bare bus of the compatibility signature, say — is refused
    with one diagnostic.
    """

    surfaces: dict[str, Any] = {}
    for name, methods in _REQUIRED_SURFACES:
        value = getattr(context, name, None)
        if value is None or not all(callable(getattr(value, m, None)) for m in methods):
            raise BrainModuleError("brain activation: runtime context is invalid")
        surfaces[name] = value
    for name, methods in _OPTIONAL_SURFACES:
        value = getattr(context, name, None)
        if value is not None and not all(
            callable(getattr(value, m, None)) for m in methods
        ):
            raise BrainModuleError("brain activation: runtime context is invalid")
        surfaces[name] = value
    clock = getattr(context, "clock", None)
    if clock is None:
        clock = time.monotonic
    elif not callable(clock):
        raise BrainModuleError("brain activation: runtime context is invalid")
    surfaces["clock"] = clock
    return _RuntimeSurfaces(**surfaces)


# --------------------------------------------------------------------------- #
# Normalised messages
# --------------------------------------------------------------------------- #


def _message_of_event(event: Any) -> _Message:
    """Read a schema-version-2 chat event; ``ValueError`` when it is not one.

    The viewer identity is ``payload.author.id`` and nothing else: it is the
    identity the input module attested at normalisation. An event without it
    raises :class:`_NoTrustedIdentity`, which is a refusal, not a malformed
    event (R3, AC12).
    """

    if not isinstance(event, Mapping):
        raise ValueError
    payload = event.get("payload")
    if not isinstance(payload, Mapping):
        raise ValueError
    platform = payload.get("platform")
    channel_id = payload.get("channel_id")
    message_id = payload.get("message_id")
    text = payload.get("text")
    if not all(_is_text(value) for value in (platform, channel_id, message_id, text)):
        raise ValueError
    author = payload.get("author")
    viewer_id = author.get("id") if isinstance(author, Mapping) else None
    if not _is_text(viewer_id):
        raise _NoTrustedIdentity
    return _Message(
        platform=platform,
        channel_id=channel_id,
        viewer_id=viewer_id,
        message_id=message_id,
        text=text,
    )


def _message_of_work(work: Any) -> _Message:
    """Read the admitted copy back; it was validated at ingestion (R2)."""

    return _message_of_event(getattr(work, "payload", None))


def _is_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _format_user_context(message: _Message) -> str:
    return (
        f"viewer_id: {message.viewer_id}\n"
        f"source_message_id: {message.message_id}\n"
        f"message: {message.text}"
    )


def _response_content(body: Any) -> str:
    try:
        choices = body["choices"]
        choice = choices[0]
        message = choice["message"]
        content = message["content"]
    except (KeyError, IndexError, TypeError):
        raise _MalformedResponse from None
    if not isinstance(content, str) or not content.strip():
        raise _MalformedResponse
    return content


def _response_usage(body: Any) -> int | None:
    """The run total the backend reports, or ``None`` when it reports none.

    ``usage.total_tokens`` is taken when it is a count; otherwise the sum of
    ``prompt_tokens`` and ``completion_tokens`` when both are. A usage block
    that is absent or not a count is no measurement, so the caller estimates.
    """

    usage = body.get("usage") if isinstance(body, Mapping) else None
    if not isinstance(usage, Mapping):
        return None
    total = usage.get("total_tokens")
    if _is_count(total):
        return total
    prompt_tokens = usage.get("prompt_tokens")
    completion_tokens = usage.get("completion_tokens")
    if _is_count(prompt_tokens) and _is_count(completion_tokens):
        return prompt_tokens + completion_tokens
    return None


def _is_count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _estimate_tokens(text: str) -> int:
    """A conservative token count of one message: over-counts prose."""

    return _TOKEN_MESSAGE_OVERHEAD + math.ceil(len(text) / _TOKEN_CHARS)


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #


async def _release_response(response: Any) -> None:
    release = getattr(response, "release", None)
    if callable(release):
        with suppress(Exception):
            result = release()
            if inspect.isawaitable(result):
                await result


async def _close_session(session: Any) -> None:
    close = getattr(session, "close", None)
    if callable(close):
        with suppress(Exception):
            result = close()
            if inspect.isawaitable(result):
                await result


async def _resolve(value: Awaitable[Any] | Any) -> Any:
    return await value if inspect.isawaitable(value) else value


def _safe_report(reporter: Callable[[str], None], message: str) -> None:
    with suppress(Exception):
        reporter(message)


def _default_reporter(message: str) -> None:
    print(message, file=sys.stderr)


__all__ = [
    "CHAT_SCOPE",
    "DELIVERY_ACTION",
    "DELIVERY_NOT_ATTEMPTED",
    "MODULE_NAME",
    "PRINCIPAL",
    "WORK_KIND",
    "BrainModule",
    "BrainModuleError",
    "ConversationMemory",
    "Exchange",
    "activate",
    "validate_settings",
]
