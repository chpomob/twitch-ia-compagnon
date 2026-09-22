# Step P7 — Brain

Plan step `P7` of the approved Phase 2 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase2/plan.md`
- Approved specification v1.2 (requirement and acceptance-criteria wording): `docs/campaigns/phase2/spec.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

`_PART_AUDIO_REF = "audio_ref"`; `_Transcript.image_parts` becomes `attachment_parts` (both reference types) used by `_expired_image` (renamed `_expired_attachment`, same rule: an expired lease at the model turn ends the run `attachment_expired`), by `_discard_images` (both types; releases through `store.discard`) and by the run-end release — every call site updated in this step (grep first; an existing test reference keeps a thin alias). `_TurnEntry` gains `audio_omitted: bool = False`; `messages()` renders an observation's `audio_ref` parts in the `user` message following the tool result: as the reference (for the adapter to encode) when not omitted, as a `text` part holding the transcription text when omitted. `_ModelAdapter._encode_part`: `audio_ref` → `{"type": "input_audio", "input_audio": {"data": base64(store bytes), "format": "wav"}}` resolved through `_resolve_attachment` (the renamed `_resolve_image`, same failures) at request time only, followed by a `text` part with the transcription text when present. In `BrainModule._propose`, beside the vision rule: an observation carrying an `audio_ref` when `audio ∉ verified_capabilities` → with a `transcription`, the entry is appended `audio_omitted=True` and `state.audio_omitted += 1`; without, `_discard_images(observation.parts)` and `_failed(state, error, capability_missing, capability="audio")` with 0 requests for that turn. `_RunState` records `audio_omitted` (default 0) in the run record and `brain.run.completed` carries it. `_part_summaries` summarises an `audio_ref` by `attachment_id`, `content_type`, `size`, `duration_ms` (no path, no bytes). `_estimate_message_tokens`: an audio part costs `ceil(duration_ms / 100)` tokens (10 per second, rounded up — read from the reference kept on the message before encoding) and sets the estimated flag; `_TOKEN_IMAGE_ESTIMATE` unchanged. No hunk in `_resolve_delivery`, `_deliver_all`, `_delivery_for` or `_loop`.

## Requirements

Plan mapping: R4
Acceptance criteria owned by this step: AC16, AC17, AC14
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

[`modules/brain/__init__.py`, `tests/test_model_adapter.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P6, P2]

All dependencies are already merged into `main` when this step runs.

## Tests

`tests/test_model_adapter.py`: in a run with `audio` verified, a scripted `audio.capture`-shaped provider (harness registry, `audio_ref` leased in the shared store, with transcription) makes the next request carry exactly one `input_audio` part (`format == "wav"`, decoded bytes equal the stored attachment) plus the transcription text, and no trace or event of the run contains the base64 payload or a filesystem path (substring over `trace_texts`); without `audio` verified and with a transcription: the request carries the transcription text and 0 audio parts, `brain.run.completed` has `audio_omitted: 1`; without transcription: the run ends `error`, `failure: capability_missing`, `capability: audio`, 0 requests for that turn and `store.lookup` of the attachment is `None`; a lease expired on the clock before the next model turn ends the run `error attachment_expired` (AC14 tail); a 3 000 ms part adds exactly 30 tokens with `tokens_estimated: true` when the backend reports no usage, 3 001 ms adds 31; the phase 1 image cases stay green.

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`, which the proxy and the
two-process topology tests import — a `python3 -m pytest` run fails at collection there.
Every acceptance criterion this step owns must be enforced by a test that fails without the
change. Never weaken, skip, xfail or delete an existing assertion to make the suite pass; if an
existing test encodes a superseded guarantee, replace it and cite the superseding requirement in
the test docstring. Do not use positive-duration sleeps in tests — inject the clock, the RNG and
the transports.

## Risks

Non-trivial (branching; invariant: attachments released exactly once). The rename is accompanied by the type extension (not a fake rename); the omission is counted once per observation, not once per re-encoding.

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
