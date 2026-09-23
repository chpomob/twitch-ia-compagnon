# Step P8 — Twitch publishes the `clip` and `moderation` services (R2, R5, R7-twitch; AC12-twitch service, AC28 platform half)

Plan step `P8` of the approved Phase 3 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase3/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase3/spec.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

Add two service classes in `modules/twitch`, both published
at activation only when `context.services.available`, next to `poll`.
  - **Clip service.** `create(broadcaster)` → `POST helix/clips`, one
    request, never retried. `lookup(clip_id)` → `GET helix/clips?id=`.
    Outcomes are classified into the taxonomy:
    - 202 → `accepted(id, edit_url)`;
    - a 404 or "not live" answer → `offline`;
    - 401/403/400 → `rejected`;
    - 429 → `rate`;
    - 5xx or a lost response → `uncertain`.
  - **Moderation service.**
    - `operations = {delete_message, timeout}`.
    - `delete_message` → `DELETE helix/moderation/chat?message_id=`.
    - `timeout` → `POST helix/moderation/bans` with `duration`. The unit is
      seconds and there is no rounding. A missing or zero duration is
      refused locally, so the ban endpoint is never called without a
      duration: no permanent ban.
    - The outcomes are `ok`, `rejected`, `rate` and `uncertain`.
  - **Credentials.** Both reuse the module's existing credential handling
    and redaction.
  - **`conftest.py`** gains HTTP answer builders for these endpoints on the
    existing scripted transport.

## Requirements

Plan mapping: R2, R5, R7
Acceptance criteria owned by this step: AC12, AC28
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

[modules/twitch/__init__.py, tests/test_twitch.py, tests/conftest.py]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P7]

All dependencies are already merged into `main` when this step runs.

## Tests

- Activation on a context with services publishes `(clip, twitch)` and
    `(moderation, twitch)`. On a default context it publishes nothing and
    succeeds.
  - Each HTTP status maps to its outcome, with 1 request each.
  - A timeout without a duration sends 0 requests.
  - No credential value appears in any trace (a `trace_texts` scan).

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`, which the proxy and the
two-process topology tests import — a `python3 -m pytest` run fails at collection there.
Every acceptance criterion this step owns must be enforced by a test that fails without the
change. Never weaken, skip, xfail or delete an existing assertion to make the suite pass; if an
existing test encodes a superseded guarantee, replace it and cite the superseding requirement in
the test docstring. Do not use positive-duration sleeps in tests — inject the clock, the RNG and
the transports.

## Risks

Twitch's clip creation needs scope `clips:edit`, and moderation
  needs `moderator:manage:chat_messages` and
  `moderator:manage:banned_users`. A token missing a scope surfaces only at
  call time as `rejected`, which is the honest taxonomy. The README (P25)
  lists the scopes.

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
