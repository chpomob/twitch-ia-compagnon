# Step P22 — Full-branch review gate and tracked report (R8; AC38; all of R1–R10)

Plan step `P22` of the approved Phase 5 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase5/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase5/spec.md`
- Brief with the operator's binding decisions: `docs/campaigns/phase5/brief.md`
- The integration study the brief is grounded in: `docs/research/streamerbot-etude-2026-10.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

Before delivery, review the **whole branch diff** (`git diff main...HEAD`) as one unit, not commit by commit.
  - **Why the per-commit reviews are not enough.** Each step's review sees one commit, so a cross-file defect is invisible by construction: each commit can be correct alone while the set is broken where files interact. Component decomposition is not integration. Examples here:
    - P4's guard and P8/P9's ingestion gates must agree on the "no ownership table = unrestricted" default;
    - P5's exclusions and P18's bridge binding must never overlap;
    - P4's v1 facade and the loader's v1 route must agree on `constrains_any()`;
    - P14's failed request futures and P15's run waiter must agree on the `acknowledgement_lost` mapping;
    - P12's `timeout_seconds` and P15's acknowledgement deadline must keep the cause intact;
    - P11's cue trust and P16's author identity must not let a viewer forge a cue;
    - D1's family set and the `watch` module's evaluations must stay compatible.
  - **The checklist:**
    - every R1–R10 and AC1–AC45 is mapped to a passing test;
    - the full suite is green except the allowlisted tests, each adapted as listed; record any test added to the allowlist in P1/P7 with its contradicting requirement;
    - re-run the AC11 grep;
    - grep `modules/` for any use of `excluded_platforms` or `add_companion_identities` outside the intended callers;
    - the deviations D5 and D6 (7 core files instead of 5) are stated;
    - P11's consumer audit result is recorded and decided;
    - the R6 "no default endpoint, port, password, version or action name" grep is run over `modules/streamerbot` and the tests;
    - the 4 shipped profiles have 0 changed lines;
    - AC38: the report is tracked.
  - **The report** is written to `docs/campaigns/phase5/full-branch-review.md`, with the findings, their resolution, and the commands run with their results.

## Requirements

Plan mapping: R8, R1, R10
Acceptance criteria owned by this step: AC38
Read the exact R/AC text in the specification file before writing code. Requirements not listed
here are other steps' responsibility — do not implement them.

Standing phase-5 decisions that override any contrary reading of a requirement:
- **The bridge is optional in every case.** Absent, disabled or unreachable, the companion behaves
  exactly as it does today: no setting becomes required at startup, no default path changes, and no
  shipped profile depends on it. An unreachable instance produces a *ready* companion with an
  explicitly unavailable capability — never a failed startup.
- **The minimum scope is the sink and the triggers, together**: the sink runs a named action of the
  user's instance behind our own authorization, deadlines and honest terminal outcomes; the triggers
  let an event the instance routes enter the companion through the ordinary trigger policies.
- **Its events may REPLACE our basic inputs (chat, viewers) — never in parallel.** For every
  (family, platform) pair there is at most one active provider, resolved at preparation from the
  profile and the manifests, and an ambiguous configuration is refused with a diagnostic naming the
  family and both providers. Counting on the deduplication window to hide two providers is excluded.
- **Do not hide the integration's defects.** An execution the instance cannot confirm is never
  reported as a success: an acknowledged run ends `external_unknown` with its cause, and the model
  and the operator both see it.
- **This phase is NOT "delivered" at merge.** The README and the documentation index carry
  "not delivered — awaiting real-instance trial" until the trial record exists, and a test enforces it.
- **The vendor and product names never appear in `core/`.** The kernel vocabulary stays vendor-free:
  action `automation.action.run`, permission `automation.run`, destination scope `automation`,
  families `chat`/`viewers`/`cues`.
- **The approved plan's deviations D1–D8 are decisions of this phase** and override any contrary
  reading of the specification — including the two files beyond the specification's cost row:
  `core/actions.py` (`excluded_platforms`, D5) and `core/triggers.py` (`add_companion_identities`,
  D6). The D8 transmission-boundary canary must stay green; if an aiohttp upgrade breaks it, take the
  fallback route D8 documents and say which route you took in the commit message.
- The configuration UI stays a **local-only** surface; the ownership table and its diagnostics are
  displayed **read-only**, and nothing in the UI can widen the authorization set or write the
  `secrets` and `actions` blocks.

## Files

[`docs/campaigns/phase5/full-branch-review.md`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P21]

All dependencies are already merged into `main` when this step runs.

## Tests

The full suite (`pytest -q`), `pytest tests/test_phase5_trials.py -q` (8 collected, 8 skipped) and `pytest tests/test_phase5_docs_status.py -q` (passing). Record the three summary lines in the report.

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

- Gate findings may require fixes in earlier files. They are applied as fix commits before the report is finalised, and the report lists them.
  - The phase is **not** "delivered" at merge. Only the trial record (outside this plan) changes that.

## Ordering rationale

## Constraints

- One atomic commit for the whole step when the suite is green; Conventional Commit message
  (`feat(phase5): ...`, `fix(phase5): ...`, `test(phase5): ...`, `refactor(phase5): ...`, `docs(phase5): ...`).
  The scope MUST be `phase5`: the repository history is full of `feat(phase4):` / `fix(phase3):` commits
  from earlier phases — do NOT copy that habit. Every commit this campaign makes is `(phase5)`.
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
