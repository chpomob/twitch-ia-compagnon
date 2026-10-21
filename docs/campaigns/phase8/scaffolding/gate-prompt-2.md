# Phase 8 — closing gate, second pass

Your first pass raised three HIGH findings. One fix step has landed (P17F1). Settle the two it targets,
and treat the third as open by construction: its resolution is an operator decision being taken now, so
a pass that finds F3 unresolved has found the truth, not a regression.

**Write your report to `docs/campaigns/phase8/full-branch-review-2.md`.** Writing THAT ONE FILE is
authorized and required. Create, stage or modify nothing else.

## Settle F1 — the hard hook bound

Re-run your exact probe: `hook_timeout_seconds=0.01`, a hook that busy-waits 0.12 s, `none_of`, the same
engine construction and clock. Expected now: the decision completes in ≈0.01 s (measure it), returns no
trigger with `rejected:wake_hook_timeout`, and an independent `loop.call_later(0.02, ...)` heartbeat fires
during the call — proving the event loop was not blocked. Then confirm the fail-closed composition still
holds: a raising hook and a timed-out hook inside `none_of` both decide no trigger.

## Settle F2 — no transcription before a decision

Re-run your unfiltered count: `call_requests = lambda transport: list(transport.requests)` against the
`voice()` fake. Expected: zero requests after prepare and before `start_inputs`; zero for a detector that
never fires; zero for a declined candidate; exactly one for an accepted candidate. Then check that AC26
and AC37 assert the unfiltered total themselves, and that the capture action's legacy behaviour is not
broken by removing the probe from the listening path.

## F3 — open by construction

The test-preservation finding is **not fixed in this tree**. Do not treat its persistence as a regression
and do not attempt to settle it here: the operator is choosing between keeping the installation and `git`
proofs real (with those nodes explicitly out of the isolation guard) and moving them to opt-in trials.
Report F3 as UNRESOLVED with the current numbers (`git diff --numstat 7782e5f HEAD -- tests/test_kick.py`,
the 44 changed functions outside the allowlist, the removed installation assertions), so the next pass
has a baseline.

## Then confirm nothing regressed

- The R1–R9 map and the 40 criteria still hold as you measured them, minus what F1/F2 touched.
- No new skip/xfail/deletion/loosening beyond what you found; no new dependency; no socket added.
- Full suite green: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/ -q -p no:cacheprovider`.
- A trigger still grants nothing: one event end-to-end, an ungranted action still refused.

## Deliverable (`docs/campaigns/phase8/full-branch-review-2.md`)

- **VERDICT**: `APPROVE` / `REQUEST_CHANGES` / `REJECT` (F3 open means REQUEST_CHANGES; say so plainly)
- **ITEMS SETTLED**: F1, F2 — RESOLVED / PARTIAL / UNRESOLVED with the measured numbers; F3 UNRESOLVED
- **REGRESSIONS** and **NEW FINDINGS** (executed reproduction only)
- **NOT VERIFIED**: unchanged
