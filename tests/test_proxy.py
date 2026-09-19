"""Proxy protocol v1 (phase 1, R6).

This file is created by P18 with the consistency checks that keep
``docs/proxy-protocol.md`` from drifting away from the constants declared in
``core/contracts.py``; P19 (brain side) and P20 (agent side) extend it with the
in-process protocol tests over an in-memory transport.

The document is the normative description; the constants are its single
spelling. Every frame type, error code, ``attachment_ack`` code and close code
declared by the contracts must appear in the document **in backticks**, and the
default limits fixed by R6 and decision 9 must appear as literals.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from core import contracts
from core.contracts import (
    ATTACHMENT_ACK_CODES,
    PROXY_CALL_FRAME_TYPES,
    PROXY_CLOSE_CODES,
    PROXY_DEFAULT_MAX_FRAME_BYTES,
    PROXY_ERROR_CODES,
    PROXY_FRAME_TYPES,
    PROXY_PROTOCOL_VERSION,
    PROXY_SESSION_FRAME_TYPES,
)

PROTOCOL_DOC = Path(__file__).resolve().parent.parent / "docs" / "proxy-protocol.md"

# Defaults fixed by the specification text (R6, decision 9) rather than by a
# contracts constant: the document must spell them as these literals.
R6_DEFAULT_LIMITS: dict[str, str] = {
    "max_frame_bytes": str(PROXY_DEFAULT_MAX_FRAME_BYTES),
    "max_entries": "1024",
    "ttl_seconds": "300",
    "heartbeat_seconds": "15",
    "heartbeat_timeout_seconds": "10",
}

# Decision 9: exponential backoff, initial 1 s, multiplier 2, cap 30 s.
DECISION_9_BACKOFF: dict[str, str] = {"initial": "1", "multiplier": "2", "cap": "30"}


def _constants_with_prefix(prefix: str) -> dict[str, object]:
    """Every module-level constant of ``core.contracts`` named ``<prefix>*``.

    Parsed from the module rather than from a hand-written list so that a
    code declared tomorrow is checked against the document the same day.
    The aggregate ``frozenset`` constants (``*_CODES``, ``*_TYPES``) are
    skipped: their members are checked, not their names.
    """

    found: dict[str, object] = {}
    for name in dir(contracts):
        if not name.startswith(prefix):
            continue
        value = getattr(contracts, name)
        if isinstance(value, (frozenset, set, tuple, list, dict)):
            continue
        found[name] = value
    return found


@pytest.fixture(scope="module")
def protocol_doc() -> str:
    assert PROTOCOL_DOC.is_file(), f"{PROTOCOL_DOC} is missing"
    text = PROTOCOL_DOC.read_text(encoding="utf-8")
    assert text.strip(), "docs/proxy-protocol.md is empty"
    return text


def _backticked(text: str, literal: object) -> bool:
    return f"`{literal}`" in text


# --------------------------------------------------------------------------- #
# Frame types
# --------------------------------------------------------------------------- #


def test_document_names_every_frame_type_in_backticks(protocol_doc: str) -> None:
    frame_constants = _constants_with_prefix("FRAME_")
    assert set(frame_constants.values()) == set(PROXY_FRAME_TYPES), (
        "every FRAME_* constant must be a member of PROXY_FRAME_TYPES"
    )
    missing = sorted(t for t in PROXY_FRAME_TYPES if not _backticked(protocol_doc, t))
    assert not missing, f"frame types absent from the document: {missing}"


def test_document_counts_eleven_text_frame_types_plus_the_binary_frame(
    protocol_doc: str,
) -> None:
    # The plan's "12 frame types" are the eleven text types of the constants
    # plus the binary attachment frame; the document must say so explicitly.
    assert len(PROXY_FRAME_TYPES) == 11
    assert "twelve frame types" in protocol_doc
    assert "binary frame" in protocol_doc


def test_document_partitions_session_and_call_scoped_frames(protocol_doc: str) -> None:
    # The two scope families are disjoint and cover every type except the
    # pairing (hello, welcome) and answer (error) frames.
    assert not PROXY_SESSION_FRAME_TYPES & PROXY_CALL_FRAME_TYPES
    others = PROXY_FRAME_TYPES - PROXY_SESSION_FRAME_TYPES - PROXY_CALL_FRAME_TYPES
    assert others == {
        contracts.FRAME_HELLO,
        contracts.FRAME_WELCOME,
        contracts.FRAME_ERROR,
    }
    assert "`PROXY_SESSION_FRAME_TYPES`" in protocol_doc
    assert "`PROXY_CALL_FRAME_TYPES`" in protocol_doc


# --------------------------------------------------------------------------- #
# Error codes, ack codes, close codes
# --------------------------------------------------------------------------- #


def test_document_names_every_error_code_in_backticks(protocol_doc: str) -> None:
    error_constants = _constants_with_prefix("PROXY_ERROR_")
    assert set(error_constants.values()) == set(PROXY_ERROR_CODES), (
        "every PROXY_ERROR_* constant must be a member of PROXY_ERROR_CODES"
    )
    missing = sorted(c for c in PROXY_ERROR_CODES if not _backticked(protocol_doc, c))
    assert not missing, f"error codes absent from the document: {missing}"


def test_document_states_retryable_for_every_error_code(protocol_doc: str) -> None:
    """The error-code table carries a `retryable` column with a boolean per code.

    R6 fixes ``proxy_disconnected`` as ``retryable: true`` and
    ``duplicate_call_unknown`` as ``retryable: false``; the document must
    give every other code an explicit value too, in the same table row.
    """

    rows = {
        m.group(1): m.group(0)
        for m in re.finditer(r"^\| `([a-z_]+)`\s+\|.*\|\s*$", protocol_doc, re.MULTILINE)
        if m.group(1) in PROXY_ERROR_CODES
    }
    assert set(rows) == set(PROXY_ERROR_CODES), sorted(set(PROXY_ERROR_CODES) - set(rows))
    for code, row in rows.items():
        assert "`true`" in row or "`false`" in row, f"no retryable value for {code}"
    assert "`true`" in rows[contracts.PROXY_ERROR_PROXY_DISCONNECTED]
    assert "`false`" in rows[contracts.PROXY_ERROR_DUPLICATE_CALL_UNKNOWN]
    assert "`false`" in rows[contracts.PROXY_ERROR_AUTH_FAILED]


def test_document_names_every_attachment_ack_code_in_backticks(protocol_doc: str) -> None:
    ack_constants = _constants_with_prefix("ATTACHMENT_ACK_")
    assert set(ack_constants.values()) == set(ATTACHMENT_ACK_CODES)
    missing = sorted(c for c in ATTACHMENT_ACK_CODES if not _backticked(protocol_doc, c))
    assert not missing, f"attachment_ack codes absent from the document: {missing}"


def test_document_names_every_close_code_in_backticks(protocol_doc: str) -> None:
    close_constants = _constants_with_prefix("PROXY_CLOSE_")
    assert set(close_constants.values()) == set(PROXY_CLOSE_CODES)
    missing = sorted(c for c in PROXY_CLOSE_CODES if not _backticked(protocol_doc, c))
    assert not missing, f"close codes absent from the document: {missing}"
    # Each close code is also named by its constant so a reader of the code
    # and a reader of the document use one spelling.
    for name in close_constants:
        assert _backticked(protocol_doc, name), name


def test_document_pairs_each_terminal_error_with_its_close_code(protocol_doc: str) -> None:
    """R6 / AC32 / AC48: the four terminal refusals and their close codes."""

    pairs = {
        contracts.PROXY_ERROR_AUTH_FAILED: contracts.PROXY_CLOSE_AUTH_FAILED,
        contracts.PROXY_ERROR_AGENT_LIMIT: contracts.PROXY_CLOSE_AGENT_LIMIT,
        contracts.PROXY_ERROR_UNSUPPORTED_PROTOCOL_VERSION: contracts.PROXY_CLOSE_BAD_REQUEST,
        contracts.PROXY_ERROR_FRAME_TOO_LARGE: contracts.PROXY_CLOSE_FRAME_TOO_LARGE,
    }
    for code, close in pairs.items():
        row = re.search(rf"^\| `{re.escape(code)}`.*$", protocol_doc, re.MULTILINE)
        assert row is not None, code
        assert f"`{close}`" in row.group(0), f"{code} row does not name close {close}"
    # ``unknown_frame`` keeps the connection open (R6).
    row = re.search(
        rf"^\| `{contracts.PROXY_ERROR_UNKNOWN_FRAME}`.*$", protocol_doc, re.MULTILINE
    )
    assert row is not None and "kept" in row.group(0)


# --------------------------------------------------------------------------- #
# Envelope, limits and defaults
# --------------------------------------------------------------------------- #


def test_document_fixes_the_envelope_and_protocol_version(protocol_doc: str) -> None:
    for field in ("v", "type", "id"):
        assert _backticked(protocol_doc, field)
    assert PROXY_PROTOCOL_VERSION == 1
    assert "`PROXY_PROTOCOL_VERSION`" in protocol_doc
    assert "must be `1`" in protocol_doc
    assert "`id: null`" in protocol_doc or "**`null`**" in protocol_doc


def test_document_spells_the_default_limits_as_literals(protocol_doc: str) -> None:
    assert "`PROXY_DEFAULT_MAX_FRAME_BYTES`" in protocol_doc
    for setting, literal in R6_DEFAULT_LIMITS.items():
        assert _backticked(protocol_doc, setting), setting
        assert _backticked(protocol_doc, literal), f"{setting} default {literal}"
    assert "`min(store max_object_bytes, max_attachment_bytes)`" in protocol_doc
    assert _backticked(protocol_doc, "max_attachment_bytes")


def test_document_spells_the_decision_9_backoff_parameters(protocol_doc: str) -> None:
    for name, literal in DECISION_9_BACKOFF.items():
        assert _backticked(protocol_doc, name), name
        assert _backticked(protocol_doc, literal), f"{name} = {literal}"
    assert "[0.5, 1.0, 2.0, 4.0, 8.0, 15.0]" in protocol_doc  # AC37 sequence


def test_document_fixes_the_call_identity_rule(protocol_doc: str) -> None:
    for token in ("seq", "last_seq", "max_entries", "ttl_seconds", "call_id", "session_id"):
        assert _backticked(protocol_doc, token), token
    assert "reset on every `welcome`" in protocol_doc


def test_document_fixes_the_deadline_rule(protocol_doc: str) -> None:
    assert _backticked(protocol_doc, "deadline_utc")
    assert _backticked(protocol_doc, "remaining_ms")
    assert "monotonic clock" in protocol_doc
    assert "informational" in protocol_doc.lower()


def test_document_fixes_the_drop_classification_by_nature(protocol_doc: str) -> None:
    assert _backticked(protocol_doc, "external_unknown")
    assert _backticked(protocol_doc, "late_observations")
    assert _backticked(protocol_doc, "write")
    assert _backticked(protocol_doc, "read")


def test_document_fixes_the_event_adapter_allowlist(protocol_doc: str) -> None:
    assert _backticked(protocol_doc, "agent.status")
    assert _backticked(protocol_doc, "event_not_allowed")
    assert 'metadata.source = "proxy"' in protocol_doc
    assert "metadata.provider_id = agent_id" in protocol_doc


def test_document_fixes_the_tls_rules(protocol_doc: str) -> None:
    assert _backticked(protocol_doc, "listen.host")
    assert _backticked(protocol_doc, "tls")
    assert _backticked(protocol_doc, "brain_url")
    assert _backticked(protocol_doc, "wss")


# =========================================================================== #
# P19 — the brain side: ``modules/proxy`` over the in-memory pair
# =========================================================================== #
#
# Every test below drives ``ProxyModule.connection_handler`` directly with one
# end of ``MemoryWebSocketPair`` — the same coroutine the ``aiohttp.web``
# listener hands each accepted socket to — and reads the brain's answers on
# the other end. Calls go through the real executor of ``runtime_context``
# with a real ``AttachmentStore`` on the same ``ManualClock``; the heartbeat
# runs on that clock's sleeper; session ids come from the seeded ``rng``. No
# positive-duration sleep anywhere.

import asyncio
import inspect
import json
from types import SimpleNamespace
from typing import Any

import yaml

from core.actions import (
    ERROR_INVALID_RESULT,
    ERROR_PROVIDER_NOT_READY,
    AuthorizationPolicy,
    AuthorizationRule,
)
from core.attachments import AttachmentStore
from core.contracts import (
    ATTACHMENT_ACK_STORE_FULL,
    ATTACHMENT_ACK_TOO_LARGE,
    ATTACHMENT_ACK_UNEXPECTED_BINARY,
    FRAME_ATTACHMENT,
    FRAME_ATTACHMENT_ACK,
    FRAME_CALL,
    FRAME_CANCEL,
    FRAME_ERROR,
    FRAME_EVENT,
    FRAME_HELLO,
    FRAME_OBSERVATION,
    FRAME_PING,
    FRAME_PONG,
    FRAME_WELCOME,
    PROXY_CLOSE_AGENT_LIMIT,
    PROXY_CLOSE_AUTH_FAILED,
    PROXY_CLOSE_BAD_REQUEST,
    PROXY_CLOSE_FRAME_TOO_LARGE,
    PROXY_ERROR_ACTION_MISMATCH,
    PROXY_ERROR_AGENT_LIMIT,
    PROXY_ERROR_AUTH_FAILED,
    PROXY_ERROR_EVENT_NOT_ALLOWED,
    PROXY_ERROR_FRAME_TOO_LARGE,
    PROXY_ERROR_INVALID_FRAME,
    PROXY_ERROR_PROXY_DISCONNECTED,
    PROXY_ERROR_UNKNOWN_FRAME,
    PROXY_ERROR_UNKNOWN_SESSION,
    PROXY_ERROR_UNSUPPORTED_PROTOCOL_VERSION,
    TRACE_BRAIN_RUN_COMPLETED,
    TRACE_MODULE_DEGRADED,
    ActionCall,
    ActionSpec,
    Destination,
)
from core.lifecycle import ROLE_INPUT, PhaseCoordinator
from core.loader import ModuleLoader
from core.runtime import RUNTIME_API, RuntimeContext
from conftest import (
    ManualClock,
    MemoryWebSocketPair,
    WSMsgType,
    events_of,
    png_bytes,
    runtime_context,
    settle,
    wait_until,
)

from modules.proxy import (
    AGENT_STATUS_EVENT,
    CLOSE_GOING_AWAY,
    MANIFEST_PATH,
    MODULE_NAME,
    PROVIDER_NAME,
    ProxyModule,
    ProxyModuleError,
    _FrameSizeGate,
    _MessageTooBig,
    activate,
    default_server_factory,
    spec_declaration,
    spec_from_declaration,
    validate_settings,
)


ROOT = Path(__file__).resolve().parent.parent
CAPTURE_MANIFEST = ROOT / "modules" / "capture" / "module.yaml"

TOKEN = "pairing-secret-token"
AGENT_ID = "agent-pc-1"
PRINCIPAL = "brain"
RUN_ID = "run-1"
SCREEN_CAPTURE = "screen.capture"
FAKE_WRITE = "fake.write"

CAPTURE_DESTINATION = Destination("fake", "channel-9", "capture")
CHAT_DESTINATION = Destination("fake", "channel-9", "chat")

STORE_LIMITS: dict[str, Any] = {
    "max_object_bytes": 65_536,
    "max_objects": 4,
    "max_total_bytes": 262_144,
    "max_bytes_per_run": 131_072,
    "ttl_seconds": 300.0,
}

BASE_SETTINGS: dict[str, Any] = {
    "listen": {"host": "127.0.0.1", "port": 8765},
    "pairing_token": TOKEN,
}

#: A fixed wall clock for ``deadline_utc``: 2023-11-14T22:13:20Z.
WALL_CLOCK = 1_700_000_000.0


# --------------------------------------------------------------------------- #
# Specs the brain's catalog declares
# --------------------------------------------------------------------------- #


def screen_capture_spec() -> ActionSpec:
    """The shipped ``screen.capture`` contract, read from the capture manifest."""

    manifest = yaml.safe_load(CAPTURE_MANIFEST.read_text(encoding="utf-8"))
    (entry,) = [item for item in manifest["actions"] if item["name"] == SCREEN_CAPTURE]
    return ActionSpec(
        name=entry["name"],
        version=entry["version"],
        description=entry["description"],
        argument_schema=entry["argument_schema"],
        result_schema=entry["result_schema"],
        nature=entry["nature"],
        required_permissions=tuple(entry["required_permissions"]),
        supported_destinations=tuple(
            Destination(d["platform"], d["channel_id"], d["scope"])
            for d in entry["supported_destinations"]
        ),
        timeout_seconds=entry["timeout_seconds"],
        idempotency=entry["idempotency"],
        delivery=entry.get("delivery"),
    )


def capture_manifest() -> dict[str, Any]:
    """The shipped capture manifest, as the loader's catalog carries it."""

    return yaml.safe_load(CAPTURE_MANIFEST.read_text(encoding="utf-8"))


def fake_write_spec() -> ActionSpec:
    """A ``write`` action with the delivery capability, served remotely."""

    return ActionSpec(
        name=FAKE_WRITE,
        version=1,
        description="Send text somewhere on the fake platform.",
        argument_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        },
        result_schema={
            "type": "object",
            "properties": {"external_id": {"type": "string"}},
            "required": ["external_id"],
        },
        nature="write",
        required_permissions=("chat.write",),
        supported_destinations=(Destination("fake", "*", "chat"),),
        timeout_seconds=5,
        idempotency="none",
        delivery={"text_argument": "text"},
    )


def grant_everything() -> AuthorizationPolicy:
    return AuthorizationPolicy(
        [
            AuthorizationRule(
                rule_id="grant-capture",
                action_name=SCREEN_CAPTURE,
                granted_permissions=("screen.capture",),
            ),
            AuthorizationRule(
                rule_id="grant-write",
                action_name=FAKE_WRITE,
                granted_permissions=("chat.write",),
            ),
        ]
    )


# --------------------------------------------------------------------------- #
# Harness
# --------------------------------------------------------------------------- #


class FakeServer:
    """What the ``_server_factory`` seam returns: records its lifecycle."""

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.stopped = False
        self.closed = False

    async def stop(self) -> None:
        self.stopped = True

    async def close(self) -> None:
        self.closed = True


class Brain:
    """One activated proxy on a real runtime context, plus its wire ends."""

    def __init__(
        self,
        *,
        actions: tuple[str, ...] = (SCREEN_CAPTURE, FAKE_WRITE),
        clock: ManualClock | None = None,
        store_limits: dict[str, Any] | None = None,
        specs: tuple[ActionSpec, ...] | None = None,
        catalog: dict[str, Any] | None = None,
        **extra_settings: Any,
    ) -> None:
        self.clock = clock if clock is not None else ManualClock()
        # The loader's full manifest catalog, as ``activate`` receives it.
        self.catalog: dict[str, Any] = catalog if catalog is not None else {}
        self.store = AttachmentStore(clock=self.clock, **(store_limits or STORE_LIMITS))
        self.context: RuntimeContext = runtime_context(
            clock=self.clock,
            attachments=self.store,
            authorization=grant_everything(),
        )
        for spec in specs if specs is not None else (screen_capture_spec(), fake_write_spec()):
            self.context.actions.declare(spec, module="fixture")
        self.servers: list[FakeServer] = []
        self.diagnostics: list[str] = []
        self.settings: dict[str, Any] = {
            **BASE_SETTINGS,
            "actions": list(actions),
            "_server_factory": self._server_factory,
            "_sleeper": self.clock.sleep,
            "_wall_clock": lambda: WALL_CLOCK,
            "diagnostic_reporter": self.diagnostics.append,
            # The accepted ``limits`` block, as the entry point hands it over.
            "limits": {"attachments": dict(store_limits or STORE_LIMITS)},
            **extra_settings,
        }
        self.module: ProxyModule | None = None
        self.handlers: list[asyncio.Task[None]] = []

    async def _server_factory(self, handler: Any, **kwargs: Any) -> FakeServer:
        server = FakeServer(handler=handler, **kwargs)
        self.servers.append(server)
        return server

    async def activate(self, *, prepare: bool = True, start: bool = False) -> ProxyModule:
        self.module = await activate(
            self.context.for_module(MODULE_NAME), self.settings, self.catalog
        )
        if prepare:
            await self.module.prepare()
        if start:
            await self.module.start_inputs()
        return self.module

    def connect(self) -> "Agent":
        assert self.module is not None
        pair = MemoryWebSocketPair()
        task = asyncio.create_task(self.module.connection_handler(pair.server))
        self.handlers.append(task)
        return Agent(self, pair, task)

    @property
    def bus(self) -> Any:
        return self.context.bus

    @property
    def executor(self) -> Any:
        return self.context.executor

    def authorized(self, destination: Destination | None = None) -> list[str]:
        return sorted(self.context.actions.authorized(principal=PRINCIPAL, destination=destination))

    async def close(self) -> None:
        if self.module is not None:
            await self.module.close()
        for task in self.handlers:
            if not task.done():
                task.cancel()
        await asyncio.gather(*self.handlers, return_exceptions=True)


class Agent:
    """The scripted agent end of one connection."""

    def __init__(self, brain: Brain, pair: MemoryWebSocketPair, handler: asyncio.Task[None]) -> None:
        self.brain = brain
        self.pair = pair
        self.ws = pair.client
        self.handler = handler
        self.session_id: str | None = None

    async def send(self, frame: dict[str, Any], *, v: int = 1) -> None:
        await self.ws.send_str(json.dumps({"v": v, **frame}))

    async def send_raw(self, text: str) -> None:
        await self.ws.send_str(text)

    async def send_bytes(self, data: bytes) -> None:
        await self.ws.send_bytes(data)

    async def receive(self) -> Any:
        return await self.ws.receive()

    async def frame(self) -> dict[str, Any]:
        message = await self.ws.receive()
        assert message.type == WSMsgType.TEXT, message
        return json.loads(message.data)

    async def close_frame(self) -> int:
        message = await self.ws.receive()
        assert message.type == WSMsgType.CLOSE, message
        return int(message.data)

    async def expect_error(self, code: str) -> dict[str, Any]:
        frame = await self.frame()
        assert frame["type"] == FRAME_ERROR, frame
        assert frame["code"] == code, frame
        assert TOKEN not in json.dumps(frame)
        return frame

    async def expect_refusal(self, code: str, close_code: int) -> dict[str, Any]:
        frame = await self.expect_error(code)
        assert await self.close_frame() == close_code
        assert self.ws.close_code == close_code
        await wait_until(self.handler.done)
        return frame

    def hello_frame(
        self,
        *,
        nonce: Any = "h1",
        token: str = TOKEN,
        agent_id: str = AGENT_ID,
        specs: tuple[ActionSpec, ...] | None = None,
        declarations: list[dict[str, Any]] | None = None,
        max_frame_bytes: int = 1_048_576,
    ) -> dict[str, Any]:
        if declarations is None:
            declared = specs if specs is not None else (screen_capture_spec(), fake_write_spec())
            declarations = [spec_declaration(spec) for spec in declared]
        frame: dict[str, Any] = {
            "type": FRAME_HELLO,
            "agent_id": agent_id,
            "token": token,
            "actions": declarations,
            "max_frame_bytes": max_frame_bytes,
        }
        if nonce is not None:
            frame["id"] = nonce
        return frame

    async def pair_up(self, **kwargs: Any) -> dict[str, Any]:
        """Send ``hello`` and return the ``welcome``; mismatches are collected first."""

        await self.send(self.hello_frame(**kwargs))
        self.mismatches: list[dict[str, Any]] = []
        while True:
            frame = await self.frame()
            if frame["type"] == FRAME_ERROR and frame["code"] == PROXY_ERROR_ACTION_MISMATCH:
                self.mismatches.append(frame)
                continue
            assert frame["type"] == FRAME_WELCOME, frame
            self.session_id = frame["session_id"]
            # Let the brain's heartbeat task take its first turn (and park
            # on the injected clock) before a test moves that clock.
            await settle()
            return frame

    async def call_frame(self) -> dict[str, Any]:
        frame = await self.frame()
        assert frame["type"] == FRAME_CALL, frame
        return frame

    async def observe(
        self,
        call_id: str,
        *,
        status: str = "success",
        result: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
        parts: list[dict[str, Any]] | None = None,
        provenance: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        frame: dict[str, Any] = {
            "type": FRAME_OBSERVATION,
            "id": call_id,
            "status": status,
            "provenance": provenance if provenance is not None else {"provider": "capture", "module": "capture"},
            "parts": parts or [],
        }
        if result is not None:
            frame["result"] = result
        if error is not None:
            frame["error"] = error
        await self.send(frame)
        return frame

    async def transfer(self, attachment_id: str, data: bytes, *, content_type: str = "image/png", call_id: str | None = None) -> dict[str, Any]:
        """Header + one binary frame; returns the ack."""

        header: dict[str, Any] = {
            "type": FRAME_ATTACHMENT,
            "id": self.session_id,
            "attachment_id": attachment_id,
            "content_type": content_type,
            "size": len(data),
        }
        if call_id is not None:
            header["call_id"] = call_id
        await self.send(header)
        await self.send_bytes(data)
        ack = await self.frame()
        assert ack["type"] == FRAME_ATTACHMENT_ACK, ack
        return ack

    def sent_types(self) -> list[str]:
        """Every frame type this end wrote, in order (binary as ``<binary>``)."""

        types: list[str] = []
        for message in self.ws.sent:
            if message.type == WSMsgType.TEXT:
                types.append(json.loads(message.data)["type"])
            elif message.type == WSMsgType.BINARY:
                types.append("<binary>")
        return types

    def received_types(self) -> list[str]:
        types: list[str] = []
        for message in self.pair.server.sent:
            if message.type == WSMsgType.TEXT:
                types.append(json.loads(message.data)["type"])
            else:
                types.append("<binary>")
        return types


def capture_call(
    call_id: str = "call-1",
    *,
    run_id: str = RUN_ID,
    deadline: float,
    arguments: dict[str, Any] | None = None,
) -> ActionCall:
    return ActionCall(
        action_name=SCREEN_CAPTURE,
        action_version=1,
        arguments=arguments if arguments is not None else {},
        conversation_id="conversation-1",
        run_id=run_id,
        call_id=call_id,
        source_event_id="source-1",
        destination=CAPTURE_DESTINATION,
        principal=PRINCIPAL,
        deadline=deadline,
        message_id="source-1",
    )


def write_call(call_id: str = "call-w1", *, deadline: float, text: str = "hello") -> ActionCall:
    return ActionCall(
        action_name=FAKE_WRITE,
        action_version=1,
        arguments={"text": text},
        conversation_id="conversation-1",
        run_id=RUN_ID,
        call_id=call_id,
        source_event_id="source-1",
        destination=CHAT_DESTINATION,
        principal=PRINCIPAL,
        deadline=deadline,
    )


def image_ref(attachment_id: str, size: int, *, width: int = 16, height: int = 9) -> dict[str, Any]:
    return {
        "type": "image_ref",
        "attachment_id": attachment_id,
        "content_type": "image/png",
        "size": size,
        "width": width,
        "height": height,
        "captured_at": 1234.5,
        "provider_id": "capture",
    }


def capture_result(size: int, *, width: int = 16, height: int = 9) -> dict[str, Any]:
    return {
        "source": "screen",
        "content_type": "image/png",
        "width": width,
        "height": height,
        "size": size,
        "captured_at": 1234.5,
    }


# --------------------------------------------------------------------------- #
# Manifest and settings
# --------------------------------------------------------------------------- #


def test_manifest_declares_the_input_role_the_event_and_no_action() -> None:
    """R6/R7: manifest v2 with role ``input``, ``produces: [agent.status]``,
    ``consumes: []``, the ``pairing_token`` credential, the listed settings
    and a validator the package implements; it declares **no** action —
    what it provides comes from the discovered catalog (decision 7)."""

    manifest = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert manifest["name"] == MODULE_NAME
    assert manifest["manifest_version"] == 2
    assert manifest["runtime_api"] == RUNTIME_API
    assert manifest["produces"] == [AGENT_STATUS_EVENT]
    assert manifest["consumes"] == []
    assert manifest["middleware"] is False
    assert manifest["lifecycle"] == {"roles": [ROLE_INPUT]}
    assert "actions" not in manifest
    assert manifest["credentials"] == ["pairing_token"]
    assert manifest["settings_validator"] == "validate_settings"
    properties = manifest["settings_schema"]["properties"]
    for key in (
        "listen", "tls", "pairing_token", "actions", "max_frame_bytes",
        "max_attachment_bytes", "heartbeat_seconds", "heartbeat_timeout_seconds",
        "late_result_seconds",
    ):
        assert key in properties, key
    assert set(manifest["settings_schema"]["required"]) == {"listen", "pairing_token", "actions"}
    assert properties["pairing_token"]["type"] == "string"


def test_validate_settings_accepts_a_loopback_listener_without_tls() -> None:
    assert validate_settings({**BASE_SETTINGS, "actions": [SCREEN_CAPTURE]}) == []
    assert validate_settings({**BASE_SETTINGS, "listen": {"host": "localhost", "port": 1}, "actions": []}) == []
    assert validate_settings({**BASE_SETTINGS, "listen": {"host": "::1", "port": 65535}, "actions": []}) == []


def test_validate_settings_refuses_a_non_loopback_listener_without_tls_and_echoes_no_value() -> None:
    """R6: a non-loopback ``listen.host`` needs ``tls``; the diagnostic names
    module and field, never the host."""

    settings = {**BASE_SETTINGS, "listen": {"host": "203.0.113.7", "port": 8765}, "actions": []}
    diagnostics = validate_settings(settings)
    assert diagnostics == [
        f"module {MODULE_NAME!r}: field 'listen.host': must be a loopback address unless tls is configured"
    ]
    assert "203.0.113.7" not in "".join(diagnostics)

    with_tls = {**settings, "tls": {"certfile": "${PROXY_CERT}", "keyfile": "${PROXY_KEY}"}}
    assert validate_settings(with_tls) == []
    assert validate_settings({**settings, "listen": {"host": "0.0.0.0", "port": 8765}})
    assert validate_settings({**settings, "tls": {"certfile": "c"}}) == [
        f"module {MODULE_NAME!r}: field 'tls.keyfile': must be a non-empty string",
    ]


def test_validate_settings_refuses_empty_allowlist_entries_and_non_finite_bounds() -> None:
    def diagnostics(**overrides: Any) -> list[str]:
        return validate_settings({**BASE_SETTINGS, "actions": [SCREEN_CAPTURE], **overrides})

    assert diagnostics(actions=[SCREEN_CAPTURE, ""]) == [
        f"module {MODULE_NAME!r}: field 'actions[1]': must be a non-empty action name"
    ]
    assert diagnostics(actions=[SCREEN_CAPTURE, SCREEN_CAPTURE]) == [
        f"module {MODULE_NAME!r}: field 'actions[1]': duplicates an earlier entry"
    ]
    assert diagnostics(actions="screen.capture") == [
        f"module {MODULE_NAME!r}: field 'actions': must be a list of action names"
    ]
    for name in ("max_frame_bytes", "max_attachment_bytes"):
        assert diagnostics(**{name: 0}) == [
            f"module {MODULE_NAME!r}: field {name!r}: must be a positive integer"
        ]
        assert diagnostics(**{name: float("inf")})
    for name in ("heartbeat_seconds", "heartbeat_timeout_seconds", "late_result_seconds"):
        for bad in (float("inf"), float("nan"), 0, -1, "5", True):
            assert diagnostics(**{name: bad}) == [
                f"module {MODULE_NAME!r}: field {name!r}: must be a finite positive number"
            ], (name, bad)
    assert diagnostics(pairing_token="") == [
        f"module {MODULE_NAME!r}: field 'pairing_token': must be a non-empty string"
    ]
    assert diagnostics(listen={"host": "127.0.0.1", "port": 70000}) == [
        f"module {MODULE_NAME!r}: field 'listen.port': must be an integer in 1..65535"
    ]
    assert diagnostics(unknown=1) == [
        f"module {MODULE_NAME!r}: field 'unknown': is not a setting this module declares"
    ]
    assert diagnostics(limits={"attachments": STORE_LIMITS}) == []
    assert validate_settings("nope") == [f"module {MODULE_NAME!r}: field 'settings': must be a mapping"]
    assert any("'listen'" in d for d in validate_settings({"pairing_token": TOKEN, "actions": []}))


def test_spec_declaration_round_trips_delivery_included() -> None:
    for spec in (screen_capture_spec(), fake_write_spec()):
        declaration = json.loads(json.dumps(spec_declaration(spec)))
        assert spec_from_declaration(declaration) == spec
    assert spec_from_declaration({"name": SCREEN_CAPTURE}) is None
    assert spec_from_declaration("screen.capture") is None
    declaration = spec_declaration(fake_write_spec())
    assert declaration["delivery"] == {"text_argument": "text"}
    del declaration["delivery"]
    assert spec_from_declaration(declaration) != fake_write_spec()


# --------------------------------------------------------------------------- #
# prepare: binding, not readiness
# --------------------------------------------------------------------------- #


async def test_prepare_binds_the_remote_provider_and_marks_nothing_ready() -> None:
    """Decision 7: the provider is bound over the catalog spec's declared
    destinations at ``prepare``; the actions are not ready, so the executor
    refuses a call ``provider_not_ready`` with 0 providers entered."""

    brain = Brain()
    try:
        module = await brain.activate()
        for name in (SCREEN_CAPTURE, FAKE_WRITE):
            bindings = brain.context.actions.bindings(name)
            assert [b.provider_name for b in bindings] == [PROVIDER_NAME] * len(bindings)
            assert {b.destination for b in bindings} == set(
                brain.context.actions.discovered()[name].supported_destinations
            )
            assert all(b.module == MODULE_NAME for b in bindings)
        assert not brain.context.actions.is_ready(MODULE_NAME)
        assert brain.authorized() == []
        assert module.bound_actions.keys() == {SCREEN_CAPTURE, FAKE_WRITE}

        observation = await brain.executor.invoke(capture_call(deadline=brain.clock() + 30))
        assert observation.status == "refused"
        assert observation.error["code"] == ERROR_PROVIDER_NOT_READY
        assert brain.executor.provider_invocations == 0
    finally:
        await brain.close()


async def test_prepare_refuses_an_allowlisted_name_no_manifest_declares() -> None:
    brain = Brain(actions=(SCREEN_CAPTURE, "audio.speak"))
    try:
        with pytest.raises(ProxyModuleError, match=r"actions\[1\].*'audio.speak'"):
            await brain.activate()
        assert not brain.context.actions.is_ready(MODULE_NAME)
        degraded = events_of(brain.bus, TRACE_MODULE_DEGRADED)
        assert degraded and "actions[1]" in degraded[-1]["payload"]["reason"]
        assert brain.diagnostics and "actions[1]" in brain.diagnostics[-1]
    finally:
        await brain.close()


async def test_prepare_takes_the_spec_from_the_manifest_catalog_when_capture_is_disabled() -> None:
    """Decision 7, the server profile: ``capture`` is disabled, so no enabled
    manifest declares ``screen.capture`` and the registry's discovered view
    lacks it. The proxy takes the spec from the loader's full manifest
    catalog, declares it under its own name, binds itself, and the paired
    agent makes it ready — the same contract the local provider would serve."""

    brain = Brain(
        actions=(SCREEN_CAPTURE,),
        specs=(fake_write_spec(),),
        catalog={"capture": capture_manifest(), MODULE_NAME: yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))},
    )
    try:
        assert SCREEN_CAPTURE not in brain.context.actions.discovered()
        module = await brain.activate()
        discovered = brain.context.actions.discovered()
        assert discovered[SCREEN_CAPTURE] == screen_capture_spec()
        bindings = brain.context.actions.bindings(SCREEN_CAPTURE)
        assert bindings and all(b.provider_name == PROVIDER_NAME and b.module == MODULE_NAME for b in bindings)
        assert module.bound_actions.keys() == {SCREEN_CAPTURE}
        assert not brain.context.actions.is_ready(MODULE_NAME)

        agent = brain.connect()
        welcome = await agent.pair_up(specs=(screen_capture_spec(),))
        assert welcome["actions"] == [SCREEN_CAPTURE] and agent.mismatches == []
        assert brain.authorized() == [SCREEN_CAPTURE]

        task = asyncio.create_task(brain.executor.invoke(capture_call(deadline=brain.clock() + 30)))
        call = await agent.call_frame()
        assert call["action_name"] == SCREEN_CAPTURE
        await agent.observe(call["call_id"], status="error", error={"code": "capture_failed", "message": "no frame", "retryable": True})
        observation = await task
        assert observation.status == "error" and observation.provenance["provider"] == PROVIDER_NAME
    finally:
        await brain.close()


async def test_prepare_prefers_the_discovered_spec_and_declares_nothing_from_the_catalog() -> None:
    """With ``screen.capture`` discovered, the catalog is not consulted: a
    catalog manifest declaring it differently changes nothing."""

    other = capture_manifest()
    (entry,) = [item for item in other["actions"] if item["name"] == SCREEN_CAPTURE]
    entry["timeout_seconds"] = 99
    brain = Brain(actions=(SCREEN_CAPTURE,), catalog={"capture": other})
    try:
        await brain.activate()
        assert brain.context.actions.discovered()[SCREEN_CAPTURE] == screen_capture_spec()
    finally:
        await brain.close()


async def test_prepare_refuses_a_name_two_catalog_manifests_declare_differently() -> None:
    other = capture_manifest()
    other["name"] = "capture2"
    (entry,) = [item for item in other["actions"] if item["name"] == SCREEN_CAPTURE]
    entry["timeout_seconds"] = 99
    brain = Brain(
        actions=(FAKE_WRITE, SCREEN_CAPTURE),
        specs=(fake_write_spec(),),
        catalog={"capture": capture_manifest(), "capture2": other},
    )
    try:
        with pytest.raises(ProxyModuleError, match=r"actions\[1\].*'screen.capture'.*differently"):
            await brain.activate()
        assert SCREEN_CAPTURE not in brain.context.actions.discovered()
        assert not brain.context.actions.is_ready(MODULE_NAME)
        assert brain.diagnostics and "actions[1]" in brain.diagnostics[-1]
    finally:
        await brain.close()


async def test_server_profile_capture_disabled_proxy_serves_screen_capture_through_the_loader(
    tmp_path: Path,
) -> None:
    """Decision 7, real loader and coordinator: the server profile enables
    ``proxy`` with ``actions: [screen.capture]`` and leaves ``capture``
    disabled. Startup succeeds, the listener opens, and once an agent pairs
    ``screen.capture`` is registered ready with the proxy as its provider."""

    clock = ManualClock()
    store = AttachmentStore(clock=clock, **STORE_LIMITS)
    context = runtime_context(clock=clock, attachments=store)
    loader = ModuleLoader(context.bus, ROOT / "modules", context=context, environ={})
    servers: list[FakeServer] = []

    async def factory(handler: Any, **kwargs: Any) -> FakeServer:
        server = FakeServer(handler=handler, **kwargs)
        servers.append(server)
        return server

    activations = await loader.activate_enabled(
        {
            "enabled_modules": [MODULE_NAME],
            "modules": {
                MODULE_NAME: {
                    **BASE_SETTINGS,
                    "actions": [SCREEN_CAPTURE],
                    "_server_factory": factory,
                    "_sleeper": clock.sleep,
                },
            },
        }
    )
    assert "capture" in loader.catalog and "capture" not in {a.name for a in activations}
    (activation,) = activations
    diagnostics: list[str] = []
    coordinator = PhaseCoordinator(activations, tasks=context.tasks, reporter=diagnostics.append)
    report = await coordinator.start()
    try:
        assert report.status == 0, (report, diagnostics)
        assert len(servers) == 1
        assert context.actions.discovered()[SCREEN_CAPTURE] == screen_capture_spec()
        bindings = context.actions.bindings(SCREEN_CAPTURE)
        assert bindings and all(b.provider_name == PROVIDER_NAME for b in bindings)
        assert SCREEN_CAPTURE not in context.actions.registered_ready()

        pair = MemoryWebSocketPair()
        handler = asyncio.create_task(servers[0].kwargs["handler"](pair.server))
        agent = Agent(SimpleNamespace(module=activation.handle), pair, handler)
        welcome = await agent.pair_up(specs=(screen_capture_spec(),))
        assert welcome["actions"] == [SCREEN_CAPTURE]
        assert SCREEN_CAPTURE in context.actions.registered_ready()

        # The agent leaves: readiness is withdrawn and the heartbeat task,
        # parked on the manual clock, is cancelled before the drain.
        await pair.client.close()
        await wait_until(handler.done)
        assert SCREEN_CAPTURE not in context.actions.registered_ready()
    finally:
        await coordinator.stop()


async def test_ac41_remote_a_local_provider_on_the_same_action_is_an_ambiguity_naming_both() -> None:
    """AC41 (R7): with a local provider already bound to ``screen.capture``,
    preparation fails with the registry's ambiguity diagnostic naming the
    action and both providers; nothing is ready."""

    brain = Brain(actions=(SCREEN_CAPTURE,))
    local = SimpleNamespace(name="capture", invoke=lambda invocation: None)
    brain.context.actions.bind(SCREEN_CAPTURE, local, module="capture", provider_name="capture")
    try:
        with pytest.raises(ProxyModuleError) as refused:
            await brain.activate()
        message = str(refused.value)
        assert SCREEN_CAPTURE in message
        assert "'capture'" in message and f"'{PROVIDER_NAME}'" in message
        assert "Ambiguous binding" in message
        assert not brain.context.actions.is_ready(MODULE_NAME)
        degraded = events_of(brain.bus, TRACE_MODULE_DEGRADED)
        assert any(
            SCREEN_CAPTURE in e["payload"]["reason"]
            and "'capture'" in e["payload"]["reason"]
            and f"'{PROVIDER_NAME}'" in e["payload"]["reason"]
            for e in degraded
        )
    finally:
        await brain.close()


async def test_ac41_capture_and_proxy_enabled_together_fail_startup_through_the_loader(
    tmp_path: Path,
) -> None:
    """AC41 (R7), real loader and coordinator: enabling ``capture`` and
    ``proxy`` with ``actions: [screen.capture]`` in one profile fails
    preparation; ``module.degraded`` carries the ambiguity diagnostic naming
    ``screen.capture`` and both providers, and the action is never ready."""

    frame = tmp_path / "frame.png"
    frame.write_bytes(png_bytes(4, 4))
    clock = ManualClock()
    store = AttachmentStore(clock=clock, **STORE_LIMITS)
    context = runtime_context(clock=clock, attachments=store)
    loader = ModuleLoader(context.bus, ROOT / "modules", context=context, environ={})
    servers: list[FakeServer] = []

    async def factory(handler: Any, **kwargs: Any) -> FakeServer:
        server = FakeServer(handler=handler, **kwargs)
        servers.append(server)
        return server

    activations = await loader.activate_enabled(
        {
            "enabled_modules": ["capture", MODULE_NAME],
            "modules": {
                "capture": {
                    "sources": {"screen": {"kind": "file", "path": str(frame)}},
                    "default_source": "screen",
                },
                MODULE_NAME: {
                    **BASE_SETTINGS,
                    "actions": [SCREEN_CAPTURE],
                    "_server_factory": factory,
                    "_sleeper": clock.sleep,
                },
            },
        }
    )
    assert len(activations) == 2
    diagnostics: list[str] = []
    coordinator = PhaseCoordinator(activations, tasks=context.tasks, reporter=diagnostics.append)

    report = await coordinator.start()

    assert report.status != 0
    assert SCREEN_CAPTURE not in context.actions.registered_ready()
    assert servers == []
    reasons = [e["payload"]["reason"] for e in events_of(context.bus, TRACE_MODULE_DEGRADED)]
    assert any(
        SCREEN_CAPTURE in reason and "'capture'" in reason and f"'{PROVIDER_NAME}'" in reason
        for reason in reasons
    ), reasons
    assert all(str(tmp_path) not in reason for reason in reasons)
    await coordinator.stop()


async def test_start_inputs_opens_the_listener_with_tls_off_and_stop_close_release_it() -> None:
    brain = Brain(max_frame_bytes=4096, max_attachment_bytes=2048)
    try:
        module = await brain.activate(start=True)
        (server,) = brain.servers
        assert server.kwargs["host"] == "127.0.0.1" and server.kwargs["port"] == 8765
        assert server.kwargs["ssl_context"] is None
        assert server.kwargs["handler"] == module.connection_handler
        # Each bound reaches the listener on its own: text frames are bounded
        # by ``max_frame_bytes``, binary frames by the binary bound (§6).
        assert server.kwargs["max_frame_bytes"] == 4096
        assert server.kwargs["max_attachment_bytes"] == 2048
        assert callable(server.kwargs["diagnose"])
        await module.start_inputs()
        assert len(brain.servers) == 1
        await module.stop_inputs()
        assert server.stopped and not server.closed
        await module.close()
        assert server.closed
        await module.close()
    finally:
        await brain.close()


def ws_frame(opcode: int, payload: bytes, *, fin: bool = True, mask: bool = True) -> bytes:
    """One RFC 6455 frame as a client sends it (masked), any length flag."""

    first = (0x80 if fin else 0) | opcode
    mask_bit = 0x80 if mask else 0
    size = len(payload)
    if size < 126:
        header = bytes([first, mask_bit | size])
    elif size < 65_536:
        header = bytes([first, mask_bit | 126]) + size.to_bytes(2, "big")
    else:
        header = bytes([first, mask_bit | 127]) + size.to_bytes(8, "big")
    if not mask:
        return header + payload
    key = b"\x21\x43\x65\x87"
    return header + key + bytes(byte ^ key[at % 4] for at, byte in enumerate(payload))


class GateHarness:
    """A :class:`_FrameSizeGate` over a recording parser and reader queue."""

    def __init__(self, *, text_bound: int, binary_bound: int) -> None:
        self.fed = bytearray()
        self.errors: list[Any] = []
        inner = SimpleNamespace(
            feed_data=lambda data: (self.fed.extend(data), (False, b""))[1],
            feed_eof=lambda: None,
        )
        queue = SimpleNamespace(set_exception=self.errors.append)
        self.gate = _FrameSizeGate(inner, queue, text_bound, binary_bound)

    def feed(self, data: bytes, *, chunk: int | None = None) -> list[tuple[bool, bytes]]:
        if chunk is None:
            return [self.gate.feed_data(data)]
        return [self.gate.feed_data(data[at:at + chunk]) for at in range(0, len(data), chunk)]


@pytest.mark.parametrize("chunk", [None, 1, 7, 1000])
def test_the_frame_gate_bounds_binary_and_text_apart_on_the_header(chunk: int | None) -> None:
    """§6: with ``max_frame_bytes`` 4096 and a binary bound of 2048, a
    3000-byte text frame passes and a 3000-byte binary frame is refused on
    its header — the parser behind the gate never sees a payload byte —
    whatever the chunking of the byte stream."""

    text = ws_frame(1, b"t" * 3000)
    harness = GateHarness(text_bound=4096, binary_bound=2048)
    assert all(not eof for eof, _ in harness.feed(text, chunk=chunk))
    assert bytes(harness.fed) == text and harness.errors == []

    # A control frame and a binary frame at the bound pass too.
    ping = ws_frame(9, b"p" * 125)
    at_bound = ws_frame(2, b"b" * 2048)
    harness.feed(ping + at_bound, chunk=chunk)
    assert bytes(harness.fed) == text + ping + at_bound and harness.errors == []

    oversized = ws_frame(2, b"b" * 3000)
    outcomes = harness.feed(oversized, chunk=chunk)
    assert outcomes[-1] == (True, b"") or any(eof for eof, _ in outcomes)
    (error,) = harness.errors
    assert error.code == 1009 and "binary" in str(error) and "2048" in str(error)
    # No payload byte of the refused frame reached the parser: at most the
    # header bytes fed before its length was complete (the 4-byte header of
    # a 3000-byte frame, minus the byte that completes it). The gate stays
    # failed afterwards.
    passed = text + ping + at_bound
    extra = bytes(harness.fed)[len(passed):]
    assert bytes(harness.fed).startswith(passed)
    assert extra == oversized[: len(extra)] and len(extra) < 4
    assert harness.gate.failed
    assert harness.gate.feed_data(b"more") == (True, b"more")


def test_the_frame_gate_bounds_fragmented_messages_on_their_accumulated_length() -> None:
    harness = GateHarness(text_bound=4096, binary_bound=2048)
    first = ws_frame(2, b"a" * 1500, fin=False)
    second = ws_frame(0, b"b" * 500)
    harness.feed(first + second)
    assert harness.errors == [] and bytes(harness.fed) == first + second

    # A text message may be fragmented past the binary bound: its bound is
    # ``max_frame_bytes``.
    text = ws_frame(1, b"t" * 1500, fin=False) + ws_frame(0, b"t" * 1500)
    harness.feed(text)
    assert harness.errors == []

    # A binary message whose continuation takes it past 2048 is refused on
    # that continuation's header.
    harness.feed(ws_frame(2, b"a" * 1500, fin=False))
    fed_before = bytes(harness.fed)
    harness.feed(ws_frame(0, b"b" * 549))
    (error,) = harness.errors
    assert error.code == 1009 and bytes(harness.fed) == fed_before


def test_the_frame_gate_refuses_a_text_frame_above_max_frame_bytes_too() -> None:
    harness = GateHarness(text_bound=1024, binary_bound=65_536)
    harness.feed(ws_frame(2, b"b" * 60_000))
    assert harness.errors == []
    harness.feed(ws_frame(1, b"t" * 1025, mask=False))
    (error,) = harness.errors
    assert error.code == 1009 and "max_frame_bytes" in str(error)
    # Without the listener's error type the gate fails the reader with its
    # own: it needs no ``aiohttp`` (the suite must pass without it installed).
    assert isinstance(error, _MessageTooBig)


async def test_the_real_listener_cuts_an_oversized_binary_frame_before_the_module_sees_it() -> None:
    """The shipped ``aiohttp`` listener with ``max_frame_bytes`` 4096 and a
    binary bound of 2048: a 3000-byte text frame reaches the module (it is
    answered); a 3000-byte binary frame — legal for the reader's single
    bound — is cut by the gate: the socket closes 1009 and the module's
    binary handler never runs. No diagnostic says the gate did not install."""

    aiohttp = pytest.importorskip("aiohttp")
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    brain = Brain(
        max_frame_bytes=4096,
        max_attachment_bytes=2048,
        _server_factory=default_server_factory,
        listen={"host": "127.0.0.1", "port": port},
    )
    binary_frames: list[int] = []
    try:
        module = await brain.activate(start=True)
        original = module._on_binary

        async def spy(connection: Any, data: Any) -> None:
            binary_frames.append(len(data))
            await original(connection, data)

        module._on_binary = spy  # type: ignore[method-assign]
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(f"ws://127.0.0.1:{port}/", max_msg_size=0) as ws:
                text = json.dumps({"v": 1, "type": FRAME_PING, "id": "x" * 2900})
                assert 2048 < len(text.encode("utf-8")) < 4096
                await ws.send_str(text)
                message = await ws.receive()
                assert message.type == WSMsgType.TEXT
                assert json.loads(message.data)["code"] == PROXY_ERROR_UNKNOWN_SESSION

                await ws.send_bytes(b"\x00" * 3000)
                # No ``error`` text frame precedes the close: the payload was
                # never read, so the module had nothing to answer.
                message = await ws.receive()
                assert message.type == WSMsgType.CLOSE and message.data == 1009, message
                await wait_until(lambda: ws.closed)
                assert ws.close_code == 1009
        assert binary_frames == []
        await wait_until(lambda: not module._connections)
        assert not any("gate did not install" in d for d in brain.diagnostics), brain.diagnostics
    finally:
        await brain.close()


async def test_start_inputs_requires_prepare_and_reports_a_listener_failure_without_values() -> None:
    brain = Brain()
    try:
        module = await brain.activate(prepare=False)
        with pytest.raises(ProxyModuleError, match="requires prepare"):
            await module.start_inputs()
    finally:
        await brain.close()

    async def failing(handler: Any, **kwargs: Any) -> Any:
        raise OSError("address already in use: 127.0.0.1:8765")

    brain = Brain(_server_factory=failing)
    try:
        module = await brain.activate()
        with pytest.raises(ProxyModuleError, match="listener could not be opened"):
            await module.start_inputs()
        assert all("127.0.0.1" not in d for d in brain.diagnostics)
    finally:
        await brain.close()


async def test_activate_refuses_bad_settings_and_an_incomplete_context() -> None:
    brain = Brain()
    try:
        with pytest.raises(ProxyModuleError, match="settings were refused"):
            await activate(brain.context.for_module(MODULE_NAME), {**BASE_SETTINGS}, {})
        with pytest.raises(ProxyModuleError, match="settings must be a mapping"):
            await activate(brain.context.for_module(MODULE_NAME), "nope", {})  # type: ignore[arg-type]
        without_store = runtime_context(clock=brain.clock)
        with pytest.raises(ProxyModuleError, match="runtime context is invalid"):
            await activate(without_store.for_module(MODULE_NAME), {**BASE_SETTINGS, "actions": []}, {})
    finally:
        await brain.close()


# --------------------------------------------------------------------------- #
# AC32 — token, agent limit, protocol version, frame size
# --------------------------------------------------------------------------- #


async def test_ac32_a_wrong_token_is_auth_failed_4401_and_nothing_becomes_ready() -> None:
    brain = Brain()
    try:
        module = await brain.activate()
        agent = brain.connect()
        await agent.send(agent.hello_frame(token="not-the-token"))
        frame = await agent.expect_refusal(PROXY_ERROR_AUTH_FAILED, PROXY_CLOSE_AUTH_FAILED)
        assert frame["id"] == "h1" and frame["retryable"] is False
        assert not brain.context.actions.is_ready(MODULE_NAME)
        assert brain.authorized() == []
        assert not module.paired
        # The token never appears in any frame the brain wrote.
        assert all(TOKEN not in (m.data or "") for m in agent.pair.server.sent if isinstance(m.data, str))
    finally:
        await brain.close()


async def test_ac32_a_second_agent_is_agent_limit_4409_while_the_first_stays() -> None:
    brain = Brain()
    try:
        module = await brain.activate()
        first = brain.connect()
        welcome = await first.pair_up()
        assert module.paired and module.agent_id == AGENT_ID

        second = brain.connect()
        await second.send(second.hello_frame(nonce="h2", agent_id="agent-pc-2"))
        frame = await second.expect_refusal(PROXY_ERROR_AGENT_LIMIT, PROXY_CLOSE_AGENT_LIMIT)
        assert frame["id"] == "h2" and frame["retryable"] is True

        # The first is untouched: still paired, same session, still answering.
        assert module.session_id == welcome["session_id"]
        assert not first.ws.closed and not first.handler.done()
        await first.send({"type": FRAME_PING, "id": first.session_id})
        pong = await first.frame()
        assert pong == {"v": 1, "type": FRAME_PONG, "id": first.session_id}
        assert brain.authorized() == [FAKE_WRITE, SCREEN_CAPTURE]
    finally:
        await brain.close()


async def test_ac32_a_second_hello_while_the_first_is_still_being_answered_is_agent_limit() -> None:
    """The pairing slot is taken before the awaited ``action_mismatch``
    replies, not after them: a second ``hello`` arriving while the first is
    still being answered is ``agent_limit`` 4409, and the first pairs alone
    once its replies go out — never two welcomed agents."""

    brain = Brain()
    try:
        module = await brain.activate()
        reached, release = asyncio.Event(), asyncio.Event()

        class SlowMismatch:
            """The first connection's server end: the mismatch reply parks."""

            def __init__(self, ws: Any) -> None:
                self._ws = ws

            def __getattr__(self, name: str) -> Any:
                return getattr(self._ws, name)

            async def send_str(self, text: str) -> None:
                if PROXY_ERROR_ACTION_MISMATCH in text:
                    reached.set()
                    await release.wait()
                await self._ws.send_str(text)

        pair = MemoryWebSocketPair()
        handler = asyncio.create_task(module.connection_handler(SlowMismatch(pair.server)))
        brain.handlers.append(handler)
        first = Agent(brain, pair, handler)
        mismatching = {**spec_declaration(screen_capture_spec()), "version": 2}
        await first.send(first.hello_frame(declarations=[spec_declaration(fake_write_spec()), mismatching]))
        await wait_until(reached.is_set)
        assert not module.paired

        second = brain.connect()
        await second.send(second.hello_frame(nonce="h2", agent_id="agent-pc-2"))
        frame = await second.expect_refusal(PROXY_ERROR_AGENT_LIMIT, PROXY_CLOSE_AGENT_LIMIT)
        assert frame["id"] == "h2" and frame["retryable"] is True
        assert not module.paired

        release.set()
        await first.expect_error(PROXY_ERROR_ACTION_MISMATCH)
        welcome = await first.frame()
        assert welcome["type"] == FRAME_WELCOME and welcome["actions"] == [FAKE_WRITE]
        assert module.paired and module.agent_id == AGENT_ID
        assert module.session_id == welcome["session_id"]
        assert module.accepted_actions == frozenset({FAKE_WRITE})

        # The slot is released with the pairing: once the first leaves, a
        # new agent pairs.
        await pair.client.close()
        await wait_until(handler.done)
        assert not module.paired
        third = brain.connect()
        welcome = await third.pair_up(agent_id="agent-pc-3")
        assert module.agent_id == "agent-pc-3"
    finally:
        await brain.close()


async def test_ac32_protocol_version_2_is_unsupported_and_closed_4400() -> None:
    brain = Brain()
    try:
        await brain.activate()
        agent = brain.connect()
        await agent.send(agent.hello_frame(), v=2)
        await agent.expect_refusal(PROXY_ERROR_UNSUPPORTED_PROTOCOL_VERSION, PROXY_CLOSE_BAD_REQUEST)

        # Also after pairing, and for a non-integer ``v``.
        agent = brain.connect()
        await agent.pair_up()
        await agent.send({"type": FRAME_PING, "id": agent.session_id}, v="1")
        await agent.expect_refusal(PROXY_ERROR_UNSUPPORTED_PROTOCOL_VERSION, PROXY_CLOSE_BAD_REQUEST)
    finally:
        await brain.close()


async def test_ac32_a_1048577_byte_text_frame_is_frame_too_large_4413() -> None:
    """The bound is measured on the UTF-8 encoded frame under the default
    ``max_frame_bytes`` of 1048576: one byte over is refused and closed."""

    brain = Brain()
    try:
        await brain.activate()
        agent = brain.connect()
        def padded(frame_type: str, size: int) -> str:
            template = json.dumps({"v": 1, "type": frame_type, "id": "h1", "pad": ""}, separators=(",", ":"))
            text = json.dumps(
                {"v": 1, "type": frame_type, "id": "h1", "pad": "x" * (size - len(template))},
                separators=(",", ":"),
            )
            assert len(text.encode("utf-8")) == size
            return text

        await agent.send_raw(padded(FRAME_HELLO, 1_048_577))
        frame = await agent.expect_refusal(PROXY_ERROR_FRAME_TOO_LARGE, PROXY_CLOSE_FRAME_TOO_LARGE)
        assert frame["id"] is None

        # Exactly at the bound is read (and then refused for what it is).
        agent = brain.connect()
        await agent.send_raw(padded("padding", 1_048_576))
        error = await agent.expect_error(PROXY_ERROR_UNKNOWN_FRAME)
        assert error["id"] == "h1" and not agent.ws.closed
    finally:
        await brain.close()


async def test_a_configured_max_frame_bytes_bounds_the_text_frame() -> None:
    brain = Brain(max_frame_bytes=256)
    try:
        await brain.activate()
        agent = brain.connect()
        # The hello itself is larger than 256 bytes: refused before parsing.
        await agent.send(agent.hello_frame())
        await agent.expect_refusal(PROXY_ERROR_FRAME_TOO_LARGE, PROXY_CLOSE_FRAME_TOO_LARGE)
    finally:
        await brain.close()


# --------------------------------------------------------------------------- #
# AC48 — correlation: hello nonce, session id, session-scoped frames
# --------------------------------------------------------------------------- #


async def test_ac48_welcome_echoes_the_nonce_issues_a_session_id_and_pings_correlate() -> None:
    brain = Brain()
    try:
        module = await brain.activate()
        agent = brain.connect()

        # A ping before any hello: unknown_session with id null, kept open.
        await agent.send({"type": FRAME_PING, "id": "whatever"})
        error = await agent.expect_error(PROXY_ERROR_UNKNOWN_SESSION)
        assert error["id"] is None
        assert not agent.ws.closed and not agent.handler.done()

        welcome = await agent.pair_up(nonce="h1")
        assert welcome["id"] == "h1"
        assert isinstance(welcome["session_id"], str) and welcome["session_id"]
        assert welcome["actions"] == [SCREEN_CAPTURE, FAKE_WRITE]
        assert welcome["limits"] == {
            "max_frame_bytes": 1_048_576,
            "max_attachment_bytes": STORE_LIMITS["max_object_bytes"],  # min(store, setting)
            "heartbeat_seconds": 15.0,
            "heartbeat_timeout_seconds": 10.0,
        }
        assert module.session_id == welcome["session_id"]

        await agent.send({"type": FRAME_PING, "id": welcome["session_id"]})
        pong = await agent.frame()
        assert pong["type"] == FRAME_PONG and pong["id"] == welcome["session_id"]

        await agent.send({"type": FRAME_PING, "id": "other"})
        error = await agent.expect_error(PROXY_ERROR_UNKNOWN_SESSION)
        assert error["id"] == "other"
        assert agent.received_types().count(FRAME_PONG) == 1
        assert not agent.ws.closed
    finally:
        await brain.close()


@pytest.mark.parametrize("nonce", [None, "", 7])
async def test_ac48_a_hello_with_a_missing_or_empty_id_is_invalid_frame_4400(nonce: Any) -> None:
    brain = Brain()
    try:
        await brain.activate()
        agent = brain.connect()
        await agent.send(agent.hello_frame(nonce=nonce))
        frame = await agent.expect_refusal(PROXY_ERROR_INVALID_FRAME, PROXY_CLOSE_BAD_REQUEST)
        assert frame["id"] is None
        assert not brain.context.actions.is_ready(MODULE_NAME)
    finally:
        await brain.close()


async def test_session_ids_come_from_the_injected_rng_and_are_never_reused() -> None:
    """Two brains seeded alike issue the same first session id; one brain
    never issues the same id twice across pairings."""

    ids: list[str] = []
    for _ in range(2):
        brain = Brain()
        try:
            await brain.activate()
            agent = brain.connect()
            ids.append((await agent.pair_up())["session_id"])
        finally:
            await brain.close()
    assert ids[0] == ids[1]

    brain = Brain()
    try:
        await brain.activate()
        seen: set[str] = set()
        for _ in range(3):
            agent = brain.connect()
            welcome = await agent.pair_up()
            assert welcome["session_id"] not in seen
            seen.add(welcome["session_id"])
            await agent.ws.close()
            await wait_until(agent.handler.done)
    finally:
        await brain.close()


async def test_unknown_types_malformed_frames_and_brain_only_frames_keep_the_connection() -> None:
    brain = Brain()
    try:
        await brain.activate()
        agent = brain.connect()
        await agent.send({"type": "teleport", "id": "x"})
        assert (await agent.expect_error(PROXY_ERROR_UNKNOWN_FRAME))["id"] == "x"
        await agent.send_raw("not json at all")
        assert (await agent.expect_error(PROXY_ERROR_INVALID_FRAME))["id"] is None
        await agent.send_raw("[1, 2, 3]")
        await agent.expect_error(PROXY_ERROR_INVALID_FRAME)
        await agent.send_raw(json.dumps({"v": 1, "id": "y"}))
        assert (await agent.expect_error(PROXY_ERROR_INVALID_FRAME))["id"] == "y"
        assert not agent.ws.closed

        await agent.pair_up()
        await agent.send({"type": FRAME_CALL, "id": "c1"})
        await agent.expect_error(PROXY_ERROR_INVALID_FRAME)
        await agent.send({"type": FRAME_WELCOME, "id": "h1"})
        await agent.expect_error(PROXY_ERROR_INVALID_FRAME)
        await agent.send(agent.hello_frame(nonce="h2"))
        await agent.expect_error(PROXY_ERROR_INVALID_FRAME)
        await agent.send({"type": FRAME_EVENT, "id": "other", "event_type": AGENT_STATUS_EVENT, "payload": {"state": "paired"}})
        await agent.expect_error(PROXY_ERROR_UNKNOWN_SESSION)
        assert events_of(brain.bus, AGENT_STATUS_EVENT) == []
        assert not agent.ws.closed and brain.authorized() == [FAKE_WRITE, SCREEN_CAPTURE]
    finally:
        await brain.close()


@pytest.mark.parametrize(
    "mutation",
    [
        {"agent_id": ""},
        {"agent_id": 5},
        {"actions": "screen.capture"},
        {"actions": [{"version": 1}]},
        {"max_frame_bytes": 0},
        {"token": None},
    ],
)
async def test_a_hello_missing_a_required_field_is_invalid_frame_4400(mutation: dict[str, Any]) -> None:
    brain = Brain()
    try:
        await brain.activate()
        agent = brain.connect()
        frame = agent.hello_frame()
        frame.update(mutation)
        await agent.send(frame)
        await agent.expect_refusal(PROXY_ERROR_INVALID_FRAME, PROXY_CLOSE_BAD_REQUEST)
        assert not brain.context.actions.is_ready(MODULE_NAME)
    finally:
        await brain.close()


# --------------------------------------------------------------------------- #
# hello spec comparison
# --------------------------------------------------------------------------- #


async def test_hello_spec_comparison_accepts_identical_and_excludes_a_delivery_less_declaration() -> None:
    """R6: an identical declaration is accepted; a ``delivery``-less
    declaration of an action whose catalog spec declares the capability is
    ``action_mismatch`` (id = the nonce) and excluded; the others stay
    accepted; a declared action outside the allowlist is ignored."""

    brain = Brain()
    try:
        module = await brain.activate()
        agent = brain.connect()
        without_delivery = spec_declaration(fake_write_spec())
        del without_delivery["delivery"]
        stranger = spec_declaration(fake_write_spec())
        stranger["name"] = "audio.speak"
        welcome = await agent.pair_up(
            nonce="nonce-7",
            declarations=[spec_declaration(screen_capture_spec()), without_delivery, stranger],
        )
        assert welcome["actions"] == [SCREEN_CAPTURE]
        assert [m["id"] for m in agent.mismatches] == ["nonce-7"]
        assert FAKE_WRITE in agent.mismatches[0]["message"]
        assert module.accepted_actions == frozenset({SCREEN_CAPTURE})
        assert brain.context.actions.is_ready(MODULE_NAME)

        # Per-action readiness: the excluded action stays bound but the
        # provider answers refused provider_not_ready for it.
        observation = await brain.executor.invoke(write_call(deadline=brain.clock() + 30))
        assert observation.status == "refused"
        assert observation.error["code"] == ERROR_PROVIDER_NOT_READY
        assert FAKE_WRITE in observation.error["message"]
        assert agent.received_types().count(FRAME_CALL) == 0
    finally:
        await brain.close()


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(version=2),
        lambda d: d.update(nature="write"),
        lambda d: d.update(timeout_seconds=11),
        lambda d: d.update(idempotency="natural"),
        lambda d: d.update(required_permissions=[]),
        lambda d: d.update(supported_destinations=[{"platform": "fake", "channel_id": "*", "scope": "capture"}]),
        lambda d: d["result_schema"]["properties"].pop("captured_at"),
        lambda d: d.update(argument_schema={"type": "object"}),
    ],
)
async def test_hello_spec_comparison_excludes_every_differing_field(mutate: Any) -> None:
    brain = Brain(actions=(SCREEN_CAPTURE,), specs=(screen_capture_spec(),))
    try:
        await brain.activate()
        agent = brain.connect()
        declaration = json.loads(json.dumps(spec_declaration(screen_capture_spec())))
        mutate(declaration)
        welcome = await agent.pair_up(declarations=[declaration])
        assert welcome["actions"] == []
        assert len(agent.mismatches) == 1
        assert not brain.context.actions.is_ready(MODULE_NAME)
        assert brain.authorized() == []
    finally:
        await brain.close()


# --------------------------------------------------------------------------- #
# AC33 — the brain side of a call with an attachment by reference
# --------------------------------------------------------------------------- #


async def test_ac33_brain_side_the_call_frame_carries_the_action_call_and_the_budget() -> None:
    brain = Brain()
    try:
        await brain.activate()
        agent = brain.connect()
        await agent.pair_up()
        deadline = brain.clock() + 12.5
        task = asyncio.create_task(
            brain.executor.invoke(capture_call("call-9", deadline=deadline, arguments={"source": "screen"}))
        )
        call = await agent.call_frame()
        assert call["id"] == "call-9" and call["call_id"] == "call-9"
        assert call["seq"] == 1
        assert call["action_name"] == SCREEN_CAPTURE and call["action_version"] == 1
        assert call["arguments"] == {"source": "screen"}
        assert call["run_id"] == RUN_ID and call["conversation_id"] == "conversation-1"
        assert call["source_event_id"] == "source-1" and call["message_id"] == "source-1"
        assert call["principal"] == PRINCIPAL and call["contract_version"] == 1
        assert call["destination"] == {"platform": "fake", "channel_id": "channel-9", "scope": "capture"}
        # The spec timeout (10 s) is the executor's; the frame carries what is
        # left of the call deadline on the brain clock — no monotonic float.
        assert call["remaining_ms"] == 12_500
        assert "deadline" not in call
        assert call["deadline_utc"] == "2023-11-14T22:13:32.500Z"
        assert call["max_attachment_bytes"] == STORE_LIMITS["max_object_bytes"]

        await agent.observe("call-9", status="error", error={"code": "capture_failed", "message": "no frame", "retryable": True}, parts=[])
        observation = await task
        assert observation.status == "error"
        assert observation.error["code"] == "capture_failed" and observation.error["retryable"] is True
        assert observation.provenance["provider"] == PROVIDER_NAME
        assert observation.provenance["module"] == MODULE_NAME
        assert observation.provenance["agent_id"] == AGENT_ID
        assert observation.provenance["session_id"] == agent.session_id
        assert observation.provenance["seq"] == 1
        assert observation.provenance["remote"] == {"provider": "capture", "module": "capture"}

        # seq is per session, strictly increasing.
        task = asyncio.create_task(brain.executor.invoke(capture_call("call-10", deadline=deadline)))
        assert (await agent.call_frame())["seq"] == 2
        await agent.observe("call-10", status="refused", error={"code": "not_authorized", "message": "no rule"})
        assert (await task).status == "refused"
    finally:
        await brain.close()


async def test_ac33_brain_side_the_image_reaches_the_store_after_exactly_one_transfer() -> None:
    """AC33 (brain side): the agent answers ``call`` with one ``attachment``
    header, one binary frame and one ``observation``; the brain sends
    exactly one ``attachment_ack``; the executor adopts the observation with
    the ``image_ref`` rewritten to the brain store's reference, leased to the
    calling run; the observation frame carries no filesystem path."""

    brain = Brain()
    try:
        await brain.activate()
        agent = brain.connect()
        await agent.pair_up()
        image = png_bytes(16, 9)
        task = asyncio.create_task(brain.executor.invoke(capture_call(deadline=brain.clock() + 30)))
        call = await agent.call_frame()

        ack = await agent.transfer("att-1", image, call_id=call["call_id"])
        assert ack == {"v": 1, "type": FRAME_ATTACHMENT_ACK, "id": agent.session_id, "attachment_id": "att-1", "accepted": True}
        assert brain.store.usage(RUN_ID).objects == 1
        assert brain.store.usage(RUN_ID).total_bytes == len(image)

        frame = await agent.observe(
            call["call_id"],
            result=capture_result(len(image)),
            parts=[image_ref("att-1", len(image))],
        )
        assert "/" not in json.dumps(frame).replace("image/png", "")
        observation = await task

        assert observation.status == "success", observation.error
        (part,) = observation.parts
        assert part["attachment_id"] != "att-1"
        ref = brain.store.lookup(part["attachment_id"])
        assert ref is not None and ref.run_id == RUN_ID and ref.size == len(image)
        assert brain.store.get(ref) == image
        assert part["provider_id"] == "capture" and part["size"] == len(image)
        assert observation.result["size"] == len(image)

        assert agent.sent_types() == [FRAME_HELLO, FRAME_ATTACHMENT, "<binary>", FRAME_OBSERVATION]
        assert agent.received_types() == [FRAME_WELCOME, FRAME_CALL, FRAME_ATTACHMENT_ACK]
        assert brain.executor.provider_invocations == 1

        # The run's cleanup releases exactly what was leased.
        released = brain.store.release(RUN_ID)
        assert released.objects == 1 and brain.store.object_count == 0
    finally:
        await brain.close()


async def test_an_image_ref_naming_an_unacknowledged_attachment_is_invalid_result_at_the_executor() -> None:
    brain = Brain()
    try:
        await brain.activate()
        agent = brain.connect()
        await agent.pair_up()
        task = asyncio.create_task(brain.executor.invoke(capture_call(deadline=brain.clock() + 30)))
        call = await agent.call_frame()
        await agent.observe(call["call_id"], result=capture_result(100), parts=[image_ref("never-sent", 100)])
        observation = await task
        assert observation.status == "error"
        assert observation.error["code"] == ERROR_INVALID_RESULT
        assert "never-sent" in observation.error["message"]
        assert brain.store.object_count == 0
    finally:
        await brain.close()


async def test_attachment_refusals_too_large_store_full_unexpected_binary_and_4413() -> None:
    """§10: a header above ``min(store max_object_bytes, max_attachment_bytes)``
    is refused on the header alone; a payload the store cannot afford on
    another axis is ``store_full``; a binary frame no header announced, or
    shorter than announced, is ``unexpected_binary``; a binary frame above
    the bound is ``frame_too_large`` and close 4413."""

    brain = Brain(max_attachment_bytes=1024, store_limits={**STORE_LIMITS, "max_objects": 1})
    try:
        await brain.activate()
        agent = brain.connect()
        welcome = await agent.pair_up()
        assert welcome["limits"]["max_attachment_bytes"] == 1024
        task = asyncio.create_task(brain.executor.invoke(capture_call(deadline=brain.clock() + 30)))
        call = await agent.call_frame()
        assert call["max_attachment_bytes"] == 1024

        await agent.send({"type": FRAME_ATTACHMENT, "id": agent.session_id, "attachment_id": "big", "content_type": "image/png", "size": 1025})
        ack = await agent.frame()
        assert ack["type"] == FRAME_ATTACHMENT_ACK and ack["accepted"] is False
        assert ack["code"] == ATTACHMENT_ACK_TOO_LARGE and ack["attachment_id"] == "big"
        assert brain.store.object_count == 0

        await agent.send_bytes(b"\x00" * 10)
        ack = await agent.frame()
        assert ack["accepted"] is False and ack["code"] == ATTACHMENT_ACK_UNEXPECTED_BINARY

        await agent.send({"type": FRAME_ATTACHMENT, "id": agent.session_id, "attachment_id": "short", "content_type": "image/png", "size": 20})
        await agent.send_bytes(b"\x00" * 19)
        ack = await agent.frame()
        assert ack["accepted"] is False and ack["code"] == ATTACHMENT_ACK_UNEXPECTED_BINARY
        assert ack["attachment_id"] == "short"

        ack = await agent.transfer("first", b"\x01" * 100)
        assert ack["accepted"] is True
        ack = await agent.transfer("second", b"\x02" * 100)
        assert ack["accepted"] is False and ack["code"] == ATTACHMENT_ACK_STORE_FULL
        assert brain.store.object_count == 1

        await agent.send({"type": FRAME_ATTACHMENT, "id": agent.session_id, "attachment_id": "x", "content_type": "text/plain", "size": 5})
        await agent.expect_error(PROXY_ERROR_INVALID_FRAME)
        assert not agent.ws.closed

        await agent.send_bytes(b"\x00" * 1025)
        await agent.expect_refusal(PROXY_ERROR_FRAME_TOO_LARGE, PROXY_CLOSE_FRAME_TOO_LARGE)
        observation = await task
        assert observation.status == "error"
        assert observation.error["code"] == PROXY_ERROR_PROXY_DISCONNECTED
    finally:
        await brain.close()


async def test_a_header_with_no_call_in_flight_is_refused_and_leases_nothing() -> None:
    brain = Brain()
    try:
        await brain.activate()
        agent = brain.connect()
        await agent.pair_up()
        await agent.send({"type": FRAME_ATTACHMENT, "id": agent.session_id, "attachment_id": "a", "content_type": "image/png", "size": 4})
        ack = await agent.frame()
        assert ack["accepted"] is False and ack["code"] == ATTACHMENT_ACK_UNEXPECTED_BINARY
        await agent.send_bytes(b"\x00" * 4)
        ack = await agent.frame()
        assert ack["accepted"] is False and ack["code"] == ATTACHMENT_ACK_UNEXPECTED_BINARY
        assert brain.store.object_count == 0
        assert not agent.ws.closed
    finally:
        await brain.close()


async def test_an_attachment_is_leased_to_the_run_of_the_call_the_header_names() -> None:
    brain = Brain()
    try:
        await brain.activate()
        agent = brain.connect()
        await agent.pair_up()
        deadline = brain.clock() + 30
        first = asyncio.create_task(brain.executor.invoke(capture_call("c1", run_id="run-A", deadline=deadline)))
        await agent.call_frame()
        second = asyncio.create_task(brain.executor.invoke(capture_call("c2", run_id="run-B", deadline=deadline)))
        await agent.call_frame()

        ack = await agent.transfer("for-a", b"\x0a" * 8, call_id="c1")
        assert ack["accepted"] is True
        ack = await agent.transfer("latest", b"\x0b" * 8)
        assert ack["accepted"] is True
        assert brain.store.usage("run-A").objects == 1
        assert brain.store.usage("run-B").objects == 1

        await agent.observe("c1", result=capture_result(8), parts=[image_ref("for-a", 8)])
        await agent.observe("c2", result=capture_result(8), parts=[image_ref("latest", 8)])
        assert (await first).status == "success"
        assert (await second).status == "success"
    finally:
        await brain.close()


async def test_a_malformed_observation_frame_is_invalid_result_and_uncertain_for_a_write() -> None:
    brain = Brain()
    try:
        await brain.activate()
        agent = brain.connect()
        await agent.pair_up()
        deadline = brain.clock() + 30
        task = asyncio.create_task(brain.executor.invoke(capture_call("r", deadline=deadline)))
        call = await agent.call_frame()
        await agent.send({"type": FRAME_OBSERVATION, "id": call["call_id"], "status": "wat"})
        observation = await task
        assert observation.status == "error" and observation.error["code"] == ERROR_INVALID_RESULT

        task = asyncio.create_task(brain.executor.invoke(write_call("w", deadline=deadline)))
        call = await agent.call_frame()
        await agent.send({"type": FRAME_OBSERVATION, "id": call["call_id"], "status": "success", "result": "not a mapping"})
        observation = await task
        assert observation.status == "external_unknown"
        assert observation.error["code"] == ERROR_INVALID_RESULT

        task = asyncio.create_task(brain.executor.invoke(write_call("w2", deadline=deadline)))
        call = await agent.call_frame()
        await agent.send({"type": FRAME_OBSERVATION, "id": call["call_id"], "status": "refused", "error": "not a mapping"})
        observation = await task
        assert observation.status == "error" and observation.error["code"] == ERROR_INVALID_RESULT
    finally:
        await brain.close()


async def test_an_error_frame_answering_a_call_resolves_it_as_an_error() -> None:
    brain = Brain()
    try:
        await brain.activate()
        agent = brain.connect()
        await agent.pair_up()
        task = asyncio.create_task(brain.executor.invoke(write_call(deadline=brain.clock() + 30)))
        call = await agent.call_frame()
        await agent.send({"type": FRAME_ERROR, "id": call["call_id"], "code": "duplicate_call_unknown", "message": "x", "retryable": False})
        observation = await task
        assert observation.status == "error"
        assert observation.error == {"code": "duplicate_call_unknown", "message": "the agent refused the call frame", "retryable": False}
        # An error naming nothing in flight is ignored, not counted as late.
        await agent.send({"type": FRAME_ERROR, "id": None, "code": "invalid_frame", "message": "x", "retryable": False})
        await agent.send({"type": FRAME_PING, "id": agent.session_id})
        assert (await agent.frame())["type"] == FRAME_PONG
        assert brain.module is not None and brain.module.late_observations == 0
    finally:
        await brain.close()


# --------------------------------------------------------------------------- #
# AC35 — drops, classified by nature, nothing retransmitted
# --------------------------------------------------------------------------- #


async def test_ac35_a_drop_with_a_write_in_flight_is_external_unknown_and_never_retransmitted() -> None:
    brain = Brain()
    try:
        module = await brain.activate()
        agent = brain.connect()
        await agent.pair_up()
        assert brain.authorized() == [FAKE_WRITE, SCREEN_CAPTURE]
        task = asyncio.create_task(brain.executor.invoke(write_call(deadline=brain.clock() + 30)))
        call = await agent.call_frame()
        assert module.in_flight == (call["call_id"],)

        agent.pair.drop()
        observation = await task

        assert observation.status == "external_unknown"
        assert observation.error["code"] == PROXY_ERROR_PROXY_DISCONNECTED
        assert observation.error["retryable"] is False
        assert observation.provenance["emission"] == "emitted"
        assert brain.executor.outcome(call["call_id"]) == observation
        await wait_until(agent.handler.done)
        assert not module.paired and module.in_flight == ()
        assert not brain.context.actions.is_ready(MODULE_NAME)
        assert brain.authorized() == []
        assert brain.authorized(CHAT_DESTINATION) == []
        degraded = events_of(brain.bus, TRACE_MODULE_DEGRADED)
        assert degraded and "disconnected" in degraded[-1]["payload"]["reason"]

        # A call while unpaired is refused by the executor, 0 providers entered.
        refused = await brain.executor.invoke(write_call("call-w2", deadline=brain.clock() + 30))
        assert refused.status == "refused" and refused.error["code"] == ERROR_PROVIDER_NOT_READY
        assert brain.executor.provider_invocations == 1

        # Re-pair: nothing from before is re-sent; seq restarts at 1; the
        # actions are back in the authorized view.
        again = brain.connect()
        welcome = await again.pair_up(nonce="h2")
        assert welcome["session_id"] != agent.session_id
        assert brain.authorized() == [FAKE_WRITE, SCREEN_CAPTURE]
        task = asyncio.create_task(brain.executor.invoke(write_call("call-w3", deadline=brain.clock() + 30)))
        fresh = await again.call_frame()
        assert fresh["call_id"] == "call-w3" and fresh["seq"] == 1
        assert again.received_types().count(FRAME_CALL) == 1
        await again.observe("call-w3", result={"external_id": "m-1"}, provenance={"provider": "fake"})
        assert (await task).status == "success"
    finally:
        await brain.close()


async def test_ac35_a_drop_with_a_read_in_flight_is_error_proxy_disconnected_retryable() -> None:
    brain = Brain()
    try:
        module = await brain.activate()
        agent = brain.connect()
        await agent.pair_up()
        task = asyncio.create_task(brain.executor.invoke(capture_call(deadline=brain.clock() + 30)))
        call = await agent.call_frame()

        await agent.ws.close(code=1000)
        observation = await task

        assert observation.status == "error"
        assert observation.error["code"] == PROXY_ERROR_PROXY_DISCONNECTED
        assert observation.error["retryable"] is True
        assert observation.provenance["emission"] == "unknown"
        assert observation.parts == ()
        assert brain.executor.outcome(call["call_id"]) == observation
        await wait_until(agent.handler.done)
        assert brain.authorized() == []
        assert not module.paired

        # A late observation for the dropped call changes nothing (AC36).
        again = brain.connect()
        await again.pair_up()
        await again.observe(call["call_id"], result=capture_result(4), parts=[])
        await again.send({"type": FRAME_PING, "id": again.session_id})
        assert (await again.frame())["type"] == FRAME_PONG
        assert module.late_observations == 1
        assert brain.executor.outcome(call["call_id"]) == observation
    finally:
        await brain.close()


async def test_close_drops_the_paired_agent_and_classifies_in_flight_calls() -> None:
    brain = Brain()
    try:
        module = await brain.activate(start=True)
        agent = brain.connect()
        await agent.pair_up()
        task = asyncio.create_task(brain.executor.invoke(write_call(deadline=brain.clock() + 30)))
        await agent.call_frame()
        await module.close()
        observation = await task
        assert observation.status == "external_unknown"
        assert observation.error["code"] == PROXY_ERROR_PROXY_DISCONNECTED
        assert await agent.close_frame() == CLOSE_GOING_AWAY
        await wait_until(agent.handler.done)
        assert brain.servers[0].closed
        assert not brain.context.actions.is_ready(MODULE_NAME)
    finally:
        await brain.close()


# --------------------------------------------------------------------------- #
# AC36 (brain side) — late and duplicate observations
# --------------------------------------------------------------------------- #


async def test_ac36_brain_side_late_and_duplicate_observations_change_nothing_and_are_counted() -> None:
    brain = Brain()
    try:
        module = await brain.activate()
        agent = brain.connect()
        await agent.pair_up()

        # Unknown call: +1.
        await agent.observe("never-issued", result=capture_result(4))
        await wait_until(lambda: module.late_observations == 1)

        # A second observation for an observed call: +1, the first stands.
        task = asyncio.create_task(brain.executor.invoke(capture_call("c1", deadline=brain.clock() + 30)))
        await agent.call_frame()
        await agent.observe("c1", status="error", error={"code": "capture_failed", "message": "first"})
        first = await task
        assert first.error["message"] == "first"
        await agent.observe("c1", status="error", error={"code": "capture_failed", "message": "second"})
        await wait_until(lambda: module.late_observations == 2)
        assert brain.executor.outcome("c1") == first
        assert brain.executor.outcome("c1").error["message"] == "first"

        # A call the executor already cancelled (run cancellation): the brain
        # sent ``cancel`` best-effort, the executor's classification stands,
        # the observation that follows is late: +1.
        task = asyncio.create_task(brain.executor.invoke(capture_call("c2", deadline=brain.clock() + 30)))
        await agent.call_frame()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        cancel = await agent.frame()
        assert cancel == {"v": 1, "type": FRAME_CANCEL, "id": "c2"}
        recorded = brain.executor.outcome("c2")
        assert recorded is not None and recorded.status == "cancelled"
        await agent.observe("c2", result=capture_result(4))
        await wait_until(lambda: module.late_observations == 3)
        assert brain.executor.outcome("c2") == recorded
        assert module.in_flight == ()
        assert brain.store.object_count == 0
    finally:
        await brain.close()


async def test_a_cancelled_write_sends_cancel_and_stays_external_unknown() -> None:
    brain = Brain()
    try:
        await brain.activate()
        agent = brain.connect()
        await agent.pair_up()
        task = asyncio.create_task(brain.executor.invoke(write_call(deadline=brain.clock() + 30)))
        await agent.call_frame()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert (await agent.frame())["type"] == FRAME_CANCEL
        recorded = brain.executor.outcome("call-w1")
        assert recorded is not None and recorded.status == "external_unknown"
    finally:
        await brain.close()


# --------------------------------------------------------------------------- #
# AC38 — the event adapter
# --------------------------------------------------------------------------- #


async def test_ac38_agent_status_is_published_with_the_adapter_metadata_and_nothing_else_is() -> None:
    brain = Brain()
    subscriptions: list[Any] = []
    original_subscribe = brain.bus.subscribe

    def recording_subscribe(*args: Any, **kwargs: Any) -> Any:
        subscriptions.append((args, kwargs))
        return original_subscribe(*args, **kwargs)

    brain.bus.subscribe = recording_subscribe  # type: ignore[method-assign]
    try:
        module = await brain.activate(start=True)
        agent = brain.connect()
        await agent.pair_up()
        await agent.send(
            {
                "type": FRAME_EVENT,
                "id": agent.session_id,
                "event_type": AGENT_STATUS_EVENT,
                "payload": {"state": "paired", "actions": [SCREEN_CAPTURE]},
                "metadata": {"source": "twitch", "provider_id": "impostor", "schema_version": 9},
            }
        )
        await wait_until(lambda: bool(events_of(brain.bus, AGENT_STATUS_EVENT)))
        (event,) = events_of(brain.bus, AGENT_STATUS_EVENT)
        assert event["payload"] == {"state": "paired", "actions": [SCREEN_CAPTURE]}
        assert event["metadata"]["source"] == MODULE_NAME
        assert event["metadata"]["provider_id"] == AGENT_ID
        assert "schema_version" not in event["metadata"]

        for forbidden in (TRACE_BRAIN_RUN_COMPLETED, "channel.chat.message", "module.ready", "action.completed", "agent.status.extra", ""):
            await agent.send({"type": FRAME_EVENT, "id": agent.session_id, "event_type": forbidden, "payload": {"state": "x"}})
            error = await agent.expect_error(PROXY_ERROR_EVENT_NOT_ALLOWED)
            assert error["id"] == agent.session_id
        await agent.send({"type": FRAME_EVENT, "id": agent.session_id, "event_type": AGENT_STATUS_EVENT, "payload": {"state": ""}})
        await agent.expect_error(PROXY_ERROR_INVALID_FRAME)
        await agent.send({"type": FRAME_EVENT, "id": agent.session_id, "event_type": AGENT_STATUS_EVENT, "payload": "paired"})
        await agent.expect_error(PROXY_ERROR_INVALID_FRAME)
        assert len(events_of(brain.bus, AGENT_STATUS_EVENT)) == 1
        assert not any(e["type"].startswith("channel.") for e in brain.bus.list_events())
        assert not agent.ws.closed and module.paired

        # No subscription crossed the transport: the proxy subscribed to
        # nothing, and every frame on the wire is a protocol frame.
        assert subscriptions == []
        wire = [json.loads(m.data) for end in agent.pair.ends for m in end.sent if m.type == WSMsgType.TEXT]
        assert {frame["type"] for frame in wire} <= PROXY_FRAME_TYPES
        assert not any("subscribe" in json.dumps(frame) for frame in wire)
    finally:
        await brain.close()


# --------------------------------------------------------------------------- #
# Heartbeat on the injected clock
# --------------------------------------------------------------------------- #


async def test_heartbeat_ping_every_15_seconds_and_a_missing_pong_within_10_is_a_drop() -> None:
    brain = Brain()
    try:
        module = await brain.activate()
        agent = brain.connect()
        await agent.pair_up()

        brain.clock.advance(14)
        await settle()
        assert FRAME_PING not in agent.received_types()
        brain.clock.advance(1)
        await settle()
        ping = await agent.frame()
        assert ping == {"v": 1, "type": FRAME_PING, "id": agent.session_id}

        # A pong in time keeps the session; the next ping comes 15 s later.
        brain.clock.advance(9)
        await agent.send({"type": FRAME_PONG, "id": agent.session_id})
        await settle()
        brain.clock.advance(1)
        await settle()
        assert module.paired and not agent.ws.closed
        brain.clock.advance(15)
        await settle()
        ping = await agent.frame()
        assert ping["type"] == FRAME_PING

        # A pong with another id does not count; no pong within 10 s: drop.
        await agent.send({"type": FRAME_PONG, "id": "other"})
        await agent.expect_error(PROXY_ERROR_UNKNOWN_SESSION)
        brain.clock.advance(9)
        await settle()
        assert module.paired
        # A read issued one second before the drop, well inside its own
        # budget, is classified by the drop and not by the executor's timer.
        task = asyncio.create_task(brain.executor.invoke(capture_call(deadline=brain.clock() + 60)))
        await agent.call_frame()
        brain.clock.advance(1)
        await settle()
        observation = await task
        assert observation.status == "error" and observation.error["code"] == PROXY_ERROR_PROXY_DISCONNECTED
        assert await agent.close_frame() == CLOSE_GOING_AWAY
        await wait_until(agent.handler.done)
        assert not module.paired
        assert brain.authorized() == []
        reasons = [e["payload"]["reason"] for e in events_of(brain.bus, TRACE_MODULE_DEGRADED)]
        assert any("heartbeat" in reason for reason in reasons)
        assert MODULE_NAME not in brain.context.tasks.owners()
    finally:
        await brain.close()


async def test_heartbeat_settings_are_honoured_and_announced() -> None:
    brain = Brain(heartbeat_seconds=2, heartbeat_timeout_seconds=1)
    try:
        module = await brain.activate()
        agent = brain.connect()
        welcome = await agent.pair_up()
        assert welcome["limits"]["heartbeat_seconds"] == 2.0
        assert welcome["limits"]["heartbeat_timeout_seconds"] == 1.0
        brain.clock.advance(2)
        await settle()
        assert (await agent.frame())["type"] == FRAME_PING
        brain.clock.advance(1)
        await settle()
        await wait_until(lambda: not module.paired)
    finally:
        await brain.close()


# --------------------------------------------------------------------------- #
# Package hygiene
# --------------------------------------------------------------------------- #


def test_module_package_is_importable_and_activate_is_a_coroutine() -> None:
    assert (ROOT / "modules" / MODULE_NAME / "__init__.py").is_file()
    assert inspect.iscoroutinefunction(activate)
    assert MANIFEST_PATH.is_file()


def test_the_proxy_package_names_no_platform() -> None:
    """AC31 (R5): platform neutrality — 0 lines mention twitch."""

    for path in (ROOT / "modules" / MODULE_NAME).glob("*"):
        if path.suffix in (".py", ".yaml"):
            assert "twitch" not in path.read_text(encoding="utf-8").lower(), path
