# Step P1 — `audio_ref` part, attachment content-type set, `audio` capability and probe reason in `core/contracts.py` (R4, R5; AC13, AC15-vocabulary)

Plan step `P1` of the approved Phase 2 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase2/plan.md`
- Approved specification v1.2 (requirement and acceptance-criteria wording): `docs/campaigns/phase2/spec.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

Add `PART_TYPE_AUDIO_REF = "audio_ref"` to `PART_TYPES`; `AUDIO_CONTENT_TYPES = frozenset({"audio/wav"})`; `ATTACHMENT_CONTENT_TYPES = IMAGE_CONTENT_TYPES | AUDIO_CONTENT_TYPES` (the set store, proxy and agent share, R5); `AUDIO_REF_FIELDS = (attachment_id, content_type, size, duration_ms, sample_rate_hz, channels, captured_at, provider_id)`; `AUDIO_TRANSCRIPTION_FIELDS = (text, transcribed_at, provider_id, truncated)`. Add `_validate_audio_ref_part(part, label)` called from `validate_parts`: the eight fields required, no other key except the optional `transcription`; `content_type ∈ AUDIO_CONTENT_TYPES`; `size`, `duration_ms`, `sample_rate_hz`, `channels` strictly positive non-bool ints; `captured_at` finite; `attachment_id`/`provider_id` non-empty text; `transcription`, when present, a mapping with required `text` (str) and `transcribed_at` (finite) and typed optional `provider_id`/`truncated` — every failure a `ContractError` naming `<label>.<field>` (nested `<label>.transcription.text`). `observation_size` adds each `audio_ref` `size`. Add `CAPABILITY_AUDIO = "audio"` beside a new `CAPABILITY_STRUCTURED_OUTPUT`/`CAPABILITY_VISION` pair (values as the brain defines them today; the brain imports them in P6), `PROBE_REASON_AUDIO_REJECTED = "audio_rejected"` in `PROBE_REASONS`, and the `__all__` entries. Nothing else changes; no audio bytes, path or vendor name.

## Requirements

Plan mapping: R4, R5
Acceptance criteria owned by this step: AC13, AC15
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

[`core/contracts.py`, `tests/test_observations.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[]

All dependencies are already merged into `main` when this step runs.

## Tests

`tests/test_observations.py`: AC13 — an `audio_ref` with the eight fields is accepted (with and without `transcription`); each field missing, `content_type: "audio/mpeg"`, `duration_ms: 0`/`-1`/`True`, an unknown key, a `transcription` lacking `text` or `transcribed_at`, a non-mapping `transcription` — each raises naming the field; `observation_size([text 100 B, audio_ref size 96 044]) == 96 144`; `image_ref` cases unchanged; `ATTACHMENT_CONTENT_TYPES == {image/png, image/jpeg, audio/wav}`; `PROBE_REASON_AUDIO_REJECTED in PROBE_REASONS`; `CAPABILITY_AUDIO == "audio"`.

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`, which the proxy and the
two-process topology tests import — a `python3 -m pytest` run fails at collection there.
Every acceptance criterion this step owns must be enforced by a test that fails without the
change. Never weaken, skip, xfail or delete an existing assertion to make the suite pass; if an
existing test encodes a superseded guarantee, replace it and cite the superseding requirement in
the test docstring. Do not use positive-duration sleeps in tests — inject the clock, the RNG and
the transports.

## Risks

Non-trivial (type-system gap: frozen nested mappings). `_freeze` must freeze the nested `transcription` mapping too; `bool` is an `int` and must be rejected for every count field; `tests/test_contracts.py` assertions on `PART_TYPES` membership are read before editing so the added member breaks no phase 1 case.

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
