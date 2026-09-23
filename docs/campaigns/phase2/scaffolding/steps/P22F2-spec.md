# Step P22F2 — gate findings F4 and F5 (the honest stalled-wake bound; scope and allowlist reconciliation)
commit: fix(phase2): P22F2 — README states the real stalled-wake bound; spec targets and allowlist reconciled

Repo: `/media/chpo/HDD-papa/twitch-ia-compagnon`, branch `main`. Authority: the gate report
`docs/campaigns/phase2/scaffolding/gate-report-1.md` (read it first), the phase 2 spec
`docs/campaigns/phase2/spec.md`, the plan `docs/campaigns/phase2/plan.md`, and — for F4 — the arbiter's
residual record `docs/campaigns/phase2/scaffolding/plan-residuals.md`.

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv (the system python lacks `aiohttp`).

## F4 — Moderate: P20 did not record the arbiter's real stalled-wake bound

Locations: `docs/README.md:392`, `modules/audio_input/__init__.py:1314`, executor stamp at
`core/actions.py:1413`. Authority: `plan-residuals.md` (the arbiter's closing record).

The README promises completion before expiry for **every wake delayed less than the reserve**, and describes
only a whole-second wake stall as the failure. The arbiter explicitly required the opposite: the reserve is the
**mitigation, not a proof**, and the real bound is (wake delay + execution/preemption before the executor's
stamp). Rewrite the README clause (and the module docstring if it repeats the over-promise) to state:
- what the MODULE guarantees testably (it never waits on anything scheduled at or after the window edge and
  returns at or before that edge — P11's exact-edge and delayed-wake tests already pin this);
- the DECLARED LIMIT with its real bound: the stamp belongs to `core/actions.py::_observe` and is taken after
  the provider returns, so the reserve bounds the risk rather than proving the outcome; a stall of the whole
  reserve is the failure case, and preemption/execution time before the stamp is part of the delay.
Do NOT change the code's behaviour here, do not adopt late success, and do not invent another deadline
subtraction: this finding is documentation honesty only.

## F5 — Moderate: the final tree still violates the declared scope/allowlist gate

Locations: `docs/campaigns/phase2/spec.md:236` and `:1183`; concrete out-of-scope hunks at `core/loader.py:109`,
`tests/test_brain.py:771`, `tests/test_integration.py:416`, plus `.gitignore` and others named in the report.

An executed comparison of the spec's `targets` against `git diff --name-only 7e26038..HEAD` (excluding the
expressly permitted campaign directory) returns files the spec never declared. Outside the eight-entry test
allowlist, the unknown-capability test now uses `smell` instead of `audio`, the observation vocabulary
assertions add audio, and the server binding test was rewritten. The prior P21 README records these
discrepancies, but RECORDING them is not an authority amendment.

Reconcile — in `docs/campaigns/phase2/spec.md` — the declared targets, the test-failure allowlist and any AC20
wording with the INTENDED changes actually present on the branch:
- for each out-of-scope file, either add it to the declared targets with the reason it is needed, or state in
  the allowlist why the touched assertion legitimately changes (with the replacing test named, in the phase 1
  allowlist style);
- keep the list falsifiable: a reader must be able to re-run the comparison and find no undeclared file.
Do NOT revert the landed changes, and do not widen the permission beyond what the branch actually did.

## Constraints
- Documentation only: no production behaviour change in this step; the suite must stay green and its count
  must not drop.
- One atomic commit; the message is pinned above.
- Report: each finding, the exact file:line changed, and the re-run comparison result for F5 (command + output).
