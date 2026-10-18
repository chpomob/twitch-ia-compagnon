# Step P6 — Unknown settings refused at top level and in every enabled module's settings (R3; F26; AC12)

Plan step `P6` of the approved Phase 6 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase6/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase6/spec.md`
- Brief with the operator's binding decisions: `docs/campaigns/phase6/brief.md`
- The full adversarial review whose findings this phase closes: `docs/reviews/2026-10-16-full-adversarial-review.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

Refuse any profile key unknown to the top-level schema (in `core/main.py`'s
  `load_config`/`check_config`) and to each enabled module's declared `settings_schema` (in the
  loader's `_manifest_settings_validator`, `core/loader.py:1853`), by treating an object schema
  without explicit `additionalProperties` as closed for profile validation (or by applying
  `additionalProperties: false` at the loader boundary), recursively in nested settings objects.
  Each refusal names the key's path, produced through `mask_known` (P5). Startup and
  `--check-config` share the path. Every shipped `*.example` profile (`config.yaml.example`,
  `config.server.yaml.example`, `agent.yaml.example`, `presence.yaml.example` and module-level
  examples) must still validate — if one carries an undeclared key, declare it in the owning
  manifest schema rather than editing the profile (constraint: no profile change).

## Requirements

Plan mapping: R3
Acceptance criteria owned by this step: AC12
Read the exact R/AC text in the specification file before writing code. Requirements not listed
here are other steps' responsibility — do not implement them.

Standing phase-6 decisions that override any contrary reading of a requirement:
- **Every phase-5 decision stays binding.** The bridge is optional in every case; its events
  replace the basic inputs and never run beside them; an execution the instance cannot confirm is
  never reported as a success; authorization is default-deny; an input pair has exactly one owner.
- **The fix is the finding, nothing more.** Close exactly the findings this step names. If a
  sibling path shows the same defect, report it in your final message instead of widening the
  step: the plan has a step for it, and a fix that edits another step's files is refused.
- **No test is weakened.** Nothing is skipped, xfailed, deleted or loosened. A fix ships with the
  regression that fails without it: for a product defect the test fails on `8010edc`; for a defect
  in a test (F27, F28, F32) the new assertion is proven able to fail by a stated mutation of the
  code under test. Only a test named in the specification's failure allowlist may have its
  assertions replaced, and each replacement carries a docstring citing its finding and requirement.
- **Nothing changes for a shipped profile beyond the finding.** No new required setting, no new
  dependency, no changed default path -- except where the finding IS that default (the installation
  guide's endpoint value, the README command, the overlay file name).
- **The bridge stays "not delivered".** `tests/test_phase5_docs_status.py` passes unchanged.
- The full suite is green at every step: `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
  (never the system python, which lacks aiohttp).

## Files

[`core/contracts.py`, `core/loader.py`, `core/main.py`, `tests/test_loader.py`, `tests/test_main.py`, `tests/test_examples.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P5] (paths routed through the primitive); [P3, P4] (their new settings are declared before closure)

All dependencies are already merged into `main` when this step runs.

## Tests

AC12 — misspelled `brodcaster_id` under `modules.twitch.settings`, and separately an
  unknown top-level key: `--check-config` exits non-zero naming the path; parametrized over every
  shipped `*.example` profile: exit 0. A credential used as an unknown key is redacted (joins AC10).
  Run against `8010edc` (fails).

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`, which the proxy and the
two-process topology tests import — a `python3 -m pytest` run fails at collection there.
Every acceptance criterion this step owns must be enforced by a test that fails without the
change. Never weaken, skip, xfail or delete an existing assertion to make the suite pass; if an
existing test encodes a superseded guarantee, replace it and cite the superseding requirement in
the test docstring. Do not use positive-duration sleeps in tests — inject the clock, the RNG and
the transports. The suite must not drop below 3,124 passed / 18 skipped, the count at the start of
this phase; the seven allowlisted tests are adapted as the specification lists them, never weakened.

## Risks

Non-trivial (public config contract change). Test fixture profiles across the suite
  (`tests/fixtures/`, `test_integration.py`, `test_main.py`, `test_proxy_process`-style tests — see
  memory on profile ripples) may carry stray keys; fix the fixtures, never the assertions, and list
  every touched fixture in the commit. Manifests that rely on open objects for free-form maps
  (e.g. destination or rule maps) need explicit `additionalProperties: true` or
  `patternProperties` handling — check each module's schema before closing.

## Constraints

- One atomic commit for the whole step when the suite is green; Conventional Commit message
  (`feat(phase6): ...`, `fix(phase6): ...`, `test(phase6): ...`, `refactor(phase6): ...`, `docs(phase6): ...`).
  The scope MUST be `phase6`: the repository history is full of `feat(phase4):` / `fix(phase3):` commits
  from earlier phases — do NOT copy that habit. Every commit this campaign makes is `(phase6)`.
- Keep the phase-0 to phase-4 guarantees in force: bounded admission, phase lifecycle, explicit
  terminal action outcomes, default-deny authorization (reads included), bounded retention, a single
  global startup/shutdown cleanup deadline, redaction of configured secrets in traces and loss
  diagnostics, the phase-3 moderation/memory/watch semantics, and the phase-4 configuration UI's
  local-only, read-only-for-secrets-and-actions surface.
- No module name may be added to `core/main.py` — a new module starts and stops through its manifest.
- Platform neutrality: contracts and brain must not depend on a specific platform; a second fake
  platform exercises the same contracts.
- A secret VALUE must never reach a response, a log line or a diagnostic — only its reference and its
  set/unset state.
- No new runtime dependency (aiohttp and PyYAML only); no model, provider or vendor name in code,
  config or commit messages.
- Report at the end: what changed, the exact test command output count, and anything you could
  not do because the plan did not cover it.
