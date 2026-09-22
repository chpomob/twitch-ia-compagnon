# Step P9 — `modules/audio_output`

Plan step `P9` of the approved Phase 2 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase2/plan.md`
- Approved specification v1.2 (requirement and acceptance-criteria wording): `docs/campaigns/phase2/spec.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

Progress: `played_ms = accepted_data_bytes × 1000 // (rate × channels × bytes_per_sample)`, capped at `duration_ms`. **Deadline (decision 1, finding P4):** at entry the provider computes `expiry = min(call.deadline, clock() + spec.timeout_seconds)`; a playback watcher parked on the injected sleeper until `expiry` — **nothing subtracted** — issues `stop(process, stop_grace_seconds)` (terminate now; the kill after `stop_grace_seconds` on the sleeper, i.e. *after* the stop) and the invoke returns at once `timeout` (`error {code: "timed_out", cause: "playback", message}`, `result {played_ms, …}`); a `CancelledError` reaching the invoke during playback is caught: the same stop if not yet issued, and the record returned normally — `timeout` cause `playback` when `clock() >= expiry` (the deadline is what interrupted the playback, whether the module's watcher or the executor's timer noticed first), `cancelled` with `played_ms` otherwise; `drain()` (below) triggers the stop with status `cancelled`. One stop per playback: the first trigger fixes the status, later triggers are no-ops. The escalation (terminate → grace → kill → `wait`) runs in a supervised module task that **owns the output slot until `wait` returned** and is joined at `close`. **Serialization and ownership (finding P5):** per output an `_OutputSlot` with one `asyncio.Lock` held from `start(argv)` until `wait(process)` returned — by the call on a normal or failed exit, by the escalation task after a stop — and a waiter counter; at entry, when the output is busy and `waiters ≥ max_waiters` → `refused resource_busy` immediately (0 synthesis requests); else the waiter slot is taken, synthesis runs, then the lock is awaited and playback starts once the previous player's `wait` returned — a stopped predecessor that ignores termination holds the successor until its kill and exit, `stop_grace_seconds` after the stop; two outputs play concurrently. **Readiness at `prepare`:** for each output `shutil.which(argv[0])` (or an absolute executable path) must resolve — an output that fails is dropped from the usable set; with `synthesis.probe: true` one bounded synthesis of `probe_text` must return a parseable WAV within `timeout_seconds` (0 playback); `audio.play` is bound when ≥ 1 output is usable, `audio.speak` when in addition the probe succeeded or `probe: false` (0 requests, no `module.degraded`); `mark_ready` when ≥ 1 action is bound; every unbound action reported once through `supervision.degraded(reason=<value-free>, capabilities=[names])`; with `required: true` an unusable output or a failed probe raises `AudioOutputModuleError("audio_output prepare: field '<outputs.<name>.player|synthesis.endpoint>' unavailable")` — with `probe: false` the synthesis dependency cannot fail prepare. `drain(deadline_seconds)`: waiting calls end `cancelled` (0 starts); running players are stopped (status `cancelled`, `played_ms` reached) and their escalations joined within the drain deadline on the clock; `close` withdraws readiness and joins what remains. No constant named or valued as a lead/margin exists in the module (AC41).

## Requirements

Plan mapping: R10, R1, R8, R10
Acceptance criteria owned by this step: AC3, AC4, AC5, AC29, AC38, AC41
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

[`modules/audio_output/__init__.py`, `tests/test_audio_output.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P8, P3]

All dependencies are already merged into `main` when this step runs.

## Tests

`tests/test_audio_output.py` (through the real `ActionExecutor` of the harness, so the R10 adoption is exercised end to end): AC3/AC38 module half — a runner accepting the 44-byte header plus exactly 128 000 of the 320 000 data bytes of a 10 s segment (`FakePlayer(accept_limit=128_000)`) then blocking, call deadline `t = 100.0` on the injected clock, `stop_grace_seconds: 1`: at `t = 99.99` the runner records 0 stops and the call is still running; at `t = 100.0` exactly 1 stop, the provider's record stamped `provider_completed_at >= 100.0`, and the terminal observation is `timeout` with `error.cause == "playback"`, `result.played_ms == 4000`, `error.message` the module's (not containing "timed out with emission"), `action.completed` carrying `status: timeout`, `executor.outcomes()` holding exactly one record for the call, `COUNTER_ACTION_TIMEOUTS == 1`; at `t = 101.0` the player that ignored termination is killed (1 kill) and its exit joined. The same assertions when the executor's adoption is delayed and the record is stamped at `100.0` exactly and at `100.5` (a scripted sleeper that parks the executor's resume). A drain started at `t = 100.0` → `cancelled` with `played_ms == 4000`, 1 stop; a cancellation at `t = 50.0` → `cancelled`, `played_ms == 4000`, 1 stop. A runner released to exit 0 at `t = 99.95` (`expiry − 0.05 s`) → `success`, `played_ms == duration_ms`, 0 stops; with `stop_grace_seconds: 5` a player still blocking at `t = 99.99` → 0 stops (nothing is stopped before the deadline whatever the grace), 1 stop at `100.0`, the kill at `105.0`. 128 031 accepted data bytes → `played_ms == 4000`; everything accepted and exit 1 → `playback_failed` with `played_ms == 10000`; unstartable → `playback_failed` with 0; a runner that accepted 20 bytes (less than `data_offset`) → `played_ms == 0`; `played_ms ≤ duration_ms` always. AC4 — three concurrent `audio.speak` on one output with `max_waiters: 1`: first plays, second starts after the first exited (runner start order), third `refused resource_busy` with 0 synthesis requests; two outputs play concurrently (both started before either exited). Ownership barrier (finding P5): first call on a player that ignores termination, second call queued on the same output, the first cancelled — and, in a twin case, timed out at `t = 100.0`: the first's observation is published at once with its `played_ms` while the runner records 1 start; after `clock.advance(stop_grace_seconds)` the first player is killed, its `wait` returns, and only then is the second `start` recorded (the runner's event log shows `kill` of the first before `start` of the second); a third call arriving while the second waits → `refused resource_busy`; `drain` during the escalation completes within its deadline once the clock passes the grace. AC5/AC29 — non-executable `argv[0]` → both actions absent from `registered_ready()` and present in `discovered()`, 1 `module.degraded` with `capabilities: [audio.speak, audio.play]`; failing probe (exception, then timeout on the clock) → `audio.speak` unbound, `audio.play` bound, `module.degraded` `capabilities: ["audio.speak"]`, reason containing neither endpoint nor key; `probe: false` + usable output → 0 requests at prepare, both bound, no `module.degraded`, the same settings with `required: true` reach readiness, the first `audio.speak` against a raising transport → `tts_unavailable` with 0 starts; `required: true` with a non-executable player → prepare raises naming module and `outputs.<name>.player`; a call to an unbound action → `refused provider_not_ready`, 0 provider invocations; no probe outlives its timeout on the clock; the `api_key` value appears in no trace or diagnostic. AC41 — a file-read assertion that `modules/audio_output/__init__.py` and `module.yaml` match `adoption_lead|lead_seconds|deadline_margin|early_stop|deadline_lead` 0 times.

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`, which the proxy and the
two-process topology tests import — a `python3 -m pytest` run fails at collection there.
Every acceptance criterion this step owns must be enforced by a test that fails without the
change. Never weaken, skip, xfail or delete an existing assertion to make the suite pass; if an
existing test encodes a superseded guarantee, replace it and cite the superseding requirement in
the test docstring. Do not use positive-duration sleeps in tests — inject the clock, the RNG and
the transports.

## Risks

Non-trivial (concurrency; ordering guarantees the type system cannot check; the executor/module meeting at the deadline). The waiter count is decremented on every exit path (refusal, failure, cancellation); the output lock is released by whoever observes the player's exit, never by the observation's publication (finding P5); the escalation task runs under `ModuleTasks` so `close`/`drain` can join it, and its `wait` is bounded by the grace plus the kill's own return; the watcher and the writer race is settled by cancelling the loser before reading the accepted count; the `CancelledError` handler must return the record (not re-raise) so the executor's timer path adopts it, and must finish without awaiting anything the cancellation could interrupt again (the stop is issued synchronously, the escalation is a separate task); the watcher's due time is `expiry` exactly — any subtraction reintroduces finding P4 and fails AC41.

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
