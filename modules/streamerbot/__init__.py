"""Automation bridge on the v2 runtime (phase 5 R1, R4, R6, R10).

The manifest beside this package declares ``manifest_version: 2``: the
loader runs :func:`validate_settings` before anything is activated, hands
:func:`activate` a scoped runtime context, and drives the returned handle
through the hooks of the ``input`` role.

The bridge is optional in every case (R1). Its settings schema requires
nothing; this package's hook refuses a configuration it cannot use — a
missing ``url`` or ``companion_name``, a plain ``ws`` URL to a non-loopback
host, a non-finite or non-positive deadline, an ambiguous allowlist — with
one value-free diagnostic per field, so ``--check-config`` reports it before
any socket opens. Ownership-aware (D7): when the runtime hands it the pairs
this module owns, a (``chat``, P) pair without a configured companion
identity for P is refused naming the platform.

At this stage the handle opens no connection: ``prepare`` binds
``automation.action.run`` to a provider that answers ``refused
provider_not_ready`` and never marks the module ready, so the executor
refuses every run before the provider is reached. No endpoint, port,
password, version or action name has a default anywhere (R6).
"""

from __future__ import annotations

import ipaddress
import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

from core.actions import ERROR_PROVIDER_NOT_READY
from core.contracts import (
    ActionObservation,
    ActionSpec,
    ContractError,
    Destination,
    validate_schema,
)

MODULE_NAME = "streamerbot"

RUN_ACTION = "automation.action.run"
RUN_PROVIDER = "automation-bridge"
"""Provider name in bindings and traces."""

MANIFEST_PATH = Path(__file__).with_name("module.yaml")

#: How much of the run action's fixed ``timeout_seconds`` the acknowledgement
#: deadline must leave free, so the executor's own timeout never pre-empts the
#: bridge's ``acknowledgement_lost`` cause (R4).
ACKNOWLEDGEMENT_MARGIN_SECONDS = 1.0

#: The platforms whose chat input the manifest lets this module own; owning
#: one requires a companion identity for it (R7, AC33).
CHAT_PLATFORMS = ("twitch", "kick", "youtube")
_CHAT_FAMILY = "chat"

_SCHEME_PLAIN = "ws"
_SCHEME_TLS = "wss"
_LOOPBACK_NAMES = frozenset({"localhost"})

_SETTING_URL = "url"
_SETTING_PASSWORD = "password"
_SETTING_COMPANION_NAME = "companion_name"
_SETTING_DEADLINES = "deadlines"
_SETTING_RECONNECT = "reconnect"
_SETTING_QUEUE_MAX_EVENTS = "queue_max_events"
_SETTING_ALLOWLIST = "allowlist"
_SETTING_COMPANION_IDENTITIES = "companion_identities"
_SETTING_INSTANCE_IDENTITY = "instance_identity"
_SETTING_TRUST_INSTANCE_ROLES = "trust_instance_roles"
#: The reserved setting the entry point hands its accepted ``limits`` block
#: over in (``core.main.LIMITS_KEY``). Accepted, never read.
_ACCEPTED_LIMITS_SETTING = "limits"

_SETTINGS = frozenset(
    {
        _SETTING_URL,
        _SETTING_PASSWORD,
        _SETTING_COMPANION_NAME,
        _SETTING_DEADLINES,
        _SETTING_RECONNECT,
        _SETTING_QUEUE_MAX_EVENTS,
        _SETTING_ALLOWLIST,
        _SETTING_COMPANION_IDENTITIES,
        _SETTING_INSTANCE_IDENTITY,
        _SETTING_TRUST_INSTANCE_ROLES,
        _ACCEPTED_LIMITS_SETTING,
    }
)
_DEADLINE_FIELDS = (
    "connect_seconds",
    "authentication_seconds",
    "request_seconds",
    "acknowledgement_seconds",
)
_ACKNOWLEDGEMENT_FIELD = "acknowledgement_seconds"
_MIN_DELAY_FIELD = "min_delay_seconds"
_MAX_DELAY_FIELD = "max_delay_seconds"
_RECONNECT_FIELDS = (_MIN_DELAY_FIELD, _MAX_DELAY_FIELD)
_ENTRY_KEYS = ("action", "group")
_ENTRY_ARGUMENTS = "arguments"


class AutomationBridgeError(RuntimeError):
    """A refused activation or preparation; the message carries no value."""


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #


def validate_settings(settings: Any, *, owned_inputs: Any = None) -> list[str]:
    """Check this module's settings; return one diagnostic per offending field.

    This is the hook ``settings_validator`` in the manifest names. Each
    diagnostic is ``module 'streamerbot': field 'X': reason`` and echoes no
    configured value. The schema requires nothing (R1, AC5); this hook is
    what refuses a missing ``url`` or ``companion_name`` (AC22).

    *owned_inputs* is this module's read-only ownership view (D7), handed
    over by the loader; ``None`` — a call outside the loader — skips the
    ownership-dependent check.
    """

    if not isinstance(settings, Mapping):
        return [_setting_diagnostic("settings", "must be a mapping")]
    diagnostics: list[str] = []

    for field_name in settings:
        if field_name not in _SETTINGS:
            diagnostics.append(
                _setting_diagnostic(str(field_name), "is not a setting this module declares")
            )

    url = settings.get(_SETTING_URL)
    if url is None:
        diagnostics.append(_setting_diagnostic(_SETTING_URL, "is required"))
    elif not _is_text(url):
        diagnostics.append(_setting_diagnostic(_SETTING_URL, "must be a non-empty string"))
    else:
        diagnostics.extend(_url_diagnostics(url))

    if _SETTING_PASSWORD in settings and not isinstance(settings[_SETTING_PASSWORD], str):
        diagnostics.append(_setting_diagnostic(_SETTING_PASSWORD, "must be a string"))

    companion_name = settings.get(_SETTING_COMPANION_NAME)
    if companion_name is None:
        diagnostics.append(_setting_diagnostic(_SETTING_COMPANION_NAME, "is required"))
    elif not _is_text(companion_name):
        diagnostics.append(
            _setting_diagnostic(_SETTING_COMPANION_NAME, "must be a non-empty string")
        )

    diagnostics.extend(_deadline_diagnostics(settings))
    diagnostics.extend(_reconnect_diagnostics(settings))

    if _SETTING_QUEUE_MAX_EVENTS in settings and not _is_positive_int(
        settings[_SETTING_QUEUE_MAX_EVENTS]
    ):
        diagnostics.append(
            _setting_diagnostic(_SETTING_QUEUE_MAX_EVENTS, "must be a positive integer")
        )

    if _SETTING_ALLOWLIST in settings:
        diagnostics.extend(_allowlist_diagnostics(settings[_SETTING_ALLOWLIST]))

    identities_valid = True
    for name in (_SETTING_COMPANION_IDENTITIES, _SETTING_INSTANCE_IDENTITY):
        if name in settings:
            found = _identity_diagnostics(name, settings[name])
            diagnostics.extend(found)
            if found and name == _SETTING_COMPANION_IDENTITIES:
                identities_valid = False

    if _SETTING_TRUST_INSTANCE_ROLES in settings and not isinstance(
        settings[_SETTING_TRUST_INSTANCE_ROLES], bool
    ):
        diagnostics.append(
            _setting_diagnostic(_SETTING_TRUST_INSTANCE_ROLES, "must be a boolean")
        )

    if owned_inputs is not None and identities_valid:
        identities = settings.get(_SETTING_COMPANION_IDENTITIES) or {}
        for platform in CHAT_PLATFORMS:
            if owned_inputs.owns(_CHAT_FAMILY, platform) and platform not in identities:
                diagnostics.append(
                    _setting_diagnostic(
                        f"{_SETTING_COMPANION_IDENTITIES}.{platform}",
                        f"is required: this module owns the chat input of platform {platform}",
                    )
                )
    return diagnostics


def _url_diagnostics(url: str) -> list[str]:
    """The R6 rule on ``url``: ``ws`` on loopback only, ``wss`` anywhere.

    Mirrors the rule of ``modules.agent_link`` (whose helpers are private).
    """

    try:
        parts = urlsplit(url.strip())
        host = parts.hostname
    except ValueError:
        return [_setting_diagnostic(_SETTING_URL, "is not a valid URL")]
    if parts.scheme not in (_SCHEME_PLAIN, _SCHEME_TLS):
        return [_setting_diagnostic(_SETTING_URL, "must use the ws or wss scheme")]
    if not host:
        return [_setting_diagnostic(_SETTING_URL, "must name a host")]
    if parts.scheme == _SCHEME_PLAIN and not _is_loopback(host):
        return [
            _setting_diagnostic(
                _SETTING_URL, "must use wss unless the host is a loopback address"
            )
        ]
    return []


def _deadline_diagnostics(settings: Mapping[str, Any]) -> list[str]:
    if _SETTING_DEADLINES not in settings:
        return []
    deadlines = settings[_SETTING_DEADLINES]
    if not isinstance(deadlines, Mapping):
        return [_setting_diagnostic(_SETTING_DEADLINES, "must be a mapping")]
    diagnostics: list[str] = []
    for key in deadlines:
        label = f"{_SETTING_DEADLINES}.{key}"
        if key not in _DEADLINE_FIELDS:
            diagnostics.append(_setting_diagnostic(label, "is not a deadlines field"))
        elif not _is_positive_number(deadlines[key]):
            diagnostics.append(_setting_diagnostic(label, "must be a finite positive number"))
        elif key == _ACKNOWLEDGEMENT_FIELD:
            bound = run_action_timeout_seconds() - ACKNOWLEDGEMENT_MARGIN_SECONDS
            if deadlines[key] > bound:
                diagnostics.append(
                    _setting_diagnostic(
                        label,
                        "must leave the run action's timeout a margin of "
                        f"{ACKNOWLEDGEMENT_MARGIN_SECONDS:g} second(s)",
                    )
                )
    return diagnostics


def _reconnect_diagnostics(settings: Mapping[str, Any]) -> list[str]:
    if _SETTING_RECONNECT not in settings:
        return []
    reconnect = settings[_SETTING_RECONNECT]
    if not isinstance(reconnect, Mapping):
        return [_setting_diagnostic(_SETTING_RECONNECT, "must be a mapping")]
    diagnostics: list[str] = []
    valid: dict[str, float] = {}
    for key in reconnect:
        label = f"{_SETTING_RECONNECT}.{key}"
        if key not in _RECONNECT_FIELDS:
            diagnostics.append(_setting_diagnostic(label, "is not a reconnect field"))
        elif not _is_positive_number(reconnect[key]):
            diagnostics.append(_setting_diagnostic(label, "must be a finite positive number"))
        else:
            valid[key] = reconnect[key]
    if (
        _MIN_DELAY_FIELD in valid
        and _MAX_DELAY_FIELD in valid
        and valid[_MIN_DELAY_FIELD] > valid[_MAX_DELAY_FIELD]
    ):
        diagnostics.append(
            _setting_diagnostic(
                f"{_SETTING_RECONNECT}.{_MIN_DELAY_FIELD}",
                f"must not exceed {_MAX_DELAY_FIELD}",
            )
        )
    return diagnostics


def _allowlist_diagnostics(allowlist: Any) -> list[str]:
    """Each entry names exactly one action or one group, never twice (R5, AC45)."""

    if isinstance(allowlist, (str, bytes, Mapping)) or not isinstance(allowlist, Sequence):
        return [_setting_diagnostic(_SETTING_ALLOWLIST, "must be a list of entries")]
    diagnostics: list[str] = []
    seen: dict[str, set[str]] = {key: set() for key in _ENTRY_KEYS}
    for index, entry in enumerate(allowlist):
        label = f"{_SETTING_ALLOWLIST}[{index}]"
        if not isinstance(entry, Mapping):
            diagnostics.append(_setting_diagnostic(label, "must be a mapping"))
            continue
        unknown = [key for key in entry if key not in (*_ENTRY_KEYS, _ENTRY_ARGUMENTS)]
        for key in unknown:
            diagnostics.append(
                _setting_diagnostic(f"{label}.{key}", "is not an allowlist entry field")
            )
        named = [key for key in _ENTRY_KEYS if key in entry]
        if len(named) != 1:
            diagnostics.append(
                _setting_diagnostic(label, "must name exactly one of action or group")
            )
        else:
            key = named[0]
            value = entry[key]
            if not _is_text(value):
                diagnostics.append(
                    _setting_diagnostic(f"{label}.{key}", "must be a non-empty string")
                )
            elif value in seen[key]:
                diagnostics.append(
                    _setting_diagnostic(
                        f"{label}.{key}", f"duplicates an earlier {key} entry"
                    )
                )
            else:
                seen[key].add(value)
        if _ENTRY_ARGUMENTS in entry:
            try:
                validate_schema(entry[_ENTRY_ARGUMENTS], label=_ENTRY_ARGUMENTS)
            except ContractError:
                diagnostics.append(
                    _setting_diagnostic(
                        f"{label}.{_ENTRY_ARGUMENTS}", "is not a supported schema"
                    )
                )
    return diagnostics


def _identity_diagnostics(name: str, identities: Any) -> list[str]:
    """A platform → account identifier mapping, both sides non-empty text."""

    if not isinstance(identities, Mapping):
        return [_setting_diagnostic(name, "must be a mapping of platform to identifier")]
    diagnostics: list[str] = []
    for platform, identity in identities.items():
        if not _is_text(platform):
            diagnostics.append(_setting_diagnostic(name, "platform names must be non-empty"))
        elif not _is_text(identity):
            diagnostics.append(
                _setting_diagnostic(f"{name}.{platform}", "must be a non-empty string")
            )
    return diagnostics


def _setting_diagnostic(field_name: str, reason: str) -> str:
    return f"module {MODULE_NAME!r}: field {field_name!r}: {reason}"


def _is_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


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


# --------------------------------------------------------------------------- #
# The run action's declared contract
# --------------------------------------------------------------------------- #


def _declared_run_entry() -> Mapping[str, Any]:
    manifest = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))
    return next(
        item
        for item in manifest["actions"]
        if isinstance(item, Mapping) and item.get("name") == RUN_ACTION
    )


def run_action_timeout_seconds() -> float:
    """The run action's fixed ``timeout_seconds``, read from the manifest."""

    return float(_declared_run_entry()["timeout_seconds"])


def declared_run_spec() -> ActionSpec:
    """Build the run action's contract from the colocated manifest (R4).

    The spec registered at ``prepare`` must equal the one the loader declared
    at discovery, field for field: reading the same file keeps the two from
    drifting, and the registry refuses a redeclaration that differs.
    """

    try:
        entry = _declared_run_entry()
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
        raise AutomationBridgeError(
            f"{MODULE_NAME} prepare: manifest declaration of {RUN_ACTION!r} is invalid"
        ) from None


# --------------------------------------------------------------------------- #
# The handle
# --------------------------------------------------------------------------- #


class _RunProvider:
    """The run action's provider before any connection exists.

    The executor refuses ``provider_not_ready`` first, since the module is
    never marked ready; a call reaching this provider anyway is refused on
    the same terms, with nothing sent.
    """

    __slots__ = ()

    name = RUN_PROVIDER

    async def invoke(self, invocation: Any) -> ActionObservation:
        invocation.mark_not_emitted()
        return ActionObservation(
            status="refused",
            provenance={"provider": RUN_PROVIDER, "route": RUN_ACTION},
            error={
                "code": ERROR_PROVIDER_NOT_READY,
                "message": f"{MODULE_NAME}: the automation instance is not connected",
                "retryable": False,
            },
        )


class AutomationBridge:
    """The v2 handle: bind the run action at ``prepare``, open nothing yet."""

    def __init__(self, context: Any) -> None:
        self._actions = context.actions
        self._provider = _RunProvider()
        self._prepared = False
        self._closed = False

    async def prepare(self) -> None:
        """Bind the run action; the module stays not ready (no connection)."""

        if self._prepared or self._closed:
            return
        spec = declared_run_spec()
        try:
            self._actions.register(spec, self._provider, provider_name=RUN_PROVIDER)
        except Exception:
            raise AutomationBridgeError(
                f"{MODULE_NAME} prepare: action binding failed"
            ) from None
        self._prepared = True

    async def start_inputs(self) -> None:
        """No source is opened at this stage."""

    async def stop_inputs(self) -> None:
        """Nothing to cut: no source was opened."""

    async def close(self) -> None:
        """Withdraw readiness; idempotent."""

        if self._closed:
            return
        self._closed = True
        if self._prepared:
            self._actions.mark_not_ready()


# --------------------------------------------------------------------------- #
# Activation
# --------------------------------------------------------------------------- #


async def activate(
    context: Any,
    settings: Mapping[str, Any],
    catalog: Mapping[str, Mapping[str, Any]],
) -> AutomationBridge:
    """Build the handle from the scoped runtime context.

    Nothing here reaches the network. Settings are checked through the same
    hook the loader ran, so a handle built outside the loader is refused on
    the same terms; the diagnostics themselves are never raised.
    """

    del catalog
    actions = getattr(context, "actions", None)
    if actions is None or not all(
        callable(getattr(actions, method, None))
        for method in ("register", "mark_ready", "mark_not_ready")
    ):
        raise AutomationBridgeError(f"{MODULE_NAME} activation: runtime context is invalid")
    if not isinstance(settings, Mapping):
        raise AutomationBridgeError(f"{MODULE_NAME} configuration: settings must be a mapping")
    diagnostics = validate_settings(settings)
    if diagnostics:
        raise AutomationBridgeError(
            f"{MODULE_NAME} configuration: settings were refused "
            f"({len(diagnostics)} diagnostics)"
        )
    return AutomationBridge(context)


__all__ = [
    "ACKNOWLEDGEMENT_MARGIN_SECONDS",
    "CHAT_PLATFORMS",
    "MANIFEST_PATH",
    "MODULE_NAME",
    "RUN_ACTION",
    "RUN_PROVIDER",
    "AutomationBridge",
    "AutomationBridgeError",
    "activate",
    "declared_run_spec",
    "run_action_timeout_seconds",
    "validate_settings",
]
