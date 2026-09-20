# Step P9 — Brain settings surface: schema, validator, `_Settings`, fixtures carry the new keys (R1, R2, R3; AC10, AC20, AC42-budgets, AC53-validation)

Plan step `P9` of the approved Phase 1 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase1/plan.md`
- Approved specification v1.2 (requirement and acceptance-criteria wording): `docs/campaigns/phase1/spec.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

`module.yaml::settings_schema` gains `budget.max_action_calls`, `budget.max_repeated_actions`, `budget.delivery_reserve_seconds` (all required), the `fallback` group (`enabled: bool`, `text: string`, both required), `capabilities.required` (array of strings, required) and the `delivery` group (`mode ∈ {fixed, modules}`, `actions[]{action, text_argument?, arguments?}`, `preference[]`, `overrides` mapping of the same shape keyed by `<platform>/<channel_id>`); `settings_validator` stays `validate_settings`. `validate_settings` checks: the 10 budget/admission values present, finite, positive (AC20, one diagnostic per field naming it), `admission.wait_seconds ≤ admission.total_run_seconds`, `fallback.text` non-empty, `capabilities.required` contains `structured_output` and only known names (`structured_output`, `vision`) naming `capabilities.required` (AC10), `delivery.mode` known, each fixed entry a mapping with a non-empty `action` string, `text_argument` a string or `none`, `arguments` a mapping, `preference` a list of strings, each override key `platform/channel_id` and its value of the same shape — each with one diagnostic naming the field (`delivery.mode`, `delivery.actions[<i>].action`, `delivery.overrides["k"].actions[<i>]`, AC53 validation part). `_Settings.from_mapping` gains `_Budget` fields, `_Fallback`, `capabilities: frozenset[str]`, `_DeliveryConfig(default, overrides)`. `config.yaml.example` brain block carries the acted defaults `5, 30, 10, 8192, 5242880, 6, 2, 10, 30, 60` (this changes `admission.total_run_seconds` from 120 to 60 in both the brain block and `limits.admission`), `fallback`, `capabilities.required: [structured_output, vision]`, `delivery: {mode: fixed, actions: [{action: chat.write, text_argument: text}]}`. Every test constant carrying brain settings (`VALID_SETTINGS`, `SETTINGS`, the `brain:` blocks of the integration/shutdown/retention/lifecycle/main suites) gains the same keys with the same values; no assertion changes. Depends on P6 (as well as P7) because P5 and P6 both edit `tests/test_main.py` before this step touches its `brain:` block.

## Requirements

Plan mapping: R1, R2, R3
Acceptance criteria owned by this step: AC10, AC20, AC42, AC53
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

[`modules/brain/module.yaml`, `modules/brain/__init__.py`, `config.yaml.example`, `tests/test_brain.py`, `tests/test_integration.py`, `tests/test_shutdown.py`, `tests/test_retention.py`, `tests/test_lifecycle.py`, `tests/test_main.py`, `tests/test_examples.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P6, P7]

All dependencies are already merged into `main` when this step runs.

## Tests

`tests/test_brain.py`: AC20 parametrised over the 10 fields × {absent, 0, negative, inf} and the wait > total case; AC10 both refusals; `delivery.mode: teleport` and a fixed entry lacking `action` each give one diagnostic naming the field; `test_manifest_declares_v2_shape_settings_hook_and_no_grant` compares the schema to the extended constant. `tests/test_examples.py::test_example_config_satisfies_every_module_owned_settings_validator` passes unchanged (reads the new keys through the validator). Every other suite green.

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`, which the proxy and the
two-process topology tests import — a `python3 -m pytest` run fails at collection there.
Every acceptance criterion this step owns must be enforced by a test that fails without the
change. Never weaken, skip, xfail or delete an existing assertion to make the suite pass; if an
existing test encodes a superseded guarantee, replace it and cite the superseding requirement in
the test docstring. Do not use positive-duration sleeps in tests — inject the clock, the RNG and
the transports.

## Risks

Non-trivial (cross-suite fixture change; `_OWNED_LIMITS` mirror check refuses a `total_run_seconds` differing from `limits.admission`, so both blocks change together in every fixture). `test_examples::test_example_authorization_grants_exactly_the_actions_the_modules_declare` and `..._is_complete_...` are allowlisted and keep failing until P21 rewrites them — they fail on `enabled_modules`, not on this step.

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
