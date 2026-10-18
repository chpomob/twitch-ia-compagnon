# Step P8F1 — the erasure test synchronises on the audit flush, not on loop turns

Post-merge fix step of Phase 6. The campaign halted at **P8** with `post-merge tests RED`: the suite
came back red immediately after the merge, then green on the next run, and red again on the following
one (~50 %). The failing test is P7's, not P8's:

    tests/test_brain.py::test_ac14_a_run_admitted_before_the_erasure_records_nothing_after_it

It passes alone (`1 passed in 0.23s`) and passes with its whole file (`213 passed`), and it passes in
pairs with `test_viewer_memory`, `test_admission`, `test_integration` and `test_lifecycle`. It fails only
in the full suite, under load.

## Root cause (confirm it from the failure before changing anything)

The test decides that the erasure's audit records have been written by calling `settle()`, which is
`20 × await asyncio.sleep(0)` — a turn count, not a wall-clock bound. The audit module writes through a
queue consumer, so its line can land after those 20 turns on a loaded machine, and
`assert audit_lines and ...` then fails with an empty list.

**Confirmed on a failing full-suite run** (two consecutive runs, one green one red, same tree):

    tests/test_brain.py:3834: in test_ac14_a_run_admitted_before_the_erasure_records_nothing_after_it
        assert audit_lines and not any(carries_erased_text(line) for line in audit_lines)
    E   assert ([])

The captured stdout of that same failure shows the bus still emitting the erasure's own events
(`channel.chat.message` m-forget, `input.trigger.accepted`, `brain.admission.accepted`) while the audit
sink has written nothing yet — the work is queued, the turns ran out.

The helper `wait_until` in `tests/conftest.py` documents exactly this hazard: "A turn count bounds
loop-only work, but not work handed to a worker thread … a loaded machine can outlast any turn count",
and offers a `seconds=` wall-clock bound for it. A second timing assumption sits in
`assert harness.brain.memory.refused_records == 1`: the count depends on where the erasure lands
relative to the held run's completion.

## Required resolution

Make the test deterministic without weakening what it asserts:

- Replace the post-erasure `settle()` + `assert audit_lines` with an explicit wait on the observable the
  assertion needs — the audit line itself — using the wall-clock-bound form of `wait_until` (or the
  audit module's own flush signal if it exposes one), so a loaded machine cannot outrun it.
- Keep the invariant unconditional: no bus event and no audit line produced by the erasure may carry the
  erased text, whether the erasure ran before, during or after the held run.
- Make `refused_records` deterministic by driving the interleaving explicitly — the held run is released
  only after the erasure has completed, as the test already intends — rather than by relying on a
  turn count. If the count can legitimately be 0 or 1 depending on ordering, hold the run until the
  erasure's completion is observable, then release.
- Prove the assertion still has teeth: with the brain's refusal removed (a mutation of the production
  code), the test must fail. Record the mutation in the step report.

## Files

[`tests/test_brain.py`, `tests/conftest.py`]

Touch ONLY these files. `tests/conftest.py` only if the helper needs a small addition (a wall-clock
`wait_until` already exists there — prefer using it to extending it). If the root cause turns out to be
in production code rather than in the test's choreography, **stop and report it**: this step is written
for a test-synchronisation defect, and a production race would need its own step.

## Tests

- `tests/test_brain.py::test_ac14_a_run_admitted_before_the_erasure_records_nothing_after_it` passes 20
  consecutive times on its own (`--count` is not installed: run it in a shell loop).
- The full suite passes **three consecutive times**: `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`.
  This is the evidence that matters — the defect only appears there.
- The mutation of the refusal makes the test fail; restore it and confirm green.

## Constraints

- Never weaken the assertion to `>= 0`, drop the audit-line check, or add a `sleep` to hide the race.
- No production file changes unless the root cause is there — in which case stop and report.
- Conventional commit once green: `fix(phase6): P8F1 — the erasure test synchronises on the audit flush`.
- The suite must not drop below 3,796 passed / 26 skipped.
