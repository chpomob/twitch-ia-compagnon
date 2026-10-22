# P17F4 — CI parity: Python 3.10 is supported, and the suite must prove it

The GitHub CI is red on the phase head, and only on **Python 3.10** (3.11, 3.12 and 3.13 pass). Two causes,
both reproduced locally with a real 3.10 interpreter.

## Cause 1 — a 3.11-only API in a test fake (a regression of this phase)

`tests/test_automation_bridge.py:1286` does `async with asyncio.timeout(FAKE_WAIT_SECONDS):`.
`asyncio.timeout` was added in Python 3.11; on 3.10 the module has no such attribute, so the test fails with
`AttributeError: module 'asyncio' has no attribute 'timeout'`. The comment above it explains why
`wait_for` was avoided (it would run the coroutine to completion), so keep that property: bound the wait and
cancel the task without awaiting it to completion. Use only 3.10-compatible asyncio (`wait`, `TimerHandle`,
`cancel`).

## Cause 2 — a load-sensitive test that has been flaky since before this phase

`tests/test_presence_pack.py::test_ac6_the_presence_pack_runs_every_observation_row` asserts
`thanks["status"] == "success"` after `presence.drive(...)` and receives `"timeout"` under load on CI. It
passes on 3.10 in isolation here, so it is a synchronisation defect, not a compatibility one — the same class
as the phase-6 `settle()` flake that produced P8F1. Make it deterministic: wait for the observable the
assertion needs (the audit/observation flush, the run's terminal record) under a bounded deadline, with no
`sleep`-counting and no enlarged timeout that merely hides the race.

## How to prove it here, not on CI

A Python 3.10 environment is ready and complete (aiohttp, PyYAML, pytest, pytest-asyncio, setuptools,
tomli):

    /home/chpo/.hermes/cache/scratch/venv310/bin/python -m pytest tests/ -q -p no:cacheprovider

Run the **whole suite** there and state the count. Then run it again with the project interpreter
(`.venv/bin/python`, 3.11) and state that count too: both must be green.

## Acceptance

- The full suite is green on **3.10** and on **3.11** (both commands above, counts reported).
- The two named tests pass on 3.10; the automation-bridge one no longer references `asyncio.timeout`, and a
  static check finds no other 3.11-only asyncio API in `tests/` or `modules/` (`asyncio.timeout`,
  `asyncio.TaskGroup`, `asyncio.Runner`).
- The presence test cannot return `"timeout"` because of scheduling: prove it by running it 20 times in a
  loop under load (a parallel busy loop is enough) with zero failures, or by asserting the synchronisation
  point it now waits for.
- No new dependency; no skip/xfail; no public behaviour change outside these two tests.

Reply with both suite counts, the 3.10 run's summary line, the 20-run loop result, and what replaced
`asyncio.timeout`.
