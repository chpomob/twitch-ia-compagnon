# Step P7 — Twitch community notices, opt-in subscriptions and the `:` identity refusal (R1, R6; AC5, AC32-twitch)

Plan step `P7` of the approved Phase 3 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase3/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase3/spec.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

- **Manifest.** Declare `notices.kinds` (array of `sub`, `resub`,
    `sub_gift`, `community_sub_gift`, `raid`, `follow`, default empty) and the
    `event_kind` trigger type. The default policy, action and credentials are
    unchanged.
  - **Subscriptions.** Activation creates `channel.chat.message` as today.
    It adds `channel.chat.notification` iff one of the chat-notification
    kinds is listed, and `channel.follow` (version 2, needs the moderator
    follower-read scope) iff `follow` is listed.
  - **Follow degradation.** A rejected follow subscription (non-2xx) emits
    one `module.degraded` naming the `follow` capability and reason. Chat
    continues.
  - **Mapping.** `_normalize_notification` maps `notice_type` `sub`,
    `resub`, `sub_gift`, `community_sub_gift` and `raid` (raid author = the
    raider from the event's raid block) and `channel.follow` → `follow`,
    each only when its kind is listed.
    - A notice carries its `kind`, its author as viewer identity, the
      `system_message` text and trusted provenance.
    - Other notice types (`announcement`, …) and unlisted kinds are ignored
      and counted by a new `notices_ignored` counter.
    - An anonymous gift is fed as `system:anonymous` and never admitted
      (decision 3).
  - **`:` refusal.** Any author id containing `:` is refused and counted in
    the invalid counter before publication and admission.
  - **Allowlisted tests.** Rewrite `test_manifest_declares_twitch_source_and_sink`
    (settings + `notices`, types + `event_kind`). In
    `test_manifests_are_unique_and_have_coherent_capabilities`, update only
    the twitch-key expectation (decision 11).

## Requirements

Plan mapping: R1, R6
Acceptance criteria owned by this step: AC5, AC32
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

[modules/twitch/__init__.py, modules/twitch/module.yaml, tests/test_twitch.py, tests/test_examples.py]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P4]

All dependencies are already merged into `main` when this step runs.

## Tests

(AC5 in full, on the existing scripted EventSub transport)
  - Without `notices` there is exactly 1 subscription; with `[sub, raid,
    follow]` there are 3.
  - A raid from 42 gives one `raid` event with author `42`, 1 chat-context
    entry and 1 admitted run in session `(twitch, c, 42)` under
    `event_kind [raid]`.
  - `announcement` gives 0 events and `notices_ignored` + 1.
  - An anonymous community gift gives 1 context entry and 0 admissions.
  - A rejected follow subscription gives 1 `module.degraded` naming
    `follow`, and a subsequent chat message is admitted.
  - AC32 twitch half: author id `x:y` gives 0 admissions and invalid + 1.

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`, which the proxy and the
two-process topology tests import — a `python3 -m pytest` run fails at collection there.
Every acceptance criterion this step owns must be enforced by a test that fails without the
change. Never weaken, skip, xfail or delete an existing assertion to make the suite pass; if an
existing test encodes a superseded guarantee, replace it and cite the superseding requirement in
the test docstring. Do not use positive-duration sleeps in tests — inject the clock, the RNG and
the transports.

## Risks

- Real EventSub payload field names (`chatter_user_id`, `notice_type`,
    `raid.user_id`, `community_sub_gift.id`, `chatter_is_anonymous`) must
    match the public schema. The step pins them in fixtures copied from the
    EventSub reference, and the P24 trial checks them for real.
  - The follow subscription may need a different condition
    (`moderator_user_id`). Build it from the configured bot identity.

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
