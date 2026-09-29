# Phase 4 — full-branch closing gate, ninth pass

## VERDICT

APPROVE

Reviewed `main` at **9739db62fc985a200446f6091d28ddadb4b2b25d**, cumulatively as **835d9b4..HEAD**, against the phase-4 specification, plan, decision 6c and gates 1–8. The cumulative diff contains **88 changed files**. This judgment covers the whole branch.

**N3, N6 and N8 are RESOLVED.** Both gate-8 failures now meet the same side-effect-free 409 refusal on Check, Save and Remove. The published wrong-target edits and older findings remain fixed. The required suite passes above the previous floor. Phase 4 closes on the executed evidence below.

## Commands and execution

Only this report was created or modified in the repository. No source, test, configuration or historical report was edited; nothing staged or committed. The pre-existing untracked `docs/runs/` directories were left alone. Pytest used its ordinary temporary fixtures; independent probes used in-memory configuration reads and intercepted commits, with no configuration-file writes or listeners. Bytecode and pytest cache were disabled.

- **S:** `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/ -q -p no:cacheprovider` → **3051 passed, 18 skipped in 174.21 s; exit 0**. This is the requested invocation with bytecode writes disabled.
- **Q:** `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/test_config_ui.py tests/test_overlay.py tests/test_status_record.py -q -p no:cacheprovider -k 'test_f1 or test_f2 or test_f3 or test_f4 or test_f5 or test_n1 or stale_layout or test_n4 or p19f9 or test_n3 or test_n5 or p19f5 or p19f10 or p19f11 or key_reorder'` → **148 passed, 367 deselected in 82.56 s; exit 0**.
- **E:** `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -`, inspected stdin probes using gate 8's memory fixture and the actual generated controls/authenticated handlers. Receiver layout, fingerprint, CSRF and controls; observations at parser, checker and commit; Check/Save/Remove response comparisons; production allocation beyond 4096; sessions, tabs, foreign pages, malformed/mixed identities, legitimate continuation. An additional `subprocess.run([sys.executable, '-c', child_script], ...)` creates an authentic identity in a **separate OS Python process**, then submits it to the receiver. No server is launched.
- **P:** the same Python stdin command, independent positive-assertion historical replays: tilde and existing hard-link collisions; short/declared/escaped/nested secrets; default-true and short-secret booleans; all four positional layout mutations; real enabled audio checker/draft equivalence; JSON punctuation; colliding hidden keys; gate-6 visible edits/reversal/append/removal; gate-7 Remove/Save; empty/scalar operations; 128-request saturation and bounded supervisor double.
- **B:** the same Python stdin command, gate 2's inspected `test_gate2_body_limit`, with installed aiohttp's actual body reader and the production serving adapter intercepted before binding → **0/1048576/1048577/1 bytes: 200/200/413/200**.
- **M:** the same Python stdin command, AST inventory plus `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/ --collect-only -q -p no:cacheprovider` → **3069 tests collected in 0.40 s; exit 0**. All **108 distinct fully qualified test references before NEW FINDINGS in gate 1** exist and collect. Manifest comparison uses `git show 835d9b4:<path>`; operational declarations of all 17 manifests are unchanged after removing only schema title/default annotations, retaining properties literally named default. Module Python implementation diff is empty.
- **G:** `git diff --stat 835d9b4..HEAD`; `git diff --name-status 835d9b4..HEAD`; cumulative source/test/schema/packaging/scaffolding diffs, final-source and caller/write-sink inspection. `git diff --check 835d9b4..HEAD` reports only the historical blank line at EOF in `gate-report-7.md:292`, left untouched.
- **I:** `git check-ignore --no-index -v -- config.local.yaml config.yaml.status.json presence.local.yaml` → **exit 0**, output below. M also compares old ignore bytes using `git show d04ddab:.gitignore`, and runs `git ls-files -z` through `git check-ignore --no-index -z --stdin`: **no tracked path is ignored**.

The initial sandbox wrapper failed before running a command (`bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted`); the approved execution fallback completed the checks. No approval rejection remains.

Probe corrections stayed in stdin: compare exact bodies within the same receiver/session rather than across different CSRF-bearing pages; use the explicit operation only on a withheld entry; retain a referenced credential in baseline-valid real-checker fixtures; distinguish an unrelated existing-key save from a layout-changing key addition. Empty voice names are diagnosed by the real audio checker, while schema-only Save accepts them; the checker draft and commit were explicitly compared, rather than claiming such a voice passes module validation. Final successful replays supersede those harness assumptions.

## OPEN ITEMS

### N6 — MEDIUM — RESOLVED

**Locations:** `core/config_ui/__init__.py:1340` (tag dimensions), `:2122` (typed real path/page/value HMAC identity), `:2684` (startup nonce and counter), `:2768` (locked monotonic production allocator), `:2794` (shared current-render membership predicate), `:3712`, `:3916`, `:3931` (Check/Save/Remove callers), `:4382` (one refusal diagnostic).

The allocator increments a process-instance counter under the session lock and prefixes it with a startup nonce; it keeps no evictable issued-tag history. The identity additionally authenticates the render, page, typed actual top path, relative path and value with the instance field key; configured keys/values are absent from its text.

The decision is membership among identities generated for the render this session currently shows on this page over the current configuration. A remembered old tag is never needed. All three HTTP identity consumers call `_stale_submission` before parsing or applying their payload, and use `_write_response` for its refusal. Apply consumes no field identities. The trusted parser helper returns edits/problems, not an HTTP status.

**Executed named tests, S/Q:** `test_p19f11_no_render_tag_or_identity_is_ever_reissued`; `test_p19f11_gate8_a_foreign_process_identity_with_current_guards_is_refused`; `test_p19f11_gate8_an_identity_past_the_old_history_bound_stays_refused`; `test_p19f11_every_route_refuses_a_non_current_identity_identically`; gate-7 `test_p19f10_gate7_an_old_identity_after_remove_and_save_is_refused_never_retargeted`; older-render/other-session P19F10 tests; gate-6 P19F9 exact-key tests.

In the table, **uniform409** means Check/Save/Remove all return 409 with identical content type and body for that receiver/session and payload, containing “Refused: stale page” and a reload diagnostic. Identity refusals run no parser, checker or commit; no partial edit/removal occurs. Layout refusals use the layout diagnostic consistently.

| Gate-8 replay / defeat attempt | Executed outcome |
|---|---|
| Authentic foreign UI identity with receiver's current layout, fingerprint, CSRF and current controls | **uniform409**, parser0/checker0/commit0. E executes independent instances and a separate OS Python child. Receiver's own form subsequently saves 303 exactly. |
| Genuine old identity after exercising the production allocator beyond its old 4096 history bound | **uniform409** after **8193 allocations**, parser0/checker0/commit0; current form saves 303. No issued-tag history exists. E and S/Q. |
| Allocation reuse/concurrency defeat | E: 8193 allocations across four threads/three sessions yield 8193 distinct tags and contiguous counters 1–8193. S/Q additionally allocate 8192 per instance across three sessions, two independent instances, and check identities from real GETs. No repeated identity observed. |
| Older render of same page, unchanged data | **uniform409**; fresh tokens differ. Current form still saves. E, S/Q. |
| Other authenticated session, with and without a page render | **uniform409** in both cases. Its GET does not retire the original session's current render; the original session's legitimate continuation succeeds. E, S/Q. |
| Other tab with same session cookie | Second same-page GET retires the first form; **uniform409** for its identities with current guards. New tab's current form saves exactly. This is the required refusal, not a wrong-target edit. E. |
| Whole foreign form, including its foreign layout | **uniform409** via keyed layout refusal. E separately tests receiver guards above so this cannot conceal failed identity classification. |
| Re-render after unrelated model save | Earlier list identities **uniform409**; new current no-op returns 200 with no commit. E. |
| Current form after another session GET, a different page GET and unrelated existing-key save, without re-rendering this page | Valid Check → Save succeeds and keeps both edits. E. An actual key-layout change instead requires reload, as N2 requires. |
| Mixed remembered stale/current identities | Whole request **uniform409**; valid current changes never partially apply. E, S/Q. |
| Foreign-page identity beside current recipient controls/guards | **uniform409** with genuine brain-action tokens posted to audio output. E exercises this explicitly; it does not rely on the new test's optional foreign-page branch. |
| Current-render identity whose entry changed on disk, with current fingerprint | **uniform409**, no checks/writes/removal. E, S/Q. |
| Gate-7 Remove first → add second with identical keyword list → current GET → old kept-entry identity | Remove/add each 303; old/current identity differs; **uniform409** for old identity. Current second-entry identity then changes only second's kept value to edited. P, S/Q. |
| Gate-6 xq7Z:one / xsecond-secret:two, change first visible value to two | Check200/passed, Save303; first keeps xq7Z, second keeps xsecond-secret; checker draft equals commit. P, S/Q. |
| Different hidden keys with same/different visible values; same hidden key with same/different values | All four P combinations edit the first only, retaining both original keys and second value. |
| Reverse actions and append to each; then remove one | Check/Save retain exact keys/order/new fields, then remove only intended action. P, S/Q. |

### N3 — MEDIUM — RESOLVED

**Locations:** shared refusal `core/config_ui/__init__.py:2794`; shared draft paths `:4564`, `:4180`, `:4571`; patch parser `:3420`, `:3524`; callers `:3712`, `:3916`, `:3931`.

**Executed tests, S/Q/E/P:** all `test_n3_*`, `test_p19f9_*` and P19F10/P19F11 identity regressions named above, including the real-checker exact-draft test, invalid-draft test, altered stand-ins, real-key conflict, duplicate, removal/edit conflict and legitimate marker text.

| Gate-8 N3 replay / defeat attempt | Executed outcome |
|---|---|
| Gate-4 enabled audio output, secret a"b, allowed kept/secret, referenced default, append added | Baseline real checker passes; Check200/passed, Save303; actual checker draft equals commit containing exactly kept/a"b/added. P and S/Q. |
| Hidden values in longer text; reorder/remove/append | S/Q exact-draft families pass; P reversal/removal retains actual keys. |
| Legacy whole withheld list with marker truncated by 12, configured → configurex, partial [hidde], or suffixed marker | P executes all four: Check200/failed, Save403, no settings checker or commit; payload cannot reconstruct a withheld value. S/Q regressions pass. |
| Stand-in beside actual key in either insertion order | S/Q refuse the whole legacy ambiguous submission; neither key order selects a winner, no writes. |
| Identity shortened by 1/12, token case changed, space padded, internal characters rearranged, all zero; unknown operation/empty/malformed patch identity | E executes ten variants: **uniform409** on all three routes, parser0/checker0/commit0. S/Q altered-token tests pass. |
| Duplicated current identity | Explicit duplicate refusal; Check200/failed, Save403; checker0/commit0. E and S/Q. This is a conflicting payload using a current identity, not a non-current-identity refusal. |
| Remove and edit the same current entry | Explicit whole-payload refusal; Check200/failed, Save403; checker0/commit0. E and S/Q. Same distinction as above. |
| Legitimate punctuation/marker-looking replacement or append | Exact intended typed data accepted; S/Q legitimate-marker families and P ordinary scalar marker values pass. |
| Old full form after append/value mutation | S/Q refuse superseded exact place/value identities. E confirms changed-entry and unrelated re-render refusals. |
| Original positional stale layout after channel reorder/add/remove/rename | P executes all four: identical 409 responses across Check/Save/Remove, parser0/checker0/commit0; S/Q base/overlay reorder tests pass. |
| Gate-7 old identity equality after Remove/Save | Distinct identities and **uniform409**, current target edits exactly; P and S/Q. |
| Foreign process/page/evicted identity with receiver's current guards | **uniform409**, covering both gate-8 residuals; E/P/S/Q. |

### N8 — LOW — RESOLVED

**Locations:** `.gitignore:16` (comment), `:18` (`*.local.yaml`), `:19` (`*.status.json`); `tests/test_hygiene.py:762` (`test_n8_the_generated_overlay_and_status_files_are_ignored`, ran under S).

Executed **I**, exit 0, new output:

```text
.gitignore:18:*.local.yaml	config.local.yaml
.gitignore:19:*.status.json	config.yaml.status.json
.gitignore:18:*.local.yaml	presence.local.yaml
```

M verifies the complete pre-fix ignore contents remain a byte-for-byte prefix of the current file. `git diff 835d9b4..HEAD -- .gitignore` only appends the comment and required patterns. The tracked-path audit returns an empty set; the patterns hide no file the project currently tracks. No representative operator file was created.

## RE-CONFIRMED

Command labels refer to the complete executed commands above; each published counterexample was replayed through current rendered controls where the former masked whole-text editor has been superseded.

- **F1 RESOLVED — S + Q + P + G:** literal ~/gate-base.yaml startup collision and current-guard authenticated Save403/commit0; real /usr/bin/bzip2–bzcat hard-link startup refusal; path-alias and final-sink tests pass. `core/overlay.py:249`; `core/config_ui/__init__.py:3827`, `:4812`.
- **F2 RESOLVED — S + Q + P:** q7Z ordinary value, declared gate-secret-channel-97bf reused as channel key, configured keys spelled rules/twitch/combination, Ω and escaped quote/backslash/newline nested keys/values are withheld; configured attribute hooks remain positional/opaque. `core/config_ui/__init__.py:1107`, `:1183`, `:2122`; running `test_f2_*` and AC35.
- **F3 RESOLVED — S + Q + P:** unset probe runtime default True and displayed default true; explicit false/true reach the actual checker and commit as booleans, plus unset no-op parameterization. `core/config_ui/__init__.py:2462`; `test_f3_a_boolean_draft_expresses_unset_false_and_true`.
- **F4 RESOLVED — S + Q + P + B:** published 128 finite blocked requests yield 12 queued/16 handled401/112 busy503, admission restored; actual body limits give 200/200/413/200. `core/config_ui/__init__.py:5253`, `:5329`, `:5366`; saturation/failed-read tests pass.
- **F5 RESOLVED — S + Q + P:** published timeout double now terminate → wait(10.0) → kill → wait(5.0); stop False, child retained; Apply/close tests retain ownership and refuse replacement. `core/config_ui/__init__.py:5053`; `test_f5_*`.
- **N1 RESOLVED — S + Q + P:** r/a/e/s/true/false secrets preserve option tokens; real checker receives bools; all named short-secret boolean/enum families pass. `core/config_ui/__init__.py:2462`; `test_n1_*`.
- **N2 RESOLVED — S + Q + P:** original reorder/add/remove/rename positional-field cases give identical 409, parser/checker/commit0. `core/config_ui/__init__.py:2794`, `:4431`; `test_ac24_a_key_reorder_makes_a_rendered_check_stale_like_save`.
- **N4 RESOLVED — S + Q + P:** quote-only/backslash-only secrets leave the legitimate JSON lists decodable and appendable; nested punctuation families pass with exact values. `core/config_ui/__init__.py:1229`, `:2097`; both named `test_n4_*`.
- **N5 RESOLVED — S + Q + P:** original xq7Z/xsecond-secret mapping and legitimate x[hidden] key retain all entries after append; same-secret/identical-shown/full-numbered marker variants preserve exact keys; loud collision exhaustion test passes. `core/config_ui/__init__.py:1146`, `:2158`; `test_n5_*`.
- **N7 RESOLVED — S + Q + P/E:** baseline-valid enabled audio model q7Z → explicit set empty produces actual Check200/passed, Save303, model exactly empty; operation semantics and clear/removal tests pass. `core/config_ui/__init__.py:2432`, `:2452`, `:4133`; `test_p19f10_gate7_n7_a_withheld_ordinary_scalar_can_be_set_to_the_empty_string` and the P19F10 families.

### N7 gate-8 defeat table re-confirmation

| Replay / defeat | Executed outcome / command |
|---|---|
| Ordinary withheld scalar set empty, configured (hidden), [hidden], three spaces, equal to base q7Z | P/E actual enabled checker passes; Save303; exact intended value, including a base-equal explicit override. |
| Set equal to current overlay q7Z | P/E Check200/passed, Save200/Nothing to save, zero new commit. |
| Genuinely unchanged ordinary field; untouched whole patch editor | S/Q no draft edit, no-op Save200; E current form continuation/no-op passes. |
| Clear optional withheld overlay model | P/E actual checker draft {}, Save303 writes {}, base effective again; S/Q also keep hidden-key neighbors on optional mapping clear. |
| Remove list entry with hidden neighbor | P/E exact intended entry removal and unchanged neighbors; S/Q removal regressions pass. |
| Set withheld list member empty | P/E real checker receives exactly kept/empty/xq7Zy and correctly diagnoses invalid voice name; Save303 transports that same schema-valid draft. S/Q schema checker regression asserts exact empty member. This is no longer an unexpressible edit. |
| Typed text with unchanged/clear, invalid remove/SET operation | S/Q explicit whole-payload refusal, no commit. |
| Forged clear on patch/base-only/protected/reference/credential/inside replacing rule list | S/Q clearability and writing-protection regressions refuse it; no scope widening. |

### Gate-1 requirement map R1–R10 → named running tests

**S reruns every named test below; M verifies collection.** All ten requirements pass their published counterexamples and current running evidence. AC1–AC43 are covered by S, including the added status reader/publisher cases; AC11 is the full regression suite and AC12–AC14 include manifest annotation tests.

| Requirement | Result | Named running tests / command |
|---|---|---|
| R1 — separate UI, bind/token/Host/Origin/CSRF guards | PASS | S: `tests/test_config_ui.py::test_the_module_entry_point_calls_main`; `tests/test_config_ui.py::test_ac1_a_non_loopback_host_is_refused_before_any_bind`; `tests/test_config_ui.py::test_ac2_each_start_has_a_fresh_256_bit_token`; `tests/test_config_ui.py::test_ac3_every_route_without_a_session_is_refused_without_content`; `tests/test_config_ui.py::test_ac4_a_post_without_or_with_a_wrong_csrf_token_is_refused`; `tests/test_config_ui.py::test_ac4_a_non_post_to_a_state_changing_path_is_405`; `tests/test_config_ui.py::test_ac6_refused_hosts_get_403_even_on_get`; `tests/test_config_ui.py::test_ac6_refused_origins_get_403_and_change_nothing`; `tests/test_config_ui.py::test_ac5_the_socket_patch_is_effective` |
| R2 — shared overlay, precedence, base preservation | PASS | S: `tests/test_overlay.py::test_ac7_merged_document_is_exact_and_inputs_are_unmutated`; `tests/test_overlay.py::test_ac7_overlay_null_replaces_the_base_value`; `tests/test_overlay.py::test_ac9_overlay_below_minimum_is_refused_then_deleting_it_accepts`; `tests/test_overlay.py::test_ac9_run_hands_the_module_the_overlay_value`; `tests/test_overlay.py::test_ac9_explicit_overlay_is_honoured_by_main_and_run`; `tests/test_overlay.py::test_overlay_is_merged_before_environment_resolution`; `tests/test_overlay.py::test_relative_modules_directory_resolves_from_the_base_directory`; `tests/test_overlay.py::test_ac10_bad_overlay_names_the_file_never_the_content` |
| R3 — all titles/defaults and annotation validation | PASS | S: `tests/test_manifest_presentation.py::test_presented_covers_every_shipped_manifest`; `tests/test_manifest_presentation.py::test_every_setting_node_has_a_title`; `tests/test_manifest_presentation.py::test_every_documented_default_is_declared`; `tests/test_manifest_presentation.py::test_every_declared_default_validates_against_its_node`; `tests/test_manifest_presentation.py::test_default_violating_the_node_is_refused_as_default`; `tests/test_manifest_presentation.py::test_default_does_not_constrain_validated_values`; `tests/test_manifest_presentation.py::test_property_named_default_is_a_property_not_an_annotation` |
| R4 — generated base/core/module pages and honest controls | PASS | S: `tests/test_config_ui.py::test_ac15_the_base_page_lists_every_module_and_the_origin`; `tests/test_config_ui.py::test_ac16_every_schema_path_is_shown_with_title_help_and_default`; `tests/test_config_ui.py::test_ac17_a_module_added_as_a_directory_gets_its_titled_page`; `tests/test_config_ui.py::test_ac17_no_ui_source_file_names_a_shipped_module`; `tests/test_config_ui.py::test_ac18_unrenderable_nodes_get_notices_and_controls_keep_the_schema`; `tests/test_config_ui.py::test_ac19_the_twitch_page_shows_the_configured_channel_policy`; `tests/test_config_ui.py::test_ac20_the_core_page_renders_every_declared_limit`; `tests/test_config_ui.py::test_ac20_secrets_and_actions_are_read_only_with_every_rule`; `tests/test_config_ui.py::test_ac20_an_uncovered_action_is_listed_until_a_rule_covers_it`; `tests/test_config_ui.py::test_ac21_readiness_combines_the_check_verdict_and_the_running_state` |
| R5 — unsaved Check through existing path, all diagnostics, no side effects | PASS | S: `tests/test_config_ui.py::test_ac38_an_unsaved_invalid_edit_fails_and_writes_nothing`; `tests/test_config_ui.py::test_ac38_an_unsaved_fix_passes_and_writes_nothing`; `tests/test_config_ui.py::test_ac22_unresolved_references_are_all_reported_and_stop_the_check`; `tests/test_config_ui.py::test_ac22_every_invalid_setting_is_reported_in_one_check`; `tests/test_config_ui.py::test_ac23_check_signals_nothing_starts_nothing_and_creates_no_socket`; `tests/test_config_ui.py::test_bridge_check_runs_the_real_checker_from_a_running_loop` |
| R6 — exact writing scope, managed path, atomicity, stale checks, removal | PASS | S: `tests/test_config_ui.py::test_ac24_each_save_writes_exactly_its_override_and_logs_paths_only`; `tests/test_config_ui.py::test_ac25_remove_override_restores_the_base_value_and_prunes`; `tests/test_config_ui.py::test_ac26_an_invalid_save_returns_a_field_diagnostic_and_writes_nothing`; `tests/test_config_ui.py::test_ac26_a_stale_save_or_remove_is_refused`; `tests/test_config_ui.py::test_ac27_writes_outside_the_scope_or_at_protected_fields_are_refused`; `tests/test_config_ui.py::test_ac27_posted_protected_or_out_of_scope_fields_are_refused`; `tests/test_config_ui.py::test_ac27_hand_written_actions_and_secrets_survive_an_unrelated_save`; `tests/test_config_ui.py::test_ac27_a_save_or_remove_through_a_yaml_alias_leaves_the_other_path_unchanged`; `tests/test_config_ui.py::test_ac27_an_ancestor_save_must_keep_every_protected_descendant`; `tests/test_config_ui.py::test_ac28_an_interrupted_write_leaves_the_previous_overlay_intact`; `tests/test_config_ui.py::test_ac28_the_write_targets_only_the_managed_overlay` |
| R7 — supervised Apply, status publication/classification, drift, bounded result | PASS | S: `tests/test_config_ui.py::test_ac29_a_failing_on_disk_check_refuses_and_touches_no_process`; `tests/test_config_ui.py::test_ac30_a_ready_record_of_the_child_with_the_on_disk_digest_is_accepted`; `tests/test_config_ui.py::test_ac30_a_child_exiting_2_is_refused_with_its_status_and_diagnostic`; `tests/test_config_ui.py::test_ac30_a_child_writing_nothing_within_the_window_is_unknown`; `tests/test_config_ui.py::test_ac31_apply_never_signals_a_foreign_record_pid`; `tests/test_config_ui.py::test_ac32_drift_follows_the_overlay_and_the_record`; `tests/test_config_ui.py::test_ac39_ac40_a_child_writing_only_an_unaccepted_record_is_unknown`; `tests/test_config_ui.py::test_ac40_every_listed_mutation_of_v_is_unusable_with_a_value_free_reason`; `tests/test_status_record.py::test_three_transitions_publish_three_records_in_order`; `tests/test_status_record.py::test_the_record_describes_the_loaded_configuration`; `tests/test_status_record.py::test_a_status_path_naming_a_configuration_file_is_refused` |
| R8 — no secret in responses, diagnostics, logs or reports | PASS | S: `tests/test_config_ui.py::test_ac35_no_secret_value_reaches_any_response_log_report_or_record`; `tests/test_config_ui.py::test_check_diagnostics_pass_the_redaction_guard`; `tests/test_config_ui.py::test_d9_the_refused_tail_is_redacted_and_value_free`; `tests/test_config_ui.py::test_ac24_a_saved_path_naming_a_secret_is_logged_redacted` |
| R9 — dependencies and offline resources | PASS | S: `tests/test_main.py::test_pyproject_declares_the_console_script_and_the_build_backend_for_tests`; `tests/test_config_ui.py::test_pages_use_inline_assets_and_relative_targets_only`; `tests/test_config_ui.py::test_r9_module_pages_use_inline_assets_and_relative_targets_only`; `tests/test_config_ui.py::test_ac36_the_url_audit_finds_every_external_target`; `tests/test_config_ui.py::test_ac36_a_configured_url_is_escaped_text_only` |
| R10 — operator documentation | PASS | S: `tests/test_config_ui.py::test_ac37_doc_launch_and_local_only`; `tests/test_config_ui.py::test_ac37_doc_overlay_rule_and_merge`; `tests/test_config_ui.py::test_ac37_doc_writable_and_read_only_blocks`; `tests/test_config_ui.py::test_ac37_doc_secrets_policy`; `tests/test_config_ui.py::test_ac37_doc_restart_drift_and_status_file` |

### Gate-1 operator-visible guarantees

- **Writing scope / 6c PASS — S AC24–AC28 + P/F1 + G:** exactly the five writable blocks; protected actions/secrets/credentials/references/ancestors; managed-overlay-only writes, base preservation and exact intended targets.
- **No secret escapes, published cases PASS — S AC35 + Q + P/F2:** values of all tested lengths/encodings/positions withheld; configured keys absent from attributes/identities; diagnostics/logs/reports/status canaries pass.
- **Overlay PASS — S overlay/AC24–AC28 + P/F1/N3/N7:** shared recursive merge, list/scalar/null replacement, implicit/explicit runtime and Check paths, atomic replacement, override removal/base restoration.
- **Local-only surface PASS — S AC1–AC6/AC41 + P/F4 + B:** loopback default, token/session/Host/Origin/POST/CSRF, collision refusal before binding, bounded admission/body retention.
- **One generated page per module PASS — S AC15–AC21 + M + P:** all 17 manifests and added-directory fixture; labels/defaults/help/notices/origin/readiness; no module HTML or hard-coded shipped-module UI.
- **Check PASS — S AC22/AC23/AC38 + P/N2/N3 + E:** actual checker bridge and diagnostics, exact intended draft, zero writes/signals/spawn/socket; stale identities uniformly refused before parser/checker.
- **Apply PASS within documented bounds — S AC29–AC34/AC39–AC43 + P/F5 + G:** accepted/refused/unknown, supervised-child-only signals, loaded-document status/digest, disk drift, stderr drain and finite child waits.
- **Dependencies/offline PASS — S AC36 + G:** runtime dependencies remain aiohttp>=3.9,<4 and PyYAML>=6,<7; only UI console entry added; inline assets/relative targets, no render-time fetch.
- **Operator documentation PASS — S AC37 + G:** launch/local-only, path/merge rules, writable/read-only blocks, secret policy, restart/drift/status, bounded 10/5-second waits and post-start acceptance window.

### Phases 0–3 regression guarantees

Each row ran under **S**; M confirms operational manifest declarations and module implementations remain unchanged.

| Guarantee | Running evidence — command S |
|---|---|
| Default-deny authorization, including reads | `tests/test_actions.py::test_zero_rules_refuse_a_read_and_a_write_with_zero_invocations`; `tests/test_actions.py::test_a_read_rule_authorizes_the_read_only` |
| Bounded admission and retention | `tests/test_admission.py::test_full_session_queue_and_global_cap_reject_with_reason_and_depth`; `tests/test_admission.py::test_three_simultaneous_sessions_never_exceed_the_worker_limit`; `tests/test_retention.py::test_ac20_bus_history_byte_limit_evicts_before_the_count_limit_without_failing_a_publication`; `tests/test_retention.py::test_ac30_byte_cap_leaves_fewer_than_three_exchanges_and_at_most_two_hundred_bytes` |
| Explicit terminal outcomes | `tests/test_actions.py::test_the_six_terminal_statuses_are_produced_exactly`; `tests/test_admission.py::test_total_deadline_after_emission_is_classified_external_unknown_once` |
| One global startup/shutdown deadline | `tests/test_main.py::test_a_hanging_activation_is_bounded_by_the_startup_deadline`; `tests/test_main.py::test_a_hanging_settings_validator_is_bounded_by_the_startup_deadline`; `tests/test_lifecycle.py::test_one_global_shutdown_deadline_caps_the_sum_of_local_timeouts`; `tests/test_shutdown.py::test_cli_deadline_covers_cancellation_resistant_task` |
| Runtime secret redaction | `tests/test_main.py::test_configured_secrets_are_redacted_from_traces_and_loss_diagnostics_through_the_entry_point`; `tests/test_main.py::test_declared_credentials_are_redacted_without_a_secrets_entry_through_the_entry_point` |
| Phase 2 R10 deadline ruling | `tests/test_audio_input.py::test_the_only_deadline_subtractions_are_the_two_ac41_authorizes`; `tests/test_audio_output.py::test_the_stop_grace_runs_after_the_stop_never_before_the_deadline`; `tests/test_audio_input.py::test_the_recorder_is_still_killed_at_the_deadline_itself_with_transcription` |
| No default provider/model | `tests/test_audio_output.py::test_an_empty_endpoint_sends_no_request_and_leaves_speak_unbound`; `tests/test_hygiene.py::test_ac45_core_and_modules_name_no_model`; M's phase-4 manifest comparison confirms no operational provider declarations changed |
| Moderation modes, strictest default, no permanent bans | `tests/test_moderation.py::test_ac24_the_default_mode_alerts_with_no_platform_request_and_one_fact`; `tests/test_moderation.py::test_ac26_a_request_in_mode_propose_yields_a_proposal_id_and_no_request`; `tests/test_moderation.py::test_ac26_auto_apply_applies_a_delete_at_once_under_the_strict_rules`; `tests/test_moderation.py::test_no_operation_removes_a_viewer_permanently` |
| Viewer-memory bounds and eviction | `tests/test_viewer_memory.py::test_ac14_eviction_deletes_the_least_recent_then_least_used_with_one_fact`; `tests/test_viewer_memory.py::test_ac15_the_total_bound_holds_and_the_target_is_never_deleted`; `tests/test_viewer_memory.py::test_ac18_the_bounds_hold_before_readiness`; entire viewer-memory suite passes |
| Requested-only watch by default | `tests/test_watch.py::test_ac29_without_activation_no_tick_is_emitted`; `tests/test_watch.py::test_ac29_a_viewers_start_command_starts_nothing`; `tests/test_watch.py::test_ac29_the_broadcasters_start_command_ticks_every_interval` (explicit startup activation remains supported) |
| Honest platform capabilities | `tests/test_moderation.py::test_ac27_delete_message_on_kick_is_platform_unsupported_with_no_request`; `tests/test_clips.py::test_ac12_with_twitch_and_kick_clips_are_ready_for_twitch_and_unsupported_on_kick`; `tests/test_youtube.py::test_the_moderation_service_offers_delete_and_timeout_and_no_clip_or_poll` |

### Cumulative integration and assertion audit

G follows UI/runtime overlay loading, shared deep_merge/canonical_digest, loaded unresolved-document publication versus on-disk drift, shared draft normalization/application, typed path identities, guard/dispatch/lock order, scope/protected descendants, final atomic sinks, subprocess supervision and packaging. The branch's scaffolding/documentation changes do not alter the runtime authorization rules.

The two allowlisted assertion adaptations preserve users-schema constraints and the exact two console scripts/dependencies/backend. The presence wait's 30-second wall-time floor retains its completion predicate/assertions. Superseded masked-editor tests now drive real rendered controls and assert real draft/commit values and unchanged snapshots. P19F11 changes old 200-failed/403 expectations to 409/stale and retains zero checker/write evidence; the uniformity test also compares response status/content type/body and zero parser calls. No assertion was weakened to permit a non-current identity. The historical tautological `or True` assertion at `tests/test_config_ui.py:1325` is excluded from evidence.

## NEW FINDINGS

**None established by executed reproduction.**

Attempted allocator reuse, concurrent allocation, cross-process/page/session/tab borrowing, malformed/recombined tokens, mixed stale/current controls, current-entry changes, duplicate/remove-edit conflicts, legitimate Check → Save continuation, parallel independent sessions/different pages, clear/empty/no-op operations, prior secret/key/boolean failures, and accidental tracked-file ignore. Outcomes are recorded above. Same-cookie same-page second rendering deliberately retires the first render, as required; the current form remains usable.

## Reproducible core residual replay

Command: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -` with the following stdin. This uses the inspected fixture definitions from the published gate-8 report; it does not execute that report's obsolete failure assertions or write files. It covers the two blocking cases on all three routes, plus a legitimate current Save.

```python
import copy, hashlib, json, re, runpy, sys, subprocess
from contextlib import contextmanager, ExitStack
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode
import core.config_ui as u
import core.main as main

t = runpy.run_path('tests/test_config_ui.py')
parse = t['_patch_editor']
browser = t['_browser']
submit = t['_submitted']
replace = t['_replaced']
source = Path('docs/campaigns/phase4/scaffolding/gate-report-8.md').read_text()
defs = source[source.index('@contextmanager\ndef fixture'):
              source.index('# Authentic foreign identity')]
exec(compile(defs, '<inspected gate8 fixture>', 'exec'))
audio = {
    'synthesis': {'endpoint': '', 'model': 'q7Z'},
    'voices': {'allowed': ['kept', 'q7Z'], 'default': '${GATE_SECRET}'},
    'outputs': {'o': {'player': {'argv': ['cat']}}}, 'default_output': 'o',
}

def refusal(ui, post, st, fields):
    before = copy.deepcopy(st['overlay'])
    n, seen = len(st['writes']), len(st['seen'])
    with patch.object(ui, 'parse_edits', wraps=ui.parse_edits) as parsed:
        responses = [post(fields, route=r)
                     for r in ('/check', '/save', '/remove')]
        assert not parsed.called
    assert len({(r.status, r.content_type, r.body) for r in responses}) == 1
    assert responses[0].status == 409
    assert 'Refused: stale page' in responses[0].body.decode()
    assert 'reload' in responses[0].body.decode()
    assert st['overlay'] == before
    assert len(st['writes']) == n and len(st['seen']) == seen

with fixture('audio_output', audio, {'GATE_SECRET': 'q7Z'}) as f:
    ui, base, st, headers, get, post, login = f
    foreign = u.ConfigUI(u.UISettings(ui.base_path), environ=ui.environ)
    remote = parse(get(headers=login(foreign), target=foreign), '.allowed')
    current = parse(get(), '.allowed')
    refusal(ui, post, st, [
        *browser(current), *submit(remote, replace(remote['entries'][1], '')),
        ('path', 'modules.audio_output.voices.allowed'),
    ])
    print('foreign: identical409, parser0/checker0/commit0')

with fixture('audio_output', audio, {'GATE_SECRET': 'q7Z'}) as f:
    ui, base, st, headers, get, post, login = f
    old = parse(get(), '.allowed')
    session = ui._session(u.UIRequest('GET', '/', {}, headers))
    tags = [ui._new_render(session, '/module/audio_output') for _ in range(8193)]
    assert len(set(tags)) == 8193 and not hasattr(ui, '_render_tags')
    current = parse(get(), '.allowed')
    refusal(ui, post, st, [
        *browser(old), ('path', 'modules.audio_output.voices.allowed'),
    ])
    fields = submit(current, t['_allowed_changes'](current, append='added'))
    checked, saved = pair(post, fields)
    assert checked.status == 200 and ui.last_check.passed and saved.status == 303
    assert st['overlay']['modules']['audio_output']['voices']['allowed'] == [
        'kept', 'q7Z', 'added',
    ]
    print('old8193: identical409; current legitimate edit303')
```

## NOT VERIFIED

- No real browser/JavaScript interaction, browser Back/unsaved-edit preservation, live listener/load or installed console-script smoke test. Generated controls and authenticated request handlers were exercised; sessions/tabs were simulated. The additional foreign identity did originate in a separate OS process.
- The **18 environment-gated skips** remain: capture process-group cases, phase-2/phase-3 opt-in trials and distribution installation. No live providers/platforms/audio/capture hardware or moderation calls.
- Independent commit observations are intercepted; S/Q cover ordinary real temporary-file persistence, atomicity and base preservation. No exhaustive hostile filesystem race/permissions/external-writer proof, new bind mount or real case-insensitive filesystem.
- No OS child stuck after SIGKILL induced; bounded doubles and ordinary real subprocess regressions ran. No total Check/filesystem/spawn/Apply 60-second transaction bound or stalled-upload wall-clock guarantee is asserted.
- Execution is finite, not a mathematical proof of cryptographic collision impossibility. Counter monotonicity/no history reuse is structural; process separation uses a random startup nonce plus a keyed digest. No universal absence proof for untested defects.
