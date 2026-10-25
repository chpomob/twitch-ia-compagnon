# VERDICT: REQUEST_CHANGES

Reviewed 2026-10-24, HEAD `38757c4`, against phase baseline `b7ec50d`, the brief's eight decisions and Amendment A1, spec R1–R8/AC1–AC32/allowlist, plan P1–P18/DD1–DD9, and the current `core/config_ui/__init__.py`.

The previous gate's two credential disclosures are fixed: their exact reproductions now return zero page, suffix and forwarded-stderr hits. The four profiles, wizard validation, regeneration/re-entry, instance arbitration and supervisor controls pass the executed local checks. Three closing failures remain: a missing French key silently switches a persisted French session to English, existing test changes exceed the allowlist, and new platform skips violate the explicit no-added-skip rule. These are the reasons for REQUEST_CHANGES; the operator trial is not a build-gate blocker.

The operator record is still **Status: not run**. **Phase 9 is not delivered until a real operator trial record exists and is accepted.** No fixture or fake-edge dry run below is evidence that a person completed the trial. Amendment A1 starts the criterion after installation, at the installed product's launcher click; the eventual record must also state who installed it, how, whether installation used a terminal, and how long installation took. This closing instruction supersedes the plan/spec's earlier suggestion that an unfilled record must block build approval.

# REQUIREMENT MAP

`F` = `tests/test_first_run.py`, `C` = `tests/test_console.py`, `U` = `tests/test_config_ui.py`. Names identify real running test functions, including their parameter cases. PASS is confined to automated/local evidence. Full-suite results appear under TEST TREATMENT.

| Requirement | Result | Named running tests and conclusion |
|---|---|---|
| R1 — first run, launcher, instance | PASS | F `test_ac1_no_config_resolves_the_a2_default`, `test_ac1_an_added_file_colliding_refuses_to_start`, `test_ac2_first_run_lands_on_the_wizard`, `test_ac3_the_browser_is_opened_only_once_serve_is_listening`, `test_ac4_instance_route_guards_and_answer`, `test_ac27_install_launcher_writes_one_launcher_and_serves_nothing`, `test_ac31_a_confirmed_instance_of_this_version_is_opened_not_served`, `test_ac31_a_stale_record_is_replaced_and_this_launch_serves`, `test_ac31_another_version_is_reported_and_never_opened`. Default locations, guards, launcher formats and second-launch behavior pass with injected OS/service edges. |
| R2 — bilingual table | FAIL | F `test_ac5_the_shipped_string_files_have_equal_complete_key_sets`, `test_ac5_every_key_referenced_by_the_wizard_and_console_exists`, `test_ac6_a_copied_language_file_is_offered_with_no_source_change`, `test_ac6_french_on_screen_one_renders_the_next_page_in_french_and_survives`; C `test_ac20_every_emitted_reason_token_has_a_sentence_in_both_languages`. Shipped tables and normal French sessions pass; missing-key attack N1 returns English with HTTP 200. |
| R3 — four complete profiles | PASS | F `test_ac7_four_profiles_load_in_the_stated_order`, `test_ac7_the_choice_screen_offers_four_profiles_in_order`, `test_ac8_a_generated_profile_is_reference_only_and_passes_check_config`, all five `test_ac9_*` functions, `test_ac10_a_generated_profile_uses_only_shipped_declarations`. Independent checks and recorded runs for every profile are below. |
| R4 — credential storage/use/redaction | PASS | F `test_ac11_the_credential_file_is_owner_only_under_the_per_user_directory`, `test_ac11_an_unrecorded_credential_file_is_neither_read_nor_replaced`, `test_ac11_no_response_carries_a_value_and_inputs_render_empty_and_kept`, `test_ac12_the_kept_credential_reaches_every_environment_redacted`, `test_p19f2_a_stored_credential_reaches_no_stream_when_the_child_fails`, `test_p19f2_a_long_credential_leaves_no_fragment_in_any_surface`; U `test_ac35_no_secret_value_reaches_any_response_log_report_or_record`. Fresh route/status/log and original crash reproductions pass. |
| R5 — observed-success wizard | PASS | F `test_ac13_each_screen_opens_only_once_the_previous_passed`, `test_ac13_changing_a_validated_input_invalidates_it_and_every_later_screen`, `test_ac14_each_platform_outcome_is_a_distinct_cause_and_keeps_next_closed`, `test_ac15_each_model_outcome_is_a_distinct_cause_and_keeps_next_closed`, `test_ac16_a_malformed_detector_names_the_field_and_keeps_next_closed`, `test_ac17_every_failure_page_is_plain_and_a_full_pass_starts_nothing`, `test_ac28_a_corrected_name_regenerates_the_wizard_own_files`, `test_ac29_screen_five_completes_only_on_a_recorded_run`, all five `test_ac30_[a-e]_*` functions. Directly injected failures cannot advance; restart revalidates external successes. |
| R6 — bounded runtime feed | PASS | C `test_250_facts_keep_exactly_the_latest_200_in_publication_order`, `test_every_supervision_fact_is_an_entry_and_nothing_else_is`, `test_a_5000_character_message_is_bounded`, `test_a_secret_is_redacted_before_the_entry_is_cut`, `test_a_raising_writer_never_reaches_the_bus`, `test_a_stalled_writer_never_holds_up_the_bus`, `test_without_the_variable_no_feed_is_written_and_nothing_changes`, `test_the_feed_through_run`. Retention, redaction, failure isolation and disabled-feed behavior pass. |
| R7 — console/supervisor/authorization | PASS | C `test_ac21_the_console_renders_the_feed_newest_first`, `test_ac21_the_poll_with_the_newest_cursor_returns_no_entry`, `test_ac22_start_with_no_live_record_launches_the_profile_once`, `test_ac22_stop_stops_only_the_child_this_ui_launched`, `test_ac23_revoke_with_a_supervised_child_writes_the_overlay_and_restarts_once`, `test_ac32_a_child_exiting_during_startup_shows_the_cause_and_the_closed_raw_detail`. Runtime authorization remains enforced and the old raw-detail leak no longer reproduces. R2's missing-language behavior is a separate failed requirement. |
| R8 — documentation, trial, distribution, invariants | NOT-VERIFIABLE | F all `test_ac24_*`, `test_ac25_the_template_carries_the_thirteen_labels_and_the_criterion`, `test_ac25_the_checker_refuses_each_violation_with_one_problem`, `test_ac26_pyproject_ships_the_strings_and_profiles_with_two_dependencies`; `tests/test_profiles.py::test_installed_distribution_discovers_the_shipped_manifests_and_answers_help`; bilingual documentation tests. Guide/checker/package data/dependencies/isolation PASS; the real human trial is NOT-VERIFIABLE and does not independently block this build gate. The separate closing test-treatment invariants fail N2/N3. |

## All 32 acceptance criteria

| Criterion | Result | Executed carrier / observation |
|---|---|---|
| AC1 | PASS | F `test_ac1_no_config_resolves_the_a2_default`, default/fallback cases and `test_ac1_an_added_file_colliding_refuses_to_start`: derived paths, collision refusal, value-free diagnoses and zero serve calls. |
| AC2 | PASS | F `test_ac2_first_run_lands_on_the_wizard`, `test_ac2_config_lands_on_the_unchanged_editor_with_one_console_link`, `test_ac2_following_the_first_run_redirect_renders_screen_one`; C `test_ac2_following_the_console_redirect_renders_the_console`. Existing editor comparison permits exactly its console link. |
| AC3 | PASS | F `test_ac3_the_access_url_is_opened_once_and_printed_once`, `test_ac3_a_raising_opener_leaves_the_printed_url_and_exit_zero`, listener-order test: one open/print, no token in logs, printed fallback on opener failure. |
| AC4 | PASS | C `test_ac4_every_new_route_is_refused_by_the_existing_guards_with_no_effect`; F `test_ac4_instance_route_guards_and_answer`. Direct re-execution also passes: no guard bypass or side effects. |
| AC5 | PASS | F shipped-file, referenced-key and wizard-key `test_ac5_*` functions. Fresh collectors: 360 keys per table, 228 source references, zero unresolved dynamic sites; equal placeholder sets and no shipped missing keys. N1 is runtime handling of a deliberately removed key. |
| AC6 | PASS | F copied-language, deleting-one-key, mismatched-placeholder and persisted-French `test_ac6_*` functions. Complete third language selectable by data; incomplete language withdrawn. Withdrawal incorrectly falls back for an existing French session, separately failed under R2/N1. |
| AC7 | PASS | F `test_ac7_the_choice_screen_offers_four_profiles_in_order` in both languages: exactly chat Q&A, channel points, spoken keyword, silent, with table names/descriptions. |
| AC8 | PASS | F `test_ac8_a_generated_profile_is_reference_only_and_passes_check_config` ×4 and independent checker calls below: exit 0, diagnostics `[]`, references listed under secrets, no literal credential. |
| AC9 | PASS | F `test_ac9_chat_qa_mention_runs_once_and_sends_nothing`, `test_ac9_chat_qa_without_the_name_is_one_rejection_and_no_run`, `test_ac9_channel_points_runs_only_for_the_picked_reward`, `test_ac9_spoken_keyword_wake_word_runs_once_without_a_process`, `test_ac9_silent_records_not_triggered_with_zero_model_requests`. Positive/negative events and no platform sends. |
| AC10 | PASS | F `test_ac10_the_shipped_examples_are_byte_identical_to_cb0f5b1`, `test_ac10_a_generated_profile_uses_only_shipped_declarations`; fresh comparisons also confirm all four root `*.example` files unchanged from `b7ec50d`. Existing 21 manifests supply all declarations. |
| AC11 | PASS | F `test_ac11_*`: owner-only credential directory/file, distinct `a.yaml`/`a.yml`, forced Windows location, refusal in insecure/unavailable directories, foreign store not read/replaced, empty/kept inputs and encoded-value response sweep. Fresh whole-value/fragment and long-tail attacks pass. |
| AC12 | PASS | F `test_ac12_the_kept_credential_reaches_every_environment_redacted`, `test_ac12_a_kept_credential_alone_counts_as_set`: stored value wins over inherited environment in check/dry run/supervised child and counts as set. Logs and report tails are redacted. |
| AC13 | PASS | F each-screen gating ×4 and input-invalidation cases. Fresh failures on screens 1–4 yield 409 for both GET and POST to the next screen. |
| AC14 | PASS | F platform-outcome, missing-scope, two-reward, empty-reward and identity-population `test_ac14_*` functions: distinct causes/next actions; refusal, timeout, malformed/client-id/scope errors cannot advance. |
| AC15 | PASS | F model-outcome, exactly-one-request, silent/no-request, shape/empty/late-answer `test_ac15_*` functions: actual fake completion required; silent explicitly needs no model. |
| AC16 | PASS | F appeared-profile, malformed-detector and read-only/no-YAML `test_ac16_*` functions; directly re-executed appeared-profile and real malformed-detector refusals. External bytes preserved; named field and next action; no YAML input through new routes. |
| AC17 | PASS | F `test_ac17_every_failure_page_is_plain_and_a_full_pass_starts_nothing`, validator failure-text tests; C `test_ac32_no_wizard_response_renders_a_raw_detail_disclosure`: zero traceback/submitted values in wizard failures, no runtime start during setup. |
| AC18 | PASS | C retention/bounds/redaction/raising-writer/stalled-writer tests named under R6: latest 200 of 250, maximum 1,024-byte entries and 80-character excerpts, redaction before truncation, publication survives writer failure. |
| AC19 | PASS | C `test_without_the_variable_no_feed_is_written_and_nothing_changes`, `test_a_colliding_feed_path_is_refused_at_startup`, `test_the_feed_check_runs_before_the_configuration_is_read`; existing runtime suites also run. No-feed behavior and collision checks pass. |
| AC20 | PASS | C `test_ac20_every_emitted_reason_token_has_a_sentence_in_both_languages`, unknown-token and collector-negative tests. Fresh collector: 138 tokens, zero unresolved dynamic sites. `zz_unknown` receives a nonempty localized sentence naming it. |
| AC21 | PASS | C newest-first, module cause/next-action and newest-cursor tests: five fixture entries, French refusal, degraded cause, incremental poll returns zero new entries. |
| AC22 | PASS | C all `test_ac22_*`: supervisor launch once when allowed; zero launches beside foreign live process; Stop only this UI's child; correct launch argv/feed environment and supervised startup result. |
| AC23 | PASS | C all `test_ac23_*`: both grants listed, base unchanged, managed overlay lists remaining rules, one supervised restart, executor subsequently refuses revoked `users.read` with `no_applicable_rule`; exact eight new routes and no grant-widening console control. |
| AC24 | PASS | F four `test_ac24_*`; `tests/test_docs_bilingual.py` discovery/shape/identifier checks. English normative/French translation, installation, launcher, all per-platform profile/credential locations, screens, console and editor documented and linked. |
| AC25 | PASS (checker/template) | F all `test_ac25_*`. Actual `not run` template yields 17 empty leaf-field problems; it cannot be approved. Fixtures reject missing/non-value install method/duration/elapsed, invalid platform, any empty field, terminal/YAML use, invalid/reversed times, inconsistent elapsed and ≥15 minutes. No real trial is asserted here. |
| AC26 | PASS | F package/dependency assertion and existing installed-distribution node: exactly aiohttp/PyYAML, specified package-data globs, two tables/four profiles installed byte-identical, unchanged 21 manifests and isolation exemptions. The separate stricter closing test-treatment rule fails N2/N3. |
| AC27 | PASS | F launcher installation/replacement/foreign-marker/link tests, forced Linux/Windows/macOS: exactly one appropriate launcher, current interpreter, no access token/credential, no server, owner-safe POSIX modes. |
| AC28 | PASS | Both F `test_ac28_*` directly re-executed: current-pass owned files regenerate from Nova to Orion, one profile/credential file, external append preserved and editor named; completed default-path restart lands on console. |
| AC29 | PASS | F each-profile recorded-result, real-entry completion, missing/wrong-record and timed-out/refused-entry `test_ac29_*` functions. Missing record/id/outcome, wrong origin/event/outcome and timeout leave completion false, even when event activity alone resembles a run. Four actual records below. |
| AC30 | PASS | All five F `test_ac30_[a-e]_*` functions directly re-executed: unfinished setup resumes inputs but only screen 1's success; Twitch/model/check/dry run revalidate; expired token refused/replaced; failed dry run retried; external edit preserved; completed wizard never renders again. State is secret-free. |
| AC31 | PASS | F `test_ac31_*` functions: confirmed live instance opens existing URL only, recorded port wins, bounded retries/total probe, unconfirmed record preserved, dead/wrong-marker/truncated/incomplete record replaced, races serve once, mismatched versions reported/open nothing, non-POSIX limitation explicit, clean release respects ownership. |
| AC32 | PASS | C cause/closed-detail/empty-tail/no-wizard-disclosure tests plus F `test_p19f2_*`. Original short and 9,042-character stored-secret reproductions now have zero page/suffix/forwarded hits; French cause remains outside closed raw detail and the traceback remains useful. |

## Four independently recorded profile dry runs

Called the real `core.main.check_config` entry (the `--check-config` path) and `core.main.dry_run` with generated profiles, fixture credentials, inert platform edges and fake model answer `bonjour`. These are records produced in this review. Scheduler/feed correlation assertions were executed, not inferred from output labels.

| Profile | `--check-config` | Recorded id | Outcome / reason | Origin and driven fake event |
|---|---|---|---|---|
| Chat Q&A (`chat_qa`) | Clean: exit 0, diagnostics `[]` | `run-bd46459fcef1c899` | `completed` / `success` | `dry_run`; `twitch`, `message`, `dry-0439e6b0` |
| Channel points (`channel_points`) | Clean: exit 0, diagnostics `[]` | `run-7b2e3107fe9f4a95` | `completed` / `success` | `dry_run`; `twitch`, `reward`, `dry-1f89ce87` |
| Spoken keyword (`spoken_keyword`) | Clean: exit 0, diagnostics `[]` | `run-90870aea37db8c13` | `completed` / `success` | `dry_run`; `audio_input`, `speech`, `dry-c85bcb13` |
| Silent (`silent`) | Clean: exit 0, diagnostics `[]` | `dry_run-3d0ded85d8090c61` | `not_triggered` / `rejected:probability_miss` | `dry_run`; `twitch`, `message`, `dry-1c04b541` |

The three triggering profiles' ids equal their scheduler record and both feed run-started/completed ids; each made exactly one model request. Silent's id identifies the dry-run observation: **zero scheduler records, zero started/completed runs, zero model requests, no claimed fired trigger**. All four attempted zero process starts and sent zero platform messages.

# SURFACE ATTEMPTS

Independent review probes ran from interpreter stdin against temporary fixtures and fake external edges. Running suite carriers named below supply additional parameter coverage. No implementation/test file was edited to execute them.

| Attack | Observed result |
|---|---|
| Store credential, restart UI, poll every route | PASS. Reloaded opaque 31-character credential; all 15 route entries driven, including concrete `/module/twitch`. Across 19 captured responses, zero whole-value hits and zero hits for any contiguous eight-character credential window. Routes/statuses: `/` 200, `/core` 200, `/module/twitch` 200, `/save` 409, `/remove` 409, `/check` 409, `/apply` 200, `/instance` 200, `/wizard` 303, `/language` 409, `/console` 200, `/feed` 200, `/start` 200, `/stop` 200, `/revoke` 200. U AC35 additionally drives valid editor operations and start/revoke outcomes across the shipped examples. |
| Capture every status publication and operation log during that sweep | PASS. Real supervisor logic over fake children: three captured status publications and seven log messages, zero whole-value or eight-character-window hits; stored credential reloaded into the UI environment. Additional Start/Apply/Stop exercised accepted status transitions. |
| Short stored credential in crashing child's stderr; open raw detail | PASS. Exact earlier-gate reproduction now prints `False 200 0 0 0`: HTTP 200, whole-value page count 0, suffix count 0, forwarded-stderr count 0. Real Supervisor/drain with fake exiting child; disclosure remains closed. New F `test_p19f2_a_stored_credential_reaches_no_stream_when_the_child_fails` covers status/log/stderr too. |
| 9,042-character stored secret crossing read and retention boundaries | PASS. Exact earlier-gate reproduction now prints `True 200 0 0 0`; the previously leaked 28-character suffix is absent. F `test_p19f2_a_long_credential_leaves_no_fragment_in_any_surface` also sweeps every suffix ≥12 characters across raw detail/page/status/log/stderr. |
| Split secrets across read chunks / overlap multiple secret spans | PASS. F split-read/overlap tests; independent 1,200 multi-secret streaming/chunk-boundary cases agree with `core.contracts.mask_known` after normalizing the marker. The new drain masks before forwarding and truncation. |
| Missing/wrong token, absent/forged session, bad Host, wrong method, foreign Origin, absent/wrong CSRF | PASS. C complete guard sweep directly re-executed: Host/Origin/CSRF 403, missing/forged session 401, wrong method 405, zero validator/checker/supervisor/feed-read/file-write effects. Access-token cases carried by F AC4. |
| `/instance` token bypass or excess response data | PASS. Missing/wrong token 401, bad Host 403, wrong method 405, no session created or marker leaked on refusal. Accepted GET answers exactly marker/version, no credential or access token. |
| Screen 1 without required companion name | PASS. `input_missing`, Next closed; both GET/POST screen 2 return 409. |
| Twitch refuses screen 2 | PASS. `unauthorized`, Next closed; GET/POST screen 3 return 409. All transport/client-id/scope/reward failures also have running AC14 cases. |
| Model errors on screen 3 | PASS. Fake HTTP 500 produces `not_a_completion`, Next closed; GET/POST screen 4 return 409. AC15 also exercises auth, not-found, empty, malformed and timeout cases. |
| Screen 4's configuration check fails | PASS. Injected checker failure gives `check_failed`, Next closed; GET/POST screen 5 return 409. Direct malformed-detector attack exercises the real checker and field-specific cause. |
| Screen 5 produces no recorded run | PASS. `dry_run_no_record`, Next closed, `completed=false`; GET console redirects 303 to wizard. AC29 also rejects incomplete/wrong records and expiry. |
| Operator creates target / edits wizard-owned profile | PASS. Appeared-file and changed-file tests directly re-executed; bytes preserved, editor next action, no advance. |
| Regenerate files owned by this wizard's current pass | PASS. Direct AC28 execution updates Nova to Orion while retaining exactly one profile and one credential file. External edits invalidate ownership. |
| Restart unfinished wizard / expired token / failed dry run | PASS. Direct AC30 a/b/c: screen 1 resumes; screens 3–5 remain 409 until renewed Twitch validation, then model/check/dry run repeat. Expired token refuses; replacement succeeds. |
| Re-enter completed wizard | PASS. Direct AC30 d: default access lands on console; every wizard GET/POST redirects without rendering a wizard screen. |
| Second launch against live instance | PASS in F AC31: no new server/instance; only existing URL reopened once on recorded port, honest already-running line; opener failure leaves printed fallback. This follows R1's explicit reopening behavior. |
| Stale instance / unconfirmed live instance / different version | PASS in F AC31: stale record removed and fresh serve once; unconfirmed live record preserved/reported, opens/serves nothing; different version reports both versions, preserves record and opens/serves nothing. |
| Console Start/Stop/Revoke or privilege widening | PASS in C AC22/AC23: supervisor controls only, foreign live process blocks Start, Stop only own child, Revoke writes managed overlay and restarts once when supervised; runtime executor refuses revoked `users.read`. Exact route/control checks expose no new grant/policy/admission/module-setting control. |
| French sentences, table references and runtime reasons | PASS on shipped data: 360 matching keys, 228 source references, 138 reason tokens, zero unresolved dynamic sites. Unknown reason has localized nonempty text. |
| Delete one French key and restart a persisted French session | FAIL, N1. Stored language remains `fr`, rendered language becomes `en`, HTTP 200 and English screen-2 title. This violates the requested missing-key failure behavior. |
| Approve unfilled trial template | Refused. Actual checker reports 17 empty leaf fields. Synthetic accepted fixture demonstrates consistency only; no invented trial was accepted as operator evidence. |

# TEST TREATMENT

## Baseline assertion ASTs and allowlist

Compared all baseline Python test files with current files via Python ASTs, matching test functions by file/name and comparing every `ast.Assert` with positions omitted. **2,725 baseline test functions; zero deleted; eight functions with changed assertion lists.** Also inspected diffed bodies, decorators and scan inputs: unchanged assertion ASTs alone do not prove unchanged expectations.

| Changed existing carrier | Treatment |
|---|---|
| U `test_a_missing_config_or_bad_port_exits_non_zero` | Allowlisted. Invalid-default/no-serve replacement for formerly required `--config`; both bad-port refusals retained. |
| U `test_ac35_no_secret_value_reaches_any_response_log_report_or_record` | Allowlisted exact route expansion, with stronger driven-route assertions. Also adds `application/json` to response types for `/instance` and `/feed`, explicitly checked as the only JSON routes. Prior security assertions retained. |
| `test_docs_bilingual.py` `GUIDES` | Allowlisted constant adds `first-run`; its existing assertion AST remains unchanged. |
| `test_profiles.py::test_installed_distribution_discovers_the_shipped_manifests_and_answers_help` | Spec-authorized strengthening only: 11 previous assertions retained, three assertion sites added for packaged files/bytes. 21-manifest count, node identity and process exemption unchanged; no ignore-rule change. |
| `test_agentic_loop.py::test_ac31_the_core_and_the_neutral_modules_name_no_platform` | Outside spec allowlist, flagged DD1 adaptation. New branch changes scan domain; baseline/current assertion counts 2/5. Exactly three paths exempted, with remaining coverage pinned. |
| `test_youtube.py::test_the_broad_core_scan_names_no_platform_or_vendor` | Outside spec allowlist, DD1. Changes scan input and adds two assertions (4/6). Same exactly-three-path exemption; AC39 unchanged. |
| `test_web.py::test_p3f1_one_huge_construct_still_reaches_the_deadline` | Outside allowlist: real-time expiry replaced by observed parser/deadline checks and relative timing. Assertion lists differ (3/4). |
| `test_web.py::test_p12f4_the_search_for_a_tag_end_stops_soon_after_its_deadline` | Outside allowlist: absolute small-budget timing replaced by forced expiry, count/result checks and relative timing (1/3). |
| `test_web.py::test_p12f4_the_json_walk_stops_soon_after_its_deadline_inside_one_value` | Outside allowlist: one absolute timing assertion replaced by `expired.reads == 100` (1/1, different AST). |

Three more existing web bodies inject `hold_the_walk_past_its_deadline` while retaining assertion ASTs: real-clock extraction, six-megabyte search and timed-out scalar walk. All six web edits improve determinism but exceed the closing rule of only allowlisted, phase-forced constant/count changes. The two DD1 scan adaptations were already flagged by the plan for ratification; this report does not silently grant that authority. DD1 exempts only `core/config_ui/locations.py`, `core/config_ui/profiles/`, `core/config_ui/strings/`; all other core code remains scanned. DD7's single editor console link passes its comparison.

**No-added-skip invariant: FAIL.** F line 1170 adds `posix_only = pytest.mark.skipif(...)`, applied to 18 new test functions; line 5039 adds `pytest.skip("POSIX modes")`. These do not fire on this Linux host, but are added skips. No added `xfail` or `importorskip`; no deleted test. The suite's pre-existing opt-in real-trial skips are distinct from these new platform conditions.

## Dependencies, sockets and processes

Runtime dependencies remain exactly `aiohttp` and `PyYAML`; pyproject's only change is package data `"core.config_ui" = ["strings/*.json", "profiles/*"]`. All four root examples are byte-identical to `b7ec50d`. No new runtime capability, module, trigger or dependency.

`tests/conftest.py` is byte-identical to baseline: no new socket/process exemption or weakened isolation fixture. Full suites run under its AC33 guard. Only its existing named A2 nodes may use real sockets/processes (plus pre-existing explicitly opted-in service trials, skipped here):

- `tests/test_kick.py::test_ac34_the_listener_runs_on_the_configured_host_and_port` — socket listener proof.
- `tests/test_profiles.py::test_installed_distribution_discovers_the_shipped_manifests_and_answers_help` — installed-distribution process proof.
- `tests/test_web.py::test_ac24_the_shipped_profiles_have_zero_changed_lines_since_the_phase_base` — baseline git process proof.

The event loop's own wakeup socketpair retains its existing narrow exception. Independent review service/process attacks used fakes; no real Twitch/model endpoint or runtime child was started.

## Faster-interpreter timeout audit

Scanned the complete suite for monotonic/performance clocks, short deadlines/budgets, timeout/abandonment expectations and finite parsing workloads, then inspected the implicated cases, including web parsing/cache/store, loader, audit and feed shutdown. **No remaining instance found in this HEAD where expected timeout/abandonment depends solely on a finite workload consuming a small wall-clock budget.**

The formerly vulnerable web tests have been changed: huge-construct/tag-end expiry is forced by observed checks, JSON expiry occurs on its 100th read, and three real-clock parse tests explicitly hold a worker until its deadline expires. The unfinished-tag case permits either fast successful empty text or timeout. Other inspected timeout cases use manual clocks, never-resolving futures or held events/stores rather than betting on CPU speed. Existing absolute latency ceilings/relative timing checks can still be sensitive to load; that is distinct from passing only on a slow interpreter. No Python 3.13 or arbitrary faster-hardware run was performed. The determinism improvements still fail the separate strict allowlist rule above.

## Full suites

Both commands use `PYTHONDONTWRITEBYTECODE=1` and `-p no:cacheprovider`. The bytecode setting was also applied to the floor command to avoid repository artifacts. The execution sandbox initially failed before running even read-only commands (`bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted`); commands therefore ran through the approved elevated tool mode.

- Project, Python **3.11.14**: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/ -q -p no:cacheprovider` → **5,243 passed, 20 skipped**, exit **0**, **495.67 s**.
- Floor, Python **3.10.19**: `PYTHONDONTWRITEBYTECODE=1 ~/.hermes/cache/scratch/venv310/bin/python -m pytest tests/ -q -p no:cacheprovider` → **5,243 passed, 20 skipped**, exit **0**, **678.05 s**.

Both A2 journals contain exactly four socket constructions for the named Kick node, four `Popen.__init__` calls for the existing install node and seven for the existing baseline-profile node. Zero new exemptions or non-exempt socket/process activity.

# NEW FINDINGS

Only executed reproductions/audits are listed. The previous gate's F1/F2 are resolved; its F3/F4/F5 remain and are numbered N1–N3 here. No implementation/test change is authorized by this report.

## N1 — MEDIUM: missing French key silently renders English in an existing French session

Locations: `core/config_ui/i18n.py:136`, `:152`; `core/config_ui/wizard.py:1964`.

Persist `language=fr` through screen 1. Copy shipped tables into a temporary directory, delete only `fr["wizard.screen2.title"]`, load that table before constructing a fresh wizard over the same state. The current code executes successfully and returns:

```json
{"stored_language":"fr","rendered_language":"en","status":200,"english_title_rendered":true,"refused":{"fr":"key set differs (missing ['wizard.screen2.title'], extra [])"}}
```

`sentence()` refuses missing keys, but the loader withdraws French and `Wizard.language` calls `language_or_default` before lookup, silently selecting English. Reproduction ran through the actual GET wizard handler. An unavailable persisted language must produce explicit failure; withdrawing incomplete optional languages from a new selection does not justify changing an existing French session to English.

Reproduce from the repository root (only automatically cleaned temporary fixtures):

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python - <<'PYCODE'
import sys, tempfile, pathlib, json, html
sys.path.insert(0, 'tests')
import pytest, test_first_run as t
from core.config_ui.i18n import StringTable
with tempfile.TemporaryDirectory() as td, pytest.MonkeyPatch.context() as mp:
    root = pathlib.Path(td)
    t._default_environment(mp, root / 'xdg')
    first = t._Flow(root)
    first.choice(language='fr')
    first.close()
    directory = root / 'tables'
    directory.mkdir()
    for code in ('en', 'fr'):
        data = json.loads((t.STRINGS / f'{code}.json').read_text())
        if code == 'fr':
            del data['wizard.screen2.title']
        (directory / f'{code}.json').write_text(json.dumps(data))
    table = StringTable.load(directory)
    mp.setattr(StringTable, 'load', classmethod(lambda cls, *a, **kw: table))
    again = t._Flow(root)
    response = again.get(screen='2')
    print(again.state()['language'], again.ui.wizard.language, response.status,
          html.escape(table.sentence('en', 'wizard.screen2.title')) in response.body.decode())
    again.close()
PYCODE
```

Observed: `fr en 200 True`.

## N2 — MEDIUM: existing expectations changed beyond the allowlist

Executed baseline/current AST comparison reports eight changed existing test functions and zero deletions; the exact carriers and assertion counts are tabulated above. Two platform scan domains change outside the allowlist (DD1); three existing web assertion lists change, and three further web bodies change with the same assertions. These changes are not only forced route/count constants. The fixes' useful intent does not satisfy the user's stricter closing invariant. Resolve the authority/scope or test treatment explicitly; a green suite alone cannot close it.

## N3 — MEDIUM: added platform skips violate the closing invariant

Executed diff/AST/decorator audit finds one added `pytest.mark.skipif` alias, 18 uses of it on new functions, and one added `pytest.skip` call. No new xfail/importorskip was found. The conditions are platform-specific and do not fire on this Linux run, but the instruction prohibits adding them. Both Linux suites passing cannot establish that no skip was added.

# NOT VERIFIED

- **No real Twitch** authentication, scopes, reward enumeration or delivery. Fake transports establish request/gating behavior only.
- **No real model endpoint**. Fake completion answers establish runtime correlation and failure handling only.
- **No real Windows trial**, launcher click or ACL observation. Windows/macOS branches and launcher contents are exercised by forced platform values on Linux; no real macOS launcher trial either.
- No timed first run by a non-technical streamer. The actual record is `not run` and fails its completeness checker. **Phase 9 is not delivered until the real record exists and is accepted**, including installation method/duration under A1. A field-consistency checker cannot establish that a narrative/timestamps describe an event that happened; no fixture is treated as such evidence here.
- No Python 3.13 or faster-hardware run. Required project/floor results and the timeout-pattern audit are reported above.

Only `docs/campaigns/phase9/full-branch-review.md` was modified. No implementation, test, guide, trial record or phase summary was changed or staged.
