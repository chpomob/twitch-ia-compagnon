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

**Readiness.** ``prepare`` binds the ``web.read`` provider, its spec built
from the colocated manifest, and marks the module ready, opening nothing: no
resolution and no connection happen before a call (R1, AC4). Until its
provider is bound, the executor refuses ``web.search``
``provider_not_ready`` — the kernel's own answer. ``close`` withdraws
readiness and closes a transport a call opened.

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
unclosed tag cannot leak — and breaks lines at block elements. Nothing runs
and nothing is rendered. :func:`cut_utf8` is the one byte bound on text: it
cuts on a character boundary and says whether it cut.

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
import functools
import inspect
import ipaddress
import re
import socket
import urllib.parse
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import yaml

from core.contracts import ActionObservation, ActionSpec, Destination, mask_known


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

#: A ``<meta charset>`` or ``<meta http-equiv content="…; charset=…">``.
_META_CHARSET = re.compile(
    rb"<meta\b[^>]*?\bcharset\s*=\s*[\"']?\s*([A-Za-z0-9_.:+-]+)", re.IGNORECASE
)

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

_INLINE_WHITESPACE = re.compile(r"[^\S\n]+")
_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class Extracted:
    """A page's text: its first ``<title>``, if any, and its body text."""

    title: str | None
    text: str


def extract_text(body: bytes, content_type_header: str | None) -> Extracted:
    """The title and text of *body*, decoded and stripped of markup (R3).

    The charset is the header's ``charset=`` parameter, else — for markup —
    a ``<meta>`` declaration in the first 1024 bytes, else UTF-8; a label no
    codec answers to is UTF-8 too, and decoding always replaces what it
    cannot read, so a wrong label mis-decodes but never fails. ``text/plain``
    is only normalised; anything else is parsed as HTML, the content of
    ``script``, ``style``, ``noscript`` and ``template`` and every comment
    dropped. Nothing is executed and nothing is rendered.
    """

    media_type, header_charset = _content_type(content_type_header)
    plain = media_type == _PLAIN_TEXT
    charset = header_charset
    if charset is None and not plain:
        charset = _meta_charset(body)
    text = _decode(body, charset)
    if plain:
        return Extracted(title=None, text=_normalise(text))
    parser = _TextParser()
    parser.feed(text)
    parser.close()
    return Extracted(title=parser.title(), text=_normalise("".join(parser.parts)))


def cut_utf8(text: str, max_bytes: int) -> tuple[str, bool]:
    """*text* cut to at most *max_bytes* UTF-8 bytes on a character boundary.

    Returns the text and whether anything was cut. A cut that falls inside a
    multi-byte character drops that whole character.
    """

    encoded = text.encode("utf-8", errors="replace")
    limit = max(0, max_bytes)
    if len(encoded) <= limit:
        return text, False
    return encoded[:limit].decode("utf-8", errors="ignore"), True


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

    As the WHATWG prescan does, a UTF-16 label found this way means UTF-8:
    a document whose bytes the prescan could read is not UTF-16.
    """

    match = _META_CHARSET.search(body[:_META_SCAN_BYTES])
    if match is None:
        return None
    label = match.group(1).decode("ascii")
    if label.lower().replace("_", "-").startswith("utf-16"):
        return "utf-8"
    return label


def _decode(body: bytes, charset: str | None) -> str:
    """*body* decoded by *charset*, UTF-8 when the label names no text codec."""

    try:
        codec = codecs.lookup(charset or "utf-8").name
        if codec == "utf-8":
            codec = "utf-8-sig"
        return body.decode(codec, errors="replace")
    except LookupError:  # an unknown label, or a codec that is not text
        return body.decode("utf-8-sig", errors="replace")


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
    of the text; the first one is the title.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skipped = dict.fromkeys(_SKIPPED_ELEMENTS, 0)
        self._preformatted = 0
        self._title_parts: list[str] = []
        self._in_title = False
        self._title_done = False

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in self._skipped:
            self._skipped[tag] += 1
        elif tag == "title":
            self._in_title = True
        elif tag == "pre":
            self._preformatted += 1
        if tag in _BLOCK_ELEMENTS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._skipped:
            self._skipped[tag] = max(0, self._skipped[tag] - 1)
        elif tag == "title" and self._in_title:
            self._in_title = False
            self._title_done = True
        elif tag == "pre":
            self._preformatted = max(0, self._preformatted - 1)
        if tag in _BLOCK_ELEMENTS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if any(self._skipped.values()):
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
# The handle
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class _Progress:
    """How far one fetch got, readable after its task is cancelled."""

    redirects: int = 0


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
        self._prepared = False
        self._closed = False

    @property
    def settings(self) -> _Settings:
        return self._settings

    # -- lifecycle hooks ---------------------------------------------------- #

    async def prepare(self) -> None:
        """Bind the ``web.read`` provider and mark ready, opening nothing (R1, AC4).

        The spec comes from the colocated manifest. ``web.search`` has no
        provider yet, so the executor answers it ``provider_not_ready``; no
        name is resolved and no connection made.
        """

        if self._prepared or self._closed:
            return
        spec = _declared_spec(WEB_READ_ACTION)
        try:
            self._actions.register(
                spec, self._read_provider, provider_name=READ_BINDING_PROVIDER
            )
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
        if self._in_flight >= self._settings.max_concurrent_requests:
            return _read_failure(trace, REFUSAL_RESOURCE_BUSY)
        self._in_flight += 1
        try:
            return await self._read(url, trace)
        finally:
            self._in_flight -= 1

    async def _read(self, url: Any, trace: dict[str, Any]) -> ActionObservation:
        """Fetch, judge the content type unread, extract, bound (R3)."""

        settings = self._settings
        fetched = await self.fetch(url, apply_allowlist=True, accept_body=_readable_answer)
        trace["redirects"] = fetched.redirects
        if isinstance(fetched, FetchFailure):
            return _read_failure(trace, fetched)
        if _classify(WEB_READ_ACTION, fetched.status)[0] != "success":
            return _read_failure(trace, fetched.status)
        if fetched.body is None:
            return _read_failure(trace, REFUSAL_UNSUPPORTED_CONTENT_TYPE)
        try:
            extracted = extract_text(fetched.body, _header(fetched.headers, "Content-Type"))
        except Exception:  # noqa: BLE001 - the parser's text would quote the page
            return _read_failure(trace, ERROR_MALFORMED_RESPONSE)
        text, text_cut = cut_utf8(extracted.text, settings.max_text_bytes)
        trace["text_bytes"] = len(text.encode("utf-8"))
        result: dict[str, Any] = {
            "advisory": advisory_marker(),
            "final_url": fetched.final_url,
            "text": text,
            "truncated": fetched.truncated or text_cut,
        }
        if extracted.title is not None:
            result["title"] = extracted.title
        return ActionObservation(
            status="success",
            provenance=_read_provenance(trace),
            result=result,
            parts=(),
        )

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
    ) -> Fetched | FetchFailure:
        """Fetch *url* through the guard, every hop, within one deadline (R4, R6).

        The whole call — resolution, every hop and the body — runs as one task
        raced against ``request_timeout_seconds`` on the injected sleeper; when
        that elapses first the task is cancelled, the open response closed,
        and the call is ``timeout timed_out``. *accept_body* sees the final
        response before any body byte is read and may decline it; by default
        the body is read, bounded. A cancellation of the caller cancels the
        work and is re-raised.
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
        timer = asyncio.ensure_future(sleeper(self._settings.request_timeout_seconds))
        try:
            await asyncio.wait({work, timer}, return_when=asyncio.FIRST_COMPLETED)
        except asyncio.CancelledError:
            work.cancel()
            timer.cancel()
            await asyncio.gather(work, timer, return_exceptions=True)
            raise
        if not work.done():
            work.cancel()
            await asyncio.gather(work, return_exceptions=True)
            return FetchFailure(FETCH_TIMEOUT, ERROR_TIMED_OUT, progress.redirects)
        timer.cancel()
        await asyncio.gather(timer, return_exceptions=True)
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
    "host_allowed",
    "literal_address",
    "normalize_host_name",
    "parse_target",
    "read_bounded",
    "resolve_checked",
    "validate_settings",
]
