# Step P14 — `modules/stream_control`

Plan step `P14` of the approved Phase 2 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase2/plan.md`
- Approved specification v1.2 (requirement and acceptance-criteria wording): `docs/campaigns/phase2/spec.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

Manifest v2 `name: stream_control`, no events, no roles; actions `stream.scene.set` (write, `[stream.scene]`, `*/*/stream`, `delivery: {text_argument: none}`, args `scene`; result `{scene, previous_scene, confirmed_at, reconciled}`; `timeout_seconds: 15`) and `stream.poll.create` (write, `[stream.poll]`, `*/*/poll`, no delivery, args `question`, `options[]`, `duration_seconds`; result `{poll_id, state, question, options[], started_at, ends_at, reconciled}`; `timeout_seconds: 20`); `settings_schema` `scenes {provider {kind ∈ none|scripted|websocket, url, password, connect_timeout_seconds: 5, request_timeout_seconds: 5}, allowed[], max_waiters: 1}`, `polls {enabled: false, max_question_chars: 60, min_options: 2, max_options: 5, max_option_chars: 25, min_duration_seconds: 15, max_duration_seconds: 1800, max_waiters: 1, max_tracked_channels: 256}`, `required: false`, accepted `limits`, no lead/margin key; `credentials: [scenes.provider.password]`; `validate_settings`: `allowed` 1..64-character names, a `websocket` URL must be `ws://` on a loopback host (`127.0.0.0/8`, `::1`, `localhost`) or `wss://`, refused naming `scenes.provider.url` and never the value. `__init__.py`: a `SceneProvider` protocol (`connected`, `list_scenes()`, `current_scene()`, `set_scene(name)`, `close()`), kinds `none` (declared, not bound) and `scripted` (from the `_scene_provider` seam), plus the `_sleeper`, `_websocket_factory` seams read at `activate`. `stream.scene.set`: `expiry` computed at entry as in decision 1; `scene ∉ allowed` → `refused scene_not_allowed` (provider uninvoked); disconnected provider → `error provider_unavailable`, `mark_not_ready` + `module.degraded` `capabilities: ["stream.scene.set"]`; per-provider lock with `max_waiters` (beyond → `refused resource_busy`); `previous = current_scene()`; `mark_emitted()` then `set_scene` → scene unknown → `error scene_unknown` (0 effect, no read-back); other failure status → `error scene_not_applied` with the numeric code; then read-back `current_scene()` — equal → `success {scene, previous_scene, confirmed_at, reconciled: false}`; different → `error scene_not_applied`; no answer to the set or a disconnection before the read-back → exactly one reconciliation `current_scene()` bounded by `expiry − now` — equal → `success reconciled: true`, else `external_unknown` cause `confirmation_lost`; the call deadline reached while a set or read is pending → the module's own `timeout` record (cause `confirmation_lost`), which the executor resolves `external_unknown` through the emission rule (R10); never a second set in one call. `drain(deadline_seconds)`: waiting calls end `cancelled` (0 set commands); an in-flight set waits for its read-back within the drain deadline on the clock, else `external_unknown`. `prepare`: kind `none` binds nothing for scenes; `scripted`/`websocket` probe reachability (`list_scenes()` once) — success binds `stream.scene.set`, failure leaves it unbound with `module.degraded` (value-free reason) or fails prepare under `required: true` naming `scenes.provider.url`. **Polls policy (finding P1):** this step performs no registry lookup, so `polls.enabled: true` always counts as "no poll service resolved", and the R8 policy is established here exactly as for scenes — `required: false` → `stream.poll.create` unbound with one `module.degraded` `capabilities: ["stream.poll.create"]`, reason `"no poll service published"`; `required: true` → `prepare` raises `StreamControlModuleError("stream_control prepare: field 'polls.enabled' unavailable: no poll service published for any platform")`, naming the module and the dependency field `polls.enabled`, with the scene provider closed first (0 transports left open); `polls.enabled: false` never degrades nor fails; dependencies are checked in settings order (scenes, then polls) and the first failing one aborts under `required: true`; `mark_ready` when ≥ 1 action is bound. Add `"modules.stream_control" = ["module.yaml"]` to `pyproject.toml`.

## Requirements

Plan mapping: R6, R8, R9, R10
Acceptance criteria owned by this step: AC21, AC22, AC23, AC24, AC29, AC41
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

[`modules/stream_control/__init__.py`, `modules/stream_control/module.yaml`, `pyproject.toml`, `tests/test_stream_control.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P4, P5, P3, P10, P13]

All dependencies are already merged into `main` when this step runs.

## Tests

`tests/test_stream_control.py` (section "scenes", after "platform poll services"): AC21 — scripted provider with `Talking`/`Gaming`, current `Gaming`, `allowed: [Talking]`: `{scene: Talking}` → `success`, `previous_scene: Gaming`, `reconciled: false`, exactly 1 set and 1 read-back; `{scene: Gaming}` → `refused scene_not_allowed` with 0 provider calls; `allowed: [Talking, Ghost]` + `{scene: Ghost}` → `error scene_unknown` with 0 set commands; read-back returning `Gaming` → `scene_not_applied`. AC22 (scripted part) — a provider swallowing the set answer: 1 set, 1 reconciliation read; read `Talking` → `success reconciled: true`; read `Gaming` → `external_unknown` cause `confirmation_lost`; read raising → `external_unknown`; set count stays 1; disconnected at call time → `error provider_unavailable`, `module.degraded` `capabilities: ["stream.scene.set"]`, action absent from `registered_ready()`; a read-back still pending at the call deadline (held answer, clock driven to `expiry`) → `external_unknown`, 1 set, 0 further requests. AC23 — two sessions serialized (second set after the first read-back, provider order), third with `max_waiters: 1` `refused resource_busy`; drain: a waiting call `cancelled` with 0 sets, an in-flight set gets its read-back within the drain deadline or `external_unknown` when the clock passes it first; the coordinator's shutdown completes within its deadline in both cases. AC24 — `validate_settings` refuses `ws://192.0.2.1:4455` naming `scenes.provider.url` and not the value, accepts `ws://127.0.0.1:4455` and `wss://example.invalid:4455`; the `password` value appears in no trace; `kind: none` leaves the action discovered and unbound, and a harness proxy allowlisting `stream.scene.set` binds it without an ambiguity diagnostic. AC29 — `required: false` with an unreachable scripted provider → exactly 1 `module.degraded`, readiness reached, `refused provider_not_ready` on call; `required: true` → prepare raises naming `stream_control` and `scenes.provider.url`; `required: true`, scenes reachable, `polls.enabled: true` → prepare raises naming `stream_control` and `polls.enabled` (not `scenes.provider.url`), the scene provider closed (0 transports open), `stream.scene.set` not left bound; `required: true` with `polls.enabled: false` → readiness; the package-data guard passes; the AC41 regex matches 0 lines in `modules/stream_control/`.

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`, which the proxy and the
two-process topology tests import — a `python3 -m pytest` run fails at collection there.
Every acceptance criterion this step owns must be enforced by a test that fails without the
change. Never weaken, skip, xfail or delete an existing assertion to make the suite pass; if an
existing test encodes a superseded guarantee, replace it and cite the superseding requirement in
the test docstring. Do not use positive-duration sleeps in tests — inject the clock, the RNG and
the transports.

## Risks

Non-trivial (branching; concurrency; an uncertain outcome never memorised). `mark_emitted()` precedes the `set_scene` await so a cancellation during the command resolves `external_unknown`; the waiter counter and lock are released on every path; the drain never starts a reconciliation read after the drain deadline; the `required: true` failure for polls is raised after the scene provider is closed.

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
