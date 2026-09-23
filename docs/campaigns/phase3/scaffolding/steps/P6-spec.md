# Step P6 — Brain run principal `brain.watch`, model-proposable write offers and the generic run limit (R5, R6; AC25-brain, AC32-brain)

Plan step `P6` of the approved Phase 3 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase3/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase3/spec.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

- **Principal.** Replace the constant principal at every call site
    (`_offered_tools`, `_delivery_call`, the model-proposed call path and
    `authorized(principal=...)` for deliveries) with the run's principal from
    decision 4. `PRINCIPAL` stays as the default name `brain`.
  - **Offers.** `_offered_tools` keeps offering authorized reads. It also
    offers a write iff `spec.model_proposable` and the authorized view for
    (run principal, destination) grants it.
  - **Model write calls.** A model call to a write goes through the executor
    as a read call does. A second call of the same model-proposable action in
    one run returns `refused`/`run_limit` to the model with 0 executor
    invocations. A call to a write that is not proposable keeps the phase 2
    `not_a_read_action` error.
  - **Scope.** No module or action name is added.

## Requirements

Plan mapping: R5, R6
Acceptance criteria owned by this step: AC25, AC32
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

[modules/brain/__init__.py, tests/test_brain.py]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P5]

All dependencies are already merged into `main` when this step runs.

## Tests

- AC25 brain half, with a test-local proposable write registered on the
    executor: it is offered only with a granting rule, and a granted
    `chat.write` is offered 0 times over a run.
  - A second proposable call in one run is `refused run_limit`, and the
    provider sees 1 call.
  - AC32 brain half: a run triggered by a `watch_tick`-kind event uses
    principal `brain.watch`:
    - a rule granting `screen.capture` to `brain` only gives 0 capture offers
      and 0 calls in that run, while a chat run of the same channel is
      offered it;
    - a rule naming `brain.watch` gives 1 provider invocation.
  - Phase 2 authorization tests stay green: every chat run's principal is
    still `brain`.

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`, which the proxy and the
two-process topology tests import — a `python3 -m pytest` run fails at collection there.
Every acceptance criterion this step owns must be enforced by a test that fails without the
change. Never weaken, skip, xfail or delete an existing assertion to make the suite pass; if an
existing test encodes a superseded guarantee, replace it and cite the superseding requirement in
the test docstring. Do not use positive-duration sleeps in tests — inject the clock, the RNG and
the transports.

## Risks

a call site missed in the swap would silently authorize a watch
  run as `brain`. Mitigation: grep `PRINCIPAL` after the change. Only the
  default definition and the derivation function may reference it. The test
  asserts the principal on every recorded `ActionCall` of a watch run.

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
