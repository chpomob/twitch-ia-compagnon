# Step P5 — `modules_directory: builtin` and `--check-config` in `core/main.py` (R7, R8; AC40, AC43 groundwork)

Plan step `P5` of the approved Phase 1 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase1/plan.md`
- Approved specification v1.2 (requirement and acceptance-criteria wording): `docs/campaigns/phase1/spec.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

In `load_config`, the reserved literal `builtin` resolves to `Path(importlib.util.find_spec("modules").origin).parent` (a `ConfigurationError` naming `modules_directory` when the package is not importable or has no directory); any other value keeps today's relative resolution. Add `check_config(config_path, *, environ, diagnostic_reporter) -> int`: loads the config, assembles the runtime (no socket is opened by assembly), builds a `ModuleLoader`, discovers and validates every manifest, validates the enabled set, resolves environment references, redacts credentials and runs each enabled module's declared settings validator with the accepted `limits` block — reusing the loader's existing pre-activation path (`_discover`, `_validate_config`, `_validate_settings`) without calling any entry point's `activate` — then returns 0, or 2 with one diagnostic per failure naming module and field, never a value. `main()` gains `--check-config` (flag, mutually compatible with `--config`) and dispatches to `check_config`. No module name is added to `core/main.py`. Depends on P3 because both steps edit `core/main.py::_assemble_runtime`'s neighbourhood (P3 wires the store into the executor there); serialising them keeps one edit history for the file.

## Requirements

Plan mapping: R7, R8
Acceptance criteria owned by this step: AC40, AC43
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

[`core/main.py`, `tests/test_main.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P3]

All dependencies are already merged into `main` when this step runs.

## Tests

`tests/test_main.py`: `builtin` resolves to the checkout's `modules/` directory (same `discover` result as `./modules`); an unresolvable package is a `ConfigurationError` naming the field; `--check-config` on the current example with a dummy environment returns 0 and activates 0 modules (a spy entry point records 0 `activate` calls); with `TWITCH_ACCESS_TOKEN` unset it returns 2 and the diagnostic names `modules.twitch.access_token` and no value; `main(["--config", path, "--check-config"])` returns the same status without installing the watchdog's process exit; `--help` exits 0.

Test command (must pass): `python3 -m pytest tests/ -q -p no:cacheprovider`
Every acceptance criterion this step owns must be enforced by a test that fails without the
change. Never weaken, skip, xfail or delete an existing assertion to make the suite pass; if an
existing test encodes a superseded guarantee, replace it and cite the superseding requirement in
the test docstring. Do not use positive-duration sleeps in tests — inject the clock, the RNG and
the transports.

## Risks

Non-trivial (module boundary: `importlib`). The loader's validation path must be callable without a `context`; if `_validate_settings` needs `self.context`, `check_config` builds the same runtime `_assemble_runtime` builds (no transport is opened by construction — verified by the socket-count test in P21).

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
