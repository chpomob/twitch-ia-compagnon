# Step P5 — One credential-redaction primitive for every settings/configuration diagnostic, enforced structurally (R3; F4; AC10, AC11)

Plan step `P5` of the approved Phase 6 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase6/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase6/spec.md`
- Brief with the operator's binding decisions: `docs/campaigns/phase6/brief.md`
- The full adversarial review whose findings this phase closes: `docs/reviews/2026-10-16-full-adversarial-review.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

Make `core.contracts.mask_known` (`core/contracts.py:2235`) the single primitive
  and route **field paths and key labels** through it, not only values/reasons: the loader's
  `_field_diagnostic`, `_module_diagnostic`, `_quoted`/`_bounded`/`_redact_known`
  (`core/loader.py:2242–2443`), the schema validator's error label/path construction in
  `core/contracts.py` (`_apply_object_schema` ~line 938 and the `SchemaError` label paths), the
  `--check-config` reporter in `core/main.py` (~line 930/1608), the config-UI Check/Save responses
  (`core/config_ui/__init__.py:1488`), supervision diagnostics (`core/runtime.py:363`) and any module
  settings validator that formats a settings key (Streamer.bot's settings validator for AC10). Local
  wrappers (`_redact_values`, the config UI's wrapper, `contracts._redact`) remain only as one-call
  delegations to `mask_known`. Add `tests/test_redaction_surfaces.py`: an AST walk over `core/` and
  `modules/` that enumerates every function constructing a configuration/settings diagnostic
  (raising `ContractError`/`SchemaError`/`ConfigError`-family exceptions or building diagnostic
  strings with a field path), asserts each passes its path/reason through `mask_known` or a
  registered delegating wrapper, and includes a self-test: a fixture source file formatting a field
  path without the primitive makes the structural check fail.

## Requirements

Plan mapping: R3
Acceptance criteria owned by this step: AC10, AC11
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

[`core/contracts.py`, `core/loader.py`, `core/main.py`, `core/config_ui/__init__.py`, `core/runtime.py`, `modules/streamerbot/__init__.py`, `tests/test_redaction_surfaces.py`, `tests/test_loader.py`, `tests/test_main.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[]

All dependencies are already merged into `main` when this step runs.

## Tests

AC10 — profile with `review-secret-4521` as a credential value also used as a key under
  `modules.streamerbot.settings`: `--check-config` exits non-zero and stdout+stderr do not contain the
  string; same for the config-UI Check response and for supervision records (tests in
  `tests/test_main.py` and `tests/test_loader.py`, config-UI case via the existing Check harness).
  AC11 — `tests/test_redaction_surfaces.py` enumeration plus its self-test. Run the AC10 tests
  against `8010edc` (fail); the AC11 self-test is its own proof of failure.

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

Non-trivial (cross-module invariant the type system cannot check). The AST
  enumeration can false-positive or miss surfaces built via helper indirection — document the
  heuristics and the allowlisted delegating wrappers in the test. Redacting key paths can make two
  diagnostics collide or become unreadable; keep the path skeleton and mask only the matching
  segment. Existing diagnostic-text assertions in `test_loader.py`/`test_main.py`/`test_config_ui.py`
  should stay equal when no secret is present; if one changes it is not allowlisted — stop and flag.

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
