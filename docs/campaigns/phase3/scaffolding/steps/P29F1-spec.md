# Step P29F1 — gate-3 finding F8 (bounded, identity-preserving failure reporting)
commit: fix(phase3): P29F1 — memory failure reports bounded before construction, file identity preserved

Repo: `/media/chpo/HDD-papa/twitch-ia-compagnon`, branch `main`. Authority: the gate 3 report
`docs/campaigns/phase3/scaffolding/gate-report-3.md` (read **F8** in full: it carries executed counterexamples
with a table at 32/256/2048 undeletable files and a second reproduction at 70 files / 32 denied unlinks), the
phase 3 spec (R3, AC18 and the bounded-retention rules) and plan (P10).

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider` (project virtualenv).

## F8 — Major: failure reports are unbounded before supervision and lose file identity after truncation

Locations: `modules/viewer_memory/__init__.py:653` (one exceeded-bound string per oversized file),
`:662` (joins them all into the exception), `:1097` (copies the whole detail into a retained diagnostic).

Executed counterexamples (unchanged configured bounds: `max_files=3`, `max_file_bytes=512`,
`max_total_bytes=40…`): with 32 / 256 / 2048 files on disk whose unlink always raises, the largest retained
diagnostic grows to **3,630 / 28,048 / 223,378 characters** — it grows with the number of files despite
unchanged bounds — and the truncation applied downstream **loses file identity**: filenames/reasons truncated
to 3 characters at 32 files, and the whole `failures` field DROPPED at 256 and 2048. A second reproduction
(70 files, `max_files=35`, unlink denied only for the oldest 32) shows the same shape.

## Required correction (the gate's own wording)

- **Bound the diagnostic and the exception content BEFORE constructing it** — never build an unbounded string
  by joining per-file details and then truncate it.
- **Report failures through bounded records/batches that preserve the full file identity, the reason and the
  fact that the entry remained** on disk. A reader must be able to reconstruct WHICH files failed and why,
  through the bounded surface, whatever the count.
- Keep F3's guarantees intact: an unsatisfied count/total-byte/per-file bound refuses readiness explicitly, a
  healthy store still prepares, and no failure is silent.

## Tests

- Turn the gate's two reproductions into running tests (a parameterised case at 32, 256 and 2048 undeletable
  files, and the 70-files/32-denied case), asserting: the retained diagnostic stays within a stated bound
  independent of the file count, and every failed file is individually identifiable with its reason and its
  remained-state through the bounded reporting surface.
- Keep `tests/test_viewer_memory.py`'s existing gate-1/gate-2 tests green — do not weaken them.

## Constraints
- Fix the production code; the operator's configurability (retention 30 days default, total size, file count)
  and the analysis-based eviction wording stay as specified.
- The suite must stay green and must not drop below 2453 passed / 18 skipped.
- One atomic commit; message pinned above.
- Report: `file:line`, the bounded shape (names and limits), the new tests, and what an operator sees for
  1000 failed files.
