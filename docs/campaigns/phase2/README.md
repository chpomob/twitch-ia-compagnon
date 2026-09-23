# Phase 2 — audio and stream interaction

Archived campaign material for PHASE 2 of the project. The authoritative design remains
`docs/design-v2.md` (section 6 defines this phase and its exit criteria).

## Provenance

| File | Origin | State |
| --- | --- | --- |
| `brief.md` | written by the orchestrator from design §6 | input to the spec stage |
| `spec.md` | adversarial spec v1.2 (`phase2-audio-stream-interaction`, R1–R10, AC1–AC43) | approved; R10/AC38–AC41 amendment and AC42/AC43 verified |
| `spec-v2-amended.md` | the earlier amended draft | superseded by `spec.md` |
| `plan.md` | adversarial plan v1.2 (21 ordered steps P1–P21) | approved; P21 is the full-branch review gate |
| `scaffolding/` | step specs, campaign runner, logs, residuals | tooling, not runtime code |

## The product decision carried from phase 1

Delivery is a configured, pluggable terminal step: a configured ordered list of delivery actions
(mode `fixed`, or mode `modules` derived from the enabled modules that declare a delivery
capability, with a configurable preference order), each entry declaring how the answer text maps
into its arguments (a named argument, or none for an effect-only delivery). Phase 2 adds
`audio.speak` (text), `audio.play` and `stream.scene.set` (no text) as delivery-capable actions with
**no change to the agentic loop** — the gate verifies that below (check 2).

## Execution

Steps P1–P20 landed on `main` as `25c152e` … `26abd26`, one commit each, each with a green suite and
a reviewer approval. P21 is the full-branch gate: it reviews the integrated diff that no per-commit
review can see.

## P21 — full-branch review gate

- Base: `7e26038` (last phase 1 commit). Reviewed tree: `26abd26` (P20). The gate commit changes
  only `docs/README.md` and this file, so the code tree it reports on is `26abd26`'s.
- Verdict: **no code defect found**. Four scope/allowlist deviations (G1–G4) are recorded; none can
  be corrected inside P21's two files, and none weakens a guarantee.

### Checklist

| # | Check | Command / method | Result |
| --- | --- | --- | --- |
| 1 | Changed files ⊂ spec `targets` ∪ `docs/campaigns/phase2/`; plan `Files:` ⊂ targets | `git diff --name-only 7e26038..HEAD`, compared with the spec front matter | 36/36 targets touched. 5 files outside the list (G1). Every plan `Files:` entry is a target, except this README (permitted scope). |
| 2 | AC7: brain diff is R4 only | `git diff 7e26038..HEAD -- modules/brain/`, each hunk mapped to its enclosing function (AST) in the base and the final tree | Hunks only in the R4 sites: `probe`, `_verify`, `_probe_reason`, `_silent_wav`, `_encode_part`/`_encode_message`, `_resolve_attachment`, `_propose` (omission rule), `_audio_token_estimate`/`_estimate_message_tokens`, `_expired_attachment` (+ alias `_expired_image` so `_loop` keeps its phase 1 call site), `_discard_images`, `attachment_parts`, `_part_summaries`, run-end release in `run`. **0 hunks** in `_resolve_delivery`, `_deliver_all`, `_loop`. |
| 3 | `core/actions.py` = P2 + P3 hunks; AC40 | same AST mapping; the two phase 1 tests compared as source | Hunks only in `_reject_parts`, `_discard_images` (P2 lease) and `_run_invocation`, `_is_interruption_record` (P3 adoption). `test_a_confirmation_landing_after_the_deadline_is_never_a_success` and `test_a_confirmation_landing_in_time_survives_a_late_adoption` are byte-identical; `tests/test_actions.py` has 0 removed lines. |
| 4 | AC41 | `grep -rniE "adoption_lead\|lead_seconds\|deadline_margin\|early_stop\|deadline_lead" modules/audio_output/ modules/audio_input/ modules/stream_control/`; manifests grepped for `lead`/`margin`; every added `-` expression in `git diff 7e26038..HEAD -- modules/` reviewed | 0 lines; 0 schema keys. Exactly two deadline subtractions, both in `modules/audio_input/__init__.py`: `seconds > (expiry - now) - grace_seconds` (`_admit`, before any spawn) and `transcription_edge = expiry - TRANSCRIPTION_RESERVE_SECONDS` (1.0 s, bounds the transcription request only). The recorder watcher sleeps `max(0, expiry - now)` — the kill stays at `expiry` (AC39). `audio_output` and `stream_control` compute `expiry = min(call.deadline, now + timeout_seconds)` and only ever wait `max(0, expiry - now)`: no subtraction. |
| 5 | Caller tables | `grep -rn PART_TYPE_IMAGE_REF / PART_TYPE_AUDIO_REF / IMAGE_CONTENT_TYPES / ATTACHMENT_CONTENT_TYPES / RuntimeContext(` | `core/contracts.py`, `core/actions.py`, `modules/proxy`, `modules/agent_link` each pair `image_ref` with `audio_ref`; `modules/capture` alone stays image-only. The only non-contract content-type check (`modules/proxy`) reads `ATTACHMENT_CONTENT_TYPES`; `IMAGE_CONTENT_TYPES` remains only in the `image_ref` part validator. The six `RuntimeContext(` constructions (`core/main.py`, `tests/conftest.py`, `tests/test_brain.py` ×2, `tests/test_loader.py`, `tests/test_stream_control.py`) build and pass. |
| 6 | R1–R10 → step → test | test names and docstrings over the suite | Every requirement and every AC1–AC43 has at least one named passing test (map below). |
| 7 | Only the 8 allowlisted tests change a phase 1 assertion | every function of every modified test file compared as source between base and final tree | **No** — 4 more (G2). |
| 8 | Neutrality, model literals, sleeps | added lines of `git diff 7e26038..HEAD -- core/ modules/brain/` grepped for `poll`, `twitch`, `obs`, `audio`, `capture`, `pipewire`, `aplay`, `ffmpeg`, `proxy`; `grep -c poll core/runtime.py core/main.py` | No platform, device or vendor word added; `audio`/`capture` only in the R4 vocabulary and the omission text (`Audio captured by tool call …`). `poll` 0 times in `core/runtime.py`/`core/main.py` (AC25). 0 `proxy`/transport words added to the brain (AC35). No model literal; no positive sleep (hygiene suite green). |
| 9 | Fake-done shortcuts over the whole diff | grep of added lines for `skip`/`xfail`, `TODO`/`FIXME`, `NotImplementedError`, `except …: pass`, dependency changes; review of removed test lines | No weakened, skipped or xfailed assertion (the only `pytest.skip` is the opt-in trial runner, named per variable). `NotImplementedError` only in the abstract base of the shared transport doubles. Every `except … pass` guards a signal to a process that may already have exited. No new runtime dependency (`pyproject.toml` adds the three package-data lines only). No happy-path-only provider: every module suite covers failure, deadline, cancellation and drain. |
| 10 | Suite, profiles, topology, trials | `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`; `-k "check_config or two_process"`; opt-in trial runner | `1887 passed, 12 skipped` (6 opt-in trials, 5 pidfd-gated capture cases, 1 pip-less install check). `--check-config` green on the three profiles. `test_ac39_two_process_topology_over_loopback` byte-identical and green (G3). Trials: playback and capture re-run on `26abd26` with the same `PHASE2-TRIAL` lines; four not re-run (G4). |

Cross-step risks named by the plan, checked on the integrated tree:

- **`expires_at` vs a module's `expiry`**: every new provider computes `min(call.deadline, now +
  timeout_seconds)` on the injected clock the executor uses; across the proxy the agent re-bases
  the deadline on its own monotonic clock (`remaining_ms`), which `tests/test_proxy.py` pins (AC34
  of phase 1) and the phase 2 scenario runs (AC35).
- **A `CancelledError` handler interrupted again**: the adopted-record tests run through the real
  executor (`test_the_call_deadline_stops_the_player_and_the_module_record_is_adopted`,
  `test_a_silent_recorder_is_killed_at_the_deadline_and_the_record_adopted`,
  `test_the_timer_winning_adopts_the_record_of_a_provider_that_answers_its_cancel`) and assert the
  module's own cause/code, not the generic `timed out with emission` record.
- **`transcription` across the proxy**: the scenario compares the observation contract locally and
  across the in-memory proxy and finds it identical (`test_ac35_the_scenario_has_one_contract_locally_and_across_the_proxy`).
- **`sent` flag of the two poll services**: `modules/twitch` and the fixture platform both raise
  `PollTransportError(sent=…)` with the same meaning (`sent=False` only when nothing left).

### Requirement map

| Req. | Steps | Tests (representative, all passing) | ACs |
| --- | --- | --- | --- |
| R1 audio output | P8, P9, P17, P18 | `tests/test_audio_output.py` (`test_speak_synthesises_once_and_plays_the_whole_wav_to_completion`, `test_a_503_is_tts_failed_naming_the_code_and_never_the_body`, `test_three_calls_on_one_output_play_in_turn_and_the_third_is_busy`, `test_a_non_executable_player_leaves_both_actions_unbound`) | AC1–AC5 |
| R2 delivery | P17 (+ gate check 2) | `tests/test_delivery.py::test_ac6_chat_write_then_audio_speak_writes_and_speaks_the_one_final_answer`, `::test_ac7_modules_mode_resolves_preferred_first_then_catalog_order`, `::test_ac7_an_uncertain_speech_across_a_dropped_proxy_is_never_memorised` | AC6, AC7 |
| R3 audio input | P10, P11, P18 | `tests/test_audio_input.py` (`test_a_5_second_source_cut_to_3_seconds_is_stored_and_referenced`, `test_seconds_beyond_the_admission_reserve_is_capture_too_long_before_any_spawn`, `test_a_transcription_answer_is_dated_and_carried_by_the_audio_ref`, `test_an_exhausted_window_sends_nothing_and_is_skipped`) | AC8–AC12, AC42 |
| R4 audio parts, brain | P1, P2, P6, P7 | `tests/test_observations.py::test_ac14_an_audio_ref_leased_to_another_run_is_invalid_result`, `tests/test_model_adapter.py::test_ac15_audio_probe_follows_the_text_probe_with_exactly_two_probes`, `::test_ac16_a_verified_audio_capability_encodes_the_stored_segment_at_request_time`, `::test_ac17_an_audio_part_is_estimated_at_ten_tokens_per_second_rounded_up` | AC13–AC17 |
| R5 proxy | P1, P12, P18 | `tests/test_proxy.py::test_ac18_an_agent_side_audio_capture_reaches_the_brain_store_by_reference`, `::test_ac19_an_audio_ref_naming_an_unacknowledged_attachment_is_refused_at_the_proxy`, `::test_ac20_the_frame_table_limits_and_codes_are_the_phase_1_ones`, `tests/test_proxy_process.py::test_ac39_two_process_topology_over_loopback`, `tests/test_phase2_scenario.py::test_ac35_the_scenario_has_one_contract_locally_and_across_the_proxy` | AC18–AC20, AC35 |
| R6 scenes | P14, P15 | `tests/test_stream_control.py::test_an_allowed_scene_is_set_once_and_confirmed_by_one_read_back`, `::test_a_lost_set_answer_is_reconciled_by_exactly_one_read`, `::test_two_sessions_are_serialized_and_a_third_is_refused_busy`, `::test_identify_carries_the_derived_authentication_and_never_the_password` | AC21–AC24, AC36 |
| R7 polls, service registry | P4, P13, P16 | `tests/test_stream_control.py::test_two_modules_publishing_one_key_fail_activation_naming_both`, `::test_a_poll_is_created_once_and_its_channel_refused_until_the_ttl`, `::test_a_lost_create_answer_reconciled_by_one_get_is_a_reconciled_success`, `::test_the_channel_cap_refuses_an_untracked_channel_until_an_entry_expires` | AC25–AC28, AC37 |
| R8 readiness, `required` | P9, P11, P14, P16, P19 | readiness/`required` cases of the three module suites, `tests/test_examples.py::test_ac30_the_chat_only_profile_starts_and_runs_the_phase_1_scenario`, `tests/test_profiles.py::test_ac31_the_full_pc_profile_degrades_three_modules_and_still_chats` | AC29–AC31 |
| R9 profiles, packaging, README | P8, P10, P12, P14, P19, P20 | `tests/test_profiles.py::test_server_profile_binds_the_device_actions_to_the_proxy_and_polls_locally`, `::test_pyproject_ships_the_three_phase_2_manifests_and_no_new_dependency`, `tests/test_hygiene.py::test_ac34_readme_records_the_phase_2_provider_trials`, `tests/test_profiles.py::test_ac43_with_no_speech_endpoint_audio_speak_is_named_not_ready_with_0_requests` | AC32–AC34, AC43 |
| R10 late interruption records | P3, P9, P10, P11, P14, P16, P20, P21 | `tests/test_actions.py::test_a_late_provider_timeout_is_adopted_verbatim`, `::test_a_late_success_is_still_replaced_by_the_generic_record`, `::test_a_provider_that_never_answers_is_cut_by_the_timer_at_expiry`, the two unchanged phase 1 boundary tests, `tests/test_hygiene.py::test_ac41_the_readme_describes_no_lead_for_playback_or_capture`, gate checks 3 and 4 | AC38–AC42 |

### Gate findings

| # | Finding | Files | Origin | Disposition |
| --- | --- | --- | --- | --- |
| G1 | Five files changed outside the spec's `targets`: `core/loader.py` (AC25 diagnostic naming the holding module, read from the registry, never from the exception text), `.gitignore` (spec-generator output of this campaign), `tests/test_brain.py`, `tests/test_integration.py` (PC-profile environment for the new devices), `tests/test_proxy_process.py` | those five | P4, `aaf6983`, P6, P19, P19 | Not changed by P21 (outside its two files). Reverting would break AC25, AC15 and the PC profile's startup; the spec's `targets` list needs these entries. |
| G2 | Phase 1 assertions changed outside the 8-test allowlist: `tests/test_brain.py::test_ac10_capabilities_required_needs_structured_output_and_only_known_names` (`audio` now known), `tests/test_observations.py::test_probe_tool_and_reasons` (`audio_rejected`), `tests/test_observations.py::test_image_ref_fields_are_the_seven_of_r4` (`PART_TYPES` gains `audio_ref`), `tests/test_profiles.py::test_server_profile_binds_screen_capture_to_the_proxy_provider` replaced by `test_server_profile_binds_the_device_actions_to_the_proxy_and_polls_locally` (a superset of its assertions) | those tests | P6, P1, P1, P19 | Legitimate contract changes. Each cites R4 or R9 in its docstring, and none weakens a guarantee. The spec's allowlist needs these four entries. |
| G3 | AC20 says the two-process topology test passes "unchanged". The test function is byte-identical and green, but its helper `_agent_profile` now strips the three phase 2 device modules from the agent profile. | `tests/test_proxy_process.py` | P19 | Recorded. The phase 2 modules across the proxy are covered by AC35's scenario. |
| G4 | Per-provider trials: playback and capture re-run on the gate tree `26abd26`. Speech synthesis and transcription wait for an operator-supplied endpoint and model, which are not in versioned configuration. The scene trial would switch the program scene of the live broadcasting software, which the gate does not do without the operator's consent. Polls still have no platform credentials. | `docs/README.md` | P20 | Each row's Commit field states which. |

### Not verified by this gate

- The four trials of G4 against real services on the gate tree.
- The five pidfd-gated capture cases and the pip-based clean-install check skip on this machine
  (`needs Linux process groups, /proc and pidfd`; `No module named pip` in the venv).
- AC42's declared stalled-wake limit (a wake delayed by the whole 1 s reserve) cannot be pinned by
  a test; it is documented in `docs/README.md` ("Limites déclarées de la phase 2").
