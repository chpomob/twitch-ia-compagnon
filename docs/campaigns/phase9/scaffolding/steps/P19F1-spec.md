# P19F1 — No test passes because the machine is slow

The CI went red on Python 3.13 while 3.10, 3.11 and 3.12 were green, on a commit that changed only
documentation. The cause is a test from phase 8 whose verdict depends on how fast the interpreter is.

## The defect

`tests/test_web.py::test_p3f1_one_huge_construct_still_reaches_the_deadline[quoted-attributes]`:

- the first half measures that the whole page extracts in under 6 s — reasonable;
- the second half sets `deadline = time.monotonic() + 0.2` and expects `ExtractionAbandoned`.

On 3.13 that body extracts in **less than 0.2 s**, so the deadline is never reached, no exception is
raised, and the test fails — while the product behaved correctly. It passed on 3.13 in the phase-8 run by
luck, and the failure has now surfaced on an unrelated commit.

## What to do

1. **Make that test deterministic.** A wall-clock budget that assumes the machine is slow is not a
   deadline test. Use a budget that has already expired (or a clock that reports the expiry on its first
   read), assert `ExtractionAbandoned`, and prove the walk stopped **early** by measuring it against the
   full extraction duration observed in the first half — a ratio, not a constant. The property the test
   exists for (the walk abandons a huge construct instead of parsing every attribute) must still be
   proven, and the test must still fail if the abandonment is removed.
2. **Sweep the suite for the same class.** Find every test whose pass depends on the clock advancing past
   a small budget — the pattern is a `time.monotonic() + <small number>` (or an injected clock advanced by
   a small step) followed by an assertion that something timed out or was abandoned. For each: make it
   deterministic the same way, or report it with the reason it cannot be. Do not lengthen a budget to hide
   the race.
3. **Prove it under speed.** Run the fixed tests repeatedly on the floor interpreter (3.10), the project
   interpreter (3.11) and 3.13 if available:

       ~/.hermes/cache/scratch/venv310/bin/python -m pytest <files> -q -p no:cacheprovider

   20 consecutive runs with zero failures each, and the full suite green on 3.10 and 3.11.

## Acceptance

- The named test passes deterministically on every interpreter you can run, 20 times in a row, and fails
  when `ExtractionAbandoned` is disabled in memory (sensitivity proven).
- The sweep's result is stated: either every instance is fixed, or each remaining one is named with the
  reason.
- No test is skipped, xfailed, deleted or loosened; no budget is lengthened to swallow the race.
- Full suite green: `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider` and on the 3.10 venv.

Reply with the fixed tests, the sweep's findings, the 20-run results per interpreter, and both suite counts.
