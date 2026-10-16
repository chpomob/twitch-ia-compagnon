# Phase 5 — full-branch closing gate, fourth pass

## VERDICT

**REQUEST_CHANGES.** P22F6 resolves every supplied N2 reproduction at the loader/startup/resolver boundaries. F1–F3 remain resolved, and the original F4 and N1 cases remain corrected. However, the same overlapping-value mechanism still exposes `secret-4521` on the new ownership page and in an ownership-rejection trace. Both were executed on this checkout, with both complete credentials already known to the respective redactor. N3 below is the remaining complete-protection blocker; a green suite does not override it.

The loader primitive has been redesigned successfully. The protection architecture still has independent UI and trace redactors using the discarded sequential-replacement algorithm. **Stop surface patching and redesign the shared protection boundary**, as the operator's rule requires. No implementation change was made by this review.

Reviewed HEAD `15ed223` on `main`, cumulatively against pre-phase implementation `9f6c1d9`, including kernel ownership, public publication surfaces, bindings, native ingestion, bridge lifecycle/mappings/actions, brain consumers, UI, fixtures, tests, packaging and documentation. Also inspected `git diff 12eb1de HEAD` for the six fixes and `git diff b91d4d9 HEAD` for P22F6. The latter changes only `core/loader.py`, `tests/test_input_ownership.py` and `tests/test_main.py`.

Authorities: the phase's `brief.md`, `spec.md`, `plan.md`, `scaffolding/plan-amendments.md`, the first three gate reports and P22F1–P22F6 specifications. D1–D11 and the approved P12/P14/P15 amendments are accepted decisions, with their invariants checked below.

The working tree was initially clean. Only this report is written in the checkout; nothing is staged or committed. Bytecode and pytest cache writes were disabled. Tests use their normal temporary fixtures, including an outside-checkout packaging copy. Independent reproductions use in-memory parsed-document substitutions and recording providers; no configuration or viewer-memory file was written and no real instance was contacted. The initial sandbox command failed with `bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted`; approved escalated execution ran the commands.

## Executed evidence

All commands ran from the repository root with the project virtual environment.

| ID | Command | Observed result |
| --- | --- | --- |
| S | `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/ -q -p no:cacheprovider` | **3659 passed, 26 skipped in 216.89s (0:03:36)**; exit 0. Output captured outside the checkout in `/tmp/phase5-review4-pytest.log`. |
| TD | `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/test_phase5_trials.py tests/test_phase5_docs_status.py -q -p no:cacheprovider` | **27 passed, 8 skipped in 0.33s**; all eight trial nodes skip. |
| P | `git diff --numstat 9f6c1d9 HEAD -- config.yaml.example config.server.yaml.example agent.yaml.example presence.yaml.example` | Empty: zero changed lines in all four profiles. |
| V | `rg -n -i -e 'streamer[._ -]?bot' -e 'nate1280' core/` | No matches. |
| C | `rg -n -e 'excluded_platforms' -e 'add_companion_identities' modules/` | Only `modules/streamerbot/__init__.py:2584`, the intended identity-registration call; no module supplies binding exclusions. |
| W | `rg -n -i '\bdelivered\b' README.md docs --glob '*.md' --glob '!docs/campaigns/**'` | Bridge delivery claims remain negated in README, index and both guides; remaining matches concern unrelated design statements. |
| Q | `git diff --check` | No whitespace errors. |

Every named test below ran in S with passing assertions. PASS describes the tested local contract, not real-instance compatibility. FAIL identifies an independently disproved guarantee. Reproduction assertions deliberately confirming a leak exit zero when the defect is present.

## PREVIOUS FINDINGS

N2 was replayed first, by extracting its self-contained script from pass 3. Only the old leak assertions were replaced with closure assertions: `status == 2`, neither whole credential nor either half of `review-secret-4521` in the joined diagnostics, and neither in the resolver error. Configuration, real loading/validation/reporting and assembly spies were unchanged.

Observed N2 output:

```text
run 2 ['inputs.<redacted>: must be a mapping of platform to owner']
check_config 2 ['inputs.<redacted>: must be a mapping of platform to owner']
runtime assemblies: 0
run 2 ['inputs.chat.<redacted>: owner must be a module name or a list of module names']
check_config 2 ['inputs.chat.<redacted>: owner must be a module name or a list of module names']
runtime assemblies: 0
inputs: family 'chat': platform '<redacted>': owned by default by both 'a' and 'b'; set inputs.chat.<redacted>
credential suffix leaked: False
```

The second N2 control was also replayed, with enabled modules `['streamerbot', 'twitch']`, `modules.streamerbot.password = 'review-secret-4521'`, `modules.twitch.access_token = 'context-prefix-review-'` and `environ={}`. Both family and platform structural cases were run, extending the supplied family-only control. Both routes returned exactly the same two redacted diagnostics above, status 2, and zero runtime assemblies. Thus this correction does not depend on an unrelated environment value. These early-refusal controls do not claim that abbreviated native settings pass later settings validation; N3 separately uses complete accepted settings.

N1-A/B were replayed from pass 2 with their leak assertions reversed, as in pass 3. Observed:

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

The first pass's F1–F4 scripts were completed through the existing pass-2 runner, which reverses defect assertions and catches F2's expected canary refusal without changing repository code. Executed replay:

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
| F1 — public supervision bypass | **RESOLVED.** `0 1`, `0 2`, `0 3`: zero chat deliveries through bus, emit and record_and_emit, one rejection counted for each attempt. S also checks recorder ordering, payload changes before await/inside the recorder and shared diagnostic deduplication. |
| F2 — canary misses implicit suspension | **RESOLVED.** `original canary PASS`; `changed canary REFUSED: D8 fallback route: module-local synchronous writer`; mutant `writes: 0 pending: True`. S's installed-client behavioural control and contended negative control also pass. |
| F3 — cue recorded as viewer speech | **RESOLVED.** `cue success`, followed by `[]` memory calls. Direct positive control also passed: named cue=0, anonymous cue=0, ordinary chat=1 confirmed. |
| F4 — credential exposure in ownership diagnostics | **PARTIAL overall; original cases RESOLVED.** Outputs remain `inputs: family '<redacted>': platform 'x': value 'streamerbot': family is outside the vocabulary (chat, viewers, cues)` and `inputs: family 'chat': platform '<redacted>': owner 'not-enabled' is not enabled`, each followed by `activations: 0`. The full confidentiality guarantee still fails in N3. |
| N1 — incomplete structural/reason protection | **PARTIAL overall; supplied A/B cases RESOLVED.** Exact outputs above preserve refusal, field identification, both conflicting providers and the correction path. No original credential appears. N3 prevents complete-protection closure. |
| N2 — overlapping known values fragment a credential | **PARTIAL overall; every supplied reproduction RESOLVED.** Startup/Check structural refusals and resolver location/reason/path now contain `<redacted>` without the whole credentials or either password half. The same overlap still fragments that password in two other ownership output paths, demonstrated in N3. |

P22F6's primitive now masks the union of all credential matches in the original text, including self-overlaps, so **that primitive no longer creates overlap fragments by construction; union coverage replaces the replacement-order assumption**. `_redact_known` delegates to it. Matches are found before normalization/bounding; no masked output is searched again.

Beyond S's new N2 matrix, an independent interval-merging oracle was executed against `_redact_values`: all binary texts of lengths 0–8, all sets of zero to three values from `('', 'a', 'b', 'aa', 'ab', 'ba', 'bb', 'aba', 'bab', 'aaa', 'bbb', '<redacted>')`, in both iteration orders, plus three Unicode/control-character cases. Observed: `loader primitive vs independent interval oracle: 152792 cases PASS; both value orders PASS`. The oracle enumerates original-text occurrences, merges sorted intervals and reconstructs their complement; it does not use the implementation's boolean mask. Empty values, marker values, adjacency, nesting, repeated and chained overlaps are covered locally. This establishes the loader algorithm, not the unchanged redactors identified below.

Legitimate publication was checked independently with `_publish_through(_intruder(_guarded_runtime()[0]), route, _chat_event('y'))`: bus/emit/record_and_emit delivered cumulative counts 1/2/3, each with zero rejections. The guard remains functional for owners.

## REQUIREMENT MAP

`O::` expands to `tests/test_input_ownership.py::`, `A::` to `tests/test_automation_bridge.py::`. All listed tests ran in S; confidentiality assessments also use N3.

| Requirement | Named running test(s) | Result |
| --- | --- | --- |
| R1 — total optionality | `O::test_ac1_main_check_config_exits_zero_with_the_pre_phase_output`; `O::test_ac2_the_shipped_profiles_have_zero_changed_lines_since_the_phase_base`; `A::test_ac3_an_unreachable_instance_leaves_a_ready_companion_and_a_degraded_bridge` | **PASS**, S/P: baseline diagnostics/profiles unchanged; unavailable run/read retain companion readiness and final degraded bridge, without fallback. |
| R2 — preparation ownership | `O::test_ac7_two_default_declarers_are_refused_before_any_effect`; `O::test_ac8_an_owner_list_of_two_is_refused_naming_both`; `tests/test_config_ui.py::test_p5_ac10_the_core_page_shows_the_resolved_ownership_read_only` | **PASS** for resolution, pre-effect refusal and read-only display. Display confidentiality fails separately under R6/N3. |
| R3 — runtime ownership | `O::test_f1_no_public_route_publishes_a_non_owned_input`; `O::test_f1_an_owned_pair_is_delivered_once_through_each_route`; `O::test_ac44_an_undeclared_publisher_is_refused_a_resolved_or_none_pair`; `O::test_ac14_the_real_users_and_bridge_split_the_viewer_read` | **PASS**, S and direct F1 controls: guarded public routes, v1/v2 compatibility and non-overlapping viewer bindings. N3 does not bypass rejection. |
| R4 — honest outcomes | `A::test_ac16_row_acknowledged_is_execution_unconfirmed`; `A::test_d8_a_pre_write_failure_sends_nothing_and_withdraws_readiness`; `A::test_d8_behaviour_the_frame_reaches_the_transport_in_the_marking_segment`; `A::test_d8_negative_control_the_behavioural_check_catches_f2s_mutation` | **PASS**, S and F2 replay; all ten outcome rows and interruption paths pass. |
| R5 — allowlist and authorization | `A::test_ac18_only_allowlisted_names_are_reachable`; `A::test_ac20_reconciliation_names_the_unmatched_entries_once`; `A::test_ac21_a_principal_without_a_rule_is_refused_before_the_provider`; `A::test_ac45_every_matching_group_entry_must_accept` | **PASS**, S: refusals before DoAction and authorization before provider entry. |
| R6 — lifecycle, bounds and credentials | `A::test_ac24_the_challenge_answer_and_readiness_only_after_the_inventory`; `A::test_ac26_a_full_queue_drops_and_counts_and_never_stalls_the_connection`; `A::test_ac4_each_failure_mode_degrades_with_its_reason_and_leaks_nothing`; `tests/test_main.py::test_n2_overlapping_known_values_leave_no_credential_fragment`; `O::test_n2_an_overlapping_known_value_leaves_no_fragment_of_the_credential` | **FAIL**, N3. All named tests pass and supplied N2 boundaries are corrected, but ownership page/trace redactors still disclose a password suffix. Other tested lifecycle/connection/bounds contracts pass. |
| R7 — cues and input replacement | `A::test_ac29_a_valid_cue_is_one_normalised_event_the_default_policy_accepts`; `A::test_ac43_each_fixture_maps_to_the_native_kind_and_key_set`; `A::test_ac34_the_owned_channel_reads_one_page_of_active_viewers`; `tests/test_brain.py::test_f3_a_cue_is_never_recorded_as_the_speech_of_the_viewer_it_names` | **PASS**, S and direct memory control. |
| R8 — evidence and trial gate | `A::test_ac35_every_frame_carries_a_provenance_and_no_question_is_answered`; `tests/test_phase5_docs_status.py::test_ac36_the_trial_file_skips_its_eight_nodes_opening_no_socket`; `tests/test_phase5_docs_status.py::test_ac37_no_document_calls_the_bridge_delivered` | **PASS** for automated evidence/status safeguards, S/TD; real-instance answers and eventual tracking of this new report are **NOT-VERIFIABLE** here. |
| R9 — configuration combinations | `A::test_ac39_each_fragment_checks_as_the_guide_says`; `A::test_ac39_combination_8_the_brain_host_finds_the_run_action_in_the_catalog`; `A::test_ac40_the_table_has_twelve_rows_with_every_cell_filled` | **PASS**, S: twelve fragments and remote-agent placement limitation. |
| R10 — static constraints | `O::test_ac11_no_file_under_core_names_the_vendor_or_the_bridge_module`; `tests/test_profiles.py::test_installed_distribution_discovers_the_shipped_manifests_and_answers_help`; `A::test_ac41_dependencies_are_unchanged_and_the_manifest_ships`; `A::test_ac42_the_guide_is_a_declared_bilingual_pair` | **PASS**, S/V: vendor-free kernel, exactly 18 installed manifests, unchanged aiohttp/PyYAML runtime dependencies and bilingual guides. |

## GUARANTEES

| Guarantee | Result and executed command/counterexample |
| --- | --- |
| Total optionality | **PASS** — S/AC1–5, P/V: no profile changes, disabled references remain unresolved, no required bridge schema setting; unreachable/authentication/protocol failures leave companion ready and bridge unavailable, zero action frames, no ownership fallback. |
| One owner per family/platform | **PASS** — S: `O::test_ac7_two_default_declarers_are_refused_before_any_effect`, `O::test_ac8_an_owner_list_of_two_is_refused_naming_both`, `O::test_a_concrete_declarer_beats_a_wildcard_declarer`, `O::test_the_explicit_wildcard_key_beats_a_concrete_default`, both AC44 publisher tests and `O::test_the_gated_bindings_never_overlap`; F1 replay rejects all three non-owner publication routes. |
| Honest silence | **PASS** — S's ten `A::test_ac16_row_*` cases, AC17 races and D8 interruption tests enforce exact statuses/codes/causes and zero/one frame counts; acknowledgement stays execution_unconfirmed, post-send loss stays acknowledgement_lost, no run-action success or retry. |
| Allowlist before network | **PASS** — S/AC18–21/45: outside names, group non-members, missing/ambiguous/disabled actions and invalid arguments send zero DoAction frames; a group member remains reachable subject to every deciding schema; no grant means zero provider invocations. |
| No secret value escapes | **FAIL** — N3's executed, accepted-settings UI request emits `[hidden]secret-4521`; an actual guarded publication emits `input.ownership.rejected` with `[REDACTED]secret-4521`. Both redactors have both complete credentials. Loader N2 controls and standard action/log/UI sweeps pass but do not cover these overlaps. |
| Not delivered | **PASS** — TD/W: eight skipped trials, pending-trial README/index/guides and no trial record; fixture provenance does not purport to answer real-instance questions. |
| Vendor-free kernel | **PASS** — V finds no bridge/vendor name; S/AC11 also checks ownership platform literals; C confirms centrally supplied binding exclusions and module-local identity registration. |
| Installed aiohttp canary | **PASS** — S's installed-source and actual-transport tests, four compiled static suspension mutants and held-lock behavioural negative control; F2 replay rejects its mutation with the named fallback route. Evidence concerns installed aiohttp 3.14.3. |

AC16's terminal rows retain: refused/not_allowlisted, refused/instance_action_missing, refused/instance_action_ambiguous, refused/instance_action_disabled, error/invalid_arguments, refused/provider_not_ready, external_unknown/external_effect_unknown/execution_unconfirmed, error/provider_failed/instance_rejected, external_unknown/external_effect_unknown/acknowledgement_lost and cancelled/cancelled. Every error mapping has retryable=false. D8 additionally tests timeout/timed_out before emission, pre-write connection failure with zero frames, and cancellation/deadline during post-write drain with one frame. AC17's late acknowledgements and AC28's in-flight shutdown preserve uncertainty without re-invocation.

### Phases 0–4 regression check

S ran the complete tests directory. The existing enforcement below passes. The newly exercised overlap trace case prevents a blanket redaction guarantee.

| Prior guarantee | Named running enforcement in S and assessment |
| --- | --- |
| Default deny, including reads | **PASS** — `tests/test_actions.py::test_zero_rules_refuse_a_read_and_a_write_with_zero_invocations`; `tests/test_users.py::test_read_without_a_rule_is_refused_and_the_provider_is_not_entered`. |
| Bounded admission and retention | **PASS** — `tests/test_admission.py::test_full_session_queue_and_global_cap_reject_with_reason_and_depth`; `tests/test_retention.py::test_byte_bound_evicts_oldest_first_and_is_never_exceeded`; bus/dedup/audit/attachment-retention suites. |
| Explicit terminal outcomes | **PASS** — `tests/test_actions.py::test_a_silent_write_defaults_to_external_unknown_on_timeout`; `tests/test_actions.py::test_a_confirmation_landing_after_the_deadline_is_never_a_success`. |
| Global startup/shutdown deadlines | **PASS** — `tests/test_main.py::test_a_hanging_settings_validator_is_bounded_by_the_startup_deadline`; `tests/test_lifecycle.py::test_start_honours_a_deadline_established_before_the_phases`; `tests/test_lifecycle.py::test_one_global_shutdown_deadline_caps_the_sum_of_local_timeouts`. |
| Secret redaction | Existing tests **PASS** — `tests/test_contracts.py::test_secret_never_reaches_a_trace_diagnostic`; `tests/test_config_ui.py::test_ac35_no_secret_value_reaches_any_response_log_report_or_record`. Full guarantee **FAIL**, executed N3 overlaps in page/trace; these passing tests do not cover them. |
| Phase 2 R10 deadline ruling | **PASS** — `tests/test_actions.py::test_the_timer_winning_adopts_the_record_of_a_provider_that_answers_its_cancel`; `tests/test_actions.py::test_a_late_success_is_still_replaced_by_the_generic_record`; `tests/test_actions.py::test_a_provider_that_never_answers_is_cut_by_the_timer_at_expiry`. |
| No default provider | **PASS** — `tests/test_profiles.py::test_check_config_passes_with_the_speech_endpoints_unset_then_configured`; `tests/test_profiles.py::test_ac43_with_no_speech_endpoint_audio_speak_is_named_not_ready_with_0_requests`. |
| Strict moderation default/modes; no permanent bans | **PASS** — `tests/test_moderation.py::test_ac24_the_default_mode_alerts_with_no_platform_request_and_one_fact`; `tests/test_moderation.py::test_ac26_auto_apply_applies_a_delete_at_once_under_the_strict_rules`; `tests/test_moderation.py::test_no_operation_removes_a_viewer_permanently`. |
| Viewer-memory bounds and analysis-based eviction | **PASS** — `tests/test_viewer_memory.py::test_ac14_eviction_deletes_the_least_recent_then_least_used_with_one_fact`; `tests/test_viewer_memory.py::test_ac15_the_total_bound_holds_and_the_target_is_never_deleted`; failed-eviction tests and F3's ordinary-message control. |
| Requested-only watch | **PASS** — `tests/test_watch.py::test_ac29_without_activation_no_tick_is_emitted`; `tests/test_watch.py::test_ac29_a_viewers_start_command_starts_nothing`. |
| Honest platform capabilities | **PASS** — complete twitch/kick/youtube suites, including `tests/test_youtube.py::test_chat_owned_elsewhere_polls_nothing_and_keeps_chat_write`, on-demand lookup/quota/rate failures, moderation limitations and clip outcomes. |
| Local-only UI; secrets/actions read-only | **PASS** for access/write policy — `tests/test_config_ui.py::test_ac20_secrets_and_actions_are_read_only_with_every_rule`, plus bind/origin/host/session/save-scope tests. Ownership remains outside Save's writable scope. Confidentiality fails separately in N3. |

### Approved anchors and integration audit

- **D1–D4:** chat excludes watch_tick/cue; explicit/concrete/wildcard/none precedence and undeclared v1/v2 compatibility pass. A refusal by trigger evaluation or transcript append does not multiply publication counters. F1 routes share their once-per-pair diagnostic. N3's trace reproduction still rejects the event and increments exactly one rejection.
- **D5:** cumulative registry/runtime diff retains requested channel/scope restrictions, central wildcard exclusions and no overlapping valid viewer bindings. `O::test_the_gated_bindings_never_overlap` and the real users/bridge split pass; C finds no module supplying exclusions.
- **D6:** additive identity registration validates before mutation, case-folds, is idempotent and never removes an identity. `O::test_ac33_an_added_identity_is_refused_by_the_anti_echo_invariant`, `test_adding_the_same_identity_twice_is_a_no_op`, `test_adding_identities_is_append_only` and `test_an_invalid_identity_is_refused_and_nothing_is_added` pass. AC33 refuses owned chat without an identity before transport.
- **D7:** ownership remains resolved before settings validation; only a hook declaring the `owned_inputs` keyword receives that view. Existing hooks retain their call shape. This approved order is not a finding.
- **D8:** pre-write check/mark/send remains a synchronous segment on the installed compress=0 path. Static and behavioural canaries, negative controls and exact interruption outcomes pass. No production transport or network-permission change was introduced by P22F2/P22F6.
- **D9/P12:** `tests/test_contracts.py::test_string_length_bounds_are_enforced`, `test_length_bound_on_a_non_string_node_is_refused`, `test_malformed_length_bounds_are_refused` and `test_schemas_without_length_bounds_validate_as_before` pass. AC15 preserves the exact 1–128 schema, including 0/129 refusal; unsupported keywords remain refused. `A::test_a_bridge_without_companion_name_is_refused_by_the_hook_not_the_triggers` passes. The setting remains optional in the manifest schema and required by its value-free validation hook when enabled.
- **D10/P14:** `tests/test_lifecycle.py::test_a_state_the_module_reported_is_not_overwritten_by_its_phase` and `test_a_module_that_reports_nothing_keeps_todays_records` pass: self-reported prepare/start_inputs health stands, silent-module records remain exact and close still reports stopped. AC3 checks the final degraded state and absence of a subsequent ready overwrite.
- **D11/P15:** `tests/test_actions.py::test_an_executor_take_over_keeps_the_providers_cause`, `test_a_take_over_without_a_cause_keeps_todays_record_exactly`, `test_an_in_time_conversion_keeps_the_cause_beside_its_note`, `test_a_rejected_conversion_keeps_the_cause_too` and `test_a_shipped_confirmation_lost_cause_survives_a_take_over` pass. Causes survive take-over/conversion; status/code and the phase-2 timer ruling remain unchanged; no-cause controls retain their exact record.
- **Queue/mapping/consumers:** the reader still uses put_nowait; AC26 proves four queued/six dropped events without blocking a subsequent acknowledged request. AC43 compares native/bridge payload keys/kinds and platform ownership filters. AC32 gates role trust. AC34 preserves active_viewers versus native observed_authors. Cues bypass the shared transcript, directory, ordinary memory/moderation/watch observers and the brain's post-run memory record. F3's direct positive control retains ordinary-chat recording and route-safety tests still refuse unsafe ordinary-message effects.
- **P22F1–P22F6:** public guards preserve recorder ordering and synchronous call-site refusals; canaries reject the original mutant; cue memory correction remains narrowly keyed by event kind; early refusal still precedes preparation/assembly. N1's exact-text controls and every resolver-refusal shape pass. P22F6 adds 22 passing nodes, including the 16-case primitive matrix and four disk-YAML public-route cases (environment/declared credential × family/platform), without deleting an old test. Only the loader helper was redesigned; UI and contract redactors were left unchanged, which N3 demonstrates is insufficient for complete protection.
- **Documentation/packaging:** all twelve guide fragments execute, including action discovery on the remote brain while families/cues remain local to its agent. Fixture provenance keeps trial questions unanswered. Packaging builds/installs a pristine temporary copy outside the checkout, discovers exactly 18 manifests and checks the launcher. Runtime dependency assertions remain exact; bilingual guide/design checks pass.

Compared the seven specification-allowlisted adaptations against `git diff 9f6c1d9 HEAD`; every named test ran in S:

| Adapted test | Invariant retained |
| --- | --- |
| `tests/test_contracts.py::test_event_kind_vocabulary_is_the_nine_names_in_order` | Exact original nine names/order/default retained, cue appended tenth, tuple/set checks intact. |
| `tests/test_contracts.py::test_platform_notice_kinds_exclude_message_and_watch_tick` | Exact original seven notice kinds; cue additionally excluded. |
| `tests/test_examples.py::test_manifests_are_unique_and_have_coherent_capabilities` | Exact unique sets, keys, declarations and credentials retained; 18 manifests/14 actions with approved P12 assertions. |
| `tests/test_manifest_presentation.py::test_presented_covers_every_shipped_manifest` | Exact presented/shipped set equality and uniqueness at 18. |
| `tests/test_profiles.py::test_pyproject_ships_the_three_phase_2_manifests_and_no_new_dependency` | Exact manifest/package-data sets at 18, unchanged runtime dependency set. |
| `tests/test_profiles.py::test_installed_distribution_discovers_the_shipped_manifests_and_answers_help` | Exact installed/discovered sets at 18; outside-checkout provenance and launcher assertions retained. |
| `tests/test_users.py::test_manifest_is_v2_and_declares_users_read_without_granting_it` | Action/no-grant assertions retained; enum adds active_viewers while COVERAGE_KIND remains exactly observed_authors, separately enforced by native page tests. |

No weakened safety assertion was established in those seven. Other visible adaptations retain their enforcement: catalog discovery gains the new action while the ready view is unchanged, bilingual registration gains the guide, demo counters become nine and UI module count becomes 18. The UI unusable-status helper excludes its numbered temporary parent from incidental numeric-canary scans while retaining diagnostic-reason/page checks. These changes are accepted adaptations, not findings.

## NEW FINDINGS

### N3 — MEDIUM: the overlap redesign does not reach ownership pages or rejection traces

This is further evidence of the still-partial F4/N1/N2 confidentiality guarantee. P22F6 fixes `core/loader.py::_redact_values`; two independently used redactors still search an already modified string:

- `core/config_ui/__init__.py:1485`–`1490`, `_redact`, sorts raw/JSON/HTML forms longest first through `_redaction_forms` and repeatedly replaces them. The phase-5 `_ownership_section` at lines 3182–3258 renders raw resolved platform names through this function in both cell text and a data attribute. Resolver diagnostics are not involved in a successful table render.
- `core/contracts.py:2234`–`2238`, `_redact`, likewise replaces each value sequentially. `Supervision.emit` uses sanitize_trace, so `_InputGuard.reject` at `core/runtime.py:1366`–`1368` exposes the same suffix in the platform of `input.ownership.rejected`. The underlying chat publication is correctly refused; the disclosure is in its diagnostic trace.

Both complete known values are available. Removing `context-prefix-review-` first destroys the full `review-secret-4521` match and leaves `secret-4521`. Neither normalization nor truncation is necessary. These redaction loops predate P22F6; the new ownership outputs consume them, so this is a residual integration gap rather than a claim that the new loader algorithm fragments text.

Executed reproduction (synthetic credentials, no file writes, no instance connection; only parsed configuration reads are substituted). The first configuration has complete settings and **passes the real check_config with status 0**, removing any reliance on an invalid native configuration. The UI is exercised through its real authenticated request handler. The trace control uses the same real guard/runtime helper as the ownership regression tests and installs both known values through Supervision.redact, the loader's normal registration surface:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tests .venv/bin/python - <<'PY'
import asyncio
import re
from pathlib import Path
from unittest.mock import patch
import core.main as app
import core.config_ui as ui_app
from test_config_ui import _get, _logged_in, _section
from test_main import _finite_limits
from test_input_ownership import _guarded_runtime, _intruder, _chat_event

secret = 'review-secret-4521'
other = 'context-prefix-review-'
key = 'context-prefix-' + secret
config = {
    'modules_directory': str(Path('modules').resolve()),
    'enabled_modules': ['streamerbot', 'twitch'],
    'modules': {
        'streamerbot': {
            'url': 'ws://127.0.0.1:9/', 'companion_name': 'Companion',
            'password': secret,
        },
        'twitch': {
            'client_id': 'review-client-id', 'client_secret': 'review-native-key',
            'access_token': other, 'broadcaster_id': 'channel-1',
            'bot_user_id': 'bot-1', 'companion_name': 'Companion',
        },
    },
    'limits': _finite_limits(), 'inputs': {'chat': {key: 'none'}},
}

async def main():
    lines = []
    with patch.object(app, 'read_base', return_value=config), \
         patch.object(app, 'read_overlay', return_value={}):
        status = await app.check_config('config.yaml.example', environ={},
                                        diagnostic_reporter=lines.append)
    print('check_config:', status, lines)
    assert status == 0
asyncio.run(main())

with patch.object(ui_app, 'read_base', return_value=config), \
     patch.object(ui_app, 'read_overlay', return_value={}):
    ui = ui_app.ConfigUI(ui_app.UISettings.from_argv(
        ['--config', 'config.yaml.example']), environ={})
    response = _get(ui, '/core', _logged_in(ui))
    section = _section(response.body.decode(), 'input-ownership')
    print('HTTP status:', response.status)
    print('both declared credentials known:', {secret, other} <= ui.secret_values)
    print(next(row for row in re.findall(r'<tr.*?</tr>', section)
               if 'secret-4521' in row))
    print('credential suffix leaked:', secret[len(secret)//2:] in section)
    assert response.status == 200 and {secret, other} <= ui.secret_values
    assert secret[len(secret)//2:] in section

async def trace_control():
    runtime, _, _ = _guarded_runtime()
    runtime.supervision.redact((secret, other))
    context = _intruder(runtime)
    seen = []
    async def observe(event):
        seen.append(event)
    runtime.bus.subscribe('**', observe)
    event = _chat_event(key)
    await context.bus.publish(event['type'], event['payload'], event['metadata'])
    print('trace:', [(e['type'], e['payload']) for e in seen])
    print('rejections:', runtime.supervision.snapshot()['input_ownership_rejections'])
    assert [e['type'] for e in seen] == ['input.ownership.rejected']
    assert secret[len(secret)//2:] in seen[0]['payload']['platform']
asyncio.run(trace_control())
PY
```

Observed:

```text
check_config: 0 []
HTTP status: 200
both declared credentials known: True
<tr data-input-family="chat" data-input-platform="[hidden]secret-4521"><td>chat</td><td>[hidden]secret-4521</td><td class="owner">none</td><td class="origin">explicit</td><td class="candidates"></td></tr>
credential suffix leaked: True
trace: [('input.ownership.rejected', {'module': 'intruder', 'family': 'chat', 'platform': '[REDACTED]secret-4521'})]
rejections: 1
```

The full configured password has 18 characters; its tested last half `cret-4521` is present inside the surviving `secret-4521` in both outputs. This is a credential fragment disclosure, not an observation that a marker or a fixed vocabulary word coincides with a credential.

Required closure is an architectural one: make the union-of-original-matches protection apply to the redaction consumers that actually emit ownership data, including UI serialization forms and trace keys/values, with one defined semantic contract rather than independent replacement-order assumptions. Preserve exact non-secret text, UI structural-token/opaque-field protections, rendering escapes, bounds and publication refusal semantics. Prove the page response and emitted trace above contain neither whole credentials nor surviving halves. Do not attempt another field-specific patch; the operator's stop-patching/redesign rule now applies to these remaining consumers. No code, permission, ownership-order or network change is needed to reproduce the defect.

No further concrete defect was established in the guard, canary, memory correction, accepted amendments or allowlisted adaptations.

## NOT VERIFIED

- No real automation instance was available or contacted. All eight trial questions remain unanswered: nonexistent action names, real latency/blocking queues, enforce-mode behaviour, minimum request versions, C# HTTP/JSON availability, custom-event round trip, Wine servers and subscribed-event volume. TD skips all eight; fixtures do not establish real compatibility.
- The full suite's 26 skipped nodes are not claimed as verified. Live native services, real credentials, operator logs/inventories, other operating systems, Python versions and aiohttp versions were not exercised. Writer evidence concerns installed aiohttp 3.14.3 and the executed/tested mutations, not arbitrary future libraries.
- Independent startup/UI reproductions substitute parsed-document reads, so they do not independently test disk YAML parsing. S's N1/N2 public-route regressions do use real temporary YAML files. N3 is an independent review reproduction, not a committed regression test.
- No dedicated N3 UI Check POST or log-filter reproduction was run. The demonstrated UI disclosure is the authenticated GET `/core` response; the second disclosure is an actual emitted ownership-rejection trace, without a persistent audit-file write. No unexecuted additional leak is claimed.
- F3's direct control uses recording providers without persistent viewer-memory writes; S separately covers persistence, retention and eviction. The canary's behavioural half observes particular schedules and is supplemented by static mutants and the held-lock negative control.
- No confirmation round trip was assumed (A6), and no separate presence family was demanded (A4). These approved decisions are not findings.
- This report is written but unstaged. Its eventual tracking under AC38 requires the operator's later staging/commit, which this review is explicitly forbidden to perform. Phase 5's closing gate remains blocked by N3's executed page/trace credential-fragment disclosures.
