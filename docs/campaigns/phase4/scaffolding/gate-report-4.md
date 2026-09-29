# Phase 4 — full-branch closing gate, fourth pass

## VERDICT

REQUEST_CHANGES

Reviewed `main` at `10ef6c2a0c6e60eda3750efb559c838240e54a22`, as the cumulative implementation diff **`835d9b4..HEAD`**, against the phase-4 spec, plan and binding brief decision 6c. **F2's disclosure residual and N2's stale-layout retargeting are resolved in the executed replays. Phase 4 still cannot close: N3 makes Check validate a different draft from Save, and N4 corrupts JSON editor syntax when a collected secret overlaps serialization punctuation.** Both have executed reproductions below; green existing tests do not cover them.

### Commands and scope

**S — full suite, run by this reviewer:**

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/ -q -p no:cacheprovider
```

**2971 passed, 18 skipped in 136.83 s (0:02:16), exit 0.** The requested command is unchanged apart from suppressing bytecode writes. Ordinary pytest temporary fixtures ran. No source, test or configuration file was edited, staged or committed. This report is the only deliberately created repository file. The pre-existing modified `docs/campaigns/phase4/scaffolding/campaign-summary.md` and untracked `docs/runs/` directories were left alone. The initial sandbox wrapper failed before execution (`bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted`); the approved execution fallback ran the commands.

**C, P, D, M** below are reviewer probes executed with **`PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -`**, supplying Python on stdin, without adding test files. C/P replay the published gate-1/gate-2 harnesses. D independently exercises handlers, rendered HTML, decoded JSON, parser calls and checker/commit inputs. Configuration reads and additional Save sinks are injected in memory; S provides real temporary-file persistence coverage. M checks the cumulative manifest/test inventory. Reproduction blocks below share the published `memory_ui` harness.

## OPEN ITEMS

### F2 — HIGH — RESOLVED for secret disclosure; follow-on defects N3/N4 remain

Locations: `core/config_ui/__init__.py:1129` (scalar comparison), `:1137` (distinct hidden keys), `:1163` (recursive value/key masking), `:1917` and `:1950` (mask before JSON serialization), `:1106` (raw/HTML/JSON diagnostic spellings). The recursive mask now follows values and keys, including mappings inside lists, rather than depending on a credential path. Save restoration is at `:3995`; its absence from Check is N3.

Executed tests under **S**: `tests/test_config_ui.py::test_f2_a_secret_used_as_a_list_key_or_value_is_never_rendered`, `test_f2_two_secret_keys_in_one_mapping_get_distinct_stand_ins`, `test_f2_an_edited_json_field_puts_withheld_values_and_keys_back`, `test_f2_a_json_spelled_secret_is_redacted_from_errors_and_diagnostics`, and all `test_p19f5_*` restoration tests. Earlier short-secret, declared-credential, configured-channel, rule-id, response/log/report and diagnostic tests also ran.

| Replay / defeat — command D unless indicated | Executed outcome |
|---|---|
| Gate 1 short `q7Z` ordinary setting and long declared credential used as a channel key — C | Both actual GET responses 200; neither old disclosure survives. |
| Gate 2 configured keys named `rules`, `twitch`, `combination`, plus Unicode and arbitrary long/short strings — P | No configured key embedded in field names; labels, inputs and Check errors redact the canary. |
| Gate 3 `a"b` as `delivery.actions[0].arguments` mapping key | Schema validation of the fixture passes; `json.loads(html.unescape(textarea))` no longer contains that key. |
| Gate 3 `voices.allowed: ['a"b']` | Decoded list element is the hidden-literal marker, not the secret. |
| Gate 3 additional `q7Z`, backslash, newline and Unicode variants | All five schema-valid action-list key replays pass; action rule identifiers remain numeric and channel controls remain positional. |
| Secret as ordinary nested value and in nested lists of mappings | Tested `arguments = {secret: 'x', 'plain': secret, 'nested': [[{secret: [secret]}]]}`. Every scalar/key occurrence is withheld after HTML unescape and JSON parse. |
| Same secret as list element and as a key elsewhere | Brain key and audio allowed-list element tested together against the same collected secret; neither recovers it. |
| Entity-bearing strings `a&amp;b`, `a&#34;b`; quote/backslash/newline-bearing strings `a"b`, `a\\b`, `a\nb` | All decoded key/value checks pass. HTML entity spelling does not restore the original configured secret. |
| Secret is a prefix or substring of a legitimate value | Exact occurrences withheld; `x<secret>y` and `<secret>-legitimate` become `x[hidden]y` / `[hidden]-legitimate`. `kept` remains intact. Save restoration preserves longer original strings; **Check does not (N3)**. This substring masking is consistent with the existing no-secret-text policy; it is not separately reported as a leak. |
| Legitimate escaped string distinct from the secret | Secret `a"b`, legitimate value containing a literal backslash before the quote: the decoded legitimate value survives and Save reaches the commit sink, 303. |
| Single double-quote character `"`, single backslash, standalone newline | No disclosure established. Backslash/newline masking of exact scalar occurrences works; **quote/escape punctuation can make otherwise legitimate JSON unparseable (N4)**. This is not counted as a successful usable editor round-trip. |

F2's original exploit is not reproducible. This is not an assertion that every rendered form remains usable: N3/N4 are independently blocking.

### N2 — MEDIUM — RESOLVED

Locations: `core/config_ui/__init__.py:3207` (`_check_endpoint`, guard before `parse_edits`), `:3419` (Save uses the same guard), `:3862` (`_stale_layout`), `:1678`/`:1688` (ordered key/list skeleton and keyed layout digest).

Executed **S**: `tests/test_config_ui.py::test_ac24_a_key_reorder_makes_a_rendered_check_stale_like_save`, both **base** and **overlay** parameterizations. The test checks identical response bodies and zero checker calls; D additionally spies on the parser itself.

| Replay / defeat — command D | Executed outcome |
|---|---|
| Published gate-3 two-channel base reorder | Check and Save both 409, identical bodies; zero parser calls and zero checker calls. The old `@0` never retargets `second`. |
| Add a channel key | Same 409/body equality/zero-parser result. |
| Remove a channel key | Same 409/body equality/zero-parser result. |
| Rename one key without changing the count | Same 409/body equality/zero-parser result. |
| Matching layout | Check 200; exactly one parse/check, draft edits `first`, the rendered channel. |
| Only a scalar value changes, key layout stays identical | Check 200 and correct target; guard does not reject a still-valid positional layout. |
| Another tab submits Save adding `third`, then the old page submits Check | Real Save route returns 303, with only its commit sink intercepted into an in-memory overlay. Old Check returns 409 before parsing. |
| Missing layout | S asserts 409 and no checker invocation. |

A value-only overlay change can leave the layout valid for Check while making an old Save fingerprint stale; the guard promises layout identity, not rejection of every value-only change.

## RE-CONFIRMED

- **F1 — RESOLVED:** **S + C/F1 + P/path + D/identity**; `core/overlay.py:249`, UI `:3336`/`:4216`. Original tilde Save with no layout returns 409; with current layout it returns 403, zero replacements. Existing real hard links `/usr/bin/bzip2` and `/usr/bin/bzcat` collide, startup refuses, status collision is `base`, authenticated Save returns 403. Tilde/dot/dot-dot/environment/symlink variants refuse; distinct missing/case-sensitive names remain distinct; bind-like ancestor/case-fold fallback probes pass. S includes `test_f1_a_hard_link_of_the_base_refuses_to_start_and_is_never_written`, `test_f1_save_never_replaces_the_base_whatever_the_overlay_spelling`, shared-overlay identity and runtime-status collision tests.
- **N1 — original boolean/enum defect RESOLVED:** **S + P/boolean**; UI `:2158`. With secret `r`, rendered option remains literal `true`, Check receives Python `True`, Save 303 passes `True` to commit. `test_n1_a_short_secret_inside_true_or_false_never_rewrites_the_boolean_options` and `test_n1_a_short_secret_never_rewrites_declared_enum_options` pass. The broader promise to preserve structural tokens has a newly demonstrated JSON gap, **N4**.
- **F3 — original tri-state defect RESOLVED:** **S + C/F3**; `test_f3_an_unset_true_default_boolean_renders_its_effective_value`, `test_f3_a_boolean_draft_expresses_unset_false_and_true`, string-correction tests pass. Published false draft now reaches Check as `False`, while unset runtime default remains true. N3 is a separate exact-draft regression for masked JSON.
- **F4 — RESOLVED:** **S + C/F4 + P/body**; UI `:4671`/`:4733`/`:4762`. Saturation: 128 requests, four workers, 12 queued, 16 admitted, 112 refused with 503. Body lengths 0/1,048,576/1,048,577/1 yield 200/200/413/200. `test_f4_a_saturated_bridge_refuses_honestly_and_queues_nothing_more` and `test_f4_a_failed_body_read_releases_its_admission` pass.
- **F5 — RESOLVED:** **S + C/F5**; UI `:4457`. Terminate → wait(10.0) → kill → wait(5.0); stop returns False, retains child. `test_f5_an_unkillable_child_is_reported_and_kept_not_waited_on_forever`, `test_f5_apply_with_an_unkillable_child_is_refused_and_starts_nothing`, `test_f5_close_logs_an_unkillable_child` pass. No total full-transaction 60-second bound is claimed.

### Gate-1 R1–R10 map

**M verified all 109 distinct named gate-1 tests still exist; S reran the complete suite.** The map below retains every gate-1 requirement-map test name. FAIL records the executed counterexample despite green existing coverage.

| Requirement | Result | Named running tests and command |
|---|---|---|
| R1 — separate UI, bind/token/Host/Origin/CSRF guards | **PASS** | **S**: `tests/test_config_ui.py::test_the_module_entry_point_calls_main`; `tests/test_config_ui.py::test_ac1_a_non_loopback_host_is_refused_before_any_bind`; `tests/test_config_ui.py::test_ac2_each_start_has_a_fresh_256_bit_token`; `tests/test_config_ui.py::test_ac3_every_route_without_a_session_is_refused_without_content`; `tests/test_config_ui.py::test_ac4_a_post_without_or_with_a_wrong_csrf_token_is_refused`; `tests/test_config_ui.py::test_ac4_a_non_post_to_a_state_changing_path_is_405`; `tests/test_config_ui.py::test_ac6_refused_hosts_get_403_even_on_get`; `tests/test_config_ui.py::test_ac6_refused_origins_get_403_and_change_nothing`; `tests/test_config_ui.py::test_ac5_the_socket_patch_is_effective`. Startup/request guards and bounded admission pass. |
| R2 — shared overlay, precedence, base preservation | **PASS** | **S**: `tests/test_overlay.py::test_ac7_merged_document_is_exact_and_inputs_are_unmutated`; `tests/test_overlay.py::test_ac7_overlay_null_replaces_the_base_value`; `tests/test_overlay.py::test_ac9_overlay_below_minimum_is_refused_then_deleting_it_accepts`; `tests/test_overlay.py::test_ac9_run_hands_the_module_the_overlay_value`; `tests/test_overlay.py::test_ac9_explicit_overlay_is_honoured_by_main_and_run`; `tests/test_overlay.py::test_overlay_is_merged_before_environment_resolution`; `tests/test_overlay.py::test_relative_modules_directory_resolves_from_the_base_directory`; `tests/test_overlay.py::test_ac10_bad_overlay_names_the_file_never_the_content`. Shared merge, runtime parity, base preservation and identity boundary pass. |
| R3 — all titles/defaults and annotation validation | **PASS** | **S**: `tests/test_manifest_presentation.py::test_presented_covers_every_shipped_manifest`; `tests/test_manifest_presentation.py::test_every_setting_node_has_a_title`; `tests/test_manifest_presentation.py::test_every_documented_default_is_declared`; `tests/test_manifest_presentation.py::test_every_declared_default_validates_against_its_node`; `tests/test_manifest_presentation.py::test_default_violating_the_node_is_refused_as_default`; `tests/test_manifest_presentation.py::test_default_does_not_constrain_validated_values`; `tests/test_manifest_presentation.py::test_property_named_default_is_a_property_not_an_annotation`. Titles/defaults and annotation validation pass. |
| R4 — generated base/core/module pages and honest controls | **FAIL** | **S**: `tests/test_config_ui.py::test_ac15_the_base_page_lists_every_module_and_the_origin`; `tests/test_config_ui.py::test_ac16_every_schema_path_is_shown_with_title_help_and_default`; `tests/test_config_ui.py::test_ac17_a_module_added_as_a_directory_gets_its_titled_page`; `tests/test_config_ui.py::test_ac17_no_ui_source_file_names_a_shipped_module`; `tests/test_config_ui.py::test_ac18_unrenderable_nodes_get_notices_and_controls_keep_the_schema`; `tests/test_config_ui.py::test_ac19_the_twitch_page_shows_the_configured_channel_policy`; `tests/test_config_ui.py::test_ac20_the_core_page_renders_every_declared_limit`; `tests/test_config_ui.py::test_ac20_secrets_and_actions_are_read_only_with_every_rule`; `tests/test_config_ui.py::test_ac20_an_uncovered_action_is_listed_until_a_rule_covers_it`; `tests/test_config_ui.py::test_ac21_readiness_combines_the_check_verdict_and_the_running_state`. Generation and boolean controls pass; N4 breaks JSON editors for legitimate lists. |
| R5 — unsaved Check through existing path, all diagnostics, no side effects | **FAIL** | **S**: `tests/test_config_ui.py::test_ac38_an_unsaved_invalid_edit_fails_and_writes_nothing`; `tests/test_config_ui.py::test_ac38_an_unsaved_fix_passes_and_writes_nothing`; `tests/test_config_ui.py::test_ac22_unresolved_references_are_all_reported_and_stop_the_check`; `tests/test_config_ui.py::test_ac22_every_invalid_setting_is_reported_in_one_check`; `tests/test_config_ui.py::test_ac23_check_signals_nothing_starts_nothing_and_creates_no_socket`; `tests/test_config_ui.py::test_bridge_check_runs_the_real_checker_from_a_running_loop`. N2 is fixed; N3 makes Check validate placeholders instead of the values Save restores. |
| R6 — exact writing scope, managed path, atomicity, stale checks, removal | **FAIL** | **S**: `tests/test_config_ui.py::test_ac24_each_save_writes_exactly_its_override_and_logs_paths_only`; `tests/test_config_ui.py::test_ac25_remove_override_restores_the_base_value_and_prunes`; `tests/test_config_ui.py::test_ac26_an_invalid_save_returns_a_field_diagnostic_and_writes_nothing`; `tests/test_config_ui.py::test_ac26_a_stale_save_or_remove_is_refused`; `tests/test_config_ui.py::test_ac27_writes_outside_the_scope_or_at_protected_fields_are_refused`; `tests/test_config_ui.py::test_ac27_posted_protected_or_out_of_scope_fields_are_refused`; `tests/test_config_ui.py::test_ac27_hand_written_actions_and_secrets_survive_an_unrelated_save`; `tests/test_config_ui.py::test_ac27_a_save_or_remove_through_a_yaml_alias_leaves_the_other_path_unchanged`; `tests/test_config_ui.py::test_ac27_an_ancestor_save_must_keep_every_protected_descendant`; `tests/test_config_ui.py::test_ac28_an_interrupted_write_leaves_the_previous_overlay_intact`; `tests/test_config_ui.py::test_ac28_the_write_targets_only_the_managed_overlay`. Scope, identity, preservation, atomicity and stale refusal pass; N4 prevents a valid list edit through the generated editor. |
| R7 — supervised Apply, status publication/classification, drift, bounded result | **PASS** | **S**: `tests/test_config_ui.py::test_ac29_a_failing_on_disk_check_refuses_and_touches_no_process`; `tests/test_config_ui.py::test_ac30_a_ready_record_of_the_child_with_the_on_disk_digest_is_accepted`; `tests/test_config_ui.py::test_ac30_a_child_exiting_2_is_refused_with_its_status_and_diagnostic`; `tests/test_config_ui.py::test_ac30_a_child_writing_nothing_within_the_window_is_unknown`; `tests/test_config_ui.py::test_ac31_apply_never_signals_a_foreign_record_pid`; `tests/test_config_ui.py::test_ac32_drift_follows_the_overlay_and_the_record`; `tests/test_config_ui.py::test_ac39_ac40_a_child_writing_only_an_unaccepted_record_is_unknown`; `tests/test_config_ui.py::test_ac40_every_listed_mutation_of_v_is_unusable_with_a_value_free_reason`; `tests/test_status_record.py::test_three_transitions_publish_three_records_in_order`; `tests/test_status_record.py::test_the_record_describes_the_loaded_configuration`; `tests/test_status_record.py::test_a_status_path_naming_a_configuration_file_is_refused`. Status publication, classification, drift and bounded child waits pass. |
| R8 — no secret in responses, diagnostics, logs or reports | **PASS** | **S**: `tests/test_config_ui.py::test_ac35_no_secret_value_reaches_any_response_log_report_or_record`; `tests/test_config_ui.py::test_check_diagnostics_pass_the_redaction_guard`; `tests/test_config_ui.py::test_d9_the_refused_tail_is_redacted_and_value_free`; `tests/test_config_ui.py::test_ac24_a_saved_path_naming_a_secret_is_logged_redacted`. F2 disclosure replays pass, including decoded JSON keys and values. N4 is a usability failure, not a demonstrated disclosure. |
| R9 — dependencies and offline resources | **PASS** | **S**: `tests/test_main.py::test_pyproject_declares_the_console_script_and_the_build_backend_for_tests`; `tests/test_config_ui.py::test_pages_use_inline_assets_and_relative_targets_only`; `tests/test_config_ui.py::test_r9_module_pages_use_inline_assets_and_relative_targets_only`; `tests/test_config_ui.py::test_ac36_the_url_audit_finds_every_external_target`; `tests/test_config_ui.py::test_ac36_a_configured_url_is_escaped_text_only`. Dependency declarations and static offline-resource checks pass. |
| R10 — operator documentation | **PASS** | **S**: `tests/test_config_ui.py::test_ac37_doc_launch_and_local_only`; `tests/test_config_ui.py::test_ac37_doc_overlay_rule_and_merge`; `tests/test_config_ui.py::test_ac37_doc_writable_and_read_only_blocks`; `tests/test_config_ui.py::test_ac37_doc_secrets_policy`; `tests/test_config_ui.py::test_ac37_doc_restart_drift_and_status_file`. Required operator-documentation subjects are covered; implementation gaps are N3/N4. |

### Gate-1 operator-visible guarantees

- **Writing scope / 6c — boundary PASS, usability incomplete:** **S** AC24/AC27/AC28 + **C/P/D F1** preserve the base, actions/secrets, credentials and references; N4 prevents an otherwise allowed list edit.
- **No secret escapes — tested disclosure cases PASS:** **S** AC35 + **C/P/D F2**, including decoded JSON keys/values, diagnostics and log/report sweeps; not an exhaustive proof.
- **Overlay — PASS:** **S** overlay/AC24–AC28 + **P/D identity** cover shared merge, precedence, explicit/implicit paths, base preservation, stale refusal, removal and atomic replacement.
- **Local-only surface — PASS tested guards:** **S** AC1–AC6/AC27/AC41 + **C/P admission/body**; token, Host/Origin, POST/CSRF, startup/path refusal, bounded admission. No live browser/listener claim.
- **One generated page per module — generation PASS, usability FAIL:** **S** AC15–AC20 + **M**: 17 manifests, zero module HTML, added-directory fixture works; **D/N4** breaks a generated JSON editor.
- **Check — FAIL exact draft:** **S** AC22/AC23/AC38 and real-checker bridge + **D/N2** pass; **D/N3** makes the real checker reject a draft whose Save-normalized form passes. No writes/signals/sockets in Check were reproduced.
- **Apply — PASS tested guarantees:** **S** AC29–AC34/AC39–AC43 + **C/F5** cover outcomes, foreign-pid isolation, drift, loaded-document digest, state transitions and bounded stop waits.
- **Dependencies/offline — PASS static evidence:** **S** AC36/packaging + **M** and `git diff 835d9b4..HEAD -- pyproject.toml`; only new console entry, unchanged dependencies, inline assets/relative targets.
- **Documentation — PASS required subject coverage:** **S** AC37 + source read of `docs/config-ui.md`; launch/local-only, overlay/merge, writing scope, secrets, restart/drift/status. Its exact-draft promise still fails under N3.

### Phases 0–3 regression guarantees

All following named tests passed under **S**. **M** also establishes that runtime module implementations and manifest operational declarations are unchanged in the cumulative diff.

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

Executed `git diff --stat 835d9b4..HEAD`, `git diff --check 835d9b4..HEAD`, and the cumulative diffs of contracts, runtime integration, packaging and pre-existing tests; read the final overlay, UI model/redaction/identifier/rendering, request guards, Check/Save/Remove, status reader, Apply/supervisor/adapter paths, spec, brief, plan and operator documentation. **70 changed files; whitespace check passes.** No per-commit verdict was substituted for whole-branch review.

**M** loaded all 17 manifests from `git show 835d9b4:<path>` and disk, removed only schema title/default annotations (preserving properties literally named `default`) and asserted equality of all remaining declarations. It also confirmed no module Python implementation diff and zero module HTML. The runtime/UI share merge and digest helpers; publication digests the loaded unresolved document. Existing-test adaptations preserve assertions: metadata allowlist, second console entry, documented thread-backed presence wait floor. The old `... or True` assertion in `tests/test_config_ui.py` is not counted as useful evidence. New restoration tests exercise Save but miss Check/Save equivalence; existing punctuation canaries do not cover a secret consisting solely of quote/escape syntax.

## NEW FINDINGS

### N3 — MEDIUM — Check validates masking placeholders while Save restores the real values

**Locations:** `core/config_ui/__init__.py:3179` (Check directly applies parsed edits), `:3282` (Save calls protection/restoration), `:3995` / `:4010` (`_protect_save` invokes `_unmask_secrets`). **Executed D/N3**, including the actual `_default_checker` → `core.main.check_config` bridge and real audio-output settings hook; only configuration reads and the Save commit sink were injected.

A valid enabled audio-output configuration has `voices.allowed = ['kept', 'a"b']`, `voices.default = '${GATE_SECRET}'`, and `GATE_SECRET = 'a"b'`. Its on-disk Check passes. Render the page, decode the allowed-voices textarea, append `'added'`, and submit with its current layout:

- The page represents the second item as `literal value configured (hidden)`.
- **Check returns 200 with a failed verdict:** `module 'audio_output': field 'voices.default': must be an allowed voice`.
- **Save of the identical submission returns 303** and passes `['kept', 'a"b', 'added']` to its commit sink.
- The **real checker passes that exact Save draft**: `(True, [])`.

A separate captured-input probe also confirms longer legitimate values diverge: Check receives `prefix-[hidden]-suffix`, Save restores `prefix-a"b-suffix`. The operator only appended a voice; Check therefore reports a false error for a different configuration. This is a regression of R5's exact-draft guarantee introduced by value-mask restoration being exclusive to Save, independent of N2's now-correct layout guard.

Required correction: share mask restoration/draft normalization between Check and Save before validation, including nested keys, reordered lists and ambiguous/unmatched marker refusal. Do not expose the restored values in responses. Add a real-checker equivalence regression for the counterexample.

### N4 — MEDIUM — redaction still rewrites JSON punctuation, making legitimate list controls uneditable

**Locations:** `core/config_ui/__init__.py:1394`–`:1398` (substring replacement), `:1433`–`:1444` (`esc` redacts already serialized text), `:1950` (JSON serialization), `:2222` (`esc(self._json_text(path))`). **Executed D/N4**, actual GET and authenticated POST handlers with current layout, parser and Save refusal; zero commit calls.

Collect the one-character secret `"`. Configure the entirely legitimate list `voices.allowed = ['kept', 'voice-two']`, containing no secret value. The GET returns 200, but its HTML-unescaped textarea is:

```text
[[hidden]kept[hidden], [hidden]voice-two[hidden]]
```

`json.loads` raises: JSON's delimiters were treated as secret data. Appending `, "added"` before the final `]` and submitting yields **Save 403**, with `a hidden value cannot be matched to exactly one configured entry; edit it in the configuration file`; no commit occurs. The parser hands Check a string instead of the intended list.

An independent escape variant also reproduces it: collected secret is a single backslash, configured list is `['kept', 'ordinary"quote']`. Its textarea becomes:

```text
["kept", "ordinary[hidden]"quote"]
```

Again invalid JSON, Save 403, no commit. The backslash was introduced by JSON serialization, not present in that legitimate configured string. Thus the display damages data that does not equal or contain the secret. N1's original boolean/enum fix still passes; its structural-token principle was not extended to JSON. This is a newly demonstrated cumulative-branch gap; no claim that every part of N4 first appeared in the latest two commits.

Required correction: redact/mask actual scalar/key content before serialization, then preserve JSON syntax and HTML-escape it without redacting delimiters/escapes again. Test decoded valid JSON and editing round-trips with quote/backslash-only secrets and unrelated legitimate values.

## Executable reproductions

Run the following blocks in one stdin program with **`PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -`** from the repository root. They create no files. Common setup:

```python
from pathlib import Path
from contextlib import ExitStack
from unittest.mock import patch
import re, html, json, copy
import core.main as main
exec(Path('docs/campaigns/phase4/scaffolding/gate-report-1.md').read_text()
     .split('# GATE_COUNTEREXAMPLES_BEGIN')[1].split('# F1:')[0])

def page_layout(page):
    return re.search('name="layout" value="([^"]+)"', page).group(1)

def textarea(page, name):
    return html.unescape(re.search(
        '<textarea name="' + re.escape(name) + '"[^>]*>(.*?)</textarea>',
        page, re.S).group(1))
```

**D/N3 — actual checker, false rejection versus accepted Save draft:**

```python
secret = 'a"b'
module = {
    'synthesis': {'endpoint': '', 'model': ''},
    'voices': {'allowed': ['kept', secret], 'default': '${GATE_SECRET}'},
    'outputs': {'o': {'player': {'argv': ['cat']}}}, 'default_output': 'o'}
base = {'modules_directory': 'builtin', 'enabled_modules': ['audio_output'],
        'secrets': ['${GATE_SECRET}'], 'modules': {'audio_output': module}}
with ExitStack() as stack:
    ui, headers = memory_ui(stack, base, {'GATE_SECRET': secret})
    stack.enter_context(patch.object(main, 'read_base', return_value=base))
    assert ui.check().passed
    page = ui.handle(u.UIRequest('GET', '/module/audio_output', {}, headers)).body.decode()
    name = 'modules.audio_output.voices.allowed'
    shown = json.loads(textarea(page, name))
    shown.append('added')
    form = urlencode({'layout': page_layout(page), 'fingerprint': 'absent',
                      'page': '/module/audio_output', name: json.dumps(shown)}).encode()
    checked = ui.handle(u.UIRequest('POST', '/check', {}, headers, form))
    verdict = ui.last_check
    with patch.object(ui, '_commit', return_value=u.WriteResult(u.OUTCOME_SAVED)) as commit:
        saved = ui.handle(u.UIRequest('POST', '/save', {}, headers, form))
        draft = commit.call_args.args[0]
        actual = u._default_checker(ui.base_path, ui.environ, ui.overlay_path, draft)
    assert checked.status == 200 and not verdict.passed
    assert saved.status == 303 and actual == (True, [])
    assert draft['modules']['audio_output']['voices']['allowed'] == ['kept', secret, 'added']
    print('D/N3', verdict, 'Save', saved.status, 'saved draft Check', actual)
```

**D/N4 — legitimate values, corrupted JSON syntax, failed round-trip:**

```python
for secret, allowed in [('"', ['kept', 'voice-two']), ('\\', ['kept', 'ordinary"quote'])]:
    base = {'modules_directory': 'builtin', 'enabled_modules': [],
            'secrets': ['${GATE_SECRET}'],
            'modules': {'audio_output': {'voices': {'allowed': allowed}}}}
    with ExitStack() as stack:
        ui, headers = memory_ui(stack, base, {'GATE_SECRET': secret})
        page = ui.handle(u.UIRequest('GET', '/module/audio_output', {}, headers)).body.decode()
        name = 'modules.audio_output.voices.allowed'
        shown = textarea(page, name)
        try:
            json.loads(shown)
        except json.JSONDecodeError:
            pass
        else:
            raise AssertionError('expected the reproduced invalid JSON')
        form = urlencode({'layout': page_layout(page), 'fingerprint': 'absent',
                          'page': '/module/audio_output',
                          name: shown[:-1] + ', "added"]'}).encode()
        with patch.object(ui, '_commit') as commit:
            saved = ui.handle(u.UIRequest('POST', '/save', {}, headers, form))
        assert saved.status == 403 and not commit.called
        print('D/N4', repr(secret), repr(shown), 'Save', saved.status)
```

**D/F2 — decoded nested data and textarea defeats:**

```python
def scalars(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from scalars(item)
    elif isinstance(value, list):
        for item in value:
            yield from scalars(item)
    else:
        yield value

for secret in ['q7Z', 'a"b', 'a\\b', 'a\nb', 'Ω', 'a&amp;b', 'a&#34;b']:
    base = {'modules_directory': 'builtin', 'enabled_modules': [],
            'secrets': ['${GATE_SECRET}'], 'modules': {
                'brain': {'delivery': {'actions': [{'action': 'x.do', 'arguments': {
                    secret: 'x', 'plain': secret, 'nested': [[{secret: [secret]}]]}}]}},
                'audio_output': {'voices': {'allowed': [secret, 'kept', 'x'+secret+'y',
                                                       secret+'-legitimate']}}}}
    with ExitStack() as stack:
        ui, headers = memory_ui(stack, base, {'GATE_SECRET': secret})
        for module, field in [('brain', 'delivery.actions'), ('audio_output', 'voices.allowed')]:
            page = ui.handle(u.UIRequest('GET', '/module/'+module, {}, headers)).body.decode()
            decoded = json.loads(textarea(page, 'modules.'+module+'.'+field))
            assert secret not in list(scalars(decoded))
        print('D/F2 decoded', repr(secret), 'withheld')
```

**D/N2 — reorder/add/remove/rename, parser spy and valid controls:**

```python
policy = {'combination': 'all_of', 'rules': []}
for mutation in ['reorder', 'add', 'remove', 'rename', 'same', 'value-only']:
    base = {'modules_directory': 'builtin', 'enabled_modules': [], 'triggers': {'twitch': {
        'channels': {'first': copy.deepcopy(policy), 'second': copy.deepcopy(policy)}}}}
    with ExitStack() as stack:
        ui, headers = memory_ui(stack, base)
        page = ui.handle(u.UIRequest('GET', '/module/twitch', {}, headers)).body.decode()
        field = re.search('<select name="([^"]+\\.combination)"', page).group(1)
        channels = base['triggers']['twitch']['channels']
        if mutation == 'reorder':
            base['triggers']['twitch']['channels'] = dict(reversed(list(channels.items())))
        elif mutation == 'add':
            channels['third'] = copy.deepcopy(policy)
        elif mutation == 'remove':
            del channels['first']
        elif mutation == 'rename':
            base['triggers']['twitch']['channels'] = {
                'renamed': channels['first'], 'second': channels['second']}
        elif mutation == 'value-only':
            channels['second']['combination'] = 'any_of'
        seen = []
        ui.checker = lambda p, e, o, d: (seen.append(d) or True, [])
        form = urlencode({'page': '/module/twitch', 'layout': page_layout(page),
                          'fingerprint': 'absent', field: 'any_of'}).encode()
        with patch.object(ui, 'parse_edits', wraps=ui.parse_edits) as parsed:
            checked = ui.handle(u.UIRequest('POST', '/check', {}, headers, form))
            count = parsed.call_count
        if mutation not in ['same', 'value-only']:
            saved = ui.handle(u.UIRequest('POST', '/save', {}, headers, form))
            assert checked.status == saved.status == 409 and checked.body == saved.body
            assert count == 0 and not seen
        else:
            assert checked.status == 200 and count == 1
            assert seen[0]['triggers']['twitch']['channels']['first']['combination'] == 'any_of'
        print('D/N2', mutation, checked.status, 'parser calls', count)
```

### Published-harness replay accounting

C ran gate 2's first executable shell block (which extracts gate 1). The old F3 form initially stopped the harness because it lacked the now-required layout and no checker call occurred. Rerun supplied the layout extracted from an actual GET to that form; all C cases then completed with the results above. Vulnerability-demanding old assertions were removed by the published AST adapter, not treated as regression expectations.

P ran gate 2's second block with current expectations: real hard links must collide; channel-name leak must be False; boolean option must be `true` and both checker/commit draft must contain `True`; Check/Save forms receive a rendered layout. Save commit is intercepted; body-size/admission adapter probe unchanged. All four probes completed. D also replayed gate 3's schema-valid key/JSON, rule-id/channel-position and file-identity probes; current-layout tilde/hard-link Save attempts both returned 403, no replacement.

An initial M inventory regex omitted `async def`; rerun with async support verified all 109 names. The first real-checker fixture used a listed secret referenced by no setting and correctly failed runtime configuration validation; the published N3 reproduction fixes the fixture by using `${GATE_SECRET}` as `voices.default`, and asserts that its baseline passes. An initial N4 probe expected 422; actual protected-marker refusal is 403, confirmed by the final assertions above. These were harness corrections, not production edits or hidden suite failures.

Final verification: all five embedded Python blocks were parsed and executed from this report successfully. Final `git status --short` shows only this report added beyond the pre-existing modified campaign summary and untracked run directories; `git diff --cached --stat` is empty.

## NOT VERIFIED

- No real browser/JavaScript execution, live listener/load test or full navigation/unsaved-edit preservation exercise. Exact rendered option/textarea values were submitted through the real handlers.
- S retains 18 environment-dependent skips: five capture process-group cases, six phase-2 trials, six phase-3 trials, and distribution installation. No installed-console smoke test, live providers/platform endpoints, audio hardware or capture devices.
- Extra Save probes intercept commit/write sinks; no real base was overwritten. S covers actual temporary-file persistence, preservation and atomicity. No exhaustive filesystem race, permission-error or external-writer-interleaving proof.
- No actual bind mount or case-insensitive filesystem was created; bind-like ancestor/case-fold branches were simulated, while existing real hard links and symlink aliases were exercised.
- No OS process stuck after SIGKILL was induced. Bounded-wait doubles and S's ordinary process tests ran; no total 60-second bound on Check/filesystem/spawn/full Apply, or slow-client body-read deadline, was verified.
- N2's small channel fixtures use an injected checker to observe targeting; no runtime acceptance of their empty rule lists is claimed. N3 separately uses the actual runtime checker with a baseline-valid enabled module.
- No proof of the absolute absence of further defects. F2/N2 pass their published defeats, but the two executed MEDIUM findings keep the closing gate open.
