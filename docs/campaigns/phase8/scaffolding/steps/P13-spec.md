# Step P13 — The listen pipeline

Plan step `P13` of the approved Phase 8 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase8/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase8/spec.md`
- Brief with the operator's binding decisions: `docs/campaigns/phase8/brief.md`
- Approved v2 design (rationale, and the trust model the guard implements): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

- Detectors behind one interface `await detector.wait() -> Verdict | None` (raises
    `DetectorFailure`): `CommandDetector` runs `argv` through the existing `_subprocess_runner` seam
    with stdin closed/empty and **no deadline**; exit 0 with trimmed stdout ≤ 64 chars = detected,
    exit 1 = no verdict, anything else (incl. signal, > 64 chars, start failure) = failure.
    `HttpDetector` sends one request (no audio, optional `api_key` header) through a new
    `_detector_session` seam with no request timeout; only `{"detected": true, "phrase": str≤64}`
    or `{"detected": false}` are verdicts, everything else (status ≠ 200, bad JSON, refused
    connection) is a failure; exceptions are mapped to fixed codes, never stringified.
  - One iteration, strictly in order: (1) await the detector; (2) on detected, build the candidate
    (schema-2 `speech`, `metadata.source` = this input, `payload.wake_word = {detected: true,
    phrase}`, text = phrase, author `voice:<speaker>`, `message_id` `voice:<speaker>:<uuid4 hex>`,
    destination) and `await context.triggers.evaluate_async(candidate)`; trace via
    `decision_trace_payload`; the candidate is never published; (3) only if accepted, invoke the
    named source once via the existing `CommandSource`/`CommandRecording` path
    (`modules/audio_input/__init__.py:723`) with `AUDIO_INPUT_PRE_ROLL_SECONDS` and
    `AUDIO_INPUT_MAX_SECONDS` in its environment; validate the WAV with `wav_data_layout` (`:561`):
    duration by header ≤ `listen.max_seconds` and size ≤ `max_bytes`, else a counted source failure
    (no trim/pad); transcribe once through the existing transcription settings (`_Transcription`,
    `:486`); (4) on a non-empty transcription publish one schema-2 `speech` event (text =
    transcription, no `payload.wake_word`, candidate's author/`message_id`/destination/source) and
    admit it through the watch-style scheduler resolution **without** calling the engine. The WAV
    bytes are held in a local variable and released after transcription; nothing is written to the
    attachment store or disk. Counts: `detector_failures`, `source_failures`,
    `transcription_errors`, `empty_transcriptions`, `candidates`, `published`.

## Requirements

Plan mapping: R7
Acceptance criteria owned by this step: AC26, AC27, AC28
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

[P12]

All dependencies are already merged into `main` when this step runs.

## Tests

With fake runner/detector/source/transcription: AC26 (keyword `nova` → candidate
  accepted with source 0× and transcription 0× at decision time; then source 1× with
  `AUDIO_INPUT_PRE_ROLL_SECONDS=2`, transcription 1×, 1 `speech` event with the transcription,
  author, candidate's `message_id`, destination, 0 further rule evaluations and 0 hook invocations;
  `banana` → `keyword_no_match`, 0/0/0; transcription failure and empty → 0 events + count; WAV over
  `max_bytes` → counted, 0 transcriptions; *observed bounds* with the marker-tone fake source:
  `max_seconds: 8` env and ≤ 8 s header, 8.5 s WAV → source failure, pre-roll 0 → offset 0, late
  verdict with pre-roll 2 → marker at 0.5 s and phrase inside, pre-roll 1 → no marker; held
  transcription → detector invoked exactly once until first publish, then 3 events in detection
  order, 4 invocations, 0 after `stop_inputs` — the last two need P14's loop and are completed
  there); AC27 detector rows (command exit 0 `hey nova` → 1 candidate `accepted:wake_word_match`;
  exit 1, exit 2, 65-char → 0 candidates, source 0×, the last two counted; HTTP detected → 1
  accepted; `{"detected": "yes"}` and 500 → 0, counted; command stdin empty; HTTP request carries no
  audio; 0 hook invocations for published events); AC28 (5 accepted: attachment store 0 entries, 0
  files written (a `tmp_path` cwd scan), no bus-history/audit record/candidate/event contains WAV
  bytes or their base64, detector got no audio, transcription got each of the 5 WAVs once).

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

Calling the engine on the transcribed event would double-decide and expose it to the
  hook — the publish path has no reference to the engine (asserted by AC26's 0 evaluations).
  Reading the WAV duration from the header must reject a header that lies about data length
  (`wav_data_layout` already validates). The HTTP detector with no timeout can hold a connection
  forever; that is the spec's intent (R7) and `stop_inputs` (P14) is its only bound.

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
