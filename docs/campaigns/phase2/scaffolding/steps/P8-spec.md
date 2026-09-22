# Step P8 — `modules/audio_output`

Plan step `P8` of the approved Phase 2 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase2/plan.md`
- Approved specification v1.2 (requirement and acceptance-criteria wording): `docs/campaigns/phase2/spec.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

Manifest v2 `name: audio_output`, `runtime_api: 2`, `produces: []`, `consumes: []`, `middleware: false`, `lifecycle.roles: []`; actions `audio.speak` (write, `required_permissions: [audio.speak]`, destinations `*/*/audio`, `delivery: {text_argument: text}`, args `text`, optional `voice`, `output`; result `{output, voice, duration_ms, played_ms, playback}`; description stating that `played_ms` is the accepted-bytes estimate; `timeout_seconds: 40`, `idempotency: none`) and `audio.play` (write, `[audio.play]`, `*/*/audio`, `delivery: {text_argument: none}`, args `sound`, optional `output`; result `{output, sound, duration_ms, played_ms, playback}`; `timeout_seconds: 30`); `settings_schema` for `synthesis {endpoint, model, api_key, timeout_seconds: 10, probe: true, probe_text: "ready"}`, `voices {allowed[], default}`, `outputs {<name>: {player: {argv[]}}}`, `default_output`, `clips {<name>: {path}}`, `max_text_chars: 400`, `max_speech_seconds: 20`, `max_audio_bytes: 5242880`, `stop_grace_seconds: 1`, `max_waiters: 1`, `required: false`, accepted `limits` — and **no** lead/margin key (AC41); `credentials: [synthesis.api_key]`; `settings_validator: validate_settings` (value-free diagnostics naming each field; `voices.default ∈ allowed`, `default_output ∈ outputs`, every argv non-empty). `__init__.py`: `validate_settings`, `_Settings`, `activate(context, settings, catalog)` reading the seams `_synthesis_transport`, `_player_runner`, `_sleeper` from settings (defaults: an `aiohttp` session factory, the real asyncio-subprocess runner of decision 2, `asyncio.sleep`), `AudioOutputModule` with the two providers; `parse_wav(data) → WavInfo(sample_rate, channels, bits, data_offset, data_bytes)` — `data_offset` the byte offset of the `data` chunk's payload (44 for a canonical header, more when a `LIST` chunk precedes `data`) — refusing a non-RIFF/WAVE, non-PCM or empty-data body (`invalid_audio`); `duration_ms = data_bytes × 1000 // (rate × channels × bytes_per_sample)`. `audio.speak`: `mark_not_emitted()` at entry; argument checks in order — `text` 1..`max_text_chars` else `invalid_arguments`, `voice ∉ allowed` → `refused voice_not_allowed`, `output ∉ outputs` → `refused output_not_allowed` (provider uninvoked in both); one POST `{model, voice, input, response_format: "wav"}` with the bearer header when `api_key` is set, bounded by `timeout_seconds` on the sleeper — transport exception → `error tts_unavailable`, non-2xx → `error tts_failed` whose message carries the status code and never the body, no answer → `timeout` cause `synthesis` (own observation, 0 player starts), body unparseable/empty → `invalid_audio`, body > `max_audio_bytes` → `audio_too_large`, duration > `max_speech_seconds` → `speech_too_long`; then playback. `audio.play`: `sound ∉ clips` → `error invalid_arguments`; clip unreadable at call time → `error clip_unreadable`; the clip bytes parsed and bounded the same way. Playback (both): `start(argv)` — not startable → `error playback_failed` with `played_ms: 0`; bounded `write`s (32 KiB) of the **complete WAV body — header bytes first, then the data chunk, every byte** (finding P2: R1 "the segment bytes are fed to the player's standard input", AC1 "received all 48 044 bytes"); `close_stdin`, `wait` → exit 0 after every byte accepted → `success {…, playback: "completed", played_ms == duration_ms}`; non-zero exit → `error playback_failed` with the accepted-bytes estimate. The runner reports the running total it accepted; `accepted_data_bytes = min(data_bytes, max(0, accepted_total − data_offset))` is the only quantity `played_ms` derives from (P9), so the header never counts as played audio. `prepare` in this step binds both actions with `probe: false` semantics (P9 replaces it with the real readiness rule). Add `"modules.audio_output" = ["module.yaml"]` to `pyproject.toml` (the `tests/test_main.py` guard requires it as soon as the directory exists).

## Requirements

Plan mapping: R1, R9
Acceptance criteria owned by this step: AC1, AC2
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

[`modules/audio_output/__init__.py`, `modules/audio_output/module.yaml`, `pyproject.toml`, `tests/test_audio_output.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P5]

All dependencies are already merged into `main` when this step runs.

## Tests

`tests/test_audio_output.py` (created; harness `runtime_context` + an applicable rule): AC1 — a scripted 1.5 s WAV (48 000 data bytes) and a recording runner exiting 0: `success`, `playback: "completed"`, `duration_ms == played_ms == 1500`, exactly 1 synthesis request with `model`, `voice`, `input == "hello"`, `response_format == "wav"`, 1 player start whose `received` buffer equals the synthesis body byte for byte (48 044 bytes, starting `RIFF`); `audio.play {sound: "chime"}` on a 0.5 s clip file → `duration_ms == 500`, the player received the clip's 16 044 bytes, 0 synthesis requests; a body with a `LIST` chunk before `data` is written whole and `parse_wav` reports its larger `data_offset`. AC2 — every failure with its stated effect count (transport exception, 503 with the code in the message and none of the body text, timeout on the clock with cause `synthesis` and 0 starts, 10 non-WAV bytes, 25 s WAV with `max_speech_seconds: 20`, `max_audio_bytes + 1`, exit 1, unknown `sound`, clip removed after prepare, `voice: "other"`, `output: "other"` with 0 requests); a module-wide assertion that no observation's status is `external_unknown`; manifest test: the two declarations, delivery mappings, permissions, destinations, credentials, `validate_settings` per field, and the settings schema declaring no key matching `lead|margin|early_stop` (AC41); the `tests/test_main.py` package-data guard passes.

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`, which the proxy and the
two-process topology tests import — a `python3 -m pytest` run fails at collection there.
Every acceptance criterion this step owns must be enforced by a test that fails without the
change. Never weaken, skip, xfail or delete an existing assertion to make the suite pass; if an
existing test encodes a superseded guarantee, replace it and cite the superseding requirement in
the test docstring. Do not use positive-duration sleeps in tests — inject the clock, the RNG and
the transports.

## Risks

Non-trivial (module boundary: HTTP + subprocess; branching). The synthesis body is never logged; the WAV reader tolerates a `LIST` chunk before `data` and refuses a `data` size larger than the body; the writer loop must read the runner's accepted count after every `write`, so a player that stops reading mid-chunk is measured at the byte, not the chunk.

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
