# Phase 4 — full-branch closing gate, eighth pass

## VERDICT

REQUEST_CHANGES

Reviewed main at **b30ab8a220eb251086d2e5a9f1d247055e69fa96**, cumulatively as **835d9b4..HEAD**, against phase-4 spec.md, plan.md, brief.md decision 6c, gates 1–7 and P19F10. This is a whole-branch judgment.

The published deterministic identity retargeting is fixed, and **N7 is RESOLVED**. **N3/N6 remain PARTIALLY RESOLVED**: genuine identities from another UI process or from an evicted render are refused without checking or committing, but Check returns 200/failed and Save 403 instead of the required identical 409/stale-page outcome. A separate LOW omission, N8, was reproduced in the branch's ignore rules. Phase 4 cannot close against the stated requirements yet.

## Commands and execution

Only this report was deliberately created or modified in the repository. Nothing staged or committed; existing untracked docs/runs directories were left alone. Normal pytest fixtures used temporary files. Independent probes intercepted configuration reads and commits in memory, without configuration-file writes, sockets or external services. Bytecode and pytest cache were disabled.

- **S:** PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/ -q -p no:cacheprovider → **3046 passed, 18 skipped in 158.35 s; exit 0**. The requested invocation is unchanged apart from preventing bytecode writes; the P19F10 floor is met.
- **Q:** PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/test_config_ui.py tests/test_overlay.py tests/test_status_record.py -q -p no:cacheprovider -k 'test_f1 or test_f2 or test_f3 or test_f4 or test_f5 or test_n1 or stale_layout or test_n4 or p19f9 or test_n3 or test_n5 or p19f5 or p19f10 or key_reorder' → **144 passed, 367 deselected in 67.42 s; exit 0**.
- **E:** PYTHONDONTWRITEBYTECODE=1 .venv/bin/python - with inspected stdin: actual authenticated GET/Check/Save/Remove forms, retained/current guards, separate sessions and independently keyed ConfigUI instances, mixed identities, production render-history allocation, exact checker/commit observations. N7 scalar probes used the actual enabled audio-output checker through core.main.check_config.
- **P:** the same Python stdin command: independent positive-assertion replays of the historical F1–F5 and N1–N6 fixtures, using the current generated controls where the old whole-masked editor was superseded.
- **D:** the same Python stdin command: malformed/duplicated/recombined identities, legacy masked payloads, remove-plus-edit, foreign pages, same-cookie tab simulation and installed aiohttp body reading.
- **M:** the same Python stdin command, AST inventory and subprocess execution of PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/ --collect-only -q -p no:cacheprovider. **3064 tests collected; all 109 qualified gate-1 test references exist and collect**. All AC1–AC43 have named running evidence (AC11 is S; AC12–AC14 use annotation tests).
- **G:** git diff --stat 835d9b4..HEAD; git diff --name-status 835d9b4..HEAD; cumulative source/test/manifest/packaging/scaffolding diffs and final-source reads; AST caller inventory; digest, dispatch, write-sink and subprocess searches. **82 changed files**. git diff --check 835d9b4..HEAD reports the existing new blank line at EOF in gate-report-7.md:292; that historical report was left untouched.
- **I:** git check-ignore --no-index -v -- config.local.yaml config.yaml.status.json presence.local.yaml modules/brain/operator.local.yaml → **exit 1, empty stdout, none ignored**. No representative file was created.

M also compared all 17 manifests with git show 835d9b4:<path>, stripping only schema title/default annotations while preserving properties literally named default: all operational declarations/constraints match. No module Python implementation changed; no module HTML or root local overlay exists.

The initial sandbox wrapper failed before execution (bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted); the approved execution fallback completed the work. No automatic approval rejection remains. One preliminary Q run lost its result handle, so the separately captured complete Q result above is the evidence. Probe corrections stayed in stdin: refresh guards after legitimate layout-changing saves, use receiver guards for borrowed tokens, and retain the secret reference needed for a baseline-valid enabled-module list-removal fixture.

## OPEN ITEMS

### N6 — MEDIUM — PARTIALLY RESOLVED; uniform refusal remains OPEN

**Locations:** core/config_ui/__init__.py:2116 (typed actual top path/render/page/relative path/value identity), :2762 (session render), **:2803** (stale classification conditional on remembered tag/page), :2771 (4096-tag eviction), :3538 (unknown identity fallback), :3699 and :3901 (Check/Save guards).

**Executed tests:** E, P/N6, D, S/Q. Named running evidence in tests/test_config_ui.py: test_p19f10_gate7_an_old_identity_after_remove_and_save_is_refused_never_retargeted; test_p19f10_an_identity_of_an_older_render_of_the_same_page_is_refused; test_p19f10_an_identity_from_another_session_is_refused (both cases); test_p19f9_gate6_n6_a_visible_value_edit_never_changes_another_entrys_key; test_p19f9_gate6_n6_reversing_and_appending_keeps_each_key_with_its_entry.

| Replay / defeat | Executed outcome |
|---|---|
| Gate-7 exact Remove first → Save second with identical keyword policy → GET current guards → submit old kept-entry identity | Remove/add 303. Old/current tokens differ. Check/Save both 409 with identical stale/reload bodies; zero checker/commit. Current token then edits second exactly. E and named S/Q regression. |
| Older render of same page and unchanged data | Tokens differ; Check/Save identical 409, no checker/commit. Current form saves correctly. E, S/Q. |
| Another authenticated session, with and without its own page render | Identical 409 refusals, no checker/commit. Drawing session still saves. E and both named S/Q parameterizations. |
| Another tab using same session cookie | Second GET retires first identities; first form against current guards gives identical 409, nothing checked/committed. D, simulated requests. |
| Another UI process: entire foreign form including its foreign layout | Receiver's keyed layout guard refuses 409. This does not establish identity classification. E. |
| **Another UI process: authentic foreign identity with receiver's current layout/fingerprint/CSRF and current controls** | **FAIL required outcome:** Check **200/failed**, Save **403/Refused**; explicit unknown/stale/foreign-entry reload diagnostic. Parser called twice, checker zero, commit zero. No retargeting. E/final, two independent UI instances/keys. |
| Re-render triggered after unrelated model save | Prior allowed-list tokens give identical 409; current no-op works without commit. E. |
| Mix current identity and remembered same-page stale identity | Entire submission 409/409; current edit not partially applied. E. |
| **Old identity after bounded history eviction** | **FAIL required outcome:** retain genuine GET identity, invoke production _new_render allocator 4096 times, GET current page, post old identity. Check **200/failed**, Save **403**; checker/commit zero. E. |
| Foreign-page identity mixed with current recipient identities/guards | Safely refused whole, but 200/failed versus 403; same uniformity residual. D. |
| Gate-6 hidden-key substitution: xq7Z:one / xsecond-secret:two, edit first to two | Exact keys remain first/second; checker draft equals commit. P and named S/Q regression. |
| Different hidden keys with identical visible values; identical hidden keys with different or identical visible values | First-only edit changes first only in all four combinations. P. |
| Reverse both actions and append to both; remove one action | Named S/Q regressions retain each original real key with its action and remove only the intended action. |

No wrong-target edit was reproduced. The remaining defect is the explicit requirement of this pass: **every identity not from the render this session shows must yield the same reload outcome on Check and Save**. Recognition by a bounded auxiliary tag history must not be necessary to classify a non-current-render identity. Keep that history bounded while fixing the refusal.

### N3 — MEDIUM — PARTIALLY RESOLVED; same residual as N6

**Locations:** core/config_ui/__init__.py:2803, :3538, :3699, :3901. Shared exact-draft code: :3620, :3726, :4165, :4557.

**Executed tests:** E, P/N3, D/N3 and S/Q. Named tests in tests/test_config_ui.py: test_n3_check_gives_the_real_checker_verdict_on_the_draft_save_writes; test_n3_an_invalid_draft_fails_check_on_its_real_values; test_n3_check_and_save_validate_the_same_restored_draft; test_n3_an_edited_stand_in_is_refused_never_written; test_n3_a_stand_in_beside_its_real_key_is_refused_never_resolved; test_p19f9_a_stale_identity_is_refused_never_retargeted; P19F10 identity tests above.

| Replay / defeat | Executed outcome |
|---|---|
| Gate-4 valid enabled audio_output, secret a"b, allowed ['kept', secret], default reference, append added | Baseline actual checker passes; Check 200/passed, Save 303; actual checker draft equals commit with ['kept', 'a"b', 'added'] exactly. P, S/Q. |
| Hidden values inside longer text; reorder/remove/append | Named S/Q exact-draft regressions pass. |
| Legacy whole withheld list with marker truncated by 12 characters, configured → configurex, partial [hidde], or suffixed marker | Explicit refusal before settings checker; Save 403, zero commit. D independently executes all four; S/Q published variants pass. |
| Stand-in beside its real key/value in either key order | Named S/Q test refuses the whole ambiguous payload, neither chosen. |
| Identity shortened by 1/12 characters, case-changed, whitespace-padded, internally changed, all-zero, recombined halves | Explicit whole-request refusal, zero checker/commit. D. Depending on tag recognition, outcome is 409/409 or 200-failed/403; latter is existing uniformity residual. |
| Two submitted values claiming one place / duplicated identity | Explicit duplicate refusal, zero checker/commit. D, S/Q. |
| Remove and edit same entry | Explicit whole-request refusal, zero checker/commit. D, S/Q. |
| Legitimate punctuation/marker-looking replacement or append | Exact typed value accepted: named S/Q test_p19f9_a_legitimate_edit_with_punctuation_or_marker_text_applies; E additionally tests ordinary scalar marker spellings. |
| Old full form after append or value mutation | Named S/Q regressions refuse identities no longer naming current exact value/place; E tests unrelated save and render replacement. |
| Original positional stale layout: reorder/add/remove/rename | Identical Check/Save 409 before parser/checker/commit. P/N2. |
| Gate-7 token equality after Remove/Save | Fixed as under N6; no equality or retargeting. |
| Authentic foreign-process/page/evicted identity with current guards | **OPEN:** explicit side-effect-free refusal, but different Check/Save outcomes instead of common stale response. Same N6 residual, not a new duplicate finding. |

### N7 — MEDIUM — RESOLVED

**Locations:** core/config_ui/__init__.py:1948 (selector), :2430 / :2444 (ordinary operation), :2204 (patch operation), :3488 / :3560 (parsing), :4099 (operation semantics), :4165 (shared clear/set draft).

**Executed tests:** E/N7, D and S/Q. Named tests in tests/test_config_ui.py: test_p19f10_gate7_n7_a_withheld_ordinary_scalar_can_be_set_to_the_empty_string; test_p19f10_a_genuinely_unchanged_withheld_field_sends_nothing; test_p19f10_an_unset_scalar_can_be_set_to_the_empty_string; test_p19f10_clearing_an_optional_overlay_entry_restores_the_base; test_p19f10_a_withheld_entry_can_be_replaced_by_the_empty_string; test_p19f10_removal_controls_drop_exactly_their_entry; test_p19f10_a_withheld_rule_parameter_offers_no_clear.

| Replay / defeat | Executed outcome |
|---|---|
| Gate-7 baseline-valid enabled audio_output: withheld model q7Z → empty text with operation set | Actual checker Check 200/passed, Save 303; draft/commit contain model exactly ''. E, S/Q. |
| Value equal to rendered replacement (empty), value configured (hidden), or [hidden] | Accepted exactly as typed, no inference/restoration. E. |
| Whitespace only (three spaces) | Actual checker draft and commit contain exactly three spaces. E. |
| Set equal to current base value q7Z | Intentional explicit override created; Check/Save agree. E. |
| Set equal to current overlay value q7Z | Checker sees same overlay; Save 200/Nothing to save, no additional commit. E. |
| Genuinely unchanged ordinary field; full untouched patch editor | No field edit added to draft; no-op Save 200, zero additional commits. E, S/Q. |
| Clear optional withheld overlay model | Actual checker draft {}; Save 303 writes {}; base-model effective again, no collateral deletion. E, S/Q. |
| Clear optional mapping entry beside withheld key | Named S/Q test removes optional only, retaining xq7Z:one. |
| Remove list entry with hidden neighbor | E uses valid enabled audio fixture, removes kept and retains q7Z/default reference; exact checker/commit match. S/Q additionally remove other only beside kept/q7Z. |
| Set withheld list member to empty string | S/Q commits ['kept', '', 'xq7Zy'] and checks exact same draft. |
| Typed text with unchanged/clear, invalid remove/SET operation | Entire submission explicitly refused; no checker/commit. E. |
| Forged clear on patch member, base-only scalar, protected reference/credential or field inside replacing rule list | D patch clear refused; S/Q base-only/trigger no-clear and writing protections pass. No scope widening or unintended deletion reproduced. |

## RE-CONFIRMED

Each line cites commands executed in this pass.

- **F1 RESOLVED — P + Q + S + G:** original literal ~/gate-base.yaml startup collision; same-base authenticated Save 403, zero commit; existing /usr/bin/bzip2 and /usr/bin/bzcat hard links compare as one file. Named test_f1_save_never_replaces_the_base_whatever_the_overlay_spelling and collision families run. core/overlay.py:249; core/config_ui/__init__.py:3812, :4798.
- **F2 RESOLVED published cases — P + D + Q + S:** q7Z ordinary setting and declared gate-secret-channel-97bf channel key withheld; escaped/nested/Unicode/short-value tests pass, structural names intact, no recoverable configured key in opaque identities. test_f2_* and AC35 run. core/config_ui/__init__.py:1107, :1183, :2116.
- **F3 RESOLVED — P + Q + S:** absent probe remains True at runtime, displays default true; false/true options reach checker/Save as actual booleans. Named default/tri-state tests run. core/config_ui/__init__.py:2459.
- **F4 RESOLVED — P + D + Q + S:** original 128 blocked requests: 12 queued, 16 handled/401, 112 refused/503, admission restored. Actual aiohttp body reading 0/1048576/1048577/1 bytes gives 200/200/413/200. Named saturation/failed-read tests pass. core/config_ui/__init__.py:5253, :5319, :5344.
- **F5 RESOLVED — P + Q + S:** original timeout double: terminate, wait(10.0), kill, wait(5.0); False, child retained, never unbounded wait. Named unkillable-child Apply/close tests run. core/config_ui/__init__.py:5039.
- **N1 RESOLVED — P + Q + S:** r/a/e/s/true/false secrets leave option tokens intact; checker/Save receive bools; all named boolean/enum parameterizations run. core/config_ui/__init__.py:2459.
- **N2 RESOLVED — P + Q + S:** original channel reorder/add/remove/rename: identical 409 bodies, zero parser/checker/commit. test_ac24_a_key_reorder_makes_a_rendered_check_stale_like_save covers base/overlay cases. core/config_ui/__init__.py:3699, :3901, :4417.
- **N4 RESOLVED — P + Q + S:** original quote-only/backslash-only secrets leave legitimate lists decodable/appendable; nested punctuation families pass. core/config_ui/__init__.py:1229, :2095.
- **N5 RESOLVED — P + Q + S:** published xq7Z/xsecond-secret mapping retains exact original keys/values after append; same-secret/identical-redacted/legitimate-marker keys survive. Collision exhaustion regression remains explicit. core/config_ui/__init__.py:1146, :2158, :1407.

### Gate-1 R1–R10 requirement map → named running tests

M verified all 109 qualified gate-1 references. S reran their parameterizations. These are the original map's named tests with this pass's judgment; green tests do not override the N3/N6 reproductions.

| Requirement | Result | Named running evidence / command |
|---|---|---|
| R1 — separate UI, bind/token/Host/Origin/CSRF guards | PASS | S: `tests/test_config_ui.py::test_the_module_entry_point_calls_main`; `tests/test_config_ui.py::test_ac1_a_non_loopback_host_is_refused_before_any_bind`; `tests/test_config_ui.py::test_ac2_each_start_has_a_fresh_256_bit_token`; `tests/test_config_ui.py::test_ac3_every_route_without_a_session_is_refused_without_content`; `tests/test_config_ui.py::test_ac4_a_post_without_or_with_a_wrong_csrf_token_is_refused`; `tests/test_config_ui.py::test_ac4_a_non_post_to_a_state_changing_path_is_405`; `tests/test_config_ui.py::test_ac6_refused_hosts_get_403_even_on_get`; `tests/test_config_ui.py::test_ac6_refused_origins_get_403_and_change_nothing`; `tests/test_config_ui.py::test_ac5_the_socket_patch_is_effective` |
| R2 — shared overlay, precedence, base preservation | PASS | S: `tests/test_overlay.py::test_ac7_merged_document_is_exact_and_inputs_are_unmutated`; `tests/test_overlay.py::test_ac7_overlay_null_replaces_the_base_value`; `tests/test_overlay.py::test_ac9_overlay_below_minimum_is_refused_then_deleting_it_accepts`; `tests/test_overlay.py::test_ac9_run_hands_the_module_the_overlay_value`; `tests/test_overlay.py::test_ac9_explicit_overlay_is_honoured_by_main_and_run`; `tests/test_overlay.py::test_overlay_is_merged_before_environment_resolution`; `tests/test_overlay.py::test_relative_modules_directory_resolves_from_the_base_directory`; `tests/test_overlay.py::test_ac10_bad_overlay_names_the_file_never_the_content` |
| R3 — all titles/defaults and annotation validation | PASS | S: `tests/test_manifest_presentation.py::test_presented_covers_every_shipped_manifest`; `tests/test_manifest_presentation.py::test_every_setting_node_has_a_title`; `tests/test_manifest_presentation.py::test_every_documented_default_is_declared`; `tests/test_manifest_presentation.py::test_every_declared_default_validates_against_its_node`; `tests/test_manifest_presentation.py::test_default_violating_the_node_is_refused_as_default`; `tests/test_manifest_presentation.py::test_default_does_not_constrain_validated_values`; `tests/test_manifest_presentation.py::test_property_named_default_is_a_property_not_an_annotation` |
| R4 — generated base/core/module pages and honest controls | PASS | S: `tests/test_config_ui.py::test_ac15_the_base_page_lists_every_module_and_the_origin`; `tests/test_config_ui.py::test_ac16_every_schema_path_is_shown_with_title_help_and_default`; `tests/test_config_ui.py::test_ac17_a_module_added_as_a_directory_gets_its_titled_page`; `tests/test_config_ui.py::test_ac17_no_ui_source_file_names_a_shipped_module`; `tests/test_config_ui.py::test_ac18_unrenderable_nodes_get_notices_and_controls_keep_the_schema`; `tests/test_config_ui.py::test_ac19_the_twitch_page_shows_the_configured_channel_policy`; `tests/test_config_ui.py::test_ac20_the_core_page_renders_every_declared_limit`; `tests/test_config_ui.py::test_ac20_secrets_and_actions_are_read_only_with_every_rule`; `tests/test_config_ui.py::test_ac20_an_uncovered_action_is_listed_until_a_rule_covers_it`; `tests/test_config_ui.py::test_ac21_readiness_combines_the_check_verdict_and_the_running_state` |
| R5 — unsaved Check through existing path, all diagnostics, no side effects | PARTIAL: N3/N6 refusal outcome | S: `tests/test_config_ui.py::test_ac38_an_unsaved_invalid_edit_fails_and_writes_nothing`; `tests/test_config_ui.py::test_ac38_an_unsaved_fix_passes_and_writes_nothing`; `tests/test_config_ui.py::test_ac22_unresolved_references_are_all_reported_and_stop_the_check`; `tests/test_config_ui.py::test_ac22_every_invalid_setting_is_reported_in_one_check`; `tests/test_config_ui.py::test_ac23_check_signals_nothing_starts_nothing_and_creates_no_socket`; `tests/test_config_ui.py::test_bridge_check_runs_the_real_checker_from_a_running_loop` |
| R6 — exact writing scope, managed path, atomicity, stale checks, removal | PARTIAL: N3/N6 refusal outcome | S: `tests/test_config_ui.py::test_ac24_each_save_writes_exactly_its_override_and_logs_paths_only`; `tests/test_config_ui.py::test_ac25_remove_override_restores_the_base_value_and_prunes`; `tests/test_config_ui.py::test_ac26_an_invalid_save_returns_a_field_diagnostic_and_writes_nothing`; `tests/test_config_ui.py::test_ac26_a_stale_save_or_remove_is_refused`; `tests/test_config_ui.py::test_ac27_writes_outside_the_scope_or_at_protected_fields_are_refused`; `tests/test_config_ui.py::test_ac27_posted_protected_or_out_of_scope_fields_are_refused`; `tests/test_config_ui.py::test_ac27_hand_written_actions_and_secrets_survive_an_unrelated_save`; `tests/test_config_ui.py::test_ac27_a_save_or_remove_through_a_yaml_alias_leaves_the_other_path_unchanged`; `tests/test_config_ui.py::test_ac27_an_ancestor_save_must_keep_every_protected_descendant`; `tests/test_config_ui.py::test_ac28_an_interrupted_write_leaves_the_previous_overlay_intact`; `tests/test_config_ui.py::test_ac28_the_write_targets_only_the_managed_overlay` |
| R7 — supervised Apply, status publication/classification, drift, bounded result | PASS | S: `tests/test_config_ui.py::test_ac29_a_failing_on_disk_check_refuses_and_touches_no_process`; `tests/test_config_ui.py::test_ac30_a_ready_record_of_the_child_with_the_on_disk_digest_is_accepted`; `tests/test_config_ui.py::test_ac30_a_child_exiting_2_is_refused_with_its_status_and_diagnostic`; `tests/test_config_ui.py::test_ac30_a_child_writing_nothing_within_the_window_is_unknown`; `tests/test_config_ui.py::test_ac31_apply_never_signals_a_foreign_record_pid`; `tests/test_config_ui.py::test_ac32_drift_follows_the_overlay_and_the_record`; `tests/test_config_ui.py::test_ac39_ac40_a_child_writing_only_an_unaccepted_record_is_unknown`; `tests/test_config_ui.py::test_ac40_every_listed_mutation_of_v_is_unusable_with_a_value_free_reason`; `tests/test_status_record.py::test_three_transitions_publish_three_records_in_order`; `tests/test_status_record.py::test_the_record_describes_the_loaded_configuration`; `tests/test_status_record.py::test_a_status_path_naming_a_configuration_file_is_refused` |
| R8 — no secret in responses, diagnostics, logs or reports | PASS | S: `tests/test_config_ui.py::test_ac35_no_secret_value_reaches_any_response_log_report_or_record`; `tests/test_config_ui.py::test_check_diagnostics_pass_the_redaction_guard`; `tests/test_config_ui.py::test_d9_the_refused_tail_is_redacted_and_value_free`; `tests/test_config_ui.py::test_ac24_a_saved_path_naming_a_secret_is_logged_redacted` |
| R9 — dependencies and offline resources | PASS | S: `tests/test_main.py::test_pyproject_declares_the_console_script_and_the_build_backend_for_tests`; `tests/test_config_ui.py::test_pages_use_inline_assets_and_relative_targets_only`; `tests/test_config_ui.py::test_r9_module_pages_use_inline_assets_and_relative_targets_only`; `tests/test_config_ui.py::test_ac36_the_url_audit_finds_every_external_target`; `tests/test_config_ui.py::test_ac36_a_configured_url_is_escaped_text_only` |
| R10 — operator documentation | PASS | S: `tests/test_config_ui.py::test_ac37_doc_launch_and_local_only`; `tests/test_config_ui.py::test_ac37_doc_overlay_rule_and_merge`; `tests/test_config_ui.py::test_ac37_doc_writable_and_read_only_blocks`; `tests/test_config_ui.py::test_ac37_doc_secrets_policy`; `tests/test_config_ui.py::test_ac37_doc_restart_drift_and_status_file` |

### Gate-1 operator-visible guarantees

- **Writing scope / 6c PASS — S + P/F1 + E + G:** five writable blocks; actions/secrets read-only; references/credentials/protected ancestors retained; managed-overlay-only writes; base preserved.
- **No secret escapes: published cases PASS — S AC35 + P/F2 + D + Q:** scalar/key/nested/escaped canaries withheld, structural tokens preserved, real values retained through patches. No universal absence proof.
- **Overlay PASS — S overlay/AC24–AC28 + P/F1 + E/N7:** shared merge/precedence, runtime/CLI/Check parity, atomicity, removal/pruning, empty-string set/clear and base preservation.
- **Local-only surface PASS tested boundary — S AC1–AC6/AC41 + P/F4 + D/F4:** loopback default, session/Host/Origin/POST/CSRF, collision guards, admission/body bounds. No live load claim.
- **Generated module pages PASS — S AC15–AC20 + M + E:** all 17 manifests and added-directory fixture, labels/defaults/help/notices, no module HTML; N7 edit expressiveness fixed.
- **Check: exact real drafts/side effects PASS, uniform refusal FAIL — S AC22/AC23/AC38 + P/N3 + E + D:** actual checker, diagnostics, zero write/process/socket; N3/N6 outcome mismatch remains.
- **Apply PASS tested outcomes — S AC29–AC34/AC39–AC43 + P/F5 + G:** supervised child only, accepted/refused/unknown, loaded-document digest, transitions, drift, stderr drain, bounded stop waits. No total 60-second transaction claim.
- **Dependencies/offline PASS — S AC36 + G:** runtime bounds unchanged (aiohttp>=3.9,<4; PyYAML>=6,<7), only console entry added; inline assets/relative targets, no render-time fetch. Ignore omission is N8.
- **Operator documentation: subjects covered — S AC37 + G:** launch/local-only, merge/base-key rule, scope/read-only reasons, secrets, restart/drift/status/collisions. Earlier-render stale-page promise at docs/config-ui.md:254 has the N3/N6 history exception.

### Phases 0–3 regression guarantees

All named tests below passed under S; M independently confirms unchanged operational manifests and module implementations.

| Guarantee | Named running evidence / command |
|---|---|
| Default-deny authorization, including reads | `tests/test_actions.py::test_zero_rules_refuse_a_read_and_a_write_with_zero_invocations`; `tests/test_actions.py::test_a_read_rule_authorizes_the_read_only` ; command S |
| Bounded admission and retention | `tests/test_admission.py::test_full_session_queue_and_global_cap_reject_with_reason_and_depth`; `tests/test_admission.py::test_three_simultaneous_sessions_never_exceed_the_worker_limit`; `tests/test_retention.py::test_ac20_bus_history_byte_limit_evicts_before_the_count_limit_without_failing_a_publication`; `tests/test_retention.py::test_ac30_byte_cap_leaves_fewer_than_three_exchanges_and_at_most_two_hundred_bytes` ; command S |
| Explicit terminal outcomes | `tests/test_actions.py::test_the_six_terminal_statuses_are_produced_exactly`; `tests/test_admission.py::test_total_deadline_after_emission_is_classified_external_unknown_once` ; command S |
| One global startup/shutdown deadline | `tests/test_main.py::test_a_hanging_activation_is_bounded_by_the_startup_deadline`; `tests/test_main.py::test_a_hanging_settings_validator_is_bounded_by_the_startup_deadline`; `tests/test_lifecycle.py::test_one_global_shutdown_deadline_caps_the_sum_of_local_timeouts`; `tests/test_shutdown.py::test_cli_deadline_covers_cancellation_resistant_task` ; command S |
| Runtime secret redaction | `tests/test_main.py::test_configured_secrets_are_redacted_from_traces_and_loss_diagnostics_through_the_entry_point`; `tests/test_main.py::test_declared_credentials_are_redacted_without_a_secrets_entry_through_the_entry_point` ; command S |
| Phase 2 R10 deadline ruling | `tests/test_audio_input.py::test_the_only_deadline_subtractions_are_the_two_ac41_authorizes`; `tests/test_audio_output.py::test_the_stop_grace_runs_after_the_stop_never_before_the_deadline`; `tests/test_audio_input.py::test_the_recorder_is_still_killed_at_the_deadline_itself_with_transcription` ; command S |
| No default provider/model | `tests/test_audio_output.py::test_an_empty_endpoint_sends_no_request_and_leaves_speak_unbound`; `tests/test_hygiene.py::test_ac45_core_and_modules_name_no_model`; phase-4 manifest comparison below confirms no operational provider declarations changed ; command S |
| Moderation modes, strictest default, no permanent bans | `tests/test_moderation.py::test_ac24_the_default_mode_alerts_with_no_platform_request_and_one_fact`; `tests/test_moderation.py::test_ac26_a_request_in_mode_propose_yields_a_proposal_id_and_no_request`; `tests/test_moderation.py::test_ac26_auto_apply_applies_a_delete_at_once_under_the_strict_rules`; `tests/test_moderation.py::test_no_operation_removes_a_viewer_permanently` ; command S |
| Viewer-memory bounds and eviction | `tests/test_viewer_memory.py::test_ac14_eviction_deletes_the_least_recent_then_least_used_with_one_fact`; `tests/test_viewer_memory.py::test_ac15_the_total_bound_holds_and_the_target_is_never_deleted`; `tests/test_viewer_memory.py::test_ac18_the_bounds_hold_before_readiness`; entire viewer-memory suite passes ; command S |
| Requested-only watch by default | `tests/test_watch.py::test_ac29_without_activation_no_tick_is_emitted`; `tests/test_watch.py::test_ac29_a_viewers_start_command_starts_nothing`; `tests/test_watch.py::test_ac29_the_broadcasters_start_command_ticks_every_interval` (explicit startup activation remains supported) ; command S |
| Honest platform capabilities | `tests/test_moderation.py::test_ac27_delete_message_on_kick_is_platform_unsupported_with_no_request`; `tests/test_clips.py::test_ac12_with_twitch_and_kick_clips_are_ready_for_twitch_and_unsupported_on_kick`; `tests/test_youtube.py::test_the_moderation_service_offers_delete_and_timeout_and_no_clip_or_poll` ; command S |

### Cumulative integration, checklist and assertion audit

- **AC1–AC43 PASS — M + S:** named running coverage, with actual S for AC11 and annotation tests for AC12–AC14.
- **Digest/runtime/status/Apply PASS — G + S:** runtime hashes loaded merged unresolved document; UI drift/Apply use shared deep_merge/canonical_digest. Publisher digest/modules fixed at load, transitions published. Production canonical_digest callers: core/main.py:534, core/overlay.py:174.
- **Exact draft/parser integration PASS, uniform identity refusal OPEN — G + E + P + D + S:** shared _draft_value/_apply_edits, typed actual paths, mutation locks and guards. Explicit operations do not turn no-ops into deletions.
- **Sockets/dispatch/supervision PASS tested contracts — G + S + P/F4/F5:** no ui.handle/self.handle/asyncio.run call inside UI adapter; admission before body reading/submission; shell-free spawn starts drain; socket-creation fixture teardown passed.
- **Write sinks PASS reviewed boundary — G + S + P/F1:** only managed overlay/temp and runtime status/temp persistence added; same-file protections; no UI FileHandler; ordinary atomic/persistence tests ran.
- **No hardcoded module page/name or external assets PASS — M + S AC17/AC36 + G.**
- **Dependencies PASS — G + S:** backend/bounds unchanged, exact two console entries.
- **Caller enumeration compatible; historical counts outdated — G AST + S:** relevant calls: core/main 4; test_main 59, profiles 6, examples 5, integration 2, shutdown 7, lifecycle 1, retention 2; new status_record 3, config_ui 6, overlay 9. Existing omitted-overlay behavior tested; validate_schema recursion carries annotation check.
- **Targets/files checklist PARTIAL — M + G + I:** recorded conftest/presence-pack deviations remain. Required .gitignore target unchanged and patterns missing (N8); prior “equal except two deviations” summaries missed it.
- **Assertion audit — G + S:** users-schema adaptation retains type/minima/properties/grants; console adaptation retains dependencies/backend/exact entries; presence wall-time floor retains completion checks. P19F10 changes opaque-token format assertion to 48 hex chars and transports operations, retaining exact-value assertions. _write now computes guards without extra GET to avoid retiring loaded identity; explicit stale-layout tests retain captured guards. Its default current guards alone are not proof of a browser's retained guards; E/P/D retain real generated guards where needed. Historical tautology at tests/test_config_ui.py:1325 excluded from evidence. No changed assertion establishes the reproduced refusal mismatch as acceptable.

## NEW FINDINGS

### N8 — LOW — required overlay/status ignore patterns omitted

**Locations:** .gitignore:1 (no *.local.yaml or *.status.json rule); docs/campaigns/phase4/spec.md:54; docs/campaigns/phase4/plan.md:604.

**Executed reproduction I:** git check-ignore --no-index -v -- config.local.yaml config.yaml.status.json presence.local.yaml modules/brain/operator.local.yaml.

**Outcome:** exit 1, empty stdout: none ignored. git diff 835d9b4..HEAD -- .gitignore is empty. The declared P17 patterns were omitted, so generated operator files remain eligible for ordinary Git staging. No accidental staging or secret publication was induced. This is a cumulative-branch omission newly found here, not attributed to P19F10.

Add the two specified patterns and repeat I. No other independent new defect established; refusal uniformity is existing N3/N6.

## Reproducible N3/N6 residual

Command: PYTHONDONTWRITEBYTECODE=1 .venv/bin/python - with this stdin. Actual generated controls/authenticated handlers, two independent UI keys/sessions, receiver's current guards, in-memory reads and commits; no files or sockets.

```python

import copy, html, json, re, runpy, hashlib
from contextlib import contextmanager, ExitStack
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode
import core.config_ui as u
import core.main as main
t=runpy.run_path('tests/test_config_ui.py')
parse=t['_patch_editor'];browser=t['_browser'];submit=t['_submitted'];replace=t['_replaced']
@contextmanager
def fixture(module='brain', settings=None, env=None, enabled=False):
    env=env or {'GATE_SECRET':'q7Z','SECOND':'second-secret'}
    base={'modules_directory':'builtin','enabled_modules':[module] if enabled else [],
          'secrets':['$'+'{'+k+'}' for k in env], 'modules':{module:copy.deepcopy(settings or {})}}
    st={'overlay':{},'seen':[],'writes':[],'pages':{}}
    with ExitStack() as s:
        for mod in (u,main):
            s.enter_context(patch.object(mod,'read_base',side_effect=lambda *a:copy.deepcopy(base)))
        s.enter_context(patch.object(u,'read_overlay',side_effect=lambda *a:copy.deepcopy(st['overlay'])))
        s.enter_context(patch.object(u,'_file_state',return_value=(False,None,None)))
        def fingerprint(*a):
            return hashlib.sha256(json.dumps(st['overlay']).encode()).hexdigest() if st['overlay'] else 'absent'
        s.enter_context(patch.object(u,'_file_fingerprint',side_effect=fingerprint))
        s.enter_context(patch.object(u,'read_status',return_value=u.StatusReading.absent()))
        ui=u.ConfigUI(u.UISettings(Path('/nonexistent-gate/config.yaml')),environ=env)
        def login(target=ui):
            r=target.handle(u.UIRequest('GET','/',{'token':target.token},{'host':target.access_authority}))
            h={'host':target.access_authority,'cookie':dict(r.headers)['Set-Cookie'].split(';')[0],
               'content-type':'application/x-www-form-urlencoded'}
            session=target._session(u.UIRequest('GET','/',{},h));h['x-csrf-token']=session.csrf_token
            return h
        h=login()
        def commit(draft,*args):
            st['writes'].append(copy.deepcopy(draft));st['overlay']=copy.deepcopy(draft)
            return u.WriteResult(u.OUTCOME_SAVED)
        s.enter_context(patch.object(ui,'_commit',side_effect=commit))
        real=ui.checker
        def checker(*a):
            st['seen'].append(copy.deepcopy(a[-1]))
            return real(*a) if enabled else (True,[])
        ui.checker=checker
        def get(page=None,headers=None,target=ui):
            page=page or '/module/'+module
            r=target.handle(u.UIRequest('GET',page,{},headers or h));assert r.status==200
            content=r.body.decode();st['pages'][page]=content;return content
        def post(fields,page=None,route='/check',layout=None,headers=None,target=ui):
            page=page or '/module/'+module
            layout=layout or re.search('name="layout" value="([^"]+)"',st['pages'][page]).group(1)
            body=urlencode([('page',page),('layout',layout),('fingerprint',fingerprint()),*fields]).encode()
            return target.handle(u.UIRequest('POST',route,{},headers or h,body))
        yield ui,base,st,h,get,post,login
def pair(post,fields,**kw):
    return post(fields,**kw),post(fields,route='/save',**kw)
def stale(post,fields,st,**kw):
    before=copy.deepcopy(st['overlay']);n=len(st['writes']);seen=len(st['seen'])
    c,s=pair(post,fields,**kw)
    assert c.status==s.status==409,(c.status,s.status,c.body.decode())
    assert c.body==s.body
    assert 'reload' in c.body.decode() and 'Refused: stale page' in c.body.decode()
    assert st['overlay']==before and len(st['writes'])==n and len(st['seen'])==seen
    return c,s
def exact(post,fields,st):
    n=len(st['seen']);c,s=pair(post,fields)
    assert c.status==200 and 'data-passed="true"' in c.body.decode(),c.body.decode()
    assert s.status in (200,303),(s.status,s.body.decode())
    assert st['seen'][n]==st['overlay'],(st['seen'][n],st['overlay'])
    return c,s
audio={'synthesis':{'endpoint':'','model':'q7Z'},
       'voices':{'allowed':['q7Z'],'default':'$'+'{GATE_SECRET}'},
       'outputs':{'o':{'player':{'argv':['cat']}}},'default_output':'o'}

# Authentic foreign identity + current recipient fields and guards:
with fixture('audio_output',audio,{'GATE_SECRET':'q7Z'}) as (ui,b,st,h,get,post,login):
    foreign=u.ConfigUI(u.UISettings(ui.base_path),environ=ui.environ)
    fh=login(foreign)
    other=parse(get(headers=fh,target=foreign),'.allowed')
    current=parse(get(),'.allowed')
    fields=[*browser(current),*submit(other,replace(other['entries'][0],''))]
    with patch.object(ui,'parse_edits',wraps=ui.parse_edits) as parsed:
        c,s=pair(post,fields)
        assert (c.status,s.status)==(200,403)
        assert 'data-passed="false"' in c.body.decode()
        assert parsed.call_count==2 and not st['seen'] and not st['writes']
    print('FOREIGN: Check200/failed Save403 parser2 checker0 commit0; expected409/409')
# Genuine stale identity after production bounded-history eviction:
with fixture('audio_output',audio,{'GATE_SECRET':'q7Z'}) as (ui,b,st,h,get,post,login):
    old=parse(get(),'.allowed')
    tag=next(iter(ui._render_tags))
    session=ui._session(u.UIRequest('GET','/',{},h))
    for _ in range(u.MAX_RENDER_TAGS):
        ui._new_render(session,'/module/audio_output')
    assert tag not in ui._render_tags
    get()
    c,s=pair(post,browser(old))
    assert (c.status,s.status)==(200,403) and not st['seen'] and not st['writes']
    print('EVICTED: Check200/failed Save403 checker0 commit0; expected409/409')

```

## NOT VERIFIED

- No real browser/JavaScript, live listener/load, installed-console or browser navigation/unsaved-form preservation smoke test. Sessions/tabs/processes were simulated through authenticated request-core instances; foreign-process probe uses independent UI keys, not OS-launched servers.
- The 18 environment-gated skips remain: capture process groups, phase-2/phase-3 opt-in trials and distribution installation. No live provider/platform/audio/capture hardware or moderation calls.
- Independent Save/Remove probes intercept commits; S/Q cover ordinary real temporary-file persistence, base preservation and atomicity. No exhaustive filesystem race/permissions/external-writer proof, newly mounted bind alias or case-insensitive filesystem.
- No OS child stuck after SIGKILL induced; bounded doubles and ordinary subprocess tests ran. No full Check/filesystem/spawn/Apply 60-second total deadline or stalled-upload wall-clock bound established.
- Exhaustive random render-tag collision absence is not proven by execution. Tested identities differed across renders/sessions/instances; typed actual path fixes the published deterministic equality. Eviction probe exercises unmodified production allocator followed by real current GET.
- No universal absence proof. N7 and the old retargeting are fixed; uniform refusal and the omitted declared ignore deliverable prevent APPROVE.
