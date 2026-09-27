# Phase 3 — full-branch closing gate, FOURTH pass (F8 correction)

Repo: `/media/chpo/HDD-papa/twitch-ia-compagnon`, branch `main`. Gate 3 confirmed F1–F7 resolved, F3's two
original counterexamples corrected, healthy stores still preparing — and raised **F8 (major)**: the new failure
reports were unbounded before supervision (up to 223,378 characters) and lost file identity when truncated
(filenames cut to 3 characters; the `failures` field dropped entirely at 256+ files). One focused fix step has
since landed.

**Write your report to `docs/campaigns/phase3/scaffolding/gate-report-4.md`.** Writing THAT ONE FILE is
explicitly authorized and required. Create, stage or modify nothing else — no `git add`, no commits, no other
file.

## Your job

1. **Verify F8**, from the code AND a RUNNING test, citing `file:line` and the executed test name:
   - the diagnostic and exception content is **bounded BEFORE it is constructed** (no unbounded join followed
     by truncation), with a stated bound independent of the number of files on disk;
   - failures are reported through **bounded records/batches that preserve the full file identity, the reason
     and the remained-on-disk fact**, recoverable whatever the count;
   - F3's guarantees still hold (explicit refusal on an unsatisfied bound, healthy store still prepares, no
     silent failure).
   Re-run the reproductions given in `gate-report-3.md` (the 32/256/2048 table and the 70-files/32-denied case)
   and report what they now do — including the size of the retained diagnostic and whether the 2048-file case
   still identifies every failed file.
2. **Re-run the whole checklist of gate 3** (requirement map R1–R8 → named running tests; the operator's six
   workstreams; memory configurability and analysis-based eviction; the three CONFIGURABLE moderation modes
   with the strictest default and no permanent bans; the requested-only configurable watch input; honest
   Kick/YouTube capabilities; protocol v1; phases 0–2 non-regression; assertion honesty; provider trials).
   Gate 3 found no defect other than F8 — if that still holds, say so plainly.
3. **Hunt for NEW defects introduced by this fix** — especially any reporting path that now hides a failure it
   used to show, any bound that a healthy store can trip, and any allocation that still grows with the file
   count.

## Deliverable (write to `docs/campaigns/phase3/scaffolding/gate-report-4.md`)

- **VERDICT**: `APPROVE` or `REQUEST_CHANGES` (or `REJECT` if unsound)
- **F8**: status, `file:line`, executed test, and the outcome of re-running the reproductions with the measured
  diagnostic size
- **CHECKS**: one line each, PASS/FAIL/NOT-VERIFIABLE with the command used
- **NEW FINDINGS**: only with a concrete counterexample or an executed reproduction
- **NOT VERIFIED**: stated plainly

This gate CLOSES phase 3. If everything you can check passes, say APPROVE plainly.
