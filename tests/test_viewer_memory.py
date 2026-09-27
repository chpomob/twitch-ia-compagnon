"""The ``viewer_memory`` store and module (phase 3 R3; AC13–AC18, AC19 chat half),
its offline command (AC19 CLI half), and its ``memory.recall`` /
``memory.record`` actions through the executor (phase 3 R4; AC16 recall half,
AC22 module half).

The store API is driven directly on an injected wall clock; the module goes
through a :func:`~conftest.runtime_context` (or the loader and the phase
coordinator, for readiness and the chat path) on a
:class:`~conftest.ManualClock` whose ``sleep`` is the sweep's sleeper and
whose reading, offset to a UTC epoch, is the module's wall clock. No
positive-duration sleep.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from conftest import (
    ManualClock,
    events_of,
    memory_directory,
    runtime_context,
    settle,
    trace_texts,
)
from core.actions import AuthorizationRule
from core.contracts import ActionCall, ActionObservation, Destination, SessionKey, observation_size
from core.lifecycle import PhaseCoordinator
from core.loader import ModuleLoader
from core.runtime import RuntimeContext
from core.triggers import TriggerRegistry
from fixtures.modules import fakeplatform
from modules import viewer_memory
from modules.viewer_memory.__main__ import main as memory_command
from modules.viewer_memory import (
    DEFAULT_RETENTION_DAYS,
    FACT_MEMORY_REMOVED,
    MemorySettings,
    MemoryStore,
    MemoryStoreError,
    RECALL_ACTION,
    RECORD_ACTION,
    ViewerMemoryError,
    ViewerMemoryModule,
    activate,
    format_timestamp,
    memory_file_name,
    validate_settings,
)


REPOSITORY = Path(__file__).resolve().parents[1]
FIXTURE_MODULES = REPOSITORY / "tests" / "fixtures" / "modules"
NAME_PATTERN = re.compile(r"^[0-9a-f]{64}\.json$")
#: The UTC instant the module's wall clock reads when the manual clock is 0.
WALL_EPOCH = 1_780_000_000.0
START = 1000.0
DAY = 86400.0
#: A four-byte UTF-8 character.
WIDE = "\U0001d11e"
#: The real ``os.unlink``, captured before any test patches it.
REAL_UNLINK = os.unlink


class Wall:
    """An injected wall clock a store test moves by hand."""

    def __init__(self, now: float = WALL_EPOCH) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def store_for(directory: Path, wall: Wall, **settings: Any) -> MemoryStore:
    store = MemoryStore(MemorySettings(directory=directory, **settings), wall_clock=wall)
    store.scan()
    return store


def at(offset: float) -> str:
    return format_timestamp(WALL_EPOCH + offset)


def write_memory(
    directory: Path,
    key: tuple[str, str, str],
    *,
    last_used: float = 0.0,
    use_count: int = 1,
    first_seen: float = 0.0,
    last_seen: float | None = None,
    notes: list[dict[str, Any]] | None = None,
    display_name: str = "",
) -> Path:
    """A valid memory file for *key*, its instants as offsets from the epoch."""

    record = {
        "format": 1,
        "platform": key[0],
        "channel_id": key[1],
        "viewer_id": key[2],
        "display_name": display_name,
        "first_seen": at(first_seen),
        "last_seen": at(last_used if last_seen is None else last_seen),
        "last_used": at(last_used),
        "use_count": use_count,
        "interactions": use_count,
        "notes": notes or [],
    }
    path = directory / memory_file_name(*key)
    path.write_bytes(json.dumps(record, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    return path


def notes_of(count: int, text: str) -> list[dict[str, Any]]:
    return [
        {"at": at(0.0), "viewer_text": text, "reply_text": text, "delivery": "confirmed"}
        for _ in range(count)
    ]


def removed_facts(runtime: RuntimeContext) -> list[dict[str, Any]]:
    return [event["payload"] for event in events_of(runtime.bus, FACT_MEMORY_REMOVED)]


async def module_for(
    runtime: RuntimeContext, clock: ManualClock, directory: Path, **settings: Any
) -> ViewerMemoryModule:
    return await activate(
        runtime.for_module("viewer_memory"),
        {
            "directory": str(directory),
            "_sleeper": clock.sleep,
            "_wall_clock": lambda: WALL_EPOCH + clock.now,
            **settings,
        },
        {},
    )


# -- settings ---------------------------------------------------------------- #


def test_validator_checks_ranges_the_cross_field_bound_and_unknown_keys(tmp_path: Path) -> None:
    """The bounds and defaults of R3; ``max_total_bytes >= max_file_bytes``."""

    assert validate_settings({"directory": str(tmp_path)}) == []
    defaults = MemorySettings.from_mapping({"directory": str(tmp_path)})
    assert (
        defaults.retention_days,
        defaults.max_files,
        defaults.max_file_bytes,
        defaults.max_total_bytes,
        defaults.max_notes,
        defaults.sweep_interval_seconds,
        defaults.forget_command,
    ) == (30, 1000, 4096, 16777216, 10, 3600, "!forgetme")
    assert validate_settings({"directory": str(tmp_path), "forget_command": ""}) == []

    refused = {
        "retention_days": 0,
        "max_files": 100001,
        "max_file_bytes": 511,
        "max_notes": 101,
        "sweep_interval_seconds": 59,
    }
    for field_name, value in refused.items():
        diagnostics = validate_settings({"directory": str(tmp_path), field_name: value})
        assert len(diagnostics) == 1 and repr(field_name) in diagnostics[0], field_name
    assert validate_settings({"directory": str(tmp_path), "retention_days": True}) != []
    assert validate_settings({"directory": str(tmp_path), "max_total_bytes": 1073741825}) != []

    cross = validate_settings(
        {"directory": str(tmp_path), "max_file_bytes": 8192, "max_total_bytes": 4096}
    )
    assert len(cross) == 1 and "'max_total_bytes'" in cross[0]
    # The default max_file_bytes (4096) takes part in the cross-field bound.
    assert validate_settings({"directory": str(tmp_path), "max_total_bytes": 2048}) != []
    assert validate_settings({"directory": str(tmp_path), "max_total_bytes": 4096}) == []

    assert validate_settings({}) != []
    assert validate_settings({"directory": ""}) != []
    assert validate_settings({"directory": str(tmp_path), "surprise": 1}) != []
    assert validate_settings({"directory": str(tmp_path), "forget_command": 3}) != []


@pytest.mark.asyncio
async def test_activation_refuses_invalid_settings_value_free(tmp_path: Path) -> None:
    runtime = runtime_context(clock=ManualClock(START))
    with pytest.raises(ViewerMemoryError) as raised:
        await activate(
            runtime.for_module("viewer_memory"),
            {"directory": "/secret/place", "max_file_bytes": 511},
            {},
        )
    assert "/secret/place" not in str(raised.value)


# -- AC13: names and content -------------------------------------------------- #


def test_ac13_one_hashed_file_per_key_with_the_declared_fields(tmp_path: Path) -> None:
    """AC13: recording ``v1`` on ``(twitch, c1)`` gives exactly 1 file whose
    name matches the pattern and equals the key's SHA-256 digest plus
    ``.json`` — it contains neither ``v1`` nor a non-hex channel id — whose
    content is JSON with ``format == 1`` and the key fields; ``(kick, c1)``
    gives a second, distinct file."""

    directory, json_names = memory_directory(tmp_path)
    wall = Wall()
    store = store_for(directory, wall)

    record, removed = store.record(
        "twitch", "c1", "v1", display_name="Viewer" * 20,
        note={"viewer_text": "hello", "delivery": "confirmed"},
    )
    assert removed == {}
    [name] = json_names()
    assert NAME_PATTERN.match(name)
    # The name is the hash of the key and nothing else (AC13 as clarified in
    # P27F3). ``v`` is not a hex digit, so ``v1`` can never appear; ``c1`` is
    # two hex digits that this very key's digest contains by chance
    # (``...65dc1a27...``), so for it the AC asks for the name-equals-digest
    # check, and a channel id with a non-hex character is absent from it.
    assert "v1" not in name
    expected = hashlib.sha256(
        json.dumps(["twitch", "c1", "v1"], ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()
    assert name == expected + ".json"
    assert "chan-1" not in memory_file_name("twitch", "chan-1", "v1")
    content = json.loads((directory / name).read_text(encoding="utf-8"))
    assert content["format"] == 1
    assert (content["platform"], content["channel_id"], content["viewer_id"]) == (
        "twitch", "c1", "v1",
    )
    assert set(content) == {
        "format", "platform", "channel_id", "viewer_id", "display_name", "first_seen",
        "last_seen", "last_used", "use_count", "interactions", "notes",
    }
    assert content["display_name"] == ("Viewer" * 20)[:64]
    assert content["first_seen"] == content["last_seen"] == content["last_used"] == at(0.0)
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$", content["last_seen"])
    assert (content["use_count"], content["interactions"]) == (1, 1)
    assert content["notes"] == [
        {"at": at(0.0), "viewer_text": "hello", "delivery": "confirmed"}
    ]
    assert record == content

    store.record("kick", "c1", "v1")
    names = json_names()
    assert len(names) == 2 and name in names
    assert all(NAME_PATTERN.match(item) for item in names)
    # The serialization separates the fields: a comma in a field is no collision.
    assert memory_file_name("a,b", "c", "d") != memory_file_name("a", "b,c", "d")
    assert memory_file_name("a", "b", "c") == memory_file_name("a", "b", "c")


def test_an_update_keeps_first_seen_and_counts_each_use(tmp_path: Path) -> None:
    directory, _ = memory_directory(tmp_path)
    wall = Wall()
    store = store_for(directory, wall)
    store.record("twitch", "c1", "v1", display_name="Vee")
    wall.now += 60.0
    record, _ = store.record("twitch", "c1", "v1")
    assert record["first_seen"] == at(0.0)
    assert record["last_seen"] == record["last_used"] == at(60.0)
    assert (record["use_count"], record["interactions"]) == (2, 2)
    assert record["display_name"] == "Vee"
    assert store.read("twitch", "c1", "v1") == record
    assert store.read("twitch", "c1", "v2") is None


# -- AC14: eviction order ---------------------------------------------------- #


@pytest.mark.asyncio
async def test_ac14_eviction_deletes_the_least_recent_then_least_used_with_one_fact(
    tmp_path: Path,
) -> None:
    """AC14: with ``max_files: 3`` and A, B, C at (``last_used``,
    ``use_count``) = (t10, 5), (t5, 9), (t5, 2), recording D deletes exactly
    C, leaves 3 files and publishes 1 ``memory.removed`` fact, ``evicted``,
    count 1."""

    clock = ManualClock(START)
    runtime = runtime_context(clock=clock)
    directory, json_names = memory_directory(tmp_path)
    a = write_memory(directory, ("twitch", "c1", "A"), last_used=10.0, use_count=5)
    b = write_memory(directory, ("twitch", "c1", "B"), last_used=5.0, use_count=9)
    c = write_memory(directory, ("twitch", "c1", "C"), last_used=5.0, use_count=2)
    handle = await module_for(runtime, clock, directory, max_files=3)
    await handle.prepare()
    try:
        assert removed_facts(runtime) == []
        await handle.record("twitch", "c1", "D")
        names = json_names()
        assert len(names) == 3
        assert c.name not in names and a.name in names and b.name in names
        assert memory_file_name("twitch", "c1", "D") in names
        assert removed_facts(runtime) == [{"reason": "evicted", "count": 1}]
    finally:
        await handle.close()


def test_ac14_ties_fall_to_first_seen_then_to_the_file_name(tmp_path: Path) -> None:
    """AC14: B and C tied on both keys → the older ``first_seen`` goes; tied
    on that too → the lexicographically smaller file name goes."""

    wall = Wall(WALL_EPOCH + 100.0)
    directory, json_names = memory_directory(tmp_path, "first-seen")
    write_memory(directory, ("twitch", "c1", "A"), last_used=10.0, use_count=5)
    b = write_memory(directory, ("twitch", "c1", "B"), last_used=5.0, use_count=2, first_seen=1.0)
    c = write_memory(directory, ("twitch", "c1", "C"), last_used=5.0, use_count=2, first_seen=2.0)
    store = store_for(directory, wall, max_files=3)
    _, removed = store.record("twitch", "c1", "D")
    assert removed == {"evicted": 1}
    assert b.name not in json_names() and c.name in json_names()

    directory, json_names = memory_directory(tmp_path, "name")
    write_memory(directory, ("twitch", "c1", "A"), last_used=10.0, use_count=5)
    b = write_memory(directory, ("twitch", "c1", "B"), last_used=5.0, use_count=2, first_seen=1.0)
    c = write_memory(directory, ("twitch", "c1", "C"), last_used=5.0, use_count=2, first_seen=1.0)
    smaller, larger = sorted((b.name, c.name))
    store = store_for(directory, wall, max_files=3)
    assert store.eviction_order()[:2] == [smaller, larger]
    _, removed = store.record("twitch", "c1", "D")
    assert removed == {"evicted": 1}
    assert smaller not in json_names() and larger in json_names()


# -- AC15: the total-bytes bound ---------------------------------------------- #


def test_ac15_the_total_bound_holds_and_the_target_is_never_deleted(tmp_path: Path) -> None:
    """AC15: with ``max_total_bytes: 8192`` and ``max_file_bytes: 4096``, a
    write raising the total above 8192 deletes candidates in the AC14 order
    until the total is ≤ 8192 after the write — never the file being
    written, although it is the oldest of all."""

    directory, json_names = memory_directory(tmp_path)
    wall = Wall(WALL_EPOCH + 100.0)
    target = write_memory(directory, ("twitch", "c1", "T"), last_used=1.0, use_count=1)
    x = write_memory(directory, ("twitch", "c1", "X"), last_used=2.0, notes=notes_of(4, "x" * 200))
    y = write_memory(directory, ("twitch", "c1", "Y"), last_used=3.0, notes=notes_of(4, "y" * 200))
    z = write_memory(directory, ("twitch", "c1", "Z"), last_used=4.0, notes=notes_of(4, "z" * 200))
    store = store_for(directory, wall, max_total_bytes=8192, max_file_bytes=4096)
    files, before = store.stats()
    assert files == 4 and before <= 8192
    assert store.eviction_order()[0] == target.name

    wide = WIDE * 200
    _, removed = store.record(
        "twitch", "c1", "T", note={"viewer_text": wide, "reply_text": wide, "delivery": "none"}
    )
    growth = target.stat().st_size
    assert before + growth > 8192  # the write alone would have broken the bound
    assert removed == {"evicted": 1}
    names = json_names()
    assert target.name in names
    assert x.name not in names and y.name in names and z.name in names
    total = sum((directory / name).stat().st_size for name in names)
    assert total <= 8192
    assert store.stats() == (3, total)


# -- AC16: retention ---------------------------------------------------------- #


@pytest.mark.asyncio
async def test_ac16_a_record_past_the_default_retention_is_not_returned_then_swept(
    tmp_path: Path,
) -> None:
    """AC16: with no ``retention_days`` configured (effective 30), a record
    29 days old is returned; 30 days + 1 s old it is not, and the next sweep
    deletes it with 1 ``expired`` fact."""

    clock = ManualClock(START)
    runtime = runtime_context(clock=clock)
    directory, json_names = memory_directory(tmp_path)
    handle = await module_for(runtime, clock, directory)
    assert handle.settings.retention_days == DEFAULT_RETENTION_DAYS == 30
    await handle.prepare()
    await handle.start_inputs()
    try:
        await handle.record("twitch", "c1", "v1")
        clock.advance(29 * DAY)
        assert handle.read("twitch", "c1", "v1") is not None
        await settle()
        assert len(json_names()) == 1
        assert removed_facts(runtime) == []

        clock.advance(1 * DAY + 1.0)
        # 30 d + 1 s since last_seen: never returned, even before the sweep.
        assert handle.read("twitch", "c1", "v1") is None
        await settle()
        assert json_names() == []
        assert removed_facts(runtime) == [{"reason": "expired", "count": 1}]
    finally:
        await handle.close()


def test_ac16_the_boundary_and_an_expired_file_at_prepare(tmp_path: Path) -> None:
    directory, json_names = memory_directory(tmp_path)
    write_memory(directory, ("twitch", "c1", "old"), last_used=0.0)
    write_memory(directory, ("twitch", "c1", "new"), last_used=1.0)
    wall = Wall(WALL_EPOCH + 30 * DAY)
    store = MemoryStore(MemorySettings(directory=directory), wall_clock=wall)
    assert store.scan() == {}
    # Exactly 30 days old is not older than the retention.
    assert store.read("twitch", "c1", "old") is not None
    wall.now += 0.5
    assert store.read("twitch", "c1", "old") is None
    assert store.scan() == {"expired": 1}
    assert json_names() == [memory_file_name("twitch", "c1", "new")]


# -- AC17: note and file bounds --------------------------------------------- #


def test_ac17_the_eleventh_note_drops_the_oldest(tmp_path: Path) -> None:
    directory, _ = memory_directory(tmp_path)
    store = store_for(directory, Wall(), max_notes=10)
    for index in range(11):
        record, _ = store.record(
            "twitch", "c1", "v1", note={"viewer_text": f"note {index}", "delivery": "none"}
        )
    assert [note["viewer_text"] for note in record["notes"]] == [
        f"note {index}" for index in range(1, 11)
    ]
    assert store.read("twitch", "c1", "v1")["notes"] == record["notes"]


def test_ac17_a_file_never_exceeds_max_file_bytes(tmp_path: Path) -> None:
    """AC17: over 50 writes of 200-character notes with ``max_file_bytes:
    1024``, every file is ≤ 1024 bytes after every write, and the newest note
    is the one kept last."""

    directory, json_names = memory_directory(tmp_path)
    wall = Wall()
    store = store_for(directory, wall, max_file_bytes=1024)
    path = store.path_of("twitch", "c1", "v1")
    for index in range(50):
        text = f"{index:03d}" + "n" * 197
        record, _ = store.record(
            "twitch", "c1", "v1",
            note={"viewer_text": text, "reply_text": "r" * 200, "delivery": "confirmed"},
        )
        wall.now += 1.0
        assert path.stat().st_size <= 1024, index
        assert record["notes"] and record["notes"][-1]["viewer_text"] == text
    assert json_names() == [path.name]


def test_ac17_a_lone_note_that_does_not_fit_is_not_stored(tmp_path: Path) -> None:
    """Plan P10 risk: 64 four-byte characters of name and a 200-character
    four-byte note at 512 bytes: the file keeps 0 notes and stays ≤ 512."""

    directory, _ = memory_directory(tmp_path)
    store = store_for(directory, Wall(), max_file_bytes=512, max_total_bytes=512)
    name = WIDE * 80
    record, _ = store.record(
        "twitch", "c1", "v1", display_name=name,
        note={"viewer_text": WIDE * 200, "delivery": "unconfirmed"},
    )
    assert store.path_of("twitch", "c1", "v1").stat().st_size <= 512
    assert record["notes"] == []
    assert name.startswith(record["display_name"]) and len(record["display_name"]) <= 64

    with pytest.raises(MemoryStoreError):
        store.record("twitch", "c1", "v" * 600)
    with pytest.raises(MemoryStoreError):
        store.record("twitch", "c1", "v2", note={"viewer_text": "x" * 201, "delivery": "none"})
    with pytest.raises(MemoryStoreError):
        store.record("twitch", "c1", "v2", note={"viewer_text": "x", "delivery": "maybe"})


def test_a_failed_eviction_stays_counted_and_refuses_the_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With ``max_files: 1``, a candidate whose deletion fails stays indexed
    and on disk, and the write that needed it gone is refused: the store
    never holds more files than it reports."""

    directory, json_names = memory_directory(tmp_path)
    wall = Wall(WALL_EPOCH + 100.0)
    stuck = write_memory(directory, ("twitch", "c1", "old"), last_used=1.0)
    store = store_for(directory, wall, max_files=1)
    real_unlink = os.unlink

    def failing_unlink(path: Any, *args: Any, **kwargs: Any) -> None:
        if Path(path).name == stuck.name:
            raise PermissionError("denied")
        real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(viewer_memory.os, "unlink", failing_unlink)
    with pytest.raises(MemoryStoreError) as raised:
        store.record("twitch", "c1", "new")
    assert raised.value.removed == {}
    assert json_names() == [stuck.name]
    assert store.stats() == (1, stuck.stat().st_size)
    assert store.indexed() == (stuck.name,)

    monkeypatch.setattr(viewer_memory.os, "unlink", real_unlink)
    _, removed = store.record("twitch", "c1", "new")
    assert removed == {"evicted": 1}
    assert json_names() == [memory_file_name("twitch", "c1", "new")]


def refusing_unlink(monkeypatch: pytest.MonkeyPatch, names: set[str] | None = None) -> list[str]:
    """Make ``os.unlink`` in the store raise ``PermissionError`` — for every
    file, or for *names* only — as a directory that allows reads but not
    removal does. Returns the names whose deletion was attempted and refused."""

    real_unlink = REAL_UNLINK
    refused: list[str] = []

    def failing_unlink(path: Any, *args: Any, **kwargs: Any) -> None:
        name = Path(path).name
        if names is None or name in names:
            refused.append(name)
            raise PermissionError("denied")
        real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(viewer_memory.os, "unlink", failing_unlink)
    return refused


def test_gate1_f3_a_scan_whose_evictions_fail_refuses_instead_of_exceeding_the_bounds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gate 1 F3: 5 valid files, ``max_files: 3``, every deletion refused. The
    scan used to return ``{}`` with 5 files indexed; it now raises
    :class:`MemoryStoreError` (the bounds cannot be met), the 5 files stay
    counted — the store never reports fewer files than it holds — and the
    failures are counted. Once deletion works, the next scan evicts 2."""

    directory, json_names = memory_directory(tmp_path)
    paths = [
        write_memory(directory, ("twitch", "c1", f"v{index}"), last_used=float(index))
        for index in range(5)
    ]
    wall = Wall(WALL_EPOCH + 100.0)
    store = MemoryStore(MemorySettings(directory=directory, max_files=3), wall_clock=wall)
    refused = refusing_unlink(monkeypatch)

    with pytest.raises(MemoryStoreError, match="bounds cannot be met") as raised:
        store.scan()
    assert raised.value.removed == {}
    assert refused, "an eviction was attempted"
    assert store.failed_deletions == len(refused)
    assert len(json_names()) == 5
    assert store.stats() == (5, sum(path.stat().st_size for path in paths))

    monkeypatch.setattr(viewer_memory.os, "unlink", REAL_UNLINK)
    assert store.scan() == {"evicted": 2}
    assert json_names() == sorted(path.name for path in paths[2:])
    assert store.stats()[0] == 3


@pytest.mark.asyncio
async def test_gate1_f3_prepare_refuses_readiness_and_says_why_when_evictions_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gate 1 F3, module half: the same directory at ``prepare`` fails the
    module with :class:`ViewerMemoryError` — no memory action bound, readiness
    never advertised — after one value-free ``module.degraded`` naming the
    reason and a diagnostic; no file is deleted and no fact claims one was."""

    clock = ManualClock(START)
    runtime = runtime_context(clock=clock)
    directory, json_names = memory_directory(tmp_path)
    for index in range(5):
        write_memory(directory, ("fake", "chan-a", f"v{index}"), last_used=START + index)
    handle = await module_for(runtime, clock, directory, max_files=3)
    refusing_unlink(monkeypatch)
    try:
        with pytest.raises(ViewerMemoryError, match="bounds cannot be met"):
            await handle.prepare()
        assert len(json_names()) == 5
        assert removed_facts(runtime) == []
        degraded = [
            event["payload"] for event in events_of(runtime.bus, "module.degraded")
            if event["payload"].get("module") == "viewer_memory"
        ]
        assert [payload["reason"] for payload in degraded] == [viewer_memory.REASON_BOUNDS_NOT_MET]
        assert degraded[0]["capabilities"] == [RECALL_ACTION, RECORD_ACTION]
        assert any(viewer_memory.REASON_BOUNDS_NOT_MET in text for text in handle.diagnostics)
        for action in (RECALL_ACTION, RECORD_ACTION):
            assert runtime.actions.bindings(action) == ()
            assert action not in runtime.actions.registered_ready()
    finally:
        await handle.close()


@pytest.mark.asyncio
async def test_gate1_f3_an_undeletable_corrupt_file_stays_counted_and_is_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gate 1 F3 (accounting): a corrupt file whose deletion fails at
    ``prepare`` is not treated as absent. Within the bounds startup goes on,
    with one ``module.degraded`` saying a file could not be deleted; the file
    counts toward ``max_files`` (a write that would need its slot is refused)
    and the next sweep deletes it once deletion works (1 ``corrupt`` fact)."""

    clock = ManualClock(START)
    runtime = runtime_context(clock=clock)
    directory, json_names = memory_directory(tmp_path)
    malformed = directory / ("a" * 64 + ".json")
    malformed.write_text("{not json", encoding="utf-8")
    handle = await module_for(runtime, clock, directory, max_files=1)
    refusing_unlink(monkeypatch, {malformed.name})
    await handle.prepare()
    try:
        assert handle.store.stranded() == (malformed.name,)
        assert handle.store.stats() == (1, malformed.stat().st_size)
        degraded = [
            event["payload"]["reason"] for event in events_of(runtime.bus, "module.degraded")
            if event["payload"].get("module") == "viewer_memory"
        ]
        assert degraded == [viewer_memory.REASON_DELETION_FAILED]
        with pytest.raises(MemoryStoreError, match="bounds cannot be met"):
            await handle.record("fake", "chan-a", "v1")
        assert json_names() == [malformed.name]

        monkeypatch.setattr(viewer_memory.os, "unlink", REAL_UNLINK)
        assert await handle.sweep() == {"corrupt": 1}
        assert json_names() == []
        assert handle.store.stats() == (0, 0)
        assert removed_facts(runtime) == [{"reason": "corrupt", "count": 1}]
    finally:
        await handle.close()


def test_gate1_f3_a_failed_erasure_is_an_error_not_an_absent_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gate 1 F3 (erasure): ``forget`` of a file that cannot be deleted raises
    :class:`MemoryStoreError` instead of answering 0; the file stays indexed
    and counted, and a later ``forget`` deletes it."""

    directory, json_names = memory_directory(tmp_path)
    wall = Wall(WALL_EPOCH + 100.0)
    path = write_memory(directory, ("twitch", "c1", "v1"), last_used=1.0)
    store = store_for(directory, wall)
    refusing_unlink(monkeypatch)
    with pytest.raises(MemoryStoreError, match="could not be deleted"):
        store.forget("twitch", "c1", "v1")
    assert store.indexed() == (path.name,)
    assert store.stats() == (1, path.stat().st_size)
    assert store.failed_deletions == 1

    monkeypatch.setattr(viewer_memory.os, "unlink", REAL_UNLINK)
    assert store.forget("twitch", "c1", "v1") == 1
    assert store.forget("twitch", "c1", "v1") == 0
    assert json_names() == []


def test_gate1_f3_a_write_over_a_stranded_file_takes_its_place(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gate 1 F3 (replacement): a viewer whose corrupt file could not be
    deleted records again. The write replaces the stranded file in its slot
    (``max_files`` 1 is not exceeded, nothing is counted twice) and is no
    longer stranded: a later sweep, with deletion working, keeps it."""

    directory, json_names = memory_directory(tmp_path)
    wall = Wall(WALL_EPOCH + 100.0)
    name = memory_file_name("twitch", "c1", "v1")
    (directory / name).write_text("{not json", encoding="utf-8")
    refusing_unlink(monkeypatch, {name})
    store = store_for(directory, wall, max_files=1)
    assert store.stranded() == (name,)

    record, removed = store.record("twitch", "c1", "v1")
    assert removed == {}
    assert store.stranded() == ()
    assert store.indexed() == (name,)
    assert store.stats() == (1, (directory / name).stat().st_size)

    monkeypatch.setattr(viewer_memory.os, "unlink", REAL_UNLINK)
    assert store.sweep() == {}
    assert json_names() == [name]
    assert store.read("twitch", "c1", "v1") == record


# -- AC18: the prepare scan -------------------------------------------------- #


@pytest.mark.asyncio
async def test_ac18_prepare_deletes_corrupt_memory_files_and_ignores_the_rest(
    tmp_path: Path,
) -> None:
    """AC18: 1 malformed, 1 too deeply nested to parse and 1 oversized memory
    file are deleted (1 fact, ``corrupt``, count 3); ``notes.txt`` is unchanged; a valid file stays."""

    clock = ManualClock(START)
    runtime = runtime_context(clock=clock)
    directory, json_names = memory_directory(tmp_path)
    malformed = directory / ("a" * 64 + ".json")
    malformed.write_text("{not json", encoding="utf-8")
    oversized = write_memory(
        directory, ("twitch", "c1", "big"), last_used=START, notes=notes_of(12, "o" * 200)
    )
    assert oversized.stat().st_size > 4096
    valid = write_memory(directory, ("twitch", "c1", "ok"), last_used=START)
    unrelated = directory / "notes.txt"
    unrelated.write_bytes(b"keep me\n")
    stray = directory / "readme.json"
    stray.write_text("{not json either", encoding="utf-8")
    nested = directory / ("b" * 64 + ".json")
    nested.write_text("[" * 1500 + "]" * 1500, encoding="utf-8")

    handle = await module_for(runtime, clock, directory)
    await handle.prepare()
    try:
        assert json_names() == sorted([valid.name, "readme.json"])
        assert unrelated.read_bytes() == b"keep me\n"
        assert stray.read_text(encoding="utf-8") == "{not json either"
        assert removed_facts(runtime) == [{"reason": "corrupt", "count": 3}]
        assert handle.store.stats() == (1, valid.stat().st_size)
    finally:
        await handle.close()


def fixture_modules(root: Path) -> Path:
    """A modules directory with copies of ``viewer_memory`` and the fixture platform."""

    ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
    shutil.copytree(REPOSITORY / "modules" / "viewer_memory", root / "viewer_memory", ignore=ignore)
    shutil.copytree(FIXTURE_MODULES / "fakeplatform", root / "fakeplatform", ignore=ignore)
    return root


async def loaded(
    tmp_path: Path, clock: ManualClock, directory: Path, *, fake: dict[str, Any], memory: dict[str, Any]
) -> tuple[RuntimeContext, list[Any]]:
    runtime = runtime_context(
        clock=clock, trigger_registry=TriggerRegistry(companion_name="companion")
    )
    loader = ModuleLoader(
        runtime.bus, fixture_modules(tmp_path / "modules"), context=runtime, environ={}
    )
    modules = {
        "fakeplatform": {"channel_ids": ["chan-a", "chan-b"], "companion_name": "companion", **fake},
        "viewer_memory": {
            "directory": str(directory),
            "_sleeper": clock.sleep,
            "_wall_clock": lambda: WALL_EPOCH + clock.now,
            "limits": {},
            **memory,
        },
    }
    activations = await loader.activate_enabled(
        {"enabled_modules": ["fakeplatform", "viewer_memory"], "modules": modules}
    )
    return runtime, activations


def coordinator_for(runtime: RuntimeContext, activations: list[Any], clock: ManualClock) -> PhaseCoordinator:
    return PhaseCoordinator(
        activations,
        tasks=runtime.tasks,
        clock=clock,
        sleeper=clock.sleep,
        shutdown_deadline_seconds=10.0,
        hook_timeout_seconds=4.0,
        observer=runtime.health.observe_phase,
    )


@pytest.fixture
def clean_fakeplatform():
    fakeplatform.reset()
    yield
    fakeplatform.reset()


@pytest.mark.asyncio
async def test_ac18_the_bounds_hold_before_readiness(tmp_path: Path, clean_fakeplatform) -> None:
    """AC18: a directory of 5 valid files with ``max_files: 3`` holds 3 files
    when ``module.ready`` is published for ``viewer_memory``, the 2 least
    recently used gone (1 ``evicted`` fact, count 2)."""

    clock = ManualClock(START)
    directory, json_names = memory_directory(tmp_path)
    paths = [
        write_memory(directory, ("fake", "chan-a", f"v{index}"), last_used=START + index)
        for index in range(5)
    ]
    runtime, activations = await loaded(
        tmp_path, clock, directory, fake={}, memory={"max_files": 3}
    )
    at_ready: list[list[str]] = []

    def on_ready(event: Any) -> None:
        if "viewer_memory" in json.dumps(event["payload"]):
            at_ready.append(json_names())

    runtime.bus.subscribe("module.ready", on_ready)
    coordinator = coordinator_for(runtime, activations, clock)
    assert (await coordinator.start()).status == 0
    try:
        assert at_ready == [sorted(path.name for path in paths[2:])]
        assert removed_facts(runtime) == [{"reason": "evicted", "count": 2}]
    finally:
        await coordinator.stop()


# -- AC19 chat half: the erasure command -------------------------------------- #


@pytest.mark.asyncio
async def test_ac19_forgetme_from_the_trusted_author_erases_even_when_rejected(
    tmp_path: Path, clean_fakeplatform
) -> None:
    """AC19 (chat half): ``!forgetme`` from ``v1`` deletes ``v1``'s file for
    that platform and channel — 1 ``memory.removed`` fact, ``erased``, count
    1, no viewer id — although the trigger rejected the message;
    ``!forgetme please`` and a notice whose text is ``!forgetme`` delete
    nothing; another channel's and another viewer's files stay."""

    clock = ManualClock(START)
    directory, json_names = memory_directory(tmp_path)
    runtime, activations = await loaded(
        tmp_path, clock, directory, fake={"notices": {"kinds": ["raid"]}}, memory={}
    )
    coordinator = coordinator_for(runtime, activations, clock)
    assert (await coordinator.start()).status == 0
    platform = next(item.handle for item in activations if hasattr(item.handle, "inject"))
    memory = next(item.handle for item in activations if hasattr(item.handle, "store"))
    try:
        for key in (("fake", "chan-a", "v1"), ("fake", "chan-b", "v1"), ("fake", "chan-a", "v2")):
            await memory.record(*key)
        target = memory_file_name("fake", "chan-a", "v1")
        assert target in json_names() and len(json_names()) == 3

        await platform.inject(
            {"channel_id": "chan-a", "author": {"id": "v1"}, "message_id": "m1",
             "text": "!forgetme please"}
        )
        await platform.inject_notice("raid", {"id": "v1"}, "!forgetme", message_id="n1")
        await platform.inject(
            {"channel_id": "chan-a", "author": {"id": "v2"}, "message_id": "m2",
             "text": " !forgetme"}
        )
        assert len(json_names()) == 3
        assert removed_facts(runtime) == []

        await platform.inject(
            {"channel_id": "chan-a", "author": {"id": "v1"}, "message_id": "m3",
             "text": "!forgetme"}
        )
        names = json_names()
        assert target not in names and len(names) == 2
        assert memory_file_name("fake", "chan-b", "v1") in names
        assert memory_file_name("fake", "chan-a", "v2") in names
        facts = removed_facts(runtime)
        assert facts == [{"reason": "erased", "count": 1}]
        fact_texts = [
            json.dumps(event, ensure_ascii=False)
            for event in events_of(runtime.bus, FACT_MEMORY_REMOVED)
        ]
        assert all("v1" not in text and target not in text for text in fact_texts)
        # The trigger rejected the command: the module acted on its own.
        rejected = [
            event["payload"] for event in events_of(runtime.bus, "input.trigger.rejected")
            if event["payload"]["source_event_id"] == "m3"
        ]
        assert len(rejected) == 1
        assert not events_of(runtime.bus, "input.trigger.accepted")

        # A repeat erases nothing more and publishes nothing.
        await platform.inject(
            {"channel_id": "chan-a", "author": {"id": "v1"}, "message_id": "m4",
             "text": "!forgetme"}
        )
        assert len(removed_facts(runtime)) == 1
        assert all("v1" not in text for text in trace_texts(runtime.bus) if FACT_MEMORY_REMOVED in text)
    finally:
        await coordinator.stop()


@pytest.mark.asyncio
async def test_an_empty_forget_command_disables_erasure(tmp_path: Path) -> None:
    clock = ManualClock(START)
    runtime = runtime_context(clock=clock)
    directory, json_names = memory_directory(tmp_path)
    handle = await module_for(runtime, clock, directory, forget_command="")
    await handle.prepare()
    try:
        await handle.record("fake", "chan-a", "v1")
        event = {
            "type": "channel.chat.message",
            "payload": {"platform": "fake", "channel_id": "chan-a", "author": {"id": "v1"},
                        "message_id": "m1", "text": ""},
            "metadata": {},
        }
        await handle.handle_chat_message(event)
        await handle.handle_chat_message({**event, "payload": {**event["payload"], "text": "!forgetme"}})
        assert len(json_names()) == 1
    finally:
        await handle.close()


@pytest.mark.asyncio
async def test_a_reserved_or_missing_author_erases_nothing(tmp_path: Path) -> None:
    clock = ManualClock(START)
    runtime = runtime_context(clock=clock)
    directory, json_names = memory_directory(tmp_path)
    handle = await module_for(runtime, clock, directory)
    await handle.prepare()
    try:
        await handle.record("fake", "chan-a", "system:anonymous")
        payload = {"platform": "fake", "channel_id": "chan-a", "message_id": "m1",
                   "text": "!forgetme"}
        for author in ({"id": "system:anonymous"}, None, {}, "v1"):
            await handle.handle_chat_message({"payload": {**payload, "author": author}})
        await handle.handle_chat_message("not an event")
        assert len(json_names()) == 1
        assert removed_facts(runtime) == []
    finally:
        await handle.close()


# -- atomic write ------------------------------------------------------------- #


def test_a_crash_between_the_temporary_write_and_the_replace_keeps_the_old_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fault-injected ``os.replace`` leaves the old file byte-identical, no
    temporary file behind, and the index unchanged."""

    directory, _ = memory_directory(tmp_path)
    store = store_for(directory, Wall())
    store.record("twitch", "c1", "v1", note={"viewer_text": "first", "delivery": "none"})
    path = store.path_of("twitch", "c1", "v1")
    before = path.read_bytes()
    stats = store.stats()
    replaced: list[tuple[Any, Any]] = []

    def crash(source: Any, destination: Any) -> None:
        replaced.append((source, destination))
        assert Path(source).parent == directory and Path(source).is_file()
        raise OSError("injected crash")

    monkeypatch.setattr(viewer_memory.os, "replace", crash)
    with pytest.raises(OSError):
        store.record("twitch", "c1", "v1", note={"viewer_text": "second", "delivery": "none"})
    monkeypatch.undo()

    assert len(replaced) == 1
    assert path.read_bytes() == before
    assert sorted(os.listdir(directory)) == [path.name]
    assert store.stats() == stats
    assert [note["viewer_text"] for note in store.read("twitch", "c1", "v1")["notes"]] == ["first"]


# -- the sweep is a supervised task ------------------------------------------ #


@pytest.mark.asyncio
async def test_the_sweep_runs_as_a_supervised_task_until_close(tmp_path: Path) -> None:
    clock = ManualClock(START)
    runtime = runtime_context(clock=clock)
    directory, _ = memory_directory(tmp_path)
    handle = await module_for(runtime, clock, directory, sweep_interval_seconds=60)
    await handle.prepare()
    assert "viewer_memory" not in list(runtime.tasks.owners())
    await handle.start_inputs()
    assert "viewer_memory" in list(runtime.tasks.owners())
    await handle.stop_inputs()
    await settle()
    assert "viewer_memory" not in list(runtime.tasks.owners())
    # Stopped: no sweep runs any more, and a later start does not restart it.
    await handle.start_inputs()
    assert "viewer_memory" not in list(runtime.tasks.owners())
    await handle.close()


# -- R4: memory.recall and memory.record through the executor -------------- #


MEMORY_CHANNEL = "chan-m"
MEMORY_DESTINATION = Destination("fake", MEMORY_CHANNEL, "memory")
VIEWER = "v1"
METADATA_KEYS = {"known", "display_name", "first_seen", "last_seen", "interactions"}


class MemoryHarness:
    """The module prepared on a runtime whose executor the test drives, with
    both memory actions granted (default deny otherwise)."""

    def __init__(self, runtime: RuntimeContext, handle: ViewerMemoryModule, clock: ManualClock) -> None:
        self.runtime = runtime
        self.handle = handle
        self.clock = clock
        self._calls = 0

    async def invoke(
        self,
        action_name: str,
        arguments: dict[str, Any] | None = None,
        *,
        conversation_id: str | None = None,
    ) -> ActionObservation:
        self._calls += 1
        if conversation_id is None:
            conversation_id = SessionKey("fake", MEMORY_CHANNEL, VIEWER).serialize()
        call = ActionCall(
            action_name=action_name,
            action_version=1,
            arguments=arguments or {},
            conversation_id=conversation_id,
            run_id="run-1",
            call_id=f"memory-call-{self._calls}",
            source_event_id="source-1",
            destination=MEMORY_DESTINATION,
            principal="brain",
            deadline=self.clock.now + 100.0,
            message_id="source-1",
        )
        return await self.runtime.executor.invoke(call)

    async def record(self, viewer_text: str = "hello", **arguments: Any) -> ActionObservation:
        return await self.invoke(
            RECORD_ACTION, {"viewer_text": viewer_text, "delivery": "none", **arguments}
        )

    async def recall(self, **options: Any) -> ActionObservation:
        return await self.invoke(RECALL_ACTION, **options)

    def stored(self) -> dict[str, Any]:
        path = self.handle.store.path_of("fake", MEMORY_CHANNEL, VIEWER)
        return json.loads(path.read_bytes().decode("utf-8"))


async def memory_harness(tmp_path: Path, **settings: Any) -> MemoryHarness:
    clock = ManualClock(START)
    runtime = runtime_context(clock=clock)
    directory, _ = memory_directory(tmp_path)
    handle = await module_for(runtime, clock, directory, **settings)
    await handle.prepare()
    for action_name in (RECALL_ACTION, RECORD_ACTION):
        runtime.actions._authorization.grant(
            AuthorizationRule(
                rule_id=f"grant-{action_name}",
                action_name=action_name,
                granted_permissions=(action_name,),
            )
        )
    return MemoryHarness(runtime, handle, clock)


def recall_text(observation: ActionObservation) -> str:
    [part] = observation.parts
    assert part["type"] == "text"
    return part["text"]


def test_the_manifest_declares_both_memory_actions() -> None:
    """R4: ``memory.recall`` is a read with no argument; ``memory.record`` a
    write with no delivery, not model-proposable, timeout 5 s; both over
    ``*/*/memory`` under their own permission; and no identity property."""

    recall = viewer_memory._declared_spec(RECALL_ACTION)
    record = viewer_memory._declared_spec(RECORD_ACTION)
    assert recall.nature == "read" and record.nature == "write"
    assert recall.required_permissions == ("memory.recall",)
    assert record.required_permissions == ("memory.record",)
    for spec in (recall, record):
        assert spec.supported_destinations == (Destination("*", "*", "memory"),)
        assert spec.argument_schema["additionalProperties"] is False
        assert not {"viewer_id", "platform", "channel_id"} & set(spec.argument_schema["properties"])
    assert dict(recall.argument_schema["properties"]) == {}
    assert record.delivery is None and record.model_proposable is False
    assert record.timeout_seconds == 5
    assert set(record.argument_schema["properties"]) == {
        "viewer_text", "reply_text", "delivery", "display_name"
    }
    assert set(record.argument_schema["required"]) == {"viewer_text", "delivery"}


def test_ac22_max_recall_bytes_is_bounded_by_the_validator(tmp_path: Path) -> None:
    """AC22: ``max_recall_bytes: 511`` is rejected; 512 and 8192 are not."""

    base = {"directory": str(tmp_path)}
    assert validate_settings({**base, "max_recall_bytes": 511}) == [
        "module 'viewer_memory': field 'max_recall_bytes': must be an integer from 512 to 8192"
    ]
    assert validate_settings({**base, "max_recall_bytes": 8193}) != []
    assert validate_settings({**base, "max_recall_bytes": 512}) == []
    assert validate_settings({**base, "max_recall_bytes": 8192}) == []
    assert MemorySettings.from_mapping(base).max_recall_bytes == 1024


@pytest.mark.asyncio
async def test_ac22_an_identity_argument_is_refused_before_the_provider(tmp_path: Path) -> None:
    """AC22: ``viewer_id`` (and any identity argument) is refused as invalid
    with 0 provider invocations, on ``memory.record`` alongside valid
    exchange data and on ``memory.recall`` (schema ``{}``)."""

    h = await memory_harness(tmp_path)
    try:
        executor = h.runtime.executor
        for arguments in (
            {"viewer_id": "v2"},
            {"platform": "fake"},
            {"channel_id": MEMORY_CHANNEL},
        ):
            observation = await h.record(**arguments)
            assert observation.status == "error", observation
            assert observation.error["code"] == "invalid_arguments"
        observation = await h.recall(arguments={"viewer_id": "v2"})
        assert observation.status == "error"
        assert observation.error["code"] == "invalid_arguments"
        assert executor.provider_invocations == 0
        assert h.handle.store.stats() == (0, 0)
    finally:
        await h.handle.close()


@pytest.mark.asyncio
async def test_ac22_record_accepts_the_exchange_data(tmp_path: Path) -> None:
    """AC22: ``memory.record`` with only ``viewer_text`` and ``delivery``
    succeeds, and with ``reply_text`` and ``display_name`` added too; an
    80-character name is stored as its first 64."""

    h = await memory_harness(tmp_path)
    try:
        first = await h.invoke(RECORD_ACTION, {"viewer_text": "hi", "delivery": "none"})
        assert first.status == "success", first
        assert dict(first.result) == {"interactions": 1, "notes": 1}
        name = "".join(chr(ord("a") + index % 26) for index in range(80))
        second = await h.invoke(
            RECORD_ACTION,
            {
                "viewer_text": "again",
                "reply_text": "hello",
                "delivery": "confirmed",
                "display_name": name,
            },
        )
        assert second.status == "success", second
        stored = h.stored()
        assert stored["display_name"] == name[:64]
        assert stored["interactions"] == 2 and stored["use_count"] == 2
        assert stored["notes"][-1] == {
            "at": stored["last_seen"],
            "viewer_text": "again",
            "reply_text": "hello",
            "delivery": "confirmed",
        }
        assert "reply_text" not in stored["notes"][0]
        too_long = await h.record("x" * 201)
        assert too_long.status == "error" and too_long.error["code"] == "invalid_arguments"
        assert h.stored()["interactions"] == 2
    finally:
        await h.handle.close()


@pytest.mark.asyncio
async def test_ac22_twelve_notes_fit_1024_bytes_newest_first(tmp_path: Path) -> None:
    """AC22: 12 notes of 200 characters at ``max_recall_bytes: 1024`` give a
    serialized result of at most 1024 bytes, most recent notes first, with
    ``truncated: true`` — measured as the executor measures the observation."""

    h = await memory_harness(tmp_path, max_notes=12, max_file_bytes=8192)
    try:
        for index in range(12):
            h.clock.advance(1.0)
            assert (await h.record(f"{index:03d}" + "n" * 197)).status == "success"
        observation = await h.recall()
        assert observation.status == "success", observation
        text = recall_text(observation)
        assert observation_size(observation.parts) == len(text.encode("utf-8")) <= 1024
        result = json.loads(text)
        assert result == dict(observation.result) | {
            "notes": [dict(note) for note in observation.result["notes"]]
        }
        assert result["known"] is True and result["truncated"] is True
        assert result["interactions"] == 12
        kept = [note["viewer_text"][:3] for note in result["notes"]]
        assert kept and len(kept) < 12
        assert kept == [f"{index:03d}" for index in range(11, 11 - len(kept), -1)]
    finally:
        await h.handle.close()


@pytest.mark.asyncio
async def test_ac22_a_wide_name_and_ten_notes_fit_512_bytes(tmp_path: Path) -> None:
    """AC22: a stored ``display_name`` of 64 four-byte characters and 10
    notes of 200 characters at ``max_recall_bytes: 512`` give at most 512
    bytes, every metadata key, ``truncated: true`` and a ``display_name``
    that is a prefix of the stored one."""

    h = await memory_harness(tmp_path, max_recall_bytes=512)
    try:
        for index in range(10):
            observation = await h.record("t" * 200, display_name=WIDE * 64)
            assert observation.status == "success", observation
        assert h.stored()["display_name"] == WIDE * 64
        assert len(h.stored()["notes"]) == 10
        observation = await h.recall()
        text = recall_text(observation)
        assert len(text.encode("utf-8")) <= 512
        result = json.loads(text)
        assert METADATA_KEYS <= set(result)
        assert result["truncated"] is True
        assert (WIDE * 64).startswith(result["display_name"])
    finally:
        await h.handle.close()


def test_fit_recall_shortens_the_name_by_whole_characters_last() -> None:
    """R4: when the metadata alone is over the limit, the notes are all gone
    and ``display_name`` is cut from its end by whole characters, to the
    longest prefix that fits; the result never exceeds the limit."""

    record = {
        "display_name": WIDE * 200,
        "first_seen": at(0.0),
        "last_seen": at(1.0),
        "interactions": 3,
        "notes": notes_of(3, "z" * 50),
    }
    result, text = viewer_memory.fit_recall(record, 512)
    size = len(text.encode("utf-8"))
    assert size <= 512 and result["notes"] == [] and result["truncated"] is True
    name = result["display_name"]
    assert name and (WIDE * 200).startswith(name) and len(name) < 200
    # One more character would not fit.
    longer = json.loads(text) | {"display_name": WIDE * (len(name) + 1)}
    assert len(json.dumps(longer, ensure_ascii=False, separators=(",", ":")).encode()) > 512
    small = dict(record, display_name="ok", notes=notes_of(1, "z"))
    result, _ = viewer_memory.fit_recall(small, 1024)
    assert result["truncated"] is False and len(result["notes"]) == 1


@pytest.mark.asyncio
async def test_ac22_a_watch_session_has_no_viewer(tmp_path: Path) -> None:
    """AC22: a ``system:watch`` conversation — or an unparsable one, or a
    session on another channel — ends ``error no_viewer`` and stores nothing."""

    h = await memory_harness(tmp_path)
    try:
        for conversation_id in (
            SessionKey("fake", MEMORY_CHANNEL, "system:watch").serialize(),
            "not-a-session",
            "1:f|6:chan-m|2:v1",
            SessionKey("fake", "other", VIEWER).serialize(),
        ):
            for action_name, arguments in (
                (RECALL_ACTION, {}),
                (RECORD_ACTION, {"viewer_text": "hi", "delivery": "none"}),
            ):
                observation = await h.invoke(
                    action_name, arguments, conversation_id=conversation_id
                )
                assert observation.status == "error", (conversation_id, observation)
                assert observation.error["code"] == viewer_memory.ERROR_NO_VIEWER
        assert h.handle.store.stats() == (0, 0)
    finally:
        await h.handle.close()


def test_parse_conversation_id_inverts_the_session_key() -> None:
    key = SessionKey("fa|ke", "c:1", "v|2:")
    assert viewer_memory.parse_conversation_id(
        SessionKey("fa|ke", "c:1", "v|2").serialize()
    ) == ("fa|ke", "c:1", "v|2")
    # A viewer in the reserved `:` namespace is no platform viewer.
    assert viewer_memory.parse_conversation_id(key.serialize()) is None
    for value in ("", "01:f|1:c|1:v", "1:f|1:c|1:v|", "1:f|1:c", None, "1:f|0:|1:v"):
        assert viewer_memory.parse_conversation_id(value) is None, value


@pytest.mark.asyncio
async def test_ac16_recall_of_an_expired_record_is_unknown(tmp_path: Path) -> None:
    """AC16 (recall half): 29 days after ``last_seen`` the recall is known;
    at 30 days + 1 s it is ``{known: false}``."""

    h = await memory_harness(tmp_path)
    try:
        unknown = await h.recall()
        assert dict(unknown.result) == {"known": False}
        assert recall_text(unknown) == '{"known":false}'
        assert h.handle.store.stats() == (0, 0)

        assert (await h.record()).status == "success"
        h.clock.advance(29 * DAY)
        assert (await h.recall()).result["known"] is True
        # The recall at 29 days is a use, not a sighting: last_seen stays.
        h.clock.advance(1 * DAY + 1.0)
        observation = await h.recall()
        assert observation.status == "success"
        assert dict(observation.result) == {"known": False}
    finally:
        await h.handle.close()


@pytest.mark.asyncio
async def test_a_recall_is_a_use(tmp_path: Path) -> None:
    """R4: a recall that returns a record increments ``use_count`` and moves
    ``last_used``; nothing else of the file changes."""

    h = await memory_harness(tmp_path)
    try:
        await h.record(display_name="Viewer")
        before = h.stored()
        assert before["use_count"] == 1
        h.clock.advance(10.0)
        observation = await h.recall()
        assert observation.result["known"] is True
        assert observation.result["display_name"] == "Viewer"
        assert observation.result["truncated"] is False
        after = h.stored()
        assert after["use_count"] == 2
        assert after["last_used"] == at(START + 10.0)
        assert after["last_used"] > before["last_used"]
        unchanged = {key for key in after if key not in {"use_count", "last_used"}}
        assert {key: after[key] for key in unchanged} == {key: before[key] for key in unchanged}
    finally:
        await h.handle.close()


@pytest.mark.asyncio
async def test_the_actions_are_bound_ready_and_withdrawn_at_close(tmp_path: Path) -> None:
    h = await memory_harness(tmp_path)
    registry = h.runtime.actions
    assert {RECALL_ACTION, RECORD_ACTION} <= set(registry.registered_ready())
    await h.handle.close()
    assert not {RECALL_ACTION, RECORD_ACTION} & set(registry.registered_ready())


# --------------------------------------------------------------------------- #
# The offline command (R3; AC19 CLI half)
# --------------------------------------------------------------------------- #


def command_config(tmp_path: Path, directory: Any, **settings: Any) -> Path:
    """A profile whose other modules hold unresolved ``${…}`` secrets."""

    config = {
        "enabled_modules": ["twitch", "viewer_memory"],
        "modules": {
            "twitch": {"client_secret": "${TWITCH_CLIENT_SECRET}"},
            "viewer_memory": {"directory": str(directory), "limits": {}, **settings},
        },
    }
    path = tmp_path / "config.yaml"
    path.write_text(json.dumps(config), encoding="utf-8")
    return path


@pytest.fixture
def no_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("the offline command opened a socket")

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


def three_viewers_and_notes(directory: Path) -> tuple[list[Path], Path]:
    paths = [
        write_memory(directory, ("twitch", "c1", "v1")),
        write_memory(directory, ("twitch", "c1", "v2")),
        write_memory(directory, ("kick", "c1", "v1")),
    ]
    notes = directory / "notes.txt"
    notes.write_text("keep me", encoding="utf-8")
    return paths, notes


def test_ac19_cli_forget_removes_exactly_the_one_hashed_file(
    tmp_path: Path, no_socket: None, capsys: pytest.CaptureFixture[str]
) -> None:
    directory, json_names = memory_directory(tmp_path)
    paths, notes = three_viewers_and_notes(directory)
    config = command_config(tmp_path, directory)
    argv = ["forget", "--config", str(config), "--platform", "twitch", "--channel", "c1",
            "--viewer", "v1"]

    assert memory_command(argv) == 0
    assert capsys.readouterr().out == "removed=1\n"
    assert json_names() == sorted(path.name for path in paths[1:])
    assert notes.read_text(encoding="utf-8") == "keep me"

    assert memory_command(argv) == 0
    assert capsys.readouterr().out == "removed=0\n"
    assert len(json_names()) == 2


def test_ac19_cli_forget_all_keeps_unrelated_files(
    tmp_path: Path, no_socket: None, capsys: pytest.CaptureFixture[str]
) -> None:
    directory, json_names = memory_directory(tmp_path)
    _, notes = three_viewers_and_notes(directory)
    unrelated = directory / "settings.json"
    unrelated.write_text("{}", encoding="utf-8")
    config = command_config(tmp_path, directory)

    assert memory_command(["forget-all", "--config", str(config)]) == 0
    assert capsys.readouterr().out == "removed=3\n"
    assert [name for name in json_names() if NAME_PATTERN.match(name)] == []
    assert json_names() == ["settings.json"]
    assert notes.read_text(encoding="utf-8") == "keep me"


def test_ac19_cli_stats_prints_the_count_and_bytes(
    tmp_path: Path, no_socket: None, capsys: pytest.CaptureFixture[str]
) -> None:
    directory, _ = memory_directory(tmp_path)
    paths, _ = three_viewers_and_notes(directory)
    config = command_config(tmp_path, directory)
    expected = sum(path.stat().st_size for path in paths)

    assert memory_command(["stats", "--config", str(config)]) == 0
    assert capsys.readouterr().out == f"files=3 bytes={expected}\n"
    # An absent directory has no memory file; nothing is created.
    absent = command_config(tmp_path, tmp_path / "absent")
    assert memory_command(["stats", "--config", str(absent)]) == 0
    assert capsys.readouterr().out == "files=0 bytes=0\n"
    assert not (tmp_path / "absent").exists()


@pytest.mark.parametrize(
    "content",
    [
        None,  # no file
        "modules: [unclosed",  # not YAML
        "- a list",  # not a mapping
        "modules:\n  twitch: {}\n",  # no viewer_memory subtree
        "modules:\n  viewer_memory:\n    retention_days: 30\n",  # no directory
        "modules:\n  viewer_memory:\n    directory: d\n    max_files: 0\n",
        "modules:\n  viewer_memory:\n    directory: d\n    max_file_bytes: 4096\n"
        "    max_total_bytes: 1024\n",
        "modules:\n  viewer_memory:\n    directory: d\n    colour: blue\n",
        "modules:\n  viewer_memory:\n    directory: ${MEMORY_DIR}\n",
    ],
)
def test_ac19_cli_an_invalid_configuration_exits_2(
    tmp_path: Path, no_socket: None, capsys: pytest.CaptureFixture[str], content: str | None
) -> None:
    directory, json_names = memory_directory(tmp_path)
    paths, _ = three_viewers_and_notes(directory)
    config = tmp_path / "config.yaml"
    if content is not None:
        config.write_text(content, encoding="utf-8")

    for argv in (["stats"], ["forget-all"]):
        assert memory_command([*argv, "--config", str(config)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "viewer_memory" in captured.err
    assert "MEMORY_DIR" not in captured.err
    assert len(json_names()) == len(paths)


def test_ac19_cli_the_module_entry_runs_without_a_network_client(tmp_path: Path) -> None:
    """``python -m modules.viewer_memory`` runs in a fresh interpreter; its
    import trace shows no ``aiohttp``, and no ``${…}`` secret is needed."""

    directory, _ = memory_directory(tmp_path)
    paths, _ = three_viewers_and_notes(directory)
    config = command_config(tmp_path, directory)
    environment = {
        key: value for key, value in os.environ.items() if key != "TWITCH_CLIENT_SECRET"
    }

    completed = subprocess.run(
        [sys.executable, "-X", "importtime", "-m", "modules.viewer_memory", "stats",
         "--config", str(config)],
        cwd=REPOSITORY,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    expected = sum(path.stat().st_size for path in paths)
    assert completed.stdout == f"files=3 bytes={expected}\n"
    imported = {line.rsplit("|", 1)[-1].strip() for line in completed.stderr.splitlines()}
    assert not {name for name in imported if name.split(".")[0] == "aiohttp"}
    assert "yaml" in imported
