# Phase 6 — full-branch closing gate, third pass

## VERDICT: REQUEST_CHANGES

Reviewed `17be55df58338f59de229f337759d9b69cb17a07` on 2026-10-19 against pre-fix `8010edc`, first-gate `0f335bc`, and second-pass product head `8f0bd26`.

**All 34 in-scope product/test findings F1–F33 and F35 remain CLOSED; N1–N3 remain CLOSED. Probes A–E pass. The amended executor behavior of AC2 passes. A1 remains OPEN solely because this pass's explicit documentary absence check fails.** There is no reason to add another action event. There is also no basis to certify that the old event name is absent from the phase documents: the executed search finds it in two still-prescriptive test clauses as well as historical quotations. This is a minor documentation conformity issue, not a reopened executor-routing defect.

Only this report was created or modified by this review in the repository. Nothing was staged, committed, checked out, or changed in source/tests. Initial status already contained a modified `scaffolding/campaign-summary.md` and an untracked `scaffolding/gate-prompt-3.md`; both were left untouched. No `AGENTS.md` was found in the repository or ancestor directories. Bytecode and pytest cache writes were disabled. Ordinary test fixtures used temporary directories outside the repository; independent source mutations and virtual probe fixtures stayed in memory. The sandbox launcher failed before executing the first command (`bwrap: loopback: Failed RTM_NEWADDR`); inspection and execution then used approved escalation.

## AC2: FAIL for the complete third-pass check; amended runtime criterion PASS

The three requested checks have different observed results:

1. **Specification PASS.** `spec.md:206` now requires exactly one `action.started` and one `action.completed`. A1 records the explicit decision changing the criterion; this review does not substitute one name for another on its own authority.
2. **Named test and behavior PASS.** `tests/test_twitch.py::test_ac2_the_compatibility_send_is_traced_as_an_executor_call` ran both directly and in the 70-node ledger selection. Immediately after the compatibility publication and before its direct-call control it destructures `(started,) = events_of(bus, "action.started")` and `(completed,) = events_of(bus, "action.completed")`. Singleton unpacking fails for zero or multiple events. It checks the compatibility principal on the start, Twitch destination/action on both, provider, successful completion, matching call ID, and start/completion/sent ordering. After the separate direct executor call, it checks equal trace shapes, distinct call IDs, and exactly two of each trace in total.
3. **No obsolete name anywhere in phase documents/code: FAIL.** The search below returned exit 0 and **16 matching lines in seven phase-document files**, before this report was written. There are no matches in `core/`, `modules/`, or `tests/`. An additional direct scan of Python/YAML/JSON code/test files also returned empty hit lists for each of those three trees.

```text
rg -n 'action\.requested' docs/campaigns/phase6 core modules tests

phase-document matching-line counts:
  plan.md                                      1
  scaffolding/steps/P2-spec.md                   1
  spec.md                                      1
  scaffolding/plan-amendments.md                 5
  full-branch-review.md                         1
  full-branch-review-2.md                        2
  scaffolding/gate-prompt-3.md                   5
Literal zero-occurrence assertion: FAIL
core hits: []   modules hits: []   tests hits: []
```

In particular, `plan.md:102` and `scaffolding/steps/P2-spec.md:77` still prescribe **“AC2 — exactly 1 `action.requested` and 1 `action.completed`”** without a local amendment annotation. These are not merely quotations explaining the correction. The spec's parenthetical, A1, old reports and current gate prompt also quote the obsolete name as history or review instructions. Even if those historical quotations were exempted, the two prescriptive occurrences remain. I have not silently narrowed “anywhere” to “product code” or edited the other documents despite the one-file scope.

**Amendment judgment:** the event-name correction is sound. `core/contracts.py:149` defines `TRACE_ACTION_STARTED = "action.started"`; the fresh executor path emits it at `core/actions.py:1474` and emits the terminal completion. The line cited in A1, `core/actions.py:1361`, is a start emission on the reuse-refusal path, also using the same canonical event. At `8010edc` the kernel already used this name. The acceptance criterion's “same traces an executor call produces” supports the correction. Adding a redundant event does not repair these stale documents.

To close A1, align the two prescriptive plan clauses and make the absence condition consistent with retaining historical amendment/review quotations (or satisfy its literal wording). No product/test change is requested. This report records the failed check; it necessarily quotes the searched token too.

## Observed runs

```text
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/ -q -p no:cacheprovider
3901 passed, 26 skipped in 275.18s (0:04:35)

70 distinct regression nodes extracted from the finding ledger in summary.md:
133 passed in 9.64s

AC2 direct invocation: PASS
Independent A/B/C/D replays: PASS boundaries (outputs below)
E held-completion replay with pre-teardown inspection: 1 passed in 0.16s
F28 unmodified, alone, -p no:randomly: 1 passed in 0.37s
```

The ledger selection actually ran every named finding node and every parametrization, not only collected them. “Ledger PASS” below refers to this fresh 133-case run. It printed the same cancellation-resistant pending-task teardown message from the F35 stalled-cleanup fixture as the second pass, after all tests passed; no new product regression was demonstrated by that message.

## Replayed probes A–E

**A — retained identity and authorization, F2/F3: PASS.** Used the real-agent/fake-peer `test_proxy.recorded_host()` harness, first completing the original granted call, then applying each alteration separately, their combination, and the exact retry:

```text
A outside   refused unknown_action    retained_equal=false result_present=false parts=[] executor_calls=1
A principal refused not_authorized    retained_equal=false result_present=false parts=[] executor_calls=1
A arguments refused call_id_conflict  retained_equal=false result_present=false parts=[] executor_calls=1
A combined  refused unknown_action    retained_equal=false result_present=false parts=[] executor_calls=1
A exact     success                   retained_equal=true  result_present=true  parts=[] executor_calls=1
```

The count includes the original execution. No altered retry entered the executor or obtained a retained result.

**B — no peer string in supervision, F5: PASS.** Executed `brain.call('gate-token', 2, run_id=TOKEN)` against the real executor and collected every bus record and diagnostic:

```text
status=success result_returns=true leaking_records=0 leaking_diagnostics=0
action.started:   action=screen.capture action_version=1 provider=capture
                  call_id=gate-token destination=fake/channel-9/capture
                  nature=read principal=brain
action.completed: action=screen.capture action_version=1 provider=capture
                  call_id=gate-token destination=fake/channel-9/capture
                  status=success duration_seconds=0.0 error_code=None
both: run_id=peer-run_id-61ae1868f736f991
      conversation_id=peer-conversation_id-49e8468b5e3220fe
```

Labels agree between events; their digest suffixes vary by agent instance. The full suite additionally exercises all four forwarded correlation fields, pairing-token/4 KiB values, lone surrogates, rendering collisions, in-flight retries and the local-call trace control.

**C — offline consumer, F4: PASS.** Called `modules.viewer_memory.__main__.main(['stats', '--config', '/virtual/gate.yaml'])` with the original probe document provided by a mocked `Path.read_text`. No configuration file or store was created:

```text
exit=2
stdout=""
stderr="viewer_memory: module 'viewer_memory': field 'validate_settings': is not a setting this module declares\n"
leak=false
```

**D — structural fixture sensitivity, F4/N1: PASS.** Injected a virtual `core/_gate_virtual_fixture.py` through `Path.rglob`/`read_text`, invoking the actual shipped `test_ac11_every_configuration_diagnostic_surface_routes_through_the_primitive` each time. No fixture was written to the repository.

| Virtual body in `for key in settings` | Actual structural-test outcome | Violations |
|---|---|---|
| Raw f-string key path | **FAIL**, expected | `('core/_gate_virtual_fixture.py', 'check'): [4]` |
| Raw `.format(key)` key path | **FAIL**, expected | Same, `[4]` |
| Raw string concatenation key path | **FAIL**, expected | Same, `[4]` |
| Unrelated `mask_known('fixed', known)` followed by raw path | **FAIL**, expected | Same, `[5]` |
| `mask_known('settings.' + key, known)` before emitting path | **PASS**, expected | `{}` |

**E — no erased text on disk, F6: PASS.** Ran the held-completion AC14 test, wrapping only `MemoryPlatform.close` to inspect the real memory/store immediately before teardown. Read every file recursively beneath the store directory:

```json
{"brain_recall_empty": true, "viewer_memory_record_exists": false,
 "erased_text_in_any_store_file": false, "files_scanned": 0,
 "store_refusals": 2, "brain_refusals": 1}
```

The test also checks no erased text in the erasure's bus/audit records. Later same-key and other-key writes remain covered by the full-suite positive control.

## PREVIOUS FINDINGS

Test paths in the table are relative to `tests/`; a bare `::name` continues the preceding filename in that cell. All ledger nodes below ran freshly and passed. Historical pre-fix results are not represented as fresh output.

| Finding | Disposition | Named executed regressions | Fresh output / checked boundary |
|---|---|---|---|
| F1 | **CLOSED** | `test_twitch.py::test_ac1_an_ungranted_compatibility_send_is_refused_by_the_executor`, `::test_ac1_without_an_executor_the_compatibility_route_refuses`, `::test_ac2_the_compatibility_send_is_traced_as_an_executor_call` | Ledger PASS; compatibility default-deny, no-executor refusal and one start/completion pair. Product F1 stays closed despite the separate documentary check. |
| F2 | **CLOSED** | `test_proxy.py::test_p6_ac6_a_call_outside_the_negotiated_set_never_reaches_the_executor`, `::test_p6_ac7_the_frame_principal_is_compared_never_executed_under`, `::test_p6_ac7_the_default_pairing_principal_is_brain` | Ledger PASS; A: outside/combined unknown_action, other principal not_authorized; no retained result, executor_calls=1; exact retry succeeds. |
| F3 | **CLOSED** | `test_actions.py::test_ac3_a_reused_call_id_naming_another_call_is_refused` (×5 components), `::test_ac3_a_reuse_whose_arguments_only_share_a_repr_is_refused`, `::test_ac3_an_identical_reuse_the_policy_now_refuses_is_refused` | Ledger PASS; A: changed arguments call_id_conflict, no retained result, count unchanged; direct-executor identity/policy tests pass. |
| F4 | **CLOSED** | `test_loader.py::test_ac10_a_hook_diagnostic_never_names_another_modules_credential`, `::test_ac10_a_schema_diagnostic_masks_a_credential_used_as_a_key`, `::test_ac10_streamerbot_settings_never_echo_the_password_used_as_a_key`, `test_main.py::test_ac10_check_config_never_echoes_a_credential_used_as_a_key`, `::test_ac10_a_refused_startup_keeps_the_credential_out_of_supervision`, `test_redaction_surfaces.py::test_ac11_every_configuration_diagnostic_surface_routes_through_the_primitive` | Ledger PASS; C exit=2, stdout empty, masked stderr; D four negative FAIL and positive PASS. |
| F5 | **CLOSED** | `test_proxy.py::test_p6_ac8_peer_strings_never_enter_diagnostics_or_supervision_records` (token, 4 KiB) | Ledger PASS; B success/result returned, leaking_records=0, leaking_diagnostics=0. |
| F6 | **CLOSED** | `test_brain.py::test_ac13_forgetme_empties_the_brain_memory_of_that_key_only`, `::test_ac14_a_run_admitted_before_the_erasure_records_nothing_after_it` | Ledger PASS; E brain/store empty, no erased disk text, store_refusals=2, brain_refusals=1. |
| F7 | **CLOSED** | `test_actions.py::test_ac4_concurrent_identical_calls_reach_the_provider_once`, `::test_ac4_a_concurrent_reuse_naming_another_call_is_refused`, `::test_ac4_a_cancelled_first_caller_still_settles_its_waiter` | Ledger PASS; simultaneous identical calls execute provider/handler once; conflicting and cancelled callers settle correctly. |
| F8 | **CLOSED** | `test_automation_bridge.py::test_ac15_phase6_a_raising_reconcile_trace_leaves_the_owner_alive`, `::test_ac15_phase6_a_permanently_raising_trace_keeps_the_backoff` | Ledger PASS; raising reconciliation trace leaves owner alive, failed connection closes, reconnect/backoff continues. |
| F9 | **CLOSED** | `test_twitch.py::test_ac16_an_authentication_refusal_of_the_chat_subscription_ends_recovery` (401, 403) | Ledger PASS for 401/403; authentication degradation is terminal for recovery, with no continuing retries. |
| F10 | **CLOSED** | `test_twitch.py::test_ac17_a_socket_silent_past_its_keepalive_is_recovered` | Ledger PASS; silent socket recovers after keepalive plus margin, never before. |
| F11 | **CLOSED** | `test_youtube.py::test_ac18_the_first_page_of_a_fresh_resolution_admits_nothing`, `::test_ac18_the_first_page_after_a_re_lookup_admits_nothing` (403, 404) | Ledger PASS; fresh and re-looked-up first pages admit no history; following new page admits its message. |
| F12 | **CLOSED** | `test_brain.py::test_ac19_a_body_one_byte_over_the_bound_fails_the_run_undecoded`, `::test_ac19_a_long_body_is_read_only_to_one_byte_past_the_bound`, `test_model_adapter.py::test_ac19_a_probe_answer_over_the_bound_is_never_decoded` | Ledger PASS; oversized body stopped before JSON decoding; exact-boundary response still decodes and delivers. |
| F13 | **CLOSED** | `test_proxy.py::test_p6_ac20_a_third_unpaired_connection_beyond_the_cap_of_2_is_refused` | Ledger PASS; third unpaired connection refused at cap 2; capacity released on pairing/closing. |
| F14 | **CLOSED** | `test_proxy.py::test_p6_ac9_running_executions_and_duplicate_waiters_are_bounded` | Ledger PASS; execution and duplicate-waiter caps enforced; capacity returns after completion. |
| F15 | **CLOSED** | `test_installation_doc.py::test_the_guides_endpoint_values_are_complete_chat_completions_urls`, `::test_the_guides_profile_posts_to_the_guides_endpoint` | Ledger PASS for both guides; complete endpoint URLs equal actual fake-backed POST URLs. |
| F16 | **CLOSED** | `test_installation_doc.py::test_the_readme_config_ui_command_runs` | Ledger PASS; README config-UI command with help exits 0 and names --config. |
| F17 | **CLOSED** | `test_installation_doc.py::test_the_documents_name_the_overlay_the_code_derives` (both guides, README) | Ledger PASS for guides/README; overlay name derived from runtime code. |
| F18 | **CLOSED** | `test_installation_doc.py::test_the_guide_prints_the_commands_the_code_still_has` (+ `::test_the_pre_fix_readme_line_does_not_resolve`) | Ledger PASS; command resolution plus all original exact profile/command assertions; N2 wrong-profile mutations FAIL on both guides. |
| F19 | **CLOSED** | `test_proxy.py::test_p6_ac20_a_silent_connection_is_closed_at_the_hello_deadline_and_not_one_tick_before`, `::test_p6_ac20_a_peer_that_stops_reading_cannot_hold_its_slot_past_the_hello_deadline` | Ledger PASS; silent and blocked-reader hello deadlines release connection/slot exactly at deadline. |
| F20 | **CLOSED** | `test_brain.py::test_ac21_arguments_nested_10000_levels_are_malformed_and_the_run_goes_on`, `test_model_adapter.py::test_ac21_probe_arguments_nested_10000_levels_raise_brain_module_error` | Ledger PASS; 10,000-level arguments yield controlled malformed_arguments/BrainModuleError; run continues. |
| F21 | **CLOSED** | `test_viewer_memory.py::test_ac25_a_write_failing_for_space_evicts_nothing`, `::test_ac25_a_rename_failing_after_evictions_publishes_their_exact_count`, `::test_ac25_a_use_whose_rename_fails_carries_its_evictions` | Ledger PASS; precommit ENOSPC evicts nothing; later rename failure reports exact deletions. |
| F22 | **CLOSED** | `test_viewer_memory.py::test_ac26_a_leftover_temporary_file_is_counted_then_reclaimed`, `::test_ac26_a_write_cut_short_counts_its_kept_temporary_at_its_real_size`, `::test_ac26_a_scan_reclaims_a_temporary_file_and_never_reads_it` | Ledger PASS; 1,000 temporary bytes counted until reclaimed, never parsed as a record. |
| F23 | **CLOSED** | `test_admission.py::test_expired_and_evicted_sessions_never_accumulate_in_the_ready_structure` | Ledger PASS; removed sessions never accumulate in ready structure; bound and FIFO hold. |
| F24 | **CLOSED** | `test_twitch.py::test_ac23_chat_write_refuses_text_over_500_characters_before_any_request`, `::test_ac23_the_compatibility_route_reaches_the_500_character_check` | Ledger PASS; 501 characters refused before any POST; direct 500-character boundary accepted and compatibility 501-character refusal verified. |
| F25 | **CLOSED** | `test_kick.py::test_ac24_the_dedup_window_is_never_shorter_than_twice_the_tolerance` | Ledger PASS; window 300 refused, 600 accepted. Full-suite replay-span positive/negative controls pass. |
| F26 | **CLOSED** | `test_loader.py::test_f26_an_undeclared_setting_is_refused_by_its_path_at_every_depth`, `::test_f26_a_credential_used_as_an_unknown_key_is_masked`, `test_main.py::test_ac12_check_config_refuses_an_unknown_key_naming_its_path`, `::test_ac12_main_check_config_exits_non_zero_on_an_unknown_key`, `::test_ac12_a_credential_used_as_an_unknown_key_is_redacted` | Ledger PASS; unknown settings refused by path at all tested depths, credential keys masked; shipped examples validate in suite. |
| F27 | **CLOSED** | `test_brain.py::test_history_beyond_the_token_budget_is_dropped`, `::test_history_leaves_half_the_token_budget_for_the_output`, `::test_history_reaches_the_prompt_in_chronological_order` | Ledger PASS; fresh budget mutation 2 failed/1 passed, reservation mutation 2 failed/1 passed, order mutation 3 failed. |
| F28 | **CLOSED** | `test_automation_bridge.py::test_ac16_no_run_observation_is_a_success` | Ledger PASS; isolated unmodified control 1 passed; valid acknowledged-success mutation 1 failed at observation.status != success. |
| F29 | **CLOSED** | `test_actions.py::test_ac5_a_non_finite_cancel_grace_is_refused` (nan, inf, -inf) | Ledger PASS; NaN/±inf cancel grace refused. Finite nonnegative controls run in full suite. |
| F30 | **CLOSED** | `test_audio_output.py::test_stop_returns_as_soon_as_the_player_exits_within_the_grace` | Ledger PASS; stop returns within 0.1 seconds of fake player exit despite 5-second grace. |
| F31 | **CLOSED** | `test_viewer_memory.py::test_ac27_a_memory_name_with_a_trailing_newline_is_foreign` | Ledger PASS; trailing-newline foreign filename remains unread, uncounted and undeleted. |
| F32 | **CLOSED** | `test_bus.py::test_absent_or_non_finite_limit_is_rejected_naming_the_setting` (21 cases); AC36 `test_bus.py::test_no_constructor_result_is_asserted_merely_not_none` | Ledger PASS; constructor-default mutation 21 failed; no-tautology guard and rejection assertions hold. |
| F33 | **CLOSED** | `test_bus.py::test_retention_copy_failure_is_a_publication_error_with_history_record` | Ledger PASS; retention-copy failure becomes PublicationError naming event, with failure retained in history. |
| F34 | **OUT-OF-SCOPE** | — | Explicit brief/spec exclusion of challenged codex:F6. No new out-of-tree stalled-subscriber experiment or closure claim. |
| F35 | **CLOSED** | `test_config_ui.py::test_p17_ac30_a_validator_that_never_returns_releases_the_check_by_the_deadline`, `::test_p17_ac30_stalled_cleanup_after_a_validator_timeout_gets_only_the_residual`, `::test_p17_ac30_cleanup_stages_after_an_early_validation_share_the_remaining_budget` | Ledger PASS; validator timeout and stalled/shared cleanup enforce one 121-second release bound. |

| `claude:F8` | **OUT-OF-SCOPE** | Brief/spec finding-accounting decision | Documented minimum-backoff reset after loss; no new experiment or closure claim. |
| N1 | **CLOSED** | Independent D and full structural suite | Four raw fixtures FAIL the real invariant, routed positive PASS, hook-outcome constructor invariant holds. |
| N2 | **CLOSED** | `test_installation_doc.py::test_the_guide_prints_the_commands_the_code_still_has` | In-memory UI-only `--config wrong.yaml` mutation: `installation.md FAIL`, `installation.fr.md FAIL`. All four original assertion ASTs retained. |
| N3 | **CLOSED** | Two-baseline AST audit; all five amended tests in suite | 2,184 pre-fix tests: zero deletions, eight assertion-changing functions exactly accounted for; 2,290 first-gate tests: zero deletions, four changes consisting only of added assertions. |
| A1 | **OPEN** (documentation only) | AC2 test PASS; literal token inventory FAIL | Corrected spec and actual trace agree. Sixteen old-token lines remain before this report, including prescriptive `plan.md:102` and `scaffolding/steps/P2-spec.md:77`. |

## REQUIREMENT MAP

All named tests below actually ran in the ledger selection or required full suite. PASS states the tested product behavior within the external-verification limits below. The corrected normative AC2 and the additional documentary absence condition are distinguished explicitly.

| Requirement | Named running tests (`tests/` prefix) | Result |
|---|---|---|
| R1 | `test_twitch.py::test_ac1_an_ungranted_compatibility_send_is_refused_by_the_executor`, `test_ac2_the_compatibility_send_is_traced_as_an_executor_call`; `test_actions.py::test_ac3_a_reused_call_id_naming_another_call_is_refused`, `test_ac3_an_identical_reuse_the_policy_now_refuses_is_refused`, `test_ac4_concurrent_identical_calls_reach_the_provider_once`, `test_ac5_a_non_finite_cancel_grace_is_refused`; A | **PASS product and amended normative AC2**. The full third-pass AC2 checklist is **FAIL** solely for documentary absence, as above. |
| R2 | `test_proxy.py::test_p6_ac6_a_call_outside_the_negotiated_set_never_reaches_the_executor`, `test_p6_ac7_the_frame_principal_is_compared_never_executed_under`, `test_p6_ac8_peer_call_fields_never_enter_supervision_records`, `test_p6_ac9_running_executions_and_duplicate_waiters_are_bounded`, `test_p6_f2_f3_a_reused_call_id_naming_another_call_reads_nothing_retained`; A/B | **PASS**: pairing governs fresh/cached calls; bounded work/waiters, no peer-string leak, valid result and local traces preserved. |
| R3 | `test_loader.py::test_ac10_a_hook_diagnostic_never_names_another_modules_credential`; `test_main.py::test_ac12_main_check_config_exits_non_zero_on_an_unknown_key`; `test_redaction_surfaces.py::test_ac11_every_configuration_diagnostic_surface_routes_through_the_primitive`, `test_ac11_probe_d_every_spelling_fails_the_real_structural_test`, `test_f4_probe_c_the_offline_command_never_echoes_a_credential_key`; C/D | **PASS** current consumers, unknown-key boundary and all required structural fixtures. Static analysis limits remain stated below. |
| R4 | `test_brain.py::test_ac13_forgetme_empties_the_brain_memory_of_that_key_only`, `test_ac14_a_run_admitted_before_the_erasure_records_nothing_after_it`, `test_f6_the_store_refuses_a_stale_record_and_keeps_later_and_other_keys`; `test_viewer_memory.py::test_f6_a_record_the_erasure_does_not_admit_writes_nothing`; E | **PASS**: all holders reject stale records, no erased text survives, later/other records accepted, refusal observable. |
| R5 | `test_automation_bridge.py::test_ac15_phase6_a_raising_reconcile_trace_leaves_the_owner_alive`; `test_twitch.py::test_ac16_an_authentication_refusal_of_the_chat_subscription_ends_recovery`, `test_ac17_a_socket_silent_past_its_keepalive_is_recovered`; `test_youtube.py::test_ac18_the_first_page_of_a_fresh_resolution_admits_nothing`, `test_ac18_the_first_page_after_a_re_lookup_admits_nothing` | **PASS**: owner survives failed supervision, terminal auth-stop, timed socket recovery, history priming. |
| R6 | `test_brain.py::test_ac19_a_body_one_byte_over_the_bound_fails_the_run_undecoded`, `test_ac19_a_body_exactly_at_the_bound_is_decoded_and_delivered`, `test_ac21_arguments_nested_10000_levels_are_malformed_and_the_run_goes_on`; `test_proxy.py::test_p6_ac20_a_third_unpaired_connection_beyond_the_cap_of_2_is_refused`, `test_p6_ac20_a_silent_connection_is_closed_at_the_hello_deadline_and_not_one_tick_before`; `test_admission.py::test_expired_and_evicted_sessions_never_accumulate_in_the_ready_structure`; `test_twitch.py::test_ac23_chat_write_refuses_text_over_500_characters_before_any_request`; `test_kick.py::test_ac24_the_dedup_window_is_never_shorter_than_twice_the_tolerance` | **PASS**: body/recursion/session/text/replay/admission bounds and boundary controls. |
| R7 | `test_viewer_memory.py::test_ac25_a_write_failing_for_space_evicts_nothing`, `test_ac25_a_rename_failing_after_evictions_publishes_their_exact_count`, `test_ac26_a_leftover_temporary_file_is_counted_then_reclaimed`, `test_ac27_a_memory_name_with_a_trailing_newline_is_foreign`; `test_bus.py::test_retention_copy_failure_is_a_publication_error_with_history_record`; `test_audio_output.py::test_stop_returns_as_soon_as_the_player_exits_within_the_grace`; `test_config_ui.py::test_p17_ac30_a_validator_that_never_returns_releases_the_check_by_the_deadline` plus its two shared/stalled-cleanup siblings | **PASS**: failure accounting, temporary/foreign-file handling, controlled publication errors, early stop and shared Check deadline. |
| R8 | `test_installation_doc.py::test_the_guides_endpoint_values_are_complete_chat_completions_urls`, `test_the_guides_profile_posts_to_the_guides_endpoint`, `test_the_documents_name_the_overlay_the_code_derives`, `test_the_guide_prints_the_commands_the_code_still_has`, `test_the_readme_config_ui_command_runs`; N2 mutations | **PASS**: actual endpoint, commands/help, derived overlay, exact guide profile arguments. |
| R9 | `test_brain.py::test_history_beyond_the_token_budget_is_dropped`, `test_history_leaves_half_the_token_budget_for_the_output`, `test_history_reaches_the_prompt_in_chronological_order`; isolated `test_automation_bridge.py::test_ac16_no_run_observation_is_a_success`; `test_bus.py::test_absent_or_non_finite_limit_is_rejected_naming_the_setting`, `test_no_constructor_result_is_asserted_merely_not_none`; fresh mutations below | **PASS**: each required mutation fails, unmodified controls pass, isolated F28 records nonempty outcomes. |
| R10 | Every finding's named ledger node (70 distinct nodes/133 cases); full suite; `test_phase5_docs_status.py`; independent two-baseline AST audit and amendment audit | **FAIL closing gate** solely because A1's explicit documentary absence condition fails. Current regressions/test treatment **PASS**. A fresh installed pre-fix run for every product finding is **NOT-VERIFIABLE from this pass**; prior gate/ledger evidence is retained, not relabeled as a new baseline execution. |

Phase-5 guarantees still run in the suite: `test_input_ownership.py::test_ac2_no_shipped_profile_names_the_bridge_or_an_inputs_key`, `test_ac5_a_twitch_event_is_dropped_while_the_owning_bridge_is_down`, `test_the_gated_bindings_never_overlap`, `test_ac14_the_real_users_and_bridge_split_the_viewer_read`; bridge `test_ac21_a_principal_without_a_rule_is_refused_before_the_provider`, acknowledgement/no-acknowledgement/cancellation rows and isolated AC16; `test_phase5_docs_status.py::test_ac37_no_document_calls_the_bridge_delivered`. The status test file is byte-identical to `8010edc` (`git diff --quiet` exit 0). Optional bridge, replacement inputs, one owner, default-deny, honest unconfirmed execution and “not delivered” remain intact.

## TEST TREATMENT: PASS

`git diff --name-only 8f0bd26 HEAD -- core modules tests` and the working-tree diff for those paths are both empty. Since the second product head, the committed changes are only the second review, spec correction and amendment. The full test diff remains 19 files; no file/test was deleted. A scan of added diff lines finds **no added `skip`, `skipif`, `xfail` or `importorskip`**. Existing suite skips are not newly introduced suppressions. Removed executable assertion/raises lines and changed setup/decorators were inspected; there is no unaccounted loosening or removed expected-exception check.

Independent AST comparison of every pre-existing test function, ignoring source locations:

| Baseline | Functions compared | Deleted | Assertion changes |
|---|---:|---:|---|
| `8010edc` | 2,184 | 0 | Eight: three allowlisted tests and exactly five amended tests |
| `0f335bc` | 2,290 | 0 | Four, all assertion additions: installation +3, structural invariant +1, registries +2, YouTube +2 |

All nine allowlisted tests have the required finding/requirement docstring pair, freshly checked:

| Allowlisted test (`tests/` prefix) | Pair | Treatment |
|---|---|---|
| `test_twitch.py::test_chat_send_uses_configured_identity_and_preserves_event` | F1/R1 | Grant fixture; assertion ASTs unchanged |
| `test_twitch.py::test_send_failure_continues_chain_and_next_send_succeeds` | F1/R1 | Grant/audit-bus fixture; assertions unchanged |
| `test_twitch.py::test_pending_send_releases_response_on_timeout_cancellation_and_close` | F1/R1 | Grant/audit-bus fixture; assertions unchanged |
| `test_twitch.py::test_notification_mapping_dedup_and_retry_reconnect` | F1/R1 | Grant fixture; assertions unchanged |
| `test_twitch.py::test_lost_sent_trace_neither_retries_nor_repeats_the_send` | F1/R1 | Documentation; assertions unchanged |
| `test_twitch.py::test_confirmed_outcome_never_waits_for_the_sent_trace` | F1/R1 | Documentation; assertions unchanged |
| `test_installation_doc.py::test_the_guide_prints_the_commands_the_code_still_has` | F18/R8 | All four original assertions retained, three added; commands resolved and README covered |
| `test_automation_bridge.py::test_ac16_no_run_observation_is_a_success` | F28/R9 | Self-contained two-outcome assertion; valid-success mutation fails |
| `test_bus.py::test_absent_or_non_finite_limit_is_rejected_naming_the_setting` | F32/R9 | Tautology replaced by exact configured limits; rejection assertions unchanged |

The five N3 amendment entries remain valid extensions, individually checked against their pre-fix predicates:

- N3.1 `test_brain.py::test_manifest_declares_v2_shape_settings_hook_and_no_grant`: exact property equality includes the optional byte bound, with type/minimum check added; prior members and required set retained.
- N3.2 `test_proxy.py::test_agent_link_manifest_declares_the_input_role_no_event_and_no_action`: exact property set includes three optional pairing/bound settings; every old member and role/event/action predicate retained.
- N3.3 `test_proxy.py::test_agent_link_validate_settings_accepts_loopback_ws_and_wss_anywhere`: full input gains the three settings, parsed-value checks added, exact zero-diagnostics and all old parse predicates retained. The chained principal comparison makes two added assertion nodes cover three new fields.
- N3.4 `test_proxy.py::test_agent_link_validate_settings_refuses_bad_entries_and_non_finite_bounds`: three bad inputs and exactly three corresponding expected refused fields added; equality and all previous refusals retained.
- N3.5 `test_youtube.py::test_ac39_three_platforms_on_one_runtime`: pre-poll `list == 1` retained and zero-admission `live_sessions == 1` added; only after advancing the poll clock does `list == 2` assert the new message's page. All old final session/binding/no-write predicates remain.

Other setup adaptations still declare fixture settings, grant sends needed to reach the original Twitch behavior, set Kick's legitimate 600-second window/clock advance, or add YouTube's priming page. They do not create an unamended assertion replacement. N2's English/French wrong-profile mutations both fail.

### Fresh sensitivity mutations

Each used a fresh interpreter and `SourceFileLoader.get_code` to compile modified source in memory, including loader-imported modules. No product file was edited.

| Finding | Mutation | Observed output |
|---|---|---|
| F27 | Remove `input_tokens + cost > history_limit` guard | `2 failed, 1 passed in 0.19s` |
| F27 | Replace half-budget history limit with whole budget | `2 failed, 1 passed in 0.20s` |
| F27 | Remove `kept.reverse()` | `3 failed in 0.24s` |
| F28 | Acknowledged run returns schema-valid `ActionObservation(status="success", provenance=_run_provenance(), result={})` | `1 failed in 0.33s`, at `assert observation.status != "success", observation` |
| F32 | Validate supplied history byte bound but store the default instead | `21 failed in 0.11s` |
| F28 control | No mutation, isolated test, `-p no:randomly` | `1 passed in 0.37s` |

All unmodified history and bus mutation targets passed in the fresh ledger run. These mutation failures are the expected evidence of assertion sensitivity, not failures in the unmodified suite.

## NEW FINDINGS

**No new product/test finding demonstrated.** All five fixed boundaries and the amended runtime trace behavior hold in executed probes. The only remaining refusal is **A1, minor documentation conformity**: the executed zero-occurrence search above fails, with two old prescriptive AC2 clauses surviving. This is recorded under the existing amendment item rather than duplicating it as another product defect. There is no evidence supporting a new event implementation.

## NOT VERIFIED

- No real platform, Streamer.bot instance, OBS, model service or audio device was contacted in this phase. Tests and these probes use in-tree fakes, local temporary stores and fake-backed transport. The bridge remains **not delivered**.
- No installed-wheel smoke test, real-instance trial, load test, process-kill durability experiment, or exhaustive scheduling/interleaving proof.
- No fresh installed `8010edc` run or replay of every historical commit. Product pre-fix evidence and its name-shim/new-seam limitations are retained from the ledger and earlier reviews; this pass freshly verifies head behavior, probes, assertion treatment and the F27/F28/F32 mutations.
- No proof for every possible Python diagnostic construction. Structural limitations involving cross-file helpers, container/attribute data flow and unrecognized hook resolution remain. The required negative fixtures, positive control and current-tree consumers pass.
- No random-order/xdist run. F28 was exercised alone with randomization disabled as well as in the ledger/full suite and valid-success mutation.
- Not every possible injected response object taking the nonstream `response.json()` fallback was exercised. Bounded aiohttp/test byte streams and exact/over-bound controls are covered.
- Offline viewer-memory statistics are not a proof of universal offline temporary-file reclamation; runtime scan/capacity guarantees are supported by F22's tests.

**REQUEST_CHANGES — A1 documentary closure only.** Corrected runtime AC2, every in-scope product/test finding, probes A–E and test treatment pass. The demanded claim that the obsolete name is absent from phase documents cannot be made on this tree.
