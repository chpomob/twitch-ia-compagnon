from functools import partial

import pytest

from core import EventBus, PublicationError


@pytest.mark.asyncio
async def test_exact_single_segment_and_recursive_wildcards() -> None:
    bus = EventBus()
    calls: list[str] = []

    async def record(name: str, event: dict) -> None:
        calls.append(name)

    bus.subscribe("channel.chat.message", lambda event: record("exact", event))
    bus.subscribe("channel.*.message", lambda event: record("single", event))
    bus.subscribe("**", lambda event: record("recursive", event))

    await bus.publish(
        event_type="channel.chat.message",
        payload={},
        metadata={},
    )
    assert calls == ["exact", "single", "recursive"]

    calls.clear()
    await bus.publish("channel.chat.send", {}, {})
    assert calls == ["recursive"]


@pytest.mark.asyncio
async def test_recursive_wildcard_matches_zero_segments() -> None:
    bus = EventBus()
    calls: list[str] = []
    bus.subscribe("channel.**", lambda event: calls.append(event["type"]))
    bus.subscribe("channel.*.message", lambda event: calls.append("unexpected"))

    await bus.publish("channel.message", {}, {})

    assert calls == ["channel.message"]


@pytest.mark.asyncio
async def test_order_is_stable_and_false_stops_chain() -> None:
    bus = EventBus()
    calls: list[str] = []

    def handler(name: str, result=None):
        async def handle(event: dict):
            calls.append(name)
            return result

        return handle

    bus.subscribe("**", handler("last"), order=90)
    bus.subscribe("**", handler("first"), order=10)
    bus.subscribe("**", handler("second"), order=10)
    bus.subscribe("**", handler("stop", False), order=50)

    finalized = await bus.publish("channel.chat.message", {}, {})

    assert calls == ["first", "second", "stop"]
    assert finalized == {"type": "channel.chat.message", "payload": {}, "metadata": {}}
    assert bus.list_events() == [finalized]


@pytest.mark.asyncio
async def test_complete_replacement_reaches_later_handlers_and_history() -> None:
    bus = EventBus()
    replacement = {
        "type": "channel.chat.rewritten",
        "payload": {"text": "updated"},
        "metadata": {"source": "middleware"},
        "extra": "preserved",
    }
    observed: list[dict] = []

    bus.subscribe("channel.chat.message", lambda event: replacement)
    bus.subscribe("channel.chat.message", lambda event: observed.append(event))

    finalized = await bus.publish("channel.chat.message", {"text": "original"}, {})

    assert observed == [replacement]
    assert finalized == replacement
    assert bus.list_events() == [replacement]


@pytest.mark.asyncio
async def test_sync_and_async_handlers_preserve_event_for_no_op_results() -> None:
    bus = EventBus()
    payload = {"text": "hello"}
    metadata = {"source": "test"}
    observed: list[tuple[dict, dict]] = []

    def sync_handler(event: dict) -> int:
        observed.append((event["payload"], event["metadata"]))
        return 0

    async def async_handler(event: dict) -> list:
        observed.append((event["payload"], event["metadata"]))
        return []

    bus.subscribe("**", sync_handler)
    bus.subscribe("**", async_handler)

    finalized = await bus.publish("channel.chat.message", payload, metadata)

    assert observed == [(payload, metadata), (payload, metadata)]
    assert finalized["payload"] is payload
    assert finalized["metadata"] is metadata


@pytest.mark.asyncio
async def test_history_is_isolated_from_publishers_and_readers() -> None:
    bus = EventBus()
    payload = {"nested": {"text": "original"}}
    metadata = {"tags": ["initial"]}

    finalized = await bus.publish("channel.chat.message", payload, metadata)
    payload["nested"]["text"] = "publisher mutation"
    metadata["tags"].append("publisher mutation")

    first_read = bus.list_events()
    assert first_read == [
        {
            "type": "channel.chat.message",
            "payload": {"nested": {"text": "original"}},
            "metadata": {"tags": ["initial"]},
        }
    ]

    first_read[0]["payload"]["nested"]["text"] = "reader mutation"
    assert bus.list_events()[0]["payload"]["nested"]["text"] == "original"
    assert finalized["payload"] is payload


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["type", "payload", "metadata"])
async def test_incomplete_replacement_fails_without_history(missing: str) -> None:
    bus = EventBus()
    replacement = {"type": "rewritten", "payload": {}, "metadata": {}}
    replacement.pop(missing)
    bus.subscribe("**", lambda event: replacement)

    with pytest.raises(PublicationError) as caught:
        await bus.publish("original", {}, {})

    assert caught.value.event_type == "original"
    assert "<lambda>" in caught.value.handler_identity
    assert bus.list_events() == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("type", ""),
        ("type", "invalid.*"),
        ("type", 123),
        ("payload", []),
        ("metadata", []),
    ],
)
async def test_invalid_replacement_field_fails_without_poisoning_bus(
    field: str, invalid: object
) -> None:
    bus = EventBus()
    replacement = {"type": "rewritten", "payload": {}, "metadata": {}}
    replacement[field] = invalid
    bus.subscribe("original", lambda event: replacement)

    with pytest.raises(PublicationError):
        await bus.publish("original", {}, {})

    completed = await bus.publish("independent", {}, {})
    assert bus.list_events() == [completed]


@pytest.mark.asyncio
async def test_handler_failure_is_identified_and_does_not_poison_bus() -> None:
    bus = EventBus()

    async def broken_handler(event: dict) -> None:
        raise RuntimeError("boom")

    bus.subscribe("broken", broken_handler)

    with pytest.raises(PublicationError) as caught:
        await bus.publish("broken", {}, {})

    assert caught.value.event_type == "broken"
    assert "broken_handler" in caught.value.handler_identity
    assert isinstance(caught.value.__cause__, RuntimeError)
    assert bus.list_events() == []

    completed = await bus.publish("independent", {"ok": True}, {})
    assert bus.list_events() == [completed]


@pytest.mark.asyncio
async def test_partial_handler_failure_identifies_wrapped_callable() -> None:
    bus = EventBus()

    def broken_handler(prefix: str, event: dict) -> None:
        raise RuntimeError(prefix)

    handler = partial(broken_handler, "boom")
    bus.subscribe("broken", handler)

    with pytest.raises(PublicationError) as caught:
        await bus.publish("broken", {}, {})

    assert caught.value.handler is handler
    assert "functools.partial" in caught.value.handler_identity
    assert "broken_handler" in caught.value.handler_identity


@pytest.mark.parametrize(
    "invalid",
    ["", ".channel", "channel.", "channel..message", "channel*", "chan*nel"],
)
def test_invalid_patterns_are_rejected(invalid: str) -> None:
    with pytest.raises((TypeError, ValueError)):
        EventBus().subscribe(invalid, lambda event: None)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid",
    ["", ".channel", "channel.", "channel..message", "channel.*", "channel.**"],
)
async def test_invalid_event_types_are_rejected(invalid: str) -> None:
    with pytest.raises((TypeError, ValueError)):
        await EventBus().publish(invalid, {}, {})
