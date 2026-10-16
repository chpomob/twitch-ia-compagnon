# Phase 5 — full-branch closing gate, fifth pass

## VERDICT

**APPROVE.** P22F7 closes N3 at the actual ownership page and rejection-trace boundaries. F1–F4, N1 and N2 remain resolved. The full suite passes **3,677 tests, with 26 skipped**. No new concrete defect or fourth reachable consumer of the discarded overlapping-value replacement mechanism was established.

Reviewed `main` at `d169150`, cumulatively against pre-phase implementation `9f6c1d9`, and inspected P22F7 from `03d16d2`. Scope includes ownership resolution/public publication, family-gated bindings, native ingestion, bridge actions/lifecycle/mappings, cue consumers, UI rendering, fixtures, packaging, documentation and the seven closing fixes. Authorities: `brief.md`, `spec.md`, `plan.md`, `scaffolding/plan-amendments.md`, the four previous reports and P22F1–P22F7 specifications. Approved adaptations and D1–D11 are decisions whose invariants are checked below.

The checkout was initially clean. Only this report is created; nothing is staged or committed. Bytecode and pytest cache writes were disabled. Tests use normal temporary fixtures, including an outside-checkout packaging copy. Independent reproductions use in-memory parsed-configuration substitutions and synthetic credentials, with no real instance connection. The sandbox failed before executing commands (`bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted`); escalated execution was approved and used. An initial report-write heredoc failed on its nested delimiter; a subsequent status check confirmed it created or changed nothing, before this corrected write.

## Executed evidence

All commands ran from the repository root with the project virtual environment. `S` ran every named test below; no failed or xfailed node was reported.

| ID | Command / check | Observed result |
| --- | --- | --- |
| S | `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/ -q -p no:cacheprovider` | **3677 passed, 26 skipped in 218.08s (0:03:38)**; exit 0. P22F7's 3659-pass floor is exceeded by 18 nodes. |
| TD | `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/test_phase5_trials.py tests/test_phase5_docs_status.py -q -rs -p no:cacheprovider` | **27 passed, 8 skipped in 0.32s**; exit 0. Eight trial questions skip with reasons naming their unset variables; socket-count guard passes. |
| P | `git diff --numstat 9f6c1d9 HEAD -- config.yaml.example config.server.yaml.example agent.yaml.example presence.yaml.example` | Empty: zero changed lines in all four shipped profiles. |
| V | `rg -n -i -e 'streamer[._ -]?bot' -e 'nate1280' core/` | No matches. |
| C | `rg -n -e 'excluded_platforms' -e 'add_companion_identities' modules/` | Only the intended identity-registration call at `modules/streamerbot/__init__.py:2584`; no module supplies binding exclusions. |
| W | `rg -n -i '\bdelivered\b' README.md docs/README.md docs/automation-bridge.md docs/automation-bridge.fr.md` | Bridge status explicitly remains “not delivered”; TD also checks the documentation for positive delivery claims. |
| Q | `git diff --check`; `git status --short`; `git diff --cached --stat` | No whitespace errors; status and staged diff empty before this sole report write. |

### N3: exact configuration, authenticated page and actual rejection trace

Ran the pass-4 reproduction first, retaining its exact configuration and changing only obsolete leak assertions and the row selector looking for the leaked suffix. Repeated with a public platform row, then separately with a non-secret platform containing quotes, angle brackets, ampersand and Unicode. No instance transport opens in these controls.

Self-contained closure command:

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
def protected(text):
    for value in (secret, other):
        for fragment in (value, value[:len(value)//2], value[len(value)//2:]):
            assert fragment not in text, fragment

async def checked():
    lines = []
    with patch.object(app, 'read_base', return_value=config), \
         patch.object(app, 'read_overlay', return_value={}):
        status = await app.check_config('config.yaml.example', environ={},
                                        diagnostic_reporter=lines.append)
    print('check_config:', status, lines)
    assert (status, lines) == (0, [])
asyncio.run(checked())

with patch.object(ui_app, 'read_base', return_value=config), \
     patch.object(ui_app, 'read_overlay', return_value={}):
    ui = ui_app.ConfigUI(ui_app.UISettings.from_argv(
        ['--config', 'config.yaml.example']), environ={})
    response = _get(ui, '/core', _logged_in(ui))
    section = _section(response.body.decode(), 'input-ownership')
    row = next(row for row in re.findall(r'<tr.*?</tr>', section)
               if 'data-input-platform="[hidden]"' in row)
    print('HTTP status:', response.status)
    print('both declared credentials known:', {secret, other} <= ui.secret_values)
    print(row)
    assert response.status == 200 and {secret, other} <= ui.secret_values
    assert '<td>[hidden]</td>' in row
    protected(response.body.decode())

async def traced():
    runtime, _, chat = _guarded_runtime()
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
    assert seen[0]['payload'] == {
        'module': 'intruder', 'family': 'chat', 'platform': '[REDACTED]'}
    protected(str(seen[0]['payload']))  # keys and values together
    assert chat.appended == []
    assert runtime.supervision.snapshot()['input_ownership_rejections'] == 1
asyncio.run(traced())
PY
```

Observed exact-configuration replay:

```text
check_config: 0 []
HTTP status: 200
both declared credentials known: True
<tr data-input-family="chat" data-input-platform="[hidden]"><td>chat</td><td>[hidden]</td><td class="owner">none</td><td class="origin">explicit</td><td class="candidates"></td></tr>
credential suffix leaked: False
trace: [('input.ownership.rejected', {'module': 'intruder', 'family': 'chat', 'platform': '[REDACTED]'})]
rejections: 1
```

The platform cell text **and** data attribute are exactly `[hidden]`. Trace keys are exactly `module`, `family`, `platform`; values are exactly `intruder`, `chat`, `[REDACTED]`. Assertions exclude both complete credentials and both halves of each from the whole page body and joined trace payload. Neither the former surviving `secret-4521` nor the password's last half `cret-4521` survives. Chat publication remains refused.

The additional row retained `non-secret-control` exactly in both cell and attribute. A separate escaping control retained platform `room-"><tag>& café 'quoted'` as `room-&quot;&gt;&lt;tag&gt;&amp; café &#x27;quoted&#x27;` in both HTML positions, without a raw `<tag>`. Its rejection trace retained the complete original platform string exactly. Positive controls through bus, emit and record_and_emit each delivered one legitimate chat event with zero rejections. Thus neither hiding every platform nor changing refusal behavior accounts for closure.

## PREVIOUS FINDINGS

All seven are **RESOLVED** in the reviewed branch; the confidentiality settlements now include the outputs N3 previously left outside the loader fix.

| Finding | Status and observed output |
| --- | --- |
| F1 — supervision publication bypass | **RESOLVED.** Replay: `0 1`, `0 2`, `0 3`: zero chat deliveries via bus, emit and record_and_emit, one rejection per attempt. Positive controls deliver one legitimate event per route with zero rejections. S checks payload mutations before await/inside the recorder, recorder ordering and shared diagnostic deduplication. |
| F2 — implicit suspension missed by canary | **RESOLVED.** `original canary PASS`; `changed canary REFUSED: D8 fallback route: module-local synchronous writer`; mutant `writes: 0 pending: True`. S also passes installed-client transport ordering, four static suspension mutants and the contended-lock negative control. |
| F3 — cue recorded as viewer speech | **RESOLVED.** Replay: `cue success`, then `[]` memory calls. `tests/test_brain.py::test_f3_a_cue_is_never_recorded_as_the_speech_of_the_viewer_it_names` passes named/anonymous cue exclusions and its ordinary-chat positive control. |
| F4 — ownership diagnostic credential exposure | **RESOLVED.** Original cases produce the two redacted lines below, each with `activations: 0`. S preserves exact non-secret wording, credential-before-bound protection, public Check and every resolver refusal shape. N3 closes the residual page/trace integration gap. |
| N1 — structural paths/conflict reason and path | **RESOLVED.** Both public routes return status 2 and the exact protected structural lines below, with `runtime assemblies: 0`. Conflict location/correction path contain `<redacted>` and retain both provider names. Direct whole/first-half/last-half absence assertions pass across joined diagnostics and exception text. |
| N2 — overlapping known values | **RESOLVED.** Both public routes return the same structural lines below, status 2 and zero assemblies. Conflict reports the protected location/reason/path and `credential suffix leaked: False`. Repeated family/platform cases with two manifest-declared credentials and `environ={}`: same result. Whole/half assertions pass. N3 proves the other ownership outputs share the correction. |
| N3 — page/rejection trace | **RESOLVED.** Check `(0, [])`, HTTP 200, platform cell/attribute `[hidden]`, trace `{'module': 'intruder', 'family': 'chat', 'platform': '[REDACTED]'}`, one rejection, no chat delivery. Both complete credentials and first/last halves are absent. |

Observed F4 output:

```text
inputs: family '<redacted>': platform 'x': value 'streamerbot': family is outside the vocabulary (chat, viewers, cues)
activations: 0
inputs: family 'chat': platform '<redacted>': owner 'not-enabled' is not enabled
activations: 0
```

Observed N1 and N2 output, independently replayed for each:

```text
run 2 ['inputs.<redacted>: must be a mapping of platform to owner']
check_config 2 ['inputs.<redacted>: must be a mapping of platform to owner']
runtime assemblies: 0
run 2 ['inputs.chat.<redacted>: owner must be a module name or a list of module names']
check_config 2 ['inputs.chat.<redacted>: owner must be a module name or a list of module names']
runtime assemblies: 0
inputs: family 'chat': platform '<redacted>': owned by default by both 'a' and 'b'; set inputs.chat.<redacted>
```

Replay method: under `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tests .venv/bin/python`, extracted the three bash/Python blocks from pass 3 using `re.findall(r'```bash\n(.*?)\n```', report, re.S)` and executed their heredoc bodies in memory. Block 0 replays N1, block 1 invokes pass 2's completed F1–F4 runner, block 2 replays N2. Replaced N2's old leak assertions with status-2 and whole/half absence assertions. Also extracted N1-A/B directly from pass 2 and asserted whole/first-half/last-half absence. The additional N2 control sets `modules.streamerbot.password = 'review-secret-4521'`, `modules.twitch.access_token = 'context-prefix-review-'`, both enabled and `environ={}`, for both structural locations. Its abbreviated native settings exercise early refusal; N3 separately proves complete accepted settings.

### Shared contract and enumeration closure

**One-sentence contract:** `core.contracts.mask_known` locates every occurrence of every caller-supplied serialization form of every known value in the original text, masks the union of those spans once, and preserves every other character before callers bound, escape or truncate it.

There is one exported implementation in `core/contracts.py`, carrying forward the loader's P22F6 algorithm. The three consumers are:

- `core/loader.py::_redact_values`: loader diagnostics, `_redact_known`, startup structural refusals and the ownership formatter; marker `<redacted>`.
- `core/contracts.py::_redact`: `sanitize_trace` keys and values, hence Supervision and ownership-rejection traces; marker `[REDACTED]`.
- `core/config_ui/__init__.py::_redact`: ownership cells/data attributes and other UI data/log rendering; marker `[hidden]`, with raw, both JSON spellings and their HTML forms supplied by `_redaction_forms`.

Their replacement loops are deleted. Overlapping values/forms, self-overlaps and adjacency use the original-match union; masked text is never searched again. The implementation resides in contracts rather than loader, allowing trace/UI/loader consumers to share it without a loader/contracts import cycle. The loader wrapper delegates rather than retaining another implementation.

Enumeration command: `rg -n '\.replace\(|\.sub\(|def .*redact|mask_known|sanitize_trace' core modules demo --glob '*.py'`, supplemented by cumulative diffs and the guard's 53-source-file scan (`core`, `modules`, `demo`, Python sources under `docs`). No further known-value replacement loop was found. `modules/audit::_redact` recursively removes sensitive fields by key; it is not a sequential known-value text redactor. Ownership traces reach audit after supervision sanitization. Other replacements concern file renames, timestamps, fixed formatting or fixed diagnostic syntax. **No fourth reachable consumer of this overlapping-value class was established; no enumeration blocker remains on the evidence checked.**

Independent interval oracle: enumerated all binary texts of lengths 0–8 and all sets of zero to three values from `('', 'a', 'b', 'aa', 'ab', 'ba', 'bb', 'aba', 'bab', 'aaa', 'bbb', '<redacted>')`. The oracle locates original-text intervals, merges sorted overlapping/adjacent intervals and reconstructs their complement, without the implementation's boolean mask. Loader/shared results match: **152789 cases PASS; reversed value order PASS**. UI/form comparison used all sets of up to three of seven quote/HTML/JSON/Unicode/control-character and N3 values against six mixed-form/public texts: **384 cases PASS**. Exact non-secret UI and trace controls pass too.

The structural guard is **non-vacuous**. `tests/test_contracts.py::test_n3_no_module_defines_its_own_sequential_known_value_replacement` scans real source, requires one `mask_known` definition, and explicitly checks all three wrappers delegate without loops/comprehensions. Six negative controls include both historical loops, tuple-target regex, compiled regex, comprehension side effects and while-loop replacement. Independently patched `Path.read_text` in memory to append `for value in values: text = text.replace(value, '[hidden]')` inside a function to `core/runtime.py`, then invoked the actual structural test: **REFUSED with AssertionError**. Unmodified source passes. No file was changed by this mutation control.

## REQUIREMENT MAP

`O::` expands to `tests/test_input_ownership.py::`; `A::` expands to `tests/test_automation_bridge.py::`. Every named node ran in S. PASS assesses the executable local contract, with real-instance limits below.

| Requirement | Named running test(s) | Result |
| --- | --- | --- |
| R1 — total optionality | `O::test_ac1_main_check_config_exits_zero_with_the_pre_phase_output`; `O::test_ac2_the_shipped_profiles_have_zero_changed_lines_since_the_phase_base`; `A::test_ac3_an_unreachable_instance_leaves_a_ready_companion_and_a_degraded_bridge` | **PASS**, S/P: unchanged baseline/profiles, companion readiness, final degraded bridge and no fallback. |
| R2 — preparation ownership | `O::test_ac7_two_default_declarers_are_refused_before_any_effect`; `O::test_ac8_an_owner_list_of_two_is_refused_naming_both`; `tests/test_config_ui.py::test_p5_ac10_the_core_page_shows_the_resolved_ownership_read_only` | **PASS**, S/N3: pre-effect refusal, shared resolution and protected read-only display. |
| R3 — runtime ownership | `O::test_f1_no_public_route_publishes_a_non_owned_input`; `O::test_f1_an_owned_pair_is_delivered_once_through_each_route`; `O::test_ac44_an_undeclared_publisher_is_refused_a_resolved_or_none_pair`; `O::test_ac14_the_real_users_and_bridge_split_the_viewer_read` | **PASS**, S/direct controls: public routes, legacy compatibility and non-overlapping bindings. |
| R4 — honest outcomes | `A::test_ac16_row_acknowledged_is_execution_unconfirmed`; `A::test_d8_a_pre_write_failure_sends_nothing_and_withdraws_readiness`; `A::test_d8_behaviour_the_frame_reaches_the_transport_in_the_marking_segment`; `A::test_d8_negative_control_the_behavioural_check_catches_f2s_mutation` | **PASS**, S/F2: all ten outcome rows, boundary, interruptions and uncertainty causes. |
| R5 — allowlist/authorization | `A::test_ac18_only_allowlisted_names_are_reachable`; `A::test_ac20_reconciliation_names_the_unmatched_entries_once`; `A::test_ac21_a_principal_without_a_rule_is_refused_before_the_provider`; `A::test_ac45_every_matching_group_entry_must_accept` | **PASS**, S: no DoAction frame on refusal; no grant means no provider invocation; schema precedence/intersection retained. |
| R6 — lifecycle/bounds/credentials | `A::test_ac24_the_challenge_answer_and_readiness_only_after_the_inventory`; `A::test_ac26_a_full_queue_drops_and_counts_and_never_stalls_the_connection`; `A::test_ac4_each_failure_mode_degrades_with_its_reason_and_leaks_nothing`; `O::test_n3_the_ownership_page_and_the_rejection_trace_leave_no_credential_fragment` | **PASS**, S/N1/N2/N3: lifecycle, queue and actual credential-bearing output controls. |
| R7 — cues/input replacement | `A::test_ac29_a_valid_cue_is_one_normalised_event_the_default_policy_accepts`; `A::test_ac43_each_fixture_maps_to_the_native_kind_and_key_set`; `A::test_ac34_the_owned_channel_reads_one_page_of_active_viewers`; `tests/test_brain.py::test_f3_a_cue_is_never_recorded_as_the_speech_of_the_viewer_it_names` | **PASS**, S/F3: mappings, policy ingress, viewer coverage and cue memory exclusion. |
| R8 — evidence/trial gate | `A::test_ac35_every_frame_carries_a_provenance_and_no_question_is_answered`; `tests/test_phase5_docs_status.py::test_ac36_the_trial_file_skips_its_eight_nodes_opening_no_socket`; `tests/test_phase5_docs_status.py::test_ac37_no_document_calls_the_bridge_delivered` | **PASS**, S/TD, for automated provenance/status enforcement and this gate artifact. Real-instance answers and later tracking of this new file are **NOT-VERIFIABLE** here. |
| R9 — configuration combinations | `A::test_ac39_each_fragment_checks_as_the_guide_says`; `A::test_ac39_combination_8_the_brain_host_finds_the_run_action_in_the_catalog`; `A::test_ac40_the_table_has_twelve_rows_with_every_cell_filled` | **PASS**, S: twelve guide fragments and action-only remote-agent placement. |
| R10 — static constraints | `O::test_ac11_no_file_under_core_names_the_vendor_or_the_bridge_module`; `tests/test_profiles.py::test_installed_distribution_discovers_the_shipped_manifests_and_answers_help`; `A::test_ac41_dependencies_are_unchanged_and_the_manifest_ships`; `A::test_ac42_the_guide_is_a_declared_bilingual_pair` | **PASS**, S/V: vendor-free kernel, 18 installed manifests, unchanged dependencies and bilingual docs. |

## GUARANTEES

| Guarantee | Result and executed enforcement |
| --- | --- |
| Total optionality | **PASS** — S/AC1–5, P/V: absent/disabled baseline unchanged; unreachable/authentication/protocol/inventory failures leave companion ready and bridge unavailable; zero DoAction frames, no fallback. |
| One owner per family/platform | **PASS** — S: `O::test_ac7_two_default_declarers_are_refused_before_any_effect`, `test_ac8_an_owner_list_of_two_is_refused_naming_both`, `test_a_concrete_declarer_beats_a_wildcard_declarer`, `test_the_explicit_wildcard_key_beats_a_concrete_default`, AC44 v1/v2 controls and `test_the_gated_bindings_never_overlap`; F1 replay rejects/counts/traces every refused route. |
| Honest silence | **PASS** — S's ten `A::test_ac16_row_*` cases, AC17/D8 interruption tests and F2 replay: exact statuses/codes/causes, zero/one frames; acceptance remains execution_unconfirmed, post-send loss acknowledgement_lost, with no run success or retry. |
| Allowlist before network | **PASS** — S/AC18–21/45: outside names, group non-members, missing/disabled/ambiguous inventory and bad arguments send zero DoAction frames; authorization refusal invokes no provider. |
| No secret value escapes on reviewed surfaces | **PASS** — S's action/log/UI sweeps, direct F4/N1/N2/N3 replays, union/form oracle and exact public-text controls; whole credentials and surviving halves absent from ownership diagnostics, page and trace. |
| Not delivered | **PASS** — TD/W: no trial record, eight skipped trials, pending-trial README/index/guides and fixture provenance guards. Implementation-gate approval makes no real-instance delivery claim. |
| Vendor-free kernel | **PASS** — V finds nothing; S/AC11 checks ownership platform literals; C confirms central exclusions and module-local identity registration. |
| Installed aiohttp canary | **PASS** — S's installed-source/actual-transport tests and static/contended negative controls, plus F2 replay. Current installed behavior is guarded; future versions are not certified. |

### Phases 0–4 regression check

The full tests directory ran in S. All prior guarantees below pass, including the corrected ownership overlap disclosure.

| Prior guarantee | Named running enforcement in S |
| --- | --- |
| Default deny, including reads | `tests/test_actions.py::test_zero_rules_refuse_a_read_and_a_write_with_zero_invocations`; `tests/test_users.py::test_read_without_a_rule_is_refused_and_the_provider_is_not_entered`. |
| Bounded admission/retention | `tests/test_admission.py::test_full_session_queue_and_global_cap_reject_with_reason_and_depth`; `tests/test_retention.py::test_byte_bound_evicts_oldest_first_and_is_never_exceeded`; bus/dedup/audit/attachment-retention suites. |
| Explicit terminal outcomes | `tests/test_actions.py::test_a_silent_write_defaults_to_external_unknown_on_timeout`; `tests/test_actions.py::test_a_confirmation_landing_after_the_deadline_is_never_a_success`. |
| Global startup/shutdown deadlines | `tests/test_main.py::test_a_hanging_settings_validator_is_bounded_by_the_startup_deadline`; `tests/test_lifecycle.py::test_start_honours_a_deadline_established_before_the_phases`; `test_one_global_shutdown_deadline_caps_the_sum_of_local_timeouts`. |
| Secret redaction | `tests/test_contracts.py::test_secret_never_reaches_a_trace_diagnostic`; `tests/test_config_ui.py::test_ac35_no_secret_value_reaches_any_response_log_report_or_record`; N1/N2/N3 regressions and independent replays. |
| Phase 2 R10 deadline ruling | `tests/test_actions.py::test_the_timer_winning_adopts_the_record_of_a_provider_that_answers_its_cancel`; `test_a_late_success_is_still_replaced_by_the_generic_record`; `test_a_provider_that_never_answers_is_cut_by_the_timer_at_expiry`. |
| No default provider | `tests/test_profiles.py::test_check_config_passes_with_the_speech_endpoints_unset_then_configured`; `test_ac43_with_no_speech_endpoint_audio_speak_is_named_not_ready_with_0_requests`. |
| Strict moderation default/modes; no permanent bans | `tests/test_moderation.py::test_ac24_the_default_mode_alerts_with_no_platform_request_and_one_fact`; `test_ac26_auto_apply_applies_a_delete_at_once_under_the_strict_rules`; `test_no_operation_removes_a_viewer_permanently`. |
| Viewer-memory bounds/analysis-based eviction | `tests/test_viewer_memory.py::test_ac14_eviction_deletes_the_least_recent_then_least_used_with_one_fact`; `test_ac15_the_total_bound_holds_and_the_target_is_never_deleted`; failed-eviction tests and F3's ordinary-chat control. |
| Requested-only watch | `tests/test_watch.py::test_ac29_without_activation_no_tick_is_emitted`; `test_ac29_a_viewers_start_command_starts_nothing`. |
| Honest platform capabilities | Complete twitch/kick/youtube suites, including `tests/test_youtube.py::test_chat_owned_elsewhere_polls_nothing_and_keeps_chat_write`, on-demand lookup/quota/rate controls, moderation limitations and clip outcomes. |
| Local-only UI; secrets/actions read-only | `tests/test_config_ui.py::test_ac20_secrets_and_actions_are_read_only_with_every_rule`, bind/origin/host/session/save-scope tests. Ownership stays outside Save's writable scope. |

### Approved anchors and defect hunt

- **D1–D4/F1:** family classification, explicit/concrete/wildcard/none precedence and undeclared v1/v2 compatibility retain one ownership predicate. Trigger/transcript refusals do not multiply publication counters. Recorders precede publication checks; payload mutations are checked at publication. Deduplication is not the boundary.
- **D5:** cumulative registry/runtime diff retains requested channel/scope restrictions and central wildcard exclusions. `O::test_the_gated_bindings_never_overlap` and real users/bridge split pass; no module supplies exclusions. Native ingestion gates preserve writes; YouTube's on-demand lookup keeps quota/rate/error outcomes with no poller; Kick's unowned authentic webhook produces no accepted publication.
- **D6:** `O::test_ac33_an_added_identity_is_refused_by_the_anti_echo_invariant`, `test_adding_the_same_identity_twice_is_a_no_op`, `test_adding_identities_is_append_only` and `test_an_invalid_identity_is_refused_and_nothing_is_added` pass. Additions validate before mutation, case-fold, and never remove identities. Missing owned-chat identity is refused before transport.
- **D7:** ownership resolves before settings validation in startup/Check; only hooks declaring `owned_inputs` receive it. Existing hook call shapes remain.
- **D8/F2:** pre-write check/mark/send stays synchronous on the installed compress=0 path. The installed-source and actual-transport canaries reject explicit/implicit suspension mutants, including under contention. Pre-send interruption stays certain; post-send interruption stays uncertain with one frame.
- **D9/P12:** `tests/test_contracts.py::test_string_length_bounds_are_enforced`, `test_length_bound_on_a_non_string_node_is_refused`, `test_malformed_length_bounds_are_refused` and `test_schemas_without_length_bounds_validate_as_before` pass. AC15 keeps the exact 1–128 schema, 0/129 refusal and rejection of unsupported keywords. `A::test_a_bridge_without_companion_name_is_refused_by_the_hook_not_the_triggers` passes with a value-free field diagnostic.
- **D10/P14:** `tests/test_lifecycle.py::test_a_state_the_module_reported_is_not_overwritten_by_its_phase` and `test_a_module_that_reports_nothing_keeps_todays_records` pass: self-reported prepare/start_inputs health stands; silent modules retain their records; close still stops; hook failure still degrades. AC3 checks final degradation and no subsequent ready overwrite.
- **D11/P15:** `tests/test_actions.py::test_an_executor_take_over_keeps_the_providers_cause`, `test_a_take_over_without_a_cause_keeps_todays_record_exactly`, `test_an_in_time_conversion_keeps_the_cause_beside_its_note`, `test_a_rejected_conversion_keeps_the_cause_too` and `test_a_shipped_confirmation_lost_cause_survives_a_take_over` pass. Cause survives takeover/conversion; status/code, timer ruling and exact no-cause records remain.
- **Queue/mapping/consumers/F3:** put_nowait keeps the socket reader moving; AC26 proves four queued/six dropped and a later acknowledged request. AC43 compares native/bridge kinds/key sets on all three platforms and unowned-platform filtering; AC32 gates role trust; AC34 retains active_viewers versus native observed_authors. Cues bypass transcript/directory/ordinary memory/moderation/watch observation and brain memory recording. Ordinary-chat memory and ordinary-message route safety remain enforced.
- **P22F4–P22F7, over-redaction/rendering/refusal:** exact no-secret diagnostics, non-ASCII provider names, manifest-discovery fallback, credential-before-bound handling and every resolver refusal shape pass. N3 checks joined fragments and exact public rows/payloads. The independent oracle verifies the complement of matched spans, not just secret absence. UI forms expand before one mask; escaping follows masking. Structural-token/opaque-field tests stay green; no whole-page rewrite was introduced. Trace keys/values sanitize before byte fitting. Diagnostic emission cannot turn a refused chat publication into delivery.
- **Docs/packaging:** twelve guide fragments execute without instance transport, including action-only remote-agent placement. Provenance never converts documented fixtures into observed trial answers. Packaging installs a pristine temporary copy, discovers exactly 18 manifests outside the checkout and checks the launcher. Dependency/bilingual assertions pass.

Compared the seven specification-allowlisted adaptations with `git diff 9f6c1d9 HEAD`; all ran in S:

| Allowlisted test | Invariant retained |
| --- | --- |
| `tests/test_contracts.py::test_event_kind_vocabulary_is_the_nine_names_in_order` | Exact original nine names/order/default; cue appended tenth; tuple/set checks retained. |
| `tests/test_contracts.py::test_platform_notice_kinds_exclude_message_and_watch_tick` | Exact original seven notice kinds; cue additionally excluded. |
| `tests/test_examples.py::test_manifests_are_unique_and_have_coherent_capabilities` | Exact unique sets, keys/families/credentials; 18 manifests/14 actions with approved P12 assertions. |
| `tests/test_manifest_presentation.py::test_presented_covers_every_shipped_manifest` | Exact presented/shipped equality and uniqueness at 18. |
| `tests/test_profiles.py::test_pyproject_ships_the_three_phase_2_manifests_and_no_new_dependency` | Exact manifest/package-data sets at 18; runtime dependencies unchanged. |
| `tests/test_profiles.py::test_installed_distribution_discovers_the_shipped_manifests_and_answers_help` | Exact installed/discovered sets at 18; outside-checkout provenance and launcher checks retained. |
| `tests/test_users.py::test_manifest_is_v2_and_declares_users_read_without_granting_it` | Action/no-grant checks retained; widened shared enum, while native COVERAGE_KIND remains exactly observed_authors and native pages enforce it. |

No weakened safety assertion was established in those adaptations. Other visible count/catalog/bilingual adaptations preserve their enforcement. No concrete loader/runtime ownership disagreement, wildcard binding overlap, stalled inbound bound, invented native payload fields or unsupported delivery claim was established.

## NEW FINDINGS

**None established by an executed counterexample.** N3 and the previously incomplete confidentiality guarantee are resolved. The stop-patching requirement is satisfied by one original-match union contract, all three enumerated consumers, deleted replacement loops and a tested recurrence guard.

## NOT VERIFIED

- No real automation instance was available or contacted. Eight questions remain unanswered: nonexistent-action behavior, latency/blocking queues, enforce-mode authentication, request-version availability, C# HTTP/JSON, custom-event round trip, Wine servers and subscribed-event volume. Approval closes the implementation gate; the bridge remains **not delivered — awaiting real-instance trial**.
- The suite's 26 skipped nodes are not claimed as verified. Live platform services, actual credentials, operator inventories/logs, other operating systems/Python versions and other aiohttp versions were not exercised.
- Independent startup/UI reproductions substitute parsed reads rather than independently exercising disk YAML. S's N1/N2 public-route regressions use real temporary YAML. Independent F3 uses recording providers; persistence/bounds/eviction are exercised separately by S.
- The interval oracle exhausts its stated finite raw domain and samples the stated form domain; it does not prove future callers supply every encoding they emit. Trace's documented guarantee is configured verbatim substrings, not arbitrary re-encoding or splitting credentials across unrelated fields.
- Structural/canary guards are regression checks, not proofs about arbitrary future Python implementations. The structural guard rejects the historical loops and tested variants; the canary combines installed-source checks, particular observed transport schedules and a contended mutant.
- No confirmation round trip was assumed (A6): acknowledged actions remain unconfirmed. No separate presence family was demanded (A4).
- AC38's later tracking of this new report requires operator staging/commit, explicitly forbidden to this review. This file is left unstaged; existing gate reports are tracked. Eventual tracking of the fifth report is outside this execution.
