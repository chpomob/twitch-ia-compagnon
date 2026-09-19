# Step P23 — `docs/README.md` phase 1 versioning and topology trial record; hygiene suite (R8; AC44, AC45)

Plan step `P23` of the approved Phase 1 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase1/plan.md`
- Approved specification v1.2 (requirement and acceptance-criteria wording): `docs/campaigns/phase1/spec.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

Add to `docs/README.md` (in French, same table style as the phase 0 section) "Versionnement de la spec phase 0 (phase 1)": which phase 0 / v1 assertions change and by what (the four allowlisted tests, R1's delivery replacing the hardcoded `chat.write`, R7's six-module PC profile) and the section "Phase 1 topology trial" with its 6 fields: commit, the two profiles used, hosts (distinct or loopback), TLS (used or not), observed outcome, remaining limits — recorded from the P22 run (loopback, no TLS, `fakeplatform` input, fake model) before the topology is described as delivered. `tests/test_hygiene.py`: grep `tests/` for `asyncio.sleep(` and `time.sleep(` with any argument other than a literal `0`/`0.0` → 0 matches; grep `core/` and `modules/` for `gpt-`, `claude-`, `llama`, `mistral`, `gemini` → 0 matches; AC44 asserts the section title and its 6 field labels exist.

## Requirements

Plan mapping: R8
Acceptance criteria owned by this step: AC44, AC45
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

[`docs/README.md`, `tests/test_hygiene.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P22]

All dependencies are already merged into `main` when this step runs.

## Tests

`tests/test_hygiene.py` as described.

Test command (must pass): `python3 -m pytest tests/ -q -p no:cacheprovider`
Every acceptance criterion this step owns must be enforced by a test that fails without the
change. Never weaken, skip, xfail or delete an existing assertion to make the suite pass; if an
existing test encodes a superseded guarantee, replace it and cite the superseding requirement in
the test docstring. Do not use positive-duration sleeps in tests — inject the clock, the RNG and
the transports.

## Risks

Trivial (documentation + grep). The sleep grep must tolerate `ManualClock.sleep` definitions (`async def sleep(self, delay)` is not a call) and `asyncio.sleep(0)` in `settle()`.

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
