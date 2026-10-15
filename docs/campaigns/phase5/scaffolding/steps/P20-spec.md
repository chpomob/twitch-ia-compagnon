# Step P20 — Opt-in real-instance trial nodes and the "not delivered" docs guard (R8; AC36, AC37)

Plan step `P20` of the approved Phase 5 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase5/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase5/spec.md`
- Brief with the operator's binding decisions: `docs/campaigns/phase5/brief.md`
- The integration study the brief is grounded in: `docs/research/streamerbot-etude-2026-10.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

- **The 8 nodes.** One `pytest` node per study question:
    1. nonexistent-name behaviour;
    2. latency and blocking queue;
    3. enforce-mode authentication;
    4. minimum version per request;
    5. C# HTTP/JSON availability;
    6. custom-event round trip;
    7. servers under Wine;
    8. subscribed-event volume.
  - **Skipping.** Each is skipped unless its variables are set, for example `PHASE5_TRIAL_URL`, `PHASE5_TRIAL_PASSWORD` and per-node variables. The skip reason names them.
  - **Nodes 5 and 7** record an operator observation passed through variables, rather than probing.
  - **Sockets.** No socket is opened when skipped: the import is lazy, and the skip is decided in the body before any I/O.
  - Mirror the structure of `tests/test_phase3_trials.py`.
  - **Docs guard (AC37), in its own file `tests/test_phase5_docs_status.py` (finding P5).** It is not in `tests/test_phase5_trials.py`, so that file collects exactly the 8 trial nodes (AC36), and the docs guard is never skipped with them. While `docs/campaigns/phase5/bridge-trial-record.md` is absent:
    - `README.md` and `docs/README.md` must contain the bridge's status "not delivered";
    - no tracked Markdown file applies "delivered" to the bridge outside those two "not delivered" statements (a regex over lines that mention the bridge or its guide).

    Once the record exists: it must carry an instance version, a date and one answer for each question 1–8.

## Requirements

Plan mapping: R8
Acceptance criteria owned by this step: AC36, AC37
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

[`tests/test_phase5_trials.py`, `tests/test_phase5_docs_status.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P15]

All dependencies are already merged into `main` when this step runs.

## Tests

- **AC36.** `pytest tests/test_phase5_trials.py` with no variable → 8 collected, 8 skipped, each reason naming its variables, and 0 sockets (socket-creation spy).
  - **AC37.** `tests/test_phase5_docs_status.py` runs unconditionally (no trial variable). It fails until P21 adds the README rows, so P21's tests must pass together with it, and P22 runs both files.

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

- The AC37 regex could flag unrelated uses of "delivered" (e.g. "delivery"). Match the whole word, and only on lines naming the bridge or its guide.
  - Order: P20's docs guard is red until P21. To keep each step green, P20 marks the AC37 test in `tests/test_phase5_docs_status.py` `xfail(strict=True)` with the reason "guide rows land in P21", and P21 removes the marker. This is flagged so it is not read as a relaxed test.

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
