# Step P6 — Brain

Plan step `P6` of the approved Phase 2 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase2/plan.md`
- Approved specification v1.2 (requirement and acceptance-criteria wording): `docs/campaigns/phase2/spec.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

Import `CAPABILITY_STRUCTURED_OUTPUT`, `CAPABILITY_VISION`, `CAPABILITY_AUDIO`, `PROBE_REASON_AUDIO_REJECTED` from `core.contracts` (the brain's own names kept as re-exports); `KNOWN_CAPABILITIES` gains `audio`; the settings validator's message lists the three names; `module.yaml` `capabilities.required` description enumerates `structured_output`, `vision`, `audio` (validator name unchanged). `_ModelAdapter.probe`: after `structured_output` and (when required) `vision`, when `audio` is required send one forced `PROBE_TOOL` call whose user content carries the text instruction plus one `{"type": "input_audio", "input_audio": {"data": <base64 of _PROBE_WAV>, "format": "wav"}}` part, `_PROBE_WAV` being the generated 0.25 s, 16 kHz, mono, 16-bit all-zero segment (8 044 bytes, built once by a private `_silent_wav()`); `_probe_reason(kind=...)` replaces the `image: bool` flag with `kind ∈ {text, image, audio}` so the rejection reason is `image_rejected` or `audio_rejected` by kind; `_names_audio_input(answer)` mirrors `_names_image_input`; the probe count is bounded by construction (one request per required capability, three names known → at most 3). Each failure keeps the phase 1 diagnostic form `module 'brain': backend capability 'audio' not verified: <reason>`, within `budget.model_call_seconds`, before the readiness barrier; `verified_capabilities` carries `audio` when verified.

## Requirements

Plan mapping: R4
Acceptance criteria owned by this step: AC15
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

[`modules/brain/__init__.py`, `modules/brain/module.yaml`, `tests/test_model_adapter.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P1, P5]

All dependencies are already merged into `main` when this step runs.

## Tests

`tests/test_model_adapter.py`: with `required: [structured_output, audio]` the fake session sees exactly 2 probe requests in order (text, audio), the audio one carrying one `input_audio` part whose decoded payload is 8 044 bytes, `RIFF`/`WAVE`, all-zero samples, `format == "wav"`, and a forced tool call; with `vision` too exactly 3; a 400 on the audio probe → `BrainModuleError` text `module 'brain': backend capability 'audio' not verified: audio_rejected`; a text-only answer → `no_tool_call`; no answer within `model_call_seconds` on the injected clock → `timed_out`; the diagnostic contains neither the endpoint, the key nor the response body; probe requests are absent from `post_calls` and from the per-run request count; a settings list naming `audio` passes the validator and an unknown name still fails naming `capabilities.required`.

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`, which the proxy and the
two-process topology tests import — a `python3 -m pytest` run fails at collection there.
Every acceptance criterion this step owns must be enforced by a test that fails without the
change. Never weaken, skip, xfail or delete an existing assertion to make the suite pass; if an
existing test encodes a superseded guarantee, replace it and cite the superseding requirement in
the test docstring. Do not use positive-duration sleeps in tests — inject the clock, the RNG and
the transports.

## Risks

Non-trivial (branching; the classification must consult `kind` before the status rule, as the image probe does, so a 400 is `audio_rejected` and not `non_success_status`). `tests/test_brain.py`/`test_integration.py` count 2 probes with vision and do not require `audio`, so they stay green.

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
