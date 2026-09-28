# Phase 4 — full-branch closing gate, first pass

## VERDICT: REQUEST_CHANGES

Reviewed `main` at `b9c337986337db9d612afe806e7386f43907240e`, as the cumulative diff `835d9b4..b9c3379` (the implementation starts after the scaffolding commit), against `spec.md`, `plan.md` and binding decision 6c in `brief.md`. This is not a per-commit review. Phase 4 cannot close: the executed counterexamples below expose base-file overwrite, secret disclosure, ignored draft edits, unbounded request admission and an unbounded supervisor wait.

**Suite command S**, executed from the repository root:

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/ -q -p no:cacheprovider
```

**Result: 2889 passed, 18 skipped in 92.02s; exit 0.** The environment variable prevents bytecode writes; the requested pytest invocation is otherwise unchanged. The initial sandbox attempt failed before execution (`bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted`); the successful run used the authorized execution fallback. Existing untracked `docs/runs/` directories were left alone. No source, test or configuration was edited or staged.

Commands C below are additional executed probes, not tests added to the repository. They use actual handlers/parsers with in-memory configuration reads and intercepted filesystem sinks; they create no configuration files and do not bind sockets. PASS in the map means the requirement's reviewed behavior passes the cited evidence; FAIL can coexist with all its existing tests passing.

## REQUIREMENT MAP

Every named test below ran and passed under **S**, including its collected parameterizations. Failed requirements need new regression coverage: the currently green named tests do not enforce the counterexamples.

| Requirement | Gate result | Named running tests and evidence |
|---|---|---|
| R1 — separate UI, bind/token/Host/Origin/CSRF guards | PASS | `tests/test_config_ui.py::test_the_module_entry_point_calls_main`; `tests/test_config_ui.py::test_ac1_a_non_loopback_host_is_refused_before_any_bind`; `tests/test_config_ui.py::test_ac2_each_start_has_a_fresh_256_bit_token`; `tests/test_config_ui.py::test_ac3_every_route_without_a_session_is_refused_without_content`; `tests/test_config_ui.py::test_ac4_a_post_without_or_with_a_wrong_csrf_token_is_refused`; `tests/test_config_ui.py::test_ac4_a_non_post_to_a_state_changing_path_is_405`; `tests/test_config_ui.py::test_ac6_refused_hosts_get_403_even_on_get`; `tests/test_config_ui.py::test_ac6_refused_origins_get_403_and_change_nothing`; `tests/test_config_ui.py::test_ac5_the_socket_patch_is_effective`. Command S. This is request-core/startup evidence, not a browser/listener smoke test. |
| R2 — shared overlay, precedence, base preservation | **FAIL** | `tests/test_overlay.py::test_ac7_merged_document_is_exact_and_inputs_are_unmutated`; `tests/test_overlay.py::test_ac7_overlay_null_replaces_the_base_value`; `tests/test_overlay.py::test_ac9_overlay_below_minimum_is_refused_then_deleting_it_accepts`; `tests/test_overlay.py::test_ac9_run_hands_the_module_the_overlay_value`; `tests/test_overlay.py::test_ac9_explicit_overlay_is_honoured_by_main_and_run`; `tests/test_overlay.py::test_overlay_is_merged_before_environment_resolution`; `tests/test_overlay.py::test_relative_modules_directory_resolves_from_the_base_directory`; `tests/test_overlay.py::test_ac10_bad_overlay_names_the_file_never_the_content`. S passes; **C/F1** bypasses base preservation and exposes inconsistent tilde-path handling. |
| R3 — all titles/defaults and annotation validation | PASS | `tests/test_manifest_presentation.py::test_presented_covers_every_shipped_manifest`; `tests/test_manifest_presentation.py::test_every_setting_node_has_a_title`; `tests/test_manifest_presentation.py::test_every_documented_default_is_declared`; `tests/test_manifest_presentation.py::test_every_declared_default_validates_against_its_node`; `tests/test_manifest_presentation.py::test_default_violating_the_node_is_refused_as_default`; `tests/test_manifest_presentation.py::test_default_does_not_constrain_validated_values`; `tests/test_manifest_presentation.py::test_property_named_default_is_a_property_not_an_annotation`. Command S. |
| R4 — generated base/core/module pages and honest controls | **FAIL** | `tests/test_config_ui.py::test_ac15_the_base_page_lists_every_module_and_the_origin`; `tests/test_config_ui.py::test_ac16_every_schema_path_is_shown_with_title_help_and_default`; `tests/test_config_ui.py::test_ac17_a_module_added_as_a_directory_gets_its_titled_page`; `tests/test_config_ui.py::test_ac17_no_ui_source_file_names_a_shipped_module`; `tests/test_config_ui.py::test_ac18_unrenderable_nodes_get_notices_and_controls_keep_the_schema`; `tests/test_config_ui.py::test_ac19_the_twitch_page_shows_the_configured_channel_policy`; `tests/test_config_ui.py::test_ac20_the_core_page_renders_every_declared_limit`; `tests/test_config_ui.py::test_ac20_secrets_and_actions_are_read_only_with_every_rule`; `tests/test_config_ui.py::test_ac20_an_uncovered_action_is_listed_until_a_rule_covers_it`; `tests/test_config_ui.py::test_ac21_readiness_combines_the_check_verdict_and_the_running_state`. S passes; **C/F3** shows a false-looking boolean control whose runtime value is true and whose false draft is ignored. |
| R5 — unsaved Check through existing path, all diagnostics, no side effects | **FAIL** | `tests/test_config_ui.py::test_ac38_an_unsaved_invalid_edit_fails_and_writes_nothing`; `tests/test_config_ui.py::test_ac38_an_unsaved_fix_passes_and_writes_nothing`; `tests/test_config_ui.py::test_ac22_unresolved_references_are_all_reported_and_stop_the_check`; `tests/test_config_ui.py::test_ac22_every_invalid_setting_is_reported_in_one_check`; `tests/test_config_ui.py::test_ac23_check_signals_nothing_starts_nothing_and_creates_no_socket`; `tests/test_config_ui.py::test_bridge_check_runs_the_real_checker_from_a_running_loop`. S passes, including the real checker; **C/F3** demonstrates that a posted false draft disappears before reaching Check. |
| R6 — exact writing scope, managed path, atomicity, stale checks, removal | **FAIL** | `tests/test_config_ui.py::test_ac24_each_save_writes_exactly_its_override_and_logs_paths_only`; `tests/test_config_ui.py::test_ac25_remove_override_restores_the_base_value_and_prunes`; `tests/test_config_ui.py::test_ac26_an_invalid_save_returns_a_field_diagnostic_and_writes_nothing`; `tests/test_config_ui.py::test_ac26_a_stale_save_or_remove_is_refused`; `tests/test_config_ui.py::test_ac27_writes_outside_the_scope_or_at_protected_fields_are_refused`; `tests/test_config_ui.py::test_ac27_posted_protected_or_out_of_scope_fields_are_refused`; `tests/test_config_ui.py::test_ac27_hand_written_actions_and_secrets_survive_an_unrelated_save`; `tests/test_config_ui.py::test_ac27_a_save_or_remove_through_a_yaml_alias_leaves_the_other_path_unchanged`; `tests/test_config_ui.py::test_ac27_an_ancestor_save_must_keep_every_protected_descendant`; `tests/test_config_ui.py::test_ac28_an_interrupted_write_leaves_the_previous_overlay_intact`; `tests/test_config_ui.py::test_ac28_the_write_targets_only_the_managed_overlay`. S passes; **C/F1** reaches replacement of the base through authenticated `/save`. |
| R7 — supervised Apply, status publication/classification, drift, bounded result | **FAIL** | `tests/test_config_ui.py::test_ac29_a_failing_on_disk_check_refuses_and_touches_no_process`; `tests/test_config_ui.py::test_ac30_a_ready_record_of_the_child_with_the_on_disk_digest_is_accepted`; `tests/test_config_ui.py::test_ac30_a_child_exiting_2_is_refused_with_its_status_and_diagnostic`; `tests/test_config_ui.py::test_ac30_a_child_writing_nothing_within_the_window_is_unknown`; `tests/test_config_ui.py::test_ac31_apply_never_signals_a_foreign_record_pid`; `tests/test_config_ui.py::test_ac32_drift_follows_the_overlay_and_the_record`; `tests/test_config_ui.py::test_ac39_ac40_a_child_writing_only_an_unaccepted_record_is_unknown`; `tests/test_config_ui.py::test_ac40_every_listed_mutation_of_v_is_unusable_with_a_value_free_reason`; `tests/test_status_record.py::test_three_transitions_publish_three_records_in_order`; `tests/test_status_record.py::test_the_record_describes_the_loaded_configuration`; `tests/test_status_record.py::test_a_status_path_naming_a_configuration_file_is_refused`. S passes; **C/F5** finds an unbounded stop wait before the Apply window even begins. |
| R8 — no secret in responses, diagnostics, logs or reports | **FAIL** | `tests/test_config_ui.py::test_ac35_no_secret_value_reaches_any_response_log_report_or_record`; `tests/test_config_ui.py::test_check_diagnostics_pass_the_redaction_guard`; `tests/test_config_ui.py::test_d9_the_refused_tail_is_redacted_and_value_free`; `tests/test_config_ui.py::test_ac24_a_saved_path_naming_a_secret_is_logged_redacted`. S passes; **C/F2** leaks both a short listed secret and a long declared credential into actual HTML responses. |
| R9 — dependencies and offline resources | PASS | `tests/test_main.py::test_pyproject_declares_the_console_script_and_the_build_backend_for_tests`; `tests/test_config_ui.py::test_pages_use_inline_assets_and_relative_targets_only`; `tests/test_config_ui.py::test_r9_module_pages_use_inline_assets_and_relative_targets_only`; `tests/test_config_ui.py::test_ac36_the_url_audit_finds_every_external_target`; `tests/test_config_ui.py::test_ac36_a_configured_url_is_escaped_text_only`. Command S; source inspection confirms inline CSS/JS, relative forms/links and no render-time network fetch. |
| R10 — operator documentation | PASS | `tests/test_config_ui.py::test_ac37_doc_launch_and_local_only`; `tests/test_config_ui.py::test_ac37_doc_overlay_rule_and_merge`; `tests/test_config_ui.py::test_ac37_doc_writable_and_read_only_blocks`; `tests/test_config_ui.py::test_ac37_doc_secrets_policy`; `tests/test_config_ui.py::test_ac37_doc_restart_drift_and_status_file`. Command S plus reading `docs/config-ui.md`; all required subjects are documented. Implementation violations of those documented promises are F1–F3/F5, not missing documentation. |

## GUARANTEES

- **Writing scope / 6c — FAIL overall:** S's AC24/AC27 tests and `_scope_refusal`/`_protect_save`/`_protect_remove` enforce the five allowed top-level blocks and reject direct `actions`/`secrets`, credentials, references and protected ancestors; actions have a read-only reason and uncovered-action listing. However **C/F1** lets `/save` replace the base with the overlay document, deleting its existing `actions`/`secrets` along with other base content. No direct grant-widening payload was found; an absolute “no request or helper can change those blocks” claim is disproved by the destructive path mismatch.
- **No secret escapes — FAIL:** **C/F2** returns HTTP 200 with a listed variable's value in an input and a declared credential's value in generated channel-control names. The broad AC35 sweep in S does not cover these cases.
- **Overlay — FAIL overall:** S covers recursive mapping merge, scalar/list/null replacement, explicit/implicit paths, runtime/CLI parity, removal, stale refusal and atomic replacement; AC24 checks the base remains intact in normal paths. **C/F1** bypasses preservation (including comments) with a quoted tilde overlay argument; runtime expands the overlay but the UI reads it without expansion and expands only at write time.
- **Local-only surface — guards PASS, writable-path boundary FAIL:** S runs AC1–AC6 and AC27/AC41 startup tests, with socket creation forbidden. Default host is `127.0.0.1`; non-loopback refusal is the `config_ui: refusing to bind non-loopback host ...` diagnostic with exit 2 before serving. Token/session, Host/Origin and POST/CSRF checks pass. **C/F1** defeats the managed-path/base-file distinction; **C/F4** exposes an admission gap before guards execute.
- **One generated page for each module — generation PASS, usability FAIL:** S's AC15–AC18 tests run across all 17 shipped manifests and a newly added fixture module, with labels/defaults/help and unsupported-node notices. Executed inventory found 17 manifests and zero module `*.html` files; UI source name checks pass. **C/F3** makes a shipped setting impossible to disable directly from its default state.
- **Check — side-effect/diagnostic collection PASS, exact-draft guarantee FAIL:** S executes the real `core.main.check_config` bridge, all unresolved-reference reporting, settings-diagnostic collection and zero-socket/zero-process/zero-write checks. **C/F3** shows a submitted boolean edit is silently omitted from the draft.
- **Apply — outcomes/drift PASS, bounded restart FAIL:** S exercises real child acceptance/refusal/unknown, foreign-pid non-supervision, unusable records, disk drift, transition publication and stderr draining. **C/F5** proves the post-kill wait has no timeout; no result deadline covers it. No additional executed lost-edit restart counterexample is claimed.
- **Dependencies/offline — PASS:** S's packaging and URL-audit tests pass. `git diff 835d9b4..HEAD -- pyproject.toml` adds only the UI console script; dependencies remain `aiohttp>=3.9,<4` and `PyYAML>=6,<7`. Pages have inline assets and no render-time fetch; actual browser offline operation was not exercised.

### Phases 0–3 regression guarantees

All following named tests **PASS under S**. This supports preservation of the existing runtime guarantees; it does not make the new UI's failures pass.

| Guarantee | Running evidence |
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

Cross-file inspection: the runtime and UI share `deep_merge` and `canonical_digest`; publication digests the loaded unresolved document, while drift recomputes disk's document. Check and Save share `_apply_edits`. An executed comparison loaded every manifest from `git show 835d9b4:<path>` and disk, recursively removed only schema `title`/`default` annotations (preserving properties literally named `default`), and asserted equality of both schemas and all other declarations: **all 17 passed**. Runtime module implementations and authorization machinery are unchanged in this phase.

Existing-test diff inspection found the two approved assertion adaptations (`test_users.py` metadata allowlist; `test_main.py` second console script). The P3F1 change adds a 30-second wall-time floor to the thread-backed presence wait; its predicate/assertions remain intact. No pre-existing safety assertion was removed to make a production failure green. New tests do encode weaker behavior in places: the short-secret exception and `wait(None)` expectation discussed below, plus an unconditional `... or True` assertion at `tests/test_config_ui.py:1318` that cannot catch a regression. These are coverage defects, not permission to weaken the specification.

## NEW FINDINGS

### F1 — HIGH — a tilde overlay path bypasses collision checks and Save targets the base

Locations: `core/config_ui/__init__.py:180`, `:1784`, `:223`, `:3561`; `core/overlay.py:162`.

`UISettings.overlay_path` and `_managed_overlay` retain the raw path. `same_file()` resolves it without expanding `~`, while `_write_overlay()` finally calls `os.replace(..., target.expanduser())`. For base `/home/chpo/gate-base.yaml` and literal overlay argument `~/gate-base.yaml`, `startup_checks()` returns `[]`. An authenticated `/save` enabling a module returns **303** and invokes replacement onto **the base path**. The replacement document contains just the overlay (`enabled_modules` in C); the original base's actions, secrets, settings and comments would be lost.

**Executed C/F1:** real startup checks, request guards, parser, Save, YAML serialization and destination selection; only filesystem reads/writes are injected/intercepted. Output: `F1 status=303 startup=[] replaces_base=True drops_base_blocks=True`. No real base was overwritten. Normalize/expand and freeze paths once, then use the identical path for collision checking, reading, fingerprints, runtime argv and replacement. Add this case to startup and authenticated write tests.

### F2 — HIGH — R8 is bypassed by short secrets and configured keys treated as structural identifiers

Locations: `core/config_ui/__init__.py:1063`, `:1070`, `:1131`, `:2281` (trigger control rendering).

The plan's P10 requires the final guard for secret values of **length ≥ 1**. Implementation skips every value shorter than four characters and never redacts `ident()`. A configured channel name is then interpolated into control names through `ident()`, even when it equals a collected secret. This is a configured value leaking through generated identifiers, not an accidental match against fixed markup.

**Executed C/F2a:** list `${GATE_SECRET}`, set it to `q7Z`, and put the same text in `modules.twitch.companion_name`. GET `/module/twitch` returns 200 containing `value="q7Z"`, although the secret set contains it.

**Executed C/F2b:** use literal `gate-secret-channel-97bf` as the manifest-declared `modules.twitch.client_secret` and as a trigger channel key. GET `/module/twitch` returns 200 containing `name="triggers.twitch.channels.gate-secret-channel-97bf.combination"`. The value is in the secret set; direct credential rendering is hidden, but the generated attribute discloses it. Output confirms both leaks. `test_redaction_skips_values_shorter_than_the_minimum` currently pins the first violation instead of guarding R8. Fix representation of configured identifiers and short values without corrupting structural markup, and test complete responses.

### F3 — MEDIUM — false boolean drafts vanish when the setting is absent and defaults true

Locations: `core/config_ui/__init__.py:1585`, `:2490`; `modules/audio_output/__init__.py:510`.

When `synthesis.probe` is omitted, runtime `_Settings.from_mapping()` sets it to **True** and the manifest declares `default: true`. The generated checkbox is nevertheless unchecked and registers `rendered="false"`. Posting false is discarded as unchanged by `parse_edits`; Save cannot write false directly from this state, and Check validates the old value instead of that draft. Toggling on and off before submission still posts false and has the same result.

**Executed C/F3:** real shipped manifest rendering, parser and runtime settings parser: `unchecked=True edits=[] runtime_probe=True`. The injected checker also receives an overlay with no false override. Implement an honest distinction between unset/default and explicitly false, and ensure false can reach Save and Check without first saving true.

### F4 — MEDIUM — four workers do not bound queued HTTP work

Locations: `core/config_ui/__init__.py:3986`, `:3993`.

`catch_all` reads each body, then `_dispatch` unconditionally enqueues it in `ThreadPoolExecutor`'s unbounded queue. Host/session/CSRF checks run only inside the worker. `MAX_REQUEST_BYTES` bounds a single body, and `MAX_SESSIONS` bounds stored sessions; neither bounds outstanding requests or their retained bodies. A slow Check/Apply can hold workers while further requests accumulate, including unauthenticated ones. Runtime admission limits do not govern this separate queue.

**Executed C/F4:** saturate the actual dispatch bridge using a finite blocked handler and submit 128 requests to the production worker count. Output: `workers=4 submitted=128 queued=124`. All complete after release. There is no capacity check or overflow response on this path. Add bounded admission before enqueue/body retention, with observable refusal/backpressure and a saturation test.

### F5 — MEDIUM — Apply's stop path has an unlimited post-kill wait

Location: `core/config_ui/__init__.py:3785` (`Supervisor.stop`).

After the bounded terminate wait expires, the code kills the child and calls `child.wait()` with no timeout. `restart()` establishes its 60-second acceptance deadline only after `stop()` and `start()` finish. Thus a child that cannot promptly be reaped can hold Apply (and UI shutdown) indefinitely; the advertised bounded result does not cover this branch.

**Executed C/F5:** an injected child times out the first wait; the real supervisor makes the sequence `terminate`, `wait(10.0)`, `kill`, `wait(None)`. This reproduces the missing bound without hanging an actual process. The existing `tests/test_config_ui.py::test_stop_waits_for_the_grace_before_killing` also explicitly asserts `wait(None)`. Bound this wait and report the inability to confirm termination honestly, retaining enough ownership information to avoid losing track of the old child.

## Executable counterexamples C

Run this block from the repository root with `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -` (stdin). It uses no network, writes no files and does not launch a child. Filesystem replacement in F1 is intercepted to honor this gate's one-file-only constraint.

```python
# GATE_COUNTEREXAMPLES_BEGIN
import asyncio
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode
import yaml
import core.config_ui as u
from modules.audio_output import _Settings


def memory_ui(stack, base, env=None, settings=None):
    stack.enter_context(patch.object(u, 'read_base', return_value=base))
    stack.enter_context(patch.object(u, 'read_overlay', return_value={}))
    stack.enter_context(patch.object(u, '_file_state', return_value=(False, None, None)))
    stack.enter_context(patch.object(u, '_file_fingerprint', return_value=u.FINGERPRINT_ABSENT))
    stack.enter_context(patch.object(u, 'read_status', return_value=u.StatusReading.absent()))
    ui = u.ConfigUI(settings or u.UISettings(Path('/nonexistent-gate/config.yaml')),
                    environ=env or {})
    login = ui.handle(u.UIRequest('GET', '/', {'token': ui.token},
                                  {'host': ui.access_authority}))
    assert login.status == 303
    cookie = dict(login.headers)['Set-Cookie'].split(';')[0]
    headers = {'host': ui.access_authority, 'cookie': cookie}
    session = ui._session(u.UIRequest('GET', '/', {}, headers))
    headers.update({'x-csrf-token': session.csrf_token,
                    'content-type': 'application/x-www-form-urlencoded'})
    return ui, headers


# F1: exercise the real authenticated save route; only the file sinks are fake.
written = []
class MemoryFile(BytesIO):
    def fileno(self):
        return 999
    def __exit__(self, *args):
        written.append(self.getvalue())
        return super().__exit__(*args)

settings = u.UISettings(Path.home() / 'gate-base.yaml',
                        overlay=Path('~/gate-base.yaml'))
base = {'modules_directory': 'builtin', 'enabled_modules': [],
        'actions': [{'rule_id': 'preserve-me'}], 'secrets': ['${KEEP}']}
with ExitStack() as stack:
    ui, headers = memory_ui(stack, base, settings=settings)
    stack.enter_context(patch.object(u.tempfile, 'mkstemp',
                                     return_value=(999, 'memory-only-temp')))
    stack.enter_context(patch.object(u.os, 'fdopen', return_value=MemoryFile()))
    stack.enter_context(patch.object(u.os, 'fsync'))
    replace = stack.enter_context(patch.object(u.os, 'replace'))
    form = {u.FINGERPRINT_FIELD: u.FINGERPRINT_ABSENT,
            'enabled_modules.twitch': 'true'}
    response = ui.handle(u.UIRequest('POST', '/save', {}, headers,
                                     urlencode(form).encode()))
    replaces_base = replace.call_args.args[1] == settings.base_path
    document = yaml.safe_load(written[0])
    drops_blocks = 'actions' not in document and 'secrets' not in document
    assert response.status == 303 and replaces_base and drops_blocks
    assert u.startup_checks(settings) == []
    print(f'F1 status={response.status} startup=[] replaces_base={replaces_base} '
          f'drops_base_blocks={drops_blocks}')

# F2a: short listed secret used as an ordinary setting.
base = {'modules_directory': 'builtin', 'enabled_modules': [],
        'secrets': ['${GATE_SECRET}'],
        'modules': {'twitch': {'companion_name': 'q7Z'}}}
with ExitStack() as stack:
    ui, headers = memory_ui(stack, base, {'GATE_SECRET': 'q7Z'})
    response = ui.handle(u.UIRequest('GET', '/module/twitch', {}, headers))
    leaked = 'value="q7Z"' in response.body.decode()
    assert response.status == 200 and 'q7Z' in ui.secret_values and leaked
    print(f'F2a status={response.status} short_secret_leaked={leaked}')

# F2b: manifest-declared credential reused as a configured trigger channel key.
secret = 'gate-secret-channel-97bf'
base = {'modules_directory': 'builtin', 'enabled_modules': [],
        'modules': {'twitch': {'client_secret': secret}},
        'triggers': {'twitch': {'channels': {
            secret: {'combination': 'all_of', 'rules': []}}}}}
with ExitStack() as stack:
    ui, headers = memory_ui(stack, base)
    response = ui.handle(u.UIRequest('GET', '/module/twitch', {}, headers))
    leaked = f'name="triggers.twitch.channels.{secret}.combination"' in response.body.decode()
    assert response.status == 200 and secret in ui.secret_values and leaked
    print(f'F2b status={response.status} credential_in_attribute={leaked}')

# F3: the same real setting is true at runtime and false-looking in the UI.
module_settings = {'synthesis': {'endpoint': '', 'model': ''},
                   'voices': {'allowed': ['v'], 'default': 'v'},
                   'outputs': {'o': {'player': {'argv': ['cat']}}},
                   'default_output': 'o'}
base = {'modules_directory': 'builtin', 'enabled_modules': [],
        'modules': {'audio_output': module_settings}}
with ExitStack() as stack:
    ui, headers = memory_ui(stack, base)
    response = ui.handle(u.UIRequest('GET', '/module/audio_output', {}, headers))
    field = 'modules.audio_output.synthesis.probe'
    unchecked = f'name="{field}" value="true" checked' not in response.body.decode()
    edits, problems = ui.parse_edits(ui.view(), [(field, 'false')])
    seen = []
    def checker(path, environ, overlay, draft):
        seen.append(draft)
        return True, []
    ui.checker = checker
    ui.handle(u.UIRequest('POST', '/check', {}, headers,
                         urlencode({field: 'false'}).encode()))
    runtime_probe = _Settings.from_mapping(module_settings).probe
    assert unchecked and edits == [] and not problems and seen == [{}] and runtime_probe
    print(f'F3 unchecked={unchecked} edits={edits} runtime_probe={runtime_probe} '
          f'checker_overlay={seen[0]}')

# F4: execute the actual bridge, keeping all work finite and socket-free.
release = threading.Event()
class Blocked:
    def handle(self, request):
        release.wait(2)
        return u.UIResponse(401)

async def saturation():
    with ThreadPoolExecutor(max_workers=u.EXECUTOR_WORKERS) as executor:
        tasks = [asyncio.create_task(u._dispatch(
            Blocked(), u.UIRequest('GET', '/', {}, {}), executor)) for _ in range(128)]
        try:
            await asyncio.sleep(0)
            queued = executor._work_queue.qsize()
            assert queued >= 124
            print(f'F4 workers={u.EXECUTOR_WORKERS} submitted=128 queued={queued}')
        finally:
            release.set()
            await asyncio.gather(*tasks)
u._run_socket_free(saturation())

# F5: observe the real supervisor's timeout arguments without hanging a child.
calls = []
class Child:
    def terminate(self):
        calls.append('terminate')
    def kill(self):
        calls.append('kill')
    def wait(self, timeout=None):
        calls.append(f'wait({timeout})')
        if timeout is not None:
            raise subprocess.TimeoutExpired('fake', timeout)
supervisor = u.Supervisor([], Path('/nonexistent-gate/status.json'))
supervisor.child = Child()
supervisor.stop()
assert calls == ['terminate', 'wait(10.0)', 'kill', 'wait(None)']
print('F5', calls)
# GATE_COUNTEREXAMPLES_END
```

## NOT VERIFIED

- No real browser session, browser form-submission/JavaScript behavior, or actual HTTP listener was exercised. Rendering and request handling ran through the socket-free core and its async bridge. Therefore full browser usability, preservation of unsaved edits across navigation/Check/Apply, and browser-specific submitter behavior are **NOT-VERIFIABLE from this run**.
- The 18 existing environment-gated skips remain: five capture process-group cases, six phase-2 trials, six phase-3 trials, and the distribution installation test (pip unavailable in the project interpreter). No live provider/platform credentials, audio hardware, capture devices or real moderation endpoints were exercised.
- Distribution installation and installed console-script execution were not verified; package declarations and module entry-point tests passed.
- F1's destination and serialized replacement are proven with intercepted writes, not destruction of an actual base file. F4 is a dispatch saturation probe, not a live HTTP load test. F5 proves the missing timeout argument; an OS process stuck after SIGKILL was not induced.
- Atomic replacement and ordinary stale-page detection are covered by S; hostile filesystem races, external-writer interleavings during Save/Apply, and all possible path-alias attacks were not exhaustively verified.
- No assertion of absolute absence of further defects is made. The five reproduced failures above are sufficient to keep this closing gate open.
