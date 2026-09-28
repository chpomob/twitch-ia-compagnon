# Step P17 — Packaging

Plan step `P17` of the approved Phase 4 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase4/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase4/spec.md`
- Brief with the operator's binding decisions: `docs/campaigns/phase4/brief.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

- Add `twitch-ia-compagnon-config-ui = "core.config_ui:main"` under `[project.scripts]`. `[project].dependencies` stays exactly `aiohttp` and `PyYAML`.
  - Append to `.gitignore`: `*.local.yaml` and `*.status.json`.
  - Adapt the allowlisted `tests/test_main.py::test_pyproject_declares_the_console_script_and_the_build_backend_for_tests`: the scripts assertion becomes an exact equality with the two entries `{"twitch-ia-compagnon": "core.main:main", "twitch-ia-compagnon-config-ui": "core.config_ui:main"}`. This is still exact and not loosened; it is the change the spec allowlists. The build-backend and dependency assertions stay unchanged.
  - `core.config_ui:main` must accept `argv=None` and return an int. P9 defines it that way.

## Requirements

Plan mapping: R1, R9
Acceptance criteria owned by this step: AC36, AC11
Read the exact R/AC text in the specification file before writing code. Requirements not listed
here are other steps' responsibility — do not implement them.

Standing phase-4 decisions that override any contrary reading of a requirement:
- The configuration UI runs in a **separate process** (`python -m core.config_ui`); it is a
  **local-only** surface (loopback by default, an access token, a Host/Origin and CSRF guard),
  never a remote administration channel, and it adds **no runtime dependency** beyond aiohttp
  and PyYAML, with no CDN or external asset.
- Its writing scope (decision 6c) is exactly `enabled_modules`, `modules.<name>`, `triggers`,
  `limits` and `modules_directory`. The `secrets` block and the `actions` block (the default-deny
  authorization rules) are **read-only in v1 and displayed read-only**, so a single mis-click can
  never widen the set of authorized actions.
- Writes go to a **managed overlay file** merged over the untouched base file (comments preserved);
  a secret VALUE never appears in any page, response, log or diagnostic, only its reference and its
  set/unset state.
- Every module page is **generated from the module manifest** (schema plus presentation metadata);
  **no module ships HTML**, and adding a module must add its page with no UI code change.
- Applying a change is a **supervised restart** (there is no hot reload in this phase).

## Files

[`pyproject.toml`, `.gitignore`, `tests/test_main.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P9]

All dependencies are already merged into `main` when this step runs.

## Tests

- The adapted pyproject test.
  - A new assertion in `tests/test_config_ui.py` is not needed: AC36's dependency half is covered by the existing test.
  - If `tests/test_main.py` has an install test that builds the distribution (the AC43 of a prior phase), run it to confirm `core.config_ui` is packaged.

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`, which the proxy and the
two-process topology tests import — a `python3 -m pytest` run fails at collection there.
Every acceptance criterion this step owns must be enforced by a test that fails without the
change. Never weaken, skip, xfail or delete an existing assertion to make the suite pass; if an
existing test encodes a superseded guarantee, replace it and cite the superseding requirement in
the test docstring. Do not use positive-duration sleeps in tests — inject the clock, the RNG and
the transports. The suite must not drop below 2,460 passed / 18 skipped.

## Risks

An existing install or entry-point test may enumerate the console scripts; grep `console_scripts`/`entry_points` in the tests first.

## Constraints

- One atomic commit for the whole step when the suite is green; Conventional Commit message
  (`feat(phase4): ...`, `fix(phase4): ...`, `test(phase4): ...`, `refactor(phase4): ...`, `docs(phase4): ...`).
- Keep the phase-0 to phase-3 guarantees in force: bounded admission, phase lifecycle, explicit
  terminal action outcomes, default-deny authorization (reads included), bounded retention, a single
  global startup/shutdown cleanup deadline, redaction of configured secrets in traces and loss
  diagnostics, and the phase-3 moderation/memory/watch semantics.
- No module name may be added to `core/main.py` — a new module starts and stops through its manifest.
- Platform neutrality: contracts and brain must not depend on a specific platform; a second fake
  platform exercises the same contracts.
- A secret VALUE must never reach a response, a log line or a diagnostic — only its reference and its
  set/unset state.
- No new runtime dependency; no model, provider or vendor name in code, config or commit messages.
- Report at the end: what changed, the exact test command output count, and anything you could
  not do because the plan did not cover it.
