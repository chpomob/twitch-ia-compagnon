# Phase 4 — full-branch closing gate, SEVENTH pass (after the patch-semantics redesign)

Repo: `/media/chpo/HDD-papa/twitch-ia-compagnon`, branch `main`. History: gates 1–6 raised and mostly
closed F1–F5 and N1–N4; gate 6 (`gate-report-6.md`) left **N5 and N3 PARTIALLY RESOLVED** and raised
**N6**, all three from the same design: the page posted a masked copy of the configuration and the server
reconstructed real values from it. The operator has since decided the replacement, and one architectural
step has landed: **the form transports edits, not a document**, with stable opaque field identities and
server-side merging onto the real configuration.

**Write your report to `docs/campaigns/phase4/scaffolding/gate-report-7.md`.** Writing THAT ONE FILE is
explicitly authorized and required. Create, stage or modify nothing else — no `git add`, no commits, no
other file. Review the whole branch diff, not one commit at a time.

Reproduce first, then judge. Replay the published counterexamples for N3, N5 and N6, and attempt to defeat
the new mechanism.

## Your job

1. **N6 (MEDIUM)** — two list entries whose withheld keys differed only by which secret they contained
   shared one stand-in, so editing a visible value silently changed another entry's hidden key. Required:
   per-entry identity preserved end to end; an edit touches exactly what it names, and nothing else.
   Try to defeat it: two entries with identical visible values, identical withheld keys, keys differing
   only by a secret, entries reordered, an entry removed, an entry duplicated, and a submission that
   reuses an identity from another page or another tab.
2. **N5 (MEDIUM)** — two distinct keys that merely contained a secret collapsed into one stand-in and the
   first entry (with its non-secret value) disappeared. Required: no collapse, no lost value.
3. **N3 (MEDIUM, open)** — an edited or truncated stand-in was accepted as literal text and written, and a
   submission posting a stand-in plus its real value silently lost a value; Check and Save also disagreed
   (a false failure for a valid configuration). Required: one shared real-input path for Check and Save,
   and explicit refusal of anything ambiguous — unknown, stale, foreign or duplicated identity, or an edit
   inside a withheld portion — never a guess, never a literal write, never a silent loss.
   Try to defeat it: truncate, case-change, whitespace-pad or recombine an identity; post two identities
   for one place; post an identity from a page rendered before a Save; post an edit that touches a
   withheld portion.
4. **Re-confirm everything resolved** — F1–F5, N1, N2, N4 — replaying each published counterexample — and
   re-run the whole checklist of gate 1: its requirement map R1–R10 → named running tests, the
   operator-visible guarantees, and the phases 0–3 regression guarantees. Run the suite yourself:
   `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`.
5. **Hunt for defects introduced by this redesign** — a patch that silently drops a legitimate edit, an
   identity that leaks a configured key, a refusal that blocks a valid operation, a control whose value a
   legitimate edit cannot express, or a previously resolved finding quietly reopened.

## Deliverable (write to `docs/campaigns/phase4/scaffolding/gate-report-7.md`)

- **VERDICT**: `APPROVE` or `REQUEST_CHANGES` (or `REJECT` if unsound), on its own line, plainly
- **OPEN ITEMS**: N3, N5, N6 — status, `file:line`, executed test, the outcome of each replay and defeat
  attempt
- **RE-CONFIRMED**: F1–F5, N1, N2, N4 and the gate-1 checklist, one line each with the command used
- **NEW FINDINGS**: only with a concrete counterexample or an executed reproduction, each with severity
- **NOT VERIFIED**: stated plainly

This gate CLOSES phase 4. If everything you can check passes, say APPROVE plainly.
