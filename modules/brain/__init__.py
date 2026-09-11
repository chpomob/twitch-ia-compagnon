"""Language-model run engine: manifest v2, module-owned settings validation (R7).

The manifest beside this package declares ``manifest_version: 2`` and names
:func:`validate_settings` as its ``settings_validator``. The loader runs that
hook for every enabled module before any of them is activated, so a
misconfigured engine is reported with 0 transports opened (R7, AC24). The hook
is a standalone function: it reads nothing from the run engine below and the
run engine reads nothing from it yet — the engine's own parsing, the tagged
``[send:...]`` output and the viewer-keyed memory are the compatibility
behaviour the next step replaces on the versioned runtime.

The limits the hook requires are the ones this module owns: the admission
scheduler's, the per-run budgets' and the conversation memory's. Each must be
present, numeric, finite and positive; an absent or unevaluable limit is a
diagnostic, never a pass, because an engine with an omitted or infinite bound
is not a usable engine (R2, R6).
"""

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
from urllib.parse import urlsplit

from core.bus import matches_event_pattern

try:  # Keep the module importable for transport-injected contract tests.
    import aiohttp
except ModuleNotFoundError:  # pragma: no cover - production installs dependencies
    aiohttp = None  # type: ignore[assignment]


MODULE_NAME = "brain"

_INPUT_EVENT = "channel.chat.message"
_TAG = re.compile(r"\[send:([^\]\r\n]+)\]")
_DEFAULT_TIMEOUT_SECONDS = 30.0
_DEFAULT_HISTORY_MESSAGES = 8
_DEFAULT_MAX_HISTORY_VIEWERS = 1_000

# --------------------------------------------------------------------------- #
# Module-owned settings validation (R7)
# --------------------------------------------------------------------------- #

_COUNT = "count"
"""A positive integer: works, sessions, workers, turns, tokens, bytes."""

_SECONDS = "seconds"
"""A finite, strictly positive number of seconds."""

_ENDPOINT_SCHEMES = frozenset({"http", "https"})
_ENV_REFERENCE = re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*\}\Z")

#: Every limit this module owns, by group, with its kind. The vocabulary is
#: the one the manifest's ``settings_schema`` declares; the hook checks what
#: that schema cannot — finiteness and strict positivity — and, called on its
#: own, also presence and type, so a handle built outside the loader is
#: refused on the same terms.
_OWNED_LIMITS: Mapping[str, Mapping[str, str]] = {
    "admission": {
        "session_queue_capacity": _COUNT,
        "global_pending_capacity": _COUNT,
        "max_sessions": _COUNT,
        "workers": _COUNT,
        "wait_seconds": _SECONDS,
        "total_run_seconds": _SECONDS,
    },
    "budget": {
        "model_turns": _COUNT,
        "model_call_seconds": _SECONDS,
        "action_seconds": _SECONDS,
        "max_tokens": _COUNT,
        "max_observation_bytes": _COUNT,
    },
    "conversation_memory": {
        "max_sessions": _COUNT,
        "max_exchanges": _COUNT,
        "max_bytes": _COUNT,
        "max_age_seconds": _SECONDS,
    },
}


def validate_settings(settings: Any) -> list[str]:
    """Check this module's settings; return one diagnostic per offending field.

    This is the hook ``settings_validator`` in the manifest names. The loader
    runs it for every enabled module before any of them is activated (R7).
    Each diagnostic names the module and the field and nothing else: the
    endpoint and the key are configured values, so no value is ever echoed
    (AC24). An empty list means the settings are accepted.

    Beyond the shape the schema states, the hook checks that the endpoint is
    a well-formed ``http(s)`` URL, that the model name is non-empty, that the
    key resolved to a non-empty string rather than a still-unresolved
    ``${NAME}`` reference, and that every owned limit is present, numeric,
    finite and positive.
    """

    if not isinstance(settings, Mapping):
        return [_setting_diagnostic("settings", "must be a mapping")]
    diagnostics: list[str] = []

    endpoint = settings.get("endpoint")
    if endpoint is None:
        diagnostics.append(_setting_diagnostic("endpoint", "is required"))
    elif not _is_well_formed_url(endpoint):
        diagnostics.append(
            _setting_diagnostic("endpoint", "must be a well-formed http(s) URL")
        )

    model = settings.get("model")
    if model is None:
        diagnostics.append(_setting_diagnostic("model", "is required"))
    elif not isinstance(model, str) or not model.strip():
        diagnostics.append(_setting_diagnostic("model", "must be a non-empty string"))

    api_key = settings.get("api_key")
    if api_key is None:
        diagnostics.append(_setting_diagnostic("api_key", "is required"))
    elif not isinstance(api_key, str) or not api_key.strip():
        diagnostics.append(
            _setting_diagnostic("api_key", "must be a non-empty string")
        )
    elif _ENV_REFERENCE.match(api_key.strip()):
        diagnostics.append(
            _setting_diagnostic("api_key", "is an unresolved environment reference")
        )

    for group, limits in _OWNED_LIMITS.items():
        section = settings.get(group)
        if section is None:
            diagnostics.append(_setting_diagnostic(group, "is required"))
            continue
        if not isinstance(section, Mapping):
            diagnostics.append(_setting_diagnostic(group, "must be a mapping of limits"))
            continue
        for field_name in section:
            if field_name not in limits:
                diagnostics.append(
                    _setting_diagnostic(
                        f"{group}.{field_name}", "is not a limit this module owns"
                    )
                )
        for field_name, kind in limits.items():
            reason = _limit_reason(section.get(field_name), kind)
            if reason is not None:
                diagnostics.append(_setting_diagnostic(f"{group}.{field_name}", reason))
    return diagnostics


def _limit_reason(value: Any, kind: str) -> str | None:
    """Why *value* is not an acceptable limit of *kind*, or ``None`` if it is."""

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


def _is_well_formed_url(value: Any) -> bool:
    if not isinstance(value, str) or not value or value != value.strip():
        return False
    if any(character.isspace() for character in value):
        return False
    try:
        parts = urlsplit(value)
        hostname = parts.hostname
        # ``port`` is parsed lazily: a non-numeric or out-of-range port raises
        # only when read, so it is read here to fail preflight rather than the
        # first request.
        parts.port
    except ValueError:
        return False
    return parts.scheme in _ENDPOINT_SCHEMES and bool(parts.netloc) and bool(hostname)


def _setting_diagnostic(field_name: str, reason: str) -> str:
    return f"module {MODULE_NAME!r}: field {field_name!r}: {reason}"


# --------------------------------------------------------------------------- #
# Run engine (compatibility behaviour until its migration)
# --------------------------------------------------------------------------- #


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
        self._active_handlers: dict[asyncio.Future[None], asyncio.Task[Any] | None] = {}
        self._close_lock = asyncio.Lock()
        self._closed = False

    async def handle_chat_message(self, event: Mapping[str, Any]) -> None:
        """Build one model request and atomically validate its tagged response."""

        task = asyncio.current_task()
        if self._closed:
            return
        completed = asyncio.get_running_loop().create_future()
        self._active_handlers[completed] = task
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
                "source": MODULE_NAME,
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
            self._active_handlers.pop(completed, None)
            if not completed.done():
                completed.set_result(None)

    async def close(self) -> None:
        """Stop future work and close the owned transport exactly once."""

        async with self._close_lock:
            if self._closed:
                return
            self._closed = True
            current = asyncio.current_task()
            active = tuple(
                completed
                for completed, owner in self._active_handlers.items()
                if owner is not current
            )
            if active:
                # A caller may be a long-lived receiver. Wait for its handler,
                # not for the entire task that happened to invoke it.
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
    runtime: Any,
    settings: Mapping[str, Any],
    catalog: Mapping[str, Mapping[str, Any]],
) -> BrainModule:
    """Create the configured model client and subscribe to incoming chat.

    The manifest declares v2, so the loader hands the module's scoped runtime
    context; a direct caller may still pass the bus itself. Either way the
    engine below speaks the bus contract only, until its migration takes the
    scheduler, the executor and supervision from that context.
    """

    bus = _bus_of(runtime)
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


def _bus_of(runtime: Any) -> Any:
    """The bus behind *runtime*: the bus itself, or the context that carries it."""

    if callable(getattr(runtime, "subscribe", None)) and callable(
        getattr(runtime, "publish", None)
    ):
        return runtime
    return runtime.bus


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
            if name == MODULE_NAME:
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


__all__ = [
    "MODULE_NAME",
    "BrainModule",
    "BrainModuleError",
    "activate",
    "validate_settings",
]
