# Phase 4 — full-branch closing gate, seventh pass

## VERDICT

REQUEST_CHANGES

Reviewed main at **a049b6da5b208713d7dee7ab8aaa6765f2511b6b**, cumulatively as **835d9b4..HEAD**, against the phase-4 spec, plan, binding brief decision 6c, P19F9's architectural decision and this gate's requirements.

The published N5 key loss and N6 cross-list hidden-key substitution are fixed. Published N3 stand-in corruption and Check/Save divergence are fixed. However, a stale patch identity can still name a different configured channel: the identity hashes a positional presentation of its top-level path instead of that path's real key. Successful Remove/Save operations reproduced the retargeting. A new MEDIUM finding, N7, makes a legitimate empty-string replacement impossible in ordinary scalar controls. Phase 4 cannot close.

### Commands and execution

- **S — required suite:** PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/ -q -p no:cacheprovider. **3035 passed, 18 skipped in 171.13 s; exit 0.** The required invocation is unchanged apart from disabling bytecode writes.
- **Q — focused published regressions:** PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/test_config_ui.py tests/test_overlay.py tests/test_status_record.py -q -p no:cacheprovider -k 'test_f1 or test_f2 or test_f3 or test_f4 or test_f5 or test_n1 or stale_layout or test_n4 or p19f9 or test_n3 or test_n5 or p19f5'. **130 passed, 370 deselected in 74.05 s; exit 0.** N2's named AC24 tests additionally ran under S.
- **E — independent new-mechanism probes:** PYTHONDONTWRITEBYTECODE=1 .venv/bin/python - with inspected stdin. Actual authenticated GET/Check/Save/Remove handlers; decoded generated controls using the existing tests' HTML parser; configuration reads and commits intercepted in memory. N6/N5 permutations, N3 altered tokens and legacy payloads, cross-session reuse, stale-token retargeting and empty replacements ran. The closing blockers are reproducible below.
- **P — independent historical replays:** the same stdin command. Original F1–F5, N1, N2 and N4 fixtures; current layouts extracted from GET; exact bool options; finite 128-request dispatch and supervisor doubles; installed aiohttp Request.read body-limit probe. No socket, external service or destructive write.
- **M — inventory:** the same stdin command, AST inventory and subprocess collection using PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/ --collect-only -q -p no:cacheprovider. **All 109 fully qualified gate-1 test references exist and collect; 3053 tests collected.** Compared all 17 manifests with git show 835d9b4:<path>, stripping only schema title/default annotations while preserving properties literally named default: operational declarations and constraints match. Zero module HTML; no cumulative module Python implementation changes.
- **G — cumulative review:** git diff --stat 835d9b4 HEAD; git diff --name-status 835d9b4 HEAD; git diff --check 835d9b4 HEAD; cumulative contracts/runtime/packaging/pre-existing-test/scaffolding diffs and final-source inspection of rendering, identity generation, parsing, merging, guards, persistence, publication and supervision. **79 changed files; whitespace check passes.**

Only this report was created or modified in the repository by the review; nothing staged or committed. Existing untracked docs/runs directories were left alone. S/Q used their normal temporary fixtures. All independent probes were file-free; bytecode and pytest cache were disabled.

## OPEN ITEMS

### N5 — MEDIUM — RESOLVED

Locations: core/config_ui/__init__.py:2068 (per-entry rendering), :2092 (mapping-entry removal identities), :2056 (identity material), :1353 (real mapping merge). Executed E/N5, S and Q.

Named running evidence: tests/test_config_ui.py::test_n5_keys_that_only_contain_a_secret_stay_distinct_entries; test_n5_legitimate_stand_in_shaped_text_never_collides; test_n5_a_single_punctuation_secret_keeps_every_key_distinct; test_n5_an_unallocatable_stand_in_fails_the_render_loudly; test_p19f9_gate6_n6_a_visible_value_edit_never_changes_another_entrys_key.

| Replay / defeat | Executed outcome |
|---|---|
| Published {'xq7Z':'one','xsecond-secret':'two'} | Two separate displayed entries and value controls, even though both labels are x[hidden]. Add added='ok': Check 200/passed, Save 303, both original keys and values survive exactly. |
| Two keys containing the same secret: xq7Z/yq7Z | Same successful exact round trip; no collapse. |
| Identical redacted labels: xq7Zq7Z/xq7Z[hidden] | Two controls; successful append retains both real keys and values. |
| Legitimate marker-looking key beside xq7Z | x[hidden] is retained literally as its own key; no aliasing with the hidden key. |
| Key equal to the secret beside legitimate full/numbered stand-ins | q7Z, literal value configured (hidden), and its (2) spelling remain three entries and survive append exactly. |
| Secret substring family: xq7Z/xq7Zmore/q7Zmore | Three entries and three non-secret values retained exactly. |
| Cross-list mappings with same or different underlying hidden keys | E/N6 below retains keys through visible edits, reorder and removal; published N6 substitution is gone. |
| Display allocator exhaustion | S/Q retain the explicit value-free StandInCollision regression. Patch identity does not depend on the displayed stand-in's uniqueness. |

No lost value or key collapse was reproduced.

### N6 — MEDIUM — PARTIALLY RESOLVED

The published collision between editable visible contents is resolved. **The broader end-to-end place-identity guarantee still fails when a configured key above a patch editor changes: the old token can retarget a new channel.** This is the same residual tracked under N3 below, not a second independent finding.

Locations: core/config_ui/__init__.py:2056 (top path is represented by _field_name), :1619 (configured keys become positions), :3354 (current renderer resolves the token), :1369 (list entries merged by their real indexes). Executed E/N6 and E/stale-after-Save, S and Q.

Named running evidence: tests/test_config_ui.py::test_p19f9_gate6_n6_a_visible_value_edit_never_changes_another_entrys_key; test_p19f9_gate6_n6_reversing_and_appending_keeps_each_key_with_its_entry; test_p19f9_a_duplicated_identity_is_refused; test_p19f9_an_identity_from_another_page_is_refused.

| Replay / defeat | Executed outcome |
|---|---|
| Exact gate-6 actions with xq7Z:'one' and xsecond-secret:'two'; edit only first visible value to 'two' | Check passed, Save 303; first stays {'xq7Z':'two'}, second stays {'xsecond-secret':'two'}. Captured checker draft equals committed draft. |
| Different hidden keys, identical visible values | Editing first changes only that entry. Original second key/value untouched. |
| Identical hidden keys, distinct visible values | Same exact first-only edit; neither member inferred from its shown contents. |
| Identical hidden keys and identical visible values | Distinct identities by list position; first-only edit changes only first. |
| Reorder entries | Reverse positions and edit first visible value: exact real keys move with their entries. S/Q additionally execute the published reverse-plus-add-to-both variant. |
| Remove an entry | Mark first action for removal: Check passed, Save 303; exactly the second original action remains. |
| Duplicate an identity to duplicate an entry | Explicit refusal: Check 200/failed before checker; Save 403; zero checker/commit. No guessing or silent duplication. |
| Identity from another module page | Explicit unknown/foreign-page refusal; Check failed before settings validation, Save 403, zero commit. |
| Identity copied into a different authenticated session/tab on the same page/current configuration | Accepted (Check passed, Save 303), and changes exactly its original entry. Tokens are UI/page scoped, not tab/session scoped. This accepted reuse is unambiguous; it is not evidence of cross-entry corruption, nor evidence of foreign-tab refusal. |
| Identity from another UI process | S/Q's test_p19f7_every_published_secret_length_is_still_never_rendered confirms tokens differ under independently generated field keys. |
| Old token after first channel is removed and second is saved with the same policy | **FAIL:** old token equals second's new token; fresh layout plus old identity passes Check/Save and edits second. See N3. |

### N3 — MEDIUM — PARTIALLY RESOLVED; OPEN

Locations: core/config_ui/__init__.py:2056, :1593, :3354; shared real-input path at :3414 (Check), :4284 (_draft_value), :3907 (_apply_edits); legacy whole-withheld submission refusal at :3304.

Executed E/N3, E/stale-after-Save, S and Q. Named running evidence: tests/test_config_ui.py::test_n3_check_gives_the_real_checker_verdict_on_the_draft_save_writes; test_n3_an_invalid_draft_fails_check_on_its_real_values; test_n3_check_and_save_validate_the_same_restored_draft; test_p19f9_gate6_n3_an_altered_stand_in_is_never_written; test_p19f9_a_stale_identity_is_refused_never_retargeted; test_p19f9_an_entry_both_removed_and_edited_is_refused.

| Replay / defeat | Executed outcome |
|---|---|
| Published valid enabled audio fixture, secret a"b, allowed ['kept',secret], append | S/Q real-checker regression passes; Check validates the same real draft Save writes. E separately verifies a baseline-valid enabled audio configuration and exact append beside two hidden q7Z entries. |
| Whole masked list with stand-in truncated by 12 characters | Explicit withheld-whole refusal; Check 200/failed, settings phase not reached; Save 403; nothing committed. |
| Whole masked list with configured → configurex, partial [hidde], or suffixed stand-in | Same refusal. S/Q cover the suffix; E independently executes internal alteration and partial/truncated spellings. No literal marker written. |
| Whole document posting stand-in plus real key/value, both key orders | S/Q test_n3_a_stand_in_beside_its_real_key_is_refused_never_resolved refuses the legacy action-list payload in either order. E's stand-in-plus-real scalar-list document is also refused whole. |
| Truncate identity by 1 or 12 characters | Unknown-entry refusal before checker; Save 403. |
| Case-change identity token, whitespace-pad token, alter one internal character | Same explicit refusal. Preserve the @patch operation prefix to exercise identity parsing. A separately attempted uppercase entire field name failed Check and Save too, but as an unknown ordinary field, after reaching the settings phase. |
| Recombine halves of two tokens; all-zero unrendered token | Explicit unknown-entry refusal; no guessed target, no commit. |
| Two submitted values claiming one place | Duplicate-identity refusal, even with untouched browser fields included; neither value chosen. |
| Remove and edit the same entry | S/Q explicit removed-and-edited refusal; nothing written. |
| Edit inside withheld whole document | Refused whole. Withheld scalar controls are empty replacements: typing a replacement replaces the whole value; it is not interpreted as a patch inside its hidden text. |
| Legitimate punctuation/marker-looking replacement or append | S/Q test_p19f9_a_legitimate_edit_with_punctuation_or_marker_text_applies succeeds exactly as typed, including x[hidden] and full stand-in-looking text. |
| Baseline page, Save appends item, then resubmit the entire old browser form | E: old parent identity becomes unknown; Check failed before settings phase; Save 403. S/Q also refuse identities whose own original values changed. |
| Published N2 stale layout | P: original stale guard returns 409 before parser for reorder/add/remove/rename. This boundary remains intact. |
| **Old entry identity combined with current guard after configured channel replacement** | **FAIL:** Remove first 303; Save second with identical keyword policy 303; old token equals newly rendered second token. Check 200/passed; Save 303 edits second. No unknown/stale diagnostic. |

**Executed closing residual:** Start with an overlay-only Twitch channel first and keyword parameters {'keywords':['q7Z','kept']}, q7Z collected as a secret. GET its patch editor and retain the visible kept entry's value identity. Through actual handlers, Remove first and Save a channel second with exactly the same policy. GET the new page for its current layout/fingerprint, then submit the **old** identity with '"edited"'. Check and Save accept it and write second's keywords as ['q7Z','edited'].

The HMAC input uses _field_name(top, view), which spells both real paths as triggers.twitch.channels.@0.rules[0].parameters.keywords. Relative index and value are identical, so the token is identical. The HMAC hides the key but never includes it. The current layout guard catches an ordinary stale form; it cannot establish that a copied identity names its original place.

**Required:** include the actual typed top-level configuration path in the keyed identity material, retaining opacity; never use a positional presentation as that identity's real path. Test old identities mixed with current guards after legitimate Remove/Save operations, configured-key rename/reorder, and cross-tab transfer. If page revisions/sessions define foreign or stale identities, bind and verify that context explicitly. Check and Save must refuse the whole ambiguous submission before checking or committing.

## RE-CONFIRMED

Each line names executed commands, not an inherited conclusion.

- **F1 — RESOLVED; P + Q + S + G:** original literal ~/gate-base.yaml refuses startup with overlay_path_collision; authenticated same-base Save 403 and zero commit; real existing /usr/bin/bzip2 and /usr/bin/bzcat hard links compare as one file and status collision is base. Q/S execute test_f1_save_never_replaces_the_base_whatever_the_overlay_spelling, test_f1_a_hard_link_of_the_base_refuses_to_start_and_is_never_written and overlay/status collision families. Guards: core/overlay.py:249; core/config_ui/__init__.py:3606, :4516.
- **F2 — RESOLVED published disclosures; P + E + Q + S:** original listed q7Z setting and long declared credential-as-channel-key withheld; Ω, rules/twitch/combination configured keys, quote/backslash/newline/entity strings withheld from their configured positions and attribute names. Q/S test_f2_* and AC35 redaction sweep pass; E retains exact nested real values through edits. Structural names/JSON punctuation remain intact. core/config_ui/__init__.py:1183, :1593, :2044. No recoverable configured key was found in a patch identity.
- **F3 — RESOLVED; P + Q + S:** original absent audio synthesis probe remains True at runtime and displays default: true; extracted true/false options reach both checker and Save as real booleans, Save 303. Named tests test_f3_an_unset_true_default_boolean_renders_its_effective_value and test_f3_a_boolean_draft_expresses_unset_false_and_true. core/config_ui/__init__.py:2355.
- **F4 — RESOLVED; P + Q + S:** original 128 finite blocked requests / four workers yield 12 queued, 16 handled/401, 112 refused/503, admission released. Installed aiohttp Request.read under the adapter's configured 1 MiB limit gives 200/200/413/200 for 0/1048576/1048577/1 bytes. Q/S test_f4_a_saturated_bridge_refuses_honestly_and_queues_nothing_more and test_f4_a_failed_body_read_releases_its_admission pass. core/config_ui/__init__.py:4963, :5039, :5065.
- **F5 — RESOLVED; P + Q + S:** original timeout-on-every-bounded-wait double calls terminate, wait(10.0), kill, wait(5.0); returns False, retains child, never calls unbounded wait. Named unkillable-child restart/close tests and ordinary-child suite pass. core/config_ui/__init__.py:4757.
- **N1 — RESOLVED; P + Q + S:** secrets r/a/e/s/true/false leave submitted option values true/false intact; checker/Save receive real booleans, Save 303. test_n1_a_short_secret_inside_true_or_false_never_rewrites_the_boolean_options and enum-token tests pass. core/config_ui/__init__.py:2355.
- **N2 — RESOLVED published stale-layout cases; P + S:** original first/second channel reorder, add/remove/rename yield identical Check/Save 409 bodies, zero parser/checker/commit calls. S test_ac24_a_key_reorder_makes_a_rendered_check_stale_like_save runs both base/overlay cases. core/config_ui/__init__.py:3478, :4144. The stronger stale-identity/current-guard defeat is explicitly OPEN under N3/N6.
- **N4 — RESOLVED; P + Q + S:** original quote secret with ['kept','voice-two'] and backslash secret with ['kept','ordinary"quote'] decode exactly and append/Save correctly. All ten quote/backslash/bracket/brace/comma/colon/slash/newline nested key/value families retain exact real data through Check and Save. Named test_n4_a_punctuation_secret_never_breaks_a_legitimate_list and test_n4_punctuation_secrets_in_nested_data_mask_the_data_not_the_syntax pass. core/config_ui/__init__.py:1229, :2023.

### Gate-1 requirement map R1–R10 → named running tests

All named tests in the map ran and passed under S. M verified every one of gate 1's 109 qualified references still exists and collects. Passing tests do not close the concrete defects above and below.

| Requirement | PASS | Named running tests / command |
|---|---|---|
| R1 — separate UI, bind/token/Host/Origin/CSRF guards | PASS | **S**: `tests/test_config_ui.py::test_the_module_entry_point_calls_main`; `tests/test_config_ui.py::test_ac1_a_non_loopback_host_is_refused_before_any_bind`; `tests/test_config_ui.py::test_ac2_each_start_has_a_fresh_256_bit_token`; `tests/test_config_ui.py::test_ac3_every_route_without_a_session_is_refused_without_content`; `tests/test_config_ui.py::test_ac4_a_post_without_or_with_a_wrong_csrf_token_is_refused`; `tests/test_config_ui.py::test_ac4_a_non_post_to_a_state_changing_path_is_405`; `tests/test_config_ui.py::test_ac6_refused_hosts_get_403_even_on_get`; `tests/test_config_ui.py::test_ac6_refused_origins_get_403_and_change_nothing`; `tests/test_config_ui.py::test_ac5_the_socket_patch_is_effective` |
| R2 — shared overlay, precedence, base preservation | PASS | **S**: `tests/test_overlay.py::test_ac7_merged_document_is_exact_and_inputs_are_unmutated`; `tests/test_overlay.py::test_ac7_overlay_null_replaces_the_base_value`; `tests/test_overlay.py::test_ac9_overlay_below_minimum_is_refused_then_deleting_it_accepts`; `tests/test_overlay.py::test_ac9_run_hands_the_module_the_overlay_value`; `tests/test_overlay.py::test_ac9_explicit_overlay_is_honoured_by_main_and_run`; `tests/test_overlay.py::test_overlay_is_merged_before_environment_resolution`; `tests/test_overlay.py::test_relative_modules_directory_resolves_from_the_base_directory`; `tests/test_overlay.py::test_ac10_bad_overlay_names_the_file_never_the_content` |
| R3 — all titles/defaults and annotation validation | PASS | **S**: `tests/test_manifest_presentation.py::test_presented_covers_every_shipped_manifest`; `tests/test_manifest_presentation.py::test_every_setting_node_has_a_title`; `tests/test_manifest_presentation.py::test_every_documented_default_is_declared`; `tests/test_manifest_presentation.py::test_every_declared_default_validates_against_its_node`; `tests/test_manifest_presentation.py::test_default_violating_the_node_is_refused_as_default`; `tests/test_manifest_presentation.py::test_default_does_not_constrain_validated_values`; `tests/test_manifest_presentation.py::test_property_named_default_is_a_property_not_an_annotation` |
| R4 — generated base/core/module pages and honest controls | FAIL: unexpressible empty edit (N7) | **S**: `tests/test_config_ui.py::test_ac15_the_base_page_lists_every_module_and_the_origin`; `tests/test_config_ui.py::test_ac16_every_schema_path_is_shown_with_title_help_and_default`; `tests/test_config_ui.py::test_ac17_a_module_added_as_a_directory_gets_its_titled_page`; `tests/test_config_ui.py::test_ac17_no_ui_source_file_names_a_shipped_module`; `tests/test_config_ui.py::test_ac18_unrenderable_nodes_get_notices_and_controls_keep_the_schema`; `tests/test_config_ui.py::test_ac19_the_twitch_page_shows_the_configured_channel_policy`; `tests/test_config_ui.py::test_ac20_the_core_page_renders_every_declared_limit`; `tests/test_config_ui.py::test_ac20_secrets_and_actions_are_read_only_with_every_rule`; `tests/test_config_ui.py::test_ac20_an_uncovered_action_is_listed_until_a_rule_covers_it`; `tests/test_config_ui.py::test_ac21_readiness_combines_the_check_verdict_and_the_running_state` |
| R5 — unsaved Check through existing path, all diagnostics, no side effects | FAIL: stale identity retargeting (N3/N6); empty edit omitted (N7) | **S**: `tests/test_config_ui.py::test_ac38_an_unsaved_invalid_edit_fails_and_writes_nothing`; `tests/test_config_ui.py::test_ac38_an_unsaved_fix_passes_and_writes_nothing`; `tests/test_config_ui.py::test_ac22_unresolved_references_are_all_reported_and_stop_the_check`; `tests/test_config_ui.py::test_ac22_every_invalid_setting_is_reported_in_one_check`; `tests/test_config_ui.py::test_ac23_check_signals_nothing_starts_nothing_and_creates_no_socket`; `tests/test_config_ui.py::test_bridge_check_runs_the_real_checker_from_a_running_loop` |
| R6 — exact writing scope, managed path, atomicity, stale checks, removal | FAIL: exact-target identity (N3/N6); empty edit omitted (N7) | **S**: `tests/test_config_ui.py::test_ac24_each_save_writes_exactly_its_override_and_logs_paths_only`; `tests/test_config_ui.py::test_ac25_remove_override_restores_the_base_value_and_prunes`; `tests/test_config_ui.py::test_ac26_an_invalid_save_returns_a_field_diagnostic_and_writes_nothing`; `tests/test_config_ui.py::test_ac26_a_stale_save_or_remove_is_refused`; `tests/test_config_ui.py::test_ac27_writes_outside_the_scope_or_at_protected_fields_are_refused`; `tests/test_config_ui.py::test_ac27_posted_protected_or_out_of_scope_fields_are_refused`; `tests/test_config_ui.py::test_ac27_hand_written_actions_and_secrets_survive_an_unrelated_save`; `tests/test_config_ui.py::test_ac27_a_save_or_remove_through_a_yaml_alias_leaves_the_other_path_unchanged`; `tests/test_config_ui.py::test_ac27_an_ancestor_save_must_keep_every_protected_descendant`; `tests/test_config_ui.py::test_ac28_an_interrupted_write_leaves_the_previous_overlay_intact`; `tests/test_config_ui.py::test_ac28_the_write_targets_only_the_managed_overlay` |
| R7 — supervised Apply, status publication/classification, drift, bounded result | PASS | **S**: `tests/test_config_ui.py::test_ac29_a_failing_on_disk_check_refuses_and_touches_no_process`; `tests/test_config_ui.py::test_ac30_a_ready_record_of_the_child_with_the_on_disk_digest_is_accepted`; `tests/test_config_ui.py::test_ac30_a_child_exiting_2_is_refused_with_its_status_and_diagnostic`; `tests/test_config_ui.py::test_ac30_a_child_writing_nothing_within_the_window_is_unknown`; `tests/test_config_ui.py::test_ac31_apply_never_signals_a_foreign_record_pid`; `tests/test_config_ui.py::test_ac32_drift_follows_the_overlay_and_the_record`; `tests/test_config_ui.py::test_ac39_ac40_a_child_writing_only_an_unaccepted_record_is_unknown`; `tests/test_config_ui.py::test_ac40_every_listed_mutation_of_v_is_unusable_with_a_value_free_reason`; `tests/test_status_record.py::test_three_transitions_publish_three_records_in_order`; `tests/test_status_record.py::test_the_record_describes_the_loaded_configuration`; `tests/test_status_record.py::test_a_status_path_naming_a_configuration_file_is_refused` |
| R8 — no secret in responses, diagnostics, logs or reports | PASS | **S**: `tests/test_config_ui.py::test_ac35_no_secret_value_reaches_any_response_log_report_or_record`; `tests/test_config_ui.py::test_check_diagnostics_pass_the_redaction_guard`; `tests/test_config_ui.py::test_d9_the_refused_tail_is_redacted_and_value_free`; `tests/test_config_ui.py::test_ac24_a_saved_path_naming_a_secret_is_logged_redacted` |
| R9 — dependencies and offline resources | PASS | **S**: `tests/test_main.py::test_pyproject_declares_the_console_script_and_the_build_backend_for_tests`; `tests/test_config_ui.py::test_pages_use_inline_assets_and_relative_targets_only`; `tests/test_config_ui.py::test_r9_module_pages_use_inline_assets_and_relative_targets_only`; `tests/test_config_ui.py::test_ac36_the_url_audit_finds_every_external_target`; `tests/test_config_ui.py::test_ac36_a_configured_url_is_escaped_text_only` |
| R10 — operator documentation | PASS | **S**: `tests/test_config_ui.py::test_ac37_doc_launch_and_local_only`; `tests/test_config_ui.py::test_ac37_doc_overlay_rule_and_merge`; `tests/test_config_ui.py::test_ac37_doc_writable_and_read_only_blocks`; `tests/test_config_ui.py::test_ac37_doc_secrets_policy`; `tests/test_config_ui.py::test_ac37_doc_restart_drift_and_status_file` |

### Gate-1 operator-visible guarantees

- **Writing scope / 6c — boundary PASS; S + P/F1 + G:** five writable blocks, protected actions/secrets/credentials/references/ancestors, managed-overlay-only writes and base preservation. Exact field targeting and expressible edits fail N3/N6/N7.
- **No secret escapes — published probes PASS; S AC35 + P/F2 + E:** short/declared/escaped/nested canaries withheld in configured positions, controls, logs/reports/diagnostics; no recoverable configured key in opaque identities. No universal absence proof.
- **Overlay — PASS storage/merge; S overlay/AC24–AC28 + P/F1:** recursive mapping precedence, list/scalar/null replacement, explicit/implicit runtime and Check parity, atomicity, removal of overrides and base preservation. N7's ordinary input discards the empty draft edit before this path.
- **Local-only surface — PASS tested boundary; S AC1–AC6/AC41 + P/F4:** loopback default, session/Host/Origin/POST/CSRF, collision guards, bounded admission and body size. No live load claim.
- **One generated page per module — generation PASS, edit completeness FAIL; S AC15–AC20 + M + E:** all 17 manifests and added-directory fixture, labels/defaults/help/notices, no module HTML; N7 blocks a valid operation.
- **Check — bridge/side effects/normalization PASS, exact draft/target FAIL; S AC22/AC23/AC38 + E + P/N2:** actual checker, all diagnostics, no write/process/socket; N3/N6 accepts a retargeted identity and N7 omits the intended empty-string edit.
- **Apply — PASS tested outcomes; S AC29–AC34/AC39–AC43 + P/F5 + G:** accepted/refused/unknown, supervised child only, loaded-document digest, transition publication, disk drift, drained stderr and bounded child waits. No total-transaction 60-second claim.
- **Dependencies/offline — PASS static/test evidence; S AC36 + G:** runtime dependencies remain aiohttp>=3.9,<4 and PyYAML>=6,<7; only the UI console entry is added; inline assets and relative targets, no render-time fetch.
- **Operator documentation — PASS coverage; S AC37 + G:** launch/local-only, overlay and merge/no-base-key-deletion, five writable blocks, read-only actions/secrets, restart/drift/status and bounded 10/5-second waits plus 60-second post-start window. Broad editor promises still need the fixes above.

### Phases 0–3 regression guarantees

Every named test below ran and passed under S. M independently confirms unchanged operational declarations and module Python implementations.

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

### Cumulative integration and assertion audit

G confirms shared deep_merge/canonical_digest for UI/runtime, runtime status publication over the loaded unresolved document, drift over current disk, shared _apply_edits/_draft_value for the two draft routes, guards before routing, mutation locking, bounded admission before body reads, shell-free spawn with started stderr drain, and atomic managed-overlay/status persistence. S covers the actual socket-free checker bridge and ordinary real subprocess/persistence fixtures.

M confirms all 109 qualified gate-1 checklist names and 3053 collected cases. S runs AC1–AC43, including status reader/publisher distinction, loaded-module/digest publication, startup collisions, no external assets, and schema annotations. No operational module implementation or authorization rule change is hidden in the cumulative manifest edits.

The allowlisted users-schema and console-script assertion adaptations retain constraints/dependencies/backend assertions. The recorded presence-wait wall-time floor keeps the completion assertions. Superseded restoration tests now drive rendered entry controls and assert exact real values/drafts, named refusals, and no writes. These adaptations are meaningful for the approved redesign; they do not cover actual configured keys above a patch editor, or an empty ordinary scalar replacement. No weakened assertion establishes either defect as acceptable. The historical tautological assertion is excluded from evidence.

## NEW FINDINGS

### N7 — MEDIUM — an empty string cannot be expressed by a withheld ordinary scalar replacement

**Locations:** core/config_ui/__init__.py:2349 (renders empty replacement and registers empty as unchanged), :3310 (drops an equal submitted value); also :1897 (_editable), :2285 (ordinary scalar control path). Executed E/empty-scalar with a baseline-valid enabled audio_output module and its real checker.

Configure synthesis.endpoint = '' and synthesis.model = 'q7Z', with collected secret q7Z and valid voices/default/output settings. The model is an ordinary string setting, not a credential/reference: replacing it with '' is allowed and passes the actual enabled-module checker.

GET renders its scalar input with value="" and data-withheld="true". Submit the intended empty string:

- Check returns 200/passed, but its draft contains no model edit.
- Save returns 200 / Nothing to save; no commit; the original model remains q7Z.
- Entering two quote characters as a supposed JSON empty string is no workaround: Save 303 stores the literal string '""', since this is an ordinary string control.
- Independently checking the true empty-string draft succeeds. Calling the same validated Save API with the explicit edit (('modules','audio_output','synthesis','model'),'') also accepts it and stores a true empty string.

The control conflates “unchanged” with one legitimate new value. It cannot send the operator's intended empty replacement, even though both real validation and writing accept that value. This is introduced by P19F9's new empty scalar replacement controls.

**Required:** transport “unchanged” independently of the replacement value (for example an explicit replacement operation flag), allowing a replacement with any schema-valid string, including empty. Test the actual form on baseline-valid enabled audio output; assert the checker draft and saved model are both exactly empty. Preserve whole-replacement semantics and secret withholding.

The stale-token retargeting is recorded under existing N3/N6 above; it is not assigned a duplicate new finding ID.

### Reproducible closing blockers, without files or sockets

Command: PYTHONDONTWRITEBYTECODE=1 .venv/bin/python - with the following stdin, from the repository root. Generated controls are parsed with the existing test helper; all configuration reads/writes below are in memory. The channel reproduction runs actual Save schema/policy validation with an observing checker; the audio reproduction uses the actual enabled-module checker. The mocked overlay fingerprint is always absent because no file is written; the identity equality and fresh-vs-stale-layout outcomes do not depend on that stand-in fingerprint.


```python
import copy, html, json, re, runpy
from contextlib import contextmanager, ExitStack
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode
import core.config_ui as u
import core.main as main
t = runpy.run_path('tests/test_config_ui.py')
parse = t['_patch_editor']; browser = t['_browser']; submit = t['_submitted']

@contextmanager
def fixture(module='brain', settings=None, env=None, enabled=False):
    env=env or {'GATE_SECRET':'q7Z','SECOND':'second-secret'}
    base={'modules_directory':'builtin','enabled_modules':[module] if enabled else [],
          'secrets':['${'+k+'}' for k in env], 'modules':{module:copy.deepcopy(settings or {})}}
    state={'overlay':{},'seen':[],'writes':[]}
    with ExitStack() as s:
        for mod in (u,main):
            s.enter_context(patch.object(mod,'read_base',side_effect=lambda *a:copy.deepcopy(base)))
        s.enter_context(patch.object(u,'read_overlay',side_effect=lambda *a:copy.deepcopy(state['overlay'])))
        s.enter_context(patch.object(u,'_file_state',return_value=(False,None,None)))
        s.enter_context(patch.object(u,'_file_fingerprint',return_value='absent'))
        s.enter_context(patch.object(u,'read_status',return_value=u.StatusReading.absent()))
        ui=u.ConfigUI(u.UISettings(Path('/nonexistent-gate/config.yaml')),environ=env)
        login=ui.handle(u.UIRequest('GET','/',{'token':ui.token},{'host':ui.access_authority}))
        h={'host':ui.access_authority,'cookie':dict(login.headers)['Set-Cookie'].split(';')[0],
           'content-type':'application/x-www-form-urlencoded'}
        session=ui._session(u.UIRequest('GET','/',{},h));h['x-csrf-token']=session.csrf_token
        def commit(draft,*args):
            state['writes'].append(copy.deepcopy(draft))
            state['overlay']=copy.deepcopy(draft)
            return u.WriteResult(u.OUTCOME_SAVED)
        s.enter_context(patch.object(ui,'_commit',side_effect=commit))
        if not enabled:
            ui.checker=lambda *a:(state['seen'].append(copy.deepcopy(a[-1])) or True,[])
        def get(page=None,headers=None):
            return ui.handle(u.UIRequest('GET',page or '/module/'+module,{},headers or h)).body.decode()
        def post(fields,page=None,route='/check',layout=None,headers=None):
            page=page or '/module/'+module
            layout=layout or re.search('name="layout" value="([^"]+)"',get(page,headers)).group(1)
            body=urlencode([('page',page),('layout',layout),('fingerprint','absent'),*fields]).encode()
            return ui.handle(u.UIRequest('POST',route,{},headers or h,body))
        yield ui,base,state,h,get,post

def pair(post,fields,**kw):
    c=post(fields,**kw);s=post(fields,route='/save',**kw)
    return c,s


policy={'combination':'all_of','rules':[{'type':'keyword','parameters':{'keywords':['q7Z','kept']}}]}
with fixture('twitch',{'companion_name':'helper'},{'GATE_SECRET':'q7Z'}) as (ui,b,st,h,get,post):
    st['overlay']={'triggers':{'twitch':{'channels':{'first':copy.deepcopy(policy)}}}}
    old=get();ed=parse(old,'.keywords');name=ed['entries'][1]['controls']['value']
    r=post([('path','triggers.twitch.channels.@0')],route='/remove')
    assert r.status==303 and st['overlay']=={}
    r=post([('add_channel_policy','triggers.twitch.channels'),('channel','second'),('combination','all_of'),
            ('rule_type','keyword'),('parameters',json.dumps({'keywords':['q7Z','kept']}))],route='/save')
    assert r.status==303 and len(st['writes'])==2
    fresh=get();ed2=parse(fresh,'.keywords')
    assert name==ed2['entries'][1]['controls']['value']
    c,s=pair(post,[(name,'"edited"')])
    assert ui.last_check.passed and s.status==303 and len(st['writes'])==3
    assert st['writes'][-1]['triggers']['twitch']['channels']['second']['rules'][0]['parameters']['keywords']==['q7Z','edited']
    print('STALE AFTER REAL HANDLER SAVES: remove first 303, add second 303, old token unchanged, Check pass Save 303 edits second')

audio={'synthesis':{'endpoint':'','model':'q7Z'},
       'voices':{'allowed':['q7Z'],'default':'${GATE_SECRET}'},
       'outputs':{'o':{'player':{'argv':['cat']}}},'default_output':'o'}
with fixture('audio_output',audio,{'GATE_SECRET':'q7Z'},enabled=True) as (ui,b,st,h,get,post):
    assert ui.check().passed
    name='modules.audio_output.synthesis.model'
    tag=re.search('<input[^>]*name="'+re.escape(name)+'"[^>]*>',get()).group(0)
    assert 'value=""' in tag and 'data-withheld="true"' in tag
    c,s=pair(post,[(name,'')])
    assert ui.last_check.passed and s.status==200 and not st['writes']
    assert 'Nothing to save' in s.body.decode()
    draft={'modules':{'audio_output':{'synthesis':{'model':''}}}}
    assert ui.checker(ui.base_path,ui.environ,ui.overlay_path,draft)[0]
    c,s=pair(post,[(name,'""')])
    assert s.status==303
    assert st['writes'][-1]['modules']['audio_output']['synthesis']['model']=='""'
    result=ui.save([(('modules','audio_output','synthesis','model'),'')],
                   fingerprint='absent',page='/module/audio_output')
    assert result.accepted
    assert st['writes'][-1]['modules']['audio_output']['synthesis']['model']==''
    print('N7: form cannot express valid empty scalar; explicit validated Save accepts it')

```

### Execution accounting

The ordinary sandbox failed before the first command (bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted). Commands completed through approved execution fallback; no approval rejection remains. An initial suite output handle was unavailable, so the full required suite was rerun and its complete final result captured as S above.

Probe corrections were confined to stdin: a whole-name uppercase mutation produced a safe ordinary-field refusal instead of an early patch refusal, so token-only case/space mutations were also executed; an overbroad attribute scan incorrectly included fixed module identifiers and was replaced with configured-position checks; a draft output-note fixture failed the enabled module's strict baseline and was discarded. No accepted mapping-removal defect is claimed from that invalid fixture. The final blockers above each have positive assertions and a complete successful reproduction. No source/test was edited and no historical report code was dynamically executed.

## NOT VERIFIED

- No real browser/JavaScript execution, live listener/load test, installed-console smoke test, or browser navigation/unsaved-edit preservation test. Actual generated controls and authenticated handlers were exercised.
- The 18 environment-gated skips remain: capture process-group cases, phase-2/phase-3 opt-in trials and distribution installation. No live provider/platform/audio/capture hardware or moderation calls.
- Independent Save/Remove probes intercept persistence. S/Q cover ordinary real temporary-file persistence, base preservation and atomicity. No destructive base write, exhaustive filesystem race/permission-error/external-writer proof, newly mounted bind alias or case-insensitive filesystem.
- No OS process stuck after SIGKILL was induced; finite bounded doubles and ordinary real subprocess tests ran. No full Check/filesystem/spawn/Apply 60-second total deadline or slow-client deadline established.
- Cross-session reuse was executed as two authenticated sessions with current same-page state; no real tabs were opened. Same-state identities are shared and accepted, so session/tab isolation is not verified as a guarantee.
- No universal absence proof for other defects. The executed stale-token retargeting and valid empty-string omission require changes despite the green suite.

