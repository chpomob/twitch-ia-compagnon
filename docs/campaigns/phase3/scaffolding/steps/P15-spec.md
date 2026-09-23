# Step P15 — `moderation` propose mode, approval/rejection commands, expiry, `auto_apply` and per-channel overrides (R5; AC26, AC28-override, AC28-fact count)

Plan step `P15` of the approved Phase 3 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase3/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase3/spec.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

- **Proposal table.** Bounded by `max_pending` (1–256, 32), keyed by
    short ids from an injected id source. Each proposal has
    `proposal_ttl_seconds` (30–3600, 300). When the table is full, the
    proposal closest to expiry is evicted and publishes an `expired` fact.
  - **Proposing.** `propose` → `success disposition proposed, proposal_id`,
    0 requests. When the operation is in `propose.auto_apply` (default
    empty), the proposal is applied at once through P14's application path.
  - **Commands.** `approve_command` (default `!modok`) and `reject_command`
    (`!modno`) followed by `<id>`, from a `kind == message` event in the
    **same channel** with trusted `broadcaster` or `moderators` roles:
    - approval → the P14 application (the same function, so the same
      per-channel lock and reservation) → a fact (`applied` or the refusal
      or error);
    - rejection → a `rejected` fact;
    - an unknown id or an untrusted author → nothing (counted).
  - **Expiry.** A supervised expiry sweep on the injected clock publishes an
    `expired` fact.
  - **Overrides.** Per-channel `mode` overrides are resolved per request.

## Requirements

Plan mapping: R5
Acceptance criteria owned by this step: AC26, AC28, AC28
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

[modules/moderation/__init__.py, modules/moderation/module.yaml, tests/test_moderation.py]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P14]

All dependencies are already merged into `main` when this step runs.

## Tests

AC26 in full:
  - a proposal id with 0 requests;
  - `!modok` from the trusted broadcaster gives 1 request and `applied`;
  - the same command from a viewer gives 0 requests;
  - `!modno` from a moderator discards the proposal;
  - expiry at 300 s gives 1 `expired` fact;
  - `max_pending: 2` makes the third proposal evict the one closest to
    expiry;
  - `auto_apply: [delete_message]` applies at once under the strict rules.
  - Concurrency: with the blocking scripted service and
    `max_actions_per_window: 1`, a `!modok` for proposal A and an
    `auto_apply` request launched together give exactly 1 service request
    and 1 `rate_limited` fact; two `!modok <id>` for the same proposal
    launched together give 1 request (the proposal is removed from the
    table under the channel lock before the application, so the second
    approval finds an unknown id).
  - AC28: the override `twitch/c2: act` leaves `twitch/c1` in `alert`.
  - Across every AC24–AC28 case in the file, `len(moderation.decision
    facts) == requests + approvals + rejections + expiries` (asserted by a
    shared fixture counter).

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`, which the proxy and the
two-process topology tests import — a `python3 -m pytest` run fails at collection there.
Every acceptance criterion this step owns must be enforced by a test that fails without the
change. Never weaken, skip, xfail or delete an existing assertion to make the suite pass; if an
existing test encodes a superseded guarantee, replace it and cite the superseding requirement in
the test docstring. Do not use positive-duration sleeps in tests — inject the clock, the RNG and
the transports.

## Risks

- A command naming an id from another channel must not apply it: the
    channel check is part of the lookup key.
  - A double approval of one proposal could apply it twice. Mitigation:
    pop the proposal from the table in the same locked section that
    reserves the slot.
  - Approval-time strict rules use the state **at approval**, not at
    proposal. The message may have left the context, giving
    `target_unknown`.

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
