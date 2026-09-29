# Phase 4 — full-branch closing gate, sixth pass

## VERDICT

REQUEST_CHANGES

Reviewed `main` at **`0955e9e5d78411a447c8264c4b45c66d1339de95`**, cumulatively as **`835d9b4..HEAD`**, against the phase-4 spec, plan, binding brief decision 6c and this gate's requirements. The published gate-5 sibling-key losses and its two exact N3 residual inputs are fixed. **N3 still accepts truncated/internally altered stand-ins literally. A list of mappings can silently substitute one hidden key for another after an ordinary non-secret value edit (MEDIUM N6). Phase 4 cannot close.**

The required suite ran independently:

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/ -q -p no:cacheprovider
```

**S: 3018 passed, 18 skipped in 160.45 s; exit 0.** Bytecode and pytest cache were disabled. Tests used ordinary temporary fixtures. Only this report was created or modified in the repository by this review; no staging or commits. The pre-existing modified campaign summary and untracked run directories were left alone.

### Executed command labels

- **S:** the full suite above, including every named running test below and its parameterizations.
- **E:** `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -`, explicit inspected stdin using gate-5 fixtures and actual authenticated GET/Check/Save handlers. Textareas were HTML-unescaped and JSON-decoded; `_commit` was intercepted. Audio cases used the real checker on baseline-valid enabled audio output. Brain cases exercised the shipped field schema with an observing checker. All requested N5/N3 defeat families ran.
- **P:** the same stdin command for published F1–F5/N1/N2/N4 input families: current layouts from GET, actual path/startup/status guards, dispatch/admission, installed aiohttp body adapter, finite supervisor doubles, typed booleans, parser spies and decoded nested JSON. All write sinks intercepted.
- **D:** the same stdin command for cross-list/key defeats and gate-5 reorder/duplicate/ambiguous/mixed-scalar/substring-marker replays. E/P/D created no configuration files, listeners or children.
- **M:** the same stdin command for AST inventory and `.venv/bin/python -m pytest tests/ --collect-only -q -p no:cacheprovider` (bytecode disabled): all **109 qualified gate-1 checklist test references** exist and collect. Also compared all 17 manifests with `git show 835d9b4:<path>`, removing only schema title/default annotations while preserving properties named `default`.
- **G:** `git diff --stat 835d9b4 HEAD`; `git diff --name-status 835d9b4 HEAD`; `git diff --check 835d9b4 HEAD`; cumulative production/packaging/pre-existing-test diffs and final-source inspection of UI/overlay/runtime configuration, rendering, guards, normalization, persistence, status, Apply and adapter paths. **76 changed files; whitespace check passes.** Integration scan: `rg -n 'load_config\(|check_config\(|canonical_digest\(|_apply_edits\(|Popen|self.popen|\.write\(|write_text\(|os.replace|asyncio.run\(|ui.handle\(' core/config_ui core/overlay.py core/main.py`. This is a full-branch assessment, not per-commit verdicts.

## OPEN ITEMS

### N5 — MEDIUM — PARTIALLY RESOLVED

The published **within-mapping** loss is fixed; different underlying keys across list mappings still share representations and can be restored incorrectly (N6).

**Locations:** `core/config_ui/__init__.py:1144` (per-mapping `_hidden_keys`), `:1178` (value-free `StandInCollision`), `:1218` (rendering), `:1330` (key restoration), `:1362`/`:1389` (list matching/duplication).

**Executed tests under S:** `tests/test_config_ui.py::test_n5_keys_that_only_contain_a_secret_stay_distinct_entries`; `test_n5_legitimate_stand_in_shaped_text_never_collides`; `test_n5_a_single_punctuation_secret_keeps_every_key_distinct`; `test_n5_an_unallocatable_stand_in_fails_the_render_loudly`.

| Replay / defeat — E/D | Executed outcome |
|---|---|
| Gate-5 `{'xq7Z':'one','xsecond-secret':'two'}` | GET decodes as `{'x[hidden]':'one','x[hidden] (2)':'two'}`. Append `added='ok'`: Check passes, Save 303; exact original keys and both values retained. |
| Gate-5 legitimate marker-looking key: `{'xq7Z':'one','x[hidden]':'two'}` | GET keeps both as `x[hidden] (2)` and legitimate `x[hidden]`; same exact successful unrelated-edit round trip. |
| Two keys containing the same secret: `xq7Z`, `yq7Z` | Both entries survive decoded GET, Check and Save after append. |
| Same secret, identical redacted form: `xq7Zq7Z`, `xq7Z[hidden]` | Both reduce to `x[hidden][hidden]`; second is numbered. Exact original key/value round trip succeeds. |
| Key equal to secret plus legitimate full/numbered stand-ins | `q7Z`, `literal value configured (hidden)` and its `(2)` form coexist; secret key takes `(3)`. Legitimate keys and a legitimate stand-in-shaped value round-trip unchanged after unrelated edit. |
| One secret substring of another: `q7Z`, `q7Zmore` | `xq7Z`, `xq7Zmore`, `q7Zmore` remain three distinct displayed entries, and restore exactly after append. |
| List mappings sharing the same underlying redacted key | Two actions with `xq7Z`, values `one`/`two`: reverse and append a field to each; Check/Save retain exact entries and order. |
| List mappings with different underlying keys sharing a displayed key | `xq7Z`/`xsecond-secret` both display `x[hidden]`. Reverse and append a field to both: named unmatched refusal, Check failed, Save 403, zero checker/commit. Change only `one` to `two`: Check passes, Save 303, **first key silently becomes `xsecond-secret`**. **FAIL; N6**. |
| Allocator cannot find a free candidate | `_shown({'a0':1,'a1':2,'a2':3,'a3':4}, frozenset('0123456789'))` raises `StandInCollision('a withheld mapping key has no distinct stand-in')`. Loud, value-free failure, no partial merged output. This proves safe refusal upon this allocator's exhaustion, not mathematical impossibility of every other allocation scheme. |

N5's original sibling-entry disappearance is resolved. The requested cross-list defeat prevents unqualified closure of configured-key identity preservation.

### N3 — MEDIUM — PARTIALLY RESOLVED; OPEN

**Locations:** `core/config_ui/__init__.py:1186` (`_MARKER_TEXTS`), `:1260`/`:1274` (`_is_mask_marker` substring heuristics), `:1327` (unrecognized text accepted), `:1352` (restored-key conflict guard), `:3244` (Check early refusal), `:4091` (shared `_draft_value`).

**Executed tests under S:** `tests/test_config_ui.py::test_n3_an_edited_stand_in_is_refused_never_written`; `test_n3_an_unmatched_placeholder_never_reaches_the_checker`; `test_n3_a_stand_in_beside_its_real_key_is_refused_never_resolved`; `test_n3_check_gives_the_real_checker_verdict_on_the_draft_save_writes`; `test_n3_an_invalid_draft_fails_check_on_its_real_values`; `test_n3_check_and_save_validate_the_same_restored_draft`; `test_n3_an_unmatched_placeholder_fails_check_as_save_refuses_it`.

| Replay / defeat — E/D | Executed outcome |
|---|---|
| Gate-5 exact full stand-in + ` edited`, real enabled audio checker | Check 200/failed, settings phase not reached, Save 403, named `_UNMATCHED_REASON`; zero observing-checker calls and commits. Original residual A fixed. |
| Gate-5 stand-in key plus real `q7Z`, both key orders | Check failed, Save 403, named `_CONFLICTING_REASON`, zero checker/commit; no order-dependent winner. Original residual B fixed. |
| Leading/trailing whitespace, uppercase, doubled internal whitespace | All explicitly refused with `_UNMATCHED_REASON` before checker/commit; Save 403. |
| Renderer-never-produced full stand-in `(999)` | Explicit unmatched refusal; Save 403, zero checker/commit. |
| Truncate full stand-in's final character | Explicit unmatched refusal; Save 403. |
| **Truncate 12 characters** | Produces **`literal value configu`**. Baseline real Check passes; posted real Check passes; Save 303 commits this literal as the final voice. **FAIL**. |
| **One-character internal alteration** | `configured` → `configurex`: **`literal value configurex (hidden)`** passes real Check and Save 303, committed literally. Same edit to a displayed argument key is accepted as a literal new key. **FAIL**. |
| Partial bracket marker `[hidde]` | Real Check passes, Save 303 commits literal text; no unmatched reason. **FAIL**. |
| Original gate-4 `a"b` allowed list + append | Real Check passes; Save 303; both routes receive exact original secret plus append. Original Check/Save divergence remains fixed. |
| Reorder secret/substring entries | Reverse `['kept','q7Z','prefix-q7Z-suffix']`, append: both real routes produce `['prefix-q7Z-suffix','q7Z','kept','added']` exactly. |
| Duplicate exact stand-in with one possible scalar value | Deterministic duplicate restores another `q7Z`; real Check/Save agree and retain both. |
| Remove one of two indistinguishable different-secret scalar stand-ins | Named unmatched refusal, Save 403, no commit; baseline real checker verified with second secret referenced at `${SECOND}` credential. |
| Keep displayed scalar plus real `q7Z` | Both retained as two scalar list items, real Check passes, Save 303; no key collision or lost value. |
| Append `x[hidden]`; ambiguous edited mapping | Named unmatched refusal before checker/commit; Save 403. |
| Different hidden mapping keys across list entries | Ordinary visible value edit can silently change underlying key; **FAIL no-guessing guarantee; N6**. |

Named reasons observed verbatim:

- Unmatched: `a hidden value cannot be matched to exactly one configured entry; edit it in the configuration file`.
- Conflicting: `a hidden key and its configured key were both posted, or two posted keys name one configured key; keep only one of them`.

The predicate recognizes only `[hidden]`, `literal value configured` or `configured (hidden)` after case/space folding. Truncation or one internal character change can eliminate all three, allowing literal validation/Save. The successful suffix/case/space tests do not establish the required altered-stand-in refusal.

**Required:** retain hidden identity independently of editable text, explicitly reject unmatched altered stand-ins before validation/commit, preserve legitimate configured marker-looking text, and avoid guessing list-entry identity. Cover truncation/internal edits and non-secret edits that make different list mappings look alike.

## RE-CONFIRMED

Each line names the command run; green suite tests were supplemented by published-input replays.

- **F1 — RESOLVED; P + S:** `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -` replays literal `~/gate-base.yaml`, existing `/usr/bin/bzip2`/`bzcat` hard links, dot/dot-dot/absolute/environment spellings, `/bin` directory symlink, distinct paths and simulated bind/case fallback: startup/status collisions recognized, current-layout authenticated Save 403, zero commit/replace. S: `test_f1_save_never_replaces_the_base_whatever_the_overlay_spelling`, `test_f1_a_hard_link_of_the_base_refuses_to_start_and_is_never_written`, overlay identity tests. Source: `core/overlay.py:249`, `core/config_ui/__init__.py:3422`, `:4343`.
- **F2 — RESOLVED published disclosure cases; P/E + S:** same stdin command: listed `q7Z`, declared credential without `secrets`, `Ω`, long canary, `rules`/`twitch`/`combination` channel keys, escaped/nested/entity-like secrets, rule IDs and diagnostics all withheld from configured positions after decoding. Structural tokens intact. S: AC35, `test_p19f7_every_published_secret_length_is_still_never_rendered`. Source: `core/config_ui/__init__.py:1201`, `:1563`, `:1630`.
- **F3 — RESOLVED; P + S:** same stdin command: absent audio synthesis probe defaults True at runtime and shows `default: true`; extracted true/false options reach checker and Save as real booleans, Save 303. S: `test_f3_an_unset_true_default_boolean_renders_its_effective_value`, `test_f3_a_boolean_draft_expresses_unset_false_and_true`.
- **F4 — RESOLVED; P + S:** same stdin command: 128 blocked requests / four workers → 12 queued, 16 handled/401, 112 refused/503; actual aiohttp adapter accepts 0/1,048,576 bytes, rejects 1,048,577 with 413, accepts next 1 byte. S: `test_f4_a_saturated_bridge_refuses_honestly_and_queues_nothing_more`, `test_f4_a_failed_body_read_releases_its_admission`. Source: `core/config_ui/__init__.py:4790`, `:4854`, `:4889`.
- **F5 — RESOLVED; P + S:** same stdin command: original finite timeout double → terminate, wait(10.0), kill, wait(5.0); returns False and retains child, no unbounded wait. S's unkillable restart/close and ordinary child tests pass. Source: `core/config_ui/__init__.py:4584`, `:4641`.
- **N1 — RESOLVED; P + S:** same stdin command: secrets `r`, `a`, `e`, `s`, `true`, `false`; HTML-extracted options remain literal true/false, checker/Save receive booleans, Save 303. S's boolean/structural-redaction tests pass. Source: `core/config_ui/__init__.py:2221`.
- **N2 — RESOLVED; P + S:** same stdin command: original first/second channel reorder plus add/remove/rename → Check/Save 409, parser/checker never called. Same-layout/value-only controls still target `first`, Check 200. S's stale-layout tests pass. Source: `core/config_ui/__init__.py:3298`, `:3944`.
- **N4 — RESOLVED; E/P + S:** same stdin command: quote secret + legitimate `['kept','voice-two']`, single-backslash secret + `['kept','ordinary"quote']`, all ten quote/backslash/bracket/brace/comma/colon/slash/newline families nested in keys/values → JSON parses, append restores exact original data, Check passes, Save 303. S: `test_n4_a_punctuation_secret_never_breaks_a_legitimate_list`, `test_n4_punctuation_secrets_in_nested_data_mask_the_data_not_the_syntax`. Source: `core/config_ui/__init__.py:1247`, `:1982`. Fixed punctuation in markup/JSON and markers is not configured secret disclosure.

### Gate-1 requirement map R1–R10 → named running tests

M inventoried every named test below; all ran under **S**. Green tests do not override executed counterexamples. Draft content still fails despite preserved storage/request boundaries.

| Requirement | Result | Named running tests / command |
|---|---|---|
| R1 — separate UI, bind/token/Host/Origin/CSRF guards | PASS | **S**: `tests/test_config_ui.py::test_the_module_entry_point_calls_main`; `tests/test_config_ui.py::test_ac1_a_non_loopback_host_is_refused_before_any_bind`; `tests/test_config_ui.py::test_ac2_each_start_has_a_fresh_256_bit_token`; `tests/test_config_ui.py::test_ac3_every_route_without_a_session_is_refused_without_content`; `tests/test_config_ui.py::test_ac4_a_post_without_or_with_a_wrong_csrf_token_is_refused`; `tests/test_config_ui.py::test_ac4_a_non_post_to_a_state_changing_path_is_405`; `tests/test_config_ui.py::test_ac6_refused_hosts_get_403_even_on_get`; `tests/test_config_ui.py::test_ac6_refused_origins_get_403_and_change_nothing`; `tests/test_config_ui.py::test_ac5_the_socket_patch_is_effective` |
| R2 — shared overlay, precedence, base preservation | PASS storage/path semantics | **S**: `tests/test_overlay.py::test_ac7_merged_document_is_exact_and_inputs_are_unmutated`; `tests/test_overlay.py::test_ac7_overlay_null_replaces_the_base_value`; `tests/test_overlay.py::test_ac9_overlay_below_minimum_is_refused_then_deleting_it_accepts`; `tests/test_overlay.py::test_ac9_run_hands_the_module_the_overlay_value`; `tests/test_overlay.py::test_ac9_explicit_overlay_is_honoured_by_main_and_run`; `tests/test_overlay.py::test_overlay_is_merged_before_environment_resolution`; `tests/test_overlay.py::test_relative_modules_directory_resolves_from_the_base_directory`; `tests/test_overlay.py::test_ac10_bad_overlay_names_the_file_never_the_content` |
| R3 — all titles/defaults and annotation validation | PASS | **S**: `tests/test_manifest_presentation.py::test_presented_covers_every_shipped_manifest`; `tests/test_manifest_presentation.py::test_every_setting_node_has_a_title`; `tests/test_manifest_presentation.py::test_every_documented_default_is_declared`; `tests/test_manifest_presentation.py::test_every_declared_default_validates_against_its_node`; `tests/test_manifest_presentation.py::test_default_violating_the_node_is_refused_as_default`; `tests/test_manifest_presentation.py::test_default_does_not_constrain_validated_values`; `tests/test_manifest_presentation.py::test_property_named_default_is_a_property_not_an_annotation` |
| R4 — generated base/core/module pages and honest controls | FAIL usability: N3/N6 | **S**: `tests/test_config_ui.py::test_ac15_the_base_page_lists_every_module_and_the_origin`; `tests/test_config_ui.py::test_ac16_every_schema_path_is_shown_with_title_help_and_default`; `tests/test_config_ui.py::test_ac17_a_module_added_as_a_directory_gets_its_titled_page`; `tests/test_config_ui.py::test_ac17_no_ui_source_file_names_a_shipped_module`; `tests/test_config_ui.py::test_ac18_unrenderable_nodes_get_notices_and_controls_keep_the_schema`; `tests/test_config_ui.py::test_ac19_the_twitch_page_shows_the_configured_channel_policy`; `tests/test_config_ui.py::test_ac20_the_core_page_renders_every_declared_limit`; `tests/test_config_ui.py::test_ac20_secrets_and_actions_are_read_only_with_every_rule`; `tests/test_config_ui.py::test_ac20_an_uncovered_action_is_listed_until_a_rule_covers_it`; `tests/test_config_ui.py::test_ac21_readiness_combines_the_check_verdict_and_the_running_state` |
| R5 — unsaved Check through existing path, all diagnostics, no side effects | FAIL draft identity/refusal: N3/N6 | **S**: `tests/test_config_ui.py::test_ac38_an_unsaved_invalid_edit_fails_and_writes_nothing`; `tests/test_config_ui.py::test_ac38_an_unsaved_fix_passes_and_writes_nothing`; `tests/test_config_ui.py::test_ac22_unresolved_references_are_all_reported_and_stop_the_check`; `tests/test_config_ui.py::test_ac22_every_invalid_setting_is_reported_in_one_check`; `tests/test_config_ui.py::test_ac23_check_signals_nothing_starts_nothing_and_creates_no_socket`; `tests/test_config_ui.py::test_bridge_check_runs_the_real_checker_from_a_running_loop` |
| R6 — exact writing scope, managed path, atomicity, stale checks, removal | FAIL content integrity: N3/N6; scope/storage PASS | **S**: `tests/test_config_ui.py::test_ac24_each_save_writes_exactly_its_override_and_logs_paths_only`; `tests/test_config_ui.py::test_ac25_remove_override_restores_the_base_value_and_prunes`; `tests/test_config_ui.py::test_ac26_an_invalid_save_returns_a_field_diagnostic_and_writes_nothing`; `tests/test_config_ui.py::test_ac26_a_stale_save_or_remove_is_refused`; `tests/test_config_ui.py::test_ac27_writes_outside_the_scope_or_at_protected_fields_are_refused`; `tests/test_config_ui.py::test_ac27_posted_protected_or_out_of_scope_fields_are_refused`; `tests/test_config_ui.py::test_ac27_hand_written_actions_and_secrets_survive_an_unrelated_save`; `tests/test_config_ui.py::test_ac27_a_save_or_remove_through_a_yaml_alias_leaves_the_other_path_unchanged`; `tests/test_config_ui.py::test_ac27_an_ancestor_save_must_keep_every_protected_descendant`; `tests/test_config_ui.py::test_ac28_an_interrupted_write_leaves_the_previous_overlay_intact`; `tests/test_config_ui.py::test_ac28_the_write_targets_only_the_managed_overlay` |
| R7 — supervised Apply, status publication/classification, drift, bounded result | PASS tested outcomes | **S**: `tests/test_config_ui.py::test_ac29_a_failing_on_disk_check_refuses_and_touches_no_process`; `tests/test_config_ui.py::test_ac30_a_ready_record_of_the_child_with_the_on_disk_digest_is_accepted`; `tests/test_config_ui.py::test_ac30_a_child_exiting_2_is_refused_with_its_status_and_diagnostic`; `tests/test_config_ui.py::test_ac30_a_child_writing_nothing_within_the_window_is_unknown`; `tests/test_config_ui.py::test_ac31_apply_never_signals_a_foreign_record_pid`; `tests/test_config_ui.py::test_ac32_drift_follows_the_overlay_and_the_record`; `tests/test_config_ui.py::test_ac39_ac40_a_child_writing_only_an_unaccepted_record_is_unknown`; `tests/test_config_ui.py::test_ac40_every_listed_mutation_of_v_is_unusable_with_a_value_free_reason`; `tests/test_status_record.py::test_three_transitions_publish_three_records_in_order`; `tests/test_status_record.py::test_the_record_describes_the_loaded_configuration`; `tests/test_status_record.py::test_a_status_path_naming_a_configuration_file_is_refused` |
| R8 — no secret in responses, diagnostics, logs or reports | PASS executed disclosure probes | **S**: `tests/test_config_ui.py::test_ac35_no_secret_value_reaches_any_response_log_report_or_record`; `tests/test_config_ui.py::test_check_diagnostics_pass_the_redaction_guard`; `tests/test_config_ui.py::test_d9_the_refused_tail_is_redacted_and_value_free`; `tests/test_config_ui.py::test_ac24_a_saved_path_naming_a_secret_is_logged_redacted` |
| R9 — dependencies and offline resources | PASS static/test evidence | **S**: `tests/test_main.py::test_pyproject_declares_the_console_script_and_the_build_backend_for_tests`; `tests/test_config_ui.py::test_pages_use_inline_assets_and_relative_targets_only`; `tests/test_config_ui.py::test_r9_module_pages_use_inline_assets_and_relative_targets_only`; `tests/test_config_ui.py::test_ac36_the_url_audit_finds_every_external_target`; `tests/test_config_ui.py::test_ac36_a_configured_url_is_escaped_text_only` |
| R10 — operator documentation | PASS documentation coverage | **S**: `tests/test_config_ui.py::test_ac37_doc_launch_and_local_only`; `tests/test_config_ui.py::test_ac37_doc_overlay_rule_and_merge`; `tests/test_config_ui.py::test_ac37_doc_writable_and_read_only_blocks`; `tests/test_config_ui.py::test_ac37_doc_secrets_policy`; `tests/test_config_ui.py::test_ac37_doc_restart_drift_and_status_file` |

### Gate-1 operator-visible guarantees

- **Writing scope / 6c — PASS boundary; S + P/F1 + G:** five writable blocks; actions/secrets/credentials/references and protected ancestors preserved; managed overlay/base guard holds. N3/N6 violate content integrity inside ordinary writable settings.
- **No secret escapes — PASS executed probes; S AC35 + P/E:** published short/declared/escaped/nested canaries withheld from configured data/names/diagnostics/logs/reports; no universal absence proof.
- **Overlay — PASS storage/merge; S overlay/AC24–AC28 + P/F1:** mapping precedence, scalar/list/null replacement, explicit/implicit CLI/runtime parity, base preservation, removal, stale Save, atomicity. N3/N6 remain draft-content defects.
- **Local-only surface — PASS tested surface; S AC1–AC6/AC41 + P/F4:** loopback startup, token/session, Host/Origin, POST/CSRF, path identity, bounded admission/bytes; no live load claim.
- **One generated page per module — generation PASS, usability FAIL; S AC15–AC20 + M + E/D:** all 17 manifests plus new-directory fixture, labels/defaults/help/notices, zero module HTML; N3/N6 defeat ordinary edits.
- **Check — side effects/bridge/layout PASS, identity/refusal FAIL; S AC22/AC23/AC38 + P/N2 + E/D:** actual checker and shared normalization hold; accepted drafts can still contain edited stand-ins or substituted keys.
- **Apply — PASS tested outcomes; S AC29–AC34/AC39–AC43 + P/F5 + G:** accepted/refused/unknown, loaded-document digest, drift, foreign-pid isolation, bounded child waits/drained stderr; no 60-second total-transaction claim.
- **Dependencies/offline — PASS static/test evidence; S AC36 + G:** runtime dependencies unchanged, only added UI console entry, inline assets/relative targets, no render-time fetch.
- **Operator documentation — PASS coverage; S AC37 + source read:** launch/local-only, overlay/merge/scope/secrets/comments/restart/drift/status, 10-second terminate/5-second kill/60-second post-start window. Broader editor promises need N3/N6 fixes.

### Phases 0–3 regression guarantees

Every named test below ran and passed under **S**. M confirms unchanged operational declarations across all 17 manifests and unchanged module Python implementations.

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

G verifies shared `deep_merge`/`canonical_digest` on unresolved loaded data for runtime publication and UI drift/Apply; shared `_draft_value`/`_apply_edits` for Check/Save; guards before routes; mutation locking; bounded admission before body reading; a shell-free launch with started stderr drain; and managed-overlay/status-only atomic persistence. S's socket-creation fixture covers the real bridge/subprocess tests and passed teardown. M found zero module HTML and zero root local overlays.

All 109 qualified gate-1 checklist tests exist and collect. The obsolete unqualified `test_redaction_skips_values_shorter_than_the_minimum`, mentioned as a vulnerability-pinning assertion in gate 1, was correctly inverted to `test_redaction_covers_values_of_every_length` (`tests/test_config_ui.py:1331`). Existing schema/console-script adaptations preserve constraints and dependency/backend assertions; the documented presence-wait wall-time floor preserves outcome checks.

P19F8 adds meaningful decoded-entry/exact-draft and no-checker/no-write assertions. Its edited-marker parameterization (`tests/test_config_ui.py:6588`) omits truncation and internal substitution. Its new argument mapping helper covers one action, leaving cross-list non-secret-edit identity untested. No newly weakened assertion explains these failures. The previous tautology at `tests/test_config_ui.py:1325` remains excluded from evidence.

## NEW FINDINGS

### N6 — MEDIUM — a non-secret value edit silently changes a hidden key across list entries

**Locations:** `core/config_ui/__init__.py:1144` (per-mapping allocation), `:1362`/`:1372` (list grouping by shown contents), **`:1389`–`:1393`** (duplicate matches reuse one configured member), `:1400` (copies complete original data). Executed **D**, actual GET/Check/Save, shipped schema validation, intercepted commit. This is newly demonstrated in the full-branch review; it is **not established as first introduced by P19F8**.

Collect `q7Z` and `second-secret`. Configure schema-valid brain delivery actions:

```python
[
    {'action': 'x.do', 'arguments': {'xq7Z': 'one'}},
    {'action': 'x.do', 'arguments': {'xsecond-secret': 'two'}},
]
```

Decoded GET uses `x[hidden]` in both mappings, with visible values `one` and `two`. Change **only** the first visible value from `one` to `two`; keep both entries and keys. Check passes and Save returns **303**. Both checker and intercepted commit receive:

```python
[
    {'action': 'x.do', 'arguments': {'xsecond-secret': 'two'}},
    {'action': 'x.do', 'arguments': {'xsecond-secret': 'two'}},
]
```

The first original key `xq7Z` vanished. The edited first mapping equals the second mapping's displayed form; `_restore_list` interprets both as duplicates of that original and copies it twice. This silently chooses identity from editable non-secret contents. Reverse and append `added='ok'` to both instead: Check fails with unmatched reason, Save 403, zero checker/commit. That refusal is safe; the accepted key substitution is the finding.

This is an additional concrete defeat of N5's list-mapping case and N3's no-guessing guarantee. Retain distinct hidden identities across entries or explicitly refuse aliasing; do not infer duplication solely from editable display contents.

### File-free reproduction of the closing blockers

Command: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -`, using the following stdin. Inputs and expected outcomes reproduce E/D. Reads are in memory and commits intercepted. Audio uses the actual checker; the illustrative brain checker observes normalization, while real Save schema validation runs.

```python
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode
import copy, html, json, re
import core.config_ui as u
import core.main as main


def run(module, field, settings, env, edit, real=False):
    base = {'modules_directory': 'builtin',
            'enabled_modules': [module] if real else [],
            'secrets': ['${' + name + '}' for name in env],
            'modules': {module: settings}}
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
        if real:
            assert ui.check().passed
        else:
            ui.checker = lambda *args: (True, [])
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
        body = urlencode({
            'layout': re.search('name="layout" value="([^"]+)"', page).group(1),
            'fingerprint': 'absent', name: json.dumps(edit(copy.deepcopy(shown))),
        }).encode()
        with patch.object(ui, '_commit', return_value=u.WriteResult(u.OUTCOME_SAVED)) as commit:
            checked = ui.handle(u.UIRequest('POST', '/check', {}, headers, body))
            verdict = ui.last_check
            saved = ui.handle(u.UIRequest('POST', '/save', {}, headers, body))
        assert checked.status == 200 and verdict.passed and saved.status == 303
        return commit.call_args.args[0]['modules'][module]


audio = {
    'synthesis': {'endpoint': '', 'model': ''},
    'voices': {'allowed': ['kept', 'q7Z', 'q7Z'], 'default': '${GATE_SECRET}'},
    'outputs': {'o': {'player': {'argv': ['cat']}}}, 'default_output': 'o',
}
for alter in [lambda t: t[:-12], lambda t: t.replace('configured', 'configurex')]:
    draft = run('audio_output', 'voices.allowed', audio, {'GATE_SECRET': 'q7Z'},
                lambda v: [v[0], v[1], alter(v[2])], real=True)
    assert draft['voices']['allowed'] == ['kept', 'q7Z', alter(u.HIDDEN_LITERAL)]
    print('N3 accepted altered stand-in:', draft['voices']['allowed'][-1])

brain = {'delivery': {'actions': [
    {'action': 'x.do', 'arguments': {'xq7Z': 'one'}},
    {'action': 'x.do', 'arguments': {'xsecond-secret': 'two'}},
]}}
def edit_value(v):
    v[0]['arguments']['x[hidden]'] = 'two'
    return v

draft = run('brain', 'delivery.actions', brain,
            {'GATE_SECRET': 'q7Z', 'SECOND': 'second-secret'}, edit_value)
assert draft['delivery']['actions'] == [
    {'action': 'x.do', 'arguments': {'xsecond-secret': 'two'}},
    {'action': 'x.do', 'arguments': {'xsecond-secret': 'two'}},
]
print('N6 accepted silently substituted first key')
```

### Execution accounting

Initial ordinary sandbox startup failed (`bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted`); commands completed through the approved fallback. No action remains blocked by approval review. Explicit inspected probes were used, without dynamically executing historical report blocks.

An initial cross-list exact-round-trip assertion stopped on the actual named refusal; a follow-up observation/positive-assertion run confirmed it and the separately accepted value-edit substitution. An initial real-checker ambiguity fixture had an unreferenced second secret and failed its baseline; final replay references `${SECOND}` at a credential and first verifies a passing baseline. M's initial inventory included an obsolete unqualified vulnerability-demanding test name; corrected inventory verifies all 109 qualified checklist references and audits the replacement separately. These harness corrections neither edit production nor suppress suite failures.

## NOT VERIFIED

- No browser/JavaScript session, live HTTP listener/load test, full navigation/unsaved-edit-preservation test or installed-console smoke test. Exact rendered controls were submitted to actual handlers.
- The 18 environment-gated skips remain: capture process-group cases, phase-2/phase-3 opt-in trials and distribution installation. No live providers/platform calls, audio hardware, capture devices or moderation endpoints.
- Extra Save probes intercept commit/replacement sinks. S covers real temporary-file persistence/base preservation/atomicity. No real base overwrite or exhaustive filesystem race/permission-error/external-writer proof.
- Existing real hard links/directory symlink aliases were exercised; bind-like ancestor/case-fold fallback was simulated, not run on newly mounted or case-insensitive filesystems.
- No OS process stuck after SIGKILL was induced. Finite bounded doubles and S's ordinary process tests ran; no total 60-second Check/filesystem/spawn/full-Apply deadline or slow-client body-read deadline established.
- Brain probes establish schema-valid editor/normalization behavior, not runnable enabled production `x.do` actions. Altered-voice counterexamples independently use the real checker with valid enabled audio output.
- No absolute absence proof for further defects. Executed MEDIUM N3 residuals and N6 key substitution require changes despite the green suite.
