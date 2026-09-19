# Step P4 — Stale-drop hook on `AdmissionScheduler` (R3; AC19 groundwork)

Plan step `P4` of the approved Phase 1 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase1/plan.md`
- Approved specification v1.2 (requirement and acceptance-criteria wording): `docs/campaigns/phase1/spec.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

Add `on_stale_drop: Callable[[Work, SessionKey, float], Awaitable[Any] | Any] | None = None` to `AdmissionScheduler.__init__` (validated callable when given). In `_complete_stale`, after the `stale_drop` record is built and before `_publish_completion`, call the hook exactly once with the work, the session key and the remaining total budget (`total_deadline - now`, ≥ 0), bounded by that remaining budget through `_bounded_by`; the hook's return value, when a mapping, is merged into the record's correlation (`fallback`, `delivery`, `deliveries`); an exception in the hook is counted and diagnosed, never raised, and the record still stands. The scheduler knows nothing about fallbacks: it hands over work, session and budget.

## Requirements

Plan mapping: R3
Acceptance criteria owned by this step: AC19
Read the exact R/AC text in the specification file before writing code. Requirements not listed
here are other steps' responsibility — do not implement them.
Phase-1 product decision that overrides any contrary reading: delivery is a **configured, pluggable
terminal step** — a configured ordered list of delivery actions (mode `fixed`, or mode `modules`
derived from the enabled modules that declare a delivery capability, with a configurable preference
order), each entry declaring how the answer text maps into its arguments (a named argument, or none
for an effect-only delivery such as a stream-scene change); every entry runs at the terminal step
only, each receiving the text only if declared; the model never selects the delivery and never
performs an intermediate effect; adding a delivery module must require no change to the agentic loop.

## Files

[`core/admission.py`, `tests/test_admission.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[]

All dependencies are already merged into `main` when this step runs.

## Tests

`tests/test_admission.py`: the hook is called exactly once per stale item with `(work, session_key, remaining)`, remaining is the total budget minus the wait already spent; a hook that raises leaves the `stale_drop` record and its `brain.run.completed` intact and counts one failure; a hook returning `{"fallback": "sent"}` shows in the record; the 4 existing constructors run unchanged without the hook.

Test command (must pass): `python3 -m pytest tests/ -q -p no:cacheprovider`
Every acceptance criterion this step owns must be enforced by a test that fails without the
change. Never weaken, skip, xfail or delete an existing assertion to make the suite pass; if an
existing test encodes a superseded guarantee, replace it and cite the superseding requirement in
the test docstring. Do not use positive-duration sleeps in tests — inject the clock, the RNG and
the transports.

## Risks

Non-trivial (ordering guarantee: record before publication, hook before publication). The hook runs inside the reaper: an unbounded hook would stall every other stale sweep — hence the `_bounded_by` cap.

## Constraints

- One atomic commit for the whole step when the suite is green; Conventional Commit message
  (`feat(phase1): ...`, `fix(phase1): ...`, `test(phase1): ...`, `refactor(phase1): ...`).
- Keep the phase-0 guarantees in force: bounded admission, phase lifecycle, explicit terminal action
  outcomes, default-deny authorization (reads included), bounded retention, a single global
  startup/shutdown cleanup deadline, redaction of configured secrets in traces and loss diagnostics.
- No module name may be added to `core/main.py` — a new module starts and stops through its manifest.
- Platform neutrality: contracts and brain must not depend on a specific platform; a second fake
  platform exercises the same contracts.
- No new runtime dependency beyond the existing ones unless this step is the one that adds it; no
  model, provider or vendor name in code, config or commit messages.
- Report at the end: what changed, the exact test command output count, and anything you could
  not do because the plan did not cover it.
