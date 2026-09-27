# Step P30F1 — gate-4 residual of F8 (lazy batch emission)
commit: fix(phase3): P30F1 — memory failure batches are emitted lazily, peak bounded by batch size

Repo: `/media/chpo/HDD-papa/twitch-ia-compagnon`, branch `main`. Authority: the gate 4 report
`docs/campaigns/phase3/scaffolding/gate-report-4.md` (read the **F8 residual** section: it carries an executed
instrumented reproduction with a table at 32 and 256 failures), the phase 3 spec (R3, AC18, bounded retention)
and plan (P10).

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider` (project virtualenv).

## F8 residual — eager batch materialization still grows with the failed-file count (major)

Locations: `modules/viewer_memory/__init__.py:290`–`:298` (returns a LIST of every batch, one new dictionary
per failure) and `_publish_failures` at `:1434` (builds that entire structure before emitting anything).

Executed instrumented reproduction (`gate4_eager_batches`): the emitter discards each payload and retains no
batch, yet `DeletionFailure.record` is called for EVERY failure before the first emission —
**32 failures → 32 records constructed before the first emit (9,760 traced bytes); 256 → 256 records
(55,824 bytes)**. Peak memory therefore still grows with the number of failed files, which is exactly what F8
forbade; the fix bounded the strings but not the materialization.

## Required correction

- Emit the failure records **lazily**: one batch materialized at a time (a generator/iterator over batches, or
  an equivalent), so peak live records/bytes are bounded by `batch_size` — NOT by the failure count.
- Preserve everything F8 already fixed: full file identity, the reason and the remained-on-disk fact per
  failure, and a bounded diagnostic/exception built before construction.
- Keep F3's guarantees: an unsatisfied bound refuses readiness explicitly, a healthy store still prepares, no
  failure is silent.

## Tests

- Turn the gate's instrumented reproduction into a running test: instrument the record construction (as the
  gate did) and assert that the number of records constructed before the FIRST emission is at most
  `batch_size` (a stated constant), parameterised at 32, 256 and 2048 failures, with the measured peak traced
  bytes also bounded by a stated constant.
- Assert the full identity/reason/remained recoverability is unchanged (the existing gate-3 tests must stay
  green).
- Do not weaken any existing assertion; the suite must not drop below 2457 passed / 18 skipped.

## Constraints
- One atomic commit; message pinned above.
- Report: `file:line`, the batch constant, the measured records-before-first-emit at 2048 failures, and the
  new tests.
