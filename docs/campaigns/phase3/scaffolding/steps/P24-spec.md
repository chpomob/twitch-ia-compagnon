# Step P24 — Opt-in real trials `tests/test_phase3_trials.py` (R8; AC41 trial source)

Plan step `P24` of the approved Phase 3 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase3/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase3/spec.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

Six trials, each skipped with an explicit reason unless
its environment variables are set:
  - **The six trials:**
    - `clips de plateforme`: Twitch clip on a live channel;
    - `modération de plateforme`: a Twitch delete in `act` on a test
      message;
    - `notifications communautaires`: a Twitch follow/raid notice observed;
    - `Kick`: a signed webhook received plus a chat send;
    - `YouTube`: token refresh, one poll and one send under the quota;
    - `veille d'écran`: watch ticks driving a real `screen.capture`.
  - **Output.** Each prints one line
    `PHASE3-TRIAL <name> commit=<sha> outcome=<...> date=<YYYY-MM-DD>`
    with no secret. Credential values are read from the environment and
    never printed.
  - **Isolation.** No trial runs by default, and none uses a positive
    `asyncio.sleep`. Real waiting uses the platform's own responses bounded
    by `asyncio.wait_for` on real transports, which is allowed only here and
    inside the opt-in gate.

## Requirements

Plan mapping: R8
Acceptance criteria owned by this step: AC41
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

[tests/test_phase3_trials.py]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P19, P21, P22]

All dependencies are already merged into `main` when this step runs.

## Tests

- Without the variables, the file collects 6 tests, all skipped, each
    reason naming its variables.
  - The sleep hygiene check (`test_ac45_the_suite_makes_no_positive_duration_sleep_call`)
    still passes over the new file.

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`, which the proxy and the
two-process topology tests import — a `python3 -m pytest` run fails at collection there.
Every acceptance criterion this step owns must be enforced by a test that fails without the
change. Never weaken, skip, xfail or delete an existing assertion to make the suite pass; if an
existing test encodes a superseded guarantee, replace it and cite the superseding requirement in
the test docstring. Do not use positive-duration sleeps in tests — inject the clock, the RNG and
the transports.

## Risks

- The hygiene sleep scan covers `tests/`. Mitigation: bound waits with
    injected-clock loops or `wait_for` timeouts, never `sleep(n > 0)`.
  - A real run needs live channels. When unavailable, the README row says
    "Non exécuté" with the reason.

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
