# Phase 3 — full-branch closing gate, FIFTH pass (F8 residual: lazy batches)

Repo: `/media/chpo/HDD-papa/twitch-ia-compagnon`, branch `main`. Gate 4 confirmed F8's original counterexamples
corrected (bounded diagnostic strings, full file identity at 2,048 files) and left ONE measured residual: the
batching helper eagerly built every record of every batch before emitting the first batch (32 failures → 32
records constructed before the first emit; 256 → 256), so peak memory still grew with the failure count. One
focused fix step has since landed.

**Write your report to `docs/campaigns/phase3/scaffolding/gate-report-5.md`.** Writing THAT ONE FILE is
explicitly authorized and required. Create, stage or modify nothing else — no `git add`, no commits, no other
file.

## Your job

1. **Verify the F8 residual**, from the code AND a RUNNING test, citing `file:line` and the executed test name:
   - batches are emitted **lazily** — the number of records constructed before the FIRST emission is at most
     `batch_size`, independent of the failure count, with the stated constant;
   - peak traced bytes stay bounded by a stated constant at 32 / 256 / 2048 failures;
   - full file identity, reason and remained-on-disk recoverability are preserved, and F3's guarantees hold
     (explicit refusal on an unsatisfied bound, healthy store still prepares, no silent failure).
   Re-run the instrumented reproduction from `gate-report-4.md` and report the measured numbers, including the
   2,048-failure case.
2. **Re-run the whole checklist of gate 4** (requirement map R1–R8 → named running tests; the operator's six
   workstreams; memory configurability and analysis-based eviction; the three CONFIGURABLE moderation modes
   with the strictest default and no permanent bans; the requested-only configurable watch input; honest
   Kick/YouTube capabilities; protocol v1; phases 0–2 non-regression; assertion honesty; provider trials).
   Gate 4 found no defect other than the F8 residual — if that still holds, say so plainly.
3. **Hunt for NEW defects introduced by this fix** — especially any laziness that loses a failure, reorders
   identity, or breaks the diagnostics contract; and any remaining allocation that grows with the file count.

## Deliverable (write to `docs/campaigns/phase3/scaffolding/gate-report-5.md`)

- **VERDICT**: `APPROVE` or `REQUEST_CHANGES` (or `REJECT` if unsound)
- **F8**: status, `file:line`, executed test, and the measured records-before-first-emit and peak bytes at
  32 / 256 / 2048 failures
- **CHECKS**: one line each, PASS/FAIL/NOT-VERIFIABLE with the command used
- **NEW FINDINGS**: only with a concrete counterexample or an executed reproduction
- **NOT VERIFIED**: stated plainly

This gate CLOSES phase 3. If everything you can check passes, say APPROVE plainly.
