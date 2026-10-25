# Phase 9 — closing gate, fourth pass

Your second pass resolved the two credential leaks (F1, F2) and left three MEDIUM findings. Step P19F3 has
landed. Settle the three, confirm the leaks stay closed, and decide.

**Write your report to `docs/campaigns/phase9/full-branch-review-4.md`.** Writing THAT ONE FILE is
authorized and required. Create, stage or modify nothing else.

## Settle N1 — the silent language fallback

Re-run your reproduction: `language=fr` persisted, `wizard.screen2.title` removed from the French table,
the table loaded and injected into a fresh UI over the same store. Expected now: the session refuses
(non-200 or a refusal page), renders **zero** English sentences, and leaves the language at `fr`. Then
confirm a complete table still switches languages, and that an incomplete **English** table is refused on
the same terms.

## Settle N2 — the assertion audit

Re-run the baseline/current AST comparison and name every changed existing test function. For each: the
allowlist entry that authorizes it, or the restored sequence. State the count outside the allowlist — it
must be zero, or each survivor must be named with the requirement that forces it and the reason it is a
constant rather than a loosening.

## Settle N3 — under Amendment A2

Re-run the diff/AST/decorator audit: no added `skip`, `skipif`, `xfail` or `importorskip` may remain, and
the 18 functions that carried the skipif alias must now run (the platform branches are exercised by forcing
the platform value). Report the count you measure.

## Confirm the earlier findings hold

- F1 and F2: re-run their exact reproductions — a canary secret in a child's stderr yields zero hits in the
  page, the status record and the server's stderr; a 9,000+ character secret leaves no suffix fragment in
  the raw detail; the negative control still shows the operator that the child failed.
- Both full suites green: the project interpreter and the 3.10 venv, with both summary lines.
- No test passes because the machine is slow: re-check the pattern you audited last pass.

## Deliverable (`docs/campaigns/phase9/full-branch-review-4.md`)

- **VERDICT**: `APPROVE` / `REQUEST_CHANGES` / `REJECT`
- **N1, N2, N3**: RESOLVED / PARTIAL / UNRESOLVED with the measured counts
- **F1, F2**: still RESOLVED, with the reproductions re-run
- **REGRESSIONS**, **NEW FINDINGS** (executed reproduction only), **NOT VERIFIED**

The operator trial record is still `not run`; its absence does not block this gate, and the phase stays
**not delivered** until it exists and is accepted.

This gate CLOSES phase 9. If everything you can check passes, say APPROVE plainly.
