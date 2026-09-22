"""``stream.scene.set`` and ``stream.poll.create`` (R6, R7, R8, R10).

The companion switches the stream's program scene through this module, and
opens platform polls. Which broadcast software answers a scene switch is the
business of the configured *scene provider* (``scenes.provider``), never of
the brain and never of a platform.

**A scene provider** is any object with the :class:`SceneProvider` surface:
``connected``, ``list_scenes()``, ``current_scene()``, ``set_scene(name)``
and ``close()``. ``set_scene`` answers ``{"result": True}`` when the provider
applied the scene, ``{"result": False, "code": 600}`` when it does not know
the scene and ``{"result": False, "code": <n>}`` for any other refusal; it
raises when its answer is lost. The ``kind`` of provider is configured:
``none`` binds nothing here (the action stays declared, so another module —
a proxy — may serve it), ``scripted`` takes the provider injected through the
``_scene_provider`` seam, and ``websocket`` is the network provider,
:class:`WebSocketSceneProvider` (below).

**One scene switch, confirmed or reconciled — never repeated** (R6). A scene
outside ``scenes.allowed`` is ``refused scene_not_allowed`` with the provider
uninvoked; a provider that is disconnected at call time answers ``error
provider_unavailable`` and the module withdraws its readiness
(``module.degraded``) — unless ``stream.poll.create`` is bound: readiness is
per module in the registry, so the poll action stays ready and the scene
call answers ``provider_unavailable`` on its own. Otherwise the call reads the current scene (the
``previous_scene``), declares the effect emitted and sends exactly one set
command: a provider that does not know the scene is ``error scene_unknown``
(nothing applied, no read-back), any other refusal ``error
scene_not_applied`` carrying the numeric code. After an applied set the
module reads the current scene back — equal is ``success`` with
``reconciled: false``, different is ``error scene_not_applied``. When the set
command's answer is lost, or the read-back fails, exactly one reconciliation
read follows within the time left: the scene current is ``success`` with
``reconciled: true``, anything else ``external_unknown`` with cause
``confirmation_lost``. A second set command is never sent in one call.

**The call deadline, nothing earlier** (R10, decision 1). At entry a call
computes ``expiry = min(call.deadline, clock() + spec.timeout_seconds)`` —
the executor's own arithmetic and the only deadline this module knows; no
quantity is subtracted from it. A provider request still pending when
``expiry`` is reached — noticed by the module's watcher on the injected
sleeper, or by the executor's cancellation arriving at or after ``expiry`` —
ends the call with the module's own ``timeout`` record (cause
``confirmation_lost`` once the set command left), which the executor's
emission rule resolves ``external_unknown``. A cancellation arriving earlier
is ``cancelled``, resolved by the same rule.

**One set command at a time per provider** (R6). A call waits behind the one
in flight; at most ``scenes.max_waiters`` calls wait, one more is ``refused
resource_busy``. ``drain`` ends every call that has not sent its set command
``cancelled`` (0 set commands) and gives a command already sent the drain
deadline, on the clock, to be confirmed; past it the call ends
``external_unknown`` and no further read is started.

**Readiness and the ``required`` policy** (R8, finding P1). ``prepare``
probes a ``scripted``/``websocket`` provider once (``list_scenes()``, bounded
on the sleeper): reachable binds ``stream.scene.set``; unreachable leaves it
unbound and reports ``module.degraded`` with a value-free reason, or — with
``required: true`` — fails ``prepare`` naming ``scenes.provider.url``.
``polls.enabled: true`` with no poll service resolved leaves
``stream.poll.create`` unbound with one ``module.degraded`` (reason ``"no
poll service published"``), or fails ``prepare`` naming ``polls.enabled``
under ``required: true`` — after the scene provider was closed, before any
poll binding. ``polls.enabled: false`` leaves it unbound with no trace and no
failure. The module is marked ready when at least one action is bound. The
URL and the password never appear in an observation, an error or a trace.

**Polls: one create per call, ever** (R7). At ``prepare`` the module reads
``context.services.entries()``, keeps the ``(poll, <platform>)`` keys and
resolves each — the only two calls it makes on the service facade — then
binds ``stream.poll.create`` once over ``<platform>/*/poll`` for every
platform whose service resolved. A call checks its arguments against the
``polls`` bounds (``error invalid_arguments``, 0 requests), takes its place
on the channel (one create in flight per ``(platform, channel_id)``, at most
``polls.max_waiters`` waiting, one more ``refused resource_busy``), and
consults the tracked table: an active poll is ``refused poll_active`` with 0
requests; an uncertain marker costs one ``get`` first (an active poll listed
→ ``refused poll_active``, none → proceed, ``get`` failing →
``external_unknown``). A table without room — the channel cap reached with
the channel untracked, or a channel's 33rd live entry — is ``refused
resource_busy`` with 0 requests: a live entry is never evicted, every entry
expires ``duration_seconds + 300`` s after it was recorded, on the clock.
Then the effect is declared emitted and exactly one create leaves: a poll
answered is ``success`` with ``reconciled: false``; 401/403 ``refused
platform_forbidden``; another 4xx ``error poll_rejected`` (the status code,
never the body); a request never sent ``error platform_unavailable``; a 5xx,
a lost answer, a malformed 2xx or no answer by the deadline cost one ``get``
within the time left — a poll with the same question and options (in order)
started at or after the call's start — both read as epoch seconds, the
call's start on the wall clock, never the monotonic one — is ``success``
with ``reconciled: true``; none after a 5xx is ``error platform_unavailable`` carrying the
status; anything else is ``external_unknown`` (cause ``confirmation_lost``)
and the channel is marked uncertain. The deadline, a cancellation or the
drain deadline reached once the create left marks the channel the same way.
A failure is told apart by its ``status``/``sent`` attributes, never by
importing a platform's classes.

**The ``websocket`` provider** speaks obs-websocket protocol 5 (RPC version
1), the protocol the reference streaming software serves, restricted to what
the boundary needs: the ``Hello`` → ``Identify`` → ``Identified`` handshake
with ``rpcVersion: 1`` and ``eventSubscriptions: 0`` (the authentication
string, when ``Hello`` asks for one, is derived from the password, the salt
and the challenge — the password itself is never sent), and three
``Request``/``RequestResponse`` pairs matched by ``requestId``:
``GetSceneList`` (the ``prepare`` probe), ``GetCurrentProgramScene`` and
``SetCurrentProgramScene``. A handshake that does not reach ``Identified``
with version 1 within ``connect_timeout_seconds`` — close code 4009, another
version, silence — is an unreachable provider; an answer missing within
``request_timeout_seconds`` — sending the request included — is a lost
answer. A reader task watches the socket from ``Identified`` until the socket
is lost or ``close``: it dispatches the answers and notices a loss while the
provider is idle. The provider owns it and ``close`` stops it, so it is not
one of the module's supervised tasks the shutdown drain would wait on. A lost
socket fails every pending request at once, marks
the provider disconnected — the module withdraws readiness
(``module.degraded``; readiness kept while the poll action is bound) — and starts a supervised reconnection loop: delays
``rng() × min(30, 1 × 2ⁿ)`` s on the sleeper, ``module.ready`` and the
action back in the ready view on the first success. ``close`` stops the loop
and the socket.

**Seams, for the tests.** ``_scene_provider`` (the ``scripted`` provider),
``_sleeper`` (the sleep every bound is raced against, ``asyncio.sleep`` by
default), ``_wall_clock`` (the epoch-seconds read a poll call's start is
compared with the platform's ``started_at`` on, ``time.time`` by default) and
``_websocket_factory`` (the connection factory of the ``websocket``
provider) are read from *settings* at ``activate`` and never
from a configuration file.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import inspect
import ipaddress
import json
import math
import random
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlsplit

import yaml

from core.actions import (
    ERROR_CANCELLED,
    ERROR_EXTERNAL_UNKNOWN,
    ERROR_INVALID_ARGUMENTS,
    ERROR_TIMED_OUT,
)
from core.contracts import ActionObservation, ActionSpec, Destination


MODULE_NAME = "stream_control"

SCENE_ACTION = "stream.scene.set"
POLL_ACTION = "stream.poll.create"
PROVIDER_NAME = "stream_control"

#: The manifest the specs are rebuilt from at ``prepare``.
MANIFEST_PATH = Path(__file__).with_name("module.yaml")

KIND_NONE = "none"
KIND_SCRIPTED = "scripted"
KIND_WEBSOCKET = "websocket"
SCENE_PROVIDER_KINDS = (KIND_NONE, KIND_SCRIPTED, KIND_WEBSOCKET)

DEFAULT_CONNECT_TIMEOUT_SECONDS = 5.0
DEFAULT_REQUEST_TIMEOUT_SECONDS = 5.0
DEFAULT_MAX_WAITERS = 1
MAX_SCENE_NAME_CHARS = 64

DEFAULT_POLLS_ENABLED = False
DEFAULT_MAX_QUESTION_CHARS = 60
DEFAULT_MIN_OPTIONS = 2
DEFAULT_MAX_OPTIONS = 5
DEFAULT_MAX_OPTION_CHARS = 25
DEFAULT_MIN_DURATION_SECONDS = 15
DEFAULT_MAX_DURATION_SECONDS = 1800
DEFAULT_MAX_TRACKED_CHANNELS = 256

#: The service kind a platform publishes its poll service under (R7). The
#: name lives in this module only: the core defines no kind.
POLL_SERVICE_KIND = "poll"
#: How long a tracked entry outlives its poll: TTL = duration + this (R7).
POLL_ENTRY_GRACE_SECONDS = 300
#: The most live entries (active polls or uncertain markers) one channel holds.
MAX_POLL_ENTRIES_PER_CHANNEL = 32
#: The poll state a platform lists for a running poll.
POLL_STATE_ACTIVE = "active"
_POLL_METHODS = ("create", "get")

#: The provider's "resource not found" status: the scene-unknown answer.
SCENE_UNKNOWN_CODE = 600

ERROR_SCENE_NOT_ALLOWED = "scene_not_allowed"
ERROR_SCENE_UNKNOWN = "scene_unknown"
ERROR_SCENE_NOT_APPLIED = "scene_not_applied"
ERROR_PROVIDER_UNAVAILABLE = "provider_unavailable"
ERROR_RESOURCE_BUSY = "resource_busy"
ERROR_POLL_ACTIVE = "poll_active"
ERROR_POLL_REJECTED = "poll_rejected"
ERROR_PLATFORM_FORBIDDEN = "platform_forbidden"
ERROR_PLATFORM_UNAVAILABLE = "platform_unavailable"
_ERROR_PROVIDER_CLOSED = "provider_closed"

#: The cause of an uncertain outcome once the set command left.
CAUSE_CONFIRMATION_LOST = "confirmation_lost"

#: The value-free reasons ``module.degraded`` carries (R8).
REASON_SCENE_PROVIDER_UNREACHABLE = f"{MODULE_NAME}: the scene provider is unreachable"
REASON_SCENE_PROVIDER_DISCONNECTED = f"{MODULE_NAME}: the scene provider is disconnected"
REASON_NO_POLL_SERVICE = "no poll service published"

# Setting names — referenced by name, never by value, in diagnostics.
_ACCEPTED_LIMITS_SETTING = "limits"
_SETTING_SCENES = "scenes"
_SETTING_POLLS = "polls"
_SETTING_REQUIRED = "required"
_SETTINGS = frozenset({_SETTING_SCENES, _SETTING_POLLS, _SETTING_REQUIRED})

_SCENES_FIELDS = frozenset({"provider", "allowed", "max_waiters"})
_PROVIDER_FIELDS = frozenset(
    {"kind", "url", "password", "connect_timeout_seconds", "request_timeout_seconds"}
)
_POLL_INT_FIELDS = (
    "max_question_chars",
    "min_options",
    "max_options",
    "max_option_chars",
    "min_duration_seconds",
    "max_duration_seconds",
    "max_tracked_channels",
)
_POLLS_FIELDS = frozenset({"enabled", "max_waiters", *_POLL_INT_FIELDS})

#: The dependency fields a failed ``prepare`` names under ``required: true``.
FIELD_SCENES_PROVIDER_URL = "scenes.provider.url"
FIELD_POLLS_ENABLED = "polls.enabled"

_SEAM_SCENE_PROVIDER = "_scene_provider"
_SEAM_SLEEPER = "_sleeper"
_SEAM_WEBSOCKET_FACTORY = "_websocket_factory"
_SEAM_WALL_CLOCK = "_wall_clock"
_SEAMS = frozenset(
    {_SEAM_SCENE_PROVIDER, _SEAM_SLEEPER, _SEAM_WEBSOCKET_FACTORY, _SEAM_WALL_CLOCK}
)

_PROVIDER_METHODS = ("list_scenes", "current_scene", "set_scene", "close")

_LOOPBACK_NAMES = frozenset({"localhost"})

Sleeper = Callable[[float], Awaitable[Any]]


class StreamControlModuleError(RuntimeError):
    """A setup failure whose message contains no configured value."""


@runtime_checkable
class SceneProvider(Protocol):
    """The scene provider boundary (R6).

    ``connected`` says whether a request can be sent now. ``list_scenes``
    returns the scene names (the ``prepare`` probe); ``current_scene`` the
    program scene; ``set_scene`` switches it and answers ``{"result": bool,
    "code": int}`` (see the module docstring), raising when its answer is
    lost; ``close`` releases the transport.
    """

    @property
    def connected(self) -> bool: ...

    async def list_scenes(self) -> list[str]: ...

    async def current_scene(self) -> str: ...

    async def set_scene(self, name: str) -> Mapping[str, Any]: ...

    async def close(self) -> None: ...


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
    diagnostic names the module and the field and nothing else — no URL or
    password is echoed; an empty list means the settings are accepted.

    ``scenes.provider.kind`` is one of ``none``, ``scripted``, ``websocket``
    (default ``none``); ``scripted`` needs the injected ``_scene_provider``;
    ``websocket`` needs a ``url`` that is ``ws://`` on a loopback host
    (``127.0.0.0/8``, ``::1``, ``localhost``) or ``wss://``. The password is
    a string, the timeouts finite positive numbers, ``scenes.allowed`` a list
    of 1..64-character names, the waiter bounds non-negative integers, the
    poll bounds positive integers with each minimum at most its maximum,
    ``polls.enabled`` and ``required`` booleans. The reserved ``limits``
    block is accepted and not inspected; any other key is refused by name.
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
    for seam in (_SEAM_SLEEPER, _SEAM_WEBSOCKET_FACTORY, _SEAM_WALL_CLOCK):
        if seam in settings and not callable(settings[seam]):
            diagnostics.append(_setting_diagnostic(seam, "must be callable"))
    if _SEAM_SCENE_PROVIDER in settings and not _is_scene_provider(
        settings[_SEAM_SCENE_PROVIDER]
    ):
        diagnostics.append(
            _setting_diagnostic(
                _SEAM_SCENE_PROVIDER,
                "must provide connected, list_scenes, current_scene, set_scene and close",
            )
        )

    diagnostics.extend(
        _scenes_diagnostics(
            settings.get(_SETTING_SCENES), has_seam=_SEAM_SCENE_PROVIDER in settings
        )
    )
    diagnostics.extend(_polls_diagnostics(settings.get(_SETTING_POLLS)))
    if _SETTING_REQUIRED in settings and not isinstance(settings[_SETTING_REQUIRED], bool):
        diagnostics.append(_setting_diagnostic(_SETTING_REQUIRED, "must be a boolean"))
    return diagnostics


def _scenes_diagnostics(scenes: Any, *, has_seam: bool) -> list[str]:
    if scenes is None:
        return []
    if not isinstance(scenes, Mapping):
        return [_setting_diagnostic(_SETTING_SCENES, "must be a mapping")]
    diagnostics = [
        _setting_diagnostic(f"{_SETTING_SCENES}.{name}", "is not a setting this module declares")
        for name in scenes
        if name not in _SCENES_FIELDS
    ]
    diagnostics.extend(_provider_diagnostics(scenes.get("provider"), has_seam=has_seam))

    allowed = scenes.get("allowed")
    if allowed is not None:
        if isinstance(allowed, str) or not isinstance(allowed, Sequence):
            diagnostics.append(
                _setting_diagnostic(f"{_SETTING_SCENES}.allowed", "must be a list of scene names")
            )
        elif not all(_is_scene_name(name) for name in allowed):
            diagnostics.append(
                _setting_diagnostic(
                    f"{_SETTING_SCENES}.allowed",
                    f"must hold names of 1 to {MAX_SCENE_NAME_CHARS} characters",
                )
            )
    if "max_waiters" in scenes and not _is_non_negative_int(scenes["max_waiters"]):
        diagnostics.append(
            _setting_diagnostic(
                f"{_SETTING_SCENES}.max_waiters", "must be a non-negative integer"
            )
        )
    return diagnostics


def _provider_diagnostics(provider: Any, *, has_seam: bool) -> list[str]:
    prefix = f"{_SETTING_SCENES}.provider"
    if provider is None:
        return []
    if not isinstance(provider, Mapping):
        return [_setting_diagnostic(prefix, "must be a mapping")]
    diagnostics = [
        _setting_diagnostic(f"{prefix}.{name}", "is not a setting this module declares")
        for name in provider
        if name not in _PROVIDER_FIELDS
    ]
    kind = provider.get("kind", KIND_NONE)
    if kind not in SCENE_PROVIDER_KINDS:
        diagnostics.append(
            _setting_diagnostic(f"{prefix}.kind", "must be one of none, scripted, websocket")
        )
    elif kind == KIND_SCRIPTED and not has_seam:
        diagnostics.append(
            _setting_diagnostic(
                f"{prefix}.kind", "'scripted' is only available with an injected scene provider"
            )
        )
    url = provider.get("url")
    if url is not None and not isinstance(url, str):
        diagnostics.append(_setting_diagnostic(FIELD_SCENES_PROVIDER_URL, "must be a string"))
    elif kind == KIND_WEBSOCKET and not _is_accepted_websocket_url(url or ""):
        diagnostics.append(
            _setting_diagnostic(
                FIELD_SCENES_PROVIDER_URL,
                "must be a ws:// URL on a loopback host, or a wss:// URL",
            )
        )
    if "password" in provider and not isinstance(provider["password"], str):
        diagnostics.append(_setting_diagnostic(f"{prefix}.password", "must be a string"))
    for field_name in ("connect_timeout_seconds", "request_timeout_seconds"):
        if field_name in provider and not _is_positive_number(provider[field_name]):
            diagnostics.append(
                _setting_diagnostic(f"{prefix}.{field_name}", "must be a finite positive number")
            )
    return diagnostics


def _polls_diagnostics(polls: Any) -> list[str]:
    if polls is None:
        return []
    if not isinstance(polls, Mapping):
        return [_setting_diagnostic(_SETTING_POLLS, "must be a mapping")]
    diagnostics = [
        _setting_diagnostic(f"{_SETTING_POLLS}.{name}", "is not a setting this module declares")
        for name in polls
        if name not in _POLLS_FIELDS
    ]
    if "enabled" in polls and not isinstance(polls["enabled"], bool):
        diagnostics.append(_setting_diagnostic(FIELD_POLLS_ENABLED, "must be a boolean"))
    if "max_waiters" in polls and not _is_non_negative_int(polls["max_waiters"]):
        diagnostics.append(
            _setting_diagnostic(f"{_SETTING_POLLS}.max_waiters", "must be a non-negative integer")
        )
    for field_name in _POLL_INT_FIELDS:
        if field_name in polls and not _is_positive_int(polls[field_name]):
            diagnostics.append(
                _setting_diagnostic(f"{_SETTING_POLLS}.{field_name}", "must be a positive integer")
            )
    for low, high, low_default, high_default in (
        ("min_options", "max_options", DEFAULT_MIN_OPTIONS, DEFAULT_MAX_OPTIONS),
        (
            "min_duration_seconds",
            "max_duration_seconds",
            DEFAULT_MIN_DURATION_SECONDS,
            DEFAULT_MAX_DURATION_SECONDS,
        ),
    ):
        minimum = polls.get(low, low_default)
        maximum = polls.get(high, high_default)
        if _is_positive_int(minimum) and _is_positive_int(maximum) and minimum > maximum:
            diagnostics.append(
                _setting_diagnostic(f"{_SETTING_POLLS}.{low}", f"must not exceed {high}")
            )
    return diagnostics


def _is_accepted_websocket_url(url: str) -> bool:
    """``ws://`` on a loopback host, or ``wss://`` on any host (R6)."""

    try:
        parts = urlsplit(url)
        host = parts.hostname
        parts.port  # noqa: B018 - a malformed port raises here
    except ValueError:
        return False
    if not host:
        return False
    if parts.scheme == "wss":
        return True
    if parts.scheme != "ws":
        return False
    if host.lower() in _LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _is_scene_provider(value: Any) -> bool:
    return hasattr(value, "connected") and all(
        callable(getattr(value, method, None)) for method in _PROVIDER_METHODS
    )


def _setting_diagnostic(field_name: str, reason: str) -> str:
    return f"module {MODULE_NAME!r}: field {field_name!r}: {reason}"


def _is_scene_name(value: Any) -> bool:
    return isinstance(value, str) and 1 <= len(value) <= MAX_SCENE_NAME_CHARS


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


@dataclass(frozen=True, slots=True)
class _Settings:
    """The accepted settings, parsed once."""

    kind: str
    url: str
    password: str
    connect_timeout_seconds: float
    request_timeout_seconds: float
    allowed: tuple[str, ...]
    scene_max_waiters: int
    polls_enabled: bool
    max_question_chars: int
    min_options: int
    max_options: int
    max_option_chars: int
    min_duration_seconds: int
    max_duration_seconds: int
    poll_max_waiters: int
    max_tracked_channels: int
    required: bool

    @classmethod
    def from_mapping(cls, settings: Mapping[str, Any]) -> "_Settings":
        scenes = settings.get(_SETTING_SCENES) or {}
        provider = scenes.get("provider") or {}
        polls = settings.get(_SETTING_POLLS) or {}
        return cls(
            kind=str(provider.get("kind", KIND_NONE)),
            url=str(provider.get("url", "")),
            password=str(provider.get("password", "")),
            connect_timeout_seconds=float(
                provider.get("connect_timeout_seconds", DEFAULT_CONNECT_TIMEOUT_SECONDS)
            ),
            request_timeout_seconds=float(
                provider.get("request_timeout_seconds", DEFAULT_REQUEST_TIMEOUT_SECONDS)
            ),
            allowed=tuple(str(name) for name in scenes.get("allowed") or ()),
            scene_max_waiters=int(scenes.get("max_waiters", DEFAULT_MAX_WAITERS)),
            polls_enabled=bool(polls.get("enabled", DEFAULT_POLLS_ENABLED)),
            max_question_chars=int(polls.get("max_question_chars", DEFAULT_MAX_QUESTION_CHARS)),
            min_options=int(polls.get("min_options", DEFAULT_MIN_OPTIONS)),
            max_options=int(polls.get("max_options", DEFAULT_MAX_OPTIONS)),
            max_option_chars=int(polls.get("max_option_chars", DEFAULT_MAX_OPTION_CHARS)),
            min_duration_seconds=int(
                polls.get("min_duration_seconds", DEFAULT_MIN_DURATION_SECONDS)
            ),
            max_duration_seconds=int(
                polls.get("max_duration_seconds", DEFAULT_MAX_DURATION_SECONDS)
            ),
            poll_max_waiters=int(polls.get("max_waiters", DEFAULT_MAX_WAITERS)),
            max_tracked_channels=int(
                polls.get("max_tracked_channels", DEFAULT_MAX_TRACKED_CHANNELS)
            ),
            required=bool(settings.get(_SETTING_REQUIRED, False)),
        )


# --------------------------------------------------------------------------- #
# The handle
# --------------------------------------------------------------------------- #


class _PollEntry:
    """One tracked entry of a channel: an active poll or an uncertain marker.

    ``expires_at`` is on the module clock: ``duration_seconds`` plus
    :data:`POLL_ENTRY_GRACE_SECONDS` after the entry was recorded.
    """

    __slots__ = ("active", "expires_at", "options", "poll_id", "question", "since")

    def __init__(
        self,
        *,
        active: bool,
        question: str,
        options: tuple[str, ...],
        since: float,
        expires_at: float,
        poll_id: str | None = None,
    ) -> None:
        self.active = active
        self.question = question
        self.options = options
        self.since = since
        self.expires_at = expires_at
        self.poll_id = poll_id


class _PollTable:
    """The bounded table of tracked polls, keyed ``(platform, channel_id)`` (R7).

    At most *max_channels* channels — counting the channels a call holds a
    reservation on — and at most :data:`MAX_POLL_ENTRIES_PER_CHANNEL` live
    entries per channel. :meth:`reap` drops the expired entries and the
    channels left with none; nothing else ever removes an entry, so a live
    one — an uncertain marker above all — is never evicted by pressure.
    """

    __slots__ = ("_channels", "_max_channels", "_reserved")

    def __init__(self, max_channels: int) -> None:
        self._max_channels = max_channels
        self._channels: dict[tuple[str, str], list[_PollEntry]] = {}
        self._reserved: set[tuple[str, str]] = set()

    def reap(self, now: float) -> None:
        for key in list(self._channels):
            live = [entry for entry in self._channels[key] if entry.expires_at > now]
            if live:
                self._channels[key] = live
            else:
                del self._channels[key]

    def entries(self, key: tuple[str, str]) -> tuple[_PollEntry, ...]:
        return tuple(self._channels.get(key, ()))

    def tracked(self) -> frozenset[tuple[str, str]]:
        return frozenset(self._channels)

    def reserve(self, key: tuple[str, str]) -> bool:
        """Hold room for one more entry on *key*; ``False`` when there is none."""

        if key in self._channels or key in self._reserved:
            if len(self._channels.get(key, ())) >= MAX_POLL_ENTRIES_PER_CHANNEL:
                return False
        elif len(self._channels.keys() | self._reserved) >= self._max_channels:
            return False
        self._reserved.add(key)
        return True

    def release(self, key: tuple[str, str]) -> None:
        self._reserved.discard(key)

    def add(self, key: tuple[str, str], entry: _PollEntry) -> None:
        self._channels.setdefault(key, []).append(entry)


class _PollRequest:
    """One call's accepted arguments."""

    __slots__ = ("duration_seconds", "options", "question")

    def __init__(self, question: str, options: tuple[str, ...], duration_seconds: int) -> None:
        self.question = question
        self.options = options
        self.duration_seconds = duration_seconds


class _PollActionProvider:
    """The ``stream.poll.create`` provider the executor invokes (R7)."""

    __slots__ = ("_module",)

    name = PROVIDER_NAME

    def __init__(self, module: "StreamControlModule") -> None:
        self._module = module

    async def invoke(self, invocation: Any) -> ActionObservation:
        return await self._module._invoke_poll(invocation)


class _SceneActionProvider:
    """The ``stream.scene.set`` provider the executor invokes (R6)."""

    __slots__ = ("_module",)

    name = PROVIDER_NAME

    def __init__(self, module: "StreamControlModule") -> None:
        self._module = module

    async def invoke(self, invocation: Any) -> ActionObservation:
        return await self._module._invoke_scene(invocation)


class _Slot:
    """One serialized resource: the scene provider (R6) or a poll channel (R7).

    ``lock`` is held by the call whose command is in flight, from before its
    first request until its outcome is known; ``occupants`` counts every
    admitted call — holding the lock or waiting for it — so the admission
    rule reads one number.
    """

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
    """A provider request that raised: its answer is lost."""

    __slots__ = ()


_FAILED = _Failed()


class _Raised:
    """A request that raised, with the exception (``None`` when cancelled)."""

    __slots__ = ("error",)

    def __init__(self, error: BaseException | None) -> None:
        self.error = error


def _admit(slot: _Slot, max_waiters: int, busy_message: str) -> _Claim:
    """Take a place on *slot*: the holder plus at most *max_waiters* waiting."""

    if slot.occupants > 0 and slot.occupants - 1 >= max_waiters:
        raise _CallFailure("refused", ERROR_RESOURCE_BUSY, busy_message)
    return _Claim(slot)


class StreamControlModule:
    """The v2 handle: probe and bind at ``prepare``, switch scenes on demand."""

    def __init__(
        self,
        context: Any,
        settings: _Settings,
        *,
        scene_provider: Any,
        sleeper: Sleeper,
        websocket_factory: Callable[..., Any] | None,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        self._actions = context.actions
        self._supervision = getattr(context, "supervision", None)
        # The module's service facade; only ``entries()`` and ``resolve()``
        # are ever called on it (R7, round 2 P3).
        self._services = getattr(context, "services", None)
        self._clock: Callable[[], float] = getattr(context, "clock", None) or time.monotonic
        # Epoch seconds, the domain a platform's poll ``started_at`` is in:
        # the monotonic clock above is never compared with it.
        self._wall_clock = wall_clock
        self._settings = settings
        self._sleeper = sleeper
        if settings.kind == KIND_WEBSOCKET and scene_provider is None:
            scene_provider = WebSocketSceneProvider(
                settings.url,
                settings.password,
                settings.connect_timeout_seconds,
                settings.request_timeout_seconds,
                websocket_factory=websocket_factory or default_websocket_factory,
                sleeper=sleeper,
                clock=self._clock,
                rng=getattr(context, "rng", None) or random.Random(),
                tasks=getattr(context, "tasks", None),
                on_disconnected=self._on_provider_lost,
                on_reconnected=self._on_provider_restored,
            )
        self._provider = scene_provider
        self._scene_provider = _SceneActionProvider(self)
        self._slot = _Slot()
        self._poll_provider = _PollActionProvider(self)
        # ``platform → poll service``, resolved once at ``prepare``.
        self._poll_services: dict[str, Any] = {}
        self._poll_table = _PollTable(settings.max_tracked_channels)
        # One slot per ``(platform, channel_id)`` with a call admitted on it;
        # dropped when its last call left.
        self._poll_slots: dict[tuple[str, str], _Slot] = {}
        loop = asyncio.get_running_loop()
        # Resolved when ``drain`` begins: every call that has not sent its
        # set command ends ``cancelled``.
        self._drain_started: asyncio.Future[None] = loop.create_future()
        # Resolved when the drain deadline passed: a call whose set command
        # left ends ``external_unknown`` and starts no further read.
        self._drain_expired: asyncio.Future[None] = loop.create_future()
        # One future per running call, resolved when it returned.
        self._calls: set[asyncio.Future[None]] = set()
        self._scene_bound = False
        self._withdrawn = False
        self._prepared = False
        self._draining = False
        self._closed = False

    @property
    def settings(self) -> _Settings:
        return self._settings

    @property
    def poll_platforms(self) -> tuple[str, ...]:
        """The platforms whose poll service resolved at ``prepare``."""

        return tuple(sorted(self._poll_services))

    def tracked_poll_channels(self) -> frozenset[tuple[str, str]]:
        """The ``(platform, channel_id)`` keys the poll table holds now."""

        self._poll_table.reap(self._clock())
        return self._poll_table.tracked()

    # -- lifecycle hooks ---------------------------------------------------- #

    async def prepare(self) -> None:
        """Probe the scene provider, apply the ``required`` policy, bind.

        Dependencies are checked in settings order — scenes, then polls — and
        under ``required: true`` the first one unavailable fails ``prepare``
        naming its field, after the scene provider was closed, with nothing
        bound. Under ``required: false`` each unavailable one is reported
        once through ``module.degraded`` naming the action it leaves unbound.
        A scene provider that did not answer its probe keeps
        ``stream.scene.set`` registered on a module that is never marked
        ready, so a call is ``refused provider_not_ready`` before the
        provider; ``kind: none`` only declares it, so another module may
        serve it.
        """

        if self._prepared or self._closed:
            return
        settings = self._settings
        scene_reachable: bool | None = None
        if settings.kind != KIND_NONE:
            scene_reachable = await self._probe()
            if not scene_reachable and settings.required:
                await self._close_provider()
                raise StreamControlModuleError(
                    f"{MODULE_NAME} prepare: field '{FIELD_SCENES_PROVIDER_URL}' unavailable"
                )
        # An enabled poll action whose service no platform published is an
        # unavailable dependency like any other (finding P1).
        poll_services = self._resolve_poll_services() if settings.polls_enabled else {}
        polls_unavailable = settings.polls_enabled and not poll_services
        if polls_unavailable and settings.required:
            await self._close_provider()
            raise StreamControlModuleError(
                f"{MODULE_NAME} prepare: field '{FIELD_POLLS_ENABLED}' unavailable: "
                "no poll service published for any platform"
            )

        self._scene_bound = scene_reachable is True
        bound_any = self._scene_bound or bool(poll_services)
        try:
            scene_spec = _declared_spec(SCENE_ACTION)
            poll_spec = _declared_spec(POLL_ACTION)
            if self._scene_bound or (scene_reachable is False and not bound_any):
                self._actions.register(
                    scene_spec, self._scene_provider, provider_name=PROVIDER_NAME
                )
            else:
                self._actions.declare(scene_spec)
            if poll_services:
                self._actions.register(
                    poll_spec,
                    self._poll_provider,
                    destinations=[
                        Destination(platform, "*", "poll") for platform in sorted(poll_services)
                    ],
                    provider_name=PROVIDER_NAME,
                )
            else:
                self._actions.declare(poll_spec)
        except StreamControlModuleError:
            await self._close_provider()
            raise
        except Exception:
            await self._close_provider()
            raise StreamControlModuleError(
                f"{MODULE_NAME} prepare: action binding failed"
            ) from None
        self._poll_services = poll_services
        self._prepared = True

        if scene_reachable is False:
            await self._report_degraded(REASON_SCENE_PROVIDER_UNREACHABLE, [SCENE_ACTION])
        if polls_unavailable:
            await self._report_degraded(REASON_NO_POLL_SERVICE, [POLL_ACTION])
        if bound_any:
            self._actions.mark_ready()

    def _resolve_poll_services(self) -> dict[str, Any]:
        """``platform → service`` for every ``(poll, <platform>)`` published.

        Reads ``entries()`` and ``resolve()`` of the service facade and
        nothing else, so any collaborator with the accepted three-method
        surface serves (round 2, P3). A context without a registry hands out
        an empty view: nothing resolves.
        """

        services = self._services
        entries = getattr(services, "entries", None)
        resolve = getattr(services, "resolve", None)
        if not callable(entries) or not callable(resolve):
            return {}
        resolved: dict[str, Any] = {}
        for key in list(entries()):
            if not isinstance(key, tuple) or len(key) != 2:
                continue
            kind, platform = key
            if kind != POLL_SERVICE_KIND or not isinstance(platform, str) or not platform:
                continue
            service = resolve(kind, platform)
            if service is not None and all(
                callable(getattr(service, method, None)) for method in _POLL_METHODS
            ):
                resolved[platform] = service
        return resolved

    async def drain(self, deadline_seconds: float) -> None:
        """End waiting calls, give a sent command the drain deadline.

        Every call that has not sent its set command or its poll create ends
        ``cancelled`` at once, with 0 commands. A call whose command left
        keeps waiting for its confirmation until *deadline_seconds* passed on
        the clock; past it the call ends ``external_unknown`` (a poll channel
        marked uncertain) and starts no further read.
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
        """Withdraw readiness, end what still runs, release the provider."""

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
        await self._close_provider()

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
                # The drain deadline passed first: the calls still waiting on
                # a confirmation end ``external_unknown`` now, on this turn.
                _resolve(self._drain_expired)
                await asyncio.wait(pending)
        finally:
            await _settle(timer)

    async def _close_provider(self) -> None:
        provider, self._provider = self._provider, None
        close = getattr(provider, "close", None)
        if callable(close):
            try:
                outcome = close()
                if inspect.isawaitable(outcome):
                    await outcome
            except Exception:  # noqa: BLE001 - a provider that fails to close is gone anyway
                pass

    async def _probe(self) -> bool:
        """One ``list_scenes()``, bounded on the sleeper; whether it answered."""

        provider = self._provider
        if provider is None:
            return False
        bound = self._settings.connect_timeout_seconds + self._settings.request_timeout_seconds
        task = asyncio.ensure_future(_probe_request(provider))
        timer = asyncio.ensure_future(self._sleeper(bound))
        try:
            await asyncio.wait({task, timer}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            # Neither the probe bound nor a cancellation of prepare() waits on
            # the provider: a request still running is cancelled and left to
            # end on its own, its outcome consumed whenever it does.
            _abandon(timer)
            if not task.done():
                _abandon(task)
        if not task.done():
            return False
        if task.cancelled() or task.exception() is not None:
            return False
        scenes = task.result()
        return isinstance(scenes, Sequence) and not isinstance(scenes, str)

    async def _report_degraded(self, reason: str, capabilities: Sequence[str]) -> None:
        degraded = getattr(self._supervision, "degraded", None)
        if not callable(degraded):
            return
        try:
            outcome = degraded(reason=reason, capabilities=list(capabilities))
            if inspect.isawaitable(outcome):
                await outcome
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a health report must not fail a call
            return

    async def _withdraw_readiness(self) -> None:
        """The provider is gone: withdraw readiness and say so, once per loss.

        Readiness is per module in the registry, so while the poll action is
        bound the module stays ready: a scene outage must not take a healthy
        poll service down with it. Scene calls then answer
        ``provider_unavailable`` on their own until the provider is back.
        """

        if self._withdrawn:
            return
        self._withdrawn = True
        if not self._poll_services:
            self._actions.mark_not_ready()
        await self._report_degraded(REASON_SCENE_PROVIDER_DISCONNECTED, [SCENE_ACTION])

    async def _on_provider_lost(self) -> None:
        """The provider's socket was lost: withdraw readiness at once."""

        if self._closed or not self._scene_bound:
            return
        await self._withdraw_readiness()

    async def _on_provider_restored(self) -> None:
        """A supervised reconnection succeeded: the action is ready again."""

        if self._closed or not self._scene_bound:
            return
        self._withdrawn = False
        self._actions.mark_ready()
        ready = getattr(self._supervision, "ready", None)
        if not callable(ready):
            return
        try:
            outcome = ready(capabilities=[SCENE_ACTION])
            if inspect.isawaitable(outcome):
                await outcome
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a health report must not fail the loop
            return

    # -- stream.scene.set (R6, AC21–AC23) ------------------------------------ #

    async def _invoke_scene(self, invocation: Any) -> ActionObservation:
        """Serve one ``stream.scene.set`` call for the executor."""

        # Nothing has left before the set command: an interruption until then
        # is a certain ``timeout``/``cancelled``.
        invocation.mark_not_emitted()
        expiry = self._expiry(invocation)
        provenance = _provenance(invocation.call, SCENE_ACTION)
        running: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._calls.add(running)
        claim: _Claim | None = None
        try:
            self._check_open()
            scene = invocation.call.arguments.get("scene")
            if not _is_scene_name(scene):
                raise _CallFailure(
                    "error",
                    ERROR_INVALID_ARGUMENTS,
                    f"scene must hold 1 to {MAX_SCENE_NAME_CHARS} characters",
                )
            if scene not in self._settings.allowed:
                raise _CallFailure(
                    "refused", ERROR_SCENE_NOT_ALLOWED, "scene is not one this module allows"
                )
            provider = self._provider
            if provider is None or not provider.connected:
                await self._withdraw_readiness()
                raise _CallFailure(
                    "error", ERROR_PROVIDER_UNAVAILABLE, "the scene provider is not connected"
                )
            claim = self._claim()
            await self._acquire(claim, expiry)
            return await self._switch(invocation, provider, scene, expiry, provenance)
        except _CallFailure as failure:
            return _failure_observation(provenance, failure)
        finally:
            if claim is not None:
                claim.release()
            self._calls.discard(running)
            _resolve(running)

    async def _switch(
        self,
        invocation: Any,
        provider: Any,
        scene: str,
        expiry: float,
        provenance: Mapping[str, Any],
    ) -> ActionObservation:
        """Read the previous scene, send one set command, confirm it."""

        self._check_may_continue(expiry, emitted=False)
        previous = await self._request(provider.current_scene(), expiry, emitted=False)
        if previous is _FAILED or not isinstance(previous, str):
            if not provider.connected:
                await self._withdraw_readiness()
            raise _CallFailure(
                "error", ERROR_PROVIDER_UNAVAILABLE, "the scene provider did not answer"
            )

        # The read may have answered on the same turn the drain began or the
        # deadline passed: no set command leaves then.
        self._check_may_continue(expiry, emitted=False)
        # From here the scene may change: a lost answer is uncertain.
        invocation.mark_emitted()
        answer = await self._request(provider.set_scene(scene), expiry, emitted=True)
        applied = _set_answer(answer)
        if applied is None:
            return await self._reconcile(provider, scene, previous, expiry, provenance)
        if applied is not True:
            if applied == SCENE_UNKNOWN_CODE:
                raise _CallFailure(
                    "error", ERROR_SCENE_UNKNOWN, "the scene provider does not know this scene"
                )
            raise _CallFailure(
                "error",
                ERROR_SCENE_NOT_APPLIED,
                f"the scene provider refused the scene with status {applied}",
                status_code=applied,
            )

        self._check_may_continue(expiry, emitted=True)
        current = await self._request(provider.current_scene(), expiry, emitted=True)
        if current is _FAILED:
            return await self._reconcile(provider, scene, previous, expiry, provenance)
        if current != scene:
            raise _CallFailure(
                "error",
                ERROR_SCENE_NOT_APPLIED,
                "the scene read back after the switch is not the one requested",
            )
        return _scene_success(provenance, scene, previous, self._clock(), reconciled=False)

    async def _reconcile(
        self,
        provider: Any,
        scene: str,
        previous: str,
        expiry: float,
        provenance: Mapping[str, Any],
    ) -> ActionObservation:
        """Exactly one reconciliation read, within the time left."""

        if self._drain_expired.done():
            raise _uncertain("the drain deadline passed before the scene was confirmed")
        if self._clock() >= expiry:
            raise _deadline_failure(emitted=True)
        current = await self._request(provider.current_scene(), expiry, emitted=True)
        if current is _FAILED or current != scene:
            raise _uncertain("the scene could not be confirmed after the switch")
        return _scene_success(provenance, scene, previous, self._clock(), reconciled=True)

    def _check_may_continue(self, expiry: float, *, emitted: bool, what: str = "scene") -> None:
        """Refuse to start the next provider request once interrupted.

        Before the set command the drain ends the call ``cancelled``; after
        it the drain deadline ends it ``external_unknown``. Past *expiry*
        either way the call ends ``timeout``.
        """

        if emitted:
            if self._drain_expired.done():
                raise _uncertain(f"the drain deadline passed before the {what} was confirmed")
        elif self._drain_started.done():
            raise _CallFailure("cancelled", ERROR_CANCELLED, f"{MODULE_NAME}: shutting down")
        if self._clock() >= expiry:
            raise _deadline_failure(emitted=emitted, what=what)

    async def _request(
        self,
        work: Awaitable[Any],
        expiry: float,
        *,
        emitted: bool,
        detail: bool = False,
        what: str = "scene",
    ) -> Any:
        """Await one provider request, bounded by *expiry* on the sleeper.

        Returns the answer, or :data:`_FAILED` when the request raised — a
        :class:`_Raised` carrying the exception when *detail* is set. A
        request still pending at the deadline, at the drain (before the
        command) or at the drain deadline (after it), or when the call is
        cancelled, is abandoned and the call ends with the matching
        interruption record, whose message names *what* was not confirmed.
        """

        task = asyncio.ensure_future(work)
        watcher = asyncio.ensure_future(self._sleeper(max(expiry - self._clock(), 0.0)))
        interrupt = self._drain_expired if emitted else self._drain_started
        try:
            await asyncio.wait(
                {task, watcher, interrupt}, return_when=asyncio.FIRST_COMPLETED
            )
        except asyncio.CancelledError:
            _abandon(task)
            _abandon(watcher)
            raise self._cancellation(expiry, emitted=emitted, what=what) from None
        _abandon(watcher)
        if task.done():
            if task.cancelled():
                return _Raised(None) if detail else _FAILED
            if task.exception() is not None:
                return _Raised(task.exception()) if detail else _FAILED
            return task.result()
        _abandon(task)
        if interrupt.done():
            if emitted:
                raise _uncertain(f"the drain deadline passed before the {what} was confirmed")
            raise _CallFailure("cancelled", ERROR_CANCELLED, f"{MODULE_NAME}: shutting down")
        raise _deadline_failure(emitted=emitted, what=what)

    def _cancellation(self, expiry: float, *, emitted: bool, what: str = "scene") -> _CallFailure:
        """The record a cancellation reaching the call stands for (decision 1)."""

        if self._clock() >= expiry:
            return _deadline_failure(emitted=emitted, what=what)
        details = {"cause": CAUSE_CONFIRMATION_LOST} if emitted else {}
        return _CallFailure("cancelled", ERROR_CANCELLED, "the call was cancelled", **details)

    async def _acquire(self, claim: _Claim, expiry: float) -> None:
        """Wait for the command in flight, unless the drain or the deadline comes first."""

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

    def _claim(self) -> _Claim:
        """Take a place behind the set command in flight, or refuse."""

        return _admit(
            self._slot,
            self._settings.scene_max_waiters,
            "a scene switch is in flight and its waiting line is full",
        )

    # -- stream.poll.create (R7, AC26–AC28, AC37) --------------------------- #

    async def _invoke_poll(self, invocation: Any) -> ActionObservation:
        """Serve one ``stream.poll.create`` call for the executor."""

        # Nothing has left before the create request: an interruption until
        # then is a certain ``timeout``/``cancelled``.
        invocation.mark_not_emitted()
        expiry = self._expiry(invocation)
        # Compared with the platform's ``started_at`` (epoch seconds) only.
        started = float(self._wall_clock())
        provenance = _provenance(invocation.call, POLL_ACTION)
        running: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._calls.add(running)
        claim: _Claim | None = None
        key: tuple[str, str] | None = None
        reserved = False
        try:
            self._check_open()
            request = self._poll_arguments(invocation.call.arguments)
            destination = invocation.call.destination
            key = (str(destination.platform), str(destination.channel_id))
            service = self._poll_services.get(key[0])
            if service is None:
                raise _CallFailure(
                    "error",
                    ERROR_PLATFORM_UNAVAILABLE,
                    "no poll service is published for this platform",
                )
            claim = self._poll_claim(key)
            await self._acquire(claim, expiry)
            await self._poll_precheck(service, key, request, expiry)
            # The cap is checked after the expired entries were reaped and
            # before the create: a live entry is never evicted to make room.
            self._poll_table.reap(self._clock())
            if not self._poll_table.reserve(key):
                raise _CallFailure(
                    "refused",
                    ERROR_RESOURCE_BUSY,
                    "the tracked poll table has no room for this channel",
                )
            reserved = True
            self._check_may_continue(expiry, emitted=False, what="poll")
            # From here a poll may open: a lost answer is uncertain.
            invocation.mark_emitted()
            try:
                return await self._create_poll(
                    service, key, request, started, expiry, provenance
                )
            except _CallFailure as failure:
                if failure.status in ("external_unknown", "timeout", "cancelled"):
                    self._mark_uncertain(key, request)
                raise
        except _CallFailure as failure:
            return _failure_observation(provenance, failure)
        finally:
            if reserved and key is not None:
                self._poll_table.release(key)
            if claim is not None:
                claim.release()
                if key is not None and claim.slot.occupants == 0:
                    if self._poll_slots.get(key) is claim.slot:
                        del self._poll_slots[key]
            self._calls.discard(running)
            _resolve(running)

    def _poll_arguments(self, arguments: Mapping[str, Any]) -> _PollRequest:
        """The call's arguments within the ``polls`` bounds, or ``invalid_arguments``."""

        settings = self._settings
        question = arguments.get("question")
        options = arguments.get("options")
        duration = arguments.get("duration_seconds")
        if not isinstance(question, str) or not 1 <= len(question) <= settings.max_question_chars:
            raise _CallFailure(
                "error",
                ERROR_INVALID_ARGUMENTS,
                f"question must hold 1 to {settings.max_question_chars} characters",
            )
        if (
            isinstance(options, str)
            or not isinstance(options, Sequence)
            or not settings.min_options <= len(options) <= settings.max_options
        ):
            raise _CallFailure(
                "error",
                ERROR_INVALID_ARGUMENTS,
                f"options must hold {settings.min_options} to {settings.max_options} entries",
            )
        if not all(
            isinstance(option, str) and 1 <= len(option) <= settings.max_option_chars
            for option in options
        ):
            raise _CallFailure(
                "error",
                ERROR_INVALID_ARGUMENTS,
                f"each option must hold 1 to {settings.max_option_chars} characters",
            )
        if len(set(options)) != len(options):
            raise _CallFailure("error", ERROR_INVALID_ARGUMENTS, "options must be distinct")
        if (
            not isinstance(duration, int)
            or isinstance(duration, bool)
            or not settings.min_duration_seconds <= duration <= settings.max_duration_seconds
        ):
            raise _CallFailure(
                "error",
                ERROR_INVALID_ARGUMENTS,
                f"duration_seconds must be {settings.min_duration_seconds} to "
                f"{settings.max_duration_seconds}",
            )
        return _PollRequest(question, tuple(options), duration)

    def _poll_claim(self, key: tuple[str, str]) -> _Claim:
        """Take a place behind the create in flight on *key*, or refuse."""

        slot = self._poll_slots.get(key)
        if slot is None:
            slot = self._poll_slots[key] = _Slot()
        return _admit(
            slot,
            self._settings.poll_max_waiters,
            "a poll create is in flight on this channel and its waiting line is full",
        )

    async def _poll_precheck(
        self, service: Any, key: tuple[str, str], request: _PollRequest, expiry: float
    ) -> None:
        """Refuse a channel running a poll; reconcile an uncertain one first.

        An active entry is ``refused poll_active`` with 0 requests. An
        uncertain marker costs one ``get``: an active poll listed is
        ``refused poll_active``, none lets the create proceed, a failing
        ``get`` is ``external_unknown`` — the marker stays until its TTL
        either way.
        """

        self._poll_table.reap(self._clock())
        entries = self._poll_table.entries(key)
        if any(entry.active for entry in entries):
            raise _CallFailure(
                "refused", ERROR_POLL_ACTIVE, "a poll is already running on this channel"
            )
        if not entries:
            return
        self._check_may_continue(expiry, emitted=False, what="poll")
        listed = await self._request(service.get(key[1]), expiry, emitted=False, what="poll")
        polls = _listed_polls(listed)
        if polls is None:
            raise _uncertain("the channel's earlier poll could not be confirmed")
        if any(poll.get("state") == POLL_STATE_ACTIVE for poll in polls):
            raise _CallFailure(
                "refused", ERROR_POLL_ACTIVE, "a poll is already running on this channel"
            )

    async def _create_poll(
        self,
        service: Any,
        key: tuple[str, str],
        request: _PollRequest,
        started: float,
        expiry: float,
        provenance: Mapping[str, Any],
    ) -> ActionObservation:
        """Exactly one create request; its answer, or one reconciliation read."""

        outcome = await self._request(
            service.create(
                key[1], request.question, list(request.options), request.duration_seconds
            ),
            expiry,
            emitted=True,
            detail=True,
            what="poll",
        )
        if not isinstance(outcome, _Raised):
            poll = _created_poll(outcome)
            if poll is not None:
                return self._poll_success(key, request, provenance, poll, reconciled=False)
            # A 2xx that is not a poll: the request did leave.
            return await self._reconcile_poll(service, key, request, started, expiry, provenance)
        status = _status_code(outcome.error)
        if status is not None:
            if status in (401, 403):
                raise _CallFailure(
                    "refused",
                    ERROR_PLATFORM_FORBIDDEN,
                    f"the platform refused the poll with status {status}",
                    status_code=status,
                )
            if 400 <= status < 500:
                raise _CallFailure(
                    "error",
                    ERROR_POLL_REJECTED,
                    f"the platform rejected the poll with status {status}",
                    status_code=status,
                )
            if 500 <= status < 600:
                return await self._reconcile_poll(
                    service, key, request, started, expiry, provenance, status_code=status
                )
        elif getattr(outcome.error, "sent", None) is False:
            raise _CallFailure(
                "error", ERROR_PLATFORM_UNAVAILABLE, "the poll request could not be sent"
            )
        return await self._reconcile_poll(service, key, request, started, expiry, provenance)

    async def _reconcile_poll(
        self,
        service: Any,
        key: tuple[str, str],
        request: _PollRequest,
        started: float,
        expiry: float,
        provenance: Mapping[str, Any],
        *,
        status_code: int | None = None,
    ) -> ActionObservation:
        """Exactly one ``get`` within the time left.

        A poll with the same question and options (in order) started at or
        after the call's start (both epoch seconds) is ``success`` with ``reconciled: true``. None
        listed after a 5xx is ``error platform_unavailable`` carrying the
        status (the request was refused); after a lost answer, or when the
        ``get`` fails, the outcome is ``external_unknown``.
        """

        self._check_may_continue(expiry, emitted=True, what="poll")
        listed = await self._request(service.get(key[1]), expiry, emitted=True, what="poll")
        polls = _listed_polls(listed)
        if polls is None:
            raise _uncertain("the poll could not be confirmed after the create")
        match = _matching_poll(polls, request, started)
        if match is not None:
            return self._poll_success(key, request, provenance, match, reconciled=True)
        if status_code is not None:
            raise _CallFailure(
                "error",
                ERROR_PLATFORM_UNAVAILABLE,
                f"the platform answered the poll create with status {status_code}",
                status_code=status_code,
            )
        raise _uncertain("the poll could not be confirmed after the create")

    def _poll_success(
        self,
        key: tuple[str, str],
        request: _PollRequest,
        provenance: Mapping[str, Any],
        poll: Mapping[str, Any],
        *,
        reconciled: bool,
    ) -> ActionObservation:
        now = self._clock()
        started_at = float(poll["started_at"])
        self._poll_table.add(
            key,
            _PollEntry(
                active=True,
                question=request.question,
                options=request.options,
                since=now,
                expires_at=now + request.duration_seconds + POLL_ENTRY_GRACE_SECONDS,
                poll_id=poll["poll_id"],
            ),
        )
        return ActionObservation(
            status="success",
            provenance=provenance,
            result={
                "poll_id": poll["poll_id"],
                "state": POLL_STATE_ACTIVE,
                "question": request.question,
                "options": list(request.options),
                "started_at": started_at,
                "ends_at": started_at + request.duration_seconds,
                "reconciled": reconciled,
            },
        )

    def _mark_uncertain(self, key: tuple[str, str], request: _PollRequest) -> None:
        """Record that a poll may have opened on *key*; kept until its TTL."""

        now = self._clock()
        self._poll_table.add(
            key,
            _PollEntry(
                active=False,
                question=request.question,
                options=request.options,
                since=now,
                expires_at=now + request.duration_seconds + POLL_ENTRY_GRACE_SECONDS,
            ),
        )


def _status_code(error: BaseException | None) -> int | None:
    """The HTTP status a poll service failure carries, read by attribute."""

    status = getattr(error, "status", None)
    if isinstance(status, int) and not isinstance(status, bool):
        return status
    return None


def _is_time(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _created_poll(answer: Any) -> Mapping[str, Any] | None:
    """A create answer usable as a success: a poll id and a start time."""

    if not isinstance(answer, Mapping):
        return None
    poll_id = answer.get("poll_id")
    if not isinstance(poll_id, str) or not poll_id.strip():
        return None
    if not _is_time(answer.get("started_at")):
        return None
    return answer


def _listed_polls(listed: Any) -> list[Mapping[str, Any]] | None:
    """A ``get`` answer as a list of polls, or ``None`` when it failed."""

    if listed is _FAILED or isinstance(listed, (str, bytes)) or not isinstance(listed, Sequence):
        return None
    return [poll for poll in listed if isinstance(poll, Mapping)]


def _matching_poll(
    polls: Sequence[Mapping[str, Any]], request: _PollRequest, started: float
) -> Mapping[str, Any] | None:
    """The listed poll this call opened: same question, same options in order,
    started at or after the call's start, with a poll id."""

    for poll in polls:
        options = poll.get("options")
        if (
            poll.get("question") == request.question
            and isinstance(options, Sequence)
            and not isinstance(options, str)
            and list(options) == list(request.options)
            and _is_time(poll.get("started_at"))
            and float(poll["started_at"]) >= started
            and _created_poll(poll) is not None
        ):
            return poll
    return None


async def _probe_request(provider: Any) -> Any:
    """Connect a provider that dials (the ``websocket`` kind), then list its scenes."""

    connect = getattr(provider, "connect", None)
    if callable(connect):
        await connect()
    return await provider.list_scenes()


def _set_answer(answer: Any) -> bool | int | None:
    """``True`` when applied, the numeric status when refused, ``None`` when lost."""

    if answer is _FAILED or not isinstance(answer, Mapping):
        return None
    result = answer.get("result")
    if result is True:
        return True
    code = answer.get("code")
    if result is False and isinstance(code, int) and not isinstance(code, bool):
        return code
    return None


def _deadline_failure(*, emitted: bool, what: str = "scene") -> _CallFailure:
    if emitted:
        return _CallFailure(
            "timeout",
            ERROR_TIMED_OUT,
            f"the call deadline was reached before the {what} was confirmed",
            cause=CAUSE_CONFIRMATION_LOST,
        )
    if what == "scene":
        return _CallFailure(
            "timeout", ERROR_TIMED_OUT, "the call deadline was reached before the switch"
        )
    return _CallFailure(
        "timeout", ERROR_TIMED_OUT, f"the call deadline was reached before the {what} was sent"
    )


def _uncertain(message: str) -> _CallFailure:
    return _CallFailure(
        "external_unknown", ERROR_EXTERNAL_UNKNOWN, message, cause=CAUSE_CONFIRMATION_LOST
    )


def _scene_success(
    provenance: Mapping[str, Any],
    scene: str,
    previous: str,
    confirmed_at: float,
    *,
    reconciled: bool,
) -> ActionObservation:
    return ActionObservation(
        status="success",
        provenance=provenance,
        result={
            "scene": scene,
            "previous_scene": previous,
            "confirmed_at": float(confirmed_at),
            "reconciled": reconciled,
        },
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


def _provenance(call: Any, action: str) -> dict[str, Any]:
    destination = call.destination
    return {
        "provider": PROVIDER_NAME,
        "platform": destination.platform,
        "channel_id": destination.channel_id,
        "route": action,
    }


def _failure_observation(provenance: Mapping[str, Any], failure: _CallFailure) -> ActionObservation:
    error: dict[str, Any] = {
        "code": failure.code,
        "message": failure.message,
        "retryable": False,
    }
    error.update(failure.details)
    return ActionObservation(status=failure.status, provenance=provenance, error=error)


# --------------------------------------------------------------------------- #
# The ``websocket`` scene provider (R6, AC36, AC22)
# --------------------------------------------------------------------------- #

# Protocol 5 opcodes and the one RPC version this provider speaks.
_OP_HELLO = 0
_OP_IDENTIFY = 1
_OP_IDENTIFIED = 2
_OP_REQUEST = 6
_OP_REQUEST_RESPONSE = 7
RPC_VERSION = 1
#: The close code of a refused authentication: an unreachable provider.
CLOSE_AUTHENTICATION_FAILED = 4009

REQUEST_LIST_SCENES = "GetSceneList"
REQUEST_CURRENT_SCENE = "GetCurrentProgramScene"
REQUEST_SET_SCENE = "SetCurrentProgramScene"

#: Supervised reconnection: initial delay, multiplier, cap (full jitter).
RECONNECT_INITIAL_SECONDS = 1.0
RECONNECT_MULTIPLIER = 2.0
RECONNECT_MAX_SECONDS = 30.0

# The frame types that end a connection, compared by name so importing this
# module never imports the websocket library.
_WS_TERMINAL = frozenset({"CLOSE", "CLOSING", "CLOSED", "ERROR"})

Notify = Callable[[], Awaitable[Any]]


class SceneProviderUnavailable(ConnectionError):
    """The provider is not connected, or the connection was lost."""


class SceneRequestTimeout(TimeoutError):
    """A request's answer did not arrive within ``request_timeout_seconds``."""


class SceneRequestFailed(RuntimeError):
    """A read answered with an unsuccessful status; carries its code only."""

    def __init__(self, request_type: str, code: Any) -> None:
        super().__init__(f"{request_type} answered with status {code}")
        self.code = code


def authentication_string(password: str, salt: str, challenge: str) -> str:
    """``base64(sha256(base64(sha256(password + salt)) + challenge))``."""

    secret = base64.b64encode(hashlib.sha256((password + salt).encode("utf-8")).digest())
    digest = hashlib.sha256(secret + challenge.encode("utf-8")).digest()
    return base64.b64encode(digest).decode("ascii")


class WebSocketSceneProvider:
    """The scene provider over obs-websocket protocol 5 (RPC version 1).

    Nothing is dialed at construction: :meth:`connect` opens the socket
    through *websocket_factory* and runs the handshake, bounded by
    *connect_timeout* on the *sleeper*. Each request is bounded by
    *request_timeout*. *on_disconnected* is awaited once when an identified
    connection is lost, before the reconnection loop draws its first delay;
    *on_reconnected* once when a reconnection succeeded. The URL and the
    password appear in no error, no frame and no trace.
    """

    def __init__(
        self,
        url: str,
        password: str,
        connect_timeout: float,
        request_timeout: float,
        *,
        websocket_factory: Callable[[str], Any],
        sleeper: Sleeper,
        clock: Callable[[], float],
        rng: Any,
        tasks: Any = None,
        on_disconnected: Notify | None = None,
        on_reconnected: Notify | None = None,
    ) -> None:
        self._url = url
        self._password = password
        self._connect_timeout = float(connect_timeout)
        self._request_timeout = float(request_timeout)
        self._factory = websocket_factory
        self._sleeper = sleeper
        self._clock = clock
        self._rng = rng
        self._tasks = tasks
        self.on_disconnected = on_disconnected
        self.on_reconnected = on_reconnected
        self._ws: Any = None
        self._connected = False
        self._closing = False
        self._sequence = 0
        self._pending: dict[str, asyncio.Future[Mapping[str, Any]]] = {}
        self._reader: asyncio.Future[Any] | None = None
        self._reconnect: asyncio.Future[Any] | None = None
        # Lost sockets whose close runs detached, awaited by ``close``.
        self._closing_sockets: set[asyncio.Future[Any]] = set()

    def __repr__(self) -> str:
        return f"{type(self).__name__}(connected={self._connected})"

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def reconnecting(self) -> bool:
        return self._reconnect is not None and not self._reconnect.done()

    # -- the handshake ------------------------------------------------------ #

    async def connect(self) -> None:
        """Open the socket and reach ``Identified``, within ``connect_timeout``."""

        if self._connected:
            return
        if self._closing:
            raise SceneProviderUnavailable("the scene provider is closed")
        attempt = asyncio.ensure_future(self._open_and_identify())
        timer = asyncio.ensure_future(self._sleeper(self._connect_timeout))
        try:
            await asyncio.wait({attempt, timer}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            _abandon(timer)
            if not attempt.done():
                _abandon(attempt)
        if not attempt.done():
            raise SceneProviderUnavailable("the scene provider did not identify in time")
        if attempt.cancelled() or attempt.exception() is not None:
            raise SceneProviderUnavailable("the scene provider refused the connection")
        ws = attempt.result()
        if self._closing:
            await _close_socket(ws)
            raise SceneProviderUnavailable("the scene provider is closed")
        self._ws = ws
        self._connected = True
        self._start_reader(ws)

    async def _open_and_identify(self) -> Any:
        created = self._factory(self._url)
        ws = await created if inspect.isawaitable(created) else created
        try:
            hello = await _receive_frame(ws)
            if hello.get("op") != _OP_HELLO:
                raise SceneProviderUnavailable("the scene provider did not say hello")
            data = hello.get("d") if isinstance(hello.get("d"), Mapping) else {}
            identify: dict[str, Any] = {"rpcVersion": RPC_VERSION, "eventSubscriptions": 0}
            challenge = data.get("authentication")
            if isinstance(challenge, Mapping):
                identify["authentication"] = authentication_string(
                    self._password,
                    str(challenge.get("salt", "")),
                    str(challenge.get("challenge", "")),
                )
            await ws.send_str(json.dumps({"op": _OP_IDENTIFY, "d": identify}))
            identified = await _receive_frame(ws)
            answer = identified.get("d") if isinstance(identified.get("d"), Mapping) else {}
            if (
                identified.get("op") != _OP_IDENTIFIED
                or answer.get("negotiatedRpcVersion") != RPC_VERSION
            ):
                raise SceneProviderUnavailable("the scene provider did not identify")
        except BaseException:
            await _close_socket(ws)
            raise
        return ws

    # -- the boundary ------------------------------------------------------- #

    async def list_scenes(self) -> list[str]:
        data = await self._read(REQUEST_LIST_SCENES)
        scenes = data.get("scenes")
        if isinstance(scenes, str) or not isinstance(scenes, Sequence):
            raise SceneRequestFailed(REQUEST_LIST_SCENES, "malformed")
        return [
            str(scene["sceneName"])
            for scene in scenes
            if isinstance(scene, Mapping) and isinstance(scene.get("sceneName"), str)
        ]

    async def current_scene(self) -> str:
        data = await self._read(REQUEST_CURRENT_SCENE)
        for key in ("currentProgramSceneName", "sceneName"):
            if isinstance(data.get(key), str):
                return data[key]
        raise SceneRequestFailed(REQUEST_CURRENT_SCENE, "malformed")

    async def set_scene(self, name: str) -> Mapping[str, Any]:
        """``{"result": True}`` or ``{"result": False, "code": n}`` — never the comment."""

        response = await self._request(REQUEST_SET_SCENE, {"sceneName": name})
        status = response.get("requestStatus")
        if isinstance(status, Mapping):
            if status.get("result") is True:
                return {"result": True}
            code = status.get("code")
            refused = status.get("result") is False
            if refused and isinstance(code, int) and not isinstance(code, bool):
                return {"result": False, "code": code}
        raise SceneRequestFailed(REQUEST_SET_SCENE, "malformed")

    async def _read(self, request_type: str) -> Mapping[str, Any]:
        response = await self._request(request_type)
        status = response.get("requestStatus")
        if not isinstance(status, Mapping) or status.get("result") is not True:
            code = status.get("code") if isinstance(status, Mapping) else "malformed"
            raise SceneRequestFailed(request_type, code)
        data = response.get("responseData")
        return data if isinstance(data, Mapping) else {}

    async def _request(
        self, request_type: str, data: Mapping[str, Any] | None = None
    ) -> Mapping[str, Any]:
        """One ``Request``, its ``RequestResponse`` matched by ``requestId``.

        Sending and answering are raced against one ``request_timeout``: a
        send stalled under backpressure is a lost answer like a silent peer.
        """

        ws = self._ws
        if not self._connected or ws is None:
            raise SceneProviderUnavailable("the scene provider is not connected")
        self._sequence += 1
        request_id = f"scene-{self._sequence}"
        answer: asyncio.Future[Mapping[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = answer
        request: dict[str, Any] = {"requestType": request_type, "requestId": request_id}
        if data is not None:
            request["requestData"] = dict(data)
        sending = asyncio.ensure_future(ws.send_str(json.dumps({"op": _OP_REQUEST, "d": request})))
        timer = asyncio.ensure_future(self._sleeper(self._request_timeout))
        try:
            waiting: set[asyncio.Future[Any]] = {sending, answer, timer}
            while not answer.done() and not timer.done():
                await asyncio.wait(waiting, return_when=asyncio.FIRST_COMPLETED)
                if sending in waiting and sending.done():
                    waiting.discard(sending)
                    if sending.cancelled() or sending.exception() is not None:
                        self._lost(ws)
                        raise SceneProviderUnavailable(
                            "the scene provider connection was lost"
                        ) from None
            if not answer.done():
                raise SceneRequestTimeout(f"{request_type} was not answered in time")
            return answer.result()
        finally:
            _abandon(timer)
            if not sending.done():
                _abandon(sending)
            else:
                _consume(sending)
            self._pending.pop(request_id, None)
            if not answer.done():
                answer.cancel()

    # -- the reader --------------------------------------------------------- #

    def _start_reader(self, ws: Any) -> None:
        """Watch *ws* until it is lost or the provider closes.

        Not a supervised task on purpose: the coordinator drains supervised
        tasks before the module drain hooks, and an idle watcher would make
        every shutdown wait out the whole drain budget. ``close`` stops it.
        """

        reader = asyncio.ensure_future(self._read_frames(ws))
        reader.add_done_callback(_consume)
        self._reader = reader

    async def _read_frames(self, ws: Any) -> None:
        """Dispatch answers until the socket ends or the provider closes."""

        try:
            while True:
                message = await ws.receive()
                kind = getattr(getattr(message, "type", None), "name", None)
                if kind in _WS_TERMINAL:
                    break
                if kind != "TEXT":
                    continue
                self._dispatch(message.data)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a failing socket is a lost one
            pass
        self._lost(ws)

    def _dispatch(self, text: Any) -> None:
        try:
            frame = json.loads(text)
        except (TypeError, ValueError):
            return
        if not isinstance(frame, Mapping) or frame.get("op") != _OP_REQUEST_RESPONSE:
            return
        data = frame.get("d")
        if not isinstance(data, Mapping):
            return
        answer = self._pending.get(data.get("requestId"))  # type: ignore[arg-type]
        if answer is not None and not answer.done():
            answer.set_result(data)

    # -- loss and reconnection ---------------------------------------------- #

    def _lost(self, ws: Any) -> None:
        """The socket *ws* is gone: fail every pending request, reconnect."""

        if ws is not self._ws:
            return
        self._ws = None
        self._connected = False
        self._fail_pending()
        if self._reader is not None and self._reader is not asyncio.current_task():
            self._reader.cancel()
        self._reader = None
        if self._closing:
            self._close_later(ws)
            return
        try:
            self._reconnect = self._spawn(self._reconnect_forever(ws), "scene-provider-reconnect")
        except Exception:  # noqa: BLE001 - a closed task registry: no reconnection
            self._close_later(ws)

    def _close_later(self, ws: Any) -> None:
        """Close *ws* detached; ``close`` waits for it."""

        closing = asyncio.ensure_future(_close_socket(ws))
        self._closing_sockets.add(closing)
        closing.add_done_callback(self._closing_sockets.discard)

    def _fail_pending(self) -> None:
        pending, self._pending = self._pending, {}
        for answer in pending.values():
            if not answer.done():
                answer.set_exception(
                    SceneProviderUnavailable("the scene provider connection was lost")
                )
                # A request already given up on must not log an unread error.
                answer.add_done_callback(_consume)

    async def _reconnect_forever(self, lost: Any) -> None:
        """Retry with full-jitter backoff until connected or closed."""

        await _close_socket(lost)
        await _notify(self.on_disconnected)
        attempt = 0
        while not self._closing:
            await self._sleeper(self.reconnect_delay(attempt))
            attempt += 1
            if self._closing:
                return
            try:
                await self.connect()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - the next attempt follows its delay
                continue
            await _notify(self.on_reconnected)
            return

    def reconnect_delay(self, attempt: int) -> float:
        """``rng() × min(30, 1 × 2ⁿ)`` for the *attempt*-th retry (from 0)."""

        exponent = min(attempt, 32)
        base = min(
            RECONNECT_MAX_SECONDS, RECONNECT_INITIAL_SECONDS * RECONNECT_MULTIPLIER**exponent
        )
        return float(self._rng.random()) * base

    def _spawn(self, coro: Awaitable[Any], name: str) -> asyncio.Future[Any]:
        spawn = getattr(self._tasks, "spawn", None)
        if callable(spawn):
            return spawn(coro, name=name)
        return asyncio.ensure_future(coro)

    async def close(self) -> None:
        """Stop the reconnection loop and the reader, close the socket."""

        self._closing = True
        current = asyncio.current_task()
        for task in (self._reconnect, self._reader):
            if task is not None and not task.done() and task is not current:
                task.cancel()
                await asyncio.wait({task})
        self._reconnect = None
        self._reader = None
        ws, self._ws = self._ws, None
        self._connected = False
        self._fail_pending()
        if ws is not None:
            await _close_socket(ws)
        if self._closing_sockets:
            await asyncio.wait(set(self._closing_sockets))


async def _receive_frame(ws: Any) -> Mapping[str, Any]:
    """The next text frame as JSON; the socket ending is an unreachable provider."""

    while True:
        message = await ws.receive()
        kind = getattr(getattr(message, "type", None), "name", None)
        if kind in _WS_TERMINAL:
            raise SceneProviderUnavailable("the scene provider closed the connection")
        if kind != "TEXT":
            continue
        try:
            frame = json.loads(message.data)
        except (TypeError, ValueError):
            raise SceneProviderUnavailable("the scene provider sent a malformed frame") from None
        if not isinstance(frame, Mapping):
            raise SceneProviderUnavailable("the scene provider sent a malformed frame")
        return frame


async def _close_socket(ws: Any) -> None:
    try:
        await ws.close()
    except Exception:  # noqa: BLE001 - a socket that fails to close is gone anyway
        pass


async def _notify(callback: Notify | None) -> None:
    if callback is None:
        return
    try:
        await callback()
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - a health report must not stop the loop
        return


class _ClientConnection:
    """The dialed socket and the client session that owns it, closed together."""

    __slots__ = ("_session", "_ws")

    def __init__(self, session: Any, ws: Any) -> None:
        self._session = session
        self._ws = ws

    @property
    def closed(self) -> bool:
        return bool(self._ws.closed)

    async def receive(self) -> Any:
        return await self._ws.receive()

    async def send_str(self, data: str) -> None:
        await self._ws.send_str(data)

    async def close(self) -> bool:
        try:
            return bool(await self._ws.close())
        finally:
            await self._session.close()


async def default_websocket_factory(url: str) -> _ClientConnection:
    """Dial *url* with ``aiohttp``; imported on first use only."""

    import aiohttp

    session = aiohttp.ClientSession()
    try:
        ws = await session.ws_connect(url, autoping=True)
    except BaseException:
        await session.close()
        raise
    return _ClientConnection(session, ws)


# --------------------------------------------------------------------------- #
# Activation
# --------------------------------------------------------------------------- #


async def activate(
    context: Any,
    settings: Mapping[str, Any],
    catalog: Mapping[str, Mapping[str, Any]],
) -> StreamControlModule:
    """Build the handle from the scoped runtime context.

    Settings are checked through the same hook the loader ran, so a handle
    built outside the loader is refused on the same terms. The seams
    ``_scene_provider``, ``_sleeper``, ``_websocket_factory`` and
    ``_wall_clock`` are read from *settings*; the sleeper defaults to
    ``asyncio.sleep``, the wall clock to ``time.time``. No transport
    is opened here.
    """

    del catalog  # Capabilities are declared by the colocated manifest.
    actions = getattr(context, "actions", None)
    if actions is None or not all(
        callable(getattr(actions, method, None))
        for method in ("declare", "register", "mark_ready", "mark_not_ready")
    ):
        raise StreamControlModuleError(f"{MODULE_NAME} activation: runtime context is invalid")
    if not isinstance(settings, Mapping):
        raise StreamControlModuleError(
            f"{MODULE_NAME} configuration: settings must be a mapping"
        )
    diagnostics = validate_settings(settings)
    if diagnostics:
        raise StreamControlModuleError(
            f"{MODULE_NAME} configuration: settings were refused "
            f"({len(diagnostics)} diagnostics)"
        )
    parsed = _Settings.from_mapping(settings)
    provider = settings.get(_SEAM_SCENE_PROVIDER) if parsed.kind == KIND_SCRIPTED else None
    return StreamControlModule(
        context,
        parsed,
        scene_provider=provider,
        sleeper=settings.get(_SEAM_SLEEPER, asyncio.sleep),
        websocket_factory=settings.get(_SEAM_WEBSOCKET_FACTORY),
        wall_clock=settings.get(_SEAM_WALL_CLOCK, time.time),
    )


def _declared_spec(action_name: str) -> ActionSpec:
    """Build *action_name*'s contract from the colocated manifest.

    The spec registered at ``prepare`` must equal the one the loader declared
    at discovery, field for field: reading the same file keeps the two from
    drifting, and the registry refuses a redeclaration that differs.
    """

    try:
        manifest = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))
        entry = next(
            item
            for item in manifest["actions"]
            if isinstance(item, Mapping) and item.get("name") == action_name
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
        )
    except Exception:
        raise StreamControlModuleError(
            f"{MODULE_NAME} prepare: manifest declaration of {action_name!r} is invalid"
        ) from None


__all__ = [
    "CAUSE_CONFIRMATION_LOST",
    "CLOSE_AUTHENTICATION_FAILED",
    "DEFAULT_CONNECT_TIMEOUT_SECONDS",
    "DEFAULT_MAX_WAITERS",
    "DEFAULT_REQUEST_TIMEOUT_SECONDS",
    "ERROR_PLATFORM_FORBIDDEN",
    "ERROR_PLATFORM_UNAVAILABLE",
    "ERROR_POLL_ACTIVE",
    "ERROR_POLL_REJECTED",
    "ERROR_PROVIDER_UNAVAILABLE",
    "ERROR_RESOURCE_BUSY",
    "ERROR_SCENE_NOT_ALLOWED",
    "ERROR_SCENE_NOT_APPLIED",
    "ERROR_SCENE_UNKNOWN",
    "FIELD_POLLS_ENABLED",
    "FIELD_SCENES_PROVIDER_URL",
    "KIND_NONE",
    "KIND_SCRIPTED",
    "KIND_WEBSOCKET",
    "MANIFEST_PATH",
    "MAX_POLL_ENTRIES_PER_CHANNEL",
    "MAX_SCENE_NAME_CHARS",
    "MODULE_NAME",
    "POLL_ACTION",
    "POLL_ENTRY_GRACE_SECONDS",
    "POLL_SERVICE_KIND",
    "POLL_STATE_ACTIVE",
    "PROVIDER_NAME",
    "REASON_NO_POLL_SERVICE",
    "REASON_SCENE_PROVIDER_DISCONNECTED",
    "REASON_SCENE_PROVIDER_UNREACHABLE",
    "RECONNECT_INITIAL_SECONDS",
    "RECONNECT_MAX_SECONDS",
    "RECONNECT_MULTIPLIER",
    "REQUEST_CURRENT_SCENE",
    "REQUEST_LIST_SCENES",
    "REQUEST_SET_SCENE",
    "RPC_VERSION",
    "SCENE_ACTION",
    "SCENE_PROVIDER_KINDS",
    "SCENE_UNKNOWN_CODE",
    "SceneProvider",
    "SceneProviderUnavailable",
    "SceneRequestFailed",
    "SceneRequestTimeout",
    "StreamControlModule",
    "StreamControlModuleError",
    "WebSocketSceneProvider",
    "activate",
    "authentication_string",
    "default_websocket_factory",
    "validate_settings",
]
