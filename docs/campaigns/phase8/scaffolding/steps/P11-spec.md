# Step P11 — The `timer` input (R8; AC29, AC30, AC31; allowlisted counts 20→21)

Plan step `P11` of the approved Phase 8 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase8/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase8/spec.md`
- Brief with the operator's binding decisions: `docs/campaigns/phase8/brief.md`
- Approved v2 design (rationale, and the trust model the guard implements): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

- Manifest v2: `produces: [channel.chat.message]`, `lifecycle.roles: [input]`,
    `settings_validator: validate_settings`; settings `channels` (list of `{destination {platform,
    channel_id}, prompt_text}` non-empty text); `triggers.fields: {timer.minute_of_day: integer,
    timer.sequence: integer}`; declared type `interval` (kinds `[timer]`, schema: required
    `every_minutes` integer 1–1440, required `probability` number 0–1, optional `window`
    `{start_minute, end_minute}` integers 0–1439 both required inside; `probability_parameter:
    probability`; rule `{all: [{gte: [{field: timer.minute_of_day}, {param: window.start_minute}]},
    {lte: [{field: timer.minute_of_day}, {param: window.end_minute}]}]}` — no `not`, so the optional
    window is allowed); default policy `interval{every_minutes: 30, probability: 0.25}`.
  - `validate_settings`: channel fields; window start < end is a policy check, so `prepare` checks
    each configured channel's **resolved** policy (`context.triggers.registry.resolve(input,
    channel_id)`): exactly one `interval` rule, `start_minute < end_minute`; a violation reports
    `module.degraded` with a value-free reason naming `triggers.timer.policy...` / the channel index,
    then raises (coordinator precedent). Out-of-range `every_minutes`/`probability` are refused by
    P5's schema validation at load.
  - Loop, modelled on `modules/watch/__init__.py` `_emit_tick`/`_admit` (`:690`): started in
    `start_inputs`, one supervised task per channel sleeping `every_minutes × 60` on the injected
    clock's sleeper; each firing builds the schema-2 `timer` event (text = `prompt_text`,
    `payload.timer = {sequence, minute_of_day}` with `minute_of_day` from an injected local-time
    seam `_local_minute` defaulting to `time.localtime`, author `timer:<channel_id>`, `message_id`
    `timer:<platform>:<channel_id>:<sequence>`, destination), decides it, traces with
    `decision_trace_payload`, admits an accepted one through the watch-style scheduler resolution.
    `stop_inputs` cancels the tasks; nothing fires outside the span.
  - `pyproject.toml`: add `"modules.timer" = ["module.yaml"]`.

## Requirements

Plan mapping: R8
Acceptance criteria owned by this step: AC29, AC30, AC31
Read the exact R/AC text in the specification file before writing code. Requirements not listed
here are other steps' responsibility — do not implement them.

Standing phase-8 decisions that override any contrary reading of a requirement:
- **A module declares its trigger types; the kernel evaluates them.** The four generic types
  (`probability`, `audience`, `keyword`, `event_kind`) stay and remain usable by any input. A declared
  type carries a declarative match rule; no module code runs in the hot path for it.
- **A declared type narrows, never widens, and grants nothing.** Configuration is validated against the
  module's declared schema *and* the kernel's canonical one. A type cannot assert a field its input
  does not emit, and a trigger only decides candidacy: admission, budgets and DEFAULT-DENY are
  untouched and still decide everything after it.
- **The one hook is bounded and fail-closed.** It runs only on events its own module emitted, under a
  hard timeout; raising or exceeding the bound means *no trigger*, never a silent pass, and its outcome
  is recorded like every other decision.
- **Event kinds are appended, never reordered.** `reward` and `manual` join the closed vocabulary at the
  end, the way `cue` did; earlier indices keep their order and meaning.
- **No new runtime dependency.** `aiohttp` and `PyYAML` remain the only ones. The wake-word detector is
  configured (a command or an HTTP endpoint), exactly like an audio source or the transcription
  endpoint; a detector is never embedded and never assumed.
- **Streamer.bot stays optional.** Its `cue` path is unchanged, and nothing in this phase needs it.
- **No continuous listening.** Audio publishes an event only when a trigger or a run asked for it; a
  run that nobody requested must not record audio, and continuous transcription is out of scope.
- **No test opens a socket.** Twitch, the detector, the surfaces and the audio device are faked; the
  real-instance trial stays a documented manual procedure with its own record.
- Close exactly the requirement(s) this step names. If a sibling path shows the same defect, report
  it in your final message instead of widening the step.
- No test is skipped, xfailed, deleted or loosened. The full suite is green at every step:
  `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider` (never the system python: no aiohttp).

## Files

[`modules/timer/module.yaml`, `modules/timer/__init__.py`, `pyproject.toml`, `tests/test_timer.py`, `tests/test_examples.py`, `tests/test_manifest_presentation.py`, `tests/test_profiles.py`, `tests/test_config_ui.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P10]

All dependencies are already merged into `main` when this step runs.

## Tests

`tests/test_timer.py` on `ManualClock` and the recorded random source: AC29 (2
  channels, `every_minutes: 10, probability: 1`, 60 min → 12 events, sequences 1–6 per channel, ids,
  texts, all accepted); AC30 (100 events with 0.25 → 100 draws, accepted = draws < 0.25; window rows
  599/600/660/661; no window at 0 and 1439; the shipped declaration registers with no hook; a
  `{param: window.nope}` fixture refused at load naming the path); AC31 (`every_minutes` 0 and 1441,
  `probability` 1.5, window 700>600, 0 and 2 `interval` rules → diagnostics naming input and field;
  60 min after `prepare` before `start_inputs` → 0 events; 60 min after `stop_inputs` → 0).
  Allowlisted counts 20→21 and `timer` added to the expected module set.

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

Drift: computing the next firing as "now + interval" after a slow admission accumulates
  delay; the loop schedules from `start + k × interval`. Host local time is an injected seam so
  tests never depend on the machine's timezone. `prepare`-time policy checks depend on trigger
  configuration having been applied before `prepare` (loader `_apply_trigger_configuration` runs
  after registration at load — verify ordering against `core/loader.py:1375` before relying on it).

## Constraints

- One atomic commit for the whole step when the suite is green; Conventional Commit message
  (`feat(phase8): ...`, `fix(phase8): ...`, `test(phase8): ...`, `refactor(phase8): ...`, `docs(phase8): ...`).
  The scope MUST be `phase8`: the repository history is full of `feat(phase4):` / `fix(phase3):` commits
  from earlier phases — do NOT copy that habit. Every commit this campaign makes is `(phase8)`.
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
