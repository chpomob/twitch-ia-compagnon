"""Observation size and the phase-1 shared vocabulary (R4, AC47 arithmetic).

Created by step P1; the executor-side checks (lease, stored size, byte
bound) are added by P3 and the model-side ones by P12.
"""

import pytest

from core import contracts
from core.contracts import (
    ATTACHMENT_ACK_CODES,
    BRAIN_ERROR_CODES,
    DELIVERY_RESOLUTION_REASONS,
    IMAGE_REF_FIELDS,
    PROBE_REASONS,
    PROBE_TOOL,
    PROXY_CLOSE_CODES,
    PROXY_DEFAULT_MAX_FRAME_BYTES,
    PROXY_ERROR_CODES,
    PROXY_FRAME_TYPES,
    PROXY_PROTOCOL_VERSION,
    RUN_FAILURES,
    ActionObservation,
    ContractError,
    observation_size,
)

MAX_OBSERVATION_BYTES = 5_242_880
"""The default ``budget.max_observation_bytes`` of R3, used by AC47."""


def _text(size: int) -> dict:
    return {"type": "text", "text": "a" * size}


def _image(size: int) -> dict:
    return {
        "type": "image_ref",
        "attachment_id": "att-1",
        "content_type": "image/jpeg",
        "size": size,
        "width": 1920,
        "height": 1080,
        "captured_at": 100.0,
        "provider_id": "capture",
    }


# --------------------------------------------------------------------------- #
# observation_size (R4, AC47)
# --------------------------------------------------------------------------- #


def test_observation_size_is_text_bytes_plus_image_size_only() -> None:
    assert observation_size([]) == 0
    assert observation_size([_text(10)]) == 10
    assert observation_size([_image(1024)]) == 1024
    assert observation_size([_text(10), _image(1024), _text(5)]) == 1039

    # UTF-8 bytes, not code points: "é" is two bytes.
    assert observation_size([{"type": "text", "text": "éé"}]) == 4

    # Dimensions, timestamps and ids are metadata and never count.
    big_metadata = _image(1)
    big_metadata["width"] = 100_000
    big_metadata["height"] = 100_000
    big_metadata["captured_at"] = 1e12
    assert observation_size([big_metadata]) == 1


def test_observation_size_ignores_envelope_and_result() -> None:
    observation = ActionObservation(
        "success",
        {"provider_id": "capture", "module": "capture", "version": 3},
        {"source": "primary", "content_type": "image/jpeg", "width": 1920, "height": 1080},
        None,
        [_image(2048)],
    )
    assert observation_size(observation.parts) == 2048


def test_ac47_arithmetic_against_the_default_bound() -> None:
    # Each part alone is under the bound; together they exceed it.
    over = [_text(3_145_728), _image(3_145_728)]
    assert observation_size(over) == 6_291_456
    assert observation_size(over) > MAX_OBSERVATION_BYTES

    # Exactly at the bound is accepted (the bound is inclusive).
    exact = [_text(3_145_728), _image(2_097_152)]
    assert observation_size(exact) == 5_242_880
    assert observation_size(exact) <= MAX_OBSERVATION_BYTES

    # The same parts construct as an observation and keep their size.
    assert observation_size(ActionObservation("success", {"p": 1}, None, None, exact).parts) == (
        MAX_OBSERVATION_BYTES
    )


def test_observation_size_refuses_an_unknown_part_rather_than_skipping_it() -> None:
    with pytest.raises(ContractError) as refused:
        observation_size([_text(1), {"type": "audio", "size": 10}])
    assert refused.value.field == "ActionObservation.parts[1].type"


# --------------------------------------------------------------------------- #
# Shared vocabulary (R1–R4, R6)
# --------------------------------------------------------------------------- #


def test_brain_synthetic_codes_and_run_failures_have_the_spec_literals() -> None:
    assert BRAIN_ERROR_CODES == {
        "not_a_read_action",
        "unknown_action",
        "malformed_arguments",
        "observation_too_large",
        "attachment_refused",
        "attachment_expired",
        "invalid_result",
    }
    assert contracts.BRAIN_ERROR_NOT_A_READ_ACTION == "not_a_read_action"
    assert contracts.BRAIN_ERROR_OBSERVATION_TOO_LARGE == "observation_too_large"
    # The two reused codes spell the executor's literals exactly.
    from core.actions import ERROR_INVALID_RESULT, ERROR_UNKNOWN_ACTION

    assert contracts.BRAIN_ERROR_UNKNOWN_ACTION == ERROR_UNKNOWN_ACTION
    assert contracts.BRAIN_ERROR_INVALID_RESULT == ERROR_INVALID_RESULT

    assert RUN_FAILURES == {"unsupported_response_shape", "capability_missing", "budget_exhausted"}
    assert contracts.RUN_FAILURE_BUDGET_EXHAUSTED == "budget_exhausted"


def test_probe_tool_and_reasons() -> None:
    assert PROBE_TOOL == "runtime.probe"
    assert PROBE_REASONS == {
        "non_success_status",
        "no_tool_call",
        "multiple_tool_calls",
        "malformed_arguments",
        "image_rejected",
        "timed_out",
        "transport_failed",
    }


def test_delivery_resolution_reasons() -> None:
    assert DELIVERY_RESOLUTION_REASONS == {
        "unknown_action",
        "not_a_delivery",
        "text_mapping_missing",
        "text_mapping_ambiguous",
        "empty",
    }


def test_image_ref_fields_are_the_seven_of_r4() -> None:
    assert IMAGE_REF_FIELDS == (
        "attachment_id",
        "content_type",
        "size",
        "width",
        "height",
        "captured_at",
        "provider_id",
    )
    assert contracts.IMAGE_CONTENT_TYPES == {"image/png", "image/jpeg"}
    assert contracts.PART_TYPES == {"text", "image_ref"}


def test_proxy_protocol_v1_vocabulary() -> None:
    assert PROXY_PROTOCOL_VERSION == 1
    assert PROXY_DEFAULT_MAX_FRAME_BYTES == 1_048_576

    assert PROXY_FRAME_TYPES == {
        "hello",
        "welcome",
        "error",
        "call",
        "observation",
        "cancel",
        "attachment",
        "attachment_ack",
        "event",
        "ping",
        "pong",
    }
    # Session- and call-scoped frames partition the post-welcome frames;
    # ``hello``/``welcome``/``error`` correlate by nonce or by echoed id.
    assert contracts.PROXY_SESSION_FRAME_TYPES == {
        "ping",
        "pong",
        "event",
        "attachment",
        "attachment_ack",
    }
    assert contracts.PROXY_CALL_FRAME_TYPES == {"call", "observation", "cancel"}
    assert not (contracts.PROXY_SESSION_FRAME_TYPES & contracts.PROXY_CALL_FRAME_TYPES)
    assert contracts.PROXY_SESSION_FRAME_TYPES | contracts.PROXY_CALL_FRAME_TYPES | {
        "hello",
        "welcome",
        "error",
    } == PROXY_FRAME_TYPES

    assert PROXY_ERROR_CODES == {
        "auth_failed",
        "agent_limit",
        "unsupported_protocol_version",
        "frame_too_large",
        "unknown_frame",
        "unknown_session",
        "invalid_frame",
        "duplicate_call_unknown",
        "action_mismatch",
        "proxy_disconnected",
        "event_not_allowed",
    }
    assert ATTACHMENT_ACK_CODES == {"attachment_too_large", "store_full", "unexpected_binary"}


def test_proxy_close_codes_are_the_spec_values_and_distinct() -> None:
    codes = (
        contracts.PROXY_CLOSE_BAD_REQUEST,
        contracts.PROXY_CLOSE_AUTH_FAILED,
        contracts.PROXY_CLOSE_AGENT_LIMIT,
        contracts.PROXY_CLOSE_FRAME_TOO_LARGE,
    )
    assert codes == (4400, 4401, 4409, 4413)
    assert len(set(codes)) == len(codes)
    assert PROXY_CLOSE_CODES == set(codes)
    # Private-use range of the WebSocket close-code registry.
    assert all(4000 <= code <= 4999 for code in codes)


def test_vocabulary_is_exported_and_carries_no_platform_or_vendor_name() -> None:
    names = [name for name in contracts.__all__ if name.isupper()]
    for name in names:
        assert hasattr(contracts, name), name
    values = []
    for name in names:
        value = getattr(contracts, name)
        values.extend(value if isinstance(value, (frozenset, tuple)) else [value])
    text = " ".join(str(value) for value in values).lower()
    for forbidden in ("twitch", "gpt", "openai", "claude", "anthropic"):
        assert forbidden not in text
