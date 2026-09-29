# Phase 4 — full-branch closing gate, second pass

## VERDICT

REQUEST_CHANGES

Reviewed `main` at `baa157b4f06fb438b574d205cb302ecb5b7a7de7`, cumulatively against `835d9b4`, the same phase boundary as gate 1, and against phase-4 `spec.md`, `plan.md`, `brief.md` and the second-pass requirements. This is a full-branch review, not separate verdicts on the two fix commits.

The suite is green: **2917 passed, 18 skipped in 95.26 s**, exit 0. Phase 4 still cannot close: F2 retains a demonstrated configured-key secret leak, F1 misses hard-link identity, and the fix round introduces N1, which corrupts boolean submissions in the presence of an unrelated short secret.

Only this report was created in the repository; no source/test/configuration edits, staging or commits. Existing untracked `docs/runs/` directories were unchanged. The required suite ran its normal temporary-file fixtures; the additional probes create no files, open no listeners and intercept write sinks. The default sandbox failed before execution with `bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted`; execution succeeded through the approved fallback.

## PREVIOUS FINDINGS

### F1 — HIGH in gate 1 — PARTIALLY RESOLVED

**Code:** `core/overlay.py:182`, `:217`, `:225`; `core/config_ui/__init__.py:178`, `:246`, `:257`, `:1925`, `:2886`, `:3724`, `:3884`; runtime `core/main.py:350`.

**Executed tests:** under S, `tests/test_config_ui.py::test_f1_an_overlay_naming_the_base_by_any_spelling_refuses_to_start`, `test_f1_an_overlay_that_cannot_be_canonicalised_refuses_to_start`, `test_f1_every_path_is_canonical_and_the_runtime_gets_the_same_files`, `test_the_runtime_is_launched_on_the_managed_overlay_of_a_linked_base`, `test_f1_save_never_replaces_the_base_whatever_the_overlay_spelling`; `tests/test_overlay.py::test_every_spelling_of_one_file_has_one_canonical_form`. Additional executed probe: P/`test_gate2_path_aliases`.

**C/F1 replay:** the original literal `~/gate-base.yaml` now produces `overlay_path_collision` at startup. The deliberately constructed UI's authenticated Save returns **403**, and intercepted `os.replace` has **zero calls**. No serialized replacement of the base occurs. Canonical paths now reach reading, managed destination, status checks and the default runtime argv. The linked-base test also checks that the runtime gets the explicitly managed overlay rather than deriving a different filename.

**Attempts to defeat it:** tilde, `./`, `..`, relative/absolute mixtures, environment-variable expansion and a real directory symlink are refused when they name the base. Existing tests additionally exercise a file symlink and invalid/unresolvable paths. A distinct ordinary overlay and an implicit overlay beside a linked base remain accepted.

**Remaining counterexample:** P uses two already existing hard links, `/usr/bin/bzip2` and `/usr/bin/bzcat`, without reading their contents or writing them:
`os.path.samefile(a,b) == True`, but `u.same_file(a,b) == False`,
`startup_checks(UISettings(a, overlay=b)) == []`, and
`status_path_collision(b,a,None) is None`.
These files provide a real same-inode namespace test of the path-only guards; they are not presented as valid YAML profiles. The guards compare resolved path strings and never compare device/inode identity. Thus the explicitly requested hard-link refusal before binding is absent, as is that identity check for the status path.

This residual is a **MEDIUM boundary defect**, not a reproduced HIGH base overwrite: atomic replacement of one hard-link directory entry normally leaves the other entry's inode/content intact. The original destructive tilde failure is fixed. Keep canonical path handling, add existing-file identity checks, and cover hard-linked YAML base/overlay/status fixtures.

### F2 — HIGH — PARTIALLY RESOLVED

**Code:** `core/config_ui/__init__.py:1092` removes the length threshold; `:1160` implements opaque field names; **`:1176` still exempts any segment whose text occurs in `declared_names`**, populated at `:1362`. This is a text-membership test, not a test of whether this occurrence is a schema-declared segment.

**Executed tests:** S includes `tests/test_config_ui.py::test_redaction_covers_values_of_every_length`, `test_f2_a_short_secret_in_an_ordinary_setting_is_never_rendered`, `test_f2_a_credential_used_as_a_trigger_channel_key_is_never_rendered`, `test_check_diagnostics_pass_the_redaction_guard`, and the full AC35 sweep. P/`test_gate2_secret_positions` tests additional positions and lengths.

**C/F2a replay:** HTTP **200**, `short_secret_leaked=False` for the original listed `q7Z` secret used as `companion_name`.

**C/F2b replay:** HTTP **200**, `credential_in_attribute=False` for the original `gate-secret-channel-97bf` credential reused as a channel key. The named S test also saves and removes that channel through the opaque name.

**Attempts to defeat it:** the one-character `Ω`, `q7Z` and the original long credential are hidden from ordinary configured inputs, configured channel-control names, label rendering and injected error text. However, use **`rules`**, **`twitch`** or **`combination`** both as the credential/collected secret and as a configured channel key. GET `/module/twitch` returns **200** containing, respectively:
`name="triggers.twitch.channels.rules.combination"`,
`name="triggers.twitch.channels.twitch.combination"`,
or `name="triggers.twitch.channels.combination.combination"`.

These are disclosures of the configured key occurrence, not merely coincidental words in fixed HTML. The ordinary input and error text are redacted in the very same response path. The opaque-name fix is defeated by its structural exemption, directly contrary to the second-pass requirement. `test_f2_declared_identifiers_are_public_text_and_stay_intact` passes but does not test a configured key whose spelling happens to equal a declared identifier. Eliminate this exemption for configured positions and test the complete response.

### F3 — MEDIUM — PARTIALLY RESOLVED

**Code:** `core/config_ui/__init__.py:1715` renders the three states; `:2653` compares submitted text with the rendered state; `:3206` coerces only literal `true`/`false`. The new interaction defect is at `:1746` (N1 below).

**Executed tests:** S includes `tests/test_config_ui.py::test_f3_an_unset_true_default_boolean_renders_its_effective_value`, `test_f3_a_boolean_draft_expresses_unset_false_and_true` (all three cases), `test_f3_a_non_boolean_configured_value_posts_untouched`, and `test_f3_a_configured_boolean_spelling_string_can_be_corrected`. Also P/`test_gate2_boolean_redaction`.

**C/F3 replay:** runtime probe remains **True**; parser now returns
`[(('modules','audio_output','synthesis','probe'), False)]`;
the injected checker receives
`{'modules': {'audio_output': {'synthesis': {'probe': False}}}}`.
The old probe still prints `unchecked=True` because it searches for a checkbox that no longer exists; this is not evidence of an incorrect current control. The new select explicitly shows `not set (default: true)`, alongside distinct true and false options. The named S test actually saves false, reads YAML false back from the overlay, and observes runtime false.

The original default/false-loss case is fixed. The broader honest-boolean guarantee is still incomplete because N1 makes a legitimate boolean option submit a string when its token overlaps a short secret. This is why the finding is not marked fully resolved.

### F4 — MEDIUM — RESOLVED

**Code:** `core/config_ui/__init__.py:4171`, `:4243`, `:4278`, `:4294`.

**Executed tests:** S includes `tests/test_config_ui.py::test_f4_a_saturated_bridge_refuses_honestly_and_queues_nothing_more`, `test_f4_a_failed_body_read_releases_its_admission`, and `test_bridge_dispatch_runs_handle_on_a_config_ui_worker`. P/`test_gate2_body_limit` additionally executes the installed aiohttp Request.read implementation through the captured production adapter.

**C/F4 replay:** same four workers, same 128 finite blocked requests: **12 queued**, **16 admitted total**, **112 receive 503**, and 16 eventually receive the handler's 401. Replay supplies the new shared `_Admission` argument; it does not substitute a different dispatch implementation. S proves overflow bodies are not read, refusal says nothing was done and carries `Retry-After: 1`, and capacity is released after completion or a failed read.

**Body boundary:** actual adapter results are **0 bytes → 200; 1,048,576 bytes → 200; 1,048,577 bytes → 413; subsequent 1 byte → 200**. No healthy at-limit refusal or leaked admission slot was reproduced. This verifies bounded body size, not a wall-clock deadline on a stalled upload. No live HTTP saturation test was performed.

### F5 — MEDIUM — RESOLVED

**Code:** `core/config_ui/__init__.py:3979`, `:3985`, `:3987`, `:4034`, `:4053`; `docs/config-ui.md:250`.

**Executed tests:** S includes `tests/test_config_ui.py::test_stop_waits_for_the_grace_before_killing`, `test_f5_an_unkillable_child_is_reported_and_kept_not_waited_on_forever`, `test_f5_apply_with_an_unkillable_child_is_refused_and_starts_nothing`, `test_f5_close_logs_an_unkillable_child`, `test_ac31_a_child_ignoring_terminate_is_killed_after_the_bounded_wait`, and `test_the_apply_window_runs_on_the_injected_clock`.

**C/F5 replay:** the original timeout-on-every-bounded-wait child now produces
`terminate, wait(10.0), kill, wait(5.0)`; `stop()` returns **False** and retains the child. There is no `wait(None)`. S proves restart reports **refused**, names the unconfirmed old pid, starts no new child, and close logs the unconfirmed outcome.

Accounting is now explicit: up to 10 s terminate wait plus 5 s post-kill wait (and bounded drain cleanup when applicable), followed by the **60 s acceptance window after starting the new child**. This matches the documented post-start window; it is not a claim that the entire Check/stop/spawn/Apply transaction finishes in 60 s. The old unbounded post-kill wait is eliminated.

## CHECKS

Commands C and P below are executable stdin probes embedded in this report; their test labels identify the observations above. An observation probe may exit zero while proving a gate failure.

- **PASS — S:** `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/ -q -p no:cacheprovider` — **2917 passed, 18 skipped**, exit 0, 95.26 s.
- **PASS — original counterexamples retested:** command **C** below — C/F1, C/F2a, C/F2b, C/F3, C/F4 and C/F5 all executed with original inputs and current outcomes reported above.
- **FAIL — hard-link refusal:** command **P**, `test_gate2_path_aliases` — all requested spelling classes exercised; existing hard link admitted, including status collision check.
- **FAIL — all secret positions:** command **P**, `test_gate2_secret_positions` — configured channel keys matching declared names still leak.
- **FAIL — healthy boolean submission with short secret:** command **P**, `test_gate2_boolean_redaction` — true becomes string `t[hidden]ue`, Save 422.
- **PASS — body bytes and recovery:** command **P**, `test_gate2_body_limit` — 1 MiB accepted, 1 MiB + 1 refused with 413, next request accepted.
- **PASS — cumulative scope / whitespace:** `git diff --stat 835d9b4..HEAD`; `git diff --check 835d9b4..HEAD` — 62 changed files, no whitespace errors.
- **PASS — regression assertion review:** `git diff 835d9b4..HEAD -- tests/test_main.py tests/test_users.py tests/conftest.py tests/test_presence_pack.py`; `git diff cfe5fad^..HEAD -- tests/test_config_ui.py` — allowed metadata/script adaptations preserve the operational assertions; short-secret and unbounded-wait assertions were correctly inverted. See qualification below.
- **PASS — manifest/runtime inventory:** `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -` (M described below) — all 17 manifests retain non-presentation semantics; no runtime module Python implementation changed; all 109 distinct test names cited by gate 1 still exist and were included in S.
- **PASS — repository write scope:** `git status --short` before/after — only this report added beyond the pre-existing untracked run directories; nothing staged.
- **NOT-VERIFIABLE — browser/listener/install/live-platform operation:** S and socket-free C/P do not establish these; limits listed below.

### Requirement map R1–R10

Every named test in this table ran and passed in S, including its parameterizations. Requirement failures below arise from additional executed counterexamples despite those passing tests.

| Requirement | Result | Named running tests (command S) and current conclusion |
|---|---|---|
| R1 — separate UI, bind/token/Host/Origin/CSRF guards | **PASS** | `tests/test_config_ui.py::test_the_module_entry_point_calls_main`; `tests/test_config_ui.py::test_ac1_a_non_loopback_host_is_refused_before_any_bind`; `tests/test_config_ui.py::test_ac2_each_start_has_a_fresh_256_bit_token`; `tests/test_config_ui.py::test_ac3_every_route_without_a_session_is_refused_without_content`; `tests/test_config_ui.py::test_ac4_a_post_without_or_with_a_wrong_csrf_token_is_refused`; `tests/test_config_ui.py::test_ac4_a_non_post_to_a_state_changing_path_is_405`; `tests/test_config_ui.py::test_ac6_refused_hosts_get_403_even_on_get`; `tests/test_config_ui.py::test_ac6_refused_origins_get_403_and_change_nothing`; `tests/test_config_ui.py::test_ac5_the_socket_patch_is_effective`. Startup and request guards; F4 admission now passes. |
| R2 — shared overlay, precedence, base preservation | **FAIL** | `tests/test_overlay.py::test_ac7_merged_document_is_exact_and_inputs_are_unmutated`; `tests/test_overlay.py::test_ac7_overlay_null_replaces_the_base_value`; `tests/test_overlay.py::test_ac9_overlay_below_minimum_is_refused_then_deleting_it_accepts`; `tests/test_overlay.py::test_ac9_run_hands_the_module_the_overlay_value`; `tests/test_overlay.py::test_ac9_explicit_overlay_is_honoured_by_main_and_run`; `tests/test_overlay.py::test_overlay_is_merged_before_environment_resolution`; `tests/test_overlay.py::test_relative_modules_directory_resolves_from_the_base_directory`; `tests/test_overlay.py::test_ac10_bad_overlay_names_the_file_never_the_content`. Normal merge/runtime parity passes; F1 hard-link boundary incomplete. |
| R3 — all titles/defaults and annotation validation | **PASS** | `tests/test_manifest_presentation.py::test_presented_covers_every_shipped_manifest`; `tests/test_manifest_presentation.py::test_every_setting_node_has_a_title`; `tests/test_manifest_presentation.py::test_every_documented_default_is_declared`; `tests/test_manifest_presentation.py::test_every_declared_default_validates_against_its_node`; `tests/test_manifest_presentation.py::test_default_violating_the_node_is_refused_as_default`; `tests/test_manifest_presentation.py::test_default_does_not_constrain_validated_values`; `tests/test_manifest_presentation.py::test_property_named_default_is_a_property_not_an_annotation`. Presentation annotations and defaults validate; 17 manifests retain semantics. |
| R4 — generated base/core/module pages and honest controls | **FAIL** | `tests/test_config_ui.py::test_ac15_the_base_page_lists_every_module_and_the_origin`; `tests/test_config_ui.py::test_ac16_every_schema_path_is_shown_with_title_help_and_default`; `tests/test_config_ui.py::test_ac17_a_module_added_as_a_directory_gets_its_titled_page`; `tests/test_config_ui.py::test_ac17_no_ui_source_file_names_a_shipped_module`; `tests/test_config_ui.py::test_ac18_unrenderable_nodes_get_notices_and_controls_keep_the_schema`; `tests/test_config_ui.py::test_ac19_the_twitch_page_shows_the_configured_channel_policy`; `tests/test_config_ui.py::test_ac20_the_core_page_renders_every_declared_limit`; `tests/test_config_ui.py::test_ac20_secrets_and_actions_are_read_only_with_every_rule`; `tests/test_config_ui.py::test_ac20_an_uncovered_action_is_listed_until_a_rule_covers_it`; `tests/test_config_ui.py::test_ac21_readiness_combines_the_check_verdict_and_the_running_state`. Original false-default control fixed; N1 breaks a rendered boolean choice. |
| R5 — unsaved Check through existing path, all diagnostics, no side effects | **FAIL** | `tests/test_config_ui.py::test_ac38_an_unsaved_invalid_edit_fails_and_writes_nothing`; `tests/test_config_ui.py::test_ac38_an_unsaved_fix_passes_and_writes_nothing`; `tests/test_config_ui.py::test_ac22_unresolved_references_are_all_reported_and_stop_the_check`; `tests/test_config_ui.py::test_ac22_every_invalid_setting_is_reported_in_one_check`; `tests/test_config_ui.py::test_ac23_check_signals_nothing_starts_nothing_and_creates_no_socket`; `tests/test_config_ui.py::test_bridge_check_runs_the_real_checker_from_a_running_loop`. Original false draft reaches Check; N1 supplies the wrong draft type. |
| R6 — exact writing scope, managed path, atomicity, stale checks, removal | **FAIL** | `tests/test_config_ui.py::test_ac24_each_save_writes_exactly_its_override_and_logs_paths_only`; `tests/test_config_ui.py::test_ac25_remove_override_restores_the_base_value_and_prunes`; `tests/test_config_ui.py::test_ac26_an_invalid_save_returns_a_field_diagnostic_and_writes_nothing`; `tests/test_config_ui.py::test_ac26_a_stale_save_or_remove_is_refused`; `tests/test_config_ui.py::test_ac27_writes_outside_the_scope_or_at_protected_fields_are_refused`; `tests/test_config_ui.py::test_ac27_posted_protected_or_out_of_scope_fields_are_refused`; `tests/test_config_ui.py::test_ac27_hand_written_actions_and_secrets_survive_an_unrelated_save`; `tests/test_config_ui.py::test_ac27_a_save_or_remove_through_a_yaml_alias_leaves_the_other_path_unchanged`; `tests/test_config_ui.py::test_ac27_an_ancestor_save_must_keep_every_protected_descendant`; `tests/test_config_ui.py::test_ac28_an_interrupted_write_leaves_the_previous_overlay_intact`; `tests/test_config_ui.py::test_ac28_the_write_targets_only_the_managed_overlay`. Ordinary scope/atomicity/stale checks pass; F1 hard-link refusal missing. |
| R7 — supervised Apply, status publication/classification, drift, bounded result | **FAIL** | `tests/test_config_ui.py::test_ac29_a_failing_on_disk_check_refuses_and_touches_no_process`; `tests/test_config_ui.py::test_ac30_a_ready_record_of_the_child_with_the_on_disk_digest_is_accepted`; `tests/test_config_ui.py::test_ac30_a_child_exiting_2_is_refused_with_its_status_and_diagnostic`; `tests/test_config_ui.py::test_ac30_a_child_writing_nothing_within_the_window_is_unknown`; `tests/test_config_ui.py::test_ac31_apply_never_signals_a_foreign_record_pid`; `tests/test_config_ui.py::test_ac32_drift_follows_the_overlay_and_the_record`; `tests/test_config_ui.py::test_ac39_ac40_a_child_writing_only_an_unaccepted_record_is_unknown`; `tests/test_config_ui.py::test_ac40_every_listed_mutation_of_v_is_unusable_with_a_value_free_reason`; `tests/test_status_record.py::test_three_transitions_publish_three_records_in_order`; `tests/test_status_record.py::test_the_record_describes_the_loaded_configuration`; `tests/test_status_record.py::test_a_status_path_naming_a_configuration_file_is_refused`. F5 stop bounds and ordinary status/Apply pass; hard-link status collision missed. |
| R8 — no secret in responses, diagnostics, logs or reports | **FAIL** | `tests/test_config_ui.py::test_ac35_no_secret_value_reaches_any_response_log_report_or_record`; `tests/test_config_ui.py::test_check_diagnostics_pass_the_redaction_guard`; `tests/test_config_ui.py::test_d9_the_refused_tail_is_redacted_and_value_free`; `tests/test_config_ui.py::test_ac24_a_saved_path_naming_a_secret_is_logged_redacted`. F2 configured channel key leaks when it matches a declared name. |
| R9 — dependencies and offline resources | **PASS** | `tests/test_main.py::test_pyproject_declares_the_console_script_and_the_build_backend_for_tests`; `tests/test_config_ui.py::test_pages_use_inline_assets_and_relative_targets_only`; `tests/test_config_ui.py::test_r9_module_pages_use_inline_assets_and_relative_targets_only`; `tests/test_config_ui.py::test_ac36_the_url_audit_finds_every_external_target`; `tests/test_config_ui.py::test_ac36_a_configured_url_is_escaped_text_only`. Packaging and inline/offline resource tests pass. |
| R10 — operator documentation | **PASS** | `tests/test_config_ui.py::test_ac37_doc_launch_and_local_only`; `tests/test_config_ui.py::test_ac37_doc_overlay_rule_and_merge`; `tests/test_config_ui.py::test_ac37_doc_writable_and_read_only_blocks`; `tests/test_config_ui.py::test_ac37_doc_secrets_policy`; `tests/test_config_ui.py::test_ac37_doc_restart_drift_and_status_file`. All documentation topic tests pass; stop/window accounting explicit. |

### Operator-visible guarantees

- **Writing scope / decision 6c — FAIL boundary, PASS ordinary edits:** S reran all five writable-block, protected credential/reference/ancestor, YAML-alias, and read-only actions/secrets cases. The original destructive Save is now 403 with zero writes. F1 still violates the required same-file startup/status boundary through a hard link; no new grant-widening or base-content overwrite was demonstrated.
- **No secret escapes — FAIL:** the broad response/header/log/report/status sweep still passes, as do short configured values and error redaction, but P proves the remaining F2 configured-key disclosure.
- **Overlay — FAIL complete alias policy, PASS normal semantics:** S reran merge precedence, scalar/list/null replacement, explicit/implicit overlay parity, runtime and CLI loading, base preservation, atomic writes, stale refusal and removal. Linked-base implicit overlay selection is fixed. Hard-link refusal is missing.
- **Local-only surface — PASS guards/admission, FAIL complete file identity boundary:** AC1–AC6 reran; startup rejects non-loopback unless explicitly allowed; session, Host, Origin, POST and CSRF rules pass. F4 now bounds admission before reading bodies. F1 is the remaining file-identity exception.
- **One generated page per module — PASS generation, FAIL complete usability:** all 17 manifests plus the added-directory fixture pass; schema titles/defaults/help, unsupported notices and core limits reran. F3's original case works, but N1 corrupts a valid boolean choice.
- **Check — PASS side effects and diagnostic collection, FAIL exact draft under N1:** real checker bridge and no-write/no-process/no-socket guarantees reran. The original false edit reaches Check; N1 instead passes a redacted string for a chosen true boolean.
- **Apply — PASS supervision/outcomes/drift/bounded child waits, FAIL complete status-path alias refusal:** S reran accepted/refused/unknown, foreign-pid isolation, malformed/unusable records, loaded-document digest, transition publication, drift and stderr draining. F5 is fixed. F1's hard-link identity omission also affects status collision detection.
- **Dependencies/offline resources — PASS static/test evidence:** packaging declarations still add only the UI console entry; aiohttp and PyYAML remain the runtime dependencies. Inline-asset and URL-audit tests passed across generated pages. Browser offline behavior and installed distribution execution remain unverified.
- **Operator documentation — PASS coverage:** launch/local-only, merge/path rules, writable/read-only scope, secrets, drift/status and restart topics all reran. Documentation now explicitly states the bounded 10 s/5 s stop sequence before the 60 s post-start window. Passing documentation tests do not excuse F1/F2/N1.

### Phases 0–3 regression guarantees

All of the following gate-1 running evidence was rerun in S and passed:

| Guarantee | Running evidence |
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

### Cumulative cross-file and assertion review

Reviewed shared overlay reading/merge/digest/collision handling, runtime Check/run/status publication, schema-default validation, generated controls, request guards, protected Save/Remove and atomic write sinks, status classification, supervisor and executor adapter as one branch change. Authorization/runtime module implementations have no phase-4 Python diff. The two approved pre-existing test adaptations remain limited to presentation metadata and the extra console script. The presence test retains its predicate and assertions, with the previously reviewed wall-time floor for thread-backed work.

M was an executed stdin Python comparison using `git diff --name-only 835d9b4..HEAD`, `git show 835d9b4:<module.yaml>`, `yaml.safe_load`, and a recursive removal of only `title`/`default` annotations (preserving property names beneath `properties`), asserting equality for all 17 manifests. The same invocation parsed tests with `ast.parse` to verify all 109 distinct gate-1 named tests still exist. S then supplies their running result, not just collection evidence.

The fix round does not weaken the old short-secret or unbounded-wait assertions to hide those defects: both now require their fixes. It does, however, retain a structural-identifier redaction exception and add `test_f2_declared_identifiers_are_public_text_and_stay_intact` at `tests/test_config_ui.py:5128`, contrary to the requested universal guard when applied to configured occurrences. The new test never exercises the leaking configured channel position. Existing broad green tests therefore do not discharge F2. The gate-1 tautological `... or True` assertion also remains a coverage limitation; it is not treated as proof.

## NEW FINDINGS

### N1 — MEDIUM — short-secret redaction corrupts boolean option values

**Locations:** `core/config_ui/__init__.py:1746`, `:2655`, `:3206`.

**Executed reproduction:** P/`test_gate2_boolean_redaction`. Start from C/F3's shipped `audio_output` settings with the probe absent, add `secrets: ['${GATE_SECRET}']`, and set that unrelated environment secret to the one-character value `r`. GET the audio-output page and extract the true option's actual submitted value. It is:

```html
<option value="t[hidden]ue">t[hidden]ue</option>
```

Submitting exactly that rendered option to Check sends
`{'modules': {'audio_output': {'synthesis': {'probe': 't[hidden]ue'}}}}`
to the injected checker, a **string rather than True**. Submitting it to the real Save route with the proper session, CSRF and current fingerprint returns **422**, and the intercepted replacement sink records zero calls.

The injected Check implementation only captures the draft; its deliberately successful result is not evidence that the real runtime validator accepts this string. Save's rejection and the wrong draft type are executed through production parser/validation code.

The new boolean select runs semantic option tokens through the secret text renderer, while the parser recognizes only literal `true` and `false`. Thus an unrelated legitimate secret makes a healthy choice impossible to save and changes the draft's meaning. Use a stable opaque option-token mapping decoded to the original boolean, keeping displayed text redacted, and test the rendered HTML values through both Check and Save. Do not solve this by restoring a short-secret exemption.

F1 and F2 residual counterexamples are tracked under their original IDs above, not duplicated as new findings.

## Executable evidence

### C — replay of gate 1's published counterexamples

The replay reads the original executable block from gate 1. It removes the old assertions that demanded a vulnerable result so every case can run; it prints the new outcomes instead. It adjusts only the F1 observation after no write, F4's now-required shared admission argument, and the F5 stop-result observation. Inputs and production request/parser/supervisor paths are unchanged. The S tests and P probes provide positive assertions about the fixed and failing behaviors.


```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python - <<'PY'
from pathlib import Path
import ast
s=Path('docs/campaigns/phase4/scaffolding/gate-report-1.md').read_text().split('# GATE_COUNTEREXAMPLES_BEGIN')[1].split('# GATE_COUNTEREXAMPLES_END')[0]
exec(s.split('# F1:')[0])
class Observe(ast.NodeTransformer):
 def visit_Assert(self,node): return ast.Pass()
sections=[('# F1:','# F2a:'),('# F2a:','# F2b:'),('# F2b:','# F3:'),('# F3:','# F4:'),('# F4:','# F5:'),('# F5:',None)]
for start,end in sections:
 code=s[s.index(start):s.index(end) if end else len(s)]
 if start=='# F1:':
  code=code[:code.index('    replaces_base =')]+"    print('C/F1',response.status,u.startup_checks(settings),'replace_calls=',replace.call_count)\n"
 if start=='# F4:':
  code=code.replace('executor))','executor, admission))').replace('        tasks =','        admission = u._Admission()\n        tasks =').replace('await asyncio.gather(*tasks)',"responses = await asyncio.gather(*tasks)\n            print('C/F4 outcomes', {status:sum(r.status==status for r in responses) for status in {r.status for r in responses}})")
 if start=='# F5:':
  code=code.replace('supervisor.stop()', "print('C/F5 stop_result',supervisor.stop(),'retained',supervisor.child is not None)")
 exec(compile(ast.fix_missing_locations(Observe().visit(ast.parse(code))),'<gate1-replay>','exec'))
PY
```

### P — additional named attempts to defeat the fixes

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python - <<'PY'
from pathlib import Path
from unittest.mock import patch
from contextlib import ExitStack
import os,re
import core.config_ui as u
from aiohttp import web
from aiohttp.test_utils import make_mocked_request
s=Path('docs/campaigns/phase4/scaffolding/gate-report-1.md').read_text().split('# GATE_COUNTEREXAMPLES_BEGIN')[1].split('# F1:')[0]
exec(s)
def test_gate2_path_aliases():
 base=Path('config.yaml.example').resolve()
 with patch.dict(os.environ,{'P4_GATE_BASE':str(base)}):
  variants=['~/'+os.path.relpath(base,Path.home()),'./config.yaml.example','../'+Path.cwd().name+'/config.yaml.example',str(base),'$P4_GATE_BASE']
  for v in variants:
   result=u.startup_checks(u.UISettings(base,overlay=Path(v)))
   assert any('overlay_path_collision' in p for p in result),v
  print('test_gate2_path_aliases: tilde,dot,dotdot,relative/absolute,environment refused')
 a,b=Path('/usr/bin/bzip2'),Path('/usr/bin/bzcat')
 assert os.path.samefile(a,b)
 assert not u.same_file(a,b)
 assert u.startup_checks(u.UISettings(a,overlay=b))==[]
 print('test_gate2_path_aliases: real hardlink same inode accepted; status collision=',u.status_path_collision(b,a,None))
 assert any('overlay_path_collision' in p for p in u.startup_checks(u.UISettings(a,overlay=Path('/bin/bzip2'))))
 print('test_gate2_path_aliases: real /bin directory symlink refused')
def test_gate2_boolean_redaction():
 module={'synthesis':{'endpoint':'','model':''},'voices':{'allowed':['v'],'default':'v'},'outputs':{'o':{'player':{'argv':['cat']}}},'default_output':'o'}
 base={'modules_directory':'builtin','enabled_modules':[],'secrets':['${GATE_SECRET}'],'modules':{'audio_output':module}}
 with ExitStack() as stack:
  ui,headers=memory_ui(stack,base,{'GATE_SECRET':'r'})
  page=ui.handle(u.UIRequest('GET','/module/audio_output',{},headers)).body.decode()
  field='modules.audio_output.synthesis.probe'
  select=re.search(r'<select name="'+field+r'".*?</select>',page,re.S).group()
  posted=re.findall(r'<option value="([^"]*)"',select)[1]
  seen=[]
  ui.checker=lambda path,environ,overlay,draft:(seen.append(draft) or True,[])
  ui.handle(u.UIRequest('POST','/check',{},headers,urlencode({field:posted}).encode()))
  replace=stack.enter_context(patch.object(u.os,'replace'))
  result=ui.handle(u.UIRequest('POST','/save',{},headers,urlencode({u.FINGERPRINT_FIELD:u.FINGERPRINT_ABSENT,field:posted}).encode()))
  assert posted=='t[hidden]ue' and seen[-1]['modules']['audio_output']['synthesis']['probe']==posted
  assert result.status==422 and replace.call_count==0
  print('test_gate2_boolean_redaction: browser true token=',posted,'Check draft is string; Save=',result.status,'writes=0')
def test_gate2_secret_positions():
 for secret in ['Ω','q7Z','gate-secret-channel-97bf','rules','twitch','combination']:
  base={'modules_directory':'builtin','enabled_modules':[], 'secrets':['${GATE_SECRET}'],'modules':{'twitch':{'companion_name':secret,'client_secret':secret}},'triggers':{'twitch':{'channels':{secret:{'combination':'all_of','rules':[]}}}}}
  with ExitStack() as stack:
   ui,headers=memory_ui(stack,base,{'GATE_SECRET':secret})
   page=ui.handle(u.UIRequest('GET','/module/twitch',{},headers)).body.decode()
   leak=f'name="triggers.twitch.channels.{secret}.combination"' in page
   assert leak == (secret in ['rules','twitch','combination'])
   assert f'value="{secret}"' not in page
   token=u._RENDERING_FOR.set(ui)
   try: assert secret not in u.esc('label '+secret)
   finally: u._RENDERING_FOR.reset(token)
   ui.checker=lambda *args:(False,['error contains '+secret+' here'])
   error=ui.handle(u.UIRequest('POST','/check',{},headers,b'')).body.decode()
   assert 'error contains '+secret+' here' not in error
   print('test_gate2_secret_positions:',secret,'input/label/error protected; channel attribute leak=',leak)
def test_gate2_body_limit():
 class Payload:
  def __init__(self,n): self.n=n
  def set_read_chunk_size(self,n): pass
  async def readany(self): n=self.n;self.n=0;return b'x'*n
 class Healthy:
  def handle(self,request): return u.UIResponse(200)
 async def scenario(app):
  handler=next(iter(app.router.routes())).handler
  for n,expected in [(0,200),(u.MAX_REQUEST_BYTES,200),(u.MAX_REQUEST_BYTES+1,413),(1,200)]:
   request=make_mocked_request('POST','/',app=app,payload=Payload(n),client_max_size=app._client_max_size)
   try: status=(await handler(request)).status
   except web.HTTPException as e:status=e.status
   assert status==expected,(n,status)
   print('test_gate2_body_limit:',n,status)
 with patch.object(web,'run_app',side_effect=lambda app,**kw:u._run_socket_free(scenario(app))):
  u.serve(Healthy(),u.UISettings(Path('/nonexistent-gate/config.yaml')))
for test in [test_gate2_path_aliases,test_gate2_secret_positions,test_gate2_boolean_redaction,test_gate2_body_limit]:test()
PY
```

## NOT VERIFIED

- No real browser session or JavaScript execution, live HTTP listener/load test, or full browser navigation/unsaved-edit preservation test. N1 submits the exact value extracted from generated HTML through real handlers, without a browser.
- S retained 18 environment-dependent skips: five capture process-group cases, six phase-2 trials, six phase-3 trials, and distribution installation. No installed console-script smoke test, live providers/platform credentials, audio hardware/capture devices or moderation endpoints.
- F1's original dangerous write was intercepted; no base was destroyed. The hard-link probe uses existing system hard links solely for the real path/inode check and startup refusal function; no hard-linked YAML fixtures were created in this gate. S supplies normal YAML write/base-preservation coverage.
- F5 used bounded-wait test doubles plus the suite's ordinary child-process tests; no operating-system process stuck indefinitely after SIGKILL was induced. No hard total 60 s wall-clock bound on Check, filesystem access, process creation and the full Apply transaction is claimed.
- Body bytes and admission counts were exercised; a wall-clock body-read timeout, slow-client connection retention, hostile filesystem races, and all external-writer interleavings were not verified.
- The first body-adapter probe had an incomplete payload double (`set_read_chunk_size` missing) and stopped with AttributeError. P supplies that installed-aiohttp interface and reruns the real adapter successfully; the failed harness run is not counted as an implementation defect.
- No absolute absence of other defects is claimed. The executed residual F2 leak and N1 regression, together with F1's incomplete identity boundary, require another fix round before phase 4 can close.
