"""Bounded attachment store owning the bytes behind opaque references (R6).

An observation may carry a screenshot, a capture or any other payload too
heavy to travel inside an event. The :class:`AttachmentStore` owns those bytes
and hands out :class:`AttachmentRef` handles instead, so events, traces and
model requests carry a reference and a content type rather than a base64 blob
or a local path that no remote provider could resolve.

Retention is bounded on the five axes the specification names — a maximum size
per object, a maximum object count, a maximum total volume, a maximum volume
per run and a time-to-live — and every one of them is required and validated
as a finite positive value at construction, raising a
:class:`~core.contracts.ContractError` naming the offending setting so that
``core.main`` can surface it as a startup diagnostic (R6).

Saturation is **refusal, never eviction**. :meth:`AttachmentStore.put` checks,
in this order, ``max_object_bytes``, ``max_objects``, ``max_total_bytes`` and
``max_bytes_per_run``; the first bound that would be exceeded raises
:class:`AttachmentRefused` naming it, and the store drops nothing to make room.
Evicting a reference leased to a live run to fit a new one would turn a
refusal — which the caller sees and can report — into silent data loss for a
run that still holds the handle (AC23, AC31). The offered payload is measured,
not copied, until every bound has been cleared, so refusing an oversized
buffer costs a refusal rather than the very allocation the bound forbids.

Volume is accounted per ``run_id``, so two concurrent runs have independent
quotas: one run saturating its own quota cannot deny the other the space the
total volume still allows. :meth:`AttachmentStore.release` is called when a run
ends or is cancelled and frees exactly that run's leased objects and bytes,
after which the same run identity may store up to its quota again.

The time-to-live is the residual cleanup behind the lease: past its deadline a
reference is reaped and its bytes reclaimed, and :meth:`AttachmentStore.get`
raises :class:`AttachmentExpired` for it — an explicit error, never empty
content. Expiry is checked against the reference's own deadline before any
lookup, so a reference is reported as expired whether or not the reaper has
already run, and every removal goes through a single accounting path that
removes an object exactly once, so a reap followed by a
:meth:`AttachmentStore.release` can never free the same bytes twice.

The module performs no I/O, uses no asyncio, and reads time and identity only
through the injected clock and identifier factory.
"""

from __future__ import annotations

import math
import secrets
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .contracts import ContractError

__all__ = [
    "ATTACHMENT_ID_BYTES",
    "AttachmentError",
    "AttachmentExpired",
    "AttachmentRef",
    "AttachmentRefused",
    "AttachmentStore",
    "AttachmentUnknown",
    "RunUsage",
]

Clock = Callable[[], float]
IdFactory = Callable[[], str]

ATTACHMENT_ID_BYTES = 16
"""Entropy of a generated attachment identifier.

A reference is opaque: holding one is the only way to name the bytes behind
it, so the identifier must not be guessable from a neighbouring one.
"""

_ID_ATTEMPTS = 8
"""Draws allowed before an identifier factory is declared broken."""


# --------------------------------------------------------------------------- #
# Explicit failures
# --------------------------------------------------------------------------- #


class AttachmentError(Exception):
    """Base class for every explicit attachment-store failure.

    The store never answers a failed request with empty content: each failure
    below carries what happened and which limit or reference it concerns.
    """


class AttachmentRefused(AttachmentError):
    """Raised when an addition is refused because a bound would be exceeded.

    ``limit`` names the exceeded setting — ``max_object_bytes``,
    ``max_objects``, ``max_total_bytes`` or ``max_bytes_per_run`` — so a caller
    can report the saturated axis instead of guessing at "no space" (R6).
    """

    def __init__(self, limit: str, reason: str) -> None:
        self.limit = limit
        self.reason = reason
        super().__init__(f"{limit}: {reason}")


class AttachmentExpired(AttachmentError):
    """Raised when a reference is read past its time-to-live.

    Expiry is a read-time error and never an empty payload: a caller that
    cannot tell "gone" from "empty" would forward silence as an observation.
    """

    def __init__(self, attachment_id: str, expires_at: float, now: float) -> None:
        self.attachment_id = attachment_id
        self.expires_at = expires_at
        self.now = now
        super().__init__(
            f"attachment {attachment_id!r} expired at {expires_at!r} "
            f"(clock at {now!r}); its time-to-live has elapsed"
        )


class AttachmentUnknown(AttachmentError):
    """Raised when the store holds no bytes for an otherwise valid reference.

    Either the reference was released with its run, or it was never issued by
    this store.
    """

    def __init__(self, attachment_id: str, reason: str) -> None:
        self.attachment_id = attachment_id
        self.reason = reason
        super().__init__(f"attachment {attachment_id!r}: {reason}")


# --------------------------------------------------------------------------- #
# Handles and accounting
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class AttachmentRef:
    """An opaque handle to bytes owned by the store.

    It carries what a consumer needs to decide whether to resolve it — the
    content type, the size, the run it is leased to and its deadline — and
    never the payload itself, so a reference may travel through events, traces
    and observations without carrying media with it.
    """

    attachment_id: str
    run_id: str
    content_type: str
    size: int
    created_at: float
    expires_at: float

    def __post_init__(self) -> None:
        _require_text(self.attachment_id, "AttachmentRef.attachment_id")
        _require_text(self.run_id, "AttachmentRef.run_id")
        _require_text(self.content_type, "AttachmentRef.content_type")
        if isinstance(self.size, bool) or not isinstance(self.size, int):
            raise ContractError(
                "AttachmentRef.size", f"must be an integer, got {type(self.size).__name__}"
            )
        if self.size < 0:
            raise ContractError("AttachmentRef.size", f"must not be negative, got {self.size!r}")
        for value, field_name in (
            (self.created_at, "AttachmentRef.created_at"),
            (self.expires_at, "AttachmentRef.expires_at"),
        ):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ContractError(
                    field_name, f"must be a number, got {type(value).__name__}"
                )
            if not math.isfinite(float(value)):
                raise ContractError(field_name, f"must be finite, got {value!r}")
        if self.expires_at <= self.created_at:
            raise ContractError(
                "AttachmentRef.expires_at",
                f"must be after created_at ({self.created_at!r}), got {self.expires_at!r}",
            )


@dataclass(frozen=True, slots=True)
class RunUsage:
    """What a run currently leases, or what releasing it freed."""

    objects: int
    total_bytes: int


@dataclass(slots=True)
class _Entry:
    """One retained object: its handle and the bytes the store owns."""

    ref: AttachmentRef
    data: bytes


@dataclass(slots=True)
class _RunState:
    """The live leases of a single ``run_id``.

    ``ids`` is the single source of truth for the object count, so the count
    and the byte total cannot drift apart.
    """

    ids: set[str] = field(default_factory=set)
    total_bytes: int = 0


class AttachmentStore:
    """Bounded store owning attachment bytes behind opaque references."""

    def __init__(
        self,
        *,
        max_object_bytes: int,
        max_objects: int,
        max_total_bytes: int,
        max_bytes_per_run: int,
        ttl_seconds: float,
        clock: Clock = time.monotonic,
        id_factory: IdFactory | None = None,
    ) -> None:
        """Build a store whose retention is bounded on every axis.

        The five limits are required — none has a default to fall back on —
        and each is validated here as a finite positive value, raising a
        :class:`~core.contracts.ContractError` naming the setting when it is
        absent, non-numeric or non-finite (R6).

        ``clock`` and ``id_factory`` are injected so that expiry and reference
        identity are testable without sleeping and without reading the wall
        clock behind the caller's back.
        """

        self._max_object_bytes = _validate_byte_limit(max_object_bytes, "max_object_bytes")
        self._max_objects = _validate_count_limit(max_objects, "max_objects")
        self._max_total_bytes = _validate_byte_limit(max_total_bytes, "max_total_bytes")
        self._max_bytes_per_run = _validate_byte_limit(max_bytes_per_run, "max_bytes_per_run")
        self._ttl_seconds = _validate_duration_limit(ttl_seconds, "ttl_seconds")
        if not callable(clock):
            raise ContractError("clock", "must be callable")
        if id_factory is not None and not callable(id_factory):
            raise ContractError("id_factory", "must be callable")
        self._clock = clock
        self._id_factory: IdFactory = id_factory or _random_attachment_id

        # Insertion ordered, and every reference shares one time-to-live, so
        # the oldest entry is always the first to expire: the reaper can stop
        # at the first live entry instead of scanning the whole store.
        self._entries: OrderedDict[str, _Entry] = OrderedDict()
        self._runs: dict[str, _RunState] = {}
        self._total_bytes = 0

    # ----------------------------------------------------------------- #
    # Storing
    # ----------------------------------------------------------------- #

    def put(self, run_id: str, data: bytes, *, content_type: str) -> AttachmentRef:
        """Store *data* for *run_id* and return the reference leased to it.

        The bounds are checked in the order the specification names them —
        ``max_object_bytes``, then ``max_objects``, then ``max_total_bytes``,
        then ``max_bytes_per_run`` — and the first one that would be exceeded
        raises :class:`AttachmentRefused` naming it. The store evicts nothing
        to make room: a reference leased to a live run stays readable, and the
        caller learns which axis is saturated (AC23, AC31).

        A refused call stores 0 bytes and leaves the object count untouched,
        and it copies nothing: the payload is only measured until every bound
        has been cleared, so refusing a buffer far past ``max_object_bytes``
        costs a refusal rather than an allocation of the same size.
        Expired references are reaped first, so space a run has stopped owing
        is available to the next caller.
        """

        run = _require_text(run_id, "AttachmentStore.put.run_id")
        size = _measure_payload(data, "AttachmentStore.put.data")
        content = _require_text(content_type, "AttachmentStore.put.content_type")

        now = self._reap()

        state = self._runs.get(run)
        held = state.total_bytes if state is not None else 0
        self._refuse_unaffordable(run, held, size)

        # Every bound is cleared: the owned copy is affordable, so take it.
        payload = _own_payload(data)
        if len(payload) != size:
            # A mutable buffer resized between the measurement and the copy.
            # Account for what the store actually owns, and hold the copy
            # to the same bounds rather than letting it drift past them.
            size = len(payload)
            self._refuse_unaffordable(run, held, size)

        ref = AttachmentRef(
            attachment_id=self._new_id(),
            run_id=run,
            content_type=content,
            size=size,
            created_at=now,
            expires_at=now + self._ttl_seconds,
        )
        if state is None:
            state = _RunState()
            self._runs[run] = state
        self._entries[ref.attachment_id] = _Entry(ref=ref, data=payload)
        state.ids.add(ref.attachment_id)
        state.total_bytes += size
        self._total_bytes += size
        return ref

    def _refuse_unaffordable(self, run: str, held: int, size: int) -> None:
        """Raise :class:`AttachmentRefused` if a *size*-byte object cannot fit.

        The bounds are tested in the order the specification names them, so
        the exception names the first saturated axis and not merely "no
        space". No state is touched: a refusal leaves the store as it was.
        """

        if size > self._max_object_bytes:
            raise AttachmentRefused(
                "max_object_bytes",
                f"object of {size} bytes exceeds the per-object limit of "
                f"{self._max_object_bytes} bytes",
            )
        if len(self._entries) + 1 > self._max_objects:
            raise AttachmentRefused(
                "max_objects",
                f"store holds {len(self._entries)} of {self._max_objects} objects; "
                "no reference leased to a live run is evicted to make room",
            )
        if self._total_bytes + size > self._max_total_bytes:
            raise AttachmentRefused(
                "max_total_bytes",
                f"store holds {self._total_bytes} of {self._max_total_bytes} bytes; "
                f"a further {size}-byte object would exceed the total volume",
            )
        if held + size > self._max_bytes_per_run:
            raise AttachmentRefused(
                "max_bytes_per_run",
                f"run {run!r} holds {held} of {self._max_bytes_per_run} bytes; "
                f"a further {size}-byte object would exceed its per-run quota",
            )

    # ----------------------------------------------------------------- #
    # Reading
    # ----------------------------------------------------------------- #

    def get(self, ref: AttachmentRef) -> bytes:
        """Return the bytes behind *ref*.

        Raises :class:`AttachmentExpired` once the reference is past its
        time-to-live on the injected clock, and :class:`AttachmentUnknown`
        when the store holds no bytes for it — released with its run, or never
        issued here. It never answers a missing payload with empty content.

        Expiry is decided from the reference's own deadline before any lookup,
        so it is reported as expired whether or not the reaper has run yet.
        """

        if not isinstance(ref, AttachmentRef):
            raise ContractError(
                "AttachmentStore.get.ref",
                f"must be an AttachmentRef, got {type(ref).__name__}",
            )

        now = self._clock()
        if now >= ref.expires_at:
            # Reap before raising so the expired bytes stop being owed even if
            # nothing else touches the store afterwards.
            self._reap(now)
            raise AttachmentExpired(ref.attachment_id, ref.expires_at, now)

        self._reap(now)
        entry = self._entries.get(ref.attachment_id)
        if entry is None:
            raise AttachmentUnknown(
                ref.attachment_id,
                "no bytes retained; the reference was released with its run or "
                "was never issued by this store",
            )
        if entry.ref != ref:
            raise AttachmentUnknown(
                ref.attachment_id,
                "the reference does not match the retained attachment",
            )
        return entry.data


    def lookup(self, attachment_id: str) -> AttachmentRef | None:
        """Return the reference retained under *attachment_id*, or ``None``.

        A pure read for a consumer that must judge a lease itself — the
        executor validating an ``image_ref`` part checks the run, the stored
        size and the deadline it carries against the call (R4, AC22, AC47).
        Nothing is reaped, extended or refreshed: a reference past its
        deadline that the reaper has not reached yet is returned as stored,
        with the ``expires_at`` that lets the caller see it expired, so
        looking a lease up can neither prolong it nor shorten another. An
        identifier this store does not hold — released, reaped or never
        issued — reads ``None``.
        """

        attachment_id = _require_text(attachment_id, "AttachmentStore.lookup.attachment_id")
        entry = self._entries.get(attachment_id)
        return entry.ref if entry is not None else None

    # ----------------------------------------------------------------- #
    # Releasing and accounting
    # ----------------------------------------------------------------- #

    def release(self, run_id: str) -> RunUsage:
        """Free everything *run_id* leases and return what was freed.

        Called when a run ends or is cancelled: in production by the admission
        scheduler's ``run_cleanup``, wired to this method by the engine that
        owns the scheduler, when the run's terminal record is written on any
        exit path — so no caller has to remember it. It frees exactly that
        run's objects and bytes — never another run's — and leaves no
        accounting behind, so the same run identity may afterwards store up
        to its full quota again (AC31). Idempotent: a run with nothing leased
        frees nothing.

        A reference already reaped by its time-to-live was accounted for at
        that moment and is not counted again here: every removal goes through
        one path that drops an object exactly once.
        """

        run = _require_text(run_id, "AttachmentStore.release.run_id")
        self._reap()

        state = self._runs.get(run)
        if state is None:
            return RunUsage(objects=0, total_bytes=0)

        freed = RunUsage(objects=len(state.ids), total_bytes=state.total_bytes)
        for attachment_id in tuple(state.ids):
            self._drop(attachment_id)
        # ``_drop`` removes the run state with its last object; a run that
        # somehow kept an empty state is cleared here too.
        self._runs.pop(run, None)
        return freed

    def discard(self, attachment_id: str) -> bool:
        """Drop the one object retained under *attachment_id*, if any.

        The release path of a rejected observation: an ``image_ref`` the
        executor refuses — wrong run, expired, stored size mismatch, or an
        observation over its byte bound — is dropped here at once rather
        than left leased until the run's terminal record releases it (R4).
        Idempotent, and idempotent with :meth:`release`: both go through the
        single accounting path, so an object discarded here is not freed a
        second time when its run ends, and discarding an identifier the
        store no longer holds does nothing. Returns whether an object was
        actually dropped.
        """

        attachment_id = _require_text(attachment_id, "AttachmentStore.discard.attachment_id")
        if attachment_id not in self._entries:
            return False
        self._drop(attachment_id)
        return True

    def usage(self, run_id: str) -> RunUsage:
        """Return what *run_id* currently leases, expiry applied first."""

        run = _require_text(run_id, "AttachmentStore.usage.run_id")
        self._reap()
        state = self._runs.get(run)
        if state is None:
            return RunUsage(objects=0, total_bytes=0)
        return RunUsage(objects=len(state.ids), total_bytes=state.total_bytes)

    @property
    def total_bytes(self) -> int:
        """Bytes the store currently owns, expiry applied first."""

        self._reap()
        return self._total_bytes

    @property
    def object_count(self) -> int:
        """Objects the store currently owns, expiry applied first."""

        self._reap()
        return len(self._entries)

    def live_runs(self) -> tuple[str, ...]:
        """Return the run identities holding at least one live reference."""

        self._reap()
        return tuple(self._runs)

    # ----------------------------------------------------------------- #
    # Internals
    # ----------------------------------------------------------------- #

    def _new_id(self) -> str:
        """Draw an identifier the store does not already use."""

        for _ in range(_ID_ATTEMPTS):
            candidate = self._id_factory()
            _require_text(candidate, "id_factory")
            if candidate not in self._entries:
                return candidate
        raise ContractError(
            "id_factory",
            f"produced a duplicate attachment identifier {_ID_ATTEMPTS} times running",
        )

    def _reap(self, now: float | None = None) -> float:
        """Drop every reference past its deadline and return the clock reading.

        Entries share one time-to-live and are kept in insertion order, so the
        first live entry ends the sweep.
        """

        moment = self._clock() if now is None else now
        entries = self._entries
        while entries:
            attachment_id, entry = next(iter(entries.items()))
            if entry.ref.expires_at > moment:
                break
            self._drop(attachment_id)
        return moment

    def _drop(self, attachment_id: str) -> None:
        """Remove one object and its accounting, exactly once.

        Expiry and :meth:`release` both come through here, so bytes reclaimed
        by the reaper are never freed a second time when the run ends.
        """

        entry = self._entries.pop(attachment_id, None)
        if entry is None:
            return
        self._total_bytes -= entry.ref.size
        state = self._runs.get(entry.ref.run_id)
        if state is not None:
            state.ids.discard(attachment_id)
            state.total_bytes -= entry.ref.size
            if not state.ids:
                del self._runs[entry.ref.run_id]


# --------------------------------------------------------------------------- #
# Field helpers
# --------------------------------------------------------------------------- #


def _random_attachment_id() -> str:
    return secrets.token_hex(ATTACHMENT_ID_BYTES)


def _require_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise ContractError(field_name, f"must be a string, got {type(value).__name__}")
    if not value.strip():
        raise ContractError(field_name, "must not be empty")
    return value


def _measure_payload(value: Any, field_name: str) -> int:
    """Validate that *value* is bytes-like and return its size in bytes.

    Measuring is separated from copying so :meth:`AttachmentStore.put` can
    refuse an oversized or unaffordable payload before allocating anything: a
    caller offering a buffer far past ``max_object_bytes`` must cost the store
    a refusal, not a copy of the whole buffer. A ``memoryview`` is measured by
    ``nbytes`` rather than ``len``, which counts elements and would understate
    a multi-byte item format.
    """

    if isinstance(value, memoryview):
        return value.nbytes
    if isinstance(value, (bytes, bytearray)):
        return len(value)
    raise ContractError(
        field_name, f"must be bytes-like, got {type(value).__name__}"
    )


def _own_payload(value: bytes | bytearray | memoryview) -> bytes:
    """Return an immutable copy of an already validated binary payload.

    The copy is the point: the store owns the bytes it hands references to, so
    a caller mutating its own buffer afterwards cannot change what a reference
    resolves to. It is taken only once every bound has been cleared.
    """

    return bytes(value)


def _validate_count_limit(value: Any, setting: str) -> int:
    if value is None:
        raise ContractError(setting, "must be configured with a finite positive value")
    if isinstance(value, bool) or not isinstance(value, int):
        raise ContractError(setting, f"must be an integer, got {type(value).__name__}")
    if value < 1:
        raise ContractError(setting, f"must be a positive integer, got {value!r}")
    return value


def _validate_byte_limit(value: Any, setting: str) -> int:
    """Validate a volume bound: an integer count of bytes, finite and positive.

    ``float('inf')`` is refused here as it is refused for a duration: an
    unbounded volume is exactly the failure R6 forbids.
    """

    if value is None:
        raise ContractError(setting, "must be configured with a finite positive value")
    if isinstance(value, bool) or not isinstance(value, int):
        if isinstance(value, float):
            raise ContractError(
                setting,
                f"must be an integer count of bytes, got {value!r}"
                if math.isfinite(value)
                else f"must be finite, got {value!r}",
            )
        raise ContractError(setting, f"must be an integer, got {type(value).__name__}")
    if value < 1:
        raise ContractError(setting, f"must be a positive integer, got {value!r}")
    return value


def _validate_duration_limit(value: Any, setting: str) -> float:
    if value is None:
        raise ContractError(setting, "must be configured with a finite positive value")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractError(setting, f"must be a number, got {type(value).__name__}")
    limit = float(value)
    if not math.isfinite(limit):
        raise ContractError(setting, f"must be finite, got {value!r}")
    if limit <= 0:
        raise ContractError(setting, f"must be strictly positive, got {value!r}")
    return limit
