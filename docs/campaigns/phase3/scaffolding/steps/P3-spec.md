# Step P3 — Loader accepts `model_proposable` (R5; AC25 discovery half)

Plan step `P3` of the approved Phase 3 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase3/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase3/spec.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

- **Key.** Add `"model_proposable"` to `_ACTION_KEYS`. `_manifest_actions`
    passes it to `ActionSpec` (absent → `False`).
  - **Diagnostics.** A `ContractError` raised by the spec is already mapped
    through `_action_field`. The step checks that the diagnostic names the
    module directory **and** the action name, and adds the action name if it
    does not.
  - **Other keys.** Every other unknown key is still refused as today.

## Requirements

Plan mapping: R5
Acceptance criteria owned by this step: AC25
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

[core/loader.py, tests/test_contracts.py]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P1]

All dependencies are already merged into `main` when this step runs.

## Tests

(in `tests/test_contracts.py`, which the spec targets for these
  refusals, using a temporary modules root and `ModuleLoader` discovery)
  - A manifest with `model_proposable: true` on a `read` action fails
    discovery, and the message names the module and the action.
  - `model_proposable: true` on a delivery-capable write fails the same way.
  - `model_proposable: true` on a delivery-less write is discovered, and its
    spec has `model_proposable is True`.
  - An unknown key `foo: 1` is still refused.
  - Every shipped manifest still discovers with `model_proposable is False`.

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`, which the proxy and the
two-process topology tests import — a `python3 -m pytest` run fails at collection there.
Every acceptance criterion this step owns must be enforced by a test that fails without the
change. Never weaken, skip, xfail or delete an existing assertion to make the suite pass; if an
existing test encodes a superseded guarantee, replace it and cite the superseding requirement in
the test docstring. Do not use positive-duration sleeps in tests — inject the clock, the RNG and
the transports.

## Risks

`tests/test_loader.py` is not a target, so no loader test may
  need editing. Mitigation: the change is additive; run `tests/test_loader.py`
  unchanged.

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
