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
repetition arriving after eviction is reprocessed (AC22). Until the bus
consumer of ``channel.chat.message`` is itself scheduler-driven (P16), the bus
copy of an *accepted* event alone carries the ``viewer_id`` bridge that
consumer reads, so nothing the trigger refused can reach a model through the
bus (R1, AC4).

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
``external_unknown``, nor hold the compatibility route's caller.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import sys
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

import yaml

from core.admission import Work
from core.context import ChatEntry
from core.contracts import (
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
_STABLE_CONNECTION_SECONDS = 10.0

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

# The interim admission bridge. The v1 consumer of ``channel.chat.message``
# runs a model for every event it can read, and it reads the viewer identity
# from this field, not from ``payload.author.id`` (P16 retires it). It is no
# part of the normalised event: it is attached, on the bus only, to the events
# the trigger accepted, so that a rejected trigger stays a traced decision that
# calls no model even while that consumer is still on the bus (R1, AC4).
_CONSUMER_BRIDGE_FIELD = "viewer_id"

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
    transport untouched. Read it through :attr:`TwitchModule.send_record`.
    """

    emitted: int = 0
    confirmed: int = 0
    failed: int = 0
    unknown: int = 0
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
    ) -> None:
        runtime = _runtime_surfaces(context)
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
        # send; awaited by ``close`` so a shutdown loses none of them.
        self._sent_traces: set[asyncio.Task[None]] = set()
        self._closed = False
        self._close_lock = asyncio.Lock()

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
                # the loop; the facts are published before the handle is gone.
                sent_traces = tuple(self._sent_traces)
                if sent_traces:
                    await asyncio.gather(*sent_traces, return_exceptions=True)

                await _close_session(self._session)

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
    ) -> _SendOutcome:
        """Emit one Helix send and classify its outcome honestly (R5).

        ``success`` needs the platform's ``is_sent`` acknowledgement with the
        identifier it assigned; ``error`` is a platform refusal or a request
        that never left; ``external_unknown`` is a request that left — or may
        have — with no readable answer. *on_emit* is called right before the
        request leaves, so the executor's invocation carries the emission
        signal from that instant on.
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
            self._confirm(message_id, route=route, correlation=correlation)
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
        self, message_id: str, *, route: str, correlation: Mapping[str, Any]
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

        ``record_and_emit`` still runs the record first, as R8's ordering
        primitive; that second write is a no-op, as the scheduler's is. A
        publication that fails is counted by supervision as a lost trace and
        never reaches back here — nothing is retried and nothing is sent
        again (AC28). :meth:`close` waits for the traces still in flight.
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
        way; only an accepted one carries the consumer bridge, so the bus
        consumer of the transition can run a model for nothing the trigger
        refused (R1, AC4). A malformed envelope raises ``ValueError`` for the
        receiver to report.
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
        # publication is the whole route, as before v2; with one, the bridge
        # follows its decision. An undecided event (the engine failed) is
        # published as a fact and bridged to nothing: fail closed.
        bridged = decision.accepted if decision is not None else engine is None
        published = _bridged_for_consumer(event, notification) if bridged else event
        try:
            await self._bus.publish(
                _CHAT_EVENT, published["payload"], published["metadata"]
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            self._diagnose("twitch notification publish: failed")

        if decision is None:
            return
        await self._trace_decision(decision)
        if decision.accepted and self._scheduler is not None:
            # The scheduler receives the normalised event itself: the bridge
            # is for the bus consumer alone.
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

    try:
        created = session_factory()
        session = await created if inspect.isawaitable(created) else created
    except Exception:
        _safe_report(reporter, "twitch transport: session creation failed")
        raise TwitchModuleError("twitch transport initialization failed") from None

    return TwitchModule(context, parsed, session, reporter, retry_delay)


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
        )
    except Exception:
        raise TwitchModuleError(
            f"twitch prepare: manifest declaration of {CHAT_WRITE_ACTION!r} is invalid"
        ) from None


def _bridged_for_consumer(
    event: Mapping[str, Any], notification: _Notification
) -> dict[str, Any]:
    """The bus copy of an accepted event, with the consumer bridge attached.

    A copy: the normalised event handed to the scheduler is left exactly as
    normalised, and the bridge field is a bus-only alias of the trusted
    viewer identity, never a second normalisation.
    """

    assert notification.author_id is not None
    return {
        **event,
        "payload": {**event["payload"], _CONSUMER_BRIDGE_FIELD: notification.author_id},
    }


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
    "MANIFEST_PATH",
    "MODULE_NAME",
    "PLATFORM",
    "SendRecord",
    "TwitchModule",
    "TwitchModuleError",
    "activate",
    "validate_settings",
]
