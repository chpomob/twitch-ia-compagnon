# Step P10 — `modules/audio_input`

Plan step `P10` of the approved Phase 2 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase2/plan.md`
- Approved specification v1.2 (requirement and acceptance-criteria wording): `docs/campaigns/phase2/spec.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

Manifest v2 `name: audio_input`, `produces: []`, `consumes: []`, `middleware: false`, `lifecycle.roles: []`; action `audio.capture` (read, `[audio.capture]`, destinations `*/*/audio`, no `delivery`; args `seconds` (integer), optional `source`; result `{source, content_type, size, duration_ms, sample_rate_hz, channels, captured_at, transcription_status}`; `timeout_seconds: 45`; `idempotency: none`); `settings_schema` `sources {<name>: {kind: command, argv[]} | {kind: file, path}}`, `default_source`, `max_seconds: 30`, `max_bytes: 1048576`, `grace_seconds: 2`, `transcription {enabled: false, endpoint, model, api_key, language, timeout_seconds: 10, max_chars: 2000}`, `required: false`, accepted `limits`, no lead/margin key; `credentials: [transcription.api_key]`; `settings_validator: validate_settings`. `__init__.py` on the `capture` template: `_Settings`, `SourceSpec`, `FileSource` (re-read at each call, truncated to `seconds`) and `CommandSource` (argv started at the call, terminated at `t0 + seconds`, stdout collected incrementally), seams `_source_factory`, `_subprocess_runner`, `_transcription_transport`, `_sleeper`; `activate` refuses a context without `attachments`/`actions`/`clock`. `audio.capture`: `expiry = min(call.deadline, clock() + spec.timeout_seconds)` at entry; `seconds` integer in 1..`max_seconds` else `error invalid_arguments` (before any spawn); `source ∉ sources` → `invalid_arguments`; `seconds > (expiry − now) − grace_seconds` → `error capture_too_long` (0 spawns; `grace_seconds` is this admission reserve and nothing else); acquire; validate the segment — RIFF/WAVE, PCM (format 1), 16-bit, 16 000 Hz, mono — else `error invalid_result` with 0 bytes stored; truncate the data chunk to `seconds × 32 000` bytes and rewrite the header sizes; total > `max_bytes` → `error attachment_refused` with 0 bytes stored; `attachments.put(run_id, data, content_type="audio/wav")`; result plus exactly one `audio_ref` part (`provider_id: "audio-input"`, `captured_at` from the clock, `transcription_status: "disabled"` in this step). **Deadline (decision 1):** a watcher parked on the sleeper until `expiry` — nothing subtracted — kills the recorder (1 kill) and the invoke returns `error capture_timed_out` with 0 bytes stored; a `CancelledError` mid-capture is caught: kill if not yet killed, then `error capture_timed_out` when `clock() >= expiry`, `cancelled` with 0 bytes stored otherwise; `drain()` kills running recorders with status `cancelled`. No task at `start_inputs`/`prepare`, no process outside a call. `prepare` probes each source (`shutil.which(argv[0])` / file readable), binds `audio.capture` when ≥ 1 source is usable, reports `module.degraded` otherwise, applies `required` (P11 adds the transcription probe). Add `"modules.audio_input" = ["module.yaml"]` to `pyproject.toml`.

## Requirements

Plan mapping: R10, R3, R4, R10, R9
Acceptance criteria owned by this step: AC8, AC9, AC10, AC12, AC39, AC41
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

[`modules/audio_input/__init__.py`, `modules/audio_input/module.yaml`, `pyproject.toml`, `tests/test_audio_input.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P2, P3, P5, P8]

All dependencies are already merged into `main` when this step runs.

## Tests

`tests/test_audio_input.py` (created; harness with a shared `AttachmentStore` on the `ManualClock`, through the real executor): AC8 — an injected source producing 5 s of valid WAV (160 044 bytes), `seconds: 3` → `success`, one `audio_ref` with `size == 96 044`, `duration_ms == 3000`, `sample_rate_hz == 16000`, `channels == 1`, `content_type == "audio/wav"`, an attachment of exactly 96 044 bytes leased to the call's run, `captured_at` from the clock, `transcription_status == "disabled"`, stored bytes starting `RIFF`/`WAVE` with a 96 000-byte data chunk. AC9 — 8 000 Hz, stereo, 8-bit, 20 non-WAV bytes → `invalid_result` with 0 attachments; `seconds: 40` under `max_seconds: 30` → `invalid_arguments` with 0 spawns; `max_seconds: 40`, `max_bytes: 1_048_576`, a 40 s source (1 280 044 bytes) → `attachment_refused` with 0 bytes stored; `seconds: 30` → `size == 960_044`. AC10/AC39 module half — 4 s left, `grace_seconds: 2`, `seconds: 3` → `capture_too_long` with 0 spawns; a gated recorder emitting nothing, call deadline `t = 100.0`, `grace_seconds: 2`: 0 kills at `t = 98.0` and at `t = 99.99`, exactly 1 kill at `t = 100.0`, the module's record stamped `>= 100.0` and adopted — terminal observation `error` with `error.code == "capture_timed_out"` (never `timed_out`), 0 attachments, `action.completed` carrying `status: error`, one record for the call; a cancellation mid-capture at `t = 50.0` → `cancelled`, 0 attachments, 1 kill; a drain started at `t = 100.0` → `cancelled`, 1 kill; after the run's terminal record (`store.release(run_id)`) every attachment is gone; a lease outliving a crashed run is reaped by the TTL on the clock. AC12 — the runner records 0 spawns outside `audio.capture` calls, `tasks.active == 0` after `prepare`/`start_inputs`, the manifest declares `produces: []`, `consumes: []`, no lifecycle role; without an applicable rule the call is `refused` with 0 spawns and 0 attachments; the package-data guard passes. AC41 — file-read assertion over `modules/audio_input/` for the lead/margin regex (0 matches).

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`, which the proxy and the
two-process topology tests import — a `python3 -m pytest` run fails at collection there.
Every acceptance criterion this step owns must be enforced by a test that fails without the
change. Never weaken, skip, xfail or delete an existing assertion to make the suite pass; if an
existing test encodes a superseded guarantee, replace it and cite the superseding requirement in
the test docstring. Do not use positive-duration sleeps in tests — inject the clock, the RNG and
the transports.

## Risks

Non-trivial (subprocess boundary; branching; the deadline meeting of decision 1). Truncation must keep the header consistent (RIFF size and `data` size rewritten) or the executor-side size check and the brain's `wav` encoding disagree; `attachments.put` may refuse a segment over the store's own per-run bound — that refusal maps to `attachment_refused` too; the command source reads stdout incrementally so a recorder that streams forever cannot exceed `max_bytes` in memory; the `CancelledError` handler returns the record without re-raising so the executor adopts it.

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
