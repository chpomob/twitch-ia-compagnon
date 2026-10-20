# Step P14 — Listen lifecycle, restart backoff and two-state health (R7 lifecycle/supervision/restart; AC26 ordering tail, AC37, AC38, AC39)

Plan step `P14` of the approved Phase 8 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase8/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase8/spec.md`
- Brief with the operator's binding decisions: `docs/campaigns/phase8/brief.md`
- Approved v2 design (rationale, and the trust model the guard implements): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

- `start_inputs` starts the loop as a supervised task (registered in `prepare`), whose first act is
    one detector invocation; `stop_inputs` sets a stopping flag, cancels the loop, terminates an
    in-flight detector process / closes the HTTP request, terminates an in-flight source
    (`CommandRecording.kill`, `:845`) discarding partial output, and releases an untranscribed
    segment; no invocation starts afterwards. A startup that never reaches `start_inputs` started
    nothing because `prepare` starts nothing.
  - Restart scheduler on the injected sleeper: after (c) — a declined candidate or a published event
    — wait 1 s and reset `n = 0`; after an unproductive settlement (no-detect, detector failure,
    source failure, transcription error, empty transcription) increment `n` and wait
    `min(retry_seconds × 2^(n−1), 300)`. Only one detector, acquisition, transcription in flight.
  - Health: state `healthy` at start; detector failure in `healthy` → `degraded` + exactly one
    `module.degraded` (`wake_word_detector_failed`, value-free); further failures report nothing;
    the first verdict (detected or exit 1) in `degraded` → `healthy` + exactly one `module.ready`;
    source/transcription outcomes never change the state. A detector terminated by `stop_inputs` is
    not a failure. Health is reported through the module's supervision surface (memory: a degraded set
    in `start_inputs` can be overridden by the coordinator's own `module.ready` — the first report
    can only happen after the first invocation settles, which is after `start_inputs` returns;
    verified by AC37/AC39 ordering).

## Requirements

Plan mapping: R7
Acceptance criteria owned by this step: AC26, AC37, AC38, AC39
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

[`modules/audio_input/__init__.py`, `tests/test_audio_input.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P13]

All dependencies are already merged into `main` when this step runs.

## Tests

AC26 ordering tail (1 invocation until first publish; 3 events in detection order; 4
  invocations; 0 after `stop_inputs`); AC37 (after `prepare` before `start_inputs`: 0 invocations, 0
  processes, 0 candidates, 0 transcriptions, 0 `speech`; after: 1 detector, 0 source; another module's
  failed `prepare` → 0 for `audio_input`; `stop_inputs` terminates an in-flight detector (recorded)
  and an in-flight acquisition, 0 candidates/transcriptions follow, 600 s → 0 invocations); AC38 (exit
  1 every time, 3 600 s → 17 invocations at 0, 5, 15, 35, 75, 155, 315 then every 300 s, 0
  candidates/sources/transcriptions/events; detecting on exactly 4 → 4 sources and 4 transcriptions
  under accepting, 0 under declining; a transcription error → no second transcription and no
  invocation before 5 s); AC39 (the (a)/(b)/(c) timing rows at 4.9/5, 9.9/10, 0.9/1 s; error after
  (c) → 5 s; detector exit 2 and source failure back off alike; 20 failures ≤ 300 s apart and exactly
  1 `module.degraded`; exit 1 from healthy reports none; recovery sequence 3 fail/1 verdict/2 fail →
  degraded, ready, degraded with no duplicates; same with a detected verdict; source/transcription
  failure after the verdict adds none; HTTP connection refused backs off alike; a detector pending
  3 600 s is neither terminated nor counted).

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

The coordinator-drain tie (memory): a `stop_inputs` that waits on process termination
  must resolve its state in `finally` so a coordinator timeout still leaves no process running.
  `module.ready` re-reported by the coordinator after `start_inputs` (memory P14 blocker) could
  break AC39's exact sequence; the step asserts the health stream and stops and flags if the
  coordinator interleaves its own report. Off-by-one in `2^(n−1)` is pinned by AC38's literal times.

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
