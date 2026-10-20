# Phase 7 — closing gate, third pass

Your second pass settled F2, F3, F4 and F5, held F1 as PARTIAL, and raised N1 (HIGH) and N2 (LOW).
Step P12F4 has landed since. Settle what is left, check that nothing regressed, and decide.

**Write your report to `docs/campaigns/phase7/full-branch-review-3.md`.** Writing THAT ONE FILE is
authorized and required. Create, stage or modify nothing else.

## Authorities

`docs/campaigns/phase7/brief.md`, `docs/campaigns/phase7/spec.md` (R1–R10, AC1–AC26, Amendment A1),
`docs/campaigns/phase7/plan.md`, `docs/design-v2.md`, and your two earlier reports.

## Settle the three open items with executed evidence

**F1a — one enormous unfinished construct.** Your second pass measured 2.358819 s (and 2.353311 s on
repeat) for `body = b'<p' + b' a' * ((8388608 - 2) // 2)` with `request_timeout_seconds=1`, because
`check_for_whole_start_tag` ran before the cooperative check. Re-run exactly that body, at the same
settings, with the real timer.

**F1b — cache insertion after the deadline.** You delayed `_ObservationCache.put` by 1.2 s and the call
returned `success` at 1.201 s with one entry stored. Re-run that same probe (process-local wrapper around
`put`), and the serialization-delay control that correctly timed out.

**N1 — the search path.** You measured `success` at 3.568903 s for the 6,000,058-byte Wikipedia answer
with a 1 s setting. Re-run exactly that payload and setting.

For each: state the measured elapsed time, the status, and whether the configured bound holds. Then
confirm the permissive controls still work: a short read, a normal search, a 1 MiB page of ordinary tags,
a fetch that never answers, and the byte-bound and cache tests.

**N2 — the summary.** Check that `docs/campaigns/phase7/summary.md` no longer claims three skip paths, no
longer calls AC30 unallowlisted or the comment undetected, has a current evidence head, and cites the fix
steps. A stale summary is a finding; a corrected one is not.

## Then the whole branch, briefly

- R1–R10 map unchanged; all 26 criteria still covered.
- Still no added skip/xfail/deletion/loosening beyond the seven allowlisted nodes; still no new
  dependency; still no socket added by this phase.
- Full suite green: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/ -q -p no:cacheprovider`.
- The read and search paths both report `timed_out` honestly — never a late `success`.

## Deliverable (`docs/campaigns/phase7/full-branch-review-3.md`)

- **VERDICT**: `APPROVE` / `REQUEST_CHANGES` / `REJECT`
- **ITEMS SETTLED**: F1a, F1b, N1, N2 — RESOLVED / PARTIAL / UNRESOLVED with the measured numbers
- **REGRESSIONS** and **NEW FINDINGS** (only with an executed reproduction)
- **NOT VERIFIED**: unchanged

This gate CLOSES phase 7. If everything you can check passes, say APPROVE plainly.
