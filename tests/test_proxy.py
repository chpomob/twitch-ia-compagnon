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
