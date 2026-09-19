# Step P12 — Multi-turn agentic loop: transcript, read-only tools, proposals and synthetic observations, image parts, budgets, call ids, release (R1, R2, R3 partial, R4; AC1–AC7, AC46, AC11, AC12, AC13-loop, AC14–AC16, AC24, AC25, AC47-model side)

Plan step `P12` of the approved Phase 1 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase1/plan.md`
- Approved specification v1.2 (requirement and acceptance-criteria wording): `docs/campaigns/phase1/spec.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

Replace the single-turn body by the loop: `_Transcript` (system instructions, viewer message, retained memory, then alternating assistant tool-call / tool-result entries; text parts rendered as tool-result content, `image_ref` parts kept as references and encoded by P10 at request time; bounded by `max_tokens` through the existing estimate). Each turn: re-read the authorized view **per action and per declared scope** — `_offered_tools(platform, channel_id)` walks `self._actions.discovered()`, keeps specs with `nature == "read"`, and for each takes the scope component of every `supported_destinations` entry (`chat` for `chat.read`/`users.read`, `capture` for `screen.capture`; a spec declaring several scopes is evaluated once per scope) and offers the spec iff `self._actions.authorized(principal=brain, destination=Destination(platform, channel_id, <that scope>))` contains its name; a single `chat`-scoped query would never see `screen.capture`, whose binding is `*/*/capture` (AC6). The tool list is the union over the offered specs (never a delivery action, no `[send:` tag in the system prompt); `run.checkpoint()`; `note_model_call()`; the model call bounded by `min(model_call_seconds, run.remaining)`; classify: `_Unsupported` → outcome `error`, `failure: unsupported_response_shape`, 0 actions; `_Final` → terminal step through P11's `_deliver_all`; `_Proposal` → (1) name absent from `discovered()` → synthetic `refused` `unknown_action`; (2) nature ≠ `read` → synthetic `refused` `not_a_read_action`; (3) arguments not a JSON object → synthetic `error` `malformed_arguments`; else (4) repeated-action check on `(name, canonical arguments)` — the (n+1)-th identical proposal with `n == max_repeated_actions` is not executed and ends the run `error` `budget_exhausted` `budget: max_repeated_actions`; (5) `action_calls == max_action_calls` → `budget_exhausted` `budget: max_action_calls`; (6) build `ActionCall` with destination `(platform, channel_id, spec's declared scope)`, principal `brain`, `call_id = <run_id>/call-<n>`, deadline `min(now + action_seconds, total_deadline)`, invoke the executor, append the observation (status, error code, result mapping, parts) to the transcript; an `image_ref` part in a run whose `verified_capabilities` lack `vision` ends the run `error` `capability_missing`, `capability: vision`, without a request and with the attachment released; an `image_ref` whose lease expired at the next turn (store lookup on the clock) ends the run `error` `attachment_expired`; the observation-size bound is re-checked here with `budget.max_observation_bytes` (`observation_too_large`, images released) for a runtime whose executor bound is `None`. Turn counter: every proposal, synthetic or executed, counts one turn; `turns == model_turns` before another model call → `budget_exhausted` `budget: model_turns`; cumulative tokens over `max_tokens` → `budget: max_tokens` with `tokens`, `tokens_estimated`. A model call exceeding `model_call_seconds` → `timeout`. Outcome correlation: `turns`, `action_calls`, `tokens`, `tokens_estimated`, `fallback` (`"none"` here, P13 fills it), `deliveries`, `delivery`, `failure`, `budget`, `capability`. Transcript and images are released at run end on every exit path (`_release_run` already frees leases; the transcript is a local). Cancellation during a capture, the model call or delivery propagates `CancelledError` after releasing. `tests/test_brain.py`: the two allowlisted tests are rewritten to the phase 1 guarantee (read actions offered as tools, delivery never offered; the per-turn re-read is re-asserted with a `chat.read` grant appearing on turn 2).

## Requirements

Plan mapping: R1, R2, R3, R4
Acceptance criteria owned by this step: AC1, AC7, AC46, AC11, AC12, AC13, AC14, AC16, AC24, AC25, AC47
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

[`modules/brain/__init__.py`, `tests/test_agentic_loop.py`, `tests/test_budgets.py`, `tests/test_observations.py`, `tests/test_brain.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P3, P11]

All dependencies are already merged into `main` when this step runs.

## Tests

`tests/test_agentic_loop.py` (a scripted read provider bound in the test as `chat.read`/`screen.capture` doubles — the real modules join in P17): AC1 (3 executor calls with `call-1..3`, 1 send, `turns: 3`, `action_calls: 3`, one `run_id`/`conversation_id` across traces); AC2 (second request carries the text observation, third exactly one image, store 0 objects after completion); AC3; AC4 (refused `screen.capture` then final → `success`, `turns: 2`); AC5; AC6 (with grants for `chat.read`, `users.read` on scope `chat` and `screen.capture` on scope `capture`, every request's tools are exactly the three; dropping the `capture` rule removes only `screen.capture`; a `screen.capture` rule on scope `chat` offers nothing); AC46 (`chat.write` proposal refused `not_a_read_action` with 0 executor calls, then final as `call-1`; `nope.action` → `unknown_action`); AC11; AC12 (no base64/path in bus events, traces, audit records; traces carry `attachment_id`, `size`, `width`, `height`); AC24; AC25 (6 terminal paths, store 0 objects and `SupervisedTasks.active` back to baseline); AC47 model side (0 or 1 image parts in the next request). `tests/test_budgets.py` (created here): AC13 loop part (2 requests then `budget: model_turns`), AC14, AC15, AC16 (held session + clock advance; held capture → `timeout` observation then `success`). `tests/test_observations.py`: expired/foreign lease through the whole brain path.

Test command (must pass): `python3 -m pytest tests/ -q -p no:cacheprovider`
Every acceptance criterion this step owns must be enforced by a test that fails without the
change. Never weaken, skip, xfail or delete an existing assertion to make the suite pass; if an
existing test encodes a superseded guarantee, replace it and cite the superseding requirement in
the test docstring. Do not use positive-duration sleeps in tests — inject the clock, the RNG and
the transports.

## Risks

Non-trivial (state machine, deadlines, cancellation). Canonical arguments for repetition use `core.contracts._canonical_text`-style rendering, not `json.dumps` with insertion order. `run.note_model_call()` before the await keeps the shutdown accounting of phase 0. The system prompt must not mention any action by name; the tools list is the only offer. The per-scope discovery is the only place the brain derives a destination from a spec — it must use the spec's `supported_destinations` scope, never a scope literal, so a new read action on a new scope is offered without a brain change.

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
