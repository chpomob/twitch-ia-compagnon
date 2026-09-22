# Step P15 — `modules/stream_control`

Plan step `P15` of the approved Phase 2 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase2/plan.md`
- Approved specification v1.2 (requirement and acceptance-criteria wording): `docs/campaigns/phase2/spec.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

`WebSocketSceneProvider(url, password, connect_timeout, request_timeout, *, websocket_factory, sleeper, clock, rng)` speaking obs-websocket protocol 5 (RPC version 1): connect through the factory (`aiohttp` `ws_connect` by default), expect `Hello` (op 0) within `connect_timeout_seconds`; send `Identify` (op 1) with `rpcVersion: 1`, `eventSubscriptions: 0` and, when `Hello.d.authentication` is present, `authentication = base64(sha256(base64(sha256(password + salt)) + challenge))`; expect `Identified` (op 2) with `negotiatedRpcVersion == 1`; a close with code 4009, another version or no `Identified` in time → unreachable at prepare / disconnected afterwards. Requests are `Request` (op 6) / `RequestResponse` (op 7) pairs matched by a generated `requestId`: `GetSceneList` (the prepare probe issues exactly this one), `GetCurrentProgramScene`, `SetCurrentProgramScene {sceneName}`; `requestStatus.result == false` with code 600 → the scene-unknown answer, any other unsuccessful status on the set → `scene_not_applied` carrying the numeric code and never the `comment`; a response missing within `request_timeout_seconds` → the "no answer" case of P14; no event subscription, no other request type; the password appears in no frame and no trace. A reader task owned by `ModuleTasks` dispatches responses; a lost socket marks the provider disconnected, the module withdraws readiness (`module.degraded`) and a supervised reconnection loop retries with delays `rng.random() × min(30, 1 × 2ⁿ)` on the injected sleeper, emitting `module.ready` and re-binding on the first success; `close` stops the loop and the socket. The vendor name stays inside `modules/stream_control/` and the README.

## Requirements

Plan mapping: R6
Acceptance criteria owned by this step: AC36, AC22
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

[P14]

All dependencies are already merged into `main` when this step runs.

## Tests

`tests/test_stream_control.py` (section "websocket provider", against an in-process loopback peer built on `MemoryWebSocketPair` and, once, a real `aiohttp` loopback server to prove the default factory's framing — timeouts on the clock, no sleep): AC36 — with password `"secret"` and `Hello {rpcVersion: 1, authentication: {challenge: "abc", salt: "xyz"}}` the module sends one `Identify` with `rpcVersion: 1`, `eventSubscriptions: 0`, `authentication == "REWw9R5R6WPBVKaASURdjK10mBqyNuldXAaq+k6jOaI="`; the password appears in 0 frames; a `Hello` without `authentication` gets an `Identify` without it; after `Identified` the prepare probe is exactly 1 `GetSceneList`; `stream.scene.set {scene: Talking}` produces exactly 1 `SetCurrentProgramScene {sceneName: Talking}` then 1 `GetCurrentProgramScene`, each answered on its own `requestId`, `success` when the read-back names `Talking`; `requestStatus {result: false, code: 600}` → `scene_unknown` with 0 read-back; `{result: false, code: 207, comment: "boom"}` → `scene_not_applied` whose message contains `207` and not `boom`; no answer within `request_timeout_seconds` → 1 reconciliation `GetCurrentProgramScene`; close 4009 after `Identify`, `Identified {negotiatedRpcVersion: 2}`, or silence for `connect_timeout_seconds` → unbound at prepare with `module.degraded`, and `error provider_unavailable` when it happens after readiness. AC22 — reconnection delays drawn on the injected RNG/clock are 1, 2, 4 … s capped at 30 with full jitter, `module.ready` after the first successful reconnection with the action back in the ready view.

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`, which the proxy and the
two-process topology tests import — a `python3 -m pytest` run fails at collection there.
Every acceptance criterion this step owns must be enforced by a test that fails without the
change. Never weaken, skip, xfail or delete an existing assertion to make the suite pass; if an
existing test encodes a superseded guarantee, replace it and cite the superseding requirement in
the test docstring. Do not use positive-duration sleeps in tests — inject the clock, the RNG and
the transports.

## Risks

Non-trivial (network boundary; the reader task, the request futures and the reconnection loop share state). The `requestId` map fails every pending future on disconnect so a caller never waits past its bound.

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
