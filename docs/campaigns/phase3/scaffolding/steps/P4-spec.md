# Step P4 — Fixture platform

Plan step `P4` of the approved Phase 3 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase3/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase3/spec.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

- **`tests/conftest.py`** gains three doubles:
    - `ScriptedClipService`: a scripted queue of create outcomes (`accepted
      <id>`, `offline`, `auth`, `rate`, `lost`, `server_error`, `hold`) and
      lookup outcomes (`found <url>`, `empty`). It keeps a request log with
      clock stamps, counts creates, and records overlap (the in-flight count
      never exceeds 1).
    - `ScriptedModerationService`: `operations` (default `{delete_message,
      timeout}`), scripted outcomes (`ok`, `rejected`, `rate`, `lost`,
      `server_error`), a request log, and the platform's duration rounding
      hook.
    - `memory_directory(tmp_path)`: a helper returning a fresh directory
      plus a function that lists `*.json` names.
  - **Fixture platform.**
    - The manifest declares `event_kind` among its trigger types and the
      optional settings `notices.kinds` and `services` (a list of
      `poll`/`clip`/`moderation`, default all three).
    - The module accepts scripted notices `{kind, author|None, text}`.
      Notices are published as `channel.chat.message` with `payload.kind`
      (decision 2) and fed to the chat context. They are trigger-evaluated
      and admitted like messages, except that an authorless notice is fed
      under `system:anonymous` and never admitted (decision 3).
    - The module refuses (and counts as invalid) any author id containing
      `:`.
    - It publishes the scripted `clip` and `moderation` services for `fake`,
      only when `context.services.available` and only the kinds listed in
      `services`.

## Requirements

Plan mapping: R1, R2, R5, R6
Acceptance criteria owned by this step: AC1, AC3, AC8
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

[tests/fixtures/modules/fakeplatform/__init__.py, tests/fixtures/modules/fakeplatform/module.yaml, tests/conftest.py, tests/test_triggers.py]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P2]

All dependencies are already merged into `main` when this step runs.

## Tests

(`tests/test_triggers.py`, end to end through the fixture
  platform on a `runtime_context`)
  - A scripted `raid` notice under policy `event_kind [raid]` gives 1
    admission and 1 chat-context entry.
  - An authorless `sub_gift` gives 1 chat-context entry and 0 admissions.
  - An author `a:b` gives 0 admissions and invalid counter + 1.
  - With `services: [poll]`, the registry holds only `(poll, fake)`.
  - Every existing fixture-platform test (`tests/test_loader.py`,
    `tests/test_stream_control.py`) still passes: the default publishes
    `poll` as before, and the phase 2 entry equality in
    `tests/test_stream_control.py` uses its own registry fixture (spec caller
    table).
  - `conftest._self_check` covers the new doubles.

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`, which the proxy and the
two-process topology tests import — a `python3 -m pytest` run fails at collection there.
Every acceptance criterion this step owns must be enforced by a test that fails without the
change. Never weaken, skip, xfail or delete an existing assertion to make the suite pass; if an
existing test encodes a superseded guarantee, replace it and cite the superseding requirement in
the test docstring. Do not use positive-duration sleeps in tests — inject the clock, the RNG and
the transports.

## Risks

- Publishing two more services by default could change a phase 2 registry
    equality. Mitigation: grep `entries()` assertions. If one breaks, the
    phase 2 test's profile sets `services: [poll]` explicitly. That test is
    not a target, so prefer a fixture default that keeps the phase 2 shape
    wherever a test constructs the fixture without settings. The step
    records which shape was chosen.
  - `system:anonymous` must be accepted by `ChatEntry`. It is: any non-empty
    text.

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
