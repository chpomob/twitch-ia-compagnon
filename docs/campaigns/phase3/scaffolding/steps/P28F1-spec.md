# Step P28F1 — gate-2 finding F3 (completing the memory-bound fix)
commit: fix(phase3): P28F1 — undeletable oversized memory files refuse readiness; failed deletions always reported

Repo: `/media/chpo/HDD-papa/twitch-ia-compagnon`, branch `main`. Authority: the gate 2 report
`docs/campaigns/phase3/scaffolding/gate-report-2.md` (read **F3** in full: it lists the two remaining defects,
the five tests that already pass, and a runnable reproduction), the phase 3 spec (R3, AC18) and plan (P10).

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider` (project virtualenv).

## What is already fixed (do not regress)
`:857` rejects an unsatisfied count/total-byte bound even at startup (`target=None`); `:1012` publishes the
failure and refuses readiness; five `tests/test_viewer_memory.py` tests cover the refusal, the retry of an
undeletable corrupt file, a failed erasure reported as an error, and a write over a stranded file.

## What still blocks closure (the operator's bounds must hold, and failures must be visible)

1. **The per-file bound is omitted from the readiness decision.**
   `modules/viewer_memory/__init__.py:649` classifies an oversized file as corrupt; `:608` retains it as
   stranded after a failed removal. An **undeletable oversized** file therefore never enters the startup refusal
   decision, so the per-file bound can be violated silently at startup.
   Required: an undeletable oversized file is COUNTED against the per-file bound and drives the same explicit
   refusal/`MemoryStoreError` path as the other unsatisfied bounds; the operator must see which file and why.

2. **A failed eviction can still be silent to the operator.**
   `:850` tries alternative candidates after a failed deletion; if later deletions restore the other bounds, the
   failed attempt is not reported.
   Required: a failed deletion is **always reported** (named file, reason, and the fact that it remained)
   even when alternative evictions make the count/total bounds satisfied — never silent.

## Constraints
- Fix the production code; keep the operator's configurability (retention 30 days default, total size, file
  count) and the analysis-based eviction (least-used/oldest) semantics.
- Turn the gate's reproduction into running test(s); never weaken an unrelated assertion.
- The suite must stay green and its count must not drop below 2449 passed / 18 skipped.
- One atomic commit; message pinned above.
- Report: `file:line` changed, the new tests, and the exact refusal/report shape an operator sees.
