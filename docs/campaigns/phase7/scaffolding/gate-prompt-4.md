# Phase 7 — closing gate, fourth pass

Your third pass resolved F1a and N1 and held F1b (HIGH) and N2 (LOW) as PARTIAL. Step P12F5 has landed.
This is a narrow pass: settle those two, confirm nothing regressed, and decide.

**Write your report to `docs/campaigns/phase7/full-branch-review-4.md`.** Writing THAT ONE FILE is
authorized and required. Create, stage or modify nothing else.

## Settle F1b — the terminal answer must not wait for the cache

Re-run your exact probe: the `delayed_put` wrapper (`time.sleep(1.2)` around `_ObservationCache.put`), a
short page, `request_timeout_seconds=1`, real timer, `_sleeper=None`. Then:

- measure elapsed wall time around `await rig.read(...)` and around `await rig.search(...)`;
- schedule an independent `loop.call_later(1, ...)` immediately before each call and report when it fires
  (last pass: 1.201245 s and 1.201417 s — the loop was blocked);
- after the call, wait another 0.5 s and assert the cache is still `(0 entries, 0 bytes)`: the abandoned
  insertion must not land;
- confirm the undelayed path still stores and still answers;
- confirm the serialization-delay control still returns at ≈1.001 s.

Expected: both calls return within a small margin of 1 s, `timed_out`, no entry, and no late entry after.
The regression test must assert elapsed time — check that it does, not just that it passes.

## Settle N2 — three figures

At the current head, verify: `tests/test_web.py` passes **474** tests (your third pass measured 474);
`git diff --numstat 7c29434 -- tests` gives **15 files, +7,005 / −15** with `test_web.py +6,334`; and the
full suite gives **4,397 passed, 26 skipped**. The summary must state those, with no other figure left
claiming to be current.

## Then confirm nothing regressed

- R1–R10 map and all 26 criteria unchanged.
- No added skip/xfail/deletion/loosening beyond the seven allowlisted nodes; no new dependency; no socket
  added by this phase.
- Full suite green: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/ -q -p no:cacheprovider`.
- Read and search both report `timed_out` honestly under every probe, and never a late `success`.

## Deliverable (`docs/campaigns/phase7/full-branch-review-4.md`)

- **VERDICT**: `APPROVE` / `REQUEST_CHANGES` / `REJECT`
- **ITEMS SETTLED**: F1b, N2 — RESOLVED / PARTIAL / UNRESOLVED with the measured numbers
- **REGRESSIONS**, **NEW FINDINGS** (executed reproduction only), **NOT VERIFIED**

This gate CLOSES phase 7. If everything you can check passes, say APPROVE plainly.
