# Phase 6 — full-branch closing gate, second pass

## VERDICT: REQUEST_CHANGES

Reviewed `8f0bd2680d886cc87592c982f8b171f17219b335` against `8010edc`, and the five fixes against first-gate commit `0f335bc`, on 2026-10-19.

**All 34 in-scope product/test findings F1–F33 and F35 are CLOSED. N1–N3 are CLOSED. F34 and `claude:F8` remain OUT-OF-SCOPE.** Probes A–E now satisfy their requested boundaries, the amendment covers all five tests, and the suite passes. No executed counterexample identified a defect introduced by P20F1–P20F5.

One previously recorded requirement discrepancy still prevents an unconditional closing approval: **literal AC2 remains FAIL**. It requires one `action.requested` event; the compatibility route emits zero, with one `action.started` and one `action.completed` instead. This is the discrepancy already identified in the first report's R1 row, not a newly discovered security defect or a regression of F1. The current amendment addresses N3 only; it does not reconcile AC2. Align the acceptance criterion explicitly with the canonical executor trace, or implement and test the required event. I have not silently treated the two event names as interchangeable.

Only this report was written in the repository. Nothing was staged, committed, checked out, or edited in source/tests. Initial working-tree status was clean. Python bytecode and pytest cache writes were disabled. Tests used their ordinary temporary fixtures outside the repository; independent source/configuration mutations were in memory.

## Observed runs and method

```text
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/ -q -p no:cacheprovider
3901 passed, 26 skipped in 276.06s (0:04:36)

70 distinct regression nodes extracted from the phase-summary finding ledger:
133 passed in 9.54s

Ownership/status/redaction/proxy selection:
174 passed, 295 deselected in 35.20s

Boolean/enum vocabulary controls + local trace + fresh/store-erasure controls:
18 passed in 20.79s

F28 alone, -p no:randomly:
1 passed in 0.37s

git diff --quiet 8010edc HEAD -- tests/test_phase5_docs_status.py
exit 0
```

The 174-case selection was `tests/test_input_ownership.py tests/test_phase5_docs_status.py tests/test_redaction_surfaces.py tests/test_proxy.py -k 'test_ac or p6 or f2_f3 or f3_a_reuse or no_document or no_shipped or gated_bindings'`. The full suite also exercised cases excluded by that selection. The 133-case focused run printed a pending cancellation-resistant task at interpreter teardown from the F35 stalled-cleanup fixture; it did not fail a test. This was not evidence of a new P20F1–P20F5 product regression.

“Ledger PASS” below means the named node actually ran in the observed `133 passed` selection, as well as the full suite. Behavior described after it is checked by that test's assertions. Historical baseline failure counts are not presented as fresh output: this pass reran head reproductions, A–E, and the mutations below, rather than repeating the first pass's baseline overlay.

## PREVIOUS FINDINGS

Test paths in this table are relative to `tests/`. Every row has an observed run or mutation; CLOSED refers to the original finding, distinct from the remaining literal AC2 discrepancy.

| Finding | Disposition | Executed evidence |
|---|---|---|
| F1 | **CLOSED** | `test_twitch.py::test_ac1_an_ungranted_compatibility_send_is_refused_by_the_executor`, `test_ac1_without_an_executor_the_compatibility_route_refuses`, `test_ac2_the_compatibility_send_is_traced_as_an_executor_call`: Ledger PASS. Independent publication output: ungranted `posts=0, provider_entries=0, failed/refused`; granted `posts=1, provider_entries=1, sent/success`. Literal event counts are recorded below. |
| F2 | **CLOSED** | Probe A: out-of-set action `refused/unknown_action`; other principal `refused/not_authorized`; combined alteration `refused/unknown_action`. Each has `retained_equal=false, result_present=false, parts=[], executor_calls=1`. Exact retry: `success, retained_equal=true, executor_calls=1`. |
| F3 | **CLOSED** | Probe A changed arguments: `refused/call_id_conflict`, no retained result, executor count unchanged. `test_actions.py::test_ac3_a_reused_call_id_naming_another_call_is_refused`, `test_ac3_a_reuse_whose_arguments_only_share_a_repr_is_refused`, and policy-change reuse test: Ledger PASS. Agent rendering-collision/in-flight controls pass in the 174-case run. |
| F4 | **CLOSED** | Probe C: `exit=2`, empty stdout, masked stderr quoted below. Probe D: all four raw fixtures **FAIL**, positive control **PASS**. Loader/main AC10 and F26 tests: Ledger PASS; structural and hook-renderer controls pass. |
| F5 | **CLOSED** | Probe B: `status=success, result_returns=true, leaking_records=0, leaking_diagnostics=0`. Real started/completed records retain action, provider, call ID, destination, status and local principal; peer correlation fields become local labels. Four-field/token/4 KiB controls and local-call control pass. |
| F6 | **CLOSED** | Probe E: `brain_recall_empty=true, viewer_memory_record_exists=false, erased_text_in_any_store_file=false, files_scanned=0, store_refusals=2, brain_refusals=1`; `1 passed in 0.16s`. Fresh same-key and other-key writes pass the additional controls. |
| F7 | **CLOSED** | `test_actions.py::test_ac4_concurrent_identical_calls_reach_the_provider_once`, concurrent-conflict and cancelled-first-caller tests: Ledger PASS; one provider/handler execution, equal outcomes, settled waiter. Independently cancelled-waiter control also passes in the suite. |
| F8 | **CLOSED** | `test_automation_bridge.py::test_ac15_phase6_a_raising_reconcile_trace_leaves_the_owner_alive` and permanently-raising/backoff control: Ledger PASS; owner survives, failed connection closes, next connection count is 2, repeated failures retain capped backoff. |
| F9 | **CLOSED** | `test_twitch.py::test_ac16_an_authentication_refusal_of_the_chat_subscription_ends_recovery`: Ledger PASS for 401/403; one degraded authentication event and no continuing retries over 60 simulated seconds. |
| F10 | **CLOSED** | `test_twitch.py::test_ac17_a_socket_silent_past_its_keepalive_is_recovered`: Ledger PASS; no early reconnect, recovery after the 10-second keepalive plus margin. |
| F11 | **CLOSED** | `test_youtube.py::test_ac18_the_first_page_of_a_fresh_resolution_admits_nothing` and `test_ac18_the_first_page_after_a_re_lookup_admits_nothing`: Ledger PASS; three historical messages admit zero, following new message admits one, 403/404 re-lookup primes again. |
| F12 | **CLOSED** | Three `test_brain.py::test_ac19_*` boundary tests: Ledger PASS; bound+1 read, zero JSON decoder calls above bound, exactly-at-bound decoded and delivered. |
| F13 | **CLOSED** | `test_proxy.py::test_p6_ac20_a_third_unpaired_connection_beyond_the_cap_of_2_is_refused`: Ledger PASS; third refused at cap 2, pairing/closing release capacity. |
| F14 | **CLOSED** | `test_proxy.py::test_p6_ac9_running_executions_and_duplicate_waiters_are_bounded`: Ledger PASS; two executor entries, third refused, bounded duplicate waiters, capacity available again afterward. |
| F15 | **CLOSED** | `test_installation_doc.py::test_the_guides_endpoint_values_are_complete_chat_completions_urls` and `test_the_guides_profile_posts_to_the_guides_endpoint`: Ledger PASS for both guides; complete `/chat/completions` URLs and actual fake-backed POST URL equality. |
| F16 | **CLOSED** | `test_installation_doc.py::test_the_readme_config_ui_command_runs`: Ledger PASS; printed command with help exits 0 and names `--config`. |
| F17 | **CLOSED** | `test_installation_doc.py::test_the_documents_name_the_overlay_the_code_derives`: Ledger PASS for guides/README; code-derived `mon-compagnon.local.yaml`. |
| F18 | **CLOSED** | `test_installation_doc.py::test_the_guide_prints_the_commands_the_code_still_has`: Ledger PASS; commands resolve against code and old exact command predicates are retained. Wrong-profile mutations fail on both guides; see N2. |
| F19 | **CLOSED** | Proxy `test_p6_ac20_a_silent_connection_is_closed_at_the_hello_deadline_and_not_one_tick_before` and blocked-reader deadline test: Ledger PASS; open before deadline, closed/free slot at deadline, blocked writer cannot retain it. |
| F20 | **CLOSED** | Brain `test_ac21_arguments_nested_10000_levels_are_malformed_and_the_run_goes_on`, adapter `test_ac21_probe_arguments_nested_10000_levels_raise_brain_module_error`: Ledger PASS; malformed observation without run failure, controlled probe exception. |
| F21 | **CLOSED** | Viewer-memory three `test_ac25_*` write/rename/use failure tests: Ledger PASS; precommit ENOSPC evicts nothing; later failure publishes exact actual deletion count. |
| F22 | **CLOSED** | Viewer-memory three `test_ac26_*` temporary-file tests: Ledger PASS; 1,000 bytes counted while deletion fails, reclaimed on successful scan, never parsed as a record. |
| F23 | **CLOSED** | `test_admission.py::test_expired_and_evicted_sessions_never_accumulate_in_the_ready_structure`: Ledger PASS; ready length stays within max_sessions across repeated removals, returns to zero, subsequent FIFO execution preserved. |
| F24 | **CLOSED** | Twitch `test_ac23_chat_write_refuses_text_over_500_characters_before_any_request` and compatibility-route control: Ledger PASS; 501 refused with zero POSTs, 500 Unicode characters sent once. |
| F25 | **CLOSED** | Kick `test_ac24_the_dedup_window_is_never_shorter_than_twice_the_tolerance`: Ledger PASS; 300 refused, 600 accepted. Full-suite replay-span control passes: replay 580 seconds later is deduplicated while a fresh ID with the same valid timestamp is accepted. |
| F26 | **CLOSED** | Loader `test_f26_an_undeclared_setting_is_refused_by_its_path_at_every_depth`, credential-key masking and main AC12 tests: Ledger PASS; unknown settings rejected by path, sensitive keys masked. Shipped examples pass in the suite. |
| F27 | **CLOSED** | Executed budget removal: `2 failed, 1 passed`; reservation removal: `2 failed, 1 passed`; order removal: `3 failed`. All three unmodified history tests pass in the ledger run. |
| F28 | **CLOSED** | Schema-valid acknowledged-run `success` mutation: `1 failed`, at `assert observation.status != 'success'`. Unmodified test alone with `-p no:randomly`: `1 passed in 0.37s`; it records two outcomes. |
| F29 | **CLOSED** | Actions `test_ac5_a_non_finite_cancel_grace_is_refused`: Ledger PASS for NaN/±inf. Finite nonnegative controls pass in the full suite. |
| F30 | **CLOSED** | Audio `test_stop_returns_as_soon_as_the_player_exits_within_the_grace`: Ledger PASS; stop returns within 0.1 seconds of fake process exit despite 5-second grace. Kill/reap control passes in the suite. |
| F31 | **CLOSED** | Viewer-memory `test_ac27_a_memory_name_with_a_trailing_newline_is_foreign`: Ledger PASS; foreign newline filename remains unread, uncounted, undeleted. |
| F32 | **CLOSED** | Mutation storing default bytes instead of supplied bytes: `21 failed`; unmodified bound test and no-tautology guard pass in the ledger run. Rejection assertions retained. |
| F33 | **CLOSED** | Bus `test_retention_copy_failure_is_a_publication_error_with_history_record`: Ledger PASS; controlled PublicationError names the event and failure remains in history. |
| F34 | **OUT-OF-SCOPE** | Explicit brief/spec exclusion of challenged `codex:F6`; no new stalled out-of-tree trace-subscriber experiment or closure claim. |
| F35 | **CLOSED** | Config-UI `test_p17_ac30_a_validator_that_never_returns_releases_the_check_by_the_deadline`, stalled-cleanup and shared-cleanup-budget tests: Ledger PASS; assertions enforce one 121-second release bound. |
| claude:F8 | **OUT-OF-SCOPE** | Explicit brief/spec exclusion: documented minimum-backoff reset after loss. No fix or new closure claim. |
| N1 | **CLOSED** | Actual structural test rejects all four virtual probe-D fixtures, including unrelated redactor call; accepts routed control. It additionally asserts no hook-outcome renderer bypasses the constructor. |
| N2 | **CLOSED** | In-memory replacement of only the UI command with `--config wrong.yaml`: `installation.md FAIL`, `installation.fr.md FAIL`. Original exact UI/run/demo assertions are AST-present again. |
| N3 | **CLOSED** | All five tests named individually in `scaffolding/plan-amendments.md`; independent AST/diff audit confirms extensions and the explained priming-count transition. No omitted sixth assertion-changing test outside the allowlist. |

## Replayed probes A–E

### A — retained identity and authorization

Used the first report's real-agent/fake-peer harness `test_proxy.recorded_host()`. Executed each alteration separately and the original combined alteration, then retried the original frame:

```text
A outside   refused unknown_action    retained_equal=False result_present=False parts=[] executor_calls=1
A principal refused not_authorized    retained_equal=False result_present=False parts=[] executor_calls=1
A arguments refused call_id_conflict  retained_equal=False result_present=False parts=[] executor_calls=1
A combined  refused unknown_action    retained_equal=False result_present=False parts=[] executor_calls=1
A exact     success                   retained_equal=True  result_present=True  parts=[] executor_calls=1
```

The count includes the original successful execution. None of the altered frames entered the executor. Another exact retry after advancing the clock 0.1 seconds and reversing JSON member order printed `success success True`; the execution count remained unchanged for that call. Rendering-collision and running-call waiter controls also passed.

### B — valid call carrying the pairing token

Executed `brain.call('gate-token', 2, run_id=TOKEN)` and collected all bus records and diagnostics:

```text
status=success result_returns=True leaking_records=0 leaking_diagnostics=0
```

Observed trace payloads retained these fields:

```text
action.started: action=screen.capture action_version=1 provider=capture
  call_id=gate-token destination=fake/channel-9/capture nature=read principal=brain
action.completed: action=screen.capture action_version=1 provider=capture
  call_id=gate-token destination=fake/channel-9/capture status=success
  duration_seconds=0.0 error_code=None
both: run_id=peer-run_id-70d53275dc4c4dca
      conversation_id=peer-conversation_id-ac5a5438662e788c
```

Digest suffixes vary by agent instance. Labels agree between the two events. The controls separately cover `conversation_id`, `source_event_id`, `message_id`, 4 KiB values, lone-surrogate correlation values, and a local call retaining its own run/conversation IDs. The provider still receives the original valid call and the result returns.

### C — offline consumer

Called `modules.viewer_memory.__main__.main(['stats', '--config', '/virtual/gate.yaml'])` with exactly the first report's YAML supplied through mocked `Path.read_text`. No store was opened. Observed:

```text
exit=2
stdout=""
stderr="viewer_memory: module 'viewer_memory': field 'validate_settings': is not a setting this module declares\n"
leak=False
```

### D — virtual structural fixtures

Appended a virtual `core/_gate_virtual_fixture.py` through mocked `Path.rglob`/`read_text`, then called the actual `test_ac11_every_configuration_diagnostic_surface_routes_through_the_primitive`. No fixture file was created.

| Body inside `for key in settings` | Observed structural result |
|---|---|
| `raise ValueError(f'settings.{key}: unknown')` | **FAIL**, violation at line 4 |
| `raise ValueError('settings.{}: unknown'.format(key))` | **FAIL**, violation at line 4 |
| `raise ValueError('settings.' + key + ': unknown')` | **FAIL**, violation at line 4 |
| Unrelated `mask_known('fixed', known)`, then raw f-string | **FAIL**, violation at line 5 |
| `raise ValueError(mask_known('settings.' + key, known) + ': unknown')` | **PASS**, violations `{}` |

Each failure named `('core/_gate_virtual_fixture.py', 'check')`. The shipped structural/self-test suite also passes. This closes the demonstrated N1 holes; it is not a proof for every possible Python data-flow spelling (see NOT VERIFIED).

### E — persistent erasure after held completion

Reran `test_ac14_a_run_admitted_before_the_erasure_records_nothing_after_it`, wrapping only `MemoryPlatform.close` to inspect immediately before original teardown. The wrapper read brain recall, the store record, and every file recursively under the store directory:

```json
{"brain_recall_empty": true, "viewer_memory_record_exists": false,
 "erased_text_in_any_store_file": false, "files_scanned": 0,
 "store_refusals": 2, "brain_refusals": 1}
```

Observed `1 passed in 0.16s`. The held run and the erasure-command run are refused by the persistent holder; the brain's existing stale-record refusal remains asserted. The additional `test_f6_the_store_refuses_a_stale_record_and_keeps_later_and_other_keys` writes another key and a later same-key record successfully, with only fresh text remaining. The store-level control also verifies refusal, count, fail-closed service failure, and successful writing with no erasure service.

## TEST TREATMENT

**PASS under the nine-test allowlist and the explicit five-test amendment.**

Independent AST comparison traversed **all 2,184 pre-existing test functions at `8010edc`**, comparing assertion AST multisets with locations excluded. No function disappeared. **Eight functions have changed assertion multisets**: three allowlisted functions and exactly the five amended functions. The six allowlisted Twitch functions retain their assertion ASTs. A second comparison against `0f335bc` covered **2,290 first-pass test functions**: no deletion and only four assertion changes, all additions (installation +3, structural invariant +1, registry checks +2, YouTube +2). Thus a reduction to a test introduced earlier in phase 6 was not hidden by the older baseline.

Also inspected the complete test diff, removed executable assertion/raises lines, changed bodies/setup and decorators. No added `skip`, `xfail`, or `importorskip`; no deleted test; no removed expected-exception check; no observed loosening. The only existing-test parametrization change expands installation coverage from two guides to guides plus README. Fixture changes declare their own settings, provide grants needed to reach Twitch's send behavior, adjust Kick's legitimate window/clock setup, or insert YouTube's priming page; assertions still exercise their intended paths.

The nine allowlisted tests all retain their required docstring pair:

| Test (`tests/` prefix omitted) | Finding/requirement | Audit |
|---|---|---|
| `test_twitch.py::test_chat_send_uses_configured_identity_and_preserves_event` | F1/R1 | Assertions unchanged |
| `test_twitch.py::test_send_failure_continues_chain_and_next_send_succeeds` | F1/R1 | Assertions unchanged |
| `test_twitch.py::test_pending_send_releases_response_on_timeout_cancellation_and_close` | F1/R1 | Assertions unchanged |
| `test_twitch.py::test_notification_mapping_dedup_and_retry_reconnect` | F1/R1 | Assertions unchanged |
| `test_twitch.py::test_lost_sent_trace_neither_retries_nor_repeats_the_send` | F1/R1 | Assertions unchanged |
| `test_twitch.py::test_confirmed_outcome_never_waits_for_the_sent_trace` | F1/R1 | Assertions unchanged |
| `test_installation_doc.py::test_the_guide_prints_the_commands_the_code_still_has` | F18/R8 | All four original assertion ASTs retained; three added; command resolution and README coverage added |
| `test_automation_bridge.py::test_ac16_no_run_observation_is_a_success` | F28/R9 | Replaces vacuous global outcome check with two local outcomes and per-outcome assertions; success mutation fails |
| `test_bus.py::test_absent_or_non_finite_limit_is_rejected_naming_the_setting` | F32/R9 | Replaces constructor tautology with exact stored limits; surrounding refusal retained; wrong-default mutation fails |

### Amendment audit (N3)

Each of the following has its own entry, explanation and extension evidence in `scaffolding/plan-amendments.md`. I checked the code diff rather than accepting the entries' accounting:

| Amended test | Observed extension |
|---|---|
| `test_brain.py::test_manifest_declares_v2_shape_settings_hook_and_no_grant` | Exact properties set adds optional `max_response_bytes`; added integer/minimum assertion. Old members, required set and remaining checks retained. 35 → 36 assertion nodes. |
| `test_proxy.py::test_agent_link_manifest_declares_the_input_role_no_event_and_no_action` | Exact properties set adds `principal`, `max_running_calls`, `max_duplicate_waiters`; all old members and other predicates retained. 14 → 14 assertion nodes. |
| `test_proxy.py::test_agent_link_validate_settings_accepts_loopback_ws_and_wss_anywhere` | Full input adds those three settings; principal and paired-bound checks added. All seven old assertion ASTs retained; 7 → 9 nodes. The amendment calls these “three assertions”; syntactically there are two statements, checking three settings. No coverage reduction is concealed by that wording. |
| `test_proxy.py::test_agent_link_validate_settings_refuses_bad_entries_and_non_finite_bounds` | Adds three bad input values and exactly their three refused field names to the exact expected set. Old bad inputs, refused names and prefix/nonmapping checks retained. 3 → 3 nodes. |
| `test_youtube.py::test_ac39_three_platforms_on_one_runtime` | Old `list == 1` assertion remains before advancing the clock; adds `live_sessions == 1` at that point, then one poll and `list == 2`. All 21 old assertion ASTs retained, two added. The extra list is required by F11 priming; later two-session, queue, binding and zero-write checks remain. |

Exact-set changes extend the declared input/schema universe and keep equality strict; they do not relax it to subset inclusion. YouTube's increase is paired with the retained pre-poll count and a behavioral zero-admission check. No amendment entry hides a removed assertion or an unexplained raised count.

### Executed sensitivity mutations

Every product mutation below used a fresh interpreter and a `SourceFileLoader.get_code` override, including loader-imported modules. Original source bytes were never changed.

| Target | Mutation | Observed output |
|---|---|---|
| F27 three named history tests | `if input_tokens + cost > history_limit:` → `if False:` | `2 failed, 1 passed in 0.20s` |
| Same | `history_limit = int(limit * _HISTORY_TOKEN_SHARE)` → `history_limit = limit` | `2 failed, 1 passed in 0.20s` |
| Same | `kept.reverse()` → `pass` | `3 failed in 0.23s`, role/content order assertions fail |
| F28 test alone | Acknowledged `_RunProvider.invoke` returns valid `ActionObservation(status='success', provenance=_run_provenance(), result={})` | `1 failed in 0.32s`, at `assert observation.status != 'success'` |
| F32 limit test | Constructor stores `DEFAULT_HISTORY_MAX_BYTES` instead of supplied `history_max_bytes` | `21 failed in 0.10s`, exact history-limit equality fails |
| N2 English/French command test | Virtual UI command uses `--config wrong.yaml` | Both **FAIL** |

Unmodified history/bus tests passed in the 133-case run; unmodified F28 also passed alone. Probe D supplies the structural negative and positive controls. No conclusion depends on an invalid success observation being converted to another error by the executor.

## REQUIREMENT MAP

Names below are tests actually executed. PASS refers to the requirement's tested behavior, subject to the stated external-verification limits. A literal acceptance-criterion failure is identified explicitly.

| Requirement | Named running tests (under `tests/`) | Result |
|---|---|---|
| R1 | `test_twitch.py::test_ac1_an_ungranted_compatibility_send_is_refused_by_the_executor`, `test_ac2_the_compatibility_send_is_traced_as_an_executor_call`; `test_actions.py::test_ac3_a_reused_call_id_naming_another_call_is_refused`, `test_ac3_an_identical_reuse_the_policy_now_refuses_is_refused`, `test_ac4_concurrent_identical_calls_reach_the_provider_once`, `test_ac5_a_non_finite_cancel_grace_is_refused`; probe A | **FAIL literal AC2; other tested behavior PASS.** Independent granted and ungranted compatibility publications both print `action.requested=0, action.started=1, action.completed=1`. The test asserts canonical `action.started`; it does not enforce the specification's event name. |
| R2 | `test_proxy.py::test_p6_ac6_a_call_outside_the_negotiated_set_never_reaches_the_executor`, `test_p6_ac7_the_frame_principal_is_compared_never_executed_under`, `test_p6_ac8_peer_strings_never_enter_diagnostics_or_supervision_records`, `test_p6_ac8_peer_call_fields_never_enter_supervision_records`, `test_p6_ac9_running_executions_and_duplicate_waiters_are_bounded`, `test_p6_f2_f3_a_reused_call_id_naming_another_call_reads_nothing_retained`; A/B | **PASS**: fresh/cached pairing checks, bounded work/waiters, peer-text-free diagnostics/supervision, returned result and local trace control. |
| R3 | Loader `test_ac10_a_hook_diagnostic_never_names_another_modules_credential`, `test_ac10_a_schema_diagnostic_masks_a_credential_used_as_a_key`; main `test_ac10_check_config_never_echoes_a_credential_used_as_a_key`, `test_ac12_main_check_config_exits_non_zero_on_an_unknown_key`; redaction `test_ac11_every_configuration_diagnostic_surface_routes_through_the_primitive`, `test_ac11_probe_d_every_spelling_fails_the_real_structural_test`, `test_f4_probe_c_the_offline_command_never_echoes_a_credential_key`; C/D | **PASS** for current consumers and required structural probes; field/reason rendering is guarded through `hook_diagnostics`, unknown keys refused, examples and vocabulary controls pass. Static-analysis limits remain below. |
| R4 | Brain `test_ac13_forgetme_empties_the_brain_memory_of_that_key_only`, `test_ac14_a_run_admitted_before_the_erasure_records_nothing_after_it`, `test_f6_the_store_refuses_a_stale_record_and_keeps_later_and_other_keys`; viewer-memory `test_f6_a_record_the_erasure_does_not_admit_writes_nothing`; E | **PASS**: brain and persistent holders reject stale records, no erased file/text survives, other/fresh records retained, refusal observable. |
| R5 | Bridge `test_ac15_phase6_a_raising_reconcile_trace_leaves_the_owner_alive`; Twitch `test_ac16_an_authentication_refusal_of_the_chat_subscription_ends_recovery`, `test_ac17_a_socket_silent_past_its_keepalive_is_recovered`; YouTube `test_ac18_the_first_page_of_a_fresh_resolution_admits_nothing`, `test_ac18_the_first_page_after_a_re_lookup_admits_nothing` | **PASS**: supervision failure recovery, auth-stop, keepalive recovery and fresh/relookup priming. |
| R6 | Brain `test_ac19_a_body_one_byte_over_the_bound_fails_the_run_undecoded`, `test_ac19_a_body_exactly_at_the_bound_is_decoded_and_delivered`, `test_ac21_arguments_nested_10000_levels_are_malformed_and_the_run_goes_on`; adapter deep-argument test; proxy AC20 cap/deadline tests; admission ready-structure test; Twitch AC23; Kick AC24 window/replay-span tests | **PASS**: byte/recursion/admission/text/replay bounds and valid boundary controls. |
| R7 | Viewer-memory `test_ac25_a_write_failing_for_space_evicts_nothing`, `test_ac25_a_rename_failing_after_evictions_publishes_their_exact_count`, `test_ac26_a_leftover_temporary_file_is_counted_then_reclaimed`, `test_ac27_a_memory_name_with_a_trailing_newline_is_foreign`; bus retention-copy test; audio early-exit stop test; all three config-UI `test_p17_ac30_*` tests | **PASS**: exact failure accounting, temporary scan, foreign names, controlled bus errors, measured stop timing, shared Check release deadline. |
| R8 | Installation `test_the_guides_endpoint_values_are_complete_chat_completions_urls`, `test_the_guides_profile_posts_to_the_guides_endpoint`, `test_the_documents_name_the_overlay_the_code_derives`, `test_the_guide_prints_the_commands_the_code_still_has`, `test_the_readme_config_ui_command_runs`; wrong-profile mutations | **PASS**: URLs, runtime POST, overlay, command resolution/help, preserved profile argument on both guides. |
| R9 | Three history tests; isolated bridge `test_ac16_no_run_observation_is_a_success`; bus `test_absent_or_non_finite_limit_is_rejected_naming_the_setting`, `test_no_constructor_result_is_asserted_merely_not_none` | **PASS**: all required defect mutations fail and unmodified controls pass. Isolated bridge test records nonempty outcomes. |
| R10 | 70 ledger nodes/133 cases; full suite; `test_phase5_docs_status.py`; independent two-baseline AST audit, amendment review and mutations | **FAIL closing-gate half**, solely because literal AC2 remains unresolved. Test treatment and head regressions pass. Historical pre-fix evidence was read in the first report/ledger, not reexecuted as an installed baseline in this pass; that fresh baseline certification is **NOT-VERIFIABLE from these runs**. This report supplies current dispositions for all findings. |

Phase-5 guarantees remain supported by running tests: `test_input_ownership.py::test_ac2_no_shipped_profile_names_the_bridge_or_an_inputs_key` (optional bridge), `test_ac5_a_twitch_event_is_dropped_while_the_owning_bridge_is_down` and `test_the_gated_bindings_never_overlap` (replacement without parallel input), `test_ac14_the_real_users_and_bridge_split_the_viewer_read` (one owner); bridge `test_ac21_a_principal_without_a_rule_is_refused_before_the_provider` (default-deny), acknowledged/no-acknowledgement/cancellation rows and isolated AC16 (honest `external_unknown`); `test_phase5_docs_status.py::test_ac37_no_document_calls_the_bridge_delivered` (“not delivered”). These pass in the full suite; the status file is unchanged.

## NEW FINDINGS

**None demonstrated in P20F1–P20F5.** Executed controls found no exact-retry refusal, boolean/enum vocabulary corruption, missing local supervision fields, loss of a legitimate later/other-key record, or concealed assertion reduction. This is a bounded result of the exercised controls, not a claim that all inputs and interleavings have been exhausted.

The remaining AC2 discrepancy is a **minor specification/implementation conformity issue already recorded in the first report**, with the executed event-count counterexample above. It is not relabeled as a newly introduced defect. F1's executor-routing security fix remains closed.

## NOT VERIFIED

- No real platform account, Streamer.bot/OBS instance, model service, audio device, installed-wheel smoke test, load test, or durability-after-process-kill experiment. The bridge remains **not delivered**.
- No fresh installed `8010edc` product run or replay of every historical commit's raw evidence. The first report's pre-fix overlay limitations still apply; historical subprocess isolation is not newly certified here.
- No exhaustive mutation of every assertion or all possible Python diagnostic constructions. The structural checker still documents limitations involving cross-file builders/helpers, container/attribute data flow and unrecognized hook resolution. Required C/D probes and current-tree consumers pass; arbitrary future source transformations are not proven safe.
- No random-order/xdist session. F28 was run alone with randomization disabled and under the required valid-success mutation.
- The bounded aiohttp stream and test streams were exercised; every possible injected response object taking the nonstream `response.json()` fallback was not.
- Offline viewer-memory stats are record statistics, not a proof of universal offline temporary-file cleanup. Runtime capacity/scan behavior is the F22 evidence.

The five demanded fixes and all three evidence repairs pass. An explicit AC2 alignment is still needed before the phase can receive an unqualified closing **APPROVE**.

**REQUEST_CHANGES**
