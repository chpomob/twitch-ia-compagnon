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
trigger admits the normalised event itself (the shipped input does) — so the
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

**The run** (R1, R2, R3, R4, R5, R8). One admitted work is one agentic
loop and one terminal delivery step through the
:class:`~core.actions.ActionExecutor`. Each turn offers the model a bounded
:class:`_Transcript` — instructions, the viewer's message, the retained
memory, then one assistant tool call and one text tool result per
previous proposal, an observation's ``image_ref`` parts following their
tool result in a ``user`` message since a ``tool`` message carries text
only — and, as tools, the ``read`` actions of the registry's
*authorized* view for the run's destination, re-read every turn, **per
action and per declared scope** (:meth:`BrainModule._offered_tools`): a
capture bound on ``*/*/capture`` is offered by its own scope, a delivery
action is never offered, whatever the grants (decision 1). The model
answers with one proposal or a final response (decision 2). A proposal
naming an action absent from the catalog, or of any nature but ``read``
— the delivery action included — is refused by this module itself
(``unknown_action``, ``not_a_read_action``), and one whose arguments are
not a JSON object is a synthetic ``malformed_arguments`` error, each with
0 executor calls; every other proposal becomes one
:class:`~core.contracts.ActionCall` — destination ``(run platform, run
channel_id, the action's declared scope)``, principal :data:`PRINCIPAL`,
identities and deadline set here — whose observation, text parts and
``image_ref`` parts alike, is appended to the transcript and traced as
``brain.run.observation`` (references and dimensions only, AC12). The
budgets of R3 are acted at their own boundaries — ``model_turns`` before
a model call, ``max_repeated_actions`` and ``max_action_calls`` before a
proposal is executed, ``max_tokens`` cumulatively on every reply and on
the prompt, ``max_observation_bytes`` on every adopted observation — each
ending the run ``error`` with ``failure: budget_exhausted`` and
``budget: <name>``; a model call over ``model_call_seconds`` ends the run
``timeout``; an action over ``action_seconds`` is the executor's own
observation fed back to the model. An ``image_ref`` in a run whose
verified capabilities lack ``vision`` ends the run ``capability_missing``
before any request; one whose lease expired before its model turn ends it
``attachment_expired``. The final response is the terminal step: the
text is handed to every entry of the run's resolved delivery list, in
order, one executor call per entry with call ids continuing the run's
counter (``<run_id>/call-<n>``), each with its own explicit
:class:`~core.contracts.ActionObservation` (AC19, AC50). Every image the
run leased is released at its end on every exit path — the loop discards
what the transcript references, and the scheduler's ``run_cleanup``
releases the run — and the transcript is a local of the run. There is no
tagged output, no bus republication and no second path: the compatibility
``channel.chat.send`` route stays available to other producers on the
same send service, and this module never uses it. The run body returns a
:class:`~core.admission.RunOutcome` — terminal state, delivery outcome
reported separately, model-call count and correlation (``turns``,
``action_calls``, ``tokens``, ``tokens_estimated``, ``fallback``,
``deliveries``, ``delivery``, ``failure``, ``budget``, ``capability``) —
and publishes **neither** ``brain.run.started`` **nor**
``brain.run.completed``: the scheduler is the single emission owner of
both and merges the outcome into its traced payload. The per-call
terminal trace (``action.completed``) is the executor's.

**Delivery list** (R1, decision 1). Which actions deliver is policy, not
code: the ``delivery`` settings group names them, and :func:`_resolve_delivery`
turns the default list and every override into resolved entries at
``prepare``, against the discovered catalog — the manifest declarations the
loader recorded before any module prepares — and before the readiness
barrier. A ``fixed`` entry must name a discovered action carrying the
delivery capability and map the answer text onto the declared argument (or
onto none, for an effect-only entry); a ``modules`` list is every
delivery-capable action of the catalog, the ``preference`` names first, the
rest in catalog order, unknown preference names ignored. A list that does
not resolve stops startup with ``module 'brain': delivery: <entry>:
<reason>`` and no scenario runs; the resolved lists are published once as
``brain.delivery.resolved``. At the terminal step every entry is invoked
whatever the outcome of the previous ones, each counting one action call
under the run's own call ids, and the record carries one outcome per entry
(``deliveries``) beside their summary (``delivery``): the single entry's
status, else ``success`` when all succeeded, else the first non-success in
list order. Nothing here names a platform or a delivery module (AC56).

**Conversation memory** (R3, R6). Keyed by
:class:`~core.contracts.SessionKey` ``(platform, channel_id, viewer_id)``, so
one viewer on two platforms or in two channels holds two memories and two
viewers in one channel never share one (AC10). It is bounded in sessions,
exchanges per session, bytes per session and age against the injected clock,
evicting oldest first on every axis (AC30). Only a confirmed delivery of the
text writes an exchange back: memory is written when at least one entry
received the answer text and every entry that did ended ``success``; a
refused, failed, timed-out or ``external_unknown`` text delivery leaves the
memory untouched, so the model is never told it said something it did not
(R5, AC7).
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
from dataclasses import dataclass, field, fields
from typing import Any
from urllib.parse import urlsplit

from core.admission import AdmissionScheduler, RunOutcome, Work
from core.attachments import AttachmentExpired
from core.contracts import (
    BRAIN_ERROR_ATTACHMENT_EXPIRED,
    BRAIN_ERROR_MALFORMED_ARGUMENTS,
    BRAIN_ERROR_NOT_A_READ_ACTION,
    BRAIN_ERROR_OBSERVATION_TOO_LARGE,
    BRAIN_ERROR_UNKNOWN_ACTION,
    DELIVERY_NO_TEXT_ARGUMENT,
    DELIVERY_REASON_EMPTY,
    DELIVERY_REASON_NOT_A_DELIVERY,
    DELIVERY_REASON_TEXT_MAPPING_AMBIGUOUS,
    DELIVERY_REASON_TEXT_MAPPING_MISSING,
    DELIVERY_REASON_UNKNOWN_ACTION,
    PROBE_REASON_IMAGE_REJECTED,
    PROBE_REASON_MALFORMED_ARGUMENTS,
    PROBE_REASON_MULTIPLE_TOOL_CALLS,
    PROBE_REASON_NO_TOOL_CALL,
    PROBE_REASON_NON_SUCCESS_STATUS,
    PROBE_REASON_TIMED_OUT,
    PROBE_REASON_TRANSPORT_FAILED,
    PROBE_TOOL,
    RUN_FAILURE_BUDGET_EXHAUSTED,
    RUN_FAILURE_CAPABILITY_MISSING,
    RUN_FAILURE_UNSUPPORTED_RESPONSE_SHAPE,
    TERMINAL_STATUSES,
    WILDCARD,
    ActionCall,
    ActionObservation,
    Destination,
    SessionKey,
    observation_size,
)
# The canonical rendering the policy content hash and the trace size run on:
# a repeated proposal is judged on the same unambiguous rendering of its
# arguments (R3), never on a serialiser's insertion order.
from core.contracts import _canonical_text

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

CHAT_SCOPE = "chat"
"""The scope a declaration open to every scope (``*``) is addressed as: the
reply's own. Every other destination the loop builds takes its scope from
the action's declaration (R1) — the offered tools per declared scope, the
proposal's call, the delivery entries — never from a literal held here."""

DELIVERY_NOT_ATTEMPTED = "not_attempted"
"""The delivery outcome of a run that ended before any executor call."""

FALLBACK_NONE = "none"
"""The ``fallback`` value of a run that delivered no fallback text (R3)."""

WORK_KIND = "chat.message"

TRACE_DELIVERY_RESOLVED = "brain.delivery.resolved"
"""The one trace ``prepare`` publishes: the resolved delivery lists (R1)."""

TRACE_OBSERVATION = "brain.run.observation"
"""The trace the loop publishes for every proposal it classified (R1, R4).

Correlation, the action, the call id (``null`` for a synthetic observation
the executor never saw), the observation's status and error code, and one
summary per part: a ``text`` part as its byte count, an ``image_ref`` part
as ``attachment_id``, ``content_type``, ``size``, ``width`` and ``height``
(AC12). Never the text itself, never a payload, never a path.
"""

_INPUT_EVENT = "channel.chat.message"
_STATUS_SUCCESS = "success"
_STATUS_REFUSED = "refused"
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

# What one ``image_ref`` part is estimated to cost when the backend reports
# no usage: a flat, deliberately high count, since nothing in the bytes says
# how a backend accounts for an image — an estimate flagged as such (§3.4).
_TOKEN_IMAGE_ESTIMATE = 512

# The share of ``budget.max_tokens`` retained history may fill: whatever the
# input leaves is the reply's room, so at least this much is reserved for the
# output whenever the mandatory messages alone fit inside it.
_HISTORY_TOKEN_SHARE = 0.5

# The budgets a run reports in ``budget: <name>`` when it exhausts one (R3).
_BUDGET_MODEL_TURNS = "model_turns"
_BUDGET_MAX_TOKENS = "max_tokens"
_BUDGET_MAX_ACTION_CALLS = "max_action_calls"
_BUDGET_MAX_REPEATED_ACTIONS = "max_repeated_actions"

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
    """Per-run budgets (R3), each acted by the loop at its own boundary."""

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
class _ConfiguredEntry:
    """One configured entry of a fixed delivery list (R1, decision 1).

    ``text_argument`` is the argument the answer text is put under,
    :data:`~core.contracts.DELIVERY_NO_TEXT_ARGUMENT` for an effect-only
    entry, or ``None`` when the configuration says nothing and the action's
    declaration decides at resolution. ``arguments`` are the constants the
    entry always carries. What the catalog makes of it is a
    :class:`_DeliveryEntry`, built by :func:`_resolve_delivery`.
    """

    action: str
    text_argument: str | None
    arguments: Mapping[str, Any]

    @classmethod
    def from_mapping(cls, entry: Mapping[str, Any]) -> "_ConfiguredEntry":
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
    actions: tuple[_ConfiguredEntry, ...]
    preference: tuple[str, ...]

    @classmethod
    def from_mapping(cls, section: Mapping[str, Any]) -> "_DeliveryList":
        return cls(
            mode=section["mode"],
            actions=tuple(
                _ConfiguredEntry.from_mapping(entry) for entry in section.get("actions") or ()
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
        return self.overrides.get(_destination_key(platform, channel_id), self.default)


def _destination_key(platform: str, channel_id: str) -> str:
    """The ``overrides`` key of a destination: ``<platform>/<channel_id>``.

    Built from the run's :class:`~core.contracts.SessionKey` components and
    nothing platform-specific, so an override on a second platform's channel
    is matched by the same rule.
    """

    return f"{platform}/{channel_id}"


# --------------------------------------------------------------------------- #
# Delivery resolution (R1, decision 1)
# --------------------------------------------------------------------------- #

_DELIVERY_PATH = "delivery"
_NATURE_READ = "read"


@dataclass(frozen=True, slots=True)
class _DeliveryEntry:
    """One resolved entry of a delivery list: what the terminal step invokes.

    Built at ``prepare`` from a configured entry (or a catalog declaration in
    ``modules`` mode) checked against the discovered spec: ``version`` and
    ``destinations`` are the declaration's, ``text_argument`` is the argument
    the answer text travels under — ``None`` for an effect-only entry, which
    receives no text — and ``arguments`` are the constants every call
    carries. The scope a call is addressed to is chosen per run by
    :meth:`destination_for`, since one declaration may support different
    scopes on different platforms.
    """

    action: str
    version: int
    destinations: tuple[Destination, ...]
    text_argument: str | None
    arguments: Mapping[str, Any]

    @property
    def receives_text(self) -> bool:
        return self.text_argument is not None

    def destination_for(self, platform: str, channel_id: str) -> Destination:
        """The concrete destination of a call to the run's channel; see
        :func:`_declared_destination`."""

        return _declared_destination(self.destinations, platform, channel_id)

    def arguments_for(self, text: str) -> dict[str, Any]:
        """The call's arguments: the constants, plus the text where declared."""

        arguments = dict(self.arguments)
        if self.text_argument is not None:
            arguments[self.text_argument] = text
        return arguments


def _declared_destination(
    destinations: Sequence[Destination], platform: str, channel_id: str
) -> Destination:
    """The concrete destination of a call to the run's channel (R1).

    The scope is the one of the first declaration that covers the run's
    platform and channel, so an action declared for ``a/*/chat`` and
    ``b/*/audio`` is addressed as ``audio`` on ``b``. A declaration open to
    every scope is addressed as :data:`CHAT_SCOPE`, the reply's own. When no
    declaration covers the channel, the first declaration's scope stands:
    the executor then classifies the call as an unsupported destination,
    keeping the call's accounting explicit. The one place, with
    :func:`_declared_scopes`, where the brain derives a destination from a
    spec — always from the declaration, never from a scope literal.
    """

    for declared in destinations:
        if _component_contains(declared.platform, platform) and _component_contains(
            declared.channel_id, channel_id
        ):
            return Destination(
                platform=platform, channel_id=channel_id, scope=_concrete_scope(declared)
            )
    return Destination(
        platform=platform, channel_id=channel_id, scope=_concrete_scope(destinations[0])
    )


def _declared_scopes(spec: Any) -> tuple[str, ...]:
    """The distinct concrete scopes a spec declares, in declaration order.

    ``chat`` for an action declared on ``*/*/chat``, ``capture`` for one on
    ``*/*/capture``; a spec declaring several scopes yields each once, so
    the offer is evaluated once per scope (R1, AC6).
    """

    return tuple(
        dict.fromkeys(
            _concrete_scope(declared)
            for declared in getattr(spec, "supported_destinations", ())
            if isinstance(declared, Destination)
        )
    )


def _component_contains(declared: str, actual: str) -> bool:
    return declared == WILDCARD or declared == actual


def _concrete_scope(declared: Destination) -> str:
    return CHAT_SCOPE if declared.scope == WILDCARD else declared.scope


class _DeliveryResolutionError(BrainModuleError):
    """A delivery list that does not resolve against the catalog (R1).

    The text is the startup diagnostic: ``module 'brain': delivery: <entry
    path>: <reason>``, the path naming ``delivery.actions[<i>]``,
    ``delivery.overrides["<k>"].actions[<i>]``, the derived action name or
    the list itself, and the reason one of
    :data:`~core.contracts.DELIVERY_RESOLUTION_REASONS`.
    """

    def __init__(self, path: str, reason: str) -> None:
        super().__init__(f"module {MODULE_NAME!r}: {_DELIVERY_PATH}: {path}: {reason}")
        self.path = path
        self.reason = reason


def _resolve_delivery(
    config: _DeliveryList, catalog: Mapping[str, Any], *, path: str = _DELIVERY_PATH
) -> tuple[_DeliveryEntry, ...]:
    """Resolve one configured list against the discovered *catalog* (R1).

    ``fixed``: every configured entry, in order, each checked in turn —
    the action is discovered (``unknown_action``); its declaration carries
    the delivery capability and its nature is not ``read``
    (``not_a_delivery``); the text mapping is unambiguous — an absent
    ``text_argument`` inherits the declaration's, a named one must be an
    argument of the schema and must not be given while the declaration says
    ``none`` (``text_mapping_missing``), must equal the declaration's and
    must not also be a constant argument (``text_mapping_ambiguous``); an
    empty list is ``empty``. ``modules``: every delivery-capable action of
    the catalog with its declared mapping, the ``preference`` names first in
    that order, the rest in catalog order, a preference name matching no
    delivery-capable action ignored (see :func:`_ignored_preferences`), and
    ``empty`` when no such action exists. Raises
    :class:`_DeliveryResolutionError` naming the entry and the reason.
    """

    if config.mode == DELIVERY_MODE_MODULES:
        candidates = _delivery_candidates(catalog)
        preferred = [name for name in dict.fromkeys(config.preference) if name in candidates]
        ordered = preferred + [name for name in candidates if name not in preferred]
        if not ordered:
            raise _DeliveryResolutionError(path, DELIVERY_REASON_EMPTY)
        return tuple(
            _resolve_entry(
                _ConfiguredEntry(action=name, text_argument=None, arguments={}),
                catalog,
                path=name,
            )
            for name in ordered
        )
    if not config.actions:
        raise _DeliveryResolutionError(path, DELIVERY_REASON_EMPTY)
    return tuple(
        _resolve_entry(entry, catalog, path=f"{path}.actions[{index}]")
        for index, entry in enumerate(config.actions)
    )


def _ignored_preferences(config: _DeliveryList, catalog: Mapping[str, Any]) -> tuple[str, ...]:
    """The ``preference`` names a ``modules`` list ignored: no such candidate."""

    if config.mode != DELIVERY_MODE_MODULES:
        return ()
    candidates = _delivery_candidates(catalog)
    return tuple(name for name in dict.fromkeys(config.preference) if name not in candidates)


def _delivery_candidates(catalog: Mapping[str, Any]) -> list[str]:
    """The delivery-capable actions of *catalog*, in catalog order."""

    return [name for name, spec in catalog.items() if _is_delivery_capable(spec)]


def _is_delivery_capable(spec: Any) -> bool:
    return (
        getattr(spec, "delivery", None) is not None
        and getattr(spec, "nature", None) != _NATURE_READ
    )


def _resolve_entry(
    entry: _ConfiguredEntry, catalog: Mapping[str, Any], *, path: str
) -> _DeliveryEntry:
    spec = catalog.get(entry.action)
    if spec is None:
        raise _DeliveryResolutionError(path, DELIVERY_REASON_UNKNOWN_ACTION)
    if not _is_delivery_capable(spec):
        raise _DeliveryResolutionError(path, DELIVERY_REASON_NOT_A_DELIVERY)
    declared = spec.delivery_text_argument
    configured = entry.text_argument
    if configured is not None and configured != DELIVERY_NO_TEXT_ARGUMENT:
        # A named text argument: it must exist in the schema and the
        # declaration must expect the text at all, else the mapping is
        # missing; and it must be the declared one, else ambiguous.
        if declared is None or configured not in _schema_properties(spec):
            raise _DeliveryResolutionError(path, DELIVERY_REASON_TEXT_MAPPING_MISSING)
        if configured != declared:
            raise _DeliveryResolutionError(path, DELIVERY_REASON_TEXT_MAPPING_AMBIGUOUS)
    elif configured == DELIVERY_NO_TEXT_ARGUMENT and declared is not None:
        raise _DeliveryResolutionError(path, DELIVERY_REASON_TEXT_MAPPING_AMBIGUOUS)
    if declared is not None and declared in entry.arguments:
        raise _DeliveryResolutionError(path, DELIVERY_REASON_TEXT_MAPPING_AMBIGUOUS)
    return _DeliveryEntry(
        action=spec.name,
        version=spec.version,
        destinations=tuple(spec.supported_destinations),
        text_argument=declared,
        arguments=dict(entry.arguments),
    )


def _schema_properties(spec: Any) -> Mapping[str, Any]:
    properties = spec.argument_schema.get("properties")
    return properties if isinstance(properties, Mapping) else {}


@dataclass(frozen=True, slots=True)
class _Delivery:
    """One invoked entry of the terminal step and its explicit outcome."""

    entry: _DeliveryEntry
    call_id: str
    observation: ActionObservation

    @property
    def status(self) -> str:
        return self.observation.status

    def record(self) -> dict[str, Any]:
        """The ``deliveries[]`` item: action, call id, text received, status."""

        return {
            "action": self.entry.action,
            "call_id": self.call_id,
            "text": self.entry.receives_text,
            "status": self.status,
        }


def _delivery_summary(statuses: Sequence[str]) -> str:
    """The run's ``delivery`` value over its entries' statuses (R1).

    The single entry's status when the list has one entry; otherwise
    ``success`` when every entry succeeded, else the first non-``success``
    status in list order — so a list of one reads exactly as the single
    delivery of phase 0 did.
    """

    if len(statuses) == 1:
        return statuses[0]
    for status in statuses:
        if status != _STATUS_SUCCESS:
            return status
    return _STATUS_SUCCESS


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
    # Delivery is executor calls and nothing else, so a runtime without an
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


# --------------------------------------------------------------------------- #
# The transcript and the run's accounting (R1, R3, R4)
# --------------------------------------------------------------------------- #

_ROLE_SYSTEM = "system"
_ROLE_USER = "user"
_ROLE_ASSISTANT = "assistant"
_ROLE_TOOL = "tool"
# The text that precedes the image parts of an observation in the ``user``
# message following its tool result, naming the call the images answer.
_IMAGE_MESSAGE_TEXT = "Image captured by tool call {key}:"

_ROUTE_SYNTHETIC = "synthetic"
_ROUTE_EXECUTOR = "executor"


@dataclass(frozen=True, slots=True)
class _TurnEntry:
    """One classified proposal and the observation it earned (R1).

    Rendered as the alternating pair a Chat Completions transcript carries:
    an assistant message holding the tool call — the name and the arguments
    exactly as the backend sent them, so a malformed string is echoed back
    as the string it was — and a ``tool`` message holding the observation
    as text. ``key`` correlates the two: the call id of an executed
    proposal, a per-turn key for a synthetic observation the executor never
    saw. An observation carrying ``image_ref`` parts adds a third message:
    a ``user`` message that names the call and carries the references — a
    ``tool`` message accepts text only in the Chat Completions schema, so
    an image placed there would be rejected by a backend enforcing it while
    the vision probe, sent as ``user``, had passed.
    """

    key: str
    name: str
    arguments: str
    observation: ActionObservation

    def messages(self) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = [
            {
                "role": _ROLE_ASSISTANT,
                "content": None,
                "tool_calls": [
                    {
                        "id": self.key,
                        "type": "function",
                        "function": {"name": self.name, "arguments": self.arguments},
                    }
                ],
            },
            {
                "role": _ROLE_TOOL,
                "tool_call_id": self.key,
                "content": _observation_content(self.observation),
            },
        ]
        images = _observation_images(self.observation)
        if images:
            messages.append(
                {
                    "role": _ROLE_USER,
                    "content": [
                        {"type": _PART_TEXT, "text": _IMAGE_MESSAGE_TEXT.format(key=self.key)},
                        *images,
                    ],
                }
            )
        return messages


@dataclass(slots=True)
class _Transcript:
    """The bounded transcript one run offers the model, turn after turn (R1).

    The instructions, the viewer's message and the retained memory come
    first; then, in order, one :class:`_TurnEntry` per classified proposal.
    A ``text`` part is rendered into the tool result; an ``image_ref`` part
    is kept as the reference it is, in a ``user`` message that follows the
    tool result — the adapter resolves and encodes it while a request body
    is built and nowhere else (R2, AC12). The object is
    a local of the run: it exists from the first turn to the terminal
    outcome and is never retained past it.
    """

    system: str
    user: str
    history: tuple[Exchange, ...]
    entries: list[_TurnEntry] = field(default_factory=list)

    def append(self, entry: _TurnEntry) -> None:
        self.entries.append(entry)

    def turn_messages(self) -> list[dict[str, Any]]:
        return [message for entry in self.entries for message in entry.messages()]

    def image_parts(self) -> tuple[Mapping[str, Any], ...]:
        """Every ``image_ref`` part the transcript still references."""

        return tuple(
            part
            for entry in self.entries
            for part in entry.observation.parts
            if part.get("type") == _PART_IMAGE_REF
        )


@dataclass(slots=True)
class _RunState:
    """What one run has spent so far, reported in its outcome (R1, R3).

    ``turns`` counts model calls — every proposal, synthetic or executed,
    and the final response each cost one; ``action_calls`` counts executor
    calls, delivery entries included; ``tokens`` is the cumulative input +
    output over every call, the backend's usage when it reported one and an
    estimate — flagged by ``tokens_estimated`` — when it did not;
    ``next_call`` numbers the run's call ids in invocation order;
    ``repeats`` counts the executed proposals per ``(name, canonical
    arguments)`` for the repeated-action budget.
    """

    turns: int = 0
    action_calls: int = 0
    model_calls: int = 0
    tokens: int = 0
    tokens_estimated: bool = False
    next_call: int = 1
    repeats: dict[tuple[str, str], int] = field(default_factory=dict)

    def correlation(self) -> dict[str, Any]:
        return {
            "turns": self.turns,
            "action_calls": self.action_calls,
            "tokens": self.tokens,
            "tokens_estimated": self.tokens_estimated,
            "fallback": FALLBACK_NONE,
        }

    def spend(self, reply: "_ModelReply") -> None:
        if reply.tokens is not None:
            self.tokens += reply.tokens
            self.tokens_estimated = self.tokens_estimated or reply.estimated


@dataclass(frozen=True, slots=True)
class _Offer:
    """One read action offered as a tool, and the scope it was authorized on."""

    spec: Any
    scope: str


def _synthetic_observation(status: str, code: str, *, route: str) -> ActionObservation:
    """An observation this module wrote itself: a refusal or an error the
    executor never saw (``synthetic``), or the stand-in for an executor
    call whose outcome could not be adopted (``executor``). No parts."""

    return ActionObservation(
        status=status,
        provenance={"source": MODULE_NAME, "route": route},
        error={"code": code, "message": "", "retryable": False},
    )


def _observation_content(observation: ActionObservation) -> str:
    """The tool result the model reads: status, error code, result, text.

    The envelope is one JSON object — ``status``, the error's ``code`` when
    there is one, the ``result`` mapping of a success — followed by every
    ``text`` part's text. The content is always a string: a ``tool``
    message carries text only, and the ``image_ref`` parts of the
    observation travel in the ``user`` message
    :func:`_observation_images` feeds, the references kept as references
    for the adapter to encode at request time (R2). The error's message is
    not rendered: it is the executor's diagnostic, not an observation the
    model needs.
    """

    envelope: dict[str, Any] = {"status": observation.status}
    if observation.error is not None:
        envelope["error"] = {"code": observation.error.get("code")}
    if observation.result is not None:
        envelope["result"] = _json_plain(observation.result)
    rendered = json.dumps(envelope, ensure_ascii=False, sort_keys=True, default=str)
    texts = [
        part["text"]
        for part in observation.parts
        if part.get("type") == _PART_TEXT and isinstance(part.get("text"), str)
    ]
    return "\n".join([rendered, *texts])


def _observation_images(observation: ActionObservation) -> list[dict[str, Any]]:
    """The ``image_ref`` parts of an observation, copied as the references
    they are, for the ``user`` message that follows its tool result."""

    return [dict(part) for part in observation.parts if part.get("type") == _PART_IMAGE_REF]


def _part_summaries(parts: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """What a trace says about the parts: sizes and dimensions only (AC12)."""

    summaries: list[dict[str, Any]] = []
    for part in parts:
        if part.get("type") == _PART_IMAGE_REF:
            summaries.append(
                {
                    "type": _PART_IMAGE_REF,
                    "attachment_id": part["attachment_id"],
                    "content_type": part["content_type"],
                    "size": part["size"],
                    "width": part["width"],
                    "height": part["height"],
                }
            )
        elif part.get("type") == _PART_TEXT:
            summaries.append({"type": _PART_TEXT, "bytes": _utf8_size(part["text"])})
    return summaries


def _raw_arguments_text(raw: Any) -> str:
    """The arguments as the backend sent them, as the transcript echoes them."""

    if isinstance(raw, str):
        return raw
    if raw is None:
        return ""
    return json.dumps(_json_plain(raw), ensure_ascii=False, default=str)


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
        #: The delivery lists resolved at ``prepare`` (R1, decision 1): the
        #: default and every override, by destination key; empty until then.
        self._delivery_default: tuple[_DeliveryEntry, ...] = ()
        self._delivery_overrides: dict[str, tuple[_DeliveryEntry, ...]] = {}
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
        """Resolve the delivery lists, probe the backend, start, route, mark ready.

        The delivery lists are resolved first (R1, decision 1), against the
        catalog every enabled manifest declared before any module prepares:
        a list that does not resolve raises, reports the module ``degraded``
        with the same value-free diagnostic and leaves everything else
        untouched — no probe, no scheduler started, no consumer routed, no
        ``mark_ready`` — so startup stops naming the entry and no scenario
        runs. The required capabilities are verified next (R2, decision 3)
        on the same terms. A scheduler shared through the context is started
        and closed by its owner. The consumer is routed before any producer
        may publish into it, and only then is the module marked past the
        barrier; the resolved lists are published once, as
        ``brain.delivery.resolved``, before that.
        """

        if self._prepared or self._closed:
            return
        try:
            default, overrides, ignored = self._resolve_deliveries()
            self.verified_capabilities = await self._adapter.probe(self._settings.capabilities)
        except BrainModuleError as failure:
            await self._report_degraded(str(failure))
            raise
        self._delivery_default = default
        self._delivery_overrides = overrides
        if self._owns_scheduler:
            await self._scheduler.start()
        self._bus.subscribe(_INPUT_EVENT, self.handle_chat_message)
        await self._publish_resolved_delivery(default, overrides, ignored)
        self._actions.mark_ready()
        self._prepared = True

    def _resolve_deliveries(
        self,
    ) -> tuple[tuple[_DeliveryEntry, ...], dict[str, tuple[_DeliveryEntry, ...]], tuple[str, ...]]:
        """Every configured list against the discovered catalog (R1).

        The default list first, then each override in configuration order,
        so the first list that does not resolve is the one reported. A
        catalog that cannot be read is reported as a resolution failure of
        the default list rather than guessed at.
        """

        try:
            catalog = dict(self._actions.discovered())
        except Exception:
            self._diagnose("brain actions: discovered catalog unavailable")
            raise _DeliveryResolutionError(
                _DELIVERY_PATH, DELIVERY_REASON_UNKNOWN_ACTION
            ) from None
        config = self._settings.delivery
        default = _resolve_delivery(config.default, catalog)
        ignored = list(_ignored_preferences(config.default, catalog))
        overrides: dict[str, tuple[_DeliveryEntry, ...]] = {}
        for key, override in config.overrides.items():
            path = f'{_DELIVERY_PATH}.overrides["{key}"]'
            overrides[key] = _resolve_delivery(override, catalog, path=path)
            ignored.extend(
                name for name in _ignored_preferences(override, catalog) if name not in ignored
            )
        return default, overrides, tuple(ignored)

    async def _publish_resolved_delivery(
        self,
        default: tuple[_DeliveryEntry, ...],
        overrides: Mapping[str, tuple[_DeliveryEntry, ...]],
        ignored: tuple[str, ...],
    ) -> None:
        """The one ``brain.delivery.resolved`` trace of this activation (R1).

        Action names only — what will be invoked, in order, per list — and
        the preference names that matched nothing. A lost trace is diagnosed
        and changes nothing: the resolution stands whether or not it was
        announced.
        """

        payload = {
            "default": [entry.action for entry in default],
            "overrides": {
                key: [entry.action for entry in entries] for key, entries in overrides.items()
            },
            "ignored": list(ignored),
        }
        try:
            await _resolve(self._supervision.emit(TRACE_DELIVERY_RESOLVED, payload))
        except asyncio.CancelledError:
            raise
        except Exception:
            self._diagnose("brain delivery: resolved trace lost")

    def _delivery_for(self, platform: str, channel_id: str) -> tuple[_DeliveryEntry, ...]:
        """The run's resolved list: its destination's override, else the default."""

        return self._delivery_overrides.get(
            _destination_key(platform, channel_id), self._delivery_default
        )

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

    # -- the run body: the agentic loop (R1, R2, R3, R4) -------------------- #

    async def run(self, run: Any) -> RunOutcome:
        """Take one admitted work through the loop to its :class:`~core.admission.RunOutcome`.

        The transcript is built once — instructions, the viewer's message,
        the retained memory — and grows by one :class:`_TurnEntry` per
        classified proposal; it is a local of this call, gone with it. The
        loop itself is :meth:`_loop`; whatever way it ends — an outcome, the
        deadline raised at a checkpoint, a cancellation during a capture, a
        model call or a delivery — every image the transcript still
        references is discarded here first, idempotently with the release
        the scheduler performs when it writes the terminal record (R4,
        AC25), and only then does the exit propagate.

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
        transcript = _Transcript(
            system=self._system_prompt(),
            user=_format_user_context(message),
            history=self._memory.recall(key),
        )
        state = _RunState()
        try:
            return await self._loop(run, message, key, transcript, state)
        finally:
            self._discard_images(transcript.image_parts())

    async def _loop(
        self,
        run: Any,
        message: _Message,
        key: SessionKey,
        transcript: _Transcript,
        state: _RunState,
    ) -> RunOutcome:
        """Model turns until a final response, a failure or a spent budget.

        Each turn, in this order: an ``image_ref`` the transcript carries
        whose lease expired on the clock ends the run ``attachment_expired``
        before any request (R4, AC24); ``turns == model_turns`` ends it
        ``budget_exhausted`` (AC13); the run's deadline is checked; the
        authorized read view is read afresh, per action and per declared
        scope (AC6); the prompt is composed inside the token room the run
        has left, and one that leaves the reply no room ends the run on
        ``max_tokens`` with 0 further calls; the model call is counted on
        the run before it is awaited — a shutdown that cancels a pending
        request still records the call it issued — and bounded by
        ``min(model_call_seconds, remaining)``. The reply is then classified
        (decision 2): unsupported shape → ``error`` with 0 actions; final →
        the terminal step; proposal → :meth:`_propose`, and the loop
        continues if it did not end the run.
        """

        budget = self._budget
        while True:
            if self._expired_image(transcript):
                self._diagnose("brain run: image attachment expired before the model turn")
                return self._failed(state, _STATUS_ERROR, BRAIN_ERROR_ATTACHMENT_EXPIRED)
            if state.turns >= budget.model_turns:
                return self._exhausted(state, _BUDGET_MODEL_TURNS)

            run.checkpoint()
            offered = self._offered_tools(key.platform, key.channel_id)
            room = budget.max_tokens - state.tokens
            prompt = self._compose(transcript, tuple(offer.spec for offer in offered), room)
            if prompt.output_tokens <= 0:
                self._diagnose("brain run: prompt exceeds the token budget")
                return self._exhausted(
                    state,
                    _BUDGET_MAX_TOKENS,
                    quiet=True,
                    tokens=state.tokens + prompt.input_tokens,
                    tokens_estimated=True,
                )

            run.note_model_call()
            state.model_calls += 1
            state.turns += 1
            reply = await self._adapter._request_model(
                prompt,
                budget_seconds=min(budget.model_call_seconds, run.remaining),
                token_room=room,
            )
            state.spend(reply)
            if reply.failure is not None:
                if reply.failure == _FAILURE_TIMED_OUT:
                    return self._failed(state, _STATUS_TIMEOUT, reply.failure)
                if reply.failure == _FAILURE_TOKEN_BUDGET_EXCEEDED:
                    return self._exhausted(state, _BUDGET_MAX_TOKENS, quiet=True)
                return self._failed(state, _STATUS_ERROR, reply.failure)
            if state.tokens > budget.max_tokens:
                self._diagnose("brain model response: token budget exceeded")
                return self._exhausted(state, _BUDGET_MAX_TOKENS, quiet=True)

            classification = reply.classification
            if isinstance(classification, _Unsupported):
                self._diagnose(
                    f"brain model response: unsupported shape: {classification.reason}"
                )
                return self._failed(
                    state,
                    _STATUS_ERROR,
                    RUN_FAILURE_UNSUPPORTED_RESPONSE_SHAPE,
                    shape=classification.reason,
                )
            if isinstance(classification, _Final):
                return await self._finish(run, key, transcript, classification.text, state)
            ended = await self._propose(run, message, transcript, state, classification, offered)
            if ended is not None:
                return ended

    async def _propose(
        self,
        run: Any,
        message: _Message,
        transcript: _Transcript,
        state: _RunState,
        proposal: _Proposal,
        offered: Sequence[_Offer],
    ) -> RunOutcome | None:
        """One proposal to its observation, or to the outcome that ends the run.

        In this order (R1, R3, R4): a name absent from the discovered catalog
        is a synthetic ``refused`` (``unknown_action``); a catalog action of
        any nature but ``read`` — the delivery action included, whatever
        the grants — a synthetic ``refused`` (``not_a_read_action``);
        arguments that are not a JSON object a synthetic ``error``
        (``malformed_arguments``); each with 0 executor calls. Then the
        budgets: the ``(n+1)``-th proposal of the same name and canonical
        arguments with ``n == max_repeated_actions`` is not executed and
        ends the run; so does a proposal with no action call left. Else one
        :class:`~core.contracts.ActionCall` — destination ``(run platform,
        run channel_id, the scope the action was offered on, or its
        declared scope when it was not offered)``, principal
        :data:`PRINCIPAL`, the run's identities, ``call_id`` next in the
        run's counter, deadline ``min(now + action_seconds, total
        deadline)`` — goes to the executor, which refuses by default what no
        rule permits and validates the arguments against the spec. The
        observation is bounded by ``max_observation_bytes`` here as well
        (``observation_too_large``, its images released) for a runtime whose
        executor bound is ``None``; an ``image_ref`` in a run whose verified
        capabilities lack ``vision`` ends the run ``capability_missing``
        without a request and with the image released (R2, AC11). Every
        proposal is traced as ``brain.run.observation`` and appended to the
        transcript; every proposal counts one turn, which the model call
        already did.
        """

        budget = self._budget
        name = proposal.name
        call_id: str | None = None
        key = f"{run.run_id}/turn-{state.turns}"
        spec = self._catalog().get(name)
        if spec is None:
            observation = _synthetic_observation(
                _STATUS_REFUSED, BRAIN_ERROR_UNKNOWN_ACTION, route=_ROUTE_SYNTHETIC
            )
        elif getattr(spec, "nature", None) != _NATURE_READ:
            observation = _synthetic_observation(
                _STATUS_REFUSED, BRAIN_ERROR_NOT_A_READ_ACTION, route=_ROUTE_SYNTHETIC
            )
        else:
            arguments = _decode_arguments(proposal.raw_arguments)
            if arguments is None:
                observation = _synthetic_observation(
                    _STATUS_ERROR, BRAIN_ERROR_MALFORMED_ARGUMENTS, route=_ROUTE_SYNTHETIC
                )
            else:
                repeat_key = (name, _canonical_text(arguments))
                repeated = state.repeats.get(repeat_key, 0)
                if repeated >= budget.max_repeated_actions:
                    return self._exhausted(state, _BUDGET_MAX_REPEATED_ACTIONS)
                if state.action_calls >= budget.max_action_calls:
                    return self._exhausted(state, _BUDGET_MAX_ACTION_CALLS)
                state.repeats[repeat_key] = repeated + 1
                call_id = f"{run.run_id}/call-{state.next_call}"
                try:
                    call = self._action_call(
                        run, message, spec, arguments, call_id, offered
                    )
                except Exception:
                    # The contract refused what the schema-free decode let
                    # through: the arguments are malformed for this action.
                    call_id = None
                    observation = _synthetic_observation(
                        _STATUS_ERROR, BRAIN_ERROR_MALFORMED_ARGUMENTS, route=_ROUTE_SYNTHETIC
                    )
                else:
                    state.next_call += 1
                    state.action_calls += 1
                    key = call_id
                    observation = self._bounded_observation(
                        await self._invoke(call, step="action")
                    )

        await self._trace_observation(run, state, name, call_id, observation)
        if any(part.get("type") == _PART_IMAGE_REF for part in observation.parts) and (
            CAPABILITY_VISION not in self.verified_capabilities
        ):
            self._discard_images(observation.parts)
            self._diagnose("brain run: image observation without a verified vision capability")
            return self._failed(
                state,
                _STATUS_ERROR,
                RUN_FAILURE_CAPABILITY_MISSING,
                capability=CAPABILITY_VISION,
            )
        transcript.append(
            _TurnEntry(
                key=key,
                name=name,
                arguments=_raw_arguments_text(proposal.raw_arguments),
                observation=observation,
            )
        )
        return None

    def _action_call(
        self,
        run: Any,
        message: _Message,
        spec: Any,
        arguments: Mapping[str, Any],
        call_id: str,
        offered: Sequence[_Offer],
    ) -> ActionCall:
        """One read proposal's call, its identities and deadline the runtime's."""

        scope = next((offer.scope for offer in offered if offer.spec.name == spec.name), None)
        if scope is None:
            destination = _declared_destination(
                spec.supported_destinations, message.platform, message.channel_id
            )
        else:
            destination = Destination(
                platform=message.platform, channel_id=message.channel_id, scope=scope
            )
        return ActionCall(
            action_name=spec.name,
            action_version=spec.version,
            arguments=arguments,
            conversation_id=run.conversation_id,
            run_id=run.run_id,
            call_id=call_id,
            source_event_id=run.work.source_event_id,
            destination=destination,
            principal=PRINCIPAL,
            deadline=min(run.now + self._budget.action_seconds, run.total_deadline),
            message_id=message.message_id,
        )

    def _bounded_observation(self, observation: ActionObservation) -> ActionObservation:
        """Enforce ``budget.max_observation_bytes`` on an adopted observation (R4).

        The executor enforces its own bound when it was given one; the
        production runtime leaves it ``None`` and the budget is applied
        here, one rule at two possible points. Over the bound, the
        observation becomes a synthetic ``error`` (``observation_too_large``)
        and every ``image_ref`` it named is released at once (AC47).
        """

        parts = observation.parts
        if not parts:
            return observation
        try:
            size = observation_size(parts)
        except Exception:
            size = self._budget.max_observation_bytes + 1
        if size <= self._budget.max_observation_bytes:
            return observation
        self._discard_images(parts)
        self._diagnose("brain run: observation exceeds the observation byte budget")
        return _synthetic_observation(
            _STATUS_ERROR, BRAIN_ERROR_OBSERVATION_TOO_LARGE, route=_ROUTE_EXECUTOR
        )

    def _expired_image(self, transcript: _Transcript) -> bool:
        """Whether an ``image_ref`` the transcript carries is no longer leased.

        Looked up on the clock, now, before the request is built (R4, AC24):
        a lease past its deadline, or one the store already reaped, ends the
        run rather than being sent stale or dropped silently. Without a
        store nothing can be judged here; the adapter reports what it cannot
        resolve.
        """

        store = self._attachments
        if store is None:
            return False
        now = self._clock()
        for part in transcript.image_parts():
            try:
                ref = store.lookup(part["attachment_id"])
            except Exception:
                continue
            if ref is None or now >= ref.expires_at:
                return True
        return False

    async def _trace_observation(
        self,
        run: Any,
        state: _RunState,
        action: str,
        call_id: str | None,
        observation: ActionObservation,
    ) -> None:
        """Publish one ``brain.run.observation``; a lost trace changes nothing."""

        try:
            size = observation_size(observation.parts)
        except Exception:
            size = None
        payload = {
            "run_id": run.run_id,
            "conversation_id": run.conversation_id,
            "turn": state.turns,
            "action": action,
            "call_id": call_id,
            "status": observation.status,
            "error_code": (observation.error or {}).get("code"),
            "observation_bytes": size,
            "parts": _part_summaries(observation.parts),
        }
        try:
            await _resolve(run.emit(TRACE_OBSERVATION, payload))
        except asyncio.CancelledError:
            raise
        except Exception:
            self._diagnose("brain run: observation trace lost")

    async def _finish(
        self,
        run: Any,
        key: SessionKey,
        transcript: _Transcript,
        reply: str,
        state: _RunState,
    ) -> RunOutcome:
        """The terminal step: deliver the final text, memorise, account (R1).

        The reply is delivered through every entry of the run's resolved
        delivery list (decision 1) — the override of its destination, else
        the default — one executor call per entry carrying the run's
        identity, call ids continuing the run's counter and each its own
        deadline, so a call still in flight at the total deadline is
        classified by the executor, never here (AC33). Each confirmed send
        is noted on the run as it is observed. The memory write-back
        happens here, only when the text was confirmed delivered (AC7).
        """

        run.checkpoint()
        entries = self._delivery_for(key.platform, key.channel_id)
        deliveries = await self._deliver_all(run, reply, entries, next_call_index=state.next_call)
        state.next_call += len(deliveries)
        state.action_calls += len(deliveries)
        summary = _delivery_summary([delivery.status for delivery in deliveries])
        text_deliveries = [delivery for delivery in deliveries if delivery.entry.receives_text]
        sends = sum(1 for delivery in text_deliveries if delivery.status == _STATUS_SUCCESS)
        # The text is memorised only once confirmed: at least one entry
        # received it and none of those ended other than ``success`` — an
        # ``external_unknown`` entry is never a confirmed send (AC7).
        delivered = bool(text_deliveries) and sends == len(text_deliveries)
        if delivered and not self._closed:
            self._memory.remember(key, transcript.user, reply)

        correlation: dict[str, Any] = {
            "deliveries": [delivery.record() for delivery in deliveries],
            "delivery": summary,
            **state.correlation(),
        }
        for delivery in deliveries:
            error = delivery.observation.error
            if delivery.status != _STATUS_SUCCESS and isinstance(error, Mapping):
                code = error.get("code")
                if isinstance(code, str):
                    correlation["delivery_error"] = code
                break
        return RunOutcome(
            status=summary,
            delivery=summary,
            model_calls=state.model_calls,
            sends=sends,
            correlation=correlation,
        )

    def _failed(self, state: _RunState, status: str, failure: str, **extra: Any) -> RunOutcome:
        """The outcome of a run that ended before any delivery."""

        return RunOutcome(
            status=status,
            delivery=DELIVERY_NOT_ATTEMPTED,
            model_calls=state.model_calls,
            correlation={
                "failure": failure,
                **extra,
                "deliveries": [],
                **state.correlation(),
            },
        )

    def _exhausted(
        self, state: _RunState, budget: str, *, quiet: bool = False, **extra: Any
    ) -> RunOutcome:
        """The outcome of a run that spent *budget* (R3): ``error``, named.

        One diagnostic per exhaustion: *quiet* is passed by the token paths,
        whose boundary already reported the overrun.
        """

        if not quiet:
            self._diagnose(f"brain run: budget exhausted: {budget}")
        outcome = self._failed(state, _STATUS_ERROR, RUN_FAILURE_BUDGET_EXHAUSTED, budget=budget)
        if not extra:
            return outcome
        return RunOutcome(
            status=outcome.status,
            delivery=outcome.delivery,
            model_calls=outcome.model_calls,
            correlation={**outcome.correlation, **extra},
        )

    def _compose(
        self,
        transcript: _Transcript,
        tools: Sequence[Any],
        limit: int,
    ) -> _Prompt:
        """The request messages inside the token room *limit* (§3.4).

        The instructions, the offered tool definitions, the viewer's message
        and every turn entry are mandatory: an observation is never dropped
        from under the proposal that earned it. The tools count as input
        because the backend reads them as such. History is then added
        newest-first while the input stays within its share of the room, so
        an old exchange is dropped before the reply's room is — and what the
        input leaves is the output cap the request carries. A prompt whose
        mandatory messages leave no room is returned with ``output_tokens``
        at or below 0: the caller ends the run rather than sending it.
        """

        system = {"role": _ROLE_SYSTEM, "content": transcript.system}
        user = {"role": _ROLE_USER, "content": transcript.user}
        turns = transcript.turn_messages()
        input_tokens = (
            _estimate_tokens(transcript.system)
            + _estimate_tokens(transcript.user)
            + _estimate_tool_tokens(tools)
            + sum(_estimate_message_tokens(message) for message in turns)
        )
        kept: list[dict[str, str]] = []
        if input_tokens < limit:
            history_limit = int(limit * _HISTORY_TOKEN_SHARE)
            for exchange in reversed(transcript.history):
                cost = _estimate_tokens(exchange.user) + _estimate_tokens(exchange.assistant)
                if input_tokens + cost > history_limit:
                    break
                input_tokens += cost
                kept.append({"role": _ROLE_ASSISTANT, "content": exchange.assistant})
                kept.append({"role": _ROLE_USER, "content": exchange.user})
            kept.reverse()
        return _Prompt(
            messages=[system, *kept, user, *turns],
            tools=tuple(tools),
            input_tokens=input_tokens,
            output_tokens=limit - input_tokens,
        )

    def _system_prompt(self) -> str:
        """The instructions. The offered actions travel as tools, not as text.

        No ``[send:`` tag and no action listing: what the model may call is
        the request's ``tools`` list (R2), and the reply is delivered by the
        runtime, never by an encoding of the text. No action is named here,
        so a new read action needs no new wording.
        """

        return "\n".join(
            (
                "You are a live-stream chat companion.",
                "You may call the offered tools to observe before answering;"
                " each tool result is returned to you.",
                "Reply to the viewer's message with one short plain-text chat message.",
                "Write the message body only: no tags, no markup, no instructions.",
            )
        )

    def _catalog(self) -> Mapping[str, Any]:
        """The discovered catalog, read afresh; empty when it cannot be read."""

        try:
            catalog = self._actions.discovered()
        except Exception:
            self._diagnose("brain actions: discovered catalog unavailable")
            return {}
        return catalog if isinstance(catalog, Mapping) else {}

    def _offered_tools(self, platform: str, channel_id: str) -> tuple[_Offer, ...]:
        """The read actions of the authorized view, per action and per declared scope (R1, AC6).

        Read through the scoped facade the runtime hands every module, and
        afresh every turn: an action authorized at the start of a previous
        turn is no permission now (R5). Every discovered spec of nature
        ``read`` is evaluated once per scope its ``supported_destinations``
        declare — ``chat`` for a chat reader, ``capture`` for a screen
        capture — and offered iff the view for ``(platform, channel_id,
        that scope)`` contains it, so a capture bound on ``*/*/capture`` is
        offered by its own scope and a single ``chat``-scoped query would
        never see it. The scope stays with the offer: it is the one the
        proposal's call is addressed to. A view that cannot be read offers
        nothing: default deny expressed as a surface rather than a guess
        about the rules. A delivery action is never offered, whatever the
        grants — the contracts refuse the delivery capability on a read
        action, so the nature check covers both (decision 1). The specs
        become the request's tool definitions at request time, in name
        order.
        """

        offered: dict[str, _Offer] = {}
        for name, spec in sorted(self._catalog().items(), key=lambda item: str(item[0])):
            if (
                not _is_text(getattr(spec, "name", None))
                or not isinstance(getattr(spec, "argument_schema", None), Mapping)
                or getattr(spec, "nature", None) != _NATURE_READ
            ):
                continue
            for scope in _declared_scopes(spec):
                try:
                    view = self._actions.authorized(
                        principal=PRINCIPAL,
                        destination=Destination(
                            platform=platform, channel_id=channel_id, scope=scope
                        ),
                    )
                except Exception:
                    self._diagnose("brain actions: authorized view unavailable")
                    continue
                if name in view:
                    offered[name] = _Offer(spec=spec, scope=scope)
                    break
        return tuple(offered.values())

    async def _deliver_all(
        self,
        run: Any,
        text: str,
        entries: Sequence[_DeliveryEntry],
        *,
        next_call_index: int,
    ) -> tuple[_Delivery, ...]:
        """The terminal step: every entry, in order, whatever the previous outcomes.

        One executor call per entry — destination ``(run platform, run
        channel_id, the scope the entry declares for that channel)``, read
        from the run's message, principal :data:`PRINCIPAL`, the run's identities,
        ``call_id`` continuing the run's counter from *next_call_index*,
        deadline ``min(now + action_seconds, total deadline)`` taken when
        that entry starts — with its own explicit observation. An entry that
        fails, is refused or stays uncertain never drops the ones after it
        (R1); a call that cannot even be built is an ``error`` entry, so the
        list's accounting stays complete. Each confirmed send — a text entry
        ended ``success`` — is noted on the run as soon as it is observed,
        so a cancellation that lands on a later entry (the shutdown ending
        the run) still leaves the sends already made in the run's record.
        Cancellation propagates.
        """

        message = _message_of_work(run.work)
        deliveries: list[_Delivery] = []
        for offset, entry in enumerate(entries):
            call_id = f"{run.run_id}/call-{next_call_index + offset}"
            try:
                call = self._delivery_call(run, message, entry, text, call_id)
            except Exception:
                self._diagnose("brain delivery: call could not be built")
                observation = _synthetic_observation(
                    _STATUS_ERROR, "malformed_call", route=_ROUTE_EXECUTOR
                )
            else:
                observation = await self._invoke(call, step="delivery")
            if entry.receives_text and observation.status == _STATUS_SUCCESS:
                run.note_send()
            deliveries.append(_Delivery(entry=entry, call_id=call_id, observation=observation))
        return tuple(deliveries)

    def _delivery_call(
        self, run: Any, message: _Message, entry: _DeliveryEntry, text: str, call_id: str
    ) -> ActionCall:
        """One delivery entry's call, bounded by the run's budget."""

        return ActionCall(
            action_name=entry.action,
            action_version=entry.version,
            arguments=entry.arguments_for(text),
            conversation_id=run.conversation_id,
            run_id=run.run_id,
            call_id=call_id,
            source_event_id=run.work.source_event_id,
            destination=entry.destination_for(message.platform, message.channel_id),
            principal=PRINCIPAL,
            deadline=min(run.now + self._budget.action_seconds, run.total_deadline),
            message_id=message.message_id,
        )

    async def _invoke(self, call: ActionCall, *, step: str) -> ActionObservation:
        """One executor call, whose explicit observation is the outcome (R5).

        The executor normalises every expected failure into an observation.
        What still escapes — a defect in the executor itself — is reported as
        a certain ``error``, never as a success and never retried; *step*
        names the boundary (``action`` or ``delivery``) in the diagnostic.
        Cancellation propagates.
        """

        try:
            observation = await self._executor.invoke(call)
        except asyncio.CancelledError:
            raise
        except Exception:
            self._diagnose(f"brain {step}: executor call failed")
            return _synthetic_observation(_STATUS_ERROR, "executor_failed", route=_ROUTE_EXECUTOR)
        if (
            getattr(observation, "status", None) not in TERMINAL_STATUSES
            or (
                observation.status != _STATUS_SUCCESS
                and not isinstance(getattr(observation, "error", None), Mapping)
            )
        ):
            self._diagnose(f"brain {step}: malformed observation")
            return _synthetic_observation(
                _STATUS_ERROR, "malformed_observation", route=_ROUTE_EXECUTOR
            )
        return observation

    def _discard_images(self, parts: Sequence[Mapping[str, Any]]) -> None:
        """Release every ``image_ref`` of *parts* from the store, if any.

        Idempotent with the run-end release — the store drops an object
        exactly once — and a no-op for a reference it no longer holds. A
        store that refuses is diagnosed and nothing else: the lease it could
        not free still falls to the run-end release, then to the store's
        time-to-live.
        """

        store = self._attachments
        if store is None:
            return
        for part in parts:
            if part.get("type") != _PART_IMAGE_REF:
                continue
            try:
                store.discard(part["attachment_id"])
            except Exception:
                self._diagnose("brain attachments: image release failed")

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
# The request failures the loop maps onto a run status or a budget (R3).
_FAILURE_TIMED_OUT = "timed_out"
_FAILURE_TOKEN_BUDGET_EXCEEDED = "token_budget_exceeded"


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

    ``tokens`` is the call's total — input and output — as the backend
    reported it, or as it was estimated when the backend reported none —
    ``estimated`` says which, so a trace never presents an estimate as a
    measurement. It is ``None`` when the call ended with nothing to count.
    The run sums the calls into its cumulative total (R3).
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

    async def _request_model(
        self, prompt: _Prompt, *, budget_seconds: float, token_room: int | None = None
    ) -> _ModelReply:
        """One request bounded in time and in tokens (§3.4).

        The room the prompt leaves inside the call's token room is sent as
        the request's ``max_tokens``; the call's total is then read from the
        backend's ``usage`` when it reports one and estimated — and reported
        as estimated — when it does not. A reply that puts the total over
        *token_room* — what the run has left of ``budget.max_tokens``, the
        whole budget by default — is a failure, not a longer answer. The
        time bound runs on the injected sleeper, so a held backend times out
        on the clock the run is scheduled by.
        """

        room = self._budget.max_tokens if token_room is None else token_room
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
            return _ModelReply(failure=_FAILURE_TIMED_OUT)
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
        if tokens > room:
            self._diagnose("brain model response: token budget exceeded")
            return _ModelReply(
                failure=_FAILURE_TOKEN_BUDGET_EXCEEDED, tokens=tokens, estimated=estimated
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


def _estimate_message_tokens(message: Mapping[str, Any]) -> int:
    """A conservative token count of one transcript message.

    String content is measured as text; list content part by part — a
    ``text`` part as its text, an ``image_ref`` part as the flat
    :data:`_TOKEN_IMAGE_ESTIMATE`; an assistant tool call as its name and
    the arguments it echoes. Nothing in the transcript costs nothing.
    """

    content = message.get("content")
    if isinstance(content, str):
        tokens = _estimate_tokens(content)
    elif isinstance(content, (list, tuple)):
        tokens = _TOKEN_MESSAGE_OVERHEAD
        for part in content:
            if not isinstance(part, Mapping):
                continue
            if part.get("type") == _PART_IMAGE_REF:
                tokens += _TOKEN_IMAGE_ESTIMATE
            else:
                tokens += _estimate_tokens(str(part.get("text", "")))
    else:
        tokens = _TOKEN_MESSAGE_OVERHEAD
    for call in message.get("tool_calls") or ():
        function = call.get("function") if isinstance(call, Mapping) else None
        if isinstance(function, Mapping):
            tokens += _estimate_tokens(
                str(function.get("name", "")) + str(function.get("arguments", ""))
            )
    return tokens


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
    "DELIVERY_NOT_ATTEMPTED",
    "FALLBACK_NONE",
    "MODULE_NAME",
    "PRINCIPAL",
    "TRACE_DELIVERY_RESOLVED",
    "TRACE_OBSERVATION",
    "WORK_KIND",
    "BrainModule",
    "BrainModuleError",
    "ConversationMemory",
    "Exchange",
    "activate",
    "validate_settings",
]
