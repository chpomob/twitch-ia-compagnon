# Step P12 — `audio_input` becomes an input

Plan step `P12` of the approved Phase 8 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase8/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase8/spec.md`
- Brief with the operator's binding decisions: `docs/campaigns/phase8/brief.md`
- Approved v2 design (rationale, and the trust model the guard implements): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

- Manifest: `produces: [channel.chat.message]`, `lifecycle.roles: [input]`, settings `listen`
    (`enabled` default false, `source`, `pre_roll_seconds` 0–5 default 2, `speaker` default
    `streamer`, `destination`, `retry_seconds` 1–300 default 5, `max_seconds` integer default 10)
    and `wake_word.detector` (`{kind: command, argv}` | `{kind: http, endpoint, api_key?}`, `api_key`
    a credential); `triggers.fields: {wake_word.detected: boolean, wake_word.phrase: string}`; types
    generic `keyword`, generic `event_kind` narrowed to `[speech]`, hooked `wake_word` (kinds
    `[speech]`, empty schema, `hook: true`, `hook_timeout_seconds: 2`); default policy
    `wake_word{}`. `audio.capture` declaration untouched.
  - `validate_settings` (`modules/audio_input/__init__.py:295`) gains, only when `listen.enabled` is
    true: `wake_word.detector` required (diagnostic names `wake_word.detector`), `listen.source`
    names a configured `command` source, `listen.max_seconds` integer in 1..module `max_seconds`,
    `listen.pre_roll_seconds` in 0–5 and `< listen.max_seconds`, `destination` valid; diagnostics name
    the key, echo no value. With `listen` absent/disabled nothing new is checked.
  - `async def _wake_word_hook(view, parameters)`: `True` iff `payload.wake_word.detected is True`,
    `False` iff it is `False`, raises otherwise; reads nothing else. `prepare` (`:1118`) supplies it
    via `context.supply_trigger_hook("wake_word", ...)` when listening is enabled and registers the
    listen loop's supervised-task owner; it starts no process.

## Requirements

Plan mapping: R7
Acceptance criteria owned by this step: AC25, AC27, AC26, AC27
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

[`modules/audio_input/module.yaml`, `modules/audio_input/__init__.py`, `tests/test_audio_input.py`, `tests/test_examples.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P11]

All dependencies are already merged into `main` when this step runs.

## Tests

AC25 (listen absent / `enabled: false`: 0 detector or source invocations over 60 s of
  `ManualClock`; existing `audio.capture` tests pass unmodified); AC27 hook rows (a candidate with
  `{detected: false}` → `rejected:wake_word_no_match`, without `wake_word` →
  `rejected:wake_word_hook_error`, a never-returning replacement hook → `hook_timeout` after 2 s, no
  hook → `hook_unavailable` and 0 runs admitted; no `wake_word.detector` → diagnostic naming it, 0
  processes); AC26 diagnostic rows (`max_seconds: 0`, above module max, `pre_roll_seconds: 6`,
  `pre_roll ≥ max` → diagnostics naming the key, 0 processes). Allowlisted:
  `test_audio_input.py::test_manifest_declares_an_on_demand_read_with_no_role` (produces, role,
  settings keys; `audio.capture` assertions stay exact) and the `audio_input` declaration keys in
  `test_examples.py`.

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

Taking the `input` role makes the coordinator call `start_inputs`/`stop_inputs` on the
  module; with listening disabled both must be no-ops so phase-7 behaviour (AC25, AC16) is unchanged.
  `produces: [channel.chat.message]` must not trigger the ownership guard: `speech` belongs to no
  family (P2), verified by a test publishing a `speech` event through the module's owned bus.

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
