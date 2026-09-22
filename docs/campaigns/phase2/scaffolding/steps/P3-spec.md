# Step P3 — Executor adopts a late provider-authored interruption record; late `success`/`refused` still generic; timer still in force (R10; AC38–AC40 executor halves)

Plan step `P3` of the approved Phase 2 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase2/plan.md`
- Approved specification v1.2 (requirement and acceptance-criteria wording): `docs/campaigns/phase2/spec.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

In `_run_invocation`, add the private predicate `_is_interruption_record(task) → bool`: the task ended (not cancelled, no exception) with an `ActionObservation` whose `status ∈ {"timeout", "cancelled", "error"}`. (a) **Provider wins, `completed_at >= expires_at`:** if `_is_interruption_record(provider_task)` → `return await self._validate_observation(...)` — parts validated (`_reject_parts`, P2), the emission rule applied unchanged through `_uncertain_status` (an emitted write's `timeout` still resolves `external_unknown`; a `mark_not_emitted` provider keeps `timeout`/`cancelled`), the provider's `error` (its `code`, `cause`, `message`) and `result` (its `played_ms`) kept verbatim, nothing else rewritten; otherwise (a `success`, a `refused`, a non-observation, an exception, a cancelled task) → the phase 1 path: `_consume` and `_terminate_uncertain(certain="timeout")`. The comment at line 1317 is rewritten to state R10's ruling: "a late confirmation is never a success; a late interruption the provider observed itself is the true record". (b) **Timer wins:** `await self._cancel(provider_task)` as today; then if `_is_interruption_record(provider_task)` (the provider caught the cancellation, stopped its device and answered within `_cancel_grace`) → `_validate_observation`; otherwise the generic `_terminate_uncertain(certain="timeout")` — the executor's timer stays in force for a provider that never answers. The `except CancelledError` branch (external cancellation) is untouched (decision 1). The boundary for non-interruption records stays `>=` on `provider_completed_at`, never on the executor's resume instant. No audio, platform or vendor name; the docstring of `_run_invocation` gains the three-line rule.

## Requirements

Plan mapping: R10
Acceptance criteria owned by this step: AC38, AC40
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

[`core/actions.py`, `tests/test_actions.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P2]

All dependencies are already merged into `main` when this step runs.

## Tests

`tests/test_actions.py` (new cases beside the two phase 1 tests, which are **not modified** — AC40): AC38 executor half — a scripted write provider that calls `mark_not_emitted()` and returns `ActionObservation(status="timeout", error={code: "timed_out", cause: "playback", message: "player stopped at the call deadline"}, result={played_ms: 4000})` with the clock set so the stamp is `== expires_at`, and again `> expires_at` (`+0.5`), yields that record verbatim (`error.cause == "playback"`, `result.played_ms == 4000`, `error.code` the provider's, `message` the provider's — not "timed out with emission"), `action.completed` carries `status: timeout`, exactly one terminal record for the call, `COUNTER_ACTION_TIMEOUTS` incremented once; the same record from a write provider that called `mark_emitted()` ends `external_unknown` exactly as in time (emission rule unchanged); a provider `cancelled` record with `played_ms` stamped `== expires_at` is adopted as `cancelled`. AC39 executor half — a scripted read provider returning `error {code: capture_timed_out}` stamped `== expires_at` yields `error.code == "capture_timed_out"`, never `timed_out`. Timer path — a provider that awaits the injected sleeper for longer than the budget and, on `CancelledError`, returns `timeout {cause: "playback"}`: the clock is advanced to expiry, the executor's timer wins, the provider's record is adopted (1 record, the provider's cause present). AC40 — a read provider's `success` stamped `> expires_at` and a `refused` stamped `== expires_at` are each replaced by the generic record (`timeout`, code `timed_out`, no `result`, the refusal code absent); a provider that never returns (parks on a future that no clock advance releases) is cancelled by the timer at expiry and the call ends with the generic record whose message contains "timed out with emission", `COUNTER_ACTION_TIMEOUTS` incremented — the timer is in force; an interruption record with an invalid `audio_ref` part stamped `>= expires_at` is rejected as `invalid_result` through the ordinary path (no partially adopted parts). `test_a_confirmation_landing_after_the_deadline_is_never_a_success` and `test_a_confirmation_landing_in_time_survives_a_late_adoption` stay green byte-for-byte (the gate diffs them).

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`, which the proxy and the
two-process topology tests import — a `python3 -m pytest` run fails at collection there.
Every acceptance criterion this step owns must be enforced by a test that fails without the
change. Never weaken, skip, xfail or delete an existing assertion to make the suite pass; if an
existing test encodes a superseded guarantee, replace it and cite the superseding requirement in
the test docstring. Do not use positive-duration sleeps in tests — inject the clock, the RNG and
the transports.

## Risks

Non-trivial (branching in the executor's single most delicate function; an ordering guarantee the type system cannot check). The adoption must go through `_validate_observation` and nothing else, so parts, schema and emission rules stay single-sourced; `_is_interruption_record` must consult `task.cancelled()` before `task.exception()` (a cancelled future raises on `exception()`); on the timer path `_cancel` may return `False` (task abandoned past the grace) — then the record is *not* read and the generic path applies; a provider `error` record after emission stays `error` (the emission rule never covered `error`, unchanged). The executor keeps no module knowledge: the predicate reads status names only.

## Constraints

- One atomic commit for the whole step when the suite is green; Conventional Commit message
  (`feat(phase2): ...`, `fix(phase2): ...`, `test(phase2): ...`, `refactor(phase2): ...`).
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
