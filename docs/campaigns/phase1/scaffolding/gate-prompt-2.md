# Phase 1 — full-branch review gate, SECOND pass (after the F1–F5 fix round)

Repo: `/media/chpo/HDD-papa/twitch-ia-compagnon`, branch `main`.
The first pass returned REQUEST_CHANGES with five findings (recorded in
`docs/campaigns/phase1/scaffolding/gate-report-1.md`). Two fix steps have since landed on `main`.

**Write your report to `docs/campaigns/phase1/scaffolding/gate-report-2.md`.** Writing THAT ONE FILE is
explicitly authorized and required (the previous pass answered inline, which the orchestrator cannot
parse). Create, stage or modify nothing else — no `git add`, no commits, no other file.

## Your job

1. **Verify each finding from the first pass**, from the code AND a RUNNING test, citing `file:line` and
   the executed test name. Status per finding: `RESOLVED`, `PARTIALLY RESOLVED` or `NOT RESOLVED`.
   The findings were:

   - **F1 (P1)** `core/actions.py:1622` — rejection deletes ANOTHER live run's attachment: the executor
     rejects a foreign lease, then unconditionally discards every attachment named by the rejected
     observation (evidence was `tests/test_observations.py:400`, which asserted the whole store became
     empty). Required: clean up only the rejecting call's own run; the owner's bytes stay readable.
   - **F2 (P2)** `modules/proxy/__init__.py:1470` — `session.acknowledged` grows without a bound across
     sequential runs on one connection; store release/expiry never prune it and no limit bounds it.
     Required: entries tied to live ownership, pruned on completion/release/expiry, with an explicit
     counted bound and a repeated-run test that exceeds it WITHOUT reconnecting.
   - **F3 (P1)** `modules/proxy/__init__.py:1413`, `:1423`, `:1456` — pending headers keep `run_id` but
     not call identity; binary frames are stored without checking the call is live; an explicit
     unknown/terminal `call_id` falls back to an unrelated call. Required: retain the owning call,
     reject unknown/terminal explicit calls, revalidate ownership right before storing, invalidate
     pending headers on call termination — with both interleavings tested.
   - **F4 (P2)** `modules/proxy/__init__.py:1353` — an unacknowledged image reference reaches the
     executor as `invalid_result`, while `docs/proxy-protocol.md` §5.5 requires `attachment_refused`.
     Required: refuse at the proxy boundary with the normative code and update the test.
   - **F5 (P3)** `docs/campaigns/phase1/spec.md:7` — the declared target list does not match the landed
     branch (planned migrations + scaffolding). Required: reconcile the target list; no code revert.

2. **Re-run the WHOLE checklist of the first pass** (same items as `gate-prompt-1.md`): R1–R8 ↔ AC1–AC58
   mapping to named running tests; caller enumeration migrated; the delivery contract (configured ordered
   list, terminal only, no hardcoded action name in `modules/brain/`); phase-0 guarantees; `core/main.py`
   hygiene; honesty checks (weakened/skipped/xfailed assertions, swallowed errors, stub validators, tests
   bent to match an implementation — note that F1/F4 fixes were REQUIRED to change tests that encoded the
   defect, so distinguish a legitimate contract change from a weakened assertion); proxy protocol
   conformance; failure paths; profiles/packaging; scope discipline.

3. **Hunt for NEW defects introduced by the fix round** — especially ownership regressions, unbounded
   growth, and any test that was rewritten to match an implementation instead of the protocol.

## Deliverable (write to `docs/campaigns/phase1/scaffolding/gate-report-2.md`)

- **VERDICT**: `APPROVE` or `REQUEST_CHANGES` (or `REJECT` if unsound)
- **PREVIOUS FINDINGS**: F1–F5 with status, `file:line`, executed test
- **CHECKS**: one line each, PASS/FAIL/NOT-VERIFIABLE with the command used
- **NEW FINDINGS**: only with a concrete counterexample or executed reproduction
- **NOT VERIFIED**: stated plainly

This gate CLOSES phase 1. If everything you can check passes, say APPROVE plainly.
