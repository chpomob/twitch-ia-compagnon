# Step P10 — Model adapter: tool-based requests, response-shape classification, prepare-time capability probe, image encoding at call time, redaction (R2; AC8, AC9, AC12)

Plan step `P10` of the approved Phase 1 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase1/plan.md`
- Approved specification v1.2 (requirement and acceptance-criteria wording): `docs/campaigns/phase1/spec.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

Introduce in `modules/brain/__init__.py` a `_ModelAdapter(session, settings, clock, attachments, reporter)` owning `_request_model`: it builds `{model, messages, tools[], tool_choice, max_tokens}` where `tools[]` are the offered read actions as `{type: function, function: {name, description, parameters: argument_schema}}`; transcript entries with an `image_ref` are resolved from the store at request time only and encoded as `{type: image_url, image_url: {url: "data:<content_type>;base64,..."}}` inside the message content, the encoded string living only in the request body (never in a trace, event, diagnostic or the transcript object). `classify(body) -> _Proposal(name, raw_arguments) | _Final(text) | _Unsupported(reason)` per decision 2 (exactly one tool call → proposal; text and no tool call → final; ≥ 2 tool calls, tool call + text, neither → unsupported). `probe(required)` at `prepare`, before `mark_ready`: request (i) a forced call on the probe tool `core.contracts.PROBE_TOOL` (`"runtime.probe"`, declared in P1; the brain imports it and declares no second copy) with `tool_choice` forcing it, expecting a 2xx body classified as exactly one tool call with a JSON-object argument; (ii) when `vision` is required, the same with one 1×1 PNG image part (constant bytes in the module). Reasons per R2: non-2xx → `non_success_status`; plain text → `no_tool_call`; ≥ 2 calls → `multiple_tool_calls`; non-object arguments → `malformed_arguments`; a 2xx body whose `error` names the image input, or an image probe answered without the forced tool call while the text probe passed → `image_rejected`; no answer within `budget.model_call_seconds` → `timed_out`; transport exception → `transport_failed`. A failed probe raises `BrainModuleError("module 'brain': backend capability '<name>' not verified: <reason>")` and `prepare` leaves the module not ready (no `mark_ready`, health `degraded` with that reason); the message never carries the key, the endpoint or a body. The verified set is stored on the module (`verified_capabilities`). `usage` reading and the estimate flag stay as today. `_request_model` returns `_ModelReply(classification, tokens, estimated, failure)`. `tests/test_brain.py` harness: `activate_with` answers the probe through P7's `FakeSession`; `Harness.requests()` excludes probe calls; every `len(harness.requests()) == 1` / `== []` assertion keeps its meaning.

## Requirements

Plan mapping: R2
Acceptance criteria owned by this step: AC8, AC9, AC12
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

[`modules/brain/__init__.py`, `tests/test_model_adapter.py`, `tests/test_brain.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P9]

All dependencies are already merged into `main` when this step runs.

## Tests

`tests/test_model_adapter.py`: AC8 (vision probe non-2xx → exact diagnostic, no key/endpoint substring, 0 scenario requests; both probes OK → ready, exactly 2 probe requests, the second with exactly one image part); AC9 (`no_tool_call`, `multiple_tool_calls`, exactly 1 probe with `[structured_output]`); `malformed_arguments`, `timed_out` (held session + clock advance), `transport_failed`; request shape (tools are the offered specs, `tool_choice: auto` for scenario turns, image parts encoded from the store only at request time and absent from the transcript object); classification of the 5 shapes; redaction (the api key never appears in diagnostics or traces on every failure path). `tests/test_brain.py`: existing request-shape tests adapted to `messages`+`tools` (allowlisted rewrites are done in P12).

Test command (must pass): `python3 -m pytest tests/ -q -p no:cacheprovider`
Every acceptance criterion this step owns must be enforced by a test that fails without the
change. Never weaken, skip, xfail or delete an existing assertion to make the suite pass; if an
existing test encodes a superseded guarantee, replace it and cite the superseding requirement in
the test docstring. Do not use positive-duration sleeps in tests — inject the clock, the RNG and
the transports.

## Risks

Non-trivial (network boundary; invariant: image bytes in exactly one place). A probe that is accepted by a backend as text-only with `tool_choice` ignored must be `no_tool_call`, not success — classification must not fall back. Cancelling `prepare` mid-probe must release the response (existing `_release_response`).

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
