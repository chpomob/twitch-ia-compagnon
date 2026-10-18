# Step P3 — agent_link

Plan step `P3` of the approved Phase 6 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase6/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase6/spec.md`
- Brief with the operator's binding decisions: `docs/campaigns/phase6/brief.md`
- The full adversarial review whose findings this phase closes: `docs/reviews/2026-10-16-full-adversarial-review.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

In `_on_call` (`modules/agent_link/__init__.py:1046`): (a) refuse — with an
  observable terminal refusal frame and 0 executor invocations — any call whose action is not in the
  session's negotiated set (intersection of offered and configured, computed once at pairing and
  stored on `_Session`) (F2); (b) stop reading `frame.get("principal")` (line ~1154) as the executing
  identity: the principal is the locally configured pairing principal; a frame that carries a
  `principal` different from it is refused with 0 invocations, an equal or absent one executes under
  the local principal (F2); (c) replace every interpolation of peer-supplied strings (error codes,
  reasons, action names, free text) in diagnostics and supervision records with locally defined
  labels — a closed mapping of known codes to labels, anything else rendered as a fixed
  `unrecognized` label, never the peer value (F5); (d) add two settings with conservative defaults
  validated in the manifest schema (`max_running_calls`, `max_duplicate_waiters`, finite positive
  ints, optional so no shipped profile gains a required setting): a received call beyond the
  running bound is refused with a terminal status (e.g. `busy`), and duplicate-call waiters (the
  futures at lines ~1096/~1356) beyond their bound are refused rather than queued (F14).

## Requirements

Plan mapping: R2
Acceptance criteria owned by this step: AC6, AC9
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

[`modules/agent_link/__init__.py`, `modules/agent_link/module.yaml`, `tests/test_proxy.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P1] (the executor's in-flight coalescing is what duplicate waiters defer to)

All dependencies are already merged into `main` when this step runs.

## Tests

In `tests/test_proxy.py`: AC6 out-of-set action refused, 0 executor invocations; in-set
  executed once. AC7 mismatched `principal` refused with 0 invocations; equal `principal` executes and
  the `ActionCall` the executor receives carries the locally configured principal. AC8 peer error
  frame whose code is the pairing token, and separately a 4 KiB string: neither occurs as a substring
  in captured diagnostics or supervision records. AC9 with running bound N=2 and a gated fake
  provider, a third concurrent call is refused with a terminal status, the executor sees exactly 2
  invocations, and a sampled count of live waiter tasks never exceeds the bound. Each run against
  `8010edc` (fails).

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

Non-trivial (remote-party boundary). New settings change the manifest schema — P6's
  unknown-key refusal must know them; keep them declared in `module.yaml`. The config-UI module
  presentation tests (`tests/test_manifest_presentation.py`, `tests/test_config_ui.py`) may count
  settings; extend fixtures, never loosen. Dropping peer text must not remove the local reason code
  operators need — keep the label set informative.

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
