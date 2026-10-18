# Phase 6 — full-branch closing gate (fourth pass)

The third pass (`docs/campaigns/phase6/full-branch-review-3.md`) kept every product finding closed, kept
probes A–E passing, and accepted the amended AC2 behaviour. It refused on **one documentary point**, and
it was right to: `action.requested` still appeared in **two prescriptive clauses** — the plan's AC2 line
and the P2 step spec.

Both are now aligned, and the absence criterion itself is corrected:

- `docs/campaigns/phase6/plan.md` and `scaffolding/steps/P2-spec.md` name `action.started`; the step spec
  carries a note pointing at amendment A1.
- `scaffolding/plan-amendments.md`, entry A1, now scopes the check to **prescriptive text** (the
  specification's criteria, the plan's steps, the step specs, the tests). The amendment record, the gate
  reports and the gate prompts quote `action.requested` on purpose: they are the history of the change,
  and an absence check that counted them would forbid the record that explains why the name changed.
  The third pass's finding came from the prompt's over-broad wording, not from a product defect.

**Write your report to `docs/campaigns/phase6/full-branch-review-4.md`.** Writing THAT ONE FILE is
explicitly authorized and required. Create, stage or modify nothing else.

## First job: settle A1, on the corrected criterion

1. AC2 names `action.started`, and `test_twitch.py::test_ac2_the_compatibility_send_is_traced_as_an_executor_call`
   asserts exactly one `action.started` and one `action.completed` for the compatibility publication.
2. No **prescriptive** text names `action.requested`: search the specification's criteria, the plan's
   steps, the step specs and the tests. Quotations inside `plan-amendments.md`, the three gate reports and
   the gate prompts are historical record and are expected.
3. State A1 **PASS** or **FAIL** with the search output. If you still believe the product should gain an
   `action.requested` event, say so with the reason — A1 is a decision, not an instruction to agree.

## Second job: confirm nothing drifted

Re-run probes **A–E** and state each disposition again: A (altered retry refused, exact retry served,
one execution), B (no peer string in supervision, the call still executes), C (the offline consumer
prints nothing unredacted), D (all four structural fixtures fail, positive control passes), E (no erased
text left on disk). Any regression re-opens its finding and this pass refuses.

## Third job: the whole agenda

- **REQUIREMENT MAP**: R1–R10, each with its named running test and PASS/FAIL/NOT-VERIFIABLE.
- **TEST TREATMENT**: no added `skip`/`xfail`/`importorskip`, no deleted test, no loosened assertion;
  only the nine allowlisted replacements and the five amended extensions; the suite
  (`PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/ -q -p no:cacheprovider`).
- **NEW FINDINGS**: only with an executed counterexample, each with severity.
- **NOT VERIFIED**: stated plainly — including that no real platform, Streamer.bot instance, OBS, model
  service or audio device was contacted in this phase.

## Deliverable (write to `docs/campaigns/phase6/full-branch-review-4.md`)

- **VERDICT**: `APPROVE` or `REQUEST_CHANGES` (or `REJECT` if unsound)
- **A1**: PASS/FAIL with the search output
- **PREVIOUS FINDINGS**: F1–F35, `claude:F8`, N1–N3 — each CLOSED/OPEN/OUT-OF-SCOPE with the probe output
- **REQUIREMENT MAP**, **TEST TREATMENT**, **NEW FINDINGS**, **NOT VERIFIED**

This gate CLOSES phase 6. If everything you can check passes, say APPROVE plainly.
