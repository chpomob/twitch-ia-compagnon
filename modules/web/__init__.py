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

The framing, as the guide restates it: the tool message's first line is one
JSON object, and nothing follows it; ``result.advisory`` is the marker and
sorts before every external field, nested search items included; every
external string — ``final_url``, ``title``, ``text``, and each result's
``title``, ``url`` and ``excerpt`` — is a JSON string value under
``result``, recovered byte-for-byte by ``json.loads`` of that first line.
A page that carries the marker's text or a closing delimiter such as
``"}`` or ``"]}`` only adds bytes inside one such value. The text is
returned unfiltered: no content-based rewriting or scoring.

**What is recorded, and what never is (R7).** Traces and diagnostics carry
the provider, byte lengths, counts, the cache-hit flag and the outcome —
never a query, a URL's path or query string, page or excerpt text, or the
Brave key's value. Every diagnostic this module produces is a fixed string
or a format of fixed vocabulary and counts; no exception text from the
transport, the codecs or the address parser is ever rendered. The module
logs on ``modules.web`` one line per call that does not succeed — the
action, the status, the code and the provider, e.g. ``web.search refused:
rate_limited provider=brave`` — and nothing on a success. Only with
``debug_lengths: true`` does that line add byte lengths: ``query_bytes=``
and ``response_bytes=`` for a search, ``url_bytes=``, ``response_bytes=``
and ``text_bytes=`` for a read, and nothing else. The settings, the
backends, the cache and its entries, a fetched answer and a target each
have a repr that holds no key, query, path or text.

**Bounds (R5).** Every bound is a setting with a validated inclusive range,
checked by the core schema subset at startup and by ``--check-config``, and
again by :func:`validate_settings` — the declared hook — which also checks
what the subset cannot say: at most 64 ``allowed_hosts``, each a host name;
the ``wikipedia_language`` shape; and a non-empty ``brave_api_key`` under
``provider: brave``.

**Readiness.** ``prepare`` binds the ``web.search`` and ``web.read``
providers, each spec built from the colocated manifest, and marks the module
ready, opening nothing: no resolution and no connection happen before a call
(R1, AC4). ``close`` withdraws readiness and closes a transport a call
opened.

**``web.search`` (R1, R2, R6).** The provider delegates to the configured
backend — :class:`WikipediaBackend` (MediaWiki ``list=search`` on the
``wikipedia_language`` wiki, no credential) or :class:`BraveBackend` (the
Brave web-search endpoint, the key in its subscription header and nowhere
else, not even its repr). A backend is a ``name``, a ``build_request(query,
limit)`` and a ``parse(body)`` answering the results and the dropped count,
or :data:`MALFORMED`: a third provider adds a class and an enum value, not a
contract field. The endpoint is fetched through the same guard and pinning
as a read but never against ``allowed_hosts``; only a 2xx body is read,
bounded, and must parse as the documented JSON shape. A result with no title
or no ``http(s)`` URL, or that cannot be used at all (text that is not valid
UTF-8), is dropped and counted in the trace's ``dropped_count`` while its
siblings are kept; an answer whose every item is dropped is still
``malformed_response``, its trace still counting them. Every excerpt goes through the
extractor and :func:`cut_utf8`; the results keep the provider's order, at
most ``max_results``. Admission is shared with reads. The trace names the
backend and adds only the query's byte length, the result and dropped
counts, the redirects, the cache-hit flag and the outcome (R7). Brave omits
``web`` for some empty answers; the specification classifies a missing list
as malformed, and so does this module.

**``web.read`` (R3, R6).** One call is admitted against the in-flight bound
(``resource_busy`` when ``max_concurrent_requests`` are already running,
before any resolution or connection), fetched through the guard with the
allowlist applied, and its content type judged on the final answer's headers
alone: anything but ``text/html``, ``application/xhtml+xml`` or
``text/plain`` is ``refused unsupported_content_type`` with the response
closed before one ``read``. The bounded body is extracted, cut to
``max_text_bytes`` and returned with the marker, the final URL, the title
when there is one and ``truncated`` — whether the body or the text was cut.
:func:`_classify` is the one R6 table both actions answer from, and each
code has one fixed message. The provenance names the provider ``http`` and
adds to the ``action.completed`` trace only counts, lengths, the cache-hit
flag and the outcome code (R7).

**The cache (R5).** One :class:`_ObservationCache` in process memory
serves both actions. It is keyed by :func:`cache_key` — the compact
sorted-key JSON of the action, the provider (``http`` for a read) and the
canonical arguments — and holds only successes, each as the complete
observation the provider returned. It is consulted after admission, so a hit
takes and at once releases a slot, resolves and connects nothing, and comes
back as a copy whose ``cache_hit`` trace field is true. An entry older than
``cache_ttl_seconds`` on the runtime clock is never served; least recently
used entries are evicted until at most ``cache_max_entries`` entries and
``cache_max_bytes`` bytes are held, an entry's size being the UTF-8 bytes
of its key plus those of the whole observation as compact sorted-key JSON;
``cache_ttl_seconds: 0`` disables it. A key or an entry never leaves it.

**The network guard (R4).** Every request goes through the same guard,
at every hop. :func:`parse_target` accepts only ``http``/``https`` URLs that
have a host and no userinfo, and normalises the host: no brackets, lower
case, no trailing dot, IDNA-encoded. :func:`literal_address` classifies
every spelling the platform resolver would read as an address, the legacy
IPv4 forms included (``2130706433``, ``0x7f.1``, ``017700000001``,
``1.2.3``), so a literal is checked as an address and never sent to the
resolver as a name. :func:`address_refusal` unwraps IPv4-mapped and
IPv4-compatible IPv6 to the embedded IPv4, then refuses every address in an
explicit table of non-public ranges, and any other address whose
``is_global`` is false. The table is explicit because ``ipaddress``
classifies some ranges differently across the supported Python versions.
:func:`resolve_checked` resolves a name once and refuses the call if *any*
answer is refused. The addresses it returns are the only ones a
connection may use. :func:`host_allowed` is the ``allowed_hosts`` exact
match, which applies to reads only. A refusal is a :class:`GuardRefusal`
that holds only its code, never the URL or the host.

*Not covered:* NAT64 (``64:ff9b::/96``) and 6to4 (``2002::/16``) also embed
an IPv4 address, but the specification names only the mapped and
compatible forms, so these two are left to ``is_global``. ``is_global``
treats both as global, so ``64:ff9b::7f00:1`` is not refused as loopback.
This gap is recorded here and was not checked in another way.

**The guarded transport (R4, R6).** :meth:`WebModule.fetch` is the one
way out. Per hop it parses the URL, applies ``allowed_hosts`` to reads
(before any resolution), resolves once through :func:`resolve_checked` and
hands the transport the *pinned* addresses — never a bare name to resolve —
so a name that answers differently later cannot reach a refused address.
A redirect (301, 302, 303, 307, 308 with a ``Location``) is closed unread,
joined to the current URL and checked again in full; more than
``max_redirects`` of them is ``too_many_redirects``. :func:`read_bounded`
asks the stream for at most the remaining ``max_response_bytes + 1``
allowance per ``read``, so no more than that is ever consumed, and keeps at
most ``max_response_bytes``. The whole call races one sleep of
``request_timeout_seconds``; losing it is ``timeout timed_out``. Every
failure is a code, never an exception's text. :class:`AiohttpTransport`, the
default, takes no proxy from the environment, follows no redirect itself,
and connects only through a :class:`_PinnedResolver`. Pinning on a real
socket is proven by unit checks of that resolver and by the operator trial,
not by a test.

**The extractor (R3).** :func:`extract_text` turns a body into a title
and text with no dependency: the charset is the header's ``charset=``, else
a ``<meta>`` declaration in the first 1024 bytes, else UTF-8, and decoding
always replaces, never fails. HTML goes through an ``html.parser``
subclass that decodes character references, drops comments, declarations,
processing instructions and the content of ``script``, ``style``,
``noscript`` and ``template`` — each counted on its own, so nesting or an
unclosed tag cannot leak, and a skipped subtree changes neither the title,
nor preformatting, nor a line break — and breaks lines at block elements.
Nothing runs and nothing is rendered. The ``<meta>`` prescan reads elements,
not text: a comment or another attribute naming a charset decides nothing.
A label is used only if it names a text codec that decodes with
replacement. The text, and every cut of it, is always UTF-8 encodable.
:func:`cut_utf8` is the one byte bound on text: it cuts on a character
boundary and says whether it cut. A ``web.read`` extracts — and a
``web.search`` decodes its JSON and builds each result — on a worker thread
under what is left of the call's one deadline, and the walk checks that
deadline as it goes, in steps no single match outlasts (a tag's end, a JSON
value), so an answer too big to parse in time is ``timeout timed_out``,
never a late success. The cache store is under the same deadline: a success
ready only once it expired is not stored.

**Seams, for the tests.** ``_transport`` replaces the HTTP transport,
``_resolver`` the name resolution and ``_sleeper`` the sleep a bound is
raced against. They are read from *settings*, never from a configuration
file, and bound lazily: activation opens nothing.

Lifecycle: the manifest declares no role, so the handle takes part in
``prepare`` and in ``close``.
"""

from __future__ import annotations

import asyncio
import codecs
import contextvars
import functools
import importlib.metadata
import inspect
import ipaddress
import json
import json.decoder
import json.scanner
import logging
import re
import socket
import threading
import urllib.parse
from collections.abc import Callable, Mapping, Sequence
import dataclasses
from dataclasses import dataclass
# The codec every decode falls back to, imported now: the first decode runs
# on a worker thread, where the import's file reads would each have to win
# the interpreter back from a busy event loop.
import encodings.utf_8_sig  # noqa: F401
import html.parser
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, NamedTuple

import yaml

from core.contracts import ActionObservation, ActionSpec, Destination, mask_known


MODULE_NAME = "web"

#: The one logger this module writes its value-free diagnostics to (R7).
LOGGER_NAME = "modules.web"
_LOG = logging.getLogger(LOGGER_NAME)

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


# --------------------------------------------------------------------------- #
# The network guard (R4)
# --------------------------------------------------------------------------- #

#: The guard's refusal codes, a fixed vocabulary (R7).
REFUSAL_BLOCKED_ADDRESS = "blocked_address"
REFUSAL_HOST_NOT_ALLOWED = "host_not_allowed"
REFUSAL_UNSUPPORTED_SCHEME = "unsupported_scheme"
REFUSAL_UNSUPPORTED_URL = "unsupported_url"
REFUSAL_TRANSPORT_FAILED = "transport_failed"

_SCHEME_PORTS: Mapping[str, int] = {"http": 80, "https": 443}

#: Characters a legacy IPv4 spelling is made of; only these reach ``inet_aton``,
#: whose platform version may ignore anything after whitespace.
_LEGACY_IPV4 = re.compile(r"[0-9a-fx.]+")

#: Non-public IPv4 ranges, refused whatever ``is_global`` says on this Python.
_BLOCKED_IPV4 = tuple(
    ipaddress.IPv4Network(network)
    for network in (
        "0.0.0.0/8",  # "this network", unspecified
        "10.0.0.0/8",  # private
        "100.64.0.0/10",  # shared (CGNAT)
        "127.0.0.0/8",  # loopback
        "169.254.0.0/16",  # link-local
        "172.16.0.0/12",  # private
        "192.0.0.0/24",  # IETF protocol assignments
        "192.0.2.0/24",  # documentation
        "192.168.0.0/16",  # private
        "198.18.0.0/15",  # benchmarking
        "198.51.100.0/24",  # documentation
        "203.0.113.0/24",  # documentation
        "224.0.0.0/4",  # multicast
        "240.0.0.0/4",  # reserved, and the broadcast 255.255.255.255
    )
)

#: Non-public IPv6 ranges; mapped and compatible IPv4 are unwrapped first.
_BLOCKED_IPV6 = tuple(
    ipaddress.IPv6Network(network)
    for network in (
        "::/128",  # unspecified
        "::1/128",  # loopback
        "::ffff:0:0/96",  # IPv4-mapped, if ever reached un-unwrapped
        "100::/64",  # discard-only
        "2001:db8::/32",  # documentation
        "3fff::/20",  # documentation (RFC 9637), global on older Pythons
        "fc00::/7",  # unique local
        "fe80::/10",  # link-local
        "fec0::/10",  # site-local
        "ff00::/8",  # multicast
    )
)

Address = ipaddress.IPv4Address | ipaddress.IPv6Address


@dataclass(frozen=True, slots=True)
class GuardRefusal:
    """A refused request: only its code, never the URL, host or address."""

    code: str


@dataclass(frozen=True, slots=True)
class Target:
    """One parsed request target, its host normalised (R4).

    ``host`` is lower case, IDNA-encoded, with no brackets and no trailing
    dot; ``port`` is the effective port; ``address`` is the literal address
    when the host is one, else ``None``. ``path`` is the path and query
    string to request (the fragment is dropped). The repr omits it, because
    a URL's path is never recorded (R7).
    """

    scheme: str
    host: str
    port: int
    path: str
    address: Address | None = None

    def __repr__(self) -> str:
        return f"Target(scheme={self.scheme!r}, host={self.host!r}, port={self.port!r})"


def parse_target(url: Any) -> Target | GuardRefusal:
    """*url* as a :class:`Target`, or the refusal the guard answers.

    A scheme other than ``http`` or ``https`` is ``unsupported_scheme``.
    Userinfo, a missing host, a port that does not parse, a host that is
    neither an address nor a host name, or an IDNA failure is
    ``unsupported_url``. The parser's error text is never read.
    """

    if not isinstance(url, str):
        return GuardRefusal(REFUSAL_UNSUPPORTED_URL)
    try:
        parts = urllib.parse.urlsplit(url)
        scheme = parts.scheme.lower()
        if scheme not in _SCHEME_PORTS:
            return GuardRefusal(REFUSAL_UNSUPPORTED_SCHEME)
        if "@" in parts.netloc:
            return GuardRefusal(REFUSAL_UNSUPPORTED_URL)
        hostname = parts.hostname
        port = parts.port
    except ValueError:
        return GuardRefusal(REFUSAL_UNSUPPORTED_URL)
    if not hostname:
        return GuardRefusal(REFUSAL_UNSUPPORTED_URL)
    effective_port = _SCHEME_PORTS[scheme] if port is None else port
    path = parts.path or "/"
    if parts.query:
        path = f"{path}?{parts.query}"

    if parts.netloc.startswith("["):
        try:
            address = ipaddress.IPv6Address(hostname)
        except ValueError:
            return GuardRefusal(REFUSAL_UNSUPPORTED_URL)
        return Target(scheme, str(address), effective_port, path, address)

    host = hostname[:-1] if hostname.endswith(".") else hostname
    address = literal_address(host)
    if isinstance(address, ipaddress.IPv4Address):
        return Target(scheme, str(address), effective_port, path, address)
    if address is not None:  # an unbracketed IPv6 literal is not a URL host
        return GuardRefusal(REFUSAL_UNSUPPORTED_URL)
    name = normalize_host_name(host)
    if name is None:
        return GuardRefusal(REFUSAL_UNSUPPORTED_URL)
    return Target(scheme, name, effective_port, path, None)


def literal_address(host: Any) -> Address | None:
    """*host* as an address when the platform resolver would read it as one.

    Canonical forms come from ``ipaddress``. Every legacy IPv4 spelling
    ``inet_aton`` accepts (decimal, hexadecimal, octal, short forms) is an
    address too. That is the safe side: a literal is checked as an address
    and never resolved as a name.
    """

    if not isinstance(host, str) or not host:
        return None
    candidate = host.strip("[]").lower()
    try:
        return ipaddress.ip_address(candidate)
    except ValueError:
        pass
    if _LEGACY_IPV4.fullmatch(candidate) is None:
        return None
    try:
        return ipaddress.IPv4Address(socket.inet_aton(candidate))
    except (OSError, ValueError):
        return None


def address_refusal(address: Any) -> str | None:
    """``"blocked_address"`` unless *address* is globally reachable (R4).

    IPv4-mapped (``::ffff:a.b.c.d``) and IPv4-compatible (``::a.b.c.d``)
    IPv6 are judged by their embedded IPv4. ``::`` and ``::1`` are not
    unwrapped and are refused as unspecified and loopback.
    """

    if not isinstance(address, (ipaddress.IPv4Address, ipaddress.IPv6Address)):
        return REFUSAL_BLOCKED_ADDRESS
    if isinstance(address, ipaddress.IPv6Address):
        value = int(address)
        address = ipaddress.IPv6Address(value)  # drop any scope id
        if address.ipv4_mapped is not None:
            address = address.ipv4_mapped
        elif value >> 32 == 0 and value > 1:
            address = ipaddress.IPv4Address(value)
    table = _BLOCKED_IPV4 if isinstance(address, ipaddress.IPv4Address) else _BLOCKED_IPV6
    if any(address in network for network in table):
        return REFUSAL_BLOCKED_ADDRESS
    if (
        address.is_loopback
        or address.is_private
        or address.is_link_local
        or address.is_unspecified
        or address.is_multicast
        or address.is_reserved
        or getattr(address, "is_site_local", False)
        or not address.is_global
    ):
        return REFUSAL_BLOCKED_ADDRESS
    return None


def host_allowed(host: Any, allowed_hosts: Any) -> bool:
    """Whether a read may go to *host* under ``allowed_hosts`` (R4).

    An empty list allows any host. Otherwise *host* must equal one entry
    exactly once both are normalised (case-insensitive, IDNA). A suffix or
    a wildcard never matches. This never applies to the fixed provider
    endpoints.
    """

    entries = tuple(allowed_hosts or ())
    if not entries:
        return True
    name = normalize_host_name(host)
    if name is None:
        return False
    return any(normalize_host_name(entry) == name for entry in entries)


async def resolve_checked(target: Target, resolver: Any = None) -> list[Address] | GuardRefusal:
    """The addresses *target* may be connected to, or the refusal.

    A literal is checked directly and never resolved. A name is resolved
    once through *resolver* (by default the running loop's ``getaddrinfo``
    for a stream socket). The call is refused ``blocked_address`` if *any*
    answer is refused, and ``transport_failed`` if resolution fails or
    returns nothing. The list returned is the pinned set: a connection is
    made only to one of these addresses.
    """

    if target.address is not None:
        if address_refusal(target.address) is not None:
            return GuardRefusal(REFUSAL_BLOCKED_ADDRESS)
        return [target.address]
    if resolver is None:
        resolver = asyncio.get_running_loop().getaddrinfo
    try:
        answers = await resolver(target.host, target.port, type=socket.SOCK_STREAM)
        answers = list(answers or ())
    except Exception:  # noqa: BLE001 - the resolver's text names the host
        return GuardRefusal(REFUSAL_TRANSPORT_FAILED)
    if not answers:
        return GuardRefusal(REFUSAL_TRANSPORT_FAILED)
    addresses: list[Address] = []
    for answer in answers:
        address = _answer_address(answer)
        if address is None or address_refusal(address) is not None:
            return GuardRefusal(REFUSAL_BLOCKED_ADDRESS)
        if address not in addresses:
            addresses.append(address)
    return addresses


def _answer_address(answer: Any) -> Address | None:
    """One resolver answer as an address: a ``getaddrinfo`` tuple or a string."""

    if isinstance(answer, tuple):
        try:
            answer = answer[4][0]
        except (IndexError, TypeError):
            return None
    if isinstance(answer, (ipaddress.IPv4Address, ipaddress.IPv6Address)):
        return answer
    if not isinstance(answer, str):
        return None
    try:
        return ipaddress.ip_address(answer)
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# The HTML-to-text extractor and charset decoding (R3)
# --------------------------------------------------------------------------- #

#: The media type read as plain text; every other readable one is markup.
_PLAIN_TEXT = "text/plain"

#: How far into a document a ``<meta>`` charset declaration is looked for.
_META_SCAN_BYTES = 1024

#: A charset label, as the prescan accepts one.
_CHARSET_LABEL = re.compile(r"[A-Za-z0-9_.:+-]+")

#: The ``charset=`` parameter in a ``<meta http-equiv>``'s ``content``.
_CONTENT_CHARSET = re.compile(r"charset\s*=\s*[\"']?\s*([A-Za-z0-9_.:+-]+)", re.IGNORECASE)

#: A lone surrogate: decodable by some codecs, encodable by none in UTF-8.
_SURROGATE = re.compile("[\ud800-\udfff]")

#: How many parser events pass between two looks at the deadline.
_DEADLINE_CHECK_EVENTS = 256

#: How much decoded markup one ``feed`` gets, at most, so a run of text with
#: no tag still reaches a deadline check. It is also how many characters of
#: start tags the parser reads between two looks at the deadline, and the
#: longest start tag whose attributes it parses.
_FEED_CHARS = 65_536

#: The characters that end a character reference, wherever it began: a cut
#: after one of them never splits a reference (html.unescape's grammar).
_CHARREF_ENDS = frozenset("\t\n\f <&;")

#: Elements whose content never reaches the text, however they are nested.
_SKIPPED_ELEMENTS = frozenset({"script", "style", "noscript", "template"})

#: Elements whose start and end separate lines.
_BLOCK_ELEMENTS = frozenset(
    {
        "address", "article", "aside", "blockquote", "body", "br", "caption",
        "dd", "details", "dialog", "div", "dl", "dt", "fieldset", "figcaption",
        "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6", "head",
        "header", "hr", "html", "li", "main", "nav", "ol", "p", "pre",
        "section", "summary", "table", "tbody", "td", "tfoot", "th", "thead",
        "title", "tr", "ul",
    }
)  # fmt: skip

#: A start tag's name, as the standard parser reads it after the ``<``.
_TAG_NAME = re.compile(r"([a-zA-Z][^\t\n\r\f />]*)")

#: Whether this ``html.parser`` finds a tag's end with ``locatetagend``, the
#: grammar :data:`_TAG_HEAD` and :data:`_TAG_ATTRIBUTES` restate in steps.
_STEPPED_TAG_END = hasattr(html.parser, "locatetagend")

#: How many attributes one step of the tag-end scan reads, at most.
_ATTRIBUTES_PER_STEP = 1024

#: ``locatetagend`` up to its attributes: the name and the space after it.
_TAG_HEAD = re.compile(r"[a-zA-Z][^\t\n\r\f />]*[\t\n\r\f /]*")

#: At most :data:`_ATTRIBUTES_PER_STEP` of ``locatetagend``'s attributes.
#: Nothing after them can fail, so the greedy repetition never gives one
#: back: steps that start where the last one stopped end where the single
#: match would, and each is one bounded call the deadline can sit between.
_TAG_ATTRIBUTES = re.compile(
    r"""
  (?:(?<=['"\t\n\r\f /])[^\t\n\r\f />][^\t\n\r\f /=>]*  # attribute name
    (?:[\t\n\r\f ]*=[\t\n\r\f ]*    # value indicator
      (?:'[^']*'                    # LITA-enclosed value
        |"[^"]*"                    # LIT-enclosed value
        |(?!['"])[^>\t\n\r\f ]*     # bare value
       )
     )?
    [\t\n\r\f /]*                   # possibly followed by a space
   ){0,%d}
"""
    % _ATTRIBUTES_PER_STEP,
    re.VERBOSE,
)

_INLINE_WHITESPACE = re.compile(r"[^\S\n]+")
_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class Extracted:
    """A page's text: its first ``<title>``, if any, and its body text."""

    title: str | None
    text: str


class ExtractionAbandoned(Exception):
    """The deadline the extraction ran under expired before it finished."""


#: The deadline the current extraction checks as it walks: a callable that
#: answers ``True`` once the call has run out of time, or ``None``.
_EXTRACTION_EXPIRED: contextvars.ContextVar[Callable[[], bool] | None] = (
    contextvars.ContextVar("web_extraction_expired", default=None)
)


def _check_deadline() -> None:
    expired = _EXTRACTION_EXPIRED.get()
    if expired is not None and expired():
        raise ExtractionAbandoned


def extract_text(body: bytes, content_type_header: str | None) -> Extracted:
    """The title and text of *body*, decoded and stripped of markup (R3).

    The charset is the header's ``charset=`` parameter, else — for markup —
    a ``<meta>`` declaration in the first 1024 bytes, else UTF-8; a label no
    codec answers to is UTF-8 too, and decoding always replaces what it
    cannot read, so a wrong label mis-decodes but never fails. ``text/plain``
    is only normalised; anything else is parsed as HTML, the content of
    ``script``, ``style``, ``noscript`` and ``template`` and every comment
    dropped. Nothing is executed and nothing is rendered. The text and the
    title are always UTF-8 encodable.

    Run under a deadline (:data:`_EXTRACTION_EXPIRED`), the walk checks it
    as it goes and raises :class:`ExtractionAbandoned` once it has expired.
    """

    media_type, header_charset = _content_type(content_type_header)
    plain = media_type == _PLAIN_TEXT
    charset = header_charset
    if charset is None and not plain:
        charset = _meta_charset(body)
    text = _decode(body, charset)
    _check_deadline()
    if plain:
        return Extracted(title=None, text=_normalise(text))
    parser = _TextParser()
    start, size = 0, _FEED_CHARS
    while start < len(text):
        _check_deadline()
        end = _feed_end(text, start, size)
        parser.feed(text[start:end])
        start = end
        # The parser reads a construct still open — a tag, a comment, a
        # script — again from its start at every feed: while one is, each
        # feed doubles, so those re-reads stay linear in the page.
        size = size * 2 if len(parser.rawdata) > _FEED_CHARS else _FEED_CHARS
    parser.close()
    _check_deadline()
    return Extracted(title=parser.title(), text=_normalise("".join(parser.parts)))


def _feed_end(text: str, start: int, size: int) -> int:
    """Where the feed of *text* that begins at *start* ends.

    At most *size* characters on, and never inside a character reference:
    the parser decodes a reference it was fed only in part as if it had
    ended, so a feed whose last ``&`` is still open ends before that ``&``
    instead — the next feed then holds the whole reference, and the text
    is the one a single feed would give. Only a reference longer than a
    whole feed is still cut.
    """

    end = min(len(text), start + size)
    if end == len(text):
        return end
    amp = text.rfind("&", start, end)
    if amp <= start or not _CHARREF_ENDS.isdisjoint(text[amp + 1 : end]):
        return end
    return amp


def cut_utf8(text: str, max_bytes: int) -> tuple[str, bool]:
    """*text* cut to at most *max_bytes* UTF-8 bytes on a character boundary.

    Returns the text and whether anything was cut. A cut that falls inside a
    multi-byte character drops that whole character. Either way the text is
    UTF-8 encodable: a lone surrogate is U+FFFD whatever the bound.
    """

    text = _utf8_safe(text)
    encoded = text.encode("utf-8")
    limit = max(0, max_bytes)
    if len(encoded) <= limit:
        return text, False
    return _utf8_safe(encoded[:limit].decode("utf-8", errors="ignore")), True


def _utf8_safe(text: str) -> str:
    """*text* with every lone surrogate replaced, so UTF-8 encodes it strictly."""

    return _SURROGATE.sub("\ufffd", text)


def _content_type(header: str | None) -> tuple[str, str | None]:
    """The lower-case media type of *header* and its ``charset``, if named."""

    if not isinstance(header, str):
        return "", None
    media_type, *parameters = header.split(";")
    charset = None
    for parameter in parameters:
        name, _, value = parameter.partition("=")
        if name.strip().lower() == "charset":
            charset = value.strip().strip("\"'").strip() or None
            break
    return media_type.strip().lower(), charset


def _meta_charset(body: bytes) -> str | None:
    """The charset a ``<meta>`` in the first 1024 bytes of *body* declares.

    Only an element decides: a ``<meta charset>``, or a ``<meta
    http-equiv="content-type">`` whose ``content`` carries ``charset=``.
    Comments, text and every other attribute are read past. As the WHATWG
    prescan does, a UTF-16 label found this way means UTF-8: a document
    whose bytes the prescan could read is not UTF-16.
    """

    prescan = _MetaPrescan()
    # Latin-1 maps each byte to one character, so ASCII markup reads as is.
    prescan.feed(body[:_META_SCAN_BYTES].decode("latin-1"))
    label = prescan.label
    if label is None:
        return None
    if label.lower().replace("_", "-").startswith("utf-16"):
        return "utf-8"
    return label


class _MetaPrescan(HTMLParser):
    """The first charset a ``<meta>`` element declares, and nothing else."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.label: str | None = None

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag != "meta" or self.label is not None:
            return
        values: dict[str, str] = {}
        for name, value in attrs:
            values.setdefault(name, value or "")
        if "charset" in values:
            match = _CHARSET_LABEL.match(values["charset"].strip())
        elif values.get("http-equiv", "").strip().lower() == "content-type":
            match = _CONTENT_CHARSET.search(values.get("content", ""))
            match = _CHARSET_LABEL.match(match.group(1)) if match else None
        else:
            return
        if match is not None:
            self.label = match.group(0)

    handle_startendtag = handle_starttag


def _text_codec(charset: str | None) -> str:
    """The codec *charset* names, if it is text and decodes with replacement.

    Anything else — an unknown label, a bytes or str transform such as
    ``base64`` or ``rot13``, a codec that rejects the replacement handler
    such as ``idna`` — is UTF-8.
    """

    try:
        info = codecs.lookup(charset or "utf-8")
        if not getattr(info, "_is_text_encoding", True):
            return "utf-8-sig"
        b"\xff\x00probe".decode(info.name, errors="replace")
    except (LookupError, ValueError, TypeError):  # UnicodeError is a ValueError
        return "utf-8-sig"
    return "utf-8-sig" if info.name == "utf-8" else info.name


def _decode(body: bytes, charset: str | None) -> str:
    """*body* decoded by *charset*, UTF-8 when the label names no usable codec.

    Never raises, and the text has no lone surrogate.
    """

    codec = _text_codec(charset)
    try:
        text = body.decode(codec, errors="replace")
    except (LookupError, ValueError, TypeError):  # a codec the probe did not catch
        text = body.decode("utf-8-sig", errors="replace")
    return _utf8_safe(text)


def _normalise(text: str) -> str:
    """Whitespace runs collapsed in each line, empty lines dropped, stripped."""

    lines = (_INLINE_WHITESPACE.sub(" ", line).strip() for line in text.splitlines())
    return "\n".join(line for line in lines if line)


class _TextParser(HTMLParser):
    """Collects the text of a document, outside every skipped element.

    Each skipped element keeps its own open count, so an end tag of one
    cannot close another and an element left unclosed skips to the end of
    the document. A source line break is whitespace, except inside
    ``<pre>``: lines break at block elements. Every ``<title>`` is kept out
    of the text; the first one is the title. Inside a skipped element
    nothing but the skip counts changes: a hidden ``<title>`` neither takes
    the title nor reads as one, a hidden ``<pre>`` or block element breaks
    no line.

    A start tag's attributes are never read, so a tag longer than
    :data:`_FEED_CHARS` is taken by its name alone: the standard parser
    would walk every attribute of it before any handler — and so any look
    at the deadline — runs. Start tags are counted by their length too, so
    the deadline is looked at at least once every :data:`_FEED_CHARS`
    characters of them. The end of a start or end tag is found in bounded
    steps (:meth:`_tag_end`), the deadline looked at between them, so a tag
    still open across feeds is never re-read in one long match.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skipped = dict.fromkeys(_SKIPPED_ELEMENTS, 0)
        self._preformatted = 0
        self._title_parts: list[str] = []
        self._in_title = False
        self._title_done = False
        self._events = 0
        self._tag_chars = 0
        self._known_end: tuple[int, int] | None = None

    def _hidden(self) -> bool:
        return any(self._skipped.values())

    def _walked(self) -> None:
        self._events += 1
        if self._events % _DEADLINE_CHECK_EVENTS == 0:
            _check_deadline()

    def parse_starttag(self, i: int) -> int:
        _check_deadline()
        end = self.check_for_whole_start_tag(i)
        if end < 0:
            return end
        self._tag_chars += end - i
        if self._tag_chars >= _FEED_CHARS:
            self._tag_chars = 0
            _check_deadline()
        if end - i <= _FEED_CHARS:
            # The standard parser asks for the end again: it is known.
            self._known_end = (i, end)
            try:
                return super().parse_starttag(i)
            finally:
                self._known_end = None
        name = _TAG_NAME.match(self.rawdata, i + 1)
        if name is None:  # unreachable: the caller matched ``<`` + letter
            return super().parse_starttag(i)
        self.lasttag = tag = name.group(1).lower()
        if self.rawdata.endswith("/>", i, end):
            self.handle_startendtag(tag, [])
            return end
        self.handle_starttag(tag, [])
        if tag in self.CDATA_CONTENT_ELEMENTS:
            self.set_cdata_mode(tag)
        elif tag in self.RCDATA_CONTENT_ELEMENTS:
            self.set_cdata_mode(tag, escapable=True)
        return end

    def check_for_whole_start_tag(self, i: int) -> int:
        known = self._known_end
        if known is not None and known[0] == i:
            return known[1]
        if not _STEPPED_TAG_END:
            return super().check_for_whole_start_tag(i)
        return self._tag_end(i + 1)

    def parse_endtag(self, i: int) -> int:
        rawdata = self.rawdata
        if (
            not _STEPPED_TAG_END
            or rawdata.find(">", i + 2) < 0
            or not html.parser.endtagopen.match(rawdata, i)
        ):
            return super().parse_endtag(i)
        end = self._tag_end(i + 2)
        if end < 0:
            return -1
        name = html.parser.tagfind_tolerant.match(rawdata, i + 2)
        if name is None:  # unreachable: the end tag opens with a letter
            return super().parse_endtag(i)
        self.handle_endtag(name.group(1).lower())
        self.clear_cdata_mode()
        return end

    def _tag_end(self, name_at: int) -> int:
        """Where the tag whose name starts at *name_at* ends, or ``-1``.

        The standard parser's ``locatetagend`` in bounded steps, the deadline
        looked at between them: one match over a tag of millions of
        attributes holds the interpreter until it returns, so the event
        loop could not end the call on time however often the walk checked
        between matches. No step reads more than
        :data:`_ATTRIBUTES_PER_STEP` attributes.
        """

        rawdata = self.rawdata
        head = _TAG_HEAD.match(rawdata, name_at)
        if head is None:  # unreachable: the caller matched a letter
            return -1
        position = head.end()
        while True:
            end = _TAG_ATTRIBUTES.match(rawdata, position).end()
            if end == position:
                break
            position = end
            _check_deadline()
        return position + 1 if rawdata.startswith(">", position) else -1

    def set_cdata_mode(self, elem: str, **kwargs: Any) -> None:
        # A hidden ``<title>`` or ``<textarea>`` is no element at all, so its
        # content must not swallow the markup after it as escapable text.
        if kwargs.get("escapable") and self._hidden():
            return
        super().set_cdata_mode(elem, **kwargs)

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        self._walked()
        if tag in self._skipped:
            self._skipped[tag] += 1
            return
        if self._hidden():
            return
        if tag == "title":
            self._in_title = True
        elif tag == "pre":
            self._preformatted += 1
        if tag in _BLOCK_ELEMENTS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        self._walked()
        if tag in self._skipped:
            self._skipped[tag] = max(0, self._skipped[tag] - 1)
            return
        if self._hidden():
            return
        if tag == "title" and self._in_title:
            self._in_title = False
            self._title_done = True
        elif tag == "pre":
            self._preformatted = max(0, self._preformatted - 1)
        if tag in _BLOCK_ELEMENTS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self._walked()
        if self._hidden():
            return
        if self._in_title:
            if not self._title_done:
                self._title_parts.append(data)
            return
        self.parts.append(data if self._preformatted else _WHITESPACE.sub(" ", data))

    def title(self) -> str | None:
        return " ".join("".join(self._title_parts).split()) or None


# --------------------------------------------------------------------------- #
# The guarded transport (R4, R6)
# --------------------------------------------------------------------------- #

#: The redirect statuses a fetch follows, each hop re-checked in full.
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})

#: The request headers that carry a credential, compared case-insensitively.
#: They are sent only to the origin the call began at: a redirect to another
#: scheme, host or port drops them for that hop and every later one.
CREDENTIAL_HEADERS = frozenset(
    {"authorization", "proxy-authorization", "cookie", "x-subscription-token"}
)

#: The most body bytes one ``read`` asks the stream for.
READ_CHUNK_BYTES = 65_536

#: How much later than ``request_timeout_seconds`` the default transport's
#: own timeout fires: a backstop only, the whole-call deadline comes first.
TRANSPORT_TIMEOUT_MARGIN_SECONDS = 1.0

#: The executor statuses a fetch failure is classified as (R6).
FETCH_REFUSED = "refused"
FETCH_ERROR = "error"
FETCH_TIMEOUT = "timeout"

REFUSAL_TOO_MANY_REDIRECTS = "too_many_redirects"
ERROR_TIMED_OUT = "timed_out"


class TransportContractError(Exception):
    """A transport broke the seam's contract, e.g. ``read(n)`` gave more than *n*."""


@dataclass(frozen=True, slots=True)
class Fetched:
    """A non-redirect answer: its status, headers, final URL and bounded body.

    ``body`` is ``None`` when the caller's ``accept_body`` declined it, in
    which case not one body byte was read; otherwise it holds at most
    ``max_response_bytes`` bytes and ``truncated`` says whether more existed.
    The repr names only the status, the counts and the flag: the URL's path
    and the body are never recorded (R7).
    """

    status: int
    headers: Mapping[str, str]
    final_url: str
    redirects: int
    body: bytes | None = None
    truncated: bool = False

    def __repr__(self) -> str:
        size = None if self.body is None else len(self.body)
        return (
            f"Fetched(status={self.status!r}, redirects={self.redirects!r}, "
            f"body_bytes={size!r}, truncated={self.truncated!r})"
        )


@dataclass(frozen=True, slots=True)
class FetchFailure:
    """A fetch that ended without an answer: an executor status and a code.

    ``status`` is ``refused``, ``error`` or ``timeout``; ``code`` is from the
    module's fixed vocabulary. Nothing else is held, so nothing else leaks.
    """

    status: str
    code: str
    redirects: int = 0


async def read_bounded(response: Any, max_response_bytes: int) -> tuple[bytes, bool]:
    """At most *max_response_bytes* body bytes of *response*, and whether more existed.

    The allowance starts at ``max_response_bytes + 1`` and each ``read``
    asks for at most what remains of it, so the stream never delivers more
    than that whatever its chunking; the one extra byte only says that more
    exists. Once the allowance is spent no further read is issued and the
    response is closed. A ``read`` giving more than it was asked for breaks
    the seam's contract: :class:`TransportContractError`.
    """

    allowance = max_response_bytes + 1
    parts: list[bytes] = []
    while allowance > 0:
        requested = min(READ_CHUNK_BYTES, allowance)
        data = await response.read(requested)
        if not isinstance(data, (bytes, bytearray)) or len(data) > requested:
            raise TransportContractError("read returned more than requested")
        if not data:
            break
        parts.append(bytes(data))
        allowance -= len(data)
    if allowance <= 0:
        await _close_response(response)
    body = b"".join(parts)
    return body[:max_response_bytes], len(body) > max_response_bytes


async def _close_response(response: Any) -> None:
    """Close *response*, sync or async; a failure to close is not a result."""

    closer = getattr(response, "close", None)
    if not callable(closer):
        return
    try:
        outcome = closer()
        if inspect.isawaitable(outcome):
            await outcome
    except Exception:  # noqa: BLE001 - closing must not mask the outcome
        return


def _header(headers: Any, name: str) -> str | None:
    """The value of header *name* in *headers*, matched case-insensitively."""

    if headers is None:
        return None
    getter = getattr(headers, "get", None)
    if callable(getter):
        value = getter(name)
        if isinstance(value, str):
            return value
    try:
        items = headers.items()
    except AttributeError:
        return None
    wanted = name.lower()
    for key, value in items:
        if isinstance(key, str) and key.lower() == wanted and isinstance(value, str):
            return value
    return None


class _PinnedResolver:
    """aiohttp's resolver for one request: the checked addresses, nothing else.

    It answers only for the host it was pinned to, with exactly the
    addresses :func:`resolve_checked` returned, and refuses every other
    host; it never asks the platform. TLS SNI and ``Host`` keep the name,
    while the connection can only reach an address that passed the guard.
    """

    def __init__(self, host: str, pinned_addresses: Sequence[Address]) -> None:
        self._host = host
        self._addresses = tuple(pinned_addresses)

    async def resolve(
        self, host: str, port: int = 0, family: int = socket.AF_INET
    ) -> list[dict[str, Any]]:
        if host != self._host:
            raise OSError("host is not the pinned host")
        answers = [
            {
                "hostname": host,
                "host": str(address),
                "port": port,
                "family": socket.AF_INET if address.version == 4 else socket.AF_INET6,
                "proto": 0,
                "flags": socket.AI_NUMERICHOST,
            }
            for address in self._addresses
        ]
        if family:
            answers = [answer for answer in answers if answer["family"] == family]
        if not answers:
            raise OSError("no pinned address for this family")
        return answers

    async def close(self) -> None:
        return None


class _AiohttpResponse:
    """One aiohttp answer behind the seam: ``read(n)`` gives at most *n* bytes."""

    def __init__(self, response: Any, session: Any, owner: "AiohttpTransport") -> None:
        self.status = response.status
        self.headers = response.headers
        self._response = response
        self._session = session
        self._owner = owner

    async def read(self, max_bytes: int) -> bytes:
        if max_bytes <= 0:
            return b""
        return await self._response.content.read(max_bytes)

    async def close(self) -> None:
        session, self._session = self._session, None
        if session is None:
            return
        try:
            self._response.release()
        finally:
            await self._owner._close_session(session)


class AiohttpTransport:
    """The default transport: aiohttp, pinned, no proxy, no redirect (R4).

    Nothing is opened until the first ``open``. Each request gets its own
    session, because aiohttp binds a connector to a session and the
    connector carries the request's :class:`_PinnedResolver`: the session
    takes no proxy from the environment (``trust_env=False``), the request
    follows no redirect (``allow_redirects=False``, the module follows them
    itself, re-checked), bodies are decompressed (the byte bound counts what
    the extractor would read), and the session's own total timeout is a
    backstop behind the module's whole-call deadline. ``close`` closes every
    session still open.
    """

    def __init__(self, *, timeout_seconds: float, session_factory: Any = None) -> None:
        self._timeout_seconds = timeout_seconds
        self._session_factory = session_factory
        self._sessions: set[Any] = set()

    async def open(
        self,
        method: str,
        *,
        scheme: str,
        host: str,
        port: int,
        path_and_query: str,
        pinned_addresses: Sequence[Address],
        headers: Mapping[str, str] | None = None,
    ) -> _AiohttpResponse:
        import aiohttp

        if not pinned_addresses:
            raise TransportContractError("no pinned address")
        connector = aiohttp.TCPConnector(
            resolver=_PinnedResolver(host, pinned_addresses), use_dns_cache=False
        )
        factory = self._session_factory or aiohttp.ClientSession
        session = factory(
            connector=connector,
            connector_owner=True,
            trust_env=False,
            auto_decompress=True,
            timeout=aiohttp.ClientTimeout(
                total=self._timeout_seconds + TRANSPORT_TIMEOUT_MARGIN_SECONDS
            ),
        )
        self._sessions.add(session)
        netloc = f"[{host}]" if ":" in host else host
        try:
            response = await session.request(
                method,
                f"{scheme}://{netloc}:{port}{path_and_query}",
                headers=dict(headers or {}),
                allow_redirects=False,
            )
        except BaseException:
            await self._close_session(session)
            raise
        return _AiohttpResponse(response, session, self)

    async def _close_session(self, session: Any) -> None:
        self._sessions.discard(session)
        outcome = session.close()
        if inspect.isawaitable(outcome):
            await outcome

    async def close(self) -> None:
        for session in list(self._sessions):
            try:
                await self._close_session(session)
            except Exception:  # noqa: BLE001 - closing must not mask shutdown
                continue


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
# Outcome classification (R6)
# --------------------------------------------------------------------------- #

#: The provider name ``web.read`` binds under, and the one its traces carry.
READ_BINDING_PROVIDER = MODULE_NAME
#: The provider a read's observation names: the public web over HTTP(S).
PROVIDER_HTTP = "http"

REFUSAL_UNSUPPORTED_CONTENT_TYPE = "unsupported_content_type"
REFUSAL_RESOURCE_BUSY = "resource_busy"
REFUSAL_PROVIDER_REFUSED = "provider_refused"
REFUSAL_RATE_LIMITED = "rate_limited"
REFUSAL_NOT_FOUND = "not_found"
ERROR_UPSTREAM_STATUS = "upstream_status"
ERROR_MALFORMED_RESPONSE = "malformed_response"

#: The trace's ``outcome`` for a success; every other outcome is its code.
OUTCOME_OK = "ok"

#: The media types ``web.read`` reads; any other is refused unread (R3).
READABLE_CONTENT_TYPES = frozenset({"text/html", "application/xhtml+xml", _PLAIN_TEXT})

#: Every code of the module's fixed vocabulary, by the status it ends in.
_STATUS_OF_CODE: Mapping[str, str] = {
    **dict.fromkeys(
        (
            REFUSAL_BLOCKED_ADDRESS,
            REFUSAL_HOST_NOT_ALLOWED,
            REFUSAL_UNSUPPORTED_SCHEME,
            REFUSAL_UNSUPPORTED_URL,
            REFUSAL_TOO_MANY_REDIRECTS,
            REFUSAL_UNSUPPORTED_CONTENT_TYPE,
            REFUSAL_RESOURCE_BUSY,
            REFUSAL_PROVIDER_REFUSED,
            REFUSAL_RATE_LIMITED,
            REFUSAL_NOT_FOUND,
        ),
        FETCH_REFUSED,
    ),
    **dict.fromkeys(
        (REFUSAL_TRANSPORT_FAILED, ERROR_UPSTREAM_STATUS, ERROR_MALFORMED_RESPONSE),
        FETCH_ERROR,
    ),
    ERROR_TIMED_OUT: FETCH_TIMEOUT,
}


def _classify(action: str, outcome: FetchFailure | int | str) -> tuple[str, str | None]:
    """The executor status and module code of *outcome*, the R6 table.

    *outcome* is a :class:`FetchFailure`, the HTTP status of a final answer,
    or a module code. A 2xx status is ``("success", None)``; 401 and 403
    are ``refused provider_refused``, 429 ``refused rate_limited``, 404 and
    410 on a ``web.read`` ``refused not_found``, and every other status
    ``error upstream_status``. A failure or a code keeps the status its code
    ends in — guard refusals and ``resource_busy`` ``refused``, transport
    and malformed answers ``error``, the deadline ``timeout``. A code
    outside the vocabulary is ``error malformed_response``: nothing is ever
    reported as a success it was not.
    """

    if isinstance(outcome, FetchFailure):
        code = outcome.code
    elif isinstance(outcome, int) and not isinstance(outcome, bool):
        if 200 <= outcome < 300:
            return "success", None
        if outcome in (401, 403):
            code = REFUSAL_PROVIDER_REFUSED
        elif outcome == 429:
            code = REFUSAL_RATE_LIMITED
        elif outcome in (404, 410) and action == WEB_READ_ACTION:
            code = REFUSAL_NOT_FOUND
        else:
            code = ERROR_UPSTREAM_STATUS
    else:
        code = outcome
    if code not in _STATUS_OF_CODE:
        code = ERROR_MALFORMED_RESPONSE
    return _STATUS_OF_CODE[code], code


def _failure_message(action: str, status: str, code: str) -> str:
    """The one fixed message of *code*: vocabulary only, never a value (R7)."""

    return f"{action} {status}: {code}"


def _declared_spec(action_name: str) -> ActionSpec:
    """Build *action_name*'s contract from the colocated manifest (R1).

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
        raise WebModuleError(
            f"{MODULE_NAME} prepare: manifest declaration of {action_name!r} is invalid"
        ) from None


class _ReadProvider:
    """The ``web.read`` provider the executor invokes (R3)."""

    __slots__ = ("_module",)

    name = READ_BINDING_PROVIDER

    def __init__(self, module: "WebModule") -> None:
        self._module = module

    async def invoke(self, invocation: Any) -> ActionObservation:
        return await self._module._invoke_read(invocation)


# --------------------------------------------------------------------------- #
# The search backends (R1, R2)
# --------------------------------------------------------------------------- #

#: The provider name ``web.search`` binds under; its traces name the backend.
SEARCH_BINDING_PROVIDER = MODULE_NAME

#: The fixed Brave web-search endpoint: never subject to ``allowed_hosts``.
BRAVE_SEARCH_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"

#: The characters a Wikipedia article URL keeps unencoded in its title.
_WIKI_TITLE_SAFE = "/:()_,'-."

#: The markup excerpts and titles are read as: the provider's answer is JSON,
#: so its strings are already decoded text.
_MARKUP_UTF8 = "text/html; charset=utf-8"


def _product_version() -> str:
    """This product's installed version, ``0`` when it is not installed."""

    try:
        return importlib.metadata.version("twitch-ia-compagnon")
    except importlib.metadata.PackageNotFoundError:
        return "0"


class Malformed:
    """A provider answer that is not the documented shape (R2): no result.

    *dropped* counts the items that were dropped on the way — every one, when
    an answer has items of which none is usable — so the trace still reports
    them; it is ``0`` when the answer's shape itself is wrong.
    """

    __slots__ = ("dropped",)

    def __init__(self, dropped: int = 0) -> None:
        self.dropped = dropped

    def __repr__(self) -> str:
        return f"Malformed(dropped={self.dropped})"


MALFORMED = Malformed()

#: What a backend's ``parse`` answers: the kept results and the dropped count.
ParsedSearch = tuple[list[dict[str, str]], int]


def _plain(text: str) -> str:
    """*text* with its markup removed and its references decoded, on one line."""

    return " ".join(extract_text(text.encode("utf-8"), _MARKUP_UTF8).text.split())


def _is_web_url(value: Any) -> bool:
    """Whether *value* is an absolute ``http(s)`` URL with a host."""

    if not isinstance(value, str) or not value:
        return False
    try:
        parts = urllib.parse.urlsplit(value)
    except ValueError:
        return False
    return parts.scheme.lower() in _SCHEME_PORTS and bool(parts.hostname)


def _encodable(text: str) -> bool:
    """Whether *text* is valid UTF-8 text: no lone surrogate."""

    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _search_result(
    title: Any, url: Any, excerpt: Any, max_excerpt_bytes: int
) -> dict[str, str] | None:
    """One result, or ``None`` when it has no title or no ``http(s)`` URL, or
    either is not valid UTF-8 text (R2).

    The excerpt goes through the extractor — markup removed, references
    decoded — and is cut to *max_excerpt_bytes* on a character boundary; a
    missing excerpt is empty.
    """

    if not isinstance(title, str) or not title.strip() or not _is_web_url(url):
        return None
    if not (_encodable(title) and _encodable(url)):
        return None
    plain = _plain(excerpt) if isinstance(excerpt, str) else ""
    cut, _ = cut_utf8(plain, max_excerpt_bytes)
    return {"title": title, "url": url, "excerpt": cut}


def _parsed_items(
    items: list[Any], result_of: Callable[[Any], dict[str, str] | None]
) -> ParsedSearch | Malformed:
    """The kept results of *items* and the dropped count, or :data:`MALFORMED`.

    The item boundary is where the guard lives (R2): an item *result_of*
    cannot use — ``None``, or any exception, such as a lone surrogate the
    URL encoding or the extractor refuses — is dropped and counted, and its
    siblings are kept. An answer with items of which none is usable is
    :class:`Malformed` carrying that dropped count, never a success with
    nothing; an empty list is an answer with no hits. The deadline the
    walk runs under is looked at before each item, and its expiry is
    :class:`ExtractionAbandoned`, never a dropped item.
    """

    results: list[dict[str, str]] = []
    dropped = 0
    for item in items:
        _check_deadline()
        try:
            result = result_of(item)
        except ExtractionAbandoned:
            raise
        except Exception:  # noqa: BLE001 - the error's text would quote the item
            result = None
        if result is None:
            dropped += 1
        else:
            results.append(result)
    if items and not results:
        return Malformed(dropped)
    return results, dropped


def _checked_scan(scan_once: Callable[..., Any]) -> Callable[..., Any]:
    """*scan_once*, the deadline looked at before each value it reads."""

    def checked(string: str, index: int) -> Any:
        _check_deadline()
        return scan_once(string, index)

    return checked


def _stepped_array(s_and_end: tuple[str, int], scan_once: Callable[..., Any]) -> Any:
    """``JSONArray`` with the deadline looked at before each element — a
    number, a literal and a string value alike."""

    _check_deadline()
    return json.decoder.JSONArray(s_and_end, _checked_scan(scan_once))


def _stepped_object(
    s_and_end: tuple[str, int], strict: bool, scan_once: Callable[..., Any], *hooks: Any
) -> Any:
    """``JSONObject`` with the deadline looked at before each member's value."""

    _check_deadline()
    return json.decoder.JSONObject(s_and_end, strict, _checked_scan(scan_once), *hooks)


def _stepped_string(string: str, end: int, strict: bool = True) -> tuple[str, int]:
    """``py_scanstring`` with the deadline looked at before each chunk: the
    run up to the next escape, and the escape it ends on.

    The standard Python step reads every escape of one string in a loop no
    deadline sits in; a multi-megabyte string of escapes held its thread
    for as long, past the call's end.
    """

    chunks: list[str] = []
    begin = end - 1
    while True:
        _check_deadline()
        chunk = json.decoder.STRINGCHUNK.match(string, end)
        if chunk is None:
            raise json.JSONDecodeError("Unterminated string starting at", string, begin)
        end = chunk.end()
        content, terminator = chunk.groups()
        if content:
            chunks.append(content)
        if terminator == '"':
            return "".join(chunks), end
        if terminator != "\\":
            if strict:
                raise json.JSONDecodeError("Invalid control character", string, end - 1)
            chunks.append(terminator)
            continue
        escape = string[end : end + 1]
        if escape != "u":
            try:
                chunks.append(json.decoder.BACKSLASH[escape])
            except KeyError:
                raise json.JSONDecodeError("Invalid \\escape", string, end) from None
            end += 1
            continue
        code = json.decoder._decode_uXXXX(string, end)
        end += 5
        if 0xD800 <= code <= 0xDBFF and string[end : end + 2] == "\\u":
            low = json.decoder._decode_uXXXX(string, end + 1)
            if 0xDC00 <= low <= 0xDFFF:
                code = 0x10000 + (((code - 0xD800) << 10) | (low - 0xDC00))
                end += 6
        chunks.append(chr(code))


class _SteppedDecoder(json.JSONDecoder):
    """``json`` decoding in Python steps: the deadline is looked at before
    each value — of an array, of an object, at the top — and before each
    chunk of a string.

    The C scanner reads a whole document in one call that holds the
    interpreter — half a second for some 7 MB answers — so the event loop
    could not end the call on time while it ran. Looking only as an object,
    array or string begins was not enough either: an array of a million
    numbers, or a string of a million escapes, is one long loop after that
    look, and a walk the deadline had abandoned ran on through it.
    """

    def __init__(self) -> None:
        super().__init__()
        self.parse_object = _stepped_object
        self.parse_array = _stepped_array
        self.parse_string = _stepped_string
        self.scan_once = _checked_scan(json.scanner.py_make_scanner(self))


def _json_answer(body: bytes) -> Any:
    """*body* as JSON, or :data:`MALFORMED`; the decoder's text is never read.

    Decoded in steps (:class:`_SteppedDecoder`), so a deadline the walk runs
    under ends it with :class:`ExtractionAbandoned`.
    """

    try:
        return _SteppedDecoder().decode(body.decode(json.detect_encoding(body), "surrogatepass"))
    except ExtractionAbandoned:
        raise
    except Exception:  # noqa: BLE001 - the decoder's text would quote the body
        return MALFORMED


class WikipediaBackend:
    """MediaWiki ``list=search`` on the configured wiki; no credential (R1, R2).

    The result URL is the article on the same wiki, built from the title with
    spaces as ``_`` and percent-encoded as Wikipedia serves it.
    """

    __slots__ = ("_language", "_user_agent", "_max_excerpt_bytes")

    name = PROVIDER_WIKIPEDIA

    def __init__(self, settings: _Settings) -> None:
        self._language = settings.wikipedia_language
        agent = f"twitch-ia-compagnon/{_product_version()} (web module)"
        if settings.user_agent_contact:
            agent = f"{agent}; {settings.user_agent_contact}"
        self._user_agent = agent
        self._max_excerpt_bytes = settings.max_excerpt_bytes

    def __repr__(self) -> str:
        return f"WikipediaBackend(language={self._language!r})"

    def _wiki(self) -> str:
        return f"https://{self._language}.wikipedia.org"

    def article_url(self, title: str) -> str:
        return f"{self._wiki()}/wiki/" + urllib.parse.quote(
            title.replace(" ", "_"), safe=_WIKI_TITLE_SAFE
        )

    def build_request(self, query: str, limit: int) -> tuple[str, dict[str, str]]:
        parameters = urllib.parse.urlencode(
            {
                "action": "query",
                "list": "search",
                "srsearch": query,
                "srlimit": limit,
                "srprop": "snippet",
                "format": "json",
                "formatversion": 2,
                "utf8": 1,
            }
        )
        return f"{self._wiki()}/w/api.php?{parameters}", {"User-Agent": self._user_agent}

    def parse(self, body: bytes) -> ParsedSearch | Malformed:
        answer = _json_answer(body)
        query = answer.get("query") if isinstance(answer, dict) else None
        items = query.get("search") if isinstance(query, dict) else None
        if not isinstance(items, list):
            return MALFORMED
        return _parsed_items(items, self._result)

    def _result(self, item: Any) -> dict[str, str] | None:
        title = item.get("title") if isinstance(item, dict) else None
        if not isinstance(title, str) or not title.strip():
            return None
        return _search_result(
            title, self.article_url(title), item.get("snippet"), self._max_excerpt_bytes
        )


class BraveBackend:
    """Brave Web Search with the configured key (R1, R2).

    The key is resolved once and only ever reaches the request header: the
    repr omits it, and nothing else this backend returns holds it.
    """

    __slots__ = ("_key", "_max_excerpt_bytes")

    name = PROVIDER_BRAVE

    def __init__(self, settings: _Settings) -> None:
        self._key = settings.brave_api_key or ""
        self._max_excerpt_bytes = settings.max_excerpt_bytes

    def __repr__(self) -> str:
        return "BraveBackend()"

    def build_request(self, query: str, limit: int) -> tuple[str, dict[str, str]]:
        parameters = urllib.parse.urlencode({"q": query, "count": limit})
        return f"{BRAVE_SEARCH_ENDPOINT}?{parameters}", {
            "Accept": "application/json",
            "X-Subscription-Token": self._key,
        }

    def parse(self, body: bytes) -> ParsedSearch | Malformed:
        answer = _json_answer(body)
        web = answer.get("web") if isinstance(answer, dict) else None
        items = web.get("results") if isinstance(web, dict) else None
        if not isinstance(items, list):
            return MALFORMED
        return _parsed_items(items, self._result)

    def _result(self, item: Any) -> dict[str, str] | None:
        if not isinstance(item, dict):
            return None
        title = item.get("title")
        return _search_result(
            _plain(title) if isinstance(title, str) else None,
            item.get("url"),
            item.get("description"),
            self._max_excerpt_bytes,
        )


#: The backend class of each ``provider`` value: a third provider adds a
#: class and an enum value here, not a contract field (R1).
SEARCH_BACKENDS: Mapping[str, Callable[[_Settings], Any]] = {
    PROVIDER_WIKIPEDIA: WikipediaBackend,
    PROVIDER_BRAVE: BraveBackend,
}


class _SearchProvider:
    """The ``web.search`` provider the executor invokes (R1, R2)."""

    __slots__ = ("_module",)

    name = SEARCH_BINDING_PROVIDER

    def __init__(self, module: "WebModule") -> None:
        self._module = module

    async def invoke(self, invocation: Any) -> ActionObservation:
        return await self._module._invoke_search(invocation)


# --------------------------------------------------------------------------- #
# The shared bounded TTL cache (R5)
# --------------------------------------------------------------------------- #


def _json_value(value: Any) -> Any:
    """*value* as plain JSON types: a mapping a ``dict``, a tuple a ``list``.

    The frozen observation holds ``MappingProxyType`` and tuples; anything
    else is left as is, so a non-JSON value fails ``json.dumps`` — a bug to
    surface, never a value to stringify.
    """

    if isinstance(value, Mapping):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def _compact_json(value: Any) -> str:
    """The R5 serialisation: compact, keys sorted, non-ASCII kept as is."""

    return json.dumps(
        _json_value(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def _observation_record(observation: ActionObservation) -> dict[str, Any]:
    """The complete observation as the cache accounts it: all five fields."""

    return {
        "status": observation.status,
        "result": observation.result,
        "error": observation.error,
        "parts": list(observation.parts),
        "provenance": observation.provenance,
    }


def cache_key(action: str, provider: str, arguments: Mapping[str, Any]) -> str:
    """The in-memory key of one call: action, provider and canonical arguments.

    It lives only inside :class:`_ObservationCache` and is never persisted,
    logged or emitted (R5, R7).
    """

    return _compact_json({"action": action, "provider": provider, "arguments": arguments})


class CacheStats(NamedTuple):
    """What the cache may say about itself: counts only."""

    entries: int
    stored_bytes: int


@dataclass(slots=True)
class _CacheEntry:
    observation: ActionObservation
    stored_at: float
    size: int

    def __repr__(self) -> str:
        return f"_CacheEntry(size={self.size})"


class _ObservationCache:
    """One in-process cache of successful observations, bounded three ways (R5).

    An entry is the complete observation a provider returned; its size — the
    UTF-8 bytes of its key plus those of :func:`_observation_record` as
    compact sorted-key JSON — is computed once at :meth:`put` and kept next
    to it — or, when the caller measured the record already (either action
    does so on its worker thread, under its deadline), taken from the caller, so
    the event loop never serialises a page. :meth:`get` never serves an entry older than the TTL on the
    runtime clock (nor one stamped in the future), and :meth:`put` evicts the
    least recently used entries until both ``max_entries`` and ``max_bytes``
    hold; an entry larger than ``max_bytes`` alone is not stored. A TTL of 0
    disables the cache. Keys and entries never leave the object: its repr
    and :meth:`stats` are counts.
    """

    __slots__ = ("_clock", "_ttl", "_max_entries", "_max_bytes", "_entries", "_stored_bytes")

    def __init__(
        self, clock: Callable[[], float], ttl: float, max_entries: int, max_bytes: int
    ) -> None:
        self._clock = clock
        self._ttl = ttl
        self._max_entries = max_entries
        self._max_bytes = max_bytes
        #: Least recently used first.
        self._entries: dict[str, _CacheEntry] = {}
        self._stored_bytes = 0

    def __repr__(self) -> str:
        return f"_ObservationCache(entries={len(self._entries)}, stored_bytes={self._stored_bytes})"

    @property
    def enabled(self) -> bool:
        return self._ttl > 0

    @staticmethod
    def entry_size(key: str, observation: ActionObservation) -> int:
        return len(key.encode("utf-8")) + _ObservationCache.record_size(observation)

    @staticmethod
    def record_size(observation: ActionObservation) -> int:
        """The UTF-8 bytes of *observation*'s record, its key not counted."""

        return len(_compact_json(_observation_record(observation)).encode("utf-8"))

    def get(self, key: str) -> ActionObservation | None:
        """The fresh stored observation under *key*, now the most recently used."""

        if not self.enabled:
            return None
        entry = self._entries.get(key)
        if entry is None:
            return None
        age = self._clock() - entry.stored_at
        if not 0 <= age <= self._ttl:
            self._drop(key)
            return None
        self._entries[key] = self._entries.pop(key)
        return entry.observation

    def put(
        self, key: str, observation: ActionObservation, record_bytes: int | None = None
    ) -> None:
        """Store a success under *key*, then evict until every bound holds.

        *record_bytes* is :meth:`record_size` of *observation* when the
        caller measured it already; ``None`` measures it here.
        """

        if not self.enabled or observation.status != "success":
            return
        if record_bytes is None:
            size = self.entry_size(key, observation)
        else:
            size = len(key.encode("utf-8")) + record_bytes
        if key in self._entries:
            self._drop(key)
        if size > self._max_bytes:
            return
        self._entries[key] = _CacheEntry(observation, self._clock(), size)
        self._stored_bytes += size
        while len(self._entries) > self._max_entries or self._stored_bytes > self._max_bytes:
            self._drop(next(iter(self._entries)))

    def discard(self, key: str) -> None:
        """Forget the entry under *key*, if there is one."""

        if key in self._entries:
            self._drop(key)

    def stats(self) -> CacheStats:
        return CacheStats(len(self._entries), self._stored_bytes)

    def _snapshot(self) -> list[tuple[str, ActionObservation]]:
        """The raw entries, least recently used first: for tests only."""

        return [(key, entry.observation) for key, entry in self._entries.items()]

    def _drop(self, key: str) -> None:
        self._stored_bytes -= self._entries.pop(key).size


def _served_from_cache(observation: ActionObservation) -> ActionObservation:
    """A new observation equal to the stored one but flagged ``cache_hit``.

    The stored entry is left untouched, and so is its accounted size.
    """

    provenance = dict(observation.provenance)
    provenance["trace_fields"] = {**provenance.get("trace_fields", {}), "cache_hit": True}
    return dataclasses.replace(observation, provenance=provenance)


# --------------------------------------------------------------------------- #
# The handle
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class _Lengths:
    """The byte counts a call measures beyond its trace.

    ``response_bytes`` is for the diagnostic (R7); ``record_bytes`` is the
    cache's size of the call's success, when the call measured it under its
    deadline.
    """

    response_bytes: int = 0
    record_bytes: int | None = None


@dataclass(slots=True)
class _Progress:
    """How far one fetch got, readable after its task is cancelled."""

    redirects: int = 0


class _Deadline:
    """One call's whole deadline: ``request_timeout_seconds`` from its start.

    :attr:`future` is the sleep the fetch races. :meth:`expired` also reads
    the clock that sleep runs on — the event loop's for the default sleeper,
    the runtime clock for an injected one — so work that held the loop past
    the deadline is seen late at once, before the sleep's own wake-up runs.
    :meth:`run` runs a function on a worker thread under it.
    """

    __slots__ = ("future", "_now", "_expires_at")

    def __init__(self, sleeper: Any, clock: Callable[[], float], seconds: float) -> None:
        if sleeper is None:
            sleeper = asyncio.sleep
            self._now: Callable[[], float] = asyncio.get_running_loop().time
        else:
            self._now = clock
        self._expires_at = self._now() + seconds
        self.future: "asyncio.Future[Any]" = asyncio.ensure_future(sleeper(seconds))

    def __repr__(self) -> str:
        return f"_Deadline(expired={self.expired()})"

    def passed(self) -> bool:
        """Whether the clock reads the deadline: safe from any thread."""

        return self._now() >= self._expires_at

    def expired(self) -> bool:
        return self.future.done() or self.passed()

    async def run(self, function: Callable[..., Any], *arguments: Any) -> tuple[bool, Any]:
        """``(True, function(*arguments, expired))`` on a worker thread, or
        ``(False, None)`` once the deadline expired first.

        *expired* is what the thread's walk checks: it turns true when the
        deadline passes or the caller is cancelled. An answer the thread
        returns after the deadline is discarded, never a late success.

        The thread is a new one, not a pool's: starting it hands it the
        interpreter at once, where a pooled thread woken from its queue can
        wait behind a busy event loop for as long as the loop keeps it.
        """

        stop = threading.Event()

        def expired() -> bool:
            return stop.is_set() or self.passed()

        loop = asyncio.get_running_loop()
        work: "asyncio.Future[Any]" = loop.create_future()
        context = contextvars.copy_context()

        def settle(outcome: Any, error: BaseException | None) -> None:
            if work.done():
                return
            if error is None:
                work.set_result(outcome)
            else:
                work.set_exception(error)

        def walk() -> None:
            outcome, error = None, None
            try:
                outcome = context.run(function, *arguments, expired)
            except BaseException as caught:  # noqa: BLE001 - handed to the caller
                error = caught
            try:
                loop.call_soon_threadsafe(settle, outcome, error)
            except RuntimeError:  # the loop closed: nobody waits for the answer
                return

        threading.Thread(target=walk, name="web-deadline-walk", daemon=True).start()
        try:
            await asyncio.wait({work, self.future}, return_when=asyncio.FIRST_COMPLETED)
        except asyncio.CancelledError:
            stop.set()
            work.cancel()
            raise
        if self.expired():
            stop.set()
            work.cancel()
            return False, None
        return True, work.result()

    async def cancel(self) -> None:
        self.future.cancel()
        await asyncio.gather(self.future, return_exceptions=True)


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
        #: Calls of either action running now, against ``max_concurrent_requests``.
        self._in_flight = 0
        self._read_provider = _ReadProvider(self)
        self._search_provider = _SearchProvider(self)
        #: The configured search backend: built here, it opens nothing.
        self._backend = SEARCH_BACKENDS[settings.provider](settings)
        #: The one cache both actions share, in this process's memory only (R5).
        self._cache = _ObservationCache(
            context.clock,
            settings.cache_ttl_seconds,
            settings.cache_max_entries,
            settings.cache_max_bytes,
        )
        self._prepared = False
        self._closed = False

    @property
    def settings(self) -> _Settings:
        return self._settings

    # -- lifecycle hooks ---------------------------------------------------- #

    async def prepare(self) -> None:
        """Bind both providers and mark ready, opening nothing (R1, AC4).

        Each spec comes from the colocated manifest. No name is resolved and
        no connection made.
        """

        if self._prepared or self._closed:
            return
        try:
            for action_name, provider, provider_name in (
                (WEB_SEARCH_ACTION, self._search_provider, SEARCH_BINDING_PROVIDER),
                (WEB_READ_ACTION, self._read_provider, READ_BINDING_PROVIDER),
            ):
                spec = _declared_spec(action_name)
                self._actions.register(spec, provider, provider_name=provider_name)
        except WebModuleError:
            raise
        except Exception:
            raise WebModuleError(f"{MODULE_NAME} prepare: action binding failed") from None
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

    # -- web.read (R3, R5 concurrency, R6, R7, R8) -------------------------- #

    async def _invoke_read(self, invocation: Any) -> ActionObservation:
        """Serve one ``web.read`` call for the executor.

        A read is a request for public data and changes nothing outside, so
        nothing is emitted and an interruption stays a certain ``timeout``.
        The slot taken against ``max_concurrent_requests`` is released on
        every exit, a cancellation included.
        """

        invocation.mark_not_emitted()
        url = invocation.call.arguments.get("url")
        trace: dict[str, Any] = {
            "url_bytes": len(url.encode("utf-8", errors="replace")) if isinstance(url, str) else 0,
            "text_bytes": 0,
            "redirects": 0,
            "cache_hit": False,
            "outcome": OUTCOME_OK,
        }
        lengths = _Lengths()
        if self._in_flight >= self._settings.max_concurrent_requests:
            observation = _read_failure(trace, REFUSAL_RESOURCE_BUSY)
        else:
            self._in_flight += 1
            try:
                observation = await self._cached(
                    cache_key(WEB_READ_ACTION, PROVIDER_HTTP, invocation.call.arguments),
                    lambda deadline: self._read(url, trace, lengths, deadline),
                    lengths,
                    lambda: _read_timed_out(trace),
                )
            finally:
                self._in_flight -= 1
        self._diagnose(
            WEB_READ_ACTION,
            observation,
            url_bytes=trace["url_bytes"],
            response_bytes=lengths.response_bytes,
            text_bytes=trace["text_bytes"],
        )
        return observation

    async def _read(
        self, url: Any, trace: dict[str, Any], lengths: _Lengths, deadline: _Deadline
    ) -> ActionObservation:
        """Fetch, judge the content type unread, extract, bound (R3, R5).

        *deadline* bounds the whole call: the fetch races it, and so does
        the work after it — the extraction, the result and the serialisation
        the cache accounts it by — which runs on a worker thread, so the
        event loop keeps the deadline while a large page is parsed and
        serialised. Once it expires the thread's answer is discarded and its
        parser, which checks the same deadline as it walks, stops. A call
        that ran out of time is ``timeout timed_out``, never a success with
        less text.
        """

        settings = self._settings
        fetched = await self.fetch(
            url, apply_allowlist=True, accept_body=_readable_answer, deadline=deadline.future
        )
        trace["redirects"] = fetched.redirects
        if isinstance(fetched, FetchFailure):
            return _read_failure(trace, fetched)
        lengths.response_bytes = len(fetched.body or b"")
        if _classify(WEB_READ_ACTION, fetched.status)[0] != "success":
            return _read_failure(trace, fetched.status)
        if fetched.body is None:
            return _read_failure(trace, REFUSAL_UNSUPPORTED_CONTENT_TYPE)
        finished, answered = await deadline.run(
            _read_success,
            fetched,
            _header(fetched.headers, "Content-Type"),
            settings.max_text_bytes,
            dict(trace),
            advisory_marker(),
        )
        if not finished:
            return _read_timed_out(trace)
        if answered is None:  # the parser's text would quote the page
            return _read_failure(trace, ERROR_MALFORMED_RESPONSE)
        observation, lengths.record_bytes = answered
        trace["text_bytes"] = observation.provenance["trace_fields"]["text_bytes"]
        return observation

    # -- web.search (R1, R2, R4 provider endpoints, R6, R7) ------------------ #

    async def _invoke_search(self, invocation: Any) -> ActionObservation:
        """Serve one ``web.search`` call for the executor.

        Like a read, a search changes nothing outside: nothing is emitted, and
        the slot taken against ``max_concurrent_requests`` — shared with
        reads — is released on every exit.
        """

        invocation.mark_not_emitted()
        query = invocation.call.arguments.get("query")
        trace: dict[str, Any] = {
            "query_bytes": (
                len(query.encode("utf-8", errors="replace")) if isinstance(query, str) else 0
            ),
            "result_count": 0,
            "dropped_count": 0,
            "redirects": 0,
            "cache_hit": False,
            "outcome": OUTCOME_OK,
        }
        backend = self._backend
        lengths = _Lengths()
        if self._in_flight >= self._settings.max_concurrent_requests:
            observation = _search_failure(backend.name, trace, REFUSAL_RESOURCE_BUSY)
        else:
            self._in_flight += 1
            try:
                observation = await self._cached(
                    cache_key(WEB_SEARCH_ACTION, backend.name, invocation.call.arguments),
                    lambda deadline: self._search(backend, query, trace, lengths, deadline),
                    lengths,
                    lambda: _search_timed_out(backend.name, trace),
                )
            finally:
                self._in_flight -= 1
        self._diagnose(
            WEB_SEARCH_ACTION,
            observation,
            query_bytes=trace["query_bytes"],
            response_bytes=lengths.response_bytes,
        )
        return observation

    def _diagnose(self, action: str, observation: ActionObservation, **lengths: int) -> None:
        """Log one value-free line for a call that did not succeed (R7).

        The line is the action, the status, the code and the provider — all
        fixed vocabulary — and, only with ``debug_lengths``, the byte
        lengths named by *lengths*, each an integer. A success logs nothing.
        """

        if observation.status == "success":
            return
        error = observation.error or {}
        line = _failure_message(action, observation.status, error.get("code"))
        line = f"{line} provider={observation.provenance.get('provider')}"
        if self._settings.debug_lengths:
            line += "".join(f" {name}={int(value)}" for name, value in lengths.items())
        _LOG.warning(line)

    async def _cached(
        self,
        key: str,
        call: Callable[[_Deadline], Any],
        lengths: _Lengths,
        timed_out: Callable[[], ActionObservation],
    ) -> ActionObservation:
        """Serve *key* from the cache, or run *call* and offer its answer.

        Looked up after admission, so a hit still took a slot, which its
        caller releases at once; a hit resolves and connects nothing (R5).
        A miss starts the call's one deadline of ``request_timeout_seconds``
        and hands it to *call*. The store is under that deadline too: an
        answer that is only ready once it expired — or whose store outlived
        it — leaves no entry and is *timed_out*'s observation, never a late
        success. An answer whose record *call* measured
        (``lengths.record_bytes``) is stored without being serialised again.
        """

        stored = self._cache.get(key)
        if stored is not None:
            return _served_from_cache(stored)
        deadline = _Deadline(self._sleeper, self._clock, self._settings.request_timeout_seconds)
        try:
            observation = await call(deadline)
            if observation.status != "success":
                return observation
            if not deadline.expired():
                self._cache.put(key, observation, lengths.record_bytes)
                if not deadline.expired():
                    return observation
                self._cache.discard(key)
            return timed_out()
        finally:
            await deadline.cancel()

    async def _search(
        self,
        backend: Any,
        query: Any,
        trace: dict[str, Any],
        lengths: _Lengths,
        deadline: _Deadline,
    ) -> ActionObservation:
        """Ask *backend* through the guard, never the allowlist; parse, bound (R2).

        The provider endpoint goes through the same scheme and address guard
        and pinning as a read, but ``allowed_hosts`` does not apply to it
        (R4). Only a 2xx body is read, bounded by ``max_response_bytes``; one
        that was cut, or that is not the documented JSON shape, is
        ``error malformed_response``. As for a read, *deadline* bounds the
        whole call: the JSON, every result's extraction, the dropped count
        and the record the cache accounts the success by are built on a
        worker thread that checks it as it walks, and a search that ran out
        of time is ``timeout timed_out``.
        """

        settings = self._settings
        url, headers = backend.build_request(query, settings.max_results)
        fetched = await self.fetch(
            url,
            apply_allowlist=False,
            accept_body=_successful_answer,
            headers=headers,
            deadline=deadline.future,
        )
        trace["redirects"] = fetched.redirects
        if isinstance(fetched, FetchFailure):
            return _search_failure(backend.name, trace, fetched)
        lengths.response_bytes = len(fetched.body or b"")
        if _classify(WEB_SEARCH_ACTION, fetched.status)[0] != "success":
            return _search_failure(backend.name, trace, fetched.status)
        if fetched.body is None or fetched.truncated:
            return _search_failure(backend.name, trace, ERROR_MALFORMED_RESPONSE)
        finished, answered = await deadline.run(
            _search_success,
            backend,
            fetched.body,
            settings.max_results,
            dict(trace),
            advisory_marker(),
        )
        if not finished:
            return _search_timed_out(backend.name, trace)
        if isinstance(answered, Malformed):
            trace["dropped_count"] = answered.dropped
            return _search_failure(backend.name, trace, ERROR_MALFORMED_RESPONSE)
        observation, lengths.record_bytes = answered
        fields = observation.provenance["trace_fields"]
        trace["result_count"] = fields["result_count"]
        trace["dropped_count"] = fields["dropped_count"]
        return observation

    # -- the guarded fetch (R4, R6) ----------------------------------------- #

    def _transport(self) -> Any:
        """The transport, bound on first use: the seam or the default one."""

        if self._opened_transport is None:
            self._opened_transport = (
                self._transport_seam
                if self._transport_seam is not None
                else AiohttpTransport(timeout_seconds=self._settings.request_timeout_seconds)
            )
        return self._opened_transport

    async def fetch(
        self,
        url: str,
        *,
        apply_allowlist: bool,
        accept_body: Callable[[Any], bool] | None = None,
        headers: Mapping[str, str] | None = None,
        deadline: "asyncio.Future[Any] | None" = None,
    ) -> Fetched | FetchFailure:
        """Fetch *url* through the guard, every hop, within one deadline (R4, R6).

        The whole call — resolution, every hop and the body — runs as one task
        raced against ``request_timeout_seconds`` on the injected sleeper; when
        that elapses first the task is cancelled, the open response closed,
        and the call is ``timeout timed_out``. *accept_body* sees the final
        response before any body byte is read and may decline it; by default
        the body is read, bounded. A cancellation of the caller cancels the
        work and is re-raised. A caller that bounds more than the fetch
        passes its own *deadline*, which the fetch races and leaves running.
        """

        sleeper = self._sleeper or asyncio.sleep
        progress = _Progress()
        work = asyncio.ensure_future(
            self._fetch_hops(
                url,
                apply_allowlist=apply_allowlist,
                accept_body=accept_body,
                headers=headers,
                progress=progress,
            )
        )
        timer = (
            asyncio.ensure_future(sleeper(self._settings.request_timeout_seconds))
            if deadline is None
            else deadline
        )
        owned = [timer] if deadline is None else []
        try:
            await asyncio.wait({work, timer}, return_when=asyncio.FIRST_COMPLETED)
        except asyncio.CancelledError:
            work.cancel()
            for pending in owned:
                pending.cancel()
            await asyncio.gather(work, *owned, return_exceptions=True)
            raise
        if not work.done():
            work.cancel()
            await asyncio.gather(work, return_exceptions=True)
            return FetchFailure(FETCH_TIMEOUT, ERROR_TIMED_OUT, progress.redirects)
        for pending in owned:
            pending.cancel()
        await asyncio.gather(*owned, return_exceptions=True)
        if work.cancelled() or work.exception() is not None:
            return FetchFailure(FETCH_ERROR, REFUSAL_TRANSPORT_FAILED, progress.redirects)
        return work.result()

    async def _fetch_hops(
        self,
        url: str,
        *,
        apply_allowlist: bool,
        accept_body: Callable[[Any], bool] | None,
        headers: Mapping[str, str] | None,
        progress: _Progress,
    ) -> Fetched | FetchFailure:
        """Every hop of one fetch: guard, resolve once, connect pinned, follow.

        The redirects followed so far are kept on *progress*, so a call cut
        short by the deadline still reports them. Credential headers go only
        to the origin the call began at (:data:`CREDENTIAL_HEADERS`).
        """

        settings = self._settings
        redirects = 0
        hop_headers = dict(headers or {})
        origin: tuple[str, str, int] | None = None
        while True:
            target = parse_target(url)
            if isinstance(target, GuardRefusal):
                return FetchFailure(FETCH_REFUSED, target.code, redirects)
            if origin is None:
                origin = (target.scheme, target.host, target.port)
            elif (target.scheme, target.host, target.port) != origin:
                hop_headers = {
                    name: value
                    for name, value in hop_headers.items()
                    if name.lower() not in CREDENTIAL_HEADERS
                }
            if apply_allowlist and not host_allowed(target.host, settings.allowed_hosts):
                return FetchFailure(FETCH_REFUSED, REFUSAL_HOST_NOT_ALLOWED, redirects)
            pinned = await resolve_checked(target, self._resolver)
            if isinstance(pinned, GuardRefusal):
                status = FETCH_ERROR if pinned.code == REFUSAL_TRANSPORT_FAILED else FETCH_REFUSED
                return FetchFailure(status, pinned.code, redirects)
            response = None
            try:
                response = await self._transport().open(
                    "GET",
                    scheme=target.scheme,
                    host=target.host,
                    port=target.port,
                    path_and_query=target.path,
                    pinned_addresses=tuple(pinned),
                    headers=dict(hop_headers),
                )
                location = _header(response.headers, "Location")
                if response.status in REDIRECT_STATUSES and location:
                    if redirects >= settings.max_redirects:
                        return FetchFailure(FETCH_REFUSED, REFUSAL_TOO_MANY_REDIRECTS, redirects)
                    redirects += 1
                    progress.redirects = redirects
                    try:
                        url = urllib.parse.urljoin(url, location)
                    except ValueError:
                        return FetchFailure(FETCH_REFUSED, REFUSAL_UNSUPPORTED_URL, redirects)
                    continue
                if accept_body is not None and not accept_body(response):
                    return Fetched(response.status, response.headers, url, redirects)
                body, truncated = await read_bounded(response, settings.max_response_bytes)
                return Fetched(response.status, response.headers, url, redirects, body, truncated)
            except Exception:  # noqa: BLE001 - transport text names the host
                return FetchFailure(FETCH_ERROR, REFUSAL_TRANSPORT_FAILED, redirects)
            finally:
                if response is not None:
                    await _close_response(response)


def _read_success(
    fetched: Fetched,
    content_type: str | None,
    max_text_bytes: int,
    trace: dict[str, Any],
    marker: str,
    expired: Callable[[], bool],
) -> tuple[ActionObservation, int] | None:
    """A read's success observation and its cache record size, in bytes.

    The text is cut to *max_text_bytes*. *trace* is the thread's own copy:
    the caller's is never touched here; *marker* is :func:`advisory_marker`,
    read by the caller. Run on a worker thread under the
    call's deadline: *expired* is what the parser checks as it walks, and
    the record is measured here, not on the event loop. ``None`` is a body
    the extractor could not parse, or one it gave up on because the
    deadline expired — the caller already knows which.
    """

    _EXTRACTION_EXPIRED.set(expired)
    try:
        extracted = extract_text(fetched.body or b"", content_type)
        text, text_cut = cut_utf8(extracted.text, max_text_bytes)
    except Exception:  # noqa: BLE001 - the parser's text would quote the page
        return None
    trace["text_bytes"] = len(text.encode("utf-8"))
    result: dict[str, Any] = {
        "advisory": marker,
        "final_url": fetched.final_url,
        "text": text,
        "truncated": fetched.truncated or text_cut,
    }
    if extracted.title is not None:
        result["title"] = extracted.title
    observation = ActionObservation(
        status="success",
        provenance=_read_provenance(trace),
        result=result,
        parts=(),
    )
    return observation, _ObservationCache.record_size(observation)


def _search_success(
    backend: Any,
    body: bytes,
    max_results: int,
    trace: dict[str, Any],
    marker: str,
    expired: Callable[[], bool],
) -> tuple[ActionObservation, int] | Malformed:
    """A search's success observation and its cache record size, in bytes.

    At most *max_results* results. *trace* is the thread's own copy, its
    counts filled here; *marker* is :func:`advisory_marker`, read by the
    caller. Run on a worker thread under the call's deadline:
    *expired* is what the JSON's walk and every result's extraction check.
    :class:`Malformed` is an answer that is not the documented shape, or
    one whose walk the deadline ended — the caller already knows which.
    """

    _EXTRACTION_EXPIRED.set(expired)
    try:
        parsed = backend.parse(body)
    except Exception:  # noqa: BLE001 - the parser's text would quote the answer
        return MALFORMED
    if isinstance(parsed, Malformed):
        return parsed
    results, dropped = parsed
    results = results[:max_results]
    trace["result_count"] = len(results)
    trace["dropped_count"] = dropped
    observation = ActionObservation(
        status="success",
        provenance=_search_provenance(backend.name, trace),
        result={
            "advisory": marker,
            "provider": backend.name,
            "results": results,
        },
        parts=(),
    )
    return observation, _ObservationCache.record_size(observation)


def _readable_answer(response: Any) -> bool:
    """Whether a final answer's body is read: a 2xx of a readable media type.

    Judged on the status and the headers alone, before any body byte: a
    refused type or a status that is not a page is closed unread (R3, AC8).
    """

    status = getattr(response, "status", None)
    if not isinstance(status, int) or not 200 <= status < 300:
        return False
    header = _header(getattr(response, "headers", None), "Content-Type")
    media_type, _charset = _content_type(header)
    return media_type in READABLE_CONTENT_TYPES


def _successful_answer(response: Any) -> bool:
    """Whether a provider's final answer body is read: a 2xx only (R2, R6)."""

    status = getattr(response, "status", None)
    return isinstance(status, int) and 200 <= status < 300


def _search_provenance(provider: str, trace: Mapping[str, Any]) -> dict[str, Any]:
    """A search's provenance: the backend's name and its count-only trace fields."""

    return {"provider": provider, "trace_fields": dict(trace)}


def _search_failure(
    provider: str, trace: dict[str, Any], outcome: FetchFailure | int | str
) -> ActionObservation:
    """The terminal observation of a search that did not succeed (R6, R7)."""

    status, code = _classify(WEB_SEARCH_ACTION, outcome)
    if code is None:  # a success status is never a failure
        status, code = FETCH_ERROR, ERROR_MALFORMED_RESPONSE
    trace["outcome"] = code
    return ActionObservation(
        status=status,
        provenance=_search_provenance(provider, trace),
        error={
            "code": code,
            "message": _failure_message(WEB_SEARCH_ACTION, status, code),
            "retryable": False,
        },
    )


def _search_timed_out(provider: str, trace: dict[str, Any]) -> ActionObservation:
    """A search the deadline ended after its fetch: counted nothing it kept."""

    trace["result_count"] = 0
    trace["dropped_count"] = 0
    return _search_failure(
        provider, trace, FetchFailure(FETCH_TIMEOUT, ERROR_TIMED_OUT, trace["redirects"])
    )


def _read_provenance(trace: Mapping[str, Any]) -> dict[str, Any]:
    """A read's provenance: the ``http`` provider and its count-only trace fields."""

    return {"provider": PROVIDER_HTTP, "trace_fields": dict(trace)}


def _read_failure(trace: dict[str, Any], outcome: FetchFailure | int | str) -> ActionObservation:
    """The terminal observation of a read that did not succeed (R6, R7)."""

    status, code = _classify(WEB_READ_ACTION, outcome)
    if code is None:  # a success status is never a failure
        status, code = FETCH_ERROR, ERROR_MALFORMED_RESPONSE
    trace["outcome"] = code
    return ActionObservation(
        status=status,
        provenance=_read_provenance(trace),
        error={
            "code": code,
            "message": _failure_message(WEB_READ_ACTION, status, code),
            "retryable": False,
        },
    )


def _read_timed_out(trace: dict[str, Any]) -> ActionObservation:
    """A read the deadline ended after its fetch: it kept no text."""

    trace["text_bytes"] = 0
    return _read_failure(
        trace, FetchFailure(FETCH_TIMEOUT, ERROR_TIMED_OUT, trace["redirects"])
    )


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
    "Address",
    "AiohttpTransport",
    "BRAVE_SEARCH_ENDPOINT",
    "BraveBackend",
    "CacheStats",
    "MALFORMED",
    "Malformed",
    "SEARCH_BACKENDS",
    "SEARCH_BINDING_PROVIDER",
    "WikipediaBackend",
    "CREDENTIAL_HEADERS",
    "ERROR_MALFORMED_RESPONSE",
    "ERROR_TIMED_OUT",
    "ERROR_UPSTREAM_STATUS",
    "OUTCOME_OK",
    "PROVIDER_HTTP",
    "READABLE_CONTENT_TYPES",
    "READ_BINDING_PROVIDER",
    "REFUSAL_NOT_FOUND",
    "REFUSAL_PROVIDER_REFUSED",
    "REFUSAL_RATE_LIMITED",
    "REFUSAL_RESOURCE_BUSY",
    "REFUSAL_UNSUPPORTED_CONTENT_TYPE",
    "FETCH_ERROR",
    "FETCH_REFUSED",
    "FETCH_TIMEOUT",
    "FetchFailure",
    "Fetched",
    "READ_CHUNK_BYTES",
    "REDIRECT_STATUSES",
    "REFUSAL_TOO_MANY_REDIRECTS",
    "TransportContractError",
    "DEFAULT_DEBUG_LENGTHS",
    "DEFAULT_PROVIDER",
    "DEFAULT_USER_AGENT_CONTACT",
    "DEFAULT_WIKIPEDIA_LANGUAGE",
    "GuardRefusal",
    "INTEGER_SETTINGS",
    "LOGGER_NAME",
    "MANIFEST_PATH",
    "MAX_ALLOWED_HOSTS",
    "MAX_USER_AGENT_CONTACT",
    "MODULE_NAME",
    "PROVIDERS",
    "PROVIDER_BRAVE",
    "PROVIDER_WIKIPEDIA",
    "REFUSAL_BLOCKED_ADDRESS",
    "REFUSAL_HOST_NOT_ALLOWED",
    "REFUSAL_TRANSPORT_FAILED",
    "REFUSAL_UNSUPPORTED_SCHEME",
    "REFUSAL_UNSUPPORTED_URL",
    "Target",
    "WEB_READ_ACTION",
    "WEB_SEARCH_ACTION",
    "WebModule",
    "WebModuleError",
    "activate",
    "address_refusal",
    "advisory_marker",
    "cache_key",
    "host_allowed",
    "literal_address",
    "normalize_host_name",
    "parse_target",
    "read_bounded",
    "resolve_checked",
    "validate_settings",
]
