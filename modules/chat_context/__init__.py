"""The ``chat.read`` action over the core's bounded chat transcript (R5).

The runtime records every normalised chat message of every channel in one
:class:`~core.context.ChatContext`, keyed by ``(platform, channel_id)`` and
bounded on count, bytes and age (AC11). This module makes that transcript
readable by the brain as an action — a ``read`` on the ``chat`` scope of any
platform and any channel — and nothing more: it ingests nothing, publishes
nothing and never writes to the transcript.

**What a read returns.** The newest ``limit`` retained messages of the
destination's channel, oldest first, dated by the transcript, plus the
*coverage* of the page (R5, AC26): ``returned`` messages out of ``retained``
ones, the ``window_seconds`` the transcript keeps a message for, and
``complete`` — true only when every retained message was returned **and**
the retention count bound was not reached, because a channel sitting at
``max_messages`` has already evicted history the caller cannot see.
``retained`` and the page come from one single transcript read: the context
re-applies age eviction on every read, so two reads could disagree on the
count (plan P14 risk).

**Where the bounds come from.** The module never asks the transcript for its
bounds — the context exposes none — and it owns no setting of its own. It
reads the reserved ``limits`` block the entry point hands every enabled
module (``core.main.LIMITS_KEY``) and takes the ``chat_context`` group of it:
that is the retention the transcript was built with, so the coverage this
action reports is stated in the terms the configuration was accepted with
(plan decision 4). The group is therefore required: a profile without
``limits.chat_context`` is refused by the settings hook naming that field.

**Authorization.** Declaring the action authorizes nothing: without an
applicable rule the executor refuses the call ``not_authorized`` and the
provider is never entered (AC30). No platform name appears here: the
platform is the destination's, a runtime value the transcript is keyed by.

Lifecycle: the manifest declares no role, so the handle takes part in
``prepare`` — bind the provider over ``*/*/chat`` and mark ready — and in
``close``, which withdraws readiness (R4).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

from core.contracts import (
    PART_TYPE_TEXT,
    ActionObservation,
    ActionSpec,
    Destination,
)


MODULE_NAME = "chat_context"

CHAT_READ_ACTION = "chat.read"
CHAT_READ_PROVIDER = "chat-context"
"""Provider name in bindings and traces."""

MANIFEST_PATH = Path(__file__).with_name("module.yaml")

#: The page-size bounds the manifest's ``argument_schema`` declares. The
#: executor enforces them before the provider is reached (``invalid_arguments``
#: for 0 and 51, AC26); the provider re-checks them so a handle invoked
#: outside the executor is refused on the same terms.
MIN_LIMIT = 1
MAX_LIMIT = 50

#: The reserved setting the entry point hands its accepted ``limits`` block
#: over in (``core.main.LIMITS_KEY``), and the group of it this module reads:
#: the retention the transcript was built with (plan decision 4).
_ACCEPTED_LIMITS_SETTING = "limits"
_ACCEPTED_GROUP = "chat_context"

_COUNT = "count"
_SECONDS = "seconds"

#: The two limits of the group this module reads, with their kinds: the
#: retention count bound ``coverage.retained`` is judged against and the age
#: window ``coverage.window_seconds`` reports. The other limits of the group
#: (bytes, channels) are the core's business and are not read here.
_READ_LIMITS: Mapping[str, str] = {
    "max_messages": _COUNT,
    "max_age_seconds": _SECONDS,
}

_ERROR_INVALID_ARGUMENTS = "invalid_arguments"
_ERROR_PROVIDER_CLOSED = "provider_closed"

_EMPTY_TRANSCRIPT = "(no retained messages)"


class ChatContextModuleError(RuntimeError):
    """A setup failure whose message contains no configured value."""


# --------------------------------------------------------------------------- #
# Settings validation hook (R7)
# --------------------------------------------------------------------------- #


def validate_settings(settings: Any) -> list[str]:
    """Check this module's settings; return one diagnostic per offending field.

    This is the hook ``settings_validator`` in the manifest names. The loader
    runs it for every enabled module before any of them is activated (R7).
    Each diagnostic names the module and the field and nothing else; an
    empty list means the settings are accepted.

    The module owns no business setting, so the only key accepted is the
    reserved ``limits`` block, and its ``chat_context`` group is required:
    ``max_messages`` must be a positive integer and ``max_age_seconds`` a
    finite positive number (R6). No configured value is echoed.
    """

    if not isinstance(settings, Mapping):
        return [_setting_diagnostic("settings", "must be a mapping")]
    diagnostics: list[str] = []

    for field_name in settings:
        if field_name != _ACCEPTED_LIMITS_SETTING:
            diagnostics.append(
                _setting_diagnostic(str(field_name), "is not a setting this module declares")
            )

    group = _accepted_group(settings, diagnostics)
    if group is not None:
        for field_name, kind in _READ_LIMITS.items():
            reason = _limit_reason(group.get(field_name), kind)
            if reason is not None:
                diagnostics.append(
                    _setting_diagnostic(
                        f"{_ACCEPTED_LIMITS_SETTING}.{_ACCEPTED_GROUP}.{field_name}",
                        reason,
                    )
                )
    return diagnostics


def _accepted_group(
    settings: Mapping[str, Any], diagnostics: list[str]
) -> Mapping[str, Any] | None:
    """The accepted ``limits.chat_context`` group, or ``None`` with a diagnostic.

    Unlike a module that owns a copy of its limits, this one has no other
    source for the bounds it reports: an absent block or group is a refusal,
    not a fallback (plan decision 4).
    """

    accepted = settings.get(_ACCEPTED_LIMITS_SETTING)
    if accepted is None:
        diagnostics.append(_setting_diagnostic(_ACCEPTED_LIMITS_SETTING, "is required"))
        return None
    if not isinstance(accepted, Mapping):
        diagnostics.append(
            _setting_diagnostic(_ACCEPTED_LIMITS_SETTING, "must be a mapping of limit groups")
        )
        return None
    label = f"{_ACCEPTED_LIMITS_SETTING}.{_ACCEPTED_GROUP}"
    group = accepted.get(_ACCEPTED_GROUP)
    if group is None:
        diagnostics.append(_setting_diagnostic(label, "is required"))
        return None
    if not isinstance(group, Mapping):
        diagnostics.append(_setting_diagnostic(label, "must be a mapping of limits"))
        return None
    return group


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


def _setting_diagnostic(field_name: str, reason: str) -> str:
    return f"module {MODULE_NAME!r}: field {field_name!r}: {reason}"


# --------------------------------------------------------------------------- #
# The transcript rendering fed back to the model (R4)
# --------------------------------------------------------------------------- #


def render_transcript(messages: Sequence[Mapping[str, Any]]) -> str:
    """Render *messages* — oldest first — as the one ``text`` part of a read.

    One line per message: the instant the transcript recorded it on the
    runtime clock, its author identifier and its text. A message text is
    kept on its line so the model reads one message per line whatever the
    viewer typed. An empty page renders as a fixed marker rather than an
    empty part, so the model is told the channel held nothing.
    """

    if not messages:
        return _EMPTY_TRANSCRIPT
    return "\n".join(
        f"[{float(message['observed_at']):.3f}] {message['author_id']}: "
        f"{_one_line(message['text'])}"
        for message in messages
    )


def _one_line(text: str) -> str:
    return " ".join(text.splitlines()) if text else ""


# --------------------------------------------------------------------------- #
# The handle
# --------------------------------------------------------------------------- #


class _Bounds:
    """The two retention bounds read from the accepted ``limits`` block."""

    __slots__ = ("max_age_seconds", "max_messages")

    def __init__(self, group: Mapping[str, Any]) -> None:
        self.max_messages = int(group["max_messages"])
        self.max_age_seconds = float(group["max_age_seconds"])


class _ChatReadProvider:
    """The ``chat.read`` provider the executor invokes (R5)."""

    __slots__ = ("_module",)

    name = CHAT_READ_PROVIDER

    def __init__(self, module: "ChatContextModule") -> None:
        self._module = module

    async def invoke(self, invocation: Any) -> ActionObservation:
        return await self._module._invoke_chat_read(invocation)


class ChatContextModule:
    """The v2 handle: bind the provider at ``prepare``, page the transcript."""

    def __init__(self, context: Any, bounds: _Bounds) -> None:
        self._actions = context.actions
        self._chat = context.chat
        self._clock = context.clock
        self._bounds = bounds
        self._provider = _ChatReadProvider(self)
        self._prepared = False
        self._closed = False

    # -- lifecycle hooks (R4) ----------------------------------------------- #

    async def prepare(self) -> None:
        """Bind the provider over the declared destinations and mark ready."""

        if self._prepared or self._closed:
            return
        spec = _declared_chat_read_spec()
        try:
            self._actions.register(spec, self._provider, provider_name=CHAT_READ_PROVIDER)
        except Exception:
            raise ChatContextModuleError(
                f"{MODULE_NAME} prepare: action binding failed"
            ) from None
        self._actions.mark_ready()
        self._prepared = True

    async def close(self) -> None:
        """Withdraw readiness; the transcript is the core's and stays open."""

        if self._closed:
            return
        self._closed = True
        if self._prepared:
            self._actions.mark_not_ready()

    # -- the read (R5, AC26) ------------------------------------------------ #

    async def _invoke_chat_read(self, invocation: Any) -> ActionObservation:
        """Serve one ``chat.read`` call for the executor.

        The transcript is read **once**, for its whole retained count: the
        context re-applies age eviction on every read, so a second read for
        the page could see fewer messages than the count reported beside it.
        The page is the newest ``limit`` of that single read, oldest first.
        """

        # A read reaches nothing outside the process: an interruption is a
        # certain ``timeout``/``cancelled``, never an uncertain effect.
        invocation.mark_not_emitted()
        call = invocation.call
        destination = call.destination
        provenance = {
            "provider": CHAT_READ_PROVIDER,
            "platform": destination.platform,
            "channel_id": destination.channel_id,
            "route": CHAT_READ_ACTION,
        }

        def failure(code: str, message: str) -> ActionObservation:
            return ActionObservation(
                status="error",
                provenance=provenance,
                error={"code": code, "message": message, "retryable": False},
            )

        if self._closed:
            return failure(_ERROR_PROVIDER_CLOSED, f"{MODULE_NAME} read: closed")
        limit = call.arguments.get("limit")
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not MIN_LIMIT <= limit <= MAX_LIMIT
        ):
            return failure(
                _ERROR_INVALID_ARGUMENTS,
                f"limit must be an integer between {MIN_LIMIT} and {MAX_LIMIT}",
            )

        bounds = self._bounds
        retained_records = self._chat.read(
            destination.platform, destination.channel_id, limit=bounds.max_messages
        )
        retained = len(retained_records)
        page = retained_records[-limit:] if limit < retained else retained_records
        observed_at = float(self._clock())

        messages = [
            {
                "author_id": record.author_id,
                "message_id": record.message_id,
                "text": record.text,
                "observed_at": float(record.timestamp),
            }
            for record in page
        ]
        returned = len(messages)
        result = {
            "messages": messages,
            "observed_at": observed_at,
            "coverage": {
                "returned": returned,
                "retained": retained,
                "window_seconds": bounds.max_age_seconds,
                "complete": returned == retained and retained < bounds.max_messages,
            },
        }
        return ActionObservation(
            status="success",
            provenance=provenance,
            result=result,
            parts=({"type": PART_TYPE_TEXT, "text": render_transcript(messages)},),
        )


# --------------------------------------------------------------------------- #
# Activation (R7)
# --------------------------------------------------------------------------- #


async def activate(
    context: Any,
    settings: Mapping[str, Any],
    catalog: Mapping[str, Mapping[str, Any]],
) -> ChatContextModule:
    """Build the handle from the scoped runtime context (R7).

    *context* is the module-scoped view of the versioned runtime: the action
    facade the provider is registered on at ``prepare``, the shared chat
    transcript the reads page through and the clock every ``observed_at``
    is stamped with. Settings are checked through the same hook the loader
    ran, so a handle built outside the loader is refused on the same terms.
    """

    del catalog  # Capabilities are declared by the colocated manifest.
    _require_runtime_surfaces(context)
    if not isinstance(settings, Mapping):
        raise ChatContextModuleError(f"{MODULE_NAME} configuration: settings must be a mapping")
    diagnostics = validate_settings(settings)
    if diagnostics:
        raise ChatContextModuleError(
            f"{MODULE_NAME} configuration: settings were refused "
            f"({len(diagnostics)} diagnostics)"
        )
    bounds = _Bounds(settings[_ACCEPTED_LIMITS_SETTING][_ACCEPTED_GROUP])
    return ChatContextModule(context, bounds)


def _require_runtime_surfaces(context: Any) -> None:
    """Refuse a context lacking the actions facade, the transcript or a clock.

    Duck-typed so a harness may inject fakes, while a runtime assembled
    without a chat context is refused at activation with one diagnostic
    rather than at the first read.
    """

    actions = getattr(context, "actions", None)
    chat = getattr(context, "chat", None)
    clock = getattr(context, "clock", None)
    if (
        actions is None
        or not all(
            callable(getattr(actions, method, None))
            for method in ("register", "mark_ready", "mark_not_ready")
        )
        or chat is None
        or not callable(getattr(chat, "read", None))
        or not callable(clock)
    ):
        raise ChatContextModuleError(f"{MODULE_NAME} activation: runtime context is invalid")


def _declared_chat_read_spec() -> ActionSpec:
    """Build the ``chat.read`` contract from the colocated manifest (R5).

    The spec registered at ``prepare`` must equal the one the loader declared
    at discovery, field for field: reading the same file keeps the two from
    drifting, and the registry refuses a redeclaration that differs.
    """

    try:
        manifest = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))
        entry = next(
            item
            for item in manifest["actions"]
            if isinstance(item, Mapping) and item.get("name") == CHAT_READ_ACTION
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
        raise ChatContextModuleError(
            f"{MODULE_NAME} prepare: manifest declaration of {CHAT_READ_ACTION!r} is invalid"
        ) from None


__all__ = [
    "CHAT_READ_ACTION",
    "CHAT_READ_PROVIDER",
    "MANIFEST_PATH",
    "MAX_LIMIT",
    "MIN_LIMIT",
    "MODULE_NAME",
    "ChatContextModule",
    "ChatContextModuleError",
    "activate",
    "render_transcript",
    "validate_settings",
]
