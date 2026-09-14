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

**Loop policy groups** (R1, R2, R3). Beside the limits, the settings carry
the three groups the agentic loop is configured by, parsed into
:class:`_Settings` by the same hook: ``budget`` gains the action-call cap,
the repeated-proposal cap and the delivery reserve; ``fallback`` says whether
and with what text a run that ends without an answer still delivers;
``capabilities.required`` lists the backend capabilities to verify at
``prepare`` (``structured_output`` mandatory, ``vision`` optional); and
``delivery`` holds the configured terminal step — a default delivery list
(``mode: fixed`` with its ``actions``, or ``mode: modules`` with a
``preference`` order) and per-destination ``overrides`` of the same shape.
The hook checks their shape only; what needs the discovered catalog — whether
an entry names a delivery-capable action and maps the text onto an existing
argument — is resolved at ``prepare``.

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

**Backend adapter** (R2). One :class:`_ModelAdapter` speaks the Chat
Completions format to the configured ``endpoint``, ``model`` and ``api_key``
and nothing else. The offered actions are sent as tool definitions (name,
description, argument schema); the answer is classified per decision 2 —
exactly one tool call is a proposal, text with no tool call the final
response, anything else an unsupported shape that ends the run ``error``
with 0 actions — and never guessed from prose. At ``prepare``, before the
readiness barrier, the adapter probes each capability of
``capabilities.required`` with one bounded request (decision 3): a forced
call on :data:`~core.contracts.PROBE_TOOL`, and the same with one 1×1 PNG
image part for ``vision``; a capability not verified stops startup with
``module 'brain': backend capability '<name>' not verified: <reason>`` —
never the key, the endpoint or a body — and leaves the module not ready. The
verified set is :attr:`BrainModule.verified_capabilities`. An ``image_ref``
part is read from the attachment store and encoded only while a request body
is built: the bytes exist in that body and nowhere else (AC12).

**The run** (R5, R8). One admitted work is exactly one model call and exactly
one delivery through the :class:`~core.actions.ActionExecutor`: the model's
final text becomes a single ``chat.write`` call whose explicit
:class:`~core.contracts.ActionObservation` is the delivery outcome (AC19). The
model is offered only the registry's *authorized* action view for the reply's
destination, read afresh for every run and sent as tools; a proposal is
classified but not executed by this single-turn body, which ends the run
explicitly (the agentic loop that executes proposals is the next step's).
The ``budget.max_tokens`` limit is the
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
import base64
import inspect
import json
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
from core.attachments import AttachmentExpired
from core.contracts import (
    BRAIN_ERROR_ATTACHMENT_EXPIRED,
    DELIVERY_NO_TEXT_ARGUMENT,
    PROBE_REASON_IMAGE_REJECTED,
    PROBE_REASON_MALFORMED_ARGUMENTS,
    PROBE_REASON_MULTIPLE_TOOL_CALLS,
    PROBE_REASON_NO_TOOL_CALL,
    PROBE_REASON_NON_SUCCESS_STATUS,
    PROBE_REASON_TIMED_OUT,
    PROBE_REASON_TRANSPORT_FAILED,
    PROBE_TOOL,
    RUN_FAILURE_UNSUPPORTED_RESPONSE_SHAPE,
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

CAPABILITY_STRUCTURED_OUTPUT = "structured_output"
CAPABILITY_VISION = "vision"
KNOWN_CAPABILITIES = frozenset({CAPABILITY_STRUCTURED_OUTPUT, CAPABILITY_VISION})
"""The backend capabilities ``capabilities.required`` may name (R2).

``structured_output`` is mandatory: the loop needs one tool call or a final
text per turn, so a list without it is refused at validation (AC10).
"""

DELIVERY_MODE_FIXED = "fixed"
DELIVERY_MODE_MODULES = "modules"
DELIVERY_MODES = frozenset({DELIVERY_MODE_FIXED, DELIVERY_MODE_MODULES})
"""How a configured delivery list is built (R1, decision 1): ``fixed`` takes
the configured ``actions``; ``modules`` derives the list from the enabled
modules' delivery-capable actions, ordered by ``preference``."""

_DELIVERY_ENTRY_KEYS = frozenset({"action", "text_argument", "arguments"})
_DELIVERY_LIST_KEYS = frozenset({"mode", "actions", "preference"})
_DELIVERY_GROUP_KEYS = _DELIVERY_LIST_KEYS | {"overrides"}
_FALLBACK_KEYS = frozenset({"enabled", "text"})
_CAPABILITIES_KEYS = frozenset({"required"})

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
        "max_action_calls": _COUNT,
        "max_repeated_actions": _COUNT,
        "delivery_reserve_seconds": _SECONDS,
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
    finite and positive (AC20), the admission wait never exceeding the total
    run deadline it is part of. When the entry point handed the accepted
    ``limits`` block over, every owned limit must also equal the accepted one
    (R6): a copy that differs is refused by field, so no bound this module
    builds can disagree with the configuration that was accepted.

    The three groups added by the agentic loop are checked here too, each
    field with one diagnostic naming it: ``fallback`` (``enabled`` a boolean,
    ``text`` a non-empty string), ``capabilities.required`` (a list of known
    capability names that contains ``structured_output``, AC10) and
    ``delivery`` (a known ``mode``, every fixed entry a mapping with a
    non-empty ``action`` and a well-typed text mapping, ``preference`` a
    list of names, every override keyed ``<platform>/<channel_id>`` and of
    the same shape, AC53). What only the discovered catalog can decide —
    whether an entry names a delivery-capable action, whether its text
    argument exists — is resolved at ``prepare``, not here.
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
    _validate_admission_window(settings.get("admission"), diagnostics)
    _validate_fallback(settings.get("fallback"), diagnostics)
    _validate_capabilities(settings.get("capabilities"), diagnostics)
    _validate_delivery(settings.get("delivery"), diagnostics)
    return diagnostics


def _validate_admission_window(section: Any, diagnostics: list[str]) -> None:
    """The queued wait is part of the total run deadline, so it cannot exceed it (R3).

    Compared only once both values are acceptable limits: an unevaluable one
    already has its own diagnostic, and a second on the same field would
    break the one-diagnostic-per-field rule (AC20).
    """

    if not isinstance(section, Mapping):
        return
    wait = section.get("wait_seconds")
    total = section.get("total_run_seconds")
    if _limit_reason(wait, _SECONDS) is not None or _limit_reason(total, _SECONDS) is not None:
        return
    if wait > total:
        diagnostics.append(
            _setting_diagnostic(
                "admission.wait_seconds", "must not exceed admission.total_run_seconds"
            )
        )


def _validate_fallback(section: Any, diagnostics: list[str]) -> None:
    """The generic fallback (R3): an explicit switch and a non-empty text."""

    if section is None:
        diagnostics.append(_setting_diagnostic("fallback", "is required"))
        return
    if not isinstance(section, Mapping):
        diagnostics.append(_setting_diagnostic("fallback", "must be a mapping"))
        return
    _refuse_unknown_keys(section, _FALLBACK_KEYS, "fallback", diagnostics)
    enabled = section.get("enabled")
    if enabled is None:
        diagnostics.append(_setting_diagnostic("fallback.enabled", "is required"))
    elif not isinstance(enabled, bool):
        diagnostics.append(_setting_diagnostic("fallback.enabled", "must be a boolean"))
    text = section.get("text")
    if text is None:
        diagnostics.append(_setting_diagnostic("fallback.text", "is required"))
    elif not isinstance(text, str) or not text.strip():
        diagnostics.append(
            _setting_diagnostic("fallback.text", "must be a non-empty string")
        )


def _validate_capabilities(section: Any, diagnostics: list[str]) -> None:
    """``capabilities.required``: known names only, ``structured_output`` among them (AC10)."""

    if section is None:
        diagnostics.append(_setting_diagnostic("capabilities", "is required"))
        return
    if not isinstance(section, Mapping):
        diagnostics.append(_setting_diagnostic("capabilities", "must be a mapping"))
        return
    _refuse_unknown_keys(section, _CAPABILITIES_KEYS, "capabilities", diagnostics)
    field_name = "capabilities.required"
    required = section.get("required")
    if required is None:
        diagnostics.append(_setting_diagnostic(field_name, "is required"))
    elif not _is_name_list(required):
        diagnostics.append(
            _setting_diagnostic(field_name, "must be a list of capability names")
        )
    elif CAPABILITY_STRUCTURED_OUTPUT not in required:
        diagnostics.append(
            _setting_diagnostic(field_name, f"must name {CAPABILITY_STRUCTURED_OUTPUT!r}")
        )
    elif not set(required) <= KNOWN_CAPABILITIES:
        diagnostics.append(
            _setting_diagnostic(
                field_name,
                "names a capability this module does not know "
                f"(known: {', '.join(sorted(KNOWN_CAPABILITIES))})",
            )
        )


def _validate_delivery(section: Any, diagnostics: list[str]) -> None:
    """The ``delivery`` group (R1, decision 1): a default list and its overrides.

    The shape is checked here, one diagnostic per offending field (AC53);
    whether the named actions exist, deliver and accept the text mapping is
    the catalog's to say and is resolved at ``prepare``. An empty fixed list
    is a resolution failure (reason ``empty``), not a shape defect.
    """

    if section is None:
        diagnostics.append(_setting_diagnostic("delivery", "is required"))
        return
    if not isinstance(section, Mapping):
        diagnostics.append(_setting_diagnostic("delivery", "must be a mapping"))
        return
    _refuse_unknown_keys(section, _DELIVERY_GROUP_KEYS, "delivery", diagnostics)
    _validate_delivery_list(section, "delivery", diagnostics)
    overrides = section.get("overrides")
    if overrides is None:
        return
    if not isinstance(overrides, Mapping):
        diagnostics.append(
            _setting_diagnostic("delivery.overrides", "must be a mapping keyed by destination")
        )
        return
    for key, override in overrides.items():
        prefix = f'delivery.overrides["{key}"]'
        if not _is_destination_key(key):
            diagnostics.append(
                _setting_diagnostic(prefix, "must be keyed '<platform>/<channel_id>'")
            )
        if not isinstance(override, Mapping):
            diagnostics.append(_setting_diagnostic(prefix, "must be a mapping"))
            continue
        _refuse_unknown_keys(override, _DELIVERY_LIST_KEYS, prefix, diagnostics)
        _validate_delivery_list(override, prefix, diagnostics)


def _validate_delivery_list(section: Mapping[str, Any], prefix: str, diagnostics: list[str]) -> None:
    """One configured delivery list: ``mode``, the fixed ``actions``, ``preference``."""

    mode = section.get("mode")
    if mode is None:
        diagnostics.append(_setting_diagnostic(f"{prefix}.mode", "is required"))
    elif not isinstance(mode, str) or mode not in DELIVERY_MODES:
        diagnostics.append(
            _setting_diagnostic(
                f"{prefix}.mode", f"must be one of {', '.join(sorted(DELIVERY_MODES))}"
            )
        )
    actions = section.get("actions")
    if actions is None:
        if mode == DELIVERY_MODE_FIXED:
            diagnostics.append(
                _setting_diagnostic(
                    f"{prefix}.actions", f"is required for mode {DELIVERY_MODE_FIXED!r}"
                )
            )
    elif not isinstance(actions, Sequence) or isinstance(actions, (str, bytes)):
        diagnostics.append(
            _setting_diagnostic(f"{prefix}.actions", "must be a list of delivery entries")
        )
    else:
        for index, entry in enumerate(actions):
            _validate_delivery_entry(entry, f"{prefix}.actions[{index}]", diagnostics)
    preference = section.get("preference")
    if preference is not None and not _is_name_list(preference):
        diagnostics.append(
            _setting_diagnostic(f"{prefix}.preference", "must be a list of action names")
        )


def _validate_delivery_entry(entry: Any, prefix: str, diagnostics: list[str]) -> None:
    """One fixed entry ``{action, text_argument?, arguments?}``."""

    if not isinstance(entry, Mapping):
        diagnostics.append(_setting_diagnostic(prefix, "must be a mapping"))
        return
    _refuse_unknown_keys(entry, _DELIVERY_ENTRY_KEYS, prefix, diagnostics)
    action = entry.get("action")
    if action is None:
        diagnostics.append(_setting_diagnostic(f"{prefix}.action", "is required"))
    elif not isinstance(action, str) or not action.strip():
        diagnostics.append(
            _setting_diagnostic(f"{prefix}.action", "must be a non-empty action name")
        )
    if "text_argument" in entry:
        text_argument = entry["text_argument"]
        if not isinstance(text_argument, str) or not text_argument.strip():
            diagnostics.append(
                _setting_diagnostic(
                    f"{prefix}.text_argument",
                    f"must be an argument name or {DELIVERY_NO_TEXT_ARGUMENT!r}",
                )
            )
    if "arguments" in entry and not isinstance(entry["arguments"], Mapping):
        diagnostics.append(
            _setting_diagnostic(f"{prefix}.arguments", "must be a mapping of arguments")
        )


def _refuse_unknown_keys(
    section: Mapping[str, Any], known: frozenset[str], prefix: str, diagnostics: list[str]
) -> None:
    for key in section:
        if key not in known:
            diagnostics.append(
                _setting_diagnostic(f"{prefix}.{key}", "is not a setting this module knows")
            )


def _is_name_list(value: Any) -> bool:
    return (
        isinstance(value, Sequence)
        and not isinstance(value, (str, bytes))
        and all(isinstance(item, str) and item.strip() for item in value)
    )


def _is_destination_key(key: Any) -> bool:
    if not isinstance(key, str):
        return False
    platform, separator, channel_id = key.partition("/")
    return bool(separator) and bool(platform.strip()) and bool(channel_id.strip())


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
    """Per-run budgets (R3). One model turn is spent per run here."""

    model_turns: int
    model_call_seconds: float
    action_seconds: float
    max_tokens: int
    max_observation_bytes: int
    max_action_calls: int
    max_repeated_actions: int
    delivery_reserve_seconds: float


@dataclass(frozen=True, slots=True)
class _MemoryLimits:
    max_sessions: int
    max_exchanges: int
    max_bytes: int
    max_age_seconds: float


@dataclass(frozen=True, slots=True)
class _Fallback:
    """The generic fallback (R3): whether it is delivered, and what text."""

    enabled: bool
    text: str


@dataclass(frozen=True, slots=True)
class _DeliveryEntry:
    """One configured entry of a fixed delivery list (R1, decision 1).

    ``text_argument`` is the argument the answer text is put under,
    :data:`~core.contracts.DELIVERY_NO_TEXT_ARGUMENT` for an effect-only
    entry, or ``None`` when the configuration says nothing and the action's
    declaration decides at resolution. ``arguments`` are the constants the
    entry always carries.
    """

    action: str
    text_argument: str | None
    arguments: Mapping[str, Any]

    @classmethod
    def from_mapping(cls, entry: Mapping[str, Any]) -> "_DeliveryEntry":
        text_argument = entry.get("text_argument")
        return cls(
            action=entry["action"].strip(),
            text_argument=None if text_argument is None else text_argument.strip(),
            arguments=dict(entry.get("arguments") or {}),
        )


@dataclass(frozen=True, slots=True)
class _DeliveryList:
    """One configured delivery list: how it is built and what it names.

    ``actions`` are the fixed entries (``mode: fixed``); ``preference`` the
    action names placed first by ``mode: modules``. Both are kept whatever
    the mode: resolution at ``prepare`` reads the one the mode calls for.
    """

    mode: str
    actions: tuple[_DeliveryEntry, ...]
    preference: tuple[str, ...]

    @classmethod
    def from_mapping(cls, section: Mapping[str, Any]) -> "_DeliveryList":
        return cls(
            mode=section["mode"],
            actions=tuple(
                _DeliveryEntry.from_mapping(entry) for entry in section.get("actions") or ()
            ),
            preference=tuple(name.strip() for name in section.get("preference") or ()),
        )


@dataclass(frozen=True, slots=True)
class _DeliveryConfig:
    """The ``delivery`` group: a default list and per-destination overrides.

    Overrides are keyed ``<platform>/<channel_id>``; a run uses the override
    of its destination when one is configured, else the default (R1).
    """

    default: _DeliveryList
    overrides: Mapping[str, _DeliveryList]

    @classmethod
    def from_mapping(cls, section: Mapping[str, Any]) -> "_DeliveryConfig":
        return cls(
            default=_DeliveryList.from_mapping(section),
            overrides={
                key: _DeliveryList.from_mapping(override)
                for key, override in (section.get("overrides") or {}).items()
            },
        )

    def for_destination(self, platform: str, channel_id: str) -> _DeliveryList:
        return self.overrides.get(f"{platform}/{channel_id}", self.default)


@dataclass(frozen=True, repr=False, slots=True)
class _Settings:
    endpoint: str
    model: str
    api_key: str
    admission: _AdmissionLimits
    budget: _Budget
    conversation_memory: _MemoryLimits
    fallback: _Fallback
    capabilities: frozenset[str]
    delivery: _DeliveryConfig

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
            fallback=_Fallback(
                enabled=settings["fallback"]["enabled"],
                text=settings["fallback"]["text"].strip(),
            ),
            capabilities=frozenset(settings["capabilities"]["required"]),
            delivery=_DeliveryConfig.from_mapping(settings["delivery"]),
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
        self._adapter = _ModelAdapter(
            session,
            settings,
            runtime.clock,
            runtime.attachments,
            reporter,
            sleeper=self._sleep,
        )
        #: The backend capabilities the prepare-time probe verified (R2);
        #: empty until ``prepare`` succeeded.
        self.verified_capabilities: frozenset[str] = frozenset()
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
        """Probe the backend, start the owned scheduler, register the consumer.

        The required capabilities are verified first (R2, decision 3): a
        capability the backend does not verify raises, reports the module
        ``degraded`` with the same value-free reason and leaves everything
        else untouched — no scheduler started, no consumer routed, no
        ``mark_ready`` — so startup stops naming the capability and no
        scenario runs. A scheduler shared through the context is started and
        closed by its owner. The consumer is routed before any producer may
        publish into it, and only then is the module marked past the barrier.
        """

        if self._prepared or self._closed:
            return
        try:
            self.verified_capabilities = await self._adapter.probe(self._settings.capabilities)
        except BrainModuleError as failure:
            await self._report_degraded(str(failure))
            raise
        if self._owns_scheduler:
            await self._scheduler.start()
        self._bus.subscribe(_INPUT_EVENT, self.handle_chat_message)
        self._actions.mark_ready()
        self._prepared = True

    async def _report_degraded(self, reason: str) -> None:
        """Report the module ``degraded`` with *reason* when the surface has it."""

        degraded = getattr(self._supervision, "degraded", None)
        if not callable(degraded):
            return
        try:
            await _resolve(degraded(reason=reason))
        except asyncio.CancelledError:
            raise
        except Exception:
            self._diagnose("brain health: degraded report failed")

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
        result = await self._adapter._request_model(
            prompt, budget_seconds=min(self._budget.model_call_seconds, run.remaining)
        )
        if result.failure is not None:
            return RunOutcome(
                status=_STATUS_TIMEOUT if result.failure == "timed_out" else _STATUS_ERROR,
                delivery=DELIVERY_NOT_ATTEMPTED,
                model_calls=1,
                correlation={"failure": result.failure, **result.usage()},
            )
        classification = result.classification
        if isinstance(classification, _Unsupported):
            self._diagnose(f"brain model response: unsupported shape: {classification.reason}")
            return RunOutcome(
                status=_STATUS_ERROR,
                delivery=DELIVERY_NOT_ATTEMPTED,
                model_calls=1,
                correlation={
                    "failure": RUN_FAILURE_UNSUPPORTED_RESPONSE_SHAPE,
                    "shape": classification.reason,
                    **result.usage(),
                },
            )
        if not isinstance(classification, _Final):
            # A proposal is classified here and executed by the agentic loop
            # (P12); this single-turn body has no turn to feed the observation
            # back into, so it ends the run explicitly rather than delivering
            # a tool call as text or dropping it silently.
            self._diagnose("brain model response: proposal not executed by the single-turn body")
            return RunOutcome(
                status=_STATUS_ERROR,
                delivery=DELIVERY_NOT_ATTEMPTED,
                model_calls=1,
                correlation={"failure": "proposal_not_executed", **result.usage()},
            )
        reply = classification.text

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

        The instructions, the offered tool definitions and the viewer's
        message are mandatory; when their estimate alone leaves the reply no
        room the prompt is not composed. The tools count as input because the
        backend reads them as such: a listing that used to be system text is
        no lighter for travelling as ``tools``. History is then added
        newest-first while the input stays within its share of the budget, so
        an old exchange is dropped before the reply's room is — and what the
        input leaves is the output cap the request carries.
        """

        system = {"role": "system", "content": self._system_prompt()}
        user = {"role": "user", "content": user_content}
        tools = self._offered_tools(destination)
        limit = self._budget.max_tokens
        input_tokens = (
            _estimate_tokens(system["content"])
            + _estimate_tokens(user_content)
            + _estimate_tool_tokens(tools)
        )
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
            tools=tools,
            input_tokens=input_tokens,
            output_tokens=limit - input_tokens,
        )

    def _system_prompt(self) -> str:
        """The instructions. The offered actions travel as tools, not as text.

        No ``[send:`` tag and no action listing: what the model may call is
        the request's ``tools`` list (R2), and the reply is delivered by the
        runtime, never by an encoding of the text.
        """

        return "\n".join(
            (
                "You are a live-stream chat companion.",
                "Reply to the viewer's message with one short plain-text chat message.",
                "Write the message body only: no tags, no markup, no instructions.",
            )
        )

    def _offered_tools(self, destination: Destination) -> tuple[Any, ...]:
        """The registry's authorized view for this destination, as specs.

        Read through the scoped facade the runtime hands every module, so the
        rules in force reach the model in production and not only under an
        injected registry, and afresh every run: an action authorized at the
        start of a previous run is no permission now (R5). A view that cannot
        be read offers nothing: default deny expressed as a surface rather
        than a guess about the rules. The specs become the request's tool
        definitions at request time.
        """

        try:
            view = self._actions.authorized(principal=PRINCIPAL, destination=destination)
        except Exception:
            self._diagnose("brain actions: authorized view unavailable")
            return ()
        offered = [
            spec
            for _name, spec in sorted(dict(view).items(), key=lambda item: str(item[0]))
            if _is_text(getattr(spec, "name", None))
            and isinstance(getattr(spec, "argument_schema", None), Mapping)
        ]
        return tuple(offered)

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


# --------------------------------------------------------------------------- #
# The model adapter (R2)
# --------------------------------------------------------------------------- #

_PART_TEXT = "text"
_PART_IMAGE_REF = "image_ref"
_PART_IMAGE_URL = "image_url"

_TOOL_CHOICE_AUTO = "auto"

# The 1×1 opaque PNG the image probe carries (decision 3): constant bytes, so
# the probe needs no store and no capture, and the one image part it sends is
# the same on every activation.
_PROBE_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753"
    "de0000000c49444154789c63606060000000040001f61738550000000049454e"
    "44ae426082"
)
_PROBE_MAX_TOKENS = 256
_PROBE_INSTRUCTION = f"Call the {PROBE_TOOL} tool with ok set to true."
_PROBE_TOOL_SPEC: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": PROBE_TOOL,
        "description": "Confirms that the backend performs a forced tool call.",
        "parameters": {
            "type": "object",
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
        },
    },
}
_PROBE_TOOL_CHOICE: dict[str, Any] = {
    "type": "function",
    "function": {"name": PROBE_TOOL},
}

# Why a response shape is unsupported (decision 2). ``malformed_body`` covers a
# 2xx body that is not a Chat Completions message at all — no choice, no
# message — which is "neither a tool call nor text" as much as an empty one.
_UNSUPPORTED_MULTIPLE_TOOL_CALLS = "multiple_tool_calls"
_UNSUPPORTED_TOOL_CALL_WITH_TEXT = "tool_call_with_text"
_UNSUPPORTED_MALFORMED_TOOL_CALL = "malformed_tool_call"
_UNSUPPORTED_NO_CONTENT = "no_content"
_UNSUPPORTED_MALFORMED_BODY = "malformed_body"

# Failures of a request whose image part could not be read from the store.
_FAILURE_ATTACHMENT_UNAVAILABLE = "attachment_unavailable"


@dataclass(frozen=True, slots=True)
class _Proposal:
    """Exactly one tool call: an action proposal (decision 2).

    ``raw_arguments`` is what the backend sent — a JSON string as the format
    specifies, or whatever else it put there — decoded by the consumer, so a
    proposal whose arguments are not a JSON object is still a proposal here
    and a ``malformed_arguments`` observation there.
    """

    name: str
    raw_arguments: Any


@dataclass(frozen=True, slots=True)
class _Final:
    """Text content and no tool call: the final response (decision 2)."""

    text: str


@dataclass(frozen=True, slots=True)
class _Unsupported:
    """Anything else: ≥ 2 tool calls, a tool call with text, neither."""

    reason: str


@dataclass(frozen=True, slots=True)
class _Prompt:
    """The composed request: messages, offered tools, the token room left.

    ``messages`` may carry list content whose parts are ``text`` or
    ``image_ref`` (an :class:`~core.contracts.ActionObservation` part, by
    ``attachment_id``); the reference is all this object ever holds — the
    bytes are read and encoded by the adapter for the request and nowhere
    else (R2, AC12). ``tools`` are the offered action specs, rendered as tool
    definitions at request time.
    """

    messages: list[dict[str, Any]]
    tools: tuple[Any, ...] = ()
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True, slots=True)
class _ModelReply:
    """One model call's result: a classified reply or a failure, with usage.

    ``tokens`` is the run's total as the backend reported it, or as it was
    estimated when the backend reported none — ``estimated`` says which, so a
    trace never presents an estimate as a measurement. It is ``None`` when the
    call ended with nothing to count.
    """

    classification: _Proposal | _Final | _Unsupported | None = None
    tokens: int | None = None
    estimated: bool = False
    failure: str | None = None

    def usage(self) -> dict[str, Any]:
        """The correlation entries this result contributes to the outcome."""

        if self.tokens is None:
            return {}
        return {"tokens": self.tokens, "tokens_estimated": self.estimated}


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


class _ImageUnavailable(Exception):
    """An ``image_ref`` part the store could not serve at request time."""

    def __init__(self, failure: str) -> None:
        super().__init__(failure)
        self.failure = failure


class _ModelAdapter:
    """The one Chat Completions adapter (R2): requests, probe, classification.

    It speaks to the configured endpoint and nothing else. A request is
    ``{model, messages, tools[], tool_choice, max_tokens}``: the offered
    actions become function tools, an ``image_ref`` part is resolved from the
    attachment store and encoded as a data URL **while the request body is
    built** — the encoded string is a local of that request, never copied
    into a trace, an event, a diagnostic or the prompt object (AC12). The
    answer is classified per decision 2 and never falls back: a backend that
    ignored a forced ``tool_choice`` and answered in prose is ``no_tool_call``
    at the probe and ``_Final`` in a run, not a proposal guessed from text.

    Every failure path is value-free: exception text and response bodies can
    contain the key or prompt content, so neither is copied anywhere.
    """

    __slots__ = (
        "_attachments",
        "_budget",
        "_clock",
        "_reporter",
        "_session",
        "_settings",
        "_sleep",
    )

    def __init__(
        self,
        session: Any,
        settings: _Settings,
        clock: Callable[[], float],
        attachments: Any | None,
        reporter: Callable[[str], None],
        *,
        sleeper: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._session = session
        self._settings = settings
        self._budget = settings.budget
        self._clock = clock
        self._attachments = attachments
        self._reporter = reporter
        self._sleep: Callable[[float], Awaitable[None]] = (
            sleeper if sleeper is not None else asyncio.sleep
        )

    # -- classification (decision 2) --------------------------------------- #

    @staticmethod
    def classify(body: Any) -> _Proposal | _Final | _Unsupported:
        """Classify one 2xx body; see :func:`_classify`."""

        return _classify(body)

    # -- the prepare-time probe (decision 3) ------------------------------- #

    async def probe(self, required: Any) -> frozenset[str]:
        """Verify every *required* capability with one bounded request each.

        The forced call on :data:`~core.contracts.PROBE_TOOL` verifies
        ``structured_output``; the same request carrying one 1×1 PNG image
        part verifies ``vision``, and is only sent once the text probe
        passed. The first capability not verified raises
        :class:`BrainModuleError` naming it and the reason (R2), a message
        built from constants alone — never the key, the endpoint or a body.
        Returns the verified set.
        """

        verified: set[str] = set()
        if CAPABILITY_STRUCTURED_OUTPUT in required:
            await self._verify(CAPABILITY_STRUCTURED_OUTPUT, image=False)
            verified.add(CAPABILITY_STRUCTURED_OUTPUT)
        if CAPABILITY_VISION in required:
            await self._verify(CAPABILITY_VISION, image=True)
            verified.add(CAPABILITY_VISION)
        return frozenset(verified)

    async def _verify(self, capability: str, *, image: bool) -> None:
        reason = await self._probe_reason(image=image)
        if reason is not None:
            self._diagnose(f"brain probe: capability '{capability}' not verified: {reason}")
            raise BrainModuleError(
                f"module '{MODULE_NAME}': backend capability '{capability}' "
                f"not verified: {reason}"
            )

    async def _probe_reason(self, *, image: bool) -> str | None:
        """One probe request; ``None`` when it verified, else the R2 reason."""

        content: Any = _PROBE_INSTRUCTION
        if image:
            content = [
                {"type": _PART_TEXT, "text": _PROBE_INSTRUCTION},
                {"type": _PART_IMAGE_URL, "image_url": {"url": _data_url("image/png", _PROBE_PNG)}},
            ]
        body = {
            "model": self._settings.model,
            "messages": [{"role": "user", "content": content}],
            "tools": [_PROBE_TOOL_SPEC],
            "tool_choice": _PROBE_TOOL_CHOICE,
            "max_tokens": _PROBE_MAX_TOKENS,
        }
        try:
            answer = await self._bounded(self._post(body), self._budget.model_call_seconds)
        except asyncio.CancelledError:
            raise
        except (asyncio.TimeoutError, TimeoutError):
            return PROBE_REASON_TIMED_OUT
        except _NonSuccessResponse:
            return PROBE_REASON_NON_SUCCESS_STATUS
        except _MalformedResponse:
            # A 2xx that is not JSON is not the expected shape: no tool call.
            return PROBE_REASON_NO_TOOL_CALL
        except Exception:
            return PROBE_REASON_TRANSPORT_FAILED

        rejected = PROBE_REASON_IMAGE_REJECTED if image else PROBE_REASON_NO_TOOL_CALL
        if image and _names_image_input(answer):
            return PROBE_REASON_IMAGE_REJECTED
        classification = _classify(answer)
        if isinstance(classification, _Proposal):
            if classification.name != PROBE_TOOL:
                return rejected
            if _decode_arguments(classification.raw_arguments) is None:
                return PROBE_REASON_MALFORMED_ARGUMENTS
            return None
        if (
            isinstance(classification, _Unsupported)
            and classification.reason == _UNSUPPORTED_MULTIPLE_TOOL_CALLS
        ):
            return PROBE_REASON_MULTIPLE_TOOL_CALLS
        # Prose, nothing, a call with text: the forced call was not honoured.
        # For the image probe, sent only after the text probe passed, the
        # image part is the one thing that changed, so it is what was refused.
        return rejected

    # -- the run's model call ---------------------------------------------- #

    async def _request_model(self, prompt: _Prompt, *, budget_seconds: float) -> _ModelReply:
        """One request bounded in time and in tokens (§3.4).

        The room the prompt leaves inside ``budget.max_tokens`` is sent as the
        request's ``max_tokens``; the run's total is then read from the
        backend's ``usage`` when it reports one and estimated — and reported
        as estimated — when it does not. A reply that puts the total over the
        bound is a failure, not a longer answer. The time bound runs on the
        injected sleeper, so a held backend times out on the clock the run
        is scheduled by.
        """

        try:
            body = self._request_body(prompt)
        except _ImageUnavailable as unavailable:
            self._diagnose("brain model request: image attachment unavailable")
            return _ModelReply(failure=unavailable.failure)
        try:
            answer = await self._bounded(self._post(body), budget_seconds)
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
        except Exception:
            self._diagnose("brain model request: transport failed")
            return _ModelReply(failure="transport_failed")
        finally:
            # The encoded image, if any, lived in this body and nowhere else.
            del body

        classification = _classify(answer)
        if isinstance(classification, _Unsupported) and (
            classification.reason == _UNSUPPORTED_MALFORMED_BODY
        ):
            self._diagnose("brain model response: malformed data")
            return _ModelReply(failure="malformed_response")
        reported = _response_usage(answer)
        estimated = reported is None
        tokens = (
            prompt.input_tokens + _estimate_tokens(_classification_text(classification))
            if reported is None
            else reported
        )
        if tokens > self._budget.max_tokens:
            self._diagnose("brain model response: token budget exceeded")
            return _ModelReply(
                failure="token_budget_exceeded", tokens=tokens, estimated=estimated
            )
        return _ModelReply(classification=classification, tokens=tokens, estimated=estimated)

    def _request_body(self, prompt: _Prompt) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self._settings.model,
            "messages": [self._encode_message(message) for message in prompt.messages],
            "max_tokens": prompt.output_tokens,
        }
        if prompt.tools:
            body["tools"] = [_tool_definition(spec) for spec in prompt.tools]
            body["tool_choice"] = _TOOL_CHOICE_AUTO
        return body

    def _encode_message(self, message: Mapping[str, Any]) -> dict[str, Any]:
        content = message.get("content")
        if not isinstance(content, (list, tuple)):
            return dict(message)
        return {**message, "content": [self._encode_part(part) for part in content]}

    def _encode_part(self, part: Any) -> Any:
        if not isinstance(part, Mapping):
            return part
        kind = part.get("type")
        if kind == _PART_TEXT:
            return {"type": _PART_TEXT, "text": part.get("text", "")}
        if kind == _PART_IMAGE_REF:
            content_type, data = self._resolve_image(part.get("attachment_id"))
            return {"type": _PART_IMAGE_URL, "image_url": {"url": _data_url(content_type, data)}}
        return dict(part)

    def _resolve_image(self, attachment_id: Any) -> tuple[str, bytes]:
        """The bytes behind an ``image_ref``, read from the store now (R2).

        Read at request time and never earlier: a lease that expired since
        the observation was appended is ``attachment_expired`` here, not a
        stale copy sent anyway. A store that does not hold the reference —
        no store, released, never issued — is ``attachment_unavailable``.
        """

        store = self._attachments
        if store is None or not isinstance(attachment_id, str):
            raise _ImageUnavailable(_FAILURE_ATTACHMENT_UNAVAILABLE)
        try:
            ref = store.lookup(attachment_id)
        except Exception:
            raise _ImageUnavailable(_FAILURE_ATTACHMENT_UNAVAILABLE) from None
        if ref is None:
            raise _ImageUnavailable(_FAILURE_ATTACHMENT_UNAVAILABLE)
        if self._clock() >= ref.expires_at:
            raise _ImageUnavailable(BRAIN_ERROR_ATTACHMENT_EXPIRED)
        try:
            data = store.get(ref)
        except AttachmentExpired:
            raise _ImageUnavailable(BRAIN_ERROR_ATTACHMENT_EXPIRED) from None
        except Exception:
            raise _ImageUnavailable(_FAILURE_ATTACHMENT_UNAVAILABLE) from None
        if not isinstance(data, (bytes, bytearray)):
            raise _ImageUnavailable(_FAILURE_ATTACHMENT_UNAVAILABLE)
        return str(ref.content_type), bytes(data)

    # -- the wire ---------------------------------------------------------- #

    async def _post(self, body: Mapping[str, Any]) -> Any:
        """POST *body*; the decoded 2xx answer, else a typed failure.

        The response is released whatever happens — a cancellation mid-read
        included — so a held connection never outlives the request.
        """

        response: Any | None = None
        try:
            response = await _resolve(
                self._session.post(
                    self._settings.endpoint,
                    headers={
                        "Authorization": f"Bearer {self._settings.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=body,
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
                return await _resolve(response.json())
            except asyncio.CancelledError:
                raise
            except (asyncio.TimeoutError, TimeoutError):
                raise
            except Exception:
                raise _MalformedResponse from None
        finally:
            if response is not None:
                await _release_response(response)

    async def _bounded(self, request: Awaitable[Any], seconds: float) -> Any:
        """Await *request* for at most *seconds* on the injected sleeper.

        The request runs as its own task and is cancelled when the timer
        wins, so its ``finally`` releases the response; a cancellation of
        the caller cancels both and propagates. A cancellation only schedules
        that cleanup, so the cancelled tasks are drained before this returns
        or raises: the request's release has run — and its outcome has been
        retrieved — by the time the adapter reports a timeout or unwinds, and
        a shutdown that follows never closes the session under a request that
        still owns its response.
        """

        task = asyncio.ensure_future(request)
        timer = asyncio.ensure_future(self._sleep(max(float(seconds), 0.0)))
        try:
            done, _pending = await asyncio.wait(
                {task, timer}, return_when=asyncio.FIRST_COMPLETED
            )
        except asyncio.CancelledError:
            await _drain(task, timer)
            raise
        if task in done:
            await _drain(timer)
            return task.result()
        await _drain(task, timer)
        raise TimeoutError

    def _diagnose(self, message: str) -> None:
        _safe_report(self._reporter, message)


def _classify(body: Any) -> _Proposal | _Final | _Unsupported:
    """Classify one 2xx Chat Completions body (decision 2).

    Exactly one tool call and no text → :class:`_Proposal`; text and no
    tool call → :class:`_Final`; two or more tool calls, a tool call with
    text, or neither → :class:`_Unsupported` naming which. Blank text is no
    text and a ``null`` or empty ``tool_calls`` is no call. Nothing here
    guesses: a body that is not a message is unsupported, not a reply.
    """

    try:
        message = body["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        return _Unsupported(_UNSUPPORTED_MALFORMED_BODY)
    if not isinstance(message, Mapping):
        return _Unsupported(_UNSUPPORTED_MALFORMED_BODY)
    raw_calls = message.get("tool_calls")
    calls = list(raw_calls) if isinstance(raw_calls, (list, tuple)) else []
    text = _message_text(message.get("content"))
    if len(calls) >= 2:
        return _Unsupported(_UNSUPPORTED_MULTIPLE_TOOL_CALLS)
    if len(calls) == 1:
        if text.strip():
            return _Unsupported(_UNSUPPORTED_TOOL_CALL_WITH_TEXT)
        call = calls[0]
        function = call.get("function") if isinstance(call, Mapping) else None
        name = function.get("name") if isinstance(function, Mapping) else None
        if not _is_text(name):
            return _Unsupported(_UNSUPPORTED_MALFORMED_TOOL_CALL)
        return _Proposal(name=name.strip(), raw_arguments=function.get("arguments"))
    if text.strip():
        return _Final(text=text)
    return _Unsupported(_UNSUPPORTED_NO_CONTENT)


def _message_text(content: Any) -> str:
    """The text of a message: a string, or the text parts of a list."""

    if isinstance(content, str):
        return content
    if isinstance(content, (list, tuple)):
        return "".join(
            part["text"]
            for part in content
            if isinstance(part, Mapping)
            and part.get("type") == _PART_TEXT
            and isinstance(part.get("text"), str)
        )
    return ""


def _classification_text(classification: _Proposal | _Final | _Unsupported) -> str:
    """What the reply cost, for the estimate a missing ``usage`` falls to."""

    if isinstance(classification, _Final):
        return classification.text
    if isinstance(classification, _Proposal):
        raw = classification.raw_arguments
        return classification.name + (raw if isinstance(raw, str) else json.dumps(raw, default=str))
    return ""


def _decode_arguments(raw: Any) -> Mapping[str, Any] | None:
    """The JSON object a tool call's arguments decode to, else ``None``."""

    if isinstance(raw, Mapping):
        return raw
    if not isinstance(raw, str):
        return None
    try:
        decoded = json.loads(raw)
    except ValueError:
        return None
    return decoded if isinstance(decoded, Mapping) else None


def _names_image_input(answer: Any) -> bool:
    """Whether a 2xx body's ``error`` names the image input (R2)."""

    error = answer.get("error") if isinstance(answer, Mapping) else None
    if error is None:
        return False
    if isinstance(error, Mapping):
        rendered = " ".join(
            str(value) for value in (error.get("message"), error.get("code"), error.get("type"))
            if value is not None
        )
    else:
        rendered = str(error)
    lowered = rendered.lower()
    return "image" in lowered or "vision" in lowered


def _tool_definition(spec: Any) -> dict[str, Any]:
    """One offered action as a Chat Completions function tool."""

    description = getattr(spec, "description", "")
    return {
        "type": "function",
        "function": {
            "name": str(spec.name),
            "description": description if isinstance(description, str) else "",
            "parameters": _json_plain(getattr(spec, "argument_schema", {})),
        },
    }


def _json_plain(value: Any) -> Any:
    """A JSON-serialisable copy of a frozen contract mapping."""

    if isinstance(value, Mapping):
        return {str(key): _json_plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_plain(item) for item in value]
    return value


def _data_url(content_type: str, data: bytes) -> str:
    return f"data:{content_type};base64,{base64.b64encode(data).decode('ascii')}"


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


def _estimate_tool_tokens(tools: Sequence[Any]) -> int:
    """A conservative token count of the offered tool definitions.

    Each definition is measured as the backend receives it — name,
    description and argument schema serialised — so a large schema weighs
    on the input estimate the way it weighs on the request. Nothing offered
    costs nothing.
    """

    return sum(
        _estimate_tokens(
            json.dumps(_tool_definition(spec), ensure_ascii=False, sort_keys=True, default=str)
        )
        for spec in tools
    )


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #


async def _drain(*tasks: asyncio.Future[Any]) -> None:
    """Cancel *tasks* and wait until each has finished unwinding.

    ``cancel()`` schedules the cancellation; the task's ``finally`` runs on
    a later loop turn. Waiting for it here is what makes a cancelled request
    finished — its response released — rather than merely doomed. The
    results are retrieved so an exception raised during cleanup is not left
    as an unretrieved-exception warning at garbage collection.
    """

    for task in tasks:
        task.cancel()
    pending = [task for task in tasks if not task.done()]
    if pending:
        await asyncio.wait(pending)
    for task in tasks:
        if not task.cancelled():
            task.exception()


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
