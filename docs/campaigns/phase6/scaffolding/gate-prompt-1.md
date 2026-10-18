# Phase 6 — full-branch closing gate

You are the closing reviewer of phase 6. The phase exists to close the defects of a full adversarial
review of this product. Twenty implementation steps have landed since `8010edc`; your job is to decide
whether the tree now deserves approval, by checking the claims rather than reading them.

**Write your report to `docs/campaigns/phase6/full-branch-review.md`.** Writing THAT ONE FILE is
explicitly authorized and required. Create, stage or modify nothing else — no `git add`, no commits, no
other file.

## Authorities

- `docs/reviews/2026-10-16-full-adversarial-review.md` — the review whose findings the phase closes.
  Its reproductions are the evidence standard: re-run them.
- `docs/campaigns/phase6/brief.md` — the operator's binding decisions.
- `docs/campaigns/phase6/spec.md` — requirements R1–R10 and acceptance criteria AC1–AC39.
- `docs/campaigns/phase6/plan.md` — the 20 implementation steps, each naming the findings it owns.
- `docs/campaigns/phase6/scaffolding/verify-by-hand-{1,2,3}.json` — the three hand verifications the
  specification passed before planning.
- The pre-fix tree is `8010edc`; the phase head is the current `main`.

## First job: settle every finding, with observed output

For each of the 34 in-scope findings (F1–F33 and F35; F34 and `claude:F8` are out of scope by the
brief), state **CLOSED** or **OPEN**, and prove it. Use the review's own reproduction where it gives
one, and the specification's criterion where the reproduction is a test. A finding is CLOSED only if
the defect's reproduction now shows the behaviour the review demanded — not if the code looks changed.
For F27, F28 and F32, whose defect is in a test, the evidence is the mutation AC34/AC35 name: apply it,
show the test fail, restore, show it pass.

If a finding is closed at one path but the same defect survives at a sibling path, say so and keep it
OPEN — the phase's whole premise is that a boundary is fixed for every caller.

## Second job: audit how the tests were treated

The phase may replace assertions only in the nine tests named in the specification's failure allowlist,
each with a docstring citing its finding and its requirement. Check the diff for:

- any added `skip`, `xfail`, deleted assertion or loosened assertion — there is no exception;
- any test whose assertions changed outside the nine allowlisted names;
- a replacement weaker than what it replaced, or one whose docstring does not cite both ids;
- `tests/test_phase5_docs_status.py` unchanged and passing;
- the structural redaction test (AC11): does it really fail when a fixture surface formats a field path
  without the shared primitive? Try that mutation.

## Third job: the requirements, the criteria, and the regressions the fixes may have introduced

Map R1–R10 to the named tests that carry them (a criterion is PASS only if a test asserts it, and
fails when that assertion is removed). Then hunt for defects the twenty steps introduced, with particular
attention to:

- the compatibility route now going through the executor: does a granted send still deliver exactly once,
  and is the legacy metadata still honest?
- the replay protection: does a legitimate retry still return its recorded outcome, and does a stale
  `call_id` from another principal now refuse without leaking the observation?
- `agent_link`: the negotiated set, the pairing-fixed principal, and the bounds — do they refuse without
  breaking a valid call?
- the redaction primitive: does every consumer now route through it, and is the fixed vocabulary still
  intact (no over-redaction)?
- the phase-5 guarantees: the bridge still optional, its events replacing and never paralleling, honest
  `external_unknown`, default-deny, single-owner inputs, and the "not delivered" status.
- the suite: run it. `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/ -q -p no:cacheprovider`

## Deliverable (write to `docs/campaigns/phase6/full-branch-review.md`)

- **VERDICT**: `APPROVE` or `REQUEST_CHANGES` (or `REJECT` if unsound)
- **FINDINGS**: one line per finding F1–F35 plus `claude:F8`, CLOSED/OPEN/OUT-OF-SCOPE, with the observed
  output or the mutation you ran
- **TEST TREATMENT**: the allowlist audit result, the structural-test mutation result, the suite count
- **REQUIREMENT MAP**: R1–R10, each with its named running test and PASS/FAIL/NOT-VERIFIABLE
- **NEW FINDINGS**: only with a concrete counterexample or an executed reproduction, each with severity
- **NOT VERIFIED**: stated plainly

This gate CLOSES phase 6. If everything you can check passes, say APPROVE plainly — and if it does not,
say exactly which findings remain open, with the reproduction that shows it.
