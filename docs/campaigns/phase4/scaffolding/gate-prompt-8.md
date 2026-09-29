# Phase 4 — full-branch closing gate, EIGHTH pass (after the identity/operation fix)

Repo: `/media/chpo/HDD-papa/twitch-ia-compagnon`, branch `main`. History: gates 1–7 raised and closed
F1–F5 and N1–N5; gate 7 (`gate-report-7.md`) confirmed **N5 RESOLVED** and the patch-over-stable-identities
mechanism holding against a dozen defeats, but left **N6 and N3 PARTIALLY RESOLVED** (an old token could
equal a current one after a removal plus a save, so a stale submission retargeted) and raised **N7** (a
withheld ordinary scalar could not be set to the empty string because "unchanged" was inferred from
equality with the rendered replacement). One fix step has since landed.

**Write your report to `docs/campaigns/phase4/scaffolding/gate-report-8.md`.** Writing THAT ONE FILE is
explicitly authorized and required. Create, stage or modify nothing else — no `git add`, no commits, no
other file. Review the whole branch diff, not one commit at a time.

Reproduce first, then judge. Replay the published counterexamples for N3, N6 and N7, and attempt to defeat
the fix.

## Your job

1. **N6 / N3 (MEDIUM)** — required: identities are **unique per render**, so an identity from an earlier
   render can never coincide with a current one; any identity not from the render the session is showing is
   **refused explicitly** with the same reload outcome the layout guard produces, changing and committing
   nothing, identically on Check and Save. Try to defeat it: the gate's exact case (remove one entry, save
   another with the same policy, submit the old token), an identity from an older render of the same page,
   one from another session or tab, one from another UI process, a re-render triggered by an unrelated
   save, and a submission mixing a current and a stale identity.
2. **N7 (MEDIUM)** — required: "unchanged" is transported independently of the replacement value (an
   explicit per-field operation), so an operator can set an ordinary scalar to the **empty string** and can
   clear an optional entry, with Check and Save agreeing on the real resulting document, while a genuinely
   unchanged field still sends nothing. Try to defeat it: set a withheld scalar to the empty string, to a
   string that looks like the rendered replacement, to whitespace only, and to a value equal to the current
   one; clear an optional entry; remove a list entry; and confirm a no-op submission still commits nothing.
3. **Re-confirm everything resolved** — F1–F5, N1, N2, N4, N5 — replaying each published counterexample —
   and re-run the whole checklist of gate 1: its requirement map R1–R10 → named running tests, the
   operator-visible guarantees, and the phases 0–3 regression guarantees. Run the suite yourself:
   `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`.
4. **Hunt for defects introduced by this round** — a per-render identity that breaks a legitimate
   multi-step edit, a refusal that blocks an ordinary save, an operation flag that can express an
   unintended deletion, a previously resolved finding quietly reopened, or a weakened assertion.

## Deliverable (write to `docs/campaigns/phase4/scaffolding/gate-report-8.md`)

- **VERDICT**: `APPROVE` or `REQUEST_CHANGES` (or `REJECT` if unsound), on its own line, plainly
- **OPEN ITEMS**: N3, N6, N7 — status, `file:line`, executed test, the outcome of each replay and defeat
  attempt
- **RE-CONFIRMED**: F1–F5, N1, N2, N4, N5 and the gate-1 checklist, one line each with the command used
- **NEW FINDINGS**: only with a concrete counterexample or an executed reproduction, each with severity
- **NOT VERIFIED**: stated plainly

This gate CLOSES phase 4. If everything you can check passes, say APPROVE plainly.
