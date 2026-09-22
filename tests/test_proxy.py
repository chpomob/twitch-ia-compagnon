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

import modules.proxy as proxy_module
from modules.proxy import (
    AGENT_STATUS_EVENT,
    CLOSE_GOING_AWAY,
    MANIFEST_PATH,
    MAX_ACKNOWLEDGED_ATTACHMENTS,
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
        authorization: AuthorizationPolicy | None = None,
        **extra_settings: Any,
    ) -> None:
        self.clock = clock if clock is not None else ManualClock()
        # The loader's full manifest catalog, as ``activate`` receives it.
        self.catalog: dict[str, Any] = catalog if catalog is not None else {}
        self.store = AttachmentStore(clock=self.clock, **(store_limits or STORE_LIMITS))
        self.context: RuntimeContext = runtime_context(
            clock=self.clock,
            attachments=self.store,
            authorization=authorization if authorization is not None else grant_everything(),
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


async def test_an_image_ref_naming_an_unacknowledged_attachment_is_attachment_refused_at_the_proxy() -> None:
    """§5.5: an ``image_ref`` naming an ``attachment_id`` this session never
    acknowledged is refused **at the proxy boundary** with the brain's
    ``attachment_refused`` — the executor never sees the reference. (Gate
    finding F4: the test this replaces sent ``"never-sent"`` and asserted
    the executor's ``invalid_result``, encoding the defect.) An upload the
    same call did make alongside the bogus reference is discarded at once:
    nothing will ever name it."""

    brain = Brain()
    try:
        await brain.activate()
        agent = brain.connect()
        await agent.pair_up()
        module = brain.module
        assert module is not None
        task = asyncio.create_task(brain.executor.invoke(capture_call(deadline=brain.clock() + 30)))
        call = await agent.call_frame()
        ack = await agent.transfer("real", b"\x01" * 100, call_id=call["call_id"])
        assert ack["accepted"] is True and brain.store.object_count == 1
        await agent.observe(
            call["call_id"], result=capture_result(100),
            parts=[image_ref("real", 100), image_ref("never-sent", 100)],
        )
        observation = await task
        assert observation.status == "error"
        assert observation.error["code"] == ERROR_ATTACHMENT_REFUSED
        assert observation.error["retryable"] is False
        assert "never-sent" in observation.error["message"]
        assert observation.parts == ()
        assert observation.provenance["provider"] == PROVIDER_NAME
        # Refused before the executor: no lease was validated, and the
        # executor's own record carries the proxy's code, not ``invalid_result``.
        recorded = brain.executor.outcome(call["call_id"])
        assert recorded is not None and recorded.error["code"] == ERROR_ATTACHMENT_REFUSED
        assert brain.store.object_count == 0
        assert module.acknowledged_attachments == 0
        assert module.in_flight == ()
        assert not agent.ws.closed
    finally:
        await brain.close()


async def test_an_image_ref_acknowledged_for_another_call_is_attachment_refused_and_that_call_keeps_it() -> None:
    """§5.5 / §10: an acknowledged identifier may be named only by the
    observation of the call it was uploaded for. Call B naming A's upload is
    refused ``attachment_refused``; A's bytes stay leased and A's own
    observation still resolves them."""

    brain = Brain()
    try:
        await brain.activate()
        agent = brain.connect()
        await agent.pair_up()
        deadline = brain.clock() + 30
        first = asyncio.create_task(brain.executor.invoke(capture_call("c-a", run_id="run-A", deadline=deadline)))
        await agent.call_frame()
        second = asyncio.create_task(brain.executor.invoke(capture_call("c-b", run_id="run-B", deadline=deadline)))
        await agent.call_frame()
        ack = await agent.transfer("for-a", b"\x0a" * 8, call_id="c-a")
        assert ack["accepted"] is True

        await agent.observe("c-b", result=capture_result(8), parts=[image_ref("for-a", 8)])
        observation = await second
        assert observation.status == "error"
        assert observation.error["code"] == ERROR_ATTACHMENT_REFUSED
        assert "for-a" in observation.error["message"] and "c-a" in observation.error["message"]
        assert brain.store.usage("run-A").objects == 1

        await agent.observe("c-a", result=capture_result(8), parts=[image_ref("for-a", 8)])
        observation = await first
        assert observation.status == "success", observation.error
        (part,) = observation.parts
        assert brain.store.lookup(part["attachment_id"]).run_id == "run-A"
    finally:
        await brain.close()


async def test_an_unacknowledged_image_ref_on_a_write_is_external_unknown_attachment_refused() -> None:
    """The refusal keeps the conservative side for a write the agent did not
    say it refused (R2, R5): the effect may have been engaged, so the status
    is ``external_unknown`` with the same ``attachment_refused`` code."""

    brain = Brain()
    try:
        await brain.activate()
        agent = brain.connect()
        await agent.pair_up()
        task = asyncio.create_task(brain.executor.invoke(write_call(deadline=brain.clock() + 30)))
        call = await agent.call_frame()
        await agent.observe(call["call_id"], result={"sent": True}, parts=[image_ref("never-sent", 4)])
        observation = await task
        assert observation.status == "external_unknown"
        assert observation.error["code"] == ERROR_ATTACHMENT_REFUSED
        assert "never-sent" in observation.error["message"]
    finally:
        await brain.close()


async def test_a_malformed_image_ref_identifier_is_still_the_executors_invalid_result() -> None:
    """The boundary refusal is for well-formed identifiers only: an
    ``image_ref`` whose ``attachment_id`` is not text is a malformed part,
    named by the executor's validation as before."""

    brain = Brain()
    try:
        await brain.activate()
        agent = brain.connect()
        await agent.pair_up()
        task = asyncio.create_task(brain.executor.invoke(capture_call(deadline=brain.clock() + 30)))
        call = await agent.call_frame()
        part = image_ref("x", 100)
        part["attachment_id"] = 7
        await agent.observe(call["call_id"], result=capture_result(100), parts=[part])
        observation = await task
        assert observation.status == "error"
        assert observation.error["code"] == ERROR_INVALID_RESULT
        assert brain.store.object_count == 0
    finally:
        await brain.close()


async def test_repeated_image_runs_over_one_connection_leave_no_translation_entry_behind() -> None:
    """Gate finding F2: more than ``MAX_ACKNOWLEDGED_ATTACHMENTS`` sequential
    image runs over ONE connection, each observed and released normally,
    leave the translation table empty after each — it is pruned with the
    call, not with the session — and the store back at zero; the bound is
    never reached and nothing is refused."""

    assert MAX_ACKNOWLEDGED_ATTACHMENTS >= 1
    brain = Brain()
    try:
        await brain.activate()
        agent = brain.connect()
        await agent.pair_up()
        module = brain.module
        assert module is not None
        runs = MAX_ACKNOWLEDGED_ATTACHMENTS + 3
        for index in range(runs):
            run_id = f"run-{index}"
            task = asyncio.create_task(
                brain.executor.invoke(capture_call(f"c-{index}", run_id=run_id, deadline=brain.clock() + 30))
            )
            call = await agent.call_frame()
            ack = await agent.transfer(f"att-{index}", b"\x01" * 8, call_id=call["call_id"])
            assert ack["accepted"] is True, (index, ack)
            assert module.acknowledged_attachments == 1
            await agent.observe(call["call_id"], result=capture_result(8), parts=[image_ref(f"att-{index}", 8)])
            observation = await task
            assert observation.status == "success", (index, observation.error)
            assert module.acknowledged_attachments == 0
            assert brain.store.release(run_id).objects == 1
            assert brain.store.object_count == 0
        assert module.saturated_uploads == 0
        assert module.paired and not agent.ws.closed
        assert agent.received_types().count(FRAME_ATTACHMENT_ACK) == runs
    finally:
        await brain.close()


async def test_translation_entries_are_pruned_on_cancellation_error_release_and_expiry() -> None:
    """Gate finding F2: an entry is tied to its call and to the store's
    lease. It goes when the call is cancelled or answered by an ``error``
    frame, when the run's cleanup releases the object, when the executor
    discards it, or when the store's time-to-live reaps it — whether or not
    an observation ever came."""

    # A short time-to-live, so the expiry case fits inside the call's own
    # 10 s timeout and under the 15 s heartbeat.
    brain = Brain(store_limits={**STORE_LIMITS, "ttl_seconds": 5.0})
    try:
        await brain.activate()
        agent = brain.connect()
        await agent.pair_up()
        module = brain.module
        assert module is not None
        deadline = brain.clock() + 30

        # Cancelled between upload and observation.
        task = asyncio.create_task(brain.executor.invoke(capture_call("c-1", run_id="run-1", deadline=deadline)))
        await agent.call_frame()
        assert (await agent.transfer("a-1", b"\x01" * 8, call_id="c-1"))["accepted"] is True
        assert module.acknowledged_attachments == 1
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert (await agent.frame())["type"] == FRAME_CANCEL
        assert module.acknowledged_attachments == 0

        # Answered by an ``error`` frame.
        task = asyncio.create_task(brain.executor.invoke(capture_call("c-2", run_id="run-2", deadline=deadline)))
        await agent.call_frame()
        assert (await agent.transfer("a-2", b"\x02" * 8, call_id="c-2"))["accepted"] is True
        assert module.acknowledged_attachments == 1
        await agent.send({"type": FRAME_ERROR, "id": "c-2", "code": "invalid_frame", "message": "no", "retryable": False})
        assert (await task).status == "error"
        assert module.acknowledged_attachments == 0

        # Released by the run's cleanup while the call is still in flight:
        # the entry goes at the next pruning — the next upload, the next
        # observation — and an observation naming it is refused at the
        # boundary: the bytes are gone.
        task = asyncio.create_task(brain.executor.invoke(capture_call("c-3", run_id="run-3", deadline=deadline)))
        await agent.call_frame()
        assert (await agent.transfer("a-3", b"\x03" * 8, call_id="c-3"))["accepted"] is True
        assert brain.store.release("run-3").objects == 1
        assert module.acknowledged_attachments == 1  # not yet looked at
        assert (await agent.transfer("a-3b", b"\x03" * 8, call_id="c-3"))["accepted"] is True
        assert module.acknowledged_attachments == 1  # a-3 pruned, a-3b held
        await agent.observe("c-3", result=capture_result(8), parts=[image_ref("a-3", 8)])
        observation = await task
        assert observation.status == "error" and observation.error["code"] == ERROR_ATTACHMENT_REFUSED
        assert "a-3" in observation.error["message"]
        assert module.acknowledged_attachments == 0
        # a-3b was discarded with the refusal; the objects of runs 1 and 2
        # stay leased until their runs' cleanup, as a local provider's would.
        assert brain.store.usage("run-3").objects == 0
        assert brain.store.object_count == 2

        # Released, then named straight away with no upload in between: the
        # observation itself is the pruning, and the reference is refused.
        task = asyncio.create_task(brain.executor.invoke(capture_call("c-5", run_id="run-5", deadline=deadline)))
        await agent.call_frame()
        assert (await agent.transfer("a-5", b"\x05" * 8, call_id="c-5"))["accepted"] is True
        assert brain.store.release("run-5").objects == 1
        assert module.acknowledged_attachments == 1
        await agent.observe("c-5", result=capture_result(8), parts=[image_ref("a-5", 8)])
        observation = await task
        assert observation.status == "error" and observation.error["code"] == ERROR_ATTACHMENT_REFUSED
        assert module.acknowledged_attachments == 0

        # Past the store's time-to-live. Nothing reads the store between
        # the clock advancing and the next upload — ``lookup`` returns an
        # expired reference as stored, and only ``put`` reaps — so the
        # entry must be judged expired by the proxy itself, on the clock.
        task = asyncio.create_task(brain.executor.invoke(capture_call("c-4", run_id="run-4", deadline=deadline)))
        await agent.call_frame()
        assert (await agent.transfer("a-4", b"\x04" * 8, call_id="c-4"))["accepted"] is True
        brain.clock.advance(6)
        assert (await agent.transfer("a-4b", b"\x04" * 8, call_id="c-4"))["accepted"] is True
        assert module.acknowledged_attachments == 1  # a-4 pruned as expired, a-4b held
        assert brain.store.object_count == 1
        await agent.observe("c-4", result=capture_result(8), parts=[image_ref("a-4b", 8)])
        assert (await task).status == "success"
        assert module.acknowledged_attachments == 0

        assert module.saturated_uploads == 0
        assert not agent.ws.closed
    finally:
        await brain.close()


async def test_the_translation_table_at_its_bound_refuses_the_next_upload_store_full_and_counts_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gate finding F2: the bound is explicit and its policy is counted, not
    silent. With the bound lowered to 2 and three calls in flight, the
    third upload is refused ``store_full`` — nothing stored — and
    ``saturated_uploads`` reads 1; once a held call is observed, its entry
    goes and the same upload is accepted. The session stays intact.

    A table at the bound whose leases have all expired is not stuck: the
    next upload finds the expired entries pruned and is accepted, with no
    other operation having made the store reap in between."""

    monkeypatch.setattr(proxy_module, "MAX_ACKNOWLEDGED_ATTACHMENTS", 2)
    brain = Brain()
    try:
        await brain.activate()
        agent = brain.connect()
        await agent.pair_up()
        module = brain.module
        assert module is not None
        deadline = brain.clock() + 30
        tasks = []
        for index in range(3):
            tasks.append(asyncio.create_task(
                brain.executor.invoke(capture_call(f"c-{index}", run_id=f"run-{index}", deadline=deadline))
            ))
            await agent.call_frame()
        assert (await agent.transfer("a-0", b"\x00" * 8, call_id="c-0"))["accepted"] is True
        assert (await agent.transfer("a-1", b"\x01" * 8, call_id="c-1"))["accepted"] is True
        assert module.acknowledged_attachments == 2

        ack = await agent.transfer("a-2", b"\x02" * 8, call_id="c-2")
        assert ack["accepted"] is False and ack["code"] == ATTACHMENT_ACK_STORE_FULL
        assert ack["attachment_id"] == "a-2"
        assert module.saturated_uploads == 1
        assert module.acknowledged_attachments == 2
        assert brain.store.object_count == 2
        assert brain.store.usage("run-2").objects == 0
        assert not agent.ws.closed

        # Observing c-0 frees its entry; the refused upload now fits.
        await agent.observe("c-0", result=capture_result(8), parts=[image_ref("a-0", 8)])
        assert (await tasks[0]).status == "success"
        assert module.acknowledged_attachments == 1
        ack = await agent.transfer("a-2", b"\x02" * 8, call_id="c-2")
        assert ack["accepted"] is True
        assert module.saturated_uploads == 1
        await agent.observe("c-2", result=capture_result(8), parts=[image_ref("a-2", 8)])
        assert (await tasks[2]).status == "success"
        await agent.observe("c-1", result=capture_result(8), parts=[image_ref("a-1", 8)])
        assert (await tasks[1]).status == "success"
        assert module.acknowledged_attachments == 0
    finally:
        await brain.close()


async def test_the_translation_table_at_its_bound_recovers_when_its_leases_expire(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gate finding F2, review A1: ``AttachmentStore.lookup`` returns a
    reference past its deadline as stored and reaps nothing; with the bound
    reached and every lease expired, the pruning must judge expiry on the
    clock itself, or every upload is refused ``store_full`` until some other
    operation makes the store reap. Nothing here touches the store between
    the clock advancing and the upload."""

    monkeypatch.setattr(proxy_module, "MAX_ACKNOWLEDGED_ATTACHMENTS", 2)
    brain = Brain(store_limits={**STORE_LIMITS, "ttl_seconds": 5.0})
    try:
        await brain.activate()
        agent = brain.connect()
        await agent.pair_up()
        module = brain.module
        assert module is not None
        deadline = brain.clock() + 30
        tasks = []
        for index in range(3):
            tasks.append(asyncio.create_task(
                brain.executor.invoke(capture_call(f"c-{index}", run_id=f"run-{index}", deadline=deadline))
            ))
            await agent.call_frame()
        assert (await agent.transfer("a-0", b"\x00" * 8, call_id="c-0"))["accepted"] is True
        assert (await agent.transfer("a-1", b"\x01" * 8, call_id="c-1"))["accepted"] is True
        assert module.acknowledged_attachments == 2

        brain.clock.advance(6)
        ack = await agent.transfer("a-2", b"\x02" * 8, call_id="c-2")
        assert ack["accepted"] is True
        assert module.saturated_uploads == 0
        assert module.acknowledged_attachments == 1  # a-0 and a-1 expired, a-2 held
        assert brain.store.object_count == 1

        # The expired uploads may no longer be named; the live one may.
        await agent.observe("c-0", result=capture_result(8), parts=[image_ref("a-0", 8)])
        observation = await tasks[0]
        assert observation.status == "error" and observation.error["code"] == ERROR_ATTACHMENT_REFUSED
        await agent.observe("c-2", result=capture_result(8), parts=[image_ref("a-2", 8)])
        assert (await tasks[2]).status == "success"
        await agent.observe("c-1", result=capture_result(8))
        assert (await tasks[1]).status == "success"
        assert module.acknowledged_attachments == 0
        assert not agent.ws.closed
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


async def test_a_binary_frame_for_a_call_cancelled_after_its_header_is_refused_and_leases_nothing() -> None:
    """Gate finding F3, interleaving 1: the header is accepted for run A;
    A is cancelled, its terminal record published and its store usage
    released; only then does the binary frame arrive. The payload is
    refused ``unexpected_binary`` naming the header's attachment and no
    object is stored — a lease created now would belong to a run whose
    cleanup has already happened, and nothing would ever release it."""

    brain = Brain()
    try:
        await brain.activate()
        agent = brain.connect()
        await agent.pair_up()
        module = brain.module
        assert module is not None
        task = asyncio.create_task(
            brain.executor.invoke(capture_call("c-a", run_id="run-A", deadline=brain.clock() + 30))
        )
        call = await agent.call_frame()
        assert call["call_id"] == "c-a"

        # The header alone: accepted for c-a, awaiting its payload.
        await agent.send({
            "type": FRAME_ATTACHMENT, "id": agent.session_id, "attachment_id": "late",
            "content_type": "image/png", "size": 8, "call_id": "c-a",
        })
        await settle()
        pending = module._session.pending_header  # type: ignore[union-attr]
        assert pending is not None and pending["attachment_id"] == "late"
        assert pending["owner"].call.call_id == "c-a"

        # Run A is cancelled: the executor records the terminal outcome, the
        # brain tells the agent, and the run's cleanup releases its usage.
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert (await agent.frame()) == {"v": 1, "type": FRAME_CANCEL, "id": "c-a"}
        recorded = brain.executor.outcome("c-a")
        assert recorded is not None and recorded.status == "cancelled"
        assert brain.store.release("run-A").objects == 0
        assert module.in_flight == ()

        # The delayed payload: refused, and nothing is leased to anyone.
        await agent.send_bytes(b"\x00" * 8)
        ack = await agent.frame()
        assert ack["type"] == FRAME_ATTACHMENT_ACK and ack["accepted"] is False
        assert ack["code"] == ATTACHMENT_ACK_UNEXPECTED_BINARY and ack["attachment_id"] == "late"
        assert brain.store.object_count == 0
        assert brain.store.usage("run-A").objects == 0
        assert brain.store.live_runs() == ()
        assert brain.store.release("run-A").objects == 0
        assert not agent.ws.closed

        # The session is intact: a fresh call still transfers normally.
        task = asyncio.create_task(
            brain.executor.invoke(capture_call("c-b", run_id="run-B", deadline=brain.clock() + 30))
        )
        await agent.call_frame()
        ack = await agent.transfer("fresh", b"\x01" * 8, call_id="c-b")
        assert ack["accepted"] is True
        assert brain.store.usage("run-B").objects == 1
        await agent.observe("c-b", result=capture_result(8), parts=[image_ref("fresh", 8)])
        assert (await task).status == "success"
    finally:
        await brain.close()


async def test_a_header_naming_a_terminal_call_is_refused_and_never_charged_to_the_call_in_flight() -> None:
    """Gate finding F3, interleaving 2: run A's call terminates while run
    B's call is still in flight; a delayed header explicitly naming A's
    call is refused ``unexpected_binary`` on the header alone — it is never
    reassigned to B — and the payload that follows is refused as
    unannounced. B's usage stays 0 until B's own transfer. (The module
    used to fall back to the most recent call for any unknown ``call_id``;
    this test replaces that expectation.)"""

    brain = Brain()
    try:
        await brain.activate()
        agent = brain.connect()
        await agent.pair_up()
        module = brain.module
        assert module is not None
        deadline = brain.clock() + 30
        first = asyncio.create_task(brain.executor.invoke(capture_call("c-a", run_id="run-A", deadline=deadline)))
        await agent.call_frame()
        second = asyncio.create_task(brain.executor.invoke(capture_call("c-b", run_id="run-B", deadline=deadline)))
        await agent.call_frame()

        # A terminates (observed without an image); B stays in flight.
        await agent.observe("c-a", status="error", error={"code": "capture_failed", "message": "no frame", "retryable": True})
        assert (await first).status == "error"
        assert module.in_flight == ("c-b",)

        # The delayed upload for A: refused on the header, not charged to B.
        await agent.send({
            "type": FRAME_ATTACHMENT, "id": agent.session_id, "attachment_id": "for-a",
            "content_type": "image/png", "size": 8, "call_id": "c-a",
        })
        ack = await agent.frame()
        assert ack["type"] == FRAME_ATTACHMENT_ACK and ack["accepted"] is False
        assert ack["code"] == ATTACHMENT_ACK_UNEXPECTED_BINARY and ack["attachment_id"] == "for-a"
        assert brain.store.usage("run-B").objects == 0
        assert brain.store.usage("run-A").objects == 0
        await agent.send_bytes(b"\x0a" * 8)
        ack = await agent.frame()
        assert ack["accepted"] is False and ack["code"] == ATTACHMENT_ACK_UNEXPECTED_BINARY
        assert brain.store.object_count == 0

        # A never-issued call is refused the same way, as is a malformed one.
        for call_id in ("never-issued", 7):
            await agent.send({
                "type": FRAME_ATTACHMENT, "id": agent.session_id, "attachment_id": "stray",
                "content_type": "image/png", "size": 8, "call_id": call_id,
            })
            ack = await agent.frame()
            assert ack["accepted"] is False and ack["code"] == ATTACHMENT_ACK_UNEXPECTED_BINARY
            await agent.send_bytes(b"\x00" * 8)
            ack = await agent.frame()
            assert ack["accepted"] is False and ack["code"] == ATTACHMENT_ACK_UNEXPECTED_BINARY
        assert brain.store.object_count == 0
        assert module.in_flight == ("c-b",)

        # B's own upload, naming B, is what B is charged for.
        ack = await agent.transfer("for-b", b"\x0b" * 8, call_id="c-b")
        assert ack["accepted"] is True
        assert brain.store.usage("run-B").objects == 1
        await agent.observe("c-b", result=capture_result(8), parts=[image_ref("for-b", 8)])
        observation = await second
        assert observation.status == "success"
        (part,) = observation.parts
        assert brain.store.lookup(part["attachment_id"]).run_id == "run-B"
    finally:
        await brain.close()


async def test_a_binary_frame_for_a_call_observed_between_header_and_payload_is_refused() -> None:
    """Gate finding F3: the owning call may end between the two frames by
    its own ``observation`` too — the header is invalidated with the call
    and the payload refused, whatever ended the call."""

    brain = Brain()
    try:
        await brain.activate()
        agent = brain.connect()
        await agent.pair_up()
        task = asyncio.create_task(brain.executor.invoke(capture_call("c-a", run_id="run-A", deadline=brain.clock() + 30)))
        await agent.call_frame()
        await agent.send({
            "type": FRAME_ATTACHMENT, "id": agent.session_id, "attachment_id": "orphan",
            "content_type": "image/png", "size": 8, "call_id": "c-a",
        })
        await agent.observe("c-a", status="error", error={"code": "capture_failed", "message": "gave up", "retryable": True})
        assert (await task).status == "error"
        await agent.send_bytes(b"\x00" * 8)
        ack = await agent.frame()
        assert ack["type"] == FRAME_ATTACHMENT_ACK and ack["accepted"] is False
        assert ack["code"] == ATTACHMENT_ACK_UNEXPECTED_BINARY and ack["attachment_id"] == "orphan"
        assert brain.store.object_count == 0
        assert brain.store.release("run-A").objects == 0
    finally:
        await brain.close()


async def test_a_duplicate_attachment_id_from_another_call_is_refused_and_the_owner_keeps_its_reference() -> None:
    """Gate finding F6 (X1): call A uploads ``same-id``; while A is live,
    call B uploads ``same-id`` too. The duplicate is refused
    ``unexpected_binary`` on the header alone — the module used to accept
    it and silently replace A's translation, so A's own acknowledged
    reference stopped resolving. Now A's entry and bytes are untouched, A's
    observation succeeds with A's bytes, and B — which stored and leased
    nothing — fails cleanly when it names the id it does not own."""

    brain = Brain()
    try:
        await brain.activate()
        agent = brain.connect()
        await agent.pair_up()
        module = brain.module
        assert module is not None
        deadline = brain.clock() + 30
        first = asyncio.create_task(brain.executor.invoke(capture_call("c-a", run_id="run-A", deadline=deadline)))
        await agent.call_frame()
        second = asyncio.create_task(brain.executor.invoke(capture_call("c-b", run_id="run-B", deadline=deadline)))
        await agent.call_frame()

        ack_a = await agent.transfer("same-id", b"\x0a" * 8, call_id="c-a")
        assert ack_a["accepted"] is True
        session = module._session
        assert session is not None
        held = session.acknowledged["same-id"]
        ref_a = held.ref

        # B's header is refused before any translation is touched; the
        # payload that follows is unannounced and refused too.
        await agent.send({
            "type": FRAME_ATTACHMENT, "id": agent.session_id, "attachment_id": "same-id",
            "content_type": "image/png", "size": 8, "call_id": "c-b",
        })
        ack_b = await agent.frame()
        assert ack_b["type"] == FRAME_ATTACHMENT_ACK and ack_b["accepted"] is False
        assert ack_b["code"] == ATTACHMENT_ACK_UNEXPECTED_BINARY and ack_b["attachment_id"] == "same-id"
        await agent.send_bytes(b"\x0b" * 8)
        ack = await agent.frame()
        assert ack["accepted"] is False and ack["code"] == ATTACHMENT_ACK_UNEXPECTED_BINARY
        assert session.acknowledged["same-id"] is held
        assert session.acknowledged["same-id"].ref is ref_a
        assert session.acknowledged["same-id"].owner.call.call_id == "c-a"
        assert module.acknowledged_attachments == 1
        assert brain.store.object_count == 1
        assert brain.store.usage("run-B").objects == 0
        assert brain.store.get(ref_a) == b"\x0a" * 8
        assert module.saturated_uploads == 0
        assert not agent.ws.closed

        # A's acknowledged reference still resolves, to A's own bytes.
        await agent.observe("c-a", result=capture_result(8), parts=[image_ref("same-id", 8)])
        observation = await first
        assert observation.status == "success", observation.error
        (part,) = observation.parts
        stored = brain.store.lookup(part["attachment_id"])
        assert stored.run_id == "run-A"
        assert brain.store.get(stored) == b"\x0a" * 8

        # B naming the id it was refused fails cleanly: nothing of B's was
        # stored, nothing is leased to run-B.
        await agent.observe("c-b", result=capture_result(8), parts=[image_ref("same-id", 8)])
        observation = await second
        assert observation.status == "error"
        assert observation.error["code"] == ERROR_ATTACHMENT_REFUSED
        assert not observation.parts
        assert brain.store.usage("run-B").objects == 0
        assert module.acknowledged_attachments == 0
        assert module.in_flight == ()
    finally:
        await brain.close()


async def test_a_duplicate_attachment_id_from_the_same_call_is_refused_and_keeps_the_first_bytes() -> None:
    """Gate finding F6: the same call uploading twice under one id is a
    duplicate too — the second transfer is refused ``unexpected_binary``,
    stores nothing, and the first upload's bytes and reference are what
    the observation resolves. An id freed by its call's terminal outcome
    may be used again by a later call."""

    brain = Brain()
    try:
        await brain.activate()
        agent = brain.connect()
        await agent.pair_up()
        module = brain.module
        assert module is not None
        deadline = brain.clock() + 30
        task = asyncio.create_task(brain.executor.invoke(capture_call("c-a", run_id="run-A", deadline=deadline)))
        await agent.call_frame()

        ack = await agent.transfer("shot", b"\x01" * 8, call_id="c-a")
        assert ack["accepted"] is True
        session = module._session
        assert session is not None
        ref = session.acknowledged["shot"].ref

        await agent.send({
            "type": FRAME_ATTACHMENT, "id": agent.session_id, "attachment_id": "shot",
            "content_type": "image/png", "size": 8, "call_id": "c-a",
        })
        ack = await agent.frame()
        assert ack["type"] == FRAME_ATTACHMENT_ACK and ack["accepted"] is False
        assert ack["code"] == ATTACHMENT_ACK_UNEXPECTED_BINARY and ack["attachment_id"] == "shot"
        await agent.send_bytes(b"\x02" * 8)
        ack = await agent.frame()
        assert ack["accepted"] is False and ack["code"] == ATTACHMENT_ACK_UNEXPECTED_BINARY
        assert session.acknowledged["shot"].ref is ref
        assert brain.store.get(ref) == b"\x01" * 8
        assert brain.store.object_count == 1
        assert brain.store.usage("run-A").objects == 1

        # A header naming no call falls to the same live call and is refused
        # the same way.
        await agent.send({
            "type": FRAME_ATTACHMENT, "id": agent.session_id, "attachment_id": "shot",
            "content_type": "image/png", "size": 8,
        })
        ack = await agent.frame()
        assert ack["accepted"] is False and ack["code"] == ATTACHMENT_ACK_UNEXPECTED_BINARY
        await agent.send_bytes(b"\x03" * 8)
        ack = await agent.frame()
        assert ack["accepted"] is False and ack["code"] == ATTACHMENT_ACK_UNEXPECTED_BINARY
        assert brain.store.object_count == 1

        await agent.observe("c-a", result=capture_result(8), parts=[image_ref("shot", 8)])
        observation = await task
        assert observation.status == "success", observation.error
        (part,) = observation.parts
        assert brain.store.get(brain.store.lookup(part["attachment_id"])) == b"\x01" * 8
        assert module.acknowledged_attachments == 0

        # The id is free again once its call is terminal.
        task = asyncio.create_task(brain.executor.invoke(capture_call("c-b", run_id="run-B", deadline=deadline)))
        await agent.call_frame()
        ack = await agent.transfer("shot", b"\x04" * 8, call_id="c-b")
        assert ack["accepted"] is True
        await agent.observe("c-b", result=capture_result(8), parts=[image_ref("shot", 8)])
        observation = await task
        assert observation.status == "success", observation.error
        (part,) = observation.parts
        assert brain.store.get(brain.store.lookup(part["attachment_id"])) == b"\x04" * 8
    finally:
        await brain.close()


async def test_repeated_duplicate_uploads_leave_the_translation_table_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gate finding F6: identity protection reuses the bounded translation
    table, not a session history. With the bound lowered to 2, hundreds of
    duplicate attempts — against one id, from the owner and from another
    live call — add no entry, store no object and count as no saturation;
    the third distinct id still meets the counted ``store_full`` policy,
    and every duplicate attempt after that leaves the table at its bound."""

    monkeypatch.setattr(proxy_module, "MAX_ACKNOWLEDGED_ATTACHMENTS", 2)
    brain = Brain()
    try:
        await brain.activate()
        agent = brain.connect()
        await agent.pair_up()
        module = brain.module
        assert module is not None
        deadline = brain.clock() + 30
        tasks = []
        for index in range(3):
            tasks.append(asyncio.create_task(
                brain.executor.invoke(capture_call(f"c-{index}", run_id=f"run-{index}", deadline=deadline))
            ))
            await agent.call_frame()
        assert (await agent.transfer("a-0", b"\x00" * 8, call_id="c-0"))["accepted"] is True
        assert module.acknowledged_attachments == 1

        for attempt in range(300):
            call_id = ("c-0", "c-1", "c-2")[attempt % 3]
            await agent.send({
                "type": FRAME_ATTACHMENT, "id": agent.session_id, "attachment_id": "a-0",
                "content_type": "image/png", "size": 8, "call_id": call_id,
            })
            ack = await agent.frame()
            assert ack["accepted"] is False and ack["code"] == ATTACHMENT_ACK_UNEXPECTED_BINARY
            await agent.send_bytes(bytes([attempt % 256]) * 8)
            ack = await agent.frame()
            assert ack["accepted"] is False and ack["code"] == ATTACHMENT_ACK_UNEXPECTED_BINARY
        assert module.acknowledged_attachments == 1
        assert brain.store.object_count == 1
        assert module.saturated_uploads == 0
        assert not agent.ws.closed

        # The bound itself is unchanged: a second distinct id fits, a third
        # is refused store_full and counted, duplicates of either add nothing.
        assert (await agent.transfer("a-1", b"\x01" * 8, call_id="c-1"))["accepted"] is True
        ack = await agent.transfer("a-2", b"\x02" * 8, call_id="c-2")
        assert ack["accepted"] is False and ack["code"] == ATTACHMENT_ACK_STORE_FULL
        assert module.saturated_uploads == 1
        for attachment_id in ("a-0", "a-1") * 50:
            await agent.send({
                "type": FRAME_ATTACHMENT, "id": agent.session_id, "attachment_id": attachment_id,
                "content_type": "image/png", "size": 8, "call_id": "c-2",
            })
            ack = await agent.frame()
            assert ack["accepted"] is False and ack["code"] == ATTACHMENT_ACK_UNEXPECTED_BINARY
            await agent.send_bytes(b"\xff" * 8)
            ack = await agent.frame()
            assert ack["accepted"] is False and ack["code"] == ATTACHMENT_ACK_UNEXPECTED_BINARY
        assert module.acknowledged_attachments == 2
        assert brain.store.object_count == 2
        assert module.saturated_uploads == 1

        for index, attachment_id in ((0, "a-0"), (1, "a-1")):
            await agent.observe(f"c-{index}", result=capture_result(8), parts=[image_ref(attachment_id, 8)])
            observation = await tasks[index]
            assert observation.status == "success", observation.error
            (part,) = observation.parts
            assert brain.store.get(brain.store.lookup(part["attachment_id"])) == bytes([index]) * 8
        assert (await agent.transfer("a-2", b"\x02" * 8, call_id="c-2"))["accepted"] is True
        await agent.observe("c-2", result=capture_result(8), parts=[image_ref("a-2", 8)])
        assert (await tasks[2]).status == "success"
        assert module.acknowledged_attachments == 0
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


# =========================================================================== #
# P20 — the agent side: ``modules/agent_link`` over the in-memory pair
# =========================================================================== #
#
# Every test below activates ``modules/agent_link`` on its own runtime
# context — the PC agent's process: its own clock, its own random source, its
# own attachment store and its own default-deny executor with a rule granting
# ``screen.capture`` to principal ``brain`` only — and lets the dial loop
# reach the scripted brain end of a ``MemoryWebSocketPair`` through the
# ``_connector`` seam. The AC33 tests put the real ``modules/proxy`` on the
# other end, the two modules and the two runtimes in one process, and the
# closing vertical test drives the whole AC1 scenario through the real loader
# on both sides. No positive-duration sleep anywhere.

import shutil
import subprocess

from core.actions import ERROR_CANCELLED, ERROR_NOT_AUTHORIZED, AuthorizationRule
from core.contracts import (
    PROXY_ERROR_DUPLICATE_CALL_UNKNOWN,
    TRACE_ACTION_COMPLETED,
    TRACE_ACTION_STARTED,
    TRACE_MODULE_READY,
    WILDCARD,
    ActionObservation,
)
from core.context import ChatContext
from core.triggers import TriggerRegistry
from conftest import FakeCaptureSource, ScriptedModel
from modules import agent_link
from modules import capture as capture_module
from modules.agent_link import (
    AgentLinkError,
    AgentLinkModule,
    ERROR_ATTACHMENT_REFUSED,
    STATE_PAIRED,
)
from modules.brain import MODULE_NAME as BRAIN_MODULE
from test_agentic_loop import (
    AC1_SCRIPT,
    image_parts,
    CAPTURE_SOURCE,
    CHAT_LIMITS,
    CHAT_READ,
    CHAT_WRITE,
    COMPANION,
    CONTEXT_LINES,
    FAKE_CHANNEL,
    FAKE_MODULE,
    FAKE_PLATFORM,
    FINAL_TEXT,
    FIXTURE_MODULES,
    IMAGE_HEIGHT,
    IMAGE_WIDTH,
    PNG,
    SHIPPED_MODULES,
    USERS_READ,
    USERS_SETTINGS,
    VerticalHarness,
    activate_vertical,
    call_suffixes,
    grant,
    trace_set,
)
from test_agentic_loop import STORE_LIMITS as VERTICAL_STORE_LIMITS
from test_brain import merge_settings


AGENT_MODULE = agent_link.MODULE_NAME
BRAIN_URL = "ws://127.0.0.1:8765/agent"
AGENT_SETTINGS: dict[str, Any] = {
    "brain_url": BRAIN_URL,
    "pairing_token": TOKEN,
    "agent_id": AGENT_ID,
}
#: The brain's limits as ``welcome`` announces them to the agent.
BRAIN_LIMITS: dict[str, Any] = {
    "max_frame_bytes": 1_048_576,
    "max_attachment_bytes": STORE_LIMITS["max_object_bytes"],
    "heartbeat_seconds": 15.0,
    "heartbeat_timeout_seconds": 10.0,
}
#: A brain wall clock and an agent wall clock 3 h ahead of it (AC34).
BRAIN_WALL_CLOCK = WALL_CLOCK
AGENT_WALL_CLOCK = WALL_CLOCK + 3 * 3600


class ConstantRandom:
    """The injected random source of AC37: every draw is *value*."""

    def __init__(self, value: float = 0.5) -> None:
        self.value = value
        self.draws = 0

    def random(self) -> float:
        self.draws += 1
        return self.value


def agent_policy(*principals: str) -> AuthorizationPolicy:
    """The agent profile's one rule: ``screen.capture`` to principal ``brain`` (P21)."""

    return AuthorizationPolicy(
        [
            AuthorizationRule(
                rule_id="agent-capture",
                action_name=SCREEN_CAPTURE,
                destination=Destination(WILDCARD, WILDCARD, "capture"),
                principals=principals or (PRINCIPAL,),
                granted_permissions=("screen.capture",),
            )
        ]
    )


class RecordingCapture:
    """The ``screen.capture`` provider behind the agent's executor.

    Stores ``image`` in the agent's store under the call's run and answers
    with the matching ``image_ref`` part; ``hold`` (a future the test
    resolves) keeps an invocation pending; ``calls`` records every call.
    """

    name = "capture"

    def __init__(self, host: "AgentHost", image: bytes | None) -> None:
        self.host = host
        self.image = image
        self.calls: list[ActionCall] = []
        self.hold: asyncio.Future[Any] | None = None
        self.entered = asyncio.Event()

    async def invoke(self, invocation: Any) -> ActionObservation:
        call = invocation.call
        self.calls.append(call)
        self.entered.set()
        if self.hold is not None:
            await self.hold
        provenance = {"provider": self.name, "module": "capture"}
        if self.image is None:
            return ActionObservation(
                status="success", provenance=provenance, result=capture_result(4)
            )
        ref = self.host.store.put(call.run_id, self.image, content_type="image/png")
        return ActionObservation(
            status="success",
            provenance=provenance,
            result=capture_result(len(self.image)),
            parts=(image_ref(ref.attachment_id, len(self.image)),),
        )


class AgentHost:
    """One activated agent link on its own runtime context, plus its dials.

    ``dials`` scripts what the ``_connector`` seam answers, in order: a
    :class:`MemoryWebSocketPair` (the client end is handed to the module)
    or an exception (the dial fails). ``delays`` records every sleep the
    module asked for — backoff and heartbeat alike — and ``backoffs`` the
    ones asked while unpaired, which are the reconnection delays (a
    heartbeat sleeps only while paired). Sleeps park on the manual clock;
    with ``immediate_sleeper`` a backoff returns at once so a failure
    sequence runs without moving the clock.
    """

    def __init__(
        self,
        *,
        actions: tuple[str, ...] = (SCREEN_CAPTURE,),
        clock: ManualClock | None = None,
        rng: Any = None,
        policy: AuthorizationPolicy | None = None,
        image: bytes | None = None,
        immediate_sleeper: bool = False,
        register_provider: bool = True,
        store_limits: dict[str, Any] | None = None,
        **extra_settings: Any,
    ) -> None:
        self.clock = clock if clock is not None else ManualClock()
        self.rng = rng if rng is not None else ConstantRandom(0.5)
        self.store = AttachmentStore(clock=self.clock, **(store_limits or STORE_LIMITS))
        self.context: RuntimeContext = runtime_context(
            clock=self.clock,
            attachments=self.store,
            authorization=policy if policy is not None else agent_policy(),
            rng=self.rng,
        )
        self.provider = RecordingCapture(self, image)
        if register_provider:
            self.context.actions.register(
                screen_capture_spec(), self.provider, module="capture", provider_name="capture"
            )
            self.context.actions.mark_ready("capture")
            self.context.actions.declare(fake_write_spec(), module="fakeplatform")
        self.dials: asyncio.Queue[Any] = asyncio.Queue()
        self.dial_urls: list[str] = []
        self.delays: list[float] = []
        self.backoffs: list[float] = []
        self.diagnostics: list[str] = []
        self._immediate = immediate_sleeper
        self.settings: dict[str, Any] = {
            **AGENT_SETTINGS,
            "actions": list(actions),
            "_connector": self._connector,
            "_sleeper": self._sleeper,
            "diagnostic_reporter": self.diagnostics.append,
            **extra_settings,
        }
        self.module: AgentLinkModule | None = None

    async def _connector(self, url: str, **kwargs: Any) -> Any:
        self.dial_urls.append(url)
        outcome = await self.dials.get()
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome.client

    async def _sleeper(self, delay: float) -> None:
        self.delays.append(delay)
        paired = self.module is not None and self.module.paired
        if not paired:
            self.backoffs.append(delay)
        if self._immediate and not paired:
            await asyncio.sleep(0)
        else:
            await self.clock.sleep(delay)

    async def activate(self, *, prepare: bool = True, start: bool = True) -> AgentLinkModule:
        self.module = await agent_link.activate(
            self.context.for_module(AGENT_MODULE), self.settings, {}
        )
        if prepare:
            await self.module.prepare()
        if start:
            await self.module.start_inputs()
        return self.module

    def fail_dial(self, count: int = 1) -> None:
        for _ in range(count):
            self.dials.put_nowait(ConnectionRefusedError("dial refused"))

    def connect(self) -> "BrainEnd":
        """Queue one pair for the next dial and return its scripted brain end."""

        pair = MemoryWebSocketPair()
        self.dials.put_nowait(pair)
        return BrainEnd(self, pair)

    @property
    def bus(self) -> Any:
        return self.context.bus

    @property
    def executor(self) -> Any:
        return self.context.executor

    async def close(self) -> None:
        if self.module is not None:
            await self.module.close()


class BrainEnd:
    """The scripted brain end of one connection the agent dialed."""

    def __init__(self, host: AgentHost, pair: MemoryWebSocketPair) -> None:
        self.host = host
        self.pair = pair
        self.ws = pair.server
        self.session_id: str | None = None
        self.hello: dict[str, Any] | None = None

    async def send(self, frame: dict[str, Any], *, v: int = 1) -> None:
        await self.ws.send_str(json.dumps({"v": v, **frame}))

    async def send_raw(self, text: str) -> None:
        await self.ws.send_str(text)

    async def frame(self) -> dict[str, Any]:
        message = await self.ws.receive()
        assert message.type == WSMsgType.TEXT, message
        return json.loads(message.data)

    async def binary(self) -> bytes:
        message = await self.ws.receive()
        assert message.type == WSMsgType.BINARY, message
        return message.data

    async def close_frame(self) -> int:
        message = await self.ws.receive()
        assert message.type == WSMsgType.CLOSE, message
        return int(message.data)

    async def expect_hello(self) -> dict[str, Any]:
        frame = await self.frame()
        assert frame["type"] == FRAME_HELLO, frame
        self.hello = frame
        return frame

    async def expect_error(self, code: str) -> dict[str, Any]:
        frame = await self.frame()
        assert frame["type"] == FRAME_ERROR, frame
        assert frame["code"] == code, frame
        assert TOKEN not in json.dumps(frame)
        return frame

    async def welcome(
        self,
        *,
        session_id: str = "session-1",
        actions: list[str] | None = None,
        limits: dict[str, Any] | None = None,
        nonce: Any = None,
    ) -> dict[str, Any]:
        """Answer the ``hello`` and return the ``agent.status`` event that follows."""

        assert self.hello is not None
        self.session_id = session_id
        await self.send(
            {
                "type": FRAME_WELCOME,
                "id": self.hello["id"] if nonce is None else nonce,
                "session_id": session_id,
                "actions": actions if actions is not None else [SCREEN_CAPTURE],
                "limits": limits if limits is not None else dict(BRAIN_LIMITS),
            }
        )
        event = await self.frame()
        assert event["type"] == FRAME_EVENT, event
        # Let the agent's heartbeat task take its first turn (and park on
        # the injected clock) before a test moves that clock.
        await settle()
        return event

    async def pair_up(self, **kwargs: Any) -> dict[str, Any]:
        await self.expect_hello()
        return await self.welcome(**kwargs)

    def call_frame(
        self,
        call_id: str,
        seq: Any,
        *,
        remaining_ms: Any = 5000,
        action: str = SCREEN_CAPTURE,
        arguments: dict[str, Any] | None = None,
        run_id: str = RUN_ID,
        principal: str = PRINCIPAL,
        deadline_utc: str = "2023-11-14T22:13:25.000Z",
        max_attachment_bytes: int | None = STORE_LIMITS["max_object_bytes"],
    ) -> dict[str, Any]:
        frame: dict[str, Any] = {
            "type": FRAME_CALL,
            "id": call_id,
            "action_name": action,
            "action_version": 1,
            "arguments": arguments if arguments is not None else {},
            "conversation_id": "conversation-1",
            "run_id": run_id,
            "call_id": call_id,
            "source_event_id": "source-1",
            "destination": {"platform": "fake", "channel_id": "channel-9", "scope": "capture"},
            "principal": principal,
            "message_id": "source-1",
            "contract_version": 1,
            "seq": seq,
            "deadline_utc": deadline_utc,
            "remaining_ms": remaining_ms,
        }
        if max_attachment_bytes is not None:
            frame["max_attachment_bytes"] = max_attachment_bytes
        return frame

    async def call(self, call_id: str, seq: Any, **kwargs: Any) -> None:
        await self.send(self.call_frame(call_id, seq, **kwargs))

    async def observation(self, call_id: str | None = None) -> dict[str, Any]:
        frame = await self.frame()
        assert frame["type"] == FRAME_OBSERVATION, frame
        if call_id is not None:
            assert frame["id"] == call_id, frame
        return frame

    async def ack(self, attachment_id: str, *, accepted: bool = True, code: str | None = None) -> None:
        frame: dict[str, Any] = {
            "type": FRAME_ATTACHMENT_ACK,
            "id": self.session_id,
            "attachment_id": attachment_id,
            "accepted": accepted,
        }
        if code is not None:
            frame["code"] = code
        await self.send(frame)

    async def receive_transfer(self) -> tuple[dict[str, Any], bytes]:
        header = await self.frame()
        assert header["type"] == FRAME_ATTACHMENT, header
        payload = await self.binary()
        return header, payload

    def received_types(self) -> list[str]:
        """Every frame type the agent wrote on this connection, in order."""

        types: list[str] = []
        for message in self.pair.client.sent:
            if message.type == WSMsgType.TEXT:
                types.append(json.loads(message.data)["type"])
            elif message.type == WSMsgType.BINARY:
                types.append("<binary>")
        return types

    def received_frames(self, frame_type: str) -> list[dict[str, Any]]:
        return [
            json.loads(message.data)
            for message in self.pair.client.sent
            if message.type == WSMsgType.TEXT and json.loads(message.data)["type"] == frame_type
        ]


async def paired_host(**kwargs: Any) -> tuple[AgentHost, BrainEnd]:
    host = AgentHost(**kwargs)
    await host.activate()
    brain = host.connect()
    await brain.pair_up()
    return host, brain


# --------------------------------------------------------------------------- #
# Manifest, settings, declarations
# --------------------------------------------------------------------------- #


def test_agent_link_manifest_declares_the_input_role_no_event_and_no_action() -> None:
    manifest = yaml.safe_load(agent_link.MANIFEST_PATH.read_text(encoding="utf-8"))
    assert manifest["name"] == AGENT_MODULE
    assert manifest["manifest_version"] == 2
    assert manifest["runtime_api"] == RUNTIME_API
    assert manifest["produces"] == [] and manifest["consumes"] == []
    assert manifest["middleware"] is False
    assert manifest["lifecycle"] == {"roles": [ROLE_INPUT]}
    assert manifest["credentials"] == ["pairing_token"]
    assert manifest["settings_validator"] == "validate_settings"
    assert "actions" not in manifest
    schema = manifest["settings_schema"]
    assert set(schema["required"]) == {"brain_url", "pairing_token", "agent_id", "actions"}
    assert set(schema["properties"]) == {
        "brain_url", "pairing_token", "agent_id", "actions", "reconnect", "heartbeat_seconds",
        "heartbeat_timeout_seconds", "call_table", "max_frame_bytes", "action_seconds", "limits",
    }
    assert set(schema["properties"]["reconnect"]["properties"]) == {
        "initial_seconds", "multiplier", "max_seconds",
    }
    assert set(schema["properties"]["call_table"]["properties"]) == {"max_entries", "ttl_seconds"}
    assert "twitch" not in agent_link.MANIFEST_PATH.read_text(encoding="utf-8").lower()


def test_agent_link_validate_settings_accepts_loopback_ws_and_wss_anywhere() -> None:
    for url in ("ws://127.0.0.1:8765", "ws://localhost:8765/agent", "ws://[::1]:8765",
                "wss://brain.example.net:8765", "wss://10.0.0.7/agent"):
        settings = {**AGENT_SETTINGS, "brain_url": url, "actions": [SCREEN_CAPTURE]}
        assert agent_link.validate_settings(settings) == [], url
    full = {
        **AGENT_SETTINGS,
        "actions": [SCREEN_CAPTURE],
        "reconnect": {"initial_seconds": 1, "multiplier": 2, "max_seconds": 30},
        "heartbeat_seconds": 15,
        "heartbeat_timeout_seconds": 10,
        "call_table": {"max_entries": 1024, "ttl_seconds": 300},
        "max_frame_bytes": 1_048_576,
        "action_seconds": 10,
        "limits": {"attachments": dict(STORE_LIMITS)},
    }
    assert agent_link.validate_settings(full) == []
    parsed = agent_link._Settings.from_mapping({**AGENT_SETTINGS, "actions": [SCREEN_CAPTURE]})
    assert (parsed.reconnect.initial_seconds, parsed.reconnect.multiplier, parsed.reconnect.max_seconds) == (1.0, 2.0, 30.0)
    assert (parsed.heartbeat_seconds, parsed.heartbeat_timeout_seconds) == (15.0, 10.0)
    assert (parsed.call_table.max_entries, parsed.call_table.ttl_seconds) == (1024, 300.0)
    assert parsed.max_frame_bytes == PROXY_DEFAULT_MAX_FRAME_BYTES
    assert parsed.action_seconds == 10.0


@pytest.mark.parametrize(
    "url",
    ["ws://10.0.0.7:8765", "ws://brain.example.net/agent", "http://127.0.0.1:8765",
     "wss:///agent", "not a url", ""],
)
def test_agent_link_validate_settings_refuses_non_loopback_ws_and_unknown_schemes_without_echo(
    url: str,
) -> None:
    """R6: a non-loopback ``brain_url`` must use ``wss``; any other scheme is
    refused; the diagnostic names module and field and never the value."""

    diagnostics = agent_link.validate_settings(
        {**AGENT_SETTINGS, "brain_url": url, "actions": [SCREEN_CAPTURE]}
    )
    assert len(diagnostics) == 1, diagnostics
    assert diagnostics[0].startswith(f"module {AGENT_MODULE!r}: field 'brain_url'")
    if url.strip():
        assert url not in diagnostics[0] and "example" not in diagnostics[0]


def test_agent_link_validate_settings_refuses_bad_entries_and_non_finite_bounds() -> None:
    diagnostics = agent_link.validate_settings(
        {
            "brain_url": BRAIN_URL,
            "actions": ["", SCREEN_CAPTURE, SCREEN_CAPTURE, 7],
            "reconnect": {"initial_seconds": float("inf"), "multiplier": 0.5, "max_seconds": 0, "other": 1},
            "heartbeat_seconds": float("nan"),
            "heartbeat_timeout_seconds": -1,
            "call_table": {"max_entries": 0, "ttl_seconds": float("inf"), "extra": 1},
            "max_frame_bytes": True,
            "action_seconds": "10",
            "unknown": 1,
            "_connector": "not callable",
        }
    )
    fields = {d.split("field ")[1].split(":")[0] for d in diagnostics}
    assert fields == {
        "'pairing_token'", "'agent_id'", "'actions[0]'", "'actions[2]'", "'actions[3]'",
        "'reconnect.initial_seconds'", "'reconnect.multiplier'", "'reconnect.max_seconds'",
        "'reconnect.other'", "'heartbeat_seconds'", "'heartbeat_timeout_seconds'",
        "'call_table.max_entries'", "'call_table.ttl_seconds'", "'call_table.extra'",
        "'max_frame_bytes'", "'action_seconds'", "'unknown'", "'_connector'",
    }
    assert all(d.startswith(f"module {AGENT_MODULE!r}: field ") for d in diagnostics)
    assert agent_link.validate_settings("nope") == [
        f"module {AGENT_MODULE!r}: field 'settings': must be a mapping"
    ]


def test_agent_link_declaration_round_trips_through_the_brains_comparison_delivery_included() -> None:
    """The agent's ``hello`` declaration rebuilds, on the brain, into a spec
    equal to the catalog's — ``delivery`` included — so the pairing accepts it."""

    for spec in (screen_capture_spec(), fake_write_spec()):
        declaration = agent_link.spec_declaration(spec)
        assert json.loads(json.dumps(declaration)) == declaration
        assert spec_from_declaration(declaration) == spec
    assert "delivery" in agent_link.spec_declaration(fake_write_spec())
    assert "delivery" not in agent_link.spec_declaration(screen_capture_spec())


async def test_agent_link_hello_carries_the_nonce_the_identity_the_declarations_and_the_bound() -> None:
    """``hello`` includes ``delivery`` on the declared write action and no
    ``delivery`` on the read; the nonce comes from the injected random source."""

    host = AgentHost(actions=(SCREEN_CAPTURE, FAKE_WRITE), max_frame_bytes=4096)
    try:
        module = await host.activate()
        assert list(module.declared_actions) == [SCREEN_CAPTURE, FAKE_WRITE]
        brain = host.connect()
        hello = await brain.expect_hello()
        assert hello["v"] == 1 and hello["type"] == FRAME_HELLO
        assert isinstance(hello["id"], str) and hello["id"]
        assert hello["agent_id"] == AGENT_ID and hello["token"] == TOKEN
        assert hello["max_frame_bytes"] == 4096
        declared = {entry["name"]: entry for entry in hello["actions"]}
        assert list(declared) == [SCREEN_CAPTURE, FAKE_WRITE]
        assert declared[FAKE_WRITE]["delivery"] == {"text_argument": "text"}
        assert "delivery" not in declared[SCREEN_CAPTURE]
        assert spec_from_declaration(declared[SCREEN_CAPTURE]) == screen_capture_spec()
        assert spec_from_declaration(declared[FAKE_WRITE]) == fake_write_spec()
        assert host.dial_urls == [BRAIN_URL]
        assert host.rng.draws >= 4
    finally:
        await host.close()


async def test_agent_link_prepare_refuses_an_action_no_local_manifest_declares() -> None:
    host = AgentHost(actions=(SCREEN_CAPTURE, "camera.pan"))
    try:
        with pytest.raises(AgentLinkError) as refused:
            await host.activate()
        assert "actions[1]" in str(refused.value) and "camera.pan" in str(refused.value)
        degraded = events_of(host.bus, TRACE_MODULE_DEGRADED)
        assert degraded and "actions[1]" in degraded[-1]["payload"]["reason"]
        assert host.dial_urls == []
        assert not host.module.running
    finally:
        await host.close()


async def test_agent_link_activate_refuses_bad_settings_and_an_incomplete_context() -> None:
    context = runtime_context()
    with pytest.raises(AgentLinkError):
        await agent_link.activate(context.for_module(AGENT_MODULE), {"brain_url": BRAIN_URL}, {})
    with pytest.raises(AgentLinkError):
        await agent_link.activate(context.for_module(AGENT_MODULE), "settings", {})
    incomplete = SimpleNamespace(actions=None, executor=None, tasks=None, clock=None, rng=None)
    with pytest.raises(AgentLinkError):
        await agent_link.activate(incomplete, {**AGENT_SETTINGS, "actions": [SCREEN_CAPTURE]}, {})
    assert inspect.iscoroutinefunction(agent_link.activate)
    assert (ROOT / "modules" / AGENT_MODULE / "__init__.py").is_file()


async def test_agent_link_start_inputs_requires_prepare_and_stop_ends_the_loop() -> None:
    host = AgentHost()
    try:
        module = await host.activate(prepare=False, start=False)
        with pytest.raises(AgentLinkError):
            await module.start_inputs()
        await module.prepare()
        await module.start_inputs()
        assert module.running and AGENT_MODULE in host.context.tasks.owners()
        brain = host.connect()
        await brain.pair_up()
        assert module.paired
        await module.stop_inputs()
        assert await brain.close_frame() == agent_link.CLOSE_GOING_AWAY
        assert not module.running and not module.paired
        assert AGENT_MODULE not in host.context.tasks.owners()
        # Nothing reconnects after stop: no backoff delay, no further dial.
        assert host.backoffs == [] and host.dial_urls == [BRAIN_URL]
        assert events_of(host.bus, TRACE_MODULE_DEGRADED) == []
    finally:
        await host.close()


# --------------------------------------------------------------------------- #
# Pairing
# --------------------------------------------------------------------------- #


async def test_agent_link_welcome_sends_agent_status_paired_reports_ready_and_resets_state() -> None:
    host = AgentHost()
    try:
        module = await host.activate()
        brain = host.connect()
        hello = await brain.expect_hello()
        assert not module.paired
        event = await brain.welcome(session_id="s-42")
        assert event == {
            "v": 1, "type": FRAME_EVENT, "id": "s-42",
            "event_type": AGENT_STATUS_EVENT, "payload": {"state": STATE_PAIRED},
        }
        assert module.paired and module.session_id == "s-42"
        assert module.accepted_actions == frozenset({SCREEN_CAPTURE})
        assert module.last_seq == 0 and module.retained_calls == ()
        assert module.pairings == 1 and module.consecutive_failures == 0
        ready = events_of(host.bus, TRACE_MODULE_READY)
        assert ready and ready[-1]["payload"]["module"] == AGENT_MODULE
        assert ready[-1]["payload"]["capabilities"] == [SCREEN_CAPTURE]
        assert TOKEN not in json.dumps(host.bus.list_events())
        # A welcome answering another hello is malformed and pairs nothing.
        assert hello["id"] != "other"
    finally:
        await host.close()


async def test_agent_link_a_refused_pairing_is_diagnosed_by_code_only_and_redialed() -> None:
    host = AgentHost(immediate_sleeper=True)
    try:
        module = await host.activate()
        brain = host.connect()
        await brain.expect_hello()
        await brain.send({"type": FRAME_ERROR, "id": brain.hello["id"], "code": PROXY_ERROR_AUTH_FAILED,
                          "message": "pairing token refused", "retryable": False})
        await brain.ws.close(code=PROXY_CLOSE_AUTH_FAILED)
        again = host.connect()
        await again.expect_hello()
        assert not module.paired and module.errors_received == 1
        assert any(PROXY_ERROR_AUTH_FAILED in line for line in host.diagnostics)
        assert all(TOKEN not in line for line in host.diagnostics)
        assert host.backoffs == [0.5]
    finally:
        await host.close()


async def test_agent_link_frames_before_welcome_and_malformed_frames_are_refused_like_the_brain() -> None:
    host = AgentHost(max_frame_bytes=512, immediate_sleeper=True)
    try:
        module = await host.activate()
        brain = host.connect()
        await brain.expect_hello()
        await brain.send({"type": FRAME_PING, "id": "x"})
        error = await brain.expect_error(PROXY_ERROR_UNKNOWN_SESSION)
        assert error["id"] is None
        await brain.send_raw("not json")
        await brain.expect_error(PROXY_ERROR_INVALID_FRAME)
        await brain.send({"type": "teleport", "id": "x"})
        await brain.expect_error(PROXY_ERROR_UNKNOWN_FRAME)
        # A welcome answering another hello pairs nothing.
        await brain.send({"type": FRAME_WELCOME, "id": "other", "session_id": "s", "actions": [], "limits": {}})
        await brain.expect_error(PROXY_ERROR_INVALID_FRAME)
        assert not module.paired and not brain.ws.closed

        await brain.welcome()
        assert module.paired
        for frame_type in (FRAME_HELLO, FRAME_OBSERVATION, FRAME_ATTACHMENT, FRAME_EVENT, FRAME_WELCOME):
            await brain.send({"type": frame_type, "id": brain.session_id})
            await brain.expect_error(PROXY_ERROR_INVALID_FRAME)
        await brain.send({"type": FRAME_PING, "id": "other"})
        await brain.expect_error(PROXY_ERROR_UNKNOWN_SESSION)
        await brain.send({"type": FRAME_PING, "id": brain.session_id})
        assert await brain.frame() == {"v": 1, "type": FRAME_PONG, "id": brain.session_id}
        assert module.paired

        await brain.send({"type": FRAME_PING, "id": brain.session_id}, v=2)
        await brain.expect_error(PROXY_ERROR_UNSUPPORTED_PROTOCOL_VERSION)
        assert await brain.close_frame() == PROXY_CLOSE_BAD_REQUEST
        await wait_until(lambda: not module.paired)

        again = host.connect()
        await again.pair_up()
        await again.send_raw("x" * 513)
        await again.expect_error(PROXY_ERROR_FRAME_TOO_LARGE)
        assert await again.close_frame() == PROXY_CLOSE_FRAME_TOO_LARGE
        await wait_until(lambda: not module.paired)
    finally:
        await host.close()


# --------------------------------------------------------------------------- #
# AC34 — the budget on the agent's monotonic clock
# --------------------------------------------------------------------------- #


def _deadline_utc(wall: float) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(wall, tz=timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


async def test_ac34_remaining_ms_bounds_the_call_on_the_agents_monotonic_clock_whatever_deadline_utc_says() -> None:
    """AC34 (R6): the agent's wall clock is 3 h ahead of the brain's, so the
    brain's ``deadline_utc`` (brain wall + 5 s) is 3 h in the agent's past
    and would expire the call at once if it were read; the call is bounded
    at 5 s on the agent's monotonic clock from ``remaining_ms: 5000``. With
    ``remaining_ms: 20000`` and ``action_seconds: 10`` the bound is 10 s.
    A ``deadline_utc`` 3 h in the agent's future never extends it either."""

    host, brain = await paired_host(action_seconds=10)
    try:
        before = host.clock()
        await brain.call("call-1", 1, remaining_ms=5000, deadline_utc=_deadline_utc(BRAIN_WALL_CLOCK + 5))
        observation = await brain.observation("call-1")
        assert observation["status"] == "success", observation
        (call,) = host.provider.calls
        assert call.deadline == pytest.approx(before + 5.0)
        assert call.call_id == "call-1" and call.principal == PRINCIPAL and call.run_id == RUN_ID
        assert call.destination == CAPTURE_DESTINATION

        await brain.call("call-2", 2, remaining_ms=20_000, deadline_utc=_deadline_utc(AGENT_WALL_CLOCK + 3 * 3600))
        await brain.observation("call-2")
        assert host.provider.calls[-1].deadline == pytest.approx(before + 10.0)

        await brain.call("call-3", 3, remaining_ms=20_000, deadline_utc="not even a date")
        await brain.observation("call-3")
        assert host.provider.calls[-1].deadline == pytest.approx(before + 10.0)

        # A budget already spent on the brain: expired before the provider.
        await brain.call("call-4", 4, remaining_ms=0)
        expired = await brain.observation("call-4")
        assert expired["status"] == "timeout" and len(host.provider.calls) == 3
        assert host.executor.outcome("call-4").status == "timeout"
    finally:
        await host.close()


# --------------------------------------------------------------------------- #
# Default-deny execution with the forwarded principal
# --------------------------------------------------------------------------- #


async def test_a_forwarded_call_runs_through_the_agents_default_deny_executor() -> None:
    """R6: peer authentication grants nothing. Without an agent-side rule the
    call is ``refused not_authorized`` with 0 providers entered; the rule
    granting principal ``brain`` does not cover another forwarded principal;
    with the rule the local provider serves it and the observation carries
    the executor's provenance and the action's result."""

    host, brain = await paired_host(policy=AuthorizationPolicy())
    try:
        await brain.call("call-1", 1)
        refused = await brain.observation("call-1")
        assert refused["status"] == "refused"
        assert refused["error"]["code"] == ERROR_NOT_AUTHORIZED
        assert host.provider.calls == [] and host.executor.provider_invocations == 0
        assert host.module.executions == 1
        assert host.executor.outcome("call-1").status == "refused"
        started = events_of(host.bus, TRACE_ACTION_STARTED)
        assert started and started[-1]["payload"]["principal"] == PRINCIPAL
    finally:
        await host.close()

    host, brain = await paired_host()
    try:
        await brain.call("call-1", 1, principal="viewer")
        assert (await brain.observation("call-1"))["error"]["code"] == ERROR_NOT_AUTHORIZED
        assert host.provider.calls == []

        await brain.call("call-2", 2, arguments={"source": "screen"})
        served = await brain.observation("call-2")
        assert served["status"] == "success" and served["result"] == capture_result(4)
        assert served["provenance"]["provider"] == "capture"
        assert served["provenance"]["module"] == "capture"
        assert served["parts"] == []
        assert "error" not in served
        assert host.provider.calls[-1].arguments == {"source": "screen"}
        assert host.executor.provider_invocations == 1

        await brain.call("call-3", 3, arguments={"source": 7})
        assert (await brain.observation("call-3"))["error"]["code"] == "invalid_arguments"
        await brain.call("call-4", 4, action="no.such")
        assert (await brain.observation("call-4"))["error"]["code"] == "unknown_action"
        assert host.executor.provider_invocations == 1
    finally:
        await host.close()


async def test_a_malformed_call_frame_is_invalid_frame_and_touches_no_state() -> None:
    host, brain = await paired_host()
    try:
        for mutation in (
            {"seq": None}, {"seq": 0}, {"seq": -1}, {"seq": "1"}, {"seq": True}, {"seq": 1.5},
            {"remaining_ms": -1}, {"remaining_ms": "5000"}, {"arguments": []},
            {"destination": "fake/channel-9/capture"}, {"destination": {"platform": "fake"}},
            {"run_id": ""}, {"principal": None}, {"call_id": "another"}, {"action_version": "1"},
        ):
            frame = brain.call_frame("call-x", 1)
            frame.update(mutation)
            for key in [key for key, value in mutation.items() if value is None]:
                del frame[key]
            await brain.send(frame)
            error = await brain.expect_error(PROXY_ERROR_INVALID_FRAME)
            assert error["id"] == "call-x" and error["retryable"] is False
        assert host.module.last_seq == 0 and host.module.retained_calls == ()
        assert host.module.executions == 0
        # The next well-formed call is still seq 1 and executes.
        await brain.call("call-1", 1)
        assert (await brain.observation("call-1"))["status"] == "success"
        assert host.module.last_seq == 1
    finally:
        await host.close()


# --------------------------------------------------------------------------- #
# AC36 — call identity and de-duplication on the agent
# --------------------------------------------------------------------------- #


async def test_ac36_agent_side_a_repeated_call_returns_the_retained_observation_without_executing() -> None:
    host, brain = await paired_host(heartbeat_seconds=100_000)
    try:
        await brain.call("call-1", 1)
        first = await brain.observation("call-1")
        assert first["status"] == "success" and host.module.executions == 1
        first_text = brain.pair.client.sent[-1].data

        await brain.call("call-1", 1)
        repeat = await brain.observation("call-1")
        assert repeat == first and host.module.executions == 1
        assert host.executor.provider_invocations == 1
        assert brain.pair.client.sent[-1].data == first_text
        assert host.module.retained_calls == ("call-1",) and host.module.last_seq == 1

        # A repeat while the first execution is still running waits for the
        # same observation: one execution, two identical frames.
        host.provider.hold = asyncio.get_running_loop().create_future()
        await brain.call("call-2", 2)
        await host.provider.entered.wait()
        await brain.call("call-2", 2)
        await settle()
        assert brain.received_frames(FRAME_OBSERVATION) == [first, first]
        host.provider.hold.set_result(None)
        second = await brain.observation("call-2")
        assert await brain.observation("call-2") == second
        assert host.module.executions == 2 and host.executor.provider_invocations == 2
    finally:
        await host.close()


async def test_ac36_agent_side_the_same_frame_after_ttl_plus_one_is_duplicate_call_unknown() -> None:
    host, brain = await paired_host(heartbeat_seconds=100_000)
    try:
        await brain.call("call-1", 1)
        await brain.observation("call-1")
        host.clock.advance(300)
        await brain.call("call-1", 1)
        assert (await brain.observation("call-1"))["status"] == "success"
        host.clock.advance(1)
        await brain.call("call-1", 1)
        error = await brain.expect_error(PROXY_ERROR_DUPLICATE_CALL_UNKNOWN)
        assert error["id"] == "call-1" and error["retryable"] is False
        assert host.module.executions == 1 and host.module.retained_calls == ()
        assert host.module.last_seq == 1
    finally:
        await host.close()


async def test_ac36_agent_side_a_configured_ttl_is_honoured() -> None:
    host, brain = await paired_host(heartbeat_seconds=100_000, call_table={"ttl_seconds": 5})
    try:
        await brain.call("call-1", 1)
        await brain.observation("call-1")
        host.clock.advance(6)
        await brain.call("call-1", 1)
        await brain.expect_error(PROXY_ERROR_DUPLICATE_CALL_UNKNOWN)
        assert host.module.executions == 1
    finally:
        await host.close()


async def test_ac36_agent_side_a_never_seen_call_id_at_or_below_last_seq_is_duplicate_call_unknown() -> None:
    host, brain = await paired_host()
    try:
        await brain.call("call-1", 1)
        await brain.observation("call-1")
        await brain.call("call-2", 2)
        await brain.observation("call-2")
        for seq in (1, 2):
            await brain.call("never-seen", seq)
            error = await brain.expect_error(PROXY_ERROR_DUPLICATE_CALL_UNKNOWN)
            assert error["id"] == "never-seen" and error["retryable"] is False
        assert host.module.executions == 2 and host.module.last_seq == 2
        assert host.module.retained_calls == ("call-1", "call-2")
        # A retained call_id under another seq is malformed, not a repeat.
        await brain.call("call-1", 3)
        await brain.expect_error(PROXY_ERROR_INVALID_FRAME)
        await brain.call("call-2", 1)
        await brain.expect_error(PROXY_ERROR_INVALID_FRAME)
        assert host.module.executions == 2 and host.module.last_seq == 2
        # A gap in seq is fine: only the order matters.
        await brain.call("call-9", 9)
        await brain.observation("call-9")
        assert host.module.last_seq == 9 and host.module.executions == 3
    finally:
        await host.close()


async def test_ac36_agent_side_max_entries_2_evicts_the_oldest_and_the_table_holds_exactly_2() -> None:
    host, brain = await paired_host(call_table={"max_entries": 2})
    try:
        for index in (1, 2, 3):
            await brain.call(f"call-{index}", index)
            await brain.observation(f"call-{index}")
        assert host.module.retained_calls == ("call-2", "call-3")
        await brain.call("call-1", 1)
        await brain.expect_error(PROXY_ERROR_DUPLICATE_CALL_UNKNOWN)
        assert host.module.executions == 3
        assert len(host.module.retained_calls) == 2
        await brain.call("call-3", 3)
        assert (await brain.observation("call-3"))["status"] == "success"
        assert host.module.executions == 3
    finally:
        await host.close()


async def test_ac36_agent_side_a_new_welcome_resets_last_seq_and_the_table() -> None:
    """After a new ``welcome`` a ``call`` with ``seq: 1`` executes — even
    when it repeats a ``call_id`` of the previous session, and even when the
    brain reissues the same ``session_id``: the table is per pairing."""

    host, brain = await paired_host(immediate_sleeper=True)
    try:
        await brain.call("call-1", 1)
        await brain.observation("call-1")
        await brain.call("call-2", 2)
        await brain.observation("call-2")
        assert host.module.retained_calls == ("call-1", "call-2")

        await brain.ws.close(code=1000)
        again = host.connect()
        await again.pair_up(session_id=brain.session_id)
        assert host.module.pairings == 2
        assert host.module.last_seq == 0 and host.module.retained_calls == ()
        await again.call("call-3", 1)
        assert (await again.observation("call-3"))["status"] == "success"
        assert host.module.executions == 3 and host.module.last_seq == 1
        # The executor's own outcome store answers a call id it already
        # terminated: 0 providers entered, but the link did execute it.
        await again.call("call-1", 2)
        assert (await again.observation("call-1"))["status"] == "success"
        assert host.module.executions == 4 and host.executor.provider_invocations == 3
    finally:
        await host.close()


# --------------------------------------------------------------------------- #
# AC37 — backoff
# --------------------------------------------------------------------------- #


async def test_ac37_six_failures_back_off_half_one_two_four_eight_fifteen_and_a_welcome_resets() -> None:
    """AC37 (R6, decision 9): with an RNG returning 0.5 the delays after six
    consecutive failures are exactly [0.5, 1, 2, 4, 8, 15] s (full jitter of
    1·2ⁿ capped at 30); attempts are unlimited; after a ``welcome`` the next
    failure starts again from the 1 s base."""

    host = AgentHost(rng=ConstantRandom(0.5), immediate_sleeper=True)
    try:
        module = await host.activate()
        host.fail_dial(6)
        await wait_until(lambda: len(host.backoffs) == 6)
        assert host.backoffs == [0.5, 1.0, 2.0, 4.0, 8.0, 15.0]
        await wait_until(lambda: module.attempts == 7)
        assert module.consecutive_failures == 6
        assert all(TOKEN not in line and BRAIN_URL not in line for line in host.diagnostics)

        # Still dialing: the seventh attempt pairs.
        brain = host.connect()
        await brain.pair_up()
        assert module.consecutive_failures == 0 and module.paired
        assert len(host.backoffs) == 6

        # A drop after the welcome: the next failure is 0.5 s again.
        brain.pair.drop()
        await wait_until(lambda: len(host.backoffs) == 7)
        assert host.backoffs[-1] == 0.5 and not module.paired
        host.fail_dial(1)
        await wait_until(lambda: len(host.backoffs) == 8)
        assert host.backoffs[-1] == 1.0
        degraded = events_of(host.bus, TRACE_MODULE_DEGRADED)
        assert degraded and "disconnected" in degraded[-1]["payload"]["reason"]
    finally:
        await host.close()


async def test_ac37_the_cap_and_the_configured_parameters_bound_every_delay() -> None:
    host = AgentHost(
        rng=ConstantRandom(1.0), immediate_sleeper=True,
        reconnect={"initial_seconds": 0.25, "multiplier": 4, "max_seconds": 10},
    )
    try:
        await host.activate()
        host.fail_dial(5)
        await wait_until(lambda: len(host.backoffs) == 5)
        assert host.backoffs == [0.25, 1.0, 4.0, 10.0, 10.0]
    finally:
        await host.close()


async def test_ac37_the_backoff_sleeps_on_the_injected_sleeper_and_nothing_dials_meanwhile() -> None:
    host = AgentHost(rng=ConstantRandom(0.5))
    try:
        module = await host.activate()
        host.fail_dial(1)
        await wait_until(lambda: host.backoffs == [0.5])
        await settle()
        assert module.attempts == 1 and host.dial_urls == [BRAIN_URL]
        host.clock.advance(0.5)
        await wait_until(lambda: module.attempts == 2)
        assert host.dial_urls == [BRAIN_URL, BRAIN_URL]
    finally:
        await host.close()


# --------------------------------------------------------------------------- #
# Attachments from the agent (AC33, agent half)
# --------------------------------------------------------------------------- #


async def test_the_agent_transfers_the_image_as_one_header_one_binary_frame_then_the_observation() -> None:
    image = png_bytes(16, 9)
    host, brain = await paired_host(image=image)
    try:
        await brain.call("call-1", 1)
        header, payload = await brain.receive_transfer()
        assert header["id"] == brain.session_id and header["call_id"] == "call-1"
        assert header["content_type"] == "image/png" and header["size"] == len(image)
        assert payload == image
        attachment_id = header["attachment_id"]
        assert host.store.lookup(attachment_id) is not None
        # The observation waits for the ack.
        await settle()
        assert FRAME_OBSERVATION not in brain.received_types()
        await brain.ack(attachment_id)
        observation = await brain.observation("call-1")
        assert observation["status"] == "success"
        (part,) = observation["parts"]
        assert part["type"] == "image_ref" and part["attachment_id"] == attachment_id
        assert part["size"] == len(image) and part["provider_id"] == "capture"
        # No filesystem path: the only slashes are the content type's and
        # the destination's (``fake/channel-9/capture``) in the provenance.
        rendered = json.dumps(observation).replace("image/png", "").replace(str(CAPTURE_DESTINATION), "")
        assert "/" not in rendered, rendered
        assert brain.received_types() == [FRAME_HELLO, FRAME_EVENT, FRAME_ATTACHMENT, "<binary>", FRAME_OBSERVATION]
        # The bytes left the agent: nothing is retained locally.
        assert host.store.object_count == 0
        # A repeat re-sends the observation only: no second transfer.
        await brain.call("call-1", 1)
        assert await brain.observation("call-1") == observation
        assert brain.received_types().count(FRAME_ATTACHMENT) == 1
    finally:
        await host.close()


@pytest.mark.parametrize("code", [ATTACHMENT_ACK_TOO_LARGE, ATTACHMENT_ACK_STORE_FULL, ATTACHMENT_ACK_UNEXPECTED_BINARY])
async def test_an_unaccepted_ack_drops_the_part_and_the_observation_is_error_attachment_refused(code: str) -> None:
    image = png_bytes(4, 4)
    host, brain = await paired_host(image=image)
    try:
        await brain.call("call-1", 1)
        header, _ = await brain.receive_transfer()
        await brain.ack(header["attachment_id"], accepted=False, code=code)
        observation = await brain.observation("call-1")
        assert observation["status"] == "error"
        assert observation["error"]["code"] == ERROR_ATTACHMENT_REFUSED
        assert code in observation["error"]["message"]
        assert observation["parts"] == [] and "result" not in observation
        assert host.store.object_count == 0
        assert host.executor.outcome("call-1").status == "success"
    finally:
        await host.close()


async def test_an_image_above_the_calls_max_attachment_bytes_is_never_sent() -> None:
    image = png_bytes(8, 8)
    host, brain = await paired_host(image=image)
    try:
        await brain.call("call-1", 1, max_attachment_bytes=len(image) - 1)
        observation = await brain.observation("call-1")
        assert observation["status"] == "error"
        assert observation["error"]["code"] == ERROR_ATTACHMENT_REFUSED
        assert FRAME_ATTACHMENT not in brain.received_types()
        assert host.store.object_count == 0
        await brain.call("call-2", 2, max_attachment_bytes=len(image))
        header, payload = await brain.receive_transfer()
        await brain.ack(header["attachment_id"])
        assert (await brain.observation("call-2"))["status"] == "success"
    finally:
        await host.close()


# --------------------------------------------------------------------------- #
# AC33 — brain proxy and agent link in one process
# --------------------------------------------------------------------------- #


class PairedRuntimes:
    """The brain's proxy and the agent's link on the two ends of one pair.

    The agent runs the shipped ``modules/capture`` on a fake screen through
    its own executor; the brain's executor calls ``screen.capture`` exactly
    as it would a local provider.
    """

    def __init__(self, *, image: bytes = PNG, brain_actions: tuple[str, ...] = (SCREEN_CAPTURE,)) -> None:
        self.brain = Brain(actions=brain_actions, specs=(screen_capture_spec(), fake_write_spec()))
        self.agent = AgentHost(clock=ManualClock(5000.0), register_provider=False)
        self.source = FakeCaptureSource(data=image, width=IMAGE_WIDTH, height=IMAGE_HEIGHT)
        self.capture: Any = None
        self.handlers: list[asyncio.Task[None]] = []
        self.pairs: list[MemoryWebSocketPair] = []

    async def start(self) -> None:
        await self.brain.activate(start=True)
        # The agent's shipped capture module, on the agent's own context.
        self.capture = await capture_module.activate(
            self.agent.context.for_module("capture"),
            {
                "sources": {CAPTURE_SOURCE: {"kind": "file", "path": "/nonexistent/never-read.png"}},
                "default_source": CAPTURE_SOURCE,
                "_source_factory": lambda spec: self.source,
            },
            {},
        )
        await self.capture.prepare()
        await self.agent.activate()
        await self.pair()

    async def pair(self) -> None:
        pair = MemoryWebSocketPair()
        self.pairs.append(pair)
        handler = self.brain.servers[0].kwargs["handler"]
        self.handlers.append(asyncio.create_task(handler(pair.server)))
        self.agent.dials.put_nowait(pair)
        await wait_until(lambda: self.agent.module.paired and self.brain.module.paired)
        await settle()

    def wire(self, index: int = -1) -> list[str]:
        """Every frame that crossed the pair, in order, prefixed by its direction."""

        pair = self.pairs[index]
        agent_sent = [(m, "A>") for m in pair.client.sent]
        brain_sent = [(m, "B>") for m in pair.server.sent]
        # Each end's ``sent`` is ordered; the tests read per-direction
        # sequences and counts, never the interleaving.
        types: list[str] = []
        for message, prefix in agent_sent + brain_sent:
            if message.type == WSMsgType.TEXT:
                types.append(prefix + json.loads(message.data)["type"])
            elif message.type == WSMsgType.BINARY:
                types.append(prefix + "<binary>")
        return types

    async def close(self) -> None:
        await self.agent.close()
        if self.capture is not None:
            await self.capture.close()
        await self.brain.close()
        for task in self.handlers:
            if not task.done():
                task.cancel()
        await asyncio.gather(*self.handlers, return_exceptions=True)


async def test_ac33_in_process_the_brains_executor_reaches_the_agents_capture_by_reference() -> None:
    """AC33 (R6), executor level: the brain's ``screen.capture`` call is
    served by the agent's shipped capture module through the agent's own
    default-deny executor; the image reaches the brain's store after
    exactly one ``attachment`` header, one binary frame and one
    ``attachment_ack``; the observation frame carries no filesystem path;
    the agent retains no bytes and the brain's run cleanup releases exactly
    what was leased — as with the local provider."""

    paired = PairedRuntimes()
    try:
        await paired.start()
        assert paired.brain.authorized() == [SCREEN_CAPTURE]
        assert paired.agent.module.accepted_actions == frozenset({SCREEN_CAPTURE})

        observation = await paired.brain.executor.invoke(
            capture_call("call-1", deadline=paired.brain.clock() + 30)
        )
        assert observation.status == "success", observation.error
        assert observation.provenance["provider"] == PROVIDER_NAME
        assert observation.provenance["remote"]["provider"] == "capture"
        (part,) = observation.parts
        ref = paired.brain.store.lookup(part["attachment_id"])
        assert ref is not None and ref.run_id == RUN_ID and ref.size == len(PNG)
        assert paired.brain.store.get(ref) == PNG
        assert part["width"] == IMAGE_WIDTH and part["height"] == IMAGE_HEIGHT
        assert observation.result["source"] == CAPTURE_SOURCE
        assert len(paired.source.calls) == 1
        assert paired.agent.executor.provider_invocations == 1
        assert paired.agent.executor.outcome("call-1").status == "success"
        assert paired.agent.store.object_count == 0

        wire = paired.wire()
        assert wire.count("A>" + FRAME_ATTACHMENT) == 1
        assert wire.count("A><binary>") == 1
        assert wire.count("B>" + FRAME_ATTACHMENT_ACK) == 1
        assert wire.count("A>" + FRAME_OBSERVATION) == 1
        assert [t for t in wire if t.startswith("A>")] == [
            "A>" + FRAME_HELLO, "A>" + FRAME_EVENT, "A>" + FRAME_ATTACHMENT, "A><binary>", "A>" + FRAME_OBSERVATION,
        ]
        assert [t for t in wire if t.startswith("B>")] == ["B>" + FRAME_WELCOME, "B>" + FRAME_CALL, "B>" + FRAME_ATTACHMENT_ACK]
        frames = [json.loads(m.data) for m in paired.pairs[-1].client.sent if m.type == WSMsgType.TEXT]
        assert "/nonexistent" not in json.dumps(frames)
        assert TOKEN not in json.dumps([f for f in frames if f["type"] != FRAME_HELLO])
        assert paired.brain.module.late_observations == 0

        released = paired.brain.store.release(RUN_ID)
        assert released.objects == 1 and paired.brain.store.object_count == 0
    finally:
        await paired.close()


async def test_ac33_in_process_the_agent_refuses_a_principal_its_rule_does_not_grant() -> None:
    paired = PairedRuntimes()
    try:
        await paired.start()
        call = ActionCall(
            action_name=SCREEN_CAPTURE, action_version=1, arguments={}, conversation_id="c",
            run_id=RUN_ID, call_id="call-v", source_event_id="s", destination=CAPTURE_DESTINATION,
            principal="viewer", deadline=paired.brain.clock() + 30,
        )
        observation = await paired.brain.executor.invoke(call)
        assert observation.status == "refused"
        assert observation.error["code"] == ERROR_NOT_AUTHORIZED
        assert paired.source.calls == [] and paired.agent.executor.provider_invocations == 0
    finally:
        await paired.close()


# --------------------------------------------------------------------------- #
# AC35 from the agent side, cancel, heartbeat
# --------------------------------------------------------------------------- #


async def test_ac35_agent_side_a_drop_with_a_call_in_flight_repairs_without_retransmission() -> None:
    """AC35 (R6) seen from the agent: the connection drops while the local
    execution is in flight; the agent reports degraded, re-dials after the
    backoff and pairs again from scratch (``hello``, ``welcome``, one
    ``agent.status``); the execution finishes locally and its observation
    is not sent on the new session; nothing is retransmitted."""

    host, brain = await paired_host(immediate_sleeper=True, image=png_bytes(2, 2))
    try:
        module = host.module
        host.provider.hold = asyncio.get_running_loop().create_future()
        await brain.call("call-1", 1)
        await host.provider.entered.wait()
        assert module.in_flight == ("call-1",)

        brain.pair.drop()
        await wait_until(lambda: not module.paired)
        again = host.connect()
        await again.pair_up(session_id="session-2")
        assert host.backoffs == [0.5]
        assert module.pairings == 2 and module.last_seq == 0 and module.retained_calls == ()
        degraded = events_of(host.bus, TRACE_MODULE_DEGRADED)
        assert degraded and "disconnected" in degraded[-1]["payload"]["reason"]

        host.provider.hold.set_result(None)
        await wait_until(lambda: host.executor.outcome("call-1") is not None)
        await settle()
        assert host.executor.outcome("call-1").status == "success"
        assert again.received_types() == [FRAME_HELLO, FRAME_EVENT]
        assert brain.received_types() == [FRAME_HELLO, FRAME_EVENT]
        # The bytes captured for the dropped call are not retained.
        assert host.store.object_count == 0

        # The new session starts at seq 1 and serves a fresh call.
        await again.call("call-2", 1)
        header, _ = await again.receive_transfer()
        await again.ack(header["attachment_id"])
        assert (await again.observation("call-2"))["status"] == "success"
    finally:
        await host.close()


async def test_a_cancel_frame_cancels_the_local_execution_and_nothing_is_sent_for_it() -> None:
    host, brain = await paired_host()
    try:
        host.provider.hold = asyncio.get_running_loop().create_future()
        await brain.call("call-1", 1)
        await host.provider.entered.wait()
        await brain.send({"type": FRAME_CANCEL, "id": "call-1"})
        await wait_until(lambda: host.executor.outcome("call-1") is not None)
        await settle()
        assert host.executor.outcome("call-1").status == "cancelled"
        assert host.executor.outcome("call-1").error["code"] == ERROR_CANCELLED
        assert host.module.in_flight == ()
        assert brain.received_types() == [FRAME_HELLO, FRAME_EVENT]
        # A cancel for an unknown call is ignored; the link still serves.
        host.provider.hold = None
        await brain.send({"type": FRAME_CANCEL, "id": "nope"})
        await brain.call("call-2", 2)
        assert (await brain.observation("call-2"))["status"] == "success"
    finally:
        await host.close()


async def test_a_cancel_during_the_image_transfer_discards_the_image_and_resolves_the_call() -> None:
    """The cancel lands while the transfer awaits its ``attachment_ack``:
    the leased image leaves the agent's store at once (not at expiry), the
    execution is over, nothing more is sent for the call, and a repeat of
    it re-sends nothing rather than hanging on an unresolved entry."""

    host, brain = await paired_host(image=png_bytes(2, 2))
    try:
        module = host.module
        await brain.call("call-1", 1)
        header, _ = await brain.receive_transfer()
        assert host.store.object_count == 1 and module.in_flight == ("call-1",)
        await brain.send({"type": FRAME_CANCEL, "id": "call-1"})
        await wait_until(lambda: module.in_flight == ())
        await settle()
        assert host.store.object_count == 0
        assert host.store.lookup(header["attachment_id"]) is None
        assert brain.received_types() == [FRAME_HELLO, FRAME_EVENT, FRAME_ATTACHMENT, "<binary>"]
        # A late ack for the cancelled transfer is ignored; a repeat of the
        # call is answered from the (empty) retained outcome: nothing sent.
        await brain.ack(header["attachment_id"])
        await brain.call("call-1", 1)
        await settle()
        assert brain.received_types() == [FRAME_HELLO, FRAME_EVENT, FRAME_ATTACHMENT, "<binary>"]
        assert module.executions == 1 and module.retained_calls == ("call-1",)
        # The link still serves: the next call transfers and observes.
        await brain.call("call-2", 2)
        header, _ = await brain.receive_transfer()
        await brain.ack(header["attachment_id"])
        assert (await brain.observation("call-2"))["status"] == "success"
        assert host.store.object_count == 0
    finally:
        await host.close()


async def test_a_call_evicted_from_the_table_while_running_stays_cancellable() -> None:
    """``max_entries: 1``: accepting ``call-2`` evicts ``call-1`` from the
    retention table while its provider still runs; ``cancel call-1`` still
    reaches that execution — the table bounds retained observations, not
    the running executions."""

    host, brain = await paired_host(call_table={"max_entries": 1})
    try:
        module = host.module
        # One hold per call: cancelling a task cancels the future it awaits.
        host.provider.hold = asyncio.get_running_loop().create_future()
        await brain.call("call-1", 1)
        await host.provider.entered.wait()
        host.provider.entered.clear()
        host.provider.hold = second = asyncio.get_running_loop().create_future()
        await brain.call("call-2", 2)
        await host.provider.entered.wait()
        assert module.retained_calls == ("call-2",)
        assert module.in_flight == ("call-1", "call-2")

        await brain.send({"type": FRAME_CANCEL, "id": "call-1"})
        await wait_until(lambda: host.executor.outcome("call-1") is not None)
        await settle()
        assert host.executor.outcome("call-1").status == "cancelled"
        assert host.executor.outcome("call-2") is None
        assert module.in_flight == ("call-2",)

        second.set_result(None)
        assert (await brain.observation("call-2"))["status"] == "success"
        assert module.in_flight == ()
        assert brain.received_frames(FRAME_OBSERVATION) == [
            frame for frame in brain.received_frames(FRAME_OBSERVATION) if frame["id"] == "call-2"
        ]
    finally:
        await host.close()


async def test_agent_heartbeat_pings_every_15_seconds_and_a_missing_pong_within_10_is_a_drop() -> None:
    host, brain = await paired_host()
    try:
        module = host.module
        host.clock.advance(14)
        await settle()
        assert FRAME_PING not in brain.received_types()
        host.clock.advance(1)
        await settle()
        assert await brain.frame() == {"v": 1, "type": FRAME_PING, "id": brain.session_id}
        host.clock.advance(9)
        await brain.send({"type": FRAME_PONG, "id": brain.session_id})
        await settle()
        host.clock.advance(1)
        await settle()
        assert module.paired and not brain.ws.closed
        host.clock.advance(15)
        await settle()
        assert (await brain.frame())["type"] == FRAME_PING
        await brain.send({"type": FRAME_PONG, "id": "other"})
        await brain.expect_error(PROXY_ERROR_UNKNOWN_SESSION)
        host.clock.advance(10)
        await settle()
        assert await brain.close_frame() == agent_link.CLOSE_GOING_AWAY
        await wait_until(lambda: not module.paired)
        reasons = [e["payload"]["reason"] for e in events_of(host.bus, TRACE_MODULE_DEGRADED)]
        assert any("heartbeat" in reason for reason in reasons)
        # The drop is a failure: the backoff sleep is drawn on the clock.
        await wait_until(lambda: host.backoffs == [0.5])
    finally:
        await host.close()


async def test_close_cancels_a_running_execution_and_leaves_no_task_behind() -> None:
    host, brain = await paired_host()
    module = host.module
    host.provider.hold = asyncio.get_running_loop().create_future()
    await brain.call("call-1", 1)
    await host.provider.entered.wait()
    await module.close()
    assert not module.paired and not module.running
    assert host.executor.outcome("call-1").status == "cancelled"
    assert AGENT_MODULE not in host.context.tasks.owners()
    assert await brain.close_frame() == agent_link.CLOSE_GOING_AWAY
    await module.close()  # idempotent


# --------------------------------------------------------------------------- #
# AC33 — the AC1 scenario end to end, brain and agent loaded by the real loader
# --------------------------------------------------------------------------- #

PAIRED_SHIPPED = ("brain", "chat_context", "users", "capture", "proxy", AGENT_MODULE)


@pytest.fixture(scope="module")
def paired_modules(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """One temporary ``modules_directory`` for this file: copies of the
    shipped packages both profiles need plus the ``fakeplatform`` fixture."""

    directory = tmp_path_factory.mktemp("paired-modules")
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
    for name in PAIRED_SHIPPED:
        shutil.copytree(SHIPPED_MODULES / name, directory / name, ignore=ignore)
    shutil.copytree(FIXTURE_MODULES / FAKE_MODULE, directory / FAKE_MODULE, ignore=ignore)
    return directory



async def activate_remote_vertical(
    *bodies: Any, modules_directory: Path
) -> tuple[VerticalHarness, list[FakeServer]]:
    """The server profile's shape on the fake platform: the shipped
    ``fakeplatform``, ``chat_context``, ``users``, ``proxy`` (``actions:
    [screen.capture]``) and ``brain`` modules loaded and prepared by the real
    loader on one runtime — ``capture`` disabled, so ``screen.capture`` is
    served remotely only (decision 7). Mirrors ``activate_vertical`` of the
    agentic-loop suite with the proxy in the capture module's place."""

    clock = ManualClock()
    store = AttachmentStore(clock=clock, **VERTICAL_STORE_LIMITS)
    policy = AuthorizationPolicy(
        [grant(action) for action in (CHAT_READ, USERS_READ, SCREEN_CAPTURE, CHAT_WRITE)]
    )
    context = runtime_context(
        clock=clock,
        authorization=policy,
        attachments=store,
        trigger_registry=TriggerRegistry(companion_name=COMPANION),
        chat=ChatContext(clock=clock, **CHAT_LIMITS),
    )
    diagnostics: list[str] = []
    session = ScriptedModel(*bodies)
    fake_transport = SimpleNamespace(outcomes=[], sends=[])
    servers: list[FakeServer] = []

    async def factory(handler: Any, **kwargs: Any) -> FakeServer:
        server = FakeServer(handler=handler, **kwargs)
        servers.append(server)
        return server

    brain_settings = merge_settings(None)
    brain_settings.update(
        {
            "_session_factory": lambda: session,
            "_sleeper": clock.sleep,
            "diagnostic_reporter": diagnostics.append,
        }
    )
    modules: dict[str, dict[str, Any]] = {
        FAKE_MODULE: {
            "channel_ids": [FAKE_CHANNEL],
            "companion_name": COMPANION,
            "transport": fake_transport,
            "diagnostic_reporter": diagnostics.append,
        },
        "chat_context": {"limits": {"chat_context": dict(CHAT_LIMITS)}},
        "users": dict(USERS_SETTINGS),
        MODULE_NAME: {
            **BASE_SETTINGS,
            "actions": [SCREEN_CAPTURE],
            "_server_factory": factory,
            "_sleeper": clock.sleep,
            "_wall_clock": lambda: BRAIN_WALL_CLOCK,
            "diagnostic_reporter": diagnostics.append,
            "limits": {"attachments": dict(VERTICAL_STORE_LIMITS)},
        },
        BRAIN_MODULE: brain_settings,
    }
    loader = ModuleLoader(context.bus, modules_directory, context=context, environ={})
    activations = await loader.activate_enabled(
        {"enabled_modules": list(modules), "modules": modules}
    )
    handles = {activation.name: activation.handle for activation in activations}
    assert list(handles) == list(modules)
    harness = VerticalHarness(
        platform=FAKE_PLATFORM,
        channel=FAKE_CHANNEL,
        context=context,
        clock=clock,
        store=store,
        session=session,
        source=FakeCaptureSource(data=PNG, width=IMAGE_WIDTH, height=IMAGE_HEIGHT),
        handles=handles,
        diagnostics=diagnostics,
        tasks_before=0,
        fake_transport=fake_transport,
    )
    try:
        for handle in handles.values():
            await handle.prepare()
        await handles[FAKE_MODULE].start_inputs()
        await handles[MODULE_NAME].start_inputs()
    except BaseException:
        await harness.close()
        raise
    harness.tasks_before = context.tasks.active
    return harness, servers


class AgentProfile:
    """The agent profile's shape: the shipped ``capture`` (fake screen) and
    ``agent_link`` modules loaded by the real loader on the agent's own
    runtime — its own clock, store, random source and a default-deny policy
    granting ``screen.capture`` to principal ``brain`` — and driven by the
    real phase coordinator (prepare, barrier, ``start_inputs``; ``stop``)."""

    def __init__(self) -> None:
        self.clock = ManualClock(7000.0)
        self.store = AttachmentStore(clock=self.clock, **STORE_LIMITS)
        self.context: RuntimeContext = runtime_context(
            clock=self.clock,
            attachments=self.store,
            authorization=agent_policy(),
            rng=ConstantRandom(0.5),
        )
        self.source = FakeCaptureSource(data=PNG, width=IMAGE_WIDTH, height=IMAGE_HEIGHT)
        self.dials: asyncio.Queue[MemoryWebSocketPair] = asyncio.Queue()
        self.diagnostics: list[str] = []
        self.handles: dict[str, Any] = {}
        self.coordinator: PhaseCoordinator | None = None

    async def _connector(self, url: str, **kwargs: Any) -> Any:
        assert url == BRAIN_URL
        return (await self.dials.get()).client

    async def start(self, modules_directory: Path) -> None:
        modules: dict[str, dict[str, Any]] = {
            "capture": {
                "sources": {CAPTURE_SOURCE: {"kind": "file", "path": "/nonexistent/never-read.png"}},
                "default_source": CAPTURE_SOURCE,
                "_source_factory": lambda spec: self.source,
            },
            AGENT_MODULE: {
                **AGENT_SETTINGS,
                "actions": [SCREEN_CAPTURE],
                "_connector": self._connector,
                "_sleeper": self.clock.sleep,
                "diagnostic_reporter": self.diagnostics.append,
                "limits": {"attachments": dict(STORE_LIMITS)},
            },
        }
        loader = ModuleLoader(self.context.bus, modules_directory, context=self.context, environ={})
        activations = await loader.activate_enabled(
            {"enabled_modules": list(modules), "modules": modules}
        )
        self.handles = {activation.name: activation.handle for activation in activations}
        assert list(self.handles) == ["capture", AGENT_MODULE]
        self.coordinator = PhaseCoordinator(
            activations, tasks=self.context.tasks, reporter=self.diagnostics.append
        )
        report = await self.coordinator.start()
        assert report.status == 0, (report, self.diagnostics)

    @property
    def link(self) -> AgentLinkModule:
        return self.handles[AGENT_MODULE]

    @property
    def executor(self) -> Any:
        return self.context.executor

    async def close(self) -> None:
        if self.coordinator is not None:
            await self.coordinator.stop()


def wire_types(pair: MemoryWebSocketPair) -> tuple[list[str], list[str]]:
    """``(agent → brain, brain → agent)`` frame types crossing *pair*, in order."""

    def types(end: Any) -> list[str]:
        result: list[str] = []
        for message in end.sent:
            if message.type == WSMsgType.TEXT:
                result.append(json.loads(message.data)["type"])
            elif message.type == WSMsgType.BINARY:
                result.append("<binary>")
        return result

    return types(pair.client), types(pair.server)


async def test_ac33_the_ac1_scenario_through_the_proxy_matches_the_local_provider(
    paired_modules: Path,
) -> None:
    """AC33 (R6): the AC1 scenario with ``screen.capture`` served through the
    proxy — the agent side (shipped ``capture`` + ``agent_link``, loaded by
    the real loader and started by the real coordinator on its own runtime)
    in the same process behind the in-memory transport — produces the same
    brain-side executor call sequence, the same trace set, the same tools
    offered, the same delivery and the same completion record as the local
    provider on the same fake platform; the image reaches the model from
    the brain's store after exactly one ``attachment`` header + one binary
    frame + one ``attachment_ack``; the ``observation`` frame carries no
    filesystem path; every store is empty once the run ends."""

    local = await activate_vertical(*AC1_SCRIPT, platform=FAKE_PLATFORM, modules_directory=paired_modules)
    remote, servers = await activate_remote_vertical(*AC1_SCRIPT, modules_directory=paired_modules)
    agent = AgentProfile()
    pair = MemoryWebSocketPair()
    handler: asyncio.Task[None] | None = None
    try:
        proxy = remote.handles[MODULE_NAME]
        registry = remote.context.actions
        assert set(registry.discovered()) == {CHAT_READ, USERS_READ, SCREEN_CAPTURE, CHAT_WRITE}
        assert [(b.module, b.provider_name) for b in registry.bindings(SCREEN_CAPTURE)] == [(MODULE_NAME, PROVIDER_NAME)]
        assert SCREEN_CAPTURE not in registry.registered_ready()
        assert len(servers) == 1

        await agent.start(paired_modules)
        assert [(b.module, b.provider_name) for b in agent.context.actions.bindings(SCREEN_CAPTURE)] == [("capture", "capture")]
        handler = asyncio.create_task(servers[0].kwargs["handler"](pair.server))
        agent.dials.put_nowait(pair)
        await wait_until(lambda: agent.link.paired and proxy.paired)
        await settle()
        assert proxy.agent_id == AGENT_ID
        assert SCREEN_CAPTURE in registry.registered_ready()
        status = events_of(remote.bus, AGENT_STATUS_EVENT)
        assert len(status) == 1 and status[0]["payload"] == {"state": STATE_PAIRED}
        assert status[0]["metadata"]["source"] == MODULE_NAME
        assert status[0]["metadata"]["provider_id"] == AGENT_ID

        runs: list[str] = []
        for harness in (local, remote):
            await harness.observe(*CONTEXT_LINES)
            record = await harness.ask()
            assert record.status == "success"
            runs.append(record.run_id)
        local_run, remote_run = runs

        # The same brain-side call sequence, served by a different provider.
        expected_calls = [(CHAT_READ, "call-1"), (SCREEN_CAPTURE, "call-2"), (CHAT_WRITE, "call-3")]
        assert call_suffixes(local, local_run) == expected_calls
        assert call_suffixes(remote, remote_run) == expected_calls
        for harness, run_id in ((local, local_run), (remote, remote_run)):
            assert [
                harness.executor.outcome(f"{run_id}/call-{index}").status for index in (1, 2, 3)
            ] == ["success", "success", "success"]
        assert local.executor.outcome(f"{local_run}/call-2").provenance["provider"] == "capture"
        remote_capture = remote.executor.outcome(f"{remote_run}/call-2")
        assert remote_capture.provenance["provider"] == PROVIDER_NAME
        assert remote_capture.provenance["agent_id"] == AGENT_ID
        assert remote_capture.provenance["remote"]["provider"] == "capture"
        assert remote_capture.result["source"] == CAPTURE_SOURCE

        # The same trace set, the same tools, the same delivery, the same record.
        assert trace_set(local, local_run) == trace_set(remote, remote_run)
        assert (TRACE_ACTION_COMPLETED, SCREEN_CAPTURE, "success", "call-2") in trace_set(remote, remote_run)
        assert [local.tools(index) for index in range(3)] == [remote.tools(index) for index in range(3)]
        assert remote.tools(0) == [CHAT_READ, SCREEN_CAPTURE, USERS_READ]
        assert local.sends() == remote.sends() == [FINAL_TEXT]
        compared = ("status", "turns", "action_calls", "model_calls", "sends", "delivery", "fallback")
        assert {key: local.run_completed()[key] for key in compared} == {
            key: remote.run_completed()[key] for key in compared
        }

        # The image reached the model from the brain's store, once, on both.
        for harness in (local, remote):
            assert [len(image_parts(body)) for body in harness.requests()] == [0, 0, 1]
        assert image_parts(remote.requests()[2]) == image_parts(local.requests()[2])

        # The agent executed the call through its own executor, once; the
        # capture was taken once on each side's screen.
        assert len(agent.source.calls) == 1 and len(local.source.calls) == 1
        assert agent.executor.provider_invocations == 1
        assert agent.executor.outcome(f"{remote_run}/call-2").status == "success"
        assert agent.link.executions == 1 and agent.link.last_seq == 1

        # Exactly one header + one binary frame + one ack on the wire; the
        # observation frame names the attachment and no path.
        agent_sent, brain_sent = wire_types(pair)
        assert agent_sent == [FRAME_HELLO, FRAME_EVENT, FRAME_ATTACHMENT, "<binary>", FRAME_OBSERVATION]
        assert brain_sent == [FRAME_WELCOME, FRAME_CALL, FRAME_ATTACHMENT_ACK]
        frames = [json.loads(m.data) for m in pair.client.sent if m.type == WSMsgType.TEXT]
        (observation_frame,) = [f for f in frames if f["type"] == FRAME_OBSERVATION]
        (header,) = [f for f in frames if f["type"] == FRAME_ATTACHMENT]
        assert observation_frame["parts"][0]["attachment_id"] == header["attachment_id"]
        assert header["size"] == len(PNG)
        assert "/nonexistent" not in json.dumps(frames) and "never-read" not in json.dumps(frames)
        assert TOKEN not in json.dumps([f for f in frames if f["type"] != FRAME_HELLO])
        assert proxy.late_observations == 0

        # Store cleanup: the run's cleanup released the brain's lease on
        # both sides exactly as with the local provider; the agent kept
        # nothing once the bytes were acknowledged.
        assert local.store.object_count == 0 and remote.store.object_count == 0
        assert agent.store.object_count == 0
        assert local.diagnostics == [] and remote.diagnostics == [] and agent.diagnostics == []
        assert TOKEN not in json.dumps(remote.bus.list_events())
    finally:
        await agent.close()
        await remote.close()
        await local.close()
        if handler is not None:
            if not handler.done():
                handler.cancel()
            await asyncio.gather(handler, return_exceptions=True)


# --------------------------------------------------------------------------- #
# Package hygiene
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("directory", ["modules/proxy", f"modules/{AGENT_MODULE}"])
def test_ac31_the_proxy_and_the_agent_link_name_no_platform(directory: str) -> None:
    """AC31 (R5): ``grep -r twitch`` over ``modules/proxy`` and
    ``modules/agent_link`` matches 0 lines — the two ends of the wire carry
    a destination's platform as a runtime value only. Extends the grep of
    the agentic-loop suite (``core/``, ``modules/brain``, ``modules/chat_context``,
    ``modules/users``, ``modules/capture``) to the two directories P20 adds."""

    completed = subprocess.run(
        ["grep", "-r", "-i", "--exclude-dir=__pycache__", "twitch", str(ROOT / directory)],
        capture_output=True,
        text=True,
        check=False,
    )
    matches = [line for line in completed.stdout.splitlines() if line]
    assert completed.returncode in (0, 1), completed.stderr
    assert matches == [], "\n".join(matches)


def test_the_agent_link_package_calls_no_sleep_directly() -> None:
    """R8: no ``time.sleep`` and no direct ``asyncio.sleep(...)`` call; every
    wait goes through the injected sleeper seam, which ``activate`` defaults
    to ``asyncio.sleep`` by reference."""

    source = (ROOT / "modules" / AGENT_MODULE / "__init__.py").read_text(encoding="utf-8")
    assert "time.sleep" not in source
    assert "asyncio.sleep(" not in source
    assert "settings.get(_SEAM_SLEEPER, asyncio.sleep)" in source


# =========================================================================== #
# Phase 2 P12 — audio attachments over protocol v1 (R5; AC18, AC19, AC20)
# =========================================================================== #
#
# The same in-memory pair and harnesses as above, with the shipped
# ``audio.capture`` and ``audio.speak`` contracts read from their manifests,
# a store whose object bound is the protocol's default binary bound
# (1 048 576 bytes), and WAV segments from ``conftest.wav_bytes``. No
# positive-duration sleep anywhere.

from core.contracts import ATTACHMENT_ACK_CODES as _ACK_CODES
from core.contracts import ATTACHMENT_CONTENT_TYPES, PROXY_DEFAULT_MAX_FRAME_BYTES as _MAX_FRAME
from conftest import wav_bytes

AUDIO_CAPTURE = "audio.capture"
AUDIO_SPEAK = "audio.speak"
AUDIO_INPUT_MANIFEST = ROOT / "modules" / "audio_input" / "module.yaml"
AUDIO_OUTPUT_MANIFEST = ROOT / "modules" / "audio_output" / "module.yaml"
AUDIO_DESTINATION = Destination("fake", "channel-9", "audio")
DEFAULT_BINARY_BOUND = 1_048_576

AUDIO_STORE_LIMITS: dict[str, Any] = {
    "max_object_bytes": DEFAULT_BINARY_BOUND,
    "max_objects": 4,
    "max_total_bytes": 4 * DEFAULT_BINARY_BOUND,
    "max_bytes_per_run": 2 * DEFAULT_BINARY_BOUND,
    "ttl_seconds": 300.0,
}

#: A 3 s, 16 kHz, mono, 16-bit segment: 44 header bytes + 96 000 data bytes.
SEGMENT_3S = wav_bytes(3.0)
#: A 30 s segment: 960 044 bytes, under the default bound.
SEGMENT_30S = wav_bytes(30.0)

TRANSCRIPTION: dict[str, Any] = {
    "text": "bonjour le chat",
    "transcribed_at": 1240.25,
    "provider_id": "stt-local",
    "truncated": False,
}


def _manifest(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def manifest_spec(path: Path, name: str) -> ActionSpec:
    (entry,) = [item for item in _manifest(path)["actions"] if item["name"] == name]
    spec = spec_from_declaration(entry)
    assert spec is not None
    return spec


def audio_capture_spec() -> ActionSpec:
    return manifest_spec(AUDIO_INPUT_MANIFEST, AUDIO_CAPTURE)


def audio_speak_spec() -> ActionSpec:
    return manifest_spec(AUDIO_OUTPUT_MANIFEST, AUDIO_SPEAK)


def audio_policy() -> AuthorizationPolicy:
    return AuthorizationPolicy(
        [
            AuthorizationRule(
                rule_id="grant-audio-capture",
                action_name=AUDIO_CAPTURE,
                principals=(PRINCIPAL,),
                granted_permissions=("audio.capture",),
            ),
            AuthorizationRule(
                rule_id="grant-audio-speak",
                action_name=AUDIO_SPEAK,
                principals=(PRINCIPAL,),
                granted_permissions=("audio.speak",),
            ),
        ]
    )


def audio_ref(attachment_id: str, size: int, *, transcription: dict[str, Any] | None = None) -> dict[str, Any]:
    part: dict[str, Any] = {
        "type": "audio_ref",
        "attachment_id": attachment_id,
        "content_type": "audio/wav",
        "size": size,
        "duration_ms": 3000,
        "sample_rate_hz": 16000,
        "channels": 1,
        "captured_at": 1234.5,
        "provider_id": "audio_input",
    }
    if transcription is not None:
        part["transcription"] = dict(transcription)
    return part


def audio_capture_result(size: int) -> dict[str, Any]:
    return {
        "source": "mic",
        "content_type": "audio/wav",
        "size": size,
        "duration_ms": 3000,
        "sample_rate_hz": 16000,
        "channels": 1,
        "captured_at": 1234.5,
        "transcription_status": "ok",
    }


def audio_call(
    call_id: str = "call-a1",
    *,
    action: str = AUDIO_CAPTURE,
    deadline: float,
    arguments: dict[str, Any] | None = None,
) -> ActionCall:
    return ActionCall(
        action_name=action,
        action_version=1,
        arguments=arguments if arguments is not None else {"seconds": 3},
        conversation_id="conversation-1",
        run_id=RUN_ID,
        call_id=call_id,
        source_event_id="source-1",
        destination=AUDIO_DESTINATION,
        principal=PRINCIPAL,
        deadline=deadline,
    )


def assert_every_frame_is_v1(pair: MemoryWebSocketPair) -> int:
    """AC20: every text frame either end wrote carries ``v == 1``."""

    count = 0
    for message in list(pair.client.sent) + list(pair.server.sent):
        if message.type == WSMsgType.TEXT:
            frame = json.loads(message.data)
            assert frame.get("v") == 1, frame
            count += 1
    assert count > 0
    return count


class RecordingAudio:
    """The agent's ``audio.capture`` provider: stores *segment* as ``audio/wav``
    in the agent's store under the call's run and answers the ``audio_ref``
    with its transcription, as ``modules/audio_input`` does."""

    name = "audio_input"

    def __init__(self, host: AgentHost, segment: bytes) -> None:
        self.host = host
        self.segment = segment
        self.calls: list[ActionCall] = []
        self.stored: list[str] = []

    async def invoke(self, invocation: Any) -> ActionObservation:
        call = invocation.call
        self.calls.append(call)
        ref = self.host.store.put(call.run_id, self.segment, content_type="audio/wav")
        self.stored.append(ref.attachment_id)
        return ActionObservation(
            status="success",
            provenance={"provider": self.name, "module": "audio_input"},
            result=audio_capture_result(len(self.segment)),
            parts=(audio_ref(ref.attachment_id, len(self.segment), transcription=TRANSCRIPTION),),
        )


def audio_agent_host(segment: bytes) -> tuple[AgentHost, RecordingAudio]:
    host = AgentHost(
        actions=(AUDIO_CAPTURE,),
        policy=audio_policy(),
        register_provider=False,
        store_limits=AUDIO_STORE_LIMITS,
    )
    provider = RecordingAudio(host, segment)
    host.context.actions.register(
        audio_capture_spec(), provider, module="audio_input", provider_name="audio_input"
    )
    host.context.actions.mark_ready("audio_input")
    return host, provider


def audio_call_frame(brain_end: BrainEnd, call_id: str, seq: int, **kwargs: Any) -> dict[str, Any]:
    frame = brain_end.call_frame(
        call_id, seq, action=AUDIO_CAPTURE, arguments={"seconds": 3}, **kwargs
    )
    frame["destination"] = {"platform": "fake", "channel_id": "channel-9", "scope": "audio"}
    return frame


# --------------------------------------------------------------------------- #
# AC18 — an audio segment crosses the boundary by reference
# --------------------------------------------------------------------------- #


async def test_ac18_an_agent_side_audio_capture_reaches_the_brain_store_by_reference() -> None:
    """AC18 (R5): the brain's proxy and the agent's link on one in-memory
    pair; the agent's ``audio.capture`` provider stores a 96 044-byte
    segment. Exactly 1 ``attachment`` header (``audio/wav``, 96 044), 1
    binary frame of 96 044 bytes and 1 ``attachment_ack accepted: true``
    cross; the brain's observation carries the ``audio_ref`` translated to a
    brain-store attachment of 96 044 bytes leased to the run, typed by the
    acknowledged header, its ``transcription`` verbatim. AC20: every frame
    carries ``v == 1``."""

    assert len(SEGMENT_3S) == 96_044
    brain = Brain(
        actions=(AUDIO_CAPTURE,),
        specs=(audio_capture_spec(),),
        store_limits=AUDIO_STORE_LIMITS,
        authorization=audio_policy(),
    )
    host, provider = audio_agent_host(SEGMENT_3S)
    pair = MemoryWebSocketPair()
    handler: asyncio.Task[None] | None = None
    try:
        await brain.activate(start=True)
        await host.activate()
        handler = asyncio.create_task(brain.servers[0].kwargs["handler"](pair.server))
        host.dials.put_nowait(pair)
        await wait_until(lambda: host.module.paired and brain.module.paired)
        await settle()
        assert brain.authorized(AUDIO_DESTINATION) == [AUDIO_CAPTURE]

        observation = await brain.executor.invoke(audio_call(deadline=brain.clock() + 30))

        assert observation.status == "success", observation.error
        assert observation.provenance["provider"] == PROVIDER_NAME
        assert observation.provenance["remote"]["provider"] == "audio_input"
        (part,) = observation.parts
        assert part["type"] == "audio_ref"
        assert part["attachment_id"] not in provider.stored
        ref = brain.store.lookup(part["attachment_id"])
        assert ref is not None and ref.run_id == RUN_ID
        assert ref.size == 96_044 and ref.content_type == "audio/wav"
        assert brain.store.get(ref) == SEGMENT_3S
        assert part["size"] == 96_044
        assert part["transcription"] == TRANSCRIPTION
        expected = audio_ref(part["attachment_id"], 96_044, transcription=TRANSCRIPTION)
        assert dict(part) == expected
        assert brain.store.usage(RUN_ID).objects == 1

        agent_frames = [m for m in pair.client.sent if m.type in (WSMsgType.TEXT, WSMsgType.BINARY)]
        headers = [
            json.loads(m.data) for m in agent_frames
            if m.type == WSMsgType.TEXT and json.loads(m.data)["type"] == FRAME_ATTACHMENT
        ]
        binaries = [m.data for m in agent_frames if m.type == WSMsgType.BINARY]
        assert len(headers) == 1
        assert headers[0]["content_type"] == "audio/wav" and headers[0]["size"] == 96_044
        assert headers[0]["attachment_id"] == provider.stored[0]
        assert [len(b) for b in binaries] == [96_044]
        acks = [
            json.loads(m.data) for m in pair.server.sent
            if m.type == WSMsgType.TEXT and json.loads(m.data)["type"] == FRAME_ATTACHMENT_ACK
        ]
        assert acks == [{
            "v": 1, "type": FRAME_ATTACHMENT_ACK, "id": headers[0]["id"],
            "attachment_id": provider.stored[0], "accepted": True,
        }]
        # The agent retains no bytes; the brain's run cleanup releases exactly
        # what was leased.
        assert host.store.object_count == 0
        assert assert_every_frame_is_v1(pair) >= 6
        released = brain.store.release(RUN_ID)
        assert released.objects == 1 and brain.store.object_count == 0
    finally:
        await host.close()
        await brain.close()
        if handler is not None and not handler.done():
            handler.cancel()
        if handler is not None:
            await asyncio.gather(handler, return_exceptions=True)


async def test_ac18_the_audio_header_rules_content_type_default_bound_and_too_large() -> None:
    """AC18 (R5), header rules at the brain: ``audio/mpeg`` is ``error
    invalid_frame`` (the message says "an accepted content_type") with
    nothing stored; a 960 044-byte segment passes under the default
    1 048 576 bound; ``size: 1 048 577`` is ``attachment_ack accepted:
    false``, ``attachment_too_large``, on the header alone. AC20: ``v == 1``."""

    assert len(SEGMENT_30S) == 960_044
    assert ATTACHMENT_CONTENT_TYPES == {"image/png", "image/jpeg", "audio/wav"}
    brain = Brain(
        actions=(AUDIO_CAPTURE,),
        specs=(audio_capture_spec(),),
        store_limits=AUDIO_STORE_LIMITS,
        authorization=audio_policy(),
    )
    try:
        await brain.activate()
        agent = brain.connect()
        welcome = await agent.pair_up(specs=(audio_capture_spec(),))
        assert welcome["limits"]["max_attachment_bytes"] == DEFAULT_BINARY_BOUND
        task = asyncio.create_task(brain.executor.invoke(audio_call(deadline=brain.clock() + 30)))
        call = await agent.call_frame()
        assert call["max_attachment_bytes"] == DEFAULT_BINARY_BOUND

        await agent.send({
            "type": FRAME_ATTACHMENT, "id": agent.session_id, "attachment_id": "mp3",
            "content_type": "audio/mpeg", "size": 4, "call_id": call["call_id"],
        })
        error = await agent.expect_error(PROXY_ERROR_INVALID_FRAME)
        assert "an accepted content_type" in error["message"]
        assert brain.store.object_count == 0
        assert not agent.ws.closed

        await agent.send({
            "type": FRAME_ATTACHMENT, "id": agent.session_id, "attachment_id": "huge",
            "content_type": "audio/wav", "size": DEFAULT_BINARY_BOUND + 1, "call_id": call["call_id"],
        })
        # Answered on the header alone: no binary frame was sent.
        ack = await agent.frame()
        assert ack["type"] == FRAME_ATTACHMENT_ACK and ack["attachment_id"] == "huge"
        assert ack["accepted"] is False and ack["code"] == ATTACHMENT_ACK_TOO_LARGE
        assert [m.type for m in agent.ws.sent].count(WSMsgType.BINARY) == 0
        assert brain.store.object_count == 0

        ack = await agent.transfer("long", SEGMENT_30S, content_type="audio/wav", call_id=call["call_id"])
        assert ack["accepted"] is True, ack
        assert brain.store.usage(RUN_ID).total_bytes == 960_044

        await agent.observe(
            call["call_id"], result=audio_capture_result(960_044),
            parts=[audio_ref("long", 960_044, transcription=TRANSCRIPTION)],
            provenance={"provider": "audio_input"},
        )
        observation = await task
        assert observation.status == "success", observation.error
        (part,) = observation.parts
        ref = brain.store.lookup(part["attachment_id"])
        assert ref is not None and ref.size == 960_044 and ref.content_type == "audio/wav"
        assert part["transcription"] == TRANSCRIPTION
        assert brain.store.object_count == 1
        assert_every_frame_is_v1(agent.pair)
    finally:
        await brain.close()


# --------------------------------------------------------------------------- #
# AC19 — no reference without an acknowledged upload; writes dropped
# --------------------------------------------------------------------------- #


async def test_ac19_an_audio_ref_naming_an_unacknowledged_attachment_is_refused_at_the_proxy() -> None:
    """AC19 (R5), brain side: an ``audio_ref`` naming an ``attachment_id``
    this session never acknowledged ends the call ``error
    attachment_refused`` (``retryable: false``) at the boundary; the upload
    the same call did make is discarded and 0 attachments stay leased."""

    brain = Brain(
        actions=(AUDIO_CAPTURE,),
        specs=(audio_capture_spec(),),
        store_limits=AUDIO_STORE_LIMITS,
        authorization=audio_policy(),
    )
    try:
        module = await brain.activate()
        agent = brain.connect()
        await agent.pair_up(specs=(audio_capture_spec(),))
        task = asyncio.create_task(brain.executor.invoke(audio_call(deadline=brain.clock() + 30)))
        call = await agent.call_frame()
        ack = await agent.transfer("real", SEGMENT_3S, content_type="audio/wav", call_id=call["call_id"])
        assert ack["accepted"] is True and brain.store.object_count == 1
        await agent.observe(
            call["call_id"], result=audio_capture_result(96_044),
            parts=[audio_ref("real", 96_044), audio_ref("never-sent", 96_044)],
        )
        observation = await task
        assert observation.status == "error"
        assert observation.error["code"] == ERROR_ATTACHMENT_REFUSED
        assert observation.error["retryable"] is False
        assert "never-sent" in observation.error["message"]
        assert observation.parts == ()
        assert brain.store.object_count == 0
        assert brain.store.usage(RUN_ID).objects == 0
        assert module.acknowledged_attachments == 0
        assert module.in_flight == ()
        assert_every_frame_is_v1(agent.pair)
    finally:
        await brain.close()


async def test_ac19_an_unacknowledged_audio_ref_on_a_write_is_external_unknown() -> None:
    """The write side of the §5.5 rule for ``audio_ref``: a forwarded
    ``audio.speak`` the agent did not report ``refused`` becomes
    ``external_unknown`` with the same ``attachment_refused`` code."""

    brain = Brain(
        actions=(AUDIO_SPEAK,),
        specs=(audio_speak_spec(),),
        store_limits=AUDIO_STORE_LIMITS,
        authorization=audio_policy(),
    )
    try:
        await brain.activate()
        agent = brain.connect()
        await agent.pair_up(specs=(audio_speak_spec(),))
        task = asyncio.create_task(brain.executor.invoke(
            audio_call("call-s1", action=AUDIO_SPEAK, arguments={"text": "salut"}, deadline=brain.clock() + 30)
        ))
        call = await agent.call_frame()
        await agent.observe(
            call["call_id"],
            result={"output": "main", "voice": "v", "duration_ms": 3000, "played_ms": 3000, "playback": "completed"},
            parts=[audio_ref("never-sent", 96_044)],
        )
        observation = await task
        assert observation.status == "external_unknown"
        assert observation.error["code"] == ERROR_ATTACHMENT_REFUSED
        assert observation.error["retryable"] is False
        assert brain.store.object_count == 0
    finally:
        await brain.close()


@pytest.mark.parametrize("code", [ATTACHMENT_ACK_TOO_LARGE, ATTACHMENT_ACK_STORE_FULL, ATTACHMENT_ACK_UNEXPECTED_BINARY])
async def test_ac19_the_agent_facing_a_refused_audio_transfer_returns_attachment_refused(code: str) -> None:
    """AC19 (R5), agent side: the agent transfers the ``audio_ref``'s
    segment before its observation; a refused ack turns the observation into
    ``error attachment_refused`` with no part — never a dangling reference —
    and the local segment is discarded."""

    host, provider = audio_agent_host(SEGMENT_3S)
    try:
        await host.activate()
        brain_end = host.connect()
        await brain_end.pair_up(actions=[AUDIO_CAPTURE])
        await brain_end.send(audio_call_frame(brain_end, "call-1", 1, max_attachment_bytes=DEFAULT_BINARY_BOUND))
        header, payload = await brain_end.receive_transfer()
        assert header["content_type"] == "audio/wav" and header["size"] == 96_044
        assert len(payload) == 96_044 and header["attachment_id"] == provider.stored[0]
        # The observation waits for the ack.
        await settle()
        assert FRAME_OBSERVATION not in brain_end.received_types()
        await brain_end.ack(header["attachment_id"], accepted=False, code=code)
        observation = await brain_end.observation("call-1")
        assert observation["status"] == "error"
        assert observation["error"]["code"] == ERROR_ATTACHMENT_REFUSED
        assert observation["error"]["retryable"] is False
        assert code in observation["error"]["message"]
        assert observation["parts"] == [] and "result" not in observation
        assert "audio_ref" not in json.dumps(observation)
        assert host.store.object_count == 0
        assert_every_frame_is_v1(brain_end.pair)
    finally:
        await host.close()


async def test_ac19_the_agent_sends_no_audio_above_the_calls_bound_and_refuses_the_observation() -> None:
    host, _ = audio_agent_host(SEGMENT_3S)
    try:
        await host.activate()
        brain_end = host.connect()
        await brain_end.pair_up(actions=[AUDIO_CAPTURE])
        await brain_end.send(audio_call_frame(brain_end, "call-1", 1, max_attachment_bytes=96_043))
        observation = await brain_end.observation("call-1")
        assert observation["status"] == "error"
        assert observation["error"]["code"] == ERROR_ATTACHMENT_REFUSED
        assert observation["parts"] == []
        assert FRAME_ATTACHMENT not in brain_end.received_types()
        assert host.store.object_count == 0

        # At the bound it is transferred, and the accepted reference is kept.
        await brain_end.send(audio_call_frame(brain_end, "call-2", 2, max_attachment_bytes=96_044))
        header, _ = await brain_end.receive_transfer()
        await brain_end.ack(header["attachment_id"])
        observation = await brain_end.observation("call-2")
        assert observation["status"] == "success"
        (part,) = observation["parts"]
        assert part["type"] == "audio_ref" and part["attachment_id"] == header["attachment_id"]
        assert part["transcription"] == TRANSCRIPTION
        assert host.store.object_count == 0
        assert_every_frame_is_v1(brain_end.pair)
    finally:
        await host.close()


async def test_ac19_a_forwarded_audio_speak_dropped_after_its_call_is_external_unknown_and_not_resent() -> None:
    """AC19 (R5): ``audio.speak``, declared in the harness catalog (the
    shipped ``audio_output`` manifest) and served through the proxy, whose
    connection drops after the ``call`` frame, resolves ``external_unknown``
    cause ``proxy_disconnected``; on reconnect nothing is retransmitted and
    its late ``observation`` is ignored and counted. AC20: ``v == 1``."""

    brain = Brain(
        actions=(AUDIO_SPEAK,),
        specs=(),
        catalog={
            "audio_output": _manifest(AUDIO_OUTPUT_MANIFEST),
            MODULE_NAME: _manifest(MANIFEST_PATH),
        },
        authorization=audio_policy(),
    )
    try:
        module = await brain.activate()
        assert brain.context.actions.discovered()[AUDIO_SPEAK] == audio_speak_spec()
        agent = brain.connect()
        welcome = await agent.pair_up(specs=(audio_speak_spec(),))
        assert welcome["actions"] == [AUDIO_SPEAK] and agent.mismatches == []
        task = asyncio.create_task(brain.executor.invoke(
            audio_call("call-speak", action=AUDIO_SPEAK, arguments={"text": "bonjour"}, deadline=brain.clock() + 30)
        ))
        call = await agent.call_frame()
        assert call["action_name"] == AUDIO_SPEAK and call["arguments"] == {"text": "bonjour"}

        agent.pair.drop()
        observation = await task
        assert observation.status == "external_unknown"
        assert observation.error["code"] == PROXY_ERROR_PROXY_DISCONNECTED
        assert observation.provenance["emission"] == "emitted"
        assert brain.executor.outcome("call-speak") == observation
        await wait_until(agent.handler.done)
        assert_every_frame_is_v1(agent.pair)

        again = brain.connect()
        await again.pair_up(nonce="h2", specs=(audio_speak_spec(),))
        await settle()
        assert again.received_types().count(FRAME_CALL) == 0
        late_before = module.late_observations
        await again.observe(
            "call-speak",
            result={"output": "main", "voice": "v", "duration_ms": 1000, "played_ms": 1000, "playback": "completed"},
            provenance={"provider": "audio_output"},
        )
        await wait_until(lambda: module.late_observations == late_before + 1)
        assert brain.executor.outcome("call-speak") == observation
        assert brain.executor.provider_invocations == 1
        assert again.received_types().count(FRAME_CALL) == 0
        assert_every_frame_is_v1(again.pair)
    finally:
        await brain.close()


# --------------------------------------------------------------------------- #
# AC20 — the protocol document's addendum; the vocabulary is unchanged
# --------------------------------------------------------------------------- #


def test_ac20_the_protocol_document_carries_the_phase_2_addendum(protocol_doc: str) -> None:
    heading = re.search(r"^## \d+\. Phase 2 addendum$", protocol_doc, re.MULTILINE)
    assert heading is not None, "docs/proxy-protocol.md lacks the Phase 2 addendum"
    addendum = protocol_doc[heading.start():]
    assert re.search(
        r"`attachment\.content_type ∈ \{image/png, image/jpeg,\s+audio/wav\}`", addendum
    ), "the addendum does not state the content-type set"
    for literal in ("image/png", "image/jpeg", "audio/wav"):
        assert literal in addendum
    assert "`audio_ref`" in addendum and "§5.5" in addendum and "§10" in addendum
    assert "attachment_refused" in addendum
    assert "`v` stays `1`" in addendum
    assert "decision 11" in addendum and "provider_not_ready" in addendum
    assert PROXY_PROTOCOL_VERSION == 1
    # The §5.7 row names the shared set and its three members.
    row = re.search(r"^\| `content_type`\s+\|.*$", protocol_doc, re.MULTILINE)
    assert row is not None
    assert "`ATTACHMENT_CONTENT_TYPES`" in row.group(0)
    for content_type in sorted(ATTACHMENT_CONTENT_TYPES):
        assert f"`{content_type}`" in row.group(0)


def test_ac20_the_frame_table_limits_and_codes_are_the_phase_1_ones(protocol_doc: str) -> None:
    """AC20: nothing but the content-type set and the ``audio_ref`` sentence
    changed — the frame types, the error/ack/close codes and the default
    frame bound are the phase 1 literals, in the constants and in the
    document's error-code table."""

    assert PROXY_FRAME_TYPES == {
        "hello", "welcome", "error", "call", "observation", "cancel",
        "attachment", "attachment_ack", "event", "ping", "pong",
    }
    phase1_codes = {
        "auth_failed", "agent_limit", "unsupported_protocol_version", "invalid_frame",
        "frame_too_large", "unknown_frame", "unknown_session", "duplicate_call_unknown",
        "action_mismatch", "proxy_disconnected", "event_not_allowed",
    }
    assert set(PROXY_ERROR_CODES) == phase1_codes
    section = protocol_doc[protocol_doc.index("## 7. Error codes"):protocol_doc.index("## 8. ")]
    table = {
        m.group(1)
        for m in re.finditer(r"^\| `([a-z_]+)`\s+\| (?:brain|agent|both)", section, re.MULTILINE)
    }
    assert table == phase1_codes
    assert set(_ACK_CODES) == {"attachment_too_large", "store_full", "unexpected_binary"}
    assert set(PROXY_CLOSE_CODES) == {4400, 4401, 4409, 4413}
    assert _MAX_FRAME == DEFAULT_BINARY_BOUND
    assert "twelve frame types" in protocol_doc
