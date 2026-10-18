# Phase 6 — full-branch closing review

## VERDICT: REQUEST_CHANGES

Reviewed `main` at `5027c930e6fe8660f43eb4d91bc34f919d22f5e9` against `8010edc`, on 2026-10-18. **29 in-scope findings CLOSED; F2, F3, F4, F5 and F6 OPEN; F34 and `claude:F8` OUT-OF-SCOPE.** The suite is green, but the trust, redaction and erasure boundaries remain incomplete. AC38 also fails independently: assertions changed outside the nine-test allowlist, and an allowlisted replacement lost an existing guarantee. Phase 6 does not pass its closing gate.

I read the original adversarial review, brief, specification, plan, all three hand-verification JSON files and phase summary. The third hand verification approves the specification after the earlier contradictions were repaired; it does not waive the final prohibition on weaker assertions or expand the nine-test allowlist.

Only this report was written in the repository. No staging, commits, checkout, source edits or test edits were performed. The initially untracked `docs/campaigns/phase6/scaffolding/campaign-summary.md` was left alone. Test-created temporary fixtures are outside the repository; bytecode and pytest cache writes were disabled.

## Evidence method and observed runs

The review supplies descriptions and expected counterexamples, rather than complete runnable scripts. I reran the named regressions exercising those reproductions, inspected their actual assertions, and added independent probes of sibling paths. A passing narrow regression does not close a boundary when an independent probe still reproduces its defect.

Observed commands/results:

```text
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/ -q -p no:cacheprovider
3849 passed, 26 skipped in 254.48s (0:04:14)

All 70 distinct named regression nodes extracted from the phase-summary finding ledger:
133 passed in 9.15s

tests/test_phase5_docs_status.py + tests/test_redaction_surfaces.py
+ the two short-secret boolean/enum vocabulary controls:
58 passed in 25.40s

git diff --quiet 8010edc HEAD -- tests/test_phase5_docs_status.py
exit 0
```

The complete suite includes the phase-5 optional-bridge, ownership, default-deny, replacement-event and uncertainty regressions. Its 26 skips are existing skips, not additions by this phase. The unchanged phase-5 status guard accounts for 27 passing cases and continues to require “not delivered”.

For the pre-fix check, I loaded `git archive 8010edc` into memory and overlaid its `core/`, `modules/`, manifests, installation guides, README and pyproject reads/imports while running the current regression tests. No extraction or worktree was created. As in the phase ledger, the only compatibility additions were the names `COMPATIBILITY_PRINCIPAL`, `KEEPALIVE_MARGIN_SECONDS`, and a collection-only `_identity` stub. None implements a fix. All 31 product-finding selections failed in that overlay; the counts appear below. Some baseline failures occur at a new setting/attribute rather than at the defective behavior, explicitly identified below. This is supplemental sensitivity evidence, not an installed pre-fix product trial; subprocesses are discussed under NOT VERIFIED.

All mutations used an in-memory `SourceFileLoader.get_code` override, including dynamically loaded modules. Each mutated subprocess ended before the unmodified control ran. Repository source bytes were never modified. For the structural test, mocked `Path.rglob`/`Path.read_text` added a virtual fixture to the real source scan; no fixture file was created.

## FINDINGS

“Head pass” below means the named regression was executed successfully in the 133-case focused run (and the full suite). The behavior after the colon is asserted by that running test, or printed by the independent probe identified in the counterexamples section. Test paths are relative to `tests/`. Baseline counts are observed, not copied from the summary.

- **F1 CLOSED** — `test_twitch.py::test_ac1_an_ungranted_compatibility_send_is_refused_by_the_executor`, `::test_ac1_without_an_executor_the_compatibility_route_refuses`, `::test_ac2_the_compatibility_send_is_traced_as_an_executor_call`: head pass; independent output: ungranted POSTs/provider entries `0/0`, metadata `failed/refused`; granted `1/1`, metadata `sent/success`. Baseline: **3 failed**. Actual trace-name discrepancy with literal AC2 is recorded below.
- **F2 OPEN** — AC6/AC7 fresh-call tests pass, but replay probe A sends the retained `(id, seq)` with an out-of-set action and another principal and receives **`success`, identical to the original observation**, rather than a refusal. The agent cache precedes both new checks. Baseline fresh-call regressions: **3 failed**.
- **F3 OPEN** — `test_actions.py::test_ac3_a_reused_call_id_naming_another_call_is_refused` (five identity components), `::test_ac3_a_reuse_whose_arguments_only_share_a_repr_is_refused`, and `::test_ac3_an_identical_reuse_the_policy_now_refuses_is_refused` pass at the executor; baseline **7 failed**. The same observation-before-authorization defect survives in agent replay probe A: an ungranted principal reads the retained success without entering the executor.
- **F4 OPEN** — loader/CLI-check/UI/supervision AC10 regressions pass; baseline **26 failed, 3 passed**. Offline CLI probe C still prints `field 'review-secret-4521'` to stderr for a credential used as a key. Structural mutation D also lets three equivalent unredacted surfaces pass AC11, so the class is not closed by construction.
- **F5 OPEN** — `test_proxy.py::test_p6_ac8_peer_strings_never_enter_diagnostics_or_supervision_records` passes for error frames (token and 4 KiB); baseline **2 failed**. Probe B instead places the pairing token in a valid call's `run_id`: both **`action.started` and `action.completed` supervision records contain `pairing-secret-token` verbatim**.
- **F6 OPEN** — `test_brain.py::test_ac13_forgetme_empties_the_brain_memory_of_that_key_only` and `::test_ac14_a_run_admitted_before_the_erasure_records_nothing_after_it` pass for conversation memory; baseline **2 failed**. Probe E observes **empty brain recall, persistent record present, erased text present** after the held run finishes. The old viewer text and reply are recreated on disk.
- **F7 CLOSED** — `test_actions.py::test_ac4_concurrent_identical_calls_reach_the_provider_once`, `::test_ac4_a_concurrent_reuse_naming_another_call_is_refused`, `::test_ac4_a_cancelled_first_caller_still_settles_its_waiter`: head pass; identical concurrent calls produce **one provider/handler entry**, equal terminal outcomes, and cancellation settles the waiter. Baseline: **3 failed**, including `assert 2 == 1` provider invocations. The full suite also runs the independently cancelled-waiter control.
- **F8 CLOSED** — `test_automation_bridge.py::test_ac15_phase6_a_raising_reconcile_trace_leaves_the_owner_alive`, `::test_ac15_phase6_a_permanently_raising_trace_keeps_the_backoff`: head pass; raising emit leaves the owner alive, clears/closes the failed connection, degrades and reconnects with **connect count 2**; permanent failure keeps capped backoff. Baseline: **2 failed**, including a finished owner task with `RuntimeError('supervision unavailable')`. The event consumer catches per-event exceptions; its unguarded inner trace call does not by itself kill that consumer.
- **F9 CLOSED** — `test_twitch.py::test_ac16_an_authentication_refusal_of_the_chat_subscription_ends_recovery` (401/403): head pass; **one degraded event**, authentication reason, **one recovery socket and one recovery POST over 60 simulated seconds** (two total including initial startup). Baseline: **2 failed**, additional sockets. Startup's terminal refusal is not the reviewed endless-recovery defect; the transient-503 control still recovers.
- **F10 CLOSED** — `test_twitch.py::test_ac17_a_socket_silent_past_its_keepalive_is_recovered`: head pass; no early reconnect, then recovery after **10 s + fixed margin**, with diagnostic. Baseline: **1 failed**, recovery condition never became true. The replacement-socket/shutdown controls pass in the suite.
- **F11 CLOSED** — `test_youtube.py::test_ac18_the_first_page_of_a_fresh_resolution_admits_nothing`, `::test_ac18_the_first_page_after_a_re_lookup_admits_nothing` (403/404): head pass; **three existing messages admit zero, the next new message admits one**; re-lookup primes again. Baseline: **3 failed**, backlog events admitted. The rate-limit-403 control keeps the current chat/page rather than priming away valid new input.
- **F12 CLOSED** — `test_brain.py::test_ac19_a_body_one_byte_over_the_bound_fails_the_run_undecoded`, `::test_ac19_a_long_body_is_read_only_to_one_byte_past_the_bound`, `::test_ac19_a_body_exactly_at_the_bound_is_decoded_and_delivered`: head pass; **bound+1 bytes read, zero decoder calls** above the bound; exactly-at-bound decodes once and delivers. Baseline: **3 failed**. The probe-size test also passes in the full suite; the non-stream test-double fallback is a verification limit below.
- **F13 CLOSED** — `test_proxy.py::test_p6_ac20_a_third_unpaired_connection_beyond_the_cap_of_2_is_refused`: head pass; **third refused at cap 2**, pairing/close free slots. Baseline: **1 failed at unknown new setting**; additional behavioral mutation removing the cap: **1 failed**, “the third unpaired connection was accepted”; unmodified control **1 passed**.
- **F14 CLOSED** — `test_proxy.py::test_p6_ac9_running_executions_and_duplicate_waiters_are_bounded`: head pass; **two running calls, third refused, duplicate waiters bounded**, valid calls complete. Baseline: **1 failed at unknown bound settings**. Removing the running guard and waiter guard separately each yields **1 failed** (expected refusal replaced by a later timeout/observation); each unmodified control **1 passed**.
- **F15 CLOSED** — `test_installation_doc.py::test_the_guides_endpoint_values_are_complete_chat_completions_urls`, `::test_the_guides_profile_posts_to_the_guides_endpoint`: head pass for both guides; values end in **`/chat/completions`**, fake-backed startup POSTs to exactly the guide's URL. Baseline: **4 failed**, including `https://api.openai.com/v1` and `http://localhost:11434/v1`.
- **F16 CLOSED** — `test_installation_doc.py::test_the_readme_config_ui_command_runs`: head pass; printed command with `--help` exits **0**, usage names `--config`. Baseline: **1 failed**, `No module named twitch-ia-compagnon-config-ui`.
- **F17 CLOSED** — `test_installation_doc.py::test_the_documents_name_the_overlay_the_code_derives`: head pass for both guides and README; code-derived filename **`mon-compagnon.local.yaml`**. Baseline: **3 failed**, documented `config.local.yaml`.
- **F18 CLOSED** — `test_installation_doc.py::test_the_guide_prints_the_commands_the_code_still_has` now resolves printed commands against code and includes README; its pre-fix-command negative control passes. Baseline selection: **1 failed, 3 passed**, rejecting the nonexistent README module. This closes the original grep-only defect; its separate loss of the guide's exact UI-profile assertion is an AC38 violation and new finding N2 below.
- **F19 CLOSED** — `test_proxy.py::test_p6_ac20_a_silent_connection_is_closed_at_the_hello_deadline_and_not_one_tick_before`, `::test_p6_ac20_a_peer_that_stops_reading_cannot_hold_its_slot_past_the_hello_deadline`: head pass; open before deadline, **closed at deadline, slot count zero**, blocked writes cannot retain the slot. Baseline: **2 failed at new settings**. Moving the deadline to 10,000 s: **1 failed**, “the silent connection outlived the hello deadline”; unmodified control **1 passed**. A valid paired connection outlives the hello deadline in the suite.
- **F20 CLOSED** — `test_brain.py::test_ac21_arguments_nested_10000_levels_are_malformed_and_the_run_goes_on`, `test_model_adapter.py::test_ac21_probe_arguments_nested_10000_levels_raise_brain_module_error`: head pass; **`malformed_arguments` without `run_failed`**, probe raises **`BrainModuleError`**. Baseline: **2 failed**, run failed/uncaught recursion.
- **F21 CLOSED** — `test_viewer_memory.py::test_ac25_a_write_failing_for_space_evicts_nothing`, `::test_ac25_a_rename_failing_after_evictions_publishes_their_exact_count`, `::test_ac25_a_use_whose_rename_fails_carries_its_evictions`: head pass; ENOSPC before commit deletes nothing; later rename failure carries/publishes **exact actual deletion count**. Baseline: **3 failed**, deleted prior record/raw OSError.
- **F22 CLOSED** — `test_viewer_memory.py::test_ac26_a_leftover_temporary_file_is_counted_then_reclaimed`, `::test_ac26_a_write_cut_short_counts_its_kept_temporary_at_its_real_size`, `::test_ac26_a_scan_reclaims_a_temporary_file_and_never_reads_it`: head pass; **1,000 temporary bytes counted while deletion is refused, removed on next successful scan, never parsed as a record**. Baseline: **3 failed**. Offline record-only `stats` is not a runtime capacity scan; broader offline cleanup is not established here.
- **F23 CLOSED** — `test_admission.py::test_expired_and_evicted_sessions_never_accumulate_in_the_ready_structure`: head pass; across **12 removals at max_sessions=4**, ready length stays ≤4 and ends at zero; later sessions execute FIFO. Baseline: **1 failed**, `assert 5 <= 4`.
- **F24 CLOSED** — `test_twitch.py::test_ac23_chat_write_refuses_text_over_500_characters_before_any_request`, `::test_ac23_the_compatibility_route_reaches_the_500_character_check`: head pass; **501 refused/zero POSTs, 500 Unicode characters sent/one POST**; compatibility also refuses 501. Baseline: **2 failed**, success instead of refusal.
- **F25 CLOSED** — `test_kick.py::test_ac24_the_dedup_window_is_never_shorter_than_twice_the_tolerance`: head pass; **300 refused, 600 accepted**, schema/default cover 600 s. Baseline: **1 failed**, 300 accepted. The full-suite `::test_ac24_a_replay_across_the_whole_tolerance_span_is_deduplicated` asserts **200 initially at +290 s, 409 replay 580 s later, 200 fresh ID at that same signed timestamp**, with zero stale-timestamp refusals.
- **F26 CLOSED** — loader `test_f26_an_undeclared_setting_is_refused_by_its_path_at_every_depth`, `::test_f26_a_credential_used_as_an_unknown_key_is_masked` and main AC12 tests: head pass; misspelled/nested and top-level unknown keys refuse by path, credential keys masked. Baseline: **10 failed**. All shipped-example validation cases pass in the full suite.
- **F27 CLOSED** — three history tests named in the mutation table below: remove budget **2 failed/1 passed**; remove 50% reservation **2 failed/1 passed**; remove `kept.reverse()` **3 failed**; each unmodified control **3 passed**. Assertions now pin budget, output reservation and chronological order.
- **F28 CLOSED** — `test_automation_bridge.py::test_ac16_no_run_observation_is_a_success`, run alone: acknowledged-run mutation returning a schema-valid **`success` with result `{}` gives 1 failed at `assert observation.status != 'success'`**; unmodified run alone with `-p no:randomly` **1 passed**, independently records two outcomes `external_unknown/error`.
- **F29 CLOSED** — `test_actions.py::test_ac5_a_non_finite_cancel_grace_is_refused` (NaN/±inf), `::test_ac5_a_finite_non_negative_cancel_grace_is_accepted` (0/0.5): head pass; non-finite values raise `ContractError` naming the bound, finite controls accepted. Baseline non-finite selection: **3 failed**; NaN/+inf did not raise, -inf already failed positivity but lacked finite-value wording.
- **F30 CLOSED** — `test_audio_output.py::test_stop_returns_as_soon_as_the_player_exits_within_the_grace`: head pass; fake exits **0.01 s after SIGTERM**, stop returns **≤0.1 s after recorded exit**, grace 5 s, no kill. Baseline: **1 failed** after the 2 s watchdog (“stop waited out the grace”). The non-exiting-player kill/reap control also passes.
- **F31 CLOSED** — `test_viewer_memory.py::test_ac27_a_memory_name_with_a_trailing_newline_is_foreign`: head pass; `<64 hex>.json\n` survives **unread, undeleted, uncounted**. Baseline: **1 failed**, foreign name scanned.
- **F32 CLOSED** — `test_bus.py::test_absent_or_non_finite_limit_is_rejected_naming_the_setting`: mutation storing default bytes instead of the supplied 4096 gives **21 failed**, actual **8388608 != 4096**; unmodified **21 passed**. `::test_no_constructor_result_is_asserted_merely_not_none` also passes; rejection assertions remain intact.
- **F33 CLOSED** — `test_bus.py::test_retention_copy_failure_is_a_publication_error_with_history_record`: head pass; uncopyable payload raises **`PublicationError` naming the event**, failure retained in history. Baseline: **1 failed**, raw `TypeError: cannot copy this value`.
- **F34 OUT-OF-SCOPE** — challenged `codex:F6`, explicitly excluded by brief/spec; stalled out-of-tree trace subscriber not treated as a closure obligation.
- **F35 CLOSED** — three `test_config_ui.py::test_p17_ac30_*` timeout/shared-budget regressions named below: head pass, **shared release bound 121 s = loader 120 s + 1**, timeout reported. Baseline: **3 failed at missing new helpers**. Adding a fresh 1 s to the remaining cleanup budget: **2 failed**, observed **122 > 121**; unmodified controls **2 passed**.
- **claude:F8 OUT-OF-SCOPE** — backoff reset after connection loss is documented behavior, challenged and explicitly excluded; no fix required.

## Executed counterexamples for the open boundaries

### A — agent replay bypasses the pairing checks (F2/F3)

Executed with the in-tree fake peer and real executor:

```python
import asyncio, sys
sys.path.insert(0, 'tests')
import test_proxy as t

async def probe():
    host, brain, recorder = await t.recorded_host()
    try:
        await brain.call('gate-replay', 1)
        original = await brain.observation('gate-replay')
        await brain.call('gate-replay', 1, principal='ungranted-attacker',
                         action=t.FAKE_WRITE, arguments={'text': 'changed'})
        forged = await brain.observation('gate-replay')
        print(original['status'], forged['status'],
              forged == original, len(recorder.calls))
    finally:
        await host.close()

asyncio.run(probe())
```

Observed: **`success success True 1`**. The second request is outside the negotiated set and names an ungranted principal, yet reads the first request's observation. `AgentLinkModule._on_call` returns the retained frame on `(id, seq)` before `_action_call`, the negotiated-action check and the pairing-principal check. The fixed executor cannot protect a call that this sibling cache never forwards. Exact retries must still work; altered retries must be checked against an immutable identity before retained results or waiter attachment are exposed.

### B — peer text survives in supervision (F5)

On another valid call in the same real-agent harness:

```python
await brain.call('gate-token', 2, run_id=t.TOKEN)
await brain.observation('gate-token')
leaks = [e for e in host.bus.list_events()
         if t.TOKEN in json.dumps(e, default=str)]
```

Observed two records:

```text
action.started:   call_id=gate-token, run_id=pairing-secret-token
action.completed: call_id=gate-token, run_id=pairing-secret-token, status=success
```

These are executor supervision emissions originating from a received peer frame. Fixing only `_on_error_frame` does not satisfy R2's prohibition on any peer free text in diagnostics/supervision. No token was printed from an actual configuration; this is the test harness's fixed token.

### C — offline settings consumer still leaks a credential key (F4)

Executed `modules.viewer_memory.__main__.main(['stats', '--config', '/virtual/gate.yaml'])` with `Path.read_text` supplying this in-memory YAML document (no file/store access):

```yaml
modules:
  streamerbot:
    password: review-secret-4521
  viewer_memory:
    directory: /tmp/phase6-unused-memory
    review-secret-4521: 1
```

Observed **exit 2**, stderr:

```text
viewer_memory: module 'viewer_memory': field 'review-secret-4521': is not a setting this module declares
```

`_memory_directory` joins a module hook's raw diagnostics and `main` prints them. This caller does not pass through the loader's credential guard. A structural rule that treats hook reachability as sufficient misses this second reporter. The original `--check-config` reproduction is fixed; this sibling consumer keeps the redaction finding open.

### D — AC11 catches its chosen spelling, misses equivalent surfaces (F4; N1)

A virtual `core/_gate_virtual_fixture.py` was appended to the real scan. I called the actual `test_ac11_every_configuration_diagnostic_surface_routes_through_the_primitive`, without changing that test:

| Fixture body, inside `for key in settings` | Structural-test output |
|---|---|
| `raise ValueError(f'settings.{key}: unknown')` | **FAIL**, violation `('core/_gate_virtual_fixture.py', 'check'): [3]` |
| `raise ValueError('settings.{}: unknown'.format(key))` | **PASS**, violations `{}` |
| `raise ValueError('settings.' + key + ': unknown')` | **PASS**, violations `{}` |
| Same raw f-string, preceded by unrelated `safe = mask_known('fixed', known)` | **PASS**, violations `{}` |

The first is the required negative control and really fails. The others prove that the general structural contract is false. The current heuristic recognizes selected f-strings and considers any redactor call in a function sufficient, regardless of which diagnostic it sanitizes. Its docstring acknowledges these limits; acknowledgment does not satisfy the brief's boundary-wide requirement.

### E — erasure's late-record test passes while disk text returns (F6)

I reran `test_ac14_a_run_admitted_before_the_erasure_records_nothing_after_it` with a pytest collection hook wrapping `MemoryPlatform.close`. Immediately before the existing teardown, the wrapper read `harness.stored()` and `harness.brain.memory.recall(memory_key())`. It did not change execution or assertions.

Observed **`1 passed in 0.24s`**, plus:

```json
{"brain_recall_empty": true,
 "viewer_memory_record_exists": true,
 "erased_text_in_persistent_record": true}
```

The stored notes included:

```json
{"viewer_text":"my secret words","reply_text":"your secret reply","delivery":"confirmed"}
```

They also included the later `!forgetme` record. `_record_memory` still invokes `memory.record` after the erasure; only `ConversationMemory.remember` checks the epoch. The reviewed boundary was erasure reaching every holder and refusing records from already admitted runs. The persistent holder must reject that stale write too.

## TEST TREATMENT

**FAIL (AC38).** I compared `git diff 8010edc HEAD -- tests` and the ASTs of all existing test functions, not just the summary's accounting.

- No added `skip`, `xfail` or `importorskip`; no deleted test. Removed assertion statements belong to the three allowlisted replacements, but one replacement loses an old predicate, as shown below.
- All nine allowlisted tests' docstrings cite their required finding/requirement pair: six Twitch tests **F1/R1**, installation **F18/R8**, bridge **F28/R9**, bus **F32/R9**.
- The six allowlisted Twitch tests keep their assertion ASTs unchanged; grant/setup changes and docstrings are the adaptation. The bridge and bus replacements are non-vacuous under the executed mutations.
- **Five existing tests outside the allowlist have changed assertion ASTs:** `test_brain.py::test_manifest_declares_v2_shape_settings_hook_and_no_grant`; `test_proxy.py::test_agent_link_manifest_declares_the_input_role_no_event_and_no_action`; `::test_agent_link_validate_settings_accepts_loopback_ws_and_wss_anywhere`; `::test_agent_link_validate_settings_refuses_bad_entries_and_non_finite_bounds`; `test_youtube.py::test_ac39_three_platforms_on_one_runtime`. The schema/validation tests extend exact expected sets or add checks; YouTube changes expected list count **1 → 2** after priming. These may be sensible adaptations, but the binding allowlist does not authorize them. I do not expand it as reviewer.
- Kick's window/clock changes, the additional Twitch grant fixture, YouTube priming fixtures, and loader/config-UI fixture-schema declarations are setup changes with unchanged assertions. They are distinguished from those five assertion changes.
- **The F18 replacement is weaker in one independent dimension.** I changed only the guide text returned in memory from `python -m core.config_ui --config mon-compagnon.yaml` to `python -m core.config_ui --config wrong.yaml`. For **both English and French**, the replacement test **PASSed** and the old exact assertion was **False**. Command resolution adds useful coverage but does not preserve the profile-argument guarantee. The allowlist permits replacement, not weakening.
- The unchanged phase-5 docs guard passes; full suite **3849 passed, 26 skipped**. Green counts do not waive either violation.

### Required test-defect mutations (AC34/AC35/AC37)

| Finding and running test | In-memory product mutation | Observed mutated result | Unmodified result |
|---|---|---|---|
| F27: `test_brain.py::test_history_beyond_the_token_budget_is_dropped`, `::test_history_leaves_half_the_token_budget_for_the_output`, `::test_history_reaches_the_prompt_in_chronological_order` | Replace `if input_tokens + cost > history_limit:` with `if False:` | **2 failed, 1 passed**, including `assert 10 < 10` | **3 passed** |
| Same F27 tests | Replace `history_limit = int(limit * _HISTORY_TOKEN_SHARE)` with `history_limit = limit` | **2 failed, 1 passed** | **3 passed** |
| Same F27 tests | Replace `kept.reverse()` with `pass` | **3 failed**, reversed role/content order | **3 passed** |
| F28: `test_automation_bridge.py::test_ac16_no_run_observation_is_a_success` alone | Acknowledged `_RunProvider.invoke` branch returns `ActionObservation(status='success', provenance=_run_provenance(), result={})` | **1 failed**, actual `success` reaches `assert observation.status != 'success'` | **1 passed**, also alone with `-p no:randomly` |
| F32: `test_bus.py::test_absent_or_non_finite_limit_is_rejected_naming_the_setting` | Constructor stores `DEFAULT_HISTORY_MAX_BYTES` instead of passed `history_max_bytes` | **21 failed**, `8388608 != 4096` | **21 passed** |

For AC35, a mutation must produce a valid success. Omitting its result yields executor `invalid_result`, which is a different failure. The result above uses `{}` and reaches the new no-success assertion itself. No conclusion relies on the preliminary, ineffective mutation attempts.

For AC11, the actual structural test fails on the virtual raw f-string fixture and passes after that fixture is removed; all shipped structural/self-test cases passed in the 58-case check. Its equivalent-spelling blind spots remain, as probe D demonstrates.

## REQUIREMENT MAP

The map names tests actually run. PASS means their assertions cover the stated criterion and have a negative control from the pre-fix overlay or the indicated mutation; a green test contradicted by an executed sibling-path counterexample is FAIL. This is not a claim that removing a Python `assert` statement itself causes ordinary pytest to fail: there is no general meta-test enforcing the presence of every assertion.

| Requirement | Named running tests carrying it | Result and criterion accounting |
|---|---|---|
| **R1** | Twitch `test_ac1_an_ungranted_compatibility_send_is_refused_by_the_executor`, `test_ac2_the_compatibility_send_is_traced_as_an_executor_call`; actions `test_ac3_a_reused_call_id_naming_another_call_is_refused`, `test_ac3_an_identical_authorized_reuse_still_reads_the_record`, `test_ac3_an_identical_reuse_the_policy_now_refuses_is_refused`, `test_ac4_concurrent_identical_calls_reach_the_provider_once`, `test_ac5_a_non_finite_cancel_grace_is_refused` | **FAIL boundary-wide**: AC1/AC4/AC5 pass; AC3's direct-executor assertions pass, but probe A reproduces the same defect at the agent cache. **AC2 FAIL literally:** the test and actual executor use `action.started`, not the required `action.requested`; independent compatibility output has exactly one started/completed pair and no requested event. This naming mismatch does not undo F1's executor routing fix. |
| **R2** | Proxy `test_p6_ac6_a_call_outside_the_negotiated_set_never_reaches_the_executor`, `test_p6_ac7_the_frame_principal_is_compared_never_executed_under`, `test_p6_ac8_peer_strings_never_enter_diagnostics_or_supervision_records`, `test_p6_ac9_running_executions_and_duplicate_waiters_are_bounded` | **FAIL**: fresh-call AC6/AC7 controls pass; altered cached calls evade both. AC8's error-frame cases pass but its general no-peer-text requirement fails probe B. AC9 passes, with both running/waiter guards separately mutation-proven. |
| **R3** | Loader `test_ac10_a_hook_diagnostic_never_names_another_modules_credential`, `test_ac10_a_schema_diagnostic_masks_a_credential_used_as_a_key`; main `test_ac10_check_config_never_echoes_a_credential_used_as_a_key`, `test_ac10_a_refused_startup_keeps_the_credential_out_of_supervision`, `test_ac12_main_check_config_exits_non_zero_on_an_unknown_key`; structural `test_ac11_every_configuration_diagnostic_surface_routes_through_the_primitive` and self-tests | **FAIL**: AC10's original check/UI/supervision surfaces pass; sibling CLI leaks. **AC11 fails** under equivalent-spelling/unrelated-redactor probes. AC12 passes, including shipped-example validation. Existing exact diagnostic expectations and short-secret boolean/enum controls pass; no observed vocabulary over-redaction. |
| **R4** | Brain `test_ac13_forgetme_empties_the_brain_memory_of_that_key_only`, `test_ac14_a_run_admitted_before_the_erasure_records_nothing_after_it`, `test_r4_a_stale_epoch_is_refused_and_a_fresh_one_kept` | **FAIL**: AC13 passes for brain recall/next prompt/other key. AC14 passes for conversation-memory epoch and erasure-event text, but fails the required all-holder stale-record refusal: probe E recreates persistent viewer text. |
| **R5** | Bridge `test_ac15_phase6_a_raising_reconcile_trace_leaves_the_owner_alive`; Twitch `test_ac16_an_authentication_refusal_of_the_chat_subscription_ends_recovery`, `test_ac17_a_socket_silent_past_its_keepalive_is_recovered`; YouTube `test_ac18_the_first_page_of_a_fresh_resolution_admits_nothing`, `test_ac18_the_first_page_after_a_re_lookup_admits_nothing` | **PASS**, AC15–AC18: raising supervision/recovery, auth termination, silent-socket watchdog, backlog priming. Existing valid/transient-recovery controls pass. |
| **R6** | Brain `test_ac19_a_body_one_byte_over_the_bound_fails_the_run_undecoded`, `test_ac19_a_body_exactly_at_the_bound_is_decoded_and_delivered`, `test_ac21_arguments_nested_10000_levels_are_malformed_and_the_run_goes_on`; model adapter `test_ac21_probe_arguments_nested_10000_levels_raise_brain_module_error`; proxy AC20 cap/deadline tests; admission `test_expired_and_evicted_sessions_never_accumulate_in_the_ready_structure`; Twitch `test_ac23_chat_write_refuses_text_over_500_characters_before_any_request`; Kick `test_ac24_the_dedup_window_is_never_shorter_than_twice_the_tolerance`, `test_ac24_a_replay_across_the_whole_tolerance_span_is_deduplicated` | **PASS**, AC19–AC24. Exact bound/valid-call controls are asserted, not inferred from refusal alone. Proxy cap/deadline behavioral mutations supplement baseline missing-setting failures. |
| **R7** | Viewer-memory `test_ac25_a_write_failing_for_space_evicts_nothing`, `test_ac25_a_rename_failing_after_evictions_publishes_their_exact_count`, `test_ac26_a_leftover_temporary_file_is_counted_then_reclaimed`, `test_ac27_a_memory_name_with_a_trailing_newline_is_foreign`; bus `test_retention_copy_failure_is_a_publication_error_with_history_record`; audio `test_stop_returns_as_soon_as_the_player_exits_within_the_grace`; config UI `test_p17_ac30_a_validator_that_never_returns_releases_the_check_by_the_deadline`, `test_p17_ac30_stalled_cleanup_after_a_validator_timeout_gets_only_the_residual`, `test_p17_ac30_cleanup_stages_after_an_early_validation_share_the_remaining_budget` | **PASS**, AC25–AC30. Timing is measured after player exit and against one shared Check deadline; the extra-cleanup-second mutation fails both shared-budget tests. |
| **R8** | Installation-doc `test_the_guides_endpoint_values_are_complete_chat_completions_urls`, `test_the_guides_profile_posts_to_the_guides_endpoint`, `test_the_documents_name_the_overlay_the_code_derives`, `test_the_guide_prints_the_commands_the_code_still_has`, `test_the_pre_fix_readme_line_does_not_resolve`, `test_the_readme_config_ui_command_runs` | **PASS**, AC31–AC33 for the current printed docs: URL actually posted, code-derived overlay, command resolution and executed help. The replacement's dropped profile-argument predicate fails R10/AC38 separately. |
| **R9** | Three history tests, isolated bridge AC16 test, bus limit test and `test_no_constructor_result_is_asserted_merely_not_none`, all named in the mutation table | **PASS**, AC34–AC36: required mutations fail; unmodified controls pass. Random/xdist installations themselves were not exercised. |
| **R10** | All 70 ledger regression nodes; full suite; unchanged `test_phase5_docs_status.py`; independent assertion-AST audit and mutations | **FAIL**. AC37's selected tests pass head and fail the overlay/mutations, but some baseline failures are new seams, and these tests miss the reproduced sibling defects. **AC38 FAIL** for five unallowlisted assertion changes and the weakened F18 replacement. **AC39 FAIL**: the summary's “34 findings closed” is contradicted by five open boundaries, and this gate cannot approve. Full installed-pre-fix subprocess isolation is **NOT-VERIFIABLE** from this overlay. |

The phase-5 guarantees remain supported by their running tests: `test_input_ownership.py::test_ac2_no_shipped_profile_names_the_bridge_or_an_inputs_key`, `::test_ac5_a_twitch_event_is_dropped_while_the_owning_bridge_is_down`, `::test_ac14_the_real_users_and_bridge_split_the_viewer_read`, `::test_the_gated_bindings_never_overlap`, the bridge's `test_ac21_a_principal_without_a_rule_is_refused_before_the_provider`, acknowledged/withheld/cancelled outcome tests, and `test_phase5_docs_status.py::test_ac37_no_document_calls_the_bridge_delivered`. These passed in the complete suite. No result here establishes real-instance delivery.

## NEW FINDINGS

These are additional defects in the phase's new evidence, demonstrated above; residual product defects are accounted for under F2–F6 rather than counted again.

- **N1 — MAJOR — AC11's new structural gate accepts unredacted diagnostic surfaces.** Probe D adds an equivalent `.format` or concatenation field-path surface, or an unrelated redactor call before a raw f-string: the actual structural test passes. Together with offline probe C, this defeats the phase's required construction-wide redaction guarantee. A call-graph/name heuristic is not sufficient evidence that each escaping diagnostic routes through the primitive.
- **N2 — MINOR — the F18 replacement drops the guide's UI-profile argument guarantee.** On both guides, changing only the UI command to `--config wrong.yaml` passes the replacement while falsifying the old exact assertion. This is an executed weakening counterexample, independently disallowed by AC38.
- **N3 — MINOR — phase assertion changes exceed the binding allowlist.** The AST comparison enumerates the five changed existing test assertion sets above. Strengthening an expectation or adapting it to a new setting does not make it one of the nine authorized replacements. Resolve the authority conflict explicitly; the closing reviewer cannot silently waive it.

## NOT VERIFIED

- No real Twitch/Kick/YouTube account, Streamer.bot instance, OBS, model service, audio device, durability-after-kill trial, load test or installed-wheel smoke test was used. The bridge remains **not delivered**.
- No exhaustive reading or mutation of every assertion in the 3,849 passing tests. Only the specified test-defect mutations, structural mutations, and the additional cap/waiter/deadline sensitivity checks were run. Literal detection of deletion of arbitrary test assertions is not established.
- The pre-fix overlay is read/import virtualization, not a checkout or installed baseline. A child Python interpreter spawned by a test does not inherit that virtualization. The recorded parent failures and name-only shims are observed; complete pre-fix subprocess isolation and each historical commit's claimed raw evidence are not independently certified.
- Random-order/xdist execution was not run. F28 was run alone, and alone with `-p no:randomly`, with observed nonempty outcomes and the required success mutation.
- The model adapter's fallback to `response.json()` for objects without a byte stream is still present; the production aiohttp stream and bounded fake-stream paths were exercised, not every injected third-party response object.
- Offline `viewer_memory stats` remains a record-file statistic and does not reclaim temporary files. Runtime store scan/capacity behavior is the F22 evidence; no claim of universal offline garbage collection is made.

Required before approval: close the cached-agent authorization/identity path (F2/F3), route every configuration reporter through credential redaction and make its structural guard effective (F4), remove peer free text from supervision (F5), reject stale persistent memory records (F6), and satisfy the binding test-treatment restrictions. Update the closure ledger to match those observed results.

**REQUEST_CHANGES**
