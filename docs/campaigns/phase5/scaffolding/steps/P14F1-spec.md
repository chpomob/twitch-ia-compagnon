# Step P14F1 — a module's own health report is not overwritten by a successful phase observation (deviation D10)

Fix step created during the Phase 5 campaign. It is **not** one of the plan's numbered steps: it
exists because step P14 stopped before editing anything, exactly as its instructions require, and
reported a kernel defect that would have made an unreachable instance look healthy. Its full report is
preserved at `docs/campaigns/phase5/scaffolding/loop-out/phase5-p14/01_build.json`.

Authority documents (read them, do not re-derive):
- Approved plan: `docs/campaigns/phase5/plan.md`
- Approved specification: `docs/campaigns/phase5/spec.md`
- Brief with the operator's binding decisions: `docs/campaigns/phase5/brief.md`
- The record of this amendment and its rationale: `docs/campaigns/phase5/scaffolding/plan-amendments.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules).
Working tree at `main`.

## Problem

The plan has the bridge's connection attempt run in `start_inputs` and, when the instance is
unreachable, report `supervision.degraded(reason=<one of five>)`, which is to yield one
`module.degraded` record and leave the module degraded (R1: absence or unavailability of the instance
never breaks the companion; R6: the bridge is ready **only** while connected, authenticated and
holding a reconciled inventory).

The kernel overwrites that report immediately. In `core/lifecycle.py`, `PhaseCoordinator._run_hook`
ends with

    await self._observe(phase, name, failure, deadline_at=deadline_at)

for **every** hook that returned, failure or not. In `core/runtime.py`, `_PHASE_STATES` maps
`PHASE_START_INPUTS` to `MODULE_STATE_READY`, and the state transition deduplicates on
`(state, reason)`. After the module has reported `(degraded, "unreachable")`, the coordinator's
`(ready, None)` is a **new** fact, so it publishes `module.ready` and records the module as ready.

The existing comment above `_PHASE_STATES` shows the reasoning that missed this case: "A module with no
producer role is ready at this point and an input module re-reports nothing when its producer opens,
because a repeated state is not re-emitted." That holds for a module that never spoke; it does not hold
for one that reported `degraded` during the same phase.

Consequences if implemented as the plan is written: an unreachable or refused instance leaves the
bridge **ready**, with a `module.ready` record published *after* its own `module.degraded`. And
acceptance criterion AC3's literal wording ("exactly 1 `module.degraded` with `unreachable`") still
passes, which is precisely how the defect would hide. The reporter said so explicitly and refused to
build on it.

## The fix, and why it is general rather than local

The reporter proposed suppressing the observation only when `phase == PHASE_START_INPUTS` and the
current state is degraded. That is correct as far as it goes, but the same clobbering exists for
`PHASE_PREPARE` (a module that reports `degraded` there and whose prepare succeeds), and the class of
defect is one: **a phase observation may not overwrite a state the module reported itself.**

Implement the general rule:

- On a hook that did **not** fail, the phase's state is reported only when the module has not reported
  a state of its own in this activation. A module-authored state stands until the module reports
  another one, or the module is closed.
- Closing still reports `stopped`: `PHASE_CLOSE` and `PHASE_CLOSE_OBSERVATION` are unchanged.
- A `prepare` that succeeds with no self-report still reports `ready`, and an ordinary `start_inputs`
  still reports `ready` — the phase-0/phase-4 lifecycle records are unchanged for every module that
  reports nothing. Today that is **every** module: a grep of `modules/` for `supervision.degraded` or
  `supervision.ready` returns 0 hits, so no existing behaviour depends on the current overwrite.
- Keep the change inside the health-observation path; do not touch hook invocation, deadlines,
  failure propagation or the phase order.

## Files

[`core/runtime.py`, `tests/test_lifecycle.py`]

Touch ONLY these files. If you believe another file must change, stop and report it instead of
editing it.

## Dependencies

[P11]

All dependencies are already merged into `main` when this step runs.

## Tests

In `tests/test_lifecycle.py`, using the existing `PhaseCoordinator`/`ModuleHealth` harnesses:

- a module that reports `degraded(reason=X)` inside `start_inputs` and whose hook returns ends in state
  `degraded`, with exactly one `module.degraded` published and **no** `module.ready` after it;
- the same for a module that reports `degraded` inside `prepare` and whose prepare returns normally;
- a module that reports nothing is still `ready` after `prepare` and after `start_inputs`, with the
  same records as today (no duplicate `module.ready` for the two phases);
- `close` still reports `stopped` for both a healthy module and a module that had reported degraded;
- a module that reports `degraded` and later reports `ready` itself ends ready — the module keeps
  control of its own state.

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`, which the proxy and the
two-process topology tests import — a `python3 -m pytest` run fails at collection there.
The suite must not drop below 3,410 passed / 18 skipped, the count when this step starts.

## Risks

- The lifecycle records are the observable contract of phases 0–4; a change here can silently alter
  `module.ready`/`module.degraded`/`module.stopped` counts that other tests assert. Run the whole
  suite, and if an existing test encodes the overwrite, do not weaken it: it encodes a guarantee this
  step supersedes, so replace the assertion and cite R1/R6 of the phase 5 specification in its
  docstring, and report it in your final message.
- Do not add a state-machine state, a new record type or a new counter: this is an observation rule,
  not a lifecycle feature.

## Constraints

- One atomic commit for the whole step when the suite is green; Conventional Commit message
  (`fix(phase5): P14F1 — ...`). The scope MUST be `phase5`.
- Keep the phase-0 to phase-4 guarantees in force, in particular the single global startup/shutdown
  deadline, failure propagation from hooks, and the phase order.
- No new runtime dependency; no model, provider or vendor name in code, config or commit messages.
- Report at the end: what changed, the exact test command output count, and anything you could not do
  because this amendment did not cover it.
