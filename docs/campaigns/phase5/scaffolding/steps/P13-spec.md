# Step P13 — Fixtures with provenance, guard test, and the fake instance server (R8; AC35)

Plan step `P13` of the approved Phase 5 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase5/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase5/spec.md`
- Brief with the operator's binding decisions: `docs/campaigns/phase5/brief.md`
- The integration study the brief is grounded in: `docs/research/streamerbot-etude-2026-10.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

- **Fixture format.** Each fixture file is `{"frames": [{"provenance": {"kind": "documented", "ledger_id": <int>} | {"kind": "observed", "trial_record": "docs/campaigns/phase5/bridge-trial-record.md#qN"}, "question": <1..8, optional>, "frame": {...}}]}`.
  - **Contents.** Every frame is taken from the documented schemas cited by ids in `docs/research/sources-ledger-streamerbot.json`: requests (id 2), authentication (id 4), events (id 6), and the further ids the build looks up. The frames are the hello with and without authentication, `GetActions`, the `DoAction` acknowledgement and error, `Subscribe`, the custom-event broadcast, the active-viewer list, each platform's chat message, and each notice kind the ledger documents for that platform.
  - **Section 9 questions.** No fixture asserts a behaviour that the study's section 9 lists as unverified. In particular there is no frame for the answer to a nonexistent action name (question 1), and none for a custom-event round trip (question 6). So at merge, the count of fixtures naming a question is 0.
  - **Fake server.** `tests/test_automation_bridge.py` gains a `FakeInstance` helper: an aiohttp `web` WebSocket server on `127.0.0.1` with an ephemeral port. It replays fixture frames, records every received frame (by request id), and can require authentication, refuse it, send a malformed hello, fail inventory, delay or withhold acknowledgements, close the connection, push N events, and stop.
  - **No vendor name** is used in identifiers outside `modules/streamerbot` and the fixtures.

## Requirements

Plan mapping: R8
Acceptance criteria owned by this step: AC35
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

[`tests/fixtures/automation_bridge/README.md`, `tests/fixtures/automation_bridge/hello.json`, `tests/fixtures/automation_bridge/authentication.json`, `tests/fixtures/automation_bridge/get_actions.json`, `tests/fixtures/automation_bridge/do_action.json`, `tests/fixtures/automation_bridge/subscribe.json`, `tests/fixtures/automation_bridge/custom_event.json`, `tests/fixtures/automation_bridge/active_viewers.json`, `tests/fixtures/automation_bridge/chat_twitch.json`, `tests/fixtures/automation_bridge/chat_kick.json`, `tests/fixtures/automation_bridge/chat_youtube.json`, `tests/fixtures/automation_bridge/notices_twitch.json`, `tests/fixtures/automation_bridge/notices_kick.json`, `tests/fixtures/automation_bridge/notices_youtube.json`, `tests/fixtures/automation_bridge/error_response.json`, `tests/test_automation_bridge.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P12]

All dependencies are already merged into `main` when this step runs.

## Tests

**AC35 guard.** Load every fixture file and fail if:
  - any frame lacks a provenance;
  - a `documented` provenance names an id absent from the ledger;
  - a frame naming a question 1–8 is not `observed`.

  Assert that the count of frames naming a question is 0 while `docs/campaigns/phase5/bridge-trial-record.md` does not exist. A self-test: the fake server answers one hello and records one frame.

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

- The ledger may not document every notice kind per platform. AC43 counts only kinds the ledger documents, so the fixture set **is** the list. The step records the enumerated kinds in the fixtures README.
  - A documented schema field may be ambiguous. When it is, the fixture follows the documented example verbatim, and the ambiguity is noted in the README for the trial.

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
