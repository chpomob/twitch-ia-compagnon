"""Observation size and the phase-1 shared vocabulary (R4, AC47 arithmetic).

Created by step P1; the executor-side checks (lease, stored size, byte
bound) are added by P3 — exercised here through the shared
``runtime_context(attachments=...)`` every suite builds, with the one store
both the modules lease from and the executor validates against — the
model-side ones by P12, and the same lease rules through the **shipped**
capture module by P17 (the vertical harness of ``test_agentic_loop``: the
real provider leases into the one store the executor validates against and
the brain releases from).
"""

import pytest

from core import contracts
from core.actions import ERROR_INVALID_RESULT, AuthorizationPolicy, AuthorizationRule
from core.attachments import AttachmentStore
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
    WILDCARD,
    ActionCall,
    ActionObservation,
    ActionSpec,
    ContractError,
    Destination,
    observation_size,
)
from tests.conftest import ManualClock, runtime_context

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


def _transcription(text: str = "hello chat") -> dict:
    return {
        "text": text,
        "transcribed_at": 101.5,
        "provider_id": "audio_input",
        "truncated": False,
    }


def _audio(size: int, *, transcription: dict | None = None) -> dict:
    part = {
        "type": "audio_ref",
        "attachment_id": "att-audio-1",
        "content_type": "audio/wav",
        "size": size,
        "duration_ms": 3_000,
        "sample_rate_hz": 16_000,
        "channels": 1,
        "captured_at": 100.0,
        "provider_id": "audio_input",
    }
    if transcription is not None:
        part["transcription"] = transcription
    return part


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


def test_observation_size_adds_each_audio_ref_size() -> None:
    # AC13: a text part of 100 UTF-8 bytes plus an audio_ref of 96 044 bytes.
    assert observation_size([_text(100), _audio(96_044)]) == 96_144
    # Duration, rate, channels, timestamps and the transcription never count.
    heard = _audio(10, transcription=_transcription(text="x" * 5_000))
    heard["duration_ms"] = 10_000_000
    assert observation_size([heard]) == 10
    assert observation_size([_text(1), _image(2), _audio(3)]) == 6


def test_observation_size_refuses_an_unknown_part_rather_than_skipping_it() -> None:
    with pytest.raises(ContractError) as refused:
        observation_size([_text(1), {"type": "audio", "size": 10}])
    assert refused.value.field == "ActionObservation.parts[1].type"


# --------------------------------------------------------------------------- #
# The audio_ref part (phase 2 R4, AC13)
# --------------------------------------------------------------------------- #


def _field(refused: pytest.ExceptionInfo) -> str:
    return refused.value.field


@pytest.mark.parametrize("with_transcription", [False, True])
def test_audio_ref_with_the_eight_fields_is_accepted(with_transcription: bool) -> None:
    part = _audio(96_044, transcription=_transcription() if with_transcription else None)
    (frozen,) = contracts.validate_parts([part])
    assert frozen == part
    assert set(frozen) - {"type", "transcription"} == set(contracts.AUDIO_REF_FIELDS)
    with pytest.raises(TypeError):
        frozen["size"] = 1  # type: ignore[index]
    if with_transcription:
        # The nested mapping is frozen too, not shared with the caller.
        with pytest.raises(TypeError):
            frozen["transcription"]["text"] = "changed"  # type: ignore[index]
        part["transcription"]["text"] = "mutated after validation"
        assert frozen["transcription"]["text"] == "hello chat"
    observation = ActionObservation("success", {"p": 1}, None, None, [part])
    assert observation.parts[0]["type"] == "audio_ref"


def test_a_transcription_needs_only_text_and_transcribed_at() -> None:
    minimal = _audio(10, transcription={"text": "", "transcribed_at": 101})
    contracts.validate_parts([minimal])


def test_audio_ref_fields_are_the_eight_of_r4() -> None:
    assert contracts.AUDIO_REF_FIELDS == (
        "attachment_id",
        "content_type",
        "size",
        "duration_ms",
        "sample_rate_hz",
        "channels",
        "captured_at",
        "provider_id",
    )
    assert contracts.AUDIO_TRANSCRIPTION_FIELDS == (
        "text",
        "transcribed_at",
        "provider_id",
        "truncated",
    )
    assert contracts.AUDIO_CONTENT_TYPES == {"audio/wav"}
    assert contracts.ATTACHMENT_CONTENT_TYPES == {"image/png", "image/jpeg", "audio/wav"}
    assert contracts.PART_TYPE_AUDIO_REF == "audio_ref"


@pytest.mark.parametrize("missing", contracts.AUDIO_REF_FIELDS)
def test_audio_ref_missing_any_field_is_rejected_naming_it(missing: str) -> None:
    part = _audio(10)
    del part[missing]
    with pytest.raises(ContractError) as refused:
        contracts.validate_parts([part])
    assert _field(refused) == f"ActionObservation.parts[0].{missing}"


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("content_type", "audio/mpeg"),
        ("content_type", "image/png"),
        ("content_type", None),
        ("duration_ms", 0),
        ("duration_ms", -1),
        ("duration_ms", True),
        ("duration_ms", 1.5),
        ("size", 0),
        ("size", False),
        ("size", "96044"),
        ("sample_rate_hz", 0),
        ("sample_rate_hz", True),
        ("channels", -1),
        ("channels", True),
        ("captured_at", float("nan")),
        ("captured_at", float("inf")),
        ("captured_at", True),
        ("captured_at", "100"),
        ("attachment_id", ""),
        ("attachment_id", 7),
        ("provider_id", "  "),
        ("provider_id", None),
    ],
)
def test_audio_ref_mistyped_field_is_rejected_naming_it(name: str, value: object) -> None:
    part = _audio(10)
    part[name] = value
    with pytest.raises(ContractError) as refused:
        contracts.validate_parts([part])
    assert _field(refused) == f"ActionObservation.parts[0].{name}"


@pytest.mark.parametrize("unknown", ["width", "path", "data", "bytes"])
def test_audio_ref_unknown_key_is_rejected_naming_it(unknown: str) -> None:
    part = _audio(10)
    part[unknown] = 1
    with pytest.raises(ContractError) as refused:
        contracts.validate_parts([part])
    assert _field(refused) == f"ActionObservation.parts[0].{unknown}"


@pytest.mark.parametrize("missing", ["text", "transcribed_at"])
def test_transcription_missing_a_required_field_is_rejected_naming_it(missing: str) -> None:
    transcription = _transcription()
    del transcription[missing]
    with pytest.raises(ContractError) as refused:
        contracts.validate_parts([_audio(10, transcription=transcription)])
    assert _field(refused) == f"ActionObservation.parts[0].transcription.{missing}"


@pytest.mark.parametrize("value", ["hello", ["hello"], None, 3])
def test_non_mapping_transcription_is_rejected_naming_it(value: object) -> None:
    part = _audio(10)
    part["transcription"] = value
    with pytest.raises(ContractError) as refused:
        contracts.validate_parts([part])
    assert _field(refused) == "ActionObservation.parts[0].transcription"


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("text", None),
        ("text", 12),
        ("transcribed_at", float("nan")),
        ("transcribed_at", True),
        ("transcribed_at", "101"),
        ("provider_id", ""),
        ("provider_id", 1),
        ("truncated", "no"),
        ("truncated", 0),
        ("language", "fr"),
    ],
)
def test_transcription_mistyped_or_unknown_field_is_rejected_naming_it(
    name: str, value: object
) -> None:
    transcription = _transcription()
    transcription[name] = value
    with pytest.raises(ContractError) as refused:
        contracts.validate_parts([_audio(10, transcription=transcription)])
    assert _field(refused) == f"ActionObservation.parts[0].transcription.{name}"


def test_image_ref_rules_are_unchanged_by_the_audio_part() -> None:
    contracts.validate_parts([_image(10)])
    # An image_ref cannot borrow the audio content type or the audio fields.
    wav_image = _image(10)
    wav_image["content_type"] = "audio/wav"
    with pytest.raises(ContractError) as refused:
        contracts.validate_parts([wav_image])
    assert _field(refused) == "ActionObservation.parts[0].content_type"
    timed_image = _image(10)
    timed_image["duration_ms"] = 3_000
    with pytest.raises(ContractError) as refused:
        contracts.validate_parts([timed_image])
    assert _field(refused) == "ActionObservation.parts[0].duration_ms"


def test_audio_capability_name() -> None:
    assert contracts.CAPABILITY_AUDIO == "audio"
    assert contracts.CAPABILITY_STRUCTURED_OUTPUT == "structured_output"
    assert contracts.CAPABILITY_VISION == "vision"
    assert contracts.PROBE_REASON_AUDIO_REJECTED == "audio_rejected"
    assert contracts.PROBE_REASON_AUDIO_REJECTED in PROBE_REASONS
    for name in (
        "PART_TYPE_AUDIO_REF",
        "AUDIO_CONTENT_TYPES",
        "ATTACHMENT_CONTENT_TYPES",
        "AUDIO_REF_FIELDS",
        "AUDIO_TRANSCRIPTION_FIELDS",
        "CAPABILITY_AUDIO",
        "CAPABILITY_STRUCTURED_OUTPUT",
        "CAPABILITY_VISION",
        "PROBE_REASON_AUDIO_REJECTED",
    ):
        assert name in contracts.__all__, name


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
    """Phase 2 R4 supersedes the phase 1 seven-reason set: ``audio_rejected``
    joins it for the third (audio) probe."""

    assert PROBE_TOOL == "runtime.probe"
    assert PROBE_REASONS == {
        "non_success_status",
        "no_tool_call",
        "multiple_tool_calls",
        "malformed_arguments",
        "image_rejected",
        "audio_rejected",
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
    """``PART_TYPES`` gains ``audio_ref`` under phase 2 R4 (superseding the
    phase 1 two-member set); the ``image_ref`` vocabulary is unchanged."""

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
    assert contracts.PART_TYPES == {"text", "image_ref", "audio_ref"}


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


# --------------------------------------------------------------------------- #
# Executor-side validation through the shared runtime context (P3, AC22)
# --------------------------------------------------------------------------- #

PLATFORM = "fake-platform"
MODULE = "capture-module"
OPERATOR = "operator:companion"
CAPTURE_SCOPE = Destination(PLATFORM, WILDCARD, "screen")

CAPTURE_SPEC = ActionSpec(
    name="screen.capture",
    version=1,
    description="Capture the screen as an image reference.",
    argument_schema={"type": "object", "properties": {}, "additionalProperties": False},
    result_schema={
        "type": "object",
        "properties": {"content_type": {"type": "string"}},
        "required": ["content_type"],
    },
    nature="read",
    required_permissions=("screen.read",),
    supported_destinations=(CAPTURE_SCOPE,),
    timeout_seconds=5.0,
    idempotency="natural",
)

ATTACHMENT_LIMITS = {
    "max_object_bytes": 1024,
    "max_objects": 8,
    "max_total_bytes": 8192,
    "max_bytes_per_run": 4096,
    "ttl_seconds": 30.0,
}


class CaptureProvider:
    """Answers each call with the parts the test scripted, in order."""

    name = "capture"

    def __init__(self) -> None:
        self.scripted: list[tuple] = []
        self.calls: list = []

    async def invoke(self, invocation):
        self.calls.append(invocation)
        parts = self.scripted.pop(0)
        return ActionObservation(
            status="success",
            provenance={"transport": "fake"},
            result={"content_type": "image/png"},
            parts=parts,
        )


def _image_part(ref, **overrides) -> dict:
    part = {
        "type": "image_ref",
        "attachment_id": ref.attachment_id,
        "content_type": ref.content_type,
        "size": ref.size,
        "width": 640,
        "height": 480,
        "captured_at": ref.created_at,
        "provider_id": "capture",
    }
    part.update(overrides)
    return part


def _capture_call(run_id: str, call_id: str, deadline: float) -> ActionCall:
    return ActionCall(
        action_name=CAPTURE_SPEC.name,
        action_version=1,
        arguments={},
        conversation_id="conv-1",
        run_id=run_id,
        call_id=call_id,
        source_event_id="evt-1",
        destination=Destination(PLATFORM, "room-1", "screen"),
        principal=OPERATOR,
        deadline=deadline,
    )


def _context_with_store():
    """The shared fixture with a store on its clock, one ready capture provider."""

    clock = ManualClock()
    store = AttachmentStore(clock=clock, **ATTACHMENT_LIMITS)
    policy = AuthorizationPolicy(
        [
            AuthorizationRule(
                rule_id="capture",
                action_name=CAPTURE_SPEC.name,
                destination=CAPTURE_SCOPE,
                principals=(OPERATOR,),
                natures=("read",),
                granted_permissions=("screen.read",),
            )
        ]
    )
    context = runtime_context(clock=clock, attachments=store, authorization=policy)
    provider = CaptureProvider()
    context.actions.declare(CAPTURE_SPEC, module=MODULE)
    context.actions.bind(CAPTURE_SPEC.name, provider, module=MODULE)
    context.actions.mark_ready(MODULE)
    return context, clock, store, provider


async def test_the_shared_context_carries_one_store_for_modules_and_executor() -> None:
    """P3: ``runtime_context(attachments=...)`` hands the same store to both."""

    context, clock, store, provider = _context_with_store()
    assert context.attachments is store

    # A module leases through the context; the executor validates against it.
    ref = context.attachments.put("run-1", b"\x00" * 512, content_type="image/png")
    provider.scripted.append([_image_part(ref)])
    observation = await context.executor.invoke(_capture_call("run-1", "run-1/call-1", clock() + 5))

    assert observation.status == "success"
    assert observation.parts == (_image_part(ref),)
    assert store.usage("run-1").objects == 1


async def test_an_image_leased_to_another_run_is_invalid_result_through_the_context() -> None:
    """AC22 through the shared fixture: leased to another run, or expired on
    the injected clock, the observation is ``invalid_result`` and the store
    holds 0 objects of the rejecting run before any model request could
    carry the image.

    The object leased to the other run is refused, not destroyed: its
    owner may still need it for its next model turn, and only that run's
    own cleanup releases it (gate finding F1 — this test used to assert
    the whole store became empty).
    """

    context, clock, store, provider = _context_with_store()

    foreign = store.put("run-2", b"\x7f" * 256, content_type="image/png")
    provider.scripted.append([_image_part(foreign)])
    rejected = await context.executor.invoke(_capture_call("run-1", "run-1/call-1", clock() + 5))
    assert rejected.status == "error"
    assert rejected.error["code"] == ERROR_INVALID_RESULT
    assert rejected.parts == ()
    assert rejected.result is None
    assert store.usage("run-1").objects == 0
    assert store.lookup(foreign.attachment_id) == foreign
    assert store.get(foreign) == b"\x7f" * 256
    assert store.usage("run-2").objects == 1
    # The owner's cleanup is what releases it.
    assert store.release("run-2").objects == 1
    assert store.object_count == 0

    stale = store.put("run-1", b"\x00" * 256, content_type="image/png")
    clock.advance(ATTACHMENT_LIMITS["ttl_seconds"])
    provider.scripted.append([_image_part(stale)])
    expired = await context.executor.invoke(_capture_call("run-1", "run-1/call-2", clock() + 5))
    assert expired.status == "error"
    assert expired.error["code"] == ERROR_INVALID_RESULT
    assert store.object_count == 0
    assert len(provider.calls) == 2


async def test_a_stored_size_off_by_one_byte_is_invalid_result_through_the_context() -> None:
    """AC47 tail through the shared fixture."""

    context, clock, store, provider = _context_with_store()
    ref = store.put("run-1", b"\x00" * 300, content_type="image/jpeg")
    provider.scripted.append([_image_part(ref, size=301)])

    observation = await context.executor.invoke(_capture_call("run-1", "run-1/call-1", clock() + 5))

    assert observation.error["code"] == ERROR_INVALID_RESULT
    assert store.usage("run-1").objects == 0
    assert store.release("run-1").objects == 0


# --------------------------------------------------------------------------- #
# The two store accessors the executor relies on (P3)
# --------------------------------------------------------------------------- #


def test_lookup_reads_the_lease_without_touching_its_time_to_live() -> None:
    """The store's lookup neither extends nor reaps a lease.

    A reference past its deadline that the reaper has not reached is returned
    as stored — its ``expires_at`` tells the caller it expired — and looking
    it up again returns it still: no side effect on the store. The next
    reaping accessor drops it, after which lookup reads ``None``.
    """

    clock = ManualClock()
    store = AttachmentStore(clock=clock, **ATTACHMENT_LIMITS)
    ref = store.put("run-1", b"\x00" * 16, content_type="image/png")

    assert store.lookup(ref.attachment_id) == ref
    clock.advance(10.0)
    assert store.lookup(ref.attachment_id) == ref  # unchanged: not refreshed
    assert store.lookup(ref.attachment_id).expires_at == ref.expires_at

    clock.advance(ATTACHMENT_LIMITS["ttl_seconds"])
    stale = store.lookup(ref.attachment_id)
    assert stale == ref and stale.expires_at <= clock()
    assert store.lookup(ref.attachment_id) == ref  # still there: lookup reaps nothing

    assert store.usage("run-1").objects == 0  # a reaping accessor
    assert store.lookup(ref.attachment_id) is None
    assert store.lookup("never-issued") is None
    with pytest.raises(ContractError):
        store.lookup("")


def test_discard_drops_one_object_and_is_idempotent_with_release() -> None:
    clock = ManualClock()
    store = AttachmentStore(clock=clock, **ATTACHMENT_LIMITS)
    first = store.put("run-1", b"\x00" * 16, content_type="image/png")
    second = store.put("run-1", b"\x00" * 32, content_type="image/png")
    other = store.put("run-2", b"\x00" * 8, content_type="image/png")

    assert store.discard(first.attachment_id) is True
    assert store.discard(first.attachment_id) is False  # already gone: no-op
    assert store.discard("never-issued") is False
    assert store.lookup(first.attachment_id) is None
    assert store.lookup(second.attachment_id) == second  # only the named one
    assert store.usage("run-1").objects == 1
    assert store.usage("run-1").total_bytes == 32
    assert store.usage("run-2").objects == 1
    assert store.total_bytes == 40

    # Run-end release frees what is left, and nothing twice.
    freed = store.release("run-1")
    assert freed.objects == 1 and freed.total_bytes == 32
    assert store.total_bytes == 8
    assert store.discard(other.attachment_id) is True
    assert store.total_bytes == 0 and store.object_count == 0
    assert store.release("run-2").objects == 0


# --------------------------------------------------------------------------- #
# Expired and foreign leases through the whole brain path (P12; AC22, AC24)
# --------------------------------------------------------------------------- #

# The agentic-loop harness: the shipped brain on the real executor, scheduler
# and store, with the capture double leasing into the run's store.
from test_agentic_loop import (  # noqa: E402
    SCREEN_CAPTURE,
    STORE_LIMITS,
    activate_loop,
    image_parts,
    observation_envelope,
    tool_messages,
)
from conftest import final, tool_call  # noqa: E402


async def test_a_foreign_lease_is_invalid_result_for_the_model_and_the_run_goes_on() -> None:
    """AC22 through the brain: a capture returning an ``image_ref`` leased to
    another run is ``invalid_result`` at the executor; the model receives
    that error observation, the request that follows carries 0 image
    parts and the run ends ``success`` on the scripted final response.

    The foreign object is refused, not discarded: it stays leased to its
    owner with its bytes readable, and this run's cleanup releases nothing
    of it (gate finding F1 — this test used to assert the store was empty
    and the owner's usage 0)."""

    harness = await activate_loop(tool_call(SCREEN_CAPTURE, {}), final("Nothing to see."))
    harness.capture.lease_run = "another-run"
    try:
        record = await harness.ask()
        assert record.status == "success"
        rejected = harness.executor.outcome(f"{record.run_id}/call-1")
        assert rejected.status == "error"
        assert rejected.error["code"] == ERROR_INVALID_RESULT
        assert rejected.parts == ()
        (result,) = tool_messages(harness.requests()[1])
        assert observation_envelope(result) == {"status": "error", "error": {"code": ERROR_INVALID_RESULT}}
        assert image_parts(harness.requests()[1]) == []
        assert harness.observations()[0]["parts"] == []
        # The run that rejected holds nothing; the owner keeps its image.
        assert harness.store.usage(record.run_id).objects == 0
        (foreign,) = harness.capture.refs
        assert foreign.run_id == "another-run"
        assert harness.store.lookup(foreign.attachment_id) == foreign
        assert harness.store.get(foreign) == harness.capture.data
        assert harness.store.usage("another-run").objects == 1
        assert harness.store.object_count == 1
        assert harness.store.release("another-run").objects == 1
    finally:
        await harness.close()


async def test_a_lease_expired_at_validation_is_invalid_result_and_one_expired_later_ends_the_run() -> None:
    """AC22 and AC24 through the brain, told apart by *when* the lease
    expires. Expired on the injected clock before the executor validates
    the observation (the capture held across the time-to-live): the
    executor's ``invalid_result`` reaches the model, the next request has 0
    image parts and the run goes on. Expired after the observation was
    adopted and before the model turn that would use it: the brain's own
    lookup ends the run ``error`` / ``attachment_expired`` with 0 further
    requests, nothing sent stale."""

    import asyncio

    # A time-to-live shorter than ``action_seconds``, so the lease expires
    # while the capture is still inside its deadline.
    short_lived = {**STORE_LIMITS, "ttl_seconds": 5.0}
    early = await activate_loop(
        tool_call(SCREEN_CAPTURE, {}), final("Nothing to see."), store_limits=short_lived
    )
    early.capture.hold = asyncio.get_running_loop().create_future()
    try:
        await early.send()
        await asyncio.wait_for(early.capture.entered.wait(), 1)
        assert early.store.object_count == 1
        early.clock.advance(short_lived["ttl_seconds"] + 1.0)
        early.capture.hold.set_result(None)
        record = await early.completed()
        observation = early.executor.outcome(f"{record.run_id}/call-1")
        assert observation.status == "error"
        assert observation.error["code"] == ERROR_INVALID_RESULT
        assert observation.parts == ()
        (result,) = tool_messages(early.requests()[1])
        assert observation_envelope(result) == {"status": "error", "error": {"code": ERROR_INVALID_RESULT}}
        assert image_parts(early.requests()[1]) == []
        assert record.status == "success"
        assert early.store.object_count == 0
    finally:
        await early.close()

    late = await activate_loop(tool_call(SCREEN_CAPTURE, {}), final("never"))
    late.bus.subscribe(
        contracts.TRACE_ACTION_COMPLETED,
        lambda event: late.clock.advance(STORE_LIMITS["ttl_seconds"] + 1.0)
        if event["payload"]["action"] == SCREEN_CAPTURE
        else None,
    )
    try:
        record = await late.ask()
        assert record.status == "error"
        assert late.run_completed()["failure"] == contracts.BRAIN_ERROR_ATTACHMENT_EXPIRED
        assert len(late.requests()) == 1
        assert late.executor.outcome(f"{record.run_id}/call-1").status == "success"
        assert late.store.object_count == 0
        assert late.sender.sends == []
    finally:
        await late.close()


# --------------------------------------------------------------------------- #
# The real capture provider through the whole brain path (P17; AC2, AC24, AC25)
# --------------------------------------------------------------------------- #

from pathlib import Path  # noqa: E402

from test_agentic_loop import (  # noqa: E402
    CAPTURE_PROVIDER,
    IMAGE_HEIGHT,
    IMAGE_WIDTH,
    PNG,
    SnapshotModel,
    activate_vertical,
    vertical_modules,  # noqa: F401  (the session-scoped modules_directory fixture)
)


async def test_the_real_capture_provider_leases_into_the_one_store_the_executor_validates(
    vertical_modules: Path,
) -> None:
    """P17 (R4, R5): the shipped capture module — loaded by the real loader
    beside the shipped brain — leases its image into the same store the
    executor validates every ``image_ref`` against: the part the model
    receives names an attachment leased under the run, of exactly the
    stored size and the sniffed dimensions, held by the store at the
    request that carries the image (1 object) and by nobody once
    ``brain.run.completed`` is published (0 objects)."""

    session = SnapshotModel(tool_call(SCREEN_CAPTURE, {}), final("Nothing to see."))
    harness = await activate_vertical(session=session, modules_directory=vertical_modules)
    objects_at_request: list[int] = []
    session.on_request = lambda body: objects_at_request.append(harness.store.object_count)
    try:
        record = await harness.ask()
        assert record.status == "success"
        assert objects_at_request == [0, 1]
        observation = harness.executor.outcome(f"{record.run_id}/call-1")
        assert observation.status == "success"
        (part,) = observation.parts
        assert part["provider_id"] == CAPTURE_PROVIDER
        assert (part["size"], part["width"], part["height"]) == (len(PNG), IMAGE_WIDTH, IMAGE_HEIGHT)
        assert len(image_parts(harness.requests()[1])) == 1
        assert harness.observations()[0]["parts"] == [
            {
                "type": "image_ref",
                "attachment_id": part["attachment_id"],
                "content_type": "image/png",
                "size": len(PNG),
                "width": IMAGE_WIDTH,
                "height": IMAGE_HEIGHT,
            }
        ]
        assert harness.store.lookup(part["attachment_id"]) is None
        assert harness.store.usage(record.run_id).objects == 0
        assert harness.store.object_count == 0
    finally:
        await harness.close()


async def test_a_real_lease_expired_after_adoption_ends_the_run_with_nothing_sent(
    vertical_modules: Path,
) -> None:
    """AC24 through the shipped capture module: the lease it made expires
    on the injected clock after the executor adopted the observation and
    before the model turn that would carry it — the brain's own lookup ends
    the run ``error`` / ``attachment_expired`` with 1 model request, 0
    image parts anywhere, 0 sends at the platform and the store at 0."""

    harness = await activate_vertical(
        tool_call(SCREEN_CAPTURE, {}), final("never"), modules_directory=vertical_modules
    )
    harness.bus.subscribe(
        contracts.TRACE_ACTION_COMPLETED,
        lambda event: harness.clock.advance(STORE_LIMITS["ttl_seconds"] + 1.0)
        if event["payload"]["action"] == SCREEN_CAPTURE
        else None,
    )
    try:
        record = await harness.ask()
        assert record.status == "error"
        assert harness.run_completed()["failure"] == contracts.BRAIN_ERROR_ATTACHMENT_EXPIRED
        assert len(harness.requests()) == 1
        assert image_parts(harness.requests()[0]) == []
        assert harness.executor.outcome(f"{record.run_id}/call-1").status == "success"
        assert harness.store.usage(record.run_id).objects == 0
        assert harness.store.object_count == 0
        assert harness.sends() == []
        assert harness.context.tasks.active == harness.tasks_before
    finally:
        await harness.close()
