# Step P23 — Packaging and catalog

Plan step `P23` of the approved Phase 3 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase3/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase3/spec.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

- **`pyproject.toml`.** Verify that it holds exactly 17 `modules.<name>`
    package-data lines (6 added by P9, P10, P14, P16, P18 and P20) and that
    `dependencies` is exactly `aiohttp` and `PyYAML`.
  - **Final values of the allowlisted assertions.**
    - `test_manifests_are_unique_and_have_coherent_capabilities`: 17
      manifests, action names unique except `chat.write`, which is declared
      by exactly twitch, kick and youtube; the twitch keys as of P7.
    - `test_ac30_the_chat_only_profile_starts_and_runs_the_phase_1_scenario`:
      13 declared action names.
    - `test_installed_distribution_discovers_the_shipped_manifests_and_answers_help`:
      17.
    - `test_pyproject_ships_the_three_phase_2_manifests_and_no_new_dependency`:
      17 lines, with the dependency assertion unchanged.
  - **Scope.** Only those assertions change, and their docstrings name
    phase 3.

## Requirements

Plan mapping: R8
Acceptance criteria owned by this step: AC40
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

[pyproject.toml, tests/test_examples.py, tests/test_profiles.py]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P9, P10, P11, P14, P16, P19, P21]

All dependencies are already merged into `main` when this step runs.

## Tests

the four rewritten tests plus
  `tests/test_main.py::test_pyproject_ships_core_and_modules_with_every_manifest`
  (unchanged) pass. The clean-install test discovers 17 manifests.

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`, which the proxy and the
two-process topology tests import — a `python3 -m pytest` run fails at collection there.
Every acceptance criterion this step owns must be enforced by a test that fails without the
change. Never weaken, skip, xfail or delete an existing assertion to make the suite pass; if an
existing test encodes a superseded guarantee, replace it and cite the superseding requirement in
the test docstring. Do not use positive-duration sleeps in tests — inject the clock, the RNG and
the transports.

## Risks

a running value left by an earlier step could leave an
  assertion looser than the final one (for example "≥ 12"). Mitigation: the
  gate compares these four assertions against the spec's literal numbers.

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
