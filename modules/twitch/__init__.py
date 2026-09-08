"""Twitch EventSub chat source and Helix chat sink."""

from __future__ import annotations

import asyncio
import inspect
import json
import sys
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
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

_CHAT_EVENT = "channel.chat.message"
_CHAT_SEND_TIMEOUT_SECONDS = 10.0
_NON_RETRYABLE_CLOSE_CODES = {4001, 4003}
_STABLE_CONNECTION_SECONDS = 10.0


class TwitchModuleError(RuntimeError):
    """A Twitch operation failure whose text is safe to surface."""


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
        if not isinstance(settings, Mapping):
            raise TwitchModuleError("twitch configuration: settings must be a mapping")

        values: dict[str, str] = {}
        for name in (
            "client_id",
            "client_secret",
            "access_token",
            "broadcaster_id",
            "bot_user_id",
        ):
            value = settings.get(name)
            if not isinstance(value, str) or not value.strip():
                raise TwitchModuleError(
                    f"twitch configuration: field {name!r} is required"
                )
            values[name] = value
        return cls(**values)


class TwitchModule:
    """Own one Twitch HTTP session and an activation-lifetime dedupe set."""

    def __init__(
        self,
        bus: Any,
        settings: _Settings,
        session: Any,
        reporter: Callable[[str], None],
        retry_delay: Callable[[float], Awaitable[None]],
    ) -> None:
        self._bus = bus
        self._settings = settings
        self._session = session
        self._reporter = reporter
        self._retry_delay = retry_delay
        self._seen_message_ids: set[str] = set()
        self._publish_lock = asyncio.Lock()
        self._websocket: Any | None = None
        self._receive_task: asyncio.Task[None] | None = None
        self._handoff_tasks: set[asyncio.Task[None]] = set()
        self._active_send_tasks: set[asyncio.Task[Any]] = set()
        self._closed = False
        self._close_lock = asyncio.Lock()

    async def start(self) -> None:
        """Complete all readiness-critical setup and then start reception."""

        await self._authenticate()
        websocket = await self._connect(EVENTSUB_URL)
        try:
            session_id = await self._receive_welcome(websocket)
            await self._subscribe(session_id)
        except BaseException:
            await _close_websocket(websocket)
            raise

        self._websocket = websocket
        self._bus.subscribe("channel.chat.send", self.handle_chat_send)
        self._receive_task = asyncio.create_task(
            self._receive_forever(websocket),
            name="twitch-eventsub-receiver",
        )

    async def close(self) -> None:
        """Stop reception and close every owned transport exactly once."""

        async with self._close_lock:
            if self._closed:
                return
            self._closed = True

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

            current = asyncio.current_task()
            active_sends = tuple(
                task for task in self._active_send_tasks if task is not current
            )
            for send_task in active_sends:
                send_task.cancel()
            if active_sends:
                await asyncio.gather(*active_sends, return_exceptions=True)

            websocket = self._websocket
            self._websocket = None
            if websocket is not None:
                await _close_websocket(websocket)
            await _close_session(self._session)

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
            while not self._closed:
                result = await self._consume(websocket)
                if self._closed:
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
                if self._closed:
                    return

                replacement = None
                while replacement is None and not self._closed:
                    delay = min(0.25 * (2**retry_attempt), 5.0)
                    retry_attempt = min(retry_attempt + 1, 5)
                    await _resolve(self._retry_delay(delay))
                    if self._closed:
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

        while not self._closed:
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
                    await self._publish_notification(envelope)
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
    bus: Any,
    settings: Mapping[str, Any],
    catalog: Mapping[str, Mapping[str, Any]],
) -> TwitchModule:
    """Authenticate, establish EventSub, and return an async lifecycle handle."""

    del catalog  # Capabilities are declared by the colocated manifest.
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

    handle = TwitchModule(bus, parsed, session, reporter, retry_delay)
    try:
        await handle.start()
    except BaseException:
        await handle.close()
        raise
    return handle


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


__all__ = ["TwitchModule", "TwitchModuleError", "activate"]
