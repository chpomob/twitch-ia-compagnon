# Phase 4 — full-branch closing gate, FOURTH pass (after the value-masking and Check-layout fix round)

Repo: `/media/chpo/HDD-papa/twitch-ia-compagnon`, branch `main`. History: gate 1 raised F1–F5
(`gate-report-1.md`); gate 2 resolved F4/F5 and raised N1 (`gate-report-2.md`); gate 3 resolved F1, N1,
F3, re-confirmed F4/F5, left **F2 PARTIALLY RESOLVED** and raised **N2** (`gate-report-3.md`). Two fix
steps have since landed.

**Write your report to `docs/campaigns/phase4/scaffolding/gate-report-4.md`.** Writing THAT ONE FILE is
explicitly authorized and required. Create, stage or modify nothing else — no `git add`, no commits, no
other file. Review the whole branch diff, not one commit at a time.

Reproduce first, then judge. Replay the published counterexamples for every open item and attempt to
defeat each fix.

## Your job

1. **F2 (HIGH, residual)** — the masking was path-driven, so a secret value reused as ordinary nested
   data, as a mapping key inside a list, or inside a textarea survived (gate 3's defeats: the secret
   `a"b` as a mapping key at `modules.brain.delivery.actions[0].arguments`, and
   `modules.audio_output.voices.allowed: ['a"b']` recovered through
   `json.loads(html.unescape(textarea))[0]`). Required: masking is **value-driven** — every scalar equal
   to a secret value, at any depth, as a value or as a key, in any rendered surface, is withheld; no
   secret value is recoverable after unescaping and re-parsing. Try to defeat it: the previous defeats,
   plus a secret inside a nested list of mappings, a secret used as a list element that is itself a key
   elsewhere, a secret containing HTML entities, quotes, backslashes or a newline, and a secret whose text
   is a prefix or substring of a legitimate configured value.
2. **N2 (MEDIUM)** — `Check` parsed a submission without the layout guard, so after a base-key reorder it
   silently retargeted the fields and returned 200 with a verdict for a key the operator never edited,
   while `/save` returned 409 stale. Required: Check applies the same guard and refuses identically,
   never parsing a mismatched layout, while a matching layout still Checks normally. Try to defeat it:
   reorder keys, add/remove a key, keep the same count but change a key's name, and submit a layout from
   a stale page after a Save from another tab.
3. **Re-confirm everything already resolved** — F1 (file-identity collision), N1, F3, F4, F5 — and re-run
   the whole checklist of gate 1: its requirement map R1–R10 → named running tests, the operator-visible
   guarantees, and the phases 0–3 regression guarantees. Run the suite yourself:
   `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`.
4. **Hunt for defects introduced by this round** — a redaction now over-broad (hiding a legitimate
   configured value), a guard that refuses a valid Check, an identifier scheme that breaks the parser
   round-trip, or a previously-resolved finding quietly reopened.

## Deliverable (write to `docs/campaigns/phase4/scaffolding/gate-report-4.md`)

- **VERDICT**: `APPROVE` or `REQUEST_CHANGES` (or `REJECT` if unsound), on its own line, plainly
- **OPEN ITEMS**: F2 and N2 — status, `file:line`, executed test, and the outcome of each replay and
  defeat attempt
- **RE-CONFIRMED**: F1, N1, F3, F4, F5 and the gate-1 checklist, one line each with the command used
- **NEW FINDINGS**: only with a concrete counterexample or an executed reproduction, each with severity
- **NOT VERIFIED**: stated plainly

This gate CLOSES phase 4. If everything you can check passes, say APPROVE plainly.
