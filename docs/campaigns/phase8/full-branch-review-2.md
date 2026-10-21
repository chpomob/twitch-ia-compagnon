# Phase 8 — full-branch closing review, second pass

## VERDICT: REQUEST_CHANGES

**F1 and F2 are RESOLVED. F3 is UNRESOLVED by construction, so the closing verdict remains REQUEST_CHANGES.** Its persistence is the expected outstanding operator decision, not a regression introduced by P17F1.

Reviewed `d4f2504383113036188d9f1fa8b9e2491046e611` (P17F1), comparing the fix with `16d0c5c` and retaining `7782e5f` as the whole-branch baseline. The [first review](full-branch-review.md) supplies the unchanged detailed requirement map, named AC carriers and prior sensitivity evidence. This pass reran the counterexamples, audited the fix and test changes, exercised authorization end-to-end, and ran the entire suite. It did not repeat the first pass's complete AC1–AC40 mutation campaign.

Only this report was created; nothing was staged. The pre-existing untracked `docs/campaigns/phase8/scaffolding/gate-prompt-2.md` was left untouched. Reviewer probes ran from standard input with bytecode disabled; substitutions were in memory only.

## ITEMS SETTLED

### F1 — RESOLVED: hard hook bound

Repeated the original construction: `_wake(hook_timeout_seconds=0.01)`, registry from `_wake_engine`, `TriggerEngine` with `clock=time.monotonic`, normal asyncio sleeper, dedup capacity **10**, TTL **60 s**, fixture RNG/companion identity, and the owning module context supplying the hook. The policy was `none_of`; the hook busy-waited **0.12 s** without yielding, then returned `True`.

| Repetition | Decision elapsed | Independent heartbeat, scheduled for 0.02 s | Hook finished when heartbeat fired? |
|---|---:|---:|---|
| 1 | 0.015563 s | 0.025689 s | No |
| 2 | 0.010416 s | 0.025529 s | No |
| 3 | 0.015438 s | 0.025552 s | No |

All three decisions returned **`accepted=False`, `rejected:wake_hook_timeout`**, versus **0.1202 s** in the first pass. The late answer did not replace the recorded decision. The measured 10.4–15.6 ms completion is consistent with the 10 ms bound plus thread/interpreter scheduling overhead and comfortably below the 120 ms spin.

Timing clarification: a heartbeat scheduled at 20 ms cannot fire inside an evaluation that has already returned at 10–16 ms. It fired **after the decision, while the hook invocation still spun**, demonstrating that the hook did not block the evaluating event loop. An additional heartbeat scheduled at 5 ms fired at **10.432 / 10.368 / 10.335 ms**, before each decision returned and before the hook finished. No claim that the 20 ms heartbeat preceded the decision is made.

Independent real-clock `none_of` checks also returned:

- Raising hook: **no trigger**, `rejected:wake_hook_error`.
- Parked/timed-out hook: **no trigger**, `rejected:wake_hook_timeout`.

`test_p7_ac34_a_failed_hook_vetoes_whatever_the_combination` passed unchanged, including unavailable/raising/timeout vetoes under `none_of`, matching `any_of` and module-default failure cases, a real `False` control that accepts under `none_of`, and **0 admissions** for failures.

The full suite also passed the four new `test_p17f1_*` hook tests: non-yielding abandonment, ordinary outcomes/cancellation, abandoned-worker limits, and concurrent-worker limits. The outstanding-worker cap is **4**; excess work fails closed without another hook invocation. R3/AC12 and the failure-veto composition are credited as passing.

### F2 — RESOLVED: no transcription before a decision

Rebound **`call_requests = lambda transport: list(transport.requests)`** in memory and inspected the existing `voice()` fake's transport directly. Each scenario advanced the injected clock **600 s before `start_inputs`**, then ran **60 s after startup**, using `PendingRunner` to hold later detector invocations.

| Scenario | Requests after prepare | Requests before start_inputs | Total requests after loop | Acquisitions | Speech events | Admissions |
|---|---:|---:|---:|---:|---:|---:|
| Detector never fires | 0 | 0 | 0 | 0 | 0 | 0 |
| Candidate declined by keyword policy | 0 | 0 | 0 | 0 | 0 | 0 |
| One accepted candidate | 0 | 0 | 1 | 1 | 1 | 1 |

Both candidate decisions observed **`(source calls, transcription requests) == (0, 0)`** at decision time. These are unfiltered transport totals, including anything prepare might have sent.

The original carriers both pass with the unfiltered counter:

- `tests/test_audio_input.py::test_p13_ac26_the_candidate_is_decided_before_any_audio_then_one_speech_event`
- `tests/test_audio_input.py::test_p14_ac37_nothing_before_start_inputs_then_one_detector_invocation`

Their own assertions now see the unfiltered total: `Voice.transcriptions()` directly returns `list(self.transport.requests)`, and `EngineSpy` uses that value for AC26's `seen == (0, 0)`. AC37 asserts the same list is empty after the preparation barrier. The capture-only `call_requests()` helper still strips and validates its legacy probe, but listening no longer calls it. As a sensitivity check, injecting one extra synthetic request immediately after `voice()` preparation makes **both original carriers fail**. Those two intentional failures are separate from the green unmodified suite.

`test_p17f1_the_listening_path_transcribes_only_an_accepted_candidate` adds all three scenarios and directly asserts the transport list, including **0 transport-factory calls** after prepare. R7/AC26 and AC37 now pass without the first pass's hidden-call qualification. The prior startup-cost qualifications on AC28/AC38 are also removed; their existing retention, bounds and loop assertions still pass.

Legacy capture was independently exercised without rebinding its helper: preparation made **1 request**, the **8,044-byte / 0.25 s** synthetic probe; capture brought the unfiltered total to **2**, with the captured **96,044-byte WAV** transcribed successfully and attached to the audio reference. The three existing failed-probe cases (raise, HTTP 503, held timeout), required-mode prepare refusal and disabled-transcription zero-transport test also passed. The production condition retains probing when `settings.listen is None`.

### F3 — UNRESOLVED: unchanged test-preservation baseline

This finding is carried forward without attempting resolution. The operator is deciding between retaining real installation/Git proofs with explicit isolation-guard exceptions and moving those proofs to opt-in trials. This review chooses neither treatment and records no regression for the existing state.

Re-executed baseline measurements:

```text
git diff --numstat 7782e5f HEAD -- tests/test_kick.py
89      14      tests/test_kick.py
```

The assertion-AST comparison again finds **52 changed existing test functions in 24 files**, **8 allowlisted and 44 outside the allowlist**. Functions are keyed by file/qualified name; ordered `ast.Assert` trees are compared without source locations. Baseline/current definition counts are now **2,497 / 2,724**, with **228 added and 1 old name removed**. The extra five definitions since the first review are P17F1 tests; the existing-function preservation numbers are unchanged.

The removed old name remains `tests/test_profiles.py::test_install_test_skips_only_when_pip_is_unavailable`, replaced by `test_install_test_never_skips_and_fails_loudly`. The installation node still replaces the real installation/import-isolation/launcher proof with checkout package-data and in-process checks. In particular these old guarantees remain absent:

- `assert report['core'].startswith(installed + os.sep)`.
- The assertion that the discovered `modules_directory` equals the fresh installed target's modules directory.
- `assert launcher.is_file()`.

The Git-worktree/base-commit assertions previously identified in `test_web.py::test_ac24_the_shipped_profiles_have_zero_changed_lines_since_the_phase_base` also remain replaced by committed-fixture comparisons. P17F1 changes neither that file nor the installation or Kick tests. These numbers and losses are the next pass's baseline, not evidence of a newly broken installation.

## REGRESSIONS

**None reproduced.** F3 remains an existing open finding.

The R1–R9 map retains the first review's named carriers and detailed measurements, with F1/F2's corrections above. All **40 functional acceptance criteria now PASS**; no AC is omitted or reassigned. F3 remains a separate closing-gate failure.

| Requirement | Current result | Criteria carried forward / rerun in the full suite |
|---|---|---|
| R1 — declarations, schemas, load-time acceptance/refusal | PASS | AC1–AC3 |
| R2 — candidacy, invariants, draws, authorization | PASS | AC4–AC9, AC35–AC36; independent authorization reproduction below |
| R3 — bounded, owned, fail-closed hooks | PASS, F1 resolved | AC11–AC14, AC34 |
| R4 — append-only vocabulary and compatibility | PASS | AC10, AC15–AC16 |
| R5 — channel points | PASS | AC17–AC20 |
| R6 — operator surfaces and executable press command | PASS | AC21–AC24, AC40 |
| R7 — voice ordering, privacy, cost and lifecycle | PASS, F2 resolved | AC25–AC28, AC37–AC39 |
| R8 — interval, probability and window | PASS | AC29–AC31 |
| R9 — guides, manual record and fake demonstrations/isolation | PASS within the first review's scope | AC32–AC33; F3 preservation remains UNRESOLVED |

The fix delta modifies only `core/triggers.py`, `modules/audio_input/__init__.py`, `tests/test_declared_triggers.py` and `tests/test_audio_input.py`. Compared with the first-pass tree:

- **0 removed test functions, 5 added definitions / 7 added pytest outcomes**, and **0 new skip/skipif/xfail/importorskip calls**. No test file was deleted. Across the whole branch, the previously reported single replaced node remains the only removed old name.
- Only **2 existing test functions** change assertion ASTs in the fix: the over-duration source-failure and hard-acquisition-deadline tests. Both replace filtered-empty transcription assertions with **unfiltered-empty** assertions; assertion counts stay **5 → 5** and **7 → 7**. No additional weakening was found in the fix.
- **No new dependency.** Runtime requirements remain exactly `aiohttp>=3.9,<4` and `PyYAML>=6,<7`; the whole-branch pyproject diff only adds operator/timer package data.
- **No application socket, connection or listener added by the fix**, and no relaxation of the isolation guard. Worker event loops use asyncio's already-documented wakeup self-pipe exception; this is not a claim of zero infrastructure socketpairs. The guarded full suite and its socket/process refusal checks pass.
- The four shipped root profiles and `tests/test_triggers.py` still have an **empty diff against `7782e5f`**. The unchanged profile-replay tests remain green.

### Full suite

Executed exactly:

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/ -q -p no:cacheprovider
```

**4,820 passed, 20 skipped in 377.49 s (0:06:17), exit 0; 4,840 outcomes.** This is seven additional passing outcomes over the first review. The 20 skips remain the pre-existing opt-in phase-2/3/5 real-instance trials. The default suite runs under `_ac33_isolation`, with no opted-in exemption selected.

### A trigger still grants nothing

Executed one operator press through the fake listener into the shipped operator module, real trigger engine, brain consumer, real admission scheduler/run and real action executor, on one shared runtime with **no authorization grants**. The fake model proposed `screen.capture`, then supplied a final response.

Observed **1 `accepted:button_match` decision, 1 published input event, 1 real run**, and **2 model requests with 0 offered tools**. The executor refused `screen.capture` with **`not_authorized` / `no_applicable_rule`**; its provider received **0 calls**. Final `chat.write` was also refused, **0 sends** occurred, and the run ended **refused**. Trigger acceptance still confers no action permission.

## NEW FINDINGS

**None from executed reproductions.** The expected persistence of F3 is not a new finding.

## NOT VERIFIED

- **No real Twitch redemption.** EventSub/Helix behavior here is exercised with fakes.
- **No real microphone.** WAVs, source processes and observed pre-roll are synthetic; no audio device was opened.
- **No real wake word.** Detector verdicts are scripted; detection quality and real ring-buffer behavior remain unverified.
- No live browser/hotkey/OBS trial or operator-filled phase-8 trial record was produced. The record remains `not run`.
- The converted fake-process/in-process checks do not establish OS signal delivery, a real two-process deployment or a fresh installed distribution. Those limits do not negate the fake demonstrations, and are not represented as real-instance proof.
