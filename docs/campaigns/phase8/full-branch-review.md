# Phase 8 — full-branch closing review

## VERDICT: REQUEST_CHANGES

Reviewed `7782e5f..c3baaeb` against the brief's seven binding decisions, spec R1–R9 and AC1–AC40, the plan's closing gate, `docs/design-v2.md`, and the changed contracts, loader, trigger engine and inputs. The working tree was clean when review began. Only this report was written; nothing was staged.

The ordinary suite is green: **4,813 passed, 20 skipped**, but two required behaviors fail executed checks: hooks have a cooperative rather than hard bound, and listening transcribes a synthetic probe before any detection or accepted candidate. The test-treatment gate also fails: the branch exceeds the constant-only allowlist and removes existing installation assertions.

These are closing-gate failures, not requests for a real Twitch or microphone trial before approval.

## REQUIREMENT MAP

Test prefixes below are exact file aliases: **D** = `tests/test_declared_triggers.py`, **A** = `tests/test_audio_input.py`, **O** = `tests/test_operator.py`, **T** = `tests/test_twitch.py`, **I** = `tests/test_timer.py`. A name after `::` is the actual collected test function; parametrized rows were included in the suite.

| Requirement | Result | Running tests carrying the requirement; assessment |
|---|---|---|
| R1 — declarations, schemas and load-time acceptance/refusal | **PASS** | D::`test_p4_ac1_the_badge_tier_manifest_loads_with_its_declared_field`; D::`test_p4_ac2_each_fixture_declaration_is_refused_at_load`; D::`test_p4_ac3_neither_match_nor_hook_is_refused_at_name`; D::`test_p5_ac1_the_registry_accepts_the_badge_tier_declaration`; D::`test_p5_ac3_a_parameter_outside_the_declared_schema_is_refused`. Unknown names and malformed schemas remain refused. |
| R2 — narrow-only candidacy, invariants, draws and authorization | **PASS** | D::`test_p6_ac4_badge_tier_decides_by_the_payload_field`; D::`test_p6_ac5_invariants_run_before_any_declared_rule`; D::`test_p5_ac6_an_override_naming_another_inputs_type_is_refused`; D::`test_p6_ac7_one_draw_per_evaluation_and_a_reproducible_replay`; D::`test_p7_ac9_an_accepted_declared_decision_grants_nothing`; D::`test_p6_ac35_draws_record_the_five_scalars_of_each_draw`; D::`test_p6_ac36_the_matcher_is_linear_in_the_text`. Independent real-brain probes also demonstrated admission, budget and action refusals. |
| R3 — bounded, owned, fail-closed hooks | **FAIL** | D::`test_p7_ac11_each_hook_answer_decides_and_is_retained`; D::`test_p7_ac12_a_hook_that_never_returns_times_out_on_the_bound`; D::`test_p7_ac13_the_hook_runs_only_for_an_eligible_event`; D::`test_p7_ac14_only_the_declaring_module_supplies_its_hook`; D::`test_p7_ac34_a_failed_hook_vetoes_whatever_the_combination` all run green. The timeout fixture yields to the event loop; a permitted coroutine that blocks it exceeds the bound. F1 below defeats the brief's hard-timeout requirement. |
| R4 — append-only vocabulary and compatibility | **PASS** | D::`test_ac15_the_four_kinds_are_appended_after_the_phase7_ten`; D::`test_ac15_an_appended_kind_belongs_to_no_input_family`; D::`test_ac16_a_brain_route_matching_manual_validates_at_startup`; D::`test_ac16_shipped_profiles_decide_as_in_phase7`; D::`test_ac16_runtime_dependencies_are_exactly_aiohttp_and_pyyaml`; the unchanged `tests/test_triggers.py`; `tests/test_brain.py::test_an_empty_text_cue_is_admitted_and_runs_its_route`. Four shipped profiles and the old trigger test file have zero diff. |
| R5 — channel points | **PASS** | T::`test_ac17_reward_subscription_is_requested_only_when_listed`; T::`test_ac18_a_refused_reward_subscription_degrades_reward_and_chat_continues`; T::`test_ac19_a_redemption_normalizes_to_one_reward_event`; T::`test_ac19_malformed_redemptions_publish_nothing_and_are_counted`; T::`test_ac20_the_reward_type_selects_on_reward_id_and_cost_bounds`. A separately ingested r1/r2 pair gave one admission. |
| R6 — operator surfaces and executable press command | **PASS** | O::`test_ac21_a_press_from_each_surface_gives_three_manual_events`; O::`test_ac22_every_refusal_gives_no_event_and_its_count_and_echoes_nothing`; O::`test_ac23_each_refused_setting_is_a_startup_diagnostic_naming_it`; O::`test_ac24_a_button_policy_accepts_hype_and_rejects_other`; O::`test_ac40_one_post_to_the_press_endpoint_naming_hype_hotkey_and_the_token`; O::`test_ac40_each_core_refusal_returns_2_with_no_event`; O::`test_ac40_a_connection_error_or_a_timeout_returns_3`; O::`test_ac40_importing_the_command_imports_no_runtime_loader_or_lifecycle`. |
| R7 — voice input, privacy, cost and lifecycle | **FAIL** | A::`test_p12_ac25_without_listening_nothing_is_invoked_over_60_seconds`; A::`test_p13_ac26_the_candidate_is_decided_before_any_audio_then_one_speech_event`; A::`test_p13_ac26_observed_bounds_the_pre_roll_window`; A::`test_p12_ac27_the_hook_decides_by_the_verdict_alone`; A::`test_p13_ac28_five_accepted_candidates_store_and_write_no_audio`; A::`test_p14_ac37_nothing_before_start_inputs_then_one_detector_invocation`; A::`test_p14_ac38_no_verdict_backs_off_to_17_invocations_in_an_hour`; A::`test_p14_ac39_recovery_reports_each_transition_once`. Acquisition, retention, ordering and recovery work, but the call-count helper hides a real prepare-time transcription. See F2. |
| R8 — interval, probability and window | **PASS** | I::`test_ac29_two_channels_every_10_minutes_give_12_accepted_events_in_an_hour`; I::`test_ac30_100_events_consume_100_draws_and_accept_those_below_the_probability`; I::`test_ac30_the_window_bounds_are_inclusive`; I::`test_ac31_an_out_of_range_parameter_is_a_load_diagnostic_naming_it`; I::`test_ac31_a_policy_the_loop_cannot_honour_is_a_startup_diagnostic`; I::`test_ac31_nothing_fires_after_prepare_before_start_inputs`; I::`test_ac31_nothing_fires_after_stop_inputs`. |
| R9 — guides, manual record and fake demonstrations | **PASS** | D::`test_p15_ac32_the_triggers_guide_is_a_discovered_bilingual_pair`; D::`test_p15_ac32_one_example_per_family_loads_against_the_shipped_manifests`; D::`test_p16_ac32_the_summary_cites_r1_to_r9_and_the_trial_is_not_run`; D::`test_ac33_the_whole_suite_runs_under_the_guard`; D::`test_ac33_no_test_reaches_a_real_spawn_or_socket`; `tests/test_ac33_isolation.py`. This passes the documentation/isolation requirement; it does not excuse F3's separate test-preservation failure. |

### All 40 acceptance criteria

**37 PASS, 3 FAIL; none omitted.** Green fixture tests are distinguished from the stronger counterexamples. AC34 belongs to R3; AC35–AC36 to R2; AC37–AC39 to R7; AC40 to R6. Numbering was not reassigned.

In-memory sensitivity checks removed or corrupted the behavior carried by at least one named test for **every AC number**, and made that carrying test fail. No source file was mutated. For loader-created inputs, mutations were applied to the actual isolated `_companion_module_*` imports, as well as canonical imports; patching only the canonical package initially missed those subjects. Positive reward rows can still pass an always-true matcher, but its wrong-reward and out-of-range rows fail. These checks establish assertion sensitivity, not universal correctness; F1 and F2 override fixture successes.

| AC | Result | Named tests and assertion / executed sensitivity check |
|---|---|---|
| 1 | PASS | D::`test_p4_ac1_the_badge_tier_manifest_loads_with_its_declared_field`; D::`test_p5_ac1_the_registry_accepts_the_badge_tier_declaration`. Badge declaration loads, registers and resolves gold policy; removing declared-type registry support breaks the test. |
| 2 | PASS | D::`test_p4_ac2_each_fixture_declaration_is_refused_at_load`; D::`test_p3_ac2_a_field_absent_from_canonical_and_declared_fields_is_refused`. Nine malformed declaration cases, diagnostic subpaths and zero transport opens; removing field-reference validation makes the expected rejection fail. |
| 3 | PASS | D::`test_p4_ac3_neither_match_nor_hook_is_refused_at_name`; D::`test_p4_ac3_a_non_required_probability_parameter_is_refused`; D::`test_p5_ac3_a_parameter_outside_the_declared_schema_is_refused`; D::`test_p5_ac3_a_parameter_of_the_wrong_type_echoes_no_value`. Removing policy validation makes the expected error fail. |
| 4 | PASS | D::`test_p6_ac4_badge_tier_decides_by_the_payload_field`. Exact gold/silver/absent/integer/cue/case outcomes; an always-true tree fails the negative rows. |
| 5 | PASS | D::`test_p6_ac5_invariants_run_before_any_declared_rule`. Invariant reasons, zero evaluation deltas and dedup reuse; removing anti-echo fails the assertions. |
| 6 | PASS | D::`test_p5_ac6_an_override_naming_another_inputs_type_is_refused`. Foreign input diagnostic and zero evaluations/window/draw changes; bypassing policy validation fails both parametrized cases. |
| 7 | PASS | D::`test_p6_ac7_one_draw_per_evaluation_and_a_reproducible_replay`. 100 draws for 100 events, matching/nonmatching trees and equal replay; bypassing the random source fails. |
| 8 | PASS | D::`test_p6_ac8_matches_is_a_case_insensitive_wildcard`; D::`test_p3_ac8_a_matches_pattern_holds_at_most_128_characters`. Exact wildcard results and pattern bound; disabling matching fails. |
| 9 | PASS | D::`test_p7_ac9_an_accepted_declared_decision_grants_nothing`. Real scheduler refusal and zero offered writes; exposing ready actions without authorization fails. Additional integrated probes below cover actual execution refusal and budgets. |
| 10 | PASS | Unmodified `tests/test_triggers.py`, including `test_two_channels_apply_two_policies_in_one_engine`. Removing generic keyword matching fails that test; the file is byte-identical to baseline. |
| 11 | PASS | D::`test_p7_ac11_each_hook_answer_decides_and_is_retained`. True/False/1/string/None/raise, decision counting and retention; replacing hooks with unconditional matches fails. |
| 12 | **FAIL** | D::`test_p7_ac12_a_hook_that_never_returns_times_out_on_the_bound`; D::`test_p7_ac12_a_bound_of_0_or_10_5_is_refused_at_load`. Cooperative 0.5 s timeout and load bounds are asserted and sensitive, but F1's allowed blocking coroutine takes 0.1202 s with a 0.01 s bound. The hard-bound behavior is not established by the parked-future fixture. |
| 13 | PASS | D::`test_p7_ac13_the_hook_runs_only_for_an_eligible_event`. Invariants, redelivery, foreign kind/source and eligible invocation counts; removing dedup recall fails. |
| 14 | PASS | D::`test_p7_ac14_only_the_declaring_module_supplies_its_hook`. Foreign supply is refused and missing hook stays unavailable; making supply silently accept fails the expected rejection. |
| 15 | PASS | D::`test_ac15_the_four_kinds_are_appended_after_the_phase7_ten`; D::`test_ac15_an_appended_kind_belongs_to_no_input_family`. Exact tuple, seven notice kinds and family classification; removing the suffix fails. |
| 16 | PASS | D::`test_ac16_shipped_profiles_decide_as_in_phase7`; D::`test_ac16_a_brain_route_matching_manual_validates_at_startup`; D::`test_ac16_runtime_dependencies_are_exactly_aiohttp_and_pyyaml`. Four fixed-sequence profile replays have zero differences; disabling keyword matching breaks the comparison. |
| 17 | PASS | T::`test_ac17_reward_subscription_is_requested_only_when_listed`. Zero versus one subscription with exact type/version/condition; deleting the reward subscription branch fails. |
| 18 | PASS | T::`test_ac18_a_refused_reward_subscription_degrades_reward_and_chat_continues`. One reward-only degradation, sanitized payload and continuing chat; changing the degraded capability fails an explicit assertion. |
| 19 | PASS | T::`test_ac19_a_redemption_normalizes_to_one_reward_event`; T::`test_ac19_malformed_redemptions_publish_nothing_and_are_counted`. Trim/fallback, payload, dedup and four invalid events, plus boolean-cost rejection; changing normalized text fails an explicit assertion. |
| 20 | PASS | T::`test_ac20_the_reward_type_selects_on_reward_id_and_cost_bounds`. r1/500 accepts; r1/50, r1/2000 and r2/500 reject; omitted bounds allow 0 and 100000. An always-true tree fails all three negative rows. |
| 21 | PASS | O::`test_ac21_a_press_from_each_surface_gives_three_manual_events`; O::`test_ac21_no_listener_before_start_inputs_and_a_press_from_page_and_overlay`; O::`test_ac21_after_stop_inputs_the_listener_is_closed_and_presses_give_nothing`. Three surfaces, lifecycle and exact event shape; corrupting the emitted button id fails. |
| 22 | PASS | O::`test_ac22_every_refusal_gives_no_event_and_its_count_and_echoes_nothing`; O::`test_the_core_compares_the_token_in_constant_time`; O::`test_the_default_listener_disables_the_access_log_and_forbids_a_referer`. Token/Host/Origin/button/cooldown and no secret echo; accepting every token fails. |
| 23 | PASS | O::`test_ac23_each_refused_setting_is_a_startup_diagnostic_naming_it`; O::`test_ac23_a_profile_that_does_not_enable_operator_opens_no_listener`. Five startup refusals and disabled-module listener count; deleting settings validation breaks the cases. |
| 24 | PASS | O::`test_ac24_the_default_policy_accepts_a_manual_event`; O::`test_ac24_a_button_policy_accepts_hype_and_rejects_other`; O::`test_ac24_no_streamerbot_module_is_needed_or_enabled`. An always-true button matcher fails the other-button rejection. |
| 25 | PASS | A::`test_p12_ac25_without_listening_nothing_is_invoked_over_60_seconds`, plus existing capture tests. Absent/disabled listening starts zero detector/source processes; forcing listening on breaks these valid disabled profiles. Legacy capture-only transcription probing is separate from the new listening cost failure. |
| 26 | **FAIL** | A::`test_p13_ac26_the_candidate_is_decided_before_any_audio_then_one_speech_event`; A::`test_p13_ac26_a_declined_candidate_acquires_and_transcribes_nothing`; A::`test_p13_ac26_observed_bounds_max_seconds_8`; A::`test_p13_ac26_observed_bounds_the_pre_roll_window`; source/transcription failure and ordered-loop tests. Acquisition, measured duration/pre-roll and one transcription per acquired segment pass. The asserted zero calls before candidacy is false when all requests are counted: `(source, transcription)` is `(0, 1)`, not `(0, 0)`; F2. |
| 27 | PASS | A::`test_p12_ac27_the_hook_decides_by_the_verdict_alone`; A::`test_p12_ac27_a_never_returning_hook_times_out_after_2_seconds`; A::`test_p12_ac27_with_no_hook_supplied_the_default_policy_is_unavailable`; A::`test_p13_ac27_a_command_detection_submits_one_candidate_the_default_accepts`; HTTP/invalid-verdict tests. Candidate verdict, no detector audio, unavailable/timeout outcomes; replacing the verdict hook with False fails. |
| 28 | PASS | A::`test_p13_ac28_five_accepted_candidates_store_and_write_no_audio`. Zero attachment objects/files; no audio/base64 on bus/candidates; each of five acquired WAVs reaches transcription once. Injecting acquired WAV bytes into the published event fails. There is also one synthetic startup request, disclosed under F2; it is not retained microphone audio. |
| 29 | PASS | I::`test_ac29_two_channels_every_10_minutes_give_12_accepted_events_in_an_hour`. Exactly 12 events, six per channel, sequences/ids/text and admissions; deleting timer firing fails. |
| 30 | PASS | I::`test_ac30_100_events_consume_100_draws_and_accept_those_below_the_probability`; I::`test_ac30_the_window_bounds_are_inclusive`; I::`test_ac30_without_a_window_every_minute_of_the_day_is_accepted`; I::`test_ac30_a_rule_naming_an_undeclared_parameter_is_refused_at_load`. Draws, boundaries, optional window and declaration checks; bypassing draws fails. |
| 31 | PASS | I::`test_ac31_an_out_of_range_parameter_is_a_load_diagnostic_naming_it`; I::`test_ac31_a_policy_the_loop_cannot_honour_is_a_startup_diagnostic`; both lifecycle firing tests. Ignoring the exactly-one-interval/window checks fails all three policy cases. |
| 32 | PASS | D::`test_p15_ac32_the_triggers_guide_is_a_discovered_bilingual_pair`; D::`test_p15_ac32_one_example_per_family_loads_against_the_shipped_manifests`; D::`test_p15_ac32_the_guide_says_a_policy_replaces_the_default`; D::`test_p15_ac32_the_guide_puts_continuous_listening_out_of_scope`; D::`test_p16_ac32_the_summary_cites_r1_to_r9_and_the_trial_is_not_run`. Removing the replacement-policy sentence from the in-memory guide read fails. |
| 33 | PASS | D::`test_ac33_the_whole_suite_runs_under_the_guard`; D::`test_ac33_no_test_reaches_a_real_spawn_or_socket`; `tests/test_ac33_isolation.py::test_connecting_raises` and socket/process refusal self-checks. Full default suite is guarded, with zero trial exemptions selected. Replacing a guarded connection call with a harmless no-op makes its refusal assertion fail, without opening a socket. F3 separately concerns altered pre-existing coverage and the touched Kick file. |
| 34 | PASS | D::`test_p7_ac34_a_failed_hook_vetoes_whatever_the_combination`. Raising, timed-out and unavailable hooks reject under none_of; matching keyword/any_of and module defaults cannot rescue them; zero admissions. Removing the veto flag fails. |
| 35 | PASS | D::`test_p6_ac35_draws_record_the_five_scalars_of_each_draw`. Exact 0.9/0.1/0.42 decisions and five-field entries, replay/dedup and empty generic draws; dropping the recorded entries fails. |
| 36 | PASS | D::`test_p6_ac36_the_matcher_is_linear_in_the_text`. 5000/10000-character bounds, scaling and engine step accounting, plus a costly middle-segment case; falsifying step accounting to zero fails. |
| 37 | **FAIL** | A::`test_p14_ac37_nothing_before_start_inputs_then_one_detector_invocation`; failed-startup and detector/acquisition shutdown tests. Detector/source lifecycle assertions pass. The criterion's zero transcription calls after prepare and before start_inputs does not: the transport already has one request. Counting unfiltered requests makes the named test fail; F2. |
| 38 | PASS | A::`test_p14_ac38_no_verdict_backs_off_to_17_invocations_in_an_hour`; A::`test_p14_ac38_four_detections_in_an_hour_acquire_four_segments`; A::`test_p14_ac38_a_transcription_error_is_not_retried_and_backs_off`. During the loop: 17 detector invocations/hour, zero acquisitions/transcriptions without verdicts, four on four accepted detections, zero on declined ones, no segment retry. Removing exponential backoff fails. The pre-loop startup call is reported under AC37/F2. |
| 39 | PASS | A::`test_p14_ac39_each_settlement_waits_its_delay_on_the_sleeper`; A::`test_p14_ac39_a_productive_settlement_resets_the_backoff`; A::`test_p14_ac39_twenty_failures_are_capped_and_degrade_once`; A::`test_p14_ac39_recovery_reports_each_transition_once`; A::`test_p14_ac39_a_detector_pending_an_hour_is_neither_terminated_nor_counted`. Removing health transitions fails; failures, recovery, cap/reset and long pending detectors are asserted. |
| 40 | PASS | O::`test_ac40_one_post_to_the_press_endpoint_naming_hype_hotkey_and_the_token`; O::`test_ac40_each_core_refusal_returns_2_with_no_event`; connection/timeout, invalid-profile/usage, default-opener and import tests. Returning accepted for every response breaks refusal assertions. `__main__.py` has the actual `sys.exit(main())` entry point. |

## INVARIANT ATTEMPTS

These observations came from execution, using the shipped loader/engine/modules and existing fake boundaries. Reviewer scripts and mutations ran from standard input with bytecode disabled. No live service, detector or device was used.

| Attack | Observed result and numbers | Result |
|---|---|---|
| Assert an unproduced field | `secret.token` rejected during declaration/configuration: `TriggerSpec.types[0].match.equals[0].field: is neither a canonical normalized field nor a declared field`. The load-fixture test also asserts the input-qualified diagnostic and **0 transports opened**. | PASS |
| Configure a value outside the declared parameter schema | Adding `extra` to badge parameters rejected immediately: `triggers.badge.policy.rules[0].parameters.extra: is not an allowed property`. The configured sentinel value was absent from the diagnostic. Wrong-type and enum tests also run. | PASS |
| Use unknown or malformed types | Undeclared `invented` policy type rejected at `triggers.badge.policy.rules[0].type`; malformed schema type `banana` rejected at `TriggerTypeDeclaration.parameter_schema.type`. Neither becomes an accepted never-firing policy. | PASS |
| Invert hook failures with none_of | Raising hook → `rejected:wake_hook_error`; parked hook at injected time **1000.5**, bound **0.5 s** → `rejected:wake_hook_timeout`; no hook → unavailable. AC34 additionally exercises matching any_of/default policies: **0 admissions**. A real False answer under none_of accepts, proving the failure veto is doing the work. | PASS |
| Defeat the hard hook bound | Allowed async hook spins for **0.12 s** before returning. Configured bound **0.01 s**; evaluation takes **0.1202 s**, then rejects as timeout. No acceptance leak, but evaluation cannot complete at its bound while that hook blocks the loop. | **FAIL — F1** |
| Grant an action by triggering | Actual `button{button_ids:[hype]}` press → `accepted:button_match` → **1 real admission**. No granting rules: **0 offered tools**, proposed `screen.capture` → executor `refused/not_authorized/no_applicable_rule`, **0 capture-provider calls**, **0 sends**; run ends refused after two turns. | PASS |
| Bypass admission or budgets | With one real run held in the fake model and one queued, **3 accepted button triggers** yield **2 admissions + 1 session_queue_full refusal**, pending depth **1**. Separately, `model_turns: 1` yields **1 model request**, terminal `failure=budget_exhausted`, `budget=model_turns`; the authorized fallback is sent once. | PASS |
| Renumber/reinterpret old kinds | Indices **0–9** remain `message, sub, resub, sub_gift, community_sub_gift, raid, follow, tip, watch_tick, cue`; **10–13** are `reward, manual, speech, timer`. Notice-kind count stays **7**; new kinds own no chat family. Existing empty-text cue route still admits and executes; generic trigger tests have **zero changed bytes**. | PASS |
| Redeem another reward | Ingested two fake redemptions, r1 and r2, cost **500**, configured r1 with bounds **100–1000**. Both normalize to `reward` with trimmed `hello`; reasons are accepted/rejected reward match/no_match; **1 admission**. Subscription and malformed-redemption counts are asserted separately. | PASS |
| Diverge the three button surfaces | Page, actual press-command `main(..., opener=CoreOpener)` and overlay yield **3 manual events**, all button id `hype`, identical configured prompt, author `operator:hype`, destination `fake/chan-a`; only surface and unique ids differ. **3 distinct message ids**. | PASS |
| Press without credentials / lie about command outcome | Tokenless page press → **401**, reason `token`, **0 events**. Command: accepted **0**; cooldown/unknown button/closed input **2**; connection refusal/timeout **3**; missing button/unreadable profile **64**. After stop, event total stays **3**. Suite also covers wrong token/Host/Origin, disabled hotkey and no secret output. | PASS |
| Never detect or decline a candidate | No verdict: **0 acquisitions, 0 segment transcriptions, 0 speech events, 0 stored objects**, but **1 actual transcription request**, already made during prepare. Declined keyword candidate: same totals, reason `rejected:keyword_no_match`. Request is an **8044-byte, 0.25 s synthetic WAV**. | **FAIL for zero-call cost — F2; retention PASS** |
| Accept, measure and transcribe the segment | With `max_seconds:8`, observed WAV duration is **8.0 s**. Pre-roll **0/2/1 s** gives observed start offsets **0/−2/−1 s**. For the phrase beginning 1.5 s before the late verdict, pre-roll 2 puts its marker at **0.5 s** and retains **17600 phrase frames**; pre-roll 1 has **0 marker frames**, **11200 phrase frames**. Starts were inferred from marker/known phrase endpoint in the actual transcribed WAV, not copied from requested env values. **1 segment transcription, 1 speech event, 1 admission**; total transport calls are **2** including startup. | Bounds/event PASS; total-cost claim **FAIL** |
| Retain or leak listening audio | Five accepted candidates: **0 attachment objects, 0 files**, five acquired WAVs each transcribed once; audio/base64 absent from bus/candidates/audit-visible history. Total transcription requests are **6**, including the synthetic startup probe. Injecting acquired WAV bytes into an event breaks the privacy test. | Retention PASS; startup cost disclosed |
| Kill detector health/rearming | Script **3 failures → verdict → 2 failures** gives exactly `module.degraded(wake_word_detector_failed)`, `module.ready`, `module.degraded(wake_word_detector_failed)`. **7 invocations**, seventh pending; starts at offsets **0,5,15,35,75,155,315 s** for the no-detection recovery case. Twenty failures report degraded once and cap gaps at **300 s**; no-verdict hourly test asserts **17 invocations**. | PASS |
| Defeat timed randomness/window | Independently drove **100 events, P=0.25**: **100 draws, 25 admissions**. At minutes **599/600/660/661**, window **600–660** yields reject/accept/accept/reject. Two-channel ten-minute test yields **12 firings/hour**, six per channel; no firing before start or after stop. | PASS |

## TEST TREATMENT

### Full suite and isolation

Executed exactly:

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/ -q -p no:cacheprovider
```

Result: **4,813 passed, 20 skipped in 390.24 s**, exit **0**, **4,833 collected outcomes**. The skips are the pre-existing opt-in phase-2/3/5 real-instance trials. No new skip, skipif, xfail or importorskip AST call was added.

The default suite ran under `_ac33_isolation`; no trial node opted out and the transitional ledger is absent. Detector, recorder, Twitch client, HTTP/overlay surfaces and process boundaries are faked. Application socket creation and real process spawning are refused by the guard; the asyncio wakeup self-pipe is its documented infrastructure exception. The socket/process refusal self-checks and AST scan pass. No real Kick listener remains in these tests: the phase **did touch** `tests/test_kick.py` and replaced its loopback transport, contrary to this gate's preservation instruction. `git diff --numstat 7782e5f HEAD -- tests/test_kick.py` gives **89 additions, 14 deletions**.

Sensitivity runs were deliberately expected to fail. They temporarily changed behavior in memory and restored patches per test; their failures are not failures of the unmodified full-suite run. The final sensitivity coverage includes at least one failing carrier for every AC1–AC40. Counting all transcription requests, without changing production, also makes AC26 and AC37 tests fail explicitly; see F2.

### Assertion AST comparison and allowlist

Compared baseline/current Python ASTs for every test file. Functions were keyed by file and qualified `test_*` name; each function's ordered `ast.Assert` trees were compared with `ast.dump(..., include_attributes=False)`, so formatting and line movement do not count. Decorator/call ASTs for skip, skipif, xfail and importorskip were compared separately. Function counts are syntax definitions, not parametrized pytest item counts.

- Baseline: **2,497 test-function definitions**; current: **2,719**; **223 added, 1 old name removed**.
- **52 existing test functions in 24 files** have changed assertion AST sequences. **8** are named by the nine-entry allowlist; **44** are outside it. The bilingual allowlist change is a module-level expected-set addition, so it does not change that function's assert AST.
- No new skip/skipif/xfail/importorskip call AST; no test file deleted. The removed node is `tests/test_profiles.py::test_install_test_skips_only_when_pip_is_unavailable`, replaced by `test_install_test_never_skips_and_fails_loudly`; this is a replacement, not an untouched existing test.
- The allowance is not satisfied by aggregate assertion counts: in changed files' surviving old tests, the total rises **8892 → 8961**, while specific guarantees disappear.

| Allowlisted test | Treatment observed | Audit |
|---|---|---|
| `test_contracts.py::test_event_kind_vocabulary_is_the_nine_names_in_order` | Appended kinds/count; extra prefix-preservation assertion | Compatible constants/additional protection |
| `test_input_ownership.py::test_cue_is_the_tenth_kind_and_not_a_platform_notice` | Cue pinned at index 9; appended final kind checked | Compatible constants/additional protection |
| `test_examples.py::test_manifests_are_unique_and_have_coherent_capabilities` | Module set/count, Twitch type and audio declaration changes | Within enumerated allowance |
| `test_manifest_presentation.py::test_presented_covers_every_shipped_manifest` | 19 → 21 | Within allowance |
| `test_profiles.py::test_installed_distribution_discovers_the_shipped_manifests_and_answers_help` | Replaced installation/import-isolation/launcher execution with checkout package-data and in-process help checks | **FAIL: substantially more than constants; old assertions removed** |
| `test_profiles.py::test_pyproject_ships_the_three_phase_2_manifests_and_no_new_dependency` | Added operator/timer package-data expectations | Within allowance; dependency assertion retained |
| `test_config_ui.py::test_ac15_the_base_page_lists_every_module_and_the_origin` | 19 → 21 | Within allowance |
| `test_docs_bilingual.py::test_the_discovered_pairs_are_exactly_the_declared_guides` | Added triggers pair to expected set | Within allowance |
| `test_audio_input.py::test_manifest_declares_an_on_demand_read_with_no_role` | Added input role, produces, listen/wake-word settings; capture action checks retained | Within enumerated allowance |

Concrete AST losses in the installation test include `assert report['core'].startswith(installed + os.sep)`, the installed `modules_directory` equality, and `assert launcher.is_file()`. None is equivalent to reading package-data globs in the checkout. Outside the allowlist, `test_web.py::test_ac24_the_shipped_profiles_have_zero_changed_lines_since_the_phase_base` removes the actual Git-worktree/base-commit existence assertions and substitutes committed fixture comparisons. Many other conversions preserve their subject/value checks, but these counterexamples alone disprove the claimed constant-only/no-assertion-loss treatment.

The plan flags D8's fidelity loss and even flags the installation-test exception for P17. That disclosure is useful; it cannot amend the spec's allowlist or this review instruction. This gate therefore fails F3 rather than treating the disclosed exception as permission.

### Dependencies and compatibility

`pyproject.toml` runtime dependencies remain exactly **`aiohttp>=3.9,<4` and `PyYAML>=6,<7`**. Its branch diff only adds the operator/timer package-data lines. No runtime dependency was added.

An explicit Git comparison of the four root profiles (`agent.yaml.example`, `config.server.yaml.example`, `config.yaml.example`, `presence.yaml.example`) and `tests/test_triggers.py` against `7782e5f` has **empty output**. New baseline-fixture copies named `*.example` were not mistaken for edits to shipped profiles. The current four-profile replay matches the P1 pre-production-change record with **zero differences** in decisions/reasons/policy versions, subscriptions and admissions; removing generic keyword behavior makes that replay fail.

## OPERATOR RESULT

The English guide is normative; the French header explicitly says the English text wins. Both explain declaration/schema/field catalog/match operators, bounded hooks, default replacement and configurations for reward, button, voice and interval. The voice example configures a detector command through `WAKE_WORD_DETECTOR`; the HTTP verdict protocol is also explained. Hotkey binding is an OS configuration step with the shipped command as its entry point. The guide explicitly requires a segment source honoring duration/pre-roll rather than silently assuming the old streaming recorder suffices.

The manual trial is a procedure for an operator, with an independent hand-filled `docs/campaigns/phase8/trial-record.md`: date/operator/version/platform/profile, per-family trace/outcome and notes. Its status remains **not run**. A pytest node proving fake behavior is not a substitute for that record.

A separate in-process UI probe used shipped manifests and the real form/Save handlers: rendered a `reward` policy with `reward_ids:[r1], min_cost:100`, submitted `min_cost:250`, received **303**, and read back `reward_ids:[r1], min_cost:250` from the temporary overlay. Existing policy-editing surfaces therefore handle the declared schema. No repository profile was changed.

The cost and hard-bound limitations in F1/F2 prevent approval despite these working operator surfaces.

## NEW FINDINGS

### F1 — HIGH: a registered coroutine can exceed the promised hard hook bound

Location: `core/triggers.py:1328`–the hook and timeout share the same event loop (`ensure_future`/`asyncio.wait`). The post-completion timestamp check correctly rejects a late answer, but cannot pre-empt the hook or complete evaluation while that hook blocks the loop. The plan's D2 and the guide acknowledge cooperation; binding brief decision 2 requires a **hard timeout** and is not narrowed by that acknowledgment.

Executed reproduction: use `_wake(hook_timeout_seconds=.01)` with `none_of`, register its hook through the owning module context, and construct `TriggerEngine` with `clock=time.monotonic`, normal asyncio sleeper, dedup capacity 10 and TTL 60. The valid hook is:

```python
async def blocking(event, parameters):
    until = time.monotonic() + .12
    while time.monotonic() < until:
        pass
    return True
```

`await engine.evaluate_async(_event('blocked'))` took **0.1202 s** against **0.01 s** configured, finally returning `accepted=False, reason='rejected:wake_hook_timeout'`. The finite probe demonstrates a twelvefold bound violation without leaving a hung process. No infinite blocking hook was executed. The failure-veto composition itself works; the missing property is bounded completion. Restore the binding hard-bound guarantee and add a test covering a hook that does not yield.

### F2 — HIGH: startup transcription defeats zero-cost listening assertions

Locations: `modules/audio_input/__init__.py:2034` calls `_probe_transcription()` during prepare whenever transcription is enabled and a source is usable; `tests/test_audio_input.py:1599` implements `call_requests()` by destructuring `probe, *rest = transport.requests` and returning only `rest`.

Executed against the existing `voice()` fake transport, inspecting **all** `transport.requests`:

| Scenario | Calls after prepare, before start_inputs | Calls after iteration(s) | Acquisitions | Stored objects |
|---|---:|---:|---:|---:|
| Detector returns no verdict | 1 | 1 | 0 | 0 |
| Detected phrase declined by keyword policy | 1 | 1 | 0 | 0 |
| One accepted candidate, 8 s segment | 1 | 2 | 1 | 0 |
| Five accepted candidates | 1 | 6 | 5 | 0 |

The extra call is a generated **8044-byte / 0.25 s silence WAV**, not recorded microphone audio. This is a cost/order failure, not evidence that microphone audio was retained. The probe predates listening for the capture action, but the new listening path inherits it and its tests remove it from the very counters used to claim zero transcription before acceptance/start.

Rerunning only AC26 and AC37 with `call_requests = lambda transport: list(transport.requests)` in memory, without production changes, gives **2 failed**: AC26's explicit `assert seen == (0, 0)` receives **`(0, 1)`**; AC37's empty-transcription-list assertion sees the probe. Preserve legacy capture-only behavior while preventing pre-candidacy transcription for listening, and assert the unfiltered transport-call total for these criteria.

### F3 — HIGH: the test-preservation gate is not met

The executed assertion-AST comparison finds **44 changed existing assertion sequences outside the allowlist**, one replaced/deleted old node name, and nonconstant replacements inside the allowlisted installation node. Specific installed-import isolation and launcher-file assertions are gone. The phase also changes the expressly protected Kick test file (**89 added / 14 deleted lines**).

This is not a claim that installation is broken: installation is no longer proven by those tests. It is a concrete counterexample to the branch's no-deleted-test/constant-only/no-assertion-loss closing claim. Retain the required prior guarantees and the gate's scope constraints; the plan's fake-transport conversion cannot silently substitute a weaker guarantee. The ordinary suite's higher green count does not resolve this finding.

## NOT VERIFIED

- **No real Twitch redemption.** EventSub/Helix behavior here is exercised with fakes.
- **No real microphone.** WAVs, source processes and observed pre-roll are synthetic; no audio device was opened.
- **No real wake word.** Detector verdicts are scripted; detection quality and real ring-buffer behavior remain unverified.
- No live browser/hotkey/OBS trial or operator-filled phase-8 trial record was produced. The record remains `not run`.
- The converted fake-process/in-process checks do not establish OS signal delivery, a real two-process deployment or a fresh installed distribution. Those limits do not negate the fake demonstrations, and are not represented as real-instance proof.

Phase 8 does **not** receive closing approval on this tree. F1, F2 and F3 require changes; the successful fake-backed families and green ordinary suite remain credited above.
