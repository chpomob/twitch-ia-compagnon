# Phase 4 — full-branch closing gate, FIFTH pass (after the placeholder-design fix)

Repo: `/media/chpo/HDD-papa/twitch-ia-compagnon`, branch `main`. History: gate 1 raised F1–F5
(`gate-report-1.md`); gate 2 resolved F4/F5, left F1/F2/F3 partial and raised N1 (`gate-report-2.md`);
gate 3 resolved F1, N1 and F3 and raised N2 (`gate-report-3.md`); gate 4 resolved **F2, N2 and every
remaining item**, and raised **N3** and **N4** (`gate-report-4.md`). One fix step has since landed,
aimed at the placeholder design those two findings share.

**Write your report to `docs/campaigns/phase4/scaffolding/gate-report-5.md`.** Writing THAT ONE FILE is
explicitly authorized and required. Create, stage or modify nothing else — no `git add`, no commits, no
other file. Review the whole branch diff, not one commit at a time.

Reproduce first, then judge. Replay gate 4's published counterexamples for N3 and N4, and attempt to
defeat the fix.

## Your job

1. **N4 (MEDIUM)** — redaction rewrote JSON punctuation, so a one-character secret `"` turned the
   legitimate `['kept', 'voice-two']` into invalid JSON (`[[hidden]kept[hidden], ...]`) and a one-character
   `\` did the same through a legitimate quote; the control became uneditable and Save refused. Required:
   masking happens on **data before serialisation**, with JSON syntax and escaping preserved. Try to
   defeat it with every punctuation and escape character as the secret (`"`, `\`, `[`, `]`, `{`, `}`, `,`,
   `:`, `/`, and a newline), inside a list, a nested mapping, a mapping key and a textarea; and confirm
   the rendered document still contains no recoverable secret.
2. **N3 (MEDIUM)** — Check validated masking placeholders while Save restored the real values, producing a
   **false failure** for a valid configuration (`field 'voices.default': must be an allowed voice`) where
   Save of the identical submission succeeded. Required: one shared normalisation/restoration path for
   both routes, both validating the real values; an ambiguous or edited placeholder refused explicitly,
   never guessed. Try to defeat it: nested keys, reordered list elements, a duplicated placeholder, an
   edited placeholder, and a submission mixing a restored and an unrestored occurrence of the same value.
3. **Re-confirm everything resolved** — F1, F2, F3, F4, F5, N1, N2 — re-running each published
   counterexample, and re-run the whole checklist of gate 1: its requirement map R1–R10 → named running
   tests, the operator-visible guarantees, and the phases 0–3 regression guarantees. Run the suite
   yourself: `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`.
4. **Hunt for defects introduced by this round** — a masking that hides a legitimate configured value, a
   restoration that guesses a wrong value, a refactor that diverges Check from Save again, a previously
   resolved finding quietly reopened, or a weakened assertion.

## Deliverable (write to `docs/campaigns/phase4/scaffolding/gate-report-5.md`)

- **VERDICT**: `APPROVE` or `REQUEST_CHANGES` (or `REJECT` if unsound), on its own line, plainly
- **OPEN ITEMS**: N3 and N4 — status, `file:line`, executed test, the outcome of each replay and defeat
  attempt
- **RE-CONFIRMED**: F1–F5, N1, N2 and the gate-1 checklist, one line each with the command used
- **NEW FINDINGS**: only with a concrete counterexample or an executed reproduction, each with severity
- **NOT VERIFIED**: stated plainly

This gate CLOSES phase 4. If everything you can check passes, say APPROVE plainly.
