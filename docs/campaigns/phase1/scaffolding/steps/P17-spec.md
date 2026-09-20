# Step P17 — Local vertical integration on twitch and on the fake platform (R1, R5; AC1, AC2, AC25, AC31, AC41-local)

Plan step `P17` of the approved Phase 1 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase1/plan.md`
- Approved specification v1.2 (requirement and acceptance-criteria wording): `docs/campaigns/phase1/spec.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

Test-only step: the AC1 scenario with the real `chat_context`, `users`, `capture` (file source through `FakeCaptureSource`) and brain modules activated through the loader on a temporary `modules_directory` holding copies of the shipped modules plus `fakeplatform`, grants for the four actions, a scripted model `[chat.read, screen.capture, final]`; then the same on platform `fake` (fake event, fake `chat.write`), asserting identical call sequences and trace type sets. The `_activate_example` helper of `tests/test_examples.py` gains seams for the new modules (injected capture source, probe-aware session) with its assertions unchanged.

## Requirements

Plan mapping: R1, R5
Acceptance criteria owned by this step: AC1, AC2, AC25, AC31, AC41
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

[`tests/test_agentic_loop.py`, `tests/test_observations.py`, `tests/test_examples.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P13, P14, P15, P16]

All dependencies are already merged into `main` when this step runs.

## Tests

AC1/AC2 with real providers; AC31 (same call sequence and trace set on `fake`; `grep -r twitch` over `core/ modules/brain modules/chat_context modules/users modules/capture` is 0 lines — proxy/agent_link directories are added to the grep in P20); AC25 with the real capture provider on every terminal path; AC41 local half (only `capture` enabled → `screen.capture` offered; 0 references to `capture` or `proxy` in `modules/brain/`).

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`, which the proxy and the
two-process topology tests import — a `python3 -m pytest` run fails at collection there.
Every acceptance criterion this step owns must be enforced by a test that fails without the
change. Never weaken, skip, xfail or delete an existing assertion to make the suite pass; if an
existing test encodes a superseded guarantee, replace it and cite the superseding requirement in
the test docstring. Do not use positive-duration sleeps in tests — inject the clock, the RNG and
the transports.

## Risks

Moderate. Copying modules into a temp directory per test is slow; use a session-scoped fixture. The trace-set comparison must exclude platform-specific payload values and compare types, statuses and call ids only.

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
