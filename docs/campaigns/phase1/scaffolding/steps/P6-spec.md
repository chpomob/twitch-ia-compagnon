# Step P6 — Package `core*` and `modules*` with manifests as package data, console script (R8; AC43)

Plan step `P6` of the approved Phase 1 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase1/plan.md`
- Approved specification v1.2 (requirement and acceptance-criteria wording): `docs/campaigns/phase1/spec.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

`[tool.setuptools.packages.find] include = ["core*", "modules*"]`; `[tool.setuptools.package-data] modules = ["*/module.yaml"]` (plus `"module.yaml"` per subpackage); `[project.scripts] twitch-ia-compagnon = "core.main:main"`; runtime dependencies unchanged (`aiohttp`, `PyYAML`); the `test` extra gains `setuptools>=69` (the declared build backend) so that the AC43 install test of P21 can build the distribution without build isolation and without network, and a missing backend is an environment error rather than a reason to skip. Every module directory must be a package: add an empty `__init__.py` to each shipped module directory that lacks one (`modules/twitch`, `modules/brain`, `modules/audit` already have `__init__.py`; the new modules of P14–P20 ship theirs).

## Requirements

Plan mapping: R8
Acceptance criteria owned by this step: AC43
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

[`pyproject.toml`, `tests/test_main.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P5]

All dependencies are already merged into `main` when this step runs.

## Tests

`tests/test_main.py`: `pyproject.toml` parsed with `tomllib` declares both package globs, the package-data glob, the console script pointing at `core.main:main` and `setuptools>=69` in the `test` extra; `main(["--help"])` raises `SystemExit(0)`. The clean-environment install test lives in `tests/test_profiles.py` (P21).

Test command (must pass): `python3 -m pytest tests/ -q -p no:cacheprovider`
Every acceptance criterion this step owns must be enforced by a test that fails without the
change. Never weaken, skip, xfail or delete an existing assertion to make the suite pass; if an
existing test encodes a superseded guarantee, replace it and cite the superseding requirement in
the test docstring. Do not use positive-duration sleeps in tests — inject the clock, the RNG and
the transports.

## Risks

Trivial-to-moderate. `setuptools` package-data globs are relative to the package: a wrong glob ships 0 manifests and the failure only shows in P21's install test — that test is the guard.

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
