"""Bounded retention: chat transcript context and attachment store (R3, R6).

Two stores share this file because they answer the same requirement from
opposite ends. The chat context (AC11) is *lossy by design*: it evicts its
oldest entries so ingestion is never refused. The attachment store (AC23,
AC31) is *refusing by design*: it never drops a reference leased to a live run
to make room, because those bytes are an observation a run still holds.

The full AC11 pipeline assertion — three real ingestions through triggers,
admission and a stale run — is added in P22; these tests pin the stores
themselves: appends are total, reads are ordered, dated and channel-isolated,
every context bound evicts oldest first against an injected clock, every
attachment bound refuses by name without evicting, per-run quotas are
independent, releasing a run frees exactly its bytes, an expired reference
reads as an explicit error rather than empty content, and a non-finite bound
is refused at construction by name in both stores.
"""

from array import array

import pytest

from core import attachments
from core.attachments import (
    AttachmentExpired,
    AttachmentRef,
    AttachmentRefused,
    AttachmentStore,
    AttachmentUnknown,
    RunUsage,
)
from core.context import ChatContext, ChatEntry
from core.contracts import ContractError, SessionKey


class _Clock:
    """An injected clock: no test ever sleeps."""

    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


_GENEROUS = {
    "max_messages": 100,
    "max_bytes": 1_000_000,
    "max_age_seconds": 1_000.0,
}


def _context(**overrides: object) -> ChatContext:
    settings = dict(_GENEROUS, **overrides)
    settings.setdefault("clock", _Clock())
    return ChatContext(**settings)  # type: ignore[arg-type]


def _entry(message_id: str, text: str = "hello", author_id: str = "a") -> ChatEntry:
    return ChatEntry(author_id=author_id, message_id=message_id, text=text)


def _ingest(context: ChatContext, entry: ChatEntry, *, outcome: str) -> str:
    """Ingest one message the way the pipeline does, then take *outcome*.

    The context is fed after normalisation and before trigger evaluation, so
    this helper appends unconditionally and only then simulates the pipeline
    verdict — a trigger rejection or an admission rejection simply returns
    without calling the next stage (AC11).
    """

    context.append("twitch", "channel-1", entry)
    return outcome


# --------------------------------------------------------------------------- #
# Appends are total, reads are ordered, dated and channel-isolated (AC11)
# --------------------------------------------------------------------------- #


def test_appends_survive_trigger_admission_and_stale_drop_rejections() -> None:
    """AC11 (R3): the transcript keeps messages the pipeline later refuses."""

    clock = _Clock()
    context = _context(clock=clock)

    clock.now = 1.0
    assert _ingest(context, _entry("m1", "rejected by its trigger"),
                   outcome="trigger_rejected") == "trigger_rejected"
    clock.now = 2.0
    assert _ingest(context, _entry("m2", "refused by admission"),
                   outcome="admission_rejected") == "admission_rejected"
    clock.now = 3.0
    assert _ingest(context, _entry("m3", "run ended stale"),
                   outcome="stale_drop") == "stale_drop"

    retained = context.read("twitch", "channel-1", limit=10)
    assert [record.message_id for record in retained] == ["m1", "m2", "m3"]


def test_reads_are_ordered_and_dated() -> None:
    """AC11 (R3): a read returns ingestion order with the ingestion timestamps."""

    clock = _Clock()
    context = _context(clock=clock)

    for index, moment in enumerate((4.0, 5.5, 9.25)):
        clock.now = moment
        context.append("twitch", "channel-1", _entry(f"m{index}", f"text {index}"))

    retained = context.read("twitch", "channel-1", limit=10)
    assert [record.message_id for record in retained] == ["m0", "m1", "m2"]
    assert [record.timestamp for record in retained] == [4.0, 5.5, 9.25]
    assert [record.text for record in retained] == ["text 0", "text 1", "text 2"]
    assert [record.author_id for record in retained] == ["a", "a", "a"]


def test_read_for_another_channel_returns_no_entries() -> None:
    """AC11 (R3): the key is the pair, so no transcript crosses a channel."""

    context = _context()
    context.append("twitch", "channel-1", _entry("m1"))
    context.append("twitch", "channel-1", _entry("m2"))

    assert len(context.read("twitch", "channel-1", limit=10)) == 2
    # Same platform, other channel; and same channel identifier, other platform.
    assert context.read("twitch", "channel-2", limit=10) == ()
    assert context.read("discord", "channel-1", limit=10) == ()


def test_read_limit_returns_the_most_recent_entries_in_order() -> None:
    """R3: a bounded read is capped without reversing ingestion order."""

    context = _context()
    for index in range(5):
        context.append("twitch", "channel-1", _entry(f"m{index}"))

    assert [r.message_id for r in context.read("twitch", "channel-1", limit=2)] == [
        "m3",
        "m4",
    ]
    assert len(context.read("twitch", "channel-1", limit=50)) == 5
    assert context.read("twitch", "channel-1", limit=0) == ()

    with pytest.raises(ContractError) as caught:
        context.read("twitch", "channel-1", limit=-1)
    assert "limit" in str(caught.value)


# --------------------------------------------------------------------------- #
# Each bound evicts oldest first (R6)
# --------------------------------------------------------------------------- #


def test_message_bound_evicts_oldest_first() -> None:
    """R6: ``max_messages`` keeps the newest entries and drops the oldest."""

    context = _context(max_messages=3)
    for index in range(5):
        context.append("twitch", "channel-1", _entry(f"m{index}"))

    retained = context.read("twitch", "channel-1", limit=10)
    assert [record.message_id for record in retained] == ["m2", "m3", "m4"]


def test_byte_bound_evicts_oldest_first_and_is_never_exceeded() -> None:
    """R6: ``max_bytes`` evicts oldest first; saturation drops history, by design."""

    # Each entry weighs len("a") + len("mN") + len("hello") == 8 bytes.
    context = _context(max_messages=100, max_bytes=17)
    for index in range(3):
        context.append("twitch", "channel-1", _entry(f"m{index}"))

    retained = context.read("twitch", "channel-1", limit=10)
    assert [record.message_id for record in retained] == ["m1", "m2"]

    # Documented saturation behaviour: an entry heavier than the whole bound
    # leaves the key empty rather than letting the store exceed its limit.
    context.append("twitch", "channel-1", _entry("m3", "x" * 100))
    assert context.read("twitch", "channel-1", limit=10) == ()


def test_age_bound_evicts_oldest_first_on_append_and_on_read() -> None:
    """R6: the age bound is applied at append and again at read, on the clock."""

    clock = _Clock()
    context = _context(max_age_seconds=10.0, clock=clock)

    clock.now = 0.0
    context.append("twitch", "channel-1", _entry("m0"))
    clock.now = 6.0
    context.append("twitch", "channel-1", _entry("m1"))
    assert [r.message_id for r in context.read("twitch", "channel-1", limit=10)] == [
        "m0",
        "m1",
    ]

    # Appending past the oldest entry's age evicts it and keeps the newer one.
    clock.now = 11.0
    context.append("twitch", "channel-1", _entry("m2"))
    assert [r.message_id for r in context.read("twitch", "channel-1", limit=10)] == [
        "m1",
        "m2",
    ]

    # No append at all: a read alone must not return a stale entry.
    clock.now = 30.0
    assert context.read("twitch", "channel-1", limit=10) == ()

    clock.now = 31.0
    context.append("twitch", "channel-1", _entry("m3"))
    assert [r.message_id for r in context.read("twitch", "channel-1", limit=10)] == [
        "m3"
    ]


def test_live_channel_cap_evicts_the_least_recently_appended_channel() -> None:
    """R6: a new channel per event cannot grow the key space without limit."""

    context = _context(max_channels=2)
    context.append("twitch", "channel-1", _entry("m1"))
    context.append("twitch", "channel-2", _entry("m2"))

    # Reading is not activity: the cap tracks the least recently *appended*.
    assert len(context.read("twitch", "channel-1", limit=10)) == 1
    context.append("twitch", "channel-2", _entry("m3"))
    context.append("twitch", "channel-3", _entry("m4"))

    assert context.live_channels() == (("twitch", "channel-2"), ("twitch", "channel-3"))
    assert context.read("twitch", "channel-1", limit=10) == ()
    assert len(context.read("twitch", "channel-2", limit=10)) == 2
    assert len(context.read("twitch", "channel-3", limit=10)) == 1


# --------------------------------------------------------------------------- #
# Construction and key validation (R3, R6)
# --------------------------------------------------------------------------- #


_VALID_LIMITS = {
    "max_messages": 10,
    "max_bytes": 4096,
    "max_age_seconds": 60.0,
    "max_channels": 8,
}


@pytest.mark.parametrize("setting", sorted(_VALID_LIMITS))
@pytest.mark.parametrize(
    "invalid",
    [None, float("inf"), float("-inf"), float("nan"), 0, -1, "10"],
)
def test_absent_or_non_finite_bound_is_rejected_naming_the_setting(
    setting: str, invalid: object
) -> None:
    """R6: every bound is required and validated finite, by name."""

    assert ChatContext(**_VALID_LIMITS) is not None  # type: ignore[arg-type]

    limits = dict(_VALID_LIMITS, **{setting: invalid})
    with pytest.raises(ContractError) as caught:
        ChatContext(**limits)  # type: ignore[arg-type]

    assert setting in str(caught.value)


@pytest.mark.parametrize("setting", ["max_messages", "max_bytes", "max_age_seconds"])
def test_every_per_key_bound_is_required(setting: str) -> None:
    """R6: the three per-key bounds have no default to fall back on."""

    limits = {name: value for name, value in _VALID_LIMITS.items() if name != setting}
    with pytest.raises(TypeError) as caught:
        ChatContext(**limits)  # type: ignore[arg-type]

    assert setting in str(caught.value)


def test_session_key_is_refused_as_a_context_key() -> None:
    """R3: the transcript is keyed by the pair; memory is the brain's, not ours."""

    context = _context()
    session = SessionKey(platform="twitch", channel_id="channel-1", viewer_id="v1")

    with pytest.raises(ContractError) as caught:
        context.append(session, "channel-1", _entry("m1"))  # type: ignore[arg-type]
    assert "SessionKey" in str(caught.value)

    with pytest.raises(ContractError):
        context.read("twitch", session, limit=10)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("platform", "channel_id"),
    [("", "channel-1"), ("twitch", ""), (None, "channel-1"), ("twitch", 7)],
)
def test_blank_or_non_text_key_components_are_refused(
    platform: object, channel_id: object
) -> None:
    context = _context()
    with pytest.raises(ContractError):
        context.append(platform, channel_id, _entry("m1"))  # type: ignore[arg-type]


def test_entry_fields_are_validated_and_empty_text_is_accepted() -> None:
    """R3: identity is required; a message carrying no text is still transcript."""

    context = _context()
    with pytest.raises(ContractError) as caught:
        ChatEntry(author_id="", message_id="m1", text="hi")
    assert "author_id" in str(caught.value)

    with pytest.raises(ContractError) as caught:
        ChatEntry(author_id="a", message_id="m1", text=None)  # type: ignore[arg-type]
    assert "text" in str(caught.value)

    with pytest.raises(ContractError):
        context.append("twitch", "channel-1", ("a", "m1", "hi"))  # type: ignore[arg-type]

    record = context.append("twitch", "channel-1", _entry("m1", ""))
    assert record.text == ""
    assert context.read("twitch", "channel-1", limit=10) == (record,)


# --------------------------------------------------------------------------- #
# Attachment store: refusal never evicts a live lease (R6, AC23)
# --------------------------------------------------------------------------- #


UNIT = 1024
"""The specification's unit of attachment volume, in bytes.

AC23 and AC31 are written in units rather than bytes; fixing one value here
keeps both readable — a "1-unit object" is literally ``_payload(1)``.
"""


def _payload(units: float, fill: bytes = b"x") -> bytes:
    """Return a payload of *units* units of volume."""

    return fill * int(UNIT * units)


_GENEROUS_ATTACHMENTS = {
    "max_object_bytes": 64 * UNIT,
    "max_objects": 64,
    "max_total_bytes": 64 * UNIT,
    "max_bytes_per_run": 64 * UNIT,
    "ttl_seconds": 1_000.0,
}


def _store(**overrides: object) -> AttachmentStore:
    settings = dict(_GENEROUS_ATTACHMENTS, **overrides)
    settings.setdefault("clock", _Clock())
    return AttachmentStore(**settings)  # type: ignore[arg-type]


def _saturated_store(clock: _Clock | None = None) -> tuple[AttachmentStore, list[AttachmentRef]]:
    """A store limited to 2 objects and 1 unit, holding exactly that for one run."""

    store = _store(
        max_objects=2,
        max_total_bytes=UNIT,
        max_object_bytes=UNIT,
        max_bytes_per_run=4 * UNIT,
        clock=clock or _Clock(),
    )
    refs = [
        store.put("run-1", _payload(0.5, b"a"), content_type="image/png"),
        store.put("run-1", _payload(0.5, b"b"), content_type="image/png"),
    ]
    assert store.object_count == 2
    assert store.total_bytes == UNIT
    return store, refs


def _assert_intact(store: AttachmentStore, refs: list[AttachmentRef]) -> None:
    """The refusal evicted nothing: every leased reference still resolves."""

    assert store.object_count == len(refs)
    assert store.total_bytes == sum(ref.size for ref in refs)
    for ref in refs:
        assert len(store.get(ref)) == ref.size


def test_a_third_object_is_refused_naming_the_object_count_and_evicts_nothing() -> None:
    """AC23 (R6): the store refuses rather than dropping a live run's lease."""

    store, refs = _saturated_store()

    with pytest.raises(AttachmentRefused) as caught:
        store.put("run-1", _payload(0.5, b"c"), content_type="image/png")

    assert caught.value.limit == "max_objects"
    assert "max_objects" in str(caught.value)
    _assert_intact(store, refs)


def test_an_over_sized_object_is_refused_naming_the_per_object_limit() -> None:
    """AC23 (R6): the per-object bound is checked first and evicts nothing."""

    store, refs = _saturated_store()

    with pytest.raises(AttachmentRefused) as caught:
        store.put("run-1", b"x" * (UNIT + 1), content_type="image/png")

    # Both the object count and the total volume are saturated as well; the
    # bound named is the first one in the specified order.
    assert caught.value.limit == "max_object_bytes"
    assert "max_object_bytes" in str(caught.value)
    _assert_intact(store, refs)


def test_total_volume_is_refused_by_name_before_the_per_run_quota() -> None:
    """R6: with room for more objects, the total volume still refuses, by name."""

    store = _store(
        max_objects=8,
        max_total_bytes=UNIT,
        max_object_bytes=UNIT,
        max_bytes_per_run=4 * UNIT,
    )
    refs = [
        store.put("run-1", _payload(0.5), content_type="image/png"),
        store.put("run-1", _payload(0.5), content_type="image/png"),
    ]

    with pytest.raises(AttachmentRefused) as caught:
        store.put("run-1", b"x", content_type="image/png")

    assert caught.value.limit == "max_total_bytes"
    assert "max_total_bytes" in str(caught.value)
    _assert_intact(store, refs)


def test_ending_the_run_releases_its_two_references() -> None:
    """AC23 (R6): a run's end frees exactly its objects and bytes."""

    store, refs = _saturated_store()

    # Refusals leave nothing behind to release.
    with pytest.raises(AttachmentRefused):
        store.put("run-1", _payload(0.5), content_type="image/png")

    assert store.release("run-1") == RunUsage(objects=2, total_bytes=UNIT)
    assert store.object_count == 0
    assert store.total_bytes == 0
    assert store.usage("run-1") == RunUsage(objects=0, total_bytes=0)
    assert store.live_runs() == ()

    # The freed space is usable, and a released reference reads as an explicit
    # error rather than as empty content.
    for ref in refs:
        with pytest.raises(AttachmentUnknown):
            store.get(ref)
    reborn = store.put("run-2", _payload(1), content_type="image/png")
    assert store.get(reborn) == _payload(1)


def test_reading_a_reference_after_its_ttl_raises_rather_than_returning_empty() -> None:
    """AC23 (R6): expiry is an explicit read error, never empty content."""

    clock = _Clock()
    store = _store(ttl_seconds=10.0, clock=clock)
    ref = store.put("run-1", _payload(1), content_type="image/png")

    clock.now = 9.5
    assert store.get(ref) == _payload(1)

    clock.now = 10.0
    with pytest.raises(AttachmentExpired) as caught:
        store.get(ref)
    assert ref.attachment_id in str(caught.value)
    assert "time-to-live" in str(caught.value)


def test_expiry_and_release_never_free_the_same_bytes_twice() -> None:
    """R6: the reaper and ``release`` share one accounting path."""

    clock = _Clock()
    store = _store(ttl_seconds=10.0, max_bytes_per_run=2 * UNIT, clock=clock)
    ref = store.put("run-1", _payload(1), content_type="image/png")

    clock.now = 11.0
    with pytest.raises(AttachmentExpired):
        store.get(ref)

    # The expired object was accounted for once, when it was reaped.
    assert store.total_bytes == 0
    assert store.object_count == 0
    assert store.usage("run-1") == RunUsage(objects=0, total_bytes=0)
    assert store.release("run-1") == RunUsage(objects=0, total_bytes=0)
    assert store.total_bytes == 0

    # And the quota is intact rather than doubly credited: 2 units fit, a
    # third is refused.
    store.put("run-1", _payload(1), content_type="image/png")
    store.put("run-1", _payload(1), content_type="image/png")
    assert store.total_bytes == 2 * UNIT
    with pytest.raises(AttachmentRefused) as caught:
        store.put("run-1", b"x", content_type="image/png")
    assert caught.value.limit == "max_bytes_per_run"


# --------------------------------------------------------------------------- #
# Attachment store: per-run quotas are independent (R6, AC31)
# --------------------------------------------------------------------------- #


def _quota_store(clock: _Clock | None = None) -> AttachmentStore:
    """A store whose per-run quota is 2 units and whose total volume is 4."""

    return _store(
        max_objects=8,
        max_object_bytes=UNIT,
        max_total_bytes=4 * UNIT,
        max_bytes_per_run=2 * UNIT,
        clock=clock or _Clock(),
    )


def test_per_run_quota_refuses_by_name_while_a_concurrent_run_still_stores() -> None:
    """AC31 (R6): quotas are keyed by run, so one run cannot starve another."""

    store = _quota_store()
    held = [
        store.put("run-a", _payload(1), content_type="image/png"),
        store.put("run-a", _payload(1), content_type="image/png"),
    ]
    assert store.usage("run-a") == RunUsage(objects=2, total_bytes=2 * UNIT)

    # The store has 2 of its 4 units free, but this run has none of its 2.
    with pytest.raises(AttachmentRefused) as caught:
        store.put("run-a", _payload(1), content_type="image/png")
    assert caught.value.limit == "max_bytes_per_run"
    assert "max_bytes_per_run" in str(caught.value)

    # 0 additional bytes stored, 0 leased references evicted.
    assert store.usage("run-a") == RunUsage(objects=2, total_bytes=2 * UNIT)
    _assert_intact(store, held)

    # A concurrent run holding nothing stores the very same object.
    other = store.put("run-b", _payload(1), content_type="image/png")
    assert store.get(other) == _payload(1)
    assert store.usage("run-b") == RunUsage(objects=1, total_bytes=UNIT)
    assert store.total_bytes == 3 * UNIT
    assert sorted(store.live_runs()) == ["run-a", "run-b"]


def test_releasing_one_run_frees_exactly_its_units_and_restores_its_quota() -> None:
    """AC31 (R6): a run's end frees its 2 units, and only its own."""

    store = _quota_store()
    store.put("run-a", _payload(1), content_type="image/png")
    store.put("run-a", _payload(1), content_type="image/png")
    other = store.put("run-b", _payload(1), content_type="image/png")
    assert store.total_bytes == 3 * UNIT

    assert store.release("run-a") == RunUsage(objects=2, total_bytes=2 * UNIT)
    assert store.total_bytes == UNIT
    assert store.object_count == 1
    assert store.usage("run-a") == RunUsage(objects=0, total_bytes=0)

    # The other run is untouched: its reference still resolves.
    assert store.usage("run-b") == RunUsage(objects=1, total_bytes=UNIT)
    assert store.get(other) == _payload(1)

    # The same run identity can store up to its quota again.
    store.put("run-a", _payload(1), content_type="image/png")
    store.put("run-a", _payload(1), content_type="image/png")
    assert store.usage("run-a") == RunUsage(objects=2, total_bytes=2 * UNIT)
    with pytest.raises(AttachmentRefused) as caught:
        store.put("run-a", b"x", content_type="image/png")
    assert caught.value.limit == "max_bytes_per_run"


def test_releasing_an_unknown_run_frees_nothing() -> None:
    """R6: release is idempotent; a second call frees 0 objects and 0 bytes."""

    store = _quota_store()
    store.put("run-a", _payload(1), content_type="image/png")

    assert store.release("run-b") == RunUsage(objects=0, total_bytes=0)
    assert store.total_bytes == UNIT
    assert store.release("run-a") == RunUsage(objects=1, total_bytes=UNIT)
    assert store.release("run-a") == RunUsage(objects=0, total_bytes=0)
    assert store.total_bytes == 0


# --------------------------------------------------------------------------- #
# Attachment store: construction and handle validation (R6)
# --------------------------------------------------------------------------- #


_VALID_ATTACHMENT_LIMITS = {
    "max_object_bytes": 5 * UNIT,
    "max_objects": 16,
    "max_total_bytes": 32 * UNIT,
    "max_bytes_per_run": 8 * UNIT,
    "ttl_seconds": 60.0,
}


@pytest.mark.parametrize("setting", sorted(_VALID_ATTACHMENT_LIMITS))
@pytest.mark.parametrize(
    "invalid",
    [None, float("inf"), float("-inf"), float("nan"), 0, -1, "10"],
)
def test_absent_or_non_finite_attachment_limit_is_rejected_by_name(
    setting: str, invalid: object
) -> None:
    """R6: all five limits are required and validated finite, by name."""

    assert AttachmentStore(**_VALID_ATTACHMENT_LIMITS) is not None  # type: ignore[arg-type]

    limits = dict(_VALID_ATTACHMENT_LIMITS, **{setting: invalid})
    with pytest.raises(ContractError) as caught:
        AttachmentStore(**limits)  # type: ignore[arg-type]

    assert setting in str(caught.value)


@pytest.mark.parametrize("setting", sorted(_VALID_ATTACHMENT_LIMITS))
def test_every_attachment_limit_is_required(setting: str) -> None:
    """R6: none of the five limits has a default to fall back on."""

    limits = {
        name: value
        for name, value in _VALID_ATTACHMENT_LIMITS.items()
        if name != setting
    }
    with pytest.raises(TypeError) as caught:
        AttachmentStore(**limits)  # type: ignore[arg-type]

    assert setting in str(caught.value)


@pytest.mark.parametrize(
    ("run_id", "data", "content_type"),
    [
        ("", b"x", "image/png"),
        (None, b"x", "image/png"),
        ("run-1", "not bytes", "image/png"),
        ("run-1", None, "image/png"),
        ("run-1", b"x", ""),
        ("run-1", b"x", None),
    ],
)
def test_put_validates_its_arguments(
    run_id: object, data: object, content_type: object
) -> None:
    store = _store()
    with pytest.raises(ContractError):
        store.put(run_id, data, content_type=content_type)  # type: ignore[arg-type]


def test_get_refuses_anything_that_is_not_a_reference() -> None:
    """R6: the handle is the only way in; an identifier string is not one."""

    store = _store()
    ref = store.put("run-1", b"x", content_type="image/png")

    with pytest.raises(ContractError) as caught:
        store.get(ref.attachment_id)  # type: ignore[arg-type]
    assert "AttachmentRef" in str(caught.value)


def test_stored_bytes_are_copied_and_references_are_distinct() -> None:
    """R6: the store owns its bytes, and no two references share an identity."""

    store = _store()
    buffer = bytearray(b"observed")
    ref = store.put("run-1", buffer, content_type="application/octet-stream")
    buffer[0:1] = b"X"

    assert store.get(ref) == b"observed"

    other = store.put("run-1", b"observed", content_type="application/octet-stream")
    assert other.attachment_id != ref.attachment_id
    assert store.get(other) == b"observed"


def test_an_over_sized_buffer_is_refused_before_it_is_copied() -> None:
    """R6: a refusal costs a measurement, never a copy of the whole buffer.

    Copying first would let a caller offering a buffer far past
    ``max_object_bytes`` cost the store that allocation anyway — the memory
    exhaustion the bound exists to prevent.
    """

    store = _store(max_object_bytes=UNIT)
    copies: list[int] = []
    original = attachments._own_payload

    def _counting_copy(value: object) -> bytes:
        copies.append(1)
        return original(value)  # type: ignore[arg-type]

    attachments._own_payload = _counting_copy  # type: ignore[assignment]
    try:
        for oversized in (
            bytearray(UNIT + 1),
            memoryview(bytearray(UNIT + 1)),
        ):
            with pytest.raises(AttachmentRefused) as caught:
                store.put("run-1", oversized, content_type="image/png")
            assert caught.value.limit == "max_object_bytes"
        assert copies == []

        # The bound cleared, the copy is taken exactly once.
        store.put("run-1", bytearray(UNIT), content_type="image/png")
        assert copies == [1]
    finally:
        attachments._own_payload = original  # type: ignore[assignment]


def test_a_multi_byte_memoryview_is_measured_in_bytes_not_items() -> None:
    """R6: a view's byte volume is what the store owns, whatever its items."""

    view = memoryview(array("I", [1, 2, 3, 4]))
    assert len(view) == 4 and view.nbytes == 16

    refusing = _store(max_object_bytes=8)
    with pytest.raises(AttachmentRefused) as caught:
        refusing.put("run-1", view, content_type="application/octet-stream")
    assert caught.value.limit == "max_object_bytes"

    accepting = _store(max_object_bytes=16)
    ref = accepting.put("run-1", view, content_type="application/octet-stream")
    assert ref.size == 16
    assert accepting.total_bytes == 16
    assert accepting.get(ref) == view.tobytes()


def test_a_factory_that_repeats_an_identifier_is_refused_by_name() -> None:
    """R6: an opaque reference must be unique; a colliding factory raises."""

    store = _store(id_factory=lambda: "always-the-same")
    store.put("run-1", b"x", content_type="image/png")

    with pytest.raises(ContractError) as caught:
        store.put("run-1", b"y", content_type="image/png")
    assert "id_factory" in str(caught.value)
