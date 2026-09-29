# Phase 4 — full-branch closing gate, third pass

## VERDICT

REQUEST_CHANGES

Reviewed `main` at `f8b2c5c`, cumulatively against `835d9b4..HEAD`, the same implementation baseline as gates 1 and 2, against the phase-4 spec and binding brief decision 6c. **F1, N1 and F3 are resolved. F2 remains partially resolved: JSON escaping defeats secret redaction. A new positional-name regression makes Check validate an edit against the wrong channel after the base's key order changes.** Phase 4 cannot close yet.

**S — executed full suite:**

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/ -q -p no:cacheprovider
```

**2948 passed, 18 skipped in 122.41 s; exit 0.** No cache or bytecode was requested. Normal pytest temporary fixtures ran. Additional probes ran from stdin, used in-memory configuration reads/intercepted write sinks, and created no files. The initial execution sandbox failed before running commands (`bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted`); the approved execution fallback succeeded.

Final `git status --short` confirms this report is the only addition beyond the pre-existing untracked run directories; no tracked changes or staged changes. Embedded D reproductions were rerun from the report successfully, and all embedded Python blocks parse. Only this report was written in the repository. No source/test/configuration edit, staging or commit; the pre-existing untracked `docs/runs/` directories were left alone.

## OPEN ITEMS

### F1 — HIGH — RESOLVED

**Decision and consumers:** `core/overlay.py:249` (`same_file`), `core/overlay.py:295` (`status_path_collision`); startup `core/config_ui/__init__.py:262`; Save preflight `core/config_ui/__init__.py:3038` and final sink `core/config_ui/__init__.py:3915`; runtime status refusal `core/main.py:517` and `core/main.py:697`. All collision decisions reach the shared identity comparison. The managed destination additionally must remain the canonical path selected at startup.

**Fallback:** canonicalize expansions, relative components and symlinks; existing paths use `os.path.samefile` (device/inode). When identity is unavailable, compare the deepest existing ancestors by identity and the missing suffix components by text, case-folding only when the ancestor probe reports case-insensitivity. Identical canonical paths immediately compare equal. This policy is documented in the function; errors/hostile races are not exhaustively proven safe by this review.

**Executed tests (S):** `tests/test_overlay.py::test_a_hard_link_is_the_same_file_whatever_its_name`, `test_a_missing_file_falls_back_to_the_canonical_path`, `test_collision_through_a_symlink`, `test_collision_through_a_symlinked_parent_of_a_missing_file`, `test_a_different_name_does_not_collide`; `tests/test_config_ui.py::test_f1_an_overlay_naming_the_base_by_any_spelling_refuses_to_start`, `test_f1_save_never_replaces_the_base_whatever_the_overlay_spelling`, `test_f1_a_hard_link_of_the_base_refuses_to_start_and_is_never_written`, `test_the_runtime_is_launched_on_the_managed_overlay_of_a_linked_base`; `tests/test_status_record.py::test_a_hard_linked_status_file_is_refused_through_main`.

**Replays and defeat attempts (C, P, D):**

- Gate-1 literal `~/gate-base.yaml`: startup collision, zero replacement calls. The unchanged old form first returns **409** because it lacks the newly required layout token. Replaying with the actual rendered layout reaches the collision guard and returns **403**, still zero writes.
- Gate-2 real hard links `/usr/bin/bzip2` and `/usr/bin/bzcat`: both `os.path.samefile` and `u.same_file` are **True**; startup now refuses, status collision is `base`, runtime `_status_collision` refuses. A constructed UI bypassing startup returns **403** on authenticated Save with a valid layout; zero replacements. S additionally uses actual hard-linked YAML fixtures and verifies byte/mtime preservation and refusal before serving.
- Tilde, dot, dot-dot, relative/absolute, environment expansion, real `/bin` directory symlink: refused when they name the base. File-symlink and linked-base implicit-overlay cases pass in S.
- Dangling/nonexistent pair `/nonexistent-gate/a/../b` versus `/nonexistent-gate/b`: equal; distinct `B` versus `b` on this filesystem: unequal.
- Bind-mount-like alias: preserve `/usr/bin` and `/bin` as distinct canonical names in a test double, retaining their real common directory identity; equal missing suffixes collide, distinct suffixes do not. This exercises the ancestor fallback without creating a mount.
- Case variation: actual case-sensitive names remain distinct; simulated case-insensitive ancestor plus case-varied missing suffixes compare equal. No real case-insensitive mount was used.
- Legitimate distinct absent overlay beside `config.yaml.example`: accepted. S also verifies an independent identical-content copy is not the same file. No over-refusal reproduced.

### F2 — HIGH — PARTIALLY RESOLVED

**Fixed path:** `core/config_ui/__init__.py:1192`–`:1322` classifies configured positions and generates positional names; `:2588` uses positional channel hooks; `:2465`/`:2473` use positional rule rows. Parser mapping is at `:2752`. **Residual:** `:1101`, `:1626`, `:1647`, `:1918`: redaction occurs after JSON serialization and does not recognize JSON-escaped secrets.

**Executed tests (S):** `tests/test_config_ui.py::test_f2_a_short_secret_in_an_ordinary_setting_is_never_rendered`, `test_f2_a_credential_used_as_a_trigger_channel_key_is_never_rendered`, `test_f2_a_configured_channel_key_never_reaches_a_generated_identifier`, `test_f2_a_non_secret_channel_key_is_shown_as_text_and_named_by_position`, `test_f2_a_rule_id_is_shown_and_never_an_identifier`, plus AC35 and diagnostics/log redaction tests. These pass; they do not cover the escaped JSON counterexample below.

**Replays and defeat attempts:**

- C/F2a `q7Z` ordinary input: HTTP 200, no value disclosure. C/F2b `gate-secret-channel-97bf` credential/channel: HTTP 200, no configured-key attribute disclosure.
- P repeats `Ω`, `q7Z`, the long credential, `rules`, `twitch`, `combination`: configured channel attribute leaks are **False in all six cases**. Ordinary inputs, labels and injected diagnostics are redacted. The former `triggers.twitch.channels.twitch.combination` is now `triggers.twitch.channels.@0.combination`.
- Channel IDs and rule aliases, including quote/backslash/newline secrets: legends/cells are redacted and hooks are positional (`@0`, rule row `0`). S proves fresh positional Save/Remove round-trips and rejects the former raw-key names; second-channel edits preserve the other channels. Non-secret keys remain readable as operator-facing text, as intended by the existing UI contract; they are not copied into generated identifiers.
- **List key defeat succeeds:** put the collected secret `a"b` at `modules.brain.delivery.actions[0].arguments` as a mapping key. This list satisfies its shipped schema. GET `/module/brain` returns HTTP 200 and its JSON textarea still decodes to that exact key. Backslash and newline secrets also survive; `q7Z` and `Ω` controls are hidden. D/positions executes these five cases and verifies the list schema with `validate_against_schema`.
- **List value defeat succeeds:** `modules.audio_output.voices.allowed: ['a"b']` emits `[&quot;a\&quot;b&quot;]`; `json.loads(html.unescape(textarea))[0]` recovers the secret exactly. Backslash/newline variants also reproduce. This is encoded configured data, not a coincidental structural token. D/JSON also tried object keys in this string-list field (invalid item shape); the brain-list reproduction above removes that limitation.

The general R8/F2 guarantee still fails. `_masked` only masks declared credential paths; a collected secret reused as ordinary nested data/key is serialized by `json.dumps` before `esc` sees it. `_redaction_forms` includes raw and HTML forms, not JSON forms. Redact configured scalar values **and keys before serialization**, keeping structural tokens intact; verify decoded JSON as well as raw HTML and preserve untouched parser behavior. The JSON residual is tracked here under F2, not counted as another duplicate finding. It was found this pass; no claim that P19F4 first introduced it.

### N1 — MEDIUM — RESOLVED

**Code:** `core/config_ui/__init__.py:1611` (structural registration), `:1826` (enum tokens), `:1855` (boolean options), `:2604` (combination registration), `:3345` (coercion).

**Executed tests (S):** `tests/test_config_ui.py::test_n1_a_short_secret_inside_true_or_false_never_rewrites_the_boolean_options` (all 12 secrets), `test_n1_a_configured_secret_is_still_never_rendered_beside_the_boolean_tokens`, `test_n1_a_short_secret_never_rewrites_declared_enum_options`.

**Gate-2 P/boolean replay:** unrelated secret `r`; extract the actual rendered true option, submit it through Check and Save. The token is **`true`**, Check receives **boolean True**, Save reaches its commit boundary with **boolean True** and returns **303**. The probe intercepts the commit to avoid writes; S additionally writes/reads actual YAML and invokes the runtime settings parser. The old `t[hidden]ue` string/422 result is gone.

**Defeat attempts:** secrets `r,u,t,e,a,l,s,f,k,-,ue,als` leave the structural choices intact; false and true traverse rendering → submission → Check → Save → YAML → runtime; enum tokens also remain intact. Configured non-boolean secrets `q7Z`/`maybe` still render hidden in the kept-value option and endpoint input. P covers one-character `Ω` and configured keys equal to declared words. D expands to JSON encoding and finds the **independent F2 residual above**: therefore the required universal no-disclosure re-confirmation fails even though the N1 boolean corruption itself is fixed. Fixing the JSON residual must not restore redaction of structural tokens.

### F3 — MEDIUM — RESOLVED

**Code:** `core/config_ui/__init__.py:1855`, `:2807`, `:3365`; runtime `modules/audio_output/__init__.py:500`/`:510`, `_Settings.from_mapping` (unchanged).

**Executed tests (S):** `tests/test_config_ui.py::test_f3_an_unset_true_default_boolean_renders_its_effective_value`, `test_f3_a_boolean_draft_expresses_unset_false_and_true` (all three cases), `test_f3_a_non_boolean_configured_value_posts_untouched`, `test_f3_a_configured_boolean_spelling_string_can_be_corrected`; N1 end-to-end parameterizations above.

**C/F3 replay:** absent probe still means runtime True. Posted `false` now parses to `[(('modules','audio_output','synthesis','probe'), False)]`; the checker sees that explicit False in the draft. The replay's legacy `unchecked=True` observation searches for an obsolete checkbox and is not evidence against the current select.

**Three states and defeat attempts:** unset selects `not set (default: true)` and submits no override; explicit false and true persist as YAML booleans and reach runtime False/True respectively. Redrawn controls select the stored state. Removing an override restores inheritance (AC25); configured invalid/string `true`/`false` values have a distinct keep token and can be corrected to real booleans. Short-secret interaction is now covered by N1. No remaining tri-state defect reproduced.

## RE-CONFIRMED

- **F4 — RESOLVED:** S (`test_f4_a_saturated_bridge_refuses_honestly_and_queues_nothing_more`, `test_f4_a_failed_body_read_releases_its_admission`) + C/F4 + P/body: 128 requests, 4 workers, 12 queued, 16 admitted, 112 return 503; bodies of 0/1,048,576/1,048,577/1 bytes yield 200/200/413/200. Code `core/config_ui/__init__.py:4346`, `:4416`, `:4445`.
- **F5 — RESOLVED:** S (`test_stop_waits_for_the_grace_before_killing`, `test_f5_an_unkillable_child_is_reported_and_kept_not_waited_on_forever`, `test_f5_apply_with_an_unkillable_child_is_refused_and_starts_nothing`, `test_f5_close_logs_an_unkillable_child`) + C/F5: terminate → wait(10.0) → kill → wait(5.0); stop False, child retained, no replacement child. Code `core/config_ui/__init__.py:4140`. The 60 s acceptance window starts after spawn; it is not a total transaction deadline.

### Gate-1 requirement map — every named test rerun under S

The map retains **all gate-1 named running tests**, not a selected subset. M verified that all 109 distinct gate-1 test names still exist; S ran them. Green tests do not override an executed counterexample.

| Requirement | Result | Named running tests and command |
|---|---|---|
| R1 — separate UI, bind/token/Host/Origin/CSRF guards | **PASS** | **S**: `tests/test_config_ui.py::test_the_module_entry_point_calls_main`; `tests/test_config_ui.py::test_ac1_a_non_loopback_host_is_refused_before_any_bind`; `tests/test_config_ui.py::test_ac2_each_start_has_a_fresh_256_bit_token`; `tests/test_config_ui.py::test_ac3_every_route_without_a_session_is_refused_without_content`; `tests/test_config_ui.py::test_ac4_a_post_without_or_with_a_wrong_csrf_token_is_refused`; `tests/test_config_ui.py::test_ac4_a_non_post_to_a_state_changing_path_is_405`; `tests/test_config_ui.py::test_ac6_refused_hosts_get_403_even_on_get`; `tests/test_config_ui.py::test_ac6_refused_origins_get_403_and_change_nothing`; `tests/test_config_ui.py::test_ac5_the_socket_patch_is_effective`. Startup/request guards and bounded admission hold. |
| R2 — shared overlay, precedence, base preservation | **PASS** | **S**: `tests/test_overlay.py::test_ac7_merged_document_is_exact_and_inputs_are_unmutated`; `tests/test_overlay.py::test_ac7_overlay_null_replaces_the_base_value`; `tests/test_overlay.py::test_ac9_overlay_below_minimum_is_refused_then_deleting_it_accepts`; `tests/test_overlay.py::test_ac9_run_hands_the_module_the_overlay_value`; `tests/test_overlay.py::test_ac9_explicit_overlay_is_honoured_by_main_and_run`; `tests/test_overlay.py::test_overlay_is_merged_before_environment_resolution`; `tests/test_overlay.py::test_relative_modules_directory_resolves_from_the_base_directory`; `tests/test_overlay.py::test_ac10_bad_overlay_names_the_file_never_the_content`. Shared merge, runtime parity, base preservation and identity boundary hold. |
| R3 — all titles/defaults and annotation validation | **PASS** | **S**: `tests/test_manifest_presentation.py::test_presented_covers_every_shipped_manifest`; `tests/test_manifest_presentation.py::test_every_setting_node_has_a_title`; `tests/test_manifest_presentation.py::test_every_documented_default_is_declared`; `tests/test_manifest_presentation.py::test_every_declared_default_validates_against_its_node`; `tests/test_manifest_presentation.py::test_default_violating_the_node_is_refused_as_default`; `tests/test_manifest_presentation.py::test_default_does_not_constrain_validated_values`; `tests/test_manifest_presentation.py::test_property_named_default_is_a_property_not_an_annotation`. Titles/defaults and annotation validation hold. |
| R4 — generated base/core/module pages and honest controls | **PASS** | **S**: `tests/test_config_ui.py::test_ac15_the_base_page_lists_every_module_and_the_origin`; `tests/test_config_ui.py::test_ac16_every_schema_path_is_shown_with_title_help_and_default`; `tests/test_config_ui.py::test_ac17_a_module_added_as_a_directory_gets_its_titled_page`; `tests/test_config_ui.py::test_ac17_no_ui_source_file_names_a_shipped_module`; `tests/test_config_ui.py::test_ac18_unrenderable_nodes_get_notices_and_controls_keep_the_schema`; `tests/test_config_ui.py::test_ac19_the_twitch_page_shows_the_configured_channel_policy`; `tests/test_config_ui.py::test_ac20_the_core_page_renders_every_declared_limit`; `tests/test_config_ui.py::test_ac20_secrets_and_actions_are_read_only_with_every_rule`; `tests/test_config_ui.py::test_ac20_an_uncovered_action_is_listed_until_a_rule_covers_it`; `tests/test_config_ui.py::test_ac21_readiness_combines_the_check_verdict_and_the_running_state`. Generated pages and boolean controls hold; disclosure remains tracked under R8. |
| R5 — unsaved Check through existing path, all diagnostics, no side effects | **FAIL** | **S**: `tests/test_config_ui.py::test_ac38_an_unsaved_invalid_edit_fails_and_writes_nothing`; `tests/test_config_ui.py::test_ac38_an_unsaved_fix_passes_and_writes_nothing`; `tests/test_config_ui.py::test_ac22_unresolved_references_are_all_reported_and_stop_the_check`; `tests/test_config_ui.py::test_ac22_every_invalid_setting_is_reported_in_one_check`; `tests/test_config_ui.py::test_ac23_check_signals_nothing_starts_nothing_and_creates_no_socket`; `tests/test_config_ui.py::test_bridge_check_runs_the_real_checker_from_a_running_loop`. Fresh drafts and side-effect bounds pass; N2 retargets a stale positional Check. |
| R6 — exact writing scope, managed path, atomicity, stale checks, removal | **PASS** | **S**: `tests/test_config_ui.py::test_ac24_each_save_writes_exactly_its_override_and_logs_paths_only`; `tests/test_config_ui.py::test_ac25_remove_override_restores_the_base_value_and_prunes`; `tests/test_config_ui.py::test_ac26_an_invalid_save_returns_a_field_diagnostic_and_writes_nothing`; `tests/test_config_ui.py::test_ac26_a_stale_save_or_remove_is_refused`; `tests/test_config_ui.py::test_ac27_writes_outside_the_scope_or_at_protected_fields_are_refused`; `tests/test_config_ui.py::test_ac27_posted_protected_or_out_of_scope_fields_are_refused`; `tests/test_config_ui.py::test_ac27_hand_written_actions_and_secrets_survive_an_unrelated_save`; `tests/test_config_ui.py::test_ac27_a_save_or_remove_through_a_yaml_alias_leaves_the_other_path_unchanged`; `tests/test_config_ui.py::test_ac27_an_ancestor_save_must_keep_every_protected_descendant`; `tests/test_config_ui.py::test_ac28_an_interrupted_write_leaves_the_previous_overlay_intact`; `tests/test_config_ui.py::test_ac28_the_write_targets_only_the_managed_overlay`. Scope, managed identity, atomicity, stale Save/Remove and removal hold. |
| R7 — supervised Apply, status publication/classification, drift, bounded result | **PASS** | **S**: `tests/test_config_ui.py::test_ac29_a_failing_on_disk_check_refuses_and_touches_no_process`; `tests/test_config_ui.py::test_ac30_a_ready_record_of_the_child_with_the_on_disk_digest_is_accepted`; `tests/test_config_ui.py::test_ac30_a_child_exiting_2_is_refused_with_its_status_and_diagnostic`; `tests/test_config_ui.py::test_ac30_a_child_writing_nothing_within_the_window_is_unknown`; `tests/test_config_ui.py::test_ac31_apply_never_signals_a_foreign_record_pid`; `tests/test_config_ui.py::test_ac32_drift_follows_the_overlay_and_the_record`; `tests/test_config_ui.py::test_ac39_ac40_a_child_writing_only_an_unaccepted_record_is_unknown`; `tests/test_config_ui.py::test_ac40_every_listed_mutation_of_v_is_unusable_with_a_value_free_reason`; `tests/test_status_record.py::test_three_transitions_publish_three_records_in_order`; `tests/test_status_record.py::test_the_record_describes_the_loaded_configuration`; `tests/test_status_record.py::test_a_status_path_naming_a_configuration_file_is_refused`. Status identity, publication, classification, drift and bounded child waits hold. |
| R8 — no secret in responses, diagnostics, logs or reports | **FAIL** | **S**: `tests/test_config_ui.py::test_ac35_no_secret_value_reaches_any_response_log_report_or_record`; `tests/test_config_ui.py::test_check_diagnostics_pass_the_redaction_guard`; `tests/test_config_ui.py::test_d9_the_refused_tail_is_redacted_and_value_free`; `tests/test_config_ui.py::test_ac24_a_saved_path_naming_a_secret_is_logged_redacted`. F2 JSON-escaped configured keys/values remain recoverable. |
| R9 — dependencies and offline resources | **PASS** | **S**: `tests/test_main.py::test_pyproject_declares_the_console_script_and_the_build_backend_for_tests`; `tests/test_config_ui.py::test_pages_use_inline_assets_and_relative_targets_only`; `tests/test_config_ui.py::test_r9_module_pages_use_inline_assets_and_relative_targets_only`; `tests/test_config_ui.py::test_ac36_the_url_audit_finds_every_external_target`; `tests/test_config_ui.py::test_ac36_a_configured_url_is_escaped_text_only`. Dependency declarations and static offline-resource checks hold. |
| R10 — operator documentation | **PASS** | **S**: `tests/test_config_ui.py::test_ac37_doc_launch_and_local_only`; `tests/test_config_ui.py::test_ac37_doc_overlay_rule_and_merge`; `tests/test_config_ui.py::test_ac37_doc_writable_and_read_only_blocks`; `tests/test_config_ui.py::test_ac37_doc_secrets_policy`; `tests/test_config_ui.py::test_ac37_doc_restart_drift_and_status_file`. Operator-documentation coverage holds. |

### Operator-visible guarantees (gate-1 checklist)

- **Writing scope / 6c — PASS:** S AC24/AC27/AC28 and F1, C + D/identity: five writable blocks; actions/secrets/credentials/references and protected ancestors preserved; no base replacement.
- **No secret escapes — FAIL:** S AC35 and P ordinary positions pass; D/JSON and D/positions recover escaped secrets (F2).
- **Overlay — PASS:** S `tests/test_overlay.py` and AC24–AC28, C/F1 + D/identity: precedence, list/scalar/null replacement, explicit/implicit parity, base preservation, atomic writes, stale Save and removal.
- **Local-only surface — PASS within tested surface:** S AC1–AC6/AC41, C/F4 + P/body: default loopback refusal, session/Host/Origin/POST/CSRF, identity boundaries and bounded admission.
- **One generated page per module — PASS:** S AC15–AC20 plus F3/N1: all 17 manifests and added-directory fixture; titles/defaults/help, unsupported notices, boolean usability. Static inventory: no module HTML.
- **Check — FAIL exact draft after layout changes:** S AC22/AC23/AC38 and real checker bridge pass; D/stale proves wrong target under N2. No writes/process/socket side effects were reproduced.
- **Apply — PASS tested guarantees:** S AC29–AC34/AC39–AC43 + F5, C/F5: accepted/refused/unknown, foreign-pid isolation, disk drift, loaded-document digest, status transitions and bounded stop waits.
- **Dependencies/offline — PASS static evidence:** S AC36 and packaging tests; `git diff 835d9b4..HEAD -- pyproject.toml`: only the additional UI entry point, same aiohttp/PyYAML dependencies; no browser offline claim.
- **Operator documentation — PASS coverage:** S AC37; read `docs/config-ui.md`: launch/local-only, merge/overlay, scope, secrets, restart/drift/status and bounded stop accounting. The documented no-disclosure/exact-draft guarantees still require F2/N2 fixes.

### Phases 0–3 regression guarantees

All following named tests ran and passed under **S**. Runtime module Python implementations are unchanged in the cumulative branch diff.

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

### Cumulative review and assertion audit

Commands: `git diff --stat 835d9b4..HEAD`, `git diff --check 835d9b4..HEAD`, `git diff 835d9b4..HEAD -- core/contracts.py core/main.py tests/conftest.py tests/test_main.py tests/test_presence_pack.py tests/test_users.py pyproject.toml`, source reads of the shared overlay and the complete UI's major paths (configuration/redaction/rendering, guards, Check/Save/Remove, status, supervisor and adapter), and manifest comparison M (`PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -`). **66 changed files; whitespace check passes.** This review treats the final implementation as one branch change.

M loaded all 17 manifests from `git show 835d9b4:<path>` and disk, removed only title/default annotations while preserving keys under `properties`, and asserted equality. No operational declarations changed. Shared merge/digest paths and publisher loaded-document semantics remain consistent; authorization/module implementation code has no phase-4 Python diff.

Pre-existing test changes preserve their substantive assertions: metadata allowlist, second console entry, and the documented thread-backed presence wait's wall-time floor. The old tautological `... or True` at `tests/test_config_ui.py:1325` remains and is **not evidence**. No new weakened assertion was found that explains the failures above. The fresh positional and boolean tests exercise useful round-trips, but omit JSON-decoded canaries and stale Check. The comments claiming redaction precedes serialization at `core/config_ui/__init__.py:1073` overstate what `_json_text` actually does.

## NEW FINDINGS

### N2 — MEDIUM — Check silently retargets positional fields after a base-key reorder

**Locations:** `core/config_ui/__init__.py:1280` (positional name), `:1403` (layout digest), `:2904`/`:2922` (Check parses without checking layout), `:3114`/`:3121` (Save does check it), `:3554` (shared stale-layout decision).

**Executed D/stale:** render Twitch channels in order `first`, `second`, both `all_of`. The first channel's field is `triggers.twitch.channels.@0.combination`. Retain the rendered layout token; reorder the in-memory base mapping to `second`, `first` (equivalent to an external base rewrite, no content edit). Submit the old field with `any_of` and the old layout.

`POST /check` returns **200**, calls the checker with:

```python
{'triggers': {'twitch': {'channels': {
    'second': {'combination': 'any_of', 'rules': []}
}}}}
```

The operator edited **first**. The identical submission to `/save` returns **409 stale**. The reproduction captures the real parser/draft at the injected checker's input; it does not claim that empty-rule policies passed a real runtime validator. This is sufficient to prove the wrong target before validation. There is no write in the reproduction.

This regression follows from replacing raw-key names with positions while adding the layout guard only to Save/Remove. Check can report readiness/diagnostics for a different draft. Reject stale layout **before parsing Check**, or preserve a server-side snapshot mapping for the posted form; test reorders in both base and overlay through Check and Save.

**F2's newly demonstrated HIGH JSON residual is detailed under OPEN ITEMS rather than duplicated here.** No other new finding is claimed without an executed counterexample.

## Executable evidence

All commands below run from the repository root with `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -` and the displayed Python supplied on stdin. They create no files.

### C — gate-1 replay

Executed the first shell block in gate 2's “Executable evidence” verbatim: it extracts gate 1's published Python, drops the old assertions demanding vulnerable outcomes, adapts the shared admission argument, and prints outcomes. To reproduce:

```python
from pathlib import Path
s = Path('docs/campaigns/phase4/scaffolding/gate-report-2.md').read_text()
exec(s.split("```sh\nPYTHONDONTWRITEBYTECODE=1 .venv/bin/python - <<'PY'\n")[1].split('\nPY\n```')[0])
```

Observed: F1 409/zero writes (403 with current layout in D), F2a/F2b no leak, F3 explicit False reaches Check, F4 12 queued/112 refused, F5 bounded 10/5 waits and retained child.

### P — gate-2 replay

Executed gate 2's second shell block with these mechanical expectation/API updates (no production changes): hard-link assertions now demand True/collision; all configured-key `leak` assertions demand False; boolean assertion demands literal `true` and `is True`; Save includes the actual layout from `_view_layout` in rendering context, and `_commit` is intercepted with `WriteResult(OUTCOME_SAVED)` while its draft is asserted to contain True. The body-limit probe is unchanged. All four named probes completed: `test_gate2_path_aliases`, `test_gate2_secret_positions`, `test_gate2_boolean_redaction`, `test_gate2_body_limit`. Their outcomes are recorded above. S covers actual filesystem persistence beyond the intercepted Save.

### D — independently executed residual and regression probes

Common setup, reused from the original published harness:

```python
from pathlib import Path
from contextlib import ExitStack
from unittest.mock import patch
import re, html, json, os
from core.contracts import validate_against_schema
exec(Path('docs/campaigns/phase4/scaffolding/gate-report-1.md').read_text()
     .split('# GATE_COUNTEREXAMPLES_BEGIN')[1].split('# F1:')[0])
```

D/positions (schema-valid configured list keys, plus channel/rule aliases):

```python
for secret in ['q7Z', 'a"b', 'a\\b', 'a\nb', 'Ω']:
    base = {
        'modules_directory': 'builtin', 'enabled_modules': [],
        'secrets': ['${GATE_SECRET}'],
        'actions': [{'rule_id': secret, 'action_name': 'x.do'}],
        'modules': {'brain': {'delivery': {'actions': [
            {'action': 'x.do', 'arguments': {secret: 'x'}}]}}},
        'triggers': {'twitch': {'channels': {
            secret: {'combination': 'all_of', 'rules': []}}}}}
    with ExitStack() as stack:
        ui, headers = memory_ui(stack, base, {'GATE_SECRET': secret})
        page = ui.handle(u.UIRequest('GET', '/module/brain', {}, headers)).body.decode()
        schema = ui.view().modules['brain'].manifest['settings_schema']['properties']['delivery']['properties']['actions']
        validate_against_schema(base['modules']['brain']['delivery']['actions'], schema, label='delivery.actions')
        area = re.search('<textarea name="modules.brain.delivery.actions"[^>]*>(.*?)</textarea>', page, re.S).group(1)
        leaked = secret in json.loads(html.unescape(area))[0]['arguments']
        assert leaked == any(c in secret for c in ['"', '\\', '\n'])
        core = ui.handle(u.UIRequest('GET', '/core', {}, headers)).body.decode()
        assert re.findall('<tr data-rule="([^"]+)"', core) == ['0']
        assert '<td><code>' + html.escape(secret) + '</code></td>' not in core
        twitch = ui.handle(u.UIRequest('GET', '/module/twitch', {}, headers)).body.decode()
        assert 'name="triggers.twitch.channels.@0.combination"' in twitch
        assert '<legend>Channel <code>' + html.escape(secret) + '</code></legend>' not in twitch
        print(repr(secret), 'list key recoverable=', leaked)
```

D/JSON uses the same five secrets in `modules.audio_output.voices.allowed`, both as a scalar item and as an object's key, extracts that field's textarea, and compares `json.loads(html.unescape(area))` with the original secret. Quote/backslash/newline variants are recoverable; plain/Unicode controls are hidden. Object items there are invalid against the string schema; D/positions establishes the same key failure with a valid schema.

D/stale:

```python
base = {'modules_directory': 'builtin', 'enabled_modules': [],
        'triggers': {'twitch': {'channels': {
            'first': {'combination': 'all_of', 'rules': []},
            'second': {'combination': 'all_of', 'rules': []}}}}}
with ExitStack() as stack:
    ui, headers = memory_ui(stack, base)
    page = ui.handle(u.UIRequest('GET', '/module/twitch', {}, headers)).body.decode()
    layout = re.search('name="layout" value="([^"]+)"', page).group(1)
    field = re.search('<select name="([^"]+\.combination)"', page).group(1)
    channels = base['triggers']['twitch']['channels']
    base['triggers']['twitch']['channels'] = {k: channels[k] for k in ['second', 'first']}
    seen = []
    ui.checker = lambda p, e, o, d: (seen.append(d) or True, [])
    form = urlencode({'layout': layout, 'fingerprint': 'absent', field: 'any_of'}).encode()
    checked = ui.handle(u.UIRequest('POST', '/check', {}, headers, form))
    saved = ui.handle(u.UIRequest('POST', '/save', {}, headers, form))
    assert seen[0]['triggers']['twitch']['channels']['second']['combination'] == 'any_of'
    assert checked.status == 200 and saved.status == 409
    print(checked.status, seen[0], saved.status)
```

D/identity (in addition to real hard-link/startup/runtime/Save probes described above):

```python
import core.overlay as o
base = Path('config.yaml.example').resolve()
assert u.startup_checks(u.UISettings(base, overlay=base.with_name('gate-distinct-absent.yaml'))) == []
assert not u.same_file(base, base.with_name(base.name.upper()))
assert u.same_file('/nonexistent-gate/a/../b', '/nonexistent-gate/b')
assert not u.same_file('/nonexistent-gate/B', '/nonexistent-gate/b')
# Preserve distinct alias spellings, like a bind mount, with real ancestor identity.
with patch.object(o, 'canonical_path', side_effect=lambda p: Path(p)):
    assert o.same_file('/usr/bin/gate-absent-file', '/bin/gate-absent-file')
    assert not o.same_file('/usr/bin/gate-absent-file', '/bin/gate-other-file')
    with patch.object(o, '_case_insensitive', return_value=True):
        assert o.same_file('/usr/bin/gate-absent-file', '/bin/GATE-ABSENT-FILE')
```

## NOT VERIFIED

- No real browser/JavaScript session, live listener/load test, or complete navigation/unsaved-edit preservation exercise. Exact option values were extracted and submitted through real handlers. N2 is a server-side reproduction, independent of browser behavior.
- The 18 environment-dependent skips remain: five capture process-group cases, six phase-2 trials, six phase-3 trials, and distribution installation. No installed-console-script smoke test, live provider/platform calls, audio hardware or moderation endpoint operation.
- No actual bind mount or case-insensitive filesystem was created. Bind-like ancestor identity and case-fold fallback were simulated as described; real hard links/symlinks and ordinary case-sensitive paths were exercised. No newly created dangling symlink fixture beyond S's missing-path/symlink-parent coverage.
- No base file was destroyed. Extra Save probes intercepted writes; S supplies actual temporary-file persistence/atomicity tests. No exhaustive hostile filesystem race, permission-error, external-writer or per-directory filesystem case-policy proof.
- No OS process stuck after SIGKILL was induced. Bounded-wait doubles plus S's ordinary child-process tests ran; no total 60 s bound on Check/filesystem/spawn/full Apply is claimed. Slow-client body-read deadlines were not verified.
- New JSON probes establish UI disclosure of reused collected secrets, including schema-valid nested keys; they do not claim the illustrative brain action is a runnable provider configuration. The stale Check uses an injected checker to capture the exact incorrect draft; it does not claim runtime acceptance of the fixture's empty rule list.
- No absolute absence of other defects is claimed. The concrete F2 residual and N2 reproduction are sufficient for REQUEST_CHANGES despite the green suite.
