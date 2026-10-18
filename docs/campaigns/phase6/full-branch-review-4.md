# Phase 6 — full-branch closing gate, fourth pass

## VERDICT: APPROVE

Reviewed `12f73296e9dd1ab4317fa26dc961e9b9e88992f8` on 2026-10-19 against pre-fix `8010edc`, first-gate `0f335bc`, and third-pass head `17be55d`.

**A1 PASS. All 34 in-scope findings F1–F33 and F35 remain CLOSED; N1–N3 remain CLOSED. Probes A–E pass. R1–R10 pass within the verification limits stated below. Phase 6 passes its closing gate.** F34 and `claude:F8` remain explicitly OUT-OF-SCOPE.

The corrected criterion distinguishes a prescription from a quotation documenting its replacement. Both surviving matches in the searched specification/step-spec perimeter are explicit historical amendment annotations. Neither prescribes the old event. The plan and P2 test clauses now require the canonical executor trace. There is no outstanding product or documentary counterexample requiring another change.

Only `docs/campaigns/phase6/full-branch-review-4.md` was written by this review in the repository. Initial `git status --short` was empty. No source, test, other document, index entry, checkout or commit was changed. No repository or ancestor `AGENTS.md` was found. Bytecode and pytest-cache writes were disabled. Ordinary fixtures used temporary directories outside the repository; independent source mutations and structural fixtures stayed in memory. The sandbox launcher initially failed with `bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted`; subsequent inspection and execution used approved escalation.

## A1: PASS

The specification's AC2 at `spec.md:206`, the plan's P2 Tests clause at `plan.md:102`, and `scaffolding/steps/P2-spec.md:77` all name exactly one `action.started` and one `action.completed`.

The named running regression, `tests/test_twitch.py::test_ac2_the_compatibility_send_is_traced_as_an_executor_call`, passes in the fresh finding selection and full suite. Before its separate direct-call control, it singleton-unpacks:

```python
(started,) = events_of(bus, "action.started")
(completed,) = events_of(bus, "action.completed")
```

That enforces exactly one of each for the compatibility publication. It checks the compatibility principal, action/provider, Twitch destination, successful terminal status, matching call ID and start/completion/sent ordering. Its direct-call control checks matching trace shapes, distinct IDs and exactly two starts/completions after the two distinct calls.

Executed raw search, before writing this report:

```text
$ rg -n 'action\.requested' docs/campaigns/phase6/spec.md docs/campaigns/phase6/plan.md docs/campaigns/phase6/scaffolding/steps tests
docs/campaigns/phase6/spec.md:208:  traces an executor call produces. (Amendment A1: this criterion said `action.requested`, a trace the
docs/campaigns/phase6/scaffolding/steps/P2-spec.md:122:> Note (amendment A1): this step spec said `action.requested` when it was written; the canonical
exit=0
```

These are the specification's explicit A1 parenthetical (`spec.md:208–211`) and P2's explicit historical blockquote (`P2-spec.md:122–124`). I inspected both complete annotations. The current prescriptions immediately preceding them use `action.started`; the quoted token explains what changed.

A second scan removed only those two explicitly bounded annotations, asserting that each matched exactly once, then searched every remaining line of the specification, plan, all step specs and Python/YAML/JSON test files:

```text
Excluded historical annotations:
  docs/campaigns/phase6/spec.md:208-211 explicit amendment parenthetical
  docs/campaigns/phase6/scaffolding/steps/P2-spec.md:122-124 explicit historical blockquote
Prescriptive old-token matches: []
A1 prescriptive criterion: PASS

$ rg -n 'action\.requested' core modules tests
(no matches)
exit=1
```

The amendment record, previous reports and gate prompts remain historical evidence and are excluded from the prescriptive absence condition. The two annotations above are historical for the same reason; no assertion of literal zero occurrences across all phase documents is made.

**Decision:** accept A1. `core/contracts.py:149` defines `TRACE_ACTION_STARTED = "action.started"`; the executor already emitted that trace before this phase. AC2's own “same traces an executor call produces” clause supports this correction. Adding `action.requested` would introduce a redundant event without repairing an observed product defect.

## Observed runs

```text
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/ -q -p no:cacheprovider
3901 passed, 26 skipped in 273.88s (0:04:33)

70 distinct finding-ledger nodes, including all parametrizations:
133 passed in 9.57s

Independent A/B/C/D replay: PASS
E held-completion replay, inspected before teardown:
1 passed in 0.15s

F28 unmodified, alone, -p no:randomly:
1 passed in 0.37s
```

All finding nodes were executed, not merely collected. The per-finding counts below are fresh results from that 133-case run; overlapping evidence nodes are not counted twice in the run total. The focused run again printed `Task was destroyed but it is pending!` for the cancellation-resistant `stubborn()` fixture in the F35 stalled-cleanup test, after all assertions passed. This is the same deliberately nonterminating fixture message recorded in previous passes; no new product regression was demonstrated.

## Replayed probes A–E

**A — retained identity and authorization, F2/F3: PASS.** Used `test_proxy.recorded_host()`, with the real agent/executor and in-memory peer. Completed the granted call, applied each alteration separately and together, then retried the exact original:

```text
case       status   code              retained_equal result_present parts executor_calls
outside    refused  unknown_action    false          false          []    1
principal  refused  not_authorized    false          false          []    1
arguments  refused  call_id_conflict  false          false          []    1
combined   refused  unknown_action    false          false          []    1
exact      success  null              true           true           []    1
```

The count includes the original execution; provider invocation count also remains one. No altered retry obtains the retained result or enters the executor.

**B — peer string absent from supervision while the call executes, F5: PASS.** Independently executed `brain.call('gate-token', 2, run_id=TOKEN)` and inspected all bus records and diagnostics:

```text
status=success result_returns=true executor_calls=1
leaking_records=0 leaking_diagnostics=0
action.started:
  action=screen.capture action_version=1 provider=capture call_id=gate-token
  destination=fake/channel-9/capture nature=read principal=brain
action.completed:
  action=screen.capture action_version=1 provider=capture call_id=gate-token
  destination=fake/channel-9/capture status=success
  duration_seconds=0.0 error_code=None
both:
  run_id=peer-run_id-98fc7208bdfa6827
  conversation_id=peer-conversation_id-0f638c1906df90d7
```

Digest suffixes vary per agent instance. Both trace labels agree; the call returns the expected capture result. Full-suite controls cover all four forwarded correlation fields, token/4 KiB text, lone surrogates, and preservation of local-call fields.

**C — offline consumer, F4: PASS.** Called `modules.viewer_memory.__main__.main(['stats', '--config', '/virtual/gate.yaml'])` with the original credential-as-key document supplied through mocked `Path.read_text`. No configuration file or store was created by this independent replay:

```text
exit=2
stdout=""
stderr="viewer_memory: module 'viewer_memory': field 'validate_settings': is not a setting this module declares\n"
leak=false
```

**D — structural fixture sensitivity, F4/N1: PASS.** Appended only a virtual `core/_gate_virtual_fixture.py` through mocked `Path.rglob`/`read_text`, then invoked the actual shipped `test_ac11_every_configuration_diagnostic_surface_routes_through_the_primitive` on each body. No fixture file was created in the repository.

| Body inside `for key in settings` | Actual invariant outcome | Violations |
|---|---|---|
| Raw f-string path | **FAIL**, expected | `('core/_gate_virtual_fixture.py', 'check'): [4]` |
| Raw `.format(key)` path | **FAIL**, expected | Same, `[4]` |
| Raw concatenation path | **FAIL**, expected | Same, `[4]` |
| Unrelated `mask_known('fixed', known)`, then raw path | **FAIL**, expected | Same, `[5]` |
| `mask_known('settings.' + key, known)` before emission | **PASS**, expected | `{}` |

**E — persistent erasure after held completion, F6: PASS.** Reran `test_ac14_a_run_admitted_before_the_erasure_records_nothing_after_it`, wrapping only `MemoryPlatform.close` to inspect the real brain/store immediately before original teardown. The wrapper recursively read every store file:

```json
{"brain_recall_empty": true, "viewer_memory_record_exists": false,
 "erased_text_in_any_store_file": false, "files_scanned": 0,
 "store_refusals": 2, "brain_refusals": 1}
```

The empty file inventory is observed before cleanup, after the held completion. The test also asserts no erased text in the erasure's bus/audit records. Full-suite controls preserve legitimate later same-key and other-key records.

## PREVIOUS FINDINGS

Test paths are relative to `tests/`; a bare `::name` continues the filename in that cell. Counts below are freshly executed ledger output. Probe letters refer to the independent outputs above; mutation outputs are reproduced under TEST TREATMENT. Historical pre-fix outputs are not presented as fresh runs.

| Finding | Disposition | Named executed regressions | Fresh output / checked boundary |
|---|---|---|---|
| F1 | **CLOSED** | `test_twitch.py::test_ac1_an_ungranted_compatibility_send_is_refused_by_the_executor`, `::test_ac1_without_an_executor_the_compatibility_route_refuses`, `::test_ac2_the_compatibility_send_is_traced_as_an_executor_call` | 3 passed; compatibility default-deny, no-executor refusal and one start/completion pair. The amended AC2 trace criterion also passes. |
| F2 | **CLOSED** | `test_proxy.py::test_p6_ac6_a_call_outside_the_negotiated_set_never_reaches_the_executor`, `::test_p6_ac7_the_frame_principal_is_compared_never_executed_under`, `::test_p6_ac7_the_default_pairing_principal_is_brain` | 3 passed; A: outside/combined unknown_action, other principal not_authorized; no retained result, executor_calls=1; exact retry succeeds. |
| F3 | **CLOSED** | `test_actions.py::test_ac3_a_reused_call_id_naming_another_call_is_refused` (×5 components), `::test_ac3_a_reuse_whose_arguments_only_share_a_repr_is_refused`, `::test_ac3_an_identical_reuse_the_policy_now_refuses_is_refused` | 7 passed; A: changed arguments call_id_conflict, no retained result, count unchanged; direct-executor identity/policy tests pass. |
| F4 | **CLOSED** | `test_loader.py::test_ac10_a_hook_diagnostic_never_names_another_modules_credential`, `::test_ac10_a_schema_diagnostic_masks_a_credential_used_as_a_key`, `::test_ac10_streamerbot_settings_never_echo_the_password_used_as_a_key`, `test_main.py::test_ac10_check_config_never_echoes_a_credential_used_as_a_key`, `::test_ac10_a_refused_startup_keeps_the_credential_out_of_supervision`, `test_redaction_surfaces.py::test_ac11_every_configuration_diagnostic_surface_routes_through_the_primitive` | 29 passed; C exit=2, stdout empty, masked stderr; D four negative FAIL and positive PASS. |
| F5 | **CLOSED** | `test_proxy.py::test_p6_ac8_peer_strings_never_enter_diagnostics_or_supervision_records` (token, 4 KiB) | 2 passed; B success/result returned, leaking_records=0, leaking_diagnostics=0. |
| F6 | **CLOSED** | `test_brain.py::test_ac13_forgetme_empties_the_brain_memory_of_that_key_only`, `::test_ac14_a_run_admitted_before_the_erasure_records_nothing_after_it` | 2 passed; E brain/store empty, no erased disk text, store_refusals=2, brain_refusals=1. |
| F7 | **CLOSED** | `test_actions.py::test_ac4_concurrent_identical_calls_reach_the_provider_once`, `::test_ac4_a_concurrent_reuse_naming_another_call_is_refused`, `::test_ac4_a_cancelled_first_caller_still_settles_its_waiter` | 3 passed; simultaneous identical calls execute provider/handler once; conflicting and cancelled callers settle correctly. |
| F8 | **CLOSED** | `test_automation_bridge.py::test_ac15_phase6_a_raising_reconcile_trace_leaves_the_owner_alive`, `::test_ac15_phase6_a_permanently_raising_trace_keeps_the_backoff` | 2 passed; raising reconciliation trace leaves owner alive, failed connection closes, reconnect/backoff continues. |
| F9 | **CLOSED** | `test_twitch.py::test_ac16_an_authentication_refusal_of_the_chat_subscription_ends_recovery` (401, 403) | 2 passed for 401/403; authentication degradation is terminal for recovery, with no continuing retries. |
| F10 | **CLOSED** | `test_twitch.py::test_ac17_a_socket_silent_past_its_keepalive_is_recovered` | 1 passed; silent socket recovers after keepalive plus margin, never before. |
| F11 | **CLOSED** | `test_youtube.py::test_ac18_the_first_page_of_a_fresh_resolution_admits_nothing`, `::test_ac18_the_first_page_after_a_re_lookup_admits_nothing` (403, 404) | 3 passed; fresh and re-looked-up first pages admit no history; following new page admits its message. |
| F12 | **CLOSED** | `test_brain.py::test_ac19_a_body_one_byte_over_the_bound_fails_the_run_undecoded`, `::test_ac19_a_long_body_is_read_only_to_one_byte_past_the_bound`, `test_model_adapter.py::test_ac19_a_probe_answer_over_the_bound_is_never_decoded` | 3 passed; oversized body stopped before JSON decoding; exact-boundary response still decodes and delivers. |
| F13 | **CLOSED** | `test_proxy.py::test_p6_ac20_a_third_unpaired_connection_beyond_the_cap_of_2_is_refused` | 1 passed; third unpaired connection refused at cap 2; capacity released on pairing/closing. |
| F14 | **CLOSED** | `test_proxy.py::test_p6_ac9_running_executions_and_duplicate_waiters_are_bounded` | 1 passed; execution and duplicate-waiter caps enforced; capacity returns after completion. |
| F15 | **CLOSED** | `test_installation_doc.py::test_the_guides_endpoint_values_are_complete_chat_completions_urls`, `::test_the_guides_profile_posts_to_the_guides_endpoint` | 4 passed for both guides; complete endpoint URLs equal actual fake-backed POST URLs. |
| F16 | **CLOSED** | `test_installation_doc.py::test_the_readme_config_ui_command_runs` | 1 passed; README config-UI command with help exits 0 and names --config. |
| F17 | **CLOSED** | `test_installation_doc.py::test_the_documents_name_the_overlay_the_code_derives` (both guides, README) | 3 passed for guides/README; overlay name derived from runtime code. |
| F18 | **CLOSED** | `test_installation_doc.py::test_the_guide_prints_the_commands_the_code_still_has` (+ `::test_the_pre_fix_readme_line_does_not_resolve`) | 4 passed; command resolution plus all original exact profile/command assertions; N2 wrong-profile mutations FAIL on both guides. |
| F19 | **CLOSED** | `test_proxy.py::test_p6_ac20_a_silent_connection_is_closed_at_the_hello_deadline_and_not_one_tick_before`, `::test_p6_ac20_a_peer_that_stops_reading_cannot_hold_its_slot_past_the_hello_deadline` | 2 passed; silent and blocked-reader hello deadlines release connection/slot exactly at deadline. |
| F20 | **CLOSED** | `test_brain.py::test_ac21_arguments_nested_10000_levels_are_malformed_and_the_run_goes_on`, `test_model_adapter.py::test_ac21_probe_arguments_nested_10000_levels_raise_brain_module_error` | 2 passed; 10,000-level arguments yield controlled malformed_arguments/BrainModuleError; run continues. |
| F21 | **CLOSED** | `test_viewer_memory.py::test_ac25_a_write_failing_for_space_evicts_nothing`, `::test_ac25_a_rename_failing_after_evictions_publishes_their_exact_count`, `::test_ac25_a_use_whose_rename_fails_carries_its_evictions` | 3 passed; precommit ENOSPC evicts nothing; later rename failure reports exact deletions. |
| F22 | **CLOSED** | `test_viewer_memory.py::test_ac26_a_leftover_temporary_file_is_counted_then_reclaimed`, `::test_ac26_a_write_cut_short_counts_its_kept_temporary_at_its_real_size`, `::test_ac26_a_scan_reclaims_a_temporary_file_and_never_reads_it` | 3 passed; 1,000 temporary bytes counted until reclaimed, never parsed as a record. |
| F23 | **CLOSED** | `test_admission.py::test_expired_and_evicted_sessions_never_accumulate_in_the_ready_structure` | 1 passed; removed sessions never accumulate in ready structure; bound and FIFO hold. |
| F24 | **CLOSED** | `test_twitch.py::test_ac23_chat_write_refuses_text_over_500_characters_before_any_request`, `::test_ac23_the_compatibility_route_reaches_the_500_character_check` | 2 passed; 501 characters refused before any POST; direct 500-character boundary accepted and compatibility 501-character refusal verified. |
| F25 | **CLOSED** | `test_kick.py::test_ac24_the_dedup_window_is_never_shorter_than_twice_the_tolerance` | 1 passed; window 300 refused, 600 accepted. Full-suite replay-span positive/negative controls pass. |
| F26 | **CLOSED** | `test_loader.py::test_f26_an_undeclared_setting_is_refused_by_its_path_at_every_depth`, `::test_f26_a_credential_used_as_an_unknown_key_is_masked`, `test_main.py::test_ac12_check_config_refuses_an_unknown_key_naming_its_path`, `::test_ac12_main_check_config_exits_non_zero_on_an_unknown_key`, `::test_ac12_a_credential_used_as_an_unknown_key_is_redacted` | 10 passed; unknown settings refused by path at all tested depths, credential keys masked; shipped examples validate in suite. |
| F27 | **CLOSED** | `test_brain.py::test_history_beyond_the_token_budget_is_dropped`, `::test_history_leaves_half_the_token_budget_for_the_output`, `::test_history_reaches_the_prompt_in_chronological_order` | 3 passed; fresh budget mutation 2 failed/1 passed, reservation mutation 2 failed/1 passed, order mutation 3 failed. |
| F28 | **CLOSED** | `test_automation_bridge.py::test_ac16_no_run_observation_is_a_success` | 1 passed; isolated unmodified control 1 passed; valid acknowledged-success mutation 1 failed at observation.status != success. |
| F29 | **CLOSED** | `test_actions.py::test_ac5_a_non_finite_cancel_grace_is_refused` (nan, inf, -inf) | 3 passed; NaN/±inf cancel grace refused. Finite nonnegative controls run in full suite. |
| F30 | **CLOSED** | `test_audio_output.py::test_stop_returns_as_soon_as_the_player_exits_within_the_grace` | 1 passed; stop returns within 0.1 seconds of fake player exit despite 5-second grace. |
| F31 | **CLOSED** | `test_viewer_memory.py::test_ac27_a_memory_name_with_a_trailing_newline_is_foreign` | 1 passed; trailing-newline foreign filename remains unread, uncounted and undeleted. |
| F32 | **CLOSED** | `test_bus.py::test_absent_or_non_finite_limit_is_rejected_naming_the_setting` (21 cases); AC36 `test_bus.py::test_no_constructor_result_is_asserted_merely_not_none` | 22 passed; constructor-default mutation 21 failed; no-tautology guard and rejection assertions hold. |
| F33 | **CLOSED** | `test_bus.py::test_retention_copy_failure_is_a_publication_error_with_history_record` | 1 passed; retention-copy failure becomes PublicationError naming event, with failure retained in history. |
| F34 | **OUT-OF-SCOPE** | — | Explicit brief/spec exclusion of challenged codex:F6. No new out-of-tree stalled-subscriber experiment or closure claim. |
| F35 | **CLOSED** | `test_config_ui.py::test_p17_ac30_a_validator_that_never_returns_releases_the_check_by_the_deadline`, `::test_p17_ac30_stalled_cleanup_after_a_validator_timeout_gets_only_the_residual`, `::test_p17_ac30_cleanup_stages_after_an_early_validation_share_the_remaining_budget` | 3 passed; validator timeout and stalled/shared cleanup enforce one 121-second release bound. |
| `claude:F8` | **OUT-OF-SCOPE** | Brief/spec finding-accounting decision | Documented minimum-backoff reset after loss; no new experiment or closure claim. |
| N1 | **CLOSED** | Independent D and full structural suite | Four raw fixtures FAIL the real invariant, routed positive PASS, hook-outcome constructor invariant holds. |
| N2 | **CLOSED** | `test_installation_doc.py::test_the_guide_prints_the_commands_the_code_still_has` | In-memory UI-only `--config wrong.yaml` mutation: `installation.md FAIL`, `installation.fr.md FAIL`. All four original assertion ASTs retained. |
| N3 | **CLOSED** | Two-baseline AST audit; all five amended tests in suite | 2,184 pre-fix tests: zero deletions, eight assertion-changing functions exactly accounted for; 2,290 first-gate tests: zero deletions, four changes consisting only of added assertions. |

A1 is **CLOSED** by the corrected prescriptive search and running AC2 regression above. No product finding is reopened.

## REQUIREMENT MAP

Every test named below ran in the fresh finding selection or full suite. PASS refers to the tested contract and retained closure evidence, subject to NOT VERIFIED.

| Requirement | Named running tests (`tests/` prefix) | Result |
|---|---|---|
| R1 | `test_twitch.py::test_ac1_an_ungranted_compatibility_send_is_refused_by_the_executor`, `test_ac2_the_compatibility_send_is_traced_as_an_executor_call`; `test_actions.py::test_ac3_a_reused_call_id_naming_another_call_is_refused`, `test_ac4_concurrent_identical_calls_reach_the_provider_once`, `test_ac5_a_non_finite_cancel_grace_is_refused`; A | **PASS**: executor-only compatibility send, default-deny, amended exact trace pair, identity-bound replay, one concurrent execution and finite grace. |
| R2 | `test_proxy.py::test_p6_ac6_a_call_outside_the_negotiated_set_never_reaches_the_executor`, `test_p6_ac7_the_frame_principal_is_compared_never_executed_under`, `test_p6_ac8_peer_call_fields_never_enter_supervision_records`, `test_p6_ac9_running_executions_and_duplicate_waiters_are_bounded`; A/B | **PASS**: pairing governs fresh/cached calls, bounded executions/waiters, no peer-text leakage, valid result and local traces preserved. |
| R3 | `test_loader.py::test_ac10_a_hook_diagnostic_never_names_another_modules_credential`; `test_main.py::test_ac12_main_check_config_exits_non_zero_on_an_unknown_key`; `test_redaction_surfaces.py::test_ac11_every_configuration_diagnostic_surface_routes_through_the_primitive`, `test_f4_probe_c_the_offline_command_never_echoes_a_credential_key`; C/D | **PASS**: current diagnostic consumers route through the primitive, unknown keys refuse, required structural negatives fail and positive passes. |
| R4 | `test_brain.py::test_ac13_forgetme_empties_the_brain_memory_of_that_key_only`, `test_ac14_a_run_admitted_before_the_erasure_records_nothing_after_it`, `test_f6_the_store_refuses_a_stale_record_and_keeps_later_and_other_keys`; E | **PASS**: erasure reaches both holders; stale completion cannot recreate text; later/other records survive; erasure records do not leak text. |
| R5 | `test_automation_bridge.py::test_ac15_phase6_a_raising_reconcile_trace_leaves_the_owner_alive`; `test_twitch.py::test_ac16_an_authentication_refusal_of_the_chat_subscription_ends_recovery`, `test_ac17_a_socket_silent_past_its_keepalive_is_recovered`; `test_youtube.py::test_ac18_the_first_page_after_a_re_lookup_admits_nothing` | **PASS**: owner survives supervision failure, auth refusal ends recovery, silent socket recovers and resolved chat history is primed away. |
| R6 | `test_brain.py::test_ac19_a_body_one_byte_over_the_bound_fails_the_run_undecoded`, `test_ac19_a_body_exactly_at_the_bound_is_decoded_and_delivered`, `test_ac21_arguments_nested_10000_levels_are_malformed_and_the_run_goes_on`; `test_proxy.py::test_p6_ac20_a_third_unpaired_connection_beyond_the_cap_of_2_is_refused`, `test_p6_ac20_a_silent_connection_is_closed_at_the_hello_deadline_and_not_one_tick_before`; `test_admission.py::test_expired_and_evicted_sessions_never_accumulate_in_the_ready_structure`; `test_twitch.py::test_ac23_chat_write_refuses_text_over_500_characters_before_any_request`; `test_kick.py::test_ac24_the_dedup_window_is_never_shorter_than_twice_the_tolerance` | **PASS**: byte, recursion, admission, session, text and replay bounds with valid-boundary controls. |
| R7 | `test_viewer_memory.py::test_ac25_a_write_failing_for_space_evicts_nothing`, `test_ac25_a_rename_failing_after_evictions_publishes_their_exact_count`, `test_ac26_a_leftover_temporary_file_is_counted_then_reclaimed`, `test_ac27_a_memory_name_with_a_trailing_newline_is_foreign`; `test_bus.py::test_retention_copy_failure_is_a_publication_error_with_history_record`; `test_audio_output.py::test_stop_returns_as_soon_as_the_player_exits_within_the_grace`; `test_config_ui.py::test_p17_ac30_cleanup_stages_after_an_early_validation_share_the_remaining_budget` | **PASS**: failure accounting, temporary/foreign-file handling, controlled publication errors, early stop and one shared Check deadline. |
| R8 | `test_installation_doc.py::test_the_guides_endpoint_values_are_complete_chat_completions_urls`, `test_the_guides_profile_posts_to_the_guides_endpoint`, `test_the_documents_name_the_overlay_the_code_derives`, `test_the_guide_prints_the_commands_the_code_still_has`, `test_the_readme_config_ui_command_runs`; N2 mutations | **PASS**: actual endpoint/POST, code-derived overlay, command resolution/help and exact guide-profile arguments. |
| R9 | `test_brain.py::test_history_beyond_the_token_budget_is_dropped`, `test_history_leaves_half_the_token_budget_for_the_output`, `test_history_reaches_the_prompt_in_chronological_order`; isolated `test_automation_bridge.py::test_ac16_no_run_observation_is_a_success`; `test_bus.py::test_absent_or_non_finite_limit_is_rejected_naming_the_setting`, `test_no_constructor_result_is_asserted_merely_not_none`; fresh mutations below | **PASS**: each required mutation fails and unmodified controls pass; isolated bridge assertion records actual outcomes. |
| R10 | All 70 named finding-ledger nodes/133 cases; full suite; `test_phase5_docs_status.py::test_ac37_no_document_calls_the_bridge_delivered`; two-baseline assertion audit, N3 amendment audit and this report | **PASS**: all in-scope closures remain evidenced, test treatment passes, A1 closes and this gate approves. Product pre-fix evidence is retained from the ledger/earlier reviews; fresh installed pre-fix execution is **NOT-VERIFIABLE from this pass**. |

Phase-5 guarantees also remain exercised: `test_input_ownership.py::test_ac2_no_shipped_profile_names_the_bridge_or_an_inputs_key`, `test_ac5_a_twitch_event_is_dropped_while_the_owning_bridge_is_down`, `test_the_gated_bindings_never_overlap`, `test_ac14_the_real_users_and_bridge_split_the_viewer_read`; bridge `test_ac21_a_principal_without_a_rule_is_refused_before_the_provider` and acknowledgement/no-acknowledgement/cancellation controls. Optional bridge, replacement inputs, one owner, default-deny and honest unconfirmed outcomes still pass. `tests/test_phase5_docs_status.py` is byte-identical to `8010edc`; the bridge remains **not delivered**.

## TEST TREATMENT: PASS

Fresh `git diff --name-only 17be55d HEAD -- core modules tests` and working-tree diffs for those paths are empty. Product/tests are also unchanged since `8f0bd26`, the previously reviewed product head. The complete phase test diff against `8010edc` spans 19 files, +6,202/−64 lines. Added-line scanning finds no `skip`, `skipif`, `xfail` or `importorskip`; no test file or test function was deleted. Removed lines contain no removed expected-exception check. The complete removed-line audit, setup/decorator review, assertion audit and mutations reveal no unaccounted loosening.

Independent AST comparison of every pre-existing test function, ignoring locations and comparing assertion multisets:

| Baseline | Functions compared | Deleted | Assertion changes |
|---|---:|---:|---|
| `8010edc` | 2,184 | 0 | Eight functions: three allowlisted tests and exactly five amended extensions |
| `0f335bc` | 2,290 | 0 | Four functions, all additions: installation +3, structural invariant +1, registries +2, YouTube +2 |

All six allowlisted Twitch tests retain their assertion ASTs. The installation replacement retains all four original assertion ASTs and adds three. The two actual predicate replacements are the explicitly allowlisted bridge and bus assertions; their required sensitivity mutations fail. No later reduction of a test first added during phase 6 is concealed by using only the pre-fix baseline.

Each of the nine allowlisted tests has its required finding/requirement docstring pair, freshly checked:

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

The five N3 entries in `scaffolding/plan-amendments.md` were checked against the actual pre-fix predicates:

| Amended test (`tests/` prefix) | Observed extension |
|---|---|
| `test_brain.py::test_manifest_declares_v2_shape_settings_hook_and_no_grant` | Exact property equality adds optional `max_response_bytes`; type/minimum check added; old members, required set and other predicates retained. 35 → 36 assertion nodes. |
| `test_proxy.py::test_agent_link_manifest_declares_the_input_role_no_event_and_no_action` | Exact property set adds optional `principal`, `max_running_calls`, `max_duplicate_waiters`; all old members and role/event/action checks retained. 14 → 14. |
| `test_proxy.py::test_agent_link_validate_settings_accepts_loopback_ws_and_wss_anywhere` | Full input gains the three settings; all seven old predicates retained; two added assertion statements cover the principal and both bounds. 7 → 9. |
| `test_proxy.py::test_agent_link_validate_settings_refuses_bad_entries_and_non_finite_bounds` | Three bad inputs and exactly three corresponding refused field names added; exact equality and all old refusals retained. 3 → 3. |
| `test_youtube.py::test_ac39_three_platforms_on_one_runtime` | Original pre-poll `list == 1` retained, with `live_sessions == 1` added to prove no history admission; then a clock advance and `list == 2`. All 21 old assertions retained, two added. |

The amendment's “three assertions” wording in N3.3 corresponds to three settings checked by two assertion statements, as prior passes observed. No assertion is missing. The YouTube count increases only after the additional priming poll imposed by F11. Exact-set checks remain equality checks.

Other existing-test adaptations declare fixture settings, configure grants needed to reach the original Twitch behavior, use Kick's legitimate 600-second window/clock advance, or script YouTube's priming page. Their assertions remain unchanged. There is no unamended sixth existing assertion-changing test.

### Fresh sensitivity evidence

Each product mutation ran in a fresh interpreter with a `SourceFileLoader.get_code` override compiling modified source in memory, including loader-imported modules. Each override asserted exactly one replacement site and confirmed the mutated module was loaded. No product bytes were written.

| Finding | Executed mutation | Observed output |
|---|---|---|
| F27 | `if input_tokens + cost > history_limit:` → `if False:` | `2 failed, 1 passed in 0.20s` |
| F27 | `history_limit = int(limit * _HISTORY_TOKEN_SHARE)` → `history_limit = limit` | `2 failed, 1 passed in 0.20s` |
| F27 | `kept.reverse()` → `pass` | `3 failed in 0.23s` |
| F28 | Acknowledged run returns valid `ActionObservation(status="success", provenance=_run_provenance(), result={})` | `1 failed in 0.32s`, at `assert observation.status != "success", observation` |
| F32 | Validate supplied history-byte limit, then store `DEFAULT_HISTORY_MAX_BYTES` | `21 failed in 0.10s` |
| F28 control | Unmodified, alone, `-p no:randomly` | `1 passed in 0.37s` |
| N2 | Virtual UI-only replacement with `--config wrong.yaml` | `installation.md FAIL`; `installation.fr.md FAIL` |

Unmodified history/bus targets passed in the finding selection and full suite; mutation failures above are expected sensitivity evidence. N2 invokes the actual installation command test against each virtually changed guide, leaving repository bytes unchanged. Probe D supplies the four structural negative controls and the routed positive control.

## NEW FINDINGS

**None demonstrated.** No executed counterexample reopens an earlier product/test finding or establishes a new finding with a severity. A1's previous minor documentary discrepancy is now closed under the corrected criterion. The expected negative-fixture/mutation failures and the known F35 fixture teardown message do not demonstrate a new product defect.

## NOT VERIFIED

- No real platform, Streamer.bot instance, OBS, model service or audio device was contacted in this phase. Runs use in-tree fakes, local loopback fake transports and temporary stores. The bridge remains **not delivered**; this gate does not certify a real-instance trial.
- No fresh installed `8010edc` product run or reexecution of every historical commit. The ledger and first gate supply the retained pre-fix evidence, including their name-shim and new-seam limitations. This pass freshly verifies current regressions, A–E, test treatment and the F27/F28/F32 mutations.
- No installed-wheel smoke test, load test, process-kill durability experiment, exhaustive scheduling/interleaving proof, or new stalled out-of-tree subscriber experiment for excluded F34.
- No proof for every possible Python diagnostic construction. The structural check documents limits involving cross-file helpers, container/attribute data flow and unrecognized hook resolution. Required C/D fixtures and current-tree consumers pass.
- No randomized-order or xdist run. F28 was executed alone with randomization disabled, in the finding selection/full suite and under the valid-success mutation.
- Not every possible injected response object using the nonstream `response.json()` fallback was exercised. Bounded byte streams and exact/over-bound controls are covered.
- Offline viewer-memory statistics do not establish universal offline temporary-file reclamation. Runtime scan/capacity behavior is covered by F22's running regressions.

**APPROVE**
