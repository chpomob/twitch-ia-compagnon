"""The retention surface, end to end, on an injected clock (R6).

Every retained state the runtime holds is bounded, and every bound is
exercised here against the component that owns it *as the application wires
it* — never against a test-built stand-in with a friendlier default:

* the bus publication history, by events, bytes and age, with the
  subscription, ordering, replacement and publication-error semantics intact
  under saturation (AC20);
* the audit record queue, saturated under a writer held blocked, losing
  records and never publications, with the loss counter read outside the
  queue (AC21);
* the source dedup window, driven through the real chat input over a fake
  socket: a replay inside the window produces nothing, a replay after
  eviction by count or by time-to-live is reprocessed (AC22);
* the attachment store — refusing by design, it never drops a reference
  leased to a live run to make room — by count, per-object size, total
  volume, per-run quota and time-to-live (AC23, AC31);
* the conversation memory, driven through the real run engine so that what is
  asserted is the next model request, by sessions, exchanges, bytes and age
  (AC30);
* and startup itself, through ``core.main.run``: each of the five non-finite
  retention configurations stops before any module is activated, naming the
  offending setting, while the all-finite control starts and hands the
  modules a bus and a store bounded exactly as configured (AC32).

The chat transcript context (AC11) — lossy by design, it evicts its oldest
entries so ingestion is never refused — keeps its store-level pins here too;
its pipeline assertion lives with the integration suite.

The fakes at the module edges (the chat input's socket and session, the run
engine's model transport and send edge) are the module suites' own; they are
imported from there rather than re-implemented, so this suite drives the same
assembly those suites do — the real executor, scheduler and supervision
behind the shared ``conftest.runtime_context`` — and cannot pass on a mock
the module suites would refuse. Nothing here sleeps: every clock is injected
and every wait is a bounded number of bare loop turns.
"""

from __future__ import annotations

import asyncio
import importlib
import json
from array import array
from pathlib import Path
from typing import Any, Callable

import pytest
import yaml

import core.main as application
from core import attachments
from core.attachments import (
    AttachmentExpired,
    AttachmentRef,
    AttachmentRefused,
    AttachmentStore,
    AttachmentUnknown,
    RunUsage,
)
from core.bus import EventBus, PublicationError
from core.context import ChatContext, ChatEntry
from core.contracts import (
    COUNTER_AUDIT_RECORD_LOSSES,
    COUNTER_DEDUP_EVICTIONS,
    ContractError,
    SessionKey,
)
from conftest import (
    FakeResponse,
    ManualClock,
    RecordingScheduler,
    completion,
    runtime_context,
    wait_until,
)
from test_brain import (
    CHANNEL as BRAIN_CHANNEL,
    PLATFORM as BRAIN_PLATFORM,
    activate_with as activate_brain,
    rendered_history,
)
from test_twitch import (
    PLATFORM as TWITCH_PLATFORM,
    SETTINGS as TWITCH_SETTINGS,
    FakeSession as TwitchSession,
    FakeWebSocket,
    activate_with as activate_twitch,
    chat_events,
    module_context as twitch_context,
    notification,
    welcome,
)


audit = importlib.import_module("modules.audit")


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


def test_ac23_a_third_object_is_refused_naming_the_object_count_and_evicts_nothing() -> None:
    """AC23 (R6): the store refuses rather than dropping a live run's lease."""

    store, refs = _saturated_store()

    with pytest.raises(AttachmentRefused) as caught:
        store.put("run-1", _payload(0.5, b"c"), content_type="image/png")

    assert caught.value.limit == "max_objects"
    assert "max_objects" in str(caught.value)
    _assert_intact(store, refs)


def test_ac23_an_over_sized_object_is_refused_naming_the_per_object_limit() -> None:
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


def test_ac23_ending_the_run_releases_its_two_references() -> None:
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


def test_ac23_reading_a_reference_after_its_ttl_raises_rather_than_returning_empty() -> None:
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


def test_ac31_per_run_quota_refuses_by_name_while_a_concurrent_run_still_stores() -> None:
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


def test_ac31_releasing_one_run_frees_exactly_its_units_and_restores_its_quota() -> None:
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


# --------------------------------------------------------------------------- #
# Bus history bounded by events, bytes and age (R6, AC20)
# --------------------------------------------------------------------------- #


_GENEROUS_HISTORY = {
    "history_max_events": 1000,
    "history_max_bytes": 1 << 20,
    "history_max_age_seconds": 1000.0,
}


def _bus(clock: ManualClock, **overrides: object) -> EventBus:
    return EventBus(clock=clock, **dict(_GENEROUS_HISTORY, **overrides))  # type: ignore[arg-type]


def _history_types(bus: EventBus, prefix: str = "retention.") -> list[str]:
    return [event["type"] for event in bus.list_events() if event["type"].startswith(prefix)]


def _serialized_bytes(event: dict[str, Any]) -> int:
    """The byte weight the bus charges a JSON-representable event."""

    return len(json.dumps(event, ensure_ascii=False).encode("utf-8"))


async def test_ac20_bus_history_event_limit_keeps_the_newest_ten_and_invokes_every_subscriber_in_order() -> None:
    """AC20 (R6): 15 publications under a 10-event history leave exactly 10
    records, the 5 oldest absent, while every one of the 15 still ran its
    full subscriber chain in the documented order — by ``order`` first, then
    by registration, sync and async alike — and none of them failed."""

    clock = ManualClock(0.0)
    bus = _bus(clock, history_max_events=10)
    seen: list[tuple[str, str]] = []

    async def async_handler(event: dict[str, Any]) -> None:
        seen.append(("middle", event["type"]))

    bus.subscribe("retention.*", lambda event: seen.append(("late", event["type"])), order=10)
    bus.subscribe("retention.**", async_handler, order=5)
    bus.subscribe("retention.*", lambda event: seen.append(("early", event["type"])), order=0)

    published = []
    for index in range(15):
        clock.now = float(index)
        published.append(await bus.publish(f"retention.{index}", {"index": index}, {}))

    assert [event["type"] for event in published] == [f"retention.{i}" for i in range(15)]
    assert _history_types(bus) == [f"retention.{i}" for i in range(5, 15)]
    assert seen == [
        (label, f"retention.{index}")
        for index in range(15)
        for label in ("early", "middle", "late")
    ]


async def test_ac20_bus_history_byte_limit_evicts_before_the_count_limit_without_failing_a_publication() -> None:
    """AC20 (R6): a byte bound reached before the 10-event bound leaves fewer
    than 10 records — the newest, within the bound — and no publication
    fails: every returned record is the event that was published."""

    clock = ManualClock(0.0)
    probe = {"type": "retention.0", "payload": {"index": 0}, "metadata": {}}
    # Room for a handful of records, well under the 10-event bound.
    byte_limit = _serialized_bytes(probe) * 4
    bus = _bus(clock, history_max_events=10, history_max_bytes=byte_limit)
    seen: list[str] = []
    bus.subscribe("retention.*", lambda event: seen.append(event["type"]))

    for index in range(15):
        returned = await bus.publish(f"retention.{index}", {"index": index}, {})
        assert returned == {
            "type": f"retention.{index}",
            "payload": {"index": index},
            "metadata": {},
        }

    retained = bus.list_events()
    assert 0 < len(retained) < 10
    assert sum(_serialized_bytes(event) for event in retained) <= byte_limit
    # Oldest first: what survives is a suffix of what was published.
    suffix = [f"retention.{i}" for i in range(15 - len(retained), 15)]
    assert _history_types(bus) == suffix
    assert seen == [f"retention.{i}" for i in range(15)]


async def test_ac20_bus_history_age_limit_evicts_on_publish_and_on_read_with_the_injected_clock() -> None:
    """R6: the age bound is applied when a record is appended and again when
    the history is read, on the injected clock, so a stale record is never
    returned even when nothing was published since it went stale."""

    clock = ManualClock(0.0)
    bus = _bus(clock, history_max_age_seconds=10.0)

    for index in range(15):
        clock.now = float(index)
        await bus.publish(f"retention.{index}", {"index": index}, {})

    # At t=14 the horizon is t=4: records dated strictly before it are gone.
    assert _history_types(bus) == [f"retention.{i}" for i in range(4, 15)]

    clock.now = 30.0
    assert bus.list_events() == []

    clock.now = 31.0
    await bus.publish("retention.late", {}, {})
    assert _history_types(bus) == ["retention.late"]


async def test_ac20_saturated_history_keeps_replacement_and_publication_error_semantics() -> None:
    """R6: eviction changes what is retained, never what a publication means —
    a replacement mapping is what the chain continues with and what history
    keeps, ``False`` still stops the chain, and a failing handler still fails
    the publication without touching the records already retained."""

    clock = ManualClock(0.0)
    bus = _bus(clock, history_max_events=2)
    reached: list[str] = []

    def replace(event: dict[str, Any]) -> dict[str, Any] | bool | None:
        if event["type"] == "retention.replaced":
            return {"type": "retention.replaced", "payload": {"replaced": True}, "metadata": {}}
        if event["type"] == "retention.stopped":
            return False
        if event["type"] == "retention.failing":
            raise RuntimeError("handler failure")
        return None

    bus.subscribe("retention.*", replace, order=0)
    bus.subscribe("retention.*", lambda event: reached.append(event["type"]), order=1)

    await bus.publish("retention.0", {}, {})
    await bus.publish("retention.1", {}, {})
    await bus.publish("retention.replaced", {"replaced": False}, {})
    assert _history_types(bus) == ["retention.1", "retention.replaced"]
    assert bus.list_events()[-1]["payload"] == {"replaced": True}

    await bus.publish("retention.stopped", {}, {})
    assert _history_types(bus) == ["retention.replaced", "retention.stopped"]
    assert reached == ["retention.0", "retention.1", "retention.replaced"]

    with pytest.raises(PublicationError) as caught:
        await bus.publish("retention.failing", {}, {})
    assert caught.value.event_type == "retention.failing"
    assert _history_types(bus) == ["retention.replaced", "retention.stopped"]

    # The bus is not poisoned: the next publication is retained as usual.
    await bus.publish("retention.after", {}, {})
    assert _history_types(bus) == ["retention.stopped", "retention.after"]


# --------------------------------------------------------------------------- #
# Audit queue: loss counter under a blocked writer (R6, AC21)
# --------------------------------------------------------------------------- #


async def test_ac21_audit_queue_of_two_under_a_blocked_writer_loses_records_never_publications() -> None:
    """AC21 (R6): with a record capacity of 2 and the writer held blocked,
    5 publications all complete, at most 2 records reach the writer, the
    loss counter reads 3 — from the handle and from supervision's in-memory
    snapshot, outside the queue, never through a bus event that would feed
    it — and 0 publications are lost: every one of the 5 reached the stage
    after the audit middleware and every one of the 5 is in the history."""

    context = runtime_context()
    bus = context.bus
    entered = asyncio.Event()
    release = asyncio.Event()
    handed: list[str] = []

    async def blocked_writer(line: str) -> None:
        handed.append(line)
        entered.set()
        await release.wait()

    handle = await audit.activate(
        context.for_module("audit"),
        {
            "output": "stdout",
            "_writer": blocked_writer,
            "queue": {"max_records": 2, "max_bytes": 4096},
        },
        {},
    )
    downstream: list[str] = []
    try:
        await handle.prepare()
        # Registered after the middleware (order 90): reached only when the
        # audit stage returned the event, whatever the queue did with it.
        bus.subscribe("retention.**", lambda event: downstream.append(event["type"]), order=100)

        completed = []
        for index in range(5):
            completed.append(await bus.publish(f"retention.audit.{index}", {"index": index}, {}))
        expected = [f"retention.audit.{i}" for i in range(5)]

        assert [event["type"] for event in completed] == expected
        assert downstream == expected
        assert _history_types(bus) == expected

        await wait_until(entered.is_set)
        assert len(handed) <= 2
        assert handle.pending == 2
        assert handle.losses == 3
        assert context.supervision.snapshot()[COUNTER_AUDIT_RECORD_LOSSES] == 3
        # The counter was reported without a single additional publication.
        assert len(bus.list_events()) == 5
    finally:
        release.set()
        await handle.close()

    # Released at close, the writer wrote what was admitted: at most 2.
    assert len(handed) <= 2
    assert handle.losses == 3


# --------------------------------------------------------------------------- #
# Dedup window: eviction by count and time-to-live, replay after eviction
# (R6, AC22)
# --------------------------------------------------------------------------- #


async def test_ac22_dedup_window_of_two_entries_replays_only_after_count_or_ttl_eviction() -> None:
    """AC22 (R6): through the real chat input over a fake socket, with a
    2-entry window and an injected clock — a replay inside the window
    produces 0 additional events and 0 admissions; a replay after 3 newer
    identifiers, or after the time-to-live, produces exactly 1 new event and
    1 admission; the window never holds more than 2 entries."""

    clock = ManualClock(0.0)
    scheduler = RecordingScheduler()
    context = twitch_context(
        clock=clock, dedup_max_entries=2, dedup_ttl_seconds=10.0, scheduler=scheduler
    )
    bus = context.bus
    websocket = FakeWebSocket(welcome("session-1"))
    handle, _, diagnostics = await activate_twitch(TwitchSession([websocket]), context=context)

    def published() -> list[str]:
        return [event["payload"]["message_id"] for event in chat_events(bus)]

    def admitted() -> list[str]:
        return [work.source_event_id for _, work in scheduler.admissions]

    def recorded(message_id: str) -> bool:
        return context.triggers.recorded(
            platform=TWITCH_PLATFORM,
            channel_id=TWITCH_SETTINGS["broadcaster_id"],
            source_event_id=message_id,
        ) is not None

    def feed(*message_ids: str) -> None:
        for message_id in message_ids:
            websocket.feed(notification(message_id, "Hello Companion"))

    try:
        feed("a")
        await wait_until(lambda: published() == ["a"])
        assert admitted() == ["a"]

        # Reception is ordered: once "b" landed, the replay of "a" queued
        # ahead of it has been decided — and it produced nothing.
        feed("a", "b")
        await wait_until(lambda: "b" in published())
        assert published() == ["a", "b"]
        assert admitted() == ["a", "b"]
        assert context.triggers.window_size == 2

        # 3 newer identifiers ("b", "c", "d") push "a" out, oldest first.
        feed("c", "d")
        await wait_until(lambda: published() == ["a", "b", "c", "d"])
        assert context.triggers.window_size == 2
        assert not recorded("a")
        assert recorded("c") and recorded("d")

        feed("a")
        await wait_until(lambda: published() == ["a", "b", "c", "d", "a"])
        assert admitted() == ["a", "b", "c", "d", "a"]
        assert context.supervision.snapshot()[COUNTER_DEDUP_EVICTIONS] == 3

        # Inside the time-to-live "e" is still recorded; 11 units later it is
        # not, and the replay is reprocessed as a new event.
        feed("e")
        await wait_until(lambda: published()[-1] == "e")
        feed("e", "f")
        await wait_until(lambda: published()[-1] == "f")
        assert published() == ["a", "b", "c", "d", "a", "e", "f"]
        assert recorded("e")

        clock.advance(11.0)
        assert not recorded("e")
        feed("e")
        await wait_until(lambda: len(published()) == 8)
        assert published() == ["a", "b", "c", "d", "a", "e", "f", "e"]
        assert admitted() == published()
        assert context.triggers.window_size == 1
        # 5 evictions by count ("a", "b", "c", "d", "a") and 2 by age ("e", "f").
        assert context.supervision.snapshot()[COUNTER_DEDUP_EVICTIONS] == 7
        assert diagnostics == []
    finally:
        await handle.close()


# --------------------------------------------------------------------------- #
# Conversation memory: sessions, exchanges, bytes and age, read from the
# next model request (R6, AC30)
# --------------------------------------------------------------------------- #


MEMORY_LIMITS = {
    "conversation_memory": {
        "max_sessions": 2,
        "max_exchanges": 3,
        "max_bytes": 200,
        "max_age_seconds": 10,
    }
}
"""AC30's bounds, handed to the engine as its own settings."""


def _replies(count: int) -> list[FakeResponse]:
    return [FakeResponse(200, completion(f"reply-{index}")) for index in range(count)]


def _brain_key(viewer_id: str) -> SessionKey:
    return SessionKey(platform=BRAIN_PLATFORM, channel_id=BRAIN_CHANNEL, viewer_id=viewer_id)


def _exchanges_in(prompt: list[dict[str, str]]) -> int:
    """Exchanges a request carries: one user and one assistant message each."""

    between = prompt[1:-1]
    assert len(between) % 2 == 0
    return len(between) // 2


async def test_ac30_session_cap_evicts_exactly_one_memory_and_the_evicted_session_starts_empty() -> None:
    """AC30 (R6): limited to 2 sessions, a third distinct session evicts
    exactly 1 memory so exactly 2 remain; the evicted session's next model
    request carries 0 earlier exchanges, while a retained session's next
    request still carries its own."""

    harness = await activate_brain(*_replies(5), settings_overrides=MEMORY_LIMITS)
    try:
        await harness.send("first question", message_id="m1", viewer_id="viewer-1")
        await harness.completed(1)
        await harness.send("second question", message_id="m2", viewer_id="viewer-2")
        await harness.completed(2)
        assert len(harness.handle.memory.sessions()) == 2

        await harness.send("third question", message_id="m3", viewer_id="viewer-3")
        await harness.completed(3)
        assert len(harness.handle.memory.sessions()) == 2
        assert harness.handle.memory.recall(_brain_key("viewer-1")) == ()

        await harness.send("first returns", message_id="m4", viewer_id="viewer-1")
        await harness.completed(4)
        assert _exchanges_in(harness.prompt(3)) == 0
        assert rendered_history(harness.prompt(3)) == ""

        await harness.send("third returns", message_id="m5", viewer_id="viewer-3")
        await harness.completed(5)
        assert _exchanges_in(harness.prompt(4)) == 1
        history = rendered_history(harness.prompt(4))
        assert "third question" in history
        assert "reply-2" in history
        assert harness.diagnostics == []
    finally:
        await harness.close()


async def test_ac30_exchange_cap_leaves_exactly_three_in_the_next_request_with_the_oldest_absent() -> None:
    """AC30 (R6): a 4th exchange in 1 session leaves exactly 3 exchanges in
    the next model request, the oldest absent and the newest 3 present.

    The exchanges are kept short — the engine prefixes the viewer and
    message identifiers into what it remembers — so that 4 of them stay
    under the 200-byte bound and the count bound is the one that evicts.
    """

    replies = [FakeResponse(200, completion(f"r{index}")) for index in range(5)]
    harness = await activate_brain(*replies, settings_overrides=MEMORY_LIMITS)
    key = _brain_key("v")
    try:
        for index in range(4):
            await harness.send(f"q{index}", message_id=str(index), viewer_id="v")
            await harness.completed(index + 1)
        retained = harness.handle.memory.recall(key)
        assert sum(exchange.size for exchange in retained) < 200
        assert [exchange.assistant for exchange in retained] == ["r1", "r2", "r3"]

        await harness.send("q4", message_id="4", viewer_id="v")
        await harness.completed(5)

        assert _exchanges_in(harness.prompt(4)) == 3
        history = rendered_history(harness.prompt(4))
        assert "message: q0" not in history
        assert "r0" not in history
        for index in (1, 2, 3):
            assert f"message: q{index}" in history
            assert f"r{index}" in history
        assert harness.diagnostics == []
    finally:
        await harness.close()


async def test_ac30_byte_cap_leaves_fewer_than_three_exchanges_and_at_most_two_hundred_bytes() -> None:
    """AC30 (R6): exchanges totalling more than 200 bytes leave fewer than 3
    retained and at most 200 bytes, the newest kept and the oldest gone."""

    replies = [f"reply-{index}-" + "r" * 30 for index in range(4)]
    harness = await activate_brain(
        *(FakeResponse(200, completion(reply)) for reply in replies),
        settings_overrides=MEMORY_LIMITS,
    )
    key = _brain_key("viewer-4")
    try:
        for index in range(3):
            await harness.send(f"question-{index}-" + "q" * 30, message_id=f"m{index}")
            await harness.completed(index + 1)

        # What was offered: each request's user content plus its confirmed
        # reply, weighed as the memory weighs them — more than 200 bytes.
        offered = sum(
            len(harness.prompt(index)[-1]["content"].encode("utf-8"))
            + len(replies[index].encode("utf-8"))
            for index in range(3)
        )
        assert offered > 200

        retained = harness.handle.memory.recall(key)
        assert 0 < len(retained) < 3
        assert sum(exchange.size for exchange in retained) <= 200
        assert retained[-1].assistant.startswith("reply-2-")

        await harness.send("question-3", message_id="m3")
        await harness.completed(4)
        assert 0 < _exchanges_in(harness.prompt(3)) < 3
        history = rendered_history(harness.prompt(3))
        assert "reply-2-" in history
        assert "reply-0-" not in history
        assert harness.diagnostics == []
    finally:
        await harness.close()


async def test_ac30_age_bound_drops_every_exchange_after_eleven_units_and_keeps_a_later_one() -> None:
    """AC30 (R6): advancing the injected clock by 11 time units leaves 0
    retained exchanges for the session — its next model request carries
    none — while the exchange added afterwards is retained."""

    harness = await activate_brain(*_replies(4), settings_overrides=MEMORY_LIMITS)
    key = _brain_key("viewer-4")
    try:
        await harness.send("old question", message_id="m0")
        await harness.completed(1)
        await harness.send("older question", message_id="m1")
        await harness.completed(2)
        assert len(harness.handle.memory.recall(key)) == 2

        harness.clock.advance(11.0)
        assert harness.handle.memory.recall(key) == ()

        await harness.send("new question", message_id="m2")
        await harness.completed(3)
        assert _exchanges_in(harness.prompt(2)) == 0

        retained = harness.handle.memory.recall(key)
        assert [exchange.assistant for exchange in retained] == ["reply-2"]
        await harness.send("newer question", message_id="m3")
        await harness.completed(4)
        assert _exchanges_in(harness.prompt(3)) == 1
        assert "new question" in rendered_history(harness.prompt(3))
        assert harness.diagnostics == []
    finally:
        await harness.close()


# --------------------------------------------------------------------------- #
# Startup: non-finite retention limits are refused through core.main.run
# (R6, AC32)
# --------------------------------------------------------------------------- #


PROBE_MODULE_SOURCE = '''
"""A fictional input that probes the retention the application wired.

It records, in the log configuration names, when it was activated, when its
transport phase ran, and what the bus and the attachment store it was handed
actually retained — so the suite reads the wired bounds, not a component it
built itself.
"""

import json
from pathlib import Path


def record(settings, entry):
    with Path(settings["log"]).open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(entry, sort_keys=True) + "\\n")


class Handle:
    def __init__(self, context, settings):
        self.context = context
        self.settings = settings
        self.seen = []

    async def prepare(self):
        self.context.bus.subscribe(
            "retention.probe.*", lambda event: self.seen.append(event["type"])
        )

    async def start_inputs(self):
        # The phase in which a real input opens its transport.
        record(self.settings, {"transport": "opened"})
        bus = self.context.bus
        for index in range(15):
            await bus.publish("retention.probe.%d" % index, {"index": index}, {})
        retained = [
            event["type"]
            for event in bus.list_events()
            if event["type"].startswith("retention.probe.")
        ]
        store = self.context.attachments
        refusals = []
        for _ in range(3):
            try:
                store.put("probe-run", b"x" * 512, content_type="application/octet-stream")
            except Exception as exc:
                refusals.append(getattr(exc, "limit", type(exc).__name__))
        released = store.release("probe-run")
        record(
            self.settings,
            {
                "retained": retained,
                "seen": list(self.seen),
                "refusals": refusals,
                "released_objects": released.objects,
            },
        )

    async def stop_inputs(self):
        return None

    async def close(self):
        return None


async def activate(context, settings, catalog):
    record(settings, {"activated": context.module})
    return Handle(context, settings)
'''


def _finite_limits() -> dict[str, dict[str, Any]]:
    """Every retention and admission limit, finite and positive (AC32)."""

    return {
        "bus_history": {"max_events": 10, "max_bytes": 65536, "max_age_seconds": 60},
        "observation_queue": {"max_records": 64, "max_bytes": 65536},
        "dedup": {"max_entries": 32, "ttl_seconds": 30.0},
        "attachments": {
            "max_object_bytes": 1024,
            "max_objects": 2,
            "max_total_bytes": 4096,
            "max_bytes_per_run": 2048,
            "ttl_seconds": 30.0,
        },
        "conversation_memory": {
            "max_sessions": 4,
            "max_exchanges": 6,
            "max_bytes": 4096,
            "max_age_seconds": 120.0,
        },
        "chat_context": {
            "max_messages": 16,
            "max_bytes": 4096,
            "max_age_seconds": 60.0,
            "max_channels": 4,
        },
        "admission": {
            "session_queue_capacity": 2,
            "global_pending_capacity": 8,
            "max_sessions": 4,
            "workers": 2,
            "wait_seconds": 5.0,
            "total_run_seconds": 20.0,
        },
    }


def _write_probe_application(tmp_path: Path) -> tuple[Path, Path, dict[str, Any]]:
    """A modules directory holding the probe input, and the configuration
    that enables it with every limit finite; the caller mutates the limits."""

    modules = tmp_path / "modules"
    directory = modules / "probe"
    directory.mkdir(parents=True)
    manifest = {
        "name": "probe",
        "manifest_version": 2,
        "runtime_api": 2,
        "produces": ["retention.probe.*"],
        "consumes": [],
        "middleware": False,
        "lifecycle": {"roles": ["input"]},
    }
    (directory / "module.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    (directory / "__init__.py").write_text(PROBE_MODULE_SOURCE, encoding="utf-8")
    log = tmp_path / "probe.jsonl"
    config: dict[str, Any] = {
        "modules_directory": "./modules",
        "enabled_modules": ["probe"],
        "modules": {"probe": {"log": str(log)}},
        "limits": _finite_limits(),
    }
    return tmp_path / "config.yaml", log, config


def _probe_log(log: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


@pytest.mark.parametrize(
    ("mutate", "setting"),
    [
        pytest.param(
            lambda limits: limits["bus_history"].pop("max_events"),
            "limits.bus_history.max_events",
            id="absent-bus-history-event-limit",
        ),
        pytest.param(
            lambda limits: limits["observation_queue"].__setitem__("max_bytes", -1),
            "limits.observation_queue.max_bytes",
            id="negative-audit-queue-byte-limit",
        ),
        pytest.param(
            lambda limits: limits["dedup"].__setitem__("ttl_seconds", "soon"),
            "limits.dedup.ttl_seconds",
            id="non-numeric-dedup-ttl",
        ),
        pytest.param(
            lambda limits: limits["attachments"].__setitem__("max_total_bytes", float("inf")),
            "limits.attachments.max_total_bytes",
            id="infinite-attachment-total-volume",
        ),
        pytest.param(
            lambda limits: limits["conversation_memory"].pop("max_exchanges"),
            "limits.conversation_memory.max_exchanges",
            id="absent-conversation-memory-exchange-limit",
        ),
    ],
)
async def test_ac32_each_non_finite_retention_limit_stops_startup_naming_it_with_zero_transports(
    tmp_path: Path, mutate: Callable[[dict[str, Any]], object], setting: str
) -> None:
    """AC32 (R6): through ``core.main.run``, one non-finite retention limit
    at a time stops startup with a non-zero status and exactly 1 diagnostic
    naming that setting; readiness is never reported, 0 modules are
    activated and 0 transports are opened."""

    config_path, log, config = _write_probe_application(tmp_path)
    mutate(config["limits"])
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    readiness: list[str] = []
    diagnostics: list[str] = []
    # Already set: a startup that wrongly got past validation would stop at
    # once with status 0 and fail below, rather than wait here forever.
    stop = asyncio.Event()
    stop.set()

    status = await application.run(
        config_path,
        stop,
        ready_reporter=readiness.append,
        diagnostic_reporter=diagnostics.append,
    )

    assert status != 0
    assert readiness == []
    assert len(diagnostics) == 1
    assert diagnostics[0].startswith(f"{setting}: ")
    # Neither activation nor the transport phase left a trace.
    assert not log.exists()


async def test_ac32_all_finite_retention_limits_start_and_wire_the_configured_bounds(
    tmp_path: Path,
) -> None:
    """AC32's control (R6): the same configuration with every limit finite
    and positive starts, reports readiness and stops cleanly — and the bus
    and the attachment store the module was handed are bounded exactly as
    configured, not at a built-in default: 15 publications leave the 10
    newest records while all 15 reached the subscriber, and a 2-object store
    refuses the third object by name and releases 2 at the run's end."""

    config_path, log, config = _write_probe_application(tmp_path)
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    stop = asyncio.Event()
    stop.set()
    readiness: list[str] = []

    status = await application.run(
        config_path,
        stop,
        ready_reporter=readiness.append,
        diagnostic_reporter=lambda message: pytest.fail(message),
    )

    assert status == 0
    assert readiness == ["ready"]
    assert _probe_log(log) == [
        {"activated": "probe"},
        {"transport": "opened"},
        {
            "retained": [f"retention.probe.{index}" for index in range(5, 15)],
            "seen": [f"retention.probe.{index}" for index in range(15)],
            "refusals": ["max_objects"],
            "released_objects": 2,
        },
    ]
