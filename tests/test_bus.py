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


def _payload_of(event: dict) -> dict:
    return event["payload"]


@pytest.mark.asyncio
async def test_count_limit_keeps_newest_records_without_skipping_subscribers() -> None:
    """AC20 (R6): 15 publications under a 10-event limit retain the 10 newest."""

    bus = EventBus(history_max_events=10)
    calls: list[str] = []

    def handler(name: str):
        async def handle(event: dict) -> None:
            calls.append(f"{name}:{event['payload']['index']}")

        return handle

    bus.subscribe("**", handler("last"), order=90)
    bus.subscribe("**", handler("first"), order=10)
    bus.subscribe("**", handler("second"), order=10)

    for index in range(15):
        await bus.publish("channel.chat.message", {"index": index}, {})

    assert calls == [
        f"{name}:{index}"
        for index in range(15)
        for name in ("first", "second", "last")
    ]

    retained = bus.list_events()
    assert len(retained) == 10
    assert [_payload_of(event)["index"] for event in retained] == list(range(5, 15))
    assert all(_payload_of(event)["index"] >= 5 for event in retained)


@pytest.mark.asyncio
async def test_byte_limit_evicts_before_count_limit_without_failing_publications(
) -> None:
    """AC20 (R6): a byte bound reached first retains fewer than 10 records."""

    bus = EventBus(history_max_events=10, history_max_bytes=400)
    failures: list[Exception] = []

    for index in range(15):
        try:
            await bus.publish(
                "channel.chat.message",
                {"index": index, "text": "x" * 100},
                {},
            )
        except PublicationError as exc:  # pragma: no cover - guards AC20
            failures.append(exc)

    retained = bus.list_events()
    assert failures == []
    assert 0 < len(retained) < 10
    assert [_payload_of(event)["index"] for event in retained] == list(
        range(15 - len(retained), 15)
    )


@pytest.mark.asyncio
async def test_age_limit_evicts_on_append_and_on_read_with_injected_clock() -> None:
    """AC20 (R6): the age bound is applied both at append and at read time."""

    now = [0.0]
    bus = EventBus(
        history_max_events=10,
        history_max_age_seconds=10.0,
        clock=lambda: now[0],
    )

    await bus.publish("channel.chat.message", {"index": 0}, {})
    now[0] = 6.0
    await bus.publish("channel.chat.message", {"index": 1}, {})
    assert [_payload_of(event)["index"] for event in bus.list_events()] == [0, 1]

    # Appending past the first record's age evicts it, keeping the newer one.
    now[0] = 11.0
    await bus.publish("channel.chat.message", {"index": 2}, {})
    assert [_payload_of(event)["index"] for event in bus.list_events()] == [1, 2]

    # No publication at all: reading alone must not return a stale record.
    now[0] = 22.0
    assert bus.list_events() == []

    now[0] = 23.0
    await bus.publish("channel.chat.message", {"index": 3}, {})
    assert [_payload_of(event)["index"] for event in bus.list_events()] == [3]


@pytest.mark.asyncio
async def test_full_history_never_fails_a_publication() -> None:
    """AC20 (R6): eviction runs after the chain, so a saturated bus still publishes."""

    bus = EventBus(history_max_events=1, history_max_bytes=1)
    seen: list[int] = []
    bus.subscribe("**", lambda event: seen.append(event["payload"]["index"]))

    for index in range(5):
        finalized = await bus.publish("channel.chat.message", {"index": index}, {})
        assert finalized["payload"]["index"] == index

    assert seen == [0, 1, 2, 3, 4]


@pytest.mark.asyncio
async def test_eviction_preserves_replacement_and_publication_error_semantics(
) -> None:
    """AC20 (R6): bounded retention leaves replacement and failure semantics intact."""

    bus = EventBus(history_max_events=2)
    replacement = {
        "type": "channel.chat.rewritten",
        "payload": {"text": "updated"},
        "metadata": {"source": "middleware"},
    }
    bus.subscribe("channel.chat.message", lambda event: replacement)
    bus.subscribe("broken", lambda event: (_ for _ in ()).throw(RuntimeError("boom")))

    await bus.publish("channel.chat.message", {"text": "original"}, {})
    with pytest.raises(PublicationError):
        await bus.publish("broken", {}, {})
    await bus.publish("channel.chat.message", {"text": "original"}, {})

    assert bus.list_events() == [replacement, replacement]


_VALID_LIMITS = {
    "history_max_events": 10,
    "history_max_bytes": 4096,
    "history_max_age_seconds": 60.0,
}


@pytest.mark.parametrize("setting", sorted(_VALID_LIMITS))
@pytest.mark.parametrize(
    "invalid",
    [None, float("inf"), float("-inf"), float("nan"), 0, -1, "10"],
)
def test_absent_or_non_finite_limit_is_rejected_naming_the_setting(
    setting: str, invalid: object
) -> None:
    """AC20 (R6): every bound is validated as finite and positive, by name."""

    assert EventBus(**_VALID_LIMITS) is not None

    limits = dict(_VALID_LIMITS, **{setting: invalid})
    with pytest.raises((TypeError, ValueError)) as caught:
        EventBus(**limits)

    assert setting in str(caught.value)


def test_non_callable_clock_is_rejected() -> None:
    with pytest.raises(TypeError):
        EventBus(clock=0.0)


class _Holder:
    """A payload value JSON cannot represent, hiding a large buffer."""

    def __init__(self, buffer: bytearray) -> None:
        self.buffer = buffer


@pytest.mark.asyncio
async def test_byte_limit_charges_retained_contents_not_their_repr() -> None:
    """AC20 (R6): an opaque value is charged for the buffer the history keeps."""

    bus = EventBus(history_max_events=10, history_max_bytes=4096)

    for index in range(5):
        await bus.publish(
            "channel.chat.message",
            {"index": index, "holder": _Holder(bytearray(2048))},
            {},
        )

    retained = bus.list_events()
    assert 0 < len(retained) < 5
    assert [_payload_of(event)["index"] for event in retained] == list(
        range(5 - len(retained), 5)
    )
