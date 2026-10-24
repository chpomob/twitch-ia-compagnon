# VERDICT: REQUEST_CHANGES

Reviewed 2026-10-24, HEAD `8ce78488d98d63388fdbc677ff79f36a6021e276`, against `b7ec50d`, the brief (including Amendment A1), spec R1–R8/AC1–AC32/allowlist, plan P1–P18/DD1–DD9, and the current `core/config_ui/__init__.py`.

The four profiles, observed-success gates, restart recovery, instance arbitration and supervisor controls work in the executed fake-edge checks. Approval is blocked by credential disclosure through forwarded stderr and through a truncated raw-detail tail, an English fallback in a persisted French session, and violations of the requested test-treatment invariant. Green existing tests do not cover these failures.

The operator trial is **not run**: `trial-record.md` is still the unfilled template. Its absence is **not a blocker for this build gate**, under the closing instruction, which supersedes the plan's earlier suggestion to block on an unfilled record. The phase is **not delivered** until a real non-technical streamer trial is recorded and accepted. No synthetic test, dry run or fixture below is evidence of that human trial. Amendment A1 starts the clock at the installed product's launcher click; the eventual record must also say who installed it, how, whether a terminal was used, and how long installation took.

# REQUIREMENT MAP

`F` means `tests/test_first_run.py`, `C` means `tests/test_console.py`, `U` means `tests/test_config_ui.py`. Names below are actual executed test functions, including their parametrized cases. PASS concerns the automated/local scope; real service and OS observations are excluded explicitly below. A requirement can fail this adversarial gate even when its existing tests pass.

| Requirement | Result | Running tests and conclusion |
|---|---|---|
| R1 — first run, launcher, instance | PASS | F `test_ac1_no_config_resolves_the_a2_default`, `test_ac1_an_added_file_colliding_refuses_to_start`, `test_ac2_first_run_lands_on_the_wizard`, `test_ac3_the_browser_is_opened_only_once_serve_is_listening`, `test_ac4_instance_route_guards_and_answer`, `test_ac27_install_launcher_writes_one_launcher_and_serves_nothing`, `test_ac31_a_confirmed_instance_of_this_version_is_opened_not_served`, `test_ac31_a_stale_record_is_replaced_and_this_launch_serves`, `test_ac31_another_version_is_reported_and_never_opened`. Default paths, refusal guards, existing-instance reopening and honest failure reports pass against fakes. |
| R2 — bilingual table | FAIL | F `test_ac5_the_shipped_string_files_have_equal_complete_key_sets`, `test_ac5_every_key_referenced_by_the_wizard_and_console_exists`, `test_ac6_a_copied_language_file_is_offered_with_no_source_change`, `test_ac6_french_on_screen_one_renders_the_next_page_in_french_and_survives`; C `test_ac20_every_emitted_reason_token_has_a_sentence_in_both_languages`. Shipped data and normal French rendering pass, but removing a French key makes a restarted French session render English with HTTP 200 (F3). |
| R3 — four complete profiles | PASS | F `test_ac7_four_profiles_load_in_the_stated_order`, `test_ac7_the_choice_screen_offers_four_profiles_in_order`, `test_ac8_a_generated_profile_is_reference_only_and_passes_check_config`, the five `test_ac9_*` functions and `test_ac10_a_generated_profile_uses_only_shipped_declarations`. All four also independently checked and recorded below. |
| R4 — credential store and confidentiality | FAIL | F `test_ac11_the_credential_file_is_owner_only_under_the_per_user_directory`, `test_ac11_an_unrecorded_credential_file_is_neither_read_nor_replaced`, `test_ac11_no_response_carries_a_value_and_inputs_render_empty_and_kept`, `test_ac12_the_kept_credential_reaches_every_environment_redacted`; U `test_ac35_no_secret_value_reaches_any_response_log_report_or_record`; C `test_ac32_a_child_exiting_during_startup_shows_the_cause_and_the_closed_raw_detail`. Ownership, modes and ordinary redaction pass; actual drain forwarding and pre-redaction retention fail F1/F2. |
| R5 — five observed-success screens | PASS | F `test_ac13_each_screen_opens_only_once_the_previous_passed`, `test_ac13_changing_a_validated_input_invalidates_it_and_every_later_screen`, `test_ac14_each_platform_outcome_is_a_distinct_cause_and_keeps_next_closed`, `test_ac15_each_model_outcome_is_a_distinct_cause_and_keeps_next_closed`, `test_ac16_a_malformed_detector_names_the_field_and_keeps_next_closed`, `test_ac17_every_failure_page_is_plain_and_a_full_pass_starts_nothing`, `test_ac28_a_corrected_name_regenerates_the_wizard_own_files`, `test_ac29_screen_five_completes_only_on_a_recorded_run`, all five `test_ac30_[a-e]_*` cases. Re-entry, external-file preservation and dry-run record validation pass. |
| R6 — runtime feed | PASS | C `test_250_facts_keep_exactly_the_latest_200_in_publication_order`, `test_every_supervision_fact_is_an_entry_and_nothing_else_is`, `test_a_5000_character_message_is_bounded`, `test_a_secret_is_redacted_before_the_entry_is_cut`, `test_a_raising_writer_never_reaches_the_bus`, `test_a_stalled_writer_never_holds_up_the_bus`, `test_without_the_variable_no_feed_is_written_and_nothing_changes`, `test_the_feed_through_run`. Retention, redaction before truncation, failure isolation and disabled-feed behavior pass. |
| R7 — console and supervisor | FAIL | C `test_ac21_the_console_renders_the_feed_newest_first`, `test_ac21_the_poll_with_the_newest_cursor_returns_no_entry`, `test_ac22_start_with_no_live_record_launches_the_profile_once`, `test_ac22_stop_stops_only_the_child_this_ui_launched`, `test_ac23_revoke_with_a_supervised_child_writes_the_overlay_and_restarts_once`, `test_ac32_a_child_exiting_during_startup_shows_the_cause_and_the_closed_raw_detail`. Controls and runtime authorization pass; the raw-detail confidentiality promise fails F2. |
| R8 — guides, trial, distribution, test isolation | NOT-VERIFIABLE | F `test_ac24_the_guide_documents_the_install_path_and_the_launcher`, the other three `test_ac24_*` functions, `test_ac25_the_template_carries_the_thirteen_labels_and_the_criterion`, `test_ac25_the_checker_refuses_each_violation_with_one_problem`, `test_ac26_pyproject_ships_the_strings_and_profiles_with_two_dependencies`; `tests/test_profiles.py::test_installed_distribution_discovers_the_shipped_manifests_and_answers_help`; bilingual documentation suite. Automated guide, field-checker, package-data and dependency/isolation checks PASS. The real human trial is NOT-VERIFIABLE and does not block this build gate by itself. The separate closing invariant on test treatment fails F4/F5. |

## All 32 acceptance criteria

| Criterion | Result | Executed carrier / evidence |
|---|---|---|
| AC1 | PASS | F `test_ac1_no_config_resolves_the_a2_default`, Linux fallback/default tests, `test_ac1_an_added_file_colliding_refuses_to_start`; collision diagnoses are value-free and serve is not called. |
| AC2 | PASS | F `test_ac2_first_run_lands_on_the_wizard`, `test_ac2_config_lands_on_the_unchanged_editor_with_one_console_link`, `test_ac2_following_the_first_run_redirect_renders_screen_one`; C `test_ac2_following_the_console_redirect_renders_the_console`. |
| AC3 | PASS | F `test_ac3_the_access_url_is_opened_once_and_printed_once`, `test_ac3_a_raising_opener_leaves_the_printed_url_and_exit_zero`. |
| AC4 | PASS | C `test_ac4_every_new_route_is_refused_by_the_existing_guards_with_no_effect`; F `test_ac4_instance_route_guards_and_answer`. Also directly re-executed the complete guard test in the review. |
| AC5 | PASS | F `test_ac5_the_shipped_string_files_have_equal_complete_key_sets`, `test_ac5_every_key_referenced_by_the_wizard_and_console_exists`, `test_ac5_the_wizard_screen_keys_are_in_both_files`. 360 keys per shipped table; 228 referenced keys; zero dynamic references outside the named exception. F3 concerns missing-key runtime behavior, not a missing key in the shipped files. |
| AC6 | PASS | F `test_ac6_a_copied_language_file_is_offered_with_no_source_change`, `test_ac6_deleting_one_key_withdraws_the_language`, `test_ac6_a_mismatched_placeholder_withdraws_the_language`, `test_ac6_french_on_screen_one_renders_the_next_page_in_french_and_survives`. Extra missing-French-key attack fails separately (F3). |
| AC7 | PASS | F `test_ac7_the_choice_screen_offers_four_profiles_in_order`, both languages; exactly the specified four options and table descriptions. |
| AC8 | PASS | F `test_ac8_a_generated_profile_is_reference_only_and_passes_check_config` ×4; independent checks below all exit 0, no diagnostics. |
| AC9 | PASS | F `test_ac9_chat_qa_mention_runs_once_and_sends_nothing`, `test_ac9_chat_qa_without_the_name_is_one_rejection_and_no_run`, `test_ac9_channel_points_runs_only_for_the_picked_reward`, `test_ac9_spoken_keyword_wake_word_runs_once_without_a_process`, `test_ac9_silent_records_not_triggered_with_zero_model_requests`. |
| AC10 | PASS | F `test_ac10_the_shipped_examples_are_byte_identical_to_cb0f5b1`, `test_ac10_a_generated_profile_uses_only_shipped_declarations`; independent byte comparisons also confirm all four root examples unchanged from `b7ec50d`. |
| AC11 | FAIL | Ordinary storage, owner restrictions, empty/kept inputs, foreign-file protection and encoded-value sweep pass their F `test_ac11_*` carriers. Extended confidentiality attack finds a 28-character credential suffix in a response after drain truncation (F2). |
| AC12 | PASS | F `test_ac12_the_kept_credential_reaches_every_environment_redacted`, `test_ac12_a_kept_credential_alone_counts_as_set`; stored values win in checker, dry-run and child environments. F1 is the additional raw-stderr sink missed by these assertions. |
| AC13 | PASS | F `test_ac13_each_screen_opens_only_once_the_previous_passed` ×4 and `test_ac13_changing_a_validated_input_invalidates_it_and_every_later_screen`; independently injected failures below yield 409 on both next-screen methods. |
| AC14 | PASS | F `test_ac14_each_platform_outcome_is_a_distinct_cause_and_keeps_next_closed`, `test_ac14_the_missing_scope_names_are_shown`, `test_ac14_two_rewards_render_two_choices_by_title`, `test_ac14_an_empty_reward_list_is_its_own_cause`, `test_ac14_the_platform_answer_fills_the_identities_of_the_written_profile`. |
| AC15 | PASS | F `test_ac15_each_model_outcome_is_a_distinct_cause_and_keeps_next_closed`, `test_ac15_an_answer_opens_next_after_exactly_one_request`, `test_ac15_the_silent_profile_needs_no_model_and_makes_no_request`. |
| AC16 | PASS | F `test_ac16_a_profile_that_appeared_after_detection_is_never_overwritten`, `test_ac16_a_malformed_detector_names_the_field_and_keeps_next_closed`, `test_ac16_the_profile_view_is_read_only_and_no_new_route_takes_yaml`. |
| AC17 | PASS | F `test_ac17_every_failure_page_is_plain_and_a_full_pass_starts_nothing`; C `test_ac32_no_wizard_response_renders_a_raw_detail_disclosure`. Zero wizard-triggered supervisor starts. |
| AC18 | PASS | C retention/bounds/redaction/raising-writer/stalled-writer tests named under R6; latest 200 of 250, 1,024-byte entries, 80-character excerpts. |
| AC19 | PASS | C `test_without_the_variable_no_feed_is_written_and_nothing_changes`, `test_a_colliding_feed_path_is_refused_at_startup`, `test_the_feed_check_runs_before_the_configuration_is_read`; existing runtime suites pass. |
| AC20 | PASS | C `test_ac20_every_emitted_reason_token_has_a_sentence_in_both_languages`, `test_ac20_an_unknown_token_renders_the_generic_sentence_naming_it`, `test_ac20_the_collector_fails_on_a_new_uncovered_token`: 138 collected tokens, zero unresolved dynamic sites. |
| AC21 | PASS | C feed rendering, degraded-module cause/next-action and newest-cursor tests; five entries, newest first, French refusal and empty incremental poll. |
| AC22 | PASS | C all `test_ac22_*` cases: one supervisor launch, no launch beside a foreign live status record, Stop only for this UI's child; stored credentials and feed path in child environment. |
| AC23 | PASS | C all `test_ac23_*` cases: base unchanged, full remaining action list in managed overlay, one supervised restart, executor refuses revoked `users.read` with `no_applicable_rule`; exact eight new routes. |
| AC24 | PASS | F all `test_ac24_*`; `tests/test_docs_bilingual.py` pair discovery/shape/identifier checks. English normative guide and French translation cover installation, launcher, all default/credential directories, five screens, console and editor. |
| AC25 | PASS | F all `test_ac25_*`. Actual template has `Status: not run`; checker reports 17 empty leaf fields. Missing/non-value installation method, duration, elapsed time, invalid platform, touched terminal/YAML, invalid/reversed times and ≥15 minutes are refused. This proves field consistency, not the truth of a human account. No trial is approved here. |
| AC26 | PASS | Package/dependency assertions and the existing installed-distribution node PASS, including byte-identical two string files/four profiles and the unchanged 21-manifest count. Zero new socket/process exemptions. The separate closing requirement of allowlisted expectation changes only is violated (F4), and added skips violate F5. |
| AC27 | PASS | F `test_ac27_install_launcher_writes_one_launcher_and_serves_nothing`, foreign-file/marker-line/link refusal tests: Linux `.desktop`, Windows `.cmd`, macOS `.command`, injected directories, no token/credential, no server. |
| AC28 | PASS | F both `test_ac28_*` cases, also directly re-executed: changing Nova to Orion regenerates one owned profile and one credential file; external edit stays byte-identical; completion routes away from wizard. |
| AC29 | PASS | F `test_ac29_each_profile_records_its_expected_outcome`, `test_ac29_each_profile_completes_on_the_real_dry_run_record`, `test_ac29_screen_five_completes_only_on_a_recorded_run`, `test_ac29_a_dry_run_past_its_bound_or_refused_by_the_entry_stays_open`; missing/wrong records and timeout do not complete. |
| AC30 | PASS | F all five `test_ac30_[a-e]_*` cases plus state-secret tests, directly re-executed: inputs resume, only screen 1 resumes as passed, Twitch/model/check/dry run revalidated; expired token fails; completed wizard redirects every wizard request. |
| AC31 | PASS | F `test_ac31_*` instance cases: existing instance, recorded port, retry/whole-probe bounds, stale/dead/mismatched/truncated/incomplete records, publication races, version mismatch, non-POSIX limitation and clean release all pass with injected seams. |
| AC32 | FAIL | Normal short-credential French cause/closed-disclosure/empty-tail tests pass. Long accepted credential is cut before redaction and its suffix reaches the disclosure (F2), violating the gate's explicit meaningful-substring sweep. |

## Four independently recorded profile dry runs

Executed the real `core.main.check_config` and `core.main.dry_run` with generated profiles, fixture credentials, fake model text `bonjour`, inert platform edges and process-spawn spies. These are actual records returned during this review, not invented example ids.

| Profile | `--check-config` equivalent | Recorded id | Outcome / reason | Origin and fake event |
|---|---|---|---|---|
| Chat Q&A (`chat_qa`) | Clean: exit 0, diagnostics `[]` | `run-64fcc454d027382c` | `completed` / `success` | `dry_run`; `twitch`, `message`, `dry-72b10fd9` |
| Channel points (`channel_points`) | Clean: exit 0, diagnostics `[]` | `run-6e372616751f6d17` | `completed` / `success` | `dry_run`; `twitch`, `reward`, `dry-3551454f` |
| Spoken keyword (`spoken_keyword`) | Clean: exit 0, diagnostics `[]` | `run-6d9f1bcfb2cf0715` | `completed` / `success` | `dry_run`; `audio_input`, `speech`, `dry-72b9b7ec` |
| Silent (`silent`) | Clean: exit 0, diagnostics `[]` | `dry_run-73badbb28e291069` | `not_triggered` / `rejected:probability_miss` | `dry_run`; `twitch`, `message`, `dry-e755e62e` |

For each triggering profile the displayed id was also the admission scheduler record id and both feed started/completed ids, with one model request. Silent has a synthetic dry-run observation id only: **zero scheduler records, zero run-started/completed facts and zero model requests**. It does not claim a trigger fired. Every profile sent zero platform messages and attempted zero process starts.

# SURFACE ATTEMPTS

In addition to the full suites, review scripts were run through interpreter stdin, with temporary directories cleaned afterwards and fake service/process edges. No repository test or implementation file was written.

| Attack | Observed result |
|---|---|
| Poll every route with a credential written by screen 4 | PASS for ordinary values: all 15 route-table entries driven; bodies contained zero full-token hits and zero contiguous 8-character token-window hits. Methods/results: `/` 200, `/core` 200, `/module/` 404 (prefix entry), `/save` 409, `/remove` 409, `/check` 409, `/apply` 200, `/instance` 200, `/wizard` 303, `/language` 409, `/console` 200, `/feed` 200, `/start` 200, `/stop` 200, `/revoke` 200. The running U AC35 sweep additionally visits concrete module pages, valid Check/Save/Remove and all start/revoke outcomes across shipped examples. |
| Reload actual credential file, Start through supervisor, inspect status records | PASS: credentials reloaded exactly; child environment contained stored token; three captured status publications had zero whole-value or 8-character credential-window hits. One operation log line also had zero hits. Existing status-transition tests and U AC35 cover additional status snapshots. |
| Inspect ordinary operation log records and all captured stderr lines | FAIL: ordinary route sweep captured four operation log messages with zero credential hits, but two child stderr forwards contained the full stored credential. Separate single-Start reproduction confirms one cleartext occurrence (F1). |
| Open raw detail after a short-credential child crash | PASS for rendered page: HTTP 200, zero full credential hits, one closed `<details>`, traceback inside the raw detail; cause remains outside it. Raw forwarding still fails F1. |
| Put an accepted 9,042-character stored client secret in crashing child's stderr | FAIL: full-value page count 0 but its 28-character suffix appears once inside closed raw detail. Drain retains 8,192 bytes before redaction; report retains 2,048 (F2). |
| Missing/wrong access token, forged/missing session, bad Host, method, Origin, CSRF | PASS: complete C AC4 guard test directly re-executed. Host/Origin/CSRF 403; missing/forged session 401; wrong method 405; no validator/checker/supervisor/feed/write side effects. |
| Probe `/instance` without/wrong token, or wrong Host/method | PASS: 401 without/wrong token, 403 bad Host, 405 wrong method; successful GET returns exactly `marker` and `version`, no session creation or credential/token disclosure. |
| Fail screen 1's required companion name | PASS: `input_missing`, no Next; GET and POST screen 2 both 409. |
| Make Twitch refuse screen 2 | PASS: `unauthorized`, no Next; GET and POST screen 3 both 409. Other transport/scope/client-id/reward failures pass the running AC14 cases. |
| Make model return HTTP 401 on screen 3 | PASS: `unauthorized`, no Next; GET and POST screen 4 both 409. Malformed, empty, unavailable and timed-out answers are covered by AC15. |
| Make screen 4 checker fail | PASS: `check_failed`, no Next; GET and POST screen 5 both 409. Direct malformed-detector case also fails the real checker and names its field. |
| Return no dry-run record on screen 5 | PASS: `dry_run_no_record`, `completed=false`, no Next; GET console redirects 303 to wizard. Existing AC29 rejects missing id/outcome, wrong origin/event/outcome and expired bound, even when completion-like activity is insufficient. |
| Create operator profile after detection / edit wizard-owned profile | PASS: both refusal tests directly executed; bytes preserved, no advance, editor named as next action. |
| Regenerate current-pass owned files after changing companion name | PASS: directly executed AC28; one updated profile and one credential file; old ownership does not authorize overwriting an externally changed file. |
| Restart unfinished wizard / expire stored token / retry failed dry run | PASS: AC30 a/b/c directly re-executed. Only screen 1 resumes passed; screens 3–5 return 409 until Twitch succeeds again; model/check/dry run run again; expired token refused and replacement accepted. |
| Re-enter completed wizard | PASS: AC30 d directly executed; access lands on console at default path, every wizard request redirects without a wizard screen. |
| Second launch against live confirmed instance | PASS in running AC31 tests: zero new serve calls; existing URL reopened once on its recorded port; one honest already-running line. Browser failure leaves printed fallback. |
| Second launch with stale record or incompatible version | PASS in running AC31 tests: stale/dead/wrong-marker/incomplete record replaced and fresh serve called once; version mismatch preserves record, reports both versions, opens/serves nothing. Unconfirmed live record is preserved and reported, not silently replaced. |
| Start/Stop and grant escalation attempts | PASS in running C AC22/AC23: supervisor only, no second start beside live foreign process, Stop only this child, opaque/fingerprinted Revoke removes through overlay, base unchanged, one restart when supervised. Executor subsequently refuses revoked `users.read`; no new grant/policy/module-setting control. |
| Check French table coverage / unknown reason | PASS on shipped data: 360 matching keys, 228 source references, 138 reason tokens; unknown `zz_unknown` gets a nonempty localized sentence. |
| Delete one French key and restart existing French session | FAIL: stored language remains `fr`, loader refuses French, renderer chooses `en`, HTTP 200 and English wizard title (F3). |
| Try approving the current trial template | Refused: 17 empty leaf fields. No real trial was claimed. Synthetic conforming fixture acceptance demonstrates consistency checks only; neither those invented times nor the four fake-edge runs above are a human trial. |

# TEST TREATMENT

## Assertion AST and allowlist audit

Executed an AST comparison of all baseline Python test files, matching their test functions by file/name (no duplicate test names found), stripping positions and comparing every `ast.Assert`. Compared **2,725 baseline test functions**: **zero deleted**, **eight functions with changed assertion AST lists**. No existing `tests/conftest.py` change; no added `xfail` or `importorskip` found.

| Changed existing carrier | Treatment |
|---|---|
| U `test_a_missing_config_or_bad_port_exits_non_zero` | Allowlisted: replaces `main([]) != 0` with invalid-default/no-serve evidence; both bad-port assertions retained. |
| U `test_ac35_no_secret_value_reaches_any_response_log_report_or_record` | Allowlisted route expansion: exact 7+8 routes and stronger driven-route assertions. Content-type expectation also adds `application/json`, necessarily used by `/instance` and `/feed`. All prior remaining assertions retained. |
| `test_docs_bilingual.py` GUIDES declaration | Allowlisted constant adds `first-run`; test assertion AST unchanged. |
| `test_profiles.py` installed-distribution node | Permitted strengthening only: all prior assertions retained, three added assertion sites verify package files/bytes; 21-manifest count unchanged, no new process node or ignore-rule change. |
| `test_agentic_loop.py::test_ac31_the_core_and_the_neutral_modules_name_no_platform` | **Outside spec allowlist**, flagged by plan DD1. Three new assertion sites plus a new core branch that returns before the old scan. Precisely three paths exempted: `core/config_ui/locations.py`, `core/config_ui/profiles/`, `core/config_ui/strings/`. Existing-file coverage retained; requires explicit ratification rather than claiming allowlist compliance. |
| `test_youtube.py::test_the_broad_core_scan_names_no_platform_or_vendor` | **Outside spec allowlist**, DD1: helper changes scan input and two assertions added; same precisely pinned exemption. `test_ac39_core_names_no_platform` unchanged. |
| `test_web.py::test_p3f1_one_huge_construct_still_reaches_the_deadline` | **Outside allowlist**: two old assertion ASTs replaced by three; retains 6-second whole-walk check via measured variable, adds expiry-read evidence and relative abandonment time. |
| `test_web.py::test_p12f4_the_search_for_a_tag_end_stops_soon_after_its_deadline` | **Outside allowlist**: old absolute 0.05-second assertion replaced with search result, read count and quarter-whole-walk comparison. |
| `test_web.py::test_p12f4_the_json_walk_stops_soon_after_its_deadline_inside_one_value` | **Outside allowlist**: old absolute 0.05-second assertion replaced with `expired.reads == 100`. |

Three additional existing web test bodies (real-clock extraction, 6 MB search, timed-out scalar walk) inject `hold_the_walk_past_its_deadline` while preserving their assertion ASTs. These are also substantive out-of-list edits, not forced route/count constants. Their determinism intent is sound; it does not make the closing allowlist invariant true. DD7 kept exactly one console link on the editor base page; its running comparison passes. DD1, all six web-test edits, and the content-type adaptation are explicitly disclosed here; this review does not silently ratify exceptions.

**Added skip invariant: FAIL.** New F defines `posix_only = pytest.mark.skipif(...)` at line 1167, applies it to **18 new test functions**, and adds `pytest.skip("POSIX modes")` at line 5036. These are platform conditions, not hidden xfails, and do not skip on this Linux host; they nevertheless violate the explicit instruction “No added skip”. The 20 skips in the Linux full-suite result are the pre-existing opt-in trial skips, not evidence that no new skip code exists.

## Dependencies, sockets and processes

Runtime dependencies remain exactly `aiohttp` and `PyYAML`; the only pyproject change adds `"core.config_ui" = ["strings/*.json", "profiles/*"]`. No capability/module/trigger or shipped example changed. All four example files compare byte-identical with baseline. No new test socket/process exemption or changed isolation fixture.

Both full-suite AC33 A2 journals list only the three existing named exemptions, with identical counts:

- `tests/test_kick.py::test_ac34_the_listener_runs_on_the_configured_host_and_port`: four socket constructions.
- `tests/test_profiles.py::test_installed_distribution_discovers_the_shipped_manifests_and_answers_help`: four `Popen.__init__` calls.
- `tests/test_web.py::test_ac24_the_shipped_profiles_have_zero_changed_lines_since_the_phase_base`: seven `Popen.__init__` calls.

Everything else runs behind the unchanged isolation guard (the event loop's own wakeup pair remains its existing special case). The review's service/process adversarial probes also used fakes and did not start a runtime process or contact Twitch/model services.

## Faster-interpreter timeout audit

Scanned the complete suite for monotonic/performance clocks, small timeout budgets, abandonment/timeout assertions and finite large parsing workloads; inspected the implicated bodies. The vulnerable baseline web pattern is present in the diff, but is corrected in the reviewed HEAD:

- Huge construct and tag-end searches now expire by observed parser/deadline reads, with relative timing assertions.
- JSON scalar walk expires on its 100th check, not after a workload might finish on a fast interpreter.
- Three real-clock parsing tests explicitly hold a worker at a deadline check until expiry; finite CPU work alone no longer decides whether timeout occurs.

**No remaining instance found in HEAD of a timeout/abandonment expectation relying only on a finite workload taking longer than a small wall-clock budget.** Other real-time timeouts inspected use never-resolving futures, held events/stores, or an explicit time-based spin; the unfinished-tag test accepts either fast success with empty text or timeout. Absolute latency ceilings/relative timing checks can still be load-sensitive, which is a different issue. Python 3.13 and arbitrary faster hardware were not run. The deterministic fixes still need resolution of the separate allowlist violation above.

## Full suites

Both runs used `PYTHONDONTWRITEBYTECODE=1` and `-p no:cacheprovider`; the extra bytecode setting was also applied to the requested floor invocation to honor repository write restrictions. Execution required the tool's elevated execution mode because even read-only sandbox commands initially failed with `bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted`.

- Project, Python **3.11.14**: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/ -q -p no:cacheprovider` → **5,237 passed, 20 skipped**, exit **0**, **438.17 s**.
- Floor, Python **3.10.19**: `PYTHONDONTWRITEBYTECODE=1 ~/.hermes/cache/scratch/venv310/bin/python -m pytest tests/ -q -p no:cacheprovider` → **5,237 passed, 20 skipped**, exit **0**, **609.55 s**.

# NEW FINDINGS

Only executed reproductions are listed. F1/F2 use fake child objects feeding real `Supervisor`/`_StderrDrain` behavior, with a credential actually stored by the wizard; no real process or service is needed.

## F1 — HIGH: stored credential is forwarded verbatim to UI stderr

Locations: `core/config_ui/__init__.py:5669`, `:5680`, `:5850`.

Complete a wizard using client secret `credential-canary-2846159703`; create a fake exiting child whose stderr contains a traceback and that value; connect it through the real supervisor and POST `/start`. Capture `sys.stderr.buffer`. Observed: HTTP 200, one closed disclosure, **0 full-value occurrences in the page, 1 full-value occurrence in forwarded stderr**. The normal-token all-route sweep also captured two such forwards. `_StderrDrain._forward(chunk)` writes raw bytes before any `_report_tail` redaction, bypassing the UI logger's filter. Redirected process stderr is therefore a credential-bearing log sink. Mask child output before forwarding, including values split between reads.

## F2 — HIGH: retention before redaction leaks a credential suffix in raw detail

Locations: `core/config_ui/__init__.py:5670`, `:5690`, `:5921`, `:5937`.

Use accepted/stored client secret `"client-secret-" + "x" * 9000 + "TAIL-FRAGMENT-9153726804-END"` (9,042 characters). Give the fake exiting child that secret in stderr, and POST `/start` through the real supervisor. Observed: HTTP 200; **full secret count 0; 28-character suffix count 1, inside the closed `<details>`**. The 8,192-byte drain drops the beginning before `_report_tail` masks whole values; the remaining 2,048-byte report still holds the unrecognizable suffix. Closing a disclosure does not remove its HTML from the page. Redact before retention and handle credentials crossing read/retention boundaries.

Reproduction for F1 and F2 (run from repository root; creates only automatically removed temporary test data):

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python - <<'PY'
import sys, tempfile, pathlib, io, types
sys.path.insert(0, 'tests')
import pytest, test_first_run as t
from core import config_ui as c
for long in (False, True):
    with tempfile.TemporaryDirectory() as td, pytest.MonkeyPatch.context() as mp:
        root = pathlib.Path(td)
        t._default_environment(mp, root / 'xdg')
        suffix = 'TAIL-FRAGMENT-9153726804-END'
        value = ('client-secret-' + 'x' * 9000 + suffix if long
                 else 'credential-canary-2846159703')
        mp.setattr(t, 'WIZARD_SECRET', value)
        flow = t._Flow(root)
        flow.through('chat_qa')
        class Child:
            pid = 987654
            def __init__(self):
                self.stderr = tempfile.TemporaryFile()
                self.stderr.write(('Traceback (most recent call last):\n' + value + '\n').encode())
                self.stderr.seek(0)
            def poll(self): return 1
            def terminate(self): pass
            def wait(self, timeout=None): return 1
            def kill(self): pass
        flow.ui.supervisor = c.Supervisor(
            ['fake-child'], flow.ui.status_path,
            popen=lambda *a, **kw: Child(), environ=flow.ui.environ)
        capture, old = io.BytesIO(), sys.stderr
        sys.stderr = types.SimpleNamespace(buffer=capture)
        try:
            response = flow.post('/start', {})
            flow.ui.supervisor.close()
        finally:
            sys.stderr = old
        body = response.body.decode()
        print(long, response.status, body.count(value), body.count(suffix),
              capture.getvalue().count(value.encode()))
        flow.close()
PY
```

Observed output: `False 200 0 0 1`, then `True 200 0 1 1`.

## F3 — MEDIUM: missing French key silently changes an existing French session to English

Locations: `core/config_ui/i18n.py:136`, `:152`; `core/config_ui/wizard.py:1966`.

Persist `language=fr` through screen 1. In a temporary copy of the shipped tables delete `fr["wizard.screen2.title"]`, load that directory and inject the resulting table into a fresh UI over the same state. This changes no repository table. Executed result:

```json
{"stored_language":"fr","rendered_language":"en","status":200,"english_title_rendered":true,"refused_languages":{"fr":"key set differs (missing ['wizard.screen2.title'], extra [])"}}
```

`sentence()` itself fails on an unknown key, but the loader removes French and `language_or_default` silently defaults the persisted French selection to English before lookup. The requested French-session failure behavior is therefore absent. Fail explicitly for an unavailable persisted language; incomplete optional languages may still be withheld from new selections.

## F4 — MEDIUM: changed existing test expectations exceed the allowlist

Executed full baseline/current assertion-AST comparison gives eight changed test functions, including the three web assertion replacements detailed above. DD1 additionally changes two existing scan domains outside the spec allowlist; three further web test bodies change without changing their assertions. These are not solely phase-forced constants/counts. No test deletion was found. Resolve the scope with explicit authority or restore the required treatment; this report is not authorization to alter tests.

## F5 — MEDIUM: new platform skips violate the closing invariant

AST/attribute/decorator inspection finds the new `pytest.mark.skipif` alias, its 18 decorated functions, and one new `pytest.skip` call in F. No new xfail/importorskip. They happen not to fire on this host, so a green Linux run cannot establish the requested no-added-skip invariant. Resolve the test treatment explicitly; do not hide this as merely the suite's pre-existing 20 real-trial skips.

# NOT VERIFIED

- **No real Twitch** authentication, scopes, reward enumeration or channel delivery was exercised. Fake transports establish request/gating behavior only.
- **No real model endpoint** was called. Fake completions establish dry-run correlation and refusal behavior only.
- **No real Windows trial** or Windows launcher click/ACL observation was performed. Windows/macOS branches and launcher formats were forced in tests on Linux; no real macOS launcher trial either.
- No non-technical streamer's timed first run. Current operator record is explicitly `not run`; the template/checker is built, but **phase 9 is not delivered** until the real record exists and is reviewed. A consistency checker cannot attest that supplied timestamps or narrative describe a trial that actually happened; no fixture is accepted here as such evidence.
- No Python 3.13 run or measurement on faster hardware. The required project/floor runs and source-level timeout-pattern audit are reported above.

Only this review file was created in the repository. No implementation, test, guide, trial record or summary was changed or staged.
