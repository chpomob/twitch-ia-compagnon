"""Fictional second chat platform on the v2 runtime — a test fixture (R5, R1).

The manifest beside this package mirrors the shipped twitch one on platform
``fake``: an ``input`` producing schema-version-2 ``channel.chat.message``
events, a ``chat.write`` provider declaring the delivery capability, the
keyword trigger on the companion name. It is loaded by the same loader under
the same rules as a shipped module (immediate child directory, no symlink,
manifest validated at discovery, activation through the scoped runtime
context), so the AC1 scenario runs on it with ``core/`` and the brain
unchanged (AC31), and the delivery scenarios (AC50–AC57) drive it through
the real executor.

``validate_settings``  The hook the manifest names: one value-free diagnostic
                       per offending field (R7).
``activate``           Parses the settings; opens nothing.
``prepare``            Binds the ``chat.write`` provider over every configured
                       channel, registers the compatibility consumer and marks
                       the module ready. Still no input.
``start_inputs``       After the readiness barrier: publishes the scripted
                       ``feed`` at once (``after_event: null``) or subscribes
                       to ``after_event`` and publishes it on every occurrence
                       (plan decision 6).
``stop_inputs``        Cuts the source: later occurrences are ignored and
                       pending feed publications are cancelled.
``close``              Withdraws readiness. Complete on its own.

**Reception** follows the twitch order exactly (R1, R3): normalise once,
consult the trigger engine's dedup window, feed the chat context, decide,
publish, trace the decision, admit an accepted event to the scheduler.
:meth:`FakePlatform.inject` is the in-process door a test drives a message
through; the ``feed`` uses the same door. A fed message may carry
``author.roles`` with ``author.roles_provenance``: both are published on the
event (for ``users.read``, plan decision 5) and offered to the trigger engine
as provenance-tagged :class:`~core.triggers.TrustedClaim` instances, so an
``audience`` rule decides on attested facts only.

**Delivery** is scripted per channel through a :class:`FakeChatTransport`:
what left is recorded in ``sends``, and the next outcome is popped from
``outcomes`` — :data:`SUCCESS` (default), :data:`FAIL_BEFORE_EMISSION` (the
provider raises before signalling emission: the executor reports ``error``)
or :data:`FAIL_AFTER_EMISSION` (it raises after: ``external_unknown``, never a
confirmed send). The transports live in the module-level :data:`TRANSPORTS`
registry keyed by channel; a test that activates through the loader — which
imports this package under a private name — reaches the same objects through
the handle's :attr:`FakePlatform.transports`, or hands one in through the
``transport`` settings seam (used for every configured channel and registered
in :data:`TRANSPORTS` under each).
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from core.admission import Work
from core.context import ChatEntry
from core.contracts import (
    TRACE_INPUT_TRIGGER_ACCEPTED,
    TRACE_INPUT_TRIGGER_REJECTED,
    ActionObservation,
    ActionSpec,
    Destination,
    SessionKey,
)
from core.triggers import NORMALIZED_SCHEMA_VERSION, TriggerContext, TrustedClaim


MODULE_NAME = "fakeplatform"
"""``metadata.source`` of every event: the input name the trigger registry knows."""

PLATFORM = "fake"
"""``payload.platform`` of every event this input normalises (R3)."""

CHAT_WRITE_ACTION = "chat.write"
CHAT_WRITE_PROVIDER = "fake-chat"
"""Provider name in bindings and traces."""

MANIFEST_PATH = Path(__file__).with_name("module.yaml")

CHAT_EVENT = "channel.chat.message"
_CHAT_SEND_EVENT = "channel.chat.send"
_CHAT_SCOPE = "chat"

# Outcomes a transport can be scripted to produce for one send.
SUCCESS = "success"
FAIL_BEFORE_EMISSION = "FAIL_BEFORE_EMISSION"
FAIL_AFTER_EMISSION = "FAIL_AFTER_EMISSION"
OUTCOMES = frozenset({SUCCESS, FAIL_BEFORE_EMISSION, FAIL_AFTER_EMISSION})

_ERROR_INVALID_ARGUMENTS = "invalid_arguments"
_ERROR_UNSUPPORTED_DESTINATION = "unsupported_destination"
_ERROR_PROVIDER_CLOSED = "provider_closed"


class FakePlatformError(RuntimeError):
    """A fixture failure whose text is safe to surface."""


class FakeChatTransport:
    """The send edge behind ``chat.write`` on one channel: what actually left.

    ``outcomes`` is consumed one entry per send, :data:`SUCCESS` once empty.
    ``sends`` records every confirmed send with the call's correlation.
    """

    def __init__(self, *outcomes: str) -> None:
        for outcome in outcomes:
            if outcome not in OUTCOMES:
                raise FakePlatformError(
                    f"fake transport: unknown scripted outcome {outcome!r}"
                )
        self.outcomes: list[str] = list(outcomes)
        self.sends: list[dict[str, Any]] = []
        self.attempts: int = 0

    def script(self, *outcomes: str) -> "FakeChatTransport":
        """Queue *outcomes* for the next sends; returns self for chaining."""

        for outcome in outcomes:
            if outcome not in OUTCOMES:
                raise FakePlatformError(
                    f"fake transport: unknown scripted outcome {outcome!r}"
                )
        self.outcomes.extend(outcomes)
        return self

    def next_outcome(self) -> str:
        self.attempts += 1
        return self.outcomes.pop(0) if self.outcomes else SUCCESS


def _next_outcome(transport: Any) -> str:
    """Pop the next scripted outcome of *transport*, :data:`SUCCESS` once empty.

    Duck-typed over ``outcomes``/``sends`` so a transport handed in through
    the seam needs no particular class; an outcome outside the vocabulary is
    a fixture misuse, refused before anything is emitted.
    """

    if isinstance(transport, FakeChatTransport):
        return transport.next_outcome()
    outcomes = transport.outcomes
    outcome = str(outcomes.pop(0)).upper() if outcomes else SUCCESS
    if outcome == SUCCESS.upper():
        outcome = SUCCESS
    if outcome not in OUTCOMES:
        raise FakePlatformError(f"fake transport: unknown scripted outcome {outcome!r}")
    return outcome


TRANSPORTS: dict[str, FakeChatTransport] = {}
"""Module-level registry of transports keyed by channel identifier.

``prepare`` reads one per configured channel, creating it when absent; a
test scripts outcomes here (or on :attr:`FakePlatform.transports`) before
the send it wants to fail. :func:`reset` clears it between scenarios.
"""


def transport_for(channel_id: str) -> FakeChatTransport:
    """The registered transport of *channel_id*, created when absent."""

    transport = TRANSPORTS.get(channel_id)
    if transport is None:
        transport = TRANSPORTS[channel_id] = FakeChatTransport()
    return transport


def reset() -> None:
    """Forget every registered transport."""

    TRANSPORTS.clear()


# --------------------------------------------------------------------------- #
# Settings (R7)
# --------------------------------------------------------------------------- #


def validate_settings(settings: Any) -> list[str]:
    """Check this module's settings; one value-free diagnostic per field."""

    if not isinstance(settings, Mapping):
        return [_setting_diagnostic("settings", "must be a mapping")]
    diagnostics: list[str] = []

    channels = settings.get("channel_ids")
    if (
        isinstance(channels, str)
        or not isinstance(channels, (list, tuple))
        or not channels
        or any(not isinstance(item, str) or not item.strip() for item in channels)
    ):
        diagnostics.append(
            _setting_diagnostic("channel_ids", "must be a non-empty list of channel identifiers")
        )
        channels = ()
    elif len(set(channels)) != len(channels):
        diagnostics.append(_setting_diagnostic("channel_ids", "must be unique"))

    companion_name = settings.get("companion_name")
    if not isinstance(companion_name, str) or not companion_name.strip():
        diagnostics.append(
            _setting_diagnostic("companion_name", "must be a non-empty string")
        )

    if "feed" in settings and settings["feed"] is not None:
        diagnostics.extend(_validate_feed(settings["feed"], set(channels)))

    transport = settings.get("transport")
    if transport is not None and not _is_transport(transport):
        diagnostics.append(
            _setting_diagnostic("transport", "must expose sends and outcomes lists")
        )
    return diagnostics


def _validate_feed(feed: Any, channels: set[str]) -> list[str]:
    if not isinstance(feed, Mapping):
        return [_setting_diagnostic("feed", "must be a mapping")]
    diagnostics: list[str] = []
    messages = feed.get("messages")
    if isinstance(messages, str) or not isinstance(messages, (list, tuple)):
        diagnostics.append(_setting_diagnostic("feed.messages", "must be a list"))
    else:
        for index, message in enumerate(messages):
            diagnostics.extend(
                _validate_message(message, channels, f"feed.messages[{index}]")
            )
    after_event = feed.get("after_event")
    if after_event is not None:
        if (
            not isinstance(after_event, str)
            or not after_event.strip()
            or any(character.isspace() for character in after_event)
        ):
            diagnostics.append(
                _setting_diagnostic("feed.after_event", "must be an event type or null")
            )
        elif after_event == CHAT_EVENT:
            diagnostics.append(
                _setting_diagnostic(
                    "feed.after_event",
                    "must not be the event this module produces (it would feed itself)",
                )
            )
    return diagnostics


def _validate_message(message: Any, channels: set[str], label: str) -> list[str]:
    """Check one fed message; *channels* empty means "do not check membership"."""

    if not isinstance(message, Mapping):
        return [_setting_diagnostic(label, "must be a mapping")]
    diagnostics: list[str] = []
    for key in ("channel_id", "message_id"):
        value = message.get(key)
        if not isinstance(value, str) or not value.strip():
            diagnostics.append(
                _setting_diagnostic(f"{label}.{key}", "must be a non-empty string")
            )
    channel_id = message.get("channel_id")
    if channels and isinstance(channel_id, str) and channel_id not in channels:
        diagnostics.append(
            _setting_diagnostic(f"{label}.channel_id", "must name a configured channel")
        )
    if not isinstance(message.get("text"), str):
        diagnostics.append(_setting_diagnostic(f"{label}.text", "must be a string"))
    diagnostics.extend(_validate_author(message.get("author"), f"{label}.author"))
    return diagnostics


def _validate_author(author: Any, label: str) -> list[str]:
    if isinstance(author, str):
        return [] if author.strip() else [_setting_diagnostic(label, "must not be blank")]
    if not isinstance(author, Mapping):
        return [_setting_diagnostic(label, "must be an identifier or a mapping")]
    diagnostics: list[str] = []
    author_id = author.get("id")
    if not isinstance(author_id, str) or not author_id.strip():
        diagnostics.append(_setting_diagnostic(f"{label}.id", "must be a non-empty string"))
    display_name = author.get("display_name")
    if display_name is not None and (
        not isinstance(display_name, str) or not display_name.strip()
    ):
        diagnostics.append(
            _setting_diagnostic(f"{label}.display_name", "must be a non-empty string")
        )
    roles = author.get("roles")
    provenance = author.get("roles_provenance")
    if roles is not None:
        if (
            isinstance(roles, str)
            or not isinstance(roles, (list, tuple))
            or any(not isinstance(role, str) or not role.strip() for role in roles)
        ):
            diagnostics.append(
                _setting_diagnostic(f"{label}.roles", "must be a list of role names")
            )
        if not isinstance(provenance, str) or not provenance.strip():
            diagnostics.append(
                _setting_diagnostic(
                    f"{label}.roles_provenance",
                    "is required with roles: an untagged role attests nothing",
                )
            )
    elif provenance is not None:
        diagnostics.append(
            _setting_diagnostic(f"{label}.roles_provenance", "requires roles")
        )
    return diagnostics


def _setting_diagnostic(field_name: str, reason: str) -> str:
    return f"module {MODULE_NAME!r}: field {field_name!r}: {reason}"


def _is_transport(value: Any) -> bool:
    return isinstance(getattr(value, "sends", None), list) and isinstance(
        getattr(value, "outcomes", None), list
    )


@dataclass(frozen=True, slots=True)
class _Message:
    """One fed or injected message, normalised exactly once (R3)."""

    channel_id: str
    author_id: str
    display_name: str | None
    roles: tuple[str, ...] | None
    roles_provenance: str | None
    message_id: str
    text: str

    @classmethod
    def from_mapping(cls, value: Any) -> "_Message":
        """Parse one message; ``ValueError`` (with the diagnostic) on a defect.

        Accepts the ``feed.messages[]`` shape, and a full event carrying a
        ``payload`` of the same shape (the ``platform`` key, if any, is
        ignored: everything injected here is on this platform).
        """

        if isinstance(value, Mapping) and isinstance(value.get("payload"), Mapping):
            value = value["payload"]
        diagnostics = _validate_message(value, set(), "message")
        if diagnostics:
            raise ValueError(diagnostics[0])
        author = value["author"]
        if isinstance(author, str):
            return cls(
                channel_id=value["channel_id"],
                author_id=author,
                display_name=None,
                roles=None,
                roles_provenance=None,
                message_id=value["message_id"],
                text=value["text"],
            )
        roles = author.get("roles")
        return cls(
            channel_id=value["channel_id"],
            author_id=author["id"],
            display_name=author.get("display_name"),
            roles=None if roles is None else tuple(roles),
            roles_provenance=author.get("roles_provenance"),
            message_id=value["message_id"],
            text=value["text"],
        )

    def with_message_id(self, message_id: str) -> "_Message":
        return _Message(
            channel_id=self.channel_id,
            author_id=self.author_id,
            display_name=self.display_name,
            roles=self.roles,
            roles_provenance=self.roles_provenance,
            message_id=message_id,
            text=self.text,
        )

    def event(self) -> dict[str, Any]:
        """The schema-version-2 bus event this message normalises to."""

        author: dict[str, Any] = {"id": self.author_id}
        if self.display_name is not None:
            author["display_name"] = self.display_name
        if self.roles is not None and self.roles_provenance is not None:
            author["roles"] = list(self.roles)
            author["roles_provenance"] = self.roles_provenance
        return {
            "type": CHAT_EVENT,
            "payload": {
                "platform": PLATFORM,
                "channel_id": self.channel_id,
                "author": author,
                "message_id": self.message_id,
                "text": self.text,
            },
            "metadata": {
                "source": MODULE_NAME,
                "schema_version": NORMALIZED_SCHEMA_VERSION,
            },
        }

    def claims(self) -> tuple[TrustedClaim, ...]:
        """The roles as provenance-tagged claims; none without a provenance."""

        if self.roles is None or self.roles_provenance is None:
            return ()
        return tuple(
            TrustedClaim(role, provenance=self.roles_provenance) for role in self.roles
        )


@dataclass(frozen=True, slots=True)
class _Settings:
    channel_ids: tuple[str, ...]
    companion_name: str
    feed_messages: tuple[_Message, ...]
    after_event: str | None

    @classmethod
    def from_mapping(cls, settings: Mapping[str, Any]) -> "_Settings":
        diagnostics = validate_settings(settings)
        if diagnostics:
            raise FakePlatformError(diagnostics[0])
        feed = settings.get("feed") or {}
        return cls(
            channel_ids=tuple(settings["channel_ids"]),
            companion_name=settings["companion_name"],
            feed_messages=tuple(
                _Message.from_mapping(message) for message in feed.get("messages", ())
            ),
            after_event=feed.get("after_event"),
        )


class _ChatWriteProvider:
    """The ``chat.write`` provider the executor invokes (R5)."""

    __slots__ = ("_module",)

    name = CHAT_WRITE_PROVIDER

    def __init__(self, module: "FakePlatform") -> None:
        self._module = module

    async def invoke(self, invocation: Any) -> ActionObservation:
        return await self._module._invoke_chat_write(invocation)


class FakePlatform:
    """The v2 handle: phase hooks, the in-process door, scripted sends."""

    def __init__(self, context: Any, settings: _Settings, reporter: Any) -> None:
        self._bus = context.bus
        self._tasks = context.tasks
        self._actions = context.actions
        self._supervision = context.supervision
        self._triggers = getattr(context, "triggers", None)
        self._chat = getattr(context, "chat", None)
        self._scheduler = getattr(context, "scheduler", None)
        self._settings = settings
        self._reporter = reporter
        self._provider = _ChatWriteProvider(self)
        self._transports: dict[str, FakeChatTransport] = {}
        self._sends: list[dict[str, Any]] = []
        self._publish_lock = asyncio.Lock()
        self._feed_tasks: set[asyncio.Task[Any]] = set()
        self._feed_occurrences = 0
        self._prepared = False
        self._started = False
        self._stopping = False
        self._closed = False
        self.injected: list[dict[str, Any]] = []
        """Every event this handle published, feed and injections alike."""

    # -- test seams -------------------------------------------------------- #

    @property
    def transports(self) -> Mapping[str, FakeChatTransport]:
        """The transport of each configured channel (the registry's objects)."""

        return self._transports

    def transport(self, channel_id: str | None = None) -> FakeChatTransport:
        """The transport of *channel_id*, or of the first configured channel."""

        target = self._settings.channel_ids[0] if channel_id is None else channel_id
        transport = self._transports.get(target)
        if transport is None:
            raise FakePlatformError(
                f"fake platform: no transport for channel {target!r} (prepare first)"
            )
        return transport

    @property
    def sends(self) -> list[dict[str, Any]]:
        """Every confirmed send through this handle, once each, in send order.

        Kept as the handle's own log rather than gathered from the
        transports: channels may share one transport (the ``transport``
        setting registers it for every channel), and a per-channel gather
        would repeat its records once per channel and lose cross-channel
        ordering.
        """

        return list(self._sends)

    @property
    def feed_occurrences(self) -> int:
        """How many times the scripted feed was published."""

        return self._feed_occurrences

    # -- startup phases ---------------------------------------------------- #

    async def prepare(self) -> None:
        """Bind the provider over every channel, route the consumer, mark ready."""

        if self._prepared or self._closed:
            return
        for channel_id in self._settings.channel_ids:
            self._transports[channel_id] = transport_for(channel_id)
        spec = _declared_chat_write_spec()
        destinations = [
            Destination(platform=PLATFORM, channel_id=channel_id, scope=_CHAT_SCOPE)
            for channel_id in self._settings.channel_ids
        ]
        try:
            self._actions.register(
                spec,
                self._provider,
                destinations=destinations,
                provider_name=CHAT_WRITE_PROVIDER,
            )
        except Exception as exc:
            self._diagnose(f"fakeplatform prepare: {exc}")
            raise FakePlatformError("fakeplatform prepare: action binding failed") from None
        self._bus.subscribe(_CHAT_SEND_EVENT, self.handle_chat_send)
        self._actions.mark_ready()
        self._prepared = True

    async def start_inputs(self) -> None:
        """Open the source: publish the feed now, or on each ``after_event``."""

        if self._started or self._stopping or self._closed:
            return
        if not self._prepared:
            raise FakePlatformError("fakeplatform lifecycle: start_inputs requires prepare")
        self._started = True
        if not self._settings.feed_messages:
            return
        if self._settings.after_event is None:
            await self._publish_feed()
            return
        self._bus.subscribe(self._settings.after_event, self._on_after_event)

    # -- shutdown phases --------------------------------------------------- #

    async def stop_inputs(self) -> None:
        self._stopping = True
        await self._cancel_feed_tasks()

    async def close(self) -> None:
        if self._closed:
            return
        self._stopping = True
        await self._cancel_feed_tasks()
        self._closed = True
        if self._prepared:
            with suppress(Exception):
                self._actions.mark_not_ready()

    async def _cancel_feed_tasks(self) -> None:
        pending = tuple(task for task in self._feed_tasks if not task.done())
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._feed_tasks.clear()

    # -- the scripted feed (plan decision 6) ------------------------------- #

    def _on_after_event(self, event: Mapping[str, Any]) -> None:
        """One occurrence of ``after_event``: publish the feed off the chain.

        Handed to a supervised task rather than awaited inside the other
        publication's chain, so the feed neither holds nor fails the
        publisher of the event it follows.
        """

        del event
        if not self._started or self._stopping or self._closed:
            return
        try:
            task = self._tasks.spawn(self._publish_feed(), name="fakeplatform-feed")
        except Exception:
            self._diagnose("fakeplatform feed: could not be scheduled")
            return
        self._feed_tasks.add(task)
        task.add_done_callback(self._feed_tasks.discard)

    async def _publish_feed(self) -> None:
        self._feed_occurrences += 1
        occurrence = self._feed_occurrences
        for message in self._settings.feed_messages:
            if occurrence > 1:
                # A replay must not be swallowed by the dedup window: the
                # identifier is suffixed with the occurrence number.
                message = message.with_message_id(f"{message.message_id}#{occurrence}")
            await self._drive(message)

    # -- the in-process door ----------------------------------------------- #

    async def inject(self, message: Any) -> dict[str, Any] | None:
        """Drive one message through reception, as the platform would.

        *message* has the ``feed.messages[]`` shape (``author`` an identifier
        or a mapping), or is a full event carrying such a ``payload``. Returns
        the published event, or ``None`` when nothing was published (a
        redelivery inside the dedup window, an unconfigured channel).
        """

        if not self._prepared or self._closed:
            raise FakePlatformError("fakeplatform inject: requires prepare, before close")
        try:
            parsed = _Message.from_mapping(message)
        except ValueError as exc:
            raise FakePlatformError(f"fakeplatform inject: {exc}") from None
        return await self._drive(parsed)

    async def _drive(self, message: _Message) -> dict[str, Any] | None:
        """Normalise, dedup, feed, decide, publish, trace, admit (R1 order)."""

        if message.channel_id not in self._settings.channel_ids:
            self._diagnose("fakeplatform reception: unconfigured channel")
            return None

        engine = self._triggers
        async with self._publish_lock:
            if engine is not None and engine.recorded(
                platform=PLATFORM,
                channel_id=message.channel_id,
                source_event_id=message.message_id,
            ) is not None:
                return None
            event = message.event()
            self._feed_chat_context(message)
            decision = None
            if engine is not None:
                try:
                    decision = engine.evaluate(
                        event, context=TriggerContext(message.claims())
                    )
                except Exception:
                    self._diagnose("fakeplatform trigger evaluation: failed")

        published: dict[str, Any] | None = None
        try:
            published = await self._bus.publish(
                CHAT_EVENT, event["payload"], event["metadata"]
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            # Diagnosed, never raised: the decision below still stands, as
            # on the shipped platform.
            self._diagnose("fakeplatform publish: failed")
        if published is not None:
            self.injected.append(published)

        if decision is not None:
            await self._trace_decision(decision)
            if decision.accepted and self._scheduler is not None:
                self._admit(message, event)
        return published

    def _feed_chat_context(self, message: _Message) -> None:
        if self._chat is None:
            return
        try:
            self._chat.append(
                PLATFORM,
                message.channel_id,
                ChatEntry(
                    author_id=message.author_id,
                    message_id=message.message_id,
                    text=message.text,
                ),
            )
        except Exception:
            self._diagnose("fakeplatform chat context: append failed")

    async def _trace_decision(self, decision: Any) -> None:
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
            self._diagnose("fakeplatform trigger trace: publication failed")

    def _admit(self, message: _Message, event: Mapping[str, Any]) -> None:
        try:
            session_key = SessionKey(
                platform=PLATFORM,
                channel_id=message.channel_id,
                viewer_id=message.author_id,
            )
            work = Work(
                payload=event, source_event_id=message.message_id, kind="chat.message"
            )
            self._scheduler.admit(session_key, work)
        except Exception:
            self._diagnose("fakeplatform admission: failed")

    # -- chat send: one service, two routes (R5) --------------------------- #

    async def _invoke_chat_write(self, invocation: Any) -> ActionObservation:
        """Serve one ``chat.write`` call for the executor, per the script.

        Emission is signalled before the scripted send "leaves", so a
        :data:`FAIL_AFTER_EMISSION` is classified ``external_unknown`` by the
        executor and a :data:`FAIL_BEFORE_EMISSION` as a plain ``error``.
        """

        invocation.mark_not_emitted()
        call = invocation.call
        channel_id = call.destination.channel_id
        provenance = {
            "provider": CHAT_WRITE_PROVIDER,
            "platform": PLATFORM,
            "channel_id": channel_id,
            "route": CHAT_WRITE_ACTION,
        }

        def failure(code: str, message: str) -> ActionObservation:
            return ActionObservation(
                status="error",
                provenance=provenance,
                error={"code": code, "message": message, "retryable": False},
            )

        if self._closed:
            return failure(_ERROR_PROVIDER_CLOSED, "fakeplatform chat send: closed")
        text = call.arguments.get("text")
        parent = call.arguments.get("parent_message_id")
        blank_parent = parent is not None and (
            not isinstance(parent, str) or not parent.strip()
        )
        if not isinstance(text, str) or not text.strip() or blank_parent:
            return failure(
                _ERROR_INVALID_ARGUMENTS,
                "text and parent_message_id must be non-blank strings",
            )
        if (
            call.destination.platform != PLATFORM
            or channel_id not in self._settings.channel_ids
        ):
            return failure(
                _ERROR_UNSUPPORTED_DESTINATION,
                "destination is outside the configured channels",
            )

        transport = self._transports[channel_id]
        outcome = _next_outcome(transport)
        if outcome == FAIL_BEFORE_EMISSION:
            raise FakePlatformError("fakeplatform chat send: transport unavailable")
        invocation.mark_emitted()
        if outcome == FAIL_AFTER_EMISSION:
            raise FakePlatformError("fakeplatform chat send: no confirmation")
        message_id = f"{PLATFORM}-{channel_id}-{len(transport.sends) + 1}"
        self._record_send(
            transport,
            {
                "text": text,
                "parent_message_id": parent,
                "message_id": message_id,
                "destination": call.destination,
                "principal": call.principal,
                "run_id": call.run_id,
                "call_id": call.call_id,
                "conversation_id": call.conversation_id,
                "source_event_id": call.source_event_id,
                "source_message_id": call.message_id,
            },
        )
        return ActionObservation(
            status="success",
            provenance=provenance,
            result={
                "message_id": message_id,
                "destination": {"platform": PLATFORM, "channel_id": channel_id},
            },
        )

    async def handle_chat_send(self, event: Mapping[str, Any]) -> dict[str, Any]:
        """The compatibility ``channel.chat.send`` route: a scripted send too."""

        def outcome(status: str, terminal: str) -> dict[str, Any]:
            return {
                **event,
                "metadata": {
                    **event["metadata"],
                    "delivery_status": status,
                    "delivery_outcome": terminal,
                },
            }

        payload = event.get("payload")
        text = payload.get("text") if isinstance(payload, Mapping) else None
        channel_id = payload.get("channel_id") if isinstance(payload, Mapping) else None
        if channel_id is None and len(self._settings.channel_ids) == 1:
            channel_id = self._settings.channel_ids[0]
        if (
            self._closed
            or not isinstance(text, str)
            or not text.strip()
            or channel_id not in self._transports
        ):
            return outcome("failed", "error")
        transport = self._transports[channel_id]
        scripted = _next_outcome(transport)
        if scripted == FAIL_BEFORE_EMISSION:
            return outcome("failed", "error")
        if scripted == FAIL_AFTER_EMISSION:
            return outcome("failed", "external_unknown")
        self._record_send(
            transport,
            {
                "text": text,
                "parent_message_id": payload.get("parent_message_id"),
                "message_id": f"{PLATFORM}-{channel_id}-{len(transport.sends) + 1}",
                "destination": Destination(PLATFORM, channel_id, _CHAT_SCOPE),
                "route": _CHAT_SEND_EVENT,
            },
        )
        return outcome("sent", "success")

    def _record_send(self, transport: Any, send: dict[str, Any]) -> None:
        """Log a confirmed send on its transport and in the handle's own order."""

        transport.sends.append(send)
        self._sends.append(send)

    def _diagnose(self, message: str) -> None:
        try:
            self._reporter(message)
        except Exception:
            pass


async def activate(
    context: Any,
    settings: Mapping[str, Any],
    catalog: Mapping[str, Mapping[str, Any]],
) -> FakePlatform:
    """Build the handle from the scoped runtime context; nothing is opened."""

    del catalog  # Capabilities are declared by the colocated manifest.
    for name, methods in (
        ("bus", ("publish", "subscribe")),
        ("tasks", ("spawn",)),
        ("actions", ("register", "mark_ready", "mark_not_ready")),
        ("supervision", ("emit",)),
    ):
        surface = getattr(context, name, None)
        if surface is None or not all(callable(getattr(surface, m, None)) for m in methods):
            raise FakePlatformError("fakeplatform activation: runtime context is invalid")
    parsed = _Settings.from_mapping(settings)
    reporter = settings.get("diagnostic_reporter", _default_reporter)
    if not callable(reporter):
        raise FakePlatformError("fakeplatform configuration: diagnostic reporter is invalid")
    transport = settings.get("transport")
    if transport is not None:
        # The seam: one transport for every configured channel, registered so
        # the module-level view and the handle's agree.
        for channel_id in parsed.channel_ids:
            TRANSPORTS[channel_id] = transport
    return FakePlatform(context, parsed, reporter)


def _declared_chat_write_spec() -> ActionSpec:
    """Build the ``chat.write`` contract from the colocated manifest (R5)."""

    try:
        manifest = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))
        entry = next(
            item
            for item in manifest["actions"]
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
            delivery=entry.get("delivery"),
        )
    except Exception:
        raise FakePlatformError(
            f"fakeplatform prepare: manifest declaration of {CHAT_WRITE_ACTION!r} is invalid"
        ) from None


def _default_reporter(message: str) -> None:
    print(message, file=sys.stderr)
