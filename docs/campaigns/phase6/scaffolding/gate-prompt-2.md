# Phase 6 — full-branch closing gate (second pass)

The first pass (`docs/campaigns/phase6/full-branch-review.md`) refused approval: 29 findings closed,
**F2, F3, F4, F5 and F6 open**, with three defects in the phase's own evidence (**N1**, **N2**, **N3**).
Five fix steps have landed since — `P20F1` (the agent's retained cache checks the call identity before
serving it), `P20F2` (one redacting constructor for hook diagnostics, proven by construction), `P20F3`
(no peer-supplied string in supervision payloads), `P20F4` (the persistent store refuses a record written
after its viewer was erased), `P20F5` (the F18 guarantee restored, and the allowlist amendment recorded
in `docs/campaigns/phase6/scaffolding/plan-amendments.md`). Their specs are in
`docs/campaigns/phase6/scaffolding/steps/P20F1-spec.md` … `P20F5-spec.md`.

**Write your report to `docs/campaigns/phase6/full-branch-review-2.md`.** Writing THAT ONE FILE is
explicitly authorized and required. Create, stage or modify nothing else.

## First job: settle every finding of the first pass, with the observed output

Re-run **each probe from the first report** (A, B, C, D, E) and state CLOSED / PARTIAL / OPEN per
finding, quoting the output you observed:

- **F2/F3** — probe A: an altered retry (out-of-set action, other principal, changed arguments) on a
  retained `(id, seq)` must now be REFUSED without exposing the retained observation and without reaching
  the executor, while an exact retry still returns it with one execution.
- **F4/N1** — probe C: the offline `viewer_memory.__main__` consumer must print nothing unredacted;
  probe D: all four virtual fixtures must now FAIL the structural test (raw f-string, `.format`,
  concatenation, raw f-string preceded by an unrelated redactor call), and the positive control must pass.
- **F5** — probe B: with the pairing token in a valid call's `run_id`, no supervision record or
  diagnostic may contain it, while the call still executes and its result returns.
- **F6** — probe E: after `!forgetme` and the held run's completion, the persistent store must hold no
  record for that key and the erased text must be absent from every file under the store directory.
- **N2** — the F18 replacement must fail when the guide's UI command is changed to `--config wrong.yaml`.
- **N3** — the five tests outside the allowlist: `plan-amendments.md` must name each one, say what
  extension happened, and the diff must show an extension, never a removed assertion or a raised count
  without a behavioural reason. If the amendment does not cover one, keep N3 OPEN.

Also spot-check that the 29 closed findings did not regress: at minimum F1, F7, F8, F9, F11, F12, F21,
F24, F27, F28, F32, F35 — and the phase-5 guarantees (optional bridge, replacement not parallel, honest
`external_unknown`, default-deny, single-owner inputs, "not delivered").

## Second job: the test treatment, again

- No added `skip`, `xfail` or `importorskip`; no deleted test; no loosened assertion anywhere.
- Only the nine allowlisted tests may have replaced assertions; the five amended tests may have EXTENDED
  assertions. Compare the ASTs of every existing test function, not the summary's accounting.
- The suite: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/ -q -p no:cacheprovider`.

## Third job: what the five fixes broke

Hunt for defects the fix steps introduced, with an executed counterexample for any you report: a cache
identity that now refuses an exact retry; a redaction constructor that over-redacts the fixed vocabulary;
a supervision payload stripped of the local fields the evidence trail needs; a store refusal that also
drops a legitimate new record; an amendment entry that hides a reduction.

## Deliverable (write to `docs/campaigns/phase6/full-branch-review-2.md`)

- **VERDICT**: `APPROVE` or `REQUEST_CHANGES` (or `REJECT` if unsound)
- **PREVIOUS FINDINGS**: F1–F35, `claude:F8`, N1–N3 — each CLOSED/OPEN/OUT-OF-SCOPE/PARTIAL with the
  observed output or the mutation you ran
- **TEST TREATMENT**: the allowlist and amendment audit, the structural-test probes, the suite count
- **REQUIREMENT MAP**: R1–R10 with their named running tests and PASS/FAIL/NOT-VERIFIABLE
- **NEW FINDINGS**: only with a concrete counterexample, each with severity
- **NOT VERIFIED**: stated plainly

This gate CLOSES phase 6. If everything you can check passes, say APPROVE plainly.
