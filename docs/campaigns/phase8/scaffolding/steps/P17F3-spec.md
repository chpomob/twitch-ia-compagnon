# P17F3 — The AC24 comparison must not count its own fixtures

The rejected step's work is preserved on branch `loop/phase8-p17f2/1`, commit **6c11f16** (its parent is
`0e7a2ab`, the current `main`, so a plain `git cherry-pick -n 6c11f16` applies cleanly). Start from it:
everything that step restored is wanted. Fix the one finding the reviewer kept for three rounds.

## The finding (A1, from the step's own review)

The new AC24 test queries every `*.example` file in the repository and requires the comparison against the
phase base to be empty — but the step itself adds its reference fixtures as `tests/fixtures/baselines/…/*.example`,
so the test counts its own fixtures as a change and can never pass. The reviewer's evidence:
`git diff --numstat 7c29434 HEAD -- '*.example'` reports eight baseline files.

## What to do

- Take `6c11f16`.
- Restrict the AC24 comparison to the **shipped profile paths** (the repository's own `*.example` profiles),
  excluding `tests/fixtures/**`. The proof must still be the real git comparison against the phase base —
  work-tree and base-commit existence included — not a comparison of committed copies.
- Keep everything else 6c11f16 restored: the three A2 nodes real and exempted by name, `test_kick.py`
  byte-identical to `7782e5f`, the 41 functions' assertions back.

## Acceptance

- `git diff --numstat 7782e5f HEAD -- tests/test_kick.py` is empty.
- The AC24 test passes **and** fails if a shipped profile is modified (prove it by touching one in memory
  or in a copy — do not leave the tree modified).
- The assertion-AST comparison reports **0** changed existing assertion sequences outside the eight
  allowlisted functions and the three A2 nodes; state the counts you measured.
- The guard still refuses an unlisted node that opens a socket or spawns (execute the negative control).
- Full suite green with the project interpreter:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/ -q -p no:cacheprovider`.
- No new dependency; no skip/xfail added.

Reply with the A1 fix in one paragraph, the AST counts, the empty Kick diff, the negative-control result and
the full-suite count.
