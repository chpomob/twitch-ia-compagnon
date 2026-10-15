# Phase 5 — full-branch closing gate, first pass

## VERDICT

**REQUEST_CHANGES.** The automated suite is green, but four executed counterexamples remain: a public publication path bypasses input ownership, the aiohttp canary misses a suspension before writing, cues about viewers enter viewer memory, and ownership diagnostics can expose a configured credential.

Reviewed main at `12eb1de`, cumulatively against `9f6c1d9` (the pre-phase implementation identified by the brief). Main already points to HEAD, so `git diff main...HEAD` is empty; it cannot represent this phase. Used `git diff 9f6c1d9 HEAD` across the kernel, modules, tests, fixtures, packaging and operator documentation, rather than reviewing individual commits. The four example profiles also have an empty diff against the specification-stage base `f900f27`, as checked by the running regression.

Authorities: `spec.md`, `brief.md`, `plan.md` and `scaffolding/plan-amendments.md`; integration context: `docs/research/streamerbot-etude-2026-10.md`. D1–D11, including the approved P12 corrections, are accounted for below. No implementation change is proposed as an unapproved amendment.

## Executed commands

All commands ran from the repository root using its virtual environment. Bytecode writes were disabled and pytest's cache provider was disabled. Initial sandbox launch failed with `bwrap: loopback: Failed RTM_NEWADDR`; approved escalated execution then ran the commands successfully.

| ID | Command | Result |
| --- | --- | --- |
| S | `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/ -q -p no:cacheprovider` | **3600 passed, 26 skipped in 228.81s (0:03:48)** |
| T | `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/test_phase5_trials.py -q -p no:cacheprovider` | **8 skipped in 0.01s**; exactly eight trial nodes |
| D | `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/test_phase5_docs_status.py -q -p no:cacheprovider` | **27 passed in 0.34s** |
| P | `git diff --numstat 9f6c1d9 HEAD -- config.yaml.example config.server.yaml.example agent.yaml.example presence.yaml.example` | Empty: zero changed lines |
| V | `rg -n -i -e 'streamer[._ -]?bot' -e 'nate1280' core/` | No matches |
| C | `rg -n -e 'excluded_platforms' -e 'add_companion_identities' modules/` | Only the intended identity registration in `modules/streamerbot/__init__.py:2584`; no module passes exclusions |

The reproduction commands in NEW FINDINGS use `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tests .venv/bin/python -` with the shown script on standard input. They ran independently of S. They assert the observed defect so the reproduction process exits zero; that does not mean the guarantee passes. No repository source or test was patched.

## REQUIREMENT MAP

“PASS” means the stated automated contract passed on this checkout; it does not establish real-instance behaviour. Each listed test ran in S. FAIL distinguishes a failing review guarantee from a green but incomplete regression test.

| Requirement | Named running test(s) | Command and assessment |
| --- | --- | --- |
| R1 — optionality | `tests/test_input_ownership.py::test_ac1_main_check_config_exits_zero_with_the_pre_phase_output`; `tests/test_input_ownership.py::test_ac2_the_shipped_profiles_have_zero_changed_lines_since_the_phase_base`; `tests/test_automation_bridge.py::test_ac3_an_unreachable_instance_leaves_a_ready_companion_and_a_degraded_bridge` | S, P: **PASS**, including both run-action and viewer-read unreachable cases and final degraded state |
| R2 — preparation ownership | `tests/test_input_ownership.py::test_ac7_two_default_declarers_are_refused_before_any_effect`; `tests/test_input_ownership.py::test_ac8_an_owner_list_of_two_is_refused_naming_both`; `tests/test_config_ui.py::test_p5_ac10_the_core_page_shows_the_resolved_ownership_read_only` | S: **PASS** for resolution, refusal before preparation and read-only display; diagnostic confidentiality fails separately under R6/F4 |
| R3 — runtime ownership | `tests/test_input_ownership.py::test_ac13_a_declared_module_never_delivers_a_pair_it_does_not_own`; `tests/test_input_ownership.py::test_ac44_an_undeclared_publisher_is_refused_a_resolved_or_none_pair`; `tests/test_input_ownership.py::test_ac14_the_real_users_and_bridge_split_the_viewer_read` | S tests PASS; requirement **FAIL**, F1 bypasses the guard through a public context surface |
| R4 — honest outcomes | `tests/test_automation_bridge.py::test_ac16_row_acknowledged_is_execution_unconfirmed`; `tests/test_automation_bridge.py::test_d8_a_pre_write_failure_sends_nothing_and_withdraws_readiness`; `tests/test_automation_bridge.py::test_d8_canary_the_installed_client_writes_before_its_first_suspension` | S outcome rows PASS on installed aiohttp 3.14.3; approved D8 protection **FAIL**, F2 |
| R5 — allowlist and authorization | `tests/test_automation_bridge.py::test_ac18_only_allowlisted_names_are_reachable`; `tests/test_automation_bridge.py::test_ac20_reconciliation_names_the_unmatched_entries_once`; `tests/test_automation_bridge.py::test_ac21_a_principal_without_a_rule_is_refused_before_the_provider`; `tests/test_automation_bridge.py::test_ac45_every_matching_group_entry_must_accept` | S: **PASS** |
| R6 — connection, bounds and credential safety | `tests/test_automation_bridge.py::test_ac24_the_challenge_answer_and_readiness_only_after_the_inventory`; `tests/test_automation_bridge.py::test_ac26_a_full_queue_drops_and_counts_and_never_stalls_the_connection`; `tests/test_automation_bridge.py::test_ac4_each_failure_mode_degrades_with_its_reason_and_leaks_nothing` | S tests PASS; requirement **FAIL**, F4 exposes the password in preparation diagnostics |
| R7 — cues and replacement mappings | `tests/test_automation_bridge.py::test_ac29_a_valid_cue_is_one_normalised_event_the_default_policy_accepts`; `tests/test_automation_bridge.py::test_ac43_each_fixture_maps_to_the_native_kind_and_key_set`; `tests/test_automation_bridge.py::test_ac34_the_owned_channel_reads_one_page_of_active_viewers`; `tests/test_brain.py::test_ac31_a_cue_only_effect_route_passes_route_safety_at_prepare` | S: **PASS** for specified normalization, ownership and action contracts; the P11 consumer-audit closure and operator claim fail in F3 |
| R8 — evidence and trial gate | `tests/test_automation_bridge.py::test_ac35_every_frame_carries_a_provenance_and_no_question_is_answered`; `tests/test_phase5_docs_status.py::test_ac36_the_trial_file_skips_its_eight_nodes_opening_no_socket`; `tests/test_phase5_docs_status.py::test_ac37_no_document_calls_the_bridge_delivered` | S, T, D: **PASS** for declared uncertainty and trial/status safeguards. Real-instance answers and eventual tracking of this report are **NOT-VERIFIABLE** in this pass |
| R9 — configuration combinations | `tests/test_automation_bridge.py::test_ac39_each_fragment_checks_as_the_guide_says`; `tests/test_automation_bridge.py::test_ac39_combination_8_the_brain_host_finds_the_run_action_in_the_catalog`; `tests/test_automation_bridge.py::test_ac40_the_table_has_twelve_rows_with_every_cell_filled` | S: **PASS**, all twelve fragments and the agent-placement restriction |
| R10 — static constraints | `tests/test_input_ownership.py::test_ac11_no_file_under_core_names_the_vendor_or_the_bridge_module`; `tests/test_profiles.py::test_installed_distribution_discovers_the_shipped_manifests_and_answers_help`; `tests/test_automation_bridge.py::test_ac41_dependencies_are_unchanged_and_the_manifest_ships`; `tests/test_automation_bridge.py::test_ac42_the_guide_is_a_declared_bilingual_pair` | S, V: **PASS**, 18 packaged manifests, unchanged aiohttp/PyYAML runtime dependencies, bilingual guide |

### Acceptance-criterion crosswalk

All test references below ran in S, with passing assertions. `A::` expands to `tests/test_automation_bridge.py::` and `O::` to `tests/test_input_ownership.py::`. These passing examples do not override F1–F4.

| Criteria | Running enforcement |
| --- | --- |
| AC1 | `O::test_ac1_check_config_gives_the_pre_phase_diagnostics_with_the_sample_values`; `O::test_ac1_main_check_config_exits_zero_with_the_pre_phase_output` |
| AC2 | `O::test_ac2_no_shipped_profile_names_the_bridge_or_an_inputs_key`; `O::test_ac2_the_shipped_profiles_have_zero_changed_lines_since_the_phase_base` |
| AC3 | `A::test_ac3_an_unreachable_instance_leaves_a_ready_companion_and_a_degraded_bridge`, both parameter cases |
| AC4 | `A::test_ac4_each_failure_mode_degrades_with_its_reason_and_leaks_nothing` |
| AC5 | `O::test_ac5_the_bridge_settings_schema_has_no_required_key`; `O::test_ac5_a_twitch_event_is_dropped_while_the_owning_bridge_is_down` |
| AC6 | `O::test_ac6_shape_default_owners_and_no_chat_pair_for_the_bridge` |
| AC7 | `O::test_ac7_two_default_declarers_are_refused_before_any_effect`; `O::test_ac7_end_to_end_check_config_exits_2_with_one_diagnostic_and_no_socket` |
| AC8 | `O::test_ac8_an_owner_list_of_two_is_refused_naming_both`; `O::test_ac8_an_owner_that_is_not_enabled_is_refused`; `O::test_ac8_an_owner_that_does_not_declare_the_platform_is_refused`; `O::test_ac8_a_family_outside_the_vocabulary_names_family_platform_and_value` |
| AC9 | `O::test_ac9_the_native_view_is_empty_and_the_owner_view_holds_the_pair`; `O::test_ac9_none_leaves_the_pair_unowned` |
| AC10 | `tests/test_config_ui.py::test_p5_ac10_the_core_page_shows_the_resolved_ownership_read_only`; `tests/test_config_ui.py::test_p5_ac10_check_shows_the_ac7_diagnostic_unmodified` |
| AC11 | `O::test_ac11_no_file_under_core_names_the_vendor_or_the_bridge_module`; `O::test_ac11_the_ownership_code_has_no_platform_name_literal` |
| AC12 | `tests/test_twitch.py::test_chat_owned_elsewhere_opens_no_socket_and_keeps_chat_write` |
| AC13 | `O::test_ac13_a_declared_module_never_delivers_a_pair_it_does_not_own` — incomplete coverage: F1 |
| AC14 | `O::test_ac14_the_real_users_and_bridge_split_the_viewer_read`; `O::test_the_gated_bindings_never_overlap` |
| AC15 | `A::test_ac15_the_discovered_run_action_spec_is_exactly_the_r4_contract`; `A::test_ac15_the_argument_schema_accepts_an_action_and_refuses_the_rest` |
| AC16 | The ten `A::test_ac16_row_*` nodes listed in the outcome table below |
| AC17 | `A::test_ac17_a_late_acknowledgement_is_lost_and_never_re_invoked`; `A::test_ac17_an_answer_landing_while_the_timer_is_cleaned_up_is_lost` |
| AC18 | `A::test_ac18_only_allowlisted_names_are_reachable`; `A::test_ac18_an_empty_or_absent_allowlist_refuses_everything`; `A::test_ac18_a_group_entry_reaches_its_actions_only` |
| AC19 | `A::test_ac19_the_entry_schema_decides_the_arguments` |
| AC20 | `A::test_ac20_reconciliation_names_the_unmatched_entries_once` |
| AC21 | `A::test_ac21_a_principal_without_a_rule_is_refused_before_the_provider` |
| AC22 | `A::test_ac22_loopback_ws_and_any_wss_are_accepted`; `A::test_ac22_plain_ws_to_a_non_loopback_host_is_refused_without_the_url`; `A::test_ac22_a_missing_url_is_refused_with_one_diagnostic`; `A::test_ac22_a_non_positive_or_non_finite_deadline_is_refused` |
| AC23 | `A::test_ac23_every_setting_node_has_a_title_and_password_is_a_credential`; trace/diagnostic/log sweep in the AC4 node and AC16 scenario teardown — incomplete outside those scenarios: F4 |
| AC24 | `A::test_ac24_the_challenge_answer_and_readiness_only_after_the_inventory` |
| AC25 | `A::test_ac25_a_lost_connection_degrades_at_once_and_reconnects_after_the_minimum`; `A::test_ac25_the_backoff_doubles_from_the_minimum_and_is_capped_at_the_maximum` |
| AC26 | `A::test_ac26_a_full_queue_drops_and_counts_and_never_stalls_the_connection` |
| AC27 | `A::test_ac27_owning_only_cues_subscribes_to_the_custom_category_only`; `A::test_ac27_owning_twitch_chat_adds_only_twitch_chat_and_notices` |
| AC28 | `A::test_ac28_cancelling_the_owner_task_propagates_and_releases_the_connection`; `A::test_ac28_shutdown_closes_within_budget_and_fails_a_pending_request`; `A::test_ac28_a_run_in_flight_at_shutdown_is_acknowledgement_lost` |
| AC29 | `A::test_ac29_a_valid_cue_is_one_normalised_event_the_default_policy_accepts`; `A::test_ac29_a_configured_policy_under_the_bridge_decides_its_cues` |
| AC30 | `A::test_ac30_a_cue_without_user_has_one_stable_author_unlike_the_companion`; `A::test_ac30_a_malformed_or_oversized_envelope_is_dropped_and_counted` |
| AC31 | `tests/test_brain.py::test_ac31_a_cue_only_effect_route_passes_route_safety_at_prepare` |
| AC32 | `A::test_ac32_instance_roles_satisfy_an_audience_only_when_trusted`; AC43 shape comparison |
| AC33 | `A::test_ac33_check_config_refuses_owned_twitch_chat_without_an_identity`; `A::test_ac33_a_mapped_chat_event_authored_by_the_companion_is_refused` |
| AC34 | `A::test_ac34_the_owned_channel_reads_one_page_of_active_viewers`; `A::test_ac34_another_channel_is_refused_unsupported_destination`; `tests/test_users.py::test_pages_still_report_observed_authors` |
| AC35 | `A::test_ac35_every_frame_carries_a_provenance_and_no_question_is_answered`; `A::test_ac35_the_guard_rejects_each_violation` |
| AC36 | `tests/test_phase5_docs_status.py::test_ac36_the_trial_file_skips_its_eight_nodes_opening_no_socket`, plus T |
| AC37 | `tests/test_phase5_docs_status.py::test_ac37_readme_and_index_state_the_bridge_not_delivered`; `tests/test_phase5_docs_status.py::test_ac37_no_document_calls_the_bridge_delivered` |
| AC38 | This report is created by this gate. Tracking it is **NOT VERIFIED**: the operator explicitly forbids staging or committing. No running test enforces its tracking |
| AC39 | `A::test_ac39_each_fragment_checks_as_the_guide_says`; `A::test_ac39_combination_8_the_brain_host_finds_the_run_action_in_the_catalog` |
| AC40 | `A::test_ac40_the_table_has_twelve_rows_with_every_cell_filled` |
| AC41 | `A::test_ac41_dependencies_are_unchanged_and_the_manifest_ships`; `tests/test_profiles.py::test_installed_distribution_discovers_the_shipped_manifests_and_answers_help` |
| AC42 | `A::test_ac42_the_guide_is_a_declared_bilingual_pair`; `tests/test_docs_bilingual.py` |
| AC43 | `A::test_ac43_each_fixture_maps_to_the_native_kind_and_key_set`; `A::test_ac43_owning_chat_each_fixture_gives_one_event_and_unmapped_is_dropped`; `A::test_ac43_owning_only_twitch_the_kick_and_youtube_fixtures_give_nothing` |
| AC44 | `O::test_ac44_an_undeclared_publisher_is_delivered_when_nobody_owns_the_pair`; `O::test_ac44_an_undeclared_publisher_is_refused_a_resolved_or_none_pair` (v1 and v2); `O::test_ac44_the_integration_scenarios_count_zero_ownership_rejections` |
| AC45 | `A::test_ac45_duplicate_action_or_group_entries_are_refused`; `A::test_ac45_the_action_entry_beats_the_group_entry`; `A::test_ac45_every_matching_group_entry_must_accept` |

The ten AC16 tests, all under `tests/test_automation_bridge.py`, assert exact status/code/cause, non-retryability and outgoing DoAction counts:

| Test name | Terminal status / code / cause | Frames |
| --- | --- | --- |
| `test_ac16_row_not_allowlisted` | refused / not_allowlisted / absent | 0 |
| `test_ac16_row_instance_action_missing` | refused / instance_action_missing / absent | 0 |
| `test_ac16_row_instance_action_ambiguous` | refused / instance_action_ambiguous / absent | 0 |
| `test_ac16_row_instance_action_disabled` | refused / instance_action_disabled / absent | 0 |
| `test_ac16_row_invalid_arguments` | error / invalid_arguments / absent | 0 |
| `test_ac16_row_bridge_not_ready` | refused / provider_not_ready / absent | 0 |
| `test_ac16_row_acknowledged_is_execution_unconfirmed` | external_unknown / external_effect_unknown / execution_unconfirmed | 1 |
| `test_ac16_row_instance_error_answer` | error / provider_failed / instance_rejected | 1 |
| `test_ac16_row_no_acknowledgement_before_the_deadline` | external_unknown / external_effect_unknown / acknowledgement_lost | 1 |
| `test_ac16_row_cancelled_before_the_frame_left` | cancelled / cancelled / absent | 0 |

D8's additional executed nodes also pass: `test_d8_the_executor_deadline_before_the_frame_left_is_a_timeout` (timeout/timed_out, zero frames), `test_d8_a_connection_closed_before_the_segment_is_provider_not_ready`, `test_d8_a_pre_write_failure_sends_nothing_and_withdraws_readiness` (both refused/provider_not_ready, zero frames), `test_d8_cancelling_after_the_segment_is_acknowledgement_lost`, and `test_d8_an_interruption_in_the_drain_after_the_write_is_acknowledgement_lost` (cancellation and executor deadline, external_unknown with one frame). No acknowledged run reports success or ok as its action outcome. F2 concerns the required detection of a future writer change, not a failure of the currently installed writer.

## GUARANTEES

| Guarantee | Result and executed evidence |
| --- | --- |
| Total optionality | **PASS** — S/AC1–5: unchanged four profiles (P), disabled configuration does not resolve unused bridge settings, absent bridge preserves existing integrations, unreachable bridge leaves the companion ready and its run/read unavailable without fallback; no schema required key. V finds no kernel name/import |
| One owner per family/platform | **FAIL** — S refuses two named owners and two same-specificity defaults before preparation/sockets, exercises v1 compatibility and concrete-over-wildcard D2 precedence, and proves non-overlapping viewer bindings; F1 nevertheless delivers a non-owner publication without counting/tracing its rejection |
| Honest silence | Current installed behaviour **PASS** — S's ten-row table and D8 nodes above exercise pre-write failure, blocked-hook cancellation/deadline, lost acknowledgement, late acknowledgement and post-write drain interruption. Upgrade protection **FAIL**, F2 |
| Allowlist before network | **PASS** — S/AC18–21/45 tests outside-name, group non-member, missing, disabled and ambiguous names with zero DoAction frames; matching group entries remain authorized, as R5 requires. No actions grant gives zero provider invocations |
| No secret value escapes | **FAIL** — standard startup/action sweeps and UI secret tests pass in S; F4 leaks the configured credential through a new ownership diagnostic |
| Not delivered | **PASS** — D passes; trial record is absent; README and docs index say “not delivered”; an additional `rg -n -i '\bdelivered\b' README.md docs --glob '*.md' --glob '!docs/campaigns/**'` inspection finds only negated bridge claims |
| Vendor-free kernel | **PASS** — V returns no matches; S/AC11 also checks ownership string literals for platform names |
| Installed aiohttp canary | **FAIL** — installed aiohttp is 3.14.3 and its writer currently writes before suspension; F2's executed in-memory writer change passes the canary while suspending before any write |

### Phases 0–4 regression guarantees

S ran the complete tests directory, including the following named enforcement. These remain green; F3 is the newly introduced cue interaction with the memory path.

| Guarantee | Executed evidence in S |
| --- | --- |
| Default deny, including reads | `tests/test_actions.py::test_zero_rules_refuse_a_read_and_a_write_with_zero_invocations`; `tests/test_users.py::test_read_without_a_rule_is_refused_and_the_provider_is_not_entered` |
| Bounded admission and retention | `tests/test_admission.py::test_full_session_queue_and_global_cap_reject_with_reason_and_depth`; `tests/test_retention.py::test_byte_bound_evicts_oldest_first_and_is_never_exceeded`; bus, audit, dedup and attachment retention suites |
| Explicit terminal outcomes | `tests/test_actions.py::test_a_silent_write_defaults_to_external_unknown_on_timeout`; `tests/test_actions.py::test_a_confirmation_landing_after_the_deadline_is_never_a_success` |
| One global startup/shutdown deadline | `tests/test_main.py::test_a_hanging_settings_validator_is_bounded_by_the_startup_deadline`; `tests/test_lifecycle.py::test_start_honours_a_deadline_established_before_the_phases`; `tests/test_lifecycle.py::test_one_global_shutdown_deadline_caps_the_sum_of_local_timeouts` |
| Existing secret redaction | `tests/test_contracts.py::test_secret_never_reaches_a_trace_diagnostic`; `tests/test_config_ui.py::test_ac35_no_secret_value_reaches_any_response_log_report_or_record`; new diagnostic exception is F4 |
| Phase 2 R10 deadline ruling | `tests/test_actions.py::test_the_timer_winning_adopts_the_record_of_a_provider_that_answers_its_cancel`; `tests/test_actions.py::test_a_late_success_is_still_replaced_by_the_generic_record`; `tests/test_actions.py::test_a_provider_that_never_answers_is_cut_by_the_timer_at_expiry` |
| No default provider | `tests/test_profiles.py::test_check_config_passes_with_the_speech_endpoints_unset_then_configured`; `tests/test_profiles.py::test_ac43_with_no_speech_endpoint_audio_speak_is_named_not_ready_with_0_requests`; the existing profile/default checks |
| Moderation modes and no permanent bans | `tests/test_moderation.py::test_ac24_the_default_mode_alerts_with_no_platform_request_and_one_fact`; `tests/test_moderation.py::test_ac26_auto_apply_applies_a_delete_at_once_under_the_strict_rules`; `tests/test_moderation.py::test_no_operation_removes_a_viewer_permanently` |
| Viewer-memory bounds and eviction | `tests/test_viewer_memory.py::test_ac14_eviction_deletes_the_least_recent_then_least_used_with_one_fact`; `tests/test_viewer_memory.py::test_ac15_the_total_bound_holds_and_the_target_is_never_deleted`; failed-eviction regressions |
| Requested-only watch input | `tests/test_watch.py::test_ac29_without_activation_no_tick_is_emitted`; `tests/test_watch.py::test_ac29_a_viewers_start_command_starts_nothing`; explicit startup activation remains opt-in |
| Honest platform capabilities | Full twitch/kick/youtube suites, including `tests/test_youtube.py::test_chat_owned_elsewhere_polls_nothing_and_keeps_chat_write`, failed on-demand lookup outcomes, and native moderation/clip limitations |
| Local-only configuration UI; secrets/actions read-only | `tests/test_config_ui.py::test_ac20_secrets_and_actions_are_read_only_with_every_rule`; local bind/origin/host/session protection and save-scope tests in that suite |

The seven core targets are accepted: D5 approves core/actions.py and D6 approves core/triggers.py beyond the specification's five. They are not findings. D10's invariant passes `tests/test_lifecycle.py::test_a_state_the_module_reported_is_not_overwritten_by_its_phase` and the final-state assertion of AC3. D11 passes `tests/test_actions.py::test_an_executor_take_over_keeps_the_providers_cause`, `test_a_take_over_without_a_cause_keeps_todays_record_exactly`, `test_a_shipped_confirmation_lost_cause_survives_a_take_over` and the original R10 cases: status/code are unchanged, and causes survive takeover/conversion. D9 passes the new string-bound tests, including rejection on non-string schemas and continued refusal of unsupported keywords. Missing companion_name is refused by the settings hook in `tests/test_automation_bridge.py::test_a_bridge_without_companion_name_is_refused_by_the_hook_not_the_triggers`.

### Adapted tests and remaining integration checks

Compared every one of the seven specification allowlist entries against the cumulative baseline diff. None loses its original safety assertion:

- The two contract tests retain exact vocabulary/order and exact seven notice kinds, adding cue and its exclusion.
- The examples manifest test retains exact sets, declarations and credentials; 18 manifests and 14 actions replace 17/13. The approved P12 credential/action assertions remain exact.
- The presentation and two profile tests retain exact shipped/package/discovered sets and unchanged runtime-dependency assertions.
- The users test retains the original provider's observed_authors constant and adds active_viewers to the shared enum; separate running page tests still require observed_authors.

Other adaptations were inspected too: the existing examples chat-only catalog assertion changes its exact action count/set while preserving its ready-provider view; the bilingual GUIDES registration adds the guide; the demo expects nine diagnostic counters rather than eight (the P1 blind-spot record identifies the R3 reason); the UI module count becomes 18. The UI helper also excludes the pytest temporary base directory from its credential-canary scan to avoid incidental matches in numbered temporary paths; this is not one of the seven allowlist entries. No weakened assertion in those seven is reported as a finding.

The new queue uses put_nowait; AC26 proves response traffic continues after overflow. Native mapping comparisons call the native normalizers rather than compare two bridge-owned copies. Wildcard exclusions and the actual users/bridge binding split pass. C found no unintended module callers. The R6 endpoint/port/password/version/action grep was inspected: runtime endpoint/password/action names have no defaults; test-only configured values, reserved example addresses, protocol/action versions and ephemeral fake-server endpoints are explicit fixtures, not assumed instance defaults.

P11's audit is recorded in `scaffolding/loop-out/phase5-p11/01_build.json`. Moderation's event index and watch commands ignore cues; the bridge does not append cues to chat_context; users ignores their authors. The audit also explicitly leaves the brain's post-run memory.record path for P22. F3 executes that unresolved path; it cannot be treated as closed by the event-consumer checks alone.

## NEW FINDINGS

### F1 — HIGH: public supervision publication bypasses input ownership

Locations: `core/runtime.py:1128` (ModuleSupervision.emit), `core/runtime.py:1137` (record_and_emit), and `core/runtime.py:1503` (for_module wiring).

Only bus/triggers/chat receive the shared ownership guard. ModuleSupervision delegates both publication methods to Supervision, whose publisher holds the unguarded bus. A module needs no private attribute or forged source to bypass the new rule. Its ordinary public supervision surface accepts channel.chat.message.

Executed reproduction:

~~~python
import asyncio
from test_input_ownership import _guarded_runtime, _intruder, _chat_event

async def main():
    runtime, _, _ = _guarded_runtime()
    context = _intruder(runtime)  # owns chat/y; native owns chat/x
    seen = []
    runtime.bus.subscribe("channel.chat.message", lambda event: seen.append(event))
    event = _chat_event("x")
    await context.bus.publish(event["type"], event["payload"], event["metadata"])
    print(len(seen), context.supervision.snapshot()["input_ownership_rejections"])
    await context.supervision.emit(
        event["type"], event["payload"], metadata=event["metadata"])
    print(len(seen), context.supervision.snapshot()["input_ownership_rejections"])
    await context.supervision.record_and_emit(
        lambda: None, event["type"], event["payload"], metadata=event["metadata"])
    print(len(seen), context.supervision.snapshot()["input_ownership_rejections"])
    assert len(seen) == 2

asyncio.run(main())
~~~

Observed `0 1`, then `1 1`, then `2 1`. The two bypassed publications reach the subscriber, neither increments the rejection counter nor adds its rejection diagnostic. This violates R3's all-subscriber guarantee even though AC13's bus-only example passes. Close every public module publication route over the same guard, or forbid input publication through supervision; cover both methods.

### F2 — MEDIUM: the installed-writer canary ignores implicit suspension

Location: `tests/test_automation_bridge.py:2694`.

The canary searches for ast.Await and pre-write raises. An async-with acquisition suspends without an ast.Await node. The following in-memory change to the installed send_frame source passes the existing canary but blocks before _write_websocket_frame. This is precisely a writer change D8 requires to fail loudly. The actual installed writer is currently sound; the defect is its regression protection.

Executed reproduction:

~~~python
import asyncio, inspect, textwrap
from types import SimpleNamespace
from unittest.mock import patch
from aiohttp._websocket.writer import WebSocketWriter
from test_automation_bridge import (
    test_d8_canary_the_installed_client_writes_before_its_first_suspension as canary,
)

canary()  # current installed writer passes
original = inspect.getsource
source = textwrap.dedent(original(WebSocketWriter.send_frame))
needle = "        self._write_websocket_frame(message, opcode, 0)"
assert source.count(needle) == 1
changed = source.replace(needle, "        async with self._send_lock:\n    " + needle)
with patch.object(inspect, "getsource", side_effect=lambda fn:
        changed if fn is WebSocketWriter.send_frame else original(fn)):
    canary()  # unexpectedly passes
namespace = dict(WebSocketWriter.send_frame.__globals__)
exec(compile(changed, "<in-memory-canary-counterexample>", "exec"), namespace)

async def main():
    writes = []
    lock = asyncio.Lock()
    await lock.acquire()
    writer = SimpleNamespace(
        _closing=False, compress=0, _send_lock=lock,
        _write_websocket_frame=lambda *args: writes.append(args),
        _output_size=0, _limit=1000,
    )
    task = asyncio.create_task(namespace["send_frame"](writer, b"frame", 1))
    await asyncio.sleep(0)
    print("writes:", len(writes), "pending:", not task.done())
    assert not writes and not task.done()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

asyncio.run(main())
~~~

Observed: original canary PASS; changed canary PASS; `writes: 0 pending: True`. With this library behaviour the provider would already have marked emission while the frame had not left. Add a behavioural check of the actual uncompressed client/writer path and negative controls for pre-write suspension, including implicit awaits; keep the loud fallback diagnostic.

### F3 — MEDIUM: a cue about a viewer is recorded as that viewer's speech

Locations: `modules/brain/__init__.py:3277` (memory eligibility), `modules/brain/__init__.py:3368` (note construction); conflicting guide claim at `docs/automation-bridge.md:258–260` and its translation.

The guide says viewer_memory acts on ordinary messages only. P11's audit explicitly warned that the brain's post-run recording is separate from viewer_memory's bus handler. The current eligibility check excludes reserved colon identities, so anonymous instance cues are safe, but a valid cue with user_id enters memory.record with the operator's cue text as viewer_text. A successful reply is also attached as a confirmed viewer exchange.

Executed through the real bridge normalizer, brain and executor, with a recording memory provider (no persistent files written):

~~~python
import asyncio
from dataclasses import replace
import test_brain as b
from modules.streamerbot import cue_event

async def main():
    factory = b.owned_rules(
        replace(b.MEMORY_RECORD_PROPOSABLE_SPEC, model_proposable=False),
        rules=[b.BRAIN_GRANT, b.grant_to(b.MEMORY_RECORD, b.PRINCIPAL)],
    )
    h = await b.activate_with(
        b.FakeResponse(200, b.completion("Back soon.")), context_factory=factory)
    try:
        p = b.chat_payload("")
        envelope = dict(
            cue_id="cue-memory-1", platform=p["platform"], channel_id=p["channel_id"],
            user_id=p["author"]["id"], user_name="Viewer", text="!brb")
        e = cue_event({
            "event": {"source": "Custom", "type": "Event"},
            "data": {"args": envelope},
        }, {})
        await h.bus.publish(e["type"], e["payload"], e["metadata"])
        (record,) = await h.completed()
        print(e["payload"]["kind"], record.status)
        calls = factory.providers[b.MEMORY_RECORD].calls
        print(calls)
        assert calls == [{
            "viewer_text": "!brb", "delivery": "confirmed", "reply_text": "Back soon.",
        }]
    finally:
        await h.close()

asyncio.run(main())
~~~

Observed `cue success` and exactly the asserted memory-record call. This attributes automation instructions to a viewer and contradicts the operator guide. Resolve the P11 finding by excluding cues from automatic viewer-memory recording, with an end-to-end regression for both user-bearing and instance-author cues. If attribution is deliberately retained, it needs an explicit operator decision and corrected documentation; the current claim cannot stand.

### F4 — MEDIUM: ownership diagnostics echo a configured credential used as a key

Locations: `core/loader.py:2444` (_inputs_line), `core/loader.py:2501` onward (invalid family/platform diagnostics).

Ownership resolution deliberately runs before credential registration/redaction. It receives a redacted set and protects owner values, but family/platform keys are quoted without that protection. A value copied from the configured password into either key leaks into the resulting ModuleLoadError. These diagnostics are the ones startup/Check report. This is a new exception to the existing redaction guarantee.

Executed with a synthetic configured credential, no socket or activation:

~~~python
import asyncio
from pathlib import Path
from core.loader import ModuleLoader, ModuleLoadError
from conftest import runtime_context

async def main():
    secret = "review-secret-4521"
    for inputs in (
        {secret: {"x": "streamerbot"}},
        {"chat": {secret: "not-enabled"}},
    ):
        runtime = runtime_context()
        loader = ModuleLoader(
            runtime.bus, Path("modules"), context=runtime, environ={})
        try:
            await loader.activate_enabled({
                "enabled_modules": ["streamerbot"],
                "modules": {"streamerbot": {
                    "url": "ws://127.0.0.1:9/",
                    "companion_name": "Companion", "password": secret,
                }},
                "inputs": inputs,
            })
        except ModuleLoadError as error:
            print(str(error))
            assert secret in str(error)
            assert loader.activations == []
        else:
            raise AssertionError("expected refusal")

asyncio.run(main())
~~~

Observed:

~~~text
inputs: family 'review-secret-4521': platform 'x': value 'streamerbot': family is outside the vocabulary (chat, viewers, cues)
inputs: family 'chat': platform 'review-secret-4521': owner 'not-enabled' is not enabled
~~~

Both expose the complete configured credential with zero modules activated. Redact complete ownership diagnostics using the known credential set, including keys and embedded values, before returning them from the resolver; retain value-free field identification and test the startup/Check routes.

## NOT VERIFIED

- No real instance was available or contacted. The eight study questions remain unanswered: nonexistent-name behaviour, real latency/blocking queues, enforce-mode handling, minimum request versions, C# HTTP/JSON, custom-event round trip, Wine servers and event volume. T intentionally skipped all eight; fixture provenance is not observed compatibility.
- No confirmation round trip was added or assumed (A6). No separate presence family was demanded (A4).
- Real credentials, an operator's external logs, live inventories and native-platform services were not inspected. Network assertions use the repository's fake servers.
- The F2 mutation models a possible library change; it is not a claim that installed aiohttp 3.14.3 currently suspends before writing.
- F3 demonstrates the emitted memory.record call with a recording provider; it does not write an actual viewer-memory file.
- Formal tracking of this report (AC38) is pending the operator's later staging/commit. Only this report is written; no git add, commit or fix is performed. The phase-closing gate remains blocked by F1–F4.
