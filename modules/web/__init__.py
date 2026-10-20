"""The ``web.search`` and ``web.read`` actions over the public web (phase 7).

The brain can look something up through this module: ``web.search`` asks the
configured provider — Wikipedia by default, with no credential, or Brave
Search with its own key — and returns at most ``max_results`` results, each
a ``title``, a ``url`` and a plain-text ``excerpt``; ``web.read`` fetches one
public HTTP(S) page and returns its final URL, its title and its extracted
text, bounded. Both are ``read`` actions on ``*/*/web``, declared in the
colocated manifest with closed argument and result schemas and no grant:
without an explicit authorization rule neither is offered and no request
leaves this machine (R1). The search result names its provider as a string
value, never as a field, so a third provider adds a value and not a field.

**The untrusted marker, and why it frames the content (R8).** Every
successful result carries an ``advisory`` field whose only allowed value is
one fixed sentence, declared once in the manifest and read from it by
:func:`advisory_marker` — no second copy exists to drift. The brain renders
an observation's ``result`` as one JSON object with sorted keys and appends
only the observation's ``text`` parts after it. So every external string —
title, URL, excerpt, page text — is a JSON string literal, in which a quote,
a backslash, a newline, the marker text or any delimiter is escaped and can
neither end the literal nor forge a second one; and ``advisory`` sorts
before ``final_url``, ``provider``, ``results``, ``text``, ``title`` and
``truncated``, so the marker is rendered before every external string. The
module therefore emits no ``text`` part. Reading content changes no
authorization: default-deny is untouched and nothing here can grant.

**What is recorded, and what never is (R7).** Traces and diagnostics carry
the provider, byte lengths, counts, the cache-hit flag and the outcome —
never a query, a URL's path or query string, page or excerpt text, or the
Brave key's value. Every diagnostic this module produces is a fixed string
or a format of fixed vocabulary and counts; no exception text from the
transport, the codecs or the address parser is ever rendered.

**Bounds (R5).** Every bound is a setting with a validated inclusive range,
checked by the core schema subset at startup and by ``--check-config``, and
again by :func:`validate_settings` — the declared hook — which also checks
what the subset cannot say: at most 64 ``allowed_hosts``, each a host name;
the ``wikipedia_language`` shape; and a non-empty ``brave_api_key`` under
``provider: brave``.

**Readiness.** ``prepare`` marks the module ready and opens nothing: no
resolution and no connection happen before a call (R1, AC4). Until the
providers are bound, the executor refuses both actions
``provider_not_ready`` — the kernel's own answer. ``close`` withdraws
readiness and closes a transport a call opened.

**Seams, for the tests.** ``_transport`` replaces the HTTP transport,
``_resolver`` the name resolution and ``_sleeper`` the sleep a bound is
raced against. They are read from *settings*, never from a configuration
file, and bound lazily: activation opens nothing.

Lifecycle: the manifest declares no role, so the handle takes part in
``prepare`` and in ``close``.
"""

from __future__ import annotations

import functools
import inspect
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from core.contracts import mask_known


MODULE_NAME = "web"

WEB_SEARCH_ACTION = "web.search"
WEB_READ_ACTION = "web.read"

MANIFEST_PATH = Path(__file__).with_name("module.yaml")

PROVIDER_WIKIPEDIA = "wikipedia"
PROVIDER_BRAVE = "brave"
PROVIDERS = (PROVIDER_WIKIPEDIA, PROVIDER_BRAVE)

#: The most ``allowed_hosts`` entries; ``maxItems`` is not in the core subset.
MAX_ALLOWED_HOSTS = 64

#: The bounded settings, each ``(default, minimum, maximum)``, inclusive (R5).
INTEGER_SETTINGS: Mapping[str, tuple[int, int, int]] = {
    "max_results": (5, 1, 10),
    "max_excerpt_bytes": (300, 50, 1000),
    "max_text_bytes": (8192, 1024, 65536),
    "max_response_bytes": (1_048_576, 65_536, 8_388_608),
    "max_redirects": (3, 0, 10),
    "request_timeout_seconds": (10, 1, 14),
    "max_concurrent_requests": (4, 1, 16),
    "cache_ttl_seconds": (3600, 0, 86_400),
    "cache_max_entries": (256, 1, 4096),
    "cache_max_bytes": (4_194_304, 65_536, 67_108_864),
}

DEFAULT_PROVIDER = PROVIDER_WIKIPEDIA
DEFAULT_WIKIPEDIA_LANGUAGE = "en"
DEFAULT_USER_AGENT_CONTACT = ""
DEFAULT_DEBUG_LENGTHS = False

#: The longest ``user_agent_contact`` the schema accepts.
MAX_USER_AGENT_CONTACT = 200

_SETTING_PROVIDER = "provider"
_SETTING_BRAVE_API_KEY = "brave_api_key"
_SETTING_ALLOWED_HOSTS = "allowed_hosts"
_SETTING_WIKIPEDIA_LANGUAGE = "wikipedia_language"
_SETTING_USER_AGENT_CONTACT = "user_agent_contact"
_SETTING_DEBUG_LENGTHS = "debug_lengths"

#: The reserved setting the entry point hands its accepted ``limits`` block
#: over in (``core.main.LIMITS_KEY``). Accepted, never read.
_ACCEPTED_LIMITS_SETTING = "limits"

#: Settings this module reads; every other non-seam key is refused by name.
_SETTINGS = frozenset(
    {
        _SETTING_PROVIDER,
        _SETTING_BRAVE_API_KEY,
        _SETTING_ALLOWED_HOSTS,
        _SETTING_WIKIPEDIA_LANGUAGE,
        _SETTING_USER_AGENT_CONTACT,
        _SETTING_DEBUG_LENGTHS,
        *INTEGER_SETTINGS,
    }
)

#: The injection seams: read from *settings*, never from a configuration file.
_SEAM_TRANSPORT = "_transport"
_SEAM_RESOLVER = "_resolver"
_SEAM_SLEEPER = "_sleeper"
_SEAMS = frozenset({_SEAM_TRANSPORT, _SEAM_RESOLVER, _SEAM_SLEEPER})
_CALLABLE_SEAMS = frozenset({_SEAM_RESOLVER, _SEAM_SLEEPER})

_LANGUAGE = re.compile(r"[a-z]{2,3}(-[a-z0-9]+)?")
#: One host-name label once IDNA-encoded: letters, digits and inner hyphens.
_HOST_LABEL = re.compile(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?")
_MAX_HOST_LENGTH = 253


class WebModuleError(RuntimeError):
    """A setup failure whose message contains no configured value."""


# --------------------------------------------------------------------------- #
# The untrusted marker, from the manifest (R8)
# --------------------------------------------------------------------------- #


@functools.cache
def advisory_marker() -> str:
    """The fixed untrusted-content sentence, as the manifest declares it.

    Read from the result schema of ``web.search``, the one place the text is
    written (``web.read`` refers to it by YAML alias), so the value the
    module returns and the value the executor validates cannot drift.
    """

    try:
        manifest = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))
        entry = next(
            item
            for item in manifest["actions"]
            if isinstance(item, Mapping) and item.get("name") == WEB_SEARCH_ACTION
        )
        (marker,) = entry["result_schema"]["properties"]["advisory"]["enum"]
    except Exception:
        raise WebModuleError(f"{MODULE_NAME}: manifest advisory marker is invalid") from None
    if not isinstance(marker, str) or not marker:
        raise WebModuleError(f"{MODULE_NAME}: manifest advisory marker is invalid")
    return marker


# --------------------------------------------------------------------------- #
# Settings validation hook (R5)
# --------------------------------------------------------------------------- #


def validate_settings(settings: Any) -> list[str]:
    """Check this module's settings; return one diagnostic per refused field.

    This is the hook ``settings_validator`` in the manifest names; the loader
    runs it for every enabled module before any is activated, after the
    declared schema. Each diagnostic names the module and the field and
    nothing else — no configured value is echoed; an empty list means the
    settings are accepted.

    Every integer bound must be an integer (``bool`` and ``str`` are not)
    within its inclusive range; ``provider`` must be ``wikipedia`` or
    ``brave``, and ``brave`` requires a non-empty ``brave_api_key``;
    ``allowed_hosts`` is a list of at most 64 host names (IDNA-encodable,
    with no scheme, port, path, userinfo or wildcard); ``wikipedia_language``
    is a 2–3 letter code with an optional ``-`` subtag; ``user_agent_contact``
    is printable text of bounded length; ``debug_lengths`` is a boolean. The
    reserved ``limits`` block is accepted and not inspected; a seam is one of
    the three this module reads; any other key is refused by name.
    """

    if not isinstance(settings, Mapping):
        return [_setting_diagnostic("settings", "must be a mapping")]
    diagnostics: list[str] = []
    known = _configured_strings(settings)

    for field_name in settings:
        if (
            field_name not in _SETTINGS
            and field_name != _ACCEPTED_LIMITS_SETTING
            and field_name not in _SEAMS
        ):
            diagnostics.append(
                _setting_diagnostic(
                    mask_known(str(field_name), known),
                    "is not a setting this module declares",
                )
            )
    for seam in _CALLABLE_SEAMS:
        if seam in settings and not callable(settings[seam]):
            diagnostics.append(_setting_diagnostic(seam, "must be callable"))
    if _SEAM_TRANSPORT in settings and settings[_SEAM_TRANSPORT] is None:
        diagnostics.append(_setting_diagnostic(_SEAM_TRANSPORT, "must be a transport"))

    for field_name, (_default, minimum, maximum) in INTEGER_SETTINGS.items():
        if field_name not in settings:
            continue
        value = settings[field_name]
        if not _is_int(value):
            diagnostics.append(_setting_diagnostic(field_name, "must be an integer"))
        elif not minimum <= value <= maximum:
            diagnostics.append(
                _setting_diagnostic(
                    field_name, f"must be between {minimum} and {maximum} inclusive"
                )
            )

    provider = settings.get(_SETTING_PROVIDER, DEFAULT_PROVIDER)
    if not isinstance(provider, str) or provider not in PROVIDERS:
        diagnostics.append(
            _setting_diagnostic(_SETTING_PROVIDER, "must be one of the supported providers")
        )
    if _SETTING_BRAVE_API_KEY in settings and not isinstance(
        settings[_SETTING_BRAVE_API_KEY], str
    ):
        diagnostics.append(_setting_diagnostic(_SETTING_BRAVE_API_KEY, "must be a string"))
    elif provider == PROVIDER_BRAVE and not _is_text(settings.get(_SETTING_BRAVE_API_KEY)):
        diagnostics.append(
            _setting_diagnostic(
                _SETTING_BRAVE_API_KEY, "is required and must be non-empty for this provider"
            )
        )

    if _SETTING_ALLOWED_HOSTS in settings:
        diagnostics.extend(_allowed_hosts_diagnostics(settings[_SETTING_ALLOWED_HOSTS]))

    if _SETTING_WIKIPEDIA_LANGUAGE in settings:
        language = settings[_SETTING_WIKIPEDIA_LANGUAGE]
        if not isinstance(language, str) or _LANGUAGE.fullmatch(language) is None:
            diagnostics.append(
                _setting_diagnostic(
                    _SETTING_WIKIPEDIA_LANGUAGE,
                    "must be a 2-3 letter language code with an optional subtag",
                )
            )

    if _SETTING_USER_AGENT_CONTACT in settings:
        contact = settings[_SETTING_USER_AGENT_CONTACT]
        if (
            not isinstance(contact, str)
            or len(contact) > MAX_USER_AGENT_CONTACT
            or not contact.isprintable()
        ):
            diagnostics.append(
                _setting_diagnostic(
                    _SETTING_USER_AGENT_CONTACT,
                    f"must be printable text of at most {MAX_USER_AGENT_CONTACT} characters",
                )
            )

    if _SETTING_DEBUG_LENGTHS in settings and not isinstance(
        settings[_SETTING_DEBUG_LENGTHS], bool
    ):
        diagnostics.append(_setting_diagnostic(_SETTING_DEBUG_LENGTHS, "must be a boolean"))

    if _ACCEPTED_LIMITS_SETTING in settings and not isinstance(
        settings[_ACCEPTED_LIMITS_SETTING], Mapping
    ):
        diagnostics.append(_setting_diagnostic(_ACCEPTED_LIMITS_SETTING, "must be a mapping"))
    return diagnostics


def _allowed_hosts_diagnostics(value: Any) -> list[str]:
    """The diagnostics of ``allowed_hosts``, value-free: one per kind of fault."""

    if not isinstance(value, (list, tuple)):
        return [_setting_diagnostic(_SETTING_ALLOWED_HOSTS, "must be a list of host names")]
    diagnostics: list[str] = []
    if len(value) > MAX_ALLOWED_HOSTS:
        diagnostics.append(
            _setting_diagnostic(
                _SETTING_ALLOWED_HOSTS, f"must have at most {MAX_ALLOWED_HOSTS} entries"
            )
        )
    if not all(normalize_host_name(entry) is not None for entry in value):
        diagnostics.append(
            _setting_diagnostic(
                _SETTING_ALLOWED_HOSTS,
                "must list host names only: no scheme, port, path, userinfo or wildcard",
            )
        )
    return diagnostics


def normalize_host_name(value: Any) -> str | None:
    """*value* as a lower-case, IDNA-encoded host name, or ``None``.

    A host name is labels separated by dots — one trailing dot accepted and
    dropped — each 1–63 characters of letters, digits and inner hyphens once
    encoded, at most 253 in all. A scheme, a port, a path, userinfo, a
    wildcard or whitespace makes it no host name. The encoder's error is
    never read: a name that does not encode is simply ``None``.
    """

    if not isinstance(value, str) or not value:
        return None
    host = value[:-1] if value.endswith(".") else value
    if not host or any(character in host for character in "/:@*?#[] \t\r\n\\"):
        return None
    try:
        encoded = host.encode("idna").decode("ascii").lower()
    except Exception:  # noqa: BLE001 - any encoder failure is "not a host name"
        return None
    if len(encoded) > _MAX_HOST_LENGTH:
        return None
    if not all(_HOST_LABEL.fullmatch(label) for label in encoded.split(".")):
        return None
    return encoded


def _setting_diagnostic(field_name: str, reason: str) -> str:
    return f"module {MODULE_NAME!r}: field {field_name!r}: {reason}"


def _configured_strings(settings: Mapping[str, Any]) -> tuple[str, ...]:
    """Every string value the settings hold, to mask out of a field name."""

    found: list[str] = []
    pending: list[Any] = list(settings.values())
    while pending:
        value = pending.pop()
        if isinstance(value, str):
            found.append(value)
        elif isinstance(value, Mapping):
            pending.extend(value.values())
        elif isinstance(value, (list, tuple)):
            pending.extend(value)
    return tuple(found)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


@dataclass(frozen=True, slots=True)
class _Settings:
    """The accepted settings, parsed once, every default applied (R5).

    ``brave_api_key`` is kept only to authenticate a Brave request; it is
    never rendered into a diagnostic, a trace or an observation.
    """

    provider: str = DEFAULT_PROVIDER
    brave_api_key: str | None = None
    max_results: int = INTEGER_SETTINGS["max_results"][0]
    max_excerpt_bytes: int = INTEGER_SETTINGS["max_excerpt_bytes"][0]
    max_text_bytes: int = INTEGER_SETTINGS["max_text_bytes"][0]
    max_response_bytes: int = INTEGER_SETTINGS["max_response_bytes"][0]
    max_redirects: int = INTEGER_SETTINGS["max_redirects"][0]
    request_timeout_seconds: int = INTEGER_SETTINGS["request_timeout_seconds"][0]
    max_concurrent_requests: int = INTEGER_SETTINGS["max_concurrent_requests"][0]
    cache_ttl_seconds: int = INTEGER_SETTINGS["cache_ttl_seconds"][0]
    cache_max_entries: int = INTEGER_SETTINGS["cache_max_entries"][0]
    cache_max_bytes: int = INTEGER_SETTINGS["cache_max_bytes"][0]
    allowed_hosts: tuple[str, ...] = ()
    wikipedia_language: str = DEFAULT_WIKIPEDIA_LANGUAGE
    user_agent_contact: str = DEFAULT_USER_AGENT_CONTACT
    debug_lengths: bool = DEFAULT_DEBUG_LENGTHS

    def __repr__(self) -> str:
        # The key must not ride out in a repr someone logs.
        return f"_Settings(provider={self.provider!r}, ...)"

    @classmethod
    def from_mapping(cls, settings: Mapping[str, Any]) -> "_Settings":
        """Parse settings :func:`validate_settings` accepted, defaults applied."""

        key = settings.get(_SETTING_BRAVE_API_KEY)
        hosts = tuple(
            host
            for host in (
                normalize_host_name(entry) for entry in settings.get(_SETTING_ALLOWED_HOSTS, ())
            )
            if host is not None
        )
        return cls(
            provider=str(settings.get(_SETTING_PROVIDER, DEFAULT_PROVIDER)),
            brave_api_key=key if isinstance(key, str) and key else None,
            allowed_hosts=tuple(dict.fromkeys(hosts)),
            wikipedia_language=str(
                settings.get(_SETTING_WIKIPEDIA_LANGUAGE, DEFAULT_WIKIPEDIA_LANGUAGE)
            ),
            user_agent_contact=str(
                settings.get(_SETTING_USER_AGENT_CONTACT, DEFAULT_USER_AGENT_CONTACT)
            ),
            debug_lengths=bool(settings.get(_SETTING_DEBUG_LENGTHS, DEFAULT_DEBUG_LENGTHS)),
            **{
                name: int(settings.get(name, default))
                for name, (default, _minimum, _maximum) in INTEGER_SETTINGS.items()
            },
        )


# --------------------------------------------------------------------------- #
# The handle
# --------------------------------------------------------------------------- #


class WebModule:
    """The v2 handle: ready at ``prepare`` with nothing opened (R1)."""

    def __init__(
        self,
        context: Any,
        settings: _Settings,
        *,
        transport: Any = None,
        resolver: Any = None,
        sleeper: Any = None,
    ) -> None:
        self._actions = context.actions
        self._clock = context.clock
        self._supervision = getattr(context, "supervision", None)
        self._settings = settings
        # The seams, or ``None`` for the shipped default, bound on first use:
        # holding a reference opens nothing.
        self._transport_seam = transport
        self._resolver = resolver
        self._sleeper = sleeper
        #: The transport a call opened, which ``close`` must close.
        self._opened_transport: Any = None
        self._prepared = False
        self._closed = False

    @property
    def settings(self) -> _Settings:
        return self._settings

    # -- lifecycle hooks ---------------------------------------------------- #

    async def prepare(self) -> None:
        """Mark the module ready, opening nothing (R1, AC4).

        No provider is bound yet, so the executor answers both actions
        ``provider_not_ready``; no name is resolved and no connection made.
        """

        if self._prepared or self._closed:
            return
        self._actions.mark_ready()
        self._prepared = True

    async def close(self) -> None:
        """Withdraw readiness, then close a transport a call opened."""

        if self._closed:
            return
        self._closed = True
        if self._prepared:
            self._actions.mark_not_ready()
        transport, self._opened_transport = self._opened_transport, None
        closer = getattr(transport, "close", None)
        if callable(closer):
            try:
                outcome = closer()
                if inspect.isawaitable(outcome):
                    await outcome
            except Exception:  # noqa: BLE001 - closing must not mask shutdown
                return


# --------------------------------------------------------------------------- #
# Activation
# --------------------------------------------------------------------------- #


async def activate(
    context: Any,
    settings: Mapping[str, Any],
    catalog: Mapping[str, Mapping[str, Any]],
) -> WebModule:
    """Build the handle from the scoped runtime context.

    *context* is the module-scoped view of the versioned runtime: the action
    facade readiness is recorded on and the clock every bound is measured
    against. Settings are checked through the same hook the loader ran, so a
    handle built outside the loader is refused on the same terms. The seams
    ``_transport``, ``_resolver`` and ``_sleeper`` are read from *settings*;
    nothing is opened here.
    """

    del catalog  # Capabilities are declared by the colocated manifest.
    _require_runtime_surfaces(context)
    if not isinstance(settings, Mapping):
        raise WebModuleError(f"{MODULE_NAME} configuration: settings must be a mapping")
    diagnostics = validate_settings(settings)
    if diagnostics:
        raise WebModuleError(
            f"{MODULE_NAME} configuration: settings were refused "
            f"({len(diagnostics)} diagnostics)"
        )
    advisory_marker()  # A manifest without its marker is refused here, not at a call.
    return WebModule(
        context,
        _Settings.from_mapping(settings),
        transport=settings.get(_SEAM_TRANSPORT),
        resolver=settings.get(_SEAM_RESOLVER),
        sleeper=settings.get(_SEAM_SLEEPER),
    )


def _require_runtime_surfaces(context: Any) -> None:
    """Refuse a context lacking the actions facade or a clock.

    Duck-typed so a harness may inject fakes, while a mis-wired runtime is
    refused at activation with one diagnostic rather than at the first call.
    """

    actions = getattr(context, "actions", None)
    clock = getattr(context, "clock", None)
    if (
        actions is None
        or not all(
            callable(getattr(actions, method, None))
            for method in ("register", "mark_ready", "mark_not_ready")
        )
        or not callable(clock)
    ):
        raise WebModuleError(f"{MODULE_NAME} activation: runtime context is invalid")


__all__ = [
    "DEFAULT_DEBUG_LENGTHS",
    "DEFAULT_PROVIDER",
    "DEFAULT_USER_AGENT_CONTACT",
    "DEFAULT_WIKIPEDIA_LANGUAGE",
    "INTEGER_SETTINGS",
    "MANIFEST_PATH",
    "MAX_ALLOWED_HOSTS",
    "MAX_USER_AGENT_CONTACT",
    "MODULE_NAME",
    "PROVIDERS",
    "PROVIDER_BRAVE",
    "PROVIDER_WIKIPEDIA",
    "WEB_READ_ACTION",
    "WEB_SEARCH_ACTION",
    "WebModule",
    "WebModuleError",
    "activate",
    "advisory_marker",
    "normalize_host_name",
    "validate_settings",
]
