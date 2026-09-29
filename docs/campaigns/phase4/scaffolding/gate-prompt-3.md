# Phase 4 — full-branch closing gate, THIRD pass (after the F1/F2 residual + N1 fix round)

Repo: `/media/chpo/HDD-papa/twitch-ia-compagnon`, branch `main`. History: gate 1 raised F1–F5
(`gate-report-1.md`); gate 2 resolved F4 and F5, left **F1 PARTIALLY RESOLVED**, **F2 PARTIALLY
RESOLVED**, **F3 PARTIALLY RESOLVED**, and raised **N1** (`gate-report-2.md`). Two further fix steps have
since landed.

**Write your report to `docs/campaigns/phase4/scaffolding/gate-report-3.md`.** Writing THAT ONE FILE is
explicitly authorized and required. Create, stage or modify nothing else — no `git add`, no commits, no
other file. Review the whole branch diff, not one commit at a time.

Reproduce first, then judge: replay gate 1's and gate 2's published counterexamples for each item, and
attempt to defeat each fix.

## Your job

1. **Verify the open items**, citing `file:line` and the executed test. Status: `RESOLVED`, `PARTIALLY
   RESOLVED` or `NOT RESOLVED`.
   - **F1 (HIGH, residual)** — the collision guards compared resolved path TEXT, so two **hard links** to
     one file slipped past (`os.path.samefile` true, `u.same_file` false, empty startup checks, no
     status-path collision). Required: the decision uses **file identity**, with all four consumers
     (startup refusal, Save destination, status-path rules, runtime check) on that one decision, and a
     stated fallback for paths that do not exist yet. Try to defeat it with hard links, symlinks, a
     dangling path pair, a bind-mount-like alias, and a case variation.
   - **F2 (HIGH, residual)** — a configured **key occurrence** still reached generated `name=` attributes
     (e.g. `triggers.twitch.channels.twitch.combination`). Required: no configured key appears in any
     generated attribute, label or JSON field; identifiers are positional and opaque, and the parser maps
     them back. Try to disclose a channel id, a rule alias and a list key.
   - **N1 (MEDIUM)** — the boolean select ran its semantic tokens through the secret renderer, so a short
     secret rewrote `true` into `t[hidden]ue` and the form submitted a string. Required: redaction applies
     to rendered data only, never to the page's own structural tokens; the control renders and submits
     real booleans. Verify that fixing this did **not** reopen F2 — a real secret value must still never
     be rendered, at any length, in any position.
   - **F3 (MEDIUM)** — the honest boolean tri-state: unset (inherit), explicit false, explicit true, each
     surviving rendering, submission, parsing and the runtime's settings parser.
2. **Re-confirm the already-resolved items** (F4, F5 from gate 2) still hold after this round, and re-run
   the whole checklist of gate 1: its requirement map R1–R10 → named running tests, the operator-visible
   guarantees, and the phases 0–3 regression guarantees. Run the suite yourself:
   `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`.
3. **Hunt for defects introduced by this round** — in particular: a redaction change that reopens a
   disclosure, an identity check that refuses a legitimate overlay, an opaque identifier that breaks the
   parser round-trip, a previously-resolved finding quietly reopened, or a weakened assertion.

## Deliverable (write to `docs/campaigns/phase4/scaffolding/gate-report-3.md`)

- **VERDICT**: `APPROVE` or `REQUEST_CHANGES` (or `REJECT` if unsound), on its own line, plainly
- **OPEN ITEMS**: F1, F2, N1, F3 — status, `file:line`, executed test, and the outcome of each replay and
  defeat attempt
- **RE-CONFIRMED**: F4, F5 and the gate-1 checklist, one line each with the command used
- **NEW FINDINGS**: only with a concrete counterexample or an executed reproduction, each with severity
- **NOT VERIFIED**: stated plainly

This gate CLOSES phase 4. If everything you can check passes, say APPROVE plainly.
