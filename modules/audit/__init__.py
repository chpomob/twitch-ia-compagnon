"""Non-blocking, redacting JSON audit middleware."""

from __future__ import annotations

import asyncio
import inspect
import json
import sys
from collections.abc import Callable, Mapping
from contextlib import suppress
from typing import Any


_MODULE_NAME = "audit"
_PATTERN = "**"
_ORDER = 90
_REDACTED = "[REDACTED]"
_SENSITIVE_KEYS = frozenset(
    {
        "authorization",
        "client_secret",
        "access_token",
        "api_key",
        "credential",
        "credentials",
    }
)
_STOP = object()
_DEFAULT_CLOSE_TIMEOUT_SECONDS = 1.0


class AuditModuleError(RuntimeError):
    """An audit setup failure whose message contains no event data."""


def _write_stdout(line: str) -> None:
    """Write an already serialized record to the current process stdout."""

    sys.stdout.write(line)
    sys.stdout.flush()


# A module-level seam is useful to embedders that cannot put callables in config.
OUTPUT_WRITER: Callable[[str], Any] = _write_stdout


class AuditModule:
    """Queue audit records and write them outside the event publication chain."""

    def __init__(
        self,
        writer: Callable[[str], Any],
        *,
        close_timeout_seconds: float = _DEFAULT_CLOSE_TIMEOUT_SECONDS,
    ) -> None:
        self._writer = writer
        self._close_timeout_seconds = close_timeout_seconds
        self._queue: asyncio.Queue[str | object] = asyncio.Queue()
        self._writer_task = asyncio.create_task(
            self._write_records(), name="audit-stdout-writer"
        )
        self._closed = False
        self._close_lock = asyncio.Lock()

    def handle_event(self, event: Mapping[str, Any]) -> Mapping[str, Any]:
        """Enqueue one safe JSON line and return *event* without modification."""

        if not self._closed:
            try:
                record = {
                    "type": _redact(event.get("type")),
                    "payload": _redact(event.get("payload")),
                }
                line = json.dumps(
                    record,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    default=_json_fallback,
                ) + "\n"
                # The queue is intentionally unbounded: this operation has no
                # output I/O and cannot wait for the writer.
                self._queue.put_nowait(line)
            except Exception:
                # Observability must never alter publication success. This also
                # protects the bus from unusual Mapping implementations.
                pass
        return event

    async def close(self) -> None:
        """Drain queued records when possible, with bounded stuck-writer cleanup."""

        async with self._close_lock:
            if self._closed:
                return
            self._closed = True

            try:
                await asyncio.wait_for(
                    self._queue.join(), timeout=self._close_timeout_seconds
                )
            except (asyncio.TimeoutError, TimeoutError):
                self._writer_task.cancel()
            else:
                self._queue.put_nowait(_STOP)

            with suppress(asyncio.CancelledError, Exception):
                await self._writer_task

    async def _write_records(self) -> None:
        while True:
            item = await self._queue.get()
            try:
                if item is _STOP:
                    return
                assert isinstance(item, str)
                await _call_writer(self._writer, item)
            except asyncio.CancelledError:
                raise
            except Exception:
                # A failed record is isolated and the worker continues with the
                # next one. No event data is copied to a diagnostic channel.
                pass
            finally:
                self._queue.task_done()


async def activate(
    bus: Any,
    settings: Mapping[str, Any],
    catalog: Mapping[str, Mapping[str, Any]],
) -> AuditModule:
    """Validate the declared audit capability and register its middleware."""

    pattern, order = _catalog_contract(catalog)
    if not isinstance(settings, Mapping):
        raise AuditModuleError("audit configuration: settings must be a mapping")

    writer = settings.get("_writer", OUTPUT_WRITER)
    if not callable(writer):
        raise AuditModuleError("audit configuration: writer is invalid")

    close_timeout = settings.get(
        "_close_timeout_seconds", _DEFAULT_CLOSE_TIMEOUT_SECONDS
    )
    if (
        isinstance(close_timeout, bool)
        or not isinstance(close_timeout, (int, float))
        or close_timeout <= 0
    ):
        raise AuditModuleError(
            "audit configuration: close timeout must be positive"
        )

    handle = AuditModule(writer, close_timeout_seconds=float(close_timeout))
    try:
        bus.subscribe(pattern, handle.handle_event, order=order)
    except Exception:
        await handle.close()
        raise AuditModuleError("audit subscription: registration failed") from None
    return handle


def _catalog_contract(
    catalog: Mapping[str, Mapping[str, Any]],
) -> tuple[str, int]:
    try:
        manifest = catalog[_MODULE_NAME]
        valid = (
            isinstance(manifest, Mapping)
            and manifest.get("name") == _MODULE_NAME
            and manifest.get("produces") in ([], ())
            and tuple(manifest.get("consumes", ())) == (_PATTERN,)
            and manifest.get("middleware") is True
            and manifest.get("order") == _ORDER
            and not isinstance(manifest.get("order"), bool)
        )
    except (KeyError, TypeError):
        valid = False
    if not valid:
        raise AuditModuleError("audit capabilities: catalog contract is invalid")
    return manifest["consumes"][0], manifest["order"]


async def _call_writer(writer: Callable[[str], Any], line: str) -> None:
    """Run synchronous writers in a thread and await asynchronous writers."""

    if inspect.iscoroutinefunction(writer) or inspect.iscoroutinefunction(
        getattr(writer, "__call__", None)
    ):
        result = writer(line)
    else:
        result = await asyncio.to_thread(writer, line)
    if inspect.isawaitable(result):
        await result


def _redact(value: Any, active: set[int] | None = None) -> Any:
    """Return a JSON-oriented copy with sensitive mapping fields removed."""

    if active is None:
        active = set()

    if isinstance(value, Mapping):
        identity = id(value)
        if identity in active:
            return "[CIRCULAR]"
        active.add(identity)
        try:
            redacted: dict[Any, Any] = {}
            for key, item in value.items():
                output_key = (
                    key
                    if isinstance(key, (str, int, float, bool)) or key is None
                    else str(key)
                )
                if isinstance(key, str) and key.casefold() in _SENSITIVE_KEYS:
                    redacted[output_key] = _REDACTED
                else:
                    redacted[output_key] = _redact(item, active)
            return redacted
        finally:
            active.remove(identity)

    if isinstance(value, (list, tuple)):
        identity = id(value)
        if identity in active:
            return "[CIRCULAR]"
        active.add(identity)
        try:
            return [_redact(item, active) for item in value]
        finally:
            active.remove(identity)

    return value


def _json_fallback(value: Any) -> str:
    """Keep an unusual value from terminating the audit worker."""

    return f"<{type(value).__name__}>"


__all__ = ["AuditModule", "AuditModuleError", "activate"]
