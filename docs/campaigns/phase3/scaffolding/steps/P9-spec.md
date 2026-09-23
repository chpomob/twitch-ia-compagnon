# Step P9 — `modules/clips`

Plan step `P9` of the approved Phase 3 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase3/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase3/spec.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

- **Manifest v2.** Declares `stream.clip.create`: version 1, write,
    permission `stream.clip`, destinations `*/*/clip`, delivery
    `text_argument: none`, timeout 20, idempotency `none`, argument schema
    `{}`, no `model_proposable`.
  - **Settings and validator.** `min_interval_seconds` (5–3600, default
    30), `max_waiters` (1–64, default 4), `required` (a strict bool, so the
    validator rejects `"yes"`). No grant.
  - **`prepare`.** Resolves `clip` services per enabled platform through
    `services.entries()`. A platform without one is unbound with reason
    `platform_unsupported`. If no platform has one: `required: true` → fail
    naming `clips` and `platform_unsupported`; otherwise every platform is
    unbound and startup continues.
  - **Call path, per channel:**
    1. Admission: waiter count > `max_waiters` → `refused resource_busy`.
    2. Take the per-channel lock.
    3. Cooldown against the last create-request instant →
       `refused cooldown` with 0 requests.
    4. `mark_emitted`, stamp the cooldown, and send one create.
    5. Classify per the R2 taxonomy.
    6. On acceptance, confirm by lookups per decision 8 until
       `acceptance + 15 s`:
       - found → `success {clip_id, url, confirmed_at}`;
       - window elapsed → `error clip_not_created`;
       - the lost-response and server-error paths →
         `external_unknown cause confirmation_lost`.
  - **Traces.** The `action.completed` trace carries the status, the code
    and `clip_id`.
  - **Packaging.** Add the `modules.clips` pyproject line. Update the
    allowlisted catalog assertions to the running values (decision 11).

## Requirements

Plan mapping: R2
Acceptance criteria owned by this step: AC8, AC9, AC10, AC11, AC12
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

[modules/clips/__init__.py, modules/clips/module.yaml, pyproject.toml, tests/test_clips.py, tests/test_examples.py, tests/test_profiles.py]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P3, P4, P8]

All dependencies are already merged into `main` when this step runs.

## Tests

(`tests/test_clips.py`, fake platform with `ScriptedClipService`
  and `ManualClock`)
  - AC8: a success after exactly 1 create; with no rule, `refused` and 0
    requests.
  - AC9: a call at 29 s is `refused cooldown` with 0 requests; a call at
    30 s sends 1.
  - AC10: every row of the spec's table, each asserting ≤ 1 create:
    - the call deadline at 12 s ends `external_unknown`;
    - a deadline before sending ends `timeout` with 0 requests.
  - AC11: 5 concurrent calls give 1 send and 4 `refused cooldown`; a sixth
    concurrent call gives `resource_busy`; there is exactly 1 create in
    total, and the overlap flag is never set.
  - AC12 without Kick: the twitch binding is ready for `twitch/*/clip`, and
    the success trace has the `clip_id` and 0 credential values. With the
    fake platform set to `services: [poll]` as the only platform:
    `required: true` fails naming `clips` and `platform_unsupported`, unset
    leaves it unbound and startup completes, and `"yes"` is rejected. The
    Kick-named half of AC12 runs in P19.

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`, which the proxy and the
two-process topology tests import — a `python3 -m pytest` run fails at collection there.
Every acceptance criterion this step owns must be enforced by a test that fails without the
change. Never weaken, skip, xfail or delete an existing assertion to make the suite pass; if an
existing test encodes a superseded guarantee, replace it and cite the superseding requirement in
the test docstring. Do not use positive-duration sleeps in tests — inject the clock, the RNG and
the transports.

## Risks

- Waiters released after the first call must re-check the cooldown
    **after** acquiring the lock, or AC11 would send a second request.
  - A lookup loop driven by the injected sleeper must not hold the lock past
    the deadline, or cancellation would leak waiters.
  - The drain must end in-flight calls in a `finally`. Per the coordinator
    note, a drain using its whole budget is reported timed out: assert the
    outcome, not status 0.

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
