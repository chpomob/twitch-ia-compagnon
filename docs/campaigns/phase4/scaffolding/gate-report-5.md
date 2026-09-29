# Phase 4 — full-branch closing gate, fifth pass

## VERDICT

REQUEST_CHANGES

Reviewed `main` at `652fc27`, as the cumulative implementation diff **`835d9b4..HEAD`**, against the phase-4 spec, plan, binding brief decision 6c and this gate's requirements. **N4 is resolved. N3's original Check/Save divergence is fixed, but edited placeholders and mixed real/placeholder keys still defeat its required refusal policy. New MEDIUM finding N5 hides a complete mapping entry.** Phase 4 cannot close on this branch.

The required suite ran independently:

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/ -q -p no:cacheprovider
```

**S: 2997 passed, 18 skipped in 147.20 s; exit 0.** Bytecode and pytest cache were disabled. Normal test temporary fixtures ran. Only this report was written in the repository; no source/test/configuration changes, staging or commits. Pre-existing untracked `docs/runs/` directories were left alone.

### Executed command labels

- **S** is the exact suite command above; every named test below ran, including its parameterizations.
- **C/P/D:** `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -` with explicit stdin probes recreating gates 1–4's published inputs: authenticated GET/POST handlers, parser, real collision functions, finite dispatch/supervisor doubles and the installed aiohttp body adapter. Configuration reads were patched in memory and Save commit/replacement sinks intercepted. Current layout tokens came from actual GET responses. Vulnerability-demanding old assertions were replaced with fixed-result assertions, not counted as expected failures.
- **D/N3–N4:** the same stdin command, with gate 4's exact audio-output fixture and actual runtime checker, the two exact N4 legitimate lists, then all ten requested punctuation/escape secrets and nested mapping/key round trips. It also exercised reordered, duplicated, edited, ambiguous and mixed placeholders.
- **E:** the same stdin command, confirming the residual/new findings through actual GET, Check and Save handlers. A reproducible, file-free form is included below. The edited-placeholder case uses the real enabled audio-output checker; mapping cases validate the actual shipped field schema and capture drafts with an injected checker.
- **M:** the same stdin command for an AST inventory of every test cited in gate 1 and a semantic comparison of all manifests against `git show 835d9b4:<path>`, stripping only schema title/default annotations while preserving properties named `default`.
- **G:** `git diff --stat 835d9b4..HEAD`; `git diff --check 835d9b4..HEAD`; cumulative diffs of contracts, runtime integration, packaging and pre-existing tests, with final-source inspection of UI/overlay/rendering/guards/normalization/writing/status/Apply/adapter paths. **73 changed files; whitespace check passes.** This is a whole-branch assessment, not a sequence of commit verdicts.

## OPEN ITEMS

### N4 — MEDIUM — RESOLVED

**Locations:** `core/config_ui/__init__.py:1172` (`_shown`), `:1218` (`_shown_text`), `:1447` (`esc`), `:1933` (`_masked`), `:1964` (`_json_text`). Masking now precedes serialization; `_Shown` prevents serialized JSON being redacted again.

**Executed tests:** S includes `tests/test_config_ui.py::test_n4_a_punctuation_secret_never_breaks_a_legitimate_list` and `tests/test_config_ui.py::test_n4_punctuation_secrets_in_nested_data_mask_the_data_not_the_syntax`; independent D/N4 extended their coverage to `/` and newline.

| Replay / defeat attempt — command D/N4 | Executed result |
|---|---|
| Gate-4 secret `"`, legitimate `['kept', 'voice-two']` | Decoded JSON equals the original list; append `added`, Check passes with captured draft, Save 303, commit draft is exactly original + `added`. |
| Gate-4 secret consisting of one backslash, legitimate `['kept', 'ordinary"quote']` | JSON's introduced escape remains intact; same successful exact append round trip. |
| Each secret `"`, backslash, `[`, `]`, `{`, `}`, `,`, `:`, `/`, newline | All ten legitimate list textareas decode exactly and support append/Save. Each also appeared as a mapping key, exact scalar, substring and nested list/mapping value in the schema-valid brain actions textarea. All ten documents parse; adding `arguments.added = 'ok'` restores original nested values/keys exactly and Saves 303. |
| Recoverability after HTML/JSON decoding | Inspected configured keys/scalars after both decoding layers; none retained the secret outside masking marker text. The original configured secret occurrences were withheld, including slash/newline. This is a data-position check: fixed HTML/JSON syntax necessarily contains punctuation, and `[hidden]` itself contains brackets. Literal absence of a punctuation character from the entire HTML document would contradict the requirement to preserve syntax. |

No punctuation-induced malformed JSON or JSON-escape disclosure was reproduced. N5 below concerns collisions between distinct data keys, not renewed corruption of serialization syntax.

### N3 — MEDIUM — PARTIALLY RESOLVED; remains open

**Locations:** `core/config_ui/__init__.py:3195` (Check normalization), `:4028` (shared `_draft_value`), `:4060` (`_protect_save`), **`:1231`–`:1236`** (edited stand-in recognition only full-matches), **`:1292`–`:1314`** (mapping restoration silently overwrites a previously restored key).

**Executed tests:** S includes `tests/test_config_ui.py::test_n3_check_gives_the_real_checker_verdict_on_the_draft_save_writes`, `test_n3_an_invalid_draft_fails_check_on_its_real_values`, `test_n3_check_and_save_validate_the_same_restored_draft` and `test_n3_an_unmatched_placeholder_fails_check_as_save_refuses_it`. D and E exercise cases those assertions omit.

| Replay / defeat attempt | Executed result |
|---|---|
| Exact gate-4 D/N3: enabled audio output; allowed `['kept', 'a"b']`; default `${GATE_SECRET}` resolving to `a"b`; append `added` | Baseline real Check passes. Posted Check 200/passed; Save 303; commit contains `['kept', 'a"b', 'added']`; actual checker on that Save draft returns `(True, [])`. Original false failure resolved. |
| Nested keys and mappings | Ten punctuation-secret cases restored exact nested keys and values on both routes, with successful Save. |
| Reordered list | `['kept', 'q7Z', 'prefix-q7Z-suffix']`, reverse displayed entries and append `added`: both routes receive `['prefix-q7Z-suffix', 'q7Z', 'kept', 'added']`. No positional guessing. |
| Duplicated placeholder, one possible underlying value | Both routes accept another exact stand-in and restore another `q7Z`. This is deterministic duplication, not an ambiguous choice between different configured values. |
| Ambiguous placeholder | Starting from `['kept', 'q7Z', 'second-secret']`, retain only one of the two indistinguishable stand-ins: Check failed with explicit unmatched reason; Save 403; zero commits. |
| Edited substring marker | Adding `x[hidden]`: Check failed with explicit unmatched reason, Save 403, zero commits. |
| **Edited full stand-in — E/N3** | **Appending ` edited` to `literal value configured (hidden)` is accepted as ordinary text and written literally.** Reproduced with the real checker on a valid enabled configuration; details below. |
| Mixed restored/unrestored scalar occurrence | Keep the displayed `q7Z` stand-in and append real `q7Z`: Check/Save agree and retain both real values. |
| **Mixed restored/unrestored mapping key — E/N3** | **Post the displayed key plus its real key with different values: both routes accept, one value silently disappears, and reversing submitted key order changes the winner.** Details below. |

**Residual counterexample A: edited stand-in saved as a voice.** Configure enabled audio output with `voices.allowed = ['kept', 'q7Z', 'q7Z']`, `voices.default = '${GATE_SECRET}'`, `GATE_SECRET = 'q7Z'`, and the same valid synthesis/output settings as gate 4. Baseline real Check passes. Render the list and change only the final stand-in to `literal value configured (hidden) edited`. Real Check passes; Save returns 303 and commits:

```python
['kept', 'q7Z', 'literal value configured (hidden) edited']
```

The edited stand-in is not refused explicitly; it becomes a literal voice name. `_is_mask_marker` recognizes `[hidden]` anywhere but recognizes the full human-readable stand-in only when the entire string matches. The running unmatched-placeholder test only exercises `x[hidden]`, leaving this path untested. This directly fails the gate's edited-placeholder requirement; it is not the old false Check/Save divergence.

**Residual counterexample B: mixed key aliases overwrite.** Configure schema-valid brain `delivery.actions = [{'action': 'x.do', 'arguments': {'q7Z': 'original'}}]` with collected secret `q7Z`. Its textarea shows the key as `literal value configured (hidden)`. Post:

```python
{'literal value configured (hidden)': 'original', 'q7Z': 'second'}
```

Check passes with the captured draft; Save 303 commits only `{'q7Z': 'second'}`. Reverse the two submitted keys and the committed mapping is only `{'q7Z': 'original'}`. Both submissions reach real schema validation and Save protection/restoration. `_restore_mapping` assigns `restored[original] = value` without checking whether another submitted key already restored to that same key. The two routes agree on the same lossy draft; sharing the normalization path does not make this ambiguity safe.

Required: explicitly reject edited stand-ins and conflicting multiple submitted aliases of one restored key before validation/commit. Keep the successful data-level masking and shared normalization. These are residual N3 cases, not duplicate new IDs.

## RE-CONFIRMED

- **F1 — RESOLVED; C/P/D + S:** original literal `~/gate-base.yaml` and real `/usr/bin/bzip2` / `/usr/bin/bzcat` hard links: startup/status/runtime collision refusal; authenticated current-layout Save 403; no commit/replacement. Tilde/dot/dot-dot/relative/absolute/environment/symlink spellings, nonexistent paths, distinct paths and simulated bind-like/case-fold fallback pass. S: `tests/test_config_ui.py::test_f1_save_never_replaces_the_base_whatever_the_overlay_spelling`, `test_f1_a_hard_link_of_the_base_refuses_to_start_and_is_never_written`; `tests/test_overlay.py::test_a_hard_link_is_the_same_file_whatever_its_name`. Current guards: `core/overlay.py:249`, `core/config_ui/__init__.py:3353`, `:4271`.
- **F2 — RESOLVED for published disclosure cases; C/P/D + S:** original `q7Z` input and credential-as-channel key withheld; `Ω`, long canary, `rules`, `twitch`, `combination`, quote/backslash/newline, HTML-entity-like secrets withheld in configured input/channel/rule positions and decoded nested JSON. Diagnostics with injected canaries also redacted. S: `test_ac35_no_secret_value_reaches_any_response_log_report_or_record`, `test_p19f7_every_published_secret_length_is_still_never_rendered`; source `core/config_ui/__init__.py:1172`, `:1581`. N5 is a display-integrity failure, not a demonstrated secret disclosure.
- **F3 — RESOLVED; C/P + S:** absent audio synthesis probe remains True at runtime; explicit false reaches Check and Save as `False`, Save 303. S: `test_f3_an_unset_true_default_boolean_renders_its_effective_value`, `test_f3_a_boolean_draft_expresses_unset_false_and_true`.
- **F4 — RESOLVED; C/P + S:** 128 blocked requests / 4 workers: exactly 12 queued, 16 handled (401), 112 refused (503); actual adapter 0 and 1,048,576 bytes → 200; 1,048,577 → 413; subsequent 1 byte → 200. S: `test_f4_a_saturated_bridge_refuses_honestly_and_queues_nothing_more`, `test_f4_a_failed_body_read_releases_its_admission`; `core/config_ui/__init__.py:4718`, `:4788`.
- **F5 — RESOLVED; C + S:** timeout-on-every-wait child yields terminate, wait(10.0), kill, wait(5.0); stop False, child retained, no unbounded wait. S: `test_f5_an_unkillable_child_is_reported_and_kept_not_waited_on_forever`, `test_f5_apply_with_an_unkillable_child_is_refused_and_starts_nothing`; `core/config_ui/__init__.py:4512`.
- **N1 — RESOLVED; P + S:** unrelated secret `r`, exact rendered `true` option: Check and Save both receive boolean True; Save 303. Structural option tokens remain intact; existing boolean/enum tests ran. Current renderer `core/config_ui/__init__.py:1910`.
- **N2 — RESOLVED; D + S:** reorder/add/remove/rename of channel keys: identical Check/Save 409 response, zero parser/checker calls for Check; unchanged and value-only cases Check 200 and target `first` correctly. Current layout decision `core/config_ui/__init__.py:3233`, `:3881`; S includes stale-layout regression tests.

### Gate-1 requirement map R1–R10 → named running tests

Every test listed in this map passed in **S**. M independently found all **109** distinct fully qualified names cited by gate 1 still present; S ran all of them. Green named tests do not override the independently reproduced N3/N5 failures.

| Requirement | Gate-5 result | Named running tests — command S |
|---|---|---|
| R1 — separate UI, bind/token/Host/Origin/CSRF guards | PASS | `tests/test_config_ui.py::test_the_module_entry_point_calls_main`; `tests/test_config_ui.py::test_ac1_a_non_loopback_host_is_refused_before_any_bind`; `tests/test_config_ui.py::test_ac2_each_start_has_a_fresh_256_bit_token`; `tests/test_config_ui.py::test_ac3_every_route_without_a_session_is_refused_without_content`; `tests/test_config_ui.py::test_ac4_a_post_without_or_with_a_wrong_csrf_token_is_refused`; `tests/test_config_ui.py::test_ac4_a_non_post_to_a_state_changing_path_is_405`; `tests/test_config_ui.py::test_ac6_refused_hosts_get_403_even_on_get`; `tests/test_config_ui.py::test_ac6_refused_origins_get_403_and_change_nothing`; `tests/test_config_ui.py::test_ac5_the_socket_patch_is_effective` |
| R2 — shared overlay, precedence, base preservation | PASS | `tests/test_overlay.py::test_ac7_merged_document_is_exact_and_inputs_are_unmutated`; `tests/test_overlay.py::test_ac7_overlay_null_replaces_the_base_value`; `tests/test_overlay.py::test_ac9_overlay_below_minimum_is_refused_then_deleting_it_accepts`; `tests/test_overlay.py::test_ac9_run_hands_the_module_the_overlay_value`; `tests/test_overlay.py::test_ac9_explicit_overlay_is_honoured_by_main_and_run`; `tests/test_overlay.py::test_overlay_is_merged_before_environment_resolution`; `tests/test_overlay.py::test_relative_modules_directory_resolves_from_the_base_directory`; `tests/test_overlay.py::test_ac10_bad_overlay_names_the_file_never_the_content` |
| R3 — all titles/defaults and annotation validation | PASS | `tests/test_manifest_presentation.py::test_presented_covers_every_shipped_manifest`; `tests/test_manifest_presentation.py::test_every_setting_node_has_a_title`; `tests/test_manifest_presentation.py::test_every_documented_default_is_declared`; `tests/test_manifest_presentation.py::test_every_declared_default_validates_against_its_node`; `tests/test_manifest_presentation.py::test_default_violating_the_node_is_refused_as_default`; `tests/test_manifest_presentation.py::test_default_does_not_constrain_validated_values`; `tests/test_manifest_presentation.py::test_property_named_default_is_a_property_not_an_annotation` |
| R4 — generated base/core/module pages and honest controls | FAIL — N3/N5 | `tests/test_config_ui.py::test_ac15_the_base_page_lists_every_module_and_the_origin`; `tests/test_config_ui.py::test_ac16_every_schema_path_is_shown_with_title_help_and_default`; `tests/test_config_ui.py::test_ac17_a_module_added_as_a_directory_gets_its_titled_page`; `tests/test_config_ui.py::test_ac17_no_ui_source_file_names_a_shipped_module`; `tests/test_config_ui.py::test_ac18_unrenderable_nodes_get_notices_and_controls_keep_the_schema`; `tests/test_config_ui.py::test_ac19_the_twitch_page_shows_the_configured_channel_policy`; `tests/test_config_ui.py::test_ac20_the_core_page_renders_every_declared_limit`; `tests/test_config_ui.py::test_ac20_secrets_and_actions_are_read_only_with_every_rule`; `tests/test_config_ui.py::test_ac20_an_uncovered_action_is_listed_until_a_rule_covers_it`; `tests/test_config_ui.py::test_ac21_readiness_combines_the_check_verdict_and_the_running_state` |
| R5 — unsaved Check through existing path, all diagnostics, no side effects | FAIL — N3 refusal/normalization | `tests/test_config_ui.py::test_ac38_an_unsaved_invalid_edit_fails_and_writes_nothing`; `tests/test_config_ui.py::test_ac38_an_unsaved_fix_passes_and_writes_nothing`; `tests/test_config_ui.py::test_ac22_unresolved_references_are_all_reported_and_stop_the_check`; `tests/test_config_ui.py::test_ac22_every_invalid_setting_is_reported_in_one_check`; `tests/test_config_ui.py::test_ac23_check_signals_nothing_starts_nothing_and_creates_no_socket`; `tests/test_config_ui.py::test_bridge_check_runs_the_real_checker_from_a_running_loop` |
| R6 — exact writing scope, managed path, atomicity, stale checks, removal | FAIL — N3 lossy normalization | `tests/test_config_ui.py::test_ac24_each_save_writes_exactly_its_override_and_logs_paths_only`; `tests/test_config_ui.py::test_ac25_remove_override_restores_the_base_value_and_prunes`; `tests/test_config_ui.py::test_ac26_an_invalid_save_returns_a_field_diagnostic_and_writes_nothing`; `tests/test_config_ui.py::test_ac26_a_stale_save_or_remove_is_refused`; `tests/test_config_ui.py::test_ac27_writes_outside_the_scope_or_at_protected_fields_are_refused`; `tests/test_config_ui.py::test_ac27_posted_protected_or_out_of_scope_fields_are_refused`; `tests/test_config_ui.py::test_ac27_hand_written_actions_and_secrets_survive_an_unrelated_save`; `tests/test_config_ui.py::test_ac27_a_save_or_remove_through_a_yaml_alias_leaves_the_other_path_unchanged`; `tests/test_config_ui.py::test_ac27_an_ancestor_save_must_keep_every_protected_descendant`; `tests/test_config_ui.py::test_ac28_an_interrupted_write_leaves_the_previous_overlay_intact`; `tests/test_config_ui.py::test_ac28_the_write_targets_only_the_managed_overlay` |
| R7 — supervised Apply, status publication/classification, drift, bounded result | PASS | `tests/test_config_ui.py::test_ac29_a_failing_on_disk_check_refuses_and_touches_no_process`; `tests/test_config_ui.py::test_ac30_a_ready_record_of_the_child_with_the_on_disk_digest_is_accepted`; `tests/test_config_ui.py::test_ac30_a_child_exiting_2_is_refused_with_its_status_and_diagnostic`; `tests/test_config_ui.py::test_ac30_a_child_writing_nothing_within_the_window_is_unknown`; `tests/test_config_ui.py::test_ac31_apply_never_signals_a_foreign_record_pid`; `tests/test_config_ui.py::test_ac32_drift_follows_the_overlay_and_the_record`; `tests/test_config_ui.py::test_ac39_ac40_a_child_writing_only_an_unaccepted_record_is_unknown`; `tests/test_config_ui.py::test_ac40_every_listed_mutation_of_v_is_unusable_with_a_value_free_reason`; `tests/test_status_record.py::test_three_transitions_publish_three_records_in_order`; `tests/test_status_record.py::test_the_record_describes_the_loaded_configuration`; `tests/test_status_record.py::test_a_status_path_naming_a_configuration_file_is_refused` |
| R8 — no secret in responses, diagnostics, logs or reports | PASS in executed disclosure probes | `tests/test_config_ui.py::test_ac35_no_secret_value_reaches_any_response_log_report_or_record`; `tests/test_config_ui.py::test_check_diagnostics_pass_the_redaction_guard`; `tests/test_config_ui.py::test_d9_the_refused_tail_is_redacted_and_value_free`; `tests/test_config_ui.py::test_ac24_a_saved_path_naming_a_secret_is_logged_redacted` |
| R9 — dependencies and offline resources | PASS | `tests/test_main.py::test_pyproject_declares_the_console_script_and_the_build_backend_for_tests`; `tests/test_config_ui.py::test_pages_use_inline_assets_and_relative_targets_only`; `tests/test_config_ui.py::test_r9_module_pages_use_inline_assets_and_relative_targets_only`; `tests/test_config_ui.py::test_ac36_the_url_audit_finds_every_external_target`; `tests/test_config_ui.py::test_ac36_a_configured_url_is_escaped_text_only` |
| R10 — operator documentation | PASS coverage | `tests/test_config_ui.py::test_ac37_doc_launch_and_local_only`; `tests/test_config_ui.py::test_ac37_doc_overlay_rule_and_merge`; `tests/test_config_ui.py::test_ac37_doc_writable_and_read_only_blocks`; `tests/test_config_ui.py::test_ac37_doc_secrets_policy`; `tests/test_config_ui.py::test_ac37_doc_restart_drift_and_status_file` |

### Gate-1 operator-visible guarantees

- **Writing scope / 6c — PASS boundaries; S + C/F1:** five writable blocks; actions/secrets/credentials/references and protected ancestors remain protected; managed overlay/base distinction enforced. N3 still permits lossy normalization within a writable ordinary setting.
- **No secret escapes — PASS executed probes; S + C/P/D:** original short, identifier-alias, escaped/nested and punctuation canaries withheld from configured data, diagnostics, logs and reports; no proof of every possible secret/context.
- **Overlay — PASS storage semantics; S + C/P/D identity:** merge precedence, scalar/list/null replacement, CLI/runtime parity, removal, stale Save, base preservation and atomicity; N3 remains a draft-content defect.
- **Local-only surface — PASS tested surface; S AC1–AC6/AC41 + C/F4 + P/body:** loopback startup, token/session, Host/Origin, POST/CSRF, bounded admitted work/body bytes.
- **One generated page per module — generation PASS, full usability FAIL; S AC15–AC20 + M + E:** 17 manifests plus new-directory fixture, titles/help/defaults and notices; zero module HTML; N3/N5 defeat some edits.
- **Check — side effects/real bridge/layout PASS, full normalization contract FAIL; S AC22/AC23/AC38 + D/N2 + D/N3 + E:** original N3 parity fixed; edited/ambiguous key cases still pass with incorrect drafts.
- **Apply — PASS tested outcomes; S AC29–AC34/AC39–AC43 + C/F5:** accepted/refused/unknown, loaded-document digest/drift, foreign-pid isolation, bounded stop waits and drained stderr. No claim of a 60-second whole-transaction deadline.
- **Dependencies/offline — PASS static evidence; S AC36 + G:** only added UI console entry; dependencies unchanged; inline assets/relative targets, no render-time fetch. No live browser offline test.
- **Operator documentation — PASS coverage; S AC37 + source read:** local launch, overlay/merge/scope/secrets, restart/drift/status and 10-second terminate/5-second kill/60-second post-start window documented.

### Phases 0–3 regression guarantees

All following named tests ran and passed under **S**; M verified unchanged module Python implementations and unchanged operational manifest declarations.

| Guarantee | Running evidence — command S |
|---|---|
| Default-deny authorization, including reads | `tests/test_actions.py::test_zero_rules_refuse_a_read_and_a_write_with_zero_invocations`; `tests/test_actions.py::test_a_read_rule_authorizes_the_read_only` |
| Bounded admission and retention | `tests/test_admission.py::test_full_session_queue_and_global_cap_reject_with_reason_and_depth`; `tests/test_admission.py::test_three_simultaneous_sessions_never_exceed_the_worker_limit`; `tests/test_retention.py::test_ac20_bus_history_byte_limit_evicts_before_the_count_limit_without_failing_a_publication`; `tests/test_retention.py::test_ac30_byte_cap_leaves_fewer_than_three_exchanges_and_at_most_two_hundred_bytes` |
| Explicit terminal outcomes | `tests/test_actions.py::test_the_six_terminal_statuses_are_produced_exactly`; `tests/test_admission.py::test_total_deadline_after_emission_is_classified_external_unknown_once` |
| One global startup/shutdown deadline | `tests/test_main.py::test_a_hanging_activation_is_bounded_by_the_startup_deadline`; `tests/test_main.py::test_a_hanging_settings_validator_is_bounded_by_the_startup_deadline`; `tests/test_lifecycle.py::test_one_global_shutdown_deadline_caps_the_sum_of_local_timeouts`; `tests/test_shutdown.py::test_cli_deadline_covers_cancellation_resistant_task` |
| Runtime secret redaction | `tests/test_main.py::test_configured_secrets_are_redacted_from_traces_and_loss_diagnostics_through_the_entry_point`; `tests/test_main.py::test_declared_credentials_are_redacted_without_a_secrets_entry_through_the_entry_point` |
| Phase 2 R10 deadline ruling | `tests/test_audio_input.py::test_the_only_deadline_subtractions_are_the_two_ac41_authorizes`; `tests/test_audio_output.py::test_the_stop_grace_runs_after_the_stop_never_before_the_deadline`; `tests/test_audio_input.py::test_the_recorder_is_still_killed_at_the_deadline_itself_with_transcription` |
| No default provider/model | `tests/test_audio_output.py::test_an_empty_endpoint_sends_no_request_and_leaves_speak_unbound`; `tests/test_hygiene.py::test_ac45_core_and_modules_name_no_model`; phase-4 manifest comparison below confirms no operational provider declarations changed |
| Moderation modes, strictest default, no permanent bans | `tests/test_moderation.py::test_ac24_the_default_mode_alerts_with_no_platform_request_and_one_fact`; `tests/test_moderation.py::test_ac26_a_request_in_mode_propose_yields_a_proposal_id_and_no_request`; `tests/test_moderation.py::test_ac26_auto_apply_applies_a_delete_at_once_under_the_strict_rules`; `tests/test_moderation.py::test_no_operation_removes_a_viewer_permanently` |
| Viewer-memory bounds and eviction | `tests/test_viewer_memory.py::test_ac14_eviction_deletes_the_least_recent_then_least_used_with_one_fact`; `tests/test_viewer_memory.py::test_ac15_the_total_bound_holds_and_the_target_is_never_deleted`; `tests/test_viewer_memory.py::test_ac18_the_bounds_hold_before_readiness`; entire viewer-memory suite passes |
| Requested-only watch by default | `tests/test_watch.py::test_ac29_without_activation_no_tick_is_emitted`; `tests/test_watch.py::test_ac29_a_viewers_start_command_starts_nothing`; `tests/test_watch.py::test_ac29_the_broadcasters_start_command_ticks_every_interval` (explicit startup activation remains supported) |
| Honest platform capabilities | `tests/test_moderation.py::test_ac27_delete_message_on_kick_is_platform_unsupported_with_no_request`; `tests/test_clips.py::test_ac12_with_twitch_and_kick_clips_are_ready_for_twitch_and_unsupported_on_kick`; `tests/test_youtube.py::test_the_moderation_service_offers_delete_and_timeout_and_no_clip_or_poll` |

### Whole-branch and assertion audit

G reviewed the cumulative contracts/runtime/overlay/UI integration and the documented phase boundary. Runtime publication and UI drift share `deep_merge` and `canonical_digest`, with the digest taken over unresolved loaded data; Check and Save both reach `_draft_value` and `_apply_edits`. Shared restoration fixes parity but shares the N3 bugs too. Request guards precede routes; mutations use their lock; the aiohttp adapter reaches handlers through bounded `_dispatch`; supervisor waits are bounded and its stderr drain starts for launched piped stderr.

M compared all **17** manifests with the baseline, removing only presentation annotations from schemas: all remaining declarations match. Module Python implementations are unchanged, zero module HTML, and no root `*.local.yaml` was present. Packaging changes only add the UI entry point.

Pre-existing assertion adaptations preserve substantive checks: users schema annotation allowance, exact two-console-script assertion and the documented thread-backed presence wait wall-time floor. No removed safety assertion was found that explains the new defects. The existing tautology at `tests/test_config_ui.py:1325` remains unusable as evidence. The new N4 assertions meaningfully decode JSON. The N3 unmatched-marker test at `tests/test_config_ui.py:6406` tests `x[hidden]` but neither a suffixed full stand-in nor conflicting restored key aliases; it also does not assert its captured checker input is empty on normalization refusal. These are coverage gaps, not suite failures.

## NEW FINDINGS

### N5 — MEDIUM — data-level redaction collapses distinct mapping entries before serialization

**Locations:** `core/config_ui/__init__.py:1186`–`:1191` (`_shown` dict comprehension), `:1200` (`_shown_key`), `:1292` (`_restore_mapping`). **Executed E/N5**, actual GET/Check/Save handlers, shipped field-schema validation, intercepted commit.

Collect two secrets `q7Z` and `second-secret`. Configure schema-valid brain actions:

```python
[{'action': 'x.do',
  'arguments': {'xq7Z': 'one', 'xsecond-secret': 'two'}}]
```

The actual HTML-unescaped, JSON-decoded textarea contains:

```python
[{'action': 'x.do', 'arguments': {'x[hidden]': 'two'}}]
```

The complete first key/value entry, including nonsecret value `one`, disappears. `_hidden_keys` only allocates distinct stand-ins for keys exactly equal to a secret; these two keys merely contain secrets. `_shown_key` maps both to `x[hidden]`, and the dict comprehension discards the first before JSON serialization. Appending unrelated `arguments.added = 'ok'` to this rendered data makes Check fail with the explicit unmatched reason and Save return **403**, with no commit. The operator cannot inspect both configured entries or perform the otherwise ordinary edit.

A second executed variant uses `{'xq7Z': 'one', 'x[hidden]': 'two'}` with only secret `q7Z`: the actual configured marker-looking key collides with the generated redaction key, again hiding the first entry. An unrelated outer edit can restore the entire hidden mapping, so the visible mapping also understates what an accepted draft retains.

Required: give every redacted configured key a collision-free representation, including substring-redacted keys and collisions with legitimate marker-looking text. Preserve entry count/content and restore identities without ambiguity. Cover both two-secret and legitimate-marker-key fixtures through decoded GET and edited round trips.

## Executable residual/new counterexamples

Run from the repository root with `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -` and the following stdin. It uses in-memory configuration reads and an intercepted commit; no source/test/configuration file is created. The real runtime checker is used for audio output. The brain fixture's actual field schema is validated; its checker is injected solely to observe normalization, without claiming runtime acceptance of the illustrative action.

```python
from pathlib import Path
from contextlib import ExitStack
from unittest.mock import patch
from urllib.parse import urlencode
import copy, re, html, json
import core.config_ui as u
import core.main as main
from core.contracts import validate_against_schema


def exercise(base, env, module, field, edit):
    with ExitStack() as stack:
        for name, value in [
            ('read_base', base), ('read_overlay', {}),
            ('_file_state', (False, None, None)),
            ('_file_fingerprint', u.FINGERPRINT_ABSENT),
            ('read_status', u.StatusReading.absent()),
        ]:
            stack.enter_context(patch.object(u, name, return_value=value))
        stack.enter_context(patch.object(main, 'read_base', return_value=base))
        ui = u.ConfigUI(u.UISettings(Path('/nonexistent-gate/config.yaml')), environ=env)
        login = ui.handle(u.UIRequest('GET', '/', {'token': ui.token},
                                      {'host': ui.access_authority}))
        headers = {'host': ui.access_authority,
                   'cookie': dict(login.headers)['Set-Cookie'].split(';')[0]}
        session = ui._session(u.UIRequest('GET', '/', {}, headers))
        headers.update({'x-csrf-token': session.csrf_token,
                        'content-type': 'application/x-www-form-urlencoded'})
        page = ui.handle(u.UIRequest('GET', '/module/' + module, {}, headers)).body.decode()
        name = 'modules.' + module + '.' + field
        shown = json.loads(html.unescape(re.search(
            '<textarea name="' + re.escape(name) + '"[^>]*>(.*?)</textarea>',
            page, re.S).group(1)))
        if module == 'audio_output':
            assert ui.check().passed
        else:
            schema = ui.view().modules['brain'].manifest['settings_schema']['properties']['delivery']['properties']['actions']
            validate_against_schema(base['modules']['brain']['delivery']['actions'],
                                    schema, label='delivery.actions')
            ui.checker = lambda *args: (True, [])
        proposed = edit(copy.deepcopy(shown))
        body = urlencode({
            'layout': re.search('name="layout" value="([^"]+)"', page).group(1),
            'fingerprint': 'absent', name: json.dumps(proposed),
        }).encode()
        with patch.object(ui, '_commit', return_value=u.WriteResult(u.OUTCOME_SAVED)) as commit:
            checked = ui.handle(u.UIRequest('POST', '/check', {}, headers, body))
            verdict = ui.last_check
            saved = ui.handle(u.UIRequest('POST', '/save', {}, headers, body))
            draft = commit.call_args.args[0] if commit.called else None
        assert checked.status == 200
        return shown, verdict, saved, draft


secret = 'q7Z'
module = {
    'synthesis': {'endpoint': '', 'model': ''},
    'voices': {'allowed': ['kept', secret, secret], 'default': '${GATE_SECRET}'},
    'outputs': {'o': {'player': {'argv': ['cat']}}}, 'default_output': 'o',
}
base = {'modules_directory': 'builtin', 'enabled_modules': ['audio_output'],
        'secrets': ['${GATE_SECRET}'], 'modules': {'audio_output': module}}
shown, verdict, saved, draft = exercise(
    base, {'GATE_SECRET': secret}, 'audio_output', 'voices.allowed',
    lambda value: [value[0], value[1], value[2] + ' edited'])
assert verdict.passed and saved.status == 303
assert draft['modules']['audio_output']['voices']['allowed'] == [
    'kept', secret, u.HIDDEN_LITERAL + ' edited']
print('E/N3 edited stand-in accepted by real Check and Save')

for reverse in (False, True):
    base = {'modules_directory': 'builtin', 'enabled_modules': [],
            'secrets': ['${GATE_SECRET}'], 'modules': {'brain': {'delivery': {
                'actions': [{'action': 'x.do', 'arguments': {secret: 'original'}}]}}}}
    def mixed(value):
        pairs = [*value[0]['arguments'].items(), (secret, 'second')]
        value[0]['arguments'] = dict(reversed(pairs) if reverse else pairs)
        return value
    shown, verdict, saved, draft = exercise(
        base, {'GATE_SECRET': secret}, 'brain', 'delivery.actions', mixed)
    assert verdict.passed and saved.status == 303
    assert draft['modules']['brain']['delivery']['actions'][0]['arguments'] == {
        secret: 'original' if reverse else 'second'}
    print('E/N3 conflicting aliases accepted; reversed order =', reverse)

base = {'modules_directory': 'builtin', 'enabled_modules': [],
        'secrets': ['${GATE_SECRET}', '${SECOND}'], 'modules': {'brain': {'delivery': {
            'actions': [{'action': 'x.do', 'arguments': {
                'xq7Z': 'one', 'xsecond-secret': 'two'}}]}}}}
def add(value):
    value[0]['arguments']['added'] = 'ok'
    return value
shown, verdict, saved, draft = exercise(
    base, {'GATE_SECRET': secret, 'SECOND': 'second-secret'},
    'brain', 'delivery.actions', add)
assert shown[0]['arguments'] == {'x[hidden]': 'two'}
assert not verdict.passed and saved.status == 403 and draft is None
assert any(u._UNMATCHED_REASON in line for line in verdict.diagnostics)
print('E/N5 entry lost from GET; unrelated edit refused')
```

### Execution accounting

The initial sandbox could not initialize (`bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted`); explicit commands ran through the approved execution fallback. Automatic approval review rejected a command dynamically executing blocks extracted from earlier reports because unspecified side effects were not verifiable. It was not run. The safer alternative used explicit inspected code with in-memory reads and intercepted writes; all requested published input families were then exercised. Nothing remains blocked by that rejection.

The first D defeat run asserted that a suffixed full stand-in would be refused and stopped when it was accepted. All ten N4 characters and the original real-checker N3 replay had already passed. A follow-up observation run completed the remaining cases, then E confirmed the real-checker residual and both key-order outcomes with positive assertions. This was a discovered product failure, not a suppressed suite failure. No test/source was edited to obtain the green suite.

## NOT VERIFIED

- No real browser/JavaScript session, live HTTP listener/load test, full navigation/unsaved-edit-preservation test or installed-console smoke test. Exact rendered controls were submitted to real handlers.
- S has 18 environment-dependent skips; capture process-group cases, phase-2/phase-3 opt-in trials and distribution installation remain outside this run. No live providers/platform calls, audio hardware, capture devices or moderation endpoints.
- Extra Save probes intercept commit/write sinks. S covers actual temporary-file persistence, base preservation and atomicity. No real base was overwritten; no exhaustive filesystem race, permission-error or external-writer-interleaving proof.
- Real existing hard links/symlink aliases were inspected. Bind-like ancestor and case-fold fallback branches were simulated, not exercised on newly mounted or case-insensitive filesystems.
- No OS process stuck after SIGKILL was induced. Bounded doubles and S's ordinary process tests ran; no total 60-second deadline for Check/filesystem/spawn/full Apply, or slow-client body-read deadline, was established.
- Brain mapping probes establish schema-valid editor/normalization behavior; they do not claim the illustrative `x.do` action is a runnable enabled brain configuration. N3's edited-voice reproduction independently uses the actual checker on a valid enabled module.
- No proof of absolute absence of further defects. N3 residuals and executed MEDIUM N5 prevent closure despite the green suite and resolved N4.
