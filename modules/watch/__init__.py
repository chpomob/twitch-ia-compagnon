"""``watch`` — configurable continuous capture: sessions, cadence and caps (R6).

**Sessions.** The module watches 1 to :data:`MAX_CHANNELS` configured
channels (``channels``, entries ``<platform>/<channel_id>``). Each channel has
one watch session, inactive at first. A session starts:

- on a chat event of kind ``message`` whose text is **exactly**
  ``start_command`` (default ``!watch``), authored in that channel by a
  trusted role of ``command_audience`` — ``broadcaster`` (the default: the
  trusted ``broadcaster`` role) or ``moderators`` (the trusted
  ``broadcaster`` or ``moderator`` role). Roles are trusted only when the
  event carries ``author.roles`` with a non-empty ``author.roles_provenance``
  (the platform attested them); a viewer's command starts nothing;
- at ``start_inputs``, for every channel, when ``activation: startup`` is
  configured explicitly (default ``activation: command``).

It stops on ``stop_command`` (default ``!unwatch``) from that audience, once
``max_active_seconds`` passed since it started, or at ``stop_inputs``. A
command is read only between ``start_inputs`` and ``stop_inputs``; a start
command for an active session, or a stop command for an inactive one, does
nothing. Every change publishes one ``watch.state`` fact through supervision:
``platform``, ``channel_id``, ``state`` (``active`` or ``inactive``) and
``reason`` (``command``, ``startup``, ``max_active`` or ``shutdown``).

**The tick scheduler.** An active session runs one supervised task that
sleeps on the injected sleeper and reads the injected clock. A session
started at ``t0`` is due a tick at ``t0 + k × interval_seconds`` (k ≥ 1) and
lives in ``[t0, t0 + max_active_seconds)``: when its end is reached — even at
an instant a tick is also due — it stops with reason ``max_active`` and emits
no further tick. A due tick first meets the per-channel cap: the instants of
the channel's emitted ticks are kept in a sliding deque whose window at
instant ``t`` is ``(t − 3600, t]``; when it already holds
``max_ticks_per_hour`` instants the tick is skipped and counted
(:data:`COUNT_SKIPPED_CAP`), otherwise it is emitted and its instant appended.
The deque belongs to the channel, not the session, so stopping and starting
again never resets the hour. When the clock passed several due instants at
once, one tick is considered and the schedule resumes at the next due instant
after the clock.

**The tick event** (plan step P17). An emitted tick is one normalised
schema-version-2 event of kind ``watch_tick`` (:func:`tick_event`): the
channel's ``platform`` and ``channel_id``, text ``prompt_text``, author the
reserved identity :data:`TICK_AUTHOR` (``system:watch`` — every platform
module refuses an author identity containing ``:``, so no viewer shares its
session), a ``message_id`` from a monotonic per-channel counter and source
``watch``. It is **never published** on the bus — in particular never as
``channel.chat.message`` — so it never reaches the chat context, the users
directory or any command consumer.

**Evaluation and admission.** The tick is decided by the shared trigger
engine with this module's policy (the manifest's default is ``event_kind:
[watch_tick]``); the decision is traced as ``input.trigger.accepted`` or
``input.trigger.rejected``, as an input module traces it. An accepted tick
is admitted **directly** through the runtime's scheduler,
``admit(session_key, work)`` — the one the context carries, or else the one
the brain owns and publishes as the service ``(admission, runs)``
(:data:`ADMISSION_SERVICE_KIND`, :data:`ADMISSION_SERVICE_SCOPE`),
resolved at the first admission, once every module is active — with the session key ``(platform,
channel_id, system:watch)``; the brain makes every call of that run under
the principal ``brain.watch``. Without a trigger engine nothing is decided
and without a scheduler nothing is admitted: the tick is then counted
(:data:`COUNT_NOT_ADMITTED`), never published instead. Every outcome is
counted per channel (:meth:`WatchModule.counts`).

**The in-flight rule.** At most one watch run per channel is in flight. The
module keeps the ``run_id`` of the :class:`~core.admission.AdmissionResult`
of its last admitted tick, per channel, and reads the run's end from the
scheduler's :meth:`~core.admission.AdmissionScheduler.run_record`: the
scheduler writes a run's terminal :class:`~core.admission.RunRecord` on
every exit path, before it publishes ``brain.run.completed``, and records
nothing else, so a present record means the run ended. That is the
completion channel this module uses — a synchronous read at the instant a
tick is due, with no subscription and no ordering against the trace. The
record store is bounded; a record already evicted when the next tick is
due reads as absent, so an absent record counts as "ended" as soon as the
scheduler holds no queued work for the watch session and that session runs
nothing (``active_run``) — whatever other sessions are doing. A tick due while the run is queued or running is skipped before the
hourly cap is consulted (it takes no slot of the hour) and counted
(:data:`COUNT_SKIPPED_IN_FLIGHT`). A scheduler that exposes no
``run_record`` cannot be followed, and nothing is then held in flight.

**Shutdown.** ``stop_inputs`` cancels every tick scheduler and awaits it,
then publishes ``watch.state inactive`` reason ``shutdown`` per active
session; nothing is evaluated, admitted or handed over after it.

**Settings** (checked by :func:`validate_settings`, one value-free diagnostic
naming the field): ``interval_seconds`` 15–3600 (60), ``max_ticks_per_hour``
1–240 (30) and at most ``3600 // interval_seconds``, ``max_active_seconds``
60–14400 (3600), ``activation``, ``start_command``/``stop_command`` (distinct
non-empty words), ``command_audience`` and ``prompt_text`` (at most
:data:`PROMPT_MAX_CHARS` characters).

**Seams, for the tests.** ``_sleeper`` (the scheduler's sleep,
``asyncio.sleep`` by default) and ``_on_tick`` (an observer receiving
``(platform, channel_id, instant)`` for every emitted tick, before it is
decided) are read from *settings* at ``activate`` and never from a
configuration file.
"""

from __future__ import annotations

import asyncio
import inspect
import time
from collections import deque
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from core.admission import Work
from core.contracts import (
    EVENT_KIND_DEFAULT,
    TRACE_INPUT_TRIGGER_ACCEPTED,
    TRACE_INPUT_TRIGGER_REJECTED,
    SessionKey,
)
from core.triggers import NORMALIZED_SCHEMA_VERSION


MODULE_NAME = "watch"

#: The fact every session change publishes.
FACT_WATCH_STATE = "watch.state"

STATE_ACTIVE = "active"
STATE_INACTIVE = "inactive"

REASON_COMMAND = "command"
REASON_STARTUP = "startup"
REASON_MAX_ACTIVE = "max_active"
REASON_SHUTDOWN = "shutdown"

ACTIVATION_COMMAND = "command"
ACTIVATION_STARTUP = "startup"
ACTIVATIONS = (ACTIVATION_COMMAND, ACTIVATION_STARTUP)

AUDIENCE_BROADCASTER = "broadcaster"
AUDIENCE_MODERATORS = "moderators"
#: The trusted roles each ``command_audience`` accepts.
AUDIENCE_ROLES: Mapping[str, frozenset[str]] = {
    AUDIENCE_BROADCASTER: frozenset({"broadcaster"}),
    AUDIENCE_MODERATORS: frozenset({"broadcaster", "moderator"}),
}

#: The sliding window ``max_ticks_per_hour`` counts in: ``(t − 3600, t]``.
WINDOW_SECONDS = 3600

MAX_CHANNELS = 4
INTERVAL_BOUNDS = (15, 3600)
DEFAULT_INTERVAL_SECONDS = 60
MAX_TICKS_BOUNDS = (1, 240)
DEFAULT_MAX_TICKS_PER_HOUR = 30
MAX_ACTIVE_BOUNDS = (60, 14400)
DEFAULT_MAX_ACTIVE_SECONDS = 3600
DEFAULT_ACTIVATION = ACTIVATION_COMMAND
DEFAULT_START_COMMAND = "!watch"
DEFAULT_STOP_COMMAND = "!unwatch"
DEFAULT_COMMAND_AUDIENCE = AUDIENCE_BROADCASTER
COMMAND_MAX_CHARS = 32
PROMPT_MAX_CHARS = 500
DEFAULT_PROMPT_TEXT = "Describe briefly what is happening on the stream right now."

#: The kind, author and work kind of a tick (R6).
TICK_KIND = "watch_tick"
TICK_AUTHOR = "system:watch"
TICK_EVENT_TYPE = "watch.tick"
WORK_KIND = "watch.tick"

#: The service key the brain publishes its owned scheduler under.
ADMISSION_SERVICE_KIND = "admission"
ADMISSION_SERVICE_SCOPE = "runs"

#: The counts :attr:`WatchModule.counts` reports, per channel.
COUNT_EMITTED = "ticks_emitted"
COUNT_SKIPPED_CAP = "ticks_skipped_cap"
COUNT_SKIPPED_IN_FLIGHT = "ticks_skipped_in_flight"
COUNT_ADMITTED = "ticks_admitted"
COUNT_NOT_ADMITTED = "ticks_not_admitted"

_CHAT_EVENT = "channel.chat.message"

_SETTING_CHANNELS = "channels"
_SETTING_INTERVAL = "interval_seconds"
_SETTING_MAX_TICKS = "max_ticks_per_hour"
_SETTING_MAX_ACTIVE = "max_active_seconds"
_SETTING_ACTIVATION = "activation"
_SETTING_START = "start_command"
_SETTING_STOP = "stop_command"
_SETTING_AUDIENCE = "command_audience"
_SETTING_PROMPT = "prompt_text"
_SETTINGS = frozenset(
    {
        _SETTING_CHANNELS,
        _SETTING_INTERVAL,
        _SETTING_MAX_TICKS,
        _SETTING_MAX_ACTIVE,
        _SETTING_ACTIVATION,
        _SETTING_START,
        _SETTING_STOP,
        _SETTING_AUDIENCE,
        _SETTING_PROMPT,
    }
)
# The reserved block the entry point hands every enabled module.
_ACCEPTED_LIMITS_SETTING = "limits"

_SEAM_SLEEPER = "_sleeper"
_SEAM_ON_TICK = "_on_tick"
_SEAMS = frozenset({_SEAM_SLEEPER, _SEAM_ON_TICK})

Sleeper = Callable[[float], Awaitable[Any]]
TickHook = Callable[[str, str, float], Any]
ChannelKey = tuple[str, str]


class WatchModuleError(RuntimeError):
    """A setup failure whose message contains no configured value."""


# --------------------------------------------------------------------------- #
# Settings validation hook
# --------------------------------------------------------------------------- #


def validate_settings(settings: Any) -> list[str]:
    """Check this module's settings; return one diagnostic per offending field.

    This is the hook ``settings_validator`` in the manifest names. Each
    diagnostic names the module and the field and nothing else; an empty list
    means the settings are accepted. The cross-check ``max_ticks_per_hour <=
    3600 // interval_seconds`` reads the default of either field when it is
    absent, and names ``max_ticks_per_hour``.
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
    for seam in (_SEAM_SLEEPER, _SEAM_ON_TICK):
        if seam in settings and not callable(settings[seam]):
            diagnostics.append(_setting_diagnostic(seam, "must be callable"))
    diagnostics.extend(_validate_channels(settings.get(_SETTING_CHANNELS)))
    bounded = (
        (_SETTING_INTERVAL, INTERVAL_BOUNDS),
        (_SETTING_MAX_TICKS, MAX_TICKS_BOUNDS),
        (_SETTING_MAX_ACTIVE, MAX_ACTIVE_BOUNDS),
    )
    for field_name, (low, high) in bounded:
        if field_name in settings and not _is_int_within(settings[field_name], low, high):
            diagnostics.append(
                _setting_diagnostic(field_name, f"must be an integer from {low} to {high}")
            )
    interval = settings.get(_SETTING_INTERVAL, DEFAULT_INTERVAL_SECONDS)
    max_ticks = settings.get(_SETTING_MAX_TICKS, DEFAULT_MAX_TICKS_PER_HOUR)
    if _is_int_within(interval, *INTERVAL_BOUNDS) and _is_int_within(
        max_ticks, *MAX_TICKS_BOUNDS
    ):
        ceiling = WINDOW_SECONDS // interval
        if max_ticks > ceiling:
            diagnostics.append(
                _setting_diagnostic(
                    _SETTING_MAX_TICKS,
                    f"must be at most 3600 // interval_seconds ({ceiling})",
                )
            )
    if _SETTING_ACTIVATION in settings and (
        not isinstance(settings[_SETTING_ACTIVATION], str) or settings[_SETTING_ACTIVATION] not in ACTIVATIONS
    ):
        diagnostics.append(
            _setting_diagnostic(_SETTING_ACTIVATION, "must be one of command, startup")
        )
    if _SETTING_AUDIENCE in settings and (
        not isinstance(settings[_SETTING_AUDIENCE], str) or settings[_SETTING_AUDIENCE] not in AUDIENCE_ROLES
    ):
        diagnostics.append(
            _setting_diagnostic(_SETTING_AUDIENCE, "must be one of broadcaster, moderators")
        )
    for field_name in (_SETTING_START, _SETTING_STOP):
        if field_name in settings and not _is_command(settings[field_name]):
            diagnostics.append(
                _setting_diagnostic(
                    field_name,
                    f"must be a non-empty word of at most {COMMAND_MAX_CHARS} characters",
                )
            )
    start = settings.get(_SETTING_START, DEFAULT_START_COMMAND)
    stop = settings.get(_SETTING_STOP, DEFAULT_STOP_COMMAND)
    if _is_command(start) and _is_command(stop) and start == stop:
        diagnostics.append(_setting_diagnostic(_SETTING_STOP, "must differ from start_command"))
    if _SETTING_PROMPT in settings:
        prompt = settings[_SETTING_PROMPT]
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > PROMPT_MAX_CHARS:
            diagnostics.append(
                _setting_diagnostic(
                    _SETTING_PROMPT,
                    f"must be a non-empty text of at most {PROMPT_MAX_CHARS} characters",
                )
            )
    return diagnostics


def _validate_channels(value: Any) -> list[str]:
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_CHANNELS:
        return [
            _setting_diagnostic(
                _SETTING_CHANNELS, f"must be a list of 1 to {MAX_CHANNELS} channels"
            )
        ]
    keys = [_channel_key(item) for item in value]
    if any(key is None for key in keys):
        return [
            _setting_diagnostic(_SETTING_CHANNELS, "entries must be <platform>/<channel_id>")
        ]
    if len(set(keys)) != len(keys):
        return [_setting_diagnostic(_SETTING_CHANNELS, "entries must be distinct")]
    return []


def _setting_diagnostic(field_name: str, reason: str) -> str:
    return f"module {MODULE_NAME!r}: field {field_name!r}: {reason}"


def _is_int_within(value: Any, low: int, high: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high


def _is_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


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


@dataclass(frozen=True, slots=True)
class WatchSettings:
    """The accepted settings, parsed once."""

    channels: tuple[ChannelKey, ...]
    interval_seconds: int
    max_ticks_per_hour: int
    max_active_seconds: int
    activation: str
    start_command: str
    stop_command: str
    command_audience: str
    prompt_text: str

    @classmethod
    def from_mapping(cls, settings: Mapping[str, Any]) -> "WatchSettings":
        channels = tuple(
            key
            for key in (_channel_key(item) for item in settings.get(_SETTING_CHANNELS) or ())
            if key is not None
        )
        return cls(
            channels=channels,
            interval_seconds=int(settings.get(_SETTING_INTERVAL, DEFAULT_INTERVAL_SECONDS)),
            max_ticks_per_hour=int(settings.get(_SETTING_MAX_TICKS, DEFAULT_MAX_TICKS_PER_HOUR)),
            max_active_seconds=int(settings.get(_SETTING_MAX_ACTIVE, DEFAULT_MAX_ACTIVE_SECONDS)),
            activation=settings.get(_SETTING_ACTIVATION, DEFAULT_ACTIVATION),
            start_command=settings.get(_SETTING_START, DEFAULT_START_COMMAND),
            stop_command=settings.get(_SETTING_STOP, DEFAULT_STOP_COMMAND),
            command_audience=settings.get(_SETTING_AUDIENCE, DEFAULT_COMMAND_AUDIENCE),
            prompt_text=settings.get(_SETTING_PROMPT, DEFAULT_PROMPT_TEXT),
        )

    @property
    def audience_roles(self) -> frozenset[str]:
        return AUDIENCE_ROLES[self.command_audience]


def tick_event(platform: str, channel_id: str, sequence: int, prompt_text: str) -> dict[str, Any]:
    """The normalised schema-version-2 event of the channel's tick number *sequence*."""

    return {
        "type": TICK_EVENT_TYPE,
        "payload": {
            "platform": platform,
            "channel_id": channel_id,
            "author": {"id": TICK_AUTHOR, "display_name": MODULE_NAME},
            "message_id": f"{MODULE_NAME}-{sequence}",
            "text": prompt_text,
            "kind": TICK_KIND,
        },
        "metadata": {"source": MODULE_NAME, "schema_version": NORMALIZED_SCHEMA_VERSION},
    }


class _Channel:
    """One watched channel: its session, its sliding hour of ticks and its run."""

    __slots__ = (
        "key",
        "active",
        "started_at",
        "next_due",
        "task",
        "stamps",
        "counts",
        "sequence",
        "in_flight",
    )

    def __init__(self, key: ChannelKey, max_ticks: int) -> None:
        self.key = key
        self.active = False
        self.started_at = 0.0
        self.next_due = 0.0
        self.task: Any = None
        # The instants of the ticks emitted within the window, oldest first;
        # never more than the cap, since a tick over it is not appended.
        self.stamps: deque[float] = deque(maxlen=max_ticks)
        self.counts = {
            COUNT_EMITTED: 0,
            COUNT_SKIPPED_CAP: 0,
            COUNT_SKIPPED_IN_FLIGHT: 0,
            COUNT_ADMITTED: 0,
            COUNT_NOT_ADMITTED: 0,
        }
        # The last tick number handed out: message ids never repeat.
        self.sequence = 0
        # The ``run_id`` of the admitted watch run not yet seen ended.
        self.in_flight: str | None = None

    @property
    def session_key(self) -> SessionKey:
        return SessionKey(platform=self.key[0], channel_id=self.key[1], viewer_id=TICK_AUTHOR)


# --------------------------------------------------------------------------- #
# The module
# --------------------------------------------------------------------------- #


class WatchModule:
    """The v2 input handle: read the commands, run the sessions, emit ticks."""

    def __init__(
        self,
        context: Any,
        settings: WatchSettings,
        *,
        sleeper: Sleeper,
        on_tick: TickHook | None = None,
    ) -> None:
        self._bus = context.bus
        self._supervision = getattr(context, "supervision", None)
        self._tasks = getattr(context, "tasks", None)
        self._triggers = getattr(context, "triggers", None)
        self._scheduler = getattr(context, "scheduler", None)
        self._services = getattr(context, "services", None)
        self._clock: Callable[[], float] = getattr(context, "clock", None) or time.monotonic
        self._settings = settings
        self._sleeper = sleeper
        self._on_tick = on_tick
        self._channels: dict[ChannelKey, _Channel] = {
            key: _Channel(key, settings.max_ticks_per_hour) for key in settings.channels
        }
        self._diagnostics: list[str] = []
        self._prepared = False
        self._started = False
        self._stopping = False
        self._closed = False

    @property
    def settings(self) -> WatchSettings:
        return self._settings

    @property
    def diagnostics(self) -> tuple[str, ...]:
        return tuple(self._diagnostics)

    def is_active(self, platform: str, channel_id: str) -> bool:
        channel = self._channels.get((platform, channel_id))
        return channel is not None and channel.active

    def counts(self, platform: str, channel_id: str) -> Mapping[str, int]:
        """How many ticks the channel emitted, skipped (cap, in flight) and admitted."""

        channel = self._channels.get((platform, channel_id))
        return {} if channel is None else dict(channel.counts)

    def window(self, platform: str, channel_id: str) -> tuple[float, ...]:
        """The instants of the channel's ticks inside ``(now − 3600, now]``."""

        channel = self._channels.get((platform, channel_id))
        if channel is None:
            return ()
        return tuple(self._prune(channel, self._clock()))

    def in_flight(self, platform: str, channel_id: str) -> int:
        """How many watch runs of the channel are queued or running: 0 or 1."""

        channel = self._channels.get((platform, channel_id))
        return int(channel is not None and self._in_flight(channel))

    # -- lifecycle hooks ---------------------------------------------------- #

    async def prepare(self) -> None:
        """Route the chat commands; nothing is emitted before ``start_inputs``."""

        if self._prepared or self._closed:
            return
        self._bus.subscribe(_CHAT_EVENT, self.handle_chat_message)
        self._prepared = True

    async def start_inputs(self) -> None:
        """After the readiness barrier: read commands; start at startup if configured."""

        if not self._prepared or self._closed or self._stopping or self._started:
            return
        self._started = True
        if self._settings.activation == ACTIVATION_STARTUP:
            for channel in self._channels.values():
                await self._start(channel, REASON_STARTUP)

    async def stop_inputs(self) -> None:
        """End every active session: no tick after this returns."""

        self._stopping = True
        await self._stop_all(REASON_SHUTDOWN)

    async def close(self) -> None:
        """End every session still active; later chat messages are ignored."""

        if self._closed:
            return
        self._stopping = True
        await self._stop_all(REASON_SHUTDOWN)
        self._closed = True

    async def _stop_all(self, reason: str) -> None:
        for channel in self._channels.values():
            await self._stop(channel, reason)

    # -- the commands --------------------------------------------------------- #

    async def handle_chat_message(self, event: Mapping[str, Any]) -> None:
        """Start or stop a session on an exact command from the audience.

        Never raises and never returns a replacement: this consumer sits in
        the publisher's chain and must not change or fail it.
        """

        if self._closed or not self._started or self._stopping:
            return
        try:
            command = self._command(event)
        except Exception:  # noqa: BLE001 - a consumer must not fail the publisher
            self._diagnose("command: failed")
            return
        if command is None:
            return
        channel, starts = command
        try:
            if starts:
                await self._start(channel, REASON_COMMAND)
            else:
                await self._stop(channel, REASON_COMMAND)
        except Exception:  # noqa: BLE001 - a consumer must not fail the publisher
            self._diagnose("session: failed")

    def _command(self, event: Any) -> tuple[_Channel, bool] | None:
        """``(channel, starts)`` for a start or stop command, else ``None``."""

        payload = event.get("payload") if isinstance(event, Mapping) else None
        if not isinstance(payload, Mapping):
            return None
        kind = payload.get("kind", EVENT_KIND_DEFAULT)
        if kind is not None and kind != EVENT_KIND_DEFAULT:
            return None
        text = payload.get("text")
        if text == self._settings.start_command:
            starts = True
        elif text == self._settings.stop_command:
            starts = False
        else:
            return None
        channel = self._channels.get((payload.get("platform"), payload.get("channel_id")))
        if channel is None:
            return None
        author = payload.get("author")
        if not isinstance(author, Mapping) or not _is_text(author.get("roles_provenance")):
            return None
        claimed = author.get("roles")
        if not isinstance(claimed, (list, tuple)):
            return None
        roles = {role for role in claimed if isinstance(role, str)}
        if not roles & self._settings.audience_roles:
            return None
        return channel, starts

    # -- the sessions --------------------------------------------------------- #

    async def _start(self, channel: _Channel, reason: str) -> None:
        if channel.active or self._stopping:
            return
        now = self._clock()
        channel.active = True
        channel.started_at = now
        channel.next_due = now + self._settings.interval_seconds
        # The scheduler is owned before the publication await: a cancelled
        # publication must not leave an active session that never ticks or ends.
        channel.task = self._spawn(self._tick_loop(channel), name=f"{MODULE_NAME}-ticks")
        await self._publish_state(channel, STATE_ACTIVE, reason)

    async def _stop(self, channel: _Channel, reason: str) -> None:
        """Mark the session inactive, stop its scheduler, publish the fact."""

        if not channel.active:
            return
        channel.active = False
        task, channel.task = channel.task, None
        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await self._publish_state(channel, STATE_INACTIVE, reason)

    def _spawn(self, coro: Awaitable[Any], *, name: str) -> Any:
        spawn = getattr(self._tasks, "spawn", None)
        if callable(spawn):
            return spawn(coro, name=name)
        return asyncio.ensure_future(coro)

    # -- the tick scheduler --------------------------------------------------- #

    async def _tick_loop(self, channel: _Channel) -> None:
        """Emit the session's ticks until it ends; the end wins a tie."""

        interval = float(self._settings.interval_seconds)
        ends_at = channel.started_at + self._settings.max_active_seconds
        while channel.active:
            now = self._clock()
            if now >= ends_at:
                await self._stop(channel, REASON_MAX_ACTIVE)
                return
            if now >= channel.next_due:
                await self._due(channel, now)
                while channel.next_due <= now:
                    channel.next_due += interval
                continue
            await self._sleeper(min(channel.next_due, ends_at) - now)

    async def _due(self, channel: _Channel, now: float) -> None:
        """One due tick: skipped while a run is in flight or over the cap, else emitted."""

        if self._in_flight(channel):
            channel.counts[COUNT_SKIPPED_IN_FLIGHT] += 1
            return
        stamps = self._prune(channel, now)
        if len(stamps) >= self._settings.max_ticks_per_hour:
            channel.counts[COUNT_SKIPPED_CAP] += 1
            return
        stamps.append(now)
        channel.counts[COUNT_EMITTED] += 1
        await self._emit_tick(channel, now)

    def _prune(self, channel: _Channel, now: float) -> deque[float]:
        """Drop the instants outside the window ``(now − 3600, now]``."""

        stamps = channel.stamps
        while stamps and stamps[0] <= now - WINDOW_SECONDS:
            stamps.popleft()
        return stamps

    async def _emit_tick(self, channel: _Channel, now: float) -> None:
        """Build the tick, hand it to the observer, decide it, admit it."""

        channel.sequence += 1
        event = tick_event(*channel.key, channel.sequence, self._settings.prompt_text)
        hook = self._on_tick
        if hook is not None:
            try:
                hook(channel.key[0], channel.key[1], now)
            except Exception:  # noqa: BLE001 - a failed observer must not stop the session
                self._diagnose("tick: hand-off failed")
        engine = self._triggers
        if engine is None:
            # Nothing decides, so nothing is admitted: fail closed.
            channel.counts[COUNT_NOT_ADMITTED] += 1
            return
        try:
            decision = engine.evaluate(event)
        except Exception:  # noqa: BLE001 - an undecided tick is never admitted
            self._diagnose("tick: trigger evaluation failed")
            channel.counts[COUNT_NOT_ADMITTED] += 1
            return
        await self._trace_decision(decision)
        if not decision.accepted or not channel.active or self._stopping:
            channel.counts[COUNT_NOT_ADMITTED] += 1
            return
        self._admit(channel, event)

    def _admit(self, channel: _Channel, event: Mapping[str, Any]) -> None:
        """Hand the accepted tick to the scheduler and keep its run as in flight."""

        scheduler = self._admission_target()
        if scheduler is None:
            self._diagnose("tick: no scheduler to admit to")
            channel.counts[COUNT_NOT_ADMITTED] += 1
            return
        try:
            result = scheduler.admit(
                channel.session_key,
                Work(
                    payload=event,
                    source_event_id=event["payload"]["message_id"],
                    kind=WORK_KIND,
                ),
            )
        except Exception:  # noqa: BLE001 - a closed scheduler must not stop the session
            self._diagnose("tick: admission failed")
            channel.counts[COUNT_NOT_ADMITTED] += 1
            return
        if getattr(result, "accepted", False) is not True:
            # A refusal is the scheduler's own traced saturation decision.
            channel.counts[COUNT_NOT_ADMITTED] += 1
            return
        channel.counts[COUNT_ADMITTED] += 1
        run_id = getattr(result, "run_id", None)
        channel.in_flight = run_id if _is_text(run_id) else None

    def _admission_target(self) -> Any:
        """The context's scheduler, else the published one, resolved once."""

        if self._scheduler is None:
            resolve = getattr(self._services, "resolve", None)
            if callable(resolve):
                try:
                    found = resolve(ADMISSION_SERVICE_KIND, ADMISSION_SERVICE_SCOPE)
                except Exception:  # noqa: BLE001 - an unreadable registry admits nothing
                    found = None
                if callable(getattr(found, "admit", None)):
                    self._scheduler = found
        return self._scheduler

    def _in_flight(self, channel: _Channel) -> bool:
        """Whether the channel's last admitted run is still queued or running.

        Read from the scheduler's terminal record (see the module docstring):
        present means ended. Absent means in flight, unless the scheduler
        holds no work of the watch session and runs nothing for it — then
        the record was evicted from the bounded store and the run ended.
        """

        run_id = channel.in_flight
        if run_id is None:
            return False
        lookup = getattr(self._scheduler, "run_record", None)
        if not callable(lookup):
            channel.in_flight = None
            return False
        try:
            ended = lookup(run_id) is not None or self._session_idle(channel)
        except Exception:  # noqa: BLE001 - an unreadable record must not hold ticks forever
            self._diagnose("tick: run record unreadable")
            ended = True
        if ended:
            channel.in_flight = None
        return not ended

    def _session_idle(self, channel: _Channel) -> bool:
        """Whether the watch session has nothing queued and nothing running.

        Per session, so another session's traffic never holds the ticks.
        """

        scheduler = self._scheduler
        depth = getattr(scheduler, "queue_depth", None)
        active = getattr(scheduler, "active_run", None)
        if not callable(depth) or not callable(active):
            return False
        return depth(channel.session_key) == 0 and active(channel.session_key) is None

    async def _trace_decision(self, decision: Any) -> None:
        """Publish the tick's trigger decision as an input's supervision fact."""

        emit = getattr(self._supervision, "emit", None)
        if not callable(emit) or not getattr(decision, "traceable", True):
            return
        event_type = (
            TRACE_INPUT_TRIGGER_ACCEPTED if decision.accepted else TRACE_INPUT_TRIGGER_REJECTED
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
            outcome = emit(event_type, payload)
            if inspect.isawaitable(outcome):
                await outcome
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a lost trace changes nothing decided
            self._diagnose("tick: trigger trace not published")

    # -- facts ---------------------------------------------------------------- #

    async def _publish_state(self, channel: _Channel, state: str, reason: str) -> None:
        emit = getattr(self._supervision, "emit", None)
        if not callable(emit):
            return
        fact = {
            "platform": channel.key[0],
            "channel_id": channel.key[1],
            "state": state,
            "reason": reason,
        }
        try:
            outcome = emit(FACT_WATCH_STATE, fact)
            if inspect.isawaitable(outcome):
                await outcome
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a lost fact must not undo a session change
            self._diagnose("fact: not published")

    def _diagnose(self, text: str) -> None:
        self._diagnostics.append(f"{MODULE_NAME} {text}")
        del self._diagnostics[:-16]


# --------------------------------------------------------------------------- #
# Activation
# --------------------------------------------------------------------------- #


async def activate(
    context: Any,
    settings: Mapping[str, Any],
    catalog: Mapping[str, Mapping[str, Any]],
) -> WatchModule:
    """Validate the settings and build the handle; nothing starts here."""

    del catalog  # Nothing is declared from the catalog.
    bus = getattr(context, "bus", None)
    tasks = getattr(context, "tasks", None)
    if not callable(getattr(bus, "subscribe", None)) or not callable(
        getattr(tasks, "spawn", None)
    ):
        raise WatchModuleError(f"{MODULE_NAME} activation: runtime context is invalid")
    if not isinstance(settings, Mapping):
        raise WatchModuleError(f"{MODULE_NAME} configuration: settings must be a mapping")
    diagnostics = validate_settings(settings)
    if diagnostics:
        raise WatchModuleError(
            f"{MODULE_NAME} configuration: settings were refused "
            f"({len(diagnostics)} diagnostics)"
        )
    return WatchModule(
        context,
        WatchSettings.from_mapping(settings),
        sleeper=settings.get(_SEAM_SLEEPER, asyncio.sleep),
        on_tick=settings.get(_SEAM_ON_TICK),
    )


__all__ = [
    "ACTIVATIONS",
    "ADMISSION_SERVICE_KIND",
    "ADMISSION_SERVICE_SCOPE",
    "ACTIVATION_COMMAND",
    "ACTIVATION_STARTUP",
    "AUDIENCE_BROADCASTER",
    "AUDIENCE_MODERATORS",
    "AUDIENCE_ROLES",
    "COUNT_ADMITTED",
    "COUNT_EMITTED",
    "COUNT_NOT_ADMITTED",
    "COUNT_SKIPPED_CAP",
    "COUNT_SKIPPED_IN_FLIGHT",
    "DEFAULT_ACTIVATION",
    "DEFAULT_COMMAND_AUDIENCE",
    "DEFAULT_INTERVAL_SECONDS",
    "DEFAULT_MAX_ACTIVE_SECONDS",
    "DEFAULT_MAX_TICKS_PER_HOUR",
    "DEFAULT_PROMPT_TEXT",
    "DEFAULT_START_COMMAND",
    "DEFAULT_STOP_COMMAND",
    "FACT_WATCH_STATE",
    "MAX_CHANNELS",
    "MODULE_NAME",
    "PROMPT_MAX_CHARS",
    "REASON_COMMAND",
    "REASON_MAX_ACTIVE",
    "REASON_SHUTDOWN",
    "REASON_STARTUP",
    "STATE_ACTIVE",
    "STATE_INACTIVE",
    "TICK_AUTHOR",
    "TICK_EVENT_TYPE",
    "TICK_KIND",
    "WINDOW_SECONDS",
    "WORK_KIND",
    "WatchModule",
    "WatchModuleError",
    "WatchSettings",
    "activate",
    "tick_event",
    "validate_settings",
]
