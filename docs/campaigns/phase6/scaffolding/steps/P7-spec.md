# Step P7 — `!forgetme` erasure reaches the brain's conversation memory (R4; F6; AC13, AC14)

Plan step `P7` of the approved Phase 6 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase6/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase6/spec.md`
- Brief with the operator's binding decisions: `docs/campaigns/phase6/brief.md`
- The full adversarial review whose findings this phase closes: `docs/reviews/2026-10-16-full-adversarial-review.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

Use the existing service mechanism (the brain already publishes its scheduler as a
  service resolved by watch) rather than a bus event, so no erasure signal enters bus history: the
  brain publishes an erasure service (`forget(platform, channel_id, viewer_id)`), and viewer_memory's
  `forget` path (`modules/viewer_memory/__init__.py:1271`) resolves it and calls it after its own
  file erasure completes (absent brain → no-op, recorded in a local diagnostic with no viewer text).
  In the brain, `ConversationMemory` (`modules/brain/__init__.py:1747`) gains a per-key erasure epoch:
  `forget(key)` (line 1855) clears the exchanges and bumps the key's epoch; each run records the
  epoch at admission, and `record` for that key refuses (drops, counts) a record whose admission
  epoch is older than the current one. The erasure call carries only the key, never message text;
  the existing `memory.removed` supervision fact keeps carrying `reason`/`count` only. Declare the
  service relation in the manifests if the loader requires it (structural decision flagged in the
  commit message: service over event, because bus history must not carry the key's text and the
  service path matches watch↔brain).

## Requirements

Plan mapping: R4
Acceptance criteria owned by this step: AC13, AC14
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

[`modules/brain/__init__.py`, `modules/viewer_memory/__init__.py`, `modules/viewer_memory/module.yaml`, `modules/brain/module.yaml`, `tests/test_brain.py`, `tests/test_viewer_memory.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[]

All dependencies are already merged into `main` when this step runs.

## Tests

In `tests/test_brain.py`/`tests/test_viewer_memory.py` (integration through a runtime
  with both modules): AC13 record an exchange, run `!forgetme` for the key → `recall(key) == ()`,
  the next prompt built for that viewer contains none of the erased text, another key's recall is
  unchanged. AC14 a run admitted before erasure whose record lands after it leaves `recall(key)`
  empty; bus history and audit records produced by the erasure contain none of the erased text. Run
  against `8010edc` (fails).

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

Non-trivial (module boundary, ordering invariant). Epoch maps must be bounded (evict
  epochs with the memory's own LRU or keep only for keys erased within the run horizon). Ordering
  between viewer_memory's async file deletion and the brain call: call the brain after the file
  erasure is committed, but a failure of either must not leave the other silently done — report.
  If the loader requires module dependency declarations for services, an optional resolve keeps
  profiles without brain valid.

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
