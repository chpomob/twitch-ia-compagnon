# Step P13 — Acted budgets at the terminal step, generic fallback, delivery reserve and total deadline, stale-drop fallback (R3; AC13-fallback, AC17, AC18, AC19, AC55)

Plan step `P13` of the approved Phase 1 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase1/plan.md`
- Approved specification v1.2 (requirement and acceptance-criteria wording): `docs/campaigns/phase1/spec.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

Before each model turn and each non-delivery action: when `run.remaining < delivery_reserve_seconds`, stop the loop with `failure: deadline_reserve` (status `error`) and go to fallback. `_deliver_all` records `skipped:budget_exhausted` for an entry with no action call left and `skipped:deadline_exceeded` for one reached at or after the total deadline, without invoking it; earlier entries keep their outcomes. `_fallback(run, entries, reason)` runs when the run ends without a delivered final response because of budget exhaustion, deadline expiry or `stale_drop`: when `fallback.enabled` is false → `fallback: skipped:disabled`; when no entry has an applicable rule (each entry evaluated on its own scope: `self._actions.authorized(principal=brain, destination=(platform, channel_id, entry.scope))` contains none of the entries' names) → `skipped:not_authorized`, 0 executor calls; when no action call is left or the reserve/deadline forbids every entry → `skipped:budget_exhausted` / `skipped:deadline_exceeded`; otherwise deliver `fallback.text` through `_deliver_all` (same principal, call ids continuing the counter, each entry counted against `max_action_calls`), `fallback: sent`, `delivery: fallback:<summary>`; the run `status` stays the failure status. Wire the P4 hook: `AdmissionScheduler(on_stale_drop=self._on_stale_drop)` in `BrainModule.__init__`; the hook builds the destination from the session key, resolves the delivery list, and runs `_fallback` with the remaining total budget, returning the correlation mapping; it never answers the stale text. Nothing is sent after the total deadline.

## Requirements

Plan mapping: R3
Acceptance criteria owned by this step: AC13, AC17, AC18, AC19, AC55
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

[`modules/brain/__init__.py`, `tests/test_budgets.py`, `tests/test_delivery.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P4, P12]

All dependencies are already merged into `main` when this step runs.

## Tests

`tests/test_budgets.py`: AC13 full (`fallback == "sent"`, `delivery == "fallback:success"`, 1 send); AC17 (clock at 51 s → 0 further requests and fallback; at 60 s → 0 sends, `skipped:deadline_exceeded`); AC18 (disabled, not authorized with provider invoked 0 times, action budget consumed); AC19 (queued work expiring `wait_seconds` → `stale_drop`, 0 model requests, exactly 1 fallback send; disabled → 0 sends) with the injected clock and no positive sleep. `tests/test_delivery.py`: AC55 (two-entry fallback; exactly 1 call left → `["success", "skipped:budget_exhausted"]`; no rule for either → `skipped:not_authorized`).

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`, which the proxy and the
two-process topology tests import — a `python3 -m pytest` run fails at collection there.
Every acceptance criterion this step owns must be enforced by a test that fails without the
change. Never weaken, skip, xfail or delete an existing assertion to make the suite pass; if an
existing test encodes a superseded guarantee, replace it and cite the superseding requirement in
the test docstring. Do not use positive-duration sleeps in tests — inject the clock, the RNG and
the transports.

## Risks

Non-trivial (deadline arithmetic on two clocks: the scheduler's `total_deadline` and the executor's call deadline). The stale-drop hook runs outside any run context: it must not call `run.checkpoint()` and must build its own deadline from the remaining budget the scheduler hands it. A fallback that itself times out is an `external_unknown`/`timeout` entry, never a retry.

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
