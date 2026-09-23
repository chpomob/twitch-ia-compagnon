# Step P17 — `watch` ticks through triggers and admission, the in-flight rule, the `brain.watch` principal end to end, and shutdown (R6; AC31-in-flight, AC32, AC33)

Plan step `P17` of the approved Phase 3 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase3/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase3/spec.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

- **The tick event.** A normalized schema-2 event: `kind: watch_tick`,
    text `prompt_text`, author `system:watch`, `message_id` from a
    monotonic per-channel counter, source `watch`.
  - **Evaluation and admission.** The tick is evaluated by the trigger
    engine with the module's policy. When accepted, it is admitted
    **directly** through the scheduler (`admit(session_key, work)`, as
    twitch does). It is never published as `channel.chat.message`, so it is
    never fed to the chat context or the users directory.
  - **In-flight rule.** The module tracks its admitted work per channel
    through the admission result/run-completion hook. A tick due while one
    is queued or running is skipped and counted. At most one watch run per
    channel is in flight.
  - **Shutdown.** `stop_inputs` cancels the tick task, publishes `inactive
    shutdown` and emits nothing after.

## Requirements

Plan mapping: R6
Acceptance criteria owned by this step: AC31, AC32, AC33
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

[modules/watch/__init__.py, tests/test_watch.py]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P6, P16]

All dependencies are already merged into `main` when this step runs.

## Tests

- AC31 in-flight: a held scripted model keeps the first watch run
    running; the next due tick gives skip + 1, and in-flight stays ≤ 1.
  - AC32 end to end with brain, the capture module's scripted source and a
    `watch_tick`:
    - 0 chat-context and users entries;
    - with a `brain`-only rule, 0 capture offers or calls;
    - with a `brain.watch` rule, 1 provider invocation on the same provider
      as a chat run.
  - AC33: shutdown during a session gives 0 ticks after `stop_inputs`, an
    `inactive shutdown` fact, and completion inside the global shutdown
    deadline on `ManualClock`.

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`, which the proxy and the
two-process topology tests import — a `python3 -m pytest` run fails at collection there.
Every acceptance criterion this step owns must be enforced by a test that fails without the
change. Never weaken, skip, xfail or delete an existing assertion to make the suite pass; if an
existing test encodes a superseded guarantee, replace it and cite the superseding requirement in
the test docstring. Do not use positive-duration sleeps in tests — inject the clock, the RNG and
the transports.

## Risks

- The run-completion signal the scheduler exposes must be verified
    (`AdmissionResult`/`RunRecord`). If only admission-time results exist,
    track completion through the `brain.run.completed` trace keyed by
    session. The step chooses and documents the channel it uses.
  - The coordinator drain tie: assert outcomes, not status 0.

## Constraints

- One atomic commit for the whole step when the suite is green; Conventional Commit message
  (`feat(phase3): ...`, `fix(phase3): ...`, `test(phase3): ...`, `refactor(phase3): ...`).
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
