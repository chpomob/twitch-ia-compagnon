"""Twitch EventSub chat source and Helix chat sink, on the v2 runtime (R4, R7).

The manifest beside this package declares ``manifest_version: 2``, so the
loader hands :func:`activate` a scoped runtime context rather than the bus,
and the handle it returns is driven by the phase coordinator through the
hooks of the ``input`` role it declares:

``validate_settings``  The module-owned hook the manifest names. It reports
                       one diagnostic per offending field, naming module and
                       field and never the configured value (R7, AC24).
``activate``           Parses the settings and creates the HTTP session. It
                       opens no transport and produces no input.
``prepare``            Validates the credential and registers the chat-send
                       consumer on the bus. Still no input.
``start_inputs``       After the readiness barrier: opens the EventSub socket,
                       subscribes and starts the supervised receiver.
``stop_inputs``        Cuts the source. Accepted publications keep running
                       and the send transport stays open for them.
``drain``              Lets accepted publications finish inside the budget
                       and cancels the rest.
``close``              Releases every owned transport exactly once. It is
                       complete on its own, so a handle that never reached
                       ``stop_inputs`` — a failed startup — is still released.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import sys
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, fields
from typing import Any

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

_CHAT_EVENT = "channel.chat.message"
_CHAT_SEND_EVENT = "channel.chat.send"
_CHAT_SEND_TIMEOUT_SECONDS = 10.0
_NON_RETRYABLE_CLOSE_CODES = {4001, 4003}
_STABLE_CONNECTION_SECONDS = 10.0


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
    sequence (R4). Dedupe of platform redeliveries lives for the handle's
    lifetime, across socket replacements.
    """

    def __init__(
        self,
        bus: Any,
        tasks: Any,
        settings: _Settings,
        session: Any,
        reporter: Callable[[str], None],
        retry_delay: Callable[[float], Awaitable[None]],
    ) -> None:
        self._bus = bus
        self._tasks = tasks
        self._settings = settings
        self._session = session
        self._reporter = reporter
        self._retry_delay = retry_delay
        self._seen_message_ids: set[str] = set()
        self._publish_lock = asyncio.Lock()
        self._websocket: Any | None = None
        self._receive_task: asyncio.Task[None] | None = None
        self._handoff_tasks: set[asyncio.Task[None]] = set()
        self._publication_tasks: set[asyncio.Task[None]] = set()
        self._prepared = False
        self._stopping = False
        self._reception_lock = asyncio.Lock()
        self._active_send_tasks: set[asyncio.Task[Any]] = set()
        self._closed = False
        self._close_lock = asyncio.Lock()

    # -- startup phases ---------------------------------------------------- #

    async def prepare(self) -> None:
        """Validate the credential and register the send consumer.

        Readiness-critical and free of input: a rejected token stops startup
        here, before the barrier, and the ``channel.chat.send`` consumer is
        routed before any producer may publish into it.
        """

        if self._prepared or self._closed:
            return
        await self._authenticate()
        self._bus.subscribe(_CHAT_SEND_EVENT, self.handle_chat_send)
        self._prepared = True

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

                current = asyncio.current_task()
                active_sends = tuple(
                    task for task in self._active_send_tasks if task is not current
                )
                for send_task in active_sends:
                    send_task.cancel()
                if active_sends:
                    await asyncio.gather(*active_sends, return_exceptions=True)

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

    # -- chat send --------------------------------------------------------- #

    async def handle_chat_send(self, event: Mapping[str, Any]) -> dict[str, Any]:
        """Record an attempted send, with an explicit delivery outcome.

        Operational failures continue the bus chain so audit sees the attempt.
        Only a validated Helix acknowledgement means sent; brain must not infer
        delivery from bus acceptance. Cancellation propagates without a record.
        No response body or external exception text enters diagnostics/metadata.
        """

        def outcome(status: str) -> dict[str, Any]:
            return {
                **event,
                "metadata": {**event["metadata"], "delivery_status": status},
            }

        if self._closed:
            return outcome("failed")
        task = asyncio.current_task()
        if task is not None:
            self._active_send_tasks.add(task)
        try:
            try:
                payload = _mapping_at(event, "payload")
                text = _required_string(payload, "text")
                if not text.strip():
                    raise ValueError
                request_body = {
                    "broadcaster_id": self._settings.broadcaster_id,
                    "sender_id": self._settings.bot_user_id,
                    "message": text,
                }
                if "parent_message_id" in payload:
                    parent = _required_string(payload, "parent_message_id")
                    if not parent.strip():
                        raise ValueError
                    request_body["reply_parent_message_id"] = parent
            except ValueError:
                self._diagnose("twitch chat send: invalid input")
                return outcome("failed")

            try:
                status, body = await asyncio.wait_for(
                    self._post_chat(request_body), timeout=_CHAT_SEND_TIMEOUT_SECONDS
                )
            except asyncio.CancelledError:
                raise
            except asyncio.TimeoutError:
                self._diagnose("twitch chat send: request timed out")
                return outcome("failed")
            except Exception:
                self._diagnose("twitch chat send: request failed")
                return outcome("failed")

            if status != 200:
                self._diagnose(
                    f"twitch chat send: rejected (status {_safe_status(status)})"
                )
                return outcome("failed")
            data = body.get("data") if isinstance(body, Mapping) else None
            sent = data[0] if isinstance(data, list) and len(data) == 1 else None
            if (
                not isinstance(sent, Mapping)
                or not isinstance(sent.get("is_sent"), bool)
            ):
                self._diagnose("twitch chat send: malformed response")
                return outcome("failed")
            if not sent["is_sent"]:
                self._diagnose("twitch chat send: rejected (status 200)")
                return outcome("failed")
            message_id = sent.get("message_id")
            if not isinstance(message_id, str) or not message_id.strip():
                self._diagnose("twitch chat send: malformed response")
                return outcome("failed")
            return outcome("sent")
        finally:
            if task is not None:
                self._active_send_tasks.discard(task)

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

        mapped = {
            "broadcaster_id": _required_string(event, "broadcaster_user_id"),
            "chatter_id": _required_string(event, "chatter_user_id"),
            "chatter_name": _required_string(event, "chatter_user_name"),
            "message_id": _required_string(event, "message_id"),
            "text": _required_string(message, "text", allow_empty=True),
        }
        # Drop self echoes before they can trigger any bus consumer (brain).
        bot_user_id = getattr(self._settings, "bot_user_id", None)
        if (
            isinstance(bot_user_id, str)
            and bot_user_id.strip()
            and mapped["chatter_id"] == bot_user_id
        ):
            return

        message_id = mapped["message_id"]

        async with self._publish_lock:
            if message_id in self._seen_message_ids:
                return

            try:
                await self._bus.publish(
                    _CHAT_EVENT,
                    mapped,
                    {"source": "twitch"},
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                self._diagnose("twitch notification publish: failed")
                return

            # "Processed" means the bus accepted the publication. Keeping this
            # set for the handle's lifetime preserves dedupe across replacements.
            self._seen_message_ids.add(message_id)

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
    bus, tasks = _runtime_surfaces(context)
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

    return TwitchModule(bus, tasks, parsed, session, reporter, retry_delay)


def _runtime_surfaces(context: Any) -> tuple[Any, Any]:
    """The two runtime surfaces this module uses, checked by shape (AC26)."""

    bus = getattr(context, "bus", None)
    tasks = getattr(context, "tasks", None)
    if (
        bus is None
        or not callable(getattr(bus, "publish", None))
        or not callable(getattr(bus, "subscribe", None))
        or not callable(getattr(tasks, "spawn", None))
    ):
        raise TwitchModuleError("twitch activation: runtime context is invalid")
    return bus, tasks


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
    "MODULE_NAME",
    "TwitchModule",
    "TwitchModuleError",
    "activate",
    "validate_settings",
]
