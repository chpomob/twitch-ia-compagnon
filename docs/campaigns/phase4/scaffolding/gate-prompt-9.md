# Phase 4 — full-branch closing gate, NINTH pass (after the uniform-identity and ignore-pattern fix)

Repo: `/media/chpo/HDD-papa/twitch-ia-compagnon`, branch `main`. History: gates 1–8 raised and closed
F1–F5, N1–N5 and N7; gate 8 (`gate-report-8.md`) left **N6 and N3 PARTIALLY RESOLVED** with the explicit
requirement that **every identity not from the render the session currently shows is refused, uniformly**,
and raised **N8 (LOW)**: the `.gitignore` patterns the plan's P17 required (`*.local.yaml`,
`*.status.json`) were omitted. Two fix steps have since landed.

**Write your report to `docs/campaigns/phase4/scaffolding/gate-report-9.md`.** Writing THAT ONE FILE is
explicitly authorized and required. Create, stage or modify nothing else — no `git add`, no commits, no
other file. Review the whole branch diff, not one commit at a time.

Reproduce first, then judge. Replay gate 8's failing cases and its full defeat table, and attempt to defeat
the fix.

## Your job

1. **N6 / N3 (MEDIUM)** — required: no identity value is ever reissued (allocation survives history
   eviction), an identity from another process can never match, and every route that consumes an identity
   decides with **one shared predicate** and answers with the **same** status and shape for the same class
   of refusal. Re-run the gate's two failing cases:
   - a foreign UI process submitting an authentic identity with the receiver's current layout, fingerprint,
     CSRF token and controls;
   - an old identity made stale by exercising the production allocator past its history bound.
   Then re-run the whole defeat table (older render, other session, other tab, re-render after an unrelated
   save, mixed stale/current, foreign page, gate-6/gate-7 hidden-key and remove/save cases) and report each
   outcome.
2. **N8 (LOW)** — re-run the gate's reproduction I (`git check-ignore --no-index -v -- config.local.yaml
   config.yaml.status.json presence.local.yaml`) and report the new output; confirm the existing
   `.gitignore` entries are untouched.
3. **Re-confirm everything resolved** — F1–F5, N1, N2, N4, N5, N7 — replaying each published
   counterexample — and re-run the whole checklist of gate 1: its requirement map R1–R10 → named running
   tests, the operator-visible guarantees, and the phases 0–3 regression guarantees. Run the suite
   yourself: `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`.
4. **Hunt for defects introduced by this round** — an identity construction that breaks a legitimate
   multi-step edit or a parallel tab, a uniform refusal that now blocks a valid operation, an ignore
   pattern that hides a file the project actually tracks, a previously resolved finding quietly reopened,
   or a weakened assertion.

## Deliverable (write to `docs/campaigns/phase4/scaffolding/gate-report-9.md`)

- **VERDICT**: `APPROVE` or `REQUEST_CHANGES` (or `REJECT` if unsound), on its own line, plainly
- **OPEN ITEMS**: N3, N6, N8 — status, `file:line`, executed test, the outcome of each replay and defeat
  attempt
- **RE-CONFIRMED**: F1–F5, N1, N2, N4, N5, N7 and the gate-1 checklist, one line each with the command used
- **NEW FINDINGS**: only with a concrete counterexample or an executed reproduction, each with severity
- **NOT VERIFIED**: stated plainly

This gate CLOSES phase 4. If everything you can check passes, say APPROVE plainly.
