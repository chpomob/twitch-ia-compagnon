# Step P20 — `modules/agent_link`: agent-side WebSocket JSON client, default-deny execution, call de-duplication, backoff (R6; AC34, AC36-agent, AC37, AC33 in-process)

Plan step `P20` of the approved Phase 1 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase1/plan.md`
- Approved specification v1.2 (requirement and acceptance-criteria wording): `docs/campaigns/phase1/spec.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

Manifest v2 (role `input`, settings `brain_url`, `pairing_token` (credential), `agent_id`, `actions[]`, `reconnect{initial_seconds 1, multiplier 2, max_seconds 30}`, `heartbeat_seconds`, `heartbeat_timeout_seconds`, `call_table{max_entries 1024, ttl_seconds 300}`, `max_frame_bytes`, `action_seconds`; validator refuses a non-loopback `brain_url` not using `wss`, unknown schemes, non-finite bounds). `prepare`: resolve each listed action from the local `discovered()` catalog (unknown → not ready naming `actions[<i>]`). `start_inputs`: spawn the dial loop through `context.tasks` (a `connector` seam defaulting to `aiohttp.ClientSession.ws_connect`; sleeper and `context.rng` injected): send `hello` (`id` = nonce from rng, `agent_id`, `token`, `actions[]` as ActionSpec-shaped declarations including `delivery`, `max_frame_bytes`); on `welcome` reset backoff, `last_seq = 0`, clear the call table, send `event agent.status {state: paired}`; on failure or drop wait full-jitter `rng.random() * min(initial·multiplier^n, max)` on the injected sleeper (AC37: `[0.5, 1, 2, 4, 8, 15]` with rng 0.5), unlimited attempts until `stop_inputs`. Frame handling: `call` → validate `seq` (missing/non-positive → `error invalid_frame`; `seq > last_seq` → new, execute, `last_seq = seq`; `seq ≤ last_seq` and `call_id` retained → resend the retained observation; retained under another `seq` → `invalid_frame`; not retained → `error duplicate_call_unknown`, `retryable: false`); execution builds an `ActionCall` with the forwarded principal and identities, deadline `now + min(remaining_ms/1000, action_seconds)` on the agent's monotonic clock (`deadline_utc` ignored), invokes the local default-deny executor; for `image_ref` parts the bytes are read from the local store and sent as `attachment` header + one binary frame, the ack awaited before the `observation` (an unaccepted ack → the part is dropped and the observation becomes `error attachment_refused`); the call table stores `(call_id → (seq, observation))` bounded by `max_entries` (oldest displaced) and `ttl_seconds` on the clock. `cancel` → cancel the local task. `ping`/`pong` and heartbeat as the brain side. `stop_inputs` closes the connection and stops the loop.

## Requirements

Plan mapping: R6
Acceptance criteria owned by this step: AC34, AC36, AC37, AC33
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

[`modules/agent_link/__init__.py`, `modules/agent_link/module.yaml`, `tests/test_proxy.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P16, P19]

All dependencies are already merged into `main` when this step runs.

## Tests

`tests/test_proxy.py`: AC34 (agent clock 3 h ahead: bound 5 s for `remaining_ms: 5000`; 10 s for 20 000 with `action_seconds: 10`); AC36 agent side (repeat of retained call → 0 executions, same observation; after `ttl + 1` → `duplicate_call_unknown`; never-seen `seq ≤ last_seq` → `duplicate_call_unknown`; `max_entries: 2` eviction; new `welcome` resets); AC37 backoff sequence and reset after `welcome`; AC33 in-process (brain proxy and agent link on the two ends of `MemoryWebSocketPair`, capture served through the agent's local executor: same brain-side call sequence, traces and store cleanup as the local provider; exactly one attachment header + one binary frame + one ack); AC35 from the agent side (drop while a call is in flight; reconnect pairs again without retransmission); hello includes `delivery` on declared write actions; AC31's grep extended to `modules/proxy modules/agent_link`.

Test command (must pass): `python3 -m pytest tests/ -q -p no:cacheprovider`
Every acceptance criterion this step owns must be enforced by a test that fails without the
change. Never weaken, skip, xfail or delete an existing assertion to make the suite pass; if an
existing test encodes a superseded guarantee, replace it and cite the superseding requirement in
the test docstring. Do not use positive-duration sleeps in tests — inject the clock, the RNG and
the transports.

## Risks

Non-trivial (network boundary, bounded state under reconnection). The call table keyed by `call_id` must be reset on every `welcome` even when the brain reissues the same `session_id` prefix; a table lookup must never execute a call twice. The agent's executor must be the agent process's own default-deny executor — the `agent.yaml` rule (P21) grants `screen.capture` to principal `brain` only.

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
