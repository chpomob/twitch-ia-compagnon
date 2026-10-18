# Step P2 — Twitch `channel.chat.send` compatibility route goes through the executor under default-deny (R1; F1; AC1, AC2; allowlisted twitch tests)

Plan step `P2` of the approved Phase 6 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase6/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase6/spec.md`
- Brief with the operator's binding decisions: `docs/campaigns/phase6/brief.md`
- The full adversarial review whose findings this phase closes: `docs/reviews/2026-10-16-full-adversarial-review.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

Rewrite `handle_chat_send` (`modules/twitch/__init__.py:1476`, subscription on
  `_CHAT_SEND_EVENT` line 186) so it no longer calls the Helix transport directly: it builds an
  `ActionCall` for `chat.write` with an explicit compatibility principal (a named module constant,
  documented in the module docstring and in `module.yaml` comments) and the Twitch destination of the
  configured broadcaster channel, a fresh `call_id`, and invokes the runtime's executor (resolved from
  the module context as other modules do). With no applicable rule the executor refuses, 0 Helix
  POSTs are emitted, and the returned legacy metadata is `delivery_status: "failed"`,
  `delivery_outcome: <executor terminal status>`; a success maps to `sent`, other terminal statuses to
  `failed` with that status. When the runtime has no executor, the route refuses (default-deny), it
  never falls back to the direct transport. This makes `docs/design-v2.md:584` true. Adapt the six
  allowlisted tests in `tests/test_twitch.py` (`test_chat_send_uses_configured_identity_and_preserves_event`,
  `test_send_failure_continues_chain_and_next_send_succeeds`,
  `test_pending_send_releases_response_on_timeout_cancellation_and_close`,
  `test_notification_mapping_dedup_and_retry_reconnect`,
  `test_lost_sent_trace_neither_retries_nor_repeats_the_send`,
  `test_confirmed_outcome_never_waits_for_the_sent_trace`) by a fixture that configures the
  `chat.write` grant to the compatibility principal, assertions unchanged, each docstring citing F1
  and R1 (AC38). No shipped `*.example` profile changes (none publishes `channel.chat.send`).

## Requirements

Plan mapping: R1
Acceptance criteria owned by this step: AC1, AC2
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

[`modules/twitch/__init__.py`, `modules/twitch/module.yaml`, `tests/test_twitch.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P1] (identity-bound retention and in-flight semantics the route relies on)

All dependencies are already merged into `main` when this step runs.

## Tests

New in `tests/test_twitch.py`: AC1 — with an executor and no rule, publishing
  `channel.chat.send {"text": "hi"}` → 0 POSTs on the fake Helix, provider invoke count 0,
  `delivery_status == "failed"` and `delivery_outcome` equal to the executor's refusal status; with a
  matching rule → exactly 1 POST and `sent`. AC2 — exactly 1 `action.requested` and 1
  `action.completed` trace carrying the compatibility principal and Twitch destination, equal in
  shape to a direct executor call's traces. Run against `8010edc` (fails). Full `tests/test_twitch.py`
  plus `tests/test_integration.py`, `tests/test_examples.py`, `tests/test_users.py` pass.

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

Non-trivial (module/service boundary). Any other fixture/test that sends to Twitch via
  `channel.chat.send` without a grant (caller table rows for `tests/fixtures/modules/fakeplatform`,
  `test_integration.py`, `test_shutdown.py`, `test_examples.py`) would turn red but is **not** on the
  allowlist: if one breaks, stop and flag rather than edit its assertions. The chain-continuation and
  timeout-release semantics of the old route must be preserved through the executor's timeout/cancel
  path. Recursion risk: `chat.write` provider must not republish `channel.chat.send`.

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
