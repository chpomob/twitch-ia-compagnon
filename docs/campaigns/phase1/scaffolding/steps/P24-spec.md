# Step P24 — Full-branch review gate before PR delivery (all requirements)

Plan step `P24` of the approved Phase 1 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase1/plan.md`
- Approved specification v1.2 (requirement and acceptance-criteria wording): `docs/campaigns/phase1/spec.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

Review every changed file of the branch **together**, not per commit: per-commit reviews miss cross-file bugs by construction — each step is correct in isolation (P3's executor validates `image_ref` leases; P12's brain releases them; P19's proxy stores them under the calling run; P20's agent reads them from its own store) yet the set only holds when the same `run_id`, the same store instance and the same clock flow across `core/actions.py`, `core/attachments.py`, `modules/brain`, `modules/capture`, `modules/proxy` and `tests/conftest.py`; component decomposition ≠ integration. The gate walks: (1) the caller tables below against `grep -rn` of the final tree (no stale `DELIVERY_ACTION`, `ActionExecutor(` constructors all pass the store, every `AdmissionScheduler(` in the brain passes the hook); (2) every requirement R1–R8 against the step that claims it and the test that proves it (AC1–AC58 mapped); (3) the four allowlisted tests are the only phase 0 assertions changed, and each rewrite keeps a guarantee; (4) no module name in `core/main.py`, no `twitch` in the neutral modules, no model literal, no positive sleep; (5) the 11 fake-done shortcuts over the whole diff; (6) `pytest -q` green on the full suite, `--check-config` 0 on the three profiles, and the P22 process test run once on the gate commit; (7) the README trial section names that commit. Findings are fixed in place and recorded in `docs/README.md` (gate findings table, as P24 of phase 0 did).

## Requirements

Plan mapping: (see plan)
Acceptance criteria owned by this step: (see plan)
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

[`docs/README.md`, `docs/campaigns/phase1/spec.md`, `docs/campaigns/phase1/plan.md`]

**Do not overwrite the repository-root `spec.md` / `plan.md`**: those are the frozen v1-MVP
documents (also archived under `docs/campaigns/v1-mvp/`). The phase-1 specification and plan are
`docs/campaigns/phase1/spec.md` and `docs/campaigns/phase1/plan.md` — record versioning there and
in `docs/README.md`.

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P23]

All dependencies are already merged into `main` when this step runs.

## Tests

The full suite; the gate's own checklist above; `git diff main...HEAD --stat` reviewed against the spec's target list (every target touched, nothing outside it except test fixtures and the plan).

Test command (must pass): `python3 -m pytest tests/ -q -p no:cacheprovider`
Every acceptance criterion this step owns must be enforced by a test that fails without the
change. Never weaken, skip, xfail or delete an existing assertion to make the suite pass; if an
existing test encodes a superseded guarantee, replace it and cite the superseding requirement in
the test docstring. Do not use positive-duration sleeps in tests — inject the clock, the RNG and
the transports.

## Risks

Non-trivial (integration). The gate may surface cross-step regressions (e.g. the executor's size bound and the brain's budget disagreeing); each is fixed in the file that owns the rule, with its test, before delivery.

## Caller enumeration

The spec's tables for `ActionObservation.parts`, the stale-drop hook, the brain settings surface, the `delivery` capability, `core.main` and `FakeSession` stand as written and are applied by P1–P13. This plan adds the API change decision 3 introduces:

### `ActionExecutor.__init__` gains `attachments=None`, `max_observation_bytes=None` (P3)

| File | Function/Method | Migration Note |
|------|----------------|----------------|
| core/main.py | `_assemble_runtime` | Pass the `AttachmentStore` it already builds; `max_observation_bytes` left `None` (brain budget applies in P12). |
| tests/conftest.py | `runtime_context` | New `attachments` parameter forwarded to the executor and the context. |
| tests/test_actions.py | every `ActionExecutor(...)` constructor | No change (both optional); new lease/size tests pass a store. |
| tests/test_lifecycle.py, tests/test_retention.py, tests/test_integration.py | executors built through `runtime_context` or `_assemble_runtime` | No change. |
| modules/proxy, modules/agent_link (new) | none — they call `context.executor.invoke` | No constructor use. |
| Unknown | external embedders of `core.main.run` | None known; the change is additive. |

### `AdmissionScheduler.__init__` gains `on_stale_drop=None` (P4)

| File | Function/Method | Migration Note |
|------|----------------|----------------|
| modules/brain/__init__.py | `BrainModule.__init__` | Pass `self._on_stale_drop` (P13). |
| tests/test_admission.py | 4 constructors | No change; hook tests added. |
| tests/test_brain.py | `RecordingScheduler` forwarder | Binds the hook alongside `run` (P13). |

### `ActionSpec` gains `delivery=None` (P2)

| File | Function/Method | Migration Note |
|------|----------------|----------------|
| core/loader.py | `_manifest_actions` | Parses the key; refuses on `read`. |
| modules/twitch/module.yaml, tests/fixtures/modules/fakeplatform/module.yaml, tests/fixtures/modules/fakeeffects/module.yaml | `chat.write`, `audio.say`, `stream.set_scene` | Declare it. |
| modules/proxy/__init__.py | `hello` comparison | Equality includes the field, no branch. |
| modules/agent_link/__init__.py | `hello` declaration | Serialises the field. |
| tests/test_actions.py, tests/test_contracts.py, tests/test_loader.py | `ActionSpec(...)` constructors | No change (defaulted). |

### `core.main.main` gains `--check-config`; `load_config` accepts `builtin` (P5)

| File | Function/Method | Migration Note |
|------|----------------|----------------|
| tests/test_main.py | `main([...])`, `run(...)`, `load_config(...)` | No change; new cases added. |
| tests/test_profiles.py (new) | `check_config`, subprocess `python -m core.main --check-config` | New consumer. |
| pyproject.toml | console script `core.main:main` | New consumer. |

## Ordering rationale

Contracts before consumers: P1 (parts, vocabulary) and P2 (delivery capability) change `core/contracts.py`, which every later file imports; P3 makes the executor enforce the parts contract before any provider produces one. P4 is an independent core seam (scheduler hook) needed by P13; P5 (entry point) follows P3 because both edit `core/main.py`, and P6 packaging follows P5 so the console script points at a `main` that already knows `--check-config`. P7 upgrades the shared harness before the brain changes shape (P9–P13), because every brain suite reads it, and P9 also waits for P6 so the three editors of `tests/test_main.py` (P5, P6, P9) run in one line; P8 ships the fixtures the delivery and platform-neutrality tests need before P11 uses them. The brain is rebuilt in four increasing layers — settings (P9) so fixtures carry the new keys first, adapter (P10) so the probe and tool shape exist, delivery list (P11) so the terminal step is pluggable while the loop is still single-turn (and today's tests keep passing), then the multi-turn loop (P12) and the fallback/deadline rules (P13) that need both the loop and the list. The three local capability modules (P14–P16) depend only on contracts, executor and settings, so they follow the brain and are integrated end-to-end in P17. The remote path is documented first (P18), then the brain side (P19), which the agent side (P20) dials, and the AC33 in-process pairing test closes them. Profiles and the examples/profile suites (P21) need every module to exist and P17's `tests/test_examples.py` seams to be in place; the two-process trial (P22) needs the profiles; the README records that trial and the hygiene grep runs over the finished tree (P23). P24 is the full-branch gate: it reviews the integrated set that no per-step review could see.

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
