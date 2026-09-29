# Phase 4 — full-branch closing gate, SIXTH pass (after the placeholder-collision fix)

Repo: `/media/chpo/HDD-papa/twitch-ia-compagnon`, branch `main`. History: gate 1 → F1–F5; gate 2 →
F4/F5 resolved, N1 raised; gate 3 → F1/N1/F3 resolved, N2 raised; gate 4 → F2/N2 resolved, N3/N4 raised;
gate 5 → **N4 resolved**, N3 partially, **N5 raised** (`gate-report-5.md`). One fix step has since
landed, aimed at the stand-in allocation those two share.

**Write your report to `docs/campaigns/phase4/scaffolding/gate-report-6.md`.** Writing THAT ONE FILE is
explicitly authorized and required. Create, stage or modify nothing else — no `git add`, no commits, no
other file. Review the whole branch diff, not one commit at a time.

Reproduce first, then judge. Replay gate 5's published counterexamples for N5 and N3, and attempt to
defeat the fix.

## Your job

1. **N5 (MEDIUM)** — stand-ins were allocated per exact secret, so two distinct keys that merely *contain*
   a secret collapsed into one and the first entry (with its non-secret value) disappeared; a legitimate
   marker-looking configured key also collided. Required: every redacted configured key gets a
   collision-free, distinct representation, and a collision that cannot be avoided by construction fails
   loudly instead of losing data. Try to defeat it: two keys containing the same secret, two keys
   containing different secrets, a key equal to a secret, a key that looks like a stand-in, a secret that
   is a substring of another secret, and a list of mappings whose entries share a redacted key.
2. **N3 (MEDIUM, residual)** — (a) a stand-in edited by the operator (e.g. `... edited`) was accepted as
   ordinary text and written literally; (b) posting a stand-in key together with its real key silently
   lost a value. Required: any stand-in that is not an exact match, and any submission that would map two
   entries to one key, is refused explicitly with a named reason — never written literally, never
   resolved by guessing. Try to defeat it: truncate a stand-in, add whitespace, case-change it, submit a
   stand-in-shaped string the renderer never produced, and post both the stand-in and the real value.
3. **Re-confirm everything resolved** — F1, F2, F3, F4, F5, N1, N2, N4 — replaying each published
   counterexample — and re-run the whole checklist of gate 1: its requirement map R1–R10 → named running
   tests, the operator-visible guarantees, and the phases 0–3 regression guarantees. Run the suite
   yourself: `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`.
4. **Hunt for defects introduced by this round** — a stand-in scheme that hides a legitimate value, a
   refusal that blocks a legitimate edit, a restoration that still guesses, a previously resolved finding
   quietly reopened, or a weakened assertion.

## Deliverable (write to `docs/campaigns/phase4/scaffolding/gate-report-6.md`)

- **VERDICT**: `APPROVE` or `REQUEST_CHANGES` (or `REJECT` if unsound), on its own line, plainly
- **OPEN ITEMS**: N5 and N3 — status, `file:line`, executed test, the outcome of each replay and defeat
  attempt
- **RE-CONFIRMED**: F1–F5, N1, N2, N4 and the gate-1 checklist, one line each with the command used
- **NEW FINDINGS**: only with a concrete counterexample or an executed reproduction, each with severity
- **NOT VERIFIED**: stated plainly

This gate CLOSES phase 4. If everything you can check passes, say APPROVE plainly.
