# Step P5 — Shared test doubles

Plan step `P5` of the approved Phase 2 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase2/plan.md`
- Approved specification v1.2 (requirement and acceptance-criteria wording): `docs/campaigns/phase2/spec.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

Add `silent_wav(seconds=0.25)` and `wav_bytes(seconds, *, sample_rate=16000, channels=1, bits=16, fill=0)` (RIFF/WAVE writer; `silent_wav(0.25)` is exactly 8 044 bytes), `wav_data_size(data)`, `carries_audio_part(body)` (an `input_audio` part in any message content), `probe_kind(body) ∈ {text, image, audio}`; `FakeSession` keeps answering every probe with one valid forced call (the audio probe included, so no phase 1 suite changes) and `probe_calls` stays apart from `post_calls` (AC15). `runtime_context` gains `services: Any = None` and passes `ServiceRegistry()` by default. New doubles, all clock-driven, none sleeping: `ScriptedSpeechTransport` (records each `post` URL/headers/JSON body; answers `FakeBytesResponse(status, body)`, an exception, or `HELD` — released only by `clock.advance` past the timeout through the injected sleeper); `ScriptedTranscriptionTransport` (records multipart fields — file bytes, `model`, `language` — answers JSON/status/exception/held); `RecordingPlayerRunner` — `start(argv)` records `starts` and returns a `FakePlayer(accept_limit, exit_code, startable, ignores_terminate)` where `accept_limit` counts **data** bytes: the player accepts every header byte before the `data` payload (44 for the canonical header `wav_bytes` writes) plus `accept_limit` data bytes, so AC3's "128 000 of the 320 000 data bytes" is `accept_limit=128_000` with `received` holding 128 044 bytes, and `None` accepts everything; `write(process, chunk)` appends to `received` up to the limit, returns the count accepted from that chunk, then parks on a future; `release()` lets a blocked player exit; `stop(process, grace)` records `stops`, then after `grace` on the injected sleeper records `kills` when the player ignores termination and lets `wait` return; `wait` returns the exit code; an ordered `events` log (`start`/`stop`/`kill`/`exit`, per output) for the ownership assertions (finding P5); `FakeAudioSource` (scripted WAV bytes for `read(seconds)`; a `gated` variant emits nothing until `release()` and records `kills`); `RecordingSubprocessRunner` (spawn/kill counts for `audio_input`); `ScriptedSceneProvider` (`scenes`, `current`, `set_answers` ∈ `ok`/`swallow`/`raise`/`unknown`, `read_answers`, `connected`, records `sets`/`reads`, `disconnect()`/`reconnect()`); `ScriptedPollService` (`create` outcomes `ok`, `lost`, `status:<code>`, `transport`, `malformed`, `held`; `get` outcomes `list`, `raise`; `polls` table; records `creates`/`gets`); `trace_texts(bus)` (every emitted trace flattened to text for value-free assertions).

## Requirements

Plan mapping: R1, R3, R4, R6, R7
Acceptance criteria owned by this step: (see plan)
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

[`tests/conftest.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P4]

All dependencies are already merged into `main` when this step runs.

## Tests

Self-checks in the file's existing pattern: `silent_wav(0.25)` is 8 044 bytes starting `RIFF`/`WAVE` with all-zero samples; `wav_bytes(1.5)` has 48 000 data bytes; `carries_audio_part` recognises an `input_audio` part and ignores `image_url`; a `FakePlayer(accept_limit=128_000)` fed a 10 s `wav_bytes` reports 128 044 received; the full existing suite stays green (`FakeSession` behaviour unchanged).

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`, which the proxy and the
two-process topology tests import — a `python3 -m pytest` run fails at collection there.
Every acceptance criterion this step owns must be enforced by a test that fails without the
change. Never weaken, skip, xfail or delete an existing assertion to make the suite pass; if an
existing test encodes a superseded guarantee, replace it and cite the superseding requirement in
the test docstring. Do not use positive-duration sleeps in tests — inject the clock, the RNG and
the transports.

## Risks

Trivial in logic, wide in reach: every suite imports this file, so a name collision with an existing helper or an import of a not-yet-existing module would break the whole run — the doubles import `core` only.

## Constraints

- One atomic commit for the whole step when the suite is green; Conventional Commit message
  (`feat(phase2): ...`, `fix(phase2): ...`, `test(phase2): ...`, `refactor(phase2): ...`).
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
