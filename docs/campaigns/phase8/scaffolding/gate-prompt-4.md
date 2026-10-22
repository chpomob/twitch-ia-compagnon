# Phase 8 — closing gate, fourth pass

Your third pass would have been this one; the third fix step is landing now. Two fix steps have been added
since your second pass: **P17F3** (the A1 finding of the rejected restoration step) and **P17F4** (CI
parity on Python 3.10). Settle both, confirm the earlier findings still hold, and decide.

**Write your report to `docs/campaigns/phase8/full-branch-review-4.md`.** Writing THAT ONE FILE is
authorized and required. Create, stage or modify nothing else.

## Settle F3 — the test-preservation gate, including A1

1. **The assertion-AST comparison**, baseline `7782e5f` versus HEAD: it must report **0 changed existing
   assertion sequences outside the eight allowlisted functions and the three A2 nodes**, and no removed
   function name without a documented replacement. Give the counts you measure.
2. **`tests/test_kick.py`** must be byte-identical to the phase base
   (`git diff --numstat 7782e5f HEAD -- tests/test_kick.py` empty).
3. **A1 specifically**: the AC24 comparison must not count its own fixtures. Verify the comparison covers
   the shipped profile paths and excludes `tests/fixtures/**`, that it still uses the real git comparison
   against the phase base (work tree and base commit), and that it fails when a shipped profile is modified.
4. **The three A2 nodes** are real and named in the guard's journaled list; the negative control (an
   unlisted node creating a socket or spawning) still raises. Execute it.

## Settle CI parity — Python 3.10

The GitHub CI was red on the phase head and **only on 3.10** (3.11/3.12/3.13 green). Two causes:

- `tests/test_automation_bridge.py` used `asyncio.timeout`, a 3.11 API, in the fake Streamer.bot instance;
- `tests/test_presence_pack.py::test_ac6_...` is load-sensitive and returned `"timeout"` on CI.

Verify, by running it yourself:

    /home/chpo/.hermes/cache/scratch/venv310/bin/python -m pytest tests/ -q -p no:cacheprovider
    PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/ -q -p no:cacheprovider

Both must be green; give both summary lines. Then confirm no 3.11-only asyncio API remains in `tests/` or
`modules/` (`asyncio.timeout`, `asyncio.TaskGroup`, `asyncio.Runner`), and that the presence test no longer
depends on scheduling luck (look at what it now waits for, and run it repeatedly if you need to).

## Then confirm nothing regressed

- F1 (hard hook bound) and F2 (no transcription before a decision): re-run one probe each.
- R1–R9 and the 40 criteria: unchanged from your second pass, or state what moved.
- No new skip/xfail/deletion; no new dependency; no socket outside A2's three names.

## Deliverable (`docs/campaigns/phase8/full-branch-review-4.md`)

- **VERDICT**: `APPROVE` / `REQUEST_CHANGES` / `REJECT`
- **F3**: RESOLVED / PARTIAL / UNRESOLVED — AST counts, Kick diff, A1 evidence, A2 list, negative control
- **CI PARITY**: both suite summary lines, the API scan, the presence determinism
- **F1, F2**: still RESOLVED, with the probes
- **REGRESSIONS**, **NEW FINDINGS** (executed reproduction only), **NOT VERIFIED**

This gate CLOSES phase 8. If everything you can check passes, say APPROVE plainly.
