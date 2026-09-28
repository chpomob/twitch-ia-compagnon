# Step P1 — `default` schema annotation in the validator (R3; AC14)

Plan step `P1` of the approved Phase 4 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase4/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase4/spec.md`
- Brief with the operator's binding decisions: `docs/campaigns/phase4/brief.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

Implements R3's validator half.
  - Add `"default"` to `_ANNOTATION_KEYWORDS` (beside `title`, `description`) so `validate_schema` accepts it.
  - After the node's own keywords are checked, validate `schema["default"]` against the node itself, using the module's existing value-validation function, the same one used to validate settings against a schema. A failure raises `SchemaError` labelled `<label>.default`.
  - The check recurses with the existing recursion, so a nested `properties`/`items` node's `default` is checked too.
  - `validate_value`-style checking of *values* must ignore `default` (no constraint effect).
  - Inside `properties`, a key literally named `default` is a property name and never an annotation. This works because keyword scanning happens only at node level, and the properties mapping is iterated as names. Confirm this with the audio_output `voices` schema, whose properties are `allowed` and `default`.

## Requirements

Plan mapping: R3
Acceptance criteria owned by this step: AC14
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

[`core/contracts.py`, `tests/test_manifest_presentation.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[]

All dependencies are already merged into `main` when this step runs.

## Tests

Create `tests/test_manifest_presentation.py` with the contract tests of AC14:
  - `{type: integer, minimum: 1, default: 5}` is accepted.
  - `{type: integer, minimum: 1, default: 0}` and `{type: string, default: 3}` raise with a label ending `.default`.
  - Validating the value `7` gives an identical result with and without `default: 5`.
  - A nested `properties.x.default` violation names `…properties.x.default`.
  - The audio_output manifest's `voices` schema still has exactly the properties `{allowed, default}` and passes `validate_schema`.
  - Run `tests/test_contracts.py` and `tests/test_loader.py` unchanged.

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`, which the proxy and the
two-process topology tests import — a `python3 -m pytest` run fails at collection there.
Every acceptance criterion this step owns must be enforced by a test that fails without the
change. Never weaken, skip, xfail or delete an existing assertion to make the suite pass; if an
existing test encodes a superseded guarantee, replace it and cite the superseding requirement in
the test docstring. Do not use positive-duration sleeps in tests — inject the clock, the RNG and
the transports. The suite must not drop below 2,460 passed / 18 skipped.

## Risks

- A `default` validated through the value validator could recurse into `default` again. Guard it so the annotation check validates the value against the node's constraints only.
  - A test in `tests/test_contracts.py` may pin the annotation set or the "supported" keyword list printed in the error message. Grep `supported:` / `_ANNOTATION_KEYWORDS` before the change, and adapt only by adding `default` to an expected listing. If an exact-message assertion breaks, that is an allowlist question: stop and flag it rather than weaken it.

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
