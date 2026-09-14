"""The brain's model adapter (R2; AC8, AC9, AC12).

One Chat Completions adapter: the offered actions travel as tool
definitions, the answer is classified per decision 2 and never guessed, the
required backend capabilities are verified by a bounded probe at ``prepare``
before the readiness barrier (decision 3), and an ``image_ref`` part is read
from the attachment store and encoded **only** while the request body is
built — the encoded bytes exist in the request body and nowhere else (AC12).

The probe runs through the shared ``FakeSession`` of ``conftest``, which
tells a probe from a scenario request by its body; ``Harness.requests()``
counts scenario requests only. Nothing here sleeps: the probe's time bound
runs on the injected clock.
"""

from __future__ import annotations

import asyncio
import base64
import json
from typing import Any

import pytest

from core.attachments import AttachmentStore
from core.contracts import (
    PROBE_TOOL,
    TRACE_MODULE_DEGRADED,
    TRACE_MODULE_READY,
    ActionSpec,
    Destination,
)
from conftest import (
    FakeResponse,
    FakeSession,
    ManualClock,
    carries_image_part,
    events_of,
    final,
    is_probe_request,
    settle,
    text_and_tool_call,
    tool_call,
    tool_calls,
)
from modules.brain import (
    BRAIN_ERROR_NOT_A_READ_ACTION,
    MODULE_NAME,
    BrainModuleError,
    _Final,
    _ModelAdapter,
    _Prompt,
    _Proposal,
    _Settings,
    _Unsupported,
)
from test_brain import (
    CHAT_WRITE_SPEC,
    LIMITS,
    SETTINGS,
    VALID_SETTINGS,
    activate_with,
    assert_sanitized,
    completion,
    merge_settings,
)


API_KEY = SETTINGS["api_key"]
ENDPOINT = SETTINGS["endpoint"]
MODEL_CALL_SECONDS = LIMITS["budget"]["model_call_seconds"]

STRUCTURED_ONLY = {"capabilities": {"required": ["structured_output"]}}
WITH_VISION = {"capabilities": {"required": ["structured_output", "vision"]}}

# A read action offered as a tool (the offered specs are whatever the run
# body hands the adapter; a spec is rendered by name, description, schema).
CHAT_READ_SPEC = ActionSpec(
    name="chat.read",
    version=1,
    description="Read the last chat messages of the channel.",
    argument_schema={
        "type": "object",
        "properties": {"limit": {"type": "integer", "minimum": 1}},
        "required": [],
        "additionalProperties": False,
    },
    result_schema={"type": "object", "properties": {"messages": {"type": "array"}}},
    nature="read",
    required_permissions=("chat.read",),
    supported_destinations=(Destination("twitch", "*", "chat"),),
    timeout_seconds=5.0,
    idempotency="none",
)

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753"
    "de0000000c49444154789c63606060000000040001f61738550000000049454e"
    "44ae426082"
)


def image_parts(body: dict[str, Any]) -> list[dict[str, Any]]:
    parts = []
    for message in body["messages"]:
        content = message.get("content")
        if isinstance(content, list):
            parts.extend(part for part in content if part.get("type") == "image_url")
    return parts


def not_verified(capability: str, reason: str) -> str:
    return f"module '{MODULE_NAME}': backend capability '{capability}' not verified: {reason}"


async def prepare_fails(harness: Any) -> BrainModuleError:
    with pytest.raises(BrainModuleError) as raised:
        await harness.handle.prepare()
    return raised.value


def assert_no_secret(rendered: str) -> None:
    assert API_KEY not in rendered
    assert ENDPOINT not in rendered


def traces_rendered(harness: Any) -> str:
    return "\n".join(json.dumps(event, default=str) for event in harness.bus.list_events())


def assert_not_ready(harness: Any, message: str) -> None:
    """The failed probe left the module outside the barrier, value-free."""

    assert not harness.context.actions.is_ready(MODULE_NAME)
    assert harness.handle.verified_capabilities == frozenset()
    assert harness.requests() == []
    assert_no_secret(message)
    assert_sanitized(harness.diagnostics)
    assert_no_secret(traces_rendered(harness))
    (degraded,) = events_of(harness.bus, TRACE_MODULE_DEGRADED)
    assert degraded["payload"]["module"] == MODULE_NAME
    assert degraded["payload"]["reason"] == message
    assert events_of(harness.bus, TRACE_MODULE_READY) == []


class HeldProbeSession(FakeSession):
    """A model transport that holds every probe until the test releases it."""

    def __init__(self, *results: Any) -> None:
        super().__init__(*results)
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def post(self, url: str, **kwargs: Any) -> Any:
        if is_probe_request(kwargs.get("json")):
            self.probe_calls.append({"url": url, **kwargs})
            self.entered.set()
            await self.release.wait()
            return self._next_probe_result()
        return await super().post(url, **kwargs)


class BlockingResponse(FakeResponse):
    """A 2xx response whose body read blocks until the test releases it."""

    def __init__(self, body: Any) -> None:
        super().__init__(200, body)
        self.reading = asyncio.Event()
        self.unblock = asyncio.Event()

    async def json(self) -> Any:
        self.reading.set()
        await self.unblock.wait()
        return self.body


# --------------------------------------------------------------------------- #
# AC8: the two probes, the readiness barrier, the exact diagnostic
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_ac8_vision_probe_non_success_stops_prepare_with_the_exact_diagnostic() -> None:
    """AC8 (R2): the first probe answers one tool call, the second a non-2xx
    status; ``prepare`` fails with exactly the diagnostic, which carries
    neither the key nor the endpoint (the 5xx body carries both); 0
    scenario requests; the module is not ready and reported ``degraded``."""

    session = FakeSession(
        FakeResponse(200, completion("never sent")),
        probe_results=[
            FakeResponse(200, tool_call(PROBE_TOOL, {"ok": True})),
            FakeResponse(503, {"error": {"message": f"{API_KEY} at {ENDPOINT}"}}),
        ],
    )
    harness = await activate_with(session=session, settings_overrides=WITH_VISION, prepare=False)
    try:
        failure = await prepare_fails(harness)

        assert str(failure) == not_verified("vision", "non_success_status")
        assert len(harness.probes()) == 2
        assert_not_ready(harness, str(failure))

        # Not ready is not routed: a published message reaches no run.
        await harness.send()
        await settle()
        assert harness.requests() == []
        assert harness.records() == []
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_ac8_both_probes_verified_make_the_module_ready_with_exactly_two_probes() -> None:
    """AC8 (R2): both probes answered with one forced tool call — the
    module is ready past the barrier, exactly 2 probe requests were made,
    the first with no image part and the second with exactly one, both
    forcing the probe tool through ``tool_choice`` and offering it alone."""

    harness = await activate_with(
        FakeResponse(200, completion("Hello")), settings_overrides=WITH_VISION
    )
    try:
        assert harness.context.actions.is_ready(MODULE_NAME)
        assert harness.handle.verified_capabilities == {"structured_output", "vision"}
        assert len(harness.probes()) == 2
        text_probe, image_probe = (call["json"] for call in harness.probes())
        for probe in (text_probe, image_probe):
            assert probe["model"] == SETTINGS["model"]
            assert probe["tool_choice"] == {"type": "function", "function": {"name": PROBE_TOOL}}
            assert [tool["function"]["name"] for tool in probe["tools"]] == [PROBE_TOOL]
            assert probe["tools"][0]["function"]["parameters"]["type"] == "object"
            assert isinstance(probe["max_tokens"], int) and probe["max_tokens"] > 0
        for call in harness.probes():
            assert call["url"] == ENDPOINT
            assert call["headers"]["Authorization"] == f"Bearer {API_KEY}"
        assert not carries_image_part(text_probe)
        assert image_parts(text_probe) == []
        (image,) = image_parts(image_probe)
        assert image == {
            "type": "image_url",
            "image_url": {"url": "data:image/png;base64," + base64.b64encode(PNG).decode()},
        }
        assert harness.requests() == []
        assert harness.diagnostics == []
        assert events_of(harness.bus, TRACE_MODULE_DEGRADED) == []

        # The probe is per activation, never per run.
        await harness.send()
        (record,) = await harness.completed()
        assert record.status == "success"
        assert len(harness.requests()) == 1
        assert len(harness.probes()) == 2
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_prepare_probes_once_and_a_repeated_prepare_sends_nothing() -> None:
    """``prepare`` is idempotent: a second call neither probes nor re-marks."""

    harness = await activate_with(settings_overrides=STRUCTURED_ONLY)
    try:
        assert len(harness.probes()) == 1
        await harness.handle.prepare()
        assert len(harness.probes()) == 1
    finally:
        await harness.close()


# --------------------------------------------------------------------------- #
# AC9 and the other reasons
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_ac9_plain_text_refuses_structured_output_with_no_tool_call() -> None:
    """AC9 (R2): a probe answered with prose is ``no_tool_call`` — a backend
    that ignored the forced ``tool_choice`` is refused, not accepted as
    text-only; with ``[structured_output]`` exactly 1 probe was sent."""

    session = FakeSession(probe_results=[FakeResponse(200, final(f"Sure! {API_KEY}"))])
    harness = await activate_with(
        session=session, settings_overrides=STRUCTURED_ONLY, prepare=False
    )
    try:
        failure = await prepare_fails(harness)
        assert str(failure) == not_verified("structured_output", "no_tool_call")
        assert len(harness.probes()) == 1
        assert_not_ready(harness, str(failure))
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_ac9_two_tool_calls_refuse_structured_output_with_multiple_tool_calls() -> None:
    """AC9 (R2): two tool calls are ``multiple_tool_calls``, 1 probe sent."""

    session = FakeSession(
        probe_results=[
            FakeResponse(
                200, tool_calls([(PROBE_TOOL, {"ok": True}), (PROBE_TOOL, {"ok": True})])
            )
        ]
    )
    harness = await activate_with(
        session=session, settings_overrides=STRUCTURED_ONLY, prepare=False
    )
    try:
        failure = await prepare_fails(harness)
        assert str(failure) == not_verified("structured_output", "multiple_tool_calls")
        assert len(harness.probes()) == 1
        assert_not_ready(harness, str(failure))
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_ac9_structured_output_only_sends_exactly_one_probe_and_verifies_it() -> None:
    """AC9 (R2): with ``[structured_output]`` only, exactly 1 probe request,
    no image part, and the verified set names that one capability."""

    harness = await activate_with(settings_overrides=STRUCTURED_ONLY)
    try:
        assert len(harness.probes()) == 1
        assert not carries_image_part(harness.probes()[0]["json"])
        assert harness.handle.verified_capabilities == {"structured_output"}
        assert harness.context.actions.is_ready(MODULE_NAME)
    finally:
        await harness.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("answer", "reason"),
    [
        (FakeResponse(200, tool_call(PROBE_TOOL, "oops")), "malformed_arguments"),
        (FakeResponse(200, tool_call(PROBE_TOOL, ["ok"])), "malformed_arguments"),
        (FakeResponse(200, tool_call(PROBE_TOOL, "")), "malformed_arguments"),
        (RuntimeError(f"connection refused by {ENDPOINT} with {API_KEY}"), "transport_failed"),
        (FakeResponse(200, text_and_tool_call("Calling.", PROBE_TOOL, {"ok": True})), "no_tool_call"),
        (FakeResponse(200, tool_call("other.tool", {"ok": True})), "no_tool_call"),
        (FakeResponse(200, {"choices": [{"message": {"content": None}}]}), "no_tool_call"),
        (FakeResponse(200, {"error": {"message": f"bad request {API_KEY}"}}), "no_tool_call"),
        (FakeResponse(200, ValueError("not json")), "no_tool_call"),
        (FakeResponse(404, {}), "non_success_status"),
        (FakeResponse(200, tool_call(PROBE_TOOL, {})), None),
        (FakeResponse(200, tool_call(PROBE_TOOL, {"ok": True}, usage={"total_tokens": 12})), None),
    ],
)
async def test_text_probe_reasons_follow_the_answer_shape(answer: Any, reason: str | None) -> None:
    """R2: non-object arguments are ``malformed_arguments``; a transport
    exception is ``transport_failed``; a tool call with text, a call on
    another tool, an empty message, a 2xx error body or a 2xx that is not
    JSON is ``no_tool_call``; a non-2xx is ``non_success_status``; a forced
    call with any JSON-object argument verifies."""

    session = FakeSession(probe_results=[answer])
    harness = await activate_with(
        session=session, settings_overrides=STRUCTURED_ONLY, prepare=False
    )
    try:
        if reason is None:
            await harness.handle.prepare()
            assert harness.context.actions.is_ready(MODULE_NAME)
            assert harness.handle.verified_capabilities == {"structured_output"}
            assert harness.diagnostics == []
        else:
            failure = await prepare_fails(harness)
            assert str(failure) == not_verified("structured_output", reason)
            assert_not_ready(harness, str(failure))
        assert len(harness.probes()) == 1
    finally:
        await harness.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("answer", "reason"),
    [
        (FakeResponse(200, {"error": {"message": "image input is not supported"}}), "image_rejected"),
        (FakeResponse(200, {"error": "This model does not accept images"}), "image_rejected"),
        (FakeResponse(200, final("I cannot see any image.")), "image_rejected"),
        (FakeResponse(200, {"choices": [{"message": {"content": ""}}]}), "image_rejected"),
        (FakeResponse(200, tool_call("other.tool", {"ok": True})), "image_rejected"),
        (FakeResponse(200, tool_call(PROBE_TOOL, "oops")), "malformed_arguments"),
        (
            FakeResponse(
                200, tool_calls([(PROBE_TOOL, {"ok": True}), (PROBE_TOOL, {"ok": False})])
            ),
            "multiple_tool_calls",
        ),
        (FakeResponse(400, {"error": {"message": "image input is not supported"}}), "non_success_status"),
        (RuntimeError(API_KEY), "transport_failed"),
    ],
)
async def test_image_probe_reasons_name_the_image_once_the_text_probe_passed(
    answer: Any, reason: str
) -> None:
    """R2: after the text probe passed, an image probe whose 2xx body
    carries an ``error`` naming the image input, or answered without the
    forced tool call, is ``image_rejected``; the other reasons keep their
    own name. Both probes were sent, in order."""

    session = FakeSession(
        probe_results=[FakeResponse(200, tool_call(PROBE_TOOL, {"ok": True})), answer]
    )
    harness = await activate_with(session=session, settings_overrides=WITH_VISION, prepare=False)
    try:
        failure = await prepare_fails(harness)
        assert str(failure) == not_verified("vision", reason)
        assert len(harness.probes()) == 2
        assert not carries_image_part(harness.probes()[0]["json"])
        assert len(image_parts(harness.probes()[1]["json"])) == 1
        assert_not_ready(harness, str(failure))
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_a_failed_text_probe_sends_no_image_probe() -> None:
    """R2: the image probe is sent only once the text probe passed."""

    session = FakeSession(probe_results=[FakeResponse(500, {}), FakeResponse(500, {})])
    harness = await activate_with(session=session, settings_overrides=WITH_VISION, prepare=False)
    try:
        failure = await prepare_fails(harness)
        assert str(failure) == not_verified("structured_output", "non_success_status")
        assert len(harness.probes()) == 1
        assert session.probe_results == [session.probe_results[0]]
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_a_held_probe_times_out_on_the_clock_after_model_call_seconds() -> None:
    """R2: no answer within ``budget.model_call_seconds`` is ``timed_out``,
    decided on the injected clock — the probe is still held when the clock
    reaches the bound, and nothing was waited on in real time."""

    session = HeldProbeSession()
    harness = await activate_with(
        session=session, settings_overrides=STRUCTURED_ONLY, prepare=False
    )
    try:
        preparing = asyncio.create_task(harness.handle.prepare())
        await asyncio.wait_for(session.entered.wait(), timeout=1)
        harness.clock.advance(MODEL_CALL_SECONDS - 1)
        await settle()
        assert not preparing.done()
        harness.clock.advance(1)
        with pytest.raises(BrainModuleError) as raised:
            await asyncio.wait_for(preparing, timeout=1)
        assert str(raised.value) == not_verified("structured_output", "timed_out")
        assert len(harness.probes()) == 1
        assert_not_ready(harness, str(raised.value))
    finally:
        session.release.set()
        await harness.close()


@pytest.mark.asyncio
async def test_cancelling_prepare_mid_probe_releases_the_response_and_marks_nothing() -> None:
    """Risk (R2): a ``prepare`` cancelled while the probe body is being read
    releases the response and leaves the module outside the barrier."""

    response = BlockingResponse(tool_call(PROBE_TOOL, {"ok": True}))
    session = FakeSession(probe_results=[response])
    harness = await activate_with(
        session=session, settings_overrides=STRUCTURED_ONLY, prepare=False
    )
    try:
        preparing = asyncio.create_task(harness.handle.prepare())
        await asyncio.wait_for(response.reading.wait(), timeout=1)
        preparing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await preparing
        await settle()
        assert response.release_calls == 1
        assert not harness.context.actions.is_ready(MODULE_NAME)
        assert harness.handle.verified_capabilities == frozenset()
        assert harness.requests() == []
    finally:
        response.unblock.set()
        await harness.close()


# --------------------------------------------------------------------------- #
# Redaction on every failure path
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "answer",
    [
        FakeResponse(500, {"error": f"{API_KEY} {ENDPOINT}"}),
        FakeResponse(200, final(f"{API_KEY} {ENDPOINT}")),
        FakeResponse(200, tool_call(PROBE_TOOL, f'"{API_KEY}"')),
        FakeResponse(200, tool_calls([(PROBE_TOOL, {"k": API_KEY}), (PROBE_TOOL, {})])),
        FakeResponse(200, {"error": {"message": f"image {API_KEY} {ENDPOINT}"}}),
        RuntimeError(f"{API_KEY} {ENDPOINT}"),
        FakeResponse(200, ValueError(f"{API_KEY} {ENDPOINT}")),
    ],
)
async def test_probe_failures_never_carry_the_key_the_endpoint_or_a_body(answer: Any) -> None:
    """R2: whatever the probe failure, the exception message, every
    diagnostic and every trace are free of the key, the endpoint and the
    body that carried them."""

    session = FakeSession(probe_results=[answer])
    harness = await activate_with(
        session=session, settings_overrides=STRUCTURED_ONLY, prepare=False
    )
    try:
        failure = await prepare_fails(harness)
        assert_not_ready(harness, str(failure))
        assert str(failure).startswith(not_verified("structured_output", ""))
        assert "choices" not in str(failure)
        rendered = "\n".join([str(failure), *harness.diagnostics])
        assert API_KEY not in rendered
        assert ENDPOINT not in rendered
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_run_failures_after_a_verified_probe_stay_sanitized() -> None:
    """R2: the run's own failure paths are as value-free as the probe's."""

    harness = await activate_with(
        FakeResponse(200, tool_calls([("a", {}), ("b", {"k": API_KEY})])),
        FakeResponse(200, text_and_tool_call(API_KEY, "a", {})),
        settings_overrides=STRUCTURED_ONLY,
    )
    try:
        await harness.send(message_id="two-calls")
        await harness.send(message_id="call-and-text", viewer_id="viewer-b")
        records = await harness.completed(2)
        assert {record.status for record in records} == {"error"}
        assert len(harness.requests()) == 2
        assert harness.transport.sends == []
        assert harness.diagnostics == [
            "brain model response: unsupported shape: multiple_tool_calls",
            "brain model response: unsupported shape: tool_call_with_text",
        ]
        assert_sanitized(harness.diagnostics)
        assert_no_secret(traces_rendered(harness))
    finally:
        await harness.close()


# --------------------------------------------------------------------------- #
# The request shape and the classification (decision 2)
# --------------------------------------------------------------------------- #


def adapter_on(
    session: FakeSession,
    *,
    clock: ManualClock | None = None,
    attachments: AttachmentStore | None = None,
    diagnostics: list[str] | None = None,
    overrides: dict[str, Any] | None = None,
) -> _ModelAdapter:
    settings = _Settings.from_mapping(merge_settings(overrides))
    return _ModelAdapter(
        session,
        settings,
        clock if clock is not None else ManualClock(),
        attachments,
        (diagnostics if diagnostics is not None else []).append,
        sleeper=(clock if clock is not None else ManualClock()).sleep,
    )


@pytest.mark.asyncio
async def test_request_offers_the_given_specs_as_tools_with_tool_choice_auto() -> None:
    """R2: ``tools[]`` are the offered specs as function tools — name,
    description, the argument schema as ``parameters`` — with
    ``tool_choice: auto`` for a scenario turn; the whole body is plain
    JSON. No tools offered: neither key is sent."""

    session = FakeSession(FakeResponse(200, final("Hi")), FakeResponse(200, final("Hi")))
    adapter = adapter_on(session)
    prompt = _Prompt(
        messages=[{"role": "system", "content": "s"}, {"role": "user", "content": "u"}],
        tools=(CHAT_READ_SPEC, CHAT_WRITE_SPEC),
        input_tokens=10,
        output_tokens=100,
    )
    reply = await adapter._request_model(prompt, budget_seconds=MODEL_CALL_SECONDS)
    assert reply.classification == _Final("Hi")
    assert reply.failure is None
    (call,) = session.post_calls
    body = call["json"]
    json.dumps(body)
    assert body["model"] == SETTINGS["model"]
    assert body["max_tokens"] == 100
    assert body["tool_choice"] == "auto"
    assert body["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "chat.read",
                "description": CHAT_READ_SPEC.description,
                "parameters": {
                    "type": "object",
                    "properties": {"limit": {"type": "integer", "minimum": 1}},
                    "required": [],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "chat.write",
                "description": CHAT_WRITE_SPEC.description,
                "parameters": {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                    "additionalProperties": False,
                },
            },
        },
    ]
    assert body["messages"] == prompt.messages
    assert call["headers"]["Authorization"] == f"Bearer {API_KEY}"
    assert call["url"] == ENDPOINT
    assert not is_probe_request(body)

    bare = _Prompt(messages=prompt.messages, tools=(), input_tokens=10, output_tokens=100)
    await adapter._request_model(bare, budget_seconds=MODEL_CALL_SECONDS)
    assert "tools" not in session.post_calls[1]["json"]
    assert "tool_choice" not in session.post_calls[1]["json"]


@pytest.mark.asyncio
async def test_ac12_image_ref_parts_are_encoded_from_the_store_at_request_time_only() -> None:
    """AC12 (R2): an ``image_ref`` part is resolved from the store while the
    request body is built and encoded as one ``image_url`` data URL of the
    stored bytes; the prompt object still holds the reference and nothing
    else, no diagnostic carries the bytes, and a text part travels as text."""

    clock = ManualClock()
    store = AttachmentStore(
        max_object_bytes=4096,
        max_objects=4,
        max_total_bytes=16384,
        max_bytes_per_run=8192,
        ttl_seconds=60,
        clock=clock,
    )
    ref = store.put("run-1", PNG, content_type="image/png")
    session = FakeSession(FakeResponse(200, final("I see a pixel.")))
    diagnostics: list[str] = []
    adapter = adapter_on(session, clock=clock, attachments=store, diagnostics=diagnostics)
    image_ref = {
        "type": "image_ref",
        "attachment_id": ref.attachment_id,
        "content_type": "image/png",
        "size": len(PNG),
        "width": 1,
        "height": 1,
        "captured_at": 1000.0,
        "provider_id": "capture",
    }
    prompt = _Prompt(
        messages=[
            {"role": "system", "content": "s"},
            {"role": "user", "content": "u"},
            {"role": "assistant", "content": None, "tool_calls": [{"id": "c1"}]},
            {"role": "tool", "tool_call_id": "c1", "content": "captured"},
            {
                "role": "user",
                "content": [{"type": "text", "text": "captured"}, dict(image_ref)],
            },
        ],
        tools=(CHAT_READ_SPEC,),
        input_tokens=20,
        output_tokens=200,
    )
    encoded = base64.b64encode(PNG).decode()

    reply = await adapter._request_model(prompt, budget_seconds=MODEL_CALL_SECONDS)

    assert reply.classification == _Final("I see a pixel.")
    (call,) = session.post_calls
    # The tool result stays text; the image travels in a ``user`` message,
    # the only role whose content the Chat Completions schema lets carry it.
    assert call["json"]["messages"][3] == {
        "role": "tool",
        "tool_call_id": "c1",
        "content": "captured",
    }
    image_message = call["json"]["messages"][4]
    assert image_message["role"] == "user"
    assert image_message["content"] == [
        {"type": "text", "text": "captured"},
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}"}},
    ]
    assert len(image_parts(call["json"])) == 1
    # The prompt object is untouched: the reference, never the bytes.
    assert prompt.messages[4]["content"][1] == image_ref
    assert encoded not in json.dumps(prompt.messages)
    assert encoded not in "\n".join(diagnostics)
    assert diagnostics == []
    assert store.usage("run-1").objects == 1  # read, not consumed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("advance", "attachment_id", "failure"),
    [
        (60.0, None, "attachment_expired"),
        (0.0, "never-issued", "attachment_unavailable"),
    ],
)
async def test_an_image_ref_the_store_cannot_serve_fails_before_any_request(
    advance: float, attachment_id: str | None, failure: str
) -> None:
    """R2: a reference whose lease expired on the clock, or one the store
    does not hold, is a failure of the request — 0 requests sent, nothing
    encoded — never a request with the image dropped silently."""

    clock = ManualClock()
    store = AttachmentStore(
        max_object_bytes=4096,
        max_objects=4,
        max_total_bytes=16384,
        max_bytes_per_run=8192,
        ttl_seconds=60,
        clock=clock,
    )
    ref = store.put("run-1", PNG, content_type="image/png")
    clock.advance(advance)
    session = FakeSession(FakeResponse(200, final("never")))
    diagnostics: list[str] = []
    adapter = adapter_on(session, clock=clock, attachments=store, diagnostics=diagnostics)
    prompt = _Prompt(
        messages=[
            {"role": "user", "content": "u"},
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_ref",
                        "attachment_id": attachment_id or ref.attachment_id,
                        "content_type": "image/png",
                        "size": len(PNG),
                        "width": 1,
                        "height": 1,
                        "captured_at": 1000.0,
                        "provider_id": "capture",
                    }
                ],
            },
        ],
        input_tokens=20,
        output_tokens=200,
    )

    reply = await adapter._request_model(prompt, budget_seconds=MODEL_CALL_SECONDS)

    assert reply.failure == failure
    assert reply.classification is None
    assert session.post_calls == []
    assert diagnostics == ["brain model request: image attachment unavailable"]


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (tool_call("chat.read", {"limit": 3}), _Proposal("chat.read", '{"limit": 3}')),
        (tool_call("chat.read", "oops"), _Proposal("chat.read", "oops")),
        (final("Hello viewer"), _Final("Hello viewer")),
        ({"choices": [{"message": {"content": "Hi", "tool_calls": []}}]}, _Final("Hi")),
        ({"choices": [{"message": {"content": "Hi", "tool_calls": None}}]}, _Final("Hi")),
        (
            tool_calls([("chat.read", {}), ("users.read", {})]),
            _Unsupported("multiple_tool_calls"),
        ),
        (text_and_tool_call("Let me look.", "chat.read", {}), _Unsupported("tool_call_with_text")),
        ({"choices": [{"message": {"content": "   "}}]}, _Unsupported("no_content")),
        ({"choices": [{"message": {"content": None}}]}, _Unsupported("no_content")),
        ({"choices": [{"message": {"content": None, "tool_calls": []}}]}, _Unsupported("no_content")),
        ({}, _Unsupported("malformed_body")),
        ({"choices": []}, _Unsupported("malformed_body")),
        ("text", _Unsupported("malformed_body")),
        (
            {"choices": [{"message": {"tool_calls": [{"type": "function"}]}}]},
            _Unsupported("malformed_tool_call"),
        ),
    ],
)
def test_classification_of_the_response_shapes(body: Any, expected: Any) -> None:
    """Decision 2: exactly one tool call is a proposal carrying the raw
    arguments as sent; text and no tool call is the final response; two or
    more calls, a call with text, and neither — blank, null, no message at
    all — are unsupported, each naming why. Nothing falls back to text."""

    assert _ModelAdapter.classify(body) == expected


@pytest.mark.asyncio
async def test_a_scenario_request_answered_with_a_proposal_is_classified_not_delivered() -> None:
    """R2: a single tool call in a run is a ``_Proposal`` — the reply is the
    classification, never a text to deliver. The loop feeds the proposal
    back as an observation and asks the model again: a ``chat.write``
    proposal is refused by the runtime itself (``not_a_read_action``), so
    nothing reaches the executor for it, and only the final response of
    the next turn is delivered — the proposal's ``text`` never is."""

    harness = await activate_with(
        FakeResponse(200, tool_call("chat.write", {"text": "hi"}, usage={"total_tokens": 42})),
        FakeResponse(200, final("Hello there.")),
        settings_overrides=STRUCTURED_ONLY,
    )
    try:
        await harness.send()
        (record,) = await harness.completed()
        assert record.status == "success"
        assert record.sends == 1
        assert [send["text"] for send in harness.transport.sends] == ["Hello there."]
        # The proposal was classified, not executed: the executor saw the
        # final delivery only.
        assert harness.executor.provider_invocations == 1
        assert harness.diagnostics == []
        _first, second = harness.requests()
        (assistant_call,) = [
            message for message in second["json"]["messages"] if message["role"] == "assistant"
        ]
        assert assistant_call["tool_calls"][0]["function"]["name"] == "chat.write"
        (tool_result,) = [
            message for message in second["json"]["messages"] if message["role"] == "tool"
        ]
        assert isinstance(tool_result["content"], str)
        assert json.loads(tool_result["content"].splitlines()[0]) == {
            "status": "refused",
            "error": {"code": BRAIN_ERROR_NOT_A_READ_ACTION},
        }
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_usage_is_read_when_reported_and_estimated_otherwise() -> None:
    """R2/§3.4 unchanged: the backend's usage is taken when reported, an
    estimate flagged as such stands in when it is not — for a proposal as
    for a final text."""

    session = FakeSession(
        FakeResponse(200, tool_call("chat.read", {"limit": 2}, usage={"total_tokens": 77})),
        FakeResponse(200, tool_call("chat.read", {"limit": 2})),
        FakeResponse(200, final("x" * 300, usage={"prompt_tokens": 5, "completion_tokens": 6})),
        FakeResponse(200, final("x" * 300)),
    )
    adapter = adapter_on(session)
    prompt = _Prompt(messages=[{"role": "user", "content": "u"}], input_tokens=10, output_tokens=50)

    reported = await adapter._request_model(prompt, budget_seconds=MODEL_CALL_SECONDS)
    assert (reported.tokens, reported.estimated) == (77, False)
    assert reported.usage() == {"tokens": 77, "tokens_estimated": False}

    estimated = await adapter._request_model(prompt, budget_seconds=MODEL_CALL_SECONDS)
    assert estimated.estimated is True
    assert estimated.tokens is not None and estimated.tokens > 10
    assert isinstance(estimated.classification, _Proposal)

    summed = await adapter._request_model(prompt, budget_seconds=MODEL_CALL_SECONDS)
    assert (summed.tokens, summed.estimated) == (11, False)

    text_estimate = await adapter._request_model(prompt, budget_seconds=MODEL_CALL_SECONDS)
    assert text_estimate.estimated is True
    assert text_estimate.tokens is not None and text_estimate.tokens >= 10 + 100


@pytest.mark.asyncio
async def test_a_held_scenario_request_times_out_on_the_clock() -> None:
    """R3 (adapter side): the model call's time bound runs on the injected
    clock — the reply is ``timed_out`` once the clock passes the budget."""

    class HeldScenarioSession(FakeSession):
        def __init__(self) -> None:
            super().__init__()
            self.release = asyncio.Event()

        async def _answer(self, url: str, kwargs: dict[str, Any]) -> Any:
            self.post_calls.append({"url": url, **kwargs})
            await self.release.wait()
            return FakeResponse(200, final("late"))

    clock = ManualClock()
    session = HeldScenarioSession()
    diagnostics: list[str] = []
    adapter = adapter_on(session, clock=clock, diagnostics=diagnostics)
    prompt = _Prompt(messages=[{"role": "user", "content": "u"}], input_tokens=10, output_tokens=50)
    request = asyncio.create_task(adapter._request_model(prompt, budget_seconds=12.5))
    await settle()
    assert not request.done()
    clock.advance(12.0)
    await settle()
    assert not request.done()
    clock.advance(0.5)
    reply = await asyncio.wait_for(request, timeout=1)
    assert reply.failure == "timed_out"
    assert diagnostics == ["brain model request: timed out"]
    session.release.set()


@pytest.mark.asyncio
async def test_a_timed_out_request_has_released_its_response_when_the_reply_returns() -> None:
    """Risk (R2): the timer winning cancels the request, and a cancellation
    only schedules the request's cleanup. The adapter waits for it: when
    ``timed_out`` is reported the response is already released — through an
    asynchronous ``release`` that itself had to be awaited — so a shutdown
    that follows closes no session under a request still holding a body."""

    class SlowReleaseResponse(BlockingResponse):
        """A blocking body read whose release is awaited across a loop turn."""

        async def release(self) -> None:  # type: ignore[override]
            await asyncio.sleep(0)
            self.release_calls += 1

    response = SlowReleaseResponse(final("late"))
    session = FakeSession(response)
    clock = ManualClock()
    diagnostics: list[str] = []
    adapter = adapter_on(session, clock=clock, diagnostics=diagnostics)
    prompt = _Prompt(messages=[{"role": "user", "content": "u"}], input_tokens=10, output_tokens=50)
    request = asyncio.create_task(adapter._request_model(prompt, budget_seconds=5.0))
    await asyncio.wait_for(response.reading.wait(), timeout=1)
    clock.advance(5.0)
    reply = await asyncio.wait_for(request, timeout=1)
    assert reply.failure == "timed_out"
    assert response.release_calls == 1
    assert diagnostics == ["brain model request: timed out"]
    response.unblock.set()


@pytest.mark.asyncio
async def test_cancelling_a_scenario_request_awaits_its_release_before_propagating() -> None:
    """Risk (R2): a caller cancelled mid-request sees the cancellation only
    once the request task has unwound and released its response."""

    class SlowReleaseResponse(BlockingResponse):
        async def release(self) -> None:  # type: ignore[override]
            await asyncio.sleep(0)
            self.release_calls += 1

    response = SlowReleaseResponse(final("late"))
    session = FakeSession(response)
    adapter = adapter_on(session, clock=ManualClock())
    prompt = _Prompt(messages=[{"role": "user", "content": "u"}], input_tokens=10, output_tokens=50)
    request = asyncio.create_task(adapter._request_model(prompt, budget_seconds=5.0))
    await asyncio.wait_for(response.reading.wait(), timeout=1)
    request.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request
    assert response.release_calls == 1
    response.unblock.set()


def test_settings_fixture_requires_both_capabilities_by_default() -> None:
    """The shared fixture verifies both capabilities, so every brain suite
    runs behind two answered probes unless it narrows the list."""

    assert VALID_SETTINGS["capabilities"]["required"] == ["structured_output", "vision"]
