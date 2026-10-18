# Phase 6 — full-branch closing gate (third pass)

The second pass (`docs/campaigns/phase6/full-branch-review-2.md`) closed **every** product and test
finding — F1–F33 and F35, plus N1, N2, N3 — confirmed probes A–E now hold their boundaries, passed the
allowlist and amendment audit, and found nothing the five fixes broke. It refused on **one** remaining
item, and refused correctly: **literal AC2 asked for an `action.requested` trace that the kernel does
not define**, and the reviewer declined to treat `action.requested` and `action.started` as
interchangeable on its own authority.

That item is now settled by record, not by code:

- `docs/campaigns/phase6/spec.md` — AC2 names `action.started`.
- `docs/campaigns/phase6/scaffolding/plan-amendments.md` — entry **A1** says what changed, why the
  event was not the answer (the kernel defines one start trace, `TRACE_ACTION_STARTED` in
  `core/contracts.py`, published by the executor; `action.requested` exists nowhere), and what this
  pass must check.

**Write your report to `docs/campaigns/phase6/full-branch-review-3.md`.** Writing THAT ONE FILE is
explicitly authorized and required. Create, stage or modify nothing else.

## First job: settle AC2

Check that AC2 now names `action.started`, that the test it names
(`test_twitch.py::test_ac2_the_compatibility_send_is_traced_as_an_executor_call`) asserts exactly one
`action.started` and one `action.completed` for the compatibility publication, and that **no
`action.requested` remains anywhere in the phase's documents or code**. State AC2 **PASS** or **FAIL**
with the evidence. If you believe the amendment itself is wrong — that the product should have gained an
`action.requested` event — say so plainly with the reason; the amendment is a decision, not an
instruction to agree.

## Second job: confirm nothing drifted

The amendment touched only documents, so re-run the five probes cheaply and state each finding's
disposition again: **A** (agent cache: altered retry refused, exact retry still served), **B** (no peer
string in supervision), **C** (offline consumer prints nothing unredacted), **D** (all four structural
fixtures fail, positive control passes), **E** (no erased text left on disk). If any probe no longer
holds, that finding is OPEN again and this pass refuses.

## Third job: the whole agenda once more

- **REQUIREMENT MAP**: R1–R10, each with its named running test and PASS/FAIL/NOT-VERIFIABLE.
- **TEST TREATMENT**: no added `skip`/`xfail`/`importorskip`, no deleted test, no loosened assertion;
  only the nine allowlisted replacements and the five amended extensions; the suite
  (`PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/ -q -p no:cacheprovider`).
- **NEW FINDINGS**: only with an executed counterexample, each with severity.
- **NOT VERIFIED**: stated plainly — including, as before, that no real platform, Streamer.bot
  instance, OBS, model service or audio device was contacted in this phase.

## Deliverable (write to `docs/campaigns/phase6/full-branch-review-3.md`)

- **VERDICT**: `APPROVE` or `REQUEST_CHANGES` (or `REJECT` if unsound)
- **AC2**: PASS/FAIL with the evidence
- **PREVIOUS FINDINGS**: F1–F35, `claude:F8`, N1–N3, A1 — each CLOSED/OPEN/OUT-OF-SCOPE with the probe
  output
- **REQUIREMENT MAP**, **TEST TREATMENT**, **NEW FINDINGS**, **NOT VERIFIED**

This gate CLOSES phase 6. If everything you can check passes, say APPROVE plainly.
