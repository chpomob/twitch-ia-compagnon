# Step P20 — README phase 2 versioning and per-provider trial records, hygiene assertions, opt-in trial runner (R9, R10-documentation; AC34, AC41-README, scene trial against an installed provider)

Plan step `P20` of the approved Phase 2 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase2/plan.md`
- Approved specification v1.2 (requirement and acceptance-criteria wording): `docs/campaigns/phase2/spec.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

`docs/README.md` (French, in the phase 1 style): a section "Versionnement de la spec phase 1 (phase 2)" naming each of the eight allowlisted tests and what replaced it, the R10 executor rule (a provider-authored interruption record at or after the deadline is adopted; a late confirmation is still never a success; the timer stays in force) with its two new `tests/test_actions.py` cases, the `played_ms` accepted-bytes estimate (bytes accepted by the pipe, not a device clock), the playback and capture sections stating that a stop or a kill happens **at** the call deadline with no lead (AC41), the scene provider's wire protocol (obs-websocket 5) and the remote-readiness, executor-cancellation and stalled-wake limits of decision 1 (a transcription-phase wake delayed by the whole 1-s reserve is a late `success` under the phase 1 rule); a section "Essais d'intégration par fournisseur (phase 2)" with a table of exactly six rows — speech synthesis, transcription, playback command, capture command, scene provider, platform polls — and five non-empty fields each (commit, settings shape without secret or token, outcome — a run's result or "not run" with the reason, e.g. the scene provider absent and port 4455 closed on the reference machine — limits, date). `tests/test_hygiene.py`: `test_ac34_readme_records_the_phase_2_provider_trials` parsing the table (`_section`/`_table_row` style) and the versioning section naming every allowlisted test; the sleep and model-literal checks unchanged over the whole tree. `tests/test_phase2_trials.py`: one test per provider, each `pytest.skip("<VARIABLE> is unset")` unless `PHASE2_SPEECH_ENDPOINT`, `PHASE2_TRANSCRIPTION_ENDPOINT`, `PHASE2_PLAYER_ARGV`, `PHASE2_RECORDER_ARGV`, `PHASE2_SCENE_URL`, `PHASE2_POLL_PLATFORM` (with the platform's credentials) is set; when set, the real module is activated with the real transports/runners (real clock, no sleep call in the test file) and the trial's outcome is printed in the form the README row cites.

## Requirements

Plan mapping: R9, R10
Acceptance criteria owned by this step: AC34, AC41
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

[`docs/README.md`, `tests/test_hygiene.py`, `tests/test_phase2_trials.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P19]

All dependencies are already merged into `main` when this step runs.

## Tests

AC34 — the README parses to exactly six rows with five non-empty fields; the versioning section names every allowlisted test; with the six variables unset every trial is skipped with a reason naming the unset variable; with one set (run by hand on the reference machine and recorded) the corresponding trial runs; `test_ac45_the_suite_makes_no_positive_duration_sleep_call` and `test_ac45_core_and_modules_name_no_model` pass over the final tree; the README's playback and capture sections contain none of `lead`, `marge`, `margin` in the deadline sentences (AC41, asserted by the hygiene test over those two subsections).

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`, which the proxy and the
two-process topology tests import — a `python3 -m pytest` run fails at collection there.
Every acceptance criterion this step owns must be enforced by a test that fails without the
change. Never weaken, skip, xfail or delete an existing assertion to make the suite pass; if an
existing test encodes a superseded guarantee, replace it and cite the superseding requirement in
the test docstring. Do not use positive-duration sleeps in tests — inject the clock, the RNG and
the transports.

## Risks

Trivial in code; the honesty of the six rows is the deliverable: the scene-provider row records what actually ran: the arbiter decided (22/09) that a REAL scene-provider integration trial is performed against a provider installed on the machine (obs-websocket protocol 5 over the `websocket` kind), and that "not run" with the closed port is recorded only if the trial genuinely could not run, and the commit field names a commit that exists on the branch (filled at the gate).

## Constraints

- One atomic commit for the whole step when the suite is green; Conventional Commit message
  (`feat(phase2): ...`, `fix(phase2): ...`, `test(phase2): ...`, `refactor(phase2): ...`).
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
