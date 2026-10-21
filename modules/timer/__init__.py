"""``timer`` — a timed random trigger is a setting (phase 8 R8).

**The type.** The manifest declares the trigger type ``interval`` (kinds
``[timer]``): ``every_minutes`` (required integer 1–1440), ``probability``
(required number 0–1, its ``probability_parameter``) and an optional
``window`` ``{start_minute, end_minute}`` (integers 0–1439, host local time,
both bounds inclusive). It is a declared ``match`` rule over the declared
field ``timer.minute_of_day``, so the kernel evaluates it and no module code
runs for it: the event is accepted only when its minute is inside the window
(an absent window always matches) and the one draw is below ``probability``.
The default policy is ``interval{every_minutes: 30, probability: 0.25}``.

**The firing.** For each configured channel (``channels``: a ``destination``
and a non-empty ``prompt_text``) one supervised task runs from
``start_inputs`` to ``stop_inputs``. The channel's interval is read from the
policy resolved for it: a loop started at ``t0`` is due at ``t0 + k ×
every_minutes × 60`` (k ≥ 1) on the injected clock, scheduled from ``t0``
and never from the previous firing, so a slow admission accumulates no
delay. When the clock passed several due instants at once, one firing is
considered and the schedule resumes at the next due instant after the clock.
Each firing builds one normalised schema-version-2 event of kind ``timer``
(:func:`timer_event`): text the channel's ``prompt_text``, ``payload.timer``
= ``{sequence, minute_of_day}`` (the per-channel firing number from 1, and
the host's local minute of the day from the ``_local_minute`` seam), author
the reserved identity ``timer:<channel_id>`` (every platform module refuses
an author identity containing ``:``, so no viewer shares its session),
``message_id`` ``timer:<platform>:<channel_id>:<sequence>`` and source
``timer``.

**Evaluation and admission.** The event is decided by the shared trigger
engine with this module's policy; the decision is traced as
``input.trigger.accepted`` or ``input.trigger.rejected`` with
:func:`~core.triggers.decision_trace_payload`. As the watch input's tick, the
event is **never published** on the bus: an accepted one is admitted
**directly** through the runtime's scheduler, ``admit(session_key, work)`` —
the one the context carries, or else the one the brain owns and publishes as
the service ``(admission, runs)`` (:data:`ADMISSION_SERVICE_KIND`,
:data:`ADMISSION_SERVICE_SCOPE`), resolved at the first admission — with the
session key ``(platform, channel_id, timer:<channel_id>)``. Without a trigger
engine nothing is decided and without a scheduler nothing is admitted: the
event is then counted (:data:`COUNT_NOT_ADMITTED`). Admission, budgets and
authorization decide everything after the trigger, exactly as for any input.

**The policy check.** A window's ``start_minute < end_minute`` is a policy
check, not a schema one, so ``prepare`` checks each configured channel's
resolved policy: it must hold exactly one ``interval`` rule, and a window on
it must start before it ends. A violation reports ``module.degraded`` with a
value-free reason naming the channel index and the policy field, then
raises: no loop starts. Out-of-range ``every_minutes`` and ``probability``
are refused by the declared schema when the policy is configured, at load.

**Settings** (checked by :func:`validate_settings`, one value-free diagnostic
naming the field): 1 to :data:`MAX_CHANNELS` channels, each with a
``destination`` (``platform``, ``channel_id``, non-empty texts) listed once
and a ``prompt_text`` of at most :data:`PROMPT_MAX_CHARS` characters.

**Seams, for the tests.** ``_sleeper`` (the loops' sleep, ``asyncio.sleep``
by default), ``_local_minute`` (the host's local minute of the day, read from
:func:`time.localtime` by default) and ``_on_fire`` (an observer receiving
every built event, before it is decided) are read from *settings* at
``activate`` and never from a configuration file.
"""

from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from core.admission import Work
from core.contracts import (
    TRACE_INPUT_TRIGGER_ACCEPTED,
    TRACE_INPUT_TRIGGER_REJECTED,
    SessionKey,
)
from core.triggers import NORMALIZED_SCHEMA_VERSION, decision_trace_payload


MODULE_NAME = "timer"

#: The kind, event type, author prefix and work kind of a firing (R8).
TIMER_KIND = "timer"
TIMER_EVENT_TYPE = "channel.chat.message"
AUTHOR_PREFIX = "timer:"
WORK_KIND = "timer.fire"

#: The declared type a channel's policy must hold exactly once.
INTERVAL_TYPE = "interval"
PARAMETER_EVERY_MINUTES = "every_minutes"
PARAMETER_WINDOW = "window"

#: The service key the brain publishes its owned scheduler under.
ADMISSION_SERVICE_KIND = "admission"
ADMISSION_SERVICE_SCOPE = "runs"

MAX_CHANNELS = 32
PROMPT_MAX_CHARS = 500
MINUTES_PER_DAY = 1440
SECONDS_PER_MINUTE = 60

#: The counts :meth:`TimerModule.counts` reports, per channel.
COUNT_FIRED = "fired"
COUNT_ADMITTED = "admitted"
COUNT_NOT_ADMITTED = "not_admitted"

_SETTING_CHANNELS = "channels"
_CHANNEL_KEYS = frozenset({"destination", "prompt_text"})
_DESTINATION_KEYS = frozenset({"platform", "channel_id"})
# The reserved block the entry point hands every enabled module.
_ACCEPTED_LIMITS_SETTING = "limits"

_SEAM_SLEEPER = "_sleeper"
_SEAM_LOCAL_MINUTE = "_local_minute"
_SEAM_ON_FIRE = "_on_fire"
_SEAMS = frozenset({_SEAM_SLEEPER, _SEAM_LOCAL_MINUTE, _SEAM_ON_FIRE})

Sleeper = Callable[[float], Awaitable[Any]]
LocalMinute = Callable[[], int]
FireHook = Callable[[Mapping[str, Any]], Any]


class TimerModuleError(RuntimeError):
    """A setup failure whose message contains no configured value."""


# --------------------------------------------------------------------------- #
# Settings validation hook
# --------------------------------------------------------------------------- #


def validate_settings(settings: Any) -> list[str]:
    """Check this module's settings; return one diagnostic per offending field.

    This is the hook ``settings_validator`` in the manifest names. Each
    diagnostic names the module and the field and nothing else — no
    destination or text is echoed; an empty list means the settings are
    accepted. The interval, probability and window are the channel's trigger
    policy, checked at load and in ``prepare``, not here.
    """

    if not isinstance(settings, Mapping):
        return [_setting_diagnostic("settings", "must be a mapping")]
    diagnostics: list[str] = []
    for field_name in settings:
        if (
            field_name != _SETTING_CHANNELS
            and field_name != _ACCEPTED_LIMITS_SETTING
            and field_name not in _SEAMS
        ):
            diagnostics.append(
                _setting_diagnostic(str(field_name), "is not a setting this module declares")
            )
    for seam in _SEAMS:
        if seam in settings and not callable(settings[seam]):
            diagnostics.append(_setting_diagnostic(seam, "must be callable"))
    diagnostics.extend(_validate_channels(settings.get(_SETTING_CHANNELS)))
    return diagnostics


def _validate_channels(value: Any) -> list[str]:
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_CHANNELS:
        return [
            _setting_diagnostic(
                _SETTING_CHANNELS, f"must be a list of 1 to {MAX_CHANNELS} channels"
            )
        ]
    diagnostics: list[str] = []
    seen: set[tuple[str, str]] = set()
    for index, channel in enumerate(value):
        field = f"{_SETTING_CHANNELS}[{index}]"
        if not isinstance(channel, Mapping):
            diagnostics.append(_setting_diagnostic(field, "must be a mapping"))
            continue
        for key in channel:
            if key not in _CHANNEL_KEYS:
                diagnostics.append(
                    _setting_diagnostic(f"{field}.{key}", "is not a channel setting")
                )
        destination = channel.get("destination")
        if not isinstance(destination, Mapping):
            diagnostics.append(_setting_diagnostic(f"{field}.destination", "must be a mapping"))
        else:
            valid = True
            for key in destination:
                if key not in _DESTINATION_KEYS:
                    diagnostics.append(
                        _setting_diagnostic(
                            f"{field}.destination.{key}", "is not a destination setting"
                        )
                    )
            for key in ("platform", "channel_id"):
                if not _is_text(destination.get(key)):
                    valid = False
                    diagnostics.append(
                        _setting_diagnostic(
                            f"{field}.destination.{key}", "must be a non-empty text"
                        )
                    )
            if valid:
                pair = (destination["platform"], destination["channel_id"])
                if pair in seen:
                    diagnostics.append(
                        _setting_diagnostic(f"{field}.destination", "must be distinct")
                    )
                seen.add(pair)
        prompt = channel.get("prompt_text")
        if not _is_text(prompt) or len(prompt) > PROMPT_MAX_CHARS:
            diagnostics.append(
                _setting_diagnostic(
                    f"{field}.prompt_text",
                    f"must be a non-empty text of at most {PROMPT_MAX_CHARS} characters",
                )
            )
    return diagnostics


def _setting_diagnostic(field_name: str, reason: str) -> str:
    return f"module {MODULE_NAME!r}: field {field_name!r}: {reason}"


def _is_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _local_minute_of_day() -> int:
    """The host's local minute of the day, 0–1439."""

    now = time.localtime()
    return now.tm_hour * 60 + now.tm_min


# --------------------------------------------------------------------------- #
# Settings and the event
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class TimerChannel:
    """One configured channel."""

    platform: str
    channel_id: str
    prompt_text: str


@dataclass(frozen=True, slots=True)
class TimerSettings:
    """The accepted settings, parsed once."""

    channels: tuple[TimerChannel, ...]

    @classmethod
    def from_mapping(cls, settings: Mapping[str, Any]) -> "TimerSettings":
        return cls(
            channels=tuple(
                TimerChannel(
                    platform=entry["destination"]["platform"],
                    channel_id=entry["destination"]["channel_id"],
                    prompt_text=entry["prompt_text"],
                )
                for entry in settings.get(_SETTING_CHANNELS) or ()
            )
        )


def timer_event(channel: TimerChannel, sequence: int, minute_of_day: int) -> dict[str, Any]:
    """The normalised schema-version-2 ``timer`` event of firing *sequence*."""

    author = f"{AUTHOR_PREFIX}{channel.channel_id}"
    return {
        "type": TIMER_EVENT_TYPE,
        "payload": {
            "platform": channel.platform,
            "channel_id": channel.channel_id,
            "author": {"id": author, "display_name": MODULE_NAME},
            "message_id": f"{AUTHOR_PREFIX}{channel.platform}:{channel.channel_id}:{sequence}",
            "text": channel.prompt_text,
            "kind": TIMER_KIND,
            "timer": {"sequence": sequence, "minute_of_day": minute_of_day},
        },
        "metadata": {"source": MODULE_NAME, "schema_version": NORMALIZED_SCHEMA_VERSION},
    }


class _Loop:
    """One channel's firing loop: its interval, its sequence and its counts."""

    __slots__ = ("channel", "interval_seconds", "sequence", "task", "counts")

    def __init__(self, channel: TimerChannel, every_minutes: int) -> None:
        self.channel = channel
        self.interval_seconds = float(every_minutes * SECONDS_PER_MINUTE)
        # The last firing number handed out: message ids never repeat.
        self.sequence = 0
        self.task: Any = None
        self.counts = {COUNT_FIRED: 0, COUNT_ADMITTED: 0, COUNT_NOT_ADMITTED: 0}

    @property
    def session_key(self) -> SessionKey:
        return SessionKey(
            platform=self.channel.platform,
            channel_id=self.channel.channel_id,
            viewer_id=f"{AUTHOR_PREFIX}{self.channel.channel_id}",
        )


# --------------------------------------------------------------------------- #
# The module
# --------------------------------------------------------------------------- #


class TimerModule:
    """The v2 input handle: check the policies, run the loops, admit firings."""

    def __init__(
        self,
        context: Any,
        settings: TimerSettings,
        *,
        sleeper: Sleeper,
        local_minute: LocalMinute,
        on_fire: FireHook | None = None,
    ) -> None:
        self._supervision = getattr(context, "supervision", None)
        self._tasks = getattr(context, "tasks", None)
        self._triggers = getattr(context, "triggers", None)
        self._scheduler = getattr(context, "scheduler", None)
        self._services = getattr(context, "services", None)
        self._clock: Callable[[], float] = getattr(context, "clock", None) or time.monotonic
        self._settings = settings
        self._sleeper = sleeper
        self._local_minute = local_minute
        self._on_fire = on_fire
        self._loops: list[_Loop] = []
        self._started_at = 0.0
        self._diagnostics: list[str] = []
        self._prepared = False
        self._started = False
        self._stopping = False
        self._closed = False

    @property
    def settings(self) -> TimerSettings:
        return self._settings

    @property
    def diagnostics(self) -> tuple[str, ...]:
        return tuple(self._diagnostics)

    @property
    def running(self) -> int:
        """How many firing loops are running."""

        return sum(1 for loop in self._loops if loop.task is not None and not loop.task.done())

    def counts(self) -> dict[tuple[str, str], Mapping[str, int]]:
        """Per ``(platform, channel_id)``: fired, admitted, not admitted."""

        return {
            (loop.channel.platform, loop.channel.channel_id): dict(loop.counts)
            for loop in self._loops
        }

    # -- lifecycle hooks ---------------------------------------------------- #

    async def prepare(self) -> None:
        """Check each channel's resolved policy; start nothing.

        A channel whose policy does not hold exactly one ``interval`` rule,
        or whose window does not start before it ends, is reported
        ``module.degraded`` naming the channel index and the field, then
        raises with the same value-free diagnostic.
        """

        if self._prepared or self._closed:
            return
        loops: list[_Loop] = []
        for index, channel in enumerate(self._settings.channels):
            every_minutes, problem = self._interval_of(channel)
            if problem is not None:
                diagnostic = f"{MODULE_NAME} prepare: {_SETTING_CHANNELS}[{index}]: {problem}"
                await self._report_degraded(diagnostic)
                raise TimerModuleError(diagnostic)
            loops.append(_Loop(channel, every_minutes))
        self._loops = loops
        self._prepared = True

    def _interval_of(self, channel: TimerChannel) -> tuple[int, str | None]:
        """The channel's ``every_minutes``, or a value-free problem."""

        label = f"triggers.{MODULE_NAME}.policy"
        registry = getattr(self._triggers, "registry", None)
        resolve = getattr(registry, "resolve", None)
        if not callable(resolve):
            return 0, f"field {label!r}: no trigger engine resolves it"
        try:
            policy = resolve(MODULE_NAME, channel.channel_id)
        except Exception:  # noqa: BLE001 - an unreadable registry starts nothing
            policy = None
        rules = tuple(getattr(policy, "rules", ()) or ())
        intervals = [
            (index, rule) for index, rule in enumerate(rules) if rule.type == INTERVAL_TYPE
        ]
        if len(intervals) != 1:
            return 0, (
                f"field '{label}.rules': must hold exactly one {INTERVAL_TYPE!r} rule, "
                f"holds {len(intervals)}"
            )
        index, rule = intervals[0]
        parameters = rule.parameters
        every_minutes = parameters.get(PARAMETER_EVERY_MINUTES)
        if not _is_int(every_minutes) or not 1 <= every_minutes <= MINUTES_PER_DAY:
            return 0, (
                f"field '{label}.rules[{index}].parameters.{PARAMETER_EVERY_MINUTES}': "
                f"must be an integer from 1 to {MINUTES_PER_DAY}"
            )
        window = parameters.get(PARAMETER_WINDOW)
        if window is not None and not window["start_minute"] < window["end_minute"]:
            return 0, (
                f"field '{label}.rules[{index}].parameters.{PARAMETER_WINDOW}': "
                "start_minute must be below end_minute"
            )
        return every_minutes, None

    async def start_inputs(self) -> None:
        """After the readiness barrier: start one firing loop per channel."""

        if not self._prepared or self._closed or self._stopping or self._started:
            return
        self._started = True
        self._started_at = self._clock()
        for loop in self._loops:
            loop.task = self._spawn(self._run(loop), name=f"{MODULE_NAME}-loop")

    async def stop_inputs(self) -> None:
        """Cancel every loop: no event is built after this returns."""

        self._stopping = True
        await self._cancel_loops()

    async def close(self) -> None:
        if self._closed:
            return
        self._stopping = True
        await self._cancel_loops()
        self._closed = True

    async def _cancel_loops(self) -> None:
        tasks = []
        for loop in self._loops:
            task, loop.task = loop.task, None
            if task is not None and not task.done():
                task.cancel()
                tasks.append(task)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _spawn(self, coro: Awaitable[Any], *, name: str) -> Any:
        spawn = getattr(self._tasks, "spawn", None)
        if callable(spawn):
            return spawn(coro, name=name)
        return asyncio.ensure_future(coro)

    # -- the loop --------------------------------------------------------------- #

    async def _run(self, loop: _Loop) -> None:
        """Fire at ``t0 + k × interval`` until stopped; never from the last firing."""

        interval = loop.interval_seconds
        start = self._started_at
        due_index = 1
        while not self._stopping:
            now = self._clock()
            due = start + due_index * interval
            if now < due:
                await self._sleeper(due - now)
                continue
            await self._fire(loop)
            # One firing is considered however many due instants passed; the
            # schedule resumes at the next one after the clock, read again
            # because the firing itself awaits.
            due_index = max(due_index, int((self._clock() - start) // interval)) + 1

    async def _fire(self, loop: _Loop) -> None:
        """Build the event, hand it to the observer, decide it, admit it."""

        if self._stopping:
            return
        try:
            minute = self._local_minute()
        except Exception:  # noqa: BLE001 - an unreadable clock builds nothing
            minute = None
        if not _is_int(minute) or not 0 <= minute < MINUTES_PER_DAY:
            self._diagnose("fire: local minute unreadable")
            loop.counts[COUNT_NOT_ADMITTED] += 1
            return
        loop.sequence += 1
        loop.counts[COUNT_FIRED] += 1
        event = timer_event(loop.channel, loop.sequence, minute)
        hook = self._on_fire
        if hook is not None:
            try:
                hook(event)
            except Exception:  # noqa: BLE001 - a failed observer must not stop the loop
                self._diagnose("fire: hand-off failed")
        engine = self._triggers
        if engine is None:
            # Nothing decides, so nothing is admitted: fail closed.
            loop.counts[COUNT_NOT_ADMITTED] += 1
            return
        try:
            decision = engine.evaluate(event)
        except Exception:  # noqa: BLE001 - an undecided event is never admitted
            self._diagnose("fire: trigger evaluation failed")
            loop.counts[COUNT_NOT_ADMITTED] += 1
            return
        await self._trace_decision(decision)
        if not getattr(decision, "accepted", False) or self._stopping:
            loop.counts[COUNT_NOT_ADMITTED] += 1
            return
        self._admit(loop, event)

    def _admit(self, loop: _Loop, event: Mapping[str, Any]) -> None:
        """Hand the accepted event to the scheduler."""

        scheduler = self._admission_target()
        if scheduler is None:
            self._diagnose("fire: no scheduler to admit to")
            loop.counts[COUNT_NOT_ADMITTED] += 1
            return
        try:
            result = scheduler.admit(
                loop.session_key,
                Work(
                    payload=event,
                    source_event_id=event["payload"]["message_id"],
                    kind=WORK_KIND,
                ),
            )
        except Exception:  # noqa: BLE001 - a closed scheduler must not stop the loop
            self._diagnose("fire: admission failed")
            loop.counts[COUNT_NOT_ADMITTED] += 1
            return
        if getattr(result, "accepted", False) is not True:
            # A refusal is the scheduler's own traced saturation decision.
            loop.counts[COUNT_NOT_ADMITTED] += 1
            return
        loop.counts[COUNT_ADMITTED] += 1

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

    async def _trace_decision(self, decision: Any) -> None:
        emit = getattr(self._supervision, "emit", None)
        if not callable(emit) or not getattr(decision, "traceable", True):
            return
        event_type = (
            TRACE_INPUT_TRIGGER_ACCEPTED if decision.accepted else TRACE_INPUT_TRIGGER_REJECTED
        )
        try:
            outcome = emit(event_type, decision_trace_payload(decision))
            if inspect.isawaitable(outcome):
                await outcome
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a lost trace changes nothing decided
            self._diagnose("fire: trigger trace not published")

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
        except Exception:  # noqa: BLE001 - a health report must not mask the failure
            return

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
) -> TimerModule:
    """Validate the settings and build the handle; nothing starts here."""

    del catalog  # Nothing is declared from the catalog.
    if not isinstance(settings, Mapping):
        raise TimerModuleError(f"{MODULE_NAME} configuration: settings must be a mapping")
    diagnostics = validate_settings(settings)
    if diagnostics:
        raise TimerModuleError(
            f"{MODULE_NAME} configuration: settings were refused "
            f"({len(diagnostics)} diagnostics)"
        )
    return TimerModule(
        context,
        TimerSettings.from_mapping(settings),
        sleeper=settings.get(_SEAM_SLEEPER, asyncio.sleep),
        local_minute=settings.get(_SEAM_LOCAL_MINUTE, _local_minute_of_day),
        on_fire=settings.get(_SEAM_ON_FIRE),
    )


__all__ = [
    "ADMISSION_SERVICE_KIND",
    "ADMISSION_SERVICE_SCOPE",
    "AUTHOR_PREFIX",
    "COUNT_ADMITTED",
    "COUNT_FIRED",
    "COUNT_NOT_ADMITTED",
    "INTERVAL_TYPE",
    "MAX_CHANNELS",
    "MODULE_NAME",
    "PROMPT_MAX_CHARS",
    "TIMER_EVENT_TYPE",
    "TIMER_KIND",
    "TimerChannel",
    "TimerModule",
    "TimerModuleError",
    "TimerSettings",
    "WORK_KIND",
    "activate",
    "timer_event",
    "validate_settings",
]
