"""Twitch EventSub chat input and Helix chat-send provider on the v2 runtime.

The manifest beside this package declares ``manifest_version: 2``, so the
loader hands :func:`activate` a scoped runtime context rather than the bus,
and the handle it returns is driven by the phase coordinator through the
hooks of the ``input`` role it declares (R4, R7):

``validate_settings``  The module-owned hook the manifest names. It reports
                       one diagnostic per offending field, naming module and
                       field and never the configured value (R7, AC24).
``activate``           Parses the settings and creates the HTTP session. It
                       opens no transport and produces no input.
``prepare``            Binds the ``chat.write`` provider, validates the
                       credential and registers the compatibility consumer.
                       Still no input.
``start_inputs``       After the readiness barrier: opens the EventSub socket,
                       subscribes and starts the supervised receiver.
``stop_inputs``        Cuts the source. Accepted publications keep running
                       and the send transport stays open for them.
``drain``              Lets accepted publications finish inside the budget
                       and cancels the rest.
``close``              Releases every owned transport exactly once. It is
                       complete on its own, so a handle that never reached
                       ``stop_inputs`` — a failed startup — is still released.

**Reception** (R1, R3, R6). Every platform notification is normalised at this
boundary exactly once into the schema-version-2 chat event — ``payload.platform``,
``payload.channel_id``, ``payload.author.id``, ``payload.message_id`` and
``payload.text`` under ``metadata.source = "twitch"`` — and then, in this order:
the bounded dedup window of the injected trigger engine is consulted, so a
redelivery still inside the window produces nothing at all; the chat context is
fed, whatever is decided afterwards; the trigger engine decides; the normalised
event is published; the decision is traced as ``input.trigger.accepted`` or
``input.trigger.rejected``; an accepted event is admitted to the scheduler. The
handler then returns — the model runs on a scheduler worker, never here. The
publish lock covers normalisation and dedup only; it is released before the
publication, the trace and the admission. A notification carrying no trusted
viewer identity is never published, fed or admitted: it is one traced
rejection (AC12). The dedup guarantee covers the retained window only — a
repetition arriving after eviction is reprocessed (AC22). The bus copy of the
event is the normalised event itself, decided either way: the consumer of
``channel.chat.message`` consults the decision this input recorded in the
shared trigger engine before it admits anything, so nothing the trigger
refused can reach a model through the bus (R1, AC4).

**Delivery** (R5, R8). One send service serves two routes: the ``chat.write``
action, bound at preparation to the configured channel and invoked by the
executor, and the compatibility ``channel.chat.send`` bus route. Exactly one
route is used per invocation. The service reports ``success`` only on the
platform's own confirmation and ``external_unknown`` when the request left and
the outcome is not known; ``channel.chat.sent`` is emitted only for a confirmed
send, through ``record_and_emit``, so the confirmed-send fact is written into
:class:`SendRecord` before the trace is published and a failed publication
neither retries nor repeats the send (AC28). The trace is handed to the loop
rather than awaited by the send: a confirmed outcome is returned as soon as it
is recorded, on either route, and never depends on how long a trace subscriber
takes — it cannot spend the executor's budget and turn a confirmed send into
``external_unknown``, nor hold the compatibility route's caller. On the action
route the hand-over runs through the invocation's completion hook, once the
executor has published its own ``action.completed``, so one run's audit reads
``action.started``, ``action.completed``, ``channel.chat.sent`` and then
``brain.run.completed`` — AC27's order — and the fact still exists only once
the transport confirmed, since the record precedes the hook. Detached, but
not unbounded (R6): at most ``DEFAULT_MAX_PENDING_SENT_TRACES`` traces may be
outstanding at once, a trace offered past that cap is dropped and counted — in
:class:`SendRecord` and as a lost trace — with the confirmed-send record left
exactly as it was, and ``close`` waits for the outstanding traces inside
``DEFAULT_SENT_TRACE_CLOSE_SECONDS`` only, cancelling and counting the rest, so
a subscriber that never returns can neither grow that set nor hold shutdown.

**Polls** (R7). ``activate`` publishes a poll service under ``(poll, twitch)``
in the runtime's service registry — only when the context carries one
(``context.services.available``); a context built without a registry
publishes nothing and activates exactly as before. The service issues one
Helix request per call over the send session and credential, and leaves
reconciliation to its consumer; it adds no action, and ``chat.write`` and the
chat source are unchanged.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import math
import sys
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, fields
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from core.admission import Work
from core.context import ChatEntry
from core.contracts import (
    COUNTER_LOST_TRACES,
    TRACE_CHANNEL_CHAT_SENT,
    TRACE_INPUT_TRIGGER_ACCEPTED,
    TRACE_INPUT_TRIGGER_REJECTED,
    ActionObservation,
    ActionSpec,
    Destination,
    SessionKey,
)
from core.triggers import NORMALIZED_SCHEMA_VERSION, TriggerContext, TrustedClaim

try:  # Keep the module importable for transport-injected contract tests.
    import aiohttp
except ModuleNotFoundError:  # pragma: no cover - production installs dependencies
    aiohttp = None  # type: ignore[assignment]


EVENTSUB_URL = "wss://eventsub.wss.twitch.tv/ws"
EVENTSUB_SUBSCRIPTIONS_URL = (
    "https://api.twitch.tv/helix/eventsub/subscriptions"
)
HELIX_CHAT_URL = "https://api.twitch.tv/helix/chat/messages"
HELIX_POLLS_URL = "https://api.twitch.tv/helix/polls"
TOKEN_VALIDATION_URL = "https://id.twitch.tv/oauth2/validate"

# Module-level seams make the network client and retry clock replaceable without
# changing the loader's three-argument activation contract.
def _default_session_factory() -> Any:
    if aiohttp is None:
        raise RuntimeError("aiohttp is not installed")
    return aiohttp.ClientSession()


SESSION_FACTORY: Callable[[], Any] = _default_session_factory
RETRY_DELAY: Callable[[float], Awaitable[None]] = asyncio.sleep

MODULE_NAME = "twitch"

PLATFORM = "twitch"
"""``payload.platform`` of every event this input normalises (R3).

A stable platform identifier, deliberately a constant of its own: it is never
derived from the module name, which is a deployment label.
"""

CHAT_WRITE_ACTION = "chat.write"
"""The action contract the manifest declares and :meth:`TwitchModule.prepare` binds."""

MANIFEST_PATH = Path(__file__).with_name("module.yaml")
"""The colocated manifest: the one declaration of ``chat.write`` this module serves."""

CHAT_WRITE_PROVIDER = "twitch-helix-chat"
"""Provider name in bindings and traces; what an ambiguity diagnostic prints."""

_CHAT_EVENT = "channel.chat.message"
_CHAT_SEND_EVENT = "channel.chat.send"
_CHAT_SCOPE = "chat"
_ROUTE_ACTION = CHAT_WRITE_ACTION
_ROUTE_COMPATIBILITY = _CHAT_SEND_EVENT
_CHAT_SEND_TIMEOUT_SECONDS = 10.0
_NON_RETRYABLE_CLOSE_CODES = {4001, 4003}

DEFAULT_MAX_PENDING_SENT_TRACES = 64
"""How many ``channel.chat.sent`` publications may be outstanding at once (R6).

A confirmed send hands its trace to the loop rather than awaiting it, so a
subscriber publishing slower than sends are confirmed would otherwise
accumulate one pending task per send without limit. Past this many the trace
is dropped and counted (``SendRecord.dropped_traces``, ``lost_traces``); the
send itself stays confirmed and recorded. Overridable per activation through
the ``_max_pending_sent_traces`` seam, validated finite at activation.
"""

DEFAULT_SENT_TRACE_CLOSE_SECONDS = 5.0
"""How long ``close`` waits for the outstanding sent traces before cancelling them.

A shutdown must be bounded by the module too, not only by the coordinator's
global deadline: a subscriber that never returns would otherwise hold the send
transport open for as long as it pleases. Traces cancelled at this budget are
counted exactly like dropped ones. Overridable through the
``_sent_trace_close_seconds`` seam, validated finite at activation.
"""
_STABLE_CONNECTION_SECONDS = 10.0

POLL_SERVICE_KIND = "poll"
"""The service kind this module publishes its poll service under (R7).

The core defines no kind: the name is agreed between the publishing platform
and the consuming module, the registry never interprets it.
"""

# EventSub badge set identifiers mapped to the trusted claim they attest. Only
# a platform-attested badge becomes a claim; nothing in the message body can
# (R1, AC4). ``founder`` is a subscriber badge variant.
_BADGE_CLAIMS: Mapping[str, str] = {
    "broadcaster": "broadcaster",
    "moderator": "moderator",
    "subscriber": "subscription",
    "founder": "subscription",
    "vip": "vip",
}
_BADGE_PROVENANCE = "eventsub.badges"
_BROADCASTER_PROVENANCE = "eventsub.broadcaster_user_id"

# Error codes the send service normalises its failures to (R5).
_ERROR_INVALID_ARGUMENTS = "invalid_arguments"
_ERROR_UNSUPPORTED_DESTINATION = "unsupported_destination"
_ERROR_PROVIDER_CLOSED = "provider_closed"
_ERROR_PLATFORM_REJECTED = "platform_rejected"
_ERROR_TRANSPORT_FAILED = "transport_failed"
_ERROR_TIMED_OUT = "timed_out"
_ERROR_MALFORMED_RESPONSE = "malformed_response"

_STATUS_SUCCESS = "success"
_STATUS_ERROR = "error"
_STATUS_EXTERNAL_UNKNOWN = "external_unknown"


class TwitchModuleError(RuntimeError):
    """A Twitch operation failure whose text is safe to surface."""


# The business settings this module owns (R7). ``companion_name`` is required
# here because the manifest's default trigger policy refers to it; the trigger
# registry, not this module, reads it.
_REQUIRED_SETTINGS: tuple[str, ...] = (
    "client_id",
    "client_secret",
    "access_token",
    "broadcaster_id",
    "bot_user_id",
    "companion_name",
)


def validate_settings(settings: Any) -> list[str]:
    """Check this module's settings; return one diagnostic per offending field.

    This is the hook ``settings_validator`` in the manifest names. The loader
    runs it for every enabled module before any of them is activated (R7).
    Each diagnostic names the module and the field and nothing else: a
    rejected value may be a credential, so no value is ever echoed (AC24).
    An empty list means the settings are accepted.
    """

    if not isinstance(settings, Mapping):
        return [_setting_diagnostic("settings", "must be a mapping")]
    diagnostics: list[str] = []
    for name in _REQUIRED_SETTINGS:
        value = settings.get(name)
        if not isinstance(value, str) or not value.strip():
            diagnostics.append(_setting_diagnostic(name, "must be a non-empty string"))
    return diagnostics


def _setting_diagnostic(field_name: str, reason: str) -> str:
    return f"module {MODULE_NAME!r}: field {field_name!r}: {reason}"


@dataclass(frozen=True, slots=True)
class _ConsumeResult:
    action: str
    reconnect_url: str | None = None
    diagnostic: str | None = None


@dataclass(frozen=True, slots=True)
class _Notification:
    """One platform notification, normalised exactly once at the boundary (R3).

    ``author_id`` is ``None`` when the platform supplied no trusted viewer
    identity; such a notification is decided (and refused) by the trigger
    engine's authenticity invariant and is never published, fed or admitted.
    ``claims`` are the platform-attested facts about the author, each tagged
    with its provenance, and are the only context a role predicate may read.
    """

    channel_id: str
    author_id: str | None
    author_name: str | None
    message_id: str
    text: str
    claims: tuple[TrustedClaim, ...]

    def event(self) -> dict[str, Any]:
        """The schema-version-2 bus event this notification normalises to."""

        author: dict[str, Any] = {}
        if self.author_id is not None:
            author["id"] = self.author_id
        if self.author_name is not None:
            author["display_name"] = self.author_name
        payload: dict[str, Any] = {
            "platform": PLATFORM,
            "channel_id": self.channel_id,
            "author": author,
            "message_id": self.message_id,
            "text": self.text,
        }
        return {
            "type": _CHAT_EVENT,
            "payload": payload,
            "metadata": {
                "source": MODULE_NAME,
                "schema_version": NORMALIZED_SCHEMA_VERSION,
            },
        }


@dataclass(slots=True)
class SendRecord:
    """The send service's own memory of what it emitted and what was confirmed.

    This is the record ``record_and_emit`` writes *before* ``channel.chat.sent``
    is published (R8): a lost trace leaves ``confirmed`` incremented and the
    transport untouched. ``dropped_traces`` counts the confirmed sends whose
    trace was never published because the pending set was saturated or
    because ``close`` ran out of budget waiting for it (R6); it is written
    outside the saturated path, so it is readable however held the subscriber
    is, and every such drop is also counted in the runtime's ``lost_traces``.
    ``snapshot`` keeps the send outcomes only — the delivery facts the audit
    and the pipeline read — so the drop count is read from the field. Read
    the record through :attr:`TwitchModule.send_record`.
    """

    emitted: int = 0
    confirmed: int = 0
    failed: int = 0
    unknown: int = 0
    dropped_traces: int = 0
    last_message_id: str | None = None

    def snapshot(self) -> dict[str, Any]:
        return {
            "emitted": self.emitted,
            "confirmed": self.confirmed,
            "failed": self.failed,
            "unknown": self.unknown,
            "last_message_id": self.last_message_id,
        }


@dataclass(frozen=True, slots=True)
class _SendOutcome:
    """What one request to the send service ended as, on either route."""

    status: str
    message_id: str | None = None
    code: str | None = None
    reason: str | None = None


class _ChatWriteProvider:
    """The ``chat.write`` provider the executor invokes (R5).

    A thin handle over the module's send service: the module owns the
    transport, the record and the lifecycle, so the provider holds no state of
    its own and reports through the module's single send path.
    """

    __slots__ = ("_module",)

    name = CHAT_WRITE_PROVIDER

    def __init__(self, module: "TwitchModule") -> None:
        self._module = module

    async def invoke(self, invocation: Any) -> ActionObservation:
        return await self._module._invoke_chat_write(invocation)


class PollServiceError(Exception):
    """Base of the poll service's failures; the text never carries a body."""


class PollTransportError(PollServiceError):
    """The transport failed; ``sent`` says whether the request had left.

    ``sent=False`` only when the transport proves nothing left — the
    connection was never established (``aiohttp.ClientConnectorError``) or the
    handle was already closed. Every other failure, timeouts and dropped
    connections included, may have reached the platform: ``sent=True``, the
    rule ``chat.write`` applies through ``mark_emitted`` (R7).
    """

    def __init__(self, sent: bool) -> None:
        super().__init__("poll request lost" if sent else "poll request not sent")
        self.sent = sent


class PollStatusError(PollServiceError):
    """A non-2xx answer: ``status`` and a sanitised ``message``, never the body."""

    def __init__(self, status: int, message: str = "") -> None:
        super().__init__(message or f"poll request failed with status {status}")
        self.status = status
        self.message = message or f"status {status}"


class PollMalformedAnswer(PollServiceError):
    """A 2xx answer whose body is not a poll; the request did leave."""

    sent = True


class _TwitchPollService:
    """The poll service this module publishes under ``(poll, twitch)`` (R7).

    One Helix request per call, over the module's own HTTP session and
    credential (``_helix_headers``): ``create`` is one POST to
    :data:`HELIX_POLLS_URL`, ``get`` one GET on it for the channel. Nothing is
    retried here — the consumer owns reconciliation. Failures are classified
    by certainty (:class:`PollTransportError` ``sent``), refusals carry the
    status code and a message built here (:class:`PollStatusError`), and a
    2xx that is not a poll is :class:`PollMalformedAnswer`. Times are epoch
    seconds parsed from the platform's RFC 3339 stamps; ``state`` is the
    platform status in lower case (``active`` for a running poll).
    """

    __slots__ = ("_module",)

    def __init__(self, module: "TwitchModule") -> None:
        self._module = module

    async def create(
        self,
        channel_id: str,
        question: str,
        options: Any,
        duration_seconds: int,
    ) -> dict[str, Any]:
        body = {
            "broadcaster_id": channel_id,
            "title": question,
            "choices": [{"title": option} for option in options],
            "duration": duration_seconds,
        }
        _, answer = await self._request(
            "create",
            lambda session: session.post(
                HELIX_POLLS_URL, headers=self._module._helix_headers(), json=body
            ),
        )
        data = answer.get("data") if isinstance(answer, Mapping) else None
        entry = data[0] if isinstance(data, list) and data else None
        poll = _parse_poll(entry)
        if poll is None:
            self._module._diagnose("twitch poll create: malformed response")
            raise PollMalformedAnswer("twitch poll create: malformed response")
        return poll

    async def get(self, channel_id: str) -> list[dict[str, Any]]:
        _, answer = await self._request(
            "get",
            lambda session: session.get(
                HELIX_POLLS_URL,
                headers=self._module._helix_headers(),
                params={"broadcaster_id": channel_id},
            ),
        )
        data = answer.get("data") if isinstance(answer, Mapping) else None
        polls = [_parse_poll(entry) for entry in data] if isinstance(data, list) else None
        if polls is None or any(poll is None for poll in polls):
            self._module._diagnose("twitch poll get: malformed response")
            raise PollMalformedAnswer("twitch poll get: malformed response")
        return [
            {key: poll[key] for key in ("poll_id", "question", "options", "started_at", "state")}
            for poll in polls
            if poll is not None
        ]

    async def _request(
        self, operation: str, send: Callable[[Any], Any]
    ) -> tuple[int, Any]:
        module = self._module
        if module._closed:
            raise PollTransportError(sent=False)
        try:
            response = await _resolve(send(module._session))
        except asyncio.CancelledError:
            raise
        except Exception as failure:
            sent = not _never_sent(failure)
            module._diagnose(
                f"twitch poll {operation}: "
                + ("request lost" if sent else "request not sent")
            )
            raise PollTransportError(sent=sent) from None
        try:
            status, answer = await _read_response(response)
        except asyncio.CancelledError:
            raise
        except Exception:
            module._diagnose(f"twitch poll {operation}: answer lost")
            raise PollTransportError(sent=True) from None
        if status is None:
            module._diagnose(f"twitch poll {operation}: malformed response")
            raise PollMalformedAnswer(f"twitch poll {operation}: malformed response")
        if not 200 <= status < 300:
            message = f"twitch poll {operation}: rejected (status {status})"
            module._diagnose(message)
            raise PollStatusError(status, message)
        return status, answer


def _never_sent(failure: BaseException) -> bool:
    """Whether *failure* proves the request never left (R7).

    Only a connection that was never established does; anything later —
    a server disconnect, a timeout, a reset — may follow a delivered request.
    """

    connector_error = getattr(aiohttp, "ClientConnectorError", None)
    return connector_error is not None and isinstance(failure, connector_error)


def _parse_poll(entry: Any) -> dict[str, Any] | None:
    """One Helix poll object as the service's poll, or ``None`` when malformed."""

    if not isinstance(entry, Mapping):
        return None
    poll_id = entry.get("id")
    title = entry.get("title")
    choices = entry.get("choices")
    state = entry.get("status")
    duration = entry.get("duration")
    started_at = _epoch_seconds(entry.get("started_at"))
    if (
        not isinstance(poll_id, str)
        or not poll_id.strip()
        or not isinstance(title, str)
        or not isinstance(choices, list)
        or not isinstance(state, str)
        or not state
        or isinstance(duration, bool)
        or not isinstance(duration, int)
        or started_at is None
    ):
        return None
    options: list[str] = []
    for choice in choices:
        option = choice.get("title") if isinstance(choice, Mapping) else None
        if not isinstance(option, str):
            return None
        options.append(option)
    return {
        "poll_id": poll_id,
        "question": title,
        "options": options,
        "started_at": started_at,
        "ends_at": started_at + duration,
        "state": state.lower(),
    }


def _epoch_seconds(stamp: Any) -> float | None:
    if not isinstance(stamp, str):
        return None
    try:
        parsed = datetime.fromisoformat(stamp)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.timestamp()


@dataclass(frozen=True, repr=False, slots=True)
class _Settings:
    client_id: str
    client_secret: str
    access_token: str
    broadcaster_id: str
    bot_user_id: str

    @classmethod
    def from_mapping(cls, settings: Mapping[str, Any]) -> "_Settings":
        """Parse accepted settings; the first diagnostic of a refusal is raised.

        The same hook the loader ran decides here, so a handle built outside
        the loader is refused on the same terms and with the same value-free
        diagnostic.
        """

        diagnostics = validate_settings(settings)
        if diagnostics:
            raise TwitchModuleError(diagnostics[0])
        return cls(**{field.name: settings[field.name] for field in fields(cls)})


class TwitchModule:
    """The v2 handle: one HTTP session, one supervised receiver, phase hooks.

    Every hook is idempotent and safe out of order — ``close`` after a failed
    ``prepare``, ``stop_inputs`` before any ``start_inputs`` — because the
    coordinator unwinds a failed startup through the ordinary shutdown
    sequence (R4). Dedupe of platform redeliveries is the trigger engine's
    bounded window, shared across socket replacements (R6).
    """

    def __init__(
        self,
        context: Any,
        settings: _Settings,
        session: Any,
        reporter: Callable[[str], None],
        retry_delay: Callable[[float], Awaitable[None]],
        *,
        max_pending_sent_traces: int = DEFAULT_MAX_PENDING_SENT_TRACES,
        sent_trace_close_seconds: float = DEFAULT_SENT_TRACE_CLOSE_SECONDS,
    ) -> None:
        runtime = _runtime_surfaces(context)
        self._max_pending_sent_traces = _validate_count_limit(
            max_pending_sent_traces, "_max_pending_sent_traces"
        )
        self._sent_trace_close_seconds = _validate_duration_limit(
            sent_trace_close_seconds, "_sent_trace_close_seconds"
        )
        self._bus = runtime.bus
        self._tasks = runtime.tasks
        self._actions = runtime.actions
        self._supervision = runtime.supervision
        self._triggers = runtime.triggers
        self._chat = runtime.chat
        self._scheduler = runtime.scheduler
        self._settings = settings
        self._session = session
        self._reporter = reporter
        self._retry_delay = retry_delay
        self._provider = _ChatWriteProvider(self)
        self._poll_service = _TwitchPollService(self)
        self._send_record = SendRecord()
        self._bound = False
        # Covers normalisation and the dedup decision only (R2): it is released
        # before the publication, the trace and the admission.
        self._publish_lock = asyncio.Lock()
        self._websocket: Any | None = None
        self._receive_task: asyncio.Task[None] | None = None
        self._handoff_tasks: set[asyncio.Task[None]] = set()
        self._publication_tasks: set[asyncio.Task[None]] = set()
        self._prepared = False
        self._stopping = False
        self._reception_lock = asyncio.Lock()
        self._active_send_tasks: set[asyncio.Task[Any]] = set()
        # ``channel.chat.sent`` publications handed to the loop by a confirmed
        # send: at most ``max_pending_sent_traces`` at once, awaited by
        # ``close`` inside its own budget (R6).
        self._sent_traces: set[asyncio.Task[None]] = set()
        # Publications that resisted their cancellation at ``close`` and that
        # the supervised registry could not take over: still owned here, so
        # none is left to the collector unobserved (R4).
        self._abandoned_sent_traces: list[asyncio.Task[None]] = []
        self._closed = False
        self._close_lock = asyncio.Lock()

    @property
    def poll_service(self) -> _TwitchPollService:
        """The poll service ``activate`` publishes under ``(poll, twitch)`` (R7)."""

        return self._poll_service

    @property
    def send_record(self) -> SendRecord:
        """The send service's own record; see :class:`SendRecord`."""

        return self._send_record

    # -- startup phases ---------------------------------------------------- #

    async def prepare(self) -> None:
        """Bind the provider, validate the credential, register the consumer.

        Readiness-critical and free of input. The ``chat.write`` binding comes
        first: an ambiguous binding fails preparation before any request is
        made (R5, AC16). A rejected token then stops startup here, before the
        barrier, and the ``channel.chat.send`` consumer is routed before any
        producer may publish into it. Only once all of that holds is the
        module marked ready for the executor.
        """

        if self._prepared or self._closed:
            return
        self._bind_chat_write()
        await self._authenticate()
        self._bus.subscribe(_CHAT_SEND_EVENT, self.handle_chat_send)
        self._actions.mark_ready()
        self._prepared = True

    def _bind_chat_write(self) -> None:
        """Register this module's provider under the declared ``chat.write``.

        The contract is read from the colocated manifest — the same
        declaration the loader recorded in the discovered view, so the
        registration is a no-op redeclaration there and a refused one if the
        two ever differed. The provider is bound over the one concrete
        destination it can serve, the configured channel, so a call for any
        other channel resolves no provider rather than reaching a transport
        that would send elsewhere. An ambiguous binding fails here, before
        any request is made (R5, AC16).
        """

        if self._bound:
            return
        spec = _declared_chat_write_spec()
        destination = Destination(
            platform=PLATFORM,
            channel_id=self._settings.broadcaster_id,
            scope=_CHAT_SCOPE,
        )
        try:
            self._actions.register(
                spec,
                self._provider,
                destinations=destination,
                provider_name=CHAT_WRITE_PROVIDER,
            )
        except Exception as exc:
            # Names the action and the competing providers; carries no value
            # from the settings beyond the channel identifier.
            self._diagnose(f"twitch prepare: {exc}")
            raise TwitchModuleError("twitch prepare: action binding failed") from None
        self._bound = True

    async def start_inputs(self) -> None:
        """Open the EventSub source. Only the coordinator, past the barrier, calls this."""

        if self._receive_task is not None or self._stopping or self._closed:
            return
        if not self._prepared:
            raise TwitchModuleError("twitch lifecycle: start_inputs requires prepare")

        websocket = await self._connect(EVENTSUB_URL)
        try:
            session_id = await self._receive_welcome(websocket)
            await self._subscribe(session_id)
            self._websocket = websocket
            # Owned by the supervised registry: its failure is observed under
            # this module's name and shutdown never orphans it.
            self._receive_task = self._tasks.spawn(
                self._receive_forever(websocket), name="twitch-eventsub-receiver"
            )
        except BaseException:
            self._websocket = None
            await _close_websocket(websocket)
            raise

    # -- shutdown phases --------------------------------------------------- #

    async def stop_inputs(self) -> None:
        """Cut the source: stop reception and close the EventSub socket.

        Accepted publications keep running — they finish in :meth:`drain` —
        and the send transport stays open until :meth:`close`, so a reply to
        the last accepted message can still be delivered.
        """

        await self._stop_reception()

    async def drain(self, deadline_seconds: float) -> None:
        """Let accepted publications finish inside the budget; cancel the rest.

        A publication is the model/send/audit chain of one accepted message.
        On the coordinator's own cancellation the pending ones are cancelled
        explicitly rather than left to run into the close phase.
        """

        publications = tuple(
            task for task in self._publication_tasks if not task.done()
        )
        if not publications:
            return
        try:
            _, pending = await asyncio.wait(
                publications, timeout=max(float(deadline_seconds), 0.0)
            )
        except asyncio.CancelledError:
            for task in publications:
                if not task.done():
                    task.cancel()
            raise
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    async def close(self) -> None:
        """Release every owned transport exactly once.

        Complete on its own: reception is stopped if it still runs and the
        publications still accepted are awaited, so a handle closed without
        the earlier phases — a failed startup, a harness — leaks nothing.
        """

        async with self._close_lock:
            if self._closed:
                return
            await self._stop_reception()

            # Reception is stopped, but accepted publications must finish their
            # model/send/audit chain before the send transport is closed.
            publications = tuple(self._publication_tasks)
            try:
                if publications:
                    await asyncio.gather(*publications, return_exceptions=True)
            finally:
                self._closed = True
                if self._bound:
                    with suppress(Exception):
                        self._actions.mark_not_ready()

                current = asyncio.current_task()
                active_sends = tuple(
                    task for task in self._active_send_tasks if task is not current
                )
                for send_task in active_sends:
                    send_task.cancel()
                if active_sends:
                    await asyncio.gather(*active_sends, return_exceptions=True)

                # A send confirmed up to this point has handed its trace to
                # the loop; the facts are published before the handle is gone
                # — inside this module's own budget, so a subscriber that
                # never returns cannot hold the shutdown (R6). Whatever the
                # settlement does, the send transport is closed after it.
                try:
                    await self._settle_sent_traces()
                finally:
                    await _close_session(self._session)

    async def _settle_sent_traces(self) -> None:
        """Wait for the outstanding sent traces inside the close budget.

        The set is already capped, so this waits for at most
        ``max_pending_sent_traces`` publications; whichever are still pending
        when the budget runs out — or when the close itself is cancelled — are
        cancelled and counted as dropped, exactly like a trace refused at the
        cap. The confirmed-send record is untouched either way: every one of
        these sends was recorded before its trace existed.

        The cancellation is bounded by the same budget: a subscriber that
        swallows ``CancelledError`` or cleans up slowly in ``finally`` would
        otherwise hold the shutdown through the settlement it was meant to
        end. A trace still unfinished after that second budget is already
        cancelled and already counted; it is handed to the supervised
        registry, which keeps owning it and records the unfinished
        cancellation as a lifecycle failure (R4/AC15) — a shutdown that had
        to leave a subscriber running is not a clean one, however normally
        this hook returns — so the transport close behind this call runs no
        later than twice the budget. A registry without that surface leaves
        the task owned by this handle instead.
        """

        sent_traces = tuple(task for task in self._sent_traces if not task.done())
        if not sent_traces:
            return
        try:
            await asyncio.wait(sent_traces, timeout=self._sent_trace_close_seconds)
        finally:
            pending = [task for task in sent_traces if not task.done()]
            if pending:
                for task in pending:
                    task.cancel()
                # Count outside the saturated path, before the cancellations
                # are awaited: a held subscriber cannot delay the accounting.
                self._drop_sent_traces(len(pending), "close budget exhausted")
                try:
                    await asyncio.wait(
                        pending, timeout=self._sent_trace_close_seconds
                    )
                finally:
                    abandoned = [task for task in pending if not task.done()]
                    if abandoned:
                        for task in abandoned:
                            self._sent_traces.discard(task)
                            self._abandon_sent_trace(task)
                        self._diagnose(
                            "twitch sent trace: cancellation abandoned "
                            f"({len(abandoned)} held past the close budget)"
                        )

    def _abandon_sent_trace(self, task: asyncio.Task[None]) -> None:
        """Hand one cancellation-resistant trace to supervised shutdown.

        The registry records it as a task that did not stop when cancelled,
        which is what makes the lifecycle report non-zero; failing that
        surface, the handle keeps the reference itself.
        """

        abandon = getattr(self._tasks, "abandon", None)
        if callable(abandon):
            try:
                abandon(task, name="sent trace")
                return
            except Exception:
                self._diagnose("twitch sent trace: supervised hand-over failed")
        task.add_done_callback(_consume_task)
        self._abandoned_sent_traces.append(task)

    def _drop_sent_traces(self, count: int, why: str) -> None:
        """Account for *count* sent traces that will never be published (R6).

        Written into this module's own record and, when the supervision facade
        exposes its counters, into the runtime's ``lost_traces`` — the same
        counter a publication that fails inside ``record_and_emit`` lands in,
        so the loss is visible in one place whichever way it happened.
        """

        self._send_record.dropped_traces += count
        count_lost = getattr(self._supervision, "count", None)
        if callable(count_lost):
            with suppress(Exception):
                count_lost(COUNTER_LOST_TRACES, count)
        self._diagnose(f"twitch sent trace: dropped ({why})")

    async def _stop_reception(self) -> None:
        """Stop the receiver and its handoff drains, then close the socket."""

        async with self._reception_lock:
            self._stopping = True

            task = self._receive_task
            if task is not None and not task.done():
                task.cancel()
            if task is not None:
                with suppress(asyncio.CancelledError, Exception):
                    await task

            handoff_tasks = tuple(self._handoff_tasks)
            for handoff_task in handoff_tasks:
                if not handoff_task.done():
                    handoff_task.cancel()
            for handoff_task in handoff_tasks:
                with suppress(asyncio.CancelledError, Exception):
                    await handoff_task
            self._handoff_tasks.clear()

            websocket = self._websocket
            self._websocket = None
            if websocket is not None:
                await _close_websocket(websocket)

    # -- chat send: one service, two routes (R5, R8) ------------------------ #

    async def _invoke_chat_write(self, invocation: Any) -> ActionObservation:
        """Serve one ``chat.write`` call for the executor.

        Arguments already passed the declared schema; what the schema cannot
        say — blank text, a blank reply parent, a destination other than the
        configured channel — is refused here without emission. Emission is
        signalled on the invocation before the request leaves, so an
        interruption after that point is classified ``external_unknown`` by
        the executor and never as a certain timeout (AC33).
        """

        invocation.mark_not_emitted()
        call = invocation.call
        provenance = {
            "provider": CHAT_WRITE_PROVIDER,
            "platform": PLATFORM,
            "channel_id": self._settings.broadcaster_id,
            "route": _ROUTE_ACTION,
        }

        def failure(status: str, code: str, message: str) -> ActionObservation:
            return ActionObservation(
                status=status,
                provenance=provenance,
                error={"code": code, "message": message, "retryable": False},
            )

        arguments = call.arguments
        text = arguments.get("text")
        parent = arguments.get("parent_message_id")
        blank_parent = parent is not None and (
            not isinstance(parent, str) or not parent.strip()
        )
        if not isinstance(text, str) or not text.strip() or blank_parent:
            self._diagnose("twitch chat send: invalid input")
            return failure(
                _STATUS_ERROR,
                _ERROR_INVALID_ARGUMENTS,
                "text and parent_message_id must be non-blank strings",
            )
        destination = call.destination
        if (
            destination.platform != PLATFORM
            or destination.channel_id != self._settings.broadcaster_id
        ):
            return failure(
                _STATUS_ERROR,
                _ERROR_UNSUPPORTED_DESTINATION,
                "destination is outside the configured channel",
            )

        correlation: dict[str, Any] = {
            "run_id": call.run_id,
            "call_id": call.call_id,
            "conversation_id": call.conversation_id,
            "source_event_id": call.source_event_id,
        }
        if call.message_id is not None:
            correlation["source_message_id"] = call.message_id
        outcome = await self._send(
            text,
            parent,
            route=_ROUTE_ACTION,
            correlation=correlation,
            on_emit=invocation.mark_emitted,
            after_completion=invocation.after_completion,
        )
        if outcome.status == _STATUS_SUCCESS:
            return ActionObservation(
                status=_STATUS_SUCCESS,
                provenance=provenance,
                result={
                    "message_id": outcome.message_id,
                    "destination": {
                        "platform": PLATFORM,
                        "channel_id": self._settings.broadcaster_id,
                    },
                },
            )
        return failure(
            outcome.status,
            outcome.code or _ERROR_TRANSPORT_FAILED,
            outcome.reason or "",
        )

    async def handle_chat_send(self, event: Mapping[str, Any]) -> dict[str, Any]:
        """The compatibility ``channel.chat.send`` route onto the send service.

        Legacy readers keep ``metadata.delivery_status`` as ``sent`` or
        ``failed``; ``metadata.delivery_outcome`` carries the terminal status
        of the same send, so an emitted request whose outcome is unknown is
        never read as a plain failure that is safe to repeat. Operational
        failures continue the bus chain so audit sees the attempt. Only the
        platform's own acknowledgement means sent. Cancellation propagates
        without a record. No response body or external exception text enters
        diagnostics or metadata.
        """

        def outcome(status: str, terminal: str) -> dict[str, Any]:
            return {
                **event,
                "metadata": {
                    **event["metadata"],
                    "delivery_status": status,
                    "delivery_outcome": terminal,
                },
            }

        if self._closed:
            return outcome("failed", _STATUS_ERROR)
        try:
            payload = _mapping_at(event, "payload")
            text = _required_string(payload, "text")
            if not text.strip():
                raise ValueError
            parent: str | None = None
            if "parent_message_id" in payload:
                parent = _required_string(payload, "parent_message_id")
                if not parent.strip():
                    raise ValueError
        except ValueError:
            self._diagnose("twitch chat send: invalid input")
            return outcome("failed", _STATUS_ERROR)

        metadata = event.get("metadata")
        correlation: dict[str, Any] = {}
        if isinstance(metadata, Mapping):
            source_message_id = metadata.get("source_message_id")
            if isinstance(source_message_id, str) and source_message_id:
                correlation["source_message_id"] = source_message_id
        result = await self._send(
            text, parent, route=_ROUTE_COMPATIBILITY, correlation=correlation
        )
        return outcome(
            "sent" if result.status == _STATUS_SUCCESS else "failed", result.status
        )

    async def _send(
        self,
        text: str,
        parent_message_id: str | None,
        *,
        route: str,
        correlation: Mapping[str, Any],
        on_emit: Callable[[], None] | None = None,
        after_completion: Callable[[Callable[[], None]], None] | None = None,
    ) -> _SendOutcome:
        """Emit one Helix send and classify its outcome honestly (R5).

        ``success`` needs the platform's ``is_sent`` acknowledgement with the
        identifier it assigned; ``error`` is a platform refusal or a request
        that never left; ``external_unknown`` is a request that left — or may
        have — with no readable answer. *on_emit* is called right before the
        request leaves, so the executor's invocation carries the emission
        signal from that instant on. *after_completion* is the invocation's
        completion hook on the action route: the confirmed-send trace is
        handed to the loop through it, once the executor has published its
        own ``action.completed`` (AC27); without it — the compatibility
        route — the trace is handed over as soon as the send is recorded.
        """

        if self._closed:
            return _SendOutcome(
                _STATUS_ERROR,
                code=_ERROR_PROVIDER_CLOSED,
                reason="twitch chat send: transport closed",
            )
        request_body = {
            "broadcaster_id": self._settings.broadcaster_id,
            "sender_id": self._settings.bot_user_id,
            "message": text,
        }
        if parent_message_id is not None:
            request_body["reply_parent_message_id"] = parent_message_id

        task = asyncio.current_task()
        if task is not None:
            self._active_send_tasks.add(task)
        try:
            self._send_record.emitted += 1
            if on_emit is not None:
                on_emit()
            try:
                status, body = await asyncio.wait_for(
                    self._post_chat(request_body), timeout=_CHAT_SEND_TIMEOUT_SECONDS
                )
            except asyncio.CancelledError:
                raise
            except asyncio.TimeoutError:
                return self._unconfirmed(
                    _STATUS_EXTERNAL_UNKNOWN,
                    _ERROR_TIMED_OUT,
                    "twitch chat send: request timed out",
                )
            except Exception:
                return self._unconfirmed(
                    _STATUS_EXTERNAL_UNKNOWN,
                    _ERROR_TRANSPORT_FAILED,
                    "twitch chat send: request failed",
                )

            if status != 200:
                return self._unconfirmed(
                    _STATUS_ERROR,
                    _ERROR_PLATFORM_REJECTED,
                    f"twitch chat send: rejected (status {_safe_status(status)})",
                )
            data = body.get("data") if isinstance(body, Mapping) else None
            sent = data[0] if isinstance(data, list) and len(data) == 1 else None
            if (
                not isinstance(sent, Mapping)
                or not isinstance(sent.get("is_sent"), bool)
            ):
                return self._unconfirmed(
                    _STATUS_EXTERNAL_UNKNOWN,
                    _ERROR_MALFORMED_RESPONSE,
                    "twitch chat send: malformed response",
                )
            if not sent["is_sent"]:
                return self._unconfirmed(
                    _STATUS_ERROR,
                    _ERROR_PLATFORM_REJECTED,
                    "twitch chat send: rejected (status 200)",
                )
            message_id = sent.get("message_id")
            if not isinstance(message_id, str) or not message_id.strip():
                return self._unconfirmed(
                    _STATUS_EXTERNAL_UNKNOWN,
                    _ERROR_MALFORMED_RESPONSE,
                    "twitch chat send: malformed response",
                )
            # Recorded before this returns; the trace is not waited for.
            self._confirm(
                message_id,
                route=route,
                correlation=correlation,
                after_completion=after_completion,
            )
            return _SendOutcome(_STATUS_SUCCESS, message_id=message_id)
        finally:
            if task is not None:
                self._active_send_tasks.discard(task)

    def _unconfirmed(self, status: str, code: str, diagnostic: str) -> _SendOutcome:
        """Record and report a send that was not confirmed, by certainty."""

        self._diagnose(diagnostic)
        if status == _STATUS_EXTERNAL_UNKNOWN:
            self._send_record.unknown += 1
        else:
            self._send_record.failed += 1
        return _SendOutcome(status, code=code, reason=diagnostic)

    def _confirm(
        self,
        message_id: str,
        *,
        route: str,
        correlation: Mapping[str, Any],
        after_completion: Callable[[Callable[[], None]], None] | None = None,
    ) -> None:
        """Record the confirmed send now; hand ``channel.chat.sent`` to the loop (R8).

        The record is this module's own write and is made here, before the
        outcome is returned and before the trace exists. The publication is
        then handed to the loop as a tracked task rather than awaited: the
        outcome the platform confirmed must not depend on how long a trace
        subscriber takes. Awaited, a slow subscriber would spend the
        executor's budget and turn a confirmed send into ``external_unknown``
        — a false uncertainty about an effect that is known — and would hold
        the compatibility route's caller without any bound at all.

        On the action route the hand-over itself waits for the executor:
        *after_completion* runs it once the executor has recorded the call's
        observation and published ``action.completed``, so the audit reads
        the executor's completion before this module's fact — AC27's order —
        and reads the fact only after the transport confirmed, since the
        record is already written when the hook is registered. The
        compatibility route has no executor and hands the trace over at once.

        ``record_and_emit`` still runs the record first, as R8's ordering
        primitive; that second write is a no-op, as the scheduler's is. A
        publication that fails is counted by supervision as a lost trace and
        never reaches back here — nothing is retried and nothing is sent
        again (AC28). :meth:`close` waits for the traces still in flight.

        Detached is not unbounded (R6): past ``max_pending_sent_traces``
        outstanding publications the trace is dropped — the record already
        stands — and counted through :meth:`_drop_sent_traces`, outside the
        saturated path, so a subscriber holding ``channel.chat.sent`` can
        never grow this set beyond the cap. A hand-over reaching a closed
        handle — the executor's completion held past ``close`` — is dropped
        and counted the same way: nothing settles a trace created after the
        settlement ran.
        """

        recorded = False

        def record() -> None:
            nonlocal recorded
            if recorded:
                return
            recorded = True
            self._send_record.confirmed += 1
            self._send_record.last_message_id = message_id

        record()

        def hand_over() -> None:
            self._hand_over_sent_trace(record, message_id, route, correlation)

        if after_completion is None:
            hand_over()
            return
        after_completion(hand_over)

    def _hand_over_sent_trace(
        self,
        record: Callable[[], None],
        message_id: str,
        route: str,
        correlation: Mapping[str, Any],
    ) -> None:
        """Hand one recorded send's ``channel.chat.sent`` to the loop, bounded."""

        if self._closed:
            self._drop_sent_traces(1, "handle closed")
            return
        if len(self._sent_traces) >= self._max_pending_sent_traces:
            self._drop_sent_traces(1, "pending traces saturated")
            return
        payload: dict[str, Any] = {
            "platform": PLATFORM,
            "channel_id": self._settings.broadcaster_id,
            "message_id": message_id,
            "provider": CHAT_WRITE_PROVIDER,
            "route": route,
            **correlation,
        }
        try:
            trace = self._supervision.record_and_emit(
                record, TRACE_CHANNEL_CHAT_SENT, payload
            )
        except Exception:
            # A trace refused at the call site is a lost trace; the send is
            # confirmed and recorded regardless.
            self._diagnose("twitch sent trace: publication failed")
            return
        task = asyncio.ensure_future(trace)
        self._sent_traces.add(task)
        task.add_done_callback(self._sent_trace_finished)

    def _sent_trace_finished(self, task: asyncio.Task[None]) -> None:
        self._sent_traces.discard(task)
        if not task.cancelled() and task.exception() is not None:
            # ``record_and_emit`` files a failed publication as a lost trace
            # itself; anything else surfacing here is still only a trace.
            self._diagnose("twitch sent trace: publication failed")

    async def _post_chat(
        self, request_body: Mapping[str, str]
    ) -> tuple[int | None, Any]:
        response = await _resolve(
            self._session.post(
                HELIX_CHAT_URL, headers=self._helix_headers(), json=request_body
            )
        )
        return await _read_response(response)

    async def _authenticate(self) -> None:
        try:
            response = await _resolve(
                self._session.get(
                    TOKEN_VALIDATION_URL,
                    headers={
                        "Authorization": f"OAuth {self._settings.access_token}"
                    },
                )
            )
            status, body = await _read_response(response)
        except asyncio.CancelledError:
            raise
        except Exception:
            self._diagnose("twitch authentication: request failed")
            raise TwitchModuleError("twitch authentication failed") from None

        valid = (
            status == 200
            and isinstance(body, Mapping)
            and body.get("client_id") == self._settings.client_id
            and (
                body.get("user_id") is None
                or body.get("user_id") == self._settings.bot_user_id
            )
        )
        if not valid:
            self._diagnose(
                f"twitch authentication: rejected (status {_safe_status(status)})"
            )
            raise TwitchModuleError("twitch authentication rejected")

    async def _connect(self, url: str) -> Any:
        try:
            return await _resolve(self._session.ws_connect(url))
        except asyncio.CancelledError:
            raise
        except Exception:
            self._diagnose("twitch websocket: connection failed")
            raise TwitchModuleError("twitch websocket connection failed") from None

    async def _receive_welcome(self, websocket: Any) -> str:
        try:
            frame = await websocket.receive()
            envelope = _decode_text_frame(frame)
            metadata = _mapping_at(envelope, "metadata")
            payload = _mapping_at(envelope, "payload")
            session = _mapping_at(payload, "session")
            session_id = session.get("id")
            if (
                metadata.get("message_type") != "session_welcome"
                or not isinstance(session_id, str)
                or not session_id
            ):
                raise ValueError
            return session_id
        except asyncio.CancelledError:
            raise
        except Exception:
            self._diagnose("twitch websocket welcome: malformed data")
            raise TwitchModuleError("twitch websocket welcome failed") from None

    async def _subscribe(self, session_id: str) -> None:
        request_body = {
            "type": _CHAT_EVENT,
            "version": "1",
            "condition": {
                "broadcaster_user_id": self._settings.broadcaster_id,
                "user_id": self._settings.bot_user_id,
            },
            "transport": {"method": "websocket", "session_id": session_id},
        }
        try:
            response = await _resolve(
                self._session.post(
                    EVENTSUB_SUBSCRIPTIONS_URL,
                    headers=self._helix_headers(),
                    json=request_body,
                )
            )
            status, body = await _read_response(response)
        except asyncio.CancelledError:
            raise
        except Exception:
            self._diagnose("twitch subscription: request failed")
            raise TwitchModuleError("twitch subscription failed") from None

        data = body.get("data") if isinstance(body, Mapping) else None
        if (
            status != 202
            or not isinstance(data, list)
            or not data
            or not isinstance(data[0], Mapping)
        ):
            self._diagnose(
                f"twitch subscription: rejected (status {_safe_status(status)})"
            )
            raise TwitchModuleError("twitch subscription rejected")

    async def _receive_forever(self, initial_websocket: Any) -> None:
        websocket = initial_websocket
        retry_attempt = 0
        connected_at = asyncio.get_running_loop().time()
        try:
            while not self._closed and not self._stopping:
                result = await self._consume(websocket)
                if self._closed or self._stopping:
                    return
                socket_already_closed = False

                if (
                    asyncio.get_running_loop().time() - connected_at
                    >= _STABLE_CONNECTION_SECONDS
                ):
                    retry_attempt = 0

                if result.action == "stop":
                    if result.diagnostic is not None:
                        self._diagnose(result.diagnostic)
                    await _close_websocket(websocket)
                    if self._websocket is websocket:
                        self._websocket = None
                    return

                if result.action == "handoff" and result.reconnect_url is not None:
                    drain_task = self._start_handoff_drain(websocket)
                    if self._websocket is websocket:
                        self._websocket = None
                    replacement = await self._connect_for_recovery(
                        result.reconnect_url, subscribe=False
                    )
                    if replacement is not None:
                        websocket = replacement
                        self._websocket = replacement
                        connected_at = asyncio.get_running_loop().time()
                        continue
                    await self._cancel_handoff_drain(drain_task)
                    socket_already_closed = True

                if not socket_already_closed:
                    await _close_websocket(websocket)
                if self._websocket is websocket:
                    self._websocket = None
                if self._closed or self._stopping:
                    return

                replacement = None
                while (
                    replacement is None and not self._closed and not self._stopping
                ):
                    delay = min(0.25 * (2**retry_attempt), 5.0)
                    retry_attempt = min(retry_attempt + 1, 5)
                    await _resolve(self._retry_delay(delay))
                    if self._closed or self._stopping:
                        return
                    replacement = await self._connect_for_recovery(
                        EVENTSUB_URL, subscribe=True
                    )
                if replacement is None:
                    return
                websocket = replacement
                self._websocket = replacement
                connected_at = asyncio.get_running_loop().time()
        except asyncio.CancelledError:
            raise
        except Exception:
            # No external exception text is copied into diagnostics.
            self._diagnose("twitch websocket receive: unexpected failure")

    def _start_handoff_drain(self, websocket: Any) -> asyncio.Task[None]:
        task = asyncio.create_task(
            self._drain_handoff_socket(websocket),
            name="twitch-eventsub-handoff-drain",
        )
        self._handoff_tasks.add(task)
        task.add_done_callback(self._handoff_tasks.discard)
        return task

    async def _cancel_handoff_drain(self, task: asyncio.Task[None]) -> None:
        if not task.done():
            task.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await task

    async def _drain_handoff_socket(self, websocket: Any) -> None:
        try:
            result = await self._consume(websocket, allow_handoff=False)
            if result.diagnostic == "twitch subscription: revoked":
                self._diagnose(result.diagnostic)
        except asyncio.CancelledError:
            raise
        except Exception:
            self._diagnose("twitch websocket receive: unexpected failure")
        finally:
            await _close_websocket(websocket)

    async def _connect_for_recovery(
        self, url: str, *, subscribe: bool
    ) -> Any | None:
        try:
            websocket = await self._connect(url)
            try:
                session_id = await self._receive_welcome(websocket)
                if subscribe:
                    await self._subscribe(session_id)
            except BaseException:
                await _close_websocket(websocket)
                raise
            return websocket
        except asyncio.CancelledError:
            raise
        except TwitchModuleError:
            return None

    async def _consume(
        self, websocket: Any, *, allow_handoff: bool = True
    ) -> _ConsumeResult:
        """Consume one socket until it needs handoff, retry, or shutdown."""

        while not self._closed and not self._stopping:
            try:
                frame = await websocket.receive()
            except asyncio.CancelledError:
                raise
            except Exception:
                self._diagnose("twitch websocket receive: connection lost")
                return _ConsumeResult("retry")

            frame_kind = _frame_kind(frame)
            if frame_kind == "closed":
                close_code = _close_code(frame, websocket)
                if close_code in _NON_RETRYABLE_CLOSE_CODES:
                    return _ConsumeResult(
                        "stop",
                        diagnostic="twitch websocket: non-retryable disconnect",
                    )
                return _ConsumeResult("retry")
            if frame_kind == "error":
                self._diagnose("twitch websocket receive: connection lost")
                return _ConsumeResult("retry")
            if frame_kind != "text":
                self._diagnose("twitch websocket receive: malformed data")
                continue

            try:
                envelope = _decode_text_frame(frame)
                metadata = _mapping_at(envelope, "metadata")
                message_type = metadata.get("message_type")
                if not isinstance(message_type, str):
                    raise ValueError

                if message_type == "session_keepalive":
                    continue
                if message_type == "notification":
                    publication = asyncio.create_task(
                        self._publish_notification(envelope),
                        name="twitch-eventsub-publication",
                    )
                    self._publication_tasks.add(publication)
                    publication.add_done_callback(self._publication_finished)
                    await asyncio.shield(publication)
                    continue
                if message_type == "session_reconnect":
                    payload = _mapping_at(envelope, "payload")
                    session = _mapping_at(payload, "session")
                    reconnect_url = session.get("reconnect_url")
                    if not isinstance(reconnect_url, str) or not reconnect_url:
                        raise ValueError
                    if allow_handoff:
                        return _ConsumeResult("handoff", reconnect_url=reconnect_url)
                    continue
                if message_type == "revocation":
                    return _ConsumeResult(
                        "stop", diagnostic="twitch subscription: revoked"
                    )
                raise ValueError
            except asyncio.CancelledError:
                raise
            except Exception:
                self._diagnose("twitch notification: malformed data")

        return _ConsumeResult("stop")

    def _publication_finished(self, task: asyncio.Task[None]) -> None:
        self._publication_tasks.discard(task)
        if not task.cancelled():
            # A receiver may already be stopped when publication fails.
            task.exception()

    async def _publish_notification(self, envelope: Mapping[str, Any]) -> None:
        """Normalise once, dedupe, feed, decide, publish, trace, admit.

        The order is R1's: normalisation, then the chat context, then the
        trigger, then admission — and nothing here awaits a run: the work is
        handed to the scheduler and this returns (R2). The bus carries the
        normalised event as a fact for every trusted input, decided either
        way, in exactly one shape; the consumer that admits from the bus
        reads the recorded decision, so it runs a model for nothing the
        trigger refused (R1, AC4). A malformed envelope raises ``ValueError``
        for the receiver to report.
        """

        notification = _normalize_notification(envelope)
        # Drop self echoes before they can reach any consumer, feed any
        # context or draw any decision (R1, AC5).
        if self._is_own_message(notification.author_id):
            return

        engine = self._triggers
        async with self._publish_lock:
            if engine is not None and engine.recorded(
                platform=PLATFORM,
                channel_id=notification.channel_id,
                source_event_id=notification.message_id,
            ) is not None:
                # Inside the retained window: the decision already exists and
                # the event was already published, fed and admitted (or
                # refused). Nothing is repeated (R6, AC22).
                return

            event = notification.event()
            if notification.author_id is not None:
                self._feed_chat_context(notification)

            decision = None
            if engine is not None:
                try:
                    decision = engine.evaluate(
                        event, context=TriggerContext(notification.claims)
                    )
                except Exception:
                    # Fail closed: the input is still recorded below, but
                    # nothing is admitted on an undecided event.
                    self._diagnose("twitch trigger evaluation: failed")
        # The lock is released: nothing below is normalisation or dedup.

        if notification.author_id is None:
            # No trusted viewer identity: never published, never admitted;
            # the refusal is the engine's traced decision (R3, AC12).
            if decision is not None:
                await self._trace_decision(decision)
            elif engine is None:
                self._diagnose("twitch notification: no trusted viewer identity")
            return

        # With no engine (the compatibility runtime) nothing decides and the
        # publication is the whole route, as before v2; with one, the
        # consumer reads the recorded decision, and an undecided event (the
        # engine failed) has none to read: fail closed.
        try:
            await self._bus.publish(_CHAT_EVENT, event["payload"], event["metadata"])
        except asyncio.CancelledError:
            raise
        except Exception:
            self._diagnose("twitch notification publish: failed")

        if decision is None:
            return
        await self._trace_decision(decision)
        if decision.accepted and self._scheduler is not None:
            self._admit(notification, event)

    def _is_own_message(self, author_id: str | None) -> bool:
        bot_user_id = getattr(self._settings, "bot_user_id", None)
        return (
            author_id is not None
            and isinstance(bot_user_id, str)
            and bool(bot_user_id.strip())
            and author_id == bot_user_id
        )

    def _feed_chat_context(self, notification: _Notification) -> None:
        """Feed the per-channel transcript, whatever is decided next (R3)."""

        if self._chat is None or notification.author_id is None:
            return
        try:
            self._chat.append(
                PLATFORM,
                notification.channel_id,
                ChatEntry(
                    author_id=notification.author_id,
                    message_id=notification.message_id,
                    text=notification.text,
                ),
            )
        except Exception:
            self._diagnose("twitch chat context: append failed")

    async def _trace_decision(self, decision: Any) -> None:
        """Publish the trigger decision as its supervision fact (R8).

        The trace carries the source event, the input, the channel pair, the
        policy version and the bounded reason — and no ``run_id``, because no
        run exists yet (AC27). A lost trace is diagnosed and changes nothing
        about what was decided.
        """

        if not getattr(decision, "traceable", True):
            return
        event_type = (
            TRACE_INPUT_TRIGGER_ACCEPTED
            if decision.accepted
            else TRACE_INPUT_TRIGGER_REJECTED
        )
        payload = {
            "source_event_id": decision.source_event_id,
            "input": decision.input_name,
            "platform": decision.platform,
            "channel_id": decision.channel_id,
            "policy_version": decision.policy_version,
            "reason": decision.reason,
        }
        try:
            await self._supervision.emit(event_type, payload)
        except asyncio.CancelledError:
            raise
        except Exception:
            self._diagnose("twitch trigger trace: publication failed")

    def _admit(self, notification: _Notification, event: Mapping[str, Any]) -> None:
        """Hand the accepted event to the scheduler and return at once (R2).

        The scheduler copies the event, decides synchronously, traces its own
        admission fact and runs the work on a worker; a refusal is its traced
        saturation decision and needs nothing from here.
        """

        assert notification.author_id is not None
        try:
            session_key = SessionKey(
                platform=PLATFORM,
                channel_id=notification.channel_id,
                viewer_id=notification.author_id,
            )
            work = Work(
                payload=event,
                source_event_id=notification.message_id,
                kind="chat.message",
            )
            self._scheduler.admit(session_key, work)
        except Exception:
            self._diagnose("twitch admission: failed")

    def _helix_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._settings.access_token}",
            "Client-Id": self._settings.client_id,
            "Content-Type": "application/json",
        }

    def _diagnose(self, message: str) -> None:
        try:
            self._reporter(message)
        except Exception:
            # Diagnostics must not make reception or publication fail.
            pass


async def activate(
    context: Any,
    settings: Mapping[str, Any],
    catalog: Mapping[str, Mapping[str, Any]],
) -> TwitchModule:
    """Build the prepared handle from the scoped runtime context (R7, AC26).

    *context* is the module-scoped view of the versioned runtime: the bus is
    read from it and the receiver is owned through its supervised tasks.
    Nothing here reaches the network — authentication is ``prepare`` and the
    source is ``start_inputs`` — so a refused activation has nothing to undo.
    """

    del catalog  # Capabilities are declared by the colocated manifest.
    _runtime_surfaces(context)  # Refused here, before any session exists.
    parsed = _Settings.from_mapping(settings)
    reporter = settings.get("diagnostic_reporter", _default_reporter)
    if not callable(reporter):
        raise TwitchModuleError("twitch configuration: diagnostic reporter is invalid")
    session_factory = settings.get("_session_factory", SESSION_FACTORY)
    retry_delay = settings.get("_retry_delay", RETRY_DELAY)
    if not callable(session_factory) or not callable(retry_delay):
        raise TwitchModuleError("twitch configuration: transport seam is invalid")
    # The detached-trace bounds are validated before any session exists, so a
    # non-finite limit is refused at startup and never discovered under load.
    max_pending_sent_traces = _validate_count_limit(
        settings.get("_max_pending_sent_traces", DEFAULT_MAX_PENDING_SENT_TRACES),
        "_max_pending_sent_traces",
    )
    sent_trace_close_seconds = _validate_duration_limit(
        settings.get("_sent_trace_close_seconds", DEFAULT_SENT_TRACE_CLOSE_SECONDS),
        "_sent_trace_close_seconds",
    )

    try:
        created = session_factory()
        session = await created if inspect.isawaitable(created) else created
    except Exception:
        _safe_report(reporter, "twitch transport: session creation failed")
        raise TwitchModuleError("twitch transport initialization failed") from None

    module = TwitchModule(
        context,
        parsed,
        session,
        reporter,
        retry_delay,
        max_pending_sent_traces=max_pending_sent_traces,
        sent_trace_close_seconds=sent_trace_close_seconds,
    )
    # Published only on a bound registry: a context built without one hands
    # out a facade whose ``available`` is false, and activation on it stays
    # exactly what it was (R7). A duplicate key is not caught — it fails
    # activation, the session released first (AC25).
    services = getattr(context, "services", None)
    if services is not None and getattr(services, "available", False) is True:
        try:
            services.publish(POLL_SERVICE_KIND, PLATFORM, module.poll_service)
        except BaseException:
            await _close_session(session)
            raise
    return module


def _validate_count_limit(value: Any, field_name: str) -> int:
    """A positive, finite integer bound, refused with a value-free diagnostic."""

    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise TwitchModuleError(
            _setting_diagnostic(field_name, "must be a positive integer")
        )
    return value


def _validate_duration_limit(value: Any, field_name: str) -> float:
    """A non-negative, finite duration bound, refused with a value-free diagnostic."""

    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
    ):
        raise TwitchModuleError(
            _setting_diagnostic(field_name, "must be a finite non-negative number")
        )
    return float(value)


@dataclass(frozen=True, slots=True)
class _RuntimeSurfaces:
    """The collaborators this module reads from its scoped context (R7)."""

    bus: Any
    tasks: Any
    actions: Any
    supervision: Any
    triggers: Any | None
    chat: Any | None
    scheduler: Any | None


_REQUIRED_SURFACES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("bus", ("publish", "subscribe")),
    ("tasks", ("spawn",)),
    ("actions", ("register", "mark_ready", "mark_not_ready")),
    ("supervision", ("emit", "record_and_emit")),
)
_OPTIONAL_SURFACES: tuple[tuple[str, tuple[str, ...]], ...] = (
    # Absent in the compatibility runtime only, which the loader refuses this
    # module in: without a trigger engine there is no dedup window and no
    # decision, without a chat context nothing is fed, and without a
    # scheduler nothing is admitted — the bus publication is then the whole
    # route, as before v2.
    ("triggers", ("evaluate", "recorded")),
    ("chat", ("append",)),
    ("scheduler", ("admit",)),
)


def _runtime_surfaces(context: Any) -> _RuntimeSurfaces:
    """Read the runtime surfaces from *context*, checked by shape (AC26).

    Duck-typed so a harness may inject fakes, while a context that is not a
    context — the bare bus, say — is refused with one diagnostic.
    """

    surfaces: dict[str, Any] = {}
    for name, methods in _REQUIRED_SURFACES:
        value = getattr(context, name, None)
        if value is None or not all(callable(getattr(value, m, None)) for m in methods):
            raise TwitchModuleError("twitch activation: runtime context is invalid")
        surfaces[name] = value
    for name, methods in _OPTIONAL_SURFACES:
        value = getattr(context, name, None)
        if value is not None and not all(
            callable(getattr(value, m, None)) for m in methods
        ):
            raise TwitchModuleError("twitch activation: runtime context is invalid")
        surfaces[name] = value
    return _RuntimeSurfaces(**surfaces)


def _declared_chat_write_spec() -> ActionSpec:
    """Build the ``chat.write`` contract from the colocated manifest (R5).

    Read at preparation, from the same file the loader validated at
    discovery, so the module serves exactly the contract it declares and
    holds no second copy of it. Any defect is one value-free diagnostic.
    """

    try:
        manifest = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))
        entries = manifest["actions"] if isinstance(manifest, Mapping) else None
        entry = next(
            item
            for item in (entries if isinstance(entries, list) else ())
            if isinstance(item, Mapping) and item.get("name") == CHAT_WRITE_ACTION
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
            # The delivery capability (R1 decision 1) is part of the contract
            # the loader discovered; omitting it would make this a different
            # specification and the registry would refuse the redeclaration.
            delivery=entry.get("delivery"),
        )
    except Exception:
        raise TwitchModuleError(
            f"twitch prepare: manifest declaration of {CHAT_WRITE_ACTION!r} is invalid"
        ) from None


def _normalize_notification(envelope: Mapping[str, Any]) -> _Notification:
    """Translate one EventSub chat notification, once, at the boundary (R3).

    Raises ``ValueError`` for an envelope that is not a chat notification or
    lacks the channel, the message identifier or the text. An absent chatter
    identity is *not* malformed: it is a notification with no trusted viewer
    identity, which the trigger engine refuses as unauthenticated.
    """

    metadata = _mapping_at(envelope, "metadata")
    subscription_type = metadata.get("subscription_type")
    if subscription_type not in (None, _CHAT_EVENT):
        raise ValueError

    payload = _mapping_at(envelope, "payload")
    subscription = payload.get("subscription")
    if isinstance(subscription, Mapping):
        declared_type = subscription.get("type")
        if declared_type not in (None, _CHAT_EVENT):
            raise ValueError
    event = _mapping_at(payload, "event")
    message = _mapping_at(event, "message")

    channel_id = _required_string(event, "broadcaster_user_id")
    message_id = _required_string(event, "message_id")
    text = _required_string(message, "text", allow_empty=True)
    author_id = _optional_string(event.get("chatter_user_id"))
    author_name = _optional_string(event.get("chatter_user_name"))
    return _Notification(
        channel_id=channel_id,
        author_id=author_id,
        author_name=author_name,
        message_id=message_id,
        text=text,
        claims=_trusted_claims(event, author_id, channel_id),
    )


def _trusted_claims(
    event: Mapping[str, Any], author_id: str | None, channel_id: str
) -> tuple[TrustedClaim, ...]:
    """The platform-attested facts about the author, tagged by provenance.

    The broadcaster is recognised by identity — the chatter *is* the channel —
    and every other role by the badge set the platform attached. The message
    body is never consulted (R1, AC4).
    """

    claims: list[TrustedClaim] = []
    names: set[str] = set()
    if author_id is not None and author_id == channel_id:
        claims.append(TrustedClaim("broadcaster", provenance=_BROADCASTER_PROVENANCE))
        names.add("broadcaster")
    badges = event.get("badges")
    if isinstance(badges, (list, tuple)):
        for badge in badges:
            if not isinstance(badge, Mapping):
                continue
            claim = _BADGE_CLAIMS.get(badge.get("set_id"))
            if claim is None or claim in names:
                continue
            claims.append(TrustedClaim(claim, provenance=_BADGE_PROVENANCE))
            names.add(claim)
    return tuple(claims)


def _mapping_at(value: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    item = value.get(key)
    if not isinstance(item, Mapping):
        raise ValueError
    return item


def _required_string(
    value: Mapping[str, Any], key: str, *, allow_empty: bool = False
) -> str:
    item = value.get(key)
    if not isinstance(item, str) or (not allow_empty and not item):
        raise ValueError
    return item


def _optional_string(value: Any) -> str | None:
    """*value* when it is a non-blank string, else ``None``."""

    if isinstance(value, str) and value.strip():
        return value
    return None


def _frame_kind(frame: Any) -> str:
    if isinstance(frame, (str, bytes, bytearray, Mapping)):
        return "text"
    frame_type = getattr(frame, "type", None)
    if frame_type == "text" or (
        aiohttp is not None and frame_type == aiohttp.WSMsgType.TEXT
    ):
        return "text"
    closed_types = {"close", "closed", "closing"}
    if aiohttp is not None:
        closed_types.update(
            {
                aiohttp.WSMsgType.CLOSE,
                aiohttp.WSMsgType.CLOSED,
                aiohttp.WSMsgType.CLOSING,
            }
        )
    if frame_type in closed_types:
        return "closed"
    if frame_type == "error" or (
        aiohttp is not None and frame_type == aiohttp.WSMsgType.ERROR
    ):
        return "error"
    return "other"


def _decode_text_frame(frame: Any) -> Mapping[str, Any]:
    if isinstance(frame, Mapping):
        decoded: Any = frame
    else:
        data = frame if isinstance(frame, (str, bytes, bytearray)) else frame.data
        if isinstance(data, (bytes, bytearray)):
            data = bytes(data).decode("utf-8")
        if not isinstance(data, str):
            raise ValueError
        decoded = json.loads(data)
    if not isinstance(decoded, Mapping):
        raise ValueError
    return decoded


def _close_code(frame: Any, websocket: Any) -> int | None:
    data = getattr(frame, "data", None)
    if isinstance(data, int):
        return data
    code = getattr(websocket, "close_code", None)
    return code if isinstance(code, int) else None


async def _read_response(response: Any) -> tuple[int | None, Any]:
    try:
        status = getattr(response, "status", None)
        try:
            body = await _resolve(response.json())
        except Exception:
            body = None
        return (status if isinstance(status, int) else None), body
    finally:
        release = getattr(response, "release", None)
        if callable(release):
            released = release()
            if inspect.isawaitable(released):
                await released


async def _resolve(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


async def _close_websocket(websocket: Any) -> None:
    close = getattr(websocket, "close", None)
    if callable(close):
        with suppress(Exception):
            result = close()
            if inspect.isawaitable(result):
                await result


def _consume_task(task: "asyncio.Future[Any]") -> None:
    """Retrieve an abandoned task's late outcome so it is never unobserved."""

    if not task.cancelled():
        task.exception()


async def _close_session(session: Any) -> None:
    close = getattr(session, "close", None)
    if callable(close):
        with suppress(Exception):
            result = close()
            if inspect.isawaitable(result):
                await result


def _safe_status(status: Any) -> str:
    return str(status) if isinstance(status, int) else "unknown"


def _safe_report(reporter: Callable[[str], None], message: str) -> None:
    with suppress(Exception):
        reporter(message)


def _default_reporter(message: str) -> None:
    print(message, file=sys.stderr)


__all__ = [
    "CHAT_WRITE_ACTION",
    "CHAT_WRITE_PROVIDER",
    "DEFAULT_MAX_PENDING_SENT_TRACES",
    "DEFAULT_SENT_TRACE_CLOSE_SECONDS",
    "HELIX_POLLS_URL",
    "MANIFEST_PATH",
    "MODULE_NAME",
    "PLATFORM",
    "POLL_SERVICE_KIND",
    "PollMalformedAnswer",
    "PollServiceError",
    "PollStatusError",
    "PollTransportError",
    "SendRecord",
    "TwitchModule",
    "TwitchModuleError",
    "activate",
    "validate_settings",
]
