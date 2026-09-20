# Step P7 — Shared test doubles: probe-aware model transport, tool-call script helpers, injectable capture source, in-memory WebSocket pair (R2, R5, R6 support)

Plan step `P7` of the approved Phase 1 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase1/plan.md`
- Approved specification v1.2 (requirement and acceptance-criteria wording): `docs/campaigns/phase1/spec.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

`FakeSession` learns to recognise a probe request (body carries `tool_choice` forcing the probe tool named by `core.contracts.PROBE_TOOL`, declared in P1 — the harness imports nothing from `modules.brain`, so it needs no brain code that P10 introduces later) and answers it from a `probe_results` list (default: one valid tool call, and one for the image probe when the request carries an image part) without consuming `results`; probe posts are recorded in `probe_calls`, scenario posts stay in `post_calls`, so per-run request counts keep their meaning. Add `tool_call(name, arguments, *, usage=None)`, `tool_calls([...])`, `text_and_tool_call(...)`, `final(text, usage=None)` builders producing Chat Completions bodies; `completion()` stays as the final-response alias. Add `ScriptedModel(*bodies)` = `FakeSession` with a `requests()` accessor returning scenario bodies only. Add `FakeCaptureSource(bytes, content_type, width, height)` with `fail_with`, `hold` (a future the test resolves) and `calls`; `png_bytes(width, height)` builds a minimal valid PNG. Add `MemoryWebSocketPair()` exposing two ends with `send_str`, `send_bytes`, `receive() -> msg(type, data)`, `close(code)`, `closed`, `close_code`, ordered queues and a `drop()` that closes both ends abruptly; the same minimal surface `aiohttp`'s `WebSocketResponse`/`ClientWebSocketResponse` expose, so P19/P20 code runs unchanged under it. `runtime_context` keeps its P3 `attachments` parameter and gains `rng` (default `random.Random(0)`).

## Requirements

Plan mapping: R2, R5, R6
Acceptance criteria owned by this step: (see plan)
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

[`tests/conftest.py`, `tests/test_brain.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P3]

All dependencies are already merged into `main` when this step runs.

## Tests

`tests/test_brain.py`: 3 added assertions on the helpers — `tool_call(...)` yields exactly one `tool_calls` entry with JSON-encoded arguments; a `FakeSession` with no probe traffic has `probe_calls == []` and `post_calls` unchanged; `MemoryWebSocketPair` delivers frames in order and propagates `close_code` to the peer. Every existing suite stays green (regression run).

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`, which the proxy and the
two-process topology tests import — a `python3 -m pytest` run fails at collection there.
Every acceptance criterion this step owns must be enforced by a test that fails without the
change. Never weaken, skip, xfail or delete an existing assertion to make the suite pass; if an
existing test encodes a superseded guarantee, replace it and cite the superseding requirement in
the test docstring. Do not use positive-duration sleeps in tests — inject the clock, the RNG and
the transports.

## Risks

Non-trivial (this file is a single point of failure for every suite — its docstring says so). Probe detection must key on the request body, not on call order, so a suite that never prepares the brain (shared-scheduler harness) is unaffected.

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
