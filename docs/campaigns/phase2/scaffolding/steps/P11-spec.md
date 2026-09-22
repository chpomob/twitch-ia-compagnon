# Step P11 — `modules/audio_input`

Plan step `P11` of the approved Phase 2 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase2/plan.md`
- Approved specification v1.2 (requirement and acceptance-criteria wording): `docs/campaigns/phase2/spec.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

With `transcription.enabled`, after storing, the module reads the clock once and computes the three named quantities of decision 1: `transcription_reserve = 1 s` (R3's `skipped:deadline` constant, the only literal; the second deadline subtraction AC41 authorizes), `transcription_edge = expiry − transcription_reserve`, `transcription_window = transcription_edge − now`. If `transcription_window ≤ 0` (the window is already exhausted at entry, including exactly 1 s left) → transcribe nothing: `transcription_status: "skipped:deadline"`, 0 requests, the observation still `success` with its `audio_ref`; else exactly one multipart POST to `endpoint` carrying the WAV file, `model` and `language` when set (bearer header when `api_key` is set), bounded on the sleeper by `min(timeout_seconds, transcription_window)` — the same 1 s that refuses to *start* a request also closes a *running* one (round 2, P1; no other constant); a 2xx JSON `{text}` → `audio_ref.transcription = {text (cut to max_chars), transcribed_at (clock), provider_id: "audio-input-stt", truncated}` and status `"ok"`; transport exception → `"failed:stt_unavailable"`, non-2xx → `"failed:stt_failed"`, no answer → `"failed:stt_timed_out"`, 2xx without a string `text` → `"failed:invalid_transcription"`; every failure leaves the observation `success` with its `audio_ref` and no `transcription`; `"unavailable"` when the prepare-time probe failed. **Deadline boundary during transcription (round 2 P1; round 3 V1, V4):** once `attachments.put` has returned, the capture is complete — the segment is leased to the run and the observation carries its `audio_ref` whatever happens to the transcription (R3) — so from that point the module authors no interruption record and never releases the segment itself (the run's cleanup does). The window exists for one reason only: phase 1 adopts a `success` only when its completion stamp is `< expires_at` (AC40), `core/actions.py::_observe` stamps the instant the invoke *actually* returns (its `finally`, in the same task step as the return), and the module must wait for the optional transcription before it can author the capture's `success` record — so it must return before `expiry`, never at it. **Ordering, fixed:** *attempt* (the single request and its bound) → *decide* → *author*. The decision happens at the wake, whatever woke the module — the transport's answer, the bound, `drain()` or a `CancelledError` — and is made on the clock the module reads at that instant, not on which event won: `now < transcription_edge` with an answer in hand → the answer's status (`ok`, `failed:stt_failed`, `failed:invalid_transcription`; a transport exception → `failed:stt_unavailable`); otherwise the request is abandoned → `failed:stt_timed_out`, and a text that arrives at or after the edge is not adopted. The record is then authored — `success`, the `audio_ref`, `transcription` when adopted, the status — and returned with **no further await**, so the executor's stamp is the module's decision instant on the injected clock: at the edge `expiry − 1 s` when the bound fires on time, later when the wake is delayed, but `< expiry` for any wake delayed by less than the reserve, because the module's last scheduled wake is the edge and it never waits on anything scheduled at or after `expiry`. The stamp is therefore strictly `< expires_at` and the executor adopts the record through the phase 1 in-time rule (a `success` stamped before `expires_at` is adopted however late the executor resumes — `test_a_confirmation_landing_in_time_survives_a_late_adoption`); R10's late-interruption adoption is not involved on this path — it governs the recorder half (P10) and could not help here, since a late `success` is never adopted and a `timeout`/`error` record would lose the capture R3 requires. A wake delayed by the whole reserve (a loop stalled for a second) is outside any module arithmetic — the stamp is the executor's — and is the limit the README records beside the executor-cancellation limit (decision 1). The three triggers that can abandon a pending request all end the same way — `success` with the `audio_ref`, `transcription` absent, `transcription_status: "failed:stt_timed_out"` (no answer within the time the request was allowed): (i) the bound fires, whether `timeout_seconds` or the window closing; (ii) `drain(deadline_seconds)` — a call in its transcription phase is not cancelled and kills nothing, its request may complete within `min(its bound, the drain deadline)` on the clock and is abandoned when the drain deadline passes first; (iii) a `CancelledError` reaching the invoke after storing — caught, the request abandoned, the `success` returned normally (only a `CancelledError` *before* storing ends `cancelled`, P10). Which outcome wins at the boundary is thus fixed: the stored capture always does, and `expiry` itself is reached only by a call still recording (P10, `capture_timed_out` under R10). The window bounds the transcription request only: the recorder's kill stays at `expiry` exactly (AC39; 0 kills at `t = 99.0` for a recorder still running with a `t = 100.0` deadline) and no other action — `audio.speak`, `audio.play`, the scene and poll paths — subtracts anything from its deadline (AC41). `prepare` adds, when enabled, one probe request with the generated 0.25 s silent segment (8 044 bytes) bounded by `timeout_seconds`: failure degrades only the transcription capability — `audio.capture` stays bound, the module stays ready, one `module.degraded` with the value-free reason `"transcription unavailable"` and an empty `capabilities` list (decision 11); every later capture reports `"unavailable"` and sends 0 transcription requests; with `required: true` a failed transcription probe or an unusable source fails prepare naming `audio_input` and `transcription.endpoint` / `sources.<name>`. The multipart body is streamed from memory; the transport never sees a path.

## Requirements

Plan mapping: R3, R8, R10
Acceptance criteria owned by this step: AC11, AC12, AC29, AC41, AC42
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

[`modules/audio_input/__init__.py`, `tests/test_audio_input.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P10]

All dependencies are already merged into `main` when this step runs.

## Tests

`tests/test_audio_input.py`: AC11 — a scripted transport answering `{"text": "bonjour à tous"}` → `transcription == {text, transcribed_at, provider_id, truncated: false}`, status `"ok"`, exactly 1 request carrying the 96 044-byte WAV and `model` (and `language` when set); a 2 500-character answer with `max_chars: 2000` → 2 000 characters, `truncated: true`; transport exception, HTTP 500, no answer within `timeout_seconds` on the clock, 2xx without `text` → `success` with the `audio_ref`, no `transcription`, the four statuses; 0.5 s left after storing → no request, `"skipped:deadline"`. Boundary (round 2 P1; AC42) — through the real executor with the call deadline at `t = 100.0` (`expiry == 100.0`): a capture whose segment is stored at `t = 95.0` and a held transport (`timeout_seconds: 10`): **(a) the window edge** — at `t = 98.99` the call is still running with exactly 1 request and 0 records; at `t = 99.0` (`expiry − 1 s`, the clock driven exactly to the edge) the request is abandoned and the terminal observation is `success` with the `audio_ref` (`size == 96 044`), `transcription` absent, `transcription_status == "failed:stt_timed_out"`, `provider_completed_at == 99.0 < expires_at`, `action.completed` carrying `status: success`, exactly one record for the call, `COUNTER_ACTION_TIMEOUTS == 0`, 0 kills, the attachment still leased to the run; the transport's answer then released at `t = 99.5` changes nothing (still one record, no `transcription`); **(b) the transcription finishes late — a delayed wake** — the same setup with the clock advanced from `t = 98.0` to `t = 99.6` in one step (the bound parked at `99.0` is released with the clock already at `99.6`) and the transport's answer released at `t = 99.6` as well, in either registration order: the terminal observation is the same `success` with the `audio_ref`, `transcription` absent, `"failed:stt_timed_out"` (the late text is not adopted — the decision is on the clock, not on the winning future), `provider_completed_at == 99.6 < 100.0`, adopted as `success`, one record, `COUNTER_ACTION_TIMEOUTS == 0`, 0 kills; the same with the executor's adoption parked until `t = 100.5` (scripted sleeper) is still `success` (the phase 1 in-time rule, never the R10 path); **exhausted at entry** — a segment stored at `t = 99.5` and one stored at `t = 99.0` (window `≤ 0`, the exactly-1-s case included) → `"skipped:deadline"` with 0 requests, still `success` with the `audio_ref`; a drain started at `t = 96.0` with `deadline_seconds: 2` and the transport released at `t = 97.0` → `success` with `transcription_status: "ok"`; the transport still held at `t = 98.0` → `success`, `"failed:stt_timed_out"`, the attachment present, the drain completed within its deadline, 0 kills; a direct invocation of the provider coroutine cancelled at `t = 96.0` (after storing) → `success` with the `audio_ref` and `"failed:stt_timed_out"`, 0 kills, 1 attachment — against the same cancellation during recording → `cancelled`, 0 attachments, 1 kill (P10). AC29 — a raising scripted transport (and once the real factory against `http://127.0.0.1:1`, bounded on the clock through the sleeper seam) → 1 `module.degraded` with a reason containing no URL, port, path or key, the action still bound and later calls reporting `"unavailable"`; `required: true` → prepare raises naming `audio_input` and `transcription.endpoint`, 0 transports left open (session `close` counted); the module's spawn count outside calls stays 0.

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`, which the proxy and the
two-process topology tests import — a `python3 -m pytest` run fails at collection there.
Every acceptance criterion this step owns must be enforced by a test that fails without the
change. Never weaken, skip, xfail or delete an existing assertion to make the suite pass; if an
existing test encodes a superseded guarantee, replace it and cite the superseding requirement in
the test docstring. Do not use positive-duration sleeps in tests — inject the clock, the RNG and
the transports.

## Risks

Non-trivial (five failure kinds; deadline arithmetic). The probe segment is generated, never read from disk; after storing, no trigger — bound, drain or `CancelledError` — may turn the record into `cancelled`/`timeout`/`error` or release the segment (R3: the capture stays `success`; round 2, P1); the window check and the request bound use the single 1-s reserve, named `transcription_reserve` for R3's `skipped:deadline`, matching none of the AC41 regex and being the second of the two subtractions AC41 authorizes; the decision at the wake must read the clock (an answer observed at or after the edge is not adopted) and the record must be returned with no await after it, or the stamp drifts past the decision instant; the `CancelledError` handler returns the `success` without awaiting anything the cancellation could interrupt again (the transport call is abandoned, not awaited).

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
