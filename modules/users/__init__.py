"""The ``users.read`` action over a bounded directory of observed authors (R5).

Every input publishes the chat it receives as one normalised
``channel.chat.message`` event whose payload names the ``platform``, the
``channel_id`` and the ``author`` — ``{id, display_name?, roles?,
roles_provenance?}``. This module listens to that event and keeps, per
``(platform, channel_id)``, a directory of the authors it saw: when each was
first and last observed on the injected clock, the display name the last
event carried, and the roles the platform attested. It makes that directory
readable by the brain as an action — a ``read`` on the ``chat`` scope of any
platform and any channel — and nothing more: it publishes nothing and never
alters the event it observes.

**What the directory is, and is not.** It lists who *spoke*, on this
process's clock, inside its bounds. It is never the audience: a lurker is
absent, an author whose last message is older than ``max_age_seconds`` is
dropped at read, and a channel holding more than ``max_users_per_channel``
authors evicts the one with the oldest ``last_seen`` (AC27). That is why
every page reports ``coverage.kind: "observed_authors"`` and
``coverage.complete: false``, always, together with ``retained_users`` and
the count of authors the channel ``evicted`` at its bound (R5). The
``freshness`` block states when the page was read and the window an author
stays listed for, so no partial list is ever presented as the audience
(design v2 §5).

**Roles are attested or absent (plan decision 5).** An author carries
``roles`` and ``roles_provenance`` only when the event that last attested
them carried ``author.roles`` as a list of strings **and**
``author.roles_provenance`` as a non-empty string; an untagged role attests
nothing and is not recorded. An author whose events never carried both has
no ``roles`` key at all — absent, not empty (AC28). A platform input
attests roles only from what its platform attached to the notification; a
notification attesting none leaves its author without them.

**Pagination.** Authors are ordered by ``last_seen`` descending, then
``user_id`` ascending. The ``cursor`` handed back in ``page.next_cursor`` is
opaque to the caller and encodes the sort key of the last author returned
(``platform/channel_id/last_seen/user_id``, base64), never an offset: an
author evicted at the boundary between two pages only shortens the next
page, and an author who spoke again since the first page moves *ahead* of
the cursor, so no author is ever returned twice through one cursor chain
(plan P15 risk). A cursor minted for another destination, or one that does
not decode, is ``error invalid_arguments``.

**Bounded state.** Beyond ``max_channels`` the least recently updated
channel directory is dropped whole. The handler that feeds the directory is
synchronous and never awaits — the bus runs its subscribers inline in the
publication chain — and it never raises: a malformed event is ignored, since
a subscriber that fails would fail the input's publication.

**Authorization.** Declaring the action authorizes nothing: without an
applicable rule the executor refuses the call ``not_authorized`` and the
provider is never entered (AC30). No platform name appears here: the
platform is the event's and the destination's, runtime values the directory
is keyed by.

Lifecycle: the manifest declares no role, so the handle takes part in
``prepare`` — subscribe to the chat event, bind the provider over
``*/*/chat`` and mark ready — and in ``close``, which withdraws readiness
and stops observing (R4). The bus keeps the subscription; the handler
ignores every event once closed.
"""

from __future__ import annotations

import base64
import binascii
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote

import yaml

from core.contracts import (
    PART_TYPE_TEXT,
    ActionObservation,
    ActionSpec,
    Destination,
)


MODULE_NAME = "users"

USERS_READ_ACTION = "users.read"
USERS_READ_PROVIDER = "users-directory"
"""Provider name in bindings and traces."""

CHAT_EVENT = "channel.chat.message"
"""The normalised chat event every input publishes; the directory's only feed."""

COVERAGE_KIND = "observed_authors"
"""What every page says it covers: the authors who spoke, never the audience."""

MANIFEST_PATH = Path(__file__).with_name("module.yaml")

#: The page-size bounds the manifest's ``argument_schema`` declares. The
#: executor enforces them before the provider is reached (``invalid_arguments``
#: for 0 and 101); the provider re-checks them so a handle invoked outside the
#: executor is refused on the same terms.
MIN_LIMIT = 1
MAX_LIMIT = 100

#: The reserved setting the entry point hands its accepted ``limits`` block
#: over in (``core.main.LIMITS_KEY``). Accepted, never read: this module owns
#: its own bounds.
_ACCEPTED_LIMITS_SETTING = "limits"

_COUNT = "count"
_SECONDS = "seconds"

#: The three bounds this module owns, with their kinds (R5, R6).
_BOUNDS: Mapping[str, str] = {
    "max_channels": _COUNT,
    "max_users_per_channel": _COUNT,
    "max_age_seconds": _SECONDS,
}

_ERROR_INVALID_ARGUMENTS = "invalid_arguments"
_ERROR_PROVIDER_CLOSED = "provider_closed"

_EMPTY_PAGE = "(no observed users)"

#: Field separator inside a cursor; each field is percent-escaped so an
#: identifier containing the separator still round-trips.
_CURSOR_SEPARATOR = "/"


class UsersModuleError(RuntimeError):
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

    The three bounds are required: ``max_channels`` and
    ``max_users_per_channel`` must be positive integers and
    ``max_age_seconds`` a finite positive number (R6). The reserved
    ``limits`` block is accepted and not inspected; any other key is refused
    by name. No configured value is echoed.
    """

    if not isinstance(settings, Mapping):
        return [_setting_diagnostic("settings", "must be a mapping")]
    diagnostics: list[str] = []

    for field_name in settings:
        if field_name not in _BOUNDS and field_name != _ACCEPTED_LIMITS_SETTING:
            diagnostics.append(
                _setting_diagnostic(str(field_name), "is not a setting this module declares")
            )

    for field_name, kind in _BOUNDS.items():
        reason = _limit_reason(settings.get(field_name), kind)
        if reason is not None:
            diagnostics.append(_setting_diagnostic(field_name, reason))
    return diagnostics


def _limit_reason(value: Any, kind: str) -> str | None:
    """Why *value* is not an acceptable bound of *kind*, or ``None`` if it is."""

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


@dataclass(frozen=True, slots=True)
class _Settings:
    """The three bounds, parsed once from accepted settings."""

    max_channels: int
    max_users_per_channel: int
    max_age_seconds: float

    @classmethod
    def from_mapping(cls, settings: Mapping[str, Any]) -> "_Settings":
        return cls(
            max_channels=int(settings["max_channels"]),
            max_users_per_channel=int(settings["max_users_per_channel"]),
            max_age_seconds=float(settings["max_age_seconds"]),
        )


# --------------------------------------------------------------------------- #
# The directory (R5, AC27, AC28)
# --------------------------------------------------------------------------- #


class _Author:
    """One observed author of one channel; mutated in place on each event."""

    __slots__ = (
        "display_name",
        "first_seen",
        "last_seen",
        "roles",
        "roles_provenance",
        "user_id",
    )

    def __init__(self, user_id: str, now: float) -> None:
        self.user_id = user_id
        self.display_name: str | None = None
        self.roles: tuple[str, ...] | None = None
        self.roles_provenance: str | None = None
        self.first_seen = now
        self.last_seen = now

    def sort_key(self) -> tuple[float, str]:
        """Most recently seen first, then identifier, ascending."""

        return (-self.last_seen, self.user_id)

    def as_result(self) -> dict[str, Any]:
        """The ``users[]`` item: ``roles`` only when attested (AC28)."""

        item: dict[str, Any] = {
            "user_id": self.user_id,
            # The identifier stands in when the platform carried no display
            # name, so the field is always a string a model can address.
            "display_name": self.display_name if self.display_name is not None else self.user_id,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
        }
        if self.roles is not None and self.roles_provenance is not None:
            item["roles"] = list(self.roles)
            item["roles_provenance"] = self.roles_provenance
        return item


class _Channel:
    """One ``(platform, channel_id)`` directory with its eviction count."""

    __slots__ = ("authors", "evicted", "updated_at")

    def __init__(self, now: float) -> None:
        self.authors: dict[str, _Author] = {}
        self.evicted = 0
        self.updated_at = now


@dataclass(frozen=True, slots=True)
class _Cursor:
    """The decoded sort key a page continues after."""

    platform: str
    channel_id: str
    last_seen: float
    user_id: str

    def encode(self) -> str:
        raw = _CURSOR_SEPARATOR.join(
            quote(field, safe="")
            for field in (self.platform, self.channel_id, repr(self.last_seen), self.user_id)
        )
        return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")

    @classmethod
    def decode(cls, value: Any) -> "_Cursor | None":
        """Decode *value*; ``None`` when it is not a cursor this module minted."""

        if not isinstance(value, str) or not value:
            return None
        try:
            raw = base64.urlsafe_b64decode(value.encode("ascii")).decode("utf-8")
        except (ValueError, binascii.Error, UnicodeError):
            return None
        fields = raw.split(_CURSOR_SEPARATOR)
        if len(fields) != 4:
            return None
        platform, channel_id, last_seen, user_id = (unquote(field) for field in fields)
        try:
            instant = float(last_seen)
        except ValueError:
            return None
        if not math.isfinite(instant) or not platform or not channel_id or not user_id:
            return None
        return cls(platform=platform, channel_id=channel_id, last_seen=instant, user_id=user_id)

    def sort_key(self) -> tuple[float, str]:
        return (-self.last_seen, self.user_id)


@dataclass(frozen=True, slots=True)
class Page:
    """One page of a channel's directory, as :meth:`UsersDirectory.page` returns it."""

    users: tuple[dict[str, Any], ...]
    has_more: bool
    next_cursor: str | None
    retained_users: int
    evicted: int


class UsersDirectory:
    """The bounded directory: observe events, page authors (R5).

    Pure and synchronous: every instant comes from the caller, no method
    awaits, and :meth:`observe` never raises on a malformed event — it
    returns whether the event was recorded. Bounds are applied at
    :meth:`observe` for counts (channels, authors per channel) and at
    :meth:`page` for age, as the plan states.
    """

    __slots__ = ("_channels", "_settings")

    def __init__(self, settings: _Settings) -> None:
        self._settings = settings
        self._channels: dict[tuple[str, str], _Channel] = {}

    # -- feed --------------------------------------------------------------- #

    def observe(self, event: Any, now: float) -> bool:
        """Record the author of one ``channel.chat.message`` event at *now*.

        Requires ``payload.platform`` and ``payload.channel_id`` to be
        non-empty strings and ``payload.author.id`` a non-empty string;
        anything else is ignored and ``False`` is returned. ``display_name``
        is taken when it is a non-empty string (the latest wins); ``roles``
        only when they are a list of strings tagged by a non-empty
        ``roles_provenance`` (decision 5) — an event without both leaves an
        earlier attestation in place and records none.
        """

        if not isinstance(event, Mapping):
            return False
        payload = event.get("payload")
        if not isinstance(payload, Mapping):
            return False
        platform = payload.get("platform")
        channel_id = payload.get("channel_id")
        author = payload.get("author")
        if not _is_text(platform) or not _is_text(channel_id) or not isinstance(author, Mapping):
            return False
        user_id = author.get("id")
        if not _is_text(user_id):
            return False

        instant = float(now)
        channel = self._channel_for(platform, channel_id, instant)
        record = channel.authors.get(user_id)
        if record is None:
            record = _Author(user_id, instant)
            channel.authors[user_id] = record
        else:
            record.last_seen = instant
        display_name = author.get("display_name")
        if _is_text(display_name):
            record.display_name = display_name
        roles = author.get("roles")
        provenance = author.get("roles_provenance")
        if _is_role_list(roles) and _is_text(provenance):
            record.roles = tuple(roles)
            record.roles_provenance = provenance
        self._bound_authors(channel)
        return True

    def _channel_for(self, platform: str, channel_id: str, now: float) -> _Channel:
        key = (platform, channel_id)
        channel = self._channels.get(key)
        if channel is None:
            channel = _Channel(now)
            self._channels[key] = channel
            self._bound_channels(keep=key)
        channel.updated_at = now
        return channel

    def _bound_authors(self, channel: _Channel) -> None:
        """Evict the oldest ``last_seen`` (then greatest id) beyond the bound."""

        excess = len(channel.authors) - self._settings.max_users_per_channel
        if excess <= 0:
            return
        # The last authors in page order are the first evicted, so a page
        # in flight only ever loses its tail.
        for record in sorted(channel.authors.values(), key=_Author.sort_key)[-excess:]:
            del channel.authors[record.user_id]
        channel.evicted += excess

    def _bound_channels(self, *, keep: tuple[str, str]) -> None:
        """Drop the least recently updated channels beyond ``max_channels``."""

        excess = len(self._channels) - self._settings.max_channels
        if excess <= 0:
            return
        candidates = sorted(
            (key for key in self._channels if key != keep),
            key=lambda key: self._channels[key].updated_at,
        )
        for key in candidates[:excess]:
            del self._channels[key]

    # -- read --------------------------------------------------------------- #

    def page(
        self,
        platform: str,
        channel_id: str,
        *,
        limit: int,
        cursor: _Cursor | None,
        now: float,
    ) -> Page:
        """One page of the channel's live authors after *cursor*, at *now*.

        Authors whose ``last_seen`` is older than ``max_age_seconds`` are
        dropped from the directory first, so ``retained_users`` counts what
        the page was cut from. An unknown channel pages as empty.
        """

        channel = self._channels.get((platform, channel_id))
        if channel is None:
            return Page(users=(), has_more=False, next_cursor=None, retained_users=0, evicted=0)
        self._drop_aged(channel, float(now))
        ordered = sorted(channel.authors.values(), key=_Author.sort_key)
        if cursor is not None:
            after = cursor.sort_key()
            ordered = [record for record in ordered if record.sort_key() > after]
        selected = ordered[:limit]
        has_more = len(ordered) > limit
        next_cursor = None
        if has_more:
            last = selected[-1]
            next_cursor = _Cursor(
                platform=platform,
                channel_id=channel_id,
                last_seen=last.last_seen,
                user_id=last.user_id,
            ).encode()
        return Page(
            users=tuple(record.as_result() for record in selected),
            has_more=has_more,
            next_cursor=next_cursor,
            retained_users=len(channel.authors),
            evicted=channel.evicted,
        )

    def _drop_aged(self, channel: _Channel, now: float) -> None:
        horizon = now - self._settings.max_age_seconds
        stale = [user_id for user_id, record in channel.authors.items() if record.last_seen < horizon]
        for user_id in stale:
            del channel.authors[user_id]

    # -- inspection --------------------------------------------------------- #

    def channels(self) -> tuple[tuple[str, str], ...]:
        """The retained channel keys, least recently updated first."""

        return tuple(sorted(self._channels, key=lambda key: self._channels[key].updated_at))

    def clear(self) -> None:
        self._channels.clear()


def _is_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value)


def _is_role_list(value: Any) -> bool:
    return isinstance(value, (list, tuple)) and all(isinstance(role, str) for role in value)


# --------------------------------------------------------------------------- #
# The page rendering fed back to the model (R4)
# --------------------------------------------------------------------------- #


def render_page(users: Sequence[Mapping[str, Any]], page: Mapping[str, Any]) -> str:
    """Render one page as the one ``text`` part of a read.

    One line per author — display name, identifier, first and last seen on
    the runtime clock, and the attested roles with their provenance when the
    author carries any — followed by one line stating whether more authors
    follow, so the model knows the page is a cut of the observed authors,
    never the audience. An empty page renders a fixed marker in place of
    the author lines.
    """

    lines = [
        f"{user['display_name']} <{user['user_id']}> first {float(user['first_seen']):.3f} "
        f"last {float(user['last_seen']):.3f}"
        + (
            f" roles {', '.join(user['roles']) or '(none)'} ({user['roles_provenance']})"
            if "roles" in user
            else ""
        )
        for user in users
    ]
    if not lines:
        lines.append(_EMPTY_PAGE)
    more = "more follow" if page["has_more"] else "no more"
    lines.append(f"page: {page['returned']} returned, {more}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# The handle
# --------------------------------------------------------------------------- #


class _UsersReadProvider:
    """The ``users.read`` provider the executor invokes (R5)."""

    __slots__ = ("_module",)

    name = USERS_READ_PROVIDER

    def __init__(self, module: "UsersModule") -> None:
        self._module = module

    async def invoke(self, invocation: Any) -> ActionObservation:
        return await self._module._invoke_users_read(invocation)


class UsersModule:
    """The v2 handle: observe chat at ``prepare``, page the directory."""

    def __init__(self, context: Any, settings: _Settings) -> None:
        self._actions = context.actions
        self._bus = context.bus
        self._clock = context.clock
        self._settings = settings
        self._directory = UsersDirectory(settings)
        self._provider = _UsersReadProvider(self)
        self._prepared = False
        self._closed = False

    @property
    def directory(self) -> UsersDirectory:
        """The directory, for inspection."""

        return self._directory

    # -- lifecycle hooks (R4) ----------------------------------------------- #

    async def prepare(self) -> None:
        """Subscribe, bind the provider over the declared destinations, mark ready."""

        if self._prepared or self._closed:
            return
        spec = _declared_users_read_spec()
        try:
            self._bus.subscribe(CHAT_EVENT, self.observe_event)
            self._actions.register(spec, self._provider, provider_name=USERS_READ_PROVIDER)
        except Exception:
            raise UsersModuleError(f"{MODULE_NAME} prepare: action binding failed") from None
        self._actions.mark_ready()
        self._prepared = True

    async def close(self) -> None:
        """Withdraw readiness and stop observing; the directory is released."""

        if self._closed:
            return
        self._closed = True
        self._directory.clear()
        if self._prepared:
            self._actions.mark_not_ready()

    # -- the feed (R5) ------------------------------------------------------ #

    def observe_event(self, event: Mapping[str, Any]) -> None:
        """Record the author of one chat event; synchronous, never raises.

        Returns ``None`` so the event is never replaced or stopped: this
        module is an observer of the input path, not a middleware. A
        malformed event is ignored — a subscriber that raised would fail the
        input's own publication — and nothing is recorded once closed.
        """

        if self._closed:
            return None
        try:
            self._directory.observe(event, self._clock())
        except Exception:  # noqa: BLE001 - the publication chain must not fail here
            return None
        return None

    # -- the read (R5, AC27, AC28) ------------------------------------------ #

    async def _invoke_users_read(self, invocation: Any) -> ActionObservation:
        """Serve one ``users.read`` call for the executor."""

        # A read reaches nothing outside the process: an interruption is a
        # certain ``timeout``/``cancelled``, never an uncertain effect.
        invocation.mark_not_emitted()
        call = invocation.call
        destination = call.destination
        provenance = {
            "provider": USERS_READ_PROVIDER,
            "platform": destination.platform,
            "channel_id": destination.channel_id,
            "route": USERS_READ_ACTION,
        }

        def failure(code: str, message: str) -> ActionObservation:
            return ActionObservation(
                status="error",
                provenance=provenance,
                error={"code": code, "message": message, "retryable": False},
            )

        if self._closed:
            return failure(_ERROR_PROVIDER_CLOSED, f"{MODULE_NAME} read: closed")
        arguments = call.arguments
        limit = arguments.get("limit")
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not MIN_LIMIT <= limit <= MAX_LIMIT
        ):
            return failure(
                _ERROR_INVALID_ARGUMENTS,
                f"limit must be an integer between {MIN_LIMIT} and {MAX_LIMIT}",
            )
        cursor: _Cursor | None = None
        if arguments.get("cursor") is not None:
            cursor = _Cursor.decode(arguments["cursor"])
            if cursor is None:
                return failure(_ERROR_INVALID_ARGUMENTS, "cursor is not one this action issued")
            if (cursor.platform, cursor.channel_id) != (
                destination.platform,
                destination.channel_id,
            ):
                return failure(
                    _ERROR_INVALID_ARGUMENTS, "cursor was issued for another destination"
                )

        observed_at = float(self._clock())
        page = self._directory.page(
            destination.platform,
            destination.channel_id,
            limit=limit,
            cursor=cursor,
            now=observed_at,
        )
        users = list(page.users)
        page_block = {
            "returned": len(users),
            "has_more": page.has_more,
            "next_cursor": page.next_cursor,
        }
        result = {
            "users": users,
            "page": page_block,
            "freshness": {
                "observed_at": observed_at,
                "window_seconds": self._settings.max_age_seconds,
            },
            "coverage": {
                "kind": COVERAGE_KIND,
                # An observed-author list is never the audience.
                "complete": False,
                "retained_users": page.retained_users,
                "evicted": page.evicted,
            },
        }
        return ActionObservation(
            status="success",
            provenance=provenance,
            result=result,
            parts=({"type": PART_TYPE_TEXT, "text": render_page(users, page_block)},),
        )


# --------------------------------------------------------------------------- #
# Activation (R7)
# --------------------------------------------------------------------------- #


async def activate(
    context: Any,
    settings: Mapping[str, Any],
    catalog: Mapping[str, Mapping[str, Any]],
) -> UsersModule:
    """Build the handle from the scoped runtime context (R7).

    *context* is the module-scoped view of the versioned runtime: the bus
    the chat event is observed on, the action facade the provider is
    registered on at ``prepare`` and the clock every instant is stamped
    with. Settings are checked through the same hook the loader ran, so a
    handle built outside the loader is refused on the same terms.
    """

    del catalog  # Capabilities are declared by the colocated manifest.
    _require_runtime_surfaces(context)
    if not isinstance(settings, Mapping):
        raise UsersModuleError(f"{MODULE_NAME} configuration: settings must be a mapping")
    diagnostics = validate_settings(settings)
    if diagnostics:
        raise UsersModuleError(
            f"{MODULE_NAME} configuration: settings were refused "
            f"({len(diagnostics)} diagnostics)"
        )
    return UsersModule(context, _Settings.from_mapping(settings))


def _require_runtime_surfaces(context: Any) -> None:
    """Refuse a context lacking the actions facade, a bus or a clock.

    Duck-typed so a harness may inject fakes, while a mis-wired runtime is
    refused at activation with one diagnostic rather than at ``prepare``.
    """

    actions = getattr(context, "actions", None)
    bus = getattr(context, "bus", None)
    clock = getattr(context, "clock", None)
    if (
        actions is None
        or not all(
            callable(getattr(actions, method, None))
            for method in ("register", "mark_ready", "mark_not_ready")
        )
        or bus is None
        or not callable(getattr(bus, "subscribe", None))
        or not callable(clock)
    ):
        raise UsersModuleError(f"{MODULE_NAME} activation: runtime context is invalid")


def _declared_users_read_spec() -> ActionSpec:
    """Build the ``users.read`` contract from the colocated manifest (R5).

    The spec registered at ``prepare`` must equal the one the loader declared
    at discovery, field for field: reading the same file keeps the two from
    drifting, and the registry refuses a redeclaration that differs.
    """

    try:
        manifest = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))
        entry = next(
            item
            for item in manifest["actions"]
            if isinstance(item, Mapping) and item.get("name") == USERS_READ_ACTION
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
        raise UsersModuleError(
            f"{MODULE_NAME} prepare: manifest declaration of {USERS_READ_ACTION!r} is invalid"
        ) from None


__all__ = [
    "CHAT_EVENT",
    "COVERAGE_KIND",
    "MANIFEST_PATH",
    "MAX_LIMIT",
    "MIN_LIMIT",
    "MODULE_NAME",
    "USERS_READ_ACTION",
    "USERS_READ_PROVIDER",
    "Page",
    "UsersDirectory",
    "UsersModule",
    "UsersModuleError",
    "activate",
    "render_page",
    "validate_settings",
]
