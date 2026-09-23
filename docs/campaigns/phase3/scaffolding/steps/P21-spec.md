# Step P21 — YouTube ingestion mapping, `chat.write`, moderation service and degradation (R7, R1-notices, R6-identity; AC37-send, AC38, AC32-youtube, AC39)

Plan step `P21` of the approved Phase 3 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase3/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase3/spec.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

- **Normalization.** Listed messages become schema-2 events for platform
    `youtube`.
  - **Roles.** From `authorDetails`: `isChatOwner` → broadcaster,
    `isChatModerator` → moderators, `isChatSponsor` → subscribers.
  - **Notice mapping.** `newSponsorEvent` → `sub`,
    `memberMilestoneChatEvent` → `resub`, `membershipGiftingEvent` →
    `sub_gift`, `superChatEvent`/`superStickerEvent` → `tip`, when listed.
    Other types are ignored and counted.
  - **Pipeline.** Anti-echo, the `:` refusal, dedup, the chat-context feed,
    triggers and admission.
  - **`chat.write`.** Declared for `youtube/*/chat`:
    - more than 200 characters → `text_too_long`, 0 requests;
    - the quota check (P20);
    - a 403 `rateLimitExceeded`/429 → `rate_limited` with a block until the
      retry;
    - uncertain → `external_unknown`.
  - **Moderation service.** `{delete_message, timeout}`:
    - delete → `liveChatMessages.delete`;
    - timeout → `liveChatBans.insert` with `type: temporary` and
      `banDurationSeconds`;
    - a `permanent` type is never built.
  - **No clip or poll service.**
  - **Catalog assertion (decision 11).** The youtube `chat.write`
    declaration makes the running multiplicity assertion in
    `tests/test_examples.py` (set to {twitch, kick} by P19) exactly
    {twitch, kick, youtube}; only that assertion changes.

## Requirements

Plan mapping: R7, R1, R6
Acceptance criteria owned by this step: AC37, AC38, AC32, AC39
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

[modules/youtube/__init__.py, modules/youtube/module.yaml, tests/test_youtube.py, tests/test_examples.py]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P20]

All dependencies are already merged into `main` when this step runs.

## Tests

- AC37 send half: after exhaustion one send succeeds with 1 insert; a
    second is `refused quota_exhausted` with 0 requests.
  - AC38: 200 characters give 1 request; 201 give `text_too_long` and 0;
    the three role mappings; the four notice mappings; delete and timeout
    make 1 request each.
  - AC32 youtube half: an author with `:` gives 0 admissions and
    invalid + 1.
  - AC39, with twitch + kick + youtube enabled on one runtime:
    - `chat.write` has exactly 3 bindings on disjoint destinations;
    - the same viewer id on two platforms gives two sessions;
    - `stream.poll.create` and `stream.clip.create` are unbound for kick and
      youtube with `platform_unsupported`;
    - a case-insensitive word scan of `core/` finds 0 `kick`/`youtube`.

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`, which the proxy and the
two-process topology tests import — a `python3 -m pytest` run fails at collection there.
Every acceptance criterion this step owns must be enforced by a test that fails without the
change. Never weaken, skip, xfail or delete an existing assertion to make the suite pass; if an
existing test encodes a superseded guarantee, replace it and cite the superseding requirement in
the test docstring. Do not use positive-duration sleeps in tests — inject the clock, the RNG and
the transports.

## Risks

- YouTube returns 403 for several unrelated reasons. Classification by
    `error.errors[0].reason` must separate rate (`rateLimitExceeded`) from
    rejected (`forbidden`, `insufficientPermissions`) and from quota
    (`quotaExceeded`, mapped to `quota_exhausted` and syncing the local
    ledger to 0).
  - `stream.poll.create`'s unbound reason for kick/youtube comes from
    `stream_control`. If its current reason string differs from
    `platform_unsupported`, a change there would be outside the targets.
    Verify first. If it differs, record it as a spec gap for the P26 gate
    instead of editing `modules/stream_control/`.

## Constraints

- One atomic commit for the whole step when the suite is green; Conventional Commit message
  (`feat(phase3): ...`, `fix(phase3): ...`, `test(phase3): ...`, `refactor(phase3): ...`).
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
