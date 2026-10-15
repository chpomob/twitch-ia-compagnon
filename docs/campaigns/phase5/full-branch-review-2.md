# Phase 5 — full-branch closing gate, second pass

## VERDICT

**REQUEST_CHANGES.** F1, F2 and F3 are resolved. F4's two original examples are fixed, but its required complete diagnostic protection remains **PARTIAL**: two further executed paths expose the configured credential. The full automated suite passes; it does not cover those paths. N1 below is the remaining blocker.

Reviewed HEAD `0ff5734` on `main`, cumulatively against the pre-phase implementation `9f6c1d9`, including the cross-file runtime, loader, native inputs, bridge, brain, packaging, fixtures, tests and documentation. Also examined `git diff 12eb1de HEAD` to isolate the four corrections. The authorities are `spec.md`, `brief.md`, `plan.md`, `scaffolding/plan-amendments.md` and `scaffolding/steps/P22F1-spec.md` through `P22F4-spec.md`. The first gate's complete agenda was repeated; D1–D11 and the approved P12/P14/P15 amendments are accepted decisions whose invariants were checked.

Only this report is written. No source or test file was edited, and nothing was staged or committed. The working tree was clean before review. Test fixtures use their normal temporary directories; bytecode and pytest's cache provider were disabled.

## Executed evidence

Commands ran from the repository root with its virtual environment. The sandbox initially failed before executing a command (`bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted`); escalated execution then succeeded.

| ID | Command | Observed result |
| --- | --- | --- |
| S | `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/ -q -p no:cacheprovider` | **3628 passed, 26 skipped in 216.20s (0:03:36)** |
| T | `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/test_phase5_trials.py -q -p no:cacheprovider` | **8 skipped in 0.01s**; exactly eight nodes |
| D | `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/test_phase5_docs_status.py -q -p no:cacheprovider` | **27 passed in 0.32s** |
| P | `git diff --numstat 9f6c1d9 HEAD -- config.yaml.example config.server.yaml.example agent.yaml.example presence.yaml.example` | Empty: zero changed lines in all four shipped profiles |
| V | `rg -n -i -e 'streamer[._ -]?bot' -e 'nate1280' core/` | No matches |
| C | `rg -n -e 'excluded_platforms' -e 'add_companion_identities' modules/` | Only `modules/streamerbot/__init__.py:2584`, the intended identity registration; no module passes binding exclusions |
| W | `rg -n -i '\bdelivered\b' README.md docs --glob '*.md' --glob '!docs/campaigns/**'` | Bridge claims are negated; README, index and both guides retain the pending-trial status |
| Q | `git diff --check` | No whitespace errors |

Every named test below ran in S with passing assertions. A requirement marked FAIL means an independent reproduction disproved its guarantee, despite those passing assertions. PASS refers to automated/local evidence, not compatibility with a real instance.

## FINDINGS OF THE FIRST PASS

The four original Python reproductions were extracted from the first report and executed unchanged first. Their old assertions deliberately assert the defects: F1 and F3 now fail those assertions; F2 stops at the newly detected mutation; F4 stops at its first newly redacted diagnostic. They were then completed with only the defect assertions reversed and F2's expected canary exception caught, to observe the remainder of each original scenario.

| Finding | Settlement and observed reproduction output |
| --- | --- |
| F1 — public supervision bypass | **RESOLVED** — `0 1`, `0 2`, `0 3`. Neither supervision method delivers the non-owned event; each attempt increments the counter. Independent owned-pair control: `owned bus delivered: 1 rejections: 0`, `owned emit delivered: 2 rejections: 0`, `owned record_and_emit delivered: 3 rejections: 0`. The guard remains active. |
| F2 — canary misses implicit suspension | **RESOLVED** — `original canary PASS`; `changed canary REFUSED: D8 fallback route: module-local synchronous writer`; the compiled mutant still demonstrates `writes: 0 pending: True`. Installed aiohttp is `3.14.3`. The real-path behavioural control also distinguishes the installed writer from the mutant, as recorded below. |
| F3 — cue recorded as viewer speech | **RESOLVED** — `cue success`, then `[]`. The real normalizer/brain/executor run succeeds and makes zero `memory.record` calls. The existing end-to-end positive control was also executed directly: user cue = 0 calls, anonymous instance cue = 0, ordinary chat = exactly 1 confirmed call. |
| F4 — ownership diagnostic credential leak | **PARTIAL** — both original cases now print `inputs: family '<redacted>': platform 'x': value 'streamerbot': family is outside the vocabulary (chat, viewers, cues)` and `inputs: family 'chat': platform '<redacted>': owner 'not-enabled' is not enabled`, each followed by `activations: 0`. Refusal was preserved. Complete protection still fails in the default-conflict reason and the earlier structural validation: N1. |

The completed reproduction runner, executed entirely in memory, was:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tests .venv/bin/python - <<'PY'
import re
from pathlib import Path
report = Path('docs/campaigns/phase5/full-branch-review.md').read_text()
for name in ('F1', 'F2', 'F3', 'F4'):
    script = re.search(r'~~~python\n(.*?)\n~~~',
        report.split('### '+name+' —', 1)[1], re.S).group(1)
    if name == 'F1':
        script = script.replace('assert len(seen) == 2', 'assert len(seen) == 0')
    elif name == 'F2':
        script = script.replace('canary()  # current installed writer passes',
            'canary(); print("original canary PASS")')
        script = script.replace('    canary()  # unexpectedly passes',
            '    try:\n        canary()\n    except AssertionError as error:\n'
            '        print("changed canary REFUSED:", error)\n    else:\n'
            '        raise AssertionError("mutation went undetected")')
    elif name == 'F3':
        script = script.replace('assert calls == [{\n'
            '            "viewer_text": "!brb", "delivery": "confirmed", '
            '"reply_text": "Back soon.",\n        }]', 'assert calls == []')
    else:
        script = script.replace('assert secret in str(error)',
            'assert secret not in str(error)\n'
            '            print("activations:", len(loader.activations))')
    print('\nCOMPLETED '+name, flush=True)
    exec(compile(script, '<completed-first-pass-'+name+'>', 'exec'),
         {'__name__': '__main__'})
PY
```

F1's three non-owner attempts share one diagnostic for the same `(module, family, platform)`, as R3 requires, with three counted rejections. Directly observed: `rejected attempts: 3 diagnostics: [{'module': 'intruder', 'family': 'chat', 'platform': 'x'}]`. Separate-pair tests verify the same diagnostic shape through each route. `record_and_emit` still performs its record when publication is refused. The tests also cover payload changes between call and await, changes by synchronous/asynchronous recorders, a recorder making the payload owned, and call-site terminal/argument refusals. These close the hole without refusing legitimate publication.

For F2, an additional in-memory run called `_d8_transmission_segment` with a logging capture and `pytest.MonkeyPatch.context()`, first unchanged, then with `_compiled_send_frame(_mutated_send_frame_source('async with self._send_lock:'))` installed and its lock held. It used the real bridge, executor, uncompressed aiohttp client and socket transport against the fake instance. Observed:

```text
aiohttp: 3.14.3
installed path: ['mark', 'write', 'turn', 'write']
mutated path: ['mark', 'turn', 'write', 'write']
behavioural rejection: D8 fallback route: module-local synchronous writer: ['mark', 'turn', 'write', 'write']
```

`turn` is scheduled by `mark_emitted` and runs once the marking segment yields; the first transport write moves to the other side of it under the mutant. The helper also records later writes during teardown. It verifies one outgoing DoAction frame independently. Static negative controls execute the canary on compiled-valid mutations for explicit await, async-with, async-for and an async comprehension. The behavioural check is not vacuous. Its stated limitation remains: one uncontended schedule cannot detect all conditional suspensions; the static half and contended negative control supplement it.

## REQUIREMENT MAP

| Requirement | Named running enforcement | Result |
| --- | --- | --- |
| R1 — total optionality | `tests/test_input_ownership.py::test_ac1_main_check_config_exits_zero_with_the_pre_phase_output`; `tests/test_input_ownership.py::test_ac2_the_shipped_profiles_have_zero_changed_lines_since_the_phase_base`; `tests/test_automation_bridge.py::test_ac3_an_unreachable_instance_leaves_a_ready_companion_and_a_degraded_bridge` | **PASS**, S/P. Absent configuration retains baseline results; both unreachable run/read scenarios leave the companion ready and the bridge finally degraded. |
| R2 — explicit preparation ownership | `tests/test_input_ownership.py::test_ac7_two_default_declarers_are_refused_before_any_effect`; `tests/test_input_ownership.py::test_ac8_an_owner_list_of_two_is_refused_naming_both`; `tests/test_config_ui.py::test_p5_ac10_the_core_page_shows_the_resolved_ownership_read_only` | **PASS**, S, for resolution, pre-effect refusal and display. The confidentiality exception is reported under R6/N1. |
| R3 — runtime ownership | `tests/test_input_ownership.py::test_f1_no_public_route_publishes_a_non_owned_input`; `tests/test_input_ownership.py::test_f1_an_owned_pair_is_delivered_once_through_each_route`; `tests/test_input_ownership.py::test_ac44_an_undeclared_publisher_is_refused_a_resolved_or_none_pair`; `tests/test_input_ownership.py::test_ac14_the_real_users_and_bridge_split_the_viewer_read` | **PASS**, S and F1 reproduction/controls. Bus and both supervision routes enforce the shared guard; v1/v2 compatibility and non-overlapping viewer providers pass. |
| R4 — honest action outcomes | `tests/test_automation_bridge.py::test_ac16_row_acknowledged_is_execution_unconfirmed`; `tests/test_automation_bridge.py::test_d8_a_pre_write_failure_sends_nothing_and_withdraws_readiness`; `tests/test_automation_bridge.py::test_d8_behaviour_the_frame_reaches_the_transport_in_the_marking_segment`; `tests/test_automation_bridge.py::test_d8_negative_control_the_behavioural_check_catches_f2s_mutation` | **PASS**, S and F2 reproduction/controls. All ten outcome rows and interruption paths pass. |
| R5 — allowlist and authorization | `tests/test_automation_bridge.py::test_ac18_only_allowlisted_names_are_reachable`; `tests/test_automation_bridge.py::test_ac20_reconciliation_names_the_unmatched_entries_once`; `tests/test_automation_bridge.py::test_ac21_a_principal_without_a_rule_is_refused_before_the_provider`; `tests/test_automation_bridge.py::test_ac45_every_matching_group_entry_must_accept` | **PASS**, S. Refusals send zero DoAction frames; authorization precedes provider entry. |
| R6 — lifecycle, connection, bounds and credentials | `tests/test_automation_bridge.py::test_ac24_the_challenge_answer_and_readiness_only_after_the_inventory`; `tests/test_automation_bridge.py::test_ac26_a_full_queue_drops_and_counts_and_never_stalls_the_connection`; `tests/test_automation_bridge.py::test_ac4_each_failure_mode_degrades_with_its_reason_and_leaks_nothing`; `tests/test_main.py::test_f4_check_config_never_echoes_a_credential_used_as_an_inputs_key` | **FAIL**, N1. Named tests pass in S, including the original credential cases, but further startup/Check and resolver diagnostics leak the complete known credential. |
| R7 — cues and replaced inputs | `tests/test_automation_bridge.py::test_ac29_a_valid_cue_is_one_normalised_event_the_default_policy_accepts`; `tests/test_automation_bridge.py::test_ac43_each_fixture_maps_to_the_native_kind_and_key_set`; `tests/test_automation_bridge.py::test_ac34_the_owned_channel_reads_one_page_of_active_viewers`; `tests/test_brain.py::test_f3_a_cue_is_never_recorded_as_the_speech_of_the_viewer_it_names` | **PASS**, S and F3 reproduction/ordinary-message control. |
| R8 — declared uncertainty and trial gate | `tests/test_automation_bridge.py::test_ac35_every_frame_carries_a_provenance_and_no_question_is_answered`; `tests/test_phase5_docs_status.py::test_ac36_the_trial_file_skips_its_eight_nodes_opening_no_socket`; `tests/test_phase5_docs_status.py::test_ac37_no_document_calls_the_bridge_delivered` | **PASS**, S/T/D, for the automated safeguards; real-instance answers and report tracking are **NOT-VERIFIABLE** here. |
| R9 — configurations | `tests/test_automation_bridge.py::test_ac39_each_fragment_checks_as_the_guide_says`; `tests/test_automation_bridge.py::test_ac39_combination_8_the_brain_host_finds_the_run_action_in_the_catalog`; `tests/test_automation_bridge.py::test_ac40_the_table_has_twelve_rows_with_every_cell_filled` | **PASS**, S. All twelve fragments and the remote-agent placement restriction pass. |
| R10 — static constraints | `tests/test_input_ownership.py::test_ac11_no_file_under_core_names_the_vendor_or_the_bridge_module`; `tests/test_profiles.py::test_installed_distribution_discovers_the_shipped_manifests_and_answers_help`; `tests/test_automation_bridge.py::test_ac41_dependencies_are_unchanged_and_the_manifest_ships`; `tests/test_automation_bridge.py::test_ac42_the_guide_is_a_declared_bilingual_pair` | **PASS**, S/V: 18 installed manifests, unchanged aiohttp/PyYAML runtime dependencies, bilingual guide, vendor-free kernel. |

## GUARANTEES

Here `O::` means `tests/test_input_ownership.py::` and `A::` means `tests/test_automation_bridge.py::`; S ran every referenced node.

| Guarantee | Assessment and executed command/counterexample |
| --- | --- |
| Total optionality | **PASS** — S/AC1–5, P and V. Profiles have zero changed lines, disabled references are not resolved, there is no required schema key, an unreachable instance leaves both run/read unavailable with no ownership fallback, and the companion remains ready. |
| One owner per family/platform | **PASS** — S: `O::test_ac7_two_default_declarers_are_refused_before_any_effect`, `O::test_ac8_an_owner_list_of_two_is_refused_naming_both`, `O::test_a_concrete_declarer_beats_a_wildcard_declarer`, `O::test_the_explicit_wildcard_key_beats_a_concrete_default`, both AC44 undeclared-publisher tests and `O::test_the_gated_bindings_never_overlap`. F1 reproduction observes zero deliveries through all three public publication routes. Confidentiality of the default-conflict diagnostic fails separately in N1. |
| Honest silence | **PASS** — S's ten `A::test_ac16_row_*` tests and D8 tests: pre-write failure/closed connection gives refused/provider_not_ready and zero frames; pre-frame cancellation gives cancelled and zero frames; a deadline at the blocked hook gives timeout/timed_out and zero frames; acknowledgement gives external_unknown/execution_unconfirmed and one frame; lost acknowledgement, post-send cancellation/deadline and drain interruption give external_unknown/acknowledgement_lost and one frame. No acknowledged run reports success or ok. |
| Allowlist before network | **PASS** — S/AC18–21/45. Outside names, non-members, missing, disabled, ambiguous names and invalid arguments give zero DoAction frames. A matching group member is reachable subject to its schemas; a principal without a rule never enters the provider. Reconciliation repeats on reconnect. |
| No secret value escapes | **FAIL** — N1-A executes both `run` and `check_config` and prints the password in structural input diagnostics; N1-B prints it in the resolver's suggested correction path. Standard action/trace/log/UI sweeps and the original F4 cases pass in S. |
| Not delivered | **PASS** — T/D/W. Eight trials skip, documentation safeguards pass, and no real-instance trial record exists. README/index and both guide statuses remain pending-trial. |
| Vendor-free kernel | **PASS** — V has no matches; S/AC11 also rejects platform literals in ownership code. C confirms binding exclusions are set by runtime and the intended identity-registration caller remains module-local. |
| Installed aiohttp canary | **PASS** — S's installed-source test, four static mutation cases, real transport behavioural test and contended behavioural negative control; F2 reproduction refuses the original counterexample with the loud fallback message. Direct observed event sequences are above. |

The ten AC16 rows also preserve exact refusal/error codes: not_allowlisted, instance_action_missing, instance_action_ambiguous, instance_action_disabled, invalid_arguments, provider_not_ready, external_effect_unknown/execution_unconfirmed, provider_failed/instance_rejected, external_effect_unknown/acknowledgement_lost and cancelled. Every non-success is non-retryable. AC17's two late-acknowledgement races and AC28's in-flight shutdown scenario pass.

### Phases 0–4 regression check

S ran the complete tests directory. These named tests continue enforcing the prior guarantees; the new input-diagnostic exception is explicitly excluded from any blanket confidentiality claim.

| Prior guarantee | Passing running evidence in S |
| --- | --- |
| Default deny, including reads | `tests/test_actions.py::test_zero_rules_refuse_a_read_and_a_write_with_zero_invocations`; `tests/test_users.py::test_read_without_a_rule_is_refused_and_the_provider_is_not_entered` |
| Bounded admission and retention | `tests/test_admission.py::test_full_session_queue_and_global_cap_reject_with_reason_and_depth`; `tests/test_retention.py::test_byte_bound_evicts_oldest_first_and_is_never_exceeded`; retention/bus/dedup/audit/attachment suites |
| Explicit terminal outcomes | `tests/test_actions.py::test_a_silent_write_defaults_to_external_unknown_on_timeout`; `tests/test_actions.py::test_a_confirmation_landing_after_the_deadline_is_never_a_success` |
| Single global startup/shutdown deadlines | `tests/test_main.py::test_a_hanging_settings_validator_is_bounded_by_the_startup_deadline`; `tests/test_lifecycle.py::test_start_honours_a_deadline_established_before_the_phases`; `tests/test_lifecycle.py::test_one_global_shutdown_deadline_caps_the_sum_of_local_timeouts` |
| Existing secret redaction | `tests/test_contracts.py::test_secret_never_reaches_a_trace_diagnostic`; `tests/test_config_ui.py::test_ac35_no_secret_value_reaches_any_response_log_report_or_record`; **new input diagnostics remain defective in N1** |
| Phase 2 R10 deadline ruling | `tests/test_actions.py::test_the_timer_winning_adopts_the_record_of_a_provider_that_answers_its_cancel`; `tests/test_actions.py::test_a_late_success_is_still_replaced_by_the_generic_record`; `tests/test_actions.py::test_a_provider_that_never_answers_is_cut_by_the_timer_at_expiry` |
| No default provider | `tests/test_profiles.py::test_check_config_passes_with_the_speech_endpoints_unset_then_configured`; `tests/test_profiles.py::test_ac43_with_no_speech_endpoint_audio_speak_is_named_not_ready_with_0_requests` |
| Strict moderation default, modes, no permanent bans | `tests/test_moderation.py::test_ac24_the_default_mode_alerts_with_no_platform_request_and_one_fact`; `tests/test_moderation.py::test_ac26_auto_apply_applies_a_delete_at_once_under_the_strict_rules`; `tests/test_moderation.py::test_no_operation_removes_a_viewer_permanently` |
| Viewer-memory bounds and analysis-based eviction | `tests/test_viewer_memory.py::test_ac14_eviction_deletes_the_least_recent_then_least_used_with_one_fact`; `tests/test_viewer_memory.py::test_ac15_the_total_bound_holds_and_the_target_is_never_deleted`; failed-eviction regressions; F3's ordinary-message positive control |
| Requested-only watch input | `tests/test_watch.py::test_ac29_without_activation_no_tick_is_emitted`; `tests/test_watch.py::test_ac29_a_viewers_start_command_starts_nothing` |
| Honest platform capabilities | Full twitch/kick/youtube suites, including `tests/test_youtube.py::test_chat_owned_elsewhere_polls_nothing_and_keeps_chat_write`, on-demand lookup failures, moderation limitations and clip outcomes |
| Local-only UI; secrets/actions read-only | `tests/test_config_ui.py::test_ac20_secrets_and_actions_are_read_only_with_every_rule`; local bind, origin, host, session and save-scope enforcement in that suite |

### Amendments and integration audit

- **D9:** `tests/test_contracts.py::test_string_length_bounds_are_enforced`, `test_length_bound_on_a_non_string_node_is_refused`, `test_malformed_length_bounds_are_refused` and the existing unsupported-schema tests pass. AC15's exact action schema accepts 1–128 characters and rejects 0/129; unsupported keywords remain refused.
- **P12:** `A::test_a_bridge_without_companion_name_is_refused_by_the_hook_not_the_triggers` passes. The manifest loads and missing companion_name is refused by settings validation before trigger registration. Exact credential, action-set and count assertions in the examples test remain enforced.
- **D10/P14:** `tests/test_lifecycle.py::test_a_state_the_module_reported_is_not_overwritten_by_its_phase` passes for prepare and start_inputs; `test_a_module_that_reports_nothing_keeps_todays_records` preserves the silent-module path. AC3 checks the final degraded state and absence of a later ready overwrite. Close still reports stopped.
- **D11/P15:** `tests/test_actions.py::test_an_executor_take_over_keeps_the_providers_cause`, `test_a_take_over_without_a_cause_keeps_todays_record_exactly`, `test_an_in_time_conversion_keeps_the_cause_beside_its_note`, `test_a_rejected_conversion_keeps_the_cause_too` and `test_a_shipped_confirmation_lost_cause_survives_a_take_over` pass. Existing R10 outcomes retain their status/code and no-cause records remain exact; uncertainty causes survive conversion/takeover.
- **D1–D8:** family membership excludes watch_tick; explicit/concrete/wildcard precedence and none are exercised; v1/v2 publishers share compatibility rules; evaluate/chat refusal does not multiply publication counters; wildcard binding exclusions preserve requested scopes and do not overlap; anti-echo registration is additive; ownership-aware validators receive the view while ordinary hooks keep their call shape; transmission-boundary tests pass.
- **Queue/mapping/consumers:** the reader uses put_nowait and AC26 proves overflow does not block request responses. AC43 compares mapped fixtures with native normalizers and checks owned-platform filtering. AC32 refuses untrusted role claims, AC33 enforces companion identities, AC34 reports active_viewers while the native users provider retains observed_authors. The brain cue route-safety test passes. Cues do not enter the shared chat transcript, users directory, ordinary viewer-memory observer, moderation or watch commands. F3 now also excludes the brain's separate post-run recording path.
- **R8–R10 details:** fixture provenance guards pass without answering real-instance questions. All twelve guide fragments are executed by AC39; combination 8 checks discovery on the brain host while documenting that its cues/families remain local to the agent. Packaging tests build/install a pristine copy outside the checkout and discover 18 manifests. Dependency assertions remain exact. Both normative/translated guides are registered in the bilingual tests.

Compared the seven allowlisted tests against the cumulative baseline diff:

| Adapted test | Retained enforcement |
| --- | --- |
| `tests/test_contracts.py::test_event_kind_vocabulary_is_the_nine_names_in_order` | Exact prior order retained; cue appended as the tenth kind. |
| `tests/test_contracts.py::test_platform_notice_kinds_exclude_message_and_watch_tick` | Exact seven notice kinds retained; cue additionally excluded. |
| `tests/test_examples.py::test_manifests_are_unique_and_have_coherent_capabilities` | Exact unique sets/keys/declarations/credentials retained; 18 manifests and 14 actions, including the approved P12 amendments. |
| `tests/test_manifest_presentation.py::test_presented_covers_every_shipped_manifest` | Exact presented/shipped-set equality retained at 18. |
| `tests/test_profiles.py::test_pyproject_ships_the_three_phase_2_manifests_and_no_new_dependency` | Exact manifest/package-data sets retained at 18; runtime dependencies unchanged. |
| `tests/test_profiles.py::test_installed_distribution_discovers_the_shipped_manifests_and_answers_help` | Exact installed/discovered sets retained at 18, outside-checkout provenance and launcher assertions retained. |
| `tests/test_users.py::test_manifest_is_v2_and_declares_users_read_without_granting_it` | Existing action and no-grant assertions retained; enum gains active_viewers while COVERAGE_KIND remains exactly observed_authors. Separate page tests enforce the native output. |

No weakened safety assertion was found in those seven. Other adaptations remain explicit: the examples chat-only catalog gains the discovered action while its registered-ready view stays unchanged; the bilingual list gains the guide; demo counters become nine; the UI module count becomes 18. The UI's existing unusable-status helper removes its numbered temporary parent path before scanning incidental numeric canaries, while continuing to inspect diagnostic reasons and page data. Those adaptations were examined, not treated as automatic exemptions.

## NEW FINDINGS

### N1 — MEDIUM: F4's redaction remains incomplete outside the corrected quoted fields

This is further evidence of the unresolved part of F4, not a new objection to the approved early ownership-resolution order. Both reproductions use a synthetic credential and no persistent memory/configuration writes.

**A. Structural input diagnostics bypass the resolver entirely.** Locations: `core/main.py:1578`–`1583` and `core/main.py:1588`–`1590`. `_validate_inputs` interpolates raw family/platform keys into ConfigurationError paths. Both `run` and `check_config` report those errors verbatim before the corrected resolver is reached. A configured password copied into either malformed key is exposed in full.

Executed command (only parsed-document reads are substituted; loading, validation and both public entry points remain real; the assembly spy proves failure precedes runtime construction):

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tests .venv/bin/python - <<'PY'
import asyncio
from pathlib import Path
from unittest.mock import patch
import core.main as app
from test_main import _finite_limits
secret = 'review-secret-4521'
async def main():
    for inputs in ({secret: 'not-a-mapping'}, {'chat': {secret: 42}}):
        config = {
            'modules_directory': str(Path('modules').resolve()),
            'enabled_modules': ['streamerbot'],
            'modules': {'streamerbot': {
                'url': 'ws://127.0.0.1:9/', 'companion_name': 'Companion',
                'password': secret,
            }},
            'limits': _finite_limits(), 'inputs': inputs,
        }
        with patch.object(app, 'read_base', return_value=config), \
             patch.object(app, 'read_overlay', return_value={}), \
             patch.object(app, '_assemble_runtime') as assemble:
            for route in ('run', 'check_config'):
                diagnostics = []
                if route == 'run':
                    status = await app.run('config.yaml.example', asyncio.Event(),
                        environ={}, diagnostic_reporter=diagnostics.append)
                else:
                    status = await app.check_config('config.yaml.example',
                        environ={}, diagnostic_reporter=diagnostics.append)
                print(route, status, diagnostics)
                assert status == 2 and any(secret in line for line in diagnostics)
            print('runtime assemblies:', assemble.call_count)
            assert assemble.call_count == 0
asyncio.run(main())
PY
```

Observed:

```text
run 2 ['inputs.review-secret-4521: must be a mapping of platform to owner']
check_config 2 ['inputs.review-secret-4521: must be a mapping of platform to owner']
runtime assemblies: 0
run 2 ['inputs.chat.review-secret-4521: owner must be a module name or a list of module names']
check_config 2 ['inputs.chat.review-secret-4521: owner must be a module name or a list of module names']
runtime assemblies: 0
```

**B. Default-owner conflicts redact the platform label but repeat the raw platform in the correction path.** Locations: `core/loader.py:2656`–`2658`, with `_inputs_line` at `core/loader.py:2457`. The known-credential set is available, but the reason contains `set inputs.{family}.{platform}` and raw owner reprs; `_inputs_line` only redacts its location fields. Its reason passes through `_value_free_reason`, which bounds text but does not redact known credentials. Generic platform names are valid manifest declarations, so this is a supported resolver input, not an invalid forged object.

Executed:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tests .venv/bin/python - <<'PY'
from test_input_ownership import _decl, _families
from core.loader import resolve_input_ownership, ModuleLoadError
secret = 'review-secret-4521'
declarations = {
    name: _decl(_families(('chat', [secret], True)), name)
    for name in ('a', 'b')
}
try:
    resolve_input_ownership(['a', 'b'], declarations, {},
                            redacted=frozenset({secret}))
except ModuleLoadError as error:
    print(str(error))
    print('credential leaked:', secret in str(error))
    assert secret in str(error)
PY
```

Observed:

```text
inputs: family 'chat': platform '<redacted>': owned by default by both 'a' and 'b'; set inputs.chat.review-secret-4521
credential leaked: True
```

Required closure: protect structural input diagnostics as well as every dynamic component of resolver reasons before any normalization/truncation can leave a credential fragment. Preserve the refusal, field identification, provider conflict information and useful fixed vocabulary. Keep the existing exact-text controls for non-secret diagnostics, and add regressions for A through run/Check and for B's reason/path. Redacting only the location fields is insufficient. No runtime permission or network change is needed to reproduce either failure.

No further concrete defect was established in the guard, canary or memory corrections.

## NOT VERIFIED

- No real automation instance was available or contacted. All eight study questions remain unanswered: nonexistent action names, actual latency/blocking queues, enforce-mode behaviour, request-version availability, C# HTTP/JSON, custom-event round trip, Wine servers and subscribed-event volume. T skips all eight; fixture provenance does not establish real compatibility.
- The full suite's 26 skipped nodes are not claimed as verified. This review establishes the passing local/fake-server contracts and the declared trial gate.
- Live native-platform services, real credentials, operator logs/inventories and alternative operating systems, Python versions or aiohttp versions were not exercised. The writer evidence is for installed aiohttp 3.14.3 and the shown in-memory mutations.
- The N1-A document-read substitution avoids creating a profile; actual disk YAML parsing for those two counterexamples was not separately exercised. Both real entry points, structural validation and diagnostic reporting were exercised. N1-B directly exercises the public resolver with validated in-memory declarations; no custom module was written or activated.
- No dedicated new N1 reproduction was run through the configuration UI's rendered Check page. Its independent redaction layer and existing secret/UI suites pass; the demonstrated leak claims concern startup/CLI Check and the resolver output.
- F3 uses the real normalizer/brain/executor with a recording provider, without writing viewer-memory files. Memory persistence/bounds/eviction are exercised separately by S.
- No confirmation round trip was assumed (A6), and no separate presence family was demanded (A4). These accepted choices are not findings.
- AC38's eventual tracking of this report is pending operator staging/commit, which this review is explicitly forbidden to perform. The gate remains blocked by N1/F4; a passing suite alone does not close it.
