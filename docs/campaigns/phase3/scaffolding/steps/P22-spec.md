# Step P22 — `presence.yaml.example`, the presence-pack scenario and `--check-config` coverage (R1, R8; AC2-check-config, AC6, AC30-check-config)

Plan step `P22` of the approved Phase 3 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase3/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase3/spec.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

- **Enabled modules.** twitch (with `notices.kinds`), chat_context,
    users, audio_output, stream_control, clips, viewer_memory, moderation
    (`mode: alert`), watch (`activation: command`) and brain.
  - **Brain.** A `persona` and the routes from the spec's list, each with
    its safe audience or notice-only kinds:
    - mention replies;
    - `!missed` summary;
    - translation;
    - thanks on notices with a jingle (`audio.play`);
    - broadcaster `!soon`/`!brb`/`!end` scene routes;
    - moderator `!clip`;
    - broadcaster `!watch`, via the watch module.
  - **Grants.** Explicit grants for every action used under `brain` and
    `brain.watch`: `chat.read`, `chat.write`, `users.*` as used,
    `audio.play`, `audio.speak`, `stream.scene.set`, `stream.clip.create`,
    `memory.recall`, `memory.record`, `moderation.request` and
    `screen.capture` (watch).
  - **Secrets.** Only `${NAME}` references, 0 literal secrets.
  - **Phase 2 profiles.** The three phase 2 profiles are not touched.
  - **`tests/test_presence_pack.py`** starts the profile through the same
    `_activate_example` path as `tests/test_examples.py`, with a scripted
    model and fake transports.
  - **`tests/test_profiles.py`** adds the `--check-config` checks.

## Requirements

Plan mapping: R1, R8
Acceptance criteria owned by this step: AC2, AC6, AC30
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

[presence.yaml.example, tests/test_presence_pack.py, tests/test_profiles.py]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P5, P7, P9, P13, P15, P17]

All dependencies are already merged into `main` when this step runs.

## Tests

- AC6: every observation row, from raid through the broadcaster's
    `!watch`, gives 1 `watch.state active`. The profile has 0 literal
    credentials: the hygiene `SECRET_SETTING` regex over the file is empty,
    and every credential path is a `${…}`.
  - `--check-config presence.yaml.example` exits 0 and opens no socket.
  - AC2 check-config half: temporary variants with a 2001-character
    persona, 17 routes, a duplicate route name and a 33-character command
    each exit 2 naming the field.
  - AC30 check-config half: the five invalid watch variants each exit 2
    naming the field.

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`, which the proxy and the
two-process topology tests import — a `python3 -m pytest` run fails at collection there.
Every acceptance criterion this step owns must be enforced by a test that fails without the
change. Never weaken, skip, xfail or delete an existing assertion to make the suite pass; if an
existing test encodes a superseded guarantee, replace it and cite the superseding requirement in
the test docstring. Do not use positive-duration sleeps in tests — inject the clock, the RNG and
the transports.

## Risks

- A new `*.example` can ripple into tests that iterate profiles
    (`_environment()` in `tests/test_integration.py` resolves each
    variable). Mitigation: the presence scenario supplies its own
    environment. If a test that globs profiles breaks, fix only its fixture
    and flag it (the phase 2 precedent).
  - AC42 forbids touching the phase 2 profiles. The P26 gate diffs them.

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
