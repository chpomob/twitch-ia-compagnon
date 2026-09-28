# Step P18 — Operator documentation (R10; AC37)

Plan step `P18` of the approved Phase 4 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase4/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase4/spec.md`
- Brief with the operator's binding decisions: `docs/campaigns/phase4/brief.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

Write `docs/config-ui.md` covering:
  - launching with `python -m core.config_ui --config config.yaml` (and the console script), the options, and a launch argv after `--`;
  - that the UI is **local-only** and does not administer a remote brain over the proxy; the token and the printed URL;
  - the overlay path rule (A1), with `config.yaml → config.local.yaml` and `presence.yaml.example → presence.local.yaml`, and `--overlay`;
  - the merge precedence (R2/AC7 example) and that the overlay cannot delete a base key (A7);
  - the 5 writable blocks (`enabled_modules`, `modules.<name>`, `triggers`, `limits`, `modules_directory`) and the 2 read-only blocks (`secrets`, `actions`), with the reason (6c: a mis-click must never widen authorized actions or expose a secret);
  - that the overlay is rewritten by the UI, so comments are not preserved;
  - the secrets policy (R8);
  - restart and supervision (A4, the stop grace, the 60 s window, the accepted/refused/unknown outcomes);
  - the drift states in sync / differs / unknown, and "status record unusable";
  - the status-file variable `TWITCH_IA_COMPAGNON_STATUS_FILE`, the default status path `<base file name>.status.json`, the collision refusal, and how to make an externally started main process visible (set the variable to the UI's status path; it is then shown "not supervised");
  - that a stray `*.local.yaml` next to a profile is merged by `--check-config` too.

## Requirements

Plan mapping: R10
Acceptance criteria owned by this step: AC37
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

[`docs/config-ui.md`, `tests/test_config_ui.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P16] (P16 and P18 both edit `tests/test_config_ui.py`; P18 runs after P16 so the two edits are ordered, and P16 transitively brings P15)

All dependencies are already merged into `main` when this step runs.

## Tests

An AC37 doc test in `tests/test_config_ui.py` asserts that the file contains:
  - `python -m core.config_ui` and `local-only`/`local only`;
  - both AC8 examples;
  - the AC7 merge example;
  - the "cannot delete" statement;
  - the 5 writable and 2 read-only block names with a reason;
  - the secrets-policy text;
  - `in sync`, `differs`, `unknown`;
  - `core.overlay.STATUS_FILE_VARIABLE`, read from the module (not a literal), and `.status.json`.

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`, which the proxy and the
two-process topology tests import — a `python3 -m pytest` run fails at collection there.
Every acceptance criterion this step owns must be enforced by a test that fails without the
change. Never weaken, skip, xfail or delete an existing assertion to make the suite pass; if an
existing test encodes a superseded guarantee, replace it and cite the superseding requirement in
the test docstring. Do not use positive-duration sleeps in tests — inject the clock, the RNG and
the transports. The suite must not drop below 2,460 passed / 18 skipped.

## Risks

Documentation drifts from the constants. Reading the variable name from `core.overlay` in the test pins it.

## Constraints

- One atomic commit for the whole step when the suite is green; Conventional Commit message
  (`feat(phase4): ...`, `fix(phase4): ...`, `test(phase4): ...`, `refactor(phase4): ...`, `docs(phase4): ...`).
  The scope MUST be `phase4`: the repository history is full of `feat(phase3):` / `fix(phase2):` commits
  from earlier phases — do NOT copy that habit. Every commit this campaign makes is `(phase4)`.
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
