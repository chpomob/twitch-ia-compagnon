# Step P16 — `modules/watch`

Plan step `P16` of the approved Phase 3 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase3/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase3/spec.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

- **Manifest v2.** Lifecycle role `input`. The module consumes
    `channel.chat.message` and declares the `event_kind` trigger type with
    default policy `event_kind: [watch_tick]`. No action, no grant.
  - **Settings.**
    - `channels` (1–4 entries of `platform/channel_id`);
    - `interval_seconds` (15–3600, 60);
    - `max_ticks_per_hour` (1–240, 30, and ≤ `3600 // interval_seconds`,
      a validator cross-check);
    - `max_active_seconds` (60–14400, 3600);
    - `activation` (`command` default | `startup`);
    - `start_command` (`!watch`), `stop_command` (`!unwatch`);
    - `command_audience` (`broadcaster` | `moderators`);
    - `prompt_text` (≤ 500).
  - **Session state per channel.**
    - Start on an exact `start_command` from a `kind == message` event with
      a trusted role of `command_audience`, or at `start_inputs` when
      `activation: startup`.
    - Stop on `stop_command` from that audience, at `max_active_seconds`,
      or at `stop_inputs`.
    - Each change publishes `watch.state {state, reason}`.
  - **Tick scheduler.** A supervised task on the injected clock emits a
    tick every `interval_seconds` while the session is active. A sliding
    one-hour deque enforces `max_ticks_per_hour`; a tick over the cap is
    skipped and counted.
  - **Hand-off.** In this step, ticks go to an internal emit hook. P17
    wires them to triggers and admission.
  - **Packaging.** Add the pyproject line and the running catalog counts.

## Requirements

Plan mapping: R6
Acceptance criteria owned by this step: AC29, AC30, AC31
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

[modules/watch/__init__.py, modules/watch/module.yaml, pyproject.toml, tests/test_watch.py, tests/test_examples.py, tests/test_profiles.py]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P2, P4]

All dependencies are already merged into `main` when this step runs.

## Tests

- AC29:
    - unset activation gives 0 ticks over 600 s;
    - the broadcaster's `!watch` gives 10 ticks over 600 s at 60 s;
    - a viewer's `!watch` starts nothing;
    - `!unwatch` gives 0 further ticks;
    - `startup` ticks without a command.
  - AC30: `validate_settings` rejects `interval_seconds: 14`,
    `max_ticks_per_hour: 241`, `100` with 60 s, `max_active_seconds: 59` and
    5 channels, each naming the field. The `--check-config` exit 2 runs in
    P22.
  - AC31: with 15 s and cap 10, at most 10 ticks in any 3600 s window
    (checked by sliding over the tick stamps); `max_active_seconds: 600`
    gives no tick after t0 + 600 and `inactive max_active`.

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`, which the proxy and the
two-process topology tests import — a `python3 -m pytest` run fails at collection there.
Every acceptance criterion this step owns must be enforced by a test that fails without the
change. Never weaken, skip, xfail or delete an existing assertion to make the suite pass; if an
existing test encodes a superseded guarantee, replace it and cite the superseding requirement in
the test docstring. Do not use positive-duration sleeps in tests — inject the clock, the RNG and
the transports.

## Risks

a tick scheduled exactly at the cap boundary is the classic
  off-by-one. Mitigation: define the window as `(t − 3600, t]` and test the
  boundary tick. Tests use `ManualClock` only, with no positive sleep.

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
