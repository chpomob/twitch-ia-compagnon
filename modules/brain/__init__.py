"""Capability-driven language-model pipeline and tagged event router."""

from __future__ import annotations

import asyncio
import inspect
import math
import re
import sys
from collections import OrderedDict, deque
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from core.bus import matches_event_pattern

try:  # Keep the module importable for transport-injected contract tests.
    import aiohttp
except ModuleNotFoundError:  # pragma: no cover - production installs dependencies
    aiohttp = None  # type: ignore[assignment]


_INPUT_EVENT = "channel.chat.message"
_MODULE_NAME = "brain"
_TAG = re.compile(r"\[send:([^\]\r\n]+)\]")
_DEFAULT_TIMEOUT_SECONDS = 30.0
_DEFAULT_HISTORY_MESSAGES = 8
_DEFAULT_MAX_HISTORY_VIEWERS = 1_000


def _default_session_factory() -> Any:
    if aiohttp is None:
        raise RuntimeError("aiohttp is not installed")
    return aiohttp.ClientSession()


SESSION_FACTORY: Callable[[], Any] = _default_session_factory


class BrainModuleError(RuntimeError):
    """A brain operation failure whose text is safe to surface."""


@dataclass(frozen=True, repr=False, slots=True)
class _Settings:
    endpoint: str
    model: str
    api_key: str
    timeout_seconds: float
    history_messages: int
    max_history_viewers: int

    @classmethod
    def from_mapping(cls, settings: Mapping[str, Any]) -> "_Settings":
        if not isinstance(settings, Mapping):
            raise BrainModuleError("brain configuration: settings must be a mapping")

        strings: dict[str, str] = {}
        for name in ("endpoint", "model", "api_key"):
            value = settings.get(name)
            if not isinstance(value, str) or not value.strip():
                raise BrainModuleError(
                    f"brain configuration: field {name!r} is required"
                )
            strings[name] = value.strip()

        raw_timeout = settings.get(
            "timeout_seconds", settings.get("timeout", _DEFAULT_TIMEOUT_SECONDS)
        )
        if (
            isinstance(raw_timeout, bool)
            or not isinstance(raw_timeout, (int, float))
            or not math.isfinite(raw_timeout)
            or raw_timeout <= 0
        ):
            raise BrainModuleError(
                "brain configuration: field 'timeout_seconds' must be positive"
            )

        raw_history = settings.get("history_messages", _DEFAULT_HISTORY_MESSAGES)
        if (
            isinstance(raw_history, bool)
            or not isinstance(raw_history, int)
            or raw_history < 0
        ):
            raise BrainModuleError(
                "brain configuration: field 'history_messages' must be non-negative"
            )

        raw_history_viewers = settings.get(
            "max_history_viewers", _DEFAULT_MAX_HISTORY_VIEWERS
        )
        if (
            isinstance(raw_history_viewers, bool)
            or not isinstance(raw_history_viewers, int)
            or raw_history_viewers <= 0
        ):
            raise BrainModuleError(
                "brain configuration: field 'max_history_viewers' must be positive"
            )

        return cls(
            **strings,
            timeout_seconds=float(raw_timeout),
            history_messages=raw_history,
            max_history_viewers=raw_history_viewers,
        )


class BrainModule:
    """Own an HTTP session, viewer histories, and one chat subscription."""

    def __init__(
        self,
        bus: Any,
        settings: _Settings,
        session: Any,
        system_prompt: str,
        output_patterns: Sequence[str],
        reporter: Callable[[str], None],
    ) -> None:
        self._bus = bus
        self._settings = settings
        self._session = session
        self._system_prompt = system_prompt
        self._output_patterns = tuple(output_patterns)
        self._reporter = reporter
        self._histories: OrderedDict[str, deque[dict[str, str]]] = OrderedDict()
        self._history_lock = asyncio.Lock()
        self._active_handlers: set[asyncio.Task[Any]] = set()
        self._close_lock = asyncio.Lock()
        self._closed = False

    async def handle_chat_message(self, event: Mapping[str, Any]) -> None:
        """Build one model request and atomically validate its tagged response."""

        task = asyncio.current_task()
        if self._closed:
            return
        if task is not None:
            self._active_handlers.add(task)
        try:
            try:
                text, source_message_id, viewer_id = _incoming_context(event)
            except ValueError:
                self._diagnose("brain input: malformed chat message")
                return

            user_content = _format_user_context(text, source_message_id, viewer_id)
            async with self._history_lock:
                history = self._histories.get(viewer_id, ())
                if viewer_id in self._histories:
                    self._histories.move_to_end(viewer_id)
                messages = [
                    {"role": "system", "content": self._system_prompt},
                    *(dict(message) for message in history),
                    {"role": "user", "content": user_content},
                ]

            content = await self._request_model(messages)
            if content is None:
                return
            try:
                sections = _parse_sections(content, self._output_patterns)
            except ValueError:
                self._diagnose("brain model response: invalid tagged output")
                return

            metadata = {
                "source": _MODULE_NAME,
                "source_message_id": source_message_id,
                "viewer_id": viewer_id,
            }
            delivered: list[tuple[str, str]] = []
            for event_type, section_text in sections:
                try:
                    published = await self._bus.publish(
                        event_type,
                        {"text": section_text},
                        dict(metadata),
                    )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    self._diagnose("brain output publish: failed")
                    if delivered:
                        await self._remember(
                            viewer_id,
                            user_content,
                            _render_sections(delivered),
                        )
                    return
                # Bus acceptance records an attempt, not necessarily delivery.
                # Sinks report operational failure without stopping audit.
                if (
                    isinstance(published, Mapping)
                    and published["metadata"].get("delivery_status") == "failed"
                ):
                    continue
                delivered.append((event_type, section_text))

            if delivered:
                await self._remember(
                    viewer_id,
                    user_content,
                    content
                    if len(delivered) == len(sections)
                    else _render_sections(delivered),
                )
        finally:
            if task is not None:
                self._active_handlers.discard(task)

    async def close(self) -> None:
        """Stop future work and close the owned transport exactly once."""

        async with self._close_lock:
            if self._closed:
                return
            self._closed = True
            current = asyncio.current_task()
            active = tuple(
                task for task in self._active_handlers if task is not current
            )
            if active:
                await asyncio.gather(*active, return_exceptions=True)
            async with self._history_lock:
                self._histories.clear()
            await _close_session(self._session)

    async def _request_model(
        self, messages: list[dict[str, str]]
    ) -> str | None:
        async def perform() -> str:
            response: Any | None = None
            try:
                response = await _resolve(
                    self._session.post(
                        self._settings.endpoint,
                        headers={
                            "Authorization": f"Bearer {self._settings.api_key}",
                            "Content-Type": "application/json",
                        },
                        json={
                            "model": self._settings.model,
                            "messages": messages,
                        },
                    )
                )
                status = getattr(response, "status", None)
                if (
                    not isinstance(status, int)
                    or isinstance(status, bool)
                    or not 200 <= status < 300
                ):
                    raise _NonSuccessResponse
                try:
                    body = await _resolve(response.json())
                except asyncio.CancelledError:
                    raise
                except (asyncio.TimeoutError, TimeoutError):
                    raise
                except Exception:
                    raise _MalformedResponse from None
                return _response_content(body)
            finally:
                if response is not None:
                    await _release_response(response)

        try:
            return await asyncio.wait_for(
                perform(), timeout=self._settings.timeout_seconds
            )
        except asyncio.CancelledError:
            raise
        except (asyncio.TimeoutError, TimeoutError):
            self._diagnose("brain model request: timed out")
        except _NonSuccessResponse:
            self._diagnose("brain model response: non-success status")
        except _MalformedResponse:
            self._diagnose("brain model response: malformed data")
        except Exception:
            # Exception text and response bodies can contain credentials or
            # prompt content, so neither is copied into diagnostics.
            self._diagnose("brain model request: transport failed")
        return None

    async def _remember(
        self, viewer_id: str, user_content: str, content: str
    ) -> None:
        limit = self._settings.history_messages
        if limit == 0 or self._closed:
            return
        async with self._history_lock:
            if self._closed:
                return
            history = self._histories.get(viewer_id)
            if history is None:
                while len(self._histories) >= self._settings.max_history_viewers:
                    self._histories.popitem(last=False)
                history = deque(maxlen=limit)
                self._histories[viewer_id] = history
            else:
                self._histories.move_to_end(viewer_id)
            history.extend(
                (
                    {"role": "user", "content": user_content},
                    {"role": "assistant", "content": content},
                )
            )

    def _diagnose(self, message: str) -> None:
        _safe_report(self._reporter, message)


class _NonSuccessResponse(Exception):
    pass


class _MalformedResponse(Exception):
    pass


async def activate(
    bus: Any,
    settings: Mapping[str, Any],
    catalog: Mapping[str, Mapping[str, Any]],
) -> BrainModule:
    """Create the configured model client and subscribe to incoming chat."""

    parsed = _Settings.from_mapping(settings)
    reporter = settings.get("diagnostic_reporter", _default_reporter)
    if not callable(reporter):
        raise BrainModuleError("brain configuration: diagnostic reporter is invalid")
    session_factory = settings.get("_session_factory", SESSION_FACTORY)
    if not callable(session_factory):
        raise BrainModuleError("brain configuration: transport seam is invalid")

    system_prompt, output_patterns = _catalog_contract(catalog)
    try:
        created = session_factory()
        session = await created if inspect.isawaitable(created) else created
    except Exception:
        _safe_report(reporter, "brain transport: session creation failed")
        raise BrainModuleError("brain transport initialization failed") from None

    handle = BrainModule(
        bus,
        parsed,
        session,
        system_prompt,
        output_patterns,
        reporter,
    )
    try:
        bus.subscribe(_INPUT_EVENT, handle.handle_chat_message)
    except Exception:
        await handle.close()
        raise BrainModuleError("brain subscription: registration failed") from None
    return handle


def _catalog_contract(
    catalog: Mapping[str, Mapping[str, Any]],
) -> tuple[str, tuple[str, ...]]:
    if not isinstance(catalog, Mapping):
        raise BrainModuleError("brain capabilities: catalog must be a mapping")

    rows: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = []
    brain_produces: tuple[str, ...] | None = None
    try:
        for catalog_name, manifest in catalog.items():
            if not isinstance(catalog_name, str) or not isinstance(manifest, Mapping):
                raise ValueError
            name = manifest.get("name")
            produces = manifest.get("produces")
            consumes = manifest.get("consumes")
            if (
                not isinstance(name, str)
                or not name
                or not _string_sequence(produces)
                or not _string_sequence(consumes)
            ):
                raise ValueError
            produced = tuple(produces)
            consumed = tuple(consumes)
            rows.append((name, produced, consumed))
            if name == _MODULE_NAME:
                brain_produces = produced
    except Exception:
        raise BrainModuleError("brain capabilities: malformed catalog") from None

    if brain_produces is None or not brain_produces:
        raise BrainModuleError("brain capabilities: produces declaration is required")

    rows.sort(key=lambda row: row[0])
    capability_lines = [
        f"- {name}: produces [{', '.join(produces)}]; "
        f"consumes [{', '.join(consumes)}]"
        for name, produces, consumes in rows
    ]
    prompt = "\n".join(
        (
            "You are an event-routing companion.",
            "Loaded module capabilities:",
            *capability_lines,
            "Respond only with one or more non-empty sections in the form "
            "[send:<event.type>]text.",
            "Use only event types permitted by the brain module's produces "
            "capabilities.",
        )
    )
    return prompt, brain_produces


def _string_sequence(value: Any) -> bool:
    return (
        isinstance(value, Sequence)
        and not isinstance(value, (str, bytes, bytearray))
        and all(isinstance(item, str) and item for item in value)
    )


def _incoming_context(event: Mapping[str, Any]) -> tuple[str, str, str]:
    if not isinstance(event, Mapping):
        raise ValueError
    payload = event.get("payload")
    if not isinstance(payload, Mapping):
        raise ValueError

    text = payload.get("text")
    source_message_id = payload.get("message_id", payload.get("source_message_id"))
    viewer_id = payload.get("chatter_id", payload.get("viewer_id"))
    if not all(
        isinstance(value, str) and value.strip()
        for value in (text, source_message_id, viewer_id)
    ):
        raise ValueError
    return text, source_message_id, viewer_id


def _format_user_context(text: str, source_message_id: str, viewer_id: str) -> str:
    return (
        f"viewer_id: {viewer_id}\n"
        f"source_message_id: {source_message_id}\n"
        f"message: {text}"
    )


def _response_content(body: Any) -> str:
    try:
        choices = body["choices"]
        choice = choices[0]
        message = choice["message"]
        content = message["content"]
    except (KeyError, IndexError, TypeError):
        raise _MalformedResponse from None
    if not isinstance(content, str) or not content.strip():
        raise _MalformedResponse
    return content


def _parse_sections(
    content: str, output_patterns: Sequence[str]
) -> list[tuple[str, str]]:
    matches = list(_TAG.finditer(content))
    if not matches or content[: matches[0].start()].strip():
        raise ValueError

    sections: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        event_type = match.group(1)
        end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
        text = content[match.end() : end].strip()
        if (
            not text
            or not _valid_event_type(event_type)
            or not any(
                matches_event_pattern(pattern, event_type)
                for pattern in output_patterns
            )
        ):
            raise ValueError
        sections.append((event_type, text))
    return sections


def _valid_event_type(event_type: str) -> bool:
    if not event_type:
        return False
    segments = event_type.split(".")
    return all(segment and "*" not in segment for segment in segments)


def _render_sections(sections: Sequence[tuple[str, str]]) -> str:
    return "".join(
        f"[send:{event_type}]{text}" for event_type, text in sections
    )


async def _release_response(response: Any) -> None:
    release = getattr(response, "release", None)
    if callable(release):
        with suppress(Exception):
            result = release()
            if inspect.isawaitable(result):
                await result


async def _close_session(session: Any) -> None:
    close = getattr(session, "close", None)
    if callable(close):
        with suppress(Exception):
            result = close()
            if inspect.isawaitable(result):
                await result


async def _resolve(value: Awaitable[Any] | Any) -> Any:
    return await value if inspect.isawaitable(value) else value


def _safe_report(reporter: Callable[[str], None], message: str) -> None:
    with suppress(Exception):
        reporter(message)


def _default_reporter(message: str) -> None:
    print(message, file=sys.stderr)


__all__ = ["BrainModule", "BrainModuleError", "activate"]
