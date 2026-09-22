# Step P17 — Delivery-list proof with the real `audio_output` and `stream_control` modules (R2; AC6, AC7)

Plan step `P17` of the approved Phase 2 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase2/plan.md`
- Approved specification v1.2 (requirement and acceptance-criteria wording): `docs/campaigns/phase2/spec.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

Add tests that activate the real `modules/audio_output` (scripted speech transport + recording runner) and `modules/stream_control` (scripted scene provider) with the fixture platform and the brain on one harness context, and prove the phase 1 resolution rules carry the new declarations without any brain change: `fixed` lists `[{chat.write, text}, {audio.speak, text}]`, `[{chat.write, text}, {audio.play, none, arguments: {sound: chime}}]`, `[{stream.scene.set, none, arguments: {scene: Talking}}]`, and `mode: modules` with `preference: [chat.write, audio.speak]`. The step writes no production code; a failing delivery test is fixed in the module's declaration (P8/P14) with its own test.

## Requirements

Plan mapping: R2
Acceptance criteria owned by this step: AC6, AC7
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

[`tests/test_delivery.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P9, P14, P7]

All dependencies are already merged into `main` when this step runs.

## Tests

AC6 — the fixed `[chat.write, audio.speak]` list delivers one final answer with `deliveries == [{chat.write, text: true, success}, {audio.speak, text: true, success}]`, the synthesis request `input` equal to the final text, exactly 1 `chat.write` send; `[chat.write, audio.play(sound: chime)]` yields `audio.play` `text: false` with the clip played (1 runner start, 0 synthesis requests); `[stream.scene.set(scene: Talking)]` yields 1 set command and `delivery == success`. AC7 — `mode: modules` resolves `[chat.write, audio.speak, audio.play, stream.scene.set]` (preferred first, then catalog order) and traces `brain.delivery.resolved`; with `audio_output` not ready (failed probe) the `audio.speak` entry records `refused` code `provider_not_ready`, the other entries keep their outcomes and conversation memory is not written; an `external_unknown` on `audio.speak` forwarded through a dropped harness proxy is never memorised as a confirmed send. The AC7 diff clause (`git diff <base>..HEAD -- modules/brain/` holds R4 hunks only) is checked by the P21 gate.

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`, which the proxy and the
two-process topology tests import — a `python3 -m pytest` run fails at collection there.
Every acceptance criterion this step owns must be enforced by a test that fails without the
change. Never weaken, skip, xfail or delete an existing assertion to make the suite pass; if an
existing test encodes a superseded guarantee, replace it and cite the superseding requirement in
the test docstring. Do not use positive-duration sleeps in tests — inject the clock, the RNG and
the transports.

## Risks

Trivial in code, non-trivial in what it proves (integration across modules the resolver never names). The `modules` mode order depends on the catalog order the loader yields — the test fixes the `enabled_modules` order explicitly.

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
