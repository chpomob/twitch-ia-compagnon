# Step P16 — `audio_output.stop` returns as soon as the player exits (R7; F30; AC29)

Plan step `P16` of the approved Phase 6 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase6/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase6/spec.md`
- Brief with the operator's binding decisions: `docs/campaigns/phase6/brief.md`
- The full adversarial review whose findings this phase closes: `docs/reviews/2026-10-16-full-adversarial-review.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

In `stop(process, grace_seconds)` (`modules/audio_output/__init__.py:691`),
  replace the unconditional grace sleep with `asyncio.wait_for(process.wait(), grace_seconds)`
  (or a race between `wait()` and the grace timer), killing only on timeout, so the call returns as
  soon as the player has exited.

## Requirements

Plan mapping: R7
Acceptance criteria owned by this step: AC29
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

[`modules/audio_output/__init__.py`, `tests/test_audio_output.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[]

All dependencies are already merged into `main` when this step runs.

## Tests

AC29 fake player exiting 0.01 s after SIGTERM with grace 5 s → `stop` returns no later
  than 0.1 s after the recorded exit time (measured from the exit). A never-exiting player is still
  killed at the grace. Run against `8010edc` (fails).

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

Real-time measurement flakiness — use the fake's recorded exit timestamp and a
  monotonic clock; avoid tight wall-clock bounds beyond the spec's 0.1 s.

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
