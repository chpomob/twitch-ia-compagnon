"""``stream.clip.create`` — live clips through each platform's ``clip`` service (R2).

A platform module that can make clips publishes a ``clip`` service under
``(clip, <platform>)`` in the runtime service registry. The service has two
coroutines: ``create(channel_id)`` sends one create request and returns a
classified answer — an object whose ``outcome`` is ``accepted`` (with
``clip_id``), ``offline``, ``rejected``, ``rate`` or ``uncertain`` — and
``lookup(clip_id)`` answers the clip's URL, ``None`` while the platform does
not list it, or raises when its answer is lost. The answer is read by its
attributes only, so no platform class is imported here.

**Binding** (R2). ``prepare`` reads ``context.services.entries()``: every
platform named there is enabled, and the action is bound over
``<platform>/*/clip`` for each one whose ``clip`` service resolves. Every
other enabled platform is unbound with reason :data:`REASON_PLATFORM_UNSUPPORTED`
(:attr:`ClipsModule.unbound`, reported once through ``module.degraded``). When
no platform has one, ``required: true`` fails ``prepare`` naming ``clips`` and
``platform_unsupported`` (the reason is reported first on ``module.degraded``,
since the coordinator's failure carries the module name only); otherwise the
action is only declared and startup continues.

**One call, per channel** (R2). A call on ``(platform, channel_id)``:

1. takes its place on the channel — the call in flight plus at most
   ``max_waiters`` waiting; one more is ``refused resource_busy``;
2. waits for the channel's lock, bounded by the call deadline and the drain;
3. checks the cooldown **after** taking the lock, against the instant of the
   channel's last create request: earlier than ``min_interval_seconds`` is
   ``refused cooldown`` with 0 requests;
4. declares the effect emitted, stamps the cooldown and sends exactly one
   create request — never retried;
5. classifies the answer: ``offline`` → ``error channel_offline``,
   ``rejected`` → ``error platform_rejected``, ``rate`` → ``error
   rate_limited``, ``uncertain``, a raised or unreadable answer →
   ``external_unknown`` with cause ``confirmation_lost``;
6. on ``accepted``, confirms the clip by lookups (plan decision 8): the
   first at once, then every 1 s on the injected clock, none after
   ``acceptance + 15 s``. A URL found is ``success {clip_id, url,
   confirmed_at}``; the window elapsed with the last lookup answering
   ``None`` is ``error clip_not_created``; the last lookup's answer lost is
   ``external_unknown`` (cause ``confirmation_lost``).

**The call deadline, nothing earlier** (phase 2 R10). A call computes
``expiry = min(call.deadline, clock() + spec.timeout_seconds)`` — the
executor's own arithmetic; nothing is subtracted from it. Reached before the
create request left, the call ends ``timeout`` with 0 requests; reached after
it, the module's own ``timeout`` record (cause ``confirmation_lost``), which
the executor's emission rule resolves ``external_unknown``. Every wait —
the lock, a request, the pause between lookups — is raced against it on the
injected sleeper, so no call holds the lock past its deadline.

**Drain** ends every call that has not sent its create request ``cancelled``
(0 requests) and gives a confirmation in progress the drain deadline, on the
clock; past it the call ends ``external_unknown`` and no further lookup
starts. ``close`` withdraws readiness and ends what still runs.

**Traces.** The executor's ``action.completed`` trace carries the status and
the code of every call; a success's ``clip_id`` is in its result. Nothing this
module returns names a credential: the service's classes carry none.

**Seams, for the tests.** ``_sleeper`` (the sleep every bound is raced
against, ``asyncio.sleep`` by default) is read from *settings* at
``activate`` and never from a configuration file.
"""

from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from core.actions import (
    ERROR_CANCELLED,
    ERROR_EXTERNAL_UNKNOWN,
    ERROR_TIMED_OUT,
    PROVENANCE_TRACE_FIELDS,
)
from core.contracts import ActionObservation, ActionSpec, Destination


MODULE_NAME = "clips"

CLIP_ACTION = "stream.clip.create"
PROVIDER_NAME = "clips"

#: The manifest the spec is rebuilt from at ``prepare``.
MANIFEST_PATH = Path(__file__).with_name("module.yaml")

#: The service kind a platform publishes its clip service under (R2). The
#: name lives in this module and in the publishers only: the core defines none.
CLIP_SERVICE_KIND = "clip"
_CLIP_METHODS = ("create", "lookup")

DEFAULT_MIN_INTERVAL_SECONDS = 30
MIN_INTERVAL_BOUNDS = (5, 3600)
DEFAULT_MAX_WAITERS = 4
MAX_WAITERS_BOUNDS = (1, 64)
DEFAULT_REQUIRED = False

#: How long after the platform accepted the create request the clip may be
#: looked up, and the pause between two lookups (plan decision 8).
CONFIRMATION_WINDOW_SECONDS = 15.0
LOOKUP_INTERVAL_SECONDS = 1.0

# The classified create answers a clip service returns.
CREATE_ACCEPTED = "accepted"
CREATE_OFFLINE = "offline"
CREATE_REJECTED = "rejected"
CREATE_RATE = "rate"
CREATE_UNCERTAIN = "uncertain"

ERROR_COOLDOWN = "cooldown"
ERROR_RESOURCE_BUSY = "resource_busy"
ERROR_CHANNEL_OFFLINE = "channel_offline"
ERROR_PLATFORM_REJECTED = "platform_rejected"
ERROR_RATE_LIMITED = "rate_limited"
ERROR_CLIP_NOT_CREATED = "clip_not_created"
ERROR_PLATFORM_UNSUPPORTED = "platform_unsupported"
_ERROR_PROVIDER_CLOSED = "provider_closed"

#: The cause of an uncertain outcome once the create request left.
CAUSE_CONFIRMATION_LOST = "confirmation_lost"

#: Why an enabled platform is unbound: it publishes no clip service (R2).
REASON_PLATFORM_UNSUPPORTED = "platform_unsupported"

_REFUSALS = {
    CREATE_OFFLINE: (ERROR_CHANNEL_OFFLINE, "the channel is not live"),
    CREATE_REJECTED: (ERROR_PLATFORM_REJECTED, "the platform refused the create request"),
    CREATE_RATE: (ERROR_RATE_LIMITED, "the platform refused the create request for its rate"),
}

# Setting names — referenced by name, never by value, in diagnostics.
_ACCEPTED_LIMITS_SETTING = "limits"
_SETTING_MIN_INTERVAL = "min_interval_seconds"
_SETTING_MAX_WAITERS = "max_waiters"
_SETTING_REQUIRED = "required"
_SETTINGS = frozenset({_SETTING_MIN_INTERVAL, _SETTING_MAX_WAITERS, _SETTING_REQUIRED})

_SEAM_SLEEPER = "_sleeper"
_SEAMS = frozenset({_SEAM_SLEEPER})

Sleeper = Callable[[float], Awaitable[Any]]


class ClipsModuleError(RuntimeError):
    """A setup failure whose message contains no configured value."""


class _CallFailure(Exception):
    """One explicit non-success outcome of a call, raised to its invoke."""

    def __init__(self, status: str, code: str, message: str, **details: Any) -> None:
        super().__init__(code)
        self.status = status
        self.code = code
        self.message = message
        self.details = details


# --------------------------------------------------------------------------- #
# Settings validation hook
# --------------------------------------------------------------------------- #


def validate_settings(settings: Any) -> list[str]:
    """Check this module's settings; return one diagnostic per offending field.

    This is the hook ``settings_validator`` in the manifest names. Each
    diagnostic names the module and the field and nothing else; an empty list
    means the settings are accepted. ``min_interval_seconds`` is an integer in
    5..3600, ``max_waiters`` an integer in 1..64, ``required`` a boolean — a
    string such as ``"yes"`` is refused, never read as true. The reserved
    ``limits`` block is accepted and not inspected; any other key is refused
    by name.
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
    for field_name, (low, high) in (
        (_SETTING_MIN_INTERVAL, MIN_INTERVAL_BOUNDS),
        (_SETTING_MAX_WAITERS, MAX_WAITERS_BOUNDS),
    ):
        if field_name in settings and not _is_int_within(settings[field_name], low, high):
            diagnostics.append(
                _setting_diagnostic(field_name, f"must be an integer from {low} to {high}")
            )
    if _SETTING_REQUIRED in settings and not isinstance(settings[_SETTING_REQUIRED], bool):
        diagnostics.append(_setting_diagnostic(_SETTING_REQUIRED, "must be a boolean"))
    return diagnostics


def _setting_diagnostic(field_name: str, reason: str) -> str:
    return f"module {MODULE_NAME!r}: field {field_name!r}: {reason}"


def _is_int_within(value: Any, low: int, high: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high


@dataclass(frozen=True, slots=True)
class _Settings:
    """The accepted settings, parsed once."""

    min_interval_seconds: int
    max_waiters: int
    required: bool

    @classmethod
    def from_mapping(cls, settings: Mapping[str, Any]) -> "_Settings":
        return cls(
            min_interval_seconds=int(
                settings.get(_SETTING_MIN_INTERVAL, DEFAULT_MIN_INTERVAL_SECONDS)
            ),
            max_waiters=int(settings.get(_SETTING_MAX_WAITERS, DEFAULT_MAX_WAITERS)),
            required=bool(settings.get(_SETTING_REQUIRED, DEFAULT_REQUIRED)),
        )


# --------------------------------------------------------------------------- #
# Per-channel serialization
# --------------------------------------------------------------------------- #


class _Slot:
    """One channel: ``lock`` is held by the call in flight, from before its
    cooldown check until its outcome is known; ``occupants`` counts every
    admitted call — holding the lock or waiting for it."""

    __slots__ = ("lock", "occupants")

    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.occupants = 0


class _Claim:
    """One call's place on a :class:`_Slot`; released on every exit path."""

    __slots__ = ("counted", "owns_lock", "slot")

    def __init__(self, slot: _Slot) -> None:
        self.slot = slot
        self.counted = True
        self.owns_lock = False
        slot.occupants += 1

    def release(self) -> None:
        if self.owns_lock:
            self.owns_lock = False
            self.slot.lock.release()
        if self.counted:
            self.counted = False
            self.slot.occupants -= 1


class _Failed:
    """A service request that raised or was cancelled: its answer is lost."""

    __slots__ = ()


_FAILED = _Failed()


class _ClipActionProvider:
    """The provider bound to ``stream.clip.create``: hands each call to the module."""

    __slots__ = ("_module",)

    name = PROVIDER_NAME

    def __init__(self, module: "ClipsModule") -> None:
        self._module = module

    async def invoke(self, invocation: Any) -> ActionObservation:
        return await self._module._invoke(invocation)


# --------------------------------------------------------------------------- #
# The handle
# --------------------------------------------------------------------------- #


class ClipsModule:
    """The v2 handle: resolve and bind at ``prepare``, create clips on demand."""

    def __init__(self, context: Any, settings: _Settings, *, sleeper: Sleeper) -> None:
        self._actions = context.actions
        self._supervision = getattr(context, "supervision", None)
        # The module's service facade; only ``entries()`` and ``resolve()``
        # are ever called on it.
        self._services = getattr(context, "services", None)
        self._clock: Callable[[], float] = getattr(context, "clock", None) or time.monotonic
        self._settings = settings
        self._sleeper = sleeper
        self._provider = _ClipActionProvider(self)
        # ``platform → clip service``, resolved once at ``prepare``.
        self._clip_services: dict[str, Any] = {}
        # ``platform → reason`` for every enabled platform left unbound.
        self._unbound: dict[str, str] = {}
        # One slot per ``(platform, channel_id)`` with a call admitted on it;
        # dropped when its last call left.
        self._slots: dict[tuple[str, str], _Slot] = {}
        # The instant of each channel's last create request, on the clock.
        # One entry per channel that ever sent one: bounded by the channels
        # the bound platforms serve; an entry older than the interval is
        # dropped when a new request is stamped.
        self._last_create: dict[tuple[str, str], float] = {}
        loop = asyncio.get_running_loop()
        # Resolved when ``drain`` begins: a call that has not sent its create
        # request ends ``cancelled``.
        self._drain_started: asyncio.Future[None] = loop.create_future()
        # Resolved when the drain deadline passed: a call whose create request
        # left ends ``external_unknown`` and starts no further lookup.
        self._drain_expired: asyncio.Future[None] = loop.create_future()
        # One future per running call, resolved when it returned.
        self._calls: set[asyncio.Future[None]] = set()
        self._prepared = False
        self._draining = False
        self._closed = False

    @property
    def settings(self) -> _Settings:
        return self._settings

    @property
    def bound_platforms(self) -> tuple[str, ...]:
        """The platforms whose clip service resolved at ``prepare``."""

        return tuple(sorted(self._clip_services))

    @property
    def unbound(self) -> Mapping[str, str]:
        """``platform → reason`` for every enabled platform left unbound."""

        return dict(sorted(self._unbound.items()))

    def last_create_at(self, platform: str, channel_id: str) -> float | None:
        """The instant of the channel's last create request, if any."""

        return self._last_create.get((platform, channel_id))

    # -- lifecycle hooks ---------------------------------------------------- #

    async def prepare(self) -> None:
        """Resolve the clip services, apply the ``required`` policy, bind."""

        if self._prepared or self._closed:
            return
        enabled, services = self._resolve_clip_services()
        unbound = {
            platform: REASON_PLATFORM_UNSUPPORTED
            for platform in sorted(enabled - set(services))
        }
        if not services and self._settings.required:
            # The coordinator's failure names the module only: the reason is
            # reported first, value-free, on the module's own health trace.
            await self._report_degraded(sorted(unbound))
            raise ClipsModuleError(
                f"{MODULE_NAME} prepare: {REASON_PLATFORM_UNSUPPORTED}: "
                "no enabled platform publishes a clip service"
            )
        spec = _declared_spec()
        try:
            if services:
                self._actions.register(
                    spec,
                    self._provider,
                    destinations=[
                        Destination(platform, "*", CLIP_SERVICE_KIND)
                        for platform in sorted(services)
                    ],
                    provider_name=PROVIDER_NAME,
                )
            else:
                self._actions.declare(spec)
        except ClipsModuleError:
            raise
        except Exception:
            raise ClipsModuleError(f"{MODULE_NAME} prepare: action binding failed") from None
        self._clip_services = services
        self._unbound = unbound
        self._prepared = True
        if unbound or not services:
            await self._report_degraded(sorted(unbound))
        if services:
            self._actions.mark_ready()

    def _resolve_clip_services(self) -> tuple[set[str], dict[str, Any]]:
        """The enabled platforms, and ``platform → service`` for those with one.

        A platform is enabled when it published any service; its ``(clip,
        <platform>)`` entry is resolved and kept when it has the two methods.
        Reads ``entries()`` and ``resolve()`` of the service facade and
        nothing else; a context without a registry hands out an empty view.
        """

        facade = self._services
        entries = getattr(facade, "entries", None)
        resolve = getattr(facade, "resolve", None)
        if not callable(entries) or not callable(resolve):
            return set(), {}
        enabled: set[str] = set()
        resolved: dict[str, Any] = {}
        for key in list(entries()):
            if not isinstance(key, tuple) or len(key) != 2:
                continue
            kind, platform = key
            if not isinstance(platform, str) or not platform:
                continue
            enabled.add(platform)
            if kind != CLIP_SERVICE_KIND:
                continue
            service = resolve(kind, platform)
            if service is not None and all(
                callable(getattr(service, method, None)) for method in _CLIP_METHODS
            ):
                resolved[platform] = service
        return enabled, resolved

    async def drain(self, deadline_seconds: float) -> None:
        """End waiting calls, give a confirmation in progress the drain deadline.

        Every call that has not sent its create request ends ``cancelled`` at
        once, with 0 requests. A call whose request left keeps confirming
        until *deadline_seconds* passed on the clock; past it the call ends
        ``external_unknown`` and starts no further lookup.
        """

        self._draining = True
        _resolve(self._drain_started)
        try:
            await self._join_calls(max(float(deadline_seconds), 0.0))
        finally:
            # Whatever ended the join — the budget, or the caller giving up on
            # the drain — no call may outlive it waiting on a confirmation.
            if self._pending_calls():
                _resolve(self._drain_expired)

    async def close(self) -> None:
        """Withdraw readiness and end what still runs."""

        if self._closed:
            return
        self._closed = True
        self._draining = True
        if self._prepared:
            self._actions.mark_not_ready()
        _resolve(self._drain_started)
        _resolve(self._drain_expired)
        pending = self._pending_calls()
        if pending:
            await asyncio.wait(pending)

    def _pending_calls(self) -> set[asyncio.Future[None]]:
        return {call for call in self._calls if not call.done()}

    async def _join_calls(self, budget: float) -> None:
        pending = self._pending_calls()
        if not pending:
            return
        timer = asyncio.ensure_future(self._sleeper(budget))
        try:
            while pending and not timer.done():
                await asyncio.wait(pending | {timer}, return_when=asyncio.FIRST_COMPLETED)
                pending = self._pending_calls()
            if pending:
                # The drain deadline passed first: the calls still confirming
                # end ``external_unknown`` now, on this turn.
                _resolve(self._drain_expired)
                await asyncio.wait(pending)
        finally:
            await _settle(timer)

    async def _report_degraded(self, platforms: Sequence[str]) -> None:
        degraded = getattr(self._supervision, "degraded", None)
        if not callable(degraded):
            return
        try:
            outcome = degraded(
                reason=REASON_PLATFORM_UNSUPPORTED,
                capabilities=[CLIP_ACTION],
                platforms=list(platforms),
            )
            if inspect.isawaitable(outcome):
                await outcome
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a health report must not fail prepare
            return

    # -- stream.clip.create ------------------------------------------------- #

    async def _invoke(self, invocation: Any) -> ActionObservation:
        """Serve one ``stream.clip.create`` call for the executor."""

        # Nothing has left before the create request: an interruption until
        # then is a certain ``timeout``/``cancelled``.
        invocation.mark_not_emitted()
        expiry = self._expiry(invocation)
        destination = invocation.call.destination
        provenance = _provenance(destination)
        running: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._calls.add(running)
        claim: _Claim | None = None
        key = (str(destination.platform), str(destination.channel_id))
        try:
            self._check_open()
            service = self._clip_services.get(key[0])
            if service is None:
                raise _CallFailure(
                    "error",
                    ERROR_PLATFORM_UNSUPPORTED,
                    "no clip service is published for this platform",
                )
            claim = self._claim(key)
            await self._acquire(claim, expiry)
            # Checked under the lock: a call released after another one sent
            # its request sees that request's stamp (R2, AC11).
            self._check_cooldown(key)
            self._check_may_continue(expiry, emitted=False)
            # From here a clip may be made: a lost answer is uncertain.
            invocation.mark_emitted()
            self._stamp(key)
            return await self._create(service, key, expiry, provenance)
        except _CallFailure as failure:
            return _failure_observation(provenance, failure)
        finally:
            if claim is not None:
                claim.release()
                if claim.slot.occupants == 0 and self._slots.get(key) is claim.slot:
                    del self._slots[key]
            self._calls.discard(running)
            _resolve(running)

    def _check_cooldown(self, key: tuple[str, str]) -> None:
        last = self._last_create.get(key)
        if last is None:
            return
        if self._clock() - last < self._settings.min_interval_seconds:
            raise _CallFailure(
                "refused",
                ERROR_COOLDOWN,
                "a clip was requested on this channel less than the minimum interval ago",
            )

    def _stamp(self, key: tuple[str, str]) -> None:
        now = self._clock()
        interval = self._settings.min_interval_seconds
        # Entries past their interval no longer refuse anything: dropped, so
        # the table holds only channels still cooling down.
        for other in [k for k, at in self._last_create.items() if now - at >= interval]:
            del self._last_create[other]
        self._last_create[key] = now

    async def _create(
        self,
        service: Any,
        key: tuple[str, str],
        expiry: float,
        provenance: Mapping[str, Any],
    ) -> ActionObservation:
        """One create request, classified; on acceptance, the confirmation."""

        answer = await self._request(service.create(key[1]), expiry, emitted=True)
        outcome = getattr(answer, "outcome", None)
        if outcome in _REFUSALS:
            code, message = _REFUSALS[outcome]
            raise _CallFailure("error", code, message)
        clip_id = getattr(answer, "clip_id", None)
        if outcome != CREATE_ACCEPTED or not isinstance(clip_id, str) or not clip_id:
            raise _uncertain("the create request left and its answer is lost")
        return await self._confirm(service, clip_id, expiry, provenance)

    async def _confirm(
        self,
        service: Any,
        clip_id: str,
        expiry: float,
        provenance: Mapping[str, Any],
    ) -> ActionObservation:
        """Look the clip up at once, then every second, until the window closes.

        No lookup starts after ``acceptance + 15 s``; the last one is issued at
        that instant at the latest, so ``clip_not_created`` is only concluded
        once the whole window elapsed. A lookup answered, or a pause resumed,
        past the window's end starts no further one: the call concludes on the
        last answer it has.
        """

        accepted_at = self._clock()
        window_end = accepted_at + CONFIRMATION_WINDOW_SECONDS
        next_at = accepted_at
        url: Any = None
        while True:
            self._check_may_continue(expiry, emitted=True)
            issued_at = self._clock()
            if issued_at > window_end:
                _conclude_unconfirmed(url)
            url = await self._request(service.lookup(clip_id), expiry, emitted=True)
            if isinstance(url, str) and url:
                return ActionObservation(
                    status="success",
                    provenance={
                        **provenance,
                        PROVENANCE_TRACE_FIELDS: {"clip_id": clip_id},
                    },
                    result={
                        "clip_id": clip_id,
                        "url": url,
                        "confirmed_at": float(self._clock()),
                    },
                )
            if issued_at >= window_end:
                _conclude_unconfirmed(url)
            next_at = min(max(next_at + LOOKUP_INTERVAL_SECONDS, self._clock()), window_end)
            await self._pause_until(next_at, expiry)

    def _check_may_continue(self, expiry: float, *, emitted: bool) -> None:
        """Refuse to start the next request once interrupted.

        Before the create request the drain ends the call ``cancelled``; after
        it the drain deadline ends it ``external_unknown``. Past *expiry*
        either way the call ends ``timeout``.
        """

        if emitted:
            if self._drain_expired.done():
                raise _uncertain("the drain deadline passed before the clip was confirmed")
        elif self._drain_started.done():
            raise _CallFailure("cancelled", ERROR_CANCELLED, f"{MODULE_NAME}: shutting down")
        if self._clock() >= expiry:
            raise _deadline_failure(emitted=emitted)

    async def _request(self, work: Awaitable[Any], expiry: float, *, emitted: bool) -> Any:
        """Await one service request, bounded by *expiry* on the sleeper.

        Returns the answer, or :data:`_FAILED` when the request raised. A
        request still pending at the deadline or the drain deadline, or when
        the call is cancelled, is abandoned and the call ends with the
        matching interruption record.
        """

        task = asyncio.ensure_future(work)
        watcher = asyncio.ensure_future(self._sleeper(max(expiry - self._clock(), 0.0)))
        interrupt = self._drain_expired if emitted else self._drain_started
        try:
            await asyncio.wait({task, watcher, interrupt}, return_when=asyncio.FIRST_COMPLETED)
        except asyncio.CancelledError:
            _abandon(task)
            _abandon(watcher)
            raise self._cancellation(expiry, emitted=emitted) from None
        _abandon(watcher)
        if task.done():
            if task.cancelled() or task.exception() is not None:
                return _FAILED
            return task.result()
        _abandon(task)
        if interrupt.done():
            raise _uncertain("the drain deadline passed before the clip was confirmed")
        raise _deadline_failure(emitted=emitted)

    async def _pause_until(self, instant: float, expiry: float) -> None:
        """Wait until *instant* on the clock, unless the deadline or the drain
        deadline comes first — then the call ends with its interruption."""

        delay = min(instant, expiry) - self._clock()
        if delay > 0:
            timer = asyncio.ensure_future(self._sleeper(delay))
            try:
                await asyncio.wait(
                    {timer, self._drain_expired}, return_when=asyncio.FIRST_COMPLETED
                )
            except asyncio.CancelledError:
                _abandon(timer)
                raise self._cancellation(expiry, emitted=True) from None
            _abandon(timer)
        self._check_may_continue(expiry, emitted=True)

    def _cancellation(self, expiry: float, *, emitted: bool) -> _CallFailure:
        """The record a cancellation reaching the call stands for."""

        if self._clock() >= expiry:
            return _deadline_failure(emitted=emitted)
        details = {"cause": CAUSE_CONFIRMATION_LOST} if emitted else {}
        return _CallFailure("cancelled", ERROR_CANCELLED, "the call was cancelled", **details)

    async def _acquire(self, claim: _Claim, expiry: float) -> None:
        """Wait for the call in flight, unless the drain or the deadline comes first."""

        lock = claim.slot.lock
        acquiring = asyncio.ensure_future(lock.acquire())
        watcher = asyncio.ensure_future(self._sleeper(max(expiry - self._clock(), 0.0)))
        try:
            await asyncio.wait(
                {acquiring, watcher, self._drain_started}, return_when=asyncio.FIRST_COMPLETED
            )
        except asyncio.CancelledError:
            _abandon_acquire(acquiring, lock)
            _abandon(watcher)
            raise self._cancellation(expiry, emitted=False) from None
        _abandon(watcher)
        if acquiring.done() and not acquiring.cancelled() and acquiring.exception() is None:
            claim.owns_lock = True
            if self._drain_started.done():
                raise _CallFailure("cancelled", ERROR_CANCELLED, f"{MODULE_NAME}: shutting down")
            return
        _abandon_acquire(acquiring, lock)
        if self._drain_started.done():
            raise _CallFailure("cancelled", ERROR_CANCELLED, f"{MODULE_NAME}: shutting down")
        raise _deadline_failure(emitted=False)

    def _expiry(self, invocation: Any) -> float:
        """``min(call.deadline, clock() + spec.timeout_seconds)`` — nothing subtracted."""

        return min(
            float(invocation.call.deadline),
            self._clock() + float(invocation.spec.timeout_seconds),
        )

    def _check_open(self) -> None:
        if self._closed:
            raise _CallFailure("error", _ERROR_PROVIDER_CLOSED, f"{MODULE_NAME}: closed")
        if self._draining:
            raise _CallFailure("cancelled", ERROR_CANCELLED, f"{MODULE_NAME}: shutting down")

    def _claim(self, key: tuple[str, str]) -> _Claim:
        """Take a place on the channel: the call in flight plus at most
        ``max_waiters`` waiting, or ``refused resource_busy``."""

        slot = self._slots.get(key)
        if slot is None:
            slot = self._slots[key] = _Slot()
        if slot.occupants > 0 and slot.occupants - 1 >= self._settings.max_waiters:
            raise _CallFailure(
                "refused",
                ERROR_RESOURCE_BUSY,
                "a clip is in progress on this channel and its waiting line is full",
            )
        return _Claim(slot)


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #


def _deadline_failure(*, emitted: bool) -> _CallFailure:
    if emitted:
        return _CallFailure(
            "timeout",
            ERROR_TIMED_OUT,
            "the call deadline was reached before the clip was confirmed",
            cause=CAUSE_CONFIRMATION_LOST,
        )
    return _CallFailure(
        "timeout", ERROR_TIMED_OUT, "the call deadline was reached before the clip was requested"
    )


def _uncertain(message: str) -> _CallFailure:
    return _CallFailure(
        "external_unknown", ERROR_EXTERNAL_UNKNOWN, message, cause=CAUSE_CONFIRMATION_LOST
    )


def _resolve(future: "asyncio.Future[None]") -> None:
    if not future.done():
        future.set_result(None)


def _abandon(task: "asyncio.Future[Any]") -> None:
    """Cancel *task* without waiting for it; its outcome is consumed when it ends."""

    if not task.done():
        task.cancel()
    task.add_done_callback(_consume)


def _consume(task: "asyncio.Future[Any]") -> None:
    if not task.cancelled():
        task.exception()


def _abandon_acquire(acquiring: "asyncio.Future[Any]", lock: asyncio.Lock) -> None:
    """Give up a lock acquisition; a lock it took anyway is released at once."""

    def _release_if_taken(task: "asyncio.Future[Any]") -> None:
        if not task.cancelled() and task.exception() is None:
            lock.release()

    if acquiring.done():
        _release_if_taken(acquiring)
        return
    acquiring.cancel()
    acquiring.add_done_callback(_release_if_taken)


async def _settle(task: "asyncio.Future[Any]") -> None:
    """Cancel *task* if still running and wait for it, consuming its outcome."""

    if not task.done():
        task.cancel()
    await asyncio.wait({task})
    if not task.cancelled():
        task.exception()


def _provenance(destination: Any) -> dict[str, Any]:
    return {
        "provider": PROVIDER_NAME,
        "platform": destination.platform,
        "channel_id": destination.channel_id,
        "route": CLIP_ACTION,
    }


def _conclude_unconfirmed(answer: Any) -> None:
    """End a confirmation whose window closed on *answer*, the last lookup's.

    ``None`` — the platform answered and did not list the clip — is
    ``clip_not_created``; a lost answer leaves the clip's fate unknown.
    """

    if answer is None:
        raise _CallFailure(
            "error",
            ERROR_CLIP_NOT_CREATED,
            "the platform did not list the clip within the confirmation window",
        )
    raise _uncertain("the clip could not be confirmed within the window")


def _failure_observation(provenance: Mapping[str, Any], failure: _CallFailure) -> ActionObservation:
    error: dict[str, Any] = {
        "code": failure.code,
        "message": failure.message,
        "retryable": False,
    }
    error.update(failure.details)
    return ActionObservation(status=failure.status, provenance=provenance, error=error)


# --------------------------------------------------------------------------- #
# Activation
# --------------------------------------------------------------------------- #


async def activate(
    context: Any,
    settings: Mapping[str, Any],
    catalog: Mapping[str, Mapping[str, Any]],
) -> ClipsModule:
    """Build the handle from the scoped runtime context.

    Settings are checked through the same hook the loader ran, so a handle
    built outside the loader is refused on the same terms. The ``_sleeper``
    seam is read from *settings* (``asyncio.sleep`` by default). Nothing is
    resolved or bound here: the platforms publish their services at their
    own activation, which ``prepare`` follows.
    """

    del catalog  # Capabilities are declared by the colocated manifest.
    actions = getattr(context, "actions", None)
    if actions is None or not all(
        callable(getattr(actions, method, None))
        for method in ("declare", "register", "mark_ready", "mark_not_ready")
    ):
        raise ClipsModuleError(f"{MODULE_NAME} activation: runtime context is invalid")
    if not isinstance(settings, Mapping):
        raise ClipsModuleError(f"{MODULE_NAME} configuration: settings must be a mapping")
    diagnostics = validate_settings(settings)
    if diagnostics:
        raise ClipsModuleError(
            f"{MODULE_NAME} configuration: settings were refused "
            f"({len(diagnostics)} diagnostics)"
        )
    return ClipsModule(
        context,
        _Settings.from_mapping(settings),
        sleeper=settings.get(_SEAM_SLEEPER, asyncio.sleep),
    )


def _declared_spec() -> ActionSpec:
    """Build ``stream.clip.create``'s contract from the colocated manifest.

    The spec registered at ``prepare`` must equal the one the loader declared
    at discovery, field for field: reading the same file keeps the two from
    drifting, and the registry refuses a redeclaration that differs.
    """

    try:
        manifest = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))
        entry = next(
            item
            for item in manifest["actions"]
            if isinstance(item, Mapping) and item.get("name") == CLIP_ACTION
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
        raise ClipsModuleError(
            f"{MODULE_NAME} prepare: manifest declaration of {CLIP_ACTION!r} is invalid"
        ) from None


__all__ = [
    "CAUSE_CONFIRMATION_LOST",
    "CLIP_ACTION",
    "CLIP_SERVICE_KIND",
    "CONFIRMATION_WINDOW_SECONDS",
    "CREATE_ACCEPTED",
    "CREATE_OFFLINE",
    "CREATE_RATE",
    "CREATE_REJECTED",
    "CREATE_UNCERTAIN",
    "DEFAULT_MAX_WAITERS",
    "DEFAULT_MIN_INTERVAL_SECONDS",
    "DEFAULT_REQUIRED",
    "ERROR_CHANNEL_OFFLINE",
    "ERROR_CLIP_NOT_CREATED",
    "ERROR_COOLDOWN",
    "ERROR_PLATFORM_REJECTED",
    "ERROR_PLATFORM_UNSUPPORTED",
    "ERROR_RATE_LIMITED",
    "ERROR_RESOURCE_BUSY",
    "LOOKUP_INTERVAL_SECONDS",
    "MANIFEST_PATH",
    "MAX_WAITERS_BOUNDS",
    "MIN_INTERVAL_BOUNDS",
    "MODULE_NAME",
    "PROVIDER_NAME",
    "REASON_PLATFORM_UNSUPPORTED",
    "ClipsModule",
    "ClipsModuleError",
    "activate",
    "validate_settings",
]
