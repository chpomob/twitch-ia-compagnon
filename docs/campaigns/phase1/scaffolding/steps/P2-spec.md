# Step P2 — `delivery` capability on action declarations, manifest parsing, twitch manifest (R1 decision 1, R5; AC58)

Plan step `P2` of the approved Phase 1 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase1/plan.md`
- Approved specification v1.2 (requirement and acceptance-criteria wording): `docs/campaigns/phase1/spec.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

Add `delivery: Mapping | None = None` to `ActionSpec` (frozen, part of equality): when present it must be a mapping `{text_argument: <name> | "none"}` and the spec's `nature` must not be `read` (`ContractError("ActionSpec.delivery", ...)`); a named `text_argument` must be a property of `argument_schema` of type `string`. Expose `ActionSpec.delivery_text_argument -> str | None` (`None` for `"none"`). In `core/loader.py::_manifest_actions` add `delivery` to `_ACTION_KEYS`, pass it through, and let the loader's `_field_error` name `actions[<i>].delivery` when the contract refuses it (AC58: a `read` action declaring `delivery` is refused at manifest validation naming the action). Declare `delivery: {text_argument: text}` on `chat.write` in `modules/twitch/module.yaml`; nothing else in that manifest changes.

## Requirements

Plan mapping: R1, R5
Acceptance criteria owned by this step: AC58
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

[`core/contracts.py`, `core/loader.py`, `modules/twitch/module.yaml`, `tests/test_contracts.py`, `tests/test_loader.py`, `tests/test_twitch.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P1]

All dependencies are already merged into `main` when this step runs.

## Tests

`tests/test_contracts.py`: `delivery` on a `write` spec is accepted and takes part in equality (two specs differing only by `delivery` are unequal); `delivery` on a `read` spec, an unknown key, a `text_argument` absent from the schema or non-string each raise naming `ActionSpec.delivery`. `tests/test_loader.py`: a temp manifest declaring `delivery` on a `read` action fails discovery with a diagnostic naming the action; the shipped twitch manifest parses with `delivery_text_argument == "text"`. `tests/test_twitch.py`: the manifest test asserts the new field and everything else unchanged.

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`, which the proxy and the
two-process topology tests import — a `python3 -m pytest` run fails at collection there.
Every acceptance criterion this step owns must be enforced by a test that fails without the
change. Never weaken, skip, xfail or delete an existing assertion to make the suite pass; if an
existing test encodes a superseded guarantee, replace it and cite the superseding requirement in
the test docstring. Do not use positive-duration sleeps in tests — inject the clock, the RNG and
the transports.

## Risks

Non-trivial (public contract change, `ActionSpec` equality used by the proxy's `hello` comparison in P19). `tests/test_examples.py::test_manifests_are_unique_and_have_coherent_capabilities` compares `DECLARATION_KEYS`; the field lives inside `actions[]`, so the top-level key set is unchanged and that test stays green until P21 extends it.

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
