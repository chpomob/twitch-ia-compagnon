# Phase 2 — full-branch review gate, SECOND pass (after the F1–F5 fix round)

Repo: `/media/chpo/HDD-papa/twitch-ia-compagnon`, branch `main`. Gate 1
(`docs/campaigns/phase2/scaffolding/gate-report-1.md`) returned **REQUEST_CHANGES** with five findings and
executed counterexamples (`X1`..`X4`); two fix steps have since landed.

**Write your report to `docs/campaigns/phase2/scaffolding/gate-report-2.md`.** Writing THAT ONE FILE is
explicitly authorized and required. Create, stage or modify nothing else — no `git add`, no commits, no other
file.

## Known-good anchors (do NOT report these as findings)

- The normative error code for an unsupported action version is **`action_version_mismatch`**
  (`core/actions.py:206`, `ERROR_VERSION_MISMATCH`, established in phase 0 step P7). An earlier run of this
  gate assumed `version_mismatch` and produced a false positive: the status is `error` with the code above.
- The two fix commits are `7f462cf` (P22F1: F1–F3) and `fa0847e` (P22F2: F4–F5); the full suite is green at
  1891 passed / 12 skipped with the project virtualenv.

## Your job

1. **Verify each finding of gate 1**, from the code AND a RUNNING test, citing `file:line` and the executed test
   name. Status per finding: `RESOLVED`, `PARTIALLY RESOLVED` or `NOT RESOLVED`.
   - **F1 (major)** — an unavailable individual action answered `error no_provider` and the tests accepted it;
     R2/AC7 and R8/AC29 require **`refused provider_not_ready` with ZERO provider invocations**. Required:
     the production behaviour fixed and the assertions updated to the normative outcome (a legitimate contract
     change, not a weakened test).
   - **F2 (major)** — with an EMPTY speech endpoint and `probe: false`, `audio.speak` was considered READY;
     AC43 requires it unbound until an operator configures an endpoint. An empty endpoint must never be ready
     whatever the probe setting.
   - **F3 (moderate)** — the empty and unreachable cases emitted the SAME not-ready reason; AC43 requires
     distinct value-free reasons, asserted through the real prepare path.
   - **F4 (moderate)** — `docs/README.md` over-promised the stalled-wake bound ("every wake delayed less than
     the reserve") whereas the arbiter's record (`plan-residuals.md`) requires the reserve stated as the
     MITIGATION, not a proof, with the real bound (wake delay + execution/preemption before the executor's
     stamp), and the module guarantee stated separately.
   - **F5 (moderate)** — the tree violated the spec's declared scope/allowlist; the spec's targets, the
     test-failure allowlist and any AC20 wording must be reconciled with the INTENDED landed changes (never a
     revert, never a widening beyond what the branch did), and the comparison must be re-runnable by a reader
     and return no undeclared file.
   Re-run gate 1's counterexamples `X1`..`X4` as written and report what each now does.
2. **Re-run the whole checklist of gate 1** (requirement map R1–R10 → named running tests; protocol v1
   conformance; the two authorized deadline subtractions and no third one; the R10 precedence on both sides;
   AC43's no-provider rule; one command/one effect; bounds and cleanup; honesty checks — distinguish a
   legitimate contract change from a weakened assertion; platform neutrality; degradation and chat-only
   startup; packaging).
3. **Hunt for NEW defects introduced by the fix round** — especially any assertion that was changed to match an
   implementation rather than the spec, any readiness path that now claims ready without a usable provider, and
   any new unbounded growth.

## Deliverable (write to `docs/campaigns/phase2/scaffolding/gate-report-2.md`)

- **VERDICT**: `APPROVE` or `REQUEST_CHANGES` (or `REJECT` if unsound)
- **PREVIOUS FINDINGS**: F1–F5 with status, `file:line`, executed test, and the outcome of re-running X1..X4
- **CHECKS**: one line each, PASS/FAIL/NOT-VERIFIABLE with the command used
- **NEW FINDINGS**: only with a concrete counterexample or an executed reproduction
- **NOT VERIFIED**: stated plainly

This gate CLOSES phase 2. If everything you can check passes, say APPROVE plainly.
