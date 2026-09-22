# Step P16 — `modules/stream_control`

Plan step `P16` of the approved Phase 2 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase2/plan.md`
- Approved specification v1.2 (requirement and acceptance-criteria wording): `docs/campaigns/phase2/spec.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

At `prepare`, when `polls.enabled`, the module reads `context.services.entries()`, keeps the keys whose kind is `poll` (the kind name lives in this module only) and resolves each `("poll", <platform>)` service — `entries()` and `resolve()` are the only two calls the module makes on the facade, both members of the three-method surface P4 requires of every registry (round 2, P3), and no `isinstance` check names `ServiceRegistry`, so any accepted collaborator works; it binds `stream.poll.create` once with `destinations=[Destination(platform, "*", "poll") for each resolved platform]`; none resolved (no `poll` key, or a context without a registry, whose facade returns an empty view) → the R8 policy P14 established (unbound + `module.degraded` under `required: false`; `StreamControlModuleError` naming `stream_control` and `polls.enabled` under `required: true`, 0 transports left open); `enabled: false` → unbound with no degraded trace and no failure whatever `required` is. Call: `expiry` at entry; argument bounds from `polls` else `error invalid_arguments` with 0 requests; a `_PollTable` keyed `(platform, channel_id)` holding at most `max_tracked_channels` channels and 32 entries per channel, each entry `active(poll_id, question, options, started_at, ends_at)` or `uncertain(question, options, since)` with TTL `duration_seconds + 300` s on the clock, expired entries and empty channels dropped at every access; per-channel lock with `max_waiters`. Order: acquire the slot (beyond → `refused resource_busy`); an active entry → `refused poll_active` (0 requests); an uncertain marker → one `get` first — an active poll listed → `refused poll_active`, none → proceed, `get` failing → `external_unknown` and the marker kept; table full with no reclaimable channel and the channel untracked, or a 33rd live entry → `refused resource_busy` (0 requests); then `mark_emitted()` and exactly one `create`: 2xx → `success {poll_id, state: "active", question, options, started_at, ends_at: started_at + duration, reconciled: false}` and an active entry; `PollStatusError` 401/403 → `refused platform_forbidden`; other 4xx → `error poll_rejected` (code and sanitised message); `sent=False` failure → `error platform_unavailable` with 0 effect; 5xx → one `get` — matching poll → `success reconciled: true`, none → `error platform_unavailable` carrying the status code (channel not marked), `get` failing → `external_unknown` cause `confirmation_lost` and the channel marked; `sent=True` loss, no answer within `expiry − now`, or a malformed 2xx → one `get` — a poll with the same question and options started at or after the call's start → `success reconciled: true`, else `external_unknown` cause `confirmation_lost` and the channel marked uncertain for `duration_seconds`. Reconciliation reads are bounded by `expiry − now`; the deadline reached with a create or `get` pending → the module marks the channel uncertain and returns its own `timeout` record (cause `confirmation_lost`), resolved `external_unknown` by the emission rule. `drain`: waiting calls `cancelled`, an in-flight create waits for its outcome within the drain deadline else `external_unknown` with the marker set.

## Requirements

Plan mapping: R7, R8, R10
Acceptance criteria owned by this step: AC26, AC27, AC28, AC37, AC29
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

[`modules/stream_control/__init__.py`, `tests/test_stream_control.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P15, P14, P13]

All dependencies are already merged into `main` when this step runs.

## Tests

`tests/test_stream_control.py` (section "polls", after "websocket provider"; conftest `ScriptedPollService` published under `("poll", "fake")` on the harness registry, and the fixture platform for the loader path): AC26 — `{question: "Next game?", options: [A, B], duration_seconds: 60}` → `success` with `poll_id`, `state: active`, `started_at`, `ends_at == started_at + 60`, `reconciled: false`, 1 create; the same call again → `refused poll_active` with 0 requests; after `clock.advance(361)` the create proceeds; each bound violation (61-char question, 1 option, 6 options, 26-char option, duplicates, 14 s, 1801 s) → `invalid_arguments` with 0 requests. AC27 — `lost` create → exactly 1 `get`: listing a matching poll started after the call's start → `success reconciled: true`; none → `external_unknown` cause `confirmation_lost`; `get` raising → `external_unknown`; create count 1 per call, never `success` without `poll_id`; after an `external_unknown` the next create performs 1 `get` first (active listed → `refused poll_active` with 0 creates; none → 1 create); 403 → `refused platform_forbidden`; 400 → `error poll_rejected` containing `400` and none of the body; transport before send → `error platform_unavailable` with 0 requests; 503 → 1 `get` (matching → `success reconciled: true`; none → `error platform_unavailable` containing `503`, next create performs 0 `get`; `get` raising → `external_unknown` and the next create performs 1 `get`); a `held` create at the call deadline → `external_unknown`, the channel marked, 1 create. AC28 — two sessions on one channel: 1 create total, the second `refused poll_active` after waiting; a third with `max_waiters: 1` `refused resource_busy`; two channels → 2 creates; through the loader, the fixture platform's published service receives the create. AC37 — `max_tracked_channels: 2`: `a`, `b` succeed, `c` `refused resource_busy` with 0 requests; after `a`'s TTL `c` proceeds and the table tracks exactly `b`, `c`; `a` marked uncertain with the cap reached → `d` refused, `a` keeps its marker until its TTL, the next create on `a` still reconciles first; the 33rd live entry of one channel → `refused resource_busy` with 0 requests. AC29 (finding P1) — `polls.enabled: true` and no service → `module.degraded` `capabilities: ["stream.poll.create"]`; `required: true`, `polls.enabled: true`, an empty registry → `prepare` raises, the text contains `stream_control` and `polls.enabled`, the coordinator aborts startup with 0 transports open and the action absent from `registered_ready()`; the same with a context built without `services`; `required: true` with `("poll", "fake")` published → readiness with the action bound to `fake/*/poll`; `required: true`, `polls.enabled: false`, scenes reachable → readiness, no degraded trace; through the loader with the fixture platform enabled, `required: true` passes because the platform published its service at activation. Accepted-surface proof (round 2, P3) — a minimal three-method fake registry (a test class with `publish`/`resolve`/`entries` only, not `ServiceRegistry`) holding `("poll", "fake") → ScriptedPollService`, passed as `RuntimeContext(services=fake)`: `prepare` with `polls.enabled: true` binds `stream.poll.create` to `fake/*/poll` and a create reaches the scripted service — the consumer runs against the surface P4 accepts, not the concrete class; the two-method form never reaches `prepare` because P4 refuses it at construction.

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`, which the proxy and the
two-process topology tests import — a `python3 -m pytest` run fails at collection there.
Every acceptance criterion this step owns must be enforced by a test that fails without the
change. Never weaken, skip, xfail or delete an existing assertion to make the suite pass; if an
existing test encodes a superseded guarantee, replace it and cite the superseding requirement in
the test docstring. Do not use positive-duration sleeps in tests — inject the clock, the RNG and
the transports.

## Risks

Non-trivial (invariant: one create per call, ever; a bounded table with no eviction of live entries). The `required: true` failure is raised before any poll binding and after the scene provider was closed; `mark_emitted` precedes the `create` await; the "same question and options" match compares option lists in order; the cap check runs after expiry reaping and before the create; a `CancelledError` after `create` was awaited marks the channel and resolves `external_unknown` through the emission rule.

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
