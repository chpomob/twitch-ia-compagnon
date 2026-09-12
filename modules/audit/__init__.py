"""Non-blocking, redacting JSON audit middleware on the v2 runtime (R4, R6, R8).

The audit stage sees every publication last (``order: 90``) and writes one
redacted JSON line per event — ``type``, ``payload`` and ``metadata`` — from a
worker that runs outside the publication chain. Three guarantees hold:

**Admission never blocks (R6).** The record queue is bounded by record count
*and* by serialized bytes. Offering a record is a synchronous, non-blocking
step of the publication chain; when either bound is reached the **newly
offered** record is dropped, never an older one, so what was admitted is
never rewritten. Every drop increments the audit-loss counter.

**The loss counter is readable outside the queue (R6, R8).** A loss is
counted in memory on the handle and, when the module runs on the versioned
runtime, in supervision's counter registry under
``COUNTER_AUDIT_RECORD_LOSSES``. It is never reported through a bus event:
such an event would feed the very queue whose saturation it reports.

**Audit failure never reaches an effect (R8, AC28).** Every terminal state is
recorded by its owner before its trace is published, so this stage only ever
sees an already-final state; a failed or dropped record is counted and the
worker moves on. Nothing here has a path back into an external effect.

Lifecycle: the manifest declares the ``observation`` role, so the coordinator
calls ``prepare`` (register the middleware), then — after every ordinary
resource closed — ``flush(deadline)`` and ``close``. The coordinator reads the
declared role; the name ``audit`` is not consulted anywhere (R4).
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import sys
from collections.abc import Callable, Mapping
from contextlib import suppress
from pathlib import Path
from typing import Any

from core.contracts import COUNTER_AUDIT_RECORD_LOSSES


MODULE_NAME = "audit"
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

#: Constructor defaults for a handle built outside the loader. A configured
#: deployment never relies on them: both limits are required settings,
#: validated finite and positive before activation (R6, AC32).
DEFAULT_MAX_RECORDS = 1000
DEFAULT_MAX_BYTES = 4 * 1024 * 1024

#: The two stream names ``output`` accepts besides a file path.
OUTPUT_STDOUT = "stdout"
OUTPUT_STDERR = "stderr"

_QUEUE_LIMITS = ("max_records", "max_bytes")


class AuditModuleError(RuntimeError):
    """An audit setup failure whose message contains no event data."""


def _write_stdout(line: str) -> None:
    """Write an already serialized record to the current process stdout."""

    sys.stdout.write(line)
    sys.stdout.flush()


def _write_stderr(line: str) -> None:
    """Write an already serialized record to the current process stderr."""

    sys.stderr.write(line)
    sys.stderr.flush()


# A module-level seam is useful to embedders that cannot put callables in config.
OUTPUT_WRITER: Callable[[str], Any] = _write_stdout


# --------------------------------------------------------------------------- #
# Settings validation hook (R7)
# --------------------------------------------------------------------------- #


def validate_settings(settings: Any) -> list[str]:
    """Check this module's settings; return one diagnostic per offending field.

    This is the hook ``settings_validator`` in the manifest names. The loader
    runs it for every enabled module before any of them is activated (R7).
    Each diagnostic names the module and the field and nothing else — the
    output path is a configured value and is never echoed. An empty list
    means the settings are accepted.

    Beyond the shape the schema states, the hook checks that ``output`` is
    ``stdout``, ``stderr`` or a writable file path, and that both queue limits
    are present, integral, finite and positive (R6).
    """

    if not isinstance(settings, Mapping):
        return [_setting_diagnostic("settings", "must be a mapping")]
    diagnostics: list[str] = []

    reason = _output_reason(settings.get("output"))
    if reason is not None:
        diagnostics.append(_setting_diagnostic("output", reason))

    queue = settings.get("queue")
    if queue is None:
        diagnostics.append(_setting_diagnostic("queue", "is required"))
    elif not isinstance(queue, Mapping):
        diagnostics.append(_setting_diagnostic("queue", "must be a mapping of limits"))
    else:
        for field_name in queue:
            if field_name not in _QUEUE_LIMITS:
                diagnostics.append(
                    _setting_diagnostic(
                        f"queue.{field_name}", "is not a limit this module owns"
                    )
                )
        for field_name in _QUEUE_LIMITS:
            reason = _limit_reason(queue.get(field_name))
            if reason is not None:
                diagnostics.append(_setting_diagnostic(f"queue.{field_name}", reason))
    return diagnostics


def _output_reason(value: Any) -> str | None:
    """Why *value* is not an acceptable output, or ``None`` if it is."""

    if value is None:
        return "is required"
    if not isinstance(value, str) or not value.strip():
        return "must be a non-empty string"
    if value in (OUTPUT_STDOUT, OUTPUT_STDERR):
        return None
    wording = f"must be {OUTPUT_STDOUT!r}, {OUTPUT_STDERR!r} or a writable file path"
    try:
        path = Path(value).expanduser()
        if path.exists():
            writable = path.is_file() and os.access(path, os.W_OK)
        else:
            parent = path.parent
            writable = parent.is_dir() and os.access(parent, os.W_OK)
    except (OSError, ValueError):
        writable = False
    return None if writable else wording


def _limit_reason(value: Any) -> str | None:
    """Why *value* is not an acceptable queue limit, or ``None`` if it is."""

    if value is None:
        return "is required"
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return "must be a finite positive integer"
    return None


def _setting_diagnostic(field_name: str, reason: str) -> str:
    return f"module {MODULE_NAME!r}: field {field_name!r}: {reason}"


# --------------------------------------------------------------------------- #
# Output sinks
# --------------------------------------------------------------------------- #


class _FileSink:
    """An append-only line sink, opened at ``prepare`` or on first write."""

    __slots__ = ("_handle", "_path")

    def __init__(self, path: str) -> None:
        self._path = Path(path).expanduser()
        self._handle: Any = None

    def open(self) -> None:
        if self._handle is None:
            self._handle = self._path.open("a", encoding="utf-8")

    def __call__(self, line: str) -> None:
        self.open()
        self._handle.write(line)
        self._handle.flush()

    def close(self) -> None:
        handle, self._handle = self._handle, None
        if handle is not None:
            handle.close()


def _output_writer(output: str) -> Callable[[str], Any]:
    """The writer *output* names: a process stream or an appendable file."""

    if output == OUTPUT_STDOUT:
        return OUTPUT_WRITER
    if output == OUTPUT_STDERR:
        return _write_stderr
    return _FileSink(output)


# --------------------------------------------------------------------------- #
# The handle
# --------------------------------------------------------------------------- #


class AuditModule:
    """Queue audit records and write them outside the event publication chain.

    ``max_records`` and ``max_bytes`` bound what is *admitted and not yet
    written*: a record in the writer's hands still counts until its write
    completes, so a blocked writer holding one record and a capacity of two
    admits exactly one more (AC21). ``losses`` counts every record this stage
    took responsibility for and did not write — dropped at admission because
    the queue was saturated, unserialisable, or refused by the writer — and
    mirrors each increment into *supervision* when one is given.
    """

    def __init__(
        self,
        writer: Callable[[str], Any],
        *,
        max_records: int = DEFAULT_MAX_RECORDS,
        max_bytes: int = DEFAULT_MAX_BYTES,
        close_timeout_seconds: float = _DEFAULT_CLOSE_TIMEOUT_SECONDS,
        supervision: Any = None,
        bus: Any = None,
    ) -> None:
        for name, value in (("max_records", max_records), ("max_bytes", max_bytes)):
            if _limit_reason(value) is not None:
                raise AuditModuleError(
                    f"audit configuration: {name} must be a finite positive integer"
                )
        self._writer = writer
        self._max_records = max_records
        self._max_bytes = max_bytes
        self._close_timeout_seconds = close_timeout_seconds
        self._supervision = supervision
        # Unbounded as a container; the bounds are enforced at admission so a
        # saturated queue drops the offered record instead of blocking.
        self._queue: asyncio.Queue[tuple[str, int] | object] = asyncio.Queue()
        self._pending_records = 0
        self._pending_bytes = 0
        self._losses = 0
        self._bus = bus
        self._prepared = False
        self._writer_task = asyncio.create_task(
            self._write_records(), name="audit-writer"
        )
        self._closed = False
        self._close_lock = asyncio.Lock()

    # -- readable outside the queue (R6, R8) -------------------------------- #

    @property
    def losses(self) -> int:
        """Records this stage did not write; never depends on queue state."""

        return self._losses

    @property
    def pending(self) -> int:
        """Records admitted and not yet written."""

        return self._pending_records

    @property
    def pending_bytes(self) -> int:
        """Serialized bytes admitted and not yet written."""

        return self._pending_bytes

    # -- lifecycle hooks (R4) ----------------------------------------------- #

    async def prepare(self) -> None:
        """Register the catch-all middleware and open the sink. Idempotent."""

        if self._prepared or self._closed:
            return
        if self._bus is None:
            raise AuditModuleError("audit preparation: no bus was bound")
        if isinstance(self._writer, _FileSink):
            self._writer.open()
        self._bus.subscribe(_PATTERN, self.handle_event, order=_ORDER)
        self._prepared = True

    async def flush(self, deadline_seconds: float) -> None:
        """Wait, at most *deadline_seconds*, for every admitted record to land.

        Called after the last ordinary resource closed, so the traces those
        closes produced are still written. A writer that does not finish
        inside the budget is left to ``close``, which cancels it; nothing here
        blocks past its deadline.
        """

        if self._closed or self._pending_records == 0:
            return
        budget = max(0.0, min(float(deadline_seconds), self._close_timeout_seconds))
        with suppress(asyncio.TimeoutError, TimeoutError):
            await asyncio.wait_for(self._queue.join(), timeout=budget)

    async def close(self) -> None:
        """Drain queued records when possible, with bounded stuck-writer cleanup.

        A writer that does not finish inside the budget is cancelled; the
        record in its hands and every record still queued behind it are then
        abandoned, and each one is counted as a loss so the counter still
        accounts for every admitted record (R6). The file sink, if any, is
        closed off the event loop: a stalled write keeps running in its
        thread after cancellation and may hold the file's lock, and closing
        it inline would block the loop past the lifecycle's own deadline.
        """

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
            self._abandon_queued()

            if isinstance(self._writer, _FileSink):
                with suppress(asyncio.TimeoutError, TimeoutError, Exception):
                    await asyncio.wait_for(
                        asyncio.to_thread(self._writer.close),
                        timeout=self._close_timeout_seconds,
                    )

    def _abandon_queued(self) -> None:
        """Count and release every record left in the queue after the writer.

        Only reached once the writer task is done and admission is closed,
        so nothing is added while draining and ``pending`` returns to zero.
        """

        while True:
            try:
                item = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            try:
                if isinstance(item, tuple):
                    _, size = item
                    self._pending_records -= 1
                    self._pending_bytes -= size
                    self._record_loss()
            finally:
                self._queue.task_done()

    # -- the middleware ------------------------------------------------------ #

    def handle_event(self, event: Mapping[str, Any]) -> Mapping[str, Any]:
        """Offer one safe JSON line to the queue and return *event* unchanged.

        Synchronous and non-blocking: the publication chain never waits for
        the writer. A saturated queue drops *this* record — the one just
        offered — and counts the loss (R6).
        """

        if self._closed:
            return event
        try:
            record = {
                "type": _redact(event.get("type")),
                "payload": _redact(event.get("payload")),
                "metadata": _redact(event.get("metadata")),
            }
            line = json.dumps(
                record,
                ensure_ascii=False,
                separators=(",", ":"),
                default=_json_fallback,
            ) + "\n"
            size = len(line.encode("utf-8"))
        except Exception:
            # Observability must never alter publication success. This also
            # protects the bus from unusual Mapping implementations.
            self._record_loss()
            return event

        if (
            self._pending_records + 1 > self._max_records
            or self._pending_bytes + size > self._max_bytes
        ):
            self._record_loss()
            return event
        self._pending_records += 1
        self._pending_bytes += size
        self._queue.put_nowait((line, size))
        return event

    def _record_loss(self) -> None:
        """Count one lost record here and, when supervised, in the registry.

        The counter lives outside the queue on purpose: reporting a loss
        through a bus event would feed the queue whose saturation it reports.
        """

        self._losses += 1
        supervision = self._supervision
        if supervision is not None:
            with suppress(Exception):
                supervision.count(COUNTER_AUDIT_RECORD_LOSSES)

    async def _write_records(self) -> None:
        while True:
            item = await self._queue.get()
            try:
                if item is _STOP:
                    return
                assert isinstance(item, tuple)
                line, size = item
                try:
                    await _call_writer(self._writer, line)
                except asyncio.CancelledError:
                    # Cancelled mid-write at close: the record was taken and
                    # not (knowingly) written, so it is counted as lost.
                    self._record_loss()
                    raise
                except Exception:
                    # A failed record is isolated and the worker continues
                    # with the next one. No event data is copied to a
                    # diagnostic channel; the loss is counted instead.
                    self._record_loss()
                finally:
                    # Admitted capacity is released once the write is over,
                    # not when the record is picked up, so an in-flight
                    # record still occupies its slot (AC21).
                    self._pending_records -= 1
                    self._pending_bytes -= size
            finally:
                self._queue.task_done()


# --------------------------------------------------------------------------- #
# Activation (R7)
# --------------------------------------------------------------------------- #


async def activate(
    context: Any,
    settings: Mapping[str, Any],
    catalog: Mapping[str, Mapping[str, Any]],
) -> AuditModule:
    """Build the handle from the scoped runtime context (R7, AC26).

    *context* is the module-scoped view of the versioned runtime: the bus
    the middleware registers on at ``prepare`` and the supervision facade the
    loss counter is mirrored into. A bare bus is refused with one diagnostic.
    Settings are checked through the same hook the loader ran, so a handle
    built outside the loader is refused on the same terms.

    Seams, read from *settings* and never from configuration files:
    ``_writer`` replaces the writer ``output`` names, and
    ``_close_timeout_seconds`` bounds the stuck-writer cleanup at close.
    """

    del catalog  # Capabilities are declared by the colocated manifest.
    bus, supervision = _runtime_surfaces(context)
    if not isinstance(settings, Mapping):
        raise AuditModuleError("audit configuration: settings must be a mapping")
    diagnostics = validate_settings(settings)
    if diagnostics:
        raise AuditModuleError(
            f"audit configuration: settings were refused ({len(diagnostics)} diagnostics)"
        )

    writer = settings.get("_writer")
    if writer is None:
        writer = _output_writer(settings["output"])
    elif not callable(writer):
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

    queue = settings["queue"]
    handle = AuditModule(
        writer,
        max_records=queue["max_records"],
        max_bytes=queue["max_bytes"],
        close_timeout_seconds=float(close_timeout),
        supervision=supervision,
        bus=bus,
    )
    return handle


def _runtime_surfaces(context: Any) -> tuple[Any, Any]:
    """Read the bus and the supervision facade from *context*, by shape.

    Duck-typed so a harness may inject fakes, while a context that is not a
    context — the bare bus of the compatibility signature, say — is refused
    with one diagnostic (AC26).
    """

    bus = getattr(context, "bus", None)
    supervision = getattr(context, "supervision", None)
    if (
        bus is None
        or not callable(getattr(bus, "subscribe", None))
        or supervision is None
        or not callable(getattr(supervision, "count", None))
        or not callable(getattr(supervision, "snapshot", None))
    ):
        raise AuditModuleError("audit activation: runtime context is invalid")
    return bus, supervision


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


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


__all__ = [
    "DEFAULT_MAX_BYTES",
    "DEFAULT_MAX_RECORDS",
    "MODULE_NAME",
    "OUTPUT_STDERR",
    "OUTPUT_STDOUT",
    "AuditModule",
    "AuditModuleError",
    "activate",
    "validate_settings",
]
