"""Bounded per-channel chat transcript context (R3, R6, AC11).

The full AC11 pipeline assertion — three real ingestions through triggers,
admission and a stale run — is added in P22; these tests pin the store itself:
appends are total, reads are ordered, dated and channel-isolated, every bound
evicts oldest first against an injected clock, and a non-finite bound is
refused at construction by name.
"""

import pytest

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
