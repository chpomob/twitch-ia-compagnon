"""``operator`` — the operator's own button: three surfaces, one event (R6).

**The press.** The module serves 1 to :data:`MAX_BUTTONS` configured buttons
(``buttons``: ``id``, ``label``, ``prompt_text``, ``cooldown_seconds`` and an
optional overlay ``rect``). An accepted press of a button builds one
normalised schema-version-2 event of kind ``manual`` (:func:`press_event`):
``payload.button`` = ``{id, surface}`` with the surface it came from
(``page``, ``hotkey`` or ``overlay``), text the button's ``prompt_text``,
author the reserved identity ``operator:<id>`` (every platform module refuses
an author identity containing ``:``, so no viewer shares its session) with
the button's label, a fresh ``message_id`` ``operator:<id>:<uuid4 hex>``, the
configured ``destination`` as ``platform``/``channel_id`` and source
``operator``.

**Evaluation and admission.** The event is decided by the shared trigger
engine with this module's policy (the manifest's default is ``event_kind:
[manual]``; the declared ``button`` type selects listed buttons); the decision
is traced as ``input.trigger.accepted`` or ``input.trigger.rejected`` with
:func:`~core.triggers.decision_trace_payload`. Every decided press is
**published** once on the bus as ``channel.chat.message``, decided either way,
as the twitch input publishes its events: the bus copy is the event itself.

**Admission, exactly once.** Who admits depends on whose scheduler it is, as
for the twitch input. A scheduler the context carries is shared, and the
brain's ``channel.chat.message`` consumer admits nothing into it: an accepted
press is admitted **directly** through it, ``admit(session_key, work)``, with
the session key ``(platform, channel_id, operator:<id>)``. Without one — the
runtime ``core.main`` assembles — the brain owns its scheduler (and publishes
it as the service ``(admission, runs)``, :data:`ADMISSION_SERVICE_KIND`,
:data:`ADMISSION_SERVICE_SCOPE`) and its bus consumer admits the published
copy after reading the decision this input recorded in the shared trigger
engine; admitting it here too would run the press twice, so the publication
is then the whole route (:data:`COUNT_PUBLISHED`). Without a trigger engine
nothing is decided, published or admitted: the press is then counted
(:data:`COUNT_NOT_ADMITTED`), never admitted undecided.

**The request core.** :class:`OperatorCore` answers ``handle(method, path,
headers, body) -> (status, body)`` with no socket: the listener hands it every
request. Its guards run in a fixed order — open, ``Host``, token, route,
``Origin`` (a press only), then the press itself — and each refusal increments
one named count (:meth:`OperatorCore.counts`) and answers a fixed body: no
response, count or diagnostic ever carries a header value or the token. The
token is compared in constant time (:func:`hmac.compare_digest`); it is read
from the ``X-Operator-Token`` header or the ``token`` query parameter. The
accepted ``Host`` set is the listen address (with the loopback names when it
is loopback). A press sent with an ``Origin`` other than the page's own —
``http://`` and the accepted ``Host``, the default port 80 implied when
absent on either — is refused. A press names its button
and surface in a JSON body; a disabled surface, an unknown button, and a
press of a button inside its cooldown (per button, on the injected clock,
since its last *accepted* press) are refused.

**The surfaces.** ``GET /`` is the page listing the buttons, ``GET /overlay``
the overlay page showing ``overlay.image`` with each button that has a
``rect`` placed over it in percent, and ``POST /press`` the press endpoint the
pages and the press command (``python -m modules.operator press``) use. The
pages read the token from their own URL and send it back in the header; they
never contain it, and every response forbids a ``Referer``
(``Referrer-Policy: no-referrer``). A page is served only when its surface is
enabled.

**Lifecycle.** ``prepare`` binds nothing. ``start_inputs`` opens the listener
through the ``_server_factory`` seam (by default aiohttp's ``AppRunner`` and
``TCPSite``, the access log disabled so no query string — the token — is
logged); ``stop_inputs`` closes it, and the core refuses every later request.

**Settings** (checked by :func:`validate_settings`, one value-free diagnostic
naming the field, before any listener opens): at least one of ``surfaces``
(``page``, ``hotkey``, ``overlay``, each false by default), a ``token`` of at
least :data:`TOKEN_MIN_CHARS` characters, a non-empty ``prompt_text`` per
button, and a loopback ``listen.host`` (default ``127.0.0.1``) unless
``listen.allow_non_loopback`` is true. A wildcard ``listen.host`` (``0.0.0.0``,
``::``) is refused even then: a request names the address it reached, so the
accepted ``Host`` set must be that address — bind it instead.

Nothing here imports ``core.runtime``, ``core.loader`` or ``core.lifecycle``
at module level, so the press command runs without the runtime (A8).
"""

from __future__ import annotations

import asyncio
import hmac
import html
import inspect
import ipaddress
import json
import time
import uuid
from collections import deque
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlsplit

# Nothing from ``core`` is imported at module level: the ``core`` package
# imports its loader, and the press command must run without it (A8). The
# few names the runtime path needs are imported where they are used.


MODULE_NAME = "operator"

#: The kind, event type, author prefix and work kind of a press (R6).
PRESS_KIND = "manual"
PRESS_EVENT_TYPE = "channel.chat.message"
AUTHOR_PREFIX = "operator:"
WORK_KIND = "operator.press"

SURFACE_PAGE = "page"
SURFACE_HOTKEY = "hotkey"
SURFACE_OVERLAY = "overlay"
SURFACES = (SURFACE_PAGE, SURFACE_HOTKEY, SURFACE_OVERLAY)

#: The service key the brain publishes its owned scheduler under: when it
#: owns one, its bus consumer admits the published press (see the docstring).
ADMISSION_SERVICE_KIND = "admission"
ADMISSION_SERVICE_SCOPE = "runs"

#: The routes the listener serves.
PATH_PAGE = "/"
PATH_OVERLAY = "/overlay"
PATH_PRESS = "/press"

#: Where a request carries the token: this header, or this query parameter.
TOKEN_HEADER = "x-operator-token"
TOKEN_QUERY = "token"

MAX_BUTTONS = 32
BUTTON_ID_MAX_CHARS = 64
LABEL_MAX_CHARS = 100
PROMPT_MAX_CHARS = 500
COOLDOWN_BOUNDS = (0, 3600)
DEFAULT_COOLDOWN_SECONDS = 2
TOKEN_MIN_CHARS = 32
DEFAULT_HOST = "127.0.0.1"
IMAGE_MAX_CHARS = 2048
#: The largest press body the core reads; a larger one is refused.
MAX_BODY_BYTES = 4096

#: The counts :meth:`OperatorCore.counts` reports.
COUNT_PRESSED = "pressed"
COUNT_PUBLISHED = "published"
COUNT_ADMITTED = "admitted"
COUNT_NOT_ADMITTED = "not_admitted"
COUNT_REFUSED_TOKEN = "refused_token"
COUNT_REFUSED_HOST = "refused_host"
COUNT_REFUSED_ORIGIN = "refused_origin"
COUNT_REFUSED_UNKNOWN_BUTTON = "refused_unknown_button"
COUNT_REFUSED_COOLDOWN = "refused_cooldown"
COUNT_REFUSED_SURFACE = "refused_surface"
COUNT_REFUSED_CLOSED = "refused_closed"
COUNT_REFUSED_ROUTE = "refused_route"
COUNT_REFUSED_MALFORMED = "refused_malformed"
REFUSAL_COUNTS = (
    COUNT_REFUSED_TOKEN,
    COUNT_REFUSED_HOST,
    COUNT_REFUSED_ORIGIN,
    COUNT_REFUSED_UNKNOWN_BUTTON,
    COUNT_REFUSED_COOLDOWN,
    COUNT_REFUSED_SURFACE,
    COUNT_REFUSED_CLOSED,
    COUNT_REFUSED_ROUTE,
    COUNT_REFUSED_MALFORMED,
)

#: The headers every response carries: no ``Referer`` leaves a page, nothing
#: is cached, and a page is never framed by another site.
RESPONSE_HEADERS: Mapping[str, str] = {
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
        "img-src * data:; connect-src 'self'; frame-ancestors 'none'"
    ),
}
CONTENT_TYPE_HTML = "text/html"
CONTENT_TYPE_JSON = "application/json"

_ORIGIN_SCHEME = "http://"
_LOOPBACK_NAMES = frozenset({"127.0.0.1", "localhost", "[::1]"})
_BUTTON_ID_CHARACTERS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
)
_IMAGE_SCHEMES = ("http://", "https://", "data:image/")
_RECT_KEYS = ("x", "y", "w", "h")

_SETTING_BUTTONS = "buttons"
_SETTING_DESTINATION = "destination"
_SETTING_SURFACES = "surfaces"
_SETTING_LISTEN = "listen"
_SETTING_TOKEN = "token"
_SETTING_OVERLAY = "overlay"
_SETTINGS = frozenset(
    {
        _SETTING_BUTTONS,
        _SETTING_DESTINATION,
        _SETTING_SURFACES,
        _SETTING_LISTEN,
        _SETTING_TOKEN,
        _SETTING_OVERLAY,
    }
)
_BUTTON_KEYS = frozenset({"id", "label", "prompt_text", "cooldown_seconds", "rect"})
_LISTEN_KEYS = frozenset({"host", "port", "allow_non_loopback"})
_DESTINATION_KEYS = frozenset({"platform", "channel_id"})
_OVERLAY_KEYS = frozenset({"image"})
# The reserved block the entry point hands every enabled module.
_ACCEPTED_LIMITS_SETTING = "limits"

_SEAM_SERVER_FACTORY = "_server_factory"
_SEAMS = frozenset({_SEAM_SERVER_FACTORY})

Clock = Callable[[], float]
#: What the listener hands every request to: ``(status, body, content type)``.
Dispatch = Callable[[str, str, Mapping[str, str], bytes], Awaitable[tuple[int, str, str]]]
#: ``factory(host, port, dispatch)`` → a listener with ``async start()`` and
#: ``async close()``.
ServerFactory = Callable[[str, int, Dispatch], Any]


class OperatorModuleError(RuntimeError):
    """A setup failure whose message contains no configured value."""


# --------------------------------------------------------------------------- #
# Settings validation hook
# --------------------------------------------------------------------------- #


def validate_settings(settings: Any) -> list[str]:
    """Check this module's settings; return one diagnostic per offending field.

    This is the hook ``settings_validator`` in the manifest names. Each
    diagnostic names the module and the field and nothing else — the token,
    the address and the texts are never echoed; an empty list means the
    settings are accepted.
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
    if _SEAM_SERVER_FACTORY in settings and not callable(settings[_SEAM_SERVER_FACTORY]):
        diagnostics.append(_setting_diagnostic(_SEAM_SERVER_FACTORY, "must be callable"))
    diagnostics.extend(_validate_buttons(settings.get(_SETTING_BUTTONS)))
    diagnostics.extend(_validate_destination(settings.get(_SETTING_DESTINATION)))
    diagnostics.extend(_validate_surfaces(settings.get(_SETTING_SURFACES)))
    diagnostics.extend(_validate_listen(settings.get(_SETTING_LISTEN)))
    token = settings.get(_SETTING_TOKEN)
    if not isinstance(token, str) or not token:
        diagnostics.append(_setting_diagnostic(_SETTING_TOKEN, "must be set"))
    elif len(token) < TOKEN_MIN_CHARS:
        diagnostics.append(
            _setting_diagnostic(
                _SETTING_TOKEN, f"must be at least {TOKEN_MIN_CHARS} characters long"
            )
        )
    if _SETTING_OVERLAY in settings:
        diagnostics.extend(_validate_overlay(settings[_SETTING_OVERLAY]))
    return diagnostics


def _validate_buttons(value: Any) -> list[str]:
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_BUTTONS:
        return [
            _setting_diagnostic(
                _SETTING_BUTTONS, f"must be a list of 1 to {MAX_BUTTONS} buttons"
            )
        ]
    diagnostics: list[str] = []
    seen: set[str] = set()
    for index, button in enumerate(value):
        field = f"{_SETTING_BUTTONS}[{index}]"
        if not isinstance(button, Mapping):
            diagnostics.append(_setting_diagnostic(field, "must be a mapping"))
            continue
        for key in button:
            if key not in _BUTTON_KEYS:
                diagnostics.append(
                    _setting_diagnostic(f"{field}.{key}", "is not a button setting")
                )
        button_id = button.get("id")
        if not _is_button_id(button_id):
            diagnostics.append(
                _setting_diagnostic(
                    f"{field}.id",
                    f"must be 1 to {BUTTON_ID_MAX_CHARS} letters, digits, - or _",
                )
            )
        elif button_id in seen:
            diagnostics.append(_setting_diagnostic(f"{field}.id", "must be distinct"))
        else:
            seen.add(button_id)
        if not _is_bounded_text(button.get("label"), LABEL_MAX_CHARS):
            diagnostics.append(
                _setting_diagnostic(
                    f"{field}.label",
                    f"must be a non-empty text of at most {LABEL_MAX_CHARS} characters",
                )
            )
        if not _is_bounded_text(button.get("prompt_text"), PROMPT_MAX_CHARS):
            diagnostics.append(
                _setting_diagnostic(
                    f"{field}.prompt_text",
                    f"must be a non-empty text of at most {PROMPT_MAX_CHARS} characters",
                )
            )
        if "cooldown_seconds" in button and not _is_int_within(
            button["cooldown_seconds"], *COOLDOWN_BOUNDS
        ):
            low, high = COOLDOWN_BOUNDS
            diagnostics.append(
                _setting_diagnostic(
                    f"{field}.cooldown_seconds", f"must be an integer from {low} to {high}"
                )
            )
        if "rect" in button and _rect_of(button["rect"]) is None:
            diagnostics.append(
                _setting_diagnostic(
                    f"{field}.rect",
                    "must hold x, y, w and h in percent, w and h above 0, within 100",
                )
            )
    return diagnostics


def _validate_destination(value: Any) -> list[str]:
    if not isinstance(value, Mapping):
        return [_setting_diagnostic(_SETTING_DESTINATION, "must be a mapping")]
    diagnostics = [
        _setting_diagnostic(f"{_SETTING_DESTINATION}.{key}", "is not a destination setting")
        for key in value
        if key not in _DESTINATION_KEYS
    ]
    for key in ("platform", "channel_id"):
        if not _is_text(value.get(key)):
            diagnostics.append(
                _setting_diagnostic(f"{_SETTING_DESTINATION}.{key}", "must be a non-empty text")
            )
    return diagnostics


def _validate_surfaces(value: Any) -> list[str]:
    if value is None:
        return [_setting_diagnostic(_SETTING_SURFACES, "must enable at least one surface")]
    if not isinstance(value, Mapping):
        return [_setting_diagnostic(_SETTING_SURFACES, "must be a mapping")]
    diagnostics: list[str] = []
    for key, enabled in value.items():
        if key not in SURFACES:
            diagnostics.append(
                _setting_diagnostic(f"{_SETTING_SURFACES}.{key}", "is not a surface")
            )
        elif not isinstance(enabled, bool):
            diagnostics.append(
                _setting_diagnostic(f"{_SETTING_SURFACES}.{key}", "must be true or false")
            )
    if not diagnostics and not any(value.get(surface) is True for surface in SURFACES):
        diagnostics.append(
            _setting_diagnostic(_SETTING_SURFACES, "must enable at least one surface")
        )
    return diagnostics


def _validate_listen(value: Any) -> list[str]:
    if not isinstance(value, Mapping):
        return [_setting_diagnostic(_SETTING_LISTEN, "must be a mapping")]
    diagnostics = [
        _setting_diagnostic(f"{_SETTING_LISTEN}.{key}", "is not a listen setting")
        for key in value
        if key not in _LISTEN_KEYS
    ]
    allow = value.get("allow_non_loopback", False)
    if not isinstance(allow, bool):
        diagnostics.append(
            _setting_diagnostic(f"{_SETTING_LISTEN}.allow_non_loopback", "must be true or false")
        )
        allow = False
    host = value.get("host", DEFAULT_HOST)
    if not _is_text(host) or any(character.isspace() for character in host):
        diagnostics.append(
            _setting_diagnostic(f"{_SETTING_LISTEN}.host", "must be a host name or address")
        )
    elif not allow and not _is_loopback(host):
        diagnostics.append(
            _setting_diagnostic(
                f"{_SETTING_LISTEN}.host",
                "must be a loopback address unless allow_non_loopback is true",
            )
        )
    elif _is_wildcard(host):
        # A request names the address it reached, never the wildcard: the
        # accepted ``Host`` set would refuse every one of them.
        diagnostics.append(
            _setting_diagnostic(
                f"{_SETTING_LISTEN}.host",
                "must be the address requests reach, not a wildcard address",
            )
        )
    if not _is_int_within(value.get("port"), 1, 65535):
        diagnostics.append(
            _setting_diagnostic(f"{_SETTING_LISTEN}.port", "must be an integer from 1 to 65535")
        )
    return diagnostics


def _validate_overlay(value: Any) -> list[str]:
    if not isinstance(value, Mapping):
        return [_setting_diagnostic(_SETTING_OVERLAY, "must be a mapping")]
    diagnostics = [
        _setting_diagnostic(f"{_SETTING_OVERLAY}.{key}", "is not an overlay setting")
        for key in value
        if key not in _OVERLAY_KEYS
    ]
    if "image" in value and not _is_image(value["image"]):
        diagnostics.append(
            _setting_diagnostic(
                f"{_SETTING_OVERLAY}.image",
                f"must be an http, https or data:image URL of at most {IMAGE_MAX_CHARS} characters",
            )
        )
    return diagnostics


def _setting_diagnostic(field_name: str, reason: str) -> str:
    return f"module {MODULE_NAME!r}: field {field_name!r}: {reason}"


def _is_int_within(value: Any, low: int, high: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_bounded_text(value: Any, limit: int) -> bool:
    return _is_text(value) and len(value) <= limit


def _is_button_id(value: Any) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= BUTTON_ID_MAX_CHARS
        and set(value) <= _BUTTON_ID_CHARACTERS
    )


def _is_image(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) <= IMAGE_MAX_CHARS
        and value.lower().startswith(_IMAGE_SCHEMES)
        and not any(character.isspace() for character in value)
    )


def _rect_of(value: Any) -> tuple[float, float, float, float] | None:
    """``(x, y, w, h)`` in percent, inside the image, else ``None``."""

    if not isinstance(value, Mapping) or set(value) != set(_RECT_KEYS):
        return None
    numbers = [value[key] for key in _RECT_KEYS]
    if not all(_is_number(number) and 0 <= number <= 100 for number in numbers):
        return None
    x, y, w, h = (float(number) for number in numbers)
    if w <= 0 or h <= 0 or x + w > 100 or y + h > 100:
        return None
    return x, y, w, h


def _is_loopback(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


def _is_wildcard(host: str) -> bool:
    """Whether *host* is an unspecified (every-interface) address."""

    try:
        return ipaddress.ip_address(host.strip("[]")).is_unspecified
    except ValueError:
        return False


def _host_form(host: str) -> str:
    """*host* as it appears in an authority: an IPv6 address bracketed."""

    bare = host.strip("[]")
    try:
        address = ipaddress.ip_address(bare)
    except ValueError:
        return host.lower()
    return f"[{address.compressed}]" if address.version == 6 else address.compressed


def accepted_hosts(host: str, port: int) -> frozenset[str]:
    """The ``Host`` values a request may name: the listen address (A7).

    A loopback listen address also accepts the other loopback names, which
    name the same listener.
    """

    names = {_host_form(host)}
    if _is_loopback(host):
        names |= _LOOPBACK_NAMES
    return frozenset(f"{name}:{port}" for name in names)


def _origin_authority(value: str) -> str | None:
    """An ``http://`` ``Origin`` as ``name:port`` like :func:`_authority`, else ``None``.

    A browser leaves the default port out of an origin, as out of a
    ``Host``: both normalise to port 80 and compare equal.
    """

    text = value.strip().lower()
    if not text.startswith(_ORIGIN_SCHEME):
        return None
    return _authority(text[len(_ORIGIN_SCHEME):])


def _authority(value: str) -> str | None:
    """A ``Host`` value as ``name:port`` (port 80 when absent), else ``None``."""

    text = value.strip().lower()
    if not text or any(character in text for character in "@/?#\\ \t,") or not text.isascii():
        return None
    if text.startswith("["):
        name, _, rest = text.partition("]")
        name += "]"
    else:
        name, colon, port = text.partition(":")
        rest = colon + port
    if not rest:
        return f"{name}:80"
    if not rest.startswith(":") or not rest[1:].isdigit():
        return None
    return f"{name}:{int(rest[1:])}"


# --------------------------------------------------------------------------- #
# Settings and the event
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Button:
    """One configured button."""

    id: str
    label: str
    prompt_text: str
    cooldown_seconds: int
    rect: tuple[float, float, float, float] | None


@dataclass(frozen=True, slots=True)
class OperatorSettings:
    """The accepted settings, parsed once."""

    buttons: tuple[Button, ...]
    platform: str
    channel_id: str
    surfaces: frozenset[str]
    host: str
    port: int
    token: str
    image: str | None

    @classmethod
    def from_mapping(cls, settings: Mapping[str, Any]) -> "OperatorSettings":
        buttons = tuple(
            Button(
                id=entry["id"],
                label=entry["label"],
                prompt_text=entry["prompt_text"],
                cooldown_seconds=int(entry.get("cooldown_seconds", DEFAULT_COOLDOWN_SECONDS)),
                rect=_rect_of(entry["rect"]) if "rect" in entry else None,
            )
            for entry in settings.get(_SETTING_BUTTONS) or ()
        )
        destination = settings.get(_SETTING_DESTINATION) or {}
        surfaces = settings.get(_SETTING_SURFACES) or {}
        listen = settings.get(_SETTING_LISTEN) or {}
        overlay = settings.get(_SETTING_OVERLAY) or {}
        return cls(
            buttons=buttons,
            platform=destination.get("platform", ""),
            channel_id=destination.get("channel_id", ""),
            surfaces=frozenset(surface for surface in SURFACES if surfaces.get(surface) is True),
            host=listen.get("host", DEFAULT_HOST),
            port=int(listen.get("port", 0)),
            token=settings.get(_SETTING_TOKEN, ""),
            image=overlay.get("image"),
        )

    def __repr__(self) -> str:
        # The token never reaches a representation (R6).
        return (
            f"OperatorSettings(buttons={len(self.buttons)}, "
            f"surfaces={sorted(self.surfaces)}, token=<redacted>)"
        )


def press_event(
    button: Button, surface: str, platform: str, channel_id: str
) -> dict[str, Any]:
    """The normalised schema-version-2 ``manual`` event of one accepted press."""

    from core.triggers import NORMALIZED_SCHEMA_VERSION

    return {
        "type": PRESS_EVENT_TYPE,
        "payload": {
            "platform": platform,
            "channel_id": channel_id,
            "author": {"id": f"{AUTHOR_PREFIX}{button.id}", "display_name": button.label},
            "message_id": f"{AUTHOR_PREFIX}{button.id}:{uuid.uuid4().hex}",
            "text": button.prompt_text,
            "kind": PRESS_KIND,
            "button": {"id": button.id, "surface": surface},
        },
        "metadata": {"source": MODULE_NAME, "schema_version": NORMALIZED_SCHEMA_VERSION},
    }


# --------------------------------------------------------------------------- #
# The pages
# --------------------------------------------------------------------------- #

# The token is read from the page's own URL and sent back in a header; the
# page never contains it and never logs it.
_PRESS_SCRIPT = (
    "(function(){"
    "var t=new URLSearchParams(location.search).get('token')||'';"
    "var s=document.body.getAttribute('data-surface');"
    "document.querySelectorAll('[data-button]').forEach(function(b){"
    "b.addEventListener('click',function(){"
    "fetch('/press',{method:'POST',cache:'no-store',referrerPolicy:'no-referrer',"
    "headers:{'Content-Type':'application/json','X-Operator-Token':t},"
    "body:JSON.stringify({button:b.getAttribute('data-button'),surface:s})});"
    "});});})();"
)


def _page_html(settings: OperatorSettings) -> str:
    buttons = "".join(
        f'<button type="button" data-button="{html.escape(button.id)}">'
        f"{html.escape(button.label)}</button>"
        for button in settings.buttons
    )
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        "<meta name=\"referrer\" content=\"no-referrer\"><title>Operator</title>"
        "<style>body{font-family:sans-serif;margin:1em}"
        "button{display:block;margin:.5em 0;padding:1em;font-size:1.2em;width:100%}</style>"
        f"</head><body data-surface=\"{SURFACE_PAGE}\"><main>{buttons}</main>"
        f"<script>{_PRESS_SCRIPT}</script></body></html>"
    )


def _overlay_html(settings: OperatorSettings) -> str:
    image = (
        ""
        if settings.image is None
        else f'<img alt="" src="{html.escape(settings.image, quote=True)}">'
    )
    buttons = "".join(
        f'<button type="button" data-button="{html.escape(button.id)}" '
        f'title="{html.escape(button.label)}" '
        f'style="left:{x:g}%;top:{y:g}%;width:{w:g}%;height:{h:g}%"></button>'
        for button in settings.buttons
        if button.rect is not None
        for x, y, w, h in (button.rect,)
    )
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        "<meta name=\"referrer\" content=\"no-referrer\"><title>Operator overlay</title>"
        "<style>html,body{margin:0;background:transparent;overflow:hidden}"
        "main{position:relative;width:100vw;height:100vh}"
        "img{position:absolute;left:0;top:0;width:100%;height:100%;object-fit:fill}"
        "button{position:absolute;background:transparent;border:0;padding:0;cursor:pointer}"
        f"</style></head><body data-surface=\"{SURFACE_OVERLAY}\"><main>{image}{buttons}</main>"
        f"<script>{_PRESS_SCRIPT}</script></body></html>"
    )


def _answer(status: str, reason: str | None = None) -> str:
    body = {"status": status} if reason is None else {"status": status, "reason": reason}
    return json.dumps(body, separators=(",", ":"))


# --------------------------------------------------------------------------- #
# The request core
# --------------------------------------------------------------------------- #

#: The status and the fixed body of each refusal.
_REFUSALS: Mapping[str, tuple[int, str]] = {
    COUNT_REFUSED_CLOSED: (503, _answer("refused", "closed")),
    COUNT_REFUSED_HOST: (403, _answer("refused", "host")),
    COUNT_REFUSED_TOKEN: (401, _answer("refused", "token")),
    COUNT_REFUSED_ROUTE: (404, _answer("refused", "route")),
    COUNT_REFUSED_ORIGIN: (403, _answer("refused", "origin")),
    COUNT_REFUSED_MALFORMED: (400, _answer("refused", "malformed")),
    COUNT_REFUSED_SURFACE: (403, _answer("refused", "surface")),
    COUNT_REFUSED_UNKNOWN_BUTTON: (404, _answer("refused", "unknown_button")),
    COUNT_REFUSED_COOLDOWN: (429, _answer("refused", "cooldown")),
}
_ACCEPTED = (200, _answer("accepted"))


class OperatorCore:
    """Every request's answer, with no socket: guards, pages and presses.

    *submit* receives each accepted press's event; what it decides is not
    the core's to answer, so an accepted press answers ``accepted`` whatever
    the trigger engine then decides. The core is closed until :meth:`open`.
    """

    def __init__(
        self,
        settings: OperatorSettings,
        *,
        clock: Clock,
        submit: Callable[[dict[str, Any]], Any],
    ) -> None:
        self._settings = settings
        self._clock = clock
        self._submit = submit
        self._buttons = {button.id: button for button in settings.buttons}
        self._hosts = accepted_hosts(settings.host, settings.port)
        self._token = settings.token.encode("utf-8")
        self._page = _page_html(settings)
        self._overlay = _overlay_html(settings)
        # The instant of each button's last accepted press.
        self._last_accepted: dict[str, float] = {}
        self._counts = {name: 0 for name in (COUNT_PRESSED, *REFUSAL_COUNTS)}
        self._open = False

    @property
    def is_open(self) -> bool:
        return self._open

    def open(self) -> None:
        self._open = True

    def close(self) -> None:
        """Refuse every later request."""

        self._open = False

    def counts(self) -> Mapping[str, int]:
        """How many presses were accepted, and each refusal by its name."""

        return dict(self._counts)

    def handle(
        self, method: str, path: str, headers: Mapping[str, str], body: bytes
    ) -> tuple[int, str]:
        """Answer one request: ``(status, body)``; never raises."""

        try:
            return self._handle(method, path, headers, body)
        except Exception:  # noqa: BLE001 - a malformed request must not escape
            return self._refuse(COUNT_REFUSED_MALFORMED)

    def _handle(
        self, method: str, path: str, headers: Mapping[str, str], body: bytes
    ) -> tuple[int, str]:
        if not self._open:
            return self._refuse(COUNT_REFUSED_CLOSED)
        lowered = {str(name).lower(): value for name, value in headers.items()}
        host = lowered.get("host")
        authority = _authority(host) if isinstance(host, str) else None
        if authority is None or authority not in self._hosts:
            return self._refuse(COUNT_REFUSED_HOST)
        parts = urlsplit(path)
        if not self._token_matches(lowered.get(TOKEN_HEADER), parts.query):
            return self._refuse(COUNT_REFUSED_TOKEN)
        route = (method.upper(), parts.path)
        if route == ("GET", PATH_PAGE):
            return self._serve(SURFACE_PAGE, self._page)
        if route == ("GET", PATH_OVERLAY):
            return self._serve(SURFACE_OVERLAY, self._overlay)
        if route != ("POST", PATH_PRESS):
            return self._refuse(COUNT_REFUSED_ROUTE)
        origin = lowered.get("origin")
        if origin is not None and (
            not isinstance(origin, str) or _origin_authority(origin) != authority
        ):
            return self._refuse(COUNT_REFUSED_ORIGIN)
        return self._press(body)

    def _token_matches(self, header: Any, query: str) -> bool:
        candidate = header
        if candidate is None:
            values = parse_qs(query, keep_blank_values=True).get(TOKEN_QUERY)
            candidate = values[0] if values and len(values) == 1 else None
        if not isinstance(candidate, str):
            return False
        # Constant time, whatever the candidate's length or content.
        return hmac.compare_digest(candidate.encode("utf-8"), self._token)

    def _serve(self, surface: str, page: str) -> tuple[int, str]:
        if surface not in self._settings.surfaces:
            return self._refuse(COUNT_REFUSED_SURFACE)
        return 200, page

    def _press(self, body: bytes) -> tuple[int, str]:
        if not isinstance(body, (bytes, bytearray)) or len(body) > MAX_BODY_BYTES:
            return self._refuse(COUNT_REFUSED_MALFORMED)
        try:
            request = json.loads(bytes(body).decode("utf-8"))
        except ValueError:
            return self._refuse(COUNT_REFUSED_MALFORMED)
        if not isinstance(request, Mapping):
            return self._refuse(COUNT_REFUSED_MALFORMED)
        surface = request.get("surface")
        button_id = request.get("button")
        if not isinstance(surface, str) or not isinstance(button_id, str):
            return self._refuse(COUNT_REFUSED_MALFORMED)
        if surface not in self._settings.surfaces:
            return self._refuse(COUNT_REFUSED_SURFACE)
        button = self._buttons.get(button_id)
        if button is None:
            return self._refuse(COUNT_REFUSED_UNKNOWN_BUTTON)
        now = self._clock()
        last = self._last_accepted.get(button.id)
        if last is not None and now - last < button.cooldown_seconds:
            return self._refuse(COUNT_REFUSED_COOLDOWN)
        self._last_accepted[button.id] = now
        self._counts[COUNT_PRESSED] += 1
        self._submit(
            press_event(button, surface, self._settings.platform, self._settings.channel_id)
        )
        return _ACCEPTED

    def _refuse(self, count: str) -> tuple[int, str]:
        self._counts[count] += 1
        return _REFUSALS[count]


def content_type(body: str) -> str:
    """The content type of a core answer: a page, or a JSON answer."""

    return CONTENT_TYPE_HTML if body.startswith("<!doctype html>") else CONTENT_TYPE_JSON


# --------------------------------------------------------------------------- #
# The default listener: aiohttp, the access log disabled
# --------------------------------------------------------------------------- #


class AiohttpListener:
    """aiohttp's ``AppRunner`` and ``TCPSite`` in front of a dispatch.

    The access log is disabled explicitly: it would log the query string,
    which may hold the token.
    """

    def __init__(self, host: str, port: int, dispatch: Dispatch) -> None:
        self._host = host
        self._port = port
        self._dispatch = dispatch
        self._runner: Any = None

    async def start(self) -> None:
        from aiohttp import web

        app = web.Application(client_max_size=MAX_BODY_BYTES)
        app.router.add_route("*", "/{tail:.*}", self._handle)
        runner = web.AppRunner(app, access_log=None, handle_signals=False)
        await runner.setup()
        try:
            await web.TCPSite(runner, self._host, self._port).start()
        except BaseException:
            await runner.cleanup()
            raise
        self._runner = runner

    async def close(self) -> None:
        runner, self._runner = self._runner, None
        if runner is not None:
            await runner.cleanup()

    async def _handle(self, request: Any) -> Any:
        from aiohttp import web

        try:
            body = await request.read()
        except web.HTTPException:
            body = b"\0" * (MAX_BODY_BYTES + 1)
        status, text, kind = await self._dispatch(
            request.method, request.path_qs, request.headers, body
        )
        return web.Response(
            status=status,
            text=text,
            content_type=kind,
            charset="utf-8",
            headers=dict(RESPONSE_HEADERS),
        )


# --------------------------------------------------------------------------- #
# The module
# --------------------------------------------------------------------------- #


class OperatorModule:
    """The v2 input handle: open the listener, decide and admit presses."""

    def __init__(
        self,
        context: Any,
        settings: OperatorSettings,
        *,
        server_factory: ServerFactory,
    ) -> None:
        self._supervision = getattr(context, "supervision", None)
        self._bus = getattr(context, "bus", None)
        self._triggers = getattr(context, "triggers", None)
        self._scheduler = getattr(context, "scheduler", None)
        self._clock: Clock = getattr(context, "clock", None) or time.monotonic
        self._settings = settings
        self._server_factory = server_factory
        self._core = OperatorCore(settings, clock=self._clock, submit=self._submit)
        self._listener: Any = None
        # The presses decided and not published yet — the event, then its
        # decision's trace — published after each request.
        self._pending: deque[tuple[dict[str, Any] | None, Any]] = deque()
        self._counts = {COUNT_ADMITTED: 0, COUNT_NOT_ADMITTED: 0, COUNT_PUBLISHED: 0}
        self._diagnostics: list[str] = []
        self._prepared = False
        self._started = False
        self._stopping = False
        self._closed = False

    @property
    def settings(self) -> OperatorSettings:
        return self._settings

    @property
    def core(self) -> OperatorCore:
        """The request core the listener hands every request to."""

        return self._core

    @property
    def diagnostics(self) -> tuple[str, ...]:
        return tuple(self._diagnostics)

    @property
    def listening(self) -> bool:
        return self._listener is not None

    def counts(self) -> Mapping[str, int]:
        """The core's press and refusal counts, what was published and admitted."""

        return {**self._core.counts(), **self._counts}

    # -- lifecycle hooks ---------------------------------------------------- #

    async def prepare(self) -> None:
        """Bind nothing: no listener opens before ``start_inputs``."""

        if self._closed:
            return
        self._prepared = True

    async def start_inputs(self) -> None:
        """After the readiness barrier: open the listener."""

        if not self._prepared or self._closed or self._stopping or self._started:
            return
        self._started = True
        listener = self._server_factory(self._settings.host, self._settings.port, self.dispatch)
        self._core.open()
        try:
            await listener.start()
        except asyncio.CancelledError:
            self._core.close()
            raise
        except Exception:
            self._core.close()
            self._diagnose("listener: could not open")
            raise OperatorModuleError(f"{MODULE_NAME} listener: could not open") from None
        self._listener = listener

    async def stop_inputs(self) -> None:
        """Close the listener: no press produces an event after this returns."""

        self._stopping = True
        await self._close_listener()

    async def close(self) -> None:
        if self._closed:
            return
        self._stopping = True
        await self._close_listener()
        self._closed = True

    async def _close_listener(self) -> None:
        self._core.close()
        listener, self._listener = self._listener, None
        if listener is not None:
            try:
                await listener.close()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - a failed close must not stop the shutdown
                self._diagnose("listener: close failed")
        await self._flush()

    # -- requests ------------------------------------------------------------- #

    async def dispatch(
        self, method: str, path: str, headers: Mapping[str, str], body: bytes
    ) -> tuple[int, str, str]:
        """One request: the core's answer, then the press and its decision's trace."""

        status, text = self._core.handle(method, path, headers, body)
        await self._flush()
        return status, text, content_type(text)

    def _submit(self, event: dict[str, Any]) -> None:
        """Decide one press, queue its publication, admit it when accepted
        into a shared scheduler; never raises."""

        if self._stopping or self._closed:
            self._counts[COUNT_NOT_ADMITTED] += 1
            return
        engine = self._triggers
        if engine is None:
            # Nothing decides, so nothing is admitted: fail closed.
            self._counts[COUNT_NOT_ADMITTED] += 1
            return
        try:
            decision = engine.evaluate(event)
        except Exception:  # noqa: BLE001 - an undecided press is never admitted
            # Still published, as twitch does: no decision is recorded for
            # it, so the brain's consumer refuses it — fail closed.
            self._diagnose("press: trigger evaluation failed")
            self._pending.append((event, None))
            self._counts[COUNT_NOT_ADMITTED] += 1
            return
        self._pending.append((event, decision))
        if not getattr(decision, "accepted", False):
            self._counts[COUNT_NOT_ADMITTED] += 1
            return
        if self._scheduler is None:
            # The brain owns the scheduler; its consumer admits the published
            # copy, so admitting it here as well would run it twice.
            return
        self._admit(self._scheduler, event)

    def _admit(self, scheduler: Any, event: Mapping[str, Any]) -> None:
        from core.admission import Work
        from core.contracts import SessionKey

        payload = event["payload"]
        try:
            result = scheduler.admit(
                SessionKey(
                    platform=payload["platform"],
                    channel_id=payload["channel_id"],
                    viewer_id=payload["author"]["id"],
                ),
                Work(payload=event, source_event_id=payload["message_id"], kind=WORK_KIND),
            )
        except Exception:  # noqa: BLE001 - a closed scheduler must not fail the request
            self._diagnose("press: admission failed")
            self._counts[COUNT_NOT_ADMITTED] += 1
            return
        if getattr(result, "accepted", False) is not True:
            # A refusal is the scheduler's own traced saturation decision.
            self._counts[COUNT_NOT_ADMITTED] += 1
            return
        self._counts[COUNT_ADMITTED] += 1

    async def _flush(self) -> None:
        """Publish each pending press, then its decision as a supervision fact."""

        while self._pending:
            event, decision = self._pending.popleft()
            await self._publish(event)
            if decision is not None:
                await self._trace_decision(decision)

    async def _publish(self, event: Mapping[str, Any]) -> None:
        """The one bus copy of a decided press (R6)."""

        publish = getattr(self._bus, "publish", None)
        if not callable(publish):
            self._diagnose("press: no bus to publish on")
            return
        try:
            await publish(event["type"], event["payload"], event["metadata"])
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a lost publication must not fail the request
            self._diagnose("press: publish failed")
            return
        self._counts[COUNT_PUBLISHED] += 1

    async def _trace_decision(self, decision: Any) -> None:
        from core.contracts import TRACE_INPUT_TRIGGER_ACCEPTED, TRACE_INPUT_TRIGGER_REJECTED
        from core.triggers import decision_trace_payload

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
            self._diagnose("press: trigger trace not published")

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
) -> OperatorModule:
    """Validate the settings and build the handle; nothing opens here."""

    del catalog  # Nothing is declared from the catalog.
    if not isinstance(settings, Mapping):
        raise OperatorModuleError(f"{MODULE_NAME} configuration: settings must be a mapping")
    diagnostics = validate_settings(settings)
    if diagnostics:
        raise OperatorModuleError(
            f"{MODULE_NAME} configuration: settings were refused "
            f"({len(diagnostics)} diagnostics)"
        )
    return OperatorModule(
        context,
        OperatorSettings.from_mapping(settings),
        server_factory=settings.get(_SEAM_SERVER_FACTORY, AiohttpListener),
    )


__all__ = [
    "ADMISSION_SERVICE_KIND",
    "ADMISSION_SERVICE_SCOPE",
    "AUTHOR_PREFIX",
    "AiohttpListener",
    "Button",
    "COUNT_ADMITTED",
    "COUNT_NOT_ADMITTED",
    "COUNT_PRESSED",
    "COUNT_PUBLISHED",
    "COUNT_REFUSED_CLOSED",
    "COUNT_REFUSED_COOLDOWN",
    "COUNT_REFUSED_HOST",
    "COUNT_REFUSED_MALFORMED",
    "COUNT_REFUSED_ORIGIN",
    "COUNT_REFUSED_ROUTE",
    "COUNT_REFUSED_SURFACE",
    "COUNT_REFUSED_TOKEN",
    "COUNT_REFUSED_UNKNOWN_BUTTON",
    "DEFAULT_COOLDOWN_SECONDS",
    "DEFAULT_HOST",
    "MAX_BODY_BYTES",
    "MODULE_NAME",
    "OperatorCore",
    "OperatorModule",
    "OperatorModuleError",
    "OperatorSettings",
    "PATH_OVERLAY",
    "PATH_PAGE",
    "PATH_PRESS",
    "PRESS_KIND",
    "REFUSAL_COUNTS",
    "RESPONSE_HEADERS",
    "SURFACES",
    "SURFACE_HOTKEY",
    "SURFACE_OVERLAY",
    "SURFACE_PAGE",
    "TOKEN_HEADER",
    "TOKEN_MIN_CHARS",
    "TOKEN_QUERY",
    "WORK_KIND",
    "accepted_hosts",
    "activate",
    "content_type",
    "press_event",
    "validate_settings",
]
