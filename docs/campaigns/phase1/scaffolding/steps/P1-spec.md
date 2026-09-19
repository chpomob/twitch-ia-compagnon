# Step P1 — Typed observation parts and the shared proxy/adapter vocabulary in `core/contracts.py` (R4; AC21, AC47-shape)

Plan step `P1` of the approved Phase 1 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase1/plan.md`
- Approved specification v1.2 (requirement and acceptance-criteria wording): `docs/campaigns/phase1/spec.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

Add to `ActionObservation` a `parts: tuple[Mapping, ...] = ()` field validated in `__post_init__`: each part is a mapping with `type ∈ {"text", "image_ref"}`; a `text` part carries a string `text`; an `image_ref` part carries all 7 fields `attachment_id` (non-empty str), `content_type ∈ {image/png, image/jpeg}`, `size` (int > 0), `width`, `height` (int > 0), `captured_at` (finite number), `provider_id` (non-empty str); any other type or missing field raises `ContractError` naming `ActionObservation.parts[<i>].<field>`. Add module-level helpers `observation_size(parts) -> int` (sum of UTF-8 bytes of each `text` plus `size` of each `image_ref`; envelope, `result` and metadata excluded) and `validate_parts(parts, *, max_text_bytes)` used by P3 (a `text` part above `max_text_bytes` raises naming the part). Constructing without `parts` is unchanged. Add the shared vocabulary constants: brain synthetic codes (`not_a_read_action`, `unknown_action`, `malformed_arguments`, `observation_too_large`, `attachment_refused`, `attachment_expired`, `invalid_result` reuse), run failures (`unsupported_response_shape`, `capability_missing`, `budget_exhausted`), the probe tool name `PROBE_TOOL = "runtime.probe"` (a runtime constant, no model name — declared here so that the test harness of P7 and the brain of P10 import the same symbol from `core.contracts`), probe reasons (`non_success_status`, `no_tool_call`, `multiple_tool_calls`, `malformed_arguments`, `image_rejected`, `timed_out`, `transport_failed`), delivery resolution reasons (`unknown_action`, `not_a_delivery`, `text_mapping_missing`, `text_mapping_ambiguous`, `empty`), proxy protocol v1 (`PROXY_PROTOCOL_VERSION = 1`, frame types, error codes `auth_failed`, `agent_limit`, `unsupported_protocol_version`, `frame_too_large`, `unknown_frame`, `unknown_session`, `invalid_frame`, `duplicate_call_unknown`, `action_mismatch`, `proxy_disconnected`, `event_not_allowed`, attachment ack codes, close codes 4400/4401/4409/4413, default `max_frame_bytes` 1 048 576).

## Requirements

Plan mapping: R4
Acceptance criteria owned by this step: AC21, AC47
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

[`core/contracts.py`, `tests/test_contracts.py`, `tests/test_observations.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[]

All dependencies are already merged into `main` when this step runs.

## Tests

`tests/test_contracts.py`: AC21 — unknown part type, `text` part over the bound (through `validate_parts`), each of the 7 `image_ref` fields missing, each raising `ContractError` naming the field; an observation with no `parts` constructs as before; `parts` are frozen. `tests/test_observations.py` (created here, extended in P3/P12): `observation_size` counts text bytes + image `size` only (AC47 arithmetic: 3 145 728 + 3 145 728 > 5 242 880; 3 145 728 + 2 097 152 = 5 242 880 accepted); vocabulary constants have the spec's literal values, `PROBE_TOOL == "runtime.probe"`, and close codes are distinct.

Test command (must pass): `python3 -m pytest tests/ -q -p no:cacheprovider`
Every acceptance criterion this step owns must be enforced by a test that fails without the
change. Never weaken, skip, xfail or delete an existing assertion to make the suite pass; if an
existing test encodes a superseded guarantee, replace it and cite the superseding requirement in
the test docstring. Do not use positive-duration sleeps in tests — inject the clock, the RNG and
the transports.

## Risks

Non-trivial (type-system gap: frozen nested mappings). Freezing `parts` through `_frozen_mapping` must not reject numeric `captured_at` floats; `size` must reject `bool`. Adding a defaulted field to a `slots=True` frozen dataclass keeps positional construction order — `parts` goes last so every existing constructor keeps working.

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
