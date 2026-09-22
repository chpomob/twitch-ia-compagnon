# Step P18 — Phase 2 end-to-end scenario on twitch and on the fake platform, local and across the proxy, with shutdown during audio and scene work (R1, R3, R4, R5, R10; AC35)

Plan step `P18` of the approved Phase 2 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase2/plan.md`
- Approved specification v1.2 (requirement and acceptance-criteria wording): `docs/campaigns/phase2/spec.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

A scripted model proposes `audio.capture {seconds: 3}` → the observation carries the `audio_ref` (with transcription from the scripted STT transport) → the model answers with a final text → delivery `[chat.write, audio.speak]`; run once with the twitch module (scripted `_session_factory` and chat transport) and once with the fixture platform, first with the three local providers on the brain's context, then with `audio_input`/`audio_output` behind `agent_link` on an agent context paired to a `proxy` on the brain context over `MemoryWebSocketPair`. Shutdown cases: the coordinator's shutdown started during the capture, during the playback and during a scene change (websocket-kind provider against the in-process peer — P15) ends within the global deadline on the injected clock, the modules' `drain()` hooks stopping their devices so each record comes back through the provider path (decision 1).

## Requirements

Plan mapping: R1, R3, R4, R5, R10
Acceptance criteria owned by this step: AC35
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

[`tests/test_phase2_scenario.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P7, P9, P11, P12, P13, P15, P16]

All dependencies are already merged into `main` when this step runs.

## Tests

AC35 — the record shows `turns == 2`, `action_calls == 3` (capture, chat.write, audio.speak), 1 chat send, 1 completed playback whose synthesis `input` equals the final text, the attachment released after the run (`lookup` is `None` on both stores in the proxy case), identical observation and delivery contracts on both transports (same keys, statuses and codes); a grep of `modules/brain/__init__.py` finds `audio`, `capture`, `proxy` only in the R4 adapter encoding, the probe and the `audio_omitted`/`capability_missing` sites (an allowlist of the decision 5 function names); shutdown during capture → the capture `cancelled` with 0 attachments; during playback → `cancelled` with its `played_ms`; during a scene change → resolved per AC23; each within the global shutdown deadline.

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`, which the proxy and the
two-process topology tests import — a `python3 -m pytest` run fails at collection there.
Every acceptance criterion this step owns must be enforced by a test that fails without the
change. Never weaken, skip, xfail or delete an existing assertion to make the suite pass; if an
existing test encodes a superseded guarantee, replace it and cite the superseding requirement in
the test docstring. Do not use positive-duration sleeps in tests — inject the clock, the RNG and
the transports.

## Risks

Non-trivial (integration; the widest test). The proxy case needs the agent store and the brain store on the same `ManualClock`; the shutdown cases must drive the clock past every module's stop grace, or a joined escalation task holds the coordinator past its deadline.

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
