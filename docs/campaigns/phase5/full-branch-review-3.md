# Phase 5 — full-branch closing gate, third pass

## VERDICT

**REQUEST_CHANGES.** Both reproductions reported in N1 are corrected. F1, F2 and F3 remain resolved, and F4's two original cases did not regress. However, complete redaction remains **PARTIAL**: an executed overlapping-value counterexample leaves a configured credential's suffix visible through startup, Check and the resolver's correction path. N2 below is further evidence of the remaining F4/N1 protection gap. The green full suite does not cover it.

Reviewed HEAD `c806f23` on `main`, cumulatively against pre-phase implementation `9f6c1d9`, including the kernel, native inputs, bridge, brain, tests, fixtures, packaging and operator documentation. Examined `git diff 12eb1de HEAD` for the five corrections and `git diff cfeed99 HEAD` for P22F5. The authorities are the phase's `brief.md`, `spec.md`, `plan.md`, `scaffolding/plan-amendments.md`, the five P22F fix specifications and the two previous reports. D1–D11 and the approved P12/P14/P15 amendments are accepted decisions; their invariants are checked below.

The working tree was initially clean. Only this report is written; nothing is staged or committed. Bytecode and pytest's cache provider were disabled. Tests use their normal temporary fixtures, including an outside-checkout copy for packaging. Independent reproductions below run in memory without configuration or viewer-memory writes. The initial sandbox launch failed with `bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted`; approved escalated execution ran the review commands.

## Executed evidence

All commands ran from the repository root with the project virtual environment.

| ID | Command | Observed result |
| --- | --- | --- |
| S | `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/ -q -p no:cacheprovider` | **3637 passed, 26 skipped in 215.58s (0:03:35)** |
| T | `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/test_phase5_trials.py -q -p no:cacheprovider` | **8 skipped in 0.01s** |
| D | `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/test_phase5_docs_status.py -q -p no:cacheprovider` | **27 passed in 0.32s** |
| P | `git diff --numstat 9f6c1d9 HEAD -- config.yaml.example config.server.yaml.example agent.yaml.example presence.yaml.example` | Empty: zero changed lines in the four profiles |
| V | `rg -n -i -e 'streamer[._ -]?bot' -e 'nate1280' core/` | No matches |
| C | `rg -n -e 'excluded_platforms' -e 'add_companion_identities' modules/` | Only the intended identity registration at `modules/streamerbot/__init__.py:2584`; no module passes binding exclusions |
| W | `rg -n -i '\bdelivered\b' README.md docs --glob '*.md' --glob '!docs/campaigns/**'` | Bridge claims remain negated in README, index and both guides; other matches concern unrelated design statements |
| Q | `git diff --check` | No whitespace errors |

Every named test below ran in S with passing assertions. PASS describes the tested local contract, not real-instance compatibility. FAIL identifies a guarantee disproved by an independent reproduction, despite the passing regression suite. Scripts asserting a defect intentionally exit zero when they reproduce it.

## PREVIOUS FINDINGS

N1 was replayed first. Its scripts were extracted from the second report, with only the two leak assertions reversed so both A cases and B could finish:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tests .venv/bin/python - <<'PY'
import re
from pathlib import Path
report = Path('docs/campaigns/phase5/full-branch-review-2.md').read_text()
section = report.split('### N1 —', 1)[1]
scripts = re.findall(r'```bash\n(.*?)\n```', section, re.S)
for name, block in zip(('A', 'B'), scripts):
    script = block.split("<<'PY'\n", 1)[1].rsplit('\nPY', 1)[0]
    script = script.replace(
        'assert status == 2 and any(secret in line for line in diagnostics)',
        'assert status == 2 and all(secret not in line for line in diagnostics)')
    script = script.replace('assert secret in str(error)',
                            'assert secret not in str(error)')
    print('N1-' + name, flush=True)
    exec(compile(script, '<N1-' + name + '>', 'exec'), {'__name__': '__main__'})
PY
```

Observed output:

```text
N1-A
run 2 ['inputs.<redacted>: must be a mapping of platform to owner']
check_config 2 ['inputs.<redacted>: must be a mapping of platform to owner']
runtime assemblies: 0
run 2 ['inputs.chat.<redacted>: owner must be a module name or a list of module names']
check_config 2 ['inputs.chat.<redacted>: owner must be a module name or a list of module names']
runtime assemblies: 0
N1-B
inputs: family 'chat': platform '<redacted>': owned by default by both 'a' and 'b'; set inputs.chat.<redacted>
credential leaked: False
```

Both precise N1 counterexamples are therefore resolved: refusals, field identification, conflicting providers and the correction remain visible, with no runtime assembly in A. The complete-protection requirement attached to N1 is still only PARTIAL because of N2.

The first pass's four reproductions were also completed using the second report's existing runner. That runner changes only the old defect assertions and catches F2's expected canary refusal; it changes no repository code:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tests .venv/bin/python - <<'PY'
import re
from pathlib import Path
report = Path('docs/campaigns/phase5/full-branch-review-2.md').read_text()
block = re.search(r'```bash\n(.*?COMPLETED.*?\nPY)\n```', report, re.S).group(1)
script = block.split("<<'PY'\n", 1)[1].rsplit('\nPY', 1)[0]
exec(compile(script, '<previous-reproduction-runner>', 'exec'))
PY
```

| Finding | Settlement and observed output |
| --- | --- |
| F1 — public supervision bypass | **RESOLVED.** `0 1`, `0 2`, `0 3`: zero deliveries through bus, emit and record_and_emit, with one rejection counted for each attempt. The shared one-per-pair diagnostic and recorder ordering also pass in S. |
| F2 — canary misses implicit suspension | **RESOLVED.** `original canary PASS`; `changed canary REFUSED: D8 fallback route: module-local synchronous writer`; mutant: `writes: 0 pending: True`. The real transport control below also rejects the mutation. |
| F3 — cue recorded as viewer speech | **RESOLVED.** `cue success`, then `[]`: the real normalizer/brain/executor completes without a memory.record call. The ordinary-chat positive control below still records once. |
| F4 — ownership diagnostic leaks credentials | **PARTIAL.** Both original cases remain fixed. Output: `inputs: family '<redacted>': platform 'x': value 'streamerbot': family is outside the vocabulary (chat, viewers, cues)` and `inputs: family 'chat': platform '<redacted>': owner 'not-enabled' is not enabled`, each followed by `activations: 0`. N1's original cases also pass, but N2 leaks a credential suffix. |
| N1 — incomplete structural/reason protection | **PARTIAL overall; A and B RESOLVED.** Exact current output is above. The required absence of credential fragments is disproved by N2 through both public routes and the resolver. This is a redaction defect, not an objection to the approved early ownership-resolution order. |

Additional controls were executed directly, using the existing helpers in `test_input_ownership`, `test_brain` and `test_automation_bridge`, with `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tests .venv/bin/python -`. The publication control calls `_publish_through` for chat/y on `_intruder(_guarded_runtime()[0])`. The memory control awaits `test_f3_a_cue_is_never_recorded_as_the_speech_of_the_viewer_it_names`. The transport control calls `_d8_transmission_segment` under `pytest.MonkeyPatch.context()`, first unchanged, then with `_compiled_send_frame(_mutated_send_frame_source('async with self._send_lock:'))` installed and its lock held. It captures actual transport writes and verifies exactly one outgoing DoAction frame:

```text
owned bus delivered: 1 rejections: 0
owned emit delivered: 2 rejections: 0
owned record_and_emit delivered: 3 rejections: 0
memory control PASS: viewer cue=0, anonymous cue=0, ordinary chat=1 confirmed
aiohttp: 3.14.3 installed path: ['mark', 'write', 'turn', 'write']
mutated path: ['mark', 'turn', 'write', 'write']
behavioural rejection: D8 fallback route: module-local synchronous writer: ['mark', 'turn', 'write', 'write']
```

The guard admits legitimate publication. F1's tests also cover unrestricted contexts, payload changes between call and await, synchronous/asynchronous recorders, a recorder making the pair owned, and call-site refusals. F3 excludes both cue forms without suppressing ordinary recording. The canary is not vacuous: `turn` runs after the marking segment yields, and the mutant moves the first write past it. Static tests additionally reject compiled-valid explicit-await, async-with, async-for and async-comprehension mutations.

## REQUIREMENT MAP

All enforcement named here ran in S. Confidentiality is assessed against the independently executed counterexample as well as existing tests.

| Requirement | Named running test(s) | Result |
| --- | --- | --- |
| R1 — total optionality | `tests/test_input_ownership.py::test_ac1_main_check_config_exits_zero_with_the_pre_phase_output`; `tests/test_input_ownership.py::test_ac2_the_shipped_profiles_have_zero_changed_lines_since_the_phase_base`; `tests/test_automation_bridge.py::test_ac3_an_unreachable_instance_leaves_a_ready_companion_and_a_degraded_bridge` | **PASS**, S/P: unchanged baseline profiles; both unavailable run/read cases retain companion readiness and final degraded bridge state, without fallback. |
| R2 — preparation ownership | `tests/test_input_ownership.py::test_ac7_two_default_declarers_are_refused_before_any_effect`; `tests/test_input_ownership.py::test_ac8_an_owner_list_of_two_is_refused_naming_both`; `tests/test_config_ui.py::test_p5_ac10_the_core_page_shows_the_resolved_ownership_read_only` | **PASS**, S: deterministic resolution, pre-effect refusals, read-only display. Diagnostic confidentiality fails separately under R6/N2. |
| R3 — runtime ownership | `tests/test_input_ownership.py::test_f1_no_public_route_publishes_a_non_owned_input`; `tests/test_input_ownership.py::test_f1_an_owned_pair_is_delivered_once_through_each_route`; `tests/test_input_ownership.py::test_ac44_an_undeclared_publisher_is_refused_a_resolved_or_none_pair`; `tests/test_input_ownership.py::test_ac14_the_real_users_and_bridge_split_the_viewer_read` | **PASS**, S and direct F1 controls: three guarded public routes, v1/v2 compatibility and non-overlapping viewer providers. |
| R4 — honest outcomes | `tests/test_automation_bridge.py::test_ac16_row_acknowledged_is_execution_unconfirmed`; `tests/test_automation_bridge.py::test_d8_a_pre_write_failure_sends_nothing_and_withdraws_readiness`; `tests/test_automation_bridge.py::test_d8_behaviour_the_frame_reaches_the_transport_in_the_marking_segment`; `tests/test_automation_bridge.py::test_d8_negative_control_the_behavioural_check_catches_f2s_mutation` | **PASS**, S and F2 controls; all ten outcome rows and interruption paths pass. |
| R5 — allowlist and authorization | `tests/test_automation_bridge.py::test_ac18_only_allowlisted_names_are_reachable`; `tests/test_automation_bridge.py::test_ac20_reconciliation_names_the_unmatched_entries_once`; `tests/test_automation_bridge.py::test_ac21_a_principal_without_a_rule_is_refused_before_the_provider`; `tests/test_automation_bridge.py::test_ac45_every_matching_group_entry_must_accept` | **PASS**, S: refusal before DoAction and authorization before provider entry. |
| R6 — lifecycle, bounds and credentials | `tests/test_automation_bridge.py::test_ac24_the_challenge_answer_and_readiness_only_after_the_inventory`; `tests/test_automation_bridge.py::test_ac26_a_full_queue_drops_and_counts_and_never_stalls_the_connection`; `tests/test_automation_bridge.py::test_ac4_each_failure_mode_degrades_with_its_reason_and_leaks_nothing`; `tests/test_main.py::test_n1_a_structural_inputs_refusal_never_echoes_a_credential`; `tests/test_input_ownership.py::test_n1_b_a_default_conflict_on_a_credential_platform_never_echoes_it` | **FAIL**, N2. All named tests pass, and N1's exact cases are corrected; overlapping known values still expose a configured password suffix. Other tested lifecycle/connection/bounds contracts pass. |
| R7 — cues and input replacement | `tests/test_automation_bridge.py::test_ac29_a_valid_cue_is_one_normalised_event_the_default_policy_accepts`; `tests/test_automation_bridge.py::test_ac43_each_fixture_maps_to_the_native_kind_and_key_set`; `tests/test_automation_bridge.py::test_ac34_the_owned_channel_reads_one_page_of_active_viewers`; `tests/test_brain.py::test_f3_a_cue_is_never_recorded_as_the_speech_of_the_viewer_it_names` | **PASS**, S and memory positive control. |
| R8 — uncertainty and trial gate | `tests/test_automation_bridge.py::test_ac35_every_frame_carries_a_provenance_and_no_question_is_answered`; `tests/test_phase5_docs_status.py::test_ac36_the_trial_file_skips_its_eight_nodes_opening_no_socket`; `tests/test_phase5_docs_status.py::test_ac37_no_document_calls_the_bridge_delivered` | **PASS** for automated safeguards, S/T/D; real-instance answers and eventual tracking of this new report are **NOT-VERIFIABLE** here. |
| R9 — configuration combinations | `tests/test_automation_bridge.py::test_ac39_each_fragment_checks_as_the_guide_says`; `tests/test_automation_bridge.py::test_ac39_combination_8_the_brain_host_finds_the_run_action_in_the_catalog`; `tests/test_automation_bridge.py::test_ac40_the_table_has_twelve_rows_with_every_cell_filled` | **PASS**, S: twelve fragments and the agent-placement limitation. |
| R10 — static constraints | `tests/test_input_ownership.py::test_ac11_no_file_under_core_names_the_vendor_or_the_bridge_module`; `tests/test_profiles.py::test_installed_distribution_discovers_the_shipped_manifests_and_answers_help`; `tests/test_automation_bridge.py::test_ac41_dependencies_are_unchanged_and_the_manifest_ships`; `tests/test_automation_bridge.py::test_ac42_the_guide_is_a_declared_bilingual_pair` | **PASS**, S/V: vendor-free kernel, 18 installed manifests, exactly aiohttp/PyYAML runtime dependencies and the bilingual guides. |

## GUARANTEES

Here `O::` expands to `tests/test_input_ownership.py::`, and `A::` to `tests/test_automation_bridge.py::`. S ran every referenced test.

| Guarantee | Result and command/counterexample |
| --- | --- |
| Total optionality | **PASS** — S/AC1–5, P, V: zero profile changes, disabled references remain unresolved, no required bridge schema setting, unreachable bridge leaves companion ready and owned run/read unavailable with zero frames and no fallback. |
| One owner per family/platform | **PASS** — S: `O::test_ac7_two_default_declarers_are_refused_before_any_effect`, `O::test_ac8_an_owner_list_of_two_is_refused_naming_both`, `O::test_a_concrete_declarer_beats_a_wildcard_declarer`, `O::test_the_explicit_wildcard_key_beats_a_concrete_default`, both AC44 publisher tests and `O::test_the_gated_bindings_never_overlap`; completed F1 reproduction rejects all three non-owner publications. |
| Honest silence | **PASS** — S's ten `A::test_ac16_row_*` cases, AC17 races and D8 tests: exact status/code/cause and zero/one frame counts; pre-write failure refuses, cancellation before send cancels, blocked-hook deadline times out, acknowledgement remains execution_unconfirmed, post-send loss/interruption remains acknowledgement_lost; no run-action success or retry. |
| Allowlist before network | **PASS** — S/AC18–21/45: outside names, group non-members, missing/ambiguous/disabled entries and invalid arguments send zero DoAction frames; group members are reachable subject to every deciding schema; no authorization rule means zero provider calls. |
| No secret value escapes | **FAIL** — executed N2: sequential redaction of overlapping known values exposes `secret-4521` in structural diagnostics through run/Check and in both the resolver's platform label and suggested correction. Standard action/trace/log/UI sweeps and N1's exact cases pass in S. |
| Not delivered | **PASS** — T/D/W: eight skipped trials, pending-trial README/index/guide statuses and no trial record; provenance tests do not claim real-instance answers. |
| Vendor-free kernel | **PASS** — V has no matches; S/AC11 also checks ownership platform literals; C confirms exclusions are supplied by runtime and the identity-registration caller is module-local. |
| Installed aiohttp canary | **PASS** — S's installed-source test, four compiled static mutants, real-transport test and contended negative control; direct F2 controls produce the distinct event sequences above and the loud fallback message. Installed aiohttp: 3.14.3. |

The AC16 terminal rows remain exact: refused/not_allowlisted, refused/instance_action_missing, refused/instance_action_ambiguous, refused/instance_action_disabled, error/invalid_arguments, refused/provider_not_ready, external_unknown/external_effect_unknown/execution_unconfirmed, error/provider_failed/instance_rejected, external_unknown/external_effect_unknown/acknowledgement_lost and cancelled/cancelled. All carry retryable=false. D8 separately checks timeout/timed_out before emission, connection closure and pre-write failure with zero frames, and cancellation/deadline during post-write drain with one frame. AC17's two late-acknowledgement races and AC28's in-flight shutdown preserve uncertainty without re-invocation.

### Phases 0–4 regression check

S ran the complete tests directory. The passing prior guarantees below do not override the newly demonstrated ownership-diagnostic confidentiality failure.

| Prior guarantee | Passing named running enforcement in S |
| --- | --- |
| Default deny, including reads | `tests/test_actions.py::test_zero_rules_refuse_a_read_and_a_write_with_zero_invocations`; `tests/test_users.py::test_read_without_a_rule_is_refused_and_the_provider_is_not_entered` |
| Bounded admission and retention | `tests/test_admission.py::test_full_session_queue_and_global_cap_reject_with_reason_and_depth`; `tests/test_retention.py::test_byte_bound_evicts_oldest_first_and_is_never_exceeded`; bus, dedup, audit and attachment-retention suites |
| Explicit terminal outcomes | `tests/test_actions.py::test_a_silent_write_defaults_to_external_unknown_on_timeout`; `tests/test_actions.py::test_a_confirmation_landing_after_the_deadline_is_never_a_success` |
| Single global startup/shutdown deadlines | `tests/test_main.py::test_a_hanging_settings_validator_is_bounded_by_the_startup_deadline`; `tests/test_lifecycle.py::test_start_honours_a_deadline_established_before_the_phases`; `tests/test_lifecycle.py::test_one_global_shutdown_deadline_caps_the_sum_of_local_timeouts` |
| Existing secret redaction | `tests/test_contracts.py::test_secret_never_reaches_a_trace_diagnostic`; `tests/test_config_ui.py::test_ac35_no_secret_value_reaches_any_response_log_report_or_record`; the additional ownership path fails in N2 |
| Phase 2 R10 deadline ruling | `tests/test_actions.py::test_the_timer_winning_adopts_the_record_of_a_provider_that_answers_its_cancel`; `tests/test_actions.py::test_a_late_success_is_still_replaced_by_the_generic_record`; `tests/test_actions.py::test_a_provider_that_never_answers_is_cut_by_the_timer_at_expiry` |
| No default provider | `tests/test_profiles.py::test_check_config_passes_with_the_speech_endpoints_unset_then_configured`; `tests/test_profiles.py::test_ac43_with_no_speech_endpoint_audio_speak_is_named_not_ready_with_0_requests` |
| Strict moderation default, modes, no permanent bans | `tests/test_moderation.py::test_ac24_the_default_mode_alerts_with_no_platform_request_and_one_fact`; `tests/test_moderation.py::test_ac26_auto_apply_applies_a_delete_at_once_under_the_strict_rules`; `tests/test_moderation.py::test_no_operation_removes_a_viewer_permanently` |
| Viewer-memory bounds and analysis-based eviction | `tests/test_viewer_memory.py::test_ac14_eviction_deletes_the_least_recent_then_least_used_with_one_fact`; `tests/test_viewer_memory.py::test_ac15_the_total_bound_holds_and_the_target_is_never_deleted`; failed-eviction regressions; F3 ordinary-message control |
| Requested-only watch | `tests/test_watch.py::test_ac29_without_activation_no_tick_is_emitted`; `tests/test_watch.py::test_ac29_a_viewers_start_command_starts_nothing` |
| Honest platform capabilities | Full twitch/kick/youtube suites, including `tests/test_youtube.py::test_chat_owned_elsewhere_polls_nothing_and_keeps_chat_write`, on-demand lookup failures, quota/rate refusals, moderation limits and clip outcomes |
| Local-only UI; secrets/actions read-only | `tests/test_config_ui.py::test_ac20_secrets_and_actions_are_read_only_with_every_rule`; that suite's bind/origin/host/session/save-scope tests; ownership edits remain outside Save's writable scope |

### Approved anchors and integration audit

- **D5:** the cumulative action/runtime diff and S's ownership binding tests retain the original requested scope, compute wildcard exclusions centrally and avoid overlap; `test_the_gated_bindings_never_overlap` and the real users/bridge split run successfully. No module supplies exclusions itself (C).
- **D6:** identity registration remains additive, validated before mutation, case-folded and idempotent. AC33's configured-identity and no-identity controls pass; an owned chat pair without a companion identity is refused before transport.
- **D7:** ownership is still resolved before settings hooks. Only hooks declaring `owned_inputs` receive it; ordinary hooks retain their call shape. No finding objects to this approved order.
- **D8:** the synchronous pre-write check/mark/send segment, compress=0, exact interruption outcomes and installed-writer static/behavioural canaries pass, including negative controls. The behavioural test observes one schedule; the static half and held-lock mutation cover the otherwise invisible uncontended-lock case.
- **D9/P12:** `tests/test_contracts.py::test_string_length_bounds_are_enforced`, `test_length_bound_on_a_non_string_node_is_refused`, `test_malformed_length_bounds_are_refused` and `test_schemas_without_length_bounds_validate_as_before` pass. AC15 retains the exact schema and 1–128 bounds, including 0/129 refusal; unsupported keywords remain refused. `A::test_a_bridge_without_companion_name_is_refused_by_the_hook_not_the_triggers` passes. The optional schema and approved examples assertions remain intact.
- **D10/P14:** `tests/test_lifecycle.py::test_a_state_the_module_reported_is_not_overwritten_by_its_phase` and `test_a_module_that_reports_nothing_keeps_todays_records` pass. Both prepare/start_inputs preserve module-authored health, silent modules retain prior records and close still reports stopped. AC3 verifies the final degraded state without a later ready overwrite.
- **D11/P15:** `tests/test_actions.py::test_an_executor_take_over_keeps_the_providers_cause`, `test_a_take_over_without_a_cause_keeps_todays_record_exactly`, `test_an_in_time_conversion_keeps_the_cause_beside_its_note`, `test_a_rejected_conversion_keeps_the_cause_too` and `test_a_shipped_confirmation_lost_cause_survives_a_take_over` pass. Status/code and the phase 2 deadline ruling remain enforced; no-cause controls remain exact.
- **D1–D4:** watch_tick stays outside the chat family; explicit/concrete/wildcard/none precedence, unrestricted undeclared publishers and guarded v1/v2 publishers pass. Trigger/chat refusals do not multiply publication counters. F1's publication routes share the count and once-per-pair diagnostic.
- **Queue, mappings and consumers:** the reader uses put_nowait; AC26 verifies four queued/six dropped events and a subsequent acknowledged request. AC43 compares native and bridge payload keys/kinds and owned-platform filtering. AC32 gates role trust. AC34 distinguishes active_viewers from the native provider's observed_authors. Cues bypass the shared transcript and ordinary users/memory/moderation/watch observers; F3 now also bypasses the brain's post-run recording. Route-safety controls still refuse unsafe ordinary-message effect routes.
- **P22F4/P22F5:** exact-text non-secret controls, pre-bound embedded-secret tests, every current resolver-refusal shape, non-ASCII declared provider names and manifest-discovery fallback tests pass. `tests/test_main.py::test_n1_a_structural_inputs_refusal_without_a_credential_is_unchanged` and `tests/test_input_ownership.py::test_n1_b_a_default_conflict_without_a_credential_is_unchanged` preserve the complete useful wording. The formatter covers dynamic location/reason/path/provider fields while retaining fixed vocabulary. The remaining overlap defect is independently demonstrated in N2.
- **Documentation/packaging:** provenance guards keep study questions unanswered; all twelve configuration fragments execute, including the remote agent's action-only placement. Packaging builds and installs a pristine temporary copy, discovers exactly 18 manifests and checks the launcher outside the checkout. Dependency assertions remain exact. The new guides/design addenda retain their English/French pairing and bilingual checks.

Compared each of the specification's seven allowlisted adaptations against `git diff 9f6c1d9 HEAD`; all ran in S:

| Adapted test | Invariant retained |
| --- | --- |
| `tests/test_contracts.py::test_event_kind_vocabulary_is_the_nine_names_in_order` | Original nine names/order retained; cue appended as tenth, exact tuple/set/default checks. |
| `tests/test_contracts.py::test_platform_notice_kinds_exclude_message_and_watch_tick` | Exact original seven notice kinds; cue additionally excluded. |
| `tests/test_examples.py::test_manifests_are_unique_and_have_coherent_capabilities` | Exact unique sets, per-manifest keys, family declarations and credentials retained; 18 manifests/14 action names with approved P12 amendments. |
| `tests/test_manifest_presentation.py::test_presented_covers_every_shipped_manifest` | Exact presented/shipped-set equality and uniqueness at 18. |
| `tests/test_profiles.py::test_pyproject_ships_the_three_phase_2_manifests_and_no_new_dependency` | Exact manifest/package-data sets at 18; runtime dependency set unchanged. |
| `tests/test_profiles.py::test_installed_distribution_discovers_the_shipped_manifests_and_answers_help` | Exact installed/discovered sets at 18; outside-checkout provenance and launcher checks retained. |
| `tests/test_users.py::test_manifest_is_v2_and_declares_users_read_without_granting_it` | Existing action/no-grant checks retained; enum adds active_viewers while COVERAGE_KIND remains exactly observed_authors, verified by native page tests. |

No weakened safety assertion was established in those seven. Other visible adaptations were also inspected: the examples catalog gains the discovered action while the ready view is unchanged; bilingual registration gains the guide; demo counters become nine; the UI module count becomes 18. The UI's unusable-status helper excludes its numbered temporary parent path from incidental numeric-canary scanning while retaining checks of diagnostic reasons and visible page content.

## NEW FINDINGS

### N2 — MEDIUM: overlapping known values fragment a credential during redaction

This is further evidence of the unresolved complete-protection part of F4/N1. Locations: `core/loader.py:2439`–`2444` (`_redact_known`) delegates to `core/loader.py:2280`–`2284` (`_redact_values`); both P22F5 boundaries consume that helper, including `core/main.py`'s `_InputsShapeError.diagnostic` and the resolver formatter.

The helper sorts values longest first, then repeatedly calls str.replace on the already modified string. A longer known value overlapping the beginning of a shorter credential is removed first. That destroys the shorter credential's full match and leaves its suffix intact. No normalization or truncation is needed: redaction itself creates the fragment. In the reproduction, the original key contains the complete configured password `review-secret-4521`; after redaction it contains `secret-4521`, including the entire last half checked by the new regression convention. Both complete known values are available before rendering.

Executed reproduction (synthetic values, no file writes or instance connection; only parsed-document reads are substituted):

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tests .venv/bin/python - <<'PY'
import asyncio
from pathlib import Path
from unittest.mock import patch
import core.main as app
from test_main import _finite_limits
from test_input_ownership import _decl, _families
from core.loader import resolve_input_ownership, ModuleLoadError

secret = 'review-secret-4521'
other = 'context-prefix-review-'
key = 'context-prefix-' + secret

async def main():
    for inputs in ({key: 'not-a-mapping'}, {'chat': {key: 42}}):
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
                lines = []
                if route == 'run':
                    status = await app.run('config.yaml.example', asyncio.Event(),
                        environ={'OTHER_PASSWORD': other}, diagnostic_reporter=lines.append)
                else:
                    status = await app.check_config('config.yaml.example',
                        environ={'OTHER_PASSWORD': other}, diagnostic_reporter=lines.append)
                print(route, status, lines)
                assert status == 2 and secret[len(secret)//2:] in '\n'.join(lines)
            print('runtime assemblies:', assemble.call_count)
            assert assemble.call_count == 0

asyncio.run(main())
declarations = {
    name: _decl(_families(('chat', [key], True)), name) for name in ('a', 'b')
}
try:
    resolve_input_ownership(['a', 'b'], declarations, {},
                            redacted=frozenset({secret, other}))
except ModuleLoadError as error:
    print(str(error))
    print('credential suffix leaked:', secret[len(secret)//2:] in str(error))
    assert secret[len(secret)//2:] in str(error)
else:
    raise AssertionError('expected refusal')
PY
```

Observed:

```text
run 2 ['inputs.<redacted>secret-4521: must be a mapping of platform to owner']
check_config 2 ['inputs.<redacted>secret-4521: must be a mapping of platform to owner']
runtime assemblies: 0
run 2 ['inputs.chat.<redacted>secret-4521: owner must be a module name or a list of module names']
check_config 2 ['inputs.chat.<redacted>secret-4521: owner must be a module name or a list of module names']
runtime assemblies: 0
inputs: family 'chat': platform '<redacted>secret-4521': owned by default by both 'a' and 'b'; set inputs.chat.<redacted>secret-4521
credential suffix leaked: True
```

An additional executed control removes any dependence on an unrelated environment value: enabled modules `['streamerbot', 'twitch']`, the same bridge password, `modules.twitch.access_token = other`, `inputs = {key: 'not-a-mapping'}`, and `environ={}`. Both strings are now literals at real manifest-declared credential paths. The same parsed-read substitutions and assembly spy were used. Observed:

```text
two declared credentials: run 2 ['inputs.<redacted>secret-4521: must be a mapping of platform to owner']
two declared credentials: check_config 2 ['inputs.<redacted>secret-4521: must be a mapping of platform to owner']
runtime assemblies: 0
```

This second control concerns early structural refusal; it does not claim the abbreviated native-module settings would pass later settings validation. Neither configuration reaches that validation or runtime construction.

Required closure: determine all credential-match spans against the original dynamic text and mask their union, including overlapping matches, before normalization/bounding. Merely changing replacement order or using a longest-match-only substitution does not establish complete protection. Keep fixed vocabulary and existing exact-text non-secret controls. Add overlapping-value regressions through both public entry points and the conflict's full reason/path, asserting the absence of whole credentials and surviving halves across the joined diagnostics. No ownership-order, runtime-permission or network change is required.

No further concrete defect was established in the guard, canary, memory correction, accepted amendments or allowlisted adaptations.

## NOT VERIFIED

- No real automation instance was available or contacted. The eight trial questions remain unanswered: nonexistent action names, latency/blocking queues, enforce-mode behavior, request-version availability, C# HTTP/JSON, custom-event round trip, Wine servers and subscribed-event volume. T skips all eight; documented fixtures do not prove real compatibility.
- The suite's 26 skipped nodes are not claimed as verified. Live native-platform services, real credentials, operator inventories/logs, other operating systems, other Python versions and other aiohttp versions were not exercised. Writer evidence concerns installed aiohttp 3.14.3 and the shown in-memory mutations.
- Independent N1/N2 startup reproductions substitute parsed-document reads; they do not separately exercise disk YAML parsing. S's N1 regression tests do exercise real temporary YAML files through both public routes. N2's new overlapping-value case is not yet a committed regression test.
- No dedicated N2 reproduction was run through the configuration UI's rendered Check page, and no claim of a rendered-page leak is made. Its existing secret/UI suite passes; the demonstrated outputs concern startup/CLI Check and the public resolver.
- F3's direct counterexample/control uses recording providers without persistent viewer-memory writes; persistence, bounds and eviction are exercised separately by S. The canary's behavioral control observes particular schedules, supplemented by its static tests and contended negative control; this is not proof about arbitrary future library implementations.
- No confirmation round trip was assumed (A6), and no separate presence family was demanded (A4).
- AC38's eventual tracking of this new report requires later operator staging/commit, which this review is explicitly forbidden to perform. The report is written but unstaged. The closing gate remains blocked by the executed N2 credential-fragment leak.
